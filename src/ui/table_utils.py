"""表格列宽自适应工具。

统一所有 QTableWidget 的列宽行为：
- 列宽按数据内容自适应（数据填充完成后调用 autofit_columns）
- 列宽可手动拖动调整（Interactive）
- 内容超出表格宽度时出现横向滚动条，可拖动查看
- 单元格内容被列宽截断时，鼠标悬停显示完整数据 tooltip

拖拽目标到集合：
- TargetDragTable 作为目标表格，拖动时把选中目标 ID 写入 TARGETS_MIME。
- ReorderableTree 作为集合树，接收该 MIME 后发出 targets_dropped(coll_id, ids)。
"""

from __future__ import annotations

import json
from functools import partial

from PySide6.QtCore import Qt, QMimeData, QPoint, Signal
from PySide6.QtGui import QColor, QDrag, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTreeWidget,
)


# 拖动目标到集合时传递目标 ID 的自定义 MIME 类型
TARGETS_MIME = "application/x-testtool-target-ids"


def _make_drag_pixmap(text: str) -> QPixmap:
    """渲染一段文本为拖拽缩略图，避免 QPixmap::scaled 空图警告。"""
    from PySide6.QtGui import QFont, QFontMetrics, QPainter
    font = QFont("Arial", 11)
    fm = QFontMetrics(font)
    w = max(fm.horizontalAdvance(text) + 20, 60)
    h = fm.height() + 8
    pixmap = QPixmap(w, h)
    pixmap.fill(QColor("#3498db"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
    painter.end()
    return pixmap


class ReorderableTree(QTreeWidget):
    """支持内部拖拽排序的树 —— 仅允许在相同父节点下调整顺序。

    拖拽完成后发出 order_changed 信号，由外部持久化新顺序。
    非集合节点应在填充时去掉 ItemIsDragEnabled/ItemIsDropEnabled 标志。
    同时接收目标表格拖入的目标：dropEvent 时解析 TARGETS_MIME，
    发出 targets_dropped(collection_id, target_ids) 信号。
    """

    order_changed = Signal()
    targets_dropped = Signal(int, list)  # collection_id, target_ids

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setAcceptDrops(True)

    def startDrag(self, supported_actions):
        """自定义拖拽：用 QPixmap 渲染项目文本，避免空图警告。"""
        items = self.selectedItems()
        if not items:
            return
        mime = self.mimeData(items)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        text = items[0].text(0) if items else ""
        pixmap = _make_drag_pixmap(text)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(10, 5))
        drag.exec(supported_actions, Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TARGETS_MIME):
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TARGETS_MIME):
            item = self.itemAt(event.position().toPoint())
            # 仅允许落在集合节点上（UserRole 存集合 ID）；父节点 UserRole 为 None，不接受
            if item is not None and item.data(0, Qt.UserRole) is not None:
                event.accept()
                return
            event.ignore()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(TARGETS_MIME):
            item = self.itemAt(event.position().toPoint())
            if item is None:
                event.ignore()
                return
            coll_id = item.data(0, Qt.UserRole)
            if coll_id is None:
                event.ignore()
                return
            try:
                ids = [int(x) for x in
                       json.loads(bytes(event.mimeData().data(TARGETS_MIME)).decode("utf-8"))]
            except Exception:
                event.ignore()
                return
            if not ids:
                event.ignore()
                return
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self.targets_dropped.emit(coll_id, ids)
            return
        # 记录被拖拽项及其原父节点，防止被拖出原父节点
        dragged = [(it, it.parent()) for it in self.selectedItems()]
        super().dropEvent(event)
        if event.isAccepted():
            for item, orig_parent in dragged:
                if item.parent() is not orig_parent:
                    if orig_parent is not None:
                        orig_parent.addChild(item)
                    else:
                        self.addTopLevelItem(item)
            self.order_changed.emit()


class TargetDragTable(QTableWidget):
    """可拖动目标行的表格 —— 拖动时把选中目标 ID 写入 TARGETS_MIME，供集合树接收。

    使用约定：目标 ID 存于第 0 列单元格的 Qt.UserRole。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def startDrag(self, supported_actions):
        ids = []
        names = []
        for idx in self.selectionModel().selectedRows(0):
            item = self.item(idx.row(), 0)
            if item is not None and item.data(Qt.UserRole) is not None:
                ids.append(item.data(Qt.UserRole))
                names.append(item.text())
        if not ids:
            return
        mime = QMimeData()
        mime.setData(TARGETS_MIME, json.dumps(ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        label = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        drag.setPixmap(_make_drag_pixmap(label))
        drag.setHotSpot(QPoint(10, 5))
        drag.exec(Qt.MoveAction)


def enable_autofit(table: QTableWidget) -> None:
    """表格创建后调用一次：开启列宽自适应 + 可调整 + 溢出横向滚动 + 截断 tooltip。

    应在 setColumnCount / setHorizontalHeaderLabels 之后调用，
    会覆盖此前设置的 Stretch / Fixed 列宽模式。
    """
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.Interactive)
    hdr.setMinimumSectionSize(40)
    hdr.setStretchLastSection(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    # 列宽被拖动调整后实时刷新该列 tooltip
    hdr.sectionResized.connect(partial(_on_section_resized, table))


def autofit_columns(table: QTableWidget) -> None:
    """数据填充完成后调用：按内容自适应列宽，并为被截断的单元格设置 tooltip。"""
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.Interactive)
    table.resizeColumnsToContents()
    _refresh_all_tooltips(table)


def enable_stretch_fill(table: QTableWidget,
                        fixed_cols: list[int] | None = None) -> None:
    """所有列均 Stretch 填满表格可用宽度（新增列自动继承 Stretch）。

    fixed_cols 指定的列保持 Fixed（如复选框/操作按钮列），其余列等宽拉伸。
    数据填充完成后调用 refresh_tooltips() 刷新（不改变列宽）。
    """
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.Stretch)
    if fixed_cols:
        for col in fixed_cols:
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
    hdr.setStretchLastSection(False)
    hdr.setMinimumSectionSize(40)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    hdr.sectionResized.connect(partial(_on_section_resized, table))


def enable_fill_autofit(table: QTableWidget,
                        stretch_cols: list[int] | None = None) -> None:
    """表格列填满可用宽度：stretch_cols 列用 Stretch 吸收剩余宽度，其余 Interactive。

    仍可手动调整列宽，并保留"内容被截断时 hover 显示完整数据"的 tooltip 行为。
    数据填充完成后调用 refresh_tooltips() 刷新（不改变列宽）。
    """
    hdr = table.horizontalHeader()
    if stretch_cols is None:
        stretch_cols = list(range(table.columnCount()))
    for col in range(table.columnCount()):
        hdr.setSectionResizeMode(
            col, QHeaderView.Stretch if col in stretch_cols else QHeaderView.Interactive
        )
    hdr.setStretchLastSection(False)
    hdr.setMinimumSectionSize(40)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    hdr.sectionResized.connect(partial(_on_section_resized, table))


def refresh_tooltips(table: QTableWidget) -> None:
    """数据填充完成后调用：仅为被截断的单元格刷新 tooltip（不改变列宽）。"""
    _refresh_all_tooltips(table)


def _on_section_resized(table: QTableWidget, logical_index: int,
                        old_size: int, new_size: int) -> None:
    _refresh_col_tooltips(table, logical_index)


def _refresh_col_tooltips(table: QTableWidget, col: int) -> None:
    fm = table.fontMetrics()
    width = table.horizontalHeader().sectionSize(col)
    for row in range(table.rowCount()):
        # 单元格内嵌控件（如按钮）不处理
        if table.cellWidget(row, col) is not None:
            continue
        item = table.item(row, col)
        if item is None or not item.text():
            continue
        # resizeColumnsToContents 已包含 Qt 内部边距，此处直接比较文本宽度与列宽
        if fm.horizontalAdvance(item.text()) > width:
            item.setToolTip(item.text())
        else:
            item.setToolTip("")


def _refresh_all_tooltips(table: QTableWidget) -> None:
    for col in range(table.columnCount()):
        _refresh_col_tooltips(table, col)
