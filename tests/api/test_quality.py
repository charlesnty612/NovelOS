"""Quality API 集成测试（Sprint 6 下半 + Sprint 11 合规补丁）。

覆盖（任务书给死）：
- POST /api/chapters/{cid}/quality/evaluate — 全 pass 场景落库可查
- GET /api/chapters/{cid}/quality — 最新一份；不存在 → 404
- GET /api/projects/{pid}/quality — 列表按 created_at DESC
- POST evaluate — chapter 不存在 → 404
- chapter_commit pipeline：enforce 模式 error 阻断 + report 模式 error 不阻断
- 前置：调用方构造「会触发 quality error」的死角色 delta 通过 observer mock 注入。

新增（Sprint 11 PRD §125 合规）：
- GET /api/projects/{pid}/quality/q8-export — CSV 导出 happy path（含 BOM/表头/
  ratio 校验）+ project 不存在 → 404
- load_reference_texts 路径白名单防护（``../evil`` → 空列表不抛错）

测试模式与 ``tests/api/test_chapter_drafts.py`` 一致：httpx.ASGITransport +
``Settings(data_dir=tmp_path)`` 拉临时 db。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.service import load_reference_texts


# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload
async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


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


async def _make_project(app, name: str = "quality 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, *, status_value: str = "REVIEWED") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "quality chapter"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]
    if status_value != "PLANNED":
        # 推到 DRAFTED 再到目标态
        for nxt in (["DRAFTED"] + ([status_value] if status_value != "DRAFTED" else [])):
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"status": nxt}
            )
            assert r.status_code == 200, r.text
    return cid


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# =============================================================================
# 基础：evaluate + GET + list
# =============================================================================


def test_evaluate_then_latest_returns_report(tmp_path: Path):
    """调用 evaluate 落库，GET 立刻能拿到。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["project_id"] == pid
            assert "report_id" in body and body["report_id"].startswith("qr_")
            assert isinstance(body["scores_json"], dict)
            # six subscores + _meta
            for sub in ("plot", "character", "continuity", "style", "pacing", "foreshadowing"):
                assert sub in body["scores_json"], body["scores_json"]
            assert "_meta" in body["scores_json"]
            assert body["scores_json"]["_meta"]["scoring_version"] == "quality-scoring-v0"
            assert isinstance(body["issues_json"], list)

            # 再 GET 一次：应能拿到同一份
            r2 = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r2.status_code == 200, r2.text
            again = r2.json()
            assert again["report_id"] == body["report_id"]

    asyncio.run(run())


def test_latest_returns_404_when_no_report(tmp_path: Path):
    """chapter 存在但无 report → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 404
            assert "no quality report" in r.json()["detail"]

    asyncio.run(run())


# =============================================================================
# V3.1 P1-2: LLM judge 双轨并排
# =============================================================================


def test_get_chapter_quality_attaches_null_judge_when_empty(tmp_path: Path):
    """未经 judge 评审时，GET quality 响应顶层 judge 字段为 None（契约兼容）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            # 跑一次 evaluate 让 schema/双轨字段稳定
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201, r.text
            body = r.json()
            assert body.get("judge") is None, body

            # GET 也带 judge 字段
            r2 = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r2.status_code == 200, r2.text
            again = r2.json()
            assert "judge" in again, again
            assert again["judge"] is None

    asyncio.run(run())


def test_post_judge_endpoint_persists_and_returns(tmp_path: Path):
    """POST /api/chapters/{cid}/quality/judge 落库 + GET 返回 judge dict。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            # 先 evaluate
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201, r.text

            payload = {
                "pacing": 72, "style": 78, "logic": 70, "dialogue": 65,
                "verdict": "节奏平稳，开场铺设完整。",
                "top_issues": ["冲突未明确给出", "对话偏少", "悬念可加强"],
                "score_avg": 71.25,
                "dry_run": True,
            }
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/judge", json=payload)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["report_id"].startswith("qr_")
            assert isinstance(body["judge"], dict)
            assert body["judge"]["pacing"] == 72
            assert body["judge"]["verdict"] == "节奏平稳，开场铺设完整。"

            # GET 路径透出
            r2 = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r2.status_code == 200, r2.text
            again = r2.json()
            assert isinstance(again["judge"], dict)
            assert again["judge"]["dialogue"] == 65
            # 七子分 / overall / formula_hash 完全不变
            assert "scores_json" in again
            assert "plot" in again["scores_json"]
            assert "overall" in again
            # 再次落库覆盖（幂等覆盖）
            payload2 = dict(payload)
            payload2["pacing"] = 90
            r3 = await _request(
                app, "POST", f"/api/chapters/{cid}/quality/judge", json=payload2
            )
            assert r3.status_code == 200
            assert r3.json()["judge"]["pacing"] == 90

    asyncio.run(run())


def test_post_judge_422_missing_score_keys(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201

            bad = {"pacing": 70, "style": 70, "logic": 60}  # 缺 dialogue
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/judge", json=bad)
            assert r.status_code == 422, r.text
            assert "missing required score keys" in r.json()["detail"]

    asyncio.run(run())


def test_post_judge_404_for_unknown_chapter(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            payload = {"pacing": 70, "style": 70, "logic": 60, "dialogue": 50}
            r = await _request(
                app, "POST", "/api/chapters/ch_nope/quality/judge", json=payload
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_post_judge_422_when_chapter_has_no_report(tmp_path: Path):
    """chapter 存在但还没 evaluate → judge 落库拒绝（先跑 evaluate）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)  # PLANNED -> REVIEWED 但无 evaluate
            payload = {"pacing": 70, "style": 70, "logic": 60, "dialogue": 50}
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/judge", json=payload)
            assert r.status_code == 422, r.text
            assert "no quality report" in r.json()["detail"]

    asyncio.run(run())


def test_list_project_quality_attaches_judge_per_row(tmp_path: Path):
    """GET /api/projects/{pid}/quality 列表每行都带 judge 字段（无则 None）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201

            # 不写 judge
            r2 = await _request(app, "GET", f"/api/projects/{pid}/quality")
            assert r2.status_code == 200
            rows = r2.json()
            assert len(rows) == 1
            assert rows[0]["judge"] is None

            # 写 judge 后再 list
            payload = {"pacing": 80, "style": 75, "logic": 60, "dialogue": 50}
            r3 = await _request(app, "POST", f"/api/chapters/{cid}/quality/judge", json=payload)
            assert r3.status_code == 200
            r4 = await _request(app, "GET", f"/api/projects/{pid}/quality")
            assert r4.status_code == 200
            rows = r4.json()
            assert len(rows) == 1
            assert isinstance(rows[0]["judge"], dict)
            assert rows[0]["judge"]["pacing"] == 80

    asyncio.run(run())


def test_latest_returns_404_for_unknown_chapter(tmp_path: Path):
    """chapter 不存在 → 404（与 chapters router 风格一致）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/chapters/ch_nope/quality")
            assert r.status_code == 404
            assert "chapter" in r.json()["detail"]

    asyncio.run(run())


def test_evaluate_returns_404_for_unknown_chapter(tmp_path: Path):
    """evaluate 端点：chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/chapters/ch_nope/quality/evaluate")
            assert r.status_code == 404

    asyncio.run(run())


def test_list_reports_orders_by_created_at_desc(tmp_path: Path):
    """list 按 created_at DESC：同一 chapter 多份 report 后入先出。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            seen_report_ids: list[str] = []
            for _ in range(3):
                r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
                assert r.status_code == 201
                seen_report_ids.append(r.json()["report_id"])

            r = await _request(app, "GET", f"/api/projects/{pid}/quality")
            assert r.status_code == 200, r.text
            rows = r.json()
            assert len(rows) == 3
            actual_ids = [row["report_id"] for row in rows]
            # 倒序：最后一次 evaluate 的 report 排首位
            assert actual_ids == list(reversed(seen_report_ids)), (actual_ids, seen_report_ids)

    asyncio.run(run())


def test_list_reports_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_unknown/quality")
            assert r.status_code == 404

    asyncio.run(run())


# =============================================================================
# Pipeline enforce / report 模式（chapter-commit 节点级测试）
# =============================================================================


def _director_script(chapter_id: str) -> list[str]:
    """合规 director 输出（最低限度的 plan）。"""
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": chapter_id,
                "chapter_goal": "演练 quality gate",
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
            },
            ensure_ascii=False,
        )
    ]


def _writer_script(chapter_id: str) -> list[str]:
    """合规 writer 输出——一段不引用任何 binding 角色的散文。"""
    prose = "无边的林海托着第一缕晨曦，远处的溪声把夜雾推向山脚，露珠沿着叶柄滑落，碎成一道细小的微光。"
    return [
        json.dumps(
            {
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
            },
            ensure_ascii=False,
        )
    ]


def _observer_dead_character_script(chapter_id: str, dead_char_id: str) -> list[str]:
    """observer 试图给一个 'dead' 角色 update activity → 触发 RULE_CHAR_DEAD_ACTIVE error。

    与 ``packages/core/quality/guardrails.character_contradiction`` 对齐：
    - ``op == "update"`` 才会进入 dead 分支；
    - ``after`` 必须是 dict 含 ``location`` / ``goal`` / ``action`` 之一；
    - snapshot（即 character_states v1.state_json）含 ``status == 'death'``。
    - ``op=update`` 路径由 Validator 要求 ``before`` 非 None（schema 允许 null 但业务
      强制，故填空 dict 占位）。

    evidence 与 chapter_id 对齐，跑通 Validator ``[business] evidence.chapter_id == chapter_id``。
    """
    return [
        json.dumps(
            {
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
            },
            ensure_ascii=False,
        )
    ]


async def _drive_full_pipeline_to_committed_or_failed(
    app,
    pid: str,
    cid: str,
    mock_providers: dict,
    *,
    quality_gate_mode: str,
) -> httpx.Response:
    """完整跑 plan → write → review(approve) → commit；返回 commit 端点响应。

    前置：chapter 处于 PLANNED；本函数自管 plan→write→review→commit 全链路。

    设计：把 ``quality_gate_mode`` 仅在 commit 阶段传入（plan/write 阶段此参数无意义）；
    这样模式差异只影响 quality_gate 节点 + commit 联动。
    """
    # plan
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "test", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

    # write
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

    # review (PAUSED, then resume approved)
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    review_run = r.json()
    review_run = await _wait_run_terminal(app, review_run["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{review_run['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

    # commit
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
        json={"mock_providers": mock_providers,
              "quality_gate_mode": quality_gate_mode},
    )
    return r


def test_quality_gate_enforce_blocks_dead_character_delta(tmp_path: Path):
    """enforce 模式：observer 触发 dead character 写入 → quality_gate 阻断 → commit FAILED。

    注意：commit 之前的 plan / write 用合规 mock；commit 用触发 error 的 observer mock。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            # 建一个 dead 角色（SQL 改 character_states.state_json，让 guardrail 看到 status=dead）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "死角色", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            dead_char_id = r.json()["character_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                # QualityEngine char_guardrail 读 snapshot 当前 canonical state——
                # snapshot 由 StoryStateService 落盘 → 从 character_states.state_json 抽出。
                # 把 status=death 注入 state v1 行即可被识别为 dead。
                conn.execute(
                    "UPDATE character_states SET state_json = ? WHERE character_id = ?",
                    ('{"status":"dead"}', dead_char_id),
                )
                conn.commit()
            finally:
                conn.close()
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "qa enforce"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["chapter_id"]

            commit_resp = await _drive_full_pipeline_to_committed_or_failed(
                app, pid, cid,
                {
                    "director": _director_script(cid),
                    "writer": _writer_script(cid),
                    "observer": _observer_dead_character_script(cid, dead_char_id),
                },
                quality_gate_mode="enforce",
            )
            assert commit_resp.status_code == 201, commit_resp.text
            body = commit_resp.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("FAILED",))
            # chapter 保持 REVIEWED（blocked 后 commit node 不再执行）
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # quality_reports 仍有落库（即便阻断，report 必须先落，便于 UI 展示）
            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 200, r.text
            report = r.json()
            # 必须有 error 级 issue
            assert any(i["severity"] == "error" for i in report["issues_json"]), report

    asyncio.run(run())


def test_quality_gate_report_mode_does_not_block(tmp_path: Path):
    """report 模式：同样 dead character delta 不阻断 run，chapter 走完 → COMMITTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "死角色2", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            dead_char_id = r.json()["character_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE character_states SET state_json = ? WHERE character_id = ?",
                    ('{"status":"dead"}', dead_char_id),
                )
                conn.commit()
            finally:
                conn.close()
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "qa report"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["chapter_id"]

            commit_resp = await _drive_full_pipeline_to_committed_or_failed(
                app, pid, cid,
                {
                    "director": _director_script(cid),
                    "writer": _writer_script(cid),
                    "observer": _observer_dead_character_script(cid, dead_char_id),
                },
                quality_gate_mode="report",
            )
            assert commit_resp.status_code == 201, commit_resp.text
            commit_data = await _wait_run_terminal(app, commit_resp.json()["run_id"], expected=("COMPLETED",))
            assert commit_data["status"] == "COMPLETED", commit_data
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED", r.json()
            # report 已落库
            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 200
            assert any(
                i["severity"] == "error" for i in r.json()["issues_json"]
            )

    asyncio.run(run())


def test_quality_gate_default_mode_is_enforce(tmp_path: Path):
    """P0：不传入 quality_gate_mode 时默认 enforce，error 级 issue 阻断 commit。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "死角色默认", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            dead_char_id = r.json()["character_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE character_states SET state_json = ? WHERE character_id = ?",
                    ('{"status":"dead"}', dead_char_id),
                )
                conn.commit()
            finally:
                conn.close()
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "qa default enforce"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["chapter_id"]

            # 完整 pipeline，commit 请求体**不**传 quality_gate_mode
            # plan
            mock_providers = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_dead_character_script(cid, dead_char_id),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "test", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

            # write
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

            # review + approve
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            review_run = r.json()
            review_run = await _wait_run_terminal(app, review_run["run_id"], expected=("PAUSED",))
            r = await _request(
                app, "POST", f"/api/runs/{review_run['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))

            # commit：默认 enforce → 应 FAILED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            body = r.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("FAILED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

            # quality_reports 仍有落库
            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 200
            assert any(
                i["severity"] == "error" for i in r.json()["issues_json"]
            )

    asyncio.run(run())


# =============================================================================
# Sprint 11 合规补丁：Q8 CSV 导出 + references 路径防护
# =============================================================================


def _insert_drafts(
    db_path: Path,
    chapter_id: str,
    drafts: list[tuple[int, str, str]],
) -> None:
    """直接 INSERT drafts 避开 create_draft 状态机校验；测试专用 helper。

    ``drafts`` 每个元素 ``(version, content, created_by)``。``created_at`` 由这里统一
    设一个固定的 ISO 串便于断言。
    """
    conn = get_connection(db_path)
    try:
        for version, content, created_by in drafts:
            conn.execute(
                """
                INSERT INTO drafts
                    (draft_id, chapter_id, version, content, created_by,
                     prompt_version, model_id, created_at)
                VALUES
                    (:draft_id, :chapter_id, :version, :content, :created_by,
                     :prompt_version, :model_id, :created_at)
                """,
                {
                    "draft_id": new_id("dr"),
                    "chapter_id": chapter_id,
                    "version": int(version),
                    "content": content,
                    "created_by": created_by,
                    "prompt_version": None,
                    "model_id": None,
                    "created_at": now_iso(),
                },
            )
        conn.commit()
    finally:
        conn.close()


def test_q8_export_happy_path_two_chapters(tmp_path: Path):
    """q8-export：2 章各自已有 drafts + 1 章挂 report → CSV 头/列/比例/evaluated_at 全对。

    Chapter A 字数手算（与 ``compute_char_stats`` 算法一致）：
    - v1=agent: "人工智能写作工具辅助生成"   len=12 → ai 全量计 12
    - v2=human: 在该 12 字 prefix 后插入 ",并补充一段人工润色文字"（12 字）
      → human diff=12
    - ratio = 12/(12+12) = 50.0%

    Chapter B 字数手算：
    - v1=human: "" → human 全量=0（content 为空）
    - v2=agent: "AI 完全重写整章正文十八字"  len=14 → ai 全部 14
    - ratio = 0/(0+14) = 0.0%
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            # chapter A：v1=agent 12 字 → ai=12；v2=human diff 增量 12 字 → human=12
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "第一章 测试"},
            )
            assert r.status_code == 201, r.text
            cid_a = r.json()["chapter_id"]
            _insert_drafts(
                app.state.settings.db_path,
                cid_a,
                [
                    (1, "人工智能写作工具辅助生成", "agent:writer:v1"),
                    (2, "人工智能写作工具辅助生成，并补充一段人工润色文字", "human"),
                ],
            )

            # chapter B：v1=human 空 → human=0；v2=agent 全 14 字 → ai=14
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 2, "title": "第二章"},
            )
            assert r.status_code == 201, r.text
            cid_b = r.json()["chapter_id"]
            _insert_drafts(
                app.state.settings.db_path,
                cid_b,
                [
                    (1, "", "human"),
                    (2, "AI 完全重写整章正文十八字", "agent:writer:v1"),
                ],
            )

            # 给 chapter A 灌一份 report，验证 evaluated_at 非空；B 不灌，验证空串
            r = await _request(
                app, "POST", f"/api/chapters/{cid_a}/quality/evaluate",
            )
            assert r.status_code == 201, r.text

            r = await _request(
                app, "GET", f"/api/projects/{pid}/quality/q8-export"
            )
            assert r.status_code == 200, r.text
            assert "text/csv" in r.headers["content-type"]
            assert "charset=utf-8" in r.headers["content-type"]
            assert r.headers["content-disposition"] == (
                f'attachment; filename="q8-report-{pid}.csv"'
            )

            # 解码（剥 UTF-8 BOM）→ 按 csv.reader 解析
            raw = r.content
            assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须以 UTF-8 BOM 开头"
            text = raw.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)
            assert rows[0] == [
                "chapter_number", "chapter_title", "chapter_status",
                "ai_chars", "human_chars", "human_ratio", "evaluated_at",
            ]
            data_rows = rows[1:]
            assert len(data_rows) == 2

            # 章节按 number 升序：A(#1) 在前，B(#2) 在后
            row_a = data_rows[0]
            assert row_a[0] == "1"
            assert row_a[1] == "第一章 测试"
            assert int(row_a[3]) == 12  # ai_chars
            assert int(row_a[4]) == 12  # human_chars
            assert float(row_a[5]) == 50.0  # human_ratio %
            assert row_a[6] != ""  # 有 evaluate → evaluated_at 非空

            row_b = data_rows[1]
            assert row_b[0] == "2"
            assert row_b[1] == "第二章"
            assert int(row_b[3]) == 14  # ai_chars
            assert int(row_b[4]) == 0   # human_chars
            assert float(row_b[5]) == 0.0  # human_ratio %
            assert row_b[6] == ""  # 未评估 → 空串

    asyncio.run(run())


def test_q8_export_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "GET", "/api/projects/prj_nope/quality/q8-export"
            )
            assert r.status_code == 404
            assert "prj_nope" in r.json()["detail"]

    asyncio.run(run())


def test_q8_export_empty_project_returns_header_only(tmp_path: Path):
    """项目存在但无章节 → 只返回表头（让作者一眼看清「项目存在但还没章节」）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            r = await _request(
                app, "GET", f"/api/projects/{pid}/quality/q8-export"
            )
            assert r.status_code == 200, r.text
            text = r.content.decode("utf-8-sig")
            lines = [ln for ln in text.split("\n") if ln.strip()]
            assert len(lines) == 1
            assert "chapter_number" in lines[0]
            assert "human_ratio" in lines[0]

    asyncio.run(run())


def test_load_reference_texts_rejects_path_traversal(tmp_path: Path):
    """``load_reference_texts`` 在 project_id 不在白名单时返回空列表不抛错。

    安全审计 P2-2：避免任意路径穿越（例如 ``../evil``）。
    """
    refs_root = tmp_path / "references"
    refs_root.mkdir()
    # 假定一个意图穿越的目录确实存在；白名单校验在路径拼之前，不应读到它
    (refs_root / "evil").mkdir()
    (refs_root / "evil" / "secret.txt").write_text("不应被读到", encoding="utf-8")

    fake_db = tmp_path / "fake.sqlite"
    fake_db.write_bytes(b"")  # placeholder：路径校验在 db 打开前发生

    # 横线/字母数字字符仍可正常解析（接口兼容历史正例）
    assert load_reference_texts(fake_db, "good-id_123") == []  # 目录不存在 → 空列表
    # 白名单不匹配的输入全数返回空列表（容错语义保持）
    assert load_reference_texts(fake_db, "../evil") == []
    assert load_reference_texts(fake_db, "a/b") == []
    assert load_reference_texts(fake_db, "") == []
    # 白名单字符集在标准 Python re ``fullmatch`` 下严格：中文、点、空格均失败
    assert load_reference_texts(fake_db, "项目") == []


def test_load_reference_texts_reads_valid_project(tmp_path: Path):
    """白名单匹配的 project_id 仍正常读取参照书（不破坏既有功能）。"""
    refs_root = tmp_path / "references" / "good-id_123"
    refs_root.mkdir(parents=True)
    (refs_root / "a.txt").write_text("参照书内容A", encoding="utf-8")
    (refs_root / "b.txt").write_text("参照书内容B", encoding="utf-8")
    # 同目录下一个非白名单命名 .md 文件不应被读取
    (refs_root / "note.md").write_text("不读", encoding="utf-8")

    fake_db = tmp_path / "fake.sqlite"
    fake_db.write_bytes(b"")

    out = load_reference_texts(fake_db, "good-id_123")
    assert out == ["参照书内容A", "参照书内容B"]
