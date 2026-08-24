"""ProjectService（Sprint 1）。

职责：projects 表 CRUD + 业务规则。
对齐 ``database/migrations/0001_init.sql`` 中 ``projects`` 表结构（line 34-44）。

设计要点（与并行代理共用的统一模式）：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- 主键由 ``new_id("prj")`` 生成；时间戳由 ``now_iso()`` 生成。
- 列表端点默认过滤 ``status = 'ARCHIVED'``，可选参数 ``include_archived=True`` 时不过滤。
- 删除策略：当存在子记录（characters / chapters 等）时拒绝删除，由 router 转 409。
- 查询不存在 → 返回 ``None``，由 router 转 404；CHECK/FK 违反 → 抛出 ``sqlite3.IntegrityError``，
  router 转 422 并带 ``detail``。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .models import ProjectCreate, ProjectUpdate


class ProjectService:
    """项目领域服务（projects 表 CRUD）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------------ create
    def create(self, payload: ProjectCreate) -> dict:
        """创建项目，返回完整行 dict（含 project_id / created_at / updated_at）。"""
        now = now_iso()
        row = {
            "project_id": new_id("prj"),
            "name": payload.name,
            "premise": payload.premise,
            "genre": payload.genre,
            "target_words": payload.target_words,
            "status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
        }
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, name, premise, genre, target_words, status, created_at, updated_at)
                VALUES
                    (:project_id, :name, :premise, :genre, :target_words, :status, :created_at, :updated_at)
                """,
                row,
            )
            conn.commit()
        finally:
            conn.close()
        return row

    # --------------------------------------------------------------------- get
    def get(self, project_id: str) -> dict | None:
        """按主键查询；不存在返回 None。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    # -------------------------------------------------------------------- list
    def list(self, include_archived: bool = False) -> list[dict]:
        """列出项目，按 created_at 升序（创建顺序）。

        - 默认排除 ``status = 'ARCHIVED'``（与前端「归档后不再出现在主列表」文案一致）。
        - ``include_archived=True`` 不过滤，全量返回（含归档）。
        """
        conn = get_connection(self.db_path)
        try:
            if include_archived:
                cur = conn.execute(
                    "SELECT * FROM projects ORDER BY created_at ASC, project_id ASC"
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM projects "
                    "WHERE status != 'ARCHIVED' "
                    "ORDER BY created_at ASC, project_id ASC"
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------- update
    def update(self, project_id: str, payload: ProjectUpdate) -> dict | None:
        """部分更新；只更新 payload 中显式提供的字段。

        返回更新后的完整行；不存在返回 None。返回的 dict 由 SELECT * 取最新值。
        """
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            # 没有字段要更新：直接返回当前行（None 表示不存在）
            return self.get(project_id)

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["project_id"] = project_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE projects SET {set_clause} WHERE project_id = :project_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    # ------------------------------------------------------------------- delete
    def delete(self, project_id: str) -> bool:
        """删除项目。

        - 不存在：返回 False。
        - 存在子记录（characters / chapters / scenes / drafts 等通过 FK 引用
          project_id 的任意行）：sqlite3.IntegrityError（FOREIGN KEY constraint failed），
          由调用方映射为 409 Conflict。Service 不吞此异常。
        - 成功：返回 True。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------ children count (helper)
    def has_children(self, project_id: str) -> bool:
        """是否存在子记录（characters / chapters）。

        供 router 在 DELETE 时把 IntegrityError 翻译成更可读的 409 信息。
        当前 Sprint 实现：检查 characters 与 chapters 两张直接 FK 到 projects 的表。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM characters WHERE project_id = ?) +
                    (SELECT COUNT(*) FROM chapters   WHERE project_id = ?) AS cnt
                """,
                (project_id, project_id),
            ).fetchone()
        finally:
            conn.close()
        return bool(row["cnt"] > 0)


__all__ = ["ProjectService"]
