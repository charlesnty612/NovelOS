"""content_sync 题材包同步（genre-push / genre-pull）单元测试（题材库 P3a）。

覆盖：

- ``push_genre_pack``：``genres/<slug>/pack.json`` 落盘（payload + 元信息）；
- slug 规则：ASCII name → 归一化；中文 / 空 name → ``pack-<pack_id 末 8>``；
  Windows 保留名前缀；同 pack_id 复用既有目录（幂等，不产生 ``-2``）；
- 内容仓根 ``manifest.json`` 的 ``genres`` 段：``synced_at`` + pack 清单，
  且重复 push 只保留一条该 pack 的清单项、既有其它顶层键不被覆盖；
- 双轨：``genres/<题材中文目录>/`` 的人编辑面文件不被 push 触碰；
- 写侧严格：payload 不合 schema → ``ValueError``（不落盘）；
- ``pull_genre_pack``：无则创建 / payload 变更则 version 自增 / 内容一致则 no-op；
  接受 slug、目录、``pack.json`` 文件路径三种入参；schema 不合 / 文件缺失 → ``ValueError``；
- 往返：push → 空库 pull 得到同一 payload；
- ``list_books`` 跳过 ``genres/`` 命名空间目录；
- CLI（``scripts/content_sync.py``）：``genre-push`` / ``genre-pull`` 退出码 0 / 1。

测试模式与 ``test_content_sync_service.py`` 一致：``tmp_path`` 拉临时 SQLite
（``Settings(data_dir=tmp_path)`` + ``apply_migrations``）+ ``tmp_path`` 内容仓，
不触碰真实 ``data/novelos.db`` 与 ``D:/zcodeproject/NovelOS-Content``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.content_sync import ContentSyncService
from packages.core.content_sync.paths import (
    GENRES_DIR,
    genre_pack_path,
    genres_dir,
)
from packages.core.content_sync.slug import derive_genre_slug
from packages.core.db import apply_migrations, get_connection
from packages.core.genre import GenrePackCreate, GenrePackService

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.content_sync import main as cli_main  # noqa: E402

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

_PACK_ID = "genre-male-quicktrans-v1"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    db_dir = tmp_path / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=db_dir, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


@pytest.fixture()
def content_dir(tmp_path: Path) -> Path:
    d = tmp_path / "content"
    d.mkdir()
    return d


@pytest.fixture()
def svc(db_path: Path, content_dir: Path) -> ContentSyncService:
    return ContentSyncService(db_path, content_dir)


def _seed_pack(
    db_path: Path,
    *,
    pack_id: str = _PACK_ID,
    name: str = "男主快穿",
    genre_tag: str = "快穿",
    payload: dict | None = None,
    source_path: str | None = "genres/male-quicktrans",
) -> dict:
    return GenrePackService(db_path).create(
        GenrePackCreate(
            name=name,
            genre_tag=genre_tag,
            payload=_PAYLOAD if payload is None else payload,
            pack_id=pack_id,
            source_path=source_path,
        )
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# slug 规则
# ---------------------------------------------------------------------------


class TestGenreSlug:
    def test_ascii_name_is_slugified(self) -> None:
        assert derive_genre_slug("Male Quick Trans", "gp_abcdef12") == "male-quick-trans"

    def test_chinese_name_falls_back_to_pack_id_tail(self) -> None:
        assert derive_genre_slug("男主快穿", "gp_abcdef12") == "pack-abcdef12"

    def test_empty_name_falls_back(self) -> None:
        assert derive_genre_slug(None, "gp_abcdef12") == "pack-abcdef12"

    def test_windows_reserved_name_gets_pack_prefix(self) -> None:
        assert derive_genre_slug("CON", "gp_abcdef12") == "pack-con"

    def test_short_pack_id_uses_full_tail(self) -> None:
        assert derive_genre_slug("中文", "abc1") == "pack-abc1"

    def test_determinism(self) -> None:
        assert derive_genre_slug("男主快穿", _PACK_ID) == derive_genre_slug(
            "男主快穿", _PACK_ID
        )


# ---------------------------------------------------------------------------
# genre-push
# ---------------------------------------------------------------------------


class TestGenrePush:
    def test_writes_pack_json_with_payload_and_meta(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path)
        pack_dir, result = svc.push_genre_pack(_PACK_ID)

        assert result.pack_written is True
        assert result.manifest_written is True
        assert result.errors == []
        # 中文 name → fallback slug ``pack-<pack_id 末 8>``
        assert result.slug == "pack-trans-v1"
        assert pack_dir == genres_dir(content_dir) / "pack-trans-v1"

        doc = _read_json(genre_pack_path(pack_dir))
        assert doc["pack_id"] == _PACK_ID
        assert doc["name"] == "男主快穿"
        assert doc["genre_tag"] == "快穿"
        assert doc["version"] == 1
        assert doc["payload"] == _PAYLOAD
        assert doc["source_path"] == "genres/male-quicktrans"
        assert doc["synced_at"]

    def test_manifest_gains_genres_section(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path)
        svc.push_genre_pack(_PACK_ID)

        manifest = _read_json(content_dir / "manifest.json")
        assert manifest["genres"]["synced_at"]
        packs = manifest["genres"]["packs"]
        assert len(packs) == 1
        assert packs[0]["pack_id"] == _PACK_ID
        assert packs[0]["slug"] == "pack-trans-v1"
        assert packs[0]["name"] == "男主快穿"
        assert packs[0]["version"] == 1

    def test_manifest_preserves_other_sections(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        (content_dir / "manifest.json").write_text(
            json.dumps({"books": [{"slug": "x"}], "note": "手工"}),
            encoding="utf-8",
        )
        _seed_pack(db_path)
        svc.push_genre_pack(_PACK_ID)

        manifest = _read_json(content_dir / "manifest.json")
        assert manifest["books"] == [{"slug": "x"}]
        assert manifest["note"] == "手工"
        assert manifest["genres"]["packs"][0]["pack_id"] == _PACK_ID

    def test_repush_reuses_dir_and_manifest_stays_single_entry(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path)
        first_dir, _ = svc.push_genre_pack(_PACK_ID)
        second_dir, second = svc.push_genre_pack(_PACK_ID)

        assert first_dir == second_dir
        assert second.errors == []
        # 目录没有 ``-2`` 兄弟（幂等复用）
        assert [p.name for p in genres_dir(content_dir).iterdir()] == [
            "pack-trans-v1"
        ]
        packs = _read_json(content_dir / "manifest.json")["genres"]["packs"]
        assert len(packs) == 1

    def test_repush_after_payload_update_overwrites_and_bumps_version(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path)
        pack_dir, _ = svc.push_genre_pack(_PACK_ID)

        updated_payload = {
            **_PAYLOAD,
            "pacing": {"chapter_words": {"target": 3000}},
        }
        GenrePackService(db_path).update(_PACK_ID, {"payload": updated_payload})
        pack_dir2, _ = svc.push_genre_pack(_PACK_ID)

        assert pack_dir2 == pack_dir
        doc = _read_json(genre_pack_path(pack_dir))
        assert doc["version"] == 2
        assert doc["payload"] == updated_payload

    def test_human_editing_surface_is_not_touched(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        """10 维文件（人编辑面）与 pack.json（运行面）双轨：push 不写中文目录。"""
        human_dir = genres_dir(content_dir) / "男主快穿"
        (human_dir / "payoffs").mkdir(parents=True)
        (human_dir / "profile.json").write_text("{}", encoding="utf-8")
        (human_dir / "payoffs" / "library.json").write_text("{}", encoding="utf-8")

        _seed_pack(db_path)
        svc.push_genre_pack(_PACK_ID)

        assert (human_dir / "profile.json").read_text(encoding="utf-8") == "{}"
        assert not genre_pack_path(human_dir).exists()
        assert _read_json(genre_pack_path(genres_dir(content_dir) / "pack-trans-v1"))

    def test_ascii_name_pack_uses_name_slug(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path, pack_id="gp_11112222", name="Male Quick Trans")
        pack_dir, result = svc.push_genre_pack("gp_11112222")

        assert result.slug == "male-quick-trans"
        assert pack_dir == genres_dir(content_dir) / "male-quick-trans"

    def test_unknown_pack_raises(self, svc: ContentSyncService) -> None:
        with pytest.raises(ValueError, match="not found"):
            svc.push_genre_pack("gp_nope")

    def test_invalid_payload_refused_without_writing(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        # 旁路写入非法 payload（绕过 API 校验）→ push 拒绝落盘（写侧严格）。
        _seed_pack(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE genre_packs SET payload_json = ? WHERE pack_id = ?",
                (json.dumps({"schema_version": "not-a-version"}), _PACK_ID),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="schema"):
            svc.push_genre_pack(_PACK_ID)
        assert not genres_dir(content_dir).exists()

    def test_list_genres_reports_pushed_pack(
        self, svc: ContentSyncService, db_path: Path
    ) -> None:
        _seed_pack(db_path)
        svc.push_genre_pack(_PACK_ID)

        entries = svc.list_genres()
        assert len(entries) == 1
        assert entries[0]["slug"] == "pack-trans-v1"
        assert entries[0]["pack_id"] == _PACK_ID

    def test_list_books_skips_genres_namespace(
        self, svc: ContentSyncService, db_path: Path
    ) -> None:
        _seed_pack(db_path)
        svc.push_genre_pack(_PACK_ID)

        assert all(e["slug"] != GENRES_DIR for e in svc.list_books())


# ---------------------------------------------------------------------------
# genre-pull
# ---------------------------------------------------------------------------


def _write_pack_json(
    content_dir: Path,
    slug: str,
    *,
    payload: dict | None = None,
    pack_id: str = _PACK_ID,
    name: str = "男主快穿",
    genre_tag: str = "快穿",
    version: int = 1,
    source_path: str | None = None,
) -> Path:
    d = genres_dir(content_dir) / slug
    d.mkdir(parents=True, exist_ok=True)
    doc: dict = {
        "pack_id": pack_id,
        "name": name,
        "genre_tag": genre_tag,
        "version": version,
        "payload": _PAYLOAD if payload is None else payload,
        "synced_at": "2026-09-13T00:00:00+00:00",
    }
    doc["source_path"] = f"{GENRES_DIR}/{slug}" if source_path is None else source_path
    target = genre_pack_path(d)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


class TestGenrePull:
    def test_creates_when_missing(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _write_pack_json(content_dir, "pack-trans-v1")

        result = svc.pull_genre_pack("pack-trans-v1")

        assert result.created is True
        assert result.updated is False
        assert result.version == 1
        pack = GenrePackService(db_path).get(_PACK_ID)
        assert pack is not None
        assert pack["name"] == "男主快穿"
        assert pack["genre_tag"] == "快穿"
        assert pack["payload"] == _PAYLOAD
        assert pack["source_path"] == f"{GENRES_DIR}/pack-trans-v1"

    def test_second_pull_is_noop(
        self, svc: ContentSyncService, db_path: Path
    ) -> None:
        _write_pack_json(svc.content_dir, "pack-trans-v1")
        svc.pull_genre_pack("pack-trans-v1")

        second = svc.pull_genre_pack("pack-trans-v1")

        assert second.created is False
        assert second.updated is False
        assert second.version == 1

    def test_payload_change_bumps_version(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        svc.pull_genre_pack(str(_write_pack_json(content_dir, "pack-trans-v1")))
        new_payload = {**_PAYLOAD, "pacing": {"chapter_words": {"target": 3000}}}
        _write_pack_json(content_dir, "pack-trans-v1", payload=new_payload)

        result = svc.pull_genre_pack("pack-trans-v1")

        assert result.created is False
        assert result.updated is True
        assert result.version == 2
        assert GenrePackService(db_path).get(_PACK_ID)["payload"] == new_payload

    def test_accepts_dir_and_file_paths(
        self, svc: ContentSyncService, content_dir: Path
    ) -> None:
        pack_file = _write_pack_json(content_dir, "pack-trans-v1")
        assert svc.pull_genre_pack(str(pack_file)).created is True

        # 目录路径（第二次 pull：内容一致 → no-op）
        dir_result = svc.pull_genre_pack(str(pack_file.parent))
        assert dir_result.created is False
        assert dir_result.updated is False

    def test_pack_id_falls_back_to_slug(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _write_pack_json(content_dir, "hand-pack", pack_id="", name="", genre_tag="")

        result = svc.pull_genre_pack("hand-pack")

        assert result.pack_id == "hand-pack"
        pack = GenrePackService(db_path).get("hand-pack")
        assert pack is not None
        assert pack["name"] == "hand-pack"

    def test_invalid_schema_raises_and_writes_nothing(
        self, svc: ContentSyncService, db_path: Path, content_dir: Path
    ) -> None:
        _write_pack_json(
            content_dir,
            "pack-trans-v1",
            payload={"schema_version": "nope", "bogus_key": 1},
        )

        with pytest.raises(ValueError, match="schema"):
            svc.pull_genre_pack("pack-trans-v1")
        assert GenrePackService(db_path).get(_PACK_ID) is None

    def test_missing_file_raises(self, svc: ContentSyncService) -> None:
        with pytest.raises(ValueError, match="pack.json"):
            svc.pull_genre_pack("no-such-slug")

    def test_round_trip_push_then_pull_into_empty_db(
        self, db_path: Path, content_dir: Path, tmp_path: Path
    ) -> None:
        _seed_pack(db_path)
        ContentSyncService(db_path, content_dir).push_genre_pack(_PACK_ID)

        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir(parents=True, exist_ok=True)
        fresh_db = Settings(data_dir=fresh_dir, log_level="WARNING").db_path
        apply_migrations(fresh_db)
        result = ContentSyncService(fresh_db, content_dir).pull_genre_pack(
            "pack-trans-v1"
        )

        assert result.created is True
        pack = GenrePackService(fresh_db).get(_PACK_ID)
        assert pack is not None
        assert pack["payload"] == _PAYLOAD


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestGenreCli:
    def test_genre_push_and_pull_exit_zero(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _seed_pack(db_path)
        base = ["--db", str(db_path), "--content-dir", str(content_dir)]

        assert cli_main([*base, "genre-push", _PACK_ID]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["slug"] == "pack-trans-v1"
        assert out["pack_written"] is True
        assert out["manifest_written"] is True
        assert out["pack"]["payload"] == _PAYLOAD

        assert cli_main([*base, "genre-pull", "pack-trans-v1"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["created"] is False
        assert out["updated"] is False
        assert out["version"] == 1

    def test_genre_push_accepts_flag_alias(
        self, db_path: Path, content_dir: Path
    ) -> None:
        _seed_pack(db_path)
        base = ["--db", str(db_path), "--content-dir", str(content_dir)]

        assert cli_main([*base, "genre-push", "--pack", _PACK_ID]) == 0

    def test_genre_push_unknown_pack_exit_one(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        base = ["--db", str(db_path), "--content-dir", str(content_dir)]

        assert cli_main([*base, "genre-push", "gp_nope"]) == 1
        assert "ERROR" in capsys.readouterr().err

    def test_genre_pull_bad_payload_exit_one(
        self, db_path: Path, content_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _write_pack_json(
            content_dir, "pack-trans-v1", payload={"schema_version": "bad"}
        )
        base = ["--db", str(db_path), "--content-dir", str(content_dir)]

        assert cli_main([*base, "genre-pull", "pack-trans-v1"]) == 1
        assert "ERROR" in capsys.readouterr().err
