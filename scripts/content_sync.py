#!/usr/bin/env python
"""内容仓同步器 CLI —— 把运行库某本书 / 某个题材包同步到内容仓。

用法
----

::

    # 列出内容仓下所有 book 子目录
    python scripts/content_sync.py --content-dir D:/zcodeproject/NovelOS-Content list

    # 仅建书目录骨架 + manifest（不写其它产物，便于先验位置 / slug）
    python scripts/content_sync.py \\
        --db data/novelos.db \\
        --content-dir D:/zcodeproject/NovelOS-Content \\
        init --project prj_xxxxxxxx

    # 全量同步一本书到内容仓（幂等可重跑）
    python scripts/content_sync.py \\
        --db data/novelos.db \\
        --content-dir D:/zcodeproject/NovelOS-Content \\
        push --project prj_xxxxxxxx

    # 题材包：DB → 内容仓 genres/<slug>/pack.json（幂等；刷新根 manifest 的 genres 段）
    python scripts/content_sync.py \\
        --db data/novelos.db \\
        --content-dir D:/zcodeproject/NovelOS-Content \\
        genre-push genre-male-quicktrans-v1

    # 题材包：内容仓 pack.json → DB（无则创建；有则 payload 变更 → version 自增）
    python scripts/content_sync.py \\
        --db data/novelos.db \\
        --content-dir D:/zcodeproject/NovelOS-Content \\
        genre-pull pack-trans-v1

参数
----

- ``--db`` SQLite 运行库路径（默认 ``data/novelos.db``）。
- ``--content-dir`` 内容仓根目录（默认 ``D:/zcodeproject/NovelOS-Content``，
  与软件仓 ``D:/zcodeproject/NovelOS`` 同盘；不同盘时显式覆盖）。

子命令
----

- ``list``：列出内容仓下所有 book 子目录（slug / project_id / synced_at / name）。
- ``init``：仅创建书目录骨架与 manifest，不写其它产物。
- ``push``：全量同步一本书到内容仓；幂等可重跑。
- ``genre-push <pack_id>``（别名 ``--pack``）：把题材包 payload + 元信息写到
  ``genres/<slug>/pack.json``，并刷新内容仓根 ``manifest.json`` 的 ``genres`` 段
  （同步时间 + pack 清单）；幂等可重跑。
- ``genre-pull <slug 或路径>``：读内容仓 ``pack.json`` → schema 校验 → DB 侧
  创建（无则）或更新（有则 payload 变更 → version 自增）；内容一致 → no-op。
  对内容仓只读。

退出码
----

- ``0``：成功。
- ``1``：参数错误 / project 或题材包不存在 / 数据库读不开 / pack.json 缺失或不合
  schema（写侧严格：非法 payload 不落盘、不入库）。
- ``2``：部分产物写入失败（产物计数 > 0 但 ``errors`` 非空）。

输出目录约定
------------

::

    <content-dir>/
        manifest.json                  # 内容仓根清单：genres 段（同步时间 + pack 清单）
        genres/
            <pack-slug>/
                pack.json              # 运行时 payload + 元信息（genre-push 写出）
        <book-slug>/
            novel/
                全书-<slug>.txt        # 整书正文（复用 build_txt kind=book）
                第<N>卷-<title>.txt    # 按卷合并（若该书有 volumes）
                第<N>章-<title>.txt    # 每章正文（复用 build_txt kind=chapter）
            canon/
                characters.json
                world_rules.json
                plot_events.json
                timeline_events.json
                volumes.json
            story_state/
                <state_version>.json   # 每版本 story_states 快照（按版本号去重）
            backup/
                <slug>-backup-<YYYYmmdd-HHMMSS>.json
            manifest.json

``book-slug`` 派生规则：

1. project name 做 NFKD 归一化 + lowercase + 仅保留 ``[a-z0-9-]``；
2. 连续 ``-`` 折叠、首尾 ``-`` 删除；
3. 若结果为空（纯中文 / 纯特殊字符），fallback 到 ``book-<project_id 末 8 字符>``；
4. 与内容仓现有 book 子目录冲突时追加 ``-2`` / ``-3`` 后缀（确定性）。

``pack-slug`` 派生规则（题材包）：

1. pack name 走同一条归一化管线（NFKD + lowercase + ``[a-z0-9-]``）；
2. 若结果为空（纯中文如「男主快穿」/ 空 name），fallback 到
   ``pack-<pack_id 末 8 字符>``；
3. 命中 Windows 设备保留名（CON / NUL / COM1…）加 ``pack-`` 前缀；
4. 冲突追加 ``-2`` / ``-3``；同一 ``pack_id`` 再次 push 时**复用**既有目录
   （按 pack.json 里的 pack_id 反查），故 slug 只在首次 push 时确定一次。

幂等性：

- novel/ 章节 / 卷 / 整书 txt：按文件名直接覆盖（每次内容可能变 → 覆盖合理）；
- canon/*.json：覆盖式；
- story_state/<version>.json：按 state_version 命名，已存在跳过；
- backup/<slug>-backup-<ts>.json：每次 push 新建（带时间戳，保留历史快照）；
- manifest.json：覆盖式；
- genres/<slug>/pack.json：覆盖式（同一 pack_id 恒定路径）；
- 内容仓根 manifest.json 的 genres 段：整体重建（packs = 现场扫描 ``genres/``）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让 ``python scripts/content_sync.py`` 直接运行也能 import packages.* 。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.core.content_sync import (  # noqa: E402
    DEFAULT_CONTENT_DIR,
    ContentSyncService,
)

DEFAULT_DB_PATH = _REPO_ROOT / "data" / "novelos.db"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="content_sync",
        description="把运行库（SQLite）中某本书（project）/ 某个题材包同步到内容仓。",
    )
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite 运行库路径（默认 {DEFAULT_DB_PATH}）",
    )
    p.add_argument(
        "--content-dir",
        default=str(DEFAULT_CONTENT_DIR),
        help=f"内容仓根目录（默认 {DEFAULT_CONTENT_DIR}）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出内容仓下所有 book 子目录")

    p_init = sub.add_parser("init", help="仅建书目录骨架 + manifest")
    p_init.add_argument("--project", required=True, help="project_id")

    p_push = sub.add_parser("push", help="全量同步一本书到内容仓（幂等）")
    p_push.add_argument("--project", required=True, help="project_id")

    p_gpush = sub.add_parser(
        "genre-push",
        help="把题材包 payload + 元信息写到内容仓 genres/<slug>/pack.json（幂等）",
    )
    p_gpush.add_argument(
        "pack_id_pos", nargs="?", default=None, metavar="pack_id", help="题材包 pack_id"
    )
    p_gpush.add_argument(
        "--pack", default=None, help="题材包 pack_id（与位置参数等价，二选一）"
    )

    p_gpull = sub.add_parser(
        "genre-pull",
        help="读内容仓 pack.json 并导入运行库（无则创建 / 有则更新）",
    )
    p_gpull.add_argument(
        "source",
        metavar="slug-or-path",
        help="题材包目录 slug（相对 <content-dir>/genres/）或 pack.json / 目录路径",
    )

    return p


def _cmd_list(args: argparse.Namespace) -> int:
    svc = ContentSyncService(args.db, args.content_dir)
    books = svc.list_books()
    print(json.dumps(books, ensure_ascii=False, indent=2))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    svc = ContentSyncService(args.db, args.content_dir)
    book_dir, manifest = svc.init_book(args.project)
    out = {"book_dir": str(book_dir), "manifest": manifest.to_dict()}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    svc = ContentSyncService(args.db, args.content_dir)
    book_dir, result = svc.push_book(args.project)
    summary = {
        "book_dir": str(book_dir),
        "slug": result.slug,
        "total_files": result.total_files,
        "chapters_written": result.chapters_written,
        "volumes_written": result.volumes_written,
        "canon_files_written": result.canon_files_written,
        "story_states_written": result.story_states_written,
        "story_states_skipped": result.story_states_skipped,
        "backup_files_written": result.backup_files_written,
        "manifest_written": result.manifest_written,
        "errors": result.errors,
    }
    # 同时把最终 manifest 内容打到 stdout，方便调用方对账。
    mpath = book_dir / "manifest.json"
    if mpath.exists():
        try:
            summary["manifest"] = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.errors and result.total_files == 0:
        return 1
    if result.errors:
        return 2
    return 0


def _cmd_genre_push(args: argparse.Namespace) -> int:
    pack_id = (args.pack or args.pack_id_pos or "").strip()
    if not pack_id:
        print(
            "ERROR: genre-push 需要 pack_id（位置参数或 --pack）",
            file=sys.stderr,
        )
        return 1
    svc = ContentSyncService(args.db, args.content_dir)
    pack_dir, result = svc.push_genre_pack(pack_id)
    summary = {
        "pack_id": result.pack_id,
        "slug": result.slug,
        "pack_dir": str(pack_dir),
        "pack_path": str(pack_dir / "pack.json"),
        "pack_written": result.pack_written,
        "manifest_written": result.manifest_written,
        "total_files": result.total_files,
        "errors": result.errors,
    }
    # 同时把最终 pack.json 内容打到 stdout，方便调用方对账。
    ppath = pack_dir / "pack.json"
    if ppath.exists():
        try:
            summary["pack"] = json.loads(ppath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.errors and result.total_files == 0:
        return 1
    if result.errors:
        return 2
    return 0


def _cmd_genre_pull(args: argparse.Namespace) -> int:
    svc = ContentSyncService(args.db, args.content_dir)
    result = svc.pull_genre_pack(args.source)
    summary = {
        "pack_id": result.pack_id,
        "slug": result.slug,
        "source": str(result.source),
        "created": result.created,
        "updated": result.updated,
        "version": result.version,
        "file_version": result.file_version,
        "errors": result.errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.errors:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "list":
            return _cmd_list(args)
        if args.cmd == "init":
            return _cmd_init(args)
        if args.cmd == "push":
            return _cmd_push(args)
        if args.cmd == "genre-push":
            return _cmd_genre_push(args)
        if args.cmd == "genre-pull":
            return _cmd_genre_pull(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown subcommand: {args.cmd}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
