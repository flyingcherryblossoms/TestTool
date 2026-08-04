"""协议测试面板 —— 左侧集合列表 + 右侧动态目标标签页。

结构:
  QSplitter(Horizontal)
  ├── [左] _CollectionSidebar
  └── [右] QTabWidget
        ├── Tab 0: _CollectionDetailTab (目标表格，双击打开目标标签页)
        ├── Tab 1: _StandaloneClientTab (独立客户端，固定)
        ├── Tab 2: _ServerTab (全部服务端，固定)
        ├── Tab 3: _GlobalHistoryTab (全局测试历史，固定)
        └── [动态] 目标标签页 (客户端参数 / 发送响应 / 历史 / Mock服务端)
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.protocol import compute_length_header
from src.ui.protocol_workers import (
    TcpClientWorker,
    TcpServerWorker,
    WsClientWorker,
    WsServerWorker,
)
from src.json_handler import (
    export_collection_to_json,
    import_collection_from_json,
    export_client_config,
    export_server_config,
)



ENCODINGS = ["UTF-8", "GBK", "GB2312", "GB18030", "ISO-8859-1", "ASCII"]


def _target_proto_label(target) -> str:
    """根据目标参数推断实际协议类型。"""
    is_tcp = bool(target.head_length or target.encoding != "UTF-8")
    is_ws = bool(target.ws_path and target.ws_path.startswith("ws"))
    if is_tcp and is_ws:
        return "TCP/WS"
    elif is_ws:
        return "WS"
    else:
        return "TCP"


def _slot(fn, *args):
    def handler(*_sig_args):
        fn(*args)
    return handler


# ── 对话框 ──────────────────────────────────────────────────


class ServerDialog(QDialog):
    """添加/编辑服务端监听器的对话框。"""

    def __init__(self, title: str, server_type: str,
                 server: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._server_type = server_type
        self._is_tcp = server_type == "tcp_server"
        layout = QFormLayout(self)

        self._name_edit = QLineEdit(server.get("name", "") if server else "")
        self._name_edit.setPlaceholderText("例如: 生产环境监听")
        layout.addRow("名称:", self._name_edit)
        self._ip_edit = QLineEdit(server.get("ip", "0.0.0.0") if server else "0.0.0.0")
        layout.addRow("监听地址:", self._ip_edit)
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(server.get("port", 35126) if server else 35126)
        layout.addRow("端口:", self._port_spin)

        if self._is_tcp:
            self._encoding_combo = QComboBox()
            self._encoding_combo.setEditable(True)
            self._encoding_combo.addItems(ENCODINGS)
            enc = server.get("encoding", "UTF-8") if server else "UTF-8"
            self._encoding_combo.setCurrentText(enc)
            layout.addRow("编码:", self._encoding_combo)
            self._head_len_spin = QSpinBox()
            self._head_len_spin.setRange(0, 20)
            self._head_len_spin.setToolTip("0=原始模式")
            self._head_len_spin.setSuffix(" 位")
            self._head_len_spin.setValue(server.get("head_length", 5) if server else 5)
            layout.addRow("HeadLen:", self._head_len_spin)
        else:
            self._encoding_combo = None
            self._head_len_spin = None
            self._ws_path_edit = QLineEdit(server.get("ws_path", "/") if server else "/")
            layout.addRow("路径:", self._ws_path_edit)

        self._response_mode_combo = QComboBox()
        self._response_mode_combo.addItem("固定响应", "fixed")
        self._response_mode_combo.addItem("回显模式", "echo")
        if server and server.get("response_mode") == "echo":
            self._response_mode_combo.setCurrentIndex(1)
        self._response_mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addRow("响应模式:", self._response_mode_combo)
        self._response_edit = QPlainTextEdit()
        self._response_edit.setPlaceholderText("输入固定响应内容...")
        self._response_edit.setMaximumHeight(120)
        if server:
            self._response_edit.setPlainText(server.get("response_message", ""))
        layout.addRow("响应内容:", self._response_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_mode_changed(self, idx: int):
        is_echo = self._response_mode_combo.currentData() == "echo"
        self._response_edit.setVisible(not is_echo)

    def _on_accept(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "验证失败", "名称不能为空。")
            return
        self.accept()

    def get_data(self) -> dict:
        data = {
            "name": self._name_edit.text().strip(),
            "server_type": self._server_type,
            "ip": self._ip_edit.text().strip(),
            "port": self._port_spin.value(),
            "response_mode": self._response_mode_combo.currentData(),
            "response_message": self._response_edit.toPlainText(),
        }
        if self._is_tcp:
            data["encoding"] = self._encoding_combo.currentText()
            data["head_length"] = self._head_len_spin.value()
        else:
            data["encoding"] = "UTF-8"
            data["head_length"] = 0
            data["ws_path"] = self._ws_path_edit.text().strip() or "/"
        return data


class _TargetDialog(QDialog):
    """添加/编辑协议目标对话框。"""
    def __init__(self, title: str, ip: str = "", port: int = 35126,
                 desc: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(350)
        layout = QFormLayout(self)
        self._ip = QLineEdit(ip)
        self._ip.setPlaceholderText("192.168.1.1")
        layout.addRow("IP:", self._ip)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        layout.addRow("端口:", self._port)
        self._desc = QLineEdit(desc)
        layout.addRow("描述:", self._desc)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: self.accept() if self._ip.text().strip() else
                                 QMessageBox.warning(self, "验证失败", "IP 不能为空。"))
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> tuple[str, int, str]:
        return (
            self._ip.text().strip(),
            self._port.value(),
            self._desc.text().strip()
        )


# ── 集合侧边栏 ──────────────────────────────────────────────


class _CollectionSidebar(QWidget):
    """固定在左侧的测试集合列表 —— 分类树形结构。"""

    collection_selected = Signal(object)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._current_coll = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>测试集合</b>"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_list)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_list_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selected)
        self._tree.setIndentation(16)
        layout.addWidget(self._tree)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("新建", clicked=self._on_new))
        bl.addWidget(QPushButton("编辑", clicked=self._on_edit))
        bl.addWidget(QPushButton("删除", clicked=self._on_delete))
        layout.addLayout(bl)

        bl2 = QHBoxLayout()
        bl2.addWidget(QPushButton("导入", clicked=self._import_collection))
        bl2.addWidget(QPushButton("导出", clicked=self._export_collection))
        layout.addLayout(bl2)
        self._refresh_list()

    def _refresh_list(self):
        self._tree.blockSignals(True)
        self._tree.clear()

        # 确保"未分类"集合存在
        uncat_coll = None
        for c in self._db.get_all_protocol_collections():
            if c.name == "未分类":
                uncat_coll = c
                break
        if not uncat_coll:
            cid = self._db.add_protocol_collection(name="未分类", protocol_type="tcp_client")
            uncat_coll = self._db.get_protocol_collection(cid)

        collections = self._db.get_all_protocol_collections()
        bold_font = self._tree.font()
        bold_font.setBold(True)

        # 分离"未分类"集合和其他
        other_colls = []
        for c in collections:
            if c.name != "未分类":
                other_colls.append(c)

        # ── 一级节点：未分类（始终显示）──
        uncat_count = len(self._db.get_protocol_targets(uncat_coll.id))
        u = QTreeWidgetItem([f"未分类 ({uncat_count})"])
        u.setData(0, Qt.UserRole, uncat_coll.id)
        u.setFont(0, bold_font)
        self._tree.addTopLevelItem(u)

        # ── 父节点：自定义集合 ──
        custom_parent = QTreeWidgetItem([f"自定义集合 ({len(other_colls)})"])
        custom_parent.setData(0, Qt.UserRole, None)
        custom_parent.setFont(0, bold_font)
        custom_parent.setFlags(custom_parent.flags() & ~Qt.ItemIsSelectable)
        self._tree.addTopLevelItem(custom_parent)

        for c in other_colls:
            target_count = len(self._db.get_protocol_targets(c.id))
            child = QTreeWidgetItem([f"{c.name} ({target_count})"])
            child.setData(0, Qt.UserRole, c.id)
            custom_parent.addChild(child)

        custom_parent.setExpanded(True)
        self._tree.blockSignals(False)
        if not self._tree.currentItem():
            self._tree.setCurrentItem(u)
        if self._search.text().strip():
            self._filter_list(self._search.text())

    def _filter_list(self, text: str):
        s = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.childCount() == 0:
                top.setHidden(s not in top.text(0).lower() if s else False)
            else:
                any_visible = False
                for j in range(top.childCount()):
                    child = top.child(j)
                    match = s in child.text(0).lower() if s else True
                    child.setHidden(not match)
                    if match:
                        any_visible = True
                top.setHidden(not any_visible if s else False)

    def _on_tree_selected(self, current, previous):
        if not current:
            return
        cid = current.data(0, Qt.UserRole)
        if cid is None:
            self._current_coll = None
            self.collection_selected.emit(None)
            return
        self._current_coll = self._db.get_protocol_collection(cid)
        self.collection_selected.emit(self._current_coll)

    def refresh(self):
        self._refresh_list()

    def _on_list_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        cid = item.data(0, Qt.UserRole)
        if cid is None:
            return
        coll = self._db.get_protocol_collection(cid)
        is_uncat = coll and coll.name == "未分类"
        menu = QMenu(self)
        if not is_uncat:
            menu.addAction("编辑", self._on_edit)
            menu.addAction("删除", self._on_delete)
        menu.addSeparator()
        menu.addAction("导出集合", self._export_collection)
        menu.exec(self._tree.mapToGlobal(pos))

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "新建测试集合", "集合名称:")
        if not ok or not name.strip():
            return
        cid = self._db.add_protocol_collection(name=name.strip(), protocol_type="tcp_client")
        self._refresh_list()
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.UserRole) == cid:
                    self._tree.setCurrentItem(child)
                    return

    def _on_edit(self):
        if not self._current_coll or self._current_coll.name == "未分类":
            return
        name, ok = QInputDialog.getText(self, "编辑集合", "集合名称:", text=self._current_coll.name)
        if ok and name.strip():
            self._db.update_protocol_collection(
                self._current_coll.id, name=name.strip(),
                protocol_type=self._current_coll.protocol_type
            )
            self._refresh_list()

    def _on_delete(self):
        if not self._current_coll or self._current_coll.name == "未分类":
            return
        r = QMessageBox.question(
            self, "确认删除",
            f"确定要删除集合 [{self._current_coll.name}] 吗？\n\n此操作将同时删除所有关联的目标和服务端。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self._db.delete_protocol_collection(self._current_coll.id)
            self._current_coll = None
            self._refresh_list()

    def _import_collection(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "导入集合", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        coll_data, err = import_collection_from_json(filepath)
        if err:
            QMessageBox.critical(self, "导入失败", err)
            return
        cid = self._db.add_protocol_collection(
            name=coll_data["name"], protocol_type=coll_data["protocol_type"],
            description=coll_data["description"]
        )
        for t in coll_data["targets"]:
            presets = json.dumps(t.get("send_presets", []), ensure_ascii=False)
            tid = self._db.add_protocol_target(
                collection_id=cid, ip=t["ip"], port=t["port"],
                description=t["description"], encoding=t["encoding"],
                head_length=t["head_length"], timeout=t["timeout"],
                ws_path=t["ws_path"], ws_use_ssl=t["ws_use_ssl"],
                send_message=t["send_message"], send_presets=presets,
            )
            for s in t.get("servers", []):
                self._db.add_protocol_server(
                    name=s["name"], server_type=s["server_type"],
                    ip=s["ip"], port=s["port"], encoding=s["encoding"],
                    head_length=s["head_length"], ws_path=s["ws_path"],
                    response_mode=s["response_mode"],
                    response_message=s["response_message"], target_id=tid,
                )
        self._refresh_list()
        QMessageBox.information(self, "导入完成", f"成功导入集合 [{coll_data['name']}]，含 {len(coll_data['targets'])} 个目标。")

    def _export_collection(self):
        if not self._current_coll:
            QMessageBox.information(self, "提示", "请先选择一个集合。")
            return
        coll = self._current_coll
        targets = self._db.get_protocol_targets(coll.id)
        targets_data = []
        for t in targets:
            servers = self._db.get_protocol_servers_by_target(t.id)
            try:
                presets = json.loads(t.send_presets) if t.send_presets else []
            except json.JSONDecodeError:
                presets = []
            targets_data.append({
                "ip": t.ip, "port": t.port, "description": t.description,
                "encoding": t.encoding, "head_length": t.head_length,
                "timeout": t.timeout, "ws_path": t.ws_path,
                "ws_use_ssl": t.ws_use_ssl, "send_message": t.send_message,
                "send_presets": presets,
                "servers": [{"name": s.name, "server_type": s.server_type,
                             "ip": s.ip, "port": s.port, "encoding": s.encoding,
                             "head_length": s.head_length, "ws_path": s.ws_path,
                             "response_mode": s.response_mode,
                             "response_message": s.response_message} for s in servers],
            })
        filepath, _ = QFileDialog.getSaveFileName(self, "导出集合", f"{coll.name}.json",
                                                   "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        ok, err = export_collection_to_json(filepath, {
            "name": coll.name, "protocol_type": coll.protocol_type,
            "description": coll.description, "targets": targets_data,
        })
        if ok:
            QMessageBox.information(self, "导出完成", f"已导出到:\n{filepath}")
        else:
            QMessageBox.critical(self, "导出失败", err)


# ── 目标详情面板 ────────────────────────────────────────────


class _TargetDetailPanel(QWidget):
    """单个目标详情：客户端参数 / 发送响应(多预设) / 测试历史 / Mock服务端。"""

    target_updated = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._target = None
        self._coll = None
        self._client_worker = None
        self._selected_preset_idx: int | None = None
        self._last_response = ""
        self._last_raw = b""
        self._tcp_workers: dict[int, TcpServerWorker] = {}
        self._ws_workers: dict[int, WsServerWorker] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_client_tab(), "客户端")
        self._tabs.addTab(self._build_servers_tab(), "Mock服务端")
        self._tabs.addTab(self._build_history_tab(), "测试历史")
        layout.addWidget(self._tabs)

    # ── 客户端（参数 + 发送/响应）────────────────────────────

    def _build_params_widgets(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        # 协议选择（共用）
        proto_row = QHBoxLayout()
        self._proto_combo = QComboBox()
        self._proto_combo.addItem("TCP", "tcp_client")
        self._proto_combo.addItem("WebSocket", "ws_client")
        self._proto_combo.currentIndexChanged.connect(self._on_params_proto)
        proto_row.addWidget(QLabel("协议:")); proto_row.addWidget(self._proto_combo)
        proto_row.addStretch()
        layout.addLayout(proto_row)

        self._tcp_params_w = QWidget()
        tf = QHBoxLayout(self._tcp_params_w)
        tf.setContentsMargins(0, 0, 0, 0)
        tf.setSpacing(2)
        tf.addWidget(QLabel("IP:")); self._param_ip = QLineEdit("127.0.0.1"); self._param_ip.setPlaceholderText("IP"); self._param_ip.setMaximumWidth(130)
        tf.addWidget(self._param_ip)
        tf.addWidget(QLabel("端口:")); self._param_port = QSpinBox(); self._param_port.setRange(1, 65535); self._param_port.setValue(35126)
        tf.addWidget(self._param_port)
        tf.addWidget(QLabel("描述:")); self._param_desc = QLineEdit(""); self._param_desc.setPlaceholderText("描述"); self._param_desc.setMaximumWidth(100)
        tf.addWidget(self._param_desc)
        tf.addWidget(QLabel("编码:")); self._param_enc = QComboBox(); self._param_enc.addItems(ENCODINGS); self._param_enc.setEditable(True); self._param_enc.setMaximumWidth(90)
        tf.addWidget(self._param_enc)
        tf.addWidget(QLabel("HeadLen:")); self._param_hl = QSpinBox(); self._param_hl.setRange(0, 20); self._param_hl.setSuffix("位"); self._param_hl.setToolTip("0=原始"); self._param_hl.setValue(5); self._param_hl.setMaximumWidth(70)
        tf.addWidget(self._param_hl)
        tf.addWidget(QLabel("超时:")); self._param_timeout = QDoubleSpinBox(); self._param_timeout.setRange(0.1, 60); self._param_timeout.setValue(5.0); self._param_timeout.setSingleStep(0.5); self._param_timeout.setDecimals(1); self._param_timeout.setSuffix("s")
        tf.addWidget(self._param_timeout)
        tf.addWidget(QPushButton("保存", clicked=self._save_params))
        tf.addWidget(QPushButton("导出", clicked=self._export_target))
        tf.addWidget(QPushButton("导入", clicked=self._import_target_config))

        self._ws_params_w = QWidget()
        wf = QHBoxLayout(self._ws_params_w)
        wf.setContentsMargins(0, 0, 0, 0)
        wf.setSpacing(2)
        wf.addWidget(QLabel("URL:")); self._param_ws_url = QLineEdit("ws://127.0.0.1:35126/ws")
        wf.addWidget(self._param_ws_url)
        wf.addWidget(QLabel("超时:")); self._param_ws_timeout = QDoubleSpinBox(); self._param_ws_timeout.setRange(0.1, 60); self._param_ws_timeout.setValue(5.0); self._param_ws_timeout.setSingleStep(0.5); self._param_ws_timeout.setSuffix("s")
        wf.addWidget(self._param_ws_timeout)
        self._param_ws_ssl = QCheckBox("SSL")
        wf.addWidget(self._param_ws_ssl)
        wf.addStretch()
        wf.addWidget(QPushButton("保存", clicked=self._save_params))
        wf.addWidget(QPushButton("导出", clicked=self._export_target))
        wf.addWidget(QPushButton("导入", clicked=self._import_target_config))

        self._param_stack = QStackedWidget()
        self._param_stack.setMaximumHeight(32)
        self._param_stack.addWidget(self._tcp_params_w)
        self._param_stack.addWidget(self._ws_params_w)
        layout.addWidget(self._param_stack)
        return tab

    def _on_params_proto(self, idx: int):
        self._param_stack.setCurrentIndex(0 if self._proto_combo.currentData() == "tcp_client" else 1)

    def _save_params(self):
        if not self._target:
            return
        proto = self._proto_combo.currentData()
        self._db.update_protocol_target(
            self._target.id,
            ip=self._param_ip.text().strip(),
            port=self._param_port.value(),
            description=self._param_desc.text().strip(),
            encoding=self._param_enc.currentText() if proto == "tcp_client" else "UTF-8",
            head_length=self._param_hl.value() if proto == "tcp_client" else 0,
            timeout=self._param_timeout.value() if proto == "tcp_client" else self._param_ws_timeout.value(),
            ws_path=self._param_ws_url.text().strip() if proto == "ws_client" else "",
            ws_use_ssl=self._param_ws_ssl.isChecked(),
            send_message=self._target.send_message,
            send_presets=self._target.send_presets,
        )
        self._target = self._db.get_protocol_target(self._target.id)
        self.target_updated.emit()

    def _export_target(self):
        if not self._target:
            return
        t = self._target
        servers = self._db.get_protocol_servers_by_target(t.id)
        try:
            presets = json.loads(t.send_presets) if t.send_presets else []
        except json.JSONDecodeError:
            presets = []
        data = {
            "version": 1, "type": "protocol_client_config",
            "protocol_type": "tcp_client" if _target_proto_label(t) != "WS" else "ws_client",
            "ip": t.ip, "port": t.port, "encoding": t.encoding,
            "head_length": t.head_length, "timeout": t.timeout,
            "ws_url": t.ws_path, "ws_use_ssl": t.ws_use_ssl,
            "send_message": t.send_message, "send_presets": presets,
            "servers": [{"name": s.name, "server_type": s.server_type,
                         "ip": s.ip, "port": s.port, "encoding": s.encoding,
                         "head_length": s.head_length, "ws_path": s.ws_path,
                         "response_mode": s.response_mode,
                         "response_message": s.response_message} for s in servers],
        }
        filepath, _ = QFileDialog.getSaveFileName(self, "导出目标", f"{t.ip}_{t.port}.json",
                                                   "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        ok, err = export_client_config(filepath, data)
        if ok:
            QMessageBox.information(self, "导出完成", f"已导出到:\n{filepath}")
        else:
            QMessageBox.critical(self, "导出失败", err)

    def _import_target_config(self):
        if not self._target:
            return
        filepath, _ = QFileDialog.getOpenFileName(self, "导入目标配置", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        result, err = import_collection_from_json(filepath)
        if err:
            QMessageBox.critical(self, "导入失败", err)
            return
        if not isinstance(result, dict):
            return
        cfg = result
        proto = cfg.get("protocol_type", "tcp_client")
        self._db.update_protocol_target(
            self._target.id,
            ip=cfg.get("ip", self._target.ip),
            port=cfg.get("port", self._target.port),
            description=self._target.description,
            encoding=cfg.get("encoding", "UTF-8"),
            head_length=cfg.get("head_length", 5),
            timeout=cfg.get("timeout", 5.0),
            ws_path=cfg.get("ws_url", ""),
            ws_use_ssl=cfg.get("ws_use_ssl", False),
            send_message=cfg.get("send_message", ""),
            send_presets=json.dumps(cfg.get("send_presets", []), ensure_ascii=False),
        )
        self._target = self._db.get_protocol_target(self._target.id)
        self.target_updated.emit()
        QMessageBox.information(self, "导入完成", "目标配置已更新。")

    # ── 客户端（参数 + 发送/响应）────────────────────────────

    def _build_client_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 参数区域
        layout.addWidget(self._build_params_widgets())

        # 左右分栏：预设列表 | 发送+响应
        h_splitter = QSplitter(Qt.Horizontal)

        # ── 左侧：预设报文名称列表 ──
        preset_g = QGroupBox("预设报文")
        pl = QVBoxLayout(preset_g)
        self._preset_list = QListWidget()
        self._preset_list.setAlternatingRowColors(True)
        self._preset_list.setStyleSheet(
            "QListWidget::item:selected { background-color: #3498db; color: white; }"
            "QListWidget::item:selected:!active { background-color: #5dade2; color: white; }"
        )
        self._preset_list.itemClicked.connect(self._on_preset_clicked)
        pl.addWidget(self._preset_list)
        self._preset_selected_label = QLabel("")
        self._preset_selected_label.setStyleSheet("color: #27ae60; font-size: 11px;")
        self._preset_selected_label.setWordWrap(True)
        pl.addWidget(self._preset_selected_label)
        pbl = QGridLayout()
        buttons = [("添加", self._add_preset), ("保存", self._save_preset),
                   ("修改", self._edit_preset), ("删除", self._delete_preset),
                   ("清空", self._clear_preset_selection)]
        for i, (btn_text, slot) in enumerate(buttons):
            btn = QPushButton(btn_text, clicked=slot)
            if btn_text == "保存":
                btn.setShortcut("Ctrl+S")
                btn.setToolTip("Ctrl+S")
            pbl.addWidget(btn, i // 3, i % 3)
        pl.addLayout(pbl)
        h_splitter.addWidget(preset_g)

        # ── 右侧：发送消息 + 响应（纵向分栏）──
        right_splitter = QSplitter(Qt.Vertical)

        send_g = QGroupBox("发送消息")
        sl = QVBoxLayout(send_g)
        self._send_edit = QPlainTextEdit()
        self._send_edit.setPlaceholderText("输入要发送的报文...")
        self._send_edit.textChanged.connect(self._update_len_label)
        sl.addWidget(self._send_edit)
        self._len_label = QLabel("报文长度: 0 字节")
        sl.addWidget(self._len_label)
        sh = QHBoxLayout()
        self._send_btn = QPushButton("发送")
        self._send_btn.setMinimumWidth(80)
        self._send_btn.clicked.connect(self._send_message)
        sh.addWidget(self._send_btn)
        self._terminate_btn = QPushButton("终止")
        self._terminate_btn.setVisible(False)
        self._terminate_btn.clicked.connect(self._cancel_client)
        sh.addWidget(self._terminate_btn)
        sh.addWidget(QPushButton("清空", clicked=self._send_edit.clear))
        sh.addStretch()
        sl.addLayout(sh)
        right_splitter.addWidget(send_g)

        resp_g = QGroupBox("响应")
        rl = QVBoxLayout(resp_g)
        resp_tool = QHBoxLayout()
        resp_tool.addWidget(QLabel("编码:"))
        self._resp_enc_combo = QComboBox()
        self._resp_enc_combo.setEditable(True)
        self._resp_enc_combo.addItems(ENCODINGS)
        self._resp_enc_combo.currentTextChanged.connect(self._refresh_response_display)
        resp_tool.addWidget(self._resp_enc_combo)
        self._resp_hex_toggle = QPushButton("十六进制")
        self._resp_hex_toggle.setCheckable(True)
        self._resp_hex_toggle.toggled.connect(self._refresh_response_display)
        resp_tool.addWidget(self._resp_hex_toggle)
        resp_tool.addStretch()
        rl.addLayout(resp_tool)
        self._resp_edit = QPlainTextEdit()
        self._resp_edit.setReadOnly(True)
        self._resp_edit.setPlaceholderText("响应将显示在这里...")
        self._resp_edit.setFont(QFont("Consolas", 10))
        rl.addWidget(self._resp_edit)
        rl.addWidget(QPushButton("清空响应", clicked=self._resp_edit.clear))
        right_splitter.addWidget(resp_g)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        h_splitter.addWidget(right_splitter)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setSizes([120, 680])
        layout.addWidget(h_splitter)
        return tab

    def _load_presets(self, presets_json: str):
        try:
            return json.loads(presets_json) if presets_json else []
        except json.JSONDecodeError:
            return []

    def _save_presets_to_target(self, presets: list):
        if self._target:
            self._db.update_protocol_target(
                self._target.id, ip=self._target.ip, port=self._target.port,
                description=self._target.description, encoding=self._target.encoding,
                head_length=self._target.head_length, timeout=self._target.timeout,
                ws_path=self._target.ws_path, ws_use_ssl=self._target.ws_use_ssl,
                send_message=self._target.send_message,
                send_presets=json.dumps(presets, ensure_ascii=False),
            )

    def _refresh_preset_table(self):
        if not self._target:
            return
        presets = self._load_presets(self._target.send_presets)
        lst = self._preset_list
        lst.clear()
        for i, p in enumerate(presets):
            item = QListWidgetItem(p.get("name", ""))
            item.setData(Qt.UserRole, i)
            lst.addItem(item)
        # 恢复选中状态
        if self._selected_preset_idx is not None and self._selected_preset_idx < len(presets):
            lst.setCurrentRow(self._selected_preset_idx)
            self._preset_selected_label.setText(f"✓ 已选择: {presets[self._selected_preset_idx].get('name', '')}")
        else:
            self._selected_preset_idx = None
            self._preset_selected_label.setText("")

    def _on_preset_clicked(self, item: QListWidgetItem):
        if not self._target:
            return
        idx = item.data(Qt.UserRole)
        presets = self._load_presets(self._target.send_presets)
        if idx is None or idx >= len(presets):
            return
        msg = presets[idx].get("message", "")
        current = self._send_edit.toPlainText()
        if current and current != msg:
            # 仅当内容不是从任何预设加载的（手动编辑过）才警告
            if not any(p.get("message", "") == current for p in presets):
                reply = QMessageBox.question(
                    self, "内容未保存",
                    "当前报文未保存为模板，是否放弃并重新选择报文？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    # 恢复列表选中
                    self._preset_list.setCurrentItem(item)
                    return
        self._send_edit.setPlainText(msg)
        self._selected_preset_idx = idx
        self._preset_selected_label.setText(f"✓ 已选择: {presets[idx].get('name', '')}")

    def _add_preset(self):
        if not self._target:
            return
        presets = self._load_presets(self._target.send_presets)
        default_name = f"报文{len(presets) + 1}"
        name, ok = QInputDialog.getText(self, "添加预设", "预设名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p.get("name", "") == name for p in presets):
            QMessageBox.warning(self, "名称重复", f"预设「{name}」已存在，请使用其他名称。")
            return
        msg = self._send_edit.toPlainText()
        presets.append({"name": name, "message": msg})
        self._save_presets_to_target(presets)
        self._target = self._db.get_protocol_target(self._target.id)
        self._refresh_preset_table()

    def _save_preset(self):
        """将当前输入框内容保存到选中的预设，或弹窗选择覆盖/新建。"""
        if not self._target:
            return
        new_msg = self._send_edit.toPlainText()
        presets = self._load_presets(self._target.send_presets)

        if self._selected_preset_idx is not None and self._selected_preset_idx < len(presets):
            # 有选中预设：保存到选中项
            old_msg = presets[self._selected_preset_idx].get("message", "")
            if old_msg and old_msg != new_msg:
                reply = QMessageBox.question(
                    self, "确认覆盖",
                    f"预设「{presets[self._selected_preset_idx]['name']}」已有内容，是否覆盖？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            presets[self._selected_preset_idx]["message"] = new_msg
        elif presets:
            # 未选中预设：弹窗选择覆盖或新建
            dlg = QDialog(self)
            dlg.setWindowTitle("保存报文")
            dlg.setMinimumWidth(300)
            dl = QVBoxLayout(dlg)
            dl.addWidget(QLabel("选择要覆盖的预设，或新建预设："))
            lst = QListWidget()
            lst.addItem("── 新建预设 ──")
            for p in presets:
                lst.addItem(p.get("name", ""))
            lst.setCurrentRow(0)
            dl.addWidget(lst)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            dl.addWidget(bb)
            if dlg.exec() != QDialog.Accepted:
                return
            sel = lst.currentRow()
            if sel < 0:
                return
            if sel == 0:
                # 新建预设
                default_name = f"报文{len(presets) + 1}"
                name, ok = QInputDialog.getText(self, "新建预设", "预设名称:", text=default_name)
                if not ok or not name.strip():
                    return
                name = name.strip()
                if any(p.get("name", "") == name for p in presets):
                    QMessageBox.warning(self, "名称重复", f"预设「{name}」已存在。")
                    return
                presets.append({"name": name, "message": new_msg})
                self._selected_preset_idx = len(presets) - 1
            else:
                # 覆盖选中
                idx = sel - 1
                old_msg = presets[idx].get("message", "")
                if old_msg and old_msg != new_msg:
                    reply = QMessageBox.question(
                        self, "确认覆盖",
                        f"预设「{presets[idx]['name']}」已有内容，是否覆盖？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return
                presets[idx]["message"] = new_msg
                self._selected_preset_idx = idx
        else:
            # 无预设：直接新建
            default_name = "报文1"
            name, ok = QInputDialog.getText(self, "新建预设", "预设名称:", text=default_name)
            if not ok or not name.strip():
                return
            presets.append({"name": name.strip(), "message": new_msg})
            self._selected_preset_idx = len(presets) - 1

        self._save_presets_to_target(presets)
        self._target = self._db.get_protocol_target(self._target.id)
        self._refresh_preset_table()

    def _edit_preset(self):
        if not self._target:
            return
        item = self._preset_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.UserRole)
        presets = self._load_presets(self._target.send_presets)
        if idx is None or idx >= len(presets):
            return
        p = presets[idx]
        name, ok = QInputDialog.getText(self, "编辑预设", "预设名称:", text=p["name"])
        if not ok or not name.strip():
            return
        presets[idx]["name"] = name.strip()
        presets[idx]["message"] = self._send_edit.toPlainText()
        self._save_presets_to_target(presets)
        self._target = self._db.get_protocol_target(self._target.id)
        self._refresh_preset_table()

    def _delete_preset(self):
        if not self._target:
            return
        item = self._preset_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.UserRole)
        presets = self._load_presets(self._target.send_presets)
        if idx is None or idx >= len(presets):
            return
        name = presets[idx].get("name", "")
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除预设「{name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        presets.pop(idx)
        self._save_presets_to_target(presets)
        self._target = self._db.get_protocol_target(self._target.id)
        self._refresh_preset_table()

    def _clear_preset_selection(self):
        """清空预设选中状态。"""
        self._selected_preset_idx = None
        self._preset_list.clearSelection()
        self._preset_selected_label.setText("")

    def _update_len_label(self):
        if not self._target:
            return
        msg = self._send_edit.toPlainText()
        enc = self._target.encoding
        hl = self._target.head_length
        try:
            nb = len(msg.encode(enc))
            hdr = compute_length_header(msg, enc, hl)
            self._len_label.setText(f"报文长度: {nb} 字节, 长度头: {hdr}")
        except (UnicodeEncodeError, UnicodeDecodeError):
            self._len_label.setText("编码错误")

    def _send_message(self):
        if not self._target:
            return
        if self._client_worker and self._client_worker.isRunning():
            QMessageBox.information(self, "提示", "有请求正在进行中。")
            return
        msg = self._send_edit.toPlainText()
        if not msg:
            QMessageBox.information(self, "提示", "请输入要发送的消息。")
            return
        self._send_btn.setEnabled(False)
        self._send_btn.setText("发送中...")
        self._terminate_btn.setVisible(True)
        proto = self._proto_combo.currentData()
        t = self._target
        if proto == "tcp_client":
            self._client_worker = TcpClientWorker(ip=t.ip, port=t.port, message=msg,
                                                   encoding=t.encoding, head_len=t.head_length,
                                                   timeout=t.timeout)
        else:
            url = t.ws_path if t.ws_path else f"ws://{t.ip}:{t.port}/ws"
            self._client_worker = WsClientWorker(url=url, message=msg, timeout=t.timeout)
        self._client_worker.finished.connect(self._on_client_done)
        self._client_worker.start()

    def _cancel_client(self):
        """终止当前客户端请求。"""
        if self._client_worker and self._client_worker.isRunning():
            self._client_worker.terminate()
            self._client_worker.wait(3000)
        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")
        self._terminate_btn.setVisible(False)
        self._resp_edit.appendPlainText("[终止] 请求已被用户终止")

    def _on_client_done(self, success: bool, response: str):
        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")
        self._terminate_btn.setVisible(False)
        # 存储原始响应用于编码切换
        self._last_response = response
        enc = self._target.encoding if self._target else "UTF-8"
        try:
            self._last_raw = response.encode(enc, errors='replace')
        except Exception:
            self._last_raw = response.encode('utf-8', errors='replace')
        # 自动判断编码
        if success and self._last_raw:
            detected = self._detect_encoding(self._last_raw)
            if detected and detected != self._resp_enc_combo.currentText():
                self._resp_enc_combo.setCurrentText(detected)
        ts = datetime.now().strftime("%H:%M:%S")
        tag = "OK" if success else "FAIL"
        self._append_response(f"[{ts}] {tag}:\n{response}")
        if self._target and self._coll:
            self._db.add_protocol_test_session(
                collection_id=self._coll.id, collection_name=self._coll.name,
                target_id=self._target.id, protocol_type=self._coll.protocol_type,
                target_ip=self._target.ip, target_port=self._target.port,
                success=success, response=response, error_msg="" if success else response,
            )

    def _detect_encoding(self, raw: bytes) -> str | None:
        """尝试自动判断字节数据的编码。"""
        candidates = ["UTF-8", "GBK", "GB2312", "GB18030", "ISO-8859-1", "ASCII"]
        for enc in candidates:
            try:
                raw.decode(enc)
                return enc
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return None

    def _refresh_response_display(self):
        """根据编码选择和十六进制开关刷新响应显示。"""
        if not self._last_raw:
            return
        if self._resp_hex_toggle.isChecked():
            hex_str = " ".join(f"{b:02x}" for b in self._last_raw)
            # 每行 16 字节
            lines = []
            for i in range(0, len(self._last_raw), 16):
                chunk = self._last_raw[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{i:04x}  {hex_part:<48}  {ascii_part}")
            self._resp_edit.setPlainText("\n".join(lines))
        else:
            enc = self._resp_enc_combo.currentText()
            try:
                text = self._last_raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                text = self._last_raw.decode(enc, errors="replace")
            self._resp_edit.setPlainText(text)

    def _append_response(self, text: str):
        """追加响应文本，根据当前显示模式格式化。"""
        if self._resp_hex_toggle.isChecked():
            self._refresh_response_display()
        else:
            self._resp_edit.appendPlainText(text)

    # ── 测试历史 ─────────────────────────────────────────

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        fl = QHBoxLayout()
        fl.addWidget(QLabel("结果:"))
        self._hist_filter = QComboBox()
        self._hist_filter.addItem("全部", None)
        self._hist_filter.addItem("OK", True)
        self._hist_filter.addItem("FAIL", False)
        self._hist_filter.currentIndexChanged.connect(self._refresh_history)
        fl.addWidget(self._hist_filter)
        self._hist_search = QLineEdit()
        self._hist_search.setPlaceholderText("搜索报文内容...")
        self._hist_search.setClearButtonEnabled(True)
        self._hist_search.textChanged.connect(self._refresh_history)
        fl.addWidget(self._hist_search)
        fl.addStretch()
        fl.addWidget(QPushButton("导出历史", clicked=self._export_history))
        layout.addLayout(fl)

        self._hist_table = QTableWidget()
        self._hist_table.setColumnCount(4)
        self._hist_table.setHorizontalHeaderLabels(["时间", "结果", "目标", "端口"])
        self._hist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._hist_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._hist_table.setAlternatingRowColors(True)
        self._hist_table.verticalHeader().setVisible(False)
        hh = self._hist_table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._on_hist_header_clicked)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        self._hist_table.setColumnWidth(0, 150)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        self._hist_table.setColumnWidth(1, 60)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        self._hist_table.setColumnWidth(3, 60)
        self._hist_table.cellClicked.connect(self._on_hist_cell_clicked)
        hist_splitter = QSplitter(Qt.Vertical)
        hist_splitter.addWidget(self._hist_table)

        self._hist_detail = QPlainTextEdit()
        self._hist_detail.setReadOnly(True)
        self._hist_detail.setPlaceholderText("点击行查看请求和响应详情...")
        hist_splitter.addWidget(self._hist_detail)
        hist_splitter.setStretchFactor(0, 3)
        hist_splitter.setStretchFactor(1, 1)
        layout.addWidget(hist_splitter)

        self._hist_sessions = []
        self._hist_sort_col = 0
        self._hist_sort_asc = False
        return tab

    def _refresh_history(self):
        if not self._target:
            return
        sessions = self._db.get_protocol_test_sessions_by_target(self._target.id)
        # 结果筛选
        status_val = self._hist_filter.currentData()
        if status_val is not None:
            sessions = [s for s in sessions if s.success == status_val]
        # 文本搜索（搜索响应/错误报文内容）
        search_text = self._hist_search.text().strip().lower()
        if search_text:
            sessions = [s for s in sessions
                        if search_text in (s.response or "").lower()
                        or search_text in (s.error_msg or "").lower()
                        or search_text in s.target_ip.lower()
                        or search_text in str(s.target_port)]
        if self._hist_sort_col >= 0:
            key_fn = {0: lambda s: s.started_at, 1: lambda s: s.success,
                      2: lambda s: s.target_ip, 3: lambda s: s.target_port}.get(
                self._hist_sort_col, lambda s: s.started_at)
            sessions.sort(key=key_fn, reverse=not self._hist_sort_asc)
        self._update_hist_sort_indicator()
        self._hist_sessions = sessions
        t = self._hist_table
        t.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            t.setItem(row, 0, QTableWidgetItem(s.started_at))
            ok_item = QTableWidgetItem("OK" if s.success else "FAIL")
            ok_item.setForeground(Qt.green if s.success else Qt.red)
            t.setItem(row, 1, ok_item)
            t.setItem(row, 2, QTableWidgetItem(s.target_ip))
            t.setItem(row, 3, QTableWidgetItem(str(s.target_port)))

    def _on_hist_header_clicked(self, col: int):
        if self._hist_sort_col == col:
            self._hist_sort_asc = not self._hist_sort_asc
        else:
            self._hist_sort_col = col
            self._hist_sort_asc = True
        self._refresh_history()

    def _update_hist_sort_indicator(self):
        headers = {0: "时间", 1: "结果", 2: "目标", 3: "端口"}
        for c, label in headers.items():
            item = self._hist_table.horizontalHeaderItem(c)
            if item:
                arrow = " ▲" if (c == self._hist_sort_col and self._hist_sort_asc) else \
                        " ▼" if c == self._hist_sort_col else ""
                item.setText(label + arrow)

    def _on_hist_cell_clicked(self, row: int, col: int):
        if row < len(self._hist_sessions):
            s = self._hist_sessions[row]
            detail = f"请求:\n---\n响应 ({'OK' if s.success else 'FAIL'}):\n{s.response}"
            if s.error_msg:
                detail += f"\n\n错误:\n{s.error_msg}"
            self._hist_detail.setPlainText(detail)

    def _export_history(self):
        if not self._hist_sessions:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        fp, _ = QFileDialog.getSaveFileName(self, "导出测试历史", "target_history.csv",
                                             "CSV (*.csv);;Excel (*.xlsx)")
        if not fp:
            return
        import csv
        try:
            with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["测试时间", "协议", "目标IP", "端口", "结果", "请求报文", "响应报文", "错误信息"])
                for s in self._hist_sessions:
                    writer.writerow([
                        s.started_at,
                        "TCP" if "tcp" in s.protocol_type else "WS",
                        s.target_ip,
                        s.target_port,
                        "OK" if s.success else "FAIL",
                        "",  # 请求报文未单独存储
                        s.response or "",
                        s.error_msg or "",
                    ])
            QMessageBox.information(self, "导出完成", f"已导出 {len(self._hist_sessions)} 条记录。")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ── Mock服务端 ─────────────────────────────────────────

    def _build_servers_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        self._srv_table = QTableWidget()
        self._srv_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._srv_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._srv_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._srv_table.setAlternatingRowColors(True)
        self._srv_table.verticalHeader().setVisible(False)
        self._srv_table.horizontalHeader().setSectionsClickable(True)
        self._srv_table.horizontalHeader().sectionClicked.connect(self._on_srv_header_clicked)

        srv_splitter = QSplitter(Qt.Vertical)
        srv_splitter.addWidget(self._srv_table)
        self._srv_log_tabs = QTabWidget()
        self._srv_log_tabs.setTabsClosable(True)
        self._srv_log_tabs.tabCloseRequested.connect(self._on_srv_log_tab_close)
        srv_splitter.addWidget(self._srv_log_tabs)
        srv_splitter.setStretchFactor(0, 3)
        srv_splitter.setStretchFactor(1, 1)
        layout.addWidget(srv_splitter)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("+ 添加", clicked=self._add_target_server))
        bl.addWidget(QPushButton("编辑", clicked=self._edit_target_server))
        bl.addWidget(QPushButton("删除选中", clicked=self._delete_selected_target_servers))
        bl.addWidget(QPushButton("启动选中", clicked=self._start_selected_target_servers))
        bl.addWidget(QPushButton("停止选中", clicked=self._stop_selected_target_servers))
        bl.addStretch()
        bl.addWidget(QPushButton("▶ 全部启动", clicked=self._start_all_servers))
        bl.addWidget(QPushButton("■ 全部停止", clicked=self._stop_all_target_servers))
        layout.addLayout(bl)
        self._srv_log_tab_to_sid: dict[int, int] = {}
        self._srv_sort_col = -1
        self._srv_sort_asc = True
        return tab

    @property
    def _current_server_proto(self) -> str:
        return "tcp_server"

    def _refresh_servers(self):
        if not self._target:
            return
        servers = self._db.get_protocol_servers_by_target(self._target.id)
        st = self._current_server_proto
        is_tcp = st == "tcp_server"
        # 排序
        key_map = {0: lambda s: s.name, 1: lambda s: s.ip, 2: lambda s: s.port}
        if self._srv_sort_col >= 0:
            key_fn = key_map.get(self._srv_sort_col, lambda s: s.name)
            servers.sort(key=key_fn, reverse=not self._srv_sort_asc)
        cols = ["名称", "监听地址", "端口", "编码", "HeadLen", "响应模式", "操作"] if is_tcp else \
               ["名称", "监听地址", "端口", "路径", "响应模式", "操作"]
        t = self._srv_table
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(len(cols) - 1, QHeaderView.Fixed)
        t.setColumnWidth(len(cols) - 1, 80)
        self._update_srv_sort_indicator()
        t.setRowCount(len(servers))
        workers = self._tcp_workers if is_tcp else self._ws_workers
        for row, srv in enumerate(servers):
            ni = QTableWidgetItem(srv.name); ni.setData(Qt.UserRole, srv.id)
            t.setItem(row, 0, ni)
            t.setItem(row, 1, QTableWidgetItem(srv.ip))
            pi = QTableWidgetItem(str(srv.port)); pi.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 2, pi)
            if is_tcp:
                t.setItem(row, 3, QTableWidgetItem(srv.encoding))
                hi = QTableWidgetItem(str(srv.head_length)); hi.setTextAlignment(Qt.AlignCenter)
                t.setItem(row, 4, hi)
                t.setItem(row, 5, QTableWidgetItem("回显" if srv.response_mode == "echo" else "固定"))
            else:
                t.setItem(row, 3, QTableWidgetItem(srv.ws_path))
                t.setItem(row, 4, QTableWidgetItem("回显" if srv.response_mode == "echo" else "固定"))
            btn = QPushButton("Stop" if srv.id in workers else "Start")
            btn.setStyleSheet("color: #e74c3c;" if srv.id in workers else "color: #27ae60;")
            btn.clicked.connect(partial(self._toggle_target_server, srv))
            t.setCellWidget(row, len(cols) - 1, btn)

    def _on_srv_header_clicked(self, col: int):
        if col >= 5:
            return
        if self._srv_sort_col == col:
            self._srv_sort_asc = not self._srv_sort_asc
        else:
            self._srv_sort_col = col; self._srv_sort_asc = True
        self._refresh_servers()

    def _update_srv_sort_indicator(self):
        headers = {0: "名称", 1: "监听地址", 2: "端口"}
        for c, label in headers.items():
            arrow = ""
            if c == self._srv_sort_col:
                arrow = " ▲" if self._srv_sort_asc else " ▼"
            hdr = self._srv_table.horizontalHeaderItem(c)
            if hdr:
                hdr.setText(label + arrow)

    def _add_target_server(self):
        if not self._target:
            return
        st = self._current_server_proto
        dlg = ServerDialog("添加 Mock 服务端", st, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.add_protocol_server(
                name=d["name"], server_type=st, ip=d["ip"], port=d["port"],
                encoding=d.get("encoding", "UTF-8"), head_length=d.get("head_length", 0),
                ws_path=d.get("ws_path", "/"), response_mode=d["response_mode"],
                response_message=d["response_message"], target_id=self._target.id,
            )
            self._refresh_servers()

    def _edit_target_server(self):
        rows = set(i.row() for i in self._srv_table.selectedIndexes())
        if not rows:
            return QMessageBox.information(self, "提示", "请选择一条记录。")
        row = rows.pop()
        item = self._srv_table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        workers = self._tcp_workers if self._current_server_proto == "tcp_server" else self._ws_workers
        if sid in workers:
            return QMessageBox.warning(self, "提示", "请先停止该监听器再编辑。")
        data = dict(name=srv.name, ip=srv.ip, port=srv.port, encoding=srv.encoding,
                    head_length=srv.head_length, ws_path=srv.ws_path,
                    response_mode=srv.response_mode, response_message=srv.response_message)
        dlg = ServerDialog("编辑 Mock 服务端", srv.server_type, data, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.update_protocol_server(
                server_id=sid, name=d["name"], server_type=srv.server_type,
                ip=d["ip"], port=d["port"], encoding=d.get("encoding", "UTF-8"),
                head_length=d.get("head_length", 0), ws_path=d.get("ws_path", "/"),
                response_mode=d["response_mode"], response_message=d["response_message"],
                target_id=self._target.id,
            )
            self._refresh_servers()

    def _delete_target_server(self):
        rows = set(i.row() for i in self._srv_table.selectedIndexes())
        if not rows:
            return QMessageBox.information(self, "提示", "请选择一条记录。")
        row = rows.pop()
        item = self._srv_table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        workers = self._tcp_workers if self._current_server_proto == "tcp_server" else self._ws_workers
        if sid in workers:
            return QMessageBox.warning(self, "提示", "请先停止该监听器再删除。")
        r = QMessageBox.question(self, "确认删除", f"确定要删除 Mock 服务端 [{srv.name}] 吗？",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            self._db.delete_protocol_server(sid)
            self._refresh_servers()

    def _toggle_target_server(self, srv, _checked=None):
        from src.database import ProtocolServer
        s: ProtocolServer = srv
        st = s.server_type
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        if s.id in workers:
            w = workers.pop(s.id); w.stop_server()
            self._srv_log_to(s.id, f"Stop [{s.name}] {s.ip}:{s.port}")
            for tab_idx, tsid in list(self._srv_log_tab_to_sid.items()):
                if tsid == s.id:
                    self._srv_log_tabs.removeTab(tab_idx)
                    del self._srv_log_tab_to_sid[tab_idx]
                    break
        else:
            log_w = QPlainTextEdit()
            log_w.setReadOnly(True)
            log_w.setMaximumBlockCount(2000)
            tab_idx = self._srv_log_tabs.addTab(log_w, f"{s.name}:{s.port}")
            self._srv_log_tabs.setCurrentIndex(tab_idx)
            self._srv_log_tab_to_sid[tab_idx] = s.id
            self._srv_log_to(s.id, f"Start [{s.name}] {s.ip}:{s.port}")
            if st == "tcp_server":
                w = TcpServerWorker(server_id=s.id, ip=s.ip, port=s.port,
                                    encoding=s.encoding, head_len=s.head_length,
                                    response_mode=s.response_mode, response_message=s.response_message)
                w.message_received.connect(lambda addr, msg, sid=s.id, nm=s.name: self._srv_log_to(sid, f"[{nm}] {addr}:\n{msg}"))
            else:
                w = WsServerWorker(server_id=s.id, ip=s.ip, port=s.port,
                                   path=s.ws_path, response_mode=s.response_mode,
                                   response_message=s.response_message)
                w.message_received.connect(lambda msg, sid=s.id, nm=s.name: self._srv_log_to(sid, f"[{nm}]:\n{msg}"))
                w.client_event.connect(partial(self._srv_log_to, s.id))
            w.status_changed.connect(partial(self._srv_log_to, s.id))
            w.error_occurred.connect(lambda err, sid=s.id: self._srv_log_to(sid, f"[ERR] {err}"))
            w.finished.connect(partial(self._on_target_worker_finished, st, s.id))
            workers[s.id] = w; w.start()
        self._refresh_servers()

    def _srv_log_to(self, sid: int, text: str):
        for tab_idx, tsid in self._srv_log_tab_to_sid.items():
            if tsid == sid:
                log = self._srv_log_tabs.widget(tab_idx)
                if isinstance(log, QPlainTextEdit):
                    log.appendPlainText(text)
                return

    def _on_srv_log_tab_close(self, idx: int):
        sid = self._srv_log_tab_to_sid.pop(idx, None)
        if sid is not None:
            for workers in (self._tcp_workers, self._ws_workers):
                w = workers.pop(sid, None)
                if w:
                    w.stop_server()
                    break
        if idx >= 0:
            self._srv_log_tabs.removeTab(idx)
        # 重建索引映射
        new_map = {}
        for i in range(self._srv_log_tabs.count()):
            w = self._srv_log_tabs.widget(i)
            for old_idx, old_sid in self._srv_log_tab_to_sid.items():
                if old_sid not in new_map.values():
                    new_map[i] = old_sid
                    break
        self._srv_log_tab_to_sid = new_map
        self._refresh_servers()

    def _on_target_worker_finished(self, st: str, sid: int):
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        workers.pop(sid, None)
        self._refresh_servers()

    def _get_selected_srv_ids(self) -> list[int]:
        rows = set(i.row() for i in self._srv_table.selectedIndexes())
        ids = []
        for row in rows:
            item = self._srv_table.item(row, 0)
            if item:
                sid = item.data(Qt.UserRole)
                if sid is not None:
                    ids.append(sid)
        return ids

    def _delete_selected_target_servers(self):
        ids = self._get_selected_srv_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要删除的监听器。")
        all_workers = {**self._tcp_workers, **self._ws_workers}
        running = [sid for sid in ids if sid in all_workers]
        if running:
            return QMessageBox.warning(self, "提示", "请先停止选中的监听器再删除。")
        r = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(ids)} 个监听器吗？",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for sid in ids:
                self._db.delete_protocol_server(sid)
            self._refresh_servers()

    def _start_selected_target_servers(self):
        ids = self._get_selected_srv_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要启动的监听器。")
        for sid in ids:
            srv = self._db.get_protocol_server(sid)
            if srv:
                self._toggle_target_server(srv)

    def _stop_selected_target_servers(self):
        ids = self._get_selected_srv_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要停止的监听器。")
        for sid in ids:
            for workers in (self._tcp_workers, self._ws_workers):
                w = workers.pop(sid, None)
                if w:
                    w.stop_server()
                    for tab_idx, tsid in list(self._srv_log_tab_to_sid.items()):
                        if tsid == sid:
                            self._srv_log_tabs.removeTab(tab_idx)
                            del self._srv_log_tab_to_sid[tab_idx]
                            break
                    break
        self._refresh_servers()

    def _start_all_servers(self):
        if not self._target:
            return
        servers = self._db.get_protocol_servers_by_target(self._target.id)
        for srv in servers:
            self._toggle_target_server(srv)

    def _stop_all_target_servers(self):
        for w in list(self._tcp_workers.values()):
            w.stop_server()
        self._tcp_workers.clear()
        for w in list(self._ws_workers.values()):
            w.stop_server()
        self._ws_workers.clear()
        for tab_idx in list(self._srv_log_tab_to_sid.keys()):
            self._srv_log_tabs.removeTab(tab_idx)
        self._srv_log_tab_to_sid.clear()
        self._refresh_servers()

    # ── 设置目标 ─────────────────────────────────────────

    def set_target(self, target, coll):
        self._target = target
        self._coll = coll
        if target is None:
            self.setEnabled(False)
            return
        self.setEnabled(True)
        proto_idx = 0 if _target_proto_label(target) != "WS" else 1
        self._proto_combo.setCurrentIndex(proto_idx)
        self._param_stack.setCurrentIndex(proto_idx)
        self._param_ip.setText(target.ip)
        self._param_port.setValue(target.port)
        self._param_desc.setText(target.description or "")
        self._param_enc.setCurrentText(target.encoding)
        self._param_hl.setValue(target.head_length)
        self._param_timeout.setValue(target.timeout)
        if target.ws_path and target.ws_path.startswith("ws"):
            self._param_ws_url.setText(target.ws_path)
        self._param_ws_ssl.setChecked(target.ws_use_ssl)
        self._param_ws_timeout.setValue(target.timeout)
        self._send_edit.setPlainText(target.send_message)
        self._update_len_label()
        self._refresh_preset_table()
        self._refresh_history()
        self._refresh_servers()

    def has_active_servers(self) -> bool:
        return bool(self._tcp_workers) or bool(self._ws_workers)

    def stop_all_servers(self):
        self._stop_all_target_servers()


# ── 集合详情标签页（仅目标表格）───────────────────────────


class _CollectionDetailTab(QWidget):
    """显示选中集合的目标列表 —— 双击打开目标详情标签页。"""

    target_double_clicked = Signal(object, object)  # (target, collection)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._coll = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>目标列表</b>"))
        self._target_search = QLineEdit()
        self._target_search.setPlaceholderText("搜索 IP/端口/描述...")
        self._target_search.setClearButtonEnabled(True)
        self._target_search.textChanged.connect(self._refresh_targets)
        top_bar.addWidget(self._target_search)
        layout.addLayout(top_bar)

        self._target_table = QTableWidget()
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._target_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._target_table.setAlternatingRowColors(True)
        vh = self._target_table.verticalHeader()
        vh.setVisible(True)
        vh.setDefaultSectionSize(36)
        vh.setMinimumSectionSize(24)
        vh.setSectionResizeMode(QHeaderView.Interactive)
        self._target_table.cellDoubleClicked.connect(self._on_target_double_clicked)
        layout.addWidget(self._target_table)

        tbl = QHBoxLayout()
        tbl.addWidget(QPushButton("添加目标", clicked=self._on_add_target))
        tbl.addWidget(QPushButton("编辑", clicked=self._on_edit_target))
        tbl.addWidget(QPushButton("删除", clicked=self._on_delete_target))
        tbl.addStretch()
        layout.addLayout(tbl)

    def set_collection(self, coll):
        self._coll = coll
        self._refresh_targets()

    def _refresh_targets(self):
        if not self._coll:
            self._target_table.setColumnCount(3)
            self._target_table.setHorizontalHeaderLabels(["IP", "端口", "描述"])
            self._target_table.setRowCount(0)
            return
        targets = self._db.get_protocol_targets(self._coll.id)
        # 搜索过滤
        search = self._target_search.text().strip().lower()
        if search:
            targets = [t for t in targets if
                       search in t.ip.lower() or
                       search in str(t.port) or
                       search in (t.description or "").lower() or
                       search in (t.encoding or "").lower() or
                       search in _target_proto_label(t).lower()]
        t = self._target_table
        t.setColumnCount(8)
        t.setHorizontalHeaderLabels(["#", "IP", "端口", "描述", "编码", "HeadLen", "超时", "类型"])
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed); t.setColumnWidth(0, 30)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed); t.setColumnWidth(2, 60)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for c in range(4, 7):
            hh.setSectionResizeMode(c, QHeaderView.Fixed); t.setColumnWidth(c, 60)
        hh.setSectionResizeMode(7, QHeaderView.Fixed); t.setColumnWidth(7, 60)

        t.setRowCount(len(targets))
        for row, target in enumerate(targets):
            ni = QTableWidgetItem(str(row + 1)); ni.setTextAlignment(Qt.AlignCenter)
            ni.setData(Qt.UserRole, target.id)
            t.setItem(row, 0, ni)
            t.setItem(row, 1, QTableWidgetItem(target.ip))
            pi = QTableWidgetItem(str(target.port)); pi.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 2, pi)
            t.setItem(row, 3, QTableWidgetItem(target.description or ""))
            t.setItem(row, 4, QTableWidgetItem(target.encoding))
            t.setItem(row, 5, QTableWidgetItem(str(target.head_length)))
            ti = QTableWidgetItem(f"{target.timeout}s"); ti.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 6, ti)
            t.setItem(row, 7, QTableWidgetItem(_target_proto_label(target)))

    def _on_target_double_clicked(self, row: int, col: int):
        if not self._coll:
            return
        item = self._target_table.item(row, 0)
        if not item:
            return
        tid = item.data(Qt.UserRole)
        target = self._db.get_protocol_target(tid)
        if target:
            self.target_double_clicked.emit(target, self._coll)

    def _on_add_target(self):
        if not self._coll:
            QMessageBox.information(self, "提示", "请先在左侧选择一个集合。")
            return
        dlg = _TargetDialog("添加目标", parent=self)
        if dlg.exec() == QDialog.Accepted:
            ip, port, desc = dlg.get_data()
            self._db.add_protocol_target(self._coll.id, ip, port, desc)
            self._refresh_targets()

    def _on_edit_target(self):
        row = self._target_table.currentRow()
        if row < 0:
            return
        item = self._target_table.item(row, 0)
        if not item:
            return
        tid = item.data(Qt.UserRole)
        t = self._db.get_protocol_target(tid)
        if not t:
            return
        dlg = _TargetDialog("编辑目标", ip=t.ip, port=t.port, desc=t.description, parent=self)
        if dlg.exec() == QDialog.Accepted:
            nip, nport, ndesc = dlg.get_data()
            self._db.update_protocol_target(tid, nip, nport, ndesc,
                                            encoding=t.encoding, head_length=t.head_length,
                                            timeout=t.timeout, ws_path=t.ws_path,
                                            ws_use_ssl=t.ws_use_ssl,
                                            send_message=t.send_message,
                                            send_presets=t.send_presets)
            self._refresh_targets()

    def _on_delete_target(self):
        rows = set(i.row() for i in self._target_table.selectedIndexes())
        if not rows:
            return
        r = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(rows)} 个目标吗？",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for row in rows:
                item = self._target_table.item(row, 0)
                if item:
                    self._db.delete_protocol_target(item.data(Qt.UserRole))
            self._refresh_targets()


# ── 服务端标签页（全部服务端）────────────────────────────


class _ServerTab(QWidget):
    """显示全部服务端配置（全局 + 目标关联），支持筛选排序。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._tcp_workers: dict[int, TcpServerWorker] = {}
        self._ws_workers: dict[int, WsServerWorker] = {}
        self._server_logs: dict[int, QPlainTextEdit] = {}
        self._log_tab_to_sid: dict[int, int] = {}
        self._all_servers = []
        self._sort_col = -1
        self._sort_asc = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        fl = QHBoxLayout()
        fl.addWidget(QLabel("类型:"))
        self._type_filter = QComboBox()
        self._type_filter.addItem("全部", None)
        self._type_filter.addItem("TCP", "tcp_server")
        self._type_filter.addItem("WebSocket", "ws_server")
        self._type_filter.currentIndexChanged.connect(self._refresh)
        fl.addWidget(self._type_filter)
        fl.addWidget(QLabel("搜索:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("名称/IP/端口...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        fl.addWidget(self._search)
        fl.addStretch()
        layout.addLayout(fl)

        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionsClickable(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

        srv_splitter = QSplitter(Qt.Vertical)
        srv_splitter.addWidget(self._table)
        self._log_tabs = QTabWidget()
        self._log_tabs.setTabsClosable(True)
        self._log_tabs.tabCloseRequested.connect(self._on_log_tab_close)
        srv_splitter.addWidget(self._log_tabs)
        srv_splitter.setStretchFactor(0, 3)
        srv_splitter.setStretchFactor(1, 1)
        layout.addWidget(srv_splitter)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("+ 添加", clicked=self._add_server))
        bl.addWidget(QPushButton("编辑", clicked=self._edit_server))
        bl.addWidget(QPushButton("删除", clicked=self._delete_selected_servers))
        bl.addWidget(QPushButton("启动选中", clicked=self._start_selected))
        bl.addWidget(QPushButton("停止选中", clicked=self._stop_selected))
        bl.addStretch()
        bl.addWidget(QPushButton("▶ 全部启动", clicked=self._start_all))
        bl.addWidget(QPushButton("■ 全部停止", clicked=self._stop_all))
        layout.addLayout(bl)
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

    def refresh(self):
        self._refresh()

    def _refresh(self):
        st = self._type_filter.currentData()
        self._all_servers = self._db.get_all_protocol_servers(st)
        if self._sort_col >= 0:
            key_map = {0: lambda s: s.name, 1: lambda s: s.ip,
                       2: lambda s: s.port, 3: lambda s: s.server_type}
            key_fn = key_map.get(self._sort_col, lambda s: s.name)
            self._all_servers.sort(key=key_fn, reverse=not self._sort_asc)
        self._populate_table()
        if self._search.text().strip():
            self._filter(self._search.text())

    def _populate_table(self):
        t = self._table
        t.setColumnCount(8)
        t.setHorizontalHeaderLabels(["名称", "类型", "监听地址", "端口", "关联目标", "响应模式", "状态", "操作"])
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 7):
            hh.setSectionResizeMode(c, QHeaderView.Fixed if c != 4 else QHeaderView.Stretch)
            if c in (1, 2, 3, 5, 6):
                t.setColumnWidth(c, 60)
        hh.setSectionResizeMode(7, QHeaderView.Fixed)
        t.setColumnWidth(7, 80)

        t.setRowCount(len(self._all_servers))
        all_workers = {**self._tcp_workers, **self._ws_workers}
        for row, s in enumerate(self._all_servers):
            ni = QTableWidgetItem(s.name); ni.setData(Qt.UserRole, s.id)
            t.setItem(row, 0, ni)
            t.setItem(row, 1, QTableWidgetItem("TCP" if "tcp" in s.server_type else "WS"))
            t.setItem(row, 2, QTableWidgetItem(s.ip))
            pi = QTableWidgetItem(str(s.port)); pi.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 3, pi)
            # 关联目标信息
            if s.target_id:
                target = self._db.get_protocol_target(s.target_id)
                target_info = f"{target.ip}:{target.port}" if target else f"ID:{s.target_id}"
            else:
                target_info = "(全局)"
            t.setItem(row, 4, QTableWidgetItem(target_info))
            t.setItem(row, 5, QTableWidgetItem("回显" if s.response_mode == "echo" else "固定"))
            running = s.id in all_workers
            status_item = QTableWidgetItem("运行中" if running else "已停止")
            status_item.setForeground(Qt.green if running else Qt.red)
            t.setItem(row, 6, status_item)
            btn = QPushButton("Stop" if running else "Start")
            btn.setStyleSheet("color: #e74c3c;" if running else "color: #27ae60;")
            btn.clicked.connect(partial(self._toggle_server, s))
            t.setCellWidget(row, 7, btn)
        self._status_label.setText(f"共 {len(self._all_servers)} 个服务端, {len(all_workers)} 个运行中")

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._table.rowCount()):
            match = False
            for col in (0, 2, 3, 4):
                item = self._table.item(row, col)
                if item and s in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match if s else False)

    def _on_header_clicked(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col; self._sort_asc = True
        self._refresh()

    def _toggle_server(self, srv, _checked=None):
        from src.database import ProtocolServer
        s: ProtocolServer = srv
        st = s.server_type
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        if s.id in workers:
            w = workers.pop(s.id); w.stop_server()
            self._log_to_server(s.id, f"Stop [{s.name}] {s.ip}:{s.port}")
            # 移除对应的日志 tab
            for tab_idx, sid in list(self._log_tab_to_sid.items()):
                if sid == s.id:
                    self._log_tabs.removeTab(tab_idx)
                    del self._log_tab_to_sid[tab_idx]
                    self._server_logs.pop(s.id, None)
                    break
        else:
            for sid in workers:
                other = self._db.get_protocol_server(sid)
                if other and other.port == s.port:
                    QMessageBox.warning(self, "端口冲突", f"端口 {s.port} 已被 [{other.name}] 占用。")
                    return
            # 创建该服务的日志 tab
            log_w = QPlainTextEdit()
            log_w.setReadOnly(True)
            log_w.setMaximumBlockCount(5000)
            tab_idx = self._log_tabs.addTab(log_w, f"{s.name}:{s.port}")
            self._log_tabs.setCurrentIndex(tab_idx)
            self._server_logs[s.id] = log_w
            self._log_tab_to_sid[tab_idx] = s.id
            self._log_to_server(s.id, f"Start [{s.name}] {s.ip}:{s.port}")
            if st == "tcp_server":
                w = TcpServerWorker(server_id=s.id, ip=s.ip, port=s.port,
                                    encoding=s.encoding, head_len=s.head_length,
                                    response_mode=s.response_mode, response_message=s.response_message)
                w.message_received.connect(partial(self._on_srv_msg, s.id, s.name))
            else:
                w = WsServerWorker(server_id=s.id, ip=s.ip, port=s.port, path=s.ws_path,
                                   response_mode=s.response_mode, response_message=s.response_message)
                w.message_received.connect(partial(self._on_srv_msg, s.id, s.name, ""))
                w.client_event.connect(partial(self._log_to_server, s.id))
            w.status_changed.connect(partial(self._log_to_server, s.id))
            w.error_occurred.connect(lambda err, sid=s.id: self._log_to_server(sid, f"[ERR] {err}"))
            w.finished.connect(partial(self._on_worker_finished, st, s.id))
            workers[s.id] = w; w.start()
        self._refresh()

    def _log_to_server(self, sid: int, text: str):
        """向指定服务端的日志 tab 追加文本。"""
        log = self._server_logs.get(sid)
        if log:
            log.appendPlainText(text)

    def _on_srv_msg(self, sid: int, name: str, addr="", msg=""):
        ts = datetime.now().strftime("%H:%M:%S")
        a = f" [{addr}]" if addr else ""
        self._log_to_server(sid, f"[{ts}] [{name}]{a} RECV:\n{msg}")

    def _on_worker_finished(self, st: str, sid: int):
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        workers.pop(sid, None)
        self._refresh()

    def _on_log_tab_close(self, idx: int):
        """关闭日志标签页并停止对应服务端。"""
        sid = self._log_tab_to_sid.pop(idx, None)
        if sid is not None:
            for workers in (self._tcp_workers, self._ws_workers):
                w = workers.pop(sid, None)
                if w:
                    w.stop_server()
                    break
            self._server_logs.pop(sid, None)
        if idx >= 0:
            self._log_tabs.removeTab(idx)
        # 修正剩余 tab 的索引映射
        self._log_tab_to_sid = {}
        for i in range(self._log_tabs.count()):
            w = self._log_tabs.widget(i)
            for _sid, _log in list(self._server_logs.items()):
                if _log == w:
                    self._log_tab_to_sid[i] = _sid
                    break
        self._refresh()

    def _add_server(self):
        st = self._type_filter.currentData() or "tcp_server"
        dlg = ServerDialog("添加监听器", st, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.add_protocol_server(
                name=d["name"], server_type=st, ip=d["ip"], port=d["port"],
                encoding=d.get("encoding", "UTF-8"), head_length=d.get("head_length", 0),
                ws_path=d.get("ws_path", "/"), response_mode=d["response_mode"],
                response_message=d["response_message"],
            )
            self._refresh()

    def _edit_server(self):
        rows = set(i.row() for i in self._table.selectedIndexes())
        if not rows:
            return QMessageBox.information(self, "提示", "请选择一条记录。")
        row = rows.pop()
        item = self._table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        all_workers = {**self._tcp_workers, **self._ws_workers}
        if sid in all_workers:
            return QMessageBox.warning(self, "提示", "请先停止该监听器再编辑。")
        data = dict(name=srv.name, ip=srv.ip, port=srv.port, encoding=srv.encoding,
                    head_length=srv.head_length, ws_path=srv.ws_path,
                    response_mode=srv.response_mode, response_message=srv.response_message)
        dlg = ServerDialog("编辑监听器", srv.server_type, data, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.update_protocol_server(
                server_id=sid, name=d["name"], server_type=srv.server_type,
                ip=d["ip"], port=d["port"], encoding=d.get("encoding", "UTF-8"),
                head_length=d.get("head_length", 0), ws_path=d.get("ws_path", "/"),
                response_mode=d["response_mode"], response_message=d["response_message"],
                target_id=srv.target_id,
            )
            self._refresh()

    def _get_selected_server_ids(self) -> list[int]:
        rows = set(i.row() for i in self._table.selectedIndexes())
        ids = []
        for row in rows:
            item = self._table.item(row, 0)
            if item:
                sid = item.data(Qt.UserRole)
                if sid is not None:
                    ids.append(sid)
        return ids

    def _delete_selected_servers(self):
        ids = self._get_selected_server_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要删除的记录。")
        all_workers = {**self._tcp_workers, **self._ws_workers}
        running = [sid for sid in ids if sid in all_workers]
        if running:
            return QMessageBox.warning(self, "提示", f"有 {len(running)} 个监听器正在运行，请先停止再删除。")
        names = []
        for sid in ids:
            srv = self._db.get_protocol_server(sid)
            names.append(srv.name if srv else f"#{sid}")
        r = QMessageBox.question(self, "确认删除",
                                 f"确定要删除选中的 {len(ids)} 个监听器吗？\n{', '.join(names[:5])}",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for sid in ids:
                self._db.delete_protocol_server(sid)
            self._refresh()

    def _start_selected(self):
        ids = self._get_selected_server_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要启动的监听器。")
        for sid in ids:
            srv = self._db.get_protocol_server(sid)
            if srv:
                self._toggle_server(srv)

    def _stop_selected(self):
        ids = self._get_selected_server_ids()
        if not ids:
            return QMessageBox.information(self, "提示", "请选择要停止的监听器。")
        for sid in ids:
            for workers in (self._tcp_workers, self._ws_workers):
                w = workers.pop(sid, None)
                if w:
                    w.stop_server()
                    self._log_to_server(sid, f"Stop")
                    for tab_idx, tsid in list(self._log_tab_to_sid.items()):
                        if tsid == sid:
                            self._log_tabs.removeTab(tab_idx)
                            del self._log_tab_to_sid[tab_idx]
                            self._server_logs.pop(sid, None)
                            break
                    break
        self._refresh()

    def _start_all(self):
        for s in self._all_servers:
            all_workers = {**self._tcp_workers, **self._ws_workers}
            if s.id not in all_workers:
                self._toggle_server(s)

    def _stop_all(self):
        for w in list(self._tcp_workers.values()):
            w.stop_server()
        self._tcp_workers.clear()
        for w in list(self._ws_workers.values()):
            w.stop_server()
        self._ws_workers.clear()
        for log in self._server_logs.values():
            log.appendPlainText("服务端已停止")
        self._refresh()

    def has_active_servers(self) -> bool:
        return bool(self._tcp_workers) or bool(self._ws_workers)

    def stop_all_servers(self):
        self._stop_all()


# ── 全局测试历史 ────────────────────────────────────────────


class _GlobalHistoryTab(QWidget):
    """全局协议测试历史。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_sessions = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        fl = QHBoxLayout()
        fl.addWidget(QLabel("协议:"))
        self._proto_filter = QComboBox()
        self._proto_filter.addItem("全部", None)
        self._proto_filter.addItem("TCP", "tcp_client")
        self._proto_filter.addItem("WebSocket", "ws_client")
        self._proto_filter.currentIndexChanged.connect(self.refresh)
        fl.addWidget(self._proto_filter)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索 IP/端口...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        fl.addWidget(self._search)
        fl.addStretch()
        export_btn = QPushButton("导出")
        export_btn.clicked.connect(self._export)
        fl.addWidget(export_btn)
        layout.addLayout(fl)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["时间", "集合", "协议", "目标", "端口", "结果"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        self._table.setColumnWidth(0, 160)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._table.setColumnWidth(5, 50)
        self._table.cellClicked.connect(self._on_cell_clicked)

        hist_splitter = QSplitter(Qt.Vertical)
        hist_splitter.addWidget(self._table)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("点击行查看请求和响应详情...")
        hist_splitter.addWidget(self._detail)
        hist_splitter.setStretchFactor(0, 3)
        hist_splitter.setStretchFactor(1, 1)
        layout.addWidget(hist_splitter)

    def refresh(self):
        proto = self._proto_filter.currentData()
        self._all_sessions = self._db.get_protocol_test_sessions(proto)
        self._populate_table()
        if self._search.text().strip():
            self._filter(self._search.text())

    def _populate_table(self):
        sessions = self._all_sessions
        self._table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            self._table.setItem(row, 0, QTableWidgetItem(s.started_at))
            self._table.setItem(row, 1, QTableWidgetItem(s.collection_name or "-"))
            proto_label = "TCP" if "tcp" in s.protocol_type else "WS"
            self._table.setItem(row, 2, QTableWidgetItem(proto_label))
            self._table.setItem(row, 3, QTableWidgetItem(s.target_ip))
            self._table.setItem(row, 4, QTableWidgetItem(str(s.target_port)))
            ok = "OK" if s.success else "FAIL"
            ri = QTableWidgetItem(ok)
            ri.setForeground(Qt.green if s.success else Qt.red)
            self._table.setItem(row, 5, ri)

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._table.rowCount()):
            ip = self._table.item(row, 3)
            port = self._table.item(row, 4)
            match = (ip and s in ip.text().lower()) or (port and s in port.text())
            self._table.setRowHidden(row, not match if s else False)

    def _on_cell_clicked(self, row: int, col: int):
        if row < len(self._all_sessions):
            sess = self._all_sessions[row]
            detail = f"请求:\n---\n响应 ({'OK' if sess.success else 'FAIL'}):\n{sess.response}"
            if sess.error_msg:
                detail += f"\n\n错误:\n{sess.error_msg}"
            self._detail.setPlainText(detail)

    def _export(self):
        sessions = self._all_sessions
        if not sessions:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        fp, _ = QFileDialog.getSaveFileName(self, "导出测试历史", "protocol_history.csv",
                                             "CSV (*.csv);;Excel (*.xlsx)")
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                import csv
                writer = csv.writer(f)
                writer.writerow(["测试时间", "集合", "协议", "目标IP", "端口", "结果", "响应报文", "错误信息"])
                for s in sessions:
                    writer.writerow([
                        s.started_at,
                        s.collection_name or "-",
                        "TCP" if "tcp" in s.protocol_type else "WS",
                        s.target_ip,
                        s.target_port,
                        "OK" if s.success else "FAIL",
                        s.response or "",
                        s.error_msg or "",
                    ])
            QMessageBox.information(self, "导出完成", f"已导出 {len(sessions)} 条记录。")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))


# ── 独立客户端 ──────────────────────────────────────────────


class _StandaloneClientTab(QWidget):
    """独立客户端 —— 不依赖集合/目标，快速测试连接。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._client_worker: TcpClientWorker | WsClientWorker | None = None
        raw = self._db.get_setting("standalone_presets", "")
        try:
            self._presets: list[dict] = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            self._presets = []
        self._selected_preset_idx: int | None = None
        self._last_response = ""
        self._last_raw = b""
        self._setup_ui()
        self._load_config()
        self._refresh_preset_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 协议选择 ──
        proto_row = QHBoxLayout()
        self._proto_combo = QComboBox()
        self._proto_combo.addItem("TCP", "tcp_client")
        self._proto_combo.addItem("WebSocket", "ws_client")
        self._proto_combo.currentIndexChanged.connect(self._on_proto_changed)
        proto_row.addWidget(QLabel("协议:")); proto_row.addWidget(self._proto_combo)
        proto_row.addStretch()
        layout.addLayout(proto_row)

        # ── 参数行 ──
        self._tcp_params_w = QWidget()
        tf = QHBoxLayout(self._tcp_params_w); tf.setContentsMargins(0, 0, 0, 0); tf.setSpacing(2)
        tf.addWidget(QLabel("IP:")); self._param_ip = QLineEdit("127.0.0.1"); self._param_ip.setPlaceholderText("IP"); self._param_ip.setMaximumWidth(130)
        tf.addWidget(self._param_ip)
        tf.addWidget(QLabel("端口:")); self._param_port = QSpinBox(); self._param_port.setRange(1, 65535); self._param_port.setValue(35126)
        tf.addWidget(self._param_port)
        tf.addWidget(QLabel("编码:")); self._param_enc = QComboBox(); self._param_enc.addItems(ENCODINGS); self._param_enc.setEditable(True); self._param_enc.setMaximumWidth(90)
        tf.addWidget(self._param_enc)
        tf.addWidget(QLabel("HeadLen:")); self._param_hl = QSpinBox(); self._param_hl.setRange(0, 20); self._param_hl.setSuffix("位"); self._param_hl.setToolTip("0=原始"); self._param_hl.setValue(5); self._param_hl.setMaximumWidth(70)
        tf.addWidget(self._param_hl)
        tf.addWidget(QLabel("超时:")); self._param_timeout = QDoubleSpinBox(); self._param_timeout.setRange(0.1, 60); self._param_timeout.setValue(5.0); self._param_timeout.setSingleStep(0.5); self._param_timeout.setDecimals(1); self._param_timeout.setSuffix("s")
        tf.addWidget(self._param_timeout)
        tf.addStretch()

        self._ws_params_w = QWidget()
        wf = QHBoxLayout(self._ws_params_w); wf.setContentsMargins(0, 0, 0, 0); wf.setSpacing(2)
        wf.addWidget(QLabel("URL:")); self._param_ws_url = QLineEdit("ws://127.0.0.1:35126/ws")
        wf.addWidget(self._param_ws_url)
        wf.addWidget(QLabel("超时:")); self._param_ws_timeout = QDoubleSpinBox(); self._param_ws_timeout.setRange(0.1, 60); self._param_ws_timeout.setValue(5.0); self._param_ws_timeout.setSingleStep(0.5); self._param_ws_timeout.setSuffix("s")
        wf.addWidget(self._param_ws_timeout)
        self._param_ws_ssl = QCheckBox("SSL")
        wf.addWidget(self._param_ws_ssl)
        wf.addStretch()

        self._param_stack = QStackedWidget()
        self._param_stack.setMaximumHeight(32)
        self._param_stack.addWidget(self._tcp_params_w)
        self._param_stack.addWidget(self._ws_params_w)
        layout.addWidget(self._param_stack)

        # ── 参数变更自动保存 ──
        self._proto_combo.currentIndexChanged.connect(lambda: self._save_config())
        self._param_ip.textChanged.connect(lambda: self._save_config())
        self._param_port.valueChanged.connect(lambda: self._save_config())
        self._param_enc.currentTextChanged.connect(lambda: self._save_config())
        self._param_hl.valueChanged.connect(lambda: self._save_config())
        self._param_timeout.valueChanged.connect(lambda: self._save_config())
        self._param_ws_url.textChanged.connect(lambda: self._save_config())
        self._param_ws_timeout.valueChanged.connect(lambda: self._save_config())
        self._param_ws_ssl.toggled.connect(lambda: self._save_config())

        # ── 左右分栏：预设 | 发送+响应 ──
        h_splitter = QSplitter(Qt.Horizontal)

        # 左侧：预设
        preset_g = QGroupBox("预设报文")
        pl = QVBoxLayout(preset_g)
        self._preset_list = QListWidget()
        self._preset_list.setAlternatingRowColors(True)
        self._preset_list.setStyleSheet(
            "QListWidget::item:selected { background-color: #3498db; color: white; }"
            "QListWidget::item:selected:!active { background-color: #5dade2; color: white; }"
        )
        self._preset_list.itemClicked.connect(self._on_preset_clicked)
        pl.addWidget(self._preset_list)
        self._preset_selected_label = QLabel("")
        self._preset_selected_label.setStyleSheet("color: #27ae60; font-size: 11px;")
        self._preset_selected_label.setWordWrap(True)
        pl.addWidget(self._preset_selected_label)
        pbl = QGridLayout()
        buttons = [("添加", self._add_preset), ("保存", self._save_preset),
                   ("修改", self._edit_preset), ("删除", self._delete_preset),
                   ("清空", self._clear_preset_selection)]
        for i, (btn_text, slot) in enumerate(buttons):
            btn = QPushButton(btn_text, clicked=slot)
            if btn_text == "保存":
                btn.setShortcut("Ctrl+S")
                btn.setToolTip("Ctrl+S")
            pbl.addWidget(btn, i // 3, i % 3)
        pl.addLayout(pbl)
        h_splitter.addWidget(preset_g)

        # 右侧：发送 + 响应
        right_splitter = QSplitter(Qt.Vertical)

        send_g = QGroupBox("发送消息")
        sl = QVBoxLayout(send_g)
        self._send_edit = QPlainTextEdit()
        self._send_edit.setPlaceholderText("输入要发送的报文...")
        sl.addWidget(self._send_edit)
        sh = QHBoxLayout()
        self._send_btn = QPushButton("发送")
        self._send_btn.setMinimumWidth(80)
        self._send_btn.clicked.connect(self._send_message)
        sh.addWidget(self._send_btn)
        self._terminate_btn = QPushButton("终止")
        self._terminate_btn.setVisible(False)
        self._terminate_btn.clicked.connect(self._cancel_client)
        sh.addWidget(self._terminate_btn)
        sh.addWidget(QPushButton("清空", clicked=self._send_edit.clear))
        sh.addStretch()
        sl.addLayout(sh)
        right_splitter.addWidget(send_g)

        resp_g = QGroupBox("响应")
        rl = QVBoxLayout(resp_g)
        resp_tool = QHBoxLayout()
        resp_tool.addWidget(QLabel("编码:"))
        self._resp_enc_combo = QComboBox()
        self._resp_enc_combo.setEditable(True)
        self._resp_enc_combo.addItems(ENCODINGS)
        self._resp_enc_combo.currentTextChanged.connect(self._refresh_response_display)
        resp_tool.addWidget(self._resp_enc_combo)
        self._resp_hex_toggle = QPushButton("十六进制")
        self._resp_hex_toggle.setCheckable(True)
        self._resp_hex_toggle.toggled.connect(self._refresh_response_display)
        resp_tool.addWidget(self._resp_hex_toggle)
        resp_tool.addStretch()
        rl.addLayout(resp_tool)
        self._resp_edit = QPlainTextEdit()
        self._resp_edit.setReadOnly(True)
        self._resp_edit.setPlaceholderText("响应将显示在这里...")
        self._resp_edit.setFont(QFont("Consolas", 10))
        rl.addWidget(self._resp_edit)
        rl.addWidget(QPushButton("清空响应", clicked=self._resp_edit.clear))
        right_splitter.addWidget(resp_g)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        h_splitter.addWidget(right_splitter)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setSizes([120, 680])
        layout.addWidget(h_splitter)

    # ── 协议切换 ──

    def _on_proto_changed(self, idx: int):
        self._param_stack.setCurrentIndex(
            0 if self._proto_combo.currentData() == "tcp_client" else 1)

    # ── 外部接口 ──

    def prefill(self, ip: str, port: int) -> None:
        """从连通测试跳转时预填 IP 和端口。"""
        self._param_ip.setText(ip)
        self._param_port.setValue(port)

    # ── 预设报文 ──

    def _refresh_preset_list(self):
        lst = self._preset_list
        lst.clear()
        for i, p in enumerate(self._presets):
            item = QListWidgetItem(p.get("name", ""))
            item.setData(Qt.UserRole, i)
            lst.addItem(item)
        # 恢复选中状态
        if self._selected_preset_idx is not None and self._selected_preset_idx < len(self._presets):
            lst.setCurrentRow(self._selected_preset_idx)
            self._preset_selected_label.setText(f"✓ 已选择: {self._presets[self._selected_preset_idx].get('name', '')}")
        else:
            self._selected_preset_idx = None
            self._preset_selected_label.setText("")

    def _save_presets_to_settings(self):
        self._db.set_setting("standalone_presets",
                             json.dumps(self._presets, ensure_ascii=False))

    def _on_preset_clicked(self, item: QListWidgetItem):
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._presets):
            return
        msg = self._presets[idx].get("message", "")
        current = self._send_edit.toPlainText()
        if current and current != msg:
            if not any(p.get("message", "") == current for p in self._presets):
                reply = QMessageBox.question(
                    self, "内容未保存",
                    "当前报文未保存为模板，是否放弃并重新选择报文？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    self._preset_list.setCurrentItem(item)
                    return
        self._send_edit.setPlainText(msg)
        self._selected_preset_idx = idx
        self._preset_selected_label.setText(f"✓ 已选择: {self._presets[idx].get('name', '')}")

    def _load_config(self):
        """从数据库加载独立客户端配置。"""
        raw = self._db.get_setting("standalone_config", "")
        if not raw:
            return
        try:
            cfg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        proto = cfg.get("proto", "tcp_client")
        self._proto_combo.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._param_stack.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._param_ip.setText(cfg.get("ip", "127.0.0.1"))
        self._param_port.setValue(cfg.get("port", 35126))
        self._param_enc.setCurrentText(cfg.get("encoding", "UTF-8"))
        self._param_hl.setValue(cfg.get("head_length", 5))
        self._param_timeout.setValue(cfg.get("timeout", 5.0))
        self._param_ws_url.setText(cfg.get("ws_url", "ws://127.0.0.1:35126/ws"))
        self._param_ws_timeout.setValue(cfg.get("ws_timeout", 5.0))
        self._param_ws_ssl.setChecked(cfg.get("ws_ssl", False))

    def _save_config(self):
        """保存独立客户端配置到数据库。"""
        proto = self._proto_combo.currentData()
        cfg = {
            "proto": proto,
            "ip": self._param_ip.text().strip(),
            "port": self._param_port.value(),
            "encoding": self._param_enc.currentText(),
            "head_length": self._param_hl.value(),
            "timeout": self._param_timeout.value(),
            "ws_url": self._param_ws_url.text().strip(),
            "ws_timeout": self._param_ws_timeout.value(),
            "ws_ssl": self._param_ws_ssl.isChecked(),
        }
        self._db.set_setting("standalone_config", json.dumps(cfg, ensure_ascii=False))
        if idx is None or idx >= len(self._presets):
            return
        msg = self._presets[idx].get("message", "")
        current = self._send_edit.toPlainText()
        if current and current != msg:
            # 仅当内容不是从任何预设加载的（手动编辑过）才警告
            if not any(p.get("message", "") == current for p in self._presets):
                reply = QMessageBox.question(
                    self, "内容未保存",
                    "当前报文未保存为模板，是否放弃并重新选择报文？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    self._preset_list.setCurrentItem(item)
                    return
        self._send_edit.setPlainText(msg)
        self._selected_preset_idx = idx
        self._preset_selected_label.setText(f"✓ 已选择: {self._presets[idx].get('name', '')}")

    def _add_preset(self):
        default_name = f"报文{len(self._presets) + 1}"
        name, ok = QInputDialog.getText(self, "添加预设", "预设名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p.get("name", "") == name for p in self._presets):
            QMessageBox.warning(self, "名称重复", f"预设「{name}」已存在，请使用其他名称。")
            return
        msg = self._send_edit.toPlainText()
        self._presets.append({"name": name, "message": msg})
        self._save_presets_to_settings()
        self._refresh_preset_list()

    def _save_preset(self):
        """将当前输入框内容保存到选中的预设，或弹窗选择覆盖/新建。"""
        new_msg = self._send_edit.toPlainText()
        if self._selected_preset_idx is not None and self._selected_preset_idx < len(self._presets):
            # 有选中预设：保存到选中项
            old_msg = self._presets[self._selected_preset_idx].get("message", "")
            if old_msg and old_msg != new_msg:
                reply = QMessageBox.question(
                    self, "确认覆盖",
                    f"预设「{self._presets[self._selected_preset_idx]['name']}」已有内容，是否覆盖？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            self._presets[self._selected_preset_idx]["message"] = new_msg
        elif self._presets:
            # 未选中预设：弹窗选择覆盖或新建
            dlg = QDialog(self)
            dlg.setWindowTitle("保存报文")
            dlg.setMinimumWidth(300)
            dl = QVBoxLayout(dlg)
            dl.addWidget(QLabel("选择要覆盖的预设，或新建预设："))
            lst = QListWidget()
            lst.addItem("── 新建预设 ──")
            for p in self._presets:
                lst.addItem(p.get("name", ""))
            lst.setCurrentRow(0)
            dl.addWidget(lst)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            dl.addWidget(bb)
            if dlg.exec() != QDialog.Accepted:
                return
            sel = lst.currentRow()
            if sel < 0:
                return
            if sel == 0:
                default_name = f"报文{len(self._presets) + 1}"
                name, ok = QInputDialog.getText(self, "新建预设", "预设名称:", text=default_name)
                if not ok or not name.strip():
                    return
                name = name.strip()
                if any(p.get("name", "") == name for p in self._presets):
                    QMessageBox.warning(self, "名称重复", f"预设「{name}」已存在。")
                    return
                self._presets.append({"name": name, "message": new_msg})
                self._selected_preset_idx = len(self._presets) - 1
            else:
                idx = sel - 1
                old_msg = self._presets[idx].get("message", "")
                if old_msg and old_msg != new_msg:
                    reply = QMessageBox.question(
                        self, "确认覆盖",
                        f"预设「{self._presets[idx]['name']}」已有内容，是否覆盖？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return
                self._presets[idx]["message"] = new_msg
                self._selected_preset_idx = idx
        else:
            # 无预设：直接新建
            name, ok = QInputDialog.getText(self, "新建预设", "预设名称:", text="报文1")
            if not ok or not name.strip():
                return
            self._presets.append({"name": name.strip(), "message": new_msg})
            self._selected_preset_idx = len(self._presets) - 1
        self._save_presets_to_settings()
        self._refresh_preset_list()

    def _edit_preset(self):
        item = self._preset_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._presets):
            return
        p = self._presets[idx]
        name, ok = QInputDialog.getText(self, "编辑预设", "预设名称:", text=p["name"])
        if not ok or not name.strip():
            return
        self._presets[idx]["name"] = name.strip()
        self._presets[idx]["message"] = self._send_edit.toPlainText()
        self._save_presets_to_settings()
        self._refresh_preset_list()

    def _delete_preset(self):
        item = self._preset_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._presets):
            return
        name = self._presets[idx].get("name", "")
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除预设「{name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._presets.pop(idx)
        self._save_presets_to_settings()
        self._refresh_preset_list()

    def _clear_preset_selection(self):
        """清空预设选中状态。"""
        self._selected_preset_idx = None
        self._preset_list.clearSelection()
        self._preset_selected_label.setText("")

    # ── 发送消息 ──

    def _send_message(self):
        if self._client_worker and self._client_worker.isRunning():
            QMessageBox.information(self, "提示", "有请求正在进行中。")
            return
        msg = self._send_edit.toPlainText()
        if not msg:
            QMessageBox.information(self, "提示", "请输入要发送的消息。")
            return
        self._send_btn.setEnabled(False)
        self._send_btn.setText("发送中...")
        self._terminate_btn.setVisible(True)
        proto = self._proto_combo.currentData()
        if proto == "tcp_client":
            self._client_worker = TcpClientWorker(
                ip=self._param_ip.text().strip(), port=self._param_port.value(),
                message=msg, encoding=self._param_enc.currentText(),
                head_len=self._param_hl.value(), timeout=self._param_timeout.value())
        else:
            self._client_worker = WsClientWorker(
                url=self._param_ws_url.text().strip(), message=msg,
                timeout=self._param_ws_timeout.value())
        self._client_worker.finished.connect(self._on_client_done)
        self._client_worker.start()

    def _cancel_client(self):
        """终止当前客户端请求。"""
        if self._client_worker and self._client_worker.isRunning():
            self._client_worker.terminate()
            self._client_worker.wait(3000)
        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")
        self._terminate_btn.setVisible(False)
        self._resp_edit.appendPlainText("[终止] 请求已被用户终止")

    def _on_client_done(self, success: bool, response: str):
        self._send_btn.setEnabled(True)
        self._send_btn.setText("发送")
        self._terminate_btn.setVisible(False)
        self._last_response = response
        enc = self._param_enc.currentText()
        try:
            self._last_raw = response.encode(enc, errors='replace')
        except Exception:
            self._last_raw = response.encode('utf-8', errors='replace')
        if success and self._last_raw:
            detected = self._detect_encoding(self._last_raw)
            if detected and detected != self._resp_enc_combo.currentText():
                self._resp_enc_combo.setCurrentText(detected)
        ts = datetime.now().strftime("%H:%M:%S")
        tag = "OK" if success else "FAIL"
        self._append_response(f"[{ts}] {tag}:\n{response}")

    def _detect_encoding(self, raw: bytes) -> str | None:
        candidates = ["UTF-8", "GBK", "GB2312", "GB18030", "ISO-8859-1", "ASCII"]
        for enc in candidates:
            try:
                raw.decode(enc)
                return enc
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return None

    def _refresh_response_display(self):
        if not self._last_raw:
            return
        if self._resp_hex_toggle.isChecked():
            lines = []
            for i in range(0, len(self._last_raw), 16):
                chunk = self._last_raw[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{i:04x}  {hex_part:<48}  {ascii_part}")
            self._resp_edit.setPlainText("\n".join(lines))
        else:
            enc = self._resp_enc_combo.currentText()
            try:
                text = self._last_raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                text = self._last_raw.decode(enc, errors="replace")
            self._resp_edit.setPlainText(text)

    def _append_response(self, text: str):
        if self._resp_hex_toggle.isChecked():
            self._refresh_response_display()
        else:
            self._resp_edit.appendPlainText(text)


# ── 协议测试主面板 ──────────────────────────────────────────


class ProtocolPanel(QWidget):

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._target_tabs: dict[int, tuple[QWidget, _TargetDetailPanel]] = {}  # target_id -> (tab, detail)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧: 集合侧边栏
        self._sidebar = _CollectionSidebar(self._db)
        self._sidebar.collection_selected.connect(self._on_collection_selected)
        splitter.addWidget(self._sidebar)

        # 右侧: 功能标签页
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(False)

        # Tab 0: 集合详情（目标表格）
        self._detail_tab = _CollectionDetailTab(self._db)
        self._detail_tab.target_double_clicked.connect(self._open_target_tab)
        self._tabs.addTab(self._detail_tab, "集合详情")

        # Tab 1: 客户端（独立，固定）
        self._standalone_client = _StandaloneClientTab(self._db)
        self._tabs.addTab(self._standalone_client, "客户端")

        # Tab 2: 服务端（全部）
        self._server_tab = _ServerTab(self._db)
        self._tabs.addTab(self._server_tab, "服务端")

        # Tab 3: 全局测试历史
        self._history_tab = _GlobalHistoryTab(self._db)
        self._tabs.addTab(self._history_tab, "全局测试历史")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.tabCloseRequested.connect(self._on_tab_close)

        # 记录固定标签页: [0, 1, 2, 3]
        self._fixed_tab_count = 4
        self._tabs.setTabsClosable(True)
        self._hide_fixed_close_buttons()

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 880])

        layout.addWidget(splitter)

    def _on_collection_selected(self, coll):
        """选中集合 → 切换到集合详情并加载。"""
        if coll is not None:
            self._tabs.setCurrentIndex(0)
        self._detail_tab.set_collection(coll)
        # 关闭所有目标标签页
        for tid in list(self._target_tabs.keys()):
            tab_w, _ = self._target_tabs[tid]
            idx = self._tabs.indexOf(tab_w)
            if idx >= 0:
                self._tabs.removeTab(idx)
            del self._target_tabs[tid]

    def _hide_fixed_close_buttons(self):
        """隐藏固定标签页的关闭按钮。"""
        from PySide6.QtWidgets import QTabBar
        bar = self._tabs.tabBar()
        right = QTabBar.ButtonPosition.RightSide
        for i in range(self._fixed_tab_count):
            if i < self._tabs.count():
                bar.setTabButton(i, right, None)

    def _open_target_tab(self, target, coll):
        """双击目标 → 打开/切换到该目标的详情标签页。"""
        if target.id in self._target_tabs:
            tab_w, detail = self._target_tabs[target.id]
            detail.set_target(target, coll)
            self._tabs.setCurrentWidget(tab_w)
            return

        tab_w = QWidget()
        layout = QVBoxLayout(tab_w)
        layout.setContentsMargins(4, 4, 4, 4)
        detail = _TargetDetailPanel(self._db)
        detail.set_target(target, coll)
        detail.target_updated.connect(lambda: self._detail_tab._refresh_targets())
        layout.addWidget(detail)

        label = f"{target.ip}:{target.port}"
        idx = self._tabs.addTab(tab_w, label)
        self._tabs.setCurrentIndex(idx)
        self._target_tabs[target.id] = (tab_w, detail)

    def _on_tab_close(self, idx: int):
        """只允许关闭动态目标标签页。"""
        if idx < self._fixed_tab_count:
            return  # 固定标签页不可关闭
        tab_w = self._tabs.widget(idx)
        for tid, (tw, _) in list(self._target_tabs.items()):
            if tw == tab_w:
                detail = self._target_tabs[tid][1]
                detail.stop_all_servers()
                del self._target_tabs[tid]
                break
        self._tabs.removeTab(idx)

    def _on_tab_changed(self, idx: int):
        widget = self._tabs.widget(idx)
        if widget == self._server_tab:
            self._server_tab.refresh()
        elif widget == self._history_tab:
            self._history_tab.refresh()

    # ── 公共方法 ─────────────────────────────────────────────

    def has_active_servers(self) -> bool:
        active = self._server_tab.has_active_servers()
        for _, detail in self._target_tabs.values():
            active = active or detail.has_active_servers()
        return active

    def stop_all_servers(self) -> None:
        self._server_tab.stop_all_servers()
        for _, detail in self._target_tabs.values():
            detail.stop_all_servers()

    def prefill_client_target(self, ip: str, port: int) -> None:
        """从连通测试跳转：切换到客户端标签页并预填 IP/端口。"""
        # 找到客户端标签页索引并切换
        for i in range(self._tabs.count()):
            if self._tabs.widget(i) == self._standalone_client:
                self._tabs.setCurrentIndex(i)
                break
        self._standalone_client.prefill(ip, port)
