# -*- coding: utf-8 -*-
"""``migrate_backups_to_sync.py`` 的失败语义（审计 C F-2，2026-10-23）。

缺陷形状（假成功 / 源文件永留）
------------------------------
``main`` 原先在 ``dest.exists()`` 分支只打印 ``[SKIP]`` 就 ``continue``，``ok`` 仍
True ⇒ ``return 0``。而「加密完成、删源之前被中断」正好留下 **源文件 + 目标密文**
⇒ 重跑时 rc 0 把「迁移未完成」伪装成成功；若目标还是**半截密文**，则连可恢复性
都无法保证（本批同时补「加密后立刻解密冒烟」）。

本文件的判别（各自对应 l15 的一条突变）：
1. 正常路径：加密 → 解密冒烟 → 删源，rc 0；
2. 目标已存在 + 源仍在 ⇒ **rc 1**（不再静默成功）；``--accept-existing`` 才放行；
3. 解密冒烟失败 ⇒ 删掉坏密文、**保留源**、rc 1（先删源再发现解不开 = 备份即丢）；
4. ``--dry-run`` 仍然零副作用（不调 openssl、不写不删）。

零真实 openssl / 零真实网盘：``subprocess.run`` 与 ``Path`` 的 D: 扫描根都被
monkeypatch 到 tmp_path；不触碰真实备份目录。
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

import scripts.migrate_backups_to_sync as mbs

_REAL_PATH = pathlib.Path


def _fake_openssl(monkeypatch, *, decrypt_fails: bool = False) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "-d" in cmd:
            if decrypt_fails:
                raise subprocess.CalledProcessError(1, cmd)
            out = _REAL_PATH(cmd[cmd.index("-out") + 1])
            out.write_bytes(b"plain-probe")
            return subprocess.CompletedProcess(cmd, 0)
        if "enc" in cmd and "-out" in cmd:
            out = _REAL_PATH(cmd[cmd.index("-out") + 1])
            out.write_bytes(b"cipher")
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mbs.subprocess, "run", fake_run)
    return calls


@pytest.fixture()
def script_env(tmp_path, monkeypatch):
    src = tmp_path / "src" / "backup.db"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x" * 16)
    monkeypatch.setattr(mbs, "MOVES", [(src, "backup.db.enc")])
    monkeypatch.setattr(mbs, "DEST_ROOT", tmp_path / "dest")
    monkeypatch.setattr(mbs, "PRUNE_DIRS", [])
    key = tmp_path / "backup.key"
    key.write_text("k\n", encoding="utf-8")
    monkeypatch.setattr(mbs, "KEY_FILE", key)
    monkeypatch.setattr(mbs, "LIVE_DB", tmp_path / "live.db")
    # main 的「残留核对」会扫 r"D:\zcodeproject" —— 把它重定向到 tmp（只读 glob）。
    monkeypatch.setattr(
        mbs,
        "Path",
        lambda p=".": tmp_path / "scan_root" if str(p) == r"D:\zcodeproject" else _REAL_PATH(p),
    )
    return src


def test_success_path_encrypts_then_decrypt_smoke_then_deletes_source(script_env, monkeypatch):
    calls = _fake_openssl(monkeypatch)
    rc = mbs.main([])
    assert rc == 0
    assert not script_env.exists(), "成功路径必须删源（移动语义）"
    assert (mbs.DEST_ROOT / "backup.db.enc").is_file()
    assert any("-d" in cmd for cmd in calls), "加密后必须做一次解密冒烟（-d）"


def test_existing_target_with_source_present_is_failure(script_env, monkeypatch):
    _fake_openssl(monkeypatch)
    dest = mbs.DEST_ROOT / "backup.db.enc"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"half-cipher")
    rc = mbs.main([])
    assert rc == 1, "目标已存在 + 源仍在 ⇒ 必须 rc 1（此前静默 rc 0 掩盖未完成迁移）"
    assert script_env.exists(), "失败时源文件必须保留"
    assert dest.read_bytes() == b"half-cipher", "失败时不得改写既有目标"


def test_accept_existing_allows_resume(script_env, monkeypatch):
    _fake_openssl(monkeypatch)
    dest = mbs.DEST_ROOT / "backup.db.enc"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"done")
    rc = mbs.main(["--accept-existing"])
    assert rc == 0
    assert script_env.exists(), "--accept-existing 是 skip 语义：源由人工清理"


def test_decrypt_smoke_failure_removes_bad_ciphertext_and_keeps_source(script_env, monkeypatch):
    _fake_openssl(monkeypatch, decrypt_fails=True)
    rc = mbs.main([])
    assert rc == 1
    assert script_env.exists(), "解密失败时必须保留源（否则备份即丢）"
    assert not (mbs.DEST_ROOT / "backup.db.enc").exists(), (
        "坏/半截密文必须删除，否则下次会被 skip-exists 挡住"
    )


def test_dry_run_stays_side_effect_free(script_env, monkeypatch, capsys):
    calls = _fake_openssl(monkeypatch)
    rc = mbs.main(["--dry-run"])
    assert rc == 0
    assert calls == [], "dry-run 不得调用 openssl（含生成密钥）"
    assert script_env.exists()
    assert not (mbs.DEST_ROOT / "backup.db.enc").exists()
    out = capsys.readouterr().out
    assert "实际执行时「目标已存在」默认记" in out, "dry-run 必须提示 rc 语义"
