"""Fluent theme setup and shared UI constants."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QSplitter,
    QSplitterHandle,
    QWidget,
)
from qfluentwidgets import (
    CustomStyleSheet,
    TableWidget,
    Theme,
    ToolTip,
    ToolTipFilter,
    ToolTipPosition,
    TreeItemDelegate,
    TreeWidget,
    isDarkTheme,
    qconfig,
    setStyleSheet,
    setTheme,
)
from qfluentwidgets.components.widgets.tool_tip import (
    ItemViewToolTip,
    ItemViewToolTipDelegate,
    ItemViewToolTipManager,
    ItemViewToolTipType,
    ToolTipPositionManager,
)

# 语义色 token: (light, dark)，档位越高越接近“浮起”的表面
COLORS = {
    "surface0": ("#f3f3f3", "#202020"),  # 窗口 / 画布底层
    "surface1": ("#fafafa", "#272727"),  # Panel 卡片（第二档）
    "surface2": ("#ffffff", "#2d2d2d"),  # 表头 / 输入框 / hover（第三档）
    "border": ("rgba(0, 0, 0, 20)", "rgba(255, 255, 255, 26)"),
    "text": ("#1a1a1a", "#e6e6e6"),
    "textMuted": ("#6b6b6b", "#9a9a9a"),
    "accent": ("#0067c0", "#6f7bf7"),
    "success": ("#0f7b0f", "#6ccb5f"),
    "warning": ("#9d5d00", "#fce100"),
    "error": ("#c42b1c", "#ff99a4"),
}

THEMES = {
    "light": Theme.LIGHT,
    "dark": Theme.DARK,
    "auto": Theme.AUTO,
}
DEFAULT_THEME = "dark"
MESH_HIGHLIGHT_COLOR = QColor("#ffd166")
PART_HIGHLIGHT_COLOR = QColor("#4cc9f0")
STAGE_BORDER_COLOR = QColor(138, 143, 153, 170)
CONTROL_HEIGHT = 32
PANEL_RADIUS = 8
TOOL_WINDOW_STRIPE_WIDTH = 40
TOOL_WINDOW_BUTTON_SIZE = (40, 36)


def token(name: str) -> QColor:
    light, dark = COLORS[name]
    return QColor(dark if isDarkTheme() else light)


def studio_palette() -> QPalette:
    """普通控件和表格单元格文字取自应用调色板，需要跟随 Fluent 主题"""
    offset = 1 if isDarkTheme() else 0
    text = QColor(COLORS["text"][offset])
    muted = QColor(COLORS["textMuted"][offset])
    accent = QColor(COLORS["accent"][offset])
    surface0 = QColor(COLORS["surface0"][offset])
    surface1 = QColor(COLORS["surface1"][offset])
    surface2 = QColor(COLORS["surface2"][offset])

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, surface0)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, surface2)
    palette.setColor(QPalette.ColorRole.AlternateBase, surface1)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, surface1)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, surface1)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, accent)
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, muted
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted
    )
    return palette


def _refresh_app_palette() -> None:
    app = QApplication.instance()
    if app is not None:
        app.setPalette(studio_palette())


_palette_connected = False


def apply_theme_qss(widget: QWidget, light_qss: str, dark_qss: str) -> None:
    """给控件装上亮/暗双套 QSS，主题切换时自动刷新

    必须走 setStyleSheet 注册到样式管理器：setCustomStyleSheet 只写属性，
    控件没被注册时没有样式监听器，QSS 不会生效。
    """
    sheet = CustomStyleSheet(widget).setCustomStyleSheet(light_qss, dark_qss)
    setStyleSheet(widget, sheet)


def panel_card_qss() -> tuple[str, str]:
    """工具窗口卡片背景的 (light, dark) QSS"""

    def build(bg: str, border: str) -> str:
        return (
            "QFrame#toolWindowCard {"
            f" background: {bg};"
            f" border-radius: {PANEL_RADIUS}px;"
            "}"
        )

    return (
        build(COLORS["surface1"][0], COLORS["border"][0]),
        build(COLORS["surface1"][1], COLORS["border"][1]),
    )


def tool_window_header_qss() -> tuple[str, str]:
    """工具窗口标题栏的 (light, dark) QSS"""

    def build(border: str) -> str:
        return (
            "QWidget#toolWindowHeader {"
            " background: transparent;"
            f" border-bottom: 1px solid {border};"
            "}"
        )

    return build(COLORS["border"][0]), build(COLORS["border"][1])


def apply_studio_theme(name: str = DEFAULT_THEME) -> str:
    """应用主题，返回实际生效的主题名；未知名称回退默认主题"""
    global _palette_connected
    theme_name = name if name in THEMES else DEFAULT_THEME
    setTheme(THEMES[theme_name])
    _refresh_app_palette()
    if not _palette_connected:
        qconfig.themeChangedFinished.connect(_refresh_app_palette)
        _palette_connected = True
    return theme_name


def install_fluent_tooltips(root: QWidget) -> None:
    for widget in root.findChildren(QWidget):
        if isinstance(widget, QAbstractItemView):
            continue
        # 单元格提示由 view 的 delegate 负责，装过滤器会吞掉 ToolTip 事件
        if isinstance(widget.parent(), QAbstractItemView):
            continue
        install_fluent_tooltip(widget)


# Wayland 下顶层窗口的位置由合成器决定，move() 无效，提示框会堆到屏幕中央；
# Windows 的顶层提示框定位正常，直接用库的默认实现
_USE_WIDGET_TOOLTIPS = sys.platform != "win32"


class _StudioToolTip(ToolTip):
    """Wayland 用的窗口内提示框，坐标完全由客户端控制"""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setWindowFlags(Qt.WindowType.Widget)

    def adjustPos(self, widget: QWidget, position) -> None:
        target = ToolTipPositionManager.make(position).position(self, widget)
        self._move_into_window(target)

    def _move_into_window(self, global_pos) -> None:
        self.move(self.parentWidget().mapFromGlobal(global_pos))
        self.raise_()


class _StudioItemViewToolTip(ItemViewToolTip):
    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setWindowFlags(Qt.WindowType.Widget)

    def adjustPos(self, view, item_rect, tooltip_type) -> None:
        manager = ItemViewToolTipManager.make(tooltip_type, item_rect)
        target = manager.position(self, view)
        self.move(self.parentWidget().mapFromGlobal(target))
        self.raise_()


class _StudioToolTipFilter(ToolTipFilter):
    def _createToolTip(self) -> ToolTip:
        parent = self.parent()
        return _StudioToolTip(parent.toolTip(), parent.window())


def install_fluent_tooltip(widget: QWidget) -> None:
    if any(isinstance(child, ToolTipFilter) for child in widget.children()):
        return
    filter_cls = _StudioToolTipFilter if _USE_WIDGET_TOOLTIPS else ToolTipFilter
    widget.installEventFilter(
        filter_cls(widget, showDelay=300, position=ToolTipPosition.TOP)
    )


# splitter 只负责拖动，不画分隔线
class StudioSplitterHandle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, splitter: QSplitter) -> None:
        super().__init__(orientation, splitter)
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if orientation == Qt.Orientation.Horizontal
            else Qt.CursorShape.SplitVCursor
        )

    def paintEvent(self, event) -> None:
        pass


class StudioSplitter(QSplitter):
    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(6)

    def createHandle(self) -> QSplitterHandle:
        return StudioSplitterHandle(self.orientation(), self)


class StudioTableWidget(TableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        # 应用表格 QSS 时控件会固定一份调色板，切主题后单元格文字色不会自己更新
        qconfig.themeChangedFinished.connect(self._sync_palette)

    def _sync_palette(self) -> None:
        self.setPalette(QApplication.palette())


class _StudioItemViewToolTipDelegate(ItemViewToolTipDelegate):
    def _createToolTip(self):
        return _StudioItemViewToolTip(self.text, self.parent().window())


class _ToolTipTreeItemDelegate(TreeItemDelegate):
    """TreeItemDelegate 没接 helpEvent，单元格提示会退回原生样式"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        delegate_cls = (
            _StudioItemViewToolTipDelegate
            if _USE_WIDGET_TOOLTIPS
            else ItemViewToolTipDelegate
        )
        self._tooltip = delegate_cls(parent, 300, ItemViewToolTipType.LIST)

    def helpEvent(self, event, view, option, index) -> bool:
        return self._tooltip.helpEvent(event, view, option, index)


class StudioTreeWidget(TreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(_ToolTipTreeItemDelegate(self))
