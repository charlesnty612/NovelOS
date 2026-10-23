"""check_orphan_relationships —— **只读**诊断「悬空关系端点」（孤儿 relationships 行）。

背景（2026-10-23 task-7 · A4；缺陷见 ``docs/impl/reviews/full-review-round2-2026-10-23.md`` §3.9）
-------------------------------------------------------------------------------

迁移 0022（``database/migrations/0022_faction_relationship_endpoints.sql``）把
``relationships`` 的两条外键摘掉了：

* ``from_character_id`` / ``to_character_id`` 的 FK 被移除（端点可以是 faction
  ⇒ FK 语义不再成立）；
* ⇒ 删角色 / 删 faction 会留**孤儿关系行**（端点指向已不存在的实体）；
* ⇒ 而 ``PRAGMA foreign_key_check`` **报不出来**（FK 已不存在 ⇒ 检查无对象）
  ⇒ **连「发现手段」都没有**。

域层的两道守卫已补上（character 删角色 / world 删 faction 都会先查引用），
但**存量**孤儿行已经在库里 ⇒ 需要一个**能回答「有没有存量孤儿」的问题**的手段。

⚠️ **本脚本只诊断、不修复**
--------------------------------

* **不写**任何数据（不删孤儿行、不做迁移）——存量清理涉及活库业务数据，
  须由 Lead 与用户单独裁决；
* **不碰** ``data/novelos.db`` 的内容：全程 ``mode=ro`` 只读 URI 打开；
* 只回答一个问题：**哪些 relationships 行的端点已经悬空？**

「悬空」的判据
--------------

关系端点按 0022 的口径**只可能是** ``character_id`` 或 ``faction_id`` ⇒
端点 id **既不在** ``characters`` **也不在** ``factions`` ⇒ 悬空。

⇒ **能证明**：这些行的端点实体已不存在（角色或 faction 被删过，或数据被外部改坏）。
⇒ **不能证明**：悬空的原因、影响范围、是否需要清理 —— 那是裁决的事，不是诊断的事。

用法示例
--------

巡检默认库（仓库根下的 ``data/novelos.db``），输出人读表格::

    python scripts/check_orphan_relationships.py

指定库 + 机器可读 JSON::

    python scripts/check_orphan_relationships.py --db path/to/novelos.db --json

退出码语义
----------

* ``0`` —— 没有悬空端点（``OK``）；
* ``2`` —— **有**悬空端点（``ORPHANS: N 行``）——⚠️ 按 Lead 指定用 **2**
  （注意与 ``check_state_sync.py`` 的口径**不同**：那边 1=有漂移、2=错误）；
* ``1`` —— 参数错误 / DB 不存在 / 不是合法 SQLite / 表结构缺失等结构性异常。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "novelos.db"

#: 关系端点可能落在哪两张表（0022：端点可以是 character 或 faction）。
ENDPOINT_TABLES: tuple[tuple[str, str], ...] = (
    ("characters", "character_id"),
    ("factions", "faction_id"),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_orphan_relationships",
        description=(
            "只读诊断 relationships 行的悬空端点（孤儿行）。"
            "输出 Markdown 表格或 JSON，退出码 0=无孤儿, 2=有孤儿, 1=错误。"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite 数据库路径（只读 URI 模式打开）；缺省 = {DEFAULT_DB}",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="输出机器可读 JSON（默认输出 Markdown 表格）",
    )
    return parser.parse_args(argv)


def open_readonly_db(db_path: Path) -> sqlite3.Connection:
    """以只读 URI 模式打开 SQLite 数据库。文件不存在或非 SQLite 时抛 RuntimeError。"""
    if not db_path.exists():
        raise RuntimeError(f"DB 文件不存在: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise RuntimeError(f"无法以只读模式打开 DB ({db_path}): {exc}") from exc
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)
    )
    return cur.fetchone() is not None


def _endpoint_exists_sql(alias: str) -> str:
    """端点 id 是否存在于「character 或 faction」任一表（端点表名来自固定常量，不接外部输入）。"""
    parts = [
        f"EXISTS (SELECT 1 FROM {table} t WHERE t.{id_col} = r.{alias})"
        for table, id_col in ENDPOINT_TABLES
    ]
    return " OR ".join(parts)


def find_orphan_relationships(conn: sqlite3.Connection) -> list[dict]:
    """返回**悬空端点**的关系行列表（每行一个 dict）。

    判据：``from`` 或 ``to`` 端点 id **既不在 characters 也不在 factions**。
    """
    from_cond = f"NOT ({_endpoint_exists_sql('from_character_id')})"
    to_cond = f"NOT ({_endpoint_exists_sql('to_character_id')})"
    sql = f"""
        SELECT r.relationship_id, r.project_id, r.from_character_id, r.to_character_id,
               r.relation_type
        FROM relationships r
        WHERE {from_cond} OR {to_cond}
        ORDER BY r.relationship_id ASC
    """
    rows = conn.execute(sql).fetchall()
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "relationship_id": row[0],
                "project_id": row[1],
                "from_character_id": row[2],
                "to_character_id": row[3],
                "relation_type": row[4],
                "dangling_endpoints": [
                    name
                    for name, endpoint in (
                        ("from_character_id", row[2]),
                        ("to_character_id", row[3]),
                    )
                    if not _endpoint_exists(conn, endpoint)
                ],
            }
        )
    return out


def _endpoint_exists(conn: sqlite3.Connection, endpoint_id: str) -> bool:
    """端点 id 是否存在于「character 或 faction」任一表。"""
    for table, id_col in ENDPOINT_TABLES:
        row = conn.execute(
            f"SELECT 1 FROM {table} t WHERE t.{id_col} = ? LIMIT 1", (endpoint_id,)
        ).fetchone()
        if row is not None:
            return True
    return False


def count_relationships(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()
    return int(row[0]) if row else 0


def render_markdown(orphans: list[dict], total: int) -> str:
    if not orphans:
        return f"OK：relationships 共 {total} 行，**无悬空端点**。\n"
    lines = [
        f"ORPHANS: {len(orphans)} 行悬空端点（relationships 共 {total} 行）。",
        "",
        "| relationship_id | project_id | from | to | 悬空端点 |",
        "|---|---|---|---|---|",
    ]
    for o in orphans:
        lines.append(
            "| {rid} | {pid} | {frm} | {to} | {dang} |".format(
                rid=o["relationship_id"],
                pid=o["project_id"],
                frm=o["from_character_id"],
                to=o["to_character_id"],
                dang=", ".join(o["dangling_endpoints"]),
            )
        )
    lines.append("")
    lines.append(
        "⚠️ 本脚本**只诊断不修复**：清理存量孤儿涉及活库业务数据，须由 Lead 与用户裁决。"
    )
    return "\n".join(lines)


def render_json(orphans: list[dict], total: int) -> str:
    return json.dumps(
        {
            "total_relationships": total,
            "orphan_count": len(orphans),
            "orphans": orphans,
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        conn = open_readonly_db(args.db)
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    try:
        for table in ("relationships", *(t for t, _ in ENDPOINT_TABLES)):
            if not _table_exists(conn, table):
                sys.stderr.write(
                    f"ERROR: DB 缺少表 {table}（未跑迁移？）: {args.db}\n"
                )
                return 1
        total = count_relationships(conn)
        orphans = find_orphan_relationships(conn)
    except Exception as exc:  # noqa: BLE001 - 兜底：stderr 一行 + 退出码 1
        sys.stderr.write(f"ERROR: 未预期异常: {exc}\n")
        return 1
    finally:
        conn.close()

    if args.as_json:
        print(render_json(orphans, total))
    else:
        print(render_markdown(orphans, total))

    return 0 if not orphans else 2


if __name__ == "__main__":
    sys.exit(main())
