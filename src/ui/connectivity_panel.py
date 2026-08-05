"""连通测试面板 —— 整合集合管理、目标管理、连通测试、测试历史为子标签页。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.csv_handler import export_targets_to_csv, parse_targets_csv
from src.database import Database
from src.excel_handler import export_targets_to_excel, parse_targets_excel
from src.ui.test_panel import TestPanel
from src.ui.result_panel import ResultPanel
from src.ui.table_utils import ReorderableTree


class _CollectionListTab(QWidget):
    """集合列表 —— 分类树形结构：全部 / 未分类 / 自定义集合。"""

    collection_changed = Signal(object)  # collection_id | None

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_collections = []
        self._reorder_timer = QTimer(self)
        self._reorder_timer.setSingleShot(True)
        self._reorder_timer.timeout.connect(self._save_collection_order)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索集合...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._tree = ReorderableTree()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.currentItemChanged.connect(self._on_selected)
        self._tree.setIndentation(16)
        self._tree.order_changed.connect(self._on_collections_moved)
        layout.addWidget(self._tree)

        # 底部仅保留导入/导出按钮，新建/编辑/删除等操作集成在右键菜单里
        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("导入", clicked=self._on_import))
        btn_row.addWidget(QPushButton("导出", clicked=self._on_export))
        layout.addLayout(btn_row)

    def refresh(self, select_id: int | None = None):
        current = self._tree.currentItem()
        prev_collection_id = current.data(0, Qt.UserRole) if current else None
        if select_id is not None:
            prev_collection_id = select_id

        self._tree.blockSignals(True)
        self._tree.clear()
        self._all_collections = self._db.get_all_collections()
        bold_font = self._tree.font()
        bold_font.setBold(True)

        # ── 一级节点：未分类（有数据才显示）──
        uncat = self._db.get_targets(0)
        if uncat:
            u = QTreeWidgetItem([f"未分类 ({len(uncat)})"])
            u.setData(0, Qt.UserRole, 0)
            u.setFont(0, bold_font)
            # 未分类节点不可拖拽/不可作为投放目标
            u.setFlags(u.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
            self._tree.addTopLevelItem(u)

        # ── 父节点：自定义集合 ──
        custom_parent = QTreeWidgetItem([f"自定义集合 ({len(self._all_collections)})"])
        custom_parent.setData(0, Qt.UserRole, None)
        custom_parent.setFont(0, bold_font)
        custom_parent.setFlags(
            custom_parent.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsDragEnabled
        )
        self._tree.addTopLevelItem(custom_parent)

        # ── 子节点：各集合 ──
        restored = False
        for b in self._all_collections:
            child = QTreeWidgetItem([f"{b.name} ({b.target_count})"])
            child.setData(0, Qt.UserRole, b.id)
            custom_parent.addChild(child)
            if prev_collection_id is not None and b.id == prev_collection_id:
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

        # 重建期间 blockSignals 屏蔽了 setCurrentItem 的信号，刷新后需手动
        # 同步默认选中的集合，否则启动时目标列表不加载数据。
        cur = self._tree.currentItem()
        if cur:
            self._on_selected(cur, None)

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
        if bid is None:
            return  # 自定义集合父节点不触发
        self.collection_changed.emit(bid)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("新建集合", self._on_new)
        if item:
            bid = item.data(0, Qt.UserRole)
            if bid not in (None, 0):
                menu.addAction("编辑", self._on_edit)
                menu.addAction("删除", self._on_delete)
        menu.addSeparator()
        menu.addAction("导入集合", self._on_import)
        menu.addAction("导出", self._on_export)
        menu.exec(self._tree.mapToGlobal(pos))

    def _on_collections_moved(self):
        """拖拽排序后防抖保存顺序。"""
        self._reorder_timer.start(80)

    def _save_collection_order(self):
        """按当前「自定义集合」子节点顺序持久化集合排序。"""
        parent = None
        for i in range(self._tree.topLevelItemCount()):
            if self._tree.topLevelItem(i).data(0, Qt.UserRole) is None:
                parent = self._tree.topLevelItem(i)
                break
        if parent is None:
            return
        ordered_ids = []
        for i in range(parent.childCount()):
            cid = parent.child(i).data(0, Qt.UserRole)
            if cid:
                ordered_ids.append(cid)
        if ordered_ids:
            self._db.update_collections_order(ordered_ids)

    def _on_new(self):
        from src.ui.main_window import CollectionDialog
        count = len(self._db.get_all_collections())
        dlg = CollectionDialog("新建集合", name=f"连通性测试集合{count + 1}", parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.add_collection(dlg.name)
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
        collection = self._db.get_collection(bid)
        if not collection:
            return
        from src.ui.main_window import CollectionDialog
        dlg = CollectionDialog("编辑集合", collection.name, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self._db.update_collection(bid, dlg.name)
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
        names = "\n".join(f"  • {name}" for _, name in valid)
        msg = f"确定删除以下 {len(valid)} 个集合？\n\n{names}"
        r = QMessageBox.question(self, "确认删除", msg,
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for bid, _ in valid:
                self._db.delete_collection(bid)
            self.refresh()

    def _on_import(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入目标", "", "表格文件 (*.csv *.xlsx *.xls);;所有文件 (*)")
        if not filepath:
            return
        ext = Path(filepath).suffix.lower()
        try:
            if ext == ".csv":
                targets, errors = parse_targets_csv(filepath)
            else:
                targets, errors = parse_targets_excel(filepath)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        if not targets:
            QMessageBox.information(self, "提示", "文件中没有有效数据。")
            return
        if errors:
            QMessageBox.warning(self, "导入警告", f"部分数据解析失败:\n{chr(10).join(errors[:10])}")
        total = len(targets)
        if total > 1000:
            reply = QMessageBox.question(
                self, "确认导入", f"检测到 {total} 条数据，确定导入？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        # 统一转为 dict
        if targets and not isinstance(targets[0], dict):
            targets = [{"ip": t.ip, "port": t.port, "description": t.description,
                        "collection_name": t.collection_name} for t in targets]
        progress = QProgressDialog("正在导入...", "取消", 0, len(targets), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        count = 0
        collection_cache = {}
        for c in self._db.get_all_collections():
            collection_cache[c.name] = c.id
        for i, t in enumerate(targets):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            cname = t.get("collection_name", "")
            cid = collection_cache.get(cname) if cname else None
            if cname and cid is None:
                cid = self._db.add_collection(cname)
                collection_cache[cname] = cid
            if not self._db.target_exists(t["ip"], t["port"], cid):
                self._db.add_target(t["ip"], t["port"], t.get("description", ""), cid)
                count += 1
        progress.setValue(len(targets))
        self.refresh()
        QMessageBox.information(self, "导入完成", f"成功导入 {count} 条记录。")

    def _on_export(self):
        selected = self._tree.selectedItems()
        valid = [it.data(0, Qt.UserRole) for it in selected
                 if it.data(0, Qt.UserRole) not in (None, 0)]
        if valid:
            targets = []
            for bid in valid:
                targets.extend(self._db.get_targets(bid))
        else:
            reply = QMessageBox.question(
                self, "导出确认",
                "未选中集合，是否导出所有目标数据？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return
            targets = self._db.get_targets(None)
        if not targets:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", "targets.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv)")
        if not filepath:
            return
        data = [{"ip": t.ip, "port": t.port, "description": t.description,
                 "collection_name": t.collection_name or ""} for t in targets]
        ext = Path(filepath).suffix.lower()
        if ext == ".csv":
            from src.csv_handler import export_targets_to_csv
            ok, err = export_targets_to_csv(filepath, data)
        else:
            from src.excel_handler import export_targets_to_excel
            ok, err = export_targets_to_excel(filepath, data)
        if ok:
            QMessageBox.information(self, "导出完成", f"成功导出 {len(data)} 条记录。")
        else:
            QMessageBox.critical(self, "导出失败", str(err))



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
        self._collection_tab = _CollectionListTab(self._db)
        self._collection_tab.collection_changed.connect(self._on_collection_changed)
        splitter.addWidget(self._collection_tab)

        # 右侧: 功能标签页
        self._tabs = QTabWidget()

        # Tab 0: 连通测试（内含目标列表，已集成目标管理）
        self._test_panel = TestPanel(self._db)
        self._test_panel.test_finished.connect(self._on_test_finished)
        self._test_panel.targets_changed.connect(self._on_targets_changed)
        self._test_panel.protocol_test_selected.connect(
            self.protocol_test_selected.emit
        )
        self._tabs.addTab(self._test_panel, "连通测试")

        # Tab 1: 测试历史
        self._result_panel = ResultPanel(self._db)
        self._tabs.addTab(self._result_panel, "测试历史")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 880])

        layout.addWidget(splitter)

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._collection_tab.refresh()

    # ── 信号转发 ────────────────────────────────────────────

    def _on_tab_changed(self, idx: int):
        if idx == 1:  # 测试历史
            self._result_panel.refresh()

    def _on_collection_changed(self, collection_id):
        self._test_panel.set_collection(collection_id)

    def _on_targets_changed(self):
        self._collection_tab.refresh()
        self.targets_changed.emit()

    def _on_test_finished(self):
        self._result_panel.refresh()
        self.targets_changed.emit()

    # ── 公共接口 ────────────────────────────────────────────

    def refresh_collection_list(self):
        self._collection_tab.refresh()

    def is_test_running(self) -> bool:
        return self._test_panel.is_running()

    def stop_test(self):
        if self._test_panel.is_running():
            self._test_panel._cancel_test()
