"""ledger（钩子/债）与 plot（事件/时间线）的 response_model 契约测试。

背景
----
这 10 个端点此前返回裸 ``dict``、router 无 ``response_model`` ⇒ OpenAPI 里没有它们的
schema ⇒ 前端只能在 ``apps/web/src/api/types.ts`` 手写契约而无任何机器校验；
后端加一个键、删一个键，要等到运行时才发现。

字段形状怎么来的
----------------
**不是照源码读出来的，是量出来的**。本仓的坑区已登记两次同族事故：
``response_model`` 会**过滤**未声明字段（少声明一个 ⇒ 响应静默少一个，而功能测试常常
照旧全绿），以及「夹具从被测代码的实际输出里抄形状 ⇒ 缺陷被写进断言，绿灯把缺陷锁死」。

所以本组的期望键集来自**独立于实现**的三方基准：

1. **DB 的 DDL**（``database/migrations/0001_init.sql`` 的 ``hooks`` /
   ``narrative_debts``；读路径是 ``SELECT *`` ⇒ 键集 ≡ 列集）；
2. **临时库 + 合成夹具跑通端点后 dump 的实际响应**（建法：``Settings(data_dir=tmp)``
   + ``app.router.lifespan_context`` + ``httpx.ASGITransport``，全程无真实 LLM 调用）；
3. 三方一致才写进下面的 ``EXPECTED_*`` 常量。若日后三者分叉，**先复算 DDL**，别改常量。

实测结论（2026-10-15）：

- ``hooks`` 12 键 / ``narrative_debts`` 11 键 / ``plot_events`` 响应 13 键 /
  ``timeline_events`` 响应 8 键，**全部恒在**（空项目时键依然在，值为 ``null``）；
- ⚠️ plot 的响应键**不是表列名**：DB 是 ``event_id`` / ``cause_json`` / ``time_json``，
  响应是 ``id`` / ``cause`` / ``time``（``to_dict`` 解码并改名）。照 DDL 建模会写出
  四个永不命中的字段，并让真正的键静默缺席——本组用 ``test_plot_event_*`` 钉住；
- 四个模型都 ``extra="allow"``（读路径可能随迁移加列；``extra="ignore"`` 会让
  ``response_model`` 静默裁掉新列）。**代价**：``extra`` 会掩盖「删掉字段声明」这件事
  （响应照样有那个键），故本组另断言 OpenAPI schema 的 ``properties`` / ``required``。

判别
----
每条突变各自对应一条会红的用例（见 ``tests/mutation_specs/g2_ledger_plot.py``）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.domain.ledger import Debt, Hook
from packages.domain.plot.models import PlotEventOut, TimelineEventOut


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


# ===========================================================================
# 期望键集 —— 硬编码的第三方基准
#
# ⚠️ 刻意**不**写成 `set(Hook.model_fields)`。首版就是自指的（拿响应和模型比），
# 「模型少声明一个字段」时两侧同时变小 ⇒ 仍然「逐字相等」⇒ 断言恒真。
# 突变验证 M14 实测存活过这个形状（见 AGENTS 坑区「断言了结果 ≠ 判别了机制」）。
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
    "description",  # 迁移 0013 的列；曾因「模型里没有」对 API 隐形（修复 B1）
}
EXPECTED_TIMELINE_EVENT_KEYS = {
    "id",  # ⚠️ 不是 timeline_event_id
    "project_id",
    "event_id",
    "day_index",
    "time_ref",
    "description",
    "visibility",
    "who_knows",
}

# 10 个端点 → （path, method, 成功状态码, 期望 schema 名）
_SURGICAL_ENDPOINTS = [
    ("/api/projects/{pid}/hooks", "post", "201", "Hook"),
    ("/api/hooks/{hook_id}", "get", "200", "Hook"),
    ("/api/hooks/{hook_id}", "patch", "200", "Hook"),
    ("/api/projects/{pid}/debts", "post", "201", "Debt"),
    ("/api/debts/{debt_id}", "get", "200", "Debt"),
    ("/api/debts/{debt_id}", "patch", "200", "Debt"),
    ("/api/projects/{pid}/events", "post", "201", "PlotEventOut"),
    ("/api/events/{event_id}", "get", "200", "PlotEventOut"),
    ("/api/events/{event_id}", "patch", "200", "PlotEventOut"),
    ("/api/projects/{pid}/timeline", "post", "201", "TimelineEventOut"),
]


def _seeded(app) -> tuple[str, str]:
    """建项目 + 章节，返回 ``(project_id, chapter_id)``。全程 mock/无 LLM。"""

    async def run() -> tuple[str, str]:
        async with _client(app) as c:
            pid = (await c.post("/api/projects", json={"name": "G2 契约"})).json()["project_id"]
            cid = (
                await c.post(f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"})
            ).json()["chapter_id"]
            return pid, cid

    return asyncio.run(run())


def _assert_keys(body: dict, expected: set[str], label: str) -> None:
    assert isinstance(body, dict), f"{label} 应是对象，实际 {type(body)}"
    assert set(body) == expected, (
        f"{label} 键集不符：多={sorted(set(body) - expected)} 少={sorted(expected - set(body))}"
    )


# ===========================================================================
# 1) OpenAPI：schema 存在 + 被端点引用
# ===========================================================================


def test_surgical_schemas_declared_and_referenced(tmp_path: Path):
    app = _create_app(tmp_path)
    schema = app.openapi()
    for name in ("Hook", "Debt", "PlotEventOut", "TimelineEventOut"):
        assert name in schema["components"]["schemas"], f"{name} 未进 OpenAPI"
    for path, method, code, name in _SURGICAL_ENDPOINTS:
        ref = schema["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ].get("$ref")
        assert ref is not None and ref.endswith(f"/{name}"), (
            f"{method.upper()} {path} 的 {code} 响应未引用 {name}（实际 {ref!r}）"
        )


def test_openapi_schema_properties_and_required_match_expectation(tmp_path: Path):
    """⚠️ 模型带 ``extra="allow"`` 时**必须**有这一条。

    ``extra="allow"`` 会掩盖「删掉一个字段声明」：响应照样透出那个键（作为 extra），
    上一条 ``set(body) == EXPECTED`` 仍然绿 ⇒ 只有 schema 的 ``properties`` /
    ``required`` 会少。突变 M20 就是靠这条抓到的。

    基准是硬编码常量，**不是** ``model_fields``——否则两侧同减又恒真。
    """
    schema = _create_app(tmp_path).openapi()
    for name, expected in (
        ("Hook", EXPECTED_HOOK_KEYS),
        ("Debt", EXPECTED_DEBT_KEYS),
        ("PlotEventOut", EXPECTED_PLOT_EVENT_KEYS),
        ("TimelineEventOut", EXPECTED_TIMELINE_EVENT_KEYS),
    ):
        sc = schema["components"]["schemas"][name]
        assert set(sc["properties"]) == expected, (
            f"{name} 的 schema.properties 不符："
            f"多={sorted(set(sc['properties']) - expected)} 少={sorted(expected - set(sc['properties']))}"
        )
        # 实测：可空列也是必填（声明成 X | None 且**不给默认值**）⇒ required ⊇ properties
        assert set(sc["required"]) == expected, (
            f"{name} 的 required 不符：多={sorted(set(sc['required']) - expected)} "
            f"少={sorted(expected - set(sc['required']))}。"
            f"⚠️ 若可空列被给了默认值，schema 会把它变成可选，前端得写 ?? 兜底（G10 病）"
        )


# ===========================================================================
# 2) 实际响应键集 vs 硬编码期望（响应侧漂移也红）
# ===========================================================================


def test_hook_endpoints_keys_match_hardcoded_expectation(tmp_path: Path):
    """POST / GET / PATCH 三条路径都必须逐字相等。"""
    app = _create_app(tmp_path)
    pid, _cid = _seeded(app)

    async def run():
        async with _client(app) as c:
            created = await c.post(f"/api/projects/{pid}/hooks", json={"name": "三年之约"})
            assert created.status_code == 201, created.text
            body = created.json()
            _assert_keys(body, EXPECTED_HOOK_KEYS, "POST /projects/{pid}/hooks")
            hid = body["hook_id"]

            fetched = await c.get(f"/api/hooks/{hid}")
            assert fetched.status_code == 200, fetched.text
            _assert_keys(fetched.json(), EXPECTED_HOOK_KEYS, "GET /hooks/{hook_id}")

            # 顺带钉住几个字段的取值来源，防止「键在但恒为空」的形状漂移
            assert body["status"] == "OPEN", body["status"]
            assert body["importance"] == 0.5, body["importance"]
            assert body["visibility"] == "RESTRICTED", body["visibility"]
            assert body["who_knows"] is None, "新建时 who_knows 应为 null（不是 []）"

            patched = await c.patch(
                f"/api/hooks/{hid}", json={"status": "ACTIVE", "who_knows": ["char_x"]}
            )
            assert patched.status_code == 200, patched.text
            pbody = patched.json()
            _assert_keys(pbody, EXPECTED_HOOK_KEYS, "PATCH /hooks/{hook_id}")
            assert pbody["status"] == "ACTIVE"
            assert pbody["who_knows"] == ["char_x"]

            # 空值列：键必须在，值为 None（见 test_nullable_keys_are_present_as_null）
            assert pbody["payoff_chapter_id"] is None

    asyncio.run(run())


def test_debt_endpoints_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    pid, _cid = _seeded(app)

    async def run():
        async with _client(app) as c:
            created = await c.post(f"/api/projects/{pid}/debts", json={"description": "欠下的交代"})
            assert created.status_code == 201, created.text
            body = created.json()
            _assert_keys(body, EXPECTED_DEBT_KEYS, "POST /projects/{pid}/debts")
            did = body["debt_id"]
            assert body["status"] == "open", body["status"]
            assert body["severity"] == 0.5, body["severity"]
            assert body["who_knows"] is None

            fetched = await c.get(f"/api/debts/{did}")
            assert fetched.status_code == 200, fetched.text
            _assert_keys(fetched.json(), EXPECTED_DEBT_KEYS, "GET /debts/{debt_id}")

            patched = await c.patch(
                f"/api/debts/{did}", json={"status": "acknowledged", "who_knows": []}
            )
            assert patched.status_code == 200, patched.text
            pbody = patched.json()
            _assert_keys(pbody, EXPECTED_DEBT_KEYS, "PATCH /debts/{debt_id}")
            assert pbody["status"] == "acknowledged"
            # 三态语义：'[]' 显式置空 ⇒ 空列表而非 null（对齐 knowledge-permission §3.1）
            assert pbody["who_knows"] == [], pbody["who_knows"]
            assert pbody["deadline_chapter_id"] is None

    asyncio.run(run())


def test_plot_event_endpoints_keys_match_hardcoded_expectation(tmp_path: Path):
    """事件：响应键名 ≠ 表列名（``id``/``cause``/``time``），本用例正是那道闸门。"""
    app = _create_app(tmp_path)
    pid, cid = _seeded(app)

    async def run():
        async with _client(app) as c:
            created = await c.post(
                f"/api/projects/{pid}/events",
                json={"type": "conflict", "description": "客栈夜斗", "introduced_chapter_id": cid},
            )
            assert created.status_code == 201, created.text
            body = created.json()
            _assert_keys(body, EXPECTED_PLOT_EVENT_KEYS, "POST /projects/{pid}/events")
            eid = body["id"]

            # ⚠️ 这三条是最容易照 DDL 写错的地方，单独点名（失败信息更直接）
            assert "event_id" not in body, "响应主键键名是 id，不是 event_id"
            assert "cause_json" not in body and "time_json" not in body, (
                "cause/effects/participants/time 已是解码后的键，_json 后缀不该出现在响应里"
            )
            assert body["time"] == {"timeline_day": 1, "in_story_date": None}, body["time"]
            assert body["cause"] == [] and body["effects"] == [] and body["participants"] == []
            assert body["status"] == "planned"
            assert body["visibility"] == "RESTRICTED"
            # 迁移 0013 的 description 曾对 API 隐形（修复 B1）——钉住它不再隐形
            assert body["description"] == "客栈夜斗", body["description"]

            fetched = await c.get(f"/api/events/{eid}")
            assert fetched.status_code == 200, fetched.text
            _assert_keys(fetched.json(), EXPECTED_PLOT_EVENT_KEYS, "GET /events/{event_id}")

            patched = await c.patch(
                f"/api/events/{eid}", json={"status": "resolved", "who_knows": ["char_y"]}
            )
            assert patched.status_code == 200, patched.text
            pbody = patched.json()
            _assert_keys(pbody, EXPECTED_PLOT_EVENT_KEYS, "PATCH /events/{event_id}")
            assert pbody["status"] == "resolved"
            assert pbody["who_knows"] == ["char_y"]
            # PATCH 不得把没提到的列弄丢
            assert pbody["description"] == "客栈夜斗", "PATCH 丢了 description"
            assert pbody["introduced_chapter_id"] == cid

    asyncio.run(run())


def test_timeline_endpoint_keys_match_hardcoded_expectation(tmp_path: Path):
    app = _create_app(tmp_path)
    pid, cid = _seeded(app)

    async def run():
        async with _client(app) as c:
            ev = (
                await c.post(f"/api/projects/{pid}/events", json={"type": "revelation"})
            ).json()
            created = await c.post(
                f"/api/projects/{pid}/timeline",
                json={"event_id": ev["id"], "day_index": 3, "time_ref": "07:30"},
            )
            assert created.status_code == 201, created.text
            body = created.json()
            _assert_keys(body, EXPECTED_TIMELINE_EVENT_KEYS, "POST /projects/{pid}/timeline")
            assert "timeline_event_id" not in body, "响应主键键名是 id"
            assert body["day_index"] == 3
            assert body["time_ref"] == "07:30"
            assert body["description"] is None
            # ⚠️ timeline 的 visibility 默认是 PUBLIC（迁移 0014），与 plot_events 的
            # RESTRICTED 不同。写混了会直接改变剧透可见性。
            assert body["visibility"] == "PUBLIC", body["visibility"]
            assert body["who_knows"] is None

            # 建事件时服务会自动同步一条 timeline 行（day_index=1）；列表端点本批未建模，
            # 但它证明「自动同步」这条路径存在，且形状与 POST 一致。
            rows = (await c.get(f"/api/projects/{pid}/timeline")).json()
            assert rows, "建事件应自动产生一条时间线行"
            for row in rows:
                _assert_keys(row, EXPECTED_TIMELINE_EVENT_KEYS, "GET /projects/{pid}/timeline 行")
            assert any(r["description"] is None and r["day_index"] == 3 for r in rows)

    asyncio.run(run())


# ===========================================================================
# 3) 模型侧：字段集 vs 硬编码（**唯一**允许出现 model_fields 的地方）
# ===========================================================================


def test_model_fields_match_hardcoded_expectation():
    """模型字段集与硬编码期望一致——与响应断言构成两道独立闸门。

    ⚠️ 本文件里 ``model_fields`` **只允许出现在这一条用例里**。出现在「响应 vs 模型」
    的比对里就是自指断言（两侧同时变小 ⇒ 恒真）。
    """
    assert set(Hook.model_fields) == EXPECTED_HOOK_KEYS
    assert set(Debt.model_fields) == EXPECTED_DEBT_KEYS
    assert set(PlotEventOut.model_fields) == EXPECTED_PLOT_EVENT_KEYS
    assert set(TimelineEventOut.model_fields) == EXPECTED_TIMELINE_EVENT_KEYS


def test_models_permit_unknown_keys():
    """``extra="allow"``：读路径是 ``SELECT *`` / ``to_dict`` 白名单，将来加键时
    不得被 ``response_model`` 静默裁掉（那正是本批次要消灭的缺口）。

    直接对模型断言即可（路由层走同一套序列化）——去掉 ``ConfigDict(extra="allow")``
    必红。
    """
    full_hook = {k: None for k in EXPECTED_HOOK_KEYS} | {
        "hook_id": "h1",
        "project_id": "prj_1",
        "created_at": "2026-10-15T00:00:00+00:00",
        "updated_at": "2026-10-15T00:00:00+00:00",
        "status": "OPEN",
        "importance": 0.5,
        "name": "n",
        "visibility": "RESTRICTED",
        "brand_new_column": "从迁移来的新列",
    }
    assert Hook(**full_hook).model_dump()["brand_new_column"] == "从迁移来的新列", (
        "Hook 的 extra 被裁掉了"
    )

    full_debt = {k: None for k in EXPECTED_DEBT_KEYS} | {
        "debt_id": "d1",
        "project_id": "prj_1",
        "created_at": "2026-10-15T00:00:00+00:00",
        "updated_at": "2026-10-15T00:00:00+00:00",
        "status": "open",
        "severity": 0.5,
        "description": "d",
        "visibility": "RESTRICTED",
        "brand_new_column": 1,
    }
    assert Debt(**full_debt).model_dump()["brand_new_column"] == 1, "Debt 的 extra 被裁掉了"

    full_ev = {k: None for k in EXPECTED_PLOT_EVENT_KEYS} | {
        "id": "e1",
        "project_id": "prj_1",
        "type": "other",
        "cause": [],
        "effects": [],
        "participants": [],
        "time": {"timeline_day": 1},
        "status": "planned",
        "visibility": "RESTRICTED",
        "brand_new_key": [1],
    }
    assert PlotEventOut(**full_ev).model_dump()["brand_new_key"] == [1], (
        "PlotEventOut 的 extra 被裁掉了"
    )

    full_tl = {k: None for k in EXPECTED_TIMELINE_EVENT_KEYS} | {
        "id": "t1",
        "project_id": "prj_1",
        "event_id": "e1",
        "day_index": 1,
        "visibility": "PUBLIC",
        "brand_new_key": "v",
    }
    assert TimelineEventOut(**full_tl).model_dump()["brand_new_key"] == "v", (
        "TimelineEventOut 的 extra 被裁掉了"
    )


def test_nullable_columns_are_present_as_null_not_omitted(tmp_path: Path):
    """⚠️ 防「谁给端点加了 ``response_model_exclude_none=True``」。

    这四个实体的可空列实测是**键恒在、值为 null**；加了那个开关，FastAPI 会把每个
    ``None`` 抹掉，响应凭空少 4~6 个键 ⇒ 本用例必红。
    （与 ``simulation`` 的 ``diff`` 方向相反：那边要「空则省略」，这里要「空也在」。）
    """
    app = _create_app(tmp_path)
    pid, _cid = _seeded(app)

    async def run():
        async with _client(app) as c:
            hook = (await c.post(f"/api/projects/{pid}/hooks", json={"name": "空值钩子"})).json()
            for col in (
                "introduced_chapter_id",
                "expected_payoff_chapter_id",
                "payoff_chapter_id",
                "who_knows",
            ):
                assert col in hook, f"hook 的 {col} 键被 exclude_none 抹掉了"
                assert hook[col] is None, f"{col} 应为 null，实际 {hook[col]!r}"

            debt = (await c.post(f"/api/projects/{pid}/debts", json={"description": "空值债"})).json()
            for col in ("created_chapter_id", "deadline_chapter_id", "who_knows"):
                assert col in debt, f"debt 的 {col} 键被 exclude_none 抹掉了"
                assert debt[col] is None

            ev = (await c.post(f"/api/projects/{pid}/events", json={"type": "other"})).json()
            for col in ("location_id", "introduced_chapter_id", "who_knows", "description"):
                assert col in ev, f"event 的 {col} 键被 exclude_none 抹掉了"
                assert ev[col] is None

            tl = (
                await c.post(
                    f"/api/projects/{pid}/timeline",
                    json={"event_id": ev["id"], "day_index": 1},
                )
            ).json()
            for col in ("time_ref", "description", "who_knows"):
                assert col in tl, f"timeline 的 {col} 键被 exclude_none 抹掉了"
                assert tl[col] is None

    asyncio.run(run())


def test_ledger_expected_keys_match_ddl_columns(tmp_path: Path):
    """**可执行的**第三方基准：ledger 两张表的期望键集 ≡ DB 实际列集。

    为什么这条不能省
    ----------------
    ``LedgerService`` 读路径是 ``SELECT *`` ⇒ 响应键集**就是**列集。上面那些响应断言
    的基准若是从「跑一下看返回什么」抄来的，就等于把缺陷写进断言（AGENTS 坑区：
    「夹具从被测代码的实际输出里抄形状 ⇒ 绿灯把缺陷锁死」）。这条直接查
    ``PRAGMA table_info``，是模型之外的独立事实源。

    它的另一半价值是**漂移告警**：将来某个迁移给 ``hooks`` 加一列，本用例会红，
    提示「契约要更新」；而若模型没跟上、``extra`` 也没兜住，响应就会多一个前端不知道
    的键。**该红的时候就红**——这正是它存在的意义。

    ⚠️ plot 不适用这条：它的响应键是 ``to_dict()`` 的**解码后**形态（``cause_json``
    → ``cause``、``event_id`` → ``id``），与列名逐字不同；plot 的基准是上面那组
    「DDL + 实测」的双向核对，已在 docstring 记明。
    """
    from packages.core.db import get_connection

    app = _create_app(tmp_path)

    async def run() -> tuple[set[str], set[str]]:
        # ⚠️ 必须进 lifespan：迁移是在**启动时**跑的。不进则 DB 还是空文件，
        # ``PRAGMA table_info`` 返回空集 ⇒ 断言变成「期望集 == 空集」的假红。
        async with app.router.lifespan_context(app):
            conn = get_connection(app.state.settings.db_path)
            try:
                return (
                    {r["name"] for r in conn.execute("PRAGMA table_info(hooks)").fetchall()},
                    {
                        r["name"]
                        for r in conn.execute("PRAGMA table_info(narrative_debts)").fetchall()
                    },
                )
            finally:
                conn.close()

    hook_cols, debt_cols = asyncio.run(run())

    assert hook_cols == EXPECTED_HOOK_KEYS, (
        f"hooks 列集与契约漂移：多={sorted(hook_cols - EXPECTED_HOOK_KEYS)} "
        f"少={sorted(EXPECTED_HOOK_KEYS - hook_cols)}"
    )
    assert debt_cols == EXPECTED_DEBT_KEYS, (
        f"narrative_debts 列集与契约漂移：多={sorted(debt_cols - EXPECTED_DEBT_KEYS)} "
        f"少={sorted(EXPECTED_DEBT_KEYS - debt_cols)}"
    )


def test_missing_entity_still_returns_404(tmp_path: Path):
    """挂 ``response_model`` 不许把 404 路径搞坏（``response_model`` 只作用于成功体）。"""
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            assert (await c.get("/api/hooks/hook_nope")).status_code == 404
            assert (await c.get("/api/debts/debt_nope")).status_code == 404
            assert (await c.get("/api/events/event_nope")).status_code == 404
            assert (await c.patch("/api/hooks/hook_nope", json={"name": "x"})).status_code == 404
            assert (await c.patch("/api/debts/debt_nope", json={"description": "x"})).status_code == 404

    asyncio.run(run())
