"""deconstruct-book 工作流（Sprint 11 上半）。

把整本参考书按章节切分 → 逐章抽取抽象 ChapterExtract → 聚合为 ReferenceCanon
（六维度 + rhythm + style_params + metadata）→ G-sim 校验阻断相似度 → 落库
``reference_canons`` + ``canon_extracts`` + 渲染 Markdown 报告。

节点列表：
- ``T1_split_chapters`` (Transform)  —— 正则切分 ``第[0-9…]章`` 标题行；
  切不出 → 抛 ValueError ⇒ run FAILED（README 记录 MVP deviation：不走 Human 补切分）。
- ``T2_extract_chapters`` (Transform) —— 对每章调
  :func:`packages.core.agent_runtime.runner.run_agent` 跑 ``deconstructor_chapter``
  agent；输入按 prompt §A.5 构造（chapter.raw_text + 滑动窗口 chapter_digest ≤200 字）；
  产物 ChapterExtract 累积进 ``ctx["chapter_extracts"]``。
- ``T3_aggregate`` (AI)              —— 调 ``deconstructor_aggregate`` 聚合；
  输出过 :func:`_validate_canon_schema`（jsonschema Draft202012Validator，
  schema = ``docs/reference-canon/schemas/reference-canon.schema.json``）；
  不合规 → run_agent 1 次重试后再失败 ⇒ run FAILED。
- ``G_sim_check`` (Transform)        —— 对 T3 产物的 ``canon_json`` 全部 string 值拼接
  与原始 text 跑 13 字 shingle 检测（复用 ``packages.core.quality.guardrails._shingles``）；
  任一公共 shingle ⇒ run FAILED（B-2 边界）。
- ``T4_persist`` (State)             —— 生成 ``canon_id``、注入 metadata 五字段、
  渲染 ``report_md``、写 ``reference_canons`` 行 + ``canon_extracts`` 逐章行。

参考：
- :mod:`packages.workflows.chapter_plan.pipeline`（节点范式 + workflow 注册）
- :mod:`packages.core.agent_runtime.runner`（run_agent 调用方式、mock_script 注入）
- :mod:`packages.core.quality.guardrails`（G-sim 13 字 shingle 实现）
- :mod:`packages.core.workflow_runtime.engine`（engine 启动方式）
- ``docs/reference-canon/reference-canon-v0.md`` + ``schemas/reference-canon.schema.json``
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.guardrails import _shingles
from packages.core.workflow_runtime.engine import WorkflowNode


# =============================================================================
# 常量
# =============================================================================

# 中文数字（零一二三四五六七八九十百千两）混阿拉伯数字，匹配 ``第N章`` 标题行
_CHAPTER_HEAD_RE = re.compile(
    r"^[ \t\u3000]*第[0-9一二三四五六七八九十百千零两]+章[^\n]*$",
    re.MULTILINE,
)
# reader_profile 枚举（与 schema metadata.target_reader_profile 对齐）
_VALID_READER_PROFILES = (
    "male_fantasy",
    "male_urban",
    "male_system",
    "female_romance",
    "female_palace",
    "female_suspense",
    "general",
)
# G-sim shingle 长度（与 REQ-Q6 spec §4.6 一致：13 字连续字符）
_Q6_SHINGLE_LEN = 13
# T2 MVP warning 阈值（章节数 > 50 仍继续，但 ctx 留 warning）
_T2_LARGE_BOOK_THRESHOLD = 50
# T3 hierarchical_digest 切片阈值（< 100 章单轮；≥ 100 章按 50 章聚合摘要）
_T3_AGGREGATION_THRESHOLD = 100
_T3_BUCKET_SIZE = 50


# =============================================================================
# Schema 校验器（懒加载缓存）
# =============================================================================

_DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "reference-canon"
    / "schemas"
    / "reference-canon.schema.json"
)


@lru_cache(maxsize=1)
def _load_canon_validator(schema_path_str: str) -> Draft202012Validator:
    """加载并缓存 ReferenceCanon Schema 校验器。"""
    path = Path(schema_path_str)
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    return Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def _validate_canon_schema(canon_json: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """校验 ReferenceCanon JSON 对齐 ``reference-canon.schema.json``。

    返回错误列表（空 = 通过）。
    """
    sp = Path(schema_path) if schema_path is not None else _DEFAULT_SCHEMA_PATH
    validator = _load_canon_validator(str(sp))
    errs: list[str] = []
    for err in sorted(validator.iter_errors(canon_json), key=lambda e: list(e.absolute_path)):
        path_str = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errs.append(f"[schema] {path_str}: {err.message}")
    return errs


# =============================================================================
# 节点 1：T1 章节切分（Transform）
# =============================================================================


def _t1_split_chapters_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """按 ``第N章`` 行匹配切分章节；空文本 / 无匹配 → ValueError。"""
    text = ctx.get("text") or ""
    if not text.strip():
        raise ValueError("T1 split_chapters failed: empty text")

    matches = list(_CHAPTER_HEAD_RE.finditer(text))
    if not matches:
        raise ValueError(
            "T1 split_chapters failed: no '第N章' 标题行命中（regex: 第[0-9一二…]章）；"
            "MVP 不做 Human 补切分"
        )

    segments: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title_line = m.group(0).strip()
        raw_text = text[start:end]
        segments.append(
            {
                "chapter_index": i + 1,
                "title": title_line,
                "raw_text": raw_text,
            }
        )
    return {
        "segments": segments,
        "chapter_count": len(segments),
    }


# =============================================================================
# 节点 2：T2 逐章提取（Transform，单节点循环）
# =============================================================================


def _build_chapter_extract_input(
    *,
    chapter_index: int,
    raw_text: str,
    prev_digest: str | None,
    next_digest: str | None,
    target_reader_profile: str,
) -> dict[str, Any]:
    """按 prompt A §A.5 输入契约构造 chapter 输入 dict。"""
    return {
        "agent": "deconstructor_chapter",
        "prompt_version": "deconstructor-chapter:v0",
        "target_reader_profile": target_reader_profile,
        "chapter": {
            "chapter_index": chapter_index,
            "raw_text": raw_text,
        },
        "window": {
            "prev_chapter_digest": prev_digest,
            "next_chapter_digest": next_digest,
        },
        "function_tag_candidates": [
            "hook",
            "setup",
            "escalation",
            "turn",
            "climax",
            "resolution",
        ],
        "config": {
            "max_string_length": 80,
            "max_digest_length": 50,
            "max_raw_text_excerpt_overlap_chars": 8,
        },
    }


def _truncate_digest(s: str, limit: int = 200) -> str:
    """滑动窗口 digest 截断到 ≤200 字（按字符数）；过长 → 截尾加省略号标记。"""
    if not s:
        return s
    if len(s) <= limit:
        return s
    return s[:limit]


def _t2_extract_chapters_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """循环对每章调 deconstructor_chapter agent；产物 ChapterExtract 累积 ctx。

    注：raw_text 只在本节点内部使用（§1.2 B-3 边界），不写入 ctx 顶层 key 持久化；
    本节点返回的 ``chapter_extracts`` 仅为业务载荷列表（含 chapter_digest 等）。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    segments: list[dict[str, Any]] = ctx.get("segments") or []
    reader_profile = ctx.get("reader_profile") or "male_fantasy"
    mock_providers = ctx.get("mock_providers") or {}
    mock_script = mock_providers.get("deconstructor_chapter")

    if not segments:
        raise ValueError("T2 extract_chapters failed: no segments from T1")

    chapter_extracts: list[dict[str, Any]] = []
    # ``mock_script`` 在生产路径为 None；测试可注入 list[str] / callable / 单值。
    # list 模式在 run_agent 每次实例化新 MockProvider ⇒ _call_count 归零 ⇒ 永远返回 [0]，
    # 导致跨章 T2 调用拿不到差异化脚本。本节点在此把 list 模式「逐章弹出」」为单值再传，
    # 避免 runner 实例化造成的状态丢失；callable 模式直接透传（callable 跨实例
    # 重置也丢失，但生产无 callable 路径）。
    for i, seg in enumerate(segments):
        prev_digest = (
            chapter_extracts[i - 1].get("chapter_digest")
            if i > 0 and chapter_extracts
            else None
        )
        next_seg = segments[i + 1] if i + 1 < len(segments) else None
        next_digest = None
        # next_digest 取下一章 chapter_digest 尚未生成；MVP 简化取下一章 raw_text 截 200 字
        # （生产应由 LLM 在 T2 循环内先摘要；MVP 用 raw_text 截断兜底，调用方通过 mock 可控）
        if next_seg is not None:
            next_digest = _truncate_digest(next_seg.get("raw_text") or "", 200)

        # 逐章分发 mock_script（仅 list 模式；None / 单值 / callable 透传）
        per_chapter_script: Any = mock_script
        if isinstance(mock_script, list) and mock_script:
            picked = mock_script[i] if i < len(mock_script) else mock_script[-1]
            # MockProvider(scripted=str) 会把 str 当 iterable 取字符；
            # 这里统一包成 list[str]（单元素），让 MockProvider 返回整段 JSON。
            if isinstance(picked, str):
                per_chapter_script = [picked]
            else:
                per_chapter_script = picked

        payload = _build_chapter_extract_input(
            chapter_index=int(seg["chapter_index"]),
            raw_text=str(seg.get("raw_text") or ""),
            prev_digest=_truncate_digest(prev_digest or "", 200) if prev_digest else None,
            next_digest=next_digest,
            target_reader_profile=reader_profile,
        )
        # run_agent 自带 1 次重试；仍失败 → AgentOutputError 抛到节点 → run FAILED
        extract = run_agent(
            db_path,
            "deconstructor_chapter",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected=None,
            mock_script=per_chapter_script,
        )
        # chapter_index 回填（prompt A §A.7.2 强调）
        extract.setdefault("chapter_index", int(seg["chapter_index"]))
        chapter_extracts.append(extract)

    warnings: list[str] = []
    if len(chapter_extracts) > _T2_LARGE_BOOK_THRESHOLD:
        warnings.append(
            f"chapter count {len(chapter_extracts)} > {_T2_LARGE_BOOK_THRESHOLD}；MVP 仅 warning 不阻塞"
        )

    return {
        "chapter_extracts": chapter_extracts,
        "chapter_count": len(chapter_extracts),
        "warnings": warnings,
    }


# =============================================================================
# 节点 3：T3 聚合（AI）
# =============================================================================


def _build_hierarchical_digests(
    chapter_extracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成 hierarchical_digests（按 prompt B §B.5 输入契约）。

    MVP 简化：<100 章单轮 → 一个 span "1-N"；≥100 章按 50 章一段分桶摘要。
    每段 summary = 段内 chapter_digest 拼接（截断到 ≤200 字）。
    """
    n = len(chapter_extracts)
    if n == 0:
        return []
    if n < _T3_AGGREGATION_THRESHOLD:
        digests_text = " | ".join(
            ce.get("chapter_digest") or "" for ce in chapter_extracts
        )
        return [
            {
                "span": f"1-{n}",
                "summary": _truncate_digest(digests_text, 200),
                "key_payoffs": [],
            }
        ]
    # ≥100 章分桶
    digests: list[dict[str, Any]] = []
    for start in range(0, n, _T3_BUCKET_SIZE):
        end = min(start + _T3_BUCKET_SIZE, n)
        bucket_text = " | ".join(
            ce.get("chapter_digest") or "" for ce in chapter_extracts[start:end]
        )
        digests.append(
            {
                "span": f"{start + 1}-{end}",
                "summary": _truncate_digest(bucket_text, 200),
                "key_payoffs": [],
            }
        )
    return digests


def _build_aggregate_input(
    *,
    chapter_extracts: list[dict[str, Any]],
    book_title: str,
    reader_profile: str,
    deconstruct_date: str,
    deconstruct_version: str,
) -> dict[str, Any]:
    """按 prompt B §B.5 输入契约构造 aggregate 输入 dict。"""
    return {
        "agent": "deconstructor_aggregate",
        "prompt_version": "deconstructor-aggregate:v0",
        "target_reader_profile": reader_profile,
        "chapter_extracts": chapter_extracts,
        "hierarchical_digests": _build_hierarchical_digests(chapter_extracts),
        "source_book_meta": {
            "book_title": book_title,
            "license_check_status": {
                "checked": False,
                "license": "unknown",
                "compatible": False,
            },
        },
        "config": {
            "max_string_length": 80,
            "deconstruct_date": deconstruct_date,
            "deconstruct_version": deconstruct_version,
        },
    }


def _t3_aggregate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """调 deconstructor_aggregate agent；产物过 schema 校验。

    run_agent 自带 1 次重试；schema 校验失败抛 ValueError → run FAILED。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_extracts: list[dict[str, Any]] = ctx.get("chapter_extracts") or []
    book_title = ctx.get("book_title") or ""
    reader_profile = ctx.get("reader_profile") or "male_fantasy"
    deconstruct_date = ctx.get("deconstruct_date") or now_iso()
    deconstruct_version = ctx.get("deconstruct_version") or "deconstruct-book-v0"
    mock_providers = ctx.get("mock_providers") or {}
    mock_script = mock_providers.get("deconstructor_aggregate")

    if not chapter_extracts:
        raise ValueError("T3 aggregate failed: no chapter_extracts from T2")
    if not book_title:
        raise ValueError("T3 aggregate failed: missing book_title in ctx")

    payload = _build_aggregate_input(
        chapter_extracts=chapter_extracts,
        book_title=book_title,
        reader_profile=reader_profile,
        deconstruct_date=deconstruct_date,
        deconstruct_version=deconstruct_version,
    )

    canon_json = run_agent(
        db_path,
        "deconstructor_aggregate",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected=None,
        mock_script=mock_script,
    )

    # T3 schema 校验：失败立即抛错（run FAILED）
    schema_errors = _validate_canon_schema(canon_json)
    if schema_errors:
        raise ValueError(
            "T3 aggregate schema invalid: " + "; ".join(schema_errors[:5])
        )

    return {"canon_json": canon_json}


# =============================================================================
# 节点 4：G-sim 校验（Transform）
# =============================================================================


def _collect_strings(obj: Any) -> list[str]:
    """深度遍历 dict/list；返回全部 string 值列表。"""
    out: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_strings(item))
    elif isinstance(obj, str):
        out.append(obj)
    return out


def _g_sim_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """对 canon_json 全部 string 值拼接 vs 原文 text 跑 13 字 shingle 检测（B-2）。"""
    canon_json: dict[str, Any] = ctx.get("canon_json") or {}
    original_text: str = ctx.get("text") or ""

    canon_strings = _collect_strings(canon_json)
    canon_text = "".join(canon_strings)
    if not canon_text.strip():
        return {"g_sim_passed": True, "overlaps": []}

    text_shingles = set(_shingles(original_text, _Q6_SHINGLE_LEN))
    canon_shingles = set(_shingles(canon_text, _Q6_SHINGLE_LEN))

    if not text_shingles or not canon_shingles:
        return {"g_sim_passed": True, "overlaps": []}

    common = text_shingles & canon_shingles
    if common:
            # 取前 3 个示例（按出现位置在 canon_text 中的顺序）
            sorted_overlaps = sorted(
                common,
                key=lambda sh: canon_text.find(sh),
            )
            sample = sorted_overlaps[:3]
            raise ValueError(
                f"G-sim 阻断：canon_json 与原书存在 {len(common)} 个 ≥13 字公共片段；"
                f"样例: {sample}"
            )

    return {"g_sim_passed": True, "overlaps": []}


# =============================================================================
# 节点 5：T4 落库渲染（State）
# =============================================================================


def _inject_metadata(
    canon_json: dict[str, Any],
    *,
    book_title: str,
    reader_profile: str,
    deconstruct_date: str,
    deconstruct_version: str,
) -> dict[str, Any]:
    """按 schema §6.1 注入 metadata 五字段；保留 LLM 输出的其他字段。"""
    canon = dict(canon_json)
    canon["metadata"] = {
        "source_book_title": book_title,
        "deconstruct_date": deconstruct_date,
        "deconstruct_version": deconstruct_version,
        "target_reader_profile": reader_profile,
        "license_check_status": {
            "checked": False,
            "license": "unknown",
            "compatible": False,
        },
    }
    return canon


def _render_report_md(canon_json: dict[str, Any]) -> str:
    """渲染六维度摘要 markdown（人读）。"""
    lines: list[str] = ["# Reference Canon 报告", ""]
    md = canon_json.get("metadata") or {}
    lines.append(f"- 来源书名（内部追踪）：`{md.get('source_book_title', '')}`")
    lines.append(f"- 拆书日期：{md.get('deconstruct_date', '')}")
    lines.append(f"- 工作流版本：{md.get('deconstruct_version', '')}")
    lines.append(f"- 读者档：{md.get('target_reader_profile', '')}")
    lines.append("")

    logline = canon_json.get("logline") or ""
    lines.append("## Logline")
    lines.append(logline)
    lines.append("")

    spine = canon_json.get("spine") or []
    lines.append(f"## 起承转合骨架（共 {len(spine)} 章）")
    for ch in spine[:10]:
        if not isinstance(ch, dict):
            continue
        lines.append(
            f"- 第 {ch.get('chapter_index', '?')} 章 "
            f"[{ch.get('function_tag', '?')}] "
            f"{ch.get('title_pattern', '')}"
        )
    if len(spine) > 10:
        lines.append(f"- ... 其余 {len(spine) - 10} 章省略")
    lines.append("")

    factions = (canon_json.get("faction_map") or {}).get("factions") or []
    lines.append(f"## 势力图（{len(factions)} 个势力类型）")
    for f in factions[:5]:
        if isinstance(f, dict):
            lines.append(
                f"- {f.get('faction_id', '?')}: {f.get('type_pattern', '')} "
                f"（{f.get('power_layer', '')}）"
            )
    lines.append("")

    emotion = canon_json.get("emotion_curve") or []
    if emotion:
        sample = emotion[0]
        lines.append(f"## 情绪折线（共 {len(emotion)} 章）")
        if isinstance(sample, dict):
            lines.append(f"- 第 1 章 valence={sample.get('valence')} marker_type={sample.get('marker_type')}")
        lines.append("")

    payoffs = canon_json.get("payoff_list") or []
    lines.append(f"## 爽点钩子清单（{len(payoffs)} 项）")
    for p in payoffs[:5]:
        if isinstance(p, dict):
            lines.append(
                f"- {p.get('payoff_id', '?')} ch{p.get('chapter_index', '?')} "
                f"[{p.get('type', '?')}] intensity={p.get('intensity')}"
            )
    lines.append("")

    rhythm = canon_json.get("rhythm") or {}
    mini = rhythm.get("mini_climax_interval") or {}
    major = rhythm.get("major_climax_interval") or {}
    lines.append("## 节奏参数")
    lines.append(
        f"- mini climax 间隔：median={mini.get('median')}, "
        f"p25={mini.get('p25')}, p75={mini.get('p75')}"
    )
    lines.append(
        f"- major climax 间隔：median={major.get('median')}, "
        f"p25={major.get('p25')}, p75={major.get('p75')}"
    )
    lines.append(f"- 章末钩子率：{rhythm.get('chapter_end_hook_rate')}")
    gtc = rhythm.get("golden_three_compliance") or {}
    lines.append(f"- 黄金三章达标：{gtc}")
    lines.append("")

    style = canon_json.get("style_params") or {}
    lines.append("## 文风参数")
    lines.append(f"- pov: {style.get('pov')}")
    lines.append(f"- dialogue_ratio: {style.get('dialogue_ratio')}")
    lines.append(f"- action_ratio: {style.get('action_ratio')}")
    lines.append(f"- psychological_ratio: {style.get('psychological_ratio')}")
    lines.append(f"- environment_ratio: {style.get('environment_ratio')}")

    return "\n".join(lines)


def _t4_persist_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """生成 canon_id、注入 metadata、写 reference_canons + canon_extracts。"""
    db_path = ctx["db_path"]
    project_id = ctx.get("project_id")
    if not project_id:
        raise ValueError("T4 persist failed: missing project_id")

    canon_json = ctx.get("canon_json") or {}
    chapter_extracts: list[dict[str, Any]] = ctx.get("chapter_extracts") or []
    book_title = ctx.get("book_title") or ""
    reader_profile = ctx.get("reader_profile") or "male_fantasy"
    deconstruct_date = ctx.get("deconstruct_date") or now_iso()
    deconstruct_version = ctx.get("deconstruct_version") or "deconstruct-book-v0"

    canon_json = _inject_metadata(
        canon_json,
        book_title=book_title,
        reader_profile=reader_profile,
        deconstruct_date=deconstruct_date,
        deconstruct_version=deconstruct_version,
    )

    # 校验 reader_profile ∈ 枚举（schema 兜底）
    if reader_profile not in _VALID_READER_PROFILES:
        raise ValueError(
            f"T4 persist failed: reader_profile {reader_profile!r} 不在 schema 枚举中"
        )

    canon_id = new_id("can")
    report_md = _render_report_md(canon_json)
    now = now_iso()

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons (
                canon_id, project_id, title, reader_profile, canon_json,
                report_md, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                canon_id,
                project_id,
                book_title,
                reader_profile,
                json.dumps(canon_json, ensure_ascii=False),
                report_md,
                now,
            ),
        )
        for ce in chapter_extracts:
            conn.execute(
                """
                INSERT INTO canon_extracts (
                    extract_id, canon_id, chapter_index, extract_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_id("cex"),
                    canon_id,
                    int(ce.get("chapter_index") or 0),
                    json.dumps(ce, ensure_ascii=False),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "canon_id": canon_id,
        "canon_json": canon_json,
        "report_md": report_md,
        "extracts_count": len(chapter_extracts),
        "status": "active",
        "created_at": now,
    }


# =============================================================================
# 节点列表 + Workflow 注册
# =============================================================================


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("T1_split_chapters", "Transform", _t1_split_chapters_node),
        WorkflowNode("T2_extract_chapters", "Transform", _t2_extract_chapters_node),
        WorkflowNode("T3_aggregate", "AI", _t3_aggregate_node, agent_name="deconstructor_aggregate"),
        WorkflowNode("G_sim_check", "Transform", _g_sim_node),
        WorkflowNode("T4_persist", "State", _t4_persist_node),
    ]


WORKFLOW = {
    "name": "deconstruct-book",
    "version": "v0",
    "description": (
        "T1 切分 → T2 逐章提取 → T3 聚合 → G-sim 校验 → T4 落库；产出 ReferenceCanon"
    ),
    "nodes": _build_nodes(),
}


def register_workflow(workflow: dict[str, Any] = WORKFLOW) -> None:
    """由 :mod:`packages.workflows.__init__` 调用；注册到全局中心。"""
    from packages.workflows import register_workflow as _register

    _register(workflow)


__all__ = [
    "WORKFLOW",
    "register_workflow",
    "_validate_canon_schema",
]