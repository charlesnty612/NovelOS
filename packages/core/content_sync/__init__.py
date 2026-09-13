"""core.content_sync —— 内容仓同步器（V1.0；题材包 P3a）。

把运行库 SQLite 中的某本书（project）按「每本书一个独立子目录」的结构
同步到内容仓（默认 ``D:/zcodeproject/NovelOS-Content``），实现软件 / 内容
两仓分离下的内容版本管理闭环；题材包（genre_pack）走 ``genres/`` 命名空间，
以 ``genres/<slug>/pack.json`` 为运行面载体。

目录约定详见 :mod:`packages.core.content_sync.paths`；具体同步逻辑见
:mod:`packages.core.content_sync.service`。

设计要点：

- **只读运行库**：同步过程中对 ``data/novelos.db`` 仅 SELECT，不修改 schema
  / 不写 migration / 不调 LLM / 不启动服务。（例外：``genre-pull`` 的语义
  就是把内容仓 pack.json 导入运行库，会写 ``genre_packs`` 行——其余命令仍
  全只读。）
- **只新增内容仓**：不在内容仓已有文件上做就地破坏；只创建
  ``<content-dir>/<slug>/`` 新子目录。
- **不同书严格隔离**：每本书通过独立子目录隔离；slug 冲突时自动追加
  ``-2`` / ``-3`` 后缀，不复用其它书目录。
- **幂等可重跑**：重复执行 ``push`` 只覆盖或跳过已有产物，不翻倍；
  ``genre-push`` 按 pack_id 复用既有目录，``genre-pull`` 内容一致时 no-op。
- **复用既有导出能力**：整书/单章 txt 直接走
  :func:`packages.core.exporter.builder.build_txt`；JSON 备份包直接走
  :class:`packages.core.backup.BackupService.export_project`。
"""

from __future__ import annotations

from .paths import (
    BOOK_DIRS,
    BOOK_SLUG_HINT,
    CONTENT_MANIFEST_FILENAME,
    DEFAULT_CONTENT_DIR,
    GENRE_PACK_FILENAME,
    GENRES_DIR,
    GenrePackDoc,
    Manifest,
    content_manifest_path,
    genre_pack_path,
    genres_dir,
    manifest_path,
)
from .service import (
    ContentSyncService,
    GenrePullResult,
    GenreSyncResult,
    SyncResult,
)
from .slug import derive_genre_slug, derive_slug

__all__ = [
    "BOOK_DIRS",
    "BOOK_SLUG_HINT",
    "CONTENT_MANIFEST_FILENAME",
    "DEFAULT_CONTENT_DIR",
    "GENRES_DIR",
    "GENRE_PACK_FILENAME",
    "ContentSyncService",
    "GenrePackDoc",
    "GenrePullResult",
    "GenreSyncResult",
    "Manifest",
    "SyncResult",
    "content_manifest_path",
    "derive_genre_slug",
    "derive_slug",
    "genre_pack_path",
    "genres_dir",
    "manifest_path",
]
