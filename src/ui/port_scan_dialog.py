"""端口扫描对话框 —— 扫描指定 IP 的端口范围，发现开放端口后可添加到集合。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.database import Database
from src.ui.table_utils import enable_stretch_fill, refresh_tooltips
from src.scanner import (
    ScanResult,
    ScanTarget,
    ScannerWorker,
    expand_ip_range,
    expand_port_range,
)


class PortScanDialog(QDialog):
    """端口扫描对话框。

    输入 IP 和端口范围，执行扫描后将开放端口导入到指定集合。
    """

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._worker: ScannerWorker | None = None
        self._results: list[ScanResult] = []
        self.setWindowTitle("端口扫描")
        self.setMinimumSize(650, 550)
        self.resize(700, 600)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 输入区 ──────────────────────────────────────────
        input_group = QGroupBox("扫描参数")
        form = QFormLayout(input_group)

        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText(
            "单个: 192.168.1.1  范围: 192.168.1.1-10  CIDR: 10.0.0.0/24"
        )
        self._ip_edit.setMinimumWidth(350)
        form.addRow("目标 IP:", self._ip_edit)

        self._port_edit = QLineEdit("1-1000")
        self._port_edit.setPlaceholderText("1-1000, 80,443,8080-8090")
        self._port_edit.setMinimumWidth(350)
        form.addRow("端口范围:", self._port_edit)

        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("超时(秒):"))
        self._timeout_spin = QComboBox()
        self._timeout_spin.addItems(["0.2", "0.5", "1", "2", "3", "5"])
        self._timeout_spin.setCurrentText("0.5")
        self._timeout_spin.setEditable(True)  # 允许自定义输入
        h_layout.addWidget(self._timeout_spin)

        h_layout.addWidget(QLabel("并发数:"))
        self._workers_spin = QComboBox()
        self._workers_spin.addItems(["20", "50", "100", "200"])
        self._workers_spin.setCurrentText("100")
        h_layout.addWidget(self._workers_spin)
        h_layout.addStretch()

        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet("color: #888;")
        h_layout.addWidget(self._preview_label)
        form.addRow("", h_layout)

        self._ip_edit.textChanged.connect(self._update_preview)
        self._port_edit.textChanged.connect(self._update_preview)

        layout.addWidget(input_group)

        # ── 扫描按钮 + 进度 ─────────────────────────────────
        ctrl_layout = QHBoxLayout()
        self._scan_btn = QPushButton("▶ 开始扫描")
        self._scan_btn.setMinimumWidth(130)
        self._scan_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #2980b9; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self._scan_btn.clicked.connect(self._toggle_scan)
        ctrl_layout.addWidget(self._scan_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        ctrl_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        ctrl_layout.addWidget(self._progress_label)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

        # ── 结果表格 ────────────────────────────────────────
        result_group = QGroupBox("扫描结果")
        result_layout = QVBoxLayout(result_group)

        self._result_table = QTableWidget()
        self._result_table.setColumnCount(5)
        self._result_table.setHorizontalHeaderLabels([
            "", "IP 地址", "端口", "状态", "延迟(ms)"
        ])
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.setAlternatingRowColors(False)
        self._result_table.verticalHeader().setVisible(False)

        # 复选框列保持窄宽，其余列 Stretch 填满表格宽度
        enable_stretch_fill(self._result_table, fixed_cols=[0])
        self._result_table.setColumnWidth(0, 30)

        result_layout.addWidget(self._result_table)

        # 状态统计
        stats_layout = QHBoxLayout()
        self._open_label = QLabel("开放: 0")
        self._open_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        stats_layout.addWidget(self._open_label)
        self._closed_label = QLabel("关闭: 0")
        self._closed_label.setStyleSheet("color: #e74c3c;")
        stats_layout.addWidget(self._closed_label)
        stats_layout.addStretch()
        result_layout.addLayout(stats_layout)

        layout.addWidget(result_group)

        # ── 底部: 导入到集合 ────────────────────────────────
        import_layout = QHBoxLayout()
        import_layout.addWidget(QLabel("将开放端口添加到集合:"))

        self._batch_combo = QComboBox()
        import_layout.addWidget(self._batch_combo)

        self._new_batch_edit = QLineEdit()
        self._new_batch_edit.setPlaceholderText("新集合名称")
        self._new_batch_edit.setVisible(True)  # 默认「新建集合」选中，输入框可见
        self._new_batch_edit.setMaximumWidth(150)
        import_layout.addWidget(self._new_batch_edit)

        # 在控件都创建好后再填充选项（触发信号时 _new_batch_edit 已存在）
        self._batch_combo.currentIndexChanged.connect(self._on_collection_changed)
        self._batch_combo.addItem("(新建集合...)", -1)
        self._batch_combo.addItem("(无集合)", None)
        for b in self._db.get_all_collections():
            self._batch_combo.addItem(f"{b.name} ({b.target_count})", b.id)

        self._import_btn = QPushButton("导入开放端口 →")
        self._import_btn.setEnabled(False)
        self._import_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #27ae60; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
            "QPushButton:disabled { background-color: #bbb; }"
        )
        self._import_btn.clicked.connect(self._import_results)
        import_layout.addWidget(self._import_btn)
        import_layout.addStretch()
        layout.addLayout(import_layout)

        self._update_preview()

    def _update_preview(self):
        ip_text = self._ip_edit.text().strip()
        port_text = self._port_edit.text().strip()
        if not ip_text or not port_text:
            self._preview_label.setText("")
            return
        try:
            ips = expand_ip_range(ip_text)
            ports = expand_port_range(port_text)
            total = len(ips) * len(ports)
            self._preview_label.setText(f"共 {total} 个端口")
        except ValueError as e:
            self._preview_label.setText(f"⚠ {e}")

    def _on_collection_changed(self, idx):
        self._new_batch_edit.setVisible(
            self._batch_combo.currentData() == -1
        )

    # ── 扫描逻辑 ───────────────────────────────────────────

    def _toggle_scan(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
            self._scan_done()
            return
        self._start_scan()

    def _start_scan(self):
        ip_text = self._ip_edit.text().strip()
        port_text = self._port_edit.text().strip()
        if not ip_text or not port_text:
            QMessageBox.warning(self, "验证失败", "IP 地址和端口范围不能为空。")
            return

        try:
            ips = expand_ip_range(ip_text)
        except ValueError as e:
            QMessageBox.warning(self, "IP 地址无效", str(e))
            return
        try:
            ports = expand_port_range(port_text)
        except ValueError as e:
            QMessageBox.warning(self, "端口范围无效", str(e))
            return

        total = len(ips) * len(ports)
        if total > 10000:
            reply = QMessageBox.question(
                self, "确认",
                f"将扫描 {total} 个端口（{len(ips)} IP × {len(ports)} 端口），可能耗时较长，确定继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        scan_targets = []
        for ip in ips:
            for port in ports:
                scan_targets.append(ScanTarget(id=0, ip=ip, port=port, description=f"{ip}:{port}"))

        self._results = []
        self._result_table.setRowCount(0)
        self._import_btn.setEnabled(False)
        self._open_label.setText("开放: 0")
        self._closed_label.setText("关闭: 0")

        self._scan_btn.setText("⏹ 停止")
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(scan_targets))
        self._progress_bar.setValue(0)

        self._worker = ScannerWorker(
            scan_targets,
            timeout=float(self._timeout_spin.currentText()),
            max_workers=int(self._workers_spin.currentText()),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_all.connect(self._scan_done)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, current: int, total: int, result: ScanResult):
        self._progress_bar.setValue(current)
        self._progress_label.setText(f"{current}/{total}")

        self._results.append(result)

        if result.success:
            row = self._result_table.rowCount()
            self._result_table.insertRow(row)

            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            cb.setCheckState(Qt.Checked)
            self._result_table.setItem(row, 0, cb)
            self._result_table.setItem(row, 1, QTableWidgetItem(result.ip))

            port_item = QTableWidgetItem(str(result.port))
            port_item.setTextAlignment(Qt.AlignCenter)
            self._result_table.setItem(row, 2, port_item)

            status_item = QTableWidgetItem("开放")
            status_item.setForeground(QBrush(QColor("#27ae60")))
            self._result_table.setItem(row, 3, status_item)

            latency_item = QTableWidgetItem(f"{result.latency_ms:.1f}")
            latency_item.setTextAlignment(Qt.AlignCenter)
            self._result_table.setItem(row, 4, latency_item)

            self._result_table.scrollToBottom()
            refresh_tooltips(self._result_table)

        # 只显示开放端口，但计数全部
        open_count = sum(1 for r in self._results if r.success)
        closed_count = current - open_count
        self._open_label.setText(f"开放: {open_count}")
        self._closed_label.setText(f"关闭: {closed_count}")

    def _scan_done(self, results=None):
        self._scan_btn.setText("▶ 开始扫描")
        self._progress_bar.setVisible(False)
        self._progress_label.setText("")
        self._worker = None

        open_count = sum(1 for r in self._results if r.success)
        if open_count > 0:
            self._import_btn.setEnabled(True)
            self._import_btn.setText(f"导入 {open_count} 个开放端口 →")

    def _on_error(self, error_msg: str):
        self._scan_done()
        QMessageBox.critical(self, "扫描错误", f"扫描过程发生错误:\n{error_msg}")

    # ── 导入结果 ───────────────────────────────────────────

    def _import_results(self):
        # 获取勾选的开放端口
        selected = []
        for row in range(self._result_table.rowCount()):
            cb = self._result_table.item(row, 0)
            if cb and cb.checkState() == Qt.Checked:
                ip = self._result_table.item(row, 1).text()
                port = int(self._result_table.item(row, 2).text())
                selected.append((ip, port))

        if not selected:
            QMessageBox.information(self, "提示", "请先勾选要导入的开放端口。")
            return

        # 确定目标集合
        collection_id = self._batch_combo.currentData()
        if collection_id == -1:
            # 新建集合
            name = self._new_batch_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "验证失败", "请输入新集合名称。")
                return
            collection_id = self._db.add_collection(name)
            self._batch_combo.blockSignals(True)
            self._batch_combo.insertItem(2, f"{name} (0)", collection_id)
            self._batch_combo.setCurrentIndex(2)
            self._batch_combo.blockSignals(False)
            self._new_batch_edit.setVisible(False)

        # 导入（跳过重复）
        added = 0
        skipped = 0
        for ip, port in selected:
            if self._db.target_exists(ip, port, collection_id):
                skipped += 1
                continue
            self._db.add_target(ip, port, f"{ip}:{port}", collection_id)
            added += 1

        msg = f"已将 {added} 个开放端口添加到集合。"
        if skipped > 0:
            msg += f"\n跳过 {skipped} 个重复。"

        QMessageBox.information(self, "导入完成", msg)
        self.accept()  # 关闭对话框
