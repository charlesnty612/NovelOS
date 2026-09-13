"""V3.9 批次 4.1：quality_gate 阻断 → 改稿出路闭环（gate-revise）。

覆盖：
1. enforce 阻断：plan_json.revision_note 被写入（含 rule_id + 可执行建议）、
   plan_json.gate_blocked 标记（含 mode / rule_ids）；
2. 「按门禁建议改稿」触发路径：POST /gate-revise → write（revise 模式，产出 v2）→
   自动接力 chapter-review（PAUSED 等作者决议）→ 批准后 REVIEWED → 再次提交
   （report 模式）成功，且门禁通过时清除 gate_blocked 标记；
3. report 模式（不阻断）：不写 revision_note / gate_blocked；
4. 无 gate_blocked 标记时 POST /gate-revise → 409（不启动任何 run）。

测试模式与 ``tests/api/test_quality.py`` 一致（httpx.ASGITransport + tmp_path +
mock_providers），helper 为独立副本，避免跨测试文件耦合。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _get_run(app, run_id: str) -> dict:
    r = await _request(app, "GET", f"/api/runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await _get_run(app, run_id)
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s (last={last['status']!r})"
    )


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str = "gate-revise 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "gate 章节") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _make_dead_character(app, pid: str) -> str:
    """建角色并把 character_states 置为 dead，供 observer 触发 RULE_CHAR_DEAD_ACTIVE。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "死角色", "role": "supporting"},
    )
    assert r.status_code == 201, r.text
    char_id = r.json()["character_id"]
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "UPDATE character_states SET state_json = ? WHERE character_id = ?",
            ('{"status":"dead"}', char_id),
        )
        conn.commit()
    finally:
        conn.close()
    return char_id


# ---------------------------------------------------------------------------
# 脚本（与 test_quality.py 同口径）
# ---------------------------------------------------------------------------


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": chapter_id,
            "chapter_goal": "演练 gate-revise 闭环",
            "core_conflict": "无",
            "turning_point": "无",
            "expected_role": "setup",
            "key_beats": [{"beat_id": "beat_001", "purpose": "测试", "involved_characters": [],
                           "involved_locations": [], "involved_hooks": [], "involved_debts": [],
                           "risk_level": "LOW", "narrative_question_served": "测试"}],
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "",
        }, ensure_ascii=False)
    ]


def _writer_script(chapter_id: str, prose_suffix: str = "") -> list[str]:
    prose = (
        "无边的林海托着第一缕晨曦，远处的溪声把夜雾推向山脚，露珠沿着叶柄滑落。" + prose_suffix
    )
    return [
        json.dumps({
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": chapter_id,
            "prose": prose,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(prose),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }, ensure_ascii=False)
    ]


def _critic_script(chapter_id: str) -> list[str]:
    """无 issue 的合规 critic 报告（接力审校不必真调 LLM）。"""
    return [
        json.dumps({
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": chapter_id,
            "overall_comment": "无阻断性意见",
            "strengths": [],
            "issues": [],
        }, ensure_ascii=False)
    ]


def _observer_dead_character_script(chapter_id: str, dead_char_id: str) -> list[str]:
    return [
        json.dumps({
            "character_changes": [
                {
                    "change_id": "cc_dead_1",
                    "op": "update",
                    "target_id": dead_char_id,
                    "character_id": dead_char_id,
                    "facet": "state",
                    "field": "state.location",
                    "before": {},
                    "after": {"location": "京城"},
                    "confidence": 0.9,
                    "evidence": {"chapter_id": chapter_id, "scene_id": "scene_001",
                                 "excerpt": "在京城出现", "span": {"start": 0, "end": 5}},
                    "risk_level": "LOW",
                    "notes": "尝试 set dead character activity",
                    "visibility": "VISIBLE",
                    "who_knows": None,
                    "reason": None,
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }, ensure_ascii=False)
    ]


async def _drive_plan_write_review(app, pid: str, cid: str, mock_providers: dict) -> str:
    """跑 plan → write → review(approve)；返回 review run_id。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "gate-revise", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    review = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{review['run_id']}/resume",
        json={"human_input": {"approved": True}, "auto_revise_max": 0},
    )
    assert r.status_code == 200, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    return review["run_id"]


async def _commit(app, pid: str, cid: str, mock_providers: dict, mode: str) -> dict:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
        json={"mock_providers": mock_providers, "quality_gate_mode": mode},
    )
    assert r.status_code == 201, r.text
    return await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))


async def _find_chained_review_run(app, pid: str, *, exclude_run_id: str) -> dict:
    """等 gate-revise 接力启动的 review run（PAUSED，且非 exclude_run_id）。"""
    import time

    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        r = await _request(app, "GET", f"/api/projects/{pid}/runs")
        assert r.status_code == 200, r.text
        candidates = [
            row for row in r.json()
            if row.get("workflow_name") == "chapter-review"
            and row["status"] == "PAUSED"
            and row["run_id"] != exclude_run_id
        ]
        if candidates:
            candidates.sort(key=lambda x: x.get("started_at") or "", reverse=True)
            return candidates[0]
        await asyncio.sleep(0.3)
    raise AssertionError("gate-revise 未在 120s 内接力出新的 PAUSED review run")


# ---------------------------------------------------------------------------
# 1. enforce 阻断落点 + gate-revise 闭环
# ---------------------------------------------------------------------------


def test_gate_enforce_block_writes_note_and_gate_revise_closes_loop(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            base_mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            first_review_run_id = await _drive_plan_write_review(app, pid, cid, base_mock)

            # --- enforce 阻断：FAILED + chapter 保持 REVIEWED
            blocked = await _commit(app, pid, cid, base_mock, "enforce")
            assert blocked["status"] == "FAILED", blocked
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # --- 落点 1：plan_json.revision_note（含 rule_id + 可执行建议）
            plan = r.json()["plan_json"]
            note = plan.get("revision_note")
            assert isinstance(note, str) and note, plan
            assert "质量门禁阻断" in note, note
            assert "RULE_CHAR_DEAD_ACTIVE" in note, note
            # --- 落点 2：gate_blocked 标记（mode + rule_ids 摘要）
            marker = plan.get("gate_blocked")
            assert isinstance(marker, dict), plan
            assert marker.get("mode") == "enforce", marker
            assert "RULE_CHAR_DEAD_ACTIVE" in (marker.get("rule_ids") or []), marker

            # --- 触发路径：一键按门禁建议改稿 → write（revise）→ 接力 review
            revise_mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid, prose_suffix="【按门禁建议改稿】"),
                "critic": _critic_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/gate-revise",
                json={"mock_providers": revise_mock},
            )
            assert r.status_code == 201, r.text
            write_run_id = r.json()["run_id"]
            write_run = await _wait_run_terminal(app, write_run_id, expected=("COMPLETED",))
            assert write_run["status"] == "COMPLETED", write_run

            # write 走 revise 模式：产出 v2 草稿，章节回退 DRAFTED
            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert r.status_code == 200, r.text
            versions = sorted(d["version"] for d in r.json())
            assert versions == [1, 2], versions

            # 接力审校：PAUSED 等作者决议
            chained = await _find_chained_review_run(
                app, pid, exclude_run_id=first_review_run_id
            )
            r = await _request(
                app, "POST", f"/api/runs/{chained['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # --- 再次提交（report 模式避开同一 error）→ 成功且标记被清除
            done = await _commit(app, pid, cid, base_mock, "report")
            assert done["status"] == "COMPLETED", done
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan_after = r.json()["plan_json"]
            assert "gate_blocked" not in plan_after, plan_after

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. report 模式（不阻断）不写落点
# ---------------------------------------------------------------------------


def test_gate_report_mode_does_not_write_plan_fields(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            await _drive_plan_write_review(app, pid, cid, mock)
            done = await _commit(app, pid, cid, mock, "report")
            assert done["status"] == "COMPLETED", done

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan = r.json()["plan_json"]
            assert "revision_note" not in plan, plan
            assert "gate_blocked" not in plan, plan

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 无阻断标记 → 409
# ---------------------------------------------------------------------------


def test_gate_revise_409_without_pending_block(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/gate-revise",
                json={},
            )
            assert r.status_code == 409, r.text
            assert "no pending quality-gate block" in r.json()["detail"]

            # 章节不存在 → 404（chapter 校验先于阻断标记校验）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/ch_nope/gate-revise",
                json={},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())
