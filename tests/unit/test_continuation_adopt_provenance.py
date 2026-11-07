"""continuation 采纳必须保留 AI 出处（B-5，2026-11-08）。

缺陷形状：``adopt_continuation`` 把模型候选拼进正文后调 ``create_draft``，
而后者契约写死 ``created_by="human"`` ⇒ 模型产出的字被算成人工产出
（``classify_created_by`` 按 ``cb == "human"`` 归 human 桶），Q8 人机占比
与 q8-export 同步被污染，``model_id`` / ``prompt_version`` 也恒 NULL。

修法：``create_draft`` 增加可选出处参数（缺省逐字不变），continuation 传
``created_by="agent:continuation"``。

判别面（3 例）：
1. adopt 落库的 draft ``created_by`` 以 ``agent:`` 开头（撤掉传参必红）；
2. 该出处被 Q8 归到 ai 桶（钉住「不只是改了个字符串」）；
3. 反向对照：不传参数的 ``create_draft`` 仍是 ``"human"``（防缺省被改坏）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.ids import new_id, now_iso
from packages.core.quality.service import classify_created_by
from packages.domain.chapter.service import ChapterService


def _insert_chapter(db_path: str, project_id: str, status: str = "DRAFTED") -> str:
    """直插一章（绕开状态机）：adopt 要求 status ∈ {DRAFTED, REVIEWED}。"""
    cid = new_id("ch")
    now = now_iso()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', ?, 'VISIBLE', ?, ?)",
            (cid, project_id, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _latest_created_by(db_path: str, chapter_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT created_by FROM drafts WHERE chapter_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
        assert row is not None, "没有落库的 draft"
        return str(row[0])
    finally:
        conn.close()


def test_adopted_continuation_is_recorded_as_agent_not_human(tmp_path: Path):
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "b5"})
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]
            cid = _insert_chapter(db_path, pid, status="DRAFTED")

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "模型续写的正文片段。"},
            )
            assert r.status_code == 200, r.text

            created_by = _latest_created_by(db_path, cid)
            assert created_by.startswith("agent:"), (
                f"采纳的模型正文被记成了 {created_by!r}（B-5 复现）"
            )
            # 钉住「不只是改了个字符串」：该出处必须被 Q8 归到 ai 桶
            assert classify_created_by(created_by) == "ai"

    asyncio.run(run())


def test_plain_create_draft_still_defaults_to_human(tmp_path: Path):
    """反向对照：不传出处参数的 ``create_draft`` 仍是 ``"human"``（人工改稿入口）。"""
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "b5b"})
            pid = r.json()["project_id"]
            cid = _insert_chapter(db_path, pid, status="DRAFTED")
            ChapterService(db_path).create_draft(cid, "作者手改的正文。")
            assert _latest_created_by(db_path, cid) == "human"

    asyncio.run(run())
