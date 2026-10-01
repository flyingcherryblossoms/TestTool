"""主窗口 —— 连通测试 / 协议测试两大标签页，菜单栏和状态栏。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src import __version__
from src.ui import shortcuts
from src.ui.connectivity_panel import ConnectivityPanel
from src.ui.csp_parser_dialog import CspParserDialog
from src.ui.port_scan_dialog import PortScanDialog
from src.ui.protocol_panel import ProtocolPanel
from src.ui.shortcut_settings_dialog import ShortcutSettingsDialog
from src.ui.scaling import (
    UIScaleManager, MIN_SCALE_PERCENT, MAX_SCALE_PERCENT, SCALE_STEP,
)
from src.ui.theme import THEMES, apply_theme
from src.ui.table_utils import fit_table_buttons


class CollectionDialog(QDialog):
    """新建/编辑集合的对话框。"""

    def __init__(self, title: str, name: str = "",
                 name_placeholder: str = "例如: 生产环境服务器", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QFormLayout(self)

        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText(name_placeholder)
        self._name_edit.setMinimumWidth(280)
        layout.addRow("集合名称:", self._name_edit)

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


class MainWindow(QMainWindow):
    """TestTool 主窗口。"""

    def __init__(self, db_path: str = ""):
        super().__init__()
        self._db = Database(db_path)
        app = QApplication.instance()
        self._scale_manager = UIScaleManager(app, self._db)
        self._ui_scale_percent = self._scale_manager.percent
        self._theme = apply_theme(app, self._db.get_setting("theme", "light"),
                                  self._ui_scale_percent)
        # 先加载快捷键绑定，面板创建时即读到已存配置
        shortcuts.load(self._db)
        self.setWindowTitle("测试工具")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        max_width = max(640, available.width() - 40) if available else 1300
        max_height = max(480, available.height() - 40) if available else 850
        self.setMinimumSize(min(1100, max_width), min(700, max_height))
        self.resize(min(1300, max_width), min(850, max_height))

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._update_statusbar()
        self._scale_manager.set_shortcut_handler(self._handle_scale_shortcut)
        self._scale_manager.apply_widget(self, resize_window=True)

    # ── 菜单栏 ─────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()

        tools_menu = menubar.addMenu("其他工具(&T)")
        port_scan_action = QAction("端口扫描...", self)
        port_scan_action.triggered.connect(self._open_port_scan)
        tools_menu.addAction(port_scan_action)
        csp_parse_action = QAction("CSP 报文解析...", self)
        csp_parse_action.triggered.connect(self._open_csp_parser)
        tools_menu.addAction(csp_parse_action)
        tools_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self.close)
        tools_menu.addAction(exit_action)

        settings_menu = menubar.addMenu("设置(&S)")
        shortcut_action = QAction("快捷键...", self)
        shortcut_action.triggered.connect(self._open_shortcut_settings)
        settings_menu.addAction(shortcut_action)
        theme_menu = settings_menu.addMenu("主题")
        self._theme_actions = QActionGroup(self)
        for name, title in (
            ("light", "浅色（增强区分）"), ("light_original", "经典浅色"),
            ("dark", "暗色"),
            ("high_contrast", "高对比度"),
        ):
            action = QAction(title, self)
            action.setCheckable(True)
            action.setData(name)
            action.setChecked(name == self._theme)
            self._theme_actions.addAction(action)
            theme_menu.addAction(action)
        self._theme_actions.triggered.connect(self._change_theme)

        settings_menu.addSeparator()
        self._scale_action = QAction(f"界面缩放（{self._ui_scale_percent}%）...", self)
        self._scale_action.triggered.connect(self._change_ui_scale)
        settings_menu.addAction(self._scale_action)
        for attr, title, action_id, slot in (
            ("_zoom_in_action", "放大界面", "zoom_in", lambda: self._step_ui_scale(1)),
            ("_zoom_out_action", "缩小界面", "zoom_out", lambda: self._step_ui_scale(-1)),
            ("_zoom_reset_action", "恢复 100%", "zoom_reset", lambda: self._apply_ui_scale(100)),
        ):
            action = QAction(title, self)
            action.setShortcutContext(Qt.WidgetShortcut)
            action.triggered.connect(slot)
            settings_menu.addAction(action)
            setattr(self, attr, action)
        self._update_scale_shortcuts()

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)


    def _change_theme(self, action: QAction):
        name = action.data()
        if name in THEMES:
            self._theme = apply_theme(QApplication.instance(), name, self._ui_scale_percent)
            self._db.set_setting("theme", name)
            self._refresh_button_rows()

    def _refresh_button_rows(self):
        for window in QApplication.topLevelWidgets():
            for table in window.findChildren(QTableWidget):
                fit_table_buttons(table, self._ui_scale_percent)

    def _update_scale_shortcuts(self):
        for action, action_id in (
            (self._zoom_in_action, "zoom_in"),
            (self._zoom_out_action, "zoom_out"),
            (self._zoom_reset_action, "zoom_reset"),
        ):
            action.setShortcuts([QKeySequence(key) for key in shortcuts.current(action_id)])

    def _step_ui_scale(self, direction: int):
        current = self._ui_scale_percent
        if direction > 0:
            percent = (current // SCALE_STEP + 1) * SCALE_STEP
        else:
            percent = ((current - 1) // SCALE_STEP) * SCALE_STEP
        self._apply_ui_scale(percent)

    def _handle_scale_shortcut(self, action_id: str):
        if action_id == "zoom_in":
            self._step_ui_scale(1)
        elif action_id == "zoom_out":
            self._step_ui_scale(-1)
        else:
            self._apply_ui_scale(100)

    def _apply_ui_scale(self, percent: int):
        percent = max(MIN_SCALE_PERCENT, min(MAX_SCALE_PERCENT, percent))
        if percent == self._ui_scale_percent:
            return
        self._ui_scale_percent = percent
        self._db.set_setting("ui_scale_percent", str(percent))
        self._scale_manager.set_percent(percent)
        apply_theme(QApplication.instance(), self._theme, percent)
        self._refresh_button_rows()
        self._scale_action.setText(f"界面缩放（{percent}%）...")

    def _change_ui_scale(self):
        percent, accepted = QInputDialog.getInt(
            self, "界面缩放", "缩放比例（%，立即生效）:",
            self._ui_scale_percent, MIN_SCALE_PERCENT, MAX_SCALE_PERCENT, 5
        )
        if accepted:
            self._apply_ui_scale(percent)

    def _open_shortcut_settings(self):
        """打开快捷键设置对话框，保存后热更新全部快捷键。"""
        dlg = ShortcutSettingsDialog(self._db, self)
        self._scale_manager.shortcuts_enabled = False
        try:
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            self._scale_manager.shortcuts_enabled = True
        if accepted:
            shortcuts.save(self._db, dlg.shortcuts)
            shortcuts.set_active(dlg.shortcuts)
            shortcuts.apply_shortcuts()
            self._update_scale_shortcuts()

    # ── 主布局 ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_main_tab_changed)

        # Tab 0: 协议测试
        self._proto_panel = ProtocolPanel(self._db)
        self._proto_panel.test_finished.connect(self._update_statusbar)
        # 协议测试选中目标 → 连通测试临时列表
        self._proto_panel.connectivity_test_requested.connect(
            self._on_connectivity_test_requested
        )
        self._tabs.addTab(self._proto_panel, "协议测试")

        # Tab 1: 连通测试
        self._conn_panel = ConnectivityPanel(self._db)
        self._conn_panel.targets_changed.connect(self._update_statusbar)
        self._conn_panel.protocol_test_selected.connect(
            self._on_protocol_test_selected
        )
        self._tabs.addTab(self._conn_panel, "连通测试")

        layout.addWidget(self._tabs)

        # 恢复上次打开的标签页
        last_tab = self._db.get_setting("last_main_tab", "0")
        try:
            idx = int(last_tab)
            if 0 <= idx < self._tabs.count():
                self._tabs.setCurrentIndex(idx)
        except ValueError:
            pass

    # ── 状态栏 ─────────────────────────────────────────────

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_target_count = QLabel()
        self._status_last_test = QLabel()
        self._statusbar.addWidget(self._status_target_count)
        self._statusbar.addPermanentWidget(self._status_last_test)

    def _update_statusbar(self):
        total = self._db.get_total_target_count()
        conn_cols = len(self._db.get_all_collections())
        proto_cols = len([c for c in self._db.get_all_protocol_collections() if c.name != "未分类"])
        batch_count = conn_cols + proto_cols
        self._status_target_count.setText(
            f"共 {total} 个目标 / {batch_count} 个集合"
        )
        last = self._db.get_last_test_time()
        self._status_last_test.setText(
            f"上次测试: {last}" if last else "暂无测试记录"
        )

    # ── 菜单操作 ───────────────────────────────────────────

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TestTool",
            f"<h3>TestTool v{__version__}</h3>"
            "<p>网络测试工具 —— 连通性检测 & 协议测试</p>"
            "<p>基于 Python + PySide6 + SQLite 构建</p>"
            "<p><a href='https://github.com/flyingcherryblossoms/TestTool'>"
            "github.com/flyingcherryblossoms/TestTool</a></p>"
        )

    def _open_port_scan(self):
        dlg = PortScanDialog(self._db, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._conn_panel.refresh_collection_list()
            self._update_statusbar()

    def _open_csp_parser(self):
        """打开 CSP 报文解析对话框。"""
        dlg = CspParserDialog(parent=self)
        dlg.exec()

    def _on_main_tab_changed(self, idx: int):
        """记住当前打开的标签页。"""
        self._db.set_setting("last_main_tab", str(idx))

    def _on_protocol_test_selected(self, ip: str, port: int):
        self._tabs.setCurrentIndex(0)  # 协议测试
        self._proto_panel.prefill_client_target(ip, port)

    def _on_connectivity_test_requested(self, targets: list):
        """协议测试选中的目标 → 切到连通测试并加载为临时列表。"""
        self._tabs.setCurrentIndex(1)  # 连通测试
        self._conn_panel.load_temporary_targets(targets)

    # ── 窗口关闭 ───────────────────────────────────────────

    def closeEvent(self, event):
        if any(detail._history_file_worker and detail._history_file_worker.isRunning()
               for _, detail in self._proto_panel._target_tabs.values()):
            QMessageBox.information(self, "正在处理历史文件", "请等待测试历史导入或导出完成后退出。")
            event.ignore()
            return
        # 未保存的预设报文/参数修改：先提示是否保存
        has_dirty_config = any(
            detail._client_panel._config_dirty
            for _, detail in self._proto_panel._target_tabs.values()
        )
        unsaved = self._proto_panel.has_unsaved_presets() or has_dirty_config
        if unsaved:
            parts = []
            if self._proto_panel.has_unsaved_presets():
                parts.append("预设报文")
            if has_dirty_config:
                parts.append("参数修改")
            reply = QMessageBox.question(
                self, "未保存的内容",
                f"有{'、'.join(parts)}未保存，是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No
            )
            if reply == QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.Yes:
                self._proto_panel.save_unsaved_presets()
                for _, detail in self._proto_panel._target_tabs.values():
                    if detail._client_panel._config_dirty:
                        detail._save_params()
        active = self._conn_panel.is_test_running()
        active = active or self._proto_panel.has_active_servers()
        if active:
            reply = QMessageBox.question(
                self, "确认退出",
                "有正在进行的测试或运行中的监听器，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._conn_panel.stop_test()
            # 退出应用前确保扫描线程已结束，避免销毁仍在运行的 QThread。
            worker = self._conn_panel._test_panel._worker
            if worker and worker.isRunning():
                worker.wait()
            self._proto_panel.stop_all_servers()
        event.accept()
