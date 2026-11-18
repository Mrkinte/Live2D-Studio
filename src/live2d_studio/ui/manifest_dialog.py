"""Dialog that previews, edits and exports the website manifest (``*.live2d.json``)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from .dialogs import show_error
from .theme import (
    CONTROL_HEIGHT,
    StudioSplitter,
    StudioTableWidget,
    install_fluent_tooltip,
)
from ..core import ManifestError, ManifestPlan, MotionRow, write_manifest

MOTION_FILE_ROLE = Qt.ItemDataRole.UserRole

_CHECK_FLAGS = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
_READONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
_EDITABLE_FLAGS = (
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsSelectable
        | Qt.ItemFlag.ItemIsEditable
)


class ManifestExportDialog(QDialog):
    """Edit/check entries, then write the manifest next to the model."""

    def __init__(
            self,
            plan: ManifestPlan,
            parent=None,
            *,
            source_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.exported_path: Path | None = None
        self._output_path = plan.target
        self._syncing = False
        self._bulk_motion_sync = False
        self._wardrobe_meta: list[tuple[dict, dict | None]] = []
        self._motion_sort_column = -1
        self._motion_sort_order = Qt.SortOrder.AscendingOrder

        title_text = "导入并编辑 Atlas 清单" if source_path else "导出 Atlas 清单"
        self.setWindowTitle(title_text)
        self.resize(1100, 780)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        layout.addWidget(SubtitleLabel(title_text, self))
        if source_path is not None:
            layout.addWidget(CaptionLabel(f"导入来源：{source_path}", self))

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(StrongBodyLabel("输出文件", self))
        self.output_edit = LineEdit(self)
        self.output_edit.setFixedHeight(CONTROL_HEIGHT)
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(str(self._output_path))
        output_row.addWidget(self.output_edit, 1)
        self.browse_button = PushButton("更改", self)
        self.browse_button.setFixedHeight(CONTROL_HEIGHT)
        self.browse_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.browse_button)
        layout.addLayout(output_row)

        layout.addWidget(
            CaptionLabel(
                "只有勾选的条目会写入清单，取消勾选的项目会记录在 excluded 字段，"
                "下次导入这份清单时仍保持未勾选。台词与时长会写入对应动作，"
                "未填时长时使用下方的默认时长。动作列表可拖拽行或点击表头排序，"
                "导出时按当前顺序写入",
                self,
            )
        )

        splitter = StudioSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_motion_table())
        splitter.addWidget(self._build_bottom_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([430, 290])
        layout.addWidget(splitter, 1)

        duration_row = QHBoxLayout()
        duration_row.setSpacing(8)
        duration_row.addWidget(StrongBodyLabel("默认气泡时长", self))
        self.default_duration_edit = LineEdit(self)
        self.default_duration_edit.setFixedHeight(CONTROL_HEIGHT)
        self.default_duration_edit.setPlaceholderText(
            "例如 4000，留空表示由 Atlas 决定"
        )
        if plan.default_duration is not None:
            self.default_duration_edit.setText(f"{plan.default_duration:g}")
        self.default_duration_edit.setFixedWidth(180)
        duration_row.addWidget(self.default_duration_edit)
        duration_row.addWidget(CaptionLabel("单位毫秒，作用于未单独填写时长的台词", self))
        duration_row.addStretch(1)
        layout.addLayout(duration_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.note_label = CaptionLabel("", self)
        self.note_label.setWordWrap(True)
        buttons.addWidget(self.note_label, 1)
        self.cancel_button = PushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)
        self.export_button = PrimaryPushButton(
            "保存清单" if source_path else "生成清单", self
        )
        self.export_button.clicked.connect(self._export)
        for button in (self.cancel_button, self.export_button):
            button.setFixedHeight(CONTROL_HEIGHT)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self._refresh_note()

    def _build_motion_table(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(CaptionLabel("点击表头排序，选中行后可微调顺序", container))
        self.move_up_button = PushButton("上移", container)
        self.move_down_button = PushButton("下移", container)
        for button in (self.move_up_button, self.move_down_button):
            button.setFixedHeight(CONTROL_HEIGHT)
            button.setFixedWidth(64)
            button.setEnabled(False)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.move_up_button.clicked.connect(lambda: self._move_motion_row(-1))
        self.move_down_button.clicked.connect(lambda: self._move_motion_row(1))

        self.motion_table = StudioTableWidget(container)
        layout.addWidget(self.motion_table, 1)
        self.motion_table.setColumnCount(6)
        self.motion_table.setHorizontalHeaderLabels(
            ["", "动作文件", "显示名", "分组", "台词", "时长(ms)"]
        )
        header = self.motion_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.motion_table.setColumnWidth(0, 80)
        self.motion_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.motion_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.motion_table.verticalHeader().setVisible(False)
        self.motion_table.verticalHeader().setDefaultSectionSize(30)
        header = self.motion_table.horizontalHeader()
        header.resizeSection(0, 68)

        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_motions_by_header)
        self.motion_table.itemSelectionChanged.connect(self._sync_move_buttons)

        rows = self.plan.rows
        self._motions_by_id = {motion.id: motion for motion in rows}
        self._build_motion_header_check(header, rows)
        self.motion_table.setRowCount(len(rows))
        for row, motion in enumerate(rows):
            check_item = _check_item(motion.enabled)
            check_item.setToolTip("取消勾选后不会写入清单")
            file_item = QTableWidgetItem(motion.file)
            file_item.setData(MOTION_FILE_ROLE, motion.id)
            file_item.setToolTip(
                f"动作 ID：{motion.id}\n\n原始分组：{motion.source_group}"
            )
            file_item.setFlags(_READONLY_FLAGS)
            name_item = QTableWidgetItem(motion.name)
            name_item.setToolTip("显示在 Atlas 右键菜单中的名称")
            group_item = QTableWidgetItem(motion.label_group)
            group_item.setToolTip("同一分组的动作排在一起，留空表示不分组")
            text_item = QTableWidgetItem(motion.text)
            text_item.setToolTip("动作播放时气泡框显示的台词，留空表示不显示")
            duration_item = QTableWidgetItem(
                f"{motion.duration:g}" if motion.duration is not None else ""
            )
            duration_item.setToolTip("气泡停留时长（毫秒），留空使用默认时长")
            self.motion_table.setItem(row, 0, check_item)
            self.motion_table.setItem(row, 1, file_item)
            self.motion_table.setItem(row, 2, name_item)
            self.motion_table.setItem(row, 3, group_item)
            self.motion_table.setItem(row, 4, text_item)
            self.motion_table.setItem(row, 5, duration_item)
        self.motion_table.itemChanged.connect(self._refresh_note)
        return container

    def _build_motion_header_check(self, header: QHeaderView, rows) -> None:
        """Place an "select all" checkbox inside the header of the export column."""
        self.motion_header_check = CheckBox("全选", header)
        self.motion_header_check.setToolTip("一键勾选或取消全部动作的导出")
        install_fluent_tooltip(self.motion_header_check)
        self.motion_header_check.setChecked(
            bool(rows) and all(motion.enabled for motion in rows)
        )
        self.motion_header_check.stateChanged.connect(self._toggle_all_motions)
        header.sectionResized.connect(self._position_motion_header_check)
        header.geometriesChanged.connect(self._position_motion_header_check)
        self.motion_table.horizontalScrollBar().valueChanged.connect(
            self._position_motion_header_check
        )
        self._position_motion_header_check()

    def _position_motion_header_check(self, *args) -> None:
        """Keep the header checkbox aligned with the export column."""
        check = getattr(self, "motion_header_check", None)
        if check is None:
            return
        header = self.motion_table.horizontalHeader()
        height = header.height()
        hint = check.sizeHint()
        x = header.sectionViewportPosition(0)
        width = header.sectionSize(0)
        check.setGeometry(
            x + 6,
            max(0, (height - hint.height()) // 2),
            max(20, width - 12),
            hint.height(),
        )

    def _toggle_all_motions(self, state) -> None:
        """Check or uncheck every motion row from the header checkbox."""
        if self._bulk_motion_sync:
            return
        checked = Qt.CheckState(state) == Qt.CheckState.Checked
        self._bulk_motion_sync = True
        self.motion_table.blockSignals(True)
        try:
            for row in range(self.motion_table.rowCount()):
                item = self.motion_table.item(row, 0)
                if item is not None:
                    item.setCheckState(
                        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    )
        finally:
            self.motion_table.blockSignals(False)
            self._bulk_motion_sync = False
        self._refresh_note()

    def _sync_motion_header_check(self) -> None:
        check = getattr(self, "motion_header_check", None)
        if check is None:
            return
        total = self.motion_table.rowCount()
        checked = sum(
            1 for row in range(total) if self._checked(self.motion_table, row)
        )
        target = total > 0 and checked == total
        if check.isChecked() == target:
            return
        self._bulk_motion_sync = True
        try:
            check.setChecked(target)
        finally:
            self._bulk_motion_sync = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_motion_header_check()

    def _sort_motions_by_header(self, column: int) -> None:
        """Sort motion rows by the clicked header column, keeping edits intact."""
        if column == 0 or self.motion_table.rowCount() <= 1:
            return

        if self._motion_sort_column == column:
            toggled = (
                Qt.SortOrder.DescendingOrder
                if self._motion_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            toggled = Qt.SortOrder.AscendingOrder
        self._motion_sort_column = column
        self._motion_sort_order = toggled
        header = self.motion_table.horizontalHeader()
        header.setSortIndicator(column, toggled)
        header.setSortIndicatorShown(True)

        table = self.motion_table
        table.blockSignals(True)
        try:
            items = [
                [table.takeItem(row, col) for col in range(table.columnCount())]
                for row in range(table.rowCount())
            ]
            items.sort(
                key=lambda row_items: self._motion_sort_key(row_items, column),
                reverse=toggled == Qt.SortOrder.DescendingOrder,
            )
            for row, row_items in enumerate(items):
                for col, item in enumerate(row_items):
                    if item is not None:
                        table.setItem(row, col, item)
        finally:
            table.blockSignals(False)
        self._sync_move_buttons()
        self._refresh_note()

    @staticmethod
    def _motion_sort_key(
            items: list[QTableWidgetItem | None], column: int
    ) -> tuple[bool, str | float]:
        item = items[column]
        text = item.text().strip() if item is not None else ""
        if column == 5:
            try:
                return (False, float(text))
            except ValueError:
                return (True, 0.0)
        return (not text, text.casefold())

    def _clear_motion_sort(self) -> None:
        """Manual order overrides the header sort, so drop the indicator."""
        if self._motion_sort_column < 0:
            return
        self._motion_sort_column = -1
        self._motion_sort_order = Qt.SortOrder.AscendingOrder
        self.motion_table.horizontalHeader().setSortIndicatorShown(False)

    def _sync_move_buttons(self, *args) -> None:
        row = self.motion_table.currentRow()
        count = self.motion_table.rowCount()
        self.move_up_button.setEnabled(row > 0)
        self.move_down_button.setEnabled(0 <= row < count - 1)

    def _move_motion_row(self, offset: int) -> None:
        """Swap the selected row with its neighbour, preserving item data."""
        if self._motion_sort_column >= 0:
            self._clear_motion_sort()
        table = self.motion_table
        row = table.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= table.rowCount():
            return
        table.blockSignals(True)
        try:
            for col in range(table.columnCount()):
                current = table.takeItem(row, col)
                neighbour = table.takeItem(target, col)
                if current is not None:
                    table.setItem(target, col, current)
                if neighbour is not None:
                    table.setItem(row, col, neighbour)
        finally:
            table.blockSignals(False)
        table.setCurrentCell(target, table.currentColumn())
        self._sync_move_buttons()
        self._refresh_note()

    def _motion_at(self, row: int) -> MotionRow | None:
        item = self.motion_table.item(row, 1)
        if item is None:
            return None
        motion_id = item.data(MOTION_FILE_ROLE)
        if not isinstance(motion_id, str):
            return None
        return self._motions_by_id.get(motion_id)

    def _build_bottom_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        tabs = SegmentedWidget(container)
        tabs.addItem("expressions", "表情")
        tabs.addItem("wardrobe", "服装")
        stack = QStackedWidget(container)
        stack.addWidget(self._build_expression_table())
        stack.addWidget(self._build_wardrobe_table())
        tabs.currentItemChanged.connect(
            lambda key: stack.setCurrentIndex(0 if key == "expressions" else 1)
        )
        tabs.setCurrentItem("expressions")

        layout.addWidget(tabs)
        layout.addWidget(stack, 1)
        return container

    def _build_expression_table(self) -> StudioTableWidget:
        self.expression_table = StudioTableWidget(self)
        self.expression_table.setColumnCount(4)
        self.expression_table.setHorizontalHeaderLabels(
            ["导出", "表情名", "文件", "运行时名称"]
        )
        header = self.expression_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.expression_table.setColumnWidth(0, 80)
        self.expression_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.expression_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.expression_table.verticalHeader().setVisible(False)
        self.expression_table.verticalHeader().setDefaultSectionSize(30)

        items = self.plan.expressions
        self.expression_table.setRowCount(len(items))
        for row, item in enumerate(items):
            check_item = _check_item(item.get("enabled", True))
            check_item.setToolTip("取消勾选后 Atlas 不再显示这个表情")
            name_item = QTableWidgetItem(item["name"])
            name_item.setToolTip("显示在 Atlas 表情菜单中的名称")
            file_item = QTableWidgetItem(item.get("file", ""))
            file_item.setFlags(_READONLY_FLAGS)
            runtime_item = QTableWidgetItem(item["runtimeName"])
            runtime_item.setFlags(_READONLY_FLAGS)
            runtime_item.setToolTip("model3.json 中登记的表达式名称，运行时按它播放")
            self.expression_table.setItem(row, 0, check_item)
            self.expression_table.setItem(row, 1, name_item)
            self.expression_table.setItem(row, 2, file_item)
            self.expression_table.setItem(row, 3, runtime_item)
        self.expression_table.itemChanged.connect(self._refresh_note)
        return self.expression_table

    def _build_wardrobe_table(self) -> StudioTableWidget:
        self.wardrobe_table = StudioTableWidget(self)
        self.wardrobe_table.setColumnCount(4)
        self.wardrobe_table.setHorizontalHeaderLabels(
            ["导出", "服装组 / 选项", "参数", "选项值"]
        )
        header = self.wardrobe_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.wardrobe_table.setColumnWidth(0, 80)
        self.wardrobe_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.wardrobe_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.wardrobe_table.verticalHeader().setVisible(False)
        self.wardrobe_table.verticalHeader().setDefaultSectionSize(30)

        self._wardrobe_meta = []
        row_count = sum(1 + len(group["options"]) for group in self.plan.wardrobe)
        self.wardrobe_table.setRowCount(row_count)

        row = 0
        for group in self.plan.wardrobe:
            check_item = _check_item(group.get("enabled", True))
            check_item.setToolTip("取消勾选后整组服装都不会导出，可单独勾选组内选项")
            name_item = QTableWidgetItem(group["name"])
            name_item.setFlags(_EDITABLE_FLAGS)
            name_item.setToolTip(
                "双击可直接编辑服装组名称，导出的 wardrobe 名称会使用这里的文本"
            )
            parameters = group.get("parameters") or [group["parameter"]]
            parameter_item = QTableWidgetItem(", ".join(parameters))
            parameter_item.setFlags(_READONLY_FLAGS)
            parameter_item.setToolTip(
                "这一组换装会同时修改的参数，多参数选项会额外写入 values 字段"
            )
            hint_item = QTableWidgetItem(f"{len(group['options'])} 个选项")
            hint_item.setFlags(_READONLY_FLAGS)
            self.wardrobe_table.setItem(row, 0, check_item)
            self.wardrobe_table.setItem(row, 1, name_item)
            self.wardrobe_table.setItem(row, 2, parameter_item)
            self.wardrobe_table.setItem(row, 3, hint_item)
            self._wardrobe_meta.append((group, None))
            row += 1

            for option in group["options"]:
                option_check = _check_item(option.get("enabled", True))
                option_name = QTableWidgetItem(f"    {option['name']}")
                option_name.setFlags(_READONLY_FLAGS)
                empty_parameter = QTableWidgetItem("")
                empty_parameter.setFlags(_READONLY_FLAGS)
                raw_values = option.get("values")
                if isinstance(raw_values, dict) and raw_values:
                    value_text = ", ".join(
                        f"{parameter_id}={float(value):g}"
                        for parameter_id, value in raw_values.items()
                    )
                else:
                    value_text = f"{option['value']:g}"
                value_item = QTableWidgetItem(value_text)
                value_item.setFlags(_READONLY_FLAGS)
                self.wardrobe_table.setItem(row, 0, option_check)
                self.wardrobe_table.setItem(row, 1, option_name)
                self.wardrobe_table.setItem(row, 2, empty_parameter)
                self.wardrobe_table.setItem(row, 3, value_item)
                self._wardrobe_meta.append((group, option))
                row += 1

        self.wardrobe_table.itemChanged.connect(self._on_wardrobe_item_changed)
        return self.wardrobe_table

    def _on_wardrobe_item_changed(self, item: QTableWidgetItem) -> None:
        """A group checkbox toggles every option below it."""
        if self._syncing or item.column() != 0:
            return
        group, option = self._wardrobe_meta[item.row()]
        if option is not None:
            return

        checked = item.checkState() == Qt.CheckState.Checked
        self._syncing = True
        try:
            row = item.row() + 1
            for _ in group["options"]:
                child = self.wardrobe_table.item(row, 0)
                if child is not None:
                    child.setCheckState(
                        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    )
                row += 1
        finally:
            self._syncing = False
        self._refresh_note()

    def _choose_output(self) -> None:
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "选择清单输出位置",
            str(self._output_path),
            " Atlas 清单 (*.live2d.json);;JSON (*.json)",
        )
        if not file_name:
            return
        target = Path(file_name)
        if not target.name.lower().endswith(".live2d.json"):
            stem = (
                target.name[:-5]
                if target.name.lower().endswith(".json")
                else target.name
            )
            target = target.with_name(f"{stem}.live2d.json")
        self._output_path = target
        self.output_edit.setText(str(target))

    def _refresh_note(self) -> None:
        motions = sum(
            1
            for row in range(self.motion_table.rowCount())
            if self._checked(self.motion_table, row)
        )
        expressions = sum(
            1
            for row, _item in enumerate(self.plan.expressions)
            if self._checked(self.expression_table, row)
        )
        wardrobe = sum(
            1
            for row, (group, option) in enumerate(self._wardrobe_meta)
            if option is None and self._checked(self.wardrobe_table, row)
        )
        self.note_label.setText(
            f"将导出 动作 {motions}/{len(self.plan.rows)} 个，"
            f"表情 {expressions}/{len(self.plan.expressions)} 个，"
            f"服装组 {wardrobe}/{len(self.plan.wardrobe)} 个"
        )
        self._sync_motion_header_check()

    @staticmethod
    def _checked(table: StudioTableWidget, row: int) -> bool:
        item = table.item(row, 0)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _collect(self) -> None:
        ordered: list[MotionRow] = []
        for row in range(self.motion_table.rowCount()):
            motion = self._motion_at(row)
            if motion is None:
                continue
            motion.enabled = self._checked(self.motion_table, row)
            name_item = self.motion_table.item(row, 2)
            group_item = self.motion_table.item(row, 3)
            text_item = self.motion_table.item(row, 4)
            duration_item = self.motion_table.item(row, 5)
            if name_item is not None:
                motion.name = name_item.text().strip() or motion.name
            if group_item is not None:
                motion.label_group = group_item.text().strip()
            if text_item is not None:
                motion.text = text_item.text().strip()
            motion.duration = _parse_duration(
                duration_item.text() if duration_item is not None else "",
                f"动作「{motion.name}」",
            )
            ordered.append(motion)
        if ordered:
            # 导出按表格当前顺序（用户拖拽 / 表头排序后的结果）写入
            self.plan.rows = ordered

        for row, item in enumerate(self.plan.expressions):
            item["enabled"] = self._checked(self.expression_table, row)
            name_item = self.expression_table.item(row, 1)
            if name_item is not None and name_item.text().strip():
                item["name"] = name_item.text().strip()

        for row, (group, option) in enumerate(self._wardrobe_meta):
            enabled = self._checked(self.wardrobe_table, row)
            if option is None:
                group["enabled"] = enabled
                name_item = self.wardrobe_table.item(row, 1)
                if name_item is not None:
                    name = name_item.text().strip()
                    if name:
                        group["name"] = name
                    if name_item.text() != group["name"]:
                        name_item.setText(group["name"])
            else:
                option["enabled"] = enabled

        self.plan.default_duration = _parse_duration(
            self.default_duration_edit.text(), "默认气泡时长"
        )
        self._refresh_note()

    def _export(self) -> None:
        try:
            self._collect()
        except ValueError as exc:
            show_error(self, "时长格式错误", str(exc))
            return

        if not self.plan.rows and not self.plan.expressions and not self.plan.wardrobe:
            show_error(
                self,
                "无法生成",
                "当前模型没有可导出的动作、表情或服装",
            )
            return
        try:
            self.exported_path = write_manifest(self.plan, self._output_path)
        except ManifestError as exc:
            show_error(self, "生成失败", str(exc))
            return
        self.accept()


def _check_item(checked: bool) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setFlags(_CHECK_FLAGS)
    item.setCheckState(
        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    )
    return item


def _parse_duration(text: str, label: str) -> float | None:
    """Blank means "unset"; invalid or non-positive values raise ``ValueError``."""
    raw = text.strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{label}的时长不是数字：{raw}") from exc
    if value <= 0:
        return None
    return value
