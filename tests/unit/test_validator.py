"""validate_delta 单测（Sprint 2）。

覆盖：
- 合法 delta → 0 错误。
- 缺 required（顶层 chapter_id 缺失 / character_changes 中 character_id 缺失）→ schema 错误。
- 非法枚举（risk_level=FATAL / facet=fake / world_kind=nowhere）→ schema 错误。
- 业务规则：evidence.chapter_id 不等于 chapter_id → business 错误。
- change_id 重复 → business 错误。

注：构造合法 delta 时按 ``docs/state-model/schemas/state-delta.schema.json`` 字段填，
只关心 schema 校验与业务校验分支。
"""

from __future__ import annotations

from packages.core.story_state.validator import validate_delta


def _good_delta() -> dict:
    """最小合法 delta（schema 完整形态）。"""
    return {
        "delta_id": "dlt_aaaaaaaaaaaaaaaa",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def _evidence(chapter_id: str = "ch_test") -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def test_legal_delta_passes():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert errors == []


def test_missing_required_chapter_id():
    delta = _good_delta()
    delta.pop("chapter_id")
    errors = validate_delta(delta)
    assert any("chapter_id" in e for e in errors)


def test_missing_character_id_in_change():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            # character_id omitted
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("character_id" in e for e in errors)


def test_invalid_risk_level_enum():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "FATAL",
        }
    ]
    errors = validate_delta(delta)
    assert any("risk_level" in e for e in errors)


def test_invalid_facet_enum():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "fake",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("facet" in e for e in errors)


def test_invalid_world_kind_enum():
    delta = _good_delta()
    delta["world_changes"] = [
        {
            "change_id": "wc_1",
            "op": "update",
            "target_id": "loc_village",
            "world_kind": "nowhere",
            "world_id": "loc_village",
            "field": "population",
            "before": 100,
            "after": 200,
            "confidence": 0.8,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("world_kind" in e for e in errors)


def test_evidence_chapter_id_mismatch_is_business_error():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(chapter_id="ch_other"),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("business" in e and "evidence.chapter_id" in e for e in errors)


def test_duplicate_change_id_is_business_error():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_dup",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["world_changes"] = [
        {
            "change_id": "cc_dup",  # duplicate
            "op": "update",
            "target_id": "loc_village",
            "world_kind": "location",
            "world_id": "loc_village",
            "field": "population",
            "before": 100,
            "after": 200,
            "confidence": 0.8,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("重复" in e or "duplicate" in e.lower() for e in errors)


def test_invalid_confidence_out_of_range():
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 1.5,  # > 1
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert any("confidence" in e for e in errors)


def test_schema_version_must_be_state_delta_v0():
    delta = _good_delta()
    delta["schema_version"] = "state-delta-v9"
    errors = validate_delta(delta)
    assert any("schema_version" in e for e in errors)


def test_delta_version_must_be_1():
    delta = _good_delta()
    delta["delta_version"] = 2
    errors = validate_delta(delta)
    assert any("delta_version" in e for e in errors)
