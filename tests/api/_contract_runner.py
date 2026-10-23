"""contract 测试族的共享断言 runner（2026-10-23 task-9 · C-1 模板族收敛）。

为什么有它
----------
``tests/api/*_response_contract*`` 十几个文件是同一模板的复制：建 app → 灌夹具 →
四段断言（① schema 已声明且被端点引用 / ② **实际返回键集 == 硬编码期望** /
③ ``model_fields == 硬编码期望`` / ④ ``extra="allow"`` 不裁新键）。
本模块把「app / client / 断言」这三样**逐字**收成单点，各文件只留**自己的硬编码
基准表 + 端点表 + 专属用例**。

⚠️⚠️ **第一铁律：②③ 两段必须保持「硬编码第三方基准」**
--------------------------------------------------
本模块的每个比对函数都要求调用方**显式传入期望值**（``expected`` 参数），
**刻意不提供**「从模型自动取期望」的便捷入口 —— 那种入口就是**自指断言**的门。

自指的危害有本仓实证（``tests/api/test_arc_response_contract.py:141-145`` 留痕）：
首版写成 ``assert set(body) == set(Model.model_fields)`` 时，突变 **M14 存活**
—— 把模型里的字段删掉，**响应与期望同时变小**，断言照样绿 ⇒
「模型少声明一个字段」这类改动**零告警**通过。
（⚠️ 那是**留痕**，不是活例：该文件**现码已是硬编码形态**，首版的自指写法早已修正。）

⇒ 判别口径（本模块的所有比对都按这个执行）：
* **期望值** = 调用方写死的常量（来自实机测量，**不是**从被测代码抄的）；
* **被测值** = 实际响应 / 实际 schema / 实际 ``model_fields``；
* 两者**逐字相等**才算通过，且失败信息必须点名**多哪些键、少哪些键**。

「OpenAPI 的 ``properties``」是**模型之外**的第三方基准（删模型声明会让
``properties`` 同步变小 ⇒ 必红），所以第①段断言也必须打它，
不能只打响应键集（``extra="allow"`` 下响应照样带着那个键）。

另外：``model_fields`` **只允许**出现在「模型 vs 硬编码」的用例里，
**一处都不许**出现在「响应 vs 模型」的比对里。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

__all__ = [
    "assert_extra_allowed",
    "assert_keys_match",
    "assert_keys_match_ddl",
    "assert_list_items_named_ref",
    "assert_model_fields_match",
    "assert_openapi_properties_and_required",
    "assert_openapi_ref",
    "assert_response_keys",
    "client_ctx",
    "create_test_app",
    "db_columns",
    "one_shot",
]


# ---------------------------------------------------------------------------
# app / client
# ---------------------------------------------------------------------------


def create_test_app(tmp_path: Path):
    """建一个指向临时库的 app（迁移由各文件自己的夹具负责，**不**在此跑）。"""
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def client_ctx(app, *, enter_lifespan: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    """产出一个 ASGI 客户端。

    ``enter_lifespan=False`` ⇒ **刻意不进 lifespan**：迁移已由夹具显式跑过，而
    lifespan 的 startup 会跑 ``recover_interrupted_runs``（自愈）⇒ 对「直接 INSERT
    终态行」的夹具是多余的干扰（``test_state_list_response_contract.py`` 的选择）。
    ``enter_lifespan=True`` ⇒ 进 lifespan（需要 prompt sync / 启动自愈的夹具用）。
    """
    if enter_lifespan:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as c:
                yield c
    else:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c


async def one_shot(
    app, method: str, path: str, *, enter_lifespan: bool = False, **kwargs
) -> httpx.Response:
    """一次性请求（每次新开一个客户端）——「每请求一个连接」的夹具用。"""
    async with client_ctx(app, enter_lifespan=enter_lifespan) as c:
        return await c.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# ② 实际键集 vs 硬编码期望
# ---------------------------------------------------------------------------


def assert_keys_match(
    body: Mapping[str, Any], expected: Iterable[str], label: str
) -> None:
    """``body`` 的键集必须与**硬编码** ``expected`` 逐字相等。

    失败信息必须点名多/少哪些键（复核者要能一眼看出差在哪）。
    """
    assert isinstance(body, Mapping), f"{label} 应是对象，实际 {type(body)}"
    want = set(expected)
    got = set(body)
    assert got == want, f"{label} 键集不符：多={sorted(got - want)} 少={sorted(want - got)}"


# ---------------------------------------------------------------------------
# ③ model_fields vs 硬编码期望（**唯一**允许出现 model_fields 的位置）
# ---------------------------------------------------------------------------


def assert_model_fields_match(
    model: Any, expected: Iterable[str], label: str = ""
) -> None:
    """模型声明的字段集必须与**硬编码**期望逐字相等。

    ⚠️ ``expected`` 必须由调用方写死；**不许**传 ``set(model.model_fields)``
    （那是自指 ⇒ 恒真，见模块 docstring 的 M14 实证）。
    """
    want = set(expected)
    declared = set(model.model_fields)
    tag = label or getattr(model, "__name__", str(model))
    assert declared == want, (
        f"{tag} 的字段集不符：多={sorted(declared - want)} 少={sorted(want - declared)}"
    )


# ---------------------------------------------------------------------------
# ① OpenAPI 侧
# ---------------------------------------------------------------------------


def assert_openapi_ref(
    openapi: dict, path: str, method: str, code: str, model_name: str, label: str = ""
) -> None:
    """该端点该状态码的响应 schema 必须是**具名** ``$ref``（不是匿名 object）。"""
    ref = (
        openapi["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ]["$ref"]
    )
    assert ref.endswith(f"/{model_name}"), f"{method.upper()} {path} -> {ref}"
    assert model_name in openapi["components"]["schemas"], (
        f"{model_name} 未进 components.schemas"
    )


def _json_schema_of(openapi: dict, path: str, method: str) -> dict:
    """取该端点该方法 200/201 的 json schema（没有就响亮失败）。"""
    op = openapi["paths"][path][method]
    for code in ("200", "201"):
        if code in op["responses"]:
            return op["responses"][code]["content"]["application/json"]["schema"]
    raise AssertionError(f"{method.upper()} {path} 没有 200/201 的 json schema")


def assert_list_items_named_ref(
    openapi: dict, path: str, method: str, model_name: str, label: str = ""
) -> None:
    """列表端点的 ``items`` 必须是**具名** ``$ref``。

    裸 ``list[dict]`` 会走成 ``{"items": {"type": "object"}, "type": "array"}``
    ⇒ 前端拿到的就是 ``Record<string, unknown>`` ⇒ **等于没有契约**。
    """
    schema = _json_schema_of(openapi, path, method)
    assert schema.get("type") == "array", f"{path} 的响应不是数组：{schema}"
    items = schema.get("items") or {}
    ref = items.get("$ref")
    assert isinstance(ref, str) and ref.endswith(f"/{model_name}"), (
        f"{method.upper()} {path} 的 items 不是具名 $ref ⇒ 前端仍拿到 "
        f"Record<string, unknown>：{items}"
    )
    assert model_name in openapi["components"]["schemas"], (
        f"{model_name} 未进 components.schemas"
    )


def assert_openapi_properties_and_required(
    schema_component: Mapping[str, Any],
    expected_properties: Iterable[str],
    expected_required: Iterable[str] | None = None,
    label: str = "",
    hint: str = "",
) -> None:
    """OpenAPI schema 的 ``properties`` / ``required`` 必须与**硬编码**期望一致。

    这是 ``extra="allow"`` 的**必要配套**：模型里删掉一个字段声明，响应照样带着
    那个键（extra 透出）⇒ 只断言响应键集抓不到 ⇒ 必须打 schema 侧。

    ``hint``：失败时附带的**留痕文字**（各域自己的事故说明，G10 病之类）⇒
    抽到共享模块后**不许丢**。
    """
    want = set(expected_properties)
    props = set(schema_component.get("properties") or {})
    tag = label or "schema"
    assert props == want, (
        f"{tag} 的 schema properties 不符：多={sorted(props - want)} 少={sorted(want - props)}"
        f"{hint}"
    )
    if expected_required is None:
        return
    need = set(expected_required)
    required = set(schema_component.get("required") or [])
    assert required == need, (
        f"{tag} 的 schema required 不符：多={sorted(required - need)} 少={sorted(need - required)}"
        f"{hint}"
    )
    assert required <= props, f"{tag} 的 required ⊄ properties{hint}"


# ---------------------------------------------------------------------------
# 端点键集（② 的循环形态：同一个期望集打一批端点）
# ---------------------------------------------------------------------------


async def assert_response_keys(
    c: Any,
    method: str,
    path: str,
    expected: Iterable[str],
    *,
    json: Any = None,
    status: int = 200,
    label: str = "",
) -> dict:
    """打一个端点并断言其响应键集 == **硬编码** ``expected``；返回响应体。

    失败信息点名**多哪些键、少哪些键**（复核者要能一眼看出差在哪）。
    """
    r = await c.request(method, path, json=json)
    assert r.status_code == status, f"{method.upper()} {path} status={r.status_code}: {r.text[:300]}"
    body = r.json()
    assert_keys_match(body, expected, label or f"{method.upper()} {path}")
    return body


# ---------------------------------------------------------------------------
# DDL 列 vs 硬编码键集（① 的第四种形态：模型之外的第二个第三方基准）
# ---------------------------------------------------------------------------


def db_columns(conn: Any, table: str) -> set[str]:
    """读一张表的列名集合（``PRAGMA table_info``）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def assert_keys_match_ddl(
    expected: Iterable[str],
    columns: Iterable[str],
    *,
    rename: Mapping[str, str] | None = None,
    label: str = "",
) -> None:
    """硬编码键集 vs **DB 实表列**：两者必须逐字相等（允许显式声明的重命名）。

    这是「模型之外的第二个第三方基准」：键集与 DDL 列各写一遍，两边同时对 ⇒
    「模型少声明一个键」或「service 漏写一个列」都会红。

    ``rename`` 显式声明「DB 列名 → API 键名」的映射（例：DB ``event_id`` 在响应里
    叫 ``id``）——**必须显式写出**，不许在比对时悄悄忽略差额。
    """
    want = set(expected)
    cols = set(columns)
    mapping = dict(rename or {})
    mapped = {mapping.get(c, c) for c in cols}
    tag = label or "DDL 列"
    assert mapped == want, (
        f"{tag} 与硬编码键集不符：DDL 多={sorted(mapped - want)} "
        f"少={sorted(want - mapped)}（rename={mapping}）"
    )


# ---------------------------------------------------------------------------
# ④ extra="allow" 不裁新键
# ---------------------------------------------------------------------------


def assert_extra_allowed(
    model: Any,
    base_payload: Mapping[str, Any],
    *,
    unknown_key: str = "future_key",
    unknown_value: Any = "透出",
    label: str = "",
) -> None:
    """``extra="allow"`` 的行为判别：未声明键必须**透出**。

    为什么必须直接断言透出行为：光断言响应键集抓不到（响应本来就只有那若干个键），
    而 ``extra`` 的 pydantic 默认值是 ``"ignore"`` ⇒ 那个形态下响应会**静默少键**，
    功能测试照旧全绿。
    """
    parsed = model(**dict(base_payload), **{unknown_key: unknown_value})
    dumped = parsed.model_dump()
    assert dumped.get(unknown_key) == unknown_value, (
        "extra='allow' 未透出未声明键——配置被改了？"
    )
    extras = getattr(parsed, "__pydantic_extra__", None)
    assert extras == {unknown_key: unknown_value}, extras
