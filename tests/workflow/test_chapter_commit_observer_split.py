"""chapter_commit observer 双 leg 拆分（V3.1.1 O-2）测试。

覆盖点（packages/workflows/chapter_commit/pipeline.py 的 observer 双 leg 改造）：
1. 默认（split on）下：observer 被调 2 次（leg_a + leg_b），merge 后产出完整 7 数组。
2. split off（``NOVELOS_OBSERVER_SPLIT=off``）：走旧单次路径——observer 只调 1 次。
3. per-leg retry：errors 涉及 ``character_changes`` → 只重跑 leg_a，leg_b 不再调一次。
4. mock 过滤：golden regression 的 7 数组 mock 按 leg 过滤后，两腿各自得到
   「只含本 leg 范围」的 mock——merge 后等价于单次大调用。
5. 合并逻辑单测：``_merge_observer_legs`` + ``_classify_validator_errors_to_legs``
   + ``_filter_mock_for_leg`` 三个纯函数。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.workflows.chapter_commit.pipeline import (
    _OBSERVER_LEG_A_ARRAYS,
    _OBSERVER_LEG_B_ARRAYS,
    _classify_validator_errors_to_legs,
    _filter_mock_for_leg,
    _merge_observer_legs,
    _observer_split_enabled,
    _pick_retry_mock,
)


# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload
async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    """轮询直到 run.status ∈ expected；返回最终 run dict。

    SQLite 跨连接视角 + 后台线程落库时延：单节点 mock 流程通常 < 1s 跑完，
    但 polling 必须等到节点行 FAILED/COMPLETED 也写入——轮询间隔 0.2s 足以。
    """
    import asyncio
    import time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        last_run = run
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")
# ---------------------------------------------------------------------------
# 纯函数单测（不依赖 DB / httpx）
# ---------------------------------------------------------------------------


def test_observer_split_enabled_default_on():
    """默认环境（无 NOVELOS_OBSERVER_SPLIT）下 split 开启。"""
    import os as _os

    saved = _os.environ.pop("NOVELOS_OBSERVER_SPLIT", None)
    try:
        assert _observer_split_enabled() is True
    finally:
        if saved is not None:
            _os.environ["NOVELOS_OBSERVER_SPLIT"] = saved


def test_observer_split_enabled_off_env(monkeypatch):
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    assert _observer_split_enabled() is False
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "0")
    assert _observer_split_enabled() is False
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "false")
    assert _observer_split_enabled() is False


def test_observer_split_enabled_explicit(monkeypatch):
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    assert _observer_split_enabled() is True


def test_merge_observer_legs_basic():
    """leg_a 与 leg_b 各自输出非空数组时，merge 后 7 数组齐全且 leg_a 数组来自 leg_a。"""
    leg_a = {
        "character_changes": [{"change_id": "cc_001"}],
        "relationship_changes": [],
        "world_changes": [{"change_id": "wc_001"}],
        "new_events": [],
        "new_hooks": [],
        "resolved_hooks": [],
        "debt_changes": [],
    }
    leg_b = {
        "character_changes": [],
        "relationship_changes": [],
        "world_changes": [],
        "new_events": [{"change_id": "ev_001"}],
        "new_hooks": [{"change_id": "nh_001"}],
        "resolved_hooks": [{"change_id": "rh_001"}],
        "debt_changes": [{"change_id": "dc_001"}],
    }
    merged = _merge_observer_legs(leg_a, leg_b)
    assert len(merged["character_changes"]) == 1
    assert merged["character_changes"][0]["change_id"] == "cc_001"
    assert len(merged["world_changes"]) == 1
    assert len(merged["new_events"]) == 1
    assert len(merged["new_hooks"]) == 1
    assert len(merged["resolved_hooks"]) == 1
    assert len(merged["debt_changes"]) == 1
    assert len(merged["relationship_changes"]) == 0
    # 7 数组齐全
    for arr in _OBSERVER_LEG_A_ARRAYS + _OBSERVER_LEG_B_ARRAYS:
        assert arr in merged
        assert isinstance(merged[arr], list)


def test_merge_observer_legs_partial_outputs():
    """两条腿部分输出（如某条腿返回 None 或某数组缺失）→ 缺失数组视为空列表。"""
    leg_a = {
        "character_changes": [{"change_id": "cc_001"}],
        # world_changes 缺失
        # relationship_changes 缺失
    }
    leg_b = None  # 防御：leg_b 异常时不应崩溃
    merged = _merge_observer_legs(leg_a, leg_b)
    assert merged["character_changes"][0]["change_id"] == "cc_001"
    assert merged["world_changes"] == []
    assert merged["relationship_changes"] == []
    assert merged["new_events"] == []


def test_merge_observer_legs_leg_a_priority():
    """leg_a 数组被 leg_b 误输出时（理论上不应发生），以 leg_a 为准。"""
    leg_a = {"character_changes": [{"change_id": "cc_001", "from": "leg_a"}]}
    leg_b = {"character_changes": [{"change_id": "cc_001", "from": "leg_b"}]}
    merged = _merge_observer_legs(leg_a, leg_b)
    # leg_a 优先：character_changes 应来自 leg_a
    assert merged["character_changes"][0]["from"] == "leg_a"


def test_classify_validator_errors_to_legs_character():
    """errors 含 character_changes → 只重跑 leg_a。"""
    errors = ["character_changes[0].before is required"]
    a, b = _classify_validator_errors_to_legs(errors)
    assert a is True
    assert b is False


def test_classify_validator_errors_to_legs_narrative():
    """errors 含 new_events → 只重跑 leg_b。"""
    errors = ["new_events[0].time.timeline_day is required"]
    a, b = _classify_validator_errors_to_legs(errors)
    assert a is False
    assert b is True


def test_classify_validator_errors_to_legs_both():
    """errors 涉及双腿数组 → 双腿都重试。"""
    errors = [
        "character_changes[0].before is required",
        "new_hooks[0].importance out of range",
    ]
    a, b = _classify_validator_errors_to_legs(errors)
    assert a is True
    assert b is True


def test_classify_validator_errors_to_legs_unclassified():
    """errors 不含数组名（兜底）→ 双腿都重试。"""
    errors = ["some generic validation error"]
    a, b = _classify_validator_errors_to_legs(errors)
    assert a is True
    assert b is True


def test_classify_validator_errors_to_legs_mixed():
    """errors 含 world_changes + resolved_hooks → 跨腿；双腿都重试。"""
    errors = [
        "world_changes[0].world_kind invalid",
        "resolved_hooks[0].to_status missing",
    ]
    a, b = _classify_validator_errors_to_legs(errors)
    assert a is True
    assert b is True


def test_filter_mock_for_leg_entities():
    """leg_a (entities) mock 过滤：保留 character/relationship/world 数组，其它空。"""
    raw = json.dumps(
        {
            "character_changes": [{"change_id": "cc_001"}],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [{"change_id": "ev_001"}],
            "new_hooks": [{"change_id": "nh_001"}],
            "resolved_hooks": [],
            "debt_changes": [{"change_id": "dc_001"}],
        },
        ensure_ascii=False,
    )
    filtered = _filter_mock_for_leg(raw, "entities")
    assert isinstance(filtered, str)
    data = json.loads(filtered)
    assert data["character_changes"] == [{"change_id": "cc_001"}]
    assert data["world_changes"] == []
    assert data["relationship_changes"] == []
    # 其它数组清空
    assert data["new_events"] == []
    assert data["new_hooks"] == []
    assert data["resolved_hooks"] == []
    assert data["debt_changes"] == []


def test_filter_mock_for_leg_narrative():
    """leg_b (narrative) mock 过滤：保留 new_events/new_hooks/resolved_hooks/debt_changes。"""
    raw = json.dumps(
        {
            "character_changes": [{"change_id": "cc_001"}],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [{"change_id": "ev_001"}],
            "new_hooks": [{"change_id": "nh_001"}],
            "resolved_hooks": [],
            "debt_changes": [{"change_id": "dc_001"}],
        },
        ensure_ascii=False,
    )
    filtered = _filter_mock_for_leg(raw, "narrative")
    data = json.loads(filtered)
    assert data["new_events"] == [{"change_id": "ev_001"}]
    assert data["new_hooks"] == [{"change_id": "nh_001"}]
    assert data["resolved_hooks"] == []
    assert data["debt_changes"] == [{"change_id": "dc_001"}]
    # 实体数组清空
    assert data["character_changes"] == []
    assert data["world_changes"] == []
    assert data["relationship_changes"] == []


def test_filter_mock_for_leg_list_mode():
    """list[str] 模式：每条元素都被过滤。"""
    raw_list = [
        json.dumps(
            {
                "character_changes": [{"change_id": "cc_001"}],
                "new_events": [{"change_id": "ev_001"}],
            },
            ensure_ascii=False,
        )
    ]
    filtered = _filter_mock_for_leg(raw_list, "entities")
    assert isinstance(filtered, list)
    assert len(filtered) == 1
    data = json.loads(filtered[0])
    assert data["character_changes"] == [{"change_id": "cc_001"}]
    assert data["new_events"] == []


def test_filter_mock_for_leg_none_passthrough():
    """None 输入透传。"""
    assert _filter_mock_for_leg(None, "entities") is None


def test_filter_mock_for_leg_non_json_passthrough():
    """非 JSON 字符串透传（runner 内部 extract_json 会再尝试）。"""
    bad = "not json at all"
    out = _filter_mock_for_leg(bad, "entities")
    assert out == bad


def test_pick_retry_mock_list_mode():
    """list 模式 + 多条：弹下一条并按 leg 过滤。"""
    full_mock = [
        json.dumps({"character_changes": [{"change_id": "cc_v1"}]}),
        json.dumps({"character_changes": [{"change_id": "cc_v2"}]}),
    ]
    picked = _pick_retry_mock(full_mock, "entities")
    assert isinstance(picked, list)
    data = json.loads(picked[0])
    assert data["character_changes"][0]["change_id"] == "cc_v2"


def test_pick_retry_mock_single_passthrough():
    """list 长度 ≤1：保持原样（单条 mock 不重试靠 _retry_hint 修正）。"""
    full_mock = [json.dumps({"character_changes": [{"change_id": "cc_v1"}]})]
    picked = _pick_retry_mock(full_mock, "entities")
    assert picked is full_mock


def test_pick_retry_mock_string_passthrough():
    """str 模式：透传（生产 / 单条 mock 走同一响应，retry 仅靠 _retry_hint）。"""
    full_mock = json.dumps({"character_changes": [{"change_id": "cc_v1"}]})
    assert _pick_retry_mock(full_mock, "entities") is full_mock


# ---------------------------------------------------------------------------
# 集成测试（依赖 DB + httpx）
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
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


async def _make_project(app, name: str = "SplitProject") -> str:
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
                "chapter_goal": "苏婉清在夜谈中第一次怀疑林渊隐瞒父亲死因",
                "core_conflict": "苏婉清的求真意志 vs 林渊的善意隐瞒",
                "turning_point": "林渊回避黑玉佩细节，苏婉清察觉",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "夜访场景设置",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "建立信任基础",
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
                "notes_for_planner": "建议场景数 1",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = (
        "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
        "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
        "林渊立在博古架前，背对着她，似乎在翻检什么。"
    )
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
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    paused = r.json()
    paused = await _wait_run_terminal(app, paused["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200, r.text
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))


def test_chapter_commit_observer_split_on_two_calls(tmp_path: Path, monkeypatch):
    """默认 split on：observer 被调 2 次（leg_a + leg_b），merge 后成功 commit。

    断言：
    - run.status == COMPLETED
    - chapters.status == COMMITTED
    - ai_call_logs 中 observer 被调 2 次（首次 leg_a + 首次 leg_b）
    - state_deltas 无 rejected 行
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

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
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            commit_resp = r.json()
            commit_resp = await _wait_run_terminal(app, commit_resp["run_id"], expected=("COMPLETED",))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED"

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                # observer 被调 2 次（leg_a + leg_b 首次；若无重试）
                rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE agent_id IN "
                    "(SELECT agent_id FROM agents WHERE name='observer')",
                ).fetchone()
                assert rows["n"] == 2, (
                    f"split on 路径下 observer 应被调 2 次（leg_a + leg_b），实际 {rows['n']}"
                )
                # 无 rejected 行
                rejected = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND status = 'rejected'",
                    (cid,),
                ).fetchone()
                assert rejected["n"] == 0, rejected["n"]
                # observer delta 落库 1 行（合并后的）
                observer_delta_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND created_by = 'observer:v1'",
                    (cid,),
                ).fetchone()
                assert observer_delta_rows["n"] == 1, observer_delta_rows["n"]
            finally:
                conn.close()

    asyncio.run(run())


def test_chapter_commit_observer_split_off_one_call(tmp_path: Path, monkeypatch):
    """split off：observer 只被调 1 次（旧单次大调用路径）。

    校验显式 env 开关：env=off 时 _observer_node 走单次路径。
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

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
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            commit_resp = r.json()
            commit_resp = await _wait_run_terminal(app, commit_resp["run_id"], expected=("COMPLETED",))

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                # split off：observer 仅 1 次
                rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE agent_id IN "
                    "(SELECT agent_id FROM agents WHERE name='observer')",
                ).fetchone()
                assert rows["n"] == 1, (
                    f"split off 路径下 observer 仅 1 次，实际 {rows['n']}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_chapter_commit_observer_split_per_leg_retry(tmp_path: Path, monkeypatch):
    """errors 涉及 character_changes → 只重跑 leg_a，leg_b 不再调一次。

    mock 序列：[leg_a_bad, leg_a_good, leg_b_good]：
    - leg_a 首次返回非法（character_changes[0].before=None）→ 触发 per-leg retry
    - leg_a 重试返回合法（_retry_hint 修正）→ merge 通过
    - leg_b 仅调一次（首次即合法）→ 共 3 次 observer 调用
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    app = _create_app(tmp_path)

    invalid_leg_a = json.dumps(
        {
            "character_changes": [
                {
                    "change_id": "chg_invalid_001",
                    "op": "update",
                    "target_id": "char_001",
                    "character_id": "char_001",
                    "facet": "state",
                    "field": "state.location",
                    "before": None,
                    "after": "京城",
                    "confidence": 0.9,
                    "evidence": {
                        "chapter_id": "ch_split",
                        "excerpt": "林渊抵达京城",
                    },
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
        ensure_ascii=False,
    )
    valid_noop = _observer_full_noop_script()[0]

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            # observer mock 序列：让 MockProvider list 模式按调用顺序返回
            # 但 pipeline 的 _filter_mock_for_leg 会按 leg 过滤；为了让 leg_a 首次
            # 看到 invalid、retry 时看到 valid、leg_b 看到 valid，直接给出三个完整
            # mock（filter 后只有 leg_a 范围的数组生效）。
            observer_script = [invalid_leg_a, valid_noop, valid_noop]
            mock_providers = dict(base_mocks)
            mock_providers["observer"] = observer_script

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            commit_resp = r.json()
            commit_resp = await _wait_run_terminal(app, commit_resp["run_id"], expected=("COMPLETED",))

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                # observer 共 3 次：leg_a 首次 bad + leg_a retry good + leg_b good
                rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE agent_id IN "
                    "(SELECT agent_id FROM agents WHERE name='observer')",
                ).fetchone()
                assert rows["n"] == 3, (
                    f"per-leg retry 后 observer 应被调 3 次（leg_a bad + leg_a retry + leg_b），实际 {rows['n']}"
                )
                # 无 rejected 行（重试循环内未调 submit_delta）
                rejected = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND status = 'rejected'",
                    (cid,),
                ).fetchone()
                assert rejected["n"] == 0, rejected["n"]
                # observer delta 落库 1 行（重试后合并成功）
                observer_delta_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND created_by = 'observer:v1'",
                    (cid,),
                ).fetchone()
                assert observer_delta_rows["n"] == 1, observer_delta_rows["n"]
            finally:
                conn.close()

    asyncio.run(run())


def test_chapter_commit_observer_split_meta_present(tmp_path: Path, monkeypatch):
    """observability：observer_split_meta 记录双腿各自的 tokens/latency_ms。

    校验：checkpoint_json 含 observer_split_meta.enabled=True 且 leg_a/leg_b
    至少有 1 条记录。
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

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
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            run_id = r.json()["run_id"]

            # 直接读 ai_call_logs 校验：observer 至少 2 行（leg_a + leg_b）
            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT a.call_id, a.latency_ms, a.token_usage_json, a.created_at
                    FROM ai_call_logs a
                    JOIN agents ag ON ag.agent_id = a.agent_id
                    WHERE a.run_id = ? AND ag.name = 'observer'
                    ORDER BY a.rowid ASC
                    """,
                    (run_id,),
                ).fetchall()
                assert len(rows) == 2, (
                    f"split on 路径下 observer 应有 2 条 ai_call_logs 行，实际 {len(rows)}"
                )
                # 每条都应有 latency_ms 字段
                for row in rows:
                    assert row["latency_ms"] is not None, row
            finally:
                conn.close()

    asyncio.run(run())
