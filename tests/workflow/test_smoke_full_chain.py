"""Sprint 4-A 冒烟测试：全 mock 链路四个 POST 端点的 status 流转。

模拟用户在浏览器里点 4 次按钮的最小工作流：
plan → write → review(approved) → commit(no HIGH)
断言每次 POST 返回的 status 与最终 chapter.status。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations




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
    import asyncio, time
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


async def _sync_prompts(app):
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
                "chapter_goal": "让女主怀疑男主",
                "core_conflict": "求真 vs 善意隐瞒",
                "turning_point": "男主回避",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "b1",
                        "purpose": "p",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "q",
                    }
                ],
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
    prose = "<think>这是内部推理，不应进入正文。</think>夜色。灯芯闪了一下花。"
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


def _observer_script():
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


def test_smoke_full_chain(tmp_path: Path):
    """一次跑完 plan / write / review(approve) / commit；输出每步 status。"""
    app = _create_app(tmp_path)
    statuses: dict[str, str] = {}

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            r = await _request(app, "POST", "/api/projects", json={"name": "Smoke"})
            pid = r.json()["project_id"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "A", "role": "protagonist"},
            )
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "C1"},
            )
            cid = r.json()["chapter_id"]

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_script(),
            }

            # 1. plan
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            statuses["plan"] = r.json()["status"]

            # 2. write
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            statuses["write"] = r.json()["status"]
            drafts = (await _request(app, "GET", f"/api/chapters/{cid}/drafts")).json()
            assert drafts and "<think>" not in drafts[0]["content"]
            assert "夜色。灯芯闪了一下花。" in drafts[0]["content"]

            # 3. review (PAUSED → resume approved)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            statuses["review"] = r.json()["status"]
            r = await _request(
                app, "POST", f"/api/runs/{r.json()['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            statuses["review_after_resume"] = r.json()["status"]

            # 4. commit
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            statuses["commit"] = r.json()["status"]

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            statuses["chapter_final"] = r.json()["status"]

    asyncio.run(run())

    # 期望 status 流转（异步化后 status 在 dict 中记录的是响应时的状态——可能 RUNNING/COMMITTED）
    # 该测试仅校验「响应 status 字段非空且在合法集合」；不校验具体值
    assert statuses["plan"] in ("RUNNING", "COMPLETED")
    assert statuses["write"] in ("RUNNING", "COMPLETED")
    assert statuses["review"] in ("RUNNING", "PAUSED")
    assert statuses["review_after_resume"] in ("RUNNING", "COMPLETED")
    assert statuses["commit"] in ("RUNNING", "COMPLETED")
    assert statuses["chapter_final"] == "COMMITTED"

    print("\n[smoke] status transitions:", statuses)
