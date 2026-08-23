"""FastAPI 路由自动发现机制（Sprint 1）。

职责：
- ``discover_routers() -> list[APIRouter]``：遍历本包（``packages.core.api.routers``）
  下的所有模块，对每个模块：
  1. ``importlib.import_module`` 触发模块顶层副作用（router 变量被绑定）。
  2. 取模块的 ``router`` 属性；不存在则跳过。

设计要点：
- 使用 ``pkgutil.iter_modules`` 而非 ``glob``，保证 Python 包的发现语义与 import 系统一致。
- 跳过本包自身（``__init__.py``）与私有模块（名称以下划线开头）。
- 失败模块（import 抛异常）会被记录到日志并跳过，避免单个坏模块拖垮整应用启动；
  这与 FastAPI 的「路由挂载容错」习惯一致，便于 Sprint 1 多方并行交付时其中一个出错
  不影响 health 端点可用。
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Iterable

from fastapi import APIRouter

from packages.core.logging_config import get_logger

log = get_logger("novelos.routers")

_PACKAGE_NAME = __name__  # "packages.core.api.routers"


def _iter_modules() -> Iterable[ModuleType]:
    """遍历本包子模块（不含自身）。"""
    package = importlib.import_module(_PACKAGE_NAME)
    for mod_info in pkgutil.iter_modules(package.__path__, prefix=f"{_PACKAGE_NAME}."):
        # mod_info.name 形如 "packages.core.api.routers.projects"
        short = mod_info.name.rsplit(".", 1)[-1]
        if short.startswith("_"):
            # 跳过 __init__、_helpers 等私有模块
            continue
        try:
            yield importlib.import_module(mod_info.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to import router module %s: %s", mod_info.name, exc)


def discover_routers() -> list[APIRouter]:
    """自动发现本包内所有 ``router`` 公开属性，返回 ``APIRouter`` 列表。

    跳过：模块不含名为 ``router`` 的属性，或 ``router`` 不是 ``APIRouter`` 实例。
    失败：模块 import 失败会被记录 warning 并跳过（不影响其他模块）。
    """
    routers: list[APIRouter] = []
    for module in _iter_modules():
        candidate = getattr(module, "router", None)
        if isinstance(candidate, APIRouter):
            routers.append(candidate)
        else:
            log.debug("module %s has no `router` APIRouter, skipped", module.__name__)
    log.info("discovered %d router(s) from %s", len(routers), _PACKAGE_NAME)
    return routers


__all__ = ["discover_routers"]
