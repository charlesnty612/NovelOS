"""`diff_snapshots` 的两份出参模型必须同形（2026-10-15 批次（六））。

背景
----
`diff_snapshots` 只有**一个实现**，却有**两份**出参模型：

* ``core/simulation/models.py::StateDiff`` —— ``POST /simulate`` 与
  ``GET /simulations/{id}`` 的响应（批次五，2026-10-15）；
* ``core/story_state/models.py::StateDiffResponse`` —— ``GET .../state/diff``
  的响应（批次六 G3，2026-10-15）。

两份不是「恰好一样」，而是**必须一样**：它们描述的是同一个函数的同一个返回。
任一份漂移，两个端点就会对同一份数据给出不同形状，而各自的功能测试都可能照旧全绿。

⚠️ 两份的「空则省略」机制**故意不同**，别把它们当成同一套东西
---------------------------------------------------------
生产端是空则省略（``_diff_lists`` / ``_diff_dicts`` 只写有差异的段），两个端点
都要把「无差异」表达成「键不存在」。但两边的实现路径相反：

* simulation：**模型**照常声明全部键，靠**端点级** ``response_model_exclude_none=True``
  把 None 抹掉（批次五；顺带被证实**不能**用于 story_state，见下）；
* story_state：**模型侧**用字段级 ``exclude_if``，端点**刻意不加**那个开关——
  因为端点级是一刀切，会把「键恒在、值为 null」的 ``branch_id`` 一起抹掉，
  实测空 diff 会从 3 键掉到 2 键（既有契约的静默变更）。

⇒ **不要断言两份模型的 ``model_dump()`` 相等**：simulation 那份在模型层根本不会
省略键（它的省略发生在端点），比出来的差异是机制差、不是契约差。
本文件断言的是**字段同形**与**生产端覆盖**，那才是真正的漂移面。

⚠️ 为什么没有直接合并成一份
--------------------------
``simulation`` 已经 import ``packages.core.story_state``（无环，已实测），
技术上确实可以只留一份。本批**不合并**的理由是：批次（五）的 simulation 模型刚做完
21/21 突变验证，尾部再动它要重跑那批闸门，收益（同形）小于风险（扰动已验证代码）。
先用本守卫把「漂移立即可见」钉住，合并另立项。

判别：为什么基准取「生产端实测」而不是模型之间互比
--------------------------------------------------
只让两份模型互比是**自指**：两边一起漂移时仍然「相等」——正是本仓反复登记的
「断言里只有被测物和它自己 ⇒ 恒真」。本文件另取 ``diff_snapshots`` 的真实输出
作第三方基准（它不经过任何模型），漂移立刻现形。
"""

from __future__ import annotations

from packages.core.simulation.models import (
    ChangedItem as SimulationChangedItem,
)
from packages.core.simulation.models import (
    ListDiff as SimulationListDiff,
)
from packages.core.simulation.models import (
    StateDiff as SimulationStateDiff,
)
from packages.core.story_state.models import (
    ChangedDiffItemResponse,
    ListDiffResponse,
    StateDiffResponse,
)
from packages.core.story_state.snapshots import diff_snapshots


def _raw_diff() -> dict:
    """调生产端拿**未经任何模型**的真实返回（非空差异）。"""
    a = {
        "characters": [{"character_id": "c1", "name": "旧名"}],
        "hooks": [],
        "debts": [],
        "events": {"e1": {"t": "x"}},
        "recent_events": [],
        "world": {"locations": {"l1": {"name": "客栈"}}},
    }
    b = {
        "characters": [
            {"character_id": "c1", "name": "新名"},
            {"character_id": "c2", "name": "新角色"},
        ],
        "hooks": [{"hook_id": "h1", "status": "RESOLVED"}],
        "debts": [],
        "events": {"e1": {"t": "y"}},
        "recent_events": [],
        "world": {"locations": {"l1": {"name": "破屋"}}},
    }
    return diff_snapshots(a, b, version_a=1, version_b=2, branch_id="br_x")


def _raw_empty_diff() -> dict:
    """无实体变更 ⇒ 生产端只吐三键。"""
    empty = {
        "characters": [],
        "hooks": [],
        "debts": [],
        "events": {},
        "recent_events": [],
        "world": {},
    }
    return diff_snapshots(empty, dict(empty), version_a=1, version_b=2, branch_id=None)


def test_both_models_declare_exactly_the_same_keys():
    """两份模型声明的字段集必须逐字相同（漂移的最直接信号）。"""
    assert set(SimulationStateDiff.model_fields) == set(StateDiffResponse.model_fields), (
        "两份 StateDiff 模型已分叉——它们描述的是同一个 diff_snapshots 输出。"
        f"仅 simulation={sorted(set(SimulationStateDiff.model_fields) - set(StateDiffResponse.model_fields))}；"
        f"仅 story_state={sorted(set(StateDiffResponse.model_fields) - set(SimulationStateDiff.model_fields))}"
    )
    assert set(SimulationListDiff.model_fields) == set(ListDiffResponse.model_fields), (
        "两份 ListDiff 模型已分叉："
        f"仅 simulation={sorted(set(SimulationListDiff.model_fields) - set(ListDiffResponse.model_fields))}；"
        f"仅 story_state={sorted(set(ListDiffResponse.model_fields) - set(SimulationListDiff.model_fields))}"
    )
    assert set(SimulationChangedItem.model_fields) == set(
        ChangedDiffItemResponse.model_fields
    ), "两份 changed 元素模型已分叉"


def test_both_models_cover_every_key_the_producer_actually_emits():
    """两份模型都必须**覆盖**生产端真会吐出的每一个键。

    反向不查：模型多声明几个键是**必需的**（生产端空则省略，键只在有差异时出现，
    模型不声明就会被 ``response_model`` 静默裁掉——正是本仓的核心坑）。
    """
    raw = _raw_diff()
    sim_fields = set(SimulationStateDiff.model_fields)
    ss_fields = set(StateDiffResponse.model_fields)

    missing_from_sim = set(raw) - sim_fields
    assert not missing_from_sim, (
        f"生产端吐出了 simulation.StateDiff 未声明的键 {sorted(missing_from_sim)}"
        "⇒ 该键会被 response_model 静默裁掉"
    )
    missing_from_ss = set(raw) - ss_fields
    assert not missing_from_ss, (
        f"生产端吐出了 story_state.StateDiffResponse 未声明的键 {sorted(missing_from_ss)}"
        "⇒ 该键会被 response_model 静默裁掉"
    )


def test_nested_models_cover_the_producer_sub_structures():
    """嵌套层同样要覆盖：列表段子键与 changed 元素键。"""
    raw = _raw_diff()
    chars = raw["characters"]
    sim_list = set(SimulationListDiff.model_fields)
    ss_list = set(ListDiffResponse.model_fields)

    assert not (set(chars) - sim_list), (
        f"characters 段吐出了 simulation.ListDiff 未声明的子键 {sorted(set(chars) - sim_list)}"
    )
    assert not (set(chars) - ss_list), (
        f"characters 段吐出了 story_state.ListDiffResponse 未声明的子键 {sorted(set(chars) - ss_list)}"
    )

    element = chars["changed"][0]
    sim_item = set(SimulationChangedItem.model_fields)
    ss_item = set(ChangedDiffItemResponse.model_fields)
    assert not (set(element) - sim_item), (
        f"changed 元素吐出了 simulation.ChangedItem 未声明的键 {sorted(set(element) - sim_item)}"
    )
    assert not (set(element) - ss_item), (
        f"changed 元素吐出了 story_state.ChangedDiffItemResponse 未声明的键 {sorted(set(element) - ss_item)}"
    )


def test_empty_diff_has_exactly_three_keys_including_a_null_branch_id():
    """无差异时生产端只吐三键，且 ``branch_id`` **键在、值为 null**。

    这三键就是两个端点都刻意**不加** ``response_model_exclude_none`` 的理由
    （端点级开关会把 ``branch_id`` 一起抹掉，3 键塌成 2 键＝既有契约静默变更）。
    另注：simulation 端点**确实**加了那个开关、且实测正确——因为它的响应里
    没有「键在值为 null」这种形状；story_state 有，所以不能加。
    """
    raw = _raw_empty_diff()
    assert set(raw) == {"version_a", "version_b", "branch_id"}, sorted(raw)
    assert raw["branch_id"] is None, "branch_id 应为 null 而非缺席"
