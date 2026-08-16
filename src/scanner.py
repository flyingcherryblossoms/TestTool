"""网络连通性检测引擎。

使用线程池并发测试 TCP 连通性，并通过 QThread + Signal 机制
将进度实时推送到 GUI 线程。
支持 IP CIDR/范围、端口范围展开，以及端口扫描功能。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class ScanTarget:
    """待检测的目标。"""
    id: int
    ip: str
    port: int
    description: str = ""


@dataclass
class ScanResult:
    """单条检测结果。"""
    target_id: int
    ip: str
    port: int
    description: str = ""
    success: bool = False
    latency_ms: float = 0.0
    error_msg: str = ""


# ── IP 地址 / 端口范围展开 ──────────────────────────────────


def expand_ip_range(ip_spec: str) -> list[str]:
    """将 IP 规格展开为单个 IP 列表。

    支持格式:
      - 单个 IP:   192.168.1.1
      - CIDR 网段: 192.168.1.0/24
      - 完整范围:   192.168.1.1-192.168.1.10
      - 简短范围:   192.168.1.1-10
    """
    ip_spec = ip_spec.strip()

    # CIDR 网段
    if "/" in ip_spec:
        try:
            network = ipaddress.ip_network(ip_spec, strict=False)
            # 限制最多展开 65536 个地址
            hosts = list(network.hosts())
            if len(hosts) > 65536:
                raise ValueError(f"IP 范围过大 ({len(hosts)} 个地址)，最多支持 65536 个")
            return [str(ip) for ip in hosts]
        except ValueError:
            pass

    # 范围格式: 192.168.1.1-192.168.1.10 或 192.168.1.1-10
    if "-" in ip_spec:
        parts = ip_spec.split("-")
        if len(parts) == 2:
            start_ip = parts[0].strip()
            end_spec = parts[1].strip()
            if "." in end_spec:
                end_ip = end_spec
            else:
                prefix = start_ip.rsplit(".", 1)[0]
                end_ip = f"{prefix}.{end_spec}"
            try:
                start = ipaddress.IPv4Address(start_ip)
                end = ipaddress.IPv4Address(end_ip)
                if start > end:
                    raise ValueError(f"起始 IP 不能大于结束 IP")
                count = int(end) - int(start) + 1
                if count > 65536:
                    raise ValueError(f"IP 范围过大 ({count} 个地址)，最多支持 65536 个")
                return [str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1)]
            except ValueError as e:
                raise ValueError(f"IP 范围无效: {e}")

    # 单个 IP  — 做基本校验
    try:
        ipaddress.IPv4Address(ip_spec)
        return [ip_spec]
    except ipaddress.AddressValueError:
        raise ValueError(f"无效的 IP 地址: {ip_spec}")


def expand_port_range(port_spec: str) -> list[int]:
    """将端口规格展开为端口列表。

    支持格式:
      - 单个端口:   80
      - 范围:       1-100
      - 逗号分隔:   80,443,8080
      - 混合:       80,443,8000-8010
    """
    ports: set[int] = set()
    for part in port_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            range_parts = part.split("-")
            if len(range_parts) != 2:
                raise ValueError(f"无效的端口范围: {part}")
            try:
                start = int(range_parts[0])
                end = int(range_parts[1])
            except ValueError:
                raise ValueError(f"无效的端口范围: {part}")
            if start > end:
                start, end = end, start
            if end - start + 1 > 65536:
                raise ValueError(f"端口范围过大: {part}")
            for p in range(start, end + 1):
                if 1 <= p <= 65535:
                    ports.add(p)
        else:
            try:
                p = int(part)
                if 1 <= p <= 65535:
                    ports.add(p)
                else:
                    raise ValueError(f"端口超出范围: {p}")
            except ValueError:
                raise ValueError(f"无效的端口: {part}")
    if not ports:
        raise ValueError("端口规格为空")
    return sorted(ports)


def build_scan_targets(ip_spec: str, port_spec: str,
                        description_template: str = "") -> list[ScanTarget]:
    """根据 IP 和端口规格生成待检测目标列表。

    description_template 中可用 {ip} 和 {port} 占位符。
    """
    ips = expand_ip_range(ip_spec)
    ports = expand_port_range(port_spec)
    targets = []
    for ip in ips:
        for port in ports:
            desc = description_template.format(ip=ip, port=port) if description_template else f"{ip}:{port}"
            targets.append(ScanTarget(id=0, ip=ip, port=port, description=desc))
    return targets


# ── 底层 TCP 连通性检测 ────────────────────────────────────


def test_tcp_connect(ip: str, port: int, timeout: float = 3.0) -> tuple[bool, float, str]:
    """检测单个 IP:Port 的 TCP 连通性。

    Returns:
        (success, latency_ms, error_msg)
    """
    start = time.perf_counter()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        elapsed = (time.perf_counter() - start) * 1000
        if result == 0:
            return True, elapsed, ""
        else:
            return False, elapsed, f"连接失败 (错误码: {result})"
    except socket.gaierror:
        return False, 0.0, f"无法解析地址: {ip}"
    except socket.timeout:
        return False, timeout * 1000, f"连接超时 ({timeout:.0f}s)"
    except OSError as e:
        return False, 0.0, f"网络错误: {e}"
    finally:
        if sock:
            sock.close()


def scan_targets_sync(targets: list[ScanTarget], timeout: float = 3.0,
                       max_workers: int = 30,
                       progress_callback=None,
                       cancel_event: threading.Event | None = None) -> list[ScanResult]:
    """同步（阻塞式）并发检测多个目标。

    使用 ThreadPoolExecutor 并发执行 TCP 连接测试，
    每个目标完成时调用 progress_callback(current, total, result)。
    如果 cancel_event 被设置，跳过尚未开始检测的目标。
    """
    results: list[ScanResult] = []
    total = len(targets)
    completed = 0
    skipped = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {}
        for t in targets:
            if cancel_event and cancel_event.is_set():
                skipped += 1
                continue
            future_to_target[executor.submit(test_tcp_connect, t.ip, t.port, timeout)] = t

        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                success, latency, error = future.result(timeout=timeout + 2)
            except FutureTimeout:
                success, latency, error = False, timeout * 1000, "检测超时"
            except Exception as e:
                success, latency, error = False, 0.0, str(e)

            result = ScanResult(
                target_id=target.id,
                ip=target.ip,
                port=target.port,
                description=target.description,
                success=success,
                latency_ms=round(latency, 2),
                error_msg=error,
            )
            results.append(result)
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed + skipped, total, result)
                except Exception:
                    pass  # 回调异常不影响检测

    return results


# ── Qt 工作线程 ────────────────────────────────────────────


class ScannerWorker(QThread):
    """在后台线程执行连通性检测的 QThread Worker。

    Signals:
        progress:    current, total, ScanResult  — 每完成一个目标触发
        finished_all: list[ScanResult]            — 全部完成时触发
        error:       str                          — 发生严重错误时触发
    """

    progress = Signal(int, int, object)   # current, total, ScanResult
    finished_all = Signal(list)            # list[ScanResult]
    error_occurred = Signal(str)           # error message

    def __init__(self, targets: list[ScanTarget], timeout: float = 3.0,
                 max_workers: int = 30, parent=None):
        super().__init__(parent)
        self._targets = targets
        self._timeout = timeout
        self._max_workers = max_workers
        self._cancel_event = threading.Event()
        self._emit_lock = threading.Lock()

    def run(self) -> None:
        """在工作线程中执行检测（由 start() 触发）。"""
        try:
            results = scan_targets_sync(
                targets=self._targets,
                timeout=self._timeout,
                max_workers=self._max_workers,
                progress_callback=self._on_progress,
                cancel_event=self._cancel_event,
            )
            if not self._cancel_event.is_set():
                self.finished_all.emit(results)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def cancel(self) -> None:
        """请求取消。正在执行的连接会尽快完成，未开始的将被跳过。"""
        self._cancel_event.set()

    def _on_progress(self, current: int, total: int, result: ScanResult) -> None:
        """内部进度回调，在多个线程池线程中调用，加锁保护信号发射。"""
        if not self._cancel_event.is_set():
            with self._emit_lock:
                self.progress.emit(current, total, result)
