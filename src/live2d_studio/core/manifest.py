"""Build the website manifest (``<model>.live2d.json``) for a model folder.

The manifest is what the Atlas frontend reads:  motion aliases and groups,
expression aliases, wardrobe groups grouped by parameter, optional bubble texts
and default choices.  Everything here is derived from ``model3.json``, the
registered ``.exp3.json`` expressions and the manifest itself when re-exporting,
so editing keeps working when the model changes.

Unchecked entries are not written to ``motions``/``expressions``/``wardrobe``
(the site would still display them if they were).  They are instead recorded
under ``excluded`` with stable natural keys, so importing the manifest again
restores the same selection for further editing.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from .model3 import MotionEntry, motion_entries, read_model3

MANIFEST_FORMAT = "atlas-live2d-manifest"
MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".live2d.json"
ID_MAX_LENGTH = 64
TEXT_MAX_LENGTH = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")


class ManifestError(Exception):
    """Invalid model data or a failed manifest write."""


@dataclass
class MotionRow:
    """One motion as shown in the export dialog and written to the manifest."""

    source_group: str
    file: str
    id: str
    name: str
    label_group: str
    enabled: bool = True
    text: str = ""
    duration: float | None = None


@dataclass
class ManifestPlan:
    """Everything needed to write one manifest, before user edits."""

    model3_path: Path
    name: str
    rows: list[MotionRow] = field(default_factory=list)
    expressions: list[dict] = field(default_factory=list)
    wardrobe: list[dict] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    default_duration: float | None = None

    @property
    def target(self) -> Path:
        return manifest_path(self.model3_path)


def manifest_path(model3_path: str | Path) -> Path:
    """``.../foo.model3.json`` → ``.../foo.live2d.json``."""
    path = Path(model3_path).expanduser().resolve()
    return path.with_name(f"{_model_stem(path)}{MANIFEST_SUFFIX}")


def load_manifest(path: str | Path) -> dict:
    """Read a manifest for re-editing; raises :class:`ManifestError` on bad JSON."""
    target = Path(path).expanduser().resolve()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"无法读取清单：{target}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"清单格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("清单根节点不是对象")
    return data


def plan_manifest(
    model3_path: str | Path,
    *,
    entries: Sequence[MotionEntry] | None = None,
    parameter_names: Mapping[str, str] | None = None,
    existing: dict | None = None,
) -> ManifestPlan:
    """Read the model folder and prepare a manifest with sensible defaults."""
    path = Path(model3_path).expanduser().resolve()
    try:
        data = read_model3(path)
    except Exception as exc:  # Model3Error and friends
        raise ManifestError(str(exc)) from exc

    if entries is None:
        entries = motion_entries(path, data)
    source = existing if existing is not None else _load_existing(manifest_path(path))
    excluded = _read_excluded(source)
    default_duration = _existing_duration(source)

    plan = ManifestPlan(
        model3_path=path,
        name=_text((source or {}).get("name")) or _model_stem(path),
        default_duration=default_duration,
    )
    plan.rows = _build_rows(entries, source, excluded)
    plan.expressions, plan.wardrobe = _build_expressions(
        data, path.parent, parameter_names or {}, source, excluded
    )
    plan.defaults = _build_defaults(plan, source)
    return plan


def write_manifest(plan: ManifestPlan, target: str | Path | None = None) -> Path:
    """Write (or replace) the manifest, backing up an existing file."""
    target_path = (
        Path(target).expanduser().resolve() if target else plan.target
    )
    data = manifest_dict(plan)

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if target_path.exists():
            shutil.copy2(target_path, _next_backup_path(target_path))
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target_path.name}.", suffix=".tmp", dir=str(target_path.parent)
        )
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target_path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ManifestError(f"写入清单失败：{target_path}（{exc}）") from exc
    return target_path


def manifest_dict(plan: ManifestPlan) -> dict:
    """Build the JSON object written to disk, skipping unchecked entries."""
    groups: list[dict] = []
    group_index: dict[str, dict] = {}
    used_group_ids: set[str] = set()
    used_motion_ids: set[str] = set()

    excluded_motions: list[str] = []
    motions: list[dict] = []
    for row in plan.rows:
        if not row.enabled:
            excluded_motions.append(_motion_key(row.source_group, row.file))
            continue

        motion_id = _unique_id(row.id or _motion_id(row.file), used_motion_ids)
        entry = {
            "id": motion_id,
            "name": _text(row.name) or Path(row.file).stem,
            "group": row.source_group,
            "file": row.file,
        }
        text = _clean_text(row.text)
        if text:
            entry["text"] = text
        duration = _positive(row.duration)
        if duration is not None:
            entry["duration"] = round(duration, 3)
        motions.append(entry)

        label = _text(row.label_group)
        if not label:
            continue
        group = group_index.get(label)
        if group is None:
            group = {
                "id": _unique_id(label, used_group_ids),
                "name": label,
                "motions": [],
            }
            group_index[label] = group
            groups.append(group)
        group["motions"].append(motion_id)

    excluded_expressions: list[str] = []
    expressions: list[dict] = []
    for item in plan.expressions:
        if not item.get("enabled", True):
            excluded_expressions.append(item["runtimeName"])
            continue
        expressions.append(
            {
                "id": item["id"],
                "name": item["name"],
                "runtimeName": item["runtimeName"],
            }
        )

    excluded_wardrobe: list[str] = []
    excluded_options: list[str] = []
    wardrobe: list[dict] = []
    for group in plan.wardrobe:
        options = []
        for option in group["options"]:
            entry = {
                "id": option["id"],
                "name": option["name"],
                "value": option["value"],
            }
            if isinstance(option.get("values"), dict):
                entry["values"] = option["values"]
            if option.get("enabled", True):
                options.append(entry)
            else:
                values = _option_values(group["parameter"], option)
                excluded_options.append(
                    _option_signature(values)
                    if values
                    else f"{group['parameter']}|{option['value']:g}"
                )

        parameters = group.get("parameters") or [group["parameter"]]
        if not group.get("enabled", True) or not options:
            excluded_wardrobe.append(_group_key(parameters))
            continue
        entry = {
            "id": group["id"],
            "name": group["name"],
            "parameter": group["parameter"],
            "options": options,
        }
        if len(parameters) > 1:
            entry["parameters"] = list(parameters)
        wardrobe.append(entry)

    result = {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "name": plan.name,
        "motions": motions,
        "motionGroups": groups,
        "expressions": expressions,
        "wardrobe": wardrobe,
        "defaults": _filter_defaults(
            plan.defaults,
            {item["id"] for item in motions},
            {item["runtimeName"] for item in expressions},
            wardrobe,
        ),
    }

    duration = _positive(plan.default_duration)
    if duration is not None:
        result["defaultDuration"] = round(duration, 3)

    excluded = {}
    for key, values in (
        ("motions", excluded_motions),
        ("expressions", excluded_expressions),
        ("wardrobe", excluded_wardrobe),
        ("wardrobeOptions", excluded_options),
    ):
        unique = sorted(set(values))
        if unique:
            excluded[key] = unique
    if excluded:
        result["excluded"] = excluded
    return result


@dataclass(frozen=True)
class _PreviousMotion:
    """One motion entry of an existing manifest."""

    id: str
    name: str
    label_group: str
    text: str
    duration: float | None


def _build_rows(
    entries: Sequence[MotionEntry],
    existing: dict | None,
    excluded: Mapping[str, set[str]],
) -> list[MotionRow]:
    motion_info = _existing_motion_info(existing)
    previous_order = _existing_motion_order(existing)
    excluded_motions = excluded.get("motions", set())
    used_ids: set[str] = set()
    rows: list[MotionRow] = []
    for entry in entries:
        previous = motion_info.get((entry.group, entry.file))
        name = previous.name if previous and previous.name else entry.display_name
        label = previous.label_group if previous else ""
        motion_id = _unique_id(
            (previous.id if previous and previous.id else "") or _motion_id(entry.file),
            used_ids,
        )
        if previous is not None:
            text = previous.text
            duration = previous.duration
        else:
            text, duration = "", None
        rows.append(
            MotionRow(
                source_group=entry.group,
                file=entry.file,
                id=motion_id,
                name=name,
                label_group=label,
                enabled=_motion_key(entry.group, entry.file) not in excluded_motions,
                text=text,
                duration=duration,
            )
        )
    return _order_rows(rows, previous_order)


def _existing_motion_order(manifest: dict | None) -> dict[tuple[str, str], int]:
    """``(group, file)`` → position in an existing manifest's motion list."""
    if not manifest:
        return {}
    motions = manifest.get("motions")
    if not isinstance(motions, list):
        return {}

    order: dict[tuple[str, str], int] = {}
    for index, item in enumerate(motions):
        if not isinstance(item, dict):
            continue
        file = _text(item.get("file"))
        if not file:
            continue
        entry = (_text(item.get("group")), file)
        order.setdefault(entry, index)
    return order


def _order_rows(
    rows: Sequence[MotionRow],
    order: Mapping[tuple[str, str], int],
) -> list[MotionRow]:
    """Keep the previous manifest order; new motions follow in model order."""
    if not order:
        return list(rows)

    fallback = len(rows)
    keyed = []
    for position, row in enumerate(rows):
        index = order.get((row.source_group, row.file))
        keyed.append((index if index is not None else fallback + position, position))
    return [rows[position] for _key, position in sorted(keyed)]


def _existing_motion_info(
    manifest: dict | None,
) -> dict[tuple[str, str], _PreviousMotion]:
    """``(group, file)`` → previous motion data of an existing manifest."""
    if not manifest:
        return {}

    motions = manifest.get("motions")
    if not isinstance(motions, list):
        return {}

    label_by_id: dict[str, str] = {}
    groups = manifest.get("motionGroups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            label = _text(group.get("name"))
            for motion_id in group.get("motions") or []:
                if isinstance(motion_id, str) and label:
                    label_by_id[motion_id] = label

    info: dict[tuple[str, str], _PreviousMotion] = {}
    for item in motions:
        if not isinstance(item, dict):
            continue
        file = _text(item.get("file"))
        if not file:
            continue
        motion_id = _text(item.get("id"))
        name = _text(item.get("name"))
        group = _text(item.get("group"))
        text = _clean_text(item.get("text"))
        duration = _positive(item.get("duration"))
        info[(group, file)] = _PreviousMotion(
            motion_id, name, label_by_id.get(motion_id, ""), text, duration
        )
    return info


def _build_expressions(
    model3_data: dict,
    model_root: Path,
    parameter_names: Mapping[str, str],
    existing: dict | None,
    excluded: Mapping[str, set[str]],
) -> tuple[list[dict], list[dict]]:
    references = (model3_data.get("FileReferences") or {}).get("Expressions") or []
    if not isinstance(references, list):
        references = []

    parsed: list[tuple[str, str, str, dict[str, float] | None]] = []
    bindings_by_parameter: dict[str, list[float]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            continue
        name = _text(reference.get("Name"))
        file_ref = _text(reference.get("File"))
        if not name or not file_ref:
            continue
        kind, values = _read_expression_binding(model_root / file_ref)
        parsed.append((name, file_ref, kind, values))
        if kind == "" and values is not None and len(values) == 1:
            parameter_id, value = next(iter(values.items()))
            bindings_by_parameter.setdefault(parameter_id, []).append(value)

    # 自动模式下，同一个参数至少有 2 个不同取值才算一组换装；
    # 在 .exp3.json 的 Meta.studioKind 里显式标记为 wardrobe 的始终按换装导出。
    wardrobe_parameters = {
        parameter_id
        for parameter_id, values in bindings_by_parameter.items()
        if len({round(value, 6) for value in values}) >= 2
    }

    existing_wardrobe = _existing_wardrobe(existing)
    existing_expressions = _existing_expressions(existing)
    excluded_wardrobe = excluded.get("wardrobe", set())
    excluded_options = excluded.get("wardrobeOptions", set())
    excluded_expressions = excluded.get("expressions", set())
    used_expression_ids: set[str] = set()
    used_group_ids: set[str] = set()
    used_option_ids: dict[str, set[str]] = {}

    expressions: list[dict] = []
    wardrobe: list[dict] = []
    groups_by_key: dict[tuple[str, ...], dict] = {}

    def add_option(name: str, values: Mapping[str, float]) -> None:
        key = tuple(sorted(values))
        group = groups_by_key.get(key)
        if group is None:
            previous = existing_wardrobe.get(key, {})
            group = {
                "id": _unique_id(
                    _text(previous.get("id")) or _group_id(key), used_group_ids
                ),
                "name": _text(previous.get("name"))
                or _group_name(key, parameter_names),
                "parameter": key[0],
                "enabled": _group_key(key) not in excluded_wardrobe,
                "options": [],
            }
            if len(key) > 1:
                group["parameters"] = list(key)
            used_option_ids[group["id"]] = set()
            groups_by_key[key] = group
            wardrobe.append(group)

        options = previous_options(existing_wardrobe, key)
        previous_option = _existing_option(options, key[0], values)
        option_name = _text((previous_option or {}).get("name")) or name
        option = {
            "id": _unique_id(
                _text((previous_option or {}).get("id")) or option_name,
                used_option_ids[group["id"]],
            ),
            "name": option_name,
            "value": round(values[key[0]], 6),
            "enabled": _option_signature(values) not in excluded_options,
        }
        if len(key) > 1:
            option["values"] = {
                parameter_id: round(value, 6)
                for parameter_id, value in sorted(values.items())
            }
        group["options"].append(option)

    for name, file_ref, kind, values in parsed:
        if kind == "wardrobe" and values:
            add_option(name, values)
            continue
        if (
            kind == ""
            and values is not None
            and len(values) == 1
            and next(iter(values)) in wardrobe_parameters
        ):
            add_option(name, values)
            continue

        previous = existing_expressions.get(name, {})
        expressions.append(
            {
                "id": _unique_id(
                    _text(previous.get("id")) or Path(file_ref).stem,
                    used_expression_ids,
                ),
                "name": _text(previous.get("name")) or name,
                "runtimeName": name,
                "file": file_ref,
                "enabled": name not in excluded_expressions,
            }
        )

    return expressions, wardrobe


def _read_expression_binding(
    exp_path: Path,
) -> tuple[str, dict[str, float] | None]:
    """``(kind, values)`` of an expression; values is None when not wardrobeable."""
    try:
        data = json.loads(exp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", None
    if not isinstance(data, dict):
        return "", None

    kind = ""
    meta = data.get("Meta")
    if isinstance(meta, dict) and meta.get("studioKind") in ("expression", "wardrobe"):
        kind = meta["studioKind"]

    parameters = data.get("Parameters")
    if not isinstance(parameters, list) or not parameters:
        return kind, None

    values: dict[str, float] = {}
    for item in parameters:
        if not isinstance(item, dict):
            return kind, None
        parameter_id = _text(item.get("Id"))
        blend = item.get("Blend", "Add")
        if not parameter_id or blend not in ("Add",):
            return kind, None
        try:
            number = float(item.get("Value"))
        except (TypeError, ValueError):
            return kind, None
        values[parameter_id] = values.get(parameter_id, 0.0) + number

    return kind, (values or None)


def _existing_wardrobe(
    manifest: dict | None,
) -> dict[tuple[str, ...], dict]:
    """Parameter set → wardrobe group of an existing manifest."""
    result: dict[tuple[str, ...], dict] = {}
    if not manifest:
        return result
    groups = manifest.get("wardrobe")
    if not isinstance(groups, list):
        return result
    for group in groups:
        if not isinstance(group, dict):
            continue
        raw = group.get("parameters")
        if isinstance(raw, list) and raw:
            key = tuple(_text(item) for item in raw if _text(item))
        else:
            parameter = _text(group.get("parameter"))
            key = (parameter,) if parameter else ()
        if key:
            result[key] = group
    return result


def _existing_expressions(manifest: dict | None) -> dict[str, dict]:
    """``runtimeName`` → expression entry of an existing manifest."""
    result: dict[str, dict] = {}
    if not manifest:
        return result
    items = manifest.get("expressions")
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        runtime_name = _text(item.get("runtimeName"))
        if runtime_name:
            result[runtime_name] = item
    return result


def previous_options(
    existing: Mapping[tuple[str, ...], dict], key: tuple[str, ...]
) -> list[dict]:
    group = existing.get(key) or {}
    options = group.get("options")
    return options if isinstance(options, list) else []


def _option_values(
    parameter: str, option: Mapping
) -> dict[str, float] | None:
    """Values of one wardrobe option; ``values`` wins over ``parameter``/``value``."""
    raw = option.get("values")
    if isinstance(raw, dict) and raw:
        try:
            return {
                str(parameter_id): float(value)
                for parameter_id, value in raw.items()
            }
        except (TypeError, ValueError):
            return None
    try:
        return {parameter: float(option.get("value"))}
    except (TypeError, ValueError):
        return None


def _values_equal(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    if set(left) != set(right):
        return False
    return all(abs(left[key] - right[key]) < 1e-6 for key in left)


def _existing_option(
    options: list, parameter: str, values: Mapping[str, float]
) -> dict | None:
    for option in options:
        if not isinstance(option, dict):
            continue
        candidate = _option_values(parameter, option)
        if candidate is not None and _values_equal(candidate, values):
            return option
    return None


def _build_defaults(plan: ManifestPlan, existing: dict | None) -> dict:
    raw = (existing or {}).get("defaults")
    raw = raw if isinstance(raw, dict) else {}

    expression_ids = {
        item["runtimeName"]
        for item in plan.expressions
        if item.get("enabled", True)
    }
    expression = _text(raw.get("expression"))
    if expression and expression not in expression_ids:
        expression = ""

    valid_motion_ids = {row.id for row in plan.rows if row.enabled}
    entry_motion = _text(raw.get("entryMotion"))
    if entry_motion not in valid_motion_ids:
        entry_motion = ""
    if not entry_motion:
        entry_motion = _default_entry_motion(plan.rows)

    wardrobe: dict[str, str] = {}
    raw_wardrobe = raw.get("wardrobe")
    if isinstance(raw_wardrobe, dict):
        for group in plan.wardrobe:
            if not group.get("enabled", True):
                continue
            option_id = raw_wardrobe.get(group["id"])
            if isinstance(option_id, str) and any(
                option["id"] == option_id and option.get("enabled", True)
                for option in group["options"]
            ):
                wardrobe[group["id"]] = option_id

    defaults: dict = {"wardrobe": wardrobe}
    if entry_motion:
        defaults["entryMotion"] = entry_motion
    if expression:
        defaults["expression"] = expression
    return defaults


def _filter_defaults(
    raw: Mapping[str, object],
    motion_ids: set[str],
    expression_names: set[str],
    wardrobe: Sequence[dict],
) -> dict:
    """Drop defaults that point at unchecked entries."""
    result: dict = {"wardrobe": {}}
    entry_motion = _text(raw.get("entryMotion"))
    if entry_motion in motion_ids:
        result["entryMotion"] = entry_motion

    expression = _text(raw.get("expression"))
    if expression in expression_names:
        result["expression"] = expression

    raw_wardrobe = raw.get("wardrobe")
    wardrobe_defaults: dict[str, str] = {}
    if isinstance(raw_wardrobe, dict):
        for group in wardrobe:
            option_id = raw_wardrobe.get(group["id"])
            if isinstance(option_id, str) and any(
                option["id"] == option_id for option in group["options"]
            ):
                wardrobe_defaults[group["id"]] = option_id
    result["wardrobe"] = wardrobe_defaults
    return result


def _default_entry_motion(rows: Sequence[MotionRow]) -> str:
    """Prefer the first idle motion so the model does not stop on a tap pose."""
    enabled = [row for row in rows if row.enabled]
    for row in enabled:
        if row.source_group.strip().lower() == "idle":
            return row.id
    return enabled[0].id if enabled else ""


def _load_existing(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_excluded(manifest: dict | None) -> dict[str, set[str]]:
    keys = ("motions", "expressions", "wardrobe", "wardrobeOptions")
    result: dict[str, set[str]] = {key: set() for key in keys}
    if not isinstance(manifest, dict):
        return result
    raw = manifest.get("excluded")
    if not isinstance(raw, dict):
        return result
    for key in keys:
        values = raw.get(key)
        if isinstance(values, list):
            result[key] = {
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            }
    return result


def _existing_duration(manifest: dict | None) -> float | None:
    if not isinstance(manifest, dict):
        return None
    return _positive(manifest.get("defaultDuration"))


def _motion_key(group: str, file: str) -> str:
    return f"{group}|{file}"


def _option_key(parameter: str, value: float) -> str:
    return f"{parameter}|{round(float(value), 6):g}"


def _option_signature(values: Mapping[str, float]) -> str:
    """Stable key for one wardrobe option; single-parameter keys stay unchanged."""
    if len(values) == 1:
        parameter_id, value = next(iter(values.items()))
        return _option_key(parameter_id, value)
    return ";".join(
        _option_key(parameter_id, value)
        for parameter_id, value in sorted(values.items())
    )


def _group_key(parameters: Sequence[str]) -> str:
    """Stable key for one wardrobe group's parameter set."""
    unique = list(dict.fromkeys(parameters))
    return unique[0] if len(unique) == 1 else ";".join(sorted(unique))


def _group_id(parameters: Sequence[str]) -> str:
    return "+".join(parameters)


def _group_name(
    parameters: Sequence[str], parameter_names: Mapping[str, str]
) -> str:
    return " + ".join(parameter_names.get(item) or item for item in parameters)


def _motion_id(file: str) -> str:
    """``motions/idle.motion3.json`` → ``idle``."""
    name = Path(file).name
    if name.lower().endswith(".motion3.json"):
        return name[: -len(".motion3.json")]
    return Path(name).stem


def _model_stem(path: Path) -> str:
    name = path.name
    if name.lower().endswith(".model3.json"):
        return name[: -len(".model3.json")]
    return path.stem


def _text(value: object) -> str:
    """Trim a string and drop control characters."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub("", value).strip()


def _clean_text(value: object) -> str:
    """Normalize free-form bubble text: control characters become spaces."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub(" ", value)
    text = _SPACES.sub(" ", text).strip()
    return text[:TEXT_MAX_LENGTH]


def _positive(value: object) -> float | None:
    """Return a finite positive number, otherwise ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _unique_id(value: str, used: set[str]) -> str:
    base = _CONTROL.sub("", value).strip()[:ID_MAX_LENGTH] or "item"
    candidate = base
    counter = 2
    while candidate in used:
        suffix = f"-{counter}"
        candidate = base[: ID_MAX_LENGTH - len(suffix)] + suffix
        counter += 1
    used.add(candidate)
    return candidate


def _next_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
        suffix += 1
    return candidate
