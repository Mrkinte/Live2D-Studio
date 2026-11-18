from __future__ import annotations

import json
from pathlib import Path

import pytest

from live2d_studio.core import (
    Model3Error,
    motion_entries,
    read_model3,
    set_motion_sound,
)


def _model_dir(tmp_path: Path) -> Path:
    root = tmp_path / "model"
    root.mkdir()
    return root


def _write_model3(tmp_path: Path) -> Path:
    root = _model_dir(tmp_path)
    path = root / "aersasi.model3.json"
    data = {
        "Version": 3,
        "FileReferences": {
            "Motions": {
                "待机": [
                    {"File": "motions/idle.motion3.json", "FadeInTime": 1.5},
                    {
                        "File": "motions/idle2.motion3.json",
                        "Sound": "sounds/idle2.mp3",
                    },
                ],
                "摸头": [{"File": "motions/touch_head.motion3.json"}],
            }
        },
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def test_motion_entries_read_sound(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    entries = motion_entries(model3)
    assert [entry.index for entry in entries] == [0, 1, 0]
    second = next(e for e in entries if e.file == "motions/idle2.motion3.json")
    assert second.sound == "sounds/idle2.mp3"
    first = next(e for e in entries if e.file == "motions/idle.motion3.json")
    assert first.sound == ""
    assert first.path is None


def test_set_sound_uses_relative_reference_and_backup(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    root = model3.parent
    original = model3.read_bytes()
    sound = root / "sounds" / "new.wav"
    sound.parent.mkdir()
    sound.write_bytes(b"RIFF")

    reference, changed = set_motion_sound(
        model3, "待机", "motions/idle.motion3.json", sound
    )
    assert changed
    assert reference == "sounds/new.wav"

    data = read_model3(model3)
    idle = data["FileReferences"]["Motions"]["待机"][0]
    assert idle["Sound"] == "sounds/new.wav"
    assert idle["FadeInTime"] == 1.5  # other keys survive

    backups = list(root.glob("aersasi.model3.json.bak-*"))
    assert backups and backups[0].read_bytes() == original

    _, changed_again = set_motion_sound(
        model3, "待机", "motions/idle.motion3.json", sound
    )
    assert not changed_again
    assert len(list(root.glob("aersasi.model3.json.bak-*"))) == 1


def test_set_sound_copies_external_file(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    external = tmp_path / "external.wav"  # outside the model directory
    external.write_bytes(b"RIFF")

    reference, changed = set_motion_sound(
        model3, "摸头", "motions/touch_head.motion3.json", external
    )
    assert changed
    assert reference == "sounds/external.wav"
    copied = model3.parent / "sounds" / "external.wav"
    assert copied.read_bytes() == external.read_bytes()
    assert read_model3(model3)["FileReferences"]["Motions"]["摸头"][0]["Sound"] == (
        "sounds/external.wav"
    )


def test_set_sound_avoids_overwriting_other_sounds(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    external_dir = tmp_path / "other"
    external_dir.mkdir()
    first = external_dir / "same.wav"
    first.write_bytes(b"one")
    assert set_motion_sound(model3, "待机", "motions/idle.motion3.json", first)[0] == (
        "sounds/same.wav"
    )

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = second_dir / "same.wav"
    second.write_bytes(b"two")
    reference, changed = set_motion_sound(
        model3, "摸头", "motions/touch_head.motion3.json", second
    )
    assert changed
    assert reference == "sounds/same (2).wav"
    assert (model3.parent / "sounds" / "same.wav").read_bytes() == b"one"
    assert (model3.parent / "sounds" / "same (2).wav").read_bytes() == b"two"


def test_clear_sound(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    reference, changed = set_motion_sound(
        model3, "待机", "motions/idle2.motion3.json", None
    )
    assert changed and reference == ""
    idle2 = read_model3(model3)["FileReferences"]["Motions"]["待机"][1]
    assert "Sound" not in idle2

    _, changed_again = set_motion_sound(
        model3, "待机", "motions/idle2.motion3.json", None
    )
    assert not changed_again


def test_set_sound_rejects_unknown_motion(tmp_path: Path) -> None:
    model3 = _write_model3(tmp_path)
    with pytest.raises(Model3Error):
        set_motion_sound(model3, "不存在", "motions/idle.motion3.json", None)
    with pytest.raises(Model3Error):
        set_motion_sound(model3, "待机", "motions/missing.motion3.json", None)
