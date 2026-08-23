"""配置测试（Sprint 0）。"""

from __future__ import annotations

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
    assert s.api_port == 8000


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
    assert str(s.data_dir).replace("\\", "/") == "/tmp/env-data"
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
