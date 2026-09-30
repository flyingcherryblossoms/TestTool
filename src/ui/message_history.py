"""发送/接收报文列表及独立详情视图。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from src.ui.message_format import format_payload


def _hex_dump(data: bytes) -> str:
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:04x}  {hex_part:<48}  {ascii_part}")
    return "\n".join(lines)


class MessageHistoryWidget(QWidget):
    """逐条显示报文；原始报文和日志按需展开。"""

    MAX_MESSAGES = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages: list[dict] = []
        self.encoding = "UTF-8"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.original_toggle = QPushButton("原报文")
        self.original_toggle.setCheckable(True)
        self.original_toggle.toggled.connect(self.refresh_detail)
        toolbar.addWidget(self.original_toggle)
        self.hex_toggle = QPushButton("十六进制")
        self.hex_toggle.setCheckable(True)
        self.hex_toggle.toggled.connect(self.refresh_detail)
        toolbar.addWidget(self.hex_toggle)
        self.log_toggle = QPushButton("日志")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        toolbar.addWidget(self.log_toggle)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(150)
        self.list.currentRowChanged.connect(self.refresh_detail)
        splitter.addWidget(self.list)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 10))
        self.detail.setPlaceholderText("点击左侧记录查看报文...")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 600])
        layout.addWidget(splitter)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 10))
        self.log_edit.setPlaceholderText("运行日志将在这里显示...")
        self.log_edit.setVisible(False)
        layout.addWidget(self.log_edit)

    def _toggle_log(self, checked: bool):
        self.log_edit.setVisible(checked)
        self.log_toggle.setText("收起日志" if checked else "日志")

    def add_message(self, direction: str, text: str, *, raw: bytes = None,
                    payload: str = None, peer: str = "") -> None:
        """添加独立报文，payload 是可格式化的正文，text 是原始展示内容。"""
        body = payload if payload is not None else text
        preview = body.strip().splitlines()[0][:28] if body.strip() else "(空报文)"
        stamp = datetime.now().strftime("%H:%M:%S")
        label = f"{stamp}  {direction}  {preview}"
        if len(self.messages) >= self.MAX_MESSAGES:
            self.messages.pop(0)
            self.list.takeItem(0)
        self.messages.append({"direction": direction, "text": text,
                              "payload": body, "raw": raw})
        self.list.addItem(label)
        self.list.item(len(self.messages) - 1).setToolTip(
            f"{direction} {peer}\n{text[:500]}" if peer else f"{direction}\n{text[:500]}")
        self.list.setCurrentRow(len(self.messages) - 1)

    def update_message(self, idx: int, text: str, *, payload: str = None) -> None:
        """用实际发出的报文替换请求前的预览。"""
        if not 0 <= idx < len(self.messages):
            return
        record = self.messages[idx]
        record["text"] = text
        record["payload"] = payload if payload is not None else text
        preview = record["payload"].strip().splitlines()
        preview = preview[0][:28] if preview else "(空报文)"
        label = self.list.item(idx).text().split("  ", 2)
        if len(label) >= 2:
            self.list.item(idx).setText(f"{label[0]}  {label[1]}  {preview}")
        self.list.item(idx).setToolTip(f"{record['direction']}\n{text[:500]}")
        if self.list.currentRow() == idx:
            self.refresh_detail()

    def attach_raw(self, direction: str, raw: bytes) -> None:
        """为最近一条同方向记录补上实际网络字节。"""
        for record in reversed(self.messages):
            if record["direction"] == direction and record["raw"] is None:
                record["raw"] = raw
                self.refresh_detail()
                return

    def set_encoding(self, encoding: str) -> None:
        self.encoding = encoding or "UTF-8"
        self.refresh_detail()

    def refresh_detail(self, *_args):
        idx = self.list.currentRow()
        if not 0 <= idx < len(self.messages):
            self.detail.clear()
            return
        record = self.messages[idx]
        raw = record["raw"]
        if self.hex_toggle.isChecked() and raw is not None:
            self.detail.setPlainText(_hex_dump(raw))
            return
        original = record["text"]
        payload = record["payload"]
        if raw is not None and record["direction"] == "接收" and payload == original:
            try:
                original = raw.decode(self.encoding)
            except LookupError:
                original = raw.decode("utf-8", errors="replace")
            except UnicodeError:
                original = raw.decode(self.encoding, errors="replace")
            payload = original
        if self.original_toggle.isChecked():
            self.detail.setPlainText(original)
            return
        formatted, error = format_payload(payload)
        self.detail.setPlainText(payload if error else formatted)

    def clear_messages(self):
        self.messages.clear()
        self.list.clear()
        self.detail.clear()
        self.log_edit.clear()
