"""skill 检查项的**后果隔离**：撞上后果表 / 落进 errors ⇒ 必红（0032）。

两条风险
--------
* **R3（F-19 红线）**：skill 检查若进「阻断 / 确认 / 可改稿 / 可修订」四张后果表，
  就等于把一条**可优化指标**接进自动改稿回路 ⇒ 模型为规避检查而回避措辞
  （「对话占比」事故的同形）。
* **R4**：skill 检查若落进 ``review_report["errors"]``，它就成了 ``decide_repair``
  的输入 ⇒ 可能触发自动改稿回路；即便表外，也会把「恒 warning」的语义破坏掉。

两条突变各钉一个失效方向
------------------------
* **SKILL-NS-1** 把 skill 检查的 rule_id 命名空间从 ``SKILL-`` 改成 ``AI-BEAT-``
  ⇒ 产出的 rule_id（``AI-BEAT-REPEAT``）**正好撞上** ``CONFIRM_RULES`` 里已登记的
  ``AI-BEAT-REPEAT`` ⇒ 「skill 的 rule_id 不得与任何后果表撞名」必红。
  （canary 的 check_id 取 ``REPEAT`` 就是为了撞上它。）
* **SKILL-NS-2** 把 skill 检查命中从 ``warnings`` 改落 ``errors`` ⇒
  「恒 warning、只进 warnings」必红。

⚠️ **为什么改本仓的代码而不是改后果表本身**：``packages/core/quality/issues.py`` 与
``packages/core/api/routers/workflows/repair_policy.py`` **不是本批的领地**
（并发成员的独占路径），本轮**不许改**它们。把突变落在「命名空间前缀」与
「落桶通道」这两处**本批自己写的代码**上，钉的是**同一个不变量**
（skill 检查绝不进后果表 / 绝不进 errors），且不与并发成员抢文件。

判别
----
``tests/unit/test_skill_injection.py``：
- ``test_skill_check_rule_id_is_not_registered_in_any_consequence_table``；
- ``test_skill_checks_land_in_warnings_only``。
"""

MUTATIONS = [
    (
        "SKILL-NS-1",
        "packages/core/skill/consumers.py",
        'SKILL_RULE_ID_PREFIX: Final[str] = "SKILL-"',
        'SKILL_RULE_ID_PREFIX: Final[str] = "AI-BEAT-"',
        "tests/unit/test_skill_injection.py",
    ),
    (
        "SKILL-NS-2",
        "packages/workflows/chapter_review/pipeline.py",
        "        warnings.append(\n"
        "            f\"[{hit['rule_id']}] {hit['keyword']} ×{hit['count']}{detail}\"\n"
        "        )\n",
        "        errors.append({\n"
        "            \"rule_id\": hit[\"rule_id\"],\n"
        "            \"severity\": \"error\",\n"
        "            \"message\": f\"[{hit['rule_id']}] {hit['keyword']} ×{hit['count']}{detail}\",\n"
        "        })\n",
        "tests/unit/test_skill_injection.py",
    ),
]
