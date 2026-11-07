"""回滚必须清理该章的 ``chapter_summaries``（B-4，2026-11-08）。

缺陷形状：``chapter_summaries`` 全仓只有写（commit 后 INSERT）与读
（``builders_common/summaries.py`` 的近窗摘要），rollback 既不删也不失效。
而近窗摘要只按 ``project_id`` / ``chapter_no`` / ``volume_id`` 过滤，
**不看 commit 是否仍然有效** ⇒ 被撤销剧情的摘要继续作为「已发生剧情」
注入后续章的 director 装配，下游写作依据与 Canonical State 分叉。

修法：rollback 的逆清理段在同事务内 ``DELETE FROM chapter_summaries
WHERE chapter_id = ?``。

判别面（2 例）：
1. 回滚后该章摘要为 0 行（撤掉 DELETE 必红）；
2. 回滚**别的章**的 commit 不得误删本章摘要（防止把 WHERE 写成恒真）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.ids import new_id, now_iso

from ._shared import _evidence

# ----------------------------------------------------------------- helpers


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": "b4"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": f"第{number}章"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _make_character(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "林夕", "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


def _meta(delta_id: str, chapter_id: str, prev: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev,
        "created_by": "observer:v1",
        "created_at": "2026-08-30T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


async def _commit(app, pid: str, delta: dict) -> str:
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            "author_approval": {"approver": "user:local:test", "approved": True},
            "workflow_run_id": delta["workflow_run_id"],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["commit_id"]


def _insert_summary(db_path: str, pid: str, chapter_id: str, no: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO chapter_summaries (summary_id, project_id, chapter_id, "
            "chapter_no, summary, tail_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("sum"), pid, chapter_id, no, "被撤销剧情", "尾段", now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _count_summaries(db_path: str, chapter_id: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM chapter_summaries WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()[0]
        )
    finally:
        conn.close()


async def _rollback(app, commit_id: str) -> None:
    r = await _request(
        app, "POST", f"/api/commits/{commit_id}/rollback",
        json={"author_approval": {"approver": "user:local:test", "approved": True}},
    )
    assert r.status_code == 201, r.text


def _update_delta(delta_id: str, chap: str, cid: str, prev: int, before: str, after: str) -> dict:
    return {
        **_meta(delta_id, chap, prev),
        "character_changes": [
            {
                "change_id": f"cc_{delta_id}",
                "op": "update",
                "target_id": cid,
                "character_id": cid,
                "facet": "state",
                "field": "state.location",
                "before": before,
                "after": after,
                "confidence": 0.9,
                "evidence": _evidence(chap),
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def test_rollback_deletes_the_chapters_summaries(tmp_path: Path):
    """回滚后该章摘要必须为 0 行（撤掉 DELETE 必红）。"""
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap1 = await _make_chapter(app, pid, 1)
            cid = await _make_character(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap1}
            )
            assert r.status_code == 201, r.text

            commit_id = await _commit(
                app, pid, _update_delta("dlt_b4_1", chap1, cid, 1, "Unknown", "Forest")
            )
            _insert_summary(db_path, pid, chap1, 1)
            assert _count_summaries(db_path, chap1) == 1

            await _rollback(app, commit_id)
            assert _count_summaries(db_path, chap1) == 0

    asyncio.run(run())


def test_rollback_of_another_chapter_keeps_this_chapters_summaries(tmp_path: Path):
    """反向对照：回滚第 1 章不得删除第 2 章的摘要（防 WHERE 恒真）。"""
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path).db_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap1 = await _make_chapter(app, pid, 1)
            chap2 = await _make_chapter(app, pid, 2)
            cid = await _make_character(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap1}
            )
            assert r.status_code == 201, r.text

            commit_id = await _commit(
                app, pid, _update_delta("dlt_b4_2", chap1, cid, 1, "Unknown", "Forest")
            )
            _insert_summary(db_path, pid, chap1, 1)
            _insert_summary(db_path, pid, chap2, 2)

            await _rollback(app, commit_id)
            assert _count_summaries(db_path, chap1) == 0
            assert _count_summaries(db_path, chap2) == 1, "回滚第 1 章误删了第 2 章的摘要"

    asyncio.run(run())
