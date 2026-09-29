"""应用主题与 Qt 调色板，组合控件的箭头由 Fusion 原生绘制。"""

from __future__ import annotations

import re
from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


THEMES = {
    "light": {
        "window": "#e9eef4", "surface": "#ffffff", "text": "#243447",
        "muted": "#687888", "border": "#aebfce", "line": "#bac9d7",
        "hover": "#dcecf8", "pressed": "#c9e2f5", "header": "#dfe7ef",
        "alternate": "#f0f4f8", "accent": "#2878b9", "selected_text": "#ffffff",
        "disabled": "#82909e", "disabled_bg": "#e3eaf1",
    },
    "light_original": {
        "window": "#f5f7fa", "surface": "#ffffff", "text": "#243447",
        "muted": "#687888", "border": "#cbd6e2", "line": "#dce4ec",
        "hover": "#eaf3fb", "pressed": "#d8eafa", "header": "#edf2f7",
        "alternate": "#f7f9fc", "accent": "#2878b9", "selected_text": "#ffffff",
        "disabled": "#929eaa", "disabled_bg": "#eef1f4",
    },
    "dark": {
        "window": "#151b23", "surface": "#222b36", "text": "#edf3f9",
        "muted": "#a8b7c6", "border": "#576879", "line": "#465565",
        "hover": "#34465a", "pressed": "#425b74", "header": "#303d4b",
        "alternate": "#283340", "accent": "#347fba", "selected_text": "#ffffff",
        "disabled": "#91a0ae", "disabled_bg": "#303b47",
    },
    "high_contrast": {
        "window": "#000000", "surface": "#202020", "text": "#ffffff",
        "muted": "#ffffff", "border": "#ffffff", "line": "#ffffff",
        "hover": "#193557", "pressed": "#284b76", "header": "#000000",
        "alternate": "#101010", "accent": "#ffff00", "selected_text": "#000000",
        "disabled": "#d0d0d0", "disabled_bg": "#252525",
    },
}


_STYLE = Template("""
QWidget { color: $text; font-size: 12px; }
QMainWindow, QDialog, QTabWidget::pane { background: $window; }
QGroupBox {
    background: $surface; border: 1px solid $line;
    border-radius: 6px; margin-top: 10px; padding-top: 10px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background: $surface; border: 1px solid $border;
    border-radius: 4px; padding: 4px 6px;
    selection-background-color: $accent; selection-color: $selected_text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid $accent; }
/* 组合控件交给 Fusion 绘制箭头；只重置其内部文字框。 */
QComboBox QLineEdit, QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {
    background: transparent; border: 0; border-radius: 0; padding: 0;
}
QPushButton {
    background: $surface; border: 1px solid $border;
    border-radius: 4px; padding: 5px 10px; min-height: 20px;
}
QPushButton:hover { background: $hover; border-color: $accent; }
QPushButton:pressed { background: $pressed; }
QPushButton:disabled { color: $disabled; background: $disabled_bg; border-color: $line; }
QTabBar::tab {
    background: $header; border: 1px solid $line;
    padding: 7px 14px; margin-right: 2px;
}
QTabBar::tab:selected { background: $surface; border-bottom: 2px solid $accent; }
QTabBar::tab:hover:!selected { background: $hover; }
QTableWidget, QTreeWidget, QListWidget {
    background: $surface; alternate-background-color: $alternate;
    border: 1px solid $line; gridline-color: $line;
}
QHeaderView::section {
    background: $header; border: 0; border-right: 1px solid $line;
    border-bottom: 1px solid $line; padding: 5px 7px; font-weight: 600;
}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected,
QAbstractItemView::item:selected { background: $accent; color: $selected_text; }
QProgressBar { border: 1px solid $border; border-radius: 4px; background: $header; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QStatusBar { background: $header; border-top: 1px solid $line; }
QMenu { background: $surface; border: 1px solid $border; }
QMenu::item:selected { background: $accent; color: $selected_text; }
QToolTip { color: $text; background: $surface; border: 1px solid $border; }
""")


def apply_theme(app: QApplication, name: str, scale_percent: int = 100) -> str:
    """立即应用主题；未知设置回退到浅色。"""
    if name not in THEMES:
        name = "light"
    colors = THEMES[name]
    palette = QPalette()
    for role, key in (
        (QPalette.Window, "window"), (QPalette.WindowText, "text"),
        (QPalette.Base, "surface"), (QPalette.AlternateBase, "alternate"),
        (QPalette.Text, "text"), (QPalette.Button, "surface"),
        (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "surface"),
        (QPalette.ToolTipText, "text"), (QPalette.Highlight, "accent"),
        (QPalette.HighlightedText, "selected_text"),
        (QPalette.PlaceholderText, "muted"),
    ):
        palette.setColor(role, QColor(colors[key]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(colors["disabled"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(colors["disabled"]))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(colors["disabled"]))
    app.setPalette(palette)
    stylesheet = _STYLE.substitute(colors)
    factor = scale_percent / 100
    stylesheet = re.sub(
        r"(\d+)px", lambda match: f"{max(1, round(int(match.group(1)) * factor))}px",
        stylesheet,
    )
    app.setStyleSheet(stylesheet)
    return name
