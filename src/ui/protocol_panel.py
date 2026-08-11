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

from PySide6.QtCore import Qt, QSettings, QTimer, Signal
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
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import Database, target_display_info
from src.protocol import compute_length_header
from src.ui import shortcuts
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
    """根据目标参数推断实际协议类型。优先检查 send_presets 中的协议。"""
    # 优先从 send_presets 推断
    try:
        sp = json.loads(target.send_presets) if target.send_presets else {}
    except (json.JSONDecodeError, TypeError):
        sp = {}
    if isinstance(sp, dict):
        keys = [k for k in sp if sp[k]]  # 有预设的协议
        if "http_client" in keys:
            return "HTTP"
        if "ws_client" in keys and "tcp_client" not in keys:
            return "WS"
        if keys:
            return "TCP"  # 包含 tcp_client 或混合
    # 回退：从 display_info 推断
    info = target_display_info(target)
    proto = info.get("proto", "tcp_client")
    if proto == "http_client":
        return "HTTP"
    elif proto == "ws_client":
        return "WS"
    return "TCP"


def _normalize_import_presets(t: dict) -> str:
    """将导入数据中的预设规范化为 {proto: [...]} JSON 字符串。

    兼容旧格式（扁平列表）和新格式（dict）。若数据中只有旧字段（ip/port 等），
    自动构建"默认配置"预设。
    """
    raw = t.get("send_presets", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if isinstance(raw, dict):
        # 已是新格式 — 确保有 _active_proto
        if "_active_proto" not in raw and raw:
            first = next(iter(raw))
            if first != "_active_proto":
                raw["_active_proto"] = first
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, list) and raw:
        # 旧格式扁平列表：包装为 tcp_client（或从 protocol_type 推断）
        proto = t.get("protocol_type", "tcp_client")
        return json.dumps({"_active_proto": proto, proto: raw}, ensure_ascii=False)
    # 无预设：从旧字段构建默认配置
    proto = t.get("protocol_type", "tcp_client")
    if proto == "http_client":
        cfg = {"method": t.get("http_method", "GET"), "url": t.get("url", ""),
               "proto": proto}
    else:
        cfg = {
            "proto": proto, "ip": t.get("ip", ""), "port": t.get("port", 0),
            "encoding": t.get("encoding", "UTF-8"),
            "recv_encoding": t.get("recv_encoding", "UTF-8"),
            "head_length": t.get("head_length", 5),
            "timeout": t.get("timeout", 30.0),
            "ws_url": t.get("ws_path", ""),
            "ws_ssl": t.get("ws_use_ssl", False),
            "send_message": t.get("send_message", ""),
        }
    return json.dumps(
        {"_active_proto": proto,
         proto: [{"name": "默认配置", "message": json.dumps(cfg, ensure_ascii=False)}]},
        ensure_ascii=False)


def _slot(fn, *args):
    def handler(*_sig_args):
        fn(*args)
    return handler


# ── 对话框 ──────────────────────────────────────────────────

class _TargetDialog(QDialog):
    """添加/编辑协议目标对话框 —— 按协议类型显示不同字段。"""

    def __init__(self, title: str, db: Database,
                 default_collection_id: int | None = None,
                 ip: str = "", port: int = 80,
                 name: str = "", encoding: str = "UTF-8",
                 recv_encoding: str = "UTF-8", head_length: int = 5,
                 timeout: float = 30.0, ws_path: str = "",
                 ws_use_ssl: bool = False, url: str = "",
                 http_method: str = "GET",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QFormLayout(self)

        # 推断当前协议类型
        if url:
            self._proto_type = "http_client"
        elif ws_path.startswith("ws"):
            self._proto_type = "ws_client"
        else:
            self._proto_type = "tcp_client"

        # ── 协议选择 ──
        self._proto_combo = QComboBox()
        self._proto_combo.addItem("TCP", "tcp_client")
        self._proto_combo.addItem("WebSocket", "ws_client")
        self._proto_combo.addItem("HTTP", "http_client")
        idx = self._proto_combo.findData(self._proto_type)
        if idx >= 0:
            self._proto_combo.setCurrentIndex(idx)
        self._proto_combo.currentIndexChanged.connect(self._on_proto_changed)
        layout.addRow("协议:", self._proto_combo)

        # 名称
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("目标名称")
        layout.addRow("名称:", self._name)

        # ── 协议参数栈 ──
        self._proto_stack = QStackedWidget()

        # TCP 页
        tcp_w = QWidget()
        tcp_f = QFormLayout(tcp_w)
        self._ip = QLineEdit(ip)
        self._ip.setPlaceholderText("192.168.1.1")
        tcp_f.addRow("IP:", self._ip)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        tcp_f.addRow("端口:", self._port)
        self._enc = QComboBox()
        self._enc.addItems(ENCODINGS)
        self._enc.setEditable(True)
        self._enc.setCurrentText(encoding)
        tcp_f.addRow("发送编码:", self._enc)
        self._recv_enc = QComboBox()
        self._recv_enc.addItems(ENCODINGS)
        self._recv_enc.setEditable(True)
        self._recv_enc.setCurrentText(recv_encoding)
        tcp_f.addRow("接收编码:", self._recv_enc)
        self._hl = QSpinBox()
        self._hl.setRange(0, 20)
        self._hl.setValue(head_length)
        self._hl.setSuffix("位")
        tcp_f.addRow("头长度:", self._hl)
        self._timeout = QDoubleSpinBox()
        self._timeout.setRange(0.1, 60)
        self._timeout.setValue(timeout)
        self._timeout.setSingleStep(0.5)
        self._timeout.setSuffix("s")
        tcp_f.addRow("超时:", self._timeout)
        self._proto_stack.addWidget(tcp_w)  # 0

        # WebSocket 页
        ws_w = QWidget()
        ws_f = QFormLayout(ws_w)
        self._ws_url = QLineEdit(ws_path or "ws://127.0.0.1:80/ws")
        self._ws_url.setPlaceholderText("ws://127.0.0.1:80/ws")
        ws_f.addRow("URL:", self._ws_url)
        self._ws_ssl = QCheckBox("SSL")
        self._ws_ssl.setChecked(ws_use_ssl)
        ws_f.addRow("SSL:", self._ws_ssl)
        self._ws_timeout = QDoubleSpinBox()
        self._ws_timeout.setRange(0.1, 60)
        self._ws_timeout.setValue(timeout)
        self._ws_timeout.setSingleStep(0.5)
        self._ws_timeout.setSuffix("s")
        ws_f.addRow("超时:", self._ws_timeout)
        self._proto_stack.addWidget(ws_w)  # 1

        # HTTP 页
        http_w = QWidget()
        http_f = QFormLayout(http_w)
        self._http_method = QComboBox()
        self._http_method.addItems(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
        self._http_method.setEditable(True)
        midx = self._http_method.findText(http_method.upper())
        if midx >= 0:
            self._http_method.setCurrentIndex(midx)
        else:
            self._http_method.setCurrentText(http_method.upper() or "GET")
        http_f.addRow("Method:", self._http_method)
        self._http_url = QLineEdit(url)
        self._http_url.setPlaceholderText("http://example.com/api")
        http_f.addRow("URL:", self._http_url)
        self._proto_stack.addWidget(http_w)  # 2

        layout.addRow(self._proto_stack)

        # 集合选择
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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # 初始状态
        self._on_proto_changed()

    def _on_proto_changed(self):
        """切换协议时显示对应的参数页。"""
        proto = self._proto_combo.currentData()
        if proto == "tcp_client":
            self._proto_stack.setCurrentIndex(0)
        elif proto == "ws_client":
            self._proto_stack.setCurrentIndex(1)
        else:
            self._proto_stack.setCurrentIndex(2)

    def _validate(self):
        import re
        proto = self._proto_combo.currentData()
        if proto == "tcp_client":
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
                QMessageBox.warning(self, "验证失败", "IP 地址超出范围，每段取值范围为 0-255。")
                return
            if self._port.value() < 1 or self._port.value() > 65535:
                QMessageBox.warning(self, "验证失败", "端口号超出范围，有效范围为 1-65535。")
                return
        elif proto == "ws_client":
            url = self._ws_url.text().strip()
            if not url:
                QMessageBox.warning(self, "验证失败", "WebSocket URL 不能为空。")
                return
            if not url.startswith(("ws://", "wss://")):
                QMessageBox.warning(self, "验证失败", "WebSocket URL 必须以 ws:// 或 wss:// 开头。")
                return
        else:  # http_client
            url = self._http_url.text().strip()
            if not url:
                QMessageBox.warning(self, "验证失败", "HTTP URL 不能为空。")
                return
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
                self._http_url.setText(url)
        self.accept()

    @property
    def protocol_type(self) -> str:
        """当前选择的协议类型。"""
        return self._proto_combo.currentData()

    def get_data(self) -> dict:
        """根据协议类型返回对应字段。"""
        proto = self._proto_combo.currentData()
        result = dict(
            name=self._name.text().strip(),
            proto=proto,
        )
        if proto == "tcp_client":
            result.update(
                ip=self._ip.text().strip(),
                port=self._port.value(),
                encoding=self._enc.currentText(),
                recv_encoding=self._recv_enc.currentText(),
                head_length=self._hl.value(),
                timeout=self._timeout.value(),
                ws_path="",
                ws_use_ssl=False,
            )
        elif proto == "ws_client":
            result.update(
                ip="0.0.0.0",
                port=0,
                encoding="UTF-8",
                recv_encoding="UTF-8",
                head_length=0,
                ws_path=self._ws_url.text().strip(),
                ws_use_ssl=self._ws_ssl.isChecked(),
                timeout=self._ws_timeout.value(),
            )
        else:  # http_client
            result.update(
                ip="0.0.0.0",
                port=80,
                encoding="UTF-8",
                recv_encoding="UTF-8",
                head_length=0,
                timeout=30.0,
                ws_path=self._http_url.text().strip(),
                ws_use_ssl=False,
                url=self._http_url.text().strip(),
                http_method=self._http_method.currentText().strip().upper() or "GET",
            )
        return result

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
                collection_id=new_cid, name=t.name,
                send_presets=t.send_presets,
                stress_params=t.stress_params,
            )
            for s in self._db.get_protocol_servers_by_target(t.id):
                self._db.add_protocol_server(
                    name=s.name, server_type=s.server_type,
                    ip=s.ip, port=s.port, encoding=s.encoding,
                    recv_encoding=s.recv_encoding, head_length=s.head_length,
                    ws_path=s.ws_path, response_mode=s.response_mode,
                    response_message=s.response_message,
                    response_messages=s.response_messages or None,
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
                    presets = _normalize_import_presets(t)
                    stress = json.dumps(t.get("stress_params", {}), ensure_ascii=False)
                    tid = self._db.add_protocol_target(
                        collection_id=cid, name=t.get("name", ""),
                        send_presets=presets,
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
                            response_messages=s.get("response_messages") or None,
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
                    presets = json.loads(t.send_presets) if t.send_presets else {}
                except json.JSONDecodeError:
                    presets = {}
                try:
                    stress = json.loads(t.stress_params) if t.stress_params else {}
                except (json.JSONDecodeError, TypeError):
                    stress = {}
                info = target_display_info(t)
                targets_data.append({
                    "name": t.name,
                    "send_presets": presets, "stress_params": stress,
                    # 兼容旧格式：冗余字段
                    "ip": info["ip"], "port": info["port"],
                    "encoding": info["encoding"], "recv_encoding": info["recv_encoding"],
                    "head_length": info["head_length"],
                    "timeout": info["timeout"], "ws_path": info.get("ws_url", ""),
                    "ws_use_ssl": info.get("ws_ssl", False),
                    "send_message": info.get("send_message", ""),
                    "url": info.get("url", ""),
                    "http_config": {},
                    "servers": [{"name": s.name, "server_type": s.server_type,
                                 "ip": s.ip, "port": s.port, "encoding": s.encoding,
                                 "recv_encoding": s.recv_encoding,
                                 "head_length": s.head_length, "ws_path": s.ws_path,
                                 "response_mode": s.response_mode,
                                 "response_message": s.response_message,
                                 "response_messages": s.response_messages or "",
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
        self._prev_proto = "tcp_client"  # 协议切换时跟踪上一个协议
        super().__init__(owner._db, parent=owner, show_len_label=True)

    # ── 钩子 ────────────────────────────────────────────────

    def _build_action_buttons(self, proto_row):
        proto_row.addWidget(QPushButton("导出配置", clicked=self._owner._export_target))
        proto_row.addWidget(QPushButton("导入配置", clicked=self._owner._import_target_config))
        # 服务端展开/收起按钮
        self._server_toggle_btn = QPushButton("服务端")
        self._server_toggle_btn.setCheckable(True)
        self._server_toggle_btn.clicked.connect(self._owner._toggle_server_panel)
        proto_row.addWidget(self._server_toggle_btn)

    def _on_param_changed(self):
        # 配置通过预设持久化，不标记 config_dirty（由 _mark_msg_dirty 处理）
        pass

    def _on_ctrl_s_no_focus(self):
        """Ctrl+S 无焦点时：保存当前配置到默认预设。"""
        self._save_as_default_preset()

    def _on_proto_changed(self, idx: int):
        """协议切换：保存旧协议配置到旧协议默认预设，再加载新协议已有默认配置。

        旧实现会把当前参数误存进「新协议」的默认配置（随后又被 _update_active_proto
        用过期内存数据还原，面板却显示默认值），这里改为：旧协议草稿/参数写回旧协议，
        再加载新协议已存在的默认配置，没有才从当前参数新建。
        """
        new_proto = self._proto_combo.currentData()
        if new_proto == self._prev_proto:
            ClientPanelBase._on_proto_changed(self, idx)
            return
        old_proto = self._prev_proto
        # 1) 把旧协议下已缓存的未保存草稿写回旧协议预设（索引基于旧协议列表）
        if self._dirty:
            self._flush_drafts(old_proto)
        # 2) 把当前（旧协议）参数存入旧协议"默认配置"预设
        if self._owner._target:
            self._save_as_default_preset(proto=old_proto)
        # 3) 切换 UI
        self._prev_proto = new_proto
        ClientPanelBase._on_proto_changed(self, idx)
        # 4) 重置草稿/脏状态，加载新协议已有默认配置（没有则从当前参数创建）
        self._drafts.clear()
        self._dirty.clear()
        self._selected_preset_idx = None
        self._msg_dirty = False
        if self._owner._target:
            self._load_active_proto_default()
            # 更新 _active_proto，确保下次加载按最近使用的协议展示
            self._owner._update_active_proto(new_proto)
            self._msg_dirty = False
        self._refresh_preset_list()
        self._update_send_label()

    def _can_send(self) -> bool:
        return bool(self._owner._target)

    def _can_edit_presets(self) -> bool:
        return bool(self._owner._target)

    def get_presets(self, proto: str = ""):
        t = self._owner._target
        if not t:
            return []
        proto = proto or self._proto_combo.currentData()
        return self._owner._load_presets(t.send_presets, proto)

    def save_presets(self, presets, proto: str = ""):
        proto = proto or self._proto_combo.currentData()
        self._owner._save_presets_to_target(presets, proto)

    def _ensure_default_preset(self):
        """确保当前协议下存在"默认配置"预设；当前协议完全没有预设时才从当前参数创建。

        若已有用户自建的预设（如改名的"配置1"），不再强行追加"默认配置"，
        避免每次重新打开详情 tab 都在预设列表尾部多出一条。
        """
        presets = list(self.get_presets())
        default_name = "默认配置"
        existing = next((p for p in presets if p.get("name") == default_name), None)
        if existing is None and not presets:
            proto = self._proto_combo.currentData()
            if proto == "http_client":
                config = json.dumps(self._http_params.get_config(), ensure_ascii=False)
            else:
                config = json.dumps(self.collect_params(), ensure_ascii=False)
            presets.append({"name": default_name, "message": config})
            self.save_presets(presets)
            self._refresh_preset_list()

    def _collect_params_for(self, proto: str) -> dict:
        """按目标协议收集参数（协议切换时组合框已指向新协议，不能依赖 currentData）。"""
        if proto == "http_client":
            cfg = self._http_params.get_config()
            cfg["proto"] = proto
            return cfg
        cfg = {
            "proto": proto,
            "ip": self._param_ip.text().strip(),
            "port": self._param_port.value(),
            "encoding": self._param_enc.currentText(),
            "recv_encoding": self._resp_enc_combo.currentText(),
            "head_length": self._param_hl.value(),
            "timeout": self._param_timeout.value(),
            "ws_url": self._param_ws_url.text().strip(),
            "ws_timeout": self._param_ws_timeout.value(),
            "ws_ssl": self._param_ws_ssl.isChecked(),
            "send_message": self._send_edit.toPlainText(),
        }
        return cfg

    def _save_as_default_preset(self, proto: str = ""):
        """将当前参数保存到指定协议（默认当前协议）的"默认配置"预设。

        有"默认配置"则覆盖它；没有"默认配置"但有用户预设时，覆盖当前选中的预设
        （无选中则覆盖第一条），避免切换协议/关闭时也强行追加"默认配置"；
        完全没有预设才新建"默认配置"。
        """
        default_name = "默认配置"
        target_proto = proto or self._proto_combo.currentData()
        presets = list(self.get_presets(proto=target_proto))
        config = json.dumps(self._collect_params_for(target_proto), ensure_ascii=False)
        for p in presets:
            if p.get("name") == default_name:
                p["message"] = config
                self.save_presets(presets, proto=target_proto)
                self._refresh_preset_list()
                return
        if presets:
            # 无"默认配置"但有用户预设：覆盖当前选中预设，无选中则覆盖第一条
            idx = (self._selected_preset_idx
                   if self._selected_preset_idx is not None
                   and 0 <= self._selected_preset_idx < len(presets) else 0)
            presets[idx]["message"] = config
            self.save_presets(presets, proto=target_proto)
            self._refresh_preset_list()
            return
        # 完全没有预设才新建
        presets.append({"name": default_name, "message": config})
        self.save_presets(presets, proto=target_proto)
        self._refresh_preset_list()

    def _flush_drafts(self, proto: str):
        """把未保存草稿按指定协议列表写回（协议切换时使用，索引基于该协议列表）。"""
        if not self._dirty:
            return
        presets = list(self.get_presets(proto=proto))
        changed = False
        for idx in list(self._dirty):
            if 0 <= idx < len(presets) and idx in self._drafts:
                presets[idx]["message"] = self._drafts[idx]
                changed = True
        if changed:
            self.save_presets(presets, proto=proto)
        self._dirty.clear()
        self._drafts.clear()
        self._msg_dirty = False

    def _load_active_proto_default(self):
        """加载当前协议的"默认配置"到面板；没有"默认配置"则优先选中第一个用户预设，
        完全没有预设才从当前参数创建默认预设。"""
        presets = list(self.get_presets())
        default_name = "默认配置"
        idx = next((i for i, p in enumerate(presets)
                    if p.get("name") == default_name), None)
        if idx is not None:
            self._selected_preset_idx = idx
            self._load_preset_config(presets[idx].get("message", ""))
            self._preset_selected_label.setText("✓ 已选择: 默认配置")
        elif presets:
            # 无"默认配置"但有用户预设：选中第一个（与 target_display_info 展示兜底一致）
            self._selected_preset_idx = 0
            self._load_preset_config(presets[0].get("message", ""))
            self._preset_selected_label.setText(f"✓ 已选择: {presets[0].get('name', '')}")
        else:
            self._selected_preset_idx = None
            self._ensure_default_preset()

    def _load_preset_config(self, msg: str):
        """把预设 message 应用到面板参数（与 _on_preset_clicked 相同逻辑）。"""
        proto = self._proto_combo.currentData()
        if proto == "http_client":
            try:
                config = json.loads(msg)
            except json.JSONDecodeError:
                config = {}
            self._http_params.set_config(config)
            return
        try:
            config = json.loads(msg)
            if isinstance(config, dict) and "proto" in config:
                self.set_params(config)  # 新格式：完整配置
            else:
                # JSON 解析成功但不是配置 dict（例如纯 JSON 报文）
                self._send_edit.setPlainText(msg)
        except json.JSONDecodeError:
            # 旧格式：纯文本报文
            self._send_edit.setPlainText(msg)

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
            proto = self._proto_combo.currentData()
            if proto == "http_client":
                protocol_type = "http_client"
                url = self._http_params.get_url() if self._http_params else ""
                ip, port = url, 0
            else:
                protocol_type = owner._coll.protocol_type
                ip = self._param_ip.text().strip()
                port = self._param_port.value()
            owner._db.add_protocol_test_session(
                collection_id=owner._coll.id, collection_name=owner._coll.name,
                target_id=owner._target.id, protocol_type=protocol_type,
                target_ip=ip,
                target_port=port,
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
        # 从预设中提取配置信息
        info = target_display_info(target)
        proto = info.get("proto", "tcp_client")
        label = _target_proto_label(target)
        if proto == "http_client":
            cfg = {"proto": "http_client"}
            # 尝试从预设中获取完整 HTTP 配置
            try:
                all_p = json.loads(target.send_presets) if target.send_presets else {}
            except json.JSONDecodeError:
                all_p = {}
            if isinstance(all_p, dict):
                http_presets = all_p.get("http_client", [])
                default = next((p for p in http_presets if p.get("name") == "默认配置"), None)
                if default:
                    try:
                        cfg = json.loads(default.get("message", "{}"))
                    except json.JSONDecodeError:
                        pass
            if "url" not in cfg:
                cfg["url"] = info.get("url", "")
            if "method" not in cfg:
                cfg["method"] = info.get("http_method", "GET")
            cfg["proto"] = "http_client"
        else:
            cfg = {
                "proto": proto,
                "ip": info["ip"], "port": info["port"],
                "encoding": info["encoding"], "recv_encoding": info["recv_encoding"],
                "head_length": info["head_length"], "timeout": info["timeout"],
                "ws_url": info.get("ws_url", "") or "ws://127.0.0.1:80/ws",
                "ws_timeout": info.get("ws_timeout", info["timeout"]),
                "ws_ssl": info.get("ws_ssl", False),
                "send_message": info.get("send_message", ""),
            }
        self.set_params(cfg)
        self._apply_stress_params(self._load_stress_from_store())
        self._send_edit.setPlainText(info.get("send_message", ""))
        self._update_len_label()
        # 同步 _prev_proto 以正确跟踪协议切换
        self._prev_proto = cfg.get("proto", "tcp_client")
        self._refresh_preset_list()
        # 确保当前协议存在"默认配置"预设
        self._ensure_default_preset()


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

    def _row_cells(self, s):
        if s.server_type == "http_server":
            return [s.name, s.ip, str(s.port), "-", "-", "-", "固定",
                    str(s.response_delay)]
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

    def _load_presets(self, presets_json: str, proto: str = ""):
        """按协议提取预设列表。新格式为 {proto: [...]}，旧格式为 [...] 直接返回。"""
        try:
            data = json.loads(presets_json) if presets_json else []
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            # 旧格式：扁平列表，按原样返回（首次保存时会迁移为新格式）
            return data
        if isinstance(data, dict):
            return data.get(proto, [])
        return []

    def _save_presets_to_target(self, presets: list, proto: str = ""):
        """按协议分组保存预设（新格式：{proto: [...]}）。"""
        if not self._target:
            return
        # 读取现有预设，兼容旧格式（扁平列表）
        try:
            all_presets = json.loads(self._target.send_presets) if self._target.send_presets else {}
        except json.JSONDecodeError:
            all_presets = {}
        if isinstance(all_presets, list):
            # 迁移旧格式 → 新格式：旧列表归入当前协议
            all_presets = {proto: all_presets} if proto else {}
        if proto:
            all_presets[proto] = presets
            all_presets["_active_proto"] = proto
        presets_json = json.dumps(all_presets, ensure_ascii=False)
        self._db.update_protocol_target(
            self._target.id,
            name=self._target.name,
            send_presets=presets_json,
        )
        # 同步内存对象，避免后续读取/回写使用过期数据覆盖本次写入
        self._target.send_presets = presets_json

    def _update_active_proto(self, proto: str):
        """更新 presets JSON 中的 _active_proto 提示，不改变预设内容。"""
        if not self._target:
            return
        try:
            all_presets = json.loads(self._target.send_presets) if self._target.send_presets else {}
        except json.JSONDecodeError:
            all_presets = {}
        if isinstance(all_presets, list):
            all_presets = {proto: all_presets} if proto else {}
        if isinstance(all_presets, dict) and all_presets.get("_active_proto") != proto:
            all_presets["_active_proto"] = proto
            presets_json = json.dumps(all_presets, ensure_ascii=False)
            self._db.update_protocol_target(
                self._target.id,
                name=self._target.name,
                send_presets=presets_json,
            )
            self._target.send_presets = presets_json

    def _on_presets_saved(self):
        if self._target:
            self._target = self._db.get_protocol_target(self._target.id)
            self.target_updated.emit()

    # ── 保存参数 / 导出 / 导入 ──────────────────────────────

    def _export_target(self):
        if not self._target:
            return
        t = self._target
        servers = self._db.get_protocol_servers_by_target(t.id)
        try:
            presets = json.loads(t.send_presets) if t.send_presets else {}
        except json.JSONDecodeError:
            presets = {}
        try:
            stress = json.loads(t.stress_params) if t.stress_params else {}
        except (json.JSONDecodeError, TypeError):
            stress = {}
        info = target_display_info(t)
        proto = info.get("proto", "tcp_client")
        label = _target_proto_label(t)
        data = {
            "version": 1, "type": "protocol_client_config",
            "protocol_type": proto,
            "ip": info["ip"], "port": info["port"],
            "encoding": info["encoding"], "recv_encoding": info["recv_encoding"],
            "head_length": info["head_length"], "timeout": info["timeout"],
            "ws_url": info.get("ws_url", ""), "ws_use_ssl": info.get("ws_ssl", False),
            "send_message": info.get("send_message", ""),
            "send_presets": presets, "stress_params": stress,
            "servers": [{"name": s.name, "server_type": s.server_type,
                         "ip": s.ip, "port": s.port, "encoding": s.encoding,
                         "recv_encoding": s.recv_encoding,
                         "head_length": s.head_length, "ws_path": s.ws_path,
                         "response_mode": s.response_mode,
                         "response_message": s.response_message,
                         "response_messages": s.response_messages or "",
                         "response_delay": s.response_delay} for s in servers],
        }
        if label == "HTTP":
            data["protocol_type"] = "http_client"
            data["http_config"] = {}
            if proto == "http_client":
                try:
                    all_p = json.loads(t.send_presets) if t.send_presets else {}
                except json.JSONDecodeError:
                    all_p = {}
                http_presets = all_p.get("http_client", []) if isinstance(all_p, dict) else []
                default = next((p for p in http_presets if p.get("name") == "默认配置"), None)
                if default:
                    try:
                        data["http_config"] = json.loads(default.get("message", "{}"))
                    except json.JSONDecodeError:
                        pass
            data["http_url"] = info.get("url", "")
        filepath, _ = QFileDialog.getSaveFileName(self, "导出目标",
                                                   f"{info['ip']}_{info['port']}.json",
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
        # 只导入预设和压测参数（配置全部在预设中）
        presets = cfg.get("send_presets", [])
        if isinstance(presets, list):
            # 旧格式：扁平列表 → 包装为当前协议
            proto = cfg.get("protocol_type", "tcp_client")
            presets = {"_active_proto": proto, proto: presets}
        elif isinstance(presets, dict) and "_active_proto" not in presets:
            from src.database import target_display_info
            presets["_active_proto"] = cfg.get("protocol_type", "tcp_client")
        self._db.update_protocol_target(
            self._target.id,
            send_presets=json.dumps(presets, ensure_ascii=False),
            stress_params=json.dumps(cfg.get("stress_params", {}), ensure_ascii=False),
        )
        self._target = self._db.get_protocol_target(self._target.id)
        self.target_updated.emit()
        # 重新加载目标（会加载默认配置预设）
        self._client_panel.load_target(self._target)
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
                "HTTP" if "http" in s.protocol_type else ("WS" if "ws" in s.protocol_type else "TCP"),
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
        # 左右并排下：客户端 / Mock服务端 各自的 keyPressEvent 会自行处理刷新/删除，
        # 此处按焦点所在面板分发，未聚焦子面板时归到测试历史。
        if shortcuts.event_matches(event, "refresh"):
            if self._focus_in(self._client_panel):
                self._client_panel._refresh_preset_list()
            elif self._focus_in(self._server_panel):
                self._server_panel.refresh()
            else:
                self._refresh_history()
        elif shortcuts.event_matches(event, "delete"):
            if self._focus_in(self._client_panel):
                self._client_panel._delete_preset()
            elif self._focus_in(self._server_panel):
                self._server_panel._delete_selected_servers()
            else:
                self._delete_hist_sessions()
        elif shortcuts.event_matches(event, "save"):
            cp = self._client_panel
            if cp._selected_preset_idx is not None:
                cp._save_preset()
            elif cp._send_edit.hasFocus():
                cp._save_preset()
            else:
                fw = QApplication.focusWidget()
                if cp._proto_combo.currentData() == "http_client" \
                        and fw is not None \
                        and cp._http_params.isAncestorOf(fw):
                    cp._save_preset()
                else:
                    cp._save_as_default_preset()
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
        sel_bar.addWidget(QPushButton("全选", clicked=lambda *_: self._target_table.selectAll()))
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
        self._target_table.horizontalHeader().sectionResized.connect(self._save_target_column_widths)
        layout.addWidget(self._target_table)

        tbl = QHBoxLayout()
        tbl.addWidget(QPushButton("添加", clicked=self._on_add_target))
        tbl.addWidget(QPushButton("编辑", clicked=self._on_edit_target))
        tbl.addWidget(QPushButton("删除", clicked=self._on_delete_target))
        tbl.addWidget(QPushButton("复制", clicked=self._copy_target))
        proto_test_btn = QPushButton("协议测试", clicked=self._on_test_target)
        proto_test_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #8e44ad; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        tbl.addWidget(proto_test_btn)
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
        # 预先解析 display_info 以便搜索/排序
        target_infos = [(t, target_display_info(t)) for t in targets]
        # 搜索过滤
        search = self._target_search.text().strip().lower()
        if search:
            target_infos = [
                (t, info) for t, info in target_infos
                if search in info["ip"].lower() or
                   search in str(info["port"]) or
                   search in (t.name or "").lower() or
                   search in info["encoding"].lower() or
                   search in _target_proto_label(t).lower()
            ]
        # 排序
        if self._target_sort_col >= 0:
            key_map = {
                0: lambda ti: (ti[0].name or "").lower(),
                1: lambda ti: tuple(int(o) if o.strip().isdigit() else -1
                                    for o in ti[1]["ip"].split(".")),
                2: lambda ti: ti[1]["port"],
                3: lambda ti: ti[1]["encoding"].lower(),
                4: lambda ti: ti[1]["recv_encoding"].lower(),
                5: lambda ti: ti[1]["head_length"],
                6: lambda ti: ti[1]["timeout"],
                7: lambda ti: _target_proto_label(ti[0]),
            }
            key_fn = key_map.get(self._target_sort_col)
            if key_fn:
                target_infos.sort(key=key_fn, reverse=not self._target_sort_asc)
        self._target_count_label.setText(f"<b>目标列表</b> ({len(target_infos)})")
        t = self._target_table
        t.setColumnCount(8)
        t.setHorizontalHeaderLabels(["名称", "IP", "端口", "发送编码", "接收编码", "HeadLen", "超时", "类型"])
        self._update_target_sort_indicator()

        t.setRowCount(len(target_infos))
        for row, (target, info) in enumerate(target_infos):
            name_item = QTableWidgetItem(target.name or "")
            name_item.setData(Qt.UserRole, target.id)
            t.setItem(row, 0, name_item)
            t.setItem(row, 1, QTableWidgetItem(info["ip"]))
            pi = QTableWidgetItem(str(info["port"])); pi.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 2, pi)
            t.setItem(row, 3, QTableWidgetItem(info["encoding"]))
            t.setItem(row, 4, QTableWidgetItem(info["recv_encoding"]))
            t.setItem(row, 5, QTableWidgetItem(str(info["head_length"])))
            ti_w = QTableWidgetItem(f"{info['timeout']}s"); ti_w.setTextAlignment(Qt.AlignCenter)
            t.setItem(row, 6, ti_w)
            t.setItem(row, 7, QTableWidgetItem(_target_proto_label(target)))
        refresh_tooltips(t)
        self._restore_target_column_widths()

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

    # ── 列宽持久化 ──────────────────────────────────────

    # 协议测试目标表格默认列宽（比连通测试紧凑）
    _DEFAULT_TARGET_COL_WIDTHS = {
        0: 120,  # 名称
        1: 100,  # IP
        2: 50,   # 端口
        3: 65,   # 发送编码
        4: 65,   # 接收编码
        5: 55,   # HeadLen
        6: 50,   # 超时
        7: 60,   # 类型
    }

    def _save_target_column_widths(self):
        """保存用户调整后的列宽到 QSettings。"""
        settings = QSettings("TestTool", "TestTool")
        t = self._target_table
        for col in range(t.columnCount()):
            settings.setValue(f"proto_target_col_{col}", t.columnWidth(col))

    def _restore_target_column_widths(self):
        """从 QSettings 恢复列宽，首次运行时使用默认紧凑列宽。"""
        settings = QSettings("TestTool", "TestTool")
        t = self._target_table
        hh = t.horizontalHeader()
        for col in range(t.columnCount()):
            saved = settings.value(f"proto_target_col_{col}")
            if saved is not None:
                hh.resizeSection(col, int(saved))
            elif col in self._DEFAULT_TARGET_COL_WIDTHS:
                hh.resizeSection(col, self._DEFAULT_TARGET_COL_WIDTHS[col])

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
            d = dlg.get_data()
            proto = d.pop("proto", "tcp_client")
            name = d.pop("name", "")
            url = d.pop("url", "")
            http_method = d.pop("http_method", "GET")

            # 构建默认预设配置
            if proto == "http_client":
                cfg = {"method": http_method, "url": url, "proto": proto}
                preset_msg = json.dumps(cfg, ensure_ascii=False)
            else:
                cfg = d
                cfg["proto"] = proto
                preset_msg = json.dumps(cfg, ensure_ascii=False)

            send_presets = json.dumps(
                {"_active_proto": proto,
                 proto: [{"name": "默认配置", "message": preset_msg}]},
                ensure_ascii=False)

            self._db.add_protocol_target(
                collection_id=cid, name=name,
                send_presets=send_presets,
            )
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
        # 从发送预设中提取当前协议和配置
        info = target_display_info(t)
        proto = info.get("proto", "tcp_client")
        http_method = info.get("http_method", "GET")

        dlg = _TargetDialog("编辑目标", self._db, default_collection_id=t.collection_id,
                            ip=info["ip"], port=info["port"], name=t.name,
                            encoding=info["encoding"], recv_encoding=info["recv_encoding"],
                            head_length=info["head_length"], timeout=info["timeout"],
                            ws_path=info.get("ws_url", ""), ws_use_ssl=info.get("ws_ssl", False),
                            url=info.get("url", ""), http_method=http_method, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            new_proto = d.pop("proto", "tcp_client")
            new_name = d.pop("name", "")
            url = d.pop("url", "")
            http_method = d.pop("http_method", "GET")

            # 保留现有预设结构，更新默认配置
            try:
                all_presets = json.loads(t.send_presets) if t.send_presets else {}
            except json.JSONDecodeError:
                all_presets = {}
            if not isinstance(all_presets, dict):
                # 旧格式迁移
                all_presets = {proto: all_presets} if isinstance(all_presets, list) else {}

            # 构建新默认配置
            if new_proto == "http_client":
                cfg = {"method": http_method, "url": url, "proto": new_proto}
            else:
                cfg = d
                cfg["proto"] = new_proto
            preset_msg = json.dumps(cfg, ensure_ascii=False)

            # 协议变更：清空旧协议预设，为新协议创建
            if new_proto != proto:
                all_presets = {"_active_proto": new_proto,
                               new_proto: [{"name": "默认配置", "message": preset_msg}]}
            else:
                # 同协议：更新"默认配置"预设
                proto_presets = all_presets.get(new_proto, [])
                if not isinstance(proto_presets, list):
                    proto_presets = []
                updated = False
                for p in proto_presets:
                    if p.get("name") == "默认配置":
                        p["message"] = preset_msg
                        updated = True
                        break
                if not updated:
                    proto_presets.insert(0, {"name": "默认配置", "message": preset_msg})
                all_presets[new_proto] = proto_presets
                all_presets["_active_proto"] = new_proto

            self._db.update_protocol_target(
                target_id=tid, name=new_name,
                send_presets=json.dumps(all_presets, ensure_ascii=False),
            )
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
                collection_id=self._coll.id, name=new_name,
                send_presets=t.send_presets,
                stress_params=t.stress_params)
            for s in self._db.get_protocol_servers_by_target(t.id):
                self._db.add_protocol_server(
                    name=s.name, server_type=s.server_type,
                    ip=s.ip, port=s.port, encoding=s.encoding,
                    recv_encoding=s.recv_encoding, head_length=s.head_length,
                    ws_path=s.ws_path, response_mode=s.response_mode,
                    response_message=s.response_message,
                    response_messages=s.response_messages or None,
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
            info = target_display_info(t)
            payload.append({
                "name": t.name or "",
                "send_presets": t.send_presets,
                "stress_params": t.stress_params,
                # 兼容旧版剪贴板格式：冗余字段
                "ip": info["ip"], "port": info["port"],
                "encoding": info["encoding"], "recv_encoding": info["recv_encoding"],
                "head_length": info["head_length"], "timeout": info["timeout"],
                "ws_path": info.get("ws_url", ""), "ws_use_ssl": info.get("ws_ssl", False),
                "send_message": info.get("send_message", ""),
                "url": info.get("url", ""),
                "servers": [
                    {"name": s.name, "server_type": s.server_type,
                     "ip": s.ip, "port": s.port, "encoding": s.encoding or "",
                     "recv_encoding": s.recv_encoding or "",
                     "head_length": s.head_length, "ws_path": s.ws_path or "",
                     "response_mode": s.response_mode,
                     "response_message": s.response_message or "",
                     "response_messages": s.response_messages or "",
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
                collection_id=self._coll.id, name=new_name,
                send_presets=p.get("send_presets", "{}"),
                stress_params=p.get("stress_params", "{}"))
            for s in p.get("servers", []):
                self._db.add_protocol_server(
                    name=s["name"], server_type=s["server_type"],
                    ip=s["ip"], port=s["port"], encoding=s.get("encoding", "UTF-8"),
                    recv_encoding=s.get("recv_encoding", "UTF-8"),
                    head_length=s.get("head_length", 5), ws_path=s.get("ws_path", ""),
                    response_mode=s.get("response_mode", "echo"),
                    response_message=s.get("response_message", ""),
                    response_messages=s.get("response_messages") or None,
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
        menu.addAction("全选", lambda *_: self._target_table.selectAll())
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
                info = target_display_info(t)
                ip = info["ip"]
                port = info["port"]
                targets.append({
                    "ip": ip, "port": port,
                    "description": t.name or f"{ip}:{port}",
                })
        if targets:
            self.connectivity_test_requested.emit(targets)

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "copy"):
            self._copy_target_to_clip()
        elif shortcuts.event_matches(event, "paste"):
            self._paste_target_from_clip()
        elif shortcuts.event_matches(event, "refresh"):
            self._refresh_targets()
        elif shortcuts.event_matches(event, "delete"):
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

    def _row_cells(self, s):
        if s.server_type == "http_server":
            return [s.name, "HTTP", s.ip, str(s.port), "-", "-",
                    self._target_cell(s), "固定", str(s.response_delay)]
        return [s.name, "TCP" if "tcp" in s.server_type else "WS",
                s.ip, str(s.port), s.encoding or "", s.recv_encoding or "",
                self._target_cell(s),
                "回显" if s.response_mode == "echo" else "固定",
                str(s.response_delay)]

    def _target_cell(self, s) -> str:
        if s.target_id:
            target = self._db.get_protocol_target(s.target_id)
            if target:
                info = target_display_info(target)
                return f"{info['ip']}:{info['port']}"
            return f"ID:{s.target_id}"
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
        for sid in self._all_workers():
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
        self._proto_filter.addItem("HTTP", "http_client")
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
                2: lambda s: ("HTTP" if "http" in s.protocol_type else ("WS" if "ws" in s.protocol_type else "TCP")),
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
            proto_label = "HTTP" if "http" in s.protocol_type else ("WS" if "ws" in s.protocol_type else "TCP")
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
                "HTTP" if "http" in s.protocol_type else ("WS" if "ws" in s.protocol_type else "TCP"),
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
        if shortcuts.event_matches(event, "refresh"):
            self.refresh()
        elif shortcuts.event_matches(event, "delete"):
            self._delete_selected()
        else:
            super().keyPressEvent(event)


# ── 独立客户端 ──────────────────────────────────────────────


class _StandaloneClientTab(ClientPanelBase):
    """独立客户端 —— 不依赖集合/目标，快速测试连接。

    每个协议（TCP / WS / HTTP）的配置和预设报文独立持久化，切换协议时不丢失。
    """

    target_saved = Signal()  # 目标保存到集合后通知集合详情刷新
    test_finished = Signal()

    # 协议 → settings key 后缀映射
    _PROTO_KEY = {"tcp_client": "tcp", "ws_client": "ws", "http_client": "http"}

    def __init__(self, db: Database, parent=None):
        super().__init__(db, parent=parent)
        self._prev_proto = self._proto_combo.currentData()
        self._presets = self._load_presets_for(self._prev_proto)
        self._load_config()
        self._apply_stress_params(self._load_stress_from_store())
        self._refresh_preset_list()
        # 恢复上次使用的协议
        last_proto = self._db.get_setting("standalone_last_proto", "")
        if last_proto and last_proto != self._prev_proto:
            idx = self._proto_combo.findData(last_proto)
            if idx >= 0:
                self._proto_combo.setCurrentIndex(idx)

    # ── 协议切换时保存/恢复配置 ──────────────────────────

    def _on_proto_changed(self, idx: int):
        new_proto = self._proto_combo.currentData()
        if new_proto == self._prev_proto:
            super()._on_proto_changed(idx)
            return
        # 保存旧协议的预设（配置已随每次参数变更自动保存，无需额外保存）
        self._db.set_setting(f"standalone_presets_{self._PROTO_KEY[self._prev_proto]}",
                             json.dumps(self._presets, ensure_ascii=False))
        # 记住最后使用的协议
        self._db.set_setting("standalone_last_proto", new_proto)
        # 切换 UI
        self._prev_proto = new_proto
        super()._on_proto_changed(idx)
        # 加载新协议的配置和预设
        self._presets = self._load_presets_for(new_proto)
        self._refresh_preset_list()
        self._load_config()
        self.reset_dirty()

    def _load_presets_for(self, proto: str) -> list:
        raw = self._db.get_setting(f"standalone_presets_{self._PROTO_KEY[proto]}", "")
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            return []

    # ── 钩子：独立客户端与目标客户端的差异 ──────────────────

    def _build_action_buttons(self, proto_row):
        proto_row.addWidget(QPushButton("保存到集合", clicked=self._save_to_collection))

    def _on_ctrl_s_no_focus(self):
        self._save_config()

    def _on_param_changed(self):
        if self._loading:
            return
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

    # ── 配置持久化（settings 表，按协议分 key）────────────

    def _load_config(self):
        proto = self._proto_combo.currentData()
        raw = self._db.get_setting(f"standalone_config_{self._PROTO_KEY[proto]}", "")
        if not raw:
            return
        try:
            cfg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        # 防御：如果存储的配置 proto 与当前协议不匹配，说明数据已被交叉污染，
        # 跳过加载（使用默认值），并清除脏数据
        stored_proto = cfg.get("proto", "")
        if stored_proto and stored_proto != proto:
            self._db.set_setting(f"standalone_config_{self._PROTO_KEY[proto]}", "")
            return
        self.set_params(cfg)

    def _save_config(self):
        proto = self._proto_combo.currentData()
        self._db.set_setting(f"standalone_config_{self._PROTO_KEY[proto]}",
                             json.dumps(self.collect_params(), ensure_ascii=False))
        self.reset_config_dirty()

    def _save_presets_to_settings(self):
        proto = self._proto_combo.currentData()
        self._db.set_setting(f"standalone_presets_{self._PROTO_KEY[proto]}",
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
        cfg = self.collect_params()
        cfg["proto"] = proto
        preset_msg = json.dumps(cfg, ensure_ascii=False)
        send_presets = json.dumps(
            {"_active_proto": proto, proto: [{"name": "默认配置", "message": preset_msg}]},
            ensure_ascii=False)
        stress = json.dumps(self.collect_stress_params(), ensure_ascii=False)

        if proto == "http_client":
            url = cfg.get("url", "")
            target_name = (cfg.get("method", "GET") + " " + url) if url else "HTTP"
        else:
            ip = cfg.get("ip", "")
            port = cfg.get("port", 0)
            target_name = f"{ip}:{port}" if ip else "新目标"

        self._db.add_protocol_target(
            collection_id=coll.id, name=target_name,
            send_presets=send_presets,
            stress_params=stress,
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
        splitter.setSizes([170, 930])

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
                label + (" *" if dirty else "")
            )
        )
        layout.addWidget(detail)

        # 详情标签页名称：描述非空用描述，否则从预设提取 ip:port
        if target.name:
            label = target.name
        else:
            info = target_display_info(target)
            label = f"{info['ip']}:{info['port']}"
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
                # 未保存判定统一走 has_unsaved_presets()（含"未选预设 + 发送框被修改"），
                # 避免仅因发送框残留文本（加载/切换协议带入）就误报未保存
                presets_unsaved = client.has_unsaved_presets()
                unsaved = presets_unsaved or client._config_dirty
                if unsaved:
                    msg_parts = []
                    if client._config_dirty:
                        msg_parts.append("参数修改")
                    if presets_unsaved:
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
                        client._save_as_default_preset()
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
