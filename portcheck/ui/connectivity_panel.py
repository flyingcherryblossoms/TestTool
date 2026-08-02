"""连通测试面板 —— 整合集合管理、目标管理、连通测试、测试历史为子标签页。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portcheck.database import Database
from portcheck.ui.target_panel import TargetPanel
from portcheck.ui.test_panel import TestPanel
from portcheck.ui.result_panel import ResultPanel


class _BatchListTab(QWidget):
    """集合列表子标签页 —— 支持右键菜单管理集合。"""

    batch_changed = Signal(object)  # batch_id | None

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_batches = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("<b>集合列表</b>"))

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索集合...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentRowChanged.connect(self._on_selected)
        layout.addWidget(self._list)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("新建", clicked=self._on_new))
        btn_layout.addWidget(QPushButton("编辑", clicked=self._on_edit))
        btn_layout.addWidget(QPushButton("删除", clicked=self._on_delete))
        layout.addLayout(btn_layout)

    def refresh(self, select_id: int | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        self._all_batches = self._db.get_all_batches()

        all_item = QListWidgetItem("全部目标")
        all_item.setData(Qt.UserRole, None)
        self._list.addItem(all_item)

        uncat = self._db.get_targets(0)
        if uncat:
            u = QListWidgetItem(f"未分类 ({len(uncat)})")
            u.setData(Qt.UserRole, 0)
            self._list.addItem(u)

        target_idx = 1 + (1 if uncat else 0)
        for i, b in enumerate(self._all_batches):
            item = QListWidgetItem(f"{b.name} ({b.target_count})")
            item.setData(Qt.UserRole, b.id)
            self._list.addItem(item)
            if select_id is not None and b.id == select_id:
                self._list.setCurrentRow(target_idx + i)

        self._list.blockSignals(False)
        if self._list.currentRow() < 0:
            self._list.setCurrentRow(0)

        if self._search.text().strip():
            self._filter(self._search.text())

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item:
                item.setHidden(s not in item.text().lower() if s else False)

    def _on_selected(self, row: int):
        if row < 0:
            return
        item = self._list.item(row)
        if item:
            self.batch_changed.emit(item.data(Qt.UserRole))

    def _on_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        bid = item.data(Qt.UserRole)
        if bid in (None, 0):
            return  # 不显示全部/未分类的右键菜单

        menu = QMenu(self)
        menu.addAction("编辑", lambda: self._on_edit())
        menu.addAction("删除", lambda: self._on_delete())
        menu.exec(self._list.mapToGlobal(pos))

    def _on_new(self):
        from portcheck.ui.main_window import BatchDialog
        dlg = BatchDialog("新建集合", parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.add_batch(dlg.name, dlg.description)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建集合失败:\n{e}")

    def _on_edit(self):
        item = self._list.currentItem()
        if not item:
            return
        bid = item.data(Qt.UserRole)
        if bid in (None, 0):
            QMessageBox.information(self, "提示", "请选择自定义集合。")
            return
        batch = self._db.get_batch(bid)
        if not batch:
            return
        from portcheck.ui.main_window import BatchDialog
        dlg = BatchDialog("编辑集合", batch.name, batch.description, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.update_batch(bid, dlg.name, dlg.description)
                self.refresh(bid)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新集合失败:\n{e}")

    def _on_delete(self):
        selected = self._list.selectedItems()
        valid = [(it.data(Qt.UserRole), it.text()) for it in selected
                 if it.data(Qt.UserRole) not in (None, 0)]
        if not valid:
            QMessageBox.information(self, "提示", "请选择自定义集合。")
            return
        msg = (f"确定删除「{valid[0][1]}」？"
               if len(valid) == 1
               else f"确定删除以下 {len(valid)} 个集合？")
        r = QMessageBox.question(self, "确认删除", msg,
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for bid, _ in valid:
                self._db.delete_batch(bid)
            self.refresh()


class _SimpleIPTab(QWidget):
    """IP 地址管理子标签页 —— 独立 IP 列表，不关联端口。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>IP 地址管理</b>"))

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索 IP...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["IP 地址", "目标数"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table)

        self._all_ips: list[tuple[str, int]] = []

    def refresh(self, batch_id: int | None = None):
        targets = self._db.get_targets(batch_id)
        ip_map: dict[str, int] = {}
        for t in targets:
            ip_map[t.ip] = ip_map.get(t.ip, 0) + 1
        self._all_ips = sorted(ip_map.items(), key=lambda x: tuple(int(o) for o in x[0].split(".")))

        self._table.setRowCount(len(self._all_ips))
        for row, (ip, count) in enumerate(self._all_ips):
            self._table.setItem(row, 0, QTableWidgetItem(ip))
            self._table.setItem(row, 1, QTableWidgetItem(str(count)))

        if self._search.text().strip():
            self._filter(self._search.text())

    def _filter(self, text: str):
        s = text.strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                self._table.setRowHidden(row, s not in item.text().lower() if s else False)

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        ip = self._table.item(row, 0).text()
        menu = QMenu(self)
        act = QAction(f"复制 {ip}", self)
        act.triggered.connect(lambda: QApplication.clipboard().setText(ip))
        menu.addAction(act)
        menu.exec(self._table.mapToGlobal(pos))


# ── 连通测试主面板 ──────────────────────────────────────────


class ConnectivityPanel(QWidget):
    """连通测试面板 —— 5 个子标签页。"""

    targets_changed = Signal()
    protocol_test_selected = Signal(str, int)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        # Subtab 1: 集合列表
        self._batch_tab = _BatchListTab(self._db)
        self._batch_tab.batch_changed.connect(self._on_batch_changed)
        self._tabs.addTab(self._batch_tab, "集合列表")

        # Subtab 2: IP 地址管理
        self._ip_tab = _SimpleIPTab(self._db)
        self._tabs.addTab(self._ip_tab, "IP地址管理")

        # Subtab 3: 目标管理
        self._target_panel = TargetPanel(self._db)
        self._target_panel.targets_changed.connect(self._on_targets_changed)
        self._target_panel.test_selected.connect(self._on_test_selected)
        self._target_panel.protocol_test_selected.connect(
            self.protocol_test_selected.emit
        )
        self._tabs.addTab(self._target_panel, "目标管理")

        # Subtab 4: 连通测试
        self._test_panel = TestPanel(self._db)
        self._test_panel.test_finished.connect(self._on_test_finished)
        self._tabs.addTab(self._test_panel, "连通测试")

        # Subtab 5: 测试历史
        self._result_panel = ResultPanel(self._db)
        self._tabs.addTab(self._result_panel, "测试历史")

        layout.addWidget(self._tabs)

        self._batch_tab.refresh()

    # ── 信号转发 ────────────────────────────────────────────

    def _on_batch_changed(self, batch_id):
        self._target_panel.set_batch(batch_id)
        self._test_panel.set_batch(batch_id)
        self._ip_tab.refresh(batch_id)

    def _on_targets_changed(self):
        self._batch_tab.refresh()
        self._ip_tab.refresh()
        self.targets_changed.emit()

    def _on_test_selected(self, target_ids: list[int]):
        self._tabs.setCurrentIndex(3)  # 切换到连通测试
        self._test_panel.start_test_with_ids(target_ids, label="选中目标")

    def _on_test_finished(self):
        self._result_panel.refresh()
        self.targets_changed.emit()

    # ── 公共接口 ────────────────────────────────────────────

    def refresh_batch_list(self):
        self._batch_tab.refresh()

    def is_test_running(self) -> bool:
        return self._test_panel.is_running()

    def stop_test(self):
        if self._test_panel.is_running():
            self._test_panel._cancel_test()
