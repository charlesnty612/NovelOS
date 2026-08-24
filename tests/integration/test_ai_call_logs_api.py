"""/api/ai-call-logs 集成测试（Sprint 13 下半）。

覆盖：
- 列表分页（DESC 排序 + limit/offset + 422 边界）
- 过滤（project_id 通过 JOIN workflow_runs / node 精确匹配）
- 详情（input_context_ids + output 解析）
- 不存在 → 404
- **安全**：list / detail 响应不得含 api_key / secret / key 任何敏感字段
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "ai-log-test") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": f"C{number}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _insert_workflow_run(
    db_path: Path,
    *,
    project_id: str,
    chapter_id: str | None = None,
    run_id: str | None = None,
    status: str = "COMPLETED",
) -> str:
    """插一行 workflow_runs（ad-hoc workflow 模式，避免依赖完整注册）。

    若传 ``chapter_id``，会校验 chapter 存在；不传则 chapter_id = NULL（非章节任务，
    过滤 project_id 时不会被命中 —— 这是预期语义）。
    """
    conn = get_connection(db_path)
    try:
        # 确保 ad-hoc workflow 存在（若之前有则跳过）
        wf_row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'adhoc'"
        ).fetchone()
        if wf_row is None:
            wf_id = new_id("wf")
            now = now_iso()
            conn.execute(
                """INSERT INTO workflows (workflow_id, name, version, definition_json,
                                          created_at, updated_at)
                   VALUES (?, 'adhoc', 'v1', '{}', ?, ?)""",
                (wf_id, now, now),
            )
        else:
            wf_id = wf_row["workflow_id"]
        rid = run_id or new_id("wfr")
        now = now_iso()
        conn.execute(
            """INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status,
                                          current_node, checkpoint_json, error,
                                          retry_count, started_at, ended_at)
               VALUES (?, ?, ?, ?, NULL, '{}', NULL, 0, ?, ?)""",
            (rid, wf_id, chapter_id, status, now, now),
        )
        conn.commit()
        return rid
    finally:
        conn.close()


def _insert_workflow_node_run(
    db_path: Path,
    *,
    run_id: str,
    node_run_id: str | None = None,
    node_id: str = "test_node",
) -> str:
    """插一行 workflow_run_nodes（ai_call_logs.node_run_id FK 目标）。"""
    nid = node_run_id or new_id("wfrn")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO workflow_run_nodes
                (node_run_id, run_id, node_id, status,
                 input_json, output_json, started_at)
               VALUES (?, ?, ?, 'COMPLETED', '{}', NULL, ?)""",
            (nid, run_id, node_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return nid


def _insert_ai_log(
    db_path: Path,
    *,
    call_id: str | None = None,
    run_id: str,
    node_run_id: str | None = None,
    agent_id: str | None = None,
    model_id: str = "openai/gpt-4o",
    prompt_version: str = "writer:v1",
    input_context_ids: list[str] | None = None,
    output: dict | None = None,
    token_usage: dict | None = None,
    latency_ms: int = 250,
    cost: float = 0.01,
    error: str | None = None,
    retry_count: int = 0,
    created_at: str | None = None,
) -> str:
    cid = call_id or new_id("aic")
    ca = created_at or now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO ai_call_logs
                (call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
                 input_context_ids_json, output_json, token_usage_json,
                 latency_ms, cost, error, retry_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid, run_id, node_run_id, agent_id, model_id, prompt_version,
                json.dumps(input_context_ids or []),
                json.dumps(output, ensure_ascii=False) if output is not None else None,
                json.dumps(token_usage, ensure_ascii=False) if token_usage is not None else None,
                latency_ms, cost, error, retry_count, ca,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


_FORBIDDEN = {"api_key", "key", "secret", "api_secret", "access_token"}


def _assert_no_sensitive_keys(obj, path: str = "$"):
    """递归遍历 dict/list，断言任何 key 不在 _FORBIDDEN 内。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FORBIDDEN, f"敏感字段出现在 {path}.{k}"
            _assert_no_sensitive_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_sensitive_keys(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_returns_logs_desc_by_created_at(tmp_path: Path):
    """插入 3 条不同 created_at，列表按 DESC 排序。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            db = app.state.settings.db_path
            run_id = _insert_workflow_run(db, project_id=pid, chapter_id=cid)
            c1 = _insert_ai_log(db, run_id=run_id, created_at="2026-08-24T00:00:00+00:00")
            c2 = _insert_ai_log(db, run_id=run_id, created_at="2026-08-24T01:00:00+00:00")
            c3 = _insert_ai_log(db, run_id=run_id, created_at="2026-08-24T02:00:00+00:00")

            r = await _request(app, "GET", "/api/ai-call-logs")
            assert r.status_code == 200, r.text
            rows = r.json()
            ids = [row["call_id"] for row in rows]
            # DESC：c3 → c2 → c1
            assert ids == [c3, c2, c1]
            # summary 形状
            first = rows[0]
            assert {"call_id", "run_id", "node_run_id", "agent_id", "model_id",
                    "prompt_version", "latency_ms", "retry_count", "error",
                    "token_usage", "cost", "created_at"} <= set(first.keys())
            # 摘要不暴露 input_context_ids（详情才有）
            assert "input_context_ids" not in first
            assert "output" not in first
            return True

    assert asyncio.run(run())


def test_list_pagination_limit_and_offset(tmp_path: Path):
    """limit + offset 正确；非法 limit/offset → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            db = app.state.settings.db_path
            run_id = _insert_workflow_run(db, project_id=pid, chapter_id=cid)
            ids = [
                _insert_ai_log(
                    db, run_id=run_id,
                    created_at=f"2026-08-24T00:0{i}:00+00:00",
                )
                for i in range(5)
            ]

            # limit=2 offset=1 → 跳过最新一条，返回剩 4 条里前 2 条
            r = await _request(app, "GET", "/api/ai-call-logs?limit=2&offset=1")
            assert r.status_code == 200, r.text
            rows = r.json()
            assert len(rows) == 2
            # 按 DESC：ids[4], ids[3], ids[2], ids[1], ids[0]
            # offset=1 → 跳过 ids[4]，前 2 = ids[3], ids[2]
            assert [row["call_id"] for row in rows] == [ids[3], ids[2]]

            # limit=0 → 422
            r = await _request(app, "GET", "/api/ai-call-logs?limit=0")
            assert r.status_code == 422, r.text
            # limit=201 → 422
            r = await _request(app, "GET", "/api/ai-call-logs?limit=201")
            assert r.status_code == 422, r.text
            # offset=-1 → 422
            r = await _request(app, "GET", "/api/ai-call-logs?offset=-1")
            assert r.status_code == 422, r.text
            return True

    assert asyncio.run(run())


def test_list_filter_by_project_id(tmp_path: Path):
    """按 project_id 过滤（JOIN workflow_runs + chapters）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, name="A")
            pid_b = await _make_project(app, name="B")
            cid_a = await _make_chapter(app, pid_a, number=1)
            cid_b = await _make_chapter(app, pid_b, number=1)
            db = app.state.settings.db_path
            run_a = _insert_workflow_run(db, project_id=pid_a, chapter_id=cid_a)
            run_b = _insert_workflow_run(db, project_id=pid_b, chapter_id=cid_b)
            cid_a_log = _insert_ai_log(db, run_id=run_a)
            cid_b_log = _insert_ai_log(db, run_id=run_b)

            r = await _request(app, "GET", f"/api/ai-call-logs?project_id={pid_a}")
            assert r.status_code == 200
            rows = r.json()
            assert [row["call_id"] for row in rows] == [cid_a_log]

            r = await _request(app, "GET", f"/api/ai-call-logs?project_id={pid_b}")
            rows = r.json()
            assert [row["call_id"] for row in rows] == [cid_b_log]

            # 不存在的 project → 空 list（不是 404；project 过滤为空语义）
            r = await _request(app, "GET", "/api/ai-call-logs?project_id=prj_nope")
            assert r.status_code == 200
            assert r.json() == []
            return True

    assert asyncio.run(run())


def test_list_filter_by_node(tmp_path: Path):
    """按 node_run_id 精确过滤（FK 要求 ai_call_logs.node_run_id 必须指向存在的 workflow_run_nodes 行）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            db = app.state.settings.db_path
            run_id = _insert_workflow_run(db, project_id=pid, chapter_id=cid)
            node_a = _insert_workflow_node_run(db, run_id=run_id, node_id="nodeA")
            node_b = _insert_workflow_node_run(db, run_id=run_id, node_id="nodeB")
            cid_a = _insert_ai_log(db, run_id=run_id, node_run_id=node_a)
            cid_b = _insert_ai_log(db, run_id=run_id, node_run_id=node_b)

            r = await _request(app, "GET", f"/api/ai-call-logs?node={node_a}")
            assert r.status_code == 200
            rows = r.json()
            assert [row["call_id"] for row in rows] == [cid_a]
            return True

    assert asyncio.run(run())


def test_get_detail_returns_full_prompt_response(tmp_path: Path):
    """详情端点含 input_context_ids（解析为 list）+ output（解析为 dict）+ token_usage（dict）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            db = app.state.settings.db_path
            run_id = _insert_workflow_run(db, project_id=pid, chapter_id=cid)
            cid_log = _insert_ai_log(
                db,
                run_id=run_id,
                input_context_ids=["ch_1", "char_2"],
                output={"schema_version": "writer-output.v1", "prose": "一段正文"},
                token_usage={"prompt": 100, "completion": 50, "total": 150},
                latency_ms=420,
            )

            r = await _request(app, "GET", f"/api/ai-call-logs/{cid_log}")
            assert r.status_code == 200, r.text
            detail = r.json()
            assert detail["call_id"] == cid_log
            assert detail["input_context_ids"] == ["ch_1", "char_2"]
            assert detail["output"] == {"schema_version": "writer-output.v1", "prose": "一段正文"}
            assert detail["token_usage"] == {"prompt": 100, "completion": 50, "total": 150}
            assert detail["latency_ms"] == 420
            return True

    assert asyncio.run(run())


def test_get_detail_returns_404_when_missing(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/ai-call-logs/aic_does_not_exist")
            assert r.status_code == 404, r.text
            return True

    assert asyncio.run(run())


def test_no_sensitive_keys_in_responses(tmp_path: Path):
    """安全断言：list / detail 响应全部字段中无 api_key / key / secret / api_secret / access_token。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            db = app.state.settings.db_path
            run_id = _insert_workflow_run(db, project_id=pid, chapter_id=cid)
            cid_log = _insert_ai_log(
                db,
                run_id=run_id,
                input_context_ids=["ch_1"],
                output={"text": "ok"},
                token_usage={"prompt": 1, "completion": 2, "total": 3},
            )

            r_list = await _request(app, "GET", "/api/ai-call-logs")
            assert r_list.status_code == 200
            for row in r_list.json():
                _assert_no_sensitive_keys(row)

            r_detail = await _request(app, "GET", f"/api/ai-call-logs/{cid_log}")
            assert r_detail.status_code == 200
            _assert_no_sensitive_keys(r_detail.json())
            return True

    assert asyncio.run(run())


import asyncio  # noqa: E402  末尾导入便于上面 asyncio.run 调用