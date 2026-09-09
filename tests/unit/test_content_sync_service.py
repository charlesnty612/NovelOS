"""content_sync.service 单元测试（V1.0）。

覆盖：

- ``ContentSyncService.push_book`` 全量同步：novel / canon / story_state /
  backup / manifest 五类产物齐全，文件名按约定；
- ``story_state`` 按版本号去重幂等：第二次 push 不再写 story_state JSON，
  ``story_states_skipped == 2``；
- ``push_book`` 多次运行 slug 稳定（同 project_id 复用同一 book_dir）；
- ``manifest.json`` 字段完整且合法（所有必需键存在、project_id 与源一致）；
- 不同 project 严格隔离：第二个 project 在内容仓下落到独立 slug 目录；
- ``init_book`` 仅建目录骨架与 manifest（不写其它产物）；
- ``list_books`` 返回内容仓下所有 book 子目录基础信息。

测试模式：

- 用 ``tmp_path`` 拉临时 SQLite（``Settings(data_dir=tmp_path)`` +
  ``apply_migrations``），不污染 ``data/novelos.db``；
- 用 :func:`packages.core.ids.new_id` 生成主键；
- 直接 ``INSERT`` 必要表行（含 workflow_run / branches / state_deltas / commits
  这条链，因为 story_states.commit_id NOT NULL 且 FK 到 commits）；
- 同步前 ``PRAGMA foreign_keys=OFF``（绕过 story_states 链上的 FK，
  同步模块只读不受影响）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.content_sync import ContentSyncService
from packages.core.content_sync.paths import (
    BOOK_DIRS,
    Manifest,
    manifest_path,
)
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


def _seed_minimal_project(db_path: Path, name: str = "测试书 A") -> str:
    """种一个最小可工作 project：projects + volume + 2 章 + 2 draft + 角色 +
    世界规则 + plot_event + timeline_event + 2 个 story_state。

    返回 project_id。FK 关掉避免 commit 链爆炸（同步模块只读不受影响）。
    """
    now = now_iso()
    pid = new_id("prj")
    cid1 = new_id("ch")
    cid2 = new_id("ch")
    vol_id = new_id("vol")
    char_id = new_id("char")
    wr_id = new_id("wrule")
    ev_id = new_id("event")
    tle_id = new_id("tle")
    wf_id = new_id("wf")
    wfr_id = new_id("wfr")
    br_id = new_id("br")
    dlt_id = new_id("dlt")
    cmt_id = new_id("cmt")
    conn = get_connection(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO projects(project_id,name,genre,target_words,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (pid, name, "科幻", 100_000, "ACTIVE", now, now),
    )
    conn.execute(
        """
        INSERT INTO volumes(volume_id,project_id,number,title,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (vol_id, pid, 1, "卷一", "sealed", now, now),
    )
    for cid, num, title, content in [
        (cid1, 1, "第一章", "这是第一章的正文。"),
        (cid2, 2, "第二章", "这是第二章的正文。"),
    ]:
        conn.execute(
            """
            INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
                visibility,who_knows,volume_id,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (cid, pid, num, title, "{}", "COMMITTED", "VISIBLE", None, vol_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
                prompt_version,model_id,created_at)
            VALUES (?,?,?,?,?,NULL,NULL,?)
            """,
            (new_id("dr"), cid, 1, content, "writer", now),
        )
    conn.execute(
        """
        INSERT INTO characters(character_id,project_id,name,role,core_json,visibility,
            created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (char_id, pid, "张三", "protagonist", '{"age":20}', "VISIBLE", now, now),
    )
    conn.execute(
        """
        INSERT INTO world_rules(world_rule_id,project_id,name,statement,data_json,
            visibility,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (wr_id, pid, "魔法", "魔法规则说明", '{"level":3}', "VISIBLE", now, now),
    )
    conn.execute(
        """
        INSERT INTO plot_events(event_id,project_id,type,cause_json,effects_json,
            participants_json,location_id,time_json,status,description,visibility)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ev_id, pid, "encounter", "{}", "{}", '["张三"]', None, "{}", "planned", "事件描述", "VISIBLE"),
    )
    conn.execute(
        """
        INSERT INTO timeline_events(timeline_event_id,project_id,event_id,day_index,
            description,visibility)
        VALUES (?,?,?,?,?,?)
        """,
        (tle_id, pid, ev_id, 1, "时间线描述", "VISIBLE"),
    )
    # story_state chain
    conn.execute(
        "INSERT INTO workflows(workflow_id,name,version,definition_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (wf_id, "chapter-commit", "v1", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO workflow_runs(run_id,workflow_id,chapter_id,status,checkpoint_json,started_at) VALUES(?,?,?,?,?,?)",
        (wfr_id, wf_id, cid1, "COMPLETED", "{}", now),
    )
    conn.execute(
        "INSERT INTO branches(branch_id,project_id,name,parent_branch_id,base_state_version,status,created_at) VALUES(?,?,?,?,?,?,?)",
        (br_id, pid, "main", None, 0, "ACTIVE", now),
    )
    conn.execute(
        """
        INSERT INTO state_deltas(delta_id,chapter_id,workflow_run_id,previous_state_version,
            delta_version,schema_version,payload_json,status,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (dlt_id, cid1, wfr_id, 0, 1, "state-delta-v0", "{}", "applied", "writer", now),
    )
    conn.execute(
        """
        INSERT INTO commits(commit_id,project_id,branch_id,chapter_id,previous_state_version,
            resulting_state_version,delta_id,validation_json,author_approval_json,timestamp,
            workflow_run_id,rollback_of)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (cmt_id, pid, br_id, cid1, 0, 1, dlt_id, "{}", "{}", now, wfr_id, None),
    )
    conn.execute(
        "INSERT INTO story_states(project_id,state_version,snapshot_json,commit_id,created_at) VALUES(?,?,?,?,?)",
        (pid, 1, '{"hp":100}', cmt_id, now),
    )
    conn.execute(
        "INSERT INTO story_states(project_id,state_version,snapshot_json,commit_id,created_at) VALUES(?,?,?,?,?)",
        (pid, 2, '{"hp":90}', cmt_id, now),
    )
    conn.commit()
    conn.close()
    return pid


def _build_content_dir(tmp_path: Path) -> Path:
    cd = tmp_path / "content"
    cd.mkdir(parents=True, exist_ok=True)
    return cd


# ---------------------------------------------------------------------------
# push 全量同步
# ---------------------------------------------------------------------------


class TestPushBook:
    def test_push_full_produces_all_artifacts(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        book_dir, result = svc.push_book(pid)

        # 子目录齐全
        for sub in BOOK_DIRS:
            assert (book_dir / sub).is_dir(), f"missing dir {sub}"

        # novel/ 至少含整书 + 两章 + 一卷
        novel = book_dir / "novel"
        novel_txts = list(novel.glob("*.txt"))
        assert len(novel_txts) >= 4, f"novel/ 产出不足: {[p.name for p in novel_txts]}"
        assert any(p.name.startswith("全书-") for p in novel_txts)
        assert any(p.name.startswith("第1章") for p in novel_txts)
        assert any(p.name.startswith("第2章") for p in novel_txts)
        assert any(p.name.startswith("第1卷") for p in novel_txts)

        # canon/ 五类
        canon = book_dir / "canon"
        for fname in (
            "characters.json",
            "world_rules.json",
            "plot_events.json",
            "timeline_events.json",
            "volumes.json",
        ):
            assert (canon / fname).is_file(), f"missing canon/{fname}"

        # story_state/ 两版本
        ssd = book_dir / "story_state"
        assert (ssd / "1.json").is_file()
        assert (ssd / "2.json").is_file()

        # backup/ 至少一份 JSON
        backup = book_dir / "backup"
        backup_files = list(backup.glob("*.json"))
        assert len(backup_files) >= 1

        # manifest.json 存在且合法
        assert manifest_path(book_dir).is_file()

        # SyncResult 计数无错
        assert result.errors == []
        assert result.chapters_written == 2
        assert result.volumes_written == 1
        assert result.canon_files_written == 5
        assert result.story_states_written == 2
        assert result.story_states_skipped == 0
        assert result.backup_files_written == 1
        assert result.manifest_written is True

    def test_story_state_dedup_across_repeated_push(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)

        svc.push_book(pid)
        _, result2 = svc.push_book(pid)

        # story_state 不再写，全部跳过
        assert result2.story_states_written == 0
        assert result2.story_states_skipped == 2
        ssd = cd / _find_book_subdir(cd, pid) / "story_state"
        assert len(list(ssd.glob("*.json"))) == 2

    def test_slug_stable_across_repeated_push(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)

        book_dir1, res1 = svc.push_book(pid)
        book_dir2, res2 = svc.push_book(pid)

        # slug 稳定（不变成 ``<base>-2``）
        assert book_dir1 == book_dir2
        assert res1.slug == res2.slug

    def test_manifest_fields_complete(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path, name="测试书 A")
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        book_dir, _ = svc.push_book(pid)

        data = json.loads(manifest_path(book_dir).read_text(encoding="utf-8"))
        # 必需键
        for k in (
            "project_id",
            "name",
            "slug",
            "genre",
            "status",
            "target_words",
            "synced_at",
            "chapter_count",
            "draft_count",
            "story_state_versions",
            "content_dir",
        ):
            assert k in data, f"manifest 缺字段 {k!r}"
        assert data["project_id"] == pid
        assert data["chapter_count"] == 2
        assert data["draft_count"] == 2
        assert data["story_state_versions"] == [1, 2]
        assert data["slug"] == res_slug_for(cd, pid)  # 单独断言同名

    def test_distinct_projects_get_distinct_slugs(self, tmp_path: Path, db_path: Path) -> None:
        pid1 = _seed_minimal_project(db_path, name="书A")
        pid2 = _seed_minimal_project(db_path, name="书B")
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)

        book_dir1, _ = svc.push_book(pid1)
        book_dir2, _ = svc.push_book(pid2)

        assert book_dir1 != book_dir2
        assert book_dir1.parent == cd
        assert book_dir2.parent == cd

    def test_push_unknown_project_raises(self, tmp_path: Path, db_path: Path) -> None:
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        with pytest.raises(ValueError, match="not found"):
            svc.push_book("prj_does_not_exist")


# ---------------------------------------------------------------------------
# init：仅建骨架
# ---------------------------------------------------------------------------


class TestInitBook:
    def test_init_creates_subdirs_and_manifest_only(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        book_dir, manifest = svc.init_book(pid)

        for sub in BOOK_DIRS:
            assert (book_dir / sub).is_dir()
        # init 不写产物
        assert list((book_dir / "novel").glob("*")) == []
        assert list((book_dir / "canon").glob("*")) == []
        assert list((book_dir / "story_state").glob("*")) == []
        assert list((book_dir / "backup").glob("*")) == []
        # manifest 字段齐
        assert manifest.project_id == pid
        assert manifest.chapter_count == 0
        assert manifest.draft_count == 0

    def test_init_then_push_uses_same_slug(self, tmp_path: Path, db_path: Path) -> None:
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        book_dir_init, _ = svc.init_book(pid)
        book_dir_push, _ = svc.push_book(pid)
        assert book_dir_init == book_dir_push


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListBooks:
    def test_list_returns_each_book_dir(self, tmp_path: Path, db_path: Path) -> None:
        pid1 = _seed_minimal_project(db_path, name="书A")
        pid2 = _seed_minimal_project(db_path, name="书B")
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        svc.push_book(pid1)
        svc.push_book(pid2)

        books = svc.list_books()
        slugs = {b["slug"] for b in books}
        assert len(slugs) == 2
        for b in books:
            assert b["project_id"] in (pid1, pid2)
            assert "synced_at" in b
            assert "name" in b

    def test_list_empty_when_no_content_dir(self, tmp_path: Path, db_path: Path) -> None:
        cd = tmp_path / "absent"
        svc = ContentSyncService(str(db_path), cd)
        assert svc.list_books() == []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _find_book_subdir(content_dir: Path, project_id: str) -> str:
    """在 ``content_dir`` 下找 ``manifest.json`` 含 ``project_id`` 的子目录。"""
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
            return child.name
    raise AssertionError(f"no book dir under {content_dir} for project {project_id}")


def res_slug_for(content_dir: Path, project_id: str) -> str:
    return _find_book_subdir(content_dir, project_id)


# ---------------------------------------------------------------------------
# 3a: 同秒 backup 命名兜底（连续 2 次 push 触发 target.exists()，断言 ``-1`` 后缀
#     且不覆盖 ``<ts>``）
# ---------------------------------------------------------------------------


class TestBackupSameSecondFallback:
    def test_same_second_push_appends_dash_one_suffix(
        self, tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两次 push 同一秒：第 1 个文件名 = ``<slug>-backup-<ts>.json``，第 2 个
        = ``<slug>-backup-<ts>-1.json``（按 -1 起跳，绝不覆盖第 1 个文件）。"""
        pid = _seed_minimal_project(db_path)
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)

        # 冻结时间戳 → 两次 push 同一秒
        fixed_ts = "20260909-120000"
        from packages.core.content_sync import service as svc_mod

        monkeypatch.setattr(svc_mod, "_timestamp_compact", lambda: fixed_ts)

        svc.push_book(pid)
        svc.push_book(pid)

        book_dir = cd / _find_book_subdir(cd, pid)
        backups = sorted((book_dir / "backup").glob("*.json"))
        names = sorted(p.name for p in backups)
        # 应有 2 个文件
        assert len(backups) == 2
        # 注意 ASCII 排序：``-`` (0x2D) < ``.`` (0x2E)，所以 ``-1`` 后缀文件
        # 字典序在前；不带后缀的 ``.json`` 在后。直接用 ``set`` 验两套文件名都存在。
        no_suffix = f"a-backup-{fixed_ts}.json"
        with_suffix = f"a-backup-{fixed_ts}-1.json"
        assert no_suffix in names, f"缺少无后缀文件 {no_suffix}，实际：{names}"
        assert with_suffix in names, f"缺少 -1 后缀文件 {with_suffix}，实际：{names}"
        # 关键：第 1 个文件未被覆盖（两个文件 size 相同——导出包内容相同）
        assert backups[0].stat().st_size == backups[1].stat().st_size


# ---------------------------------------------------------------------------
# 3b: Windows 保留名（CON / PRN / AUX / NUL / COM1 / LPT1）的 slug 派生不抛错
#     且目录名安全（保留名加 ``book-`` 前缀）
# ---------------------------------------------------------------------------


class TestWindowsReservedNameSlug:
    """测试 ``derive_slug`` + ``push_book`` 在 project name 为 Windows 保留名时
    的派生安全。Windows 下 ``mkdir CON`` 会失败；本任务在 slug 派生阶段就
    把保留名前缀化，避免触发设备文件语义。"""

    @pytest.mark.parametrize(
        "reserved_name",
        ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1", "con", "Prn", "lpt9"],
    )
    def test_derive_slug_prefixes_windows_reserved(
        self, tmp_path: Path, reserved_name: str
    ) -> None:
        from packages.core.content_sync.slug import derive_slug

        slug = derive_slug(reserved_name, "prj_test01")
        # 保留名一定被 ``book-`` 前缀化
        assert slug.startswith("book-"), f"reserved {reserved_name!r} → {slug!r} 未前缀化"
        assert slug.lower() != reserved_name.lower()

    def test_push_book_with_reserved_name_creates_safe_book_dir(
        self, tmp_path: Path, db_path: Path
    ) -> None:
        """端到端：project name = ``CON``，slug 应自动前缀化为 ``book-con``，
        ``mkdir`` 不报错，目录名不含裸 ``CON``。"""
        pid = _seed_minimal_project(db_path, name="CON")
        cd = _build_content_dir(tmp_path)
        svc = ContentSyncService(str(db_path), cd)
        book_dir, _ = svc.push_book(pid)
        # 目录名 = ``book-con``（保留名前缀化），不是裸 ``CON``
        assert book_dir.name == "book-con"
        assert book_dir.is_dir()
        # 其它产物写入无错
        assert (book_dir / "manifest.json").is_file()
        assert any((book_dir / "backup").glob("*.json"))


# ---------------------------------------------------------------------------
# 3c: ``Manifest.from_dict`` 缺字段 / 类型异常容错
# ---------------------------------------------------------------------------


class TestManifestFromDictFaultTolerance:
    """``Manifest.from_dict`` 对外部篡改 / 老版本 manifest 必须不抛错。"""

    def test_missing_optional_fields_use_defaults(self) -> None:
        # 全部可选字段都缺：仅 project_id / name / slug 三必填字符串保留
        m = Manifest.from_dict({"project_id": "p1", "name": "n", "slug": "s"})
        assert m.project_id == "p1"
        assert m.name == "n"
        assert m.slug == "s"
        assert m.genre is None
        assert m.status is None
        assert m.target_words is None
        assert m.synced_at == ""
        assert m.chapter_count == 0
        assert m.draft_count == 0
        assert m.story_state_versions == []
        assert m.content_dir is None

    def test_missing_content_dir_keeps_none(self) -> None:
        m = Manifest.from_dict(
            {
                "project_id": "p1",
                "name": "n",
                "slug": "s",
                "genre": "x",
                "status": "ACTIVE",
                # content_dir 缺失
            }
        )
        assert m.content_dir is None
        assert m.genre == "x"
        assert m.status == "ACTIVE"

    def test_missing_genre_and_status_keep_none(self) -> None:
        m = Manifest.from_dict(
            {"project_id": "p1", "name": "n", "slug": "s"}
        )
        assert m.genre is None
        assert m.status is None

    def test_malformed_target_words_falls_back_to_none(self) -> None:
        # ``target_words`` 传字符串无法转 int → 容错为 None
        m = Manifest.from_dict(
            {
                "project_id": "p1",
                "name": "n",
                "slug": "s",
                "target_words": "not-an-int",
            }
        )
        assert m.target_words is None

    def test_malformed_story_state_versions_filters_bad_items(self) -> None:
        m = Manifest.from_dict(
            {
                "project_id": "p1",
                "name": "n",
                "slug": "s",
                "story_state_versions": [1, "two", 3.0, None, 4],
            }
        )
        # 可转换的留下：1, 4（字符串 ``"two"`` / 浮点 ``3.0`` Python ``int()`` 会
        # 成功 → 算可转换；这里验证「None / 非数字类型」被过滤）。
        assert all(isinstance(x, int) for x in m.story_state_versions)

    def test_empty_dict_does_not_raise(self) -> None:
        m = Manifest.from_dict({})
        assert m.project_id == ""
        assert m.slug == ""

    def test_non_dict_input_does_not_raise(self) -> None:
        # 外部篡改成 list / str / None 都应安全
        for bad in (None, [], "not-a-dict", 42):
            m = Manifest.from_dict(bad)  # type: ignore[arg-type]
            assert isinstance(m, Manifest)
            assert m.project_id == ""
