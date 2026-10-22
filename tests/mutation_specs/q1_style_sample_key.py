"""作者文风样例的缓存键维度：撤掉 ``style_fp`` ⇒ 必红（dev-b 批次十九）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 4 次**）：

- 0028 ``outline_json`` 进 payload 却没进键 ⇒ 改大纲后同 state_version 脏命中；
- 2026-09-16 ``prompt_label`` 硬编码 ⇒ 升版后键与 payload 脱节；
- 2026-10-16 E1 ``chars.core_json`` 进 payload 却没进键 ⇒ 改角色设定后脏命中；
- **本批** ``author_style_samples.samples`` 进 writer payload 却没进键。

失效链（逐条可复算）：

- writer 装配把项目最近 ≤2 篇「作者本人散文样例」注入 payload 的
  ``author_style_samples.samples`` 段（``writer_input.py`` 的
  ``_build_writer_input_uncached``；数据源
  ``builders_common/summaries.py::_author_style_samples``）；
- 作者新增 / 删除样例走 ``api/routers/author_style_samples.py`` 的
  INSERT（:164-172）/ DELETE（:193-198）—— **只写该表，不推
  ``story_states.state_version``**；
- ⇒ 修前 writer 键 20 元里没有任何一维会因样例增删而变 ⇒ 同
  ``state_version`` + 同 ``chapter_no`` 重跑该章**命中旧装配** ⇒
  正文不按作者文风写，**零告警**。

突变
----
``STYLE-KEY-1``：把 writer 键里的 ``style_fp`` 换成常量 ⇒ 样例增删不再影响键
⇒ 命中旧装配 ⇒ ``tests/unit/test_ctx_b_style_sample_key.py`` 必红。

⚠️ **replacement 必须不破坏 import**：本条的 replacement 是**就地换元**（把
``style_fp`` 换成常量），``style_fp`` 变量本身仍被上一行赋值、仍被 ``cacheable``
判定引用 ⇒ 模块照常 import ⇒ 红的**原因**是断言失效，而不是 ``ImportError``
（后者会被误记成「捕获」——AGENTS 坑区「变异改得太狠、把源码改到读不出来 ⇒
归 INVALID 而不是 CAUGHT」）。

⚠️ **replacement 刻意不含任何双引号**（用 ``None`` 而不是 ``"__none__"``）：
**Windows PowerShell 5.1 向原生程序传 argv 时会剥掉内嵌的双引号**——实测
``python -c "import sys; print(repr(sys.argv[1]))" 'x "__none__" y'`` 收到的是
``'x __none__ y'``（引号没了）⇒ 含引号的 replacement 会被静默变形成一个
**未定义名字** ⇒ 运行期 ``NameError`` ⇒ **全部**用例变红 ⇒ 报出「捕获得很彻底」
的假结论，而红的原因根本不是被测的那个机制（AGENTS 坑区「量具自身的缺陷伪装成
执行结果」的新场景）。用 ``None``（无引号、且不依赖任何上下文）⇒ 无论从本
规格文件还是从命令行 argv 重新施加，变异体逐字相同。

⚠️ **锚点只命中 1 次**：选的是 writer 键元组的第 2 行
（``scene_fp, …, active_canon_id, style_fp,``）——它**不在**
``e1_character_cache_key.py::E1-1`` 与 ``l7_skill_cache_key.py::SKILL-KEY-2``
的锚点原文里（那两条锚的是 ``target_word_count…chars_fp,`` 与
``world_fp…prompt_label,`` 两行）⇒ 本次加维度**没有打断既有规格的锚点**
（AGENTS 坑区「改一个模块会打断既有突变规格的锚点」）。

判别
----
``tests/unit/test_ctx_b_style_sample_key.py``：
- ``test_adding_style_sample_misses_cache_and_payload_shows_it``（加样例 ⇒ 必重装
  且 payload 看得见新样例）；
- ``test_deleting_style_sample_misses_cache_and_payload_drops_it``（删样例 ⇒ 必重装
  且 payload 里不再有它）；
- ``test_editing_style_sample_content_misses_cache``（改内容 ⇒ 必重装）；
- 反向不变量：``test_unchanged_style_samples_still_hit_cache`` /
  ``test_touching_only_updated_at_still_hits_cache`` /
  ``test_other_project_style_samples_do_not_affect_key``（样例未变时**必须命中**，
  防「过度失效」把缓存改成永不命中）。
"""

MUTATIONS = [
    (
        "STYLE-KEY-1",
        "packages/core/context_engine/writer_input.py",
        "        scene_fp, context_mode, relevance_flag, wb_fp, active_canon_id, style_fp,\n",
        "        scene_fp, context_mode, relevance_flag, wb_fp, active_canon_id, None,\n",
        "tests/unit/test_ctx_b_style_sample_key.py",
    ),
]
