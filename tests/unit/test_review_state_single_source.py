"""审校状态查询的单一落点 + 「第 4 份不合并」的护栏（2026-11-08 批次三十八 · REF-04）。

## 背景

「最近一次 COMPLETED chapter-review」的 ``ended_at`` 与「阈值之后有没有更新草稿」此前各有
多份逐字相同的 SQL（control / domain service / commit）。批次三十八收敛到
``packages/domain/chapter/review_state.py``。

但 ``chapter_commit/gate.py::_reviewed_draft_version`` 是**另一套语义**：
它按 ``started_at DESC`` 取多行、再逐条解 checkpoint 取 ``draft_version``。
**它只复用谓词，不合并排序与产出**——合并会改变快通道下门禁取到的版本
（历史事故：导出物按 ``commits.draft_version`` 拿到旧稿）。

## 本文件钉住三件事

1. **结构单源**：那两段 SQL 文本在 ``packages/`` 里只允许出现在 ``review_state.py``
   （有人再内联一份 ⇒ 红）。这是「收敛」这个动作本身的可判别断言。
2. **行为一致**：三个调用点对同一样本返回同一个值（防止只留接口、各自算各自的）。
3. **第 4 份仍按 started_at 取候选**：构造 ``started_at`` 序与 ``ended_at`` 序**相反**的
   两条 review run，断言 gate 取的是 ``started_at`` 更晚那条的 ``draft_version``
   —— 这条专门防止「顺手把 gate 也并进 review_state」。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.chapter.review_state import last_review_ended_at
from packages.domain.chapter.service import ChapterService
from tests._db_fixtures import migrated_db

_ROOT = Path(__file__).resolve().parents[2]

_ENDED_AT_SQL = "SELECT wr.ended_at FROM workflow_runs wr"
_DRAFT_AFTER_SQL = "SELECT version, created_at FROM drafts"


def _python_sources() -> list[Path]:
    return [p for p in (_ROOT / "packages").rglob("*.py") if p.is_file()]


# ---------------------------------------------------------------------------
# 1. 结构单源
# ---------------------------------------------------------------------------


def test_review_sql_lives_only_in_review_state():
    """那两段 SQL 文本在 packages/ 里只允许出现在 review_state.py。"""
    allowed = "packages/domain/chapter/review_state.py"
    offenders: dict[str, list[str]] = {}
    for path in _python_sources():
        rel = path.relative_to(_ROOT).as_posix()
        text = path.read_text(encoding="utf-8-sig")
        for needle in (_ENDED_AT_SQL, _DRAFT_AFTER_SQL):
            if needle in text and rel != allowed:
                offenders.setdefault(rel, []).append(needle)
    assert not offenders, (
        "审校状态查询被重新内联（REF-04 回退）；应调用 "
        "packages/domain/chapter/review_state.py：\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in sorted(offenders.items()))
    )
    # 反向不变量：单点文件本身必须真的有这两段（否则上面那条是「全仓都没有」的假绿）
    single = (_ROOT / allowed).read_text(encoding="utf-8")
    assert _ENDED_AT_SQL in single
    assert _DRAFT_AFTER_SQL in single


# ---------------------------------------------------------------------------
# 2. 三个调用点行为一致
# ---------------------------------------------------------------------------


def _seed_completed_review(db_path: Path, *, started_at: str, ended_at: str, version: int) -> tuple[str, str]:
    pid, cid, now = new_id("prj"), new_id("ch"), now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, 'ref04', 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', 'DRAFTED', 'VISIBLE', ?, ?)",
            (cid, pid, now, now),
        )
        row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'chapter-review'"
        ).fetchone()
        if row is None:
            wf_id = new_id("wf")
            conn.execute(
                "INSERT INTO workflows (workflow_id, name, version, definition_json, "
                "created_at, updated_at) VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)",
                (wf_id, now, now),
            )
        else:
            wf_id = row["workflow_id"]
        checkpoint = {
            "author_review": {
                "__pause_payload__": {"review_report": {"draft_version": version}}
            }
        }
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "checkpoint_json, retry_count, started_at, ended_at) "
            "VALUES (?, ?, ?, 'COMPLETED', ?, 0, ?, ?)",
            (new_id("wfr"), wf_id, cid, json.dumps(checkpoint), started_at, ended_at),
        )
        conn.commit()
    finally:
        conn.close()
    return pid, cid


def test_three_call_sites_agree_on_the_same_sample(tmp_path):
    migrated_db(tmp_path)
    db_path = tmp_path / "novelos.db"
    _pid, cid = _seed_completed_review(
        db_path, started_at="2026-11-01T10:00:00+00:00", ended_at="2026-11-01T12:00:00+00:00", version=5
    )

    from packages.core.api.routers.workflows.control import _latest_completed_review_end

    service_value = ChapterService(str(db_path)).get_last_review_completed_at(cid)
    router_value = _latest_completed_review_end(str(db_path), cid)
    conn = get_connection(db_path)
    try:
        helper_value = last_review_ended_at(conn, cid)
    finally:
        conn.close()

    assert service_value == router_value == helper_value == "2026-11-01T12:00:00+00:00"


# ---------------------------------------------------------------------------
# 3. 第 4 份（gate）仍按 started_at 取候选 —— 不可合并的护栏
# ---------------------------------------------------------------------------


def test_gate_reviewed_version_still_orders_by_started_at(tmp_path):
    """构造 started_at 序与 ended_at 序**相反**的两条 review run。

    - run A：started 10:00 / ended 12:00 / draft_version=5
    - run B：started 11:00 / ended 11:30 / draft_version=6

    按 ``started_at DESC`` 取 ⇒ B（version 6）；
    若有人把它并进 ``review_state``（``ended_at DESC``）⇒ 会取到 A（version 5）⇒ 本用例红。
    """
    migrated_db(tmp_path)
    db_path = tmp_path / "novelos.db"
    _pid, cid = _seed_completed_review(
        db_path, started_at="2026-11-01T10:00:00+00:00", ended_at="2026-11-01T12:00:00+00:00", version=5
    )
    # 第二条来自同一条 seed：手工把它的 chapter 指到同一个 cid
    conn = get_connection(db_path)
    try:
        wf_id = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'chapter-review'"
        ).fetchone()["workflow_id"]
        checkpoint = {
            "author_review": {
                "__pause_payload__": {"review_report": {"draft_version": 6}}
            }
        }
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "checkpoint_json, retry_count, started_at, ended_at) "
            "VALUES (?, ?, ?, 'COMPLETED', ?, 0, ?, ?)",
            (
                new_id("wfr"),
                wf_id,
                cid,
                json.dumps(checkpoint),
                "2026-11-01T11:00:00+00:00",
                "2026-11-01T11:30:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from packages.workflows.chapter_commit.gate import _reviewed_draft_version

    assert _reviewed_draft_version(str(db_path), cid, {}) == 6, (
        "gate 的 reviewed-version 查询改用了 ended_at 序（REF-04 误合并第 4 份）"
    )
