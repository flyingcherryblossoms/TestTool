"""集合管理侧边栏 —— 连通测试 / 协议测试共用的集合分类树组件。

两个面板各自创建一个实例，通过子类重写集合访问方法接入各自的数据库表
（connect_* 与 protocol_*）。本基类负责统一的树形结构、搜索过滤、
新建/重命名/删除、拖拽排序以及拖拽目标归集。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.ui import shortcuts
from src.ui.clipboard import KIND_COLLECTION, copy_items, paste_items
from src.ui.table_utils import ReorderableTree, unique_copy_name


class CollectionSidebarBase(QWidget):
    """集合分类树：未分类 + 自定义集合。

    结构:
      未分类 (N)          <- UserRole = _uncat_node_id()
      自定义集合 (M)       <- 父节点，UserRole = None（不可选中）
        ├─ 集合A (N)
        └─ 集合B (N)

    子类需实现集合访问方法（见下方 NotImplementedError 方法），并可按需重写
    _on_import / _on_export / _build_collection_menu / _build_blank_menu。
    """

    collection_selected = Signal(object)  # 集合 id（未分类=uncat_node_id）或 None

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._current_cid: int | None = None
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

    # ── 子类需实现的集合访问方法 ─────────────────────────────

    def _get_all_collections(self) -> list:
        """所有集合对象列表（含 .id / .name）。"""
        raise NotImplementedError

    def _ensure_uncat(self):
        """确保"未分类"集合存在并返回其对象。"""
        raise NotImplementedError

    def _uncat_node_id(self) -> int | None:
        """"未分类"节点 UserRole。连通测试=0；协议=真实"未分类"集合 id。"""
        raise NotImplementedError

    def _count_targets(self, cid: int | None) -> int:
        """集合内目标数量（cid=0 时为未分类目标数）。"""
        raise NotImplementedError

    def _get_collection(self, cid: int):
        """按 id 获取集合对象。"""
        raise NotImplementedError

    def _add_collection(self, name: str) -> int:
        """新建集合，返回 id。"""
        raise NotImplementedError

    def _update_collection(self, cid: int, name: str) -> None:
        raise NotImplementedError

    def _delete_collection(self, cid: int) -> None:
        raise NotImplementedError

    def _move_to_uncat(self, cid: int) -> None:
        """删除集合前把其目标移入未分类。连通测试默认依赖外键 SET NULL，无需处理。"""

    def _save_collections_order(self, ordered_ids: list[int]) -> None:
        raise NotImplementedError

    def _new_collection_prefix(self) -> str:
        return "集合"

    # ── 可重写的 UI 行为 ─────────────────────────────────────

    def _on_import(self):
        pass

    def _on_export(self):
        pass

    def _build_collection_menu(self, menu: QMenu, item, cid: int) -> None:
        """自定义集合右键动作（新建目标 / 刷新 / 重命名 / 删除）。"""

    def _build_blank_menu(self, menu: QMenu) -> None:
        """空白 / 未分类 / 父节点右键：统一提供刷新。子类可追加动作。"""
        menu.addAction("刷新集合", self.refresh)

    # ── 刷新与筛选 ───────────────────────────────────────────

    def refresh(self, select_id: int | None = None):
        """重建集合树，保留当前选中（或指定 select_id 集合）。"""
        current = self._tree.currentItem()
        prev_cid = current.data(0, Qt.UserRole) if current else None
        if select_id is not None:
            prev_cid = select_id

        self._tree.blockSignals(True)
        self._tree.clear()

        self._ensure_uncat()
        uncat_id = self._uncat_node_id()

        collections = self._get_all_collections()
        bold_font = self._tree.font()
        bold_font.setBold(True)

        # ── 一级节点：未分类（始终显示）──
        u = QTreeWidgetItem([f"未分类 ({self._uncat_count()})"])
        u.setData(0, Qt.UserRole, uncat_id)
        u.setFont(0, bold_font)
        # 未分类节点不可拖拽/不可作为排序投放目标
        u.setFlags(u.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
        self._tree.addTopLevelItem(u)

        # ── 父节点：自定义集合（排除未分类）──
        custom_cols = [b for b in collections if getattr(b, "name", "") != "未分类"]
        custom_parent = QTreeWidgetItem([f"自定义集合 ({len(custom_cols)})"])
        custom_parent.setData(0, Qt.UserRole, None)
        custom_parent.setFont(0, bold_font)
        custom_parent.setFlags(
            custom_parent.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsDragEnabled
        )
        self._tree.addTopLevelItem(custom_parent)

        # ── 子节点：各集合 ──
        restored = False
        for b in custom_cols:
            child = QTreeWidgetItem([f"{b.name} ({self._count_targets(b.id)})"])
            child.setData(0, Qt.UserRole, b.id)
            custom_parent.addChild(child)
            if prev_cid is not None and b.id == prev_cid:
                self._tree.setCurrentItem(child)
                restored = True

        custom_parent.setExpanded(True)
        if not restored:
            # 默认选中第一个自定义集合，没有自定义集合才选中未分类
            if custom_parent.childCount() > 0:
                self._tree.setCurrentItem(custom_parent.child(0))
            else:
                self._tree.setCurrentItem(u)

        # 重建期间 blockSignals 屏蔽了 setCurrentItem，刷新后需手动同步选中集合
        self._tree.blockSignals(False)
        cur = self._tree.currentItem()
        if cur:
            self._on_selected(cur, None)

        if self._search.text().strip():
            self._filter(self._search.text())

    def _uncat_count(self) -> int:
        """"未分类"节点显示的数量。默认按未分类口径统计。"""
        return self._count_targets(self._uncat_node_id())

    def _filter(self, text: str):
        s = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.childCount() == 0:
                top.setHidden(s not in top.text(0).lower() if s else False)
            else:
                any_visible = False
                for j in range(top.childCount()):
                    child = top.child(j)
                    match = s in child.text(0).lower() if s else True
                    child.setHidden(not match)
                    if match:
                        any_visible = True
                top.setHidden(not any_visible if s else False)

    # ── 选中与拖拽排序 ───────────────────────────────────────

    def _on_selected(self, current, previous):
        if not current:
            return
        cid = current.data(0, Qt.UserRole)
        self._current_cid = cid
        self.collection_selected.emit(cid)

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
            self._save_collections_order(ordered_ids)

    # ── 右键菜单 ─────────────────────────────────────────────

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        # 右键集合时：若未多选或点击了未选中的项，则单选该项
        if item is not None and item.data(0, Qt.UserRole) is not None:
            selected = self._tree.selectedItems()
            if item not in selected:
                self._tree.clearSelection()
            self._tree.setCurrentItem(item)
        cid = item.data(0, Qt.UserRole) if item else None
        is_custom = cid is not None and cid != self._uncat_node_id()

        menu = QMenu(self)
        menu.addAction("新建集合", self._on_new)
        if is_custom:
            menu.addSeparator()
            self._build_collection_menu(menu, item, cid)
            menu.addAction("复制集合", lambda *_, cid=cid: self._copy_collection(cid))
        else:
            # 空白 / 未分类 / 自定义集合父节点：统一提供"刷新集合"
            self._build_blank_menu(menu)
        menu.addSeparator()
        menu.addAction("导入集合", self._on_import)
        menu.addAction("导出集合", self._on_export)
        menu.exec(self._tree.mapToGlobal(pos))

    # ── 新建 / 编辑 / 删除 ───────────────────────────────────

    def _on_new(self):
        from src.ui.main_window import CollectionDialog
        count = len([c for c in self._get_all_collections()
                     if getattr(c, "name", "") != "未分类"])
        dlg = CollectionDialog("新建集合",
                               name=f"{self._new_collection_prefix()}集合{count + 1}",
                               parent=self)
        if dlg.exec() != QDialog.Accepted or not dlg.name:
            return
        cid = self._add_collection(dlg.name)
        self.refresh()
        # 选中新建的集合
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.UserRole) == cid:
                    self._tree.setCurrentItem(child)
                    return

    def _copy_collection(self, cid: int):
        """深拷贝集合：复制集合及其下全部目标，名称追加"副本"。

        集合本身经 _add_collection 创建（协议集合保留源集合的 protocol_type），
        目标经 _copy_collection_targets 逐个复制。
        """
        collection = self._get_collection(cid)
        if not collection:
            return
        existing = {c.name for c in self._get_all_collections()}
        new_name = unique_copy_name(getattr(collection, "name", ""), existing)
        kwargs = {}
        pt = getattr(collection, "protocol_type", None)
        if pt:
            kwargs["protocol_type"] = pt
        new_id = self._add_collection(new_name, **kwargs)
        self._copy_collection_targets(cid, new_id)
        self.refresh(select_id=new_id)

    def _copy_collection_targets(self, src_cid: int, new_cid: int) -> None:
        """把源集合的全部目标复制到新集合。子类实现。"""
        raise NotImplementedError

    def _copy_collections_to_clip(self):
        """Ctrl+C：把选中的集合 id 复制到应用内剪贴板。"""
        selected = self._tree.selectedItems()
        uncat_id = self._uncat_node_id()
        cids = [it.data(0, Qt.UserRole) for it in selected
                if it.data(0, Qt.UserRole) not in (None, uncat_id)]
        if cids:
            copy_items(KIND_COLLECTION, cids)

    def _paste_collections_from_clip(self):
        """Ctrl+V：把剪贴板中的集合粘贴为副本（深拷贝集合及目标）。"""
        cids = paste_items(KIND_COLLECTION)
        valid = [c for c in cids if self._get_collection(c)]
        if not valid:
            return QMessageBox.information(self, "提示", "剪贴板中没有可粘贴的集合。")
        for cid in valid:
            self._copy_collection(cid)

    def _on_edit(self):
        item = self._tree.currentItem()
        if not item:
            return
        cid = item.data(0, Qt.UserRole)
        if cid in (None, self._uncat_node_id()):
            QMessageBox.information(self, "提示", "请选择自定义集合。")
            return
        collection = self._get_collection(cid)
        if not collection:
            return
        from src.ui.main_window import CollectionDialog
        dlg = CollectionDialog("编辑集合", collection.name, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._update_collection(cid, dlg.name)
            self.refresh(cid)

    def _on_delete(self):
        selected = self._tree.selectedItems()
        # 右键只设当前项不选中，未多选时退回当前项
        if not selected and self._tree.currentItem() is not None:
            selected = [self._tree.currentItem()]
        uncat_id = self._uncat_node_id()
        valid = [(it.data(0, Qt.UserRole), it.text(0)) for it in selected
                 if it.data(0, Qt.UserRole) not in (None, uncat_id)]
        if not valid:
            QMessageBox.information(self, "提示", "请选择自定义集合。")
            return
        names = "\n".join(f"  • {name}" for _, name in valid)
        msg = (f"确定删除以下 {len(valid)} 个集合？\n\n{names}\n\n"
               f"其中的目标将移动到「未分类」。")
        r = QMessageBox.question(self, "确认删除", msg,
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            for cid, _ in valid:
                self._move_to_uncat(cid)
                self._delete_collection(cid)
            self.refresh()

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "copy"):
            self._copy_collections_to_clip()
        elif shortcuts.event_matches(event, "paste"):
            self._paste_collections_from_clip()
        elif shortcuts.event_matches(event, "refresh"):
            self.refresh()
        elif shortcuts.event_matches(event, "delete"):
            self._on_delete()
        else:
            super().keyPressEvent(event)
