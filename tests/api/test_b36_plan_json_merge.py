"""``PATCH /api/chapters/{id}`` 的 ``plan_json`` **保留合并**判别（批次三十六 C-22）。

缺陷形状：``plan_json`` 曾是**整体替换** ⇒ 作者在「章节计划」面板改一个字，
就会把后端写下的三个门禁键一并静默清掉——

- ``revision_note``（门禁改稿建议，``chapter_commit/gate.py`` / review 驳回写）
- ``gate_blocked``（阻断原因与 rule_ids，``gate.py::_persist_gate_block`` 写）
- ``suggested_hashes``（评审驳回建议，``chapter_review/pipeline.py`` 写）

修法（Lead 拍板）：``ChapterService.update`` 的 ``plan_json`` 分支改 read-modify-write
保留合并——请求体覆盖普通键；三个门禁键在请求体未显式提供时由 DB 现值保留；
请求体**显式**带（含显式 ``null``）⇒ 以请求体为准；``plan_json`` 本身显式 ``null``
⇒ 按「门禁键不可经通用 PATCH 清除」的拍板语义，仍从现值补回（专用清除入口另议）。

判别面（5 例，全部断言 **DB 实际落库值**，不打响应回显）：
1. PATCH 不带门禁键 ⇒ 三键逐字保留、普通键被替换（整体替换语义只对普通键成立）；
2. PATCH 显式带 ``revision_note`` ⇒ 以请求体为准；
3. PATCH 显式带 ``gate_blocked: null`` ⇒ 以请求体为准（变 null）；
4. PATCH ``plan_json: null`` ⇒ 门禁键仍补回（普通键清空）；
5. 空 plan（``{}``，DDL 默认态）首建 ⇒ 原样写入，不凭空加门禁键。
   （``plan_json`` 列 NOT NULL ⇒ DB 层无 NULL 形态，"无现值"分支由空 dict/损坏 JSON 承接。）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from tests._db_fixtures import migrated_db

_GATE_KEYS = ("revision_note", "gate_blocked", "suggested_hashes")


def _create_app(tmp_path: Path):
    migrated_db(tmp_path)  # 每例新库 + 全量迁移（tests/_db_fixtures 单点）
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": "c22 合并"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _insert_chapter(db_path: str, pid: str, plan_json: str | None) -> str:
    """直插 PLANNED 章（绕开状态机），``plan_json`` 由调用方给死。"""
    cid = new_id("ch")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, 'C22', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, plan_json, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _read_plan_json(db_path: str, chapter_id: str) -> dict:
    """直读 DB（不打响应回显——判别点必须落在落库值上）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return json.loads(row["plan_json"])


def _seeded(tmp_path: Path, plan_json: str | None):
    """建 app + 项目 + 直插章；返回 (app, pid, cid, db_path)。"""
    app = _create_app(tmp_path)
    db_path = str(Path(tmp_path) / "novelos.db")

    async def run():
        pid = await _make_project(app)
        return pid, _insert_chapter(db_path, pid, plan_json)

    pid, cid = asyncio.run(run())
    return app, pid, cid, db_path


_SEED = {
    "chapter_goal": "旧目标",
    "revision_note": "改稿建议 v1",
    "gate_blocked": {"mode": "enforce", "gate": "block", "rule_ids": ["W-LEN"]},
    "suggested_hashes": ["abc123"],
}


def test_patch_without_gate_keys_preserves_gate_keys(tmp_path: Path):
    app, _pid, cid, db_path = _seeded(tmp_path, json.dumps(_SEED, ensure_ascii=False))

    async def run():
        return await _request(
            app, "PATCH", f"/api/chapters/{cid}",
            json={"plan_json": {"chapter_goal": "新目标"}},
        )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    plan = _read_plan_json(db_path, cid)
    assert plan["chapter_goal"] == "新目标"  # 普通键被替换
    assert plan["revision_note"] == "改稿建议 v1"  # 三门禁键逐字保留
    assert plan["gate_blocked"] == _SEED["gate_blocked"]
    assert plan["suggested_hashes"] == ["abc123"]


def test_patch_with_explicit_revision_note_wins(tmp_path: Path):
    app, _pid, cid, db_path = _seeded(tmp_path, json.dumps(_SEED, ensure_ascii=False))

    async def run():
        return await _request(
            app, "PATCH", f"/api/chapters/{cid}",
            json={"plan_json": {"chapter_goal": "新目标", "revision_note": "作者改写"}},
        )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    plan = _read_plan_json(db_path, cid)
    assert plan["revision_note"] == "作者改写"  # 显式带 ⇒ 请求体为准
    assert plan["gate_blocked"] == _SEED["gate_blocked"]  # 未带的键仍保留
    assert plan["suggested_hashes"] == ["abc123"]


def test_patch_with_explicit_null_gate_key_wins(tmp_path: Path):
    app, _pid, cid, db_path = _seeded(tmp_path, json.dumps(_SEED, ensure_ascii=False))

    async def run():
        return await _request(
            app, "PATCH", f"/api/chapters/{cid}",
            json={"plan_json": {"chapter_goal": "新目标", "gate_blocked": None}},
        )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    plan = _read_plan_json(db_path, cid)
    assert plan["gate_blocked"] is None  # 显式置 null ⇒ 以请求体为准
    assert plan["revision_note"] == "改稿建议 v1"
    assert plan["suggested_hashes"] == ["abc123"]


def test_patch_plan_json_null_still_backfills_gate_keys(tmp_path: Path):
    app, _pid, cid, db_path = _seeded(tmp_path, json.dumps(_SEED, ensure_ascii=False))

    async def run():
        return await _request(
            app, "PATCH", f"/api/chapters/{cid}", json={"plan_json": None},
        )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    plan = _read_plan_json(db_path, cid)
    # 门禁键不可经通用 PATCH 清除（拍板语义）：普通键清空、门禁键从现值补回
    assert plan == {
        "revision_note": "改稿建议 v1",
        "gate_blocked": _SEED["gate_blocked"],
        "suggested_hashes": ["abc123"],
    }


def test_first_patch_on_empty_plan_writes_incoming_verbatim(tmp_path: Path):
    """``plan_json`` 列是 ``NOT NULL DEFAULT '{}'``（0001）⇒ DB 层无 NULL 形态；
    「首建/空 plan」的真实形态是 ``{}`` ⇒ PATCH 后应原样写入、不凭空加门禁键。"""
    app, _pid, cid, db_path = _seeded(tmp_path, "{}")

    async def run():
        return await _request(
            app, "PATCH", f"/api/chapters/{cid}",
            json={"plan_json": {"chapter_goal": "首建"}},
        )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    plan = _read_plan_json(db_path, cid)
    assert plan == {"chapter_goal": "首建"}  # 无现值 ⇒ 不凭空加门禁键
