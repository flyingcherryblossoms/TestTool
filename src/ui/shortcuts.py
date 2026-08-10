"""全局快捷键注册表：默认绑定 + 用户自定义，存 app_settings。

所有可配置快捷键在此集中注册（动作 id → 默认绑定），
各 widget 的 keyPressEvent 通过 event_matches() 查询当前生效绑定；
QShortcut 型绑定经 make_shortcut() 注册，apply_shortcuts() 热更新。

存储格式：app_settings["keyboard_shortcuts"] = JSON {action_id: [键序列,...]}
键序列使用 Qt 标准字符串（如 "Ctrl+Return"、"F5"、"Ctrl++"）。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut

# 设置存储键
SETTINGS_KEY = "keyboard_shortcuts"

# 参与匹配的修饰键（QKeySequence 组合里的标准四键）
_MOD_MASK = (Qt.ControlModifier | Qt.AltModifier
             | Qt.ShiftModifier | Qt.MetaModifier)
# 加号/等号在 Ctrl 下等效：键盘布局常以 Ctrl+= 触发 Ctrl++，Qt 不自动归一化
_PLUS_KEYS = {int(Qt.Key_Plus), int(Qt.Key_Equal)}

# ── 动作注册表（有序）：(id, 描述, 默认绑定列表) ──────────────
ACTIONS = [
    ("send",         "发送报文",       ["Ctrl+Return"]),
    ("save",         "保存预设/参数",  ["Ctrl+S"]),
    ("refresh",      "刷新当前列表",   ["F5"]),
    ("delete",       "删除选中项",     ["Delete", "Ctrl+D"]),
    ("copy",         "复制选中项",     ["Ctrl+C"]),
    ("paste",        "粘贴",           ["Ctrl+V"]),
    ("edit_preset",  "编辑预设",       ["F2"]),
    ("format_body",  "HTTP 报文格式化", ["Ctrl+Shift+F"]),
    ("zoom_in",      "放大字号",       ["Ctrl++", "Ctrl+="]),
    ("zoom_out",     "缩小字号",       ["Ctrl+-"]),
    ("zoom_reset",   "恢复字号",       ["Ctrl+0"]),
]

DEFAULTS: dict[str, list[str]] = {aid: list(seqs) for aid, _, seqs in ACTIONS}

# 当前生效绑定（模块级，keyPressEvent 每击键实时读取）
_active: dict[str, list[str]] = {aid: list(seqs) for aid, _, seqs in ACTIONS}

# 已注册的 QShortcut 对象：(shortcut, action_id)，供 apply_shortcuts 热更新
_shortcut_objects: list[tuple[QShortcut, str]] = []


def set_active(shortcuts: dict) -> None:
    """替换当前生效绑定（与默认合并，保证新动作总是存在）。"""
    for aid, seqs in DEFAULTS.items():
        _active[aid] = list(shortcuts.get(aid, seqs))


def load(db) -> dict:
    """从数据库读取用户绑定并设为当前生效值，返回合并后的完整字典。"""
    raw = db.get_setting(SETTINGS_KEY, "")
    stored = {}
    if raw:
        try:
            stored = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            stored = {}
    merged = {aid: list(seqs) for aid, seqs in DEFAULTS.items()}
    for aid, seqs in stored.items():
        if aid in DEFAULTS and isinstance(seqs, list):
            merged[aid] = [str(s) for s in seqs]
    set_active(merged)
    return merged


def save(db, shortcuts: dict) -> None:
    """把绑定字典持久化到数据库。"""
    db.set_setting(SETTINGS_KEY, json.dumps(shortcuts, ensure_ascii=False))


def current(action_id: str) -> list[str]:
    """当前动作的全部绑定（可能为空列表 = 禁用）。"""
    return list(_active.get(action_id, DEFAULTS.get(action_id, [])))


def keyseq(action_id: str) -> QKeySequence:
    """动作当前绑定的 QKeySequence（空绑定返回空序列以禁用 QShortcut）。"""
    seqs = current(action_id)
    return QKeySequence(seqs[0]) if seqs else QKeySequence()


def _mods_match(ev_mods, seq_mods) -> bool:
    """比较修饰键是否一致（忽略 NumLock 等非标准修饰位）。"""
    return (ev_mods & _MOD_MASK) == (seq_mods & _MOD_MASK)


def event_matches(event, action_id: str) -> bool:
    """keyPressEvent 里判断事件是否命中该动作的任一当前绑定。

    QKeyEvent.matches 在本版 PySide6 只接受 StandardKey，因此改为逐键
    比对 key+modifiers；另把 Ctrl+Plus / Ctrl+Equal 视为等效（不同键盘
    布局常以 Ctrl+= 触发 Ctrl++）。
    """
    ev_key = event.key()
    ev_mods = event.modifiers()
    for seq in current(action_id):
        if not seq:
            continue
        ks = QKeySequence(seq)
        if ks.count() == 0:
            continue
        kc = ks[0]
        bkey = int(kc.key())
        if not _mods_match(ev_mods, kc.keyboardModifiers()):
            continue
        if ev_key == bkey:
            return True
        if ev_key in _PLUS_KEYS and bkey in _PLUS_KEYS:
            return True
    return False


def make_shortcut(parent, action_id: str, slot, context=Qt.WindowShortcut):
    """创建绑定到动作的 QShortcut 并注册热更新，返回该 QShortcut。"""
    sc = QShortcut(keyseq(action_id), parent)
    sc.setContext(context)
    sc.activated.connect(slot)
    _shortcut_objects.append((sc, action_id))
    return sc


def apply_shortcuts() -> None:
    """把当前生效绑定热更新到所有已注册的 QShortcut。"""
    for sc, action_id in _shortcut_objects:
        sc.setKey(keyseq(action_id))
