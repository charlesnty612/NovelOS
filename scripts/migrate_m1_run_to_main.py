"""一次性数据迁移：把 m1_run 库中的日常数据搬回默认库（2026-08-28）。

背景：服务进程曾按 scripts/m1_long_run.py 的方式以 ``NOVELOS_DATA_DIR=data/m1_run``
启动，导致日常数据（旧项目A项目、模型配置）落在长跑脚本的工作库。本脚本把：
- ``projects`` 中指定项目及其所有含 ``project_id`` 列的子表行；
- ``model_profiles`` 中来自旧 ``model_configs`` 迁移的三个 MiniMax-M3 档案；
搬回默认库 ``data/novelos.db``。m1 压测的测试项目（m1_*）不搬。

用法：先停服务，备份两库后执行 ``python scripts/migrate_m1_run_to_main.py``。
"""

from __future__ import annotations

import sqlite3
import sys

MAIN_DB = "data/novelos.db"
M1_DB = "data/m1_run/novelos.db"
PROJECT_ID = "prj_8365f42af5a6"  # 旧项目A：从旧项目A时代开始


def _tables_with_project_id(conn: sqlite3.Connection) -> list[str]:
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("_") and r[0] != "sqlite_sequence"
    ]
    out = []
    for t in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
        if "project_id" in cols:
            out.append(t)
    return out


def main() -> int:
    conn = sqlite3.connect(MAIN_DB)
    conn.execute(f"ATTACH DATABASE '{M1_DB}' AS m1")

    # 1) 项目主行
    cur = conn.execute(
        "INSERT OR REPLACE INTO projects SELECT * FROM m1.projects WHERE project_id=?",
        (PROJECT_ID,),
    )
    print(f"projects: {cur.rowcount} 行")

    # 2) 所有含 project_id 的业务子表
    for t in _tables_with_project_id(conn):
        if t == "projects":
            continue
        try:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {t} SELECT * FROM m1.{t} WHERE project_id=?",
                (PROJECT_ID,),
            )
            if cur.rowcount:
                print(f"{t}: {cur.rowcount} 行")
        except sqlite3.Error as exc:
            print(f"{t}: 跳过（{exc}）")

    # 3) 模型档案（m1_run 库 0016 迁移产物：三个 MiniMax-M3）
    cur = conn.execute(
        "INSERT OR IGNORE INTO model_profiles SELECT * FROM m1.model_profiles "
        "WHERE model='MiniMax-M3'"
    )
    print(f"model_profiles: {cur.rowcount} 行")

    conn.commit()

    # 4) 校验
    print("--- 校验 ---")
    print("projects:", conn.execute("SELECT name FROM projects WHERE project_id=?", (PROJECT_ID,)).fetchall())
    print("profiles:", conn.execute("SELECT name, model FROM model_profiles ORDER BY created_at").fetchall())
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
