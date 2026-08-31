"""/api/chapters/{id}/revision-note 集成测试（Sprint 6，task R1）。

覆盖（任务书给死）：
- PATCH 200 设置成功 → GET chapter 验证 plan_json.revision_note 写入
- PATCH 空串 → revision_note 键消失
- COMMITTED 状态 → 409
- 不存在 chapter → 404

测试模式参考 ``tests/api/test_chapter_drafts.py``：
httpx.ASGITransport + tmp_path db + ``Settings(data_dir=tmp_path)`` 构造 app。
"""

from __future__ import annotations

import asyncio
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


async def _make_project(app, name: str = "revision_note 项目") -> str:
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
    if status and status != "PLANNED":
        # 沿 ALLOWED_NEXT 走最短正向链：PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED
        chain = ["PLANNED", "DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"]
        target_idx = chain.index(status)
        for s in chain[1:target_idx + 1]:
            r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"status": s})
            assert r.status_code == 200, r.text
    return cid


# ---------------------------------------------------------------------------
# 设置 / 清除
# ---------------------------------------------------------------------------


def test_patch_revision_note_set_success(tmp_path: Path):
    """PATCH 设置成功 → 返回 chapter；GET 验证 plan_json.revision_note 写入。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "请加强第二幕张力。"},
                )
            assert r.status_code == 200, r.text
            chapter = r.json()
            assert chapter["chapter_id"] == cid
            assert chapter["plan_json"]["revision_note"] == "请加强第二幕张力。"

            # 二次 GET 验证持久化
            r2 = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r2.status_code == 200
            assert r2.json()["plan_json"]["revision_note"] == "请加强第二幕张力。"

    asyncio.run(run())


def test_patch_revision_note_overwrites_existing(tmp_path: Path):
    """PATCH 覆盖已有 note。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r1 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "旧意见"},
                )
            assert r1.status_code == 200
            r2 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "新意见"},
                )
            assert r2.status_code == 200
            assert r2.json()["plan_json"]["revision_note"] == "新意见"

    asyncio.run(run())


def test_patch_revision_note_clear_with_empty_string(tmp_path: Path):
    """PATCH 空串 → revision_note 键消失。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            # 先写入
            r1 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "待清除意见"},
                )
            assert r1.status_code == 200
            assert r1.json()["plan_json"]["revision_note"] == "待清除意见"

            # 清除
            r2 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": ""},
                )
            assert r2.status_code == 200, r2.text
            assert "revision_note" not in r2.json()["plan_json"]

            # 二次 GET 验证持久化
            r3 = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r3.status_code == 200
            assert "revision_note" not in r3.json()["plan_json"]

    asyncio.run(run())


def test_patch_revision_note_clear_with_whitespace(tmp_path: Path):
    """PATCH 全空白字符串 → 视为清除。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r1 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "原意见"},
                )
            assert r1.status_code == 200

            r2 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "   \t  "},
                )
            assert r2.status_code == 200, r2.text
            assert "revision_note" not in r2.json()["plan_json"]

    asyncio.run(run())


def test_patch_revision_note_preserves_other_plan_keys(tmp_path: Path):
    """写入 note 不破坏 plan_json 其它键。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            # 创建时直接带 plan_json
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={
                    "number": 1,
                    "title": "C1",
                    "plan_json": {"key_beats": ["b1", "b2"], "extra": "value"},
                },
                )
            assert r.status_code == 201, r.text
            cid = r.json()["chapter_id"]

            r2 = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "意见"},
                )
            assert r2.status_code == 200, r2.text
            plan = r2.json()["plan_json"]
            assert plan["revision_note"] == "意见"
            assert plan["key_beats"] == ["b1", "b2"]
            assert plan["extra"] == "value"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 状态守卫（COMMITTED → 409，RELEASED → 409）
# ---------------------------------------------------------------------------


def test_patch_revision_note_committed_returns_409(tmp_path: Path):
    """chapter.status = COMMITTED → 409，detail 含当前 status。"""
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
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "意见"},
                )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "COMMITTED" in detail

    asyncio.run(run())


def test_patch_revision_note_released_returns_409(tmp_path: Path):
    """chapter.status = RELEASED → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            for nxt in ["REVIEWED", "COMMITTED", "RELEASED"]:
                r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"status": nxt})
                assert r.status_code == 200, r.text

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "意见"},
                )
            assert r.status_code == 409, r.text
            assert "RELEASED" in r.json()["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 章节不存在 → 404
# ---------------------------------------------------------------------------


def test_patch_revision_note_unknown_chapter_returns_404(tmp_path: Path):
    """chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "PATCH", "/api/chapters/ch_nope/revision-note",
                json={"note": "意见"},
                )
            assert r.status_code == 404, r.text
            assert "chapter" in r.json()["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 状态守卫（PLANNED / DRAFTED / REVIEWED 放行回归）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["PLANNED", "DRAFTED", "REVIEWED"])
def test_patch_revision_note_allowed_statuses(tmp_path: Path, status: str):
    """PLANNED / DRAFTED / REVIEWED 放行。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, status=status)

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}/revision-note",
                json={"note": "意见"},
                )
            assert r.status_code == 200, r.text
            assert r.json()["plan_json"]["revision_note"] == "意见"

    asyncio.run(run())