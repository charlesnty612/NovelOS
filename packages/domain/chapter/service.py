"""ChapterService（Sprint 1）。

职责：chapters 表 CRUD + 状态机迁移约束 + 同项目内 number 唯一。
对齐 ``database/migrations/0001_init.sql`` 中 ``chapters`` 表结构（line 240-254）。

设计要点：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- ``plan_json`` 列：写入前 ``json.dumps(ensure_ascii=False)``，读出后 ``json.loads``；
  pydantic 模型对应字段为 ``dict``。
- 列表按 ``number ASC`` 排序（任务书给死）。
- 状态机迁移白名单见 ``models.ALLOWED_NEXT``；非法跳变抛出 ``ChapterTransitionError``，
  router 转 409。
- 同 project 内 ``number`` 重复：DB 层没有 UNIQUE 索引，由 Service 在 INSERT 前查重，
  重复则抛 ``ChapterNumberConflict``，router 转 409。CHECK 违反（status 枚举外值）则
  透传 ``sqlite3.IntegrityError``，router 转 422。
- ``create`` / ``update`` 中的 ``status`` 变更：create 默认 ``PLANNED``，update 走状态机。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .models import (
    ALLOWED_NEXT,
    ChapterCreate,
    ChapterUpdate,
)


class ChapterError(Exception):
    """Chapter Service 业务异常基类。"""


class ChapterNumberConflict(ChapterError):
    """同一 project 下 number 已存在。"""


class ChapterTransitionError(ChapterError):
    """状态机非法迁移。"""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"illegal chapter status transition {current!r} -> {target!r}")
        self.current = current
        self.target = target


class ChapterService:
    """章节领域服务（chapters 表 CRUD）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _dump_json(value: dict | None) -> str:
        return json.dumps(value or {}, ensure_ascii=False)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        # plan_json 反序列化
        if "plan_json" in d and isinstance(d["plan_json"], str):
            d["plan_json"] = json.loads(d["plan_json"]) if d["plan_json"] else {}
        # who_knows 是 JSON 字符串数组（NULL 或 '[]' 或 '["..."]')
        if "who_knows" in d and d["who_knows"]:
            try:
                d["who_knows"] = json.loads(d["who_knows"])
            except json.JSONDecodeError:
                d["who_knows"] = None
        else:
            d["who_knows"] = None
        return d

    # ------------------------------------------------------------------ create
    def create(self, project_id: str, payload: ChapterCreate) -> dict:
        """创建章节。

        - 同 project 内 ``number`` 重复 → ``ChapterNumberConflict``（409）。
        - status 默认 ``PLANNED``（不支持创建时直接指定 status；状态机迁移从 PLANNED 起步）。
        """
        now = now_iso()
        plan_json = self._dump_json(payload.plan_json)

        conn = get_connection(self.db_path)
        try:
            # 1) number 唯一性预检（同事务内，避免并发竞态时产生重复行）
            cur = conn.execute(
                "SELECT 1 FROM chapters WHERE project_id = ? AND number = ?",
                (project_id, payload.number),
            )
            if cur.fetchone() is not None:
                raise ChapterNumberConflict(
                    f"chapter number {payload.number} already exists in project {project_id}"
                )

            row = {
                "chapter_id": new_id("ch"),
                "project_id": project_id,
                "number": payload.number,
                "title": payload.title,
                "plan_json": plan_json,
                "status": "PLANNED",
                "visibility": "VISIBLE",
                "who_knows": None,
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO chapters
                    (chapter_id, project_id, number, title, plan_json,
                     status, visibility, who_knows, created_at, updated_at)
                VALUES
                    (:chapter_id, :project_id, :number, :title, :plan_json,
                     :status, :visibility, :who_knows, :created_at, :updated_at)
                """,
                row,
            )
            conn.commit()
        finally:
            conn.close()
        # 走 get() 路径再读一次以保证 plan_json / who_knows 等列反序列化一致
        return self.get(row["chapter_id"])  # type: ignore[return-value]

    # --------------------------------------------------------------------- get
    def get(self, chapter_id: str) -> dict | None:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------- list by proj
    def list_by_project(self, project_id: str) -> list[dict]:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT * FROM chapters WHERE project_id = ? ORDER BY number ASC",
                (project_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------- update
    def update(self, chapter_id: str, payload: ChapterUpdate) -> dict | None:
        """部分更新。

        - 不存在 → None（router 转 404）。
        - ``plan_json`` 提供时整体替换为该 dict。
        - ``status`` 提供时校验状态机白名单，非法跳变 → ``ChapterTransitionError``。
        """
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            return self.get(chapter_id)

        # 状态机预检（需要先读 current status）
        if "status" in fields:
            current = self.get(chapter_id)
            if current is None:
                return None
            target = fields["status"]
            if target not in ALLOWED_NEXT.get(current["status"], set()):
                raise ChapterTransitionError(current["status"], target)

        # 处理 plan_json 序列化
        if "plan_json" in fields:
            fields["plan_json"] = self._dump_json(fields["plan_json"])

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["chapter_id"] = chapter_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE chapters SET {set_clause} WHERE chapter_id = :chapter_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
            cur = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------------- delete
    def delete(self, chapter_id: str) -> bool:
        """删除章节；不存在返回 False。

        存在 scenes / drafts 子记录时抛 ``sqlite3.IntegrityError``（FOREIGN KEY 约束），
        Service 不吞此异常，由 router 转 409。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (chapter_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


__all__ = [
    "ChapterService",
    "ChapterNumberConflict",
    "ChapterTransitionError",
    "ChapterError",
]
