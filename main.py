"""TestTool 入口 —— 网络端口连通性检测与协议测试工具。

用法:
    python main.py                 # 启动 GUI
    python main.py --db <path>     # 指定数据库路径
    python main.py --cli <ip> <port> [<ip> <port> ...]  # 命令行模式
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QtMsgType, qFormatLogMessage, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def _install_qt_message_filter() -> None:
    """过滤 Qt 已知无害的 DirectWrite 字体警告，保持控制台干净。

    中文 Windows 上 Qt 探测遗留的 Fixedsys 字体时，DirectWrite 会打印
    "CreateFontFaceFromHDC() failed" 警告并自动回退，不影响功能。
    仅针对该条已知警告做过滤，其余 Qt 日志原样输出。
    """
    import sys

    def handler(mode, context, message):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtDebugMsg,
                    QtMsgType.QtInfoMsg) and "DirectWrite" in message \
                and ("Fixedsys" in message or "CreateFontFaceFromHDC" in message):
            return  # 已知无害的字体回退警告，忽略
        sys.stderr.write(qFormatLogMessage(mode, context, message))
        sys.stderr.flush()

    qInstallMessageHandler(handler)


def _get_icon_path() -> str:
    """获取图标文件路径（兼容源码运行和 PyInstaller 打包，Windows/Linux）。"""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    # Windows 优先 ICO，Linux 用 PNG
    for name in ("icon.ico", "icon.png"):
        icon = base / "resources" / name
        if icon.exists():
            return str(icon)
    return ""


def _make_fallback_icon() -> QIcon:
    """程序绘制的兜底图标：深色圆角方块 + 白色 TT。

    当图标文件缺失或 Qt 无法解码时使用，避免 QPixmap::scaled 空图警告。
    """
    try:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#2c3e50"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 22, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "TT")
        painter.end()
        return QIcon(pixmap)
    except Exception:
        return QIcon()


def _load_icon() -> QIcon:
    """加载窗口图标；文件缺失或 Qt 无法解码时返回程序绘制的兜底图标。"""
    icon_path = _get_icon_path()
    if icon_path:
        icon = QIcon(icon_path)
        if not icon.isNull():
            return icon
    return _make_fallback_icon()


def run_gui(db_path: str) -> None:
    """启动图形界面。"""
    _install_qt_message_filter()
    app = QApplication(sys.argv)
    app.setApplicationName("TestTool")
    app.setOrganizationName("TestTool")

    # 设置窗口图标（无效图标会被兜底替换，避免 QPixmap::scaled 空图警告）
    icon = _load_icon()
    app.setWindowIcon(icon)

    # 设置全局样式
    app.setStyle("Fusion")

    # 显式设置默认字体：中文 Windows 下避免 Qt 探测遗留的 Fixedsys 字体
    # （DirectWrite 无法加载 Fixedsys 时会打印一条无害警告并自动回退）
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft YaHei UI", 9))

    window = MainWindow(db_path)
    window.setWindowIcon(icon)
    window.show()

    sys.exit(app.exec())


def run_cli(targets: list[tuple[str, int]], timeout: float = 3.0) -> None:
    """命令行模式: 快速检测几个目标。"""
    from src.scanner import ScanTarget, scan_targets_sync

    scan_targets = [
        ScanTarget(id=0, ip=ip, port=port, description=f"{ip}:{port}")
        for ip, port in targets
    ]

    print(f"检测 {len(scan_targets)} 个目标 (超时: {timeout}s)...\n")

    def progress(current, total, result):
        status = "✓ 连通" if result.success else "✗ 未连通"
        latency = f" {result.latency_ms:.1f}ms" if result.success else ""
        error = f" - {result.error_msg}" if not result.success else ""
        print(f"  [{current}/{total}] {result.ip}:{result.port} {status}{latency}{error}")

    results = scan_targets_sync(scan_targets, timeout=timeout, progress_callback=progress)

    success = sum(1 for r in results if r.success)
    fail = len(results) - success
    print(f"\n--- 完成: {success} 连通, {fail} 未连通 ---")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TestTool - 网络端口连通性检测与协议测试工具"
    )
    parser.add_argument("--db", type=str, default="", help="SQLite 数据库路径")
    parser.add_argument("--cli", nargs="+", help="命令行模式: IP1 Port1 [IP2 Port2 ...]")
    parser.add_argument("--timeout", type=float, default=3.0, help="连接超时秒数 (默认3s)")

    args = parser.parse_args()

    if args.cli:
        # 命令行模式
        if len(args.cli) % 2 != 0:
            print("错误: IP 和 Port 必须成对出现。")
            sys.exit(1)
        targets = []
        for i in range(0, len(args.cli), 2):
            ip = args.cli[i]
            try:
                port = int(args.cli[i + 1])
            except ValueError:
                print(f"错误: 端口无效 '{args.cli[i + 1]}'")
                sys.exit(1)
            targets.append((ip, port))
        run_cli(targets, args.timeout)
    else:
        # GUI 模式
        if args.db:
            db_path = args.db
        elif getattr(sys, 'frozen', False):
            # PyInstaller 打包后：存到 exe 同目录，数据不丢失
            db_path = str(Path(sys.executable).parent / "testtool.db")
        else:
            # 源码运行：存到项目目录
            db_path = str(Path(__file__).resolve().parent / "testtool.db")
        run_gui(db_path)


if __name__ == "__main__":
    main()
