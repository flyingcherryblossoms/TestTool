"""协议测试 Worker 线程。

提供 TCP/WebSocket 客户端和服务端的 QThread 封装。
服务端 Worker 的 run() 直接调用引擎的阻塞式 start()，
stop_server() 从主线程调用引擎的 stop() 解除阻塞。
"""

from __future__ import annotations

import threading
import time

import requests
from PySide6.QtCore import QThread, Signal

from src.protocol import (
    HttpServerEngine,
    TcpServerEngine,
    WsServerEngine,
    tcp_send_and_receive,
    ws_send_and_receive,
)
from src.ui.http_client import config_to_request_kwargs


class TcpClientWorker(QThread):
    """TCP 客户端一次性发送 Worker。"""

    finished = Signal(bool, str)

    def __init__(self, ip: str, port: int, message: str, encoding: str,
                 head_len: int, timeout: float, parent=None):
        super().__init__(parent)
        self._ip = ip
        self._port = port
        self._message = message
        self._encoding = encoding
        self._head_len = head_len
        self._timeout = timeout

    def run(self) -> None:
        success, response = tcp_send_and_receive(
            self._ip, self._port, self._message,
            self._encoding, self._head_len, self._timeout
        )
        self.finished.emit(success, response)


class TcpServerWorker(QThread):
    """TCP 服务端监听 Worker。

    run() 调用 TcpServerEngine.start()（阻塞），
    直到 stop_server() 从主线程调用 engine.stop()。
    """

    message_received = Signal(str, str)
    message_received_raw = Signal(str, bytes)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, server_id: int, ip: str, port: int, encoding: str,
                 head_len: int, response_mode: str,
                 response_message: str, recv_encoding: str | None = None,
                 response_delay_ms: int = 0, parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._encoding = encoding
        self._recv_encoding = recv_encoding or encoding
        self._head_len = head_len
        self._response_mode = response_mode
        self._response_message = response_message
        self._response_delay_ms = response_delay_ms
        self._engine: TcpServerEngine | None = None
        self._pending_raw: bytes | None = None

    def run(self) -> None:
        """在 QThread 中阻塞运行 accept 循环。"""
        self._engine = TcpServerEngine(
            ip=self._ip,
            port=self._port,
            encoding=self._encoding,
            recv_encoding=self._recv_encoding,
            head_len=self._head_len,
            on_message=self._on_message_received,
            on_message_raw=self._on_raw_received,
            on_status=self._on_status,
            on_error=self._on_error,
        )
        # start() 阻塞当前线程直到 stop() 被调用
        self._engine.start()

    def set_encodings(self, encoding: str,
                      recv_encoding: str | None = None) -> None:
        """运行时更新发送/接收编码。"""
        self._encoding = encoding
        if recv_encoding:
            self._recv_encoding = recv_encoding
        if self._engine:
            self._engine.set_encodings(self._encoding, self._recv_encoding)

    def _on_raw_received(self, client_addr: str, raw: bytes) -> None:
        self._pending_raw = raw

    def _on_message_received(self, client_addr: str, message: str) -> str:
        raw = self._pending_raw
        self._pending_raw = None
        self.message_received.emit(client_addr, message)
        if raw is not None:
            self.message_received_raw.emit(client_addr, raw)
        if self._response_delay_ms > 0:
            time.sleep(self._response_delay_ms / 1000.0)
        if self._response_mode == "echo":
            return message
        return self._response_message

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)

    def _on_error(self, error: str) -> None:
        self.error_occurred.emit(error)

    def set_active_message(self, text: str) -> None:
        """运行时热更新固定响应内容（服务端运行中生效）。"""
        self._response_message = text

    def stop_server(self) -> None:
        """从主线程停止服务端。"""
        if self._engine:
            self._engine.stop()
        self.wait(3000)


class WsClientWorker(QThread):
    """WebSocket 客户端一次性发送 Worker。"""

    finished = Signal(bool, str)

    def __init__(self, url: str, message: str, timeout: float, parent=None):
        super().__init__(parent)
        self._url = url
        self._message = message
        self._timeout = timeout

    def run(self) -> None:
        success, response = ws_send_and_receive(
            self._url, self._message, self._timeout
        )
        self.finished.emit(success, response)


class WsServerWorker(QThread):
    """WebSocket 服务端监听 Worker。

    run() 调用 WsServerEngine.start()（阻塞运行 asyncio 事件循环），
    直到 stop_server() 从主线程调用 engine.stop()。
    """

    message_received = Signal(str, str)
    message_received_raw = Signal(str, bytes)
    client_event = Signal(str)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, server_id: int, ip: str, port: int, path: str,
                 response_mode: str, response_message: str,
                 response_delay_ms: int = 0, parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._path = path
        self._response_mode = response_mode
        self._response_message = response_message
        self._response_delay_ms = response_delay_ms
        self._engine: WsServerEngine | None = None

    def run(self) -> None:
        """在 QThread 中阻塞运行 asyncio 事件循环。"""
        self._engine = WsServerEngine(
            ip=self._ip,
            port=self._port,
            path=self._path,
            on_message=self._on_message_received,
            on_client_event=self._on_client_event,
            on_status=self._on_status,
            on_error=self._on_error,
        )
        self._engine.start()

    def _on_message_received(self, message: str) -> str:
        self.message_received.emit("", message)
        try:
            self.message_received_raw.emit("", message.encode("utf-8"))
        except Exception:
            pass
        if self._response_delay_ms > 0:
            time.sleep(self._response_delay_ms / 1000.0)
        if self._response_mode == "echo":
            return message
        return self._response_message

    def _on_client_event(self, event: str) -> None:
        self.client_event.emit(event)

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)

    def _on_error(self, error: str) -> None:
        self.error_occurred.emit(error)

    def set_active_message(self, text: str) -> None:
        """运行时热更新固定响应内容（服务端运行中生效）。"""
        self._response_message = text

    def stop_server(self) -> None:
        """从主线程停止 WebSocket 服务端。"""
        if self._engine:
            self._engine.stop()
        self.wait(3000)


class HttpServerWorker(QThread):
    """HTTP 服务端监听 Worker（mock server）。

    run() 调用 HttpServerEngine.start()（阻塞），
    直到 stop_server() 从主线程调用 engine.stop()。
    """

    message_received = Signal(str, str)
    message_received_raw = Signal(str, bytes)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, server_id: int, ip: str, port: int,
                 response_mode: str, status_code: int, headers: list,
                 response_message: str, response_delay_ms: int = 0,
                 parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._response_mode = response_mode
        self._status_code = status_code
        self._headers = list(headers or [])
        self._response_message = response_message
        self._response_delay_ms = response_delay_ms
        self._engine: HttpServerEngine | None = None
        self._pending_raw: bytes | None = None

    def run(self) -> None:
        """在 QThread 中阻塞运行 accept 循环。"""
        self._engine = HttpServerEngine(
            ip=self._ip,
            port=self._port,
            on_request=self._on_request,
            on_message_raw=self._on_raw_received,
            on_status=self._on_status,
            on_error=self._on_error,
        )
        self._engine.start()

    def set_active_response(self, status_code: int, headers: list,
                            body: str) -> None:
        """运行时热更新当前返回响应（服务端运行中生效）。"""
        self._status_code = int(status_code)
        self._headers = list(headers or [])
        self._response_message = body

    def _on_raw_received(self, client_addr: str, raw: bytes) -> None:
        self._pending_raw = raw

    def _on_request(self, client_addr: str, req: dict) -> tuple[int, list, str]:
        raw = self._pending_raw
        self._pending_raw = None
        self.message_received.emit(client_addr, req["text"])
        if raw is not None:
            self.message_received_raw.emit(client_addr, raw)
        if self._response_delay_ms > 0:
            time.sleep(self._response_delay_ms / 1000.0)
        if self._response_mode == "echo":
            body = req["body"].decode("utf-8", errors="replace")
            return 200, list(self._headers), body
        return self._status_code, list(self._headers), self._response_message

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)

    def _on_error(self, error: str) -> None:
        self.error_occurred.emit(error)

    def stop_server(self) -> None:
        """从主线程停止 HTTP 服务端。"""
        if self._engine:
            self._engine.stop()
        self.wait(3000)


# ── 压测 Worker ────────────────────────────────────────────


class _TokenBucket:
    """简单令牌桶，用于 QPS 限速。rate <= 0 时不限速。"""

    def __init__(self, rate: float):
        self._rate = rate
        self._lock = threading.Lock()
        self._tokens = float(rate) if rate > 0 else 0.0
        self._last = None

    def acquire(self) -> None:
        """阻塞直到取得一个令牌。"""
        if self._rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                if self._last is None:
                    self._last = now
                self._tokens = min(
                    float(self._rate),
                    self._tokens + (now - self._last) * self._rate,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            time.sleep(0.001)


class StressTestWorker(QThread):
    """压测 Worker：按 并发数 / 总请求数 / QPS限制 / 压测时长 并发发送报文并统计。

    run() 阻塞直到达到总请求数、时长耗尽或 stop() 被调用。
    预热期内递增步长逐渐把并发数升到目标值。
    """

    progress = Signal(int, int, int)          # 已完成, 成功, 失败
    finished = Signal(int, int, int, float)   # 已完成, 成功, 失败, 耗时(秒)

    def __init__(self, proto: str, ip: str, port: int, message: str,
                 encoding: str, head_len: int, timeout: float,
                 ws_url: str, concurrency: int, total_requests: int,
                 qps_limit: int, duration: int, warmup: int,
                 ramp_step: int, http_config: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._proto = proto
        self._ip = ip
        self._port = port
        self._message = message
        self._encoding = encoding
        self._head_len = head_len
        self._timeout = timeout
        self._ws_url = ws_url
        self._http_config = http_config or {}
        self._concurrency = max(1, concurrency)
        self._total_requests = max(1, total_requests)
        self._qps_limit = max(0, qps_limit)
        self._duration = max(0, duration)
        self._warmup = max(0, warmup)
        self._ramp_step = max(0, ramp_step)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._done = 0
        self._success = 0
        self._fail = 0

    def stop(self) -> None:
        """请求停止压测（线程安全）。"""
        self._stop_event.set()

    def _send_once(self) -> bool:
        """执行一次请求，返回是否成功。"""
        if self._proto == "tcp_client":
            ok, _ = tcp_send_and_receive(
                self._ip, self._port, self._message,
                self._encoding, self._head_len, self._timeout,
            )
            return ok
        if self._proto == "http_client":
            try:
                kwargs = config_to_request_kwargs(self._http_config)
                requests.request(**kwargs)
                return True
            except Exception:
                return False
        url = self._ws_url or f"ws://{self._ip}:{self._port}/ws"
        ok, _ = ws_send_and_receive(url, self._message, self._timeout)
        return ok

    def _worker_loop(self, bucket: _TokenBucket, start: float) -> None:
        """单个压测线程：取令牌 → 占请求槽 → 发送 → 记录结果。"""
        while not self._stop_event.is_set():
            if self._duration > 0 and (time.monotonic() - start) >= self._duration:
                break
            with self._lock:
                if self._done >= self._total_requests:
                    break
                # 原子占用一个请求槽，避免并发线程一起越过总请求数边界
                self._done += 1
                slot = self._done
            bucket.acquire()
            if self._stop_event.is_set():
                break
            ok = self._send_once()
            with self._lock:
                if ok:
                    self._success += 1
                else:
                    self._fail += 1
                success, fail = self._success, self._fail
            if slot % 5 == 0 or slot >= self._total_requests:
                self.progress.emit(slot, success, fail)

    def run(self) -> None:
        start = time.monotonic()
        bucket = _TokenBucket(self._qps_limit)
        threads = []
        # 预热递增：先启动 ramp_step 个，之后分批补足到 concurrency
        active = self._concurrency
        if self._ramp_step > 0 and self._concurrency > self._ramp_step:
            active = self._ramp_step
        for _ in range(active):
            t = threading.Thread(target=self._worker_loop, args=(bucket, start), daemon=True)
            threads.append(t)
            t.start()
        if active < self._concurrency:
            steps = (self._concurrency - active + self._ramp_step - 1) // self._ramp_step
            interval = (self._warmup / steps) if (self._warmup > 0 and steps > 0) else 0.5
            nxt = time.monotonic() + interval
            while active < self._concurrency and not self._stop_event.is_set():
                if self._duration > 0 and (time.monotonic() - start) >= self._duration:
                    break
                time.sleep(0.05)
                if time.monotonic() >= nxt:
                    add = min(self._ramp_step, self._concurrency - active)
                    for _ in range(add):
                        t = threading.Thread(target=self._worker_loop, args=(bucket, start), daemon=True)
                        threads.append(t)
                        t.start()
                    active += add
                    nxt = time.monotonic() + interval
        # 等待：总请求数达 / 时长耗尽 / 全部线程退出 / 停止
        while not self._stop_event.is_set():
            with self._lock:
                done = self._done
            if done >= self._total_requests:
                break
            if self._duration > 0 and (time.monotonic() - start) >= self._duration:
                break
            if not any(t.is_alive() for t in threads):
                break
            time.sleep(0.05)
        self._stop_event.set()
        for t in threads:
            t.join(0.5)
        elapsed = time.monotonic() - start
        with self._lock:
            done, success, fail = self._done, self._success, self._fail
        self._last_elapsed = elapsed
        self.finished.emit(done, success, fail, elapsed)
