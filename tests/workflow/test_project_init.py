"""project-init workflow 端到端测试（P1）。

- mock_providers 全 mock 跑通 project-init。
- 断言节点顺序、落库结果、章节种子数量。
- 覆盖「新建项目」与「挂载已有项目」两种模式。
- 覆盖 AI 节点降级模式与 persist 失败抛错。
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


def _premise_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "premise-design.v1",
                "prompt_version": "premise_designer:v1",
                "title": "九天星辰诀",
                "genre": "玄幻",
                "logline": "少年叶尘偶得星辰古卷，逆天改命，踏上九重天阙",
                "positioning": "传统玄幻升级流，快节奏爽文",
                "selling_points": ["金手指独特", "升级节奏快", "世界观宏大"],
                "protagonist": {
                    "name": "叶尘",
                    "role": "protagonist",
                    "core_desire": "查明父亲死因并登临九天",
                    "core_conflict": "身负废脉之体与星辰古卷的冲突",
                    "distinctive_trait": "冷静坚韧，善于借势",
                },
            },
            ensure_ascii=False,
        )
    ]


def _world_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "world-build.v1",
                "prompt_version": "world_builder:v1",
                "core_premise": "九天之上存在星辰古神留下的力量体系，凡人可借星辰之力修炼",
                "rules": [
                    {
                        "name": "星辰共鸣",
                        "statement": "修炼者需在星夜下与特定星辰建立共鸣，方可吸收星力",
                        "data": {"severity": "hard"},
                    }
                ],
                "locations": [
                    {
                        "name": "青石城",
                        "statement": "叶尘出身的小城，靠近陨星山脉",
                        "data": {"layer": "地表", "importance": "起点"},
                    }
                ],
                "factions": [
                    {
                        "name": "天星宗",
                        "statement": "执掌星辰传承的顶尖宗门",
                        "data": {"alignment": "守序", "relation_to_protagonist": "观望"},
                    }
                ],
            },
            ensure_ascii=False,
        )
    ]


def _character_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "character-design.v1",
                "prompt_version": "character_designer:v1",
                "characters": [
                    {
                        "name": "叶尘",
                        "role": "protagonist",
                        "core_json": {
                            "motivation": "登临九天查明真相",
                            "goal": "一年内踏入星辰境",
                            "conflict": "废脉之体无法共鸣星力",
                            "distinctive_trait": "冷静坚韧",
                            "relationships": [
                                {"to_name": "苏婉清", "relation_type": "ally", "one_line": "青梅竹马，暗中相助"}
                            ],
                        },
                    },
                    {
                        "name": "苏婉清",
                        "role": "love_interest",
                        "core_json": {
                            "motivation": "守护叶尘",
                            "goal": "帮叶尘恢复修炼资质",
                            "conflict": "家族反对她与废脉少年往来",
                            "distinctive_trait": "外柔内刚",
                            "relationships": [
                                {"to_name": "叶尘", "relation_type": "lover", "one_line": "青梅竹马"}
                            ],
                        },
                    },
                    {
                        "name": "林渊",
                        "role": "antagonist",
                        "core_json": {
                            "motivation": "夺取星辰古卷",
                            "goal": "在宗门大比中击败叶尘",
                            "conflict": "自负天才却被废脉少年超越",
                            "distinctive_trait": "阴狠骄傲",
                            "relationships": [
                                {"to_name": "叶尘", "relation_type": "enemy", "one_line": "宿敌"}
                            ],
                        },
                    },
                ],
            },
            ensure_ascii=False,
        )
    ]


def _outline_script(seed_count: int = 10) -> list[str]:
    seeds = [
        {
            "number": i + 1,
            "title": f"第{i + 1}章",
            "role": "setup" if i < 3 else ("escalation" if i < 7 else "climax"),
            "one_sentence": f"本章事件{i + 1}",
            "expected_word_count": 2200,
            "key_beats": [f"beat_{i + 1}_a", f"beat_{i + 1}_b"],
        }
        for i in range(seed_count)
    ]
    return [
        json.dumps(
            {
                "schema_version": "volume-outline.v1",
                "prompt_version": "volume_outliner:v1",
                "volume": {"number": 1, "title": "星落青石", "arc_summary": "叶尘从废脉少年踏上星辰之路"},
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _mock_providers(seed_count: int = 10) -> dict[str, list[str]]:
    return {
        "premise_designer": _premise_script(),
        "world_builder": _world_script(),
        "character_designer": _character_script(),
        "volume_outliner": _outline_script(seed_count),
    }


def test_project_init_creates_new_project(tmp_path: Path):
    """project-init 在 brief 不带 project_id 时应创建新项目并落库所有实体。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {
                        "genre": "玄幻",
                        "logline": "少年叶尘偶得星辰古卷",
                        "platform": "起点",
                        "target_words": 300000,
                        "title": "",
                        "author_notes": "快节奏爽文",
                    },
                    "chapter_seed_count": 8,
                    "mock_providers": _mock_providers(8),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            run_data = await _wait_run_terminal(app, payload["run_id"], expected=("COMPLETED",))
            assert run_data["current_node"] is None
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id and project_id.startswith("prj_")

            # 校验项目字段
            r = await _request(app, "GET", f"/api/projects/{project_id}")
            assert r.status_code == 200
            proj = r.json()
            assert proj["name"] == "九天星辰诀"
            assert proj["genre"] == "玄幻"
            assert "星辰古卷" in (proj["premise"] or "")

            # 校验角色
            r = await _request(app, "GET", f"/api/projects/{project_id}/characters")
            assert r.status_code == 200
            chars = r.json()
            assert len(chars) == 3
            names = {c["name"] for c in chars}
            assert names == {"叶尘", "苏婉清", "林渊"}

            # 校验世界实体
            r = await _request(app, "GET", f"/api/projects/{project_id}/locations")
            assert r.status_code == 200
            assert len(r.json()) == 1
            r = await _request(app, "GET", f"/api/projects/{project_id}/factions")
            assert r.status_code == 200
            assert len(r.json()) == 1
            r = await _request(app, "GET", f"/api/projects/{project_id}/world-rules")
            assert r.status_code == 200
            assert len(r.json()) == 1

            # 校验卷
            r = await _request(app, "GET", f"/api/projects/{project_id}/volumes")
            assert r.status_code == 200
            volumes = r.json()
            assert len(volumes) == 1
            assert volumes[0]["number"] == 1
            assert volumes[0]["title"] == "星落青石"

            # 校验章节种子数量与挂载
            conn = get_connection(app.state.settings.db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM chapters WHERE project_id = ? ORDER BY number",
                    (project_id,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 8, [r["number"] for r in rows]
            for idx, row in enumerate(rows):
                assert row["number"] == idx + 1
                assert row["volume_id"] == volumes[0]["volume_id"]
                plan = json.loads(row["plan_json"])
                assert plan.get("schema_version") == "director-plan.v1"

            # 校验 workflow_run_nodes 顺序
            run_id = payload["run_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                node_rows = conn.execute(
                    """
                    SELECT node_id, status FROM workflow_run_nodes
                    WHERE run_id = ? ORDER BY started_at
                    """,
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
            node_ids = [n["node_id"] for n in node_rows]
            assert node_ids == [
                "load_brief",
                "premise_designer",
                "world_builder",
                "character_designer",
                "volume_outliner",
                "persist_all",
            ]
            assert all(n["status"] == "COMPLETED" for n in node_rows)

    asyncio.run(run())


def test_project_init_attaches_to_existing_project(tmp_path: Path):
    """project-init 传入已有 project_id 时应更新项目并挂载生成内容。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "旧项目")

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": pid,
                    "brief": {
                        "genre": "玄幻",
                        "logline": "少年叶尘偶得星辰古卷",
                    },
                    "mock_providers": _mock_providers(5),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            run_data = await _wait_run_terminal(app, payload["run_id"], expected=("COMPLETED",))
            assert (run_data.get("checkpoint_json") or {}).get("project_id") == pid

            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 200
            proj = r.json()
            assert proj["name"] == "九天星辰诀"
            assert proj["genre"] == "玄幻"

            r = await _request(app, "GET", f"/api/projects/{pid}/characters")
            assert r.status_code == 200
            assert len(r.json()) == 3

            conn = get_connection(app.state.settings.db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
            finally:
                conn.close()
            assert count == 5

    asyncio.run(run())


def test_project_init_degrades_on_agent_failure(tmp_path: Path):
    """AI 节点失败时应降级，工作流仍可完成。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            mocks = _mock_providers(6)
            # 让 world_builder 两次都返回非法 JSON，触发降级
            mocks["world_builder"] = ["not valid json {"]

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "测试"},
                    "chapter_seed_count": 6,
                    "mock_providers": mocks,
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            run_id = payload["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))

            conn = get_connection(app.state.settings.db_path)
            try:
                # 校验 world_builder 节点是 COMPLETED（节点级降级不抛错）
                row = conn.execute(
                    "SELECT status, output_json FROM workflow_run_nodes WHERE run_id = ? AND node_id = ?",
                    (run_id, "world_builder"),
                ).fetchone()
            finally:
                conn.close()
            # 之前 await _wait_run_terminal(app, row["run_id"]...) 是 patcher 错误插的——row 是 SQL 结果不含 run_id
            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            output = json.loads(row["output_json"])
            assert output["world_output"]["_degraded"] is True

            # 项目仍应落库成功
            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            pid = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert pid
            r = await _request(app, "GET", f"/api/projects/{pid}/characters")
            assert r.status_code == 200
            assert len(r.json()) == 3

    asyncio.run(run())


def test_project_init_persist_failure_raises(tmp_path: Path):
    """persist 失败（如 project_id 不存在）应直接抛 404/400，不降级。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": "prj_nonexistent",
                    "brief": {"genre": "玄幻", "logline": "测试"},
                    "mock_providers": _mock_providers(),
                },
            )
            assert r.status_code == 404, r.text
            assert "prj_nonexistent" in r.json()["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# step_mode 分段审阅暂停（4 关卡）
# ---------------------------------------------------------------------------


def _step_mode_full_scripts(seed_count: int = 10) -> dict[str, list[str]]:
    """每个 agent 给足 1 条 mock：mock_script 列表在 checkpoint 中跨 resume
    持久化但调用计数不重置，列表模式耗尽后重复末条，足够支撑各关卡取一次。
    """
    return {
        "premise_designer": _premise_script(),
        "world_builder": _world_script(),
        "character_designer": _character_script(),
        "volume_outliner": _outline_script(seed_count),
    }


def test_project_init_step_mode_pauses_at_first_stage(tmp_path: Path):
    """step_mode=True 时第一关 premise 即挂起，pause_payload 契约齐备。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {
                        "genre": "玄幻",
                        "logline": "少年叶尘偶得星辰古卷",
                        "platform": "起点",
                        "target_words": 300000,
                        "title": "",
                        "author_notes": "",
                    },
                    "chapter_seed_count": 5,
                    "step_mode": True,
                    "mock_providers": _step_mode_full_scripts(5),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()

            payload = await _wait_run_terminal(app, payload["run_id"], expected=("PAUSED",))
            assert payload["current_node"] == "premise_designer"
            assert "pause_payload" in payload
            pp = payload["pause_payload"]
            assert pp["stage"] == "premise"
            assert pp["stage_index"] == 0
            assert pp["stages_total"] == 4
            assert pp["degraded"] is False
            draft = pp["draft"]
            assert draft["title"] == "九天星辰诀"
            assert draft["genre"] == "玄幻"
            assert "selling_points" in draft and draft["selling_points"]

            # 尚未落库：run dict 无 project_id 字段
            assert "project_id" not in payload or payload["project_id"] is None

    asyncio.run(run())


def test_project_init_step_mode_revision_advances_to_next_stage(tmp_path: Path):
    """第一关修订回灌后应推进到第二关（world）继续挂起。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 5,
                    "step_mode": True,
                    "mock_providers": _step_mode_full_scripts(5),
                },
            )
            assert r.status_code == 201, r.text
            start = r.json()
            start = await _wait_run_terminal(app, start["run_id"], expected=("PAUSED",))
            run_id = start["run_id"]

            # 人工修订 premise_output，回灌推进
            revised_premise = {
                "title": "改后书名",
                "genre": "玄幻",
                "logline": "修订后的一句话",
                "positioning": "x",
                "selling_points": ["s1"],
                "protagonist": {"name": "叶尘"},
            }
            r = await _request(
                app,
                "POST",
                f"/api/runs/{run_id}/resume",
                json={"human_input": {"revisions": {"premise_output": revised_premise}}},
            )
            assert r.status_code == 200, r.text
            after = r.json()
            # 异步化后：resume_async 立刻返回 RUNNING，_run_nodes 在后台推进。
            # 等到 PAUSED 时 current_node 已经被 _finalize_run(PAUSED, current_node=node_id) 写入。
            # 但后台线程可能与 GET 之间有微小延迟，循环重试以稳定。
            import asyncio
            run_id = after["run_id"]
            for _ in range(50):
                after = await _wait_run_terminal(app, run_id, expected=("PAUSED",))
                if after["current_node"] == "world_builder":
                    break
                await asyncio.sleep(0.1)
            assert after["current_node"] == "world_builder"
            pp = after["pause_payload"]
            assert pp["stage"] == "world"
            assert pp["stage_index"] == 1
            draft = pp["draft"]
            assert "core_premise" in draft

    asyncio.run(run())


def test_project_init_step_mode_completes_with_persistence(tmp_path: Path):
    """四关走完后应 COMPLETED 且 premise 修订穿透 persist_all 落到 projects.name。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 4,
                    "step_mode": True,
                    "mock_providers": _step_mode_full_scripts(4),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            r2 = await _wait_run_terminal(app, r2["run_id"], expected=("PAUSED",))

            # 第 1 关：premise 修订（关键校验——回灌后落库的 name 应来自此处）
            r = await _request(
                app,
                "POST",
                f"/api/runs/{run_id}/resume",
                json={
                    "human_input": {
                        "revisions": {
                            "premise_output": {
                                "title": "改后书名",
                                "genre": "玄幻",
                                "logline": "修订后的一句话",
                                "positioning": "传统玄幻升级流",
                                "selling_points": ["s1", "s2"],
                                "protagonist": {"name": "叶尘"},
                            }
                        }
                    }
                },
            )
            assert r.status_code == 200, r.text
            import asyncio
            r2 = None
            for _ in range(50):
                r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
                if r2["current_node"] == "world_builder":
                    break
                await asyncio.sleep(0.1)
            assert r2["current_node"] == "world_builder"

            # 第 2~4 关：不带 revisions 直接推进（依赖 _resolve_stage_input
            # 第二层 ctx[output_key] 兜底）
            last_r_run = None
            import asyncio as _aio
            for _ in range(3):
                r = await _request(
                    app,
                    "POST",
                    f"/api/runs/{run_id}/resume",
                    json={"human_input": {}},
                )
                assert r.status_code == 200, r.text
                r_run = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED", "COMPLETED"))
                last_r_run = r_run
                if r_run["status"] == "COMPLETED":
                    break
                # 异步化后：等前一个后台线程 _finalize_run 事务提交（SQLite WAL 写入可见）
                await _aio.sleep(0.2)
            assert last_r_run is not None
            r2 = await _wait_run_terminal(app, last_r_run["run_id"], expected=("COMPLETED",))
            # 最后一关 resume 返回即应含 project_id（与落库项目一致）
            # 异步化后：resume_async 立即返回 RUNNING，project_id 需从 checkpoint_json 读
            run_data = await _wait_run_terminal(app, last_r_run["run_id"], expected=("COMPLETED",))
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id and project_id.startswith("prj_")

            # 校验修订后的 premise 穿透到 projects.name
            r = await _request(app, "GET", f"/api/projects/{project_id}")
            assert r.status_code == 200
            proj = r.json()
            assert proj["name"] == "改后书名", proj
            assert proj["genre"] == "玄幻"

            # 校验角色与章节数量
            r = await _request(app, "GET", f"/api/projects/{project_id}/characters")
            assert r.status_code == 200
            assert len(r.json()) == 3

            conn = get_connection(app.state.settings.db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?",
                    (project_id,),
                ).fetchone()["c"]
                # 校验挂起节点被标 SKIPPED，旧 PENDING 行被收尾
                node_rows = conn.execute(
                    """
                    SELECT node_id, status FROM workflow_run_nodes
                    WHERE run_id = ? ORDER BY started_at
                    """,
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
            assert count == 4
            # premise_designer 行应被 SKIPPED（resume 时旧的 PENDING 行被收尾）
            premise_row = next(n for n in node_rows if n["node_id"] == "premise_designer")
            assert premise_row["status"] == "SKIPPED", premise_row

    asyncio.run(run())


def test_project_init_default_no_step_mode_regression(tmp_path: Path):
    """回归：省略 step_mode 或显式 False 时，行为与现状一致——一次性 COMPLETED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            # 省略 step_mode
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 6,
                    "mock_providers": _mock_providers(6),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            payload = await _wait_run_terminal(app, payload["run_id"], expected=("COMPLETED",))
            assert payload["current_node"] is None
            assert "pause_payload" not in payload

            # 显式 step_mode=False
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 6,
                    "step_mode": False,
                    "mock_providers": _mock_providers(6),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            payload = await _wait_run_terminal(app, payload["run_id"], expected=("COMPLETED",))
            assert "pause_payload" not in payload

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 回归：_resolve_stage_input 第 1 层 revisions 空 dict 应视为"未提供"，
# 回退到 ctx[output_key] / checkpoint draft 兜底。
#
# 语义决策：project_init 工作流把"空修订 dict"解释为"调用方没真正提供修订"，
# 因为：
#   1. 前端审阅框清空提交是常见误操作，不应短路掉 checkpoint 中的合法 AI 草稿；
#   2. 真要清空某字段应通过 explicit reset 语义或删行级实体，不该靠"传空对象"。
# 因此第 1 层只接受"非空 dict"，空 dict 走 ctx/draft 兜底。
# ---------------------------------------------------------------------------


def test_resolve_stage_input_empty_revisions_falls_back_to_draft():
    """单测：_resolve_stage_input 第 1 层 revisions 空 dict 应回退到 draft。

    修复前：空 dict 命中第 1 层即短路，不走 draft 兜底。
    修复后：空 dict 跳过第 1 层，回退到 ctx[node_id].__pause_payload__.draft。
    """
    from packages.workflows.project_init.pipeline import _resolve_stage_input

    draft_data = {
        "title": "九天星辰诀",
        "genre": "玄幻",
        "selling_points": ["金手指独特"],
        "protagonist": {"name": "叶尘"},
    }
    ctx = {
        "human_input": {"revisions": {"premise_output": {}}},
        # pause_payload 模拟：premise_designer 节点挂起时的 AI 草稿
        "premise_designer": {"__pause_payload__": {"draft": draft_data}},
        # premise_output 不在 ctx（Pause 分支不写 ctx[output_key]，与引擎实现一致）
    }

    resolved = _resolve_stage_input(ctx, "premise")
    # 修复后应回退到 draft；修复前会返回 {}
    assert resolved == draft_data, (
        f"空 revisions 应回退到 checkpoint draft，"
        f"修复前会返回 {{}}。实得 resolved={resolved!r}"
    )
    assert resolved.get("title") == "九天星辰诀"


def test_resolve_stage_input_nonempty_revisions_take_precedence():
    """单测回归：非空 revisions 仍优先于 draft。"""
    from packages.workflows.project_init.pipeline import _resolve_stage_input

    revised = {
        "title": "改后书名",
        "genre": "玄幻",
        "selling_points": ["s1"],
        "protagonist": {"name": "叶尘"},
    }
    draft_data = {
        "title": "九天星辰诀",
        "genre": "玄幻",
        "selling_points": ["金手指独特"],
        "protagonist": {"name": "叶尘"},
    }
    ctx = {
        "human_input": {"revisions": {"premise_output": revised}},
        "premise_designer": {"__pause_payload__": {"draft": draft_data}},
    }

    resolved = _resolve_stage_input(ctx, "premise")
    assert resolved == revised, f"非空 revisions 应优先，实得 {resolved!r}"
    assert resolved.get("title") == "改后书名"


def test_resolve_stage_input_revisions_key_absent_falls_back_to_draft():
    """单测：revisions dict 缺少对应 output_key 时也回退到 draft（既有用法不变）。"""
    from packages.workflows.project_init.pipeline import _resolve_stage_input

    draft_data = {"title": "九天星辰诀", "genre": "玄幻"}
    ctx = {
        "human_input": {"revisions": {"other_output": {"foo": "bar"}}},
        "premise_designer": {"__pause_payload__": {"draft": draft_data}},
    }
    resolved = _resolve_stage_input(ctx, "premise")
    assert resolved == draft_data


def test_resolve_stage_input_falls_back_to_ctx_output_key():
    """单测：revisions 无值时回退到 ctx[output_key]（节点已 COMPLETED 的场景）。"""
    from packages.workflows.project_init.pipeline import _resolve_stage_input

    persisted = {"title": "已落库", "genre": "玄幻"}
    ctx = {
        "human_input": {"revisions": {}},
        "premise_output": persisted,
    }
    resolved = _resolve_stage_input(ctx, "premise")
    assert resolved == persisted


def test_project_init_step_mode_empty_revisions_persists_real_data(tmp_path: Path):
    """端到端：step_mode 全程传空 revisions，落库数据应来自 AI 草稿而非占位。

    修复前：world/character/outline 任一关传空 revisions 都会让
    _resolve_stage_input 第 1 层短路回空 dict，persist_all 用空数据
    落库并通过 _fallback_chapter_seeds 静默造 10 个占位章。
    修复后：空 revisions 视为未提供 → 回退到 draft → 落库与一次性跑通一致。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 3,
                    "step_mode": True,
                    "mock_providers": _step_mode_full_scripts(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            await _wait_run_terminal(app, run_id, expected=("PAUSED",))

            # 每关都传空 revisions dict 复现 bug：
            # 第 1 关：premise_output: {}
            # 第 2~4 关：world/character/outline_output: {}
            revisions_payloads = [
                {"premise_output": {}},
                {"world_output": {}, "character_output": {}, "outline_output": {}},
                {"world_output": {}, "character_output": {}, "outline_output": {}},
                {"world_output": {}, "character_output": {}, "outline_output": {}},
            ]
            for rev_payload in revisions_payloads:
                r = await _request(
                    app,
                    "POST",
                    f"/api/runs/{run_id}/resume",
                    json={"human_input": {"revisions": rev_payload}},
                )
                assert r.status_code == 200, r.text
                cur = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED", "COMPLETED"))
                if cur["status"] == "COMPLETED":
                    break
                await asyncio.sleep(0.2)

            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id and project_id.startswith("prj_")

            # 角色应来自 _character_script（3 个），不是空 / 占位
            r = await _request(app, "GET", f"/api/projects/{project_id}/characters")
            assert r.status_code == 200
            chars = r.json()
            assert len(chars) == 3, (
                f"空 revisions 不应让 character 落空/占位，实得 {len(chars)} 个角色: "
                f"{[c['name'] for c in chars]!r}"
            )
            names = {c["name"] for c in chars}
            assert names == {"叶尘", "苏婉清", "林渊"}

            # 章节种子数应来自请求（3），不是 fallback 占位 10
            conn = get_connection(app.state.settings.db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?",
                    (project_id,),
                ).fetchone()["c"]
            finally:
                conn.close()
            assert count == 3, (
                f"应为 3 章（空 revisions 不应触发 fallback 10 章占位），实得 {count}"
            )

    asyncio.run(run())


def test_project_init_step_mode_nonempty_revisions_still_take_effect(tmp_path: Path):
    """回归：非空 revisions 正常生效（修订穿透到 projects.name）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 3,
                    "step_mode": True,
                    "mock_providers": _step_mode_full_scripts(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            await _wait_run_terminal(app, run_id, expected=("PAUSED",))

            # 传非空修订 → 应穿透
            revised = {
                "title": "改后书名",
                "genre": "玄幻",
                "logline": "修订后的一句话",
                "positioning": "传统玄幻升级流",
                "selling_points": ["s1", "s2"],
                "protagonist": {"name": "叶尘"},
            }
            r = await _request(
                app,
                "POST",
                f"/api/runs/{run_id}/resume",
                json={"human_input": {"revisions": {"premise_output": revised}}},
            )
            assert r.status_code == 200, r.text

            import asyncio as _aio
            for _ in range(50):
                cur = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
                if cur["current_node"] == "world_builder":
                    break
                await _aio.sleep(0.1)
            assert cur["current_node"] == "world_builder"

            # 后续 resume 用空 human_input（不动 revisions），保证第 1 关的修订
            # 不会被后续空 revisions 覆盖——这是 project_init 的标准用法。
            for _ in range(3):
                r = await _request(
                    app,
                    "POST",
                    f"/api/runs/{run_id}/resume",
                    json={"human_input": {}},
                )
                cur = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED", "COMPLETED"))
                if cur["status"] == "COMPLETED":
                    break
                await _aio.sleep(0.2)

            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id

            r = await _request(app, "GET", f"/api/projects/{project_id}")
            assert r.status_code == 200
            proj = r.json()
            assert proj["name"] == "改后书名", f"非空 revisions 应穿透，实得 {proj['name']!r}"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# #10：persist_all 已存在实体应更新内容字段（修复前会被静默跳过）。
# ---------------------------------------------------------------------------


def _character_script_v2() -> list[str]:
    """第二次 init：把核心角色 core_json 内容全部改掉，name 保持兼容。"""
    return [
        json.dumps(
            {
                "schema_version": "character-design.v1",
                "prompt_version": "character_designer:v1",
                "characters": [
                    {
                        "name": "叶尘",
                        "role": "protagonist",
                        "core_json": {
                            "motivation": "改后动机：守护苍生",
                            "goal": "改后目标：踏入星辰境巅峰",
                            "conflict": "改后冲突：体内暗藏古神残念",
                            "distinctive_trait": "改后特征：沉稳果断",
                            "relationships": [
                                {"to_name": "苏婉清", "relation_type": "ally", "one_line": "改后关系描述"}
                            ],
                        },
                    },
                    {
                        "name": "苏婉清",
                        "role": "love_interest",
                        "core_json": {
                            "motivation": "改后：陪伴叶尘",
                            "goal": "改后：双修星辰诀",
                            "conflict": "改后：家族覆灭",
                            "distinctive_trait": "改后：坚毅果决",
                            "relationships": [],
                        },
                    },
                    {
                        "name": "林渊",
                        "role": "antagonist",
                        "core_json": {
                            "motivation": "改后：成神",
                            "goal": "改后：灭世",
                            "conflict": "改后：被宿命束缚",
                            "distinctive_trait": "改后：阴狠冷厉",
                            "relationships": [],
                        },
                    },
                ],
            },
            ensure_ascii=False,
        )
    ]


def _world_script_v2() -> list[str]:
    """第二次 init：把 location/faction/rule 的 statement 与 data 都改掉。"""
    return [
        json.dumps(
            {
                "schema_version": "world-build.v1",
                "prompt_version": "world_builder:v1",
                "core_premise": "改后核心设定",
                "rules": [
                    {
                        "name": "星辰共鸣",
                        "statement": "改后陈述：需以心血祭祀星辰方可共鸣",
                        "data": {"severity": "soft", "改后": True},
                    }
                ],
                "locations": [
                    {
                        "name": "青石城",
                        "statement": "改后陈述：已成废墟",
                        "data": {"layer": "地下", "importance": "起点-改后"},
                    }
                ],
                "factions": [
                    {
                        "name": "天星宗",
                        "statement": "改后陈述：内部已分裂",
                        "data": {"alignment": "混乱", "relation_to_protagonist": "敌对-改后"},
                    }
                ],
            },
            ensure_ascii=False,
        )
    ]


def _outline_script_v2(seed_count: int = 6) -> list[str]:
    """第二次 init：6 章。"""
    seeds = [
        {
            "number": i + 1,
            "title": f"改后第{i + 1}章",
            "role": "setup" if i < 2 else ("escalation" if i < 5 else "climax"),
            "one_sentence": f"改后本章事件{i + 1}",
            "expected_word_count": 2400,
            "key_beats": [f"改后_beat_{i + 1}_a"],
        }
        for i in range(seed_count)
    ]
    return [
        json.dumps(
            {
                "schema_version": "volume-outline.v1",
                "prompt_version": "volume_outliner:v1",
                "volume": {
                    "number": 1,
                    "title": "改后卷名",
                    "arc_summary": "改后卷摘要",
                },
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _mock_providers_v2(seed_count: int = 6) -> dict[str, list[str]]:
    return {
        "premise_designer": _premise_script(),
        "world_builder": _world_script_v2(),
        "character_designer": _character_script_v2(),
        "volume_outliner": _outline_script_v2(seed_count),
    }


def test_project_init_reinit_updates_existing_entities(tmp_path: Path, monkeypatch):
    """#10 修复：同一 project 二次 init，已存在 character/location/faction/rule
    应被更新内容字段，id/created_at 不变；章节数被新 seeds 替换。

    修复前：persist_all 按 name 命中即跳过，已存在实体的 core_json / data 等
    内容字段不会被覆盖，分段审阅中修订过的 AI 内容重新生成时被静默丢弃。
    修复后：存在 → UPDATE 内容字段（保留 id 与 created_at），章节走单事务重建。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            # 1) 第一次 init：3 角色 / 1 location / 1 faction / 1 rule / 5 章
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 5,
                    "mock_providers": _mock_providers(5),
                },
            )
            assert r.status_code == 201, r.text
            run_id_1 = r.json()["run_id"]
            await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id and project_id.startswith("prj_")

            # 抓住第一次落库的 character_id 与 created_at（红测基线）
            r = await _request(app, "GET", f"/api/projects/{project_id}/characters")
            assert r.status_code == 200
            chars_before = {c["name"]: c for c in r.json()}
            assert set(chars_before) == {"叶尘", "苏婉清", "林渊"}
            old_char_id_yc = chars_before["叶尘"]["character_id"]
            old_created_at_yc = chars_before["叶尘"]["created_at"]
            old_core_json_yc = chars_before["叶尘"]["core_json"]
            assert old_core_json_yc["motivation"] == "登临九天查明真相"

            r = await _request(app, "GET", f"/api/projects/{project_id}/locations")
            assert r.status_code == 200
            loc_before = r.json()[0]
            old_loc_id = loc_before["id"]
            old_loc_created_at = loc_before["created_at"]

            r = await _request(app, "GET", f"/api/projects/{project_id}/factions")
            assert r.status_code == 200
            fac_before = r.json()[0]
            old_fac_id = fac_before["id"]

            r = await _request(app, "GET", f"/api/projects/{project_id}/world-rules")
            assert r.status_code == 200
            rule_before = r.json()[0]
            old_rule_id = rule_before["id"]

            conn = get_connection(app.state.settings.db_path)
            try:
                chap_before = conn.execute(
                    "SELECT chapter_id, number, title FROM chapters "
                    "WHERE project_id = ? ORDER BY number",
                    (project_id,),
                ).fetchall()
            finally:
                conn.close()
            assert len(chap_before) == 5
            old_chap_ids = {r["chapter_id"] for r in chap_before}

            # 2) 第二次 init：相同 project_id，mock 内容全改；章节数改 6
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": project_id,
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 6,
                    "mock_providers": _mock_providers_v2(6),
                },
            )
            assert r.status_code == 201, r.text
            run_id_2 = r.json()["run_id"]
            await _wait_run_terminal(app, run_id_2, expected=("COMPLETED",))

            # 3) 校验：角色 id/created_at 不变，core_json 被更新
            r = await _request(app, "GET", f"/api/projects/{project_id}/characters")
            assert r.status_code == 200
            chars_after = {c["name"]: c for c in r.json()}
            yc_after = chars_after["叶尘"]
            assert yc_after["character_id"] == old_char_id_yc, (
                f"character_id 应不变；修复前会保持不变（但内容不更新），"
                f"修复后 id 不变但 content 应更新。旧={old_char_id_yc} 新={yc_after['character_id']}"
            )
            assert yc_after["created_at"] == old_created_at_yc, (
                f"created_at 应不变（旧={old_created_at_yc} 新={yc_after['created_at']}）"
            )
            assert yc_after["core_json"]["motivation"] == "改后动机：守护苍生", (
                f"core_json 应被更新为第二次 init 的内容；"
                f"修复前会保持旧值 {old_core_json_yc['motivation']!r} 不变（静默跳过），"
                f"实得 {yc_after['core_json']['motivation']!r}"
            )
            # role 也应被更新（也是 AI 可管字段）
            # 原 role=protagonist 不变；只验 core_json 内容更新 + id 不变

            # location / faction / rule id 不变，data/statement 更新
            r = await _request(app, "GET", f"/api/projects/{project_id}/locations")
            assert r.status_code == 200
            loc_after = r.json()[0]
            assert loc_after["id"] == old_loc_id, "location id 应保留"
            assert loc_after["created_at"] == old_loc_created_at, "location created_at 应保留"
            assert loc_after["statement"] == "改后陈述：已成废墟", (
                f"location statement 应被更新；修复前会保留旧值，实得 {loc_after['statement']!r}"
            )
            assert loc_after["data"]["importance"] == "起点-改后"

            r = await _request(app, "GET", f"/api/projects/{project_id}/factions")
            assert r.status_code == 200
            fac_after = r.json()[0]
            assert fac_after["id"] == old_fac_id
            assert fac_after["statement"] == "改后陈述：内部已分裂"

            r = await _request(app, "GET", f"/api/projects/{project_id}/world-rules")
            assert r.status_code == 200
            rule_after = r.json()[0]
            assert rule_after["id"] == old_rule_id
            assert rule_after["statement"] == "改后陈述：需以心血祭祀星辰方可共鸣"

            # 4) 章节被重建：旧 chapter_id 应全部消失，新 6 章
            conn = get_connection(app.state.settings.db_path)
            try:
                chap_after = conn.execute(
                    "SELECT chapter_id, number, title FROM chapters "
                    "WHERE project_id = ? ORDER BY number",
                    (project_id,),
                ).fetchall()
            finally:
                conn.close()
            new_chap_ids = {r["chapter_id"] for r in chap_after}
            assert len(chap_after) == 6, [r["number"] for r in chap_after]
            assert new_chap_ids.isdisjoint(old_chap_ids), (
                "旧 chapter_id 应被全部清除，新章走全新 ID"
            )
            # 新章标题来自 _outline_script_v2
            assert chap_after[0]["title"] == "改后第1章"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# #11：章节「先清后建」重建序列必须包进单事务；中途失败旧章应保留。
# ---------------------------------------------------------------------------


def test_project_init_chapter_rebuild_atomic_on_failure(tmp_path: Path, monkeypatch):
    """#11 修复：章节重建（DELETE 旧章 + INSERT 新章）必须包进单事务；中途异常
    触发 rollback，旧章应保留。

    修复前：先 DELETE + commit，再多次 chapter create + commit——中途失败时
    旧章已被删除、新章半写入，数据丢失。
    修复后：单连接 BEGIN→DELETE→INSERT→COMMIT；异常触发 ROLLBACK，旧章
    完整保留，新章一行不入库。

    注入策略：monkeypatch ``pipeline.new_id``，让第二次 init 的章节重建序列
    在第 2 个新 chapter INSERT 前抛错（new_id("ch") 是每个新 chapter 的
    第一次调用；第 2 次调用即抛错 → 第 1 个新 chapter 已 INSERT、第 2 个未
    INSERT，事务回滚把第 1 个新 chapter 也撤销）。
    """
    from packages.workflows.project_init import pipeline as pipeline_mod

    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)

            # 1) 第一次 init：3 章
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 3,
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id_1 = r.json()["run_id"]
            await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            project_id = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert project_id and project_id.startswith("prj_")

            # 旧章基线
            conn = get_connection(app.state.settings.db_path)
            try:
                chap_before = conn.execute(
                    "SELECT chapter_id, number, title FROM chapters "
                    "WHERE project_id = ? ORDER BY number",
                    (project_id,),
                ).fetchall()
            finally:
                conn.close()
            assert len(chap_before) == 3
            old_chap_ids = [r["chapter_id"] for r in chap_before]
            old_titles = [r["title"] for r in chap_before]
            assert old_titles == ["第1章", "第2章", "第3章"]

            # 2) 注入：第二次 init 时，让 pipeline.new_id 在第 2 次被调用时抛错。
            #    第二次 init 走 persist_all 章节重建序列：每个新 chapter 调 1 次
            #    new_id("ch")；第 2 次抛错 → 第 1 个新 chapter 已 INSERT、第 2
            #    个未 INSERT，事务异常 → ROLLBACK 应把第 1 个新 chapter 也撤销。
            original_new_id = pipeline_mod.new_id
            call_state = {"n": 0, "boom_at": 2}

            def maybe_boom_new_id(prefix: str) -> str:
                if prefix == "ch":
                    call_state["n"] += 1
                    if call_state["n"] == call_state["boom_at"]:
                        raise RuntimeError(
                            "injected: chapter rebuild mid-sequence failure"
                        )
                return original_new_id(prefix)

            monkeypatch.setattr(pipeline_mod, "new_id", maybe_boom_new_id)

            # 3) 第二次 init：3 章
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": project_id,
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 3,
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id_2 = r.json()["run_id"]
            final = await _wait_run_terminal(
                app, run_id_2, expected=("COMPLETED", "FAILED"), timeout=60.0,
            )
            # 注入异常应让 run 落入 FAILED（持久化失败冒泡到工作流层）
            assert final["status"] == "FAILED", (
                f"注入异常后 run 应为 FAILED；实得 status={final['status']!r}"
            )

            # 4) 核心断言：旧章必须仍存在，ID 与注入前完全一致；
            #    不应有任何「新章残留」（事务回滚把 INSERT 全部撤销）。
            conn = get_connection(app.state.settings.db_path)
            try:
                chap_after = conn.execute(
                    "SELECT chapter_id, number, title FROM chapters "
                    "WHERE project_id = ? ORDER BY number",
                    (project_id,),
                ).fetchall()
                drafts_rows = conn.execute(
                    "SELECT chapter_id FROM drafts WHERE chapter_id IN (?, ?, ?)",
                    tuple(old_chap_ids),
                ).fetchall()
            finally:
                conn.close()

            # 旧 3 章必须完整保留（顺序、ID、title 全部一致）
            assert [r["chapter_id"] for r in chap_after] == old_chap_ids, (
                f"修复前：旧章会被 DELETE 清空，新章半写入；"
                f"修复后：单事务回滚，旧章应保留。"
                f"实得 chapter_ids={[r['chapter_id'] for r in chap_after]} "
                f"期望={old_chap_ids}"
            )
            assert [r["title"] for r in chap_after] == old_titles
            # 不应有第 4/5/6... 章（新章 INSERT 全部回滚）
            assert len(chap_after) == 3, (
                f"事务回滚后旧章应仍为 3 条；实得 {len(chap_after)} 条"
            )
            # drafts 不应被孤立
            assert len(drafts_rows) == 0, (
                f"旧章未删 → 旧章的 drafts 也应保持 0；实得 {len(drafts_rows)} 条"
            )

    asyncio.run(run())
