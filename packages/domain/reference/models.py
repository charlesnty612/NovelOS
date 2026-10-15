"""Reference（拆书 canon）域的 **API 出参** 契约模型（2026-10-15 批次（六）· G7）。

与 :mod:`packages.domain.reference.service` 的分工：service 管 CRUD 与行投影，
本文件只描述 ``routers/reference.py`` 四条端点**实际吐出**的 JSON 键集，
供 OpenAPI 声明与前端 codegen。

覆盖端点：
- ``POST /projects/{pid}/deconstruct``            → :class:`DeconstructStartResponse`
- ``POST /projects/{pid}/deconstruct-upload``     → :class:`DeconstructStartResponse`
- ``GET  /canons/{canon_id}``                     → :class:`CanonDetailOut`
- ``GET  /projects/{pid}/canons``                 → :class:`CanonSummaryOut`
- ``POST /projects/{pid}/canons/{cid}/to-style-sample`` → :class:`StyleSampleOut`

（``GET /projects/{pid}/canons`` 曾一度「刻意不建模」，理由是它不在当时的端点清单内；
2026-10-15 批次（六）· L4 补上，模型见 :class:`CanonSummaryOut`。）

字段形状经实测（临时库 + 合成夹具）。三条关键事实写在各模型的 docstring 里，
其中第 3 条是**有意引入的行为变更**，调用方须知：

1. ``deconstruct`` 的响应**是条件键**：`canon_id` / ``extracts_count`` 只在
   ``status == "COMPLETED"`` 时出现，``error`` 只在 ``FAILED`` 时出现，
   其余状态（RUNNING / PAUSED / …）三者皆无。
2. canon / extract 的 JSON 列是**存什么吐什么**，不做 schema 校验 ⇒ 用宽松类型。
3. 声明 ``response_model`` 后，``deconstruct`` 的三个条件键**从「可能缺席」变成
   「恒在，未命中时为 null」**。这是本批次唯一改变了线上 JSON 形状的地方，
   理由与代价见 :class:`DeconstructStartResponse`。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CanonDetailOut",
    "CanonExtractOut",
    "CanonSummaryOut",
    "DeconstructStartResponse",
    "StyleSampleOut",
]


class DeconstructStartResponse(BaseModel):
    """``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。

    两条端点都汇到 ``_start_deconstruct_internal``，返回同一个 dict。

    ⚠️ **本模型引入了一处行为变更**：原实现的三个条件键是「命中才写、否则整个键缺席」；
    声明成 ``Optional`` + 默认 ``None`` 后，它们**恒在**，未命中时为 ``null``。

    - 为什么接受：OpenAPI / 前端 codegen 需要一个**稳定**的键集。条件键在
      TypeScript 里会逼出 ``canon_id?: string`` 与一堆 ``&&`` 判空，而「缺席」在
      JSON Schema 里根本没有对应表达——这正是本批次要消灭的「前端手写契约」。
    - 代价：存量前端若用 ``"canon_id" in body`` 判存在，语义从「已 COMPLETED 且有
      canon」变成「恒真」。**改判据请改成 ``body.canon_id != null``。**
      （本仓唯一前端调用方是拆书面板，未用 in 判据；见
      ``tests/api/test_reveal_and_judge_response_contract.py`` 同批口径。）

    不设 ``extra="allow"``：这三个条件键是全部的分支，超出的键只可能是 bug。
    """

    run_id: str
    # workflow_runs.status 的取值（PENDING/RUNNING/PAUSED/COMPLETED/FAILED/CANCELLED）。
    # 刻意**不**用 Literal：新增状态不该让这条端点 500。
    status: str
    current_node: Optional[str] = None
    project_id: str
    book_title: str
    # ↓↓↓ 仅 COMPLETED 时由 T4_persist 带出
    canon_id: Optional[str] = None
    extracts_count: Optional[int] = None
    # ↑↑↑
    # 仅 FAILED 时带出（run.error）
    error: Optional[str] = None


class CanonExtractOut(BaseModel):
    """``GET /canons/{cid}`` 里 ``extracts[]`` 的一格。

    ⚠️ ``extract_json`` 用 :data:`typing.Any`：**这是有意的**。
    ``ReferenceService._safe_parse`` 在 JSON 解析失败时**原样返回字符串**
    （「失败返回原值」是它的 docstring 明写的行为），所以该列的运行时类型可能是
    dict / list / str。声明成 ``dict[str, Any]`` 会让一份坏 extract 把整条端点打成
    500；这里如实建模为「不校验的存储载荷」。
    """

    model_config = ConfigDict(extra="allow")

    extract_id: str
    chapter_index: int
    extract_json: Any = None
    created_at: str


class CanonDetailOut(BaseModel):
    """``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。

    键集来源（独立于实现的第三方基准）：:meth:`ReferenceService.get_canon_detail`
    的 docstring 明列 ``canon_id / project_id / title / reader_profile / status /
    canon_json / report_md / created_at / extracts``。

    ⚠️ ``canon_json`` 允许为 ``None``：``json.loads('null')`` 返回 ``None`` 且**不抛**，
    而 ``get_canon_detail`` 的 except 只接 ``(TypeError, ValueError)`` ⇒ 一份
    内容为字面量 ``null`` 的存量 canon 会让 ``canon_json`` 变成 ``None``。
    声明成 ``dict`` 会把这种情况打成 500，故如实建模为可空。
    """

    model_config = ConfigDict(extra="allow")

    canon_id: str
    project_id: str
    title: str
    reader_profile: str
    status: str
    canon_json: Optional[dict[str, Any]] = None
    report_md: str
    created_at: str
    extracts: list[CanonExtractOut] = Field(default_factory=list)


class StyleSampleOut(BaseModel):
    """``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。

    该端点**复用** ``author_style_samples`` router 的 ``create_style_sample``（直接函数
    调用），因此响应就是文风样例本体的 6 个键 = ``author_style_samples`` 表的全部列
    （见 ``0008_author_style_samples_and_overdue.sql``）。

    ⚠️ 本模型**只**在 reference 端点上声明；``author_style_samples`` 自己的端点
    仍用 router 内定义的 ``StyleSample``（两者字段逐字相同）。
    ⚠️ **两个同形模型并存是已知的重复**：合并会让批次（六）· G7 钉住的
    ``$ref.endswith("/StyleSampleOut")`` 断言（端点 ⇄ schema 的接线判别）失败，
    故留待独立裁决，不在本文件顺手改。
    """

    sample_id: str
    project_id: str
    title: str
    content: str
    created_at: str
    updated_at: str


class CanonSummaryOut(BaseModel):
    """``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。

    **键集来源（独立于实现的第三方基准）**：
    :func:`packages.domain.reference.service.summary_of` 的 docstring 明列
    「输出：``canon_id / project_id / title / reader_profile / status / created_at /
    logline / spine_count / rhythm_chapter_count`` 完整 dict」＝ 恒 9 键、无条件键。
    （前端 ``apps/web/src/api/types.ts`` 的手写 ``CanonSummary`` 逐字一致。）

    ⚠️ **别把它当成「CanonDetailOut 少几个字段」**——两边**都是 9 键但键名不同**：

    =======================================  ======================================
    ``CanonSummaryOut``（列表行）           ``CanonDetailOut``（单体）
    =======================================  ======================================
    6 个身份列 + ``logline`` +               6 个身份列 + ``canon_json`` +
    ``spine_count`` + ``rhythm_chapter_count``  ``report_md`` + ``extracts``
    =======================================  ======================================

    根因：列表的 SQL 只 SELECT 7 列（**无** ``report_md``），而 ``canon_json``
    **本身不进响应**——它被拆成三个派生量（logline 直取、``spine`` / ``emotion_curve``
    取长度）。用同一个模型套两条端点 ⇒ 其中一条静默丢字段或 500。
    「同一条 canon，列表和单体都是 9 键」这个巧合正是最容易被读成同形的地方。

    ⚠️ ``status`` **刻意不用 Literal**：DDL 有 ``CHECK (status IN
    ('active','archived'))``，用 Literal 不会因存量数据炸；但同文件的
    :class:`DeconstructStartResponse` 已定调「新增状态不该让端点 500」，保持一致。

    ``extra="allow"`` 的理由：``summary_of`` 返回的是**手写字面量 dict**，将来给摘要
    加一个派生键（如 ``technique_count``）是自然演进；不开 ``extra`` 会被
    ``response_model`` 静默裁掉（数据在库里、API 读不到、零报错）。
    ⚠️ **如实登记残余边界**：该 dict 没有天然注入点，所以「多出来的键能不能透出」
    在本文件里**只能靠 monkeypatch service 制造**（见
    ``test_canon_list_passes_through_unknown_keys``）。不换 fixture 的读者会
    以为这条属性是白声明的。
    """

    model_config = ConfigDict(extra="allow")

    canon_id: str
    project_id: str
    title: str
    reader_profile: str
    status: str
    created_at: str
    # 以下三个由 summary_of 从 canon_json 派生；解析失败/类型不符时回落为 "" / 0。
    logline: str = ""
    spine_count: int = 0
    rhythm_chapter_count: int = 0
