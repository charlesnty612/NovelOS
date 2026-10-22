"""备份 NovelOS 主库到**网盘同步盘**（不在本机留副本）。

为什么落同步盘（2026-10-21 用户裁决）
------------------------------------
本机只留一份活库，备份一律写到百度网盘同步盘 ⇒ 机器坏了/被误删时数据在云端，
且本机不留「另一份本项目的备份」。

⚠️ **安全前提（必须让用户知道）**：``data/novelos.db`` 里
``model_profiles.params_json.api_key`` / ``model_connections.params_json.api_key``
是**明文密钥**（见 ``docs/adr/ADR-0002-model-provider-api-key-plaintext-sqlite.md``）
⇒ **备份文件同样含明文密钥**，写进同步盘 = 密钥上云。
本脚本**默认照原样备份**（否则备份不可恢复，背离备份的用途），但会打印这条警告；
需要不带密钥的副本时用 ``--redact-secrets``（那份**不可直接恢复**，只用于排障）。

一致性
------
用 ``sqlite3.Connection.backup()`` 的**在线备份 API**，不是 ``shutil.copy2`` ——
后者在 WAL 模式下会拷到「主库文件 + 未合并的 -wal」的不一致状态，
还原时可能丢最后一次事务。

用法
----
    python scripts/backup_db.py                       # 备份到探测到的同步盘
    python scripts/backup_db.py --out D:\\BaiduSyncdisk\\NovelOS\\backups
    python scripts/backup_db.py --redact-secrets      # 不含明文密钥（不可恢复）
    python scripts/backup_db.py --keep 20             # 保留最近 20 份（必须 ≥1）
    python scripts/backup_db.py --list                # 只看会写到哪里（先校验源库）

⚠️ ``--keep`` 语义（2026-10-23 F-6 订正）：只接受 **≥1**。旧实现 ``keep=0`` 走
「全保留」分支，与「保留最近 N 份」直觉相反、又容易被读成「全删」⇒ CLI 直接拒绝。
``--list`` 现在**先**做 ``源库 is_file()`` 校验再打印——旧实现先返回，源路径打错也
会 exit 0。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "novelos.db"

# 同步盘候选位置（按序探测第一个存在的）。**不含本机兜底** ——
# 用户裁决「本机不留其它本项目的备份」⇒ 找不到同步盘就报错，不静默写回本机。
SYNC_CANDIDATES = (
    Path(r"D:\BaiduSyncdisk"),
    Path.home() / "BaiduSyncdisk",
    Path(r"E:\BaiduSyncdisk"),
)
SYNC_SUBDIR = Path("NovelOS") / "backups"

SECRET_HINT = (
    "备份文件内含**明文 API 密钥**（model_profiles / model_connections 的 params_json.api_key）"
    "⇒ 写进同步盘等于密钥上云。确认该同步盘账号可信且已开启加密。"
)

# 加密用的 openssl（Git for Windows 自带）。**不装 cryptography**（本仓 .venv 无此包）。
OPENSSL_CANDIDATES = (
    Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
    Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
)
CIPHER = "aes-256-cbc"
PBKDF2_ITER = "200000"
DEFAULT_KEY_FILE = Path.home() / ".novelos" / "backup.key"


def find_openssl() -> Path:
    for c in OPENSSL_CANDIDATES:
        if c.is_file():
            return c
    found = shutil.which("openssl")
    if found:
        return Path(found)
    raise SystemExit("[ABORT] 找不到 openssl（Git for Windows 自带，通常在 C:\\Program Files\\Git\\mingw64\\bin）")


def ensure_key_file(key_file: Path) -> Path:
    """取（或生成）备份加密用的密钥文件。⚠️ 密钥**只存本机**，绝不进同步盘。"""
    env = os.environ.get("NOVELOS_BACKUP_KEY")
    if env and not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(env.strip() + "\n", encoding="utf-8")
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        secret = subprocess.run(
            [str(find_openssl()), "rand", "-base64", "32"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
        key_file.write_text(secret + "\n", encoding="utf-8")
        print(f"[KEY] 已生成新密钥 -> {key_file}（⚠️ 机器坏掉会一起丢，请另存）")
    else:
        print(f"[KEY] 复用既有密钥 {key_file}")
    return key_file


def encrypt_file(src: Path, dest: Path, key_file: Path) -> None:
    """openssl 加密（密码从**文件**读 ⇒ 不进命令行/进程列表）。"""
    subprocess.run(
        [
            str(find_openssl()),
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
            f"file:{key_file}",
        ],
        check=True,
    )


def resolve_out_dir(explicit: str | None) -> Path:
    """解析备份输出目录：``--out`` > ``NOVELOS_BACKUP_DIR`` > 探测同步盘。"""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("NOVELOS_BACKUP_DIR")
    if env:
        return Path(env).expanduser()
    for cand in SYNC_CANDIDATES:
        if cand.is_dir():
            return cand / SYNC_SUBDIR
    raise SystemExit(
        "[ABORT] 找不到网盘同步盘候选路径（"
        + " / ".join(str(c) for c in SYNC_CANDIDATES)
        + "）。\n"
        "       按用户裁决「备份落同步盘、本机不留副本」，**不静默回退到本机目录**。\n"
        "       请显式指定：--out <同步盘目录>  或设环境变量 NOVELOS_BACKUP_DIR。"
    )


def redact_secrets(target: Path) -> int:
    """把 ``params_json.api_key`` 换成 ``***``（**该副本不可用于恢复**）。"""
    conn = sqlite3.connect(target)
    try:
        n = 0
        for table in ("model_profiles", "model_configs", "model_connections"):
            try:
                rows = conn.execute(f"SELECT rowid, params_json FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            for rowid, raw in rows:
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if not data.get("api_key"):
                    continue
                data["api_key"] = "***"
                conn.execute(
                    f"UPDATE {table} SET params_json = ? WHERE rowid = ?",
                    (json.dumps(data, ensure_ascii=False), rowid),
                )
                n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def prune(out_dir: Path, keep: int) -> list[Path]:
    """只保留最近 ``keep`` 份备份（``novelos_*.db`` 与 ``novelos_*.db.enc`` 都算）。

    ``keep`` 必须 ≥1（2026-10-23 F-6）：旧实现把 ``keep=0`` 静默当成「全保留」，
    与参数名直觉相反且不同人读成「全删」⇒ 在这里显式拒绝，避免两种误读。
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep!r}")
    backups = sorted(out_dir.glob("novelos_*.db*"))
    removed = []
    for old in backups[:-keep]:
        old.unlink()
        removed.append(old)
    return removed


def main(argv: list[str] | None = None) -> int:
    # ⚠️ 本机控制台默认 cp936（AGENTS 坑区「Windows 默认 cp936 吞中文」）⇒ 改 utf-8。
    # ⚠️ **必须在函数里、且判 hasattr**：pytest 捕获期间 ``sys.stdout`` 被换成
    # 没有 ``reconfigure`` 的对象 ⇒ 模块级调用会让 **import 就炸**。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="备份 NovelOS 主库到网盘同步盘")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="源库路径（默认 data/novelos.db）")
    ap.add_argument("--out", default=None, help="输出目录（默认探测同步盘）")
    ap.add_argument(
        "--keep",
        type=int,
        default=10,
        help="保留最近 N 份（默认 10；**必须 ≥ 1**——0 不是「全保留」也不是「全删」，"
             "语义歧义故直接拒绝）",
    )
    ap.add_argument("--redact-secrets", action="store_true", help="抹掉明文密钥（该副本不可恢复）")
    ap.add_argument(
        "--encrypt",
        action="store_true",
        help="用 openssl 加密后再落盘（**推荐**：备份可恢复，且明文密钥不出本机）",
    )
    ap.add_argument(
        "--key-file",
        default=str(DEFAULT_KEY_FILE),
        help=f"加密密钥文件（默认 {DEFAULT_KEY_FILE}；⚠️ 2026-10-22 用户裁决："
             "另存一份到**私有**网盘 —— 两处 SHA256 应一致；密钥丢失 = 备份不可恢复）",
    )
    ap.add_argument("--list", action="store_true", help="只打印将要写到哪里，不执行")
    args = ap.parse_args(argv)
    # --keep 0 旧语义是「全保留」（`if keep > 0 else []`），与「保留最近 N 份」直觉相反，
    # 且不同人读成「全删」；两种解释都危险 ⇒ 显式拒绝并要求 ≥1（F-6，2026-10-23）。
    if args.keep < 1:
        ap.error("--keep 必须 >= 1（保留最近 N 份；0 的语义有歧义，已拒绝）")

    # 源库校验**先于** --list（F-6）：此前 --list 在 is_file() 之前返回 ⇒ 源路径
    # 打错也打印「未执行任何写入」并 exit 0，预检看不出问题。
    src = Path(args.db).expanduser()
    if not src.is_file():
        raise SystemExit(f"[ABORT] 源库不存在：{src}")
    out_dir = resolve_out_dir(args.out)

    print(f"[源库]   {src}")
    print(f"[输出]   {out_dir}")
    if args.list:
        print("[--list] 未执行任何写入")
        return 0
    if args.redact_secrets:
        print("[模式]   抹除明文密钥 ⇒ 该副本**不可直接恢复**")
    elif args.encrypt:
        print(f"[模式]   加密落盘（{CIPHER} + pbkdf2 x{PBKDF2_ITER}）⇒ 明文密钥不出本机")
    else:
        print(f"[警告]   {SECRET_HINT}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"novelos_{stamp}.db"

    # ⚠️ **加密模式下明文 db 绝不能先落在同步盘里**（同步盘会立刻把它上传 ⇒
    # 明文密钥上云）。⇒ 先落到系统临时目录，加密后只把**密文**搬进同步盘。
    work_dir = Path(tempfile.mkdtemp(prefix="novelos_backup_"))
    plain = work_dir / f"novelos_{stamp}.db"
    try:
        # 在线一致性备份（WAL 模式下不会拷到不一致的半截状态）
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        d = sqlite3.connect(plain)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()

        if args.redact_secrets:
            n = redact_secrets(plain)
            print(f"[脱敏]   已抹除 {n} 处 api_key")

        # 完整性校验：备份坏了却看不出来 = 没有备份
        chk = sqlite3.connect(plain)
        try:
            verdict = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
        if verdict != "ok":
            raise SystemExit(f"[ABORT] 备份完整性校验未通过（{verdict}）⇒ 不留坏备份")
        size_mb = plain.stat().st_size / 1024 / 1024

        if args.encrypt:
            key_file = ensure_key_file(Path(args.key_file).expanduser())
            enc_dest = out_dir / f"novelos_{stamp}.db.enc"
            encrypt_file(plain, enc_dest, key_file)
            plain.unlink(missing_ok=True)
            dest = enc_dest
            print(f"[已备份] {dest}（{size_mb:.1f} MB 明文 → {dest.stat().st_size / 1024 / 1024:.1f} MB 密文）")
            print("[校验]   integrity_check=ok（在加密前的明文副本上做的）")
        else:
            shutil.move(str(plain), str(dest))
            print(f"[已备份] {dest}（{size_mb:.1f} MB，integrity_check=ok）")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    removed = prune(out_dir, args.keep)
    for r in removed:
        print(f"[清理]   旧备份 {r.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
