"""前章草稿内容维度：撤掉 ``prev_draft_fp`` ⇒ 必红（R-2，2026-10-24）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 8 次**）
------------------------------------------------------------------
前一章的**最新草稿正文**进两条装配 payload：

- director：``previous_chapter_tail``（``_previous_chapter_tail``：前章尾 300 字 +
  前章 ``chapter_id`` / ``number``）；
- writer：``recent_prose.last_chapter_excerpt``（``_recent_prose_tail``：尾 500 字）。

而**草稿写路径不推** ``story_states.state_version``（``drafts`` 表由
``ChapterService.create_draft`` 写；只有 commit 才推版本）⇒ 前章**重出稿但未
commit**时（同 ``state_version`` + 同 ``chapter_no`` 重跑该章）旧键里**没有任何
维度**会变 ⇒ **命中旧装配** ⇒ planner / writer 拿着**改动前的前章尾段**排计划、
写正文，**零告警**。

⇒ 修复 = 单列一个内容指纹维度 ``prev_draft_fp``
（:func:`packages.core.context_engine.cache.previous_draft_fingerprint`），
director / writer **同源同函数** ⇒ 两侧指纹值逐字相同。

突变
----
``PREV-DRAFT-KEY-1``：撤掉 **director** 键里的 ``prev_draft_fp`` 维度
⇒ 前章重出稿后键不变 ⇒ 命中旧装配 ⇒
``tests/unit/test_ctx_d_prev_draft_key.py::test_new_previous_draft_misses_director_cache_and_payload_shows_new_tail``
必红。
``PREV-DRAFT-KEY-2``：撤掉 **writer** 键里的 ``prev_draft_fp`` 维度 ⇒ 同上，
``test_new_previous_draft_misses_writer_cache_and_payload_shows_new_tail`` 必红。

⚠️ **replacement 是「删掉那一行」**（空串）：变量本身仍被上一行赋值、仍被
``cacheable`` 判定引用 ⇒ 模块照常 import ⇒ 红的**原因**是断言失效，而不是
``NameError`` / ``ImportError``（后者会被误记成「捕获」——AGENTS 坑区
「变异改得太狠、把源码改到读不出来 ⇒ 归 INVALID 而不是 CAUGHT」）。

⚠️ **锚点只命中 1 次**：选的是键元组里**单独成行**的 ``        prev_draft_fp,``
——赋值行（4 空格 + ``=``）与 ``cacheable`` 守卫行（``and prev_draft_fp != ...``）
形态都不同 ⇒ 不会误命中；且**不在**任何既有规格的锚点原文里
（``VOL-1`` 锚的是 ``volume_scope,`` + ``project_meta_fp,`` 两行、
``VOL-2`` 锚的是 ``volume_scope,`` + ``title_fp,`` 两行、
``TITLE-KEY-1`` 锚的是 ``        title_fp,`` 单行）⇒ 本条**不打断**既有规格。

判别
----
``tests/unit/test_ctx_d_prev_draft_key.py``（7 例：director / writer 各一条
「前章重出稿 ⇒ 必重装且 payload 换成新稿」+ 两条「未改必命中」反向不变量 +
两条指纹口径/内容级断言）。
"""

MUTATIONS = [
    (
        "PREV-DRAFT-KEY-1",
        "packages/core/context_engine/director_input.py",
        "        prev_draft_fp,\n",
        "",
        "tests/unit/test_ctx_d_prev_draft_key.py",
    ),
    (
        "PREV-DRAFT-KEY-2",
        "packages/core/context_engine/writer_input.py",
        "        prev_draft_fp,\n",
        "",
        "tests/unit/test_ctx_d_prev_draft_key.py",
    ),
]
