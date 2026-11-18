"""Read-only dialog listing everything py-moc3 exposes about the model."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    SegmentedWidget,
    SubtitleLabel,
)

from .theme import CONTROL_HEIGHT, StudioTableWidget
from ..core import ModelInfo

_NUMBER_COLUMNS = {2, 3}
_READONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class ModelInfoDialog(QDialog):
    """Overview, structure counts and per-section element counts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模型信息")
        self.resize(820, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        layout.addWidget(SubtitleLabel("模型信息", self))
        self.model_label = CaptionLabel("", self)
        self.model_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.model_label)

        overview = QWidget(self)
        self.overview_form = QFormLayout(overview)
        self.overview_form.setContentsMargins(0, 4, 0, 4)
        self.overview_form.setHorizontalSpacing(16)
        self.overview_form.setVerticalSpacing(6)
        layout.addWidget(overview)

        tabs = SegmentedWidget(self)
        tabs.addItem("counts", "结构统计")
        tabs.addItem("sections", "Section 明细")
        stack = QStackedWidget(self)
        self.count_table = self._build_table(
            ["分类", "项目", "数量"], stack
        )
        self.section_table = self._build_table(
            ["分组", "Section", "类型", "元素数"], stack
        )
        stack.addWidget(self.count_table)
        stack.addWidget(self.section_table)
        tabs.currentItemChanged.connect(
            lambda key: stack.setCurrentIndex(0 if key == "counts" else 1)
        )
        tabs.setCurrentItem("counts")
        layout.addWidget(tabs)
        layout.addWidget(stack, 1)

        footer = QHBoxLayout()
        self.section_hint = CaptionLabel("", self)
        footer.addWidget(self.section_hint, 1)
        close_button = PrimaryPushButton("关闭", self)
        close_button.setFixedHeight(CONTROL_HEIGHT)
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    @staticmethod
    def _build_table(headers: list[str], parent: QWidget) -> StudioTableWidget:
        table = StudioTableWidget(parent)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        return table

    def set_info(self, info: ModelInfo, model_name: str) -> None:
        self.model_label.setText(model_name)
        while self.overview_form.rowCount():
            self.overview_form.removeRow(0)
        for key, value in info.overview:
            label = BodyLabel(value, self)
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.overview_form.addRow(CaptionLabel(key, self), label)

        self._fill_table(self.count_table, info.counts)
        self._fill_table(self.section_table, info.sections)
        self.section_hint.setText(
            f"共 {len(info.sections)} 个 section · 元素数为 py-moc3 解析出的条目数量"
        )

    @staticmethod
    def _fill_table(
        table: StudioTableWidget, rows: tuple[tuple, ...]
    ) -> None:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(_READONLY_FLAGS)
                if column in _NUMBER_COLUMNS:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, column, item)
