"""V3.9 批次 4.1：quality_gate 阻断 → 改稿出路闭环（gate-revise）。

覆盖：
1. enforce 阻断：plan_json.revision_note 被写入（含 rule_id + 可执行建议）、
   plan_json.gate_blocked 标记（含 mode / rule_ids）；
2. 「按门禁建议改稿」触发路径：POST /gate-revise → write（revise 模式，产出 v2）→
   自动接力 chapter-review（PAUSED 等作者决议）→ 批准后 REVIEWED → 再次提交
   （report 模式）成功，且门禁通过时清除 gate_blocked 标记；
3. report 模式（不阻断）：不写 revision_note / gate_blocked；
4. 无 gate_blocked 标记时 POST /gate-revise → 409（不启动任何 run）；
5. auto_revise 回路（2026-09-18 策略表批次，端到端）：review 驳回后回路的 write 子 run
   在字数带下限缺口大到 capped revise（writer 规则 20：净增 ≤ +5%）追不回时，必须走
   ``fresh_write``（writer 收 mode='write'、无 draft_text / revision_note），而不是继续
   把旧稿按 revise 局部改。该分支现由 ``workflows/repair_policy.decide_repair`` 的
   ``length_shortfall_beyond_revise_cap`` 一行产出（策略表逐行覆盖见
   ``tests/unit/test_repair_policy.py``，四条端到端路径见 ``tests/api/test_repair_paths.py``）。

测试模式与 ``tests/api/test_quality.py`` 一致（httpx.ASGITransport + tmp_path +
mock_providers），helper 为独立副本，避免跨测试文件耦合。
"""

from __future__ import annotations

import asyncio
import json
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


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    import time

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


async def _make_project(app, name: str = "gate-revise 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "gate 章节") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _make_dead_character(app, pid: str) -> str:
    """建角色并把 character_states 置为 dead，供 observer 触发 RULE_CHAR_DEAD_ACTIVE。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "死角色", "role": "supporting"},
    )
    assert r.status_code == 201, r.text
    char_id = r.json()["character_id"]
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "UPDATE character_states SET state_json = ? WHERE character_id = ?",
            ('{"status":"dead"}', char_id),
        )
        conn.commit()
    finally:
        conn.close()
    return char_id


# ---------------------------------------------------------------------------
# 脚本（与 test_quality.py 同口径）
# ---------------------------------------------------------------------------


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": chapter_id,
            "chapter_goal": "演练 gate-revise 闭环",
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


def _writer_script(chapter_id: str, prose_suffix: str = "") -> list[str]:
    prose = (
        "无边的林海托着第一缕晨曦，远处的溪声把夜雾推向山脚，露珠沿着叶柄滑落。" + prose_suffix
    )
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
    """无 issue 的合规 critic 报告（接力审校不必真调 LLM）。"""
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


def _observer_dead_character_script(chapter_id: str, dead_char_id: str) -> list[str]:
    return [
        json.dumps({
            "character_changes": [
                {
                    "change_id": "cc_dead_1",
                    "op": "update",
                    "target_id": dead_char_id,
                    "character_id": dead_char_id,
                    "facet": "state",
                    "field": "state.location",
                    "before": {},
                    "after": {"location": "京城"},
                    "confidence": 0.9,
                    "evidence": {"chapter_id": chapter_id, "scene_id": "scene_001",
                                 "excerpt": "在京城出现", "span": {"start": 0, "end": 5}},
                    "risk_level": "LOW",
                    "notes": "尝试 set dead character activity",
                    "visibility": "VISIBLE",
                    "who_knows": None,
                    "reason": None,
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }, ensure_ascii=False)
    ]


async def _drive_plan_write_review(app, pid: str, cid: str, mock_providers: dict) -> str:
    """跑 plan → write → review(approve)；返回 review run_id。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "gate-revise", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    review = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{review['run_id']}/resume",
        json={"human_input": {"approved": True}, "auto_revise_max": 0},
    )
    assert r.status_code == 200, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    return review["run_id"]


async def _commit(app, pid: str, cid: str, mock_providers: dict, mode: str) -> dict:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
        json={"mock_providers": mock_providers, "quality_gate_mode": mode},
    )
    assert r.status_code == 201, r.text
    return await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))


async def _find_chained_review_run(app, pid: str, *, exclude_run_id: str) -> dict:
    """等 gate-revise 接力启动的 review run（PAUSED，且非 exclude_run_id）。"""
    import time

    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        r = await _request(app, "GET", f"/api/projects/{pid}/runs")
        assert r.status_code == 200, r.text
        candidates = [
            row for row in r.json()
            if row.get("workflow_name") == "chapter-review"
            and row["status"] == "PAUSED"
            and row["run_id"] != exclude_run_id
        ]
        if candidates:
            candidates.sort(key=lambda x: x.get("started_at") or "", reverse=True)
            return candidates[0]
        await asyncio.sleep(0.3)
    raise AssertionError("gate-revise 未在 120s 内接力出新的 PAUSED review run")


async def _find_new_chapter_write_run(app, pid: str, *, known: set[str]) -> dict:
    """等 auto_revise 回路启动的 chapter-write 子 run（run_id 不在 known 内）。"""
    import time

    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        r = await _request(app, "GET", f"/api/projects/{pid}/runs")
        assert r.status_code == 200, r.text
        candidates = [
            row for row in r.json()
            if row.get("workflow_name") == "chapter-write"
            and row["run_id"] not in known
        ]
        if candidates:
            candidates.sort(key=lambda x: x.get("started_at") or "", reverse=True)
            return candidates[0]
        await asyncio.sleep(0.3)
    raise AssertionError("auto_revise 未在 120s 内启动新的 chapter-write 子 run")


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


# ---------------------------------------------------------------------------
# 1. enforce 阻断落点 + gate-revise 闭环
# ---------------------------------------------------------------------------


def test_gate_enforce_block_writes_note_and_gate_revise_closes_loop(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            base_mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            first_review_run_id = await _drive_plan_write_review(app, pid, cid, base_mock)

            # --- enforce 阻断：FAILED + chapter 保持 REVIEWED
            blocked = await _commit(app, pid, cid, base_mock, "enforce")
            assert blocked["status"] == "FAILED", blocked
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # --- 落点 1：plan_json.revision_note（含 rule_id + 可执行建议）
            plan = r.json()["plan_json"]
            note = plan.get("revision_note")
            assert isinstance(note, str) and note, plan
            assert "质量门禁阻断" in note, note
            assert "RULE_CHAR_DEAD_ACTIVE" in note, note
            # --- 落点 2：gate_blocked 标记（mode + rule_ids 摘要）
            marker = plan.get("gate_blocked")
            assert isinstance(marker, dict), plan
            assert marker.get("mode") == "enforce", marker
            assert "RULE_CHAR_DEAD_ACTIVE" in (marker.get("rule_ids") or []), marker

            # --- 触发路径：一键按门禁建议改稿 → write（revise）→ 接力 review
            revise_mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid, prose_suffix="【按门禁建议改稿】"),
                "critic": _critic_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/gate-revise",
                json={"mock_providers": revise_mock},
            )
            assert r.status_code == 201, r.text
            write_run_id = r.json()["run_id"]
            write_run = await _wait_run_terminal(app, write_run_id, expected=("COMPLETED",))
            assert write_run["status"] == "COMPLETED", write_run

            # write 走 revise 模式：产出 v2 草稿，章节回退 DRAFTED
            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert r.status_code == 200, r.text
            versions = sorted(d["version"] for d in r.json())
            assert versions == [1, 2], versions

            # 接力审校：PAUSED 等作者决议
            chained = await _find_chained_review_run(
                app, pid, exclude_run_id=first_review_run_id
            )
            r = await _request(
                app, "POST", f"/api/runs/{chained['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # --- 再次提交（report 模式避开同一 error）→ 成功且标记被清除
            done = await _commit(app, pid, cid, base_mock, "report")
            assert done["status"] == "COMPLETED", done
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan_after = r.json()["plan_json"]
            assert "gate_blocked" not in plan_after, plan_after

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. report 模式（不阻断）不写落点
# ---------------------------------------------------------------------------


def test_gate_report_mode_does_not_write_plan_fields(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            await _drive_plan_write_review(app, pid, cid, mock)
            done = await _commit(app, pid, cid, mock, "report")
            assert done["status"] == "COMPLETED", done

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan = r.json()["plan_json"]
            assert "revision_note" not in plan, plan
            assert "gate_blocked" not in plan, plan

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 无阻断标记 → 409
# ---------------------------------------------------------------------------


def test_gate_revise_409_without_pending_block(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/gate-revise",
                json={},
            )
            assert r.status_code == 409, r.text
            assert "no pending quality-gate block" in r.json()["detail"]

            # 章节不存在 → 404（chapter 校验先于阻断标记校验）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/ch_nope/gate-revise",
                json={},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3b. gate-revise 透传 target_word_count / author_intent（2026-09-21 检修 m6）
# ---------------------------------------------------------------------------


def test_gate_revise_passes_target_word_count_and_author_intent(tmp_path: Path):
    """gate-revise 的 write 子 run 必须收到调用方给的 target / author_intent。

    缺陷形状（m6 亲验复现）：``GateReviseRequest`` 此前没有这两个字段，构造
    ``StartWorkflowRequest`` 时也不填 ⇒ 作者按非默认字数起稿后被门禁拦下，一键改稿
    时 target 退回服务端默认 3000、作者铁律整段丢失（改稿在错误口径 + 无约束下进行）。
    突变验证：撤掉 revise.py 的两处透传 → 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, name="gate-revise 透传项目")
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            await _drive_plan_write_review(app, pid, cid, mock)
            blocked = await _commit(app, pid, cid, mock, "enforce")
            assert blocked["status"] == "FAILED", blocked

            revise_mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid, prose_suffix="【改稿】"),
                "critic": _critic_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/gate-revise",
                json={
                    "mock_providers": revise_mock,
                    "target_word_count": 300,
                    "author_intent": "本书铁律：不许出现价签数字",
                },
            )
            assert r.status_code == 201, r.text
            write_run_id = r.json()["run_id"]
            await _wait_run_terminal(app, write_run_id, expected=("COMPLETED",))

            writer_input = _read_writer_input(str(app.state.settings.db_path), write_run_id)
            assert writer_input is not None, "writer 节点应落 writer_input"
            chapter_sec = writer_input.get("chapter") or {}
            assert chapter_sec.get("target_word_count") == 300, chapter_sec.get("target_word_count")
            assert writer_input.get("author_intent", {}).get("raw") == "本书铁律：不许出现价签数字", (
                writer_input.get("author_intent")
            )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. auto_revise 回路：字数大缺口 → 本轮 write 走 fresh_write（端到端）
# ---------------------------------------------------------------------------

def test_auto_revise_loop_fresh_writes_when_shortfall_outruns_the_revise_cap(
    tmp_path: Path,
):
    """review 驳回（改稿意见落 plan_json.revision_note）后，回路的 write 子 run 必须全新重写。

    事实链（与线上 ch_92bac068ff0d 同形，只是把缺口放大到极端）：mock 正文约 40 字，
    目标 3000（带 2550~3450）⇒ review 报 ``W-LEN-DEVIATION`` 且缺口 ≈ +6275%，
    远超回路 1 轮在 +5%（writer-v3.md 规则 20）下可达的 5% ⇒ 本轮必须带 ``fresh_write``。
    writer 收到 mode='write'、无 draft_text / revision_note 只可能来自该逃逸：不带逃逸时
    plan_json.revision_note + 既有 draft 会把它钉在 revise 模式（本地既有回归用例
    ``test_chapter_write_without_fresh_write_remains_revise`` 同口径）。

    突变验证：撤 ``revise.py`` 里按 ``decide_repair`` 的动作分派（把 regenerate 分支去掉）
    → writer_input['mode'] 实得 ``'revise'``（带 draft_text + revision_note）→ 本测试红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, name="auto-revise 逃逸项目")
            cid = await _make_chapter(app, pid)
            dead_char_id = await _make_dead_character(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "critic": _critic_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }

            # 1) plan → write v1（草稿落库）→ review（PAUSED 等作者决议）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "auto-revise 逃逸", "mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            review = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            # 前置事实：这份 review 真的按长度出带报了 W-LEN-DEVIATION（error 档）
            report = review["pause_payload"]["review_report"]
            assert report["within_range"] is False, report
            assert [e["rule_id"] for e in report["errors"]] == ["W-LEN-DEVIATION"], report

            known = {
                row["run_id"]
                for row in (await _request(app, "GET", f"/api/projects/{pid}/runs")).json()
            }

            # 2) 驳回并改稿 + auto_revise_max=1 → 回路在 daemon 线程跑 write → review
            r = await _request(
                app, "POST", f"/api/runs/{review['run_id']}/resume",
                json={
                    "human_input": {
                        "approved": False,
                        "revise": True,
                        "note": "正文严重欠带，请补足到带宽下限以上。",
                    },
                    "auto_revise_max": 1,
                    "mock_providers": mock,
                },
            )
            assert r.status_code == 200, r.text

            # 3) 回路的 write 子 run：writer 必须收 mode='write'（全新重写）
            child = await _find_new_chapter_write_run(app, pid, known=known)
            child_run = await _wait_run_terminal(app, child["run_id"], expected=("COMPLETED",))
            assert child_run["status"] == "COMPLETED", child_run
            writer_input = _read_writer_input(str(app.state.settings.db_path), child["run_id"])
            assert writer_input is not None, "writer 节点应落 writer_input"
            assert writer_input.get("mode") == "write", (
                f"大缺口改稿轮必须走 fresh_write ⇒ mode='write'，"
                f"实际 mode={writer_input.get('mode')!r}（draft_text="
                f"{bool(writer_input.get('draft_text'))!r}）"
            )
            assert not writer_input.get("draft_text"), writer_input
            assert "revision_note" not in writer_input, writer_input
            # 旧稿仍在（fresh_write 只是不喂给 writer，不删草稿）
            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert sorted(d["version"] for d in r.json()) == [1, 2], r.json()

    asyncio.run(run())
