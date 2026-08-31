"""chapter-plan/write/review/commit 端到端集成测试（Sprint 4-A）。

用全 mock（mock_providers 给 director/writer/observer 脚本化合法 JSON）跑通 §110 链路核心段：
- project → character → chapter → init state
- plan → write → review (approve) → commit (无 HIGH)
- 断言 chapters.status=COMMITTED、state_version 递增、drafts/plan_json 落库
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection




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
    import asyncio, time
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
    """同步 prompts（注册 director / writer / observer ACTIVE 行）"""
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


def _director_script() -> list[str]:
    """合规 director-plan.v1 输出。"""
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
                    },
                    {
                        "beat_id": "beat_002",
                        "purpose": "黑玉佩引入对话",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "MEDIUM",
                        "narrative_question_served": "伏笔推进",
                    },
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "建议场景数 2",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    """合规 writer-output.v1 输出。"""
    prose = (
        "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
        "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
        "林渊立在博古架前，背对着她，似乎在翻检什么。松烟墨香被夜风裹着送进来，"
        "灯芯爆了一下花，啪地轻响。她开口问起父亲遗物。"
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


def _writer_revised_script() -> list[str]:
    """改稿后的 writer-output.v1 输出（用于 auto-revise 回路第二轮 write）。"""
    prose = (
        "戌时的更鼓从街尾传过来。苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
        "林渊闻言沉默良久，终是轻轻点头：\"令尊遗物，确有一件我尚未说清。\""
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


def _observer_noop_script() -> list[str]:
    """Observer 输出 7 个空数组（无 change）；通过 schema 校验（无 risk_level=HIGH）。"""
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


def test_chapter_pipelines_end_to_end(tmp_path: Path):
    """端到端：plan → write → review(approve) → commit(无 HIGH) → COMMITTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 1) chapter-plan
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            plan_run = r.json()
            plan_run = await _wait_run_terminal(app, plan_run["run_id"], expected=("COMPLETED",))
            assert plan_run["current_node"] is None

            # 断言 chapters.plan_json 已落库
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["plan_json"]["chapter_goal"], "plan_json 没落库"
            assert ch["status"] == "PLANNED"  # plan 不改 status

            # 2) chapter-write
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            write_run = r.json()
            write_run = await _wait_run_terminal(app, write_run["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"

            # 断言 drafts 行
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT * FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 1, drafts
            assert (drafts[0]["content"] or "") != ""

            # 3) chapter-review (approving)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            review_resp = r.json()
            review_resp = await _wait_run_terminal(app, review_resp["run_id"], expected=("PAUSED",))
            assert "pause_payload" in review_resp

            # resume with approved=true
            r = await _request(
                app, "POST", f"/api/runs/{review_resp['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            final = r.json()
            final = await _wait_run_terminal(app, final["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "REVIEWED"

            # 4) chapter-commit (observer 产出 7 空数组，无 HIGH)
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

            # 断言 chapters.status=COMMITTED + state_version 递增
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED"

            conn = get_connection(app.state.settings.db_path)
            try:
                snap_row = conn.execute(
                    "SELECT MAX(state_version) AS v FROM story_states WHERE project_id = ?",
                    (pid,),
                ).fetchone()
                assert snap_row["v"] == 2, snap_row["v"]  # init_genesis v1 + commit v2
                commits = conn.execute(
                    "SELECT * FROM commits WHERE chapter_id = ? ORDER BY resulting_state_version ASC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(commits) == 2, commits  # genesis + observer commit

    asyncio.run(run())


def test_chapter_review_rejected_results_in_failed(tmp_path: Path):
    """review 时 author 拒绝 → resume 后 run FAILED（因 mark_reviewed 抛错）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 先 plan + write
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

            # review
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

            # resume with approved=false → mark_reviewed 抛错 → run FAILED
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": False}},
            )
            assert r.status_code == 200, r.text
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("FAILED",))

            # chapters.status 保持 DRAFTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 纯驳回（无 revise）：run FAILED + error='rejected'（与 revise 分支的
            # 'rejected-for-revision' 区分），且不会触发 auto_revise 回路。
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected", r.json().get("error")

    asyncio.run(run())


def test_chapter_review_revise_loop_end_to_end(tmp_path: Path):
    """review 驳回并改稿闭环（PRD §59/§87 / deviation #1 关闭）：

    review PAUSED → resume {approved:false, revise:true, note} → run FAILED
    (error='rejected-for-revision')、chapter 保持 DRAFTED、note 落 plan_json.revision_note
    → 人工改稿（POST drafts）→ 重跑 write（新 draft 版本）→ 再 review → approve → REVIEWED。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write
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
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 2) review → PAUSED
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

            # 3) resume revise:true + note → run FAILED(rejected-for-revision)
            # P0：auto_revise 默认开启，为验证原手动改稿闭环显式关闭。
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "禁用词命中，请改写后重审"},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            final = r.json()
            final = await _wait_run_terminal(app, final["run_id"], expected=("FAILED",))
            # resume 响应不含 error 字段（router 只回 run_id/status/current_node），经 GET /runs/{id} 校验
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected-for-revision", r.json()["error"]

            # chapter 保持 DRAFTED；plan_json.revision_note 已落
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"
            assert ch["plan_json"]["revision_note"] == "禁用词命中，请改写后重审"

            # 4) 人工改稿（POST drafts）
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "修订后的草稿内容：林渊闻言沉默良久，终是轻轻点头。"},
            )
            assert r.status_code == 201, r.text

            # 5) 重跑 write（DRAFTED 允许）→ 新 draft 版本落库（v1=write、v2=人工改稿、v3=重跑 write）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            write_run = r.json()
            write_run = await _wait_run_terminal(app, write_run["run_id"], expected=("COMPLETED",))
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, created_by FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2, 3], drafts

            # 6) 再 review → approve → REVIEWED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused2 = r.json()
            paused2 = await _wait_run_terminal(app, paused2["run_id"], expected=("PAUSED",))
            r = await _request(
                app, "POST", f"/api/runs/{paused2['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_chapter_review_revise_without_note_clears_revision_note(tmp_path: Path):
    """revise:true 但无 note → plan_json.revision_note 键被移除（幂等清理）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            for path in ("plan", "write"):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                    json={"mock_providers": mock_providers},
                )
                assert r.status_code == 201, r.text
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))

            # 预置旧 revision_note，模拟上一轮 revise 残留
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan = dict(r.json()["plan_json"])
            plan["revision_note"] = "旧意见"
            r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"plan_json": plan})
            assert r.status_code == 200, r.text

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused_run = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            paused = {"run_id": paused_run["run_id"]}
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("FAILED",))
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected-for-revision"

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"
            assert "revision_note" not in r.json()["plan_json"]

    asyncio.run(run())


def test_chapter_commit_without_review_fails(tmp_path: Path):
    """commit 在 chapter 仍为 DRAFTED 时（未跑 review）应 FAILED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            # 跳过 plan/write/review，直接 commit
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": {"observer": _observer_noop_script()}},
            )
            assert r.status_code == 201, r.text
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("FAILED",))

    asyncio.run(run())


def test_chapter_write_on_committed_chapter_fails(tmp_path: Path):
    """对已 COMMITTED 的 chapter 再次启动 chapter-write 应 FAILED，drafts 行数不增。

    F1：chapter_write 仅允许 PLANNED/DRAFTED 启动；REVIEWED/COMMITTED 重跑被拒。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 完整跑到 COMMITTED
            for path in ("plan", "write", "review", "commit"):
                if path == "review":
                    r = await _request(
                        app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                        json={"mock_providers": mock_providers},
                    )
                    assert r.status_code == 201, r.text
                    paused = r.json()
                    paused = await _wait_run_terminal(app, paused["run_id"], expected=("PAUSED",))
                    r = await _request(
                        app, "POST", f"/api/runs/{paused['run_id']}/resume",
                        json={"human_input": {"approved": True}},
                    )
                    assert r.status_code == 200, r.text
                    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
                    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))
                else:
                    r = await _request(
                        app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                        json={"mock_providers": mock_providers},
                    )
                    assert r.status_code == 201, r.text
                    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
                    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED"

            # 记录 COMMITTED 时的 drafts 行数
            conn = get_connection(app.state.settings.db_path)
            try:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert before == 1

            # 再跑 chapter-write，期望 FAILED 且 drafts 行数不增
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            write_run = r.json()
            write_run = await _wait_run_terminal(app, write_run["run_id"], expected=("FAILED",))

            conn = get_connection(app.state.settings.db_path)
            try:
                after = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert after == before, f"drafts 行数应不变（before={before}, after={after}）"

    asyncio.run(run())


def test_list_runs_endpoint(tmp_path: Path):
    """GET /projects/{pid}/runs 列出该项目下的所有 workflow runs。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {"director": _director_script()}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(app, "GET", f"/api/projects/{pid}/runs")
            assert r.status_code == 200
            runs = r.json()
            assert len(runs) >= 1
            assert all(r["chapter_id"] == cid or r["chapter_id"] is None for r in runs)
            # 每行必须带 label（人类可读中文名）；chapter-plan run 的 label 应为 "生成计划"
            assert all("label" in r for r in runs)
            assert any(r["workflow_name"] == "chapter-plan" and r["label"] == "生成计划" for r in runs)

    asyncio.run(run())


def test_chapter_review_auto_revise_loop_once_then_approve(tmp_path: Path):
    """P0 自动改稿回路：revise 一次后 approve。

    review PAUSED → resume {revise:true, auto_revise_max:2} → 自动重跑 write→review
    → 新 review PAUSED → resume {approved:true} → COMPLETED + REVIEWED。
    验证 drafts 产生 v2（自动 write 追加新版本）。

    异步化后适配：resume 端点立即返回 RUNNING，run_id 仍是原 review run；
    auto_revise 在 daemon 线程里跑 → 实际写入的「第二次 review run」需要通过
    GET /projects/{pid}/runs list 端点按时间序找到（status=PAUSED 且 != first_review_run_id）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = r.json()
            paused = await _wait_run_terminal(app, paused["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume with revise + auto_revise_max=2：触发自动回路（daemon 线程执行）
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),  # 改稿后 prose
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "节奏太散，重写"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                },
            )
            assert r.status_code == 200, r.text
            # 异步化：响应仍指向原 review run；等它进 FAILED 即 daemon 链路启动完成。
            assert r.json()["run_id"] == first_review_run_id
            assert r.json()["status"] == "RUNNING"
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            # 找 daemon 回路产生的「第二次 review run」：list 端点过滤 chapter_review + PAUSED
            # 且 run_id != first_review_run_id 的最新一行。
            second_review_run_id: str | None = None
            import time as _list_t
            _list_deadline = _list_t.monotonic() + 120.0
            while _list_t.monotonic() < _list_deadline:
                rr = await _request(app, "GET", f"/api/projects/{pid}/runs")
                assert rr.status_code == 200, rr.text
                candidates = [
                    row for row in rr.json()
                    if row.get("workflow_name") == "chapter-review"
                    and row["status"] == "PAUSED"
                    and row["run_id"] != first_review_run_id
                ]
                if candidates:
                    # 取 started_at 最大的（最新一轮）
                    candidates.sort(key=lambda r0: r0.get("started_at") or "", reverse=True)
                    second_review_run_id = candidates[0]["run_id"]
                    break
                await asyncio.sleep(0.3)
            assert second_review_run_id is not None, (
                "daemon 回路未在 120s 内产出第二个 PAUSED review run"
            )
            assert second_review_run_id != first_review_run_id

            # 4) 校验 chapter 仍为 DRAFTED，但 drafts 已有 v2
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"
            assert ch["plan_json"]["revision_note"] == "节奏太散，重写"

            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, content FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 2, drafts
            assert "终是轻轻点头" in (drafts[1]["content"] or "")

            # 5) 批准新 review → COMPLETED + REVIEWED
            r = await _request(
                app, "POST", f"/api/runs/{second_review_run_id}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_chapter_review_auto_revise_disabled_keeps_failed(tmp_path: Path):
    """auto_revise_max=0（或默认 env=0）时，revise 后保持 FAILED，不自动重跑。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            for path in ("plan", "write"):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                    json={"mock_providers": mock_providers},
                )
                assert r.status_code == 201, r.text
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            paused = {"run_id": paused["run_id"]}

            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "不改"},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("FAILED",))
            assert r.json()["run_id"] == paused["run_id"]

            # drafts 只有 v1
            conn = get_connection(app.state.settings.db_path)
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert n == 1

    asyncio.run(run())


def test_chapter_review_resume_with_auto_revise_returns_within_2s(tmp_path: Path):
    """resume + auto_revise 触发时：POST ≤2s 即返回；轮询最终见到 daemon 回路新 write/review run。

    验证点：
    - POST /runs/{id}/resume 在 ≤2s 内拿到 200 响应（不再像旧实现那样阻塞到回路结束，
      auto_revise_max=2 最长可跑两轮 write+review，几分钟量级）。
    - 响应 status="RUNNING"，run_id 等于被 resume 的原 review run。
    - daemon 线程最终在 list 端点可观察到 chapter-review 的新 PAUSED run 与
      chapter-write 新 run（数量 ≥2 的 chapter-review run、≥1 的 chapter-write run
      是在 daemon 链路里新建的）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume revise + auto_revise_max=2：必须 ≤2s 返回
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            import time as _wall_t
            _t0 = _wall_t.monotonic()
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿触发"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                },
            )
            _t1 = _wall_t.monotonic()
            assert r.status_code == 200, r.text
            assert (_t1 - _t0) <= 2.0, (
                f"resume + auto_revise 响应耗时 {_t1 - _t0:.2f}s 超 2s 上限"
            )
            assert r.json()["status"] == "RUNNING"
            assert r.json()["run_id"] == first_review_run_id

            # 4) 轮询 list 端点，直到见到 daemon 回路产生的新 write + 新 review run
            import time as _list_t
            _list_deadline = _list_t.monotonic() + 120.0
            new_write_run_id: str | None = None
            new_review_run_id: str | None = None
            while _list_t.monotonic() < _list_deadline:
                rr = await _request(app, "GET", f"/api/projects/{pid}/runs")
                assert rr.status_code == 200, rr.text
                rows = rr.json()
                review_rows = [
                    row for row in rows
                    if row.get("workflow_name") == "chapter-review"
                    and row["run_id"] != first_review_run_id
                ]
                write_rows = [row for row in rows if row.get("workflow_name") == "chapter-write"]
                # 至少一个新 review PAUSED（回路最终态）+ 至少一个新 write（回路跑过）
                paused_new = [row for row in review_rows if row["status"] == "PAUSED"]
                if paused_new and write_rows:
                    new_review_run_id = max(
                        paused_new,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    new_write_run_id = max(
                        write_rows,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    break
                await asyncio.sleep(0.3)

            assert new_write_run_id is not None, "daemon 未在 120s 内产出新 chapter-write run"
            assert new_review_run_id is not None, "daemon 未在 120s 内产出新 PAUSED chapter-review run"
            assert new_write_run_id != new_review_run_id

            # 5) drafts 已含 v2（daemon 跑过 write）
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2], drafts

    asyncio.run(run())


def test_chapter_review_with_draft_version_selects_specific_version(tmp_path: Path):
    """指定 draft_version=1 审 v1；不指定时审最新稿（v2）。

    场景：plan → write(v1) → 人工 POST drafts(v2) → review 带 draft_version=1
    → review_report.draft_version==1 且 label 为「审校（审 v1）」。
    再单独验证不带 draft_version → 审最新版（v2）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            for path in ("plan", "write"):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                    json={"mock_providers": mock_providers},
                )
                assert r.status_code == 201, r.text
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            # 人工 POST drafts 产出 v2（特征 marker 便于断言）
            v2_marker = "【人工修订稿】苏婉清怒掷茶盏。"
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": v2_marker + "林渊默然。"},
            )
            assert r.status_code == 201, r.text

            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, content FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2], drafts

            # 1) review 带 draft_version=1 → 审 v1（review_report.draft_version==1, 不含 v2 marker）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers, "draft_version": 1},
            )
            assert r.status_code == 201, r.text
            paused1 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            run1_resp = await _request(app, "GET", f"/api/runs/{paused1['run_id']}")
            assert run1_resp.status_code == 200
            run1 = run1_resp.json()
            # review_report 落进 pause_payload（author_review payload）
            pp1 = run1.get("pause_payload") or {}
            rr1 = pp1.get("review_report") or {}
            assert rr1.get("draft_version") == 1, rr1
            # list 端点 label 应为「审校（审 v1）」
            list_resp = await _request(app, "GET", f"/api/projects/{pid}/runs")
            runs = list_resp.json()
            label1 = next(r["label"] for r in runs if r["run_id"] == paused1["run_id"])
            assert label1 == "审校（审 v1）", label1

            # 清理 reviewer（驳回 + 关 auto_revise），让能再次起 review
            r = await _request(
                app, "POST", f"/api/runs/{paused1['run_id']}/resume",
                json={"human_input": {"approved": False}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

            # 2) review 不带 draft_version → 审最新版 v2
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            run2_resp = await _request(app, "GET", f"/api/runs/{paused2['run_id']}")
            run2 = run2_resp.json()
            pp2 = run2.get("pause_payload") or {}
            rr2 = pp2.get("review_report") or {}
            assert rr2.get("draft_version") == 2, rr2
            list_resp = await _request(app, "GET", f"/api/projects/{pid}/runs")
            runs = list_resp.json()
            label2 = next(r["label"] for r in runs if r["run_id"] == paused2["run_id"])
            assert label2 == "审校（审 v2）", label2

    asyncio.run(run())


def test_chapter_review_with_nonexistent_draft_version_fails(tmp_path: Path):
    """指定 draft_version 不存在 → basic_checks 抛错 → run FAILED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            for path in ("plan", "write"):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                    json={"mock_providers": mock_providers},
                )
                assert r.status_code == 201, r.text
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers, "draft_version": 99},
            )
            assert r.status_code == 201, r.text
            failed = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            await _wait_run_terminal(app, failed["run_id"], expected=("FAILED",))
            assert "no draft version 99" in (failed.get("error") or ""), failed.get("error")

    asyncio.run(run())


def test_chapter_review_auto_revise_loop_propagates_model_overrides(tmp_path: Path):
    """P0 自动改稿回路：resume body 带 ``model_overrides`` 时，回路重跑的 write 与 review
    子 run 的 checkpoint_json 中必须原样携带同一份 overrides（首轮档案不丢失）。

    验证点：
    - 原 review run 的 checkpoint_json 含 ``model_overrides``（首轮即生效）；
    - resume body 显式给 overrides → 回路内 write / review 子 run 的 checkpoint_json
      也含**同一份** overrides（透传）；
    - 第二轮 review PAUSED（回路跑成功一整轮）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1（首轮不带 overrides，保持简单）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 2) 首轮 review → PAUSED（也不带 overrides）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume revise + model_overrides + auto_revise_max=2：触发回路
            overrides = {"creative_writing": "mprof_auto_revise_passthrough"}
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                    "model_overrides": overrides,
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "RUNNING"
            assert r.json()["run_id"] == first_review_run_id

            # 等首轮 review 走到 FAILED(rejected-for-revision) → 回路启动
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            # 4) 轮询找回路产生的「新 chapter-write run」+「新 chapter-review PAUSED run」
            import time as _list_t
            _list_deadline = _list_t.monotonic() + 120.0
            new_write_run_id: str | None = None
            new_review_run_id: str | None = None
            while _list_t.monotonic() < _list_deadline:
                rr = await _request(app, "GET", f"/api/projects/{pid}/runs")
                assert rr.status_code == 200, rr.text
                rows = rr.json()
                review_rows = [
                    row for row in rows
                    if row.get("workflow_name") == "chapter-review"
                    and row["run_id"] != first_review_run_id
                ]
                write_rows = [
                    row for row in rows
                    if row.get("workflow_name") == "chapter-write"
                ]
                paused_new = [row for row in review_rows if row["status"] == "PAUSED"]
                if paused_new and write_rows:
                    new_review_run_id = max(
                        paused_new,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    new_write_run_id = max(
                        write_rows,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    break
                await asyncio.sleep(0.3)

            assert new_write_run_id is not None, "daemon 未在 120s 内产出新 chapter-write run"
            assert new_review_run_id is not None, "daemon 未在 120s 内产出新 PAUSED chapter-review run"

            # 5) 断言：回路内 write / review 子 run 的 checkpoint_json 中都携带 overrides
            # （与首轮跑 test_write_model_overrides_passthrough_through_ctx 同口径：
            # 在任意 node checkpoint 或合并 ctx 中匹配到即可。）
            async def _ckpt_has_overrides(run_id: str) -> bool:
                rr = await _request(app, "GET", f"/api/runs/{run_id}")
                assert rr.status_code == 200, rr.text
                ckpt = rr.json().get("checkpoint_json") or {}
                for _node_id, node_ckpt in ckpt.items():
                    if not isinstance(node_ckpt, dict):
                        continue
                    ctx_blob = node_ckpt.get("ctx")
                    merged = (
                        {**{k: v for k, v in ckpt.items() if k != "ctx"}, **(ctx_blob if isinstance(ctx_blob, dict) else {})}
                        if isinstance(ckpt.get("ctx"), dict)
                        else {**ckpt, **(ctx_blob if isinstance(ctx_blob, dict) else {})}
                    )
                    if isinstance(merged.get("model_overrides"), dict) and merged["model_overrides"] == overrides:
                        return True
                    if isinstance(node_ckpt.get("model_overrides"), dict) and node_ckpt["model_overrides"] == overrides:
                        return True
                return False

            assert await _ckpt_has_overrides(new_write_run_id), (
                f"回路内 chapter-write 子 run（{new_write_run_id}）未携带 model_overrides={overrides}"
            )
            assert await _ckpt_has_overrides(new_review_run_id), (
                f"回路内 chapter-review 子 run（{new_review_run_id}）未携带 model_overrides={overrides}"
            )

    asyncio.run(run())


def test_chapter_review_auto_revise_loop_without_overrides_inherits_from_review_run(tmp_path: Path):
    """回归：resume body 不传 model_overrides 且原 review run checkpoint_json 内也无 overrides
    时，回路子 run 的 checkpoint_json 中不应出现 model_overrides 键（保证缺省零行为变更）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                    # 故意不传 model_overrides
                },
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            import time as _list_t
            _list_deadline = _list_t.monotonic() + 120.0
            new_write_run_id: str | None = None
            new_review_run_id: str | None = None
            while _list_t.monotonic() < _list_deadline:
                rr = await _request(app, "GET", f"/api/projects/{pid}/runs")
                assert rr.status_code == 200, rr.text
                rows = rr.json()
                review_rows = [
                    row for row in rows
                    if row.get("workflow_name") == "chapter-review"
                    and row["run_id"] != first_review_run_id
                ]
                write_rows = [
                    row for row in rows
                    if row.get("workflow_name") == "chapter-write"
                ]
                paused_new = [row for row in review_rows if row["status"] == "PAUSED"]
                if paused_new and write_rows:
                    new_review_run_id = max(
                        paused_new,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    new_write_run_id = max(
                        write_rows,
                        key=lambda r0: r0.get("started_at") or "",
                    )["run_id"]
                    break
                await asyncio.sleep(0.3)

            assert new_write_run_id is not None
            assert new_review_run_id is not None

            async def _ckpt_omits_overrides(run_id: str) -> None:
                rr = await _request(app, "GET", f"/api/runs/{run_id}")
                assert rr.status_code == 200, rr.text
                ckpt = rr.json().get("checkpoint_json") or {}
                for node_id, node_ckpt in ckpt.items():
                    if not isinstance(node_ckpt, dict):
                        continue
                    if "model_overrides" in node_ckpt:
                        raise AssertionError(
                            f"未传 model_overrides 时回路子 run {run_id} 的 {node_id} 不应携带该键；"
                            f"实际={node_ckpt!r}"
                        )

            await _ckpt_omits_overrides(new_write_run_id)
            await _ckpt_omits_overrides(new_review_run_id)

    asyncio.run(run())
