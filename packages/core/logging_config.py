"""NovelOS 日志配置（Sprint 0）。

仅 stdlib logging，控制台输出；格式：时间 | 级别 | 模块 | 消息。
提供 ``configure_logging(level)``，幂等可重复调用。
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """配置根 logger；重复调用安全（移除旧 handler 后重设）。"""
    global _CONFIGURED
    root = logging.getLogger()
    # 清掉已有 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)
    try:
        lvl = getattr(logging, level.upper())
    except AttributeError:
        lvl = logging.INFO
    root.setLevel(lvl)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """便捷取 logger；首次调用自动 configure。"""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
