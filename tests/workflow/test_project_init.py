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
            assert payload["status"] == "COMPLETED", payload
            assert payload["current_node"] is None
            project_id = payload["project_id"]
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
            assert payload["status"] == "COMPLETED"
            assert payload["project_id"] == pid

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
            assert payload["status"] == "COMPLETED"

            run_id = payload["run_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                # 校验 world_builder 节点是 COMPLETED（节点级降级不抛错）
                row = conn.execute(
                    "SELECT status, output_json FROM workflow_run_nodes WHERE run_id = ? AND node_id = ?",
                    (run_id, "world_builder"),
                ).fetchone()
            finally:
                conn.close()
            assert row["status"] == "COMPLETED"
            output = json.loads(row["output_json"])
            assert output["world_output"]["_degraded"] is True

            # 项目仍应落库成功
            pid = payload["project_id"]
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

            assert payload["status"] == "PAUSED", payload
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

            # 尚未落库
            assert payload["project_id"] is None

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
            assert start["status"] == "PAUSED"
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
            assert after["status"] == "PAUSED", after
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
            assert r.json()["status"] == "PAUSED"

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
            assert r.json()["status"] == "PAUSED"
            assert r.json()["current_node"] == "world_builder"

            # 第 2~4 关：不带 revisions 直接推进（依赖 _resolve_stage_input
            # 第二层 ctx[output_key] 兜底）
            for _ in range(3):
                r = await _request(
                    app,
                    "POST",
                    f"/api/runs/{run_id}/resume",
                    json={"human_input": {}},
                )
                assert r.status_code == 200, r.text
                st = r.json()["status"]
                if st == "COMPLETED":
                    break
                assert st == "PAUSED", r.json()
            assert r.json()["status"] == "COMPLETED", r.json()
            # 最后一关 resume 返回即应含 project_id（与落库项目一致）
            completed_resp = r.json()
            assert "project_id" in completed_resp, completed_resp
            project_id = completed_resp["project_id"]
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
            assert payload["status"] == "COMPLETED", payload
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
            assert payload["status"] == "COMPLETED"
            assert "pause_payload" not in payload

    asyncio.run(run())
