"""ContentSyncService —— 内容同步器主入口。

本服务把运行库（SQLite）某一本书（project）的产出按固定目录结构同步到
内容仓；题材包（genre_pack）走独立的 ``genres/`` 命名空间（P3a）。详见
:mod:`packages.core.content_sync.paths` 的目录约定。

公共 API：

- :meth:`ContentSyncService.init_book`：只创建书目录骨架（``novel / canon /
  story_state / backup``）与 ``manifest.json``，不写其它产物。用于在第一次
  同步前先验位置 / slug。
- :meth:`ContentSyncService.push_book`：全量同步该书到目标目录；幂等可重跑。
- :meth:`ContentSyncService.list_books`：枚举内容仓下所有 book 子目录的
  基础信息（slug / manifest）。
- :meth:`ContentSyncService.push_genre_pack`：把运行库某个题材包（payload +
  元信息）写到 ``genres/<slug>/pack.json``，并刷新内容仓根 ``manifest.json``
  的 ``genres`` 段；幂等可重跑。
- :meth:`ContentSyncService.pull_genre_pack`：读内容仓 ``pack.json`` → schema
  校验 → DB 侧创建（无则）或更新（有则 payload 变更 → version 自增）；
  对内容仓只读。
- :meth:`ContentSyncService.list_genres`：枚举 ``genres/`` 下带 ``pack.json``
  的题材包目录。
- :data:`SyncResult` / :class:`GenreSyncResult` / :class:`GenrePullResult`：
  push / pull 的返回结构。

设计要点：

- 所有写入走 ``pathlib.Path``，避免字符串拼路径；Windows 下 ``Path`` /
  ``PosixPath`` 自动切换。
- 章节 / 卷 / 整书 txt 全部走 :func:`packages.core.exporter.builder.build_txt`
  （不重新造轮子）；JSON 备份包走
  :meth:`packages.core.backup.BackupService.export_project`。
- 卷合并 txt：用 :func:`build_volume_txt` 按卷过滤章节后再走 ``build_txt``
  ``ExportScope(kind="book")`` 的相同口径重新拼装——保持单源真相。
- story_state 按 ``state_version`` 命名（天然幂等去重）。
- backup 每次 push 都新建带时间戳的 JSON 文件（不覆盖：内容仓版本管理
  闭环要求保留历史快照；时间戳精度到秒，重复 push 秒级一致会撞名，
  故同时记录 manifest 指向「本次新建的 backup 名」）。
- **双轨**：``genres/`` 下 10 维题材资产（``profile.json`` /
  ``payoffs/library.json`` …）是**人编辑面**；``pack.json`` 是**运行面**
  （软件层消费的结构化 payload 子集）。两者互不覆盖、互不自动聚合——v1 只提供
  ``genre-push`` / ``genre-pull`` 在 DB 与 ``pack.json`` 之间搬运；「10 维 →
  pack.json」的聚合留后续版本。
- 题材包写路径复用 :mod:`packages.core.genre` 的 service / 校验器（单点真相），
  本模块只做「内容仓文件 ↔ DB 行」的搬运与目录约定。
- 不重写 exporter / backup 的签名；只在 service 内做组装。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.core.backup import BackupService
from packages.core.exporter import ExportScope, build_txt
from packages.core.genre import (
    GenrePackCreate,
    GenrePackService,
    GenrePackUpdate,
    validate_payload,
)
from packages.core.logging_config import get_logger

from . import queries
from .paths import (
    BOOK_DIRS,
    GENRES_DIR,
    GenrePackDoc,
    Manifest,
    backup_filename,
    content_manifest_path,
    genre_pack_path,
    genres_dir,
    manifest_path,
    novel_book_filename,
    novel_chapter_filename,
    novel_volume_filename,
    story_state_filename,
)
from .slug import derive_genre_slug, derive_slug, unique_slug

log = get_logger("novelos.content_sync")


@dataclass
class SyncResult:
    """``push_book`` 的返回结构。"""

    project_id: str
    slug: str
    book_dir: Path
    chapters_written: int = 0
    volumes_written: int = 0
    canon_files_written: int = 0
    story_states_written: int = 0
    story_states_skipped: int = 0
    backup_files_written: int = 0
    manifest_written: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return (
            self.chapters_written
            + self.volumes_written
            + self.canon_files_written
            + self.story_states_written
            + self.backup_files_written
            + (1 if self.manifest_written else 0)
        )


@dataclass
class GenreSyncResult:
    """``push_genre_pack`` 的返回结构。"""

    pack_id: str
    slug: str
    pack_dir: Path
    pack_written: bool = False
    manifest_written: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return (1 if self.pack_written else 0) + (1 if self.manifest_written else 0)


@dataclass
class GenrePullResult:
    """``pull_genre_pack`` 的返回结构。

    - ``created`` / ``updated`` 互斥：新建 → ``created=True``；既有行内容有变更 →
      ``updated=True``（payload 变更时 version 自增）；内容一致 → 两者皆 False
      （幂等 no-op，不产生新版本）；
    - ``version`` 为操作后 DB 侧版本号（**不**采用 pack.json 里写的 version：
      version 由 DB 单调维护，文件版本只作审计提示）。
    """

    pack_id: str
    slug: str
    source: Path
    created: bool = False
    updated: bool = False
    version: int = 0
    file_version: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON 容错工具
# ---------------------------------------------------------------------------


def _safe_json_loads(text: Any) -> Any:
    """``SELECT`` 出的 JSON 字段做容错解析；失败 fallback 原串（None 透传）。"""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _row_with_json_decoded(row: dict[str, Any], json_cols: tuple[str, ...]) -> dict[str, Any]:
    """对指定 JSON 列做 ``_safe_json_loads``，其它字段保留。"""
    out = dict(row)
    for c in json_cols:
        if c in out:
            out[c] = _safe_json_loads(out[c])
    return out


def _now_iso() -> str:
    """UTC ISO-8601；与 packages.core.ids.now_iso 风格保持一致。"""
    return datetime.now(timezone.utc).isoformat()


def _timestamp_compact() -> str:
    """本地时间戳：``YYYYmmdd-HHMMSS``，用于 backup 文件名。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ContentSyncService:
    """内容仓同步器。

    构造接收 ``db_path``（SQLite 运行库）与 ``content_dir``（内容仓根目录）。
    两个路径都可以是字符串或 :class:`pathlib.Path`。
    """

    def __init__(self, db_path: str | Path, content_dir: str | Path) -> None:
        self.db_path = str(db_path)
        self.content_dir = Path(content_dir)

    # ========================================================================
    # init：仅创建书目录骨架 + manifest（无内容产物）
    # ========================================================================

    def init_book(self, project_id: str) -> tuple[Path, Manifest]:
        """创建书目录骨架与 manifest；不写其它产物。

        返回 ``(book_dir, manifest)``。manifest 的 ``synced_at`` 字段记 init
        时刻（不代表已全量同步）；其它产物计数为 0。
        """
        project = queries.fetch_project(self.db_path, project_id)
        if project is None:
            raise ValueError(f"project {project_id!r} not found")
        book_dir, manifest = self._prepare_book_dir(project, story_state_versions=[])
        # init 也要写 manifest，便于 CLI ``init`` 后立即能 ls / diff
        self._write_manifest(book_dir, manifest)
        log.info("init_book: book_dir=%s manifest_written", book_dir)
        return book_dir, manifest

    # ========================================================================
    # push：全量同步（幂等）
    # ========================================================================

    def push_book(self, project_id: str) -> tuple[Path, SyncResult]:
        """全量同步一本书到内容仓；幂等可重跑。

        流程：
        1. 取 project，决定 slug + book_dir（必要时去重追加 ``-2`` / ``-3``）；
        2. 拉取 chapters / drafts / volumes / 五张 canon 表 / story_states；
        3. 写入 ``novel/``：整书 txt + 每章 txt + 每卷合并 txt；
        4. 写入 ``canon/``：五张 canon JSON；
        5. 写入 ``story_state/``：按 ``state_version`` 命名，重复跳过；
        6. 写入 ``backup/``：调用 BackupService.export_project 生成 JSON 包；
        7. 写入 ``manifest.json``（含本次产物统计 + synced_at）。
        """
        project = queries.fetch_project(self.db_path, project_id)
        if project is None:
            raise ValueError(f"project {project_id!r} not found")

        book_dir, base_manifest = self._prepare_book_dir(project, story_state_versions=[])
        result = SyncResult(project_id=project_id, slug=base_manifest.slug, book_dir=book_dir)

        # --- novel/ --------------------------------------------------------
        novel_dir = book_dir / "novel"
        novel_dir.mkdir(parents=True, exist_ok=True)

        chapters = queries.fetch_chapters(self.db_path, project_id)
        drafts = queries.fetch_drafts(self.db_path, project_id)

        # 整书 txt：复用 build_txt
        try:
            txt_bytes = build_txt(
                self.db_path,
                project_id,
                ExportScope(kind="book"),
            )
            (novel_dir / novel_book_filename(base_manifest.slug)).write_bytes(txt_bytes)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"novel book txt: {exc}")
            log.warning("novel book txt failed: %s", exc)

        # 每章 txt：复用 build_txt kind=chapter
        # 防御：``number`` 为 NULL / 非整数 / 转换抛错时，绝不让 ``第None章`` 落盘。
        # 静默 ``continue`` 会让用户不知道丢了哪几章；改为显式追加 ``errors`` 记录
        # chapter_id 与 number 原值，便于上游排查；同名 chapter_id 在同次 push 中只
        # 报一次（避免重复刷日志）。
        _skipped_chapter_ids: set[str] = set()
        for ch in chapters:
            cid = ch.get("chapter_id")
            num_raw = ch.get("number")
            if num_raw is None:
                if cid not in _skipped_chapter_ids:
                    result.errors.append(
                        f"chapter {cid!r} skipped: number is NULL (title={ch.get('title')!r})"
                    )
                    _skipped_chapter_ids.add(cid)
                    log.warning("chapter %s skipped: number is NULL", cid)
                continue
            try:
                num = int(num_raw)
            except (TypeError, ValueError) as exc:
                if cid not in _skipped_chapter_ids:
                    result.errors.append(
                        f"chapter {cid!r} skipped: number {num_raw!r} not int-castable ({exc})"
                    )
                    _skipped_chapter_ids.add(cid)
                    log.warning("chapter %s skipped: number=%r not int-castable", cid, num_raw)
                continue
            try:
                txt_bytes = build_txt(
                    self.db_path,
                    project_id,
                    ExportScope(kind="chapter", chapter_no=num),
                )
                (novel_dir / novel_chapter_filename(num, ch.get("title"))).write_bytes(txt_bytes)
                result.chapters_written += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"chapter {cid} (number={num}) txt: {exc}")
                log.warning("chapter %s (number=%s) txt failed: %s", cid, num, exc)

        # 每卷合并 txt：复用 build_txt 的拼装逻辑（按 chapter.volume_id 过滤）
        volume_groups = queries.fetch_volumes_for_book_txt(self.db_path, project_id)
        for grp in volume_groups:
            v = grp["volume"]
            vchs = grp["chapters"]
            if not v or not vchs:
                continue
            vid = v.get("volume_id")
            num_raw = v.get("number")
            if num_raw is None:
                result.errors.append(
                    f"volume {vid!r} skipped: number is NULL (title={v.get('title')!r})"
                )
                log.warning("volume %s skipped: number is NULL", vid)
                continue
            try:
                v_num = int(num_raw)
            except (TypeError, ValueError) as exc:
                result.errors.append(
                    f"volume {vid!r} skipped: number {num_raw!r} not int-castable ({exc})"
                )
                log.warning("volume %s skipped: number=%r not int-castable", vid, num_raw)
                continue
            try:
                txt_bytes = build_volume_txt(self.db_path, project_id, v, vchs)
                (novel_dir / novel_volume_filename(v_num, v.get("title"))).write_bytes(txt_bytes)
                result.volumes_written += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"volume {vid} (number={v_num}) txt: {exc}")
                log.warning("volume %s (number=%s) txt failed: %s", vid, v_num, exc)

        # --- canon/ --------------------------------------------------------
        canon_dir = book_dir / "canon"
        canon_dir.mkdir(parents=True, exist_ok=True)
        canon_writes: list[tuple[str, list[dict[str, Any]]]] = [
            (
                "characters.json",
                [
                    _row_with_json_decoded(r, ("core_json",))
                    for r in queries.fetch_characters(self.db_path, project_id)
                ],
            ),
            (
                "world_rules.json",
                [
                    _row_with_json_decoded(r, ("data_json",))
                    for r in queries.fetch_world_rules(self.db_path, project_id)
                ],
            ),
            (
                "plot_events.json",
                [
                    _row_with_json_decoded(
                        r,
                        ("cause_json", "effects_json", "participants_json", "time_json"),
                    )
                    for r in queries.fetch_plot_events(self.db_path, project_id)
                ],
            ),
            (
                "timeline_events.json",
                list(queries.fetch_timeline_events(self.db_path, project_id)),
            ),
            (
                "volumes.json",
                [
                    _row_with_json_decoded(r, ("terminal_snapshot_json",))
                    for r in queries.fetch_volumes(self.db_path, project_id)
                ],
            ),
        ]
        for fname, payload in canon_writes:
            try:
                _write_json(canon_dir / fname, payload)
                result.canon_files_written += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"canon {fname}: {exc}")
                log.warning("canon %s failed: %s", fname, exc)

        # --- story_state/ --------------------------------------------------
        state_dir = book_dir / "story_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        story_state_versions: list[int] = []
        for ss in queries.fetch_story_states(self.db_path, project_id):
            sv = ss.get("state_version")
            if sv is None:
                continue
            target = state_dir / story_state_filename(int(sv))
            if target.exists():
                # 幂等：同 state_version 已存在则跳过
                result.story_states_skipped += 1
                story_state_versions.append(int(sv))
                continue
            payload = {
                "project_id": ss.get("project_id"),
                "state_version": int(sv),
                "commit_id": ss.get("commit_id"),
                "created_at": ss.get("created_at"),
                "snapshot": _safe_json_loads(ss.get("snapshot_json")),
            }
            try:
                _write_json(target, payload)
                result.story_states_written += 1
                story_state_versions.append(int(sv))
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"story_state v{sv}: {exc}")
                log.warning("story_state v%s failed: %s", sv, exc)

        # --- backup/ -------------------------------------------------------
        backup_dir = book_dir / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            backup_payload = BackupService(self.db_path).export_project(project_id)
            ts = _timestamp_compact()
            # 同秒冲突兜底：首次未冲突用 ``<ts>``；冲突则按 ``<ts>-1``、``<ts>-2``
            # 递增加后缀，永远不覆盖已存在文件（首次 push 不带 ``-1`` 后缀；
            # 同秒第 2 次 push 才追加 ``-1``，与 README「``-n`` 兜底」语义一致）。
            target = backup_dir / backup_filename(base_manifest.slug, ts)
            if target.exists():
                n = 1
                while True:
                    candidate = backup_dir / backup_filename(
                        base_manifest.slug, f"{ts}-{n}"
                    )
                    if not candidate.exists():
                        target = candidate
                        break
                    n += 1
            _write_json(target, backup_payload)
            result.backup_files_written += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"backup json: {exc}")
            log.warning("backup json failed: %s", exc)

        # --- manifest ------------------------------------------------------
        manifest = Manifest(
            project_id=project_id,
            name=str(project.get("name") or ""),
            slug=base_manifest.slug,
            genre=project.get("genre"),
            status=project.get("status"),
            target_words=(int(project["target_words"]) if project.get("target_words") is not None else None),
            synced_at=_now_iso(),
            chapter_count=len(chapters),
            draft_count=len(drafts),
            story_state_versions=sorted(set(story_state_versions)),
            content_dir=str(self.content_dir),
        )
        try:
            self._write_manifest(book_dir, manifest)
            result.manifest_written = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"manifest: {exc}")
            log.warning("manifest write failed: %s", exc)

        log.info(
            "push_book: book_dir=%s files=%d errors=%d",
            book_dir,
            result.total_files,
            len(result.errors),
        )
        return book_dir, result

    # ========================================================================
    # list：枚举内容仓下 book 子目录
    # ========================================================================

    def list_books(self) -> list[dict[str, Any]]:
        """列出内容仓下所有 book 子目录的基础信息。

        仅返回 ``{slug, project_id?, synced_at?}``，读不到 manifest 的子目录
        仍返回，仅缺字段。``genres/`` 是题材包命名空间（非书），跳过。
        """
        if not self.content_dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(self.content_dir.iterdir()):
            if not p.is_dir() or p.name == GENRES_DIR:
                continue
            slug = p.name
            entry: dict[str, Any] = {"slug": slug, "path": str(p)}
            mpath = manifest_path(p)
            if mpath.exists():
                try:
                    m = Manifest.from_dict(json.loads(mpath.read_text(encoding="utf-8")))
                    entry["project_id"] = m.project_id
                    entry["synced_at"] = m.synced_at
                    entry["name"] = m.name
                except Exception as exc:  # noqa: BLE001
                    log.warning("manifest read failed for %s: %s", p, exc)
            out.append(entry)
        return out

    # ========================================================================
    # 题材包 push / pull / list（题材库 P3a）
    # ========================================================================

    def push_genre_pack(self, pack_id: str) -> tuple[Path, GenreSyncResult]:
        """把一个题材包（payload + 元信息）写到 ``genres/<slug>/pack.json``。

        幂等：同一 ``pack_id`` 复用既有 ``genres/<slug>/`` 目录（按 pack.json 里的
        ``pack_id`` 反查），重复 push 覆盖同一文件、不产生 ``-2`` 目录；并刷新
        内容仓根 ``manifest.json`` 的 ``genres`` 段（同步时间 + 全量 pack 清单）。

        写侧严格：DB 里的 payload 不合 genre-pack schema → ``ValueError``（不落盘，
        避免把非法 pack.json 写进内容仓；错误串与 API 422 口径一致）。

        返回 ``(pack_dir, GenreSyncResult)``。pack 不存在 → ``ValueError``。
        """
        svc = GenrePackService(self.db_path)
        pack = svc.get(pack_id)
        if pack is None:
            raise ValueError(f"genre pack {pack_id!r} not found")

        payload = pack.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        schema_errors = validate_payload(payload)
        if schema_errors:
            raise ValueError(
                "genre pack payload 不合 schema，拒绝写入内容仓："
                + "；".join(schema_errors)
            )

        pack_dir = self._prepare_genre_dir(pack)
        result = GenreSyncResult(pack_id=pack_id, slug=pack_dir.name, pack_dir=pack_dir)

        source_path = pack.get("source_path") or f"{GENRES_DIR}/{pack_dir.name}"
        doc = GenrePackDoc(
            pack_id=pack_id,
            name=str(pack.get("name") or ""),
            genre_tag=str(pack.get("genre_tag") or ""),
            version=int(pack.get("version") or 1),
            payload=payload,
            source_path=str(source_path),
            synced_at=_now_iso(),
        )
        try:
            _write_json(genre_pack_path(pack_dir), doc.to_dict())
            result.pack_written = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"pack.json: {exc}")
            log.warning("genre push pack.json failed: %s", exc)

        try:
            self._write_genre_manifest()
            result.manifest_written = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"manifest genres: {exc}")
            log.warning("genre manifest write failed: %s", exc)

        log.info(
            "push_genre_pack: pack_id=%s dir=%s files=%d errors=%d",
            pack_id,
            pack_dir,
            result.total_files,
            len(result.errors),
        )
        return pack_dir, result

    def pull_genre_pack(self, slug_or_path: str) -> GenrePullResult:
        """读内容仓 ``pack.json`` → 校验 → DB 侧创建（无则）或更新（有则）。

        入参可以是：

        - slug（如 ``pack-trans-v1``）→ ``<content-dir>/genres/<slug>/pack.json``；
        - 目录路径 → 该目录下 ``pack.json``；
        - ``pack.json`` 文件路径。

        语义：

        - payload 过 :func:`packages.core.genre.validate_payload`（不合 → ``ValueError``，
          DB 不写）；
        - ``pack_id`` 取文件字段；缺失时回退目录名（容忍手工策展的 pack.json）；
        - DB 无该 pack → 创建（``created=True``）；
        - 已有 → 逐字段比对：完全一致 → no-op（``updated=False``，**不**产生新版本，
          保证重复 pull 幂等）；payload 变更 → 更新且 version 自增；仅元信息变更 →
          更新但不升版本（与 :meth:`GenrePackService.update` 语义一致）；
        - 对内容仓**只读**：pull 不写任何文件、不刷新根 manifest。

        文件缺失 / JSON 非法 → ``ValueError``。返回 :class:`GenrePullResult`。
        """
        source = self._resolve_genre_source(slug_or_path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"pack.json 读取失败（{source}）：{exc}") from exc

        doc = GenrePackDoc.from_dict(raw)
        slug = source.parent.name

        pack_id = (doc.pack_id or "").strip() or slug
        name = (doc.name or "").strip() or slug or pack_id
        genre_tag = (doc.genre_tag or "").strip() or name

        schema_errors = validate_payload(doc.payload)
        if schema_errors:
            raise ValueError(
                f"pack.json payload 不合 schema（{source}）：" + "；".join(schema_errors)
            )

        source_path = (doc.source_path or "").strip() or f"{GENRES_DIR}/{slug}"

        result = GenrePullResult(
            pack_id=pack_id,
            slug=slug,
            source=source,
            file_version=doc.version,
        )

        svc = GenrePackService(self.db_path)
        existing = svc.get(pack_id)
        if existing is None:
            created = svc.create(
                GenrePackCreate(
                    name=name,
                    genre_tag=genre_tag,
                    payload=doc.payload,
                    pack_id=pack_id,
                    source_path=source_path,
                )
            )
            result.created = True
            result.version = int(created.get("version") or 1)
            log.info("pull_genre_pack: created pack_id=%s from=%s", pack_id, source)
            return result

        payload_same = existing.get("payload") == doc.payload
        meta_same = (
            str(existing.get("name") or "") == name
            and str(existing.get("genre_tag") or "") == genre_tag
            and (existing.get("source_path") or None) == source_path
        )
        if payload_same and meta_same:
            result.version = int(existing.get("version") or 0)
            log.info("pull_genre_pack: unchanged pack_id=%s (%s)", pack_id, source)
            return result

        updated = svc.update(
            pack_id,
            GenrePackUpdate(
                name=name,
                genre_tag=genre_tag,
                # payload 未变 → 传 None：不升版本、不重写 payload_json
                payload=None if payload_same else doc.payload,
                source_path=source_path,
            ),
        )
        result.updated = True
        result.version = int((updated or existing).get("version") or 0)
        log.info("pull_genre_pack: updated pack_id=%s from=%s", pack_id, source)
        return result

    def list_genres(self) -> list[dict[str, Any]]:
        """枚举 ``genres/`` 下带 ``pack.json`` 的题材包目录（按 slug 排序）。

        读不到 / 解析不了的 pack.json 仍返回该目录（仅缺字段），不抛错。
        """
        gdir = genres_dir(self.content_dir)
        if not gdir.exists():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(gdir.iterdir()):
            if not p.is_dir():
                continue
            entry: dict[str, Any] = {"slug": p.name, "path": str(p)}
            ppath = genre_pack_path(p)
            if ppath.exists():
                entry["pack_path"] = str(ppath)
                try:
                    doc = GenrePackDoc.from_dict(
                        json.loads(ppath.read_text(encoding="utf-8"))
                    )
                    entry["pack_id"] = doc.pack_id
                    entry["name"] = doc.name
                    entry["genre_tag"] = doc.genre_tag
                    entry["version"] = doc.version
                    entry["synced_at"] = doc.synced_at
                except Exception as exc:  # noqa: BLE001
                    log.warning("pack.json read failed for %s: %s", p, exc)
            out.append(entry)
        return out

    # ========================================================================
    # 内部 helper
    # ========================================================================

    def _prepare_book_dir(self, project: dict[str, Any], story_state_versions: list[int]) -> tuple[Path, Manifest]:
        """派生 slug、去重、建子目录；不写 manifest。

        slug 复用优先级：
        1. 若 ``content_dir`` 下某 book 子目录的 ``manifest.json.project_id`` 与
           本项目一致，则直接复用该 slug（保证同一书 push 多次路径稳定）。
        2. 否则走 :func:`derive_slug` + :func:`unique_slug` 的去重逻辑。
        """
        project_id = str(project["project_id"])
        name = str(project.get("name") or "")
        base_slug = derive_slug(name, project_id)

        slug: str | None = None
        if self.content_dir.exists():
            for child in self.content_dir.iterdir():
                if not child.is_dir():
                    continue
                mp = manifest_path(child)
                if not mp.exists():
                    continue
                try:
                    existing = Manifest.from_dict(json.loads(mp.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    continue
                if existing.project_id == project_id:
                    slug = child.name
                    break
        if slug is None:
            slug = unique_slug(base_slug, self.content_dir)

        book_dir = self.content_dir / slug
        for sub in BOOK_DIRS:
            (book_dir / sub).mkdir(parents=True, exist_ok=True)
        book_dir.mkdir(parents=True, exist_ok=True)
        manifest = Manifest(
            project_id=project_id,
            name=name,
            slug=slug,
            genre=project.get("genre"),
            status=project.get("status"),
            target_words=(int(project["target_words"]) if project.get("target_words") is not None else None),
            synced_at=_now_iso(),
            chapter_count=0,
            draft_count=0,
            story_state_versions=list(story_state_versions),
            content_dir=str(self.content_dir),
        )
        return book_dir, manifest

    @staticmethod
    def _write_manifest(book_dir: Path, manifest: Manifest) -> None:
        _write_json(manifest_path(book_dir), manifest.to_dict())

    # ---------------------------- 题材包内部 helper -------------------------

    def _prepare_genre_dir(self, pack: dict[str, Any]) -> Path:
        """定位（或创建）题材包目录，返回 pack 目录。

        slug 复用优先级（与书侧同款思路）：

        1. 若 ``genres/`` 下某子目录的 ``pack.json.pack_id`` 与本 pack 一致 →
           直接复用该目录（保证同一 pack push 多次路径稳定、不产生 ``-2``）；
        2. 否则走 :func:`derive_genre_slug`（name 归一化；中文/空 → ``pack-<id 末 8>``）
           + :func:`unique_slug` 去重。
        """
        pack_id = str(pack.get("pack_id") or "")
        gdir = genres_dir(self.content_dir)
        gdir.mkdir(parents=True, exist_ok=True)

        for child in sorted(gdir.iterdir()):
            if not child.is_dir():
                continue
            ppath = genre_pack_path(child)
            if not ppath.exists():
                continue
            try:
                doc = GenrePackDoc.from_dict(json.loads(ppath.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
            if doc.pack_id and doc.pack_id == pack_id:
                return child

        base_slug = derive_genre_slug(str(pack.get("name") or ""), pack_id)
        pack_dir = gdir / unique_slug(base_slug, gdir)
        pack_dir.mkdir(parents=True, exist_ok=True)
        return pack_dir

    def _write_genre_manifest(self) -> None:
        """刷新内容仓根 ``manifest.json`` 的 ``genres`` 段（同步时间 + pack 清单）。

        合并语义：读既有根 manifest（容错），保留其它顶层键，只覆盖 ``genres``；
        ``packs`` 由 :meth:`list_genres` 现场扫描重建（内容仓真实状态为准，不依赖
        增量记账）。根 manifest 此前不存在时新建。
        """
        mpath = content_manifest_path(self.content_dir)
        manifest: dict[str, Any] = {}
        if mpath.exists():
            try:
                loaded = json.loads(mpath.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    manifest = loaded
            except (OSError, ValueError) as exc:
                log.warning("content manifest read failed (%s): %s", mpath, exc)

        manifest.setdefault("content_dir", str(self.content_dir))
        manifest["genres"] = {
            "synced_at": _now_iso(),
            "packs": [
                {k: v for k, v in entry.items() if k != "path" and k != "pack_path"}
                for entry in self.list_genres()
            ],
        }
        _write_json(mpath, manifest)

    def _resolve_genre_source(self, slug_or_path: str) -> Path:
        """``slug 或路径`` → ``pack.json`` 绝对 / 相对路径（存在性校验）。"""
        raw = (slug_or_path or "").strip()
        if not raw:
            raise ValueError("genre-pull 需要 slug 或路径（非空）")
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            ppath = genre_pack_path(candidate)
            if ppath.exists():
                return ppath
            raise ValueError(f"目录下没有 pack.json：{candidate}")
        # 非路径 → 当 slug 处理（相对内容仓 genres/ 解析）
        ppath = genre_pack_path(genres_dir(self.content_dir) / raw)
        if not ppath.exists():
            raise ValueError(
                f"找不到题材包 pack.json：{ppath}（可用 slug 或直接给目录 / 文件路径）"
            )
        return ppath


# ---------------------------------------------------------------------------
# 模块级 helper：卷合并 txt（复用 build_txt 的章节取数 / 标题 / BOM 规则）
# ---------------------------------------------------------------------------


def build_volume_txt(
    db_path: str,
    project_id: str,
    volume_row: dict[str, Any],
    chapters_in_volume: list[dict[str, Any]],
) -> bytes:
    """卷合并 txt：按 chapter number ASC 把该卷章节拼起来，复用 build_txt 的 BOM
    与章节标题格式。

    实现方式：复用 :func:`packages.core.exporter.builder.build_txt`
    ``ExportScope(kind="book")`` 的取数路径 + 章节标题 helper，但仅渲染
    ``chapters_in_volume`` 这部分——避免重复实现 BOM / 章节标题逻辑。

    章节内容为空（无 draft）时，保留标题与空行占位，与 build_txt 行为一致。
    """
    # V3.9 批次 5.14：改引 exporter 公开 helper（原 ``_chapter_heading`` /
    # ``_latest_draft_content`` / ``_project_name`` 私有名已提升为公开名，
    # 由 ``packages.core.exporter`` 包面导出；旧下划线名保留别名）。
    from packages.core.exporter import (
        chapter_heading,
        latest_draft_content,
        project_name,
    )

    lines: list[str] = []
    pname = project_name(db_path, project_id)
    if pname:
        lines.append(pname)
        lines.append("=" * max(len(pname), 8))
        lines.append("")
    # 卷首注释：让单卷文件有独立可读性（与 build_txt 不同点）。
    vol_num = volume_row.get("number")
    vol_title = volume_row.get("title")
    vol_label = f"第{vol_num}卷"
    if vol_title:
        vol_label += f" {vol_title}"
    lines.append(vol_label)
    lines.append("=" * max(len(vol_label), 8))
    lines.append("")
    for ch in chapters_in_volume:
        heading = chapter_heading(int(ch["number"]), ch.get("title"))
        lines.append(heading)
        lines.append("-" * max(len(heading), 8))
        body = latest_draft_content(db_path, ch["chapter_id"])
        lines.append(body if body else "（本章尚无正文）")
        lines.append("")
    body_str = "\n".join(lines).rstrip() + "\n"
    return ("\ufeff" + body_str).encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    """原子写 JSON：``utf-8`` + ``ensure_ascii=False`` + indent。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
