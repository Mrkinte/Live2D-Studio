"""Bottom editor: Live2D parameters with sliders and outfit expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget, )
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    Slider,
    StrongBodyLabel,
    TransparentToolButton, )

from .dialogs import ask_binding, ask_binding_kind, show_error
from .manifest_dialog import ManifestExportDialog
from .theme import CONTROL_HEIGHT, StudioSplitter, StudioTableWidget
from ..core import (
    ExpressionError,
    ManifestError,
    Moc3Document,
    ParameterInfo,
    PatchError,
    expression_entries,
    load_manifest,
    plan_manifest,
    read_expression_kind,
    read_expression_values,
    register_expression,
    relative_expression_ref,
    save_expression,
    unregister_expression,
    update_expression_kind,
)

PARAMETER_ROLE = Qt.ItemDataRole.UserRole
SLIDER_STEPS = 1000
UNSAFE_FILE_CHARS = re.compile(r'[\\/:*?"<>|]+')


@dataclass
class OutfitBinding:
    """One outfit worth of parameter values, exported as an expression."""

    name: str
    values: dict[str, float] = field(default_factory=dict)
    file: Path | None = None
    kind: str = ""


class ParameterPanel(QWidget):
    """List every MOC3 parameter, preview edits, and export exp3.json files."""

    parameterChanged = Signal(str, float)
    overridesCleared = Signal()
    statusMessage = Signal(str)
    defaultsChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document: Moc3Document | None = None
        self.model3_path: Path | None = None
        self._parameters: list[ParameterInfo] = []
        self._row_parameters: dict[int, ParameterInfo] = {}
        self._sliders: dict[str, Slider] = {}
        self._value_items: dict[str, QTableWidgetItem] = {}
        self._values: dict[str, float] = {}
        self._bindings: list[OutfitBinding] = []
        self._default_dir = Path.cwd()
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        splitter = StudioSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_parameter_table())
        splitter.addWidget(self._build_binding_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 440])
        layout.addWidget(splitter, 1)

        self.set_document(None)

    def _build_parameter_table(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self.search_edit = SearchLineEdit(self)
        self.search_edit.setFixedHeight(CONTROL_HEIGHT)
        self.search_edit.setPlaceholderText("搜索参数名或参数 ID")
        self.search_edit.textChanged.connect(self._apply_filter)
        title_row.addWidget(self.search_edit)
        self.count_label = CaptionLabel("没有参数", self)
        title_row.addWidget(self.count_label)

        title_row.addStretch(1)

        self.reset_button = PrimaryPushButton("恢复默认值", self)
        self.reset_button.setFixedHeight(CONTROL_HEIGHT)
        self.reset_button.setToolTip("把所有参数滑块恢复为 MOC3 中定义的默认值")
        self.reset_button.clicked.connect(self.reset_values)
        title_row.addWidget(self.reset_button)
        self.write_default_button = PrimaryPushButton("写入默认值", self)
        self.write_default_button.setFixedHeight(CONTROL_HEIGHT)
        self.write_default_button.setToolTip(
            "把选中参数的当前数值写入 .moc3 的 parameter.default_values\n\n"
            "点击主界面“保存”后才会真正写盘，并自动生成备份"
        )
        self.write_default_button.clicked.connect(self._write_selected_defaults)
        title_row.addWidget(self.write_default_button)

        self.info_button = TransparentToolButton(FluentIcon.INFO, self)
        self.info_button.setFixedSize(24, 24)
        self.info_button.setToolTip(
            "选中参数后点击“绑定选中参数”按钮，即可加入待绑定列表\n\n"
            "支持 Ctrl / Shift 多选参数\n\n"
            "双击“数值”、“名称”、“类型”列可进入编辑模式\n\n"
            "编辑完绑定信息后，记得点击“生成exp3”按钮保存，建议保存在模型的 exps 目录中"
        )
        title_row.addWidget(self.info_button)
        layout.addLayout(title_row)

        self.param_table = StudioTableWidget(container)
        self.param_table.setColumnCount(3)
        self.param_table.setHorizontalHeaderLabels(["参数", "数值", "滑块"])
        self.param_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.param_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.param_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.param_table.setWordWrap(False)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.verticalHeader().setDefaultSectionSize(30)
        self.param_table.itemSelectionChanged.connect(self._update_buttons)
        self.param_table.itemChanged.connect(self._on_value_item_changed)
        layout.addWidget(self.param_table, 1)
        return container

    def _build_binding_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        label = StrongBodyLabel("换装 / 表情绑定", container)
        label.setFixedHeight(CONTROL_HEIGHT)
        title_row.addWidget(label)
        title_row.addStretch(1)
        self.register_check = CheckBox("注册到 model3.json 的 Expressions", container)
        self.register_check.setChecked(True)
        self.register_check.setToolTip(
            "生成后把 表情名 → 文件 写入 model3.json，运行时可直接 SetExpression"
        )
        title_row.addWidget(self.register_check)

        layout.addLayout(title_row)

        self.binding_table = StudioTableWidget(container)
        self.binding_table.setColumnCount(4)
        self.binding_table.setHorizontalHeaderLabels(
            ["名称", "类型", "参数取值", "文件"]
        )
        self.binding_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.binding_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.binding_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.binding_table.verticalHeader().setVisible(False)
        self.binding_table.verticalHeader().setDefaultSectionSize(30)
        self.binding_table.itemSelectionChanged.connect(self._update_buttons)
        self.binding_table.cellDoubleClicked.connect(self._on_binding_double_clicked)
        layout.addWidget(self.binding_table, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.bind_button = PushButton("绑定选中参数", container)
        self.bind_button.setFixedHeight(CONTROL_HEIGHT)
        self.bind_button.setToolTip(
            "把选中的参数和当前值保存为一个绑定，并选择导出为表情还是换装"
        )
        self.bind_button.clicked.connect(self._bind_selected_parameters)
        controls.addWidget(self.bind_button)

        self.remove_button = PushButton("移除", container)
        self.remove_button.setFixedHeight(CONTROL_HEIGHT)
        self.remove_button.setToolTip(
            "移除绑定，并删除硬盘上已生成的 exp3 文件"
        )
        self.remove_button.clicked.connect(self._remove_selected_binding)
        controls.addWidget(self.remove_button)

        self.kind_button = PushButton("修改类型", container)
        self.kind_button.setFixedHeight(CONTROL_HEIGHT)
        self.kind_button.clicked.connect(self._change_binding_kind)
        controls.addWidget(self.kind_button)

        self.generate_button = PrimaryPushButton("生成 exp3", container)
        self.generate_button.setFixedHeight(CONTROL_HEIGHT)
        self.generate_button.setToolTip(
            "把选中绑定写为 Cubism 表情文件（Blend 为 Add）"
        )
        self.generate_button.clicked.connect(self._generate_expression)
        controls.addWidget(self.generate_button)

        self.import_button = PushButton("导入清单", container)
        self.import_button.setFixedHeight(CONTROL_HEIGHT)
        self.import_button.setToolTip(
            "载入已有的 .live2d.json"
        )
        self.import_button.clicked.connect(self._import_manifest)
        controls.addWidget(self.import_button)

        self.manifest_button = PushButton("导出 Atlas 清单", container)
        self.manifest_button.setFixedHeight(CONTROL_HEIGHT)
        self.manifest_button.setToolTip(
            "生成与 model3.json 同目录的 .live2d.json，供 Atlas 读取动作别名、分组、表情与服装"
        )
        self.manifest_button.clicked.connect(self._export_manifest)
        controls.addWidget(self.manifest_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.status_label = CaptionLabel("", container)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        return container

    def set_document(self, document: Moc3Document | None) -> None:
        self.document = document
        self.model3_path = document.model3_path if document is not None else None
        self._parameters = list(document.parameters) if document is not None else []
        self._row_parameters.clear()
        self._sliders.clear()
        self._value_items.clear()
        self._values.clear()
        self._bindings.clear()
        self._updating = True
        try:
            self.param_table.setRowCount(0)
            self.param_table.setRowCount(len(self._parameters))
            for row, parameter in enumerate(self._parameters):
                self._add_parameter_row(row, parameter)
        finally:
            self._updating = False

        self._load_expression_bindings()
        self._refresh_binding_table()
        self.search_edit.setEnabled(document is not None)
        self.reset_button.setEnabled(bool(self._parameters))
        has_model3 = self.model3_path is not None and model3_path_exists(
            self.model3_path
        )
        self.register_check.setEnabled(has_model3)
        self.import_button.setEnabled(has_model3)
        self.manifest_button.setEnabled(has_model3)
        self.count_label.setText(
            f"{len(self._parameters)} 个参数"
            if self._parameters
            else "没有参数"
        )
        self.search_edit.clear()
        self._update_buttons()

    def set_default_dir(self, directory: str | Path) -> None:
        path = Path(directory)
        if path.is_dir():
            self._default_dir = path

    def _add_parameter_row(self, row: int, parameter: ParameterInfo) -> None:
        display_name = (
            self.document.parameter_name(parameter.id)
            if self.document is not None
            else parameter.id
        )
        name_item = QTableWidgetItem(display_name)
        name_item.setData(PARAMETER_ROLE, parameter.id)
        name_item.setToolTip(
            f"参数 ID: {parameter.id}\n\n"
            f"范围: {parameter.format(parameter.minimum)}  "
            f"{parameter.format(parameter.maximum)}\n\n"
            f"默认值: {parameter.format(parameter.default)}"
        )
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

        value = parameter.default
        self._values[parameter.id] = value
        value_item = QTableWidgetItem(parameter.format(value))
        value_item.setToolTip(
            "当前滑块值（仅预览），双击可直接输入数值，超出范围会自动截断"
        )
        value_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        value_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

        slider_container = QWidget(self.param_table)
        slider_layout = QHBoxLayout(slider_container)
        slider_layout.setContentsMargins(6, 0, 6, 0)
        slider = Slider(Qt.Orientation.Horizontal, slider_container)
        slider.setRange(0, SLIDER_STEPS)
        slider.setFixedHeight(24)
        slider.setValue(self._encode(parameter, value))
        slider.valueChanged.connect(
            lambda position, pid=parameter.id: self._on_slider_changed(pid, position)
        )
        slider_layout.addWidget(slider)

        self.param_table.setItem(row, 0, name_item)
        self.param_table.setItem(row, 1, value_item)
        self.param_table.setCellWidget(row, 2, slider_container)
        self._row_parameters[row] = parameter
        self._sliders[parameter.id] = slider
        self._value_items[parameter.id] = value_item

    @staticmethod
    def _encode(parameter: ParameterInfo, value: float) -> int:
        span = parameter.maximum - parameter.minimum
        if span <= 0:
            return 0
        ratio = (parameter.clamp(value) - parameter.minimum) / span
        return int(round(ratio * SLIDER_STEPS))

    @staticmethod
    def _decode(parameter: ParameterInfo, position: int) -> float:
        span = parameter.maximum - parameter.minimum
        value = parameter.minimum + span * (position / SLIDER_STEPS)
        return round(value, parameter.decimals)

    def _on_slider_changed(self, parameter_id: str, position: int) -> None:
        if self._updating:
            return
        parameter = self._parameter(parameter_id)
        if parameter is None:
            return
        value = self._decode(parameter, position)
        self._values[parameter_id] = value
        item = self._value_items.get(parameter_id)
        if item is not None:
            item.setText(parameter.format(value))
        self.parameterChanged.emit(parameter_id, value)

    def _on_value_item_changed(self, item: QTableWidgetItem) -> None:
        """Apply a value typed directly into the table's value column."""
        if self._updating or item.column() != 1:
            return
        parameter = self._row_parameters.get(item.row())
        if parameter is None:
            return

        try:
            value = float(item.text().strip())
        except ValueError:
            self._restore_value_text(parameter, item)
            return

        value = round(parameter.clamp(value), parameter.decimals)
        if value == self._values.get(parameter.id):
            self._restore_value_text(parameter, item, value)
            return

        self._updating = True
        try:
            self._values[parameter.id] = value
            item.setText(parameter.format(value))
            slider = self._sliders.get(parameter.id)
            if slider is not None:
                slider.setValue(self._encode(parameter, value))
        finally:
            self._updating = False
        self.parameterChanged.emit(parameter.id, value)

    def _restore_value_text(
            self,
            parameter: ParameterInfo,
            item: QTableWidgetItem,
            value: float | None = None,
    ) -> None:
        if value is None:
            value = self._values.get(parameter.id, parameter.default)
        self._updating = True
        try:
            item.setText(parameter.format(value))
        finally:
            self._updating = False

    def reset_values(self) -> None:
        if not self._parameters:
            return
        self._updating = True
        try:
            for parameter in self._parameters:
                value = parameter.default
                self._values[parameter.id] = value
                slider = self._sliders.get(parameter.id)
                if slider is not None:
                    slider.setValue(self._encode(parameter, value))
                item = self._value_items.get(parameter.id)
                if item is not None:
                    item.setText(parameter.format(value))
        finally:
            self._updating = False
        self.overridesCleared.emit()
        self.status_label.setText("已恢复所有参数默认值")

    def _write_selected_defaults(self) -> None:
        """Stage the selected rows' current values as MOC3 defaults."""
        if self.document is None:
            return
        parameters = self._selected_parameters()
        if not parameters:
            show_error(
                self.window(),
                "没有选中参数",
                "请先在左侧选择参数"
            )
            return

        try:
            for parameter in parameters:
                self.document.set_parameter_default(
                    parameter.id, self._values[parameter.id]
                )
        except (KeyError, PatchError) as exc:
            show_error(self.window(), "无法写入默认值", str(exc))
            return

        names = ", ".join(
            self.document.parameter_name(parameter.id)
            for parameter in parameters
        )
        self.status_label.setText(
            f"已标记 {len(parameters)} 个参数的默认值，保存后写入 .moc3：{names}"
        )
        self.statusMessage.emit(f"默认值待写入（保存后生效）：{names}")
        self.defaultsChanged.emit()
        self._update_buttons()

    def refresh_defaults(self) -> None:
        """Refresh cached parameter definitions after the file is written."""
        if self.document is None:
            return
        self._parameters = list(self.document.parameters)
        self._row_parameters = {
            row: parameter for row, parameter in enumerate(self._parameters)
        }
        for row, parameter in enumerate(self._parameters):
            item = self.param_table.item(row, 0)
            if item is not None:
                item.setToolTip(
                    f"参数 ID: {parameter.id}\n\n"
                    f"范围: {parameter.format(parameter.minimum)}  "
                    f"{parameter.format(parameter.maximum)}\n\n"
                    f"默认值: {parameter.format(parameter.default)}"
                )

    def _parameter(self, parameter_id: str) -> ParameterInfo | None:
        if self.document is None:
            return None
        return self.document.parameter_by_id.get(parameter_id)

    def values(self) -> dict[str, float]:
        return dict(self._values)

    def _selected_parameters(self) -> list[ParameterInfo]:
        selection = self.param_table.selectionModel()
        if selection is None:
            return []
        parameters: list[ParameterInfo] = []
        for index in selection.selectedRows():
            parameter = self._row_parameters.get(index.row())
            if parameter is not None:
                parameters.append(parameter)
        return parameters

    def _bind_selected_parameters(self) -> None:
        parameters = self._selected_parameters()
        if not parameters:
            show_error(
                self.window(),
                "没有选中参数",
                "请先在左侧参数表中选择一行或多行参数"
            )
            return

        default_name = (
            self.document.parameter_name(parameters[0].id)
            if self.document is not None
            else parameters[0].id
        )
        existing = next(
            (item for item in self._bindings if item.name == default_name), None
        )
        result = ask_binding(
            self.window(),
            "绑定为表情 / 换装",
            placeholder="绑定名称，例如：黑丝 / 微笑",
            default=default_name,
            kind=existing.kind if existing is not None else "",
        )
        if result is None:
            return
        name, kind = result

        values = {parameter.id: self._values[parameter.id] for parameter in parameters}
        for index, binding in enumerate(self._bindings):
            if binding.name == name:
                self._bindings[index] = OutfitBinding(
                    name=name, values=values, kind=kind
                )
                break
        else:
            self._bindings.append(OutfitBinding(name=name, values=values, kind=kind))

        self._refresh_binding_table(select_row=len(self._bindings) - 1)
        label = {"expression": "表情", "wardrobe": "换装"}.get(kind, "自动")
        self.statusMessage.emit(
            f"已绑定为{label}“{name}”：{len(values)} 个参数（生成 exp3 后类型生效）"
        )

    def _on_binding_double_clicked(self, row: int, column: int) -> None:
        if column == 1:
            self._change_binding_kind()

    def _change_binding_kind(self) -> None:
        row = self.binding_table.currentRow()
        if not 0 <= row < len(self._bindings):
            return
        binding = self._bindings[row]
        kind = ask_binding_kind(self.window(), kind=binding.kind)
        if kind is None or kind == binding.kind:
            return

        note = ""
        if binding.file is not None and binding.file.is_file():
            try:
                update_expression_kind(binding.file, kind)
            except ExpressionError as exc:
                show_error(self.window(), "更新类型失败", str(exc))
                return
            note = "，已同步更新 exp3 文件"

        binding.kind = kind
        self._refresh_binding_table(select_row=row)
        label = {"expression": "表情", "wardrobe": "换装"}.get(kind, "自动")
        self.status_label.setText(
            f"“{binding.name}”已改为{label}{note}，导出清单时按新类型归类"
        )
        self.statusMessage.emit(f"绑定“{binding.name}”类型：{label}{note}")

    def _remove_selected_binding(self) -> None:
        row = self.binding_table.currentRow()
        if not 0 <= row < len(self._bindings):
            return
        binding = self._bindings[row]
        target = binding.file if binding.file is not None else None
        if target is not None and not target.is_file():
            target = None

        if target is not None:
            message = (
                f"确定移除绑定“{binding.name}”吗？\n\n"
                f"同时删除硬盘上的文件：\n{target}\n\n"
                "此操作不可撤销"
            )
        else:
            message = (
                f"确定移除绑定“{binding.name}”吗？\n\n"
                "该绑定还没有可删除的 exp3 文件"
            )
        if (
                MessageBox("移除绑定", message, self.window()).exec()
                != QDialog.DialogCode.Accepted
        ):
            return

        if target is not None:
            try:
                target.unlink()
            except OSError as exc:
                show_error(
                    self.window(), "删除文件失败", f"{target}\n\n{exc}"
                )
                return

        note = ""
        if self.model3_path is not None and model3_path_exists(self.model3_path):
            try:
                if unregister_expression(self.model3_path, binding.name):
                    note = "，已从 model3.json 移除"
            except ExpressionError as exc:
                show_error(self.window(), "更新 model3.json 失败", str(exc))

        self._bindings.pop(row)
        self._refresh_binding_table(
            select_row=min(row, len(self._bindings) - 1)
        )
        self.statusMessage.emit(f"已移除绑定“{binding.name}”{note}")

    def _refresh_binding_table(self, select_row: int | None = None) -> None:
        self.binding_table.setRowCount(len(self._bindings))
        for row, binding in enumerate(self._bindings):
            name_item = QTableWidgetItem(binding.name)
            kind_label = {
                "expression": "表情",
                "wardrobe": "换装",
            }.get(binding.kind, "自动")
            kind_item = QTableWidgetItem(kind_label)
            kind_item.setToolTip(
                "自动：单参数且同一参数有多个取值时归为换装，其余归为表情"
            )
            summary_item = QTableWidgetItem(self._binding_summary(binding))
            summary_item.setToolTip(summary_item.text())
            file_item = QTableWidgetItem(
                binding.file.name if binding.file is not None else "未生成"
            )
            if binding.file is not None:
                file_item.setToolTip(str(binding.file))
            for item in (name_item, kind_item, summary_item, file_item):
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
            self.binding_table.setItem(row, 0, name_item)
            self.binding_table.setItem(row, 1, kind_item)
            self.binding_table.setItem(row, 2, summary_item)
            self.binding_table.setItem(row, 3, file_item)

        if select_row is not None and 0 <= select_row < len(self._bindings):
            self.binding_table.selectRow(select_row)
        self.status_label.setText(
            f"{len(self._bindings)} 个绑定"
            if self._bindings
            else "还没有绑定：选中参数行后点击“绑定选中参数”"
        )
        self._update_buttons()

    def _load_expression_bindings(self) -> None:
        """Import Expressions registered in model3.json so they can be re-edited."""
        if self.model3_path is None or not model3_path_exists(self.model3_path):
            return
        try:
            entries = expression_entries(self.model3_path)
        except Exception:
            return
        for entry in entries:
            values = (
                read_expression_values(entry.path) if entry.path is not None else {}
            )
            file_path = entry.path if entry.path is not None else Path(entry.file)
            kind = (
                read_expression_kind(entry.path) if entry.path is not None else ""
            )
            self._bindings.append(
                OutfitBinding(
                    name=entry.name, values=values, file=file_path, kind=kind
                )
            )

    def _binding_summary(self, binding: OutfitBinding) -> str:
        parts = []
        for parameter_id, value in binding.values.items():
            parameter = self._parameter(parameter_id)
            name = (
                self.document.parameter_name(parameter_id)
                if self.document is not None
                else parameter_id
            )
            text = parameter.format(value) if parameter is not None else f"{value:g}"
            parts.append(f"{name}={text}")
        return ", ".join(parts)

    def _generate_expression(self) -> None:
        row = self.binding_table.currentRow()
        if not 0 <= row < len(self._bindings):
            return
        binding = self._bindings[row]
        if not binding.values:
            return

        default = self._default_dir / "expressions" / (
                safe_file_name(binding.name) + ".exp3.json"
        )
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "生成绑定文件",
            str(default),
            "Cubism Expression (*.exp3.json);;JSON (*.json)",
        )
        if not file_name:
            return
        target = _expression_path(Path(file_name))

        try:
            saved = save_expression(target, binding.values, kind=binding.kind)
        except ExpressionError as exc:
            show_error(self.window(), "生成失败", str(exc))
            return

        registered = False
        if (
                self.register_check.isChecked()
                and self.model3_path is not None
                and model3_path_exists(self.model3_path)
        ):
            try:
                registered = register_expression(
                    self.model3_path,
                    binding.name,
                    relative_expression_ref(self.model3_path, saved),
                    keep_names=[item.name for item in self._bindings],
                )
            except ExpressionError as exc:
                show_error(
                    self.window(),
                    "注册到 model3.json 失败",
                    f"{exc}\n\n表情文件已经生成：{saved}",
                )
                return

        binding.file = saved
        self._refresh_binding_table(select_row=row)
        note = "，已注册到 model3.json" if registered else ""
        self.status_label.setText(f"已生成：{saved.name}{note}")
        self.statusMessage.emit(f"换装文件已生成：{saved}{note}")

    def _export_manifest(self) -> None:
        self._show_manifest_dialog()

    def _import_manifest(self) -> None:
        if self.model3_path is None or not model3_path_exists(self.model3_path):
            show_error(
                self.window(),
                "无法导入",
                "当前模型没有可用的 model3.json"
            )
            return

        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "导入网站清单",
            str(self._default_dir),
            "网站清单 (*.live2d.json);;JSON (*.json)",
        )
        if not file_name:
            return
        try:
            existing = load_manifest(file_name)
        except ManifestError as exc:
            show_error(self.window(), "导入失败", str(exc))
            return
        self._show_manifest_dialog(existing=existing, source=Path(file_name))

    def _show_manifest_dialog(
            self, existing: dict | None = None, source: Path | None = None
    ) -> None:
        if self.model3_path is None or not model3_path_exists(self.model3_path):
            show_error(
                self.window(),
                "无法导出",
                "当前模型没有可用的 model3.json"
            )
            return

        parameter_names = (
            dict(self.document.parameter_names)
            if self.document is not None
            else {}
        )
        try:
            plan = plan_manifest(
                self.model3_path,
                parameter_names=parameter_names,
                existing=existing,
            )
        except ManifestError as exc:
            show_error(self.window(), "导出失败", str(exc))
            return

        dialog = ManifestExportDialog(plan, self.window(), source_path=source)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.exported_path is None:
            return

        self.status_label.setText(f"已保存网站清单：{dialog.exported_path.name}")
        self.statusMessage.emit(f"网站清单已保存：{dialog.exported_path}")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, parameter in self._row_parameters.items():
            name = (
                self.document.parameter_name(parameter.id)
                if self.document is not None
                else parameter.id
            )
            match = needle in name.lower() or needle in parameter.id.lower()
            self.param_table.setRowHidden(row, bool(needle) and not match)

    def _update_buttons(self) -> None:
        has_parameters = bool(self._parameters)
        selected = len(self._selected_parameters())
        row = self.binding_table.currentRow()
        has_binding = 0 <= row < len(self._bindings)
        self.bind_button.setEnabled(has_parameters and selected > 0)
        self.kind_button.setEnabled(has_binding)
        self.remove_button.setEnabled(has_binding)
        self.generate_button.setEnabled(has_binding)
        self.reset_button.setEnabled(has_parameters)
        self.write_default_button.setEnabled(
            has_parameters
            and selected > 0
            and self.document is not None
            and self.document.parameter_default_offset is not None
        )


def safe_file_name(name: str) -> str:
    """Turn a user-facing expression name into a Windows-safe file stem."""
    cleaned = UNSAFE_FILE_CHARS.sub("_", name).strip().strip(".")
    return cleaned or "expression"


def _expression_path(path: Path) -> Path:
    """Normalize a chosen path to ``*.exp3.json``."""
    if path.name.lower().endswith(".exp3.json"):
        return path
    stem = path.name[:-5] if path.name.lower().endswith(".json") else path.name
    return path.with_name(f"{stem}.exp3.json")


def model3_path_exists(path: Path | None) -> bool:
    return path is not None and path.is_file()
