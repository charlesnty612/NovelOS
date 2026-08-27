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
            plan_run = r.json()
            assert plan_run["status"] == "COMPLETED", plan_run
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
            write_run = r.json()
            assert write_run["status"] == "COMPLETED", write_run
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
            review_resp = r.json()
            assert review_resp["status"] == "PAUSED", review_resp
            assert "pause_payload" in review_resp

            # resume with approved=true
            r = await _request(
                app, "POST", f"/api/runs/{review_resp['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            final = r.json()
            assert final["status"] == "COMPLETED", final
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "REVIEWED"

            # 4) chapter-commit (observer 产出 7 空数组，无 HIGH)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            commit_resp = r.json()
            assert commit_resp["status"] == "COMPLETED", commit_resp

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
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text

            # review
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201
            paused = r.json()
            assert paused["status"] == "PAUSED"

            # resume with approved=false → mark_reviewed 抛错 → run FAILED
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": False}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "FAILED"

            # chapters.status 保持 DRAFTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

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
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201
            paused = r.json()
            assert paused["status"] == "PAUSED"

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
            assert final["status"] == "FAILED", final
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
            write_run = r.json()
            assert write_run["status"] == "COMPLETED", write_run
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
            assert r.status_code == 201
            paused2 = r.json()
            assert paused2["status"] == "PAUSED"
            r = await _request(
                app, "POST", f"/api/runs/{paused2['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "COMPLETED"
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
            paused = r.json()
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200
            assert r.json()["status"] == "FAILED"
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
            assert r.json()["status"] == "FAILED"

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
                    assert paused["status"] == "PAUSED"
                    r = await _request(
                        app, "POST", f"/api/runs/{paused['run_id']}/resume",
                        json={"human_input": {"approved": True}},
                    )
                    assert r.status_code == 200, r.text
                    assert r.json()["status"] == "COMPLETED"
                else:
                    r = await _request(
                        app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                        json={"mock_providers": mock_providers},
                    )
                    assert r.status_code == 201, r.text
                    assert r.json()["status"] == "COMPLETED"

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
            write_run = r.json()
            assert write_run["status"] == "FAILED", write_run

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
            assert r.status_code == 201

            r = await _request(app, "GET", f"/api/projects/{pid}/runs")
            assert r.status_code == 200
            runs = r.json()
            assert len(runs) >= 1
            assert all(r["chapter_id"] == cid or r["chapter_id"] is None for r in runs)

    asyncio.run(run())


def test_chapter_review_auto_revise_loop_once_then_approve(tmp_path: Path):
    """P0 自动改稿回路：revise 一次后 approve。

    review PAUSED → resume {revise:true, auto_revise_max:2} → 自动重跑 write→review
    → 新 review PAUSED → resume {approved:true} → COMPLETED + REVIEWED。
    验证 drafts 产生 v2（自动 write 追加新版本）。
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
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201
            paused = r.json()
            assert paused["status"] == "PAUSED"
            first_review_run_id = paused["run_id"]

            # 3) resume with revise + auto_revise_max=2：触发自动回路
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
            loop_result = r.json()
            # 自动回路应返回新 review 的 PAUSED 状态
            assert loop_result["status"] == "PAUSED", loop_result
            assert loop_result["run_id"] != first_review_run_id
            second_review_run_id = loop_result["run_id"]

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
            assert r.json()["status"] == "COMPLETED"
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

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            paused = r.json()

            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "不改"},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "FAILED"
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
