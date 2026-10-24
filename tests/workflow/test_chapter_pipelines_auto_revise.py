"""auto_revise 改稿回路的端到端集成测试（自 ``test_chapter_pipelines.py`` 拆出，2026-10-24）。

拆分理由：原文件 1836 行（>1500 红线）。本文件承载「改稿回路」一个主题簇：

- 回路跑一轮后批准（``auto_revise_max=2`` → 新 write + 新 review run）；
- 开关关闭保持 FAILED；
- resume 端点 ≤2s 立即返回（不阻塞到回路结束）；
- 回路内子 run 的**模型档案**（``model_overrides``）与**作者意图**（``author_intent``）
  透传 / 继承。

原文件的自登记 TODO 记「auto_revise 主题被『草稿版本选择』簇劈成两段」，并要求拆分时
二选一：① 两节并入同一模块；② 保持两节独立、把本簇改记为「模型档案覆盖」。
本次取**方案①**：``auto_revise 回路`` 与 ``auto_revise · 模型档案覆盖`` 两节在本文件
合并 ⇒ 劈成两段的问题随拆分一并消解，不再需要二次搬运。

共享的 mock 工厂 / 请求 helper 从 ``.test_chapter_pipelines`` **import**（同一份实现，
不复制第二份 ⇒ 两文件口径恒等；这是 T-4「拆分是夹具契约搬运，不是文本搬运」的落法）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.core.db import get_connection

from ._shared import _create_app
from .test_chapter_pipelines import (
    _director_script,
    _make_chapter,
    _make_character,
    _make_project,
    _plan_and_write,
    _request,
    _sync_prompts,
    _wait_run_terminal,
    _writer_revised_script,
    _writer_script,
)

# ---------------------------------------------------------------------------
# 回路专有 helper：首轮 review 跑到 PAUSED / 轮询回路子 run / 取子 run 的 ctx
# ---------------------------------------------------------------------------


async def _setup_paused_first_review(
    app, pid: str, cid: str, *, review_author_intent: str | None = None,
) -> str:
    """plan → write → review 跑到 PAUSED，返回首轮 review run_id。

    ``review_author_intent`` 非 None 时给首轮 review run 的 ctx 一并塞该键
    （验证回路「请求体没给 → 从父 review run ctx 继承」路径的前置条件）。
    """
    first_mock = {
        "director": _director_script(),
        "writer": _writer_script(),
    }
    await _plan_and_write(
        app, pid, cid, mock_providers=first_mock, author_intent="计划意图",
    )
    review_body: dict = {"mock_providers": first_mock}
    if review_author_intent is not None:
        review_body["author_intent"] = review_author_intent
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review", json=review_body,
    )
    assert r.status_code == 201, r.text
    paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
    return paused["run_id"]


async def _wait_auto_revise_child_runs(
    app, pid: str, first_review_run_id: str, *, timeout: float = 120.0,
) -> tuple[str, str]:
    """轮询到回路产出的新 write run 与新 PAUSED review run；返回 ``(write_run_id, review_run_id)``。"""
    import time as _list_t
    deadline = _list_t.monotonic() + timeout
    while _list_t.monotonic() < deadline:
        rr = await _request(app, "GET", f"/api/projects/{pid}/runs")
        assert rr.status_code == 200, rr.text
        rows = rr.json()
        review_rows = [
            row for row in rows
            if row.get("workflow_name") == "chapter-review"
            and row["run_id"] != first_review_run_id
        ]
        write_rows = [
            row for row in rows if row.get("workflow_name") == "chapter-write"
        ]
        paused_new = [row for row in review_rows if row["status"] == "PAUSED"]
        if paused_new and write_rows:
            new_write = max(write_rows, key=lambda r0: r0.get("started_at") or "")
            new_review = max(paused_new, key=lambda r0: r0.get("started_at") or "")
            return new_write["run_id"], new_review["run_id"]
        await asyncio.sleep(0.3)
    raise AssertionError("daemon 未在 120s 内产出新的 write / PAUSED review 子 run")


async def _ckpt_ctx(app, run_id: str) -> dict:
    """取 ``run.checkpoint_json`` 顶层（引擎落盘的 ctx 就是这一层，扁平键值）。"""
    rr = await _request(app, "GET", f"/api/runs/{run_id}")
    assert rr.status_code == 200, rr.text
    return rr.json().get("checkpoint_json") or {}


# ---------------------------------------------------------------------------
# auto_revise 回路：跑一轮后批准 / 开关关闭保持 FAILED / resume 后 2s 内返回
# ---------------------------------------------------------------------------


def test_chapter_review_auto_revise_loop_once_then_approve(tmp_path: Path):
    """P0 自动改稿回路：revise 一次后 approve。

    review PAUSED → resume {revise:true, auto_revise_max:2} → 自动重跑 write→review
    → 新 review PAUSED → resume {approved:true} → COMPLETED + REVIEWED。
    验证 drafts 产生 v2（自动 write 追加新版本）。

    异步化后适配：resume 端点立即返回 RUNNING，run_id 仍是原 review run；
    auto_revise 在 daemon 线程里跑 → 实际写入的「第二次 review run」需要通过
    GET /projects/{pid}/runs list 端点按时间序找到（status=PAUSED 且 != first_review_run_id）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1
            await _plan_and_write(
                app, pid, cid, mock_providers=first_mock, author_intent="意图",
            )

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume with revise + auto_revise_max=2：触发自动回路（daemon 线程执行）
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),  # 改稿后 prose
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "节奏太散，重写"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                },
            )
            assert r.status_code == 200, r.text
            # 异步化：响应仍指向原 review run；等它进 FAILED 即 daemon 链路启动完成。
            assert r.json()["run_id"] == first_review_run_id
            assert r.json()["status"] == "RUNNING"
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            # 找 daemon 回路产生的「第二次 review run」（PAUSED 且 != 首轮 review run）
            _new_write_run_id, second_review_run_id = await _wait_auto_revise_child_runs(
                app, pid, first_review_run_id,
            )
            assert second_review_run_id != first_review_run_id

            # 4) 校验 chapter 仍为 DRAFTED，但 drafts 已有 v2
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"
            assert ch["plan_json"]["revision_note"] == "节奏太散，重写"

            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, content FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 2, drafts
            assert "终是轻轻点头" in (drafts[1]["content"] or "")

            # 5) 批准新 review → COMPLETED + REVIEWED
            r = await _request(
                app, "POST", f"/api/runs/{second_review_run_id}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_chapter_review_auto_revise_disabled_keeps_failed(tmp_path: Path):
    """auto_revise_max=0（或默认 env=0）时，revise 后保持 FAILED，不自动重跑。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
            }
            for path in ("plan", "write"):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                    json={"mock_providers": mock_providers},
                )
                assert r.status_code == 201, r.text
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            paused = {"run_id": paused["run_id"]}

            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "不改"},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            assert r.json()["run_id"] == paused["run_id"]

            # drafts 只有 v1
            conn = get_connection(app.state.settings.db_path)
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert n == 1

    asyncio.run(run())


def test_chapter_review_resume_with_auto_revise_returns_within_2s(tmp_path: Path):
    """resume + auto_revise 触发时：POST ≤2s 即返回；轮询最终见到 daemon 回路新 write/review run。

    验证点：
    - POST /runs/{id}/resume 在 ≤2s 内拿到 200 响应（不再像旧实现那样阻塞到回路结束，
      auto_revise_max=2 最长可跑两轮 write+review，几分钟量级）。
    - 响应 status="RUNNING"，run_id 等于被 resume 的原 review run。
    - daemon 线程最终在 list 端点可观察到 chapter-review 的新 PAUSED run 与
      chapter-write 新 run（数量 ≥2 的 chapter-review run、≥1 的 chapter-write run
      是在 daemon 链路里新建的）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1
            await _plan_and_write(
                app, pid, cid, mock_providers=first_mock, author_intent="意图",
            )

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume revise + auto_revise_max=2：必须 ≤2s 返回
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            import time as _wall_t
            _t0 = _wall_t.monotonic()
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿触发"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                },
            )
            _t1 = _wall_t.monotonic()
            assert r.status_code == 200, r.text
            assert (_t1 - _t0) <= 2.0, (
                f"resume + auto_revise 响应耗时 {_t1 - _t0:.2f}s 超 2s 上限"
            )
            assert r.json()["status"] == "RUNNING"
            assert r.json()["run_id"] == first_review_run_id

            # 4) 轮询 list 端点，直到见到 daemon 回路产生的新 write + 新 review run
            new_write_run_id, new_review_run_id = await _wait_auto_revise_child_runs(
                app, pid, first_review_run_id,
            )
            assert new_write_run_id != new_review_run_id

            # 5) drafts 已含 v2（daemon 跑过 write）
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2], drafts

    asyncio.run(run())


# ---------------------------------------------------------------------------
# auto_revise 回路 · 模型档案覆盖：显式 model_overrides 透传 / 缺省时从 review run 继承
#
# 【主题簇登记 · 2026-10-13 Lead 裁决 = (b) 下一批待办 —— 2026-10-24 已落地】
#   原裁决：auto_revise 主题在 ``test_chapter_pipelines.py`` 被「草稿版本选择」簇劈成
#   两段，拆分时须二选一（① 两节并入同一模块；② 保持两节独立并改记簇名）。
#   本次落地：取**方案①**——「auto_revise 回路」与「auto_revise · 模型档案覆盖」两节
#   合并进本文件（原文件拆出的另一半是章节生命周期主簇），两节不再被第三方簇隔开。
#   本簇因此改记为「auto_revise 回路 · 模型档案覆盖」，名实相符。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "with_overrides",
    [True, False],
    ids=["with_overrides", "without_overrides"],
)
def test_chapter_review_auto_revise_loop_model_overrides_propagation(
    tmp_path: Path, with_overrides: bool,
):
    """auto_revise 回路的 model_overrides 正反两面（2026-10-24 参数化合一）。

    - ``with_overrides=True``（原 ``test_..._propagates_model_overrides``）：resume body
      显式给 ``model_overrides`` → 回路重跑的 write / review 子 run 的 checkpoint_json
      中必须原样携带同一份 overrides（首轮档案不丢失）。
    - ``with_overrides=False``（原 ``test_..._without_overrides_inherits_from_review_run``）：
      resume body 不传、且原 review run 的 checkpoint_json 内也无 overrides → 回路子 run
      的 checkpoint_json **不应**出现 ``model_overrides`` / ``author_intent`` 键（保证缺省
      零行为变更）。

    正反两面**覆盖面不许变窄**：反面那一支原本单独一个用例断言「author_intent 缺席」，
    参数化后该断言逐字保留在 ``without_overrides`` 参数组里。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_mock = {
                "director": _director_script(),
                "writer": _writer_script(),
            }

            # 1) plan + write v1（首轮不带 overrides，保持简单）
            await _plan_and_write(
                app, pid, cid, mock_providers=first_mock, author_intent="意图",
            )

            # 2) 首轮 review → PAUSED（也不带 overrides）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": first_mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            first_review_run_id = paused["run_id"]

            # 3) resume revise + model_overrides + auto_revise_max=2：触发回路
            overrides = {"creative_writing": "mprof_auto_revise_passthrough"}
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            resume_body = {
                "human_input": {"approved": False, "revise": True, "note": "改稿"},
                "auto_revise_max": 2,
                "mock_providers": revise_mock,
            }
            if with_overrides:
                resume_body["model_overrides"] = overrides
            # else：故意不传 model_overrides
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json=resume_body,
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "RUNNING"
            assert r.json()["run_id"] == first_review_run_id

            # 等首轮 review 走到 FAILED(rejected-for-revision) → 回路启动
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            # 4) 轮询找回路产生的「新 chapter-write run」+「新 chapter-review PAUSED run」
            new_write_run_id, new_review_run_id = await _wait_auto_revise_child_runs(
                app, pid, first_review_run_id,
            )

            if with_overrides:
                # 5a) 断言：回路内 write / review 子 run 的 checkpoint_json 中都携带 overrides
                # （与首轮跑 test_write_model_overrides_passthrough_through_ctx 同口径：
                # 在任意 node checkpoint 或合并 ctx 中匹配到即可。）
                async def _ckpt_has_overrides(run_id: str) -> bool:
                    rr = await _request(app, "GET", f"/api/runs/{run_id}")
                    assert rr.status_code == 200, rr.text
                    ckpt = rr.json().get("checkpoint_json") or {}
                    for _node_id, node_ckpt in ckpt.items():
                        if not isinstance(node_ckpt, dict):
                            continue
                        ctx_blob = node_ckpt.get("ctx")
                        merged = (
                            {**{k: v for k, v in ckpt.items() if k != "ctx"}, **(ctx_blob if isinstance(ctx_blob, dict) else {})}
                            if isinstance(ckpt.get("ctx"), dict)
                            else {**ckpt, **(ctx_blob if isinstance(ctx_blob, dict) else {})}
                        )
                        if isinstance(merged.get("model_overrides"), dict) and merged["model_overrides"] == overrides:
                            return True
                        if isinstance(node_ckpt.get("model_overrides"), dict) and node_ckpt["model_overrides"] == overrides:
                            return True
                    return False

                assert await _ckpt_has_overrides(new_write_run_id), (
                    f"回路内 chapter-write 子 run（{new_write_run_id}）未携带 model_overrides={overrides}"
                )
                assert await _ckpt_has_overrides(new_review_run_id), (
                    f"回路内 chapter-review 子 run（{new_review_run_id}）未携带 model_overrides={overrides}"
                )
            else:
                # 5b) 断言：未传的透传键（model_overrides / author_intent）不得出现在
                # 回路子 run 的 ctx 里。
                async def _ckpt_omits_loop_ctx_keys(run_id: str) -> None:
                    """未传的透传键（model_overrides / author_intent）不得出现在回路子 run 的 ctx 里。"""
                    rr = await _request(app, "GET", f"/api/runs/{run_id}")
                    assert rr.status_code == 200, rr.text
                    ckpt = rr.json().get("checkpoint_json") or {}
                    for key in ("model_overrides", "author_intent"):
                        if key in ckpt:
                            raise AssertionError(
                                f"未传 {key}（父 ctx 也无）时回路子 run {run_id} 的 checkpoint "
                                f"顶层不应携带该键；实际={ckpt.get(key)!r}"
                            )
                    for node_id, node_ckpt in ckpt.items():
                        if not isinstance(node_ckpt, dict):
                            continue
                        if "model_overrides" in node_ckpt:
                            raise AssertionError(
                                f"未传 model_overrides 时回路子 run {run_id} 的 {node_id} 不应携带该键；"
                                f"实际={node_ckpt!r}"
                            )

                await _ckpt_omits_loop_ctx_keys(new_write_run_id)
                await _ckpt_omits_loop_ctx_keys(new_review_run_id)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# F-11（2026-09-16 实证）：auto_revise 回路的 author_intent 透传 / 继承
# ---------------------------------------------------------------------------


def test_chapter_review_auto_revise_loop_propagates_author_intent(tmp_path: Path):
    """F-11：resume body 带 ``author_intent`` 时，回路重跑的 write / review 子 run 的 ctx
    必须原样携带同一份 author_intent——改稿轮不再在「无任何作者约束」状态下重写正文。

    实证背景（2026-09-16 新书 01 ch2）：回路只透传 model_overrides，v1 正文干净、
    改稿轮 v2 把内部字段名 ``recalled_passages`` 写进正文。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            first_review_run_id = await _setup_paused_first_review(app, pid, cid)
            # 前置条件：首轮 review run 的 ctx 里没有 author_intent（否则下面的断言可能靠继承蒙混过关）
            assert "author_intent" not in await _ckpt_ctx(app, first_review_run_id)

            intent = "本书铁律：无CP、字数 2200~2800、禁止内部标识入文"
            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                    "author_intent": intent,
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "RUNNING"
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            new_write_run_id, new_review_run_id = await _wait_auto_revise_child_runs(
                app, pid, first_review_run_id
            )
            for run_id in (new_write_run_id, new_review_run_id):
                ckpt = await _ckpt_ctx(app, run_id)
                assert ckpt.get("author_intent") == intent, (
                    f"回路子 run {run_id} 的 ctx.author_intent={ckpt.get('author_intent')!r}，"
                    f"期望 {intent!r}（resume 请求体显式给出的值）"
                )

    asyncio.run(run())


def test_chapter_review_auto_revise_loop_inherits_author_intent_from_review_run(tmp_path: Path):
    """F-11 核心断言：resume body 不给 ``author_intent`` 时，回路从父 review run 的
    checkpoint_json（即其 ctx）继承首轮那份——「改稿轮不再丢作者约束」的正解路径。

    突变验证：撤掉 ``control.py`` 的继承分支（``effective_author_intent`` 恒 None）→ 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            intent = "本书铁律：价签快照只给单一价格数字、对话引号统一 “”"
            first_review_run_id = await _setup_paused_first_review(
                app, pid, cid, review_author_intent=intent,
            )
            # 前置条件：父 review run 的 ctx 确实带这份 author_intent（继承的来源）
            assert (await _ckpt_ctx(app, first_review_run_id)).get("author_intent") == intent

            revise_mock = {
                "director": _director_script(),
                "writer": _writer_revised_script(),
            }
            r = await _request(
                app, "POST", f"/api/runs/{first_review_run_id}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "改稿"},
                    "auto_revise_max": 2,
                    "mock_providers": revise_mock,
                    # 故意不传 author_intent → 期望从父 review run ctx 继承
                },
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, first_review_run_id, expected=("FAILED",))

            new_write_run_id, new_review_run_id = await _wait_auto_revise_child_runs(
                app, pid, first_review_run_id
            )
            for run_id in (new_write_run_id, new_review_run_id):
                ckpt = await _ckpt_ctx(app, run_id)
                assert ckpt.get("author_intent") == intent, (
                    f"回路子 run {run_id} 未从父 review run（{first_review_run_id}）继承 "
                    f"author_intent；实际={ckpt.get('author_intent')!r}，期望 {intent!r}"
                )

    asyncio.run(run())
