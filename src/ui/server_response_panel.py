"""服务端「返回报文」管理区。

位于服务端面板表格与日志 tabs 之间，管理每条服务端下的多条命名返回报文
（逻辑参考客户端预设报文列表）：
  - 左侧列表：返回报文名称，● 标记当前返回项，未保存项追加 *；
  - 右侧编辑区：HTTP 服务端含状态码 + 响应头表格 + 内容，TCP/WS 仅内容；
  - 选中列表项即「当前返回内容」，切换时若编辑区有未保存改动先确认；
  - 保存（按钮或 Ctrl+S）写库并经 saved 信号热推给运行中的 Worker。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.database import Database, default_response_messages, parse_server_responses
from src.ui import shortcuts
from src.ui.format_text import FormatTextEdit
from src.ui.http_client import _CompactKvTable


class ResponseMessageSection(QWidget):
    """服务端返回报文列表 + 编辑区。

    set_server(srv) 由外部（ServerPanelBase 选中行变化）驱动；
    saved(sid) 在保存/切换当前返回后发出，供外部热推运行中的 Worker。
    """

    saved = Signal(int)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._panel = None               # ServerPanelBase（取消时恢复表格选中）
        self._srv = None                 # 当前服务端 ProtocolServer
        self._responses: list[dict] = []  # 返回报文编辑副本
        self._sel_idx: int | None = None
        self._dirty = False
        self._loading = False            # 装载期间屏蔽编辑信号
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._stack = QStackedWidget()
        # 页 0：未选中提示
        hint = QLabel("请选择服务端")
        hint.setAlignment(Qt.AlignCenter)
        self._stack.addWidget(hint)

        # 页 1：列表 + 编辑区
        editor = QWidget()
        ed = QHBoxLayout(editor)
        ed.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left.setFixedWidth(176)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.currentRowChanged.connect(self._on_select)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_menu)
        lv.addWidget(self._list)
        row1 = QHBoxLayout()
        for text, slot in (("添加", self._add), ("重命名", self._rename),
                           ("删除", self._delete)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row1.addWidget(btn)
        lv.addLayout(row1)
        row2 = QHBoxLayout()
        save_btn = QPushButton("保存 (Ctrl+S)")
        save_btn.clicked.connect(self._save)
        row2.addWidget(save_btn)
        row2.addStretch(1)
        lv.addLayout(row2)
        ed.addWidget(left)

        self._edit_widget = QWidget()
        ev = QVBoxLayout(self._edit_widget)
        ev.setContentsMargins(6, 0, 0, 0)

        # HTTP 服务端专属：状态码 + 响应头
        self._http_box = QWidget()
        hb = QVBoxLayout(self._http_box)
        hb.setContentsMargins(0, 0, 0, 0)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("状态码:"))
        self._status_spin = QSpinBox()
        self._status_spin.setRange(100, 599)
        self._status_spin.setValue(200)
        self._status_spin.valueChanged.connect(self._mark_dirty)
        status_row.addWidget(self._status_spin)
        status_row.addStretch(1)
        hb.addLayout(status_row)
        hb.addWidget(QLabel("响应头:"))
        self._headers_table = _CompactKvTable(2, ("名称", "值"))
        self._headers_table._table.itemChanged.connect(self._mark_dirty)
        hb.addWidget(self._headers_table)
        ev.addWidget(self._http_box)

        body_row = QHBoxLayout()
        body_row.addWidget(QLabel("内容:"))
        self._body = FormatTextEdit()
        self._body.textChanged.connect(self._mark_dirty)
        body_row.addWidget(self._body.format_combo)
        ev.addLayout(body_row)
        ev.addWidget(self._body, 1)
        ed.addWidget(self._edit_widget, 1)

        self._stack.addWidget(editor)
        layout.addWidget(self._stack)

        self._save_shortcut = shortcuts.make_shortcut(
            self, "save", self._save, context=Qt.WidgetWithChildrenShortcut)

    # ── 对外接口 ──────────────────────────────────────────

    def set_panel(self, panel):
        """绑定 ServerPanelBase，用于切换取消时恢复表格选中行。"""
        self._panel = panel

    def set_server(self, srv):
        """切换到指定服务端（None 表示无选中）；有未保存改动先确认。"""
        if self._srv is not None and srv is not None and self._srv.id == srv.id:
            return
        if not self._confirm_save_if_dirty():
            if self._panel is not None and self._srv is not None:
                self._panel._restore_selection(self._srv.id)
            return
        self._srv = srv
        if srv is None:
            self._stack.setCurrentIndex(0)
            self._responses = []
            self._sel_idx = None
            self._dirty = False
            return
        self._responses = parse_server_responses(srv)
        self._sel_idx = next(
            (i for i, it in enumerate(self._responses) if it["active"]), 0)
        self._stack.setCurrentIndex(1)
        self._reload_list()
        self._load_editor()

    # ── 列表与编辑区 ───────────────────────────────────────

    def _reload_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for i, it in enumerate(self._responses):
            item = QListWidgetItem(self._item_text(i))
            item.setData(Qt.UserRole, i)
            self._list.addItem(item)
        if self._sel_idx is not None and 0 <= self._sel_idx < len(self._responses):
            self._list.setCurrentRow(self._sel_idx)
        self._list.blockSignals(False)

    def _item_text(self, idx: int) -> str:
        it = self._responses[idx]
        text = ("● " if it.get("active") else "") + it["name"]
        if self._dirty and idx == self._sel_idx:
            text += " *"
        return text

    def _update_item_label(self):
        if self._sel_idx is None:
            return
        item = self._list.item(self._sel_idx)
        if item:
            item.setText(self._item_text(self._sel_idx))

    def _load_editor(self):
        if self._sel_idx is None or self._srv is None:
            return
        it = self._responses[self._sel_idx]
        is_http = self._srv.server_type == "http_server"
        self._http_box.setVisible(is_http)
        self._loading = True
        self._status_spin.setValue(it.get("status_code", 200))
        self._headers_table.set_data(
            [tuple(x) for x in (it.get("headers") or [])])
        self._body.setPlainText(it.get("body", ""))
        self._loading = False
        self._dirty = False

    def _mark_dirty(self, *args):
        if self._loading or self._srv is None:
            return
        if not self._dirty:
            self._dirty = True
            self._update_item_label()

    def _on_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("添加", self._add)
        if self._list.itemAt(pos) is not None:
            menu.addAction("重命名", self._rename)
            menu.addAction("删除", self._delete)
        menu.addSeparator()
        menu.addAction("保存", self._save)
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _on_select(self, idx: int):
        if idx < 0 or self._srv is None or idx == self._sel_idx:
            return
        if not self._confirm_save_if_dirty():
            self._list.blockSignals(True)
            self._list.setCurrentRow(self._sel_idx)
            self._list.blockSignals(False)
            return
        self._sel_idx = idx
        self._load_editor()
        self._set_active(idx)

    # ── 增删改存 ──────────────────────────────────────────

    def _set_active(self, idx: int):
        """把 idx 设为当前返回报文：更新 active 标记并持久化 + 热推。"""
        if self._srv is None:
            return
        for i, it in enumerate(self._responses):
            it["active"] = (i == idx)
        self._reload_list()
        self._persist()
        self.saved.emit(self._srv.id)

    def _save(self):
        if self._srv is None or self._sel_idx is None:
            return
        it = self._responses[self._sel_idx]
        it["status_code"] = self._status_spin.value()
        it["headers"] = self._read_headers()
        it["body"] = self._body.toPlainText()
        self._dirty = False
        self._persist()
        self._update_item_label()
        self.saved.emit(self._srv.id)

    def _add(self):
        if self._srv is None:
            return
        if not self._confirm_save_if_dirty():
            return
        name, ok = QInputDialog.getText(self, "添加返回报文", "名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(it["name"] == name for it in self._responses):
            QMessageBox.warning(self, "重名", "已存在同名返回报文。")
            return
        self._responses.append({"name": name, "active": False,
                                "status_code": 200, "headers": [], "body": ""})
        self._sel_idx = len(self._responses) - 1
        self._set_active(self._sel_idx)
        self._load_editor()

    def _rename(self):
        if self._srv is None or self._sel_idx is None:
            return
        it = self._responses[self._sel_idx]
        name, ok = QInputDialog.getText(self, "重命名返回报文", "名称:",
                                        text=it["name"])
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(i != self._sel_idx and r["name"] == name
               for i, r in enumerate(self._responses)):
            QMessageBox.warning(self, "重名", "已存在同名返回报文。")
            return
        it["name"] = name
        self._persist()
        self._update_item_label()

    def _delete(self):
        if self._srv is None or self._sel_idx is None:
            return
        it = self._responses[self._sel_idx]
        ret = QMessageBox.question(self, "删除返回报文",
                                   f"确定删除返回报文「{it['name']}」吗？",
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        del self._responses[self._sel_idx]
        if not self._responses:
            self._responses = default_response_messages()
            self._sel_idx = 0
        else:
            self._sel_idx = min(self._sel_idx, len(self._responses) - 1)
        self._set_active(self._sel_idx)
        self._load_editor()

    # ── 持久化 ────────────────────────────────────────────

    def _confirm_save_if_dirty(self) -> bool:
        """有未保存改动时确认；返回 False 表示取消切换。"""
        if not self._dirty or self._srv is None:
            return True
        ret = QMessageBox.question(
            self, "未保存的修改", "当前返回报文有未保存的修改，是否保存？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if ret == QMessageBox.Save:
            self._save()
            return True
        if ret == QMessageBox.Discard:
            self._dirty = False
            return True
        return False

    def _read_headers(self) -> list:
        """从响应头表格读取 [key, value] 列表（跳过空键）。"""
        rows = []
        t = self._headers_table._table
        for r in range(t.rowCount()):
            k_item = t.item(r, 0)
            v_item = t.item(r, 1)
            k = k_item.text().strip() if k_item else ""
            if not k:
                continue
            rows.append([k, v_item.text() if v_item else ""])
        return rows

    def _persist(self):
        """把返回报文列表写库；response_message 保持为 active body 的镜像。"""
        if self._srv is None or not self._responses:
            return
        # 重取最新服务端配置，避免覆盖对话框对响应模式/延迟的修改
        fresh = self._db.get_protocol_server(self._srv.id)
        if fresh is None:
            return
        srv = fresh
        self._srv = fresh
        active = next((it for it in self._responses if it["active"]),
                      self._responses[0])
        payload = json.dumps(self._responses, ensure_ascii=False)
        self._db.update_protocol_server(
            server_id=srv.id, name=srv.name, server_type=srv.server_type,
            ip=srv.ip, port=srv.port, encoding=srv.encoding,
            recv_encoding=srv.recv_encoding, head_length=srv.head_length,
            ws_path=srv.ws_path, response_mode=srv.response_mode,
            response_message=active["body"],
            response_messages=payload,
            response_delay=srv.response_delay, target_id=srv.target_id)
        srv.response_messages = payload
