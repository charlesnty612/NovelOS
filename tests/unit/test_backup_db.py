"""``scripts/backup_db.py`` 的行为测试（**合成夹具 + 临时库**，不碰真库）。

判别点（按 AGENTS「断言了结果 ≠ 判别了机制」挑，期望值不抄实现）：
- 备份**真的一致**（行数 + integrity_check），且**源库不被改**；
- **加密模式下输出目录里没有任何明文 .db**（安全关键：明文落进同步盘 = 密钥上云）；
- 密文**能解开且内容与源库一致**（否则「加密」只是把备份弄丢了）；
- ``--redact-secrets`` 只改副本、且 ``api_key`` 变 ``***``；
- ``prune`` 只保留最近 N 份；
- **探测不到同步盘时报错退出**（不静默回退到本机 —— 用户裁决「本机不留备份」）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_db  # noqa: E402

#: 被测脚本路径（**模块级常量**：突变验证时把它指向「临时副本」，不在仓库文件上改）。
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"


def _run_backup(args: list[str]) -> subprocess.CompletedProcess:
    """跑一次备份脚本（**子进程** ⇒ 断言的是进程退出码，不是「函数被调用」）。

    ⚠️ 环境变量必须**清干净**（``NOVELOS_BACKUP_KEY`` / ``NOVELOS_BACKUP_DIR``）：
    否则本机恰好设了密钥环境变量时，「密钥缺失 ⇒ 报错」这条用例会假绿。
    """
    env = {k: v for k, v in os.environ.items() if k not in {"NOVELOS_BACKUP_KEY", "NOVELOS_BACKUP_DIR"}}
    return subprocess.run(
        [sys.executable, str(BACKUP_SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _make_db(path: Path) -> None:
    """合成一个最小可备份的库（**表结构照生产契约**，内容是虚构的）。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE model_profiles (
            profile_id TEXT PRIMARY KEY,
            name TEXT,
            provider TEXT,
            model TEXT,
            params_json TEXT,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE model_connections (
            connection_id TEXT PRIMARY KEY,
            name TEXT,
            provider TEXT,
            base_url TEXT,
            params_json TEXT,
            enabled INTEGER DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO model_profiles VALUES (?,?,?,?,?,?)",
        (
            "mprof_test01",
            "合成档案",
            "openai_compatible",
            "gpt-fake",
            json.dumps({"api_key": "sk-SYNTHETIC-NOT-REAL", "top_p": 0.9}),
            1,
        ),
    )
    conn.execute(
        "INSERT INTO model_connections VALUES (?,?,?,?,?,?)",
        (
            "mconn_test01",
            "合成连接",
            "openai_compatible",
            "https://example.invalid/v1",
            json.dumps({"api_key": "oc-SYNTHETIC-NOT-REAL"}),
            1,
        ),
    )
    conn.commit()
    conn.close()


def _row_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _decrypt(enc: Path, key_file: Path, dest: Path) -> None:
    subprocess.run(
        [
            str(backup_db.find_openssl()),
            "enc",
            "-d",
            f"-{backup_db.CIPHER}",
            "-pbkdf2",
            "-iter",
            backup_db.PBKDF2_ITER,
            "-in",
            str(enc),
            "-out",
            str(dest),
            "-pass",
            f"file:{key_file}",
        ],
        check=True,
    )


def test_backup_is_consistent_and_leaves_source_untouched(tmp_path: Path):
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    # ⚠️ 2026-10-23：**默认已加密**（D-4）⇒ 本用例要断言「明文备份的内容正确」，
    # 故显式加 ``--plaintext``；默认路径由 ``test_default_backup_is_encrypted_*`` 单独钉。
    proc = _run_backup(
        [
            "--db",
            str(src),
            "--out",
            str(out),
            "--keep",
            "5",
            "--plaintext",
        ]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    backups = list(out.glob("novelos_*.db"))
    assert len(backups) == 1
    assert _row_count(backups[0], "model_profiles") == 1
    conn = sqlite3.connect(backups[0])
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    # 源库不被改
    assert _row_count(src, "model_profiles") == 1


def test_encrypt_mode_leaves_no_plaintext_in_output_and_is_recoverable(tmp_path: Path):
    """安全关键：密文可恢复 **且** 输出目录里看不到明文。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "key.txt"
    # ⚠️ 2026-10-23：密钥文件**不存在时不再静默生成**（D-5）⇒ 首次创建须显式 ``--init-key``。
    proc = _run_backup(
        [
            "--db",
            str(src),
            "--out",
            str(out),
            "--encrypt",
            "--init-key",
            "--key-file",
            str(key),
        ]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # ① 输出目录里**不得**有任何明文 .db（明文落进同步盘 = 密钥上云）
    assert list(out.glob("novelos_*.db")) == []
    encs = list(out.glob("novelos_*.db.enc"))
    assert len(encs) == 1
    # ② 密文里**不得**出现明文密钥
    assert b"SYNTHETIC-NOT-REAL" not in encs[0].read_bytes()

    # ③ 密文能解开且内容一致（否则「加密」只是把备份弄丢了）
    restored = tmp_path / "restored.db"
    _decrypt(encs[0], key, restored)
    assert _row_count(restored, "model_profiles") == 1
    conn = sqlite3.connect(restored)
    try:
        raw = conn.execute("SELECT params_json FROM model_profiles").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(raw)["api_key"] == "sk-SYNTHETIC-NOT-REAL"


def test_redact_secrets_touches_only_the_copy(tmp_path: Path):
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    # ⚠️ 2026-10-23：默认加密 ⇒ 本用例断言的是**明文脱敏副本**的内容，故显式 ``--plaintext``。
    proc = _run_backup(
        [
            "--db",
            str(src),
            "--out",
            str(out),
            "--redact-secrets",
            "--plaintext",
        ]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    copy = list(out.glob("novelos_*.db"))[0]
    conn = sqlite3.connect(copy)
    try:
        prof = json.loads(conn.execute("SELECT params_json FROM model_profiles").fetchone()[0])
        connr = json.loads(conn.execute("SELECT params_json FROM model_connections").fetchone()[0])
    finally:
        conn.close()
    assert prof["api_key"] == "***"
    assert connr["api_key"] == "***"
    # 源库**不得**被抹（抹了就把真库毁了）
    conn = sqlite3.connect(src)
    try:
        raw = conn.execute("SELECT params_json FROM model_profiles").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(raw)["api_key"] == "sk-SYNTHETIC-NOT-REAL"


def test_prune_keeps_only_the_newest_n(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    for i in range(5):
        (out / f"novelos_2026010{i}_000000.db").write_bytes(b"x")
    removed = backup_db.prune(out, 2)
    assert len(removed) == 3
    assert len(list(out.glob("novelos_*.db*"))) == 2


def test_resolve_out_dir_refuses_to_fall_back_to_local(monkeypatch, tmp_path: Path):
    """探测不到同步盘 ⇒ **报错**，不静默回退到本机目录。"""
    monkeypatch.setattr(backup_db, "SYNC_CANDIDATES", (tmp_path / "nope",))
    monkeypatch.delenv("NOVELOS_BACKUP_DIR", raising=False)
    with pytest.raises(SystemExit) as exc:
        backup_db.resolve_out_dir(None)
    assert "不静默回退到本机目录" in str(exc.value)


def test_resolve_out_dir_prefers_env_then_explicit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NOVELOS_BACKUP_DIR", str(tmp_path / "from_env"))
    assert backup_db.resolve_out_dir(None) == tmp_path / "from_env"
    assert backup_db.resolve_out_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"


# ---------------------------------------------------------------------------
# 2026-10-23 批次三十一（D-4 默认加密 / D-5 密钥缺失 / 密文自检）
# ---------------------------------------------------------------------------


def test_default_backup_is_encrypted_and_decrypts_to_source_content(tmp_path: Path):
    """**不带任何 flag** 的常规备份必须产出 ``.enc``，且密文能解回与源库一致的内容。

    判别的是**产物形态 + 密文可解性**，不是「函数被调用」。
    """
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "key.txt"

    proc = _run_backup(
        ["--db", str(src), "--out", str(out), "--key-file", str(key), "--init-key"]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # ① 默认走加密：输出目录里**不得**有明文 .db
    assert list(out.glob("novelos_*.db")) == []
    encs = list(out.glob("novelos_*.db.enc"))
    assert len(encs) == 1, proc.stdout + proc.stderr

    # ② 密文可解 ⇒ 解出来的库与源库内容一致（否则「加密」只是把备份弄丢了）
    restored = tmp_path / "restored.db"
    _decrypt(encs[0], key, restored)
    assert _row_count(restored, "model_profiles") == 1
    assert _row_count(restored, "model_connections") == 1
    conn = sqlite3.connect(restored)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        raw = conn.execute("SELECT params_json FROM model_profiles").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(raw)["api_key"] == "sk-SYNTHETIC-NOT-REAL"


def test_plaintext_flag_is_required_to_get_plaintext_output(tmp_path: Path):
    """``--plaintext`` 才是明文路径的**显式** opt-in（默认加密 ⇒ 行为变更）。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"

    proc = _run_backup(["--db", str(src), "--out", str(out), "--plaintext"])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dbs = list(out.glob("novelos_*.db"))
    assert len(dbs) == 1, proc.stdout + proc.stderr
    assert list(out.glob("novelos_*.db.enc")) == []
    assert _row_count(dbs[0], "model_profiles") == 1


def test_missing_key_file_aborts_instead_of_silently_generating(tmp_path: Path):
    """密钥文件缺失 ⇒ **报错退出**，**不**静默生成（D-5：否则旧备份永久不可解）。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "nested" / "backup.key"

    proc = _run_backup(["--db", str(src), "--out", str(out), "--key-file", str(key)])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    # 关键断言：**没有**被静默创建（这正是旧实现的行为）
    assert not key.exists()
    # 也没有产出任何备份（失败不留半个产物）
    assert list(out.glob("novelos_*")) == []
    assert "密钥" in proc.stdout + proc.stderr


def test_init_key_creates_key_file_and_backup_succeeds(tmp_path: Path):
    """``--init-key`` 是**唯一**创建密钥的入口。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "nested" / "backup.key"

    proc = _run_backup(
        ["--db", str(src), "--out", str(out), "--key-file", str(key), "--init-key"]
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert key.is_file()
    assert key.read_text(encoding="utf-8").strip()
    assert len(list(out.glob("novelos_*.db.enc"))) == 1


def test_existing_key_file_is_reused_and_left_unchanged(tmp_path: Path):
    """**既有**密钥文件 ⇒ 行为不变：复用，且内容逐字节不变（不轮换密钥）。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "key.txt"
    key.write_text("SYNTHETIC-KEY-MATERIAL\n", encoding="utf-8")
    before = key.read_bytes()

    proc = _run_backup(["--db", str(src), "--out", str(out), "--key-file", str(key)])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert key.read_bytes() == before
    encs = list(out.glob("novelos_*.db.enc"))
    assert len(encs) == 1
    # 用这个既有密钥能解开 ⇒ 复用的确实是它
    restored = tmp_path / "restored.db"
    _decrypt(encs[0], key, restored)
    assert _row_count(restored, "model_profiles") == 1


def test_self_check_failure_writes_no_ciphertext_and_deletes_plaintext(
    monkeypatch, capsys, tmp_path: Path
):
    """密文**自检**失败 ⇒ 报错退出、**不落盘密文**、**明文副本默认被删除**。

    注入方式：把 ``encrypt_file`` 换成**不加密的替身**（原样拷贝）⇒ 解密往返必然失败。
    这是本仓既有的「替身复刻关键副作用」做法，不是 mock 掉被测机制。

    ⚠️ 2026-10-23 对抗审查 T3-2：旧行为把**含明文 API 密钥的整库**留在 ``%TEMP%``
    且无限期保留 ⇒ 现在**默认删除**（源库未被触碰、备份可重跑 ⇒ 删副本不丢数据）。
    """
    src = tmp_path / "src.db"
    _make_db(src)
    src_before = src.read_bytes()
    out = tmp_path / "out"
    key = tmp_path / "key.txt"
    key.write_text("SYNTHETIC-KEY-MATERIAL\n", encoding="utf-8")

    def _fake_encrypt(src_path: Path, dest_path: Path, key_file: Path) -> None:
        shutil.copyfile(src_path, dest_path)  # **不加密** ⇒ 自检必然失败

    monkeypatch.setattr(backup_db, "encrypt_file", _fake_encrypt)

    with pytest.raises(SystemExit) as exc:
        backup_db.main(
            [
                "--db",
                str(src),
                "--out",
                str(out),
                "--key-file",
                str(key),
            ]
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    message = captured.out + captured.err

    # ① 不落盘密文
    assert list(out.glob("novelos_*.db.enc")) == [], message
    # ② 源库逐字节未变
    assert src.read_bytes() == src_before
    # ③ 明文副本**默认被删除**（不留含明文密钥的整库在临时目录）
    match = re.search(r"工作目录：(\S+)", message)
    assert match, message
    work_dir = Path(match.group(1))
    assert not work_dir.exists(), f"明文副本仍在 {work_dir}（含明文 API 密钥）"
    assert "已删除" in message


def test_keep_plaintext_on_failure_flag_keeps_the_copy(
    monkeypatch, capsys, tmp_path: Path
):
    """``--keep-plaintext-on-failure`` ⇒ **显式**保留明文副本（默认关）。"""
    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "key.txt"
    key.write_text("SYNTHETIC-KEY-MATERIAL\n", encoding="utf-8")

    def _fake_encrypt(src_path: Path, dest_path: Path, key_file: Path) -> None:
        shutil.copyfile(src_path, dest_path)

    monkeypatch.setattr(backup_db, "encrypt_file", _fake_encrypt)

    with pytest.raises(SystemExit) as exc:
        backup_db.main(
            [
                "--db",
                str(src),
                "--out",
                str(out),
                "--key-file",
                str(key),
                "--keep-plaintext-on-failure",
            ]
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    message = captured.out + captured.err
    match = re.search(r"工作目录：(\S+)", message)
    assert match, message
    work_dir = Path(match.group(1))
    assert work_dir.exists(), message
    assert list(work_dir.glob("*.db")), message
    assert "已保留" in message
    assert "明文 API 密钥" in message
    # ⚠️ **测试自己必须回收**（2026-10-23 补）：本用例**故意**让脚本保留明文副本
    # （那正是被断言的行为）⇒ 但「保留」是**脚本的**决定，不是「让垃圾堆在系统 Temp」
    # 的许可。旧实现跑完就把目录留在 Temp ⇒ 每跑一次全量回归多一个
    # `novelos_backup_*`（实测曾躺着含明文 API 密钥的整库）⇒ 逐期累积成几十个。
    shutil.rmtree(work_dir, ignore_errors=True)
    assert not work_dir.exists(), f"测试自身泄漏了明文副本目录：{work_dir}"


def test_cleanup_stale_work_dirs_only_removes_old_own_dirs(monkeypatch, tmp_path: Path):
    """兜底清理：**只**删「本脚本前缀 + 陈旧」的目录；别人的、新鲜的一律不动。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old = tmp_path / f"{backup_db.WORK_DIR_PREFIX}old"
    fresh = tmp_path / f"{backup_db.WORK_DIR_PREFIX}fresh"
    foreign = tmp_path / "someone_elses_dir"
    for d in (old, fresh, foreign):
        d.mkdir()
        (d / "payload.db").write_bytes(b"x")
    # 把 old 的 mtime 调到 48 小时前
    stale_at = time.time() - 48 * 3600
    os.utime(old, (stale_at, stale_at))

    removed = backup_db.cleanup_stale_work_dirs()

    assert old in removed
    assert not old.exists(), "陈旧的同前缀目录应被清理"
    assert fresh.exists(), "新鲜目录（可能是正在跑的实例）不得动"
    assert foreign.exists(), "别人的目录不得动"
    assert (fresh / "payload.db").exists()


def test_backup_cleans_stale_work_dir_on_startup(tmp_path: Path):
    """兜底清理的**接线**：跑一次备份 ⇒ 临时目录里的陈旧工作目录被清掉。

    ⚠️ 子进程的临时根用 ``TEMP``/``TMP`` 环境变量控制（Windows 下
    ``tempfile.gettempdir()`` 依次看 ``TMPDIR``/``TEMP``/``TMP``）。
    """
    temp_root = tmp_path / "troot"
    temp_root.mkdir()
    stale = temp_root / f"{backup_db.WORK_DIR_PREFIX}stale"
    stale.mkdir()
    (stale / "novelos_20260101_000000.db").write_bytes(b"plaintext-with-secrets")
    stale_at = time.time() - 48 * 3600
    os.utime(stale, (stale_at, stale_at))

    src = tmp_path / "src.db"
    _make_db(src)
    out = tmp_path / "out"
    key = tmp_path / "key.txt"

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"NOVELOS_BACKUP_KEY", "NOVELOS_BACKUP_DIR"}
    }
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)
    proc = subprocess.run(
        [
            sys.executable,
            str(BACKUP_SCRIPT),
            "--db",
            str(src),
            "--out",
            str(out),
            "--key-file",
            str(key),
            "--init-key",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not stale.exists(), "陈旧工作目录应被启动时的兜底清理删掉"
    assert "陈旧工作目录" in proc.stdout
