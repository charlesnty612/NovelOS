"""``chapter_commit`` 门禁节点的突变规格（2026-10-21 · novelos-dev-a）。

补的缺口
--------
``packages/workflows/chapter_commit/`` 此前**突变覆盖 0/7**——含质量门禁节点
``gate.py``。本文件补其中**判定链**上的两处：

* ``C-CONFIRM-1``：confirm 命中在 ``enforce`` 模式下是否真的会阻断；
* ``C-OVERRIDE-1``：``gate_override`` 的「集合包含 + reason 非空」校验是否真的有效。

为什么这两处值得钉死
--------------------
门禁的语义是「**不许在不知道批准什么的情况下批准**」（P0-1）：
confirm 命中必须由作者显式接受才放行，而「显式接受」必须**逐条覆盖本次实际命中的
confirm rule_id 并写明理由**。两处任何一处被撤掉，后果都是**静默降级**：

* ``C-CONFIRM-1`` 撤掉 ⇒ confirm 形同不存在（回到 P0-1 事故形态：9 段逐字重复、
  ``overall: 90``、照常提交）；
* ``C-OVERRIDE-1`` 撤掉 ⇒ 声明「只批准了一条」也能放行**全部**命中 ⇒ 作者在
  不知道还命中了什么的情况下批准了全部（且审计面照样记「已接受」）。

**实测结果（本文件每条都手工改源码 → 跑定向用例 → 看红 → 还原）**
------------------------------------------------------------------
| id | 目标 | 锚点唯一命中 | 手工实测 | 红在哪个用例 |
|---|---|---|---|---|
| C-CONFIRM-1 | ``packages/workflows/chapter_commit/gate.py:521`` | 1 | **红** | ``tests/api/test_gate_confirm_e2e.py::test_confirm_gate_blocks_then_passes_with_recorded_override`` |
| C-OVERRIDE-1 | ``packages/workflows/chapter_commit/gate.py:159`` | 1 | **红** | ``tests/api/test_gate_confirm_e2e.py::test_confirm_gate_partial_override_via_http_still_blocks`` |

实测方式 = ``tests/mutation_specs/run_spec.py``（``copy2`` 备份 → 改写 → 跑定向用例
→ ``finally`` 还原 → SHA256 核对）。两条全部 ``[CAUGHT]``，**无存活、无中止**；
``gate.py`` 还原后 SHA256 与跑前逐位一致，无 ``.mutbak`` 残留。

⚠️ **刻意未覆盖的方向**：本文件只钉「confirm 阻断」与「override 校验」两处；
``gate.py`` 里的另外几条分支（``mode == "report"`` 的关闸门语义、
``gate_blocked`` 落库与清除、高风险审批门）不在本文件范围内——
它们各有自己的用例文件（``tests/api/test_fast_mode_review_gate.py`` 等），
本文件**不声称**覆盖它们。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① confirm 命中的阻断判定：``enforce`` 模式下必须拦
    # 撤掉它 ⇒ confirm 永不阻断 ⇒ P0-1 的「必须显式接受才放行」形同虚设。
    # ------------------------------------------------------------------
    (
        "C-CONFIRM-1",
        "packages/workflows/chapter_commit/gate.py",
        "    confirm_unresolved = bool(gate_summary.confirm_issues) and accepted_override is None",
        "    confirm_unresolved = False  # MUT: confirm 永不阻断",
        "tests/api/test_gate_confirm_e2e.py",
    ),
    # ------------------------------------------------------------------
    # ② gate_override 的**集合包含**校验：必须覆盖本次全部 confirm rule_id
    # 撤掉它 ⇒ 部分覆盖也算覆盖 ⇒ 作者只声明一条就放行了全部命中。
    # ------------------------------------------------------------------
    (
        "C-OVERRIDE-1",
        "packages/workflows/chapter_commit/gate.py",
        "    missing = [rid for rid in confirm_rule_ids if rid not in set(rule_ids)]",
        "    missing = []  # MUT: 集合包含校验改宽（部分覆盖也算覆盖）",
        "tests/api/test_gate_confirm_e2e.py",
    ),
]
