"""交付判定服务层：证据 → 逐章判定 → 项目汇总（JSON-ready payload）。

- :func:`build_delivery_report` —— 项目级入口（router 唯一调用方）；
- :func:`build_chapter_verdict` —— 单章入口（不需要项目汇总的调用方 / 单测用）。

设计要点：

- 判定一律来自纯函数 :func:`packages.core.delivery.verdict.derive_chapter_verdict`，
  本层只做「证据 → 响应模型」的搬运，不含任何判定分支；
- 返回 :meth:`DeliveryVerdictResponse.model_dump` 的 dict（不是模型实例）——router
  用同一模型声明 ``response_model``，因此字段漂移在写代码时就暴露，不会到运行时才发现；
- 项目级理由（``project_reasons``）是**汇总陈述**，不参与章级阶梯（见 verdict 模块
  docstring 的 ``project_*`` 说明）。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Sequence

from packages.core.ids import now_iso
from packages.core.logging_config import get_logger

from .evidence import collect_project_evidence
from .models import (
    DeliveryChapter,
    DeliveryEvidenceSource,
    DeliveryIssue,
    DeliveryLength,
    DeliveryQuality,
    DeliveryReason,
    DeliveryRollUp,
    DeliverySigning,
    DeliverySigningItem,
    DeliveryVerdictResponse,
)
from .verdict import (
    DELIVERABLE,
    NEEDS_WORK,
    NOT_A_CANDIDATE,
    NOT_DELIVERABLE,
    RULE_PROJECT_EMPTY,
    RULE_PROJECT_INCOMPLETE,
    RULE_PROJECT_NEEDS_WORK,
    RULE_PROJECT_NOT_DELIVERABLE,
    WRITTEN_STATUSES,
    ChapterEvidence,
    Reason,
    VerdictResult,
    derive_chapter_verdict,
    issue_gate_rows,
    project_verdict_of,
)

__all__ = ["build_delivery_report", "build_chapter_verdict"]

log = get_logger("novelos.delivery.service")

#: 项目级理由里列举章号的上限（超出的用「…」收尾，避免超长消息）。
_NUMBER_LIST_LIMIT = 12


def _numbers_text(numbers: Sequence[int]) -> str:
    """章号列表 → 「第 1、2、3 章」（超长截断）。"""
    head = numbers[:_NUMBER_LIST_LIMIT]
    text = "、".join(str(n) for n in head)
    if len(numbers) > _NUMBER_LIST_LIMIT:
        return f"第 {text}、… 共 {len(numbers)} 章"
    return f"第 {text} 章"


def _quality_model(evidence: ChapterEvidence) -> DeliveryQuality | None:
    quality = evidence.quality
    if quality is None:
        return None
    return DeliveryQuality(
        report_id=quality.report_id,
        overall=quality.overall,
        evaluated_at=quality.evaluated_at,
        draft_version=quality.draft_version,
        gate_summary=quality.gate_summary,
        accepted_override=quality.accepted_override,
        issues=[DeliveryIssue(**row) for row in issue_gate_rows(quality.issues)],
    )


def _reasons_model(result: VerdictResult) -> list[DeliveryReason]:
    return [DeliveryReason(**reason.as_dict()) for reason in result.reasons]


def build_chapter_verdict(evidence: ChapterEvidence) -> DeliveryChapter:
    """单章证据 → 判定行（不含项目汇总）。"""
    result = derive_chapter_verdict(evidence)
    blocking = result.blocking_reason
    return DeliveryChapter(
        chapter_id=evidence.chapter_id,
        number=evidence.number,
        title=evidence.title,
        status=evidence.status,
        verdict=result.verdict,
        blocking_reason=blocking.message if blocking is not None else None,
        draft_version=evidence.draft_version,
        length=DeliveryLength(**asdict(evidence.length)) if evidence.length else None,
        quality=_quality_model(evidence),
        signing=DeliverySigning(
            scope=evidence.signing.scope,
            note=evidence.signing.note,
            items=[DeliverySigningItem(**item) for item in evidence.signing.items],
        ),
        reasons=_reasons_model(result),
    )


def _project_reasons(
    verdict: str, chapters: Sequence[DeliveryChapter]
) -> list[DeliveryReason]:
    """项目级理由（汇总陈述；不参与章级阶梯）。"""
    if verdict == NOT_A_CANDIDATE:
        return [
            DeliveryReason(
                **Reason(
                    rule_id=RULE_PROJECT_EMPTY,
                    source="evidence",
                    severity="info",
                    status="not_a_candidate",
                    message="项目还没有任何有正文的章节，尚不是可交付对象",
                    evidence=f"chapter_count={len(chapters)}",
                ).as_dict()
            )
        ]
    if verdict == NOT_DELIVERABLE:
        hit = [c for c in chapters if c.verdict == NOT_DELIVERABLE]
        return [
            DeliveryReason(
                **Reason(
                    rule_id=RULE_PROJECT_NOT_DELIVERABLE,
                    source="evidence",
                    severity="error",
                    status="project_not_deliverable",
                    message=(
                        f"{_numbers_text([c.number for c in hit])} 硬停，不可交付"
                        "（逐章原因见 chapters[].blocking_reason）"
                    ),
                    evidence=f"not_deliverable_count={len(hit)}",
                ).as_dict()
            )
        ]
    if verdict == NEEDS_WORK:
        reasons: list[DeliveryReason] = []
        pending = [c for c in chapters if c.verdict == NEEDS_WORK]
        if pending:
            reasons.append(
                DeliveryReason(
                    **Reason(
                        rule_id=RULE_PROJECT_NEEDS_WORK,
                        source="evidence",
                        severity="warning",
                        status="project_needs_work",
                        message=(
                            f"{_numbers_text([c.number for c in pending])} 有待办，尚未可交付"
                            "（逐章原因见 chapters[].blocking_reason）"
                        ),
                        evidence=f"needs_work_count={len(pending)}",
                    ).as_dict()
                )
            )
        unwritten = [c for c in chapters if c.verdict == NOT_A_CANDIDATE]
        if unwritten:
            reasons.append(
                DeliveryReason(
                    **Reason(
                        rule_id=RULE_PROJECT_INCOMPLETE,
                        source="evidence",
                        severity="warning",
                        status="project_needs_work",
                        message=(
                            f"还有 {len(unwritten)} 章没有正文（{_numbers_text([c.number for c in unwritten])}），"
                            "书还没写完"
                        ),
                        evidence=f"not_a_candidate_count={len(unwritten)}",
                    ).as_dict()
                )
            )
        return reasons
    return []


def _roll_up(chapters: Sequence[DeliveryChapter]) -> DeliveryRollUp:
    """项目汇总：分档计数 + 按档列章 id + 每章阻塞理由。"""
    def ids(verdict: str) -> list[str]:
        return [c.chapter_id for c in chapters if c.verdict == verdict]

    def note(chapter: DeliveryChapter) -> str | None:
        """非可交付章的理由文本：降级理由优先，``not_a_candidate`` 等无降级理由的档
        回落到首条理由（作者读 roll-up 时每一章都要有「为什么」）。"""
        if chapter.blocking_reason:
            return chapter.blocking_reason
        return chapter.reasons[0].message if chapter.reasons else None

    blocking: dict[str, str] = {}
    for chapter in chapters:
        if chapter.verdict == DELIVERABLE:
            continue
        text = note(chapter)
        if text:
            blocking[chapter.chapter_id] = text
    return DeliveryRollUp(
        chapter_count=len(chapters),
        written_chapter_count=sum(1 for c in chapters if c.status in WRITTEN_STATUSES),
        deliverable_count=len(ids(DELIVERABLE)),
        needs_work_count=len(ids(NEEDS_WORK)),
        not_deliverable_count=len(ids(NOT_DELIVERABLE)),
        not_a_candidate_count=len(ids(NOT_A_CANDIDATE)),
        deliverable_chapter_ids=ids(DELIVERABLE),
        needs_work_chapter_ids=ids(NEEDS_WORK),
        not_deliverable_chapter_ids=ids(NOT_DELIVERABLE),
        not_a_candidate_chapter_ids=ids(NOT_A_CANDIDATE),
        blocking_reason_by_chapter=blocking,
    )


def build_delivery_report(db_path: str, project_id: str) -> dict[str, Any]:
    """项目级交付判定报告（JSON-ready dict）。

    - 证据：:func:`packages.core.delivery.evidence.collect_project_evidence`
      （质量报告 / 字数带 / 签约体检 / 章状态）；
    - 判定：逐章走纯函数推导，项目档 = 各章的最坏值（见 ``project_verdict_of``）；
    - 异常：``ValueError`` —— project 不存在（router 转 404）。
    """
    evidence = collect_project_evidence(db_path, project_id)
    chapters = [build_chapter_verdict(ch) for ch in evidence.chapters]
    verdict = project_verdict_of([c.verdict for c in chapters])
    response = DeliveryVerdictResponse(
        project_id=evidence.project_id,
        project_name=evidence.project_name,
        generated_at=now_iso(),
        project_verdict=verdict,
        project_reasons=_project_reasons(verdict, chapters),
        roll_up=_roll_up(chapters),
        evidence_sources=[
            DeliveryEvidenceSource(**source) for source in evidence.sources
        ],
        signing_check_scope=evidence.signing_scope_note,
        chapters=chapters,
    )
    return response.model_dump()
