"""Reference Canon API 路由测试（Sprint 11 上半）。

端点：
- ``POST /projects/{project_id}/deconstruct``  —— 启动 deconstruct-book workflow
- ``GET  /projects/{project_id}/canons``       —— 列出 active canon 摘要
- ``GET  /canons/{canon_id}``                  —— 全文 + report_md + extracts
- ``DELETE /canons/{canon_id}``                —— 级联删除（204）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "参照测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _sync_prompts(app) -> None:
    """同步 prompts（注册 director / writer / observer / deconstructor_* ACTIVE 行）"""
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(
        app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}"
    )
    assert r.status_code == 200, r.text


def _chapter_extract_mock() -> list[str]:
    """deconstructor_chapter mock script（list 模式，T2 节点负责逐章分发）。

    顺序：chapter1 / chapter2 / chapter3。
    """
    return [
        json.dumps(
            {
                "schema_version": "chapter-extract.v0",
                "chapter_index": 1,
                "event_pattern": "主角类型 1 被压制",
                "function_tag": "hook",
                "valence": 2,
                "hook_marker": "章末留悬念",
                "payoff_tags": [],
                "chapter_digest": "主角出场被压制",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "schema_version": "chapter-extract.v0",
                "chapter_index": 2,
                "event_pattern": "主角暗修传承",
                "function_tag": "setup",
                "valence": 0,
                "hook_marker": "章末留悬念",
                "payoff_tags": [],
                "chapter_digest": "暗中蓄力",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "schema_version": "chapter-extract.v0",
                "chapter_index": 3,
                "event_pattern": "家族大比首次胜",
                "function_tag": "climax",
                "valence": 7,
                "hook_marker": "反派反扑悬念",
                "payoff_tags": ["face_slap"],
                "chapter_digest": "反压制同辈",
            },
            ensure_ascii=False,
        ),
    ]


def _aggregate_mock() -> list[str]:
    canon = {
        "logline": "草根主角获逆袭金手指 → 家族比试首胜",
        "spine": [
            {
                "chapter_index": 1,
                "title_pattern": "主角受辱-偶获宝",
                "function_tag": "hook",
                "summary_pattern": "主角类型 1 出场被压制偶获物品类型 V",
            },
            {
                "chapter_index": 2,
                "title_pattern": "暗修传承-敌来挑衅",
                "function_tag": "setup",
                "summary_pattern": "主角类型 1 暗修传承",
            },
            {
                "chapter_index": 3,
                "title_pattern": "家族大比-首胜反转",
                "function_tag": "climax",
                "summary_pattern": "主角类型 1 反压制同辈",
            },
        ],
        "faction_map": {
            "factions": [
                {
                    "faction_id": "fac_hero",
                    "type_pattern": "主角派",
                    "power_layer": "low",
                },
                {
                    "faction_id": "fac_rival",
                    "type_pattern": "反派势力",
                    "power_layer": "mid",
                },
            ],
            "relations": [
                {
                    "from_faction_id": "fac_hero",
                    "to_faction_id": "fac_rival",
                    "relation_type": "hostile",
                }
            ],
            "power_layers": [
                {"layer": "low", "count": 1},
                {"layer": "mid", "count": 1},
            ],
        },
        "emotion_curve": [
            {"chapter_index": 1, "valence": 2, "marker_type": "buildup"},
            {"chapter_index": 2, "valence": 0, "marker_type": "buildup"},
            {"chapter_index": 3, "valence": 7, "marker_type": "release"},
        ],
        "payoff_list": [
            {
                "payoff_id": "payoff_001_fs",
                "chapter_index": 1,
                "type": "face_slap",
                "intensity": 2,
                "setup_chapter": 1,
                "payoff_chapter": 1,
            },
            {
                "payoff_id": "payoff_003_fs",
                "chapter_index": 3,
                "type": "face_slap",
                "intensity": 3,
                "setup_chapter": 1,
                "payoff_chapter": 3,
            },
        ],
        "techniques": [
            {
                "technique_id": "tech_001",
                "name_pattern": "黄金三章强制钩子",
                "location_pattern": "前三章章末",
                "effect_pattern": "前 300 字冲突前置 + 三章钩子",
            }
        ],
        "rhythm": {
            "mini_climax_interval": {"median": 3, "p25": 2, "p75": 5},
            "major_climax_interval": {"median": 5, "p25": 4, "p75": 7},
            "chapter_end_hook_rate": 0.8,
            "golden_three_compliance": {
                "first_300_chars_conflict": True,
                "ch1_end_hook": True,
                "ch2_end_hook": True,
                "ch3_end_hook": True,
                "mini_climax_in_first_three": True,
            },
        },
        "style_params": {
            "sentence_length_distribution": {"mean": 18.0, "median": 16.0, "max": 80},
            "dialogue_ratio": 0.25,
            "action_ratio": 0.45,
            "pov": "third_limited",
            "paragraph_length_distribution": {"mean": 120.0, "median": 100.0, "max": 600},
            "psychological_ratio": 0.15,
            "environment_ratio": 0.15,
        },
        "metadata": {
            "source_book_title": "<PLACEHOLDER>",
            "deconstruct_date": "2026-08-23T00:00:00+00:00",
            "deconstruct_version": "deconstruct-book-v0",
            "target_reader_profile": "male_fantasy",
            "license_check_status": {
                "checked": False,
                "license": "unknown",
                "compatible": False,
            },
        },
    }
    return [json.dumps(canon, ensure_ascii=False)]


_SAMPLE_BOOK_TEXT = (
    "第一章 少年被欺\n"
    "叶家演武台上，少年被同辈一掌震飞。众人哄笑，他咬牙站起。\n"
    "玉佩发出微光，苍老声音在脑海炸响。\n"
    "\n"
    "第二章 暗修传承\n"
    "主角暗中修习玉佩所授心法。同族反派数次挑衅，他避其锋芒。\n"
    "\n"
    "第三章 家族大比\n"
    "家族大比之日，主角在众人注视下首次展现实力，反压同辈。\n"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_canons_returns_summary_after_deconstruct(tmp_path: Path):
    """跑完 deconstruct 后 list 端点返回摘要（含 logline/spine_count）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={
                    "book_title": "API 测试参照书",
                    "text": _SAMPLE_BOOK_TEXT,
                    "reader_profile": "male_fantasy",
                    "mock_providers": {
                        "deconstructor_chapter": _chapter_extract_mock(),
                        "deconstructor_aggregate": _aggregate_mock(),
                    },
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["status"] == "COMPLETED", body
            assert "canon_id" in body
            canon_id = body["canon_id"]

            r = await _request(app, "GET", f"/api/projects/{pid}/canons")
            assert r.status_code == 200
            summaries = r.json()
            assert len(summaries) == 1
            s = summaries[0]
            assert s["canon_id"] == canon_id
            assert s["project_id"] == pid
            assert s["title"] == "API 测试参照书"
            assert s["status"] == "active"
            assert s["spine_count"] == 3
            assert s["rhythm_chapter_count"] == 3
            assert "logline" in s
            assert s["logline"] != ""

    asyncio.run(run())


def test_get_canon_returns_full_payload(tmp_path: Path):
    """GET /canons/{id} 返回 canon_json + report_md + extracts 列表。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={
                    "book_title": "Detail 测试",
                    "text": _SAMPLE_BOOK_TEXT,
                    "reader_profile": "male_fantasy",
                    "mock_providers": {
                        "deconstructor_chapter": _chapter_extract_mock(),
                        "deconstructor_aggregate": _aggregate_mock(),
                    },
                },
            )
            assert r.status_code == 201, r.text
            canon_id = r.json()["canon_id"]

            r = await _request(app, "GET", f"/api/canons/{canon_id}")
            assert r.status_code == 200
            detail = r.json()
            assert detail["canon_id"] == canon_id
            assert detail["title"] == "Detail 测试"
            assert detail["status"] == "active"
            assert detail["canon_json"]["metadata"]["source_book_title"] == "Detail 测试"
            assert (detail["report_md"] or "") != ""
            assert len(detail["extracts"]) == 3
            assert [e["chapter_index"] for e in detail["extracts"]] == [1, 2, 3]

    asyncio.run(run())


def test_get_unknown_canon_returns_404(tmp_path: Path):
    """GET 不存在的 canon_id → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/canons/can_nonexistent")
            assert r.status_code == 404

    asyncio.run(run())


def test_delete_canon_cascades_to_extracts(tmp_path: Path):
    """DELETE canon 级联删 extracts（断言行数 0）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={
                    "book_title": "Cascade 测试",
                    "text": _SAMPLE_BOOK_TEXT,
                    "reader_profile": "male_fantasy",
                    "mock_providers": {
                        "deconstructor_chapter": _chapter_extract_mock(),
                        "deconstructor_aggregate": _aggregate_mock(),
                    },
                },
            )
            assert r.status_code == 201, r.text
            canon_id = r.json()["canon_id"]

            # 断言 3 行 extracts 存在
            conn = get_connection(app.state.settings.db_path)
            try:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM canon_extracts WHERE canon_id = ?",
                    (canon_id,),
                ).fetchone()["n"]
                assert before == 3
            finally:
                conn.close()

            # DELETE → 204
            r = await _request(app, "DELETE", f"/api/canons/{canon_id}")
            assert r.status_code == 204

            # 断言 canon + extracts 行数 = 0
            conn = get_connection(app.state.settings.db_path)
            try:
                canon_n = conn.execute(
                    "SELECT COUNT(*) AS n FROM reference_canons WHERE canon_id = ?",
                    (canon_id,),
                ).fetchone()["n"]
                extracts_n = conn.execute(
                    "SELECT COUNT(*) AS n FROM canon_extracts WHERE canon_id = ?",
                    (canon_id,),
                ).fetchone()["n"]
            finally:
                conn.close()
            assert canon_n == 0
            assert extracts_n == 0

            # 二次 GET 应 404
            r = await _request(app, "GET", f"/api/canons/{canon_id}")
            assert r.status_code == 404

    asyncio.run(run())


def test_list_canons_for_project_without_canon(tmp_path: Path):
    """项目无 canon → 返回空列表（200，不 404）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(app, "GET", f"/api/projects/{pid}/canons")
            assert r.status_code == 200
            assert r.json() == []

    asyncio.run(run())


def test_deconstruct_unknown_project_returns_404(tmp_path: Path):
    """项目不存在 → 启动 deconstruct 返 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/projects/prj_nonexistent/deconstruct",
                json={
                    "book_title": "X",
                    "text": "第一章 X\n正文",
                },
            )
            assert r.status_code == 404

    asyncio.run(run())