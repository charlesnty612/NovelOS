"""``m1_report.py`` 的容错契约：撤掉「链式取值的中段类型守卫」⇒ 必红（dev-b 批次二十）。

背景
----
``scripts/m1_report.py``（796 行）长期零测试（2026-10-22 批次十九实测）。它有一条
明确的容错契约（脚本自己的分节注释：「纯读取 + 容错」）：**数据文件坏了要能被跳过
并报出来，而不是让整份报告崩掉**。

突变
----
* **MUT-M1R-1** 撤掉 ``_safe_get`` 链式取值里的「中段不是 dict 就回落 default」
  守卫 ⇒ 遇到 ``{"a": 5}`` 取 ``a.b`` 时会 ``TypeError``（而不是回落 default）
  ⇒ ``test_safe_get_walks_chained_keys_and_falls_back_to_default`` 必红。
  ⚠️ 危害：报告生成器被一个**形状不符**的数据文件带崩 ⇒ 长跑白跑。

判别
----
``tests/unit/test_script_m1_report.py``（7 例，全在 ``tmp_path`` 造合成 JSON
⇒ 零不可逆操作、零 dev 库）。
"""

MUTATIONS = [
    (
        "MUT-M1R-1",
        "scripts/m1_report.py",
        "        if not isinstance(cur, dict) or k not in cur:\n",
        "        if k not in cur:\n",
        "tests/unit/test_script_m1_report.py",
    ),
]
