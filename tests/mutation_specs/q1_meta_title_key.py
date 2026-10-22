"""缓存键第 5 次：撤掉「项目元信息」/「本章标题」维度 ⇒ 必红（dev-b 批次二十一 P0-1）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 5 次**）：
``projects.name/genre/premise`` 进 **director** payload、``chapters.title`` 进
**writer** payload，而这两个写路径（``PATCH /api/projects/{id}`` /
``PATCH /api/chapters/{id}``）**都不推 ``state_version``** ⇒ 旧键无任何维度会因
它们而变 ⇒ 同 state_version + 同 chapter_no 重跑该章**命中旧装配** ⇒
**planner 拿着改动前的 premise 排计划 / 正文按改动前的标题写，零告警**。

两条突变各钉一个失效方向
------------------------
* **META-KEY-1** 撤掉 director 键的 ``project_meta_fp`` 维度 ⇒ 改 premise 后
  键不变 ⇒ ``test_changing_project_premise_misses_director_cache`` 必红
  （它断言「重装次数 + payload 里的新 premise」两件事）。
* **TITLE-KEY-1** 撤掉 writer 键的 ``title_fp`` 维度 ⇒ 改章标题后键不变 ⇒
  ``test_changing_chapter_title_misses_writer_cache`` 必红。

⚠️ **replacement 是「删掉那一行」**（空串）：变量本身仍被上一行赋值、仍被
``cacheable`` 判定引用 ⇒ 模块照常 import ⇒ 红的**原因**是断言失效，而不是
``NameError`` / ``ImportError``（后者会被误记成「捕获」——AGENTS 坑区
「变异改得太狠、把源码改到读不出来 ⇒ 归 INVALID 而不是 CAUGHT」）。

⚠️ **锚点只命中 1 次**：两处的锚点都是「**单独成行**的维度名」——
``        project_meta_fp,`` / ``        title_fp,``；赋值行与 ``cacheable`` 守卫行
的形态都不同（``    project_meta_fp = ...`` / ``        and title_fp != ...``）
⇒ 不会误命中。⚠️ 另：这两个维度**刻意单独成行**而不是并进相邻行，正是为了
**不打断**既有规格 ``E1-1`` / ``E1-2`` / ``SKILL-KEY-1`` / ``SKILL-KEY-2`` /
``STYLE-KEY-1`` 的锚点原文（AGENTS 坑区「改一个模块会打断既有突变规格的锚点
⇒ 执行器会 [ABORT] 或误判」）。

判别
----
``tests/unit/test_ctx_b_meta_title_key.py``（5 例：改 premise 必 miss / 未改必命中
/ 改标题必 miss / 未改必命中 / **preview 路径同样吃到新维度**）。
"""

MUTATIONS = [
    (
        "META-KEY-1",
        "packages/core/context_engine/director_input.py",
        "        project_meta_fp,\n",
        "",
        "tests/unit/test_ctx_b_meta_title_key.py",
    ),
    (
        "TITLE-KEY-1",
        "packages/core/context_engine/writer_input.py",
        "        title_fp,\n",
        "",
        "tests/unit/test_ctx_b_meta_title_key.py",
    ),
]
