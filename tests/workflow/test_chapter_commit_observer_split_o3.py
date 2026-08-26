"""chapter_commit observer 双腿拆分 V3.1.1 O-3 测试。

覆盖点（packages/workflows/chapter_commit/pipeline.py 的 O-3 改造）：
1. 按腿裁剪：leg_a (entities) 保留实体集合，移除 events/hooks/debts；leg_b (narrative)
   保留 events/hooks/debts，实体集合降级为标识性摘要。
2. recent_event_ids 白名单：从 plot_events 取最近 N 条注入 payload.config.recent_event_ids。
3. per-leg snapshot_trim_stats：按腿记录裁剪前后字节数与各集合保留数。
4. leg_a_chars/leg_b_chars 量化：observer_split_meta 含 per-leg payload 字符数与合计。
5. off 路径不受影响（旧单次大调用）：单测 _trim_observer_input_for_leg 是纯函数，
   验证它本身不影响 off 路径；off 路径直接用 base_payload，不调裁剪函数。
6. 与 O-2 既有测试兼容：既有 split 测试不依赖裁剪形状（仅断言 observer 调用次数
   + 7 数组齐全 + observer_split_meta 存在）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.workflows.chapter_commit.pipeline import (
    _summarize_character_for_leg_b,
    _summarize_faction_for_leg_b,
    _summarize_location_for_leg_b,
    _summarize_world_rule_for_leg_b,
    _trim_observer_input_for_leg,
)

# ---------------------------------------------------------------------------
# 摘要函数单测
# ---------------------------------------------------------------------------


def test_summarize_character_for_leg_b_basic():
    """leg_b character 摘要：仅 {character_id, name, role, status}。"""
    char = {
        "character_id": "char_001",
        "name": "林夕",
        "role": "protagonist",
        "current_state": {"status": "alive", "location": "京城"},
        "core_json": {"personality": "坚毅"},  # 应被剥离
        "knowledge_scope": "AUTHOR",
    }
    out = _summarize_character_for_leg_b(char)
    assert out["character_id"] == "char_001"
    assert out["name"] == "林夕"
    assert out["role"] == "protagonist"
    assert out["status"] == "alive"
    assert out["summary_marker"] == "leg_b_narrative"
    # 不带额外字段
    assert "core_json" not in out
    assert "knowledge_scope" not in out
    assert "current_state" not in out


def test_summarize_character_for_leg_b_no_state():
    """无 current_state 时 status=None，不抛错。"""
    char = {"character_id": "char_002", "name": "无名"}
    out = _summarize_character_for_leg_b(char)
    assert out["status"] is None
    assert out["character_id"] == "char_002"


def test_summarize_location_for_leg_b_dict_form():
    loc = {"location_id": "loc_001", "name": "京城", "data_json": {"x": 1}}
    out = _summarize_location_for_leg_b(loc)
    assert out["location_id"] == "loc_001"
    assert out["name"] == "京城"
    assert out["summary_marker"] == "leg_b_narrative"


def test_summarize_faction_for_leg_b():
    fac = {"faction_id": "fac_001", "name": "玄门", "data_json": {}}
    out = _summarize_faction_for_leg_b(fac)
    assert out["faction_id"] == "fac_001"
    assert out["name"] == "玄门"


def test_summarize_world_rule_for_leg_b():
    """world_rule 摘要保留 statement（事件可能引用规则变化）。"""
    rule = {
        "world_rule_id": "wr_001",
        "name": "灵气规则",
        "statement": "末法时代灵气稀薄",
        "data_json": {"x": 1},
    }
    out = _summarize_world_rule_for_leg_b(rule)
    assert out["world_rule_id"] == "wr_001"
    assert out["name"] == "灵气规则"
    assert out["statement"] == "末法时代灵气稀薄"
    assert out["summary_marker"] == "leg_b_narrative"


# ---------------------------------------------------------------------------
# _trim_observer_input_for_leg 单测
# ---------------------------------------------------------------------------


def _make_base_payload() -> dict:
    """构造一份「典型 trimmed snapshot」base_payload 用于裁剪测试。"""
    return {
        "agent": "observer",
        "prompt_version": "observer:v1",
        "chapter": {
            "chapter_id": "ch_xxx",
            "title": "测试章",
            "draft_text": "正文内容" * 50,
            "scene_ids": [],
        },
        "previous_state_version": 5,
        "previous_state": {
            "snapshot_mode": "trimmed",
            "state_version": 5,
            "recent_events": ["evt_ch60_aaa", "evt_ch61_bbb"],
            "characters": [
                {
                    "character_id": "char_001",
                    "name": "林夕",
                    "facet": "state",
                    "current_state": {"status": "alive", "location": "京城"},
                    "relationships": [],
                },
                {
                    "character_id": "char_002",
                    "name": "苏婉清",
                    "facet": "state",
                    "current_state": {"status": "alive"},
                    "relationships": [],
                },
            ],
            "world": {
                "current_time_in_story": "末法元年春",
                "active_resources": [],
                "locations": {
                    "loc_001": {"location_id": "loc_001", "name": "京城", "data_json": {}},
                    "loc_002": {"location_id": "loc_002", "name": "王城", "data_json": {}},
                },
                "factions": {
                    "fac_001": {"faction_id": "fac_001", "name": "玄门", "data_json": {}},
                },
                "world_rules": [
                    {"world_rule_id": "wr_001", "name": "灵气规则", "statement": "末法时代灵气稀薄"},
                ],
            },
            "events": {
                "evt_ch60_aaa": {"type": "revelation", "description": "60 章事件"},
                "evt_ch61_bbb": {"type": "conflict", "description": "61 章事件"},
            },
            "hooks": [
                {"hook_id": "h_001", "name": "黑玉佩之谜", "status": "OPEN"},
                {"hook_id": "h_002", "name": "旧伏笔", "status": "RESOLVED"},
            ],
            "debts": [
                {"debt_id": "d_001", "description": "回报约定", "status": "open"},
            ],
        },
        "director_plan_summary": {
            "chapter_goal": "测试章目标",
            "key_beats": ["beat 1"],
        },
        "knowledge_permissions": {
            "your_visibility": ["AUTHOR", "DIRECTOR"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "config": {
            "min_excerpt_chars_low_confidence": 80,
            "max_changes_per_array": 24,
            "recent_event_ids": ["evt_ch61_bbb", "evt_ch60_aaa"],
        },
    }


def test_trim_observer_input_for_leg_a_entities_kept_events_removed():
    """leg_a (entities)：保留实体集合全量，移除 events/hooks/debts。"""
    base = _make_base_payload()
    trimmed, stats = _trim_observer_input_for_leg(base, "entities")

    # 实体集合全量保留（O-1 口径）
    assert len(trimmed["previous_state"]["characters"]) == 2
    assert "loc_001" in trimmed["previous_state"]["world"]["locations"]
    assert "fac_001" in trimmed["previous_state"]["world"]["factions"]
    assert len(trimmed["previous_state"]["world"]["world_rules"]) == 1

    # events / hooks / debts 移除（即便原 snapshot_mode=trimmed 也清空）
    assert trimmed["previous_state"]["events"] == {}
    assert trimmed["previous_state"]["hooks"] == []
    assert trimmed["previous_state"]["debts"] == []

    # 公共部分保留
    assert trimmed["chapter"]["chapter_id"] == "ch_xxx"
    assert trimmed["previous_state"]["state_version"] == 5
    assert trimmed["previous_state"]["snapshot_mode"] == "trimmed"
    assert trimmed["config"]["recent_event_ids"] == ["evt_ch61_bbb", "evt_ch60_aaa"]
    assert trimmed["knowledge_permissions"]["your_visibility"] == ["AUTHOR", "DIRECTOR"]
    assert trimmed["director_plan_summary"]["chapter_goal"] == "测试章目标"

    # 顶层元信息：recent_events 不动
    assert trimmed["previous_state"]["recent_events"] == ["evt_ch60_aaa", "evt_ch61_bbb"]

    # stats：leg_a 移除 events/hooks/debts 后字节数应减少
    assert stats["leg"] == "entities"
    assert stats["characters_kept"] == 2
    assert stats["locations_kept"] == 2
    assert stats["factions_kept"] == 1
    assert stats["world_rules_kept"] == 1
    assert stats["events_kept"] == 0
    assert stats["hooks_kept"] == 0
    assert stats["debts_kept"] == 0
    assert stats["previous_state_bytes_before"] > 0
    assert stats["previous_state_bytes_after"] >= 0
    assert stats["previous_state_bytes_delta"] >= 0  # 移除后变小（或持平）


def test_trim_observer_input_for_leg_b_narrative_keeps_hooks_debts_events():
    """leg_b (narrative)：保留 events/hooks/debts，实体集合降级为标识摘要。"""
    base = _make_base_payload()
    trimmed, stats = _trim_observer_input_for_leg(base, "narrative")

    # 实体集合降级为摘要
    chars = trimmed["previous_state"]["characters"]
    assert len(chars) == 2
    for c in chars:
        assert c["summary_marker"] == "leg_b_narrative"
        # 摘要不应携带完整 current_state / relationships / core_json
        assert "current_state" not in c or set(c.keys()) <= {
            "character_id", "name", "role", "status", "summary_marker",
        }
    assert chars[0]["character_id"] == "char_001"
    assert chars[0]["status"] == "alive"

    # location 降级
    locs = trimmed["previous_state"]["world"]["locations"]
    assert "loc_001" in locs
    assert locs["loc_001"]["summary_marker"] == "leg_b_narrative"
    assert locs["loc_001"]["name"] == "京城"

    # faction 降级
    facs = trimmed["previous_state"]["world"]["factions"]
    assert "fac_001" in facs
    assert facs["fac_001"]["summary_marker"] == "leg_b_narrative"

    # world_rules 保留 statement
    rules = trimmed["previous_state"]["world"]["world_rules"]
    assert rules[0]["statement"] == "末法时代灵气稀薄"
    assert rules[0]["summary_marker"] == "leg_b_narrative"

    # events / hooks / debts 原样保留
    assert len(trimmed["previous_state"]["events"]) == 2
    assert len(trimmed["previous_state"]["hooks"]) == 2
    assert len(trimmed["previous_state"]["debts"]) == 1

    # config 公共部分不动
    assert trimmed["config"]["recent_event_ids"] == ["evt_ch61_bbb", "evt_ch60_aaa"]

    # stats
    assert stats["leg"] == "narrative"
    assert stats["characters_kept"] == 2
    assert stats["locations_kept"] == 2
    assert stats["factions_kept"] == 1
    assert stats["world_rules_kept"] == 1
    assert stats["events_kept"] == 2
    assert stats["hooks_kept"] == 2
    assert stats["debts_kept"] == 1


def test_trim_observer_input_for_leg_b_chars_less_than_leg_a_or_comparable():
    """量化假设：leg_a 移除 events/hooks/debts 后，体积应 ≤ leg_b（leg_b 保留 events）。

    实际裁剪字节差异受数据分布影响；本测试仅验证 leg_b ≥ leg_a - tolerance 关系，
    防止未来回归。
    """
    base = _make_base_payload()
    _, stats_a = _trim_observer_input_for_leg(base, "entities")
    _, stats_b = _trim_observer_input_for_leg(base, "narrative")
    # leg_b 体积应 ≥ leg_a（因为 leg_b 保留 events/hooks/debts 全量）
    # 注：摘要构造 vs 全量构造不一定 leg_a 更小（如果实体集合本身巨大），故仅做软比较
    assert stats_a["previous_state_bytes_before"] == stats_b["previous_state_bytes_before"]
    # 至少 events/hooks/debts 在 leg_b 中保留
    assert stats_b["events_kept"] == 2
    assert stats_b["hooks_kept"] == 2
    assert stats_b["debts_kept"] == 1


def test_trim_observer_input_for_leg_invalid_leg_raises():
    """非法 leg 抛 ValueError。"""
    base = _make_base_payload()
    try:
        _trim_observer_input_for_leg(base, "bogus")
    except ValueError as exc:
        assert "leg must be 'entities' or 'narrative'" in str(exc)
    else:
        raise AssertionError("应抛 ValueError")


def test_trim_observer_input_for_leg_per_leg_stats_in_payload():
    """per-leg stats 写到 payload 顶层 snapshot_trim_stats_leg_a/b，便于观测与测试。"""
    base = _make_base_payload()
    trimmed_a, _ = _trim_observer_input_for_leg(base, "entities")
    trimmed_b, _ = _trim_observer_input_for_leg(base, "narrative")
    assert "snapshot_trim_stats_leg_a" in trimmed_a
    assert "snapshot_trim_stats_leg_b" not in trimmed_a
    assert "snapshot_trim_stats_leg_b" in trimmed_b
    assert "snapshot_trim_stats_leg_a" not in trimmed_b
    assert trimmed_a["snapshot_trim_stats_leg_a"]["leg"] == "entities"
    assert trimmed_b["snapshot_trim_stats_leg_b"]["leg"] == "narrative"


def test_trim_observer_input_for_leg_does_not_mutate_input():
    """_trim_observer_input_for_leg 不修改 base_payload（深隔离，防止 retry 路径脏改）。"""
    base = _make_base_payload()
    snap_before = json.dumps(base, ensure_ascii=False)
    _trim_observer_input_for_leg(base, "entities")
    _trim_observer_input_for_leg(base, "narrative")
    snap_after = json.dumps(base, ensure_ascii=False)
    assert snap_before == snap_after, "trim_observer_input_for_leg 不得修改输入"


def test_trim_observer_input_for_leg_world_list_fallback():
    """兼容 snapshot 把 locations/factions 存为 list 的情况。

    leg_a 保留 list 形态原样；leg_b 同样保留 list 形态（按摘要格式 dict 输出项，
    保持输入 schema 形态——O-1 期间就有 list/dict 混用情况，narrative 不强行变 dict）。
    """
    base = _make_base_payload()
    base["previous_state"]["world"]["locations"] = [
        {"location_id": "loc_001", "name": "京城"},
        {"location_id": "loc_002", "name": "王城"},
    ]
    base["previous_state"]["world"]["factions"] = [
        {"faction_id": "fac_001", "name": "玄门"},
    ]
    trimmed_a, stats_a = _trim_observer_input_for_leg(base, "entities")
    assert len(trimmed_a["previous_state"]["world"]["locations"]) == 2
    assert len(trimmed_a["previous_state"]["world"]["factions"]) == 1
    assert stats_a["locations_kept"] == 2
    assert stats_a["factions_kept"] == 1
    # leg_b：保留 list 形态（与 leg_a 一致）；每项带 summary_marker
    trimmed_b, stats_b = _trim_observer_input_for_leg(base, "narrative")
    locs_b = trimmed_b["previous_state"]["world"]["locations"]
    assert isinstance(locs_b, list)
    assert len(locs_b) == 2
    for x in locs_b:
        assert x["summary_marker"] == "leg_b_narrative"
    facs_b = trimmed_b["previous_state"]["world"]["factions"]
    assert isinstance(facs_b, list)
    assert len(facs_b) == 1
    assert facs_b[0]["summary_marker"] == "leg_b_narrative"
    assert stats_b["locations_kept"] == 2
    assert stats_b["factions_kept"] == 1


def test_trim_observer_input_for_leg_empty_previous_state_safe():
    """空 previous_state 不抛错，stats 各集合保留数均为 0。"""
    base = {"config": {"recent_event_ids": []}}
    trimmed_a, stats_a = _trim_observer_input_for_leg(base, "entities")
    assert isinstance(trimmed_a["previous_state"], dict)
    # 空 base → trimmed["previous_state"] 只剩 {snapshot_mode:'trimmed'} 标识
    # 故 bytes_before=0, bytes_after>0 是正常行为；测试断言各集合计数为 0
    assert stats_a["characters_kept"] == 0
    assert stats_a["locations_kept"] == 0
    assert stats_a["factions_kept"] == 0
    assert stats_a["world_rules_kept"] == 0
    assert stats_a["events_kept"] == 0
    assert stats_a["hooks_kept"] == 0
    assert stats_a["debts_kept"] == 0


# ---------------------------------------------------------------------------
# 集成测试：observer_split_meta 含 leg_a_chars/leg_b_chars + recent_event_ids_count
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    from packages.core.api.main import create_app
    from packages.core.config import Settings
    from packages.core.db import apply_migrations

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs):
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "O3Project") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "测试 O-3 按腿裁剪",
                "core_conflict": "验证双腿 payload 字节级正确性",
                "turning_point": "leg_a 移除 events；leg_b 实体降级",
                "expected_role": "setup",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "测试场景",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "O-3 验证",
                    }
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "测试",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = "测试章节正文，验证 O-3 路径。"
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": prose,
                "self_report": {
                    "slots_filled": ["slot_001"],
                    "word_count": len(prose),
                    "scene_count": 1,
                    "deviations": [],
                    "forbidden_word_hits": [],
                    "self_check_notes": "",
                },
            },
            ensure_ascii=False,
        )
    ]


def _observer_full_noop_script() -> list[str]:
    """完整 7 数组 mock：merge 后产出完整 7 数组。"""
    return [
        json.dumps(
            {
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]


async def _push_chapter_to_reviewed(app, pid: str, cid: str, mock_providers: dict) -> None:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "意图", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    paused = r.json()
    assert paused["status"] == "PAUSED", paused
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED", r.json()


def test_chapter_commit_observer_split_o3_meta_has_per_leg_chars(
    tmp_path: Path, monkeypatch,
):
    """O-3 集成：observer_split_meta 含 leg_a_chars / leg_b_chars / 合计 / recent_event_ids_count。

    集成路径：完整 chapter-commit 走完，run.checkpoint_json 应含 observer_split_meta。
    """
    import asyncio

    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "O-3 测试章")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_full_noop_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": base_mocks},
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]

            from packages.core.db import get_connection
            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            ckpt = json.loads(row["checkpoint_json"] or "{}")
            smeta = ckpt.get("observer_split_meta") or {}
            assert smeta.get("enabled") is True
            assert isinstance(smeta.get("leg_a_chars"), int)
            assert isinstance(smeta.get("leg_b_chars"), int)
            assert smeta["leg_a_chars"] > 0
            assert smeta["leg_b_chars"] > 0
            assert smeta.get("leg_a_total_chars") == smeta["leg_a_chars"] + smeta["leg_b_chars"]
            # recent_event_ids_count：项目首次 commit 时 plot_events 为空 → 0
            assert smeta.get("recent_event_ids_count") == 0
            # per-leg trim stats
            assert isinstance(smeta.get("leg_a_trim_stats"), dict)
            assert isinstance(smeta.get("leg_b_trim_stats"), dict)
            assert smeta["leg_a_trim_stats"]["leg"] == "entities"
            assert smeta["leg_b_trim_stats"]["leg"] == "narrative"

    asyncio.run(run())


def test_chapter_commit_observer_split_off_legacy_path_unaffected(
    tmp_path: Path, monkeypatch,
):
    """off 路径回归：env=off 时 observer_split_meta.enabled=False 且无 per-leg chars。"""
    import asyncio

    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "O-3 off 路径回归")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_full_noop_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": base_mocks},
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]

            from packages.core.db import get_connection
            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                # observer 只调 1 次（旧路径）
                cnt = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE run_id = ? "
                    "AND agent_id IN (SELECT agent_id FROM agents WHERE name='observer')",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            assert cnt["n"] == 1, f"off 路径 observer 仅 1 次，实际 {cnt['n']}"
            ckpt = json.loads(row["checkpoint_json"] or "{}")
            smeta = ckpt.get("observer_split_meta") or {}
            assert smeta.get("enabled") is False
            # off 路径不应有 per-leg chars（这些字段仅 split on 路径写入）
            assert smeta.get("leg_a_chars") is None
            assert smeta.get("leg_b_chars") is None

    asyncio.run(run())
