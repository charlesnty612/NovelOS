"""V3.9 批次 4.2：critic 建议去重（plan_json.suggested_hashes）。

覆盖：
- 纯函数：``_suggestion_hash`` 归一化口径（首尾空白 / 内部空白不影响 hash）；
  ``_merge_suggested_hashes`` 去重、跳过空行、尾部截断到上限；
- 端到端：review 驳回并改稿（note=critic 建议原文）→ plan_json.suggested_hashes
  落库 → 新一轮 review 的 critic 报告过滤掉同内容建议、保留新建议，并带
  ``deduped_suggestion_count`` 观测字段。

测试模式与 ``tests/api/test_quality.py`` 一致（httpx.ASGITransport + tmp_path + mock）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.workflows.chapter_review.pipeline import (
    _SUGGESTED_HASH_LIMIT,
    _merge_suggested_hashes,
    _suggestion_hash,
)

_PROSE = "无边的林海托着第一缕晨曦，远处的溪声把夜雾推向山脚，露珠沿着叶柄滑落，碎成一道细小的微光。"
_SUGGESTION_REPEAT = "让男主主动提一句玉佩。"
_SUGGESTION_NEW = "删去或换成具体动作描写。"


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_suggestion_hash_normalization_is_stable():
    """strip + 内部空白折叠后同 hash；不同内容不同 hash；长度 12。"""
    assert _suggestion_hash(f"  {_SUGGESTION_REPEAT}  ") == _suggestion_hash(_SUGGESTION_REPEAT)
    assert _suggestion_hash("删去 或换成\n具体动作描写。") == _suggestion_hash("删去 或换成 具体动作描写。")
    assert _suggestion_hash(_SUGGESTION_REPEAT) != _suggestion_hash(_SUGGESTION_NEW)
    assert len(_suggestion_hash(_SUGGESTION_REPEAT)) == 12


def test_merge_suggested_hashes_dedupes_and_caps():
    """逐行并入、去重、跳过空行、保留顺序、尾部截断到上限。"""
    h1 = _suggestion_hash("A")
    h2 = _suggestion_hash("B")
    assert _merge_suggested_hashes(None, "A\n\n  \nB\n") == [h1, h2]
    # 已存在的不重复追加（覆盖语义：同一建议不叠写）
    assert _merge_suggested_hashes([h1], "A\nB") == [h1, h2]
    # 上限：尾部截断（保留最新的 LIMIT 条）
    many = "\n".join(f"建议 {i}" for i in range(_SUGGESTED_HASH_LIMIT + 5))
    merged = _merge_suggested_hashes(None, many)
    assert len(merged) == _SUGGESTED_HASH_LIMIT


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


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


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = await _request(app, "GET", f"/api/runs/{run_id}")
        assert r.status_code == 200, r.text
        last = r.json()
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


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": chapter_id,
            "chapter_goal": "演练 critic 建议去重",
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


def _writer_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": chapter_id,
            "prose": _PROSE,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(_PROSE),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }, ensure_ascii=False)
    ]


def _critic_script(chapter_id: str) -> list[str]:
    """两条建议：一条与将采纳的 revision_note 同内容（应被过滤），一条为新建议。"""
    return [
        json.dumps({
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": chapter_id,
            "overall_comment": "节奏尚可，末段描写可更具体。",
            "strengths": ["开篇场景有画面感"],
            "issues": [
                {
                    "category": "foreshadowing",
                    "severity": "high",
                    "quote": "无边的林海托着第一缕晨曦",
                    "suggestion": _SUGGESTION_REPEAT,
                },
                {
                    "category": "ai_flavor",
                    "severity": "low",
                    "quote": "露珠沿着叶柄滑落",
                    "suggestion": _SUGGESTION_NEW,
                },
            ],
        }, ensure_ascii=False)
    ]


def test_critic_report_filters_already_suggested_issue(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            r = await _request(app, "POST", "/api/projects", json={"name": "dedup 项目"})
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "第一章"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["chapter_id"]

            mock = {"director": _director_script(cid), "writer": _writer_script(cid)}
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "dedup", "mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))

            # 第 1 轮 review：critic 报 2 条建议
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {**mock, "critic": _critic_script(cid)}},
            )
            assert r.status_code == 201, r.text
            first = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            r = await _request(app, "GET", f"/api/runs/{first['run_id']}")
            pause = r.json()["pause_payload"]
            assert pause["critic_status"] == "ok", pause
            assert len(pause["critic_report"]["issues"]) == 2, pause["critic_report"]

            # 作者「按建议修改」：把第 1 条建议原文写进 revision_note（驳回并改稿）
            r = await _request(
                app, "POST", f"/api/runs/{first['run_id']}/resume",
                json={
                    "human_input": {
                        "approved": False,
                        "revise": True,
                        "note": _SUGGESTION_REPEAT,
                    },
                    "auto_revise_max": 0,
                },
            )
            assert r.status_code == 200, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("FAILED",))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            plan = r.json()["plan_json"]
            assert plan["revision_note"] == _SUGGESTION_REPEAT, plan
            assert plan["suggested_hashes"] == [_suggestion_hash(_SUGGESTION_REPEAT)], plan

            # 第 2 轮 review：同一条建议被过滤，新建议保留
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {**mock, "critic": _critic_script(cid)}},
            )
            assert r.status_code == 201, r.text
            second = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            r = await _request(app, "GET", f"/api/runs/{second['run_id']}")
            report = r.json()["pause_payload"]["critic_report"]
            assert report["deduped_suggestion_count"] == 1, report
            suggestions = [i["suggestion"] for i in report["issues"]]
            assert suggestions == [_SUGGESTION_NEW], suggestions

    asyncio.run(run())
