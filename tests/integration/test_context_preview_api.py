"""/api/chapters/{cid}/context-preview 集成测试（Sprint 13 下半）。

覆盖：
- 200：分层结构齐全（L0/L1/L2）、token > 0、含 character/location/hook/debt 条目；
- 404：chapter / project 不存在；
- **零 LLM**：调用前后 ai_call_logs COUNT 不变。
"""

from __future__ import annotations

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


async def _make_project(app, name: str = "preview-test") -> str:
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


def _seed_minimal_data(db_path: Path, pid: str, cid: str) -> dict[str, str]:
    """直接插库一份 character + location + hook + debt，便于断言 items。"""
    now = now_iso()
    char_id = new_id("char")
    loc_id = new_id("loc")
    hook_id = new_id("hk")
    debt_id = new_id("dbt")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO characters (character_id, project_id, name, role,
                                       core_json, visibility, who_knows,
                                       created_at, updated_at)
               VALUES (?, ?, '测试主角', 'protagonist', '{}', 'VISIBLE', NULL, ?, ?)""",
            (char_id, pid, now, now),
        )
        conn.execute(
            """INSERT INTO locations (location_id, project_id, name, statement,
                                      data_json, visibility, who_knows,
                                      created_at, updated_at)
               VALUES (?, ?, '测试王城', 'desc', '{}', 'VISIBLE', NULL, ?, ?)""",
            (loc_id, pid, now, now),
        )
        conn.execute(
            """INSERT INTO hooks (hook_id, project_id, name, status, importance,
                                  visibility, who_knows, created_at, updated_at)
               VALUES (?, ?, '测试伏笔', 'OPEN', 0.8, 'VISIBLE', NULL, ?, ?)""",
            (hook_id, pid, now, now),
        )
        conn.execute(
            """INSERT INTO narrative_debts (debt_id, project_id, description, severity,
                                            status, visibility, who_knows,
                                            created_at, updated_at)
               VALUES (?, ?, '测试待还债', 0.7, 'open', 'VISIBLE', NULL, ?, ?)""",
            (debt_id, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"character_id": char_id, "location_id": loc_id,
            "hook_id": hook_id, "debt_id": debt_id}


def _ai_log_count(db_path: Path) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM ai_call_logs").fetchone()
        return int(row["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_context_preview_happy_path(tmp_path: Path):
    """200 + 分层结构齐全 + 含 character/location/hook/debt 条目。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            seeded = _seed_minimal_data(app.state.settings.db_path, pid, cid)

            r = await _request(app, "GET", f"/api/chapters/{cid}/context-preview")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["project_id"] == pid
            assert sorted(body["agents"]) == ["director", "observer", "writer"]
            assert len(body["layers"]) == 3
            layer_ids = [layer["id"] for layer in body["layers"]]
            assert layer_ids == ["L0", "L1", "L2"]
            assert body["token_budget"] == 8000
            assert body["within_budget"] == (body["total_tokens"] <= 8000)
            for layer in body["layers"]:
                assert "token_estimate" in layer
                assert layer["token_estimate"] >= 1
                assert isinstance(layer["items"], list)
                assert isinstance(layer["truncated"], bool)

            # L1 items 必须含 4 类
            l1 = next(layer for layer in body["layers"] if layer["id"] == "L1")
            kinds = {it["kind"] for it in l1["items"]}
            assert {"character", "location", "hook", "debt"} <= kinds
            by_kind_id = {(it["kind"], it["id"]): it for it in l1["items"]}
            assert ("character", seeded["character_id"]) in by_kind_id
            assert ("location", seeded["location_id"]) in by_kind_id
            assert ("hook", seeded["hook_id"]) in by_kind_id
            assert ("debt", seeded["debt_id"]) in by_kind_id
            return True

    assert asyncio.run(run())


def test_context_preview_returns_404_when_chapter_missing(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/chapters/ch_nope/context-preview")
            assert r.status_code == 404, r.text
            return True

    assert asyncio.run(run())


def test_context_preview_returns_404_when_project_missing(tmp_path: Path):
    """临时关闭 FK 后插 chapter 行指向不存在的 project_id → builder 抛 ValueError → 404。

    实际生产中 FK 会拦截 orphan chapter 的写入，但 preview builder 本身对
    project 不存在抛 ValueError 的契约必须仍成立 —— 我们用 PRAGMA 临时绕过
    FK 以覆盖这条路径（与 FK 触发器行为正交）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            db_path = app.state.settings.db_path
            cid = new_id("ch")
            pid_orphan = "prj_orphan_no_such_project"
            now = now_iso()
            conn = get_connection(db_path)
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute(
                    """INSERT INTO chapters (chapter_id, project_id, number, title,
                                              plan_json, status, visibility,
                                              who_knows, created_at, updated_at)
                       VALUES (?, ?, 1, 'T', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)""",
                    (cid, pid_orphan, now, now),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/chapters/{cid}/context-preview")
            assert r.status_code == 404, r.text
            return True

    assert asyncio.run(run())


def test_context_preview_does_not_call_llm(tmp_path: Path):
    """硬断言：调用前后 ai_call_logs COUNT 不变（证明 dry-run 不触发 LLM）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            _seed_minimal_data(app.state.settings.db_path, pid, cid)

            db_path = app.state.settings.db_path
            before = _ai_log_count(db_path)
            r = await _request(app, "GET", f"/api/chapters/{cid}/context-preview")
            assert r.status_code == 200, r.text
            after = _ai_log_count(db_path)

            assert after == before, (
                f"dry-run 不应触发 ai_call_logs 写入；before={before} after={after}"
            )
            return True

    assert asyncio.run(run())


def test_context_preview_token_budget_invariant(tmp_path: Path):
    """total_tokens == sum(layers[].token_estimate); within_budget 与之对应。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)
            _seed_minimal_data(app.state.settings.db_path, pid, cid)

            r = await _request(app, "GET", f"/api/chapters/{cid}/context-preview")
            assert r.status_code == 200
            body = r.json()
            expected = sum(layer["token_estimate"] for layer in body["layers"])
            assert body["total_tokens"] == expected
            assert body["within_budget"] == (body["total_tokens"] <= body["token_budget"])
            return True

    assert asyncio.run(run())


def test_context_preview_list_summary_field_shape(tmp_path: Path):
    """契约：响应含 chapter_id/project_id/agents/layers/total_tokens/token_budget/within_budget。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, number=1)

            r = await _request(app, "GET", f"/api/chapters/{cid}/context-preview")
            assert r.status_code == 200
            body = r.json()
            required = {
                "chapter_id", "project_id", "agents", "layers",
                "total_tokens", "token_budget", "within_budget",
            }
            assert required <= set(body.keys())
            # layer 必须含 4 个字段
            for layer in body["layers"]:
                assert {"id", "label", "token_estimate", "items", "truncated"} <= set(layer.keys())
            return True

    assert asyncio.run(run())


import asyncio  # noqa: E402  末尾导入便于上面 asyncio.run 调用
