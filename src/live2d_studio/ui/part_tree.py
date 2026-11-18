"""Left-side Part / ArtMesh tree with visibility controls."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    SearchLineEdit,
    SwitchButton,
    TransparentPushButton,
    qconfig,
)

from .theme import CONTROL_HEIGHT, StudioTreeWidget, token
from ..core import ArtMeshInfo, Moc3Document, PartInfo

KIND_ROLE = Qt.ItemDataRole.UserRole
INDEX_ROLE = Qt.ItemDataRole.UserRole + 1
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2


class PartTreePanel(QWidget):
    """Part tree with checkboxes that drive the pending visibility edit set."""

    partSelected = Signal(int)
    meshSelected = Signal(int)
    visibilityChanged = Signal(int, bool)
    meshVisibilityChanged = Signal(int, bool)
    bulkChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document: Moc3Document | None = None
        self._part_items: dict[int, QTreeWidgetItem] = {}
        self._mesh_items: dict[int, QTreeWidgetItem] = {}
        self._updating = False
        # 节点前景色在构建时写入，换主题后要重刷一遍
        qconfig.themeChangedFinished.connect(self.refresh_styles)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.search_edit = SearchLineEdit(self)
        self.search_edit.setFixedHeight(CONTROL_HEIGHT)
        self.search_edit.setPlaceholderText("搜索 Part 或 ArtMesh ID")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.expand_button = TransparentPushButton("展开", self)
        self.expand_button.setToolTip("展开所有 Part / Mesh 节点")
        self.expand_button.clicked.connect(lambda: self.set_expanded(True))
        self.collapse_button = TransparentPushButton("折叠", self)
        self.collapse_button.setToolTip("折叠所有 Part / Mesh 节点")
        self.collapse_button.clicked.connect(lambda: self.set_expanded(False))
        self.show_all_button = TransparentPushButton("全显", self)
        self.show_all_button.setToolTip("显示所有 Part")
        self.show_all_button.clicked.connect(lambda: self.set_all_hidden(False))
        self.hide_all_button = TransparentPushButton("全隐", self)
        self.hide_all_button.setToolTip("隐藏所有 Part（遵循深度隐藏开关）")
        self.hide_all_button.clicked.connect(lambda: self.set_all_hidden(True))
        for button in (
                self.expand_button,
                self.collapse_button,
                self.show_all_button,
                self.hide_all_button,
        ):
            button.setFixedHeight(CONTROL_HEIGHT)
            tools.addWidget(button)
        tools.addStretch(1)
        layout.addLayout(tools)

        self.tree = StudioTreeWidget(self)
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree, 1)

        deep_row = QHBoxLayout()
        deep_row.setSpacing(6)
        self.deep_switch = SwitchButton(self)
        self.deep_switch.setChecked(True)
        self.deep_switch.setToolTip(
            "开启后会同时把该 Part 下所有 ArtMesh 的透明度关键帧清零"
        )
        deep_row.addWidget(self.deep_switch)
        deep_label = CaptionLabel("深度隐藏", self)
        deep_row.addWidget(deep_label, 1)
        layout.addLayout(deep_row)

        self.set_document(None)

    def set_document(self, document: Moc3Document | None) -> None:
        self.document = document
        self.tree.clear()
        self._part_items.clear()
        self._mesh_items.clear()
        enabled = document is not None
        for button in (
                self.expand_button,
                self.collapse_button,
                self.show_all_button,
                self.hide_all_button,
        ):
            button.setEnabled(enabled)
        self.search_edit.setEnabled(enabled)

        if document is None:
            return

        self._build_tree(document)
        for item in self._part_items.values():
            if item.parent() is None:
                item.setExpanded(True)

    def _build_tree(self, document: Moc3Document) -> None:
        meshes_by_part: dict[int, list[ArtMeshInfo]] = defaultdict(list)
        for mesh in document.art_meshes:
            meshes_by_part[mesh.parent_part_index].append(mesh)

        self._updating = True
        try:
            for root in document.roots:
                self._add_part_item(None, root, meshes_by_part, document)

            unparented = meshes_by_part.get(-1, [])
            if unparented:
                group = QTreeWidgetItem(
                    [f"未归属 Part 的 ArtMesh（{len(unparented)}）"]
                )
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                group.setForeground(0, QBrush(token("textMuted")))
                font = group.font(0)
                font.setItalic(True)
                group.setFont(0, font)
                group.setToolTip(
                    0,
                    "这些 ArtMesh 的 parent_part_indices 为 -1，没有 Part 控制其透明度。\n\n"
                    "可勾选单独隐藏，写入时会清零该 ArtMesh 的透明度关键帧，保存后生效",
                )
                self.tree.addTopLevelItem(group)
                for mesh in unparented:
                    self._add_mesh_item(group, mesh)
        finally:
            self._updating = False

        self.refresh_visibility()

    def _add_part_item(
            self,
            parent_item: QTreeWidgetItem | None,
            part: PartInfo,
            meshes_by_part: dict[int, list[ArtMeshInfo]],
            document: Moc3Document,
    ) -> None:
        display_name = document.part_name(part.index)
        item = QTreeWidgetItem([f"{display_name}  ({part.subtree_mesh_count})"])
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setData(0, KIND_ROLE, "part")
        item.setData(0, INDEX_ROLE, part.index)
        item.setData(0, SEARCH_ROLE, f"{display_name} {part.id}".lower())
        parent_name = (
            document.part_name(part.parent_index)
            if part.parent_index >= 0
            else "—（根 Part）"
        )
        item.setToolTip(
            0,
            f"显示名: {display_name}\n\n"
            f"Part ID: {part.id}\n\n"
            f"索引: {part.index}\n\n"
            f"父 Part: {parent_name}\n\n"
            f"直接 ArtMesh: {part.direct_mesh_count}\n\n"
            f"子树 ArtMesh: {part.subtree_mesh_count}",
        )
        self._style_part(item, document.effective_hidden(part.index), part.parent_index == -1)

        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        self._part_items[part.index] = item

        for child_index in part.child_indices:
            self._add_part_item(
                item, document.parts[child_index], meshes_by_part, document
            )
        for mesh in meshes_by_part.get(part.index, []):
            self._add_mesh_item(item, mesh)

    def _add_mesh_item(
            self, parent_item: QTreeWidgetItem, mesh: ArtMeshInfo
    ) -> None:
        item = QTreeWidgetItem([f"· {mesh.id}"])
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if mesh.parent_part_index == -1:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)
        item.setData(0, KIND_ROLE, "mesh")
        item.setData(0, INDEX_ROLE, mesh.index)
        item.setData(0, SEARCH_ROLE, mesh.id.lower())
        hide_note = (
            "\n可单独隐藏（写入后生效）" if mesh.parent_part_index == -1 else ""
        )
        item.setToolTip(
            0,
            f"ArtMesh ID: {mesh.id}\n\n"
            f"索引: {mesh.index}\n\n"
            f"父 Part 索引: {mesh.parent_part_index}\n\n"
            f"透明度关键帧: {mesh.keyform_count}{hide_note}",
        )
        parent_item.addChild(item)
        self._mesh_items[mesh.index] = item

    def _style_part(self, item: QTreeWidgetItem, hidden: bool, is_root: bool) -> None:
        item.setForeground(
            0, QBrush(token("textMuted") if hidden else token("text"))
        )
        font = item.font(0)
        font.setBold(is_root)
        font.setItalic(hidden)
        item.setFont(0, font)

    def _style_mesh(self, item: QTreeWidgetItem, hidden: bool) -> None:
        item.setForeground(
            0, QBrush(token("textMuted") if hidden else token("text"))
        )
        font = item.font(0)
        font.setItalic(hidden)
        item.setFont(0, font)

    def refresh_visibility(self) -> None:
        """Pull check states and styles from the document."""
        document = self.document
        if document is None:
            return
        self._updating = True
        try:
            for index, item in self._part_items.items():
                item.setCheckState(
                    0,
                    Qt.CheckState.Unchecked
                    if document.effective_hidden(index)
                    else Qt.CheckState.Checked,
                )
            for index, item in self._mesh_items.items():
                if not document.is_mesh_hideable(index):
                    continue
                item.setCheckState(
                    0,
                    Qt.CheckState.Unchecked
                    if document.is_mesh_hidden(index)
                    else Qt.CheckState.Checked,
                )
        finally:
            self._updating = False
        self.refresh_styles()

    def refresh_styles(self) -> None:
        document = self.document
        if document is None:
            return
        # Changing fonts/colors also emits itemChanged, so keep the guard on
        # to avoid feeding our own style updates back through visibilityChanged.
        self._updating = True
        try:
            for index, item in self._part_items.items():
                is_root = document.parts[index].parent_index == -1
                self._style_part(item, document.effective_hidden(index), is_root)
            for index, item in self._mesh_items.items():
                if document.is_mesh_hideable(index):
                    self._style_mesh(item, document.is_mesh_hidden(index))
        finally:
            self._updating = False

    def deep_hide_enabled(self) -> bool:
        return self.deep_switch.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        if expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    def set_all_hidden(self, hidden: bool) -> None:
        document = self.document
        if document is None:
            return
        deep = self.deep_hide_enabled() if hidden else False
        for index in range(document.part_count):
            document.toggle_part(index, hidden, deep=deep)
        self.refresh_visibility()
        self.bulkChanged.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0 or self.document is None:
            return
        kind = item.data(0, KIND_ROLE)
        index = int(item.data(0, INDEX_ROLE))
        hidden = item.checkState(0) == Qt.CheckState.Unchecked
        if kind == "part":
            self.visibilityChanged.emit(index, hidden)
        elif kind == "mesh" and self.document.is_mesh_hideable(index):
            self.meshVisibilityChanged.emit(index, hidden)

    def _on_current_changed(
            self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        if current is None or self.document is None:
            return
        kind = current.data(0, KIND_ROLE)
        if kind == "part":
            self.partSelected.emit(int(current.data(0, INDEX_ROLE)))
        elif kind == "mesh":
            self.meshSelected.emit(int(current.data(0, INDEX_ROLE)))

    def select_part(self, index: int) -> None:
        item = self._part_items.get(index)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter
        )
        self.tree.setFocus()

    def select_mesh(self, index: int) -> None:
        item = self._mesh_items.get(index)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter
        )
        self.tree.setFocus()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            haystack = str(item.data(0, SEARCH_ROLE) or item.text(0)).lower()
            own_match = needle in haystack
            child_match = False
            for i in range(item.childCount()):
                if visit(item.child(i)):
                    child_match = True
            visible = not needle or own_match or child_match
            item.setHidden(not visible)
            if needle and child_match:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
