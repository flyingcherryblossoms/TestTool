"""协议测试引擎 —— TCP/WebSocket 客户端与服务端。

移植自 TestUtil 的长度前缀帧协议，扩展 WebSocket 支持。
引擎的 start() 方法设计为阻塞式（在调用线程中运行 accept/event loop），
配合 QThread 使用：run() 调用 start() 自然阻塞，stop() 从主线程关闭。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Callable, Optional

# ── 常量 ────────────────────────────────────────────────────

MAX_MESSAGE_BYTES = 10 * 1024 * 1024  # 10 MB
READ_CHUNK_SIZE = 4096
ACCEPT_TIMEOUT = 1.0  # accept() 超时间隔，用于检查停止信号


# ── 消息帧封装（移植自 TestUtil MessageUtils.java）────────────


def compute_length_header(message: str, encoding: str, head_len: int) -> str:
    """计算长度头，例如 head_len=5 返回 "00042"。"""
    if head_len <= 0:
        return ""
    body_bytes = message.encode(encoding)
    return str(len(body_bytes)).zfill(head_len)


def pack_message(message: str, encoding: str, head_len: int) -> bytes:
    """将消息打包为 [长度头字节] + [报文体字节]。head_len=0 时返回原始报文体。"""
    body_bytes = message.encode(encoding)
    if head_len <= 0:
        return body_bytes
    header_str = str(len(body_bytes)).zfill(head_len)
    return header_str.encode("ascii") + body_bytes


def write_message(sock: socket.socket, message: str, encoding: str,
                  head_len: int) -> None:
    """向 socket 写入一条帧封装的消息并 flush。"""
    data = pack_message(message, encoding, head_len)
    sock.sendall(data)


def _read_exactly(sock: socket.socket, n: int,
                  timeout: Optional[float] = None) -> bytes:
    """从 socket 精确读取 n 个字节。"""
    if timeout is not None:
        sock.settimeout(timeout)
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), READ_CHUNK_SIZE))
        if not chunk:
            raise ConnectionError(
                f"连接关闭 (已读取 {len(buf)}/{n} 字节)"
            )
        buf.extend(chunk)
    return bytes(buf)


def read_message_bytes(sock: socket.socket, head_len: int,
                       timeout: Optional[float] = None) -> bytes:
    """从 socket 读取一条帧封装的原始报文体字节。

    - head_len > 0: 读取 head_len 字节作为长度头，解析后读取报文体
    - head_len == 0: 读取直到 EOF
    """
    if head_len > 0:
        header_bytes = _read_exactly(sock, head_len, timeout)
        try:
            body_len = int(header_bytes.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"无效的长度头: {header_bytes!r}")

        if body_len < 0:
            raise ValueError(f"负数的消息长度: {body_len}")
        if body_len > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"消息体过大 ({body_len} 字节)，最大允许 {MAX_MESSAGE_BYTES} 字节"
            )
        body_bytes = _read_exactly(sock, body_len, timeout)
    else:
        buf = bytearray()
        if timeout is not None:
            sock.settimeout(timeout)
        while True:
            try:
                chunk = sock.recv(READ_CHUNK_SIZE)
            except socket.timeout:
                if len(buf) == 0:
                    raise
                break
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_MESSAGE_BYTES:
                raise ValueError(f"消息体过大 (>{MAX_MESSAGE_BYTES} 字节)")
        body_bytes = bytes(buf)

    return body_bytes


def read_message(sock: socket.socket, encoding: str, head_len: int,
                 timeout: Optional[float] = None) -> str:
    """从 socket 读取一条帧封装的消息并解码。"""
    body_bytes = read_message_bytes(sock, head_len, timeout)
    return body_bytes.decode(encoding)


# ── TCP 客户端（移植自 TestUtil Client.java）──────────────────


def tcp_send_and_receive(
    ip: str, port: int, message: str, encoding: str,
    head_len: int, timeout: float
) -> tuple[bool, str]:
    """一次性 TCP 请求：连接、打包发送、半关闭、接收、关闭。

    Returns:
        (success, response_or_error_message)
    """
    sock = None
    start = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        write_message(sock, message, encoding, head_len)
        sock.shutdown(socket.SHUT_WR)
        response = read_message(sock, encoding, head_len, timeout)
        elapsed = (time.perf_counter() - start) * 1000
        return True, response

    except socket.timeout:
        elapsed = (time.perf_counter() - start) * 1000
        return False, f"连接超时 ({elapsed:.0f}ms, 阈值 {timeout:.0f}s)"
    except ConnectionRefusedError:
        return False, f"连接被拒绝: {ip}:{port}"
    except socket.gaierror as e:
        return False, f"地址解析失败: {e}"
    except ConnectionError as e:
        return False, f"连接错误: {e}"
    except ValueError as e:
        return False, f"协议错误: {e}"
    except UnicodeEncodeError as e:
        return False, f"编码失败 ({encoding}): {e}"
    except UnicodeDecodeError as e:
        return False, f"解码失败 ({encoding}): {e}"
    except OSError as e:
        return False, f"网络错误: {e}"
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


# ── TCP 服务端（移植自 TestUtil Server.java）──────────────────


class TcpServerEngine:
    """TCP 服务端引擎。

    start() 在调用线程中阻塞运行 accept 循环，直到 stop() 从另一个线程调用。
    配合 QThread 使用: run() -> engine.start() 自然阻塞。
    """

    def __init__(
        self,
        ip: str,
        port: int,
        encoding: str,
        head_len: int,
        on_message: Callable[[str, str], str],
        on_status: Callable[[str], None],
        on_error: Callable[[str], None],
        recv_encoding: Optional[str] = None,
        on_message_raw: Optional[Callable[[str, bytes], None]] = None,
    ):
        self._ip = ip
        self._port = port
        self._encoding = encoding
        self._recv_encoding = recv_encoding or encoding
        self._head_len = head_len
        self._on_message = on_message
        self._on_status = on_status
        self._on_error = on_error
        self._on_message_raw = on_message_raw

        self._server_sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        """启动监听（阻塞当前线程，直到 stop() 被调用）。"""
        if self._running:
            return

        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self._ip, self._port))
            self._server_sock.listen(5)
            self._server_sock.settimeout(ACCEPT_TIMEOUT)
        except OSError as e:
            self._on_error(f"启动监听失败 {self._ip}:{self._port}: {e}")
            self._running = False
            return

        self._running = True
        self._stop_event.clear()
        self._on_status(f"监听已启动 {self._ip}:{self._port}")

        # 在当前线程中运行 accept 循环（阻塞）
        try:
            while self._running and not self._stop_event.is_set():
                try:
                    client_sock, client_addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not self._running:
                    try:
                        client_sock.close()
                    except OSError:
                        pass
                    break

                self._handle_client(client_sock, client_addr)
        finally:
            self._running = False

    def stop(self) -> None:
        """停止监听（从另一个线程调用，解除 start() 的阻塞）。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        self._on_status(f"监听已停止 {self._ip}:{self._port}")

    def is_running(self) -> bool:
        return self._running

    def set_encodings(self, encoding: str,
                      recv_encoding: Optional[str] = None) -> None:
        """运行时更新发送/接收编码（供主线程在服务端运行中调整）。"""
        self._encoding = encoding
        if recv_encoding:
            self._recv_encoding = recv_encoding

    def _handle_client(self, client_sock: socket.socket,
                       client_addr: tuple) -> None:
        """处理单个客户端连接（同步）。"""
        addr_str = f"{client_addr[0]}:{client_addr[1]}"
        try:
            with client_sock:
                body_bytes = read_message_bytes(client_sock, self._head_len)
                message = body_bytes.decode(self._recv_encoding)
                self._on_status(f"收到消息 来自 {addr_str}")
                if self._on_message_raw:
                    self._on_message_raw(addr_str, body_bytes)
                response = self._on_message(addr_str, message)
                write_message(
                    client_sock, response, self._encoding, self._head_len
                )
                self._on_status(f"已回复 来自 {addr_str}")
        except (ConnectionError, socket.timeout, ValueError,
                UnicodeDecodeError, OSError) as e:
            self._on_error(f"处理客户端 {addr_str} 时出错: {e}")


# ── WebSocket 客户端 ─────────────────────────────────────────


def ws_send_and_receive(
    url: str, message: str, timeout: float,
    ssl_verify: bool = True
) -> tuple[bool, str]:
    """一次性 WebSocket 请求：连接、发送、接收、关闭。

    Returns:
        (success, response_or_error_message)
    """
    try:
        from websocket import create_connection, WebSocketTimeoutError
    except ImportError:
        return False, "请安装 websocket-client: pip install websocket-client"

    ws = None
    start = time.perf_counter()
    try:
        ws = create_connection(url, timeout=timeout, enable_multithread=False)
        ws.send(message)
        response = ws.recv()
        elapsed = (time.perf_counter() - start) * 1000
        return True, response

    except WebSocketTimeoutError:
        elapsed = (time.perf_counter() - start) * 1000
        return False, f"WebSocket 超时 ({elapsed:.0f}ms, 阈值 {timeout:.0f}s)"
    except ConnectionRefusedError:
        return False, f"WebSocket 连接被拒绝: {url}"
    except OSError as e:
        return False, f"WebSocket 网络错误: {e}"
    except Exception as e:
        return False, f"WebSocket 错误: {e}"
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


# ── WebSocket 服务端 ─────────────────────────────────────────


class WsServerEngine:
    """WebSocket 服务端引擎。

    start() 在调用线程中阻塞运行 asyncio 事件循环，直到 stop() 从另一个线程调用。
    """

    def __init__(
        self,
        ip: str,
        port: int,
        path: str,
        on_message: Callable[[str], str],
        on_client_event: Callable[[str], None],
        on_status: Callable[[str], None],
        on_error: Callable[[str], None],
    ):
        self._ip = ip
        self._port = port
        self._path = path
        self._on_message = on_message
        self._on_client_event = on_client_event
        self._on_status = on_status
        self._on_error = on_error

        self._running = False
        self._server = None

    def start(self) -> None:
        """启动 WebSocket 服务端（阻塞当前线程直到 stop()）。"""
        if self._running:
            return

        try:
            import websockets
        except ImportError:
            self._on_error("请安装 websockets: pip install websockets")
            return

        self._running = True

        async def handler(websocket):
            client_info = (
                f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
                if websocket.remote_address else "未知客户端"
            )
            self._on_client_event(f"客户端已连接: {client_info}")
            try:
                async for message in websocket:
                    self._on_status(f"收到 WS 消息 来自 {client_info}")
                    response = self._on_message(message)
                    await websocket.send(response)
                    self._on_status(f"已回复 WS 消息 到 {client_info}")
            except websockets.exceptions.ConnectionClosed:
                self._on_client_event(f"客户端已断开: {client_info}")
            except Exception as e:
                self._on_error(f"WS 处理错误: {e}")

        async def serve():
            try:
                self._server = await websockets.serve(
                    handler, self._ip, self._port,
                )
                self._on_status(
                    f"WebSocket 服务已启动 ws://{self._ip}:{self._port}{self._path or '/'}"
                )
                # 持续运行直到被 stop() 停止
                while self._running:
                    await asyncio.sleep(0.5)
            except OSError as e:
                self._on_error(f"WebSocket 服务启动失败 {self._ip}:{self._port}: {e}")
            except Exception as e:
                self._on_error(f"WebSocket 服务错误: {e}")
            finally:
                if self._server:
                    self._server.close()
                    await self._server.wait_closed()
                self._running = False

        try:
            asyncio.run(serve())
        except Exception as e:
            self._on_error(f"WebSocket 事件循环异常: {e}")
        finally:
            self._running = False

    def stop(self) -> None:
        """停止 WebSocket 服务端（从另一个线程调用）。"""
        if not self._running:
            return
        self._running = False
        self._on_status(f"WebSocket 服务已停止 {self._ip}:{self._port}")

    def is_running(self) -> bool:
        return self._running
