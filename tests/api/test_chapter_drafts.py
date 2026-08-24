"""/api/chapters/{id}/drafts 集成测试（Sprint 5，A1 任务）。

覆盖（任务书给死）：
- 创建首版 draft → version=1，created_by="human"
- 第二版 draft → version=2
- 列表按 version DESC
- chapter 不存在 → 404
- chapter.status = PLANNED → 409
- chapter.status = COMMITTED → 409
- 空 content → 422

测试模式参考 ``tests/integration/test_chapters_api.py``：httpx.ASGITransport + tmp_path db。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "drafts 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, *, status: str | None = None) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "C1"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]
    if status:
        r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"status": status})
        assert r.status_code == 200, r.text
    return cid


def test_create_first_draft_version_one(tmp_path: Path):
    """PLANNED→DRAFTED 后创建首版 draft → version=1，created_by='human'。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "第一章正文"},
            )
            assert r.status_code == 201, r.text
            draft = r.json()
            assert draft["chapter_id"] == cid
            assert draft["version"] == 1
            assert draft["content"] == "第一章正文"
            assert draft["created_by"] == "human"
            assert draft["prompt_version"] is None
            assert draft["model_id"] is None
            assert draft["draft_id"].startswith("dr_")
            assert isinstance(draft["created_at"], str) and draft["created_at"]

    asyncio.run(run())


def test_create_second_draft_version_two(tmp_path: Path):
    """第二版 draft → version=2。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r1 = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "v1 正文"},
            )
            assert r1.status_code == 201
            assert r1.json()["version"] == 1

            r2 = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "v2 正文"},
            )
            assert r2.status_code == 201, r2.text
            assert r2.json()["version"] == 2
            assert r2.json()["content"] == "v2 正文"

    asyncio.run(run())


def test_list_drafts_returns_descending(tmp_path: Path):
    """列表按 version DESC 排序。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            for content in ["v1", "v2", "v3"]:
                r = await _request(
                    app, "POST", f"/api/chapters/{cid}/drafts",
                    json={"content": content},
                )
                assert r.status_code == 201

            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert r.status_code == 200, r.text
            drafts = r.json()
            assert [d["version"] for d in drafts] == [3, 2, 1]
            assert [d["content"] for d in drafts] == ["v3", "v2", "v1"]

    asyncio.run(run())


def test_list_drafts_unknown_chapter_returns_404(tmp_path: Path):
    """chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/chapters/ch_nope/drafts")
            assert r.status_code == 404
            assert "chapter" in r.json()["detail"]

    asyncio.run(run())


def test_create_draft_unknown_chapter_returns_404(tmp_path: Path):
    """POST drafts，chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/chapters/ch_nope/drafts",
                json={"content": "x"},
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_create_draft_planned_status_returns_409(tmp_path: Path):
    """chapter.status = PLANNED → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)  # 默认 PLANNED

            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "PLANNED" in detail
            assert "DRAFTED" in detail and "REVIEWED" in detail

    asyncio.run(run())


def test_create_draft_committed_status_returns_409(tmp_path: Path):
    """chapter.status = COMMITTED → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            # 推进到 COMMITTED
            for nxt in ["REVIEWED", "COMMITTED"]:
                r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"status": nxt})
                assert r.status_code == 200, r.text

            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "x"},
            )
            assert r.status_code == 409
            assert "COMMITTED" in r.json()["detail"]

    asyncio.run(run())


def test_create_draft_empty_content_returns_422(tmp_path: Path):
    """content 空字符串 → 422（pydantic min_length=1）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": ""},
            )
            assert r.status_code == 422

    asyncio.run(run())


def test_create_draft_over_max_length_returns_422(tmp_path: Path):
    """content 长度超过 500_000 → 422（pydantic max_length=500_000，防超大 body）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            # 500_001 字符（> 上限 500_000）；用 ASCII 字符避免编码差异
            oversized = "a" * 500_001
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": oversized},
            )
            assert r.status_code == 422, r.text

            # 边界：恰好 500_000 字符应通过
            boundary = "b" * 500_000
            r2 = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": boundary},
            )
            assert r2.status_code == 201, r2.text

    asyncio.run(run())


# =============================================================================
# Sprint 5 review F2：(chapter_id, version) 唯一索引 — 自增路径不退化 + 直插重复触发 IntegrityError
# =============================================================================


def test_create_draft_after_manual_v1_still_yields_v2(tmp_path: Path):
    """手工 INSERT version=1 后，``create_draft`` 仍能拿到 version=2
    （COALESCE(MAX)+1 自增路径不被 F2 唯一索引破坏）。"""
    from packages.core.db import get_connection

    app = _create_app(tmp_path)
    db_path = tmp_path / "novelos.db"

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            # 手工 SQL 直插一份 version=1
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO drafts
                        (draft_id, chapter_id, version, content,
                         created_by, prompt_version, model_id, created_at)
                    VALUES (?, ?, 1, ?, 'human', NULL, NULL, ?)
                    """,
                    ("dr_manual_v1", cid, "手工 v1", "2025-01-01T00:00:00Z"),
                )
                conn.commit()
            finally:
                conn.close()

            # 再走 API create_draft → 应得 version=2（COALESCE MAX+1）
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "API v2"},
            )
            assert r.status_code == 201, r.text
            draft = r.json()
            assert draft["version"] == 2
            assert draft["content"] == "API v2"

    asyncio.run(run())


def test_duplicate_chapter_version_triggers_integrity_error(tmp_path: Path):
    """直接 SQL 插入重复 (chapter_id, version) 触发 idx_drafts_chapter_version 唯一约束 → IntegrityError。"""
    from packages.core.db import get_connection

    app = _create_app(tmp_path)
    db_path = tmp_path / "novelos.db"

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            # 通过 API 创建一份 version=1 的 draft
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "API v1"},
            )
            assert r.status_code == 201
            assert r.json()["version"] == 1

            # 手工再 INSERT 一份 version=1（重复），期望 IntegrityError
            conn = get_connection(db_path)
            try:
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO drafts
                            (draft_id, chapter_id, version, content,
                             created_by, prompt_version, model_id, created_at)
                        VALUES (?, ?, 1, ?, 'human', NULL, NULL, ?)
                        """,
                        ("dr_duplicate", cid, "dup", "2025-01-01T00:00:00Z"),
                    )
            finally:
                conn.close()

    asyncio.run(run())
