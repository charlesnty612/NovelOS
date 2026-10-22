"""两个校准器的分位数口径：退化任一口径 ⇒ 必红（dev-b 批次二十）。

背景
----
``scripts/delta_anchoring_calibrate.py`` 的 ``_quantile`` 是**线性插值**分位数，
``scripts/revise_fidelity_calibrate.py`` 的 ``_percentile`` 是**按索引取**——
**两套不同的口径**（量的东西也不同：delta bigram 覆盖率 vs revise 保真比），
且**后者没有 docstring** ⇒ 口径只能从代码读出来。

本规格把**各自**的口径钉死（**不合并**）：任一口径被动过都会响，从而逼出一次
有意识的决定，而不是让它悄悄漂移。

突变
----
* **MUT-CAL-1** 把 ``_quantile`` 的插值系数改成 0 ⇒ 退化成「取下界」
  ⇒ ``q=0.5`` 于 ``[1,2,3,4]`` 从 ``2.5`` 变成 ``2`` ⇒
  ``test_quantile_is_linear_interpolation`` 必红。
* **MUT-CAL-2** 把 ``_percentile`` 的索引从 ``int(q*n)`` 改成 ``int(q*(n-1))``
  ⇒ ``q=0.5`` 于 ``[1,2,3,4]`` 从 ``3`` 变成 ``2`` ⇒
  ``test_percentile_picks_by_index_not_by_interpolation`` 必红。

判别
----
``tests/unit/test_script_calibrate_stats.py``（6 例，纯函数 ⇒ 无需语料、CI 不 skip、
不碰任何数据库）。
"""

MUTATIONS = [
    (
        "MUT-CAL-1",
        "scripts/delta_anchoring_calibrate.py",
        "    frac = pos - lo\n",
        "    frac = 0\n",
        "tests/unit/test_script_calibrate_stats.py",
    ),
    (
        "MUT-CAL-2",
        "scripts/revise_fidelity_calibrate.py",
        "    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))\n",
        "    idx = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))\n",
        "tests/unit/test_script_calibrate_stats.py",
    ),
]
