"""Left-side motion group tree with playback and sound binding."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    TransparentToolButton,
    qconfig,
)

from .dialogs import show_error
from .theme import CONTROL_HEIGHT, StudioTreeWidget, token
from ..core import Model3Error, MotionEntry, set_motion_sound

KIND_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1
INDEX_ROLE = Qt.ItemDataRole.UserRole + 2
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 3


class MotionTreePanel(QWidget):
    """Motion groups from model3.json; double-click plays a motion."""

    playRequested = Signal(str, int)
    stopRequested = Signal()
    mergeRequested = Signal(str, int)
    soundChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: dict[tuple[str, int], MotionEntry] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._motion_items: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._playing: tuple[str, int] | None = None
        self.model3_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setFixedHeight(CONTROL_HEIGHT)
        self.search_edit.setPlaceholderText("搜索动作组或 motion 文件")
        self.search_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_edit, 1)
        self.count_label = CaptionLabel("未打开模型", self)
        search_row.addWidget(self.count_label)
        self.info_button = TransparentToolButton(FluentIcon.INFO, self)
        self.info_button.setFixedSize(24, 24)
        self.info_button.setToolTip(
            "双击 motion 即可播放\n\n"
            "选中 motion 单击下方“加入合并”按钮，即可加入待合并列表\n\n"
            "绑定声音是将音频文件的路径写入 model3.json 的 Motions[].Sound，建议在模型目录中新建 sounds 目录用于存放音频文件"
        )
        search_row.addWidget(self.info_button)
        layout.addLayout(search_row)

        self.tree = StudioTreeWidget(self)
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.tree, 1)

        self.status_label = CaptionLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.status_label.setVisible(False)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.play_button = PrimaryPushButton("播放", self)
        self.play_button.setFixedHeight(CONTROL_HEIGHT)
        self.play_button.clicked.connect(self.play_selected)
        self.stop_button = PushButton("停止", self)
        self.stop_button.setFixedHeight(CONTROL_HEIGHT)
        self.stop_button.clicked.connect(lambda: self.stopRequested.emit())
        self.merge_button = PrimaryPushButton("加入合并", self)
        self.merge_button.setFixedHeight(CONTROL_HEIGHT)
        self.merge_button.setToolTip("把选中的 motion 加入底部合并列表")
        self.merge_button.clicked.connect(self._add_to_merge)
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        controls.addWidget(self.merge_button)
        layout.addLayout(controls)

        sound_row = QHBoxLayout()
        sound_row.setSpacing(6)
        self.sound_label = CaptionLabel("声音：未设置", self)
        self.sound_label.setMaximumWidth(190)
        sound_row.addWidget(self.sound_label)
        sound_row.addStretch(1)
        self.sound_button = PrimaryPushButton("选择", self)
        self.sound_button.setFixedHeight(CONTROL_HEIGHT)
        self.sound_button.setToolTip(
            "为选中的 motion 选择声音，写入 model3.json 的 Motions[].Sound"
        )
        self.sound_button.clicked.connect(self.choose_sound)
        self.clear_sound_button = PrimaryPushButton("清除", self)
        self.clear_sound_button.setFixedHeight(CONTROL_HEIGHT)
        self.clear_sound_button.setToolTip("从 model3.json 移除该 motion 的 Sound")
        self.clear_sound_button.clicked.connect(self.clear_sound)
        sound_row.addWidget(self.sound_button)
        sound_row.addWidget(self.clear_sound_button)
        layout.addLayout(sound_row)

        self.set_motions([])
        self.set_model_available(False)
        # 节点前景色在构建时写入，换主题后要重刷一遍
        qconfig.themeChangedFinished.connect(self._refresh_colors)

    def _refresh_colors(self) -> None:
        for item in self._group_items.values():
            item.setForeground(0, QBrush(token("text")))
        for item in self._motion_items.values():
            item.setForeground(0, QBrush(token("textMuted")))

    def set_motions(self, entries: list[MotionEntry]) -> None:
        self.tree.clear()
        self._entries.clear()
        self._group_items.clear()
        self._motion_items.clear()
        self._playing = None

        grouped: dict[str, list[MotionEntry]] = {}
        for entry in entries:
            grouped.setdefault(entry.group, []).append(entry)

        self.tree.setUpdatesEnabled(False)
        try:
            for group, items in grouped.items():
                group_item = QTreeWidgetItem([f"{group}  ({len(items)})"])
                group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                group_item.setForeground(0, QBrush(token("text")))
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
                group_item.setData(0, KIND_ROLE, "group")
                group_item.setData(0, SEARCH_ROLE, group.lower())
                self.tree.addTopLevelItem(group_item)
                self._group_items[group] = group_item

                for entry in items:
                    item = QTreeWidgetItem([self._item_text(entry)])
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    )
                    item.setForeground(0, QBrush(token("textMuted")))
                    item.setData(0, KIND_ROLE, "motion")
                    item.setData(0, GROUP_ROLE, entry.group)
                    item.setData(0, INDEX_ROLE, entry.index)
                    item.setData(
                        0,
                        SEARCH_ROLE,
                        f"{entry.group} {entry.file} {entry.sound}".lower(),
                    )
                    item.setToolTip(0, self._item_tooltip(entry))
                    group_item.addChild(item)
                    self._entries[(entry.group, entry.index)] = entry
                    self._motion_items[(entry.group, entry.index)] = item
                group_item.setExpanded(True)
        finally:
            self.tree.setUpdatesEnabled(True)

        self.count_label.setText(
            f"{len(grouped)} 组 · {len(entries)} 个动作" if entries else "没有动作"
        )
        self._set_status("")
        self._update_buttons()

    def set_model3_path(self, path: str | Path | None) -> None:
        self.model3_path = Path(path).resolve() if path is not None else None
        self._update_buttons()

    def set_model_available(self, available: bool) -> None:
        self.play_button.setEnabled(available and bool(self._entries))
        self.stop_button.setEnabled(available and self._playing is not None)
        self.merge_button.setEnabled(available and bool(self._entries))
        self.tree.setEnabled(available)
        self._update_buttons()

    def set_playing(self, group: str, number: int) -> None:
        self._playing = (group, number)
        entry = self._entries.get((group, number))
        name = entry.display_name if entry else f"#{number}"
        self._set_status(f"正在播放：{group} / {name}")
        item = self._motion_items.get((group, number))
        if item is not None:
            self.tree.setCurrentItem(item)
        self._update_buttons()

    def clear_playing(self) -> None:
        self._playing = None
        self._set_status("")
        self._update_buttons()

    def current_entry(self) -> MotionEntry | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, KIND_ROLE) != "motion":
            return None
        key = (str(item.data(0, GROUP_ROLE)), int(item.data(0, INDEX_ROLE)))
        return self._entries.get(key)

    def play_selected(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        self.playRequested.emit(entry.group, entry.index)

    def _on_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.data(0, KIND_ROLE) != "motion":
            return
        self.playRequested.emit(
            str(item.data(0, GROUP_ROLE)), int(item.data(0, INDEX_ROLE))
        )

    def _add_to_merge(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        self.mergeRequested.emit(entry.group, entry.index)

    def choose_sound(self) -> None:
        """Pick an audio file and write it into the selected motion."""
        entry = self.current_entry()
        if entry is None or self.model3_path is None:
            return
        start = str(self.model3_path.parent)
        if entry.sound:
            candidate = self.model3_path.parent / entry.sound
            if candidate.is_file():
                start = str(candidate)
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "选择动作声音",
            start,
            "音频文件 (*.wav *.mp3 *.ogg *.m4a *.aac *.flac);;所有文件 (*)",
        )
        if not file_name:
            return
        self._write_sound(entry, Path(file_name))

    def clear_sound(self) -> None:
        entry = self.current_entry()
        if entry is None or self.model3_path is None or not entry.sound:
            return
        self._write_sound(entry, None)

    def _write_sound(self, entry: MotionEntry, source: Path | None) -> None:
        if self.model3_path is None:
            return
        try:
            reference, changed = set_motion_sound(
                self.model3_path, entry.group, entry.file, source
            )
        except (Model3Error, OSError) as exc:
            show_error(self.window(), "写入声音失败", str(exc))
            return

        updated = replace(entry, sound=reference)
        self._entries[(entry.group, entry.index)] = updated
        item = self._motion_items.get((entry.group, entry.index))
        if item is not None:
            item.setText(0, self._item_text(updated))
            item.setToolTip(0, self._item_tooltip(updated))
        self._update_buttons()

        if not changed:
            self._set_status("声音没有变化")
            return
        name = Path(reference).name if reference else "（已清除）"
        self._set_status(f"{entry.display_name} 的声音：{name}")
        self.soundChanged.emit(
            f"已更新声音：{entry.group} / {entry.display_name} → {name}"
        )

    @staticmethod
    def _item_text(entry: MotionEntry) -> str:
        return f"{entry.display_name}  ♪" if entry.sound else entry.display_name

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    @staticmethod
    def _item_tooltip(entry: MotionEntry) -> str:
        return (
                f"动作组: {entry.group}\n"
                f"序号: {entry.index}\n"
                f"文件: {entry.file}\n"
                f"声音: {entry.sound or '未设置'}\n"
                + (f"路径: {entry.path}" if entry.path else "文件缺失")
        )

    def entry_at(self, group: str, number: int) -> MotionEntry | None:
        return self._entries.get((group, number))

    def _update_buttons(self) -> None:
        entry = self.current_entry()
        has_entry = entry is not None
        self.play_button.setEnabled(
            self.tree.isEnabled() and has_entry
        )
        self.stop_button.setEnabled(self.tree.isEnabled() and self._playing is not None)
        self.merge_button.setEnabled(self.tree.isEnabled() and has_entry)
        writable = self.tree.isEnabled() and self.model3_path is not None
        self.sound_button.setEnabled(writable and has_entry)
        self.clear_sound_button.setEnabled(
            writable and has_entry and bool(entry.sound)
        )
        if entry is None:
            self.sound_label.setText("声音：—")
            self.sound_label.setToolTip("")
        elif entry.sound:
            self.sound_label.setText(f"声音：{Path(entry.sound).name}")
            self.sound_label.setToolTip(entry.sound)
        else:
            self.sound_label.setText("声音：未设置")
            self.sound_label.setToolTip("")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for group, group_item in self._group_items.items():
            own_match = needle in group.lower()
            child_match = False
            for i in range(group_item.childCount()):
                child = group_item.child(i)
                haystack = str(child.data(0, SEARCH_ROLE) or child.text(0)).lower()
                match = needle in haystack
                child.setHidden(bool(needle) and not match and not own_match)
                child_match = child_match or match
            group_item.setHidden(bool(needle) and not own_match and not child_match)
            if needle and (own_match or child_match):
                group_item.setExpanded(True)

    def shutdown(self) -> None:
        self.stopRequested.emit()
