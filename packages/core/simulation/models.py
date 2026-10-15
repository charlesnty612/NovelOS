"""Pydantic 模型：What-if 推演（``/simulate`` 与 ``/simulations``）。

背景同 :mod:`packages.core.arc.models`——``SimulationService`` 此前返回裸 dict、
router 无 ``response_model``，前端手写契约且无从校验。

本模块最需要小心的是 ``StateDiff``：**空段落是被省略的，不是空对象**
-------------------------------------------------------------------------
``story_state/snapshots.py::diff_snapshots`` 逐段判断「有差异才写入」：

    out = {"version_a": ..., "version_b": ..., "branch_id": ...}   # 恒在
    if chars_diff: out["characters"] = chars_diff                  # 空则整个键不写
    ... 同 world / hooks / debts / events_changed / recent_events

所以六个实体段声明为 ``X | None = None``，**并且两个 GET 端点必须带**
``response_model_exclude_none=True``——否则 FastAPI 会把 ``None`` 序列化成
``"characters": null``，**凭空多出六个键**，把「空则省略」这个既有契约改掉
（实测：无实体变更的推演，其 diff 只有 3 个键）。
``tests/api/test_simulation_response_contract.py`` 有专门用例钉住这一点。

其他形状同样**量出来而非读出来**（临时库 + 合成夹具跑通端点后 dump 键集）：

- ``SimulationResult`` 9 键恒在；``SimulationSummary`` 6 键恒在；
- ``issues[]`` 的元素是 ``{index, errors[]}``。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class FieldDelta(BaseModel):
    """``_diff_dicts`` 的叶子：``{"before": …, "after": …}``。"""

    model_config = ConfigDict(extra="allow")

    before: Any = None
    after: Any = None


class ChangedItem(BaseModel):
    """``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``。

    ⚠️ ``changes`` 同样**空则省略**（见 ``_diff_lists`` 的
    ``**({"changes": sub} if sub else {})``）。
    """

    model_config = ConfigDict(extra="allow")

    id: str
    changes: Optional[dict[str, Any]] = None


class ListDiff(BaseModel):
    """列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。"""

    model_config = ConfigDict(extra="allow")

    added: Optional[list[dict[str, Any]]] = None
    removed: Optional[list[dict[str, Any]]] = None
    changed: Optional[list[ChangedItem]] = None


class StateDiff(BaseModel):
    """两个快照的结构化差异。

    ⚠️ 六个实体段**空则整个键被省略**（见模块 docstring）——读法必须是
    ``diff.hooks?.added?.length ?? 0``，不能假设存在。
    """

    model_config = ConfigDict(extra="allow")

    version_a: int
    version_b: int
    branch_id: Optional[str] = None
    characters: Optional[ListDiff] = None
    world: Optional[dict[str, Any]] = None
    hooks: Optional[ListDiff] = None
    debts: Optional[ListDiff] = None
    events_changed: Optional[dict[str, Any]] = None
    recent_events: Optional[ListDiff] = None


class SimulationIssue(BaseModel):
    """被拒的 delta（``issues[index]``）。"""

    index: int
    errors: list[str]


class SimulationResult(BaseModel):
    """``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。"""

    model_config = ConfigDict(extra="allow")

    simulation_id: str = Field(description="与 branch_id 同值")
    branch_id: str
    name: str = Field(
        description="⚠️ **必须以 `sim-` 开头**，否则该次推演在 "
        "`GET /simulations` 与 `GET /simulations/{id}` 里**查不到**"
        "（两者都按 `name LIKE 'sim-%'` 过滤，实测自定义名会 404）",
    )
    base_version: int
    applied: int = Field(description="已成功应用的 delta 条数；失败时是部分应用数")
    diff: StateDiff
    # 恒在（实测无 issues 时是 `[]`）⇒ 必填。
    issues: list[SimulationIssue]
    branch_status: str = Field(description="恒为 ARCHIVED（推演结束即归档）")
    created_at: str


class SimulationSummary(BaseModel):
    """``GET /simulations`` 的一行（按 created_at DESC）。"""

    model_config = ConfigDict(extra="allow")

    simulation_id: str
    branch_id: str
    name: str
    base_version: int
    status: str
    created_at: str


__all__ = [
    "ChangedItem",
    "FieldDelta",
    "ListDiff",
    "SimulationIssue",
    "SimulationResult",
    "SimulationSummary",
    "StateDiff",
]
