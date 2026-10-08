"""基于录制交互的离线 Mock；精确/字段匹配及动态字段回填。"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading

from src.recording import MessageParser, json_field, set_json_field

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length"}


def validate_rule(rule, encoding="UTF-8"):
    if not rule.name.strip():
        raise ValueError("规则名称不能为空")
    if not 0 <= rule.delay_ms <= 60000:
        raise ValueError("响应延迟必须为 0 到 60000 毫秒")
    if rule.mode not in ("exact", "json"):
        raise ValueError("不支持的匹配模式")
    if rule.mode == "json" and not rule.fields.strip():
        raise ValueError("字段匹配至少需要一个 JSON 字段")
    if rule.mode == "json" or rule.copy_fields.strip():
        req = json.loads(rule.request.decode(encoding))
        paths = [p.strip() for p in rule.fields.split(",") if p.strip()]
        for path in paths if rule.mode == "json" else []:
            json_field(req, path)
        if rule.copy_fields.strip():
            headers = json.loads(rule.response_meta).get("headers", [])
            if any(k.lower() == "content-encoding" and v.lower() != "identity" for k, v in headers):
                raise ValueError("压缩响应不能直接回填 JSON 字段")
            resp = json.loads(rule.response.decode(encoding))
            for path in [p.strip() for p in rule.copy_fields.split(",") if p.strip()]:
                set_json_field(resp, path, json_field(req, path))


def match_rule(rule, payload, meta, encoding):
    saved_meta = json.loads(rule.request_meta)
    if rule.protocol == "http" and any(saved_meta.get(k) != meta.get(k) for k in ("method", "path")):
        return False
    if rule.protocol == "ws" and saved_meta.get("opcode", 1) != meta.get("opcode", 1):
        return False
    if rule.mode == "exact":
        return rule.request == payload
    try:
        saved, current = json.loads(rule.request.decode(encoding)), json.loads(payload.decode(encoding))
        return all(json.dumps(json_field(saved, p.strip()), sort_keys=True) == json.dumps(json_field(current, p.strip()), sort_keys=True)
                   for p in rule.fields.split(",") if p.strip())
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def response_payload(rule, request, encoding):
    if not rule.copy_fields.strip():
        return rule.response
    current = json.loads(request.decode(encoding))
    response = json.loads(rule.response.decode(encoding))
    for path in rule.copy_fields.split(","):
        if path.strip():
            set_json_field(response, path.strip(), json_field(current, path.strip()))
    return json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(encoding)


def ws_frame(payload, opcode=1):
    size = len(payload)
    if size < 126:
        header = bytes([128 | opcode, size])
    elif size < 65536:
        header = bytes([128 | opcode, 126]) + size.to_bytes(2, "big")
    else:
        header = bytes([128 | opcode, 127]) + size.to_bytes(8, "big")
    return header + payload


def build_reply(config, rule, request, meta):
    body = response_payload(rule, request, config.get("encoding", "UTF-8"))
    if config["protocol"] == "ws":
        return ws_frame(body, json.loads(rule.response_meta).get("opcode", 1))
    if config["protocol"] == "http":
        from http.client import responses
        saved = json.loads(rule.response_meta)
        code = saved.get("status_code", 200)
        headers = saved.get("headers", [])
        blocked = set(HOP_HEADERS)
        for k, v in headers:
            if k.lower() == "connection":
                blocked.update(x.strip().lower() for x in v.split(","))
        lines = ["HTTP/1.1 {} {}".format(code, responses.get(code, ""))]
        lines.extend("{}: {}".format(k, v) for k, v in headers if k.lower() not in blocked)
        if not (100 <= code < 200 or code in (204, 304)):
            length = len(body)
            if meta.get("method") == "HEAD" and not rule.copy_fields.strip():
                length = next((int(v) for k, v in headers if k.lower() == "content-length"), length)
            lines.append("Content-Length: {}".format(length))
        lines.append("Connection: keep-alive")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        return raw if meta.get("method") == "HEAD" or code in (204, 304) else raw + body
    framing = config.get("framing", "length")
    if framing == "length":
        width = config.get("head_length", 5)
        length = str(len(body))
        if len(length) > width:
            raise ValueError("响应长度超出长度头容量")
        return length.zfill(width).encode("ascii") + body
    if framing == "delimiter":
        return body + bytes.fromhex(config["delimiter"])
    return body


class ReplayServerEngine:
    """录制页中的独立回放监听器；不命中或歧义时明确报错。"""

    def __init__(self, config, rules, on_status, on_error):
        self.config, self.rules = dict(config), [r for r in rules if r.enabled]
        if not self.rules:
            raise ValueError("请先生成至少一条回放规则")
        for rule in self.rules:
            if rule.protocol != config["protocol"]:
                raise ValueError("回放规则协议与会话不一致")
            validate_rule(rule, config.get("encoding", "UTF-8"))
        self.on_status, self.on_error = on_status, on_error
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.sockets, self.threads = set(), set()
        self.listener = None
        self.ready = threading.Event()
        self.bound_port = None
        self.error = ""

    def stop(self):
        self.cancel_event.set()
        if self.listener:
            self.listener.close()
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def start(self):
        try:
            addr = socket.getaddrinfo(self.config["listen_host"], self.config["listen_port"], type=socket.SOCK_STREAM)[0]
            listener = socket.socket(addr[0], addr[1], addr[2])
            self.listener = listener
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(addr[4])
            listener.listen(100)
            listener.settimeout(0.2)
            self.bound_port = listener.getsockname()[1]
            self.ready.set()
            self.on_status("录制 Mock 已启动 {}:{}".format(self.config["listen_host"], self.bound_port))
            while not self.cancel_event.is_set():
                try:
                    client, addr = listener.accept()
                except socket.timeout:
                    continue
                with self.lock:
                    if len(self.threads) >= 200:
                        client.close()
                        continue
                    self.sockets.add(client)
                    thread = threading.Thread(target=self._client, args=(client, addr), name="recording-replay")
                    self.threads.add(thread)
                thread.start()
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.error = str(exc)
                self.on_error("回放监听失败: " + self.error)
        finally:
            self.stop()
            with self.lock:
                threads = list(self.threads)
            for thread in threads:
                thread.join()
            self.on_status("录制 Mock 已停止")

    def _client(self, sock, addr):
        import time
        parser = MessageParser(self.config, "request")
        position = {}
        last_activity = time.monotonic()
        try:
            sock.settimeout(0.2)
            while not self.cancel_event.is_set():
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    if time.monotonic() - last_activity > self.config.get("timeout", 30):
                        raise TimeoutError("回放连接空闲超时")
                    continue
                last_activity = time.monotonic()
                for _, payload, meta in parser.feed(data, not data):
                    if meta.get("handshake"):
                        headers = {k.lower(): v for k, v in meta["headers"]}
                        key = headers.get("sec-websocket-key", "")
                        if not key:
                            raise ValueError("缺少 WebSocket 握手密钥")
                        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest())
                        self._send(sock, b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                        continue
                    if meta.get("control"):
                        if meta.get("opcode") == 9:
                            self._send(sock, ws_frame(payload, 10))
                        elif meta.get("opcode") == 8:
                            self._send(sock, ws_frame(payload, 8))
                            return
                        continue
                    matches = [r for r in self.rules if match_rule(r, payload, meta, self.config.get("encoding", "UTF-8"))]
                    if not matches:
                        if self.config["protocol"] == "http":
                            self._send(sock, b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                        raise ValueError("没有匹配的录制规则")
                    if len(matches) > 1:
                        if self.config.get("replay_policy", "unique") != "sequence":
                            raise ValueError("多条规则同时匹配，请删除重复规则或选择顺序回放")
                        key = tuple(r.id for r in matches)
                        idx = position.get(key, 0)
                        if idx >= len(matches):
                            raise ValueError("当前连接的场景响应已回放完毕")
                        position[key] = idx + 1
                        rule = matches[idx]
                    else:
                        rule = matches[0]
                    if self.cancel_event.wait(rule.delay_ms / 1000.0):
                        return
                    self._send(sock, build_reply(self.config, rule, payload, meta))
                    self.on_status("{}:{} 命中规则：{}".format(addr[0], addr[1], rule.name))
                if not data:
                    return
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.on_error("{}:{} 回放失败: {}".format(addr[0], addr[1], exc))
        finally:
            with self.lock:
                self.sockets.discard(sock)
                self.threads.discard(threading.current_thread())
            sock.close()

    def _send(self, sock, data):
        # timeout 限制慢客户端，stop() 可通过 shutdown 解除发送阻塞。
        sock.settimeout(2)
        try:
            sock.sendall(data)
        finally:
            sock.settimeout(0.2)
