"""目标管理面板 —— 管理 IP:Port 目标条目的增删改查和 CSV 导入导出。
支持 IP 范围（CIDR/范围）、端口范围展开、筛选和勾选测试。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.csv_handler import export_targets_to_csv, parse_targets_csv
from src.database import Database, Target
from src.ui.clipboard import KIND_CONN_TARGET, copy_items, paste_items
from src.ui.table_utils import (
    TargetDragTable,
    enable_stretch_fill,
    refresh_tooltips,
    unique_copy_name,
)
from src.excel_handler import (
    export_targets_to_excel,
    parse_targets_excel,
)
from src.scanner import expand_ip_range, expand_port_range


class TargetDialog(QDialog):
    """添加目标的对话框 —— 支持 IP 范围和端口范围。"""

    def __init__(self, db: Database, target_id: int | None = None,
                 collection_id: int | None = None, parent=None):
        super().__init__(parent)
        self._db = db
        self._target_id = target_id
        self._default_collection_id = collection_id
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
        self._ip_edit.setMinimumWidth(350)
        layout.addRow("IP 地址:", self._ip_edit)

        # 端口（支持范围和逗号分隔）
        self._port_edit = QLineEdit()
        self._port_edit.setPlaceholderText(
            "单个: 80  范围: 1-100  多个: 80,443,8080  混合: 80,443,8000-8010"
        )
        self._port_edit.setMinimumWidth(350)
        layout.addRow("端口:", self._port_edit)

        # 描述（{ip} 和 {port} 会自动替换）
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("{ip}:{port} 服务 (自动替换 IP/端口)")
        self._desc_edit.setMinimumWidth(350)
        layout.addRow("描述:", self._desc_edit)

        # 集合选择（"无集合"由"未分类"承担，不单列选项）
        self._batch_combo = QComboBox()
        self._uncat_collection_id = None
        for b in self._db.get_all_collections():
            if b.name == "未分类":
                self._uncat_collection_id = b.id
            self._batch_combo.addItem(f"{b.name} ({b.target_count})", b.id)
        # 预选默认集合：无集合（None/0）一律归入"未分类"
        default_cid = self._default_collection_id
        if default_cid in (None, 0):
            default_cid = self._uncat_collection_id
        idx = self._batch_combo.findData(default_cid)
        if idx >= 0:
            self._batch_combo.setCurrentIndex(idx)
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
            # 无集合目标（collection_id 为空）展示为"未分类"
            cid = t.collection_id
            if cid in (None, 0):
                cid = self._uncat_collection_id
            idx = self._batch_combo.findData(cid)
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
        collection_id = self._batch_combo.currentData()
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
                    "ip": ip, "port": port, "description": desc, "collection_id": collection_id
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


class ImportWorker(QThread):
    """后台线程执行导入，避免大数量时卡 UI。"""

    progress = Signal(int, int)   # current, total
    finished = Signal(int, int, int)  # import_count, skip_count, update_count

    def __init__(self, db_path: str, targets: list[dict],
                 overwrite: bool, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._targets = targets
        self._overwrite = overwrite

    def run(self):
        from src.database import Database
        db = Database(self._db_path)

        # 预解析集合名称 → ID
        collection_cache = {}
        for b in db.get_all_collections():
            collection_cache[b.name] = b.id

        import_count = skip_count = update_count = 0
        total = len(self._targets)

        for i, t in enumerate(self._targets):
            collection_name = t.get("collection_name", "")
            collection_id = None
            if collection_name:
                if collection_name in collection_cache:
                    collection_id = collection_cache[collection_name]
                else:
                    collection_id = db.add_collection(collection_name)
                    collection_cache[collection_name] = collection_id

            existing_id = db.find_target_id(t["ip"], t["port"], collection_id)
            if existing_id is not None:
                if self._overwrite:
                    db.update_target(existing_id, t["ip"], t["port"],
                                     t.get("description", ""), collection_id)
                    update_count += 1
                else:
                    skip_count += 1
            else:
                db.add_target(t["ip"], t["port"], t.get("description", ""), collection_id)
                import_count += 1

            if i % 20 == 0 or i == total - 1:
                self.progress.emit(i + 1, total)

        self.finished.emit(import_count, skip_count, update_count)


class TargetPanel(QWidget):
    """目标管理面板。

    Signals:
        targets_changed:    目标数据发生变更时触发。
        selection_changed:  表格选中变化时触发，携带选中的目标 ID 列表。
        connectivity_test:  请求对指定目标进行连通测试（双击行触发），携带 ID 列表。
        protocol_test_selected: 通知协议测试面板，携带目标 IP/端口。
    """

    targets_changed = Signal()
    selection_changed = Signal(list)      # list[target_id]
    connectivity_test = Signal(list)      # list[target_id]
    protocol_test_selected = Signal(str, int)  # ip, port

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._current_collection_id: int | None = None  # None=全部, 0=未分类
        self._all_targets: list = []  # 缓存当前全部目标用于筛选
        self._temporary_mode = False  # 临时列表（协议测试转来，不入库）
        self._temporary_targets: list = []
        self._temp_last_results: dict[int, bool] = {}  # 临时目标 fake_id → 最近是否连通
        self._temp_real_ids: dict[int, int] = {}  # 临时目标 fake_id → 真实 connect_target id
        self._sort_col: int = -1  # 当前排序列（-1 为按 sort_order）
        self._sort_asc: bool = True
        # 筛选防抖（仅用于文本框输入 + 状态下拉）
        self._filter_dirty = False
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._do_apply_filter)
        # 防止 populate 期间拖拽信号触发筛选循环
        self._populating = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 第一行: 标题 + 筛选 + 全选/反选/刷新 ──────────────
        top_layout = QHBoxLayout()
        self._info_label = QLabel("全部目标")
        top_layout.addWidget(self._info_label)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("筛选 IP/端口/描述...")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setMinimumWidth(120)
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
        # 全选/反选放在筛选行行尾，随后是刷新按钮（紧凑样式，避免挤压主分栏）
        btn_style = "QPushButton { padding: 2px 6px; }"
        sel_all_btn = QPushButton("全选")
        sel_all_btn.setStyleSheet(btn_style)
        sel_all_btn.clicked.connect(self._select_all_rows)
        top_layout.addWidget(sel_all_btn)
        invert_btn = QPushButton("反选")
        invert_btn.setStyleSheet(btn_style)
        invert_btn.clicked.connect(self._invert_selection)
        top_layout.addWidget(invert_btn)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setStyleSheet(btn_style)
        self._refresh_btn.clicked.connect(self.refresh)
        top_layout.addWidget(self._refresh_btn)
        layout.addLayout(top_layout)

        # ── 表格 ─────────────────────────────────────────────
        # 支持拖拽目标到左侧集合树归集
        self._table = TargetDragTable()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            "IP 地址", "端口", "描述", "最近状态"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._emit_selection_changed)
        # 编辑/删除等操作集成到表格右键菜单
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)

        hh = self._table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._on_header_clicked)
        # 列填满可用宽度：IP/描述 吸收剩余空间，端口/最近状态 可拖动调整
        enable_stretch_fill(self._table)

        layout.addWidget(self._table)

        action_layout = QHBoxLayout()

        self._add_btn = QPushButton("添加")
        self._add_btn.clicked.connect(self._add_target)
        action_layout.addWidget(self._add_btn)

        self._edit_btn = QPushButton("编辑")
        self._edit_btn.clicked.connect(self._edit_target)
        action_layout.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.clicked.connect(self._delete_targets)
        action_layout.addWidget(self._delete_btn)

        self._copy_btn = QPushButton("复制")
        self._copy_btn.clicked.connect(self._copy_targets)
        action_layout.addWidget(self._copy_btn)

        self._proto_test_btn = QPushButton("协议测试")
        self._proto_test_btn.setStyleSheet(
            "QPushButton { color: #fff; background-color: #8e44ad; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self._proto_test_btn.clicked.connect(self._on_protocol_test_selected)
        action_layout.addWidget(self._proto_test_btn)

        # 临时列表：显示"保存"按钮，把临时目标存入连通测试集合
        self._save_temp_btn = QPushButton("保存")
        self._save_temp_btn.setVisible(False)
        self._save_temp_btn.clicked.connect(self._save_temporary_to_collection)
        action_layout.addWidget(self._save_temp_btn)

        action_layout.addStretch()

        layout.addLayout(action_layout)

    # ── 公开接口 ───────────────────────────────────────────

    def set_collection(self, collection_id: int | None) -> None:
        """切换到指定集合。None=全部, 0=未分类。"""
        self._current_collection_id = collection_id
        self._temporary_mode = False
        self.refresh()

    def refresh(self) -> None:
        """刷新目标列表（集合切换/增删改时调用，立即执行不防抖）。"""
        # 临时列表：显示内存中的临时目标，不查库
        if self._temporary_mode:
            self._all_targets = self._temporary_targets
            self._info_label.setText(f"临时列表 ({len(self._all_targets)})")
            self._save_temp_btn.setVisible(True)
            self._filter_timer.stop()
            self._filter_dirty = False
            self._do_apply_filter()
            return

        self._all_targets = self._db.get_targets(self._current_collection_id)
        self._save_temp_btn.setVisible(False)

        # 更新标题
        if self._current_collection_id is None:
            self._info_label.setText(f"全部目标 ({len(self._all_targets)})")
        elif self._current_collection_id == 0:
            self._info_label.setText(f"未分类目标 ({len(self._all_targets)})")
        else:
            collection = self._db.get_collection(self._current_collection_id)
            name = collection.name if collection else "未知"
            self._info_label.setText(f"{name} ({len(self._all_targets)})")

        # 取消防抖定时器，立即执行
        self._filter_timer.stop()
        self._filter_dirty = False
        self._do_apply_filter()

    def _apply_filter(self):
        """由筛选框/状态下拉变化触发，防抖执行（避免打字时每键都查 DB）。"""
        if not self._filter_dirty:
            self._filter_dirty = True
            self._filter_timer.start(150)

    def _do_apply_filter(self):
        """实际执行筛选 + 表格填充。"""
        self._filter_dirty = False

        filter_text = self._filter_edit.text().strip().lower()
        status_val = self._status_filter.currentData()

        targets = list(self._all_targets)

        # 文本筛选
        if filter_text:
            targets = [t for t in targets if
                       filter_text in t.ip.lower()
                       or filter_text in str(t.port)
                       or filter_text in t.description.lower()
                       or filter_text in t.collection_name.lower()]

        # 批量获取最近测试结果（一次查询替代逐条查询）
        if self._temporary_mode:
            # 结果按真实 target_id 记录，这里转成以 fake_id 为键的查找表
            last_results = {}
            for t in targets:
                key = self._temp_real_ids.get(t.id, t.id)
                last_results[t.id] = self._temp_last_results.get(key)
        else:
            all_ids = [t.id for t in targets]
            last_results = self._db.get_targets_last_results(all_ids) if all_ids else {}

        # 状态筛选
        if status_val is not None:
            filtered = []
            for t in targets:
                last_ok = last_results.get(t.id)
                if status_val == "untested":
                    if last_ok is None:
                        filtered.append(t)
                elif isinstance(status_val, bool):
                    if last_ok is not None and last_ok == status_val:
                        filtered.append(t)
            targets = filtered

        # 应用排序
        if self._sort_col >= 0:
            targets = self._sort_targets(targets)

        self._update_sort_indicator()
        self._populate_table(targets, last_results)

        # 如果填充期间又来了新请求，再次执行
        if self._filter_dirty:
            self._do_apply_filter()

    def _sort_targets(self, targets):
        """按当前排序列排序目标列表（IP 按数字段排序，端口按数值排序）。"""
        def _ip_key(t):
            try:
                return tuple(int(o) for o in t.ip.split("."))
            except (ValueError, AttributeError):
                return (0, 0, 0, 0)

        key_map = {
            0: lambda t: _ip_key(t),
            1: lambda t: t.port,
            2: lambda t: t.description.lower(),
        }

        key_func = key_map.get(self._sort_col)
        if key_func:
            targets.sort(key=key_func, reverse=not self._sort_asc)
        return targets

    def _on_header_clicked(self, col: int):
        """点击表头切换排序。IP(1)、端口(2)、描述(3)、集合(4) 可排序。"""
        if col not in (0, 1, 2):
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._apply_filter()

    def _update_sort_indicator(self):
        """在列标题上显示排序箭头。"""
        headers = {0: "IP 地址", 1: "端口", 2: "描述"}
        for c, label in headers.items():
            if c == self._sort_col:
                arrow = " ▲" if self._sort_asc else " ▼"
            else:
                arrow = ""
            self._table.horizontalHeaderItem(c).setText(label + arrow)

    def _populate_table(self, targets, last_results: dict | None = None):
        """填充表格数据。"""
        self._populating = True
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(targets))

        for row, t in enumerate(targets):
            ip_item = QTableWidgetItem(t.ip)
            ip_item.setData(Qt.UserRole, t.id)
            self._table.setItem(row, 0, ip_item)

            port_item = QTableWidgetItem(str(t.port))
            port_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 1, port_item)

            self._table.setItem(row, 2, QTableWidgetItem(t.description))

            if last_results is not None:
                last_ok = last_results.get(t.id)
            else:
                last = self._db.get_target_last_result(t.id)
                last_ok = last.success if last else None

            if last_ok is not None:
                status_text = "✓ 连通" if last_ok else "✗ 未连通"
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(
                    QBrush(QColor("#27ae60") if last_ok else QColor("#e74c3c"))
                )
            else:
                status_item = QTableWidgetItem("-")
                status_item.setForeground(QBrush(QColor("#999")))
            self._table.setItem(row, 3, status_item)

        self._table.setUpdatesEnabled(True)
        refresh_tooltips(self._table)
        self._populating = False

    def get_visible_target_ids(self) -> list[int]:
        """获取当前表格中可见（未被筛选掉）的目标 ID 列表。"""
        ids = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                tid = item.data(Qt.UserRole)
                if tid:
                    ids.append(tid)
        return ids

    def get_selected_target_ids(self) -> list[int]:
        """获取当前选中的目标 ID 列表。"""
        ids = []
        for row in set(idx.row() for idx in self._table.selectedIndexes()):
            item = self._table.item(row, 0)  # ID 在 col 1
            if item:
                ids.append(item.data(Qt.UserRole))
        return ids

    # ── 临时列表（协议测试转来，不入库）─────────────────────

    def is_temporary(self) -> bool:
        return self._temporary_mode

    def load_temporary_targets(self, targets: list[dict]) -> None:
        """加载临时目标列表（ip/port/description），不写入数据库。"""
        self._temporary_mode = True
        self._temporary_targets = []
        self._temp_last_results = {}
        self._temp_real_ids = {}
        for i, t in enumerate(targets):
            self._temporary_targets.append(Target(
                id=-(i + 1), ip=str(t["ip"]), port=int(t["port"]),
                description=t.get("description", ""),
                collection_id=None, collection_name="", created_at="",
            ))
        self.refresh()

    def get_targets_by_ids(self, target_ids: list[int]) -> list:
        """按 id 获取目标对象；临时模式返回内存对象，否则查库。"""
        if self._temporary_mode:
            by_id = {t.id: t for t in self._temporary_targets}
            return [by_id[tid] for tid in target_ids if tid in by_id]
        return [t for t in (self._db.get_target(tid) for tid in target_ids) if t]

    def set_temporary_results(self, results: dict) -> None:
        """记录临时目标最近的连通测试结果（真实 target_id → success）。"""
        self._temp_last_results = dict(results)

    def _ensure_uncat_collection(self) -> int:
        """查找或创建连通测试「未分类」集合，返回其 id。"""
        for c in self._db.get_all_collections():
            if c.name == "未分类":
                return c.id
        return self._db.add_collection("未分类")

    def persist_temporary_targets(self) -> None:
        """把临时目标持久化为「未分类」下的真实目标（测试前调用，保证结果可写库）。"""
        if not self._temporary_mode:
            return
        uncat_id = self._ensure_uncat_collection()
        for t in self._temporary_targets:
            if t.id in self._temp_real_ids:
                continue  # 已持久化
            cid = self._db.find_target_id(t.ip, t.port, uncat_id)
            if cid is None:
                cid = self._db.add_target(t.ip, t.port, t.description, uncat_id)
            self._temp_real_ids[t.id] = cid
        # 持久化后切换为非临时模式，后续按真实 DB 目标操作
        self._temporary_mode = False
        self._temporary_targets = []
        self._current_collection_id = uncat_id
        self.refresh()

    def get_real_id(self, fake_id: int) -> int:
        """临时目标的真实 connect_target id；未持久化时返回原值。"""
        return self._temp_real_ids.get(fake_id, fake_id)

    def _save_temporary_to_collection(self):
        """把临时目标直接保存到用户选择的连通测试集合。"""
        if not self._temporary_targets:
            return
        collections = self._db.get_all_collections()
        if not collections:
            QMessageBox.information(self, "提示", "请先创建连通测试集合。")
            return
        names = [c.name for c in collections]
        name, ok = QInputDialog.getItem(self, "保存到集合", "选择目标集合:", names, 0, False)
        if not ok:
            return
        coll = collections[names.index(name)]
        count = 0
        for t in self._temporary_targets:
            if not self._db.target_exists(t.ip, t.port, coll.id):
                self._db.add_target(t.ip, t.port, t.description, coll.id)
                count += 1
        # 退出临时模式，切换到目标集合
        self._temporary_mode = False
        self._temporary_targets = []
        self.set_collection(coll.id)
        QMessageBox.information(self, "保存完成", f"已保存 {count} 个目标到 [{coll.name}]")
        self.targets_changed.emit()
        self.targets_changed.emit()

    def _emit_selection_changed(self):
        """表格选中变化时通知外部（用于控制栏显示选中数量）。"""
        self.selection_changed.emit(self.get_selected_target_ids())

    # ── 槽函数 ─────────────────────────────────────────────

    def _save_column_widths(self):
        """保存用户调整后的列宽到 QSettings。"""
        settings = QSettings("TestTool", "TestTool")
        for col in [1, 2, 3, 4]:  # IP, 端口, 描述, 集合
            settings.setValue(f"target_col_{col}", self._table.columnWidth(col))

    def _select_all_rows(self):
        self._table.selectAll()

    def _invert_selection(self):
        model = self._table.model()
        rows = self._table.rowCount()
        if rows == 0:
            return
        sm = self._table.selectionModel()
        sel_rows = set()
        for r in range(rows):
            if sm.isSelected(model.index(r, 0)):
                sel_rows.add(r)
        if not sel_rows:
            self._table.selectAll()
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        new_sel = QItemSelection()
        for r in range(rows):
            if r not in sel_rows:
                new_sel.select(model.index(r, 0), model.index(r, self._table.columnCount() - 1))
        sm.select(new_sel, QItemSelectionModel.ClearAndSelect)
        self._table.setFocus()

    def _schedule_drag_rebuild(self, *args):
        """拖拽操作后延迟重建（debounce 50ms，合并多次信号）。
        populate 期间忽略，避免 setRowCount 触发的 rowsRemoved 导致循环。
        """
        if not self._populating:
            self._drag_rebuild_timer.start(50)

    def _rebuild_after_drag(self):
        """拖拽完成：读取当前顺序 → 保存 → 完整重建表格。"""
        ordered_ids = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)  # ID 在 col 1
            if item:
                tid = item.data(Qt.UserRole)
                if tid:
                    ordered_ids.append(tid)
        if ordered_ids:
            self._db.update_targets_sort_order(ordered_ids)
            # 仅当有实际数据时才刷新，防止空表触发筛选循环
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
        """双击目标行 → 测试该条目标的连通性。"""
        row = index.row()
        item = self._table.item(row, 0)  # ID 在 col 0
        if item:
            tid = item.data(Qt.UserRole)
            if tid:
                self.connectivity_test.emit([tid])

    def _on_table_menu(self, pos):
        """目标列表右键菜单：测试连通性 / 协议测试 / 编辑 / 删除 / 添加目标。"""
        item = self._table.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("添加", self._add_target)
        if item:
            row = item.row()
            model = self._table.model()
            if not self._table.selectionModel().isSelected(model.index(row, 0)):
                self._table.selectRow(row)
            menu.addAction("测试连通性", lambda *_, r=row: self._on_double_click(model.index(r, 0)))
            menu.addAction("协议测试", self._on_protocol_test_selected)
            menu.addSeparator()
            menu.addAction("复制", self._copy_targets)
            menu.addAction("编辑", self._edit_target)
            menu.addAction("删除", self._delete_targets)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _add_target(self):
        # 临时列表下"添加"的目标默认归入未分类（临时列表本身不入库）
        target_cid = None if self._temporary_mode else self._current_collection_id
        dlg = TargetDialog(self._db, collection_id=target_cid, parent=self)
        if dlg.exec() == QDialog.Accepted:
            targets = dlg.target_list
            if targets:
                for t in targets:
                    self._db.add_target(
                        t["ip"], t["port"], t["description"], t["collection_id"]
                    )
                self.refresh()
                self.targets_changed.emit()
                QMessageBox.information(
                    self, "添加完成",
                    f"成功添加 {len(targets)} 个目标。"
                )
            elif dlg.target_data:
                r = dlg.target_data
                self._db.add_target(r["ip"], r["port"], r["description"], r["collection_id"])
                self.refresh()
                self.targets_changed.emit()

    def _edit_target(self):
        ids = self.get_selected_target_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要编辑的目标。")
            return
        if len(ids) == 1:
            self._edit_target_by_id(ids[0])
        else:
            # 多选：批量修改集合
            dlg = QDialog(self)
            dlg.setWindowTitle(f"批量修改集合 ({len(ids)} 个目标)")
            dlg.setMinimumWidth(300)
            dl = QFormLayout(dlg)
            combo = QComboBox()
            uncat_id = None
            for c in self._db.get_all_collections():
                if c.name == "未分类":
                    uncat_id = c.id
                combo.addItem(f"{c.name} ({c.target_count})", c.id)
            # 默认选中"未分类"，无集合即归入未分类
            idx = combo.findData(uncat_id) if uncat_id is not None else -1
            if idx >= 0:
                combo.setCurrentIndex(idx)
            dl.addRow("移动到集合:", combo)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            dl.addRow(bb)
            if dlg.exec() == QDialog.Accepted:
                cid = combo.currentData()
                self._db.move_targets_to_collection(ids, cid)
                self.refresh()
                self.targets_changed.emit()

    def _edit_target_by_id(self, target_id: int):
        dlg = TargetDialog(self._db, target_id, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.target_data:
            r = dlg.target_data
            self._db.update_target(
                target_id, r["ip"], r["port"], r["description"], r["collection_id"]
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

    def _resolve_collection_id(self) -> int | None:
        """把当前集合 id 解析为可用于写入的集合 id（未分类 0 → 真实未分类集合）。"""
        cid = self._current_collection_id
        if cid == 0:
            return self._ensure_uncat_collection()
        return cid

    def _copy_targets(self):
        """复制选中的目标。连通测试目标无名称字段，描述自动追加"副本"区分。"""
        if self._temporary_mode:
            QMessageBox.information(self, "提示", "临时列表不支持复制。")
            return
        ids = self.get_selected_target_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选中要复制的目标。")
            return
        targets = self.get_targets_by_ids(ids)
        if not targets:
            return
        cid = self._resolve_collection_id()
        existing = {t.description or "" for t in self._db.get_targets(cid)}
        for t in targets:
            desc = t.description or ""
            if desc:
                new_desc = unique_copy_name(desc, existing)
            else:
                new_desc = "副本"
                while new_desc in existing:
                    new_desc += "副本"
            self._db.add_target(
                ip=t.ip, port=t.port, description=new_desc,
                collection_id=cid,
            )
            existing.add(new_desc)
        self.refresh()
        self.targets_changed.emit()

    def _copy_targets_to_clip(self):
        """Ctrl+C：把选中的目标复制到应用内剪贴板。"""
        if self._temporary_mode:
            return
        ids = self.get_selected_target_ids()
        if not ids:
            return
        targets = self.get_targets_by_ids(ids)
        payload = [{"ip": t.ip, "port": t.port, "description": t.description or ""}
                   for t in targets]
        if payload:
            copy_items(KIND_CONN_TARGET, payload)

    def _paste_targets_from_clip(self):
        """Ctrl+V：把剪贴板中的目标粘贴到当前集合，描述追加"副本"。"""
        if self._temporary_mode:
            QMessageBox.information(self, "提示", "临时列表不支持粘贴。")
            return
        payload = paste_items(KIND_CONN_TARGET)
        if not payload:
            QMessageBox.information(self, "提示", "剪贴板中没有可粘贴的目标。")
            return
        cid = self._resolve_collection_id()
        existing = {t.description or "" for t in self._db.get_targets(cid)}
        for p in payload:
            desc = (p.get("description") or "").strip()
            if desc:
                new_desc = unique_copy_name(desc, existing)
            else:
                new_desc = "副本"
                while new_desc in existing:
                    new_desc += "副本"
            self._db.add_target(
                ip=p["ip"], port=p["port"], description=new_desc,
                collection_id=cid,
            )
            existing.add(new_desc)
        self.refresh()
        self.targets_changed.emit()

    def _on_protocol_test_selected(self):
        """将第一个勾选目标的 IP/端口发送到协议测试面板。"""
        ids = self.get_selected_target_ids()
        if not ids:
            item = self._table.item(0, 0)
            if item:
                ids = [item.data(Qt.UserRole)]
        if not ids:
            QMessageBox.information(self, "提示", "当前没有可测试的目标。")
            return
        # 临时模式：从内存列表查找
        if self._temporary_mode:
            by_id = {t.id: t for t in self._temporary_targets}
            target = by_id.get(ids[0])
        else:
            target = self._db.get_target(ids[0])
        if target:
            self.protocol_test_selected.emit(target.ip, target.port)

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
                    "collection_name": t.collection_name,
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
            collection_id = self._resolve_batch(t.get("collection_name", ""))
            if self._db.target_exists(t["ip"], t["port"], collection_id):
                dup_count += 1

        preview_lines = []
        for t in targets[:10]:
            preview_lines.append(f"  {t['ip']}:{t['port']}  {t.get('description', '')}  [{t.get('collection_name', '')}]")
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

        # 数量过大时二次确认
        if len(targets) > 1000:
            reply = QMessageBox.question(
                self, "导入确认",
                f"即将导入 {len(targets)} 条目标，数量较大，确定继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # 异步导入 + 进度对话框
        progress_dlg = QProgressDialog("正在导入...", "取消", 0, len(targets), self)
        progress_dlg.setWindowTitle("导入进度")
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.setMinimumDuration(0)
        progress_dlg.setAutoClose(False)
        progress_dlg.show()

        self._import_worker = ImportWorker(
            self._db.db_path, targets, overwrite
        )
        self._import_worker.progress.connect(
            lambda c, t: progress_dlg.setValue(c)
        )
        self._import_worker.finished.connect(
            lambda new, skip, upd: self._on_import_done(progress_dlg, new, skip, upd, errors)
        )
        self._import_worker.start()

    def _on_import_done(self, dlg, import_count, skip_count, update_count, errors):
        """导入线程完成回调。"""
        dlg.close()
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

    def _resolve_batch(self, collection_name: str) -> int | None:
        """根据集合名称获取 collection_id，不存在则创建。"""
        if not collection_name:
            return None
        for b in self._db.get_all_collections():
            if b.name == collection_name:
                return b.id
        return self._db.add_collection(collection_name)

    def _export_file(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", "targets.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not filepath:
            return

        targets = self._db.get_targets(self._current_collection_id)
        data = [{
            "ip": t.ip, "port": t.port,
            "description": t.description,
            "collection_name": t.collection_name,
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self._copy_targets_to_clip()
        elif event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            self._paste_targets_from_clip()
        elif event.key() == Qt.Key_Delete or (event.key() == Qt.Key_D and event.modifiers() == Qt.ControlModifier):
            self._delete_targets()
        else:
            super().keyPressEvent(event)
