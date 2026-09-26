"""失败形状 → 修复动作的端到端路径（2026-09-18 策略表批次）。

本文件测「服务端真的按形状走了不同的路」，不重复 ``tests/unit/test_repair_policy.py``
的纯函数覆盖。四个形状各一条：

1. **长度大缺口 ⇒ regenerate**：草稿远低于字数带下限、缺口超出 capped revise 可达幅度
   ⇒ 改稿轮的 write 子 run 必须带 ``fresh_write``（writer 收 ``mode='write'``、无
   ``draft_text`` / ``revision_note``）。
2. **只有标点问题 ⇒ revise**：报告 error 只有 ``AI-PUNCT-ABUSE`` 且字数在带内 ⇒
   改稿轮的 write 子 run 仍走 ``mode='revise'``（带旧稿 + 定向改稿意见）——**这是
   revise 真正擅长的事**，也是「一个字都不许改的旧行为」的正向看守。
3. **未知 rule_id ⇒ stop**：报告里出现策略表未覆盖的规则（连续性 / 设定类）⇒
   回路不启动任何子 run，把结论追加到该 run 的 ``error``（作者在 run 列表里读得到）。
4. **目标字数透传**：父 review run 的 ``target_word_count``（resume 未显式给）必须
   被改稿子 run 继承——否则子 run 按服务端默认 3000 判字数带，回路在错误口径上判定成败。

测试模式与 ``tests/api/test_gate_revise_closure.py`` 一致（httpx.ASGITransport + tmp_path
+ mock_providers），helper 为独立副本，避免跨测试文件耦合。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _get_run(app, run_id: str) -> dict:
    r = await _request(app, "GET", f"/api/runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _wait_run_terminal(
    app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0
) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await _get_run(app, run_id)
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s (last={last['status']!r})"
    )


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str, *, word_band: dict | None = None) -> str:
    body: dict = {"name": name}
    if word_band is not None:
        body["word_band"] = word_band
    r = await _request(app, "POST", "/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "策略章节") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _list_runs(app, pid: str) -> list[dict]:
    r = await _request(app, "GET", f"/api/projects/{pid}/runs")
    assert r.status_code == 200, r.text
    return r.json()


async def _wait_new_run(
    app, pid: str, *, workflow_name: str, known: set[str], timeout: float = 90.0
) -> dict:
    """等一个「不在 known 内」的指定 workflow 子 run 出现。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [
            row for row in await _list_runs(app, pid)
            if row.get("workflow_name") == workflow_name and row["run_id"] not in known
        ]
        if rows:
            rows.sort(key=lambda x: x.get("started_at") or "", reverse=True)
            return rows[0]
        await asyncio.sleep(0.3)
    raise AssertionError(f"{workflow_name} 子 run 未在 {timeout}s 内出现")


# ---------------------------------------------------------------------------
# 脚本（与 test_gate_revise_closure.py 同口径，含标点专版正文）
# ---------------------------------------------------------------------------


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": chapter_id,
            "chapter_goal": "演练修复策略路径",
            "core_conflict": "无",
            "turning_point": "无",
            "expected_role": "setup",
            "key_beats": [{"beat_id": "beat_001", "purpose": "测试", "involved_characters": [],
                           "involved_locations": [], "involved_hooks": [], "involved_debts": [],
                           "risk_level": "LOW", "narrative_question_served": "测试"}],
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "",
        }, ensure_ascii=False)
    ]


# 标点专版正文：60 可见字、带 2 处破折号 ⇒ 确定性扫描只报一条 error（AI-PUNCT-ABUSE）。
# 句首不重复、无长段 / 短段、无远距复现——本用例要隔离的形状是**标点**。
PUNCT_PROSE = (
    "雨还没停——他把伞收了，檐水顺着瓦当滴下来，在青石上砸出一个个小坑。"
    "灯影晃了晃——铺子里那口旧钟走慢了半刻，他也不去拨。"
)


def _writer_script(chapter_id: str, prose: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": chapter_id,
            "prose": prose,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(prose),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }, ensure_ascii=False)
    ]


def _critic_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": chapter_id,
            "overall_comment": "无阻断性意见",
            "strengths": [],
            "issues": [],
        }, ensure_ascii=False)
    ]


def _observer_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "character_changes": [], "world_changes": [], "relationship_changes": [],
            "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
        }, ensure_ascii=False)
    ]


def _mocks(chapter_id: str, prose: str) -> dict[str, list[str]]:
    return {
        "director": _director_script(chapter_id),
        "writer": _writer_script(chapter_id, prose),
        "critic": _critic_script(chapter_id),
        "observer": _observer_script(chapter_id),
    }


async def _drive_to_review(app, pid: str, cid: str, mocks: dict, target: int | None) -> dict:
    """plan → write → review（PAUSED）；返回 PAUSED 的 review run。"""
    plan_body: dict = {"author_intent": "策略路径演练", "mock_providers": mocks}
    if target is not None:
        plan_body["target_word_count"] = target
    r = await _request(app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan", json=plan_body)
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    write_body: dict = {"mock_providers": mocks}
    if target is not None:
        write_body["target_word_count"] = target
    r = await _request(app, "POST", f"/api/projects/{pid}/chapters/{cid}/write", json=write_body)
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    review_body: dict = {"mock_providers": mocks}
    if target is not None:
        review_body["target_word_count"] = target
    r = await _request(app, "POST", f"/api/projects/{pid}/chapters/{cid}/review", json=review_body)
    assert r.status_code == 201, r.text
    return await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))


def _read_writer_input(db_path: str, run_id: str) -> dict | None:
    """从 writer 节点的 ``output_json`` 读 writer_input（该节点产出的权威副本）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT output_json FROM workflow_run_nodes "
            "WHERE run_id = ? AND node_id = 'writer' ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["output_json"]:
        return None
    return json.loads(row["output_json"]).get("writer_input")


def _patch_review_report(db_path: str, run_id: str, mutate) -> dict:
    """把该 review run 的 pause payload 里的 ``review_report`` 改写成 ``mutate(report)``。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row is not None, run_id
        checkpoint = json.loads(row["checkpoint_json"])
        report = checkpoint["author_review"]["__pause_payload__"]["review_report"]
        mutate(report)
        checkpoint["author_review"]["__pause_payload__"]["review_report"] = report
        conn.execute(
            "UPDATE workflow_runs SET checkpoint_json = ? WHERE run_id = ?",
            (json.dumps(checkpoint, ensure_ascii=False), run_id),
        )
        conn.commit()
        return report
    finally:
        conn.close()


async def _reject_and_revise(app, run_id: str, mocks: dict, *, auto_revise_max: int = 1, **extra):
    r = await _request(
        app, "POST", f"/api/runs/{run_id}/resume",
        json={
            "human_input": {"approved": False, "revise": True, "note": "请按报告修改。"},
            "auto_revise_max": auto_revise_max,
            "mock_providers": mocks,
            **extra,
        },
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 1. 长度大缺口（默认 3000 目标 + 短文）⇒ regenerate
# ---------------------------------------------------------------------------


def test_length_short_chapter_regenerates_the_child_write(tmp_path: Path):
    """改稿轮的 write 子 run 必须全新重写（``mode='write'``、无 draft_text / revision_note）。

    事实链：mock 正文约 60 字、目标 3000（带下限 2550）⇒ 缺口 ≈ +4150%，远超 1 轮在
    +5%（writer-v3.md 规则 20）下可达的幅度 ⇒ 策略判 ``length_shortfall_beyond_revise_cap``
    ⇒ regenerate。writer 收到 mode='write' 且没有旧稿 / 改稿意见，只可能来自该分支。

    突变验证：撤 ``decide_repair`` 的长度分支（或把 regenerate 改回只 revise）→
    writer_input['mode'] 实得 ``'revise'`` → 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "长度大缺口项目")
            cid = await _make_chapter(app, pid)
            mocks = _mocks(cid, PUNCT_PROSE)

            review = await _drive_to_review(app, pid, cid, mocks, target=None)
            report = review["pause_payload"]["review_report"]
            assert report["word_band"]["low"] == 2550, report
            assert report["word_count"] < report["word_band"]["low"], report

            known = {row["run_id"] for row in await _list_runs(app, pid)}
            await _reject_and_revise(app, review["run_id"], mocks)

            child = await _wait_new_run(
                app, pid, workflow_name="chapter-write", known=known
            )
            child_run = await _wait_run_terminal(app, child["run_id"], expected=("COMPLETED",))
            assert child_run["status"] == "COMPLETED", child_run

            writer_input = _read_writer_input(str(app.state.settings.db_path), child["run_id"])
            assert writer_input is not None, "writer 节点应落 writer_input"
            assert writer_input.get("mode") == "write", (
                f"长度大缺口必须走 regenerate ⇒ mode='write'，"
                f"实际 mode={writer_input.get('mode')!r}"
            )
            assert not writer_input.get("draft_text"), writer_input
            assert "revision_note" not in writer_input, writer_input

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. 只有标点问题（字数在带内）⇒ revise
# ---------------------------------------------------------------------------


def test_punctuation_only_problem_takes_the_revise_path(tmp_path: Path):
    """报告 error 只有 ``AI-PUNCT-ABUSE`` 且字数在带内 ⇒ 子 run 走 ``mode='revise'``。

    这是 revise 的正向看守：局部形态类问题**不该**触发重产（重产会丢掉已认可的正文，
    且是重复的温床）。本用例把项目字数带覆盖成 30~114（项目级 ``word_band``，V3.7
    能力），让 60 字的短稿落在带内——要隔离的形状是**标点**，不是长度。

    突变验证：把 ``decide_repair`` 的 ``surgical_style_rules`` 分支改成 regenerate →
    writer_input['mode'] 实得 ``'write'``（且 draft_text 为空）→ 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(
                app, "标点项目",
                word_band={"low_ratio": 0.1, "high_ratio": 1.15, "floor": 30},
            )
            cid = await _make_chapter(app, pid)
            mocks = _mocks(cid, PUNCT_PROSE)

            review = await _drive_to_review(app, pid, cid, mocks, target=100)
            report = review["pause_payload"]["review_report"]
            assert report["within_range"] is True, report
            assert [e["rule_id"] for e in report["errors"]] == ["AI-PUNCT-ABUSE"], report

            known = {row["run_id"] for row in await _list_runs(app, pid)}
            await _reject_and_revise(app, review["run_id"], mocks)

            child = await _wait_new_run(app, pid, workflow_name="chapter-write", known=known)
            child_run = await _wait_run_terminal(app, child["run_id"], expected=("COMPLETED",))
            assert child_run["status"] == "COMPLETED", child_run

            writer_input = _read_writer_input(str(app.state.settings.db_path), child["run_id"])
            assert writer_input is not None, "writer 节点应落 writer_input"
            assert writer_input.get("mode") == "revise", (
                f"局部形态类问题必须走 revise ⇒ mode='revise'，"
                f"实际 mode={writer_input.get('mode')!r}"
            )
            assert writer_input.get("draft_text"), writer_input
            assert writer_input.get("revision_note"), writer_input

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 未知 rule_id（连续性 / 设定类）⇒ stop，不启动任何子 run
# ---------------------------------------------------------------------------


def test_unknown_rule_id_stops_the_loop_without_child_runs(tmp_path: Path):
    """报告含策略表未覆盖的 rule_id ⇒ 回路停在人工：不启动子 run，结论落到 run.error。

    报告形状用 DB 直写注入（``RULE_CHAR_DEAD_ACTIVE`` 无法由 mock 正文稳定触发）：
    注入点与 ``_extract_pause_payload`` 的读点同形（``author_review.__pause_payload__``），
    即前端 reviewer UI / resume 用的是同一份报告。

    突变验证：撤 ``_auto_revise_loop`` 里 ``decision.action == "stop"`` 的短路分支 →
    子 run 会被启动 → 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "未知规则项目")
            cid = await _make_chapter(app, pid)
            mocks = _mocks(cid, PUNCT_PROSE)

            review = await _drive_to_review(app, pid, cid, mocks, target=100)
            db_path = str(app.state.settings.db_path)

            def _inject(report: dict) -> None:
                report["errors"] = [
                    {
                        "rule_id": "RULE_CHAR_DEAD_ACTIVE",
                        "severity": "error",
                        "message": "已死亡角色被写活动字段",
                    }
                ]
                report["within_range"] = True

            _patch_review_report(db_path, review["run_id"], _inject)
            known = {row["run_id"] for row in await _list_runs(app, pid)}

            await _reject_and_revise(app, review["run_id"], mocks)

            # 正向信号：回路把结论追加到该 run 的 error（= stop 分支已走完）
            deadline = time.monotonic() + 60.0
            parent_error = ""
            while time.monotonic() < deadline:
                parent_error = (await _get_run(app, review["run_id"])).get("error") or ""
                if "auto_revise:" in parent_error:
                    break
                await asyncio.sleep(0.2)
            assert "auto_revise:" in parent_error, parent_error
            assert "unknown_rule_id" in parent_error, parent_error
            assert "RULE_CHAR_DEAD_ACTIVE" in parent_error, parent_error

            # 硬停的证据：没有任何新的 write / review 子 run
            rows = await _list_runs(app, pid)
            new_children = [
                row for row in rows
                if row["run_id"] not in known
                and row.get("workflow_name") in ("chapter-write", "chapter-review")
            ]
            assert new_children == [], new_children

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. 目标字数透传：子 run 必须按父 run 的目标判字数带
# ---------------------------------------------------------------------------


def test_target_word_count_survives_into_child_runs(tmp_path: Path):
    """resume 未显式给 target 时，改稿子 run 必须继承父 review run 的 ``target_word_count``。

    线上实证（另一路端到端）：作者按 ``--target-word-count 300`` 起稿，首轮 review 判
    「288/300 字」，改稿回路的 review 子 run 却判「288/3000 字」——子 run 拿不到 target
    就退回服务端默认 3000，**回路据此在错误口径上判定成败**。

    断言的是**子 run 的判据**（pause payload 里 ``review_report.target_word_count``），
    不是「子 run 存在」。取 1000 作父目标：既不是默认 3000，也不是任何题材包 fallback，
    缺失透传时必然现形。

    突变验证：撤 ``control.py`` 里 ``effective_target_word_count`` 的解析与透传（或撤
    ``revise.py`` 中把该键并入 ``initial_ctx_extra`` 的两行）→ 子 run 报告
    ``target_word_count`` 实得 3000 → 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "目标字数透传项目")
            cid = await _make_chapter(app, pid)
            mocks = _mocks(cid, PUNCT_PROSE)

            review = await _drive_to_review(app, pid, cid, mocks, target=1000)
            parent_report = review["pause_payload"]["review_report"]
            assert parent_report["target_word_count"] == 1000, parent_report

            known = {row["run_id"] for row in await _list_runs(app, pid)}
            # resume 请求体**不带** target_word_count ⇒ 只能从父 run 的 ctx 继承
            await _reject_and_revise(app, review["run_id"], mocks)

            # 写侧：改稿子 run 的 writer_input 必须带同一个目标字数
            child_write = await _wait_new_run(
                app, pid, workflow_name="chapter-write", known=known
            )
            write_run = await _wait_run_terminal(
                app, child_write["run_id"], expected=("COMPLETED",)
            )
            assert write_run["status"] == "COMPLETED", write_run
            writer_input = _read_writer_input(str(app.state.settings.db_path), child_write["run_id"])
            assert writer_input is not None, "writer 节点应落 writer_input"
            # writer payload 的目标字数落点在 ``chapter.target_word_count``（装配层口径），
            # 与之配套的 ``chapter.word_band`` 也必须跟着父目标走（1000×0.85=850 → floor 1200）。
            assert writer_input["chapter"]["target_word_count"] == 1000, writer_input["chapter"]
            assert writer_input["chapter"]["word_band"]["low"] == 1200, writer_input["chapter"]

            # 审校侧：子 run 判字数带用的目标字数（判据本身，不是「子 run 存在」）
            child_review = await _wait_new_run(
                app, pid, workflow_name="chapter-review", known=known
            )
            child_run = await _wait_run_terminal(
                app, child_review["run_id"], expected=("PAUSED",)
            )
            assert child_run["status"] == "PAUSED", child_run
            child_report = child_run["pause_payload"]["review_report"]
            assert child_report["target_word_count"] == 1000, (
                f"改稿子 run 必须继承父 run 的目标字数（1000），"
                f"实际 {child_report['target_word_count']!r}（= 服务端默认 3000 即透传丢失）"
            )
            # 同一份判据的字数带随之下移：raw 下限 1000×0.85=850 被 writer 纪律 Rule 15
            # 的下限保护抬到 1200（``_MIN_BAND_FLOOR``）——证明子 run 的 target 不是 3000
            # （若为 3000 则下限同样是 2550 减去 floor 后的值，与本断言不符）。
            assert child_report["word_band"]["low"] == 1200, child_report

    asyncio.run(run())


def test_resume_body_target_word_count_overrides_the_inherited_one(tmp_path: Path):
    """resume 请求体显式给 target ⇒ 以请求体为准（与 author_intent / overrides 同优先级）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "目标字数覆盖项目")
            cid = await _make_chapter(app, pid)
            mocks = _mocks(cid, PUNCT_PROSE)

            review = await _drive_to_review(app, pid, cid, mocks, target=1000)
            known = {row["run_id"] for row in await _list_runs(app, pid)}
            await _reject_and_revise(app, review["run_id"], mocks, target_word_count=1200)

            child_review = await _wait_new_run(
                app, pid, workflow_name="chapter-review", known=known
            )
            child_run = await _wait_run_terminal(
                app, child_review["run_id"], expected=("PAUSED",)
            )
            child_report = child_run["pause_payload"]["review_report"]
            assert child_report["target_word_count"] == 1200, child_report

    asyncio.run(run())
