"""V3.9 批次 5.3 等价性钉住：observer 调用形态单源化（零变更重构）。

背景：``run_agent("observer", ...)`` 原有 9 处近复制（observer.py 7 处 + commit.py 2 处）
与两份逐字重复的 leg 闭包；重构后收敛为 ``_call_observer``（唯一调用形态）+
``_submit_observer_legs``（唯一并发提交点），retry hint 模板单源到 pipeline_common。

本文件用同一份 fixture 跑四条调用路径（单次 / 双腿串行 / 双腿并发 / 双腿+summary
并发）与重试路径，断言「同一条腿在不同路径上发出的 run_agent 调用形态完全一致」——
任何一处只改一半的回归（例如某条路径漏传 capability_override / profile_id）都会变红。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_commit import observer as _observer_mod
from packages.workflows.chapter_commit import pipeline as cc_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_OBSERVER_PROFILE = "mprof_observer_equivalence"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _empty_payload() -> dict:
    return {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def _invalid_character_change(chapter_id: str) -> dict:
    """业务校验必失败的 change：op='update' 但 before=None（state-delta-v0 §2.5）。"""
    return {
        "change_id": "chg_invalid_eq",
        "op": "update",
        "target_id": "char_absent_eq",
        "character_id": "char_absent_eq",
        "facet": "state",
        "field": "state.location",
        "before": None,
        "after": "京城",
        "confidence": 0.9,
        "evidence": {"chapter_id": chapter_id, "excerpt": "缺 before 的非法变更"},
        "risk_level": "LOW",
    }


def _observer_ctx(
    db_path: Path,
    *,
    chapter_id: str = "ch_call_shape",
    run_id: str = "run_call_shape",
    split: bool = True,
    parallel: bool = True,
    summary_parallel: bool = False,
) -> dict:
    return {
        "db_path": str(db_path),
        "run_id": run_id,
        "chapter_id": chapter_id,
        "observer_input": {
            "previous_state": {},
            "config": {"recent_event_ids": ["ev_1"]},
        },
        "mock_providers": {"observer": [json.dumps(_empty_payload(), ensure_ascii=False)]},
        "model_overrides": {"observer": _OBSERVER_PROFILE},
        "observer_split": split,
        "observer_parallel": parallel,
        "summary_parallel": summary_parallel,
        "_current_node_run_id": "nrun_call_shape",
    }


def _capture_runner(monkeypatch, calls: list[dict], *, first_payload: dict | None = None):
    """用假 runner 记录每次调用的 (payload, kwargs)，不落库、不依赖 provider。

    ``first_payload`` 只在**首次** observer 调用上返回（调用方 clear(calls) 后不重置），
    用于构造「首次输出非法 → 触发 retry」的场景。
    """
    served = {"first": False}

    def fake_run_agent(db_path, agent_name, payload, run_id, **kwargs):
        calls.append({
            "agent": agent_name,
            "payload": payload,
            "run_id": run_id,
            "kwargs": kwargs,
        })
        if agent_name != "observer":
            return {"skipped": True}
        if first_payload is not None and not served["first"]:
            served["first"] = True
            return first_payload
        # 其余调用：合法空 delta（7 空数组）——不引入 schema/业务噪声，只关心调用形态。
        return _empty_payload()

    monkeypatch.setattr(_observer_mod, "run_agent", fake_run_agent)


def _shapes_by_scope(calls: list[dict]) -> dict[str, dict]:
    """按 extraction_scope 归拢 observer 调用形态（单次路径无 scope → '<single>'）。"""
    out: dict[str, dict] = {}
    for call in calls:
        if call["agent"] != "observer":
            continue
        scope = call["payload"].get("extraction_scope", "<single>")
        assert scope not in out, f"同一次 _observer_node 内出现重复 scope: {scope}"
        out[scope] = call
    return out


def _insert_project_and_chapter(db_path: Path) -> str:
    project_id = new_id("prj")
    chapter_id = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, '等价性测试', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (project_id, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, 1, '等价性', '{}', 'REVIEWED', 'VISIBLE', NULL, ?, ?)",
            (chapter_id, project_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return chapter_id


# ---------------------------------------------------------------------------
# 1) 四条路径的同腿调用形态一致
# ---------------------------------------------------------------------------


def test_all_observer_paths_emit_identical_leg_call_shape(tmp_path: Path, monkeypatch):
    """串行 / 并发 / 并发+summary 三条拆分路径：同腿 payload 与 kwargs 逐字一致。

    另钉住单次（split off）路径的形态：``capability_override=None``（与不传等价，
    runner.py:286/289 的 ``capability_override or capability_for(...)``）+ 不注入
    ``extraction_scope``。
    """
    db_path = _fresh_db(tmp_path)
    calls: list[dict] = []
    _capture_runner(monkeypatch, calls)

    shapes: dict[str, dict[str, dict]] = {}
    for label, flags in (
        ("serial", {"parallel": False, "summary_parallel": False}),
        ("parallel", {"parallel": True, "summary_parallel": False}),
        ("parallel+summary", {"parallel": True, "summary_parallel": True}),
    ):
        calls.clear()
        cc_pipeline._observer_node(_observer_ctx(db_path, **flags))
        shapes[label] = _shapes_by_scope(calls)

    baseline = shapes["serial"]
    assert set(baseline) == {"entities", "narrative"}, sorted(baseline)

    for label, shape in shapes.items():
        assert set(shape) == {"entities", "narrative"}, (label, sorted(shape))
        for scope in ("entities", "narrative"):
            assert shape[scope]["payload"] == baseline[scope]["payload"], (
                f"{label}/{scope} 腿 payload 与串行基线不一致"
            )
            assert shape[scope]["kwargs"] == baseline[scope]["kwargs"], (
                f"{label}/{scope} 腿 run_agent kwargs 与串行基线不一致："
                f"{shape[scope]['kwargs']} != {baseline[scope]['kwargs']}"
            )

    # 拆分路径的腿形态：capability 锁 observer（V3.9.3）+ 单次 run 级 profile_id 透传
    for scope in ("entities", "narrative"):
        kwargs = baseline[scope]["kwargs"]
        assert kwargs["capability_override"] == "observer"
        assert kwargs["profile_id"] == _OBSERVER_PROFILE
        assert kwargs["expected"] == "observer"
        assert kwargs["node_run_id"] == "nrun_call_shape"
        assert baseline[scope]["run_id"] == "run_call_shape"

    # 单次（split off）路径：不覆盖 capability，payload 未按腿裁剪
    calls.clear()
    cc_pipeline._observer_node(_observer_ctx(db_path, split=False))
    single = _shapes_by_scope(calls)
    assert set(single) == {"<single>"}, sorted(single)
    assert single["<single>"]["kwargs"]["capability_override"] is None
    assert single["<single>"]["kwargs"]["profile_id"] == _OBSERVER_PROFILE
    assert "extraction_scope" not in single["<single>"]["payload"]


# ---------------------------------------------------------------------------
# 2) 重试腿（commit.py 路径）与首次腿（observer.py 路径）调用形态一致
# ---------------------------------------------------------------------------


def test_retry_leg_call_shape_matches_first_leg(tmp_path: Path, monkeypatch):
    """同一腿的 retry 调用与首次调用：kwargs 全等 + 同一份裁剪 payload + retry hint。"""
    db_path = _fresh_db(tmp_path)
    chapter_id = _insert_project_and_chapter(db_path)

    calls: list[dict] = []
    first_payload = _empty_payload()
    first_payload["character_changes"] = [_invalid_character_change(chapter_id)]
    _capture_runner(monkeypatch, calls, first_payload=first_payload)

    # 首次：observer 节点（串行双腿，entities 腿拿到非法 change、narrative 腿合法）
    ctx = _observer_ctx(
        db_path, chapter_id=chapter_id, run_id="run_retry_shape",
        parallel=False, summary_parallel=False,
    )
    first = cc_pipeline._observer_node(ctx)
    first_leg = _shapes_by_scope(calls)["entities"]

    # 重试：inject_validate 节点（errors 只涉及 character_changes → 只重跑 entities 腿）
    calls.clear()
    ctx["observer_payload"] = first["observer_payload"]
    ctx["observer_split_meta"] = first["observer_split_meta"]
    cc_pipeline._inject_validate_node(ctx)

    assert len(calls) == 1, f"应只重试 entities 腿 1 次，实际 {len(calls)} 次"
    retry_leg = calls[0]
    assert retry_leg["payload"]["extraction_scope"] == "entities"
    assert "_retry_hint" in retry_leg["payload"]
    assert "_retry_hint" not in first_leg["payload"]

    # kwargs 全等（refactor 前 retry 内联调用的参数集与首次腿一致）。
    # mock_script 例外：首次腿走 _filter_mock_for_leg（按 leg 重排 7 数组键序），
    # retry 腿在「单元素脚本」下按 _pick_retry_mock 原样透传——两者解析后的 JSON
    # 对象相同（MockProvider 消费的是解析结果），仅键序不同。
    assert {
        k: v for k, v in retry_leg["kwargs"].items() if k != "mock_script"
    } == {
        k: v for k, v in first_leg["kwargs"].items() if k != "mock_script"
    }, f"retry 腿 kwargs 与首次腿不一致：{retry_leg['kwargs']} != {first_leg['kwargs']}"
    retry_mock = retry_leg["kwargs"]["mock_script"]
    first_mock = first_leg["kwargs"]["mock_script"]
    assert isinstance(retry_mock, list) and isinstance(first_mock, list)
    assert [json.loads(x) for x in retry_mock] == [json.loads(x) for x in first_mock]
    # payload 除 retry hint 外相同；config 的白名单键是**已知**差异（非本次重构引入）：
    # retry payload 由 ctx['observer_input'] 重建（未经 _observer_node 的首次注入），
    # entities 腿 retry 因此不含 resolvable_hook_ids / resolvable_debt_ids——
    # 该白名单只有 narrative 腿消费；narrative 腿 retry 会在 _inject_validate_node
    # 内重新注入补齐（见 commit.py 的 V3.10 O-4 注释）。
    def _normalize(payload: dict) -> dict:
        out = {k: v for k, v in payload.items() if k != "_retry_hint"}
        cfg = dict(out.get("config") or {})
        for key in ("resolvable_hook_ids", "resolvable_debt_ids"):
            cfg.pop(key, None)
        out["config"] = cfg
        return out

    assert _normalize(retry_leg["payload"]) == _normalize(first_leg["payload"])
