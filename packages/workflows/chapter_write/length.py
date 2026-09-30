"""chapter_write · 字数闭环段（2026-09-27 B-1 拆分自 pipeline.py）。

含字数带解析 / 目标字数解析 / length_check 节点 + condense 的两枚常量。
condense 节点本体 _condense_node 因 monkeypatch 契约留在门面。
同包内搬移，逻辑逐字不变。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from packages.core.db import get_connection
from packages.core.genre.target_words import resolve_target_word_count
from packages.core.quality.wordcount import classify_prose_length, resolve_band_config

_log = logging.getLogger(__name__)

# condense 压缩预算门（_condense_node）：payload（待压缩正文，visible_chars 口径）
# 必须**严格超过**「带上限 × 本系数」才调 LLM 压缩。取值 1.0 = 不放大、不收紧
# （condense 的唯一用途是把超 band_high 的正文压回带内，低于该线既无余量可压，
# 内循环还会把欠字正文压得更短）。2026-09-27 B-1 拆分时随 _condense_node 一同迁出。
_CONDENSE_PAYLOAD_TRIGGER_RATIO = 1.0
# ---------------------------------------------------------------------------
# 字数闭环：length_check + condense（V3.7+ P1 字数闭环节点）
# ---------------------------------------------------------------------------

# condense 最大重试轮次（含 condense 节点的执行次数）；超过则放行并标注偏差。
_MAX_CONDENSE_ROUNDS = 2


def _resolve_chapter_word_band(
    db_path: str, chapter_id: str
) -> tuple[int, dict[str, Any]]:
    """读 chapter → project → projects.word_band_json，覆盖 → resolve_band_config。

    返回 ``(chapter_project_id_or_empty, band_cfg)``。band_cfg 始终是合法可
    ``**word_band(...)`` 展开的 dict（无覆盖 = 模块默认 0.85/1.15/1200）。

    与 chapter_review/pipeline.py _basic_checks_node L111-156 同口径（共用
    ``resolve_band_config``），保证 review 侧与 write 侧字数带读源唯一。

    防御：``resolve_band_config`` 在 ratios/floor 非法值时会抛 ``ValueError``
    （如 low_ratio > high_ratio 倒挂、floor=负数等）。此处捕获并落默认带 + 告警，
    与项目内「非法 JSON → 视为无覆盖」的 fail-soft 风格一致。chapter_review 侧
    ``_basic_checks_node`` 是独立函数、未复用本 helper，行为差异不在本任务范围
    （已记 Known Issue / 后续可一并覆盖）。
    """
    chapter_project_id = ""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["project_id"]:
            chapter_project_id = row["project_id"]
    except Exception:  # noqa: BLE001
        return chapter_project_id, resolve_band_config(None)

    if not chapter_project_id:
        return chapter_project_id, resolve_band_config(None)

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
    except Exception:  # noqa: BLE001
        band_row = None

    if band_row is None or not band_row["word_band_json"]:
        return chapter_project_id, resolve_band_config(None)
    try:
        parsed = json.loads(band_row["word_band_json"])
    except (TypeError, ValueError):
        return chapter_project_id, resolve_band_config(None)
    if not isinstance(parsed, dict):
        return chapter_project_id, resolve_band_config(None)
    try:
        return chapter_project_id, resolve_band_config(parsed)
    except ValueError as exc:
        # 非法值（low_ratio > high_ratio 倒挂 / floor 负数等）→ 落默认 + 告警。
        _log.warning(
            "chapter_write._resolve_chapter_word_band: invalid word_band_json "
            "project_id=%s err=%s; fall back to default band",
            chapter_project_id, exc,
        )
        return chapter_project_id, resolve_band_config(None)


def _resolve_target_word_count(ctx: dict[str, Any], plan: dict[str, Any]) -> int:
    """解析本章目标字数（与 _writer_node / review 两侧同口径）。

    优先级：ctx 显式传入 → plan_json.expected_word_count → plan_json.target_word_count
    → 绑定题材包的 ``pacing.chapter_words.target`` → 兜底 ``DEFAULT_TARGET_WORD_COUNT``
    （3000，全仓单源）。后两级由 :func:`packages.core.genre.target_words.resolve_target_word_count`
    单点实现（未绑定题材包时与改造前逐字节一致）。
    """
    return resolve_target_word_count(
        ctx.get("db_path"),
        ctx.get("chapter_id"),
        plan,
        explicit=ctx.get("target_word_count"),
    )


def _length_check_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：权威口径实测 polished_prose 字数，断是否在项目字数带内。

    输入优先级：``polished_prose``（polisher/fallback 后）→ ``writer_output.prose``。
    不调 LLM，pure Transform；纯依赖 ``visible_chars`` / ``classify_prose_length``。

    输出字段（ctx → checkpoint_json / run 节点产出均可见）：
    - ``length_report``：classify_prose_length 完整 dict（visible_chars/target/
      band_low/band_high/deviation_pct/status）外加 ``within_band`` bool
      （=status == 'in_band'）。
    - ``target_word_count``：本节点选用的目标字数，供 condense / save_draft 复用。
    - ``word_band_cfg``：本节点选用的 band 配置 dict，供 condense prompt 注入。
    - ``length_check_passed``：bool，True ⇒ condense 跳过，False ⇒ condense 触发。

    不抛错：DB 异常 / 空 prose 走保守路径（status='under', visible=0，
    within_band=False），下游 condense 若无可压缩文本会走 0 长度不动。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    target = _resolve_target_word_count(ctx, plan)
    _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)

    writer_output = ctx.get("writer_output") or {}
    raw_prose = ctx.get("polished_prose")
    if not isinstance(raw_prose, str) or not raw_prose.strip():
        raw_prose = writer_output.get("prose") or ""
    if not isinstance(raw_prose, str):
        raw_prose = ""

    report = classify_prose_length(
        raw_prose, target, **band_cfg,
    )
    report["within_band"] = report["status"] == "in_band"
    # condense_rounds 已跑几轮（ctx 累积；首轮 length_check 默认 0）。
    rounds_used = int(ctx.get("condense_rounds") or 0)
    return {
        "length_report": report,
        "target_word_count": target,
        "word_band_cfg": band_cfg,
        "length_check_passed": report["within_band"],
        # 主会话约定：唯一计数键 ctx['condense_rounds']，length_check 仅 echo 不改写。
        "condense_rounds": rounds_used,
    }
