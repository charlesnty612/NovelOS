"""快通道开关的单点解析（2026-10-14，配套迁移 0031 ``projects.fast_mode``）。

**快通道是什么**：写完一章直接提交——跳过 ``chapter-review`` 整条链、``quality_gate``
阻断与 ``high_risk_approval`` 人工挂起。单章从「4 个 run + 2 次人工往返 + 最多 2 轮
自动改稿」压到「2 个 run + 0 次人工往返」。

**快通道不是什么**（边界，勿越界扩大）：

- **不砍状态更新**。``observer`` 节点照跑——它在 ``chapter-commit`` 里而不在 review 里，
  是唯一抽出角色目标推进 / 伏笔收放 / 世界观 delta 并写 ``story_state`` 的节点。
  砍掉它，第 2 章起就失忆（伏笔不闭环、角色目标不动）。
- **不关判定**。``quality_gate`` 仍算、报告仍落库，只是 ``should_block`` 恒假
  （走既有的 ``quality_gate_mode="report"`` 语义）。
- **不删闸门代码**。本开关只改「是否阻断 / 是否挂起」，回退成本为零（关掉即原样）。

优先级（与 ``_resolve_auto_revise_max`` / ``_quality_gate_mode`` 同款范式）：

1. 请求体 ``StartWorkflowRequest.fast_mode``（显式 ``false`` = 本次运行临时关闭，
   是项目级开启后的**逃生舱**）；
2. ``projects.fast_mode`` 列（可空三态：NULL 未设置 ≠ 0 显式关）；
3. env ``NOVELOS_FAST_MODE``；
4. 默认关。
"""

from __future__ import annotations

import os
import sqlite3

from packages.core.db import get_connection

#: env 真值词表——与 ``NOVELOS_AUTO_REVISE_MAX`` 之外的布尔 env 同款口径。
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})

#: env 变量名（全局兜底，主要给脚本 / 单用户本机场景用）。
ENV_FAST_MODE = "NOVELOS_FAST_MODE"


def _env_fast_mode() -> bool | None:
    """读 env；未设置或非法值返回 None（不猜）。"""
    raw = os.environ.get(ENV_FAST_MODE)
    if raw is None:
        return None
    token = raw.strip().lower()
    if token in _TRUTHY:
        return True
    if token in _FALSY:
        return False
    return None


def read_project_fast_mode(db_path: str, project_id: str) -> bool | None:
    """读 ``projects.fast_mode`` 列；三态返回 ``True / False / None``。

    - 项目不存在 / 极老库缺该列 → None（回落 env 与默认值，不炸）。
    - 走**直查**而非 ``ProjectService.get``：本模块在 API 层、且每次启动 run 都要用，
      直查避开 domain 依赖并与 ``_guard_commit_draft_freshness`` 的读法同款（都在本层）。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT fast_mode FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # 只吞「列不存在」（极老库未应用 0031）→ 视同「未设置」，回落 env 与默认值。
        # 其余 OperationalError（database is locked / disk I/O / no such table）
        # 必须上抛（2026-10-15 收窄）：静默回落成「快通道关」会让作者以为自己
        # 开着、却莫名又被人工闸门拦下。注释原本只声称覆盖「列不存在」，
        # 捕获范围与注释不符——本项目反复登记的「注释主张必须可复算」那条坑。
        # ⚠️ 同款过宽写法在 `builders_common/common.py:662`（题材包绑定）仍在，
        # 本批只收窄本文件；其余挂账待独立处理。
        if "no such column" not in str(exc):
            raise
        return None
    finally:
        conn.close()
    if row is None:
        return None
    raw = row["fast_mode"]
    if raw is None:
        return None
    return bool(raw)


def resolve_fast_mode(
    *,
    db_path: str,
    project_id: str,
    request_value: bool | None,
) -> bool:
    """解析本次运行是否走快通道。**唯一权威口径**，两个消费方都调它
    （``control.py`` 的 ctx 注入处 + auto-commit 前的二次确认；
    数量写死在这里——新增消费方时同步改这一行，别让它再次变成不可核的数字）。

    参数 ``request_value`` 取 ``StartWorkflowRequest.fast_mode``；
    ``None`` 表示「请求体没说」，不是「显式关」——显式关传 ``False``。
    """
    if request_value is not None:
        return bool(request_value)
    project_value = read_project_fast_mode(db_path, project_id)
    if project_value is not None:
        return project_value
    env_value = _env_fast_mode()
    if env_value is not None:
        return env_value
    return False


__all__ = [
    "ENV_FAST_MODE",
    "read_project_fast_mode",
    "resolve_fast_mode",
]
