"""chapter_review 工作流（Sprint 4-A；revise 语义 Sprint 5 补全，闭环 PRD §59/§87；
V1.3 新增 critic 节点；P0 默认 always）。

节点列表：
- ``basic_checks`` (Transform) —— 草稿存在性、字数偏离 target ±15% 记 warning 进
  ctx["review_report"]、禁用词扫描（默认 forbidden_words=[仿佛,如同,本章目标]）。
- ``critic_review`` (AI) —— V1.3 LLM 评审员。调 ``critic`` agent 对草稿生成**建议性**
  结构化报告；写入 ctx["critic_report"]，并合并进 author_review 的 pause_payload
  （键名 ``critic_report``），供人工审批界面渲染。
  **仅建议、不拦截**：任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级
  ``critic_status='failed'`` 且 ``critic_report=None``，**不**阻断人工审批 / run 终态。
  P0 默认模式改为 ``always``（每章都评），``NOVELOS_CRITIC_MODE`` / ``ctx['critic_mode']`` 仍覆盖。
- ``author_review`` (Human) —— payload=review_report + critic_report；human_input 三态决议：
  ``{"approved": true}`` 通过；``{"approved": false}`` 拒绝（run FAILED）；
  ``{"approved": false, "revise": true, "note": str?}`` 驳回并改稿（run FAILED、
  error='rejected-for-revision'，chapter 保持 DRAFTED，note 落 plan_json.revision_note）。
- ``mark_reviewed`` (State) —— chapters.status DRAFTED→REVIEWED（仅当 DRAFTED 状态可走）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.model_router.router import capability_for
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.wordcount import classify_prose_length
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode

DEFAULT_FORBIDDEN_WORDS = ["仿佛", "如同", "本章目标"]
_CRITIC_PROMPT_VERSION = "critic:v1"
# 单章目标字数默认。与 chapter_plan / project-init 的 DEFAULT_CHAPTER_WORD_COUNT(3000)
# 同一口径（用户拍板单章约 3000 字）；builders._DEFAULT_TARGET_WORD_COUNT(2200) 是
# context_engine 侧的另一 fallback 口径，两处允许不同：本值仅在请求体与 plan_json
# 均未给出 expected_word_count 时兜底。
_DEFAULT_TARGET_WORD_COUNT = 3000

_log = logging.getLogger(__name__)

# V3 P0-1：critic LLM 评审员采样模式开关
# - ``off``：完全跳过 critic LLM 调用，节点直接返回 ``critic_status='skipped'``。
# - ``sample``：仅当 ``chapter.number % 5 == 0`` 时调用 LLM；其余章节跳过。
# - ``always``：每章都评（P0 默认）。
# 优先级：``ctx["critic_mode"]``（来自 start review 请求 body） > ``NOVELOS_CRITIC_MODE`` 环境变量 > 默认 ``always``。
_VALID_CRITIC_MODES = ("off", "sample", "always")


def _resolve_critic_mode(ctx: dict[str, Any]) -> str:
    """按优先级解析 critic 模式：ctx > env > 默认 ``always``。非法值回退到 ``always``。"""
    raw = ctx.get("critic_mode")
    if isinstance(raw, str) and raw in _VALID_CRITIC_MODES:
        return raw
    env = os.environ.get("NOVELOS_CRITIC_MODE", "always").strip().lower()
    return env if env in _VALID_CRITIC_MODES else "always"


def _should_invoke_critic(mode: str, chapter_number: int | None) -> bool:
    """判定当前章节是否触发 critic LLM 调用。``chapter_number`` 为 None（查不到）→ 保守调。"""
    if mode == "off":
        return False
    if mode == "always":
        return True
    # sample：仅 chapter_number % 5 == 0 时调用；number 缺失时保守调
    if chapter_number is None:
        return True
    return chapter_number % 5 == 0


def _fetch_chapter_number(db_path: str, chapter_id: str) -> int | None:
    """从 chapters 表取 number 字段；章节不存在或异常 → None（视同 always）。"""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT number FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 任何读库异常均降级为 None
        _log.warning(
            "chapter_review.critic number lookup failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return None
    if row is None:
        return None
    try:
        return int(row["number"])
    except (TypeError, ValueError):
        return None


def _basic_checks_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    # 目标字数：请求体显式传入 > plan_json.expected_word_count > 默认 3000
    target = int(ctx.get("target_word_count") or 0)
    if not target:
        try:
            conn = get_connection(db_path)
            try:
                prow = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
                ).fetchone()
            finally:
                conn.close()
            if prow and prow["plan_json"]:
                plan = json.loads(prow["plan_json"])
                target = int(plan.get("expected_word_count") or 0)
        except Exception:
            target = 0
    if not target:
        target = _DEFAULT_TARGET_WORD_COUNT

    conn = get_connection(db_path)
    try:
        # 用户指定 draft_version（ctx["draft_version"]）时按版本精确读取；
        # 否则保持原"取最新"语义，便于跨模型文风对比时复审指定稿。
        version = ctx.get("draft_version")
        if version:
            draft_row = conn.execute(
                "SELECT content, version FROM drafts WHERE chapter_id = ? AND version = ?",
                (chapter_id, int(version)),
            ).fetchone()
        else:
            draft_row = conn.execute(
                """
                SELECT content, version FROM drafts WHERE chapter_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()
    finally:
        conn.close()

    if draft_row is None:
        if version:
            raise ValueError(f"chapter {chapter_id!r} has no draft version {version}")
        raise ValueError(f"chapter {chapter_id!r} has no draft; run chapter-write first")

    prose = draft_row["content"] or ""
    reviewed_version = int(draft_row["version"])
    # V3.7：字数带硬约束 —— ±15% 升 warning（带 rule_id），超 ±30% 追加到 errors。
    # classify 既出 visible_chars（word_count）又出 band / status / deviation_pct，避免重复调用。
    classify = classify_prose_length(prose, target)
    word_count = classify["visible_chars"]
    deviation_pct = classify["deviation_pct"]
    abs_dev_pct = abs(deviation_pct)
    band_low, band_high = classify["band_low"], classify["band_high"]
    within_range = abs_dev_pct <= 15.0
    over_band = abs_dev_pct > 30.0

    # V3.8：去 AI 味确定性检测（纯规则、不调用 LLM）
    ai_hits = scan_ai_patterns(prose)
    # 向后兼容：保留 forbidden_word_hits 字段（由原 DEFAULT_FORBIDDEN_WORDS 扩展而来）
    forbidden_hits: list[str] = []
    for hit in ai_hits:
        if hit.get("rule_id") == "AI-FORBIDDEN-WORD":
            forbidden_hits.extend(hit.get("words", []))
    forbidden_hits = sorted(set(forbidden_hits))

    warnings: list[str] = []
    errors: list[dict[str, Any]] = []
    if not within_range:
        warnings.append(
            f"[W-LEN-DEVIATION] visible={word_count} target={target} "
            f"deviation={deviation_pct:+.1f}% (band {band_low}~{band_high})"
        )
    if over_band:
        # 超 ±30%：error 级条目，供 author_review / 前端 reviewer UI 消费
        # （与 signing_check/quality_gate 的阻断语义对齐——errors 不阻断 run，但
        # 在 review UI 上以「严重」色渲染，作者可见）。
        errors.append({
            "rule_id": "W-LEN-DEVIATION",
            "severity": "error",
            "message": (
                f"[W-LEN-DEVIATION] visible={word_count} target={target} "
                f"deviation={deviation_pct:+.1f}% exceeds band {band_low}~{band_high} "
                f"(±30% 阈值)"
            ),
            "word_band": {"low": band_low, "high": band_high},
            "visible_chars": word_count,
            "target": target,
            "deviation_pct": deviation_pct,
        })
    if forbidden_hits:
        warnings.append(f"禁用词命中：{','.join(forbidden_hits)}")
    for hit in ai_hits:
        if hit.get("severity") == "error":
            errors.append(hit)
        else:
            warnings.append(f"[{hit['rule_id']}] {hit['message']}")

    report = {
        "chapter_id": chapter_id,
        "draft_version": reviewed_version,
        "word_count": word_count,
        "target_word_count": target,
        "within_range": within_range,
        "deviation": round(deviation_pct / 100, 4),
        "deviation_pct": deviation_pct,
        "word_band": {"low": band_low, "high": band_high},
        "forbidden_word_hits": forbidden_hits,
        "ai_pattern_hits": ai_hits,
        "warnings": warnings,
        "errors": errors,
    }
    return {"review_report": report}


def _collect_critic_inputs(
    db_path: str, chapter_id: str, draft_version: int | None = None
) -> tuple[str, str, dict[str, Any]]:
    """收集 critic 节点所需输入：draft_text、project_id、plan_summary。

    返回 ``(draft_text, project_id, plan_summary_dict)``。任一环节失败抛 ValueError，
    让 critic 节点降级。

    ``draft_version``：用户显式指定时按版本取；None 时维持"取最新"语义。
    """
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            """
            SELECT project_id, title, plan_json
            FROM chapters WHERE chapter_id = ?
            """,
            (chapter_id,),
        ).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        if draft_version:
            draft_row = conn.execute(
                "SELECT content FROM drafts WHERE chapter_id = ? AND version = ?",
                (chapter_id, int(draft_version)),
            ).fetchone()
        else:
            draft_row = conn.execute(
                """
                SELECT content FROM drafts WHERE chapter_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()
    finally:
        conn.close()
    if draft_row is None:
        if draft_version:
            raise ValueError(f"chapter {chapter_id!r} has no draft version {draft_version}")
        raise ValueError(f"chapter {chapter_id!r} has no draft")
    project_id = chap_row["project_id"]
    raw = chap_row["plan_json"] or "{}"
    try:
        plan = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        plan = {}
    if not isinstance(plan, dict):
        plan = {}
    plan_summary = {
        "chapter_goal": plan.get("chapter_goal"),
        "key_beats": plan.get("key_beats") or [],
    }
    return (draft_row["content"] or ""), project_id, plan_summary


def _collect_settings_digest(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """V3.9：组装 critic 的设定上下文摘要。

    - world_rules 全部规则的 name + statement（顺序：world_rule_id ASC）。
    - 主要角色档案：name + core_json 中的 one_line 字段（缺则用 statement 兜底）。
    - 上限 8 个角色（按 character_id ASC 截断）。
    - core_json 是非法 JSON 字符串 → **跳过该角色**（其余角色保留），不抛错。
    - 全部失败 / 表为空 → 返回 []，**不**阻断 critic 调用；critic 拿空 settings_digest
      仍能基于 plan_summary + open_hooks 正常出报告（OOC 维度无新发现）。
    """
    if not project_id:
        return []
    try:
        conn = get_connection(db_path)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "chapter_review.settings_digest db open failed: project_id=%s err=%s",
            project_id, exc,
        )
        return []
    try:
        try:
            rule_rows = conn.execute(
                "SELECT name, statement FROM world_rules "
                "WHERE project_id = ? ORDER BY world_rule_id ASC",
                (project_id,),
            ).fetchall()
            char_rows = conn.execute(
                "SELECT name, core_json FROM characters "
                "WHERE project_id = ? ORDER BY character_id ASC LIMIT 8",
                (project_id,),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "chapter_review.settings_digest query failed: project_id=%s err=%s",
                project_id, exc,
            )
            return []
    finally:
        conn.close()

    digest: list[dict[str, Any]] = []
    for r in rule_rows:
        if not isinstance(r["name"], str):
            continue
        digest.append({
            "kind": "world_rule",
            "name": r["name"],
            "one_line": r["statement"] if isinstance(r["statement"], str) else "",
        })
    for r in char_rows:
        if not isinstance(r["name"], str):
            continue
        raw_core = r["core_json"]
        # 解析 core_json：字符串 → json.loads；已是 dict → 直接用；
        # 解析失败（非法 JSON / 异常类型）→ 跳过该角色。
        if isinstance(raw_core, str):
            if not raw_core:
                core: dict[str, Any] | None = {}
            else:
                try:
                    parsed = json.loads(raw_core)
                except (TypeError, ValueError):
                    _log.warning(
                        "chapter_review.settings_digest skip character with bad core_json: "
                        "project_id=%s name=%s",
                        project_id, r["name"],
                    )
                    continue
                core = parsed if isinstance(parsed, dict) else None
        elif isinstance(raw_core, dict):
            core = raw_core
        else:
            # None / 其他类型：core_json 不可用，跳过该角色（与损坏 JSON 同样保守处理）
            _log.warning(
                "chapter_review.settings_digest skip character with unusable core_json: "
                "project_id=%s name=%s type=%s",
                project_id, r["name"], type(raw_core).__name__,
            )
            continue

        one_line = ""
        if isinstance(core, dict):
            val = core.get("one_line")
            if isinstance(val, str):
                one_line = val
            else:
                # 兜底：用 statement（结构不固定时的最小有用描述）
                stmt = core.get("statement")
                if isinstance(stmt, str):
                    one_line = stmt
        digest.append({
            "kind": "character",
            "name": r["name"],
            "one_line": one_line,
        })
    return digest


def _collect_open_hooks(db_path: str, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """收集项目下开放伏笔（status ∈ OPEN/ACTIVE/ESCALATED），按 importance 降序截断。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT hook_id, name, importance, status
            FROM hooks
            WHERE project_id = ?
              AND status IN ('OPEN','ACTIVE','ESCALATED')
            ORDER BY importance DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "hook_id": r["hook_id"],
            "name": r["name"],
            "importance": float(r["importance"] or 0.0),
            "summary": r["name"],  # hooks 表无 description 列；以 name 作为摘要文本
        }
        for r in rows
    ]


def _critic_review_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """V1.3 LLM 评审员节点。

    行为：
    1. 取最新 draft_text + plan_summary + 项目下 open_hooks。
    2. 调 ``run_agent(..., agent_name='critic', expected='critic', mock_script=...)``。
       runner 内部走 parse_json → validate_contract("critic") → 写 ai_call_logs；本节点只
       关心最终输出 payload。
    3. 任何异常（PromptNotFound / Provider / 契约校验失败 / 输出字段缺失）→ 降级
       ``critic_status='failed'`` + ``critic_report=None``，**不**抛错。
    4. ``critic_status='ok'`` 时 ``critic_report`` 写入 ctx，供 author_review 合并进 pause_payload。

    V3 P0-1：critic 采样模式开关（off / sample / always）。跳过时返回 ``critic_status='skipped'``
    + ``critic_skipped=True`` + ``critic_mode=<mode>``，工作流状态机不受影响（author_review
    Human 节点仍按 PauseRequested 走，pause_payload 中 ``critic_status='skipped'`` 不合并 critic_report）。

    返回 ``{"critic_status": "ok|failed|skipped", "critic_report": dict|None,
    "critic_error": str|None, "critic_skipped": bool, "critic_mode": str}``。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("critic")

    # V3 P0-1：解析 critic_mode 并按 chapter_number 判定是否跳过
    mode = _resolve_critic_mode(ctx)
    chapter_number = _fetch_chapter_number(db_path, chapter_id)
    if not _should_invoke_critic(mode, chapter_number):
        _log.info(
            "chapter_review.critic skipped: chapter_id=%s mode=%s chapter_number=%s",
            chapter_id, mode, chapter_number,
        )
        return {
            "critic_status": "skipped",
            "critic_report": None,
            "critic_error": None,
            "critic_skipped": True,
            "critic_mode": mode,
        }

    try:
        draft_text, project_id, plan_summary = _collect_critic_inputs(
            db_path, chapter_id, draft_version=ctx.get("draft_version")
        )
        open_hooks = _collect_open_hooks(db_path, project_id)
        # V3.9：设定上下文摘要（world_rules + 角色档案 one_line），供 critic 做 OOC 审查。
        # 失败 / 空 → settings_digest=[]，不阻断 critic（与 §5 契约一致）。
        settings_digest = _collect_settings_digest(db_path, project_id)
    except Exception as exc:  # noqa: BLE001 —— 输入收集失败即降级
        _log.warning(
            "chapter_review.critic inputs collection failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "critic_status": "failed",
            "critic_report": None,
            "critic_error": f"inputs: {exc}",
            "critic_skipped": False,
            "critic_mode": mode,
        }

    # V3.8：把确定性检测结果摘要注入 critic payload，供 LLM 参考。
    # 优先从 basic_checks 已产出的 review_report 读取；若未经历该节点（单测/异常），
    # 退化为对 draft_text 直接扫描，保证节点自洽。
    ai_hits = (ctx.get("review_report") or {}).get("ai_pattern_hits")
    if ai_hits is None:
        ai_hits = scan_ai_patterns(draft_text)
    deterministic_hints = {
        "ai_pattern_hit_count": len(ai_hits),
        "ai_pattern_summary": [
            {"rule_id": h.get("rule_id"), "message": h.get("message"), "count": h.get("count")}
            for h in ai_hits
        ],
    }

    payload = {
        "agent": "critic",
        "prompt_version": _CRITIC_PROMPT_VERSION,
        "chapter": {
            "title": None,
            "target_word_count": int(ctx.get("target_word_count") or _DEFAULT_TARGET_WORD_COUNT),
            "expected_role": ctx.get("expected_role"),
            "chapter_id": chapter_id,
        },
        "draft_text": draft_text,
        "plan_summary": plan_summary,
        "open_hooks": open_hooks,
        "settings_digest": settings_digest,
        "deterministic_hints": deterministic_hints,
    }

    try:
        out = run_agent(
            db_path,
            "critic",
            payload,
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="critic",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("critic")
            ),
        )
        if not isinstance(out, dict):
            raise ValueError(f"critic output not dict: {type(out).__name__}")
        # 软校验：quote 必须能溯源到 draft_text（trim 后 substring）；不溯源的 issue 直接丢弃
        strengths = out.get("strengths")
        issues = out.get("issues")
        if isinstance(issues, list):
            cleaned_issues: list[dict[str, Any]] = []
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                quote = issue.get("quote")
                if not isinstance(quote, str):
                    continue
                quote_trim = quote.strip()
                if not quote_trim or quote_trim not in draft_text:
                    _log.warning(
                        "chapter_review.critic dropped untraceable issue: chapter_id=%s quote=%r",
                        chapter_id, quote[:30],
                    )
                    continue
                cleaned_issues.append(issue)
            out["issues"] = cleaned_issues
        elif issues is not None:
            out["issues"] = []
        if strengths is None:
            out["strengths"] = []
        elif not isinstance(strengths, list):
            out["strengths"] = []
        return {
            "critic_status": "ok",
            "critic_report": out,
            "critic_error": None,
            "critic_skipped": False,
            "critic_mode": mode,
        }
    except Exception as exc:  # noqa: BLE001 —— 任何 LLM / 契约 / parse 失败均降级
        _log.warning(
            "chapter_review.critic degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "critic_status": "failed",
            "critic_report": None,
            "critic_error": str(exc),
            "critic_skipped": False,
            "critic_mode": mode,
        }


def _author_review_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：抛 PauseRequested 等 author 决议。
    human_input={"approved": true} → 通过；其他（"approved":false 或缺 approved）→ 拒绝。

    本 fn 总是抛 PauseRequested；resume 时会被重跑读 ctx["human_input"]["approved"]。
    """
    report = ctx.get("review_report") or {}
    critic_report = ctx.get("critic_report") if ctx.get("critic_status") == "ok" else None
    critic_status = ctx.get("critic_status") or "skipped"
    # V3 P0-1：把 critic_mode/skipped 透传到 pause_payload，便于前端观测
    payload = {
        "stage": "chapter-review",
        "message": "请审查章节草稿并批准或驳回",
        "review_report": report,
        "critic_status": critic_status,
        "critic_report": critic_report,
        "critic_mode": ctx.get("critic_mode"),
        "critic_skipped": bool(ctx.get("critic_skipped")),
    }
    # 先把 human_input 已批准的标志 merge 进 ctx（提供给 mark_reviewed 用）
    hi = ctx.get("human_input") or {}
    ctx["_author_approved"] = bool(hi.get("approved")) if isinstance(hi, dict) else False
    if ctx["_author_approved"]:
        # 已通过审批：直接返回（不抛 PauseRequested）
        return {"author_decision": "approved", "human_input": hi}
    # 驳回并改稿（revise:true）时把 note 透传给 mark_reviewed，供 revision_note 落库
    ctx["_revision_note"] = hi.get("note") if isinstance(hi, dict) else None
    raise PauseRequested(payload)


class _RejectForRevision(Exception):
    """revise 分支专用异常：让 run 以 FAILED + error='rejected-for-revision' 收尾。

    引擎侧无 CANCELLED 触发路径（engine._run_nodes 只在节点抛异常时置 FAILED；
    节点内直接改 ctx 的 status 会被 _finalize_run 无条件覆盖为 COMPLETED），
    故沿用 FAILED 终态、以 error 字段区分，改动最小且不碰 engine/DDL。
    error 字段取 ``str(exc)``（engine._run_nodes），故 args[0] 直接写标识值。
    """

    def __init__(self) -> None:
        super().__init__("rejected-for-revision")


class _Rejected(Exception):
    """纯驳回（approved=false，无 revise）的终态标记：run FAILED + error='rejected'，
    章节保持 DRAFTED。与 _RejectForRevision 同机制（error 字段区分，不碰 engine/DDL）。"""

    def __init__(self) -> None:
        super().__init__("rejected")


def _mark_reviewed_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    # resume 场景：author_review 节点被 SKIPPED（不会再跑）；直接读 human_input
    hi = ctx.get("human_input") or {}
    approved = bool(hi.get("approved")) if isinstance(hi, dict) else bool(ctx.get("_author_approved"))
    if not approved:
        if isinstance(hi, dict) and hi.get("revise"):
            # 驳回并改稿：chapter 保持 DRAFTED，note 追加进 plan_json.revision_note，
            # 供下次 chapter-write 的 load_plan 参考；run 以 FAILED(rejected-for-revision) 收尾
            note = hi.get("note") if isinstance(hi.get("note"), str) else ""
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"chapter {chapter_id!r} not found")
                raw = row["plan_json"]
                plan = json.loads(raw) if raw else {}
                if not isinstance(plan, dict):
                    plan = {}
                if note:
                    plan["revision_note"] = note
                else:
                    plan.pop("revision_note", None)
                conn.execute(
                    "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), now_iso(), chapter_id),
                )
                conn.commit()
            finally:
                conn.close()
            raise _RejectForRevision()
        raise _Rejected()

    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        if cur["status"] != "DRAFTED":
            raise ValueError(
                f"chapter {chapter_id!r} status={cur['status']!r}，必须先 DRAFTED 才能 REVIEWED"
            )
        conn.execute(
            "UPDATE chapters SET status = 'REVIEWED', updated_at = ? WHERE chapter_id = ?",
            (now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status_after": "REVIEWED"}


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("basic_checks", "Transform", _basic_checks_node),
        WorkflowNode(
            "critic_review", "AI", _critic_review_node, agent_name="critic"
        ),
        WorkflowNode("author_review", "Human", _author_review_node),
        WorkflowNode("mark_reviewed", "State", _mark_reviewed_node),
    ]


WORKFLOW = {
    "name": "chapter-review",
    "version": "v1",
    "description": (
        "basic_checks → critic_review(AI, advisory, default always) → author_review(Human) → mark_reviewed "
        "(DRAFTED→REVIEWED)；revise 驳回改稿闭环；critic 仅做建议、不拦截"
    ),
    "nodes": _build_nodes(),
    # V1.0 checkpoint 写放大优化（Sprint V1.5）：author_review 是 Human 节点（可 PAUSE）；
    # PAUSE 时 checkpoint 落盘整张 ctx，下游 mark_reviewed 节点**不读** review_report / critic_*
    # 字段（mark_reviewed 仅依赖 human_input + _author_approved + _revision_note）。
    # 这两个字段较大（critic_report 含 issues/strengths/revision_guidance；review_report 含
    # 完整字数/禁用词扫描结果），落盘只增体积不影响 resume——可安全 exclude。
    # 注：author_review 的 __pause_payload__ 单独存在 ctx['author_review'] 内，不受 exclude 影响，
    # 前端仍可读到 review_report / critic_report（reviewer UI 必需）。
    "checkpoint_exclude": [
        "review_report",
        "critic_report",
        "critic_status",
        "critic_error",
        "critic_skipped",
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_review.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
