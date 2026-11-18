"""Live2D preview canvas built on live2d-py and QOpenGLWidget."""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Sequence

import live2d
from PySide6.QtCore import QPointF, QRectF, QTimerEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget
from qfluentwidgets import qconfig

from .theme import (
    MESH_HIGHLIGHT_COLOR,
    PART_HIGHLIGHT_COLOR,
    STAGE_BORDER_COLOR,
    token,
)


class _HighlightOverlay(QWidget):
    """Transparent child widget drawn above the OpenGL canvas."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self._rect: QRectF | None = None
        self._label = ""
        self._color = MESH_HIGHLIGHT_COLOR
        self._stage_rect: QRectF | None = None

    def set_highlight(
            self, rect: QRectF | None, label: str = "", color: QColor = MESH_HIGHLIGHT_COLOR
    ) -> None:
        self._rect = rect
        self._label = label
        self._color = color
        self.update()

    def set_stage_rect(self, rect: QRectF | None) -> None:
        """Draw the MOC3 stage boundary as a dashed rectangle."""
        if self._stage_rect == rect:
            return
        self._stage_rect = rect
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._rect is None and self._stage_rect is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_stage(painter)
            self._paint_highlight(painter)
        finally:
            painter.end()

    def _paint_stage(self, painter: QPainter) -> None:
        if self._stage_rect is None:
            return
        pen = QPen(STAGE_BORDER_COLOR, 1.2, Qt.PenStyle.DashLine)
        pen.setDashPattern([4.0, 5.0])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._stage_rect)

    def _paint_highlight(self, painter: QPainter) -> None:
        if self._rect is None:
            return
        pen = QPen(self._color, 1.6, Qt.PenStyle.DashLine)
        pen.setDashPattern([5.0, 4.0])
        painter.setPen(pen)
        fill = QColor(self._color)
        fill.setAlpha(24)
        painter.setBrush(fill)
        painter.drawRect(self._rect)

        if not self._label:
            return
        font = QFont("Microsoft YaHei UI", 8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(self._label) + 10
        label_rect = QRectF(
            self._rect.left(),
            max(0.0, self._rect.top() - 19.0),
            min(text_width, max(40.0, float(self.width()))),
            18.0,
        )
        background = QColor(15, 17, 22, 215)
        painter.fillRect(label_rect, background)
        painter.setPen(self._color)
        painter.drawText(
            label_rect.adjusted(5, 0, -3, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )


class Live2DCanvas(QOpenGLWidget):
    """Render a model and apply preview-only Part opacity overrides."""

    part_hit = Signal(str)
    drawable_hit = Signal(str)
    binding_triggered = Signal(str, str)
    motion_started = Signal(str, int)
    motion_finished = Signal(str, int)
    model_loaded = Signal()
    load_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = None
        self._model_json: Path | None = None
        self._pending_model_json: Path | None = None
        self._hidden_parts: set[int] = set()
        self._param_overrides: dict[str, float] = {}
        self._click_bindings: list[tuple[str, str]] = []
        self._click_preview_enabled = False
        self._drawable_index_by_id: dict[str, int] = {}
        self._highlight_indices: tuple[int, ...] = ()
        self._highlight_label = ""
        self._highlight_color = MESH_HIGHLIGHT_COLOR
        self._stage_size: tuple[float, float] | None = None
        self._gl_ready = False
        self._timer_id = 0
        self._zoom = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._panning = False
        self._pan_anchor: QPointF | None = None
        self._mouse_down = False
        self._background = token("surface1").getRgbF()
        qconfig.themeChangedFinished.connect(self._on_theme_changed)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(420, 520)
        self.setToolTip("滚轮缩放，中键或 Ctrl+左键拖动平移，双击复位视图")

        self._highlight_overlay = _HighlightOverlay(self)
        self._highlight_overlay.setGeometry(self.rect())
        self._highlight_overlay.raise_()

    def _on_theme_changed(self) -> None:
        self._background = token("surface1").getRgbF()
        self.update()

    @property
    def model(self):
        return self._model

    @property
    def model_json(self) -> Path | None:
        return self._model_json

    def load_model(self, model_json: str | Path) -> None:
        path = Path(model_json).expanduser().resolve()
        self._model_json = path
        self._pending_model_json = path
        self._zoom = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        if self._gl_ready:
            self._load_model(path, context_is_current=False)

    def _load_model(self, path: Path, *, context_is_current: bool) -> bool:
        if not context_is_current:
            self.makeCurrent()
        try:
            old = self._model
            self._model = None
            if old is not None:
                del old
                gc.collect()

            model = live2d.Model()
            model.LoadModelJson(str(path))
            model.Resize(
                max(1, int(self.width() * self.devicePixelRatioF())),
                max(1, int(self.height() * self.devicePixelRatioF())),
            )
            model.SetScale(self._zoom)
            for index in self._hidden_parts:
                model.SetPartOpacity(index, 0.0)
            self._drawable_index_by_id = {
                drawable_id: index
                for index, drawable_id in enumerate(model.GetDrawableIds())
            }
            self._stage_size = self._read_stage_size(model)
            self._highlight_indices = ()
            self._highlight_overlay.set_highlight(None)
            self._model = model
            self._pending_model_json = None
            self.update()
            self.model_loaded.emit()
            return True
        except Exception as exc:  # live2d raises RuntimeError/ValueError
            self._model = None
            self._stage_size = None
            self._highlight_overlay.set_highlight(None)
            self._highlight_overlay.set_stage_rect(None)
            self.load_failed.emit(f"加载 Live2D 模型失败：\n{exc}")
            return False
        finally:
            if not context_is_current:
                self.doneCurrent()

    @staticmethod
    def _read_stage_size(model) -> tuple[float, float] | None:
        """Read the MOC3 canvas size, i.e. the model's stage in model units."""
        try:
            width, height = model.GetCanvasSize()
        except Exception:
            return None
        width, height = float(width), float(height)
        if width <= 0 or height <= 0:
            return None
        return width, height

    def reload_model(self) -> None:
        if self._model_json is not None:
            self.load_model(self._model_json)

    def shutdown(self) -> None:
        if self._timer_id:
            self.killTimer(self._timer_id)
            self._timer_id = 0
        self._model = None
        self._stage_size = None
        self._highlight_overlay.set_highlight(None)
        self._highlight_overlay.set_stage_rect(None)
        gc.collect()

    @property
    def hidden_parts(self) -> set[int]:
        return set(self._hidden_parts)

    def set_hidden_parts(self, indices: set[int] | list[int] | tuple[int, ...]) -> None:
        new_hidden = {int(i) for i in indices}
        if self._model is not None:
            for index in sorted(self._hidden_parts - new_hidden):
                self._model.SetPartOpacity(index, 1.0)
            for index in sorted(new_hidden - self._hidden_parts):
                self._model.SetPartOpacity(index, 0.0)
        self._hidden_parts = new_hidden
        self.update()

    @property
    def param_overrides(self) -> dict[str, float]:
        return dict(self._param_overrides)

    def set_param_override(self, parameter_id: str, value: float) -> None:
        """Pin one parameter to a value on every frame."""
        if not parameter_id:
            return
        self._param_overrides[str(parameter_id)] = float(value)
        self.update()

    def set_param_overrides(
            self, values: dict[str, float] | None
    ) -> None:
        new_values = {
            str(key): float(value) for key, value in (values or {}).items()
        }
        self._restore_parameters(set(self._param_overrides) - set(new_values))
        self._param_overrides = new_values
        self.update()

    def clear_param_overrides(self) -> None:
        self._restore_parameters(self._param_overrides.keys())
        self._param_overrides.clear()
        self.update()

    def _apply_param_overrides(self) -> None:
        if self._model is None:
            return
        for parameter_id, value in self._param_overrides.items():
            try:
                # Update() reloads the saved parameter values, so pinning must
                # write the saved slot as well as the working value.
                self._model.SetParamById(parameter_id, value)
                self._model.SetSaveParamById(parameter_id, value)
            except Exception:
                continue

    def _restore_parameters(self, parameter_ids) -> None:
        if self._model is None:
            return
        for parameter_id in parameter_ids:
            try:
                default = self._model.GetParamDefaultById(parameter_id)
                self._model.SetSaveParamById(parameter_id, default)
            except Exception:
                continue

    def set_click_bindings(self, bindings: list[tuple[str, str]]) -> None:
        """Set (HitArea Name, motion group) pairs for click preview."""
        self._click_bindings = [
            (str(area), str(group)) for area, group in bindings if area and group
        ]

    def set_click_preview_enabled(self, enabled: bool) -> None:
        self._click_preview_enabled = bool(enabled)
        if self._click_preview_enabled:
            # 预览点击动作时不叠加 Part / ArtMesh 虚线框
            self.clear_highlight()

    def highlight_mesh(self, mesh_id: str | None) -> None:
        """Draw a dashed bounding box around one ArtMesh."""
        if self._click_preview_enabled:
            return
        if not mesh_id:
            self.clear_highlight()
            return
        index = self._drawable_index_by_id.get(mesh_id)
        if index is None:
            self.clear_highlight()
            return
        self._set_highlight((index,), mesh_id, MESH_HIGHLIGHT_COLOR)

    def highlight_part(self, mesh_ids: Sequence[str], label: str) -> None:
        """Draw one dashed box around the union of a Part's ArtMeshes."""
        if self._click_preview_enabled:
            return
        indices = tuple(
            sorted(
                index
                for index in (
                    self._drawable_index_by_id.get(mesh_id) for mesh_id in mesh_ids
                )
                if index is not None
            )
        )
        if not indices:
            self.clear_highlight()
            return
        self._set_highlight(indices, label, PART_HIGHLIGHT_COLOR)

    def clear_highlight(self) -> None:
        self._highlight_indices = ()
        self._highlight_label = ""
        self._highlight_overlay.set_highlight(None)
        self.update()

    def _set_highlight(
            self, indices: tuple[int, ...], label: str, color: QColor
    ) -> None:
        self._highlight_indices = indices
        self._highlight_label = label
        self._highlight_color = color
        self._sync_highlight_overlay()
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        if self._model is not None:
            self._model.SetScale(1.0)
            self._model.SetOffset(0.0, 0.0)
        self.update()

    def _clamped_offset(self, x: float, y: float) -> tuple[float, float]:
        """Keep the model from being dragged completely out of the viewport."""
        limit = max(0.0, self._zoom - 1.0)
        return (
            max(-limit, min(limit, x)),
            max(-limit, min(limit, y)),
        )

    def _apply_view_transform(self) -> None:
        if self._model is not None:
            self._model.SetScale(self._zoom)
            self._model.SetOffset(self._offset_x, self._offset_y)
        self.update()

    def play_motion(self, group: str, number: int) -> None:
        if self._model is None:
            raise RuntimeError("还没有加载 Live2D 模型")
        self._model.StartMotion(
            group,
            int(number),
            live2d.MotionPriority.FORCE,
            onFinish=lambda finished_group, finished_no: self.motion_finished.emit(
                str(finished_group), int(finished_no)
            ),
        )
        self.motion_started.emit(group, int(number))

    def stop_motions(self) -> None:
        if self._model is not None:
            try:
                self._model.StopAllMotions()
            except Exception:
                pass

    def initializeGL(self) -> None:
        try:
            live2d.glInit()
            self._gl_ready = True
        except Exception as exc:
            self.load_failed.emit(f"OpenGL 初始化失败：\n{exc}")
            return

        self._highlight_overlay.raise_()
        self._timer_id = self.startTimer(16)
        if self._pending_model_json is not None:
            self._load_model(self._pending_model_json, context_is_current=True)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._highlight_overlay.setGeometry(self.rect())
        self._highlight_overlay.raise_()
        self._sync_highlight_overlay()

    def resizeGL(self, w: int, h: int) -> None:  # noqa: N802
        if self._model is not None:
            self._model.Resize(
                max(1, int(self.width() * self.devicePixelRatioF())),
                max(1, int(self.height() * self.devicePixelRatioF())),
            )

    def paintGL(self) -> None:  # noqa: N802
        live2d.clearBuffer(*self._background)
        if self._model is None:
            return
        try:
            self._apply_param_overrides()
            self._model.Update()
            for index in self._hidden_parts:
                self._model.SetPartOpacity(index, 0.0)
            self._model.Draw()
            self._sync_highlight_overlay()
        except Exception:
            # A transient GL/driver error should not kill the Qt event loop.
            return

    def _sync_highlight_overlay(self) -> None:
        if self._model is None:
            self._highlight_overlay.set_highlight(None)
            self._highlight_overlay.set_stage_rect(None)
            return
        try:
            mvp = self._model.GetMvp()
        except Exception:
            mvp = None
        if not mvp or len(mvp) < 16:
            self._highlight_overlay.set_highlight(None)
            self._highlight_overlay.set_stage_rect(None)
            return

        self._sync_stage_rect(mvp)
        if not self._highlight_indices:
            self._highlight_overlay.set_highlight(None)
            return

        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        found = False
        for index in self._highlight_indices:
            try:
                vertices = self._model.GetDrawableVertices(index)
            except Exception:
                continue
            if not vertices:
                continue
            for i in range(0, len(vertices) - 1, 2):
                px, py = self._model_to_screen(
                    mvp, float(vertices[i]), float(vertices[i + 1])
                )
                min_x = min(min_x, px)
                max_x = max(max_x, px)
                min_y = min(min_y, py)
                max_y = max(max_y, py)
                found = True
        if not found:
            self._highlight_overlay.set_highlight(None)
            return

        rect = QRectF(
            min_x, min_y, max(1.0, max_x - min_x), max(1.0, max_y - min_y)
        )
        self._highlight_overlay.set_highlight(
            rect, self._highlight_label, self._highlight_color
        )

    def _sync_stage_rect(self, mvp) -> None:
        """Map the MOC3 stage corners into widget space for the dashed border."""
        if not self._stage_size:
            self._highlight_overlay.set_stage_rect(None)
            return
        # Live2D 的模型坐标系以画布中心为原点，因此舞台范围为 ±canvas/2
        width, height = self._stage_size
        half_w, half_h = width / 2.0, height / 2.0
        corners = (
            (-half_w, -half_h),
            (half_w, -half_h),
            (half_w, half_h),
            (-half_w, half_h),
        )
        points = [self._model_to_screen(mvp, x, y) for x, y in corners]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self._highlight_overlay.set_stage_rect(
            QRectF(
                min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys))
            )
        )

    def _model_to_screen(self, mvp, x: float, y: float) -> tuple[float, float]:
        """Project a model-space coordinate into widget pixels."""
        ndc_x = mvp[0] * x + mvp[4] * y + mvp[12]
        ndc_y = mvp[1] * x + mvp[5] * y + mvp[13]
        return (ndc_x + 1.0) * 0.5 * self.width(), (1.0 - ndc_y) * 0.5 * self.height()

    def timerEvent(self, event: QTimerEvent) -> None:  # noqa: N802
        if self.isVisible():
            self.update()

    def _physical_pos(self, event: QMouseEvent) -> tuple[float, float]:
        dpr = self.devicePixelRatioF()
        pos = event.position()
        return pos.x() * dpr, pos.y() * dpr

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._model is None:
            return
        is_pan = event.button() == Qt.MouseButton.MiddleButton or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        )
        if is_pan:
            self._panning = True
            self._pan_anchor = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._mouse_down = True
        x, y = self._physical_pos(event)

        if self._click_preview_enabled:
            for area, group in self._click_bindings:
                try:
                    hit = self._model.IsAreaHit(area, x, y)
                except Exception:
                    hit = False
                if not hit:
                    continue
                try:
                    self.play_motion(group, 0)
                    self.binding_triggered.emit(area, group)
                except Exception:
                    pass
                return

        try:
            parts = self._model.HitPart(x, y, True)
        except Exception:
            parts = []
        if parts:
            self.part_hit.emit(parts[0])
            return
        try:
            drawables = self._model.HitDrawable(x, y, True)
        except Exception:
            drawables = []
        if drawables:
            self.drawable_hit.emit(drawables[0])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning and self._pan_anchor is not None:
            pos = event.position()
            dx = pos.x() - self._pan_anchor.x()
            dy = pos.y() - self._pan_anchor.y()
            self._pan_anchor = pos
            # Screen pixels -> normalized model offset (viewport spans [-1, 1]).
            self._offset_x, self._offset_y = self._clamped_offset(
                self._offset_x + 2.0 * dx / max(1, self.width()),
                self._offset_y - 2.0 * dy / max(1, self.height()),
            )
            self._apply_view_transform()
            return
        if self._model is None or not self._mouse_down:
            return
        x, y = self._physical_pos(event)
        try:
            self._model.Drag(x, y)
        except Exception:
            pass

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning and event.button() in (
                Qt.MouseButton.MiddleButton,
                Qt.MouseButton.LeftButton,
        ):
            self._panning = False
            self._pan_anchor = None
            self.unsetCursor()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_down = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._model is None:
            return
        delta = event.angleDelta().y()
        if delta:
            factor = 1.1 if delta > 0 else 1 / 1.1
            self._zoom = max(0.35, min(4.0, self._zoom * factor))
            self._offset_x, self._offset_y = self._clamped_offset(
                self._offset_x, self._offset_y
            )
            self._apply_view_transform()
            event.accept()

    def check_consistency(self, moc_path: str | Path) -> bool | None:
        if self._model is None:
            return None
        self.makeCurrent()
        try:
            return bool(self._model.HasMocConsistencyFromFile(str(moc_path)))
        except Exception:
            return None
        finally:
            self.doneCurrent()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
