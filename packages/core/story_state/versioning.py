"""``story_states.state_version`` 推进（**core 层唯一实现**，2026-10-24）。

职责
----
在**当前写事务内**推进某项目的 ``story_states.state_version``（+1），并把
**内容不变**的新版本快照落库（``materialize_snapshot``）——这是「装配缓存失效主线」
（AGENTS.md 硬规则 2「改权威输入必须 miss 缓存」的**根治条件②**：写路径推
``state_version``）的**唯一**实现点。

为什么搬到这里
--------------
本函数的实现原先落在 ``packages/domain/project/service.py``（域层），
于是 **core 层**（``packages/core/story_state/branches.py``、
``packages/core/simulation/service.py``）**无法调用它**——core 不得 import domain
（分层纪律：依赖方向 core ← domain ← workflows）。而这两处写路径
（建分支 / 分支合并 / 临时分支归档）改的 ``branches`` 表**进 director 装配 payload**
（``plot_graph_excerpt`` 段），**必须**推 ``state_version``，否则同 ``state_version``
下重跑同一章会命中旧装配。

⇒ 本模块是 core 层的**归属地**；域层 ``domain/project/service.py`` 的同名函数
保留为**转发**（公开 API 与既有 import / 突变规格锚点零变化），保证
**「唯一实现」**不因搬家而变成两份（两份实现 = 迟早口径漂移）。

边界（如实登记，与域层旧实现逐字一致）
----------------------------------------
- 项目**还没有 story_states 行**（从未 commit 过）⇒ **不造快照行**，返回 ``None``
  （该路径下 ``state_version`` 保持不变）。
- 快照 JSON 损坏 ⇒ **不造行**（绝不拿空 dict 覆盖 Canonical State）。
- 必须在**写事务内**调用（与被改的行同一连接 / 同一事务）⇒ 写失败则版本一起回滚。

用法
----
::

    from packages.core.story_state.versioning import bump_project_state_version

    conn.execute("UPDATE branches SET status = 'MERGED' WHERE branch_id = ?", (bid,))
    bump_project_state_version(conn, project_id)   # 同一连接、同一事务
    conn.commit()
"""

from __future__ import annotations

import json
import sqlite3

from packages.core.db import is_missing_schema_error
from packages.core.ids import now_iso

from .snapshot import materialize_snapshot


def bump_project_state_version(conn: sqlite3.Connection, project_id: str) -> int | None:
    """在**当前事务内**推进该项目的 ``story_states.state_version``。

    返回新的 ``state_version``；**不推进**时返回 ``None``（项目尚无快照行 /
    快照 JSON 损坏 ⇒ 不造快照行）。

    ⚠️ 调用方必须在**写事务内**调用（本函数不 commit、不开连接）⇒ 业务写失败时
    版本推进一起回滚 ⇒ 不会出现「数据没改但版本涨了」。

    ⚠️ **调用方必须先做完自己的写**（如 ``UPDATE branches ...``）再调本函数：
    本函数读的是**当前**最新快照行（``ORDER BY state_version DESC LIMIT 1``），
    读-改-写之间的竞态由调用方的写事务（WAL 单写者）保证。
    """
    if not project_id:
        return None
    try:
        row = conn.execute(
            "SELECT state_version, snapshot_json, commit_id FROM story_states "
            "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # 极老库没有 story_states 表 ⇒ 本路径下 version 不变（不是静默失真：
        # 没有快照行时 MAX(state_version) 本来就是 0）。
        if not is_missing_schema_error(exc):
            raise
        return None
    if row is None:
        # 项目还没有快照 ⇒ **不造快照行**。
        return None
    try:
        snapshot = json.loads(row["snapshot_json"])
    except (TypeError, ValueError):
        # 快照 JSON 损坏 ⇒ 不造行（拿空 dict 覆盖 Canonical State 是更坏的事）。
        return None
    if not isinstance(snapshot, dict):
        return None
    new_version = int(row["state_version"]) + 1
    materialize_snapshot(
        conn,
        project_id=project_id,
        state_version=new_version,
        snapshot_json=snapshot,
        commit_id=row["commit_id"],
        created_at=now_iso(),
    )
    return new_version


__all__ = ["bump_project_state_version"]
