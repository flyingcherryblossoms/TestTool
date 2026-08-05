"""协议测试 Worker 线程。

提供 TCP/WebSocket 客户端和服务端的 QThread 封装。
服务端 Worker 的 run() 直接调用引擎的阻塞式 start()，
stop_server() 从主线程调用引擎的 stop() 解除阻塞。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from src.protocol import (
    TcpServerEngine,
    WsServerEngine,
    tcp_send_and_receive,
    ws_send_and_receive,
)


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
                 parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._encoding = encoding
        self._recv_encoding = recv_encoding or encoding
        self._head_len = head_len
        self._response_mode = response_mode
        self._response_message = response_message
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
        if self._response_mode == "echo":
            return message
        return self._response_message

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)

    def _on_error(self, error: str) -> None:
        self.error_occurred.emit(error)

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
                 response_mode: str, response_message: str, parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._path = path
        self._response_mode = response_mode
        self._response_message = response_message
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
        if self._response_mode == "echo":
            return message
        return self._response_message

    def _on_client_event(self, event: str) -> None:
        self.client_event.emit(event)

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)

    def _on_error(self, error: str) -> None:
        self.error_occurred.emit(error)

    def stop_server(self) -> None:
        """从主线程停止 WebSocket 服务端。"""
        if self._engine:
            self._engine.stop()
        self.wait(3000)
