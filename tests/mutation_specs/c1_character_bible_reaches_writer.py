"""角色圣经到达 writer 的修复（2026-10-16 批次十一 · Lead 自办）的突变规格。

六条，覆盖三处机制各两个方向。判别文件
``tests/unit/test_character_bible_reaches_writer.py``（7 例）已由
``.mutcheck_bible.py`` 形式的定向脚本实测 **6/6 全捕获**、源文件逐字节还原；
本文件把它固化进统一闸门。

机制与撤除方向：

* **CB-L1** ``_is_triggered`` 的 ``entity_id`` 候选词（语料侧本就收了
  ``character_id``，判据侧此前从不看它 ⇒ 本场出场角色必然「未命中」）
* **CB-L3a** 分页层放行条件整体退回「只认 touched」
* **CB-L3b/c/d** 三条放行依据逐条撤：``inject_mode='always'`` / ``protagonist`` /
  本场出场
* **CB-FP** 角色指纹维度（E1 的 ``chars_fp``）退化成常量 ⇒ 改角色圣经后脏命中

⚠️ CB-L3b/c/d 必须**各占一条**：只钉 CB-L3a（整体撤除）的话，一个「只保留了其中
两条」的半修实现照样全绿——这正是本仓登记的「半修」形状（``l2_state_list`` 的
L2-18 同款）。
"""

MUTATIONS = [
    (
        "CB-L1",
        "packages/core/context_engine/builders_common/excerpts.py",
        "    if _is_triggered(name, aliases, trigger_corpus_lower, entity_id=entity_id):",
        "    if _is_triggered(name, aliases, trigger_corpus_lower):",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        "CB-L3a",
        "packages/core/context_engine/writer_input.py",
        """            keep_full = (
                isinstance(cid, str)
                and (
                    cid in touched_chars
                    or cid in scene_chars
                    or c.get("inject_mode") == "always"
                    or c.get("role") == "protagonist"
                )
            )""",
        "            keep_full = isinstance(cid, str) and cid in touched_chars",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        "CB-L3b",
        "packages/core/context_engine/writer_input.py",
        '                    or c.get("inject_mode") == "always"\n',
        "",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        "CB-L3c",
        "packages/core/context_engine/writer_input.py",
        '                    or c.get("role") == "protagonist"\n',
        "",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        "CB-L3d",
        "packages/core/context_engine/writer_input.py",
        "                    or cid in scene_chars\n",
        "",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        # ⚠️ 2026-10-22 批次二十三（热路径：指纹查询合并进 peek 的连接）：锚点原文
        # 随源码演化——``characters_fingerprint`` 现在多传一个 ``conn=``（复用装配器
        # 持有的那条连接）。**语义不变**（仍是「撤掉 chars_fp 维度 ⇒ 改角色圣经后
        # 脏命中」⇒ 定向用例必红）。
        "CB-FP",
        "packages/core/context_engine/writer_input.py",
        "    chars_fp = characters_fingerprint(db_path, project_id, conn=_conn)",
        '    chars_fp = "CB-FP-MUTATION: 常量指纹，退化成脏命中"',
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        # L1 主角放行：计划文本没提主角时也保他。与 CB-L1 是**不同层**的同一诉求——
        # CB-L1 撤的是「id 作为候选词」，本条撤的是「主角无条件放行」，两条各占一层。
        "CB-L1PRO",
        "packages/core/context_engine/builders_common/excerpts.py",
        '''    if kind == "character" and full_excerpt.get("role") == "protagonist":
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
''',
        "    # CB-L1PRO-MUTATION: 撤掉主角放行\n",
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
    (
        # plan_fp：plan_json 进 payload 却从未入键（director 键有、writer 键没有）。
        # 与 CB-FP 同族不同源，必须各占一条。
        "CB-PLANFP",
        "packages/core/context_engine/writer_input.py",
        "    plan_fp = _fingerprint_plan_json(peek.get(\"plan_json_raw\"))",
        '    plan_fp = "CB-PLANFP-MUTATION: 常量指纹"',
        "tests/unit/test_character_bible_reaches_writer.py",
    ),
]
