"""``mutation_harness.py`` 的锚点看守：让一条锚点失配 ⇒ 预检必红（dev-b 批次二十）。

背景
----
仓里有**两套**突变执行器（AGENTS 坑区已登记「登记不修（批次十八）：两套突变执行器
并存」）：``tests/mutation_specs/run_spec.py``（全套 85 个规格文件 / 477 条，2026-10-24
实测；**有**只读预检 ``validate_specs.py``）与 ``scripts/mutation_harness.py``
（**21 条硬编码**，**无预检**）。

⇒ 缺口不是「21 条没跑过」，而是**锚点被别人的改动打断时无人预警**：执行器跑起来
才判 ``[ABORT]``，而锚点漂移会一路静默。

本规格配套的新测试 ``tests/unit/test_script_mutation_harness.py`` 用 **AST** 取
``MUTATIONS`` 字面量（**不 import**——虽然该模块现在**可以**被 import 了：
``sys.stdout.reconfigure`` 已在 2026-10-23 移进 ``main()`` 的 ``_reconfigure_stdio()``；
AST 取值是为了**零副作用**地读字面量，不是因为 import 会崩），并逐条断言：目标文件
存在、**锚点恰好命中 1 次**、替换确实改变内容（**字节级**）、**.py 目标变异后仍可
解析**（2026-10-24 补齐真用例，此前只是**声称**）、定向用例文件存在。

突变
----
* **MUT-HARNESS-1** 把 M1 的锚点改成一个**在目标文件里不存在**的形态
  ⇒ 该条锚点命中 0 次 ⇒ ``test_every_anchor_hits_exactly_once`` 必红。
  ⚠️ 这条突变**模拟的正是真实事故形状**：批次十三实测「0032 给 writer/director 键
  各加一维 ``skills_fp`` ⇒ 键元组尾行形态变了」⇒ 既有锚点失配 ⇒ 执行器 ``[ABORT]``
  ⇒ 而**当时没有任何预检会在跑之前告诉你**（AGENTS 坑区「改一个模块会打断既有
  突变规格的锚点 ⇒ 执行器会 [ABORT] 或误判」）。

判别
----
``tests/unit/test_script_mutation_harness.py``（**8 例** = 既有 7 条 + 2026-10-24
补齐的「``.py`` 目标变异后仍可解析」1 条；纯只读：只读仓库文件，不写一个字节）。

⚠️ **「8 例」此前是失实声称**（2026-10-24 实测：当时文件里只有 **7** 条用例）
——AGENTS 方法论「声称与实测分离」同族；现已随补用例**同时**订正为真。
"""

MUTATIONS = [
    (
        "MUT-HARNESS-1",
        "scripts/mutation_harness.py",
        '        "disabled={v.status === \'sealed\'}",\n',
        '        "disabled={v.status === \'never\'}",\n',
        "tests/unit/test_script_mutation_harness.py",
    ),
]
