"""Right-side property inspector for the selected Part or ArtMesh."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    SwitchButton, PrimaryPushButton,
)

from .theme import CONTROL_HEIGHT
from ..core import Moc3Document


class InspectorPanel(QWidget):
    """Shows the selected item and exposes Part visibility toggles."""

    hideToggled = Signal(int, bool)
    deepToggled = Signal(int, bool)
    parentRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document: Moc3Document | None = None
        self._selected_part: int | None = None
        self._selected_mesh: int | None = None
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.item_title = StrongBodyLabel("未选择图层", self)
        self.item_title.setWordWrap(True)
        layout.addWidget(self.item_title)

        self.item_path = CaptionLabel("", self)
        self.item_path.setWordWrap(True)
        self.item_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.item_path)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(6)
        self._values: dict[str, BodyLabel] = {}
        for key in ("索引", "父 Part", "直接 ArtMesh", "子树 ArtMesh", "当前状态"):
            value = BodyLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._values[key] = value
            self.form.addRow(CaptionLabel(key, self), value)
        layout.addLayout(self.form)

        hide_row = QHBoxLayout()
        self.hide_switch = SwitchButton(self)
        hide_row.addWidget(self.hide_switch)
        hide_row.addWidget(CaptionLabel("隐藏此 Part", self), 1)
        layout.addLayout(hide_row)
        self.hide_switch.checkedChanged.connect(self._on_hide_toggled)

        deep_row = QHBoxLayout()
        self.deep_switch = SwitchButton(self)
        deep_row.addWidget(self.deep_switch)
        deep_row.addWidget(CaptionLabel("深度隐藏", self), 1)
        layout.addLayout(deep_row)
        self.deep_switch.checkedChanged.connect(self._on_deep_toggled)

        self.parent_button = PrimaryPushButton("选中父 Part", self)
        self.parent_button.setFixedHeight(CONTROL_HEIGHT)
        self.parent_button.clicked.connect(self._on_parent_clicked)
        self.parent_button.hide()
        layout.addWidget(self.parent_button)

        layout.addStretch(1)

        self.clear()

    def set_document(self, document: Moc3Document | None) -> None:
        self.document = document
        self.clear()

    def clear(self) -> None:
        self._selected_part = None
        self._selected_mesh = None
        self._updating = True
        try:
            self.item_title.setText("未选择图层")
            self.item_path.setText("在左侧图层树中选择 Part 或 ArtMesh")
            self.item_path.setToolTip("")
            for value in self._values.values():
                value.setText("—")
            self.hide_switch.setChecked(False)
            self.hide_switch.setEnabled(False)
            self.hide_switch.setToolTip("")
            self.deep_switch.setChecked(False)
            self.deep_switch.setEnabled(False)
            self.deep_switch.setToolTip("")
            self.parent_button.hide()
        finally:
            self._updating = False

    def show_part(self, index: int) -> None:
        document = self.document
        if document is None or not 0 <= index < document.part_count:
            return
        part = document.parts[index]
        display_name = document.part_name(index)
        parent_name = (
            document.part_name(part.parent_index)
            if part.parent_index >= 0
            else "—（根）"
        )
        self._selected_part = index
        self._selected_mesh = None
        self.item_title.setText(display_name)
        self.item_path.setText(
            f"Part ID: {part.id} · 索引 {index} · 父级 {parent_name}"
        )
        self.item_path.setToolTip("")
        self._values["索引"].setText(str(index))
        self._values["父 Part"].setText(parent_name)
        self._values["直接 ArtMesh"].setText(str(part.direct_mesh_count))
        self._values["子树 ArtMesh"].setText(str(part.subtree_mesh_count))
        if document.is_hidden(index):
            state = "隐藏"
        elif document.effective_hidden(index):
            state = "随父级隐藏"
        else:
            state = "显示"
        self._values["当前状态"].setText(state)

        hidden = document.is_hidden(index)
        explicit_deep = index in document.pending_deep
        effective_deep = document.is_deep_hidden(index)
        inherited_deep = effective_deep and not explicit_deep

        self._updating = True
        try:
            self.hide_switch.setChecked(hidden)
            self.hide_switch.setEnabled(True)
            self.deep_switch.setChecked(effective_deep)
            self.deep_switch.setEnabled(
                hidden and not inherited_deep and part.subtree_mesh_count > 0
            )
        finally:
            self._updating = False

        self.parent_button.hide()
        if part.subtree_mesh_count == 0:
            self.deep_switch.setToolTip("这个 Part 没有 ArtMesh，可能只是分组节点")
        elif inherited_deep:
            self.deep_switch.setToolTip(
                "父 Part 已开启深度隐藏，当前 Part 随父级生效"
            )
        elif hidden and effective_deep:
            self.deep_switch.setToolTip(
                "写入后所有子网格透明度关键帧为 0，Motion/Pose 也无法恢复显示"
            )
        else:
            self.deep_switch.setToolTip(
                "即使 Motion 或 Pose 重新设置该 Part 的透明度，"
                "子网格的透明度关键帧仍为 0"
            )
        if hidden and not effective_deep:
            self.hide_switch.setToolTip(
                "仅修改 part.visibles，如果播放 Motion 后又出现，请开启深度隐藏"
            )
        else:
            self.hide_switch.setToolTip("")

    def show_mesh(self, index: int) -> None:
        document = self.document
        if document is None or not 0 <= index < document.art_mesh_count:
            return
        mesh = document.art_meshes[index]
        self._selected_mesh = index
        self._selected_part = None
        self.item_title.setText(mesh.id)
        self._values["索引"].setText(str(index))
        self._values["直接 ArtMesh"].setText("—")
        self._values["子树 ArtMesh"].setText("—")

        if mesh.parent_part_index >= 0:
            parent = document.parts[mesh.parent_part_index]
            parent_name = document.part_name(parent.index)
            self.item_path.setText(
                f"ArtMesh 索引 {index} · 父 Part {parent_name}（ID {parent.id}）"
            )
            self._values["父 Part"].setText(parent_name)
            self._values["当前状态"].setText(
                "随父 Part" if document.effective_hidden(parent.index) else "显示"
            )
            self.parent_button.show()
            self.parent_button.setToolTip(
                "ArtMesh 的可见性跟随父 Part，选中父 Part 可修改隐藏状态"
            )
            self.item_path.setToolTip("")
        else:
            self.item_path.setText(f"ArtMesh 索引 {index} · 未归属任何 Part")
            self.item_path.setToolTip("parent_part_indices 为 -1，不会随 Part 隐藏")
            self._values["父 Part"].setText("—（未归属）")
            self._values["当前状态"].setText("不受 Part 隐藏影响")
            self.parent_button.hide()

        self.hide_switch.setToolTip("")
        self.deep_switch.setToolTip("")

        self._updating = True
        try:
            self.hide_switch.setChecked(False)
            self.hide_switch.setEnabled(False)
            self.deep_switch.setChecked(False)
            self.deep_switch.setEnabled(False)
        finally:
            self._updating = False

    def _on_hide_toggled(self, checked: bool) -> None:
        if self._updating or self._selected_part is None:
            return
        self.hideToggled.emit(self._selected_part, checked)

    def _on_deep_toggled(self, checked: bool) -> None:
        if self._updating or self._selected_part is None:
            return
        self.deepToggled.emit(self._selected_part, checked)

    def _on_parent_clicked(self) -> None:
        document = self.document
        if document is None or self._selected_mesh is None:
            return
        parent = document.art_meshes[self._selected_mesh].parent_part_index
        if parent >= 0:
            self.parentRequested.emit(parent)
