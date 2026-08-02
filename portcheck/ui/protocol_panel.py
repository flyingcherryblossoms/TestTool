"""协议测试面板 —— 测试集合 / 客户端 / 服务端 / 测试历史 四个子标签页。"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from PySide6.QtCore import Qt
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
from portcheck.csv_handler import export_results_to_csv
from portcheck.excel_handler import export_results_to_excel


ENCODINGS = ["UTF-8", "GBK", "GB2312", "ISO-8859-1", "ASCII"]


def _slot(fn, *args):
    def handler(*_sig_args):
        fn(*args)
    return handler


# ── 服务端对话框 ──────────────────────────────────────────


class ServerDialog(QDialog):
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


# ── 测试集合管理标签页 (Subtab 1) ──────────────────────────


class _CollectionManagerTab(QWidget):
    """协议测试集合管理 —— 左侧集合列表 + 右侧目标表格。"""

    collection_selected = Signal(object)  # ProtocolCollection | None
    target_test_requested = Signal(object, object)  # collection, target

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._current_coll = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧: 集合列表
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addWidget(QLabel("<b>测试集合</b>"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_list)
        ll.addWidget(self._search)

        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_menu)
        self._list.currentRowChanged.connect(self._on_list_selected)
        ll.addWidget(self._list)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("新建", clicked=self._on_new))
        bl.addWidget(QPushButton("编辑", clicked=self._on_edit))
        bl.addWidget(QPushButton("删除", clicked=self._on_delete))
        ll.addLayout(bl)

        splitter.addWidget(left)

        # 右侧: 目标表格
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.addWidget(QLabel("<b>目标列表</b>"))

        self._target_table = QTableWidget()
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._target_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._target_table.setAlternatingRowColors(True)
        self._target_table.verticalHeader().setVisible(False)
        self._target_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._target_table.customContextMenuRequested.connect(self._on_target_menu)
        rl.addWidget(self._target_table)

        tbl = QHBoxLayout()
        tbl.addWidget(QPushButton("添加目标", clicked=self._on_add_target))
        tbl.addWidget(QPushButton("编辑", clicked=self._on_edit_target))
        tbl.addWidget(QPushButton("删除", clicked=self._on_delete_target))
        tbl.addStretch()
        tbl.addWidget(QPushButton("▸ 测试", clicked=self._on_test_target))
        rl.addLayout(tbl)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 600])
        layout.addWidget(splitter)

        self._refresh_list()

    def _refresh_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        self._list.addItem("-- 全部 --")
        self._list.item(0).setData(Qt.UserRole, None)
        for c in self._db.get_all_protocol_collections():
            label = f"{c.name}\n({c.protocol_type})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c.id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.currentRow() < 0:
            self._list.setCurrentRow(0)

    def _filter_list(self, text: str):
        s = text.strip().lower()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item:
                item.setHidden(s not in item.text().lower() if s else False)

    def _on_list_selected(self, row: int):
        if row < 0:
            return
        item = self._list.item(row)
        if not item:
            return
        cid = item.data(Qt.UserRole)
        if cid is None:
            self._current_coll = None
            self._target_table.setRowCount(0)
            self._target_table.setColumnCount(3)
            self._target_table.setHorizontalHeaderLabels(["IP", "端口", "描述"])
            self.collection_selected.emit(None)
            return
        self._current_coll = self._db.get_protocol_collection(cid)
        self.collection_selected.emit(self._current_coll)
        self._refresh_targets()

    def _refresh_targets(self):
        table = self._target_table
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["#", "IP", "端口", "描述"])
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        table.setColumnWidth(0, 30)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        table.setColumnWidth(2, 60)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)

        cid = self._current_coll.id if self._current_coll else None
        if cid is None:
            table.setRowCount(0)
            return
        targets = self._db.get_protocol_targets(cid)
        table.setRowCount(len(targets))
        for row, t in enumerate(targets):
            ni = QTableWidgetItem(str(row + 1))
            ni.setTextAlignment(Qt.AlignCenter)
            ni.setData(Qt.UserRole, t.id)
            table.setItem(row, 0, ni)
            table.setItem(row, 1, QTableWidgetItem(t.ip))
            pi = QTableWidgetItem(str(t.port))
            pi.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 2, pi)
            table.setItem(row, 3, QTableWidgetItem(t.description))

    # ── 集合操作 ──────────────────────────────────────────

    def _on_list_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item or item.data(Qt.UserRole) is None:
            return
        menu = QMenu(self)
        menu.addAction("编辑", self._on_edit)
        menu.addAction("删除", self._on_delete)
        menu.exec(self._list.mapToGlobal(pos))

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "新建测试集合", "集合名称:")
        if not ok or not name.strip():
            return
        cid = self._db.add_protocol_collection(
            name=name.strip(), protocol_type="tcp_client"
        )
        self._refresh_list()
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == cid:
                self._list.setCurrentRow(i)
                break

    def _on_edit(self):
        if not self._current_coll:
            return
        name, ok = QInputDialog.getText(
            self, "编辑集合", "集合名称:", text=self._current_coll.name
        )
        if ok and name.strip():
            self._db.update_protocol_collection(
                self._current_coll.id, name=name.strip()
            )
            self._refresh_list()

    def _on_delete(self):
        if not self._current_coll:
            return
        r = QMessageBox.question(
            self, "确认删除",
            f"确定要删除集合 [{self._current_coll.name}] 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self._db.delete_protocol_collection(self._current_coll.id)
            self._current_coll = None
            self._refresh_list()

    # ── 目标操作 ──────────────────────────────────────────

    def _on_target_menu(self, pos):
        row = self._target_table.rowAt(pos.y())
        if row < 0 or not self._current_coll:
            return
        menu = QMenu(self)
        menu.addAction("编辑", self._on_edit_target)
        menu.addAction("删除", self._on_delete_target)
        menu.addSeparator()
        menu.addAction("测试此目标", self._on_test_target)
        menu.exec(self._target_table.mapToGlobal(pos))

    def _on_add_target(self):
        if not self._current_coll:
            QMessageBox.information(self, "提示", "请先选择或新建一个集合。")
            return
        dlg = _TargetDialog("添加目标", parent=self)
        if dlg.exec() == QDialog.Accepted:
            ip, port, desc = dlg.get_data()
            self._db.add_protocol_target(self._current_coll.id, ip, port, desc)
            self._refresh_targets()

    def _on_edit_target(self):
        row = self._target_table.currentRow()
        if row < 0:
            return
        tid = self._target_table.item(row, 0).data(Qt.UserRole)
        ip = self._target_table.item(row, 1).text()
        port = int(self._target_table.item(row, 2).text())
        desc = self._target_table.item(row, 3).text() if self._target_table.item(row, 3) else ""
        dlg = _TargetDialog("编辑目标", ip=ip, port=port, desc=desc, parent=self)
        if dlg.exec() == QDialog.Accepted:
            nip, nport, ndesc = dlg.get_data()
            # 简单实现: 删除旧的，添加新的
            self._db.delete_protocol_target(tid)
            self._db.add_protocol_target(self._current_coll.id, nip, nport, ndesc)
            self._refresh_targets()

    def _on_delete_target(self):
        rows = set(i.row() for i in self._target_table.selectedIndexes())
        if not rows:
            return
        r = QMessageBox.question(
            self, "确认删除", f"确定要删除选中的 {len(rows)} 个目标吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            for row in rows:
                tid = self._target_table.item(row, 0).data(Qt.UserRole)
                self._db.delete_protocol_target(tid)
            self._refresh_targets()

    def _on_test_target(self):
        if not self._current_coll:
            return
        row = self._target_table.currentRow()
        if row < 0:
            return
        tid = self._target_table.item(row, 0).data(Qt.UserRole)
        ip = self._target_table.item(row, 1).text()
        port = int(self._target_table.item(row, 2).text())
        from portcheck.database import ProtocolTarget
        target = ProtocolTarget(id=tid, collection_id=self._current_coll.id, ip=ip, port=port)
        self.target_test_requested.emit(self._current_coll, target)


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


# ── 协议测试历史 (Subtab 4) ────────────────────────────────


class _ProtocolHistoryTab(QWidget):
    """协议测试历史 —— 列表展示 + 详情 + 筛选导出。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_sessions = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 筛选栏
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

        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "时间", "集合", "协议", "目标", "端口", "结果", "详情"
        ])
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
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        self._table.setColumnWidth(6, 50)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table)

        # 详情
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("点击「详情」列查看请求和响应...")
        self._detail.setMaximumHeight(150)
        layout.addWidget(self._detail)

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
            ri.setForeground(
                Qt.green if s.success else Qt.red
            )
            self._table.setItem(row, 5, ri)
            btn = QPushButton("→")
            btn.setMaximumWidth(40)
            btn.clicked.connect(partial(self._show_detail, row))
            self._table.setCellWidget(row, 6, btn)

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
            detail = (
                f"请求:\n"
                f"---\n"
                f"响应 ({'OK' if sess.success else 'FAIL'}):\n{sess.response}"
            )
            if sess.error_msg:
                detail += f"\n\n错误:\n{sess.error_msg}"
            self._detail.setPlainText(detail)

    def _show_detail(self, row: int):
        self._on_cell_clicked(row, 6)

    def _export(self):
        proto = self._proto_filter.currentData()
        sessions = self._all_sessions
        if not sessions:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        fp, _ = QFileDialog.getSaveFileName(
            self, "导出测试历史", "protocol_history.csv",
            "CSV (*.csv);;Excel (*.xlsx)"
        )
        if not fp:
            return
        data = [{
            "started_at": s.started_at,
            "collection": s.collection_name,
            "protocol": s.protocol_type,
            "target_ip": s.target_ip,
            "target_port": str(s.target_port),
            "success": "OK" if s.success else "FAIL",
            "response": s.response,
            "error": s.error_msg,
        } for s in sessions]
        if fp.endswith(".csv"):
            ok, err = export_results_to_csv(fp, data)
        else:
            ok, err = export_results_to_excel(fp, data)
        if ok:
            QMessageBox.information(self, "导出完成", f"已导出 {len(data)} 条记录。")
        else:
            QMessageBox.critical(self, "导出失败", str(err))


# ── 协议测试主面板 ──────────────────────────────────────────


class ProtocolPanel(QWidget):

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._tcp_workers: dict[int, TcpServerWorker] = {}
        self._ws_workers: dict[int, WsServerWorker] = {}
        self._client_worker = None
        self._current_test_target = None  # (collection, target) for history
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._sub_tabs = QTabWidget()

        # Subtab 1: 测试集合
        self._coll_mgr = _CollectionManagerTab(self._db)
        self._coll_mgr.collection_selected.connect(self._on_collection_loaded)
        self._coll_mgr.target_test_requested.connect(self._on_target_test)
        self._sub_tabs.addTab(self._coll_mgr, "测试集合")

        # Subtab 2: 客户端
        self._sub_tabs.addTab(self._build_client_tab(), "客户端")

        # Subtab 3: 服务端
        self._sub_tabs.addTab(self._build_server_tab(), "服务端")

        # Subtab 4: 测试历史
        self._history_tab = _ProtocolHistoryTab(self._db)
        self._sub_tabs.addTab(self._history_tab, "测试历史")
        self._sub_tabs.currentChanged.connect(self._on_subtab_changed)

        layout.addWidget(self._sub_tabs)

    def _on_subtab_changed(self, idx: int):
        if idx == 3:  # 测试历史
            self._history_tab.refresh()

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
        self._sub_tabs.setCurrentIndex(1)
        self._client_proto_combo.setCurrentIndex(0)
        self._tcp_ip.setText(ip)
        self._tcp_port.setValue(port)

    # ── 集合 → 客户端联动 ────────────────────────────────────

    def _on_collection_loaded(self, coll):
        if coll is None:
            return
        proto = coll.protocol_type
        idx = 0 if proto == "tcp_client" else 1
        self._client_proto_combo.setCurrentIndex(idx)
        if proto == "tcp_client":
            self._tcp_enc.setCurrentText(coll.encoding)
            self._tcp_hl.setValue(coll.head_length)
            self._tcp_timeout.setValue(coll.timeout)
        else:
            scheme = "wss" if coll.ws_use_ssl else "ws"
            self._ws_url.setText(f"{scheme}://{coll.target_ip}:{coll.target_port}{coll.ws_path or '/'}")
            self._ws_timeout.setValue(coll.timeout)
            self._ws_ssl.setChecked(coll.ws_use_ssl)
        msgs = self._db.get_protocol_messages(coll.id)
        send = next((m.message for m in msgs if m.direction == "send"), "")
        self._client_send.setPlainText(send)

    def _on_target_test(self, coll, target):
        # 预填客户端参数并切换到客户端标签页
        proto = coll.protocol_type
        idx = 0 if proto == "tcp_client" else 1
        self._client_proto_combo.setCurrentIndex(idx)
        if proto == "tcp_client":
            self._tcp_ip.setText(target.ip)
            self._tcp_port.setValue(target.port)
            self._tcp_enc.setCurrentText(coll.encoding)
            self._tcp_hl.setValue(coll.head_length)
            self._tcp_timeout.setValue(coll.timeout)
        else:
            scheme = "wss" if coll.ws_use_ssl else "ws"
            self._ws_url.setText(
                f"{scheme}://{target.ip}:{target.port}{coll.ws_path or '/'}"
            )
            self._ws_timeout.setValue(coll.timeout)
            self._ws_ssl.setChecked(coll.ws_use_ssl)
        # 加载集合的消息模板
        msgs = self._db.get_protocol_messages(coll.id)
        send = next((m.message for m in msgs if m.direction == "send"), "")
        self._client_send.setPlainText(send)
        self._current_test_target = (coll, target)
        self._sub_tabs.setCurrentIndex(1)  # 切换到客户端

    # ═══════════════════════════════════════════════════════════
    # 客户端标签页
    # ═══════════════════════════════════════════════════════════

    def _build_client_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 协议类型
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>协议类型:</b>"))
        self._client_proto_combo = QComboBox()
        self._client_proto_combo.addItem("TCP", "tcp_client")
        self._client_proto_combo.addItem("WebSocket", "ws_client")
        self._client_proto_combo.currentIndexChanged.connect(self._on_client_proto)
        top.addWidget(self._client_proto_combo)
        top.addStretch()
        layout.addLayout(top)

        # 连接参数
        conn = QGroupBox("连接参数")
        cf = QFormLayout(conn)
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
        self._tcp_hl.setToolTip("0=原始模式")
        tf.addRow("HeadLen:", self._tcp_hl)
        self._tcp_timeout = QDoubleSpinBox()
        self._tcp_timeout.setRange(0.1, 60)
        self._tcp_timeout.setValue(5.0)
        self._tcp_timeout.setSingleStep(0.5)
        self._tcp_timeout.setSuffix(" s")
        tf.addRow("超时:", self._tcp_timeout)

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

        self._param_stack = QStackedWidget()
        self._param_stack.addWidget(self._tcp_params)
        self._param_stack.addWidget(self._ws_params)
        cf.addRow(self._param_stack)
        layout.addWidget(conn)

        # 发送消息
        send = QGroupBox("发送消息")
        sl = QVBoxLayout(send)
        self._client_send = QPlainTextEdit()
        self._client_send.setPlaceholderText("输入要发送的报文...")
        self._client_send.setMinimumHeight(80)
        self._client_send.textChanged.connect(self._update_len)
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
        layout.addWidget(send)

        # 响应
        resp = QGroupBox("响应")
        rl = QVBoxLayout(resp)
        self._client_resp = QPlainTextEdit()
        self._client_resp.setReadOnly(True)
        self._client_resp.setPlaceholderText("响应将显示在这里...")
        self._client_resp.setMinimumHeight(80)
        rl.addWidget(self._client_resp)
        rl.addWidget(QPushButton("清空响应", clicked=self._client_resp.clear))
        layout.addWidget(resp)

        return tab

    def _current_client_proto(self) -> str:
        return self._client_proto_combo.currentData()

    def _on_client_proto(self, idx: int):
        proto = self._current_client_proto()
        self._param_stack.setCurrentIndex(0 if proto == "tcp_client" else 1)
        self._update_len()

    def _update_len(self):
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
            self._client_len_label.setText(f"消息长度: {len(self._client_send.toPlainText())} 字符")

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
            ip = self._tcp_ip.text().strip()
            port = self._tcp_port.value()
            self._last_ip, self._last_port = ip, port
            self._client_worker = TcpClientWorker(
                ip=ip, port=port, message=msg,
                encoding=self._tcp_enc.currentText(),
                head_len=self._tcp_hl.value(),
                timeout=self._tcp_timeout.value(),
            )
        else:
            url = self._ws_url.text().strip()
            self._last_ip, self._last_port = url, 0
            self._client_worker = WsClientWorker(
                url=url, message=msg,
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

        # 记录到测试历史
        coll, target = self._current_test_target or (None, None)
        self._db.add_protocol_test_session(
            collection_id=coll.id if coll else None,
            collection_name=coll.name if coll else "",
            target_id=target.id if target else None,
            protocol_type=self._current_client_proto(),
            target_ip=getattr(self, '_last_ip', ''),
            target_port=getattr(self, '_last_port', 0),
            success=success,
            response=response,
            error_msg="" if success else response,
        )

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
        bl.addWidget(QPushButton("全部停止", clicked=self._stop_all_current))
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

    def _stop_all_current(self):
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
        cols = (["名称", "监听地址", "端口", "编码", "HeadLen", "响应模式", "操作"]
                if is_tcp else
                ["名称", "监听地址", "端口", "路径", "响应模式", "操作"])
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

    def _on_worker_finished(self, st: str, sid: int):
        workers = self._tcp_workers if st == "tcp_server" else self._ws_workers
        workers.pop(sid, None)
        self._refresh_server_table()

    def _on_server_message(self, st, name, addr="", msg=""):
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
            return QMessageBox.information(self, "提示", "请选择一条记录。")
        row = rows.pop()
        item = table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        if sid in self._current_workers():
            return QMessageBox.warning(self, "提示", "请先停止该监听器再编辑。")
        data = dict(name=srv.name, ip=srv.ip, port=srv.port,
                    encoding=srv.encoding, head_length=srv.head_length,
                    ws_path=srv.ws_path, response_mode=srv.response_mode,
                    response_message=srv.response_message)
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
            return QMessageBox.information(self, "提示", "请选择一条记录。")
        row = rows.pop()
        item = table.item(row, 0)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        srv = self._db.get_protocol_server(sid)
        if not srv:
            return
        if sid in self._current_workers():
            return QMessageBox.warning(self, "提示", "请先停止该监听器再删除。")
        r = QMessageBox.question(
            self, "确认删除", f"确定要删除监听器 [{srv.name}] 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self._db.delete_protocol_server(sid)
            self._refresh_server_table()
