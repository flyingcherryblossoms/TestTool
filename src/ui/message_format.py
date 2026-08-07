"""报文格式化：按格式选择（text / json / xml）输出缩进排版。

供发送报文输入框的「格式化」按钮与右键菜单使用。
"""

from __future__ import annotations

import json
import xml.dom.minidom


def format_payload(text: str, fmt: str = "") -> tuple[str, str | None]:
    """把文本按指定格式 fmt 格式化为缩进形式。

    fmt 取值：
      - ""       自动识别：先按 JSON（json.dumps indent=2），失败再按 XML
      - "json"   仅按 JSON 格式化
      - "xml"    仅按 XML 格式化
      - "text"   不处理，原样返回

    返回 (formatted, None) 表示成功；否则 (原文本, 错误信息)。
    """
    stripped = text.strip()
    if not stripped:
        return text, "报文为空，无需格式化。"

    # ── text：不处理 ──
    if fmt == "text":
        return text, None

    # ── json：仅按 JSON ──
    if fmt == "json":
        try:
            obj = json.loads(stripped)
        except Exception as e:
            return text, f"JSON 格式化失败：{e}"
        return json.dumps(obj, ensure_ascii=False, indent=2), None

    # ── xml：仅按 XML ──
    if fmt == "xml":
        return _format_xml(stripped, text)

    # ── 自动识别：先 JSON 后 XML ──
    try:
        obj = json.loads(stripped)
    except Exception:
        obj = None
    if obj is not None:
        return json.dumps(obj, ensure_ascii=False, indent=2), None
    return _format_xml(stripped, text)


def _format_xml(stripped: str, original: str) -> tuple[str, str | None]:
    try:
        parsed = xml.dom.minidom.parseString(stripped)
    except Exception as e:
        return original, f"XML 格式化失败：{e}"
    formatted = parsed.toprettyxml(indent="  ")
    # toprettyxml 会在开头补一个 XML 声明；若原文本身没有声明则去掉
    if not stripped.lower().startswith("<?xml"):
        lines = [ln for ln in formatted.split("\n") if not ln.lstrip().lower().startswith("<?xml")]
        formatted = "\n".join(lines).strip()
    return formatted, None
