"""exporter 外发取稿口径回归测试（Task 6 / M-13 / D-10）。

最终口径（Lead 裁决，2026-10-23；权威列 = 迁移 ``0034_commits_draft_version.sql``
新增的 ``commits.draft_version``，含义 = **本次 commit 所定的那一版草稿**）：

取稿规则**只对「外发面」= ``build_fanqie_package``** 生效；``build_txt`` /
``build_docx`` / ``content_sync`` 的口径**逐字不变**（仍是取最新草稿 —— 作者 WIP 面）。

::

    若 该章 chapters.status == 'COMMITTED':
        v = SELECT draft_version FROM commits
            WHERE chapter_id = ? AND draft_version IS NOT NULL
            ORDER BY timestamp DESC, commit_id DESC LIMIT 1
        若 v 命中 且 drafts 里存在 (chapter_id, version=v) ⇒ 取该版正文
        否则 ⇒ 兜底：取最新草稿（存量行为逐字不变）
    否则（章非 COMMITTED，WIP/返修中）:
        取最新草稿（章节头照旧带【未定稿】标记）

**存量零退化**是硬要求：存量 ``commits`` 行 ``draft_version`` 恒 NULL（迁移**不
回填**，回填即造假）⇒ 一律走兜底分支 ⇒ 行为与改造前逐字一致。

⚠️ **不做**的两件事（各有理由，均为显式决策）：

- 不按 ``commits.timestamp`` 回溯推版本号 —— 那是在**猜**「提交时刻的最新稿就是
  被定的那一版」；「审完又改稿」的时序下会把**没被审过的那一版**认成定稿版。
- 不解析 checkpoint 里的 ``quality_reviewed_draft_version`` —— checkpoint 是
  **run 级**的，与 commits 行不是一一对应 ⇒ 语义比 ``commits.draft_version`` 弱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.db import get_connection
from packages.core.exporter import ExportScope, build_fanqie_package, build_txt
from packages.core.ids import new_id, now_iso
from tests._db_fixtures import migrated_db

_T1 = "2026-01-01T00:00:00+00:00"
_T2 = "2026-02-01T00:00:00+00:00"
_T_MID = "2026-01-15T00:00:00+00:00"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return migrated_db(tmp_path)


def _seed_project(db_path: Path, name: str = "外发测试书") -> str:
    now = now_iso()
    pid = new_id("prj")
    conn = get_connection(str(db_path))
    conn.execute(
        """
        INSERT INTO projects(project_id,name,genre,target_words,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (pid, name, "科幻", 100_000, "ACTIVE", now, now),
    )
    conn.commit()
    conn.close()
    return pid


def _seed_chapter(
    db_path: Path,
    project_id: str,
    number: int = 1,
    title: str = "第一章",
    status: str = "COMMITTED",
) -> str:
    now = now_iso()
    cid = new_id("ch")
    conn = get_connection(str(db_path))
    conn.execute(
        """
        INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
            visibility,who_knows,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (cid, project_id, number, title, "{}", status, "VISIBLE", None, now, now),
    )
    conn.commit()
    conn.close()
    return cid


def _seed_draft(db_path: Path, chapter_id: str, version: int, content: str, created_at: str) -> None:
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
                prompt_version,model_id,created_at)
            VALUES (?,?,?,?,?,NULL,NULL,?)
            """,
            (new_id("dr"), chapter_id, version, content, "writer", created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_commit(
    db_path: Path,
    project_id: str,
    chapter_id: str,
    *,
    timestamp: str,
    draft_version: int | None,
    commit_id: str | None = None,
) -> str:
    """种一条提交链（workflows / workflow_runs / branches / state_deltas / commits）。

    ``draft_version`` = 本次 commit 所定的那一版草稿（迁移 0034 新列）；``None``
    = 存量形态（未记录）。
    """
    now = now_iso()
    wf_id = new_id("wf")
    wfr_id = new_id("wfr")
    br_id = new_id("br")
    dlt_id = new_id("dlt")
    cmt_id = commit_id or new_id("cmt")
    conn = get_connection(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO workflows(workflow_id,name,version,definition_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (wf_id, "chapter-commit", "v1", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO workflow_runs(run_id,workflow_id,chapter_id,status,checkpoint_json,started_at)"
        " VALUES(?,?,?,?,?,?)",
        (wfr_id, wf_id, chapter_id, "COMPLETED", "{}", now),
    )
    conn.execute(
        "INSERT INTO branches(branch_id,project_id,name,parent_branch_id,base_state_version,"
        "status,created_at) VALUES(?,?,?,?,?,?,?)",
        (br_id, project_id, "main", None, 0, "ACTIVE", now),
    )
    conn.execute(
        """
        INSERT INTO state_deltas(delta_id,chapter_id,workflow_run_id,previous_state_version,
            delta_version,schema_version,payload_json,status,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (dlt_id, chapter_id, wfr_id, 0, 1, "state-delta-v0", "{}", "applied", "writer", now),
    )
    conn.execute(
        """
        INSERT INTO commits(commit_id,project_id,branch_id,chapter_id,previous_state_version,
            resulting_state_version,delta_id,validation_json,author_approval_json,timestamp,
            workflow_run_id,rollback_of,draft_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cmt_id,
            project_id,
            br_id,
            chapter_id,
            0,
            1,
            dlt_id,
            "{}",
            "{}",
            timestamp,
            wfr_id,
            None,
            draft_version,
        ),
    )
    conn.commit()
    conn.close()
    return cmt_id


# ---------------------------------------------------------------------------
# 1. 有 draft_version 记录 ⇒ 取定稿那一版
# ---------------------------------------------------------------------------


class TestCommittedDraftVersion:
    def test_uses_committed_draft_version_not_latest_draft(self, db_path: Path) -> None:
        """commits.draft_version=1 ⇒ 外发正文必须是 v1，不能是更新的 v2。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "已定稿正文", _T1)
        _seed_draft(db_path, cid, 2, "未过审新稿正文", _T2)
        _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=1)

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

        assert "已定稿正文" in text, "外发包应包含定稿那一版"
        assert "未过审新稿正文" not in text, "外发包不得包含定稿之后的新稿"

    def test_picks_latest_commit_when_chapter_committed_twice(self, db_path: Path) -> None:
        """返修后二次提交 ⇒ 取**最近一次**提交记录的那一版（timestamp DESC）。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "第一版正文", _T1)
        _seed_draft(db_path, cid, 2, "返修后定稿正文", _T2)
        _seed_commit(
            db_path,
            pid,
            cid,
            timestamp=_T_MID,
            draft_version=1,
            commit_id="cmt_0000000000000000000000000a",
        )
        _seed_commit(
            db_path,
            pid,
            cid,
            timestamp="2026-03-01T00:00:00+00:00",
            draft_version=2,
            commit_id="cmt_0000000000000000000000000b",
        )

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

        assert "返修后定稿正文" in text
        assert "第一版正文" not in text


# ---------------------------------------------------------------------------
# 2. 兜底：draft_version 为 NULL / 指向不存在的版本 ⇒ 取最新（存量零退化）
# ---------------------------------------------------------------------------


class TestFallbackToLatestDraft:
    def test_null_draft_version_falls_back_to_latest(self, db_path: Path) -> None:
        """存量 commits 行（draft_version=NULL）⇒ 兜底取最新（行为与改造前一致）。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "v1 正文", _T1)
        _seed_draft(db_path, cid, 2, "v2 正文", _T2)
        _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=None)

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

        assert "v2 正文" in text, "draft_version 为 NULL 时必须兜底取最新草稿"
        assert "v1 正文" not in text

    def test_draft_version_pointing_to_missing_draft_falls_back_to_latest(
        self, db_path: Path
    ) -> None:
        """draft_version 指向不存在的草稿版本 ⇒ 兜底取最新（不得外发空正文）。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "v1 正文", _T1)
        _seed_draft(db_path, cid, 2, "v2 正文", _T2)
        _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=99)

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

        assert "v2 正文" in text
        assert "v1 正文" not in text

    def test_without_commit_record_takes_latest_draft(self, db_path: Path) -> None:
        """无提交记录 ⇒ 取最新草稿。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "唯一一版正文", _T1)

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

        assert "唯一一版正文" in text

    def test_null_draft_version_matches_pre_change_behaviour(self, db_path: Path) -> None:
        """存量零退化：draft_version=NULL 时，外发正文与「改造前取最新」逐字一致。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid)
        _seed_draft(db_path, cid, 1, "v1 正文", _T1)
        _seed_draft(db_path, cid, 2, "v2 正文", _T2)
        _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=None)

        text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")
        body = text.partition("========== 故事大纲 ==========")[0]

        assert "v2 正文" in body
        assert "v1 正文" not in body


# ---------------------------------------------------------------------------
# 3. 章非 COMMITTED ⇒ 走 WIP 面（取最新 + 【未定稿】标记）
# ---------------------------------------------------------------------------


class TestUncommittedChapterUnchanged:
    def test_uncommitted_chapter_keeps_marker_and_latest_draft(self, db_path: Path) -> None:
        """章非 COMMITTED（返修中）⇒ 不进外发面；WIP 面取最新并带【未定稿】标记。"""
        pid = _seed_project(db_path)
        cid = _seed_chapter(db_path, pid, 1, "返修中", status="REVIEWED")
        _seed_draft(db_path, cid, 1, "v1 正文", _T1)
        _seed_draft(db_path, cid, 2, "v2 正文", _T2)
        _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=1)

        txt = build_txt(str(db_path), pid, ExportScope(kind="book")).decode("utf-8-sig")
        assert "【未定稿】第1章 返修中" in txt
        assert "v2 正文" in txt
        assert "v1 正文" not in txt

        fanqie = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")
        assert "v1 正文" not in fanqie
        assert "v2 正文" not in fanqie


# ---------------------------------------------------------------------------
# 4. 作者 WIP 面（txt / docx）口径不变 —— 仍取最新草稿
# ---------------------------------------------------------------------------


def test_author_wip_face_still_takes_latest_draft(db_path: Path) -> None:
    """txt（作者 WIP 面）口径不变：取最新草稿（不受本任务影响）。"""
    pid = _seed_project(db_path)
    cid = _seed_chapter(db_path, pid)
    _seed_draft(db_path, cid, 1, "v1 正文", _T1)
    _seed_draft(db_path, cid, 2, "v2 正文", _T2)
    _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=1)

    text = build_txt(str(db_path), pid, ExportScope(kind="book")).decode("utf-8-sig")

    assert "v2 正文" in text
    assert "v1 正文" not in text


def test_committed_chapter_body_has_no_uncommitted_marker(db_path: Path) -> None:
    """COMMITTED 章的外发正文不得带【未定稿】标记（内容与标记不得自相矛盾）。"""
    pid = _seed_project(db_path)
    cid = _seed_chapter(db_path, pid)
    _seed_draft(db_path, cid, 1, "定稿正文", _T1)
    _seed_commit(db_path, pid, cid, timestamp=_T_MID, draft_version=1)

    text = build_fanqie_package(str(db_path), pid).decode("utf-8-sig")

    assert "【未定稿】" not in text
    assert "定稿正文" in text


def test_exporter_does_not_read_workflow_runs() -> None:
    """守门：exporter **不得**再去解析 run 级快照（语义弱于 commits.draft_version 列）。

    判别按 **SQL 形态**（不是按散文里的词）——docstring 里可以写「不读 workflow_runs」
    这句说明，但 SQL 里不得出现 ``FROM workflow_runs`` / ``checkpoint_json``。
    """
    import packages.core.exporter.builder as builder

    src = Path(builder.__file__).read_text(encoding="utf-8")
    for forbidden in ("FROM workflow_runs", "JOIN workflow_runs", "checkpoint_json"):
        assert forbidden not in src, (
            f"exporter 不得解析 run 级快照（口径以 commits.draft_version 为准）：{forbidden}"
        )
