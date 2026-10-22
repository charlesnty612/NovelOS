r"""质量域「rule_id 总账」的突变规格（2026-10-22 批次二十一 · novelos-dev-a）。

补的缺口
--------
``tests/unit/quality/test_quality_rule_id_ledger.py`` 刚建立（批次二十一），它把
「质量域一共有哪些 rule_id」从**没人说得清**变成**一个必须显式更新才能通过的数字**
（当前 **N = 50** = 注册表内 26 ＋ 注册表外硬编码 24）。
按方法论要求，**新测试必须做突变验证**——否则「这份测试到底能不能判别」无人知道。

两条突变打**两个不同的失效方向**
--------------------------------
* **QLEDGER-1**（``payoff.py``）——把一条 rule_id **改名**
  （``RULE_H1_NO_END_HOOK`` → ``RULE_H1_NO_END_HOOK_RENAMED``）。
  ⇒ 实跑红在 ``test_rule_id_ledger_is_exact``：旧 id 从总账消失、新 id 出现
  ⇒ 集合不再相等。
  ⚠️ 诚实说明：**签发点断言不红**——改名后那条 issue 仍被签发（只是 id 变了），
  所以「有签发点」这条不变 ⇒ 本条的判别点**只有集合相等那一条**。

* **QLEDGER-2**（``scoring.py``）——把 ``_CONTINUITY_DEDUCTIONS`` 里的一个**键**
  改名（``RULE_CHAR_BEFORE_MISMATCH`` → ``..._V2``）。
  ⇒ 实跑**也红在** ``test_rule_id_ledger_is_exact``，**不是**文件成员关系那条：
  原因是改名后的新 id ``RULE_CHAR_BEFORE_MISMATCH_V2`` **本身也符合 rule_id 形态**
  ⇒ 它成了一个**新** rule_id，而旧 id 仍留在 ``guardrails.py`` ⇒ 全集由 50 变 51
  ⇒ 集合不等。
  ⇒ 「文件成员关系」那条断言是**第二道防线**：它抓的是「某个 id 从某文件消失、
  但仍存在于其它文件 ⇒ 集合不变、成员关系变」的情形（本条被集合相等**先**抓到，
  不是它抓的）。

两条都是「**破坏结构但不破坏 import**」：改的只是**字符串字面量**，模块仍可正常
import ⇒ 跑测时不会变成收集期 ImportError（**假捕获**：红的原因必须是结构断言，
不是模块 import 不进来）。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① rule_id 被改名 ⇒ 总账集合不再相等（+ 无签发点）
    # ------------------------------------------------------------------
    (
        "QLEDGER-1-rule-id-renamed-out-of-ledger",
        "packages/core/quality/payoff.py",
        '                rule_id="RULE_H1_NO_END_HOOK",',
        '                rule_id="RULE_H1_NO_END_HOOK_RENAMED",',
        "tests/unit/quality/test_quality_rule_id_ledger.py",
    ),
    # ------------------------------------------------------------------
    # ② rule_id 被挪走（所在文件成员关系变化，集合不变）
    # ------------------------------------------------------------------
    (
        "QLEDGER-2-rule-id-moved-between-files",
        "packages/core/quality/scoring.py",
        '    "RULE_CHAR_BEFORE_MISMATCH": 5.0,',
        '    "RULE_CHAR_BEFORE_MISMATCH_V2": 5.0,',
        "tests/unit/quality/test_quality_rule_id_ledger.py",
    ),
]
