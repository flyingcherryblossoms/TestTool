"""带格式选择 / 语法高亮 / 字体缩放的文本编辑组件。

基于 QPlainTextEdit（协议报文本质是纯文本，禁用富文本粘贴以免混入 HTML），
通过 QSyntaxHighlighter 实现 JSON / XML 语法着色，视觉上即"富文本框"。

交互：
  - 格式下拉（text / json / xml）切换语法高亮
  - Ctrl + 滚轮、Ctrl + 加号 / 减号 调整字号
  - Ctrl + 0 恢复默认字号

供客户端发送报文、服务端响应内容、目标对话框发送报文统一使用。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QComboBox, QPlainTextEdit

from src.ui import shortcuts


def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    return f


# ── 配色 ──────────────────────────────────────────────────
_COL_KEY = _fmt("#007BFF")          # JSON 键       蓝
_COL_STRING = _fmt("#28A745")       # 字符串        绿
_COL_NUMBER = _fmt("#E67E22")       # 数字          橙
_COL_CONST = _fmt("#E74C3C", True)  # true/false/null 红
_COL_COMMENT = _fmt("#95A5A6")      # 注释          灰
_COL_PROC = _fmt("#9C27B0")         # XML 声明      紫
_COL_TAG = _fmt("#0056B3", True)    # XML 标签名    深蓝
_COL_ATTR_NAME = _fmt("#17A2B8")    # XML 属性名    青
_COL_ATTR_VAL = _fmt("#28A745")     # XML 属性值    绿


class FormatHighlighter(QSyntaxHighlighter):
    """按当前格式对文本块着色：text 不着色，json / xml 分别高亮。"""

    _JSON_KEY = re.compile(r'("(?:\\.|[^"\\])*")\s*(?=:)')
    _JSON_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
    _JSON_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
    _JSON_CONST = re.compile(r"\b(?:true|false|null)\b")

    _XML_COMMENT = re.compile(r"<!--.*?-->")
    _XML_PROC = re.compile(r"<\?.*?\?>")
    _XML_TAG = re.compile(r"</?[a-zA-Z_][\w.:-]*")
    _XML_ATTR_NAME = re.compile(r"[a-zA-Z_][\w.:-]*(?=\s*=)")
    _XML_ATTR_VAL = re.compile(r'"[^"]*"|\'[^\']*\'|"(?:\\.|[^"\\])*"')

    def __init__(self, document):
        super().__init__(document)
        self._fmt = "text"

    def set_format(self, fmt: str) -> None:
        self._fmt = fmt
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if self._fmt == "json":
            self._apply_rules(text, [
                (self._JSON_KEY, _COL_KEY),
                (self._JSON_STRING, _COL_STRING),
                (self._JSON_NUMBER, _COL_NUMBER),
                (self._JSON_CONST, _COL_CONST),
            ])
        elif self._fmt == "xml":
            self._apply_rules(text, [
                (self._XML_COMMENT, _COL_COMMENT),
                (self._XML_PROC, _COL_PROC),
                (self._XML_ATTR_VAL, _COL_ATTR_VAL),
                (self._XML_ATTR_NAME, _COL_ATTR_NAME),
                (self._XML_TAG, _COL_TAG),
            ])

    def _apply_rules(self, text: str, rules: list) -> None:
        """按规则优先级着色：高优先级先着色，低优先级跳过重叠区。"""
        spans = []
        for pattern, fmt in rules:
            for m in pattern.finditer(text):
                if m.groups():
                    s, e = m.start(1), m.end(1)   # 只用第 1 捕获组着色
                else:
                    s, e = m.span()
                if any(not (e <= os or s >= oe) for (os, oe, _) in spans):
                    continue
                spans.append((s, e, fmt))
        for s, e, fmt in spans:
            self.setFormat(s, e - s, fmt)


class FormatTextEdit(QPlainTextEdit):
    """协议文本编辑组件：格式下拉 + 语法高亮 + Ctrl 缩放。"""

    FORMATS = ["text", "json", "xml"]

    def __init__(self, text: str = "", format_name: str = "text", parent=None):
        super().__init__(text, parent)
        base = self.font().pointSize()
        self._base_point_size = base if base > 0 else 10
        self._highlighter = FormatHighlighter(self.document())
        # 格式下拉（由外部布局决定摆放位置）
        self.format_combo = QComboBox()
        for f in self.FORMATS:
            self.format_combo.addItem(f.upper(), f)
        self.format_combo.currentIndexChanged.connect(self._on_format_combo)
        self.set_format(format_name)

    # ── 格式 ──────────────────────────────────────────────

    def _on_format_combo(self, idx: int):
        fmt = self.format_combo.itemData(idx)
        if fmt:
            self._format = fmt
            self._highlighter.set_format(fmt)

    def set_format(self, name: str) -> None:
        """切换格式并同步下拉框与高亮。"""
        if name not in self.FORMATS:
            name = "text"
        self._format = name
        idx = self.format_combo.findData(name)
        if idx >= 0 and idx != self.format_combo.currentIndex():
            self.format_combo.blockSignals(True)
            self.format_combo.setCurrentIndex(idx)
            self.format_combo.blockSignals(False)
        self._highlighter.set_format(name)

    def current_format(self) -> str:
        return self._format

    # ── Ctrl 缩放 ─────────────────────────────────────────

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom(1)
            elif delta < 0:
                self._zoom(-1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "zoom_in"):
            self._zoom(1)
            return
        if shortcuts.event_matches(event, "zoom_out"):
            self._zoom(-1)
            return
        if shortcuts.event_matches(event, "zoom_reset"):
            self._reset_zoom()
            return
        super().keyPressEvent(event)

    def _zoom(self, step: int) -> None:
        f = self.font()
        if f.pointSize() > 0:
            f.setPointSize(max(5, min(72, f.pointSize() + step)))
        else:
            f.setPixelSize(max(6, min(96, f.pixelSize() + step)))
        self.setFont(f)

    def _reset_zoom(self) -> None:
        f = self.font()
        f.setPointSize(self._base_point_size)
        self.setFont(f)
