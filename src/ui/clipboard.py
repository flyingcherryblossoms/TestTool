"""应用内剪贴板：Ctrl+C / Ctrl+V 在各类列表间复制粘贴列表项。

每种列表类型有独立槽位（同类型间可跨集合/跨面板粘贴），由 KIND_* 常量标识。
载荷为纯 dict/list，与数据库解耦 —— 复制时抓取数据，粘贴时据此重建。
"""

from __future__ import annotations

# 剪贴板类型标识
KIND_PRESET = "preset"            # 预设报文 {name, message}
KIND_PROTO_TARGET = "proto_target"  # 协议目标（含其下挂服务端）
KIND_PROTO_SERVER = "proto_server"  # 协议服务端（监听器）
KIND_COLLECTION = "collection"    # 集合深拷贝（存集合 id，粘贴时重新拷贝）
KIND_CONN_TARGET = "conn_target"  # 连通测试目标 {ip, port, description}

_clip: dict[str, list] = {}


def copy_items(kind: str, items: list) -> None:
    """复制选中项到应用内剪贴板（覆盖同类型旧内容）。"""
    if items:
        _clip[kind] = list(items)


def paste_items(kind: str) -> list:
    """取回指定类型的剪贴板内容（无则返回空列表）。"""
    return list(_clip.get(kind, []))
