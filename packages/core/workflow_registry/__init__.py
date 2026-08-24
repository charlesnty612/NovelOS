"""Workflow Registry（Sprint V1.5 架构债务项）。

职责与边界：

- :func:`register_workflow` —— 注册一条 workflow（按 ``name``）。
- :func:`get_workflow` —— 按 ``name`` 取 workflow dict（含 ``nodes`` / ``name`` / ``version``）。
- :func:`all_workflows` —— 取注册中心全部 workflow（快照）。
- :func:`_reset_for_tests` —— 测试辅助：清空注册中心。

设计要点：

- 注册表位于 core 侧，core 上层模块通过本包查询；
  业务流程包在自己 ``__init__`` 调 :func:`register_workflow` 写入。
- 注册参数 ``builder: Callable[[], dict]`` 是惰性引用：调用 :func:`get_workflow` 时
  才构建 workflow dict，避免 import 阶段触发业务 pipeline 模块顶层副作用导致
  循环引用 / 重活。
- :data:`_REGISTRY` 存 ``{name: builder}``；构建结果缓存到 :data:`_CACHE`，
  同一 name 多次查询只构建一次。
- 本包不依赖业务流程包；core → 业务流程包的反向依赖由本注册表切断。

依赖方向：

- 业务流程包 → core（写注册表）。
- core → 业务流程包（仅限装配入口 ``packages/core/api/main.py`` 通过
  ``importlib.import_module`` 触发一次导入，视为插件发现；
  core 业务模块零依赖业务流程包）。
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["register_workflow", "get_workflow", "all_workflows", "_reset_for_tests"]

_REGISTRY: dict[str, Callable[[], dict[str, Any]]] = {}
_CACHE: dict[str, dict[str, Any]] = {}


def register_workflow(name: str, builder: Callable[[], dict[str, Any]]) -> None:
    """注册一条 workflow。

    - ``name`` —— 注册名（必须非空）；同名重复注册后写覆盖前写。
    - ``builder`` —— 惰性构造 callable，调用返回 ``dict``（含 ``name`` / ``nodes`` /
      ``version`` 等键）。**不要**在调用处立刻求值 dict，由 :func:`get_workflow`
      第一次访问时构建，规避 import 阶段重活与循环引用。

    注册动作本身不触发 import / 执行副作用；只把 builder 放进内部 dict。
    """
    if not name or not isinstance(name, str):
        raise ValueError("workflow name must be a non-empty string")
    if not callable(builder):
        raise ValueError("workflow builder must be callable")
    _REGISTRY[name] = builder
    # 失效缓存：同 name 重注册时丢弃旧 dict
    _CACHE.pop(name, None)


def _build(name: str, builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    wf = builder()
    if not isinstance(wf, dict):
        raise TypeError(f"workflow builder for {name!r} must return a dict, got {type(wf).__name__}")
    # 校验最小契约：name 必填；其余字段（nodes / version）由调用方保证。
    if "name" not in wf:
        wf = {**wf, "name": name}
    return wf


def get_workflow(name: str) -> dict[str, Any] | None:
    """按 ``name`` 取 workflow dict；未注册返回 ``None``。

    第一次访问时调用 builder 构建并缓存 dict；后续直接返回缓存。
    """
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    builder = _REGISTRY.get(name)
    if builder is None:
        return None
    wf = _build(name, builder)
    _CACHE[name] = wf
    return wf


def all_workflows() -> dict[str, dict[str, Any]]:
    """返回 ``{name: WORKFLOW}`` 快照；不暴露内部 builder。

    已构建的走缓存；未构建的当场构建一次。
    """
    out: dict[str, dict[str, Any]] = {}
    for name, builder in _REGISTRY.items():
        cached = _CACHE.get(name)
        if cached is None:
            cached = _build(name, builder)
            _CACHE[name] = cached
        out[name] = cached
    return out


def _reset_for_tests() -> None:
    """测试辅助：清空注册中心与缓存。

    业务流程包一般不在 import 时立即注册（仅放置 builder），因此重置不会丢失信息；
    测试如需重新触发注册，调用方需自行重新 import 业务流程包。
    """
    _REGISTRY.clear()
    _CACHE.clear()
