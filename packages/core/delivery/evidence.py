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
   ⚠️ **项目级「不可检」项不再被丢弃**（2026-10-23 返工 · concern 2）：
   「题材开篇检查不可检」（``genre_opening_unavailable``）摊到**在范围章（1~3）**
   ⇒ 判定层能区分「**没检**」与「检过且通过」。

设计要点：

- **不复制判定逻辑**：本层只产出 :mod:`packages.core.delivery.verdict` 的数据类；
  「怎么判」全在纯函数里。
- **fail-soft 但留痕**：任一证据源读失败（异常），不抛 500，而是把该源标成
  ``reachable=False`` 并把异常文本写进 ``detail``；受影响章节落
  ``not_evaluated`` 理由（绝不静默当通过）。
- **「读不到」≠「没有」**（Task 1 的 :class:`DraftResolutionError` 机制）：
  正文读不到（草稿表不可用）逐章记 ``length_unavailable_reason``，
  与「该章没有草稿」分开；两者在判定侧都是 ``not_evaluated``，但**理由文本不同**。
- **不复用私有函数**：``signing_check`` / ``draft_resolver`` / ``wordcount`` /
  ``target_words`` 全是公开接口。
- **「不可检」识别口径单点**：判定「某项是不是不可检」由**拥有该概念的模块**
  提供（``signing_check.checks.is_genre_opening_unavailable_item``），
  本层与判定层都 import 它，**不复制字符串、不另造检测**。
"""

from __future__ import annotations

import re
from typing import Any, Final

from packages.core.genre.target_words import resolve_target_word_count
from packages.core.logging_config import get_logger
from packages.core.quality.service import QualityService
from packages.core.quality.wordcount import classify_prose_length, resolve_band_config
from packages.core.signing_check.checks import is_genre_opening_unavailable_item
from packages.core.signing_check.service import run_signing_check
from packages.domain.chapter.draft_resolver import DraftResolutionError, resolve_draft
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
    "（不适用，不是「量不出来」）；项目级项（echo_words / signing_window）不摊到单章，"
    "但项目级**不可检**标记（题材开篇检查没跑）摊到第 1~3 章"
    "（否则「没检」与「检过且通过」在交付判定里分不开）"
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


def _signing_unavailable_detail(result: dict[str, Any]) -> str:
    """「题材开篇检查**不可检**」的人读说明（来自不可检条目本身）。

    取不到条目时退回顶层的 ``genre_opening_error``；两者皆无 ⇒ 空串
    （= 本次体检没有「不可检」这回事）。
    """
    for item in result.get("items") or []:
        if is_genre_opening_unavailable_item(item):
            detail = str(item.get("detail") or "")
            if detail:
                return detail
    if result.get("genre_opening_unavailable"):
        return str(result.get("genre_opening_error") or "题材开篇检查不可检")
    return ""


def signing_items_by_chapter(result: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """把 ``run_signing_check`` 的 items 按章号摊开（只保留 1~3 章）。

    返回 ``{章号: [{key, level, detail, advice}]}``。

    ⚠️ **项目级项的处理（2026-10-23 返工 · concern 2）**：

    - 「题材开篇检查**不可检**」项（``genre_opening_unavailable``）是**项目级事实**
      （无章号）——此前在此**被静默丢弃** ⇒ 交付判定里「**没检**」与「检过且通过」
      **不可区分**（「不可检」项是 ``info`` 级，而 ``info`` 级不进判定理由）。
      ⇒ 现在把它**摊到在范围章（1~3）**：题材开篇检查按设计只对黄金三章生效，
      且判定层据此产出 ``not_evaluated`` 理由 ⇒ **阻止 ``deliverable``**。
    - 其余项目级项（``echo_words`` / ``signing_window`` / ``empty``）**仍不摊到单章**
      （它们是全文统计口径，不是某章的门槛）——行为不变。
    """
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    genre_chapters = _genre_opening_item_chapters(result)
    for item in result.get("items") or []:
        key = str(item.get("key") or "")
        numbers: tuple[int, ...] = ()
        if is_genre_opening_unavailable_item(item):
            # 项目级「不可检」标记 ⇒ 摊到在范围章（黄金三章口径）。
            numbers = tuple(range(1, SIGNING_SCOPE_MAX_CHAPTER + 1))
        elif key in _SIGNING_FIXED_CHAPTERS:
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
) -> tuple[
    dict[str, LengthEvidence], dict[str, int | None], dict[str, str], str | None
]:
    """量各章字数（最新草稿 vs 目标带）。

    返回 ``(map, 草稿版本, 不可检原因, 错误文本)``：

    - ``map``：量到字数的章 → :class:`LengthEvidence`；
    - ``草稿版本``：各章解析到的草稿版本（读不到 ⇒ 无该键）；
    - ``不可检原因``：``{chapter_id: 原因}``——**正文读不到**；
    - ``错误文本``：整源级失败（其它异常）时的说明。

    ⚠️ **「读不到」与「没有草稿」必须分开**（Task 1 已落地的
    :class:`packages.domain.chapter.draft_resolver.DraftResolutionError` 机制；
    2026-10-23 返工 · concern 2 —— Task 1 需求 3(b) 把这一处点名交给本任务）：

    - **没有草稿**（正常业务状态）→ 该章**不进 map** → 判定侧落 ``not_evaluated``
      （既有语义，逐字不变）；
    - **读不到**（故障：草稿表不可用 / 查询异常）→ 该章落 ``不可检原因``
      → 判定侧产出**带失败原因**的 ``not_evaluated`` 理由。

    此前两者被**同一个 blanket ``except Exception``** 折叠 ⇒ 故障原因只剩一句
    「字数测量失败：…」挂在**证据源**上，章级理由的 ``evidence`` 是 ``None``
    ⇒ 作者在章级看不到「草稿表不可用」这个真因。
    """
    out: dict[str, LengthEvidence] = {}
    versions: dict[str, int | None] = {}
    unavailable: dict[str, str] = {}
    try:
        for chapter in chapters:
            chapter_id = str(chapter.get("chapter_id") or "")
            if not chapter_id:
                continue
            try:
                draft = resolve_draft(db_path, chapter_id)
            except DraftResolutionError as exc:
                # 正文**读不到**（草稿表不可用 / 查询异常）——**不是**「该章没有草稿」。
                reason = f"正文读不到（草稿表不可用）：{exc}"
                unavailable[chapter_id] = reason
                log.warning("delivery: draft unavailable for %s: %s", chapter_id, exc)
                continue
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
        return {}, {}, unavailable, f"字数测量失败：{exc}"
    return out, versions, unavailable, None


def _signing_items_map(
    db_path: str, project_id: str
) -> tuple[dict[int, list[dict[str, Any]]], str | None, str]:
    """跑一次签约体检并摊到章。

    返回 ``(按章摊好的 items, 整源错误文本, 「不可检」说明)``：

    - 整源错误：体检器本身跑不起来（异常）；
    - 「不可检」说明：体检跑起来了，但**题材开篇那一段没跑**（读题材包失败）。
      ⚠️ 两者**不是一回事**：前者是「体检没跑」，后者是「体检跑了但有一段没跑」——
      交付判定层据此产出**不同的** ``not_evaluated`` 理由。
    """
    try:
        result = run_signing_check(db_path, project_id)
    except Exception as exc:  # noqa: BLE001 —— 体检器坏掉不等于章节不合格
        log.warning("delivery: signing check unavailable: %s", exc)
        return {}, f"签约体检不可得：{exc}", ""
    return signing_items_by_chapter(result), None, _signing_unavailable_detail(result)


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
    length_map, draft_versions, length_unavailable, length_error = _length_evidence_map(
        db_path, project_id, chapters, band_cfg
    )
    signing_map, signing_error, signing_unavailable = _signing_items_map(db_path, project_id)

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
                length_unavailable_reason=length_unavailable.get(chapter_id),
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
            "reachable": length_error is None and not length_unavailable,
            "detail": length_error
            or (
                f"{len(length_map)} 章量到正文字数；目标字数走 plan_json.expected_word_count"
                " → 绑定题材包 → 默认 3000；带覆盖走 projects.word_band_json"
                + (
                    f"；{len(length_unavailable)} 章正文**读不到**（草稿表不可用）"
                    if length_unavailable
                    else ""
                )
            ),
        },
        {
            "key": "signing_check",
            "reachable": signing_error is None and not signing_unavailable,
            "detail": signing_error
            or signing_unavailable
            or SIGNING_SCOPE_NOTE,
        },
    ]

    return ProjectEvidence(
        project_id=project_id,
        project_name=str(project.get("name") or ""),
        chapters=tuple(evidence_chapters),
        sources=tuple(sources),
        signing_scope_note=SIGNING_SCOPE_NOTE,
    )
