"""chapter_commit observer 校验重试测试（Sprint 任务）。

覆盖点（packages/workflows/chapter_commit/pipeline.py 的 observer 校验重试改造）：
1. 真实 LLM（MiniMax-M3）曾出现 ``character_changes[0].op='update' 但 before 为 None``
   这类业务校验失败。_inject_validate_node 改造后先调纯函数 validate_delta，
   失败带错误提示重试 observer 1 次；通过后再调 submit_delta。
2. mock observer 第一次非法、第二次合法 → commit COMPLETED、observer 被调 2 次、
   state_deltas 无本章 rejected 行（重试循环内不调 submit_delta）。
3. mock 两次都非法 → run FAILED 且 error 含 "observer delta rejected by validator"。
4. 单次合法回归：原有测试已覆盖，此处复用同一链路确认。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection

# ---------------------------------------------------------------------------
# Fixtures（与 test_chapter_pipelines.py 对齐）
# ---------------------------------------------------------------------------


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "测试项目") -> str:
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
    """同步 prompts（注册 observer 等 ACTIVE 行）"""
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Mock observer payloads
# ---------------------------------------------------------------------------


def _observer_invalid_update_before_null_script(chapter_id: str) -> list[str]:
    """非法 observer 输出：character_changes[0].op='update' 但 before=None。

    该字段在 validate_delta 业务校验中必非 None（state-delta-v0 §2.5）。
    MockProvider 每次 run_agent 新实例化、list 模式取 [0]，故第 1 次返回本条。
    """
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "chg_invalid_001",
                        "op": "update",
                        "target_id": "char_001",
                        "character_id": "char_001",
                        "facet": "state",
                        "field": "state.location",
                        "before": None,  # 业务校验失败：update 时 before 必非 None
                        "after": "京城",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
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
    ]


def _observer_valid_noop_script() -> list[str]:
    """合法 observer 输出：7 个空数组，无 risk_level=HIGH。"""
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


# ---------------------------------------------------------------------------
# Plan/Write/Review mock（用于推到 REVIEWED 后才能 commit）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 辅助：把 chapter 推到 REVIEWED（committable 状态）
# ---------------------------------------------------------------------------


async def _push_chapter_to_reviewed(
    app, pid: str, cid: str, mock_providers: dict
) -> None:
    """plan + write + review(approve) → REVIEWED。"""
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chapter_commit_observer_retry_succeeds_on_second_attempt(tmp_path: Path, monkeypatch):
    """observer 第 1 次非法（before=None）→ 第 2 次合法 → commit COMPLETED。

    V3.1.1 O-2：默认 split on（双 leg）。本测试把 observer_split 强制 off，模拟旧
    单次大调用路径，保证既有 retry 语义回归。split on 路径下的 retry 行为见
    ``test_chapter_commit_observer_split_retry``。

    断言：
    - run.status == COMPLETED
    - chapters.status == COMMITTED
    - ai_call_logs 中 observer 被调 2 次（call_count 列）
    - state_deltas 中无本章 rejected 行（重试循环内未调 submit_delta）
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
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            # commit 时 observer 用 [bad, good] 序列：第 1 次返回非法，第 2 次合法
            observer_script = (
                _observer_invalid_update_before_null_script(cid)
                + _observer_valid_noop_script()
            )
            mock_providers = dict(base_mocks)
            mock_providers["observer"] = observer_script

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            commit_resp = r.json()
            assert commit_resp["status"] == "COMPLETED", commit_resp

            # chapters.status=COMMITTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED"

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                # 1) observer 被调 2 次（mock_script 切到 list[1] 让 retry 拿到合法响应）
                rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE agent_id IN "
                    "(SELECT agent_id FROM agents WHERE name='observer')",
                ).fetchone()
                assert rows["n"] == 2, (
                    f"observer 应被调 2 次（第 1 次失败 + 重试成功），实际 {rows['n']}"
                )

                # 2) state_deltas 中无本章 rejected 行：
                #    重试循环内未调 submit_delta，最终只调一次（在合法 delta 上）
                rejected = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND status = 'rejected'",
                    (cid,),
                ).fetchone()
                assert rejected["n"] == 0, (
                    f"重试路径不应产生 rejected 行，实际 {rejected['n']}"
                )

                # 3) observer delta 仅落库 1 行（commit_delta 后 status='applied'）；
                #    init_genesis 也写一条 chapter_id=cid / created_by='system:genesis'，
                #    故按 created_by 区分。仅 observer 那条应存在。
                observer_delta_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND created_by = 'observer:v1'",
                    (cid,),
                ).fetchone()
                assert observer_delta_rows["n"] == 1, (
                    f"observer delta 应只落库 1 行（合法 delta），"
                    f"实际 {observer_delta_rows['n']}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_chapter_commit_observer_retry_fails_after_two_invalid_attempts(tmp_path: Path, monkeypatch):
    """observer 第 1 次、第 2 次都非法 → run FAILED 且 error 含 "observer delta rejected by validator"。

    V3.1.1 O-2：本测试把 observer_split 强制 off，模拟旧单次路径；split on 路径下的
    失败语义由 ``test_chapter_commit_observer_split_fails_after_retry`` 覆盖。

    断言：
    - run.status == FAILED
    - GET /runs/{id}.error 含 "observer delta rejected by validator"
    - chapters.status 保持 REVIEWED（commit 未推进）
    - state_deltas 中无 rejected 行（重试循环内未调 submit_delta）
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "第一章")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            # observer 两次都返回非法载荷
            observer_script = (
                _observer_invalid_update_before_null_script(cid)
                + _observer_invalid_update_before_null_script(cid)
            )
            mock_providers = dict(base_mocks)
            mock_providers["observer"] = observer_script

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            commit_resp = r.json()
            assert commit_resp["status"] == "FAILED", commit_resp
            run_id = commit_resp["run_id"]

            # 经 GET /runs/{id} 拿到 error 字段
            r = await _request(app, "GET", f"/api/runs/{run_id}")
            assert r.status_code == 200, r.text
            err = r.json().get("error") or ""
            assert "observer delta rejected by validator" in err, (
                f"error 应包含 'observer delta rejected by validator'，实际：{err!r}"
            )

            # chapters.status 保持 REVIEWED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "REVIEWED", r.json()["status"]

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                # observer 被调 2 次
                rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE agent_id IN "
                    "(SELECT agent_id FROM agents WHERE name='observer')",
                ).fetchone()
                assert rows["n"] == 2, rows["n"]

                # state_deltas 中无 rejected 行（重试循环内未调 submit_delta；
                # 唯一一次失败是 raise ValueError，未落库）
                rejected = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND status = 'rejected'",
                    (cid,),
                ).fetchone()
                assert rejected["n"] == 0, rejected["n"]

                # 也不应有 observer 的 validated/applied 行（合法 delta 一次都没出现）
                # init_genesis 的 applied 行（created_by='system:genesis'）存在但不算 observer
                observer_delta_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM state_deltas "
                    "WHERE chapter_id = ? AND created_by = 'observer:v1'",
                    (cid,),
                ).fetchone()
                assert observer_delta_rows["n"] == 0, (
                    f"两次失败时不应有 observer delta 落库，实际 {observer_delta_rows['n']}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_build_observer_ctx_node_uses_trimmed_snapshot(tmp_path: Path):
    """端到端断言（任务书 DoD #3）：_build_observer_ctx_node 调用后 observer_input
    必须带 trimmed 标记——``previous_state.snapshot_mode == 'trimmed'`` +
    ``snapshot_trim_stats`` 顶层存在。验证 chapter_commit pipeline 已真正接入 M3 引擎包。

    直接调节点函数、避开 HTTP 链路以最小化依赖；底层仍走真 DB + 真
    build_observer_input（trimmed 模式）。
    """
    from packages.workflows.chapter_commit.pipeline import _build_observer_ctx_node

    app = _create_app(tmp_path)

    async def setup():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "第一章")
            return pid, cid

    pid, cid = asyncio.run(setup())
    db_path = app.state.settings.db_path

    out = _build_observer_ctx_node({"db_path": db_path, "chapter_id": cid})

    assert "observer_input" in out
    payload = out["observer_input"]
    # trimmed 模式必须注入 snapshot_trim_stats
    assert "snapshot_trim_stats" in payload, (
        f"trimmed 模式应在顶层带 snapshot_trim_stats；当前顶层键={list(payload.keys())}"
    )
    # trimmed 模式必须把 previous_state.snapshot_mode 标记为 trimmed
    prev_state = payload.get("previous_state") or {}
    assert prev_state.get("snapshot_mode") == "trimmed", (
        f"previous_state.snapshot_mode 应为 'trimmed'，实际 {prev_state.get('snapshot_mode')!r}"
    )
