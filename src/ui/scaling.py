"""界面运行时缩放：字体、样式尺寸、布局和显式控件尺寸一起调整。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QLayout, QTableWidget, QWidget

from src.database import Database
from src.ui import shortcuts
from src.ui.table_utils import fit_table_buttons


MIN_SCALE_PERCENT = 50
MAX_SCALE_PERCENT = 300
DEFAULT_SCALE_PERCENT = 100
SCALE_STEP = 10


def parse_scale_percent(value: str) -> int:
    """无效配置回退到 100%。"""
    try:
        percent = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SCALE_PERCENT
    if MIN_SCALE_PERCENT <= percent <= MAX_SCALE_PERCENT:
        return percent
    return DEFAULT_SCALE_PERCENT


def recommended_scale_percent(screen) -> int:
    """按 Qt 的逻辑屏幕尺寸选择初始比例，避免对系统 DPI 重复放大。"""
    if screen is None:
        return DEFAULT_SCALE_PERCENT
    area = screen.availableGeometry()
    width, height = area.width(), area.height()
    if width < 1200 or height < 720:
        return 80
    if width < 1450 or height < 850:
        return 90
    if width >= 3200 and height >= 1700:
        return 150
    if width >= 2500 and height >= 1350:
        return 125
    return DEFAULT_SCALE_PERCENT


class UIScaleManager(QObject):
    """保留控件原始尺寸，使连续缩放不会累积舍入误差。"""

    def __init__(self, app: QApplication, db: Database):
        super().__init__(app)
        self._app = app
        self._shortcut_handler = None
        self.shortcuts_enabled = True
        self._base_font = app.font()
        saved = db.get_setting("ui_scale_percent", "")
        if saved and str(parse_scale_percent(saved)) == saved.strip():
            self.percent = parse_scale_percent(saved)
        else:
            self.percent = recommended_scale_percent(app.primaryScreen())
            db.set_setting("ui_scale_percent", str(self.percent))
        app._ui_scale_percent = self.percent
        self._apply_font()
        app.installEventFilter(self)

    def set_shortcut_handler(self, handler):
        """注册应用级缩放处理器，模态对话框中也可使用快捷键。"""
        self._shortcut_handler = handler

    def _apply_font(self):
        font = self._base_font
        font = type(font)(font)
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(5.0, font.pointSizeF() * self.percent / 100))
        elif font.pixelSize() > 0:
            font.setPixelSize(max(6, round(font.pixelSize() * self.percent / 100)))
        self._app.setFont(font)

    def set_percent(self, percent: int):
        """立即缩放当前全部窗口；后续显示的控件由事件过滤器处理。"""
        old_factor = self.percent / 100
        windows = self._app.topLevelWidgets()
        for window in windows:
            if window.isVisible():
                # 以用户当前调整过的窗口尺寸为基准缩放。
                window._ui_scale_window_size = (
                    round(window.width() / old_factor),
                    round(window.height() / old_factor),
                )
        self.percent = max(MIN_SCALE_PERCENT, min(MAX_SCALE_PERCENT, percent))
        self._app._ui_scale_percent = self.percent
        self._apply_font()
        for window in windows:
            self.apply_widget(window, resize_window=True)

    def apply_widget(self, widget: QWidget, resize_window: bool = False):
        if not isinstance(widget, QWidget):
            return
        factor = self.percent / 100
        self._scale_constraints(widget, factor)
        for child in widget.findChildren(QWidget):
            self._scale_constraints(child, factor)
        for layout in widget.findChildren(QLayout):
            self._scale_layout(layout, factor)
        tables = [widget] if isinstance(widget, QTableWidget) else []
        tables.extend(widget.findChildren(QTableWidget))
        for table in tables:
            fit_table_buttons(table, self.percent)
        if resize_window and widget.isWindow():
            baseline = getattr(widget, "_ui_scale_window_size", None)
            if baseline is None:
                baseline = (widget.width(), widget.height())
                widget._ui_scale_window_size = baseline
            screen = widget.screen() or self._app.primaryScreen()
            area = screen.availableGeometry() if screen else None
            width = round(baseline[0] * factor)
            height = round(baseline[1] * factor)
            if area:
                width = min(width, area.width() - 20)
                height = min(height, area.height() - 20)
            widget.resize(max(widget.minimumWidth(), width),
                          max(widget.minimumHeight(), height))

    def _scale_constraints(self, widget: QWidget, factor: float):
        baseline = getattr(widget, "_ui_scale_constraints", None)
        if baseline is None:
            minimum, maximum = widget.minimumSize(), widget.maximumSize()
            baseline = (minimum.width(), minimum.height(),
                        maximum.width(), maximum.height())
            widget._ui_scale_constraints = baseline
        min_w, min_h, max_w, max_h = baseline
        limit = 16777215  # QWidget 默认最大尺寸
        widget.setMinimumSize(round(min_w * factor), round(min_h * factor))
        widget.setMaximumSize(round(max_w * factor) if max_w < limit else limit,
                              round(max_h * factor) if max_h < limit else limit)

    def _scale_layout(self, layout: QLayout, factor: float):
        baseline = getattr(layout, "_ui_scale_layout", None)
        if baseline is None:
            margin = layout.contentsMargins()
            baseline = (margin.left(), margin.top(), margin.right(),
                        margin.bottom(), layout.spacing())
            layout._ui_scale_layout = baseline
        left, top, right, bottom, spacing = baseline
        layout.setContentsMargins(*(round(value * factor) for value in
                                    (left, top, right, bottom)))
        if spacing >= 0:
            layout.setSpacing(round(spacing * factor))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress and self._shortcut_handler and self.shortcuts_enabled:
            for action_id in ("zoom_in", "zoom_out", "zoom_reset"):
                if shortcuts.event_matches(event, action_id):
                    self._shortcut_handler(action_id)
                    return True
        if event.type() == QEvent.Show and isinstance(watched, QWidget):
            if not hasattr(watched, "_ui_scale_constraints"):
                QTimer.singleShot(0, lambda widget=watched: self._apply_shown(widget))
        return False

    def _apply_shown(self, widget):
        try:
            self.apply_widget(widget, resize_window=widget.isWindow())
        except RuntimeError:
            pass  # 控件在排队处理期间已关闭
