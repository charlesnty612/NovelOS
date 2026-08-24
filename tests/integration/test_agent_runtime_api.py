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
    """sync 后 GET /agents 应含 6 个 agent（director / writer / observer / arbiter /
    deconstructor_chapter / deconstructor_aggregate）。P2-2 拆分后下划线名注册。"""
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
            expected = {
                "director", "writer", "observer", "arbiter",
                "deconstructor_chapter", "deconstructor_aggregate",
            }
            assert expected == set(payload["agents"]), (
                f"agents mismatch: got {payload['agents']}"
            )

            # GET /agents
            r = await _request(app, "GET", "/api/agents")
            assert r.status_code == 200
            names = [a["name"] for a in r.json()]
            assert "director" in names and "observer" in names and "writer" in names
            assert "deconstructor_chapter" in names
            assert "deconstructor_aggregate" in names

            # GET /agents/observer/prompts → 至少 1 个 ACTIVE
            r = await _request(app, "GET", "/api/agents/observer/prompts")
            assert r.status_code == 200
            prompts = r.json()
            assert len(prompts) >= 1
            assert prompts[0]["status"] == "ACTIVE"

            # P2-2：deconstructor_chapter / deconstructor_aggregate 均能取到 prompt
            for name in ("deconstructor_chapter", "deconstructor_aggregate"):
                r = await _request(app, "GET", f"/api/agents/{name}/prompts")
                assert r.status_code == 200
                ps = r.json()
                assert len(ps) >= 1
                assert ps[0]["status"] == "ACTIVE"

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
            # P1-1：读路径响应 params_json 是 dict（已 json.loads），
            # 不含 api_key 时直接等于入参 dict；附 has_api_key 顶层字段。
            assert cfg["params_json"] == {"base_url": "https://api.openai.com/v1"}
            assert cfg["has_api_key"] is False

            # list
            r = await _request(app, "GET", "/api/model-configs")
            assert r.status_code == 200
            listed = next(c for c in r.json() if c["config_id"] == cid)
            assert listed["params_json"] == {"base_url": "https://api.openai.com/v1"}
            assert listed["has_api_key"] is False

            # get one
            r = await _request(app, "GET", f"/api/model-configs/{cid}")
            assert r.status_code == 200
            assert r.json()["config_id"] == cid
            assert r.json()["has_api_key"] is False

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


def test_model_configs_test_endpoint_disabled_returns_422(tmp_path: Path):
    """P2-5：enabled=0 的 config 不可 /test，返回 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "mock",
                    "model": "mock-1",
                    "enabled": 0,
                },
            )
            assert r.status_code == 201
            cid = r.json()["config_id"]

            r = await _request(app, "POST", f"/api/model-configs/{cid}/test")
            assert r.status_code == 422, r.text
            assert "disabled" in r.json()["detail"].lower() or "enabled=0" in r.json()["detail"]

            # 重新打开 enabled=1 后 /test 可走通（mock provider）
            r = await _request(app, "PATCH", f"/api/model-configs/{cid}", json={"enabled": 1})
            assert r.status_code == 200
            assert r.json()["enabled"] == 1

            r = await _request(app, "POST", f"/api/model-configs/{cid}/test")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["config_id"] == cid
            assert "latency_ms" in body

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


def test_run_observer_strips_forbidden_keys_and_succeeds(tmp_path: Path):
    """P2-1 修订：mock_script 给含 delta_id / schema_version / deviations 的 observer 输出
    → 越权字段剥离（不重试，不抛错） → 201 成功，ai_call_logs.error 含 warn 前缀。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )

            # 含越权字段（10 元信息 + 3 辅助，sample）
            dirty = {
                "delta_id": "dlt_evil",
                "schema_version": "chapter-extract.v0",
                "chapter_id": "ch_003",
                "created_at": "2026-08-23T00:00:00Z",
                "notes": "should be stripped",
                "deviations": [{"x": 1}],
                "self_check": "ok",
                "unresolved_plan_intents": [],
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_003"},
                    "expected": "observer",
                    "mock_script": [json.dumps(dirty)],  # 只 1 次就够：剥离后通过
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["output"]["character_changes"] == []
            # 输出不含越权字段
            assert "delta_id" not in body["output"]
            assert "schema_version" not in body["output"]
            assert "deviations" not in body["output"]
            assert "notes" not in body["output"]

            # ai_call_logs retry_count=0（不重试） + error 含 warn 前缀 + 列出被剥键
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT retry_count, error FROM ai_call_logs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert row["retry_count"] == 0
            assert row["error"] is not None
            assert row["error"].startswith("warn: stripped keys=")
            stripped = row["error"]
            for k in ("delta_id", "schema_version", "chapter_id", "created_at",
                      "notes", "deviations", "self_check", "unresolved_plan_intents"):
                assert k in stripped

            # workflow_runs 收尾为 COMPLETED
            conn = get_connection(settings.db_path)
            try:
                wf = conn.execute(
                    "SELECT status FROM workflow_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert wf["status"] == "COMPLETED"

    asyncio.run(run())


def test_run_observer_strips_when_missing_arrays_auto_filled(tmp_path: Path):
    """P2-1 边界：剥离函数自动补齐缺失的 7 数组为 []（不计入 stripped_keys）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")

            await _request(
                app, "POST", "/api/model-configs",
                json={"capability": "reasoning", "provider": "mock", "model": "mock-1"},
            )

            # 只给部分数组 + 1 个越权字段
            dirty = {
                "delta_id": "dlt_xyz",
                "character_changes": [],
                "new_events": [],
            }
            r = await _request(
                app, "POST", "/api/agents/observer/run",
                json={
                    "input_payload": {"chapter_id": "ch_007"},
                    "expected": "observer",
                    "mock_script": [json.dumps(dirty)],
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            # 缺省 5 数组被自动补为 []
            for k in ("world_changes", "relationship_changes",
                      "resolved_hooks", "new_hooks", "debt_changes"):
                assert body["output"][k] == []

            # stripped_keys 仅含越权 delta_id（不包含自动补的 5 个空数组）
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT error FROM ai_call_logs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert row["error"] == "warn: stripped keys=['delta_id']"

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
