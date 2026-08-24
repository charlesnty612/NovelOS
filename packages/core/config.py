"""NovelOS 配置层（Sprint 0 基础设施）。

使用 pydantic BaseSettings（v1 兼容写法，避免 BaseSettings v2 的 pydantic-settings 依赖）。
所有环境变量以 NOVELOS_ 前缀覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


class Settings:
    """运行时配置。

    设计为显式字段 + 类方法 ``load``，以便测试可注入临时目录。
    """

    data_dir: Path
    db_path: Path
    log_level: str
    api_host: str
    api_port: int

    def __init__(
        self,
        data_dir: Path | str = "./data",
        db_path: Path | str | None = None,
        log_level: str = "INFO",
        api_host: str = "127.0.0.1",
        api_port: int = 18081,
    ) -> None:
        self.data_dir = Path(data_dir)
        if db_path is None:
            db_path = self.data_dir / "novelos.db"
        self.db_path = Path(db_path)
        self.log_level = log_level
        self.api_host = api_host
        self.api_port = api_port

    @classmethod
    def load(cls) -> "Settings":
        """从环境变量加载，NOVELOS_ 前缀。

        端口优先级（V2.0 Wave C 任务三 统一收敛）：
        - ``NOVELOS_PORT``（通用便捷变量） > ``NOVELOS_API_PORT``（旧精细变量）> 默认 18081。
        旧精细变量保留兼容：仅设置 ``NOVELOS_API_PORT`` 时仍按其值生效（基线测试断言）。
        默认 18081 由 ``main.py`` 的 ``__main__`` 入口 + ``Settings.api_port`` 共同承载；
        任何位置调 ``Settings.load()`` 均得到同一默认值。
        """
        data_dir = _env("NOVELOS_DATA_DIR", "./data")
        db_path = _env("NOVELOS_DB_PATH", "")  # 空则按 data_dir 推导
        log_level = _env("NOVELOS_LOG_LEVEL", "INFO")
        api_host = _env("NOVELOS_API_HOST", "127.0.0.1")
        # 端口优先级：NOVELOS_PORT > NOVELOS_API_PORT > 18081
        port_env = _env("NOVELOS_PORT", "") or _env("NOVELOS_API_PORT", "")
        api_port = int(port_env) if port_env else 18081
        return cls(
            data_dir=data_dir,
            db_path=db_path or None,
            log_level=log_level,
            api_host=api_host,
            api_port=api_port,
        )

    def ensure_data_dir(self) -> None:
        """确保 data_dir 存在；调用方负责选择时机。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "log_level": self.log_level,
            "api_host": self.api_host,
            "api_port": self.api_port,
        }


_settings_singleton: Settings | None = None


def get_settings() -> Settings:
    """全局单例；测试可通过 ``reset_settings`` 重新加载。"""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings.load()
    return _settings_singleton


def reset_settings() -> None:
    """测试辅助：清除单例以便重载环境变量。"""
    global _settings_singleton
    _settings_singleton = None
