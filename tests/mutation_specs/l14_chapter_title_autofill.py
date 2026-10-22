"""章名自动回填的四条突变（2026-10-23 批次二十五）。

缺陷形状
--------
用户新建章节 → 生成计划后章名永远为空：
* ``chapter_plan/pipeline.py::_save_plan_node`` 的 ``plan_payload`` 白名单**没有 title**
  ⇒ 模型给的章名被静默丢弃；
* 该节点只写 ``plan_json``，**从不写 ``chapters.title``** ⇒ 没有落库路径。
本批（v4 提示词 + save_plan 白名单/回填）补上两段，各配一条突变。

四条突变各钉一个失效方向
------------------------
* **TTL-1** 撤掉回填（``if planner_title is not None:`` → ``if False:``）⇒ 空标题章
  拿不到章名 ⇒ ``test_empty_title_is_backfilled`` 必红。
* **TTL-2** 撤掉「仅空标题」的 WHERE 守卫（无条件覆盖）⇒ 作者已填标题被模型改写
  （**过修**）⇒ ``test_author_title_is_never_overwritten`` 必红。
* **TTL-3** 从 ``plan_payload`` 白名单删掉 ``"title"`` ⇒ ``plan_json`` 读不到章名
  ⇒ ``test_plan_json_carries_planner_title`` 必红。
* **TTL-4** 从 ``director_planner-v4.md`` 的 §7 输出契约删掉 ``"title"`` 声明
  ⇒ 提示词不再教模型产出 ⇒ ``test_prompt_v4_declares_title_in_output_schema`` 必红
  （钉的是**契约本身**，不是实现——v3 没有该键，是本批升版加的）。

锚点说明
--------
* TTL-1/2/3 的锚点都在 ``pipeline.py`` 的 save_plan 内，彼此**不重叠**（各自只命中 1 次）；
* TTL-4 的锚点带 **2 空格缩进的 §7 形态**（``  "chapter_id": "string",`` + ``  "title"``），
  与 §5 输入契约的 4 空格缩进形态不同 ⇒ 不会误命中输入契约那行；
* replacement 一律不含双引号（Windows PowerShell 5.1 向原生程序传 argv 会剥内嵌引号，
  见 ``q1_style_sample_key.py`` 的留痕；本仓执行器虽从文件读，仍统一遵守）。
"""

MUTATIONS = [
    (
        "TTL-1",
        "packages/workflows/chapter_plan/pipeline.py",
        "        if planner_title is not None:\n",
        "        if False:  # MUTATION-TTL-1: 撤回填\n",
        "tests/unit/test_chapter_title_autofill.py",
    ),
    (
        "TTL-2",
        "packages/workflows/chapter_plan/pipeline.py",
        '                "UPDATE chapters SET title = ? "\n'
        '                "WHERE chapter_id = ? AND (title IS NULL OR "\n'
        '                "TRIM(title, \' \' || CHAR(9) || CHAR(10) || CHAR(13)) = \'\')",\n',
        '                "UPDATE chapters SET title = ? "\n'
        '                "WHERE chapter_id = ?",  # MUTATION-TTL-2: 撤空标题守卫（无条件覆盖）\n',
        "tests/unit/test_chapter_title_autofill.py",
    ),
    (
        "TTL-3",
        "packages/workflows/chapter_plan/pipeline.py",
        '        "title": director_output.get("title"),\n',
        "",
        "tests/unit/test_chapter_title_autofill.py",
    ),
    (
        "TTL-4",
        "docs/agents/prompts/director_planner-v4.md",
        '  "chapter_id": "string",\n  "title": "string 或 null",\n',
        '  "chapter_id": "string",\n',
        "tests/unit/test_chapter_title_autofill.py",
    ),
]
