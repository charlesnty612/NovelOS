"""chapter-plan 工作流：重生成时保留 expected_word_count（防呆 B）。

P1 缺陷：用户误点「生成计划」覆盖了已有的 plan_json，导致原
``expected_word_count``（如 3000）丢失，writer 拿不到字数规划。

契约：
- 若原 plan_json.expected_word_count > 0，重生成时新 plan 继承该值；
- 若 director 自己产出 expected_word_count 且 >0，优先用 director 的；
- 否则用默认 2200（对齐 build_director_input 默认 target_word_count）。
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


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str = "测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _director_script_with_no_word_count() -> list[str]:
    """合规 director-plan.v1 输出（不含 expected_word_count 字段）。"""
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "苏婉清在夜谈中第一次怀疑林渊隐瞒父亲死因",
                "core_conflict": "苏婉清的求真意志 vs 林渊的善意隐瞒",
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
                ],
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


def test_chapter_plan_preserves_existing_expected_word_count(tmp_path: Path):
    """原 plan.expected_word_count=3000 → 重跑 chapter-plan → 新 plan 仍为 3000。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {"director": _director_script_with_no_word_count()}

            # 1) 第一次 chapter-plan（director 不产出 expected_word_count → 用默认 2200）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            ch = r.json()
            assert ch["plan_json"]["expected_word_count"] == 2200

            # 2) 人工把 expected_word_count 改成 3000（模拟用户/编辑手动调过字数）
            plan = dict(ch["plan_json"])
            plan["expected_word_count"] = 3000
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}",
                json={"plan_json": plan},
            )
            assert r.status_code == 200, r.text

            # 3) 重跑 chapter-plan（误点场景）→ 新 plan 必须继承 3000
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            ch_after = r.json()
            assert (
                ch_after["plan_json"]["expected_word_count"] == 3000
            ), f"expected 3000, got {ch_after['plan_json']['expected_word_count']}"
            # 其他字段被 director 正常刷新
            assert ch_after["plan_json"]["chapter_goal"]
            # status 仍是 PLANNED（plan 不改 status）
            assert ch_after["status"] == "PLANNED"

    asyncio.run(run())


def test_chapter_plan_uses_default_when_no_inherit_and_no_director(tmp_path: Path):
    """原 plan 为空 + director 不带 expected_word_count → 用默认 2200。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {"director": _director_script_with_no_word_count()}

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            ch = r.json()
            assert ch["plan_json"]["expected_word_count"] == 2200

    asyncio.run(run())


def test_chapter_plan_director_word_count_takes_priority(tmp_path: Path):
    """director 自己产出 expected_word_count > 0 时，优先用 director 的。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            # director 显式带 expected_word_count=2500
            director_with_word = [
                json.dumps(
                    {
                        "schema_version": "director-plan.v1",
                        "prompt_version": "director:v1",
                        "chapter_id": "ch_xxx",
                        "chapter_goal": "目标",
                        "core_conflict": "冲突",
                        "turning_point": "转折",
                        "expected_role": "setup",
                        "expected_word_count": 2500,
                        "key_beats": [],
                        "character_changes_planned": [],
                        "information_releases": [],
                        "hook_handling": [],
                        "debt_handling": [],
                        "proposed_new_entities": [],
                        "deviations": [],
                        "knowledge_leakage_check": {
                            "uses_hidden_knowledge": False,
                            "leakage_details": None,
                        },
                        "open_questions": [],
                        "notes_for_planner": "",
                    },
                    ensure_ascii=False,
                )
            ]

            # 先把原 plan 设成 4000 → 验证 director 的 2500 仍胜出（director 优先）
            mock_providers_initial = {
                "director": _director_script_with_no_word_count()
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": mock_providers_initial},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (
                        json.dumps(
                            {
                                "chapter_goal": "旧",
                                "expected_word_count": 4000,
                            },
                            ensure_ascii=False,
                        ),
                        cid,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            # 重跑，director 产出 2500 → 优先采用 2500
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"mock_providers": {"director": director_with_word}},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "FAILED"))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            ch = r.json()
            assert ch["plan_json"]["expected_word_count"] == 2500

    asyncio.run(run())