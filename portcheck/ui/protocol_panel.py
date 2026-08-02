"""协议测试面板 —— 客户端 / 服务端两个标签页，协议类型内部切换。"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portcheck.database import Database
from portcheck.protocol import compute_length_header
from portcheck.ui.protocol_workers import (
    TcpClientWorker,
    TcpServerWorker,
    WsClientWorker,
    WsServerWorker,
)


ENCODINGS = ["UTF-8", "GBK", "GB2312", "ISO-8859-1", "ASCII"]


def _slot(fn, *args):
    """返回忽略所有信号参数、只调用 fn(*args) 的可调用对象。"""
    def handler(*_sig_args):
        fn(*args)
    return handler


# ── 服务端配置对话框 ────────────────────────────────────────


class ServerDialog(QDialog):
    """新建/编辑协议服务端监听器配置。"""

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
            self._encoding_combo.addItems(ENCODINGS)
            enc = server.get("encoding", "UTF-8") if server else "UTF-8"
            self._encoding_combo.setCurrentText(enc)
            layout.addRow("编码:", self._encoding_combo)

            self._head_len_spin = QSpinBox()
            self._head_len_spin.setRange(0, 20)
            self._head_len_spin.setToolTip("0=原始模式")
            self._head_len_spin.setSuffix(" 位")
            self._head_len_spin.setValue(
                server.get("head_length", 5) if server else 5
            )
            layout.addRow("HeadLen:", self._head_len_spin)
        else:
            self._encoding_combo = None
            self._head_len_spin = None
            self._ws_path_edit = QLineEdit(
                server.get("ws_path", "/") if server else "/"
            )
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


# ── 协议测试面板 ────────────────────────────────────────────


class ProtocolPanel(QWidget):
    """协议测试面板 —— 客户端 / 服务端 两个标签页。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._tcp_workers: dict[int, TcpServerWorker] = {}
        self._ws_workers: dict[int, WsServerWorker] = {}
        self._client_worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_client_tab(), "客户端")
        self._sub_tabs.addTab(self._build_server_tab(), "服务端")
        layout.addWidget(self._sub_tabs)

    # ── 公共方法 ─────────────────────────────────────────────

    def has_active_servers(self) -> bool:
        return bool(self._tcp_workers) or bool(self._ws_workers)

    def stop_all_servers(self) -> None:
        for w in list(self._tcp_workers.values()):
            w.stop_server()
        self._tcp_workers.clear()
        for w in list(self._ws_workers.values()):
            w.stop_server()
        self._ws_workers.clear()

    def prefill_client_target(self, ip: str, port: int) -> None:
        """从外部（目标管理面板）预填客户端目标 IP 和端口。切换到 TCP 客户端模式。"""
        self._sub_tabs.setCurrentIndex(0)  # 切换到客户端标签页
        self._client_proto_combo.setCurrentIndex(0)  # 切换到 TCP
        self._tcp_ip.setText(ip)
        self._tcp_port.setValue(port)

    # ═══════════════════════════════════════════════════════════
    # 客户端标签页
    # ═══════════════════════════════════════════════════════════

    def _build_client_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # ── 左侧: 集合列表 ────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)

        ll.addWidget(QLabel("<b>测试集合</b>"))

        self._coll_search = QLineEdit()
        self._coll_search.setPlaceholderText("搜索...")
        self._coll_search.setClearButtonEnabled(True)
        self._coll_search.textChanged.connect(self._filter_collection_list)
        ll.addWidget(self._coll_search)

        self._coll_list = QListWidget()
        self._coll_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._coll_list.currentRowChanged.connect(self._on_collection_list_selected)
        ll.addWidget(self._coll_list)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("Save", clicked=_slot(self._save_client_collection)))
        bl.addWidget(QPushButton("Delete", clicked=_slot(self._delete_client_collection)))
        ll.addLayout(bl)

        splitter.addWidget(left)

        # ── 右侧: 表单 ────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        # 协议类型切换
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>协议类型:</b>"))
        self._client_proto_combo = QComboBox()
        self._client_proto_combo.addItem("TCP", "tcp_client")
        self._client_proto_combo.addItem("WebSocket", "ws_client")
        self._client_proto_combo.currentIndexChanged.connect(self._on_client_proto_changed)
        top.addWidget(self._client_proto_combo)
        top.addStretch()
        rl.addLayout(top)

        # 连接参数 (TCP / WS 切换)
        conn = QGroupBox("连接参数")
        cf = QFormLayout(conn)

        # TCP 参数页
        self._tcp_params = QWidget()
        tf = QFormLayout(self._tcp_params)
        tf.setContentsMargins(0, 0, 0, 0)
        self._tcp_ip = QLineEdit("127.0.0.1")
        tf.addRow("目标 IP:", self._tcp_ip)
        self._tcp_port = QSpinBox()
        self._tcp_port.setRange(1, 65535)
        self._tcp_port.setValue(35126)
        tf.addRow("端口:", self._tcp_port)
        self._tcp_enc = QComboBox()
        self._tcp_enc.addItems(ENCODINGS)
        self._tcp_enc.setCurrentText("UTF-8")
        tf.addRow("编码:", self._tcp_enc)
        self._tcp_hl = QSpinBox()
        self._tcp_hl.setRange(0, 20)
        self._tcp_hl.setValue(5)
        self._tcp_hl.setSuffix(" 位")
        self._tcp_hl.setToolTip("0=原始模式（无长度头）")
        tf.addRow("HeadLen:", self._tcp_hl)
        self._tcp_timeout = QDoubleSpinBox()
        self._tcp_timeout.setRange(0.1, 60)
        self._tcp_timeout.setValue(5.0)
        self._tcp_timeout.setSingleStep(0.5)
        self._tcp_timeout.setSuffix(" s")
        tf.addRow("超时:", self._tcp_timeout)

        # WS 参数页
        self._ws_params = QWidget()
        wf = QFormLayout(self._ws_params)
        wf.setContentsMargins(0, 0, 0, 0)
        self._ws_url = QLineEdit("ws://127.0.0.1:35126/ws")
        self._ws_url.setPlaceholderText("ws://host:port/path")
        wf.addRow("URL:", self._ws_url)
        self._ws_timeout = QDoubleSpinBox()
        self._ws_timeout.setRange(0.1, 60)
        self._ws_timeout.setValue(5.0)
        self._ws_timeout.setSingleStep(0.5)
        self._ws_timeout.setSuffix(" s")
        wf.addRow("超时:", self._ws_timeout)
        self._ws_ssl = QCheckBox("SSL 验证")
        self._ws_ssl.setChecked(True)
        wf.addRow("", self._ws_ssl)

        self._client_param_stack = QStackedWidget()
        self._client_param_stack.addWidget(self._tcp_params)
        self._client_param_stack.addWidget(self._ws_params)
        cf.addRow(self._client_param_stack)
        rl.addWidget(conn)

        # 发送消息
        send = QGroupBox("发送消息")
        sl = QVBoxLayout(send)
        self._client_send = QPlainTextEdit()
        self._client_send.setPlaceholderText("输入要发送的报文...")
        self._client_send.setMinimumHeight(80)
        self._client_send.textChanged.connect(self._update_client_length_label)
        sl.addWidget(self._client_send)
        self._client_len_label = QLabel("报文长度: 0 字节, 长度头: 00000")
        sl.addWidget(self._client_len_label)
        br = QHBoxLayout()
        self._client_send_btn = QPushButton("Send")
        self._client_send_btn.setMinimumWidth(100)
        self._client_send_btn.clicked.connect(self._client_send_message)
        br.addWidget(self._client_send_btn)
        br.addWidget(QPushButton("清空", clicked=self._client_send.clear))
        br.addStretch()
        sl.addLayout(br)
        rl.addWidget(send)

        # 响应
        resp = QGroupBox("响应")
        rl2 = QVBoxLayout(resp)
        self._client_resp = QPlainTextEdit()
        self._client_resp.setReadOnly(True)
        self._client_resp.setPlaceholderText("响应将显示在这里...")
        self._client_resp.setMinimumHeight(80)
        rl2.addWidget(self._client_resp)
        rl2.addWidget(QPushButton("清空响应", clicked=self._client_resp.clear))
        rl.addWidget(resp)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 600])
        outer.addWidget(splitter)

        self._refresh_collection_list()
        return tab

    def _current_client_proto(self) -> str:
        return self._client_proto_combo.currentData()

    def _on_client_proto_changed(self, idx: int):
        proto = self._current_client_proto()
        self._client_param_stack.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._refresh_collection_list()
        self._update_client_length_label()

    def _update_client_length_label(self):
        proto = self._current_client_proto()
        if proto == "tcp_client":
            msg = self._client_send.toPlainText()
            enc = self._tcp_enc.currentText()
            hl = self._tcp_hl.value()
            try:
                nb = len(msg.encode(enc))
                hdr = compute_length_header(msg, enc, hl)
                self._client_len_label.setText(f"报文长度: {nb} 字节, 长度头: {hdr}")
            except (UnicodeEncodeError, UnicodeDecodeError):
                self._client_len_label.setText("编码错误")
        else:
            msg = self._client_send.toPlainText()
            self._client_len_label.setText(f"消息长度: {len(msg)} 字符")

    def _client_send_message(self):
        if self._client_worker and self._client_worker.isRunning():
            QMessageBox.information(self, "提示", "有请求正在进行中。")
            return
        msg = self._client_send.toPlainText()
        if not msg:
            QMessageBox.information(self, "提示", "请输入要发送的消息。")
            return

        self._client_send_btn.setEnabled(False)
        self._client_send_btn.setText("发送中...")

        proto = self._current_client_proto()
        if proto == "tcp_client":
            self._client_worker = TcpClientWorker(
                ip=self._tcp_ip.text().strip(),
                port=self._tcp_port.value(),
                message=msg,
                encoding=self._tcp_enc.currentText(),
                head_len=self._tcp_hl.value(),
                timeout=self._tcp_timeout.value(),
            )
        else:
            self._client_worker = WsClientWorker(
                url=self._ws_url.text().strip(),
                message=msg,
                timeout=self._ws_timeout.value(),
            )
        self._client_worker.finished.connect(self._on_client_done)
        self._client_worker.start()

    def _on_client_done(self, success: bool, response: str):
        self._client_send_btn.setEnabled(True)
        self._client_send_btn.setText("Send")
        ts = datetime.now().strftime("%H:%M:%S")
        tag = "OK" if success else "FAIL"
        self._client_resp.appendPlainText(f"[{ts}] {tag}:\n{response}")

    # ── 集合列表管理 ─────────────────────────────────────────

    def _refresh_collection_list(self):
        """刷新集合列表。"""
        proto = self._current_client_proto()
        self._coll_list.blockSignals(True)
        self._coll_list.clear()

        # "新建测试" 占位项
        new_item = QListWidgetItem("+ 新建测试")
        new_item.setData(Qt.UserRole, None)
        new_item.setForeground(Qt.gray)
        self._coll_list.addItem(new_item)

        for c in self._db.get_all_protocol_collections(proto):
            label = f"{c.name}\n{c.target_ip}:{c.target_port}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c.id)
            self._coll_list.addItem(item)

        self._coll_list.blockSignals(False)

        # 应用搜索过滤
        search = self._coll_search.text().strip().lower()
        if search:
            self._filter_collection_list(search)

    def _filter_collection_list(self, text: str):
        search = text.strip().lower()
        for row in range(self._coll_list.count()):
            item = self._coll_list.item(row)
            if item:
                # 不过滤 "新建测试" 项
                if item.data(Qt.UserRole) is None:
                    item.setHidden(False)
                else:
                    item.setHidden(search not in item.text().lower())

    def _on_collection_list_selected(self, row: int):
        if row < 0:
            return
        item = self._coll_list.item(row)
        if not item:
            return
        cid = item.data(Qt.UserRole)
        if cid is None:  # "+ 新建测试"
            return

        coll = self._db.get_protocol_collection(cid)
        if not coll:
            return

        proto = coll.protocol_type
        idx = 0 if proto == "tcp_client" else 1
        self._client_proto_combo.setCurrentIndex(idx)

        if proto == "tcp_client":
            self._tcp_ip.setText(coll.target_ip)
            self._tcp_port.setValue(coll.target_port)
            self._tcp_enc.setCurrentText(coll.encoding)
            self._tcp_hl.setValue(coll.head_length)
            self._tcp_timeout.setValue(coll.timeout)
        else:
            scheme = "wss" if coll.ws_use_ssl else "ws"
            url = f"{scheme}://{coll.target_ip}:{coll.target_port}{coll.ws_path or '/'}"
            self._ws_url.setText(url)
            self._ws_timeout.setValue(coll.timeout)
            self._ws_ssl.setChecked(coll.ws_use_ssl)

        msgs = self._db.get_protocol_messages(cid)
        send = next((m.message for m in msgs if m.direction == "send"), "")
        self._client_send.setPlainText(send)

    def _save_client_collection(self):
        name, ok = QInputDialog.getText(self, "保存测试集合", "集合名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        proto = self._current_client_proto()

        if proto == "tcp_client":
            cid = self._db.add_protocol_collection(
                name=name, protocol_type="tcp_client",
                target_ip=self._tcp_ip.text().strip(),
                target_port=self._tcp_port.value(),
                encoding=self._tcp_enc.currentText(),
                head_length=self._tcp_hl.value(),
                timeout=self._tcp_timeout.value(),
            )
        else:
            ip, port, path, ssl = _parse_ws_url(self._ws_url.text().strip())
            cid = self._db.add_protocol_collection(
                name=name, protocol_type="ws_client",
                target_ip=ip, target_port=port,
                timeout=self._ws_timeout.value(),
                ws_path=path, ws_use_ssl=ssl,
            )

        batch = []
        sm = self._client_send.toPlainText()
        rm = self._client_resp.toPlainText()
        if sm:
            batch.append((cid, "send", sm, 0))
        if rm:
            batch.append((cid, "expected_response", rm, 1))
        if batch:
            self._db.save_protocol_messages_batch(batch)

        self._refresh_collection_list()
        # 选中刚保存的项
        for i in range(self._coll_list.count()):
            if self._coll_list.item(i).data(Qt.UserRole) == cid:
                self._coll_list.setCurrentRow(i)
                break
        QMessageBox.information(self, "已保存", f"集合 [{name}] 已保存。")

    def _delete_client_collection(self):
        item = self._coll_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请从列表中选择一个集合。")
            return
        cid = item.data(Qt.UserRole)
        if cid is None:
            return
        coll = self._db.get_protocol_collection(cid)
        if not coll:
            return
        r = QMessageBox.question(
            self, "确认删除", f"确定要删除集合 [{coll.name}] 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self._db.delete_protocol_collection(cid)
            self._refresh_collection_list()

    # ═══════════════════════════════════════════════════════════
    # 服务端标签页
    # ═══════════════════════════════════════════════════════════

    def _build_server_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>协议类型:</b>"))
        self._server_proto_combo = QComboBox()
        self._server_proto_combo.addItem("TCP", "tcp_server")
        self._server_proto_combo.addItem("WebSocket", "ws_server")
        self._server_proto_combo.currentIndexChanged.connect(
            _slot(self._refresh_server_table)
        )
        top.addWidget(self._server_proto_combo)
        top.addStretch()
        layout.addLayout(top)

        tbl = QGroupBox("监听器管理")
        tll = QVBoxLayout(tbl)
        self._server_table = QTableWidget()
        self._server_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._server_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._server_table.setAlternatingRowColors(True)
        self._server_table.verticalHeader().setVisible(False)
        tll.addWidget(self._server_table)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("+ 添加", clicked=_slot(self._add_server)))
        bl.addWidget(QPushButton("编辑", clicked=_slot(self._edit_server)))
        bl.addWidget(QPushButton("删除", clicked=_slot(self._delete_server)))
        bl.addStretch()
        bl.addWidget(QPushButton("全部停止", clicked=self._stop_all_current_servers))
        tll.addLayout(bl)
        layout.addWidget(tbl)

        log = QGroupBox("消息日志")
        ll = QVBoxLayout(log)
        self._server_log = QPlainTextEdit()
        self._server_log.setReadOnly(True)
        self._server_log.setMaximumBlockCount(5000)
        self._server_log.setPlaceholderText("客户端连接和消息将显示在这里...")
        ll.addWidget(self._server_log)
        self._server_status = QLabel("共 0 个监听器, 0 个运行中")
        ll.addWidget(self._server_status)
        ll.addWidget(QPushButton("清空日志", clicked=self._server_log.clear))
        layout.addWidget(log)

        self._refresh_server_table()
        return tab

    def _current_server_proto(self) -> str:
        return self._server_proto_combo.currentData()

    def _current_workers(self) -> dict:
        return (self._tcp_workers if self._current_server_proto() == "tcp_server"
                else self._ws_workers)

    def _stop_all_current_servers(self):
        for w in list(self._current_workers().values()):
            w.stop_server()
        self._current_workers().clear()
        self._refresh_server_table()
        self._server_log.appendPlainText("所有监听器已停止")

    def _refresh_server_table(self):
        st = self._current_server_proto()
        workers = self._current_workers()
        table = self._server_table
        is_tcp = st == "tcp_server"
        if is_tcp:
            cols = ["名称", "监听地址", "端口", "编码", "HeadLen", "响应模式", "操作"]
        else:
            cols = ["名称", "监听地址", "端口", "路径", "响应模式", "操作"]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(len(cols) - 1, QHeaderView.Fixed)
        table.setColumnWidth(len(cols) - 1, 80)
        table.setRowCount(0)
        servers = self._db.get_all_protocol_servers(st)
        running = 0
        for row, srv in enumerate(servers):
            table.insertRow(row)
            ni = QTableWidgetItem(srv.name)
            ni.setData(Qt.UserRole, srv.id)
            table.setItem(row, 0, ni)
            table.setItem(row, 1, QTableWidgetItem(srv.ip))
            pi = QTableWidgetItem(str(srv.port))
            pi.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 2, pi)
            if is_tcp:
                table.setItem(row, 3, QTableWidgetItem(srv.encoding))
                hi = QTableWidgetItem(str(srv.head_length))
                hi.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, 4, hi)
                mode = "回显" if srv.response_mode == "echo" else "固定"
                table.setItem(row, 5, QTableWidgetItem(mode))
            else:
                table.setItem(row, 3, QTableWidgetItem(srv.ws_path))
                mode = "回显" if srv.response_mode == "echo" else "固定"
                table.setItem(row, 4, QTableWidgetItem(mode))
            if srv.id in workers:
                running += 1
            btn = QPushButton("Stop" if srv.id in workers else "Start")
            btn.setStyleSheet(
                "color: #e74c3c;" if srv.id in workers else "color: #27ae60;"
            )
            btn.clicked.connect(partial(self._toggle_server, srv))
            table.setCellWidget(row, len(cols) - 1, btn)
        self._server_status.setText(f"共 {len(servers)} 个监听器, {running} 个运行中")

    def _toggle_server(self, server, _checked=None):
        from portcheck.database import ProtocolServer
        srv: ProtocolServer = server
        st = srv.server_type
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        if srv.id in workers:
            w = workers.pop(srv.id)
            w.stop_server()
            self._server_log.appendPlainText(f"Stop [{srv.name}] {srv.ip}:{srv.port}")
        else:
            for sid in workers:
                other = self._db.get_protocol_server(sid)
                if other and other.port == srv.port:
                    QMessageBox.warning(self, "端口冲突",
                                        f"端口 {srv.port} 已被 [{other.name}] 占用。")
                    return
            if st == "tcp_server":
                w = TcpServerWorker(
                    server_id=srv.id, ip=srv.ip, port=srv.port,
                    encoding=srv.encoding, head_len=srv.head_length,
                    response_mode=srv.response_mode,
                    response_message=srv.response_message,
                )
                w.message_received.connect(
                    partial(self._on_server_message, st, srv.name)
                )
            else:
                w = WsServerWorker(
                    server_id=srv.id, ip=srv.ip, port=srv.port,
                    path=srv.ws_path,
                    response_mode=srv.response_mode,
                    response_message=srv.response_message,
                )
                w.message_received.connect(
                    partial(self._on_server_message, st, srv.name, "")
                )
                w.client_event.connect(
                    partial(self._server_log.appendPlainText)
                )
            w.status_changed.connect(partial(self._server_log.appendPlainText))
            w.error_occurred.connect(
                lambda err: self._server_log.appendPlainText(f"[ERR] {err}")
            )
            w.finished.connect(partial(self._on_worker_finished, st, srv.id))
            workers[srv.id] = w
            w.start()
            self._server_log.appendPlainText(f"Start [{srv.name}] {srv.ip}:{srv.port}")
        self._refresh_server_table()

    def _on_worker_finished(self, server_type: str, server_id: int):
        workers = self._tcp_workers if server_type == "tcp_server" else self._ws_workers
        workers.pop(server_id, None)
        self._refresh_server_table()

    def _on_server_message(self, st: str, name: str,
                           addr: str = "", msg: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        a = f" [{addr}]" if addr else ""
        self._server_log.appendPlainText(f"[{ts}] [{name}]{a} RECV:\n{msg}")

    def _add_server(self):
        st = self._current_server_proto()
        dlg = ServerDialog("添加监听器", st, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.add_protocol_server(
                name=d["name"], server_type=st, ip=d["ip"], port=d["port"],
                encoding=d.get("encoding", "UTF-8"),
                head_length=d.get("head_length", 0),
                ws_path=d.get("ws_path", "/"),
                response_mode=d["response_mode"],
                response_message=d["response_message"],
            )
            self._refresh_server_table()

    def _edit_server(self):
        st = self._current_server_proto()
        table = self._server_table
        rows = set(i.row() for i in table.selectedIndexes())
        if not rows:
            QMessageBox.information(self, "提示", "请选择一条记录。")
            return
        row = rows.pop()
        item = table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        workers = self._current_workers()
        if sid in workers:
            QMessageBox.warning(self, "提示", "请先停止该监听器再编辑。")
            return
        data = {
            "name": srv.name, "ip": srv.ip, "port": srv.port,
            "encoding": srv.encoding, "head_length": srv.head_length,
            "ws_path": srv.ws_path, "response_mode": srv.response_mode,
            "response_message": srv.response_message,
        }
        dlg = ServerDialog("编辑监听器", st, data, parent=self)
        if dlg.exec() == QDialog.Accepted:
            d = dlg.get_data()
            self._db.update_protocol_server(
                server_id=sid, name=d["name"], server_type=st,
                ip=d["ip"], port=d["port"],
                encoding=d.get("encoding", "UTF-8"),
                head_length=d.get("head_length", 0),
                ws_path=d.get("ws_path", "/"),
                response_mode=d["response_mode"],
                response_message=d["response_message"],
            )
            self._refresh_server_table()

    def _delete_server(self):
        table = self._server_table
        rows = set(i.row() for i in table.selectedIndexes())
        if not rows:
            QMessageBox.information(self, "提示", "请选择一条记录。")
            return
        row = rows.pop()
        item = table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        if sid in self._current_workers():
            QMessageBox.warning(self, "提示", "请先停止该监听器再删除。")
            return
        r = QMessageBox.question(
            self, "确认删除", f"确定要删除监听器 [{srv.name}] 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self._db.delete_protocol_server(sid)
            self._refresh_server_table()


# ── URL 解析 ────────────────────────────────────────────────


def _parse_ws_url(url: str) -> tuple[str, int, str, bool]:
    url = url.strip()
    use_ssl = url.startswith("wss://")
    if url.startswith("ws://"):
        url = url[5:]
    elif url.startswith("wss://"):
        url = url[6:]
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host = url
        path = "/"
    if ":" in host:
        ip, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 443 if use_ssl else 80
    else:
        ip = host
        port = 443 if use_ssl else 80
    return ip, port, path, use_ssl
