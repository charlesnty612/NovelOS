"""``scripts/migrate.py`` CLI 参数纪律测试（V3.9 全量检修）。

背景（F12-migrate）：该脚本原先完全不读 ``argv``——``--help`` 或拼错的参数
都会照常对运行库执行迁移。迁移是 DDL 操作，破坏半径大，参数必须先过检查。

覆盖：
- 未知参数 → exit 2 + usage，且**不触碰**数据库（不建库、不迁移）；
- ``--help`` → exit 0 + usage，同样不迁移；
- 无参数 → 正常迁移（临时 NOVELOS_DATA_DIR 下的新库，全链 0001~0025）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import scripts.migrate as migrate
from packages.core.config import reset_settings


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``get_settings()`` 指向临时目录。

    ``get_settings`` 是全局单例（``_settings_singleton``）：必须先 ``reset_settings()``
    清缓存，否则单例里可能已经装着别处加载的 data_dir（main() 会迁到那个库）。
    测试结束后再清一次，把单例归还给后续测试重新按环境变量加载。
    """
    target = tmp_path / "data"
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(target))
    monkeypatch.delenv("NOVELOS_DB_PATH", raising=False)
    reset_settings()
    yield target
    reset_settings()


def test_unknown_arg_exits_2_without_migrating(data_dir: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        migrate.main(["--help-me-not"])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower(), captured.err
    # 未创建库 / 未迁移
    assert not (data_dir / "novelos.db").exists()
    assert captured.out == ""


def test_positional_arg_exits_2_without_migrating(data_dir: Path, capsys) -> None:
    """位置参数（如误把库路径当参数）同样拒绝。"""
    with pytest.raises(SystemExit) as exc:
        migrate.main(["data/novelos.db"])

    assert exc.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()
    assert not (data_dir / "novelos.db").exists()


def test_help_exits_0_without_migrating(data_dir: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        migrate.main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert not (data_dir / "novelos.db").exists()


def test_no_args_runs_migrations(data_dir: Path, capsys) -> None:
    assert migrate.main([]) == 0

    out = capsys.readouterr().out
    assert "applied=" in out
    db_file = data_dir / "novelos.db"
    assert db_file.exists()
    conn = sqlite3.connect(db_file)
    try:
        applied = {
            r[0]
            for r in conn.execute("SELECT filename FROM _migrations").fetchall()
        }
    finally:
        conn.close()
    assert "0001_init.sql" in applied
    assert "0015_volumes.sql" in applied
