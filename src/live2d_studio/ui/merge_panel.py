"""Bottom editor: merge a list of motion3 files into one linear motion."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget, QSpinBox,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
)

from .theme import CONTROL_HEIGHT, StudioTableWidget
from ..core import (
    Motion3,
    Motion3Error,
    MotionEntry,
    dumps_motion3,
    sample_motions,
)


class MergePanel(QWidget):
    """Collect motion3 clips and write one merged motion3 file."""

    statusMessage = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._clips: list[tuple[Path, Motion3]] = []
        self._last_dir = str(Path.cwd())
        self._default_dir = Path.cwd()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self.table = StudioTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["文件", "时长", "FPS", "循环"])
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
        self.add_button = PrimaryPushButton("添加 motion3", self)
        self.add_button.setFixedHeight(CONTROL_HEIGHT)
        self.add_button.clicked.connect(self._add_files)
        controls.addWidget(self.add_button)
        self.remove_button = PushButton("移除", self)
        self.remove_button.clicked.connect(self._remove_selected)
        self.up_button = PushButton("上移", self)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button = PushButton("下移", self)
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.clear_button = PushButton("清空", self)
        self.clear_button.clicked.connect(self._clear)
        for button in (
                self.remove_button,
                self.up_button,
                self.down_button,
                self.clear_button,
        ):
            button.setFixedHeight(CONTROL_HEIGHT)
            controls.addWidget(button)

        controls.addSpacing(12)
        controls.addWidget(CaptionLabel("采样", self))
        self.fps_spin = QSpinBox(self)
        self.fps_spin.setFixedHeight(CONTROL_HEIGHT)
        self.fps_spin.setRange(10, 60)
        self.fps_spin.setValue(60)
        self.fps_spin.setSuffix(" FPS")
        self.fps_spin.setFixedWidth(self.fps_spin.sizeHint().width())
        self.fps_spin.setToolTip("按该采样率重写为线性关键帧")
        controls.addWidget(self.fps_spin)
        self.loop_check = CheckBox("循环", self)
        self.loop_check.setToolTip("合并结果的 Meta.Loop")
        controls.addWidget(self.loop_check)
        controls.addStretch(1)
        self.status_label = CaptionLabel("合并列表为空", self)
        self.status_label.setWordWrap(True)
        controls.addWidget(self.status_label)
        controls.addStretch(1)
        self.info_button = TransparentToolButton(FluentIcon.INFO, self)
        self.info_button.setFixedSize(24, 24)
        self.info_button.setToolTip(
            "按顺序拼接 motion3，输出线性关键帧，不包含物理/眨眼等运行时效果"
        )
        controls.addWidget(self.info_button)
        self.merge_button = PrimaryPushButton("合并并保存", self)
        self.merge_button.setFixedHeight(CONTROL_HEIGHT)
        self.merge_button.clicked.connect(self.merge_to_file)
        controls.addWidget(self.merge_button)
        layout.addLayout(controls)

        self._refresh()

    def set_default_dir(self, directory: str | Path) -> None:
        path = Path(directory)
        if path.is_dir():
            self._default_dir = path
            self._last_dir = str(path)

    def add_entry(self, entry: MotionEntry) -> bool:
        if entry.path is None:
            MessageBox(
                "无法添加",
                f"motion 文件不存在：\n{entry.file}",
                self.window(),
            ).exec()
            return False
        return self._add_paths([entry.path])

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 motion3",
            self._last_dir,
            "Cubism Motion (*.motion3.json *.motion3);;JSON (*.json);;所有文件 (*)",
        )
        if paths:
            self._last_dir = str(Path(paths[0]).parent)
            self._add_paths([Path(path) for path in paths])

    def _add_paths(self, paths: list[Path]) -> bool:
        added = 0
        errors: list[str] = []
        known = {path.resolve() for path, _ in self._clips}
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            if resolved in known:
                continue
            try:
                motion = Motion3.load(resolved)
            except Motion3Error as exc:
                errors.append(f"{resolved.name}: {exc}")
                continue
            self._clips.append((resolved, motion))
            known.add(resolved)
            added += 1
        self._refresh(select_row=len(self._clips) - 1 if added else None)
        if errors:
            MessageBox("部分文件未导入", "\n".join(errors), self.window()).exec()
        return added > 0

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._clips):
            return
        self._clips.pop(row)
        self._refresh(select_row=min(row, len(self._clips) - 1))

    def _move_selected(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if not 0 <= row < len(self._clips) or not 0 <= target < len(self._clips):
            return
        self._clips[row], self._clips[target] = (
            self._clips[target],
            self._clips[row],
        )
        self._refresh(select_row=target)

    def _clear(self) -> None:
        if not self._clips:
            return
        self._clips.clear()
        self._refresh()

    def _refresh(self, select_row: int | None = None) -> None:
        self.table.setRowCount(len(self._clips))
        for row, (path, motion) in enumerate(self._clips):
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            duration_item = QTableWidgetItem(f"{motion.duration:.2f} s")
            fps_item = QTableWidgetItem(f"{motion.fps:.0f}")
            loop_item = QTableWidgetItem("是" if motion.loop else "否")
            for item in (duration_item, fps_item, loop_item):
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, duration_item)
            self.table.setItem(row, 2, fps_item)
            self.table.setItem(row, 3, loop_item)
        if select_row is not None and 0 <= select_row < len(self._clips):
            self.table.selectRow(select_row)
        total = sum(motion.duration for _, motion in self._clips)
        self.status_label.setText(
            f"{len(self._clips)} 段，总时长 {total:.2f} 秒"
            if self._clips
            else "合并列表为空"
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        row = self.table.currentRow()
        has_row = 0 <= row < len(self._clips)
        has_clips = bool(self._clips)
        self.remove_button.setEnabled(has_row)
        self.up_button.setEnabled(has_row and row > 0)
        self.down_button.setEnabled(has_row and row < len(self._clips) - 1)
        self.clear_button.setEnabled(has_clips)
        self.merge_button.setEnabled(has_clips)

    def merge_to_file(self) -> None:
        if not self._clips:
            return
        default = self._default_dir / "combined.motion3.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "合并为 motion3",
            str(default),
            "Cubism Motion (*.motion3.json);;JSON (*.json)",
        )
        if not path:
            return

        target = Path(path)
        try:
            data = sample_motions(
                [motion for _, motion in self._clips],
                fps=self.fps_spin.value(),
                loop=self.loop_check.isChecked(),
            )
            Motion3.from_dict(data)
            text = dumps_motion3(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except Motion3Error as exc:
            MessageBox("合并失败", str(exc), self.window()).exec()
            return
        except OSError as exc:
            MessageBox(
                "合并写入失败",
                f"{exc}\n\n请检查输出路径是否可写",
                self.window(),
            ).exec()
            return

        self.status_label.setText(f"已合并：{target.name}")
        self.statusMessage.emit(f"合并完成：{target}")
        MessageBox(
            "合并完成",
            f"已生成：\n{target}\n\n"
            f"总时长：{data['Meta']['Duration']:.2f} 秒\n"
            f"曲线数：{data['Meta']['CurveCount']}\n"
            f"关键帧点数：{data['Meta']['TotalPointCount']:,}",
            self.window(),
        ).exec()
