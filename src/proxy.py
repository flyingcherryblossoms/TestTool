"""透明双向 TCP 代理；录制解析与网络转发相互独立。"""
from __future__ import annotations

import codecs
import errno
import queue
import selectors
import socket
import threading
import time
import uuid

from src.recording import ExchangeRecorder, MAX_BYTES

MAX_PENDING = 1024 * 1024


def validate_config(config):
    codecs.lookup(config.get("encoding", "UTF-8"))
    if config.get("protocol") not in ("tcp", "http", "ws"):
        raise ValueError("不支持的录制协议")
    for key in ("listen_port", "upstream_port"):
        if not 1 <= int(config[key]) <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间")
    if not config.get("listen_host", "").strip() or not config.get("upstream_host", "").strip():
        raise ValueError("监听地址和上游地址不能为空")
    if not 1 <= int(config.get("head_length", 5)) <= 12:
        raise ValueError("长度头必须为 1 到 12 位")
    if config.get("framing", "length") == "delimiter":
        delimiter = bytes.fromhex(config.get("delimiter", ""))
        if not delimiter or len(delimiter) > 64:
            raise ValueError("分隔符必须是 1 到 64 字节的十六进制值")
    if config.get("protocol") == "tcp" and config.get("framing") not in ("length", "delimiter", "eof"):
        raise ValueError("不支持的分帧模式")
    if not 1 <= float(config.get("timeout", 30)) <= 3600:
        raise ValueError("空闲超时必须为 1 到 3600 秒")


class RecordingWriter:
    """有界队列批量持久化，失败后明确标记会话不完整。"""

    def __init__(self, db, on_error, max_queue=512):
        self.db, self.on_error = db, on_error
        self.queue = queue.Queue(max_queue)
        self.error = ""
        self.pending_bytes = 0
        self.bytes_lock = threading.Lock()
        self.failed = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="recording-writer")

    def start(self):
        self.thread.start()

    def emit(self, kind, values):
        if self.failed.is_set():
            return
        size = sum(len(v) for v in values if isinstance(v, bytes))
        with self.bytes_lock:
            overflow = self.pending_bytes + size > 32 * 1024 * 1024
            if not overflow:
                self.pending_bytes += size
        if overflow:
            self.fail("录制缓冲超过 32 MB，会话数据不完整；网络仍继续转发")
            return
        try:
            self.queue.put_nowait((kind, values))
        except queue.Full:
            with self.bytes_lock:
                self.pending_bytes -= size
            self.fail("录制队列已满，会话数据不完整；网络仍继续转发")

    def fail(self, error):
        if not self.failed.is_set():
            self.error = error
            self.failed.set()
            self.on_error(error)

    def _run(self):
        while not self.done.is_set() or not self.queue.empty():
            try:
                first = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < 64:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            with self.bytes_lock:
                self.pending_bytes -= sum(len(v) for _, values in batch for v in values if isinstance(v, bytes))
            try:
                self.db.write_recording_batch(batch)
            except Exception as exc:
                self.fail("录制数据库写入失败: " + str(exc))
                # 不反复重试已失败的数据库，保证停止能及时完成。
                while True:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        break
                return

    def stop(self):
        self.done.set()
        self.thread.join()


class RecordingProxyEngine:
    """每连接一个后台线程，双向非阻塞转发、半关闭及发送缓冲背压。"""

    def __init__(self, config, session_id, emit, on_status, on_error):
        validate_config(config)
        self.config, self.session_id, self.emit = dict(config), session_id, emit
        self.on_status, self.on_error = on_status, on_error
        self.cancel_event = threading.Event()
        self.listener = None
        self.lock = threading.Lock()
        self.sockets = set()
        self.threads = set()
        self.ready = threading.Event()
        self.bound_port = None
        self.error = ""
        self.recording_error = ""
        self.upstream_addr = None
        self.upstream_addresses = []

    def start(self):
        try:
            # 地址解析与建连均在工作线程执行。
            addresses = socket.getaddrinfo(self.config["upstream_host"], self.config["upstream_port"], type=socket.SOCK_STREAM)
            listen = socket.getaddrinfo(self.config["listen_host"], self.config["listen_port"], type=socket.SOCK_STREAM)[0]
            if self.config["listen_port"] == self.config["upstream_port"]:
                local = {"127.0.0.1", "::1", self.config["listen_host"]}
                local.update(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None))
                if any(a[4][0] in local for a in addresses):
                    raise ValueError("上游指向本机同一端口，可能形成代理循环；请调整上游端口")
            self.upstream_addr = addresses[0]
            self.upstream_addresses = addresses
            listener = socket.socket(listen[0], listen[1], listen[2])
            self.listener = listener
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(listen[4])
            listener.listen(100)
            listener.settimeout(0.2)
            self.bound_port = listener.getsockname()[1]
            self.ready.set()
            self.on_status("代理已启动：{}:{} → {}:{}".format(self.config["listen_host"], self.bound_port,
                                                          self.config["upstream_host"], self.config["upstream_port"]))
            while not self.cancel_event.is_set():
                try:
                    client, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.cancel_event.is_set():
                        break
                    raise
                with self.lock:
                    if len(self.threads) >= 200:
                        client.close()
                        self.on_error("达到 200 个并发连接上限，拒绝新连接")
                        continue
                    self.sockets.add(client)
                    thread = threading.Thread(target=self._connection, args=(client, address), name="recording-proxy")
                    self.threads.add(thread)
                thread.start()
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.error = str(exc)
                self.on_error("代理启动或监听失败: " + self.error)
        finally:
            self.stop()
            with self.lock:
                threads = list(self.threads)
            for thread in threads:
                thread.join()
            self.on_status("代理已停止，录制数据正在保存")

    def stop(self):
        self.cancel_event.set()
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _observe(self, recorder, direction, data, eof=False):
        if recorder.failed:
            if data:
                self._partial_event(recorder, direction, data)
            return False
        previous = bytes(recorder.parsers[direction].buffer)
        try:
            recorder.feed(direction, data, eof)
        except Exception as exc:
            recorder.failed = True
            parser = recorder.parsers[direction]
            self._partial_event(recorder, direction, previous + data)
            parser.buffer.clear()
            recorder.close("incomplete")
            self.recording_error = "录制解析失败: " + str(exc)
            self.on_error(self.recording_error + "；此连接继续原样转发")
            return False
        return True

    def _partial_event(self, recorder, direction, data):
        if not data:
            return
        recorder.sequence += 1
        self.emit("event", (uuid.uuid4().hex, recorder.connection_id, direction, recorder.sequence,
                            time.time(), data, data, '{"incomplete":true,"control":true}'))

    def _connect(self, sock, address):
        sock.setblocking(False)
        result = sock.connect_ex(address[4])
        allowed = (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                   getattr(errno, "WSAEWOULDBLOCK", 10035))
        if result not in allowed:
            raise OSError(result, "连接上游失败")
        deadline = time.monotonic() + min(10, self.config.get("timeout", 30))
        with selectors.DefaultSelector() as selector:
            selector.register(sock, selectors.EVENT_WRITE)
            while not self.cancel_event.is_set():
                if selector.select(0.2):
                    error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if error:
                        raise OSError(error, "连接上游失败")
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("连接上游超时")
        raise ConnectionError("代理已停止")

    def _connection(self, client, address):
        cid = uuid.uuid4().hex
        source = "{}:{}".format(address[0], address[1])
        upstream_name = "{}:{}".format(self.config["upstream_host"], self.config["upstream_port"])
        self.emit("connection", (cid, self.session_id, source, upstream_name, time.time()))
        recorder = ExchangeRecorder(cid, self.config, self.emit)
        upstream, status, error = None, "closed", ""
        try:
            connect_error = None
            for address_info in self.upstream_addresses:
                if self.cancel_event.is_set():
                    break
                family, stype, proto, _, _ = address_info
                upstream = socket.socket(family, stype, proto)
                with self.lock:
                    self.sockets.add(upstream)
                try:
                    self._connect(upstream, address_info)
                    connect_error = None
                    break
                except OSError as exc:
                    connect_error = exc
                    with self.lock:
                        self.sockets.discard(upstream)
                    upstream.close()
            if self.cancel_event.is_set():
                raise ConnectionError("代理已停止")
            if connect_error:
                raise connect_error
            peer = upstream.getpeername()
            self.emit("destination", ("{}:{}".format(peer[0], peer[1]), cid))
            client.setblocking(False)
            peers = {client: upstream, upstream: client}
            directions = {client: "request", upstream: "response"}
            buffers = {client: bytearray(), upstream: bytearray()}
            ended, shutdown = set(), set()
            last_activity = time.monotonic()
            with selectors.DefaultSelector() as selector:
                while not self.cancel_event.is_set():
                    for sock in peers:
                        events = 0
                        if sock not in ended and len(buffers[peers[sock]]) < MAX_PENDING:
                            events |= selectors.EVENT_READ
                        if buffers[sock]:
                            events |= selectors.EVENT_WRITE
                        try:
                            selector.unregister(sock)
                        except KeyError:
                            pass
                        if events:
                            selector.register(sock, events)
                    for src in ended:
                        dst = peers[src]
                        if not buffers[dst] and dst not in shutdown:
                            try:
                                dst.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                            shutdown.add(dst)
                    if len(ended) == 2 and not any(buffers.values()):
                        break
                    if time.monotonic() - last_activity > self.config.get("timeout", 30):
                        status = "timeout"
                        break
                    for key, mask in selector.select(0.2):
                        sock = key.fileobj
                        if mask & selectors.EVENT_READ:
                            try:
                                data = sock.recv(65536)
                            except BlockingIOError:
                                continue
                            if data:
                                last_activity = time.monotonic()
                                buffers[peers[sock]].extend(data)
                                self._observe(recorder, directions[sock], data)
                            else:
                                ended.add(sock)
                                self._observe(recorder, directions[sock], b"", True)
                        if mask & selectors.EVENT_WRITE and buffers[sock]:
                            try:
                                count = sock.send(buffers[sock])
                            except BlockingIOError:
                                continue
                            del buffers[sock][:count]
                            last_activity = time.monotonic()
            if self.cancel_event.is_set():
                status = "cancelled"
        except Exception as exc:
            status = "cancelled" if self.cancel_event.is_set() else "error"
            error = str(exc)
            if status == "error":
                self.on_error("连接 {}: {}".format(source, error))
                # 建连失败时仍保留客户端已发送的请求，不把错误文字冒充响应。
                if recorder.sequence == 0:
                    try:
                        client.settimeout(0.1)
                        remaining = MAX_BYTES
                        while remaining > 0 and not self.cancel_event.is_set():
                            data = client.recv(min(65536, remaining))
                            remaining -= len(data)
                            if not data:
                                self._observe(recorder, "request", b"", True)
                                break
                            self._observe(recorder, "request", data)
                    except OSError:
                        pass
        finally:
            for direction, parser in recorder.parsers.items():
                if parser.buffer or parser.fragment:
                    self._partial_event(recorder, direction, bytes(parser.fragment_wire) + bytes(parser.buffer))
                    self.recording_error = "连接 {} 存在不完整报文，原始字节已保存".format(source)
                    self.on_error(self.recording_error)
                    if status == "closed":
                        status = "incomplete"
            recorder.close("incomplete" if recorder.failed else ("unanswered" if status == "closed" else status))
            self.emit("close", (time.time(), "incomplete" if recorder.failed else status, error, cid))
            for sock in (client, upstream):
                if sock:
                    with self.lock:
                        self.sockets.discard(sock)
                    sock.close()
            with self.lock:
                self.threads.discard(threading.current_thread())
