"""两个 ``*_calibrate.py`` 脚本的**纯统计函数**（dev-b 批次二十）。

为什么测它们
------------
``scripts/delta_anchoring_calibrate.py`` 与 ``scripts/revise_fidelity_calibrate.py``
都是**只读**校准器（``mode=ro`` 打开 SQLite，**不写库**），且都长期零测试
（2026-10-22 批次十九实测）。它们要语料才能跑端到端，**但分位数算法本身是纯函数**
⇒ 可以在**没有语料**的情况下钉住（CI 不需要语料，也不许 skip）。

⚠️ **两个脚本用的是两套不同的分位数口径**（本文件把**各自**的口径钉死，不合并）：

- ``delta_anchoring_calibrate._quantile`` = **线性插值**（docstring 原文）
  ⇒ ``q=0.5`` 于 ``[1,2,3,4]`` 得 ``2.5``；
- ``revise_fidelity_calibrate._percentile`` = **按索引取**（``int(q*n)``，无 docstring）
  ⇒ ``q=0.5`` 于 ``[1,2,3,4]`` 得 ``3``。

这是**已存在的口径差异**（两者量的是不同的东西：delta bigram 覆盖率 vs revise
保真比），**不是缺陷**；本文件的目的是让**任一口径被动过都会响**，从而逼出一次
有意识的决定，而不是让它悄悄漂移。⚠️ 其中 ``_percentile`` **没有 docstring**，
其口径只能从代码读出来 ⇒ 已作为「待裁决项」报给 Lead（补一行 docstring 即可，
**本轮不改 scripts/**）。

零真实 LLM、零内容仓接触、零 dev 库、**零不可逆操作**：不碰任何数据库。
"""

from __future__ import annotations

import math

from scripts.delta_anchoring_calibrate import _quantile
from scripts.revise_fidelity_calibrate import _percentile

# ---------------------------------------------------------------------------
# delta_anchoring_calibrate._quantile —— 线性插值分位数
# ---------------------------------------------------------------------------


def test_quantile_is_linear_interpolation():
    """**线性插值**口径：``q=0.5`` 于 ``[1,2,3,4]`` ⇒ ``2.5``（不是 2 或 3）。"""
    vals = [1.0, 2.0, 3.0, 4.0]
    assert _quantile(vals, 0.0) == 1.0
    assert _quantile(vals, 1.0) == 4.0
    assert _quantile(vals, 0.5) == 2.5
    # 1/3 处落在第 2 与第 3 之间 ⇒ 插值
    assert math.isclose(_quantile(vals, 1.0 / 3.0), 2.0)


def test_quantile_handles_empty_and_single_element():
    """空列表 ⇒ ``NaN``（**不是** 0.0——0.0 会被误读成「覆盖率 0」）；
    单元素 ⇒ 就是它自己。"""
    assert math.isnan(_quantile([], 0.5))
    assert _quantile([7.0], 0.5) == 7.0
    assert math.isnan(_quantile([], 0.0))


def test_quantile_requires_sorted_input():
    """入参是**已排序**序列（docstring 的参数名就写着 ``sorted_vals``）⇒
    传未排序列表时结果不保证正确——本用例钉住「输入未排序时结果与排序后不同」
    这一**已知前提**，提醒调用方必须先排序。"""
    unsorted = [3.0, 1.0, 2.0]
    assert _quantile(unsorted, 0.5) != _quantile(sorted(unsorted), 0.5)


# ---------------------------------------------------------------------------
# revise_fidelity_calibrate._percentile —— 按索引取（另一套口径）
# ---------------------------------------------------------------------------


def test_percentile_picks_by_index_not_by_interpolation():
    """**按索引取**口径：``q=0.5`` 于 ``[1,2,3,4]`` ⇒ ``3``（**不是** 2.5）。

    ⚠️ 这与 ``delta_anchoring_calibrate._quantile`` **刻意不同** ⇒ 两个脚本的
    「p50」**不可直接互相比较**。
    """
    vals = [1.0, 2.0, 3.0, 4.0]
    assert _percentile(vals, 0.0) == 1.0
    assert _percentile(vals, 1.0) == 4.0
    assert _percentile(vals, 0.5) == 3.0
    assert _percentile(vals, 0.25) == 2.0


def test_percentile_handles_empty_and_single_element():
    """空列表 ⇒ ``NaN``；单元素 ⇒ 就是它自己。"""
    assert math.isnan(_percentile([], 0.5))
    assert _percentile([7.0], 0.9) == 7.0


def test_percentile_clamps_to_the_last_element():
    """``q=1.0`` 且 n 较大时索引被夹到最后一项（不得 IndexError）。"""
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 1.0) == 5.0
    assert _percentile(vals, 0.999) == 5.0
