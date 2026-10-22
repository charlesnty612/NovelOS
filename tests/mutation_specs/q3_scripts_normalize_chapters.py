"""``normalize_chapters.py`` 的纯函数：撤掉「引号深度守卫」/「不切短段」⇒ 必红（dev-b 批次二十）。

背景
----
``scripts/normalize_chapters.py`` 的写路径（``--apply`` 原地改 ``drafts.content``）
属**不可逆操作** ⇒ 本规格**只碰两个纯函数**：

- :func:`split_long_paragraph` —— 按句切开超长段落，**只插换行，一个字不改**；
  docstring 明写安全边界：「**引号未闭合的位置不切**（对白可能跨段续写，切错了
  会改变说话人归属）」。
- :func:`rescale_paragraphs` —— 返回 ``(新文本, 被切开的段数)``。

两条突变各钉一个失效方向
------------------------
* **MUT-NORM-1** 撤掉切点判据里的 ``and depth == 0`` ⇒ 引号**内部**的句号也成了
  候选切点 ⇒ 切点会落在**未闭合的引号内部** ⇒
  ``test_unclosed_quote_is_never_a_cut_point`` 必红（它断言「第一个切点必须落在
  闭合引号之后」）。
  ⚠️ 危害是**语义**的：把对白拆到两段会改变说话人归属（脚本 docstring 的原话）。
* **MUT-NORM-2** 让「未超限就原样返回」这条分支**恒真** ⇒ 任何段落都不再被切 ⇒
  ``test_long_paragraph_is_split_at_sentence_boundaries_without_changing_text``、
  ``test_unclosed_quote_is_never_a_cut_point``、
  ``test_rescale_paragraphs_counts_split_paragraphs_and_keeps_structure`` 必红
  （长段不再被切 ⇒ 本脚本在生产上**静默不干活**）。

判别
----
``tests/unit/test_script_normalize_chapters.py``（4 例，纯字符串进字符串出
⇒ **零不可逆操作**、零 dev 库、零真实 LLM）。
"""

MUTATIONS = [
    (
        "MUT-NORM-1",
        "scripts/normalize_chapters.py",
        "        elif ch in _SENT_END and depth == 0:\n",
        "        elif ch in _SENT_END:\n",
        "tests/unit/test_script_normalize_chapters.py",
    ),
    (
        "MUT-NORM-2",
        "scripts/normalize_chapters.py",
        "    if visible_chars(para) <= limit:\n        return [para]\n",
        "    if True:\n        return [para]\n",
        "tests/unit/test_script_normalize_chapters.py",
    ),
]
