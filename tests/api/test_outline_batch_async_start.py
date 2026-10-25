"""M11（2026-10-24）：outline-batch 起跑端点**异步化**。

缺陷：``POST /api/projects/{project_id}/outline-batch`` 在**请求线程**里同步跑完
整个工作流（assemble→planner→persist）⇒ 真实 LLM 时 HTTP 挂数分钟。

修法（``packages/core/api/routers/workflows/control.py``）：``_start_outline_batch``
改用 ``engine.start_with_nodes_async``——插完 run 行立刻返回，三个节点在后台
线程推进（与章节级起跑 ``_start_workflow_core`` 同一形态）。响应恒为
``status="RUNNING"``，真终态由客户端轮询 ``GET /api/runs/{run_id}`` 取。

判别力（本文件的三条用例各自独立）：

1. **端点不等工作流跑完**：把 planner 节点**确定性阻塞**在一个 Event 上
   （只 monkeypatch 测试侧的 ``pipeline.run_agent``，生产代码一字不动）⇒
   同步实现下 POST 必然等到放行才返回（≥ 放行上限）⇒ 用例红。
2. **响应形态**：``status`` 必须是 ``"RUNNING"``（同步实现回的是真终态
   ``"COMPLETED"``）⇒ 用例红。
3. **守卫 409 语义保留**：第一个 run 卡在 RUNNING 时，第二个 POST 必须 409
   ——``project_level_guard`` 的「守卫 SELECT + run INSERT」在
   ``engine._insert_run_row`` 的 BEGIN IMMEDIATE 事务里、且在**调用线程**里
   执行 ⇒ 异步化不改变它。

⚠️ 零真实 LLM 调用：planner 节点全部走 ``mock_providers`` 脚本。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from packages.core.db import get_connection
from packages.workflows.outline_batch import pipeline as pipeline_module

from ._shared import _create_app, _request
from .test_outline_batch_api import _make_project, _planner_script, _wait_run_terminal


class _BlockingPlanner:
    """把 outline-batch 的 planner 节点卡在入口（造出确定性的「工作流还在跑」窗口）。

    只 monkeypatch **测试侧可见**的 ``pipeline.run_agent``（模块级名字，调用时
    解析 ⇒ 打桩生效）；``packages/workflows/outline_batch/pipeline.py`` 与
    ``packages/core/workflow_runtime/engine.py`` 生产代码一字不动。

    放行机制：``release`` 事件 + 一个 ``release_timeout`` 兜底定时器——兜底
    存在的唯一目的是**不让同步实现把测试挂死**（同步实现下 POST 会一直等
    release）；届时耗时已超阈值 ⇒ 用例照样红。
    """

    def __init__(self, release_timeout: float = 8.0):
        self.entered = threading.Event()
        self.release = threading.Event()
        self._release_timeout = release_timeout
        self._timer: threading.Timer | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_run_agent = pipeline_module.run_agent

        def _blocking_run_agent(*args, **kwargs):
            self.entered.set()
            self.release.wait(timeout=30.0)
            return real_run_agent(*args, **kwargs)

        monkeypatch.setattr(pipeline_module, "run_agent", _blocking_run_agent)
        self._timer = threading.Timer(self._release_timeout, self.release.set)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        self.release.set()
        if self._timer is not None:
            self._timer.cancel()


def test_outline_batch_post_returns_before_workflow_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """⭐ 主判别：POST **不等**工作流跑完，且响应恒为「已受理」形态。

    同步实现下：POST 会挂在 planner 阻塞点直到兜底定时器放行（≥ 8s）⇒
    ``elapsed`` 超阈值 ⇒ 红；且响应 ``status`` 会是真终态 ``"COMPLETED"``
    ⇒ 「响应形态」那条断言也红（两条独立的判别）。
    """
    app = _create_app(tmp_path)
    blocker = _BlockingPlanner(release_timeout=8.0)
    blocker.install(monkeypatch)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "异步起跑测试书")
            t0 = time.monotonic()
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/outline-batch",
                json={
                    "chapter_count": 3,
                    "mock_providers": {
                        "outline_batch_planner": _planner_script(1, 3)
                    },
                },
            )
            elapsed = time.monotonic() - t0
            try:
                assert r.status_code == 201, r.text
                payload = r.json()
                # 响应形态：已受理（RUNNING + current_node=null），不是真终态。
                assert payload["status"] == "RUNNING", (
                    f"起跑响应 status 应恒为 RUNNING（异步语义），实际 {payload}"
                )
                assert payload["current_node"] is None, payload
                # 关键判别：HTTP 没有等工作流跑完（同步实现下这里 ≥ 8s）。
                assert elapsed < 4.0, (
                    f"POST 耗时 {elapsed:.2f}s ⇒ 端点仍在同步等待工作流（M11 未修）"
                )
                # 工作流确实在后台推进（planner 已进入）。
                assert blocker.entered.wait(timeout=30.0), "planner 节点未进入"
                run_id = payload["run_id"]
            finally:
                blocker.release.set()

            # 后台线程照常跑完：终态 COMPLETED + 章节建成（异步化没把工作流改没）。
            final = await _wait_run_terminal(app, run_id)
            assert final["status"] == "COMPLETED", final
            conn = get_connection(app.state.settings.db_path)
            try:
                numbers = [
                    int(row["number"])
                    for row in conn.execute(
                        "SELECT number FROM chapters WHERE project_id = ? ORDER BY number",
                        (pid,),
                    ).fetchall()
                ]
            finally:
                conn.close()
            assert numbers == [1, 2, 3], numbers

    asyncio.run(run())


def test_outline_batch_conflict_still_409_while_first_run_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """守卫 409 语义保留：第一个 run 卡在 RUNNING 时，第二个 POST 必须 409。

    这是 M11 的硬约束（a）：``project_level_guard`` 的 BEGIN IMMEDIATE 原子
    守卫在**调用线程**里执行（后台线程只跑节点）⇒ 异步化不改并发语义。
    撤掉 ``project_level_guard=True`` ⇒ 本用例红（第二个 POST 会拿到 201）。
    """
    app = _create_app(tmp_path)
    blocker = _BlockingPlanner(release_timeout=20.0)
    blocker.install(monkeypatch)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "异步守卫测试书")
            payload_body = {
                "chapter_count": 2,
                "mock_providers": {"outline_batch_planner": _planner_script(1, 2)},
            }
            r1 = await _request(
                app, "POST", f"/api/projects/{pid}/outline-batch", json=payload_body
            )
            assert r1.status_code == 201, r1.text
            run_id_1 = r1.json()["run_id"]
            # 等 planner 进入 ⇒ 第一个 run 确定处于 RUNNING（run 行早已提交）。
            assert blocker.entered.wait(timeout=30.0), "planner 节点未进入"
            try:
                r2 = await _request(
                    app, "POST", f"/api/projects/{pid}/outline-batch", json=payload_body
                )
                assert r2.status_code == 409, (
                    f"同项目已有活跃 outline-batch run 时第二个 POST 必须 409，实际 "
                    f"{r2.status_code}: {r2.text}"
                )
                detail = r2.json()["detail"]
                assert "already has an active outline-batch run" in detail, detail
                assert f"run_id='{run_id_1}'" in detail, (
                    f"409 detail 必须指向本项目的活跃 run（{run_id_1}），实际 {detail}"
                )
            finally:
                blocker.release.set()

            final = await _wait_run_terminal(app, run_id_1)
            assert final["status"] == "COMPLETED", final

    asyncio.run(run())


def test_outline_batch_async_run_row_is_owned_by_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """异步起跑仍写归属列（0037）：run 行的 project_id == 请求的项目。

    这是 M7 与 M11 的交叉点：归属列既是项目级并发守卫的过滤依据，也是 run
    列表项目隔离的依据 ⇒ 异步化不得把归属写丢（否则列表会退回「全可见」）。
    """
    app = _create_app(tmp_path)
    blocker = _BlockingPlanner(release_timeout=20.0)
    blocker.install(monkeypatch)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "异步归属测试书")
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/outline-batch",
                json={
                    "chapter_count": 2,
                    "mock_providers": {"outline_batch_planner": _planner_script(1, 2)},
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            assert blocker.entered.wait(timeout=30.0), "planner 节点未进入"
            try:
                conn = get_connection(app.state.settings.db_path)
                try:
                    row = conn.execute(
                        "SELECT project_id, status FROM workflow_runs WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                finally:
                    conn.close()
                assert row is not None
                assert row["project_id"] == pid, (
                    f"run 行归属列必须是请求的项目 {pid}，实际 {row['project_id']!r}"
                )
                assert row["status"] == "RUNNING", (
                    f"起跑响应返回时 run 行应处于 RUNNING，实际 {row['status']!r}"
                )
            finally:
                blocker.release.set()

            final = await _wait_run_terminal(app, run_id)
            assert final["status"] == "COMPLETED", final

    asyncio.run(run())
