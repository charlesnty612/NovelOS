"""Project Backup / Restore API 集成测试（V1.4 Sprint 16 / MVP）。

端点：
- ``GET  /api/projects/{project_id}/backup`` —— 下载 JSON 包（Content-Disposition）
- ``POST /api/projects/import-backup`` —— 导入为新项目

覆盖（任务书）：
- 导出下载：返回 application/json + Content-Disposition attachment；
- 导出 404：project 不存在；
- 导入 happy path：导出 → 导入 → 新项目独立存在；
- 导入 422：坏 format / 坏 version / 坏表名。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


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


async def _make_project(app, name: str = "backup 测试") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "第一章"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _seed_draft(db_path: str, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES
                (:draft_id, :chapter_id, 1, :content, 'human', NULL, NULL,
                 :created_at)
            """,
            {
                "draft_id": new_id("dr"),
                "chapter_id": chapter_id,
                "content": content,
                "created_at": now_iso(),
            },
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /projects/{pid}/backup
# ---------------------------------------------------------------------------


def test_download_backup_happy_path(tmp_path: Path):
    """下载导出：返回 application/json + Content-Disposition attachment。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            _seed_draft(app.state.settings.db_path, cid, "测试正文")

            r = await _request(app, "GET", f"/api/projects/{pid}/backup")
            assert r.status_code == 200, r.text
            assert "application/json" in r.headers["content-type"]
            assert "charset=utf-8" in r.headers["content-type"]
            assert r.headers["content-disposition"] == (
                f'attachment; filename="backup-{pid}.json"'
            )

            body = r.json()
            assert body["format"] == "novelos-backup"
            assert body["version"] == 1
            assert body["project"]["project_id"] == pid
            assert "tables" in body
            assert "characters" in body["tables"]
            assert "chapters" in body["tables"]
            assert "drafts" in body["tables"]
            assert body["metadata"]["api_keys_stripped"] is True

    asyncio.run(run())


def test_download_backup_404_for_unknown_project(tmp_path: Path):
    """不存在的 project → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_nope/backup")
            assert r.status_code == 404
            assert "not found" in r.json()["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# POST /projects/import-backup
# ---------------------------------------------------------------------------


def test_import_backup_happy_path(tmp_path: Path):
    """导出 → 导入 → 新项目独立存在，源项目不变。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            _seed_draft(app.state.settings.db_path, cid, "导入测试正文")

            # 导出
            r = await _request(app, "GET", f"/api/projects/{pid}/backup")
            assert r.status_code == 200
            package = r.json()

            # 导入
            r2 = await _request(
                app, "POST", "/api/projects/import-backup", json=package,
            )
            assert r2.status_code == 201, r2.text
            new_project = r2.json()
            assert new_project["project_id"] != pid
            assert new_project["name"] == "backup 测试（导入）"
            assert new_project["status"] == "ACTIVE"

            # 新项目可见：列表里有 2 个项目
            r3 = await _request(app, "GET", "/api/projects")
            assert r3.status_code == 200
            listed_ids = {p["project_id"] for p in r3.json()}
            assert pid in listed_ids
            assert new_project["project_id"] in listed_ids

    asyncio.run(run())


def test_import_backup_422_for_bad_format(tmp_path: Path):
    """坏 format → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            payload = {"format": "other", "version": 1, "exported_at": "x",
                       "project": {}, "tables": {}}
            r = await _request(
                app, "POST", "/api/projects/import-backup", json=payload,
            )
            assert r.status_code == 422
            assert "format" in r.json()["detail"].lower()

    asyncio.run(run())


def test_import_backup_422_for_bad_version(tmp_path: Path):
    """坏 version → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            payload = {
                "format": "novelos-backup",
                "version": 2,
                "exported_at": "x",
                "project": {"project_id": "p", "name": "n", "created_at": "c"},
                "tables": {},
            }
            r = await _request(
                app, "POST", "/api/projects/import-backup", json=payload,
            )
            assert r.status_code == 422
            assert "version" in r.json()["detail"].lower()

    asyncio.run(run())


def test_import_backup_422_for_unknown_table(tmp_path: Path):
    """表名不在白名单 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            payload = {
                "format": "novelos-backup",
                "version": 1,
                "exported_at": "x",
                "project": {"project_id": "p", "name": "n", "created_at": "c"},
                "tables": {
                    "characters": [],
                    "model_configs": [{"api_key": "secret"}],
                },
            }
            r = await _request(
                app, "POST", "/api/projects/import-backup", json=payload,
            )
            assert r.status_code == 422
            assert "model_configs" in r.json()["detail"]

    asyncio.run(run())


def test_import_backup_422_for_missing_top_key(tmp_path: Path):
    """缺必填顶层 key → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            payload = {
                "format": "novelos-backup",
                "version": 1,
                "project": {"project_id": "p", "name": "n", "created_at": "c"},
                # 缺 "tables"
            }
            r = await _request(
                app, "POST", "/api/projects/import-backup", json=payload,
            )
            assert r.status_code == 422
            assert "tables" in r.json()["detail"]

    asyncio.run(run())