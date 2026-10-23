"""提示词自愈与章名可观测的三条突变（2026-10-23 章名不生成事故修）。

缺陷形状
--------
``prompts`` 表是 ``docs/agents/prompts/*.md`` 的**运行时镜像**，但只在**服务启动时**
同步一次 ⇒ 长驻服务不重启时，仓内新增的提示词版本**永远到不了运行时**。
实测事故：活库 director_planner 停在 v2（v2/v3 的 §7 **都没有**顶层 ``title``）
⇒ 模型产不出章名 ⇒ 落库侧回填的 UPDATE 根本不执行 ⇒ 「新增章节 → 生成计划 →
章名不生成」，且**全程零告警**。

本批修两段，各配一条突变
------------------------
* **自愈**（``agent_runtime/prompts.py`` + ``runner.py``）：DB 镜像落后于仓内源码时
  自动推进到最新版本，但**只在 DB 是仓内文件的忠实镜像时才动**；
* **可观测**（``chapter_plan/pipeline.py``）：章名回填的三种结局各留痕迹，
  「模型没给名」不再静默。

三条突变各钉一个失效方向
------------------------
* **SH-1** 撤掉「忠实镜像守卫」（``if current_file.read_text(...) != before_content:``
  → ``if False:``）⇒ 人工改写过的提示词会被新版**覆盖**（数据丢失类）
  ⇒ ``test_customized_prompt_row_is_never_overwritten`` 必红。
* **SH-2** 撤掉 runner 里自愈后的「重新取用」（``if refreshed is not None:`` →
  ``if False:``）⇒ DB 推进了但**下发的仍是旧版提示词**（调用记录里的版本号说谎）
  ⇒ ``test_run_agent_uses_advanced_prompt_version`` 必红。
* **SH-3** 把「模型没给名」谎报成成功（``title_status = "unavailable"`` →
  ``"planner"``）⇒ 静默失败复发、告警消失
  ⇒ ``test_title_backfill_status_is_unavailable_and_warns`` 必红。

为什么这三条值得写突变规格（AGENTS §三 第 7 条的三类条件）
----------------------------------------------------------
SH-1 / SH-2 命中「跨域/跨层且极易被好心改错」：自愈横跨「仓内源码 → DB 镜像 →
模型调用」三层，一旦有人把守卫删掉，**用户手工改过的提示词会被静默覆盖**，
而这类覆盖没有任何测试会主动报出来。SH-3 命中「门禁」：告警消失 = 事故再次静默。
"""

MUTATIONS = [
    (
        "SH-1",
        "packages/core/agent_runtime/prompts.py",
        '            if current_file.read_text(encoding="utf-8") != before_content:\n',
        "            if False:  # MUTATION-SH-1: 撤掉忠实镜像守卫（会覆盖人工改写）\n",
        "tests/unit/test_prompt_selfheal.py",
    ),
    (
        "SH-2",
        "packages/core/agent_runtime/runner.py",
        "            if refreshed is not None:\n",
        "            if False:  # MUTATION-SH-2: 撤掉自愈后的重新取用（下发的仍是旧版）\n",
        "tests/unit/test_prompt_selfheal.py",
    ),
    (
        "SH-3",
        "packages/workflows/chapter_plan/pipeline.py",
        '    elif planner_title is None:\n        title_status = "unavailable"\n',
        '    elif planner_title is None:\n        title_status = "planner"  # MUTATION-SH-3: 谎报成功\n',
        "tests/unit/test_chapter_title_autofill.py",
    ),
]
