"""``applies_to`` 过滤失效 ⇒ 必红（内部消费外部 skill · 0032）。

缺陷形状：**范围失控**。

``applies_to`` 是 skill 的**适用环节白名单**：作者声明「这份手艺只给 writer 用」，
消费侧就必须真的只给 writer 用。过滤一旦失效（恒真 / 恒假 / 名单写错），后果是
两种方向都坏：

- **恒真**（本条突变）：本该只给 writer 的手艺流进 critic / deep_reviewer ⇒
  评审方按一份「作者没打算用于评审」的手艺去审正文；而**更糟**的是
  ``checks`` 也跟着流进评审侧 ⇒ 一份「只给写作端」的检查项在评审里开始报命中。
- **恒假**：该注入的 skill 静默不生效 ⇒ 「校验通过但永不生效」的死资源
  （本仓硬规则 9「数据源存在 ≠ 已进装配」的同形）。

突变
----
* **SKILL-FILTER-1** 把 ``skill_applies_to`` 的**生效分支**判定改成恒真
  （``return True``）⇒ 全部 skill 注入全部 consumer ⇒
  ``test_applies_to_filters_per_consumer`` 必红。

⚠️ **突变必须打在「生效分支」上**（本批实测踩过）：``skill_applies_to`` 同时接受
``str`` 与 ``tuple`` 两种 consumer，而**生产链路的 consumer 恒为元组**
（``_skill_excerpt`` 把单个 consumer 归一成 ``(consumer,)``）⇒ 真正生效的是
``isinstance(consumer, tuple)`` 那一支。首版突变打在了 ``str`` 分支
（``return consumer in declared``）——那是**生产链路上的死代码** ⇒ 突变存活
（用例全绿）。教训与坑区「断言了结果 ≠ 判别了机制」同族，但失效方向相反：
**不是测试测不到机制，而是变异改了一行没人走的代码**。
该事实已留痕在 ``packages/core/skill/consumers.py::skill_applies_to`` 的 docstring。

判别
----
``tests/unit/test_skill_injection.py``：
- ``test_applies_to_filters_per_consumer``（writer 专用 skill 不进 director 装配）。
"""

MUTATIONS = [
    (
        "SKILL-FILTER-1",
        "packages/core/skill/consumers.py",
        "    if isinstance(consumer, tuple):\n"
        "        return any(c in declared for c in consumer)\n",
        "    if isinstance(consumer, tuple):\n"
        "        return True  # MUTATION-SKILL-FILTER-1: 撤掉 applies_to 过滤\n",
        "tests/unit/test_skill_injection.py",
    ),
]
