"""交付判定的证据取数层（delivery）：只读 DB、只组装证据，不做判定。

三个证据源（与 :mod:`packages.core.delivery.verdict` 的降级规则一一对应）：

1. **质量报告**（``quality_reports``）：取该章 ``created_at`` 最新一行，交给
   :class:`QualityService`（与 ``GET /chapters/{id}/quality`` 同口径）；``issues_json``
   的分档**不信任落库的 ``gate`` 字段**，由 ``verdict.issue_gate_rows`` 走
   :func:`quality.issues.issue_gate` 权威函数重推（本项目 111 份历史报告早于该字段，
   重推后仍能正确分档）。``_meta.gate_summary`` / ``_meta.gate_accepted_override``
   原样带出：前者是聚合摘要，后者是「作者显式接受了哪些 confirm 规则」的留痕。
2. **字数带**（``quality.wordcount`` + ``genre.target_words``）：正文取
   :func:`packages.domain.chapter.draft_resolver.resolve_draft`（共享解析单点，
   ``draft_version=None`` = 有意取最新，交付对象就是当前最新一版）；目标字数走
   :func:`packages.core.genre.target_words.resolve_target_word_count`（plan →
   题材包 → 3000），带覆盖走 ``projects.word_band_json`` + ``resolve_band_config``。
3. **签约体检**（``signing_check``，**仅黄金三章**）：整项目跑一次
   :func:`packages.core.signing_check.service.run_signing_check`，再把项目级 items
   按章号摊到单章。分摊规则与适用范围见 :func:`signing_items_by_chapter` 与
   :data:`SIGNING_SCOPE_NOTE`。

设计要点：

- **不复制判定逻辑**：本层只产出 :mod:`packages.core.delivery.verdict` 的数据类；
  「怎么判」全在纯函数里。
- **fail-soft 但留痕**：任一证据源读失败（异常），不抛 500，而是把该源标成
  ``reachable=False`` 并把异常文本写进 ``detail``；受影响章节落
  ``not_evaluated`` 理由（绝不静默当通过）。
- **不复用私有函数**：``signing_check`` / ``draft_resolver`` / ``wordcount`` /
  ``target_words`` 全是公开接口。
"""

from __future__ import annotations

import re
from typing import Any, Final

from packages.core.genre.target_words import resolve_target_word_count
from packages.core.logging_config import get_logger
from packages.core.quality.service import QualityService
from packages.core.quality.wordcount import classify_prose_length, resolve_band_config
from packages.core.signing_check.service import run_signing_check
from packages.domain.chapter.draft_resolver import resolve_draft
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService

from .verdict import (
    WRITTEN_STATUSES,
    ChapterEvidence,
    LengthEvidence,
    ProjectEvidence,
    QualityEvidence,
    SigningEvidence,
    classify_length_tier,
)

__all__ = [
    "SIGNING_SCOPE_MAX_CHAPTER",
    "SIGNING_SCOPE_NOTE",
    "signing_items_by_chapter",
    "collect_project_evidence",
]

log = get_logger("novelos.delivery.evidence")

#: 签约体检按黄金三章口径消费的章号上界（第 1~3 章）。
#: 依据：``signing_check.checks`` 的开篇 / 金手指 / 第三章小高潮三项只对 1~3 章成立；
#: ``chapter_hooks_ch{n}`` / ``chapter_length_ch{n}`` 虽然对**每一章**都出项，但那是
#: 「番茄建议（1500~2200 字最佳、章末留钩子）」，不是第 40 章的交付门槛——后者由质量
#: 报告与项目字数带承担。故 1~3 章之外标记 ``out_of_scope``（显式说明，不是静默跳过）。
SIGNING_SCOPE_MAX_CHAPTER: Final[int] = 3

SIGNING_SCOPE_NOTE: Final[str] = (
    "签约体检（黄金三章 / 签约窗口口径）只对第 1~3 章计入交付判定："
    "此范围内 fail 档降级为待办，warn 档仅展示，第 4 章及以后标记 out_of_scope"
    "（不适用，不是「量不出来」）；项目级项（echo_words / signing_window）不摊到单章。"
)

#: 固定绑到某几章的体检项（黄金三章三项中，前两章金手指是**窗口**规则 → 1、2 章都算）。
_SIGNING_FIXED_CHAPTERS: Final[dict[str, tuple[int, ...]]] = {
    "ch1_conflict_300": (1,),
    "ch1_protagonist_500": (1,),
    "ch2_golden_finger": (1, 2),
    "ch3_climax": (3,),
}
#: ``chapter_hooks_ch{n}`` / ``chapter_length_ch{n}`` → 第 n 章。
_SIGNING_CHAPTER_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:chapter_hooks|chapter_length)_ch(\d+)$"
)


def _genre_opening_item_chapters(result: dict[str, Any]) -> dict[str, int]:
    """``genre_opening_<check_id>`` → 规则声明的章号（窗口规则无章号 → 不映射）。"""
    section = result.get("genre_opening") or {}
    mapping: dict[str, int] = {}
    for rule in section.get("rules") or []:
        check_id = str(rule.get("check_id") or "")
        chapter_no = rule.get("chapter_no")
        if check_id and isinstance(chapter_no, int):
            mapping[f"genre_opening_{check_id}"] = chapter_no
    return mapping


def signing_items_by_chapter(result: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """把 ``run_signing_check`` 的 items 按章号摊开（只保留 1~3 章）。

    返回 ``{章号: [{key, level, detail, advice}]}``。项目级项（echo_words /
    signing_window / empty）不映射到任何章；抽不到章号的题材开篇规则同理。
    """
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    genre_chapters = _genre_opening_item_chapters(result)
    for item in result.get("items") or []:
        key = str(item.get("key") or "")
        numbers: tuple[int, ...] = ()
        if key in _SIGNING_FIXED_CHAPTERS:
            numbers = _SIGNING_FIXED_CHAPTERS[key]
        else:
            matched = _SIGNING_CHAPTER_ITEM_RE.match(key)
            if matched:
                numbers = (int(matched.group(1)),)
            else:
                genre_no = genre_chapters.get(key)
                if isinstance(genre_no, int):
                    numbers = (genre_no,)
        for number in numbers:
            if number <= SIGNING_SCOPE_MAX_CHAPTER:
                by_chapter.setdefault(number, []).append(
                    {
                        "key": key,
                        "level": str(item.get("level") or ""),
                        "detail": str(item.get("detail") or ""),
                        "advice": str(item.get("advice") or ""),
                    }
                )
    return by_chapter


def _quality_evidence_map(
    db_path: str, chapter_ids: list[str]
) -> tuple[dict[str, QualityEvidence], str | None]:
    """取各章最新质量报告；返回 ``(map, 错误文本或 None)``。"""
    service = QualityService(db_path)
    out: dict[str, QualityEvidence] = {}
    try:
        for chapter_id in chapter_ids:
            row = service.latest_report(chapter_id)
            if row is None:
                continue
            scores = row.get("scores_json") or {}
            meta = scores.get("_meta") if isinstance(scores, dict) else {}
            meta = meta if isinstance(meta, dict) else {}
            out[chapter_id] = QualityEvidence(
                report_id=str(row.get("report_id") or ""),
                overall=int(row.get("overall") or 0),
                evaluated_at=str(meta.get("evaluated_at") or row.get("created_at") or "") or None,
                draft_version=row.get("draft_version"),
                gate_summary=meta.get("gate_summary"),
                accepted_override=meta.get("gate_accepted_override"),
                issues=tuple(row.get("issues_json") or ()),
            )
    except Exception as exc:  # noqa: BLE001 —— 证据源读失败按「看不到」处理并留痕
        log.warning("delivery: quality report read failed: %s", exc)
        return {}, f"质量报告读取失败：{exc}"
    return out, None


def _length_evidence_map(
    db_path: str, project_id: str, chapters: list[dict[str, Any]], band_cfg: dict[str, Any]
) -> tuple[dict[str, LengthEvidence], dict[str, int | None], str | None]:
    """量各章字数（最新草稿 vs 目标带）；返回 ``(map, 草稿版本, 错误文本)``。

    无草稿 / 草稿为空 → 该章**不进 map**（判定侧落 ``not_evaluated``）：量不出字数的章
    不应被当成「字数合格」。
    """
    out: dict[str, LengthEvidence] = {}
    versions: dict[str, int | None] = {}
    try:
        for chapter in chapters:
            chapter_id = str(chapter.get("chapter_id") or "")
            if not chapter_id:
                continue
            draft = resolve_draft(db_path, chapter_id)
            versions[chapter_id] = (draft or {}).get("version")
            prose = (draft or {}).get("content") or ""
            if not prose.strip():
                continue
            plan = chapter.get("plan_json")
            plan_dict = plan if isinstance(plan, dict) else {}
            target = resolve_target_word_count(
                db_path, chapter_id, plan_dict, project_id=project_id
            )
            classified = classify_prose_length(prose, target, **band_cfg)
            out[chapter_id] = LengthEvidence(
                visible_chars=int(classified["visible_chars"]),
                target_word_count=int(classified["target"]),
                band_low=int(classified["band_low"]),
                band_high=int(classified["band_high"]),
                deviation_pct=float(classified["deviation_pct"]),
                status=str(classified["status"]),
                tier=classify_length_tier(classified, target),
            )
    except Exception as exc:  # noqa: BLE001 —— 同上，字数量不出来就报「未评估」
        log.warning("delivery: length measurement failed: %s", exc)
        return {}, {}, f"字数测量失败：{exc}"
    return out, versions, None


def _signing_items_map(
    db_path: str, project_id: str
) -> tuple[dict[int, list[dict[str, Any]]], str | None]:
    """跑一次签约体检并摊到章；失败 → ``({}, 错误文本)``。"""
    try:
        result = run_signing_check(db_path, project_id)
    except Exception as exc:  # noqa: BLE001 —— 体检器坏掉不等于章节不合格
        log.warning("delivery: signing check unavailable: %s", exc)
        return {}, f"签约体检不可得：{exc}"
    return signing_items_by_chapter(result), None


def collect_project_evidence(db_path: str, project_id: str) -> ProjectEvidence:
    """收集一个项目全部章节的证据（纯读；不做任何判定）。

    异常：``ValueError`` —— project 不存在（router 转 404）。
    """
    db_path = str(db_path)
    project = ProjectService(db_path).get(project_id)
    if project is None:
        raise ValueError(f"project {project_id!r} not found")

    chapters = ChapterService(db_path).list_by_project(project_id)
    chapter_ids = [str(c.get("chapter_id") or "") for c in chapters if c.get("chapter_id")]
    band_cfg = resolve_band_config(project.get("word_band"))

    quality_map, quality_error = _quality_evidence_map(db_path, chapter_ids)
    length_map, draft_versions, length_error = _length_evidence_map(
        db_path, project_id, chapters, band_cfg
    )
    signing_map, signing_error = _signing_items_map(db_path, project_id)

    evidence_chapters: list[ChapterEvidence] = []
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        number = int(chapter.get("number") or 0)
        status = str(chapter.get("status") or "")
        if number <= SIGNING_SCOPE_MAX_CHAPTER:
            signing = SigningEvidence(
                scope="unavailable" if signing_error else "in_scope",
                items=tuple(signing_map.get(number) or ()),
                note=signing_error or SIGNING_SCOPE_NOTE,
            )
        else:
            signing = SigningEvidence(scope="out_of_scope", items=(), note=SIGNING_SCOPE_NOTE)
        evidence_chapters.append(
            ChapterEvidence(
                chapter_id=chapter_id,
                number=number,
                title=str(chapter.get("title") or ""),
                status=status,
                draft_version=draft_versions.get(chapter_id),
                quality=quality_map.get(chapter_id),
                length=length_map.get(chapter_id),
                signing=signing,
            )
        )

    written = [c for c in evidence_chapters if c.status in WRITTEN_STATUSES]
    sources: list[dict[str, Any]] = [
        {
            "key": "quality_reports",
            "reachable": quality_error is None,
            "detail": quality_error
            or (
                f"已写章节 {len(written)} 章，其中 {sum(1 for c in written if c.quality)} 章"
                "有质量报告（取每章最新一份）"
            ),
        },
        {
            "key": "length_band",
            "reachable": length_error is None,
            "detail": length_error
            or (
                f"{len(length_map)} 章量到正文字数；目标字数走 plan_json.expected_word_count"
                " → 绑定题材包 → 默认 3000；带覆盖走 projects.word_band_json"
            ),
        },
        {
            "key": "signing_check",
            "reachable": signing_error is None,
            "detail": signing_error or SIGNING_SCOPE_NOTE,
        },
    ]

    return ProjectEvidence(
        project_id=project_id,
        project_name=str(project.get("name") or ""),
        chapters=tuple(evidence_chapters),
        sources=tuple(sources),
        signing_scope_note=SIGNING_SCOPE_NOTE,
    )
