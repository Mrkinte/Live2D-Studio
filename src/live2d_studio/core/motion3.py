"""Minimal motion3.json reader, sampler and merger.

This module is independent of Qt and live2d-py so it can be tested headlessly.
It supports the four Cubism segment types and all three target types
(Model, Parameter, PartOpacity).
"""

from __future__ import annotations

import copy
import json
import math
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

TARGETS = {"Model", "Parameter", "PartOpacity"}
SEGMENT_LINEAR = 0
SEGMENT_BEZIER = 1
SEGMENT_STEPPED = 2
SEGMENT_INVERSE_STEPPED = 3


class Motion3Error(Exception):
    """Invalid or unsupported motion3 data."""


@dataclass(frozen=True)
class MotionSegment:
    start_time: float
    start_value: float
    end_time: float
    end_value: float
    kind: int
    points: tuple[tuple[float, float], ...] = ()


@dataclass
class MotionCurve:
    target: str
    id: str
    segments: list[MotionSegment]
    fade_in: float = -1.0
    fade_out: float = -1.0
    _ends: list[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._ends = [seg.end_time for seg in self.segments]

    @property
    def end_time(self) -> float:
        return self.segments[-1].end_time if self.segments else 0.0

    def value_at(self, time: float) -> float:
        if not self.segments:
            return 0.0
        first = self.segments[0]
        if time <= first.start_time:
            return first.start_value
        index = bisect_left(self._ends, time)
        if index >= len(self.segments):
            return self.segments[-1].end_value
        seg = self.segments[index]
        if time <= seg.start_time:
            return seg.start_value
        if time >= seg.end_time:
            return seg.end_value
        if seg.kind == SEGMENT_STEPPED:
            return seg.start_value
        if seg.kind == SEGMENT_INVERSE_STEPPED:
            return seg.end_value
        if seg.kind == SEGMENT_LINEAR or len(seg.points) != 3:
            duration = seg.end_time - seg.start_time
            if duration <= 0:
                return seg.end_value
            u = (time - seg.start_time) / duration
            return seg.start_value + (seg.end_value - seg.start_value) * u

        p0 = (seg.start_time, seg.start_value)
        p1, p2, p3 = seg.points
        if time <= p0[0]:
            return p0[1]
        if time >= p3[0]:
            return p3[1]
        lo, hi = 0.0, 1.0
        for _ in range(24):
            u = (lo + hi) * 0.5
            x = _bezier(p0[0], p1[0], p2[0], p3[0], u)
            if x < time:
                lo = u
            else:
                hi = u
        u = (lo + hi) * 0.5
        return _bezier(p0[1], p1[1], p2[1], p3[1], u)


@dataclass
class Motion3:
    path: Path | None
    data: dict
    version: int
    meta: dict
    curves: list[MotionCurve]
    user_data: list[dict]

    @property
    def duration(self) -> float:
        duration = float(self.meta.get("Duration", 0.0) or 0.0)
        if duration <= 0:
            duration = max((c.end_time for c in self.curves), default=0.0)
        return max(0.0, duration)

    @property
    def fps(self) -> float:
        return float(self.meta.get("Fps", 30.0) or 30.0)

    @property
    def loop(self) -> bool:
        return bool(self.meta.get("Loop", False))

    @property
    def fade_in(self) -> float:
        return max(0.0, float(self.meta.get("FadeInTime", 0.0) or 0.0))

    @property
    def fade_out(self) -> float:
        return max(0.0, float(self.meta.get("FadeOutTime", 0.0) or 0.0))

    @property
    def curve_map(self) -> dict[tuple[str, str], MotionCurve]:
        return {(c.target, c.id): c for c in self.curves}

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)

    @classmethod
    def load(cls, path: str | Path) -> "Motion3":
        p = Path(path).expanduser().resolve()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise Motion3Error(f"无法读取 motion3 文件：{p}") from exc
        except json.JSONDecodeError as exc:
            raise Motion3Error(f"motion3 JSON 格式错误：{exc}") from exc
        return cls.from_dict(raw, p)

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> "Motion3":
        if not isinstance(data, dict):
            raise Motion3Error("motion3 根节点不是对象")
        version = int(data.get("Version", 0) or 0)
        if version != 3:
            raise Motion3Error(f"不支持的 motion3 Version：{version}")

        meta = data.get("Meta")
        if not isinstance(meta, dict):
            raise Motion3Error("motion3 缺少 Meta")
        raw_curves = data.get("Curves")
        if not isinstance(raw_curves, list) or not raw_curves:
            raise Motion3Error("motion3 没有 Curves")

        curves: list[MotionCurve] = []
        for item in raw_curves:
            if not isinstance(item, dict):
                raise Motion3Error("Curves 中存在非对象项")
            target = str(item.get("Target", ""))
            curve_id = str(item.get("Id", ""))
            if target not in TARGETS:
                raise Motion3Error(f"不支持的曲线 Target：{target}")
            if not curve_id:
                raise Motion3Error("曲线缺少 Id")
            segments = _parse_segments(item.get("Segments"))
            curves.append(
                MotionCurve(
                    target=target,
                    id=curve_id,
                    segments=segments,
                    fade_in=_optional_float(item.get("FadeInTime"), -1.0),
                    fade_out=_optional_float(item.get("FadeOutTime"), -1.0),
                )
            )

        user_data = data.get("UserData", [])
        if not isinstance(user_data, list):
            user_data = []

        return cls(
            path=path,
            data=copy.deepcopy(data),
            version=version,
            meta=copy.deepcopy(meta),
            curves=curves,
            user_data=copy.deepcopy(user_data),
        )


def _parse_segments(values) -> list[MotionSegment]:
    if not isinstance(values, list) or len(values) < 2:
        raise Motion3Error("曲线 Segments 为空或格式错误")
    try:
        t0 = float(values[0])
        v0 = float(values[1])
    except (TypeError, ValueError) as exc:
        raise Motion3Error("曲线起始点不是数字") from exc

    segments: list[MotionSegment] = []
    index = 2
    while index < len(values):
        try:
            kind = int(values[index])
        except (TypeError, ValueError) as exc:
            raise Motion3Error("段类型不是整数") from exc
        index += 1
        point_count = 3 if kind == SEGMENT_BEZIER else 1
        if kind not in (
            SEGMENT_LINEAR,
            SEGMENT_BEZIER,
            SEGMENT_STEPPED,
            SEGMENT_INVERSE_STEPPED,
        ):
            raise Motion3Error(f"未知段类型：{kind}")
        if index + point_count * 2 > len(values):
            raise Motion3Error("Segments 点数不足")
        points = []
        for _ in range(point_count):
            points.append((float(values[index]), float(values[index + 1])))
            index += 2
        t1, v1 = points[-1]
        if t1 < t0:
            raise Motion3Error("Segments 时间倒退")
        segments.append(
            MotionSegment(
                start_time=t0,
                start_value=v0,
                end_time=t1,
                end_value=v1,
                kind=kind,
                points=tuple(points),
            )
        )
        t0, v0 = t1, v1
    return segments


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        value = 0.0
    if abs(value) < 1e-9:
        value = 0.0
    # Cubism's native JSON parser rejects exponent notation such as 1e-08.
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def dumps_motion3(data: dict, indent: int = 2) -> str:
    """Serialize motion3 JSON without scientific notation for floats."""
    step = " " * max(0, indent)

    def encode(value, level: int) -> str:
        pad = step * level
        if isinstance(value, dict):
            if not value:
                return "{}"
            parts = [
                f"{pad}{step}{json.dumps(str(key), ensure_ascii=False)}: "
                f"{encode(item, level + 1)}"
                for key, item in value.items()
            ]
            return "{\n" + ",\n".join(parts) + f"\n{pad}}}"
        if isinstance(value, list):
            if not value:
                return "[]"
            parts = [f"{pad}{step}{encode(item, level + 1)}" for item in value]
            return "[\n" + ",\n".join(parts) + f"\n{pad}]"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return _format_float(value)
        if value is None:
            return "null"
        return json.dumps(str(value), ensure_ascii=False)

    return encode(data, 0)


def _optional_float(value, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bezier(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    inv = 1.0 - u
    return (
        inv * inv * inv * p0
        + 3.0 * inv * inv * u * p1
        + 3.0 * inv * u * u * p2
        + u * u * u * p3
    )


def sample_motions(
    motions: Iterable[Motion3],
    *,
    fps: int = 30,
    loop: bool = False,
    include_user_data: bool = True,
) -> dict:
    """Sample a sequence of motions into one linear-keyframe motion3 dict.

    Curves are sampled independently; parameters absent from a segment hold
    their previous value.  Runtime effects (physics, eye-blink, lip-sync) are
    not added; explicit Model/PartOpacity curves are preserved.
    """
    sources = list(motions)
    if not sources:
        raise Motion3Error("播放列表为空")
    if fps < 1 or fps > 240:
        raise Motion3Error("FPS 必须在 1..240 之间")

    durations = [m.duration for m in sources]
    total = sum(durations)
    if total <= 0:
        raise Motion3Error("所有 motion 的时长都是 0")

    keys = sorted({(c.target, c.id) for m in sources for c in m.curves})
    series: dict[tuple[str, str], list[tuple[float, float]]] = {key: [] for key in keys}
    last_values: dict[tuple[str, str], float] = {}

    cursor = 0.0
    for source_index, motion in enumerate(sources):
        start = cursor
        end = cursor + motion.duration
        frame_count = max(1, int(round(motion.duration * fps)))
        is_last = source_index == len(sources) - 1
        local_times = [frame / fps for frame in range(frame_count + 1)]
        if not is_last:
            # Leave the boundary point to the next motion; this also gives a
            # one-frame transition if the two motions disagree at the seam.
            local_times = local_times[:-1]
        curve_map = motion.curve_map

        for key in keys:
            curve = curve_map.get(key)
            if curve is not None:
                for local_t in local_times:
                    local_t = min(max(local_t, 0.0), motion.duration)
                    _append_point(series[key], start + local_t, curve.value_at(local_t))
                last_values[key] = curve.value_at(motion.duration)
            elif key in last_values:
                _append_point(series[key], start, last_values[key])
                _append_point(series[key], end, last_values[key])

        cursor = end

    curves_out = []
    total_segments = 0
    total_points = 0
    for key in keys:
        points = series[key]
        if not points:
            continue
        if len(points) == 1:
            points.append((total, points[0][1]))
        # Remove duplicate-time points, keeping the later value.
        cleaned: list[tuple[float, float]] = []
        for t, value in points:
            t = min(max(t, 0.0), total)
            if cleaned and abs(t - cleaned[-1][0]) < 1e-9:
                cleaned[-1] = (t, value)
            else:
                cleaned.append((t, value))
        if cleaned[-1][0] < total:
            cleaned.append((total, cleaned[-1][1]))
        if len(cleaned) < 2:
            continue

        segments_flat: list[float] = [cleaned[0][0], cleaned[0][1]]
        for t, value in cleaned[1:]:
            segments_flat.extend((0, t, value))
        curves_out.append({"Target": key[0], "Id": key[1], "Segments": segments_flat})
        total_segments += len(cleaned) - 1
        total_points += len(cleaned)

    if not curves_out:
        raise Motion3Error("合并结果没有可用曲线")

    meta = {
        "Duration": total,
        "Fps": int(fps),
        "Loop": bool(loop),
        "AreBeziersRestricted": True,
        "FadeInTime": 0.0,
        "FadeOutTime": 0.0,
        "CurveCount": len(curves_out),
        "TotalSegmentCount": total_segments,
        "TotalPointCount": total_points,
        "UserDataCount": 0,
        "TotalUserDataSize": 0,
    }

    user_data_out: list[dict] = []
    cursor = 0.0
    if include_user_data:
        for motion in sources:
            for entry in motion.user_data:
                if not isinstance(entry, dict):
                    continue
                try:
                    t = float(entry.get("Time", 0.0)) + cursor
                    value = str(entry.get("Value", ""))
                except (TypeError, ValueError):
                    continue
                if 0.0 <= t <= total:
                    user_data_out.append({"Time": t, "Value": value})
            cursor += motion.duration
    if user_data_out:
        meta["UserDataCount"] = len(user_data_out)
        meta["TotalUserDataSize"] = sum(
            len(item["Value"].encode("utf-8")) for item in user_data_out
        )

    data = {"Version": 3, "Meta": meta, "Curves": curves_out}
    if user_data_out:
        data["UserData"] = user_data_out
    return data


def _append_point(points: list[tuple[float, float]], t: float, value: float) -> None:
    if points and abs(t - points[-1][0]) < 1e-9:
        points[-1] = (t, value)
    else:
        points.append((t, value))
