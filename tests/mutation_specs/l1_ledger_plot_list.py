"""L1 组（ledger / plot 的 5 个 **GET 列表**端点）的突变规格（2026-10-15 批次（六））。

纯数据文件——执行器是 ``tests/mutation_specs/run_spec.py``（所有人共用，不许改）。
用法::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/l1_ledger_plot_list.py

⚠️ **会临时改源码** ⇒ 禁与任何 pytest 并发跑（含 ``-n 4``）。

本组的量具纪律（每条都对应 AGENTS 坑区里已登记的一次实测存活）
--------------------------------------------------------------
1. **每条突变都真的改变行为**。只插注释 / 只改排版 = 等于没改，执行器按设计判 ``[ABORT]``
   （SHA256 未变）。L1-9 特意用「**加上** ``response_model_exclude_none=True``」而不是
   「翻掉」——列表端点**本来就没有**那个开关，撤一个不存在的开关是空变异；加它才真会把
   「键恒在、值为 null」的契约改成「键可能缺席」。
2. **锚点恰好命中 1 次**。``Hook`` / ``Debt`` / ``PlotEventOut`` / ``TimelineEventOut`` /
   ``RelationshipOut`` 的模型字段在两个文件里有**逐字相同**的形态（如
   ``    model_config = ConfigDict(extra="allow")`` 在同一文件出现 3 次），
   故 L1-6 ~ L1-8 一律带上一行注释区分——注释必须**写全**，截半行在文件里不存在。
3. **「少声明一个字段」只有 OpenAPI 断言抓得到**（L1-6 / L1-7）。五个模型都
   ``extra="allow"`` ⇒ 响应照样透出那个键（作为 extra），响应侧的键集断言仍绿。
   照实写，不假装响应侧能抓到。
4. **本组只突变本组独占的文件**。``Hook`` / ``Debt`` / ``PlotEventOut`` /
   ``TimelineEventOut`` 的字段声明属 G2 组（``g2_ledger_plot.py`` 已覆盖），
   本文件不重复突变它们——避免两组规格互相覆盖同一条断言的证据。

2026-10-16 追加：relationships 的 visibility / who_knows（服务层曾静默丢弃）
--------------------------------------------------------------------------
修复是「``PlotService._row_to_relationship`` 补映射 + ``Relationship`` 补声明」
**两侧同时**——只改 service 会让 dataclass 收到不认识的关键字参数 ⇒ HTTP 500。
故 L1-11~L1-13 分别守：service 的可见性映射、who_knows 的解码兜底、模型的 ``to_dict``。

**两条量具纪律被这三条直接踩到**：

- **锚点重复**：``visibility=...`` 与 ``who_knows=_decode_who_knows(...)`` 两行在
  ``_row_to_timeline`` 里**逐字符相同**（已实测 naive 锚点命中 2 次，执行器会判
  ``[ABORT]``）⇒ 必须带 ``last_state_version=...`` 那行做上下文，它是 relationship 独有的。
- **判别文件要选对，且理由要实测**：L1-12 的判别在 ``_TEST_DIRTY`` 而不是 ``_TEST``。
  **先验已被推翻**——我原以为「``_TEST`` 夹具 who_knows 全是合法 JSON，裸 loads 照样跑通」，
  实测 ``_TEST`` **也红**，但红在 ``json.loads(None)`` 的 TypeError（它的首行夹具是 NULL），
  属**失败点漂移**而非测到目标机制。复核者若只看到 [CAUGHT] 无法区分这两者，
  故条目注释里写明了「预期红在哪个用例、红的理由是什么」。

另外 L1-11 是**键集断言恒真、值断言才红**的典型：删掉映射后 ``to_dict`` 照样吐
``visibility`` 键 ⇒ 键集仍是 9 个且全绿 ⇒ 判别靠的是 fixture 里造的两个不同
``visibility`` 值（PUBLIC / RESTRICTED）。**键在 ≠ 键里的值是活的。**
"""

from __future__ import annotations

_TEST = "tests/api/test_ledger_plot_list_response_contract.py"
_TEST_DIRTY = "tests/api/test_plot_dirty_json_row_degradation.py"
_LEDGER_ROUTER = "packages/core/api/routers/ledger.py"
_PLOT_ROUTER = "packages/core/api/routers/plots.py"
_PLOT_MODELS = "packages/domain/plot/models.py"
_PLOT_SERVICE = "packages/domain/plot/service.py"

MUTATIONS = [
    # ------------------------------------------------------------ routers: 撤 response_model
    # 撤掉之后 OpenAPI 的 items 退化成匿名 object ⇒ 前端拿回 Record<string, unknown>
    # ⇒ test_list_endpoints_declare_typed_items 必红。
    (
        "L1-1",
        _LEDGER_ROUTER,
        '@router.get("/projects/{pid}/hooks", response_model=list[Hook])\n',
        '@router.get("/projects/{pid}/hooks")\n',
        _TEST,
    ),
    (
        "L1-2",
        _LEDGER_ROUTER,
        '@router.get("/projects/{pid}/debts", response_model=list[Debt])\n',
        '@router.get("/projects/{pid}/debts")\n',
        _TEST,
    ),
    (
        "L1-3",
        _PLOT_ROUTER,
        '@router.get("/projects/{pid}/events", response_model=list[PlotEventOut])\n',
        '@router.get("/projects/{pid}/events")\n',
        _TEST,
    ),
    (
        "L1-4",
        _PLOT_ROUTER,
        '@router.get("/projects/{pid}/timeline", response_model=list[TimelineEventOut])\n',
        '@router.get("/projects/{pid}/timeline")\n',
        _TEST,
    ),
    (
        "L1-5",
        _PLOT_ROUTER,
        '@router.get("/projects/{pid}/relationships", response_model=list[RelationshipOut])\n',
        '@router.get("/projects/{pid}/relationships")\n',
        _TEST,
    ),
    # ------------------------------------------- router: 接线挂错模型（防复制粘贴式接线）
    # relationships 若误挂 list[PlotEventOut]：PlotEventOut 的 type/cause/time 等必填
    # 字段在关系行里不存在 ⇒ FastAPI ResponseValidationError ⇒ 整个端点 500。
    (
        "L1-10",
        _PLOT_ROUTER,
        '@router.get("/projects/{pid}/relationships", response_model=list[RelationshipOut])\n',
        '@router.get("/projects/{pid}/relationships", response_model=list[PlotEventOut])\n',
        _TEST,
    ),
    # --------------------------- router: 加上 response_model_exclude_none（不是撤不存在的开关）
    # 实测这五类实体的可空列是「键恒在、值为 null」；加上它 FastAPI 会把每个 None 抹掉，
    # hooks 列表行凭空少 introduced_chapter_id / expected_payoff_chapter_id /
    # payoff_chapter_id / who_knows 四个键 ⇒ 键集断言 + null 断言双双红。
    (
        "L1-9",
        _LEDGER_ROUTER,
        '@router.get("/projects/{pid}/hooks", response_model=list[Hook])\n',
        '@router.get(\n'
        '    "/projects/{pid}/hooks", response_model=list[Hook], response_model_exclude_none=True\n'
        ")\n",
        _TEST,
    ),
    # ------------------------------------------------ models: 少声明一个字段（只有 OpenAPI 抓得到）
    (
        "L1-6",
        _PLOT_MODELS,
        '    state: dict[str, Any] = Field(description="已从 state_json 解码，形如 {intensity, since_chapter}")\n'
        '    last_state_version: int = Field(description="DDL NOT NULL；快照版本号")\n',
        '    state: dict[str, Any] = Field(description="已从 state_json 解码，形如 {intensity, since_chapter}")\n',
        _TEST,
    ),
    # ⚠️ 锚点含中文注释行：`from_character_id/to_character_id/relation_type` 三行在
    # ``Relationship`` dataclass 里**逐字相同**，必须靠上面那行注释唯一区分。
    (
        "L1-7",
        _PLOT_MODELS,
        "    # 0022 之后端点**可以**是 ``faction_id``（不再有 characters 外键），故是裸 str。\n"
        "    from_character_id: str\n"
        "    to_character_id: str\n"
        "    relation_type: str\n",
        "    # 0022 之后端点**可以**是 ``faction_id``（不再有 characters 外键），故是裸 str。\n"
        "    from_character_id: str\n"
        "    to_character_id: str\n",
        _TEST,
    ),
    # RelationshipOut 的 extra="allow" 消失 ⇒ 新键被 response_model 静默裁掉
    #（由 test_relationship_out_permits_unknown_keys 抓到，不是响应侧）。
    (
        "L1-8",
        _PLOT_MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n'
        "    # ⚠️ 键名是 ``id``（DB 主键列名 ``relationship_id``），同 PlotEventOut / TimelineEventOut。\n"
        "    id: str\n"
        "    project_id: str\n",
        "    # ⚠️ 键名是 ``id``（DB 主键列名 ``relationship_id``），同 PlotEventOut / TimelineEventOut。\n"
        "    id: str\n"
        "    project_id: str\n",
        _TEST,
    ),
    # ================================ 2026-10-16：relationships 的 visibility / who_knows
    # 服务层曾只映射 7 列 ⇒ 这两列被静默丢弃（DB 明明有值，响应里连键都没有）。
    # 修复是「service 补映射 + models 补声明」**两侧同时**改；只改一侧就是 HTTP 500。
    # 下面三条分别守两侧与解码兜底。
    #
    # ⚠️ **锚点必须带 ``last_state_version`` 那一行**做上下文（执行器按「恰好命中 1 次」
    # 判定，命中 2 次即 [ABORT]）：``visibility=...`` 与
    # ``who_knows=_decode_who_knows(...)`` 这两行在 ``_row_to_timeline`` 里
    # **逐字符相同**，本批已实测 naive 锚点命中 2 次（坑区已登记的锚点陷阱）。
    # 只有 ``last_state_version=...`` 是 relationship 独有的（timeline_events 无此列）。
    #
    # 判别点说明：L1-11 删掉映射后 ``Relationship.visibility`` 退回 dataclass 默认
    # ``"PUBLIC"`` ⇒ **键集仍是 9 个、仍是绿的** ⇒ 只有**值**断言能抓到它
    # （``test_relationships_list_row_keys_match_hardcoded_expectation`` 里的
    # ``enemy["visibility"] == "RESTRICTED"``）。这正是本缺陷的同款形状：
    # **键在、内容是死的**，键集断言对它恒真。
    (
        "L1-11",
        _PLOT_SERVICE,
        '            last_state_version=row["last_state_version"],\n'
        '            visibility=row["visibility"] if "visibility" in row.keys() else "PUBLIC",\n',
        '            last_state_version=row["last_state_version"],\n',
        _TEST,
    ),
    # who_knows 回退裸 ``json.loads``：解析失败抛 JSONDecodeError ⇒ 冒到路由层 ⇒
    # **整个 relationships 列表 500，干净行陪葬**。
    # ⚠️ 判别文件是 ``test_plot_dirty_json_row_degradation.py``，**不是** ``_TEST``——
    # 但理由**不是**「``_TEST`` 不会被抓到」（那是先验，已被实测推翻，见下）。
    # 实测（monkeypatch 装上变异后跑真实用例）：``_TEST`` **也会红**，但红在
    # ``TypeError: the JSON object must be str, bytes or bytearray, not NoneType``
    # ——它的第一行夹具 ``who_knows`` 是 NULL，``json.loads(None)`` 当场就炸。
    # 那是**失败点漂移到更靠前的另一个机制**（坑区「夹具自带噪声 ⇒ 失败点漂移」）：
    # 红是真的红，测的却不是「脏 JSON 文本该降级」。
    # ``_TEST_DIRTY`` 才是守这条机制的：脏文本 ``not json at all`` 与形状不对
    # （``'"字符串"'`` / ``'["ok", 7]'``）都必须降级为 null 且端点保持 200。
    (
        "L1-12",
        _PLOT_SERVICE,
        '            last_state_version=row["last_state_version"],\n'
        '            visibility=row["visibility"] if "visibility" in row.keys() else "PUBLIC",\n'
        '            who_knows=_decode_who_knows(row["who_knows"]) if "who_knows" in row.keys() else None,\n',
        '            last_state_version=row["last_state_version"],\n'
        '            visibility=row["visibility"] if "visibility" in row.keys() else "PUBLIC",\n'
        '            who_knows=json.loads(row["who_knows"]) if "who_knows" in row.keys() else None,\n',
        _TEST_DIRTY,
    ),
    # 模型侧：``to_dict`` 漏掉一个键 ⇒ 响应少一键 ⇒ 键集断言红。
    # 与 L1-6/L1-7 的区别：那两条突变的是**出参模型 RelationshipOut**（只被 OpenAPI
    # 断言抓到），本条突变的是**领域 dataclass Relationship.to_dict**（被响应键集
    # 断言直接抓到）——两侧是不同的接线点，不是同一条。
    (
        "L1-13",
        _PLOT_MODELS,
        '            "last_state_version": self.last_state_version,\n'
        '            "visibility": self.visibility,\n'
        '            "who_knows": self.who_knows,\n',
        '            "last_state_version": self.last_state_version,\n'
        '            "visibility": self.visibility,\n',
        _TEST,
    ),
]
