"""outline-batch API 端到端测试（POST /api/projects/{project_id}/outline-batch）。

零真实 LLM 调用：planner 节点全部走 mock_providers 脚本。

覆盖：
- 起 run ⇒ 终态 COMPLETED ⇒ 章节列表 +10（默认 chapter_count=10）；
  plan_json 为空 plan（不写该列 ⇒ DDL DEFAULT '{}'，留给 chapter-plan 生成槽）、
  outline_json = canonical 键映射结构（C-3：chapter_goal ← synopsis 原文，
  synopsis/title 原文并存）；
- **C-1/C-2 并发守卫**：同项目并发 8 个 POST ⇒ 恰 1 个 201 其余 409、章号无重复
  （内容级断言 DB）、409 detail 的 run_id 属本项目；项目 A 活跃时项目 B ⇒ 201
  （跨项目不拦，判别 C-1）；
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


async def _list_run_ids(app, project_id: str) -> set[str]:
    """列出该项目的 run_id 集合（M7 用例用）。"""
    r = await _request(app, "GET", f"/api/projects/{project_id}/runs")
    assert r.status_code == 200, r.text
    return {row["run_id"] for row in r.json()}


def _planner_script(start: int, count: int) -> list[str]:
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 API 链路",
            "synopsis": _synopsis(start + i),
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


def _synopsis(number: int) -> str:
    """第 number 章的 mock synopsis（脚本构造与断言共用同一份原文）。"""
    return (
        f"API 端到端测试第{number}章梗概：主角沿官道南行，"
        "夜宿荒驿时撞见押运队伍的异动，尾随至渡口发现私货夹层，"
        "章末停在对方递出一枚旧令牌的瞬间。"
    )


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
            # write 的 load_plan 会正确引导先跑 chapter-plan）。大纲只存 outline_json，
            # 且为 canonical 键映射结构（C-3）：chapter_goal ← synopsis 原文。
            for row in rows:
                assert json.loads(row["plan_json"]) == {}
                outline = json.loads(row["outline_json"])
                assert outline["chapter_goal"] == _synopsis(row["number"])
                assert outline["synopsis"] == _synopsis(row["number"])
                assert outline["title"]
            # run 行带 project_id 归属（0037 列，C-1 守卫的过滤依据）
            conn = get_connection(app.state.settings.db_path)
            try:
                run_row = conn.execute(
                    "SELECT project_id FROM workflow_runs WHERE run_id = ?",
                    (payload["run_id"],),
                ).fetchone()
            finally:
                conn.close()
            assert run_row is not None and run_row["project_id"] == pid

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


# ---------------------------------------------------------------------------
# C-1/C-2 并发守卫（2026-10-24）：项目级原子守卫 + project_id 过滤
# ---------------------------------------------------------------------------


def test_outline_batch_api_concurrent_posts_single_winner(tmp_path: Path):
    """C-2：同项目并发 8 个 POST ⇒ 恰 1 个 201 其余 409，且**章号无重复**。

    - 内容级断言 DB：chapters.number 集合无重复（chapters 无 (project_id, number)
      唯一索引 ⇒ 守卫失效时重复章号可发生，本断言抓得住）；
    - 409 detail 里的 run_id 必属**本项目**（守卫按 project_id 过滤的副产物）；
    - 兜底口径：若个别请求因「首个 run 已 COMPLETED」而串行成功（窗口极小、
      理论存在），则断言退化为「201 ≥ 1 且成功批次章号互不重叠」——重复章号
      在任何路径下都不允许。
    """
    app = _create_app(tmp_path)
    n = 8

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            script = _planner_script(1, 10)

            async def _post() -> httpx.Response:
                return await _request(
                    app,
                    "POST",
                    f"/api/projects/{pid}/outline-batch",
                    json={"mock_providers": {"outline_batch_planner": script}},
                )

            responses = await asyncio.gather(*[_post() for _ in range(n)])
            statuses = [r.status_code for r in responses]
            created = [r for r in responses if r.status_code == 201]
            conflicted = [r for r in responses if r.status_code == 409]

            assert len(created) >= 1, f"至少 1 个请求成功: {statuses}"
            assert len(created) + len(conflicted) == n, statuses
            # 主断言：并发窗口内其余请求全被原子守卫拦下（201 + 409 == 8 且
            # created == 1）。若环境导致串行成功（首个 run 过早 COMPLETED），
            # 上面的内容级断言仍保证零重复。
            assert len(created) == 1, (
                f"并发 8 POST 应恰 1 个 201（其余被原子守卫 409），got {statuses}"
            )

            # 409 detail 的 run_id 必属本项目：查该项目全部 run 行，detail 里的
            # run_id 必在其中（守卫带 project_id 过滤 ⇒ 不可能引用他项目 run）。
            conn = get_connection(app.state.settings.db_path)
            try:
                run_ids_here = {
                    row["run_id"]
                    for row in conn.execute(
                        "SELECT run_id FROM workflow_runs WHERE project_id = ?", (pid,)
                    ).fetchall()
                }
                numbers = [
                    int(row["number"])
                    for row in conn.execute(
                        "SELECT number FROM chapters WHERE project_id = ? ORDER BY number",
                        (pid,),
                    ).fetchall()
                ]
            finally:
                conn.close()
            for r in conflicted:
                detail = r.json().get("detail", "")
                assert "already has an active outline-batch run" in detail, detail
                # detail 形如 project 'prj_x' already has an active outline-batch
                # run (run_id='wfr_...'); ...——抽取 run_id 断言归属。
                assert "run_id=" in detail, detail
                rid = detail.split("run_id='", 1)[1].split("'", 1)[0]
                assert rid in run_ids_here, (
                    f"409 detail 的 run_id {rid} 不属于本项目 {pid}（跨项目泄漏）"
                )

            # 内容级硬断言：章号无重复（守卫生效 ⇒ 只有 1 批 1..10；
            # 串行合法路径 ⇒ 多批连续顺延，同样无重复）。
            assert len(numbers) == len(set(numbers)), f"并发产生了重复章号: {numbers}"
            assert numbers == list(range(1, len(numbers) + 1)), numbers

            # 等 201 的 run 走到终态（避免测试进程结束时 run 线程仍在写库）
            await _wait_run_terminal(app, created[0].json()["run_id"])

    asyncio.run(run())


def test_outline_batch_api_cross_project_not_blocked_and_detail_scoped(tmp_path: Path):
    """C-1 + detail 归属：项目 A 有 RUNNING 的 outline-batch run 时——

    - 项目 A 再 POST ⇒ 409，detail 的 run_id == 该活跃行（属本项目）；
    - 项目 B POST ⇒ **201**（跨项目不拦，反事实：守卫无 project_id 过滤时 B
      也会 409 ⇒ 本用例红），且 B 的 run 正常走完建章。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, name="跨项目守卫测试·A")
            pid_b = await _make_project(app, name="跨项目守卫测试·B")

            # 手工插一条项目 A 的 RUNNING outline-batch run（确定性活跃场景；
            # 带 project_id 归属 = 0037 守卫读的形态）。workflow 行与 run 行在
            # **同一连接同一事务**提交：engine.ensure_workflow 只插行不 commit
            # （生产路径在 _insert_run_row 的同一事务里收尾），跨连接直接用会
            # 因 close 回滚而 FK 失败。
            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                wf_row = conn.execute(
                    "SELECT workflow_id FROM workflows WHERE name = 'outline-batch'"
                ).fetchone()
                if wf_row is None:
                    conn.execute(
                        "INSERT INTO workflows (workflow_id, name, version, "
                        "definition_json, created_at, updated_at) "
                        "VALUES ('wf_ob_test', 'outline-batch', 'v1', '{}', 't', 't')"
                    )
                    wf_id = "wf_ob_test"
                else:
                    wf_id = wf_row["workflow_id"]
                conn.execute(
                    "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, "
                    "project_id, status, current_node, checkpoint_json, error, "
                    "retry_count, started_at, ended_at) "
                    "VALUES ('wfr_active_a', ?, NULL, ?, 'RUNNING', NULL, '{}', "
                    "NULL, 0, 't', NULL)",
                    (wf_id, pid_a),
                )
                conn.commit()
            finally:
                conn.close()

            # A：409 且 detail 指向本项目的活跃 run
            r_a = await _request(
                app, "POST", f"/api/projects/{pid_a}/outline-batch", json={}
            )
            assert r_a.status_code == 409, r_a.text
            detail_a = r_a.json()["detail"]
            assert "wfr_active_a" in detail_a, detail_a
            assert pid_a in detail_a, detail_a

            # B：201（跨项目不拦）→ 走到 COMPLETED → B 的章建成
            r_b = await _request(
                app,
                "POST",
                f"/api/projects/{pid_b}/outline-batch",
                json={
                    "chapter_count": 3,
                    "mock_providers": {"outline_batch_planner": _planner_script(1, 3)},
                },
            )
            assert r_b.status_code == 201, (
                f"项目 A 活跃时项目 B 必须放行（守卫按 project_id 过滤）: {r_b.text}"
            )
            run_b = r_b.json()
            final = await _wait_run_terminal(app, run_b["run_id"])
            assert final["status"] == "COMPLETED", final

            conn = get_connection(db_path)
            try:
                b_numbers = [
                    int(row["number"])
                    for row in conn.execute(
                        "SELECT number FROM chapters WHERE project_id = ? ORDER BY number",
                        (pid_b,),
                    ).fetchall()
                ]
                # A 只有手工行、无章节（A 的 POST 都被守卫拦下）
                a_rows = conn.execute(
                    "SELECT COUNT(*) AS n FROM chapters WHERE project_id = ?", (pid_a,)
                ).fetchone()
            finally:
                conn.close()
            assert b_numbers == [1, 2, 3], b_numbers
            assert a_rows["n"] == 0, "A 的起跑应全部被守卫拦截，不得建章"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# M7（2026-10-24）：项目级 run 的**列表可见性**按项目隔离
# ---------------------------------------------------------------------------


def test_outline_batch_run_is_scoped_to_its_project_end_to_end(tmp_path: Path):
    """⭐ M7 端到端：真实起跑路径产出的项目级 run 只出现在**本项目**的 run 列表里。

    直插行的版本在 ``tests/api/test_run_list_project_scope.py``；本用例走**真实
    链路**（POST outline-batch ⇒ 引擎写 0037 归属列 ⇒ GET /projects/{pid}/runs），
    证明归属列在生产路径上确实被写入 —— 否则列表过滤会退化成「全可见」。

    判别力：撤掉列表端的归属过滤 ⇒ A 的列表里会出现 B 的 run_id ⇒ 必红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "列表隔离测试·A")
            pid_b = await _make_project(app, "列表隔离测试·B")

            run_ids = {}
            for pid in (pid_a, pid_b):
                r = await _request(
                    app,
                    "POST",
                    f"/api/projects/{pid}/outline-batch",
                    json={
                        "chapter_count": 2,
                        "mock_providers": {
                            "outline_batch_planner": _planner_script(1, 2)
                        },
                    },
                )
                assert r.status_code == 201, r.text
                run_ids[pid] = r.json()["run_id"]

            listed_a = await _list_run_ids(app, pid_a)
            listed_b = await _list_run_ids(app, pid_b)

            assert run_ids[pid_a] in listed_a, "A 的 run 必须出现在 A 的列表里"
            assert run_ids[pid_b] not in listed_a, (
                "A 的 run 列表不得含 B 的项目级 run（M7 跨项目可见缺陷）"
            )
            assert run_ids[pid_b] in listed_b, "B 的 run 必须出现在 B 的列表里"
            assert run_ids[pid_a] not in listed_b, (
                "B 的 run 列表不得含 A 的项目级 run（M7 跨项目可见缺陷）"
            )

            # 等两个 run 走到终态（避免测试进程结束时后台线程仍在写库）
            for pid in (pid_a, pid_b):
                await _wait_run_terminal(app, run_ids[pid])

    asyncio.run(run())
