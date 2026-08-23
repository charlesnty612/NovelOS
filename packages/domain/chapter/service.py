"""ChapterService（Sprint 1 + Sprint 5 drafts 扩展）。

职责：chapters 表 CRUD + 状态机迁移约束 + 同项目内 number 唯一 +
Sprint 5 起的 chapter drafts 人工改稿能力（list / create）。
对齐 ``database/migrations/0001_init.sql`` 中 ``chapters`` 表结构（line 240-254）、
``drafts`` 表结构（line 270-280）。

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
- Sprint 5 drafts：
  - ``list_drafts(chapter_id)``：按 ``version DESC``；chapter 不存在返回 ``None``
    （让 router 转 404，与 ``ChapterService.get`` 一致）。
  - ``create_draft(chapter_id, content)``：仅当 chapter.status ∈ {DRAFTED, REVIEWED}
    时允许；其余状态抛 ``DraftStatusNotAllowed``（router 转 409，detail 携带当前 status）。
  - ``version`` 计算：``COALESCE(MAX(version), 0) + 1``，在 INSERT 同事务内执行，
    避免并发竞态下产生重复 version。
  - ``created_by`` 固定 ``"human"``；``prompt_version`` / ``model_id`` 写 NULL。
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


class DraftStatusNotAllowed(ChapterError):
    """当前 chapter.status 不允许新增 draft（仅 DRAFTED / REVIEWED 允许）。

    Sprint 5 任务书给死：人工改稿只能在 chapter 已进入「已写 / 已审」状态时追加；
    PLANNED 阶段不允许手写（应在 chapter 推进到 DRAFTED 之后），COMMITTED/RELEASED
    阶段正文已锁定。
    """

    def __init__(self, current: str) -> None:
        super().__init__(
            f"draft creation not allowed when chapter status is {current!r} "
            f"(only 'DRAFTED' or 'REVIEWED' accepted)"
        )
        self.current = current


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

    # ============================================================== Sprint 5
    # drafts：人工改稿能力（task A1）。
    # ----------------------------------------------------------------------

    # 仅 DRAFTED / REVIEWED 状态允许新增 draft（任务书给死）。
    _DRAFT_ALLOWED_STATUS: frozenset[str] = frozenset({"DRAFTED", "REVIEWED"})

    def list_drafts(self, chapter_id: str) -> list[dict] | None:
        """返回该 chapter 下全部 draft，按 ``version`` 降序。

        - chapter 不存在 → ``None``（router 转 404）。
        - 存在但无 draft → ``[]``。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT 1 FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            if cur.fetchone() is None:
                return None
            cur = conn.execute(
                "SELECT * FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                (chapter_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def create_draft(self, chapter_id: str, content: str) -> dict | None:
        """在 chapter 下新增一份 draft（人工改稿入口）。

        - chapter 不存在 → ``None``（router 转 404，与 ``ChapterService.get`` 风格一致）。
        - chapter.status ∉ {DRAFTED, REVIEWED} → ``DraftStatusNotAllowed``（router 转 409）。
        - ``version`` = ``COALESCE(MAX(version), 0) + 1``，在 INSERT 同事务内执行，
          避免并发竞态下产生重复 version。
        - ``created_by`` 固定 ``"human"``；``prompt_version`` / ``model_id`` 为 ``None``。
        - ``draft_id`` 用 ``new_id("dr")``；``created_at`` 用 ``now_iso()``。
        """
        if not content:
            # 防御性二次校验：router 层已用 pydantic ``min_length=1`` 拦截；此处兜底
            raise ValueError("content must be non-empty")

        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            # 1) chapter 存在性 & 当前 status 校验
            cur = conn.execute(
                "SELECT status FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            current_status = row["status"]
            if current_status not in self._DRAFT_ALLOWED_STATUS:
                raise DraftStatusNotAllowed(current_status)

            # 2) 计算下一 version（COALESCE 处理「尚无 draft」的情况）
            cur = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM drafts WHERE chapter_id = ?",
                (chapter_id,),
            )
            next_version = int(cur.fetchone()["v"]) + 1

            # 3) INSERT
            draft_id = new_id("dr")
            conn.execute(
                """
                INSERT INTO drafts
                    (draft_id, chapter_id, version, content,
                     created_by, prompt_version, model_id, created_at)
                VALUES
                    (:draft_id, :chapter_id, :version, :content,
                     :created_by, :prompt_version, :model_id, :created_at)
                """,
                {
                    "draft_id": draft_id,
                    "chapter_id": chapter_id,
                    "version": next_version,
                    "content": content,
                    "created_by": "human",
                    "prompt_version": None,
                    "model_id": None,
                    "created_at": now,
                },
            )
            conn.commit()
        finally:
            conn.close()

        # 走读路径返回完整行（保证字段类型一致）
        return {
            "draft_id": draft_id,
            "chapter_id": chapter_id,
            "version": next_version,
            "content": content,
            "created_by": "human",
            "prompt_version": None,
            "model_id": None,
            "created_at": now,
        }


__all__ = [
    "ChapterService",
    "ChapterNumberConflict",
    "ChapterTransitionError",
    "DraftStatusNotAllowed",
    "ChapterError",
]
