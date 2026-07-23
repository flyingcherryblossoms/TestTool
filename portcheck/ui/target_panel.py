"""目标管理面板 —— 管理 IP:Port 目标条目的增删改查和 CSV 导入导出。
支持 IP 范围（CIDR/范围）、端口范围展开、筛选和勾选测试。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portcheck.csv_handler import export_targets_to_csv, parse_targets_csv
from portcheck.database import Database
from portcheck.excel_handler import (
    export_targets_to_excel,
    parse_targets_excel,
)
from portcheck.scanner import build_scan_targets, expand_ip_range, expand_port_range


class TargetDialog(QDialog):
    """添加目标的对话框 —— 支持 IP 范围和端口范围。"""

    def __init__(self, db: Database, target_id: int | None = None,
                 parent=None):
        super().__init__(parent)
        self._db = db
        self._target_id = target_id
        self._targets: list[dict] = []  # 展开后的目标列表
        self.setWindowTitle("编辑目标" if target_id else "添加目标")
        self.setMinimumWidth(480)
        self._setup_ui()
        if target_id:
            self._load_target()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # IP 地址（支持范围和 CIDR）
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText(
            "单个: 192.168.1.1  范围: 192.168.1.1-10  CIDR: 10.0.0.0/24"
        )
        layout.addRow("IP 地址:", self._ip_edit)

        # 端口（支持范围和逗号分隔）
        self._port_edit = QLineEdit()
        self._port_edit.setPlaceholderText(
            "单个: 80  范围: 1-100  多个: 80,443,8080  混合: 80,443,8000-8010"
        )
        layout.addRow("端口:", self._port_edit)

        # 描述（{ip} 和 {port} 会自动替换）
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("{ip}:{port} 服务 (自动替换 IP/端口)")
        layout.addRow("描述:", self._desc_edit)

        # 集合选择
        self._batch_combo = QComboBox()
        self._batch_combo.addItem("(无集合)", None)
        for b in self._db.get_all_batches():
            self._batch_combo.addItem(f"{b.name} ({b.target_count})", b.id)
        layout.addRow("所属集合:", self._batch_combo)

        # 展开预览
        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addRow("", self._preview_label)

        # 实时预览
        self._ip_edit.textChanged.connect(self._update_preview)
        self._port_edit.textChanged.connect(self._update_preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._update_preview()

    def _update_preview(self):
        """实时显示 IP/端口展开预览。"""
        ip_text = self._ip_edit.text().strip()
        port_text = self._port_edit.text().strip()
        if not ip_text or not port_text:
            self._preview_label.setText("")
            return
        try:
            ips = expand_ip_range(ip_text)
            ports = expand_port_range(port_text)
            total = len(ips) * len(ports)
            if total == 1:
                self._preview_label.setText(f"→ 将添加 1 个目标: {ips[0]}:{ports[0]}")
            elif total <= 50:
                sample = ", ".join(f"{ip}:{p}" for ip in ips[:2] for p in ports[:3])
                if total > 6:
                    sample += " ..."
                self._preview_label.setText(f"→ 将添加 {total} 个目标: {sample}")
            else:
                self._preview_label.setText(f"→ 将添加 {total} 个目标 (范围较大)")
        except ValueError as e:
            self._preview_label.setText(f"⚠ {e}")

    def _load_target(self):
        t = self._db.get_target(self._target_id)
        if t:
            self._ip_edit.setText(t.ip)
            self._port_edit.setText(str(t.port))
            self._desc_edit.setText(t.description)
            idx = self._batch_combo.findData(t.batch_id)
            if idx >= 0:
                self._batch_combo.setCurrentIndex(idx)

    def _on_accept(self):
        ip_text = self._ip_edit.text().strip()
        port_text = self._port_edit.text().strip()
        if not ip_text:
            QMessageBox.warning(self, "验证失败", "IP 地址不能为空。")
            return
        if not port_text:
            QMessageBox.warning(self, "验证失败", "端口不能为空。")
            return

        try:
            ips = expand_ip_range(ip_text)
        except ValueError as e:
            QMessageBox.warning(self, "验证失败", str(e))
            return
        try:
            ports = expand_port_range(port_text)
        except ValueError as e:
            QMessageBox.warning(self, "验证失败", str(e))
            return

        desc_template = self._desc_edit.text().strip()
        batch_id = self._batch_combo.currentData()
        total = len(ips) * len(ports)

        # 范围较大时确认
        if total > 100:
            reply = QMessageBox.question(
                self, "确认",
                f"将添加 {total} 个目标（{len(ips)} 个 IP × {len(ports)} 个端口），确定继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        self._targets = []
        for ip in ips:
            for port in ports:
                if desc_template:
                    desc = desc_template.replace("{ip}", ip).replace("{port}", str(port))
                else:
                    desc = f"{ip}:{port}"
                self._targets.append({
                    "ip": ip, "port": port, "description": desc, "batch_id": batch_id
                })
        self.accept()

    @property
    def target_data(self) -> dict | None:
        """单目标模式：返回第一个目标的数据（编辑模式兼容）。"""
        if super().result() == QDialog.Accepted and self._targets:
            return self._targets[0]
        return None

    @property
    def target_list(self) -> list[dict]:
        """多目标模式：返回展开后的全部目标。"""
        if super().result() == QDialog.Accepted:
            return self._targets
        return []


class TargetPanel(QWidget):
    """目标管理面板。

    Signals:
        targets_changed:    目标数据发生变更时触发。
        test_selected:      携带勾选的目标 ID 列表，通知主窗口切换到测试页。
    """

    targets_changed = Signal()
    test_selected = Signal(list)  # list[target_id]

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._current_batch_id: int | None = None  # None=全部, 0=未分类
        self._all_targets: list = []  # 缓存当前全部目标用于筛选
        self._sort_col: int = -1  # 当前排序列（-1 为按 sort_order）
        self._sort_asc: bool = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 第一行: 标题 + 筛选 ──────────────────────────────
        top_layout = QHBoxLayout()
        self._info_label = QLabel("全部目标")
        top_layout.addWidget(self._info_label)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("筛选 IP/端口/描述...")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setMaximumWidth(280)
        self._filter_edit.textChanged.connect(self._apply_filter)
        top_layout.addWidget(self._filter_edit)

        self._status_filter = QComboBox()
        self._status_filter.addItem("全部状态", None)
        self._status_filter.addItem("✓ 连通", True)
        self._status_filter.addItem("✗ 未连通", False)
        self._status_filter.addItem("未测试", "untested")
        self._status_filter.currentIndexChanged.connect(self._apply_filter)
        top_layout.addWidget(self._status_filter)

        top_layout.addStretch()
        self._select_all_cb = QCheckBox("全选")
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        top_layout.addWidget(self._select_all_cb)
        layout.addLayout(top_layout)

        # ── 表格 ─────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "#", "", "IP 地址", "端口", "描述", "集合", "最近状态"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.setDragDropOverwriteMode(False)
        self._table.setDragDropMode(QAbstractItemView.InternalMove)
        self._table.setDropIndicatorShown(True)
        # 拖拽后的 debounce 重建（防止拖出列表外丢数据）
        self._table.model().rowsMoved.connect(self._schedule_drag_rebuild)
        self._table.model().rowsRemoved.connect(self._schedule_drag_rebuild)
        self._drag_rebuild_timer = QTimer(self)
        self._drag_rebuild_timer.setSingleShot(True)
        self._drag_rebuild_timer.timeout.connect(self._rebuild_after_drag)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.cellClicked.connect(self._on_cell_clicked)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 40)   # 序号
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 30)   # 复选框
        hh.setSectionResizeMode(2, QHeaderView.Interactive)  # IP - 可拖拽调整
        self._table.setColumnWidth(2, 140)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)  # 端口
        self._table.setColumnWidth(3, 70)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)  # 描述
        self._table.setColumnWidth(4, 160)
        hh.setSectionResizeMode(5, QHeaderView.Interactive)  # 集合
        self._table.setColumnWidth(5, 120)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)       # 状态
        self._table.setColumnWidth(6, 100)

        # 恢复保存的列宽
        settings = QSettings("PortCheck", "PortCheck")
        for col in [2, 3, 4, 5]:
            saved = settings.value(f"target_col_{col}")
            if saved is not None:
                self._table.setColumnWidth(col, int(saved))

        # 列宽变化时保存
        hh.sectionResized.connect(self._save_column_widths)

        # 点击表头排序
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._on_header_clicked)

        layout.addWidget(self._table)

        # ── 按钮栏 ───────────────────────────────────────────
        btn_layout = QHBoxLayout()

        self._add_btn = QPushButton("添加目标")
        self._add_btn.clicked.connect(self._add_target)
        btn_layout.addWidget(self._add_btn)

        self._edit_btn = QPushButton("编辑")
        self._edit_btn.clicked.connect(self._edit_target)
        btn_layout.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.clicked.connect(self._delete_targets)
        btn_layout.addWidget(self._delete_btn)

        self._test_selected_btn = QPushButton("▶ 测试选中")
        self._test_selected_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #2980b9; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self._test_selected_btn.clicked.connect(self._on_test_selected)
        btn_layout.addWidget(self._test_selected_btn)

        btn_layout.addStretch()

        self._import_btn = QPushButton("导入文件")
        self._import_btn.clicked.connect(self._import_file)
        btn_layout.addWidget(self._import_btn)

        self._export_btn = QPushButton("导出文件")
        self._export_btn.clicked.connect(self._export_file)
        btn_layout.addWidget(self._export_btn)

        layout.addLayout(btn_layout)

    # ── 公开接口 ───────────────────────────────────────────

    def set_batch(self, batch_id: int | None) -> None:
        """切换到指定集合。None=全部, 0=未分类。"""
        self._current_batch_id = batch_id
        self.refresh()

    def refresh(self) -> None:
        """刷新目标列表。"""
        self._all_targets = self._db.get_targets(self._current_batch_id)

        # 更新标题
        if self._current_batch_id is None:
            self._info_label.setText(f"全部目标 ({len(self._all_targets)})")
        elif self._current_batch_id == 0:
            self._info_label.setText(f"未分类目标 ({len(self._all_targets)})")
        else:
            batch = self._db.get_batch(self._current_batch_id)
            name = batch.name if batch else "未知"
            self._info_label.setText(f"{name} ({len(self._all_targets)})")

        self._apply_filter()

    def _apply_filter(self):
        """根据筛选文本 + 状态 + 排序刷新表格显示。"""
        filter_text = self._filter_edit.text().strip().lower()
        status_val = self._status_filter.currentData()

        targets = list(self._all_targets)

        # 文本筛选
        if filter_text:
            targets = [t for t in targets if
                       filter_text in t.ip.lower()
                       or filter_text in str(t.port)
                       or filter_text in t.description.lower()
                       or filter_text in t.batch_name.lower()]

        # 状态筛选
        if status_val is not None:
            filtered = []
            for t in targets:
                last = self._db.get_target_last_result(t.id)
                if status_val == "untested":
                    if last is None:
                        filtered.append(t)
                elif isinstance(status_val, bool):
                    if last is not None and last.success == status_val:
                        filtered.append(t)
            targets = filtered

        # 应用排序
        if self._sort_col >= 0:
            targets = self._sort_targets(targets)

        self._populate_table(targets)
        self._update_sort_indicator()

    def _sort_targets(self, targets):
        """按当前排序列排序目标列表（IP 按数字段排序，端口按数值排序）。"""
        def _ip_key(t):
            try:
                return tuple(int(o) for o in t.ip.split("."))
            except (ValueError, AttributeError):
                return (0, 0, 0, 0)

        key_map = {
            2: lambda t: _ip_key(t),              # IP - 按数字段自然排序
            3: lambda t: t.port,                  # 端口 - 按数值
            4: lambda t: t.description.lower(),   # 描述 - 按字母
            5: lambda t: t.batch_name.lower(),    # 集合 - 按字母
        }

        key_func = key_map.get(self._sort_col)
        if key_func:
            targets.sort(key=key_func, reverse=not self._sort_asc)
        return targets

    def _on_header_clicked(self, col: int):
        """点击表头切换排序。仅 IP(2)、端口(3)、描述(4)、集合(5) 可排序。"""
        if col not in (2, 3, 4, 5):
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc  # 切换升降序
        else:
            self._sort_col = col
            self._sort_asc = True  # 新列默认升序
        self._apply_filter()

    def _update_sort_indicator(self):
        """在列标题上显示排序箭头。"""
        headers = {2: "IP 地址", 3: "端口", 4: "描述", 5: "集合"}
        for c, label in headers.items():
            if c == self._sort_col:
                arrow = " ▲" if self._sort_asc else " ▼"
            else:
                arrow = ""
            self._table.horizontalHeaderItem(c).setText(label + arrow)

    def _populate_table(self, targets):
        """填充表格数据。"""
        self._table.setRowCount(len(targets))
        for row, t in enumerate(targets):
            # 序号 (col 0)
            num_item = QTableWidgetItem(str(row + 1))
            num_item.setTextAlignment(Qt.AlignCenter)
            num_item.setFlags(Qt.ItemIsEnabled)  # 不可编辑不可拖拽
            self._table.setItem(row, 0, num_item)

            # 复选框 (col 1)
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            cb.setCheckState(Qt.Unchecked)
            self._table.setItem(row, 1, cb)

            # IP (col 2)
            ip_item = QTableWidgetItem(t.ip)
            ip_item.setData(Qt.UserRole, t.id)  # 存储 target_id
            self._table.setItem(row, 2, ip_item)

            # Port (col 3)
            port_item = QTableWidgetItem(str(t.port))
            port_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 3, port_item)

            # 描述 (col 4)
            self._table.setItem(row, 4, QTableWidgetItem(t.description))

            # 集合 (col 5)
            self._table.setItem(row, 5, QTableWidgetItem(t.batch_name))

            # 最近状态 (col 6)
            last = self._db.get_target_last_result(t.id)
            if last:
                status_text = "✓ 连通" if last.success else "✗ 未连通"
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(
                    QBrush(QColor("#27ae60") if last.success else QColor("#e74c3c"))
                )
            else:
                status_item = QTableWidgetItem("-")
                status_item.setForeground(QBrush(QColor("#999")))
            self._table.setItem(row, 6, status_item)

        self._select_all_cb.setChecked(False)

    def get_selected_target_ids(self) -> list[int]:
        """获取当前勾选/选中的目标 ID 列表。"""
        ids = []
        for row in range(self._table.rowCount()):
            cb = self._table.item(row, 1)  # 复选框在 col 1
            if cb and cb.checkState() == Qt.Checked:
                item = self._table.item(row, 2)  # ID 在 col 2
                if item:
                    ids.append(item.data(Qt.UserRole))
        if not ids:
            for row in set(idx.row() for idx in self._table.selectedIndexes()):
                item = self._table.item(row, 2)
                if item:
                    ids.append(item.data(Qt.UserRole))
        return ids

    # ── 槽函数 ─────────────────────────────────────────────

    def _save_column_widths(self):
        """保存用户调整后的列宽到 QSettings。"""
        settings = QSettings("PortCheck", "PortCheck")
        for col in [2, 3, 4, 5]:  # IP, 端口, 描述, 集合
            settings.setValue(f"target_col_{col}", self._table.columnWidth(col))

    def _on_select_all(self, state):
        check_state = Qt.Checked if state else Qt.Unchecked
        for row in range(self._table.rowCount()):
            cb = self._table.item(row, 1)  # 复选框在 col 1
            if cb:
                cb.setCheckState(check_state)

    def _schedule_drag_rebuild(self, *args):
        """拖拽操作后延迟重建（debounce 50ms，合并多次信号）。"""
        self._drag_rebuild_timer.start(50)

    def _rebuild_after_drag(self):
        """拖拽完成：读取当前顺序 → 保存 → 完整重建表格。"""
        ordered_ids = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 2)  # ID 在 col 2
            if item:
                tid = item.data(Qt.UserRole)
                if tid:
                    ordered_ids.append(tid)
        if ordered_ids:
            self._db.update_targets_sort_order(ordered_ids)
        # 从数据库重新加载，修复拖出列表外导致的空白行/数据丢失
        self._apply_filter()

    def _on_cell_clicked(self, row: int, col: int):
        """点击行任意位置切换复选框状态（复选框列本身 Qt 已自动处理）。"""
        if col == 1:
            return  # 复选框列 Qt 自动切换
        cb = self._table.item(row, 1)
        if cb and (cb.flags() & Qt.ItemIsUserCheckable):
            new_state = Qt.Unchecked if cb.checkState() == Qt.Checked else Qt.Checked
            cb.setCheckState(new_state)

    def _on_double_click(self, index):
        row = index.row()
        item = self._table.item(row, 2)  # ID 在 col 2
        if item:
            self._edit_target_by_id(item.data(Qt.UserRole))

    def _add_target(self):
        dlg = TargetDialog(self._db, parent=self)
        if dlg.exec() == QDialog.Accepted:
            targets = dlg.target_list
            if targets:
                for t in targets:
                    self._db.add_target(
                        t["ip"], t["port"], t["description"], t["batch_id"]
                    )
                self.refresh()
                self.targets_changed.emit
                QMessageBox.information(
                    self, "添加完成",
                    f"成功添加 {len(targets)} 个目标。"
                )
            elif dlg.target_data:
                r = dlg.target_data
                self._db.add_target(r["ip"], r["port"], r["description"], r["batch_id"])
                self.refresh()
                self.targets_changed.emit()

    def _edit_target(self):
        ids = self.get_selected_target_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要编辑的目标。")
            return
        self._edit_target_by_id(ids[0])

    def _edit_target_by_id(self, target_id: int):
        dlg = TargetDialog(self._db, target_id, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.target_data:
            r = dlg.target_data
            self._db.update_target(
                target_id, r["ip"], r["port"], r["description"], r["batch_id"]
            )
            self.refresh()
            self.targets_changed.emit()

    def _delete_targets(self):
        ids = self.get_selected_target_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选或选中要删除的目标。")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 个目标吗？\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._db.delete_targets(ids)
            self.refresh()
            self.targets_changed.emit()

    def _on_test_selected(self):
        """将勾选的目标 ID 发送出去用于测试。"""
        ids = self.get_selected_target_ids()
        if not ids:
            # 没有勾选 → 测试当前筛选结果的全部
            ids = []
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 2)  # ID 在 col 2
                if item:
                    ids.append(item.data(Qt.UserRole))
        if not ids:
            QMessageBox.information(self, "提示", "当前没有可测试的目标。")
            return
        self.test_selected.emit(ids)

    def _import_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入目标", "",
            "表格文件 (*.csv *.xlsx *.xls);;CSV 文件 (*.csv);;Excel 文件 (*.xlsx *.xls);;所有文件 (*)"
        )
        if not filepath:
            return

        ext = Path(filepath).suffix.lower()
        if ext in (".xlsx", ".xls"):
            raw_targets, errors = parse_targets_excel(filepath)
        else:
            raw_targets, errors = parse_targets_csv(filepath)

        # 统一转为 dict（CSV 返回 CsvTarget dataclass，Excel 返回 dict）
        targets: list[dict] = []
        for t in raw_targets:
            if isinstance(t, dict):
                targets.append(t)
            else:
                targets.append({
                    "ip": t.ip, "port": t.port,
                    "description": t.description,
                    "batch_name": t.batch_name,
                })

        if not targets:
            QMessageBox.warning(
                self, "导入失败",
                f"没有解析到有效数据。\n\n错误:\n" + "\n".join(errors[:20])
            )
            return

        # 检测重复
        dup_count = 0
        for t in targets:
            batch_id = self._resolve_batch(t.get("batch_name", ""))
            if self._db.target_exists(t["ip"], t["port"], batch_id):
                dup_count += 1

        preview_lines = []
        for t in targets[:10]:
            preview_lines.append(f"  {t['ip']}:{t['port']}  {t.get('description', '')}  [{t.get('batch_name', '')}]")
        if len(targets) > 10:
            preview_lines.append(f"  ... 等共 {len(targets)} 条")
        preview = "\n".join(preview_lines)

        msg = f"将导入 {len(targets)} 条目标:\n\n{preview}"
        if dup_count > 0:
            msg += f"\n\n⚠ 检测到 {dup_count} 条重复。"
        if errors:
            msg += f"\n⚠ 格式错误 {len(errors)} 条。"

        # 有重复时提供覆盖选项
        if dup_count > 0:
            msg += "\n\n如何处理重复数据？"
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("导入确认")
            msg_box.setText(msg)
            msg_box.setIcon(QMessageBox.Question)
            skip_btn = msg_box.addButton("跳过重复 (保留已有)", QMessageBox.NoRole)
            overwrite_btn = msg_box.addButton("覆盖已有数据", QMessageBox.YesRole)
            cancel_btn = msg_box.addButton("取消", QMessageBox.RejectRole)
            msg_box.setDefaultButton(skip_btn)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == cancel_btn:
                return
            overwrite = (clicked == overwrite_btn)
        else:
            reply = QMessageBox.question(
                self, "确认导入", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return
            overwrite = False

        import_count = 0
        skip_count = 0
        update_count = 0
        for t in targets:
            batch_id = self._resolve_batch(t.get("batch_name", ""))
            existing_id = self._db.find_target_id(t["ip"], t["port"], batch_id)

            if existing_id is not None:
                if overwrite:
                    self._db.update_target(
                        existing_id, t["ip"], t["port"],
                        t.get("description", ""), batch_id
                    )
                    update_count += 1
                else:
                    skip_count += 1
            else:
                self._db.add_target(t["ip"], t["port"], t.get("description", ""), batch_id)
                import_count += 1

        self.refresh()
        self.targets_changed.emit()

        parts = [f"新增 {import_count} 条"]
        if update_count > 0:
            parts.append(f"覆盖 {update_count} 条")
        if skip_count > 0:
            parts.append(f"跳过 {skip_count} 条重复")
        if errors:
            parts.append(f"{len(errors)} 条格式错误")
        QMessageBox.information(self, "导入完成", "，".join(parts))

    def _resolve_batch(self, batch_name: str) -> int | None:
        """根据集合名称获取 batch_id，不存在则创建。"""
        if not batch_name:
            return None
        for b in self._db.get_all_batches():
            if b.name == batch_name:
                return b.id
        return self._db.add_batch(batch_name, "")

    def _export_file(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", "targets.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not filepath:
            return

        targets = self._db.get_targets(self._current_batch_id)
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
