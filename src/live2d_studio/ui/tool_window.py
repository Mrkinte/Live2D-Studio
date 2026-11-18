"""Fluent 工具窗口：图标侧边条 + 可折叠的圆角内容卡片。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    FluentIconBase,
    IconWidget,
    NavigationPushButton,
    StrongBodyLabel,
    TransparentToolButton,
)

from .theme import (
    TOOL_WINDOW_STRIPE_WIDTH,
    apply_theme_qss,
    panel_card_qss,
    tool_window_header_qss,
)

_EDGES = ("left", "right", "bottom")


class ToolWindowArea(QWidget):
    """同一区域只显示一个面板，侧边条常驻，内容可整体收起。"""

    visibilityChanged = Signal(bool)
    activeChanged = Signal(str)

    def __init__(
            self,
            position: str,
            integrated_stripe: bool = True,
            closable: bool = True,
            parent=None,
    ) -> None:
        super().__init__(parent)
        self._position = position if position in _EDGES else "left"
        # 左侧由 FluentWindow 的 navigationInterface 当侧边条
        self._integrated_stripe = integrated_stripe and self._position != "left"
        self._windows: dict[
            str, tuple[str, FluentIconBase, QWidget, NavigationPushButton | None]
        ] = {}
        self._active_key = ""
        self._visible = False

        outer = (
            QVBoxLayout(self)
            if self._position == "bottom"
            else QHBoxLayout(self)
        )
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._content = QFrame(self)
        self._content.setObjectName("toolWindowCard")
        self._content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_theme_qss(self._content, *panel_card_qss())
        content_layout = QVBoxLayout(self._content)
        # 1px 内边距让表格不会盖住卡片圆角
        content_layout.setContentsMargins(1, 1, 1, 1)
        content_layout.setSpacing(0)

        header = QWidget(self._content)
        header.setObjectName("toolWindowHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_theme_qss(header, *tool_window_header_qss())
        header.setFixedHeight(34)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(6)
        self._header_layout = header_layout
        self._icon = IconWidget(header)
        self._icon.setFixedSize(16, 16)
        self._title = StrongBodyLabel("", header)
        header_layout.addWidget(self._icon)
        header_layout.addWidget(self._title)
        header_layout.addStretch(1)
        self._hide_button: TransparentToolButton | None = None
        if closable:
            self._hide_button = TransparentToolButton(FluentIcon.REMOVE, header)
            self._hide_button.setFixedSize(24, 24)
            self._hide_button.setToolTip("隐藏面板")
            self._hide_button.clicked.connect(self.hide_window)
            header_layout.addWidget(self._hide_button)

        self._stack = QStackedWidget(self._content)
        content_layout.addWidget(header)
        content_layout.addWidget(self._stack, 1)
        self._content.setVisible(False)

        self._stripe: QWidget | None = None
        self._stripe_layout: QHBoxLayout | QVBoxLayout | None = None
        if self._integrated_stripe:
            self._stripe = QWidget(self)
            if self._position == "bottom":
                self._stripe.setFixedHeight(TOOL_WINDOW_STRIPE_WIDTH)
                self._stripe_layout = QHBoxLayout(self._stripe)
                self._stripe_layout.setContentsMargins(6, 0, 6, 0)
            else:
                self._stripe.setFixedWidth(TOOL_WINDOW_STRIPE_WIDTH)
                self._stripe_layout = QVBoxLayout(self._stripe)
                self._stripe_layout.setContentsMargins(0, 6, 0, 6)
            self._stripe_layout.setSpacing(4)
            self._stripe_layout.addStretch(1)

        if self._stripe is None:
            outer.addWidget(self._content, 1)
        elif self._position == "right":
            outer.addWidget(self._content, 1)
            outer.addWidget(self._stripe)
        elif self._position == "bottom":
            outer.addWidget(self._content, 1)
            outer.addWidget(self._stripe)
        else:
            outer.addWidget(self._stripe)
            outer.addWidget(self._content, 1)

    def add_window(
            self,
            key: str,
            title: str,
            icon: FluentIconBase,
            widget: QWidget,
            shortcut: str = "",
    ) -> None:
        if key in self._windows:
            return
        self._stack.addWidget(widget)

        button = None
        if self._stripe is not None and self._stripe_layout is not None:
            button = NavigationPushButton(icon, "", True, self._stripe)
            button.setCompacted(True)
            button.setToolTip(f"{title} ({shortcut})" if shortcut else title)
            button.clicked.connect(
                lambda _checked=False, k=key: self.toggle_window(k)
            )
            self._stripe_layout.insertWidget(
                self._stripe_layout.count() - 1, button
            )

        self._windows[key] = (title, icon, widget, button)
        if not self._active_key:
            self._set_active_display(key)
        self._sync_buttons()

    def add_header_widget(self, widget: QWidget) -> None:
        # 排在隐藏按钮之前，没有隐藏按钮时直接落到标题栏最右侧
        if self._hide_button is not None:
            index = self._header_layout.indexOf(self._hide_button)
        else:
            index = self._header_layout.count()
        self._header_layout.insertWidget(index, widget)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def show_window(self, key: str) -> None:
        if key not in self._windows:
            return
        was_visible = self._visible
        changed = key != self._active_key
        if changed:
            self._set_active_display(key)
        self._visible = True
        self._content.setVisible(True)
        self._sync_buttons()
        if not was_visible:
            self.visibilityChanged.emit(True)
        if changed:
            self.activeChanged.emit(key)

    def hide_window(self) -> None:
        if not self._visible:
            self._content.setVisible(False)
            self._sync_buttons()
            return
        self._visible = False
        self._content.setVisible(False)
        self._sync_buttons()
        self.visibilityChanged.emit(False)

    def toggle_window(self, key: str) -> None:
        if key not in self._windows:
            return
        if self._visible and key == self._active_key:
            self.hide_window()
        else:
            self.show_window(key)

    def set_active_window(self, key: str) -> None:
        if key not in self._windows or key == self._active_key:
            return
        self._set_active_display(key)
        self._sync_buttons()
        self.activeChanged.emit(key)

    def set_window_selected(self, key: str, selected: bool) -> None:
        entry = self._windows.get(key)
        if entry is not None and entry[3] is not None:
            entry[3].setSelected(selected)

    def is_window_visible(self) -> bool:
        return self._visible

    def active_key(self) -> str:
        return self._active_key

    def window_keys(self) -> list[str]:
        return list(self._windows)

    def _set_active_display(self, key: str) -> None:
        title, icon, widget, _button = self._windows[key]
        self._active_key = key
        self._stack.setCurrentWidget(widget)
        self._title.setText(title)
        self._icon.setIcon(icon)

    def _sync_buttons(self) -> None:
        for key, (_title, _icon, _widget, button) in self._windows.items():
            if button is not None:
                button.setSelected(self._visible and key == self._active_key)
