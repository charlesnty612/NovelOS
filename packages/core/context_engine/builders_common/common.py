"""共享工具 / 题材包注入 / peek 预读（拆分自 builders_common.py，2026-09-13 V4.0）。

- 基础常量与工具：``_parse_json`` / ``_estimate_tokens`` / ``_attach_assembly_meta`` /
  ``_row_to_project`` / ``_row_to_chapter`` / ``_latest_draft`` / ``_chapter_project_id`` /
  ``_safe_copy``；
- 题材库 P1a/P1b：``_genre_pack_excerpt`` 注入段（director / scene_planner / writer 三 consumer）；
- peek 预读（V3.9 批次 2.3）：缓存键预判 + uncached 装配共用的「1 连接 1 条 JOIN」轻查询，
  含 ``state_version`` / 题材包指纹 / active canon / word_band 原文各维度。

本模块是包内最底层（不 import 兄弟子模块）；既有引用仍经包门面
``packages.core.context_engine.builders_common``（全量再导出）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """连接工厂：按名向包门面解析 ``get_connection``（保留既有 monkeypatch 补丁点）。

    拆分前 ``builders_common`` 模块直接绑定 ``packages.core.db.get_connection``；
    ``tests/unit/test_context_peek_convergence.py`` 依此在**模块属性**上做连接计数 /
    SQL trace 探针（``_COUNTER_MODULES`` + ``monkeypatch.setattr(bc_mod,
    "get_connection", ...)``）。拆分后本模块成了子模块，若在此静态绑定，探针会打到
    门面属性而实际调用绕过它（连接计数恒 0、trace 为空）。故此处每次调用现取门面
    属性：未打补丁时得到的仍是 ``packages.core.db.get_connection``（行为零变化），
    打补丁时与拆分前逐字同效。

    注：门面 ``builders_common.get_connection`` 再导出的**是 ``packages.core.db``
    原函数**（非本函数）；本函数只在包内被 peek / 尾段取数调用。
    """
    from . import get_connection as _facade_get_connection

    return _facade_get_connection(db_path)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# V3.9 批次 5.1 收尾：与 wordcount.DEFAULT_TARGET_WORD_COUNT 收敛同值（3000）。
# 旧值 2200（PRD §124 番茄 2000-2500 中点）与管线层 docstring 承诺的 3000 长期
# 矛盾（三处口径缺陷）；番茄带差异由 projects.word_band_json 项目级配置承载，
# 装配层默认值只管「未配置时的兜底」，统一为 3000。
_DEFAULT_TARGET_WORD_COUNT = 3000

_DEFAULT_STYLE_CONSTRAINTS = {
    "language": "zh-Hans",
    "pov": "third_limited",
    "dialogue_ratio": 0.4,
    "forbidden_words": ["仿佛", "如同", "本章目标"],
}


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# V3.9 批次 2.1：全局 token 预算兜底（只告警不阻断）
# ---------------------------------------------------------------------------

# 装配结果全局预算上限（token）；与 preview.py 的展示口径同源
# （``preview._TOKEN_BUDGET`` 直接引用本常量，避免两处各写 8000 后漂移）。
_ASSEMBLY_TOKEN_BUDGET = 8000
# 粗略 token 估算除数：4 字节 ≈ 1 token（中文场景保守近似，与 preview 同款）。
_TOKEN_DIVISOR = 4


def _estimate_tokens(value: Any) -> int:
    """粗略 token 估算：``len(json.dumps(value, ensure_ascii=False)) // 4``，至少 1。

    V3.9 批次 2.1：由本函数单点提供口径，``preview._token_estimate`` 与
    ``_assembly_meta.estimate_tokens`` 都引用它（展示 / 装配告警不再各算一套）。
    不可序列化 → 退回 ``str(value)``，不抛错。
    """
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(value)
    return max(1, len(serialized) // _TOKEN_DIVISOR)


def _attach_assembly_meta(
    payload: dict[str, Any], *, summary_truncated: bool | None = None,
) -> dict[str, Any]:
    """就地写入 ``_assembly_meta`` 后返回 payload（各装配路径末尾单点调用）。

    字段（下划线前缀 = 非注入侧契约：调用方 / 前端不得当作 prompt 内容消费）：
    - ``estimate_tokens``：对 payload 本体（不含 ``_assembly_meta`` 自身）估算；
    - ``token_budget``：``_ASSEMBLY_TOKEN_BUDGET``；
    - ``token_budget_exceeded``：超预算标记——**只告警，不阻断也不改写已装配内容**
      （真裁剪仍靠各字段 cap 与摘要链 token 预算截断；一刀切割会破坏 JSON 契约）；
    - ``summary_truncated``：可选，摘要链是否被预算截断（仅 director 装配传入）。
    """
    body = {k: v for k, v in payload.items() if k != "_assembly_meta"}
    tokens = _estimate_tokens(body)
    meta: dict[str, Any] = {
        "estimate_tokens": tokens,
        "token_budget": _ASSEMBLY_TOKEN_BUDGET,
        "token_budget_exceeded": tokens > _ASSEMBLY_TOKEN_BUDGET,
    }
    if summary_truncated is not None:
        meta["summary_truncated"] = bool(summary_truncated)
    payload["_assembly_meta"] = meta
    return payload


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _row_to_chapter(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["plan_json"] = _parse_json(d.get("plan_json")) or {}
    return d


def _latest_draft(conn: sqlite3.Connection, chapter_id: str) -> dict[str, Any] | None:
    """取该章最新 draft 行（按 created_at DESC）。"""
    row = conn.execute(
        """
        SELECT * FROM drafts
        WHERE chapter_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()
    return dict(row) if row else None


def _chapter_project_id(conn: sqlite3.Connection, chapter_id: str) -> str | None:
    row = conn.execute(
        "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
    ).fetchone()
    return row["project_id"] if row else None


# ---------------------------------------------------------------------------
# 题材库 P1a：genre_pack 注入段（director 消费；项目绑定题材包时）
# ---------------------------------------------------------------------------
#
# 架构裁决（docs/roadmap/题材库-评估与落地计划-2026-09-13.md §三/§四）：
# - 题材包是**独立资源**（自有表 / 自有 schema 版本线），与 reference_canon 的双 slot
#   并存：本段与 ``_reference_canon_excerpt`` 互不覆盖、各自独立缺席；
# - 复用既有注入管道（consumer 模式），P1a 只落 director 消费的结构模板摘要 +
#   爽点类型清单（含密度约束文本化）+ pacing 摘要；writer / scene_planner 消费留 P1b；
# - **缺席时零注入**：未绑定 / pack 行缺失 / payload 解析失败 → 不写 payload 键
#   （既有装配行为逐字段不变）。
#
# 缓存键：director 装配键含 pack 指纹（``pack_id@version``，见
# :func:`_peek_chapter_context` 的 ``genre_pack_ref``）——换 pack / 换 version 必 miss
# （照 V3.9 批次 1B「漏键=脏命中」教训）。
_GENRE_PACK_CONSUMER_DIRECTOR = "director"
_GENRE_PACK_CONSUMER_PLANNER = "scene_planner"
_GENRE_PACK_CONSUMER_WRITER = "writer"
# 爽点类型清单条数上限（题材包通常 20~40 型；超出截断并标记）。
_GENRE_PACK_PAYOFF_CAP = 40
# 结构模板节拍条数上限。
_GENRE_PACK_BEAT_CAP = 12
# 单字段字符上限（description / fatigue_risk / verify_hint 等长文本）；
# 与 schema 的 maxLength 同量级，注入层再兜一刀。
_GENRE_PACK_FIELD_CHARS = 200
# pacing 段 JSON 序列化后的字符预算（超出按优先级逐项保留）。
_GENRE_PACK_PACING_MAX_CHARS = 1200
# scene_planner 爽点摘要（payoff_types）JSON 序列化字符预算（P1b 口径 ≤1500 字符）；
# 超预算按条目顺序保留到吃满并打 ``__genre_pack_truncated__``。
_GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS = 1500
# writer 题材体例（style_constraints）JSON 序列化字符预算。
_GENRE_PACK_STYLE_MAX_CHARS = 1500


def _truncate_text(value: Any, cap: int = _GENRE_PACK_FIELD_CHARS) -> str | None:
    """非空字符串 → 去空白 + 截断；其余 → None（缺字段语义）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:cap]


def _compose_density_text(payoff: dict[str, Any]) -> str | None:
    """把爽点类型的密度约束文本化（注入用的人话口径）。

    口径：``density_cap``（原文文本，如「每卷 2~3 次」）+ ``min_interval_chapters``
    （同型最小间隔章数）；两者都缺 → None（不注入该字段）。
    """
    parts: list[str] = []
    cap = _truncate_text(payoff.get("density_cap"), 120)
    if cap:
        parts.append(f"密度上限 {cap}")
    interval = payoff.get("min_interval_chapters")
    if isinstance(interval, int) and not isinstance(interval, bool):
        if interval > 0:
            parts.append(f"同型最小间隔 {interval} 章")
        else:
            parts.append("同型无最小间隔约束")
    return "；".join(parts) if parts else None


def _textualize_genre_payoff_types(
    payoff_types: list[Any], *, cap: int = _GENRE_PACK_PAYOFF_CAP,
) -> tuple[list[dict[str, Any]], bool]:
    """爽点类型清单 → 注入形态（含密度约束文本化）；返回 ``(items, truncated)``。

    逐条字段：``type_id`` / ``name`` / ``strength``（可选）/ ``density_constraint``
    （文本化密度约束）/ ``applicable`` / ``fatigue_risk`` / ``verify_hint``。
    ``type_id`` 非字符串或空 → 跳过该条（schema 已要求必填，此为装配层兜底）。
    """
    items: list[dict[str, Any]] = []
    for raw in payoff_types:
        if not isinstance(raw, dict):
            continue
        type_id = _truncate_text(raw.get("type_id"), 64)
        if not type_id:
            continue
        entry: dict[str, Any] = {
            "type_id": type_id,
            "name": _truncate_text(raw.get("name"), 80) or type_id,
        }
        strength = _truncate_text(raw.get("strength"), 8)
        if strength:
            entry["strength"] = strength
        density = _compose_density_text(raw)
        if density:
            entry["density_constraint"] = density
        applicable = _truncate_text(raw.get("applicable"), 120)
        if applicable:
            entry["applicable"] = applicable
        fatigue = _truncate_text(raw.get("fatigue_risk"))
        if fatigue:
            entry["fatigue_risk"] = fatigue
        verify = _truncate_text(raw.get("verify_hint"))
        if verify:
            entry["verify_hint"] = verify
        items.append(entry)
    return items[:cap], len(items) > cap


def _summarize_genre_structure_templates(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """结构模板 → 注入摘要（阶段骨架 + 节拍模板 + 卷数期望 + 主线悬念）。

    返回 ``(summary, truncated)``；``truncated`` 表示节拍模板条数超上限被截断
    （``_GENRE_PACK_BEAT_CAP``）。
    """
    out: dict[str, Any] = {}
    truncated = False

    model = _truncate_text(raw.get("structure_model"), 400)
    if model:
        out["structure_model"] = model

    skeleton = raw.get("full_book_skeleton")
    if isinstance(skeleton, dict):
        picked: dict[str, str] = {}
        for key, value in skeleton.items():
            if not isinstance(key, str):
                continue
            text = _truncate_text(value, 400)
            if text:
                picked[key] = text
        if picked:
            out["full_book_skeleton"] = picked

    beats = raw.get("arc_beat_template")
    if isinstance(beats, list):
        items: list[dict[str, Any]] = []
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            entry: dict[str, Any] = {}
            for key, cap in (("beat", 80), ("chapters", 40), ("content", 400), ("must", 300)):
                text = _truncate_text(beat.get(key), cap)
                if text:
                    entry[key] = text
            if entry:
                items.append(entry)
        if items:
            if len(items) > _GENRE_PACK_BEAT_CAP:
                truncated = True
            out["arc_beat_template"] = items[:_GENRE_PACK_BEAT_CAP]

    volume = raw.get("volume_count_expectation")
    if isinstance(volume, dict):
        picked_v: dict[str, Any] = {}
        for vkey, vvalue in volume.items():
            if not isinstance(vkey, str):
                continue
            if isinstance(vvalue, int) and not isinstance(vvalue, bool):
                picked_v[vkey] = vvalue
                continue
            vtext = _truncate_text(vvalue, 200)
            if vtext:
                picked_v[vkey] = vtext
        if picked_v:
            out["volume_count_expectation"] = picked_v

    mainline = raw.get("mainline_suspense")
    if isinstance(mainline, dict):
        picked_m: dict[str, Any] = {}
        role = _truncate_text(mainline.get("role"))
        if role:
            picked_m["role"] = role
        designs = mainline.get("common_designs")
        if isinstance(designs, list):
            texts = [t for t in (_truncate_text(d) for d in designs) if t]
            if texts:
                picked_m["common_designs"] = texts
        cadence = _truncate_text(mainline.get("cadence"))
        if cadence:
            picked_m["cadence"] = cadence
        if picked_m:
            out["mainline_suspense"] = picked_m

    return out, truncated


def _normalize_genre_pacing(pacing: dict[str, Any]) -> dict[str, Any]:
    """pacing 值归一：字符串列表逐条截断（``density_rules`` / ``redlines``）。"""
    out = dict(pacing)
    for key, item_cap, list_cap in (
        ("density_rules", 200, 20),
        ("redlines", 200, 20),
    ):
        value = out.get(key)
        if isinstance(value, list):
            out[key] = [t for t in (_truncate_text(v, item_cap) for v in value) if t][:list_cap]
    return out


def _trim_genre_pacing(
    pacing: dict[str, Any], max_chars: int = _GENRE_PACK_PACING_MAX_CHARS,
) -> tuple[dict[str, Any], bool]:
    """pacing 段字符预算：按优先级逐项保留到 JSON ≤ ``max_chars``。

    优先级（高→低）：章字数带 / 章字数 / 密度规则 / 节奏红线 / 单元预算 / 全书预算 /
    其余键。单键自身超预算 → 跳过该键（不硬截断 JSON 结构）。
    返回 ``(picked, truncated)``。
    """
    priority = (
        "chapter_words", "chapter_word_band", "density_rules", "redlines",
        "arc_words", "book_words",
    )
    ordered = [k for k in priority if k in pacing] + [
        k for k in pacing if k not in priority
    ]
    picked: dict[str, Any] = {}
    for key in ordered:
        candidate = dict(picked)
        candidate[key] = pacing[key]
        try:
            encoded = json.dumps(candidate, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if len(encoded) <= max_chars:
            picked = candidate
    return picked, len(picked) < len(pacing)


def _trim_genre_style(
    style: dict[str, Any], max_chars: int = _GENRE_PACK_STYLE_MAX_CHARS,
) -> tuple[dict[str, Any], bool]:
    """writer 用题材体例（``style_constraints``）归一 + 字符预算。

    优先级（高→低）：``forbidden_words`` / ``pov`` / ``sentence_style`` / ``language``
    / ``notes`` / 其余键；列表值逐条截断 120 字、上限 50 条，字符串值截断 300 字；
    整体 JSON 超 :data:`_GENRE_PACK_STYLE_MAX_CHARS` 时从低优先级键开始删减。
    返回 ``(picked, truncated)``。
    """
    priority = ("forbidden_words", "pov", "sentence_style", "language", "notes")
    ordered = [k for k in priority if k in style] + [k for k in style if k not in priority]
    picked: dict[str, Any] = {}
    for key in ordered:
        value = style[key]
        if isinstance(value, list):
            texts = [t for t in (_truncate_text(v, 120) for v in value) if t]
            if texts:
                picked[key] = texts[:50]
        elif isinstance(value, str):
            text = _truncate_text(value, 300)
            if text:
                picked[key] = text
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            picked[key] = value
        elif isinstance(value, dict):
            picked[key] = value
    try:
        encoded = json.dumps(picked, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}, bool(style)
    if len(encoded) <= max_chars:
        return picked, len(picked) < len(style)
    truncated = True
    for key in reversed(ordered):
        if key in picked:
            picked.pop(key)
            try:
                encoded = json.dumps(picked, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if len(encoded) <= max_chars:
                return picked, truncated
    return {}, truncated


def _textualize_genre_planner_payoffs(
    payoff_types: list[Any],
    *,
    max_chars: int = _GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS,
    cap: int = _GENRE_PACK_PAYOFF_CAP,
) -> tuple[list[dict[str, Any]], bool]:
    """scene_planner 用爽点摘要 → ``(items, truncated)``。

    与 director 的口径差别：这里服务「规划期硬约束」——逐条给出
    ``type_id`` / ``name`` / ``strength`` / ``density_cap``（原文）/
    ``min_interval_chapters``（整数）与文本化的 ``density_constraint``
    （「密度上限 X；同型最小间隔 N 章」，与 director 复用
    :func:`_compose_density_text`），条数上限 :data:`_GENRE_PACK_PAYOFF_CAP`。

    字符预算 :data:`_GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS`（1500）：按条目顺序
    逐条保留到吃满预算；被预算或条数砍掉任何一条 → ``truncated=True``
    （调用方打 ``__genre_pack_truncated__``）。
    """
    items: list[dict[str, Any]] = []
    for raw in payoff_types:
        if len(items) >= cap:
            return items, True
        if not isinstance(raw, dict):
            continue
        type_id = _truncate_text(raw.get("type_id"), 64)
        if not type_id:
            continue
        entry: dict[str, Any] = {
            "type_id": type_id,
            "name": _truncate_text(raw.get("name"), 80) or type_id,
        }
        strength = _truncate_text(raw.get("strength"), 8)
        if strength:
            entry["strength"] = strength
        density_cap = _truncate_text(raw.get("density_cap"), 120)
        if density_cap:
            entry["density_cap"] = density_cap
        interval = raw.get("min_interval_chapters")
        if isinstance(interval, int) and not isinstance(interval, bool):
            entry["min_interval_chapters"] = interval
        density = _compose_density_text(raw)
        if density:
            entry["density_constraint"] = density
        try:
            encoded = json.dumps([*items, entry], ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if len(encoded) > max_chars:
            return items, True
        items.append(entry)
    # 只按「预算 / 条数砍条」判截断；非法条目（非 dict / 缺 type_id）跳过不算截断
    # （它们本就不会进注入，标 truncated 会误导消费方以为内容被削）。
    return items, False


def _ratio_instruction(
    ratio_declarations: dict[str, Any] | None,
) -> str | None:
    """配比声明 → scene_planner 的配比指令文本；未声明 / 非法 → None。

    文本口径（写死给 prompt，不随实现漂移）：「题材配比声明：<键>=<份额%>…。」+
    「逐 scene 标注 scene_type（取值与配比维度一致），并为每个 scene 给出
    target_words（整数，总和≈本章目标字数；各 scene 字数占比遵守上述配比）。」
    占比按声明原值渲染（schema 保证 ∈ [0,1]）；份额之和 ≠ 1 时附
    「按份额相对比例理解」注记。
    """
    if not isinstance(ratio_declarations, dict) or not ratio_declarations:
        return None
    parts: list[str] = []
    total = 0.0
    for key, value in ratio_declarations.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        parts.append(f"{key.strip()}={float(value):.0%}")
        total += float(value)
    if not parts:
        return None
    text = (
        "题材配比声明："
        + "、".join(parts)
        + "。逐 scene 标注 scene_type（取值与配比维度一致），并为每个 scene 给出 "
        "target_words（整数，总和≈本章目标字数；各 scene 字数占比遵守上述配比）。"
    )
    if abs(total - 1.0) > 0.01:
        text += f"（声明份额之和 {total:.2f}，按份额相对比例理解）"
    return text


def _genre_pack_excerpt(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    consumer: str = _GENRE_PACK_CONSUMER_DIRECTOR,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """读项目绑定的题材包（``projects.genre_pack_id`` → ``genre_packs``）。

    返回 ``(inject, audit)``：
    - inject：写入 payload 的 ``genre_pack`` 键（None = 未绑定 / 无行 / 非法 payload）；
      **writer** consumer 例外——它把题材体例合并进既有 ``style_constraints`` 的
      ``genre_style`` 子键（见 :func:`packages.core.context_engine.writer_input`），
      inject 返回的是待合并的 ``style_constraints`` 段而非顶层 ``genre_pack`` 段；
    - audit：``{"pack_id", "version", "consumed_fields"}``，落到 ctx 顶层
      ``_genre_pack_consumed``（与 ``_reference_canon_consumed`` 同款溯源口径）。

    consumer 口径（P1a director / P1b scene_planner + writer）：
    - ``"director"``：``structure_templates``（结构模板摘要）/ ``payoff_types``
      （爽点类型 + 密度约束文本化）/ ``pacing``（节奏摘要）；
    - ``"scene_planner"``：``payoff_types``（规划期爽点摘要，≤1500 字符预算，
      超限打 ``__genre_pack_truncated__``）/ ``ratio_declarations`` + 由其派生的
      ``ratio_instruction``（配比分摊指令）；
    - ``"writer"``：``style_constraints``（题材体例，供 writer 合并为
      ``style_constraints.genre_style``）；
    - 其它 consumer：不注入（返回 ``(None, None)``）。

    容错：表 / 列缺失（迁移未跑的极老库）或 payload_json 非法 → ``(None, None)``，
    不抛错（既有装配行为不变）。
    """
    if consumer not in (
        _GENRE_PACK_CONSUMER_DIRECTOR,
        _GENRE_PACK_CONSUMER_PLANNER,
        _GENRE_PACK_CONSUMER_WRITER,
    ):
        return None, None
    try:
        row = conn.execute(
            """
            SELECT gp.pack_id, gp.name, gp.genre_tag, gp.version, gp.payload_json
            FROM projects pj
            JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
            WHERE pj.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0025 未跑过的极老库 → 视为未绑定（与无题材包行为零差异）。
        return None, None
    if row is None:
        return None, None

    payload = _parse_json(row["payload_json"])
    if not isinstance(payload, dict):
        return None, None

    pack_id = row["pack_id"]
    version = _as_int_or(row["version"])
    inject: dict[str, Any] = {
        "pack_id": pack_id,
        "name": row["name"],
        "genre_tag": row["genre_tag"],
        "version": version,
    }
    consumed: list[str] = []

    if consumer == _GENRE_PACK_CONSUMER_DIRECTOR:
        structure = payload.get("structure_templates")
        if isinstance(structure, dict) and structure:
            summary, structure_truncated = _summarize_genre_structure_templates(structure)
            if summary:
                inject["structure_templates"] = summary
                if structure_truncated:
                    inject["__structure_templates_truncated__"] = True
                consumed.append("structure_templates")

        payoff_types = payload.get("payoff_types")
        if isinstance(payoff_types, list) and payoff_types:
            items, payoffs_truncated = _textualize_genre_payoff_types(payoff_types)
            if items:
                inject["payoff_types"] = items
                if payoffs_truncated:
                    inject["__payoff_types_truncated__"] = True
                consumed.append("payoff_types")

        pacing = payload.get("pacing")
        if isinstance(pacing, dict) and pacing:
            picked, pacing_truncated = _trim_genre_pacing(_normalize_genre_pacing(pacing))
            if picked:
                inject["pacing"] = picked
                if pacing_truncated:
                    inject["__pacing_truncated__"] = True
                consumed.append("pacing")
    elif consumer == _GENRE_PACK_CONSUMER_PLANNER:
        payoff_types = payload.get("payoff_types")
        if isinstance(payoff_types, list) and payoff_types:
            items, payoffs_truncated = _textualize_genre_planner_payoffs(payoff_types)
            if items:
                inject["payoff_types"] = items
                if payoffs_truncated:
                    # P1b 口径标记名（与 director 的 __payoff_types_truncated__ 并存，
                    # 供 scene_planner 侧单点判断「爽点摘要被预算/条数截断」）。
                    inject["__genre_pack_truncated__"] = True
                consumed.append("payoff_types")
        ratio = payload.get("ratio_declarations")
        if isinstance(ratio, dict) and ratio:
            inject["ratio_declarations"] = ratio
            consumed.append("ratio_declarations")
            instruction = _ratio_instruction(ratio)
            if instruction:
                inject["ratio_instruction"] = instruction
    elif consumer == _GENRE_PACK_CONSUMER_WRITER:
        style = payload.get("style_constraints")
        if isinstance(style, dict) and style:
            picked, style_truncated = _trim_genre_style(style)
            if picked:
                inject["style_constraints"] = picked
                if style_truncated:
                    inject["__style_constraints_truncated__"] = True
                consumed.append("style_constraints")
        if not consumed:
            # writer 的注入面是「合并进 style_constraints.genre_style」——题材包未声明
            # 文风段时整段不注入（避免往 style_constraints 塞无文风含义的空壳）。
            return None, None

    audit = {"pack_id": pack_id, "version": version, "consumed_fields": consumed}
    return inject, audit


# ---------------------------------------------------------------------------
# 缓存键预判 / uncached 装配共用的轻量预读（V3.9 批次 2.3）
# ---------------------------------------------------------------------------
#
# 旧实现：四个 ``_peek_*`` 各自 ``get_connection``（每连接 3 条 PRAGMA 固定成本），
# 其中两个为拿 state_version 走 ``StoryStateService.get_current_state``（读 + 解析
# 整份 snapshot_json）；writer 缓存命中路径合计 5 连接。现收敛为「1 连接 1 条 JOIN」，
# 结果同时喂给 uncached 装配复用（``peek=`` 参数，见 director/writer 调用点）。
_PEEK_CHAPTER_SQL = """
    SELECT ch.number                           AS chapter_no,
           ch.project_id                       AS project_id,
           ch.plan_json                        AS plan_json,
           (SELECT MAX(sv.state_version) FROM story_states sv
             WHERE sv.project_id = COALESCE(?, ch.project_id))       AS state_version,
           (SELECT rc.canon_id FROM reference_canons rc
             WHERE rc.project_id = COALESCE(?, ch.project_id)
               AND rc.status = 'active'
             ORDER BY rc.created_at DESC, rc.canon_id DESC LIMIT 1)  AS active_canon_id,
           (SELECT pj2.genre_pack_id || '@' || gp.version
              FROM projects pj2
              LEFT JOIN genre_packs gp ON gp.pack_id = pj2.genre_pack_id
             WHERE pj2.project_id = COALESCE(?, ch.project_id))      AS genre_pack_ref,
           pj.word_band_json                   AS word_band_json
    FROM chapters ch
    LEFT JOIN projects pj ON pj.project_id = COALESCE(?, ch.project_id)
    WHERE ch.chapter_id = ?
"""

# 降级 SQL：极老库缺 reference_canons 表 / projects.word_band_json 列时上面的 JOIN 抛
# OperationalError → 退回只读章节三列，项目维度各项独立 try（逐项与旧 _peek_* 同语义）。
_PEEK_CHAPTER_FALLBACK_SQL = """
    SELECT number AS chapter_no, project_id AS project_id, plan_json AS plan_json
    FROM chapters WHERE chapter_id = ?
"""


def _as_int_or(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _empty_chapter_peek(chapter_id: str, project_id: str | None = None) -> dict[str, Any]:
    """无章节行 / 连接打不开时的空态 peek（字段齐全，调用方免判 None）。"""
    return {
        "found": False,
        "chapter_id": chapter_id,
        "project_id": project_id,
        "chapter_no": 0,
        "state_version": 0,
        "plan_json_raw": None,
        "word_band_json": None,
        "active_canon_id": None,
        "genre_pack_ref": None,
    }


def _project_max_state_version(conn: sqlite3.Connection, project_id: str) -> int:
    """``SELECT MAX(state_version) FROM story_states`` 轻查询；无快照 / 表缺失 → 0。

    与 ``StoryStateService.get_current_state`` 的 state_version 口径一致：main 路径取
    ``story_states`` 最新快照版本（``snapshots.latest_snapshot_version`` 即
    ``ORDER BY state_version DESC LIMIT 1``），无快照行时 ``build_initial_state`` 填 0。
    差别只在成本：旧实现为拿这个整数读 + 解析整份 snapshot_json。
    """
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS max_sv FROM story_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    return _as_int_or(row["max_sv"])


def _try_project_word_band_json(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读 ``projects.word_band_json`` 原文；列缺失 / 无项目 → None。"""
    try:
        row = conn.execute(
            "SELECT word_band_json FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0023 未跑过的极老库 → 列不存在 → 视为无覆盖（与无覆盖行为零差异）。
        return None
    if row is None:
        return None
    val = row["word_band_json"]
    return val if val else None


def _try_active_canon_id(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读最新 active canon_id；表缺失 / 无 canon → None。"""
    try:
        row = conn.execute(
            """
            SELECT canon_id FROM reference_canons
            WHERE project_id = ? AND status = 'active'
            ORDER BY created_at DESC, canon_id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["canon_id"] if row else None


def _try_project_genre_pack_ref(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读项目题材包指纹 ``<pack_id>@<version>``；表/列缺失 / 未绑定 → None。

    指纹形态与 :data:`_PEEK_CHAPTER_SQL` 内联子查询逐字一致（``|| '@' ||``）——
    director 缓存键的题材包维度在两个路径（主 SQL / 降级逐项读）必须同形，
    否则降级路径会把键维度写成另一种形状导致脏命中。
    """
    try:
        row = conn.execute(
            """
            SELECT pj.genre_pack_id || '@' || gp.version AS genre_pack_ref
            FROM projects pj
            LEFT JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
            WHERE pj.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0025 未跑过的极老库（无 genre_packs 表 / 无 genre_pack_id 列）→ 未绑定。
        return None
    if row is None:
        return None
    ref = row["genre_pack_ref"]
    return ref if ref else None


def _peek_chapter_context_conn(
    conn: sqlite3.Connection, chapter_id: str, *, project_id: str | None = None,
) -> dict[str, Any]:
    """已持有连接时的 peek 实现（不关闭连接；供同连接复用）。"""
    try:
        row = conn.execute(
            _PEEK_CHAPTER_SQL,
            (project_id, project_id, project_id, project_id, chapter_id),
        ).fetchone()
    except sqlite3.Error:
        return _peek_chapter_context_degraded(conn, chapter_id, project_id)
    if row is None:
        return _empty_chapter_peek(chapter_id, project_id)
    return {
        "found": True,
        "chapter_id": chapter_id,
        "project_id": row["project_id"],
        "chapter_no": _as_int_or(row["chapter_no"]),
        "state_version": _as_int_or(row["state_version"]),
        "plan_json_raw": row["plan_json"],
        "word_band_json": row["word_band_json"] or None,
        "active_canon_id": row["active_canon_id"] or None,
        "genre_pack_ref": row["genre_pack_ref"] or None,
    }


def _peek_chapter_context_degraded(
    conn: sqlite3.Connection, chapter_id: str, project_id: str | None,
) -> dict[str, Any]:
    """JOIN 失败时的逐项降级读（语义与收敛前各 ``_peek_*`` 单函数一致）。"""
    try:
        row = conn.execute(_PEEK_CHAPTER_FALLBACK_SQL, (chapter_id,)).fetchone()
    except sqlite3.Error:
        return _empty_chapter_peek(chapter_id, project_id)
    if row is None:
        return _empty_chapter_peek(chapter_id, project_id)
    pid = project_id or row["project_id"]
    info = _empty_chapter_peek(chapter_id, pid)
    info.update({
        "found": True,
        "chapter_no": _as_int_or(row["chapter_no"]),
        "plan_json_raw": row["plan_json"],
    })
    if pid:
        info["state_version"] = _project_max_state_version(conn, pid)
        info["word_band_json"] = _try_project_word_band_json(conn, pid)
        info["active_canon_id"] = _try_active_canon_id(conn, pid)
        info["genre_pack_ref"] = _try_project_genre_pack_ref(conn, pid)
    return info


def _peek_chapter_context(
    db_path: str | Path, chapter_id: str, *, project_id: str | None = None,
) -> dict[str, Any]:
    """单连接轻量预读（缓存键预判 + uncached 装配复用；V3.9 批次 2.3）。

    返回 ``{found, chapter_id, project_id, chapter_no, state_version, plan_json_raw,
    word_band_json, active_canon_id, genre_pack_ref}``：

    - ``state_version`` = ``MAX(story_states.state_version)``（无快照 → 0），口径见
      :func:`_project_max_state_version`；
    - ``plan_json_raw`` / ``word_band_json`` 为 DB 原文（不做解析，避免破坏键稳定性）；
    - ``genre_pack_ref`` = 绑定题材包指纹 ``<pack_id>@<version>``（未绑定 / 极老库 → None），
      供 director 缓存键的题材包维度使用（题材库 P1a）；
    - 任一字段缺失 / 异常 → 该字段安全 fallback，不抛错（连接打不开 → 全空态）；
    - ``project_id`` 显式传入时以其为准（保留 ``_peek_chapter_no_state_version`` 旧语义）；
      传 None 则用章节行自带的 ``project_id``（唯一正确来源）。
    """
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 打不开库 → 全空态（旧 _peek_project_id_* 同款）
        return _empty_chapter_peek(chapter_id, project_id)
    try:
        return _peek_chapter_context_conn(conn, chapter_id, project_id=project_id)
    finally:
        conn.close()


def _peek_project_context(db_path: str | Path, project_id: str | None) -> dict[str, Any]:
    """单连接读项目维度三标量（word_band_json / active_canon_id / state_version）。"""
    empty = {"word_band_json": None, "active_canon_id": None, "state_version": 0}
    if not project_id:
        return empty
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return empty
    try:
        return {
            "word_band_json": _try_project_word_band_json(conn, project_id),
            "active_canon_id": _try_active_canon_id(conn, project_id),
            "state_version": _project_max_state_version(conn, project_id),
        }
    finally:
        conn.close()


def _peek_chapter_no_state_version(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int]:
    """轻量读 chapters.number + state_version；用于缓存键预判。

    返回 ``(chapter_no, state_version)``；任一缺失 → (0, 0)。
    V3.9 批次 2.3：薄封装 :func:`_peek_chapter_context`（单连接 JOIN）。
    """
    info = _peek_chapter_context(db_path, chapter_id, project_id=project_id)
    return info["chapter_no"], info["state_version"]


def _peek_chapter_no_state_version_plan(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int, Any]:
    """V2.0 Wave C P1-1：director 缓存键的轻量预读。

    返回 ``(chapter_no, state_version, plan_json_raw)``。
    ``plan_json_raw`` 是 DB 原文 str（不做解析，避免破坏键稳定性）；
    缺失 → ``None``。任一异常 → 对应字段安全 fallback，不抛错。
    V3.9 批次 2.3：薄封装 :func:`_peek_chapter_context`（单连接 JOIN）。
    """
    info = _peek_chapter_context(db_path, chapter_id, project_id=project_id)
    return info["chapter_no"], info["state_version"], info["plan_json_raw"]


def _peek_project_id_from_chapter(db_path: str | Path, chapter_id: str) -> str | None:
    """轻量读 chapters.project_id；用于缓存键预判（V3.9 批次 2.3：单连接 JOIN）。"""
    return _peek_chapter_context(db_path, chapter_id)["project_id"]


def _peek_project_word_band_json(
    db_path: str | Path, project_id: str | None,
) -> str | None:
    """轻量读 ``projects.word_band_json``；列缺失 / 异常 / 无项目 → None。

    供 build_writer_input 缓存键预判用——返回原文（JSON 字符串）以构造稳定指纹；
    解析失败不在此处处理（uncached 路径由 ``resolve_band_config`` 兜底）。
    """
    if not project_id:
        return None
    return _peek_project_context(db_path, project_id)["word_band_json"]


def _fingerprint_word_band_json(raw: str | None) -> str:
    """``projects.word_band_json`` 轻量指纹（用于 writer 缓存键）。

    None / 空串 → ``"none"``（稳定指纹）；非空 → sha256[:16]（与 plan_fp 同款风格）。
    """
    if not raw:
        return "none"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return h[:16]


def _peek_active_canon_id(
    db_path: str | Path, project_id: str | None,
) -> str | None:
    """轻量读该项目最新 active reference_canon 的 ``canon_id``；无 → None。

    表缺失（老库 / 迁移未跑）→ 返回 ``None``；与无 canon 行为一致。
    V3.9 批次 2.3：薄封装 :func:`_peek_project_context`（单连接）。
    """
    if not project_id:
        return None
    return _peek_project_context(db_path, project_id)["active_canon_id"]


def _safe_copy(value: Any) -> Any:
    """对 writer payload 的 list 字段做浅拷贝（dict 元素逐个 dict() 拷贝）。

    payload 中的字符字段（如 ``chapter`` / ``project``）不会被裁剪，不需要深拷贝；
    这里只需在裁剪前快照出被裁剪的 3 个键，防止后续 in-place 修改后无法量化
    before 体积。``copy.deepcopy`` 在 SQLite Row 等不可序列化对象上会失败，故
    采用「list 包浅拷贝 + dict 元素逐个 dict()」的折中：list 顶层新建避免共享
    引用；dict 元素新建避免子项共享。
    """
    import copy as _copy

    try:
        return _copy.copy(value)
    except Exception:  # noqa: BLE001
        if isinstance(value, list):
            return [dict(x) if isinstance(x, dict) else x for x in value]
        if isinstance(value, dict):
            return dict(value)
        return value
