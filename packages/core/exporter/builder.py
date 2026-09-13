"""导出器主逻辑（V1.4）。

对外接口（与 ``packages/core/exporter/__init__.py`` 重导出）：

- :func:`build_txt(project_id, scope)` -> ``bytes``
- :func:`build_docx(project_id, scope)` -> ``bytes``
- :func:`build_fanqie_package(project_id)` -> ``bytes``
- :func:`plan_to_outline(plan_json)` -> ``str``
- :func:`project_name` / :func:`latest_draft_content` / :func:`chapter_heading`
  -> 章节渲染 helper（V3.9 批次 5.14 由私有名下划线版提升；旧名保留别名，
  ``content_sync.build_volume_txt`` 复用同一实现）

``scope`` 是 ``dataclass`` 风格 dict，字段：

- ``kind``: ``"book"``（整书） / ``"chapter"``（单章）；
- ``chapter_no``: 仅 ``kind="chapter"`` 时必填，project 内唯一 number。

数据取数路径（任务书给死）：
- 章节列表：``ChapterService.list_by_project(project_id)``（number ASC）；
- 单章正文：``SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC LIMIT 1``；
  无 draft（仅 PLANNED 状态）则用空串；
- 大纲：``chapters.plan_json``（chapter-plan 工作流写回），用 :func:`plan_to_outline` 渲染。

口径（V3.9 批次 4.1 裁决落地：此前实现 / 文档 / m2_judge 三方分裂，此处为唯一裁决）：
- txt / docx = **作者 WIP 面**：按 chapter_no ASC 输出项目全部章节（含 PLANNED / DRAFTED /
  REVIEWED）；非 COMMITTED 章节标题加 ``【未定稿】`` 前缀标注，无 draft 时用空 body 占位。
- 番茄投稿包 = **外发面**：正文与故事大纲都只取 ``status='COMMITTED'`` 的章节，
  与 README / m2_judge 的 COMMITTED-only 口径一致。
- 番茄投稿包正文：从 chapter_no=1 起累加章节正文到目标字数附近；超过则在最近章节边界截断；
  本函数目标字数固定 10000（README 注明口径）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.domain.chapter.service import ChapterService

from .docx import build_minimal_docx

# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

log = get_logger("novelos.exporter.builder")


@dataclass(frozen=True)
class ExportScope:
    """导出范围。

    ``kind`` 为 ``"book"`` 时忽略 ``chapter_no``；
    ``kind`` 为 ``"chapter"`` 时 ``chapter_no`` 必填。
    """

    kind: str  # "book" | "chapter"
    chapter_no: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def project_name(db_path: str, project_id: str) -> str:
    """项目名（缺行 → 空串）。公开 API（V3.9 批次 5.14）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["name"] if row else ""


UNCOMMITTED_MARKER = "【未定稿】"
"""非 COMMITTED 章节在作者 WIP 面（txt / docx）的标题前缀（V3.9 批次 4.1 裁决）。"""


def _project_chapters(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """返回 project 全部 chapter（number ASC）；是否定稿由调用方按 status 判断。

    前身 ``_committed_chapters`` 名不副实——返回全部章节、不做任何 status 过滤，
    与本模块文档 / ``m2_judge`` 的 COMMITTED-only 声称三方分裂（V3.9 批次 4.1）。
    改名后口径外显：过滤与否是调用方（产物面）的决定——txt / docx 保留全部并标注
    ``【未定稿】``，番茄投稿包过滤 COMMITTED。
    """
    return ChapterService(db_path).list_by_project(project_id)


def _is_committed(chapter: Mapping[str, Any]) -> bool:
    """chapter 行是否已定稿（``status == 'COMMITTED'``）。"""
    return (chapter.get("status") or "").strip().upper() == "COMMITTED"


def _export_heading(chapter: Mapping[str, Any]) -> str:
    """作者 WIP 面章节标题：非 COMMITTED 章节加 ``【未定稿】`` 前缀标注。"""
    heading = _chapter_heading(chapter["number"], chapter.get("title"))
    return heading if _is_committed(chapter) else UNCOMMITTED_MARKER + heading


def latest_draft_content(db_path: str, chapter_id: str) -> str:
    """该章最新 draft（``version DESC``）正文；无 draft → 空串。公开 API（V3.9 批次 5.14）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["content"] if row else ""


def chapter_heading(number: int, title: str | None) -> str:
    """章节标题行（``第N章 标题``；无标题 → ``第N章``）。公开 API（V3.9 批次 5.14）。"""
    label = f"第{number}章"
    if title:
        label += f" {title}"
    return label


# 兼容别名（V3.9 批次 5.14）：三个 helper 曾是私有名，``content_sync`` 等模块
# 跨模块引用下划线名属实现细节泄漏，故提升为公开 API；旧下划线名保留为别名，
# 任何遗留引用（含本模块内部的旧调用点）行为零变化。
# 本模块内部沿用别名调用：``build_txt`` / ``build_docx`` / ``build_fanqie_package``
# 里有同名局部变量 ``project_name``（赋值即声明为局部），改调公开名会 UnboundLocalError；
# 与其改局部变量名，不如保持别名调用——公开名与别名绑定同一函数对象，无行为差异。
_project_name = project_name
_latest_draft_content = latest_draft_content
_chapter_heading = chapter_heading


def plan_to_outline(plan_json: Mapping[str, Any] | None) -> str:
    """把 chapter-plan 的 plan_json dict 渲染成可读大纲文本。

    容错：plan_json 为 None / 非 dict / 缺字段时输出"（暂无）"，方便单章无 plan
    时整书大纲仍可生成。
    """
    if not plan_json:
        return "（暂无）"
    goal = plan_json.get("chapter_goal")
    conflict = plan_json.get("core_conflict")
    turning = plan_json.get("turning_point")
    role = plan_json.get("expected_role")
    beats = plan_json.get("key_beats") or []

    lines: list[str] = []
    if goal:
        lines.append(f"目标：{goal}")
    if conflict:
        lines.append(f"冲突：{conflict}")
    if turning:
        lines.append(f"转折：{turning}")
    if role:
        lines.append(f"功能：{role}")
    if isinstance(beats, list) and beats:
        lines.append("节拍：")
        for i, b in enumerate(beats, 1):
            if not isinstance(b, dict):
                continue
            purpose = b.get("purpose") or ""
            lines.append(f"  {i}. {purpose}")
    return "\n".join(lines) if lines else "（暂无）"


def _project_outline(chapters: list[dict[str, Any]]) -> str:
    """整书大纲：每章一段 plan 渲染结果。

    章节列表由调用方给出（V3.9 批次 4.1：番茄包只传 COMMITTED 章节——外发面口径），
    本函数不做 status 过滤。
    """
    if not chapters:
        return "（暂无）"
    parts: list[str] = []
    for ch in chapters:
        title = _chapter_heading(ch["number"], ch.get("title"))
        body = plan_to_outline(ch.get("plan_json") or {})
        parts.append(f"{title}\n{body}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_txt(db_path: str, project_id: str, scope: ExportScope) -> bytes:
    """导出 txt：utf-8-sig 编码；每章标题 + 正文段落。

    - scope.kind == "book"：按 chapter_no ASC 全量（作者 WIP 面，未定稿章节保留并标注）；
    - scope.kind == "chapter"：仅指定 chapter_no 的章节；
    - 非 COMMITTED 章节标题加 ``【未定稿】`` 前缀；
    - 章节正文为空（无 draft）时，仍输出标题与空行占位。
    """
    chapters = _project_chapters(db_path, project_id)
    if scope.kind == "chapter":
        if scope.chapter_no is None:
            raise ValueError("chapter scope requires chapter_no")
        chapters = [c for c in chapters if c["number"] == scope.chapter_no]
    elif scope.kind != "book":
        raise ValueError(f"unsupported scope kind: {scope.kind!r}")

    lines: list[str] = []
    project_name = _project_name(db_path, project_id)
    if project_name:
        lines.append(project_name)
        lines.append("=" * max(len(project_name), 8))
        lines.append("")
    for ch in chapters:
        heading = _export_heading(ch)
        lines.append(heading)
        lines.append("-" * max(len(heading), 8))
        body = _latest_draft_content(db_path, ch["chapter_id"])
        lines.append(body if body else "（本章尚无正文）")
        lines.append("")
    body_str = "\n".join(lines).rstrip() + "\n"
    return ("\ufeff" + body_str).encode("utf-8")  # UTF-8 BOM


def build_docx(db_path: str, project_id: str, scope: ExportScope) -> bytes:
    """导出 docx：与 txt 同样的内容，组装成最小 OOXML docx。

    口径同 :func:`build_txt`：作者 WIP 面，全部章节；非 COMMITTED 章节标题加
    ``【未定稿】`` 前缀。
    """
    chapters = _project_chapters(db_path, project_id)
    if scope.kind == "chapter":
        if scope.chapter_no is None:
            raise ValueError("chapter scope requires chapter_no")
        chapters = [c for c in chapters if c["number"] == scope.chapter_no]
    elif scope.kind != "book":
        raise ValueError(f"unsupported scope kind: {scope.kind!r}")

    project_name = _project_name(db_path, project_id)
    title = project_name or "NovelOS Export"

    paragraphs: list[tuple[str, str]] = []  # (style, text)
    if project_name:
        paragraphs.append(("Heading1", project_name))
    for ch in chapters:
        heading = _export_heading(ch)
        paragraphs.append(("Heading2", heading))
        body = _latest_draft_content(db_path, ch["chapter_id"])
        if not body:
            paragraphs.append(("Normal", "（本章尚无正文）"))
            continue
        for para in body.split("\n"):
            text = para.rstrip()
            if not text:
                continue
            paragraphs.append(("Normal", text))
    return build_minimal_docx(title=title, paragraphs=paragraphs)


# 番茄投稿包目标字数（README 注明：超过则截断，无则全量）
FANQIE_TARGET_CHARS = 10_000
FANQIE_DELIMITER = "\n\n========== 故事大纲 ==========\n\n"


def build_fanqie_package(db_path: str, project_id: str) -> bytes:
    """番茄投稿包：前 N 章拼满 ~1 万字正文 + 分隔线 + 全书大纲（外发面）。

    口径（V3.9 批次 4.1 裁决）：
    - 只取 ``status='COMMITTED'`` 的章节——正文与故事大纲都过滤，与 README /
      ``scripts/m2_judge.py`` 的 COMMITTED-only 口径一致；
    - 非 COMMITTED 章节不出现（作者 WIP 标注 ``【未定稿】`` 只属于 txt/docx 面）；
    - 按 chapter_no ASC 累加章节正文长度（按字符数）到 ``FANQIE_TARGET_CHARS`` 附近；
    - 一旦超阈值就在「最近章节边界」截断（不切到章中间），不再追加后续章节；
    - 若所有章节总字数仍 < 阈值，则全量输出。
    """
    chapters = [ch for ch in _project_chapters(db_path, project_id) if _is_committed(ch)]
    project_name = _project_name(db_path, project_id)

    head_lines: list[str] = []
    if project_name:
        head_lines.append(project_name)
        head_lines.append("=" * max(len(project_name), 8))
        head_lines.append("")

    body_parts: list[str] = []
    truncated = False
    running = 0
    included_chapters: list[dict[str, Any]] = []
    for ch in chapters:
        content = _latest_draft_content(db_path, ch["chapter_id"])
        heading = _export_heading(ch)
        block = heading + "\n" + "-" * max(len(heading), 8) + "\n" + (content or "（本章尚无正文）")
        new_running = running + len(content or "")
        if not truncated and running >= FANQIE_TARGET_CHARS:
            truncated = True
        if truncated:
            break
        body_parts.append(block)
        included_chapters.append(ch)
        running = new_running
        if running >= FANQIE_TARGET_CHARS:
            truncated = True
            break

    outline_md = _project_outline(chapters)

    head = "\n".join(head_lines).rstrip()
    body = "\n\n".join(body_parts).rstrip()
    pieces: list[str] = []
    if head:
        pieces.append(head)
    if body:
        pieces.append(body)
    pieces.append(FANQIE_DELIMITER.rstrip())
    pieces.append(outline_md)
    # V2.0 Wave D：番茄签约体检摘要（最小侵入拼接；异常时回退纯空摘要，
    # 不阻断主导出流）。服务层读 DB 与 checks 同源，确保摘要与端点一致。
    signing_summary = ""
    try:
        from packages.core.signing_check.service import (
            format_summary,
            run_signing_check,
        )

        signing_summary = format_summary(run_signing_check(db_path, project_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("signing check summary skipped: %s", exc)
    if signing_summary:
        pieces.append(signing_summary.rstrip())
    text = "\n\n".join(pieces).rstrip() + "\n"
    return ("\ufeff" + text).encode("utf-8")


__all__ = [
    "ExportScope",
    "UNCOMMITTED_MARKER",
    "build_txt",
    "build_docx",
    "build_fanqie_package",
    "plan_to_outline",
    "FANQIE_TARGET_CHARS",
    "FANQIE_DELIMITER",
]
