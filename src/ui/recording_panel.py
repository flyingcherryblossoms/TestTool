"""联调录制：透明代理、会话详情、人工配对、客户端导出和 Mock 回放。"""
from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from src.database import Database, DEFAULT_PRESET_NAME
from src.proxy import validate_config
from src.recording import ReplayRule
from src.replay import validate_rule
from src.ui.recording_workers import RecordingWorker, ReplayWorker, RecordingExportWorker


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows):
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(str(value)))


class RecordingPanel(QWidget):
    targets_changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.worker = None
        self.replay_worker = None
        self.file_worker = None
        self.active_session = None
        self.sessions, self.exchanges, self.events, self.rules = [], [], [], []
        self._setup_ui()
        self.refresh_sessions()
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        hint = QLabel("在线：业务系统 → 本页监听地址 → 真实服务。离线：停止代理后，在本页启动录制 Mock。\n"
                      "支持明文 TCP / HTTP/1.1 / WebSocket（无压缩）。HTTPS/WSS 不解密；HTTP Host 原样转发。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        grid = QGridLayout()
        self.name = QLineEdit("联调 " + datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.protocol = QComboBox()
        for label, value in (("TCP", "tcp"), ("HTTP/1.1", "http"), ("WebSocket", "ws")):
            self.protocol.addItem(label, value)
        self.listen_host, self.upstream_host = QLineEdit("127.0.0.1"), QLineEdit("127.0.0.1")
        self.listen_port, self.upstream_port = QSpinBox(), QSpinBox()
        for control, value in ((self.listen_port, 9000), (self.upstream_port, 8000)):
            control.setRange(1, 65535)
            control.setValue(value)
        self.encoding = QComboBox()
        self.encoding.setEditable(True)
        self.encoding.addItems(["UTF-8", "GBK", "GB18030", "latin-1"])
        self.framing = QComboBox()
        for label, value in (("ASCII 长度头", "length"), ("分隔符", "delimiter"), ("连接关闭（每方向一条）", "eof")):
            self.framing.addItem(label, value)
        self.head = QSpinBox()
        self.head.setRange(1, 12)
        self.head.setValue(5)
        self.delimiter = QLineEdit("0a")
        self.association = QLineEdit()
        self.association.setPlaceholderText("留空：严格一问一答；如 $.requestId：按 JSON 流水号配对")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 3600)
        self.timeout.setValue(30)
        items = [("会话名称", self.name), ("协议", self.protocol),
                 ("监听地址", self.listen_host), ("监听端口", self.listen_port),
                 ("上游地址", self.upstream_host), ("上游端口", self.upstream_port),
                 ("文本编码", self.encoding), ("TCP 分帧", self.framing),
                 ("长度头位数", self.head), ("分隔符（Hex）", self.delimiter),
                 ("关联字段", self.association), ("空闲超时（秒）", self.timeout)]
        self.config_controls = [widget for _, widget in items]
        for i, (label, widget) in enumerate(items):
            row, col = divmod(i, 2)
            grid.addWidget(QLabel(label), row, col * 2)
            grid.addWidget(widget, row, col * 2 + 1)
        layout.addLayout(grid)
        self.protocol.currentIndexChanged.connect(self._framing_enabled)
        self.framing.currentIndexChanged.connect(self._framing_enabled)
        self._framing_enabled()
        bar = QHBoxLayout()
        self.start_button = QPushButton("开始代理录制")
        self.stop_button = QPushButton("停止并保存")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        bar.addWidget(self.start_button)
        bar.addWidget(self.stop_button)
        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.session_table = _table(["会话", "状态", "开始时间（UTC）"])
        self.session_table.itemSelectionChanged.connect(self._session_selected)
        left_layout.addWidget(self.session_table)
        buttons = QHBoxLayout()
        for label, slot in (("刷新", self.refresh_sessions), ("加载配置", self._load_config),
                            ("导出 JSON", self._export_json), ("删除", self._delete_session)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("选择会话查看录制交互。顺序关联要求严格一问一答；乱序或主动推送请设置关联字段或人工配对。")
        self.summary.setWordWrap(True)
        right_layout.addWidget(self.summary)
        tabs = QTabWidget()
        exchange_page = QWidget()
        ex_layout = QVBoxLayout(exchange_page)
        self.exchange_table = _table(["来源", "上游", "状态", "关联方式", "耗时 ms"])
        self.exchange_table.itemSelectionChanged.connect(self._exchange_selected)
        ex_layout.addWidget(self.exchange_table)
        ex_buttons = QHBoxLayout()
        for label, slot in (("生成客户端用例", self._export_client), ("保存为固定 Mock", self._export_mock),
                            ("生成回放规则", self._create_rule)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            ex_buttons.addWidget(button)
        ex_layout.addLayout(ex_buttons)
        tabs.addTab(exchange_page, "请求 / 响应")
        event_page = QWidget()
        ev_layout = QVBoxLayout(event_page)
        self.event_table = _table(["连接", "方向", "序号", "字节数", "时间"])
        self.event_table.itemSelectionChanged.connect(self._event_selected)
        ev_layout.addWidget(self.event_table)
        pair = QPushButton("人工配对请求与响应")
        pair.clicked.connect(self._pair)
        ev_layout.addWidget(pair)
        tabs.addTab(event_page, "全部消息（含未关联响应）")
        self.connection_table = _table(["来源", "上游", "状态", "错误"])
        tabs.addTab(self.connection_table, "连接")
        rule_page = QWidget()
        rule_layout = QVBoxLayout(rule_page)
        self.rule_table = _table(["规则", "匹配", "字段", "回填字段", "延迟 ms"])
        rule_layout.addWidget(self.rule_table)
        rule_bar = QHBoxLayout()
        self.policy = QComboBox()
        self.policy.addItem("多条命中时报错", "unique")
        self.policy.addItem("多条命中按连接内顺序回放", "sequence")
        rule_bar.addWidget(self.policy)
        self.replay_start = QPushButton("在当前监听地址启动 Mock")
        self.replay_start.clicked.connect(self._start_replay)
        self.replay_stop = QPushButton("停止 Mock")
        self.replay_stop.setEnabled(False)
        self.replay_stop.clicked.connect(self._stop_replay)
        delete_rule = QPushButton("删除规则")
        delete_rule.clicked.connect(self._delete_rule)
        for button in (self.replay_start, self.replay_stop, delete_rule):
            rule_bar.addWidget(button)
        rule_layout.addLayout(rule_bar)
        rule_layout.addWidget(QLabel("HTTP 匹配方法、路径（含查询串）和正文；请求头不参与匹配。规则变更在重启 Mock 后生效。"))
        tabs.addTab(rule_page, "录制 Mock 规则")
        right_layout.addWidget(tabs, 2)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        right_layout.addWidget(self.details, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    def _framing_enabled(self):
        tcp = self.protocol.currentData() == "tcp"
        self.framing.setEnabled(tcp)
        self.head.setEnabled(tcp and self.framing.currentData() == "length")
        self.delimiter.setEnabled(tcp and self.framing.currentData() == "delimiter")

    def _config(self):
        return dict(protocol=self.protocol.currentData(), listen_host=self.listen_host.text().strip(),
                    listen_port=self.listen_port.value(), upstream_host=self.upstream_host.text().strip(),
                    upstream_port=self.upstream_port.value(), encoding=self.encoding.currentText(),
                    framing=self.framing.currentData(), head_length=self.head.value(),
                    delimiter=self.delimiter.text().strip(), association_field=self.association.text().strip(),
                    timeout=self.timeout.value())

    def is_running(self):
        return any(worker and worker.isRunning() for worker in (self.worker, self.replay_worker, self.file_worker))

    def stop_all(self):
        self.timer.stop()
        for worker in (self.worker, self.replay_worker):
            if worker:
                worker.stop()
                worker.wait()
        if self.file_worker:
            self.file_worker.wait()

    def _busy_ui(self):
        busy = self.is_running()
        self.start_button.setEnabled(not busy)
        self.replay_start.setEnabled(not busy)
        self.stop_button.setEnabled(bool(self.worker and self.worker.isRunning()))
        self.replay_stop.setEnabled(bool(self.replay_worker and self.replay_worker.isRunning()))
        for widget in self.config_controls:
            widget.setEnabled(not busy)
        if not busy:
            self._framing_enabled()

    def _start(self):
        if self.is_running():
            return
        sid = None
        try:
            config = self._config()
            validate_config(config)
            name = self.name.text().strip()
            if not name:
                raise ValueError("会话名称不能为空")
            sid = self.db.create_recording_session(name, json.dumps(config, ensure_ascii=False))
            worker = RecordingWorker(self.db, sid, config, self)
            self.worker, self.active_session = worker, sid
            worker.status_changed.connect(self.status.setText)
            worker.error_occurred.connect(self._error)
            worker.finished.connect(self._finished)
            worker.start()
            self.refresh_sessions(select_id=sid)
            self._busy_ui()
        except Exception as exc:
            if sid is not None:
                self.db.finish_recording_session(sid, "incomplete", str(exc))
            self._warning(exc)

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("正在关闭连接并保存录制…")

    def _finished(self):
        worker = self.worker
        error = worker.writer.error or worker.engine.error or worker.engine.recording_error
        self.worker = None
        worker.deleteLater()
        self._busy_ui()
        self.refresh_sessions()
        self.status.setText("代理已停止：" + error if error else "代理已停止，会话已保存")

    def _error(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    def _warning(self, exc):
        QMessageBox.warning(self, "联调录制", str(exc))

    def _selected_session(self):
        row = self.session_table.currentRow()
        return self.sessions[row] if 0 <= row < len(self.sessions) else None

    def _selected_exchange(self):
        row = self.exchange_table.currentRow()
        if not 0 <= row < len(self.exchanges):
            raise ValueError("请先选择一条交互")
        return self.db.get_recording_exchanges(self._selected_session().id, exchange_id=self.exchanges[row].id)[0]

    def refresh_sessions(self, _checked=False, select_id=None):
        selected = self._selected_session()
        selected_id = select_id if select_id is not None else (selected.id if selected else None)
        self.sessions = self.db.get_recording_sessions()
        self.session_table.blockSignals(True)
        _fill(self.session_table, [(s.name, s.status, s.started_at) for s in self.sessions])
        for i, session in enumerate(self.sessions):
            if session.id == selected_id:
                self.session_table.selectRow(i)
                break
        self.session_table.blockSignals(False)
        self._session_selected()

    def _poll(self):
        if self.is_running():
            self.refresh_sessions()

    def _session_selected(self):
        session = self._selected_session()
        selected_ex = self.exchanges[self.exchange_table.currentRow()].id if 0 <= self.exchange_table.currentRow() < len(self.exchanges) else None
        selected_event = self.events[self.event_table.currentRow()].id if 0 <= self.event_table.currentRow() < len(self.events) else None
        self.exchanges = self.db.get_recording_exchanges(session.id, limit=500, preview=True) if session else []
        self.events = self.db.get_recording_events(session.id, limit=500, preview=True) if session else []
        self.rules = self.db.get_recording_rules(session.id, preview=True) if session else []
        self.exchange_table.blockSignals(True)
        self.event_table.blockSignals(True)
        _fill(self.exchange_table, [(e.source, e.upstream, e.status, e.association, "{:.1f}".format(e.elapsed_ms)) for e in self.exchanges])
        _fill(self.event_table, [(e.connection_id[:8], e.direction, e.sequence, e.payload_size,
                                 datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S.%f")[:-3]) for e in self.events])
        _fill(self.rule_table, [(r.name, r.mode, r.fields, r.copy_fields, r.delay_ms) for r in self.rules])
        connections = self.db.get_recording_connections(session.id) if session else []
        _fill(self.connection_table, [(c.source, c.upstream, c.status, c.error) for c in connections])
        for table, objects, eid in ((self.exchange_table, self.exchanges, selected_ex), (self.event_table, self.events, selected_event)):
            for row, obj in enumerate(objects):
                if obj.id == eid:
                    table.selectRow(row)
                    break
            table.blockSignals(False)
        self.summary.setText(("{}：显示最近 {} 条交互 / {} 条消息（各最多 500 条，导出包含全部）。{}\n{}".format(session.name, len(self.exchanges), len(self.events), session.status, session.error)) if session else "选择会话查看详情")
        if session is None:
            self.details.clear()
        elif any(e.id == selected_ex for e in self.exchanges):
            self._exchange_selected()
        else:
            self.details.clear()

    def _display(self, label, payload, meta="{}"):
        session = self._selected_session()
        encoding = json.loads(session.config).get("encoding", "UTF-8") if session else "UTF-8"
        if payload is None:
            return label + "：无响应"
        preview = payload[:65536]
        suffix = "\n（仅预览前 64 KB，原始数据已完整保存）" if len(payload) > len(preview) else ""
        return "{}（{} 字节）\n元数据：{}\n文本：\n{}\nHex：\n{}{}".format(
            label, len(payload), meta, preview.decode(encoding, errors="replace"), preview.hex(" "), suffix)

    def _exchange_selected(self):
        try:
            ex = self._selected_exchange()
        except ValueError:
            return
        self.details.setPlainText(self._display("请求", ex.request, ex.request_meta) + "\n\n" + self._display("响应", ex.response, ex.response_meta))

    def _event_selected(self):
        row = self.event_table.currentRow()
        if 0 <= row < len(self.events):
            event = self.db.get_recording_events(self._selected_session().id, event_id=self.events[row].id)[0]
            self.details.setPlainText(self._display(event.direction, event.payload, event.metadata) + "\n原始线路字节：\n" + event.wire[:65536].hex(" "))

    def _load_config(self):
        session = self._selected_session()
        if session is None or self.is_running():
            return
        cfg = json.loads(session.config)
        self.name.setText(session.name)
        for control, key in ((self.protocol, "protocol"), (self.framing, "framing")):
            control.setCurrentIndex(control.findData(cfg.get(key)))
        for control, key in ((self.listen_host, "listen_host"), (self.upstream_host, "upstream_host"),
                             (self.delimiter, "delimiter"), (self.association, "association_field")):
            control.setText(cfg.get(key, ""))
        for control, key in ((self.listen_port, "listen_port"), (self.upstream_port, "upstream_port"),
                             (self.head, "head_length"), (self.timeout, "timeout")):
            control.setValue(cfg.get(key, control.value()))
        self.encoding.setCurrentText(cfg.get("encoding", "UTF-8"))

    def _editable_session(self):
        session = self._selected_session()
        if session is None:
            raise ValueError("请先选择会话")
        if self.is_running():
            raise ValueError("请先停止代理或 Mock，并等待文件导出完成")
        return session

    def _pair(self):
        try:
            self._editable_session()
            dialog = QDialog(self)
            dialog.setWindowTitle("人工配对")
            layout = QFormLayout(dialog)
            request, response = QComboBox(), QComboBox()
            for event in self.events:
                meta = json.loads(event.metadata)
                if any(meta.get(k) for k in ("control", "handshake", "informational")):
                    continue
                combo = request if event.direction == "request" else response
                text = "{} #{}：{}".format(event.connection_id[:8], event.sequence, event.payload[:80].decode("utf-8", errors="replace"))
                combo.addItem(text, event.id)
            layout.addRow("请求", request)
            layout.addRow("响应", response)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)
            if dialog.exec() == QDialog.Accepted:
                self.db.pair_recording_events(request.currentData(), response.currentData())
                self._session_selected()
        except Exception as exc:
            self._warning(exc)

    def _create_rule(self):
        try:
            session = self._editable_session()
            ex = self._selected_exchange()
            if ex.status != "complete" or ex.response is None:
                raise ValueError("请选择有完整请求响应的交互")
            dialog = QDialog(self)
            dialog.setWindowTitle("生成录制回放规则")
            form = QFormLayout(dialog)
            name = QLineEdit("交互 " + ex.id[:8])
            mode = QComboBox()
            mode.addItem("精确匹配正文", "exact")
            mode.addItem("JSON 字段匹配", "json")
            fields = QLineEdit()
            fields.setPlaceholderText("$.tradeCode,$.orderId")
            copies = QLineEdit()
            copies.setPlaceholderText("$.requestId（同路径从当前请求回填响应）")
            delay = QSpinBox()
            delay.setRange(0, 60000)
            delay.setValue(min(60000, int(ex.elapsed_ms)))
            for label, widget in (("名称", name), ("匹配方式", mode), ("匹配字段（逗号分隔）", fields),
                                  ("回填字段（逗号分隔）", copies), ("延迟（毫秒）", delay)):
                form.addRow(label, widget)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            cfg = json.loads(session.config)
            rule = ReplayRule(0, session.id, name.text().strip(), cfg["protocol"], ex.request, ex.response,
                              ex.request_meta, ex.response_meta, mode.currentData(), fields.text().strip(), copies.text().strip(), delay.value(), 1)
            validate_rule(rule, cfg.get("encoding", "UTF-8"))
            self.db.add_recording_rule(session.id, rule.name, rule.protocol, ex, rule.mode, rule.fields, rule.copy_fields, rule.delay_ms)
            self._session_selected()
        except Exception as exc:
            self._warning(exc)

    def _delete_rule(self):
        try:
            self._editable_session()
            row = self.rule_table.currentRow()
            if 0 <= row < len(self.rules):
                self.db.delete_recording_rule(self.rules[row].id)
                self._session_selected()
        except Exception as exc:
            self._warning(exc)

    def _start_replay(self):
        try:
            session = self._editable_session()
            config = json.loads(session.config)
            config.update(listen_host=self.listen_host.text().strip(), listen_port=self.listen_port.value(), replay_policy=self.policy.currentData())
            worker = ReplayWorker(config, self.db.get_recording_rules(session.id), self)
            self.replay_worker = worker
            worker.status_changed.connect(self.status.setText)
            worker.error_occurred.connect(self._error)
            worker.finished.connect(self._replay_finished)
            worker.start()
            self._busy_ui()
        except Exception as exc:
            self._warning(exc)

    def _stop_replay(self):
        if self.replay_worker:
            self.replay_worker.stop()
            self.replay_stop.setEnabled(False)

    def _replay_finished(self):
        worker = self.replay_worker
        self.replay_worker = None
        worker.deleteLater()
        self._busy_ui()

    def _export_client(self):
        self._export_existing(False)

    def _export_mock(self):
        self._export_existing(True)

    def _export_existing(self, mock):
        try:
            session = self._editable_session()
            ex = self._selected_exchange()
            cfg = json.loads(session.config)
            proto, encoding = cfg["protocol"], cfg.get("encoding", "UTF-8")
            if proto == "tcp" and cfg.get("framing") == "delimiter":
                raise ValueError("现有客户端/固定 Mock 不支持分隔符协议，请使用本页录制 Mock 回放")
            if mock and ex.response is None:
                raise ValueError("当前交互没有响应")
            if proto == "ws" and (json.loads(ex.request_meta).get("opcode") == 2 or json.loads(ex.response_meta).get("opcode") == 2):
                raise ValueError("二进制 WebSocket 请使用本页录制 Mock 回放")
            response_meta = json.loads(ex.response_meta)
            if mock and any(k.lower() == "content-encoding" and v.lower() != "identity" for k, v in response_meta.get("headers", [])):
                raise ValueError("压缩 HTTP 响应请使用录制 Mock 回放，不能保存为文本固定响应")
            if proto == "http":
                request_meta = json.loads(ex.request_meta)
                if any(k.lower() == "content-encoding" and v.lower() != "identity" for k, v in request_meta.get("headers", [])):
                    raise ValueError("压缩 HTTP 请求请使用录制 Mock，不能转换为文本客户端用例")
            text_encoding = "UTF-8" if proto in ("http", "ws") else encoding
            request = ex.request.decode(text_encoding)
            response = ex.response.decode(text_encoding, errors="strict" if mock else "replace") if ex.response is not None else ""
            if request.encode(text_encoding) != ex.request or (mock and response.encode(text_encoding) != ex.response):
                raise ValueError("报文不能无损转换为文本，请使用本页录制 Mock 保留原始字节")
            name, ok = QInputDialog.getText(self, "保存用例", "新集合名称", text=session.name + (" Mock" if mock else " 回测"))
            if not ok or not name.strip():
                return
            client_type = proto + "_client"
            if proto == "http":
                meta = json.loads(ex.request_meta)
                headers = [(k, v) for k, v in meta.get("headers", []) if k.lower() not in ("content-length", "transfer-encoding", "connection")]
                url = "http://{}:{}{}".format(cfg["upstream_host"], cfg["upstream_port"], meta.get("path", "/"))
                preset = dict(proto=client_type, url=url, method=meta.get("method", "GET"), headers=headers,
                              body={"type": "text", "text": request}, settings={"timeout": cfg["timeout"]})
            else:
                preset = dict(proto=client_type, ip=cfg["upstream_host"], port=cfg["upstream_port"],
                              encoding=encoding, recv_encoding=encoding, head_length=cfg["head_length"] if cfg["framing"] == "length" else 0,
                              timeout=cfg["timeout"], send_message=request, ws_url="ws://{}:{}/".format(cfg["upstream_host"], cfg["upstream_port"]), ws_timeout=cfg["timeout"])
                if proto == "ws":
                    handshake = next((json.loads(e.metadata) for e in self.db.get_recording_events(session.id, preview=True, connection_id=ex.connection_id) if e.direction == "request" and json.loads(e.metadata).get("handshake")), {})
                    preset["ws_url"] = "ws://{}:{}{}".format(cfg["upstream_host"], cfg["upstream_port"], handshake.get("path", "/"))
            # 现有表保存可编辑文本；原始录制和预期响应仍保留在录制库和历史中。
            server = None
            if mock:
                meta = json.loads(ex.response_meta)
                headers = [(k, v) for k, v in meta.get("headers", []) if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")]
                responses = [{"name": "录制响应", "active": True, "status_code": meta.get("status_code", 200), "headers": headers, "body": response, "format": "text"}]
                server = dict(name=name.strip(), server_type=proto + "_server", ip=cfg["listen_host"], port=cfg["listen_port"],
                              encoding=encoding, recv_encoding=encoding,
                              head_length=cfg["head_length"] if cfg["framing"] == "length" else 0,
                              response_message=response, response_messages=json.dumps(responses, ensure_ascii=False),
                              response_delay=int(ex.elapsed_ms), response_mode="fixed")
            presets = json.dumps({"_active_proto": client_type, client_type: [
                {"name": DEFAULT_PRESET_NAME, "message": json.dumps(preset, ensure_ascii=False)}]}, ensure_ascii=False)
            self.db.export_recording_case(name.strip(), client_type, presets, ex, dict(cfg, **preset), request, response, server)
            self.targets_changed.emit()
            self.status.setText("已保存到协议测试：" + name.strip())
        except Exception as exc:
            self._warning(exc)

    def _export_json(self):
        try:
            session = self._editable_session()
            path, _ = QFileDialog.getSaveFileName(self, "导出录制会话", session.name + ".json", "JSON (*.json)")
            if not path:
                return
            worker = RecordingExportWorker(self.db, session, path, self)
            self.file_worker = worker
            worker.finished_all.connect(self._export_done)
            worker.finished.connect(self._export_finished)
            worker.start()
            self.status.setText("正在导出完整会话…")
            self._busy_ui()
        except Exception as exc:
            self._warning(exc)

    def _export_done(self, success, message):
        if success:
            self.status.setText("已导出 " + message)
        else:
            self._warning(message)

    def _export_finished(self):
        worker = self.file_worker
        self.file_worker = None
        worker.deleteLater()
        self._busy_ui()

    def _delete_session(self):
        try:
            session = self._editable_session()
            if QMessageBox.question(self, "删除会话", "删除此会话及其录制数据和回放规则？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self.db.delete_recording_session(session.id)
                self.refresh_sessions()
        except Exception as exc:
            self._warning(exc)
