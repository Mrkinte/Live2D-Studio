from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from live2d_studio.core.moc3_document import Moc3Document
from live2d_studio.core.params import (
    ExpressionError,
    build_expression,
    dumps_expression,
    read_expression_values,
    read_parameter_names,
    register_expression,
    relative_expression_ref,
    save_expression,
)

MODEL_DIR = Path(
    r"C:\Users\Marjin\Documents\DeepSeek Harness\hide-moc3\test\aersasi_3"
)
MOC3 = MODEL_DIR / "aersasi_3.moc3"
MODEL3 = MODEL_DIR / "aersasi_3.model3.json"
CDI3 = MODEL_DIR / "aersasi_3.cdi3.json"

pytestmark = pytest.mark.skipif(
    not MOC3.exists() or not MODEL3.exists() or not CDI3.exists(),
    reason="test/aersasi_3 fixture is not present",
)


@pytest.fixture(scope="module")
def document() -> Moc3Document:
    return Moc3Document.from_model3(MODEL3)


def test_parameters_match_moc3_and_cdi3(document: Moc3Document) -> None:
    assert len(document.parameters) == 360
    parameter = document.parameter_by_id["touch_drag13"]
    assert (parameter.minimum, parameter.maximum) == (-1.0, 1.0)
    assert parameter.default == 0.0
    assert parameter.decimals == 3
    assert document.parameter_name("touch_drag13") == "丝袜选择"
    assert document.parameter_name("unknown_param") == "unknown_param"


def test_read_parameter_names_only_keeps_strings() -> None:
    names = read_parameter_names(CDI3)
    assert names["touch_drag13"] == "丝袜选择"
    assert names["touch_drag12"] == "丝袜弹出"
    assert read_parameter_names(MODEL3) == {}


def test_build_expression() -> None:
    data = build_expression({"touch_drag13": -1.0, "touch_drag12": 1.0})
    assert data["Type"] == "Live2D Expression"
    assert data["Parameters"] == [
        {"Id": "touch_drag13", "Value": -1.0, "Blend": "Add"},
        {"Id": "touch_drag12", "Value": 1.0, "Blend": "Add"},
    ]
    with pytest.raises(ExpressionError, match="没有要写入"):
        build_expression({})
    with pytest.raises(ExpressionError, match="Blend"):
        build_expression({"touch_drag13": 1.0}, blend="Absolute")


def test_save_expression_and_backup(tmp_path: Path) -> None:
    target = tmp_path / "expressions" / "黑丝.exp3.json"
    saved = save_expression(target, {"touch_drag13": -1.0})
    assert saved == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["Parameters"][0]["Id"] == "touch_drag13"
    assert data["Parameters"][0]["Value"] == -1.0

    save_expression(target, {"touch_drag13": 1.0})
    backups = sorted(tmp_path.glob("expressions/*.bak-*"))
    assert backups
    assert "touch_drag13" in backups[0].read_text(encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8"))["Parameters"][0][
        "Value"
    ] == 1.0


def test_register_expression(tmp_path: Path) -> None:
    model3 = tmp_path / "aersasi_3.model3.json"
    shutil.copy2(MODEL3, model3)
    expression = tmp_path / "expressions" / "黑丝.exp3.json"
    save_expression(expression, {"touch_drag13": -1.0})
    reference = relative_expression_ref(model3, expression)
    assert reference == "expressions/黑丝.exp3.json"

    assert register_expression(model3, "黑丝", reference) is True
    data = json.loads(model3.read_text(encoding="utf-8"))
    assert data["FileReferences"]["Expressions"] == [
        {"Name": "黑丝", "File": "expressions/黑丝.exp3.json"}
    ]
    assert sorted(tmp_path.glob("aersasi_3.model3.json.bak-*"))
    assert register_expression(model3, "黑丝", reference) is False


def test_register_expression_prunes_removed_entries(tmp_path: Path) -> None:
    model3 = tmp_path / "aersasi_3.model3.json"
    data = {
        "FileReferences": {
            "Expressions": [
                {"Name": "黑丝", "File": "expressions/黑丝.exp3.json"},
                {"Name": "旧表情", "File": "expressions/旧表情.exp3.json"},
                {"Name": "", "File": "expressions/unknown.exp3.json"},
            ]
        }
    }
    model3.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    reference = "expressions/白丝.exp3.json"
    assert (
        register_expression(
            model3, "白丝", reference, keep_names=["黑丝", "白丝"]
        )
        is True
    )
    expressions = json.loads(model3.read_text(encoding="utf-8"))[
        "FileReferences"
    ]["Expressions"]
    assert expressions == [
        {"Name": "黑丝", "File": "expressions/黑丝.exp3.json"},
        {"Name": "", "File": "expressions/unknown.exp3.json"},
        {"Name": "白丝", "File": reference},
    ]


def test_dumps_expression_is_utf8() -> None:
    text = dumps_expression(build_expression({"touch_drag13": 0.5}))
    assert "touch_drag13" in text
    assert "\n" in text
    assert "\\u" not in text
