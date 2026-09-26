"""交付判定（delivery verdict）——纯推导层：证据 → 闭合判定 + 机器可读理由。

「done 到底是什么意思」在本仓此前**没有任何单一出口**：一章能到 ``COMMITTED``
（提交门的硬停只有一份很窄的结构性白名单），可交付性散落在 quality_reports /
review run 的字数带 / signing_check / 章状态四处，没有一处回答「这一章能不能交」。
本模块就是那个出口，且是**推导**出来的，不允许手工设置。

判定档位（闭合四档）：

- ``deliverable``：全部证据齐备、无降级项——可以交。
- ``needs_work``：有已知待办（confirm 档未接受 / 字数出带 / 签约硬规则 fail /
  尚未提交 / 某项证据未评估）——**不是**「不能交」，是「现在交不出去」。
- ``not_deliverable``：硬停（block 档 issue，或字数偏离超过带边缘到目标的距离）。
- ``not_a_candidate``：还不是交付对象（``PLANNED``——没正文）。**刻意与
  ``not_deliverable`` 分开**：后者说「交了会出事」，前者说「还没有东西可交」，
  混为一谈会让 27 章未写的项目看起来像 27 章写坏了。

理由形状（机器可读，前端 / 报告按 ``rule_id`` 分支）：``rule_id``（规则或证据来源
标识）、``source``（证据出处）、``severity``（该证据本身的严重度）、``status``
（后果档）、``message``（中文人话）、``evidence``（引文摘录或数字）。

降级规则（哪些理由真的把判定往下拉）：

=============== ================== ==================================================
status          后果              依据
=============== ================== ==================================================
``block``       硬停               ``quality.issues.issue_gate`` == block（白名单）
``length_beyond_band_edge`` 硬停   与 chapter_review ``basic_checks`` 的 error 级同式
``confirm``     待办               confirm 档命中且报告内无接受留痕
``length_out_of_band`` 待办        出带（±15% 带边缘）
``signing_fail`` 待办              番茄硬规则 fail 档（开篇冲突 / 主角出场 / 金手指）
``not_committed`` 待办             章状态未到 COMMITTED / RELEASED
``not_evaluated`` 待办             证据源不可得（无报告 / 无草稿 / 无正文无法量字数）
``warn``        仅展示             severity=warning 且 gate=auto（可静默通过）
``accepted``    仅展示             confirm 档已有显式接受留痕
``out_of_scope`` 仅展示            该证据源按设计不适用于本章（见 signing_check 段）
``not_a_candidate`` 仅展示         章节未写正文（判定已是 not_a_candidate）
``project_*``   不参与章级阶梯     项目级汇总陈述（只在 ``project_reasons`` 里出现）
=============== ================== ==================================================

**为什么不按 severity 降级**：``gate`` 与 ``severity`` 正交是仓内既有裁决
（``quality/issues.py`` 的 ``Gate`` 注释：severity 答「多严重」，gate 答「谁能说
通过」）。``gate=auto`` 的 warning 是「允许静默通过」的可视化噪声——若把它们一律
算作待办，本项目 111 份历史报告里几乎每章都有 warning，``deliverable`` 会变成
死档，判定随之失去区分度。反过来，confirm 档**正是**为了「不许静默通过」而设，
所以未获显式接受的 confirm 必须降级。

**证据缺失 ≠ 通过**（本模块存在的理由之一）：任何一项证据不可得，都必须落一条
``not_evaluated`` 理由并降级到 ``needs_work``；绝不允许「因为量不出来所以当作合格」。

本模块零 IO（证据由 :mod:`packages.core.delivery.evidence` 取），因此每条分支都能
在单测里直接构造输入验证；``gate`` 分档一律走 :func:`quality.issues.issue_gate`
（唯一权威），不读落库的 ``gate`` 字段值做判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Iterable, Literal

from packages.core.quality.issues import issue_gate

__all__ = [
    "Verdict",
    "ReasonStatus",
    "ReasonSource",
    "Reason",
    "VerdictResult",
    "QualityEvidence",
    "LengthEvidence",
    "SigningEvidence",
    "ChapterEvidence",
    "ProjectEvidence",
    "project_verdict_of",
    "derive_chapter_verdict",
    "classify_length_tier",
    "issue_gate_rows",
    "WRITTEN_STATUSES",
    "DELIVERED_STATUSES",
    "DELIVERABLE",
    "NEEDS_WORK",
    "NOT_DELIVERABLE",
    "NOT_A_CANDIDATE",
    "RULE_CHAPTER_NOT_WRITTEN",
    "RULE_NOT_COMMITTED",
    "RULE_QUALITY_REPORT_MISSING",
    "RULE_QUALITY_REPORT_STALE",
    "RULE_LENGTH_UNMEASURED",
    "RULE_LENGTH_OUT_OF_BAND",
    "RULE_LENGTH_BEYOND_BAND_EDGE",
    "RULE_SIGNING_OUT_OF_SCOPE",
    "RULE_SIGNING_UNMEASURED",
    "RULE_PROJECT_EMPTY",
    "RULE_PROJECT_INCOMPLETE",
    "RULE_PROJECT_NOT_DELIVERABLE",
    "RULE_PROJECT_NEEDS_WORK",
]

# --------------------------------------------------------------------------- 档位

Verdict = Literal["deliverable", "needs_work", "not_deliverable", "not_a_candidate"]
"""交付判定四档（闭合集合；由 :func:`derive_chapter_verdict` 推导，不接受手工设置）。"""

DELIVERABLE: Final[Verdict] = "deliverable"
NEEDS_WORK: Final[Verdict] = "needs_work"
NOT_DELIVERABLE: Final[Verdict] = "not_deliverable"
NOT_A_CANDIDATE: Final[Verdict] = "not_a_candidate"

ReasonStatus = Literal[
    "block",
    "confirm",
    "accepted",
    "warn",
    "length_out_of_band",
    "length_beyond_band_edge",
    "signing_fail",
    "not_committed",
    "not_evaluated",
    "out_of_scope",
    "not_a_candidate",
    "project_not_deliverable",
    "project_needs_work",
]
"""理由的后果档（见模块 docstring 的降级规则表）。

``project_*`` 两档只出现在 ``DeliveryVerdictResponse.project_reasons``（项目级汇总的
陈述），**不参与**章级阶梯——章级降级只看 :data:`_HARD_STOP_STATUSES` /
:data:`_NEEDS_WORK_STATUSES`。
"""

ReasonSource = Literal["quality_issue", "length", "signing_check", "chapter_status", "evidence"]
"""理由的证据出处。"""

# 章状态口径：与 packages/core/signing_check/service.py::_INCLUDED_STATUSES 同源
# （「有正文的章节」= 非 PLANNED），另加「已过提交门」一档（COMMITTED / RELEASED）。
WRITTEN_STATUSES: Final[frozenset[str]] = frozenset(
    {"DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"}
)
DELIVERED_STATUSES: Final[frozenset[str]] = frozenset({"COMMITTED", "RELEASED"})

# 降级集合：命中任一即不能是 deliverable。
_HARD_STOP_STATUSES: Final[frozenset[str]] = frozenset({"block", "length_beyond_band_edge"})
_NEEDS_WORK_STATUSES: Final[frozenset[str]] = frozenset(
    {"confirm", "length_out_of_band", "signing_fail", "not_committed", "not_evaluated"}
)
_DEGRADING_STATUSES: Final[frozenset[str]] = _HARD_STOP_STATUSES | _NEEDS_WORK_STATUSES

# --------------------------------------------------------------------------- 规则 id

#: 章未写正文（status=PLANNED）——不是交付对象。
RULE_CHAPTER_NOT_WRITTEN: Final[str] = "DELIVERY_CHAPTER_NOT_WRITTEN"
#: 章有正文但未过提交门（DRAFTED / REVIEWED）。
RULE_NOT_COMMITTED: Final[str] = "DELIVERY_NOT_COMMITTED"
#: 该章没有任何 quality_report（质量口径未评）。
RULE_QUALITY_REPORT_MISSING: Final[str] = "DELIVERY_QUALITY_REPORT_MISSING"
#: 质量报告评的草稿版本 ≠ 当前最新版本（报告描述的不是将被交付的那份正文）。
RULE_QUALITY_REPORT_STALE: Final[str] = "DELIVERY_QUALITY_REPORT_STALE"
#: 字数无法测量（无草稿 / 目标字数无法解析）。
RULE_LENGTH_UNMEASURED: Final[str] = "DELIVERY_LENGTH_UNMEASURED"
#: 字数出带（未超带边缘距离）。
RULE_LENGTH_OUT_OF_BAND: Final[str] = "DELIVERY_LENGTH_OUT_OF_BAND"
#: 字数偏离超过「带边缘到目标的距离」（与 chapter_review error 级同式）。
RULE_LENGTH_BEYOND_BAND_EDGE: Final[str] = "DELIVERY_LENGTH_BEYOND_BAND_EDGE"
#: 签约体检按设计不适用于本章（章号在黄金三章之外）。
RULE_SIGNING_OUT_OF_SCOPE: Final[str] = "DELIVERY_SIGNING_CHECK_OUT_OF_SCOPE"
#: 签约体检在范围内但没有本章的检查项（体检本身不可得）。
RULE_SIGNING_UNMEASURED: Final[str] = "DELIVERY_SIGNING_CHECK_UNMEASURED"
#: 项目级：没有任何章节。
RULE_PROJECT_EMPTY: Final[str] = "DELIVERY_PROJECT_EMPTY"
#: 项目级：仍有章未写（书没写完）。
RULE_PROJECT_INCOMPLETE: Final[str] = "DELIVERY_PROJECT_INCOMPLETE"
#: 项目级：有 hard stop 章。
RULE_PROJECT_NOT_DELIVERABLE: Final[str] = "DELIVERY_PROJECT_NOT_DELIVERABLE"
#: 项目级：有待办章。
RULE_PROJECT_NEEDS_WORK: Final[str] = "DELIVERY_PROJECT_NEEDS_WORK"


# --------------------------------------------------------------------------- 理由


@dataclass(frozen=True)
class Reason:
    """一条判定的理由（机器可读 + 可展示）。"""

    rule_id: str
    source: ReasonSource
    severity: str
    status: ReasonStatus
    message: str
    evidence: str | None = None

    @property
    def degrading(self) -> bool:
        """该理由是否把判定往下拉（``warn`` / ``accepted`` / ``out_of_scope`` 不拉）。"""
        return self.status in _DEGRADING_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source": self.source,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "evidence": self.evidence,
        }


# --------------------------------------------------------------------------- 证据


@dataclass(frozen=True)
class QualityEvidence:
    """该章最新一份 quality_report 的证据面。"""

    report_id: str
    overall: int
    evaluated_at: str | None
    draft_version: int | None
    gate_summary: dict[str, Any] | None
    accepted_override: dict[str, Any] | None
    issues: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class LengthEvidence:
    """该章字数对目标带的测量结果（口径来自 ``quality.wordcount``）。"""

    visible_chars: int
    target_word_count: int
    band_low: int
    band_high: int
    deviation_pct: float
    status: str  # in_band / under / over
    tier: str  # in_band / warn / error


@dataclass(frozen=True)
class SigningEvidence:
    """签约体检在本章的适用面。

    ``scope``：

    - ``in_scope``：章号在黄金三章内，``items`` 是本章的检查项；
    - ``out_of_scope``：章号在黄金三章外（按设计不适用，不是「量不出来」）；
    - ``unavailable``：体检本身跑不起来（整段不可得）——本章按未评估处理。
    """

    scope: str
    items: tuple[dict[str, Any], ...] = ()
    note: str = ""


@dataclass(frozen=True)
class ChapterEvidence:
    """一章的全部可得证据（判定输入）。"""

    chapter_id: str
    number: int
    title: str
    status: str
    draft_version: int | None = None
    quality: QualityEvidence | None = None
    length: LengthEvidence | None = None
    signing: SigningEvidence = SigningEvidence(scope="unavailable")


@dataclass(frozen=True)
class ProjectEvidence:
    """一个项目的全部可得证据。"""

    project_id: str
    project_name: str
    chapters: tuple[ChapterEvidence, ...] = ()
    #: 证据源可达性（诚实面）：``[{key, reachable, detail}]``。
    sources: tuple[dict[str, Any], ...] = ()
    #: 签约体检的适用面说明（中文，随 payload 一起给作者看）。
    signing_scope_note: str = ""


# --------------------------------------------------------------------------- 判定


@dataclass(frozen=True)
class VerdictResult:
    """一章的判定结果。"""

    verdict: Verdict
    reasons: tuple[Reason, ...] = ()

    @property
    def degrading_reasons(self) -> tuple[Reason, ...]:
        return tuple(r for r in self.reasons if r.degrading)

    @property
    def blocking_reason(self) -> Reason | None:
        """首要降级理由：硬停优先，其次待办（同类保持证据顺序）。"""
        for bucket in (_HARD_STOP_STATUSES, _NEEDS_WORK_STATUSES):
            for r in self.reasons:
                if r.status in bucket:
                    return r
        return None


def classify_length_tier(
    classified: dict[str, Any],
    target_word_count: int,
) -> str:
    """把 ``classify_prose_length`` 的结果折成三档：``in_band`` / ``warn`` / ``error``。

    与 ``packages/workflows/chapter_review/pipeline.py::basic_checks`` 的两级口径同式
    （V3.9 批次 5.2）：出带即 ``warn``；带外偏离**超过该侧带边缘到目标的距离**才
    ``error``（默认带 ±15% ⇒ 阈值即 ±30%，与改造前的写死阈值逐值一致）。

    ``target_word_count <= 0`` 时无法算距离 → 退回 ``warn``（不猜硬停）。
    """
    status = str(classified.get("status") or "")
    if status == "in_band":
        return "in_band"
    visible = int(classified.get("visible_chars") or 0)
    band_low = int(classified.get("band_low") or 0)
    band_high = int(classified.get("band_high") or 0)
    if target_word_count <= 0:
        return "warn"
    edge_deviation = max(0, band_low - visible, visible - band_high)
    edge_distance = (
        target_word_count - band_low if visible < band_low else band_high - target_word_count
    )
    return "error" if edge_deviation > edge_distance else "warn"


@dataclass(frozen=True)
class _GateView:
    """把落库的 issue dict 适配给 :func:`issue_gate`（该函数按属性访问，dict 不适用）。

    只暴露 ``issue_gate`` 真正读的三个字段；``gate`` 一律透传落库值，
    让「单条显式上修为 confirm」这条既有语义继续生效。
    """

    severity: str | None = None
    rule_id: str | None = None
    gate: str | None = None


def _issue_evidence(issue: dict[str, Any]) -> str:
    """issue 的证据摘录：``message`` + （有则）``evidence_refs`` 原文片段。"""
    message = str(issue.get("message") or "")
    refs = [str(r) for r in (issue.get("evidence_refs") or []) if r]
    if refs:
        return f"{message}｜证据：{'、'.join(refs)}"
    return message


def issue_gate_rows(issues: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """把落库 issue 列表折成 ``[{rule_id, severity, gate, message}]``（gate 走权威函数）。

    落库报告可能早于 ``gate`` 字段（本项目 111 份历史报告全部如此）——此时按
    :func:`issue_gate` 的规则表重新推出分档，因此旧报告也能被正确分档。
    """
    rows: list[dict[str, str]] = []
    for issue in issues or ():
        severity = str(issue.get("severity") or "info")
        rule_id = str(issue.get("rule_id") or "")
        gate = issue_gate(
            _GateView(severity=severity, rule_id=rule_id, gate=issue.get("gate"))
        )
        rows.append(
            {
                "rule_id": rule_id,
                "severity": severity,
                "gate": gate,
                "message": str(issue.get("message") or ""),
            }
        )
    return rows


def _issue_reasons(quality: QualityEvidence) -> list[Reason]:
    """quality issue → 理由（分档走 :func:`issue_gate` 权威）。

    ``gate=auto`` 且 severity=info 的条目不是理由（例如 Q6 未配置参照书的提示），
    它们仍完整出现在 payload 的 ``issues`` 全量列表里；``gate=auto`` 的
    error / warning 落 ``warn``（展示但不降级）。
    """
    accepted_rule_ids = {
        str(r) for r in ((quality.accepted_override or {}).get("rule_ids") or [])
    }
    accepted_at = str((quality.accepted_override or {}).get("accepted_at") or "")
    accepted_reason = str((quality.accepted_override or {}).get("reason") or "")

    reasons: list[Reason] = []
    for issue in quality.issues:
        severity = str(issue.get("severity") or "info")
        rule_id = str(issue.get("rule_id") or "")
        gate = str(issue_gate(_GateView(severity=severity, rule_id=rule_id, gate=issue.get("gate"))))
        message = str(issue.get("message") or "")
        if gate == "block":
            status: ReasonStatus = "block"
            message = f"{message}（硬停：{rule_id} 在阻断白名单内，不可覆盖）"
        elif gate == "confirm":
            if rule_id in accepted_rule_ids:
                status = "accepted"
                tail = f"{accepted_at}｜{accepted_reason}" if accepted_reason else accepted_at
                message = f"{message}（confirm 档已有显式接受留痕：{tail}）"
            else:
                status = "confirm"
                message = f"{message}（confirm 档：须显式接受才放行，本章报告内无接受留痕）"
        elif severity in ("error", "warning"):
            status = "warn"
        else:
            continue
        reasons.append(
            Reason(
                rule_id=rule_id,
                source="quality_issue",
                severity=severity,
                status=status,
                message=message,
                evidence=_issue_evidence(issue),
            )
        )
    return reasons


def _length_reason(length: LengthEvidence) -> Reason | None:
    """字数证据 → 理由（带内无理由；出带 / 超带边缘各一条）。"""
    if length.tier == "in_band":
        return None
    deviation = f"{length.deviation_pct:+.1f}%"
    if length.tier == "error":
        edge_distance = (
            length.target_word_count - length.band_low
            if length.visible_chars < length.band_low
            else length.band_high - length.target_word_count
        )
        return Reason(
            rule_id=RULE_LENGTH_BEYOND_BAND_EDGE,
            source="length",
            severity="error",
            status="length_beyond_band_edge",
            message=(
                f"字数 {length.visible_chars} 偏离目标带 {length.band_low}~{length.band_high}"
                f"（目标 {length.target_word_count}，偏离 {deviation}）"
                f"已超过带边缘到目标的 {edge_distance} 字——硬口径不达标"
            ),
            evidence=(
                f"visible={length.visible_chars} target={length.target_word_count} "
                f"band={length.band_low}~{length.band_high} deviation={deviation}"
            ),
        )
    return Reason(
        rule_id=RULE_LENGTH_OUT_OF_BAND,
        source="length",
        severity="warning",
        status="length_out_of_band",
        message=(
            f"字数 {length.visible_chars} 不在目标带 {length.band_low}~{length.band_high}"
            f"（目标 {length.target_word_count}，偏离 {deviation}）"
        ),
        evidence=(
            f"visible={length.visible_chars} target={length.target_word_count} "
            f"band={length.band_low}~{length.band_high} deviation={deviation}"
        ),
    )


def _signing_reasons(signing: SigningEvidence) -> list[Reason]:
    """签约体检 → 理由（fail 档降级；warn 档仅展示；info 档不进理由）。"""
    if signing.scope == "out_of_scope":
        return [
            Reason(
                rule_id=RULE_SIGNING_OUT_OF_SCOPE,
                source="signing_check",
                severity="info",
                status="out_of_scope",
                message=(
                    "签约体检是黄金三章 / 签约窗口工具，不适用于本章（章号在 1~3 之外）；"
                    "本章的质量门槛由质量报告与字数带承担"
                ),
                evidence=signing.note or None,
            )
        ]
    if signing.scope == "unavailable" or not signing.items:
        return [
            Reason(
                rule_id=RULE_SIGNING_UNMEASURED,
                source="signing_check",
                severity="info",
                status="not_evaluated",
                message="签约体检未能给出本章检查项（体检不可得）——按未评估处理，不计为通过",
                evidence=signing.note or None,
            )
        ]
    reasons: list[Reason] = []
    for item in signing.items:
        level = str(item.get("level") or "")
        key = str(item.get("key") or "")
        detail = str(item.get("detail") or "")
        advice = str(item.get("advice") or "")
        if level == "fail":
            reasons.append(
                Reason(
                    rule_id=key,
                    source="signing_check",
                    severity="error",
                    status="signing_fail",
                    message=f"{detail}；建议：{advice}" if advice else detail,
                    evidence=detail,
                )
            )
        elif level == "warn":
            reasons.append(
                Reason(
                    rule_id=key,
                    source="signing_check",
                    severity="warning",
                    status="warn",
                    message=detail,
                    evidence=advice or None,
                )
            )
    return reasons


def derive_chapter_verdict(evidence: ChapterEvidence) -> VerdictResult:
    """由证据推导一章的交付判定（纯函数，无 IO）。

    判定顺序（先到先得）：

    1. ``status`` 不在 :data:`WRITTEN_STATUSES`（即 ``PLANNED``）⇒ ``not_a_candidate``，
       直接返回——没正文的章不进入后续证据评估（否则会给「29 章未写」刷一屏
       「未评估」）。
    2. 其余情形收集四组理由（质量报告 / 字数 / 签约体检 / 章状态），任一证据源不可得
       落 ``not_evaluated``。
    3. 汇总：有硬停 ⇒ ``not_deliverable``；有待办 ⇒ ``needs_work``；否则 ``deliverable``。
    """
    if evidence.status not in WRITTEN_STATUSES:
        return VerdictResult(
            verdict=NOT_A_CANDIDATE,
            reasons=(
                Reason(
                    rule_id=RULE_CHAPTER_NOT_WRITTEN,
                    source="chapter_status",
                    severity="info",
                    status="not_a_candidate",
                    message=(
                        f"章节 status={evidence.status} 且尚无正文，还不是交付对象"
                        "（与「写了但不能交」是两回事）"
                    ),
                    evidence=f"status={evidence.status}",
                ),
            ),
        )

    reasons: list[Reason] = []

    # ---- 证据源 1：质量报告 ----
    if evidence.quality is None:
        reasons.append(
            Reason(
                rule_id=RULE_QUALITY_REPORT_MISSING,
                source="evidence",
                severity="info",
                status="not_evaluated",
                message="本章没有任何质量报告，质量口径未评估——按未评估处理，不计为通过",
                evidence=None,
            )
        )
    else:
        reasons.extend(_issue_reasons(evidence.quality))
        report_version = evidence.quality.draft_version
        if (
            report_version is not None
            and evidence.draft_version is not None
            and int(report_version) != int(evidence.draft_version)
        ):
            reasons.append(
                Reason(
                    rule_id=RULE_QUALITY_REPORT_STALE,
                    source="evidence",
                    severity="info",
                    status="not_evaluated",
                    message=(
                        f"质量报告评的是草稿 v{report_version}，当前最新正文是 "
                        f"v{evidence.draft_version}——报告描述的不是将被交付的这份稿"
                    ),
                    evidence=f"report draft_version={report_version} latest={evidence.draft_version}",
                )
            )

    # ---- 证据源 2：字数带 ----
    if evidence.length is None:
        reasons.append(
            Reason(
                rule_id=RULE_LENGTH_UNMEASURED,
                source="evidence",
                severity="info",
                status="not_evaluated",
                message="正文或目标字数无法测量，字数带未评估——按未评估处理，不计为通过",
                evidence=None,
            )
        )
    else:
        length_reason = _length_reason(evidence.length)
        if length_reason is not None:
            reasons.append(length_reason)

    # ---- 证据源 3：签约体检（仅黄金三章在范围内，见 SigningEvidence）----
    reasons.extend(_signing_reasons(evidence.signing))

    # ---- 证据源 4：章状态 ----
    if evidence.status not in DELIVERED_STATUSES:
        reasons.append(
            Reason(
                rule_id=RULE_NOT_COMMITTED,
                source="chapter_status",
                severity="info",
                status="not_committed",
                message=(
                    f"章节 status={evidence.status}，尚未过提交门（COMMITTED / RELEASED）"
                    "——提交门未过的正文不算可交付"
                ),
                evidence=f"status={evidence.status}",
            )
        )

    if any(r.status in _HARD_STOP_STATUSES for r in reasons):
        verdict: Verdict = NOT_DELIVERABLE
    elif any(r.status in _NEEDS_WORK_STATUSES for r in reasons):
        verdict = NEEDS_WORK
    else:
        verdict = DELIVERABLE
    return VerdictResult(verdict=verdict, reasons=tuple(reasons))


def project_verdict_of(verdicts: Iterable[str]) -> Verdict:
    """项目级判定 = 各章判定的最坏值（纯函数）。

    - 无章节 ⇒ ``not_a_candidate``；
    - 全部章都是 ``not_a_candidate``（一个字没写）⇒ ``not_a_candidate``；
    - 有 ``not_deliverable`` ⇒ ``not_deliverable``；
    - 有 ``needs_work`` **或** 有未写的章 ⇒ ``needs_work``（书没写完就不能算可交付）；
    - 其余（至少一章且全部 ``deliverable``）⇒ ``deliverable``。
    """
    values = [str(v) for v in verdicts]
    if not values:
        return NOT_A_CANDIDATE
    if all(v == NOT_A_CANDIDATE for v in values):
        return NOT_A_CANDIDATE
    if NOT_DELIVERABLE in values:
        return NOT_DELIVERABLE
    if NEEDS_WORK in values or NOT_A_CANDIDATE in values:
        return NEEDS_WORK
    return DELIVERABLE
