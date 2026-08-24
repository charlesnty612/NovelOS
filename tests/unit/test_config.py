"""配置测试（Sprint 0 + V2.0 Wave C 任务三）。"""

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


# ---------------------------------------------------------------------------
# Sprint 5 + V2.0 Wave C 任务三：端口优先级 — NOVELOS_PORT > NOVELOS_API_PORT > 18081。
# 任务书给死：``NOVELOS_PORT`` 是便捷变量，``NOVELOS_API_PORT`` 保留兼容。
# ---------------------------------------------------------------------------


def test_novelos_port_overrides_api_port(monkeypatch: pytest.MonkeyPatch):
    """同时设置两个端口变量时，``NOVELOS_PORT`` 胜出。"""
    monkeypatch.setenv("NOVELOS_API_PORT", "9001")
    monkeypatch.setenv("NOVELOS_PORT", "18081")
    reset_settings()
    try:
        s = config_mod.get_settings()
    finally:
        reset_settings()
    assert s.api_port == 18081


def test_novelos_api_port_still_works_when_novelos_port_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    """仅设置 ``NOVELOS_API_PORT`` 时仍按其值生效（兼容基线 test_env_var_override）。"""
    monkeypatch.setenv("NOVELOS_API_PORT", "9999")
    monkeypatch.delenv("NOVELOS_PORT", raising=False)
    reset_settings()
    try:
        s = config_mod.get_settings()
    finally:
        reset_settings()
    assert s.api_port == 9999


def test_default_port_is_18081_when_no_env(monkeypatch: pytest.MonkeyPatch):
    """两个端口变量均未设置时，默认 18081（V2.0 Wave C 任务三统一收敛）。"""
    monkeypatch.delenv("NOVELOS_PORT", raising=False)
    monkeypatch.delenv("NOVELOS_API_PORT", raising=False)
    reset_settings()
    try:
        s = config_mod.get_settings()
    finally:
        reset_settings()
    assert s.api_port == 18081
