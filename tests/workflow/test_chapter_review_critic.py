"""chapter-review V1.3 critic 节点集成测试（Sprint 15）。

覆盖：
1. mock critic 输出合规 JSON → pause_payload 含 critic_status='ok' + critic_report。
2. mock critic 输出非 JSON（连续坏两次）→ critic_status='failed'，run 仍 PAUSED，
   人工三态审批照常工作（approve / reject / revise）。
3. critic prompt 缺失 → 同 2 降级路径。
4. run 完成后 ai_call_logs 出现 critic 调用行（prompt_version=critic:v1）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


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
# 共用 scripts：director / writer
# ---------------------------------------------------------------------------


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


PROSE_FOR_CRITIC = (
    "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
    "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。\n\n"
    "林渊立在博古架前，背对着她，似乎在翻检什么。松烟墨香被夜风裹着送进来，"
    "灯芯爆了一下花，啪地轻响。她开口问起父亲遗物。"
)


def _writer_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": PROSE_FOR_CRITIC,
                "self_report": {
                    "slots_filled": ["slot_001"],
                    "word_count": len(PROSE_FOR_CRITIC),
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
    """合规 critic 输出：quote 必须是 PROSE_FOR_CRITIC 的真实连续子串。"""
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
                    {
                        "category": "foreshadowing",
                        "severity": "high",
                        "quote": "她开口问起父亲遗物",
                        "suggestion": "让男主主动提一句玉佩。",
                    },
                ],
            },
            ensure_ascii=False,
        )
    ]


# ---------------------------------------------------------------------------
# 准备：跑 plan + write，落 draft
# ---------------------------------------------------------------------------


async def _plan_and_write(app, pid: str, cid: str, with_critic_script: list[str] | None):
    mock_providers = {
        "director": _director_script(),
        "writer": _writer_script(),
    }
    if with_critic_script is not None:
        mock_providers["critic"] = with_critic_script
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "COMPLETED"
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 1. critic 正常路径
# ---------------------------------------------------------------------------


def test_critic_ok_report_in_pause_payload(tmp_path: Path):
    """mock critic 合规 → pause_payload.critic_status='ok' + critic_report 含 2 issues。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                # V3 P0-1：显式固定 critic_mode=always，规避默认 sample 跳过（chapter.number=1 不命中 5）。
                json={"mock_providers": {"critic": _critic_ok_script()}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            payload = paused["pause_payload"]
            assert payload is not None
            assert payload["stage"] == "chapter-review"
            # V1.3 关键断言
            assert payload["critic_status"] == "ok"
            assert isinstance(payload["critic_report"], dict)
            cr = payload["critic_report"]
            assert cr["schema_version"] == "critic-report.v1"
            assert cr["prompt_version"] == "critic:v1"
            assert cr["overall_comment"], "overall_comment 应非空"
            assert isinstance(cr["strengths"], list) and len(cr["strengths"]) >= 1
            assert isinstance(cr["issues"], list) and len(cr["issues"]) == 2
            for issue in cr["issues"]:
                assert issue["category"] in (
                    "pacing", "character", "logic", "foreshadowing", "ai_flavor", "other"
                )
                assert issue["severity"] in ("high", "medium", "low")
                assert issue["quote"] in PROSE_FOR_CRITIC, (
                    f"quote 必须溯源到 draft_text: {issue['quote']!r}"
                )
            # review_report 仍然存在
            assert "review_report" in payload
            assert payload["review_report"]["chapter_id"] == cid

            # ai_call_logs 出现 critic:v1 调用（status='ok'）
            conn = get_connection(app.state.settings.db_path)
            try:
                rows = conn.execute(
                    "SELECT prompt_version FROM ai_call_logs WHERE prompt_version = ?",
                    ("critic:v1",),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) >= 1, "ai_call_logs 缺少 critic:v1 调用记录"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. critic 非 JSON（连续两次）→ 降级
# ---------------------------------------------------------------------------


def test_critic_non_json_degrades_and_keeps_pause(tmp_path: Path):
    """mock critic 输出非 JSON → 1 次重试仍失败 → critic_status='failed'，
    pause_payload 仍存在且三态审批正常工作。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            # write 阶段不配 critic mock（与当前代码不冲突）
            await _plan_and_write(app, pid, cid, with_critic_script=None)

            bad_script = ["not json at all", "still not json"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                # V3 P0-1：显式固定 always 验证 critic 降级路径（不被默认 sample 跳过拦截）
                json={"mock_providers": {"critic": bad_script}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            payload = paused["pause_payload"]
            assert payload is not None
            assert payload["critic_status"] == "failed"
            assert payload["critic_report"] is None
            # review_report 仍存在（与 critic 无关）
            assert payload["review_report"]["chapter_id"] == cid

            # approve 仍能走到 COMPLETED
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "COMPLETED"
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 三态审批在 critic 失败时仍工作：驳回 / 驳回并改稿
# ---------------------------------------------------------------------------


def test_critic_failed_reject_revise_still_works(tmp_path: Path):
    """critic 失败 → 驳回（approved=false）与驳回并改稿（revise:true）闭环仍成立。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid, with_critic_script=None)

            bad_script = ["not json", "still not json"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                # V3 P0-1：显式固定 always 验证 critic 降级 → revise 闭环
                json={"mock_providers": {"critic": bad_script}, "critic_mode": "always"},
            )
            paused = r.json()
            assert paused["pause_payload"]["critic_status"] == "failed"

            # revise:true 闭环
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": False, "revise": True, "note": "AI 评审失败仍可人工驳回"}},
            )
            assert r.status_code == 200, r.text
            r = await _request(app, "GET", f"/api/runs/{paused['run_id']}")
            assert r.json()["status"] == "FAILED"
            assert r.json()["error"] == "rejected-for-revision"
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"
            assert r.json()["plan_json"]["revision_note"] == "AI 评审失败仍可人工驳回"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. 不配 critic mock（mock_script 缺省）→ ModelRouter 走失败转移，失败降级
# ---------------------------------------------------------------------------


def test_critic_no_mock_degrades_safely(tmp_path: Path):
    """不配 critic mock：若 reasoning 模型配置存在则走 ModelRouter；否则降级。
    本测试用默认数据目录（无 model_config）→ 期望 critic_status='failed'，
    且 review 仍 PAUSED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid, with_critic_script=None)

            # 不配 critic mock：workflow ctx["mock_providers"]["critic"] 不存在
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                # V3 P0-1：显式固定 always 验证 ModelNotConfiguredError → failed 降级
                json={"mock_providers": {}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            # 无 model_config + 无 mock → ModelNotConfiguredError → 降级
            payload = paused["pause_payload"]
            assert payload["critic_status"] == "failed"
            assert payload["critic_report"] is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# V3 P0-1：critic_mode 三模式（off / sample / always）+ env fallback
# ---------------------------------------------------------------------------


async def _set_chapter_number(app, cid: str, number: int) -> None:
    """直接 SQL 把 chapters.number 改成目标值，便于构造 sample 命中/不命中。"""
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "UPDATE chapters SET number = ?, updated_at = ? WHERE chapter_id = ?",
            (number, "2026-08-24T00:00:00Z", cid),
        )
        conn.commit()
    finally:
        conn.close()


async def _count_critic_logs(app, prompt_version: str = "critic:v1") -> int:
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_call_logs WHERE prompt_version = ?",
            (prompt_version,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def test_critic_mode_off_skips_llm(tmp_path: Path):
    """critic_mode='off'：跳过 critic LLM 调用，pause_payload.critic_status='skipped'，
    ai_call_logs 中无 critic:v1 行。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "off",
                },
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            payload = paused["pause_payload"]
            assert payload["critic_status"] == "skipped"
            assert payload["critic_report"] is None
            assert payload["critic_mode"] == "off"
            assert payload["critic_skipped"] is True
            # 无 critic LLM 调用
            assert await _count_critic_logs(app) == 0

    asyncio.run(run())


def test_critic_mode_sample_only_runs_every_5(tmp_path: Path):
    """critic_mode='sample'：chapter.number=5 命中（调 LLM）；chapter.number=3 跳过。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            # chapter A：number=5 → 应调 critic
            cid_a = await _make_chapter(app, pid, 5, "第五章")
            await _make_character(app, pid)
            await _plan_and_write(app, pid, cid_a, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid_a}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "sample",
                },
            )
            assert r.status_code == 201, r.text
            payload_a = r.json()["pause_payload"]
            assert payload_a["critic_status"] == "ok"
            assert payload_a["critic_mode"] == "sample"
            assert payload_a["critic_skipped"] is False
            assert isinstance(payload_a["critic_report"], dict)
            assert await _count_critic_logs(app) == 1

            # chapter B：number=3 → 应跳过
            cid_b = await _make_chapter(app, pid, 3, "第三章")
            await _plan_and_write(app, pid, cid_b, _critic_ok_script())

            r2 = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid_b}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "sample",
                },
            )
            assert r2.status_code == 201, r2.text
            payload_b = r2.json()["pause_payload"]
            assert payload_b["critic_status"] == "skipped"
            assert payload_b["critic_report"] is None
            assert payload_b["critic_skipped"] is True
            # chapter A 已调过一次，B 跳过后总数仍为 1
            assert await _count_critic_logs(app) == 1

    asyncio.run(run())


def test_critic_mode_always_runs_regardless_of_number(tmp_path: Path):
    """critic_mode='always'：chapter.number=2（不命中 sample）也应调 critic。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 2, "第二章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "always",
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            assert payload["critic_status"] == "ok"
            assert payload["critic_mode"] == "always"
            assert payload["critic_skipped"] is False
            assert await _count_critic_logs(app) == 1

    asyncio.run(run())


def test_critic_mode_env_fallback_when_body_missing(tmp_path, monkeypatch):
    """body 不传 critic_mode 但 NOVELOS_CRITIC_MODE=off → 跳过；ctx > env 优先级验证。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            monkeypatch.setenv("NOVELOS_CRITIC_MODE", "off")
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 5, "第五章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _critic_ok_script()}},
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            assert payload["critic_status"] == "skipped"
            assert payload["critic_mode"] == "off"
            assert await _count_critic_logs(app) == 0

    asyncio.run(run())


def test_critic_mode_body_overrides_env(tmp_path, monkeypatch):
    """body=always 但 env=off：ctx 优先级 > env，应调 critic。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            monkeypatch.setenv("NOVELOS_CRITIC_MODE", "off")
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={
                    "mock_providers": {"critic": _critic_ok_script()},
                    "critic_mode": "always",
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            assert payload["critic_status"] == "ok"
            assert payload["critic_mode"] == "always"
            assert await _count_critic_logs(app) == 1

    asyncio.run(run())


def test_critic_default_mode_is_always_without_env_or_body(tmp_path: Path):
    """P0：不 env 不 body 时 critic 默认 always，chapter.number=3 也应调 critic。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 3, "第三章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _critic_ok_script()}},
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            assert payload["critic_status"] == "ok"
            assert payload["critic_mode"] == "always"
            assert payload["critic_skipped"] is False
            assert await _count_critic_logs(app) == 1

    asyncio.run(run())


def test_critic_mode_invalid_env_falls_back_to_always(tmp_path, monkeypatch):
    """env 是非法值 → 默认 always；chapter.number=3 也应调 critic。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            monkeypatch.setenv("NOVELOS_CRITIC_MODE", "garbage")
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 3, "第三章")
            await _plan_and_write(app, pid, cid, _critic_ok_script())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _critic_ok_script()}},
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            # 非法 env 值回退到默认 always；number=3 也调 critic
            assert payload["critic_status"] == "ok"
            assert payload["critic_mode"] == "always"
            assert await _count_critic_logs(app) == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# V3.8：review_report 集成验证（含 ai_pattern_hits）
# ---------------------------------------------------------------------------


def test_review_report_includes_ai_pattern_hits(tmp_path: Path):
    """完整 workflow 调用后，pause_payload.review_report 含 ai_pattern_hits 字段。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")
            # 在 draft 中故意加入 AI 腔词
            prose_with_ai_flavor = PROSE_FOR_CRITIC + "\n\n仿佛命运的齿轮已悄然转动。"
            script = [
                json.dumps(
                    {
                        "schema_version": "writer-output.v1",
                        "prompt_version": "writer:v1",
                        "chapter_id": "ch_xxx",
                        "prose": prose_with_ai_flavor,
                        "self_report": {
                            "slots_filled": ["slot_001"],
                            "word_count": len(prose_with_ai_flavor),
                            "scene_count": 1,
                            "deviations": [],
                            "forbidden_word_hits": [],
                            "self_check_notes": "",
                        },
                    },
                    ensure_ascii=False,
                )
            ]
            mock_providers = {
                "director": _director_script(),
                "writer": script,
                "critic": _critic_ok_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            payload = r.json()["pause_payload"]
            review_report = payload["review_report"]
            assert "ai_pattern_hits" in review_report
            rule_ids = {h["rule_id"] for h in review_report["ai_pattern_hits"]}
            assert "AI-FORBIDDEN-WORD" in rule_ids
            assert "仿佛" in review_report["forbidden_word_hits"]

    asyncio.run(run())
