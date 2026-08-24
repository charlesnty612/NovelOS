"""LedgerService（Sprint 9）。

职责：``hooks`` 表与 ``narrative_debts`` 表的 CRUD + 状态机迁移约束 +
三个章节外键（hook）/两个章节外键（debt）的存在性校验。
对齐 ``database/migrations/0001_init.sql`` 中 ``hooks`` 表结构（line 197-214）与
``narrative_debts`` 表结构（line 219-235）。

设计要点
========
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- 状态机迁移白名单见 ``models.HOOK_ALLOWED_NEXT`` / ``DEBT_ALLOWED_NEXT``；
  非法跳变抛出 ``LedgerTransitionError``，router 转 409。
- 章节外键（如 ``introduced_chapter_id`` / ``payoff_chapter_id`` /
  ``created_chapter_id`` 等）：create / update 时若提供则校验存在性，
  不存在 → ``ChapterNotFound``（router 转 404）。
- ``who_knows`` 列：JSON 字符串数组；NULL 沿用默认，``'[]'`` 显式置空
  （对齐 knowledge-permission-v0.md §3.1）。
- ``created_at`` / ``updated_at``：调用本地 ``now_iso()``。
- 与 State Delta 的关系：本 CRUD 是「管理面」的人工维护入口；
  Observer 的写透路径（``story_state`` service）不动；
  ``hooks`` / ``debts`` 表是状态快照展示的权威来源之一。

不在本服务范围
--------------
- 自动从 story_state 同步（属 Observer 写透职责，本 Sprint 不接入）。
- 逾期判定（属前端 UI 职责，调用 ``GET /projects/{pid}/chapters`` 比对 number）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

# V2.0 Wave B：双写面共享写入助手（与 write_through 同源）；
# who_knows 三态语义与 JSON 列序列化与 canon 写透统一。
from packages.core.story_state.write_helpers import (
    decode_who_knows as _decode_who_knows,
    dump_json_or_null as _dump_json_or_null,
    now_iso_for_db,
)

from .models import (
    DEBT_ALLOWED_NEXT,
    HOOK_ALLOWED_NEXT,
    DebtCreate,
    DebtUpdate,
    HookCreate,
    HookUpdate,
)

# ---------------------------------------------------------------------------
# 异常（router 层据此映射 HTTP 状态码）
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Ledger Service 业务异常基类。"""


class NotFoundError(LedgerError):
    """实体不存在 → 404。"""


class ChapterNotFound(LedgerError):
    """引用的 chapter_id 不存在 → 404。"""

    def __init__(self, chapter_id: str) -> None:
        super().__init__(f"chapter {chapter_id!r} not found")
        self.chapter_id = chapter_id


class LedgerTransitionError(LedgerError):
    """状态机非法迁移 → 409。"""

    def __init__(self, kind: str, current: str, target: str) -> None:
        super().__init__(
            f"illegal {kind} status transition {current!r} -> {target!r}"
        )
        self.kind = kind  # "hook" | "debt"
        self.current = current
        self.target = target


class ValidationError(LedgerError):
    """字段非法 → 422。"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LedgerService:
    """Hook 与 Debt 的统一 Service。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ====================================================================== hooks

    def create_hook(self, project_id: str, payload: HookCreate) -> dict:
        """创建 Hook。"""
        now = now_iso()
        name = payload.name.strip()
        if not name:
            raise ValidationError("hook name must be non-empty")

        # 章节外键存在性校验
        for fld in (
            "introduced_chapter_id",
            "expected_payoff_chapter_id",
            "payoff_chapter_id",
        ):
            cid = getattr(payload, fld)
            if cid is not None:
                self._require_chapter(cid)

        status = payload.status or "OPEN"
        visibility = (payload.visibility or "RESTRICTED").strip()
        if visibility not in ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"):
            raise ValidationError(f"visibility illegal: {visibility!r}")
        who_knows_json = _dump_json_or_null(payload.who_knows)
        hook_id = new_id("hook")

        conn = get_connection(self.db_path)
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO hooks
                        (hook_id, project_id, name, introduced_chapter_id, status,
                         importance, expected_payoff_chapter_id, payoff_chapter_id,
                         visibility, who_knows, created_at, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hook_id,
                        project_id,
                        name,
                        payload.introduced_chapter_id,
                        status,
                        payload.importance,
                        payload.expected_payoff_chapter_id,
                        payload.payoff_chapter_id,
                        visibility,
                        who_knows_json,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"hook insert failed: {exc}") from exc
        finally:
            conn.close()

        out = self.get_hook(hook_id)
        assert out is not None
        return out

    def get_hook(self, hook_id: str) -> dict | None:
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM hooks WHERE hook_id = ?", (hook_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_hook(row) if row else None

    def list_hooks_by_project(
        self, project_id: str, status: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM hooks WHERE project_id = ?"
        params: list = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC"
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_hook(r) for r in rows if r is not None]

    def update_hook(self, hook_id: str, payload: HookUpdate) -> dict | None:
        """部分更新 Hook。"""
        existing = self.get_hook(hook_id)
        if existing is None:
            return None

        fields: dict = {}

        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise ValidationError("hook name must be non-empty")
            fields["name"] = name

        for fld in (
            "introduced_chapter_id",
            "expected_payoff_chapter_id",
            "payoff_chapter_id",
        ):
            v = getattr(payload, fld)
            if v is not None and not isinstance(v, str):
                raise ValidationError(f"{fld} must be string or null")
            if v is not None:
                self._require_chapter(v)
            fields[fld] = v

        if payload.status is not None:
            current = existing["status"]
            if payload.status not in HOOK_ALLOWED_NEXT.get(current, set()):
                raise LedgerTransitionError("hook", current, payload.status)
            fields["status"] = payload.status

        if payload.importance is not None:
            fields["importance"] = payload.importance

        if payload.visibility is not None:
            if payload.visibility not in ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"):
                raise ValidationError(f"visibility illegal: {payload.visibility!r}")
            fields["visibility"] = payload.visibility

        if payload.who_knows is not None:
            v = payload.who_knows
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValidationError("who_knows must be list[str] or null")
            fields["who_knows"] = _dump_json_or_null(v)

        if not fields:
            return existing

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["hook_id"] = hook_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE hooks SET {set_clause} WHERE hook_id = :hook_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
        finally:
            conn.close()

        out = self.get_hook(hook_id)
        assert out is not None
        return out

    def delete_hook(self, hook_id: str) -> bool:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM hooks WHERE hook_id = ?", (hook_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ==================================================================== debts

    def create_debt(self, project_id: str, payload: DebtCreate) -> dict:
        """创建 Debt。"""
        now = now_iso()
        description = payload.description.strip()
        if not description:
            raise ValidationError("debt description must be non-empty")

        for fld in ("created_chapter_id", "deadline_chapter_id"):
            cid = getattr(payload, fld)
            if cid is not None:
                self._require_chapter(cid)

        status = payload.status or "open"
        visibility = (payload.visibility or "RESTRICTED").strip()
        if visibility not in ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"):
            raise ValidationError(f"visibility illegal: {visibility!r}")
        who_knows_json = _dump_json_or_null(payload.who_knows)
        debt_id = new_id("debt")

        conn = get_connection(self.db_path)
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO narrative_debts
                        (debt_id, project_id, description, created_chapter_id,
                         severity, deadline_chapter_id, status, visibility,
                         who_knows, created_at, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        debt_id,
                        project_id,
                        description,
                        payload.created_chapter_id,
                        payload.severity,
                        payload.deadline_chapter_id,
                        status,
                        visibility,
                        who_knows_json,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"debt insert failed: {exc}") from exc
        finally:
            conn.close()

        out = self.get_debt(debt_id)
        assert out is not None
        return out

    def get_debt(self, debt_id: str) -> dict | None:
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM narrative_debts WHERE debt_id = ?", (debt_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_debt(row) if row else None

    def list_debts_by_project(
        self, project_id: str, status: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM narrative_debts WHERE project_id = ?"
        params: list = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC"
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_debt(r) for r in rows if r is not None]

    def update_debt(self, debt_id: str, payload: DebtUpdate) -> dict | None:
        """部分更新 Debt。"""
        existing = self.get_debt(debt_id)
        if existing is None:
            return None

        fields: dict = {}

        if payload.description is not None:
            description = payload.description.strip()
            if not description:
                raise ValidationError("debt description must be non-empty")
            fields["description"] = description

        for fld in ("created_chapter_id", "deadline_chapter_id"):
            v = getattr(payload, fld)
            if v is not None and not isinstance(v, str):
                raise ValidationError(f"{fld} must be string or null")
            if v is not None:
                self._require_chapter(v)
            fields[fld] = v

        if payload.status is not None:
            current = existing["status"]
            if payload.status not in DEBT_ALLOWED_NEXT.get(current, set()):
                raise LedgerTransitionError("debt", current, payload.status)
            fields["status"] = payload.status

        if payload.severity is not None:
            fields["severity"] = payload.severity

        if payload.visibility is not None:
            if payload.visibility not in ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"):
                raise ValidationError(f"visibility illegal: {payload.visibility!r}")
            fields["visibility"] = payload.visibility

        if payload.who_knows is not None:
            v = payload.who_knows
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValidationError("who_knows must be list[str] or null")
            fields["who_knows"] = json.dumps(v, ensure_ascii=False)

        if not fields:
            return existing

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["debt_id"] = debt_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE narrative_debts SET {set_clause} WHERE debt_id = :debt_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
        finally:
            conn.close()

        out = self.get_debt(debt_id)
        assert out is not None
        return out

    def delete_debt(self, debt_id: str) -> bool:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM narrative_debts WHERE debt_id = ?", (debt_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ================================================================== helpers

    @staticmethod
    def _row_to_hook(row: sqlite3.Row) -> dict:
        d = dict(row)
        # V2.0 Wave B：who_knows 走 write_helpers._decode_who_knows 与
        # canon 写透读侧统一（list[str] | None 类型约束、解析失败兜底为 None）。
        d["who_knows"] = _decode_who_knows(d.get("who_knows"))
        return d

    @staticmethod
    def _row_to_debt(row: sqlite3.Row) -> dict:
        d = dict(row)
        # V2.0 Wave B：同上（narrative_debts.who_knows）
        d["who_knows"] = _decode_who_knows(d.get("who_knows"))
        return d

    def _require_chapter(self, chapter_id: str) -> None:
        """校验 chapter_id 存在；不存在抛 ChapterNotFound。

        不限制 project：让跨项目引用的「伏笔在另一章兑现」场景仍能落库（业务
        上一般同 project；若未来需强制同 project，由 router 在事务内
        二次校验）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChapterNotFound(chapter_id)


__all__ = [
    "LedgerService",
    "LedgerError",
    "NotFoundError",
    "ChapterNotFound",
    "LedgerTransitionError",
    "ValidationError",
]