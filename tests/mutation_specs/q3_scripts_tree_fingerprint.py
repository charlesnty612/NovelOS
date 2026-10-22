"""``tree_fingerprint.py`` 的口径看守：撤掉「mtime 维度」/「扫描根」⇒ 必红（dev-b 批次二十）。

背景
----
``scripts/tree_fingerprint.py`` **定义着闸门口径本身**（跑测前后各取一次，逐位一致
才算数），却长期**零测试**（2026-10-22 批次十九实测）。本规格配套的新测试
``tests/unit/test_script_tree_fingerprint.py`` 钉住三条口径性质：

1. 口径 = ``{相对路径}|{字节长度}|{mtime 整秒}`` ⇒ **mtime 是哈希的输入**；
2. 扫描根 = ``packages/ tests/ scripts/ database`` ⇒ **``docs/`` 与
   ``apps/web/`` 不在指纹内**（现网口径；AGENTS 载明「指纹口径不含 ``apps/web``」，
   前端批次的等价保证是 vitest 全量 + ``git status``）。

两条突变各钉一个失效方向
------------------------
* **MUT-TREE-1** 撤掉行里的 ``|{int(st.st_mtime)}`` ⇒ 指纹变成「路径 + 字节数」
  ⇒ ``touch``（内容一字未动）不再改变指纹 ⇒
  ``test_touching_a_file_without_changing_content_changes_the_fingerprint`` 与
  ``test_restoring_the_exact_mtime_restores_the_fingerprint`` 必红。
  ⚠️ 这条**不是「实现坏了」**：口径不含 mtime 本身是**已裁决**的性质
  （AGENTS 坑区：两段式比对只关心「是否变化」，不含 mtime 也够）；但**本仓的口径
  就是含 mtime** ⇒ 有人把它改掉时，必须同批更新 AGENTS 坑区与本规格，而不是
  让它悄悄生效。
* **MUT-TREE-2** 给 ``DEFAULT_ROOTS`` 加上 ``docs`` ⇒ 指纹范围扩大 ⇒
  ``test_scope_is_exactly_the_four_declared_roots`` 与
  ``test_default_roots_are_the_four_declared_ones`` 必红。
  ⚠️ 同款理由：脚本 docstring 明写「改这里 = 历史哈希全部作废，须在 CHANGELOG 登记」
  ⇒ 属**须登记**的口径变更，不该无声发生。

判别
----
``tests/unit/test_script_tree_fingerprint.py``（10 例，全在 ``tmp_path`` 的合成
小文件树上跑 ⇒ **零 mtime 噪音**，不碰仓库里的真文件）。
"""

MUTATIONS = [
    (
        "MUT-TREE-1",
        "scripts/tree_fingerprint.py",
        '        rows.append(f"{rel}|{st.st_size}|{int(st.st_mtime)}")\n',
        '        rows.append(f"{rel}|{st.st_size}")\n',
        "tests/unit/test_script_tree_fingerprint.py",
    ),
    (
        "MUT-TREE-2",
        "scripts/tree_fingerprint.py",
        'DEFAULT_ROOTS: tuple[str, ...] = ("packages", "tests", "scripts", "database")\n',
        'DEFAULT_ROOTS: tuple[str, ...] = ("packages", "tests", "scripts", "database", "docs")\n',
        "tests/unit/test_script_tree_fingerprint.py",
    ),
]
