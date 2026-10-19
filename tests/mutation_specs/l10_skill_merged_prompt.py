"""合并体 ``director_planner`` 的 ``skills`` 声明必须真在提示词里（2026-10-19 批次十三）。

缺口是怎么来的
--------------
内部消费外部 skill（0032 批次）给 **5 份**提示词升版并声明了 ``skills`` 段
（writer-v5 / director-v2 / scene_planner-v2 / critic-v2 / deep_reviewer-v2），
**唯独漏了合并体** ``director_planner``——而它恰恰是 **chapter-plan 的活路径**
（``chapter_plan/pipeline.py::_director_planner_node``；独立 director 节点已不再被
chapter-plan 调用）。后果与「author_intent 死参数」事故同形：

* 装配侧**确实**把 ``skills`` 段放进了合并 payload
  （``planner_input.py::_merge_skill_planner_fields``）⇒ **数据源存在**；
* 提示词**没有声明**这个键 ⇒ 模型不知道它是什么、跟谁冲突时听谁的
  ⇒ **「进了装配」也不等于「模型看得懂」**。

而 F 组的死参数守卫（``test_skill_prompt_contract.py``）的判别面是
``SKILL_CONSUMER_AGENTS`` **五个角色名**，**不含**合并体 ⇒ **守卫也漏**。

两条突变各钉一个失效方向
------------------------
* **SKILL-MERGED-1** 把 ``director_planner-v3.md`` 的顶层 ``skills`` 键改名成
  ``skill``（**改字节但不改行为**的弱变异：JSON 仍合法、提示词仍「有声明」，
  只是声明的是另一个键）⇒ 声明的键名与 payload 的键名对不上 ⇒
  ``test_latest_prompt_of_every_skill_consumer_declares_skills_segment`` 必红。
* **SKILL-MERGED-2** 删掉合并体提示词里的「**优先级裁决**」条款
  ⇒ ``test_every_skill_consumer_prompt_has_absence_and_priority_clause`` 必红。

⚠️ **两条都只改提示词、不改守卫**——守卫本身被削弱的情形由「守卫的判别面是
**派生**的」保证：``SKILL_CONSUMER_PROMPTS`` 里多了一个合并体调用点，而断言
遍历的是**整个** tuple，不是一个手挑的名字。
"""

MUTATIONS = [
    (
        "SKILL-MERGED-1",
        "docs/agents/prompts/director_planner-v3.md",
        '  "skills": {\n    "items": [\n',
        '  "skill": {\n    "items": [\n',
        "tests/unit/test_skill_prompt_contract.py",
    ),
    (
        "SKILL-MERGED-2",
        "docs/agents/prompts/director_planner-v3.md",
        "> - **优先级裁决**：`skills` 段是外部可移植的通用手艺，仅在 `author_intent`、",
        "> - （MUTATION-SKILL-MERGED-2：删掉优先级裁决条款）",
        "tests/unit/test_skill_prompt_contract.py",
    ),
]
