"""番茄签约体检——服务层。

职责：
- 串联 DB 读数 + :func:`packages.core.signing_check.checks.run_checks`；
- 项目不存在抛 :class:`ValueError`（与其他 service 风格一致，由 router 转 404）；
- :func:`format_summary` 把结果渲染成可嵌入番茄投稿包的纯文本摘要；
- 题材库 P2：项目绑定题材包且 payload 声明 ``opening_rules`` 时，追加「题材开篇检查段」
  （逐条 pass/fail；失败为 info 级提示，report-only 不阻断）。

DB 取数说明（任务书要求**复制查询逻辑不 import 私有函数**）：
- 章节列表走 :class:`ChapterService.list_by_project`（按 number ASC）；
- 单章正文 SQL 与 ``packages/core/exporter/builder.py::_latest_draft_content``
  完全等价（``SELECT content FROM drafts WHERE chapter_id=? ORDER BY version DESC LIMIT 1``）；
- 主角名单 SQL：``SELECT name FROM characters WHERE project_id=? AND role='protagonist'``；
- 题材包绑定走 :class:`packages.core.genre.service.GenrePackService`（读侧接口，
  不复制 SQL；全容错——读失败视为未绑定，体检照常）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from packages.core.db import get_connection
from packages.core.genre.consumers import opening_rules as project_opening_rules
from packages.core.genre.service import GenrePackService
from packages.core.ids import now_iso
from packages.core.quality.wordcount import visible_chars
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService

from .checks import CheckItem, evaluate_genre_opening, run_checks

if TYPE_CHECKING:
    from pathlib import Path

# 与 builder._latest_draft_content 等价的 SQL 模板（避免依赖私有函数）。
_LATEST_DRAFT_SQL: str = (
    "SELECT content FROM drafts "
    "WHERE chapter_id = ? "
    "ORDER BY version DESC LIMIT 1"
)

# 主角查询 SQL（characters.role='protagonist'，枚举值已在 0001 迁移 CHECK 中声明）。
_PROTAG_SQL: str = (
    "SELECT name FROM characters "
    "WHERE project_id = ? AND role = 'protagonist' "
    "ORDER BY created_at ASC, character_id ASC"
)

# 章节过滤：非 PLANNED 即视为「有正文的章节」。COMMITTED/RELEASED 必含，
# DRAFTED/REVIEWED 含草稿也算（与 builder._committed_chapters 取全部章节对齐，
# 因为 builder 不做 status 过滤——正文是否有效由 draft 内容决定）。
_INCLUDED_STATUSES: frozenset[str] = frozenset({"DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"})


# ---------------------------------------------------------------------------
# DB 取数辅助
# ---------------------------------------------------------------------------


def _fetch_latest_draft_content(db_path: str, chapter_id: str) -> str:
    """按 chapter_id 取最新版本草稿正文；无草稿返回空串。

    SQL 来自 :mod:`packages.core.exporter.builder._latest_draft_content`（line 78-92）。
    复制而不 import 私有函数，按任务书要求做「跨包照搬」。
    """

    conn = get_connection(db_path)
    try:
        row = conn.execute(_LATEST_DRAFT_SQL, (chapter_id,)).fetchone()
    finally:
        conn.close()
    return row["content"] if row else ""


def _fetch_protagonist_names(db_path: str, project_id: str) -> list[str]:
    """取项目全部主角名（按 created_at ASC，去重保持顺序）。"""

    conn = get_connection(db_path)
    try:
        rows = conn.execute(_PROTAG_SQL, (project_id,)).fetchall()
    finally:
        conn.close()
    seen: set[str] = set()
    names: list[str] = []
    for r in rows:
        name = r["name"]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _filter_chapters(chapters: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤 status ∈ {DRAFTED, REVIEWED, COMMITTED, RELEASED}；保留 number/title。"""

    return [
        {"chapter_id": ch.get("chapter_id"), "number": ch.get("number"), "title": ch.get("title")}
        for ch in chapters
        if (ch.get("status") or "") in _INCLUDED_STATUSES
    ]


# ---------------------------------------------------------------------------
# 题材开篇检查段（题材库 P2）
# ---------------------------------------------------------------------------


def _load_project_opening_rules(db_path: str, project_id: str) -> dict[str, Any] | None:
    """读项目绑定题材包的 ``opening_rules``（题材库 P2）。

    返回 ``{"pack_id", "pack_name", "rules": [...]}``；以下情形 → ``None``（整段缺席，
    与无题材包行为零差异）：
    - 未绑定题材包 / 绑定行已被删除；
    - payload 未声明 ``opening_rules`` 或声明为空 / 全为非法条目；
    - 读库异常（含迁移未跑的极老库无 ``genre_packs`` 表）——体检绝不因题材包读失败而失败。
    """

    try:
        binding = GenrePackService(db_path).get_project_binding(project_id)
    except Exception:  # noqa: BLE001 —— 题材包读失败不阻断体检
        return None
    if not binding or not binding.get("bound"):
        return None
    pack = binding.get("pack") or {}
    payload = pack.get("payload") or {}
    rules = project_opening_rules(payload)
    if not rules:
        return None
    return {
        "pack_id": binding.get("pack_id"),
        "pack_name": pack.get("name") or "",
        "rules": rules,
    }


def _genre_opening_section(
    genre: dict[str, Any], chapters_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    """题材开篇检查段（结构化）：逐条规则的 pass / fail 判定 + 汇总计数。"""

    verdicts = evaluate_genre_opening(chapters_payload, genre.get("rules") or [])
    counts = {
        "pass_count": sum(1 for v in verdicts if v.get("status") == "pass"),
        "fail_count": sum(1 for v in verdicts if v.get("status") == "fail"),
        "info_count": sum(
            1 for v in verdicts if v.get("status") in ("unverifiable", "not_written")
        ),
    }
    return {
        "pack_id": genre.get("pack_id"),
        "pack_name": genre.get("pack_name") or "",
        "rule_count": len(verdicts),
        "rules": verdicts,
        "pass_count": counts["pass_count"],
        "fail_count": counts["fail_count"],
        "info_count": counts["info_count"],
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_signing_check(db_path: str | "Path", project_id: str) -> dict[str, Any]:
    """执行一次签约体检，返回结构化结果 dict。

    返回结构：
        ``{
            "project_id": str,
            "project_name": str,
            "total_chars": int,
            "generated_at": str (ISO-8601),
            "items": list[dict],       # 每条 CheckItem 的 dict 形式
            "summary": {
                "pass_count": int, "warn_count": int,
                "fail_count": int, "info_count": int,
            },
        }``

    绑定题材包且 payload 声明 ``opening_rules`` 时额外含 ``genre_opening`` 段
    （``{pack_id, pack_name, rule_count, rules[], pass_count, fail_count, info_count}``；
    每条规则 ``status ∈ pass / fail / unverifiable / not_written``）。未绑定 / 无声明 →
    该键缺席（零行为变化）。

    异常：
        ``ValueError`` — project 不存在（router 转 404）。
    """

    db_path = str(db_path)

    # 项目存在性：仿照其他 service 的做法（ProjectService.get 返回 None 即抛 ValueError）。
    proj = ProjectService(db_path).get(project_id)
    if proj is None:
        raise ValueError(f"project {project_id!r} not found")

    chapters_raw = ChapterService(db_path).list_by_project(project_id)
    chapters_in_scope = _filter_chapters(chapters_raw)

    chapters_payload: list[dict[str, Any]] = []
    total_chars = 0
    for ch in chapters_in_scope:
        cid = ch.get("chapter_id")
        text = _fetch_latest_draft_content(db_path, cid) if cid else ""
        chapters_payload.append({"number": ch.get("number"), "text": text})
        # 总字数按非空白字符计，与 checks._count_chars 对齐（V3.7：复用 wordcount.visible_chars）
        total_chars += visible_chars(text)

    protagonist_names = _fetch_protagonist_names(db_path, project_id)

    # 题材库 P2：题材开篇规则（未绑定 / 未声明 → None，行为与题材库前一致）。
    genre_opening = _load_project_opening_rules(db_path, project_id)

    items: list[CheckItem] = run_checks(
        chapters_payload,
        protagonist_names,
        opening_rules=(genre_opening or {}).get("rules"),
    )

    summary = {
        "pass_count": sum(1 for it in items if it.level == "pass"),
        "warn_count": sum(1 for it in items if it.level == "warn"),
        "fail_count": sum(1 for it in items if it.level == "fail"),
        "info_count": sum(1 for it in items if it.level == "info"),
    }

    result: dict[str, Any] = {
        "project_id": project_id,
        "project_name": proj.get("name") or "",
        "total_chars": total_chars,
        "generated_at": now_iso(),
        "items": [
            {"key": it.key, "level": it.level, "detail": it.detail, "advice": it.advice}
            for it in items
        ],
        "summary": summary,
    }
    if genre_opening is not None:
        result["genre_opening"] = _genre_opening_section(genre_opening, chapters_payload)
    return result


# ---------------------------------------------------------------------------
# 摘要渲染（导出包嵌入用）
# ---------------------------------------------------------------------------


def format_summary(result: dict[str, Any]) -> str:
    """把 :func:`run_signing_check` 结果渲染为纯文本摘要。

    格式：
        ===== 签约体检摘要 =====
        通过 X / 警告 Y / 失败 Z / 信息 W
        [pass] key：detail
        [warn] key：detail
        ...

    供 :func:`packages.core.exporter.builder.build_fanqie_package` 末尾追加。
    """

    items = result.get("items") or []
    summary = result.get("summary") or {}
    lines: list[str] = ["===== 签约体检摘要 ====="]
    counts = {
        "pass": summary.get("pass_count", 0),
        "warn": summary.get("warn_count", 0),
        "fail": summary.get("fail_count", 0),
        "info": summary.get("info_count", 0),
    }
    lines.append(
        f"通过 {counts['pass']} / 警告 {counts['warn']} / 失败 {counts['fail']} / 信息 {counts['info']}"
    )
    for it in items:
        lines.append(f"[{it.get('level', 'info')}] {it.get('key', '')}：{it.get('detail', '')}")
    return "\n".join(lines) + "\n"


__all__ = ["run_signing_check", "format_summary"]
