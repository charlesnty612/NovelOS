"""Human Node 挂起 / 恢复 + HIGH risk 路径集成测试（Sprint 4-A）。

- observer 输出含 HIGH change → commit 工作流 PAUSED
- resume approved → COMMITTED
- resume rejected → FAILED
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations


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


async def _make_project(app) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": "HIGH 测试"})
    assert r.status_code == 201
    return r.json()["project_id"]


async def _make_character(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "林夕", "role": "protagonist"},
    )
    assert r.status_code == 201
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, n: int = 1) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters", json={"number": n, "title": "C1"}
    )
    assert r.status_code == 201
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200


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


def _observer_high_script(char_id: str, chapter_id: str):
    """Observer 输出含 1 条 HIGH risk character change + 1 条 LOW change + 1 个 new_hook。"""
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
                    {
                        "change_id": "cc_low_001",
                        "op": "update",
                        "target_id": char_id,
                        "character_id": char_id,
                        "facet": "state",
                        "field": "emotion",
                        "before": "sad",
                        "after": "happy",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "scene_id": None,
                            "excerpt": "测试",
                            "span": None,
                        },
                        "risk_level": "LOW",
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


async def _setup_drafted_chapter(app, pid: str, cid: str) -> None:
    """跑 plan + write + review(approve) 把 chapter 推到 REVIEWED。"""
    mock_providers = {
        "director": _director_script(),
        "writer": _writer_script(),
    }
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "intent", "mock_providers": mock_providers},
    )
    assert r.status_code == 201
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201
    paused = r.json()
    assert paused["status"] == "PAUSED"
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"


def test_commit_high_risk_pause_then_approve(tmp_path: Path):
    """Observer 输出含 HIGH change → commit PAUSED；resume approved → COMMITTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1)
            await _setup_drafted_chapter(app, pid, cid)

            mock_providers = {"observer": _observer_high_script(char_id, cid)}

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            paused = r.json()
            assert paused["status"] == "PAUSED", paused
            assert "pause_payload" in paused

            # 校验 pause_payload 含 delta_id
            assert paused["pause_payload"]["stage"] == "chapter-commit.high_risk_approval"
            assert "delta_id" in paused["pause_payload"]

            # resume approved=true
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "COMPLETED"

            # chapter 应为 COMMITTED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED"

    asyncio.run(run())


def test_commit_high_risk_pause_then_reject(tmp_path: Path):
    """Observer 输出含 HIGH change → commit PAUSED；resume approved=false → FAILED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1)
            await _setup_drafted_chapter(app, pid, cid)

            mock_providers = {"observer": _observer_high_script(char_id, cid)}

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201
            paused = r.json()
            assert paused["status"] == "PAUSED"

            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": False}},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "FAILED"

            # chapter 应保持 REVIEWED
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())
