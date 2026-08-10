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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from src.csv_handler import export_results_to_csv
from src.database import Database
from src.excel_handler import export_results_to_excel
from src.ui import shortcuts
from src.ui.table_utils import enable_stretch_fill, refresh_tooltips


class ResultPanel(QWidget):
    """测试历史面板。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._cached_results: list = []
        self._sort_col: int = -1
        self._sort_asc: bool = True
        self._session_sort_col: int = 0
        self._session_sort_asc: bool = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 垂直分割: 左边是会话列表，右边是结果详情
        splitter = QSplitter(Qt.Horizontal)

        # ── 左半部分: 会话列表 ──────────────────────────────
        session_group = QGroupBox("测试历史")
        session_group.setMinimumWidth(160)
        session_layout = QVBoxLayout(session_group)

        self._session_table = QTableWidget()
        self._session_table.setColumnCount(5)
        self._session_table.setHorizontalHeaderLabels([
            "测试时间", "集合", "总数", "连通", "未连通"
        ])
        self._session_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._session_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._session_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._session_table.setAlternatingRowColors(False)
        self._session_table.verticalHeader().setVisible(False)
        self._session_table.itemSelectionChanged.connect(self._on_session_selected)
        self._session_table.horizontalHeader().setSectionsClickable(True)
        self._session_table.horizontalHeader().sectionClicked.connect(self._on_session_header_clicked)

        enable_stretch_fill(self._session_table)

        session_layout.addWidget(self._session_table)

        session_btn_layout = QHBoxLayout()
        self._delete_session_btn = QPushButton("删除选中")
        self._delete_session_btn.clicked.connect(self._delete_sessions)
        session_btn_layout.addWidget(self._delete_session_btn)
        session_btn_layout.addStretch()
        session_layout.addLayout(session_btn_layout)

        splitter.addWidget(session_group)

        # ── 右半部分: 结果详情 ──────────────────────────────
        result_group = QGroupBox("结果详情")
        result_group.setMinimumWidth(260)
        result_layout = QVBoxLayout(result_group)

        # 筛选栏
        filter_layout = QHBoxLayout()

        self._text_filter = QLineEdit()
        self._text_filter.setPlaceholderText("筛选 IP/端口/描述...")
        self._text_filter.setClearButtonEnabled(True)
        self._text_filter.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self._text_filter)

        self._filter_combo = QComboBox()
        self._filter_combo.addItem("全部状态", None)
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
        self._result_table.setAlternatingRowColors(False)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.horizontalHeader().setSectionsClickable(True)
        self._result_table.horizontalHeader().sectionClicked.connect(self._on_result_header_clicked)

        enable_stretch_fill(self._result_table)

        result_layout.addWidget(self._result_table)

        splitter.addWidget(result_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 380])

        layout.addWidget(splitter)

    # ── 公开接口 ───────────────────────────────────────────

    def refresh(self) -> None:
        """刷新会话列表。"""
        sessions = self._db.get_test_sessions()
        # 会话列表字段排序
        if self._session_sort_col >= 0:
            key_map = {
                0: lambda s: s.started_at,
                1: lambda s: (s.collection_name or "").lower(),
                2: lambda s: s.total_count,
                3: lambda s: s.success_count,
                4: lambda s: s.fail_count,
            }
            key_fn = key_map.get(self._session_sort_col)
            if key_fn:
                sessions.sort(key=key_fn, reverse=not self._session_sort_asc)
        self._update_session_sort_indicator()
        self._session_table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            self._session_table.setItem(row, 0, QTableWidgetItem(s.started_at))
            self._session_table.setItem(row, 1, QTableWidgetItem(
                s.collection_name if s.collection_name else "(全部)"
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
        refresh_tooltips(self._session_table)

        # 清空结果表格
        self._result_table.setRowCount(0)
        self._result_count_label.setText("")

    # ── 槽函数 ─────────────────────────────────────────────

    def _on_session_selected(self):
        """选中某个会话，加载其结果。"""
        self._load_results()

    def _on_session_header_clicked(self, col: int):
        if self._session_sort_col == col:
            self._session_sort_asc = not self._session_sort_asc
        else:
            self._session_sort_col = col
            self._session_sort_asc = True
        self.refresh()

    def _update_session_sort_indicator(self):
        headers = {0: "测试时间", 1: "集合", 2: "总数", 3: "连通", 4: "未连通"}
        for c, label in headers.items():
            item = self._session_table.horizontalHeaderItem(c)
            if item:
                arrow = " ▲" if (c == self._session_sort_col and self._session_sort_asc) else \
                        " ▼" if c == self._session_sort_col else ""
                item.setText(label + arrow)

    def _apply_filter(self):
        """筛选条件改变，重新加载。"""
        self._load_results()

    def _load_results(self):
        """根据当前选中的会话和筛选条件（文本 + 状态）+ 排序加载结果。"""
        rows = self._session_table.selectionModel().selectedRows()
        if not rows:
            self._result_table.setRowCount(0)
            return

        session_id = self._session_table.item(rows[0].row(), 0).data(Qt.UserRole)
        status_filter = self._filter_combo.currentData()
        results = self._db.get_test_results(session_id, status_filter)

        text = self._text_filter.text().strip().lower()
        if text:
            results = [r for r in results if
                       text in r.ip.lower()
                       or text in str(r.port)
                       or text in r.description.lower()
                       or text in r.error_msg.lower()]

        # 缓存用于排序切换（不用重查 DB）
        self._cached_results = results

        # 排序
        if self._sort_col >= 0:
            key_map = {
                1: lambda r: tuple(int(o) for o in r.ip.split(".")),
                2: lambda r: r.port,
                4: lambda r: r.latency_ms if r.success else -1,
            }
            key_func = key_map.get(self._sort_col)
            if key_func:
                results.sort(key=key_func, reverse=not self._sort_asc)

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
            latency_text = f"{r.latency_ms:.1f}" if r.success else "-"
            latency_item = QTableWidgetItem(latency_text)
            latency_item.setTextAlignment(Qt.AlignCenter)
            self._result_table.setItem(row, 4, latency_item)
            self._result_table.setItem(row, 5, QTableWidgetItem(r.error_msg))
            self._result_table.setItem(row, 6, QTableWidgetItem(r.tested_at))

        self._result_count_label.setText(f"({len(results)} 条)")
        self._update_result_sort_indicator()
        refresh_tooltips(self._result_table)

    def _on_result_header_clicked(self, col: int):
        if col not in (1, 2, 4):
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._apply_filter()

    def _update_result_sort_indicator(self):
        headers = {1: "IP 地址", 2: "端口", 4: "延迟(ms)"}
        for c, label in headers.items():
            if c == self._sort_col:
                arrow = " ▲" if self._sort_asc else " ▼"
            else:
                arrow = ""
            self._result_table.horizontalHeaderItem(c).setText(label + arrow)

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
        collection_name = session.collection_name if session else ""

        data = [{
            "ip": r.ip,
            "port": r.port,
            "description": r.description,
            "collection_name": collection_name,
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

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "delete"):
            self._delete_sessions()
        else:
            super().keyPressEvent(event)
