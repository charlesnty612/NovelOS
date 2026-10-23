"""``scoring_missing_subscore`` 一号两义：把**结构化判别器**钉成断言。

台账背景（``docs/impl/reviews/remaining-debt-2026-10-23.md`` · ``scoring_missing_subscore``
一号两义）：一个 rule_id 覆盖了两类语义——

- **语义 A「子分缺失」**（``aggregate.compute_overall``）：某个子分是 ``None`` ⇒
  ``message == "{name} 子分缺失"``、**``suggestion is None``**；
- **语义 B「正文读不到」**（``scoring._draft_unavailable_issue``）：草稿表不可用
  ⇒ ``message`` 以「正文读不到」开头、**``suggestion`` 以「不可检：」开头**。

Lead 已裁决：**不拆 rule_id**（拆要改 ``formula_hash`` + 牵动既有测试 + 双 id 兼容），
改为**把判别器钉成断言 + 补两条不变量**。本文件就是那三条断言。

三条断言（各自钉在**真实值**上，不是恒真断言）：

1. :func:`test_suggestion_is_the_structured_discriminator_between_two_semantics`
   —— 判别器本体：A 的 ``suggestion is None``、B 的 ``suggestion`` 以「不可检：」开头；
   并钉住「两类语义在 ``rule_id`` / ``severity`` / ``gate`` 上完全不可区分」
   ⇒ 判别器是**必需**的，不是可有可无的装饰。
2. :func:`test_engine_path_never_produces_the_aggregate_missing_subscore_semantics`
   —— 反向不变量：**生产引擎路径永不产出语义 A**（引擎传给聚合的 7 个子分恒为 int
   ⇒ A 在生产上不可达）。本条是「A 不可达」的**可执行看守**：将来若有人让某个子分
   变成 ``None``，它会红。
3. :func:`test_aggregate_still_produces_the_missing_subscore_semantics`
   —— 正向不变量：直接聚合**仍能**产出语义 A（防「有人把 A 路径删了」）。

⚠️ **本文件不碰生产代码**，只加断言。

⚠️ 合成夹具：``QualityContext`` 是纯 pydantic 模型，``QualityEngine`` 无 DB / 无网络
（只读仓内 schema 文件）⇒ 本文件**不建库、不读活库 ``data/novelos.db``、零真实 LLM
调用**（AGENTS.md §一 硬约束 2）。
"""

from __future__ import annotations

from packages.core.quality.aggregate import compute_overall
from packages.core.quality.engine import QualityEngine
from packages.core.quality.issues import Issue
from packages.core.quality.models import QualityContext
from packages.core.quality.scoring import DRAFT_UNAVAILABLE
from tests.unit.quality.test_engine import _full_delta

RULE_ID = "scoring_missing_subscore"
"""一号两义的那个 rule_id（``issues.BLOCKING_RULES`` 成员 ⇒ 两类语义都是 block 档）。"""

UNAVAILABLE_SUGGESTION_PREFIX = "不可检："
"""语义 B 的**结构化**判别标记（``suggestion`` 前缀；比 ``message`` 文案稳）。"""

AGGREGATE_MESSAGE_SUFFIX = "子分缺失"
"""语义 A 的 ``message`` 后缀（**弱**判别器：钉在文案上，只作辅助证据）。"""

_DRAFT_TEXT = "他把窗子推开，风灌进来，桌上的纸角翻了两下就压住了。"
"""合成正文（正常路径）。"""

_PLAN = {"key_beats": [{"summary": "在山间遭遇山贼"}]}
"""合成 plan：让 ``plot`` 子分不打 ``RULE_PLOT_NO_PLAN``（正常路径是「健康」运行）。"""


def _healthy_ctx(draft: str, *, chapter_id: str = "ch_missing_subscore_probe") -> QualityContext:
    """健康合成上下文：合法 delta + 合法 plan，**只有正文是变量**。

    正常正文 ⇒ 报告 ``overall > 0``（真的评过一遍）；``DRAFT_UNAVAILABLE`` ⇒
    「读不到」那一支。两条路径只差正文 ⇒ 差异可归因。
    """
    return QualityContext(
        chapter_id=chapter_id,
        chapter_number=1,
        draft=draft,
        plan=_PLAN,
        delta=_full_delta(chapter_id),
    )


def _missing_issue(issues: list[Issue]) -> list[Issue]:
    """挑出该 rule_id 的 issue（按 rule_id 精确匹配，不靠 severity / category 过滤）。"""
    return [i for i in issues if i.rule_id == RULE_ID]


# ---------------------------------------------------------------------------
# 1) 核心判别器：两类语义的 ``suggestion`` 必须可区分
# ---------------------------------------------------------------------------


def test_suggestion_is_the_structured_discriminator_between_two_semantics():
    """语义 A 的 ``suggestion is None``；语义 B 的 ``suggestion`` 以「不可检：」开头。

    判别力落在**结构化字段**上：``suggestion`` 是 ``Issue`` 的正式字段（不是文案拼接），
    客户端按它判别不需要解析中文串。

    同时钉住「判别器是**必需**的」：两类语义在 ``rule_id`` / ``severity`` / ``gate``
    上完全一致 ⇒ 只看 rule_id 无法分辨。
    """
    # --- 语义 A：直接调 compute_overall，造一个 None 子分 ---
    overall_a, issues_a = compute_overall(
        {
            "plot": 85,
            "character": 100,
            "continuity": 100,
            "style": None,
            "pacing": 100,
            "foreshadowing": 100,
            "ai_trace": 100,
        },
        [],
    )
    a_issues = _missing_issue(issues_a)
    assert len(a_issues) == 1, (
        f"语义 A 应恰好一条，实际 {len(a_issues)} 条：{[i.message for i in a_issues]}"
    )
    a = a_issues[0]
    # 用 `is None`（不是 falsy）：空串 / 缺字段都不算「无建议」。
    assert a.suggestion is None, f"语义 A 的 suggestion 必须是 None，实际 {a.suggestion!r}"
    assert a.message == "style 子分缺失", a.message
    assert overall_a == 0, overall_a

    # --- 语义 B：引擎跑「正文读不到」那一支 ---
    report_b = QualityEngine().evaluate(_healthy_ctx(DRAFT_UNAVAILABLE))
    b_issues = _missing_issue(report_b.issues)
    assert len(b_issues) == 2, (
        f"语义 B 应恰好两条（style + pacing 两路），实际 {len(b_issues)} 条："
        f"{[i.message for i in b_issues]}"
    )
    for it in b_issues:
        assert it.suggestion is not None, f"语义 B 的 suggestion 不得为 None：{it.message}"
        assert it.suggestion.startswith(UNAVAILABLE_SUGGESTION_PREFIX), (
            f"语义 B 的 suggestion 必须以「{UNAVAILABLE_SUGGESTION_PREFIX}」开头，"
            f"实际 {it.suggestion!r}"
        )

    # --- 判别器**必要**：两类语义在 rule_id / severity / gate 上不可区分 ---
    b_style = next(i for i in b_issues if i.category == "style")
    assert a.rule_id == b_style.rule_id == RULE_ID
    assert a.severity == b_style.severity == "error"
    assert a.gate == b_style.gate == "block"
    # B 的另一条（pacing）同样是 error + block ⇒ 不是「某一条特殊」。
    assert all(i.severity == "error" and i.gate == "block" for i in b_issues)


# ---------------------------------------------------------------------------
# 2) 反向不变量：引擎路径永不产出语义 A
# ---------------------------------------------------------------------------


def test_engine_path_never_produces_the_aggregate_missing_subscore_semantics():
    """**生产引擎路径永不产出语义 A「子分缺失」。**

    依据（Lead 已读码实证、本文件已复验）：``engine.evaluate`` 传给
    ``compute_overall_with_gates`` 的 7 个子分是**硬编码的 int**（``score_*`` 与
    ``compute_ai_trace`` 的返回值恒为 int，0 也不算 None）⇒ 语义 A 在生产上不可达，
    只在「直接调 ``compute_overall``」时出现。

    本条是它的**可执行看守**：将来若有人让某个子分变成 ``None``（例如把某个
    ``score_*`` 改成失败返回 ``None``），这里会红。

    断言精确到「能区分正确与退化」：数量**恰好** 2（不是 ``>= 1``）、category
    **恰好** {style, pacing}、且**每一条**都不是「子分缺失」那一支。
    """
    # ① 正常正文 ⇒ 真的评过一遍（overall > 0），且**零**条该 rule_id
    report_ok = QualityEngine().evaluate(_healthy_ctx(_DRAFT_TEXT))
    assert report_ok.overall > 0, report_ok.overall
    ok_issues = _missing_issue(report_ok.issues)
    assert ok_issues == [], f"正常正文不得产出该 rule_id，实际 {[i.message for i in ok_issues]}"

    # ② 正文读不到 ⇒ 恰好两条（style + pacing 两路），且没有一条是语义 A
    report_unavailable = QualityEngine().evaluate(_healthy_ctx(DRAFT_UNAVAILABLE))
    mine = _missing_issue(report_unavailable.issues)
    assert len(mine) == 2, (
        f"应恰好两条（style + pacing 两路），实际 {len(mine)} 条：{[i.message for i in mine]}"
    )
    assert sorted(i.category for i in mine) == ["pacing", "style"], sorted(
        i.category for i in mine
    )
    for it in mine:
        assert not it.message.endswith(AGGREGATE_MESSAGE_SUFFIX), (
            f"引擎路径产出了语义 A（子分缺失）：{it.message}"
        )
        assert it.suggestion is not None, it.message
        assert it.suggestion.startswith(UNAVAILABLE_SUGGESTION_PREFIX), it.suggestion


# ---------------------------------------------------------------------------
# 3) 正向不变量：直接聚合仍能产出语义 A
# ---------------------------------------------------------------------------


def test_aggregate_still_produces_the_missing_subscore_semantics():
    """直接聚合**仍能**产出语义 A（防「有人把 A 路径删了 / 改文案」）。

    语义 A 是 ``aggregate.compute_overall`` 的**公开契约**（docstring 规则 3：
    「任一子分缺失（None）⇒ push 一条 ``scoring_missing_subscore`` issue」），
    删掉它等于静默改变 API 行为 ⇒ 必须有看守。
    """
    overall, issues = compute_overall(
        {
            "plot": 85,
            "character": 100,
            "continuity": 100,
            "style": None,
            "pacing": 100,
            "foreshadowing": 100,
            "ai_trace": 100,
        },
        [],
    )
    mine = _missing_issue(issues)
    assert len(mine) == 1, (
        f"语义 A 应恰好一条，实际 {len(mine)} 条：{[i.message for i in mine]}"
    )
    assert mine[0].message == "style 子分缺失", mine[0].message
    assert mine[0].suggestion is None, mine[0].suggestion
    assert mine[0].severity == "error", mine[0].severity
    assert mine[0].gate == "block", mine[0].gate
    assert overall == 0, overall
