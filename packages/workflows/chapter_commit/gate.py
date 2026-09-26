"""质量门禁与高危审批节点
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。

V3.9 批次 4.1（失败闭环）：enforce 阻断时除抛错外，把改稿出路落到 chapter：
- ``plan_json.revision_note``：由 ``revision_guidance`` 编译成的人类可读改稿意见
  （与 chapter-review「驳回并改稿」同款落点，单一属主语义）；
- ``plan_json.gate_blocked``：阻断标记 + ``rule_ids`` 摘要，供前端区分
  「review 驳回」与「gate 阻断」，并驱动「按门禁建议改稿」入口。
- 门禁通过时清除上一次残留的 ``gate_blocked``（防御历史数据 / 上一次阻断已解除）。

2026-09-18 P0-1（severity ≠ 后果）：本节点在既有 blocking 之外，再执行 **confirm 档**——
``issues.issue_gate`` 判为 ``confirm`` 的 issue（``rule_id ∈ issues.CONFIRM_RULES``，
或产出侧按量级把该条上修的，如 ``scoring`` 的 trigram > 0.25 档）一律**不许静默通过**：

- ``ctx["gate_override"] = {"rule_ids": [...], "reason": "..."}`` 是唯一的放行方式；
  要求 ``rule_ids`` **覆盖本次全部 confirm rule id** 且 ``reason`` 非空。
- 缺失 / 部分覆盖 / reason 为空 ⇒ 复用同一条阻断落点（``plan_json.revision_note`` +
  ``gate_blocked``），并在 message 里同时给出 **rule_id 清单 + 证据摘录**，
  让调用方不可能在不知道自己在批准什么的情况下批准。
- 放行时把接受声明写进 ``quality_reports._meta.gate_accepted_override``（留痕）+ 节点输出，
  ``plan_json.gate_blocked`` 照常清除。

为什么需要这一档（实证）：2026-09-18 某章 9 段逐字重复（章内重复 20.3% / trigram 30.37%）
照常提交，review ``errors: []``、quality ``overall: 90``——severity 三档但后果只有「过」
一档，没有任何一处有权限说「不许静默通过」。重复类问题的正确出路不是调阈值，
而是要求「看见并显式接受」。
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.quality.aggregate import GateSummary, summarize_gates
from packages.core.quality.engine import QualityEngine
from packages.core.quality.issues import is_blocking_issue
from packages.core.quality.service import QualityService, build_quality_context, capture_reference_consumption
from packages.core.story_state.service import StoryStateService
from packages.core.workflow_runtime.engine import PauseRequested

from .pipeline_common import (
    _build_revision_guidance,
)

_log = logging.getLogger(__name__)

# plan_json 中的门禁标记键（唯一属主：本节点写/清，前端与 API 只读）。
_GATE_BLOCKED_KEY = "gate_blocked"

# 放行声明键（ctx / 节点输出；P0-1）。API 请求体同名字段见
# ``packages.core.api.routers.workflows.common.StartWorkflowRequest.gate_override``。
_GATE_OVERRIDE_KEY = "gate_override"

# 证据摘录每条的截断长度（避免把整章正文塞进 run.error / plan_json）。
_EVIDENCE_MAX_CHARS = 240


def _build_gate_revision_note(
    revision_guidance: list[dict[str, Any]],
    error_rule_ids: list[str],
) -> str:
    """把 ``revision_guidance`` 编译成写进 ``plan_json.revision_note`` 的改稿意见。

    纯文本、多行，供 chapter-write 的 revise 模式（``revision_note``）直接消费：
    首行给出阻断规则清单，其后每个 guidance 维度一行（含 rule_hint），
    再把该维度的 top_issues 逐条列出（最多 3 条，带 rule_id / message）。
    """
    lines: list[str] = [
        "【质量门禁阻断·改稿建议】quality_gate 检测到 error 级问题："
        + ("、".join(error_rule_ids) if error_rule_ids else "（未给出 rule_id）"),
    ]
    for entry in revision_guidance:
        if not isinstance(entry, dict):
            continue
        dim = entry.get("dimension") or "unknown"
        score = entry.get("score")
        threshold = entry.get("threshold")
        hint = str(entry.get("rule_hint") or "").strip()
        prefix = f"- {dim}"
        if isinstance(score, int) and isinstance(threshold, int):
            prefix += f"（分 {score}/阈值 {threshold}）"
        lines.append(f"{prefix}：{hint}" if hint else prefix)
        for issue in (entry.get("top_issues") or [])[:3]:
            if not isinstance(issue, dict):
                continue
            rule_id = issue.get("rule_id") or issue.get("category") or ""
            message = str(issue.get("message") or issue.get("suggestion") or "").strip()
            if not message:
                continue
            lines.append(f"  · {rule_id}: {message}")
    lines.append("（改稿后重新审校并提交，门禁通过后本提示自动解除。）")
    return "\n".join(lines)


def _issue_evidence_excerpt(issue: Any) -> str:
    """抽取一条 issue 的证据摘录（evidence_refs 优先，退化到 message）。

    P0-1 要求：confirm 档的阻断消息必须让调用方**看见自己在批准什么**——
    只给 rule_id 不够（``RULE_STYLE_REPETITION_TRIGRAM`` 单看名字读不出重复了什么）。
    """
    refs = [str(r).strip() for r in (getattr(issue, "evidence_refs", None) or []) if str(r).strip()]
    text = " / ".join(refs) if refs else str(getattr(issue, "message", "") or "").strip()
    if not text:
        return "（该规则未附带证据摘录，请回看本章原文）"
    return text[:_EVIDENCE_MAX_CHARS]


def _normalize_gate_override(raw: Any) -> tuple[list[str], str] | None:
    """把 ``ctx["gate_override"]`` 规范化为 ``(rule_ids, reason)``；非 dict → None。

    接受 ``{"rule_ids": [...], "reason": "..."}``；``rule_ids`` 允许 list/tuple/set，
    元素一律 str 化去空白。**``rule_ids`` 非 list-like（如裸字符串）时归一为空 list**，
    不是 None——空 list 会让下游 :func:`_resolve_gate_override` 按「未覆盖」阻断，
    与缺字段等效；``reason`` 缺省归一为空串（同样在下游被拦）。返回 None 只发生在
    ``raw`` 本身不是 dict 时。
    """
    if not isinstance(raw, dict):
        return None
    raw_ids = raw.get("rule_ids")
    if isinstance(raw_ids, (list, tuple, set, frozenset)):
        rule_ids = [str(r).strip() for r in raw_ids if str(r).strip()]
    else:
        rule_ids = []
    reason = str(raw.get("reason") or "").strip()
    return rule_ids, reason


def _resolve_gate_override(
    ctx: dict[str, Any],
    confirm_rule_ids: list[str],
) -> tuple[dict[str, Any] | None, str]:
    """校验 ``ctx["gate_override"]`` 对本次 confirm rule_id 的覆盖情况。

    返回 ``(accepted_record, failure_reason)``：
    - 全部覆盖且 ``reason`` 非空 ⇒ ``(record, "")``；
    - 缺失 / 部分覆盖 / reason 为空 ⇒ ``(None, <人类可读原因>)``。

    覆盖判定是**集合包含**：``rule_ids`` 必须涵盖本次实际命中的每一个 confirm rule id；
    多给（涵盖未被命中的规则）不算错——调用方可能一次声明一类问题的处理立场。

    本次**没有** confirm 命中时直接返回 ``(None, ...)``：不接受空声明留痕——
    ``_meta.gate_accepted_override`` 只应记录「确实批准了什么」，否则审计面会出现
    「用 gate_override 通过了，但什么都没被覆盖」的误导性记录。
    """
    if not confirm_rule_ids:
        return None, "本次无 confirm 命中（无需放行声明）"
    raw = ctx.get(_GATE_OVERRIDE_KEY)
    if raw is None:
        return None, "未提供 gate_override"
    parsed = _normalize_gate_override(raw)
    if parsed is None:
        return None, "gate_override 形态非法（应为 {'rule_ids': [...], 'reason': '...'}）"
    rule_ids, reason = parsed
    missing = [rid for rid in confirm_rule_ids if rid not in set(rule_ids)]
    if missing:
        return None, "gate_override.rule_ids 未覆盖：" + "、".join(missing)
    if not reason:
        return None, "gate_override.reason 为空（必须写明为何接受这些规则命中）"
    return (
        {
            "rule_ids": [rid for rid in confirm_rule_ids],
            "offered_rule_ids": rule_ids,
            "reason": reason,
            "accepted_at": now_iso(),
        },
        "",
    )


def _build_gate_confirm_note(
    gate: GateSummary,
    failure_reason: str,
) -> str:
    """编译 confirm 档阻断的 ``plan_json.revision_note``（与 blocking 同一落点）。

    内容要求（P0-1）：(a) 明确「这不是硬停、可显式接受放行」；(b) 逐条列出
    rule_id + 证据摘录；(c) 给出可直接照抄的 ``gate_override`` 形状；
    (d) 保留 failure_reason，让「为什么又被拦了」一眼可读（回退到既有改稿回路也适用）。
    """
    lines: list[str] = [
        "【质量门禁待确认】quality_gate 检测到必须显式接受的规则命中："
        + ("、".join(gate.confirm_rule_ids) if gate.confirm_rule_ids else "（未给出 rule_id）"),
        f"（未放行原因：{failure_reason}）",
        "",
        "命中与证据：",
    ]
    for issue in gate.confirm_issues:
        lines.append(f"- {getattr(issue, 'rule_id', '')}: {_issue_evidence_excerpt(issue)}")
    lines.append("")
    lines.append(
        "如确认这些重复是本意（例如刻意的复沓），带 gate_override 重新提交即可放行："
        '{"gate_override": {"rule_ids": '
        + json.dumps(list(gate.confirm_rule_ids), ensure_ascii=False)
        + ', "reason": "<为何接受>"}}'
    )
    lines.append("（改稿后重新审校并提交，门禁通过后本提示自动解除。）")
    return "\n".join(lines)


def _build_gate_mixed_note(
    revision_guidance: list[dict[str, Any]],
    blocking_rule_ids: list[str],
    gate: GateSummary,
    override_failure: str,
) -> str:
    """blocking 与 confirm **同时**命中时的 ``plan_json.revision_note``（2026-09-21 M1）。

    结构 = blocking 改稿建议（既有形态，作者要照着修硬伤）+ confirm 段（说明
    「修完硬伤还会被这条拦一次，以及怎样显式接受」）。为什么必须合成一份：
    confirm 单独存在时它有专属 note；但一旦 blocking 同时命中，改造前的实现会让
    confirm 整段消失，作者修掉硬伤后再提交才第一次撞上 confirm，且不知道要准备
    ``gate_override``——这正是 P0-1「不许在不知道批准什么的情况下批准」要堵的口子。
    """
    base = _build_gate_revision_note(revision_guidance, list(blocking_rule_ids))
    lines = [base, "", "【另有必须显式接受的命中】以上硬伤修完后，以下规则仍需签字放行："]
    for issue in gate.confirm_issues:
        lines.append(f"- {getattr(issue, 'rule_id', '')}: {_issue_evidence_excerpt(issue)}")
    lines.append(f"（未放行原因：{override_failure}）")
    lines.append(
        "带 gate_override 重新提交即可放行这些确认项（不影响上面的硬伤修复）："
        '{"gate_override": {"rule_ids": '
        + json.dumps(list(gate.confirm_rule_ids), ensure_ascii=False)
        + ', "reason": "<为何接受>"}}'
    )
    return "\n".join(lines)


def _read_plan_json(db_path: str, chapter_id: str) -> dict[str, Any] | None:
    """读 chapters.plan_json；chapter 不存在 → None；非法 JSON / 非 dict → {}。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    raw = row["plan_json"]
    if not raw:
        return {}
    try:
        plan = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return plan if isinstance(plan, dict) else {}


def _write_plan_json(db_path: str, chapter_id: str, plan: dict[str, Any]) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_gate_block(
    db_path: str,
    chapter_id: str,
    *,
    mode: str,
    rule_ids: list[str],
    note: str,
    gate: str = "block",
) -> None:
    """enforce 阻断落点：写 ``plan_json.revision_note`` + ``gate_blocked`` 标记。

    覆盖语义（重复阻断只重写，不追加）；写库失败只记日志——阻断本身（raise）
    才是主行为，不能因为落点失败改变 run 的失败语义。

    ``gate``（P0-1）：``"block"``（硬停，既有行为）/ ``"confirm"``（待显式接受）。
    额外键，既有读方（前端 ``gate_blocked.rule_ids`` / ``mode``）不受影响；
    它让「这次被拦是因为坏了、还是因为要你点头」在前端可区分。
    """
    try:
        plan = _read_plan_json(db_path, chapter_id)
        if plan is None:
            return
        plan["revision_note"] = note
        plan[_GATE_BLOCKED_KEY] = {
            "mode": mode,
            "gate": gate,
            "rule_ids": list(rule_ids),
            "blocked_at": now_iso(),
        }
        _write_plan_json(db_path, chapter_id, plan)
    except Exception as exc:  # noqa: BLE001 —— 落点失败不改变阻断语义
        _log.warning(
            "quality_gate persist blocked note failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )


def _clear_gate_block(db_path: str, chapter_id: str) -> None:
    """门禁通过时清除残留的 ``gate_blocked`` 标记（不改 revision_note——其属主是改稿流程）。"""
    try:
        plan = _read_plan_json(db_path, chapter_id)
        if plan is None or _GATE_BLOCKED_KEY not in plan:
            return
        plan.pop(_GATE_BLOCKED_KEY, None)
        _write_plan_json(db_path, chapter_id, plan)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "quality_gate clear blocked marker failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )


def _quality_gate_mode(ctx: dict[str, Any]) -> str:
    """解析 quality_gate 阻断模式。

    优先级（与现有 NOVELOS_* 环境变量口径对齐）：
    - ``ctx["quality_gate_mode"]``（调用方 / 测试用例可显式注入）。
    - ``NOVELOS_QUALITY_GATE`` 环境变量（``"enforce"`` / ``"report"``，默认 ``"enforce"``）。
    """
    mode = ctx.get("quality_gate_mode") or os.environ.get("NOVELOS_QUALITY_GATE", "enforce")
    mode = str(mode).strip().lower()
    return mode if mode in {"enforce", "report"} else "enforce"


# 回溯的 chapter-review run 条数上限（见 :func:`_reviewed_draft_version`）。
_REVIEWED_VERSION_LOOKBACK_RUNS = 5


def _positive_int(value: Any) -> int | None:
    """``value`` 是正整数（排除 ``bool``）→ int；否则 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _latest_review_report_draft_version(db_path: str, chapter_id: str) -> int | None:
    """从该章最近若干次 chapter-review run 的 checkpoint 里取**被审版本号**。

    取数形态：``checkpoint_json["author_review"]["__pause_payload__"]["review_report"]
    ["draft_version"]``——author_review 是 Human 节点，pause payload 里带着本次评审
    实际量过的那一版（``review_report.draft_version``，P1-1 后由
    ``resolve_draft`` 与正文同源产出）。该镜像键**不在** chapter-review 的
    ``checkpoint_exclude`` 里（前端评审卡要从它取 review_report），因此 run 走到
    终态后依然可读。

    按 ``started_at`` 由新到旧取前 ``_REVIEWED_VERSION_LOOKBACK_RUNS`` 条，
    返回第一条可解析出正整数版本的记录；都没有 → ``None``（调用方退回「取最新」，
    与改造前行为一致）。读库 / JSON 解析失败一律跳过（门禁不因观测数据缺失而炸）。
    """
    try:
        conn = get_connection(db_path)
    except Exception as exc:  # noqa: BLE001 —— 读不到库时不阻断门禁主逻辑
        _log.warning(
            "quality_gate reviewed-version lookup open failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return None
    try:
        rows = conn.execute(
            """
            SELECT wr.checkpoint_json FROM workflow_runs wr
            JOIN workflows wf ON wf.workflow_id = wr.workflow_id
            WHERE wr.chapter_id = ? AND wf.name = 'chapter-review'
            ORDER BY wr.started_at DESC
            LIMIT ?
            """,
            (chapter_id, _REVIEWED_VERSION_LOOKBACK_RUNS),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 —— 同上
        _log.warning(
            "quality_gate reviewed-version lookup query failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return None
    finally:
        conn.close()

    for row in rows:
        raw = row["checkpoint_json"]
        if not raw:
            continue
        try:
            checkpoint = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(checkpoint, dict):
            continue
        mirror = checkpoint.get("author_review")
        payload = mirror.get("__pause_payload__") if isinstance(mirror, dict) else None
        report = payload.get("review_report") if isinstance(payload, dict) else None
        version = report.get("draft_version") if isinstance(report, dict) else None
        resolved = _positive_int(version)
        if resolved is not None:
            return resolved
    return None


def _reviewed_draft_version(
    db_path: str, chapter_id: str, ctx: dict[str, Any]
) -> int | None:
    """解析「本次 quality_gate 该量哪一版草稿」——**被审版本**优先。

    为什么默认不是「最新」：门禁的语义是「放行**作者审过的那份稿**」。改造前
    ``build_quality_context`` 未传 ``draft_version`` ⇒ 门禁量的是**最新**一版；
    作者在评审后手改出新版本（``POST /drafts`` 在 DRAFTED / REVIEWED 下均允许，
    质量报告还专门用 Q8 人工占比鼓励它）就会出现「门禁批准的是没人审过的正文」——
    同一形状的错误（测量对象 ≠ 被审对象）在 P1-1 已在评审 / 核销两处修复，这里是
    提交侧的同一站点。

    优先级（先到先得）：

    1. ``ctx["draft_version"]``：调用方显式指定（start 端点该字段对所有 workflow
       都会进 ctx；commit 侧此前无人消费它）——显式 > 推断；
    2. 该章最近一次 chapter-review 的 ``review_report.draft_version``
       （见 :func:`_latest_review_report_draft_version`）——这才是「被审那一版」的权威来源；
    3. ``None``：无任何评审记录（如直接构造 ctx 的测试 / 历史数据）⇒ 退回
       ``build_quality_context`` 的「取最新」语义，与改造前逐字节一致。
    """
    explicit = _positive_int(ctx.get("draft_version"))
    if explicit is not None:
        return explicit
    return _latest_review_report_draft_version(db_path, chapter_id)


def _quality_gate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """quality_gate 节点。

    - 现场组装 :class:`QualityContext`（复用 :func:`packages.core.quality.service.build_quality_context`）；
      **量的是被审那一版**（``ctx["draft_version"]`` > 最近一次 chapter-review 的
      ``review_report.draft_version`` > 最新一版，见 :func:`_reviewed_draft_version`）；
    - 调 :class:`QualityEngine.evaluate`；
    - 落 ``quality_reports`` 表（与 ``commit`` 不在同一事务——见 :mod:`packages.core.quality.service` 注释）；
    - **blocking** error issue（``issues.is_blocking_issue``：severity=='error' 且 rule_id
      在阻断白名单）+ ``enforce`` 模式 ⇒ 抛 :class:`ValueError` 阻断；
      informational error（如显式 strict 前的 Q8）只随报告落库、不阻断
      （与 ``aggregate.compute_overall`` 的 blocking/informational 分组同口径，V3.9 批次 3.1）；
    - **confirm** issue（``issues.issue_gate == "confirm"``）+ ``enforce`` 模式 ⇒ 无有效
      ``ctx["gate_override"]`` 时同样阻断（同一落点、同一 ValueError 前缀），
      有有效声明时放行并把声明留痕（P0-1，2026-09-18）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    project_id = ctx.get("project_id")
    if not project_id:
        svc = StoryStateService(db_path)
        project_id = svc._project_id_for_chapter(  # noqa: SLF001
            get_connection(db_path), chapter_id
        )
    if not project_id:
        raise ValueError(f"chapter {chapter_id!r} not found")

    delta = ctx.get("delta") or {}
    snapshot_pre = ctx.get("snapshot_pre") or {}

    # Sprint V1.4：参照系消费可观测——quality_gate 现场采集本节点消费的 *.txt 清单
    # 写到节点返回 dict，供 checkpoint_json 暴露给前端；与 quality 评估同一时机，
    # 阻断时也会随 ctx 落盘（_update_run_checkpoint 在每节点完成后写一次）。
    reference_consumption = capture_reference_consumption(db_path, project_id)

    # P1-1 / 本批次第 3 项：门禁必须量**被审那一版**，而不是「最新那一版」——
    # 作者评审后手改出新版本时，改造前的「取最新」会让门禁批准没人审过的正文。
    reviewed_version = _reviewed_draft_version(db_path, chapter_id, ctx)
    quality_ctx = build_quality_context(
        db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        delta=delta,
        snapshot_pre=snapshot_pre,
        run_id=ctx.get("run_id"),
        draft_version=reviewed_version,
    )
    report = QualityEngine().evaluate(quality_ctx)

    mode = _quality_gate_mode(ctx)
    error_issues = [i for i in report.issues if i.severity == "error"]
    # V3.9 批次 3.1：只有 blocking error 触发 enforce 阻断；informational error 仅落库。
    blocking_issues = [i for i in error_issues if is_blocking_issue(i)]
    blocking_rule_ids = [i.rule_id for i in blocking_issues]

    # P0-1（2026-09-18）：后果档分组。block 与 confirm 都来自 issues.issue_gate
    # （唯一权威）；confirm 档的放行条件在这里裁定，裁定结果先落报告 _meta 再落库，
    # 保证「接受声明」与本次评估同一次留痕（不依赖第二次写入）。
    gate_summary = summarize_gates(report.issues)
    accepted_override, override_failure = _resolve_gate_override(
        ctx, list(gate_summary.confirm_rule_ids)
    )

    # Sprint V1.4：把参照系消费清单持久化到 quality_reports._meta.reference_consumption，
    # 让 /api/chapters/{cid}/quality 端点直接返回，UI 不必再回查 runs.checkpoint_json。
    # P0-1：同一次写入带上 gate_summary（block/confirm 摘要）与已接受的 gate_override。
    meta = dict(report.meta or {})
    meta["reference_consumption"] = reference_consumption
    meta["gate_summary"] = gate_summary.as_meta()
    if accepted_override is not None:
        meta["gate_accepted_override"] = accepted_override
    report.meta = meta
    QualityService(db_path).save_report(
        report,
        project_id=project_id,
        chapter_id=chapter_id,
        run_id=ctx.get("run_id"),
    )

    # Sprint V1.4：enforce 模式阻断时，把每低分维度的可执行改稿建议结构化带上。
    # revision_guidance 写进 ctx['quality_gate']（checkpoint_json 会自动收录），
    # 同时把整段结构化 payload JSON 化追加到 ValueError 信息里——run FAILED 时
    # runs.error 已包含它，前端 QualityPanel 可直接从错误字符串里解析。
    # 注：guidance 覆盖全部 error（含 informational，仍有改稿价值），阻断只看 blocking。
    # P0-1：confirm 档命中时同样带上 guidance——作者要么改稿，要么显式接受，
    # 两条出路都要有可执行建议。
    revision_guidance: list[dict[str, Any]] = []
    if error_issues or gate_summary.confirm_issues:
        revision_guidance = _build_revision_guidance(report, report.issues)

    # P0-1：confirm 档在 enforce 模式下未获有效声明 ⇒ 与 blocking 同级阻断。
    confirm_unresolved = bool(gate_summary.confirm_issues) and accepted_override is None
    should_block = mode == "enforce" and (bool(blocking_issues) or confirm_unresolved)

    ctx["quality_gate"] = {
        "blocked": should_block,
        "mode": mode,
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
        "gate_summary": gate_summary.as_meta(),
        "gate_accepted_override": accepted_override,
    }

    if should_block:
        # V3.9 批次 4.1：先把改稿出路写进 chapter（plan_json.revision_note +
        # gate_blocked 标记），再抛错阻断。作者可在章节详情页一键「按门禁建议改稿」
        # 复用既有写正文（revise）→ 审校回路，不必手工改稿。
        # P0-1：block 与 confirm 复用同一条落点，不另开通道。
        # 2026-09-21 检修 M1：blocking 与 confirm **同时**命中时，confirm 的信息此前被
        # 整体吞掉（rule_ids 只列 blocking、error 串无 override_error/evidence）——
        # 作者修掉硬伤后再提交才第一次见到 confirm，且无从知道该准备什么 gate_override。
        # 修法：blocking 优先（硬停不可协商）不变，但 rule_ids 取两组并集、note 与 error
        # 串把 confirm 侧一并带上。既有解析方按 "| guidance=" 切分，新段插在它之前。
        if blocking_issues:
            gate_kind = "block"
            block_rule_ids = list(blocking_rule_ids)
            if gate_summary.confirm_rule_ids:
                gate_kind = "block+confirm"
                for rid in gate_summary.confirm_rule_ids:
                    if rid not in block_rule_ids:
                        block_rule_ids.append(rid)
                note = _build_gate_mixed_note(
                    revision_guidance, blocking_rule_ids, gate_summary, override_failure
                )
            else:
                note = _build_gate_revision_note(revision_guidance, block_rule_ids)
        else:
            block_rule_ids = list(gate_summary.confirm_rule_ids)
            note = _build_gate_confirm_note(gate_summary, override_failure)
            gate_kind = "confirm"
        _persist_gate_block(
            db_path,
            chapter_id,
            mode=mode,
            rule_ids=block_rule_ids,
            note=note,
            gate=gate_kind,
        )
        # 与现有 _commit_node 失败语义一致：抛 ValueError 让 run FAILED，
        # chapter 保持当前状态（当前章节 status=REVIEWED；error 阻断不会推到 COMMITTED）。
        # 在错误信息里把 revision_guidance 序列化为可解析段：
        #   "quality gate blocked: <rule_ids> | guidance=<json>"
        #   （confirm 档再补 "| gate=confirm | evidence=..."，位置在各 rule_id 之后、
        #     "| guidance=" 之前，故按 "| guidance=" 切分的既有解析方零影响）
        # 前端 / 测试可按 "| guidance=" 分隔；JSON 解析失败也不影响主信息。
        try:
            guidance_json = json.dumps(revision_guidance, ensure_ascii=False)
        except (TypeError, ValueError):
            guidance_json = "[]"
        if gate_kind == "block+confirm":
            # blocking 优先（不可覆盖），同时把 confirm 侧的存在、规则清单与证据带上——
            # 作者必须在同一屏里看见「有硬伤」和「还有一处要你签字」，否则修掉硬伤后
            # 再被 confirm 拦一次会完全不知道为什么（2026-09-21 检修 M1）。
            evidence = " ; ".join(
                f"{getattr(i, 'rule_id', '')}: {_issue_evidence_excerpt(i)}"
                for i in gate_summary.confirm_issues
            )
            raise ValueError(
                f"quality gate blocked: {block_rule_ids} | gate=block+confirm"
                f" | confirm_rule_ids={list(gate_summary.confirm_rule_ids)}"
                f" | override_error={override_failure} | evidence={evidence}"
                f" | guidance={guidance_json}"
            )
        if gate_kind == "confirm":
            evidence = " ; ".join(
                f"{getattr(i, 'rule_id', '')}: {_issue_evidence_excerpt(i)}"
                for i in gate_summary.confirm_issues
            )
            raise ValueError(
                f"quality gate blocked: {block_rule_ids} | gate=confirm"
                f" | override_error={override_failure} | evidence={evidence}"
                f" | guidance={guidance_json}"
            )
        raise ValueError(
            f"quality gate blocked: {block_rule_ids} | guidance={guidance_json}"
        )

    # 未阻断（report 模式 / 无 blocking error / confirm 已显式接受）：
    # 清除上一次残留的 gate_blocked 标记。
    # revision_note 不在此清除——其属主是改稿流程（review 驳回 / gate 阻断写入）。
    _clear_gate_block(db_path, chapter_id)

    return {
        "quality_report": report.model_dump(by_alias=True),
        "quality_gate_mode": mode,
        "quality_report_id": report.report_id,
        "quality_overall": int(report.overall),
        # 本节点实际量过的那一版（= 被审版本；无评审记录时为最新一版）——
        # 前端 / 测试 / 排障直接读它，不必回查 run 记录（P1-1 的可观测面）。
        "quality_reviewed_draft_version": report.draft_version,
        "quality_error_count": len(error_issues),
        "quality_blocking_count": len(blocking_issues),
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
        "quality_gate_summary": gate_summary.as_meta(),
        "quality_gate_accepted_override": accepted_override,
    }


def _high_risk_approval_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：仅在 observer_payload 含 HIGH / definition / rule change 时需要人工审批。
    human_input={"approved": true} → 通过。
    """
    needs = bool(ctx.get("needs_high_risk_approval"))
    hi = ctx.get("human_input") or {}
    approved = bool(hi.get("approved")) if isinstance(hi, dict) else False
    ctx["_high_risk_approved"] = approved

    if not needs:
        # 不需要审批：直接通过
        return {"high_risk_required": False, "human_input": hi}

    if approved:
        return {"high_risk_required": True, "human_input": hi}

    message = (
        "Observer 检测到 HIGH 风险 / definition / world_kind=rule change，请人工审批"
    )
    # 源锚定告警摘要（2026-09-26 批次，恒 warning——只附摘要供人工审批参考，
    # **不改变**本节点判定与审批语义；findings 产自 inject_validate 节点）。
    _findings = ctx.get("source_anchoring")
    if isinstance(_findings, list) and _findings:
        _dict_findings = [f for f in _findings if isinstance(f, dict)]
        if _dict_findings:
            counts = Counter(str(f.get("rule_id", "?")) for f in _dict_findings)
            digest = "，".join(f"{rid}×{n}" for rid, n in counts.most_common())
            samples = " | ".join(
                str(f.get("sample", ""))[:60] for f in _dict_findings[:3]
            )
            message += (
                f"【源锚定告警 {len(_dict_findings)} 条：{digest}】"
                f"示例：{samples}"
            )
    payload = {
        "stage": "chapter-commit.high_risk_approval",
        "message": message,
        "delta_id": ctx.get("delta_id"),
        "changes": {
            "character_changes": ctx.get("observer_payload", {}).get("character_changes", []),
            "world_changes": ctx.get("observer_payload", {}).get("world_changes", []),
        },
    }
    raise PauseRequested(payload)
