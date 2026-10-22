r"""质量域「结构钉」的突变规格（2026-10-22 批次二十 · novelos-dev-a）。

补的缺口
--------
``tests/unit/quality/test_quality_module_inventory.py`` 刚建立（批次二十），它是
**结构看守**（钉模块清单 / 逐模块可直接导入 / 门面一致性 / 五族职责边界），
按方法论要求，**新测试必须做突变验证**——否则「这份测试到底能不能判别」无人知道。

本文件两条突变都刻意选「**破坏结构但不破坏 import**」的写法：
⇒ 跑测时**不会**变成 import 错误 / 收集失败（那是**假捕获**：
退出码 2/4 与「断言红」在退出码上同形，AGENTS 坑区已登记四次同源事故），
红的就是**新测试里那一条结构断言**。

两条突变
--------
* **QINV-1**（``quality/__init__.py``）——从门面 ``__all__`` 里**删掉一个公开名**
  （``check_style_trend``）。import 语句不动 ⇒ 包仍可正常 import、
  ``from packages.core.quality import check_style_trend`` 仍可用 ⇒
  **只有「门面公开面收缩」这一件事变了** ⇒ 判别点 =
  ``test_facade_keeps_its_key_public_api``。
  （这一条正是「从 ``__init__.py`` 的再导出里删掉一个名字」的建议写法。）

* **QINV-2**（``ai_patterns.py``）——把**门面**对某族规则函数的 import 改成
  ``_legacy_`` 别名（``count_short_paras``）⇒ 门面**不再暴露**那个族的规则函数 ⇒
  判别点 = ``test_facade_reexports_every_family_rule_symbol``。

⚠️ **为什么 QINV-2 改在「门面」而不是「族内」**（口径说明，留痕）
------------------------------------------------------------
建议写法是「把某个**族模块**里的一条规则函数改名为 ``_legacy_xxx``（其他模块仍能
import，但归属断言抓）」。**该写法在本仓不可行**：实测（2026-10-22），
``ai_patterns.py`` 门面 import 了四个族**几乎全部**的模块级符号
（``count_short_paras`` / ``count_long_paragraphs`` / ``count_anthro_vehicles`` /
``count_contrast_pairs`` / ``count_dialogue_echoes`` / ``dialogue_ratio`` …），
且这些符号在门面**函数体里并未被使用**（只用于 ``__all__`` 再导出）⇒
在族内改名会**直接打断门面的 import** ⇒ 变成收集期 ImportError ⇒
**假捕获**（红的原因不是结构断言，而是模块根本 import 不进来）。

⇒ 故把同一形状放到**可观测的一侧**：改门面的 import（别名 import 合法、
且该符号门面体内未用 ⇒ 行为零变化）⇒ 结构断言能真正抓到「公开面缩水」。
这与「族内改名」要抓的**同一个失效形状**（规则从门面上被摘掉），
但**不破坏 import**。

判别力自检（不是恒真断言）
--------------------------
两条突变分别只让**一条**新断言变红，其余**九条**仍绿：
- QINV-1 ⇒ 只有 ``test_facade_keeps_its_key_public_api`` 红
  （``__all__`` 其余名字仍解析得到 ⇒ 悬空/自指/重复三条断言不受影响）；
- QINV-2 ⇒ 只有 ``test_facade_reexports_every_family_rule_symbol`` 红
  （族模块本身仍定义着那个符号 ⇒ 第 2、4(a)(b) 层断言不受影响）。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① 门面公开面收缩：从 __all__ 删掉一个关键公开名
    #    import 语句不动 ⇒ 包仍可 import ⇒ 只有「公开面」这一件事变了。
    # ------------------------------------------------------------------
    (
        "QINV-1-facade-drops-key-public-name",
        "packages/core/quality/__init__.py",
        '    "check_style_trend",\n',
        "",
        "tests/unit/quality/test_quality_module_inventory.py",
    ),
    # ------------------------------------------------------------------
    # ② 门面不再暴露某族的规则函数（改 import 为 _legacy_ 别名）
    #    该符号在门面函数体里未被使用 ⇒ 行为零变化 ⇒ 纯结构破坏。
    # ------------------------------------------------------------------
    (
        "QINV-2-facade-reexport-of-family-symbol-dropped",
        "packages/core/quality/ai_patterns.py",
        "    count_short_paras,\n",
        "    count_short_paras as _legacy_count_short_paras,\n",
        "tests/unit/quality/test_quality_module_inventory.py",
    ),
]
