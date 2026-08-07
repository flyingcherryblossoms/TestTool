"""协议测试面板 —— 左侧集合列表 + 右侧动态目标标签页。

结构:
  QSplitter(Horizontal)
  ├── [左] _CollectionSidebar
  └── [右] QTabWidget
        ├── Tab 0: _CollectionDetailTab (目标表格，双击打开目标标签页)
        ├── Tab 1: _StandaloneClientTab (独立客户端，固定)
        ├── Tab 2: _ServerTab (全部服务端，固定)
        ├── Tab 3: _GlobalHistoryTab (全局测试历史，固定)
        └── [动态] 目标标签页 (客户端 / Mock服务端 / 历史)

客户端与服务端的公共逻辑抽到 src/ui/protocol_components.py：
- ClientPanelBase —— 独立客户端与每个目标详情内的客户端各持一个实例
- ServerPanelBase —— 独立服务端与每个目标详情内的 Mock服务端各持一个实例
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.protocol import compute_length_header
from src.ui.clipboard import KIND_PROTO_TARGET, copy_items, paste_items
from src.ui.collection_sidebar import CollectionSidebarBase
from src.ui.table_utils import (
    TargetDragTable,
    enable_stretch_fill,
    refresh_tooltips,
    unique_copy_name,
)
from src.ui.protocol_workers import (
    TcpClientWorker,
    WsClientWorker,
)
from src.ui.format_text import FormatTextEdit
from src.json_handler import (
    export_collection_to_json,
    export_collections_to_json,
    import_collection_from_json,
    export_client_config,
    export_server_config,
)
from src.ui.protocol_components import (
    ClientPanelBase,
    ServerPanelBase,
    ServerDialog,
    ENCODINGS,
    _hex_dump,
)


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

class _TargetDialog(QDialog):
    """添加/编辑协议目标对话框 —— 支持所有目标字段。"""
    def __init__(self, title: str, db: Database,
                 default_collection_id: int | None = None,
                 ip: str = "", port: int = 80,
                 name: str = "", encoding: str = "UTF-8",
                 recv_encoding: str = "UTF-8", head_length: int = 5,
                 timeout: float = 30.0, ws_path: str = "",
                 ws_use_ssl: bool = False, send_message: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("目标名称")
        layout.addRow("名称:", self._name)
        self._ip = QLineEdit(ip)
        self._ip.setPlaceholderText("192.168.1.1")
        layout.addRow("IP:", self._ip)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        layout.addRow("端口:", self._port)
        # 集合选择：默认当前集合，否则未分类
        self._collection_combo = QComboBox()
        self._uncat_collection_id = None
        for c in db.get_all_protocol_collections():
            if c.name == "未分类":
                self._uncat_collection_id = c.id
            count = len(db.get_protocol_targets(c.id))
            self._collection_combo.addItem(f"{c.name} ({count})", c.id)
        cid = default_collection_id if default_collection_id is not None \
            else self._uncat_collection_id
        idx = self._collection_combo.findData(cid)
        if idx >= 0:
            self._collection_combo.setCurrentIndex(idx)
        layout.addRow("所属集合:", self._collection_combo)
        self._enc = QComboBox()
        self._enc.addItems(ENCODINGS)
        self._enc.setEditable(True)
        self._enc.setCurrentText(encoding)
        layout.addRow("发送编码:", self._enc)
        self._recv_enc = QComboBox()
        self._recv_enc.addItems(ENCODINGS)
        self._recv_enc.setEditable(True)
        self._recv_enc.setCurrentText(recv_encoding)
        layout.addRow("接收编码:", self._recv_enc)
        self._hl = QSpinBox()
        self._hl.setRange(0, 20)
        self._hl.setValue(head_length)
        self._hl.setSuffix("位")
        layout.addRow("头长度:", self._hl)
        self._timeout = QDoubleSpinBox()
        self._timeout.setRange(0.1, 60)
        self._timeout.setValue(timeout)
        self._timeout.setSingleStep(0.5)
        self._timeout.setSuffix("s")
        layout.addRow("超时:", self._timeout)
        self._ws_path = QLineEdit(ws_path)
        layout.addRow("WS路径:", self._ws_path)
        self._ws_ssl = QCheckBox("SSL")
        self._ws_ssl.setChecked(ws_use_ssl)
        layout.addRow("WS SSL:", self._ws_ssl)
        self._send_msg = FormatTextEdit(text=send_message)
        self._send_msg.setFixedHeight(60)
        layout.addRow("报文格式:", self._send_msg.format_combo)
        layout.addRow("发送报文:", self._send_msg)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _validate(self):
        import re
        ip = self._ip.text().strip()
        if not ip:
            QMessageBox.warning(self, "验证失败", "IP 地址不能为空。")
            return
        pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
        m = re.match(pattern, ip)
        if not m:
            QMessageBox.warning(self, "验证失败",
                                "IP 地址格式不正确，请输入有效的 IPv4 地址（例如 192.168.1.1）。")
            return
        parts = [int(g) for g in m.groups()]
        if any(p > 255 for p in parts):
            QMessageBox.warning(self, "验证失败",
                                "IP 地址超出范围，每段取值范围为 0-255。")
            return
        port = self._port.value()
        if port < 1 or port > 65535:
            QMessageBox.warning(self, "验证失败",
                                "端口号超出范围，有效范围为 1-65535。")
            return
        self.accept()

    def get_data(self) -> dict:
        return dict(
            ip=self._ip.text().strip(),
            port=self._port.value(),
            name=self._name.text().strip(),
            encoding=self._enc.currentText(),
            recv_encoding=self._recv_enc.currentText(),
            head_length=self._hl.value(),
            timeout=self._timeout.value(),
            ws_path=self._ws_path.text().strip(),
            ws_use_ssl=self._ws_ssl.isChecked(),
            send_message=self._send_msg.toPlainText(),
        )

    @property
    def collection_id(self) -> int | None:
        """所选集合 ID。"""
        return self._collection_combo.currentData()


# ── 集合侧边栏 ──────────────────────────────────────────────


class _CollectionSidebar(CollectionSidebarBase):
    """固定在左侧的协议测试集合列表 —— 分类树形结构：未分类 / 自定义集合。"""

    target_add_requested = Signal()
    target_edit_requested = Signal()
    target_delete_requested = Signal()
    target_select_all_requested = Signal()
    target_invert_requested = Signal()

    # ── 集合访问方法（接入协议测试数据表）────────────────────

    def _get_all_collections(self):
        return self._db.get_all_protocol_collections()

    def _ensure_uncat(self):
        for c in self._db.get_all_protocol_collections():
            if c.name == "未分类":
                return c
        cid = self._db.add_protocol_collection(name="未分类", protocol_type="tcp_client")
        return self._db.get_protocol_collection(cid)

    def _uncat_node_id(self):
        return self._ensure_uncat().id

    def _count_targets(self, cid) -> int:
        return len(self._db.get_protocol_targets(cid))

    def _get_collection(self, cid):
        return self._db.get_protocol_collection(cid)

    def _add_collection(self, name: str, protocol_type: str = "tcp_client") -> int:
        return self._db.add_protocol_collection(name=name, protocol_type=protocol_type)

    def _copy_collection_targets(self, src_cid: int, new_cid: int):
        """把源集合的全部目标复制到新集合（含目标上挂的服务端配置）。"""
        for t in self._db.get_protocol_targets(src_cid):
            tid = self._db.add_protocol_target(
                collection_id=new_cid, ip=t.ip, port=t.port, name=t.name,
                encoding=t.encoding, recv_encoding=t.recv_encoding,
                head_length=t.head_length, timeout=t.timeout,
                ws_path=t.ws_path, ws_use_ssl=t.ws_use_ssl,
                send_message=t.send_message, send_presets=t.send_presets,
                stress_params=t.stress_params,
            )
            for s in self._db.get_protocol_servers_by_target(t.id):
                self._db.add_protocol_server(
                    name=s.name, server_type=s.server_type,
                    ip=s.ip, port=s.port, encoding=s.encoding,
                    recv_encoding=s.recv_encoding, head_length=s.head_length,
                    ws_path=s.ws_path, response_mode=s.response_mode,
                    response_message=s.response_message,
                    response_delay=s.response_delay, target_id=tid,
                )

    def _update_collection(self, cid: int, name: str):
        coll = self._db.get_protocol_collection(cid)
        self._db.update_protocol_collection(
            cid, name=name, protocol_type=coll.protocol_type if coll else "tcp_client"
        )

    def _delete_collection(self, cid: int):
        self._db.delete_protocol_collection(cid)

    def _move_to_uncat(self, cid: int):
        uncat = self._ensure_uncat()
        self._db.move_protocol_targets_to_collection(cid, uncat.id)

    def _save_collections_order(self, ordered_ids: list[int]):
        self._db.update_protocol_collections_order(ordered_ids)

    def _new_collection_prefix(self) -> str:
        return "协议测试"

    # ── 右键菜单 ───────────────────────────────────────────

    def _build_collection_menu(self, menu, item, cid: int):
        menu.addAction("新建目标", self.target_add_requested.emit)
        menu.addSeparator()
        menu.addAction("刷新集合", self.refresh)
        menu.addAction("集合重命名", self._on_edit)
        menu.addAction("删除集合", self._on_delete)

    # ── 导入导出（协议集合 JSON）─────────────────────────────

    def _on_import(self):
        filepaths, _ = QFileDialog.getOpenFileNames(
            self, "导入集合", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not filepaths:
            return
        imported = 0
        for filepath in filepaths:
            coll_list, err = import_collection_from_json(filepath)
            if err:
                QMessageBox.warning(self, "导入失败", f"{filepath}\n{err}")
                continue
            for coll_data in coll_list:
                cid = self._db.add_protocol_collection(
                    name=coll_data["name"], protocol_type=coll_data["protocol_type"]
                )
                for t in coll_data["targets"]:
                    presets = json.dumps(t.get("send_presets", []), ensure_ascii=False)
                    stress = json.dumps(t.get("stress_params", {}), ensure_ascii=False)
                    tid = self._db.add_protocol_target(
                        collection_id=cid, ip=t["ip"], port=t["port"],
                        name=t.get("name", ""), encoding=t["encoding"],
                        recv_encoding=t.get("recv_encoding", "UTF-8"),
                        head_length=t["head_length"], timeout=t["timeout"],
                        ws_path=t["ws_path"], ws_use_ssl=t["ws_use_ssl"],
                        send_message=t["send_message"], send_presets=presets,
                        stress_params=stress,
                    )
                    for s in t.get("servers", []):
                        self._db.add_protocol_server(
                            name=s["name"], server_type=s["server_type"],
                            ip=s["ip"], port=s["port"], encoding=s["encoding"],
                            recv_encoding=s.get("recv_encoding", "UTF-8"),
                            head_length=s["head_length"], ws_path=s["ws_path"],
                            response_mode=s["response_mode"],
                            response_message=s["response_message"],
                            response_delay=s.get("response_delay", 0), target_id=tid,
                        )
                imported += 1
        self.refresh()
        QMessageBox.information(self, "导入完成", f"成功导入 {imported} 个集合。")

    def _on_export(self):
        # 收集所有选中的集合（支持多选）
        selected = self._tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先选择一个或多个集合。")
            return
        # 解析选中集合 ID
        coll_ids = []
        for item in selected:
            cid = item.data(0, Qt.UserRole)
            if cid is not None:
                coll = self._get_collection(cid)
                if coll and coll.name != "未分类":
                    coll_ids.append(cid)
        if not coll_ids:
            QMessageBox.information(self, "提示", "请选择有效的集合（不能导出未分类）。")
            return
        # 构建导出数据
        collections_data = []
        for cid in coll_ids:
            coll = self._get_collection(cid)
            targets = self._db.get_protocol_targets(cid)
            targets_data = []
            for t in targets:
                servers = self._db.get_protocol_servers_by_target(t.id)
                try:
                    presets = json.loads(t.send_presets) if t.send_presets else []
                except json.JSONDecodeError:
                    presets = []
                try:
                    stress = json.loads(t.stress_params) if t.stress_params else {}
                except (json.JSONDecodeError, TypeError):
                    stress = {}
                targets_data.append({
                    "ip": t.ip, "port": t.port, "name": t.name,
                    "encoding": t.encoding, "recv_encoding": t.recv_encoding,
                    "head_length": t.head_length,
                    "timeout": t.timeout, "ws_path": t.ws_path,
                    "ws_use_ssl": t.ws_use_ssl, "send_message": t.send_message,
                    "send_presets": presets, "stress_params": stress,
                    "servers": [{"name": s.name, "server_type": s.server_type,
                                 "ip": s.ip, "port": s.port, "encoding": s.encoding,
                                 "recv_encoding": s.recv_encoding,
                                 "head_length": s.head_length, "ws_path": s.ws_path,
                                 "response_mode": s.response_mode,
                                 "response_message": s.response_message,
                                 "response_delay": s.response_delay} for s in servers],
                })
            collections_data.append({
                "name": coll.name, "protocol_type": coll.protocol_type,
                "targets": targets_data,
            })
        # 默认文件名：集合名称_导出时间(yyyyMMddHHmmss)，多选取首集合名
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        if len(collections_data) == 1:
            default_name = f"{collections_data[0]['name']}_{ts}.json"
        else:
            default_name = f"{collections_data[0]['name']}_等{len(collections_data)}个集合_{ts}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出集合", default_name, "JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        ok, err = export_collections_to_json(filepath, collections_data)
        if ok:
            QMessageBox.information(
                self, "导出完成", f"已导出 {len(collections_data)} 个集合到:\n{filepath}")
        else:
            QMessageBox.critical(self, "导出失败", err)

# ── 目标详情面板 ────────────────────────────────────────────


class TargetClientPanel(ClientPanelBase):
    """目标详情内的客户端子页 —— 参数持久化到目标，记录测试会话。"""

    def __init__(self, owner: "_TargetDetailPanel"):
        self._owner = owner
        super().__init__(owner._db, parent=owner, show_len_label=True)

    # ── 钩子 ────────────────────────────────────────────────

    def _build_action_buttons(self, proto_row):
        proto_row.addWidget(QPushButton("保存参数", clicked=self._owner._save_params))
        proto_row.addWidget(QPushButton("导出配置", clicked=self._owner._export_target))
        proto_row.addWidget(QPushButton("导入配置", clicked=self._owner._import_target_config))
        # 服务端展开/收起按钮
        self._server_toggle_btn = QPushButton("服务端")
        self._server_toggle_btn.setCheckable(True)
        self._server_toggle_btn.clicked.connect(self._owner._toggle_server_panel)
        proto_row.addWidget(self._server_toggle_btn)

    def _on_param_changed(self):
        self._mark_config_dirty()

    def _on_ctrl_s_no_focus(self):
        self._owner._save_params()

    def _can_send(self) -> bool:
        return bool(self._owner._target)

    def _can_edit_presets(self) -> bool:
        return bool(self._owner._target)

    def get_presets(self):
        t = self._owner._target
        return self._owner._load_presets(t.send_presets) if t else []

    def save_presets(self, presets):
        self._owner._save_presets_to_target(presets)

    def _build_client_worker(self, msg, proto):
        if proto == "tcp_client":
            return TcpClientWorker(
                ip=self._param_ip.text().strip(), port=self._param_port.value(),
                message=msg, encoding=self._param_enc.currentText(),
                head_len=self._param_hl.value(), timeout=self._param_timeout.value(),
            )
        url = self._param_ws_url.text().strip() or f"ws://{self._param_ip.text().strip()}:{self._param_port.value()}/ws"
        return WsClientWorker(url=url, message=msg, timeout=self._param_ws_timeout.value())

    def _response_encoding(self):
        return self._resp_enc_combo.currentText() or "UTF-8"

    def _client_ip_label(self):
        return self._param_ip.text().strip() or "?"

    def _client_endpoint(self):
        return (self._param_ip.text().strip(), self._param_port.value())

    def _record_session(self, success: bool, response: str, request: str):
        owner = self._owner
        if owner._target and owner._coll:
            owner._db.add_protocol_test_session(
                collection_id=owner._coll.id, collection_name=owner._coll.name,
                target_id=owner._target.id, protocol_type=owner._coll.protocol_type,
                target_ip=self._param_ip.text().strip(),
                target_port=self._param_port.value(),
                success=success, request=request,
                response=response, error_msg="" if success else response,
            )
            owner._refresh_history()

    def _update_len_label(self):
        if not self._owner._target:
            return
        msg = self._send_edit.toPlainText()
        enc = self._param_enc.currentText()
        hl = self._param_hl.value()
        try:
            nb = len(msg.encode(enc))
            hdr = compute_length_header(msg, enc, hl)
            self._len_label.setText(f"报文长度: {nb} 字节, 长度头: {hdr}")
        except (UnicodeEncodeError, UnicodeDecodeError):
            self._len_label.setText("编码错误")

    def _params_area_max_height(self):
        return 64

    # ── 压测参数持久化：目标客户端写入目标行，保存由"保存参数"统一处理 ──

    def _load_stress_from_store(self) -> dict:
        t = self._owner._target
        if not t:
            return {}
        try:
            return json.loads(t.stress_params) if t.stress_params else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_stress_to_store(self, sp: dict):
        # 仅标记脏，落库由"保存参数"按钮统一写入 update_protocol_target
        self._mark_config_dirty()

    # ── 加载目标参数 ────────────────────────────────────────

    def load_target(self, target):
        # 切换目标时清空预设草稿/选中
        self._selected_preset_idx = None
        self._drafts.clear()
        self._dirty.clear()
        proto_idx = 0 if _target_proto_label(target) != "WS" else 1
        cfg = {
            "proto": "ws_client" if proto_idx == 1 else "tcp_client",
            "ip": target.ip, "port": target.port,
            "encoding": target.encoding, "recv_encoding": target.recv_encoding,
            "head_length": target.head_length, "timeout": target.timeout,
            "ws_url": target.ws_path if (target.ws_path and target.ws_path.startswith("ws"))
            else "ws://127.0.0.1:80/ws",
            "ws_timeout": target.timeout, "ws_ssl": target.ws_use_ssl,
        }
        self.set_params(cfg)
        self._apply_stress_params(self._load_stress_from_store())
        self._send_edit.setPlainText(target.send_message)
        self._update_len_label()
        self._refresh_preset_list()


class TargetMockServerPanel(ServerPanelBase):
    """目标详情内的 Mock服务端 —— 按 target_id 过滤。"""

    def __init__(self, owner: "_TargetDetailPanel"):
        self._owner = owner
        super().__init__(owner._db, parent=owner)

    def set_target(self, target):
        self.refresh()

    # ── 钩子 ────────────────────────────────────────────────

    def _can_refresh(self) -> bool:
        return bool(self._owner._target)

    def _can_add(self) -> bool:
        return bool(self._owner._target)

    # 显示搜索筛选与状态栏；Mock 服务端不显示类型筛选
    def _has_filter_bar(self) -> bool:
        return True

    def _show_type_filter(self) -> bool:
        return False

    def _show_status_label(self) -> bool:
        return True

    def _load_servers(self):
        return self._db.get_protocol_servers_by_target(self._owner._target.id)

    def _server_columns(self):
        return ["名称", "监听地址", "端口", "发送编码", "接收编码", "HeadLen", "响应模式", "延迟(ms)", "状态", "操作"]

    def _row_cells(self, s, is_tcp: bool):
        return [s.name, s.ip, str(s.port), s.encoding or "", s.recv_encoding or "",
                str(s.head_length), "回显" if s.response_mode == "echo" else "固定",
                str(s.response_delay)]

    def _center_columns(self):
        return {2, 5}

    def _sortable_column(self, col: int) -> bool:
        return col < 3

    def _sort_key(self, col: int):
        key_map = {0: lambda s: s.name, 1: lambda s: s.ip, 2: lambda s: s.port}
        return key_map.get(col)

    def _default_add_type(self) -> str:
        return "tcp_server"

    def _add_dialog_title(self) -> str:
        return "添加 Mock 服务端"

    def _edit_dialog_title(self) -> str:
        return "编辑 Mock 服务端"

    def _add_target_id(self):
        return self._owner._target.id

    def _edit_target_id(self, srv):
        return self._owner._target.id

    def _log_block_cap(self) -> int:
        return 2000

    def _confirm_delete_text(self, ids) -> str:
        return f"确定要删除选中的 {len(ids)} 个监听器吗？"

    def _running_delete_warning(self, running) -> str:
        return "请先停止选中的监听器再删除。"

    def _on_stop_all(self):
        for tab_idx in list(self._log_tab_to_sid.keys()):
            self._log_tabs.removeTab(tab_idx)
        self._log_tab_to_sid.clear()
        # 清除临时日志数据，保留编码值以便重启后复用
        self._logs.clear()
        self._status.clear()
        self._recv_raw.clear()
        self._send_combos.clear()
        self._recv_combos.clear()
        self._hex_toggles.clear()

    def _start_all(self):
        if not self._owner._target:
            return
        servers = self._db.get_protocol_servers_by_target(self._owner._target.id)
        for srv in servers:
            self._toggle_server(srv)


class _TargetDetailPanel(QWidget):
    """单个目标详情：客户端 / Mock服务端 / 测试历史。"""

    target_updated = Signal()
    test_finished = Signal()
    config_dirty_changed = Signal(bool)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._target = None
        self._coll = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._client_panel = TargetClientPanel(self)
        self._server_panel = TargetMockServerPanel(self)
        self._client_panel.config_dirty_changed.connect(self.config_dirty_changed.emit)
        self._client_panel.test_finished.connect(self.test_finished.emit)
        self._client_panel.presets_saved.connect(self._on_presets_saved)
        self._server_collapsed = False

        # 左右并排：客户端(左) | Mock服务端(右)，可拖动分隔条调整比例、可收起/展开服务端
        self._split_h = QSplitter(Qt.Horizontal)
        self._split_h.setChildrenCollapsible(True)
        self._split_h.splitterMoved.connect(self._on_splitter_moved)
        # 忽略面板自身的宽度 sizeHint，允许自由调整两半比例
        self._client_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._client_panel.setMinimumWidth(0)
        self._server_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._server_panel.setMinimumWidth(0)
        self._split_h.addWidget(self._client_panel)
        self._split_h.addWidget(self._server_panel)
        self._split_h.setSizes([900, 0])
        self._server_collapsed = True
        self._client_collapsed = False
        self._size_check_timer = QTimer(self)
        self._size_check_timer.setSingleShot(True)
        self._size_check_timer.timeout.connect(self._check_splitter_sizes)
        # 服务端默认关闭，按钮同步
        if hasattr(self._client_panel, '_server_toggle_btn'):
            self._client_panel._server_toggle_btn.setChecked(False)

        self._split_page = QWidget()
        sp_layout = QVBoxLayout(self._split_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        top_bar = QHBoxLayout()
        top_bar.addStretch()
        sp_layout.addLayout(top_bar)
        sp_layout.addWidget(self._split_h)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._split_page, "客户端/Mock服务端")
        self._history_widget = self._build_history_tab()
        self._tabs.addTab(self._history_widget, "测试历史")
        layout.addWidget(self._tabs)

    def _focus_in(self, widget) -> bool:
        fw = QApplication.focusWidget()
        return fw is not None and widget.isAncestorOf(fw)

    def _on_splitter_moved(self, pos, index):
        """拖拽分隔条后延迟检查尺寸。"""
        self._size_check_timer.start(50)

    def _check_splitter_sizes(self):
        """拖拽结束：客户端低于 80px 阈值时自动隐藏。"""
        sizes = self._split_h.sizes()
        if len(sizes) < 2:
            return
        total = sum(sizes)
        if sizes[0] <= 80 and sizes[0] > 0 and not self._client_collapsed:
            self._client_collapsed = True
            self._split_h.setSizes([0, total])
        elif sizes[0] > 80 and self._client_collapsed:
            self._client_collapsed = False

    def toggle_server_collapsed(self):
        """收起/展开右侧的 Mock服务端 面板。"""
        sizes = self._split_h.sizes()
        total = sum(sizes) if sizes else 900
        if not self._server_collapsed:
            self._server_collapsed = True
            self._split_h.setSizes([total, 0])
        else:
            self._server_collapsed = False
            self._client_collapsed = False
            self._split_h.setSizes([total // 2, total // 2])
        self._split_h.updateGeometry()
        # 更新按钮选中状态
        if hasattr(self._client_panel, '_server_toggle_btn'):
            self._client_panel._server_toggle_btn.setChecked(not self._server_collapsed)

    # ── 预设辅助 ────────────────────────────────────────────

    def _load_presets(self, presets_json: str):
        try:
            return json.loads(presets_json) if presets_json else []
        except json.JSONDecodeError:
            return []

    def _save_presets_to_target(self, presets: list):
        if self._target:
            self._db.update_protocol_target(
                self._target.id, ip=self._target.ip, port=self._target.port,
                name=self._target.name, encoding=self._target.encoding,
                recv_encoding=self._target.recv_encoding,
                head_length=self._target.head_length, timeout=self._target.timeout,
                ws_path=self._target.ws_path, ws_use_ssl=self._target.ws_use_ssl,
                send_message=self._target.send_message,
                send_presets=json.dumps(presets, ensure_ascii=False),
            )

    def _on_presets_saved(self):
        if self._target:
            self._target = self._db.get_protocol_target(self._target.id)
            self.target_updated.emit()

    # ── 保存参数 / 导出 / 导入 ──────────────────────────────

    def _save_params(self):
        if not self._target:
            return
        p = self._client_panel.collect_params()
        proto = p["proto"]
        self._db.update_protocol_target(
            self._target.id,
            ip=p["ip"], port=p["port"],
            name=self._target.name,
            encoding=p["encoding"] if proto == "tcp_client" else "UTF-8",
            recv_encoding=p["recv_encoding"] if proto == "tcp_client" else "UTF-8",
            head_length=p["head_length"] if proto == "tcp_client" else 0,
            timeout=p["timeout"] if proto == "tcp_client" else p["ws_timeout"],
            ws_path=p["ws_url"] if proto == "ws_client" else "",
            ws_use_ssl=p["ws_ssl"],
            send_message=self._target.send_message,
            send_presets=self._target.send_presets,
            stress_params=json.dumps(
                self._client_panel.collect_stress_params(), ensure_ascii=False),
        )
        self._target = self._db.get_protocol_target(self._target.id)
        self.target_updated.emit()
        self._client_panel.reset_config_dirty()

    def _export_target(self):
        if not self._target:
            return
        t = self._target
        servers = self._db.get_protocol_servers_by_target(t.id)
        try:
            presets = json.loads(t.send_presets) if t.send_presets else []
        except json.JSONDecodeError:
            presets = []
        try:
            stress = json.loads(t.stress_params) if t.stress_params else {}
        except (json.JSONDecodeError, TypeError):
            stress = {}
        data = {
            "version": 1, "type": "protocol_client_config",
            "protocol_type": "tcp_client" if _target_proto_label(t) != "WS" else "ws_client",
            "ip": t.ip, "port": t.port, "encoding": t.encoding,
            "recv_encoding": t.recv_encoding,
            "head_length": t.head_length, "timeout": t.timeout,
            "ws_url": t.ws_path, "ws_use_ssl": t.ws_use_ssl,
            "send_message": t.send_message, "send_presets": presets,
            "stress_params": stress,
            "servers": [{"name": s.name, "server_type": s.server_type,
                         "ip": s.ip, "port": s.port, "encoding": s.encoding,
                         "recv_encoding": s.recv_encoding,
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
        if not result or not isinstance(result[0], dict):
            return
        cfg = result[0]
        proto = cfg.get("protocol_type", "tcp_client")
        self._db.update_protocol_target(
            self._target.id,
            ip=cfg.get("ip", self._target.ip),
            port=cfg.get("port", self._target.port),
            name=self._target.name,
            encoding=cfg.get("encoding", "UTF-8"),
            recv_encoding=cfg.get("recv_encoding", self._target.recv_encoding),
            head_length=cfg.get("head_length", 5),
            timeout=cfg.get("timeout", 5.0),
            ws_path=cfg.get("ws_url", ""),
            ws_use_ssl=cfg.get("ws_use_ssl", False),
            send_message=cfg.get("send_message", ""),
            send_presets=json.dumps(cfg.get("send_presets", []), ensure_ascii=False),
            stress_params=json.dumps(cfg.get("stress_params", {}), ensure_ascii=False),
        )
        self._target = self._db.get_protocol_target(self._target.id)
        self.target_updated.emit()
        QMessageBox.information(self, "导入完成", "目标配置已更新。")

    def _toggle_server_panel(self):
        """从客户端按钮行切换服务端面板显示/隐藏。"""
        self.toggle_server_collapsed()

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
        fl.addWidget(QPushButton("刷新", clicked=self._refresh_history))
        fl.addWidget(QPushButton("删除", clicked=self._delete_hist_sessions))
        fl.addWidget(QPushButton("清空", clicked=self._clear_hist))
        fl.addWidget(QPushButton("导出", clicked=self._export_history))
        layout.addLayout(fl)

        self._hist_table = QTableWidget()
        self._hist_table.setColumnCount(4)
        self._hist_table.setHorizontalHeaderLabels(["时间", "结果", "目标", "端口"])
        self._hist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._hist_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._hist_table.setAlternatingRowColors(False)
        self._hist_table.verticalHeader().setVisible(False)
        hh = self._hist_table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._on_hist_header_clicked)
        self._hist_table.cellClicked.connect(self._on_hist_cell_clicked)
        self._hist_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._hist_table.customContextMenuRequested.connect(self._on_hist_menu)
        enable_stretch_fill(self._hist_table)
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
        status_val = self._hist_filter.currentData()
        if status_val is not None:
            sessions = [s for s in sessions if s.success == status_val]
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
        refresh_tooltips(t)

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
            detail = f"请求:\n{s.request}\n---\n响应 ({'OK' if s.success else 'FAIL'}):\n{s.response}"
            if s.error_msg:
                detail += f"\n\n错误:\n{s.error_msg}"
            self._hist_detail.setPlainText(detail)

    def _on_hist_menu(self, pos):
        item = self._hist_table.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("导出", self._export_history)
        menu.addAction("刷新", self._refresh_history)
        if item:
            row = item.row()
            model = self._hist_table.model()
            if not self._hist_table.selectionModel().isSelected(model.index(row, 0)):
                self._hist_table.selectRow(row)
            menu.addAction("删除", self._delete_hist_sessions)
        menu.addSeparator()
        menu.addAction("清空", self._clear_hist)
        menu.exec(self._hist_table.viewport().mapToGlobal(pos))

    def _delete_hist_sessions(self):
        rows = set(i.row() for i in self._hist_table.selectedIndexes())
        ids = [self._hist_sessions[r].id for r in rows
               if r < len(self._hist_sessions)]
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        r = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 条测试记录吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self._db.delete_protocol_test_sessions(ids)
        self._refresh_history()

    def _clear_hist(self):
        if not self._target:
            return
        r = QMessageBox.question(
            self, "确认清空",
            "确定要清空该目标的全部测试历史吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        sessions = self._db.get_protocol_test_sessions_by_target(self._target.id)
        ids = [s.id for s in sessions]
        if ids:
            self._db.delete_protocol_test_sessions(ids)
        self._refresh_history()

    def _export_history(self):
        sessions = self._hist_sessions
        sel_rows = sorted(set(i.row() for i in self._hist_table.selectedIndexes()))
        if sel_rows:
            sessions = [sessions[r] for r in sel_rows if r < len(sessions)]
        if not sessions:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        fp, sel_filter = QFileDialog.getSaveFileName(self, "导出测试历史", "target_history.xlsx",
                                                     "Excel (*.xlsx);;CSV (*.csv)")
        if not fp:
            return
        headers = ["测试时间", "协议", "目标IP", "端口", "结果", "请求报文", "响应报文", "错误信息"]
        rows = [
            [
                s.started_at,
                "TCP" if "tcp" in s.protocol_type else "WS",
                s.target_ip,
                s.target_port,
                "OK" if s.success else "FAIL",
                s.request or "",
                s.response or "",
                s.error_msg or "",
            ]
            for s in sessions
        ]
        is_xlsx = fp.lower().endswith(".xlsx")
        if not fp.lower().endswith((".csv", ".xlsx")):
            fp += ".xlsx" if "xlsx" in sel_filter else ".csv"
            is_xlsx = fp.lower().endswith(".xlsx")
        try:
            if is_xlsx:
                from src.excel_handler import export_rows_to_excel
                ok, err = export_rows_to_excel(fp, headers, rows)
                if not ok:
                    QMessageBox.critical(self, "导出失败", err)
                    return
            else:
                import csv
                with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)
            QMessageBox.information(self, "导出完成", f"已导出 {len(self._hist_sessions)} 条记录。")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ── 设置目标 ─────────────────────────────────────────

    def set_target(self, target, coll):
        self._target = target
        self._coll = coll
        if target is None:
            self.setEnabled(False)
            return
        self.setEnabled(True)
        self._client_panel.load_target(target)
        self._server_panel.set_target(target)
        self._client_panel.reset_dirty()
        self._refresh_history()

    def has_active_servers(self) -> bool:
        return self._server_panel.has_active_servers()

    def stop_all_servers(self):
        self._server_panel.stop_all_servers()

    def keyPressEvent(self, event):
        # 左右并排下：客户端 / Mock服务端 各自的 keyPressEvent 会自行处理 F5/删除，
        # 此处按焦点所在面板分发，未聚焦子面板时归到测试历史。
        if event.key() == Qt.Key_F5:
            if self._focus_in(self._client_panel):
                self._client_panel._refresh_preset_list()
            elif self._focus_in(self._server_panel):
                self._server_panel.refresh()
            else:
                self._refresh_history()
        elif event.key() == Qt.Key_Delete or (event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier):
            if self._focus_in(self._client_panel):
                self._client_panel._delete_preset()
            elif self._focus_in(self._server_panel):
                self._server_panel._delete_selected_servers()
            else:
                self._delete_hist_sessions()
        elif event.key() == Qt.Key_S and event.modifiers() == Qt.ControlModifier:
            if self._client_panel._send_edit.hasFocus():
                self._client_panel._save_preset()
            else:
                self._save_params()
        else:
            super().keyPressEvent(event)

class _CollectionDetailTab(QWidget):
    """显示选中集合的目标列表 —— 双击打开目标详情标签页。"""

    target_double_clicked = Signal(object, object)  # (target, collection)
    targets_changed = Signal()  # 目标增删改后通知刷新集合计数
    connectivity_test_requested = Signal(list)  # 选中的目标 dict 列表 → 连通测试临时列表

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._coll = None
        self._target_sort_col = -1
        self._target_sort_asc = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        top_bar = QHBoxLayout()
        self._target_count_label = QLabel("<b>目标列表</b>")
        top_bar.addWidget(self._target_count_label)
        self._target_search = QLineEdit()
        self._target_search.setPlaceholderText("搜索 IP/端口/描述...")
        self._target_search.setClearButtonEnabled(True)
        self._target_search.textChanged.connect(self._refresh_targets)
        top_bar.addWidget(self._target_search)
        layout.addLayout(top_bar)

        sel_bar = QHBoxLayout()
        sel_bar.addWidget(QPushButton("全选", clicked=lambda: self._target_table.selectAll()))
        sel_bar.addWidget(QPushButton("反选", clicked=self._invert_target_selection))
        sel_bar.addStretch()
        sel_bar.addWidget(QPushButton("刷新", clicked=self._refresh_targets))
        layout.addLayout(sel_bar)

        # 支持拖拽目标到左侧集合树归集
        self._target_table = TargetDragTable()
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._target_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._target_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._target_table.setAlternatingRowColors(False)
        self._target_table.verticalHeader().setVisible(False)
        self._target_table.horizontalHeader().setSectionsClickable(True)
        self._target_table.horizontalHeader().sectionClicked.connect(self._on_target_header_clicked)
        self._target_table.cellDoubleClicked.connect(self._on_target_double_clicked)
        self._target_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._target_table.customContextMenuRequested.connect(self._on_target_menu)
        enable_stretch_fill(self._target_table)
        layout.addWidget(self._target_table)

        tbl = QHBoxLayout()
        tbl.addWidget(QPushButton("添加", clicked=self._on_add_target))
        tbl.addWidget(QPushButton("编辑", clicked=self._on_edit_target))
        tbl.addWidget(QPushButton("删除", clicked=self._on_delete_target))
        tbl.addWidget(QPushButton("复制", clicked=self._copy_target))
        tbl.addWidget(QPushButton("测试", clicked=self._on_test_target))
        conn_btn = QPushButton("连通测试", clicked=self._on_connectivity_test_requested)
        conn_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        tbl.addWidget(conn_btn)
        tbl.addStretch()
        layout.addLayout(tbl)

    def set_collection(self, coll):
        self._coll = coll
        self._refresh_targets()

    def _refresh_targets(self):
        if not self._coll:
            self._target_table.setColumnCount(3)
            self._target_table.setHorizontalHeaderLabels(["名称", "IP", "端口"])
            self._update_target_sort_indicator()
            self._target_table.setRowCount(0)
            refresh_tooltips(self._target_table)
            return
        targets = self._db.get_protocol_targets(self._coll.id)
        # 搜索过滤
        search = self._target_search.text().strip().lower()
        if search:
            targets = [t for t in targets if
                       search in t.ip.lower() or
                       search in str(t.port) or
                       search in (t.name or "").lower() or
                       search in (t.encoding or "").lower() or
                       search in _target_proto_label(t).lower()]
        # 排序
        if self._target_sort_col >= 0:
            key_map = {
                0: lambda t: (t.name or "").lower(),
                1: lambda t: tuple(int(o) for o in t.ip.split(".")),
                2: lambda t: t.port,
                3: lambda t: (t.encoding or "").lower(),
                4: lambda t: (t.recv_encoding or "").lower(),
                5: lambda t: t.head_length,
                6: lambda t: t.timeout,
                7: lambda t: _target_proto_label(t),
            }
            key_fn = key_map.get(self._target_sort_col)
            if key_fn:
                targets.sort(key=key_fn, reverse=not self._target_sort_asc)
        self._target_count_label.setText(f"<b>目标列表</b> ({len(targets)})")
        t = self._target_table
        t.setColumnCount(8)
        t.setHorizontalHeaderLabels(["名称", "IP", "端口", "发送编码", "接收编码", "HeadLen", "超时", "类型"])
        self._update_target_sort_indicator()

        t.setRowCount(len(targets))
        for row, target in enumerate(targets):
            name_item = QTableWidgetItem(target.name or "")
            name_item.setData(Qt.UserRole, target.id)
            t.setItem(row, 0, name_item)
            t.setItem(row, 1, QTableWidgetItem(target.ip))
            pi = QTableWidgetItem(str(target.port)); pi.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 2, pi)
            t.setItem(row, 3, QTableWidgetItem(target.encoding))
            t.setItem(row, 4, QTableWidgetItem(target.recv_encoding))
            t.setItem(row, 5, QTableWidgetItem(str(target.head_length)))
            ti = QTableWidgetItem(f"{target.timeout}s"); ti.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 6, ti)
            t.setItem(row, 7, QTableWidgetItem(_target_proto_label(target)))
        refresh_tooltips(t)

    def _invert_target_selection(self):
        model = self._target_table.model()
        rows = self._target_table.rowCount()
        if rows == 0:
            return
        sm = self._target_table.selectionModel()
        sel_rows = set()
        for r in range(rows):
            if sm.isSelected(model.index(r, 0)):
                sel_rows.add(r)
        if not sel_rows:
            self._target_table.selectAll()
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        new_sel = QItemSelection()
        for r in range(rows):
            if r not in sel_rows:
                new_sel.select(model.index(r, 0), model.index(r, self._target_table.columnCount() - 1))
        sm.select(new_sel, QItemSelectionModel.ClearAndSelect)
        self._target_table.setFocus()

    def _on_target_header_clicked(self, col: int):
        if self._target_sort_col == col:
            self._target_sort_asc = not self._target_sort_asc
        else:
            self._target_sort_col = col
            self._target_sort_asc = True
        self._refresh_targets()

    def _update_target_sort_indicator(self):
        for c in range(self._target_table.columnCount()):
            item = self._target_table.horizontalHeaderItem(c)
            if item:
                base = item.text().rstrip(" ▲▼")
                arrow = " ▲" if (c == self._target_sort_col and self._target_sort_asc) else \
                        " ▼" if c == self._target_sort_col else ""
                item.setText(base + arrow)

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
        # 默认用当前选中的集合；未选中任何集合时落到"未分类"
        default_cid = self._coll.id if self._coll else None
        dlg = _TargetDialog("添加目标", self._db,
                            default_collection_id=default_cid, parent=self)
        if dlg.exec() == QDialog.Accepted:
            cid = dlg.collection_id
            if cid is None:
                cid = self._ensure_uncat_collection()
            self._db.add_protocol_target(collection_id=cid, **dlg.get_data())
            self._refresh_targets()
            self.targets_changed.emit()

    def _ensure_uncat_collection(self) -> int:
        """查找或创建"未分类"协议集合，返回其 ID。"""
        for c in self._db.get_all_protocol_collections():
            if c.name == "未分类":
                return c.id
        return self._db.add_protocol_collection(name="未分类", protocol_type="tcp_client")

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
        dlg = _TargetDialog("编辑目标", self._db, default_collection_id=t.collection_id,
                            ip=t.ip, port=t.port, name=t.name,
                            encoding=t.encoding, recv_encoding=t.recv_encoding,
                            head_length=t.head_length, timeout=t.timeout,
                            ws_path=t.ws_path, ws_use_ssl=t.ws_use_ssl,
                            send_message=t.send_message, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.update_protocol_target(target_id=tid, **d, send_presets=t.send_presets)
            new_cid = dlg.collection_id
            if new_cid is not None and new_cid != t.collection_id:
                self._db.move_protocol_target_ids_to_collection([tid], new_cid)
            self._refresh_targets()
            self.targets_changed.emit()

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
            self.targets_changed.emit()

    def _copy_target(self):
        """复制选中的目标，名称自动追加"副本"（含全部客户端参数及其下挂服务端）。"""
        if not self._coll:
            return QMessageBox.information(self, "提示", "请先选择一个集合。")
        rows = set(i.row() for i in self._target_table.selectedIndexes())
        if not rows:
            return QMessageBox.information(self, "提示", "请选择要复制的目标。")
        ids = []
        for row in sorted(rows):
            item = self._target_table.item(row, 0)
            if item and item.data(Qt.UserRole) is not None:
                ids.append(item.data(Qt.UserRole))
        if not ids:
            return
        targets = [t for t in (self._db.get_protocol_target(tid) for tid in ids) if t]
        if not targets:
            return
        existing = {t.name or "" for t in self._db.get_protocol_targets(self._coll.id)}
        for t in targets:
            new_name = unique_copy_name(t.name or "", existing)
            new_tid = self._db.add_protocol_target(
                collection_id=self._coll.id, ip=t.ip, port=t.port, name=new_name,
                encoding=t.encoding, recv_encoding=t.recv_encoding,
                head_length=t.head_length, timeout=t.timeout,
                ws_path=t.ws_path, ws_use_ssl=t.ws_use_ssl,
                send_message=t.send_message, send_presets=t.send_presets,
                stress_params=t.stress_params)
            for s in self._db.get_protocol_servers_by_target(t.id):
                self._db.add_protocol_server(
                    name=s.name, server_type=s.server_type,
                    ip=s.ip, port=s.port, encoding=s.encoding,
                    recv_encoding=s.recv_encoding, head_length=s.head_length,
                    ws_path=s.ws_path, response_mode=s.response_mode,
                    response_message=s.response_message,
                    response_delay=s.response_delay, target_id=new_tid)
            existing.add(new_name)
        self._refresh_targets()
        self.targets_changed.emit()

    def _copy_target_to_clip(self):
        """Ctrl+C：把选中的协议目标复制到应用内剪贴板（含其下挂服务端）。"""
        ids = self._get_selected_target_ids()
        if not ids:
            return
        payload = []
        for t in (self._db.get_protocol_target(tid) for tid in ids):
            if not t:
                continue
            payload.append({
                "ip": t.ip, "port": t.port, "name": t.name or "",
                "encoding": t.encoding, "recv_encoding": t.recv_encoding,
                "head_length": t.head_length, "timeout": t.timeout,
                "ws_path": t.ws_path, "ws_use_ssl": t.ws_use_ssl,
                "send_message": t.send_message, "send_presets": t.send_presets,
                "stress_params": t.stress_params,
                "servers": [
                    {"name": s.name, "server_type": s.server_type,
                     "ip": s.ip, "port": s.port, "encoding": s.encoding or "",
                     "recv_encoding": s.recv_encoding or "",
                     "head_length": s.head_length, "ws_path": s.ws_path or "",
                     "response_mode": s.response_mode,
                     "response_message": s.response_message or "",
                     "response_delay": s.response_delay}
                    for s in self._db.get_protocol_servers_by_target(t.id)
                ],
            })
        if payload:
            copy_items(KIND_PROTO_TARGET, payload)

    def _paste_target_from_clip(self):
        """Ctrl+V：把剪贴板中的协议目标粘贴到当前集合，名称追加"副本"。"""
        if not self._coll:
            return QMessageBox.information(self, "提示", "请先选择一个集合。")
        payload = paste_items(KIND_PROTO_TARGET)
        if not payload:
            return QMessageBox.information(self, "提示", "剪贴板中没有可粘贴的目标。")
        existing = {t.name or "" for t in self._db.get_protocol_targets(self._coll.id)}
        for p in payload:
            new_name = unique_copy_name(p.get("name", ""), existing)
            new_tid = self._db.add_protocol_target(
                collection_id=self._coll.id, ip=p["ip"], port=p["port"], name=new_name,
                encoding=p.get("encoding", "UTF-8"),
                recv_encoding=p.get("recv_encoding", "UTF-8"),
                head_length=p.get("head_length", 5), timeout=p.get("timeout", 30.0),
                ws_path=p.get("ws_path", ""), ws_use_ssl=p.get("ws_use_ssl", False),
                send_message=p.get("send_message", ""),
                send_presets=p.get("send_presets", "[]"),
                stress_params=p.get("stress_params", "{}"))
            for s in p.get("servers", []):
                self._db.add_protocol_server(
                    name=s["name"], server_type=s["server_type"],
                    ip=s["ip"], port=s["port"], encoding=s.get("encoding", "UTF-8"),
                    recv_encoding=s.get("recv_encoding", "UTF-8"),
                    head_length=s.get("head_length", 5), ws_path=s.get("ws_path", ""),
                    response_mode=s.get("response_mode", "echo"),
                    response_message=s.get("response_message", ""),
                    response_delay=s.get("response_delay", 0), target_id=new_tid)
            existing.add(new_name)
        self._refresh_targets()
        self.targets_changed.emit()

    def _on_test_target(self):
        row = self._target_table.currentRow()
        if row < 0:
            return
        self._on_target_double_clicked(row, 0)

    def _on_target_menu(self, pos):
        item = self._target_table.itemAt(pos)
        if item is not None:
            row = item.row()
            model = self._target_table.model()
            if not self._target_table.selectionModel().isSelected(model.index(row, 0)):
                self._target_table.selectRow(row)
        menu = QMenu(self)
        menu.addAction("添加", self._on_add_target)
        row = self._target_table.currentRow()
        if row >= 0:
            menu.addAction("测试", self._on_test_target)
            menu.addAction("编辑", self._on_edit_target)
            menu.addAction("连通测试", self._on_connectivity_test_requested)
        if self._target_table.selectedIndexes():
            menu.addAction("复制", self._copy_target)
            menu.addAction("删除", self._on_delete_target)
        menu.addSeparator()
        menu.addAction("全选", lambda: self._target_table.selectAll())
        menu.addAction("反选", self._invert_target_selection)
        menu.addAction("刷新", self._refresh_targets)
        menu.exec(self._target_table.mapToGlobal(pos))

    def _get_selected_target_ids(self) -> list[int]:
        """获取当前选中的协议目标 ID 列表（支持多选）。"""
        rows = set(i.row() for i in self._target_table.selectedIndexes())
        ids = []
        for row in rows:
            item = self._target_table.item(row, 0)
            if item and item.data(Qt.UserRole):
                ids.append(item.data(Qt.UserRole))
        return ids

    def _on_connectivity_test_requested(self):
        """把选中的协议目标发送到连通测试临时列表。"""
        ids = self._get_selected_target_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要发送到连通性测试的目标。")
            return
        targets = []
        for tid in ids:
            t = self._db.get_protocol_target(tid)
            if t:
                targets.append({
                    "ip": t.ip, "port": t.port,
                    "description": t.name or f"{t.ip}:{t.port}",
                })
        if targets:
            self.connectivity_test_requested.emit(targets)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self._copy_target_to_clip()
        elif event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            self._paste_target_from_clip()
        elif event.key() == Qt.Key_F5:
            self._refresh_targets()
        elif event.key() == Qt.Key_Delete or (event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier):
            self._on_delete_target()
        else:
            super().keyPressEvent(event)


# ── 服务端标签页（全部服务端）────────────────────────────


class _ServerTab(ServerPanelBase):
    """显示全部服务端配置（全局 + 目标关联），支持筛选排序。"""

    # ── 钩子：全局服务端与目标 Mock 的差异 ──────────────────

    def _has_filter_bar(self) -> bool:
        return True

    def _show_status_label(self) -> bool:
        return True

    def _content_margins(self):
        return (9, 9, 9, 9)

    def _load_servers(self):
        return self._db.get_all_protocol_servers(self._type_filter.currentData())

    def _server_columns(self):
        return ["名称", "类型", "监听地址", "端口", "发送编码", "接收编码", "关联目标", "响应模式", "延迟(ms)", "状态", "操作"]

    def _row_cells(self, s, is_tcp: bool):
        return [s.name, "TCP" if "tcp" in s.server_type else "WS",
                s.ip, str(s.port), s.encoding or "", s.recv_encoding or "",
                self._target_cell(s),
                "回显" if s.response_mode == "echo" else "固定",
                str(s.response_delay)]

    def _target_cell(self, s) -> str:
        if s.target_id:
            target = self._db.get_protocol_target(s.target_id)
            return f"{target.ip}:{target.port}" if target else f"ID:{s.target_id}"
        return "(全局)"

    def _center_columns(self):
        return {3}

    def _sort_key(self, col: int):
        key_map = {0: lambda s: s.name, 1: lambda s: s.server_type,
                   2: lambda s: s.ip, 3: lambda s: s.port,
                   4: lambda s: (s.encoding or "").lower(),
                   5: lambda s: (s.recv_encoding or "").lower()}
        return key_map.get(col)

    def _default_add_type(self) -> str:
        return self._type_filter.currentData() or "tcp_server"

    def _check_port_conflict(self, s) -> bool:
        for sid in {**self._tcp_workers, **self._ws_workers}:
            other = self._db.get_protocol_server(sid)
            if other and other.port == s.port:
                QMessageBox.warning(self, "端口冲突", f"端口 {s.port} 已被 [{other.name}] 占用。")
                return True
        return False

    def _confirm_delete_text(self, ids) -> str:
        names = []
        for sid in ids:
            srv = self._db.get_protocol_server(sid)
            names.append(srv.name if srv else f"#{sid}")
        return f"确定要删除选中的 {len(ids)} 个监听器吗？\n{', '.join(names[:5])}"

    def _running_delete_warning(self, running) -> str:
        return f"有 {len(running)} 个监听器正在运行，请先停止再删除。"

    def _on_stop_all(self):
        for log in self._logs.values():
            log.appendPlainText("服务端已停止")

class _GlobalHistoryTab(QWidget):
    """全局协议测试历史。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_sessions = []
        self._current_session = None
        self._last_raw = b""
        self._sort_col: int = 0
        self._sort_asc: bool = False
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
        layout.addLayout(fl)

        # ── 选择与删除操作栏 ──
        sel_bar = QHBoxLayout()
        sel_bar.addWidget(QPushButton("全选", clicked=self._select_all))

        sel_bar.addWidget(QPushButton("反选", clicked=self._invert_selection))
        sel_bar.addStretch()
        # 刷新按钮在删除按钮前面，删除/清空在行尾
        sel_bar.addWidget(QPushButton("刷新", clicked=self.refresh))
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._delete_selected)
        sel_bar.addWidget(del_btn)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_all)
        sel_bar.addWidget(clear_btn)
        # 导出按钮放在清空按钮后面
        sel_bar.addWidget(QPushButton("导出", clicked=self._export))
        layout.addLayout(sel_bar)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["时间", "集合", "协议", "目标", "端口", "结果"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.horizontalHeader().setSectionsClickable(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)
        enable_stretch_fill(self._table)

        hist_splitter = QSplitter(Qt.Vertical)

        # ── 详情区（编码 + 十六进制切换）──
        detail_w = QWidget()
        detail_layout = QVBoxLayout(detail_w)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(2)
        detail_tool = QHBoxLayout()
        detail_tool.addWidget(QLabel("编码:"))
        self._detail_enc_combo = QComboBox()
        self._detail_enc_combo.setEditable(True)
        self._detail_enc_combo.addItems(ENCODINGS)
        self._detail_enc_combo.currentTextChanged.connect(self._refresh_detail_display)
        detail_tool.addWidget(self._detail_enc_combo)
        self._detail_hex_toggle = QPushButton("十六进制")
        self._detail_hex_toggle.setCheckable(True)
        self._detail_hex_toggle.toggled.connect(self._refresh_detail_display)
        detail_tool.addWidget(self._detail_hex_toggle)
        detail_tool.addStretch()
        detail_layout.addLayout(detail_tool)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("点击行查看请求和响应详情...")
        self._detail.setFont(QFont("Consolas", 10))
        detail_layout.addWidget(self._detail)

        hist_splitter.addWidget(self._table)
        hist_splitter.addWidget(detail_w)
        hist_splitter.setStretchFactor(0, 3)
        hist_splitter.setStretchFactor(1, 1)
        layout.addWidget(hist_splitter)

    def refresh(self):
        proto = self._proto_filter.currentData()
        self._all_sessions = self._db.get_protocol_test_sessions(proto)
        # 字段排序
        if self._sort_col >= 0:
            key_map = {
                0: lambda s: s.started_at,
                1: lambda s: (s.collection_name or "").lower(),
                2: lambda s: ("TCP" if "tcp" in s.protocol_type else "WS"),
                3: lambda s: s.target_ip,
                4: lambda s: s.target_port,
                5: lambda s: s.success,
            }
            key_fn = key_map.get(self._sort_col)
            if key_fn:
                self._all_sessions.sort(key=key_fn, reverse=not self._sort_asc)
        self._update_sort_indicator()
        self._populate_table()
        if self._search.text().strip():
            self._filter(self._search.text())
        # 数据可能已变化，重置详情显示
        self._current_session = None
        self._last_raw = b""
        self._detail.clear()

    def _on_header_clicked(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self.refresh()

    def _update_sort_indicator(self):
        headers = {0: "时间", 1: "集合", 2: "协议", 3: "目标", 4: "端口", 5: "结果"}
        for c, label in headers.items():
            item = self._table.horizontalHeaderItem(c)
            if item:
                arrow = " ▲" if (c == self._sort_col and self._sort_asc) else \
                        " ▼" if c == self._sort_col else ""
                item.setText(label + arrow)

    # ── 选择操作 ──────────────────────────────────────────

    def _select_all(self):
        self._table.selectAll()

    def _deselect_all(self):
        self._table.clearSelection()

    def _invert_selection(self):
        model = self._table.model()
        rows = self._table.rowCount()
        if rows == 0:
            return
        sm = self._table.selectionModel()
        sel_rows = set()
        for r in range(rows):
            if sm.isSelected(model.index(r, 0)):
                sel_rows.add(r)
        if not sel_rows:
            self._table.selectAll()
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        new_sel = QItemSelection()
        for r in range(rows):
            if r not in sel_rows:
                new_sel.select(model.index(r, 0), model.index(r, self._table.columnCount() - 1))
        sm.select(new_sel, QItemSelectionModel.ClearAndSelect)
        self._table.setFocus()

    # ── 删除操作 ──────────────────────────────────────────

    def _delete_selected(self):
        rows = set(i.row() for i in self._table.selectedIndexes())
        ids = [self._all_sessions[r].id for r in rows
               if r < len(self._all_sessions)]
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        r = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 条测试记录吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self._db.delete_protocol_test_sessions(ids)
        self.refresh()

    def _clear_all(self):
        r = QMessageBox.question(
            self, "确认清空",
            "确定要清空全部协议测试历史吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self._db.clear_protocol_test_sessions()
        self.refresh()

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
        refresh_tooltips(self._table)

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._table.rowCount()):
            ip = self._table.item(row, 3)
            port = self._table.item(row, 4)
            match = (ip and s in ip.text().lower()) or (port and s in port.text())
            self._table.setRowHidden(row, not match if s else False)

    def _on_cell_clicked(self, row: int, col: int):
        if row >= len(self._all_sessions):
            return
        sess = self._all_sessions[row]
        self._current_session = sess
        # 存储响应原始字节，供编码切换/十六进制显示使用
        self._last_raw = (sess.response or "").encode("utf-8", errors="replace")
        self._refresh_detail_display()

    def _on_table_menu(self, pos):
        """全局测试历史右键菜单：导出/刷新/删除/清空。"""
        item = self._table.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("导出", self._export)
        menu.addAction("刷新", self.refresh)
        if item:
            row = item.row()
            model = self._table.model()
            if not self._table.selectionModel().isSelected(model.index(row, 0)):
                self._table.selectRow(row)
            menu.addAction("删除", self._delete_selected)
        menu.addSeparator()
        menu.addAction("清空", self._clear_all)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _refresh_detail_display(self):
        """根据当前编码选择与十六进制开关刷新详情显示。"""
        sess = self._current_session
        if sess is None:
            return
        header = f"请求:\n{sess.request}\n---\n响应 ({'OK' if sess.success else 'FAIL'}):"
        if self._detail_hex_toggle.isChecked():
            raw = self._last_raw or b""
            detail = f"{header}\n{_hex_dump(raw)}"
        else:
            enc = self._detail_enc_combo.currentText()
            raw = self._last_raw or b""
            try:
                text = raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                text = raw.decode(enc, errors="replace")
            detail = f"{header}\n{text}"
        if sess.error_msg:
            detail += f"\n\n错误:\n{sess.error_msg}"
        self._detail.setPlainText(detail)

    def _export(self):
        sessions = self._all_sessions
        # 支持多选导出：选中则只导出选中行
        sel_rows = sorted(set(i.row() for i in self._table.selectedIndexes()))
        if sel_rows:
            sessions = [sessions[r] for r in sel_rows if r < len(sessions)]
        if not sessions:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        fp, sel_filter = QFileDialog.getSaveFileName(self, "导出测试历史", "protocol_history.xlsx",
                                                     "Excel (*.xlsx);;CSV (*.csv)")
        if not fp:
            return
        headers = ["测试时间", "集合", "协议", "目标IP", "端口", "结果", "请求报文", "响应报文", "错误信息"]
        rows = [
            [
                s.started_at,
                s.collection_name or "-",
                "TCP" if "tcp" in s.protocol_type else "WS",
                s.target_ip,
                s.target_port,
                "OK" if s.success else "FAIL",
                s.request or "",
                s.response or "",
                s.error_msg or "",
            ]
            for s in sessions
        ]
        is_xlsx = fp.lower().endswith(".xlsx")
        if not fp.lower().endswith((".csv", ".xlsx")):
            # 无扩展名时按所选过滤器补充
            fp += ".xlsx" if "xlsx" in sel_filter else ".csv"
            is_xlsx = fp.lower().endswith(".xlsx")
        try:
            if is_xlsx:
                from src.excel_handler import export_rows_to_excel
                ok, err = export_rows_to_excel(fp, headers, rows)
                if not ok:
                    QMessageBox.critical(self, "导出失败", err)
                    return
            else:
                import csv
                with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)
            QMessageBox.information(self, "导出完成", f"已导出 {len(sessions)} 条记录。")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F5:
            self.refresh()
        elif event.key() == Qt.Key_Delete or (event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier):
            self._delete_selected()
        else:
            super().keyPressEvent(event)


# ── 独立客户端 ──────────────────────────────────────────────


class _StandaloneClientTab(ClientPanelBase):
    """独立客户端 —— 不依赖集合/目标，快速测试连接。"""

    target_saved = Signal()  # 目标保存到集合后通知集合详情刷新
    test_finished = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(db, parent=parent)
        raw = self._db.get_setting("standalone_presets", "")
        try:
            self._presets = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            self._presets = []
        self._load_config()
        self._apply_stress_params(self._load_stress_from_store())
        self._refresh_preset_list()

    # ── 钩子：独立客户端与目标客户端的差异 ──────────────────

    def _build_action_buttons(self, proto_row):
        proto_row.addWidget(QPushButton("保存到集合", clicked=self._save_to_collection))

    def _on_ctrl_s_no_focus(self):
        self._save_to_collection()

    def _on_param_changed(self):
        self._save_config()

    def _build_client_worker(self, msg, proto):
        if proto == "tcp_client":
            return TcpClientWorker(ip=self._param_ip.text().strip(), port=self._param_port.value(),
                                   message=msg, encoding=self._param_enc.currentText(),
                                   head_len=self._param_hl.value(), timeout=self._param_timeout.value())
        return WsClientWorker(url=self._param_ws_url.text().strip(), message=msg,
                              timeout=self._param_ws_timeout.value())

    def get_presets(self):
        return self._presets

    def save_presets(self, presets):
        self._presets = presets
        self._save_presets_to_settings()

    # ── 压测参数持久化：独立客户端写入 settings 表 ────────────

    def _load_stress_from_store(self) -> dict:
        raw = self._db.get_setting("standalone_stress", "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_stress_to_store(self, sp: dict):
        self._db.set_setting("standalone_stress",
                             json.dumps(sp, ensure_ascii=False))

    # ── 配置持久化（settings 表）────────────────────────────

    def _load_config(self):
        raw = self._db.get_setting("standalone_config", "")
        if not raw:
            return
        try:
            cfg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        self.set_params(cfg)

    def _save_config(self):
        self._db.set_setting("standalone_config",
                             json.dumps(self.collect_params(), ensure_ascii=False))

    def _save_presets_to_settings(self):
        self._db.set_setting("standalone_presets",
                             json.dumps(self._presets, ensure_ascii=False))

    # ── 保存到集合 ──────────────────────────────────────────

    def _save_to_collection(self):
        """将当前独立客户端配置保存为测试集合中的一个目标。"""
        collections = self._db.get_all_protocol_collections()
        if not collections:
            QMessageBox.information(self, "提示", "请先在协议测试中创建测试集合。")
            return
        names = [c.name for c in collections]
        name, ok = QInputDialog.getItem(self, "保存到集合", "选择目标集合:", names, 0, False)
        if not ok:
            return
        coll = collections[names.index(name)]
        proto = self._proto_combo.currentData()
        ip = self._param_ip.text().strip()
        port = self._param_port.value()
        self._db.add_protocol_target(
            collection_id=coll.id,
            ip=ip, port=port,
            name=f"{ip}:{port}",
            encoding=self._param_enc.currentText(),
            recv_encoding=self._resp_enc_combo.currentText(),
            head_length=self._param_hl.value(),
            timeout=self._param_timeout.value(),
            ws_path=self._param_ws_url.text().strip(),
            ws_use_ssl=self._param_ws_ssl.isChecked(),
            send_message=self._send_edit.toPlainText() if proto == "tcp_client" else "",
            stress_params=json.dumps(self.collect_stress_params(), ensure_ascii=False),
        )
        QMessageBox.information(self, "保存完成", f"已保存到集合 [{coll.name}]")
        self.target_saved.emit()

# ── 协议测试主面板 ──────────────────────────────────────────


class ProtocolPanel(QWidget):

    test_finished = Signal()
    connectivity_test_requested = Signal(list)  # 目标 dict 列表 → 连通测试临时列表

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
        # 目标列表拖拽到集合 → 移动到该集合
        self._sidebar._tree.targets_dropped.connect(self._on_targets_dropped_to_collection)
        splitter.addWidget(self._sidebar)

        # 右侧: 功能标签页
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(False)

        # Tab 0: 集合详情（目标表格）
        self._detail_tab = _CollectionDetailTab(self._db)
        self._detail_tab.target_double_clicked.connect(self._open_target_tab)
        # 目标增删改后自动刷新集合名称后的数量
        self._detail_tab.targets_changed.connect(self._sidebar.refresh)
        # 选中目标发送到连通测试临时列表
        self._detail_tab.connectivity_test_requested.connect(
            self.connectivity_test_requested.emit
        )
        # 侧边栏右键菜单 → 集合详情操作
        self._sidebar.target_add_requested.connect(self._detail_tab._on_add_target)
        self._sidebar.target_edit_requested.connect(self._detail_tab._on_edit_target)
        self._sidebar.target_delete_requested.connect(self._detail_tab._on_delete_target)
        self._sidebar.target_select_all_requested.connect(lambda: self._detail_tab._target_table.selectAll())
        self._sidebar.target_invert_requested.connect(self._detail_tab._invert_target_selection)
        self._tabs.addTab(self._detail_tab, "集合详情")

        # Tab 1: 客户端（独立，固定）
        self._standalone_client = _StandaloneClientTab(self._db)
        self._standalone_client.target_saved.connect(self._detail_tab._refresh_targets)
        self._standalone_client.target_saved.connect(self._sidebar.refresh)
        self._standalone_client.test_finished.connect(self.test_finished.emit)
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
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 880])

        layout.addWidget(splitter)

        # 初始加载侧边栏默认选中的集合（此时信号已连接、各标签页已就绪）
        self._sidebar.refresh()

    def _on_collection_selected(self, coll_id):
        """选中集合 → 切换到集合详情并加载（不关闭已打开的目标详情标签页）。"""
        coll = self._db.get_protocol_collection(coll_id) if coll_id is not None else None
        if coll is not None:
            self._tabs.setCurrentIndex(0)
        self._detail_tab.set_collection(coll)

    def _on_targets_dropped_to_collection(self, coll_id: int, target_ids: list):
        """拖拽协议目标到集合 → 移动目标到该集合内。"""
        self._db.move_protocol_target_ids_to_collection(target_ids, coll_id)
        self._sidebar.refresh()
        self._detail_tab._refresh_targets()

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
        detail.test_finished.connect(self.test_finished.emit)
        detail.config_dirty_changed.connect(
            lambda dirty: self._tabs.setTabText(
                self._tabs.indexOf(tab_w),
                (target.name if target.name else f"{target.ip}:{target.port}") + (" *" if dirty else "")
            )
        )
        layout.addWidget(detail)

        # 详情标签页名称：描述非空用描述，否则用 ip:port
        label = target.name if target.name else f"{target.ip}:{target.port}"
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
                client = detail._client_panel
                unsaved = client.has_unsaved_presets() or (
                    client._selected_preset_idx is None
                    and client._send_edit.toPlainText().strip()
                )
                unsaved = unsaved or client._config_dirty
                if unsaved:
                    msg_parts = []
                    if client._config_dirty:
                        msg_parts.append("参数修改")
                    if client.has_unsaved_presets() or (
                        client._selected_preset_idx is None
                        and client._send_edit.toPlainText().strip()
                    ):
                        msg_parts.append("报文内容")
                    reply = QMessageBox.question(
                        self, "未保存的内容",
                        f"该目标有未保存的{'、'.join(msg_parts)}，关闭后将会丢失。\n是否保存后再关闭？",
                        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.Cancel:
                        return
                    if reply == QMessageBox.Yes:
                        client.save_all_drafts()
                        if client._config_dirty:
                            detail._save_params()
                detail.stop_all_servers()
                del self._target_tabs[tid]
                break
        self._tabs.removeTab(idx)
        # 关闭后切回集合详情
        self._tabs.setCurrentIndex(0)

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

    def _all_client_panels(self) -> list:
        """独立客户端 + 所有已打开目标详情的客户端面板。"""
        panels = [self._standalone_client]
        panels += [detail._client_panel for _, detail in self._target_tabs.values()]
        return panels

    def has_unsaved_presets(self) -> bool:
        """是否有任一客户端面板存在未保存的预设报文修改。"""
        return any(p.has_unsaved_presets() for p in self._all_client_panels())

    def save_unsaved_presets(self) -> None:
        """保存所有客户端面板的未保存预设报文。"""
        for p in self._all_client_panels():
            p.save_all_drafts()

    def prefill_client_target(self, ip: str, port: int) -> None:
        """从连通测试跳转：切换到客户端标签页并预填 IP/端口。"""
        # 找到客户端标签页索引并切换
        for i in range(self._tabs.count()):
            if self._tabs.widget(i) == self._standalone_client:
                self._tabs.setCurrentIndex(i)
                break
        self._standalone_client.prefill(ip, port)
