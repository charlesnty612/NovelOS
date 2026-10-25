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

    ⚠️ BUG-14 受限面归一（2026-10-25 · OS开发 W1 批）：``data_dir`` 与 ``db_path``
    的默认值**原本分写两处**（``__init__`` 的 ``data_dir="./data"`` ＋ 推导式的
    ``db_path = data_dir/"novelos.db"``），而 ``load()`` 又按环境变量各读一次
    ⇒ **「数据根」与「库文件」两个概念在两处各自表述**，任何一处被单独改动
    就会出现「``data_dir`` 指 A、``db_path`` 指 B」的错配。

    ⇒ 现在**只有一个真源**：``data_dir`` 决定数据根，``db_path`` **恒定由
    ``data_dir`` 推导**（除非显式传入或显式给 ``NOVELOS_DB_PATH``）⇒
    「data_dir 与 db_path 归一」＝ 二者**不可能**再分叉。
    """

    data_dir: Path
    db_path: Path
    log_level: str
    api_host: str
    api_port: int
    disabled_modules: list[str]

    #: 数据根的**唯一**默认名（``data_dir`` 与 ``db_path`` 共用此真源）。
    DEFAULT_DATA_DIR_NAME: str = "data"
    #: 库文件名（``db_path`` 由 ``data_dir`` ＋ 此名推导）。
    DEFAULT_DB_NAME: str = "novelos.db"

    def __init__(
        self,
        data_dir: Path | str | None = None,
        db_path: Path | str | None = None,
        log_level: str = "INFO",
        api_host: str = "127.0.0.1",
        api_port: int = 18081,
        disabled_modules: list[str] | None = None,
    ) -> None:
        # ⚠️ 默认值取**类属性真源**（不再写字面量）⇒ data_dir 与 db_path 同源。
        self.data_dir = Path(data_dir) if data_dir is not None else Path(".") / self.DEFAULT_DATA_DIR_NAME
        if db_path is None:
            # ⚠️ 归一关键：db_path **恒**由 data_dir 推导 ⇒ 两者不可能分叉。
            db_path = self.data_dir / self.DEFAULT_DB_NAME
        self.db_path = Path(db_path)
        self.log_level = log_level
        self.api_host = api_host
        self.api_port = api_port
        self.disabled_modules = list(disabled_modules) if disabled_modules is not None else []

    @classmethod
    def load(cls) -> "Settings":
        """从环境变量加载，NOVELOS_ 前缀。

        端口优先级（V2.0 Wave C 任务三 统一收敛）：
        - ``NOVELOS_PORT``（通用便捷变量） > ``NOVELOS_API_PORT``（旧精细变量）> 默认 18081。
        旧精细变量保留兼容：仅设置 ``NOVELOS_API_PORT`` 时仍按其值生效（基线测试断言）。
        默认 18081 由 ``main.py`` 的 ``__main__`` 入口 ＋ ``Settings.api_port`` 共同承载；
        任何位置调 ``Settings.load()`` 均得到同一默认值。

        ⚠️ BUG-14 受限面归一（2026-10-25 · OS开发 W1 批）：
        - ``data_dir`` 的默认 **不再写死字面量** ``"./data"``，改由
          ``DEFAULT_DATA_DIR_NAME`` 真源推导 ⇒ 与 ``__init__`` 同一真源。
        - ``db_path`` **只有**在 ``NOVELOS_DB_PATH`` **显式给且非空**时才独立取值；
          否则**恒**由 ``data_dir`` 推导 ⇒ 「data_dir 指 A、db_path 指 B」的错配
          在结构上不再可能。
        - ⚠️ **相对路径按 CWD 解析**（``Path("./data")``）⇒ 测试进程里若未注入
          沙箱，``get_settings()`` 会指向**仓内** ``./data``（＝生产真库所在处）。
          这是 BUG-26 的根因侧 ⇒ 沙箱由 ``tests/conftest.py`` 的 autouse fixture
          注入（``NOVELOS_DATA_DIR`` ＋ ``NOVELOS_DB_PATH`` 双写 ＋ 单例重置），
          **不在本文件**做「测试专用」分支（生产代码不认测试）。
        """
        data_dir = _env("NOVELOS_DATA_DIR", "") or str(Path(".") / cls.DEFAULT_DATA_DIR_NAME)
        db_path = _env("NOVELOS_DB_PATH", "")  # 空 ⇒ 由 data_dir 推导（归一）
        log_level = _env("NOVELOS_LOG_LEVEL", "INFO")
        api_host = _env("NOVELOS_API_HOST", "127.0.0.1")
        # 端口优先级：NOVELOS_PORT > NOVELOS_API_PORT > 18081
        port_env = _env("NOVELOS_PORT", "") or _env("NOVELOS_API_PORT", "")
        api_port = int(port_env) if port_env else 18081
        # 模块开关（V3.3 轻量方案）：逗号分隔，strip 空白，丢空串
        # 例如 "simulation,reference" → ["simulation", "reference"]
        raw_disabled = _env("NOVELOS_DISABLED_MODULES", "")
        disabled_modules = [s.strip() for s in raw_disabled.split(",") if s.strip()]
        return cls(
            data_dir=data_dir,
            db_path=db_path or None,
            log_level=log_level,
            api_host=api_host,
            api_port=api_port,
            disabled_modules=disabled_modules,
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
            "disabled_modules": list(self.disabled_modules),
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
