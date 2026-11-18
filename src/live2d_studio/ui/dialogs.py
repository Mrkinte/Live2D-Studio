"""Fluent dialogs shared by the studio UI."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import QDialog, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from .theme import CONTROL_HEIGHT, install_fluent_tooltip
from ..core.update import ReleaseInfo


class SaveChangesDialog(MessageBoxBase):
    """Three-button dialog: save / discard / cancel."""

    def __init__(self, title: str, content: str, parent=None) -> None:
        super().__init__(parent)
        self.result_action = "cancel"

        self.title_label = SubtitleLabel(title, self)
        self.content_label = BodyLabel(content, self)
        self.content_label.setWordWrap(True)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)

        self.discard_button = PushButton("不保存", self.buttonGroup)
        self.buttonLayout.insertWidget(1, self.discard_button)
        self.buttonLayout.setStretch(0, 1)
        self.buttonLayout.setStretch(1, 1)
        self.buttonLayout.setStretch(2, 1)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")

        self.yesButton.clicked.connect(self._mark_save)
        self.discard_button.clicked.connect(self._discard)
        self.widget.setFixedWidth(480)

    def _mark_save(self) -> None:
        self.result_action = "save"

    def _discard(self) -> None:
        self.result_action = "discard"
        self.reject()


def ask_save_changes(parent: QWidget, title: str, content: str) -> str:
    """Return ``save`` / ``discard`` / ``cancel``."""
    dialog = SaveChangesDialog(title, content, parent)
    dialog.exec()
    return dialog.result_action


def show_error(parent: QWidget, title: str, content: str) -> None:
    box = MessageBox(title, content, parent)
    box.cancelButton.hide()
    box.yesButton.setText("知道了")
    box.exec()


def _release_notes(text: str, limit: int = 700) -> str:
    notes = text.strip()
    if len(notes) > limit:
        notes = notes[:limit].rstrip() + "…"
    return notes


class UpdateAvailableDialog(MessageBoxBase):
    """展示 GitHub 最新 Release，并提供下载页面入口。"""

    def __init__(self, current: str, release: ReleaseInfo, parent=None) -> None:
        super().__init__(parent)
        self._page_url = release.page_url

        self.viewLayout.addWidget(SubtitleLabel("发现新版本", self))
        self.viewLayout.addWidget(BodyLabel(f"当前版本：v{current}", self))
        self.viewLayout.addWidget(BodyLabel(f"最新版本：{release.tag}", self))
        if release.published_at:
            self.viewLayout.addWidget(
                BodyLabel(f"发布时间：{release.published_at[:10]}", self)
            )

        notes = _release_notes(release.notes)
        if notes:
            notes_label = BodyLabel(notes, self)
            notes_label.setWordWrap(True)
            self.viewLayout.addWidget(notes_label)

        self.yesButton.setText("打开下载页面")
        self.cancelButton.setText("关闭")
        self.yesButton.clicked.connect(self._open_page)
        self.widget.setFixedWidth(560)

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._page_url))


def show_update_available(
        parent: QWidget, current: str, release: ReleaseInfo
) -> None:
    UpdateAvailableDialog(current, release, parent).exec()


def show_up_to_date(parent: QWidget, current: str) -> None:
    box = MessageBox("检查更新", f"当前已是最新版本（v{current}）", parent)
    box.cancelButton.hide()
    box.yesButton.setText("知道了")
    box.exec()


class AboutDialog(MessageBoxBase):
    """Version, dependencies, author and license of the studio."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        import live2d
        import moc3

        from .. import __version__

        self.viewLayout.addWidget(SubtitleLabel("关于 Live2D Studio", self))
        self.viewLayout.addWidget(
            BodyLabel(
                f"版本：{__version__}",
                self,
            )
        )
        self.viewLayout.addWidget(
            BodyLabel(
                "作者：Mrkinte",
                self,
            )
        )
        self.viewLayout.addWidget(
            BodyLabel(
                "Github：https://github.com/mrkinte/Live2D-Studio",
                self,
            )
        )
        self.viewLayout.addWidget(
            BodyLabel(
                "许可：MIT",
                self,
            )
        )
        self.viewLayout.addWidget(
            BodyLabel(
                f"支持解析 V3.00 / V3.03 / V4.00 / V4.02 / V5.00 版本的 MOC3 结构（基于 py-moc3 v{moc3.__version__}）",
                self,
            )
        )
        self.viewLayout.addWidget(
            BodyLabel(
                f"内置 Cubism SDK {live2d.__csm_version__}（非官方 runtime ，基于 live2d-py v{live2d.__version__}）",
                self,
            )
        )

        self.yesButton.setText("关闭")
        self.cancelButton.hide()


class TextInputDialog(MessageBoxBase):
    """Single-line text input dialog used for expression names."""

    def __init__(
            self,
            title: str,
            *,
            placeholder: str = "",
            default: str = "",
            parent=None,
    ) -> None:
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        self.line_edit = LineEdit(self)
        self.line_edit.setFixedHeight(CONTROL_HEIGHT)
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setText(default)
        self.line_edit.selectAll()
        self.viewLayout.addWidget(self.line_edit)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.line_edit.returnPressed.connect(self.accept)
        self.widget.setFixedWidth(420)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.line_edit.selectAll()


def ask_text(
        parent: QWidget,
        title: str,
        *,
        placeholder: str = "",
        default: str = "",
) -> str | None:
    """Return the trimmed input, or ``None`` when cancelled."""
    dialog = TextInputDialog(
        title, placeholder=placeholder, default=default, parent=parent
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    text = dialog.line_edit.text().strip()
    return text or None


class BindingInputDialog(MessageBoxBase):
    """Expression name plus the kind used when exporting the manifest."""

    def __init__(
            self,
            title: str,
            *,
            placeholder: str = "",
            default: str = "",
            kind: str = "",
            parent=None,
            show_name: bool = True,
    ) -> None:
        super().__init__(parent)
        self._show_name = show_name
        self.viewLayout.addWidget(SubtitleLabel(title, self))

        if show_name:
            self.viewLayout.addWidget(BodyLabel("名称", self))
            self.line_edit = LineEdit(self)
            self.line_edit.setFixedHeight(CONTROL_HEIGHT)
            self.line_edit.setPlaceholderText(placeholder)
            self.line_edit.setText(default)
            self.line_edit.selectAll()
            self.viewLayout.addWidget(self.line_edit)

        self.viewLayout.addWidget(BodyLabel("绑定类型", self))
        self.kind_combo = ComboBox(self)
        self.kind_combo.setFixedHeight(CONTROL_HEIGHT)
        self.kind_combo.addItem("自动（按参数数量判断）", userData="")
        self.kind_combo.addItem("表情", userData="expression")
        self.kind_combo.addItem("换装", userData="wardrobe")
        index = self.kind_combo.findData(kind) if kind else 0
        self.kind_combo.setCurrentIndex(max(0, index))
        self.kind_combo.setToolTip(
            "自动：单参数且同一参数有多个取值时归为换装，其余归为表情"
        )
        install_fluent_tooltip(self.kind_combo)
        self.viewLayout.addWidget(self.kind_combo)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        if show_name:
            self.line_edit.returnPressed.connect(self.accept)
            self.widget.setFixedWidth(440)
        else:
            self.widget.setFixedWidth(400)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._show_name:
            self.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self.line_edit.selectAll()


def ask_binding(
        parent: QWidget,
        title: str,
        *,
        placeholder: str = "",
        default: str = "",
        kind: str = "",
) -> tuple[str, str] | None:
    """Return ``(name, kind)``, or ``None`` when cancelled."""
    dialog = BindingInputDialog(
        title,
        placeholder=placeholder,
        default=default,
        kind=kind,
        parent=parent,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    text = dialog.line_edit.text().strip()
    if not text:
        return None
    return text, dialog.kind_combo.currentData() or ""


def ask_binding_kind(
        parent: QWidget,
        title: str = "修改绑定类型",
        *,
        kind: str = "",
) -> str | None:
    """Return the chosen kind ("" = auto), or ``None`` when cancelled."""
    dialog = BindingInputDialog(
        title, kind=kind, show_name=False, parent=parent
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.kind_combo.currentData() or ""
