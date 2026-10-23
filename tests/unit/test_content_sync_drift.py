"""题材包版本漂移巡检（``genre-doctor``）与 ``genre-pull`` 反向漂移告警的测试。

覆盖：

- 巡检四类状态：一致（静默）/ ``content_newer`` / ``db_newer`` /
  ``content_only``（内容仓有、DB 无）/ ``db_only``（DB 有、内容仓无）；
- 退出码口径：``0`` 无漂移 / ``1`` 有漂移 / ``2`` 巡检自身出错；
- ``--json`` 输出可解析、字段齐全（``pack_id`` / ``content_version`` /
  ``db_version`` / ``direction`` / ``content_path``）；
- ``genre-pull`` 遇反向漂移：**产生 warning**（stderr + 返回结构），
  **既有行为完全不变**（创建 / 更新 / no-op / version 自增与无漂移时逐字一致）；
- 降级收窄：运行库「缺表」降级为记错，锁 / I/O 故障**上抛**不吞；
- **突变自检**：把「版本比较」换成恒假 / 反转谓词后，「漂移必须被检出」
  这条行为断言**必须变红**——否则说明检测没真的走比较机制。

测试模式与 ``test_content_sync_genre.py`` 一致：``tmp_path`` 拉临时 SQLite
（``Settings(data_dir=...)`` + ``apply_migrations``）+ ``tmp_path`` 内容仓，
**不触碰**真实 ``data/novelos.db`` 与 ``D:/zcodeproject/NovelOS-Content``。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.content_sync import ContentSyncService, detect_genre_drift
from packages.core.content_sync import drift as drift_mod
from packages.core.content_sync.drift import (
    DIRECTION_CONTENT_NEWER,
    DIRECTION_CONTENT_ONLY,
    DIRECTION_DB_NEWER,
    DIRECTION_DB_ONLY,
)
from packages.core.content_sync.paths import (
    GENRES_DIR,
    genre_pack_path,
    genres_dir,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.genre import GenrePackCreate, GenrePackService

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.content_sync import main as cli_main  # noqa: E402

_PACK_ID = "genre-male-quicktrans-v1"
_SLUG = "pack-trans-v1"

# 合 schema 的最小 payload（与 test_content_sync_genre.py 同款）。
_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "min_interval_chapters": 3,
        }
    ],
    "structure_templates": {"structure_model": "单元剧"},
    "pacing": {"chapter_words": {"target": 2500}},
}

# 内容仓侧的「另一份」payload（用于构造 pull 会真正写库的场景）。
_PAYLOAD_OTHER: dict = {
    **_PAYLOAD,
    "pacing": {"chapter_words": {"target": 3000}},
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_db(tmp_path: Path):
    """工厂：造一个跑过全量迁移的临时运行库（每次调用一个独立库）。"""

    def _make(tag: str) -> Path:
        d = tmp_path / f"db_{tag}"
        d.mkdir(parents=True, exist_ok=True)
        settings = Settings(data_dir=d, log_level="WARNING")
        apply_migrations(settings.db_path)
        return settings.db_path

    return _make


@pytest.fixture()
def db_path(make_db) -> Path:
    return make_db("main")


@pytest.fixture()
def content_dir(tmp_path: Path) -> Path:
    d = tmp_path / "content"
    d.mkdir()
    return d


def _seed_db_pack(
    db_path: Path,
    *,
    pack_id: str = _PACK_ID,
    version: int = 1,
    payload: dict | None = None,
) -> str:
    """在运行库种一个题材包，并把 ``version`` **直接置为指定值**。

    version 走 SQL 直写（而不是靠 N 次 update 累加）——夹具需要**精确**的版本号，
    且 payload 要保持不变（否则「同 payload、不同 version」的场景根本造不出来）。
    """
    GenrePackService(db_path).create(
        GenrePackCreate(
            name="男主快穿",
            genre_tag="快穿",
            payload=_PAYLOAD if payload is None else payload,
            pack_id=pack_id,
            source_path=f"{GENRES_DIR}/{_SLUG}",
        )
    )
    if version != 1:
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE genre_packs SET version = ? WHERE pack_id = ?",
                (version, pack_id),
            )
            conn.commit()
        finally:
            conn.close()
    return pack_id


def _write_content_pack(
    content_dir: Path,
    *,
    slug: str = _SLUG,
    pack_id: str = _PACK_ID,
    version: int = 1,
    payload: dict | None = None,
) -> Path:
    """在内容仓写一份 ``genres/<slug>/pack.json``（形态与 ``genre-push`` 产出同款）。"""
    d = genres_dir(content_dir) / slug
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "pack_id": pack_id,
        "name": "男主快穿",
        "genre_tag": "快穿",
        "version": version,
        "payload": _PAYLOAD if payload is None else payload,
        "source_path": f"{GENRES_DIR}/{slug}",
        "synced_at": "2026-09-14T14:30:28+08:00",
    }
    target = genre_pack_path(d)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _directions(report) -> list[str]:
    return [d.direction for d in report.drift]


def _assert_reports_db_newer(report) -> None:
    """行为断言（供突变自检复用）：只应报出且报出一个 ``db_newer``。"""
    assert _directions(report) == [DIRECTION_DB_NEWER]


def _assert_warns(result) -> None:
    """行为断言（供突变自检复用）：反向漂移必须产生告警。"""
    assert result.warnings


# ---------------------------------------------------------------------------
# 巡检：四类状态
# ---------------------------------------------------------------------------


class TestDriftDetection:
    def test_same_version_is_silent_and_exit_zero(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """① 两边一致 ⇒ 无漂移（静默通过），退出码 0。"""
        _seed_db_pack(db_path, version=6)
        _write_content_pack(content_dir, version=6)

        report = detect_genre_drift(db_path, content_dir)

        assert report.drift == []
        assert report.errors == []
        assert report.checked == 1
        assert report.ok is True

        code = cli_main(
            ["--db", str(db_path), "--content-dir", str(content_dir), "genre-doctor"]
        )
        assert code == 0

    def test_content_version_higher_reports_content_newer(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """② 内容仓 version 高于 DB ⇒ ``content_newer``。"""
        _seed_db_pack(db_path, version=3)
        _write_content_pack(content_dir, version=7)

        report = detect_genre_drift(db_path, content_dir)

        assert len(report.drift) == 1
        item = report.drift[0]
        assert item.direction == DIRECTION_CONTENT_NEWER
        assert item.pack_id == _PACK_ID
        assert item.content_version == 7
        assert item.db_version == 3
        assert item.content_path is not None
        assert item.content_path.endswith("pack.json")

    def test_db_version_higher_reports_db_newer(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """③ DB version 高于内容仓 ⇒ ``db_newer``（反向漂移）。"""
        _seed_db_pack(db_path, version=10)
        _write_content_pack(content_dir, version=6)

        report = detect_genre_drift(db_path, content_dir)

        assert len(report.drift) == 1
        item = report.drift[0]
        assert item.direction == DIRECTION_DB_NEWER
        assert item.pack_id == _PACK_ID
        assert item.content_version == 6
        assert item.db_version == 10
        assert item.content_path is not None

    def test_content_only_when_db_has_no_such_pack(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """④ 内容仓有、DB 无 ⇒ 「内容仓有、DB 无」。"""
        _write_content_pack(content_dir, version=2)

        report = detect_genre_drift(db_path, content_dir)

        assert len(report.drift) == 1
        item = report.drift[0]
        assert item.direction == DIRECTION_CONTENT_ONLY
        assert item.pack_id == _PACK_ID
        assert item.content_version == 2
        assert item.db_version is None
        assert item.content_path is not None

    def test_db_only_when_content_repo_has_no_pack_json(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """⑤ DB 有、内容仓无 ⇒ 「DB 有、内容仓无」。"""
        _seed_db_pack(db_path, version=4)

        report = detect_genre_drift(db_path, content_dir)

        assert len(report.drift) == 1
        item = report.drift[0]
        assert item.direction == DIRECTION_DB_ONLY
        assert item.pack_id == _PACK_ID
        assert item.db_version == 4
        assert item.content_version is None
        assert item.content_path is None
        # DB 行记录的内容仓来源路径 = 「文件该放在哪」的指针
        assert item.db_source_path == f"{GENRES_DIR}/{_SLUG}"

    def test_missing_content_dir_is_not_an_error(self, db_path: Path, tmp_path: Path) -> None:
        """内容仓目录不存在 ⇒ 空报告（不是错误）。"""
        empty = tmp_path / "no-content-dir"
        report = detect_genre_drift(db_path, empty)

        assert report.drift == []
        assert report.errors == []
        assert report.checked == 0
        assert report.ok is True


# ---------------------------------------------------------------------------
# CLI：--json 输出与退出码
# ---------------------------------------------------------------------------


class TestDriftCli:
    def test_json_output_is_parsable_with_all_required_fields(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """⑥ ``--json`` 输出可解析、字段齐全、退出码符合预期。"""
        _seed_db_pack(db_path, version=10)
        _write_content_pack(content_dir, version=6)

        code = cli_main(
            [
                "--db",
                str(db_path),
                "--content-dir",
                str(content_dir),
                "genre-doctor",
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert code == 1
        assert payload["ok"] is False
        assert payload["checked"] == 1
        assert payload["errors"] == []
        assert len(payload["drift"]) == 1
        item = payload["drift"][0]
        for key in (
            "pack_id",
            "content_version",
            "db_version",
            "direction",
            "content_path",
        ):
            assert key in item, f"JSON 漂移项缺字段：{key}"
        assert item["pack_id"] == _PACK_ID
        assert item["content_version"] == 6
        assert item["db_version"] == 10
        assert item["direction"] == DIRECTION_DB_NEWER
        assert item["content_path"]

    def test_json_output_ok_true_and_exit_zero_when_no_drift(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _seed_db_pack(db_path, version=6)
        _write_content_pack(content_dir, version=6)

        code = cli_main(
            [
                "--db",
                str(db_path),
                "--content-dir",
                str(content_dir),
                "genre-doctor",
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["ok"] is True
        assert payload["drift"] == []
        assert payload["checked"] == 1

    def test_text_output_lists_direction_and_versions(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _seed_db_pack(db_path, version=10)
        _write_content_pack(content_dir, version=6)

        code = cli_main(
            ["--db", str(db_path), "--content-dir", str(content_dir), "genre-doctor"]
        )
        out = capsys.readouterr().out

        assert code == 1
        assert DIRECTION_DB_NEWER in out
        assert _PACK_ID in out

    def test_exit_two_when_inspection_errors(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """巡检自身出错（pack.json 不可解析）⇒ 退出码 2（优先于漂移）。"""
        bad = genres_dir(content_dir) / "broken"
        bad.mkdir(parents=True)
        (bad / "pack.json").write_text("{not json", encoding="utf-8")
        _seed_db_pack(db_path, version=3)

        code = cli_main(
            [
                "--db",
                str(db_path),
                "--content-dir",
                str(content_dir),
                "genre-doctor",
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)

        assert code == 2
        assert payload["errors"]
        assert payload["ok"] is False



# ---------------------------------------------------------------------------
# 降级收窄：缺表降级 / 故障上抛
# ---------------------------------------------------------------------------


class TestDriftErrorScope:
    def test_missing_table_degrades_to_error_entry_not_crash(
        self, tmp_path: Path, content_dir: Path
    ) -> None:
        """``genre_packs`` 表缺失（迁移未跑）⇒ 降级为「记错」，不抛。"""
        bare = tmp_path / "bare.db"
        conn = sqlite3.connect(bare)
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        finally:
            conn.close()

        report = detect_genre_drift(bare, content_dir)

        assert report.errors  # 缺表被记为巡检错误
        assert report.ok is False

    def test_unreadable_db_raises_instead_of_being_swallowed(
        self, tmp_path: Path, content_dir: Path
    ) -> None:
        """运行库打不开（I/O 故障）⇒ **上抛**，不得降级成「DB 侧为空」。"""
        bad_db = tmp_path / "no-such-dir" / "novelos.db"

        with pytest.raises(sqlite3.OperationalError):
            detect_genre_drift(bad_db, content_dir)

    def test_cli_reports_inspection_failure_as_exit_two(
        self, tmp_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        bad_db = tmp_path / "no-such-dir" / "novelos.db"

        code = cli_main(
            ["--db", str(bad_db), "--content-dir", str(content_dir), "genre-doctor"]
        )
        captured = capsys.readouterr()

        assert code == 2
        assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# P2：genre-pull 反向漂移告警（既有行为不变）
# ---------------------------------------------------------------------------


class TestPullReverseDriftWarning:
    def test_reverse_drift_produces_warning_without_changing_behaviour(
        self, make_db, content_dir: Path
    ) -> None:
        """⑦ 反向漂移 ⇒ 产生 warning；pull 的既有行为与无漂移时**逐字一致**。"""
        db_plain = make_db("plain")
        db_drift = make_db("drift")
        # 内容仓 v6（payload B）；DB v6 / v10（payload A）
        _write_content_pack(content_dir, version=6, payload=_PAYLOAD_OTHER)
        _seed_db_pack(db_plain, version=6, payload=_PAYLOAD)
        _seed_db_pack(db_drift, version=10, payload=_PAYLOAD)

        plain = ContentSyncService(db_plain, content_dir).pull_genre_pack(_SLUG)
        drift = ContentSyncService(db_drift, content_dir).pull_genre_pack(_SLUG)

        # 无漂移：无告警
        assert plain.warnings == []
        # 反向漂移：有告警，且告警内容指明是哪边新
        assert drift.warnings
        assert "反向漂移" in drift.warnings[0]
        assert "10" in drift.warnings[0]
        assert "6" in drift.warnings[0]

        # 既有行为不变：创建 / 更新 / 版本自增 与无漂移时**同形**
        # （绝对版本号本就随「拉取前 DB 版本」不同——那正是漂移本身；
        #   不变的是「行为」：不是新建、是更新、版本恰好 +1。）
        assert (plain.created, plain.updated) == (drift.created, drift.updated)
        assert plain.created is False
        assert plain.updated is True
        assert plain.version == 6 + 1  # DB v6 → payload 变更 → 自增
        assert drift.version == 10 + 1  # DB v10 → 同样 payload 变更 → 同样自增

        # 落库结果也一致：pull 语义是「以内容仓为准」（漂移不改变方向）
        assert GenrePackService(db_plain).get(_PACK_ID)["payload"] == _PAYLOAD_OTHER
        assert GenrePackService(db_drift).get(_PACK_ID)["payload"] == _PAYLOAD_OTHER


    def test_pull_when_content_is_newer_has_no_warning(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """内容仓更新（正常「拉新」）⇒ 不告警。"""
        _seed_db_pack(db_path, version=2, payload=_PAYLOAD)
        _write_content_pack(content_dir, version=8, payload=_PAYLOAD_OTHER)

        result = ContentSyncService(db_path, content_dir).pull_genre_pack(_SLUG)

        assert result.warnings == []
        assert result.updated is True

    def test_cli_pull_writes_warning_to_stderr(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """告警落 stderr，stdout 只留机器可读 JSON（JSON 里也带 ``warnings``）。"""
        _seed_db_pack(db_path, version=10, payload=_PAYLOAD)
        _write_content_pack(content_dir, version=6, payload=_PAYLOAD_OTHER)

        code = cli_main(
            [
                "--db",
                str(db_path),
                "--content-dir",
                str(content_dir),
                "genre-pull",
                _SLUG,
            ]
        )
        captured = capsys.readouterr()

        assert code == 0
        assert "WARNING" in captured.err
        assert "反向漂移" in captured.err
        summary = json.loads(captured.out)
        assert summary["warnings"]
        # 既有行为不变：pull 照样把内容仓 payload 写进 DB
        assert summary["updated"] is True
        assert GenrePackService(db_path).get(_PACK_ID)["payload"] == _PAYLOAD_OTHER


# ---------------------------------------------------------------------------
# 突变自检：证明「版本比较」这条机制真的被调用
# ---------------------------------------------------------------------------


class TestMutationSelfCheck:
    """把「版本比较」换成恒假 / 反转谓词 ⇒ 行为断言**必须变红**。

    若这些用例在变异后仍绿，说明巡检 / 告警**根本没走**比较机制
    （本组用例测不到东西）——这正是「断言了结果 ≠ 判别了机制」那条纪律要防的。
    """

    def test_compare_versions_being_consulted_is_load_bearing(
        self, monkeypatch: pytest.MonkeyPatch, db_path: Path, content_dir: Path
    ) -> None:
        _seed_db_pack(db_path, version=10)
        _write_content_pack(content_dir, version=6)

        # 基线：行为断言成立
        _assert_reports_db_newer(detect_genre_drift(db_path, content_dir))

        # 变异：版本比较改成恒假谓词（永远判「一致」）
        monkeypatch.setattr(drift_mod, "compare_versions", lambda content_v, db_v: "")

        # 变异后同一条行为断言必须失败 ⇒ 证明它真的守住了机制
        with pytest.raises(AssertionError):
            _assert_reports_db_newer(detect_genre_drift(db_path, content_dir))

    def test_direction_reversal_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, db_path: Path, content_dir: Path
    ) -> None:
        _seed_db_pack(db_path, version=10)
        _write_content_pack(content_dir, version=6)

        _assert_reports_db_newer(detect_genre_drift(db_path, content_dir))

        # 变异：方向判定反转（永远判「内容仓新」）
        monkeypatch.setattr(
            drift_mod, "compare_versions", lambda content_v, db_v: DIRECTION_CONTENT_NEWER
        )

        with pytest.raises(AssertionError):
            _assert_reports_db_newer(detect_genre_drift(db_path, content_dir))

    def test_pull_warning_depends_on_version_compare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        make_db,
        content_dir: Path,
    ) -> None:
        db_baseline = make_db("warn_base")
        db_mutated = make_db("warn_mut")
        _seed_db_pack(db_baseline, version=10, payload=_PAYLOAD)
        _seed_db_pack(db_mutated, version=10, payload=_PAYLOAD)
        _write_content_pack(content_dir, version=6, payload=_PAYLOAD_OTHER)

        # 基线：反向漂移 ⇒ 有告警
        baseline = ContentSyncService(db_baseline, content_dir).pull_genre_pack(_SLUG)
        _assert_warns(baseline)

        # 变异：版本比较恒假 ⇒ 同一条告警行为断言必须失败
        monkeypatch.setattr(drift_mod, "compare_versions", lambda content_v, db_v: "")
        mutated = ContentSyncService(db_mutated, content_dir).pull_genre_pack(_SLUG)
        with pytest.raises(AssertionError):
            _assert_warns(mutated)

    def test_content_only_and_db_only_are_not_produced_by_equal_versions(
        self, db_path: Path, content_dir: Path
    ) -> None:
        """单边状态判定不依赖版本比较：两侧都无该 pack 时不得凭空造出漂移项。"""
        _seed_db_pack(db_path, pack_id="pack-a", version=1)
        _write_content_pack(content_dir, pack_id="pack-b", version=1)

        report = detect_genre_drift(db_path, content_dir)

        assert sorted(_directions(report)) == [DIRECTION_CONTENT_ONLY, DIRECTION_DB_ONLY]
        assert report.checked == 2
