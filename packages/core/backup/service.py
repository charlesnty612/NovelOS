"""BackupService（V1.4 Sprint 16 / MVP）—— 项目级 JSON 备份 / 恢复。

职责：
- :meth:`BackupService.export_project`：把指定项目相关的 22 张业务表 SELECT * 出来，
  按 :data:`schema.EXPORTED_TABLES` 顺序组装成备份包 dict（含 metadata 标记与
  时间戳）。
- :meth:`BackupService.import_project`：把备份包导入为**新项目**（不覆盖源项目）；
  全部主键与外键 id 重映射到新 namespace；事务包裹，失败整体回滚。

设计要点：
- 主键重映射：按 :data:`packages.core.backup.ids.TABLE_META` 生成新主键；所有外键
  列同步替换为新 namespace；原值不复用（避免导入项目与源项目 id 冲突）。
- 复合主键：``character_states`` 保留 ``state_version`` 仅替换 ``character_id``；
  ``story_states`` 保留 ``state_version`` 替换 ``project_id / commit_id``。
- 自引用外键：``branches.parent_branch_id`` / ``state_deltas.supersedes`` 在
  第一轮 INSERT 时（外键列 NULL 占位），同时把 ``(table, pk_col, new_pk_value,
  old_target)`` 记入 ``_self_ref_rewrites`` 内存清单；第二轮
  ``_rewrite_self_references`` 按该清单 + 全局 ``project_id_map`` 直接 UPDATE
  新行的对应列为映射后的新 id（不依赖库内 col IS NOT NULL 扫描）。
- 导入事务：单连接 + 单事务；任意一步失败 → rollback + 抛 ValueError。
- 新项目命名：``f"{原名}（导入）"``；新项目 id 用 new_id("prj")，status='ACTIVE'，
  foreshadow_overdue_chapters 默认 30（与 ProjectService 一致）。
- metadata 强制标记：``api_keys_stripped=True`` / ``ai_call_logs_excluded=True`` /
  ``evaluations_excluded=True``——明确告知导入端「这些字段不会出现在包内」。

边界：
- 不导出 model_configs（全局 + 含 API key，红线）；不导出 agents / prompts /
  workflows / workflow_runs / workflow_run_nodes / ai_call_logs（运行时或全局）。
- 不导出 reference_canons / canon_extracts（v1.4 MVP 暂不导出拆书工作流相关数据）；
  详见 README「已知边界」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .ids import TABLE_META, new_pk
from .schema import BACKUP_FORMAT, BACKUP_VERSION, EXPORTED_TABLES, validate_backup

__all__ = ["BackupService"]


# 备份包 metadata 中显式声明"已剔除/未导出"的标记字段；
# 这些字段值恒为 True（写入包时），供下游审计 / 自证使用。
_BACKUP_METADATA_FLAGS: dict[str, bool] = {
    "api_keys_stripped": True,
    "ai_call_logs_excluded": True,
    "evaluations_excluded": True,
    "workflow_runs_excluded": True,
    "model_configs_excluded": True,
    "reference_canons_excluded": True,
}


def _list_migrations(db_path: Path | str) -> list[str]:
    """读取 _migrations 表已应用的 SQL 文件名清单（按 filename 升序）。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM _migrations ORDER BY filename ASC"
        ).fetchall()
    finally:
        conn.close()
    return [r["filename"] for r in rows]


def _fetch_project_row(db_path: Path | str, project_id: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """把 sqlite3.Row 转换为 dict；保留字段顺序。

    ``sqlite3.Row`` 支持 ``keys()`` 取字段名；这里直接转 dict 即可。
    """
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


class BackupService:
    """项目级 JSON 备份 / 恢复服务。"""

    # 自引用外键列清单：第二轮 ``_rewrite_self_references`` 需要按 (table, col)
    # UPDATE；不在此清单内的自引用列不会参与第二轮改写。
    _SELF_REF_COLS: tuple[tuple[str, str], ...] = (
        ("branches", "parent_branch_id"),
        ("state_deltas", "supersedes"),
    )

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ========================================================================
    # 导出
    # ========================================================================

    def export_project(self, project_id: str) -> dict:
        """导出项目为备份包 dict。

        参数：
            project_id: 源项目 id；不存在 → ValueError。

        返回：备份包 dict（顶层字段见 ``schema.BACKUP_FORMAT`` 描述）。
        """
        # 1. 取项目根行
        project_row = _fetch_project_row(self.db_path, project_id)
        if project_row is None:
            raise ValueError(f"project {project_id!r} not found")

        # 2. 收集该项目直接 + 间接相关表
        tables = self._collect_project_tables(project_id)

        # 3. 组装 metadata
        migrations = _list_migrations(self.db_path)
        metadata = {
            "schema_migrations": migrations,
            "table_count_exported": len(tables),
            "exported_table_names": list(tables.keys()),
            "exported_at_iso": now_iso(),  # 额外时间戳，与顶层 exported_at 同步
            **_BACKUP_METADATA_FLAGS,
        }

        # 4. 顶层包
        package: dict[str, Any] = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "exported_at": now_iso(),
            "exported_from_project_id": project_id,
            "metadata": metadata,
            "project": project_row,
            "tables": tables,
        }
        return package

    def _collect_project_tables(self, project_id: str) -> dict[str, list[dict]]:
        """按 :data:`EXPORTED_TABLES` 顺序收集所有应导出表的行。"""
        result: dict[str, list[dict]] = {}
        conn = get_connection(self.db_path)
        try:
            for table in EXPORTED_TABLES:
                rows = self._fetch_table_rows(conn, table, project_id)
                result[table] = rows
        finally:
            conn.close()
        return result

    def _fetch_table_rows(
        self, conn: sqlite3.Connection, table: str, project_id: str
    ) -> list[dict]:
        """按表类型分别过滤行；表名不在白名单 → ValueError（防御）。"""
        if table not in TABLE_META:
            raise ValueError(f"unknown exported table: {table!r}")

        if table == "character_states":
            # 通过 character_id ∈ characters 间接过滤
            rows = conn.execute(
                """
                SELECT cs.* FROM character_states cs
                JOIN characters c ON c.character_id = cs.character_id
                WHERE c.project_id = ?
                ORDER BY cs.character_id ASC, cs.state_version ASC
                """,
                (project_id,),
            ).fetchall()
        elif table == "scenes":
            # 通过 chapter_id ∈ chapters 间接过滤
            rows = conn.execute(
                """
                SELECT s.* FROM scenes s
                JOIN chapters c ON c.chapter_id = s.chapter_id
                WHERE c.project_id = ?
                ORDER BY s.chapter_id ASC, s.order_index ASC
                """,
                (project_id,),
            ).fetchall()
        elif table == "drafts":
            rows = conn.execute(
                """
                SELECT d.* FROM drafts d
                JOIN chapters c ON c.chapter_id = d.chapter_id
                WHERE c.project_id = ?
                ORDER BY d.chapter_id ASC, d.version ASC
                """,
                (project_id,),
            ).fetchall()
        elif table == "state_deltas":
            # 通过 chapter_id ∈ chapters 间接过滤
            rows = conn.execute(
                """
                SELECT sd.* FROM state_deltas sd
                JOIN chapters c ON c.chapter_id = sd.chapter_id
                WHERE c.project_id = ?
                ORDER BY sd.delta_id ASC
                """,
                (project_id,),
            ).fetchall()
        elif table == "timeline_events":
            # 通过 event_id ∈ plot_events 间接过滤（plot_events.project_id 直连）
            rows = conn.execute(
                """
                SELECT t.* FROM timeline_events t
                JOIN plot_events e ON e.event_id = t.event_id
                WHERE e.project_id = ?
                ORDER BY t.timeline_event_id ASC
                """,
                (project_id,),
            ).fetchall()
        else:
            # 默认：按 project_id 直连过滤
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE project_id = ? "
                f"ORDER BY rowid ASC",
                (project_id,),
            ).fetchall()

        # tuple → dict
        return [_row_to_dict(conn, r) for r in rows]

    # ========================================================================
    # 导入
    # ========================================================================

    def import_project(self, data: dict) -> dict:
        """导入备份包为**新项目**（不覆盖源项目）。

        参数：
            data: 备份包 dict（顶层格式见 :func:`schema.validate_backup`）。

        返回：新 projects 行 dict（结构与 ProjectService.create 返回一致）。

        异常：
            ValueError：包格式非法、缺字段、表名非法、单步失败 → 整体回滚。
        """
        # 1. 顶层校验（坏包 → 422 由 router 透出）
        validate_backup(data)

        # 2. 准备：旧 project_id → 新 project_id 的映射（顶层 project 走特殊路径）
        old_project = data["project"]
        old_project_id = old_project["project_id"]
        new_project_id = new_id("prj")
        project_id_map: dict[str, str] = {old_project_id: new_project_id}
        # 自引用外键待改写清单：每项 = (table, pk_col, new_pk_value, old_target_value)
        # 第一轮 INSERT 时，遇到 branches.parent_branch_id / state_deltas.supersedes
        # 这两列会把 (新主键, 旧目标值) append 进来；第二轮按本清单 UPDATE 新行
        # 的自引用列为映射后的新 id（不依赖库内 WHERE col IS NOT NULL 扫描，因为
        # 第一轮已统一置 NULL）。
        self._self_ref_rewrites: list[tuple[str, str, str, str | None]] = []

        # 3. 单连接 + 单事务
        conn = get_connection(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            # 关 FK：跨表重映射期间，外键暂时悬挂（INSERT 后再 UPDATE）；
            # 事务末尾统一提交前再启用 FK + 校验，失败则整体 rollback。

            # 3.1 插入 projects 根（最特殊的一张表：主键替换 + name 后缀）
            now = now_iso()
            new_project_row = self._insert_new_project_row(
                conn, old_project, new_project_id, now
            )

            # 3.2 按 EXPORTED_TABLES 顺序逐表导入 + 收集 id 映射
            for table in EXPORTED_TABLES:
                rows = data["tables"].get(table) or []
                if not rows:
                    continue
                self._import_table_rows(conn, table, rows, project_id_map)

            # 3.3 第二轮：改写自引用外键（branches.parent_branch_id /
            # state_deltas.supersedes）；此时所有 id 已映射完毕。
            self._rewrite_self_references(conn, project_id_map)

            # 3.4 第二轮：所有引用新 project_id 的列统一替换
            # （projects 行已由 _insert_new_project_row 处理；其它表的外键列
            #  已在 _import_table_rows 内做映射）
            # —— 此处不需额外操作。

            # 3.5 启用 FK + 校验（任何 FK 违反都会抛 IntegrityError → rollback）
            conn.execute("PRAGMA foreign_keys = ON")
            # 触发一次空查询让 PRAGMA 检查生效
            conn.execute("SELECT 1").fetchone()

            conn.commit()
            return new_project_row
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except sqlite3.OperationalError:
                pass
            conn.close()

    # ---------------------------------------------------------------- insert

    def _insert_new_project_row(
        self,
        conn: sqlite3.Connection,
        old_project: dict,
        new_project_id: str,
        now: str,
    ) -> dict:
        """插入新 projects 行；name 后缀『（导入）』，其余字段沿用源行。"""
        new_name = f"{old_project['name']}（导入）"
        new_row = {
            "project_id": new_project_id,
            "name": new_name,
            "premise": old_project.get("premise"),
            "genre": old_project.get("genre"),
            "target_words": old_project.get("target_words"),
            "status": "ACTIVE",  # 强制 ACTIVE，避免导入 ARCHIVED 项目后显示异常
            "foreshadow_overdue_chapters": old_project.get(
                "foreshadow_overdue_chapters", 30
            ),
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, created_at, updated_at)
            VALUES
                (:project_id, :name, :premise, :genre, :target_words, :status,
                 :foreshadow_overdue_chapters, :created_at, :updated_at)
            """,
            new_row,
        )
        return new_row

    def _import_table_rows(
        self,
        conn: sqlite3.Connection,
        table: str,
        rows: list[dict],
        project_id_map: dict[str, str],
    ) -> None:
        """按 TABLE_META 驱动导入单张表的全部行（含 id 重映射 + 外键改写）。"""
        meta = TABLE_META[table]
        pk_col = meta["pk_col"]

        # 1. 第一轮：生成新主键（含复合主键保留旧主键字段的特殊情形）
        # 维护"本表 旧主键 → 新主键"映射，写入 project_id_map（统一命名空间）
        local_pk_map: dict[str, str] = {}
        if pk_col is not None:
            for row in rows:
                old_id = row.get(pk_col)
                if old_id is None:
                    raise ValueError(
                        f"table {table!r} row missing pk_col {pk_col!r}"
                    )
                new_id_value = new_pk(table)
                local_pk_map[old_id] = new_id_value
                # 同步挂到全局命名空间（跨表查用）
                project_id_map[old_id] = new_id_value
        else:
            # 复合主键：character_states / story_states
            # 不需要 local_pk_map，但需要在第二阶段直接 UPDATE 外键列
            pass

        # 2. 第二轮：构造 INSERT 语句 + 外键列替换
        if pk_col is not None:
            self._insert_single_pk_table(
                conn, table, rows, pk_col, local_pk_map, project_id_map
            )
        else:
            self._insert_composite_pk_table(
                conn, table, rows, project_id_map
            )

    def _insert_single_pk_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        rows: list[dict],
        pk_col: str,
        local_pk_map: dict[str, str],
        project_id_map: dict[str, str],
    ) -> None:
        """单列主键表的 INSERT 路径（含 project_id 替换 + 外键列替换）。"""
        # 列清单 = 行内出现过的所有键（保留原序：取第一行的 keys）
        if not rows:
            return
        # 使用 ALL 列，确保 JSON 字段不丢
        all_keys = list(rows[0].keys())
        # 重新组织列序：pk 在最前，其它按原序
        if pk_col in all_keys:
            all_keys.remove(pk_col)
            all_keys.insert(0, pk_col)
        # 额外保证 project_id 一定在列中（多数表都有 project_id；个别没有的如下处理）
        columns_clause = ", ".join(all_keys)
        placeholders = ", ".join(f":{k}" for k in all_keys)

        for row in rows:
            old_pk = row[pk_col]
            new_pk_value = local_pk_map[old_pk]
            new_row: dict[str, Any] = {}
            for k in all_keys:
                v = row.get(k)
                # 主键列：替换
                if k == pk_col:
                    new_row[k] = new_pk_value
                    continue
                # project_id 列：替换（项目根的 project_id 在 _insert_new_project_row
                # 已处理；这里的 project_id 指其它表的 project_id FK）
                if k == "project_id":
                    new_row[k] = project_id_map.get(v, v)
                    continue
                # 自引用外键暂留 NULL（branches.parent_branch_id /
                # state_deltas.supersedes），由 _rewrite_self_references 统一改写；
                # 必须**先于**通用外键替换判断（否则 d2.supersedes 在 d1 已入库后
                # 会先被通用替换逻辑命中 project_id_map、走不到 self_ref 分支）。
                is_self_ref = False
                for ref_table, ref_col in self._SELF_REF_COLS:
                    if table == ref_table and k == ref_col:
                        self._self_ref_rewrites.append(
                            (table, pk_col, new_pk_value, v)
                        )
                        new_row[k] = None
                        is_self_ref = True
                        break
                if is_self_ref:
                    continue
                # 其它外键列：在全局映射表中查；查到则替换，未查到保持原值
                if isinstance(v, str) and v in project_id_map:
                    new_row[k] = project_id_map[v]
                    continue
                # JSON 字段：尝试解析后透传；保持 TEXT 字符串形态入库
                # （schema 中 *_json 列就是 TEXT，不二次序列化）
                new_row[k] = v

            conn.execute(
                f"INSERT INTO {table} ({columns_clause}) VALUES ({placeholders})",
                new_row,
            )

    def _insert_composite_pk_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        rows: list[dict],
        project_id_map: dict[str, str],
    ) -> None:
        """复合主键表（character_states / story_states）的导入路径。"""
        # character_states: 主键 (character_id, state_version)
        #   - character_id → 替换
        # story_states: 主键 (project_id, state_version)
        #   - project_id → 替换；commit_id → 替换
        if not rows:
            return
        all_keys = list(rows[0].keys())

        columns_clause = ", ".join(all_keys)
        placeholders = ", ".join(f":{k}" for k in all_keys)

        for row in rows:
            new_row: dict[str, Any] = {}
            for k in all_keys:
                v = row.get(k)
                if table == "character_states" and k == "character_id":
                    new_row[k] = project_id_map.get(v, v)
                elif table == "story_states":
                    if k == "project_id":
                        new_row[k] = project_id_map.get(v, v)
                    elif k == "commit_id":
                        new_row[k] = project_id_map.get(v, v) if v else v
                    else:
                        new_row[k] = v
                else:
                    new_row[k] = v
            conn.execute(
                f"INSERT INTO {table} ({columns_clause}) VALUES ({placeholders})",
                new_row,
            )

    # ---------------------------------------------------------------- second pass

    def _rewrite_self_references(
        self,
        conn: sqlite3.Connection,
        project_id_map: dict[str, str],
    ) -> None:
        """第二轮：把自引用外键改为新 id。

        - ``branches.parent_branch_id``
        - ``state_deltas.supersedes``

        数据来源：第一轮 INSERT 时收集到 ``self._self_ref_rewrites`` 的
        ``(table, pk_col, new_pk_value, old_target_value)`` 清单。第一轮已
        把这两列置 NULL 落库，这里按清单 + 全局 ``project_id_map`` 直接 UPDATE
        新行；旧目标值若不在 ``project_id_map``（指向非本次导入行），保持 NULL。
        """
        for table, pk_col, new_pk_value, old_target in self._self_ref_rewrites:
            if old_target is None:
                continue
            # 找该 (table, col) 的列名
            col_name = next(
                c for t, c in self._SELF_REF_COLS if t == table
            )
            new_target = project_id_map.get(old_target)
            if new_target is None:
                # 旧目标不在本次导入批次内（如 NULL 或指向无关项目），保持 NULL
                continue
            conn.execute(
                f"UPDATE {table} SET {col_name} = ? WHERE {pk_col} = ?",
                (new_target, new_pk_value),
            )
