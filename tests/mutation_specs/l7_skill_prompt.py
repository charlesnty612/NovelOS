"""提示词不声明 ``skills`` 段 ⇒ 必红（**死参数**守卫 · 0032）。

缺陷形状（``author_intent`` 事故的同形，本仓已犯过一次）：

端点收下参数 → 写进 run ctx → 装配 payload 里有 → **提示词里没有** ⇒ 模型看不见 ⇒
「作者亲手设的铁律在 ch1-6 一个模型都没看见」，全程无告警。

本仓的提示词是「一文件一整段」，**没有模板引擎、没有片段位**（19 份 prompt 零占位符）
⇒ 往 payload 加 ``skills`` 键**必须**有提示词声明它，否则 ``skills`` 段是死参数。

两条突变各钉一个失效方向
------------------------
* **SKILL-PROMPT-1** 把 ``writer-v5.md`` 的顶层 ``skills`` 键改名成 ``skill``
  （改字节但**不改行为**的一类弱变异：JSON 仍合法、提示词仍「有声明」，只是声明
  的是另一个键）⇒ payload 的 ``skills`` 段与提示词声明**对不上** ⇒
  ``test_latest_prompt_of_every_skill_consumer_declares_skills_segment`` 必红。
* **SKILL-PROMPT-2** 同款改 ``critic-v2.md`` ⇒ 评审侧的声明守卫必红。

⚠️ 为什么是「改名」而不是「删块」：改名保留 JSON 合法性 ⇒ 突变**确实跑起来了**
（若删块导致 JSON 非法，判别靠的是「解析失败」而不是「声明缺失」，那是另一条
失效方向）。本条钉的是「声明的键名必须与 payload 的键名逐字一致」。

判别
----
``tests/unit/test_skill_prompt_contract.py``：
- ``test_latest_prompt_of_every_skill_consumer_declares_skills_segment``；
- ``test_prompt_declared_shape_matches_code_shape``（声明形状 vs 代码实际形状）。
"""

MUTATIONS = [
    (
        "SKILL-PROMPT-1",
        "docs/agents/prompts/writer-v5.md",
        '  "skills": {\n    "items": [\n',
        '  "skill": {\n    "items": [\n',
        "tests/unit/test_skill_prompt_contract.py",
    ),
    (
        "SKILL-PROMPT-2",
        "docs/agents/prompts/critic-v2.md",
        '  "skills": {\n    "items": [\n',
        '  "skill": {\n    "items": [\n',
        "tests/unit/test_skill_prompt_contract.py",
    ),
]
