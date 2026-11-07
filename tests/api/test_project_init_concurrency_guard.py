"""project-init 必须带**项目级原子并发守卫**（B-6，2026-11-08）。

缺陷形状：``outline-batch`` 起跑带 ``project_level_guard=True``，而
``project-init`` 走同步 ``start_with_nodes`` 时**没传** ⇒ 同一项目可同时起两条
初始化。persist 节点按 ``volume_id`` 整体 DELETE 后重建，而 ``_upsert_volume``
对同 ``(project, number)`` 复用同一个卷 ⇒ 后一条 run 会把前一条刚建的、
恰好 ``status='PLANNED'`` 且无草稿的章全部删掉重建（``chapter_id`` 全换新，
``chapter_scene_plans`` 随 CASCADE 消失）。

修法：挂载模式（``body.project_id`` 非空）传
``project_level_guard=True``，冲突映射 409。

判别面（2 例）：
1. 同项目已有 RUNNING 的 project-init run ⇒ POST 必须 409（撤掉守卫必红，
   且此时会真的开始跑节点、烧 LLM）；
2. 守卫必须在**插 run 行之前**生效 ⇒ 冲突时 run 行总数不增加。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.ids import new_id, now_iso
from tests._db_fixtures import migrated_db


def _create_app(tmp_path: Path):
    migrated_db(tmp_path)
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _seed_active_init_run(db_path: str, project_id: str) -> None:
    now = now_iso()
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'project-init'"
        ).fetchone()
        wf_id = row[0] if row else new_id("wf")
        if row is None:
            conn.execute(
                "INSERT INTO workflows (workflow_id, name, version, definition_json, "
                "created_at, updated_at) VALUES (?, 'project-init', 'v1', '{}', ?, ?)",
                (wf_id, now, now),
            )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, project_id, "
            "status, checkpoint_json, retry_count, started_at) "
            "VALUES (?, ?, NULL, ?, 'RUNNING', '{}', 0, ?)",
            (new_id("wfr"), wf_id, project_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def _count_project_init_runs(db_path: str, project_id: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM workflow_runs r "
                "JOIN workflows w ON w.workflow_id = r.workflow_id "
                "WHERE w.name = 'project-init' AND r.project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_project_init_is_rejected_when_an_active_run_exists(tmp_path: Path):
    import asyncio

    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "b6"})
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]

            _seed_active_init_run(db_path, pid)
            before = _count_project_init_runs(db_path, pid)

            r = await _request(
                app, "POST", "/api/projects/init",
                json={"brief": {"genre": "玄幻", "logline": "少年得古籍"}, "project_id": pid},
            )
            assert r.status_code == 409, (
                f"并发 project-init 未被守卫拦下（B-6 复现）：{r.status_code} {r.text}"
            )
            # 守卫必须在插 run 行之前生效
            assert _count_project_init_runs(db_path, pid) == before

    asyncio.run(run())
