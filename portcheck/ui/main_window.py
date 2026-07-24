"""主窗口 —— 整合所有面板，管理集合列表、菜单栏和状态栏。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from portcheck.csv_handler import export_targets_to_csv
from portcheck.database import Database
from portcheck.excel_handler import export_targets_to_excel
from portcheck.ui.port_scan_dialog import PortScanDialog
from portcheck.ui.result_panel import ResultPanel
from portcheck.ui.target_panel import TargetPanel
from portcheck.ui.test_panel import TestPanel


class BatchDialog(QDialog):
    """新建/编辑集合的对话框。"""

    def __init__(self, title: str, name: str = "", description: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QFormLayout(self)

        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText("例如: 生产环境服务器")
        self._name_edit.setMinimumWidth(280)
        layout.addRow("集合名称:", self._name_edit)

        self._desc_edit = QLineEdit(description)
        self._desc_edit.setPlaceholderText("可选描述")
        self._desc_edit.setMinimumWidth(280)
        layout.addRow("描述:", self._desc_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_accept(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "验证失败", "集合名称不能为空。")
            return
        self.accept()

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def description(self) -> str:
        return self._desc_edit.text().strip()


class MainWindow(QMainWindow):
    """PortCheck 主窗口。"""

    def __init__(self, db_path: str = ""):
        super().__init__()
        self._db = Database(db_path)
        self.setWindowTitle("PortCheck - 网络端口连通性检测工具")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 800)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._refresh_batch_list()
        self._result_panel.refresh()

    # ── 菜单栏 ─────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")

        import_action = QAction("导入目标...", self)
        import_action.triggered.connect(self._import_targets_file)
        file_menu.addAction(import_action)

        export_action = QAction("导出目标...", self)
        export_action.triggered.connect(self._export_all_targets)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        port_scan_action = QAction("端口扫描...", self)
        port_scan_action.triggered.connect(self._open_port_scan)
        file_menu.addAction(port_scan_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ── 主布局 ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 左右分割器
        splitter = QSplitter(Qt.Horizontal)

        # ── 左侧: 集合列表 ──────────────────────────────────
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        left_layout.addWidget(QLabel("<b>集合列表</b>"))

        self._batch_list = QListWidget()
        self._batch_list.setDragDropMode(QAbstractItemView.InternalMove)
        self._batch_list.setDefaultDropAction(Qt.MoveAction)
        self._batch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._batch_list.model().rowsMoved.connect(self._on_batches_reordered)
        self._batch_list.currentRowChanged.connect(self._on_batch_selected)
        left_layout.addWidget(self._batch_list)

        # 集合操作按钮
        batch_btn_layout = QHBoxLayout()
        new_batch_btn = QPushButton("+ 新建")
        new_batch_btn.clicked.connect(self._add_batch)
        batch_btn_layout.addWidget(new_batch_btn)

        edit_batch_btn = QPushButton("编辑")
        edit_batch_btn.clicked.connect(self._edit_batch)
        batch_btn_layout.addWidget(edit_batch_btn)

        delete_batch_btn = QPushButton("删除")
        delete_batch_btn.clicked.connect(self._delete_batch)
        batch_btn_layout.addWidget(delete_batch_btn)
        left_layout.addLayout(batch_btn_layout)

        splitter.addWidget(left_panel)

        # ── 右侧: 标签页 ────────────────────────────────────
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        self._target_panel = TargetPanel(self._db)
        self._target_panel.targets_changed.connect(self._on_targets_changed)
        self._target_panel.test_selected.connect(self._on_test_selected)
        self._tabs.addTab(self._target_panel, "目标管理")

        self._test_panel = TestPanel(self._db)
        self._test_panel.test_finished.connect(self._on_test_finished)
        self._tabs.addTab(self._test_panel, "连通测试")

        self._result_panel = ResultPanel(self._db)
        self._tabs.addTab(self._result_panel, "测试历史")

        right_layout.addWidget(self._tabs)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])

        main_layout.addWidget(splitter)

    # ── 状态栏 ─────────────────────────────────────────────

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_target_count = QLabel()
        self._status_last_test = QLabel()
        self._statusbar.addWidget(self._status_target_count)
        self._statusbar.addPermanentWidget(self._status_last_test)
        self._update_statusbar()

    def _update_statusbar(self):
        total = self._db.get_total_target_count()
        batch_count = len(self._db.get_all_batches())
        self._status_target_count.setText(
            f"共 {total} 个目标 / {batch_count} 个集合"
        )
        last = self._db.get_last_test_time()
        if last:
            self._status_last_test.setText(f"上次测试: {last}")
        else:
            self._status_last_test.setText("暂无测试记录")

    # ── 集合列表操作 ───────────────────────────────────────

    def _refresh_batch_list(self, select_batch_id: int | None = None):
        """刷新左侧集合列表。"""
        self._batch_list.blockSignals(True)
        self._batch_list.clear()

        # "全部目标" 项
        all_item = QListWidgetItem(f"🌐 全部目标")
        all_item.setData(Qt.UserRole, None)  # None = 全部
        self._batch_list.addItem(all_item)

        # "未分类" 项
        uncat_count = len(self._db.get_targets(0))
        if uncat_count > 0:
            uncat_item = QListWidgetItem(f"📄 未分类 ({uncat_count})")
            uncat_item.setData(Qt.UserRole, 0)  # 0 = 未分类
            self._batch_list.addItem(uncat_item)

        # 各集合
        batches = self._db.get_all_batches()
        target_index = 1 + (1 if uncat_count > 0 else 0)
        for i, b in enumerate(batches):
            item = QListWidgetItem(f"📁 {b.name} ({b.target_count})")
            item.setData(Qt.UserRole, b.id)
            self._batch_list.addItem(item)
            if select_batch_id is not None and b.id == select_batch_id:
                self._batch_list.setCurrentRow(target_index + i)

        self._batch_list.blockSignals(False)

        # 默认选中第一项
        if self._batch_list.currentRow() < 0:
            self._batch_list.setCurrentRow(0)

    def _on_batch_selected(self, row: int):
        """集合选择变更，更新所有面板。"""
        if row < 0:
            return
        item = self._batch_list.item(row)
        if not item:
            return
        batch_id = item.data(Qt.UserRole)
        self._target_panel.set_batch(batch_id)
        self._test_panel.set_batch(batch_id)

    def _on_batches_reordered(self):
        """拖拽排序后持久化集合顺序。"""
        ordered_ids = []
        for row in range(self._batch_list.count()):
            item = self._batch_list.item(row)
            bid = item.data(Qt.UserRole)
            if bid is not None and bid != 0:  # 跳过「全部目标」和「未分类」
                ordered_ids.append(bid)
        if ordered_ids:
            self._db.update_batches_sort_order(ordered_ids)

    def _add_batch(self):
        dlg = BatchDialog("新建集合", parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.add_batch(dlg.name, dlg.description)
                self._refresh_batch_list()
                self._update_statusbar()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建集合失败:\n{e}")

    def _edit_batch(self):
        item = self._batch_list.currentItem()
        if not item or item.data(Qt.UserRole) in (None, 0):
            QMessageBox.information(self, "提示", "请选择一个自定义集合进行编辑。")
            return
        batch_id = item.data(Qt.UserRole)
        batch = self._db.get_batch(batch_id)
        if not batch:
            return
        dlg = BatchDialog(
            "编辑集合", batch.name, batch.description, parent=self
        )
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.update_batch(batch_id, dlg.name, dlg.description)
                self._refresh_batch_list(batch_id)
                self._update_statusbar()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新集合失败:\n{e}")

    def _delete_batch(self):
        selected = self._batch_list.selectedItems()
        # 过滤：只保留自定义集合（排除「全部目标」和「未分类」）
        valid = [(it.data(Qt.UserRole), it.text()) for it in selected
                 if it.data(Qt.UserRole) not in (None, 0)]
        if not valid:
            QMessageBox.information(self, "提示", "请选择一个或多个自定义集合进行删除。")
            return

        if len(valid) == 1:
            msg = f"确定要删除集合「{valid[0][1]}」吗？\n\n集合中的目标不会删除，但会变为「未分类」。"
        else:
            names = "\n".join(f"  • {name}" for _, name in valid)
            msg = f"确定要删除以下 {len(valid)} 个集合吗？\n\n{names}\n\n集合中的目标不会删除，但会变为「未分类」。"

        reply = QMessageBox.question(
            self, "确认删除", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for batch_id, _ in valid:
                self._db.delete_batch(batch_id)
            self._refresh_batch_list()
            self._update_statusbar()
            self._target_panel.refresh()
            self._test_panel.set_batch(None)

    # ── 全局事件处理 ───────────────────────────────────────

    def _on_targets_changed(self):
        """目标数据变更后刷新集合列表和状态栏。"""
        self._refresh_batch_list()
        self._update_statusbar()

    def _on_test_finished(self):
        """测试完成后刷新状态栏和结果面板。"""
        self._update_statusbar()
        self._result_panel.refresh()

    # ── 菜单操作 ───────────────────────────────────────────

    def _import_targets_file(self):
        """菜单: 导入目标。"""
        self._target_panel._import_file()

    def _export_all_targets(self):
        """菜单: 导出全部目标。"""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", "targets.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not filepath:
            return
        targets = self._db.get_targets(None)
        data = [{
            "ip": t.ip, "port": t.port,
            "description": t.description,
            "batch_name": t.batch_name,
            "created_at": t.created_at,
        } for t in targets]
        ext = Path(filepath).suffix.lower()
        if ext == ".csv":
            ok, err = export_targets_to_csv(filepath, data)
        else:
            if ext not in (".xlsx", ".xls"):
                filepath = str(Path(filepath).with_suffix(".xlsx"))
            ok, err = export_targets_to_excel(filepath, data)
        if ok:
            QMessageBox.information(
                self, "导出完成",
                f"成功导出 {len(data)} 条目标到:\n{filepath}"
            )
        else:
            QMessageBox.critical(self, "导出失败", f"导出失败:\n{err}")

    def _show_about(self):
        QMessageBox.about(
            self, "关于 PortCheck",
            "<h3>PortCheck v1.0</h3>"
            "<p>网络端口连通性检测工具</p>"
            "<p>基于 Python + PySide6 + SQLite 构建</p>"
            "<p>用于批量检测本机到远程 IP:Port 的 TCP 连通性。</p>"
        )

    # ── 选中目标测试 ───────────────────────────────────────

    def _on_test_selected(self, target_ids: list[int]):
        """目标面板「测试选中」按钮回调：切换到测试页并启动测试。"""
        self._tabs.setCurrentIndex(1)  # 切换到「连通测试」标签页
        self._test_panel.start_test_with_ids(target_ids, label="选中目标")

    # ── 端口扫描 ───────────────────────────────────────────

    def _open_port_scan(self):
        """打开端口扫描对话框。"""
        dlg = PortScanDialog(self._db, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._on_targets_changed()

    # ── 窗口关闭 ───────────────────────────────────────────

    def closeEvent(self, event):
        """关闭窗口时确保测试线程已停止。"""
        if self._test_panel.is_running():
            reply = QMessageBox.question(
                self, "确认退出",
                "有正在进行的测试，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._test_panel._cancel_test()
        event.accept()
