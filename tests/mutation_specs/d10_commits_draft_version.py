"""D-10「commits 记定稿版本」的突变规格（2026-10-23 · 第二轮审查 D-10）。

**执行**：由 Lead 统一跑 ``tests/mutation_specs/run_spec.py``；本文件只做只读预检
（``validate_specs.py``）。⚠️ **预检必须后置**——它读的正是执行器临时改写的源文件
（坑区「预检与执行器并发 ⇒ validate_specs 报出假『锚点未命中』」，2026-10-16 批次八）。

**本组守的形状**：**测量/消费对象 ≠ 被审对象**（本仓反复出现的同一形状）。
前三处已修：0024（质量报告记录的是哪一版）、``gate._reviewed_draft_version``
（门禁量的是不是被审那一版）、评审/核销两处。本组守**第四处**：
产物面导出的是不是**已定稿**那一版。

两条各守一个机制（撤任意一条只让对应用例红，互不串味）
--------------------------------------------------------------------------
D10M1 ``commit_delta`` 写入 commits 时把 draft_version 丢掉
      ⇒ ``test_commit_delta_persists_draft_version`` 红。
      守的是「**记录本身**」：值算对了但没落库 = 没记。

D10M2 commit 节点不再从 ctx 取「被审版本」（恒 None）
      ⇒ ``test_commit_node_records_reviewed_draft_version`` 红。
      守的是「**取值来源**」：``quality_reviewed_draft_version`` 这个键此前
      **只写不读**（全仓零消费点）——与「author_intent 死参数」同族：
      「写进 ctx」不等于「生效」。撤掉读取就退化成「谁都没记」。

**为什么这两条不能省**：本组是最容易「测了但没测到」的一类——
commits.draft_version 是一个**从未被读过的列**（导出侧接线在另一批），
若只写测试不撤修复，全绿也证明不了任何事。

三条自查（写完必做，勿跳过）
--------------------------
1. **锚点恰好命中 1 次**——两条均逐条 ``str.count`` 核过（均为 1）；
   ``commits.py`` / ``commit.py`` 工作树为 **CRLF**（core.autocrlf=true），
   但执行器用 ``read_text(encoding="utf-8")``（universal newlines）读取、
   ``write_text`` 写入 ⇒ 锚点里的 ``\\n`` 能命中且替换后仍是 CRLF。
2. **每条都真的改行为**——``run_spec.py`` 比对写入前后 SHA256，没变即 ``[ABORT]``。
   两条都是真换语义（一个改写入值、一个改函数返回），无一条是「插了行注释、
   逻辑未变」那种自伤写法（坑区「脚本报存活时先怀疑量具，再怀疑变异」第 2 次）。
3. **交接前清点突变残留**——``find . -name "*.mutbak"`` + ``grep -rn "MUTATION-PROBE-D10"``
   （区分大小写；``MUTATION`` 本身是**正常测试函数名**的一部分，用裸词搜会误报）。
"""

MUTATIONS = [
    (
        # commit 事务里把「定的是哪一版」丢掉 ⇒ commits.draft_version 恒 NULL
        "D10M1",
        "packages/core/story_state/commits.py",
        """                    _rollback_of,
                    _normalize_draft_version(draft_version),
                ),""",
        """                    _rollback_of,
                    None,  # MUTATION-PROBE-D10M1 丢掉「定稿版本」
                ),""",
        "tests/unit/test_commits_draft_version.py",
    ),
    (
        # commit 节点不再读 ctx 里 gate 写的「被审版本」⇒ 谁都没记
        "D10M2",
        "packages/workflows/chapter_commit/commit.py",
        """    raw = ctx.get("quality_reviewed_draft_version")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    return None""",
        """    return None  # MUTATION-PROBE-D10M2 不再读 ctx 的被审版本""",
        "tests/unit/test_commits_draft_version.py",
    ),
]
