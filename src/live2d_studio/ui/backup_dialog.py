"""Dialog that lists and removes the timestamped ``*.bak-*`` files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout, QHeaderView,
)
from qfluentwidgets import (
    CaptionLabel,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

from .dialogs import show_error
from .theme import CONTROL_HEIGHT, StudioTableWidget
from ..core import BackupFile, delete_backups, find_backups

_CHECK_FLAGS = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled


class BackupCleanupDialog(QDialog):
    """List backups under a model folder and delete the selected ones."""

    def __init__(self, root: Path, parent=None) -> None:
        super().__init__(parent)
        self.root = Path(root).expanduser().resolve()
        self.deleted = 0
        self._backups: list[BackupFile] = []

        self.setWindowTitle("清理备份文件")
        self.resize(860, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        layout.addWidget(SubtitleLabel("清理备份文件", self))
        layout.addWidget(CaptionLabel(f"扫描目录：{self.root}", self))
        layout.addWidget(
            CaptionLabel(
                "写入 model3.json、exp3.json 或网站清单前会自动生成 "
                ".bak-时间戳 备份，删除后无法恢复，请确认不再需要回滚",
                self,
            )
        )

        self.table = StudioTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["选择", "文件", "大小", "修改时间"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 80)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.table, 1)

        self.empty_label = CaptionLabel("", self)
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.select_all_button = PushButton("全选", self)
        self.select_all_button.setFixedHeight(CONTROL_HEIGHT)
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        self.select_none_button = PushButton("全不选", self)
        self.select_none_button.setFixedHeight(CONTROL_HEIGHT)
        self.select_none_button.clicked.connect(lambda: self._set_all(False))
        self.delete_button = PrimaryPushButton("删除选中", self)
        self.delete_button.setFixedHeight(CONTROL_HEIGHT)
        self.delete_button.clicked.connect(self._delete_selected)
        self.close_button = PushButton("关闭", self)
        self.close_button.setFixedHeight(CONTROL_HEIGHT)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.select_none_button)
        buttons.addStretch(1)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self._reload()

    def _reload(self) -> None:
        self._backups = find_backups(self.root)
        self.table.setRowCount(len(self._backups))
        for row, backup in enumerate(self._backups):
            check_item = QTableWidgetItem()
            check_item.setFlags(_CHECK_FLAGS)
            check_item.setCheckState(Qt.CheckState.Checked)
            try:
                relative = backup.path.relative_to(self.root).as_posix()
            except ValueError:
                relative = backup.path.name
            name_item = QTableWidgetItem(relative)
            name_item.setToolTip(str(backup.path))
            size_item = QTableWidgetItem(_human_size(backup.size))
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            time_item = QTableWidgetItem(
                backup.modified.strftime("%Y-%m-%d %H:%M:%S")
            )
            for item in (name_item, size_item, time_item):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, size_item)
            self.table.setItem(row, 3, time_item)

        has_backups = bool(self._backups)
        for widget in (
                self.select_all_button,
                self.select_none_button,
                self.delete_button,
        ):
            widget.setEnabled(has_backups)
        self.empty_label.setText(
            f"共 {len(self._backups)} 个备份文件"
            if has_backups
            else "没有找到备份文件"
        )

    def _set_all(self, checked: bool) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    def _selected(self) -> list[BackupFile]:
        selected: list[BackupFile] = []
        for row, backup in enumerate(self._backups):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(backup)
        return selected

    def _delete_selected(self) -> None:
        selected = self._selected()
        if not selected:
            return
        box = MessageBox(
            "删除备份文件",
            f"将永久删除 {len(selected)} 个备份文件，删除后无法恢复\n\n继续吗？",
            self,
        )
        if box.exec() != QDialog.DialogCode.Accepted:
            return

        deleted, failures = delete_backups(selected)
        self.deleted += deleted
        self._reload()
        if failures:
            show_error(
                self,
                "部分备份删除失败",
                "\n".join(failures[:10])
                + ("\n" if len(failures) > 10 else ""),
            )


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
