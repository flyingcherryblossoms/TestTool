"""CSP 报文解析对话框 —— 从 Hex Dump 中提取字节，按可选编码解码并格式化。

原程序只按单一编码解码导致中文乱码（如 UTF-8 中文被按 GBK 读出乱码），
本工具允许选择/输入解码编码或自动检测，解析后支持 JSON / XML / 纯文本格式化，
并对结果做 JSON / XML 语法高亮（复用 format_text.FormatHighlighter）。
编码选错时同样容错解码并展示错误结果（无效字节替换为 �，状态栏橙色提示），
便于与正确编码的结果对比、修正。

输入格式为带偏移地址与 ASCII 列的十六进制 Dump（如报文日志导出格式），
解析时仅提取十六进制部分，忽略偏移、ASCII 列及 DataLength/--- 头尾说明行；
也兼容直接粘贴纯十六进制字符串。
"""

from __future__ import annotations

import json
import re
import xml.dom.minidom

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.ui import shortcuts
from src.ui.format_text import FormatHighlighter
from src.ui.message_format import format_payload

# 与协议组件保持一致的可选编码
ENCODINGS = ["UTF-8", "GBK", "GB2312", "GB18030", "ISO-8859-1", "ASCII"]
# 编码下拉首位为自动检测
AUTO_ENCODING = "自动检测"

# 输出格式：(显示名, format_payload 用格式)
FORMATS = [("自动识别", ""), ("JSON", "json"), ("XML", "xml"), ("纯文本", "text")]

# 标准 Hex Dump 行：偏移地址 + 十六进制字节 [+ ASCII 列]
_OFFSET_LINE = re.compile(r"^\s*[0-9a-fA-F]{8}h?\s*:\s*(.*)$")
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{2}")
_ASCII_SEPARATOR = re.compile(r"\s{2,}")  # 十六进制与 ASCII 列之间的分隔
_DATALENGTH = re.compile(r"^\s*DataLength\s*=\s*(\d+)\s*$", re.IGNORECASE)


def extract_hex_bytes(text: str) -> bytes:
    """从 Hex Dump 文本中提取原始字节。

    优先按「偏移地址 + 十六进制」格式逐行解析（仅取十六进制部分，忽略
    ASCII 列与 DataLength/--- 头尾行）；若无偏移格式，则将整段视为
    十六进制字符串提取（支持带空格与不带空格两种写法）。
    """
    lines = text.splitlines()
    tokens: list[str] = []
    dump_found = False
    for line in lines:
        m = _OFFSET_LINE.match(line)
        if not m:
            continue
        dump_found = True
        hex_part = _ASCII_SEPARATOR.split(m.group(1), maxsplit=1)[0]
        tokens.extend(_HEX_TOKEN.findall(hex_part))
    if not dump_found:
        tokens = _HEX_TOKEN.findall(text)
    return bytes(int(t, 16) for t in tokens)


def detect_encoding(raw: bytes) -> str | None:
    """自动检测编码：按候选顺序尝试完整解码，成功即返回。"""
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return None


class CspParserDialog(QDialog):
    """CSP 报文解析对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_decoded_text = ""  # 最近一次成功解码的文本，供「格式化」重排
        self.setWindowTitle("CSP 报文解析")
        self.setMinimumSize(760, 640)
        self.resize(820, 700)
        self._setup_ui()
        shortcuts.make_shortcut(self, "send", self._parse)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 输入区 ──────────────────────────────────────────
        input_group = QGroupBox("输入报文（Hex Dump，仅取十六进制部分）")
        input_layout = QVBoxLayout(input_group)

        self._input_edit = QPlainTextEdit()
        self._input_edit.setPlaceholderText(
            "粘贴带偏移的十六进制 Dump（自动忽略偏移、ASCII 列与 DataLength/--- 说明行），\n"
            "也可直接粘贴纯十六进制字符串，例如: 7B 22 72 65 73 70 ...\n\n"
            "00000000h: 7B 22 72 65 73 70 48 65 61 64 65 72 22 3A 7B 22   {\"respHeader\":{\"\n"
            "00000010h: 66 6F 72 6D 61 74 56 65 72 22 3A 22 76 31 22 2C   formatVer\":\"v1\",\n"
        )
        self._input_edit.setMinimumHeight(170)
        # Consolas 本身是等宽字体，勿加 setStyleHint(QFont.Monospace)：
        # 后者会让 Qt 走通用等宽字体匹配，部分 Windows 环境探测到遗留的
        # Fixedsys，触发 DirectWrite 警告（全项目统一用 QFont("Consolas") 不带 hint）
        mono = QFont("Consolas")
        self._input_edit.setFont(mono)
        input_layout.addWidget(self._input_edit)

        layout.addWidget(input_group)

        # ── 解析设置 ────────────────────────────────────────
        settings_layout = QHBoxLayout()

        settings_layout.addWidget(QLabel("解码编码:"))
        self._encoding_combo = QComboBox()
        self._encoding_combo.setEditable(True)  # 允许输入自定义编码
        self._encoding_combo.addItem(AUTO_ENCODING)
        self._encoding_combo.addItems(ENCODINGS)
        self._encoding_combo.setCurrentText("UTF-8")
        self._encoding_combo.setMinimumWidth(150)
        settings_layout.addWidget(self._encoding_combo)

        settings_layout.addWidget(QLabel("输出格式:"))
        self._format_combo = QComboBox()
        for label, fmt in FORMATS:
            self._format_combo.addItem(label, fmt)
        self._format_combo.setMinimumWidth(110)
        settings_layout.addWidget(self._format_combo)
        self._format_combo.currentIndexChanged.connect(self._reformat_last)

        settings_layout.addSpacing(12)

        self._parse_btn = QPushButton("解析")
        self._parse_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #2980b9; padding: 6px 18px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self._parse_btn.clicked.connect(self._parse)
        settings_layout.addWidget(self._parse_btn)

        self._format_btn = QPushButton("格式化")
        self._format_btn.setToolTip("对已解析文本按所选输出格式重排")
        self._format_btn.clicked.connect(self._reformat_last)
        settings_layout.addWidget(self._format_btn)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._clear_all)
        settings_layout.addWidget(self._clear_btn)

        settings_layout.addStretch()
        layout.addLayout(settings_layout)

        # ── 解析结果 ────────────────────────────────────────
        result_group = QGroupBox("解析结果")
        result_layout = QVBoxLayout(result_group)

        self._result_edit = QPlainTextEdit()
        self._result_edit.setReadOnly(True)
        self._result_edit.setFont(mono)
        result_layout.addWidget(self._result_edit)
        self._highlighter = FormatHighlighter(self._result_edit.document())

        self._status_label = QLabel("请粘贴报文后点击「解析」。")
        self._status_label.setStyleSheet("color: #888;")
        result_layout.addWidget(self._status_label)

        layout.addWidget(result_group)

    # ── 解析逻辑 ───────────────────────────────────────────

    @staticmethod
    def _resolve_format(fmt: str, text: str) -> str:
        """把输出格式选择解析为实际格式（json / xml / text），供格式化与高亮使用。

        fmt 为下拉框数据："" 自动识别 / "json" / "xml" / "text"。
        自动识别按 JSON → XML → 纯文本的顺序判断内容类型。
        """
        if fmt in ("json", "xml"):
            return fmt
        if fmt == "text":
            return "text"
        try:
            json.loads(text)
            return "json"
        except Exception:
            pass
        try:
            xml.dom.minidom.parseString(text)
            return "xml"
        except Exception:
            return "text"

    def _parse(self):
        text = self._input_edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "提示", "请先粘贴要解析的报文。")
            return

        raw = extract_hex_bytes(text)
        if not raw:
            QMessageBox.warning(self, "解析失败", "未提取到十六进制字节，请检查输入内容。")
            return

        # 解码：编码选错时也容错解码并展示错误结果，便于对比后修正
        enc = self._encoding_combo.currentText().strip() or AUTO_ENCODING
        note = ""
        if enc == AUTO_ENCODING:
            detected = detect_encoding(raw)
            if detected is None:
                decoded = raw.decode("utf-8", errors="replace")
                used_enc = "UTF-8(容错)"
                note = "自动检测未找到可完整解码的编码，已按 UTF-8 容错展示"
            else:
                used_enc = detected
                decoded = raw.decode(detected)
        else:
            try:
                decoded = raw.decode(enc)
                used_enc = enc
            except (UnicodeDecodeError, UnicodeEncodeError):
                decoded = raw.decode(enc, errors="replace")
                used_enc = f"{enc}(容错)"
                note = f"按 {enc} 解码不完整，无效字节已替换为 �"

        self._show_result(decoded, raw, used_enc, note)

    def _show_result(self, decoded: str, raw: bytes, used_enc: str,
                     note: str = ""):
        """展示解码结果：格式化 + 语法高亮 + 状态说明。"""
        self._last_decoded_text = decoded
        resolved = self._resolve_format(self._format_combo.currentData(), decoded)
        if resolved == "text":
            # 自动识别下的纯文本内容：原样展示，不视为格式化失败
            self._result_edit.setPlainText(decoded)
            self._highlighter.set_format("text")
        else:
            formatted, err = format_payload(decoded, resolved)
            if err:
                self._result_edit.setPlainText(decoded)
                self._highlighter.set_format("text")
                note = f"{note}；格式化失败：{err}" if note else f"格式化失败：{err}"
            else:
                self._result_edit.setPlainText(formatted)
                self._highlighter.set_format(resolved)
        self._update_status(raw, used_enc, note)

    def _update_status(self, raw: bytes, used_enc: str, note: str = ""):
        parts = [f"共 {len(raw)} 字节 / 使用编码 {used_enc}"]
        declared = self._declared_length()
        if declared is not None:
            match = "一致" if declared == len(raw) else "不一致"
            parts.append(f"（输入声明 DataLength={declared}，{match}）")
        if note:
            parts.append(note)
        self._set_status("，".join(parts), warn=bool(note))

    def _declared_length(self) -> int | None:
        for line in self._input_edit.toPlainText().splitlines():
            m = _DATALENGTH.match(line)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
        return None

    def _reformat_last(self):
        """用当前输出格式对最近一次解码结果重排并同步语法高亮。"""
        if not self._last_decoded_text:
            return
        text = self._last_decoded_text
        resolved = self._resolve_format(self._format_combo.currentData(), text)
        if resolved == "text":
            self._result_edit.setPlainText(text)
            self._highlighter.set_format("text")
        else:
            formatted, err = format_payload(text, resolved)
            if err:
                self._result_edit.setPlainText(text)
                self._highlighter.set_format("text")
                self._set_status(f"格式化失败：{err}", warn=True)
                return
            self._result_edit.setPlainText(formatted)
            self._highlighter.set_format(resolved)
        self._set_status("已按所选格式重排。")

    def _clear_all(self):
        self._input_edit.clear()
        self._result_edit.clear()
        self._last_decoded_text = ""
        self._highlighter.set_format("text")
        self._set_status("请粘贴报文后点击「解析」。")

    def _set_status(self, text: str, warn: bool = False):
        # 正常信息灰色，编码选错/格式化失败等警告橙色醒目
        self._status_label.setStyleSheet(
            "color: #e67e22;" if warn else "color: #888;"
        )
        self._status_label.setText(text)

    # ── 快捷键提示 ─────────────────────────────────────────

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "send"):
            self._parse()
            return
        super().keyPressEvent(event)
