"""outline-batch API 端到端测试（POST /api/projects/{project_id}/outline-batch）。

零真实 LLM 调用：planner 节点全部走 mock_providers 脚本。

覆盖：
- 起 run ⇒ 终态 COMPLETED ⇒ 章节列表 +10（默认 chapter_count=10）；
  plan_json 为空 plan（不写该列 ⇒ DDL DEFAULT '{}'，留给 chapter-plan 生成槽）、
  outline_json 存 seed 原文（synopsis/title/number）；
- chapter_count=0 / 51 ⇒ 422（请求体约束）；
- 项目不存在 ⇒ 404；model_profile_id 不存在 ⇒ 400。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.db import get_connection

from ._shared import _create_app, _make_client


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _wait_run_terminal(app, run_id: str, *, timeout: float = 60.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        r = await _request(app, "GET", f"/api/runs/{run_id}")
        if r.status_code == 200:
            last = r.json()
            if last["status"] in ("COMPLETED", "PAUSED", "FAILED", "CANCELLED"):
                return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach terminal state within {timeout}s")


async def _make_project(app, name: str = "批量续规划 API 测试书") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _planner_script(start: int, count: int) -> list[str]:
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 API 链路",
            "synopsis": (
                f"API 端到端测试第{start + i}章梗概：主角沿官道南行，"
                "夜宿荒驿时撞见押运队伍的异动，尾随至渡口发现私货夹层，"
                "章末停在对方递出一枚旧令牌的瞬间。"
            ),
        }
        for i in range(count)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def test_outline_batch_api_end_to_end(tmp_path: Path):
    """默认 chapter_count=10：201 → COMPLETED → 章节列表 +10（编号 1..10、PLANNED）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/outline-batch",
                json={"mock_providers": {"outline_batch_planner": _planner_script(1, 10)}},
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            assert payload["run_id"]
            final = await _wait_run_terminal(app, payload["run_id"])
            assert final["status"] == "COMPLETED", final

            conn = get_connection(app.state.settings.db_path)
            try:
                rows = conn.execute(
                    "SELECT number, status, plan_json, outline_json FROM chapters "
                    "WHERE project_id = ? ORDER BY number ASC",
                    (pid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 10
            assert [row["number"] for row in rows] == list(range(1, 11))
            assert all(row["status"] == "PLANNED" for row in rows)
            # plan_json 不写 ⇒ DDL DEFAULT '{}' = 空 plan（「计划未生成」；
            # write 的 load_plan 会正确引导先跑 chapter-plan）。大纲只存 outline_json。
            for row in rows:
                assert json.loads(row["plan_json"]) == {}
                seed = json.loads(row["outline_json"])
                assert seed["number"] == row["number"]
                assert seed["title"]
                assert seed["synopsis"]

    asyncio.run(run())


def test_outline_batch_api_rejects_out_of_range_chapter_count(tmp_path: Path):
    """chapter_count=0 / 51 ⇒ 422（请求体 ge=1 le=50）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            for bad in (0, 51):
                r = await _request(
                    app,
                    "POST",
                    f"/api/projects/{pid}/outline-batch",
                    json={"chapter_count": bad},
                )
                assert r.status_code == 422, (bad, r.status_code, r.text)

    asyncio.run(run())


def test_outline_batch_api_unknown_project_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/projects/prj_nonexistent/outline-batch",
                json={},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())


def test_outline_batch_api_unknown_model_profile_400(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/outline-batch",
                json={"model_profile_id": "mp_nonexistent"},
            )
            assert r.status_code == 400, r.text
            assert "model_profile_id" in r.text

    asyncio.run(run())
