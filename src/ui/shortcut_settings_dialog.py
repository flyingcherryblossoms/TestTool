"""快捷键设置对话框：查看并修改全局快捷键绑定。

点击「快捷键」单元格进入录制态，按下新组合键即写入；
Esc 取消录制、Backspace 清空该动作的绑定（禁用）。
保存前做跨动作冲突校验，冲突时阻止关闭。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.ui import shortcuts
from src.ui.table_utils import enable_fill_autofit, refresh_tooltips

_PLACEHOLDER = "按下新快捷键…（Esc 取消 / Backspace 清空）"

# 参与录制的修饰键（其余如 NumLock 不写入绑定）
_MOD_MASK = (Qt.ControlModifier | Qt.AltModifier
             | Qt.ShiftModifier | Qt.MetaModifier)

# 纯修饰键，录制时忽略（event.key() 返回 int，这里存 int）
_MODIFIER_KEYS = {
    int(Qt.Key_Control), int(Qt.Key_Shift), int(Qt.Key_Alt),
    int(Qt.Key_Meta), int(Qt.Key_AltGr), int(Qt.Key_CapsLock),
    int(Qt.Key_NumLock), int(Qt.Key_ScrollLock),
}


class ShortcutSettingsDialog(QDialog):
    """快捷键设置对话框。exec() 返回 Accepted 后从 .shortcuts 取结果。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.setMinimumSize(520, 480)
        self._db = db
        # 当前生效绑定（含默认合并），对话框内独立一份，取消不影响全局
        effective = shortcuts.load(db)
        self._bindings: list[list[str]] = [
            list(effective.get(aid, seqs)) for aid, _, seqs in shortcuts.ACTIONS
        ]
        self._recording_row: int | None = None
        self._result: dict = {}
        self._setup_ui()
        self._populate()

    # ── UI ────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "点击「快捷键」单元格后按下新组合键录制；Backspace 清空（禁用）；"
            "Esc 取消录制。"))

        self._table = QTableWidget(len(shortcuts.ACTIONS), 3)
        self._table.setHorizontalHeaderLabels(["功能", "快捷键", "恢复默认"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        # 快捷键列填满剩余宽度；功能列按内容自适应；恢复默认列固定按钮宽度。
        # 内容被列宽截断的单元格由 refresh_tooltips 设置 hover 提示。
        enable_fill_autofit(self._table, stretch_cols=[1])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        reset_all = QPushButton("全部恢复默认")
        reset_all.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_all)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _populate(self):
        for row, (aid, desc, defaults) in enumerate(shortcuts.ACTIONS):
            self._table.setItem(row, 0, QTableWidgetItem(desc))
            self._refresh_cell(row)
            btn = QPushButton("恢复默认")
            btn.clicked.connect(lambda _=False, r=row: self._reset_row(r))
            self._table.setCellWidget(row, 2, btn)
        # 功能列按内容自适应（最长功能名决定列宽），快捷键列保持填满剩余宽度
        self._table.resizeColumnToContents(0)
        refresh_tooltips(self._table)

    def _refresh_cell(self, row: int):
        item = QTableWidgetItem(" / ".join(self._bindings[row]) or "无")
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, 1, item)
        refresh_tooltips(self._table)

    def _reset_row(self, row: int):
        self._bindings[row] = list(shortcuts.ACTIONS[row][2])
        self._recording_row = None
        self._refresh_cell(row)

    def _reset_all(self):
        for row in range(len(shortcuts.ACTIONS)):
            self._bindings[row] = list(shortcuts.ACTIONS[row][2])
        self._recording_row = None
        for row in range(len(shortcuts.ACTIONS)):
            self._refresh_cell(row)

    # ── 录制 ──────────────────────────────────────────────

    def _on_item_clicked(self, item):
        row, col = item.row(), item.column()
        if col != 1:
            # 点击其他单元格取消录制
            self._recording_row = None
            return
        if self._recording_row == row:
            self._recording_row = None
            self._refresh_cell(row)
            return
        self._recording_row = row
        self._table.item(row, 1).setText(_PLACEHOLDER)
        refresh_tooltips(self._table)

    def keyPressEvent(self, event):
        if self._recording_row is None:
            return super().keyPressEvent(event)
        row = self._recording_row
        key = event.key()  # 已是 int
        mods = event.modifiers() & _MOD_MASK
        if key in _MODIFIER_KEYS:
            event.accept()
            return
        if key == int(Qt.Key_Escape):
            self._recording_row = None
            self._refresh_cell(row)
            event.accept()
            return
        if key == int(Qt.Key_Backspace):
            self._bindings[row] = []
            self._recording_row = None
            self._refresh_cell(row)
            event.accept()
            return
        seq = QKeySequence(mods.value | key)
        if not seq.isEmpty():
            self._bindings[row] = [seq.toString()]
        self._recording_row = None
        self._refresh_cell(row)
        event.accept()

    # ── 保存与冲突校验 ────────────────────────────────────

    def _on_accept(self):
        result: dict = {}
        seen: dict[str, str] = {}
        conflicts: list[tuple[str, str, str]] = []
        for (aid, desc, _), bindings in zip(shortcuts.ACTIONS, self._bindings):
            result[aid] = list(bindings)
            for seq in bindings:
                other = seen.get(seq)
                if other is not None:
                    conflicts.append((seq, desc, other))
                else:
                    seen[seq] = desc
        if conflicts:
            lines = "\n".join(
                f"{seq}：{desc_a} 与 {desc_b} 冲突" for seq, desc_a, desc_b in conflicts)
            QMessageBox.warning(self, "快捷键冲突", f"以下快捷键被多个功能使用：\n{lines}")
            return
        self._result = result
        self.accept()

    @property
    def shortcuts(self) -> dict:
        return self._result
