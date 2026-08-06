"""协议测试公共组件 —— 客户端 / 服务端基类。

从 protocol_panel.py 抽取的共享组件：
- ClientPanelBase：协议选择 + TCP/WS 参数 + 预设报文 + 发送/响应。
  独立客户端（_StandaloneClientTab）与目标详情客户端（TargetClientPanel）
  各自持有一个实例，仅顶部按钮、持久化方式与会话记录不同。
- ServerPanelBase：服务端表格 + 启停 + 逐服务端日志/编码。
  独立服务端（_ServerTab，显示全部）与目标 Mock服务端
  （TargetMockServerPanel，按 target_id 过滤）各自持有一个实例。

ServerDialog / ENCODINGS / _hex_dump 一并搬至此模块，避免循环导入。
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.protocol import compute_length_header
from src.scanner import ScanTarget, ScannerWorker
from src.ui.protocol_workers import (
    TcpClientWorker,
    TcpServerWorker,
    WsClientWorker,
    WsServerWorker,
)
from src.ui.table_utils import enable_stretch_fill, refresh_tooltips


ENCODINGS = ["UTF-8", "GBK", "GB2312", "GB18030", "ISO-8859-1", "ASCII"]


def _hex_dump(data: bytes) -> str:
    """将字节数据格式化为十六进制转储（每行 16 字节）。"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hex_part:<48}  {ascii_part}")
    return "\n".join(lines)


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
        self._port_spin.setValue(server.get("port", 80) if server else 80)
        layout.addRow("端口:", self._port_spin)

        if self._is_tcp:
            self._encoding_combo = QComboBox()
            self._encoding_combo.setEditable(True)
            self._encoding_combo.addItems(ENCODINGS)
            enc = server.get("encoding", "UTF-8") if server else "UTF-8"
            self._encoding_combo.setCurrentText(enc)
            layout.addRow("发送编码:", self._encoding_combo)
            self._recv_encoding_combo = QComboBox()
            self._recv_encoding_combo.setEditable(True)
            self._recv_encoding_combo.addItems(ENCODINGS)
            recv_enc = server.get("recv_encoding", "UTF-8") if server else "UTF-8"
            self._recv_encoding_combo.setCurrentText(recv_enc)
            layout.addRow("接收编码:", self._recv_encoding_combo)
            self._head_len_spin = QSpinBox()
            self._head_len_spin.setRange(0, 20)
            self._head_len_spin.setToolTip("0=原始模式")
            self._head_len_spin.setSuffix(" 位")
            self._head_len_spin.setValue(server.get("head_length", 5) if server else 5)
            layout.addRow("HeadLen:", self._head_len_spin)
        else:
            self._encoding_combo = None
            self._recv_encoding_combo = None
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
            data["recv_encoding"] = self._recv_encoding_combo.currentText()
            data["head_length"] = self._head_len_spin.value()
        else:
            data["encoding"] = "UTF-8"
            data["head_length"] = 0
            data["ws_path"] = self._ws_path_edit.text().strip() or "/"
        return data


# ── 客户端公共组件 ────────────────────────────────────────────


class ClientPanelBase(QWidget):
    """客户端组件基类 —— 协议选择 + 参数 + 预设报文 + 发送/响应。

    子类通过钩子决定差异：
      _build_action_buttons  顶部按钮（保存到集合 vs 保存参数/导出/导入）
      _on_param_changed      参数变更（自动保存 vs 标记脏）
      _can_send/_can_edit_presets  是否允许操作
      get_presets/save_presets     预设存储（settings vs 目标行）
      _build_client_worker  发送 worker 参数来源（控件 vs 目标）
      _response_encoding/_client_ip_label  响应显示信息来源
      _record_session       发送完成后是否记录测试会话
      _update_len_label     报文长度显示（目标客户端有，独立客户端无）
      _params_area_max_height 参数区限高（目标客户端 64）
    """

    test_finished = Signal()
    config_dirty_changed = Signal(bool)
    target_saved = Signal()      # 独立客户端保存到集合后触发
    presets_saved = Signal()     # 预设保存后触发（目标客户端用于刷新目标）

    def __init__(self, db: Database, parent=None, show_len_label: bool = False):
        super().__init__(parent)
        self._db = db
        self._client_worker: TcpClientWorker | WsClientWorker | None = None
        self._conn_worker: ScannerWorker | None = None
        self._selected_preset_idx: int | None = None
        self._drafts: dict[int, str] = {}  # 每个预设独立的未保存草稿（索引 → 内容）
        self._dirty: set[int] = set()      # 有未保存修改的预设索引
        self._last_response = ""
        self._last_raw = b""
        self._msg_dirty = False
        self._config_dirty = False
        self._show_len_label = show_len_label
        self._presets: list[dict] = []
        self._setup_ui()

    # ── 子类钩子 ─────────────────────────────────────────────

    def _build_action_buttons(self, proto_row):
        """在协议行右侧追加操作按钮。"""

    def _on_param_changed(self):
        """任一参数变化时的行为（自动保存 / 标记脏）。"""

    def _can_send(self) -> bool:
        return True

    def _can_edit_presets(self) -> bool:
        return self._can_send()

    def get_presets(self) -> list:
        return self._presets

    def save_presets(self, presets: list):
        self._presets = presets

    def _build_client_worker(self, msg: str, proto: str):
        raise NotImplementedError

    def _response_encoding(self) -> str:
        return self._resp_enc_combo.currentText()

    def _client_ip_label(self) -> str:
        return self._param_ip.text().strip() or "?"

    def _client_endpoint(self) -> tuple[str, int]:
        """当前客户端用于连通性检测的 IP:端口。"""
        return self._param_ip.text().strip(), self._param_port.value()

    def _record_session(self, success: bool, response: str, request: str):
        """发送完成后记录测试会话（目标客户端实现）。"""

    def _update_len_label(self):
        """更新报文长度显示（目标客户端实现）。"""

    def _params_area_max_height(self):
        return None

    # ── UI ───────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 协议选择 + 操作按钮 ──
        proto_row = QHBoxLayout()
        self._proto_combo = QComboBox()
        self._proto_combo.addItem("TCP", "tcp_client")
        self._proto_combo.addItem("WebSocket", "ws_client")
        self._proto_combo.currentIndexChanged.connect(self._on_proto_changed)
        self._proto_combo.currentIndexChanged.connect(self._on_param_changed)
        proto_row.addWidget(QLabel("协议:")); proto_row.addWidget(self._proto_combo)
        proto_row.addStretch()
        self._build_action_buttons(proto_row)
        layout.addLayout(proto_row)

        # ── 参数行（TCP / WS）──
        self._tcp_params_w = QWidget()
        tf = QHBoxLayout(self._tcp_params_w); tf.setContentsMargins(0, 0, 0, 0); tf.setSpacing(2)
        tf.addWidget(QLabel("IP:")); self._param_ip = QLineEdit("127.0.0.1"); self._param_ip.setPlaceholderText("IP"); self._param_ip.setMaximumWidth(130)
        tf.addWidget(self._param_ip)
        tf.addWidget(QLabel("端口:")); self._param_port = QSpinBox(); self._param_port.setRange(1, 65535); self._param_port.setValue(80)
        tf.addWidget(self._param_port)
        tf.addWidget(QLabel("头长度:")); self._param_hl = QSpinBox(); self._param_hl.setRange(0, 20); self._param_hl.setSuffix("位"); self._param_hl.setToolTip("0=原始"); self._param_hl.setValue(5); self._param_hl.setMaximumWidth(70)
        tf.addWidget(self._param_hl)
        tf.addWidget(QLabel("超时:")); self._param_timeout = QDoubleSpinBox(); self._param_timeout.setRange(0.1, 60); self._param_timeout.setValue(30.0); self._param_timeout.setSingleStep(0.5); self._param_timeout.setDecimals(1); self._param_timeout.setSuffix("s")
        tf.addWidget(self._param_timeout)
        tf.addStretch()

        self._ws_params_w = QWidget()
        wf = QHBoxLayout(self._ws_params_w); wf.setContentsMargins(0, 0, 0, 0); wf.setSpacing(2)
        wf.addWidget(QLabel("URL:")); self._param_ws_url = QLineEdit("ws://127.0.0.1:80/ws")
        wf.addWidget(self._param_ws_url)
        wf.addWidget(QLabel("超时:")); self._param_ws_timeout = QDoubleSpinBox(); self._param_ws_timeout.setRange(0.1, 60); self._param_ws_timeout.setValue(30.0); self._param_ws_timeout.setSingleStep(0.5); self._param_ws_timeout.setSuffix("s")
        wf.addWidget(self._param_ws_timeout)
        self._param_ws_ssl = QCheckBox("SSL")
        wf.addWidget(self._param_ws_ssl)
        wf.addStretch()

        self._param_stack = QStackedWidget()
        self._param_stack.setMaximumHeight(32)
        self._param_stack.addWidget(self._tcp_params_w)
        self._param_stack.addWidget(self._ws_params_w)
        layout.addWidget(self._param_stack)

        # 参数变更自动处理（保存 / 标记脏）
        self._param_ip.textChanged.connect(self._on_param_changed)
        self._param_port.valueChanged.connect(self._on_param_changed)
        self._param_hl.valueChanged.connect(self._on_param_changed)
        self._param_timeout.valueChanged.connect(self._on_param_changed)
        self._param_ws_url.textChanged.connect(self._on_param_changed)
        self._param_ws_timeout.valueChanged.connect(self._on_param_changed)
        self._param_ws_ssl.toggled.connect(self._on_param_changed)

        # ── 左右分栏：预设 | 发送+响应 ──
        h_splitter = QSplitter(Qt.Horizontal)

        preset_g = QGroupBox("预设报文")
        pl = QVBoxLayout(preset_g)
        self._preset_list = QListWidget()
        self._preset_list.setAlternatingRowColors(False)
        self._preset_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._preset_list.itemClicked.connect(self._on_preset_clicked)
        self._preset_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._preset_list.customContextMenuRequested.connect(self._on_preset_menu)
        pl.addWidget(self._preset_list)
        self._preset_selected_label = QLabel("")
        self._preset_selected_label.setStyleSheet("color: #27ae60; font-size: 11px;")
        self._preset_selected_label.setWordWrap(True)
        pl.addWidget(self._preset_selected_label)
        pbl = QGridLayout()
        buttons = [("添加", self._add_preset), ("删除", self._delete_preset),
                   ("清空", self._clear_presets)]
        for i, (btn_text, slot) in enumerate(buttons):
            pbl.addWidget(QPushButton(btn_text, clicked=slot), i // 3, i % 3)
        pl.addLayout(pbl)
        h_splitter.addWidget(preset_g)

        # 右侧：发送 + 响应
        right_splitter = QSplitter(Qt.Vertical)

        self._send_group = QGroupBox("发送报文")
        sl = QVBoxLayout(self._send_group)
        self._send_edit = QPlainTextEdit()
        self._send_edit.setPlaceholderText("输入要发送的报文...")
        self._send_edit.textChanged.connect(self._mark_msg_dirty)
        if self._show_len_label:
            self._send_edit.textChanged.connect(self._update_len_label)
            self._len_label = QLabel("报文长度: 0 字节")
            sl.addWidget(self._len_label)
        sl.addWidget(self._send_edit)
        sh = QHBoxLayout()
        sh.addWidget(QLabel("发送编码:"))
        self._param_enc = QComboBox()
        self._param_enc.addItems(ENCODINGS)
        self._param_enc.setEditable(True)
        self._param_enc.setMaximumWidth(85)
        self._param_enc.currentTextChanged.connect(self._on_param_changed)
        sh.addWidget(self._param_enc)
        self._send_btn = QPushButton("发送")
        self._send_btn.setMinimumWidth(80)
        self._send_btn.clicked.connect(self._send_message)
        sh.addWidget(self._send_btn)
        self._terminate_btn = QPushButton("终止")
        self._terminate_btn.setVisible(False)
        self._terminate_btn.clicked.connect(self._cancel_client)
        sh.addWidget(self._terminate_btn)
        sh.addWidget(QPushButton("保存", clicked=self._save_preset))
        sh.addWidget(QPushButton("清空", clicked=self._send_edit.clear))
        # 连通性测试：直接检测当前 IP:端口并显示结果，不跳转页面
        sh.addWidget(QPushButton("连通性测试", clicked=self._run_connectivity_test))
        sh.addStretch()
        sl.addLayout(sh)
        right_splitter.addWidget(self._send_group)

        resp_g = QGroupBox("响应报文")
        rl = QVBoxLayout(resp_g)
        resp_tool = QHBoxLayout()
        resp_tool.addWidget(QLabel("接收编码:"))
        self._resp_enc_combo = QComboBox()
        self._resp_enc_combo.setEditable(True)
        self._resp_enc_combo.addItems(ENCODINGS)
        self._resp_enc_combo.currentTextChanged.connect(self._refresh_response_display)
        self._resp_enc_combo.currentTextChanged.connect(self._on_param_changed)
        resp_tool.addWidget(self._resp_enc_combo)
        self._resp_hex_toggle = QPushButton("十六进制")
        self._resp_hex_toggle.setCheckable(True)
        self._resp_hex_toggle.toggled.connect(self._refresh_response_display)
        resp_tool.addWidget(self._resp_hex_toggle)
        resp_tool.addWidget(QPushButton("清空", clicked=lambda: self._resp_edit.clear()))
        resp_tool.addStretch()
        rl.addLayout(resp_tool)
        self._resp_edit = QPlainTextEdit()
        self._resp_edit.setReadOnly(True)
        self._resp_edit.setPlaceholderText("响应将显示在这里...")
        self._resp_edit.setFont(QFont("Consolas", 10))
        rl.addWidget(self._resp_edit)
        right_splitter.addWidget(resp_g)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        h_splitter.addWidget(right_splitter)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setSizes([120, 680])
        layout.addWidget(h_splitter)

    # ── 协议切换 / 参数收集 ──────────────────────────────────

    def _on_proto_changed(self, idx: int):
        self._param_stack.setCurrentIndex(
            0 if self._proto_combo.currentData() == "tcp_client" else 1)

    def set_params(self, cfg: dict):
        """从配置 dict 填充参数控件（不触发保存/脏标记语义）。"""
        proto = cfg.get("proto", "tcp_client")
        self._proto_combo.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._param_stack.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._param_ip.setText(cfg.get("ip", "127.0.0.1"))
        self._param_port.setValue(cfg.get("port", 80))
        self._param_enc.setCurrentText(cfg.get("encoding", "UTF-8"))
        self._resp_enc_combo.setCurrentText(cfg.get("recv_encoding", "UTF-8"))
        self._param_hl.setValue(cfg.get("head_length", 5))
        self._param_timeout.setValue(cfg.get("timeout", 5.0))
        self._param_ws_url.setText(cfg.get("ws_url", "ws://127.0.0.1:80/ws"))
        self._param_ws_timeout.setValue(cfg.get("ws_timeout", 5.0))
        self._param_ws_ssl.setChecked(cfg.get("ws_ssl", False))

    def collect_params(self) -> dict:
        """汇总当前协议与参数。"""
        return {
            "proto": self._proto_combo.currentData(),
            "ip": self._param_ip.text().strip(),
            "port": self._param_port.value(),
            "encoding": self._param_enc.currentText(),
            "recv_encoding": self._resp_enc_combo.currentText(),
            "head_length": self._param_hl.value(),
            "timeout": self._param_timeout.value(),
            "ws_url": self._param_ws_url.text().strip(),
            "ws_timeout": self._param_ws_timeout.value(),
            "ws_ssl": self._param_ws_ssl.isChecked(),
        }

    def prefill(self, ip: str, port: int):
        """预填 IP 和端口（从连通测试跳转）。"""
        self._param_ip.setText(ip)
        self._param_port.setValue(port)

    # ── 脏标记 ───────────────────────────────────────────────

    def _mark_config_dirty(self):
        if not self._config_dirty:
            self._config_dirty = True
            self.config_dirty_changed.emit(True)

    def reset_config_dirty(self):
        self._config_dirty = False
        self.config_dirty_changed.emit(False)

    def reset_dirty(self):
        self.reset_config_dirty()
        self._drafts.clear()
        self._dirty.clear()
        self._selected_preset_idx = None
        self._msg_dirty = False
        self._update_send_label()
        self._update_preset_stars()

    def _mark_msg_dirty(self):
        self._sync_current_draft()
        self._update_send_label()
        self._update_preset_stars()

    def _update_send_label(self):
        self._send_group.setTitle("发送报文" + (" *" if self._current_is_dirty() else ""))

    def _current_is_dirty(self) -> bool:
        idx = self._selected_preset_idx
        return idx is not None and idx in self._dirty

    def _sync_current_draft(self):
        """把发送框内容缓存为当前预设的草稿，并同步脏标记。"""
        idx = self._selected_preset_idx
        if idx is None:
            return
        presets = self.get_presets()
        if idx >= len(presets):
            return
        text = self._send_edit.toPlainText()
        saved = presets[idx].get("message", "")
        if text != saved:
            self._drafts[idx] = text
            self._dirty.add(idx)
        else:
            self._drafts.pop(idx, None)
            self._dirty.discard(idx)
        self._msg_dirty = idx in self._dirty

    def _update_preset_stars(self):
        """把列表项名称与脏标记同步（有未保存修改的预设名后加 *）。"""
        presets = self.get_presets()
        for i in range(self._preset_list.count()):
            item = self._preset_list.item(i)
            idx = item.data(Qt.UserRole)
            if idx is None or idx >= len(presets):
                continue
            name = presets[idx].get("name", "")
            item.setText(f"{name} *" if idx in self._dirty else name)

    def has_unsaved_presets(self) -> bool:
        """是否有预设报文存在未保存的修改。"""
        return bool(self._dirty)

    def save_all_drafts(self) -> None:
        """保存所有有未保存修改的预设报文。"""
        presets = list(self.get_presets())
        changed = False
        for idx in list(self._dirty):
            if 0 <= idx < len(presets) and idx in self._drafts:
                presets[idx]["message"] = self._drafts[idx]
                changed = True
        if changed:
            self.save_presets(presets)
        self._dirty.clear()
        self._drafts.clear()
        self._msg_dirty = False
        self._update_send_label()
        self._update_preset_stars()
        self._refresh_preset_list()
        self.presets_saved.emit()

    # ── 预设报文 ─────────────────────────────────────────────

    def _refresh_preset_list(self):
        lst = self._preset_list
        lst.clear()
        presets = self.get_presets()
        for i, p in enumerate(presets):
            star = " *" if i in self._dirty else ""
            item = QListWidgetItem(f"{p.get('name', '')}{star}")
            item.setData(Qt.UserRole, i)
            lst.addItem(item)
        if self._selected_preset_idx is not None and self._selected_preset_idx < len(presets):
            lst.setCurrentRow(self._selected_preset_idx)
            self._preset_selected_label.setText(
                f"✓ 已选择: {presets[self._selected_preset_idx].get('name', '')}")
        else:
            self._selected_preset_idx = None
            self._preset_selected_label.setText("")

    def _on_preset_menu(self, pos):
        item = self._preset_list.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("添加", self._add_preset)
        if item:
            menu.addAction("保存", self._save_preset)
            menu.addAction("重命名", self._edit_preset)
            menu.addAction("删除", self._delete_preset)
            menu.addSeparator()
            menu.addAction("清空", self._clear_presets)
        menu.exec(self._preset_list.viewport().mapToGlobal(pos))

    def _on_preset_clicked(self, item: QListWidgetItem):
        idx = item.data(Qt.UserRole)
        presets = self.get_presets()
        if idx is None or idx >= len(presets):
            return
        # 先把当前预设未保存的内容缓存到它的草稿区，再切换，不弹确认
        self._sync_current_draft()
        self._selected_preset_idx = idx
        draft = self._drafts.get(idx)
        self._send_edit.setPlainText(
            draft if draft is not None else presets[idx].get("message", ""))
        self._preset_selected_label.setText(f"✓ 已选择: {presets[idx].get('name', '')}")
        self._sync_current_draft()
        self._update_send_label()
        self._update_preset_stars()

    def _add_preset(self):
        if not self._can_edit_presets():
            return
        presets = list(self.get_presets())
        default_name = f"报文{len(presets) + 1}"
        name, ok = QInputDialog.getText(self, "添加预设", "预设名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p.get("name", "") == name for p in presets):
            QMessageBox.warning(self, "名称重复", f"预设「{name}」已存在，请使用其他名称。")
            return
        presets.append({"name": name, "message": self._send_edit.toPlainText()})
        self._selected_preset_idx = len(presets) - 1
        self._drafts.pop(self._selected_preset_idx, None)
        self._dirty.discard(self._selected_preset_idx)
        self.save_presets(presets)
        self.presets_saved.emit()
        self._refresh_preset_list()
        self._msg_dirty = False
        self._update_send_label()

    def _save_preset(self):
        """将当前输入框内容保存到选中的预设，或弹窗选择覆盖/新建。"""
        if not self._can_edit_presets():
            return
        new_msg = self._send_edit.toPlainText()
        presets = list(self.get_presets())

        if self._selected_preset_idx is not None and self._selected_preset_idx < len(presets):
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
            # 未选中预设：弹窗选择覆盖或新建（双击列表项可直接确认）
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
            lst.itemDoubleClicked.connect(dlg.accept)
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
            name, ok = QInputDialog.getText(self, "新建预设", "预设名称:", text="报文1")
            if not ok or not name.strip():
                return
            presets.append({"name": name.strip(), "message": new_msg})
            self._selected_preset_idx = len(presets) - 1

        self.save_presets(presets)
        self.presets_saved.emit()
        # 保存后清除与保存内容一致的草稿
        for idx in list(self._dirty):
            if 0 <= idx < len(presets) and self._drafts.get(idx) == presets[idx].get("message", ""):
                self._drafts.pop(idx, None)
                self._dirty.discard(idx)
        self._refresh_preset_list()
        self._msg_dirty = self._current_is_dirty()
        self._update_send_label()
        self._update_preset_stars()

    def _edit_preset(self):
        if not self._can_edit_presets():
            return
        item = self._preset_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.UserRole)
        presets = self.get_presets()
        if idx is None or idx >= len(presets):
            return
        p = presets[idx]
        name, ok = QInputDialog.getText(self, "编辑预设", "预设名称:", text=p["name"])
        if not ok or not name.strip():
            return
        presets[idx]["name"] = name.strip()
        presets[idx]["message"] = self._send_edit.toPlainText()
        self._selected_preset_idx = idx
        self._drafts.pop(idx, None)
        self._dirty.discard(idx)
        self.save_presets(presets)
        self.presets_saved.emit()
        self._refresh_preset_list()
        self._msg_dirty = False
        self._update_send_label()

    def _delete_preset(self):
        items = self._preset_list.selectedItems()
        if not items:
            return
        presets = list(self.get_presets())
        idxs = sorted({it.data(Qt.UserRole) for it in items
                       if it.data(Qt.UserRole) is not None})
        idxs = [i for i in idxs if i < len(presets)]
        if not idxs:
            return
        names = [presets[i].get("name", "") for i in idxs]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除选中的 {len(idxs)} 个预设？\n" + "\n".join(f"  • {n}" for n in names),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        original_len = len(presets)
        deleted = set(idxs)
        for i in reversed(idxs):
            presets.pop(i)
        # 删除后按剩余顺序重建草稿/脏索引
        new_drafts, new_dirty = {}, set()
        new_idx = 0
        for old_idx in range(original_len):
            if old_idx in deleted:
                continue
            if old_idx in self._drafts:
                new_drafts[new_idx] = self._drafts[old_idx]
            if old_idx in self._dirty:
                new_dirty.add(new_idx)
            new_idx += 1
        self._drafts = new_drafts
        self._dirty = new_dirty
        if self._selected_preset_idx in deleted:
            self._selected_preset_idx = None
        self.save_presets(presets)
        self.presets_saved.emit()
        self._refresh_preset_list()
        self._msg_dirty = False
        self._update_send_label()

    def _clear_presets(self):
        presets = self.get_presets()
        if not presets:
            return
        reply = QMessageBox.question(
            self, "确认清空",
            f"确定要清空全部 {len(presets)} 个预设报文吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.save_presets([])
        self._drafts.clear()
        self._dirty.clear()
        self._selected_preset_idx = None
        self.presets_saved.emit()
        self._refresh_preset_list()
        self._msg_dirty = False
        self._update_send_label()

    # ── 发送消息 ─────────────────────────────────────────────

    def _run_connectivity_test(self):
        """连通性测试：直接检测当前 IP:端口并显示结果，不跳转页面。"""
        if self._conn_worker and self._conn_worker.isRunning():
            return
        ip, port = self._client_endpoint()
        if not ip:
            QMessageBox.information(self, "提示", "IP 地址为空。")
            return
        self._conn_worker = ScannerWorker(
            [ScanTarget(id=0, ip=ip, port=port, description="")],
            timeout=3.0, max_workers=1, parent=self,
        )
        self._conn_worker.finished_all.connect(self._on_connectivity_done)
        self._conn_worker.start()

    def _on_connectivity_done(self, results: list):
        self._conn_worker = None
        if not results:
            return
        r = results[0]
        if r.success:
            QMessageBox.information(
                self, "连通性测试", f"{r.ip}:{r.port} 连通 ({r.latency_ms:.1f}ms)")
        else:
            QMessageBox.warning(
                self, "连通性测试", f"{r.ip}:{r.port} 未连通: {r.error_msg}")

    def _send_message(self):
        if not self._can_send():
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
        self._client_worker = self._build_client_worker(msg, proto)
        self._client_worker.finished.connect(self._on_client_done)
        self._client_worker.start()

    def _cancel_client(self):
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
        enc = self._response_encoding()
        try:
            self._last_raw = response.encode(enc, errors='replace')
        except Exception:
            self._last_raw = response.encode('utf-8', errors='replace')
        if success and self._last_raw:
            detected = self._detect_encoding(self._last_raw)
            if detected and detected != self._resp_enc_combo.currentText():
                self._resp_enc_combo.setCurrentText(detected)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tag = "OK" if success else "FAIL"
        ip = self._client_ip_label()
        request = self._send_edit.toPlainText()
        self._append_response(
            f"-------------------------------------------------------------------------------\n"
            f"[{ts}][客户端][{ip}]:\n{request}\n"
            f"[{ts}] {tag}:\n{response}"
        )
        self._record_session(success, response, request)
        self.test_finished.emit()

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
                chunk = self._last_raw[i:i + 16]
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F5:
            self._refresh_preset_list()
        else:
            super().keyPressEvent(event)


# ── 服务端公共组件 ────────────────────────────────────────────


class ServerPanelBase(QWidget):
    """服务端管理组件基类 —— 表格 + 启停 + 逐服务端日志/编码。

    子类通过钩子决定差异：
      _has_filter_bar / _show_status_label  是否显示类型筛选、搜索、状态栏
      _load_servers                         加载范围（全部 vs 按目标）
      _server_columns / _row_cells          列结构与单元格内容
      _target_cell / _center_columns        关联目标列、居中列
      _sortable_column / _sort_key          可排序列
      _add_target_id / _edit_target_id      target_id 归属
      _default_add_type / 对话框标题        添加默认类型
      _check_port_conflict / _log_block_cap 端口冲突检查、日志上限
      _confirm_delete_text / _running_delete_warning  删除确认文案
      _on_stop_all                          全部停止后的清理
    """

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._tcp_workers: dict[int, TcpServerWorker] = {}
        self._ws_workers: dict[int, WsServerWorker] = {}
        self._servers: list = []          # 当前加载的服务端
        self._logs: dict[int, QPlainTextEdit] = {}
        self._log_tab_to_sid: dict[int, int] = {}
        self._send: dict[int, str] = {}
        self._recv: dict[int, str] = {}
        self._hex: dict[int, bool] = {}
        self._send_combos: dict[int, QComboBox] = {}
        self._recv_combos: dict[int, QComboBox] = {}
        self._hex_toggles: dict[int, QPushButton] = {}
        self._recv_raw: dict[int, bytes] = {}
        self._status: dict[int, list[str]] = {}
        self._addr: dict[int, str] = {}    # sid -> "ip:port"
        self._sort_col = -1
        self._sort_asc = True
        self._setup_ui()

    # ── 子类钩子 ─────────────────────────────────────────────

    def _has_filter_bar(self) -> bool:
        return False

    def _show_type_filter(self) -> bool:
        return True

    def _show_search(self) -> bool:
        return True

    def _show_status_label(self) -> bool:
        return False

    def _content_margins(self):
        return (4, 4, 4, 4)

    def _can_refresh(self) -> bool:
        return True

    def _can_add(self) -> bool:
        return True

    def _load_servers(self) -> list:
        return []

    def _server_columns(self) -> list:
        return ["名称", "监听地址", "端口", "发送编码", "接收编码", "响应模式"]

    def _row_cells(self, s, is_tcp: bool) -> list:
        return []

    def _target_cell(self, s) -> str:
        return ""

    def _center_columns(self):
        return set()

    def _sortable_column(self, col: int) -> bool:
        return True

    def _sort_key(self, col: int):
        key_map = {0: lambda s: s.name, 1: lambda s: s.ip, 2: lambda s: s.port}
        return key_map.get(col)

    def _default_add_type(self) -> str:
        return "tcp_server"

    def _add_dialog_title(self) -> str:
        return "添加监听器"

    def _edit_dialog_title(self) -> str:
        return "编辑监听器"

    def _add_target_id(self):
        return None

    def _edit_target_id(self, srv):
        return srv.target_id

    def _check_port_conflict(self, s) -> bool:
        return False

    def _log_block_cap(self) -> int:
        return 5000

    def _confirm_delete_text(self, ids) -> str:
        return f"确定要删除选中的 {len(ids)} 个监听器吗？"

    def _running_delete_warning(self, running) -> str:
        return "请先停止选中的监听器再删除。"

    def _on_stop_all(self):
        """全部停止后的清理（子类可重写）。"""

    # ── UI ───────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*self._content_margins())

        if self._has_filter_bar():
            fl = QHBoxLayout()
            if self._show_type_filter():
                fl.addWidget(QLabel("类型:"))
                self._type_filter = QComboBox()
                self._type_filter.addItem("全部", None)
                self._type_filter.addItem("TCP", "tcp_server")
                self._type_filter.addItem("WebSocket", "ws_server")
                self._type_filter.currentIndexChanged.connect(self._refresh)
                fl.addWidget(self._type_filter)
            if self._show_search():
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
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionsClickable(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_menu)
        self._table.cellDoubleClicked.connect(self._on_double_clicked)
        enable_stretch_fill(self._table)

        srv_splitter = QSplitter(Qt.Vertical)
        srv_splitter.addWidget(self._table)
        self._log_tabs = QTabWidget()
        self._log_tabs.setTabsClosable(True)
        self._log_tabs.tabCloseRequested.connect(self._on_log_tab_close)
        srv_splitter.addWidget(self._log_tabs)
        srv_splitter.setStretchFactor(0, 1)
        srv_splitter.setStretchFactor(1, 1)
        srv_splitter.setSizes([300, 300])
        layout.addWidget(srv_splitter)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("添加", clicked=self._add_server))
        bl.addWidget(QPushButton("编辑", clicked=self._edit_server))
        bl.addWidget(QPushButton("删除", clicked=self._delete_selected_servers))
        bl.addWidget(QPushButton("启动选中", clicked=self._start_selected))
        bl.addWidget(QPushButton("停止选中", clicked=self._stop_selected))
        bl.addStretch()
        bl.addWidget(QPushButton("▶ 全部启动", clicked=self._start_all))
        bl.addWidget(QPushButton("■ 全部停止", clicked=self._stop_all))
        layout.addLayout(bl)
        if self._show_status_label():
            self._status_label = QLabel("")
            layout.addWidget(self._status_label)

    # ── 刷新 ─────────────────────────────────────────────────

    def refresh(self):
        self._refresh()

    def _refresh(self):
        if not self._can_refresh():
            return
        self._servers = self._load_servers()
        if self._sort_col >= 0:
            key_fn = self._sort_key(self._sort_col) or (lambda s: s.name)
            self._servers.sort(key=key_fn, reverse=not self._sort_asc)
        self._populate_table()
        if hasattr(self, "_search") and self._search.text().strip():
            self._filter(self._search.text())

    def _populate_table(self):
        t = self._table
        cols = self._server_columns()
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        self._update_sort_indicator()
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(len(cols) - 1, QHeaderView.Fixed)
        t.setColumnWidth(len(cols) - 1, 80)

        t.setRowCount(len(self._servers))
        all_workers = {**self._tcp_workers, **self._ws_workers}
        center = self._center_columns()
        for row, s in enumerate(self._servers):
            is_tcp = s.server_type == "tcp_server"
            cells = self._row_cells(s, is_tcp)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setData(Qt.UserRole, s.id)
                if col in center:
                    item.setTextAlignment(Qt.AlignCenter)
                t.setItem(row, col, item)
            running = s.id in all_workers
            status_item = QTableWidgetItem("运行中" if running else "已停止")
            status_item.setForeground(Qt.green if running else Qt.red)
            t.setItem(row, len(cols) - 2, status_item)
            btn = QPushButton("Stop" if running else "Start")
            btn.setStyleSheet("color: #e74c3c;" if running else "color: #27ae60;")
            btn.clicked.connect(partial(self._toggle_server, s))
            t.setCellWidget(row, len(cols) - 1, btn)
        refresh_tooltips(t)
        if hasattr(self, "_status_label"):
            self._status_label.setText(
                f"共 {len(self._servers)} 个服务端, {len(all_workers)} 个运行中")

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._table.rowCount()):
            match = False
            for col in (0, 2, 3, 6):
                item = self._table.item(row, col)
                if item and s in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match if s else False)

    def _on_header_clicked(self, col: int):
        if not self._sortable_column(col):
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col; self._sort_asc = True
        self._refresh()

    def _update_sort_indicator(self):
        for c in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(c)
            if item:
                base = item.text().rstrip(" ▲▼")
                arrow = " ▲" if (c == self._sort_col and self._sort_asc) else \
                        " ▼" if c == self._sort_col else ""
                item.setText(base + arrow)

    def _on_menu(self, pos):
        item = self._table.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("添加", self._add_server)
        if item:
            row = item.row()
            model = self._table.model()
            if not self._table.selectionModel().isSelected(model.index(row, 0)):
                self._table.selectRow(row)
            menu.addAction("编辑", self._edit_server)
            menu.addAction("删除", self._delete_selected_servers)
            menu.addAction("启动", self._start_selected)
            menu.addAction("停止", self._stop_selected)
        menu.addSeparator()
        menu.addAction("全部启动", self._start_all)
        menu.addAction("全部停止", self._stop_all)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── 启停 + 日志 ──────────────────────────────────────────

    def _toggle_server(self, srv, _checked=None):
        from src.database import ProtocolServer
        s: ProtocolServer = srv
        st = s.server_type
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        if s.id in workers:
            w = workers.pop(s.id); w.stop_server()
            self._log_to_server(s.id, f"Stop [{s.name}] {s.ip}:{s.port}")
            for tab_idx, sid in list(self._log_tab_to_sid.items()):
                if sid == s.id:
                    self._log_tabs.removeTab(tab_idx)
                    del self._log_tab_to_sid[tab_idx]
                    # 清除临时日志数据，保留编码值以便重启后复用
                    self._logs.pop(s.id, None)
                    self._recv_raw.pop(s.id, None)
                    self._status.pop(s.id, None)
                    self._addr.pop(s.id, None)
                    self._send_combos.pop(s.id, None)
                    self._recv_combos.pop(s.id, None)
                    self._hex_toggles.pop(s.id, None)
                    break
        else:
            if self._check_port_conflict(s):
                return
            tab_w = QWidget()
            tab_layout = QVBoxLayout(tab_w)
            tab_layout.setContentsMargins(4, 4, 4, 4)
            tab_tool = QHBoxLayout()
            is_tcp_srv = st == "tcp_server"
            if is_tcp_srv:
                send_combo = QComboBox()
                send_combo.setEditable(True)
                send_combo.addItems(ENCODINGS)
                send_combo.setCurrentText(self._send.get(s.id, s.encoding or "UTF-8"))
                recv_combo = QComboBox()
                recv_combo.setEditable(True)
                recv_combo.addItems(ENCODINGS)
                recv_combo.setCurrentText(self._recv.get(s.id, s.recv_encoding or "UTF-8"))
                tab_tool.addWidget(QLabel("发送编码:"))
                tab_tool.addWidget(send_combo)
                tab_tool.addWidget(QLabel("接收编码:"))
                tab_tool.addWidget(recv_combo)
            else:
                send_combo = None
                recv_combo = QComboBox()
                recv_combo.setEditable(True)
                recv_combo.addItems(ENCODINGS)
                recv_combo.setCurrentText(self._recv.get(s.id, "UTF-8"))
                tab_tool.addWidget(QLabel("编码:"))
                tab_tool.addWidget(recv_combo)
            hex_toggle = QPushButton("十六进制")
            hex_toggle.setCheckable(True)
            hex_toggle.setChecked(self._hex.get(s.id, False))
            tab_tool.addWidget(hex_toggle)
            tab_tool.addStretch()
            tab_tool.addWidget(QPushButton(
                "清空", clicked=lambda _checked=False, sid=s.id: self._clear_log(sid)))
            tab_layout.addLayout(tab_tool)
            log_w = QPlainTextEdit()
            log_w.setReadOnly(True)
            log_w.setMaximumBlockCount(self._log_block_cap())
            tab_layout.addWidget(log_w)
            if send_combo is not None:
                send_combo.currentTextChanged.connect(
                    lambda text=None, sid=s.id, cb=send_combo: self._on_send_changed(sid, cb))
            recv_combo.currentTextChanged.connect(
                lambda text=None, sid=s.id, cb=recv_combo: self._on_recv_changed(sid, cb))
            hex_toggle.toggled.connect(
                lambda checked=None, sid=s.id, btn=hex_toggle: self._on_hex_changed(sid, btn))
            tab_idx = self._log_tabs.addTab(tab_w, f"{s.name}:{s.port}")
            self._log_tabs.setCurrentIndex(tab_idx)
            self._logs[s.id] = log_w
            self._log_tab_to_sid[tab_idx] = s.id
            if send_combo is not None:
                self._send_combos[s.id] = send_combo
                self._send[s.id] = send_combo.currentText()
            self._recv_combos[s.id] = recv_combo
            self._hex_toggles[s.id] = hex_toggle
            self._recv[s.id] = recv_combo.currentText()
            self._hex[s.id] = hex_toggle.isChecked()
            self._recv_raw.setdefault(s.id, b"")
            self._status.setdefault(s.id, [])
            self._addr[s.id] = f"{s.ip}:{s.port}"
            self._log_to_server(s.id, f"Start [{s.name}] {s.ip}:{s.port}")
            if is_tcp_srv:
                w = TcpServerWorker(server_id=s.id, ip=s.ip, port=s.port,
                                    encoding=self._send.get(s.id, s.encoding or "UTF-8"),
                                    recv_encoding=self._recv.get(s.id, s.recv_encoding or "UTF-8"),
                                    head_len=s.head_length,
                                    response_mode=s.response_mode, response_message=s.response_message)
                w.message_received.connect(partial(self._on_srv_msg, s.id, s.name))
                w.message_received_raw.connect(partial(self._on_srv_msg_raw, s.id))
            else:
                w = WsServerWorker(server_id=s.id, ip=s.ip, port=s.port, path=s.ws_path,
                                   response_mode=s.response_mode, response_message=s.response_message)
                w.message_received.connect(lambda addr, msg, sid=s.id, nm=s.name:
                                           self._on_srv_msg(sid, nm, addr, msg))
                w.message_received_raw.connect(partial(self._on_srv_msg_raw, s.id))
                w.client_event.connect(partial(self._log_to_server, s.id))
            w.status_changed.connect(partial(self._log_to_server, s.id))
            w.error_occurred.connect(lambda err, sid=s.id: self._log_to_server(sid, f"[ERR] {err}"))
            w.finished.connect(partial(self._on_worker_finished, st, s.id))
            workers[s.id] = w; w.start()
        self._refresh()

    def _log_to_server(self, sid: int, text: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        addr = self._addr.get(sid, "?")
        if text.startswith("已回复:\n"):
            body = text[len("已回复:\n"):]
            formatted = f"[{ts}][服务端][{addr}]:\n{body}"
        elif "已回复" in text or "已发送" in text:
            formatted = f"[{ts}][服务端][{addr}]: {text}"
        else:
            formatted = f"[{ts}] {text}"
        self._status.setdefault(sid, []).append(formatted)
        log = self._logs.get(sid)
        if log and not self._hex.get(sid, False):
            log.appendPlainText(formatted)

    def _on_srv_msg(self, sid: int, name: str, addr="", msg=""):
        if self._hex.get(sid, False):
            return  # 十六进制模式下由 _on_srv_msg_raw 统一渲染
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log = self._logs.get(sid)
        if log:
            ip = addr or "?"
            log.appendPlainText(
                f"-------------------------------------------------------------------------------\n[{ts}][客户端][{ip}]:\n{msg}")

    def _on_srv_msg_raw(self, sid: int, addr: str, raw: bytes):
        if raw:
            self._recv_raw[sid] = self._recv_raw.get(sid, b"") + raw + b"\n"
        if self._hex.get(sid, False):
            self._refresh_single_log(sid)

    def _on_send_changed(self, sid: int, combo: QComboBox):
        self._send[sid] = combo.currentText()
        self._save_server_encodings(sid)
        self._refresh()  # 编码修改后自动刷新服务列表

    def _on_recv_changed(self, sid: int, combo: QComboBox):
        self._recv[sid] = combo.currentText()
        self._save_server_encodings(sid)
        self._refresh_single_log(sid)
        self._refresh()  # 编码修改后自动刷新服务列表

    def _on_hex_changed(self, sid: int, btn: QPushButton):
        self._hex[sid] = btn.isChecked()
        self._refresh_single_log(sid)

    def _clear_log(self, sid: int):
        self._recv_raw[sid] = b""
        self._status[sid] = []
        log = self._logs.get(sid)
        if log:
            log.clear()

    def _save_server_encodings(self, sid: int):
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        send_enc = self._send.get(sid, srv.encoding)
        recv_enc = self._recv.get(sid, srv.recv_encoding)
        self._db.update_protocol_server(
            server_id=sid, name=srv.name, server_type=srv.server_type,
            ip=srv.ip, port=srv.port, encoding=send_enc,
            recv_encoding=recv_enc, head_length=srv.head_length,
            ws_path=srv.ws_path, response_mode=srv.response_mode,
            response_message=srv.response_message, target_id=srv.target_id,
        )
        w = self._tcp_workers.get(sid)
        if w is not None and hasattr(w, "set_encodings"):
            w.set_encodings(send_enc, recv_enc)

    def _refresh_single_log(self, sid: int):
        log = self._logs.get(sid)
        if not log:
            return
        if self._hex.get(sid, False):
            raw = self._recv_raw.get(sid, b"")
            log.setPlainText(_hex_dump(raw))
        else:
            enc = self._recv.get(sid, "UTF-8")
            raw = self._recv_raw.get(sid, b"")
            try:
                decoded = raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                decoded = raw.decode(enc, errors="replace")
            parts = list(self._status.get(sid, []))
            if raw:
                parts.append(decoded)
            log.setPlainText("\n".join(parts))

    def _on_worker_finished(self, st: str, sid: int):
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        workers.pop(sid, None)
        self._refresh()

    def _on_log_tab_close(self, idx: int):
        sid = self._log_tab_to_sid.pop(idx, None)
        # 清除临时日志数据，保留编码值以便重启后复用
        self._recv_raw.pop(sid, None)
        self._status.pop(sid, None)
        self._addr.pop(sid, None)
        self._send_combos.pop(sid, None)
        self._recv_combos.pop(sid, None)
        self._hex_toggles.pop(sid, None)
        if sid is not None:
            for workers in (self._tcp_workers, self._ws_workers):
                w = workers.pop(sid, None)
                if w:
                    w.stop_server()
                    break
            self._logs.pop(sid, None)
        if idx >= 0:
            self._log_tabs.removeTab(idx)
        # 重建索引映射
        self._log_tab_to_sid = {}
        for i in range(self._log_tabs.count()):
            w = self._log_tabs.widget(i)
            for _sid, _log in list(self._logs.items()):
                if _log == w:
                    self._log_tab_to_sid[i] = _sid
                    break
        self._refresh()

    # ── 增删改 ───────────────────────────────────────────────

    def _add_server(self):
        if not self._can_add():
            return
        st = self._default_add_type()
        dlg = ServerDialog(self._add_dialog_title(), st, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.add_protocol_server(
                name=d["name"], server_type=st, ip=d["ip"], port=d["port"],
                encoding=d.get("encoding", "UTF-8"), recv_encoding=d.get("recv_encoding", "UTF-8"), head_length=d.get("head_length", 0),
                ws_path=d.get("ws_path", "/"), response_mode=d["response_mode"],
                response_message=d["response_message"], target_id=self._add_target_id(),
            )
            self._refresh()

    def _on_double_clicked(self, row: int, col: int):
        self._table.selectRow(row)
        self._edit_server()

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
                    recv_encoding=srv.recv_encoding, head_length=srv.head_length,
                    ws_path=srv.ws_path, response_mode=srv.response_mode,
                    response_message=srv.response_message)
        dlg = ServerDialog(self._edit_dialog_title(), srv.server_type, data, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.update_protocol_server(
                server_id=sid, name=d["name"], server_type=srv.server_type,
                ip=d["ip"], port=d["port"], encoding=d.get("encoding", "UTF-8"),
                recv_encoding=d.get("recv_encoding", "UTF-8"),
                head_length=d.get("head_length", 0), ws_path=d.get("ws_path", "/"),
                response_mode=d["response_mode"], response_message=d["response_message"],
                target_id=self._edit_target_id(srv),
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
            return QMessageBox.information(self, "提示", "请选择要删除的监听器。")
        all_workers = {**self._tcp_workers, **self._ws_workers}
        running = [sid for sid in ids if sid in all_workers]
        if running:
            return QMessageBox.warning(self, "提示", self._running_delete_warning(running))
        r = QMessageBox.question(self, "确认删除", self._confirm_delete_text(ids),
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
                    for tab_idx, tsid in list(self._log_tab_to_sid.items()):
                        if tsid == sid:
                            self._log_tabs.removeTab(tab_idx)
                            del self._log_tab_to_sid[tab_idx]
                            # 清除临时日志数据，保留编码值
                            self._logs.pop(sid, None)
                            self._recv_raw.pop(sid, None)
                            self._status.pop(sid, None)
                            self._addr.pop(sid, None)
                            self._send_combos.pop(sid, None)
                            self._recv_combos.pop(sid, None)
                            self._hex_toggles.pop(sid, None)
                            break
                    break
        self._refresh()

    def _start_all(self):
        for s in self._servers:
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
        self._on_stop_all()
        self._refresh()

    def has_active_servers(self) -> bool:
        return bool(self._tcp_workers) or bool(self._ws_workers)

    def stop_all_servers(self):
        self._stop_all()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F5:
            self._refresh()
        elif event.key() == Qt.Key_Delete or (event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier):
            self._delete_selected_servers()
        else:
            super().keyPressEvent(event)
