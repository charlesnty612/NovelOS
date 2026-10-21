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
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_db  # noqa: E402


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
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backup_db.py"),
            "--db",
            str(src),
            "--out",
            str(out),
            "--keep",
            "5",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backup_db.py"),
            "--db",
            str(src),
            "--out",
            str(out),
            "--encrypt",
            "--key-file",
            str(key),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backup_db.py"),
            "--db",
            str(src),
            "--out",
            str(out),
            "--redact-secrets",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
