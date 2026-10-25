"""配置测试（Sprint 0 + V2.0 Wave C 任务三）。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from packages.core import config as config_mod
from packages.core.config import Settings, reset_settings


def test_defaults():
    s = Settings()
    # Path("./data") 在不同 OS 上 resolve 形式不同，比较 Path 对象即可
    assert Path(s.data_dir).name == "data"
    assert s.db_path.name == "novelos.db"
    assert s.db_path.parent.resolve() == Path("./data").resolve()
    assert s.log_level == "INFO"
    assert s.api_host == "127.0.0.1"
    # V2.0 Wave C 任务三：端口统一收敛 → 默认 18081（取代原 8000）
    assert s.api_port == 18081


def test_explicit_db_path_overrides_default():
    s = Settings(data_dir=Path("/tmp/n"), db_path=Path("/tmp/n/custom.db"))
    assert s.db_path == Path("/tmp/n/custom.db")


def test_env_var_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOVELOS_DATA_DIR", "/tmp/env-data")
    monkeypatch.setenv("NOVELOS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NOVELOS_API_PORT", "9999")
    reset_settings()
    try:
        s = config_mod.get_settings()
    finally:
        reset_settings()
    # ★ BUG-59（复查修②）：data_dir 一律绝对化 ⇒ 相对形态按 CWD 解析成绝对（Windows 补盘符）
    assert Path(s.data_dir) == Path("/tmp/env-data").resolve()
    assert s.log_level == "DEBUG"
    assert s.api_port == 9999


def test_ensure_data_dir_creates(tmp_path: Path):
    target = tmp_path / "newdir"
    s = Settings(data_dir=target)
    assert not target.exists()
    s.ensure_data_dir()
    assert target.exists()
    assert target.is_dir()


def test_reset_clears_singleton():
    config_mod._settings_singleton = Settings(data_dir=Path("/x"))
    reset_settings()
    assert config_mod._settings_singleton is None


# ---------------------------------------------------------------------------
# Sprint 5 + V2.0 Wave C 任务三：端口优先级 — NOVELOS_PORT > NOVELOS_API_PORT > 18081。
# 任务书给死：``NOVELOS_PORT`` 是便捷变量，``NOVELOS_API_PORT`` 保留兼容。
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# BUG-59（critical · 复查修①②③）：落点一律绝对、缺 env 落仓外默认、启动路径显式注入
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """仓根（本文件 ``tests/unit/test_config.py`` 上溯三级）。"""
    return Path(__file__).resolve().parents[2]


def test_bug59_缺env落仓外绝对路径(monkeypatch: pytest.MonkeyPatch):
    """★ BUG-59 判别①：缺 ``NOVELOS_*`` ⇒ 落点**绝对**且**仓外**（且不抛异常）。

    - 旧行为：静默落 ``./data/novelos.db``（CWD 相对 ⇒ CWD＝仓根时正是生产真库处）；
    - 旧修法（``d4d9fb5``）：此处 ``raise RuntimeError`` ⇒ 抛点落在 pytest
      **collection 期** ⇒ ``tests/api/test_module_switch.py`` 整块 ERROR
      ⇒ 改为**仓外绝对默认**（``~/.novelos/data``）。
    """
    monkeypatch.delenv("NOVELOS_DATA_DIR", raising=False)
    monkeypatch.delenv("NOVELOS_DB_PATH", raising=False)
    s = Settings.load()
    assert s.data_dir.is_absolute(), f"数据根必须绝对化，实得 {s.data_dir}"
    assert s.db_path.is_absolute(), f"库路径必须绝对化，实得 {s.db_path}"
    assert s.db_path.parent == s.data_dir
    assert s.data_dir == Settings.default_data_dir()
    # 默认落点必须在**仓外**（仓内 ``data/`` ＝ 生产真库所在处）
    assert _repo_root() not in s.data_dir.parents
    assert _repo_root() not in s.db_path.parents


def test_bug59_相对env也绝对化且数据根绝对(monkeypatch: pytest.MonkeyPatch):
    """★ BUG-59 判别②：只给**相对形态**的 ``NOVELOS_DB_PATH`` ⇒ 两个落点**都绝对**。

    - 旧行为：``data_dir`` 落相对 ``./data`` ⇒ ``ensure_data_dir()`` 在 CWD 建 ``./data``；
    - 本条补上 ``data_dir.is_absolute()`` 断言（旧用例只断言 ``db_path`` ⇒ 判别力有缺口）。
    """
    monkeypatch.delenv("NOVELOS_DATA_DIR", raising=False)
    monkeypatch.setenv("NOVELOS_DB_PATH", "rel-data/rel.db")
    s = Settings.load()
    assert s.db_path.is_absolute(), f"库路径必须绝对化，实得 {s.db_path}"
    assert s.data_dir.is_absolute(), f"数据根必须绝对化，实得 {s.data_dir}"
    assert s.data_dir == s.db_path.parent
    assert s.data_dir == (Path.cwd() / "rel-data").resolve()


def test_bug59_启动路径显式注入env且不覆盖显式配置(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """★ BUG-59 判别③：启动路径必须**显式注入** env（``ensure_env_defaults``）。

    - 缺 env ⇒ 注入**仓外绝对**落点；
    - 已显式给 ⇒ **不覆盖**（运维配置优先）；只给 data_dir ⇒ 库路径按归一推导。
    """
    monkeypatch.delenv("NOVELOS_DATA_DIR", raising=False)
    monkeypatch.delenv("NOVELOS_DB_PATH", raising=False)
    config_mod.ensure_env_defaults()
    assert os.environ["NOVELOS_DATA_DIR"] == str(Settings.default_data_dir())
    assert os.environ["NOVELOS_DB_PATH"] == str(Settings.default_db_path())
    assert Path(os.environ["NOVELOS_DATA_DIR"]).is_absolute()
    # 幂等：重复注入不改变已注入的值
    config_mod.ensure_env_defaults()
    assert os.environ["NOVELOS_DATA_DIR"] == str(Settings.default_data_dir())
    # 运维显式配置不被覆盖
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NOVELOS_DB_PATH", raising=False)
    config_mod.ensure_env_defaults()
    assert os.environ["NOVELOS_DATA_DIR"] == str(tmp_path)
    assert os.environ["NOVELOS_DB_PATH"] == str(Path(tmp_path) / Settings.DEFAULT_DB_NAME)


def test_bug59_缺env落默认时必打WARNING(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """★ BUG-59 判别④：走兜底落点时**必须出声**（WARNING），不许一声不响。

    - 缺 env ⇒ 落仓外默认 ⇒ 必须打 WARNING 写明「未注入 ⇒ 已落仓外默认 <路径>」；
    - 已注入 env ⇒ **不**打这条 WARNING（不误报）。
    """
    monkeypatch.delenv("NOVELOS_DATA_DIR", raising=False)
    monkeypatch.delenv("NOVELOS_DB_PATH", raising=False)
    with caplog.at_level(logging.WARNING, logger="packages.core.config"):
        Settings.load()
    hits = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "未注入" in r.getMessage()
        and "仓外默认" in r.getMessage()
    ]
    assert hits, f"缺 env 未打 WARNING（兜底落点静默）⇒ caplog={caplog.records}"
    assert hits[0].levelno == logging.WARNING
    assert str(Settings.default_data_dir()) in hits[0].getMessage()
    # 已注入 env ⇒ 不再打这条 WARNING（不误报）
    caplog.clear()
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOVELOS_DB_PATH", str(tmp_path / Settings.DEFAULT_DB_NAME))
    with caplog.at_level(logging.WARNING, logger="packages.core.config"):
        Settings.load()
    assert not [r for r in caplog.records if "仓外默认" in r.getMessage()], (
        "已注入 env 却仍报兜底落点"
    )
