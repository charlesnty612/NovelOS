"""NovelOS 配置层（Sprint 0 基础设施）。

使用 pydantic BaseSettings（v1 兼容写法，避免 BaseSettings v2 的 pydantic-settings 依赖）。
所有环境变量以 NOVELOS_ 前缀覆盖。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any


#: 模块级 logger。★ **不用** ``logging_config.get_logger``：那会在 import 期
#: 配置根 logger（改级别＋换 handler）⇒ 污染进程日志配置；这里只用 stdlib
#: ⇒ 未配置 logging 时 WARNING 仍由 ``logging.lastResort`` 打到 stderr（可见）。
log = logging.getLogger(__name__)


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

    ⚠️ **潜在隐患·不在本批**（2026-10-25 登记）：``__init__`` 的**构造器默认**
    在 ``data_dir=None`` 时仍是**相对** ``./data`` ⇒ 裸 ``Settings()`` 或
    ``Settings(db_path=…)`` 的数据根相对 ⇒ ＋ ``ensure_data_dir()`` 会在 CWD
    建出 ``./data``；★ **唯一现存调用点＝``tests/unit/test_config.py:15`` 的
    ``Settings()``**（生产零调用点：一律走 ``load()`` 或显式传参）
    ⇒ 留待后续批次裁（本批只封 ``load()`` 路径）。
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
    #: ★ BUG-59：无 env 时的**仓外**根目录名（用户主目录下 ⇒ 绝不与真库同相对位置）。
    DEFAULT_HOME_DIR_NAME: str = ".novelos"

    @classmethod
    def default_data_dir(cls) -> Path:
        """无 env 时的**仓外绝对**数据根（``~/.novelos/data``）。

        ⚠️ BUG-59（复查修①）：**不得**再退回 ``./data``（CWD 相对 ⇒ CWD＝仓根时
        正是生产真库所在处）⇒ 缺 env 时落到**用户主目录**下的绝对路径。
        （``~/.novelos`` 是本仓既有约定，见 ``scripts/backup_db.py:133``。）
        """
        return (
            Path.home() / cls.DEFAULT_HOME_DIR_NAME / cls.DEFAULT_DATA_DIR_NAME
        ).expanduser().resolve()

    @classmethod
    def default_db_path(cls) -> Path:
        """无 env 时的**仓外绝对**库路径（``~/.novelos/data/novelos.db``）。"""
        return cls.default_data_dir() / cls.DEFAULT_DB_NAME

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
        - ⚠️ BUG-59（复查修①②，2026-10-25）：``data_dir``／``db_path`` **一律绝对化**，
          且**缺 env 时不再退回 CWD 相对的 ``./data``**（那正是生产真库所在处）
          ⇒ 缺 env ⇒ 落**仓外**绝对默认 ``~/.novelos/data``（见 ``default_data_dir``）。
          ★ 旧修法在此处 ``raise RuntimeError`` ⇒ 抛点落在 pytest **collection 期**
          ⇒ ``tests/api/test_module_switch.py`` 整块 ERROR ⇒ 已改为**不抛异常**。
        """
        # ★ BUG-59（复查修①②）：落点解析**统一**走 ``resolve_data_paths()``
        #   （缺 env ⇒ 仓外绝对默认；两个值一律绝对化）⇒ 此处**不再抛异常**。
        data_dir, db_path = resolve_data_paths()
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
            data_dir=str(data_dir),
            db_path=str(db_path),
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


def resolve_data_paths() -> tuple[Path, Path]:
    """解析 ``(data_dir, db_path)`` ⇒ **一律绝对**；缺 env ⇒ **仓外绝对默认**。

    ⚠️ BUG-59（复查修①②）：

    - 缺 ``NOVELOS_DATA_DIR`` 且缺 ``NOVELOS_DB_PATH`` ⇒ 落**仓外**默认
      （``~/.novelos/data``）⇒ 绝不与生产真库（仓内 ``./data``）同相对位置；
      ★ **不抛异常**（旧修法在此抛 ``RuntimeError`` ⇒ 落在 pytest collection 期
      ⇒ ``tests/api/test_module_switch.py`` 整块 ERROR）；
    - 只给 ``NOVELOS_DB_PATH`` ⇒ 数据根 = 库文件所在目录（BUG-14 归一：db_path 恒由
      data_dir 推导）⇒ ``ensure_data_dir()`` 不会在 CWD 建出 ``./data``；
    - 两个值**一律绝对化**（相对形态按 CWD 解析成绝对）⇒ 落点唯一且可判定。

    ★ 本函数是**唯一真源**：``Settings.load()`` 与 ``ensure_env_defaults()`` 共用
    （两处各写一份必然分叉）。
    """
    data_dir = _env("NOVELOS_DATA_DIR", "").strip()
    db_path = _env("NOVELOS_DB_PATH", "").strip()
    if not data_dir and not db_path:
        data_dir = str(Settings.default_data_dir())
        db_path = str(Settings.default_db_path())
        # ★ 兜底落点**必须出声**（2026-10-25 口径）：缺 env ⇒ 落仓外默认，
        #   但**不许一声不响** ⇒ 运维必须知道自己在用兜底落点。
        log.warning(
            "未注入 NOVELOS_DATA_DIR / NOVELOS_DB_PATH ⇒ 已落仓外默认："
            "data_dir=%s ／ db_path=%s（如需指定请显式注入 env；"
            "此为兜底落点，绝不指向仓内 ./data）",
            data_dir,
            db_path,
        )
    elif not db_path:
        db_path = str(Path(data_dir) / Settings.DEFAULT_DB_NAME)
    elif not data_dir:
        data_dir = str(Path(db_path).parent)
    return (
        Path(data_dir).expanduser().resolve(),
        Path(db_path).expanduser().resolve(),
    )


def ensure_env_defaults() -> None:
    """把解析出的落点**显式注入**环境变量（幂等；**不覆盖**运维显式配置）。

    ⚠️ BUG-59（复查修③）：全仓 **0 处** ``load_dotenv`` ⇒ ``_env`` 只读 ``os.environ``
    ⇒ 启动路径必须**显式注入**，库落点才是确定的。三处调用：
    ``packages/core/api/main.py`` 的 ``create_app()``（含模块级 ``app = create_app()``）
    与 ``__main__``、``scripts/serve.py`` 的 ``main()``。

    ★ **有意不注入**的三处（2026-10-25 口径）：``scripts/migrate.py:43``／
    ``scripts/produce_chapters.py:1350``／``scripts/measure_cache_key_perf.py:461``
    ⇒ 它们是 CLI／诊断工具，缺 env 时落**仓外默认****正是期望行为**（不注入、
    不抛异常），并由 ``resolve_data_paths()`` 打 WARNING 告知兜底落点。
    """
    data_dir, db_path = resolve_data_paths()
    if not os.environ.get("NOVELOS_DATA_DIR"):
        os.environ["NOVELOS_DATA_DIR"] = str(data_dir)
    if not os.environ.get("NOVELOS_DB_PATH"):
        os.environ["NOVELOS_DB_PATH"] = str(db_path)


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
