"""chapter_review V1.3 deep_review 二审 AI 节点集成测试。

覆盖：
1. 缺省（body 不传 deep_review）→ deep_review_status='skipped'，payload 不含 deep_review_report。
2. body deep_review=False → 同上 skipped。
3. body deep_review=True + mock 合规 → pause_payload 含完整 deep_review_report。
4. body deep_review=True + mock 坏 JSON → deep_review_status='failed'，run 仍 PAUSED，三态审批照常。
5. verdict=revise 不自动驳回（advisory 原则）：deep_review_status='ok' verdict='revise'，人工仍可批准。
6. prompt sync：prompts 表出现 (deep_reviewer, v1) ACTIVE 行 + content 与源文件一致（防漂移）。
7. AGENT_CAPABILITY 同步：deep_reviewer → 'reasoning'（与 prompts.py / model_router 一致）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.agent_runtime.prompts import _AGENT_TO_CAPABILITY as PROMPTS_AGENT_TO_CAPABILITY
from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.router import AGENT_CAPABILITY


# ---------------------------------------------------------------------------
# 脚手架（裁剪自 test_chapter_review_critic.py，保持口径一致）
# ---------------------------------------------------------------------------


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
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})"
    )


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
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 复用 director / writer / critic scripts（与 critic 测试同口径）
# ---------------------------------------------------------------------------


PROSE_FOR_REVIEW = (
    "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
    "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
    "林渊立在博古架前，背对着她，似乎在翻检什么。松烟墨香被夜风裹着送进来，"
    "灯芯爆了一下花，啪地轻响。她开口问起父亲遗物。"
)


def _director_script() -> list[str]:
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
                        "purpose": "苏婉清察觉林渊回避",
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


def _writer_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": PROSE_FOR_REVIEW,
                "self_report": {
                    "slots_filled": ["slot_001"],
                    "word_count": len(PROSE_FOR_REVIEW),
                    "scene_count": 1,
                    "deviations": [],
                    "forbidden_word_hits": [],
                    "self_check_notes": "",
                },
            },
            ensure_ascii=False,
        )
    ]


def _critic_ok_script() -> list[str]:
    """合规 critic 输出：quote 必须是 PROSE_FOR_REVIEW 的真实连续子串。"""
    return [
        json.dumps(
            {
                "schema_version": "critic-report.v1",
                "prompt_version": "critic:v1",
                "chapter_id": "ch_xxx",
                "overall_comment": "节奏整体尚可，但末段伏笔推进不足。",
                "strengths": ["女主情绪位移有锚点"],
                "issues": [
                    {
                        "category": "ai_flavor",
                        "severity": "low",
                        "quote": "竹影斜斜地落在青石地砖上",
                        "suggestion": "删去或换成具体动作描写。",
                    },
                ],
            },
            ensure_ascii=False,
        )
    ]


def _deep_reviewer_pass_script() -> list[str]:
    """合规 deep_reviewer 输出：verdict=pass，无 issues。
    quote 全部为空串以避开溯源校验（pass 时 issues=[]，不需要 quote）。"""
    return [
        json.dumps(
            {
                "schema_version": "deep-review.v1",
                "prompt_version": "deep_reviewer:v1",
                "chapter_id": "ch_xxx",
                "verdict": "pass",
                "overall_comment": "三层均过，无硬伤。",
                "issues": [],
            },
            ensure_ascii=False,
        )
    ]


def _deep_reviewer_revise_script() -> list[str]:
    """合规 deep_reviewer 输出：verdict=revise，含一条 layer=behavior high（无源信息）+ 一条 layer=beat 缺失。
    high issue 的 quote 是 PROSE_FOR_REVIEW 真实子串；缺失型 quote 为空串。"""
    return [
        json.dumps(
            {
                "schema_version": "deep-review.v1",
                "prompt_version": "deep_reviewer:v1",
                "chapter_id": "ch_xxx",
                "verdict": "revise",
                "overall_comment": "存在无源信息硬伤且章末钩子拍缺失，建议驳回。",
                "issues": [
                    {
                        "layer": "behavior",
                        "severity": "high",
                        "quote": "她开口问起父亲遗物",
                        "suggestion": "据残影判断需符合 world_rule『残影只重演执念片段』，不能据此知晓父亲遗物细节。",
                    },
                    {
                        "layer": "beat",
                        "severity": "medium",
                        "quote": "",
                        "suggestion": "[beat 2 缺失] plan 要求『苏婉清察觉林渊回避』，正文未落地具体回避动作，请补一个。",
                    },
                ],
            },
            ensure_ascii=False,
        )
    ]


# ---------------------------------------------------------------------------
# 准备：跑 plan + write，落 draft
# ---------------------------------------------------------------------------


async def _plan_and_write(app, pid: str, cid: str) -> None:
    mock_providers = {
        "director": _director_script(),
        "writer": _writer_script(),
    }
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))


# ---------------------------------------------------------------------------
# 1. 缺省 → skipped
# ---------------------------------------------------------------------------


def test_deep_review_default_skipped(tmp_path: Path):
    """body 不传 deep_review → 默认跳过：deep_review_status='skipped'，
    pause_payload 中 deep_review_report 键存在但值为 None（与 critic_report 同口径）；
    ai_call_logs 无 deep_reviewer:v1 行。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _plan_and_write(app, pid, cid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _critic_ok_script()}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload is not None
            assert payload["deep_review_status"] == "skipped"
            assert payload["deep_review_report"] is None
            assert payload["deep_review_skipped"] is True
            # ai_call_logs 无 deep_reviewer 调用
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE prompt_version = ?",
                    ("deep_reviewer:v1",),
                ).fetchone()
            finally:
                conn.close()
            assert int(row["n"]) == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. body deep_review=False → skipped（与 1 等价）
# ---------------------------------------------------------------------------


def test_deep_review_explicit_false_skipped(tmp_path: Path):
    """body deep_review=False → 同默认 skipped。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _plan_and_write(app, pid, cid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "always",
                    "deep_review": False,
                },
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload["deep_review_status"] == "skipped"
            assert payload["deep_review_report"] is None
            assert payload["deep_review_skipped"] is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. deep_review=True + mock 合规 → pause_payload 含 deep_review_report
# ---------------------------------------------------------------------------


def test_deep_review_ok_report_in_pause_payload(tmp_path: Path):
    """mock deep_reviewer 合规（pass + 0 issues）→ pause_payload.deep_review_status='ok'
    + pause_payload 含 deep_review_report（schema_version=deep-review.v1）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _plan_and_write(app, pid, cid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {
                        "critic": _critic_ok_script(),
                        "deep_reviewer": _deep_reviewer_pass_script(),
                    },
                    "critic_mode": "always",
                    "deep_review": True,
                },
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload is not None
            assert payload["deep_review_status"] == "ok"
            assert payload["deep_review_skipped"] is False
            assert isinstance(payload["deep_review_report"], dict)
            dr = payload["deep_review_report"]
            assert dr["schema_version"] == "deep-review.v1"
            assert dr["prompt_version"] == "deep_reviewer:v1"
            assert dr["verdict"] == "pass"
            assert dr["overall_comment"]
            assert isinstance(dr["issues"], list) and len(dr["issues"]) == 0
            # review_report 仍存在
            assert "review_report" in payload

            # ai_call_logs 出现 deep_reviewer:v1 调用（status='ok'）
            conn = get_connection(app.state.settings.db_path)
            try:
                rows = conn.execute(
                    "SELECT prompt_version FROM ai_call_logs WHERE prompt_version = ?",
                    ("deep_reviewer:v1",),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) >= 1, "ai_call_logs 缺少 deep_reviewer:v1 调用记录"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. verdict=revise 时仍可人工批准（advisory 不驳回）
# ---------------------------------------------------------------------------


def test_deep_review_revise_does_not_auto_reject(tmp_path: Path):
    """verdict=revise + mock 报告含一条 high → pause_payload.deep_review_status='ok'
    + verdict='revise'；人工 approve 仍能走到 COMPLETED（advisory 原则）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _plan_and_write(app, pid, cid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {
                        "critic": _critic_ok_script(),
                        "deep_reviewer": _deep_reviewer_revise_script(),
                    },
                    "critic_mode": "always",
                    "deep_review": True,
                },
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload["deep_review_status"] == "ok"
            dr = payload["deep_review_report"]
            assert dr["verdict"] == "revise"
            issues = dr["issues"]
            # high + 缺失型 medium 各一
            assert any(i["layer"] == "behavior" and i["severity"] == "high" for i in issues)
            missing = [
                i for i in issues
                if i["layer"] == "beat"
                and i["suggestion"].startswith("[beat ")
                and "缺失" in i["suggestion"]
            ]
            assert missing, f"应有一条 beat 缺失型 issue; got {issues!r}"
            # high issue quote 必溯源
            for issue in issues:
                if issue.get("severity") == "high" and issue.get("quote"):
                    assert issue["quote"] in PROSE_FOR_REVIEW, (
                        f"high quote must be a substring of draft: {issue['quote']!r}"
                    )

            # 人工 approve 仍能走到 COMPLETED（advisory 不驳回）
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, paused["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. mock 坏 JSON → deep_review_status='failed'，run 仍 PAUSED，三态审批照常
# ---------------------------------------------------------------------------


def test_deep_review_bad_json_degrades_and_keeps_pause(tmp_path: Path):
    """mock deep_reviewer 连续两次非 JSON → 1 次重试仍失败 → deep_review_status='failed'，
    pause_payload.deep_review_report=None，review 仍 PAUSED，人工 approve 可走 COMPLETED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid)

            bad_script = ["not json at all", "still not json"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {
                        "critic": _critic_ok_script(),
                        "deep_reviewer": bad_script,
                    },
                    "critic_mode": "always",
                    "deep_review": True,
                },
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload["deep_review_status"] == "failed"
            assert payload["deep_review_report"] is None
            assert payload["deep_review_skipped"] is False
            # critic 仍正常
            assert payload["critic_status"] == "ok"
            # review_report 仍存在
            assert payload["review_report"]["chapter_id"] == cid

            # approve 仍能走到 COMPLETED
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, paused["run_id"], expected=("COMPLETED",))
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 6. prompt sync：prompts 表出现 deep_reviewer:v1 ACTIVE 行 + content 与源文件一致
# ---------------------------------------------------------------------------


def test_deep_reviewer_prompt_synced_and_matches_source(tmp_path: Path):
    """防漂移：sync_from_docs 后 (deep_reviewer, v1) ACTIVE 行 content 必须与源 .md 一致。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                agent_row = conn.execute(
                    "SELECT agent_id FROM agents WHERE name = ?", ("deep_reviewer",),
                ).fetchone()
                assert agent_row is not None, "deep_reviewer agent row missing in agents table"
                prompt_row = conn.execute(
                    """
                    SELECT content FROM prompts
                    WHERE agent_id = ? AND version = ? AND status = 'ACTIVE'
                    """,
                    (agent_row["agent_id"], "v1"),
                ).fetchone()
                assert prompt_row is not None, "deep_reviewer:v1 ACTIVE row missing"
                content = prompt_row["content"]
                source_path = (
                    Path.cwd().resolve() / "docs" / "agents" / "prompts" / "deep_reviewer-v1.md"
                )
                assert source_path.exists(), f"deep_reviewer source prompt missing: {source_path}"
                assert content == source_path.read_text(encoding="utf-8"), (
                    "prompts.deep_reviewer:v1 content drifted from docs/agents/prompts/deep_reviewer-v1.md"
                )
            finally:
                conn.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 7. AGENT_CAPABILITY 同步：deep_reviewer → 'reasoning'（与 prompts.py / model_router 一致）
# ---------------------------------------------------------------------------


def test_deep_reviewer_capability_is_reasoning():
    """AGENT_CAPABILITY 双源（prompts._AGENT_TO_CAPABILITY / model_router.AGENT_CAPABILITY）
    把 deep_reviewer 映射为 reasoning；二者必须完全相等（含 deep_reviewer 键）。"""
    assert PROMPTS_AGENT_TO_CAPABILITY.get("deep_reviewer") == "reasoning"
    assert AGENT_CAPABILITY.get("deep_reviewer") == "reasoning"
    # 漂移检测（与 test_model_router 的 test_ag_capability_matches_prompts 口径一致）
    assert PROMPTS_AGENT_TO_CAPABILITY == AGENT_CAPABILITY, (
        "AGENT_CAPABILITY drift detected between prompts._AGENT_TO_CAPABILITY "
        "and model_router.AGENT_CAPABILITY"
    )
