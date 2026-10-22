"""``scripts/mutation_probe.py`` 的突变规格（2026-10-22 工程卫生批次）。

这个探针是**危险工具**：它临时改工作区。批次十七刚为此付过一次代价
（突变态被 ``git add -A`` 扫进提交并推送 ⇒ CI 真的红了）。⇒ 它的三条安全性质
必须各有一条突变钉住，否则「测试全绿」只说明夹具没坏，不说明闸门在。

三条突变各钉一个失效方向：

* **MP-1** 撤掉 ``finally`` 里的还原 ⇒ 源文件停在改动态 ⇒ 用例 1 必红
  （「改坏源码后文件被还原」那条断言）；
* **MP-2** 撤掉「发现 ``*.mutbak`` 即拒绝」的并发闸 ⇒ 执行器在飞时也敢跑
  ⇒ 用例 3 必红；
* **MP-3** 撤掉「锚点必须唯一命中」的检查 ⇒ 在锚点不存在时仍去替换
  ⇒ 用例 2 必红。

⚠️ **只钉一条不够**：只钉 MP-1 的话，「闸门形同虚设」这种改法能全绿；
只钉 MP-2 的话，「不还原」这种改法能全绿。三条是**正交**的失效方向。

⚠️ MP-1 会让 ``scripts/mutation_probe.py`` 在突变期间处于「不还原」态
⇒ 执行器自己的 ``finally`` 负责把它还原（嵌套两层还原，顺序：先探针的 finally
不动，再执行器的 finally 还原）⇒ 不会留下残留。
"""

MUTATIONS = [
    (
        "MP-1",
        "scripts/mutation_probe.py",
        "        target.write_text(original, encoding=\"utf-8\")\n        restored = sha256(target)\n",
        "        # MUTATION-MP-1: 撤掉还原\n        restored = sha256(target)\n",
        "tests/unit/test_mutation_probe.py",
    ),
    (
        "MP-2",
        "scripts/mutation_probe.py",
        "    if backups:\n",
        "    if False:  # MUTATION-MP-2: 撤掉并发闸\n",
        "tests/unit/test_mutation_probe.py",
    ),
    (
        "MP-3",
        "scripts/mutation_probe.py",
        "    if hits != 1:\n",
        "    if False:  # MUTATION-MP-3: 撤掉锚点唯一性检查\n",
        "tests/unit/test_mutation_probe.py",
    ),
]
