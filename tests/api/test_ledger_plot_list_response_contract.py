"""ledger / plot 的 5 个 **GET 列表**端点的 ``response_model`` 契约测试。

背景
----
上一轮（批次（六）G2）补的是**单体**端点；列表端点当时仍是裸 ``list[dict]`` ⇒
OpenAPI 里 items 是**匿名 object** ⇒ 前端拿到的还是 ``Record<string, unknown>``，
与「完全没建模」是同一形状。本组把 5 个列表端点全部建模，并按本组口径钉住：

    已建模 ≡ 该端点的 200 响应 schema 是 ``array`` 且其 ``items`` 是 ``$ref``。

覆盖的 5 个端点::

    GET /api/projects/{pid}/hooks           -> list[Hook]
    GET /api/projects/{pid}/debts           -> list[Debt]
    GET /api/projects/{pid}/events          -> list[PlotEventOut]
    GET /api/projects/{pid}/timeline        -> list[TimelineEventOut]
    GET /api/projects/{pid}/relationships   -> list[RelationshipOut]

字段形状怎么来的（本仓坑区已登记两次同族事故，故写死流程）
----------------------------------------------------------
**不是照源码读出来的，也不是「应该和单体一样」推出来的**。``response_model`` 会**过滤**
未声明字段：少声明一个 ⇒ 响应静默少一个，而功能测试常常照旧全绿；反过来，若基准是从
「跑一下看返回什么」抄来的，缺陷会被写进断言、绿灯把缺陷锁死。Lead 手上就有现成的
反例：``GET /commits`` 列表行 12 键，而 ``POST /commits`` 响应只有 4 键。

故本文件的 ``EXPECTED_*`` 来自**三个互相独立的来源**，缺一不可：

1. **DB 的 DDL**（``PRAGMA table_info``）——ledger 读路径是 ``SELECT *`` ⇒ 键集 ≡ 列集；
   plot 则是 ``to_dict()`` 的镜像（**≠** 列集，见下）；
2. **临时库 + 合成夹具跑通端点 dump 的实际响应**（每端点造 2 行：一个「全空」、
   一个「全字段带值」，另跑一次过滤参数），建法 = ``Settings(data_dir=tmp)`` +
   ``app.router.lifespan_context`` + ``httpx.ASGITransport``，**全程无真实 LLM 调用**；
3. 两条写路径（单体 / 列表）都实测过，才敢复用同一个模型。

实测结论（2026-10-15 首次；``relationships`` 一行于 2026-10-16 复测）::

    hooks          12 键  ≡ hooks 表 12 列  ≡ 单体 GET 响应
    debts          11 键  ≡ narrative_debts 表 11 列 ≡ 单体 GET 响应
    events         13 键  ≡ to_dict（≠ plot_events 的 13 列：cause_json→cause 等）
    timeline        8 键  ≡ to_dict（≠ timeline_events 的 8 列：timeline_event_id→id）
    relationships   9 键  ≡ relationships 表 9 列（2026-10-16 修复后）

``relationships`` 原先是**唯一的反向形态**：表 **9** 列而响应只有 **7** 键——
``visibility`` / ``who_knows`` 被 ``PlotService._row_to_relationship`` 丢弃。当时缺口
**不在本组文件边界内**（``PlotService`` 不是我的文件），于是没有绿灯锁死它，而是
``test_relationships_list_drops_visibility_and_who_knows_columns_today`` 把差异显式登记
成**会红的漂移告警**（DB 9 列 vs 响应 7 键）。

2026-10-16 服务层补上映射后，那条告警**已反转**成新事实
（``test_relationships_list_covers_every_column``）：基准仍取 ``PRAGMA table_info``
（**不抄数字**），断言「9 列 − 2 个改名 = 7 个直呼键，且响应无缺席列」。
**告警的价值不在于它当时是红的，而在于它还在**——下次谁再加一列又忘了映射，它照样红。

禁自指断言
----------
期望键集全部硬编码，**刻意不写成** ``set(body[0]) == set(Model.model_fields)``：
那两侧同源，模型少声明一个字段时一起变小 ⇒ 仍「逐字相等」⇒ 断言恒真。
本文件里 ``model_fields`` **只允许出现在** ``test_model_fields_match_hardcoded_expectation``
一条里，且它比的是**硬编码常量**。

又因四个模型都 ``extra="allow"``（读路径可能随迁移加列），「少声明一个字段」**响应照样
有那个键**（作为 extra 透出）⇒ 响应断言抓不到 ⇒ 必须另断言 OpenAPI 的 ``properties`` /
``required``（``test_openapi_item_schema_properties_and_required_match_expectation``）。

判别
----
每条突变各自对应一条会红的用例（见 ``tests/mutation_specs/l1_ledger_plot_list.py``）。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.domain.ledger import Debt, Hook
from packages.domain.plot.models import PlotEventOut, RelationshipOut, TimelineEventOut

# ===========================================================================
# 期望键集 —— 硬编码的第三方基准（**不是** model_fields，见模块 docstring）
# ===========================================================================

EXPECTED_HOOK_KEYS = {
    "hook_id",
    "project_id",
    "name",
    "introduced_chapter_id",
    "status",
    "importance",
    "expected_payoff_chapter_id",
    "payoff_chapter_id",
    "visibility",
    "who_knows",
    "created_at",
    "updated_at",
}
EXPECTED_DEBT_KEYS = {
    "debt_id",
    "project_id",
    "description",
    "created_chapter_id",
    "severity",
    "deadline_chapter_id",
    "status",
    "visibility",
    "who_knows",
    "created_at",
    "updated_at",
}
EXPECTED_PLOT_EVENT_KEYS = {
    "id",  # ⚠️ 不是 event_id（DB 主键列名），是 to_dict 改名后的产物
    "project_id",
    "type",
    "cause",  # ⚠️ 不是 cause_json
    "effects",
    "participants",
    "location_id",
    "time",  # ⚠️ 不是 time_json
    "status",
    "introduced_chapter_id",
    "visibility",
    "who_knows",
    "description",
}
EXPECTED_TIMELINE_KEYS = {
    "id",  # ⚠️ 不是 timeline_event_id
    "project_id",
    "event_id",
    "day_index",
    "time_ref",
    "description",
    "visibility",
    "who_knows",
}
EXPECTED_RELATIONSHIP_KEYS = {
    "id",  # ⚠️ 不是 relationship_id
    "project_id",
    "from_character_id",  # 0022 后端点可以是 faction_id，不再有 characters 外键
    "to_character_id",
    "relation_type",
    "state",  # ⚠️ 不是 state_json（已解码）
    "last_state_version",
    # ⚠️ 2026-10-16 新增：这两键原先**不在**本常量里（实测响应只有 7 键）。
    # 修复 ``PlotService._row_to_relationship`` 丢弃它们之后，键集 ≡ 表列集 ≡ 9。
    "visibility",
    "who_knows",
}

# 5 个列表端点 → （path, 期望 items 的 $ref 名）
_LIST_ENDPOINTS = [
    ("/api/projects/{pid}/hooks", "Hook"),
    ("/api/projects/{pid}/debts", "Debt"),
    ("/api/projects/{pid}/events", "PlotEventOut"),
    ("/api/projects/{pid}/timeline", "TimelineEventOut"),
    ("/api/projects/{pid}/relationships", "RelationshipOut"),
]


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


def _item_ref(node: dict[str, Any]) -> str | None:
    """从 200 响应的 schema 节点取出 ``items.$ref``。

    已建模的口径：``array`` 且 ``items`` 是 ``$ref``。裸 ``list[dict]`` 会走成
    ``{"items": {"type": "object"}, "type": "array"}`` ⇒ 这里返回 ``None``。
    ``allOf`` 包装（FastAPI 某些版本对 response_model 的处理）一并穿透。
    """
    if "$ref" in node:
        return node["$ref"]
    items = node.get("items")
    if isinstance(items, dict):
        return items.get("$ref")
    for sub in node.get("allOf", []):
        got = _item_ref(sub)
        if got is not None:
            return got
    return None


def _assert_keys(body: dict, expected: set[str], label: str) -> None:
    assert isinstance(body, dict), f"{label} 应是对象，实际 {type(body)}"
    assert set(body) == expected, (
        f"{label} 键集不符：多={sorted(set(body) - expected)} 少={sorted(expected - set(body))}"
    )


async def _seed(app) -> dict[str, str]:
    """造一个带章节 / 角色 / 地点的项目，返回 id 包（无任何 LLM 调用）。"""

    async with _client(app) as c:
        pid = (await c.post("/api/projects", json={"name": "L1 列表契约"})).json()["project_id"]
        cid = (
            await c.post(f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"})
        ).json()["chapter_id"]
        char = (await c.post(f"/api/projects/{pid}/characters", json={"name": "林昭"})).json()
        loc = (await c.post(f"/api/projects/{pid}/locations", json={"name": "客栈"})).json()
    return {
        "pid": pid,
        "cid": cid,
        "char_id": char["character_id"],
        "loc_id": loc["id"],
    }


def _seeded(app) -> dict[str, str]:
    return asyncio.run(_seed(app))


def _seed_ledger(app, ids: dict[str, str]) -> None:
    """每个列表端点造 **2 行**：一行全空、一行全字段带值（证明「键恒在」而非「有时在」）。"""

    async def run() -> None:
        async with _client(app) as c:
            pid, cid = ids["pid"], ids["cid"]
            r = await c.post(f"/api/projects/{pid}/hooks", json={"name": "三年之约"})
            assert r.status_code == 201, r.text
            r = await c.post(
                f"/api/projects/{pid}/hooks",
                json={
                    "name": "铜钥匙",
                    "introduced_chapter_id": cid,
                    "expected_payoff_chapter_id": cid,
                    "status": "ACTIVE",
                    "importance": 0.9,
                    "visibility": "VISIBLE",
                    "who_knows": ["char_x"],
                },
            )
            assert r.status_code == 201, r.text
            r = await c.post(f"/api/projects/{pid}/debts", json={"description": "欠下的交代"})
            assert r.status_code == 201, r.text
            r = await c.post(
                f"/api/projects/{pid}/debts",
                json={
                    "description": "第三条命",
                    "created_chapter_id": cid,
                    "deadline_chapter_id": cid,
                    "status": "acknowledged",
                    "severity": 0.8,
                    "visibility": "VISIBLE",
                    "who_knows": [],
                },
            )
            assert r.status_code == 201, r.text

    asyncio.run(run())


def _seed_plot(app, ids: dict[str, str]) -> None:
    """造 1 个空事件 + 1 个全字段事件（cause 须指向已存在事件）。建事件会自动同步 timeline 行。"""

    async def run() -> None:
        async with _client(app) as c:
            pid, cid = ids["pid"], ids["cid"]
            first = await c.post(f"/api/projects/{pid}/events", json={"type": "revelation"})
            assert first.status_code == 201, first.text
            second = await c.post(
                f"/api/projects/{pid}/events",
                json={
                    "type": "conflict",
                    "cause": [first.json()["id"]],
                    "effects": [],
                    "participants": [ids["char_id"]],
                    "location_id": ids["loc_id"],
                    "time": {"timeline_day": 5, "in_story_date": "d5"},
                    "status": "recorded",
                    "introduced_chapter_id": cid,
                    "visibility": "VISIBLE",
                    "who_knows": [ids["char_id"]],
                    "description": "客栈夜斗",
                },
            )
            assert second.status_code == 201, second.text
            # 手工时间线行：visibility 走 POST 端点的缺省 'PUBLIC'，与事件自动同步行不同
            manual = await c.post(
                f"/api/projects/{pid}/timeline",
                json={"event_id": second.json()["id"], "day_index": 9, "time_ref": "07:30"},
            )
            assert manual.status_code == 201, manual.text

    asyncio.run(run())


def _seed_relationships(app, ids: dict[str, str]) -> None:
    """relationships 是**只读**端点（写入由 State Delta 驱动）⇒ 直接往临时库插合成行。

    迁移 0022 之后该表只剩 ``project_id`` 一个外键，端点可以是 character_id 或
    faction_id ⇒ 用不存在的 id 也能插入，正好覆盖「端点不是角色」这条真实用法。
    """

    async def run() -> None:
        from packages.core.db import get_connection

        async with app.router.lifespan_context(app):
            conn = get_connection(app.state.settings.db_path)
            try:
                for rel_id, src, dst, kind, state, ver, vis, knows in (
                    ("rel_l1_1", "char_from", "char_to", "ally", {"intensity": 0.7}, 3, "PUBLIC", None),
                    (
                        "rel_l1_2",
                        "fac_from",
                        "fac_to",
                        "enemy",
                        {},
                        1,
                        "RESTRICTED",
                        json.dumps(["char_z"], ensure_ascii=False),
                    ),
                ):
                    conn.execute(
                        "INSERT INTO relationships (relationship_id, project_id,"
                        " from_character_id, to_character_id, relation_type, state_json,"
                        " last_state_version, visibility, who_knows) VALUES (?,?,?,?,?,?,?,?,?)",
                        (rel_id, ids["pid"], src, dst, kind, json.dumps(state), ver, vis, knows),
                    )
                conn.commit()
            finally:
                conn.close()

    asyncio.run(run())


def _table_columns(app, table: str) -> set[str]:
    """读临时库的 ``PRAGMA table_info``（模型之外的独立事实源）。"""

    async def run() -> set[str]:
        from packages.core.db import get_connection

        async with app.router.lifespan_context(app):
            conn = get_connection(app.state.settings.db_path)
            try:
                return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            finally:
                conn.close()

    return asyncio.run(run())


# ===========================================================================
# 1) OpenAPI：列表响应必须是 array + items.$ref（撤掉 response_model 即红）
# ===========================================================================


def test_list_endpoints_declare_typed_items(tmp_path: Path):
    """本组的核心判别：items 是 ``$ref``，不是匿名 object。

    撤掉任一端点的 ``response_model`` ⇒ 这里 ``$ref`` 变 ``None`` ⇒ 必红。
    """
    schema = _create_app(tmp_path).openapi()
    for name in ("Hook", "Debt", "PlotEventOut", "TimelineEventOut", "RelationshipOut"):
        assert name in schema["components"]["schemas"], f"{name} 未进 OpenAPI"
    for path, name in _LIST_ENDPOINTS:
        node = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        ref = _item_ref(node)
        assert ref is not None, (
            f"GET {path} 的响应不是「array + items.$ref」（实际 {node!r}）—— "
            f"前端拿到的仍是 Record<string, unknown>"
        )
        assert ref.endswith(f"/{name}"), f"GET {path} 的 items 应引用 {name}，实际 {ref!r}"


def test_openapi_item_schema_properties_and_required_match_expectation(tmp_path: Path):
    """⚠️ 模型带 ``extra="allow"`` 时**必须**有这一条。

    ``extra="allow"`` 会掩盖「删掉一个字段声明」：响应照样透出那个键（作为 extra），
    响应侧的键集断言仍然绿 ⇒ 只有 schema 的 ``properties`` / ``required`` 会少。

    基准是硬编码常量，**不是** ``model_fields``——否则两侧同减又恒真。
    """
    schema = _create_app(tmp_path).openapi()
    for name, expected in (
        ("Hook", EXPECTED_HOOK_KEYS),
        ("Debt", EXPECTED_DEBT_KEYS),
        ("PlotEventOut", EXPECTED_PLOT_EVENT_KEYS),
        ("TimelineEventOut", EXPECTED_TIMELINE_KEYS),
        ("RelationshipOut", EXPECTED_RELATIONSHIP_KEYS),
    ):
        sc = schema["components"]["schemas"][name]
        assert set(sc["properties"]) == expected, (
            f"{name} 的 schema.properties 不符："
            f"多={sorted(set(sc['properties']) - expected)} 少={sorted(expected - set(sc['properties']))}"
        )
        # 可空列也声明成 X | None 且**不给默认值** ⇒ 全部进 required（键恒在、值为 null）
        assert set(sc["required"]) == expected, (
            f"{name} 的 required 不符：多={sorted(set(sc['required']) - expected)} "
            f"少={sorted(expected - set(sc['required']))}。"
            f"⚠️ 若可空列被给了默认值，schema 会把它变成可选，前端得写 ?? 兜底（G10 病）"
        )


# ===========================================================================
# 2) 实际响应行键集 vs 硬编码期望
#    ⚠️ 每条都**先断言行数 > 0**：空数组量不到任何东西，断言会恒真。
# ===========================================================================


def test_hooks_list_row_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_ledger(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/hooks")).json()
            assert len(rows) == 2, f"夹具没造出 2 行（实际 {len(rows)}）——空数组的断言是恒真的"
            for i, row in enumerate(rows):
                _assert_keys(row, EXPECTED_HOOK_KEYS, f"GET hooks row[{i}]")
            by_name = {r["name"]: r for r in rows}
            # 空值行：键在、值为 null（不是省略）
            empty = by_name["三年之约"]
            assert empty["status"] == "OPEN" and empty["importance"] == 0.5
            assert empty["visibility"] == "RESTRICTED"
            assert empty["who_knows"] is None, "新建时 who_knows 应为 null（不是 []）"
            for col in ("introduced_chapter_id", "expected_payoff_chapter_id", "payoff_chapter_id"):
                assert col in empty and empty[col] is None, f"{col} 应存在且为 null"

            # 带值行：确认「键在」不是「键恒为空」
            full = by_name["铜钥匙"]
            assert full["status"] == "ACTIVE" and full["importance"] == 0.9
            assert full["visibility"] == "VISIBLE" and full["who_knows"] == ["char_x"]
            assert full["introduced_chapter_id"] == ids["cid"]
            assert full["created_at"] and full["updated_at"]

            # 过滤参数只收窄行数，不改行形状
            filtered = (
                await c.get(f"/api/projects/{ids['pid']}/hooks", params={"status": "ACTIVE"})
            ).json()
            assert len(filtered) == 1, filtered
            _assert_keys(filtered[0], EXPECTED_HOOK_KEYS, "GET hooks ?status=ACTIVE")
            assert (
                await c.get(f"/api/projects/{ids['pid']}/hooks", params={"status": "RESOLVED"})
            ).json() == [], "过滤到空集时应返回 [] 而不是报错"

    asyncio.run(run())


def test_debts_list_row_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_ledger(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/debts")).json()
            assert len(rows) == 2, f"夹具没造出 2 行（实际 {len(rows)}）"
            for i, row in enumerate(rows):
                _assert_keys(row, EXPECTED_DEBT_KEYS, f"GET debts row[{i}]")
            by_desc = {r["description"]: r for r in rows}
            empty = by_desc["欠下的交代"]
            assert empty["status"] == "open" and empty["severity"] == 0.5
            assert empty["visibility"] == "RESTRICTED" and empty["who_knows"] is None
            for col in ("created_chapter_id", "deadline_chapter_id"):
                assert col in empty and empty[col] is None, f"{col} 应存在且为 null"

            full = by_desc["第三条命"]
            assert full["status"] == "acknowledged" and full["severity"] == 0.8
            # 三态语义：显式 [] ⇒ 空列表而非 null
            assert full["who_knows"] == [], full["who_knows"]
            assert full["deadline_chapter_id"] == ids["cid"]

            filtered = (
                await c.get(f"/api/projects/{ids['pid']}/debts", params={"status": "paid"})
            ).json()
            assert filtered == [], filtered

    asyncio.run(run())


def test_events_list_row_keys_match_hardcoded_expectation(tmp_path: Path):
    """事件：响应键名 ≠ 表列名（``id``/``cause``/``time``），本用例正是那道闸门。"""
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_plot(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/events")).json()
            assert len(rows) == 2, f"夹具没造出 2 行（实际 {len(rows)}）"
            for i, row in enumerate(rows):
                _assert_keys(row, EXPECTED_PLOT_EVENT_KEYS, f"GET events row[{i}]")
                assert "event_id" not in row, "响应主键键名是 id，不是 event_id"
                assert not any(k.endswith("_json") for k in row), (
                    "cause/effects/participants/time 已是解码后的键，_json 后缀不该出现"
                )
            by_type = {r["type"]: r for r in rows}
            empty = by_type["revelation"]
            assert empty["cause"] == [] and empty["effects"] == [] and empty["participants"] == []
            assert empty["time"] == {"timeline_day": 1, "in_story_date": None}
            assert empty["status"] == "planned" and empty["visibility"] == "RESTRICTED"
            for col in ("location_id", "introduced_chapter_id", "who_knows", "description"):
                assert col in empty and empty[col] is None, f"{col} 应存在且为 null"

            full = by_type["conflict"]
            assert full["cause"] and full["cause"][0].startswith("event_"), full["cause"]
            assert full["participants"] == [ids["char_id"]]
            assert full["location_id"] == ids["loc_id"]
            assert full["time"] == {"timeline_day": 5, "in_story_date": "d5"}
            assert full["status"] == "recorded" and full["visibility"] == "VISIBLE"
            assert full["who_knows"] == [ids["char_id"]]
            assert full["description"] == "客栈夜斗"
            assert full["introduced_chapter_id"] == ids["cid"]

            # 两个过滤参数各自收窄行数，行形状不变
            for params, label in (({"type": "conflict"}, "type"), ({"status": "recorded"}, "status")):
                filtered = (
                    await c.get(f"/api/projects/{ids['pid']}/events", params=params)
                ).json()
                assert len(filtered) == 1, f"?{label}= 过滤后应有 1 行，实际 {len(filtered)}"
                _assert_keys(filtered[0], EXPECTED_PLOT_EVENT_KEYS, f"GET events ?{label}")

    asyncio.run(run())


def test_timeline_list_row_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_plot(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/timeline")).json()
            # 2 个事件各自动同步 1 条（day 1 / day 5）+ 1 条手工行（day 9）
            assert len(rows) == 3, f"应有 3 行（实际 {len(rows)}）"
            for i, row in enumerate(rows):
                _assert_keys(row, EXPECTED_TIMELINE_KEYS, f"GET timeline row[{i}]")
                assert "timeline_event_id" not in row, "响应主键键名是 id"
            assert [r["day_index"] for r in rows] == [1, 5, 9], [r["day_index"] for r in rows]

            by_day = {r["day_index"]: r for r in rows}
            # ⚠️ 自动同步行继承事件的 visibility（RESTRICTED / VISIBLE），
            # 手工行走 POST 端点缺省 'PUBLIC' —— 写混了会直接改变剧透可见性。
            assert by_day[1]["visibility"] == "RESTRICTED", by_day[1]["visibility"]
            assert by_day[5]["visibility"] == "VISIBLE", by_day[5]["visibility"]
            assert by_day[9]["visibility"] == "PUBLIC", by_day[9]["visibility"]
            assert by_day[9]["time_ref"] == "07:30"
            assert by_day[1]["time_ref"] is None, "自动同步行不带 time_ref"
            assert by_day[5]["description"] == "客栈夜斗", "自动同步行应带事件描述"
            # 可空列：键恒在、值为 null（不是省略）
            for day in (1, 9):
                for col in ("description", "who_knows"):
                    assert col in by_day[day], f"day={day} 的 {col} 键缺席"
                    assert by_day[day][col] is None, f"day={day} 的 {col} 应为 null"

    asyncio.run(run())


def test_relationships_list_row_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_relationships(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/relationships")).json()
            assert len(rows) == 2, f"夹具没造出 2 行（实际 {len(rows)}）"
            for i, row in enumerate(rows):
                _assert_keys(row, EXPECTED_RELATIONSHIP_KEYS, f"GET relationships row[{i}]")
                assert "relationship_id" not in row, "响应主键键名是 id"
            by_type = {r["relation_type"]: r for r in rows}
            ally = by_type["ally"]
            assert ally["state"] == {"intensity": 0.7}, ally["state"]
            assert ally["last_state_version"] == 3
            assert ally["from_character_id"] == "char_from" and ally["to_character_id"] == "char_to"
            enemy = by_type["enemy"]
            assert enemy["state"] == {}, "空 state_json 解码成 {}，不是 null"
            assert enemy["last_state_version"] == 1
            # 0022 之后端点可以不是 character（faction 端点）⇒ 模型不能按角色 id 校验
            assert enemy["from_character_id"] == "fac_from"
            # ⚠️ 2026-10-16：下面两条是**值**断言，不是键集断言。键集断言只证明「键在」，
            # 证不了「键里装的是 DB 的值」——服务层若写成 `visibility="PUBLIC"` 硬编码，
            # 键集照样 9 个、照样全绿，而这正是本缺陷的同款形状（键在、内容是死的）。
            # 夹具刻意造「一行 PUBLIC/None + 一行 RESTRICTED/["char_z"]」，
            # 这样硬编码任何一侧的值都会被另一侧抓到。
            assert ally["visibility"] == "PUBLIC", ally["visibility"]
            assert ally["who_knows"] is None, f"who_knows=NULL 应透出 null，实得 {ally['who_knows']!r}"
            assert enemy["visibility"] == "RESTRICTED", (
                f"visibility 必须透出 DB 的值而不是硬编码：{enemy['visibility']!r}"
            )
            assert enemy["who_knows"] == ["char_z"], (
                f"who_knows 必须透出 DB 解码后的值：{enemy['who_knows']!r}"
            )

    asyncio.run(run())


def test_relationships_list_covers_every_column(tmp_path: Path):
    """⚠️ **列集不得再次静默缩小**（原 ``..._drops_visibility_and_who_knows_columns_today``，
    2026-10-16 反转断言方向）。

    这条告警的**问题从来不是它当时是红的，而是它在**。原先它断言「DB 9 列 − 2 个改名
    − 响应 7 键 == {visibility, who_knows}」，把服务层丢弃两列这件事写成会红的断言；
    ``_row_to_relationship`` 补上映射后它红了，提醒把两个字段加进 ``RelationshipOut``。
    现在**同一份告警反转成新事实**：仍然「无缺席列」，但缺席集合必须为空。

    基准取 ``PRAGMA table_info``（模型之外的独立事实源）**而不抄数字**——抄 9 这个数
    本身就会随迁移漂移，而漂移的方向恰恰是这条用例要抓的。

    ⚠️ 「9 列 ≡ 9 键」是**键数相等**，不是列名逐字相等：``relationship_id`` → ``id``、
    ``state_json`` → ``state``（已解码）两处改名/解码**没有**消失，故映射关系是
    「列 − 改名 = 直呼键」。
    """
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_relationships(app, ids)

    cols = _table_columns(app, "relationships")
    # 两列是**改名/解码**（relationship_id→id、state_json→state），其余应当直呼透出
    renamed = {"relationship_id": "id", "state_json": "state"}
    assert cols - set(renamed) - EXPECTED_RELATIONSHIP_KEYS == set(), (
        f"有列既没被改名/解码、也不在期望键集里——新列被静默丢弃了："
        f"{sorted(cols - set(renamed) - EXPECTED_RELATIONSHIP_KEYS)}"
    )
    assert EXPECTED_RELATIONSHIP_KEYS - cols == set(renamed.values()), (
        f"期望键集里有不属于本表列的键（改名后的键除外）："
        f"{sorted(EXPECTED_RELATIONSHIP_KEYS - cols - set(renamed.values()))}"
    )

    async def run() -> set[str]:
        async with _client(app) as c:
            rows = (await c.get(f"/api/projects/{ids['pid']}/relationships")).json()
            assert len(rows) == 2, rows
            return {k for r in rows for k in r}

    actual = asyncio.run(run())
    assert actual == EXPECTED_RELATIONSHIP_KEYS, (
        f"响应键集漂移：多={sorted(actual - EXPECTED_RELATIONSHIP_KEYS)} "
        f"少={sorted(EXPECTED_RELATIONSHIP_KEYS - actual)}"
    )


# ===========================================================================
# 3) 列表端点专属的两条防退化
# ===========================================================================


def test_list_nullable_columns_are_present_as_null_not_omitted(tmp_path: Path):
    """⚠️ 防「谁给某个列表端点加了 ``response_model_exclude_none=True``」。

    这五类实体的可空列实测是**键恒在、值为 null**；加了那个开关，FastAPI 会把每个
    ``None`` 抹掉，列表响应凭空少 1~6 个键。
    """
    app = _create_app(tmp_path)
    ids = _seeded(app)
    _seed_ledger(app, ids)
    _seed_plot(app, ids)

    async def run() -> None:
        async with _client(app) as c:
            pid = ids["pid"]
            # ⚠️ **按业务键取行，不按下标**：`list_events` 是 ``ORDER BY event_id``，
            # 而 event_id 带随机十六进制 ⇒ 「哪一行排在第 0 位」每次运行都不同。
            # 早期版本按 rows[0] 取「全空行」，单跑绿、换一次 id 序就红——量具在测
            # 「排序」而不是「键是否存在」。
            hooks = (await c.get(f"/api/projects/{pid}/hooks")).json()
            assert len(hooks) == 2
            empty_hook = {r["name"]: r for r in hooks}["三年之约"]
            for col in (
                "introduced_chapter_id",
                "expected_payoff_chapter_id",
                "payoff_chapter_id",
                "who_knows",
            ):
                assert col in empty_hook, f"hook 的 {col} 键被 exclude_none 抹掉了"
                assert empty_hook[col] is None, f"{col} 应为 null，实际 {empty_hook[col]!r}"

            debts = (await c.get(f"/api/projects/{pid}/debts")).json()
            assert len(debts) == 2
            empty_debt = {r["description"]: r for r in debts}["欠下的交代"]
            for col in ("created_chapter_id", "deadline_chapter_id", "who_knows"):
                assert col in empty_debt, f"debt 的 {col} 键被 exclude_none 抹掉了"
                assert empty_debt[col] is None

            evs = (await c.get(f"/api/projects/{pid}/events")).json()
            assert len(evs) == 2
            empty_ev = {r["type"]: r for r in evs}["revelation"]
            for col in ("location_id", "introduced_chapter_id", "who_knows", "description"):
                assert col in empty_ev, f"event 的 {col} 键被 exclude_none 抹掉了"
                assert empty_ev[col] is None

            tls = (await c.get(f"/api/projects/{pid}/timeline")).json()
            assert len(tls) == 3
            synced = {r["day_index"]: r for r in tls}[1]  # 自动同步行
            for col in ("time_ref", "description", "who_knows"):
                assert col in synced, f"timeline 自动同步行的 {col} 键被 exclude_none 抹掉了"
                assert synced[col] is None

    asyncio.run(run())


def test_empty_project_returns_empty_list_not_error(tmp_path: Path):
    """空项目必须仍是 200 + ``[]``（挂 ``response_model`` 不许把空集变成 422/500）。"""
    app = _create_app(tmp_path)
    ids = _seeded(app)  # 只建项目 + 章节，不造任何 hook/debt/event/relationship

    async def run() -> None:
        async with _client(app) as c:
            for path in ("hooks", "debts", "events", "timeline", "relationships"):
                r = await c.get(f"/api/projects/{ids['pid']}/{path}")
                assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
                assert r.json() == [], f"{path} 空项目应返回 []，实际 {r.json()!r}"

    asyncio.run(run())


# ===========================================================================
# 4) 基准的第三方核对：DB 实际列集（模型之外的独立事实源）
# ===========================================================================


def test_ledger_list_keys_match_table_columns(tmp_path: Path):
    """ledger 读路径是 ``SELECT *`` ⇒ 响应键集 **就是**列集（可执行的第三方基准）。

    这条同时是**漂移告警**：将来某个迁移给 ``hooks`` 加一列，本用例会红，提示契约要更新。
    **该红的时候就红**——这正是它存在的意义。
    """
    app = _create_app(tmp_path)
    assert _table_columns(app, "hooks") == EXPECTED_HOOK_KEYS, "hooks 列集与契约漂移"
    assert _table_columns(app, "narrative_debts") == EXPECTED_DEBT_KEYS, (
        "narrative_debts 列集与契约漂移"
    )


def test_plot_list_keys_are_not_table_columns(tmp_path: Path):
    """⚠️ plot **相反**：响应键是 ``to_dict()`` 的镜像，**≠** 列集。

    照 DDL 建模会写出四个永不命中的字段（``cause_json`` / ``effects_json`` /
    ``participants_json`` / ``time_json``）外加错的主键名，并让真正的键静默缺席。
    本用例把这条「不是列集」钉成可执行事实——它是本组三个模型复用判断的依据。
    """
    app = _create_app(tmp_path)
    event_cols = _table_columns(app, "plot_events")
    assert event_cols - EXPECTED_PLOT_EVENT_KEYS == {
        "cause_json",
        "effects_json",
        "participants_json",
        "time_json",
        "event_id",
    }, f"plot_events 列集漂移：{sorted(event_cols)}"
    assert EXPECTED_PLOT_EVENT_KEYS - event_cols == {"id", "cause", "effects", "participants", "time"}

    tl_cols = _table_columns(app, "timeline_events")
    assert tl_cols - EXPECTED_TIMELINE_KEYS == {"timeline_event_id"}, sorted(tl_cols)
    assert EXPECTED_TIMELINE_KEYS - tl_cols == {"id"}


# ===========================================================================
# 5) 模型侧：字段集 vs 硬编码（**唯一**允许出现 model_fields 的地方）
# ===========================================================================


def test_model_fields_match_hardcoded_expectation():
    """模型字段集与硬编码期望一致——与响应断言构成两道独立闸门。

    ⚠️ 本文件里 ``model_fields`` **只允许出现在这一条用例里**。出现在「响应 vs 模型」
    的比对里就是自指断言（两侧同时变小 ⇒ 恒真）。
    """
    assert set(Hook.model_fields) == EXPECTED_HOOK_KEYS
    assert set(Debt.model_fields) == EXPECTED_DEBT_KEYS
    assert set(PlotEventOut.model_fields) == EXPECTED_PLOT_EVENT_KEYS
    assert set(TimelineEventOut.model_fields) == EXPECTED_TIMELINE_KEYS
    assert set(RelationshipOut.model_fields) == EXPECTED_RELATIONSHIP_KEYS


def test_relationship_out_permits_unknown_keys():
    """``extra="allow"``：``to_dict()`` 是手写白名单，将来加了键而忘了声明时，
    ``extra="ignore"`` 会让 ``response_model`` 把它静默裁掉——那正是本批次要消灭的缺口。
    """
    row = {k: None for k in EXPECTED_RELATIONSHIP_KEYS} | {
        "id": "rel_1",
        "project_id": "prj_1",
        "from_character_id": "a",
        "to_character_id": "b",
        "relation_type": "ally",
        "state": {},
        "last_state_version": 0,
        # ⚠️ 2026-10-16：``visibility`` 是裸 ``str``（不给默认值、不给 Optional），
        # 「全部置 None 再逐个覆盖」的建法对它就是 ValidationError ⇒ 显式给个合法值。
        # 这条恰恰是「可空列必填可空、非空列必给值」这条建模纪律的体现。
        "visibility": "PUBLIC",
        "brand_new_key": "v",
    }
    assert RelationshipOut(**row).model_dump()["brand_new_key"] == "v", "extra 被裁掉了"
