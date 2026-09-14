"""chapter_scene_plans 读写（P1 规划合并，迁移 0027）。

落库面语义：

- **唯一写入方** = chapter-plan 的 ``save_plan`` 节点：与 ``chapters.plan_json`` 同一事务、
  同一 run 内落库（合并调用一次产出 plan + scene_plan，避免双写不一致）；
- **读取方** = chapter-write 的 ``scene_planner`` 节点：命中（有行且 scenes 非空）即跳过
  自身 LLM 调用，未命中保留既有单节点调用 + 机械映射降级路径；
- 1 章 1 面（``chapter_id`` UNIQUE）：重跑「生成计划」即覆盖；合并调用本轮**没有**
  scene_plan（计划-only 输出）时由写入方删除本行——旧 scene_plan 配新 plan_json 是
  错配数据，宁可让 chapter-write 重规划场景，也不让陈旧场景被 writer 消费。

连接纪律（AGENTS.md 坑区「事务内另开连接写库 → database is locked」）：
写函数**只接受调用方的连接**（复用外层事务，不新开连接）；读函数自建短连接（只读）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

_TABLE = "chapter_scene_plans"


def save_scene_plan(
    conn: sqlite3.Connection,
    *,
    chapter_id: str,
    project_id: str,
    scene_plan: dict[str, Any],
    run_id: str | None = None,
    source: str = "director_planner",
    prompt_version: str | None = None,
) -> str:
    """UPSERT 一章的 scene_plan 行（按 ``chapter_id``），返回 ``scene_plan_id``。

    调用方负责 ``commit()``（本函数只执行语句，复用外层事务连接）。
    """
    scenarios = scene_plan.get("scenes")
    scene_count = len(scenarios) if isinstance(scenarios, list) else 0
    payload_json = json.dumps(scene_plan, ensure_ascii=False)
    now = now_iso()
    row = conn.execute(
        f"SELECT scene_plan_id FROM {_TABLE} WHERE chapter_id = ?", (chapter_id,)
    ).fetchone()
    if row is not None:
        scene_plan_id = row["scene_plan_id"]
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET project_id = ?, run_id = ?, source = ?, prompt_version = ?,
                scene_count = ?, payload_json = ?, updated_at = ?
            WHERE scene_plan_id = ?
            """,
            (
                project_id,
                run_id,
                source,
                prompt_version,
                scene_count,
                payload_json,
                now,
                scene_plan_id,
            ),
        )
        return scene_plan_id
    scene_plan_id = new_id("csp")
    conn.execute(
        f"""
        INSERT INTO {_TABLE}
            (scene_plan_id, chapter_id, project_id, run_id, source, prompt_version,
             scene_count, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scene_plan_id,
            chapter_id,
            project_id,
            run_id,
            source,
            prompt_version,
            scene_count,
            payload_json,
            now,
            now,
        ),
    )
    return scene_plan_id


def clear_scene_plan(conn: sqlite3.Connection, chapter_id: str) -> int:
    """删除该章的 scene_plan 行，返回删除行数（0 = 本来就没有）。

    用途：重规划时本轮合并调用**没有**产出 scene_plan（计划-only 降级）——旧行必须失效，
    否则 chapter-write 会拿陈旧场景写新计划对应的正文。
    """
    cur = conn.execute(f"DELETE FROM {_TABLE} WHERE chapter_id = ?", (chapter_id,))
    return int(cur.rowcount or 0)


def load_scene_plan(db_path: str | Path, chapter_id: str) -> dict[str, Any] | None:
    """读该章落库的 scene_plan；无行 / 老库（0027 未跑）/ payload 非法 → ``None``。

    返回 ``{"scene_plan": <scene_plan 对象>, "source", "prompt_version", "run_id",
    "scene_count", "updated_at"}``。调用方（chapter-write）对 ``scene_plan.scenes``
    再做一次非空校验——旧行 / 手改数据不保证结构完整。
    """
    try:
        conn = get_connection(str(db_path))
    except Exception:  # noqa: BLE001 —— 读侧任何失败都按「未命中」降级
        return None
    try:
        row = conn.execute(
            f"""
            SELECT scene_plan_id, source, prompt_version, run_id, scene_count,
                   payload_json, updated_at
            FROM {_TABLE} WHERE chapter_id = ?
            """,
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0027 未迁移的老库 → 视为未命中（与「有行但空」同路径）
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        scene_plan = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(scene_plan, dict):
        return None
    return {
        "scene_plan": scene_plan,
        "source": row["source"],
        "prompt_version": row["prompt_version"],
        "run_id": row["run_id"],
        "scene_count": row["scene_count"],
        "updated_at": row["updated_at"],
    }


__all__ = ["clear_scene_plan", "load_scene_plan", "save_scene_plan"]
