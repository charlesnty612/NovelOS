"""世界三实体（locations / factions / world_rules）的 response_model 契约测试。

2026-10-15 批次（六）· G1。方法同批次（五）：**先量再建**——临时库 + 合成夹具跑通
端点、把实际返回的键集 dump 出来，再照**量出来的形状**建模。实测结论写在这里，
它们是本组断言的全部依据（不是照 ``WorldEntity.to_dict()`` 抄的）：

- 9 个端点（POST / GET / PATCH × 三类实体）+ 3 个列表端点，返回的键集
  **逐字相同**，恰好 11 个键，**一个都不省略**：

  ``id / project_id / name / statement / data / visibility / who_knows /
  aliases / inject_mode / created_at / updated_at``

- ``who_knows`` 无数据时是 **null 而非省略** ⇒ 模型里必须必填可空且**不给默认值**。
  这条同时意味着**端点上不能挂 ``response_model_exclude_none=True``**：挂上去
  ``who_knows`` 会被整个吃掉，响应凭空少一个键（突变 G1-7 守的就是它）。
- ``statement`` / ``data`` / ``aliases`` **恒非 null**（分别至少 ``""`` / ``{}`` /
  ``[]``），别按可空建模。
- ``data`` 实测两种形态都出现（默认 ``{}``，写入数组时是 ``[1, "x"]``）
  ⇒ 声明为 ``dict[str, Any] | list[Any]``。
- ``visibility`` 四档 ``PUBLIC / VISIBLE / RESTRICTED / HIDDEN`` 全部实测过，
  第五个值被 service 挡在 422 ⇒ 可以收窄成 Literal。
- ⚠️ **world_rules 的 ``aliases`` / ``inject_mode`` 恒为 ``[]`` / ``"auto"``**：
  POST 与 PATCH 带上这两个键都会被**静默吞掉**（service 对 world_rules 不写这两列）。
  这是**既有行为不是本次引入**，见
  ``test_world_rule_swallows_aliases_and_inject_mode``。

判别：撤掉任一 ``response_model`` / 任一字段声明 / 挂上 ``exclude_none``，
本组用例必红。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.domain.world.models import (
    INJECT_MODES,
    VISIBILITY_VALUES,
    WorldEntity,
    WorldEntityOut,
    WorldInjectModeLiteral,
    WorldVisibilityLiteral,
)

# ---------------------------------------------------------------------------
# 硬编码基准（模型之外的第三方依据）
# ---------------------------------------------------------------------------
# ❌ 禁写 `assert set(body) == set(WorldEntityOut.model_fields)`：
#    那是**自指断言**——把字段从模型里删掉，响应与期望**同时**变小，仍然「相等」，
#    断言恒真（坑区已登记，突变 M14 实测存活过这个形状）。
#    下面所有比对都以本段常量为基准；`model_fields` 只出现在
#    `test_model_matches_hardcoded_expectation` 等「模型 vs 硬编码」用例里，
#    一处都不出现在「响应 vs 模型」的比对里。
#    静态自查：`grep -n "model_fields" tests/api/test_world_response_contract.py`

EXPECTED_ENTITY_KEYS = {
    "id",
    "project_id",
    "name",
    "statement",
    "data",
    "visibility",
    "who_knows",
    "aliases",
    "inject_mode",
    "created_at",
    "updated_at",
}

# 实测四档全部被接受，第五档被 service 挡在 422（见模块 docstring）
EXPECTED_VISIBILITY_VALUES = ["PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"]
EXPECTED_INJECT_MODES = ["auto", "always", "never"]

# (path, method, 响应状态码) —— 9 个任务端点 + 3 个列表端点
SINGLE_ENDPOINTS = [
    ("/api/projects/{pid}/locations", "post", "201"),
    ("/api/locations/{location_id}", "get", "200"),
    ("/api/locations/{location_id}", "patch", "200"),
    ("/api/projects/{pid}/factions", "post", "201"),
    ("/api/factions/{faction_id}", "get", "200"),
    ("/api/factions/{faction_id}", "patch", "200"),
    ("/api/projects/{pid}/world-rules", "post", "201"),
    ("/api/world-rules/{world_rule_id}", "get", "200"),
    ("/api/world-rules/{world_rule_id}", "patch", "200"),
]
LIST_ENDPOINTS = [
    ("/api/projects/{pid}/locations", "get", "200"),
    ("/api/projects/{pid}/factions", "get", "200"),
    ("/api/projects/{pid}/world-rules", "get", "200"),
]


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _new_pid(c) -> str:
    return (await c.post("/api/projects", json={"name": "世界契约"})).json()["project_id"]


# ---------------------------------------------------------------------------
# 1. OpenAPI 侧：schema 存在 + 12 个端点都引用它
# ---------------------------------------------------------------------------


def test_world_entity_out_schema_declared_and_referenced(tmp_path: Path):
    """OpenAPI 里有 ``WorldEntityOut``，且 9 个单实体 + 3 个列表端点**全部**指向它。

    改动前这 12 个端点的响应 schema 全是匿名
    ``{"type": "object", "additionalProperties": true}``——等于没有契约。
    撤掉任一 ``response_model=`` ⇒ 对应那条断言立刻变成匿名 schema ⇒ 必红。
    """
    app = _create_app(tmp_path)
    schema = app.openapi()
    assert "WorldEntityOut" in schema["components"]["schemas"], "WorldEntityOut 未进 OpenAPI"

    for path, method, code in SINGLE_ENDPOINTS:
        ref = schema["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert ref.endswith("/WorldEntityOut"), f"{method.upper()} {path} -> {ref}"

    for path, method, code in LIST_ENDPOINTS:
        sc = schema["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ]
        assert sc["type"] == "array", sc
        assert sc["items"]["$ref"].endswith("/WorldEntityOut"), sc


def test_openapi_schema_properties_and_required_match_expectation(tmp_path: Path):
    """⚠️ ``extra="allow"`` 的对冲断言：schema 的 ``properties`` / ``required`` 必须齐全。

    为什么需要这条：模型开了 ``extra="allow"``，所以**从模型里删掉一个字段声明，
    响应里那个键照样在**（extra 透出）——只断言响应键集抓不到这种改动。
    必须另断言 OpenAPI 里 schema 的 ``properties`` 与 ``required``，光看响应抓不到。
    """
    s = _create_app(tmp_path).openapi()["components"]["schemas"]["WorldEntityOut"]
    assert set(s["properties"]) == EXPECTED_ENTITY_KEYS, (
        f"多={set(s['properties']) - EXPECTED_ENTITY_KEYS} "
        f"少={EXPECTED_ENTITY_KEYS - set(s['properties'])}"
    )
    # 11 个键**恒在** ⇒ 全部必填（who_knows 可空但不可省略）。
    # 给任何字段加 `= None` 默认值都会把它踢出 required ⇒ 本条必红。
    assert set(s["required"]) == EXPECTED_ENTITY_KEYS, (
        f"required 缺={EXPECTED_ENTITY_KEYS - set(s['required'])} "
        f"多={set(s['required']) - EXPECTED_ENTITY_KEYS}"
    )


def test_openapi_enum_values_match_measured_visibility_and_inject_mode(tmp_path: Path):
    """``visibility`` / ``inject_mode`` 的 enum 与实测值逐字相符。"""
    props = _create_app(tmp_path).openapi()["components"]["schemas"]["WorldEntityOut"][
        "properties"
    ]
    assert props["visibility"]["enum"] == EXPECTED_VISIBILITY_VALUES, props["visibility"]
    assert props["inject_mode"]["enum"] == EXPECTED_INJECT_MODES, props["inject_mode"]


# ---------------------------------------------------------------------------
# 2. 响应侧：9 个端点 + 3 个列表端点的实际键集 == 硬编码期望
# ---------------------------------------------------------------------------


def test_all_world_endpoints_keys_match_hardcoded_expectation(tmp_path: Path):
    """把 9 个任务端点逐个跑一遍，实际返回的键集与**硬编码期望**逐字相等。"""
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            checked: list[str] = []

            for kind, plural, single, id_key in (
                ("location", "locations", "locations", "id"),
                ("faction", "factions", "factions", "id"),
                ("world_rule", "world-rules", "world-rules", "id"),
            ):
                # POST
                r = await c.post(f"/api/projects/{pid}/{plural}", json={"name": f"{kind}-新建"})
                assert r.status_code == 201, f"POST {plural}: {r.text}"
                assert set(r.json()) == EXPECTED_ENTITY_KEYS, (
                    f"POST {plural}: 多={set(r.json()) - EXPECTED_ENTITY_KEYS} "
                    f"少={EXPECTED_ENTITY_KEYS - set(r.json())}"
                )
                eid = r.json()[id_key]
                checked.append(f"POST /{plural}")

                # GET by id
                r = await c.get(f"/api/{single}/{eid}")
                assert r.status_code == 200, f"GET {single}: {r.text}"
                assert set(r.json()) == EXPECTED_ENTITY_KEYS, f"GET {single}"
                checked.append(f"GET /{single}/{{id}}")

                # PATCH
                r = await c.patch(f"/api/{single}/{eid}", json={"statement": "改过了"})
                assert r.status_code == 200, f"PATCH {single}: {r.text}"
                assert set(r.json()) == EXPECTED_ENTITY_KEYS, f"PATCH {single}"
                checked.append(f"PATCH /{single}/{{id}}")

                # LIST
                r = await c.get(f"/api/projects/{pid}/{plural}")
                assert r.status_code == 200, f"LIST {plural}: {r.text}"
                rows = r.json()
                assert len(rows) == 1, f"LIST {plural} 应有 1 行，实际 {len(rows)}"
                assert set(rows[0]) == EXPECTED_ENTITY_KEYS, f"LIST {plural}"
                checked.append(f"LIST /{plural}")

            return checked

    checked = asyncio.run(run())
    assert len(checked) == 12, checked


# ---------------------------------------------------------------------------
# 3. 模型侧：模型字段集 == 硬编码期望（唯一允许出现 model_fields 的位置）
# ---------------------------------------------------------------------------


def test_model_matches_hardcoded_expectation():
    """``WorldEntityOut`` 的字段集与硬编码期望一致（与响应断言构成两道）。"""
    assert set(WorldEntityOut.model_fields) == EXPECTED_ENTITY_KEYS


def test_dataclass_and_pydantic_models_stay_same_shape():
    """两份模型逐字同形（``reveal_policies`` 先例：dataclass + Pydantic 各一份）。

    ⚠️ ``WorldEntity.to_dict()`` 是 service 层的唯一出参来源，Pydantic 模型是
    ``response_model`` 的唯一来源。两边一旦分叉，就会出现「service 写了新键但
    模型收不下（静默丢）/ 模型声明了 service 从不写的键（恒 null）」的分叉。
    两侧都只与**硬编码基准**比，不互相比（否则就成了自指）。
    """
    ent = WorldEntity(
        id="loc_probe",
        id_field="location_id",
        id_prefix="loc",
        project_id="prj_probe",
        name="云海城",
    )
    assert set(ent.to_dict()) == EXPECTED_ENTITY_KEYS, (
        f"dataclass 多={set(ent.to_dict()) - EXPECTED_ENTITY_KEYS} "
        f"少={EXPECTED_ENTITY_KEYS - set(ent.to_dict())}"
    )
    assert set(WorldEntityOut.model_fields) == EXPECTED_ENTITY_KEYS


def test_literal_enums_stay_in_sync_with_domain_constants():
    """收窄的 Literal 必须与 domain 层的枚举常量同源（否则 DDL 加值而模型没跟上）。"""
    assert set(WorldVisibilityLiteral.__args__) == set(VISIBILITY_VALUES)
    assert set(WorldInjectModeLiteral.__args__) == set(INJECT_MODES)


def test_extra_allow_lets_unknown_keys_through():
    """⚠️ ``extra="allow"`` 的行为判别（撤掉它 ⇒ 未声明键被静默丢弃 ⇒ 本条必红）。

    为什么这条重要：``extra="allow"`` 是**前向兼容**约定——后端 ``to_dict()``
    以后加键时能自动透出，不必同步改模型。而 ``extra`` 的 pydantic 默认值是
    ``"ignore"``，那个形态下响应里会**静默少键**，功能测试照旧全绿。
    光断言响应键集抓不到（响应本来就只有那 11 个键），必须直接断言透出行为。
    """
    base = {
        "id": "loc_x",
        "project_id": "prj_x",
        "name": "云海城",
        "statement": "",
        "data": {},
        "visibility": "PUBLIC",
        "who_knows": None,
        "aliases": [],
        "inject_mode": "auto",
        "created_at": "2026-10-15T00:00:00+00:00",
        "updated_at": "2026-10-15T00:00:00+00:00",
    }
    assert set(base) == EXPECTED_ENTITY_KEYS, "夹具自身与硬编码基准不符，先修夹具"
    parsed = WorldEntityOut(**base, future_key="透出")
    dumped = parsed.model_dump()
    assert dumped["future_key"] == "透出", "extra='allow' 未透出未声明键——配置被改了？"
    assert parsed.__pydantic_extra__ == {"future_key": "透出"}, parsed.__pydantic_extra__


# ---------------------------------------------------------------------------
# 4. 形状细节：无数据时的 null / 空值语义
# ---------------------------------------------------------------------------


def test_who_knows_is_null_not_omitted(tmp_path: Path):
    """⚠️ ``who_knows`` 无数据时是 **null** 而不是被整个省略。

    这是本组最容易被改坏的一条：给端点挂上 ``response_model_exclude_none=True``
    （批次（五）的 simulation 就是靠它才正确的）会在这里**吃掉** ``who_knows``，
    响应凭空少一个键。world 这边实测 11 个键恒在，**不需要** exclude_none。
    判别：端点上多挂一个 ``response_model_exclude_none=True`` ⇒ 本条必红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            r = await c.post(f"/api/projects/{pid}/locations", json={"name": "云海城"})
            assert r.status_code == 201, r.text
            body = r.json()
            assert "who_knows" in body, (
                f"who_knows 被省略了（实际键：{sorted(body)}）——"
                f"多半是端点上多挂了 response_model_exclude_none"
            )
            assert body["who_knows"] is None, body["who_knows"]
            # 其余「实测恒非 null」的三个字段也不能被 exclude_none 误伤
            for key, want in (("aliases", []), ("statement", ""), ("data", {})):
                assert body[key] == want, f"{key} 实测应恒为 {want!r}，实际 {body[key]!r}"

    asyncio.run(run())


def test_optional_but_present_fields_round_trip(tmp_path: Path):
    """``who_knows`` 有值时原样回显，PATCH 能改（实测 str 数组）。"""
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            r = await c.post(
                f"/api/projects/{pid}/locations",
                json={"name": "破屋", "who_knows": ["char_a", "char_b"], "aliases": ["老家"]},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["who_knows"] == ["char_a", "char_b"], body["who_knows"]
            assert body["aliases"] == ["老家"], body["aliases"]

            got = (await c.get(f"/api/locations/{body['id']}")).json()
            assert got["who_knows"] == ["char_a", "char_b"], got["who_knows"]

            patched = (await c.patch(f"/api/locations/{body['id']}", json={"who_knows": ["char_c"]})).json()
            assert patched["who_knows"] == ["char_c"], patched["who_knows"]

    asyncio.run(run())


def test_data_accepts_both_object_and_array(tmp_path: Path):
    """⚠️ ``data`` 实测两种形态都出现 ⇒ 模型必须是 ``dict | list`` 联合。

    收窄成 ``dict[str, Any]`` 会让「写入数组」这一步直接 500
    （``ResponseValidationError``），而实体在库里其实是建成的——
    一个「写得进去、读不出来」的洞。判别：``data`` 改成只容对象 ⇒ 本条必红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            for payload, want in (
                ({"a": 1, "b": [2]}, {"a": 1, "b": [2]}),
                ([1, "x"], [1, "x"]),
                ([], []),
            ):
                r = await c.post(f"/api/projects/{pid}/locations", json={"name": "数据形态", "data": payload})
                assert r.status_code == 201, f"data={payload!r} -> {r.status_code}: {r.text}"
                assert r.json()["data"] == want, r.json()["data"]
                # 读回来还是同一形态
                got = (await c.get(f"/api/locations/{r.json()['id']}")).json()
                assert got["data"] == want, got["data"]

            # 默认值是 {} 而不是 null
            r = await c.post(f"/api/projects/{pid}/locations", json={"name": "无 data"})
            assert r.json()["data"] == {}, r.json()["data"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. 既有行为的刻画（不是本次引入，也不是本次认可的契约）
# ---------------------------------------------------------------------------


def test_world_rule_swallows_aliases_and_inject_mode(tmp_path: Path):
    """⚠️ **刻画**既有行为：world_rules 静默吞掉 ``aliases`` / ``inject_mode``。

    实测：POST 与 PATCH 都带上这两个键都**不报错**，返回的却是恒定的
    ``[]`` / ``"auto"``（service 对 world_rules 不写这两列）。
    这与 AGENTS.md §二 9「端点收下的参数必须有消费方测试」是同一族形状——
    「收下即忘」。模块 docstring 说这是**刻意**的（PRD 视 world_rule 为硬设定、
    常驻不参与触发），但从 HTTP 契约看，调用方发了这两个键却拿到静默丢弃，
    没有任何信号。

    本用例只把它**钉成可见**（将来有人改了实现会被立刻发现），
    **不代表认可**。要不要改成 422 拒绝是产品决策，需另行裁决——
    改动会波及 ``WorldService.create_world_rule`` / ``update_world_rule`` 的签名，
    不在本任务的独占文件内。
    """
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            r = await c.post(
                f"/api/projects/{pid}/world-rules",
                json={"name": "灵气稀薄", "aliases": ["X"], "inject_mode": "never"},
            )
            assert r.status_code == 201, r.text
            assert r.json()["aliases"] == [], r.json()["aliases"]
            assert r.json()["inject_mode"] == "auto", r.json()["inject_mode"]

            r = await c.patch(f"/api/world-rules/{r.json()['id']}", json={"aliases": ["Y"]})
            assert r.status_code == 200, r.text
            assert r.json()["aliases"] == [], r.json()["aliases"]

            # 对照组：locations / factions 确实会消费这两个键（所以不是整体失效）
            r = await c.post(
                f"/api/projects/{pid}/locations",
                json={"name": "对照组", "aliases": ["Z"], "inject_mode": "always"},
            )
            assert r.json()["aliases"] == ["Z"], r.json()["aliases"]
            assert r.json()["inject_mode"] == "always", r.json()["inject_mode"]

    asyncio.run(run())


def test_visibility_and_inject_mode_measured_values_survive_round_trip(tmp_path: Path):
    """实测过的枚举值逐个跑一遍（建模依据留证：四档 visibility + 三档 inject_mode）。"""
    app = _create_app(tmp_path)

    async def run():
        async with _client(app) as c:
            pid = await _new_pid(c)
            for vis in EXPECTED_VISIBILITY_VALUES:
                r = await c.post(f"/api/projects/{pid}/locations", json={"name": f"地{vis}", "visibility": vis})
                assert r.status_code == 201, f"{vis}: {r.text}"
                assert r.json()["visibility"] == vis
            for mode in EXPECTED_INJECT_MODES:
                r = await c.post(f"/api/projects/{pid}/factions", json={"name": f"宗{mode}", "inject_mode": mode})
                assert r.status_code == 201, f"{mode}: {r.text}"
                assert r.json()["inject_mode"] == mode
            # 第五档被 service 挡在 422（说明枚举是穷尽的，可以收窄成 Literal）
            r = await c.post(f"/api/projects/{pid}/locations", json={"name": "非法", "visibility": "NOPE"})
            assert r.status_code == 422, r.status_code
            r = await c.post(f"/api/projects/{pid}/locations", json={"name": "非法", "inject_mode": "sometimes"})
            assert r.status_code == 422, r.status_code

    asyncio.run(run())
