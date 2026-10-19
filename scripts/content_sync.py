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

    # 题材包版本漂移巡检（只读；不写运行库、不写内容仓）
    python scripts/content_sync.py \\
        --db data/novelos.db \\
        --content-dir D:/zcodeproject/NovelOS-Content \\
        genre-doctor
    python scripts/content_sync.py ... genre-doctor --json      # 机器可读

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
  对内容仓只读。**若运行库 version 高于内容仓文件 version（反向漂移），会向
  stderr 打出告警并在返回结构的 ``warnings`` 里列出——但 pull 的既有行为
  （以内容仓为准）完全不变。**
- ``genre-doctor``（别名 ``check-drift``）：**只读**巡检题材包版本漂移——比对
  内容仓 ``genres/<slug>/pack.json`` 与运行库 ``genre_packs`` 同 ``pack_id`` 行的
  ``version``，报出四类状态：``content_newer`` / ``db_newer``（版本不同，指明
  哪边新）/ ``content_only``（内容仓有、DB 无）/ ``db_only``（DB 有、内容仓无）；
  一致则静默通过。加 ``--json`` 输出 ``{"drift": [...], "checked": N, "ok": bool,
  "errors": [...]}``。

退出码
----

- ``0``：成功（对 ``genre-doctor``：**无版本漂移**）。
- ``1``：参数错误 / project 或题材包不存在 / 数据库读不开 / pack.json 缺失或不合
  schema（写侧严格：非法 payload 不落盘、不入库）。（对 ``genre-doctor``：
  **检出版本漂移**。）
- ``2``：部分产物写入失败（产物计数 > 0 但 ``errors`` 非空）。（对
  ``genre-doctor``：**巡检自身出错**——运行库读不开 / ``pack.json`` 不可解析等；
  巡检错误优先于漂移。）

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
    DIRECTION_CONTENT_NEWER,
    DIRECTION_CONTENT_ONLY,
    DIRECTION_DB_NEWER,
    DIRECTION_DB_ONLY,
    ContentSyncService,
    detect_genre_drift,
)

DEFAULT_DB_PATH = _REPO_ROOT / "data" / "novelos.db"

# ``genre-doctor`` 的退出码口径（**与本 CLI 其余子命令不同**，故单列）：
#   0 = 无漂移（且无巡检错误） / 1 = 有漂移 / 2 = 巡检自身出错（异常）
DOCTOR_EXIT_OK = 0
DOCTOR_EXIT_DRIFT = 1
DOCTOR_EXIT_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="content_sync",
        description="把运行库（SQLite）中某本书（project）/ 某个题材包同步到内容仓。",
        epilog=(
            "退出码：\n"
            "  0  成功（对 genre-doctor：无版本漂移）\n"
            "  1  参数错误 / 对象不存在 / pack.json 缺失或不合 schema\n"
            "     （对 genre-doctor：检出版本漂移）\n"
            "  2  部分产物写入失败\n"
            "     （对 genre-doctor：巡检自身出错，如运行库不可读）"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    p_doctor = sub.add_parser(
        "genre-doctor",
        aliases=["check-drift"],
        help=(
            "巡检题材包版本漂移（内容仓 pack.json ↔ 运行库 genre_packs；只读，不写任何一侧）"
            "——退出码 0=无漂移 / 1=有漂移 / 2=巡检出错"
        ),
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="机器可读输出（JSON）：{drift, checked, ok, errors}",
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
        "warnings": result.warnings,
    }
    # 告警走 stderr（stdout 只留机器可读 JSON），**不改变 pull 既有行为**。
    for w in result.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.errors:
        return 2
    return 0


def _format_drift_text(report, db_path: str, content_dir: str) -> str:
    """人读形态：逐行列出漂移项 + 汇总行。"""
    lines: list[str] = [
        "题材包版本漂移巡检（只读）",
        f"  内容仓: {content_dir}",
        f"  运行库: {db_path}",
        f"  检查题材包数: {report.checked}",
        "",
    ]
    if not report.drift:
        lines.append("未发现版本漂移。")
    else:
        for item in report.drift:
            where = item.content_path or "（内容仓无 pack.json）"
            if item.direction == DIRECTION_DB_ONLY and item.db_source_path:
                where = f"（内容仓无 pack.json；DB 记录来源路径 {item.db_source_path}）"
            if item.direction == DIRECTION_CONTENT_NEWER:
                detail = f"内容仓 v{item.content_version} > DB v{item.db_version}"
            elif item.direction == DIRECTION_DB_NEWER:
                detail = f"DB v{item.db_version} > 内容仓 v{item.content_version}"
            elif item.direction == DIRECTION_CONTENT_ONLY:
                detail = f"内容仓 v{item.content_version}（运行库无此 pack_id）"
            elif item.direction == DIRECTION_DB_ONLY:
                detail = f"DB v{item.db_version}（内容仓无此 pack.json）"
            else:
                detail = f"（未知方向：{item.direction}）"
            lines.append(f"  [{item.direction}] {item.pack_id}  {detail}  ← {where}")
    if report.errors:
        lines.append("")
        lines.append("巡检错误：")
        for e in report.errors:
            lines.append(f"  - {e}")
    lines.append("")
    lines.append(f"漂移 {len(report.drift)} 处 / 检查 {report.checked} 个 / 巡检错误 {len(report.errors)} 处")
    return "\n".join(lines)


def _cmd_genre_doctor(args: argparse.Namespace) -> int:
    """版本漂移巡检（只读）。

    退出码口径（**与本 CLI 其余子命令不同**）：
    ``0`` = 无漂移；``1`` = 有漂移；``2`` = 巡检自身出错（异常）。
    巡检错误（``errors`` 非空）优先于漂移 ⇒ 返回 ``2``。
    """
    try:
        report = detect_genre_drift(args.db, args.content_dir)
    except Exception as exc:  # noqa: BLE001 - CLI 边界：异常 → 诊断 + 退出码 2
        print(f"ERROR: 题材包漂移巡检失败：{exc}", file=sys.stderr)
        return DOCTOR_EXIT_ERROR

    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_drift_text(report, args.db, args.content_dir))

    if report.errors:
        return DOCTOR_EXIT_ERROR
    if report.drift:
        return DOCTOR_EXIT_DRIFT
    return DOCTOR_EXIT_OK


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
        if args.cmd in ("genre-doctor", "check-drift"):
            return _cmd_genre_doctor(args)
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
