"""skill 的缓存键维度：撤掉 ``skills_fp`` ⇒ 必红（内部消费外部 skill · 0032）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形第三次**）：

- 0028 ``outline_json`` 进 payload 却没进键 ⇒ 改大纲后同 state_version 脏命中；
- 2026-10-16 E1 ``chars.core_json`` 进 payload 却没进键 ⇒ 改角色设定后脏命中；
- **本批** ``skills`` 段进 payload 却没进键 ⇒ 换 skill / 改 skill payload
  （PUT ⇒ version 自增）/ 改绑定集合后，同 state_version 重跑命中旧装配 ⇒
  writer 拿着改之前的旧手艺写正文、director 拿着旧手艺规划本章。

两条突变各钉一个失效方向
------------------------
* **SKILL-KEY-1** 撤掉 director 键的 ``skills_fp`` 维度 ⇒ 绑定 skill 前后键**不变**
  ⇒ ``test_director_cache_key_has_skills_dimension`` 必红（它断言「只差一个位置」）。
* **SKILL-KEY-2** 撤掉 writer 键的 ``skills_fp`` 维度 ⇒ 同上，
  ``test_writer_cache_key_also_has_skills_dimension`` 必红。

⚠️ 两条都**不是**「删字段声明」那种改字节不改行为的弱变异：删掉的是**键的一元**，
键的元数从 15 变 14 / 18 变 17 ⇒ 判别用例里的「键元数」与「只差一个位置」两条
断言同时具备判别力。

判别
----
``tests/unit/test_skill_injection.py``：
- ``test_director_cache_key_has_skills_dimension``（绑定前后键只差一个位置）；
- ``test_writer_cache_key_also_has_skills_dimension``（同款）；
- ``test_changing_skill_payload_misses_director_cache``（改 payload ⇒ 必须重装）。
"""

MUTATIONS = [
    (
        "SKILL-KEY-1",
        "packages/core/context_engine/director_input.py",
        "        active_canon_id, intent_fp, bible_fp, target_word_count, genre_pack_ref,\n"
        "        chars_fp, skills_fp, prompt_label,\n",
        "        active_canon_id, intent_fp, bible_fp, target_word_count, genre_pack_ref,\n"
        "        chars_fp, prompt_label,\n",
        "tests/unit/test_skill_injection.py",
    ),
    (
        "SKILL-KEY-2",
        "packages/core/context_engine/writer_input.py",
        "        target_word_count, genre_pack_ref, intent_fp, bible_fp, chars_fp,\n"
        "        plan_fp, skills_fp, prompt_label, _cache_namespace_tag(namespace),\n",
        "        target_word_count, genre_pack_ref, intent_fp, bible_fp, chars_fp,\n"
        "        plan_fp, prompt_label, _cache_namespace_tag(namespace),\n",
        "tests/unit/test_skill_injection.py",
    ),
]
