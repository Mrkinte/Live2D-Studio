"""Read MOC3 parameters and write Cubism ``.exp3.json`` expressions.

The MOC3 parameter sections are read through ``py-moc3`` (read-only).  Display
names come from the Cubism Display Info file (``cdi3.json``).  Expressions are
written as plain JSON next to the model, with a timestamped backup when an
existing file is replaced.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from moc3 import Moc3

EXPRESSION_TYPE = "Live2D Expression"
EXPRESSION_BLENDS = ("Add", "Multiply")
# How a binding is exported to the website manifest.  Empty means "auto".
EXPRESSION_KINDS = ("expression", "wardrobe")


class ExpressionError(Exception):
    """Invalid expression data or a failed write."""


@dataclass(frozen=True)
class ParameterInfo:
    """One MOC3 parameter definition."""

    index: int
    id: str
    minimum: float
    maximum: float
    default: float
    decimals: int

    @property
    def step(self) -> float:
        return 10.0 ** (-self.decimals)

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, float(value)))

    def format(self, value: float) -> str:
        return f"{self.clamp(value):.{self.decimals}f}"


def read_parameters(moc: Moc3) -> list[ParameterInfo]:
    """Read every parameter definition from a parsed MOC3 model."""
    try:
        ids = [str(value) for value in moc["parameter.ids"]]
        minimums = [float(value) for value in moc["parameter.min_values"]]
        maximums = [float(value) for value in moc["parameter.max_values"]]
        defaults = [float(value) for value in moc["parameter.default_values"]]
        decimals_raw = list(moc["parameter.decimal_places"])
    except (KeyError, TypeError):
        return []
    if not ids:
        return []

    if len(minimums) != len(ids) or len(maximums) != len(ids):
        return []
    if len(defaults) != len(ids):
        defaults = [0.0] * len(ids)
    if len(decimals_raw) != len(ids):
        decimals_raw = [2] * len(ids)

    parameters: list[ParameterInfo] = []
    for index, parameter_id in enumerate(ids):
        minimum = minimums[index]
        maximum = maximums[index]
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        try:
            decimals = int(decimals_raw[index])
        except (TypeError, ValueError):
            decimals = 2
        defaults_value = defaults[index]
        parameters.append(
            ParameterInfo(
                index=index,
                id=parameter_id,
                minimum=minimum,
                maximum=maximum,
                default=max(minimum, min(maximum, defaults_value)),
                decimals=max(0, min(4, decimals)),
            )
        )
    return parameters


def read_expression_values(path: str | Path) -> dict[str, float]:
    """Read additive ``.exp3.json`` parameters as ``{parameter id: value}``.

    Multiply entries cannot be represented as absolute slider values, so they
    are ignored.  Missing or invalid files return an empty mapping.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    parameters = data.get("Parameters") if isinstance(data, dict) else None
    if not isinstance(parameters, list):
        return {}

    values: dict[str, float] = {}
    for item in parameters:
        if not isinstance(item, dict):
            continue
        parameter_id = item.get("Id")
        if not isinstance(parameter_id, str) or not parameter_id.strip():
            continue
        if item.get("Blend", "Add") != "Add":
            continue
        try:
            number = float(item.get("Value"))
        except (TypeError, ValueError):
            continue
        parameter_id = parameter_id.strip()
        values[parameter_id] = values.get(parameter_id, 0.0) + number
    return values


def read_parameter_names(path: str | Path) -> dict[str, str]:
    """Read ``Parameters[].Id`` → ``.Name`` from a cdi3.json Display Info file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("Parameters")
    if not isinstance(raw, list):
        return {}

    names: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        parameter_id = item.get("Id")
        name = item.get("Name")
        if isinstance(parameter_id, str) and isinstance(name, str):
            parameter_id = parameter_id.strip()
            name = name.strip()
            if parameter_id and name:
                names[parameter_id] = name
    return names


def read_expression_kind(path: str | Path) -> str:
    """Read the studio binding kind ("expression"/"wardrobe") from an exp3."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    meta = data.get("Meta")
    if not isinstance(meta, dict):
        return ""
    kind = meta.get("studioKind")
    return kind if kind in EXPRESSION_KINDS else ""


def build_expression(
    values: Mapping[str, float],
    *,
    blend: str = "Add",
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    kind: str = "",
) -> dict:
    """Build a Cubism expression dict from ``{parameter id: value}``."""
    if not values:
        raise ExpressionError("没有要写入表达式的参数")
    if blend not in EXPRESSION_BLENDS:
        raise ExpressionError(f"不支持的 Blend：{blend}")
    if kind and kind not in EXPRESSION_KINDS:
        raise ExpressionError(f"不支持的绑定类型：{kind}")

    parameters: list[dict] = []
    for parameter_id, value in values.items():
        parameter_id = str(parameter_id).strip()
        if not parameter_id:
            raise ExpressionError("参数 ID 不能为空")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ExpressionError(
                f"参数 {parameter_id} 的值不是数字：{value}"
            ) from exc
        parameters.append(
            {
                "Id": parameter_id,
                "Value": round(number, 6),
                "Blend": blend,
            }
        )

    data = {
        "Type": EXPRESSION_TYPE,
        "Parameters": parameters,
        "FadeInTime": max(0.0, float(fade_in)),
        "FadeOutTime": max(0.0, float(fade_out)),
    }
    if kind:
        # Unknown root keys are ignored by the Cubism runtime.
        data["Meta"] = {"studioKind": kind}
    return data


def dumps_expression(data: dict) -> str:
    """Serialize an expression without ASCII escaping (Chinese IDs are valid)."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _write_text_atomic(target: Path, text: str, label: str) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ExpressionError(f"{label}：{exc}") from exc


def save_expression(
    path: str | Path,
    values: Mapping[str, float],
    *,
    blend: str = "Add",
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    kind: str = "",
) -> Path:
    """Write an ``.exp3.json`` expression, backing up an existing file."""
    target = Path(path).expanduser().resolve()
    data = build_expression(
        values, blend=blend, fade_in=fade_in, fade_out=fade_out, kind=kind
    )
    text = dumps_expression(data)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExpressionError(f"无法创建输出目录：{target.parent}") from exc

    if target.exists():
        backup = _next_backup_path(target)
        try:
            shutil.copy2(target, backup)
        except OSError as exc:
            raise ExpressionError(f"无法创建备份：{backup}") from exc

    _write_text_atomic(target, text, "写入表达式失败")
    return target


def update_expression_kind(path: str | Path, kind: str) -> Path:
    """Rewrite only ``Meta.studioKind`` of an existing expression file."""
    if kind and kind not in EXPRESSION_KINDS:
        raise ExpressionError(f"不支持的绑定类型：{kind}")
    target = Path(path).expanduser().resolve()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExpressionError(f"无法读取表达式：{target}") from exc
    except json.JSONDecodeError as exc:
        raise ExpressionError(f"表达式格式错误：{target}") from exc
    if not isinstance(data, dict):
        raise ExpressionError("表达式根节点不是对象")

    meta = data.get("Meta")
    if not isinstance(meta, dict):
        meta = {}
    if kind:
        meta["studioKind"] = kind
        data["Meta"] = meta
    else:
        meta.pop("studioKind", None)
        if meta:
            data["Meta"] = meta
        else:
            data.pop("Meta", None)

    _write_text_atomic(target, dumps_expression(data), "写入表达式失败")
    return target


def register_expression(
    model3_path: str | Path,
    name: str,
    file_ref: str,
    *,
    keep_names: Iterable[str] | None = None,
) -> bool:
    """Add or update an entry in ``model3.json`` ``FileReferences.Expressions``.

    When ``keep_names`` is given, entries whose ``Name`` is not in that list are
    dropped, so the file mirrors the expression list currently shown in the
    editor (removed bindings disappear from ``model3.json`` on write).
    """
    path = Path(model3_path).expanduser().resolve()
    name = name.strip()
    file_ref = file_ref.replace("\\", "/").strip()
    if not name or not file_ref:
        raise ExpressionError("表情名称和文件路径不能为空")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExpressionError(f"无法读取 model3.json：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ExpressionError(f"model3.json 格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise ExpressionError("model3.json 根节点不是对象")

    references = data.setdefault("FileReferences", {})
    if not isinstance(references, dict):
        raise ExpressionError("model3.json 的 FileReferences 不是对象")
    expressions = references.setdefault("Expressions", [])
    if not isinstance(expressions, list):
        raise ExpressionError("model3.json 的 Expressions 不是数组")

    changed = False
    if keep_names is not None:
        keep = {str(entry).strip() for entry in keep_names}
        keep.add(name)
        kept = []
        for item in expressions:
            if isinstance(item, dict):
                entry_name = item.get("Name")
                # Entries without a usable name cannot be matched with the
                # editor list, so leave them untouched.
                if isinstance(entry_name, str) and entry_name.strip():
                    if entry_name.strip() not in keep:
                        changed = True
                        continue
            kept.append(item)
        expressions[:] = kept

    entry = None
    for item in expressions:
        if not isinstance(item, dict):
            continue
        if item.get("Name") == name or item.get("File") == file_ref:
            entry = item
            break
    if entry is None:
        expressions.append({"Name": name, "File": file_ref})
        changed = True
    elif entry.get("Name") != name or entry.get("File") != file_ref:
        entry["Name"] = name
        entry["File"] = file_ref
        changed = True

    if not changed:
        return False

    _write_model3(path, data)
    return True


def unregister_expression(model3_path: str | Path, name: str) -> bool:
    """Remove one expression entry by name from ``model3.json``."""
    path = Path(model3_path).expanduser().resolve()
    name = name.strip()
    if not name:
        return False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExpressionError(f"无法读取 model3.json：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ExpressionError(f"model3.json 格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise ExpressionError("model3.json 根节点不是对象")

    references = data.get("FileReferences")
    if not isinstance(references, dict):
        return False
    expressions = references.get("Expressions")
    if not isinstance(expressions, list):
        return False

    kept = []
    removed = False
    for item in expressions:
        if isinstance(item, dict) and item.get("Name") == name:
            removed = True
            continue
        kept.append(item)
    if not removed:
        return False

    expressions[:] = kept
    _write_model3(path, data)
    return True


def _write_model3(path: Path, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    backup = _next_backup_path(path)
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        raise ExpressionError(f"无法创建备份：{backup}") from exc
    _write_text_atomic(path, text, "写入 model3.json 失败")


def relative_expression_ref(model3_path: str | Path, target: str | Path) -> str:
    """Return a model3.json-friendly relative reference to ``target``."""
    model3 = Path(model3_path).expanduser().resolve()
    path = Path(target).expanduser().resolve()
    try:
        reference = path.relative_to(model3.parent)
    except ValueError:
        reference = path
    return str(reference).replace("\\", "/")


def _next_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
        suffix += 1
    return candidate


def parameter_lookup(
    parameters: Iterable[ParameterInfo],
) -> dict[str, ParameterInfo]:
    return {parameter.id: parameter for parameter in parameters}
