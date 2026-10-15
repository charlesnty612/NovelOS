"""Pydantic 模型：叙事弧光聚合视图（``GET /projects/{pid}/arc``）。

为什么需要这个模块
------------------
``packages/core/arc/service.py::build_arc_view`` 现场组装五表汇总并返回**裸 dict**，
router 此前也没有 ``response_model`` ⇒ OpenAPI 里没有它的 schema ⇒ 前端只能在
``apps/web/src/api/types.ts`` 手写一个契约（``ArcView`` 等），而那份手写契约
**没有任何机器校验**——后端加一个键、删一个键，前端要等到运行时才发现。

字段形状**不是照源码读出来的，是量出来的**
------------------------------------------
``response_model`` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而测试可能
全绿、功能坏掉。所以建模前先用临时库 + 合成夹具跑通端点、把实际返回的键集 dump
出来（见 ``tests/api/test_arc_response_contract.py`` 的 docstring 记录方法）。
实测结论（2026-10-15）：

- 顶层 9 键**恒在**：``project_id / generated_at / chapters / payoff / hooks /
  debts / reveal_policies / volumes / alerts``；
- ``chapters[]`` 9 键恒在，其中 ``volume_id / volume_number / overall / pacing /
  prose_chars`` 在无数据时是 **``null`` 而非省略**（因此这几个字段声明为
  ``X | None`` 且**不给默认值**——给了默认值就变成「可省略」，与实测不符）；
- ``alerts[].level`` 只有 ``warn`` / ``fail`` 两档。

⚠️ ``chapters[]`` 里**没有 ``chapter_id``**（service 显式 ``pop`` 掉，见
``build_arc_view`` 的「输出时把 chapter_id 内部字段脱敏」注释）。这是**后端刻意的**
（不对外暴露内部主键），UI 不得拿章号或标题当跳转目标。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ArcChapterRow(BaseModel):
    """弧光视图里的一行章节。

    ⚠️ **刻意不含 ``chapter_id``**：后端主动脱敏（见模块 docstring）。
    """

    number: int
    title: str
    has_payoff_beat: bool = Field(
        description="由 plan_json.key_beats[].purpose 里的 [payoff] 标记推断，非作者声明的结构",
    )
    charge_beat: bool = Field(
        description="由 plan_json.key_beats[].purpose 里的 [charge] 标记推断",
    )
    # 以下五个在无数据时后端返回 **null 而非省略**（实测），故必填可空。
    # ⚠️ 不给 default：给了就生成成可选（`overall?:`），而后端从不省略它 ——
    # 前端就得写 `?? 0` 兜底，那正是 G10「欠定义类型」的病。
    overall: Optional[int]
    pacing: Optional[int]
    prose_chars: Optional[int] = Field(
        description="最新草稿的去空白字符数（visible_chars 口径）；无草稿为 null",
    )
    volume_id: Optional[str]
    volume_number: Optional[int]


class ArcPayoff(BaseModel):
    """回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。"""

    total: int
    charge_streak: int = Field(description="尾部连续蓄力（charge）章数")
    max_charge_streak: int
    last_payoff_chapter: Optional[int]


class ArcHookCounts(BaseModel):
    open: int
    resolved: int
    overdue: int = Field(description="计划回收章号 < 当前最大章号的伏笔数")


class ArcDebtCounts(BaseModel):
    open: int
    paid: int


class ArcOverdueReveal(BaseModel):
    """逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。"""

    policy_id: str
    target_kind: str
    target_id: str
    reveal_by_chapter: Optional[int]
    audience: str


class ArcRevealCounts(BaseModel):
    planned: int
    revealed: int
    cancelled: int
    overdue: list[ArcOverdueReveal]


class ArcVolumeRow(BaseModel):
    volume_id: str
    number: int
    title: Optional[str]
    status: str = Field(description="卷状态；后端列是 TEXT，未知值原样透出")
    chapter_count: int


class ArcAlert(BaseModel):
    """节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded），
    ``'warn'`` 是体检项。"""

    level: Literal["warn", "fail"]
    code: str
    message: str


class ArcView(BaseModel):
    """``GET /projects/{pid}/arc`` 的响应。

    ``extra="allow"``：这是只读聚合视图，service 可能随新告警种类加顶层键；
    契约描述**已知的 9 个**，不宣称封闭。
    """

    model_config = ConfigDict(extra="allow")

    project_id: str
    generated_at: str
    # 三个列表实测**恒在**（空项目是 `[]` 而非省略），故必填。
    chapters: list[ArcChapterRow]
    payoff: ArcPayoff
    hooks: ArcHookCounts
    debts: ArcDebtCounts
    reveal_policies: ArcRevealCounts
    volumes: list[ArcVolumeRow]
    alerts: list[ArcAlert]


__all__ = [
    "ArcAlert",
    "ArcChapterRow",
    "ArcDebtCounts",
    "ArcHookCounts",
    "ArcOverdueReveal",
    "ArcPayoff",
    "ArcRevealCounts",
    "ArcView",
    "ArcVolumeRow",
]
