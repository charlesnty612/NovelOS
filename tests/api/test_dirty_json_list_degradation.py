"""chapter / character / world 列--表的**脏 JSON 行降级**契约（2026-10-16 批次九）。

背景（与 ``test_plot_dirty_json_row_degradation.py`` 同形，但落在另一个面上）
------------------------------------------------------------------------------
2026-10-15 批次（六）给 plot / relationships 的列表端点挂 ``response_model`` 时，
L1 成员实测发现：service 侧裸 ``json.loads`` 读一个 JSON 列，一行脏数据（非法文本
**或**合法 JSON 但形状不符）会让 ``json.loads`` / pydantic 抛值冒到路由层 ⇒
**整个列表端点 500，干净行一起陪葬**。当时处置了 plot 侧（``_safe_json`` 兜底）。

2026-10-16 批次九复算同一形状，发现 **chapter / character / world 三处从未处置**——
而且它们**同一个函数里**的兄弟字段（chapter 的 ``outline_json`` / ``who_knows``、
character 的 ``who_knows`` / ``aliases`` / ``inject_mode``）**早已兜底**，唯漏的那几处
是裸 ``json.loads``（与 ``update_hook`` 的三列漏 ``model_fields_set`` 同一种「笔误
不是设计」的形状）。实测（临时库 + 直接塞脏行）：

    脏列                            端点                                        修复前
    ------------------------------  ------------------------------------------  ------
    chapters.plan_json              GET /projects/{pid}/chapters, /chapters/{id}  500
    characters.core_json            GET /projects/{pid}/characters, /characters/{} 500
    character_states.state_json     GET /characters/{id}/states, 列表的 latest     500
    locations.data_json             GET /projects/{pid}/locations                 500

处置：三处读路径统一走 ``write_helpers.decode_json_field``（与 ``decode_who_knows``
同源：解析失败 / 形状不符 ⇒ 该字段退回它的空值）。**这组用例守的是降级方向**——
把任一调用改回裸 ``json.loads`` 必须让对应用例变红。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id


def _app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="ERROR"))


def _get(app, path: str):
    import asyncio

    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.get(path)

    return asyncio.run(go())


def _project(app) -> str:
    import asyncio

    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return (await c.post("/api/projects", json={"name": "脏行"})).json()[
                    "project_id"
                ]

    return asyncio.run(go())


def _exec(app, sql: str, params: tuple) -> None:
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# chapters.plan_json
# ---------------------------------------------------------------------------


def test_chapters_list_survives_dirty_plan_json(tmp_path: Path):
    """一行脏 ``plan_json`` 不得让 ``GET /projects/{pid}/chapters`` 500。"""
    app = _app(tmp_path)
    pid = _project(app)
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("ch"), pid, 1, "干净章", json.dumps({"a": 1}), "PLANNED", "VISIBLE",
         None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("ch"), pid, 2, "脏章", "not json at all", "PLANNED", "VISIBLE",
         None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    r = _get(app, f"/api/projects/{pid}/chapters")
    assert r.status_code == 200, f"一行脏 plan_json 让整个列表 500：{r.text[:200]}"
    rows = {x["title"]: x for x in r.json()}
    assert set(rows) == {"干净章", "脏章"}, rows
    assert rows["脏章"]["plan_json"] == {}, rows["脏章"]["plan_json"]
    # 干净行不得被连累
    assert rows["干净章"]["plan_json"] == {"a": 1}, rows["干净章"]


def test_chapter_detail_survives_dirty_plan_json(tmp_path: Path):
    """单体 ``GET /chapters/{id}`` 同款。"""
    app = _app(tmp_path)
    pid = _project(app)
    cid = new_id("ch")
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, pid, 1, "脏章", "not json at all", "PLANNED", "VISIBLE",
         None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    r = _get(app, f"/api/chapters/{cid}")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["plan_json"] == {}


def test_chapter_wrong_shaped_plan_json_also_degrades(tmp_path: Path):
    """合法 JSON 但**形状不对**（数组而非对象）也降级——``json.loads`` 不解析语义。"""
    app = _app(tmp_path)
    pid = _project(app)
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("ch"), pid, 1, "数组章", "[1,2,3]", "PLANNED", "VISIBLE",
         None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    r = _get(app, f"/api/projects/{pid}/chapters")
    assert r.status_code == 200, r.text[:200]
    assert r.json()[0]["plan_json"] == {}, r.json()[0]


def test_chapter_wrong_shaped_who_knows_degrades_to_null(tmp_path: Path):
    """``chapters.who_knows`` 存成对象（而非数组）⇒ 降级为 ``null``，端点仍 200。"""
    app = _app(tmp_path)
    pid = _project(app)
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("ch"), pid, 1, "脏 who", "{}", "PLANNED", "VISIBLE",
         '{"a": 1}', "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    r = _get(app, f"/api/projects/{pid}/chapters")
    assert r.status_code == 200, r.text[:200]
    assert r.json()[0]["who_knows"] is None, r.json()[0]


# ---------------------------------------------------------------------------
# characters.core_json / character_states.state_json
# ---------------------------------------------------------------------------


def _seed_character(app, pid: str, *, core_json: str, name: str) -> str:
    cid = new_id("char")
    _exec(
        app,
        "INSERT INTO characters (character_id, project_id, name, role, core_json,"
        " aliases, inject_mode, who_knows, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, pid, name, "supporting", core_json, "[]", "auto", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    return cid


def test_characters_list_survives_dirty_core_json(tmp_path: Path):
    app = _app(tmp_path)
    pid = _project(app)
    _seed_character(app, pid, core_json=json.dumps({"flaws": ["x"]}), name="干净角")
    _seed_character(app, pid, core_json="not json at all", name="脏角")

    r = _get(app, f"/api/projects/{pid}/characters")
    assert r.status_code == 200, f"一行脏 core_json 让整个列表 500：{r.text[:200]}"
    rows = {x["name"]: x for x in r.json()}
    assert set(rows) == {"干净角", "脏角"}, rows
    assert rows["脏角"]["core_json"] == {}, rows["脏角"]["core_json"]
    assert rows["干净角"]["core_json"] == {"flaws": ["x"]}, rows["干净角"]


def test_character_detail_survives_dirty_core_json(tmp_path: Path):
    app = _app(tmp_path)
    pid = _project(app)
    cid = _seed_character(app, pid, core_json="not json at all", name="脏角")
    r = _get(app, f"/api/characters/{cid}")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["core_json"] == {}


def test_characters_list_survives_dirty_latest_state_json(tmp_path: Path):
    """``character_states.state_json`` 脏 ⇒ 列表行的 ``latest_state_json`` 退 ``{}``。"""
    app = _app(tmp_path)
    pid = _project(app)
    cid = _seed_character(app, pid, core_json="{}", name="脏状态角")
    _exec(
        app,
        "INSERT INTO character_states (character_id, state_version, state_json,"
        " visibility, who_knows, created_at) VALUES (?,?,?,?,?,?)",
        (cid, 2, "not json at all", "VISIBLE", None, "2026-01-01T00:00:00Z"),
    )
    r = _get(app, f"/api/projects/{pid}/characters")
    assert r.status_code == 200, r.text[:200]
    row = next(x for x in r.json() if x["character_id"] == cid)
    assert row["latest_state_json"] == {}, row


def test_character_states_list_survives_dirty_state_json(tmp_path: Path):
    app = _app(tmp_path)
    pid = _project(app)
    cid = _seed_character(app, pid, core_json="{}", name="脏状态角")
    _exec(
        app,
        "INSERT INTO character_states (character_id, state_version, state_json,"
        " visibility, who_knows, created_at) VALUES (?,?,?,?,?,?)",
        (cid, 2, "not json at all", "VISIBLE", None, "2026-01-01T00:00:00Z"),
    )
    r = _get(app, f"/api/characters/{cid}/states")
    assert r.status_code == 200, f"一行脏 state_json 让整个状态列表 500：{r.text[:200]}"
    dirty = next(x for x in r.json() if x["state_version"] == 2)
    assert dirty["state_json"] == {}, dirty


# ---------------------------------------------------------------------------
# quality_reports.*_json（形状维度：合法 JSON 但类型不符）
# ---------------------------------------------------------------------------


def _seed_report(app, pid: str, ch: str, *, scores: str, issues: str, overall: int) -> None:
    _exec(
        app,
        "INSERT INTO quality_reports (report_id, project_id, chapter_id, overall,"
        " scores_json, issues_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (new_id("qr"), pid, ch, overall, scores, issues, "2026-01-01T00:00:00Z"),
    )


def test_quality_list_survives_wrong_shaped_issues_json(tmp_path: Path):
    """``issues_json`` 存成对象（expects list）⇒ 降级 ``[]``，质量报告列表仍 200。"""
    app = _app(tmp_path)
    pid = _project(app)
    ch = new_id("ch")
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ch, pid, 1, "C", "{}", "PLANNED", "VISIBLE", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    _seed_report(app, pid, ch, scores=json.dumps({"pacing": 80}), issues="[]", overall=80)
    _seed_report(app, pid, ch, scores=json.dumps({"pacing": 70}), issues='{"a": 1}', overall=70)

    r = _get(app, f"/api/projects/{pid}/quality")
    assert r.status_code == 200, f"一行形状错的 issues_json 让整个列表 500：{r.text[:200]}"
    assert len(r.json()) == 2, r.json()
    assert all(x["issues_json"] == [] for x in r.json()), r.json()


def test_quality_list_survives_wrong_shaped_scores_json(tmp_path: Path):
    """``scores_json`` 存成数组（expects object）⇒ 降级 ``{}``。"""
    app = _app(tmp_path)
    pid = _project(app)
    ch = new_id("ch")
    _exec(
        app,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
        " status, visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ch, pid, 1, "C", "{}", "PLANNED", "VISIBLE", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    _seed_report(app, pid, ch, scores="[1,2,3]", issues="[]", overall=60)

    r = _get(app, f"/api/projects/{pid}/quality")
    assert r.status_code == 200, r.text[:200]
    assert r.json()[0]["scores_json"] == {}, r.json()[0]


# ---------------------------------------------------------------------------
# world entities (locations / factions / world_rules).data_json
# ---------------------------------------------------------------------------


def test_locations_list_survives_dirty_data_json(tmp_path: Path):
    app = _app(tmp_path)
    pid = _project(app)
    for name, data in (("干净地", json.dumps({"climate": "寒"})), ("脏地", "not json at all")):
        _exec(
            app,
            "INSERT INTO locations (location_id, project_id, name, statement, data_json,"
            " visibility, who_knows, aliases, inject_mode, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("loc"), pid, name, "s", data, "PUBLIC", None, "[]", "auto",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    r = _get(app, f"/api/projects/{pid}/locations")
    assert r.status_code == 200, f"一行脏 data_json 让整个列表 500：{r.text[:200]}"
    rows = {x["name"]: x for x in r.json()}
    assert rows["脏地"]["data"] == {}, rows["脏地"]
    assert rows["干净地"]["data"] == {"climate": "寒"}, rows["干净地"]


def test_factions_and_world_rules_share_the_guard(tmp_path: Path):
    """factions / world_rules 走同一个 ``_row_to_entity`` —— 一并守护。"""
    app = _app(tmp_path)
    pid = _project(app)
    _exec(
        app,
        "INSERT INTO factions (faction_id, project_id, name, statement, data_json,"
        " visibility, who_knows, aliases, inject_mode, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("fac"), pid, "脏势力", "s", "not json", "PUBLIC", None, "[]", "auto",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    _exec(
        app,
        "INSERT INTO world_rules (world_rule_id, project_id, name, statement, data_json,"
        " visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id("wr"), pid, "脏规则", "s", "not json", "PUBLIC", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    rf = _get(app, f"/api/projects/{pid}/factions")
    rw = _get(app, f"/api/projects/{pid}/world-rules")
    assert rf.status_code == 200, rf.text[:200]
    assert rw.status_code == 200, rw.text[:200]
    assert rf.json()[0]["data"] == {}, rf.json()[0]
    assert rw.json()[0]["data"] == {}, rw.json()[0]
