"""叙事弧光视图 REST API 集成测试。

覆盖：
- 200 + 完整结构（chapters / payoff / hooks / debts / alerts）；
- 404（project 不存在）；
- 数据缺失容错：项目无 chapters / 无 quality_report / 无 draft / 无 hooks 一律返回
  合法结构而非 500；
- plan_json 容错（非 JSON / 缺失 key_beats）；
- 自动发现：路由模块被 ``discover_routers`` 拾取。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.api.routers import discover_routers
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


async def _make_project(app, name: str = "弧光项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter_with_plan(
    app,
    pid: str,
    number: int,
    title: str,
    plan: dict,
) -> str:
    """直插 chapter（plan_json 任意 dict），绕开 ChapterService 校验。"""
    cid = new_id("ch")
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (
                cid,
                pid,
                number,
                title,
                json.dumps(plan, ensure_ascii=False),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 自动发现
# ---------------------------------------------------------------------------


def test_arc_router_is_discovered():
    routers = discover_routers()
    # 至少要存在 arc router
    assert any("/projects/{project_id}/arc" in [r.path for r in r.routes] for r in routers)


# ---------------------------------------------------------------------------
# 端点：正常路径
# ---------------------------------------------------------------------------


def test_arc_endpoint_returns_full_structure(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "完整弧光")
            # 3 章：第 1 章 payoff + pacing 80；第 2-3 章 charge_only + pacing 90
            c1 = await _make_chapter_with_plan(
                app, pid, 1, "开篇",
                {"key_beats": [{"purpose": "[payoff] 起手兑现"}]},
            )
            c2 = await _make_chapter_with_plan(
                app, pid, 2, "蓄力1",
                {"key_beats": [{"purpose": "[charge] 蓄力"}]},
            )
            c3 = await _make_chapter_with_plan(
                app, pid, 3, "蓄力2",
                {"key_beats": [{"purpose": "[charge] 蓄力"}]},
            )

            # 直插 quality_report
            conn = get_connection(app.state.settings.db_path)
            try:
                for cid, pacing in [(c1, 80), (c2, 90), (c3, 90)]:
                    conn.execute(
                        """
                        INSERT INTO quality_reports
                            (report_id, project_id, chapter_id, commit_id, run_id,
                             overall, scores_json, issues_json, created_at)
                        VALUES (?, ?, ?, NULL, NULL, ?, ?, '[]', ?)
                        """,
                        (
                            new_id("qr"),
                            pid,
                            cid,
                            pacing,
                            json.dumps({"overall": pacing, "pacing": pacing}, ensure_ascii=False),
                            now_iso(),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            # 顶层 schema
            assert set(body.keys()) >= {
                "project_id",
                "generated_at",
                "chapters",
                "payoff",
                "hooks",
                "debts",
                "alerts",
            }
            assert body["project_id"] == pid
            # chapters 长度与章号
            assert len(body["chapters"]) == 3
            assert [ch["number"] for ch in body["chapters"]] == [1, 2, 3]
            # 第一章 has_payoff_beat=True，其余 False
            assert body["chapters"][0]["has_payoff_beat"] is True
            assert body["chapters"][0]["charge_beat"] is False
            assert body["chapters"][1]["charge_beat"] is True
            # payoff 段
            payoff = body["payoff"]
            assert payoff["total"] == 1
            assert payoff["last_payoff_chapter"] == 1
            # 蓄力连击 = 2（不到阈值 3，不应触发 fail）
            assert payoff["charge_streak"] == 2
            assert payoff["max_charge_streak"] == 2
            # hooks / debts 空项目应为 0
            assert body["hooks"] == {"open": 0, "resolved": 0, "overdue": 0}
            assert body["debts"] == {"open": 0, "paid": 0}
            # alerts：尾部连击 2 < 3 不触发 charge_streak；
            # 最近 5 章 payoff 占比 1/3 ≈ 33% < 40% → low_payoff_density
            # pacing 都不低于 50 → 无 low_pacing_chapter
            codes = [a["code"] for a in body["alerts"]]
            assert "charge_streak_exceeded" not in codes
            assert "low_payoff_density" in codes
            assert "low_pacing_chapter" not in codes

    asyncio.run(run())


def test_arc_endpoint_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_nope/arc")
            assert r.status_code == 404, r.text
            detail = r.json()["detail"]
            assert "not found" in detail.lower()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 端点：数据缺失容错
# ---------------------------------------------------------------------------


def test_arc_endpoint_empty_project_no_chapters(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "空项目")
            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapters"] == []
            assert body["payoff"]["total"] == 0
            assert body["payoff"]["charge_streak"] == 0
            assert body["hooks"] == {"open": 0, "resolved": 0, "overdue": 0}
            assert body["debts"] == {"open": 0, "paid": 0}
            assert body["alerts"] == []

    asyncio.run(run())


def test_arc_endpoint_chapter_without_quality_or_draft(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "章节无报告")
            # chapter 但不插 draft / quality_report
            await _make_chapter_with_plan(
                app, pid, 1, "空章", {"key_beats": []}
            )
            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            ch = r.json()["chapters"][0]
            assert ch["overall"] is None
            assert ch["pacing"] is None
            assert ch["prose_chars"] is None

    asyncio.run(run())


def test_arc_endpoint_plan_json_invalid_safe(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "烂 plan_json")
            # 直插 plan_json 为非 JSON 字符串
            cid = new_id("ch")
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO chapters
                        (chapter_id, project_id, number, title, plan_json,
                         status, visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
                    """,
                    (cid, pid, 1, "烂", "{not valid json", now_iso(), now_iso()),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            ch = r.json()["chapters"][0]
            assert ch["has_payoff_beat"] is False
            assert ch["charge_beat"] is False

    asyncio.run(run())


def test_arc_endpoint_alerts_charge_streak_fail(tmp_path: Path):
    """尾部 3 章 charge_only → 触发 charge_streak_exceeded (fail)。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "蓄力超限")
            for i in range(1, 5):
                await _make_chapter_with_plan(
                    app, pid, i, str(i),
                    {"key_beats": [{"purpose": "[charge] 蓄力"}]},
                )

            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            codes = [(a["code"], a["level"]) for a in body["alerts"]]
            assert ("charge_streak_exceeded", "fail") in codes

    asyncio.run(run())


def test_arc_endpoint_alerts_foreshadow_overdue(tmp_path: Path):
    """有逾期伏笔 → 触发 foreshadow_overdue (warn)。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "逾期伏笔")
            # 引入章 chapter 1（章号 1）；再写章 50 → 距 50 章，远超阈值 30
            cid = await _make_chapter_with_plan(
                app, pid, 1, "intro", {"key_beats": []}
            )
            for i in range(2, 51):
                await _make_chapter_with_plan(
                    app, pid, i, str(i), {"key_beats": []}
                )

            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO hooks
                        (hook_id, project_id, name, introduced_chapter_id, status,
                         importance, expected_payoff_chapter_id, payoff_chapter_id,
                         visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'OPEN', 0.9, NULL, NULL,
                            'RESTRICTED', NULL, ?, ?)
                    """,
                    (new_id("hook"), pid, "久远伏笔", cid, now_iso(), now_iso()),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["hooks"]["overdue"] >= 1
            codes = [a["code"] for a in body["alerts"]]
            assert "foreshadow_overdue" in codes

    asyncio.run(run())
