"""G9「token 硬顶接线告警」批次的突变规格（2026-10-15 批次六，独占文件）。

被测接线
--------
``packages/core/context_engine/builders_common/common.py::_attach_assembly_meta``
在 ``token_budget_exceeded`` 为真时发一条 ``WARNING`` 日志
（标识 :data:`_ASSEMBLY_BUDGET_WARN_TAG`）。接线前该标记的**全仓唯一读者是它自己的
单测**——「数据源存在 ≠ 已进装配」的典型：字段算了、落进 payload 了，但运行时对
作者与运维完全不可见。本文件验的是**消费侧**被改坏时用例会不会红。

四条覆盖两个方向，缺一不可（AGENTS.md 坑区「夹具永远不触发闸门 ⇒ 空断言」要求
闸门断言配反向用例）：

* 方向 A（消费必须发）：M1 摘掉消费 / M2 判据恒假 / M4 告警数字写死；
* 方向 B（消费不得滥发）：M3 把「只在超预算时发」改成恒发 —— 只做 A 组的话，
  一条「每次装配都 warning」的噪声接线同样能骗过全部用例（真信号被淹没，见坑区
  「把正常写法与真信号混在一张『命中即报』表里 = 噪声淹没信号」）。

执行（**会临时改源码**，禁与任何 pytest 并发）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g9_token_budget.py

锚点纪律：每条 anchor 在目标文件里**恰好命中 1 次**（已 grep 自检），且每条
replacement 都**真的改变行为**——不是「只插了行注释、逻辑未变」那种脚本报存活
而真因是变异写错了的形态（坑区「断言里只有被测物和它自己 ⇒ 自指断言恒真」第 3 点）。
"""

from __future__ import annotations

_SRC = "packages/core/context_engine/builders_common/common.py"
_TEST = "tests/unit/test_context_token_budget.py"

MUTATIONS = [
    # M1 —— 消费被整体摘掉：告警永不发。
    # 预期红：test_oversized_assembly_emits_budget_warning_with_real_numbers
    #        （len(messages) == 1 → 0）、test_budget_warning_path_does_not_alter_assembly
    #        （前提断言「告警真的发了」红）、test_director_oversized_assembly_warns_with_agent_name。
    # 注意 payload 里的 token_budget_exceeded 仍为 True —— 证明「只断言 meta 有那个键」
    # 的用例根本抓不到本条（那正是本批次要修的缺陷形状）。
    (
        "G9-M1-consumers-removed",
        _SRC,
        'if meta["token_budget_exceeded"]:',
        'if False:',
        _TEST,
    ),
    # M2 —— 判据恒假：分支条件挂上一个恒不成立的合取项（模拟「预算值算错 / 判据写反」）。
    # 与 M1 的区别：M1 是消费没了，M2 是消费在、判据永不成立。两者预期红的用例集合相同，
    # 但守的是不同的失效方向（hook 被摘 vs 条件写错）。
    (
        "G9-M2-criterion-always-false",
        _SRC,
        'if meta["token_budget_exceeded"]:',
        'if meta["token_budget_exceeded"] and _ASSEMBLY_TOKEN_BUDGET < 0:',
        _TEST,
    ),
    # M3 —— 反向：改成恒发。正常体量装配也刷 warning，真信号被噪声淹没。
    # 预期红：test_small_assembly_emits_no_budget_warning、test_writer_small_assembly_stays_silent。
    # 这组是 M1~M2 完全覆盖不到的方向（「发得太多」与「发得太少」在断言上不对称）。
    (
        "G9-M3-always-warns",
        _SRC,
        'if meta["token_budget_exceeded"]:',
        'if True:',
        _TEST,
    ),
    # M4 —— 告警照发，但里面的数字写死成预算值。
    # 只断言「有一条含标识的日志」的话，这条照样全绿 —— 它正是「量具没量到目标却报告
    # 量过了」的形状。预期红：两条要求 estimate_tokens 为**本次实测值**的用例。
    (
        "G9-M4-warning-number-hardcoded",
        _SRC,
        '            payload.get("agent") or "?",\n            tokens,\n',
        '            payload.get("agent") or "?",\n            _ASSEMBLY_TOKEN_BUDGET,\n',
        _TEST,
    ),
]
