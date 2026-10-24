"""chapter-plan/write/review/commit 端到端集成测试（Sprint 4-A）。

用全 mock（mock_providers 给 director/writer/observer 脚本化合法 JSON）跑通 §110 链路核心段：
- project → character → chapter → init state
- plan → write → review (approve) → commit (无 HIGH)
- 断言 chapters.status=COMMITTED、state_version 递增、drafts/plan_json 落库

【2026-10-24 拆分留痕 · 原自登记 TODO 的裁决落地】
原文件 1836 行（>1500 红线），自登记 TODO（本文件历史 :1234-1248）要求「触发条件成立
即必须先落裁决」。本次按 TODO 的**方案①**（两节并入同一模块，跨节搬运）落地：

- 本文件 = 章节生命周期主簇（plan→write→review→commit + 草稿版本选择 + writer 落库规整）
- ``tests/workflow/test_chapter_pipelines_auto_revise.py`` = 改稿回路簇（auto_revise
  回路 + auto_revise · 模型档案覆盖，两节合并进同一模块 ⇒ TODO 的「劈成两段」问题
  在拆分的同时被消解，不再需要「二选一」的二次搬运）

用例数守恒：拆分前 17 ⇒ 拆分后 10 + 7 = 17（``--collect-only -q`` 实测）。
共享的 mock 工厂 / 请求 helper **全部留在本文件**，由新文件 import（不复制第二份 ⇒
两文件口径恒等；这是 T-4「拆分是夹具契约搬运，不是文本搬运」的落法）。

【2026-10-24 样板清理留痕】
- 62+ 条「同一 run 连续多次 ``_wait_run_terminal``」的恒等重复调用已删（第 1 条到达终态
  后其余恒立即返回）；异 run 的调用（``first_review_run_id`` 等）一条未动。
- 4 处逐字复制的「轮询 list 端点找回路子 run」块 → 复用文件内已有的
  ``_wait_auto_revise_child_runs``（原只被 2 例用）。
- 7 处 plan→write 样板 → ``_plan_and_write`` / ``_plan`` / ``_write``。
- 3 个同骨架 writer mock → ``_writer_script(prose=..., deviations=...)`` 派生式。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.db import get_connection

from ._shared import _create_app, _get_run_via_http, _make_client

# ---------------------------------------------------------------------------
# 公共 helper：run 终态轮询 / app 与 client 构造 / 项目·角色·章节创建
# + 各 agent 的脚本化 mock 响应（director / writer 派生式 / observer）
# ---------------------------------------------------------------------------

# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    """轮询直到 run.status ∈ expected；返回最终 run dict。

    SQLite 跨连接视角 + 后台线程落库时延：单节点 mock 流程通常 < 1s 跑完，
    但 polling 必须等到节点行 FAILED/COMPLETED 也写入——轮询间隔 0.2s 足以。
    """
    import asyncio
    import time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        last_run = run
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    """同步 prompts（注册 director / writer / observer ACTIVE 行）"""
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _plan(
    app, pid: str, cid: str, *, mock_providers: dict, author_intent: str | None = None,
) -> dict:
    """起 chapter-plan 并等终态 + 等 COMPLETED；返回 plan run dict。

    两等各有分工，缺一不可：
    - 先等「终态」：同 chapter 的下一次 POST 不撞 409 并发闸门的前置
      （该等待由 ``tests/workflow/_async_helpers.py`` 第 5 步自动插入）；
    - 后等「COMPLETED」：这才是「这一步真的成功」的断言（PAUSED/FAILED 都不算数）。
    """
    plan_body: dict = {"mock_providers": mock_providers}
    if author_intent is not None:
        plan_body["author_intent"] = author_intent
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json=plan_body,
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    return await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))


async def _write(app, pid: str, cid: str, *, mock_providers: dict) -> dict:
    """起 chapter-write 并等终态 + 等 COMPLETED；返回 write run dict（分工同 ``_plan``）。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    return await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))


async def _plan_and_write(
    app, pid: str, cid: str, *, mock_providers: dict, author_intent: str | None = None,
) -> tuple[dict, dict]:
    """plan → write 两步样板（7 处用例逐字重复的那一段）。

    返回 ``(plan_run, write_run)`` 两个终态 run dict。
    """
    plan_run = await _plan(
        app, pid, cid, mock_providers=mock_providers, author_intent=author_intent,
    )
    write_run = await _write(app, pid, cid, mock_providers=mock_providers)
    return plan_run, write_run


def _director_script() -> list[str]:
    """合规 director-plan.v1 输出。"""
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "苏婉清在夜谈中第一次怀疑林渊隐瞒父亲死因",
                "core_conflict": "苏婉清的求真意志 vs 林渊的善意隐瞒",
                "turning_point": "林渊回避黑玉佩细节，苏婉清察觉",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "夜访场景设置",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "建立信任基础",
                    },
                    {
                        "beat_id": "beat_002",
                        "purpose": "黑玉佩引入对话",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "MEDIUM",
                        "narrative_question_served": "伏笔推进",
                    },
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "建议场景数 2",
            },
            ensure_ascii=False,
        )
    ]


# ---------------------------------------------------------------------------
# writer mock：三个正文 + 一份「整章改写申报」，共用同一个 JSON 骨架构造器
# ---------------------------------------------------------------------------

_DEFAULT_WRITER_PROSE = (
    "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
    "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
    "林渊立在博古架前，背对着她，似乎在翻检什么。松烟墨香被夜风裹着送进来，"
    "灯芯爆了一下花，啪地轻响。她开口问起父亲遗物。"
)

_REVISED_WRITER_PROSE = (
    "戌时的更鼓从街尾传过来。苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
    "林渊闻言沉默良久，终是轻轻点头：\"令尊遗物，确有一件我尚未说清。\""
)

_QUOTES_LEAK_WRITER_PROSE = (
    "戌时的更鼓从街尾传过来。\n\n"
    "「你来晚了。」苏婉清把茶盏往他那边推了推，"
    "「这盏茶温了三回。」\n\n"
    "他忽然想起recalled_passages里自己的那句话，"
    "喉头动了一下，终究没说出口。灯芯爆了个花，"
    "啪地轻响，影子在青石地砖上晃了晃，又稳住了。"
)

# 契约合规路径②：整章改写必须申报（writer-v3.md §6.1「越界必申报」）。
_REWRITE_DEVIATION = {
    "kind": "other",
    "from": "修订后的草稿内容：林渊闻言沉默良久，终是轻轻点头。",
    "to": "整章按 mock 固定稿重写（与 25 字上游稿零逐字重合）",
    "reason": "mock 输出与上游稿零逐字重合：整章改写，超出定向局部修改范围",
}


def _writer_script(*, prose: str = _DEFAULT_WRITER_PROSE, deviations: tuple[dict, ...] = ()) -> list[str]:
    """合规 writer-output.v1 输出。

    三个 writer mock 曾是逐字复制的三份骨架（只有正文不同），现收敛成一个构造器：
    - ``prose`` 换正文；
    - ``deviations`` 非空时写进 ``self_report.deviations``（契约合规路径②）。

    输出字节与拆分前逐位一致（字段顺序不变：``self_report`` 内 deviations 仍在
    ``word_count`` / ``scene_count`` 之后）。
    """
    payload = {
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v1",
        "chapter_id": "ch_xxx",
        "prose": prose,
        "self_report": {
            "slots_filled": ["slot_001"],
            "word_count": len(prose),
            "scene_count": 1,
            "deviations": list(deviations),
            "forbidden_word_hits": [],
            "self_check_notes": "",
        },
    }
    return [json.dumps(payload, ensure_ascii=False)]


def _writer_declared_rewrite_script() -> list[str]:
    """_writer_script 的局部变体：self_report.deviations 申报整章改写（契约合规路径②）。

    仅 test_chapter_review_revise_loop_end_to_end 使用：其第 5 步在 DRAFTED 章
    重跑 write（revision_note + draft_text 皆非空 → mode='revise'），上游稿是 25 字
    人工改稿、本 mock 固定回 ~119 字稿，preserved_ratio=0.0833 < 0.5——零申报会被
    保真闸门正确拒绝（闸门语义是对的，夹具不合规）；按 writer-v3.md §6.1「越界
    必申报」申报该次改写即放行。不改其它测试共享的 _writer_script（保持零申报
    期望）。
    """
    return _writer_script(deviations=(_REWRITE_DEVIATION,))


def _writer_revised_script() -> list[str]:
    """改稿后的 writer-output.v1 输出（用于 auto-revise 回路第二轮 write）。"""
    return _writer_script(prose=_REVISED_WRITER_PROSE)


def _writer_script_with_quotes_and_leak() -> list[str]:
    """含「」对话与内部标识的 writer 输出：用于断言落库前的确定性规整。"""
    return _writer_script(prose=_QUOTES_LEAK_WRITER_PROSE)


def _observer_noop_script() -> list[str]:
    """Observer 输出 7 个空数组（无 change）；通过 schema 校验（无 risk_level=HIGH）。"""
    return [
        json.dumps(
            {
                "character_changes": [],
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


# ---------------------------------------------------------------------------
# 端到端主链路：plan → write → review(approve) → commit → COMMITTED
# ---------------------------------------------------------------------------


def test_chapter_pipelines_end_to_end(tmp_path: Path):
    """端到端：plan → write → review(approve) → commit(无 HIGH) → COMMITTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 1) chapter-plan
            plan_run = await _plan(
                app, pid, cid,
                mock_providers=mock_providers,
                author_intent="让女主第一次怀疑男主",
            )
            assert plan_run["current_node"] is None

            # 断言 chapters.plan_json 已落库
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["plan_json"]["chapter_goal"], "plan_json 没落库"
            assert ch["status"] == "PLANNED"  # plan 不改 status

            # 2) chapter-write
            await _write(app, pid, cid, mock_providers=mock_providers)
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"

            # 断言 drafts 行
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT * FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 1, drafts
            assert (drafts[0]["content"] or "") != ""

            # 3) chapter-review (approving)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            review_resp = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            assert "pause_payload" in review_resp

            # resume with approved=true
            r = await _request(
                app, "POST", f"/api/runs/{review_resp['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "REVIEWED"

            # 4) chapter-commit (observer 产出 7 空数组，无 HIGH)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            # 断言 chapters.status=COMMITTED + state_version 递增
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED"

            conn = get_connection(app.state.settings.db_path)
            try:
                snap_row = conn.execute(
                    "SELECT MAX(state_version) AS v FROM story_states WHERE project_id = ?",
                    (pid,),
                ).fetchone()
                assert snap_row["v"] == 2, snap_row["v"]  # init_genesis v1 + commit v2
                commits = conn.execute(
                    "SELECT * FROM commits WHERE chapter_id = ? ORDER BY resulting_state_version ASC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(commits) == 2, commits  # genesis + observer commit

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 人工评审门禁：review 驳回应把 run 打成 FAILED
# ---------------------------------------------------------------------------


def test_chapter_review_rejected_results_in_failed(tmp_path: Path):
    """review 时 author 拒绝 → resume 后 run FAILED（因 mark_reviewed 抛错）。"""
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

            # 先 plan + write
            await _plan_and_write(
                app, pid, cid, mock_providers=mock_providers, author_intent="意图",
            )
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))

            # resume with approved=false → mark_reviewed 抛错 → run FAILED
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": False}},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

            # chapters.status 保持 DRAFTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 纯驳回（无 revise）：run FAILED + error='rejected'（与 revise 分支的
            # 'rejected-for-revision' 区分），且不会触发 auto_revise 回路。
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected", r.json().get("error")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 改稿回路（revise loop）：review 驳回 → 改稿 → 复审闭环
# + 无修订意见时应清空 revision_note
# ---------------------------------------------------------------------------


def test_chapter_review_revise_loop_end_to_end(tmp_path: Path):
    """review 驳回并改稿闭环（PRD §59/§87 / deviation #1 关闭）：

    review PAUSED → resume {approved:false, revise:true, note} → run FAILED
    (error='rejected-for-revision')、chapter 保持 DRAFTED、note 落 plan_json.revision_note
    → 人工改稿（POST drafts）→ 重跑 write（新 draft 版本）→ 再 review → approve → REVIEWED。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                # 第 5 步重跑 write 走 mode='revise'（25 字上游稿 vs 固定 mock 输出，
                # preserved_ratio<0.5）：writer 需申报改写（契约合规路径②），否则被
                # 保真闸门正确拒绝——闸门不动，夹具合规化。
                "writer": _writer_declared_rewrite_script(),
            }

            # 1) plan + write
            await _plan_and_write(
                app, pid, cid, mock_providers=mock_providers, author_intent="意图",
            )
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 2) review → PAUSED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))

            # 3) resume revise:true + note → run FAILED(rejected-for-revision)
            # P0：auto_revise 默认开启，为验证原手动改稿闭环显式关闭。
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True, "note": "禁用词命中，请改写后重审"},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            # resume 响应不含 error 字段（router 只回 run_id/status/current_node），经 GET /runs/{id} 校验
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected-for-revision", r.json()["error"]

            # chapter 保持 DRAFTED；plan_json.revision_note 已落
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            ch = r.json()
            assert ch["status"] == "DRAFTED"
            assert ch["plan_json"]["revision_note"] == "禁用词命中，请改写后重审"

            # 4) 人工改稿（POST drafts）
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": "修订后的草稿内容：林渊闻言沉默良久，终是轻轻点头。"},
            )
            assert r.status_code == 201, r.text

            # 5) 重跑 write（DRAFTED 允许）→ 新 draft 版本落库（v1=write、v2=人工改稿、v3=重跑 write）
            await _write(app, pid, cid, mock_providers=mock_providers)
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, created_by FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2, 3], drafts

            # 6) 再 review → approve → REVIEWED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            paused2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            r = await _request(
                app, "POST", f"/api/runs/{paused2['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_chapter_review_revise_without_note_clears_revision_note(tmp_path: Path):
    """revise:true 但无 note → plan_json.revision_note 键被移除（幂等清理）。"""
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

            # 预置旧 revision_note，模拟上一轮 revise 残留
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan = dict(r.json()["plan_json"])
            plan["revision_note"] = "旧意见"
            r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"plan_json": plan})
            assert r.status_code == 200, r.text

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused_run = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            paused = {"run_id": paused_run["run_id"]}
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={
                    "human_input": {"approved": False, "revise": True},
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.status_code == 200
            assert r.json()["error"] == "rejected-for-revision"

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"
            assert "revision_note" not in r.json()["plan_json"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 前置状态守卫：未跑 review 就 commit 应 FAILED；对已 COMMITTED 章再 write 应 FAILED
# ---------------------------------------------------------------------------


def test_chapter_commit_without_review_fails(tmp_path: Path):
    """commit 在 chapter 仍为 DRAFTED 时（未跑 review）应 FAILED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            # 跳过 plan/write/review，直接 commit
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": {"observer": _observer_noop_script()}},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

    asyncio.run(run())


def test_chapter_write_on_committed_chapter_fails(tmp_path: Path):
    """对已 COMMITTED 的 chapter 再次启动 chapter-write 应 FAILED，drafts 行数不增。

    F1：chapter_write 仅允许 PLANNED/DRAFTED 启动；REVIEWED/COMMITTED 重跑被拒。
    """
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
                "observer": _observer_noop_script(),
            }

            # 完整跑到 COMMITTED
            for path in ("plan", "write", "review", "commit"):
                if path == "review":
                    r = await _request(
                        app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                        json={"mock_providers": mock_providers},
                    )
                    assert r.status_code == 201, r.text
                    paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
                    r = await _request(
                        app, "POST", f"/api/runs/{paused['run_id']}/resume",
                        json={"human_input": {"approved": True}},
                    )
                    assert r.status_code == 200, r.text
                    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
                else:
                    r = await _request(
                        app, "POST", f"/api/projects/{pid}/chapters/{cid}/{path}",
                        json={"mock_providers": mock_providers},
                    )
                    assert r.status_code == 201, r.text
                    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED"

            # 记录 COMMITTED 时的 drafts 行数
            conn = get_connection(app.state.settings.db_path)
            try:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert before == 1

            # 再跑 chapter-write，期望 FAILED 且 drafts 行数不增
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

            conn = get_connection(app.state.settings.db_path)
            try:
                after = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()["n"]
            finally:
                conn.close()
            assert after == before, f"drafts 行数应不变（before={before}, after={after}）"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# run 列表端点：GET /runs
# ---------------------------------------------------------------------------


def test_list_runs_endpoint(tmp_path: Path):
    """GET /projects/{pid}/runs 列出该项目下的所有 workflow runs。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {"director": _director_script()}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(app, "GET", f"/api/projects/{pid}/runs")
            assert r.status_code == 200
            runs = r.json()
            assert len(runs) >= 1
            assert all(r["chapter_id"] == cid or r["chapter_id"] is None for r in runs)
            # 每行必须带 label（人类可读中文名）；chapter-plan run 的 label 应为 "生成计划"
            assert all("label" in r for r in runs)
            assert any(r["workflow_name"] == "chapter-plan" and r["label"] == "生成计划" for r in runs)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 草稿版本选择：review 显式带 draft_version 时评该版；指向不存在版本应失败
# ---------------------------------------------------------------------------


def test_chapter_review_with_draft_version_selects_specific_version(tmp_path: Path):
    """指定 draft_version=1 审 v1；不指定时审最新稿（v2）。

    场景：plan → write(v1) → 人工 POST drafts(v2) → review 带 draft_version=1
    → review_report.draft_version==1 且 label 为「审校（审 v1）」。
    再单独验证不带 draft_version → 审最新版（v2）。
    """
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
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            # 人工 POST drafts 产出 v2（特征 marker 便于断言）
            v2_marker = "【人工修订稿】苏婉清怒掷茶盏。"
            r = await _request(
                app, "POST", f"/api/chapters/{cid}/drafts",
                json={"content": v2_marker + "林渊默然。"},
            )
            assert r.status_code == 201, r.text

            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT version, content FROM drafts WHERE chapter_id = ? ORDER BY version",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert [d["version"] for d in drafts] == [1, 2], drafts

            # 1) review 带 draft_version=1 → 审 v1（review_report.draft_version==1, 不含 v2 marker）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers, "draft_version": 1},
            )
            assert r.status_code == 201, r.text
            paused1 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            run1_resp = await _request(app, "GET", f"/api/runs/{paused1['run_id']}")
            assert run1_resp.status_code == 200
            run1 = run1_resp.json()
            # review_report 落进 pause_payload（author_review payload）
            pp1 = run1.get("pause_payload") or {}
            rr1 = pp1.get("review_report") or {}
            assert rr1.get("draft_version") == 1, rr1
            # list 端点 label 应为「审校（审 v1）」
            list_resp = await _request(app, "GET", f"/api/projects/{pid}/runs")
            runs = list_resp.json()
            label1 = next(r["label"] for r in runs if r["run_id"] == paused1["run_id"])
            assert label1 == "审校（审 v1）", label1

            # 清理 reviewer（驳回 + 关 auto_revise），让能再次起 review
            r = await _request(
                app, "POST", f"/api/runs/{paused1['run_id']}/resume",
                json={"human_input": {"approved": False}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

            # 2) review 不带 draft_version → 审最新版 v2
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            run2_resp = await _request(app, "GET", f"/api/runs/{paused2['run_id']}")
            run2 = run2_resp.json()
            pp2 = run2.get("pause_payload") or {}
            rr2 = pp2.get("review_report") or {}
            assert rr2.get("draft_version") == 2, rr2
            list_resp = await _request(app, "GET", f"/api/projects/{pid}/runs")
            runs = list_resp.json()
            label2 = next(r["label"] for r in runs if r["run_id"] == paused2["run_id"])
            assert label2 == "审校（审 v2）", label2

    asyncio.run(run())


def test_chapter_review_with_nonexistent_draft_version_fails(tmp_path: Path):
    """指定 draft_version 不存在 → basic_checks 抛错 → run FAILED。"""
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
                await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers, "draft_version": 99},
            )
            assert r.status_code == 201, r.text
            failed = await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))
            assert "no draft version 99" in (failed.get("error") or ""), failed.get("error")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# writer 落库前确定性规整：「」对话标点归一 + 内部标识（recalled_passages 等）清除
# ---------------------------------------------------------------------------


def test_write_threads_author_intent_into_writer_payload_and_normalizes_draft(
    tmp_path: Path,
) -> None:
    """F-10 消费方闭环：端点收下的 author_intent 必须真进 writer payload。

    2026-09-16 新书01 实证（AGENTS.md 硬规则 9 同一形状第二次）：write 端点收下
    ``author_intent`` 写进 run ctx，但 ``build_writer_input`` 没有这个形参——
    本书铁律（价签四锁 / 字数带 / 文风 / 无CP）在 ch1-6 一个模型都没看见。
    本用例钉「端点参数必须有消费方」：撤掉 ``_writer_node`` 的
    ``author_intent=ctx.get("author_intent")`` 传参，用例必须转红。

    同时钉 F-10 ③ 确定性规整：writer 吐「」与内部标识 → 落库正文引号已归一为
    弯双引号，内部标识**只检出不改写**（删了句子就断）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "苏婉清")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            intent = "本书铁律：价签快照只给单一价格数字，绝不给状态与原因。"
            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script_with_quotes_and_leak(),
                "observer": _observer_noop_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"author_intent": intent, "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            write_run_id = r.json()["run_id"]
            await _wait_run_terminal(app, write_run_id, expected=("COMPLETED",))

            conn = get_connection(app.state.settings.db_path)
            try:
                draft = conn.execute(
                    "SELECT content, created_by, prompt_version FROM drafts "
                    "WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                node = conn.execute(
                    "SELECT output_json FROM workflow_run_nodes "
                    "WHERE run_id = ? AND node_id = 'writer'",
                    (write_run_id,),
                ).fetchone()
                save_node = conn.execute(
                    "SELECT output_json FROM workflow_run_nodes "
                    "WHERE run_id = ? AND node_id = 'save_draft'",
                    (write_run_id,),
                ).fetchone()
            finally:
                conn.close()

            assert draft is not None, "write 未落 draft"
            prose = draft["content"]

            # ① 引号归一化：writer 吐的「」必须已变弯双引号，且不再有「」
            assert "“你来晚了。”" in prose, f"引号未归一化：{prose[:80]!r}"
            assert "「" not in prose and "」" not in prose, "落库正文仍含角引号"

            # ② 内部标识只检出不改写（删了句子就断）
            assert "recalled_passages" in prose, "检出型缺陷不应被静默删除"

            assert node is not None, "writer 节点产出缺失"
            out = node["output_json"]
            payload = json.loads(out) if isinstance(out, str) else out

            # ③ 端点参数真的进了 writer payload（本用例的核心断言）
            got = (payload.get("writer_input") or {}).get("author_intent") or {}
            assert got.get("raw") == intent, (
                f"author_intent 没进 writer payload：实际={got!r}"
            )
            # ④ 规整与检出都进了节点产出（可观测）；二者挂在 save_draft 节点上
            assert save_node is not None, "save_draft 节点产出缺失"
            save_out = save_node["output_json"]
            save_payload = json.loads(save_out) if isinstance(save_out, str) else save_out
            changes = save_payload.get("normalize_changes") or []
            assert any(c.get("rule") == "NORM-QUOTE" for c in changes), changes
            assert save_payload.get("internal_identifiers") == ["recalled_passages"], (
                save_payload.get("internal_identifiers")
            )

            # ⑤ 版本口径统一：drafts.created_by / prompt_version 必须是**ACTIVE 提示词
            # 的真值**，不是硬编码 writer:v1（mock 路径无 ai_call_logs 行 → 回落
            # ACTIVE 标签；生产路径取本 run 实载版本）。期望值现取现算，本用例因此
            # 对 writer 未来升版免疫，但撤掉「取真值」的逻辑必红。
            from packages.core.agent_runtime.prompts import active_prompt_label

            expected = active_prompt_label(
                app.state.settings.db_path, "writer", fallback="writer:v1"
            )
            assert draft["created_by"] == expected, (
                f"created_by 未取真值：{draft['created_by']!r} != {expected!r}"
            )
            assert draft["prompt_version"] == expected, (
                f"prompt_version 未取真值：{draft['prompt_version']!r} != {expected!r}"
            )

    asyncio.run(run())
