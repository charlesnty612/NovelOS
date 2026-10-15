"""快通道的闸门判别用例（2026-10-15 审查批次）。

补的是两条此前**完全无覆盖**的路径，以及一条把散文声明变成机器不变量：

1. **人工审校闸门不被快通道静默批准**（CRITICAL 修复的回归看守）。
   ``ctx["human_input"]`` 在全仓有两个读者——``chapter_commit/gate.py`` 的
   「预置放行本闸门」与 ``chapter_review/pipeline.py`` 的「读人已做出的决议」。
   修复前 :func:`control._start_workflow_core` 对 plan/write/review 一律注入，
   于是作者手动点「审校」在快通道项目上**一句不问就自批**、章节直推 REVIEWED
   （实测：快通道开→COMPLETED/REVIEWED，关→PAUSED/DRAFTED）。
   撤掉 ``control.py`` 里 ``workflow_name == "chapter-commit"`` 那层收口即红。

2. **预置批准本身真的起作用**（修掉此前的空断言）。
   原 ``test_fast_commit_e2e.py`` 的夹具只产 ``risk_level="LOW"`` 的 delta ⇒
   ``_has_high_risk_change`` 恒 False ⇒ ``gate.py`` 的 ``if not needs: return``
   提前返回 ⇒ ``human_input`` 一次都没被读 ⇒ 把 ``"approved": True`` 改成
   ``False`` 三条用例照样全绿。本文件改用 HIGH 风险夹具，让该路径**真的被执行**：
   ``test_high_risk_change_is_auto_approved_by_fast_mode`` 与
   ``test_high_risk_change_pauses_when_fast_off`` 构成正反一对。

3. **「不发真实模型调用」从散文声明变成可校验不变量**。
   同批审查实测：``scene_planner`` / ``summarizer`` / ``critic`` 在既有 e2e 里
   没有 mock 脚本，会真的进 ``ModelRouter.call_with_fallback``——只因临时库没有
   任何模型配置才在**构造 Provider 之前**抛 ``ModelNotConfiguredError``，
   随后被各节点的 degraded 分支吞掉，测试全绿。
   拦住它的是巧合不是设计：任何夹具将来播种一行配置，立刻变成真实调用且无人察觉。
   ``test_e2e_db_cannot_reach_a_real_provider`` 把这条钉死。

模式沿用 ``tests/api/test_fast_commit_e2e.py``（ASGITransport + 临时库 + mock）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.api.test_fast_commit_e2e import (
    _make_character,
    _observer_script,
    _plan,
    _runs_for_chapter,
    _wait_for_commit_run,
    _writer_script,
)
from tests.integration.test_v1_4_reference_and_revision import (
    _create_app,
    _director_script,
    _make_chapter,
    _make_project,
    _request,
    _sync_prompts,
    _wait_run_terminal,
)

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _observer_script_high(chapter_id: str, character_id: str) -> list[str]:
    """observer mock，**HIGH 风险**版。

    与 ``test_fast_commit_e2e._observer_script`` 的唯一差别是 ``risk_level``：
    ``pipeline_common._has_high_risk_change`` 要求 ``risk_level == "HIGH"``
    或 ``facet == "definition"`` 或 ``world_kind == "rule"`` 任一命中，
    LOW 版会让 ``high_risk_approval`` 节点提前 return——那样它就是空断言。
    """
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "cc_fast_high_1",
                        "op": "update",
                        "target_id": character_id,
                        "character_id": character_id,
                        "facet": "state",
                        "field": "state.location",
                        "before": "破屋",
                        "after": "城门",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "scene_id": None,
                            "excerpt": "他推开门，往城门去。",
                            "span": None,
                        },
                        "risk_level": "HIGH",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]


def _db_path(tmp_path: Path) -> str:
    from packages.core.config import Settings

    return str(Settings(data_dir=tmp_path, log_level="WARNING").db_path)


async def _write_to_drafted(app, pid: str, cid: str, mock: dict) -> None:
    """plan → write（不自动衔接）→ 停在 DRAFTED。"""
    await _plan(app, pid, cid, mock)
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock},
    )
    assert r.status_code == 201, r.text
    run = await _wait_run_terminal(
        app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
    )
    assert run["status"] == "COMPLETED", run.get("error")
    r = await _request(app, "GET", f"/api/chapters/{cid}")
    assert r.json()["status"] == "DRAFTED", r.json()["status"]


async def _nodes(app, run_id: str) -> dict[str, dict]:
    r = await _request(app, "GET", f"/api/runs/{run_id}")
    assert r.status_code == 200, r.text
    return {n["node_id"]: n for n in r.json()["nodes"]}


# ---------------------------------------------------------------------------
# 1. CRITICAL 回归：人工审校闸门不得被快通道静默批准
# ---------------------------------------------------------------------------


def test_manual_review_still_pauses_under_fast_mode(tmp_path: Path):
    """快通道项目上**手动点「审校」必须真的 PAUSE 等人**。

    这是 CRITICAL 的回归看守：修复前 ``ctx["human_input"]`` 被无差别注入，
    ``_author_review_node`` 读到 ``approved=True`` 就直接返回（不抛
    ``PauseRequested``），run 走 COMPLETED、章节被标成 REVIEWED，
    作者从头到尾没被问过一句。

    判别：撤掉 ``control.py`` 里 ``workflow_name == "chapter-commit"`` 的收口。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "fast project")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)

            r = await _request(
                app, "PATCH", f"/api/projects/{pid}", json={"fast_mode": True}
            )
            assert r.status_code == 200, r.text

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script(cid, char_id),
            }
            await _write_to_drafted(app, pid, cid, mock)

            # 作者显式点「审校」——这是「我要人工看一眼」的动作
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            review = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED", "PAUSED")
            )

            assert review["status"] == "PAUSED", (
                "快通道项目上手动审校被静默自动批准了——"
                f"author_review 不该拿到预置的 human_input：{review}"
            )
            nodes = await _nodes(app, review["run_id"])
            assert nodes["author_review"]["status"] == "PENDING", nodes["author_review"]

            # 未被人工批准 ⇒ 章节不得推进到 REVIEWED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED", r.json()["status"]

            return pid

    __import__("asyncio").run(run())


# ---------------------------------------------------------------------------
# 2. 预置批准真的起作用（修掉此前的空断言）
# ---------------------------------------------------------------------------


def test_high_risk_change_is_auto_approved_by_fast_mode(tmp_path: Path):
    """HIGH 风险 delta + 快通道 ⇒ 预置批准**真的**放行，不 PAUSE。

    判别：把 ``control.py`` 里预置的 ``"approved": True`` 改成 ``False``。
    修复前这条路径在本仓 e2e 中从未被执行（夹具只产 LOW 风险），
    所以那种改动**不会让任何既有用例变红**——这正是本用例存在的理由。
    """
    app = _create_app(tmp_path)
    db_path = _db_path(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "fast high risk")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)
            await _request(
                app, "PATCH", f"/api/projects/{pid}", json={"fast_mode": True}
            )
            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script_high(cid, char_id),
            }
            await _plan(app, pid, cid, mock)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock, "auto_commit": True},
            )
            assert r.status_code == 201, r.text
            w = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert w["status"] == "COMPLETED", w.get("error")

            commit_run = await _wait_for_commit_run(app, pid, cid)
            assert commit_run["status"] == "COMPLETED", (
                f"HIGH 风险下预置批准失效（run 未走到 COMPLETED）：{commit_run.get('error')}"
            )
            nodes = await _nodes(app, commit_run["run_id"])
            assert nodes["high_risk_approval"]["status"] == "COMPLETED"
            runs = await _runs_for_chapter(app, pid, cid)
            assert not [x for x in runs if x["status"] == "PAUSED"], runs
            return pid

    pid = __import__("asyncio").run(run())

    from packages.core.db import get_connection

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT author_approval_json FROM commits WHERE project_id = ?", (pid,)
        ).fetchall()
    finally:
        conn.close()
    assert rows, "commits 表无行"
    approval = json.loads(rows[-1]["author_approval_json"])
    assert approval["approver"] == "system:fast_mode", approval


def test_high_risk_change_pauses_when_fast_off(tmp_path: Path):
    """**对照组**：快通道关时，HIGH 风险必须真的 PAUSE 等人工批准。

    没有这条，上面那条就只是「没挂起」而看不出「该挂起时挂起了」——
    闸门若被整体摘掉，两条会一起绿。

    完整链路：write(DRAFTED) → review(人工批准→REVIEWED) → commit(应 PAUSE)。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "normal high risk")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)

            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.json()["fast_mode"] is None, "对照组项目应是未设置态"

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script_high(cid, char_id),
            }
            await _write_to_drafted(app, pid, cid, mock)

            # 常规评审：PAUSE → 人工批准 → REVIEWED
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            review = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED", "PAUSED")
            )
            assert review["status"] == "PAUSED", review
            r = await _request(
                app,
                "POST",
                f"/api/runs/{review['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            done = await _wait_run_terminal(
                app, review["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert done["status"] == "COMPLETED", done.get("error")
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()["status"]

            # 提交：HIGH 风险 ⇒ 必须 PAUSE 等待 high_risk_approval
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            commit = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED", "PAUSED")
            )
            assert commit["status"] == "PAUSED", (
                f"HIGH 风险未触发人工审批门：{commit}"
            )
            nodes = await _nodes(app, commit["run_id"])
            assert nodes["high_risk_approval"]["status"] == "PENDING", nodes[
                "high_risk_approval"
            ]
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", "未批准前不得定稿"

            # 人工批准后应能走完
            r = await _request(
                app,
                "POST",
                f"/api/runs/{commit['run_id']}/resume",
                json={"human_input": {"approved": True, "approver": "human"}},
            )
            assert r.status_code == 200, r.text
            done = await _wait_run_terminal(
                app, commit["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert done["status"] == "COMPLETED", done.get("error")
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED", r.json()["status"]

    __import__("asyncio").run(run())


# ---------------------------------------------------------------------------
# 3. 「不发真实模型调用」变成机器可校验的不变量
# ---------------------------------------------------------------------------


def test_e2e_db_cannot_reach_a_real_provider(tmp_path: Path):
    """临时库里**不得存在任何可用的模型配置**——真实 Provider 无从构造。

    把 §一 硬约束 2（改软件过程中不做真实 LLM 调用）从散文声明变成断言。

    背景：既有 e2e 里 ``scene_planner`` / ``summarizer`` / ``critic`` 没有 mock
    脚本，会真的进 ``ModelRouter.call_with_fallback``；拦住它们的**不是设计而是
    巧合**——临时库恰好没有任何模型配置，router 在构造 Provider 之前就抛了
    ``ModelNotConfiguredError``，错误又被各节点的 degraded 分支吞掉，测试全绿。
    任何人将来在夹具里播种一行配置，这些用例就会静默变成真实调用。

    判别：往这些 e2e 的临时库里 INSERT 任一模型配置 / 档案 / 绑定，本用例即红。
    """
    db_path = _db_path(tmp_path)
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

    __import__("asyncio").run(run())

    from packages.core.db import get_connection

    conn = get_connection(db_path)
    try:
        for table in ("model_configs", "model_profiles", "capability_bindings"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n == 0, (
                f"{table} 有 {n} 行——这些 e2e 的临时库必须无法构造真实 Provider，"
                "否则「不发真实模型调用」只是散文声明而非事实"
            )
    finally:
        conn.close()
