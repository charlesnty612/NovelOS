"""M7（2026-10-24）：``GET /projects/{project_id}/runs`` 的**项目隔离**。

缺陷：数据层 ``workflow_runtime.runs.list_runs`` 的 SQL 口径是
「chapter→project 反查 **OR** ``chapter_id IS NULL``」——后半句让**所有**项目的
项目级 run（project-init / outline-batch 等**无 chapter** 的 run）出现在
**每个**项目的列表里（跨项目可见）。

修法（``packages/core/api/routers/workflows/runs.py``）：按 0037 的归属列
``workflow_runs.project_id`` 收口；归属列为空的行**判不出来** ⇒ 保持
全项目可见（语义与成因在被测模块的注释里显式登记）。

⚠️ **口径订正（F-1，2026-10-25）**：NULL 归属行有两条**成因不同**的路径——
① project-init「新建项目」模式**建项时恒为 NULL**（项目此刻还不存在），
**2026-10-25 起建项成功后回写归属** ⇒ 该路径不再产生 NULL 归属行（端到端判别见
``tests/workflow/test_project_init.py::test_project_init_new_project_claims_run_ownership``）；
② **真正判不出来的存量行**（checkpoint 无 project_id）⇒ 保持全项目可见。
本文件钉的是 ②。

本文件的用例**直插 workflow_runs 行**（不走工作流）：M7 的判别点是
「列表按项目过滤」，与工作流执行无关；直插行让「归属列的值」成为唯一变量。
真实链路的端到端版在 ``tests/api/test_outline_batch_api.py``
（``test_outline_batch_run_is_scoped_to_its_project_end_to_end``）。

判别力：撤掉过滤 ⇒ 用例 1/2 必红（A 的列表里会出现 B 的 run_id）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from packages.core.api.routers.workflows.runs import _run_visible_in_project
from packages.core.db import get_connection
from packages.domain.chapter.service import ChapterService

from ._shared import _create_app, _request


async def _make_project(app, name: str) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _list_run_ids(app, project_id: str) -> set[str]:
    r = await _request(app, "GET", f"/api/projects/{project_id}/runs")
    assert r.status_code == 200, r.text
    return {row["run_id"] for row in r.json()}


def _insert_chapter(db_path, chapter_id: str, project_id: str, number: int) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 't', 't')",
            (chapter_id, project_id, number, f"第{number}章"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_run_row(
    db_path,
    run_id: str,
    *,
    workflow_name: str = "outline-batch",
    chapter_id: str | None = None,
    project_id: str | None = None,
    status: str = "COMPLETED",
) -> None:
    """直插一条 workflow_runs 行（``chapter_id=None`` 即项目级 run）。

    ``project_id=None`` 复刻**判不出归属的存量 NULL 行**（checkpoint 里没有
    project_id）——那一档的可见性语义被单独钉住。
    """
    conn = get_connection(db_path)
    try:
        wf = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = ?", (workflow_name,)
        ).fetchone()
        if wf is None:
            conn.execute(
                "INSERT INTO workflows (workflow_id, name, version, definition_json, "
                "created_at, updated_at) VALUES (?, ?, 'v1', '{}', 't', 't')",
                (f"wf_{run_id}", workflow_name),
            )
            wf_id = f"wf_{run_id}"
        else:
            wf_id = wf["workflow_id"]
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, project_id, "
            "status, current_node, checkpoint_json, error, retry_count, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, NULL, '{}', NULL, 0, 't', 't')",
            (run_id, wf_id, chapter_id, project_id, status),
        )
        conn.commit()
    finally:
        conn.close()


def test_project_level_run_is_scoped_to_its_own_project(tmp_path: Path):
    """项目级 run（无 chapter）只出现在**本项目**的列表里。

    ⭐ 主判别用例：撤掉过滤 ⇒ A 的列表里会出现 B 的 run_id ⇒ 必红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "项目隔离测试·A")
            pid_b = await _make_project(app, "项目隔离测试·B")
            db_path = app.state.settings.db_path
            _insert_run_row(db_path, "wfr_scope_a", project_id=pid_a)
            _insert_run_row(db_path, "wfr_scope_b", project_id=pid_b)

            ids_a = await _list_run_ids(app, pid_a)
            ids_b = await _list_run_ids(app, pid_b)

            assert "wfr_scope_a" in ids_a, "A 的项目级 run 必须出现在 A 的列表里"
            assert "wfr_scope_b" not in ids_a, (
                "A 的 run 列表不得含 B 的项目级 run（M7 跨项目可见缺陷）"
            )
            assert "wfr_scope_b" in ids_b, "B 的项目级 run 必须出现在 B 的列表里"
            assert "wfr_scope_a" not in ids_b, (
                "B 的 run 列表不得含 A 的项目级 run（M7 跨项目可见缺陷）"
            )

    asyncio.run(run())


def test_chapter_run_stays_scoped_to_its_chapter_project(tmp_path: Path):
    """带 chapter 的 run 可见性**零变化**：章属 A ⇒ 只在 A 的列表里。

    这条是回归看守：M7 的修法不得把「有 chapter 的 run」也放成全局可见。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "章级归属测试·A")
            pid_b = await _make_project(app, "章级归属测试·B")
            db_path = app.state.settings.db_path
            _insert_chapter(db_path, "ch_scope_a", pid_a, 1)
            _insert_run_row(
                db_path,
                "wfr_chapter_a",
                workflow_name="chapter-plan",
                chapter_id="ch_scope_a",
                project_id=pid_a,
            )

            ids_a = await _list_run_ids(app, pid_a)
            ids_b = await _list_run_ids(app, pid_b)
            assert "wfr_chapter_a" in ids_a
            assert "wfr_chapter_a" not in ids_b, "章属 A 的 run 不得出现在 B 的列表里"

    asyncio.run(run())


def test_legacy_chapter_run_without_owner_falls_back_to_chapter_lookup(tmp_path: Path):
    """遗留章级行（归属列为空）：按 chapter→project 反查判归属，仍只在本项目可见。

    ⚠️ **本用例是行为看守，不是回落分支的判别位**：数据层的 ``list_runs`` SQL
    已按 chapter→project 反查过滤过 ⇒ 端到端路径上回落分支恒真 ⇒ 端到端断言
    对它**无判别力**（坑区「断言了结果 ≠ 判别了机制」）。回落分支的判别位在
    :func:`test_run_visible_in_project_judgement_branches`（直接打函数返回值）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "遗留章级行·A")
            pid_b = await _make_project(app, "遗留章级行·B")
            db_path = app.state.settings.db_path
            _insert_chapter(db_path, "ch_legacy_a", pid_a, 1)
            _insert_run_row(
                db_path,
                "wfr_legacy_chapter",
                workflow_name="chapter-plan",
                chapter_id="ch_legacy_a",
                project_id=None,  # 0037 回填不出的遗留行
            )

            ids_a = await _list_run_ids(app, pid_a)
            ids_b = await _list_run_ids(app, pid_b)
            assert "wfr_legacy_chapter" in ids_a
            assert "wfr_legacy_chapter" not in ids_b, (
                "归属列为空的章级行仍须按章归属判（不得一律全可见）"
            )

    asyncio.run(run())


def test_run_visible_in_project_judgement_branches(tmp_path: Path):
    """判定单点 ``_run_visible_in_project`` 的**三个分支**（直接打函数返回值）。

    为什么必须直接测函数：端到端路径上，「归属列为空 + 带 chapter」的**回落
    分支**被数据层 SQL（chapter→project 反查）提前挡住 ⇒ 端到端断言对它恒真
    ⇒ 无判别力（坑区「断言了结果 ≠ 判别了机制」）。这里直接断言函数的
    真实返回值，三个分支各自可判别：

    1. 归属列非空 ⇒ 以归属列为准（他项目 ⇒ False）；
    2. 归属列为空 + 带 chapter ⇒ 回落 chapter 反查（他项目 ⇒ False）；
    3. 归属列为空 + 无 chapter ⇒ 无从归属 ⇒ 保持全可见（两个项目都 True）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "判定分支测试·A")
            pid_b = await _make_project(app, "判定分支测试·B")
            db_path = app.state.settings.db_path
            _insert_chapter(db_path, "ch_branch_a", pid_a, 1)
            chapter_service = ChapterService(db_path)

            def _visible(run: dict, project_id: str) -> bool:
                return _run_visible_in_project(
                    run, chapter_service=chapter_service, project_id=project_id
                )

            # 分支 1：归属列非空 ⇒ 归属列说了算
            assert _visible({"project_id": pid_a, "chapter_id": None}, pid_a) is True
            assert _visible({"project_id": pid_b, "chapter_id": None}, pid_a) is False

            # 分支 2：归属列为空 + 带 chapter ⇒ 回落 chapter 反查
            assert _visible({"project_id": None, "chapter_id": "ch_branch_a"}, pid_a) is True
            assert _visible({"project_id": None, "chapter_id": "ch_branch_a"}, pid_b) is False

            # 分支 3：归属列为空 + 无 chapter ⇒ 无从归属 ⇒ 保持全可见
            assert _visible({"project_id": None, "chapter_id": None}, pid_a) is True
            assert _visible({"project_id": None, "chapter_id": None}, pid_b) is True

    asyncio.run(run())


def test_legacy_null_row_without_owner_stays_visible_everywhere(tmp_path: Path):
    """归属列为空、无 chapter 的**存量 NULL 行**：**保持全项目可见**。

    ⚠️ **口径订正（F-1，2026-10-25）**：NULL 归属行有**两条成因不同的路径**——
    ① project-init「新建项目」模式**建项时恒为 NULL**（项目此刻还不存在），
    **2026-10-25 起建项成功后回写归属** ⇒ 该路径不再产生 NULL 归属行
    （端到端判别见 ``tests/workflow/test_project_init.py::
    test_project_init_new_project_claims_run_ownership``）；
    ② **真正判不出来的存量行**（checkpoint 为 ``{}`` 或无 project_id，0037
    回填不出）——**判不出来 ⇒ 不排除**，保持全项目可见。

    本用例钉的是 **②**（成因不再被登记成「历史遗留」）：它是有意的取舍
    （dev 库实测 2026-10-24 有 2 条这样的行），不是漏网。
    **若将来决定收紧口径，本用例必须同步改**——它是那一次口径变更的看守。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "遗留项目级行·A")
            pid_b = await _make_project(app, "遗留项目级行·B")
            db_path = app.state.settings.db_path
            _insert_run_row(db_path, "wfr_legacy_project", project_id=None)

            ids_a = await _list_run_ids(app, pid_a)
            ids_b = await _list_run_ids(app, pid_b)
            assert "wfr_legacy_project" in ids_a, "归属不可判的行保持全项目可见"
            assert "wfr_legacy_project" in ids_b, "归属不可判的行保持全项目可见"

    asyncio.run(run())
