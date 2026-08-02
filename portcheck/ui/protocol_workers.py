"""协议测试 Worker 线程。

提供 TCP/WebSocket 客户端和服务端的 QThread 封装。
服务端 Worker 的 run() 直接调用引擎的阻塞式 start()，
stop_server() 从主线程调用引擎的 stop() 解除阻塞。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from portcheck.protocol import (
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
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, server_id: int, ip: str, port: int, encoding: str,
                 head_len: int, response_mode: str,
                 response_message: str, parent=None):
        super().__init__(parent)
        self._server_id = server_id
        self._ip = ip
        self._port = port
        self._encoding = encoding
        self._head_len = head_len
        self._response_mode = response_mode
        self._response_message = response_message
        self._engine: TcpServerEngine | None = None

    def run(self) -> None:
        """在 QThread 中阻塞运行 accept 循环。"""
        self._engine = TcpServerEngine(
            ip=self._ip,
            port=self._port,
            encoding=self._encoding,
            head_len=self._head_len,
            on_message=self._on_message_received,
            on_status=self._on_status,
            on_error=self._on_error,
        )
        # start() 阻塞当前线程直到 stop() 被调用
        self._engine.start()

    def _on_message_received(self, client_addr: str, message: str) -> str:
        self.message_received.emit(client_addr, message)
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
