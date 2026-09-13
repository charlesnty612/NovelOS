"""题材包核销层 v1（题材库 P1b）——``verify_chapter`` 与 ``GenreCheckResult``。

职责：把题材包 payload 里的**规定性约束**与某一章的既有产物对账，产出
``GenreIssue``（``rule_id`` 前缀 ``GENRE-``、``severity`` 恒 ``warning``）列表。
v1 只核两类（roadmap §五 P1 口径，report-only **不阻断**）：

1. **配比偏差**（``ratio_declarations``）：scene_plan 里逐 scene 声明 ``scene_type``
   与 ``target_words`` 时，按 scene 字数分摊换算出的实际配比与声明配比的偏离；
   总偏离 > :data:`RATIO_DEVIATION_THRESHOLD`（10%）记 issue。
2. **红线**：
   - 字数带一致性：章实际字数与 ``pacing.chapter_word_band``（``low``/``high``）比对，
     出带记 issue；
   - 红线文本命中：``pacing.redlines`` 条目文本命中该章 ``plan_json.deviations`` /
     ``length_report`` 时记 issue（v1 保守口径，见 :func:`_match_redlines`）。

数据来源（全部只读；任一缺失 → 该项跳过并记录原因，不抛错、不阻断评审）：

- ``chapters``：``project_id`` / ``plan_json``（``deviations`` 注记）；
- ``drafts``：最新草稿正文（字数兜底口径 :func:`packages.core.quality.wordcount.visible_chars`）；
- ``workflow_runs``（``JOIN workflows``，``name='chapter-write'``）：``checkpoint_json``
  里的 ``scene_plan``（scene_planner 权威产出，见 chapter_write 的 checkpoint 保留清单）
  与 ``length_report``（length_check 实测字数）；
- ``projects.genre_pack_id`` → ``genre_packs``：未显式传 ``pack`` 时的绑定题材包。

边界（与 P1a README 一致的「读侧全容错」）：
- 无绑定题材包 → ``GenreCheckResult(bound=False, checked=False)``，零 issue
  （调用方据此整段跳过，零行为变化）；
- 极老库缺 ``genre_packs`` 表 / ``workflow_runs`` 行 / 脏 checkpoint_json →
  对应来源缺席，按「跳过 + 原因」处理；
- 本模块**不产 error / 不进** :data:`packages.core.quality.issues.BLOCKING_RULES`
  （质量门禁阻断白名单不动）；``GenreIssue.to_quality_issue()`` 给出与 quality
  ``Issue`` 同形的 informational 条目。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.quality.wordcount import visible_chars

__all__ = [
    "GENRE_ISSUE_RULE_PREFIX",
    "GENRE_ISSUE_SEVERITY",
    "RATIO_DEVIATION_THRESHOLD",
    "GenreCheckResult",
    "GenreIssue",
    "verify_chapter",
]

# issue 规则前缀 / severity：核销层 v1 恒 warning（informational，不阻断）。
GENRE_ISSUE_RULE_PREFIX = "GENRE-"
GENRE_ISSUE_SEVERITY = "warning"

# 配比总偏离阈值（绝对份额之和）；> 该值记 issue。
RATIO_DEVIATION_THRESHOLD = 0.10

# 规则 id
RULE_RATIO_DEVIATION = "GENRE-RATIO-DEVIATION"
RULE_WORD_BAND_DEVIATION = "GENRE-WORD-BAND-DEVIATION"
RULE_REDLINE_HIT = "GENRE-REDLINE-HIT"

# quality category 口径（packages.core.quality.issues.Category 的合法值）：
# 配比属 payoff 维度、字数带 / 红线属 pacing 维度。
_CATEGORY_RATIO = "payoff"
_CATEGORY_PACING = "pacing"

# chapter-write 工作流名（checkpoint_json 里 scene_plan / length_report 的来源）。
_WRITE_WORKFLOW_NAME = "chapter-write"


@dataclass(frozen=True)
class GenreIssue:
    """题材核销 issue（report-only）。

    字段与 ``packages.core.quality.models.Issue`` 同形（``to_quality_issue`` 直接
    构造合法 dict），但**不是** quality engine 的 Issue 实例——核销层不写
    ``quality_reports``，只把条目挂到评审报告 / 调用方。

    ``severity`` 恒 ``"warning"``（:data:`GENRE_ISSUE_SEVERITY`）；``rule_id`` 恒以
    ``GENRE-`` 开头，故 :func:`packages.core.quality.issues.is_blocking_issue` 对它
    恒为 False（阻断白名单不含 GENRE- 规则）。
    """

    rule_id: str
    message: str
    category: str = _CATEGORY_PACING
    severity: str = GENRE_ISSUE_SEVERITY
    location: str = "<unknown>"
    suggestion: str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """评审报告 / API 用 dict 形态（与 quality issue dump 同形）。"""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
            "evidence_refs": list(self.evidence_refs),
        }

    def to_quality_issue(self) -> dict[str, Any]:
        """quality ``Issue`` 兼容 dict（informational 通道；不落库、不参与评分）。"""
        return self.to_dict()


@dataclass
class GenreCheckResult:
    """核销结果（``genre_check`` 段的数据源）。

    - ``bound`` / ``checked``：是否绑定题材包 / 是否真的核了至少一项；
    - ``issues``：``GENRE-`` 前缀 issue 列表（可能为空）；
    - ``ratio_check`` / ``redline_check``：两项检查的明细（``checked=False`` 时带
      ``reason``）；``None`` 表示该维度在 payload 里未声明；
    - ``skipped``：跳过原因清单（``"no_binding"`` / ``"ratio_declarations_not_declared"`` …）；
    - ``error``：只在读路径整体失败时非空（仍不抛错）。
    """

    bound: bool = False
    checked: bool = False
    pack_id: str | None = None
    pack_version: int | None = None
    issues: list[GenreIssue] = field(default_factory=list)
    ratio_check: dict[str, Any] | None = None
    redline_check: dict[str, Any] | None = None
    skipped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def rule_ids(self) -> list[str]:
        return [i.rule_id for i in self.issues]

    def to_dict(self) -> dict[str, Any]:
        """``review_report["genre_check"]`` 的落点形态（纯 JSON 可序列化）。"""
        return {
            "bound": self.bound,
            "checked": self.checked,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "issues": [i.to_dict() for i in self.issues],
            "issue_count": len(self.issues),
            "rule_ids": self.rule_ids,
            "ratio_check": self.ratio_check,
            "redline_check": self.redline_check,
            "skipped": list(self.skipped),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 读路径（全容错）
# ---------------------------------------------------------------------------


def _parse_json(raw: Any) -> Any:
    """JSON 原文 → 对象；已是 dict/list 原样返回；非法 → None。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _load_chapter_sources(db_path: str | Path, chapter_id: str) -> dict[str, Any]:
    """一次性读核销所需来源（章节行 / 最新草稿 / 最近 chapter-write run）。

    任一查询失败 → 该来源缺席（``None``），不抛错（与 P1a 读侧全容错同款）。
    """
    sources: dict[str, Any] = {
        "found": False,
        "project_id": None,
        "number": None,
        "plan_json": {},
        "draft_content": None,
        "draft_version": None,
        "scene_plan": None,
        "length_report": None,
        "write_run_id": None,
    }
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 打不开库 → 全缺席
        return sources
    try:
        try:
            row = conn.execute(
                "SELECT project_id, number, plan_json FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            sources["found"] = True
            sources["project_id"] = row["project_id"]
            sources["number"] = row["number"]
            plan = _parse_json(row["plan_json"])
            sources["plan_json"] = plan if isinstance(plan, dict) else {}

        try:
            draft = conn.execute(
                "SELECT content, version FROM drafts WHERE chapter_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (chapter_id,),
            ).fetchone()
        except sqlite3.Error:
            draft = None
        if draft is not None:
            sources["draft_content"] = draft["content"] or ""
            sources["draft_version"] = draft["version"]

        try:
            run = conn.execute(
                """
                SELECT wr.run_id AS run_id, wr.checkpoint_json AS checkpoint_json
                FROM workflow_runs wr
                JOIN workflows wf ON wf.workflow_id = wr.workflow_id
                WHERE wr.chapter_id = ? AND wf.name = ?
                ORDER BY wr.started_at DESC, wr.run_id DESC
                LIMIT 1
                """,
                (chapter_id, _WRITE_WORKFLOW_NAME),
            ).fetchone()
        except sqlite3.Error:
            run = None
        if run is not None:
            sources["write_run_id"] = run["run_id"]
            checkpoint = _parse_json(run["checkpoint_json"])
            if isinstance(checkpoint, dict):
                scene_plan = checkpoint.get("scene_plan")
                sources["scene_plan"] = scene_plan if isinstance(scene_plan, dict) else None
                length_report = checkpoint.get("length_report")
                sources["length_report"] = (
                    length_report if isinstance(length_report, dict) else None
                )
    finally:
        conn.close()
    return sources


def _load_bound_pack(db_path: str | Path, project_id: str | None) -> dict[str, Any] | None:
    """读项目绑定题材包行（``pack_id`` / ``version`` / ``payload``）；无 → None。"""
    if not project_id:
        return None
    conn = get_connection(db_path)
    try:
        try:
            row = conn.execute(
                """
                SELECT gp.pack_id, gp.version, gp.payload_json
                FROM projects pj
                JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
                WHERE pj.project_id = ?
                """,
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            # 0025 未跑过的极老库 → 视为未绑定。
            return None
    finally:
        conn.close()
    if row is None:
        return None
    payload = _parse_json(row["payload_json"])
    return {
        "pack_id": row["pack_id"],
        "version": int(row["version"] or 0),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _split_pack(pack: Any) -> tuple[dict[str, Any], str | None, int | None]:
    """``pack`` 参数归一 → ``(payload, pack_id, version)``。

    接受：题材包行 dict（含 ``payload`` 键，service 投影）/ 裸 payload dict /
    None（未传）。无法识别 → 空 payload（各段缺席 → 全跳过）。
    """
    if not isinstance(pack, dict):
        return {}, None, None
    if isinstance(pack.get("payload"), dict):
        version = pack.get("version")
        try:
            version_int = int(version) if version is not None else None
        except (TypeError, ValueError):
            version_int = None
        return pack["payload"], pack.get("pack_id"), version_int
    return pack, pack.get("pack_id"), None


# ---------------------------------------------------------------------------
# 检查 a：配比偏差
# ---------------------------------------------------------------------------


def _normalize_declared(declared: dict[str, Any]) -> dict[str, float]:
    """配比声明归一：丢弃非数值 / 非正份额，按总和归一化到 1。

    声明值之和为 0（或全部非法）→ 空 dict（调用方按「未声明」处理）。
    """
    cleaned: dict[str, float] = {}
    for key, value in declared.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        cleaned[key.strip()] = float(value)
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in cleaned.items()}


def _check_ratio(
    scene_plan: dict[str, Any] | None,
    ratio_declarations: Any,
    *,
    location: str,
) -> tuple[dict[str, Any], list[GenreIssue]]:
    """配比偏差检查；返回 ``(明细 dict, issues)``。

    明细 ``checked=False`` + ``reason`` 表示跳过（未声明配比 / 无 scene_plan /
    无任何 scene 声明 scene_type）；``checked=True`` 时给出 declared / observed /
    deviations / total_deviation。
    """
    detail: dict[str, Any] = {
        "checked": False,
        "reason": None,
        "declared": {},
        "observed": {},
        "deviations": {},
        "total_deviation": 0.0,
        "threshold": RATIO_DEVIATION_THRESHOLD,
        "deviation_exceeded": False,
        "typed_scene_count": 0,
        "untyped_scene_count": 0,
        "typed_words": 0,
    }
    if not isinstance(ratio_declarations, dict) or not ratio_declarations:
        detail["reason"] = "ratio_declarations_not_declared"
        return detail, []
    declared = _normalize_declared(ratio_declarations)
    if not declared:
        detail["reason"] = "ratio_declarations_invalid"
        return detail, []
    detail["declared"] = declared

    scenes = scene_plan.get("scenes") if isinstance(scene_plan, dict) else None
    if not isinstance(scenes, list) or not scenes:
        detail["reason"] = "no_scene_plan"
        return detail, []

    words_by_type: dict[str, int] = {}
    untyped = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_type = scene.get("scene_type")
        if not isinstance(scene_type, str) or not scene_type.strip():
            untyped += 1
            continue
        target = scene.get("target_words")
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            target = 0
        words_by_type[scene_type.strip()] = words_by_type.get(scene_type.strip(), 0) + max(
            0, int(target)
        )
    detail["untyped_scene_count"] = untyped
    detail["typed_scene_count"] = sum(1 for s in scenes if isinstance(s, dict)) - untyped
    typed_words = sum(words_by_type.values())
    detail["typed_words"] = typed_words
    if not words_by_type:
        detail["reason"] = "no_scene_type"
        return detail, []
    if typed_words <= 0:
        # 有 scene_type 但没有任何 scene 声明 target_words（预算口径由 writer 装配
        # 兜底等分，checkpoint 里的 scene_plan 是 scene_planner 原始产出）→ 无可核。
        detail["reason"] = "no_scene_target_words"
        return detail, []

    observed = {k: v / typed_words for k, v in words_by_type.items()}
    keys = sorted(set(declared) | set(observed))
    deviations = {k: observed.get(k, 0.0) - declared.get(k, 0.0) for k in keys}
    total_deviation = sum(abs(v) for v in deviations.values())
    detail.update(
        {
            "checked": True,
            "observed": observed,
            "deviations": deviations,
            "total_deviation": round(total_deviation, 6),
            "deviation_exceeded": total_deviation > RATIO_DEVIATION_THRESHOLD,
        }
    )
    issues: list[GenreIssue] = []
    if detail["deviation_exceeded"]:
        rendered = "、".join(
            f"{k} 声明 {declared.get(k, 0.0):.0%} / 实际 {observed.get(k, 0.0):.0%}"
            for k in keys
        )
        note = (
            f"；另有 {untyped} 个 scene 未标注 scene_type（未计入配比）" if untyped else ""
        )
        issues.append(
            GenreIssue(
                rule_id=RULE_RATIO_DEVIATION,
                category=_CATEGORY_RATIO,
                message=(
                    f"配比总偏离 {total_deviation:.1%} > 阈值 "
                    f"{RATIO_DEVIATION_THRESHOLD:.0%}：{rendered}{note}"
                ),
                location=location,
                suggestion=(
                    "按题材包 ratio_declarations 重排 scene 的 scene_type 与 target_words 分摊"
                    "（规划期硬约束），或人工确认配比声明需要更新。"
                ),
                evidence_refs=[f"scene_type:{k}" for k in keys],
            )
        )
    return detail, issues


# ---------------------------------------------------------------------------
# 检查 b：红线（字数带一致性 + 红线文本命中）
# ---------------------------------------------------------------------------


def _normalize_text(value: Any) -> str:
    """文本归一（折叠空白 + 去首尾）；非字符串 → 空串。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _collect_deviation_text(plan_json: dict[str, Any], length_report: Any) -> str:
    """章节偏差注记语料（plan_json.deviations + length_report）→ 归一文本。"""
    parts: list[str] = []
    deviations = plan_json.get("deviations") if isinstance(plan_json, dict) else None
    if isinstance(deviations, list):
        for entry in deviations:
            text = _normalize_text(entry)
            if text:
                parts.append(text)
    elif isinstance(deviations, str):
        parts.append(_normalize_text(deviations))
    if isinstance(length_report, dict):
        parts.append(json.dumps(length_report, ensure_ascii=False, sort_keys=True))
    elif length_report is not None:
        parts.append(str(length_report))
    return _normalize_text(" | ".join(p for p in parts if p))


def _match_redlines(redlines: Any, corpus: str) -> list[str]:
    """红线文本命中（v1 保守口径）：整条红线归一后为 corpus 子串即命中。

    口径说明：roadmap 举的例（「压抑段≤2章」）无法由 plan beats 判定，v1 不做语义
    判定——只做「红线条目原文出现在该章 deviations / length_report」的确定性文本
    匹配（写侧若把红线写进偏差注记即可被核到）。故本检查默认零命中，不产生噪音。
    """
    if not isinstance(redlines, list) or not corpus:
        return []
    corpus_norm = corpus.lower()
    hits: list[str] = []
    for raw in redlines:
        text = _normalize_text(raw)
        if text and text.lower() in corpus_norm:
            hits.append(text)
    return hits


def _check_redlines(
    payload: dict[str, Any],
    sources: dict[str, Any],
    *,
    location: str,
) -> tuple[dict[str, Any] | None, list[GenreIssue]]:
    """红线检查；返回 ``(明细 dict | None, issues)``。

    ``pacing`` 段缺席 → ``(None, [])``（该题材包未声明节奏公约，整项不展示）。
    """
    pacing = payload.get("pacing")
    if not isinstance(pacing, dict) or not pacing:
        return None, []

    issues: list[GenreIssue] = []
    detail: dict[str, Any] = {}

    # b1) 字数带一致性（pacing.chapter_word_band）
    band = pacing.get("chapter_word_band")
    band_detail: dict[str, Any] = {"checked": False, "reason": None}
    declared_band: dict[str, int] = {}
    if isinstance(band, dict):
        for key in ("low", "high"):
            value = band.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                declared_band[key] = value
    length_report = sources.get("length_report")
    if length_report is not None and isinstance(length_report.get("visible_chars"), int):
        word_count = int(length_report["visible_chars"])
        source = "length_report"
    elif isinstance(sources.get("draft_content"), str):
        word_count = visible_chars(sources["draft_content"])
        source = "draft"
    else:
        word_count = None
        source = None
    band_detail.update({"declared": declared_band, "word_count": word_count, "source": source})
    if not {"low", "high"} <= set(declared_band):
        band_detail["reason"] = "chapter_word_band_not_declared"
    elif word_count is None:
        band_detail["reason"] = "no_draft"
    else:
        within = declared_band["low"] <= word_count <= declared_band["high"]
        band_detail.update({"checked": True, "within_band": within})
        if not within:
            side = "低于下限" if word_count < declared_band["low"] else "超出上限"
            issues.append(
                GenreIssue(
                    rule_id=RULE_WORD_BAND_DEVIATION,
                    category=_CATEGORY_PACING,
                    message=(
                        f"实际字数 {word_count}（{source}）{side}题材包字数带 "
                        f"{declared_band['low']}~{declared_band['high']}"
                    ),
                    location=location,
                    suggestion="对齐题材包 pacing.chapter_word_band，或人工确认该章例外。",
                    evidence_refs=[f"word_band:{declared_band['low']}-{declared_band['high']}"],
                )
            )
    detail["word_band"] = band_detail

    # b2) 红线文本命中（pacing.redlines × 章节 deviations / length_report）
    redlines = pacing.get("redlines")
    redline_detail: dict[str, Any] = {"checked": False, "reason": None, "declared": [], "hits": []}
    if isinstance(redlines, list) and redlines:
        declared_redlines = [t for t in (_normalize_text(r) for r in redlines) if t]
        corpus = _collect_deviation_text(sources.get("plan_json") or {}, length_report)
        hits = _match_redlines(declared_redlines, corpus)
        redline_detail.update(
            {
                "checked": True,
                "declared": declared_redlines,
                "hits": hits,
                "corpus_chars": len(corpus),
            }
        )
        for hit in hits:
            issues.append(
                GenreIssue(
                    rule_id=RULE_REDLINE_HIT,
                    category=_CATEGORY_PACING,
                    message=f"红线文本命中该章偏差注记：{hit}",
                    location=location,
                    suggestion="人工确认该章是否触碰题材节奏红线（v1 只做文本匹配）。",
                    evidence_refs=["plan_json.deviations"],
                )
            )
    else:
        redline_detail["reason"] = "redlines_not_declared"
    detail["redlines"] = redline_detail

    return detail, issues


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def verify_chapter(
    db_path: str | Path,
    chapter_id: str,
    pack: Any = None,
) -> GenreCheckResult:
    """核销单章（report-only）。

    参数：
        db_path / chapter_id：目标章。
        pack：题材包行 dict（含 ``payload``）/ 裸 payload dict；省略（``None``）时
            读 ``projects.genre_pack_id`` 的绑定题材包。

    返回 :class:`GenreCheckResult`；**任何读路径异常都不抛错**（``error`` 字段
    记录原因，``checked=False``），调用方据此整段跳过或只做展示。

    零行为变化口径：未绑定题材包 → ``bound=False`` / 零 issue（不产生任何条目）。
    """
    result = GenreCheckResult()
    location = str(chapter_id)
    try:
        sources = _load_chapter_sources(db_path, chapter_id)
    except Exception as exc:  # noqa: BLE001 —— 读路径整体失败（不阻断评审）
        result.error = f"read_failed: {exc}"
        result.skipped.append("chapter_sources_unavailable")
        return result
    if not sources["found"]:
        result.skipped.append("chapter_not_found")
        return result

    if pack is None:
        try:
            bound = _load_bound_pack(db_path, sources.get("project_id"))
        except Exception as exc:  # noqa: BLE001
            result.error = f"binding_read_failed: {exc}"
            result.skipped.append("genre_pack_unavailable")
            return result
        if bound is None:
            result.skipped.append("no_binding")
            return result
        payload, pack_id, version = (
            bound["payload"], bound["pack_id"], bound["version"],
        )
    else:
        payload, pack_id, version = _split_pack(pack)

    result.bound = True
    result.pack_id = pack_id
    result.pack_version = version

    ratio_detail, ratio_issues = _check_ratio(
        sources.get("scene_plan"),
        payload.get("ratio_declarations"),
        location=location,
    )
    result.ratio_check = ratio_detail
    if not ratio_detail["checked"]:
        result.skipped.append(str(ratio_detail["reason"]))

    redline_detail, redline_issues = _check_redlines(payload, sources, location=location)
    result.redline_check = redline_detail
    if redline_detail is None:
        result.skipped.append("pacing_not_declared")
    else:
        if not redline_detail["word_band"]["checked"]:
            result.skipped.append(f"word_band:{redline_detail['word_band']['reason']}")
        if not redline_detail["redlines"]["checked"]:
            result.skipped.append(f"redlines:{redline_detail['redlines']['reason']}")

    result.issues = [*ratio_issues, *redline_issues]
    # checked = 至少有一项真的核过（声明了配比且 scene 带 scene_type，或字数带 /
    # 红线文本任一开核）；只声明了段但无输入（无 scene_type / 无草稿）不算核过。
    word_band_checked = bool(
        redline_detail is not None and redline_detail["word_band"]["checked"]
    )
    redlines_checked = bool(
        redline_detail is not None and redline_detail["redlines"]["checked"]
    )
    result.checked = bool(ratio_detail["checked"] or word_band_checked or redlines_checked)
    return result
