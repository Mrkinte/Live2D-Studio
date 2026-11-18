from __future__ import annotations

import hashlib
import shutil
import struct
from pathlib import Path

import pytest

from live2d_studio.core.moc3_document import (
    Moc3Document,
    Moc3Error,
    PatchError,
    UnsupportedMoc3Error,
    read_header,
)

MODEL_DIR = Path(
    r"C:\Users\Marjin\Documents\DeepSeek Harness\hide-moc3\test\aersasi_3"
)
MOC3 = MODEL_DIR / "aersasi_3.moc3"
MODEL3 = MODEL_DIR / "aersasi_3.model3.json"

pytestmark = pytest.mark.skipif(
    not MOC3.exists() or not MODEL3.exists(),
    reason="test/aersasi_3 fixture is not present",
)


@pytest.fixture()
def copied_moc3(tmp_path: Path) -> Path:
    target = tmp_path / "aersasi_3.moc3"
    shutil.copy2(MOC3, target)
    return target


def test_header_and_document(copied_moc3: Path) -> None:
    header = read_header(copied_moc3)
    assert header.version == 5
    assert header.version_name == "V5.00"
    assert header.endian == 0
    assert header.size == copied_moc3.stat().st_size
    assert header.sha256 == hashlib.sha256(copied_moc3.read_bytes()).hexdigest()

    doc = Moc3Document.open(copied_moc3)
    assert doc.part_count == 185
    assert doc.art_mesh_count == 1247
    assert len(doc.roots) == 8
    assert len(doc.unparented_meshes) == 0
    assert doc.parts[0].id == "Part122"
    assert doc.hidden_count == len(doc.initial_hidden)
    assert not doc.dirty
    assert not doc.file_changed


def test_from_model3_and_tree(copied_moc3: Path) -> None:
    model3 = copied_moc3.with_suffix(".model3.json")
    # The copied moc3 lives next to a minimal model3.json that points at it.
    model3.write_text(
        '{"Version": 3, "FileReferences": {"Moc": "aersasi_3.moc3"}}',
        encoding="utf-8",
    )
    doc = Moc3Document.from_model3(model3)
    assert doc.path == copied_moc3
    assert doc.model3_path == model3
    assert doc.parts[0].subtree_mesh_count == 6
    assert doc.roots[0].index == 0
    assert all(
        doc.is_hidden(i) == (i in doc.initial_hidden)
        for i in range(doc.part_count)
    )


def test_cdi3_display_names() -> None:
    doc = Moc3Document.from_model3(MODEL3)
    assert doc.display_info_path is not None
    assert len(doc.display_names) == 185
    part = doc.part_by_id["Part122"]
    assert doc.part_name(part.index) != part.id


def test_part_patch_apply_restore(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    original = copied_moc3.read_bytes()
    initial_hidden = set(doc.initial_hidden)
    target = next(i for i in range(doc.part_count) if i not in initial_hidden)

    doc.toggle_part(target, True)
    assert doc.dirty
    assert doc.changed_bytes() == 1
    patched = doc.build_patched_bytes()
    assert len(patched) == len(original)
    diff = [i for i, (a, b) in enumerate(zip(original, patched)) if a != b]
    assert len(diff) == 1

    backup = doc.apply()
    assert backup is not None
    assert backup.exists()
    assert backup.read_bytes() == original
    assert copied_moc3.read_bytes() == patched
    assert doc.saved_hidden == initial_hidden | {target}
    assert not doc.dirty
    assert doc.file_changed
    assert doc.hidden_count == len(initial_hidden) + 1

    restored_backup = doc.restore_original()
    assert restored_backup is not None
    assert copied_moc3.read_bytes() == original
    assert doc.saved_hidden == initial_hidden
    assert doc.pending_hidden == initial_hidden
    assert not doc.file_changed


def _has_nonzero_opacity(doc: Moc3Document, mesh_indices: tuple[int, ...]) -> bool:
    for mesh_index in mesh_indices:
        mesh = doc.art_meshes[mesh_index]
        for k in range(mesh.keyform_begin, mesh.keyform_begin + mesh.keyform_count):
            value = struct.unpack_from(
                "<f", doc.raw, doc.opacity_offset + k * 4
            )[0]
            if value != 0.0:
                return True
    return False


def test_deep_hide_zeroes_descendant_mesh_opacities(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    original = copied_moc3.read_bytes()
    initial_hidden = set(doc.initial_hidden)
    target = next(
        i
        for i in range(doc.part_count)
        if i not in initial_hidden
        and doc.part_mesh_indices[i]
        and _has_nonzero_opacity(doc, doc.part_mesh_indices[i])
    )
    mesh_indices = doc.part_mesh_indices[target]
    assert mesh_indices

    doc.toggle_part(target, True, deep=True)
    assert doc.is_hidden(target)
    assert doc.is_deep_hidden(target)
    patched = doc.build_patched_bytes()
    assert doc.changed_bytes() > 1

    for mesh_index in mesh_indices:
        mesh = doc.art_meshes[mesh_index]
        for k in range(mesh.keyform_begin, mesh.keyform_begin + mesh.keyform_count):
            offset = doc.opacity_offset + k * 4
            value = struct.unpack_from("<f", patched, offset)[0]
            assert value == 0.0

    backup = doc.apply()
    assert backup is not None
    assert doc.saved_deep == {target}
    assert doc.path.read_bytes() == patched

    doc.toggle_part(target, False)
    shown = doc.build_patched_bytes()
    for mesh_index in mesh_indices:
        mesh = doc.art_meshes[mesh_index]
        start = doc.opacity_offset + mesh.keyform_begin * 4
        end = start + mesh.keyform_count * 4
        assert shown[start:end] == original[start:end]
    doc.restore_original()
    assert doc.path.read_bytes() == original
    assert doc.pending_deep == set()


def test_deep_hide_can_be_undone_after_reopen(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    original = copied_moc3.read_bytes()
    target = next(
        i
        for i in range(doc.part_count)
        if i not in doc.initial_hidden
        and doc.part_mesh_indices[i]
        and _has_nonzero_opacity(doc, doc.part_mesh_indices[i])
    )
    mesh_indices = doc.part_mesh_indices[target]
    assert mesh_indices

    doc.toggle_part(target, True, deep=True)
    doc.apply()
    assert doc.path.read_bytes() != original

    # 模拟二次打开：新会话看不到 deep 状态，必须从备份恢复原始透明度。
    reopened = Moc3Document.open(copied_moc3)
    assert reopened.is_hidden(target)
    assert reopened.is_deep_hidden(target)

    reopened.toggle_part(target, False)
    patched = reopened.build_patched_bytes()
    for mesh_index in mesh_indices:
        mesh = reopened.art_meshes[mesh_index]
        start = reopened.opacity_offset + mesh.keyform_begin * 4
        end = start + mesh.keyform_count * 4
        assert patched[start:end] == original[start:end]

    reopened.apply()
    assert reopened.path.read_bytes() == patched
    assert reopened.saved_deep == set()
    assert not reopened.is_deep_hidden(target)


UNPARENTED_MOC3 = Path(
    r"C:\Users\Marjin\Documents\DeepSeek Harness\moc2cmo\out\no_bg.moc3"
)


@pytest.mark.skipif(
    not UNPARENTED_MOC3.exists(),
    reason="no_bg.moc3 fixture with unparented ArtMeshes is not present",
)
def test_unparented_mesh_can_be_hidden_and_undone_after_reopen(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "no_bg.moc3"
    shutil.copy2(UNPARENTED_MOC3, copied)
    doc = Moc3Document.open(copied)
    original = copied.read_bytes()
    mesh = next(
        mesh
        for mesh in doc.unparented_meshes
        if _has_nonzero_opacity(doc, (mesh.index,))
    )

    doc.toggle_mesh(mesh.index, True)
    patched = doc.build_patched_bytes()
    start = doc.opacity_offset + mesh.keyform_begin * 4
    end = start + mesh.keyform_count * 4
    assert patched[start:end] == b"\x00" * (end - start)

    doc.apply()
    assert doc.dirty is False

    # 二次打开后仍能识别该 Mesh 的隐藏状态，并还原原始透明度。
    reopened = Moc3Document.open(copied)
    assert reopened.is_mesh_hidden(mesh.index)
    reopened.toggle_mesh(mesh.index, False)
    patched = reopened.build_patched_bytes()
    assert patched[start:end] == original[start:end]

    reopened.apply()
    assert reopened.path.read_bytes() == patched
    assert reopened.saved_mesh_hidden == set()


def test_reset_pending_does_not_touch_file(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    original = copied_moc3.read_bytes()
    targets = [i for i in range(doc.part_count) if i not in doc.initial_hidden]
    doc.toggle_part(targets[0], True)
    doc.toggle_part(targets[1], True)
    assert doc.dirty
    doc.reset_pending()
    assert not doc.dirty
    assert copied_moc3.read_bytes() == original


def test_atomic_replace_falls_back_to_direct_patch(
    copied_moc3: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = Moc3Document.open(copied_moc3)
    original = copied_moc3.read_bytes()
    initial_hidden = set(doc.initial_hidden)
    target = next(i for i in range(doc.part_count) if i not in initial_hidden)
    doc.toggle_part(target, True)

    def deny_replace(*args, **kwargs):
        raise PermissionError("simulated ACL/AV denial")

    monkeypatch.setattr(
        "live2d_studio.core.moc3_document.os.replace", deny_replace
    )
    monkeypatch.setattr(
        "live2d_studio.core.moc3_document.time.sleep", lambda _: None
    )

    backup = doc.apply()
    assert backup is not None and backup.exists()
    assert backup.read_bytes() == original
    assert copied_moc3.read_bytes() == doc.build_patched_bytes()
    assert doc.saved_hidden == initial_hidden | {target}
    assert doc.file_changed


def test_refuses_to_overwrite_external_change(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    doc.toggle_part(0, True)
    external = copied_moc3.read_bytes() + b"\x00"
    copied_moc3.write_bytes(external)
    with pytest.raises(PatchError, match="已被其他程序修改"):
        doc.apply()
    assert copied_moc3.read_bytes() == external


def test_reload_after_external_change(copied_moc3: Path) -> None:
    doc = Moc3Document.open(copied_moc3)
    initial_hidden = set(doc.initial_hidden)
    targets = [i for i in range(doc.part_count) if i not in initial_hidden]
    mine, external = targets[0], targets[1]
    offset = doc.part_visible_offset

    doc.toggle_part(mine, True)
    external_bytes = bytearray(copied_moc3.read_bytes())
    struct.pack_into("<i", external_bytes, offset + external * 4, 0)
    copied_moc3.write_bytes(external_bytes)

    with pytest.raises(PatchError, match="已被其他程序修改"):
        doc.apply()

    doc.reload_from_disk(keep_pending=True)
    assert doc.is_hidden(external)
    assert doc.is_hidden(mine)
    backup = doc.apply()
    assert backup is not None
    assert doc.saved_hidden == initial_hidden | {mine, external}
    assert doc.path.read_bytes() == doc.build_patched_bytes()


def test_invalid_magic(tmp_path: Path) -> None:
    bad = tmp_path / "bad.moc3"
    bad.write_bytes(b"NOT3" + b"\x00" * 1000)
    with pytest.raises(UnsupportedMoc3Error):
        Moc3Document.open(bad)


def test_invalid_version(tmp_path: Path) -> None:
    bad = tmp_path / "bad.moc3"
    bad.write_bytes(b"MOC3" + bytes([99, 0]) + b"\x00" * 1000)
    with pytest.raises(Moc3Error):
        Moc3Document.open(bad)
