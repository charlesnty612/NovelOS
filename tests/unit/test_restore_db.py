"""``scripts/restore_db.py`` 的行为测试（**合成夹具 + 临时库**，不碰真库/真备份目录）。

本文件钉住「恢复侧」的硬要求（2026-10-23 批次三十一，D-2 / D-3）：

1. 解密 → 恢复 → **表数与内容**与源库一致；
2. 目标库旁**残留 ``-wal``/``-shm``** 时必须恢复成**正确的库**
   （子代理实测的「假绿」形态：恢复后 49 表 → 1 表、``projects`` 表不存在，
   而 ``integrity_check`` **报 ok** ⇒ 恢复静默失效且校验报绿）；
3. **故意损坏密文 ⇒ 目标库逐字节未变**
   （旧实现无 temp+rename ⇒ openssl 先截断目标再报错 ⇒ **解密失败仍会破坏活库**）；
4. 恢复是**原子替换**（目标被换掉，而不是就地覆写）；
5. 恢复后 ``integrity_check=ok``；**表数不符 ⇒ 明确报错**且不碰原库；
6. **CLI 退出码**：失败非 0、成功 0（CLI 层才是 CI / 人真正消费的那一层）。

⚠️ **真实 openssl**：沿用 ``tests/unit/test_backup_db.py`` 的既有做法（真 openssl 往返），
**不改成 mock**；全部只在**系统临时目录**跑，绝不写真实备份目录或真实库。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import restore_db  # noqa: E402

BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
RESTORE_SCRIPT = ROOT / "scripts" / "restore_db.py"


def _run_backup(args: list[str]) -> subprocess.CompletedProcess:
    """跑一次备份脚本（子进程 ⇒ 断言的是进程退出码）。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"NOVELOS_BACKUP_KEY", "NOVELOS_BACKUP_DIR"}
    }
    return subprocess.run(
        [sys.executable, str(BACKUP_SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _make_wal_db(path: Path) -> None:
    """合成一个 **WAL 模式**的库（内容虚构，表名照生产契约的形状）。

    ⚠️ **必须是 WAL 模式**：在线备份副本会保留 WAL 头（header 18/19 = 2）
    ⇒ 目标库旁的残留 ``-wal`` 会被 SQLite **回放**（这正是「静默失效」的成因）。
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, title TEXT);
            CREATE TABLE chapters (chapter_id TEXT PRIMARY KEY, project_id TEXT, title TEXT);
            INSERT INTO projects VALUES ('proj_test01', '合成项目');
            INSERT INTO chapters VALUES ('chap_test01', 'proj_test01', '合成第一章');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _stale_wal_bytes(tmp_dir: Path) -> bytes:
    """造一份「陈旧 wal」：另一个 WAL 库的非空 ``-wal``（回放后会覆盖目标库内容）。

    ⚠️ 必须在连接**未关闭**时取字节：关闭最后一个连接会 checkpoint 并删掉 ``-wal``。
    """
    stale = tmp_dir / "stale.db"
    conn = sqlite3.connect(stale)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")  # 别 checkpoint，留在 -wal 里
        conn.executescript(
            "CREATE TABLE OLD_LIVE_TABLE (x TEXT);"
            "INSERT INTO OLD_LIVE_TABLE VALUES ('stale');"
        )
        conn.commit()
        return stale.with_name(stale.name + "-wal").read_bytes()
    finally:
        conn.close()


def _make_backup(src: Path, out_dir: Path, key: Path) -> Path:
    """用**真实** backup_db.py + 真实 openssl 造一份加密备份，返回密文路径。"""
    proc = _run_backup(
        ["--db", str(src), "--out", str(out_dir), "--key-file", str(key), "--init-key"]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    encs = list(out_dir.glob("novelos_*.db.enc"))
    assert len(encs) == 1, proc.stdout + proc.stderr
    return encs[0]


def _tables_of(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _restore_args(enc: Path, target: Path, key: Path, *extra: str) -> list[str]:
    return [
        "--enc",
        str(enc),
        "--out",
        str(target),
        "--key-file",
        str(key),
        *extra,
    ]


# ---------------------------------------------------------------------------
# 基本往返
# ---------------------------------------------------------------------------


def test_restore_roundtrip_restores_tables_and_rows(tmp_path: Path):
    """解密 → 恢复 ⇒ **表与内容**与源库一致（不是「文件存在」就算数）。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "restored.db"
    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 0
    tables = _tables_of(target)
    assert {"projects", "chapters"} <= tables
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT title FROM projects").fetchone()[0] == "合成项目"
        assert conn.execute("SELECT title FROM chapters").fetchone()[0] == "合成第一章"
    finally:
        conn.close()


def test_restore_removes_stale_wal_and_shm(tmp_path: Path):
    """恢复前必须删掉目标旁的 ``-wal``/``-shm``（它们会被 SQLite 回放）。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "restored.db"
    target.write_bytes(b"OLD LIVE DATABASE")
    (tmp_path / "restored.db-wal").write_bytes(_stale_wal_bytes(tmp_path))
    (tmp_path / "restored.db-shm").write_bytes(b"\x00" * 64)

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 0
    assert not (tmp_path / "restored.db-wal").exists()
    assert not (tmp_path / "restored.db-shm").exists()


def test_restore_with_stale_wal_gets_the_right_database(tmp_path: Path):
    """**子代理实测的假绿形态**：目标库旁有陈旧 ``-wal`` 时，旧实现恢复成 1 张表
    （``projects`` 表不存在）而 ``integrity_check`` **仍报 ok** ⇒ 静默失效。

    本用例断言的是**恢复出来的库内容**，不是「文件存在」。

    ⚠️ **夹具要点（否则本用例是空的）**：目标 ``.db`` 文件**不存在**、只有残留的
    ``-wal``。若目标 ``.db`` 存在，本工具的「替换前快照」会先打开旧库 ⇒ SQLite
    顺手把陈旧 WAL 回放并清掉 ⇒ 夹具就**测不到**「替换后回放」这条路径
    （实测：那种夹具下撤掉删除逻辑仍然全绿 ⇒ 量具自身失效）。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "restored.db"
    (tmp_path / "restored.db-wal").write_bytes(_stale_wal_bytes(tmp_path))

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 0
    tables = _tables_of(target)
    assert "projects" in tables, tables
    assert "chapters" in tables, tables
    assert "OLD_LIVE_TABLE" not in tables, tables
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 失败路径：**绝不碰原库**
# ---------------------------------------------------------------------------


def test_corrupt_ciphertext_leaves_target_byte_identical(tmp_path: Path):
    """故意损坏密文 ⇒ 目标库**逐字节未变**（旧实现：openssl 先截断目标再报错）。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before = target.read_bytes()

    raw = enc.read_bytes()
    enc.write_bytes(raw[: len(raw) // 2])  # 截断 ⇒ 解密必失败

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc != 0
    assert target.read_bytes() == before
    # 也没有留下**恢复过程**的临时文件残骸（``.live.db.restore-*`` / ``.live.db.rollback-*``）
    leftovers = sorted(p.name for p in tmp_path.glob(".live.db.*"))
    assert leftovers == [], leftovers


def test_wrong_key_leaves_target_byte_identical(tmp_path: Path):
    """密钥不配对 ⇒ 解密失败 ⇒ 目标库逐字节未变（且失败原因可区分）。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    other_key = tmp_path / "other.key"
    other_key.write_text("SYNTHETIC-OTHER-KEY\n", encoding="utf-8")

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before = target.read_bytes()

    rc = restore_db.main(_restore_args(enc, target, other_key, "--expect-tables", "2"))

    assert rc != 0
    assert target.read_bytes() == before


def test_missing_key_file_leaves_target_untouched(tmp_path: Path, capsys):
    """密钥文件不存在 ⇒ 报错退出（**不生成**新密钥），且不碰原库。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before = target.read_bytes()

    rc = restore_db.main(
        _restore_args(enc, target, tmp_path / "nope.key", "--expect-tables", "2")
    )

    assert rc != 0
    assert not (tmp_path / "nope.key").exists()
    assert target.read_bytes() == before
    captured = capsys.readouterr()
    assert "密钥" in captured.out + captured.err


def test_table_count_mismatch_is_reported_and_target_untouched(tmp_path: Path, capsys):
    """表数不符 ⇒ **明确报错**（不静默成功），且目标库逐字节未变。

    ⚠️ 2026-10-23 对抗审查 T3-4：``--expect-tables`` **默认不核对**（表数随迁移漂移，
    写死必然过期）⇒ 这里**显式**传 43 来制造不符。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before = target.read_bytes()

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "43"))

    assert rc == 5, "表数不符必须退 5（EXIT_TABLE_COUNT_MISMATCH）"
    assert target.read_bytes() == before
    captured = capsys.readouterr()
    assert "表数" in captured.out + captured.err


# ---------------------------------------------------------------------------
# 原子性 + CLI 退出码
# ---------------------------------------------------------------------------


def test_restore_replaces_target_atomically(tmp_path: Path):
    """恢复必须是**原子替换**：目标被**换掉**（inode 变），不是就地覆写。

    ⚠️ 判别口径：``os.replace`` 后目标路径指向**新文件**（Windows 的文件索引 /
    POSIX 的 inode 都变）；直接覆写（``open(wb)`` / ``copyfile``）则**保持不变**。
    本机实测两者可区分 ⇒ 该断言能抓住「改成直接写」的退化实现。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before_ino = target.stat().st_ino

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 0
    assert target.stat().st_ino != before_ino, "目标被就地覆写了（不是原子替换）"
    assert "projects" in _tables_of(target)


def test_cli_exit_code_is_zero_on_success_and_nonzero_on_failure(tmp_path: Path):
    """**CLI 层**的退出码才是 CI / 人真正消费的那一层 ⇒ 必须有看守。"""
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    ok_target = tmp_path / "ok.db"
    proc_ok = subprocess.run(
        [
            sys.executable,
            str(RESTORE_SCRIPT),
            "--enc",
            str(enc),
            "--out",
            str(ok_target),
            "--key-file",
            str(key),
            "--expect-tables",
            "2",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc_ok.returncode == 0, proc_ok.stdout + proc_ok.stderr

    raw = enc.read_bytes()
    enc.write_bytes(raw[: len(raw) // 2])
    bad_target = tmp_path / "bad.db"
    bad_target.write_bytes(b"LIVE")
    proc_bad = subprocess.run(
        [
            sys.executable,
            str(RESTORE_SCRIPT),
            "--enc",
            str(enc),
            "--out",
            str(bad_target),
            "--key-file",
            str(key),
            "--expect-tables",
            "2",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc_bad.returncode != 0
    assert bad_target.read_bytes() == b"LIVE"


# ---------------------------------------------------------------------------
# 2026-10-23 对抗审查 T3-1 / T3-3 / T3-4
# ---------------------------------------------------------------------------


def test_expect_tables_defaults_to_no_check(tmp_path: Path, capsys):
    """``--expect-tables`` **默认不核对**（T3-4：表数随迁移漂移，写死必然过期）。

    判别：不给该参数时，恢复一个 2 表的库**必须成功**（旧实现默认期望 43 ⇒ 会退 5）。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "restored.db"
    rc = restore_db.main(_restore_args(enc, target, key))

    assert rc == 0, "默认不该核对表数"
    assert {"projects", "chapters"} <= _tables_of(target)
    captured = capsys.readouterr()
    assert "不核对" in captured.out


def test_sidecar_removal_failure_exits_2_without_traceback(
    monkeypatch, capsys, tmp_path: Path
):
    """sidecar 删不掉（= 目标被占用）⇒ **退 2 + 明确文案**，**不冒泡 traceback**（T3-1）。

    ⚠️ 旧实现：``unlink`` 的 ``PermissionError`` 直接冒泡 ⇒ 退出码 1 + 原始 traceback，
    与脚本 docstring 承诺的退出码表**不符**。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    target.write_bytes(b"LIVE DATABASE BYTES")
    before = target.read_bytes()

    original = restore_db.remove_sidecars

    def _boom(db_path: Path):
        if Path(db_path) == target:
            raise PermissionError(32, "另一个程序正在使用此文件")
        return original(db_path)

    monkeypatch.setattr(restore_db, "remove_sidecars", _boom)

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 2, f"目标被占用必须退 2（EXIT_USAGE），实际 {rc}"
    captured = capsys.readouterr()
    assert "占用" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert target.read_bytes() == before


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows 上被占用的文件不可删除（WinError 32）；POSIX 无此占用形态",
)
def test_cli_exits_2_when_target_is_locked_by_another_process(tmp_path: Path):
    """**审查者 A 的原始复现**：另一个进程开着目标库 ⇒ CLI 必须退 2 且无 traceback。

    模拟方式：父进程持有目标库的连接（⇒ ``-wal``/``-shm`` 被占），
    再用**子进程**跑恢复 CLI ⇒ 断言的是**真实进程退出码与真实 stderr**。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    _make_wal_db(target)
    holder = sqlite3.connect(target)
    try:
        # ⚠️ 必须把读事务**结束**（fetchall + rollback）：否则父进程一直占着读锁 ⇒
        # 恢复侧的 checkpoint 会先以「locked」中止 ⇒ 走不到「删 sidecar」那一步，
        # 测不到「占用 ⇒ 退 2 且无 traceback」这条路径。
        holder.execute("SELECT COUNT(*) FROM projects").fetchall()
        holder.rollback()
        proc = subprocess.run(
            [
                sys.executable,
                str(RESTORE_SCRIPT),
                "--enc",
                str(enc),
                "--out",
                str(target),
                "--key-file",
                str(key),
                "--expect-tables",
                "2",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        holder.close()

    assert proc.returncode == 2, (
        f"目标被占用时必须退 2，实际 {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    assert "占用" in proc.stdout + proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr
    # 数据安全：目标库内容仍在
    assert "projects" in _tables_of(target)


def _make_wal_only_db(db_path: Path) -> None:
    """造一个「提交只在 ``-wal`` 里」的库（主库文件里**没有**那一行）。

    ⚠️ 做法：在暂存位置用 ``wal_autocheckpoint=0`` 造好（提交不并入主库），
    再把 **db + -wal 一起复制**到目标位置 ⇒ 目标带着「未 checkpoint 的提交」，
    且**没有任何连接持有它**（否则 Windows 上 sidecar 删不掉，测不到目标行为）。
    """
    stage = db_path.with_name(db_path.name + ".stage")
    conn = sqlite3.connect(stage)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executescript(
            "CREATE TABLE t (x TEXT); INSERT INTO t VALUES ('wal-only');"
        )
        conn.commit()
        wal = stage.with_name(stage.name + "-wal")
        shutil.copy2(stage, db_path)
        shutil.copy2(wal, db_path.with_name(db_path.name + "-wal"))
    finally:
        conn.close()


def test_wal_only_commits_survive_sidecar_removal(monkeypatch, tmp_path: Path):
    """删 sidecar 之前必须先 checkpoint ⇒ **只在 WAL 里的提交**不会随 ``-wal`` 一起丢（T3-3）。

    夹具：目标库的提交只在 ``-wal`` 里（主库里没有）。
    场景：快照失败（只警告）+ 原子替换失败（模拟目标被占用）⇒ 目标库保持原样。
    断言：那一行**仍在** ⇒ 说明 WAL 已先落进主库，删 ``-wal`` 没丢数据。
    """
    src = tmp_path / "src.db"
    _make_wal_db(src)
    out_dir = tmp_path / "backups"
    key = tmp_path / "key.txt"
    enc = _make_backup(src, out_dir, key)

    target = tmp_path / "live.db"
    _make_wal_only_db(target)
    # 前提断言：``-wal`` 非空（提交确实还在 WAL 里；**不能**用 sqlite 打开它来查，
    # 那样会触发 close 时 checkpoint、把提交并进主库 ⇒ 夹具失效）
    wal = tmp_path / "live.db-wal"
    assert wal.exists() and wal.stat().st_size > 0

    # 快照失败（模拟旧目标库已损坏）+ 原子替换失败（模拟目标被占用）
    monkeypatch.setattr(restore_db, "snapshot", lambda src_path, dest_path: False)

    def _boom(a, b):
        raise OSError("simulated: 目标被占用")

    monkeypatch.setattr(restore_db.os, "replace", _boom)

    rc = restore_db.main(_restore_args(enc, target, key, "--expect-tables", "2"))

    assert rc == 2, "原子替换失败必须退 2（EXIT_USAGE）"
    # 那一行仍在 ⇒ WAL 已 checkpoint 进主库 ⇒ 删 -wal 没丢数据
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == "wal-only"
    finally:
        conn.close()
