"""``scripts/normalize_chapters.py`` 的纯函数部分（dev-b 批次二十）。

为什么只测纯函数
----------------
这个脚本的另一半是**写路径**（``--apply`` 原地改 ``drafts.content``）⇒ 属
**不可逆操作**（AGENTS：不可逆操作须主会话确认）⇒ 本文件**一个字都不碰写路径**，
只测两个纯函数：

- :func:`split_long_paragraph` —— 按句把超长段落切开，**只插换行，一个字不改**；
- :func:`rescale_paragraphs` —— 返回 ``(新文本, 被切开的段数)``，段落之外的换行结构原样保留。

零真实 LLM、零内容仓接触、零 dev 库、**零不可逆操作**：全部是字符串进字符串出。
"""

from __future__ import annotations

from packages.core.quality.wordcount import visible_chars
from scripts.normalize_chapters import rescale_paragraphs, split_long_paragraph

#: 12 个短句，拼成一段「引号内有很多句号」的长引文。
_INNER = "".join(f"第{i}句。" for i in range(1, 13))


def test_short_paragraph_is_returned_unchanged():
    """未超限的段落**逐字原样返回**（不切、不 strip、不加换行）。"""
    para = "短句。"
    assert split_long_paragraph(para, 100) == [para]
    # 边界：可见字数**等于**上限时也不切
    assert visible_chars(para) == 3
    assert split_long_paragraph(para, 3) == [para]


def test_long_paragraph_is_split_at_sentence_boundaries_without_changing_text():
    """超长段落按句切开，且**只插换行、一个字不改**（拼接后逐字还原）。"""
    text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
    segs = split_long_paragraph(text, 20)

    assert len(segs) > 1, "超限段落必须被切开"
    assert "".join(segs) == text, "切开只能是插换行 ⇒ 拼接必须逐字还原"
    for seg in segs:
        assert visible_chars(seg) <= 20, f"切出来的段仍超限：{seg!r}"


def test_unclosed_quote_is_never_a_cut_point():
    """**引号未闭合处不切**（对白可能跨段续写，切错会改变说话人归属）。

    这是 :func:`split_long_paragraph` docstring 明写的安全边界。判别方式**行为化**：
    第一个切点必须落在**闭合引号之后** ⇒ ``segs[0]`` 必含闭合引号 ``”``，
    且包含引号内的**全部**句号。
    """
    quoted = f"“{_INNER}”他说完了。" + "尾巴" * 40
    segs = split_long_paragraph(quoted, 20)

    assert len(segs) > 1, "本用例的前提是这段确实会被切开"
    assert "”" in segs[0], (
        f"第一个切点必须落在闭合引号之后，实得 segs[0]={segs[0]!r}"
    )
    assert segs[0].count("。") == 13, (
        f"引号内的 12 个句号 + 引号后的 1 个必须都在第一段里，实得 {segs[0].count('。')}"
    )
    assert "".join(segs) == quoted, "切开仍只能是插换行"


def test_rescale_paragraphs_counts_split_paragraphs_and_keeps_structure():
    """``rescale_paragraphs`` 返回 ``(新文本, 切开的段数)``，段落外的换行结构原样保留。"""
    long_text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
    text = f"短段。\n\n{long_text}\n\n第三段也很短。"

    out, cut = rescale_paragraphs(text, 20)

    assert cut == 1, "只有中间那段超限 ⇒ 切开的段数应为 1"
    assert out.startswith("短段。\n\n"), "首段与它后面的空行必须原样保留"
    assert out.endswith("\n\n第三段也很短。"), "末段与它前面的空行必须原样保留"
    assert long_text in out.replace("\n", ""), "被切开的内容一个字都不能少"
