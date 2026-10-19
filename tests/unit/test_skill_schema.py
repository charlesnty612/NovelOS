"""skill payload 的 schema 校验（唯一权威 = ``docs/state-model/schemas/skill.schema.json``）。

本文件钉住的**不变量**（每条都对应一条设计约束，不是实现细节）：

1. **合法 payload 通过**；缺必填字段 → 报错串点名该字段。
2. **顶层禁字段必须被拒**（``additionalProperties: false``）：``version`` 这类
   「DB 列名」不是 payload 键——它会与 ``skills.version`` 形成两个真相源。
3. **可测指标字段必须被拒**（F-19 红线）：``ratio`` / ``threshold`` / ``score`` /
   ``weight`` / ``target_ratio`` 一律不得进 payload——一旦进去就会被模型当成指标去凑
   （「对话占比」的实证）。
4. **不设 ``scripts``**（R6）：执行外部代码 ⇒ 安全 + 三仓分离红线。
5. **不设 ``prompt`` / ``system_prompt``**：会打破 ``prompt_version`` 单源
   （模型回声与落库版本分叉）。
6. ``applies_to`` 只接受 5 个已知环节；``check_id`` 只接受小写形态。
7. 错误串形态 ``"[schema] <path>: <message>"``（与题材包 / canon 校验器同款）。

零真实 LLM、零内容仓接触、零 dev 库。
"""

from __future__ import annotations

from packages.core.skill import SKILL_SCHEMA_PATH, validate_payload

from .skill_fixtures import valid_payload


def test_valid_payload_passes():
    """一份完整合法 payload → 零错误。"""
    payload = valid_payload(
        applies_to=["writer", "critic"],
        references=[{"path": "skills/x/ref.md", "title": "示例", "body": "示例正文"}],
        checks=[{"check_id": "exposed", "description": "直白情绪词", "keywords": ["其实"]}],
    )
    assert validate_payload(payload) == []


def test_required_fields_are_reported_by_name():
    """缺必填字段 → 错误串点名该字段（不是一句笼统的「invalid」）。"""
    for missing in ("schema_version", "name", "instructions"):
        payload = valid_payload()
        payload.pop(missing)
        errors = validate_payload(payload)
        assert errors, f"缺 {missing} 必须报错"
        assert any(missing in e for e in errors), (
            f"错误串必须点名 {missing}，实际：{errors}"
        )


def test_top_level_version_is_rejected():
    """**顶层 ``version`` 是禁字段**：它是 DB 列，不是 payload 键。

    放行它会造出「两个真相源」——``skills.version``（表列）与 ``payload.version``
    （payload 键）各说各话，而缓存键指纹只认前者。
    """
    payload = {**valid_payload(), "version": 3}
    errors = validate_payload(payload)
    assert errors, "顶层 version 必须被拒"
    assert any("version" in e for e in errors), f"实际：{errors}"


def test_measurable_metric_fields_are_rejected():
    """F-19 红线：可测指标字段一律不得进 payload。

    题材包那次事故（对话占比写成硬指标 ⇒ 模型灌对白）证明：指标一旦进生成驱动，
    模型就会去凑。本仓的 skill 是**外部可移植的手艺**，带指标进去等于把 F-19 重开。
    """
    for field, value in (
        ("ratio", 0.4),
        ("threshold", 0.25),
        ("score", 90),
        ("weight", 0.2),
        ("target_ratio", 0.3),
    ):
        payload = {**valid_payload(), field: value}
        errors = validate_payload(payload)
        assert errors, f"{field} 必须被拒"
        assert any(field in e for e in errors), f"{field} 错误串未点名：{errors}"


def test_scripts_field_is_rejected():
    """R6：``scripts`` 不得存在（执行外部代码 ⇒ 安全 + 三仓分离红线）。"""
    payload = {**valid_payload(), "scripts": ["python evil.py"]}
    errors = validate_payload(payload)
    assert errors, "scripts 必须被拒"
    assert any("scripts" in e for e in errors), f"实际：{errors}"


def test_prompt_field_is_rejected():
    """``prompt`` / ``system_prompt`` 不得存在（会打破 prompt_version 单源）。"""
    for field in ("prompt", "system_prompt"):
        payload = {**valid_payload(), field: "你是一个大师"}
        errors = validate_payload(payload)
        assert errors, f"{field} 必须被拒"
        assert any(field in e for e in errors), f"{field} 错误串未点名：{errors}"


def test_applies_to_rejects_unknown_consumer():
    """``applies_to`` 只接受 5 个已知环节（与装配侧白名单逐字一致）。"""
    payload = valid_payload(applies_to=["writer", "summarizer"])
    errors = validate_payload(payload)
    assert errors, "未知环节必须被拒"
    assert any("applies_to" in e for e in errors), f"实际：{errors}"


def test_applies_to_accepts_all_five_consumers():
    """5 个环节全部合法（与装配侧 ``_SKILL_CONSUMERS`` 逐字同形）。"""
    payload = valid_payload(
        applies_to=["director", "scene_planner", "writer", "critic", "deep_reviewer"],
    )
    assert validate_payload(payload) == []


def test_check_id_pattern_is_enforced():
    """``check_id`` 只接受小写字母数字与 ``. _ -``（rule_id 形态可控）。"""
    payload = valid_payload(checks=[{"check_id": "Bad Id", "description": "x"}])
    errors = validate_payload(payload)
    assert errors, "非法 check_id 必须被拒"
    assert any("check_id" in e for e in errors), f"实际：{errors}"


def test_reference_body_length_cap():
    """reference 正文上限 20000 字符（schema 侧封顶，装配层再兜一刀）。"""
    payload = valid_payload(
        references=[{"path": "p", "title": "t", "body": "字" * 20001}],
    )
    errors = validate_payload(payload)
    assert errors, "超长 reference body 必须被拒"


def test_non_dict_payload_is_rejected():
    """非 dict（list / str / None）→ 单条 ``[schema] <root>`` 错误。"""
    for bad in ([], "x", None, 3):
        errors = validate_payload(bad)
        assert errors, f"{bad!r} 必须报错"
        assert errors[0].startswith("[schema] <root>:"), f"实际：{errors[0]}"


def test_error_format_is_schema_prefixed():
    """错误串形态 ``"[schema] <path>: <message>"``（与题材包校验器同款）。"""
    errors = validate_payload({**valid_payload(), "bogus": 1})
    assert errors
    assert all(e.startswith("[schema] ") for e in errors), f"实际：{errors}"


def test_schema_file_exists_and_declares_closed_top_level():
    """schema 文件在位，且顶层 ``additionalProperties: false``（纪律的机器可读形态）。"""
    import json

    schema = json.loads(SKILL_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "name", "instructions"]
    assert set(schema["properties"]) == {
        "schema_version", "name", "description", "instructions",
        "applies_to", "references", "checks",
    }
    # 故意不设的字段，必须**不在** properties 里（不是「不在但被 required 兜住」）。
    for absent in ("scripts", "ratio", "threshold", "score", "weight", "target_ratio",
                   "prompt", "system_prompt"):
        assert absent not in schema["properties"], f"{absent} 不得出现在 schema 里"
