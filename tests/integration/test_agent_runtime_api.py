"""Agent Runtime 端到端集成测试（Sprint 3）。

覆盖：
1. POST /agents/sync → 三 prompt（director / writer / observer）注册入表。
2. GET /agents 含 director / writer / observer。
3. model_configs CRUD：create → list → patch（enabled 关闭） → delete。
4. POST /agents/{name}/run：mock_script 先坏后好 → 重试后成功 + ai_call_logs retry_count=1。
5. POST /agents/{name}/run：mock_script 两次都坏 → AgentOutputError（502）+ ai_call_logs retry_count=1。
6. 无 ACTIVE prompt → 404。
7. 无 model_config → 422。
8. workflow_runs 行被创建 + 收尾为 COMPLETED / FAILED。

测试模式：httpx.ASGITransport + tmp_path db（与 S1/S2 一致）。
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


def _valid_observer_output() -> dict:
    """合规的 Observer 输出：仅 7 个空数组。"""
    return {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


# ---------------------------------------------------------------------------
# 1. sync + list agents
# ---------------------------------------------------------------------------


def test_sync_registers_all_prompts_from_docs(tmp_path: Path):
    """sync 后 GET /agents 应含 director / writer / observer（以及 arbiter / deconstructor）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 改默认 docs_dir 指向真实目录

            cwd = Path.cwd().resolve()
            docs_dir = cwd / "docs" / "agents" / "prompts"
            r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
            assert r.status_code == 200, r.text
            payload = r.json()
            assert payload["agents"] == sorted(payload["agents"])  # sorted
            assert {"director", "writer", "observer", "arbiter", "deconstructor"}.issubset(set(payload["agents"]))

            # GET /agents
            r = await _request(app, "GET", "/api/agents")
            assert r.status_code == 200
            names = [a["name"] for a in r.json()]
            assert "director" in names and "observer" in names and "writer" in names

            # GET /agents/observer/prompts → 至少 1 个 ACTIVE
            r = await _request(app, "GET", "/api/agents/observer/prompts")
            assert r.status_code == 200
            prompts = r.json()
            assert len(prompts) >= 1
            assert prompts[0]["status"] == "ACTIVE"

    asyncio.run(run())


def test_sync_idempotent(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):

            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            r1 = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
            r2 = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
            assert r1.status_code == 200 and r2.status_code == 200
            # 第二次：scanned 同长；updated 为 0（无内容变化）
            assert r1.json()["scanned"] == r2.json()["scanned"]
            assert r2.json()["updated"] == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. model_configs CRUD
# ---------------------------------------------------------------------------


def test_model_configs_crud_happy_path(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # create
            r = await _request(
                app, "POST", "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"base_url": "https://api.openai.com/v1"},
                    "enabled": 1,
                },
            )
            assert r.status_code == 201, r.text
            cfg = r.json()
            cid = cfg["config_id"]
            assert cfg["capability"] == "reasoning"
            assert cfg["params_json"] == json.dumps({"base_url": "https://api.openai.com/v1"}, ensure_ascii=False)

            # list
            r = await _request(app, "GET", "/api/model-configs")
            assert r.status_code == 200
            assert any(c["config_id"] == cid for c in r.json())

            # get one
            r = await _request(app, "GET", f"/api/model-configs/{cid}")
            assert r.status_code == 200
            assert r.json()["config_id"] == cid

            # patch（关掉 enabled）
            r = await _request(app, "PATCH", f"/api/model-configs/{cid}", json={"enabled": 0})
            assert r.status_code == 200
            assert r.json()["enabled"] == 0

            # patch 再开
            r = await _request(app, "PATCH", f"/api/model-configs/{cid}", json={"enabled": 1})
            assert r.json()["enabled"] == 1

            # delete
            r = await _request(app, "DELETE", f"/api/model-configs/{cid}")
            assert r.status_code == 204

            # get after delete → 404
            r = await _request(app, "GET", f"/api/model-configs/{cid}")
            assert r.status_code == 404

    asyncio.run(run())


def test_model_configs_create_validation_errors(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 缺 capability
            r = await _request(app, "POST", "/api/model-configs", json={"provider": "openai", "model": "m"})
            assert r.status_code == 422
            # params_json 不可解析
            r = await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "openai", "model": "m", "params_json": "{not json"},
            )
            assert r.status_code == 422

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. observer run happy + retry
# ---------------------------------------------------------------------------


def _observer_7_arrays_json() -> str:
    return json.dumps(_valid_observer_output(), ensure_ascii=False)


def test_run_observer_with_mock_script_happy_path(tmp_path: Path):
    """配 mock model_config + 给合法 observer JSON → 200，ai_call_logs retry_count=0。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            # 配 mock provider（runner 内部走 ModelRouter.resolve("reasoning")）
            r = await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )
            assert r.status_code == 201

            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_001"},
                    "expected": "observer",
                    "mock_script": [_observer_7_arrays_json()],
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["agent"] == "observer"
            assert body["output"]["character_changes"] == []
            assert "run_id" in body

            # 查 ai_call_logs 验证 retry_count=0
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                rows = conn.execute(
                    "SELECT retry_count, output_json, error FROM ai_call_logs ORDER BY created_at"
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            row = rows[0]
            assert row["retry_count"] == 0
            assert row["error"] is None
            out = json.loads(row["output_json"])
            assert "character_changes" in out

            # 查 workflow_runs 收尾为 COMPLETED
            conn = get_connection(settings.db_path)
            try:
                wf = conn.execute(
                    "SELECT status FROM workflow_runs WHERE run_id = ?", (body["run_id"],)
                ).fetchone()
            finally:
                conn.close()
            assert wf["status"] == "COMPLETED"

    asyncio.run(run())


def test_run_observer_with_bad_then_good_script_retries(tmp_path: Path):
    """mock_script 先坏 JSON 再给合法 → 201 + retry_count=1。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )

            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_002"},
                    "expected": "observer",
                    "mock_script": ["not json at all", _observer_7_arrays_json()],
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["output"]["character_changes"] == []

            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT retry_count, error FROM ai_call_logs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert row["retry_count"] == 1
            assert row["error"] is None

    asyncio.run(run())


def test_run_observer_with_forbidden_metadata_raises_agent_output_error(tmp_path: Path):
    """mock_script 给含 delta_id 元信息的 observer 输出 → 契约校验失败 → 502。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )

            # 含 delta_id + 缺 new_events：两次都坏 → 502
            bad = {
                "delta_id": "dlt_evil",
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_003"},
                    "expected": "observer",
                    "mock_script": [json.dumps(bad), json.dumps(bad)],
                },
            )
            assert r.status_code == 502, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "agent_output_invalid"

            # ai_call_logs retry_count=1
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT retry_count, error FROM ai_call_logs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert row["retry_count"] == 1
            assert row["error"] is not None

            # workflow_runs 收尾为 FAILED
            conn = get_connection(settings.db_path)
            try:
                wf = conn.execute(
                    "SELECT status, error FROM workflow_runs WHERE status = 'FAILED' ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert wf is not None
            assert wf["status"] == "FAILED"

    asyncio.run(run())


def test_run_missing_model_config_returns_422(tmp_path: Path):
    """sync 了 prompt 但未配置 model_config → 422（ModelNotConfiguredError）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            # 不配 model_config；显式 mock_script=None 走 ModelRouter.resolve
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_004"},
                    "expected": "observer",
                },
            )
            assert r.status_code == 422, r.text
            assert "reasoning" in r.json()["detail"]

    asyncio.run(run())


def test_run_unknown_agent_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 不 sync（无所谓，prompt 找不到先抛）
            r = await _request(
                app, "POST", "/api/agents/nonexistent/run",
                json={"input_payload": {}, "mock_script": ["ok"]},
            )
            assert r.status_code == 404
            assert "nonexistent" in r.json()["detail"]

    asyncio.run(run())


def test_run_validation_input_payload_must_be_dict(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # sync + mock config（避免先撞 model 错误）
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={"input_payload": "not a dict"},
            )
            assert r.status_code == 422

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. adhoc run 落库校验
# ---------------------------------------------------------------------------


def test_run_creates_workflow_runs_and_agents_row(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_005", "draft_id": "dr_005"},
                    "expected": "observer",
                    "mock_script": [_observer_7_arrays_json()],
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]

            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                # workflow_runs 存在且为 adhoc workflow
                wf = conn.execute(
                    "SELECT wfr.run_id, wf.name FROM workflow_runs wfr "
                    "JOIN workflows wf ON wfr.workflow_id = wf.workflow_id "
                    "WHERE wfr.run_id = ?",
                    (run_id,),
                ).fetchone()
                assert wf["name"] == "adhoc"

                # ai_call_logs 含 input_context_ids（chapter_id + draft_id）
                log = conn.execute(
                    "SELECT input_context_ids_json, prompt_version FROM ai_call_logs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                ids = json.loads(log["input_context_ids_json"])
                assert "ch_005" in ids and "dr_005" in ids
                assert log["prompt_version"] == "observer:v1"
            finally:
                conn.close()

    asyncio.run(run())
