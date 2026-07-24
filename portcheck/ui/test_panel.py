"""连通性测试面板 —— 执行测试、显示实时进度和结果。
支持按集合测试和按指定目标 ID 列表测试。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portcheck.database import Database
from portcheck.scanner import ScanResult, ScanTarget, ScannerWorker


class TestPanel(QWidget):
    """连通性测试面板。

    Signals:
        test_finished: 一轮测试完成时触发，通知结果面板刷新。
    """

    test_finished = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._worker: ScannerWorker | None = None
        self._session_id: int | None = None
        self._total = 0
        self._success_count = 0
        self._fail_count = 0
        self._current_batch_id: int | None = None
        self._all_results: list[ScanResult] = []  # 缓存全部结果用于筛选
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 控制栏 ──────────────────────────────────────────
        ctrl_group = QGroupBox("测试控制")
        ctrl_layout = QHBoxLayout(ctrl_group)

        self._batch_label = QLabel("待测目标: 全部")
        ctrl_layout.addWidget(self._batch_label)

        ctrl_layout.addStretch()

        ctrl_layout.addWidget(QLabel("超时(秒):"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 60)
        self._timeout_spin.setValue(1)
        self._timeout_spin.setSuffix(" s")
        ctrl_layout.addWidget(self._timeout_spin)

        ctrl_layout.addWidget(QLabel("并发数:"))
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 200)
        self._workers_spin.setValue(50)
        ctrl_layout.addWidget(self._workers_spin)

        self._test_btn = QPushButton("▶ 开始测试")
        self._test_btn.setMinimumWidth(120)
        self._test_btn.clicked.connect(self._toggle_test)
        ctrl_layout.addWidget(self._test_btn)

        layout.addWidget(ctrl_group)

        # ── 进度栏 ──────────────────────────────────────────
        progress_group = QGroupBox("测试进度")
        progress_layout = QVBoxLayout(progress_group)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("就绪")
        progress_layout.addWidget(self._progress_label)

        layout.addWidget(progress_group)

        # ── 实时结果表格 ────────────────────────────────────
        result_group = QGroupBox("测试结果")
        result_layout = QVBoxLayout(result_group)

        filter_layout = QHBoxLayout()
        self._result_filter = QLineEdit()
        self._result_filter.setPlaceholderText("筛选结果...")
        self._result_filter.setClearButtonEnabled(True)
        self._result_filter.textChanged.connect(self._apply_result_filter)
        filter_layout.addWidget(self._result_filter)
        result_layout.addLayout(filter_layout)

        self._result_table = QTableWidget()
        self._result_table.setColumnCount(6)
        self._result_table.setHorizontalHeaderLabels([
            "状态", "IP 地址", "端口", "描述", "延迟(ms)", "错误信息"
        ])
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.horizontalHeader().setStretchLastSection(True)

        hh = self._result_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self._result_table.setColumnWidth(0, 70)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self._result_table.setColumnWidth(2, 70)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self._result_table.setColumnWidth(4, 80)

        result_layout.addWidget(self._result_table)

        layout.addWidget(result_group)

        # ── 状态栏 ──────────────────────────────────────────
        self._status_label = QLabel("就绪 | 选择一个集合后点击「开始测试」，或在目标管理中勾选后点击「测试选中」")
        layout.addWidget(self._status_label)

    # ── 公开接口 ───────────────────────────────────────────

    def set_batch(self, batch_id: int | None) -> None:
        """设置当前集合，更新控制栏标签。"""
        self._current_batch_id = batch_id
        targets = self._db.get_targets(batch_id)
        if batch_id is None:
            self._batch_label.setText(f"待测目标: 全部 ({len(targets)})")
        elif batch_id == 0:
            self._batch_label.setText(f"待测目标: 未分类 ({len(targets)})")
        else:
            batch = self._db.get_batch(batch_id)
            name = batch.name if batch else "未知"
            self._batch_label.setText(f"待测目标: {name} ({len(targets)})")

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start_test_with_ids(self, target_ids: list[int], label: str = "选中目标") -> None:
        """使用指定目标 ID 列表启动测试（供外部调用）。"""
        if self.is_running():
            QMessageBox.information(self, "提示", "有测试正在进行中，请等待完成。")
            return

        targets = []
        for tid in target_ids:
            t = self._db.get_target(tid)
            if t:
                targets.append(t)

        if not targets:
            QMessageBox.information(self, "提示", "没有找到有效的目标。")
            return

        scan_targets = [
            ScanTarget(id=t.id, ip=t.ip, port=t.port, description=t.description)
            for t in targets
        ]

        self._batch_label.setText(f"待测目标: {label} ({len(scan_targets)})")
        self._session_id = self._db.create_test_session(None, label)
        self._run_test(scan_targets)

    # ── 槽函数 ─────────────────────────────────────────────

    def _toggle_test(self):
        if self.is_running():
            self._cancel_test()
        else:
            self._start_test()

    def _start_test(self):
        """启动连通性检测（按当前集合）。"""
        targets = self._db.get_targets(self._current_batch_id)
        if not targets:
            QMessageBox.information(self, "提示", "当前集合没有目标，请先添加目标。")
            return

        scan_targets = [
            ScanTarget(id=t.id, ip=t.ip, port=t.port, description=t.description)
            for t in targets
        ]

        batch_name = ""
        if self._current_batch_id is not None and self._current_batch_id > 0:
            batch = self._db.get_batch(self._current_batch_id)
            batch_name = batch.name if batch else ""
        self._session_id = self._db.create_test_session(
            self._current_batch_id, batch_name
        )
        self._run_test(scan_targets)

    def _run_test(self, scan_targets: list[ScanTarget]):
        """通用测试启动逻辑。"""
        self._total = len(scan_targets)
        self._success_count = 0
        self._fail_count = 0
        self._all_results = []
        self._result_table.setRowCount(0)
        self._result_filter.clear()

        self._test_btn.setText("⏹ 停止测试")
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(self._total)
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"0 / {self._total}")

        self._worker = ScannerWorker(
            scan_targets,
            timeout=self._timeout_spin.value(),
            max_workers=self._workers_spin.value(),
        )
        self._worker.progress.connect(self._on_target_done)
        self._worker.finished_all.connect(self._on_all_done)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _cancel_test(self):
        """取消测试。"""
        if self._worker:
            self._worker.cancel()
            self._worker.wait(2000)
        self._finalize_ui()
        self._status_label.setText("测试已取消")

    # ── Worker 回调 ────────────────────────────────────────

    def _on_target_done(self, current: int, total: int, result: ScanResult):
        self._progress_bar.setValue(current)
        self._progress_label.setText(f"{current} / {total}")

        if result.success:
            self._success_count += 1
        else:
            self._fail_count += 1

        self._all_results.append(result)

        # 始终保存到数据库
        self._db.save_test_result(
            session_id=self._session_id,
            target_id=result.target_id,
            ip=result.ip,
            port=result.port,
            description=result.description,
            success=result.success,
            latency_ms=result.latency_ms,
            error_msg=result.error_msg,
        )

        # 仅当通过筛选时才添加到表格
        if self._result_matches_filter(result):
            self._add_result_row(result)

    def _on_all_done(self, results: list):
        if self._session_id is not None:
            self._db.complete_test_session(
                self._session_id, self._total, self._success_count, self._fail_count
            )
        self._finalize_ui()
        self._status_label.setText(
            f"测试完成 | 共计 {self._total} | "
            f"连通 {self._success_count} | 未连通 {self._fail_count}"
        )
        self.test_finished.emit()

    def _on_error(self, error_msg: str):
        self._finalize_ui()
        QMessageBox.critical(self, "测试错误", f"检测过程发生错误:\n{error_msg}")
        self._status_label.setText(f"测试失败: {error_msg}")

    def _add_result_row(self, result: ScanResult):
        """添加一行结果到表格。"""
        row = self._result_table.rowCount()
        self._result_table.insertRow(row)

        status_text = "✓ 连通" if result.success else "✗ 未连通"
        status_item = QTableWidgetItem(status_text)
        status_item.setForeground(
            QBrush(QColor("#27ae60") if result.success else QColor("#e74c3c"))
        )
        self._result_table.setItem(row, 0, status_item)
        self._result_table.setItem(row, 1, QTableWidgetItem(result.ip))
        port_item = QTableWidgetItem(str(result.port))
        port_item.setTextAlignment(Qt.AlignCenter)
        self._result_table.setItem(row, 2, port_item)
        self._result_table.setItem(row, 3, QTableWidgetItem(result.description))

        latency_text = f"{result.latency_ms:.1f}" if result.success else "-"
        latency_item = QTableWidgetItem(latency_text)
        latency_item.setTextAlignment(Qt.AlignCenter)
        self._result_table.setItem(row, 4, latency_item)
        self._result_table.setItem(row, 5, QTableWidgetItem(result.error_msg))
        self._result_table.scrollToBottom()

    def _result_matches_filter(self, result: ScanResult) -> bool:
        """检查结果是否匹配当前筛选文本。"""
        text = self._result_filter.text().strip().lower()
        if not text:
            return True
        status = "连通" if result.success else "未连通"
        return (text in result.ip.lower()
                or text in str(result.port)
                or text in result.description.lower()
                or text in status
                or text in result.error_msg.lower())

    def _apply_result_filter(self):
        """筛选文本变化时重建表格。"""
        self._result_table.setRowCount(0)
        for r in self._all_results:
            if self._result_matches_filter(r):
                self._add_result_row(r)

    def _finalize_ui(self):
        self._test_btn.setText("▶ 开始测试")
        self._progress_bar.setVisible(False)
        self._worker = None
