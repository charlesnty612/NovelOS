# -*- coding: utf-8 -*-
"""``backup_db.py`` 的 ``--keep`` / ``--list`` 语义（审计 C F-6，2026-10-23）。

缺陷形状（参数语义反直觉 + 预检不校验输入）
------------------------------------------
1. ``--keep 0``：原实现 ``for old in backups[:-keep] if keep > 0 else []`` ⇒ 0 走
   「**全保留**」分支，与「保留最近 N 份」直觉相反；又有人会读成「全删」。
   两种解释都危险 ⇒ 本批改为：CLI 显式拒绝 ``keep < 1``（argparse exit 2），
   函数层 ``prune(keep=0)`` 抛 ValueError。
2. ``--list``：原实现**先返回**再做 ``src.is_file()`` 校验 ⇒ ``--db`` 打错也打印
   「未执行任何写入」+ exit 0，预检看不出问题。本批把源库校验前移到 ``--list`` 之前。

判别（对应 l15 ``BACKUP-KEEP-1``）：``--keep 0`` 必须 SystemExit(2)；
``--list`` 对不存在的源库必须非 0（此前 0）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.backup_db as bdb


def test_keep_zero_is_rejected_by_argparse(tmp_path: Path):
    src = tmp_path / "src.db"
    src.write_bytes(b"x")
    with pytest.raises(SystemExit) as excinfo:
        bdb.main(["--db", str(src), "--out", str(tmp_path / "out"), "--keep", "0"])
    assert excinfo.value.code == 2, "--keep 0 必须走 argparse error（exit 2），不得静默全保留"


def test_prune_function_rejects_zero(tmp_path: Path):
    with pytest.raises(ValueError, match="keep must be >= 1"):
        bdb.prune(tmp_path, 0)


def test_list_checks_source_before_returning(tmp_path: Path):
    with pytest.raises(SystemExit) as excinfo:
        bdb.main(
            ["--db", str(tmp_path / "missing.db"), "--out", str(tmp_path / "out"), "--list"]
        )
    assert excinfo.value.code not in (0, None), (
        "--list 必须先校验源库；打错路径不得 exit 0"
    )


def test_list_ok_for_existing_source(tmp_path: Path):
    src = tmp_path / "src.db"
    src.write_bytes(b"x")
    out = tmp_path / "out"
    rc = bdb.main(["--db", str(src), "--out", str(out), "--list"])
    assert rc == 0
    assert not out.exists(), "--list 不得创建输出目录"


def test_prune_keeps_exactly_the_newest_n(tmp_path: Path):
    """``prune(keep=N)`` 必须**恰好保留 N 份最新备份**（撤边界/off-by-one 必红）。

    2026-10-23 批次二十九 task-26：l20 的 ``SC-BACKUP-KEEP`` 把切片
    ``backups[:-keep]`` 改成 ``backups[:-(keep + 1)]`` ⇒ 少删一份（磁盘泄漏）；
    本用例同时钉「删了几份」与「剩下哪几份」。
    """
    out = tmp_path / "out"
    out.mkdir()
    for day in range(1, 6):
        (out / f"novelos_2026010{day}_000000.db").write_bytes(b"x")
    removed = bdb.prune(out, 2)
    assert len(removed) == 3, f"应删 3 份，实际删了 {len(removed)} 份"
    remaining = sorted(p.name for p in out.glob("novelos_*.db*"))
    assert remaining == [
        "novelos_20260104_000000.db",
        "novelos_20260105_000000.db",
    ], remaining
