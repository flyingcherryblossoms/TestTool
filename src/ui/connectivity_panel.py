"""连通测试面板 —— 整合集合管理、目标管理、连通测试、测试历史为子标签页。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.ui.target_panel import TargetPanel
from src.ui.test_panel import TestPanel
from src.ui.result_panel import ResultPanel


class _BatchListTab(QWidget):
    """集合列表 —— 分类树形结构：全部 / 未分类 / 自定义集合。"""

    batch_changed = Signal(object)  # batch_id | None

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_batches = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("<b>集合分类</b>"))

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索集合...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.currentItemChanged.connect(self._on_selected)
        self._tree.setIndentation(16)
        layout.addWidget(self._tree)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("新建", clicked=self._on_new))
        btn_layout.addWidget(QPushButton("编辑", clicked=self._on_edit))
        btn_layout.addWidget(QPushButton("删除", clicked=self._on_delete))
        layout.addLayout(btn_layout)

    def refresh(self, select_id: int | None = None):
        current = self._tree.currentItem()
        prev_batch_id = current.data(0, Qt.UserRole) if current else None
        if select_id is not None:
            prev_batch_id = select_id

        self._tree.blockSignals(True)
        self._tree.clear()
        self._all_batches = self._db.get_all_batches()
        bold_font = self._tree.font()
        bold_font.setBold(True)

        # ── 一级节点：未分类（有数据才显示）──
        uncat = self._db.get_targets(0)
        if uncat:
            u = QTreeWidgetItem([f"未分类 ({len(uncat)})"])
            u.setData(0, Qt.UserRole, 0)
            u.setFont(0, bold_font)
            self._tree.addTopLevelItem(u)

        # ── 父节点：自定义集合 ──
        custom_parent = QTreeWidgetItem([f"自定义集合 ({len(self._all_batches)})"])
        custom_parent.setData(0, Qt.UserRole, None)
        custom_parent.setFont(0, bold_font)
        custom_parent.setFlags(custom_parent.flags() & ~Qt.ItemIsSelectable)
        self._tree.addTopLevelItem(custom_parent)

        # ── 子节点：各集合 ──
        restored = False
        for b in self._all_batches:
            child = QTreeWidgetItem([f"{b.name} ({b.target_count})"])
            child.setData(0, Qt.UserRole, b.id)
            custom_parent.addChild(child)
            if prev_batch_id is not None and b.id == prev_batch_id:
                self._tree.setCurrentItem(child)
                restored = True

        custom_parent.setExpanded(True)

        # 恢复选中：默认选未分类（有则选），否则选第一个集合
        if not restored:
            if uncat:
                self._tree.setCurrentItem(u)
            elif custom_parent.childCount() > 0:
                self._tree.setCurrentItem(custom_parent.child(0))

        self._tree.blockSignals(False)
        if not self._tree.currentItem() and self._tree.topLevelItemCount() > 0:
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

        if self._search.text().strip():
            self._filter(self._search.text())

    def _filter(self, text: str):
        s = text.strip().lower()

        def _match(item):
            return s in item.text(0).lower()

        def _show_branch(item, visible: bool):
            item.setHidden(not visible)

        # 遍历所有顶层节点
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.childCount() == 0:
                # 无子节点：直接匹配
                top.setHidden(s not in top.text(0).lower() if s else False)
            else:
                # 有子节点：任一子节点匹配就显示父节点
                any_visible = False
                for j in range(top.childCount()):
                    child = top.child(j)
                    match = s in child.text(0).lower() if s else True
                    child.setHidden(not match)
                    if match:
                        any_visible = True
                top.setHidden(not any_visible if s else False)

    def _on_selected(self, current, previous):
        if not current:
            return
        bid = current.data(0, Qt.UserRole)
        self.batch_changed.emit(bid)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        bid = item.data(0, Qt.UserRole)
        if bid in (None, 0):
            return  # 不显示全部/未分类/分类父节点的右键菜单

        menu = QMenu(self)
        menu.addAction("编辑", lambda: self._on_edit())
        menu.addAction("删除", lambda: self._on_delete())
        menu.exec(self._tree.mapToGlobal(pos))

    def _on_new(self):
        from src.ui.main_window import BatchDialog
        dlg = BatchDialog("新建集合", parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.add_batch(dlg.name, dlg.description)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建集合失败:\n{e}")

    def _on_edit(self):
        item = self._tree.currentItem()
        if not item:
            return
        bid = item.data(0, Qt.UserRole)
        if bid in (None, 0):
            QMessageBox.information(self, "提示", "请选择自定义集合。")
            return
        batch = self._db.get_batch(bid)
        if not batch:
            return
        from src.ui.main_window import BatchDialog
        dlg = BatchDialog("编辑集合", batch.name, batch.description, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.update_batch(bid, dlg.name, dlg.description)
                self.refresh(bid)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新集合失败:\n{e}")

    def _on_delete(self):
        selected = self._tree.selectedItems()
        valid = [(it.data(0, Qt.UserRole), it.text(0)) for it in selected
                 if it.data(0, Qt.UserRole) not in (None, 0)]
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



# ── 连通测试主面板 ──────────────────────────────────────────


class ConnectivityPanel(QWidget):
    """连通测试面板 —— 左侧固定集合分类 + 右侧 3 个子标签页。"""

    targets_changed = Signal()
    protocol_test_selected = Signal(str, int)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧: 集合分类（固定）
        self._batch_tab = _BatchListTab(self._db)
        self._batch_tab.batch_changed.connect(self._on_batch_changed)
        splitter.addWidget(self._batch_tab)

        # 右侧: 功能标签页
        self._tabs = QTabWidget()

        # Tab 0: 目标管理
        self._target_panel = TargetPanel(self._db)
        self._target_panel.targets_changed.connect(self._on_targets_changed)
        self._target_panel.test_selected.connect(self._on_test_selected)
        self._target_panel.protocol_test_selected.connect(
            self.protocol_test_selected.emit
        )
        self._tabs.addTab(self._target_panel, "目标管理")

        # Tab 1: 连通测试
        self._test_panel = TestPanel(self._db)
        self._test_panel.test_finished.connect(self._on_test_finished)
        self._tabs.addTab(self._test_panel, "连通测试")

        # Tab 2: 测试历史
        self._result_panel = ResultPanel(self._db)
        self._tabs.addTab(self._result_panel, "测试历史")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 880])

        layout.addWidget(splitter)

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._batch_tab.refresh()

    # ── 信号转发 ────────────────────────────────────────────

    def _on_tab_changed(self, idx: int):
        if idx == 2:  # 测试历史
            self._result_panel.refresh()

    def _on_batch_changed(self, batch_id):
        self._target_panel.set_batch(batch_id)
        self._test_panel.set_batch(batch_id)

    def _on_targets_changed(self):
        self._batch_tab.refresh()
        self.targets_changed.emit()

    def _on_test_selected(self, target_ids: list[int]):
        self._tabs.setCurrentIndex(1)  # 切换到连通测试
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
