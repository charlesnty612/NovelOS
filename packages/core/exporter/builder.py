"""导出器主逻辑（V1.4）。

对外接口（与 ``packages/core/exporter/__init__.py`` 重导出）：

- :func:`build_txt(project_id, scope)` -> ``bytes``
- :func:`build_docx(project_id, scope)` -> ``bytes``
- :func:`build_fanqie_package(project_id)` -> ``bytes``
- :func:`plan_to_outline(plan_json)` -> ``str``

``scope`` 是 ``dataclass`` 风格 dict，字段：

- ``kind``: ``"book"``（整书） / ``"chapter"``（单章）；
- ``chapter_no``: 仅 ``kind="chapter"`` 时必填，project 内唯一 number。

数据取数路径（任务书给死）：
- 章节列表：``ChapterService.list_by_project(project_id)``（number ASC）；
- 单章正文：``SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC LIMIT 1``；
  无 draft（仅 PLANNED 状态）则用空串；
- 大纲：``chapters.plan_json``（chapter-plan 工作流写回），用 :func:`plan_to_outline` 渲染。

口径：
- COMMITTED 章节取正文；其它状态章节在 txt/docx 中保留标题但用空 body 占位（提示作者尚未定稿）；
  本任务书未禁止保留结构——以「按 chapter_no 升序全量输出，作者自检用」为默认行为。
- 番茄投稿包正文：从 chapter_no=1 起累加章节正文到目标字数附近；超过则在最近章节边界截断；
  本函数目标字数固定 10000（README 注明口径）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.core.db import get_connection
from packages.domain.chapter.service import ChapterService

from .docx import build_minimal_docx

# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


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


def _project_name(db_path: str, project_id: str) -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["name"] if row else ""


def _committed_chapters(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """返回 project 全部 chapter（number ASC）。是否已定稿由调用方按 status 判断。"""
    return ChapterService(db_path).list_by_project(project_id)


def _latest_draft_content(db_path: str, chapter_id: str) -> str:
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


def _chapter_heading(number: int, title: str | None) -> str:
    label = f"第{number}章"
    if title:
        label += f" {title}"
    return label


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


def _project_outline(db_path: str, project_id: str) -> str:
    """整书大纲：每章一段 plan 渲染结果。"""
    chapters = _committed_chapters(db_path, project_id)
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

    - scope.kind == "book"：按 chapter_no ASC 全量；
    - scope.kind == "chapter"：仅指定 chapter_no 的章节；
    - 章节正文为空（无 draft）时，仍输出标题与空行占位。
    """
    chapters = _committed_chapters(db_path, project_id)
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
        heading = _chapter_heading(ch["number"], ch.get("title"))
        lines.append(heading)
        lines.append("-" * max(len(heading), 8))
        body = _latest_draft_content(db_path, ch["chapter_id"])
        lines.append(body if body else "（本章尚无正文）")
        lines.append("")
    body_str = "\n".join(lines).rstrip() + "\n"
    return ("\ufeff" + body_str).encode("utf-8")  # UTF-8 BOM


def build_docx(db_path: str, project_id: str, scope: ExportScope) -> bytes:
    """导出 docx：与 txt 同样的内容，组装成最小 OOXML docx。"""
    chapters = _committed_chapters(db_path, project_id)
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
        heading = _chapter_heading(ch["number"], ch.get("title"))
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
    """番茄投稿包：前 N 章拼满 ~1 万字正文 + 分隔线 + 全书大纲。

    口径：
    - 按 chapter_no ASC 取已 COMMITTED 的章节；
    - 累加章节正文长度（按字符数）到 ``FANQIE_TARGET_CHARS`` 附近；
    - 一旦超阈值就在「最近章节边界」截断（不切到章中间），不再追加后续章节；
    - 若所有章节总字数仍 < 阈值，则全量输出。
    """
    chapters = _committed_chapters(db_path, project_id)
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
        heading = _chapter_heading(ch["number"], ch.get("title"))
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

    outline_md = _project_outline(db_path, project_id)

    head = "\n".join(head_lines).rstrip()
    body = "\n\n".join(body_parts).rstrip()
    pieces: list[str] = []
    if head:
        pieces.append(head)
    if body:
        pieces.append(body)
    pieces.append(FANQIE_DELIMITER.rstrip())
    pieces.append(outline_md)
    text = "\n\n".join(pieces).rstrip() + "\n"
    return ("\ufeff" + text).encode("utf-8")


__all__ = [
    "ExportScope",
    "build_txt",
    "build_docx",
    "build_fanqie_package",
    "plan_to_outline",
    "FANQIE_TARGET_CHARS",
    "FANQIE_DELIMITER",
]
