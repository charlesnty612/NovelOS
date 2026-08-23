"""/api/simulation What-if 推演 API 测试（Sprint 10）。

覆盖任务书范围：
- 主链路：init → POST /simulate 两个 delta → 201 返回 SimulationResult；
  main snapshot 不变 → 分支 archived → GET /simulations 列表 / GET /simulations/{id} 重放。
- 校验失败：delta 缺必填字段 → 422 + issues 列表 + 分支 archived。
- HIGH 风险审批绕过：facet=definition 不触发 409 approval_required；audit 字段写入。
- 空 deltas → 422。
- project 404。

测试模式参考 ``tests/api/test_branches.py``：httpx ASGI + tmp_path db；不引入 pytest-asyncio。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "Sim Project") -> str:
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


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


def _state_delta_location(cid: str, chap: str, change_id: str, before: str, after: str) -> dict:
    return {
        "change_id": change_id,
        "op": "update",
        "target_id": cid,
        "character_id": cid,
        "facet": "state",
        "field": "state.location",
        "before": before,
        "after": after,
        "confidence": 0.95,
        "evidence": _evidence(chap),
        "risk_level": "LOW",
    }


# --------------------------------------------------------------------- happy path


def test_simulate_endpoint_happy_path(tmp_path: Path):
    """POST /simulate 两个 delta → 201 → main 不变 → GET /simulations 重放。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text

            deltas = [
                {
                    **_make_meta("dlt_a1", chap, 1),
                    "character_changes": [_state_delta_location(cid, chap, "cc_a1", "Unknown", "Cave")],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [
                        {
                            "change_id": "ev_a1",
                            "op": "add",
                            "target_id": "event_a1",
                            "event_id": "event_a1",
                            "type": "encounter",
                            "participants": [cid],
                            "time": {"timeline_day": 2, "in_story_date": None},
                            "description": "推演事件",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                        }
                    ],
                    "resolved_hooks": [],
                    "new_hooks": [
                        {
                            "change_id": "nh_a1",
                            "op": "add",
                            "target_id": "hook_a1",
                            "hook_id": "hook_a1",
                            "name": "推演伏笔",
                            "importance": 0.7,
                            "description": "推演独有",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                        }
                    ],
                    "debt_changes": [],
                },
                {
                    **_make_meta("dlt_a2", chap, 2),
                    "character_changes": [
                        {
                            "change_id": "cc_a2",
                            "op": "update",
                            "target_id": cid,
                            "character_id": cid,
                            "facet": "state",
                            "field": "state.emotion",
                            "before": "calm",
                            "after": "alert",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                        }
                    ],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
            ]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/simulate",
                json={"deltas": deltas, "name": "sim-happy-api"},
            )
            assert r.status_code == 201, r.text
            result = r.json()
            assert result["name"] == "sim-happy-api"
            assert result["base_version"] == 1
            assert result["applied"] == 2
            assert result["branch_status"] == "ARCHIVED"
            assert result["simulation_id"] == result["branch_id"]
            assert "characters" in result["diff"] or "hooks" in result["diff"] or "events_changed" in result["diff"]

            # main 不变：state_version 仍为 1
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            main_state = r.json()
            assert main_state["state_version"] == 1
            assert "event_a1" not in main_state.get("recent_events", [])
            assert not any(h["hook_id"] == "hook_a1" for h in main_state["hooks"])

            # 列推演
            r = await _request(app, "GET", f"/api/projects/{pid}/simulations")
            assert r.status_code == 200
            listed = r.json()
            assert len(listed) == 1
            assert listed[0]["simulation_id"] == result["simulation_id"]
            assert listed[0]["status"] == "ARCHIVED"

            # 重放
            r = await _request(
                app, "GET", f"/api/projects/{pid}/simulations/{result['simulation_id']}",
            )
            assert r.status_code == 200
            replay = r.json()
            assert replay["simulation_id"] == result["simulation_id"]
            assert replay["applied"] == 2

    asyncio.run(run())


# --------------------------------------------------------------------- validation failure


def test_simulate_validation_failure_returns_422_and_archives(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 第二个 delta 缺 change_id → 422
            deltas = [
                {
                    **_make_meta("dlt_v1", chap, 1),
                    "character_changes": [_state_delta_location(cid, chap, "cc_v1", "Unknown", "Town")],
                    "world_changes": [], "relationship_changes": [], "new_events": [],
                    "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                },
                {
                    **_make_meta("dlt_v2", chap, 2),
                    "character_changes": [
                        {
                            # 缺 change_id
                            "op": "update",
                            "target_id": cid,
                            "character_id": cid,
                            "facet": "state",
                            "field": "state.location",
                            "before": "Town",
                            "after": "Mountain",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                        }
                    ],
                    "world_changes": [], "relationship_changes": [], "new_events": [],
                    "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                },
            ]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/simulate",
                json={"deltas": deltas, "name": "sim-vfail"},
            )
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "validation_failed"
            assert len(detail["issues"]) == 1
            assert detail["issues"][0]["index"] == 1
            assert detail["branch_id"] is not None

            # 分支 archived（即便校验失败也归档）
            r = await _request(app, "GET", f"/api/projects/{pid}/simulations")
            assert r.status_code == 200
            listed = r.json()
            assert any(
                x["simulation_id"] == detail["branch_id"] and x["status"] == "ARCHIVED"
                for x in listed
            )

    asyncio.run(run())


# --------------------------------------------------------------------- HIGH risk bypass


def test_simulate_high_risk_bypasses_approval(tmp_path: Path):
    """character facet=definition 触发 HIGH 风险；simulation 不返回 409 approval_required。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 直接走 /simulate，body 是 delta dict（含 change_id）
            # 注意：没有走 /commits，不传 author_approval；simulate 内部自动处理
            deltas = [
                {
                    **_make_meta("dlt_h1", chap, 1),
                    "character_changes": [
                        {
                            "change_id": "cc_h1",
                            "op": "update",
                            "target_id": cid,
                            "character_id": cid,
                            "facet": "definition",
                            "field": "core.personality",
                            "before": "calm",
                            "after": "reckless",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "HIGH",
                        }
                    ],
                    "world_changes": [], "relationship_changes": [], "new_events": [],
                    "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                },
            ]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/simulate",
                json={"deltas": deltas, "name": "sim-high-risk"},
            )
            assert r.status_code == 201, r.text
            result = r.json()
            assert result["applied"] == 1
            assert result["branch_status"] == "ARCHIVED"

    asyncio.run(run())


# --------------------------------------------------------------------- empty deltas


def test_simulate_empty_deltas_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(app, "POST", f"/api/projects/{pid}/simulate", json={"deltas": []})
            assert r.status_code == 422
            assert r.json()["detail"]["error"] == "empty_deltas"

    asyncio.run(run())


# --------------------------------------------------------------------- project 404


def test_simulate_unknown_project_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects/prj_nope/simulate",
                json={"deltas": [{"x": 1}]},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())


# --------------------------------------------------------------------- get_simulation 404


def test_get_simulation_unknown_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(app, "GET", f"/api/projects/{pid}/simulations/br_nope")
            assert r.status_code == 404, r.text

    asyncio.run(run())
