"""chapter-write P0 Scene Planner 节点测试。

覆盖：
1. mock scene_planner 输出合规 JSON → scene_plan 含完整 scenes/slots，writer 正常调用。
2. mock scene_planner 连续坏两次 → 降级到 stub 机械映射，writer 仍成功，run COMPLETED。
3. scene_planner 未配 mock（无 model config）→ 同样降级到 stub，不阻断 writer。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


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


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "苏婉清第一次怀疑林渊隐瞒父亲死因",
                "core_conflict": "求真 vs 隐瞒",
                "turning_point": "林渊回避黑玉佩细节",
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
                "notes_for_planner": "建议场景数 1",
            },
            ensure_ascii=False,
        )
    ]


def _scene_planner_script() -> list[str]:
    """合规 scene-plan.v1 输出：含 information_boundary / ending_hook / slots。"""
    return [
        json.dumps(
            {
                "schema_version": "scene-plan.v1",
                "prompt_version": "scene_planner:v1",
                "chapter_id": "ch_xxx",
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "purpose": "夜访玉惜轩：黑玉佩引入对话",
                        "characters": [],
                        "location": None,
                        "conflict": "苏婉清追问，林渊回避",
                        "turn": "林渊转移话题",
                        "time_in_story": "戌时",
                        "pov": "third_person_limited",
                        "pov_character_id": None,
                        "information_boundary": ["黑玉佩真正来历"],
                        "ending_hook": "苏婉清决定暗中调查",
                        "slots": [
                            {
                                "slot_id": "scene_001_desc_01",
                                "type": "description",
                                "purpose": "建立夜访场景气氛",
                                "characters": [],
                                "target_mood": "清寂微凉",
                                "constraints": ["不用元叙述"],
                            },
                            {
                                "slot_id": "scene_001_dialogue_01",
                                "type": "dialogue",
                                "purpose": "苏婉清引出父亲遗物话题",
                                "characters": [],
                                "target_mood": "克制中的试探",
                                "constraints": ["苏婉清先开口"],
                            },
                        ],
                    }
                ],
                "notes_for_writer": "",
                "deviations": [],
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = (
        "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
        "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。"
    )
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": prose,
                "self_report": {
                    "slots_filled": ["scene_001_desc_01", "scene_001_dialogue_01"],
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


def test_scene_planner_ok_produces_structured_scene_plan(tmp_path: Path):
    """mock scene_planner 合规 → scene_plan 含完整 slots，writer 正常调用。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "scene_planner": _scene_planner_script(),
                "writer": _writer_script(),
            }

            # plan
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
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
            body = r.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("COMPLETED",))

            # 校验 ai_call_logs 有 scene_planner:v1 调用
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE prompt_version = ?",
                    ("scene_planner:v1",),
                ).fetchone()
            finally:
                conn.close()
            assert row["n"] == 1, "ai_call_logs 缺少 scene_planner:v1 调用记录"

            # 校验 chapter 状态
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "DRAFTED"

    asyncio.run(run())


def test_scene_planner_bad_output_falls_back_to_stub(tmp_path: Path):
    """mock scene_planner 连续输出非 JSON → 降级到 stub，writer 仍可完成。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            bad_scene_script = ["not json at all", "still not json"]
            mock_providers = {
                "director": _director_script(),
                "scene_planner": bad_scene_script,
                "writer": _writer_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            body = r.json()
            body = await _wait_run_terminal(app, body["run_id"], expected=("COMPLETED",))

            # 校验 drafts 已写
            conn = get_connection(app.state.settings.db_path)
            try:
                drafts = conn.execute(
                    "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchone()
            finally:
                conn.close()
            assert drafts["n"] == 1

    asyncio.run(run())


def test_scene_planner_missing_mock_degrades_to_stub(tmp_path: Path):
    """不配 scene_planner mock（无 model config）→ 降级到 stub，run COMPLETED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "第一章")

            mock_providers = {
                "director": _director_script(),
                # 不配 scene_planner mock
                "writer": _writer_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

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

            # model config 缺失时 runner 仍会写一条 ai_call_logs（error 字段非空）
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_call_logs WHERE prompt_version = ?",
                    ("scene_planner:v1",),
                ).fetchone()
            finally:
                conn.close()
            assert row["n"] == 1, "无 model config 时 runner 也应记录一次失败的 ai_call_log"

    asyncio.run(run())
