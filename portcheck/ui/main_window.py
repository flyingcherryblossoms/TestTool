"""主窗口 —— 连通测试 / 协议测试两大标签页，菜单栏和状态栏。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from portcheck.csv_handler import export_targets_to_csv
from portcheck.database import Database
from portcheck.excel_handler import export_targets_to_excel
from portcheck.ui.connectivity_panel import ConnectivityPanel
from portcheck.ui.port_scan_dialog import PortScanDialog
from portcheck.ui.protocol_panel import ProtocolPanel


class BatchDialog(QDialog):
    """新建/编辑集合的对话框。"""

    def __init__(self, title: str, name: str = "", description: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QFormLayout(self)

        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText("例如: 生产环境服务器")
        self._name_edit.setMinimumWidth(280)
        layout.addRow("集合名称:", self._name_edit)

        self._desc_edit = QLineEdit(description)
        self._desc_edit.setPlaceholderText("可选描述")
        self._desc_edit.setMinimumWidth(280)
        layout.addRow("描述:", self._desc_edit)

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

    @property
    def description(self) -> str:
        return self._desc_edit.text().strip()


class MainWindow(QMainWindow):
    """TestTool 主窗口。"""

    def __init__(self, db_path: str = ""):
        super().__init__()
        self._db = Database(db_path)
        self.setWindowTitle("TestTool - 网络测试工具")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 850)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._update_statusbar()

    # ── 菜单栏 ─────────────────────────────────────────────

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        import_action = QAction("导入目标...", self)
        import_action.triggered.connect(self._import_targets_file)
        file_menu.addAction(import_action)

        export_action = QAction("导出目标...", self)
        export_action.triggered.connect(self._export_all_targets)
        file_menu.addAction(export_action)
        file_menu.addSeparator()

        port_scan_action = QAction("端口扫描...", self)
        port_scan_action.triggered.connect(self._open_port_scan)
        file_menu.addAction(port_scan_action)
        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ── 主布局 ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        # Tab 1: 连通测试
        self._conn_panel = ConnectivityPanel(self._db)
        self._conn_panel.targets_changed.connect(self._update_statusbar)
        self._conn_panel.protocol_test_selected.connect(
            self._on_protocol_test_selected
        )
        self._tabs.addTab(self._conn_panel, "连通测试")

        # Tab 2: 协议测试
        self._proto_panel = ProtocolPanel(self._db)
        self._tabs.addTab(self._proto_panel, "协议测试")

        layout.addWidget(self._tabs)

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
        batch_count = len(self._db.get_all_batches())
        self._status_target_count.setText(
            f"共 {total} 个目标 / {batch_count} 个集合"
        )
        last = self._db.get_last_test_time()
        self._status_last_test.setText(
            f"上次测试: {last}" if last else "暂无测试记录"
        )

    # ── 菜单操作 ───────────────────────────────────────────

    def _import_targets_file(self):
        self._conn_panel._target_panel._import_file()

    def _export_all_targets(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", "targets.xlsx",
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not filepath:
            return
        targets = self._db.get_targets(None)
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

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TestTool",
            "<h3>TestTool v1.0</h3>"
            "<p>网络测试工具</p>"
            "<p>基于 Python + PySide6 + SQLite 构建</p>"
        )

    def _open_port_scan(self):
        dlg = PortScanDialog(self._db, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._conn_panel.refresh_batch_list()

    def _on_protocol_test_selected(self, ip: str, port: int):
        self._tabs.setCurrentIndex(1)  # 协议测试
        self._proto_panel.prefill_client_target(ip, port)

    # ── 窗口关闭 ───────────────────────────────────────────

    def closeEvent(self, event):
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
