"""Bottom editor for model3.json HitAreas click bindings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    FluentIcon,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
)

from .theme import CONTROL_HEIGHT, StudioTableWidget
from ..core import (
    HitAreaBinding,
    HitAreaError,
    Moc3Document,
    camel_to_snake,
    group_for_action,
    merge_bindings,
    motion_groups,
    read_hit_areas,
    read_model3,
    save_hit_areas,
    suggest_touch_bindings,
    validate_bindings,
)


class ClickPanel(QWidget):
    """Edit ``HitAreas`` without touching the MOC3 file."""

    saved = Signal()
    statusMessage = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document: Moc3Document | None = None
        self.model3_path: Path | None = None
        self.groups: dict[str, tuple[str, ...]] = {}
        self.mesh_ids: list[str] = []
        self.mesh_by_id = {}
        self.bindings: list[HitAreaBinding] = []
        self.selected_mesh_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self.table = StudioTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ArtMesh", "点击区域名", "动作组", "状态"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(96)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(CaptionLabel("动作组：", self))
        self.group_combo = ComboBox(self)
        self.group_combo.setFixedHeight(CONTROL_HEIGHT)
        self.group_combo.setMinimumWidth(180)
        self.group_combo.setToolTip("点击区域名会直接使用这个 motion group")
        controls.addWidget(self.group_combo)
        self.add_selected_button = PrimaryPushButton("添加选中网格", self)
        self.add_selected_button.setFixedHeight(CONTROL_HEIGHT)
        self.add_selected_button.setToolTip(
            "在左侧图层树中选中 ArtMesh，然后添加为点击区域"
        )
        self.add_selected_button.clicked.connect(self._add_selected_mesh)
        controls.addWidget(self.add_selected_button)
        self.scan_button = PrimaryPushButton("扫描 TouchMesh", self)
        self.scan_button.setFixedHeight(CONTROL_HEIGHT)
        self.scan_button.setToolTip(
            "扫描所有 Touch* ArtMesh，自动匹配同名 motion group"
        )
        self.scan_button.clicked.connect(self._scan_touch_meshes)
        controls.addWidget(self.scan_button)
        self.remove_button = PushButton("移除选中", self)
        self.remove_button.setFixedHeight(CONTROL_HEIGHT)
        self.remove_button.clicked.connect(self._remove_selected)
        controls.addWidget(self.remove_button)
        self.save_button = PrimaryPushButton("保存到 model3.json", self)
        self.save_button.setFixedHeight(CONTROL_HEIGHT)
        self.save_button.clicked.connect(self._save)
        controls.addWidget(self.save_button)
        self.info_button = TransparentToolButton(FluentIcon.INFO, self)
        self.info_button.setFixedSize(24, 24)
        self.info_button.setToolTip(
            "HitAreas[].Name 必须与 motion group 完全一致，只修改 model3.json"
        )
        controls.addWidget(self.info_button)
        controls.addStretch(1)
        self.status_label = CaptionLabel("", self)
        self.status_label.setWordWrap(True)
        controls.addWidget(self.status_label)

        layout.addLayout(controls)

        self.set_document(None)

    def set_document(self, document: Moc3Document | None) -> None:
        self.document = document
        self.model3_path = document.model3_path if document is not None else None
        self.selected_mesh_id = None
        self.group_combo.clear()
        if document is None or self.model3_path is None:
            self.mesh_ids = []
            self.mesh_by_id = {}
            self.groups = {}
            self.bindings = []
            self._refresh_table()
            self.status_label.setText("需要 model3.json 才能编辑点击绑定")
            return

        try:
            data = read_model3(self.model3_path)
        except Exception as exc:
            data = {}
            self.status_label.setText(f"读取 model3.json 失败：{exc}")
        self.groups = motion_groups(data)
        self.mesh_ids = [mesh.id for mesh in document.art_meshes]
        self.mesh_by_id = {mesh.id: mesh for mesh in document.art_meshes}
        self.bindings = read_hit_areas(data)
        self.group_combo.addItems(sorted(self.groups.keys()))
        self._refresh_table()

    def set_selected_mesh(self, mesh_id: str | None) -> None:
        self.selected_mesh_id = mesh_id
        if mesh_id and self.groups:
            action = camel_to_snake(mesh_id)
            preferred = group_for_action(action, self.groups) or action
            index = self.group_combo.findText(preferred)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
        self._update_buttons()

    def _refresh_table(self, select_row: int | None = None) -> None:
        self.table.setRowCount(len(self.bindings))
        for row, binding in enumerate(self.bindings):
            mesh = self.mesh_by_id.get(binding.mesh_id)
            mesh_text = binding.mesh_id
            parent_name = "未归属"
            if (
                    mesh is not None
                    and mesh.parent_part_index >= 0
                    and self.document is not None
            ):
                parent_name = self.document.part_name(mesh.parent_part_index)
                mesh_text += f"  ·  Part {parent_name}"
            mesh_item = QTableWidgetItem(mesh_text)
            mesh_item.setToolTip(f"ArtMesh: {binding.mesh_id}\n\n父 Part: {parent_name}")
            name_item = QTableWidgetItem(binding.name)
            group_item = QTableWidgetItem(binding.motion_group or "—")
            status_item = QTableWidgetItem(binding.status)
            for item in (mesh_item, name_item, group_item, status_item):
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
            self.table.setItem(row, 0, mesh_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, group_item)
            self.table.setItem(row, 3, status_item)
        if select_row is not None and 0 <= select_row < len(self.bindings):
            self.table.selectRow(select_row)
        self.status_label.setText(
            f"{len(self.bindings)} 条点击绑定"
            if self.model3_path is not None
            else "需要 model3.json 才能编辑点击绑定"
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        row = self.table.currentRow()
        has_row = 0 <= row < len(self.bindings)
        can_edit = self.model3_path is not None
        self.remove_button.setEnabled(has_row)
        self.add_selected_button.setEnabled(
            can_edit and self.selected_mesh_id in self.mesh_by_id and bool(self.groups)
        )
        self.scan_button.setEnabled(can_edit and bool(self.groups))
        self.save_button.setEnabled(can_edit and bool(self.bindings))

    def _scan_touch_meshes(self) -> None:
        suggested, skipped = suggest_touch_bindings(self.mesh_ids, self.groups)
        before = len(self.bindings)
        self.bindings = merge_bindings(self.bindings, suggested)
        self._refresh_table()
        message = f"扫描完成：新增 {len(self.bindings) - before} 条"
        if skipped:
            message += f"，跳过 {len(skipped)} 条（无同名动作组或重复）"
        self.status_label.setText(message)

    def _add_selected_mesh(self) -> None:
        mesh_id = self.selected_mesh_id
        group = self.group_combo.currentText().strip()
        if not mesh_id or not group:
            MessageBox(
                "无法添加",
                "请先在左侧图层树选中 ArtMesh，并选择动作组",
                self.window(),
            ).exec()
            return
        if mesh_id not in self.mesh_by_id:
            MessageBox(
                "无法添加", f"ArtMesh 不存在：{mesh_id}", self.window()
            ).exec()
            return
        for row, binding in enumerate(self.bindings):
            if binding.mesh_id == mesh_id or binding.name == group:
                self.table.selectRow(row)
                self.status_label.setText(
                    f"已存在：{binding.mesh_id} → {binding.name}"
                )
                return
        self.bindings.append(
            HitAreaBinding(mesh_id=mesh_id, name=group, motion_group=group)
        )
        self._refresh_table(select_row=len(self.bindings) - 1)

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.bindings):
            return
        self.bindings.pop(row)
        self._refresh_table(select_row=min(row, len(self.bindings) - 1))

    def _save(self) -> None:
        if self.model3_path is None:
            MessageBox(
                "没有模型", "请先加载 Live2D 模型", self.window()
            ).exec()
            return
        errors = validate_bindings(self.bindings, self.mesh_ids, groups=self.groups)
        if errors:
            MessageBox(
                "绑定校验失败", "\n".join(errors[:12]), self.window()
            ).exec()
            return
        missing = [binding for binding in self.bindings if not binding.motion_group]
        if missing:
            box = MessageBox(
                "存在未匹配动作组",
                f"有 {len(missing)} 条绑定没有匹配的 motion group，"
                "保存后点击不会有动作，仍要保存吗？",
                self.window(),
            )
            if box.exec() != QDialog.DialogCode.Accepted:
                return
        try:
            backup, saved = save_hit_areas(
                self.model3_path,
                self.bindings,
                art_mesh_ids=self.mesh_ids,
                groups=self.groups,
            )
        except HitAreaError as exc:
            MessageBox("保存失败", str(exc), self.window()).exec()
            return

        self.bindings = saved
        self._refresh_table()
        self.status_label.setText(
            f"已保存 {len(saved)} 条 HitAreas · 备份 {backup.name}"
        )
        self.statusMessage.emit(f"点击绑定已保存：{backup.name}")
        self.saved.emit()
