"""D-9「commit 风控门两条子句零可区分」的突变规格（2026-10-23 · 第二轮审查 D-9）。

**执行**：由 Lead 统一跑 ``tests/mutation_specs/run_spec.py``；本文件只做只读预检
（``validate_specs.py``）。⚠️ **预检必须后置**——它读的正是执行器临时改写的源文件
（坑区「预检与执行器并发 ⇒ validate_specs 报出假『锚点未命中』」，2026-10-16 批次八）。

**本组守的机制**：``packages/workflows/chapter_commit/pipeline_common.py``
``_has_high_risk_change`` 的**两条零区分子句**：

    character facet == "definition"  |  world world_kind == "rule"

Lead 插桩量化（``tests/workflow`` + ``tests/api`` 全量跑完）：该函数被调用 **41 次**，
其中 ``facet=='definition'`` **0 次**、``world_kind=='rule'`` **0 次**
⇒ 删掉这两条子句，41 次调用的判定结果**一次都不会变** ⇒ 全仓无任何测试会红
⇒ 那不是「覆盖率低」，是**判别力为零**：没有任何东西能区分它们存在与否。

两条各守一个**机制**（撤任意一条 ⇒ 对应机制那侧判别力归零）
--------------------------------------------------------------------------
D9M1 撤掉 ``facet == "definition"`` 子句
      ⇒ workflow 侧判定对该夹具不再命中 ⇒ ``test_definition_facet_triggers_high_risk``
        与 ``test_inject_validate_marks_definition_facet_as_needing_approval`` **红**
        （另：L3 的 definition 用例因 ctx 标志由 payload 推导 ⇒ **也红**）。

D9M2 撤掉 ``world_kind == "rule"`` 子句
      ⇒ 同上，对应 world 侧的三条用例红（L1 + L2 + L3）。

⚠️ **「互不串味」的字面口径（2026-10-23 订正）**：一条突变会同时打红**多条用例**
（实测各红 2~3 条，而非 1 条）——因为「写入路径断言」天然包含取值链路
（L2 的标志、L3 的 ctx 标志都从同一判定推导）。**各守一个机制**成立；
「只让对应用例红」字面为假，别照字面理解。

⚠️ **孪生实现提醒**（别在错的地方撤）：``packages/core/story_state/deltas.py::high_risk_change_ids``
是**另一份**判定，它与 workflow 侧口径**完全对称**——同样是
``risk_level=="HIGH"`` / ``character facet=="definition"`` / ``world world_kind=="rule"``
（core 侧见 ``deltas.py:116`` 的 docstring 第 3 条 + ``:127-131`` 的实现）。
⚠️ **本文件早先的版本把 core 侧写成「不认 ``world_kind=="rule"``、覆盖不对称」——那是错的**
（对抗审查 A 实测证伪）。照着错描述去 core 侧「对齐」**恰好造出真的门禁缺口**：
core 侧 ``commits.py`` 的 HIGH 风险门将不再拦世界规则变更 ⇒ 走 API 直接提交可绕过人工审批。
⇒ 本组只动 workflow 侧这一份（``pipeline_common.py``）；core 侧的对称性由
``tests/unit/test_commit_high_risk_facets.py`` 的 L4 用例钉住。

**为什么这两条不能省**：它们守的正是「删掉也没人发现」的那两行。
没有它们，新加的夹具只是一次性断言，**证明不了子句真的被守住**。

三条自查（写完必做，勿跳过）
--------------------------
1. **锚点恰好命中 1 次**——两条均逐条 ``str.count`` 核过（均为 1）；
   ``pipeline_common.py`` 工作树为 **CRLF**（core.autocrlf=true），
   但执行器用 ``read_text(encoding="utf-8")``（universal newlines）读取、
   ``write_text`` 写入 ⇒ 锚点里的 ``\\n`` 能命中且替换后仍是 CRLF。
2. **每条都真的改行为**——``run_spec.py`` 比对写入前后 SHA256，没变即 ``[ABORT]``。
   两条都是真换语义（各删掉一个 OR 条件 ⇒ 判定结果在对应夹具上从 True 变 False），
   不是「插了行注释、逻辑未变」那种自伤写法。
3. **交接前清点突变残留**——``find . -name "*.mutbak"`` + ``grep -rn "MUTATION-PROBE-D9"``
   （区分大小写；``MUTATION`` 本身是**正常测试函数名**的一部分，用裸词搜会误报）。
"""

MUTATIONS = [
    (
        # 撤掉 character facet=definition 子句 ⇒ 该夹具不再触发高风险判定
        "D9M1",
        "packages/workflows/chapter_commit/pipeline_common.py",
        """        if isinstance(ch, dict) and (
            ch.get("risk_level") == "HIGH" or ch.get("facet") == "definition"
        ):""",
        """        if isinstance(ch, dict) and (
            ch.get("risk_level") == "HIGH"  # MUTATION-PROBE-D9M1 撤掉 facet=definition
        ):""",
        "tests/unit/test_commit_high_risk_facets.py",
    ),
    (
        # 撤掉 world world_kind=rule 子句 ⇒ 该夹具不再触发高风险判定
        "D9M2",
        "packages/workflows/chapter_commit/pipeline_common.py",
        """        if isinstance(w, dict) and (
            w.get("risk_level") == "HIGH" or w.get("world_kind") == "rule"
        ):""",
        """        if isinstance(w, dict) and (
            w.get("risk_level") == "HIGH"  # MUTATION-PROBE-D9M2 撤掉 world_kind=rule
        ):""",
        "tests/unit/test_commit_high_risk_facets.py",
    ),
]
