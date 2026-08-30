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


# ---------------------------------------------------------------------------
# model_profile_id 透传：ctx 注入 → 4 个 AI 节点 run_agent kwargs 校验
# ---------------------------------------------------------------------------


def _fake_outputs_by_agent() -> dict[str, dict]:
    """按 agent 名返回合法假产出：覆盖 pipeline 各 _run_* 节点 out.setdefault 的
    关键字段；最小化 mock 字段。"""
    return {
        "premise_designer": {
            "title": "T",
            "genre": "G",
            "logline": "L",
            "positioning": "P",
            "selling_points": [],
            "protagonist": {},
        },
        "world_builder": {
            "core_premise": "",
            "rules": [],
            "locations": [],
            "factions": [],
        },
        "character_designer": {
            "characters": [],
        },
        "volume_outliner": {
            "volume": {"number": 1, "title": "", "arc_summary": ""},
            "chapter_seeds": [],
        },
    }


def test_project_init_model_profile_id_propagates_to_all_ai_nodes(monkeypatch):
    """ctx 含 model_profile_id='mprof_x' 时，4 个 AI 节点的 run_agent 调用
    都应透传 profile_id='mprof_x'。"""
    from packages.workflows.project_init import pipeline as pipeline_mod

    fake_outs = _fake_outputs_by_agent()
    captured: list[dict] = []

    def fake_run_agent(*args, **kwargs):
        captured.append({"args": args, "kwargs": dict(kwargs)})
        # args: (db_path, agent_name, input_payload, run_id)
        agent_name = args[1]
        return dict(fake_outs[agent_name])

    monkeypatch.setattr(pipeline_mod, "run_agent", fake_run_agent)

    # 公共 ctx：4 个节点都需要的最小字段；db_path 留占位（不会真连库）。
    base_ctx = {
        "db_path": "/tmp/nonex.db",
        "brief": {"genre": "玄幻", "logline": "x", "title": "T"},
        "model_profile_id": "mprof_x",
        "chapter_seed_count": 3,
    }

    pipeline_mod._run_premise_designer(base_ctx)
    pipeline_mod._run_world_builder(base_ctx)
    pipeline_mod._run_character_designer(base_ctx)
    pipeline_mod._run_volume_outliner(base_ctx)

    assert len(captured) == 4, f"应有 4 次 run_agent 调用，实得 {len(captured)}"
    agents_called = [c["args"][1] for c in captured]
    assert agents_called == [
        "premise_designer",
        "world_builder",
        "character_designer",
        "volume_outliner",
    ], agents_called
    for c in captured:
        assert c["kwargs"].get("profile_id") == "mprof_x", (
            f"agent={c['args'][1]!r} 应透传 profile_id='mprof_x'，"
            f"实得 kwargs={c['kwargs']!r}"
        )


def test_project_init_model_profile_id_absent_yields_none(monkeypatch):
    """ctx 不含 model_profile_id 时，4 个 AI 节点的 run_agent 调用应
    profile_id=None（走全局 capability_bindings / model_configs）。"""
    from packages.workflows.project_init import pipeline as pipeline_mod

    fake_outs = _fake_outputs_by_agent()
    captured: list[dict] = []

    def fake_run_agent(*args, **kwargs):
        captured.append({"args": args, "kwargs": dict(kwargs)})
        agent_name = args[1]
        return dict(fake_outs[agent_name])

    monkeypatch.setattr(pipeline_mod, "run_agent", fake_run_agent)

    base_ctx = {
        "db_path": "/tmp/nonex.db",
        "brief": {"genre": "玄幻", "logline": "x", "title": "T"},
        # 故意不放 model_profile_id
        "chapter_seed_count": 3,
    }

    pipeline_mod._run_premise_designer(base_ctx)
    pipeline_mod._run_world_builder(base_ctx)
    pipeline_mod._run_character_designer(base_ctx)
    pipeline_mod._run_volume_outliner(base_ctx)

    assert len(captured) == 4
    for c in captured:
        assert c["kwargs"].get("profile_id") is None, (
            f"agent={c['args'][1]!r} 应 profile_id=None，"
            f"实得 kwargs={c['kwargs']!r}"
        )


def test_project_init_route_rejects_unknown_model_profile_id(tmp_path: Path):
    """POST /projects/init 带不存在的 model_profile_id 应返回 400，且
    不写入 workflow_runs 行（启动前校验失败）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "brief": {"genre": "玄幻", "logline": "测试"},
                    "chapter_seed_count": 3,
                    "model_profile_id": "mprof_nonexistent",
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 400, r.text
            assert "mprof_nonexistent" in r.json()["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 部分生成（selected_stages 子集）：未选环节从 DB 重建，persist_all 不落占位
# ---------------------------------------------------------------------------


def test_project_init_partial_world_only_skips_volume_chapters(tmp_path: Path):
    """#13 部分生成契约：selected_stages=['world'] 时仅跑 world_builder AI 节点，
    premise/character/outline 从 DB 重建为下游输入。persist_all 检测到
    outline._degraded=True（DB 中无卷/章可重建）时**不**写占位卷 / 占位章节 /
    plot_event，返回 ``outline_skipped=True``、``volume_id=None``、
    ``chapter_ids=[]``、``event_id=None``。

    修复前：outline 重建走 ``_rebuild_outline_from_db`` 但 DB 无卷/章 →
    ``empty`` 分支 + ``_degraded=True``；persist_all 仍调
    ``_normalize_chapter_seeds([], ...)`` 触发 ``_fallback_chapter_seeds`` 兜底
    创建 10 个占位章节 + `_upsert_volume` 建空卷；并写入 type=other 的 plot_event
    占位事件，污染 DB。
    """
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
                    "chapter_seed_count": 3,
                    "selected_stages": ["world"],
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            run_id = payload["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))

            # 1) persist_all 返回值含 outline_skipped / volume_id=None / chapter_ids=[]
            conn = get_connection(app.state.settings.db_path)
            try:
                node_row = conn.execute(
                    "SELECT output_json FROM workflow_run_nodes "
                    "WHERE run_id = ? AND node_id = ?",
                    (run_id, "persist_all"),
                ).fetchone()
            finally:
                conn.close()
            assert node_row is not None
            persist_out = json.loads(node_row["output_json"])
            assert persist_out["outline_skipped"] is True, (
                f"outline 降级时 persist 应标记 outline_skipped=True；实得 {persist_out!r}"
            )
            assert persist_out["volume_id"] is None, (
                f"outline 降级时不应落卷；实得 volume_id={persist_out['volume_id']!r}"
            )
            assert persist_out["chapter_ids"] == [], (
                f"outline 降级时不应落占位章节；实得 {persist_out['chapter_ids']!r}"
            )
            assert persist_out["event_id"] is None, (
                f"outline 降级时不应落 plot_event；实得 event_id={persist_out['event_id']!r}"
            )
            assert persist_out["persisted"] is True

            # 2) DB 校验：volumes / chapters 该 project 行数必须为 0（无占位）
            conn = get_connection(app.state.settings.db_path)
            try:
                vol_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM volumes WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
                chap_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
                char_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM characters WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
                plot_event_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM plot_events WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
            finally:
                conn.close()
            assert vol_count == 0, f"部分生成不应落占位卷；实得 {vol_count} 条"
            assert chap_count == 0, f"部分生成不应落占位章节；实得 {chap_count} 条"
            assert char_count == 0, "character 环节未选，DB 不应有角色行"
            assert plot_event_count == 0, (
                f"部分生成不应落 plot_event；实得 {plot_event_count} 条"
            )

            # 3) world 实体确实落库（世界 1 rule / 1 location / 1 faction）
            r = await _request(app, "GET", f"/api/projects/{pid}/locations")
            assert r.status_code == 200
            assert len(r.json()) == 1
            r = await _request(app, "GET", f"/api/projects/{pid}/factions")
            assert r.status_code == 200
            assert len(r.json()) == 1
            r = await _request(app, "GET", f"/api/projects/{pid}/world-rules")
            assert r.status_code == 200
            assert len(r.json()) == 1

            # 4) checkpoint_json 含 project_id（仍指向同一条记录）
            assert (run_data.get("checkpoint_json") or {}).get("project_id") == pid

    asyncio.run(run())


def test_project_init_full_pipeline_unaffected_by_partial_skip(tmp_path: Path):
    """#13 全量回归：selected_stages 缺省时（None）走全流程；persist_all
    outline._degraded=False（AI 产出合法 volume + chapter_seeds），**不**触发
    outline_skipped 分支，volume / chapters / plot_event 正常落库。

    修复前：若 outline_skipped 误判把全量生成也跳过，volumes / chapters 表行数
    为 0 + outline_skipped=True，会让全量用例红。这是 test_project_init_creates_new_project
    之外的独立断言，明确把 outline_skipped=False 锁进回归。
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
                    "brief": {
                        "genre": "玄幻",
                        "logline": "少年叶尘偶得星辰古卷",
                    },
                    "chapter_seed_count": 3,
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            run_id = payload["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            pid = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert pid and pid.startswith("prj_")

            # persist_all 应标记 outline_skipped=False + 正常落卷/章/plot_event
            conn = get_connection(app.state.settings.db_path)
            try:
                node_row = conn.execute(
                    "SELECT output_json FROM workflow_run_nodes "
                    "WHERE run_id = ? AND node_id = ?",
                    (run_id, "persist_all"),
                ).fetchone()
                vol_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM volumes WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
                chap_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
            finally:
                conn.close()
            assert node_row is not None
            persist_out = json.loads(node_row["output_json"])
            assert persist_out["outline_skipped"] is False, (
                f"全量生成时 outline_skipped 应为 False；实得 {persist_out!r}"
            )
            assert isinstance(persist_out["volume_id"], str) and persist_out["volume_id"]
            assert len(persist_out["chapter_ids"]) == 3
            assert vol_count == 1
            assert chap_count == 3

    asyncio.run(run())


# ---------------------------------------------------------------------------
# V3.10 init 空壳 plot_event 修复：persist_all 应把 volume.arc_summary 作为
# description 写入 type='other' status='planned' 的 plot_event，重复 init
# 不堆叠（命中即复用旧 id），timeline_events 对应索引行也带 description。
# ---------------------------------------------------------------------------


def test_project_init_persist_all_records_arc_summary_as_description(tmp_path: Path):
    """#14 init plot_event 描述修复：persist_all 第 6 步把 arc_summary 写入
    plot_event.description（修复前 create_event 不支持 description，arc_summary
    被静默丢弃 → 空壳事件）。

    - 全量 init 后 plot_events 中 type='other' AND status='planned' 的事件
      description 应等于 arc_summary（来自 outline JSON）；
    - timeline_events 对应索引行 description 也应等于 arc_summary；
    - 同时 volumes.arc_summary 列已被写（持久化收口，迁移 0020 落列）。
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
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            pid = (run_data.get("checkpoint_json") or {}).get("project_id")
            assert pid and pid.startswith("prj_")

            conn = get_connection(app.state.settings.db_path)
            try:
                # plot_event 描述 = arc_summary
                pe_row = conn.execute(
                    "SELECT event_id, description FROM plot_events "
                    "WHERE project_id = ? AND type = 'other' AND status = 'planned'",
                    (pid,),
                ).fetchone()
                assert pe_row is not None, "init 应至少落一条 type='other' plot_event"
                assert pe_row["description"] == "叶尘从废脉少年踏上星辰之路", (
                    f"plot_event.description 应等于 arc_summary；"
                    f"实得 {pe_row['description']!r}"
                )
                # timeline_events 对应索引行也带 description
                te_row = conn.execute(
                    "SELECT description FROM timeline_events "
                    "WHERE event_id = ?",
                    (pe_row["event_id"],),
                ).fetchone()
                assert te_row is not None
                assert te_row["description"] == "叶尘从废脉少年踏上星辰之路", (
                    f"timeline_events.description 应等于 arc_summary；"
                    f"实得 {te_row['description']!r}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_project_init_reinit_does_not_duplicate_arc_summary_event(tmp_path: Path):
    """#14 init plot_event 描述防重：同 project 重复跑 init，已有相同 arc_summary
    的占位 plot_event 不应被堆叠——持久化层命中即复用其 id（同时复用 timeline
    索引行），plot_events + timeline_events 各只 1 条。

    修复前：每次 init 都调一次 create_event（无 description），产生 N 条
    description=NULL 的空壳，污染 timeline。
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
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id_1 = r.json()["run_id"]
            await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            run_data = await _wait_run_terminal(app, run_id_1, expected=("COMPLETED",))
            pid = (run_data.get("checkpoint_json") or {}).get("project_id")

            # 第二次 init：mock 内容不变（arc_summary 相同），应复用 event_id
            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": pid,
                    "brief": {"genre": "玄幻", "logline": "少年叶尘偶得星辰古卷"},
                    "chapter_seed_count": 3,
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id_2 = r.json()["run_id"]
            await _wait_run_terminal(app, run_id_2, expected=("COMPLETED",))

            conn = get_connection(app.state.settings.db_path)
            try:
                pe_rows = conn.execute(
                    "SELECT event_id, description FROM plot_events "
                    "WHERE project_id = ? AND type = 'other' AND status = 'planned'",
                    (pid,),
                ).fetchall()
                te_rows = conn.execute(
                    "SELECT event_id, description FROM timeline_events "
                    "WHERE project_id = ?",
                    (pid,),
                ).fetchall()
            finally:
                conn.close()

            assert len(pe_rows) == 1, (
                f"重复 init 不应堆叠 plot_event；实得 {len(pe_rows)} 条 "
                f"{[r['event_id'] for r in pe_rows]}"
            )
            assert pe_rows[0]["description"] == "叶尘从废脉少年踏上星辰之路"
            # timeline_events 对应此 event_id 的索引行也只 1 条（创建事件时同步
            # 落索引；防重分支复用 event_id，不再新插 timeline 行）
            te_for_event = [r for r in te_rows if r["event_id"] == pe_rows[0]["event_id"]]
            assert len(te_for_event) == 1, (
                f"对应 event 的 timeline 索引行只 1 条；实得 {len(te_for_event)} 条"
            )
            assert te_for_event[0]["description"] == "叶尘从废脉少年踏上星辰之路"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# P1.2 GET /runs/{id} PAUSED 携带 stage_models：按 agent 名聚合最近一次
# 成功（error IS NULL）的 model_id；错误行不计入；多次调用取最新。
# ---------------------------------------------------------------------------


def test_get_run_paused_attaches_stage_models(tmp_path: Path):
    """GET /runs/{id} PAUSED 时 stage_models 携带本次 run 实际使用的模型。

    - 真实 mock provider 调起 premise_designer → ai_call_logs 写入 model_id='mock/mock'
    - 直插两条 premise_designer（不同 model_id、晚者应胜出）+ 一条失败行（应被排除）
    - 直插另一 agent 错误行（应被排除）
    - GET /runs/{id} 返回 stage_models 含两 agent 名 → model_id
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
            run_id = r.json()["run_id"]
            run_dict = await _wait_run_terminal(app, run_id, expected=("PAUSED",))
            assert run_dict["current_node"] == "premise_designer"

            # 直插额外 ai_call_logs 验证聚合规则：
            # 1) premise_designer 旧行（应被覆盖）：model_id=openai/gpt-4o
            # 2) premise_designer 新行（应胜出）：model_id=openai_compatible/k3-256k
            # 3) premise_designer 错误行（应被排除）：error='boom'
            # 4) world_builder 错误行（应被排除）：error='world-fail'
            db_path = app.state.settings.db_path
            from packages.core.ids import new_id
            from packages.core.db import get_connection
            from packages.core.ids import now_iso

            with get_connection(db_path) as conn:
                # 取 premise_designer / world_builder 的 agent_id
                p_id = conn.execute(
                    "SELECT agent_id FROM agents WHERE name = ?",
                    ("premise_designer",),
                ).fetchone()["agent_id"]
                w_id = conn.execute(
                    "SELECT agent_id FROM agents WHERE name = ?",
                    ("world_builder",),
                ).fetchone()["agent_id"]
                base_ts = now_iso()

                def _insert(
                    agent_id: str,
                    model_id: str,
                    ts: str,
                    error: str | None = None,
                    call_id: str | None = None,
                ) -> None:
                    conn.execute(
                        """
                        INSERT INTO ai_call_logs (
                            call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
                            input_context_ids_json, output_json, token_usage_json, latency_ms,
                            cost, error, retry_count, created_at
                        ) VALUES (?, ?, NULL, ?, ?, 'manual:v1', '[]', NULL, NULL, 0,
                                  NULL, ?, 0, ?)
                        """,
                        (
                            call_id or new_id("aic"),
                            run_id,
                            agent_id,
                            model_id,
                            error,
                            ts,
                        ),
                    )

                # 让 premise_designer 的「最新成功」变成 openai_compatible/k3-256k
                # （晚于真实 mock 落库时间——用更晚的 created_at 覆盖之）
                _insert(p_id, "openai/gpt-4o", base_ts + "z0")
                _insert(p_id, "openai_compatible/k3-256k", base_ts + "z9")
                # 错误行：因 created_at 较新，验证 error 过滤是否仍生效
                _insert(p_id, "openai_compatible/error-model", base_ts + "za", error="boom")
                _insert(w_id, "anthropic/claude-3", base_ts + "z8", error="world-fail")
                conn.commit()

            detail = await _get_run_via_http(app, run_id)
            assert detail is not None
            assert detail["status"] == "PAUSED"
            assert "stage_models" in detail, (
                f"GET /runs/{id} PAUSED 应携带 stage_models；实得 keys={list(detail.keys())}"
            )
            stage_models = detail["stage_models"]
            assert isinstance(stage_models, dict)

            # premise_designer 应为最新成功调用（k3-256k），不是 gpt-4o，也不是 error-model
            assert stage_models.get("premise_designer") == "openai_compatible/k3-256k", (
                f"premise_designer 应取最新成功 model_id；实得 {stage_models!r}"
            )
            # world_builder 仅写过错误行 → 不应出现
            assert "world_builder" not in stage_models, (
                f"world_builder 仅错误行，应被排除；实得 {stage_models!r}"
            )

    asyncio.run(run())


def test_get_run_paused_attaches_stage_models_with_warn_prefix(tmp_path: Path):
    """附带 warn 前缀的「重试后成功」调用仍计入 stage_models（与 error 失败行区分）。

    - premise_designer 真实调用 + 直插一条 error='warn: first attempt invalid: ...'
      的「重试成功」行（晚于真实 mock）→ 应作为最新有效 model_id 被聚合。
    - 直插一条 error='real-failure' 的失败行（晚于 warn 行）→ 应被排除。
    - 期望：stage_models['premise_designer'] == warn 行 model_id。
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
            run_id = r.json()["run_id"]
            run_dict = await _wait_run_terminal(app, run_id, expected=("PAUSED",))
            assert run_dict["current_node"] == "premise_designer"

            db_path = app.state.settings.db_path
            from packages.core.ids import new_id
            from packages.core.db import get_connection
            from packages.core.ids import now_iso

            with get_connection(db_path) as conn:
                p_id = conn.execute(
                    "SELECT agent_id FROM agents WHERE name = ?",
                    ("premise_designer",),
                ).fetchone()["agent_id"]
                base_ts = now_iso()

                def _insert(
                    agent_id: str,
                    model_id: str,
                    ts: str,
                    error: str | None = None,
                    call_id: str | None = None,
                ) -> None:
                    conn.execute(
                        """
                        INSERT INTO ai_call_logs (
                            call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
                            input_context_ids_json, output_json, token_usage_json, latency_ms,
                            cost, error, retry_count, created_at
                        ) VALUES (?, ?, NULL, ?, ?, 'manual:v1', '[]', NULL, NULL, 0,
                                  NULL, ?, 0, ?)
                        """,
                        (
                            call_id or new_id("aic"),
                            run_id,
                            agent_id,
                            model_id,
                            error,
                            ts,
                        ),
                    )

                # warn 前缀的「重试成功」行：晚于真实 mock 落库时间，应胜出
                _insert(
                    p_id,
                    "openai_compatible/k3-warn",
                    base_ts + "z9",
                    error="warn: first attempt invalid: no JSON object braces found",
                )
                # 真正的失败行：比 warn 行更晚，应被排除
                _insert(
                    p_id,
                    "openai_compatible/should-not-win",
                    base_ts + "za",
                    error="real-failure",
                )
                conn.commit()

            detail = await _get_run_via_http(app, run_id)
            assert detail is not None
            assert detail["status"] == "PAUSED"
            assert "stage_models" in detail
            stage_models = detail["stage_models"]
            assert isinstance(stage_models, dict)
            # warn 前缀软告警行被视为「成功」，应进入聚合；失败行被排除
            assert stage_models.get("premise_designer") == "openai_compatible/k3-warn", (
                f"premise_designer 应取最新「warn 软告警」行 model_id；实得 {stage_models!r}"
            )
            assert "openai_compatible/should-not-win" not in stage_models.values(), (
                f"真实失败行应被 stage_models 排除；实得 {stage_models!r}"
            )

    asyncio.run(run())


def test_get_run_non_paused_has_no_stage_models(tmp_path: Path):
    """非 PAUSED 状态的 run：响应不附加 stage_models（响应体最小化语义）。"""
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
                    # 不开 step_mode：一次性跑完，最终 COMPLETED
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            final = await _wait_run_terminal(app, run_id, expected=("COMPLETED",))
            assert final["status"] == "COMPLETED"
            assert "pause_payload" not in final, (
                f"非 PAUSED 不应携带 pause_payload；实得 keys={list(final.keys())}"
            )
            assert "stage_models" not in final, (
                f"非 PAUSED 不应携带 stage_models；实得 keys={list(final.keys())}"
            )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# premise 套娃污染回归：partial 生成（selected_stages 不含 premise）时，
# projects.premise 应保持库中现状不被复合文本「定位：…一句话：…」叠层。
# ---------------------------------------------------------------------------


def test_project_init_partial_world_preserves_existing_premise_text(tmp_path: Path):
    """用例 A（污染回归）：预置 project 行 premise 为复合文本
    「定位：旧定位\\n一句话：旧一句话」，selected_stages=['world'] 时跑完后
    projects.premise 应与预置完全一致（未叠任何前缀、未拼接新 logline）。

    修复前：_persist_all_node 拿重建后的 premise（含完整复合文本）走
    _build_premise_text，再包一层「定位：」+「一句话：」落库，每跑一次叠一层。
    修复后：premise 环节本次未选 → 跳过 project update 分支，库文本不动。
    """
    app = _create_app(tmp_path)
    preserved_premise = "定位：旧定位\n一句话：旧一句话"

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "旧项目")

            # 预置复合文本（模拟历史被污染/修复后的库现状）
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE projects SET premise = ?, genre = ? WHERE project_id = ?",
                    (preserved_premise, "玄幻", pid),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app,
                "POST",
                "/api/projects/init",
                json={
                    "project_id": pid,
                    "brief": {
                        "genre": "玄幻",
                        "logline": "新一句话——不应写入",
                    },
                    "chapter_seed_count": 3,
                    "selected_stages": ["world"],
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))

            # 核心断言：projects.premise 必须与预置完全一致，未被叠加任何前缀。
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT name, premise, genre FROM projects WHERE project_id = ?",
                    (pid,),
                ).fetchone()
            finally:
                conn.close()
            assert row["premise"] == preserved_premise, (
                f"修复前：premise 环节未选时仍会被叠一层「定位：」+「一句话：」"
                f"变成「{preserved_premise}\\n卖点：…\\n一句话：新的一句话」之类；"
                f"修复后：项目简介应原样保留。"
                f"实得 premise={row['premise']!r}（应保持 {preserved_premise!r}）"
            )
            # name 也不应在 premise 未选时被覆盖（避免误覆盖用户原有书名）
            assert row["name"] == "旧项目", (
                f"premise 环节未选时 name 不应被覆盖；实得 name={row['name']!r}"
            )

    asyncio.run(run())


def test_project_init_premise_selected_updates_premise_text(tmp_path: Path):
    """用例 B（正常路径回归）：selected_stages 含 premise（或 None 全选）时
    project_svc.update 应照常发生——projects.premise 被新生成的定位/卖点/一句话
    拼接文本覆盖。

    既有用例 test_project_init_creates_new_project / test_project_init_attaches_to_existing_project
    / test_project_init_reinit_updates_existing_entities 等均走全量生成且断言了
    projects.premise 含新内容，本用例用 selected_stages=['premise'] 单独触发
    该路径以锁定未来回归。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "旧项目")

            # 预置旧 premise
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE projects SET premise = ? WHERE project_id = ?",
                    ("旧文本", pid),
                )
                conn.commit()
            finally:
                conn.close()

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
                    "chapter_seed_count": 3,
                    "selected_stages": ["premise"],
                    "mock_providers": _mock_providers(3),
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            await _wait_run_terminal(app, run_id, expected=("COMPLETED",))

            # premise 环节被选中 → 应被新生成的复合文本覆盖
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT name, premise FROM projects WHERE project_id = ?",
                    (pid,),
                ).fetchone()
            finally:
                conn.close()
            assert row["name"] == "九天星辰诀", (
                f"premise 被选中时 name 应被 AI 产出的 title 覆盖；"
                f"实得 name={row['name']!r}"
            )
            assert row["premise"] != "旧文本", (
                "premise 被选中时 premise 文本应被新内容覆盖，"
                "未覆盖则 _persist_all_node 的 project update 分支未执行"
            )
            assert "星辰古卷" in row["premise"], (
                f"新 premise 应含 logline「星辰古卷」；实得 {row['premise']!r}"
            )
            assert "定位：传统玄幻升级流" in row["premise"], (
                f"新 premise 应含「定位：…」（来自 _premise_script）；"
                f"实得 {row['premise']!r}"
            )

    asyncio.run(run())


def test_build_premise_text_strips_redundant_positioning_prefix():
    """用例 C（防御单元）：_build_premise_text 在 positioning 已带「定位：」
    前缀时应剥掉该层后再拼接，杜绝「定位：定位：…」套娃。"""
    from packages.workflows.project_init.pipeline import _build_premise_text

    # 1) positioning 已带「定位：」前缀 → 不应叠第二层
    premise = {"positioning": "定位：已污染的定位", "selling_points": []}
    brief = {"logline": "一句话"}
    out = _build_premise_text(premise, brief)
    # 期望：「定位：已污染的定位\\n卖点：（无）\\n一句话：一句话」
    assert out.count("定位：") == 1, (
        f"positioning 已带「定位：」时不应再叠一层；实得 {out!r}"
    )
    assert "定位：已污染的定位" in out
    assert "一句话：一句话" in out

    # 2) 干净 positioning（不带前缀）→ 行为与既有契约一致
    premise_clean = {"positioning": "传统玄幻升级流", "selling_points": ["s1", "s2"]}
    brief_clean = {"logline": "少年叶尘偶得星辰古卷"}
    out_clean = _build_premise_text(premise_clean, brief_clean)
    assert out_clean == "定位：传统玄幻升级流\n卖点：s1 / s2\n一句话：少年叶尘偶得星辰古卷", (
        f"干净输入应保持既有契约；实得 {out_clean!r}"
    )
