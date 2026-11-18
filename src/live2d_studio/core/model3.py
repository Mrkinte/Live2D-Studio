"""Read the parts of model3.json that the studio UI needs, and write sounds."""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class Model3Error(Exception):
    """Invalid or unreadable model3.json."""


@dataclass(frozen=True)
class ExpressionEntry:
    """One registered Cubism expression of a model3.json."""

    name: str
    file: str
    path: Path | None


@dataclass(frozen=True)
class MotionEntry:
    """One motion file inside a Cubism motion group."""

    group: str
    index: int
    file: str
    path: Path | None
    sound: str = ""

    @property
    def display_name(self) -> str:
        name = Path(self.file).name
        if name.endswith(".motion3.json"):
            return name[: -len(".motion3.json")]
        return Path(name).stem


def read_model3(path: str | Path) -> dict:
    p = Path(path).expanduser().resolve()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Model3Error(f"无法读取 model3.json：{p}") from exc
    except json.JSONDecodeError as exc:
        raise Model3Error(f"model3.json 格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise Model3Error("model3.json 根节点不是对象")
    return data


def motion_groups(data: dict) -> dict[str, tuple[str, ...]]:
    """Return ``{group: (motion file, ...)}`` from model3.json."""
    motions = (data.get("FileReferences") or {}).get("Motions") or {}
    if not isinstance(motions, dict):
        return {}
    groups: dict[str, tuple[str, ...]] = {}
    for group, items in motions.items():
        if not isinstance(group, str) or not isinstance(items, list):
            continue
        files = tuple(
            str(item.get("File", ""))
            for item in items
            if isinstance(item, dict) and item.get("File")
        )
        if files:
            groups[group] = files
    return groups


def motion_entries(model3_path: str | Path, data: dict | None = None) -> list[MotionEntry]:
    """Flatten motion groups into entries with resolved file paths."""
    p = Path(model3_path).expanduser().resolve()
    if data is None:
        data = read_model3(p)
    motions = (data.get("FileReferences") or {}).get("Motions") or {}
    if not isinstance(motions, dict):
        return []

    entries: list[MotionEntry] = []
    for group, items in motions.items():
        if not isinstance(group, str) or not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            file_name = item.get("File")
            if not isinstance(file_name, str) or not file_name:
                continue
            raw_sound = item.get("Sound")
            sound = raw_sound.strip() if isinstance(raw_sound, str) else ""
            target = p.parent / file_name
            entries.append(
                MotionEntry(
                    group=group,
                    index=index,
                    file=file_name,
                    path=target if target.exists() else None,
                    sound=sound,
                )
            )
    return entries


def expression_entries(
    model3_path: str | Path, data: dict | None = None
) -> list[ExpressionEntry]:
    """Read ``FileReferences.Expressions`` with resolved file paths."""
    path = Path(model3_path).expanduser().resolve()
    if data is None:
        data = read_model3(path)
    expressions = (data.get("FileReferences") or {}).get("Expressions") or []
    if not isinstance(expressions, list):
        return []

    entries: list[ExpressionEntry] = []
    for item in expressions:
        if not isinstance(item, dict):
            continue
        name = item.get("Name")
        file_name = item.get("File")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(file_name, str) or not file_name:
            continue
        target = path.parent / file_name
        entries.append(
            ExpressionEntry(
                name=name.strip(),
                file=file_name,
                path=target if target.is_file() else None,
            )
        )
    return entries


def set_motion_sound(
    model3_path: str | Path,
    group: str,
    motion_file: str,
    source: str | Path | None,
) -> tuple[str, bool]:
    """Write (or clear) ``Motions[group][].Sound`` for one motion.

    Sound files outside the model directory are copied into ``sounds/`` so the
    reference stays relative and the model remains portable.  Returns the new
    reference (empty when cleared) and whether the file changed.
    """
    path = Path(model3_path).expanduser().resolve()
    data = read_model3(path)
    motions = (data.get("FileReferences") or {}).get("Motions")
    if not isinstance(motions, dict):
        raise Model3Error("model3.json 没有 Motions 配置")
    items = motions.get(group)
    if not isinstance(items, list):
        raise Model3Error(f"model3.json 中不存在动作组：{group}")

    target = None
    for item in items:
        if isinstance(item, dict) and item.get("File") == motion_file:
            target = item
            break
    if target is None:
        raise Model3Error(f"动作组 {group} 中找不到文件：{motion_file}")

    if source is None:
        if "Sound" not in target:
            return "", False
        del target["Sound"]
        reference = ""
    else:
        sound_path = Path(source).expanduser().resolve()
        if not sound_path.is_file():
            raise Model3Error(f"声音文件不存在：{sound_path}")
        reference = _sound_reference(path, sound_path)
        if target.get("Sound") == reference:
            return reference, False
        target["Sound"] = reference

    _write_model3(path, data)
    return reference, True


def _sound_reference(model3_path: Path, source: Path) -> str:
    """Relative reference, copying external files into ``sounds/``."""
    root = model3_path.parent
    try:
        inside = source.relative_to(root)
    except ValueError:
        inside = None
    if inside is not None:
        return inside.as_posix()

    sounds_dir = root / "sounds"
    try:
        sounds_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Model3Error(f"无法创建 sounds 目录：{sounds_dir}") from exc

    target = _unique_sound_target(sounds_dir, source)
    if not target.exists():
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise Model3Error(f"无法复制声音文件：{exc}") from exc
    return target.relative_to(root).as_posix()


def _unique_sound_target(directory: Path, source: Path) -> Path:
    """Reuse an identical copy, otherwise pick ``name (2).ext`` etc."""
    candidate = directory / source.name
    stem, suffix = source.stem, source.suffix
    counter = 2
    while candidate.exists():
        try:
            if filecmp.cmp(source, candidate, shallow=False):
                return candidate
        except OSError:
            pass
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _write_model3(path: Path, data: dict) -> None:
    """Timestamped backup plus atomic replace, keeping UTF-8 and 2-space JSON."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    backup = _next_backup_path(path)
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        raise Model3Error(f"无法创建备份：{backup}") from exc

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
        raise Model3Error(f"写入 model3.json 失败：{exc}") from exc


def _next_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
        suffix += 1
    return candidate
