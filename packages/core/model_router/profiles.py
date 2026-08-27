"""Model Profiles 数据访问层（V3.7「模型档案 + 环节绑定」）。

职责：
- :class:`ProfileService` —— ``model_profiles`` 表 CRUD（与 :class:`ModelConfigService` 镜像）。
  把 :mod:`packages.core.api.routers.model_profiles` 的直接 SQL 收敛到本 service，
  路由层仅做参数校验 + 错误映射（与既有 service 层一致）。
- 写入语义：
  - ``create`` —— 必填 ``name / provider / model``；``params_json`` 由调用方预先过滤
    mask/空 api_key（POST 路径剥离）。
  - ``update_partial`` —— 按 ``fields`` dict 部分更新；空 dict 返回原行；
    ``params_json`` 字段的合并由调用方预先处理（PATCH 路径合并语义）。
  - ``delete`` —— 不存在 → False。
- 读路径统一返回 ``sqlite3.Row`` 转 dict；``_row_to_dict`` 不脱敏——脱敏由 router
  层 :func:`packages.core.model_router.security._mask_response` 处理。
- 异常：
  - ``IntegrityError`` 由调用方按业务映射（router 转 422）。

设计要点：
- 构造接收 ``db_path``；方法内部 ``packages.core.db.get_connection`` + try/finally。
- ``params_json`` 字段不参与本 service 的合并/脱敏——这是 router 层职责（涉及
  api_key mask / 空字符串特殊语义）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id

__all__ = ["ProfileService"]


class ProfileService:
    """``model_profiles`` 表 CRUD（V3.7 起独立 service；router 仅做参数校验）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    @staticmethod
    def _dump_params(params: dict) -> str:
        return json.dumps(params, ensure_ascii=False)

    @staticmethod
    def _now_iso() -> str:
        # 与 ids.now_iso 同语义；本地实现避免循环依赖。
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ get
    def get(self, profile_id: str) -> dict | None:
        """按主键取一条；不存在 → None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM model_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)

    # ----------------------------------------------------------------- list
    def list(self, *, include_enabled_only: bool = False) -> list[dict]:
        """列出全部档案（按 rowid ASC）；``include_enabled_only=True`` 时只取 enabled=1。"""
        sql = "SELECT * FROM model_profiles"
        if include_enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY rowid ASC"
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]  # type: ignore[arg-type]

    # ---------------------------------------------------------------- create
    def create(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        params: dict,
        enabled: int,
    ) -> dict:
        """插入一条档案，返回新行 dict。

        ``params`` 应是入参已剥离 mask/空 api_key 后的纯 dict；service 仅做 json 序列化。
        """
        profile_id = new_id("mprof")
        params_str = self._dump_params(params)
        ts = self._now_iso()
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO model_profiles
                    (profile_id, name, provider, model, params_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (profile_id, name, provider, model, params_str, enabled, ts, ts),
            )
            conn.commit()
        finally:
            conn.close()
        row = self.get(profile_id)
        assert row is not None  # INSERT 必成功
        return row

    # --------------------------------------------------------------- update
    def update_partial(
        self,
        profile_id: str,
        fields: dict[str, Any],
    ) -> dict | None:
        """按 ``fields`` dict 部分更新；``fields`` 空 → 直接返回当前行。

        - 不存在 → None（router 转 404）。
        - 自动维护 ``updated_at`` 字段。
        - ``params_json`` 字段值应为调用方已处理过的 JSON 字符串（service 不解析合并）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM model_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            if row is None:
                return None

            if fields:
                fields = dict(fields)
                fields["updated_at"] = self._now_iso()
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                values = list(fields.values()) + [profile_id]
                conn.execute(
                    f"UPDATE model_profiles SET {set_clause} WHERE profile_id = ?",
                    values,
                )
                conn.commit()
        finally:
            conn.close()

        return self.get(profile_id)

    # ---------------------------------------------------------------- delete
    def delete(self, profile_id: str) -> bool:
        """删除；不存在 → False；存在 → True。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM model_profiles WHERE profile_id = ?", (profile_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ---------------------------------------------------------------- helpers
    def list_bindings_referencing(self, profile_id: str) -> list[str]:
        """列出引用该 profile_id 的 capability 清单（防 DELETE 误删）。"""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT capability, profile_ids FROM capability_bindings", ()
            ).fetchall()
        finally:
            conn.close()
        refs: list[str] = []
        for r in rows:
            try:
                ids = json.loads(r["profile_ids"] or "[]")
            except (TypeError, ValueError):
                continue
            if profile_id in ids:
                refs.append(r["capability"])
        return refs

    def get_many(self, profile_ids: list[str]) -> list[dict]:
        """按 ID 列表批量取档案（保持入参顺序；缺失跳过）。"""
        if not profile_ids:
            return []
        conn = get_connection(self.db_path)
        try:
            placeholders = ",".join("?" * len(profile_ids))
            rows = conn.execute(
                f"SELECT * FROM model_profiles WHERE profile_id IN ({placeholders})",
                list(profile_ids),
            ).fetchall()
            by_id = {r["profile_id"]: self._row_to_dict(r) for r in rows}
        finally:
            conn.close()
        return [by_id[pid] for pid in profile_ids if pid in by_id]
