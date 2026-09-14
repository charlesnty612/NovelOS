"""chapter_review 工作流（Sprint 4-A；revise 语义 Sprint 5 补全，闭环 PRD §59/§87；
V1.3 新增 critic 节点；P0 默认 always；V1.3 新增 deep_review 二审 AI 节点）。

节点列表：
- ``basic_checks`` (Transform) —— 草稿存在性、字数带判定进 ctx["review_report"]
  （出带记 warning；带外偏离超过该侧带边缘到 target 的距离记 error。两级阈值全部由
  ``resolve_band_config`` 生效带派生，见 V3.9 批次 5.2）、禁用词扫描（默认
  forbidden_words=[仿佛,如同,本章目标]）；V3.9 批次 5.7 起同时一次性取齐
  critic / deep_review 共享输入（ctx["review_inputs"]，见下）；题材库 P1b 起项目
  绑定题材包时额外产出 ``review_report.genre_check``（核销层 v2：配比偏差 +
  节奏红线，``GENRE-`` 前缀 issue 恒 warning，**report-only 不阻断**；未绑定 →
  该段整体缺席，零行为变化）。题材库 P2：绑定题材包时另做两件事——(a) payload
  ``style_constraints.forbidden_words`` 非空 → 确定性禁词扫描，命中以
  ``[GENRE-FORBIDDEN-WORD] <词> ×N`` 进 ``review_report.warnings``（warning 级）；
  (b) payload ``critic_rubric`` 投影为 ``genre_rubric`` 段随 ``review_inputs``
  下传 critic / deep_review（两者 payload 同得该键）。
- ``critic_review`` (AI) —— V1.3 LLM 评审员。调 ``critic`` agent 对草稿生成**建议性**
  结构化报告；写入 ctx["critic_report"]，并合并进 author_review 的 pause_payload
  （键名 ``critic_report``），供人工审批界面渲染。
  **仅建议、不拦截**：任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级
  ``critic_status='failed'`` 且 ``critic_report=None``，**不**阻断人工审批 / run 终态。
  P0 默认模式改为 ``always``（每章都评），``NOVELOS_CRITIC_MODE`` / ``ctx['critic_mode']`` 仍覆盖。
  V3.9 4.2 起：已采纳进 ``plan_json.revision_note`` 的建议（``plan_json.suggested_hashes``
  记归一化 hash）在新一轮报告中直接过滤，避免「每轮重写后再生再推荐」的放大。
- ``deep_review`` (AI) —— V1.3 二审 AI 节点。调 ``deep_reviewer`` agent 按仓根
  REVIEW-CHECKLIST.md 三层清单（设定一致性→节拍核销→行为链连续性）核销生成
  **建议性**结构化报告；写入 ctx["deep_review_report"]，并合并进 author_review 的
  pause_payload（键名 ``deep_review_report``），供人工审批界面渲染。
  **仅建议、不拦截**：任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级
  ``deep_review_status='failed'`` 且 ``deep_review_report=None``，**不**阻断人工审批 / run 终态。
  **可选节点**：默认 ``ctx.get("deep_review") is None/False → skipped``（不调 AI），
  ``ctx["deep_review"] is True → always``；与 critic 默认 always 形成差异化——critic 写法层每章评，
  deep_reviewer 事实层按需启用。``ctx['deep_review']`` 由请求体 ``deep_review`` 字段透传。
  verdict=revise **不**触发自动驳回（advisory 原则）。
- ``author_review`` (Human) —— payload=review_report + critic_report + deep_review_report；
  human_input 三态决议：
  ``{"approved": true}`` 通过；``{"approved": false}`` 拒绝（run FAILED）；
  ``{"approved": false, "revise": true, "note": str?}`` 驳回并改稿（run FAILED、
  error='rejected-for-revision'，chapter 保持 DRAFTED，note 落 plan_json.revision_note）。
- ``mark_reviewed`` (State) —— chapters.status DRAFTED→REVIEWED（仅当 DRAFTED 状态可走）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.genre.consumers import (
    count_forbidden_word_hits,
    critic_rubric,
    forbidden_words,
)
from packages.core.genre.service import GenrePackService
from packages.core.genre.target_words import resolve_target_word_count
from packages.core.genre.verifier import verify_chapter
from packages.core.ids import now_iso
from packages.core.model_router.router import capability_for
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.wordcount import (
    classify_prose_length,
    resolve_band_config,
)
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode

DEFAULT_FORBIDDEN_WORDS = ["仿佛", "如同", "本章目标"]
_CRITIC_PROMPT_VERSION = "critic:v1"
# V3.9 批次 4.2：critic 建议去重——归一化（去首尾空白 + 内部空白折叠为单空格）后
# sha1 前 12 位。已采纳进 revision_note 的建议 hash 记在 plan_json.suggested_hashes，
# 下一轮 critic 报告里同 hash 的 issue 直接过滤，避免「每轮重写后再生再推荐」的放大。
_SUGGESTED_HASH_LIMIT = 50
# V3.9 批次 5 / F-3：settings_digest 的 world_rules 注入上限——改造前为「全量无上限」
# （规则多的项目会把 prompt 撑大且无边界）；条数 cap + 单条 statement 字符 cap，
# 与批次 2A 给 builders 加的 *_CAP / *_PER_CHARS 同风格（见 builders_common）。
# 取数排序仍为 world_rule_id ASC（确定性稳定序，cap 前后同一批规则）。
_WORLD_RULES_CAP = 20
_WORLD_RULE_STATEMENT_MAX_CHARS = 500
_WORLD_RULE_TRUNCATED_SUFFIX = "…（已截断）"
_DEEP_REVIEWER_PROMPT_VERSION = "deep_reviewer:v1"
# 单章目标字数（V3.9 批次 5.1；题材包驱动 2026-09-14）：解析单点 =
# packages.core.genre.target_words.resolve_target_word_count ——
# 请求体显式 > plan_json 显式 > 绑定题材包 pacing.chapter_words.target >
# 全仓单源常量 wordcount.DEFAULT_TARGET_WORD_COUNT(3000)。
# 注：context_engine 装配侧另有内部 fallback（builders_common._DEFAULT_TARGET_WORD_COUNT=2200），
# 只在调用方完全未传 target_word_count 时生效，与本兜底不同层、不在本单源范围。

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


def _collect_genre_check(db_path: str, chapter_id: str) -> dict[str, Any] | None:
    """核销层 v2 挂点（题材库 P1b）：返回 ``genre_check`` 段，未绑定题材包 → None。

    - report-only：核销结果只进 ``review_report.genre_check``（结构见
      :meth:`packages.core.genre.verifier.GenreCheckResult.to_dict`），issues 另以
      ``[GENRE-…] <message>`` 文本追加进 ``review_report.warnings``（评审 UI 可读通道）；
    - **不阻断**：issue 恒 ``severity='warning'`` 且 ``rule_id`` 以 ``GENRE-`` 开头，
      不在 quality 阻断白名单（``packages.core.quality.issues.BLOCKING_RULES``）内，
      不产生 error、不触发质量门禁；
    - 无绑定题材包 → ``None``（调用方整段跳过，零行为变化）；
    - verifier 内部已全容错，这里再兜一层异常（核销失败绝不阻断评审）。
    """
    try:
        result = verify_chapter(db_path, chapter_id)
    except Exception as exc:  # noqa: BLE001 —— 核销失败不阻断评审
        _log.warning(
            "chapter_review.genre_check failed: chapter_id=%s err=%s", chapter_id, exc,
        )
        return None
    if not result.bound:
        return None
    return result.to_dict()


def _collect_genre_review_data(db_path: str, project_id: str) -> dict[str, Any]:
    """题材库 P2 读侧投影：**一次**读项目绑定题材包，供本节点两处消费。

    - ``forbidden_words``：``style_constraints.forbidden_words`` → basic_checks 的
      题材禁词确定性扫描（``[GENRE-FORBIDDEN-WORD] <词> ×N``，warning 级）；
    - ``rubric``：``critic_rubric`` → ``genre_rubric`` 段（critic / deep_review 共享，
      经 ``review_inputs`` 下传）；未声明 / 无法投影 → ``None``。

    全部容错：未绑定 / payload 非法 / 读库异常（含迁移未跑的极老库）→ 空投影
    （``{"forbidden_words": [], "rubric": None}``），评审路径零行为变化。
    """

    empty: dict[str, Any] = {"forbidden_words": [], "rubric": None}
    if not project_id:
        return empty
    try:
        binding = GenrePackService(db_path).get_project_binding(project_id)
    except Exception as exc:  # noqa: BLE001 —— 题材包读失败不阻断评审
        _log.warning(
            "chapter_review.genre_pack read failed: project_id=%s err=%s", project_id, exc,
        )
        return empty
    if not binding or not binding.get("bound"):
        return empty
    payload = ((binding.get("pack") or {}).get("payload")) or {}
    return {
        "forbidden_words": forbidden_words(payload),
        "rubric": critic_rubric(payload),
    }


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
    # V3.7：项目级字数带覆盖——先一次性从 chapters 行取 project_id + plan_json，
    # 再读 projects.word_band_json 折叠覆盖。无覆盖时输出与原逐字节一致。
    # V3.9 批次 5.7：plan_json 解析结果同时供 review_inputs（critic / deep_review 共享）复用。
    word_band_overrides: dict | None = None
    chapter_project_id: str | None = None
    plan_dict: dict[str, Any] = {}
    try:
        conn = get_connection(db_path)
        try:
            prow = conn.execute(
                "SELECT project_id, plan_json FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if prow is not None:
            chapter_project_id = prow["project_id"]
            plan_dict = _parse_plan_json(prow["plan_json"])
    except Exception:
        pass
    # 无论 target 是否显式传入，都尝试读项目覆盖（与 target 来源解耦——ctx 传
    # target 也应跟随项目覆盖；这是 V3.7 升级语义的关键）。
    if chapter_project_id:
        try:
            conn2 = get_connection(db_path)
            try:
                try:
                    band_row = conn2.execute(
                        "SELECT word_band_json FROM projects WHERE project_id = ?",
                        (chapter_project_id,),
                    ).fetchone()
                except sqlite3.OperationalError:
                    band_row = None
            finally:
                conn2.close()
        except Exception:  # noqa: BLE001 —— 防御性：读库失败视为无覆盖
            band_row = None
        if band_row is not None and band_row["word_band_json"]:
            try:
                parsed = json.loads(band_row["word_band_json"])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                word_band_overrides = parsed
    # 目标字数：请求体显式传入 > plan_json.expected_word_count > 绑定题材包
    # pacing.chapter_words.target > 默认（全仓单源 3000）。解析单点：
    # packages.core.genre.target_words.resolve_target_word_count。
    target = resolve_target_word_count(
        db_path,
        chapter_id,
        plan_dict,
        explicit=ctx.get("target_word_count"),
        project_id=chapter_project_id,
    )

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
    # V3.7 字数带硬约束；V3.9 批次 5.2：warning / error 两级阈值**全部由生效字数带派生**
    # ——``resolve_band_config`` 是唯一阈值源，项目覆盖 band 后两级判定同步跟随，
    # 不再与写死的 ±15% / ±30% 互相矛盾：
    # - warning：出带（classify 的 status）。带边缘本身就是阈值——改造前写死 |dev|<=15%，
    #   项目把带收窄到 (1800,2200) 后 1720/2000=-14% 仍判「在带内」，与 word_band 自相矛盾；
    # - error：带外偏离超过**该侧带边缘到 target 的距离**（默认 0.85/1.15 ⇒ 偏离 target 超
    #   ±30%，与改造前逐值一致；带越宽则 error 阈值同步放宽）。
    # classify 既出 visible_chars（word_count）又出 band / status / deviation_pct，避免重复调用。
    classify = classify_prose_length(
        prose, target, **resolve_band_config(word_band_overrides),
    )
    word_count = classify["visible_chars"]
    deviation_pct = classify["deviation_pct"]
    band_low, band_high = classify["band_low"], classify["band_high"]
    within_range = classify["status"] == "in_band"
    # 带外偏离幅度（带内恒 0）与该侧 error 阈值（band 边缘到 target 的距离）
    edge_deviation = max(0, band_low - word_count, word_count - band_high)
    if word_count < band_low:
        edge_distance = target - band_low
    elif word_count > band_high:
        edge_distance = band_high - target
    else:
        edge_distance = 0
    over_band = edge_deviation > edge_distance

    # V3.8：去 AI 味确定性检测（纯规则、不调用 LLM）
    ai_hits = scan_ai_patterns(prose)
    # 向后兼容：保留 forbidden_word_hits 字段（由原 DEFAULT_FORBIDDEN_WORDS 扩展而来）
    forbidden_hits: list[str] = []
    for hit in ai_hits:
        if hit.get("rule_id") == "AI-FORBIDDEN-WORD":
            forbidden_hits.extend(hit.get("words", []))
    forbidden_hits = sorted(set(forbidden_hits))

    # 题材库 P2：题材包读侧投影（**一次**读绑定，两处消费——禁词扫描在本节点，
    # genre_rubric 经 review_inputs 下传 critic / deep_review）。未绑定 / 无声明 →
    # 空投影，与题材库前行为逐字节一致。
    genre_data = (
        _collect_genre_review_data(db_path, chapter_project_id)
        if chapter_project_id
        else {"forbidden_words": [], "rubric": None}
    )

    warnings: list[str] = []
    errors: list[dict[str, Any]] = []
    if not within_range:
        warnings.append(
            f"[W-LEN-DEVIATION] visible={word_count} target={target} "
            f"deviation={deviation_pct:+.1f}% (band {band_low}~{band_high})"
        )
    if over_band:
        # 带外偏离超过该侧带边缘到 target 的距离：error 级条目，供 author_review /
        # 前端 reviewer UI 消费（与 signing_check/quality_gate 的阻断语义对齐——
        # errors 不阻断 run，但在 review UI 上以「严重」色渲染，作者可见）。
        errors.append({
            "rule_id": "W-LEN-DEVIATION",
            "severity": "error",
            "message": (
                f"[W-LEN-DEVIATION] visible={word_count} target={target} "
                f"deviation={deviation_pct:+.1f}% beyond band {band_low}~{band_high} "
                f"by {edge_deviation} 字 > 带边缘距 target {edge_distance} 字"
            ),
            "word_band": {"low": band_low, "high": band_high},
            "visible_chars": word_count,
            "target": target,
            "deviation_pct": deviation_pct,
        })
    if forbidden_hits:
        warnings.append(f"禁用词命中：{','.join(forbidden_hits)}")
    # 题材库 P2：题材禁词确定性扫描（子串计数，与 quality.ai_flavor 的词典机制同款口径）。
    # 命中 → warning 级文本行（不进 errors、不阻断）；未命中 / 未声明禁用词 → 零输出。
    for word, count in count_forbidden_word_hits(prose, genre_data.get("forbidden_words") or ()):
        warnings.append(f"[GENRE-FORBIDDEN-WORD] {word} ×{count}")
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
    # 题材库 P1b：核销层 v2（配比偏差 + 节奏红线）挂点——report-only，不阻断。
    # 未绑定题材包 → 整段跳过（report 无 genre_check 键，零行为变化）。
    genre_check = _collect_genre_check(db_path, chapter_id)
    if genre_check is not None:
        report["genre_check"] = genre_check
        # issues 的 informational 通道：结构化条目已在 genre_check.issues
        # （与 quality Issue dump 同形：rule_id / severity / category / message /
        # location / suggestion / evidence_refs），此处再补文本行便于评审 UI 直读。
        for issue in genre_check.get("issues") or []:
            warnings.append(f"[{issue.get('rule_id')}] {issue.get('message')}")
    # V3.9 批次 5.7：critic / deep_review 共享取数——本节点（必跑首节点）一次取齐，
    # 两个 AI 节点直接消费 ctx["review_inputs"]，不再各自把同一批 SQL 重跑一遍。
    if chapter_project_id is None:
        # 章节行缺失（draft 孤儿）：与改造前 critic 的取数口径一致 → 标记不可用，
        # critic / deep_review 按既有「inputs 失败」路径降级，不误用空项目上下文。
        review_inputs: dict[str, Any] | None = None
        review_inputs_error: str | None = f"chapter {chapter_id!r} not found"
    else:
        review_inputs = _collect_review_inputs(
            db_path,
            chapter_project_id,
            draft_text=prose,
            plan=plan_dict,
            genre_rubric=genre_data.get("rubric"),
        )
        review_inputs_error = None
    return {
        "review_report": report,
        "review_inputs": review_inputs,
        "review_inputs_error": review_inputs_error,
    }


def _parse_plan_json(raw: Any) -> dict[str, Any]:
    """chapters.plan_json（JSON 字符串 / 已是 dict）→ dict；非法 / 非 dict → ``{}``。

    与 :func:`_safe_load_json`（chapter_plan）同口径，不抛错（fail-soft）。
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _collect_review_inputs(
    db_path: str,
    project_id: str,
    *,
    draft_text: str,
    plan: dict[str, Any],
    genre_rubric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次性取齐 critic / deep_review 共享输入（V3.9 批次 5.7）。

    在 ``basic_checks``（必跑首节点）调用，结果存 ``ctx["review_inputs"]``；两个
    AI 节点只消费不重查——改造前 critic 与 deep_review 各自把同一批 SQL 跑一遍
    （draft / chapters / world_rules / characters / hooks，同一 run 内重复取数）。

    返回：
      - ``draft_text``：审校正文（与 review_report 同一次读取，版本一致）
      - ``project_id``：章节所属项目
      - ``plan_summary``：``{"chapter_goal", "key_beats"}``（两个 AI 节点 payload）
      - ``suggested_hashes``：已采纳建议 hash（critic 去重专用，deep_review 不用）
      - ``open_hooks``：项目开放伏笔（critic payload）
      - ``settings_digest``：设定摘要（world_rules + 角色档案，两节点共用）
      - ``genre_rubric``：题材审查要点（题材库 P2；由 basic_checks 单次读题材包投影，
        未被绑定 / 未声明 ``critic_rubric`` → ``None``）

    ``open_hooks`` / ``settings_digest`` 取数失败一律降级为空列表（不抛错）——
    与改造前两个 AI 节点各自的 try/except 语义一致：设定上下文缺失不阻断评审。
    """
    if project_id:
        try:
            open_hooks = _collect_open_hooks(db_path, project_id)
        except Exception as exc:  # noqa: BLE001 —— 取数失败降级为空，不阻断 critic
            _log.warning(
                "chapter_review.open_hooks collection failed: project_id=%s err=%s",
                project_id, exc,
            )
            open_hooks = []
    else:
        open_hooks = []
    return {
        "draft_text": draft_text,
        "project_id": project_id,
        "plan_summary": {
            "chapter_goal": plan.get("chapter_goal"),
            "key_beats": plan.get("key_beats") or [],
        },
        "suggested_hashes": _read_suggested_hashes(plan),
        "open_hooks": open_hooks,
        "settings_digest": _collect_settings_digest(db_path, project_id),
        "genre_rubric": genre_rubric,
    }


def _require_review_inputs(ctx: dict[str, Any]) -> dict[str, Any]:
    """取 ``basic_checks`` 预取的共享输入（V3.9 批次 5.7）。

    生产链路 basic_checks 为必跑首节点，键恒在；直调节点（单测 / 复用）或章节行
    缺失时不可用 → 抛 ``ValueError``，由调用节点按既有「inputs 收集失败」路径降级。
    **不回退重查**：重查会抵消共享收益，且与 basic_checks 已审的草稿版本可能不一致。
    """
    inputs = ctx.get("review_inputs")
    if isinstance(inputs, dict):
        return inputs
    raise ValueError(
        str(ctx.get("review_inputs_error") or "review_inputs missing（basic_checks 未运行）")
    )


def _normalize_suggestion(text: Any) -> str:
    """建议文本归一化：去首尾空白 + 内部连续空白折叠为单空格。"""
    return re.sub(r"\s+", " ", str(text or "").strip())


def _suggestion_hash(text: Any) -> str:
    """建议文本的归一化 hash（sha1 前 12 位十六进制）。

    写入（``_mark_reviewed_node`` 落 ``plan_json.suggested_hashes``）与过滤
    （``_critic_review_node`` 丢弃重复 issue）必须走同一函数，保证口径一致。
    """
    normalized = _normalize_suggestion(text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _merge_suggested_hashes(existing: Any, note: str) -> list[str]:
    """把 note 的逐行 hash 并入已有列表（去重、保持出现顺序、尾部截断到上限）。

    逐行口径与前端「按建议修改」的组装方式对齐：ApprovalCard 把勾选的 critic
    suggestion 以 ``\\n`` 连接后作为 note 下发，故每行即一条被采纳的建议。
    自由文本行也会被 hash——同文本再次被 critic 提出时同样视为「已建议过」。
    """
    hashes: list[str] = [
        h for h in (existing if isinstance(existing, list) else []) if isinstance(h, str) and h
    ]
    for line in str(note or "").splitlines():
        if not _normalize_suggestion(line):
            continue
        h = _suggestion_hash(line)
        if h not in hashes:
            hashes.append(h)
    return hashes[-_SUGGESTED_HASH_LIMIT:]


def _read_suggested_hashes(plan: dict[str, Any]) -> list[str]:
    raw = plan.get("suggested_hashes")
    return [h for h in (raw if isinstance(raw, list) else []) if isinstance(h, str) and h]


def _clip_world_rule_statement(statement: Any) -> str:
    """world_rule.statement 的单条字符 cap（V3.9 批次 5 / F-3）。

    非字符串 → ``""``；超 ``_WORLD_RULE_STATEMENT_MAX_CHARS`` → 截断并追加标记，
    保证单条规则注入 prompt 的长度有界。
    """
    if not isinstance(statement, str):
        return ""
    if len(statement) <= _WORLD_RULE_STATEMENT_MAX_CHARS:
        return statement
    return statement[:_WORLD_RULE_STATEMENT_MAX_CHARS] + _WORLD_RULE_TRUNCATED_SUFFIX


def _collect_settings_digest(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """V3.9：组装 critic / deep_review 的设定上下文摘要。

    - world_rules 前 ``_WORLD_RULES_CAP``(20) 条的 name + statement（顺序：
      world_rule_id ASC，确定性稳定序）；单条 statement 截断到
      ``_WORLD_RULE_STATEMENT_MAX_CHARS``(500) 字符 + 截断标记（F-3：改造前是全量注入）。
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
                "WHERE project_id = ? ORDER BY world_rule_id ASC LIMIT ?",
                (project_id, _WORLD_RULES_CAP),
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
            "one_line": _clip_world_rule_statement(r["statement"]),
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
    1. 消费 ``basic_checks`` 预取的共享输入（``ctx["review_inputs"]``：draft_text /
       plan_summary / open_hooks / settings_digest / suggested_hashes / genre_rubric）
       ——V3.9 批次 5.7 起不再自取（同 run 内 deep_review 也要用同一批数据，原先各查一遍）；
       其中 ``genre_rubric``（题材库 P2）为可选输入，非 None 时进 payload。
    2. 调 ``run_agent(..., agent_name='critic', expected='critic', mock_script=...)``。
       runner 内部走 parse_json → validate_contract("critic") → 写 ai_call_logs；本节点只
       关心最终输出 payload。
    3. 任何异常（PromptNotFound / Provider / 契约校验失败 / 输出字段缺失 / 共享输入
       不可用）→ 降级 ``critic_status='failed'`` + ``critic_report=None``，**不**抛错。
    4. ``critic_status='ok'`` 时 ``critic_report`` 写入 ctx，供 author_review 合并进 pause_payload。

    V3 P0-1：critic 采样模式开关（off / sample / always）。跳过时返回 ``critic_status='skipped'``
    + ``critic_skipped=True`` + ``critic_mode=<mode>``，工作流状态机不受影响（author_review
    Human 节点仍按 PauseRequested 走，pause_payload 中 ``critic_status='skipped'`` 不合并 critic_report）。
    V3.9 批次 5.7：章节号只在 ``sample`` 模式需要，``always`` / ``off`` 下不再白查库。

    返回 ``{"critic_status": "ok|failed|skipped", "critic_report": dict|None,
    "critic_error": str|None, "critic_skipped": bool, "critic_mode": str}``。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("critic")

    # V3 P0-1：解析 critic_mode 并按 chapter_number 判定是否跳过。
    # V3.9 批次 5.7：_fetch_chapter_number 只在 sample 模式调用（always 下必然调用
    # critic、off 下必然跳过，两种模式下章节号都无用）。
    mode = _resolve_critic_mode(ctx)
    chapter_number = _fetch_chapter_number(db_path, chapter_id) if mode == "sample" else None
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
        shared = _require_review_inputs(ctx)
    except ValueError as exc:  # 共享输入不可用 → 与改造前「取数失败」同一降级路径
        _log.warning(
            "chapter_review.critic inputs unavailable: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "critic_status": "failed",
            "critic_report": None,
            "critic_error": f"inputs: {exc}",
            "critic_skipped": False,
            "critic_mode": mode,
        }
    draft_text = shared.get("draft_text") or ""
    plan_summary = shared.get("plan_summary") or {}
    open_hooks = shared.get("open_hooks") or []
    settings_digest = shared.get("settings_digest") or []
    suggested_hashes = shared.get("suggested_hashes") or []
    # 题材库 P2：题材审查要点（basic_checks 单次读题材包投影，≤1000 字符、超限带
    # __genre_rubric_truncated__ 标记）；未绑定 / 未声明 → 不注入该键。
    genre_rubric = shared.get("genre_rubric")

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
            "target_word_count": resolve_target_word_count(
                db_path, chapter_id, None, explicit=ctx.get("target_word_count"),
            ),
            "expected_role": ctx.get("expected_role"),
            "chapter_id": chapter_id,
        },
        "draft_text": draft_text,
        "plan_summary": plan_summary,
        "open_hooks": open_hooks,
        "settings_digest": settings_digest,
        "deterministic_hints": deterministic_hints,
    }
    # 题材库 P2：题材审查要点（可选输入；缺席 = 未绑定题材包 / 未声明 critic_rubric）。
    if genre_rubric is not None:
        payload["genre_rubric"] = genre_rubric

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
        # V3.9 批次 4.2：丢弃「已采纳进 revision_note」的重复建议（同内容 hash）。
        # 只过滤 critic 报告呈现面，不改其它字段；过滤条数写进报告供观测。
        if suggested_hashes and isinstance(out.get("issues"), list):
            known = set(suggested_hashes)
            kept: list[dict[str, Any]] = []
            dropped = 0
            for issue in out["issues"]:
                if not isinstance(issue, dict):
                    continue
                if _suggestion_hash(issue.get("suggestion")) in known:
                    dropped += 1
                    continue
                kept.append(issue)
            if dropped:
                out["issues"] = kept
                out["deduped_suggestion_count"] = dropped
                _log.info(
                    "chapter_review.critic dropped %d already-suggested issue(s): "
                    "chapter_id=%s", dropped, chapter_id,
                )
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


def _deep_review_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """V1.3 二审 AI 节点。

    行为：
    1. ``ctx.get("deep_review")`` falsy → 返回 ``deep_review_status='skipped'``，
       **不调 AI**、不写 deep_review_report（与 critic 默认 always 形成差异化）。
    2. truthy → 调 ``run_agent(..., agent_name='deep_reviewer', expected='deep_reviewer', mock_script=...)``
       跑三层清单核销（设定一致性 → 节拍核销 → 行为链连续性）。
    3. 任何异常（PromptNotFound / Provider / 契约校验失败 / 输出字段缺失 / 共享输入
       不可用）→ 降级 ``deep_review_status='failed'`` + ``deep_review_report=None``，
       **不**抛错。
    4. ``deep_review_status='ok'`` 时 ``deep_review_report`` 写入 ctx，供 author_review
       合并进 pause_payload。

    输入载荷与 critic 节点保持最大复用（V3.9 批次 5.7 起改为**直接消费** basic_checks
    预算好的 ``ctx["review_inputs"]``——draft_text / plan_summary / settings_digest 与
    critic 完全同一份数据，同 run 内不再各查一遍相同 SQL）：
    - ``draft_text`` / ``plan_summary`` / ``settings_digest``：取自 ``review_inputs``。
    - ``prev_chapter_summaries``：当前项目暂未启用 summarizer 的「按章节一句话摘要」字段
      透传——本节点传空数组；待 summarizer 落库后由上层补齐并经 run_agent 注入。
      此处留 TODO 注释待后续 summarizer 章节摘要接入。
    - ``critic_report``：若 critic 节点已跑出 ``critic_status='ok'``，把 ``ctx["critic_report"]``
      透传给 deep_reviewer 作为盲区对照基准（§3.4 边界）。
    - ``genre_rubric``：题材审查要点（题材库 P2）——与 critic 同一份投影，非 None 时进
      payload（deep_review 同样可得）。

    返回 ``{"deep_review_status": "ok|failed|skipped", "deep_review_report": dict|None,
    "deep_review_error": str|None, "deep_review_skipped": bool}``。
    """
    # 可选节点开关：默认 None / False → 跳过（不调 AI）；True → 调 AI
    if not ctx.get("deep_review"):
        _log.info(
            "chapter_review.deep_review skipped: chapter_id=%s (deep_review=%r)",
            ctx.get("chapter_id"), ctx.get("deep_review"),
        )
        return {
            "deep_review_status": "skipped",
            "deep_review_report": None,
            "deep_review_error": None,
            "deep_review_skipped": True,
        }

    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("deep_reviewer")

    try:
        shared = _require_review_inputs(ctx)
    except ValueError as exc:  # 共享输入不可用 → 与改造前「取数失败」同一降级路径
        _log.warning(
            "chapter_review.deep_review inputs unavailable: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "deep_review_status": "failed",
            "deep_review_report": None,
            "deep_review_error": f"inputs: {exc}",
            "deep_review_skipped": False,
        }
    draft_text = shared.get("draft_text") or ""
    plan_summary = shared.get("plan_summary") or {}
    settings_digest = shared.get("settings_digest") or []
    # 题材库 P2：deep_review 与 critic 同得 genre_rubric（同一份 review_inputs）。
    genre_rubric = shared.get("genre_rubric")

    payload = {
        "agent": "deep_reviewer",
        "prompt_version": _DEEP_REVIEWER_PROMPT_VERSION,
        "chapter": {
            "title": None,
            "target_word_count": resolve_target_word_count(
                db_path, chapter_id, None, explicit=ctx.get("target_word_count"),
            ),
            "expected_role": ctx.get("expected_role"),
            "chapter_id": chapter_id,
        },
        "draft_text": draft_text,
        "plan_summary": plan_summary,
        "settings_digest": settings_digest,
        # TODO：summarizer 节点产出「按章节一句话摘要」表后，由上层 pipeline 注入；
        # 当前项目未启用 summarizer 的章节摘要字段，先传空数组。
        "prev_chapter_summaries": [],
        # critic 报告作为盲区对照基准（§3.4 边界）；critic 未跑出 ok 时传 null
        "critic_report": (
            ctx.get("critic_report")
            if ctx.get("critic_status") == "ok"
            else None
        ),
    }
    # 题材库 P2：题材审查要点（与 critic 同一份；缺席 = 未绑定 / 未声明 critic_rubric）。
    if genre_rubric is not None:
        payload["genre_rubric"] = genre_rubric

    try:
        out = run_agent(
            db_path,
            "deep_reviewer",
            payload,
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="deep_reviewer",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("deep_reviewer")
            ),
        )
        if not isinstance(out, dict):
            raise ValueError(f"deep_reviewer output not dict: {type(out).__name__}")
        # 软校验：非缺失型 quote 必须能溯源到 draft_text（trim 后 substring）；缺失型（layer=beat
        # 且 suggestion 以 [beat N 缺失] 前缀）允许 quote=""。不溯源的 issue 直接丢弃。
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
                # 缺失型允许空串：layer=beat 且 suggestion 以 "[beat N" 前缀且包含 "缺失"
                layer = issue.get("layer")
                suggestion = issue.get("suggestion")
                is_missing_beat = (
                    layer == "beat"
                    and isinstance(suggestion, str)
                    and suggestion.lstrip().startswith("[beat ")
                    and "缺失" in suggestion
                )
                if not quote_trim:
                    if is_missing_beat:
                        cleaned_issues.append(issue)
                    else:
                        _log.warning(
                            "chapter_review.deep_review dropped empty quote (non-missing): "
                            "chapter_id=%s layer=%s",
                            chapter_id, layer,
                        )
                    continue
                if quote_trim not in draft_text:
                    _log.warning(
                        "chapter_review.deep_review dropped untraceable issue: "
                        "chapter_id=%s quote=%r",
                        chapter_id, quote[:30],
                    )
                    continue
                cleaned_issues.append(issue)
            out["issues"] = cleaned_issues
        elif issues is not None:
            out["issues"] = []
        return {
            "deep_review_status": "ok",
            "deep_review_report": out,
            "deep_review_error": None,
            "deep_review_skipped": False,
        }
    except Exception as exc:  # noqa: BLE001 —— 任何 LLM / 契约 / parse 失败均降级
        _log.warning(
            "chapter_review.deep_review degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "deep_review_status": "failed",
            "deep_review_report": None,
            "deep_review_error": str(exc),
            "deep_review_skipped": False,
        }


def _author_review_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：抛 PauseRequested 等 author 决议。
    human_input={"approved": true} → 通过；其他（"approved":false 或缺 approved）→ 拒绝。

    本 fn 总是抛 PauseRequested；resume 时会被重跑读 ctx["human_input"]["approved"]。
    """
    report = ctx.get("review_report") or {}
    critic_report = ctx.get("critic_report") if ctx.get("critic_status") == "ok" else None
    critic_status = ctx.get("critic_status") or "skipped"
    # V1.3：deep_review_report 键始终存在于 pause_payload（未开启 / 失败时为 None）；
    # 仅当 deep_review_status='ok' 时并入实际报告值——前端按 critic_status /
    # deep_review_status 分别渲染（与 critic_report 同口径：键恒在、值按状态切换）。
    deep_review_report = (
        ctx.get("deep_review_report")
        if ctx.get("deep_review_status") == "ok"
        else None
    )
    deep_review_status = ctx.get("deep_review_status") or "skipped"
    # V3 P0-1：把 critic_mode/skipped 透传到 pause_payload，便于前端观测
    payload = {
        "stage": "chapter-review",
        "message": "请审查章节草稿并批准或驳回",
        "review_report": report,
        "critic_status": critic_status,
        "critic_report": critic_report,
        "critic_mode": ctx.get("critic_mode"),
        "critic_skipped": bool(ctx.get("critic_skipped")),
        "deep_review_status": deep_review_status,
        "deep_review_report": deep_review_report,
        "deep_review_skipped": bool(ctx.get("deep_review_skipped")),
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
                    # V3.9 批次 4.2：记录本次采纳的建议 hash（逐行归一化 hash），
                    # 供下一轮 critic 报告过滤同内容建议；空行为跳过，列表有上限。
                    plan["suggested_hashes"] = _merge_suggested_hashes(
                        plan.get("suggested_hashes"), note
                    )
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
        WorkflowNode(
            "deep_review", "AI", _deep_review_node, agent_name="deep_reviewer"
        ),
        WorkflowNode("author_review", "Human", _author_review_node),
        WorkflowNode("mark_reviewed", "State", _mark_reviewed_node),
    ]


WORKFLOW = {
    "name": "chapter-review",
    "version": "v1",
    "description": (
        "basic_checks → critic_review(AI, advisory, default always) → "
        "deep_review(AI, advisory, opt-in via deep_review=true) → "
        "author_review(Human) → mark_reviewed(DRAFTED→REVIEWED)；"
        "revise 驳回改稿闭环；critic / deep_reviewer 仅做建议、不拦截"
    ),
    "nodes": _build_nodes(),
    # V1.0 checkpoint 写放大优化（Sprint V1.5）：author_review 是 Human 节点（可 PAUSE）；
    # PAUSE 时 checkpoint 落盘整张 ctx，下游 mark_reviewed 节点**不读** review_report / critic_*
    # 字段（mark_reviewed 仅依赖 human_input + _author_approved + _revision_note）。
    # 这两个字段较大（critic_report 含 issues/strengths/revision_guidance；review_report 含
    # 完整字数/禁用词扫描结果），落盘只增体积不影响 resume——可安全 exclude。
    # 注：author_review 的 __pause_payload__ 单独存在 ctx['author_review'] 内，不受 exclude 影响，
    # 前端仍可读到 review_report / critic_report（reviewer UI 必需）。
    # V1.3 deep_review_report 加入 exclude：与 critic_report 同口径，不影响 resume（mark_reviewed
    # 不读 deep_review_report）。
    # V3.9 批次 5.7：review_inputs（basic_checks 预取的共享输入：draft_text / plan_summary /
    # settings_digest / open_hooks / suggested_hashes）**不**进 exclude——critic 与 deep_review
    # 均消费它，resume 重建 ctx 后键仍可达（引擎 resume 从 PAUSED 节点后继续，不回跑 AI 节点）；
    # 体积以 draft_text 为主（≈正文 UTF-8 字节数），远低于引擎 512KB checkpoint 软上限。
    #
    # V3.9 全量检修 M2：节点镜像键补齐（引擎 ``ctx[node_id] = output`` 与
    # workflow_run_nodes.output_json 逐字节重复；不排除则上面的数据键经镜像原样落盘）。
    # 例外：``author_review`` 是 Human 节点，其镜像键承载 ``__pause_payload__``
    # （review_report / critic_report 供前端 reviewer UI 渲染，apps/web/src/utils/pausePayload.ts
    # 从 checkpoint_json[node_id] 取）——保留，不得排除。
    "checkpoint_exclude": [
        "review_report",
        "critic_report",
        "critic_status",
        "critic_error",
        "critic_skipped",
        "deep_review_report",
        "deep_review_status",
        "deep_review_error",
        "deep_review_skipped",
        # ---- 节点镜像键（author_review 除外：pause payload 消费契约）----
        "basic_checks",     # 镜像内含 review_report / review_inputs（draft_text 大）
        "critic_review",    # 镜像内含 critic_report
        "deep_review",      # 镜像内含 deep_review_report
        "mark_reviewed",
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_review.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
