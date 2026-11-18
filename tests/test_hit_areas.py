from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from live2d_studio.core import (
    HitAreaBinding,
    HitAreaError,
    Moc3Document,
    camel_to_snake,
    group_for_action,
    merge_bindings,
    motion_groups,
    read_hit_areas,
    read_model3,
    save_hit_areas,
    suggest_touch_bindings,
    validate_bindings,
)

MODEL_DIR = Path(
    r"C:\Users\Marjin\Documents\DeepSeek Harness\hide-moc3\test\aersasi_3"
)
MODEL3 = MODEL_DIR / "aersasi_3.model3.json"
MOC3 = MODEL_DIR / "aersasi_3.moc3"

pytestmark = pytest.mark.skipif(
    not MODEL3.exists() or not MOC3.exists(),
    reason="aersasi_3 fixture is not present",
)


def _suggestions(model3: Path):
    doc = Moc3Document.from_model3(model3)
    groups = motion_groups(read_model3(model3))
    suggestions, skipped = suggest_touch_bindings(
        [mesh.id for mesh in doc.art_meshes], groups
    )
    return suggestions, skipped


def _copy_model(tmp_path: Path) -> Path:
    dst = tmp_path / "aersasi_3"
    dst.mkdir()
    shutil.copy2(MODEL3, dst / MODEL3.name)
    shutil.copy2(MOC3, dst / MOC3.name)
    return dst / MODEL3.name


def test_camel_to_snake() -> None:
    assert camel_to_snake("TouchHead") == "touch_head"
    assert camel_to_snake("TouchIdle12") == "touch_idle12"
    assert camel_to_snake("TouchSpecial") == "touch_special"


def test_suggest_touch_bindings() -> None:
    suggestions, skipped = _suggestions(MODEL3)
    assert suggestions
    meshes = {binding.mesh_id for binding in suggestions}
    assert "TouchHead" in meshes
    assert "TouchBody" in meshes
    assert all(binding.motion_group == binding.name for binding in suggestions)
    assert all(binding.mesh_id.startswith("Touch") for binding in suggestions)
    # Many TouchDrag meshes have no matching motion group in this model.
    assert skipped


def test_suggest_skips_missing_group() -> None:
    groups = {"touch_head": ("touch_head.motion3.json",)}
    suggested, skipped = suggest_touch_bindings(
        ["TouchHead", "TouchBody", "ArtMesh1"], groups
    )
    assert [binding.mesh_id for binding in suggested] == ["TouchHead"]
    assert any("TouchBody" in message for message in skipped)


def test_chinese_motion_group_and_name() -> None:
    groups = {"摸头": ("motions/touch_head.motion3.json",)}
    assert group_for_action("touch_head", groups) == "摸头"
    suggested, skipped = suggest_touch_bindings(["TouchHead"], groups)
    assert [(binding.mesh_id, binding.name) for binding in suggested] == [
        ("TouchHead", "摸头")
    ]
    assert not skipped
    errors = validate_bindings(
        [HitAreaBinding("TouchHead", "摸头", "摸头")],
        ["TouchHead"],
        groups=groups,
    )
    assert not errors


def test_merge_does_not_duplicate() -> None:
    existing = [HitAreaBinding("TouchHead", "touch_head", "touch_head")]
    additions = [
        HitAreaBinding("TouchHead", "touch_head", "touch_head"),
        HitAreaBinding("TouchBody", "touch_body", "touch_body"),
    ]
    merged = merge_bindings(existing, additions)
    assert [binding.mesh_id for binding in merged] == ["TouchHead", "TouchBody"]


def test_validate_catches_bad_mesh_and_duplicates() -> None:
    errors = validate_bindings(
        [
            HitAreaBinding("Missing", "touch_missing", "touch_missing"),
            HitAreaBinding("TouchHead", "touch_head", "touch_head"),
            HitAreaBinding("TouchHead", "touch_head", "touch_head"),
        ],
        ["TouchHead"],
    )
    assert any("ArtMesh 不存在" in error for error in errors)
    assert any("重复 ArtMesh" in error for error in errors)
    assert any("重复 Name" in error for error in errors)


def test_save_hit_areas_with_backup(tmp_path: Path) -> None:
    model3 = _copy_model(tmp_path)
    moc3 = model3.with_name(MOC3.name)
    original_model3 = model3.read_bytes()
    original_moc3_hash = hashlib.sha256(moc3.read_bytes()).hexdigest()

    data = read_model3(model3)
    doc = Moc3Document.from_model3(model3)
    suggestions, _ = _suggestions(model3)
    bindings = merge_bindings(read_hit_areas(data), suggestions)
    backup, saved = save_hit_areas(
        model3,
        bindings,
        art_mesh_ids=[mesh.id for mesh in doc.art_meshes],
        groups=motion_groups(data),
    )

    result = read_model3(model3)
    assert "HitAreas" in result
    assert len(result["HitAreas"]) == len(saved)
    assert read_hit_areas(result)
    assert backup.exists()
    assert backup.read_bytes() == original_model3
    assert hashlib.sha256(moc3.read_bytes()).hexdigest() == original_moc3_hash


def test_save_rejects_invalid_binding(tmp_path: Path) -> None:
    model3 = _copy_model(tmp_path)
    doc = Moc3Document.from_model3(model3)
    with pytest.raises(HitAreaError):
        save_hit_areas(
            model3,
            [HitAreaBinding("NoSuchMesh", "touch_none", "touch_none")],
            art_mesh_ids=[mesh.id for mesh in doc.art_meshes],
            groups=motion_groups(read_model3(model3)),
        )
