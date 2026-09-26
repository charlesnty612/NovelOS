"""P1-1：「不管谁读草稿，读的必须是被审那一版」——形状级回归。

事故（2026-09-18 实证，review run ``wfr_adf71afbb7d9`` / 章 ``ch_92bac068ff0d``）：
同一份评审报告里 ``word_count=2248, draft_version=13``，而题材核销栏报「实际字数
713（length_report）」——713 是 v11 的可见字数（作者手改出 v12 / v13 后，写稿 run
的 ``length_report`` 仍停在 v11）。根因是**形状**而非某一处：任何按 chapter_id
取「该章草稿」的读者，在「手里已有一版被审对象」时都会与它分叉。

本文件钉三件事（都不针对单一站点）：

1. 共享解析单点 :func:`packages.domain.chapter.draft_resolver.resolve_draft` 的口径
   （显式版本 / ``None`` = 最新 / 排序依据是 ``version`` 而非 ``created_at``）；
2. **整份评审报告内部自洽**：报告里所有「字数」测量值必须等于被审那一版的字数、
   所有「版本」标签必须等于被审那一版——用递归遍历动态收集，**新加的测量点漏改
   也会被抓住**（针对站点写的断言抓不到新站点）；
3. 质量报告（``quality_reports.draft_version``）的标签与其实际量过的正文同源。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import QualityService, build_quality_context
from packages.domain.chapter.draft_resolver import resolve_draft
from packages.workflows.chapter_review.pipeline import _basic_checks_node
from tests.unit.neutral_prose import neutral_prose

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 题材包：字数带 2400~3600。事故形状里旧版 713 出带、新版 2500 在带内——
# 一旦哪一处又量了旧版，报告会立刻多出 GENRE-WORD-BAND-DEVIATION /
# W-LEN-DEVIATION 并带上 713，断言随之失败。
_PACK_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "pacing": {"chapter_word_band": {"low": 2400, "high": 3600}},
}

_STALE_CHARS = 713      # 写稿 run 落 v11 时的实测字数（length_report 的归属版本）
_STALE_VERSION = 11
_MID_CHARS = 1674       # 作者手改 v12（复审指定的那一版用得上）
_MID_VERSION = 12
_REVIEWED_CHARS = 2500  # 作者手改 v13（最新版；带内）
_REVIEWED_VERSION = 13


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(
    db_path: Path,
    cid: str,
    version: int,
    content: str,
    *,
    created_at: str | None = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'human', ?)",
            (new_id("drf"), cid, version, content, created_at or now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_write_run(
    db_path: Path,
    cid: str,
    *,
    length_report: dict,
    draft_version: int,
) -> None:
    """写稿 run：checkpoint 里带 ``length_report`` 与它的归属版本。"""
    run_id, workflow_id, now = new_id("wfr"), new_id("wf"), now_iso()
    checkpoint = {"length_report": length_report, "draft_version": draft_version}
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-write', 'v1', '{}', ?, ?)",
            (workflow_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, 'COMPLETED', 'save_draft', ?, ?, ?)",
            (run_id, workflow_id, cid, json.dumps(checkpoint, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _bind_pack(db_path: Path, pid: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_p11', 'P1-1 包', '测试', 1, "
            "?, NULL, ?, ?)",
            (json.dumps(_PACK_PAYLOAD, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_p11' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()


def _incident_chapter(db_path: Path) -> str:
    """事故形状：写稿 run 写了 v11（length_report 713），作者随后手改出 v12 / v13。"""
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        length_report={"visible_chars": _STALE_CHARS},
        draft_version=_STALE_VERSION,
    )
    _insert_draft(db_path, cid, _STALE_VERSION, neutral_prose(_STALE_CHARS))
    _insert_draft(db_path, cid, _MID_VERSION, neutral_prose(_MID_CHARS))
    _insert_draft(db_path, cid, _REVIEWED_VERSION, neutral_prose(_REVIEWED_CHARS))
    return cid


# ---------------------------------------------------------------------------
# 1) 共享解析单点的口径
# ---------------------------------------------------------------------------


def test_resolver_none_takes_latest_draft(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "一")
    _insert_draft(db_path, cid, 2, "二")

    row = resolve_draft(db_path, cid)
    assert row is not None
    assert row["version"] == 2
    assert row["content"] == "二"


def test_resolver_explicit_version_wins_over_latest(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "一")
    _insert_draft(db_path, cid, 2, "二")

    row = resolve_draft(db_path, cid, 1)
    assert row is not None
    assert (row["version"], row["content"]) == (1, "一")


def test_resolver_orders_by_version_not_created_at(tmp_path: Path):
    """``None`` = 最新一版，按 ``version`` 发布序——墙钟戳倒挂时也不改口径。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 2, "二", created_at="2026-01-01T00:00:00+00:00")
    _insert_draft(db_path, cid, 1, "一", created_at="2026-09-18T00:00:00+00:00")

    row = resolve_draft(db_path, cid)
    assert row is not None
    assert row["version"] == 2


def test_resolver_returns_none_for_missing_or_invalid_targets(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    # 该章尚无草稿
    assert resolve_draft(db_path, cid) is None
    _insert_draft(db_path, cid, 1, "一")
    # 指定版本不存在
    assert resolve_draft(db_path, cid, 99) is None
    # 版本号不是正整数（含 bool / 0 / 负数 / 非数字）
    for bad in (True, 0, -1, "x"):
        assert resolve_draft(db_path, cid, bad) is None  # type: ignore[arg-type]
    # 章节不存在
    assert resolve_draft(db_path, "ch_missing") is None


# ---------------------------------------------------------------------------
# 2) 整份评审报告内部自洽（形状级）
# ---------------------------------------------------------------------------


_CHARS_KEYS = frozenset({"word_count", "visible_chars"})
_VERSION_KEYS = frozenset({"draft_version", "reviewed_draft_version"})


def _collect_by_key(node: Any, keys: frozenset[str], path: str = "") -> list[tuple[str, Any]]:
    """递归收集报告里所有 ``keys`` 命名的值（含嵌套 dict / list）。

    动态遍历而非逐站点断言：新增测量点若用了别的版本/别的字数，这里会直接看到
    多出来一个不一致的值。
    """
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key in keys:
                found.append((child, value))
            found.extend(_collect_by_key(value, keys, child))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            found.extend(_collect_by_key(item, keys, f"{path}[{idx}]"))
    return found


def test_review_report_measures_one_and_the_same_draft(tmp_path: Path):
    """手改出新版后评审：报告里**每一处**字数 / 版本都必须指向被审那一版。

    （对照 ``tests/unit/test_chapter_review_genre_check.py`` 里针对具体站点的用例，
    本用例遍历整份报告：将来新增的测量点若又用了写稿 run 的旧 ``length_report``，
    或另按 chapter_id 取草稿，都会在这里现形。）
    """
    db_path = _fresh_db(tmp_path)
    cid = _incident_chapter(db_path)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": _REVIEWED_CHARS}
    )["review_report"]

    chars = _collect_by_key(rep, _CHARS_KEYS)
    versions = _collect_by_key(rep, _VERSION_KEYS)

    # 报告里至少要有两处字数测量（顶层 word_count + 题材字数带）与两处版本标签，
    # 否则下面的「全都一致」是空断言。
    assert {p for p, _ in chars} >= {"word_count"}
    assert len(chars) >= 2, chars
    assert len(versions) >= 2, versions
    assert {v for _, v in chars} == {_REVIEWED_CHARS}, chars
    assert {v for _, v in versions} == {_REVIEWED_VERSION}, versions

    # 旧版字数 / 旧版归属只允许出现在「显式登记为陈旧来源」的位置
    assert rep["genre_check"]["redline_check"]["word_band"][
        "length_report_origin_version"
    ] == _STALE_VERSION
    assert rep["genre_check"]["redline_check"]["word_band"]["source"] == "draft"
    assert rep["warnings"] == []
    assert rep["errors"] == []


def test_review_report_follows_explicit_reviewed_version(tmp_path: Path):
    """指定复审旧版（v12）：整份报告只出现那一版——既不是最新的 v13，也不是
    ``length_report`` 归属的 v11（形状同上，方向相反）。"""
    db_path = _fresh_db(tmp_path)
    cid = _incident_chapter(db_path)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": _REVIEWED_CHARS,
         "draft_version": _MID_VERSION}
    )["review_report"]

    chars = _collect_by_key(rep, _CHARS_KEYS)
    versions = _collect_by_key(rep, _VERSION_KEYS)
    assert len(chars) >= 2, chars
    assert len(versions) >= 2, versions
    assert {v for _, v in chars} == {_MID_CHARS}, chars
    assert {v for _, v in versions} == {_MID_VERSION}, versions
    assert rep["genre_check"]["redline_check"]["word_band"]["source"] == "draft"


# ---------------------------------------------------------------------------
# 3) 质量报告：标签与它量过的那份正文同源
# ---------------------------------------------------------------------------


def test_quality_context_resolves_the_requested_draft_version(tmp_path: Path):
    """``build_quality_context(draft_version=...)`` 按指定版本取正文与版本号。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "旧版正文")
    _insert_draft(db_path, cid, 2, "新版正文")

    ctx = build_quality_context(
        db_path, project_id=pid, chapter_id=cid,
        delta=None, snapshot_pre=None, draft_version=1,
    )
    assert (ctx.draft_version, ctx.draft) == (1, "旧版正文")

    latest = build_quality_context(
        db_path, project_id=pid, chapter_id=cid, delta=None, snapshot_pre=None,
    )
    assert (latest.draft_version, latest.draft) == (2, "新版正文")


def test_quality_report_label_names_the_text_it_measured(tmp_path: Path):
    """评估与落库之间作者又改了一稿：报告仍须指到它真正量过的那一版。

    事故形状的镜像：标签（``quality_reports.draft_version``）若在落库时才独立查一次
    「最新版本」，标签就会指到另一份正文上。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "被评估的正文")

    ctx = build_quality_context(
        db_path, project_id=pid, chapter_id=cid, delta=None, snapshot_pre=None,
    )
    assert (ctx.draft_version, ctx.draft) == (1, "被评估的正文")

    # 作者在「评估」与「落库」之间手改出新版（这正是缺陷的触发条件）
    _insert_draft(db_path, cid, 2, "评估之后才出现的正文")

    report = QualityEngine().evaluate(ctx)
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)

    assert report.draft_version == 1
    stored = QualityService(db_path).latest_report(cid)
    assert stored is not None
    assert stored["draft_version"] == 1


def test_quality_report_without_draft_keeps_null_version(tmp_path: Path):
    """该章尚无草稿 → ctx 与落库报告的版本均为 None（列可空）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    ctx = build_quality_context(
        db_path, project_id=pid, chapter_id=cid, delta=None, snapshot_pre=None,
    )
    assert (ctx.draft_version, ctx.draft) == (None, "")

    report = QualityEngine().evaluate(ctx)
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)
    stored = QualityService(db_path).latest_report(cid)
    assert stored is not None
    assert stored["draft_version"] is None
