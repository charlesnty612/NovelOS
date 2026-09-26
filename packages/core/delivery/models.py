"""交付判定的响应模型（pydantic v2）。

形状对齐 :mod:`packages.domain.chapter.models` / :mod:`packages.core.quality.models`
（同为「面向调用方的强类型序列化层」），供 FastAPI ``response_model`` 生成 OpenAPI
schema 与前端类型。

字段口径：

- ``verdict`` / ``reason.status`` / ``reason.source`` 用 :mod:`packages.core.delivery.verdict`
  的 ``Literal`` 别名——闭合集合进 schema，前端可穷举；写错值会在响应校验阶段炸，
  不会静默漏出去。
- ``chapters[].status`` 是**章节状态**（PLANNED / DRAFTED / ...），与 ``verdict``
  是两回事：前者是流程位置，后者是本模块推导出的可交付性。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .verdict import ReasonSource, ReasonStatus, Verdict

__all__ = [
    "DeliveryReason",
    "DeliveryIssue",
    "DeliveryQuality",
    "DeliveryLength",
    "DeliverySigningItem",
    "DeliverySigning",
    "DeliveryChapter",
    "DeliveryRollUp",
    "DeliveryEvidenceSource",
    "DeliveryVerdictResponse",
]


class DeliveryReason(BaseModel):
    """一条判定理由。"""

    rule_id: str = Field(..., description="规则 / 证据来源标识（前端按此分支）")
    source: ReasonSource = Field(..., description="证据出处")
    severity: str = Field(..., description="该证据自身的严重度（error / warning / info）")
    status: ReasonStatus = Field(..., description="后果档（决定判定是否降级）")
    message: str = Field(..., description="面向作者的中文说明")
    evidence: str | None = Field(None, description="证据摘录（引文片段或数字）")


class DeliveryIssue(BaseModel):
    """质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。"""

    rule_id: str
    severity: str
    gate: str = Field(..., description="后果档 auto / confirm / block")
    message: str


class DeliveryQuality(BaseModel):
    """该章最新质量报告的证据面。"""

    report_id: str
    overall: int
    evaluated_at: str | None = None
    draft_version: int | None = Field(None, description="本报告评估的草稿版本")
    gate_summary: dict[str, Any] | None = Field(
        None, description="报告 _meta.gate_summary（block / confirm 聚合摘要）"
    )
    accepted_override: dict[str, Any] | None = Field(
        None, description="报告 _meta.gate_accepted_override（confirm 档接受留痕）"
    )
    issues: list[DeliveryIssue] = Field(default_factory=list)


class DeliveryLength(BaseModel):
    """字数测量（``quality.wordcount`` 口径）。"""

    visible_chars: int
    target_word_count: int
    band_low: int
    band_high: int
    deviation_pct: float
    status: str = Field(..., description="in_band / under / over")
    tier: str = Field(..., description="in_band / warn / error（两级阈值同 review 侧）")


class DeliverySigningItem(BaseModel):
    """签约体检的单项（仅第 1~3 章）。"""

    key: str
    level: str = Field(..., description="pass / warn / fail / info")
    detail: str
    advice: str = ""


class DeliverySigning(BaseModel):
    """签约体检在本章的适用面。"""

    scope: str = Field(..., description="in_scope / out_of_scope / unavailable")
    note: str = ""
    items: list[DeliverySigningItem] = Field(default_factory=list)


class DeliveryChapter(BaseModel):
    """一章的交付判定行。"""

    chapter_id: str
    number: int
    title: str
    status: str = Field(..., description="章节状态（流程位置，非交付判定）")
    verdict: Verdict
    blocking_reason: str | None = Field(
        None, description="首个降级理由的中文说明；可交付时为 null"
    )
    draft_version: int | None = None
    length: DeliveryLength | None = None
    quality: DeliveryQuality | None = None
    signing: DeliverySigning
    reasons: list[DeliveryReason] = Field(default_factory=list)


class DeliveryRollUp(BaseModel):
    """项目级汇总（作者的问题是「这本书能不能交」）。"""

    chapter_count: int
    written_chapter_count: int = Field(..., description="非 PLANNED 的章节数")
    deliverable_count: int
    needs_work_count: int
    not_deliverable_count: int
    not_a_candidate_count: int
    deliverable_chapter_ids: list[str]
    needs_work_chapter_ids: list[str]
    not_deliverable_chapter_ids: list[str]
    not_a_candidate_chapter_ids: list[str]
    blocking_reason_by_chapter: dict[str, str] = Field(
        default_factory=dict, description="每章 id → 首个降级理由（可交付章不出现）"
    )


class DeliveryEvidenceSource(BaseModel):
    """证据源可达性（诚实面：看不到的要说出来）。"""

    key: str
    reachable: bool
    detail: str


class DeliveryVerdictResponse(BaseModel):
    """``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。"""

    project_id: str
    project_name: str
    generated_at: str
    project_verdict: Verdict
    project_reasons: list[DeliveryReason] = Field(default_factory=list)
    roll_up: DeliveryRollUp
    evidence_sources: list[DeliveryEvidenceSource] = Field(default_factory=list)
    signing_check_scope: str = ""
    chapters: list[DeliveryChapter] = Field(default_factory=list)
