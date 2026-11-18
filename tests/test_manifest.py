"""Tests for the website manifest export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from live2d_studio.core import (
    manifest_dict,
    manifest_path,
    plan_manifest,
    write_manifest,
)
from live2d_studio.core.params import read_expression_values


def _write_model3(root: Path, motions: dict, expressions: list[dict]) -> Path:
    data = {
        "Version": 3,
        "FileReferences": {
            "Moc": "model.moc3",
            "Motions": motions,
            "Expressions": expressions,
        },
    }
    path = root / "model.model3.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _write_exp(root: Path, name: str, parameters: list[dict]) -> None:
    data = {
        "Type": "Live2D Expression",
        "Parameters": parameters,
        "FadeInTime": 0.0,
        "FadeOutTime": 0.0,
    }
    target = root / "expressions" / f"{name}.exp3.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_manifest_path_replaces_model_suffix(tmp_path: Path):
    model = tmp_path / "foo.model3.json"
    assert manifest_path(model).name == "foo.live2d.json"
    assert manifest_path("bar.json").name == "bar.live2d.json"


def test_plan_motions_default_names_and_ids(tmp_path: Path):
    _write_model3(
        tmp_path,
        {
            "Idle": [{"File": "motions/idle.motion3.json"}],
            "TapHead": [{"File": "motions/head_a.motion3.json"}],
        },
        [],
    )
    plan = plan_manifest(tmp_path / "model.model3.json")
    assert [row.name for row in plan.rows] == ["idle", "head_a"]
    assert [row.source_group for row in plan.rows] == ["Idle", "TapHead"]
    assert len({row.id for row in plan.rows}) == 2


def test_plan_reuses_existing_names_and_groups(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "Idle": [{"File": "motions/idle.motion3.json"}],
            "TapHead": [{"File": "motions/head_a.motion3.json"}],
        },
        [],
    )
    manifest = manifest_path(model)
    manifest.write_text(
        json.dumps(
            {
                "motions": [
                    {
                        "id": "idle",
                        "name": "待机",
                        "group": "Idle",
                        "file": "motions/idle.motion3.json",
                    },
                    {
                        "id": "head-a",
                        "name": "摸头 1",
                        "group": "TapHead",
                        "file": "motions/head_a.motion3.json",
                    },
                ],
                "motionGroups": [
                    {"id": "idle", "name": "待机动作", "motions": ["idle"]},
                    {"id": "head", "name": "互动动作", "motions": ["head-a"]},
                ],
                "defaults": {
                    "entryMotion": "idle",
                    "expression": "微笑",
                    "wardrobe": {"hair": "short"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = plan_manifest(model, parameter_names={"ParamHairSwitch": "发型"})
    assert plan.rows[0].name == "待机"
    assert plan.rows[0].label_group == "待机动作"
    assert plan.rows[1].name == "摸头 1"
    assert plan.rows[1].label_group == "互动动作"


def test_plan_splits_wardrobe_and_expressions(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {"Idle": [{"File": "motions/idle.motion3.json"}]},
        [
            {"Name": "黑丝", "File": "expressions/黑丝.exp3.json"},
            {"Name": "黑丝（湿）", "File": "expressions/wet.exp3.json"},
            {"Name": "微笑", "File": "expressions/smile.exp3.json"},
            {"Name": "换脸", "File": "expressions/two_params.exp3.json"},
            {"Name": "乘法", "File": "expressions/multiply.exp3.json"},
            {"Name": "旧版", "File": "expressions/legacy.exp3.json"},
        ],
    )
    _write_exp(tmp_path, "黑丝", [{"Id": "ParamSilk", "Value": 0.0, "Blend": "Add"}])
    _write_exp(
        tmp_path,
        "wet",
        [{"Id": "ParamSilk", "Value": 1.0, "Blend": "Add"}],
    )
    _write_exp(
        tmp_path,
        "smile",
        [{"Id": "ParamMouthForm", "Value": 1.0, "Blend": "Add"}],
    )
    _write_exp(
        tmp_path,
        "two_params",
        [
            {"Id": "ParamEyeLOpen", "Value": 0.0},
            {"Id": "ParamEyeROpen", "Value": 0.0},
        ],
    )
    _write_exp(
        tmp_path,
        "multiply",
        [{"Id": "ParamSilk", "Value": 2.0, "Blend": "Multiply"}],
    )
    # Additive leftovers on one parameter should be summed into one option
    _write_exp(
        tmp_path,
        "legacy",
        [
            {"Id": "ParamSilk", "Value": -1.0, "Blend": "Add"},
            {"Id": "ParamSilk", "Value": 1.0, "Blend": "Add"},
        ],
    )

    plan = plan_manifest(
        model, parameter_names={"ParamSilk": "丝袜选择"}
    )
    assert len(plan.wardrobe) == 1
    group = plan.wardrobe[0]
    assert group["parameter"] == "ParamSilk"
    assert group["name"] == "丝袜选择"
    values = sorted(option["value"] for option in group["options"])
    assert values == [0.0, 0.0, 1.0]
    names = {option["name"] for option in group["options"]}
    assert {"黑丝", "黑丝（湿）", "旧版"} <= names

    # 单值参数、多参数与 Multiply 表情都保留为普通表情
    runtime_names = {item["runtimeName"] for item in plan.expressions}
    assert {"微笑", "换脸", "乘法"} <= runtime_names
    assert "黑丝" not in runtime_names


def test_defaults_prefer_idle_and_filter_stale_ids(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "TapHead": [{"File": "motions/head.motion3.json"}],
            "Idle": [{"File": "motions/idle.motion3.json"}],
        },
        [],
    )
    plan = plan_manifest(model)
    assert plan.defaults["entryMotion"] == "idle"


def test_write_manifest_backs_up_and_serializes(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {"Idle": [{"File": "motions/idle.motion3.json"}]},
        [],
    )
    plan = plan_manifest(model)
    plan.rows[0].name = "待机 1"
    plan.rows[0].label_group = "待机动作"

    target = write_manifest(plan)
    assert target == manifest_path(model)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["format"] == "atlas-live2d-manifest"
    assert data["version"] == 1
    assert data["motionGroups"][0]["name"] == "待机动作"
    assert data["motions"][0]["name"] == "待机 1"
    assert data["motions"][0]["group"] == "Idle"
    assert data["defaults"]["wardrobe"] == {}
    assert data["defaults"]["entryMotion"] == "idle"

    write_manifest(plan)
    assert list(tmp_path.glob("model.live2d.json.bak-*"))


def test_manifest_dict_creates_unique_group_ids(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "A": [
                {"File": "motions/a1.motion3.json"},
                {"File": "motions/a2.motion3.json"},
            ]
        },
        [],
    )
    plan = plan_manifest(model)
    plan.rows[0].label_group = "动作"
    plan.rows[1].label_group = "动作"
    first = manifest_dict(plan)
    second = manifest_dict(plan)
    assert first["motionGroups"] == second["motionGroups"]
    assert first["motionGroups"][0]["id"] == "动作"
    assert first["motionGroups"][0]["motions"] != []


def test_manifest_text_and_duration_are_reused(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "Idle": [
                {"File": "motions/idle.motion3.json"},
                {"File": "motions/idle2.motion3.json"},
            ],
            "TapHead": [{"File": "motions/head.motion3.json"}],
        },
        [],
    )
    manifest_path(model).write_text(
        json.dumps(
            {
                "defaultDuration": 5000,
                "motions": [
                    {
                        "id": "idle",
                        "name": "欢迎",
                        "group": "Idle",
                        "file": "motions/idle.motion3.json",
                        "text": "欢迎回来",
                        "duration": 3000,
                    },
                    {
                        "id": "head",
                        "name": "摸头",
                        "group": "TapHead",
                        "file": "motions/head.motion3.json",
                        "text": "摸头",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = plan_manifest(model)
    assert plan.default_duration == 5000
    # 再次编辑时沿用清单中的动作顺序（idle2 是清单里新增的动作，排在最后）
    assert [row.text for row in plan.rows] == ["欢迎回来", "摸头", ""]
    assert [row.duration for row in plan.rows] == [3000, None, None]

    target = write_manifest(plan)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["defaultDuration"] == 5000
    assert data["motions"][0]["text"] == "欢迎回来"
    assert data["motions"][0]["duration"] == 3000
    assert data["motions"][1]["text"] == "摸头"
    assert "text" not in data["motions"][2]


def test_motions_json_is_ignored(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {"Idle": [{"File": "motions/idle.motion3.json"}]},
        [],
    )
    (tmp_path / "model.motions.json").write_text(
        json.dumps({"motions": {"Idle": "旧台词"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    plan = plan_manifest(model)
    assert plan.rows[0].text == ""
    assert plan.default_duration is None


def test_unchecked_entries_are_excluded_and_restored(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "Idle": [{"File": "motions/idle.motion3.json"}],
            "TapHead": [{"File": "motions/head.motion3.json"}],
        },
        [
            {"Name": "微笑", "File": "expressions/smile.exp3.json"},
            {"Name": "短发", "File": "expressions/short.exp3.json"},
            {"Name": "长发", "File": "expressions/long.exp3.json"},
        ],
    )
    _write_exp(tmp_path, "smile", [{"Id": "ParamMouthForm", "Value": 1.0}])
    _write_exp(tmp_path, "short", [{"Id": "ParamHair", "Value": 0.0}])
    _write_exp(tmp_path, "long", [{"Id": "ParamHair", "Value": 1.0}])

    plan = plan_manifest(model, parameter_names={"ParamHair": "发型"})
    assert len(plan.wardrobe) == 1

    plan.rows[0].label_group = "待机动作"
    plan.rows[1].enabled = False
    plan.expressions[0]["enabled"] = False
    plan.wardrobe[0]["enabled"] = False

    target = write_manifest(plan)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["motions"]] == ["idle"]
    assert data["motionGroups"][0]["motions"] == ["idle"]
    assert data["expressions"] == []
    assert data["wardrobe"] == []
    assert data["defaults"]["entryMotion"] == "idle"
    assert data["excluded"] == {
        "expressions": ["微笑"],
        "motions": ["TapHead|motions/head.motion3.json"],
        "wardrobe": ["ParamHair"],
    }

    # Re-importing the manifest restores the same unchecked selection
    restored = plan_manifest(model, parameter_names={"ParamHair": "发型"})
    assert [row.enabled for row in restored.rows] == [True, False]
    assert restored.expressions[0]["enabled"] is False
    assert restored.wardrobe[0]["enabled"] is False


def test_unchecked_defaults_are_dropped(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {
            "Idle": [{"File": "motions/idle.motion3.json"}],
            "TapHead": [{"File": "motions/head.motion3.json"}],
        },
        [{"Name": "微笑", "File": "expressions/smile.exp3.json"}],
    )
    _write_exp(tmp_path, "smile", [{"Id": "ParamMouthForm", "Value": 1.0}])
    manifest_path(model).write_text(
        json.dumps(
            {
                "defaults": {
                    "entryMotion": "idle",
                    "expression": "微笑",
                    "wardrobe": {},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = plan_manifest(model)
    assert plan.defaults["entryMotion"] == "idle"
    assert plan.defaults["expression"] == "微笑"

    plan.rows[0].enabled = False
    plan.expressions[0]["enabled"] = False
    data = manifest_dict(plan)
    assert "entryMotion" not in data["defaults"]
    assert "expression" not in data["defaults"]


def test_read_expression_values_sums_additive_parameters(tmp_path: Path):
    path = tmp_path / "expressions" / "mixed.exp3.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "Type": "Live2D Expression",
                "Parameters": [
                    {"Id": "ParamA", "Value": 1.0, "Blend": "Add"},
                    {"Id": "ParamA", "Value": 0.5, "Blend": "Add"},
                    {"Id": "ParamB", "Value": 0.2},
                    {"Id": "ParamC", "Value": 2.0, "Blend": "Multiply"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert read_expression_values(path) == {"ParamA": 1.5, "ParamB": 0.2}
    assert read_expression_values(tmp_path / "missing.exp3.json") == {}


def test_expression_aliases_are_reused(tmp_path: Path):
    model = _write_model3(
        tmp_path,
        {"Idle": [{"File": "motions/idle.motion3.json"}]},
        [{"Name": "微笑", "File": "expressions/smile.exp3.json"}],
    )
    _write_exp(tmp_path, "smile", [{"Id": "ParamMouthForm", "Value": 1.0}])
    manifest_path(model).write_text(
        json.dumps(
            {
                "expressions": [
                    {"id": "happy", "name": "开心", "runtimeName": "微笑"}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = plan_manifest(model)
    assert plan.expressions[0]["id"] == "happy"
    assert plan.expressions[0]["name"] == "开心"
    assert plan.expressions[0]["runtimeName"] == "微笑"
