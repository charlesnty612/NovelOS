"""RevealPolicyService（V3.3 P0-2 知识权限补全）。

reveal_policies 表 CRUD + 业务规则（V3.3 schema，对齐 0014 DDL）。

设计要点（与并行代理共用模式）：
- 构造接收 ``db_path``；每个方法内部 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- 主键由 ``new_id("rp")`` 生成；时间戳由 ``now_iso()`` 生成。
- 校验：
  - ``target_kind`` 必须在 8 种枚举内；
  - ``status`` 必须在 3 态枚举内；
  - ``audience`` 非空字符串；
  - ``status='revealed'`` 必须显式提供 ``revealed_chapter``（业务规则：DDL CHECK
    仅约束枚举值，不强制 NOT NULL；service 层强约束以保证 arc 视图统计正确）；
  - ``reveal_by_chapter`` / ``revealed_chapter`` 必须为正整数（≥1，章节号从 1 起）。
- 引用完整性（按 ``target_kind`` 路由查表）：
  - character→characters.character_id；
  - location→locations.location_id；
  - faction→factions.faction_id；
  - world_rule→world_rules.world_rule_id；
  - event→plot_events.event_id；
  - hook→hooks.hook_id；
  - debt→narrative_debts.debt_id；
  - relationship→relationships.relationship_id。
  校验失败 → ValidationError（422）。
- 删除：不存在 → NotFoundError（404）；存在 → 删除。
- 不实现 FK 级联（DDL 无 FK from reveal_policies），删除策略由调用方决定。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .models import (
    REVEAL_POLICY_STATUSES,
    REVEAL_POLICY_TARGET_KINDS,
    RevealPolicy,
)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class KnowledgeServiceError(Exception):
    """RevealPolicyService 通用错误基类。"""

    http_status: int = 400


class NotFoundError(KnowledgeServiceError):
    http_status = 404


class ValidationError(KnowledgeServiceError):
    http_status = 422


# ---------------------------------------------------------------------------
# target_kind → 表/列路由
# ---------------------------------------------------------------------------


_TARGET_TABLE: dict[str, tuple[str, str]] = {
    "character":    ("characters",       "character_id"),
    "location":     ("locations",        "location_id"),
    "faction":      ("factions",         "faction_id"),
    "world_rule":   ("world_rules",      "world_rule_id"),
    "event":        ("plot_events",      "event_id"),
    "hook":         ("hooks",            "hook_id"),
    "debt":         ("narrative_debts",  "debt_id"),
    "relationship": ("relationships",    "relationship_id"),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RevealPolicyService:
    """reveal_policies 领域服务。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------------ create
    def create(
        self,
        *,
        project_id: str,
        target_kind: str,
        target_id: str,
        reveal_by_chapter: int | None = None,
        audience: str | None = None,
        status: str | None = None,
        revealed_chapter: int | None = None,
        notes: str | None = None,
    ) -> RevealPolicy:
        """创建 reveal_policy。校验 target_kind / status / audience / 实体存在性 /
        revealed_chapter 必填规则后 INSERT，返回完整行（带 policy_id / 时间戳）。

        - ``status='revealed'`` 必须显式提供 ``revealed_chapter``；
        - ``audience`` 默认 'reader'（与 DDL DEFAULT 对齐）。
        """
        self._require_project(project_id)
        self._validate_target_kind(target_kind)
        self._validate_status(status or "planned", revealed_chapter)
        audience = self._validate_audience(audience)
        self._validate_pos_int_or_none(reveal_by_chapter, "reveal_by_chapter")
        self._validate_pos_int_or_none(revealed_chapter, "revealed_chapter")
        self._require_target_exists(target_kind, target_id)

        policy_id = new_id("rp")
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO reveal_policies (
                    policy_id, project_id, target_kind, target_id,
                    reveal_by_chapter, audience, status, revealed_chapter,
                    notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id, project_id, target_kind, target_id,
                    reveal_by_chapter, audience,
                    status or "planned", revealed_chapter,
                    notes, now, now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValidationError(f"插入 reveal_policy 失败: {exc}") from exc
        finally:
            conn.close()

        return RevealPolicy(
            policy_id=policy_id,
            project_id=project_id,
            target_kind=target_kind,
            target_id=target_id,
            reveal_by_chapter=reveal_by_chapter,
            audience=audience,
            status=status or "planned",
            revealed_chapter=revealed_chapter,
            notes=notes,
            created_at=now,
            updated_at=now,
        )

    # -------------------------------------------------------------------- list
    def list(
        self,
        project_id: str,
        *,
        status: str | None = None,
    ) -> list[RevealPolicy]:
        """列项目下所有 reveal_policies，按 policy_id 稳定排序。

        - ``status`` 可选过滤（必须合法枚举；非法 → ValidationError）；
        - project 不存在 → 抛 ValidationError（与 PlotService 风格一致）。
        """
        self._require_project(project_id)
        if status is not None and status not in REVEAL_POLICY_STATUSES:
            raise ValidationError(
                f"status 非法: {status!r}，允许 {REVEAL_POLICY_STATUSES}"
            )
        sql = (
            "SELECT * FROM reveal_policies "
            "WHERE project_id = ?"
        )
        params: list[Any] = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY policy_id ASC"
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_policy(r) for r in rows]

    # -------------------------------------------------------------------- get
    def get(self, policy_id: str) -> RevealPolicy | None:
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM reveal_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_policy(row) if row else None

    # ------------------------------------------------------------------ update
    def update(
        self,
        policy_id: str,
        *,
        status: str | None = None,
        revealed_chapter: int | None = None,
        notes: str | None = None,
    ) -> RevealPolicy:
        """部分更新（仅允许改 status / revealed_chapter / notes 三字段，与设计文档 PATCH
        语义对齐）。

        - 不存在的 policy_id → NotFoundError（404）；
        - ``status='revealed'`` 必须显式带 ``revealed_chapter``（业务规则；
          若 update 单独改 status 而未提供 revealed_chapter，则尝试复用当前行的
          revealed_chapter；若当前行也为空，则抛 ValidationError）；
        - 若显式提供 revealed_chapter，必须为正整数；
        - 空 fields dict → 不写盘，直接返回当前行。
        """
        existing = self.get(policy_id)
        if existing is None:
            raise NotFoundError(f"reveal_policy#{policy_id} 不存在")

        fields: dict[str, Any] = {}
        if status is not None:
            # 决定校验用的 revealed_chapter：显式传入 > 现有值
            rc_for_check = (
                revealed_chapter if revealed_chapter is not None
                else existing.revealed_chapter
            )
            self._validate_status(status, rc_for_check)
            fields["status"] = status
        if revealed_chapter is not None:
            self._validate_pos_int_or_none(revealed_chapter, "revealed_chapter")
            fields["revealed_chapter"] = revealed_chapter
        if notes is not None:
            fields["notes"] = notes

        if fields:
            fields["updated_at"] = now_iso()
            set_clause = ", ".join(f"{k} = :{k}" for k in fields)
            params: dict[str, Any] = dict(fields)
            params["policy_id"] = policy_id
            conn = get_connection(self.db_path)
            try:
                conn.execute(
                    f"UPDATE reveal_policies SET {set_clause} "
                    "WHERE policy_id = :policy_id",
                    params,
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValidationError(f"更新 reveal_policy 失败: {exc}") from exc
            finally:
                conn.close()

        updated = self.get(policy_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ delete
    def delete(self, policy_id: str) -> None:
        """删除；不存在 → NotFoundError。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM reveal_policies WHERE policy_id = ?",
                (policy_id,),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"reveal_policy#{policy_id} 不存在")
            conn.commit()
        finally:
            conn.close()

    # ================================================================ helpers
    @staticmethod
    def _row_to_policy(row: sqlite3.Row | dict) -> RevealPolicy:
        d = dict(row)
        return RevealPolicy(
            policy_id=d["policy_id"],
            project_id=d["project_id"],
            target_kind=d["target_kind"],
            target_id=d["target_id"],
            reveal_by_chapter=d["reveal_by_chapter"],
            audience=d["audience"],
            status=d["status"],
            revealed_chapter=d["revealed_chapter"],
            notes=d["notes"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    def _require_project(self, project_id: str) -> None:
        if not project_id:
            raise ValidationError("project_id 不能为空")
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValidationError(f"project#{project_id} 不存在")

    @staticmethod
    def _validate_target_kind(target_kind: str) -> None:
        if target_kind not in REVEAL_POLICY_TARGET_KINDS:
            raise ValidationError(
                f"target_kind 非法: {target_kind!r}，"
                f"允许 {REVEAL_POLICY_TARGET_KINDS}"
            )

    @staticmethod
    def _validate_status(status: str, revealed_chapter: int | None) -> None:
        if status not in REVEAL_POLICY_STATUSES:
            raise ValidationError(
                f"status 非法: {status!r}，允许 {REVEAL_POLICY_STATUSES}"
            )
        if status == "revealed" and (
            revealed_chapter is None or not isinstance(revealed_chapter, int)
        ):
            raise ValidationError(
                "status='revealed' 时必须显式提供 revealed_chapter（正整数）"
            )

    @staticmethod
    def _validate_audience(audience: str | None) -> str:
        if audience is None:
            return "reader"
        if not isinstance(audience, str) or not audience.strip():
            raise ValidationError("audience 必须是非空字符串")
        return audience

    @staticmethod
    def _validate_pos_int_or_none(value: int | None, field_name: str) -> None:
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError(f"{field_name} 必须是正整数（≥1）")

    def _require_target_exists(self, target_kind: str, target_id: str) -> None:
        if not target_id:
            raise ValidationError("target_id 不能为空")
        table, pk_col = _TARGET_TABLE[target_kind]
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {pk_col} = ?", (target_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValidationError(
                f"{target_kind}#{target_id} 不存在（表 {table}）"
            )


__all__ = [
    "RevealPolicyService",
    "KnowledgeServiceError",
    "NotFoundError",
    "ValidationError",
]
