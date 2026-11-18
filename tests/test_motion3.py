from __future__ import annotations

import json
from pathlib import Path

import pytest

from live2d_studio.core.motion3 import (
    Motion3,
    Motion3Error,
    MotionCurve,
    _parse_segments,
    dumps_motion3,
    sample_motions,
)


def _motion(curves: list[dict], duration: float = 1.0, loop: bool = False) -> Motion3:
    data = {
        "Version": 3,
        "Meta": {
            "Duration": duration,
            "Fps": 30,
            "Loop": loop,
            "CurveCount": len(curves),
            "TotalSegmentCount": 0,
            "TotalPointCount": 0,
        },
        "Curves": curves,
    }
    return Motion3.from_dict(data)


def test_segment_parsing_and_interpolation() -> None:
    linear = MotionCurve("Parameter", "X", _parse_segments([0, 0, 0, 1, 10]))
    assert linear.value_at(0.0) == 0
    assert linear.value_at(0.5) == pytest.approx(5)
    assert linear.value_at(1.0) == 10

    stepped = MotionCurve("Parameter", "X", _parse_segments([0, 0, 2, 1, 10]))
    assert stepped.value_at(0.5) == 0
    assert stepped.value_at(1.0) == 10

    inverse = MotionCurve("Parameter", "X", _parse_segments([0, 0, 3, 1, 10]))
    assert inverse.value_at(0.5) == 10

    bezier = MotionCurve(
        "Parameter",
        "X",
        _parse_segments([0, 0, 1, 1 / 3, 0, 2 / 3, 10, 1, 10]),
    )
    assert bezier.value_at(1.0) == 10
    assert 0 <= bezier.value_at(0.5) <= 10


def test_load_real_motion() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "test"
        / "aersasi_3"
        / "motions"
        / "idle.motion3.json"
    )
    if not path.exists():
        path = (
            Path(r"C:\Users\Marjin\Documents\DeepSeek Harness\hide-moc3\test")
            / "aersasi_3"
            / "motions"
            / "idle.motion3.json"
        )
    if not path.exists():
        pytest.skip("test motion fixture is not present")
    motion = Motion3.load(path)
    assert motion.version == 3
    assert motion.duration > 0
    assert motion.curves
    assert motion.curve_map
    assert motion.fps > 0


def test_sample_two_motions_holds_missing_curves() -> None:
    a = _motion(
        [
            {"Target": "Parameter", "Id": "X", "Segments": [0, 0, 0, 1, 10]},
            {"Target": "Parameter", "Id": "Y", "Segments": [0, 5, 0, 1, 5]},
        ],
        duration=1.0,
    )
    b = _motion(
        [{"Target": "Parameter", "Id": "X", "Segments": [0, 10, 0, 1, 20]}],
        duration=1.0,
    )

    data = sample_motions([a, b], fps=10)
    merged = Motion3.from_dict(data)
    assert merged.duration == pytest.approx(2.0)
    assert merged.meta["CurveCount"] == 2

    x = merged.curve_map[("Parameter", "X")]
    y = merged.curve_map[("Parameter", "Y")]
    assert x.value_at(0.5) == pytest.approx(5.0, abs=0.1)
    assert x.value_at(1.5) == pytest.approx(15.0, abs=0.1)
    # Y is absent from B, so it holds its final value across the second half.
    assert y.value_at(1.5) == pytest.approx(5.0, abs=0.01)
    assert y.value_at(2.0) == pytest.approx(5.0, abs=0.01)


def test_serializer_avoids_scientific_notation() -> None:
    data = {
        "Version": 3,
        "Meta": {"Duration": 1.0, "Fps": 30, "CurveCount": 1},
        "Curves": [
            {
                "Target": "Parameter",
                "Id": "X",
                "Segments": [0, 0.0, 0, 0.5, 1.67e-08, 0, 1.0, -0.0],
            }
        ],
    }
    text = dumps_motion3(data)
    # Cubism's native parser fails on 1e-08-style numbers.
    assert "e-" not in text and "e+" not in text
    assert Motion3.from_dict(json.loads(text)).duration == 1.0


def test_sample_rejects_empty() -> None:
    with pytest.raises(Motion3Error):
        sample_motions([], fps=30)


def test_invalid_segment_type() -> None:
    with pytest.raises(Motion3Error):
        _parse_segments([0, 0, 99, 1, 1])
