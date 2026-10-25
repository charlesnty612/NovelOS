"""从**加密备份**恢复 NovelOS 主库（原子替换 + 恢复后校验）。

用法::

    python scripts/restore_db.py --enc <密文.db.enc> --out <恢复目标.db> [--key-file <密钥>]
    python scripts/restore_db.py --enc ... --out ... --expect-tables 43

为什么需要它（2026-10-23 批次三十一，D-2 / D-3）
------------------------------------------------
备份「**有写无读**」：仓内此前**只有导出、没有恢复工具与 runbook**，机器坏了只能
手工拼 openssl 命令（路径/顺序/校验全靠记忆）。而手工拼错的后果被实测出来：

============================================  ==========================================
错误形态                                      后果
============================================  ==========================================
目标库旁**残留** ``-wal`` / ``-shm``          SQLite 用旧 WAL 覆盖新主库 ⇒ 49 表 → 1 表、
                                              ``projects`` 表不存在，而 ``integrity_check``
                                              **仍报 ok** ⇒ 恢复**静默失效**且校验报绿
密文**截断 / 解密失败**                       openssl exit 1，但目标文件**已被截断重写**
                                              ⇒ **解密失败反而破坏活库**（无 temp+rename）
============================================  ==========================================

⇒ 本工具的四条硬规则
1. **解密到目标同目录的临时文件**（同卷 ⇒ ``os.replace`` 才是原子替换）；
2. **原子替换**（``os.replace``）⇒ 目标要么是旧的、要么是新的，**没有中间态**；
3. **恢复前删掉目标旁的 ``-wal``/``-shm``**（否则旧 WAL 会被回放，覆盖新主库）；
4. **恢复后校验**（``PRAGMA integrity_check`` + **表数核对**）⇒ 全过才报成功。

**失败绝不碰原库**：任何一步失败 ⇒ 临时文件清理、**目标库保持原样**、退出码非 0；
替换之后若校验不过 ⇒ 用替换前的快照**回滚**。

⚠️ **目标库被占用时必须安静地中止**（2026-10-23 对抗审查 T3-1/T3-3）：
另一个进程（= 正在跑的 NovelOS）开着目标库时，Windows 上 ``-wal``/``-shm``
**删不掉**（``WinError 32``）⇒ 旧实现让异常冒泡成 traceback、退出码 1，
与本脚本 docstring 承诺的退出码表**不符**。现在：
1. 删 sidecar **之前**先对目标执行 ``PRAGMA wal_checkpoint(TRUNCATE)``
   （把 WAL 里的提交**落进主库** ⇒ 删 sidecar 不丢数据；这一步同时是「占用预检」）；
2. checkpoint 失败（被占用）⇒ **中止**，退出码 2，不硬删、不冒泡 traceback；
3. sidecar 删不掉 ⇒ 单文件失败**记录 + 继续**，整体失败 ⇒ 退出码 2 + 明确文案。

退出码（**可区分**失败类别，CI 可据此分流）::

    0 = 成功
    2 = 用法 / 前置条件错误（密文或密钥不存在、**目标库被占用**、原子替换失败……）
    3 = 解密失败（密文损坏 / 密钥不配对）
    4 = 完整性校验未通过（integrity_check != ok）
    5 = 表数不符（**仅在显式给了 --expect-tables 时**才核对）

⚠️ **只操作用户显式给出的路径**：不写任何真实库 / 真实备份目录（测试亦然）。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 库落点闸（BUG-59 / 落码批 C-02、C-03）：从 ``packages.core.db`` 取**唯一实现点**
# ⇒ 库落点判定只实现一次，**不与** ``scripts/guard_repo_write_path.py`` 的**写落点**闸
# 混用：后者管「产物写哪」（含 ``tmp/``），本闸管「库落哪」（**仅** ``data/``）。
# ⚠️ 本 import 必须在**仓根入 sys.path 之后**（否则直接 `python scripts/restore_db.py` ⇒ ImportError）。
from packages.core.db import RepoLocalDbPathError, check_db_path  # noqa: E402
from scripts.backup_db import (  # noqa: E402
    CIPHER,
    DEFAULT_KEY_FILE,
    PBKDF2_ITER,
    find_openssl,
)

#: 表数核对**默认关闭**（``--expect-tables`` 不给就不核对）。
#:
#: ⚠️ **刻意不写死张数**（2026-10-23 对抗审查 T3-4）：``packages/core/db.py::count_tables``
#: 的 docstring 明写「本 docstring 不写死具体张数（A-009）：表数在每次迁移后即漂移，
#: 写死的数字必然过期」⇒ 这里若写死 43，每次加表都会与「期望值」静默分叉。
#: ⇒ 默认**只打印实际表数**；显式给了 ``--expect-tables`` 才核对（不符仍退 5）。
DEFAULT_EXPECT_TABLES: int | None = None

_TABLE_COUNT_QUERY = (
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'chapter_fts%'"
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DECRYPT_FAILED = 3
EXIT_INTEGRITY_FAILED = 4
EXIT_TABLE_COUNT_MISMATCH = 5


class TargetBusy(RuntimeError):
    """目标库被占用（另一个进程正开着它）⇒ 无法安全恢复。"""


def _err(message: str) -> None:
    """诊断走 stderr（stdout 留给「做了什么」的流水）。"""
    print(message, file=sys.stderr)


def decrypt_to(enc: Path, dest: Path, key_file: Path) -> None:
    """openssl 解密（密码从**文件**读 ⇒ 不进命令行 / 进程列表）。"""
    subprocess.run(
        [
            str(find_openssl()),
            "enc",
            "-d",
            f"-{CIPHER}",
            "-pbkdf2",
            "-iter",
            PBKDF2_ITER,
            "-in",
            str(enc),
            "-out",
            str(dest),
            "-pass",
            f"file:{key_file}",
        ],
        check=True,
    )


def integrity_verdict(db_path: Path) -> str:
    """``PRAGMA integrity_check`` 的首行结果（打不开时返回可读描述）。"""
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def count_tables(db_path: Path) -> int:
    """物理表数（口径同 ``packages/core/db.py::count_tables``）。"""
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(_TABLE_COUNT_QUERY).fetchone()[0])
    finally:
        conn.close()


def sidecars(db_path: Path) -> list[Path]:
    """目标库旁的 ``-wal`` / ``-shm``（SQLite 会**回放**它们 ⇒ 必须先删）。"""
    return [Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]


def remove_sidecars(db_path: Path) -> tuple[list[Path], list[tuple[Path, OSError]]]:
    """删掉 ``-wal`` / ``-shm``（幂等）。

    ⚠️ **单文件失败记录 + 继续**（2026-10-23 对抗审查 T3-1）：旧实现直接 ``unlink()``
    ⇒ 目标被占用时（Windows ``WinError 32``）异常冒泡成 traceback、退出码 1。
    现在返回 ``(已删, [(文件, 异常), ...])``，由调用方判断「整体是否失败」。

    ⚠️ 删之前**必须**先 checkpoint（见 :func:`checkpoint_target`）—— 否则 WAL 里
    尚未并入主库的提交会随 ``-wal`` 一起消失。
    """
    removed: list[Path] = []
    failed: list[tuple[Path, OSError]] = []
    for p in sidecars(db_path):
        try:
            if not p.exists():
                continue
            p.unlink()
            removed.append(p)
        except OSError as exc:  # 含 PermissionError（Windows：文件正被占用）
            failed.append((p, exc))
    return removed, failed


def checkpoint_target(target: Path) -> str:
    """把目标库的 WAL **落进主库**（``PRAGMA wal_checkpoint(TRUNCATE)``）。

    ⚠️ 为什么必须先 checkpoint 再删 sidecar（2026-10-23 对抗审查 T3-3）：
    WAL 里可能有**尚未并入主库**的提交 ⇒ 直接删 ``-wal`` 会丢数据；
    checkpoint 之后主库已含这些提交 ⇒ 删 sidecar 不丢数据。

    ⚠️ 这一步同时是「**目标占用预检**」：目标被另一个进程占用时 checkpoint 会失败
    ⇒ 抛 :class:`TargetBusy` ⇒ 调用方**中止**（不硬删 sidecar、不冒泡 traceback）。

    返回 ``"ok"`` / ``"skipped"``（目标不存在或非 WAL 库）/ ``"unreadable"``
    （目标不是可读库——多半已损坏，此时它的 sidecar 无意义）。
    """
    if not target.exists():
        return "skipped"
    try:
        conn = sqlite3.connect(str(target))
    except (sqlite3.Error, OSError):
        return "unreadable"
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TargetBusy(
                "目标库正被占用（另一个进程正开着它），无法安全恢复。"
                "请先关闭 NovelOS 再重试。"
            ) from exc
        return "skipped"
    except sqlite3.DatabaseError:
        return "unreadable"
    except OSError as exc:
        raise TargetBusy(f"目标库无法访问（可能被占用）：{exc}") from exc
    finally:
        conn.close()
    return "ok"


def snapshot(src: Path, dest: Path) -> bool:
    """用**在线备份 API** 给旧目标库拍一份快照（回滚用）。

    ⚠️ 不用 ``shutil.copy2``：WAL 模式下那会拷到「主库 + 未合并的 -wal」的不一致状态。
    ⚠️ 也**不用** ``mode=ro`` 打开：WAL 模式的库若没有 ``-shm``，只读连接打不开
    （SQLite 需要写 ``-shm``）⇒ 回滚快照反而拿不到。拍不到只警告，不阻断恢复。
    """
    try:
        s = sqlite3.connect(str(src))
        d = sqlite3.connect(str(dest))
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
    except sqlite3.Error:
        return False
    return True


def rollback(snapshot_path: Path | None, target: Path, *, confirm: bool = False) -> None:
    """把替换前的快照放回目标（失败也只警告，不掩盖主错误）。

    ⚠️ **确认闸**（S-2 ①）：破坏性动作（用快照覆盖目标库）**必须**显式
    ``confirm=True``；默认 ``False`` ⇒ **拒绝且零副作用**（不 replace、不删 sidecar）。

    ★ 两种语义的区分（S-2 ④）：
    - **用户主动回滚** = 外部直接调用本函数 ⇒ 须显式 ``confirm=True``；
    - **失败自动回滚** = :func:`restore` 内部在替换失败/校验不过时的补偿动作 ⇒
      由 :func:`restore` 在**同一次调用内**显式传 ``confirm=True``（三处自调用，
      **显式传参、不是隐式豁免**）。
    ⇒ 闸**只有 ``confirm`` 一个信号**（不引入 env／全局开关／外部可传的绕过通道）；
      「主动 vs 自动」的区分体现在**调用点**与**判别用例**上。
    """
    if not confirm:
        _err("[拒绝]   回滚未确认（confirm=False）⇒ 目标库保持当前状态（零副作用）")
        return
    if snapshot_path is None or not snapshot_path.exists():
        _err("[回滚]   无快照可回滚 ⇒ 目标库保持当前状态")
        return
    try:
        os.replace(snapshot_path, target)
    except OSError as exc:
        _err(f"[回滚]   回滚失败：{exc}（快照保留在 {snapshot_path}）")
        return
    _, failed = remove_sidecars(target)
    if failed:
        _err(
            "[回滚]   已放回快照，但目标旁的 sidecar 删不掉："
            + "、".join(p.name for p, _ in failed)
        )
        return
    print("[回滚]   已回滚到替换前的快照")


def restore(
    enc: Path,
    target: Path,
    key_file: Path,
    expect_tables: int | None = DEFAULT_EXPECT_TABLES,
    *,
    confirm: bool = False,
) -> int:
    """执行恢复；返回退出码（``EXIT_OK`` = 成功）。

    ⚠️ **确认闸**（S-2 ①）：破坏性动作（整库替换）**必须**显式 ``confirm=True``；
    默认 ``False`` ⇒ **拒绝且零副作用**（不解密、不拍快照、不替换、不删 sidecar）。
    """
    if not confirm:
        _err("[拒绝]   恢复未确认（confirm=False）⇒ 目标库未改动（零副作用）")
        return EXIT_USAGE
    if not enc.is_file():
        _err(f"[ABORT] 密文不存在：{enc}")
        return EXIT_USAGE
    if not key_file.is_file():
        _err(
            f"[ABORT] 密钥文件不存在：{key_file}\n"
            "        恢复**绝不**生成新密钥（那只会解不开旧密文，等于把备份扔掉）。\n"
            "        请从私有网盘取回原密钥，或用 --key-file 指定它。"
        )
        return EXIT_USAGE

    target.parent.mkdir(parents=True, exist_ok=True)

    # ① 解密到**目标同目录**的临时文件（同卷 ⇒ os.replace 才是原子替换）
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.restore-", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        try:
            decrypt_to(enc, tmp, key_file)
        except subprocess.CalledProcessError as exc:
            _err(
                f"[ABORT] 解密失败（openssl exit {exc.returncode}）："
                "密文损坏或密钥不配对 ⇒ **目标库未改动**"
            )
            return EXIT_DECRYPT_FAILED

        # ② 校验解密结果 —— **在替换之前** ⇒ 校验不过时目标库根本没被碰过
        try:
            verdict = integrity_verdict(tmp)
        except sqlite3.Error as exc:
            _err(f"[ABORT] 完整性校验未通过（无法打开解密结果：{exc}）⇒ **目标库未改动**")
            return EXIT_INTEGRITY_FAILED
        if verdict != "ok":
            _err(f"[ABORT] 完整性校验未通过（{verdict}）⇒ **目标库未改动**")
            return EXIT_INTEGRITY_FAILED

        tables = count_tables(tmp)
        if expect_tables is not None and tables != expect_tables:
            _err(
                f"[ABORT] 表数不符：恢复出来 {tables} 张表，期望 {expect_tables} 张"
                " ⇒ **目标库未改动**"
            )
            return EXIT_TABLE_COUNT_MISMATCH
        remove_sidecars(tmp)

        # ③ 给旧目标库拍快照（回滚用；拍不到只警告）
        snapshot_path: Path | None = None
        if target.exists():
            snap_fd, snap_name = tempfile.mkstemp(
                dir=str(target.parent),
                prefix=f".{target.name}.rollback-",
                suffix=".tmp",
            )
            os.close(snap_fd)
            candidate = Path(snap_name)
            if snapshot(target, candidate):
                snapshot_path = candidate
                print(f"[快照]   旧目标库已快照 -> {candidate.name}（失败可回滚）")
            else:
                candidate.unlink(missing_ok=True)
                _err("[警告]   旧目标库快照失败（可能已损坏）⇒ 无回滚副本")

        # ④ **占用预检 + WAL 落盘** —— 删 sidecar 之前必须先做（T3-1 / T3-3）
        #    · checkpoint 把 WAL 里的提交**落进主库** ⇒ 之后删 -wal 不丢数据；
        #    · checkpoint 失败（目标被占用）⇒ **中止**（退出码 2），不硬删、不冒泡 traceback。
        try:
            checkpoint_state = checkpoint_target(target)
        except TargetBusy as exc:
            _err(f"[ABORT] {exc}")
            return EXIT_USAGE
        except (OSError, sqlite3.Error) as exc:
            _err(f"[ABORT] 目标库预检失败（{type(exc).__name__}: {exc}）⇒ **目标库未改动**")
            return EXIT_USAGE
        if checkpoint_state == "unreadable":
            _err("[警告]   目标库打不开（可能已损坏）⇒ 跳过 WAL 落盘，其 sidecar 仍会删除")
        # 删掉目标旁的 -wal/-shm —— **否则 SQLite 会回放它们**，覆盖刚恢复的主库
        try:
            removed, failed = remove_sidecars(target)
        except OSError as exc:  # 兜底：remove_sidecars 内部已逐个捕获，这里防 unforeseen
            _err(
                "[ABORT] 目标库正被占用（或 sidecar 无法删除）："
                f"{type(exc).__name__}: {exc}\n"
                "        请先关闭 NovelOS（及任何持有该库的进程）再恢复。"
                "        **目标库未改动**"
            )
            return EXIT_USAGE
        if failed:
            _err(
                "[ABORT] 目标库正被占用（或 sidecar 无法删除）："
                + "、".join(f"{p.name}({type(e).__name__})" for p, e in failed)
                + "\n        请先关闭 NovelOS（及任何持有该库的进程）再恢复。"
                "        **目标库未改动**"
            )
            return EXIT_USAGE
        if removed:
            print(f"[清理]   删除目标旁残留：{', '.join(p.name for p in removed)}")

        # ⑤ 原子替换（同目录 ⇒ 同卷 ⇒ os.replace 原子）
        try:
            os.replace(tmp, target)
        except OSError as exc:
            _err(f"[ABORT] 原子替换失败：{exc} ⇒ **目标库未改动**")
            if snapshot_path is not None:
                # 失败自动回滚：**显式**传 confirm=True（S-2 ③；不是隐式豁免）。
                # ⚠️ 此处 ``os.replace`` **未成功** ⇒ 目标库仍是旧库；回滚仍属破坏性
                # 覆盖 ⇒ 必须显式授权，否则这条错误路径会留下「目标被快照覆盖」。
                rollback(snapshot_path, target, confirm=True)
            return EXIT_USAGE

        # ⑥ 恢复后校验 —— 不过 ⇒ 回滚
        try:
            verdict = integrity_verdict(target)
        except sqlite3.Error as exc:
            verdict = f"无法打开：{exc}"
        if verdict != "ok":
            _err(f"[ABORT] 恢复后完整性校验未通过（{verdict}）⇒ 回滚旧库")
            # 失败自动回滚：**显式**传 confirm=True（S-2 ③）——此处 ``os.replace``
            # **已成功**（目标已是新库）⇒ 若闸拒绝回滚，目标会停在坏的新库上。
            rollback(snapshot_path, target, confirm=True)
            return EXIT_INTEGRITY_FAILED
        tables = count_tables(target)
        if expect_tables is not None and tables != expect_tables:
            _err(
                f"[ABORT] 恢复后表数不符：{tables} 张（期望 {expect_tables} 张）⇒ 回滚旧库"
            )
            # 失败自动回滚：**显式**传 confirm=True（S-2 ③）——同上，``os.replace``
            # **已成功** ⇒ 回滚必须能执行，目标才能回到旧库。
            rollback(snapshot_path, target, confirm=True)
            return EXIT_TABLE_COUNT_MISMATCH

        remove_sidecars(target)
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        print(f"[已恢复] {target}（{tables} 张表，integrity_check=ok）")
        return EXIT_OK
    finally:
        # 任何路径都不留临时文件残骸（清理失败**不得**掩盖真正的错误）
        tmp.unlink(missing_ok=True)
        try:
            remove_sidecars(tmp)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    # ⚠️ 本机控制台默认 cp936（AGENTS 坑区）⇒ 改 utf-8。
    # ⚠️ **必须在函数里、且判 hasattr**：pytest 捕获期间 sys.stdout 被换成没有
    # ``reconfigure`` 的对象 ⇒ 模块级调用会让 import 就炸。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="从加密备份恢复 NovelOS 主库（原子替换 + 恢复后校验）")
    ap.add_argument("--enc", required=True, help="加密备份文件（.db.enc）")
    ap.add_argument("--out", required=True, help="恢复目标库路径（.db）")
    ap.add_argument(
        "--key-file",
        default=str(DEFAULT_KEY_FILE),
        help=f"解密密钥文件（默认 {DEFAULT_KEY_FILE}；**绝不**自动生成）",
    )
    ap.add_argument(
        "--expect-tables",
        type=int,
        default=DEFAULT_EXPECT_TABLES,
        help="期望的物理表数（**默认不核对**，只打印实际表数；显式传值才核对，"
             "不符退 5）——⚠️ 刻意不写死张数：表数随迁移漂移"
             "（见 packages/core/db.py::count_tables 的 A-009）",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="确认执行破坏性恢复（映射到库层 confirm=True；**缺省 False** ⇒ 拒绝且零副作用）",
    )
    args = ap.parse_args(argv)

    enc = Path(args.enc).expanduser()
    target = Path(args.out).expanduser()
    key_file = Path(args.key_file).expanduser()

    # 库落点闸（C-02 ``--out`` 目标库 / C-03 ``--enc`` 备份源）：仓内**仅**允许
    # ``data/`` 子树 ⇒ 拒绝 ⇒ 退出码 2（用法/前置条件错误），**零副作用**。
    try:
        check_db_path(target)
        check_db_path(enc)
    except RepoLocalDbPathError as exc:
        _err(f"[拒绝库落点] {exc}")
        return EXIT_USAGE
    print(f"[密文]   {enc}")
    print(f"[目标]   {target}")
    print(f"[密钥]   {key_file}")
    print(f"[期望表数] {args.expect_tables if args.expect_tables is not None else '（不核对）'}")
    if target.exists():
        print("[提示]   目标已存在，将被**覆盖**（替换前拍快照，失败可回滚）")
    # ``--yes`` 映射到库层 ``confirm``（S-2 ②）：缺省 False ⇒ 库层拒绝且零副作用。
    return restore(enc, target, key_file, args.expect_tables, confirm=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
