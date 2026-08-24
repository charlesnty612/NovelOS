"""Model Configs 数据访问层（Sprint V1.5 架构整理 / V1.0 越层整改）。

职责：
- 把 :mod:`packages.core.api.routers.model_configs` 的直接 SQL 收敛到本 service，
  路由层仅做参数校验 + 错误映射（与既有 service 层一致）。
- 写入语义：
  - ``create`` —— 必填 capability / provider / model；params_json 由调用方预先过滤
    mask/空 api_key（POST 路径剥离）。
  - ``update_partial`` —— 按 ``fields`` dict 部分更新；空 dict 返回原行；
    params_json 字段的合并由调用方预先处理（PATCH 路径合并语义）。
  - ``delete`` —— 不存在 → False。
- 读路径统一返回 ``sqlite3.Row`` 转 dict；``_row_to_dict`` 不脱敏——脱敏由 router
  层 :func:`_mask_response` 处理（保持 service 语义纯粹、路由层负责对外契约）。
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

__all__ = ["ModelConfigService"]


class ModelConfigService:
    """``model_configs`` 表 CRUD（V1.5 起独立 service；router 仅做参数校验）。"""

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

    # ------------------------------------------------------------------ get
    def get(self, config_id: str) -> dict | None:
        """按主键取一条；不存在 → None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM model_configs WHERE config_id = ?", (config_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)

    # ----------------------------------------------------------------- list
    def list(
        self,
        *,
        capability: str | None = None,
        provider: str | None = None,
    ) -> list[dict]:
        """按 capability / provider 过滤列出（按 rowid ASC）。"""
        clauses: list[str] = []
        params: list = []
        if capability:
            clauses.append("capability = ?")
            params.append(capability)
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        sql = "SELECT * FROM model_configs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid ASC"
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]  # type: ignore[arg-type]

    # ---------------------------------------------------------------- create
    def create(
        self,
        *,
        capability: str,
        provider: str,
        model: str,
        params: dict,
        enabled: int,
    ) -> dict:
        """插入一条 model_config，返回新行 dict。

        ``params`` 应是入参已剥离 mask/空 api_key 后的纯 dict；
        service 仅做 json 序列化。
        """
        config_id = new_id("mcf")
        params_str = self._dump_params(params)
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO model_configs (config_id, capability, provider, model, params_json, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (config_id, capability, provider, model, params_str, enabled),
            )
            conn.commit()
        finally:
            conn.close()
        row = self.get(config_id)
        assert row is not None  # INSERT 必成功
        return row

    # --------------------------------------------------------------- update
    def update_partial(
        self,
        config_id: str,
        fields: dict[str, Any],
    ) -> dict | None:
        """按 ``fields`` dict 部分更新；``fields`` 空 → 直接返回当前行。

        - 不存在 → None（router 转 404）。
        - ``params_json`` 字段值应为调用方已处理过的 JSON 字符串（service 不解析合并）。
        """
        if not fields:
            return self.get(config_id)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [config_id]
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE model_configs SET {set_clause} WHERE config_id = ?",
                values,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
        finally:
            conn.close()
        return self.get(config_id)

    # ---------------------------------------------------------------- delete
    def delete(self, config_id: str) -> bool:
        """删除；不存在 → False；存在 → True。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM model_configs WHERE config_id = ?", (config_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
