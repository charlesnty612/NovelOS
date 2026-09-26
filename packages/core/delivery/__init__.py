"""交付判定（delivery）——「这一章 / 这本书能不能交」的单一出口。

背景（2026-09-18 实证）：一章能到 ``COMMITTED``，而「可交付」此前没有任何单一出处——
质量分、字数带、签约体检、章状态各在一处，没人汇总，于是「9 段逐字重复的章以
overall 90 提交」「低于字数带的章带着一条 warning 就发布」这类情况全程无告警。

对外接口（三层）：

- **纯推导层**（:mod:`packages.core.delivery.verdict`，零 IO）：
  :func:`derive_chapter_verdict` 由证据推导闭合判定，
  :func:`project_verdict_of` 汇总项目档；
- **证据层**（:mod:`packages.core.delivery.evidence`，只读 DB）：
  :func:`collect_project_evidence` 取质量报告 / 字数带 / 签约体检 / 章状态，
  读不到的源显式标 ``reachable=False``（绝不当通过）；
- **服务层**（:mod:`packages.core.delivery.service`）：:func:`build_delivery_report`
  产出 JSON-ready 报告，:func:`build_chapter_verdict` 产出单章判定行。

HTTP 面：``GET /api/projects/{project_id}/delivery-verdict``（见
:mod:`packages.core.api.routers.delivery`）。契约细节见本包 ``README.md``。
"""

from __future__ import annotations

from .evidence import (
    SIGNING_SCOPE_MAX_CHAPTER,
    SIGNING_SCOPE_NOTE,
    collect_project_evidence,
    signing_items_by_chapter,
)
from .models import (
    DeliveryChapter,
    DeliveryEvidenceSource,
    DeliveryReason,
    DeliveryRollUp,
    DeliveryVerdictResponse,
)
from .service import build_chapter_verdict, build_delivery_report
from .verdict import (
    DELIVERABLE,
    DELIVERED_STATUSES,
    NEEDS_WORK,
    NOT_A_CANDIDATE,
    NOT_DELIVERABLE,
    WRITTEN_STATUSES,
    ChapterEvidence,
    LengthEvidence,
    ProjectEvidence,
    QualityEvidence,
    Reason,
    SigningEvidence,
    Verdict,
    VerdictResult,
    classify_length_tier,
    derive_chapter_verdict,
    issue_gate_rows,
    project_verdict_of,
)

__all__ = [
    # 纯推导层
    "Verdict",
    "DELIVERABLE",
    "NEEDS_WORK",
    "NOT_DELIVERABLE",
    "NOT_A_CANDIDATE",
    "WRITTEN_STATUSES",
    "DELIVERED_STATUSES",
    "Reason",
    "VerdictResult",
    "ChapterEvidence",
    "QualityEvidence",
    "LengthEvidence",
    "SigningEvidence",
    "ProjectEvidence",
    "derive_chapter_verdict",
    "project_verdict_of",
    "classify_length_tier",
    "issue_gate_rows",
    # 证据层
    "collect_project_evidence",
    "signing_items_by_chapter",
    "SIGNING_SCOPE_MAX_CHAPTER",
    "SIGNING_SCOPE_NOTE",
    # 服务层
    "build_delivery_report",
    "build_chapter_verdict",
    # 响应模型
    "DeliveryVerdictResponse",
    "DeliveryChapter",
    "DeliveryRollUp",
    "DeliveryReason",
    "DeliveryEvidenceSource",
]
