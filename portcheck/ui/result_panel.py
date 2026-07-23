"""测试历史面板 —— 查看历史测试会话、筛选结果、导出。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from portcheck.csv_handler import export_results_to_csv
from portcheck.database import Database
from portcheck.excel_handler import export_results_to_excel


class ResultPanel(QWidget):
    """测试历史面板。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 水平分割: 上面是会话列表，下面是结果详情
        splitter = QSplitter(Qt.Vertical)

        # ── 上半部分: 会话列表 ──────────────────────────────
        session_group = QGroupBox("测试历史")
        session_layout = QVBoxLayout(session_group)

        self._session_table = QTableWidget()
        self._session_table.setColumnCount(5)
        self._session_table.setHorizontalHeaderLabels([
            "测试时间", "集合", "总数", "连通", "未连通"
        ])
        self._session_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._session_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._session_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._session_table.setAlternatingRowColors(True)
        self._session_table.verticalHeader().setVisible(False)
        self._session_table.setMaximumHeight(200)
        self._session_table.itemSelectionChanged.connect(self._on_session_selected)

        hh = self._session_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, 5):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self._session_table.setColumnWidth(i, 70)

        session_layout.addWidget(self._session_table)

        session_btn_layout = QHBoxLayout()
        self._delete_session_btn = QPushButton("删除选中")
        self._delete_session_btn.clicked.connect(self._delete_sessions)
        session_btn_layout.addWidget(self._delete_session_btn)
        session_btn_layout.addStretch()
        session_layout.addLayout(session_btn_layout)

        splitter.addWidget(session_group)

        # ── 下半部分: 结果详情 ──────────────────────────────
        result_group = QGroupBox("结果详情")
        result_layout = QVBoxLayout(result_group)

        # 筛选栏
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("筛选:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("全部", None)
        self._filter_combo.addItem("✓ 连通", "success")
        self._filter_combo.addItem("✗ 未连通", "fail")
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_layout.addWidget(self._filter_combo)

        self._result_count_label = QLabel("")
        filter_layout.addWidget(self._result_count_label)

        filter_layout.addStretch()

        self._export_btn = QPushButton("导出结果")
        self._export_btn.clicked.connect(self._export_results)
        filter_layout.addWidget(self._export_btn)

        result_layout.addLayout(filter_layout)

        # 结果表格
        self._result_table = QTableWidget()
        self._result_table.setColumnCount(7)
        self._result_table.setHorizontalHeaderLabels([
            "状态", "IP 地址", "端口", "描述", "延迟(ms)", "错误信息", "检测时间"
        ])
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.horizontalHeader().setStretchLastSection(True)

        hh2 = self._result_table.horizontalHeader()
        hh2.setSectionResizeMode(0, QHeaderView.Fixed)
        self._result_table.setColumnWidth(0, 70)
        hh2.setSectionResizeMode(1, QHeaderView.Stretch)
        hh2.setSectionResizeMode(2, QHeaderView.Fixed)
        self._result_table.setColumnWidth(2, 70)
        hh2.setSectionResizeMode(3, QHeaderView.Stretch)
        hh2.setSectionResizeMode(4, QHeaderView.Fixed)
        self._result_table.setColumnWidth(4, 80)

        result_layout.addWidget(self._result_table)

        splitter.addWidget(result_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    # ── 公开接口 ───────────────────────────────────────────

    def refresh(self) -> None:
        """刷新会话列表。"""
        sessions = self._db.get_test_sessions()
        self._session_table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            self._session_table.setItem(row, 0, QTableWidgetItem(s.started_at))
            self._session_table.setItem(row, 1, QTableWidgetItem(
                s.batch_name if s.batch_name else "(全部)"
            ))

            total_item = QTableWidgetItem(str(s.total_count))
            total_item.setTextAlignment(Qt.AlignCenter)
            self._session_table.setItem(row, 2, total_item)

            ok_item = QTableWidgetItem(str(s.success_count))
            ok_item.setTextAlignment(Qt.AlignCenter)
            ok_item.setForeground(QBrush(QColor("#27ae60")))
            self._session_table.setItem(row, 3, ok_item)

            fail_item = QTableWidgetItem(str(s.fail_count))
            fail_item.setTextAlignment(Qt.AlignCenter)
            fail_item.setForeground(QBrush(QColor("#e74c3c")))
            self._session_table.setItem(row, 4, fail_item)

            # 存储 session_id
            self._session_table.item(row, 0).setData(Qt.UserRole, s.id)

        # 清空结果表格
        self._result_table.setRowCount(0)
        self._result_count_label.setText("")

    # ── 槽函数 ─────────────────────────────────────────────

    def _on_session_selected(self):
        """选中某个会话，加载其结果。"""
        self._load_results()

    def _apply_filter(self):
        """筛选条件改变，重新加载。"""
        self._load_results()

    def _load_results(self):
        """根据当前选中的会话和筛选条件加载结果。"""
        rows = self._session_table.selectionModel().selectedRows()
        if not rows:
            self._result_table.setRowCount(0)
            return

        session_id = self._session_table.item(rows[0].row(), 0).data(Qt.UserRole)
        status_filter = self._filter_combo.currentData()
        results = self._db.get_test_results(session_id, status_filter)

        self._result_table.setRowCount(len(results))
        for row, r in enumerate(results):
            status_text = "✓ 连通" if r.success else "✗ 未连通"
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(
                QBrush(QColor("#27ae60") if r.success else QColor("#e74c3c"))
            )
            self._result_table.setItem(row, 0, status_item)
            self._result_table.setItem(row, 1, QTableWidgetItem(r.ip))

            port_item = QTableWidgetItem(str(r.port))
            port_item.setTextAlignment(Qt.AlignCenter)
            self._result_table.setItem(row, 2, port_item)

            self._result_table.setItem(row, 3, QTableWidgetItem(r.description))

            if r.success:
                latency_text = f"{r.latency_ms:.1f}"
            else:
                latency_text = "-"
            latency_item = QTableWidgetItem(latency_text)
            latency_item.setTextAlignment(Qt.AlignCenter)
            self._result_table.setItem(row, 4, latency_item)

            self._result_table.setItem(row, 5, QTableWidgetItem(r.error_msg))
            self._result_table.setItem(row, 6, QTableWidgetItem(r.tested_at))

        self._result_count_label.setText(f"({len(results)} 条)")

    def _delete_sessions(self):
        """批量删除选中的测试会话。"""
        rows = self._session_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要删除的测试会话。")
            return

        # 收集所有选中的 session_id（去重）
        session_ids = set()
        for idx in rows:
            item = self._session_table.item(idx.row(), 0)
            if item:
                sid = item.data(Qt.UserRole)
                if sid:
                    session_ids.add(sid)

        if not session_ids:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(session_ids)} 条测试记录吗？\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for sid in session_ids:
                self._db.delete_test_session(sid)
            self.refresh()

    def _export_results(self):
        """导出当前显示的结果。"""
        rows = self._session_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选择一条测试会话。")
            return

        session_id = self._session_table.item(rows[0].row(), 0).data(Qt.UserRole)
        status_filter = self._filter_combo.currentData()
        results = self._db.get_test_results(session_id, status_filter)

        if not results:
            QMessageBox.information(self, "提示", "没有可导出的结果。")
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出测试结果", "test_results.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not filepath:
            return

        session = self._db.get_test_session(session_id)
        batch_name = session.batch_name if session else ""

        data = [{
            "ip": r.ip,
            "port": r.port,
            "description": r.description,
            "batch_name": batch_name,
            "success": r.success,
            "latency_ms": r.latency_ms,
            "error_msg": r.error_msg,
            "tested_at": r.tested_at,
        } for r in results]

        ext = Path(filepath).suffix.lower()
        if ext == ".csv":
            ok, err = export_results_to_csv(filepath, data)
        else:
            if ext not in (".xlsx", ".xls"):
                filepath = str(Path(filepath).with_suffix(".xlsx"))
            ok, err = export_results_to_excel(filepath, data)

        if ok:
            QMessageBox.information(
                self, "导出完成",
                f"成功导出 {len(data)} 条结果到:\n{filepath}"
            )
        else:
            QMessageBox.critical(self, "导出失败", f"导出失败:\n{err}")
