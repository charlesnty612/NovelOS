"""chapter-commit 时序守卫（pipeline 兜底层）workflow 级测试。

对应审查缺口的 P3-1：``packages/workflows/chapter_commit/pipeline.py`` 的
``_commit_node`` 内置时序守卫（draft.created_at > 最近一次 COMPLETED review.ended_at
→ raise ValueError('...未审...')）此前无任何 workflow 级测试覆盖。本文件复用
``tests/workflow/test_chapter_commit_observer_retry.py`` 的 async 轮询夹具模式
（``_create_app`` / ``_push_chapter_to_reviewed`` / ``_director_script`` / ``_writer_script`` /
``_observer_valid_noop_script`` 的等价脚本），但直接走 :class:`WorkflowEngine` 启动
chapter-commit workflow 以**绕过** API 层 ``_guard_commit_draft_freshness``（router
那一层的 5 个用例已经在 ``tests/api/test_chapter_commit_freshness_guard.py`` 覆盖），
从而真正触发 pipeline 兜底守卫。

用例：
- ``test_pipeline_guard_blocks_commit_when_draft_newer_than_review``：
  REVIEWED 章节 + SQL 注入「更晚的 draft」→ 直接启 chapter-commit run → run FAILED，
  run.error 含「未审」文案。
- ``test_pipeline_guard_does_not_fire_when_drafts_not_newer_than_review``（控制）：
  不注入更新草稿，仅复用 ``_push_chapter_to_reviewed`` 推到 REVIEWED → run COMPLETED，
  证明夹具/启动链路本身没有引入额外失败——只测出「守卫是否触发」。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine


# ---------------------------------------------------------------------------
# 夹具（与 tests/workflow/test_chapter_commit_observer_retry.py 对齐）
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


async def _make_project(app, name: str = "pipeline guard") -> str:
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
    """同步 prompts（注册 observer 等 ACTIVE 行）。"""
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Mock 脚本（最小子集：director / writer / observer 合法 no-op）
# ---------------------------------------------------------------------------


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "测试目标",
                "core_conflict": "A vs B",
                "turning_point": "T",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "p",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "q",
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
                "notes_for_planner": "n",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = "测试正文。章节正文片段。"
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
# 推到 REVIEWED 的链路（与 observer_retry 测试共用相同写法）
# ---------------------------------------------------------------------------


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    """轮询直到 run.status ∈ expected。"""
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        deadline = time.monotonic() + timeout
        last_run = None
        while time.monotonic() < deadline:
            r = await client.get(f"/api/runs/{run_id}")
            if r.status_code == 404:
                raise AssertionError(f"run {run_id} disappeared")
            run = r.json()
            last_run = run
            if run["status"] in expected:
                return run
            await asyncio.sleep(0.2)
        raise AssertionError(
            f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})"
        )
    finally:
        await client.aclose()


async def _push_chapter_to_reviewed(app, pid: str, cid: str, mock_providers: dict) -> None:
    """plan + write + review(approve) → REVIEWED。"""
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
    paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200, r.text
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))


def _insert_stale_draft(conn, *, chapter_id: str, version: int, content: str, created_at: str) -> str:
    """SQL 注入一个「晚于最近一次 COMPLETED review.ended_at」的草稿行（绕过 create_draft 状态机）。"""
    dr_id = new_id("dr")
    conn.execute(
        """
        INSERT INTO drafts
            (draft_id, chapter_id, version, content, created_by,
             prompt_version, model_id, created_at)
        VALUES
            (:draft_id, :chapter_id, :version, :content, :created_by,
             :prompt_version, :model_id, :created_at)
        """,
        {
            "draft_id": dr_id,
            "chapter_id": chapter_id,
            "version": version,
            "content": content,
            "created_by": "test:pipeline-guard",
            "prompt_version": None,
            "model_id": None,
            "created_at": created_at,
        },
    )
    return dr_id


async def _start_chapter_commit_run(
    engine: WorkflowEngine, *, db_path: str, project_id: str, chapter_id: str,
    mock_providers: dict | None,
) -> str:
    """绕过 API 层直接启动 chapter-commit workflow（不调 ``_guard_commit_draft_freshness``）。"""
    wf = get_workflow("chapter-commit")
    assert wf is not None, "chapter-commit workflow 未注册"
    return engine.start_with_nodes_async(
        "chapter-commit",
        wf["nodes"],
        chapter_id=chapter_id,
        # db_path 在 ctx 里被引擎 serialize 进 checkpoint_json（_update_run_checkpoint）；
        # Settings.db_path 是 Path 对象，json.dumps 不认——必须提前 str()，与
        # packages/core/api/routers/workflows.py::_start_workflow 的写法对齐。
        initial_ctx={
            "db_path": str(db_path),
            "project_id": project_id,
            "chapter_id": chapter_id,
        },
        mock_providers=mock_providers,
        checkpoint_exclude=wf.get("checkpoint_exclude"),
    )


async def _wait_engine_run_terminal(
    engine: WorkflowEngine, run_id: str, *, expected: tuple[str, ...], timeout: float = 30.0
) -> dict:
    """直接通过 engine 轮询 run 行（不经 HTTP）。"""
    from packages.core.workflow_runtime.runs import get_run

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        run = get_run(engine.db_path, run_id)
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        last = run
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s (last={last['status']!r})"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pipeline_guard_blocks_commit_when_draft_newer_than_review(tmp_path: Path):
    """P3-1 主用例：draft 晚于最近一次 COMPLETED review → pipeline 兜底守卫拒收。

    走 WorkflowEngine 启动 chapter-commit run（绕过 API 层 router 的
    ``_guard_commit_draft_freshness``），仅由 ``_commit_node`` 内嵌的 SQL 守卫生效。
    断言 run.status == FAILED 且 run.error 含「未审」相关文案。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "pipeline guard chapter")

            # 推章节到 REVIEWED（创建一条 COMPLETED review run + 至少一条 draft）
            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            db_path = app.state.settings.db_path
            # SQL 注入一条「更晚的 draft」：created_at 设为远未来，确保
            # 严格大于最近一次 COMPLETED review 的 ended_at。
            conn = get_connection(db_path)
            try:
                _insert_stale_draft(
                    conn,
                    chapter_id=cid,
                    version=999,
                    content="post-review 续写",
                    created_at="9999-01-01T00:00:00+00:00",
                )
                conn.commit()
            finally:
                conn.close()

            # 直接通过引擎启动 chapter-commit，**不走** API endpoint（避免被
            # router 的 guard 拦截）；observer mock 用合法 no-op 让 observer
            # 节点走通、最终到 commit 节点撞上 pipeline 内嵌守卫。
            engine = WorkflowEngine(db_path)
            commit_mocks = dict(base_mocks)
            commit_mocks["observer"] = _observer_valid_noop_script()
            run_id = await _start_chapter_commit_run(
                engine,
                db_path=db_path,
                project_id=pid,
                chapter_id=cid,
                mock_providers=commit_mocks,
            )

            final_run = await _wait_engine_run_terminal(
                engine, run_id, expected=("FAILED",), timeout=30.0
            )
            assert final_run["status"] == "FAILED", (
                f"pipeline 兜底守卫应让 run 进入 FAILED，实际 status={final_run['status']!r}"
            )
            err = (final_run.get("error") or "")
            assert "未审" in err, (
                f"run.error 应含「未审」文案（_commit_node 守卫抛 ValueError），实际：{err!r}"
            )
            # 错误信息还应带版本号与 review 结束时间，便于审计
            assert "v999" in err, f"run.error 应含触发守卫的草稿版本号 v999，实际：{err!r}"

    asyncio.run(run())


def test_pipeline_guard_does_not_fire_when_drafts_not_newer_than_review(tmp_path: Path):
    """P3-1 控制用例：review 完成后没有「更晚的 draft」→ pipeline 守卫不触发、commit 跑通。

    目的：证明上面那个 FAILED 确实是守卫抛的，而不是其他环节（observer mock 错、
    setup 漏 init_genesis、状态机不对等）导致的偶发 FAILED。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "control chapter")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            db_path = app.state.settings.db_path
            engine = WorkflowEngine(db_path)
            commit_mocks = dict(base_mocks)
            commit_mocks["observer"] = _observer_valid_noop_script()
            run_id = await _start_chapter_commit_run(
                engine,
                db_path=db_path,
                project_id=pid,
                chapter_id=cid,
                mock_providers=commit_mocks,
            )

            final_run = await _wait_engine_run_terminal(
                engine, run_id, expected=("COMPLETED", "FAILED"), timeout=30.0
            )
            assert final_run["status"] == "COMPLETED", (
                f"控制用例应 COMPLETED（守卫不触发、commit 正常完成）；"
                f"实际 status={final_run['status']!r}, error={(final_run.get('error') or '')!r}"
            )

            # 顺带：chapters.status 应推进到 COMMITTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED", (
                f"控制用例成功后 chapter.status 应为 COMMITTED，实际 {r.json()['status']!r}"
            )

    asyncio.run(run())
