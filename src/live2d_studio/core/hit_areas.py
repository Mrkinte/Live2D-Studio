"""Generate and edit model3.json HitAreas for tap-region click bindings.

Standard:
  HitAreas[].Id   = an existing ArtMesh ID (usually a transparent Touch* quad)
  HitAreas[].Name = the motion group name that should play on tap

The MOC3 file is never modified.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .model3 import motion_groups, read_model3

# HitAreas Name / motion group names may be Chinese (or other Unicode words).
# Allow Unicode letters, digits, underscores and hyphens; keep a non-digit start.
ACTION_RE = re.compile(r"^(?!\d)[\w-]+$", re.UNICODE)


class HitAreaError(Exception):
    """Invalid model3 HitAreas data or a failed write."""


@dataclass
class HitAreaBinding:
    mesh_id: str
    name: str
    motion_group: str

    @property
    def status(self) -> str:
        if not self.motion_group:
            return "缺少动作组"
        if self.name != self.motion_group:
            return "名称与动作组不同"
        return "OK"


def camel_to_snake(value: str) -> str:
    """TouchIdle12 -> touch_idle12, TouchHead -> touch_head."""
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
    return value.lower()


def _motion_file_stem(file_name: str) -> str:
    name = Path(file_name).name
    if name.endswith(".motion3.json"):
        return name[: -len(".motion3.json")]
    if name.endswith(".json"):
        return name[: -len(".json")]
    return Path(name).stem


def group_for_action(action: str, groups: dict[str, tuple[str, ...]]) -> str | None:
    """Find the motion group for an ASCII Touch action.

    The group itself may be Chinese (e.g. TouchHead -> 摸头).  Matching by the
    motion file stem keeps the standard independent of the language used for
    the group display name.
    """
    if action in groups:
        return action
    target = action.lower()
    for group, files in groups.items():
        for file_name in files:
            if _motion_file_stem(file_name).lower() == target:
                return group
    return None


def read_hit_areas(data: dict) -> list[HitAreaBinding]:
    raw = data.get("HitAreas") or []
    if not isinstance(raw, list):
        return []
    groups = motion_groups(data)
    bindings: list[HitAreaBinding] = []
    by_mesh: dict[str, HitAreaBinding] = {}
    by_name: dict[str, HitAreaBinding] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        mesh_id = item.get("Id", item.get("id"))
        name = item.get("Name", item.get("name"))
        if not isinstance(mesh_id, str) or not isinstance(name, str):
            continue
        mesh_id = mesh_id.strip()
        name = name.strip()
        if not mesh_id or not name:
            continue
        binding = HitAreaBinding(
            mesh_id=mesh_id,
            name=name,
            motion_group=name if name in groups else "",
        )
        # Last entry wins for duplicate IDs/names; keep one binding per mesh
        # and one per HitArea name.
        old_mesh = by_mesh.get(mesh_id)
        old_name = by_name.get(name)
        for old in (old_mesh, old_name):
            if old is not None and old in bindings:
                bindings.remove(old)
                by_mesh.pop(old.mesh_id, None)
                by_name.pop(old.name, None)
        by_mesh[mesh_id] = binding
        by_name[name] = binding
        bindings.append(binding)
    return bindings


def suggest_touch_bindings(
    art_mesh_ids: Iterable[str],
    groups: dict[str, tuple[str, ...]],
) -> tuple[list[HitAreaBinding], list[str]]:
    """Suggest HitAreas for Touch* meshes whose snake_case name is a motion group."""
    suggested: list[HitAreaBinding] = []
    skipped: list[str] = []
    used_names: set[str] = set()
    used_meshes: set[str] = set()

    for mesh_id in art_mesh_ids:
        if not mesh_id.lower().startswith("touch"):
            continue
        action = camel_to_snake(mesh_id)
        if not ACTION_RE.match(action):
            skipped.append(f"{mesh_id}（名称不合规）")
            continue
        group = group_for_action(action, groups)
        if group is None:
            skipped.append(f"{mesh_id} → {action}（没有同名动作组）")
            continue
        if group in used_names or mesh_id in used_meshes:
            skipped.append(f"{mesh_id}（重复绑定）")
            continue
        suggested.append(HitAreaBinding(mesh_id=mesh_id, name=group, motion_group=group))
        used_names.add(group)
        used_meshes.add(mesh_id)
    return suggested, skipped


def merge_bindings(
    current: Iterable[HitAreaBinding],
    additions: Iterable[HitAreaBinding],
) -> list[HitAreaBinding]:
    result = list(current)
    by_mesh = {binding.mesh_id: binding for binding in result}
    by_name = {binding.name: binding for binding in result}

    for binding in additions:
        if binding.mesh_id in by_mesh or binding.name in by_name:
            continue
        result.append(binding)
        by_mesh[binding.mesh_id] = binding
        by_name[binding.name] = binding
    return result


def validate_bindings(
    bindings: Iterable[HitAreaBinding],
    art_mesh_ids: Iterable[str],
    *,
    groups: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    mesh_set = set(art_mesh_ids)
    errors: list[str] = []
    seen_meshes: set[str] = set()
    seen_names: set[str] = set()
    for index, binding in enumerate(bindings, start=1):
        if binding.mesh_id not in mesh_set:
            errors.append(f"第 {index} 行：ArtMesh 不存在：{binding.mesh_id}")
        if not ACTION_RE.match(binding.name):
            errors.append(f"第 {index} 行：Name 不符合 {ACTION_RE.pattern}")
        if binding.mesh_id in seen_meshes:
            errors.append(f"第 {index} 行：重复 ArtMesh：{binding.mesh_id}")
        if binding.name in seen_names:
            errors.append(f"第 {index} 行：重复 Name：{binding.name}")
        if groups is not None and binding.motion_group and binding.motion_group not in groups:
            errors.append(f"第 {index} 行：动作组不存在：{binding.motion_group}")
        seen_meshes.add(binding.mesh_id)
        seen_names.add(binding.name)
    return errors


def build_hit_areas(bindings: Iterable[HitAreaBinding]) -> list[dict]:
    return [{"Id": b.mesh_id, "Name": b.name} for b in bindings]


def save_hit_areas(
    model3_path: str | Path,
    bindings: Iterable[HitAreaBinding],
    *,
    art_mesh_ids: Iterable[str],
    groups: dict[str, tuple[str, ...]] | None = None,
) -> tuple[Path, list[HitAreaBinding]]:
    path = Path(model3_path).expanduser().resolve()
    data = read_model3(path)
    binding_list = list(bindings)
    errors = validate_bindings(binding_list, art_mesh_ids, groups=groups)
    if errors:
        raise HitAreaError("\n".join(errors))

    data["HitAreas"] = build_hit_areas(binding_list)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    backup = _next_backup_path(path)
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        raise HitAreaError(f"无法创建备份：{backup}") from exc

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise HitAreaError(f"写入 model3.json 失败：{exc}") from exc
    return backup, binding_list


def _next_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
        suffix += 1
    return candidate
