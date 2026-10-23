"""content_sync 三仓边界回归测试（Task 6 / D-11）。

覆盖三条静默形态（每条都先红后绿 + 突变验证）：

1. **旧正文文件残留**：改标题 / 删章后，内容仓 ``novel/`` 里同一章**只允许有一个
   正文文件**（旧标题文件必须被清理）；重复 push **幂等**（不再产生删除副作用）。
2. **缺表整次中断**：缺 ``volumes`` / ``characters`` 表 ⇒ **不抛未捕获异常**，
   且 ``SyncResult.errors`` 非空（可观测降级 ⇒ CLI 退出码非 0）。
3. **快照永不回灌**：``story_state/<v>.json`` 在**同 v 但内容被原地改过**（运行面
   修好了、内容面还是旧的）时必须回灌。

内容仓一律在 pytest 临时目录（``tmp_path``）里造 —— **绝不**写真实内容仓
（``D:/zcodeproject/NovelOS-Content``）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.content_sync import ContentSyncService
from packages.core.content_sync.paths import manifest_path
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """临时 SQLite db（已应用全部迁移）。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


@pytest.fixture()
def content_dir(tmp_path: Path) -> Path:
    """**临时**内容仓根目录（绝不使用真实内容仓）。"""
    cd = tmp_path / "content-repo"
    cd.mkdir(parents=True, exist_ok=True)
    return cd


def _seed_project(
    db_path: Path,
    *,
    name: str = "边界测试书",
    chapters: list[tuple[int, str]] | None = None,
) -> tuple[str, dict[int, str]]:
    """种一个最小可工作 project（project + 章 + draft + story_state 链）。

    返回 ``(project_id, {章号: chapter_id})``。FK 关掉避免 commit 链炸
    （同步模块只读不受影响）。
    """
    chapters = chapters if chapters is not None else [(1, "旧标题")]
    now = now_iso()
    pid = new_id("prj")
    conn = get_connection(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO projects(project_id,name,genre,target_words,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (pid, name, "科幻", 100_000, "ACTIVE", now, now),
    )
    ids: dict[int, str] = {}
    for num, title in chapters:
        cid = new_id("ch")
        ids[num] = cid
        conn.execute(
            """
            INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
                visibility,who_knows,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (cid, pid, num, title, "{}", "COMMITTED", "VISIBLE", None, now, now),
        )
        conn.execute(
            """
            INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
                prompt_version,model_id,created_at)
            VALUES (?,?,?,?,?,NULL,NULL,?)
            """,
            (new_id("dr"), cid, 1, f"第{num}章正文", "writer", now),
        )
    # story_state 链（workflow / run / branch / delta / commit / story_states）
    wf_id = new_id("wf")
    wfr_id = new_id("wfr")
    br_id = new_id("br")
    dlt_id = new_id("dlt")
    cmt_id = new_id("cmt")
    conn.execute(
        "INSERT INTO workflows(workflow_id,name,version,definition_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (wf_id, "chapter-commit", "v1", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO workflow_runs(run_id,workflow_id,chapter_id,status,checkpoint_json,started_at)"
        " VALUES(?,?,?,?,?,?)",
        (wfr_id, wf_id, next(iter(ids.values())), "COMPLETED", "{}", now),
    )
    conn.execute(
        "INSERT INTO branches(branch_id,project_id,name,parent_branch_id,base_state_version,"
        "status,created_at) VALUES(?,?,?,?,?,?,?)",
        (br_id, pid, "main", None, 0, "ACTIVE", now),
    )
    conn.execute(
        """
        INSERT INTO state_deltas(delta_id,chapter_id,workflow_run_id,previous_state_version,
            delta_version,schema_version,payload_json,status,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (dlt_id, next(iter(ids.values())), wfr_id, 0, 1, "state-delta-v0", "{}", "applied", "writer", now),
    )
    conn.execute(
        """
        INSERT INTO commits(commit_id,project_id,branch_id,chapter_id,previous_state_version,
            resulting_state_version,delta_id,validation_json,author_approval_json,timestamp,
            workflow_run_id,rollback_of)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (cmt_id, pid, br_id, next(iter(ids.values())), 0, 1, dlt_id, "{}", "{}", now, wfr_id, None),
    )
    conn.execute(
        "INSERT INTO story_states(project_id,state_version,snapshot_json,commit_id,created_at)"
        " VALUES(?,?,?,?,?)",
        (pid, 1, '{"hp":100}', cmt_id, now),
    )
    conn.commit()
    conn.close()
    return pid, ids


def _book_dir(content_dir: Path, project_id: str) -> Path:
    """在内容仓里找到该 project 的书目录。"""
    for child in content_dir.iterdir():
        if not child.is_dir():
            continue
        mp = manifest_path(child)
        if not mp.exists():
            continue
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("project_id") == project_id:
            return child
    raise AssertionError(f"no book dir under {content_dir} for project {project_id}")


def _chapter_txts(book_dir: Path) -> list[str]:
    return sorted(p.name for p in (book_dir / "novel").glob("第*章*.txt"))


def _set_chapter_title(db_path: Path, chapter_id: str, title: str) -> None:
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            "UPDATE chapters SET title = ?, updated_at = ? WHERE chapter_id = ?",
            (title, now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_chapter(db_path: Path, chapter_id: str) -> None:
    conn = get_connection(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM drafts WHERE chapter_id = ?", (chapter_id,))
        conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (chapter_id,))
        conn.commit()
    finally:
        conn.close()


def _repair_story_state_in_place(db_path: Path, project_id: str, state_version: int, snapshot: str) -> None:
    """**同 state_version** 原地改写快照（运行面修好了、内容面还是旧的）。"""
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            "UPDATE story_states SET snapshot_json = ? WHERE project_id = ? AND state_version = ?",
            (snapshot, project_id, state_version),
        )
        conn.commit()
    finally:
        conn.close()


def _add_story_state(db_path: Path, project_id: str, version: int, snapshot: str) -> None:
    """追加一条 story_states 行（**新版本号**，可与上一版内容相同）。"""
    conn = get_connection(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        row = conn.execute(
            "SELECT commit_id FROM story_states WHERE project_id = ? ORDER BY state_version ASC LIMIT 1",
            (project_id,),
        ).fetchone()
        commit_id = row["commit_id"] if row else "cmt_unknown"
        conn.execute(
            "INSERT INTO story_states(project_id,state_version,snapshot_json,commit_id,created_at)"
            " VALUES (?,?,?,?,?)",
            (project_id, version, snapshot, commit_id, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. 旧正文文件残留
# ---------------------------------------------------------------------------


class TestStaleChapterFiles:
    def test_rename_leaves_exactly_one_chapter_file(self, db_path: Path, content_dir: Path) -> None:
        """改标题后：旧文件必须消失，只留新文件（每章只允许一个正文文件）。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "旧标题")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        assert _chapter_txts(book_dir) == ["第1章-旧标题.txt"]

        _set_chapter_title(db_path, ids[1], "新标题")
        svc.push_book(pid)

        assert _chapter_txts(book_dir) == ["第1章-新标题.txt"], (
            f"改标题后内容仓仍有旧正文文件：{_chapter_txts(book_dir)}"
        )

    def test_deleted_chapter_file_is_removed(self, db_path: Path, content_dir: Path) -> None:
        """删章后：该章的正文文件必须被清理。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "甲"), (2, "乙")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        assert _chapter_txts(book_dir) == ["第1章-甲.txt", "第2章-乙.txt"]

        _delete_chapter(db_path, ids[2])
        svc.push_book(pid)

        assert _chapter_txts(book_dir) == ["第1章-甲.txt"], (
            f"删章后残留正文文件：{_chapter_txts(book_dir)}"
        )

    def test_cleanup_is_idempotent(self, db_path: Path, content_dir: Path) -> None:
        """重复 push 不产生新的删除副作用（幂等）。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "旧标题")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, first = svc.push_book(pid)
        _set_chapter_title(db_path, ids[1], "新标题")
        svc.push_book(pid)
        before = sorted(p.name for p in (book_dir / "novel").glob("*.txt"))

        _, again = svc.push_book(pid)
        after = sorted(p.name for p in (book_dir / "novel").glob("*.txt"))

        assert before == after
        assert again.stale_files_removed == 0, "重复 push 不应再删除任何文件"
        assert again.stale_files == []
        assert again.errors == []

    def test_removed_file_names_are_recorded(self, db_path: Path, content_dir: Path) -> None:
        """清理是**破坏性**动作 ⇒ 被删文件名必须留痕（可审计）。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "旧标题")])
        svc = ContentSyncService(str(db_path), content_dir)
        svc.push_book(pid)
        _set_chapter_title(db_path, ids[1], "新标题")
        _, result = svc.push_book(pid)

        assert result.stale_files_removed == 1
        assert result.stale_files == ["第1章-旧标题.txt"]

    def test_other_chapter_files_untouched(self, db_path: Path, content_dir: Path) -> None:
        """清理只针对「第N章/第N卷」形态的文件，不误伤整书 txt / canon。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "甲"), (2, "乙")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        _set_chapter_title(db_path, ids[2], "乙改")
        svc.push_book(pid)

        novel_names = sorted(p.name for p in (book_dir / "novel").glob("*.txt"))
        assert any(n.startswith("全书-") for n in novel_names), "整书 txt 不应被清理"
        assert "第1章-甲.txt" in novel_names
        assert "第2章-乙改.txt" in novel_names


# ---------------------------------------------------------------------------
# 2. 缺表降级
# ---------------------------------------------------------------------------


class TestMissingTableDegradation:
    @pytest.mark.parametrize("table", ["volumes", "characters"])
    def test_missing_table_does_not_raise_and_reports_errors(
        self, db_path: Path, content_dir: Path, table: str
    ) -> None:
        """缺表 ⇒ 不抛未捕获异常 + errors 非空（可观测降级）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        conn = get_connection(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(f"DROP TABLE {table}")
            conn.commit()
        finally:
            conn.close()

        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, result = svc.push_book(pid)  # 不抛

        assert result.errors, "缺表时 errors 必须非空（否则降级不可观测）"
        assert any(table in e for e in result.errors), (
            f"errors 未点名缺失的表：{result.errors}"
        )
        # 降级后仍要把 manifest 写出来（内容仓不留「半成品无清单」）
        assert result.manifest_written is True
        assert manifest_path(book_dir).is_file()

    def test_missing_table_still_writes_chapter_txt(self, db_path: Path, content_dir: Path) -> None:
        """缺表只降级**相关**产物，其它产物照常写（不被整次中断）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        conn = get_connection(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE volumes")
            conn.commit()
        finally:
            conn.close()

        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, result = svc.push_book(pid)

        assert result.chapters_written == 1
        assert _chapter_txts(book_dir) == ["第1章-甲.txt"]
        assert result.errors


# ---------------------------------------------------------------------------
# 3. 同 v 快照回灌
# ---------------------------------------------------------------------------


class TestStoryStateRehydration:
    def test_same_version_repaired_snapshot_is_rehydrated(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """同 state_version、内容被原地改过 ⇒ 必须回灌内容仓。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)

        target = book_dir / "story_state" / "1.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["snapshot"] == {"hp": 100}

        _repair_story_state_in_place(db_path, pid, 1, '{"hp":7,"repaired":true}')
        _, result = svc.push_book(pid)

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["snapshot"] == {"hp": 7, "repaired": True}, (
            "同 v 但内容改过的快照没有回灌内容仓"
        )
        assert result.story_states_written == 1
        assert result.story_states_skipped == 0

    def test_unchanged_snapshot_is_skipped(self, db_path: Path, content_dir: Path) -> None:
        """内容未变 ⇒ 仍然跳过（幂等，不重复写文件）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        svc.push_book(pid)
        _, result = svc.push_book(pid)

        assert result.story_states_written == 0
        assert result.story_states_skipped == 1

    def test_rehydration_is_idempotent(self, db_path: Path, content_dir: Path) -> None:
        """回灌一次后再 push ⇒ 不再重复写（内容哈希相同 ⇒ 跳过）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        _repair_story_state_in_place(db_path, pid, 1, '{"hp":7}')
        svc.push_book(pid)
        _, third = svc.push_book(pid)

        assert third.story_states_written == 0
        assert third.story_states_skipped == 1
        payload = json.loads((book_dir / "story_state" / "1.json").read_text(encoding="utf-8"))
        assert payload["snapshot"] == {"hp": 7}


# ---------------------------------------------------------------------------
# 3b. 同内容快照合并（Task 2 副作用：台账写路径会物化同内容、只换版本号的快照行）
# ---------------------------------------------------------------------------


class TestSameContentSnapshotMerge:
    def test_next_version_with_same_content_is_not_written(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """与上一版本内容相同 ⇒ **不落盘**（内容仓不攒同内容快照文件）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        _add_story_state(db_path, pid, 2, '{"hp":100}')  # 与 v1 同内容
        svc = ContentSyncService(str(db_path), content_dir)

        book_dir, result = svc.push_book(pid)
        state_dir = book_dir / "story_state"

        assert (state_dir / "1.json").is_file()
        assert not (state_dir / "2.json").exists(), "同内容快照不得产生第二个文件"
        assert result.story_states_written == 1
        assert result.story_states_merged == 1

    def test_merge_records_alias_in_manifest(self, db_path: Path, content_dir: Path) -> None:
        """被合并的版本必须**留痕**（版本 → 它重复的那个版本），信息不丢。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        _add_story_state(db_path, pid, 2, '{"hp":100}')
        svc = ContentSyncService(str(db_path), content_dir)

        book_dir, _ = svc.push_book(pid)
        manifest = json.loads(manifest_path(book_dir).read_text(encoding="utf-8"))

        assert manifest["story_state_versions"] == [1]
        assert manifest["story_state_aliases"] == {"2": 1}

    def test_different_content_versions_both_exported(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """内容不同 ⇒ 两版都落盘（合并规则不得误伤）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        _add_story_state(db_path, pid, 2, '{"hp":90}')  # 与 v1 不同
        svc = ContentSyncService(str(db_path), content_dir)

        book_dir, result = svc.push_book(pid)
        state_dir = book_dir / "story_state"

        assert (state_dir / "1.json").is_file()
        assert (state_dir / "2.json").is_file()
        assert result.story_states_merged == 0
        manifest = json.loads(manifest_path(book_dir).read_text(encoding="utf-8"))
        assert manifest["story_state_versions"] == [1, 2]
        assert manifest["story_state_aliases"] == {}

    def test_merge_is_idempotent(self, db_path: Path, content_dir: Path) -> None:
        """重复 push 不得把被合并的版本补写出来（合并判定稳定）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        _add_story_state(db_path, pid, 2, '{"hp":100}')
        svc = ContentSyncService(str(db_path), content_dir)

        book_dir, first = svc.push_book(pid)
        _, second = svc.push_book(pid)
        state_dir = book_dir / "story_state"

        assert not (state_dir / "2.json").exists()
        assert second.story_states_written == 0
        assert second.story_states_skipped == 1
        assert second.story_states_merged == 1

    def test_merge_and_rehydration_coexist(self, db_path: Path, content_dir: Path) -> None:
        """两条规则同时成立：同 v 内容变了 ⇒ 回灌；不同 v 同内容 ⇒ 合并。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        _add_story_state(db_path, pid, 2, '{"hp":100}')  # 与 v1 同内容
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        state_dir = book_dir / "story_state"

        # ① 同 v 原地修复 ⇒ 回灌（1.json 内容被更新）
        _repair_story_state_in_place(db_path, pid, 1, '{"hp":7}')
        _, second = svc.push_book(pid)
        payload = json.loads((state_dir / "1.json").read_text(encoding="utf-8"))
        assert payload["snapshot"] == {"hp": 7}, "同 v 内容变了必须回灌"

        # ② v1 改了之后，v2（内容 hp:100）不再与 v1 重复 ⇒ 它自己落盘
        #    （合并判定的比较对象是**上一版本的内容**；上一版本变了，重复关系就不成立）
        assert second.story_states_written == 2, "v1 回灌 + v2 不再重复 ⇒ 两份都写"
        assert second.story_states_merged == 0
        assert (state_dir / "2.json").is_file()
        payload2 = json.loads((state_dir / "2.json").read_text(encoding="utf-8"))
        assert payload2["snapshot"] == {"hp": 100}
        assert payload2["state_version"] == 2


# ---------------------------------------------------------------------------
# 4. 旧正文清理收窄：**永不删除作者文件**（2026-10-23 返工 · concern 4）
# ---------------------------------------------------------------------------
#
# 改造前的失效形态（不是假想）：残留清理是**无条件** ``path.unlink()`` ⇒
# 「改标题 / 删章」时会把**作者手写**或**作者改过**的正文**物理删除**（不可逆）。
# 内容仓是**作者的编辑面** ⇒ 这属于数据损失。
#
# 现在的口径（两档）：
#   ① manifest 的 ``novel_files`` 记着「本工具写过的正文 + 当时的内容 sha256」；
#      残留文件既在凭证里、内容又没变 ⇒ **物理删除**；
#   ② 判定不了归属（不在凭证 / 凭证在但内容被改过 / 读不出）⇒ **移动**到
#      ``_quarantine/<slug>/``（**不删除**）+ manifest ``quarantined`` 留痕。


def _manifest_of(book_dir: Path) -> dict:
    return json.loads(manifest_path(book_dir).read_text(encoding="utf-8"))


def _quarantine_root(content_dir: Path, slug: str) -> Path:
    return content_dir / "_quarantine" / slug


class TestAuthorFilesAreNeverDeleted:
    def test_tool_written_file_is_still_deleted(self, db_path: Path, content_dir: Path) -> None:
        """反向不变量：**工具产出且未被改动**的旧文件**仍然被删**（不能什么都不做）。

        ⚠️ 这是收窄后的「正向档」：若这条不成立，收窄就变成了「永不清理」，
        同章留两份正文的原始缺陷（D-11 ①）会回来。
        """
        pid, ids = _seed_project(db_path, chapters=[(1, "旧标题")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        old_file = book_dir / "novel" / "第1章-旧标题.txt"
        assert old_file.is_file()

        _set_chapter_title(db_path, ids[1], "新标题")
        _, result = svc.push_book(pid)

        assert not old_file.exists(), "工具产出且未被改动的旧文件必须被清理"
        assert result.stale_files_removed == 1
        assert result.stale_files == ["第1章-旧标题.txt"]
        assert result.quarantined_files == [], "可证明归属的文件不该进隔离区"

    def test_manifest_records_ownership_of_written_files(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """manifest 必须记下「本工具写过哪些正文 + 当时的内容哈希」（归属凭证）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)

        manifest = _manifest_of(book_dir)
        owned = manifest["novel_files"]
        assert "第1章-甲.txt" in owned, owned
        assert isinstance(owned["第1章-甲.txt"], str) and owned["第1章-甲.txt"], owned
        # 凭证里的哈希 = 磁盘上的真实内容哈希（否则判定「有没有被改过」就不成立）
        import hashlib

        actual = hashlib.sha256(
            (book_dir / "novel" / "第1章-甲.txt").read_bytes()
        ).hexdigest()
        assert owned["第1章-甲.txt"] == actual

    def test_hand_written_file_is_never_deleted(self, db_path: Path, content_dir: Path) -> None:
        """**作者手写**的正文文件 ⇒ 绝不删除（进隔离区，内容逐字保留）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)

        # 作者在 novel/ 里手写一个「DB 里没有这一章」的正文
        authored = book_dir / "novel" / "第9章-手写.txt"
        authored.write_text("这是作者自己写的第九章。", encoding="utf-8")

        _, result = svc.push_book(pid)

        # ❌ 绝不能消失
        assert not (book_dir / "novel" / "第9章-手写.txt").exists()
        quarantined = _quarantine_root(content_dir, _manifest_of(book_dir)["slug"])
        moved = quarantined / "第9章-手写.txt"
        assert moved.is_file(), (
            f"作者手写文件必须被**移动**到隔离区（不是删除）：{list(quarantined.glob('*'))}"
        )
        assert moved.read_text(encoding="utf-8") == "这是作者自己写的第九章。"
        # 留痕
        assert result.quarantined_files, "隔离动作必须留痕"
        record = result.quarantined_files[0]
        assert record["file"] == "第9章-手写.txt"
        assert record["original_path"].endswith("第9章-手写.txt")
        assert record["reason"], "留痕必须写明原因"

    def test_author_edited_tool_file_is_not_deleted(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """**作者改过**的正文（工具写的，但内容被作者编辑）⇒ 不删，进隔离区。"""
        pid, ids = _seed_project(db_path, chapters=[(1, "旧标题")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)

        old_file = book_dir / "novel" / "第1章-旧标题.txt"
        old_file.write_text("作者在这一版上改了字。", encoding="utf-8")

        _set_chapter_title(db_path, ids[1], "新标题")
        _, result = svc.push_book(pid)

        assert result.stale_files_removed == 0, "作者改过的文件不得被物理删除"
        assert result.stale_files == []
        quarantined = _quarantine_root(content_dir, _manifest_of(book_dir)["slug"])
        moved = quarantined / "第1章-旧标题.txt"
        assert moved.is_file(), "作者改过的文件必须被移动到隔离区"
        assert moved.read_text(encoding="utf-8") == "作者在这一版上改了字。"
        assert result.quarantined_files[0]["file"] == "第1章-旧标题.txt"

    def test_quarantine_is_recorded_in_manifest(self, db_path: Path, content_dir: Path) -> None:
        """manifest 的 ``quarantined`` 段必须留痕（文件名 + 原路径 + 原因）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        (book_dir / "novel" / "第9章-手写.txt").write_text("作者手写", encoding="utf-8")

        svc.push_book(pid)

        manifest = _manifest_of(book_dir)
        records = manifest["quarantined"]
        assert len(records) == 1, records
        record = records[0]
        assert record["file"] == "第9章-手写.txt"
        assert record["original_path"]
        assert record["reason"]
        assert record["quarantine_path"]

    def test_quarantine_records_accumulate_across_pushes(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """隔离留痕**累积**（文件移走后后续 push 看不到它 ⇒ 不累积就丢记录）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        (book_dir / "novel" / "第9章-手写.txt").write_text("作者手写", encoding="utf-8")

        svc.push_book(pid)
        svc.push_book(pid)

        manifest = _manifest_of(book_dir)
        assert len(manifest["quarantined"]) == 1, manifest["quarantined"]

    def test_quarantine_is_idempotent(self, db_path: Path, content_dir: Path) -> None:
        """重复 push 不产生新的删除 / 隔离副作用（幂等）。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        (book_dir / "novel" / "第9章-手写.txt").write_text("作者手写", encoding="utf-8")

        svc.push_book(pid)
        _, again = svc.push_book(pid)

        assert again.stale_files_removed == 0
        assert again.stale_files == []
        assert again.quarantined_files == [], "重复 push 不得再隔离一次"

    def test_quarantine_dir_is_not_listed_as_a_book(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """隔离区**不是书** ⇒ ``list_books`` 不得把它列出来。"""
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        (book_dir / "novel" / "第9章-手写.txt").write_text("作者手写", encoding="utf-8")
        svc.push_book(pid)

        slugs = [b["slug"] for b in svc.list_books()]

        assert "_quarantine" not in slugs, slugs
        assert len(slugs) == 1, slugs

    def test_author_file_survives_the_whole_push(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """端到端不变量：**作者文件的字节在整个 push 之后仍然存在于内容仓**。

        ⚠️ 这是 concern 4 的**核心断言**：无论走删除档还是隔离档，
        作者写的内容**一个字节都不能丢**（要么在 ``novel/``，要么在隔离区）。
        """
        pid, _ = _seed_project(db_path, chapters=[(1, "甲")])
        svc = ContentSyncService(str(db_path), content_dir)
        book_dir, _ = svc.push_book(pid)
        authored = book_dir / "novel" / "第9章-手写.txt"
        payload = "作者自己写的正文，一个字都不能丢。"
        authored.write_text(payload, encoding="utf-8")

        svc.push_book(pid)

        slug = _manifest_of(book_dir)["slug"]
        survivors = [
            p
            for p in list((book_dir / "novel").glob("*.txt"))
            + list(_quarantine_root(content_dir, slug).glob("*.txt"))
            if p.read_text(encoding="utf-8") == payload
        ]
        assert len(survivors) == 1, f"作者文件丢失或重复：{survivors}"
