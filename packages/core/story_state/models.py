"""Canonical State 快照的**对外契约**（Pydantic）。

为什么要有这个模块
------------------
2026-10-15 之前，`GET /projects/{pid}/state` 的返回类型是裸 ``dict``——**没有**
``response_model``，于是 OpenAPI 里根本没有它的 schema。前端拿不到契约，只能在
``apps/web/src/api/types.ts`` 里手写一个欠定义类型：

    export interface SnapshotResponse {
      version?: number;          // ← 后端从不返回这个键
      state_version?: number;    // ← 后端真正返回的键
      characters?: unknown;
      ...
      [key: string]: unknown;
    }

两个互斥的版本命名、六个全 optional 的字段、一个吞掉一切类型错误的索引签名——
这不是契约，是「不知道后端返回什么」的兜底。**代价已经付出**（见
``apps/web/src/pages/ProjectOverviewPage.tsx``）：兜底类型逼出一层手写归一化，
而归一化把 ``characters``（后端是 list）用 ``normObj`` 拍成 ``{}``、把 ``events``
（后端是 dict）用 ``normArr`` 拍成 ``[]``，于是概览页「角色」与「事件·累计」
**两个统计项恒为 0**，且全程无任何告警。

字段形状由 ``snapshot.build_initial_state`` 与 ``snapshot.materialize_snapshot``
共同保证，并由 ``commits.rebuild_snapshot_collections_from_db`` 从领域表重放。
实测 133 条存量快照形状零违例（2026-10-15，只读查询），故此处用**严格类型**
而不是宽松 ``Any``——宽松类型只会把同一个坑重新挖一遍。

``extra="allow"``：故事状态是开放的 JSON 文档，delta 可以带额外集合键；契约描述
**已知的六个**，不宣称封闭（封闭的话每次加集合都要改契约 + regen 前端类型）。

本模块其余部分（2026-10-15 批次（六）追加）：**写面**七个端点的 response_model。
形状一律**量出来而非读出来**——临时库 + 合成夹具跑通端点后 dump 键集（见
``tests/api/test_story_state_write_response_contract.py`` 顶部实测表）。三条
最容易踩空的结论写在各自类里，不要凭直觉改。

**读面列表端点**（批次（六）L2 组追加，见文件末尾的三个 ``*ListItem`` / ``BranchSummary``）
与之同款纪律，但多一条：列表行的形状**不能**从单体端点的形状推。
实测推翻了两条这样的先验——``commits`` 列表行是裸 DB 行（12 键）而
``POST /commits`` 是 4 键动作摘要；``branches`` 列表行与 ``POST /branches``
**键集逐字相同**却因 main 的 ``parent_branch_id`` 为 null 而**不可复用**。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def _drop_if_none(value: Any) -> bool:
    """``exclude_if`` 谓词：值为 None ⇒ **整个键省略**（而不是序列化成 ``null``）。

    为什么用**字段级** ``exclude_if`` 而不是端点级 ``response_model_exclude_none=True``
    （``packages/core/simulation/models.py::StateDiff`` 用的是后者，两种并存）：

    ``diff_snapshots`` 对六个实体段是「有差异才写入」。而三个恒在键里 ``branch_id``
    **恒为 null**（``queries.diff_versions`` 把 ``branch_id=None`` 写死，且
    ``branch_id is not None`` 时一律抛 409）。端点级 ``response_model_exclude_none``
    是一刀切的：它会把 ``branch_id: null`` 一起抹掉，**空 diff 的键集从 3 个掉到 2 个**
    （实测），那是既有契约的静默变更；前端 ``StateDiffResponse.branch_id?: string | null``
    正是按「键在、值为 null」声明的。字段级 ``exclude_if`` 只对那六个条件段生效，
    ``branch_id`` 原样保留，**响应与加契约之前逐键一致**。

    反向的例子同样在门面上：``promote_branch`` 的空分支形状里 ``commit_id`` /
    ``delta_id`` 就是 ``null``，端点级开关会把这两个键一起抹掉。⇒ 写面七个端点
    **一个都没加**端点级 ``response_model_exclude_none``。
    """

    return value is None


class SnapshotResponse(BaseModel):
    """``GET /projects/{pid}/state`` 与 ``GET /projects/{pid}/state/versions/{n}`` 的响应。

    ``state_version`` 是**唯一**的版本字段名。后端 ``queries.get_current_state``
    与 ``queries.get_snapshot`` 都写 ``state_version``；前端历史上还并列过一个
    ``version``，是欠定义类型自己臆造的别名，已删除。
    """

    model_config = ConfigDict(extra="allow")

    state_version: int = Field(
        description="快照版本号；无快照时后端返回 0（genesis 未初始化前的形态）",
    )
    characters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="角色列表（按 character_id ASC）",
    )
    world: dict[str, Any] = Field(
        default_factory=dict,
        description="世界观：{locations: dict, factions: dict, world_rules: list}",
    )
    hooks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="伏笔列表（按 hook_id ASC）",
    )
    debts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="叙事债务列表（按 debt_id ASC）",
    )
    recent_events: list[Any] = Field(
        default_factory=list,
        description="最近事件（时间线视角）",
    )
    events: dict[str, Any] = Field(
        default_factory=dict,
        description="剧情事件，**按 event_id 键的字典**（不是列表）",
    )


# ---------------------------------------------------------------------------
# 写面契约（2026-10-15 批次（六））。键集全部由实测得出，见模块 docstring。
# ---------------------------------------------------------------------------


class SubmitDeltaResponse(BaseModel):
    """``POST /projects/{pid}/deltas`` 的 201 响应（实测 3 键恒在）。

    ⚠️ ``status`` 在这条路径上**恒为 ``"validated"``**：``status="rejected"`` 时
    router 会抛 422（``detail.errors``），响应体是错误形状而不是本模型。
    字段仍按实测声明成 ``str`` 而非 Literal——把它钉成字面量等于把「将来
    会不会多一种 201 状态」的判断提前烧掉，而校验器 ``validate_delta`` 的
    返回面不受本模块控制。
    """

    model_config = ConfigDict(extra="allow")

    delta_id: str
    status: str
    errors: list[str] = Field(description="恒在；无错时是 []（不是缺键）")


class CommitResponse(BaseModel):
    """``POST /projects/{pid}/commits`` 的 201 响应（实测 4 键恒在）。

    ⚠️ ``snapshot_ref`` **键恒在、值可为 null**：分支 commit 走
    ``commits.commit_delta`` 的非 main 分支，那条路径不写 ``story_states``，
    ``snapshot_ref`` 恒为 ``None``（实测：分支 commit 返回 ``"snapshot_ref": null``）。
    所以这里**不能**声明成 ``str``，也**不能**给端点加 ``response_model_exclude_none``
    ——任一条都会让前端 ``ref.snapshot_ref`` 从 ``null`` 变 ``undefined``。
    """

    model_config = ConfigDict(extra="allow")

    commit_id: str
    delta_id: str
    state_version: int
    snapshot_ref: Optional[str] = Field(
        default=None,
        description="main commit 才有；分支 commit 恒为 null（键本身恒在）",
    )


class RollbackResponse(CommitResponse):
    """``POST /commits/{cid}/rollback`` 的 201 响应（实测 5 键 = commit + 1）。

    回滚是「生成逆 delta 再走一遍 submit+commit」，所以返回体就是 commit 的返回体
    再加一个 ``rollback_of``（指向被回滚的 commit_id）。⚠️ **实测无 delta 列表**：
    逆 delta 的 7 数组不在这份响应里，只落在 ``state_deltas.payload_json``；
    要读逆 delta 走 ``GET /projects/{pid}/chapters/{cid}/deltas``。
    """

    rollback_of: str = Field(description="被回滚的 commit_id（= 本次 commit 的来源）")


class BranchCreatedResponse(BaseModel):
    """``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。"""

    model_config = ConfigDict(extra="allow")

    branch_id: str
    project_id: str
    name: str
    parent_branch_id: str = Field(description="父分支；建在 main 上时是 main 的 branch_id")
    base_state_version: int
    status: str = Field(description="建成的瞬间恒为 ACTIVE")
    created_at: str


class BranchPromoteResponse(BaseModel):
    """``POST /projects/{pid}/branches/{bid}/promote`` 的 201 响应——**同一端点两个形状**。

    实测（临时库 + 合成夹具）::

        空分支（7 键）    {branch_id, status, promoted_commits, commit_id,
                          state_version, delta_id, replayed_delta_ids}
        非空分支（8 键）  {commit_id, delta_id, state_version, promoted_from,
                          promoted_commits, branch_id, replayed_delta_ids,
                          snapshot_ref}

    两处语义分叉，**前端必须知道**，否则一定读错：

    1. ``branch_id`` 在两个形状里**指的不是同一个东西**：空分支时它就是**被 promote
       的那个分支**；非空时它是 **main 的 branch_id**（被 promote 的分支 id 在
       ``promoted_from``）。实测两者不同值。
    2. ``state_version`` 在两个形状里也不是同一个东西：空分支时是
       ``branches.base_state_version``（**没有产生新版本**），非空时是最后一次重放
       落在 main 上的版本。

    取并集建模，故有三个键在某一形状里**缺席**（``status`` 只在空分支、
    ``promoted_from`` / ``snapshot_ref`` 只在非空分支）——这正是它们带默认值的原因。
    ``commit_id`` / ``delta_id`` 在两个形状里都出现，但空分支时**值为 null**。

    ⚠️ **「缺席」与「值为 null」是两回事，补默认值会把前者变成后者。**
    本模型带默认值 ⇒ 不给 ``response_model_exclude_unset=True`` 的话，Pydantic 会把
    缺席的键补成 ``null``：实测空分支响应从 7 键变成 9 键（凭空多出
    ``promoted_from`` / ``snapshot_ref``）——**这是加 response_model 本身引入的契约变更**。
    ⇒ 端点上带了 ``response_model_exclude_unset=True``（只看「生产方给没给这个键」）。
    ⇒ 但**不能**改用 ``response_model_exclude_none=True``：那会把 ``commit_id`` /
    ``delta_id`` 这两个「键在、值为 null」的键抹掉，把「有键」改成「无键」。

    另：这三个缺席键带默认值 ⇒ OpenAPI 的 ``required`` 里**没有**它们（也就没有
    ``commit_id`` / ``delta_id`` / ``branch_id``）。这是 schema 对「可能缺席」的
    保守声明，与实测并不矛盾——宁可少承诺，不要让前端把可选键当必填。
    """

    model_config = ConfigDict(extra="allow")

    commit_id: Optional[str] = Field(default=None, description="空分支时为 null（键恒在）")
    delta_id: Optional[str] = Field(default=None, description="空分支时为 null（键恒在）")
    state_version: int = Field(description="⚠️ 空分支时是 base_state_version，不是新版本")
    branch_id: Optional[str] = Field(default=None, description="⚠️ 空分支=源分支；非空=main 分支")
    promoted_commits: int
    replayed_delta_ids: list[str] = Field(description="本次在 main 上重放出的新 delta_id 列表")
    status: Optional[str] = Field(default=None, description="只有空分支形状才有（恒为 MERGED）")
    promoted_from: Optional[str] = Field(default=None, description="只有非空分支形状才有：源分支 id")
    snapshot_ref: Optional[str] = Field(default=None, description="只有非空分支形状才有")


class ChangedDiffItemResponse(BaseModel):
    """``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``（实测）。

    ⚠️ ``changes`` **空则整个键省略**（``**({"changes": sub} if sub else {})``），
    所以它是条件字段。⚠️ ``changes`` 的**值**刻意不建模成 ``dict[str, FieldDelta]``：
    ``_diff_dicts`` 是**任意深度递归**的，实测能产出
    ``{"current_state": {"location": {"before": null, "after": "破屋"}}}`` 这种
    嵌套结构——只有最外层是 ``{before, after}``。把它声明成 ``FieldDelta`` 会让
    校验在真实负载上失败（这正是「读源码建模」的典型翻车点）。
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="稳定键：character_id / hook_id / debt_id / …")
    changes: Optional[dict[str, Any]] = Field(default=None, exclude_if=_drop_if_none)


class ListDiffResponse(BaseModel):
    """``_diff_lists`` 的返回：``added`` / ``removed`` / ``changed``，三者都**空则省略**。"""

    model_config = ConfigDict(extra="allow")

    added: Optional[list[dict[str, Any]]] = Field(default=None, exclude_if=_drop_if_none)
    removed: Optional[list[dict[str, Any]]] = Field(default=None, exclude_if=_drop_if_none)
    changed: Optional[list[ChangedDiffItemResponse]] = Field(
        default=None, exclude_if=_drop_if_none
    )


class StateDiffResponse(BaseModel):
    """``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。

    实测：无实体变更时**恰好 3 键**（``version_a`` / ``version_b`` / ``branch_id``），
    有变更时追加六个实体段里的若干个（实测一次同时出现
    ``characters`` / ``hooks`` / ``debts`` / ``events_changed``）。

    - 六个实体段用**字段级** ``exclude_if``（理由见 :func:`_drop_if_none` 的 docstring）
      ——**端点刻意没有** ``response_model_exclude_none=True``，加了会把恒为 null 的
      ``branch_id`` 一起抹掉（实测：空 diff 从 3 键掉到 2 键）。
    - ``events_changed`` 是**按 event_id 键的字典**（``{event_id: {before, after} | 嵌套 diff}``），
      不是列表段，故不是 :class:`ListDiffResponse`。
    - ``branch_id`` **恒为 null**：``queries.diff_versions`` 把它写死成 ``None``，
      传非 None 一律 409（``diff_conflict``）。
    """

    model_config = ConfigDict(extra="allow")

    version_a: int
    version_b: int
    branch_id: Optional[str] = Field(default=None, description="恒为 null——本端点不支持分支视角")
    characters: Optional[ListDiffResponse] = Field(default=None, exclude_if=_drop_if_none)
    world: Optional[dict[str, Any]] = Field(default=None, exclude_if=_drop_if_none)
    hooks: Optional[ListDiffResponse] = Field(default=None, exclude_if=_drop_if_none)
    debts: Optional[ListDiffResponse] = Field(default=None, exclude_if=_drop_if_none)
    events_changed: Optional[dict[str, Any]] = Field(default=None, exclude_if=_drop_if_none)
    recent_events: Optional[ListDiffResponse] = Field(default=None, exclude_if=_drop_if_none)


# ---------------------------------------------------------------------------
# 读面：三个**列表**端点（2026-10-15 批次（六）· L2 组）。
# 键集全部由实测得出，见 tests/api/test_state_list_response_contract.py 顶部实测表。
# ---------------------------------------------------------------------------


class CommitListItem(BaseModel):
    """``GET /projects/{pid}/commits`` 的**行**（实测恒 12 键）。

    ⚠️ **与 :class:`CommitResponse` 不同形，必须另建**：``POST /commits`` 返回的是
    「提交动作的结果摘要」（4 键：``commit_id`` / ``delta_id`` / ``state_version`` /
    ``snapshot_ref``），本端点返回的是**裸 DB 行**——``SELECT * FROM commits`` 加上两个
    JSON 列的解析结果。两者唯一的交集是 ``commit_id`` 与 ``delta_id``，
    ``state_version`` 在这里是**拆成两个列**（``previous_state_version`` /
    ``resulting_state_version``）。**「应该和单体一样」是本轮实测推翻的先验**。

    ⚠️ ``rollback_of`` 恒可空：实测四条行里三条为 ``null``、只有回滚产生的那条是
    字符串；声明成 ``str`` ⇒ 普通 commit 的整条列表 500（**不是**单行失败，是整页）。
    ``validation_json`` / ``author_approval_json`` 同理声明成 ``Optional``——它们经
    ``snapshots._parse_json`` 解析，那函数对空串 / 非法 JSON 返回 ``None`` 而非 ``{}``。
    实测四条行都是 dict，但**键恒在** ⇒ 不给默认值（进了 OpenAPI ``required``）。

    ``extra="allow"``：行来自 ``SELECT *``，迁移给 ``commits`` 加列就会多一个键
    （本表 12 列自 0001 建表起未变，但 ``workflow_runs`` 的 ``instance_id`` 是 0026
    加的同款先例）。代价见模块 docstring。
    """

    model_config = ConfigDict(extra="allow")

    commit_id: str
    project_id: str
    branch_id: str = Field(description="NOT NULL；main commit 与分支 commit 各指向自己的分支")
    chapter_id: str
    previous_state_version: int
    resulting_state_version: int = Field(description="本 commit 产生的版本；回滚 commit 同样占一个版本号")
    delta_id: str
    validation_json: Optional[dict[str, Any]] = Field(description="NOT NULL 列；解析失败时为 null")
    author_approval_json: Optional[dict[str, Any]] = Field(description="NOT NULL 列；解析失败时为 null")
    timestamp: str
    workflow_run_id: str
    rollback_of: Optional[str] = Field(description="只有回滚产生的 commit 非 null；其余恒为 null")


class StateDeltaListItem(BaseModel):
    """``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。

    ⚠️ **行里没有 ``payload_json`` 这个键**——这是本组最值得留痕的一条实测结论
    （任务书的预期形状与实测不符）。DB 列 ``payload_json`` 里装的是 7 个 change 数组的
    JSON，而 ``deltas.restore_delta_from_row`` 把它们**摊到顶层**，再拼上 10 个元信息字段
    与 ``status``：10 + 7 + 1 = 18。

    ⚠️ **7 个数组刻意不逐字段建模**：``docs/state-model/schemas/state-delta.schema.json``
    里每个数组的元素都是具名 ``$ref``（``character_change`` / ``new_hook`` / …），delta
    schema 会演进；在这里复制一份逐字段模型等于造出一个**必然过期**的假契约。
    故只声明到 ``list[dict[str, Any]]``——前端拿到「数组 of 对象」这一层就够写渲染，
    要细节时另开 delta 端点。**同理不要把它们合并成 ``dict[str, Any]``**：实测它们的
    类型是 ``list``（`restore_delta_from_row` 的 ``payload.get(k, [])``），不是 dict。

    ⚠️ ``notes`` **恒为 null**：``restore_delta_from_row`` 里写死 ``"notes": None``
    （``notes`` 不进 payload_json）。声明成 ``Optional[str]`` 而非删掉——键恒在。
    ``supersedes`` 才是真正可空的可变列（重试指向旧 delta_id）。

    ⚠️ **残余边界（如实登记）**：``list[dict[str, Any]]`` 的元素类型来自 delta schema 的
    ``items.$ref``，而**写库路径**（``submit_delta`` → ``validate_delta``）会保证它是
    对象；但**绕过 API 直写 DB / 备份导入**的行没有这层保证，那种行会让本页 500。
    收紧到 ``list[Any]`` 能免掉这个风险，代价是丢掉全部类型信息——本仓按前者取舍，
    并在此登记边界，而不是假装它不存在。
    """

    model_config = ConfigDict(extra="allow")

    delta_id: str
    chapter_id: str
    workflow_run_id: str
    previous_state_version: int
    delta_version: int = Field(description="schema 里 const=1；保留 int 而非 Literal 以免过紧")
    schema_version: str
    created_by: str
    created_at: str
    supersedes: Optional[str] = Field(description="重试时指向被取代的 delta_id；普通 delta 为 null")
    notes: Optional[str] = Field(description="恒为 null（restore_delta_from_row 写死）")
    status: str = Field(description="proposed / validated / applied / rejected / superseded")
    character_changes: list[dict[str, Any]]
    world_changes: list[dict[str, Any]]
    relationship_changes: list[dict[str, Any]]
    new_events: list[dict[str, Any]]
    resolved_hooks: list[dict[str, Any]]
    new_hooks: list[dict[str, Any]]
    debt_changes: list[dict[str, Any]]


class BranchSummary(BaseModel):
    """``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。

    ⚠️ **键集与 :class:`BranchCreatedResponse` 逐字相同，但不能复用**——类型不同：
    列表恒含 ``main`` 分支，而 main 的 ``parent_branch_id`` 是 **null**（根节点无父）；
    ``POST /branches`` 只会新建**子**分支，``BranchCreatedResponse`` 声明的是
    ``parent_branch_id: str``（必填非空）。实测：列表第一行（main）该键为 ``None``，
    第二行（子分支）为字符串。**复用会让这条列表端点恒 500。**
    这正是「键集相同 ⇒ 可复用」的先验被实测推翻的第二处。

    ``extra="allow"``：行来自 ``SELECT * FROM branches``，加列即多一键。
    """

    model_config = ConfigDict(extra="allow")

    branch_id: str
    project_id: str
    name: str
    parent_branch_id: Optional[str] = Field(description="⚠️ main 分支恒为 null（根节点）；子分支指向父分支")
    base_state_version: int
    status: str = Field(description="ACTIVE / MERGED / DISCARDED；列表里恒含 main（恒 ACTIVE）")
    created_at: str


__all__ = [
    "BranchCreatedResponse",
    "BranchPromoteResponse",
    "BranchSummary",
    "ChangedDiffItemResponse",
    "CommitListItem",
    "CommitResponse",
    "ListDiffResponse",
    "RollbackResponse",
    "SnapshotResponse",
    "StateDeltaListItem",
    "StateDiffResponse",
    "SubmitDeltaResponse",
]
