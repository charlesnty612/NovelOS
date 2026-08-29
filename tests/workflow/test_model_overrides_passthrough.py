"""单次 run 级 model_overrides 透传测试。

锁定契约：
- ``POST /api/projects/{pid}/chapters/{cid}/write`` 带 ``model_overrides`` 时，
  ctx 中原样存在该字段（mock 路径不消费 profile_id）。
- 不带 ``model_overrides`` 时，ctx 中无该键（保证缺省零行为变更）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations

# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload
async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r = await client.get(f"/api/runs/{run_id}")
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 30.0) -> dict:
    import asyncio, time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s")



def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": "覆盖测试项目"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "测试角色", "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": "测试章"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _director_script() -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": "ch_xxx",
            "chapter_goal": "测试目标",
            "core_conflict": "测试冲突",
            "turning_point": "测试转折",
            "expected_role": "setup",
            "key_beats": [{
                "beat_id": "beat_001",
                "purpose": "测试",
                "involved_characters": [],
                "involved_locations": [],
                "involved_hooks": [],
                "involved_debts": [],
                "risk_level": "LOW",
                "narrative_question_served": "测试",
            }],
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "",
        }, ensure_ascii=False),
    ]


def _scene_planner_script() -> list[str]:
    return [
        json.dumps({
            "schema_version": "scene-planner.v1",
            "prompt_version": "scene_planner:v1",
            "chapter_id": "ch_xxx",
            "scenes": [{
                "scene_id": "scene_001",
                "purpose": "测试场景",
                "slots": [{
                    "slot_id": "slot_001",
                    "kind": "narration",
                    "text_hint": "提示",
                }],
                "characters": [],
                "locations": [],
                "conflicts": [],
                "information_boundary": {},
                "ending_hook": None,
            }],
        }, ensure_ascii=False),
    ]


def _writer_script() -> list[str]:
    prose = "测试正文段落。" * 200  # 约 1200 字，足够触发字数校验
    return [
        json.dumps({
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": "ch_xxx",
            "prose": prose,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(prose),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }, ensure_ascii=False),
    ]


async def _plan_chapter(app, pid: str, cid: str) -> str:
    """先跑一次 chapter-plan，让 chapters.plan_json 落库。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={
            "author_intent": "测试",
            "mock_providers": {"director": _director_script()},
        },
    )
    assert r.status_code == 201, r.text
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    r2 = await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))
    return r2["run_id"]


def test_write_model_overrides_passthrough_through_ctx(tmp_path: Path):
    """chapter-write 带 ``model_overrides`` 时，mock 路径不消费 profile_id，但 ctx 必须原样存在
    ``model_overrides`` 字段（pipeline 从 ctx 读取 → 转 profile_id 透传给 call_with_fallback）。
    """
    import asyncio

    async def run():
        app = _create_app(tmp_path)
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid)

            await _plan_chapter(app, pid, cid)

            overrides = {"creative_writing": "mprof_test_override"}
            mock_providers = {
                "scene_planner": _scene_planner_script(),
                "writer": _writer_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers, "model_overrides": overrides},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            body = r.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("COMPLETED",))

            run_id = body["run_id"]
            # 通过 GET /runs/{run_id} 取 checkpoint_json，断言 ctx.model_overrides 原样存在
            r2 = await _request(app, "GET", f"/api/runs/{run_id}")
            assert r2.status_code == 200, r2.text
            run = r2.json()
            ckpt = run.get("checkpoint_json") or {}
            found = False
            for node_id, node_ckpt in ckpt.items():
                if not isinstance(node_ckpt, dict):
                    continue
                ctx_blob = node_ckpt.get("ctx") if isinstance(node_ckpt.get("ctx"), dict) else None
                merged = {**{k: v for k, v in ckpt.items() if k != "ctx"}, **(ctx_blob or {})}
                if "model_overrides" in merged and merged["model_overrides"] == overrides:
                    found = True
                    break
                if node_ckpt.get("model_overrides") == overrides:
                    found = True
                    break
            assert found, (
                f"checkpoint_json 中未找到 model_overrides={overrides}；实际 keys={list(ckpt.keys())}"
            )

    asyncio.run(run())


def test_write_without_model_overrides_omits_key(tmp_path: Path):
    """不带 ``model_overrides`` 时，ctx 中不应出现 ``model_overrides`` 键（保证缺省零行为变更）。"""
    import asyncio

    async def run():
        app = _create_app(tmp_path)
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid)

            await _plan_chapter(app, pid, cid)

            mock_providers = {
                "scene_planner": _scene_planner_script(),
                "writer": _writer_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            body = r.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("COMPLETED",))

            run_id = body["run_id"]
            r2 = await _request(app, "GET", f"/api/runs/{run_id}")
            assert r2.status_code == 200, r2.text
            run = r2.json()
            ckpt = run.get("checkpoint_json") or {}

            for node_id, node_ckpt in ckpt.items():
                if isinstance(node_ckpt, dict) and "model_overrides" in node_ckpt:
                    pytest.fail(
                        f"未传 model_overrides 时 ctx 不应出现该键，但 {node_id}={node_ckpt!r}"
                    )

    asyncio.run(run())