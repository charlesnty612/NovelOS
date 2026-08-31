"""Workflow Resume After Crash 集成测试（Sprint 4-A）。

- 启动 chapter-review → author_review Human 节点 PAUSED
- 模拟重启：新建 WorkflowEngine 实例（同 db_path）resume 成功
- 校验节点行 + checkpoint 持久化
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.workflows import get_workflow




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


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200


def _director_script():
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "x",
                "chapter_goal": "test",
                "core_conflict": "x",
                "turning_point": "x",
                "expected_role": "setup",
                "key_beats": [],
                "character_changes_planned": [],
                "hook_handling": [],
                "debt_handling": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
            },
            ensure_ascii=False,
        )
    ]


def _writer_script():
    prose = "测试 prose。灯芯爆了一下花。"
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "x",
                "prose": prose,
                "self_report": {
                    "slots_filled": ["s1"],
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


def test_resume_via_new_engine_instance(tmp_path: Path):
    """PAUSED 后新建 WorkflowEngine 实例（同 db_path）resume 成功。"""
    db_path = tmp_path / "novelos.db"
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(db_path)
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            # 建项目 / character / chapter
            r = await _request(app, "POST", "/api/projects", json={"name": "Crash test"})
            assert r.status_code == 201
            pid = r.json()["project_id"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "林夕", "role": "protagonist"},
            )
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "C1"}
            )
            assert r.status_code == 201
            cid = r.json()["chapter_id"]

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            # 先 plan + write
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "intent", "mock_providers": mock_providers},
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

            # 启动 review → PAUSED（author_review 节点）
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
            run_id = paused["run_id"]

            # 校验：workflow_run_nodes 含至少 3 行（V1.3：basic_checks + critic_review + author_review PENDING；
            # V1.3+ 可选含 deep_review，因 deep_review 默认跳过不落节点行）。
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT node_id, status FROM workflow_run_nodes WHERE run_id = ? ORDER BY started_at ASC",
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) >= 3, rows
            statuses = {r["node_id"]: r["status"] for r in rows}
            assert statuses["basic_checks"] == "COMPLETED"
            assert statuses["critic_review"] in ("COMPLETED", "FAILED")
            assert statuses["author_review"] == "PENDING"

            # 校验 checkpoint_json 含 author_review pause payload
            run_before = get_run(db_path, run_id)
            ckpt = run_before["checkpoint_json"]
            assert "author_review" in ckpt
            assert "__pause_payload__" in ckpt["author_review"]

            # 模拟「进程崩溃」：完全新建 WorkflowEngine（同 db_path）
            new_engine = WorkflowEngine(db_path)
            wf = get_workflow("chapter-review")
            new_engine.resume(run_id, wf["nodes"], human_input={"approved": True})

            # 校验：run COMPLETED，chapter.status=REVIEWED
            run_after = get_run(db_path, run_id)
            run_after = await _wait_run_terminal(app, run_after["run_id"], expected=("COMPLETED",))
            assert run_after["current_node"] is None

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

            # 校验节点行：author_review 已被 SKIPPED（resume 时被收尾），mark_reviewed COMPLETED
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT node_id, status FROM workflow_run_nodes WHERE run_id = ? ORDER BY started_at ASC",
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
            statuses = {r["node_id"]: r["status"] for r in rows}
            assert statuses["author_review"] == "SKIPPED"
            assert statuses["mark_reviewed"] == "COMPLETED"

    asyncio.run(run())


def test_resume_unrelated_state_returns_no_resume(tmp_path: Path):
    """非 PAUSED run 调用 resume → ValueError。"""
    db_path = tmp_path / "novelos.db"
    apply_migrations(db_path)
    engine = WorkflowEngine(db_path)

    # 创建一个假 PAUSED-like 但状态不对的 run：用 create_adhoc_run 插入 RUNNING 行
    from packages.core.agent_runtime.runner import create_adhoc_run

    run_id = create_adhoc_run(db_path, "test-crash")
    # create_adhoc_run 走 run_agent 会把 run 收尾为 COMPLETED/FAILED。
    # 这里我们仅断言：resume 一个非 PAUSED run 抛 ValueError
    # run_id 的 status 可能是 COMPLETED 或 RUNNING（如果没调 run_agent）。两者都应抛 ValueError。
    wf = get_workflow("chapter-review")
    if wf is None:
        import pytest

        pytest.skip("chapter-review workflow not registered")
    raised = False
    try:
        engine.resume(run_id, wf["nodes"])
    except ValueError:
        raised = True
    assert raised, f"resume of non-PAUSED run {run_id!r} should raise ValueError"
