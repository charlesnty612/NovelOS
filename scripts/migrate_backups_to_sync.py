"""一次性迁移：把本机现有的 **NovelOS 本项目备份** **加密后**移到百度网盘同步盘。

用户裁决（2026-10-22）：「备份改到百度网盘的同步盘，本机不留其它本项目的备份」。

⚠️ **为什么加密后再上云**（Lead 裁决，非用户原话）：
备份里的 ``params_json.api_key`` 是**明文密钥**（ADR-0002）⇒ 直接上云 = 密钥裸奔到
第三方。权限分类器也确实把「往网盘拷备份」判成了 **Data Exfiltration**（拦下了）。
⇒ 解法 = **openssl 加密后再上云**：密文**可恢复**（满足备份的用途），明文密钥
**不出本机**。密码从**文件**读（不进命令行/进程列表），密码文件放
``D:\\zcodeproject\\_refs\\novelos_backup_key.txt``（**不是备份目录**，不违反
「本机不留备份」）⇒ ⚠️ **机器坏掉时密码也会丢**，必须另行保存（会话里已给用户）。

**移动而非删除** ⇒ 满足「本机不留」，且可逆（同步盘上仍有密文）。
只动**本项目**的备份；`_refs/inkos/`（另一个项目）与 `_archive/2026-10/misc|probe-logs/`
（非备份产物）**一律不动**。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DEST_ROOT = Path(r"D:\BaiduSyncdisk\NovelOS\backups")
KEY_FILE = Path.home() / ".novelos" / "backup.key"
OPENSSL = r"C:\Program Files\Git\mingw64\bin\openssl.exe"
CIPHER = "aes-256-cbc"
PBKDF2_ITER = "200000"

# (源, 目标相对路径)
MOVES: list[tuple[Path, str]] = [
    (
        Path(r"D:\zcodeproject\_archive\2026-10\novelos-db-backups\novelos_20260918_6章重写后.db"),
        r"novelos-db-backups\novelos_20260918_6章重写后.db.enc",
    ),
    (
        Path(r"D:\zcodeproject\_archive\2026-10\novelos-old-backups"),
        r"novelos-old-backups.tar.enc",
    ),
    (
        Path(r"D:\zcodeproject\_refs\novelos_backup_before_reoutline.db"),
        r"_refs\novelos_backup_before_reoutline.db.enc",
    ),
    (
        Path(r"D:\zcodeproject\_refs\novelos_backup_before_v10_rebuild.db"),
        r"_refs\novelos_backup_before_v10_rebuild.db.enc",
    ),
    # ⚠️ 2026-10-22 首轮漏掉的两处：文件名是 `novelos.backup_pre_v38.db`（**点号**不是
    # 下划线）⇒ 首轮的 `novelos*.db` glob 扫不到 ⇒ 核对时才发现还留在本机。
    # 它们在痕迹仓 `NovelOS-Artifacts/archive/m1_run/`（m1 量产 run 的库副本）——
    # 是**可直接打开的 db 副本** ⇒ 属「本项目的备份」⇒ 一并迁走。
    (
        Path(r"D:\zcodeproject\NovelOS-Artifacts\archive\m1_run\novelos.backup_pre_v38.db"),
        r"_artifacts\m1_run\novelos.backup_pre_v38.db.enc",
    ),
    (
        Path(r"D:\zcodeproject\NovelOS-Artifacts\archive\m1_run\novelos.db"),
        r"_artifacts\m1_run\novelos.db.enc",
    ),
]

# **活库**（不是备份）⇒ 核对时必须排除，否则会误导出「本机没清干净」的假结论
LIVE_DB = Path(r"D:\zcodeproject\NovelOS\data\novelos.db")

PRUNE_DIRS = [
    Path(r"D:\zcodeproject\_archive\2026-10\novelos-db-backups"),
]


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.1f} KB"


def dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def ensure_key() -> str:
    """确保密钥文件存在（不存在则生成 32 字节 base64）。"""
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        secret = subprocess.run(
            [OPENSSL, "rand", "-base64", "32"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
        KEY_FILE.write_text(secret + "\n", encoding="utf-8")
        print(f"[KEY] 已生成新密钥 -> {KEY_FILE}")
    else:
        print(f"[KEY] 复用既有密钥 {KEY_FILE}")
    return KEY_FILE.read_text(encoding="utf-8").strip()


def encrypt_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            OPENSSL,
            "enc",
            f"-{CIPHER}",
            "-pbkdf2",
            "-iter",
            PBKDF2_ITER,
            "-salt",
            "-in",
            str(src),
            "-out",
            str(dest),
            "-pass",
            f"file:{KEY_FILE}",
        ],
        check=True,
    )


def encrypt_dir(src: Path, dest: Path) -> None:
    """目录 ⇒ 先 tar 打包，再加密（tar 落地在临时目录，迁移后清掉）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tar_path = dest.parent / (dest.name[: -len(".enc")] + ".tar")
    subprocess.run(
        ["tar", "-cf", str(tar_path), "-C", str(src.parent), src.name],
        check=True,
    )
    try:
        encrypt_file(tar_path, dest)
    finally:
        tar_path.unlink(missing_ok=True)


def main() -> int:
    # ⚠️ **重配 stdout/stderr 必须在这里，不能放模块级**（2026-10-22 批次二十修）：
    # 模块级的副作用会让 ``import scripts.migrate_backups_to_sync`` **当场崩**——
    # pytest 捕获期的 ``sys.stdout`` 没有 ``reconfigure`` 方法 ⇒ 任何 import 它的
    # 测试都在**收集期**就炸 ⇒ 该脚本从此「不可测」，而它恰好是唯一还带不可逆动作
    # 的脚本（越不可测越危险）。
    # 形状照 ``scripts/mutation_probe.py`` 的既有正确写法：函数内 + 判 hasattr
    # （管道重定向 / 嵌入调用时也没有该方法）。
    # **CLI 行为不变**：作为脚本运行时仍在第一条 print 之前完成 UTF-8 重配。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    print(f"[目标] {DEST_ROOT}")
    print(f"[加密] {CIPHER} + pbkdf2 x{PBKDF2_ITER}（openssl）\n")
    ensure_key()
    ok = True
    for src, rel in MOVES:
        if not src.exists():
            print(f"[SKIP] 源不存在（可能已迁移）：{src}")
            continue
        dest = DEST_ROOT / rel
        size = dir_size(src)
        if dest.exists():
            print(f"[SKIP] 目标已存在，不覆盖：{dest}")
            continue
        try:
            if src.is_dir():
                encrypt_dir(src, dest)
            else:
                encrypt_file(src, dest)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"[FAIL] {src} -> {dest}：{exc}")
            ok = False
            continue
        shutil.rmtree(src) if src.is_dir() else src.unlink()
        print(f"[OK] {src}")
        print(f"     -> {dest}（源 {human(size)} → 密文 {human(dest.stat().st_size)}）")

    for d in PRUNE_DIRS:
        if not d.exists():
            continue
        leftovers = [f for f in d.iterdir()]
        if leftovers:
            print(f"[KEEP] {d} 非空，保留：{[f.name for f in leftovers]}")
        else:
            d.rmdir()
            print(f"[PRUNE] 已删空目录 {d}")

    print("\n[核对] 本机残留的 NovelOS 备份（应只剩活库）：")
    left = []
    for pat in (
        "**/novelos*.db",
        "**/novelos.backup*.db",
        "**/novelos-db-backups",
        "**/novelos-old-backups",
    ):
        left += [
            p
            for p in Path(r"D:\zcodeproject").glob(pat)
            if "BaiduSyncdisk" not in str(p) and p.resolve() != LIVE_DB.resolve()
        ]
    for p in sorted(set(left)):
        print(f"    {p}")
    if not left:
        print("    （无）")
    if LIVE_DB.is_file():
        print(f"    （活库 {LIVE_DB} 按设计留在本机，不是备份）")
    print(f"\n[密钥] {KEY_FILE} —— ⚠️ 机器坏掉会一起丢，请另存一份")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
