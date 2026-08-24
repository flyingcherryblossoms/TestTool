"""主窗口 —— 连通测试 / 协议测试两大标签页，菜单栏和状态栏。"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.database import Database
from src.ui import shortcuts
from src.ui.connectivity_panel import ConnectivityPanel
from src.ui.csp_parser_dialog import CspParserDialog
from src.ui.port_scan_dialog import PortScanDialog
from src.ui.protocol_panel import ProtocolPanel
from src.ui.shortcut_settings_dialog import ShortcutSettingsDialog


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
        # 先加载快捷键绑定，面板创建时即读到已存配置
        shortcuts.load(self._db)
        self.setWindowTitle("测试工具")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 850)

        self.setStyleSheet("""
            QTableWidget::item:selected, QTreeWidget::item:selected,
            QListWidget::item:selected {
                background-color: #3498db; color: white;
            }
            QTableWidget::item:selected:!active, QTreeWidget::item:selected:!active,
            QListWidget::item:selected:!active {
                background-color: #5dade2; color: white;
            }
        """)
        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._update_statusbar()

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

        # 设置：作为菜单栏项插在「帮助」左侧
        settings_action = QAction("设置(&S)", self)
        settings_action.triggered.connect(self._open_shortcut_settings)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        menubar.insertAction(help_menu.menuAction(), settings_action)

    def _open_shortcut_settings(self):
        """打开快捷键设置对话框，保存后热更新全部快捷键。"""
        dlg = ShortcutSettingsDialog(self._db, self)
        if dlg.exec() == QDialog.Accepted:
            shortcuts.save(self._db, dlg.shortcuts)
            shortcuts.set_active(dlg.shortcuts)
            shortcuts.apply_shortcuts()

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
            "<h3>TestTool v1.0</h3>"
            "<p>网络测试工具 —— 连通性检测 & 协议测试</p>"
            "<p>基于 Python + PySide6 + SQLite 构建</p>"
            "<p><a href='https://github.com/flyingcherryblossoms/TestTool'>"
            "github.com/flyingcherryblossoms/TestTool</a></p>"
        )

    def _open_port_scan(self):
        dlg = PortScanDialog(self._db, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._conn_panel.refresh_collection_list()

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
            self._proto_panel.stop_all_servers()
        event.accept()
