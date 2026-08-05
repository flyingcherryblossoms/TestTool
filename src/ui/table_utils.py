"""表格列宽自适应工具。

统一所有 QTableWidget 的列宽行为：
- 列宽按数据内容自适应（数据填充完成后调用 autofit_columns）
- 列宽可手动拖动调整（Interactive）
- 内容超出表格宽度时出现横向滚动条，可拖动查看
- 单元格内容被列宽截断时，鼠标悬停显示完整数据 tooltip
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTreeWidget,
)


class ReorderableTree(QTreeWidget):
    """支持内部拖拽排序的树 —— 仅允许在相同父节点下调整顺序。

    拖拽完成后发出 order_changed 信号，由外部持久化新顺序。
    非集合节点应在填充时去掉 ItemIsDragEnabled/ItemIsDropEnabled 标志。
    """

    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
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
