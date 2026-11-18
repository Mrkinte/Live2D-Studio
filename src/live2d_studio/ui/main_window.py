"""Main studio window: menu bar, JetBrains-style tool windows and document actions."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QActionGroup, QColor, QIcon, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CaptionLabel,
    FluentIcon,
    FluentWindow,
    InfoBadge,
    InfoLevel,
    MessageBox,
    NavigationItemPosition,
    NavigationPushButton,
    RoundMenu,
    TransparentPushButton,
    TransparentToolButton,
    qconfig,
)
from qfluentwidgets.components.widgets.menu import MenuAnimationType

from .backup_dialog import BackupCleanupDialog
from .click_panel import ClickPanel
from .dialogs import (
    AboutDialog,
    ask_save_changes,
    show_error,
    show_update_available,
    show_up_to_date,
)
from .inspector import InspectorPanel
from .merge_panel import MergePanel
from .model_info_dialog import ModelInfoDialog
from .motion_tree import MotionTreePanel
from .param_panel import ParameterPanel
from .part_tree import PartTreePanel
from .preview import Live2DCanvas
from .theme import (
    COLORS,
    CONTROL_HEIGHT,
    StudioSplitter,
    apply_studio_theme,
    apply_theme_qss,
    install_fluent_tooltips,
    token,
)
from .tool_window import ToolWindowArea
from .. import ICON_PATH, __version__
from ..config import Config
from ..core import (
    LATEST_RELEASE_API,
    Moc3Document,
    Moc3Error,
    PatchError,
    UpdateError,
    is_newer,
    motion_entries,
    motion_groups,
    read_hit_areas,
    read_model3,
    read_model_info,
    release_from_json,
)

LAYOUT_VERSION = 7
TOOL_WINDOWS = (
    ("part", "图层 / Part", "Alt+1"),
    ("motion", "Motion 动作", "Alt+2"),
    ("params", "Live2D 参数", "Alt+3"),
    ("merge", "合并动作（按帧采样）", "Alt+4"),
    ("click", "点击绑定", "Alt+5"),
    ("inspector", "属性", "Alt+7"),
)
TOOL_WINDOW_ICONS = {
    "part": FluentIcon.LAYOUT,
    "motion": FluentIcon.MOVIE,
    "params": FluentIcon.EDIT,
    "merge": FluentIcon.CUT,
    "click": FluentIcon.PROJECTOR,
    "inspector": FluentIcon.INFO,
}
THEME_OPTIONS = (
    ("light", "浅色"),
    ("dark", "深色"),
    ("auto", "跟随系统"),
)
LEFT_COLLAPSED = 0
RIGHT_COLLAPSED = 0
BOTTOM_COLLAPSED = 0
NAV_TOP_MARGIN = 50
NAV_BOTTOM_MARGIN = 40


def _veil(color: str, alpha: int) -> str:
    rgb = QColor(color)
    return f"rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, {alpha})"


class _LoadingOverlay(QWidget):
    """加载期间盖住预览区，同时挡住画布上的鼠标操作

    live2d 原生加载和纹理上传期间一直持有 GIL，主线程拿不到时间片，
    旋转动画只会在起始位置停住；这里改用静态图标，持续反馈交给系统忙碌光标
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        logo = QLabel(self)
        logo.setFixedSize(48, 48)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(40, 40))
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignCenter)

        label = CaptionLabel("正在加载模型…", self)
        apply_theme_qss(
            label,
            f"color: {COLORS['textMuted'][0]}; background: transparent;",
            f"color: {COLORS['textMuted'][1]}; background: transparent;",
        )
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)

        apply_theme_qss(
            self,
            f"background: {_veil(COLORS['surface0'][0], 216)};",
            f"background: {_veil(COLORS['surface0'][1], 216)};",
        )

        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
            self.raise_()
        return super().eventFilter(obj, event)


class MainWindow(FluentWindow):
    """Fluent studio window with Part tree, preview and motion tooling."""

    def __init__(self, config: Config | None = None, parent=None) -> None:
        super().__init__(parent)
        self._config = config if config is not None else Config()
        self.document: Moc3Document | None = None
        self._selected_part: int | None = None
        self._selected_mesh: int | None = None
        self._last_dir = str(Path.cwd())
        self._base_status = "未打开模型"
        self._settings = QSettings("live2d-studio", "live2d-studio")
        self._network = QNetworkAccessManager(self)
        self._update_reply: QNetworkReply | None = None
        self._loading = False
        self._left_width = 300
        self._right_width = 270
        self._bottom_height = 260
        self._startup_states: tuple | None = None
        self._nav_ready = False

        self.setWindowTitle("Live2D Studio")
        self.resize(1480, 940)
        self.setMinimumSize(1120, 780)
        # Mica 的主机背板在亮色下会留成黑色（导航栏、标题栏、splitter 缝隙都透出它），
        # 关掉后由窗口自己画 surface0，亮/暗都能跟主题走
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(*COLORS["surface0"])
        self.setBackgroundColor(token("surface0"))
        qconfig.themeChangedFinished.connect(
            lambda: self.setBackgroundColor(token("surface0"))
        )
        panel = self.navigationInterface.panel
        panel.setReturnButtonVisible(False)
        panel.menuButton.hide()
        panel.vBoxLayout.setContentsMargins(0, NAV_TOP_MARGIN, 0, NAV_BOTTOM_MARGIN)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._restore_status)

        self._audio_output = QAudioOutput(self)
        self._motion_player = QMediaPlayer(self)
        self._motion_player.setAudioOutput(self._audio_output)

        self._build_actions()
        self._build_menus()
        self._build_ui()
        self._restore_layout()
        self._sync_theme_actions()
        self._update_actions()
        self._refresh_edit_label()

    def _build_actions(self) -> None:
        self.open_action = Action("打开模型", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dialog)

        self.save_action = Action("写入原文件", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.apply_changes)

        self.restore_action = Action("恢复打开时文件", self)
        self.restore_action.triggered.connect(self.restore_file)

        self.cleanup_action = Action("清理备份", self)
        self.cleanup_action.triggered.connect(self._cleanup_backups)

        self.model_info_action = Action("模型信息…", self)
        self.model_info_action.triggered.connect(self._show_model_info)

        self.quit_action = Action("退出", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

        self.undo_action = Action("撤销未写入修改", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self.undo_changes)

        self.reload_action = Action("重新加载预览", self)
        self.reload_action.setShortcut("F5")
        self.reload_action.triggered.connect(self.reload_preview)

        self.reset_view_action = Action("适应窗口", self)
        self.reset_view_action.setShortcut("Ctrl+0")
        self.reset_view_action.triggered.connect(self._reset_view)

        self.tool_window_actions: dict[str, Action] = {}
        for key, title, shortcut in TOOL_WINDOWS:
            action = Action(title, self)
            action.setCheckable(True)
            action.setShortcut(shortcut)
            action.triggered.connect(
                lambda _checked=False, k=key: self._toggle_tool_window(k)
            )
            self.tool_window_actions[key] = action

        self.play_action = Action("播放选中动作", self)
        self.play_action.setShortcut("Ctrl+Return")
        self.play_action.triggered.connect(self._play_selected_motion)

        self.stop_action = Action("停止动作", self)
        self.stop_action.triggered.connect(self._stop_motion)

        self.click_preview_action = Action("点击动作预览", self)
        self.click_preview_action.setCheckable(True)
        self.click_preview_action.setChecked(True)
        self.click_preview_action.setShortcut("Ctrl+B")
        self.click_preview_action.toggled.connect(self._set_click_preview)

        self.theme_actions: dict[str, Action] = {}
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for key, label in THEME_OPTIONS:
            action = Action(label, self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, k=key: self._set_theme(k)
            )
            self._theme_group.addAction(action)
            self.theme_actions[key] = action

        self.check_update_action = Action("检查更新…", self)
        self.check_update_action.triggered.connect(self._check_updates)

        self.about_action = Action("关于 Live2D Studio", self)
        self.about_action.triggered.connect(self._show_about)

        for action in (
                self.open_action,
                self.save_action,
                self.restore_action,
                self.cleanup_action,
                self.quit_action,
                self.undo_action,
                self.reload_action,
                self.reset_view_action,
                self.play_action,
                self.stop_action,
                self.click_preview_action,
        ):
            self.addAction(action)
        for action in self.tool_window_actions.values():
            self.addAction(action)

    def _build_menus(self) -> None:
        self.file_menu = RoundMenu("文件", self)
        self.file_menu.addAction(self.open_action)
        self.file_menu.addAction(self.save_action)
        self.file_menu.addAction(self.restore_action)
        self.file_menu.addAction(self.cleanup_action)
        self.file_menu.addAction(self.model_info_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)

        self.edit_menu = RoundMenu("编辑", self)
        self.edit_menu.addAction(self.undo_action)

        self.view_menu = RoundMenu("视图", self)
        self.view_menu.addAction(self.reset_view_action)
        self.view_menu.addAction(self.reload_action)
        self.view_menu.addSeparator()
        for action in self.tool_window_actions.values():
            self.view_menu.addAction(action)

        self.appearance_menu = RoundMenu("外观", self)
        for action in self.theme_actions.values():
            self.appearance_menu.addAction(action)

        self.help_menu = RoundMenu("帮助", self)
        self.help_menu.addAction(self.check_update_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)

        self._menus = (
            ("文件", self.file_menu),
            ("编辑", self.edit_menu),
            ("视图", self.view_menu),
            ("外观", self.appearance_menu),
            ("帮助", self.help_menu),
        )

    def _build_ui(self) -> None:
        self.workspace = QWidget(self)
        self.workspace.setObjectName("workspace")
        self.workspace.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.stackedWidget.setObjectName("mainStackedWidget")
        # 库默认给 StackedWidget 带 1px 边框；标记内容透明走库内置的无边框样式，
        # 直接覆盖 stylesheet 会在切换主题被库重新套用时失效
        self.workspace.setProperty("isStackedTransparent", True)
        self.stackedWidget.addWidget(self.workspace)
        self.stackedWidget.setCurrentWidget(self.workspace)
        self._updateStackedBackground()
        self._install_title_bar_menu()

        root_layout = QVBoxLayout(self.workspace)
        root_layout.setContentsMargins(4, 0, 4, 6)
        root_layout.setSpacing(6)

        root_layout.addWidget(self._build_splitters(), 1)
        root_layout.addWidget(self._build_status_bar())
        self.hBoxLayout.addWidget(self.right_rail)
        self.titleBar.raise_()
        install_fluent_tooltips(self)

    def _install_title_bar_menu(self) -> None:
        self.titleBar.hBoxLayout.insertWidget(
            2, self._build_menu_bar(), 0, Qt.AlignmentFlag.AlignVCenter
        )

    def _build_menu_bar(self) -> QWidget:
        bar = QWidget(self.titleBar)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(2)

        for title, menu in self._menus:
            button = TransparentPushButton(title, bar)
            button.setFixedHeight(CONTROL_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, m=menu, b=button: m.exec(
                    b.mapToGlobal(QPoint(0, b.height())),
                    aniType=MenuAnimationType.NONE,
                )
            )
            layout.addWidget(button)

        return bar

    def _build_preview_actions(self) -> QWidget:
        bar = QWidget(self.preview_area)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.click_preview_badge = InfoBadge("", bar, InfoLevel.INFOAMTION)
        layout.addWidget(self.click_preview_badge)

        self.click_preview_button = TransparentToolButton(
            FluentIcon.PROJECTOR, bar
        )
        self.click_preview_button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.click_preview_button.setToolTip("启用/禁用区域触发 (Ctrl+B)")
        self.click_preview_button.clicked.connect(
            lambda: self.click_preview_action.toggle()
        )
        layout.addWidget(self.click_preview_button)

        self.reload_button = TransparentToolButton(FluentIcon.SYNC, bar)
        self.reload_button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.reload_button.setToolTip("重新加载预览 (F5)")
        self.reload_button.clicked.connect(self.reload_preview)
        layout.addWidget(self.reload_button)

        self.reset_button = TransparentToolButton(FluentIcon.FIT_PAGE, bar)
        self.reset_button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.reset_button.setToolTip("适应窗口 (Ctrl+0)")
        self.reset_button.clicked.connect(self._reset_view)
        layout.addWidget(self.reset_button)

        self._sync_click_preview_ui(self.click_preview_action.isChecked())
        return bar

    def _build_splitters(self) -> StudioSplitter:
        self.horizontal_splitter = StudioSplitter(Qt.Orientation.Horizontal, self)
        self.horizontal_splitter.setChildrenCollapsible(False)

        self.part_panel = PartTreePanel(self)
        self.motion_panel = MotionTreePanel(self)
        self.param_panel = ParameterPanel(self)
        self.merge_panel = MergePanel(self)
        self.click_panel = ClickPanel(self)
        self.inspector = InspectorPanel(self)

        self.left_area = ToolWindowArea(
            "left", integrated_stripe=False, parent=self
        )
        self.left_area.setMinimumWidth(300)
        self.left_area.add_window(
            "part",
            "图层 / Part",
            TOOL_WINDOW_ICONS["part"],
            self.part_panel,
            "Alt+1",
        )
        self.left_area.add_window(
            "motion",
            "Motion 动作",
            TOOL_WINDOW_ICONS["motion"],
            self.motion_panel,
            "Alt+2",
        )

        self.bottom_area = ToolWindowArea(
            "bottom", integrated_stripe=False, parent=self
        )
        self.bottom_area.add_window(
            "params",
            "Live2D 参数",
            TOOL_WINDOW_ICONS["params"],
            self.param_panel,
            "Alt+3",
        )
        self.bottom_area.add_window(
            "merge",
            "合并动作（按帧采样）",
            TOOL_WINDOW_ICONS["merge"],
            self.merge_panel,
            "Alt+4",
        )
        self.bottom_area.add_window(
            "click",
            "点击绑定",
            TOOL_WINDOW_ICONS["click"],
            self.click_panel,
            "Alt+5",
        )

        self.right_area = ToolWindowArea(
            "right", integrated_stripe=False, parent=self
        )
        self.right_area.setMaximumWidth(420)
        self.right_area.add_window(
            "inspector",
            "属性",
            TOOL_WINDOW_ICONS["inspector"],
            self.inspector,
            "Alt+7",
        )

        self.tool_areas = {
            "left": self.left_area,
            "right": self.right_area,
            "bottom": self.bottom_area,
        }
        self._build_tool_navigation()
        self.right_rail = self._build_right_rail()

        preview = self._build_preview_panel()

        self.horizontal_splitter.addWidget(self.left_area)
        self.horizontal_splitter.addWidget(preview)
        self.horizontal_splitter.addWidget(self.right_area)
        self.horizontal_splitter.setStretchFactor(0, 0)
        self.horizontal_splitter.setStretchFactor(1, 1)
        self.horizontal_splitter.setStretchFactor(2, 0)
        self.horizontal_splitter.setSizes([270, 880, 300])

        self.center_splitter = StudioSplitter(Qt.Orientation.Vertical, self)
        self.center_splitter.setChildrenCollapsible(False)
        self.center_splitter.addWidget(self.horizontal_splitter)
        self.center_splitter.addWidget(self.bottom_area)
        self.center_splitter.setStretchFactor(0, 1)
        self.center_splitter.setStretchFactor(1, 0)
        self.center_splitter.setSizes([700, BOTTOM_COLLAPSED])

        self.left_area.visibilityChanged.connect(
            self._on_left_visibility_changed
        )
        self.right_area.visibilityChanged.connect(
            self._on_right_visibility_changed
        )
        self.bottom_area.visibilityChanged.connect(
            self._on_bottom_visibility_changed
        )
        for area in self.tool_areas.values():
            area.activeChanged.connect(self._sync_tool_window_actions)
            area.visibilityChanged.connect(self._sync_tool_window_actions)

        self._connect_panels()
        return self.center_splitter

    def _build_tool_navigation(self) -> None:
        titles = {key: text for key, text, _ in TOOL_WINDOWS}
        shortcuts = {
            key: f"{text} ({shortcut})" for key, text, shortcut in TOOL_WINDOWS
        }
        # addItem 传 tooltip 会让库额外装 NavigationToolTipFilter，弹出的是没做窗口内定位的顶层提示
        for key in ("part", "motion"):
            item = self.navigationInterface.addItem(
                key,
                TOOL_WINDOW_ICONS[key],
                titles[key],
                onClick=lambda _checked=False, k=key: self._toggle_tool_window(k),
            )
            item.setToolTip(shortcuts[key])

        self._rail_buttons: dict[str, NavigationPushButton] = {}
        for key in ("params", "merge", "click"):
            button = self._make_rail_button(key, self.navigationInterface.panel)
            self.navigationInterface.addWidget(
                key,
                button,
                onClick=lambda _checked=False, k=key: self._toggle_tool_window(k),
                position=NavigationItemPosition.BOTTOM,
            )
        self._nav_ready = True
        self._install_sidebar_logo()

    def _install_sidebar_logo(self) -> None:
        self.titleBar.iconLabel.hide()
        panel = self.navigationInterface.panel
        logo = QLabel(panel)
        logo.setFixedSize(panel.width(), NAV_TOP_MARGIN)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(24, 24))
        logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        logo.raise_()
        logo.show()
        self._sidebar_logo = logo

    def _build_right_rail(self) -> QWidget:
        rail = QWidget(self)
        rail.setFixedWidth(48)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(4, NAV_TOP_MARGIN, 4, NAV_BOTTOM_MARGIN)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        button = self._make_rail_button("inspector", rail)
        button.clicked.connect(
            lambda _checked=False: self._toggle_tool_window("inspector")
        )
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        return rail

    def _make_rail_button(self, key: str, parent: QWidget) -> NavigationPushButton:
        title = next(
            f"{text} ({shortcut})"
            for k, text, shortcut in TOOL_WINDOWS
            if k == key
        )
        button = NavigationPushButton(TOOL_WINDOW_ICONS[key], "", False, parent)
        button.setCompacted(True)
        button.setToolTip(title)
        self._rail_buttons[key] = button
        return button

    def _build_preview_panel(self) -> QWidget:
        self.preview_area = ToolWindowArea(
            "left", integrated_stripe=False, closable=False, parent=self
        )
        self.preview_area.add_header_widget(self._build_preview_actions())

        self.preview_stack = QStackedWidget()
        placeholder = QLabel(
            "打开 .model3.json 加载模型",
            self.preview_stack,
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_qss = "color: {}; font-size: 15px; background: transparent;"
        apply_theme_qss(
            placeholder,
            placeholder_qss.format(COLORS["textMuted"][0]),
            placeholder_qss.format(COLORS["textMuted"][1]),
        )
        self.canvas = Live2DCanvas(self.preview_stack)
        hint = "拖动：视线跟随 · 滚轮：缩放 · 双击：适应窗口"
        for target in (self.preview_stack, placeholder, self.canvas):
            target.setToolTip(hint)
        self.preview_stack.addWidget(placeholder)
        self.preview_stack.addWidget(self.canvas)
        self.preview_stack.setCurrentWidget(placeholder)

        self.loading_overlay = _LoadingOverlay(self.preview_stack)
        self.loading_overlay.hide()

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(6, 6, 6, 6)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.preview_stack)

        self.preview_area.add_window(
            "preview", "未打开模型", FluentIcon.PEOPLE, body
        )
        self.preview_area.show_window("preview")
        return self.preview_area

    def _build_status_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName("studioStatusBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(26)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)
        self.status_label = CaptionLabel("未打开模型", bar)
        self.edit_label = CaptionLabel("", bar)
        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.edit_label)
        return bar

    def _connect_panels(self) -> None:
        self.part_panel.partSelected.connect(self._on_part_selected)
        self.part_panel.meshSelected.connect(self._on_mesh_selected)
        self.part_panel.visibilityChanged.connect(self._on_visibility_changed)
        self.part_panel.meshVisibilityChanged.connect(
            self._on_mesh_visibility_changed
        )
        self.part_panel.bulkChanged.connect(self._on_bulk_visibility_changed)

        self.motion_panel.playRequested.connect(self._play_motion)
        self.motion_panel.stopRequested.connect(self._stop_motion)
        self.motion_panel.mergeRequested.connect(self._add_motion_to_merge)
        self.motion_panel.soundChanged.connect(self.show_message)

        self.inspector.hideToggled.connect(self._on_inspector_hide)
        self.inspector.deepToggled.connect(self._on_inspector_deep)
        self.inspector.parentRequested.connect(self.part_panel.select_part)

        self.merge_panel.statusMessage.connect(self.show_message)
        self.click_panel.statusMessage.connect(self.show_message)
        self.click_panel.saved.connect(self._on_click_bindings_saved)

        self.param_panel.parameterChanged.connect(self._on_parameter_changed)
        self.param_panel.defaultsChanged.connect(self._on_parameter_defaults_changed)
        self.param_panel.overridesCleared.connect(
            self.canvas.clear_param_overrides
        )
        self.param_panel.statusMessage.connect(self.show_message)

        self.canvas.part_hit.connect(self._on_canvas_part_hit)
        self.canvas.drawable_hit.connect(self._on_canvas_drawable_hit)
        self.canvas.binding_triggered.connect(self._on_binding_triggered)
        self.canvas.motion_started.connect(self._on_motion_started)
        self.canvas.motion_finished.connect(self._on_motion_finished)
        self.canvas.model_loaded.connect(self._on_model_loaded)
        self.canvas.load_failed.connect(self._on_load_failed)

    def _set_loading(self, loading: bool) -> None:
        if loading:
            self.loading_overlay.setGeometry(self.preview_stack.rect())
            self.loading_overlay.raise_()
        self.loading_overlay.setVisible(loading)
        if loading != self._loading:
            self._loading = loading
            if loading:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            else:
                QApplication.restoreOverrideCursor()
        if loading:
            # 先画出一帧，避免遮罩等到加载结束才出现
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
            )

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Live2D 模型",
            self._last_dir,
            "Live2D 模型 (*.model3.json);;JSON 文件 (*.json);;所有文件 (*)",
        )
        if file_name:
            self.load_model(Path(file_name))

    def load_model(self, path: str | Path) -> bool:
        model3_path = Path(path).expanduser().resolve()
        self._stop_motion_sound()
        if not self._confirm_discard():
            return False
        self._set_loading(True)
        try:
            document = Moc3Document.from_model3(model3_path)
        except (Moc3Error, OSError) as exc:
            self._set_loading(False)
            show_error(self, "打开模型失败", str(exc))
            return False

        self.document = document
        self._last_dir = str(model3_path.parent)
        self._selected_part = None
        self._selected_mesh = None

        try:
            entries = motion_entries(model3_path)
        except Exception:
            entries = []

        self.part_panel.set_document(document)
        self.motion_panel.set_motions(entries)
        self.motion_panel.set_model3_path(model3_path)
        self.motion_panel.set_model_available(False)
        self.inspector.set_document(document)
        self.click_panel.set_document(document)
        self.param_panel.set_document(document)
        self.param_panel.set_default_dir(model3_path.parent)
        self.merge_panel.set_default_dir(model3_path.parent)

        self.canvas.clear_param_overrides()
        self.canvas.set_hidden_parts(document.pending_hidden)
        self.preview_stack.setCurrentWidget(self.canvas)
        self.canvas.load_model(model3_path)
        self._refresh_click_bindings()
        self.canvas.set_click_preview_enabled(
            self.click_preview_action.isChecked()
        )

        self.preview_area.set_title(
            f"{document.model3_name()} · MOC3 {document.header.version_name}"
        )
        self._base_status = document.summary()
        self._update_actions()
        self._refresh_edit_label()
        self.show_message("模型已加载，正在准备预览", 3000)
        return True

    def _on_model_loaded(self) -> None:
        self._set_loading(False)
        if self.document is not None:
            self.canvas.set_hidden_parts(self.document.pending_hidden)
        self.motion_panel.set_model_available(True)
        self._refresh_highlight()
        self._update_actions()
        self.show_message("预览已就绪", 2500)

    def _on_load_failed(self, message: str) -> None:
        self._set_loading(False)
        self.motion_panel.set_model_available(False)
        self._update_actions()
        show_error(self, "预览加载失败", message)
        self.show_message("预览加载失败", 5000)

    def _confirm_discard(self) -> bool:
        document = self.document
        if document is None or not document.dirty:
            return True
        action = ask_save_changes(
            self,
            "有未写入的修改",
            "当前 Part 可见性 / 参数默认值修改尚未写入原 .moc3 文件\n\n"
            "选择“保存”先写入原文件，选择“不保存”放弃这些修改",
        )
        if action == "cancel":
            return False
        if action == "save":
            return self.apply_changes()
        document.reset_pending()
        return True

    def _on_part_selected(self, index: int) -> None:
        if self.document is None:
            return
        self._selected_part = index
        self._selected_mesh = None
        self.inspector.show_part(index)
        self.click_panel.set_selected_mesh(None)
        mesh_ids = [
            self.document.art_meshes[mesh_index].id
            for mesh_index in self.document.part_mesh_indices.get(index, ())
        ]
        self.canvas.highlight_part(mesh_ids, self.document.part_name(index))

    def _on_mesh_selected(self, index: int) -> None:
        if self.document is None:
            return
        self._selected_mesh = index
        self._selected_part = None
        mesh = self.document.art_meshes[index]
        self.inspector.show_mesh(index)
        self.click_panel.set_selected_mesh(mesh.id)
        self.canvas.highlight_mesh(mesh.id)

    def _on_visibility_changed(self, index: int, hidden: bool) -> None:
        document = self.document
        if document is None:
            return
        deep = self.part_panel.deep_hide_enabled() if hidden else False
        document.toggle_part(index, hidden, deep=deep)
        self._after_document_changed()

    def _on_mesh_visibility_changed(self, index: int, hidden: bool) -> None:
        document = self.document
        if document is None:
            return
        document.toggle_mesh(index, hidden)
        self._after_document_changed()

    def _on_bulk_visibility_changed(self) -> None:
        self._after_document_changed()

    def _on_inspector_hide(self, index: int, hidden: bool) -> None:
        document = self.document
        if document is None:
            return
        deep = self.part_panel.deep_hide_enabled() if hidden else False
        document.toggle_part(index, hidden, deep=deep)
        self.part_panel.refresh_visibility()
        self._after_document_changed()

    def _on_inspector_deep(self, index: int, deep: bool) -> None:
        document = self.document
        if document is None:
            return
        document.set_part_deep(index, deep)
        self.part_panel.refresh_visibility()
        self._after_document_changed()

    def _after_document_changed(self) -> None:
        document = self.document
        if document is None:
            return
        self.part_panel.refresh_visibility()
        self._sync_canvas_hidden()
        self.param_panel.refresh_defaults()
        if self._selected_part is not None:
            self.inspector.show_part(self._selected_part)
        elif self._selected_mesh is not None:
            self.inspector.show_mesh(self._selected_mesh)
        self._update_actions()
        self._refresh_edit_label()
        self._refresh_highlight()

    def _sync_canvas_hidden(self) -> None:
        if self.document is not None:
            self.canvas.set_hidden_parts(self.document.pending_hidden)

    def _refresh_highlight(self) -> None:
        document = self.document
        if document is None:
            self.canvas.clear_highlight()
            return
        if self._selected_mesh is not None:
            mesh = document.art_meshes[self._selected_mesh]
            self.canvas.highlight_mesh(mesh.id)
        elif self._selected_part is not None:
            mesh_ids = [
                document.art_meshes[mesh_index].id
                for mesh_index in document.part_mesh_indices.get(
                    self._selected_part, ()
                )
            ]
            self.canvas.highlight_part(
                mesh_ids, document.part_name(self._selected_part)
            )
        else:
            self.canvas.clear_highlight()

    def _on_parameter_changed(self, parameter_id: str, value: float) -> None:
        self.canvas.set_param_override(parameter_id, value)

    def _on_parameter_defaults_changed(self) -> None:
        self._update_actions()
        self._refresh_edit_label()

    def _on_canvas_part_hit(self, part_id: str) -> None:
        if self.document is None:
            return
        part = self.document.part_by_id.get(part_id)
        if part is not None:
            self.part_panel.select_part(part.index)

    def _on_canvas_drawable_hit(self, drawable_id: str) -> None:
        if self.document is None:
            return
        mesh = self.document.mesh_by_id.get(drawable_id)
        if mesh is not None:
            self.part_panel.select_mesh(mesh.index)

    def _play_motion(self, group: str, number: int) -> None:
        try:
            self.canvas.play_motion(group, number)
        except Exception as exc:
            show_error(self, "动作播放失败", str(exc))
            return
        self.motion_panel.set_playing(group, number)
        self.show_message(f"播放动作：{group} / #{number}", 3000)

    def _play_selected_motion(self) -> None:
        self.motion_panel.play_selected()

    def _stop_motion(self) -> None:
        self.canvas.stop_motions()
        self._stop_motion_sound()
        self.motion_panel.clear_playing()
        self.show_message("已停止播放", 2500)

    def _on_motion_started(self, group: str, number: int) -> None:
        self._stop_motion_sound()
        entry = self.motion_panel.entry_at(group, number)
        if (
                entry is None
                or not entry.sound
                or self.document is None
                or self.document.model3_path is None
        ):
            return
        sound_path = (self.document.model3_path.parent / entry.sound).resolve()
        if not sound_path.is_file():
            self.show_message(f"声音文件不存在：{entry.sound}", 4000)
            return
        self._motion_player.setSource(QUrl.fromLocalFile(str(sound_path)))
        self._motion_player.play()

    def _stop_motion_sound(self) -> None:
        self._motion_player.stop()

    def _on_motion_finished(self, group: str, number: int) -> None:
        self.motion_panel.clear_playing()
        self.show_message(f"动作播放完毕：{group} / #{number}", 3000)

    def _add_motion_to_merge(self, group: str, number: int) -> None:
        entry = self.motion_panel.entry_at(group, number)
        if entry is None:
            return
        if self.merge_panel.add_entry(entry):
            self._show_bottom_tab("merge")
            self.show_message(f"已加入合并列表：{entry.display_name}", 3000)

    def _refresh_click_bindings(self) -> None:
        document = self.document
        if document is None or document.model3_path is None:
            self.canvas.set_click_bindings([])
            return
        try:
            data = read_model3(document.model3_path)
            groups = motion_groups(data)
            bindings = read_hit_areas(data)
        except Exception:
            bindings = []
            groups = {}
        pairs = [
            (binding.name, binding.motion_group or binding.name)
            for binding in bindings
            if binding.name and (binding.motion_group or binding.name) in groups
        ]
        self.canvas.set_click_bindings(pairs)

    def _on_click_bindings_saved(self) -> None:
        self._refresh_click_bindings()
        self.canvas.reload_model()
        self.show_message("点击绑定已保存，正在重新加载预览", 4000)

    def _on_binding_triggered(self, area: str, group: str) -> None:
        self.show_message(f"点击 {area} → {group}", 2500)

    def _set_click_preview(self, enabled: bool) -> None:
        self.canvas.set_click_preview_enabled(enabled)
        self._sync_click_preview_ui(enabled)
        self._refresh_highlight()
        self.show_message(
            "点击动作预览已开启" if enabled else "点击动作预览已关闭",
            2500,
        )

    def _sync_click_preview_ui(self, enabled: bool) -> None:
        self.click_preview_button.setChecked(enabled)
        self.click_preview_badge.setText("已启用区域触发" if enabled else "已禁用区域触发")
        self.click_preview_badge.setLevel(
            InfoLevel.SUCCESS if enabled else InfoLevel.INFOAMTION
        )

    def apply_changes(self) -> bool:
        document = self.document
        if document is None:
            return False
        if not document.dirty:
            self.show_message("没有需要写入的修改", 3000)
            return True

        deep_note = (
            f"深度隐藏：{len(document.pending_deep)} 个 Part\n"
            if document.pending_deep
            else ""
        )
        mesh_note = (
            f"单独隐藏：{len(document.pending_mesh_hidden)} 个未归属 ArtMesh\n"
            if document.pending_mesh_hidden
            else ""
        )
        default_note = (
            f"变更参数默认值：{len(document.changed_parameter_ids)} 个\n"
            if document.changed_parameter_ids
            else ""
        )
        box = MessageBox(
            "写入原文件",
            "即将修改原 .moc3 文件：\n\n"
            f"{document.path}\n\n"
            f"变更 Part 数：{len(document.changed_part_indices)}\n"
            f"变更 Mesh 数：{len(document.changed_mesh_indices)}\n"
            f"{deep_note}{mesh_note}{default_note}"
            f"文件大小保持：{document.header.size:,} 字节\n\n"
            "同目录会自动生成 .bak-时间戳 备份\n"
            "建议先关闭正在使用该模型的应用继续吗？",
            self,
        )
        if box.exec() != QDialog.DialogCode.Accepted:
            return False

        deep_changed = document.pending_deep != document.saved_deep
        mesh_changed = document.pending_mesh_hidden != document.saved_mesh_hidden
        try:
            backup = document.apply()
        except PatchError as exc:
            if "已被其他程序修改" not in str(exc):
                show_error(self, "写入失败", f"{exc}\n\n原文件未被修改")
                return False
            box = MessageBox(
                "文件已被外部修改",
                "原文件在打开后发生了变化\n\n"
                "是否重新读取磁盘上的文件，再应用当前勾选并重试？",
                self,
            )
            if box.exec() != QDialog.DialogCode.Accepted:
                return False
            try:
                document.reload_from_disk(keep_pending=True)
            except (PatchError, OSError) as reload_exc:
                show_error(self, "重新读取失败", str(reload_exc))
                return False
            self.part_panel.refresh_visibility()
            self._sync_canvas_hidden()
            try:
                backup = document.apply()
            except (PatchError, OSError) as retry_exc:
                show_error(
                    self, "重试写入失败", f"{retry_exc}\n\n原文件未被修改"
                )
                return False
        except OSError as exc:
            show_error(self, "写入失败", f"{exc}\n\n原文件未被修改")
            return False

        consistency = self.canvas.check_consistency(document.path)
        if consistency is False:
            try:
                rollback = document.restore_original()
                detail = f"已回滚到打开时状态。\n回滚备份：{rollback}"
            except (PatchError, OSError) as exc:
                detail = f"自动回滚失败：{exc}\n请手动恢复：{backup}"
            show_error(
                self,
                "模型一致性校验失败",
                "Cubism Core 认为写入后的 MOC3 不一致，已阻止继续使用。\n\n"
                + detail,
            )
            self.part_panel.refresh_visibility()
            self._sync_canvas_hidden()
            self._after_document_changed()
            return False

        self.part_panel.refresh_visibility()
        self._after_document_changed()
        if deep_changed or mesh_changed:
            # ArtMesh opacity keyforms cannot be changed at runtime through
            # SetPartOpacity, so reload the preview from the written file.
            self.canvas.reload_model()
        self.show_message(f"已写入 {document.path.name} · 备份 {backup.name}", 6000)
        return True

    def undo_changes(self) -> None:
        document = self.document
        if document is None:
            return
        if document.dirty:
            document.reset_pending()
            self.part_panel.refresh_visibility()
            self._after_document_changed()
            self.show_message("已撤销未写入的修改", 3000)
            return
        if document.file_changed:
            self.restore_file()
            return
        self.show_message("没有可撤销的修改", 3000)

    def restore_file(self) -> None:
        document = self.document
        if document is None:
            return
        if not document.file_changed:
            self.show_message("原文件与打开时一致，无需恢复", 3000)
            return
        box = MessageBox(
            "恢复到打开时状态",
            "这会把原文件恢复到本次打开时捕获的内容，\n"
            "并撤销本次会话中已经写入的所有修改。\n\n继续吗？",
            self,
        )
        if box.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup = document.restore_original()
        except (PatchError, OSError) as exc:
            show_error(self, "恢复失败", str(exc))
            return
        self.part_panel.refresh_visibility()
        self._after_document_changed()
        self.show_message(f"已恢复原文件 · 备份 {backup.name if backup else '—'}", 4000)

    def _cleanup_backups(self) -> None:
        document = self.document
        if document is None or document.model3_path is None:
            return
        dialog = BackupCleanupDialog(document.model3_path.parent, self)
        dialog.exec()
        if dialog.deleted:
            self.show_message(f"已删除 {dialog.deleted} 个备份文件", 4000)

    def reload_preview(self) -> None:
        if self.document is None:
            return
        self._sync_canvas_hidden()
        self.canvas.reload_model()
        self.show_message("正在重新加载预览", 2500)

    def _reset_view(self) -> None:
        self.canvas.reset_view()

    def _sync_theme_actions(self) -> None:
        current = self._config.get("appearance.theme", "dark")
        action = self.theme_actions.get(current) or self.theme_actions["dark"]
        action.setChecked(True)

    def _set_theme(self, name: str) -> None:
        theme_name = apply_studio_theme(name)
        self._config.set("appearance.theme", theme_name)
        self._config.save()
        self._sync_theme_actions()
        self.show_message(f"主题：{dict(THEME_OPTIONS)[theme_name]}", 2500)

    def _toggle_tool_window(self, key: str) -> None:
        area = self._area_for(key)
        if area is not None:
            area.toggle_window(key)
        self._sync_tool_window_actions()

    def _sync_tool_window_actions(self, *_args) -> None:
        for key, action in self.tool_window_actions.items():
            area = self._area_for(key)
            action.setChecked(
                bool(
                    area
                    and area.is_window_visible()
                    and area.active_key() == key
                )
            )
        if self._nav_ready:
            if self.left_area.is_window_visible():
                key = self.left_area.active_key()
                if key:
                    self.navigationInterface.setCurrentItem(key)
            # 侧栏按钮不是导航项，只能直接改绘制状态，避免抢占左侧导航的单选游标
            for key, button in self._rail_buttons.items():
                area = self._area_for(key)
                selected = bool(
                    area
                    and area.is_window_visible()
                    and area.active_key() == key
                )
                if button.isSelected != selected:
                    button.isSelected = selected
                    button.update()

    def _area_for(self, key: str) -> ToolWindowArea | None:
        for area in self.tool_areas.values():
            if key in area.window_keys():
                return area
        return None

    def _on_left_visibility_changed(self, visible: bool) -> None:
        sizes = self.horizontal_splitter.sizes()
        if len(sizes) != 3:
            return
        total = sum(sizes)
        if total <= 0:
            return
        if not visible:
            self._left_width = max(200, sizes[0])
            left = LEFT_COLLAPSED
        else:
            left = self._left_width
        self.left_area.setVisible(visible)
        right = (
            sizes[2]
            if self.right_area.is_window_visible()
            else RIGHT_COLLAPSED
        )
        self.horizontal_splitter.setSizes(self._fit_horizontal(total, left, right))

    def _on_right_visibility_changed(self, visible: bool) -> None:
        sizes = self.horizontal_splitter.sizes()
        if len(sizes) != 3:
            return
        total = sum(sizes)
        if total <= 0:
            return
        if not visible:
            if sizes[2] > 0:
                self._right_width = max(200, sizes[2])
            right = RIGHT_COLLAPSED
        else:
            right = self._right_width
        # 只把宽度收成 0 的话 splitter 右侧还留着一根手柄，预览卡和底部面板对不齐
        self.right_area.setVisible(visible)
        left = (
            sizes[0]
            if self.left_area.is_window_visible()
            else LEFT_COLLAPSED
        )
        self.horizontal_splitter.setSizes(self._fit_horizontal(total, left, right))

    def _on_bottom_visibility_changed(self, visible: bool) -> None:
        sizes = self.center_splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        if not visible:
            self._bottom_height = max(160, sizes[1])
            bottom = BOTTOM_COLLAPSED
        else:
            bottom = self._bottom_height
        preview = total - bottom
        if preview < 200:
            bottom = max(BOTTOM_COLLAPSED, total - 200)
            preview = total - bottom
        self.center_splitter.setSizes([preview, bottom])

    def _fit_horizontal(self, total: int, left: int, right: int) -> list[int]:
        left = max(LEFT_COLLAPSED, left)
        right = max(RIGHT_COLLAPSED, right)
        if total - left - right < 300:
            deficit = 300 - (total - left - right)
            take = min(deficit, max(0, right - 200))
            right -= take
            deficit -= take
            take = min(deficit, max(0, left - 200))
            left -= take
        center = total - left - right
        if center < 300:
            right = max(RIGHT_COLLAPSED, total - left - 300)
            center = total - left - right
        if center < 0:
            left = LEFT_COLLAPSED
            center = max(0, total - right)
        return [left, center, right]

    def _compact_hidden_areas(self) -> None:
        self.left_area.setVisible(self.left_area.is_window_visible())
        self.right_area.setVisible(self.right_area.is_window_visible())
        sizes = self.horizontal_splitter.sizes()
        if len(sizes) == 3:
            total = sum(sizes)
            if total > 0:
                left = (
                    sizes[0]
                    if self.left_area.is_window_visible()
                    else LEFT_COLLAPSED
                )
                right = (
                    sizes[2]
                    if self.right_area.is_window_visible()
                    else RIGHT_COLLAPSED
                )
                self.horizontal_splitter.setSizes(
                    self._fit_horizontal(total, left, right)
                )
        sizes = self.center_splitter.sizes()
        if len(sizes) == 2:
            total = sum(sizes)
            if total > 0 and not self.bottom_area.is_window_visible():
                self.center_splitter.setSizes([total, BOTTOM_COLLAPSED])

    def _show_bottom_tab(self, route_key: str) -> None:
        self.bottom_area.show_window(route_key)

    def _update_actions(self) -> None:
        document = self.document
        has_doc = document is not None
        model_ready = self.canvas.model is not None
        dirty = bool(document and document.dirty)
        changed = bool(document and (document.dirty or document.file_changed))

        self.save_action.setEnabled(dirty)
        self.undo_action.setEnabled(changed)
        self.restore_action.setEnabled(bool(document and document.file_changed))
        self.cleanup_action.setEnabled(
            bool(document and document.model3_path is not None)
        )
        self.model_info_action.setEnabled(has_doc)
        self.reload_action.setEnabled(has_doc)
        self.reset_view_action.setEnabled(has_doc)
        self.play_action.setEnabled(model_ready)
        self.stop_action.setEnabled(model_ready)
        self.click_preview_action.setEnabled(model_ready)

        self.reload_button.setEnabled(has_doc)
        self.reset_button.setEnabled(has_doc)

    def _refresh_edit_label(self) -> None:
        document = self.document
        if document is None:
            self.edit_label.setText("")
            return
        deep = f" · 深度 {len(document.pending_deep)}" if document.pending_deep else ""
        mesh = (
            f" · Mesh {len(document.pending_mesh_hidden)}"
            if document.pending_mesh_hidden
            else ""
        )
        defaults = (
            f" · 默认值 {len(document.changed_parameter_ids)}"
            if document.changed_parameter_ids
            else ""
        )
        if document.dirty:
            state = (
                f"未保存 · 隐藏 {document.hidden_count}/{document.part_count}"
                f"{deep}{mesh}{defaults}"
            )
        elif document.file_changed:
            state = (
                f"已写入 · 隐藏 {document.hidden_count}/{document.part_count}"
                f"{deep}{mesh}{defaults}"
            )
        else:
            state = (
                f"未修改 · 隐藏 {document.hidden_count}/{document.part_count}"
                f"{deep}{mesh}{defaults}"
            )
        self.edit_label.setText(state)

    def show_message(self, text: str, timeout: int = 4000) -> None:
        self.status_label.setText(text)
        self._status_timer.start(max(500, timeout))

    def _restore_status(self) -> None:
        self.status_label.setText(self._base_status)

    def _show_model_info(self) -> None:
        document = self.document
        if document is None:
            return
        dialog = getattr(self, "_model_info_dialog", None)
        if dialog is None:
            dialog = ModelInfoDialog(self)
            self._model_info_dialog = dialog
        dialog.set_info(read_model_info(document), document.model3_name())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _check_updates(self) -> None:
        if self._update_reply is not None:
            return
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(
            b"User-Agent", f"Live2D-Studio/{__version__}".encode()
        )
        request.setTransferTimeout(10000)

        self.check_update_action.setEnabled(False)
        self.show_message("正在检查更新…", 10000)
        reply = self._network.get(request)
        self._update_reply = reply
        reply.finished.connect(lambda: self._finish_update_check(reply))

    def _finish_update_check(self, reply: QNetworkReply) -> None:
        if reply is not self._update_reply:
            reply.deleteLater()
            return
        self._update_reply = None
        self.check_update_action.setEnabled(True)

        raw = bytes(reply.readAll())
        detail = reply.errorString()
        failed = reply.error() != QNetworkReply.NetworkError.NoError
        reply.deleteLater()

        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            payload = None

        if failed:
            if isinstance(payload, dict) and payload.get("message"):
                detail = f"{payload['message']}（{detail}）"
            show_error(self, "检查更新失败", f"无法获取最新版本：{detail}")
            self.show_message("检查更新失败")
            return

        try:
            release = release_from_json(payload)
        except UpdateError as exc:
            show_error(self, "检查更新失败", str(exc))
            self.show_message("检查更新失败")
            return

        if is_newer(release.version, __version__):
            self.show_message(f"发现新版本 {release.tag}")
            show_update_available(self, __version__, release)
        else:
            self.show_message(f"已是最新版本 v{__version__}")
            show_up_to_date(self, __version__)

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _restore_layout(self) -> None:
        try:
            geometry = self._settings.value("window/geometry")
            if geometry:
                self.restoreGeometry(geometry)

            version = self._settings.value("window/layout_version", 0, type=int)
            if version == LAYOUT_VERSION:
                left_visible = self._settings.value(
                    "toolwindow/left/visible", True, type=bool
                )
                left_active = str(
                    self._settings.value("toolwindow/left/active", "part")
                )
                right_visible = self._settings.value(
                    "toolwindow/right/visible", True, type=bool
                )
                right_active = str(
                    self._settings.value("toolwindow/right/active", "inspector")
                )
                bottom_visible = self._settings.value(
                    "toolwindow/bottom/visible", False, type=bool
                )
                bottom_active = str(
                    self._settings.value("toolwindow/bottom/active", "params")
                )
                self._left_width = max(
                    200,
                    self._settings.value("window/left_width", 270, type=int),
                )
                self._right_width = max(
                    200,
                    self._settings.value("window/right_width", 300, type=int),
                )
                self._bottom_height = max(
                    160,
                    self._settings.value("window/bottom_height", 260, type=int),
                )
            else:
                left_visible, left_active = True, "part"
                right_visible, right_active = True, "inspector"
                bottom_visible, bottom_active = False, "params"
                self._left_width = 270
                self._right_width = 300
                self._bottom_height = 260

            areas = (self.left_area, self.right_area, self.bottom_area)
            for area in areas:
                area.blockSignals(True)
            try:
                self.left_area.set_active_window(left_active)
                self.right_area.set_active_window(right_active)
                self.bottom_area.set_active_window(bottom_active)
                for area, visible in (
                        (self.left_area, left_visible),
                        (self.right_area, right_visible),
                        (self.bottom_area, bottom_visible),
                ):
                    if visible:
                        area.show_window(area.active_key())
                    else:
                        area.hide_window()

                states = []
                if version == LAYOUT_VERSION:
                    for key, splitter in (
                            ("window/main_splitter", self.horizontal_splitter),
                            ("window/center_splitter", self.center_splitter),
                    ):
                        state = self._settings.value(key)
                        if state:
                            splitter.restoreState(state)
                        splitter.setHandleWidth(6)
                        states.append((splitter, state))
                self._startup_states = tuple(states) if states else None
                self._compact_hidden_areas()
            finally:
                for area in areas:
                    area.blockSignals(False)
            self._sync_tool_window_actions()
            # 构造期 splitter 尺寸尚未定型，首次布局后再套用一次
            QTimer.singleShot(0, self._apply_startup_layout)
        except Exception:
            pass

    def _apply_startup_layout(self) -> None:
        if self._startup_states:
            for splitter, state in self._startup_states:
                if state:
                    splitter.restoreState(state)
                splitter.setHandleWidth(6)
            self._startup_states = None
            self._compact_hidden_areas()
            self._sync_tool_window_actions()
            return
        self._apply_default_sizes()
        self._sync_tool_window_actions()

    def _apply_default_sizes(self) -> None:
        self.left_area.setVisible(self.left_area.is_window_visible())
        self.right_area.setVisible(self.right_area.is_window_visible())
        sizes = self.horizontal_splitter.sizes()
        if len(sizes) != 3:
            return
        total = sum(sizes)
        if total <= 0:
            return
        left = (
            self._left_width
            if self.left_area.is_window_visible()
            else LEFT_COLLAPSED
        )
        right = (
            self._right_width
            if self.right_area.is_window_visible()
            else RIGHT_COLLAPSED
        )
        self.horizontal_splitter.setSizes(self._fit_horizontal(total, left, right))

        sizes = self.center_splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        bottom = (
            min(
                self._bottom_height,
                max(BOTTOM_COLLAPSED, total - 200),
            )
            if self.bottom_area.is_window_visible()
            else BOTTOM_COLLAPSED
        )
        self.center_splitter.setSizes([total - bottom, bottom])

    def _save_layout(self) -> None:
        try:
            self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.setValue("window/layout_version", LAYOUT_VERSION)
            self._settings.setValue(
                "window/main_splitter", self.horizontal_splitter.saveState()
            )
            self._settings.setValue(
                "window/center_splitter", self.center_splitter.saveState()
            )

            for name, area in self.tool_areas.items():
                self._settings.setValue(
                    f"toolwindow/{name}/visible", area.is_window_visible()
                )
                self._settings.setValue(
                    f"toolwindow/{name}/active", area.active_key()
                )

            sizes = self.horizontal_splitter.sizes()
            if len(sizes) == 3:
                if self.left_area.is_window_visible():
                    self._left_width = sizes[0]
                if self.right_area.is_window_visible():
                    self._right_width = sizes[2]
            sizes = self.center_splitter.sizes()
            if len(sizes) == 2 and self.bottom_area.is_window_visible():
                self._bottom_height = sizes[1]

            self._settings.setValue("window/left_width", self._left_width)
            self._settings.setValue("window/right_width", self._right_width)
            self._settings.setValue("window/bottom_height", self._bottom_height)
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._update_reply is not None:
            reply, self._update_reply = self._update_reply, None
            reply.abort()
        if self._loading:
            self._loading = False
            QApplication.restoreOverrideCursor()
        self._stop_motion_sound()
        self.motion_panel.shutdown()
        self.canvas.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.document is not None and self.document.dirty:
            action = ask_save_changes(
                self,
                "退出前保存？",
                "当前有 Part 可见性修改尚未写入原文件。\n\n"
                "选择“保存”写入原文件后退出，选择“不保存”直接退出",
            )
            if action == "cancel":
                event.ignore()
                return
            if action == "save" and not self.apply_changes():
                event.ignore()
                return
        self._save_layout()
        self.shutdown()
        super().closeEvent(event)
