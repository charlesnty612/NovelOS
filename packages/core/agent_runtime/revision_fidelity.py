"""改稿保真度测量（revise 契约的机器强约束，2026-09-21「改稿审计断链」检修批次）。

背景（AGENTS.md 坑区「改稿轮悄悄重写未提及段落，人工只能全文 diff 才发现」）：
writer 的 revise 契约（writer-v3.md §6.1「未提及部分逐字保留 / 越界必申报」）此前
只有 prompt 纪律、零机器约束——2026-09-21 实证 writer 在被明确指示「禁止改动打脸
段落」时仍整段重写，且 ``self_report.deviations`` 零申报。

本模块提供唯一测量单点 :func:`preserved_ratio`（上游稿 → 改稿的字符级保留比），
由 ``structured_output.validate_contract`` 的 writer 分支消费：

- 输入 ``mode == "revise"`` 且实测 ``ratio < REVISE_MIN_PRESERVED_RATIO`` 且
  ``self_report.deviations`` 为空/缺失 → 抛 :class:`AgentOutputError`
  （runner 既有 output-invalid 路径自动重试 1 次，重试提示携带实测比值与两条
  合规路径：真做定向局部修改，或申报实际改动）；申报非空 → 放行（申报本身进
  self_report / revision_checklist 审计面，越界改动的追责由人工相似度 diff 承担）。
- mode='write' / 无上游稿 / 体量不成比例的输出不进本闸门（见
  :data:`REVISE_FIDELITY_MIN_LENGTH_FRACTION` 的 docstring）。

阈值定锚（实测依据见 ``scripts/revise_fidelity_calibrate.py``，2026-09-26 dev 库）：

- **闸门管辖人群**（输入 mode='revise'，从历史 ``workflow_runs.checkpoint_json``
  的 ``writer_input`` 还原，n=18）：min 0.6463 / p10 0.7515 / p25 0.8424 /
  p50 0.9344 / max 0.9865——真实定向改稿全部 ≥ 0.64。
- **静默整章重写簇**（fresh_write / 欠带翻模轮，运行时 mode='write' 本就不归本
  闸门管，作为比值空间参照）：相邻版本对比值 bulk ≤ 0.16（p25 0.1752）。
- **事故章 ch_92bac068ff0d 的逃逸轮**（v5→v6=0.5981 / v7→v8=0.7417）：+88% / +65%
  的增幅在 writer 规则 20（净增 ≤ +5%）下不可能是合规 revise，运行时走的是
  write 翻模路径，闸门不适用。
- **尾块权重**：REVISION-CHECKLIST 尾块是机读元数据，比对前必须剥离（60 字样板
  实测含尾块 0.417 → 剥后 0.845）。

⇒ 定值 **0.50**：低于闸门管辖人群实测下沿 0.6463 约 0.146（覆盖运行时残差），
高于静默重写簇上沿（bulk 0.16）约 0.32，两侧留量均衡；比初始猜测 0.55 更保守
（契约拒绝会烧一次真 LLM 重试，宁可少拦误报）。
"""

from __future__ import annotations

from difflib import SequenceMatcher

# ---REVISION-CHECKLIST--- 尾块分隔行（canonical 定义）。
# pipeline（workflows 层）的 ``_REVISION_CHECKLIST_MARKER`` 是本常量的别名引用；
# 契约层比对保真度前必须先剥掉该尾块：它是机读元数据不是散文，计入会把短章
# 比值显著拉低（60 字样板实测 0.845 → 0.417）。
REVISION_CHECKLIST_MARKER = "---REVISION-CHECKLIST---"

REVISE_MIN_PRESERVED_RATIO: float = 0.50
"""revise 输出的最低字符保留比（低于它且零申报 → 契约拒绝）。定锚依据见模块 docstring。"""

REVISE_FIDELITY_MIN_LENGTH_FRACTION: float = 0.1
"""体量前置守卫：改稿正文不足上游稿 10% 时不进保真闸门。

理由：preserved_ratio 对长度悬殊的文本没有「保真」语义——60KB 旧稿配 24 字产出
（checkpoint 体积测试的 mock 形态）比值恒为 0，但那不是「静默整段重写」，是
「没干活」，由字数带闭环（length_check / 生成期欠带重写）接管。本闸门只管
「同量级产出的静默重写」。
"""


def strip_revision_checklist_tail(prose: str) -> str:
    """剥离 revise 输出尾部的 REVISION-CHECKLIST 尾块（分隔行起全切，坏 JSON 同样切）。

    与 pipeline 的 ``_parse_revision_checklist`` 切分边界一致（首处分隔行即契约边界），
    但本函数只管「给保真比对喂散文」，不解析 JSON、不做 rstrip（比对两端同策略即可）。
    """
    if not isinstance(prose, str) or REVISION_CHECKLIST_MARKER not in prose:
        return prose
    return prose.split(REVISION_CHECKLIST_MARKER, 1)[0]


def preserved_ratio(upstream: str, revised: str) -> float:
    """上游稿 → 改稿的字符级保留比（``difflib.SequenceMatcher.ratio`` 口径）。

    - ``upstream`` 为空 → 1.0（无从比较不拦——「没有上游稿」不是本闸门的管辖形状）；
    - ``revised`` 为空且 upstream 非空 → 0.0（全量丢弃是最重的失真）；
    - 非字符串入参 → 1.0（调用方上游已做类型守卫，防御性放行）。

    性能：先取 :meth:`SequenceMatcher.real_quick_ratio` / :meth:`quick_ratio` 两个
    O(n+m) 廉价上界做快速淘汰——二者归零 ⇒ 两串无任何公共字符 ⇒ ``ratio()`` 恒为 0
    （**精确**短路，可安全跳过 O(n²) 全量计算）；非零时上界不构成实测值，落全量
    ``ratio()``（契约报错文案必须报实测值，不报上界）。CJK 长文（~8000 字）实测
    远低于 1s（autojunk 默认对高频字剪枝）。
    """
    if not isinstance(upstream, str) or not isinstance(revised, str):
        return 1.0
    if not upstream:
        return 1.0
    if not revised:
        return 0.0
    sm = SequenceMatcher(None, upstream, revised)
    # 廉价上界快速淘汰（上界 ≥ ratio()；归零 ⇒ ratio() 必为 0，精确短路）
    if sm.real_quick_ratio() <= 0.0 or sm.quick_ratio() <= 0.0:
        return 0.0
    return sm.ratio()


__all__ = [
    "REVISION_CHECKLIST_MARKER",
    "REVISE_MIN_PRESERVED_RATIO",
    "REVISE_FIDELITY_MIN_LENGTH_FRACTION",
    "strip_revision_checklist_tail",
    "preserved_ratio",
]
