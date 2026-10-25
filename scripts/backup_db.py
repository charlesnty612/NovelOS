"""备份 NovelOS 主库到**网盘同步盘**（不在本机留副本）。

为什么落同步盘（2026-10-21 用户裁决）
------------------------------------
本机只留一份活库，备份一律写到百度网盘同步盘 ⇒ 机器坏了/被误删时数据在云端，
且本机不留「另一份本项目的备份」。

⚠️ **安全前提（必须让用户知道）**：``data/novelos.db`` 里
``model_profiles.params_json.api_key`` / ``model_connections.params_json.api_key``
是**明文密钥**（见 ``docs/adr/ADR-0002-model-provider-api-key-plaintext-sqlite.md``）
⇒ **备份文件同样含明文密钥**，写进同步盘 = 密钥上云。
⇒ **2026-10-23 起默认加密落盘**（明文密钥不出本机）；确实需要明文副本时用
``--plaintext``（会打印「密钥上云」警告）。``--redact-secrets`` 产出**不含明文密钥**
的副本（那份**不可直接恢复**，只用于排障）。

一致性
------
用 ``sqlite3.Connection.backup()`` 的**在线备份 API**，不是 ``shutil.copy2`` ——
后者在 WAL 模式下会拷到「主库文件 + 未合并的 -wal」的不一致状态，
还原时可能丢最后一次事务。

用法
----
    python scripts/backup_db.py                       # 备份到探测到的同步盘（**默认加密**）
    python scripts/backup_db.py --init-key            # 首次备份：显式创建密钥文件
    python scripts/backup_db.py --out D:\\BaiduSyncdisk\\NovelOS\\backups
    python scripts/backup_db.py --plaintext           # 显式落明文（不推荐：密钥上云）
    python scripts/backup_db.py --redact-secrets      # 不含明文密钥（不可恢复）
    python scripts/backup_db.py --keep 20             # 保留最近 20 份（必须 ≥1）
    python scripts/backup_db.py --list                # 只看会写到哪里（先校验源库）
    python scripts/backup_db.py --keep-plaintext-on-failure  # 自检失败时保留明文副本（含密钥！）

落盘约束（D-19 / 写路径守卫）
---------------------------
备份落点由 ``--out`` / ``NOVELOS_BACKUP_DIR`` / 同步盘探测决定（**默认在同步盘，
不在软件仓** ⇒ 默认路径不受影响）。但**软件仓内只允许写 ``data/`` 与 ``tmp/``
两个子树**（均已被 ``.gitignore`` 覆盖）：``--out`` 指到软件仓内其它位置
（仓库根、``docs/`` …）⇒ **明确拒绝**（退出码 2，不产出任何备份）——
脚本不得把产物写回软件仓（落进去 = 进 GitHub 历史，**不可撤回**），
也**不得**静默改写到别处。逃生口 ``--allow-software-repo-out``（默认关）
须由用户显式承担风险。

⚠️ **密钥文件同样受这条约束**（2026-10-23 对抗审查 F-3）：``--key-file`` 指向
软件仓内（仓库根、``docs/`` …）⇒ **明确拒绝**（退出码 2，**不写密钥文件**、
**不建目标目录**）——那个文件是「**能解开所有备份的密钥**」，``.gitignore`` 此前
也不覆盖 ``backup.key`` ⇒ ``git add -A`` 就会把它提交进 GitHub 历史，**不可撤回**。
逃生口 ``--allow-software-repo-key``（默认关，**与 ``--allow-software-repo-out``
分开**）：「允许备份落仓」与「允许密钥落仓」是两件不同的事。

⚠️ **CLI 行为变更（2026-10-23，D-4 / D-5，详见 CHANGELOG）**：
1. ``--encrypt`` 由 opt-in 改为**默认开**；明文备份须显式 ``--plaintext``；
2. 密钥文件**不再静默生成**——缺失时**报错退出**，只有 ``--init-key`` 才创建
   （旧行为：新密钥解旧密文必然 ``bad decrypt`` ⇒ 旧备份永久不可恢复）；
3. 加密后新增**密文解密往返自检**（解密回临时文件 → 比对 sha256），
   不通过则**不落盘密文**；
4. 自检失败时**默认删除**明文副本（2026-10-23 对抗审查 T3-2：留着它 = 把含
   **明文 API 密钥**的整库无限期留在 ``%TEMP%``）。要留必须显式
   ``--keep-plaintext-on-failure``（**默认关**）；
5. 每次备份前会清理**陈旧的** ``novelos_backup_*`` 工作目录（只清本脚本前缀 +
   超过 24 小时的；不碰别人的目录、不碰正在跑的实例）。

恢复用 ``scripts/restore_db.py``（原子替换 + 删 ``-wal``/``-shm`` + 恢复后校验）。

⚠️ ``--keep`` 语义（2026-10-23 F-6 订正）：只接受 **≥1**。旧实现 ``keep=0`` 走
「全保留」分支，与「保留最近 N 份」直觉相反、又容易被读成「全删」⇒ CLI 直接拒绝。
``--list`` 现在**先**做 ``源库 is_file()`` 校验再打印——旧实现先返回，源路径打错也
会 exit 0。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:  # 正常路径：仓库根在 sys.path（包导入 / pytest pythonpath=["."]）
    from scripts.guard_repo_write_path import (
        EXIT_WRITE_PATH_REJECTED,
        RepoWritePathError,
        check_write_path,
        is_inside_repo,
    )
except ImportError:  # pragma: no cover —— 直接 `python scripts/backup_db.py` 时
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from guard_repo_write_path import (  # type: ignore[no-redef]
        EXIT_WRITE_PATH_REJECTED,
        RepoWritePathError,
        check_write_path,
        is_inside_repo,
    )

# 库落点闸（BUG-59 / 落码批 C-05）：与 ``packages.core.db`` 的**唯一实现点**同源
# （此处只管**源库**落点；产物落点已由上面的 ``check_write_path`` 单独管）。
try:  # 正常路径：仓库根在 sys.path（包导入 / pytest pythonpath=["."]）
    from packages.core.db import RepoLocalDbPathError, check_db_path  # noqa: E402
except ImportError:  # pragma: no cover —— 直接 `python scripts/backup_db.py` 时
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from packages.core.db import RepoLocalDbPathError, check_db_path  # type: ignore[no-redef]  # noqa: E402

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

#: 本脚本创建的工作目录前缀（清理陈旧目录时**只认这个前缀**，不碰别人的目录）。
WORK_DIR_PREFIX = "novelos_backup_"
#: 陈旧判定阈值（秒）：默认 24 小时。
STALE_WORK_DIR_MAX_AGE = 24 * 3600


def find_openssl() -> Path:
    for c in OPENSSL_CANDIDATES:
        if c.is_file():
            return c
    found = shutil.which("openssl")
    if found:
        return Path(found)
    raise SystemExit("[ABORT] 找不到 openssl（Git for Windows 自带，通常在 C:\\Program Files\\Git\\mingw64\\bin）")


#: CLI 退出码：0 = 成功；2 = 前置条件不满足（中止，**未产出任何备份**）。
#: ⚠️ 与守卫的 ``EXIT_WRITE_PATH_REJECTED`` **同值（2）**：落点被拒也是「前置条件不满足」，
#: 语义一致（中止 ⇒ 仓内零产物），不新增退出码含义。
EXIT_ABORT = 2


def _abort(message: str, code: int = EXIT_ABORT) -> None:
    """打印诊断（stderr）并以**非 0 退出码**中止。

    ⚠️ 用「打印 + ``SystemExit(code)``」而不是 ``SystemExit(整段消息)``：
    后者的退出码恒为 1 且消息被当作退出码打印 ⇒ 调用方无法按类别区分失败原因。
    """
    print(message, file=sys.stderr)
    raise SystemExit(code)


class SelfCheckFailed(RuntimeError):
    """密文**解密往返自检**失败（解密失败 / 解密后 sha256 与待加密明文不一致）。"""


def decrypt_file(src: Path, dest: Path, key_file: Path) -> None:
    """openssl 解密（密码从**文件**读 ⇒ 不进命令行/进程列表）。"""
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
            str(src),
            "-out",
            str(dest),
            "-pass",
            f"file:{key_file}",
        ],
        check=True,
    )


def sha256_file(path: Path) -> str:
    """文件 sha256（分块读 ⇒ 不把整库读进内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def self_check_ciphertext(enc: Path, plain: Path, key_file: Path) -> str:
    """**密文解密往返自检**：解密回临时文件 → 比对 sha256 与待加密明文逐字节一致。

    ⚠️ 为什么必须有（旧实现只对**加密前**的明文副本做 ``integrity_check``
    ⇒ 密文是否可解**从未被验证** ⇒ 可能产出永远解不开的 ``.enc`` 而无人知晓）。

    失败抛 :class:`SelfCheckFailed`（由调用方转成 CLI 中止 + 保留明文副本）。
    """
    probe_dir = Path(tempfile.mkdtemp(prefix="novelos_selfcheck_"))
    probe = probe_dir / "probe.db"
    try:
        try:
            decrypt_file(enc, probe, key_file)
        except subprocess.CalledProcessError as exc:
            raise SelfCheckFailed(
                f"密文自检失败：解密报错（openssl exit {exc.returncode}）"
                "—— 密文可能损坏，或密钥与该密文不配对"
            ) from exc
        digest_cipher = sha256_file(probe)
        digest_plain = sha256_file(plain)
        if digest_cipher != digest_plain:
            raise SelfCheckFailed(
                "密文自检失败：解密后 sha256 与待加密明文不一致"
                f"（密文={digest_cipher[:16]}… 明文={digest_plain[:16]}…）"
            )
        return digest_cipher
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def ensure_key_file(key_file: Path, *, init: bool = False) -> Path:
    """取备份加密用的密钥文件。⚠️ 密钥**只存本机**，绝不进同步盘。

    ⚠️ **语义变更（2026-10-23，D-5）**：旧实现在密钥文件缺失时**静默生成新密钥**
    （实测：旧 key 前 8 位 ``ab1d0551`` → 新 key ``c53b90f5``；用新 key 解旧密文
    exit 1 ``bad decrypt``）⇒ **旧备份永久不可恢复**，且全程 exit 0 无告警。现在：

    * 文件**存在** ⇒ 复用（**行为逐字不变**）；
    * 文件**不存在** ⇒ **报错退出**（退出码 2），只有显式 ``init=True``
      （CLI 的 ``--init-key``）才创建。
    """
    if key_file.exists():
        print(f"[KEY] 复用既有密钥 {key_file}")
        return key_file

    env = os.environ.get("NOVELOS_BACKUP_KEY")
    if env:
        # ⚠️ 唯一的例外：环境变量是**用户显式提供的密钥材料**（不是「静默生成」），
        # 照旧落盘供 openssl ``-pass file:`` 读。
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(env.strip() + "\n", encoding="utf-8")
        print(f"[KEY] 已从 NOVELOS_BACKUP_KEY 环境变量写入密钥 -> {key_file}")
        return key_file

    if not init:
        _abort(
            f"[ABORT] 密钥文件不存在：{key_file}\n"
            "        旧实现会在此处**静默生成新密钥** ⇒ 旧备份将永久不可解"
            "（用新 key 解旧密文必然 bad decrypt）。现已改为报错退出。\n"
            "        请二选一：\n"
            "          ① 首次备份：加 --init-key 显式创建新密钥（并另存一份到私有网盘）；\n"
            "          ② 已有备份：从私有网盘取回原密钥放到该路径"
            "（或设 NOVELOS_BACKUP_KEY 环境变量）。"
        )

    key_file.parent.mkdir(parents=True, exist_ok=True)
    secret = subprocess.run(
        [str(find_openssl()), "rand", "-base64", "32"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    key_file.write_text(secret + "\n", encoding="utf-8")
    print(f"[KEY] 已按 --init-key 创建新密钥 -> {key_file}（⚠️ 机器坏掉会一起丢，请另存）")
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


def cleanup_stale_work_dirs(max_age_seconds: int = STALE_WORK_DIR_MAX_AGE) -> list[Path]:
    """清理**陈旧的** ``novelos_backup_*`` 工作目录（只清本脚本自己建的）。

    ⚠️ 为什么需要（2026-10-23 对抗审查 T3-2）：失败路径曾把**含明文 API 密钥的整库**
    留在 ``%TEMP%`` 且**无限期保留** ⇒ 除本次失败外，历次失败留下的目录也会一直堆着。
    这里做**兜底清理**。

    ⚠️ **只按「前缀 + 年龄」判定**，不碰别人的目录：
    * 前缀是本脚本独有的 ``novelos_backup_``；
    * 年龄阈值（默认 24h）天然排除**正在跑的**本脚本实例（刚建的目录 mtime 是新的）。
    """
    root = Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_seconds
    removed: list[Path] = []
    try:
        candidates = list(root.glob(f"{WORK_DIR_PREFIX}*"))
    except OSError:
        return removed
    for path in candidates:
        try:
            if not path.is_dir():
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
        except OSError:
            continue
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
    enc_group = ap.add_mutually_exclusive_group()
    enc_group.add_argument(
        "--encrypt",
        action="store_true",
        help="用 openssl 加密后再落盘（**默认已开启**；本 flag 仅作兼容，显式写出无副作用）",
    )
    enc_group.add_argument(
        "--plaintext",
        action="store_true",
        help="**显式**落明文（不推荐：明文 API 密钥会进同步盘）",
    )
    ap.add_argument(
        "--init-key",
        action="store_true",
        help="密钥文件不存在时**显式创建**（默认：报错退出，不静默生成——"
             "静默生成的新密钥解不开旧备份）",
    )
    ap.add_argument(
        "--keep-plaintext-on-failure",
        action="store_true",
        help="自检失败时**保留**明文副本（含明文 API 密钥！）——默认关闭："
             "失败时明文副本会被删除（源库未被触碰，重跑即可）",
    )
    ap.add_argument(
        "--key-file",
        default=str(DEFAULT_KEY_FILE),
        help=f"加密密钥文件（默认 {DEFAULT_KEY_FILE}；⚠️ 2026-10-22 用户裁决："
             "另存一份到**私有**网盘 —— 两处 SHA256 应一致；密钥丢失 = 备份不可恢复）",
    )
    ap.add_argument("--list", action="store_true", help="只打印将要写到哪里，不执行")
    ap.add_argument(
        "--allow-software-repo-out",
        action="store_true",
        help="逃生口：允许把备份写回软件仓（默认关；风险自负——可能被误提交 ⇒ 进 GitHub 历史不可撤回）",
    )
    ap.add_argument(
        "--allow-software-repo-key",
        action="store_true",
        help="逃生口：允许把**备份加密密钥**写进软件仓（默认关；风险自负——"
             "该密钥能解开**所有**备份，提交进 GitHub 历史不可撤回）。"
             "与 --allow-software-repo-out **分开**：允许备份落仓 ≠ 允许密钥落仓",
    )
    args = ap.parse_args(argv)
    # ⚠️ 2026-10-23 CLI 行为变更（D-4）：``--encrypt`` 由 opt-in 改为**默认开**，
    # 明文备份必须显式 ``--plaintext``。
    encrypt = not args.plaintext
    # --keep 0 旧语义是「全保留」（`if keep > 0 else []`），与「保留最近 N 份」直觉相反，
    # 且不同人读成「全删」；两种解释都危险 ⇒ 显式拒绝并要求 ≥1（F-6，2026-10-23）。
    if args.keep < 1:
        ap.error("--keep 必须 >= 1（保留最近 N 份；0 的语义有歧义，已拒绝）")

    # 源库校验**先于** --list（F-6）：此前 --list 在 is_file() 之前返回 ⇒ 源路径
    # 打错也打印「未执行任何写入」并 exit 0，预检看不出问题。
    src = Path(args.db).expanduser()
    # 库落点闸（C-05）：源库落点仓内**仅**允许 ``data/`` 子树 ⇒ 拒绝 ⇒ 零副作用
    # （未拍快照、未复制、未写任何文件）。
    try:
        check_db_path(src)
    except RepoLocalDbPathError as exc:
        _abort(f"[拒绝库落点] {exc}", code=EXIT_WRITE_PATH_REJECTED)
    if not src.is_file():
        raise SystemExit(f"[ABORT] 源库不存在：{src}")
    out_dir = resolve_out_dir(args.out)

    # 守卫（D-19）：待写路径不得落在软件仓根下非 data/tmp 处。
    # 默认落点（同步盘）在仓外 ⇒ 不受影响；显式 --out 指到仓内非法位置 ⇒ 拒绝。
    allow_repo_out = bool(args.allow_software_repo_out)
    try:
        check_write_path(out_dir, allow_repo_root=allow_repo_out)
    except RepoWritePathError as exc:
        _abort(f"[拒绝写入] {exc}", code=EXIT_WRITE_PATH_REJECTED)
    if allow_repo_out and is_inside_repo(out_dir):
        print(
            f"[警告] --allow-software-repo-out 已开启：本次备份将落软件仓 {out_dir}"
            "（可能被误提交 ⇒ 进 GitHub 历史不可撤回）。",
            file=sys.stderr,
        )

    # 守卫（D-19 / F-3）：**密钥文件**同样不得落在软件仓内的非允许位置。
    # ⚠️ `--key-file` 指到仓内 ⇒ 「能解开所有备份的密钥」进版本库 ⇒ 提交即不可撤回。
    #    逃生口**单独**给（--allow-software-repo-key）：允许「备份落仓」与允许
    #    「密钥落仓」是**两件不同的事**，共用一个开关会让只想把备份放回仓里的用户
    #    顺带授权提交密钥。
    key_file_path = Path(args.key_file).expanduser()
    allow_repo_key = bool(args.allow_software_repo_key)
    try:
        check_write_path(key_file_path, allow_repo_root=allow_repo_key)
    except RepoWritePathError as exc:
        _abort(f"[拒绝写入] {exc}", code=EXIT_WRITE_PATH_REJECTED)
    if allow_repo_key and is_inside_repo(key_file_path):
        print(
            f"[警告] --allow-software-repo-key 已开启：备份加密密钥将落软件仓 {key_file_path}"
            "（⚠️ 该密钥能解开**所有**备份；提交进 GitHub 历史不可撤回）。",
            file=sys.stderr,
        )

    print(f"[源库]   {src}")
    print(f"[输出]   {out_dir}")
    if args.list:
        print("[--list] 未执行任何写入")
        return 0
    if args.redact_secrets:
        print("[模式]   抹除明文密钥 ⇒ 该副本**不可直接恢复**")
    if encrypt:
        print(f"[模式]   加密落盘（{CIPHER} + pbkdf2 x{PBKDF2_ITER}）⇒ 明文密钥不出本机")
    else:
        print(f"[警告]   {SECRET_HINT}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"novelos_{stamp}.db"

    # ⚠️ 兜底清理**陈旧的**工作目录（T3-2）：历次失败留下的 `novelos_backup_*`
    # 里可能还躺着**含明文 API 密钥的整库** ⇒ 只清「本脚本前缀 + 超过 24h」的。
    stale = cleanup_stale_work_dirs()
    if stale:
        print(f"[清理]   陈旧工作目录 {len(stale)} 个已删除：{', '.join(p.name for p in stale)}")

    # ⚠️ **加密模式下明文 db 绝不能先落在同步盘里**（同步盘会立刻把它上传 ⇒
    # 明文密钥上云）。⇒ 先落到系统临时目录，加密后只把**密文**搬进同步盘。
    work_dir = Path(tempfile.mkdtemp(prefix=WORK_DIR_PREFIX))
    plain = work_dir / f"novelos_{stamp}.db"
    # 自检失败时**默认删除**明文副本（T3-2）；只有 --keep-plaintext-on-failure 才保留。
    keep_work_dir = False
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

        if encrypt:
            key_file = ensure_key_file(Path(args.key_file).expanduser(), init=args.init_key)
            # ⚠️ 密文先落在**临时目录**（不是同步盘）：自检没过就不该让同步盘看见它。
            enc_tmp = work_dir / f"novelos_{stamp}.db.enc"
            encrypt_file(plain, enc_tmp, key_file)
            try:
                digest = self_check_ciphertext(enc_tmp, plain, key_file)
            except SelfCheckFailed as exc:
                # ⚠️ 2026-10-23 对抗审查 T3-2：**默认删除**含明文 API 密钥的明文副本。
                #    源库未被触碰、备份可重跑 ⇒ 删副本不丢任何数据；而留着它 = 把整库
                #    的明文密钥无限期留在 %TEMP%。要留必须**显式** --keep-plaintext-on-failure。
                keep_work_dir = bool(args.keep_plaintext_on_failure)
                tail = (
                    f"        工作目录：{work_dir}\n"
                    "        明文副本：**已保留**（⚠️ 该目录含明文 API 密钥，"
                    "确认无用后请自行删除）。"
                    if keep_work_dir
                    else f"        工作目录：{work_dir}\n"
                    "        明文副本：**已删除**（默认不留含明文密钥的副本；"
                    "源库未被触碰，重跑即可重新备份）。"
                )
                _abort(f"[ABORT] {exc}\n        ⇒ **不落盘密文**。\n{tail}")
            # 自检通过后才删明文副本 ⇒ 失败路径上明文由 finally 统一处理
            plain.unlink(missing_ok=True)
            dest = out_dir / f"novelos_{stamp}.db.enc"
            shutil.move(str(enc_tmp), str(dest))
            print(f"[已备份] {dest}（{size_mb:.1f} MB 明文 → {dest.stat().st_size / 1024 / 1024:.1f} MB 密文）")
            print(f"[校验]   密文自检通过（解密往返 sha256 一致：{digest[:16]}…）+ integrity_check=ok")
        else:
            shutil.move(str(plain), str(dest))
            print(f"[已备份] {dest}（{size_mb:.1f} MB，integrity_check=ok）")
    finally:
        if keep_work_dir:
            print(f"[保留]   工作目录（**含明文 API 密钥**）保留在：{work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

    removed = prune(out_dir, args.keep)
    for r in removed:
        print(f"[清理]   旧备份 {r.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
