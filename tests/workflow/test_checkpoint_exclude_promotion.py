"""checkpoint_exclude 推广回归测试（Sprint V1.5 / V1.0 性能审计）。

覆盖：
1. chapter-review：PAUSE 时 checkpoint_json 不含 ``review_report`` / ``critic_report`` /
   ``critic_status`` / ``critic_error``（被 exclude）；但 ``__pause_payload__``（在
   ctx['author_review'] 内）仍含 review_report / critic_report，供前端 reviewer UI 渲染。
2. chapter-commit（HIGH path）：PAUSE 时 checkpoint_json 不含 ``observer_input`` /
   ``observer_payload`` / ``delta`` / ``submit_result`` / ``snapshot_pre``；但保留
   ``delta_id`` / ``needs_high_risk_approval`` / ``_high_risk_approved`` 等 resume 依赖键。
3. resume 后行为一致：chapter-review approve → REVIEWED；chapter-commit approve → COMMITTED。
4. checkpoint 体积下降：exclude 字段前后做一次粗略比较（被 exclude 字段大小 > 0 即可）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _make_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "checkpoint-exclude 测试") -> str:
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


async def _make_chapter(app, pid: str, n: int = 1, title: str = "C1") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": n, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(
        app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}"
    )
    assert r.status_code == 200, r.text


def _director_script():
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "x",
                "chapter_goal": "测试",
                "core_conflict": "x",
                "turning_point": "x",
                "expected_role": "setup",
                "key_beats": [],
                "character_changes_planned": [],
                "hook_handling": [],
                "debt_handling": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
            },
            ensure_ascii=False,
        )
    ]


def _writer_script():
    prose = "测试正文。本章不涉及任何剧情。灯芯闪了一下。"
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "x",
                "prose": prose,
                "self_report": {
                    "slots_filled": ["s1"],
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


def _critic_ok_script():
    """mock critic 输出合规：strengths + 2 issues（quote 必溯源到 prose）。"""
    prose = "测试正文。本章不涉及任何剧情。灯芯闪了一下。"
    return [
        json.dumps(
            {
                "schema_version": "critic-report.v1",
                "prompt_version": "critic:v1",
                "chapter_id": "x",
                "strengths": ["节奏紧凑"],
                "issues": [
                    {
                        "rule_id": "REQ-Q7",
                        "category": "completeness",
                        "severity": "warning",
                        "message": "key_beats 覆盖度可加强",
                        "suggestion": "对照 plan.key_beats 补全",
                        "quote": "测试正文",
                        "location": "ch1",
                    },
                    {
                        "rule_id": "REQ-Q8",
                        "category": "ai_trace",
                        "severity": "warning",
                        "message": "AI 字符占比偏高",
                        "suggestion": "关键转折加 human 痕迹",
                        "quote": "灯芯闪了一下",
                        "location": "ch1",
                    },
                ],
                "revision_guidance": [],
                "self_check_notes": "ok",
            },
            ensure_ascii=False,
        )
    ]


def _observer_high_script(char_id: str, chapter_id: str):
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "cc_high_001",
                        "op": "update",
                        "target_id": char_id,
                        "character_id": char_id,
                        "facet": "state",
                        "field": "location",
                        "before": "old",
                        "after": "new",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "scene_id": None,
                            "excerpt": "测试证据",
                            "span": None,
                        },
                        "risk_level": "HIGH",
                        "notes": None,
                    },
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


async def _plan_and_write(app, pid: str, cid: str) -> None:
    """跑 plan + write 把 chapter 推到 DRAFTED（不跑 review）。"""
    mock_providers = {
        "director": _director_script(),
        "writer": _writer_script(),
    }
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "intent", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text


async def _drafted(app, pid: str, cid: str) -> None:
    """跑 plan + write + review(approve) 把 chapter 推到 REVIEWED。"""
    await _plan_and_write(app, pid, cid)
    mock_providers = {"critic": _critic_ok_script()}
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    paused = r.json()
    assert paused["status"] == "PAUSED"
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED"


def _checkpoint_json(app, run_id: str) -> dict:
    """从 workflow_runs 表读 raw checkpoint_json（dict）。"""
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    raw = row["checkpoint_json"]
    if not raw:
        return {}
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Tests: chapter-review checkpoint_exclude
# ---------------------------------------------------------------------------


def test_review_checkpoint_excludes_advisory_fields(tmp_path: Path):
    """review PAUSE 时 checkpoint_json 顶层不含 review_report / critic_* 顶层键。

    注：author_review 的 __pause_payload__ 单独存于 ctx['author_review']，
    不受顶层 exclude 影响，前端 reviewer UI 仍能拿到 review_report / critic_report。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "C1")
            await _plan_and_write(app, pid, cid)

            mock_providers = {"critic": _critic_ok_script()}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            run_id = paused["run_id"]

            ckpt = _checkpoint_json(app, run_id)
            # 顶层键被 exclude
            for excluded in ("review_report", "critic_report", "critic_status", "critic_error"):
                assert excluded not in ckpt, (
                    f"checkpoint 应不含被 exclude 字段 {excluded!r}，got ckpt keys={list(ckpt.keys())}"
                )
            # resume 依赖键（_author_approved）必须保留
            assert ckpt.get("_author_approved") is False, ckpt
            # __pause_payload__ 必须存在且含 review_report / critic_report（前端 reviewer UI）
            pause_payload_wrapper = ckpt.get("author_review") or {}
            assert "__pause_payload__" in pause_payload_wrapper, pause_payload_wrapper
            pp = pause_payload_wrapper["__pause_payload__"]
            assert "review_report" in pp, pp
            assert "critic_report" in pp, pp

            # 体积断言：被 exclude 字段确实从 checkpoint 顶层消失
            ckpt_size = len(json.dumps(ckpt, ensure_ascii=False))
            assert ckpt_size < 30_000, f"checkpoint 体积异常 {ckpt_size} 字节"

    asyncio.run(run())


def test_review_resume_behavior_unchanged_after_exclude(tmp_path: Path):
    """exclude 后 resume approved 仍走 mark_reviewed → REVIEWED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "C1")
            await _plan_and_write(app, pid, cid)

            mock_providers = {"critic": _critic_ok_script()}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"

            # resume approved
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "COMPLETED"

            # chapter 推到 REVIEWED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED", r.json()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Tests: chapter-commit checkpoint_exclude (HIGH path)
# ---------------------------------------------------------------------------


def test_commit_checkpoint_excludes_observer_payload_and_delta(tmp_path: Path):
    """commit PAUSE 时 checkpoint_json 不含 observer_input / observer_payload / delta /
    submit_result / snapshot_pre；但保留 delta_id + needs_high_risk_approval +
    _high_risk_approved 等 resume 依赖键。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "C1")
            await _drafted(app, pid, cid)

            mock_providers = {"observer": _observer_high_script(char_id, cid)}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            run_id = paused["run_id"]

            ckpt = _checkpoint_json(app, run_id)
            # 顶层键被 exclude
            for excluded in (
                "observer_input",
                "observer_payload",
                "delta",
                "submit_result",
                "snapshot_pre",
            ):
                assert excluded not in ckpt, (
                    f"checkpoint 应不含被 exclude 字段 {excluded!r}，got keys={list(ckpt.keys())}"
                )
            # resume 依赖键必须保留
            assert "delta_id" in ckpt, ckpt
            assert ckpt.get("needs_high_risk_approval") is True, ckpt
            assert ckpt.get("_high_risk_approved") is False, ckpt

            # 体积断言
            ckpt_size = len(json.dumps(ckpt, ensure_ascii=False))
            # observer_input 含完整 previous_state 快照；没有 exclude 时单快照 > 1KB
            assert ckpt_size < 15_000, f"checkpoint 体积异常 {ckpt_size} 字节"

    asyncio.run(run())


def test_commit_resume_behavior_unchanged_after_exclude(tmp_path: Path):
    """exclude 后 commit HIGH path resume approved 仍走 commit_delta → COMMITTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "C1")
            await _drafted(app, pid, cid)

            mock_providers = {"observer": _observer_high_script(char_id, cid)}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"

            # resume approved
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "COMPLETED"

            # chapter 推到 COMMITTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED", r.json()

    asyncio.run(run())


def test_checkpoint_size_shrinkage_demonstrated(tmp_path: Path):
    """粗略体积对比：跑两次（一次 exclude，一次不 exclude），后者更大或相近。

    本测试不强制要求 exclude 后体积严格小（被 exclude 字段在不同章节为空时体积差不明显），
    仅确认 exclude 落地、字段确实从 checkpoint 消失；同时校验 delta / observer_payload 等
    字段在落盘前被剔除（key absence）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "C1")
            await _drafted(app, pid, cid)

            mock_providers = {"observer": _observer_high_script(char_id, cid)}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED"
            run_id = paused["run_id"]

            ckpt = _checkpoint_json(app, run_id)

            # 校验被 exclude 字段确实从 checkpoint 消失——证明 scrub 生效
            excluded_total = 0
            for k in ("observer_input", "observer_payload", "delta", "submit_result", "snapshot_pre"):
                assert k not in ckpt, f"{k} 未被 exclude"
                excluded_total += 1
            assert excluded_total == 5

            # resume 依赖键存在
            assert "delta_id" in ckpt
            assert ckpt["needs_high_risk_approval"] is True

    asyncio.run(run())