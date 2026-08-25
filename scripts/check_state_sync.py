"""check_state_sync —— DB 实体表 vs Story State 快照 JSON 的实体集合漂移巡检。

只读巡检 NovelOS 工程的 SQLite 库，对比 7 个核心实体集合在 DB 实体表与
``story_states.snapshot_json`` 最新版本中的差异，输出 Markdown 或 JSON 报告。

支持的实体集合（每个项目独立计算双向集合差）：
characters / locations / factions / world_rules / plot_events / hooks / narrative_debts

用法示例
--------

默认巡检库中全部项目，输出 Markdown 到 stdout，退出码反映漂移情况::

    python scripts/check_state_sync.py --db data/m1_run/novelos.db

只巡检指定项目::

    python scripts/check_state_sync.py --db data/m1_run/novelos.db \\
        --project prj_262251b433a9

机器可读 JSON 输出（便于接 CI / smoke）::

    python scripts/check_state_sync.py --db data/m1_run/novelos.db --json

退出码语义
----------

* ``0`` —— 全部项目 / 集合双向一致，``SYNC OK``；
* ``1`` —— 至少一处漂移，``DRIFT: N 处``；亦覆盖"快照 JSON 解析失败"等结构性异常；
* ``2`` —— 参数错误 / DB 文件不存在 / DB 不是合法 SQLite / 脚本内部未预期异常。

设计要点
--------

* 全程只读：通过 ``sqlite3.connect('file:<db>?mode=ro', uri=True)`` 打开数据库，
  不修改任何文件、不写入新表、不创建 sidecar 日志；
* 对每个项目取 ``story_states`` 中 ``MAX(state_version)`` 的最新快照（即 ``--latest`` 行为）；
* 对快照中结构不符的字段（缺失 / 类型不对）容错降级，跳过该集合并向 stderr 打印提示，
  不抛异常；
* DB 与快照两侧的 id 一律规范化为 ``set[str]`` 再做差集。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------
# 实体集合配置：DB 表 / 主键列名 / 快照取值路径
# ----------------------------------------------------------------------------

# 单个集合的配置。
#   db_table:        DB 实体表名
#   db_id_column:    该表的主键 id 列名
#   snapshot_kind:   快照侧取值类型 —— "list_of_dicts" / "dict_keys" / "list_field"
#   snapshot_path:   在 snapshot_json 中的路径元组（world.factions 走 ["world","factions"]）
#   id_field:        list_of_dicts 模式下从每项取的 id 字段名
#   table_required:  DB 中表不存在时是否记 stderr + 跳过（True）或 raise（False）

# 9 个键固定给出，便于顺序输出与表格对齐。
COLLECTIONS: list[dict] = [
    {
        "name": "characters",
        "db_table": "characters",
        "db_id_column": "character_id",
        "snapshot_kind": "list_of_dicts",
        "snapshot_path": ("characters",),
        "id_field": "character_id",
    },
    {
        "name": "locations",
        "db_table": "locations",
        "db_id_column": "location_id",
        "snapshot_kind": "dict_keys",
        "snapshot_path": ("world", "locations"),
        "id_field": None,
    },
    {
        "name": "factions",
        "db_table": "factions",
        "db_id_column": "faction_id",
        "snapshot_kind": "dict_keys",
        "snapshot_path": ("world", "factions"),
        "id_field": None,
    },
    {
        "name": "world_rules",
        "db_table": "world_rules",
        "db_id_column": "world_rule_id",
        "snapshot_kind": "list_field",
        "snapshot_path": ("world", "world_rules"),
        "id_field": "world_rule_id",  # 与 snapshot.py 写入口径一致（曾误配 rule_id 导致假 DRIFT）
    },
    {
        "name": "plot_events",
        "db_table": "plot_events",
        "db_id_column": "event_id",
        "snapshot_kind": "dict_keys",
        "snapshot_path": ("events",),
        "id_field": None,
    },
    {
        "name": "hooks",
        "db_table": "hooks",
        "db_id_column": "hook_id",
        "snapshot_kind": "list_of_dicts",
        "snapshot_path": ("hooks",),
        "id_field": "hook_id",
    },
    {
        "name": "narrative_debts",
        "db_table": "narrative_debts",
        "db_id_column": "debt_id",
        "snapshot_kind": "list_of_dicts",
        "snapshot_path": ("debts",),
        "id_field": "debt_id",
    },
]

MAX_DRIFT_IDS_PRINTED = 10


@dataclass
class CollectionResult:
    """单个项目下某个集合的巡检结果。"""

    project_id: str
    name: str
    db_count: int
    snapshot_count: int
    only_in_db: list[str] = field(default_factory=list)
    only_in_snapshot: list[str] = field(default_factory=list)
    skipped: str | None = None  # 非空 = 跳过原因（如 "快照结构不符"、"DB表不存在"）


@dataclass
class ProjectReport:
    """单个项目的巡检报告。"""

    project_id: str
    state_version: int | None
    results: list[CollectionResult] = field(default_factory=list)
    parse_error: str | None = None  # 非空 = 快照 JSON 解析失败


# ----------------------------------------------------------------------------
# DB / 快照读取
# ----------------------------------------------------------------------------


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


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    )
    return cur.fetchone() is not None


def fetch_db_ids(conn: sqlite3.Connection, table: str, id_column: str, project_id: str) -> set[str]:
    """从 DB 实体表中 SELECT id WHERE project_id=?，返回 set[str]。"""
    # id_column / table 来自固定常量（COLLECTIONS），不接外部输入，故直接拼接安全。
    sql = f"SELECT {id_column} FROM {table} WHERE project_id = ?"
    cur = conn.execute(sql, (project_id,))
    return {str(row[0]) for row in cur.fetchall() if row[0] is not None}


def list_projects(conn: sqlite3.Connection) -> list[str]:
    """枚举 projects.project_id；若 projects 表不存在则返回空。"""
    if not table_exists(conn, "projects"):
        return []
    cur = conn.execute("SELECT project_id FROM projects ORDER BY created_at")
    return [str(r[0]) for r in cur.fetchall()]


def fetch_latest_snapshot(
    conn: sqlite3.Connection, project_id: str
) -> tuple[int | None, dict | None, str | None]:
    """取指定 project 的最新 story_states 快照。

    Returns
    -------
    (state_version, snapshot_dict_or_None, error_or_None)
    """
    cur = conn.execute(
        """
        SELECT state_version, snapshot_json
        FROM story_states
        WHERE project_id = ?
        ORDER BY state_version DESC
        LIMIT 1
        """,
        (project_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None, None, None
    version = int(row[0])
    raw = row[1]
    try:
        snap = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return version, None, f"快照 JSON 解析失败: {exc}"
    if not isinstance(snap, dict):
        return version, None, "快照 JSON 顶层不是 dict"
    return version, snap, None


# ----------------------------------------------------------------------------
# 快照侧 id 集合抽取（带容错）
# ----------------------------------------------------------------------------


def _walk(snap: dict, path: tuple[str, ...]) -> object:
    """按 path 元组逐层下钻；任一层缺失或类型不符返回哨兵字符串。"""
    cur: object = snap
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)  # type: ignore[assignment]
    return cur


def extract_snapshot_ids(cfg: dict, snap: dict) -> tuple[set[str] | None, str | None]:
    """按 cfg 从快照抽取 id 集合。返回 (ids_or_None, skip_reason_or_None)。"""
    kind = cfg["snapshot_kind"]
    path = cfg["snapshot_path"]
    node = _walk(snap, path)
    if node is None:
        return None, f"集合 {cfg['name']} 在快照中结构不符，已跳过"

    if kind == "dict_keys":
        if not isinstance(node, dict):
            return None, f"集合 {cfg['name']} 在快照中结构不符，已跳过"
        return {str(k) for k in node.keys()}, None

    if kind in ("list_of_dicts", "list_field"):
        if not isinstance(node, list):
            return None, f"集合 {cfg['name']} 在快照中结构不符，已跳过"
        if not node:
            return set(), None
        if kind == "list_of_dicts":
            ids: set[str] = set()
            for item in node:
                if not isinstance(item, dict):
                    return None, f"集合 {cfg['name']} 在快照中结构不符，已跳过"
                v = item.get(cfg["id_field"])
                if v is None:
                    return None, f"集合 {cfg['name']} 在快照中结构不符，已跳过"
                ids.add(str(v))
            return ids, None
        # list_field: 元素可能是 dict 或字符串（仅取 id_field 字段；非 dict / 缺字段则跳过该集合）
        ids = set()
        for item in node:
            if isinstance(item, dict):
                v = item.get(cfg["id_field"])
                if v is not None:
                    ids.add(str(v))
        # list_field 模式下若无任何 dict 带 id_field，整集合视为空而非报错（容错）
        return ids, None

    return None, f"集合 {cfg['name']} 抽取方式未知，已跳过"


# ----------------------------------------------------------------------------
# 巡检主流程
# ----------------------------------------------------------------------------


def inspect_project(conn: sqlite3.Connection, project_id: str) -> ProjectReport:
    """对单个 project 巡检所有 7 个集合。"""
    report = ProjectReport(project_id=project_id, state_version=None)
    version, snap, err = fetch_latest_snapshot(conn, project_id)
    report.state_version = version
    if err is not None:
        report.parse_error = err
        # 快照不可解析：所有集合以"解析失败"占位，视为整体 1 处漂移
        for cfg in COLLECTIONS:
            report.results.append(
                CollectionResult(
                    project_id=project_id,
                    name=cfg["name"],
                    db_count=0,
                    snapshot_count=0,
                    skipped=err,
                )
            )
        return report
    if snap is None:
        # 无快照：所有集合视为 DB 多 / 快照 0
        for cfg in COLLECTIONS:
            try:
                db_ids = (
                    fetch_db_ids(conn, cfg["db_table"], cfg["db_id_column"], project_id)
                    if table_exists(conn, cfg["db_table"])
                    else set()
                )
            except sqlite3.Error as exc:
                db_ids = set()
                sys.stderr.write(
                    f"check_state_sync: 跳过集合 {cfg['name']} (DB 读取失败: {exc})\n"
                )
            report.results.append(
                CollectionResult(
                    project_id=project_id,
                    name=cfg["name"],
                    db_count=len(db_ids),
                    snapshot_count=0,
                    only_in_db=sorted(db_ids),
                    skipped="项目无 story_states 快照",
                )
            )
        return report

    for cfg in COLLECTIONS:
        # DB 侧
        if table_exists(conn, cfg["db_table"]):
            try:
                db_ids = fetch_db_ids(conn, cfg["db_table"], cfg["db_id_column"], project_id)
            except sqlite3.Error as exc:
                sys.stderr.write(
                    f"check_state_sync: 跳过集合 {cfg['name']} (DB 读取失败: {exc})\n"
                )
                report.results.append(
                    CollectionResult(
                        project_id=project_id,
                        name=cfg["name"],
                        db_count=0,
                        snapshot_count=0,
                        skipped=f"DB 读取失败: {exc}",
                    )
                )
                continue
        else:
            sys.stderr.write(
                f"check_state_sync: 跳过集合 {cfg['name']} (DB 表 {cfg['db_table']} 不存在)\n"
            )
            db_ids = set()

        # 快照侧
        snap_ids, skip_reason = extract_snapshot_ids(cfg, snap)
        if snap_ids is None:
            sys.stderr.write(f"check_state_sync: {skip_reason}\n")
            report.results.append(
                CollectionResult(
                    project_id=project_id,
                    name=cfg["name"],
                    db_count=len(db_ids),
                    snapshot_count=0,
                    only_in_db=sorted(db_ids),
                    skipped=skip_reason,
                )
            )
            continue

        only_in_db = sorted(db_ids - snap_ids)
        only_in_snapshot = sorted(snap_ids - db_ids)
        report.results.append(
            CollectionResult(
                project_id=project_id,
                name=cfg["name"],
                db_count=len(db_ids),
                snapshot_count=len(snap_ids),
                only_in_db=only_in_db,
                only_in_snapshot=only_in_snapshot,
            )
        )

    return report


def collect_reports(conn: sqlite3.Connection, project_filter: str | None) -> list[ProjectReport]:
    """根据 --project 决定要巡检的项目集合并执行。"""
    if project_filter is not None:
        return [inspect_project(conn, project_filter)]
    project_ids = list_projects(conn)
    if not project_ids:
        sys.stderr.write("check_state_sync: projects 表为空，无可巡检项目\n")
    return [inspect_project(conn, pid) for pid in project_ids]


# ----------------------------------------------------------------------------
# 报告渲染（Markdown / JSON）
# ----------------------------------------------------------------------------


def _format_id_list(ids: list[str], limit: int = MAX_DRIFT_IDS_PRINTED) -> str:
    if not ids:
        return "-"
    if len(ids) <= limit:
        return ", ".join(ids)
    head = ", ".join(ids[:limit])
    return f"{head} ...(共 {len(ids)} 个)"


def render_markdown(reports: list[ProjectReport]) -> str:
    lines: list[str] = []
    lines.append("# 状态同步巡检报告")
    lines.append("")

    # 总览表
    lines.append("## 总览")
    lines.append("")
    lines.append("| 项目 | 集合 | DB数 | 快照数 | 仅在DB | 仅在快照 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for rep in reports:
        for r in rep.results:
            lines.append(
                f"| {rep.project_id} | {r.name} | {r.db_count} | {r.snapshot_count} "
                f"| {len(r.only_in_db)} | {len(r.only_in_snapshot)} |"
            )
    lines.append("")

    # 漂移明细
    drifts = [r for rep in reports for r in rep.results if r.only_in_db or r.only_in_snapshot]
    lines.append("## 漂移明细")
    lines.append("")
    if not drifts:
        lines.append("（无）")
    else:
        for r in drifts:
            lines.append(f"### {r.project_id} / {r.name}")
            lines.append(
                f"- 仅在DB({len(r.only_in_db)}): {_format_id_list(r.only_in_db)}"
            )
            lines.append(
                f"- 仅在快照({len(r.only_in_snapshot)}): "
                f"{_format_id_list(r.only_in_snapshot)}"
            )
            lines.append("")

    # 结论
    drift_count = _count_drifts(reports)
    if drift_count == 0:
        conclusion = "SYNC OK"
    else:
        conclusion = f"DRIFT: {drift_count} 处"
    lines.append("## 结论")
    lines.append("")
    lines.append(conclusion)
    lines.append("")
    return "\n".join(lines)


def render_json(reports: list[ProjectReport]) -> str:
    summary: list[dict] = []
    drifts: list[dict] = []
    for rep in reports:
        for r in rep.results:
            summary.append(
                {
                    "project_id": rep.project_id,
                    "collection": r.name,
                    "db_count": r.db_count,
                    "snapshot_count": r.snapshot_count,
                    "only_in_db": r.only_in_db,
                    "only_in_snapshot": r.only_in_snapshot,
                    "skipped": r.skipped,
                }
            )
            if r.only_in_db or r.only_in_snapshot:
                drifts.append(
                    {
                        "project_id": rep.project_id,
                        "collection": r.name,
                        "only_in_db": r.only_in_db,
                        "only_in_snapshot": r.only_in_snapshot,
                    }
                )
    drift_count = _count_drifts(reports)
    conclusion = "SYNC OK" if drift_count == 0 else f"DRIFT: {drift_count} 处"
    payload = {
        "summary": summary,
        "drifts": drifts,
        "conclusion": conclusion,
        "exit_code": 0 if drift_count == 0 else 1,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _count_drifts(reports: list[ProjectReport]) -> int:
    """统计漂移总数：仅算存在非空 only_in_db 或 only_in_snapshot 的集合。
    快照解析失败的项目算 1 处（已在 inspect_project 中用占位记录表达，
    仅当 results 全部为 skipped 且无 only_in_db 时不计入；以 only_in_db/snapshot 非空为准）。"""
    total = 0
    for rep in reports:
        if rep.parse_error is not None:
            # 解析失败视为整项目 1 处漂移
            total += 1
            continue
        for r in rep.results:
            if r.only_in_db or r.only_in_snapshot:
                total += 1
    return total


# ----------------------------------------------------------------------------
# CLI 入口
# ----------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_state_sync",
        description=(
            "只读巡检 DB 实体表 vs story_states 快照 JSON 的实体集合漂移。"
            "输出 Markdown 或 JSON，退出码 0=无漂移, 1=有漂移, 2=错误。"
        ),
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="SQLite 数据库路径（只读 URI 模式打开）",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="仅巡检指定 project_id；缺省 = DB 中全部 project",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        default=True,
        help="对每个 project 取 MAX(state_version) 最新快照（默认行为，可省略）",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="输出机器可读 JSON（默认输出 Markdown）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    mode_label = "json" if args.as_json else "md"
    project_label = args.project if args.project else "ALL"
    sys.stderr.write(
        f"check_state_sync: db={args.db} project={project_label} "
        f"latest={args.latest} mode={mode_label}\n"
    )

    try:
        conn = open_readonly_db(args.db)
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    try:
        reports = collect_reports(conn, args.project)
    except Exception as exc:  # noqa: BLE001 - 兜底，主控要的是 stderr 一行 + 退出码 2
        sys.stderr.write(f"ERROR: 未预期异常: {exc}\n")
        try:
            conn.close()
        except sqlite3.Error:
            pass
        return 2

    conn.close()

    drift_count = _count_drifts(reports)
    if args.as_json:
        print(render_json(reports))
    else:
        print(render_markdown(reports))

    return 0 if drift_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
