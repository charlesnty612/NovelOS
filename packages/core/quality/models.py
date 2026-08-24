"""Pydantic 模型：Quality Engine 上下文与报告（packages.core.quality）。

对齐 ``docs/evaluation/quality-scoring-v0.md`` §1.1 八字段 + §1.3 issues + §2.2 _meta。

设计要点：

- 使用 pydantic BaseModel 与 ``packages/domain/chapter/models.py`` /
  ``packages/domain/character/models.py`` 保持一致风格；Report / Context 都是面向调用方的
  强类型数据，便于序列化与回归 diff。
- Issue 字段集合与 :mod:`.issues` 中的 dataclass 完全等价；前者给 pydantic 序列化（dump /
  JSON）使用，后者给规则内部构造使用（避开 pydantic 校验的 import-cost 与循环）。
- QualityContext 中所有集合/字典字段默认空（Service / 调用方填值）。
- ``_meta`` 在 Report 中作为 ``dict``（不强制 shape）——engine 写入时保证 4 个固定键：
  scoring_version / llm_judge / evaluated_at / scoring_formula_hash。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .issues import Category, Severity


# ----------------------------------------------------------------------------- Issue


class Issue(BaseModel):
    """Quality Issue（与 spec §1.3 字段一一对应）。

    字段：
        severity: ``error`` / ``warning`` / ``info``。
        category: 13 个枚举值之一，与 Guardrail / 子分 / 爽感维度对齐。
        location: 形如 ``"<chapter_id>"`` 或 ``"<chapter_id>:<scene_id>"``，scene 缺则退化。
        rule_id: ``RULE_<NAME>`` 形式（错误/缺失/特殊规则允许其他前缀，如
            ``SCHEMA_VALIDATION_FAILED`` / ``scoring_missing_subscore``）。
        message: 人类可读的一句话。
        suggestion / evidence_refs / judge_trace: 可选。
    """

    severity: Severity
    category: Category
    location: str = "<unknown>"
    rule_id: str
    message: str
    suggestion: Optional[str] = None
    evidence_refs: Optional[list[str]] = None
    judge_trace: Optional[dict[str, Any]] = None


# ----------------------------------------------------------------------------- QualityContext


class QualityContext(BaseModel):
    """Quality 评估的输入上下文。

    字段（多引用 spec §1.2 / §3.7）：
        chapter_id / chapter_number: 章节身份与序号。
        draft: 章节正文（UTF-8 str）。
        plan: ``chapter_plan`` 的 dict 形态；plot 子分读 ``key_beats``。
        snapshot_pre: 提交前 Canonical State dict（取自 Story State service 的
            ``build_initial_state`` / ``materialize_snapshot`` 输出）。
        delta: Observer 给出的 State Delta dict（顶 7 数组 + 元信息）。
        payoff_history: 最近若干章每章 payoff 计数（resolved_hooks + paid debts 数）。
        reference_texts / whitelist: REQ-Q6 参照书与白名单。
        ai_chars / human_chars: REQ-Q8 字符数。
        commit_id / run_id: 仅做报告回显。
    """

    chapter_id: str
    chapter_number: int
    draft: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    snapshot_pre: dict[str, Any] = Field(default_factory=dict)
    delta: dict[str, Any] = Field(default_factory=dict)
    payoff_history: list[int] = Field(default_factory=list)
    reference_texts: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    ai_chars: int = 0
    human_chars: int = 0
    # ai_trace 跨章子信号：同项目最近 N 章正文（章节号降序）。
    # service / pipeline 现场拉取后传入；engine 不直接读 DB。
    previous_drafts: list[str] = Field(default_factory=list)
    commit_id: Optional[str] = None
    run_id: Optional[str] = None


# ----------------------------------------------------------------------------- QualityReport


class QualityReport(BaseModel):
    """Quality 评估输出（spec §1.1 + §2.2）。

    八字段（``overall / plot / character / continuity / style / pacing / foreshadowing /
    issues``）+ ``_meta`` + 回显字段（``report_id / chapter_id / chapter_number /
    commit_id / run_id``）。

    pydantic v2 限制字段名不能以下划线开头，因此 ``_meta`` 用
    ``Field(alias='_meta')`` 暴露为序列化别名；Python 端属性名仍为 ``meta``。
    """

    model_config = {"populate_by_name": True}

    # 九字段（七子分 + overall + issues；ai_trace 与 plot/character/.../foreshadowing 并列）
    overall: int = 0
    plot: int = 0
    character: int = 0
    continuity: int = 0
    style: int = 0
    pacing: int = 0
    foreshadowing: int = 0
    ai_trace: int = 0
    issues: list[Issue] = Field(default_factory=list)

    # 元信息（§2.2）—— Python 端属性名 ``meta``；JSON 序列化为 ``"_meta"``。
    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")

    # 回显
    report_id: str = ""
    chapter_id: str = ""
    chapter_number: int = 0
    commit_id: Optional[str] = None
    run_id: Optional[str] = None


__all__ = ["Issue", "QualityContext", "QualityReport"]
