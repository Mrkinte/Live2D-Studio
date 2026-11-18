"""Read and safely patch Live2D MOC3 part visibility.

``py-moc3`` is used for parsing only.  Its writer is deliberately avoided:
it does not round-trip every real model, so all edits are same-length byte
patches applied to the original file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from moc3 import Moc3

from .params import ParameterInfo, read_parameter_names, read_parameters

MAGIC = b"MOC3"
VERSION_NAMES = {
    1: "V3.00",
    2: "V3.03",
    3: "V4.00",
    4: "V4.02",
    5: "V5.00",
}


class Moc3Error(Exception):
    """Base error for loading or patching a MOC3 file."""


class UnsupportedMoc3Error(Moc3Error):
    """The file is not a supported little-endian MOC3 model."""


class PatchError(Moc3Error):
    """A safe patch could not be produced or written."""


@dataclass(frozen=True)
class Moc3Header:
    version: int
    endian: int
    size: int
    sha256: str

    @property
    def version_name(self) -> str:
        return VERSION_NAMES.get(self.version, f"V{self.version}")


@dataclass(frozen=True)
class PartInfo:
    index: int
    id: str
    parent_index: int
    child_indices: tuple[int, ...]
    direct_mesh_count: int
    subtree_mesh_count: int


@dataclass(frozen=True)
class ArtMeshInfo:
    index: int
    id: str
    parent_part_index: int
    keyform_begin: int
    keyform_count: int


def _parse_header(raw: bytes) -> Moc3Header:
    if len(raw) < 64:
        raise UnsupportedMoc3Error("文件太小，不是有效的 MOC3 文件")
    if raw[:4] != MAGIC:
        raise UnsupportedMoc3Error("文件头缺少 MOC3 magic")
    version = raw[4]
    endian = raw[5]
    if version not in VERSION_NAMES:
        raise UnsupportedMoc3Error(f"不支持的 MOC3 版本号：{version}")
    if endian != 0:
        raise UnsupportedMoc3Error("暂不支持 big-endian MOC3 文件")
    return Moc3Header(
        version=version,
        endian=endian,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def read_header(path: str | Path) -> Moc3Header:
    """Read and validate only the MOC3 header."""
    p = Path(path).expanduser().resolve()
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise Moc3Error(f"无法读取文件：{p}") from exc
    return _parse_header(raw)


class Moc3Document:
    """An in-memory MOC3 document with a pending part-visibility edit set."""

    def __init__(
        self,
        path: Path,
        model3_path: Path | None,
        raw: bytes,
        header: Moc3Header,
        moc: Moc3,
        part_visible_offset: int,
        opacity_offset: int,
        parameter_default_offset: int | None,
        parts: list[PartInfo],
        art_meshes: list[ArtMeshInfo],
        initial_hidden: set[int],
        initial_defaults: dict[str, float],
    ) -> None:
        self.path = path
        self.model3_path = model3_path
        self.raw = raw
        self.original_raw = raw
        self.header = header
        self.moc = moc
        self.part_visible_offset = part_visible_offset
        self.opacity_offset = opacity_offset
        self.parameter_default_offset = parameter_default_offset
        self.parts = parts
        self.art_meshes = art_meshes
        self.part_by_id = {p.id: p for p in parts}
        self.mesh_by_id = {m.id: m for m in art_meshes}
        self.initial_hidden = set(initial_hidden)
        self.saved_hidden = set(initial_hidden)
        self.pending_hidden = set(initial_hidden)
        self.saved_deep: set[int] = set()
        self.pending_deep: set[int] = set()
        self.initial_deep: set[int] = set()
        # Unparented ArtMeshes have no Part to carry their visibility, so they
        # are hidden by zeroing their own opacity keyforms.
        self.initial_mesh_hidden: set[int] = set()
        self.saved_mesh_hidden: set[int] = set()
        self.pending_mesh_hidden: set[int] = set()
        # Original opacity keyforms recovered from a pre-hide backup, keyed by
        # the Part that owns the deep hide / by the hidden ArtMesh.
        self._deep_restore: dict[int, dict[int, tuple[float, ...]]] = {}
        self._mesh_restore: dict[int, tuple[float, ...]] = {}
        self.backup_path: Path | None = None
        self.part_mesh_indices = self._build_part_mesh_map()
        self.parameters = read_parameters(moc)
        self.parameter_by_id = {p.id: p for p in self.parameters}
        # Default values are patched in place from ``parameter.default_values``.
        self.initial_defaults = dict(initial_defaults)
        self.saved_defaults = dict(initial_defaults)
        self.pending_defaults = dict(initial_defaults)
        self.display_names: dict[str, str] = {}
        self.parameter_names: dict[str, str] = {}
        self.display_info_path: Path | None = None

    def _build_part_mesh_map(self) -> dict[int, tuple[int, ...]]:
        by_parent: dict[int, list[int]] = {}
        for mesh in self.art_meshes:
            by_parent.setdefault(mesh.parent_part_index, []).append(mesh.index)

        result: dict[int, tuple[int, ...]] = {}
        for part in self.parts:
            indices: list[int] = []
            stack = [part.index]
            while stack:
                current = stack.pop()
                indices.extend(by_parent.get(current, ()))
                stack.extend(self.parts[current].child_indices)
            result[part.index] = tuple(indices)
        return result

    @staticmethod
    def _opacity_values(raw: bytes, offset: int, mesh: ArtMeshInfo) -> tuple[float, ...]:
        if mesh.keyform_count == 0:
            return ()
        return struct.unpack_from(
            f"<{mesh.keyform_count}f", raw, offset + mesh.keyform_begin * 4
        )

    def _part_opacities_all_zero(self, index: int) -> bool:
        for mesh_index in self.part_mesh_indices.get(index, ()):
            mesh = self.art_meshes[mesh_index]
            if any(value != 0.0 for value in self._opacity_values(self.raw, self.opacity_offset, mesh)):
                return False
        return True

    def _mesh_opacities_all_zero(self, mesh: ArtMeshInfo) -> bool:
        return all(
            value == 0.0
            for value in self._opacity_values(self.raw, self.opacity_offset, mesh)
        )

    def _backup_matches_structure(self, moc: Moc3, raw: bytes) -> int | None:
        """Return the opacity section offset if a backup can restore this file."""
        try:
            if list(moc.part_ids) != [part.id for part in self.parts]:
                return None
            if list(moc.art_mesh_ids) != [mesh.id for mesh in self.art_meshes]:
                return None
            begins = list(moc["art_mesh.keyform_begin_indices"])
            counts = list(moc["art_mesh.keyform_counts"])
            if len(begins) != len(self.art_meshes) or len(counts) != len(self.art_meshes):
                return None
            for index, mesh in enumerate(self.art_meshes):
                if int(begins[index]) != mesh.keyform_begin:
                    return None
                if int(counts[index]) != mesh.keyform_count:
                    return None
            return self._locate_section(
                moc, raw, "art_mesh_keyform.opacities", len(moc["art_mesh_keyform.opacities"])
            )
        except (KeyError, TypeError, ValueError, Moc3Error):
            return None

    def _recover_hidden_from_backups(
        self, part_candidates: list[int], mesh_candidates: list[int]
    ) -> tuple[dict[int, dict[int, tuple[float, ...]]], dict[int, tuple[float, ...]]]:
        """Read pre-hide opacity keyforms from the newest usable backup.

        Deep-hiding a Part zeroes every ArtMesh opacity keyform below it, and
        hiding an unparented ArtMesh zeroes its own keyforms.  A reopen has no
        in-file baseline left, so the newest backup where the keyforms are
        still non-zero is used to make the hide reversible.
        """
        part_restore: dict[int, dict[int, tuple[float, ...]]] = {}
        mesh_restore: dict[int, tuple[float, ...]] = {}
        pending_parts = set(part_candidates)
        pending_meshes = set(mesh_candidates)
        if not pending_parts and not pending_meshes:
            return part_restore, mesh_restore

        try:
            backups = sorted(
                self.path.parent.glob(f"{self.path.name}.bak-*"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return part_restore, mesh_restore

        for backup in backups:
            if not pending_parts and not pending_meshes:
                break
            try:
                raw = backup.read_bytes()
            except OSError:
                continue
            try:
                moc = Moc3.from_bytes(raw)
            except Exception:
                continue
            offset = self._backup_matches_structure(moc, raw)
            if offset is None:
                continue

            for part_index in list(pending_parts):
                values_by_mesh: dict[int, tuple[float, ...]] = {}
                has_nonzero = False
                for mesh_index in self.part_mesh_indices.get(part_index, ()):
                    mesh = self.art_meshes[mesh_index]
                    values = self._opacity_values(raw, offset, mesh)
                    if any(value != 0.0 for value in values):
                        has_nonzero = True
                    values_by_mesh[mesh_index] = values
                if has_nonzero:
                    part_restore[part_index] = values_by_mesh
                    pending_parts.discard(part_index)

            for mesh_index in list(pending_meshes):
                mesh = self.art_meshes[mesh_index]
                values = self._opacity_values(raw, offset, mesh)
                if any(value != 0.0 for value in values):
                    mesh_restore[mesh_index] = values
                    pending_meshes.discard(mesh_index)
        return part_restore, mesh_restore

    def _load_hidden_state(self) -> None:
        """Re-detect deep-hidden Parts and hidden unparented ArtMeshes."""
        part_candidates = [
            part.index
            for part in self.parts
            if part.index in self.initial_hidden
            and part.subtree_mesh_count > 0
            and self._part_opacities_all_zero(part.index)
        ]
        mesh_candidates = [
            mesh.index
            for mesh in self.unparented_meshes
            if self._mesh_opacities_all_zero(mesh)
        ]
        self._deep_restore, self._mesh_restore = self._recover_hidden_from_backups(
            part_candidates, mesh_candidates
        )
        self.initial_deep = set(self._deep_restore)
        self.saved_deep = set(self.initial_deep)
        self.pending_deep = set(self.initial_deep)
        self.initial_mesh_hidden = set(self._mesh_restore)
        self.saved_mesh_hidden = set(self.initial_mesh_hidden)
        self.pending_mesh_hidden = set(self.initial_mesh_hidden)

    @classmethod
    def open(cls, path: str | Path) -> "Moc3Document":
        """Open a .moc3 file directly."""
        p = Path(path).expanduser().resolve()
        try:
            raw = p.read_bytes()
        except OSError as exc:
            raise Moc3Error(f"无法读取文件：{p}") from exc

        header = _parse_header(raw)

        try:
            moc = Moc3.from_bytes(raw)
        except Exception as exc:  # py-moc3 raises a variety of exceptions
            raise Moc3Error(f"py-moc3 无法解析该文件：{exc}") from exc

        part_count = len(moc.part_ids)
        art_mesh_count = len(moc.art_mesh_ids)
        if part_count == 0:
            raise Moc3Error("该 MOC3 文件没有 Part")
        if art_mesh_count == 0:
            raise Moc3Error("该 MOC3 文件没有 ArtMesh")

        part_visible_offset = cls._locate_section(moc, raw, "part.visibles", part_count)
        values = struct.unpack_from(f"<{part_count}i", raw, part_visible_offset)
        if any(v not in (0, 1) for v in values):
            raise PatchError("part.visibles 区域包含非 0/1 值，拒绝修改")

        opacity_values = list(moc["art_mesh_keyform.opacities"])
        opacity_offset = cls._locate_section(
            moc, raw, "art_mesh_keyform.opacities", len(opacity_values)
        )
        parameter_default_offset = cls._locate_defaults(moc, raw)

        parts = cls._build_parts(moc, part_count, art_mesh_count)
        art_meshes = cls._build_art_meshes(moc, part_count)
        for mesh in art_meshes:
            if (
                mesh.keyform_begin < 0
                or mesh.keyform_begin + mesh.keyform_count > len(opacity_values)
            ):
                raise PatchError(f"ArtMesh {mesh.id} 的透明度关键帧越界")
        initial_hidden = {i for i, value in enumerate(values) if value == 0}

        doc = cls(
            path=p,
            model3_path=None,
            raw=raw,
            header=header,
            moc=moc,
            part_visible_offset=part_visible_offset,
            opacity_offset=opacity_offset,
            parameter_default_offset=parameter_default_offset,
            parts=parts,
            art_meshes=art_meshes,
            initial_hidden=initial_hidden,
            initial_defaults=cls._read_defaults(moc),
        )
        doc._load_hidden_state()
        # A same-stem .cdi3.json is the common Cubism layout; model3.json can
        # point to a differently named file and will override this below.
        doc._load_display_info_file(p.with_suffix(".cdi3.json"))
        return doc

    @classmethod
    def from_model3(cls, model3_path: str | Path) -> "Moc3Document":
        """Open the MOC3 file referenced by a Live2D .model3.json."""
        p = Path(model3_path).expanduser().resolve()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise Moc3Error(f"无法读取 model3.json：{p}") from exc
        except json.JSONDecodeError as exc:
            raise Moc3Error(f"model3.json 格式错误：{exc}") from exc

        moc_ref = (data.get("FileReferences") or {}).get("Moc")
        if not moc_ref:
            raise Moc3Error("model3.json 中没有 FileReferences.Moc")

        moc_path = (p.parent / moc_ref).resolve()
        if not moc_path.exists():
            raise Moc3Error(f"model3.json 引用的 MOC3 不存在：{moc_path}")

        doc = cls.open(moc_path)
        doc.model3_path = p
        display_ref = (data.get("FileReferences") or {}).get("DisplayInfo")
        if display_ref:
            display_path = (p.parent / display_ref).resolve()
            if display_path.exists():
                doc._load_display_info_file(display_path)
        return doc

    def _load_display_info_file(self, path: Path) -> None:
        """Load Part and parameter display names from cdi3.json."""
        try:
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        raw_parts = data.get("Parts")
        names: dict[str, str] = {}
        if isinstance(raw_parts, list):
            for item in raw_parts:
                if not isinstance(item, dict):
                    continue
                part_id = item.get("Id")
                name = item.get("Name")
                if not isinstance(part_id, str) or not isinstance(name, str):
                    continue
                part_id = part_id.strip()
                name = name.strip()
                if part_id and name and part_id in self.part_by_id:
                    names[part_id] = name

        parameter_names = read_parameter_names(path)
        if parameter_names:
            self.parameter_names = {
                parameter_id: name
                for parameter_id, name in parameter_names.items()
                if parameter_id in self.parameter_by_id
            }
        if names:
            self.display_names = names
        if names or self.parameter_names:
            self.display_info_path = path

    def part_name(self, index: int) -> str:
        part = self.parts[index]
        return self.display_names.get(part.id, part.id)

    def parameter_name(self, parameter_id: str) -> str:
        return self.parameter_names.get(parameter_id, parameter_id)

    @staticmethod
    def _locate_section(moc: Moc3, raw: bytes, name: str, count: int) -> int:
        """Find a 4-byte-element section's file offset through the MOC3 SOT.

        ``py-moc3``'s layout table is internal, so the pinned version is
        validated before every offset is used.
        """
        layout = getattr(moc, "_layout", None)
        sot = getattr(moc, "_sot_offsets", None)
        if not layout or not sot:
            raise UnsupportedMoc3Error("py-moc3 未提供 section offset table")

        try:
            layout_index = next(i for i, entry in enumerate(layout) if entry.name == name)
            offset = int(sot[layout_index + 2])
        except (StopIteration, IndexError) as exc:
            raise UnsupportedMoc3Error(f"无法定位 {name} section") from exc

        if offset <= 0 or offset + count * 4 > len(raw):
            raise PatchError(f"{name} 偏移越界，拒绝修改")
        if offset % 4 != 0:
            raise PatchError(f"{name} 偏移未按 4 字节对齐，拒绝修改")
        return offset

    @staticmethod
    def _read_defaults(moc: Moc3) -> dict[str, float]:
        """Read ``parameter.ids`` → raw ``parameter.default_values``."""
        try:
            ids = [str(value) for value in moc["parameter.ids"]]
            values = [float(value) for value in moc["parameter.default_values"]]
        except (KeyError, TypeError):
            return {}
        if len(ids) != len(values):
            return {}
        return dict(zip(ids, values))

    @classmethod
    def _locate_defaults(cls, moc: Moc3, raw: bytes) -> int | None:
        """File offset of ``parameter.default_values`` (None when no parameters)."""
        try:
            values = list(moc["parameter.default_values"])
        except (KeyError, TypeError):
            return None
        if not values:
            return None
        return cls._locate_section(moc, raw, "parameter.default_values", len(values))

    @staticmethod
    def _build_parts(moc: Moc3, part_count: int, art_mesh_count: int) -> list[PartInfo]:
        parent_values = list(moc["part.parent_part_indices"])
        if len(parent_values) != part_count:
            raise PatchError("Part 父级数组长度与 Part 数量不一致")

        mesh_parents = list(moc["art_mesh.parent_part_indices"])
        if len(mesh_parents) != art_mesh_count:
            raise PatchError("ArtMesh 父 Part 数组长度不一致")

        direct_mesh_count = [0] * part_count
        for parent in mesh_parents:
            if 0 <= parent < part_count:
                direct_mesh_count[parent] += 1

        children: list[list[int]] = [[] for _ in range(part_count)]
        normalized_parents: list[int] = []
        for index, parent in enumerate(parent_values):
            if parent == -1:
                normalized_parents.append(-1)
            elif 0 <= parent < part_count and parent != index:
                normalized_parents.append(parent)
                children[parent].append(index)
            else:
                raise PatchError(f"Part {index} 的父级索引非法：{parent}")

        # Detect cycles before recursion.  A cycle would make the model invalid.
        state = [0] * part_count  # 0=unseen, 1=visiting, 2=done
        for start in range(part_count):
            if state[start] != 0:
                continue
            stack: list[tuple[int, int]] = [(start, 0)]
            while stack:
                node, child_pos = stack[-1]
                if child_pos == 0:
                    if state[node] == 1:
                        raise PatchError("Part 树存在循环，拒绝修改")
                    if state[node] == 2:
                        stack.pop()
                        continue
                    state[node] = 1
                if child_pos < len(children[node]):
                    child = children[node][child_pos]
                    stack[-1] = (node, child_pos + 1)
                    if state[child] == 1:
                        raise PatchError("Part 树存在循环，拒绝修改")
                    stack.append((child, 0))
                else:
                    state[node] = 2
                    stack.pop()

        memo: dict[int, int] = {}

        def subtree_meshes(index: int) -> int:
            if index in memo:
                return memo[index]
            total = direct_mesh_count[index]
            for child in children[index]:
                total += subtree_meshes(child)
            memo[index] = total
            return total

        parts = []
        for index, name in enumerate(moc.part_ids):
            parts.append(
                PartInfo(
                    index=index,
                    id=name,
                    parent_index=normalized_parents[index],
                    child_indices=tuple(children[index]),
                    direct_mesh_count=direct_mesh_count[index],
                    subtree_mesh_count=subtree_meshes(index),
                )
            )
        return parts

    @staticmethod
    def _build_art_meshes(moc: Moc3, part_count: int) -> list[ArtMeshInfo]:
        parents = list(moc["art_mesh.parent_part_indices"])
        keyform_begins = list(moc["art_mesh.keyform_begin_indices"])
        keyform_counts = list(moc["art_mesh.keyform_counts"])
        meshes = []
        for index, name in enumerate(moc.art_mesh_ids):
            parent = int(parents[index])
            if parent < -1 or parent >= part_count:
                parent = -1
            meshes.append(
                ArtMeshInfo(
                    index=index,
                    id=name,
                    parent_part_index=parent,
                    keyform_begin=int(keyform_begins[index]),
                    keyform_count=int(keyform_counts[index]),
                )
            )
        return meshes

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def art_mesh_count(self) -> int:
        return len(self.art_meshes)

    @property
    def roots(self) -> list[PartInfo]:
        return [p for p in self.parts if p.parent_index == -1]

    @property
    def unparented_meshes(self) -> list[ArtMeshInfo]:
        return [m for m in self.art_meshes if m.parent_part_index == -1]

    @property
    def dirty(self) -> bool:
        return (
            self.pending_hidden != self.saved_hidden
            or self.pending_deep != self.saved_deep
            or self.pending_mesh_hidden != self.saved_mesh_hidden
            or self.pending_defaults != self.saved_defaults
        )

    @property
    def file_changed(self) -> bool:
        return self.raw != self.original_raw

    @property
    def changed_part_indices(self) -> list[int]:
        return sorted(
            (self.pending_hidden ^ self.saved_hidden)
            | (self.pending_deep ^ self.saved_deep)
        )

    @property
    def changed_mesh_indices(self) -> list[int]:
        return sorted(self.pending_mesh_hidden ^ self.saved_mesh_hidden)

    @property
    def changed_parameter_ids(self) -> list[str]:
        return sorted(
            parameter_id
            for parameter_id in self.pending_defaults.keys()
            | self.saved_defaults.keys()
            if self.pending_defaults.get(parameter_id)
            != self.saved_defaults.get(parameter_id)
        )

    @property
    def hidden_count(self) -> int:
        return len(self.pending_hidden)

    @property
    def deep_count(self) -> int:
        return len(self.pending_deep)

    @property
    def hidden_mesh_count(self) -> int:
        return len(self.pending_mesh_hidden)

    def is_hidden(self, index: int) -> bool:
        return index in self.pending_hidden

    def is_mesh_hidden(self, index: int) -> bool:
        return index in self.pending_mesh_hidden

    def is_mesh_hideable(self, index: int) -> bool:
        """Only unparented ArtMeshes have no Part opacity to hide them."""
        return (
            0 <= index < self.art_mesh_count
            and self.art_meshes[index].parent_part_index == -1
        )

    def _deep_owner(self, index: int, *, pending: bool) -> int | None:
        """Return the nearest Part that owns this Part's deep hide, if any."""
        if not 0 <= index < self.part_count:
            return None
        deep = self.pending_deep if pending else self.saved_deep
        current = index
        while current >= 0:
            if current in deep:
                return current
            current = self.parts[current].parent_index
        return None

    def is_deep_hidden(self, index: int, *, pending: bool = True) -> bool:
        """Whether this Part or one of its ancestors has deep-hide enabled."""
        return self._deep_owner(index, pending=pending) is not None

    def effective_hidden(self, index: int) -> bool:
        """Whether this Part is hidden directly or through an ancestor."""
        current = index
        while 0 <= current < self.part_count:
            if current in self.pending_hidden:
                return True
            current = self.parts[current].parent_index
        return False

    def toggle_part(self, index: int, hidden: bool, *, deep: bool | None = None) -> None:
        if not 0 <= index < self.part_count:
            raise IndexError(f"Part 索引越界：{index}")
        if hidden:
            self.pending_hidden.add(index)
            if deep is not None:
                if deep:
                    self.pending_deep.add(index)
                else:
                    self.pending_deep.discard(index)
        else:
            self.pending_hidden.discard(index)
            self.pending_deep.discard(index)

    def set_part_deep(self, index: int, deep: bool) -> None:
        if not 0 <= index < self.part_count:
            raise IndexError(f"Part 索引越界：{index}")
        if deep:
            self.pending_hidden.add(index)
            self.pending_deep.add(index)
        else:
            self.pending_deep.discard(index)

    def toggle_mesh(self, index: int, hidden: bool) -> None:
        if not 0 <= index < self.art_mesh_count:
            raise IndexError(f"ArtMesh 索引越界：{index}")
        if not self.is_mesh_hideable(index):
            raise ValueError("只有未归属 Part 的 ArtMesh 可以单独隐藏")
        if hidden:
            self.pending_mesh_hidden.add(index)
        else:
            self.pending_mesh_hidden.discard(index)

    def reset_pending(self) -> None:
        self.pending_hidden = set(self.saved_hidden)
        self.pending_deep = set(self.saved_deep)
        self.pending_mesh_hidden = set(self.saved_mesh_hidden)
        self.pending_defaults = dict(self.saved_defaults)

    def parameter_default(self, parameter_id: str) -> float | None:
        """Default value currently pending for a parameter."""
        if parameter_id in self.pending_defaults:
            return self.pending_defaults[parameter_id]
        parameter = self.parameter_by_id.get(parameter_id)
        return parameter.default if parameter is not None else None

    def set_parameter_default(self, parameter_id: str, value: float) -> None:
        """Stage a new MOC3 default value for one parameter."""
        parameter = self.parameter_by_id.get(parameter_id)
        if parameter is None:
            raise KeyError(f"未知参数：{parameter_id}")
        if self.parameter_default_offset is None:
            raise PatchError("该 MOC3 没有可写的 parameter.default_values 区域")
        self.pending_defaults[parameter_id] = parameter.clamp(float(value))

    def changed_bytes(self) -> int:
        """Number of bytes that would differ from the current on-disk file."""
        new = self.build_patched_bytes()
        return sum(1 for a, b in zip(self.raw, new) if a != b)

    def build_patched_bytes(self) -> bytes:
        data = bytearray(self.raw)
        offset = self.part_visible_offset
        for index in range(self.part_count):
            value = 0 if index in self.pending_hidden else 1
            struct.pack_into("<i", data, offset + index * 4, value)

        if self.parameter_default_offset is not None:
            for index, parameter in enumerate(self.parameters):
                value = self.pending_defaults.get(parameter.id)
                if value is None:
                    continue
                struct.pack_into(
                    "<f", data, self.parameter_default_offset + index * 4, value
                )

        for mesh in self.art_meshes:
            if (
                self._deep_owner(mesh.parent_part_index, pending=True) is not None
                or mesh.index in self.pending_mesh_hidden
            ):
                for k in range(mesh.keyform_begin, mesh.keyform_begin + mesh.keyform_count):
                    struct.pack_into("<f", data, self.opacity_offset + k * 4, 0.0)
                continue

            owner = self._deep_owner(mesh.parent_part_index, pending=False)
            recovered = None
            if owner is not None:
                recovered = self._deep_restore.get(owner, {}).get(mesh.index)
            elif mesh.index in self.saved_mesh_hidden:
                recovered = self._mesh_restore.get(mesh.index)
            else:
                continue
            start = self.opacity_offset + mesh.keyform_begin * 4
            end = start + mesh.keyform_count * 4
            if recovered is not None and len(recovered) == mesh.keyform_count:
                # A Part / ArtMesh hidden in a previous session is being shown:
                # restore the opacities captured before the hide.
                data[start:end] = struct.pack(f"<{mesh.keyform_count}f", *recovered)
            else:
                # Hidden in this session: the open-time baseline still holds
                # the original keyform bytes.
                data[start:end] = self.original_raw[start:end]
        return bytes(data)

    def _validate_new_bytes(self, data: bytes) -> None:
        if len(data) != len(self.raw):
            raise PatchError("补丁改变了文件长度，拒绝写入")
        if data[:4] != MAGIC:
            raise PatchError("补丁破坏了 MOC3 magic，拒绝写入")
        if data[4] != self.header.version or data[5] != self.header.endian:
            raise PatchError("补丁改变了 MOC3 版本头，拒绝写入")
        try:
            parsed = Moc3.from_bytes(data)
        except Exception as exc:
            raise PatchError(f"补丁后 py-moc3 无法解析：{exc}") from exc
        if len(parsed.part_ids) != self.part_count:
            raise PatchError("补丁后 Part 数量变化，拒绝写入")
        if list(parsed.part_ids) != [p.id for p in self.parts]:
            raise PatchError("补丁后 Part ID 顺序变化，拒绝写入")
        if [str(value) for value in parsed["parameter.ids"]] != [
            parameter.id for parameter in self.parameters
        ]:
            raise PatchError("补丁后参数 ID 顺序变化，拒绝写入")

    def _ensure_target_writable(self) -> None:
        """Fail early with an actionable message if the target is read-only.

        ``os.access(path, os.W_OK)`` may report writable inside a Windows
        sandbox even when a real open for write is denied, so probe with an
        actual ``r+b`` open.  No bytes are changed by this check.
        """
        try:
            with self.path.open("r+b"):
                pass
        except PermissionError as exc:
            raise PatchError(
                "当前进程没有写入目标文件的权限：\n"
                f"{self.path}\n\n"
                "如果模型放在桌面或项目目录之外，沙箱可能只允许读取。\n"
                "请从资源管理器直接启动程序，或把模型复制到项目目录后再修改。\n"
                "也请确认模型没有被其他程序占用"
            ) from exc
        except OSError as exc:
            raise PatchError(f"无法以写方式打开目标文件：{exc}") from exc

    def apply(self) -> Path | None:
        """Write pending changes to the original file, with a timestamped backup."""
        if not self.dirty:
            return None
        self._ensure_target_writable()
        try:
            disk_bytes = self.path.read_bytes()
        except OSError as exc:
            raise PatchError(f"无法读取原文件，可能已被占用：{exc}") from exc
        if disk_bytes != self.raw:
            raise PatchError(
                "原文件在打开后已被其他程序修改，已拒绝覆盖。\n"
                "请重新打开模型后再试"
            )
        new_bytes = self.build_patched_bytes()
        self._validate_new_bytes(new_bytes)
        backup = self._write_atomic(new_bytes)
        self.backup_path = backup
        self.raw = new_bytes
        self.saved_hidden = set(self.pending_hidden)
        self.saved_deep = set(self.pending_deep)
        self.saved_mesh_hidden = set(self.pending_mesh_hidden)
        self._deep_restore = {
            index: values
            for index, values in self._deep_restore.items()
            if index in self.saved_deep
        }
        self._mesh_restore = {
            index: values
            for index, values in self._mesh_restore.items()
            if index in self.saved_mesh_hidden
        }
        self.moc = Moc3.from_bytes(new_bytes)
        self.saved_defaults = dict(self.pending_defaults)
        self.parameters = read_parameters(self.moc)
        self.parameter_by_id = {p.id: p for p in self.parameters}
        return backup

    def reload_from_disk(self, *, keep_pending: bool = True) -> None:
        """Re-read the file after an external change.

        The Part structure must still match.  Used by the UI to recover when
        another process changed the file between open and save.
        """
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise PatchError(f"无法重新读取原文件：{exc}") from exc
        header = _parse_header(raw)
        try:
            moc = Moc3.from_bytes(raw)
        except Exception as exc:
            raise PatchError(f"重新读取后 py-moc3 无法解析：{exc}") from exc

        old_ids = [p.id for p in self.parts]
        if len(moc.part_ids) != self.part_count or list(moc.part_ids) != old_ids:
            raise PatchError("文件已被外部修改，且 Part 结构发生变化，无法安全重载")
        if [str(value) for value in moc["parameter.ids"]] != [
            parameter.id for parameter in self.parameters
        ]:
            raise PatchError("文件已被外部修改，且参数结构发生变化，无法安全重载")

        offset = self._locate_section(moc, raw, "part.visibles", self.part_count)
        values = struct.unpack_from(f"<{self.part_count}i", raw, offset)
        if any(v not in (0, 1) for v in values):
            raise PatchError("重载后的 part.visibles 包含非 0/1 值，拒绝继续")

        new_hidden = {i for i, value in enumerate(values) if value == 0}
        old_saved = set(self.saved_hidden)
        old_pending = set(self.pending_hidden)
        old_saved_deep = set(self.saved_deep)
        old_pending_deep = set(self.pending_deep)
        old_saved_mesh = set(self.saved_mesh_hidden)
        old_pending_mesh = set(self.pending_mesh_hidden)
        old_saved_defaults = dict(self.saved_defaults)
        old_pending_defaults = dict(self.pending_defaults)
        new_defaults = self._read_defaults(moc)
        if keep_pending:
            # Preserve the user's deltas relative to the old saved state while
            # keeping unrelated external edits.
            user_hidden = old_pending - old_saved
            user_shown = old_saved - old_pending
            pending = (new_hidden | user_hidden) - user_shown
        else:
            pending = set(new_hidden)

        self.raw = raw
        self.original_raw = raw
        self.header = header
        self.moc = moc
        self.part_visible_offset = offset
        self.parts = self._build_parts(moc, self.part_count, len(moc.art_mesh_ids))
        self.art_meshes = self._build_art_meshes(moc, self.part_count)
        self.part_by_id = {p.id: p for p in self.parts}
        self.mesh_by_id = {m.id: m for m in self.art_meshes}
        self.part_mesh_indices = self._build_part_mesh_map()
        self.parameters = read_parameters(moc)
        self.parameter_by_id = {p.id: p for p in self.parameters}
        self.initial_hidden = set(new_hidden)
        self.saved_hidden = set(new_hidden)
        self.pending_hidden = pending
        self.parameter_default_offset = self._locate_defaults(moc, raw)
        self.initial_defaults = dict(new_defaults)
        self.saved_defaults = dict(new_defaults)
        self.pending_defaults = dict(new_defaults)
        if keep_pending:
            for parameter_id, value in old_pending_defaults.items():
                if old_saved_defaults.get(parameter_id) == value:
                    continue
                if parameter_id in self.pending_defaults:
                    self.pending_defaults[parameter_id] = value
        self.opacity_offset = self._locate_section(
            moc,
            raw,
            "art_mesh_keyform.opacities",
            len(moc["art_mesh_keyform.opacities"]),
        )
        self._load_hidden_state()
        if keep_pending:
            # Carry the user's hidden-state deltas over the freshly read file.
            for saved, pending, old_saved_set, old_pending_set in (
                ("saved_deep", "pending_deep", old_saved_deep, old_pending_deep),
                (
                    "saved_mesh_hidden",
                    "pending_mesh_hidden",
                    old_saved_mesh,
                    old_pending_mesh,
                ),
            ):
                new_saved = set(getattr(self, saved))
                user_hidden = old_pending_set - old_saved_set
                user_shown = old_saved_set - old_pending_set
                setattr(self, saved, new_saved)
                setattr(self, pending, (new_saved | user_hidden) - user_shown)
        self.backup_path = None

    def restore_original(self) -> Path | None:
        """Restore the exact bytes captured when the document was opened."""
        if not self.file_changed:
            self.reset_pending()
            return None
        backup = self._write_atomic(self.original_raw)
        self.backup_path = backup
        self.raw = self.original_raw
        self.moc = Moc3.from_bytes(self.raw)
        self.saved_hidden = set(self.initial_hidden)
        self.pending_hidden = set(self.initial_hidden)
        self.saved_defaults = dict(self.initial_defaults)
        self.pending_defaults = dict(self.initial_defaults)
        self.parameters = read_parameters(self.moc)
        self.parameter_by_id = {p.id: p for p in self.parameters}
        self.parameter_default_offset = self._locate_defaults(self.moc, self.raw)
        self._load_hidden_state()
        return backup

    def _write_atomic(self, data: bytes) -> Path:
        if len(data) != len(self.original_raw):
            raise PatchError("写入数据长度与打开时不一致，拒绝写入")
        if not self.path.parent.exists():
            raise PatchError(f"目录不存在：{self.path.parent}")
        self._ensure_target_writable()

        backup = self._next_backup_path()
        last_copy_error: OSError | None = None
        for attempt in range(3):
            try:
                shutil.copy2(self.path, backup)
                last_copy_error = None
                break
            except OSError as exc:
                last_copy_error = exc
                if attempt < 2:
                    time.sleep(0.08 * (attempt + 1))
        if last_copy_error is not None:
            raise PatchError(f"无法创建备份：{backup}") from last_copy_error

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(data)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PatchError(f"无法写入临时文件：{exc}") from exc

        try:
            self._replace_with_retry(tmp_path)
        except OSError as exc:
            # Some Windows security/AV configurations deny rename-over-existing
            # even though the file itself is writable.  Fall back to an
            # in-place write of only the changed byte ranges.
            try:
                self._direct_patch(data)
            except OSError as direct_exc:
                self._restore_backup_best_effort(backup)
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise PatchError(
                    "写入原文件失败：\n"
                    f"原子替换：{exc}\n"
                    f"直接写入：{direct_exc}\n"
                    f"原文件备份：{backup}"
                ) from direct_exc
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return backup

    def _replace_with_retry(self, tmp_path: Path) -> None:
        last_error: OSError | None = None
        for attempt in range(4):
            try:
                os.replace(tmp_path, self.path)
                return
            except OSError as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.08 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _direct_patch(self, data: bytes) -> None:
        """Write only differing byte ranges in place, then verify the file."""
        current = self.path.read_bytes()
        if len(current) != len(data):
            raise OSError("文件长度在写入前发生变化")

        ranges: list[tuple[int, int]] = []
        start: int | None = None
        for index, (old, new) in enumerate(zip(current, data)):
            if old != new:
                if start is None:
                    start = index
            elif start is not None:
                ranges.append((start, index))
                start = None
        if start is not None:
            ranges.append((start, len(data)))

        if not ranges:
            return
        with self.path.open("r+b") as handle:
            for start, end in ranges:
                handle.seek(start)
                handle.write(data[start:end])
            handle.flush()
            os.fsync(handle.fileno())

        if self.path.read_bytes() != data:
            raise OSError("直接写入后校验失败")

    def _restore_backup_best_effort(self, backup: Path) -> None:
        try:
            shutil.copy2(backup, self.path)
        except OSError:
            pass

    def _next_backup_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = self.path.with_name(f"{self.path.name}.bak-{stamp}")
        suffix = 1
        while candidate.exists():
            candidate = self.path.with_name(f"{self.path.name}.bak-{stamp}-{suffix}")
            suffix += 1
        return candidate

    def part_label(self, index: int) -> str:
        part = self.parts[index]
        marker = "隐藏" if index in self.pending_hidden else "显示"
        return f"{self.part_name(index)}  ·  {part.subtree_mesh_count} 网格  ·  {marker}"

    def model3_name(self) -> str:
        return self.model3_path.name if self.model3_path else self.path.name

    def summary(self) -> str:
        return (
            f"{self.path.name} · MOC3 {self.header.version_name} · "
            f"{self.part_count} Parts / {self.art_mesh_count} ArtMeshes"
        )
