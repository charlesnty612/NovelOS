"""``GET /api/ai-call-logs?project_id=`` 必须同时覆盖章节级与**项目级** run（B-2，2026-11-08）。

缺陷形状：过滤用的 SQL 是 ``JOIN workflow_runs r`` + ``JOIN chapters ch``，
而 ``project-init`` / ``outline-batch`` / ``deconstruct`` 这三类项目级 run 的
``workflow_runs.chapter_id`` 为 NULL ⇒ INNER JOIN 把它们名下的全部
``ai_call_logs`` 静默滤掉。注释「workflow_runs 没有 project_id 列」在 0037
迁移之后已过期。

修法：0037 的 ``workflow_runs.project_id`` 为主，``chapters`` 反查为兜底
（历史 NULL 行），JOIN 降为 LEFT JOIN。

判别面（3 例）：
1. 项目级 run（``chapter_id IS NULL``）的调用必须出现；
2. 章节级 run 的调用照旧出现（防「修一个坏一个」）；
3. 别的项目的调用不得出现（防过滤条件写成恒真）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from tests._db_fixtures import migrated_db


def _create_app(tmp_path: Path):
    migrated_db(tmp_path)
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _seed(db_path: str, *, project_id: str, chapter_id: str | None, tag: str) -> str:
    """建一条 run + 一条 ai_call_log，返回 call_id。

    ``chapter_id=None`` 即项目级 run（project-init / outline-batch / deconstruct 的形态）。
    """
    run_id = new_id("wfr")
    call_id = new_id("aic")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        wf = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = ?", (f"wf-{tag}",)
        ).fetchone()
        if wf is None:
            wf_id = new_id("wf")
            conn.execute(
                "INSERT INTO workflows (workflow_id, name, version, definition_json, "
                "created_at, updated_at) VALUES (?, ?, 'v1', '{}', ?, ?)",
                (wf_id, f"wf-{tag}", now, now),
            )
        else:
            wf_id = wf["workflow_id"]
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, project_id, "
            "status, checkpoint_json, retry_count, started_at) "
            "VALUES (?, ?, ?, ?, 'COMPLETED', '{}', 0, ?)",
            (run_id, wf_id, chapter_id, project_id, now),
        )
        conn.execute(
            "INSERT INTO ai_call_logs (call_id, run_id, model_id, input_context_ids_json, "
            "token_usage_json, latency_ms, retry_count, created_at) "
            "VALUES (?, ?, 'm', '[]', '{}', 1, 0, ?)",
            (call_id, run_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return call_id


def test_project_level_run_calls_are_listed(tmp_path):
    """项目级 run（chapter_id NULL）的调用不得被项目过滤丢掉。"""
    asyncio.run(_project_level_run_calls_are_listed(tmp_path))


async def _project_level_run_calls_are_listed(tmp_path):
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, 'b2', 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    project_level = _seed(db_path, project_id=pid, chapter_id=None, tag="init")

    r = await _request(app, "GET", f"/api/ai-call-logs?project_id={pid}")
    assert r.status_code == 200, r.text
    ids = [row["call_id"] for row in r.json()]
    assert project_level in ids, "项目级 run 的 ai_call_logs 被静默丢弃（B-2 复现）"


def test_chapter_level_run_calls_still_listed(tmp_path):
    """反向对照：章节级 run 的调用照旧可见（防止「修一个坏一个」）。"""
    asyncio.run(_chapter_level_run_calls_still_listed(tmp_path))


async def _chapter_level_run_calls_still_listed(tmp_path):
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, 'b2', 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, 't', '{}', 'DRAFTED', 'VISIBLE', ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    chapter_level = _seed(db_path, project_id=pid, chapter_id=cid, tag="write")

    r = await _request(app, "GET", f"/api/ai-call-logs?project_id={pid}")
    assert r.status_code == 200, r.text
    assert chapter_level in [row["call_id"] for row in r.json()]


def test_other_project_calls_are_excluded(tmp_path):
    """反向对照：别的项目的调用不得出现（防过滤条件写成恒真）。"""
    asyncio.run(_other_project_calls_are_excluded(tmp_path))


async def _other_project_calls_are_excluded(tmp_path):
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)
    pid_a = new_id("prj")
    pid_b = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        for pid, name in ((pid_a, "a"), (pid_b, "b")):
            conn.execute(
                "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (pid, name, now, now),
            )
        conn.commit()
    finally:
        conn.close()

    mine = _seed(db_path, project_id=pid_a, chapter_id=None, tag="a")
    other = _seed(db_path, project_id=pid_b, chapter_id=None, tag="b")

    r = await _request(app, "GET", f"/api/ai-call-logs?project_id={pid_a}")
    assert r.status_code == 200, r.text
    ids = [row["call_id"] for row in r.json()]
    assert mine in ids
    assert other not in ids
