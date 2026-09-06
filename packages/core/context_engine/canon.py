"""ReferenceCanon 注入——按 consumer（director/scene_planner/writer）
裁剪注入字段（拆分自 builders.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# 顶层 director_input 注入键 + 截断上限；缺字段容错跳过。
_REFERENCE_CANON_SPINE_CAP = 20
_REFERENCE_CANON_PAYOFF_CAP = 30
# Scene Planner 专用：emotion_curve 单章数组，参照书可能上千章；截断 ≤50。
_REFERENCE_CANON_EMOTION_CAP = 50
# Writer 专用：style_params 整体 JSON 序列化后总字符上限；超出逐项删减并标记。
_REFERENCE_CANON_STYLE_MAX_CHARS = 1500
# Director 专用：protagonist.personality_tags / foil_techniques 截断上限。
_REFERENCE_CANON_PROTAGONIST_TAGS_CAP = 6
_REFERENCE_CANON_PROTAGONIST_FOILS_CAP = 4

# consumer 取值（与 agent-contracts + docs/reference-canon/reference-canon-v0.md §4.1 对齐）
_REFERENCE_CANON_CONSUMER_DIRECTOR = "director"
_REFERENCE_CANON_CONSUMER_PLANNER = "scene_planner"
_REFERENCE_CANON_CONSUMER_WRITER = "writer"


def _reference_canon_excerpt(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    consumer: str = _REFERENCE_CANON_CONSUMER_DIRECTOR,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """查该项目最新 active reference_canon（按 created_at DESC）。

    返回 (inject, audit_payload) 元组：
    - inject：注入到对应 agent payload 的 reference_canon 键（None 表示无 canon）。
    - audit_payload：溯源审计 dict（含 canon_id + consumed_fields），落到 ctx 顶层
      ``_reference_canon_consumed``，随 ctx 进入 workflow run 的 checkpoint_json。

    参数：
    - ``consumer``：消费方标识，按消费字段集裁剪：
        * ``"director"``（默认）：logline / spine(≤20) / payoff_list(≤30) / rhythm /
          protagonist(personality_tags ≤6, foil_techniques ≤4)；
        * ``"scene_planner"``：emotion_curve(≤50) / payoff_list(≤30，与 director 同口径)；
        * ``"writer"``：style_params（整体 JSON ≤1500 字符，超出逐项删减）。

    设计：
    - MVP 单参照系（多书加权合并策略见 docs/reference-canon/reference-canon-v0.md §4.1 OV-2，defer）。
    - canon_json 解析失败 → 容错返回 (None, None)，不抛错。
    - 缺字段 → 跳过该字段，consumed_fields 不计。
    - director 行为与 Sprint 11 下半实现逐字段一致（key 名 / 顺序 / 截断上限不变）。
    """
    row = conn.execute(
        """
        SELECT canon_id, canon_json
        FROM reference_canons
        WHERE project_id = ? AND status = 'active'
        ORDER BY created_at DESC, canon_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None, None

    canon_id = row["canon_id"]
    raw_canon_json = row["canon_json"] or "{}"
    try:
        cj = json.loads(raw_canon_json)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(cj, dict):
        return None, None

    consumed: list[str] = []
    inject: dict[str, Any] = {"canon_id": canon_id}

    if consumer == _REFERENCE_CANON_CONSUMER_DIRECTOR:
        logline = cj.get("logline")
        if isinstance(logline, str) and logline.strip():
            inject["logline"] = logline
            consumed.append("logline")

        spine = cj.get("spine")
        if isinstance(spine, list):
            inject["spine"] = spine[:_REFERENCE_CANON_SPINE_CAP]
            consumed.append("spine")

        payoff_list = cj.get("payoff_list")
        if isinstance(payoff_list, list):
            inject["payoff_list"] = payoff_list[:_REFERENCE_CANON_PAYOFF_CAP]
            consumed.append("payoff_list")

        rhythm = cj.get("rhythm")
        if isinstance(rhythm, dict):
            inject["rhythm"] = rhythm
            consumed.append("rhythm")

        # 主角人设（v0.1.2 新增，顶层 optional）：identity / core_drive 全量注入，
        # personality_tags 截前 6，foil_techniques 截前 4；任一子字段非法类型
        # （非 str / 非 list）→ 跳过该子字段。整块仅在至少 1 个有效子字段时注入。
        protagonist = cj.get("protagonist")
        if isinstance(protagonist, dict):
            protagonist_inject: dict[str, Any] = {}
            identity = protagonist.get("identity")
            if isinstance(identity, str) and identity.strip():
                protagonist_inject["identity"] = identity
            core_drive = protagonist.get("core_drive")
            if isinstance(core_drive, str) and core_drive.strip():
                protagonist_inject["core_drive"] = core_drive
            personality_tags = protagonist.get("personality_tags")
            if isinstance(personality_tags, list):
                tag_strs = [
                    t for t in personality_tags if isinstance(t, str) and t.strip()
                ]
                if tag_strs:
                    protagonist_inject["personality_tags"] = tag_strs[
                        :_REFERENCE_CANON_PROTAGONIST_TAGS_CAP
                    ]
            foil_techniques = protagonist.get("foil_techniques")
            if isinstance(foil_techniques, list):
                ft_strs = [
                    t for t in foil_techniques if isinstance(t, str) and t.strip()
                ]
                if ft_strs:
                    protagonist_inject["foil_techniques"] = ft_strs[
                        :_REFERENCE_CANON_PROTAGONIST_FOILS_CAP
                    ]
            if protagonist_inject:
                inject["protagonist"] = protagonist_inject
                consumed.append("protagonist")
    elif consumer == _REFERENCE_CANON_CONSUMER_PLANNER:
        # Scene Planner 吃 emotion_curve（张力曲线）+ payoff_list（Scene 级爽点排布）。
        # emotion_curve 按 chapter_index 取连续段：优先取靠近中间 ±_REFERENCE_CANON_EMOTION_CAP
        # 范围的连续窗口（保留最新一端用于对齐节奏）；若总长 ≤ CAP 则全量。
        emotion_curve = cj.get("emotion_curve")
        if isinstance(emotion_curve, list) and emotion_curve:
            window = _select_emotion_window(emotion_curve, _REFERENCE_CANON_EMOTION_CAP)
            inject["emotion_curve"] = window
            consumed.append("emotion_curve")

        payoff_list = cj.get("payoff_list")
        if isinstance(payoff_list, list):
            inject["payoff_list"] = payoff_list[:_REFERENCE_CANON_PAYOFF_CAP]
            consumed.append("payoff_list")
    elif consumer == _REFERENCE_CANON_CONSUMER_WRITER:
        # Writer 吃 style_params：JSON 字段值（dict），整体序列化 ≤1500 字符；
        # 超出时按字段优先级逐项删减（保留 sentence_length_distribution +
        # dialogue_ratio + action_ratio + psychological_ratio + pov；paragraph_length_distribution
        # 与 environment_ratio 可省略），并打 ``__style_params_truncated__`` 标记。
        style_params = cj.get("style_params")
        if isinstance(style_params, dict) and style_params:
            trimmed, was_truncated = _trim_style_params(
                style_params, _REFERENCE_CANON_STYLE_MAX_CHARS,
            )
            inject["style_params"] = trimmed
            if was_truncated:
                inject["__style_params_truncated__"] = True
            consumed.append("style_params")
    # 未知 consumer 视为无字段注入（保安全，避免误塞）

    audit = {"canon_id": canon_id, "consumed_fields": consumed}
    return inject, audit


def _select_emotion_window(
    curve: list[Any], cap: int
) -> list[Any]:
    """emotion_curve 截断：取末尾 cap 条（保留最近节奏信号）。

    Scene Planner 主要关心"近期节奏信号"——前 1000 章的情绪点对当前章节的张力曲线
    设计参考价值有限。优先保留尾部 cap 条；若总长 ≤ cap 则全量。
    """
    if len(curve) <= cap:
        return list(curve)
    return list(curve[-cap:])


def _trim_style_params(
    style_params: dict[str, Any], max_chars: int
) -> tuple[dict[str, Any], bool]:
    """style_params 字符截断：按字段优先级逐项删减到 ≤max_chars 字符（JSON 序列化口径）。

    字段优先级（高→低）：
      1. ``pov``（强制保留，单值）
      2. ``dialogue_ratio``（对白比例）
      3. ``action_ratio``（动作比例）
      4. ``psychological_ratio``（心理比例）
      5. ``sentence_length_distribution``（句长分布）
      6. ``environment_ratio``（环境比例）
      7. ``paragraph_length_distribution``（段落长度，可省略）

    返回 ``(trimmed_dict, was_truncated)``。
    """
    priority = (
        "pov", "dialogue_ratio", "action_ratio", "psychological_ratio",
        "sentence_length_distribution", "environment_ratio",
        "paragraph_length_distribution",
    )
    picked: dict[str, Any] = {}
    for key in priority:
        if key in style_params:
            picked[key] = style_params[key]
    try:
        encoded = json.dumps(picked, ensure_ascii=False)
    except (TypeError, ValueError):
        return picked, False
    if len(encoded) <= max_chars:
        return picked, False
    # 逐项删减（从低优先级往高）
    for key in reversed(priority):
        if key in picked:
            picked.pop(key)
            try:
                encoded = json.dumps(picked, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if len(encoded) <= max_chars:
                return picked, True
    # 即便删完仍超（极少见，全字段都是巨型 nested）→ 截断字符串本身（兜底）
    if not picked:
        return {}, True
    try:
        encoded = json.dumps(picked, ensure_ascii=False)
    except (TypeError, ValueError):
        return picked, True
    if len(encoded) <= max_chars:
        return picked, True
    # 兜底：硬截断到 max_chars 以内的可解析片段。Style 是 dict，硬截字符串会破坏
    # JSON——这里只标 truncated 并原样保留，调用方按"未消费"处理（不注入）。
    return {}, True
