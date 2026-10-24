"""Model Connections 数据访问层（V3.9「连接层」——连接与档案解耦）。

职责：
- :class:`ConnectionService` —— ``model_connections`` 表 CRUD
  （与 :class:`packages.core.model_router.profiles.ProfileService` 镜像）。

**为什么要有这一层**（用户裁决 2026-10-21）：
「同一厂商，想分配不同模型承担不同职责，需要要重新完全新建。」
⇒ 连接信息（base_url / api_key / headers / models_url / protocol_base_urls）与
模型名分离到两条记录：同一厂商的多个档案**共享一条连接**，改一次 key 全局生效。

⚠️ **协议下放档案**（2026-10-24 裁决）：provider（协议）是**模型级属性**，由
档案自持；连接只是厂商网关（地址池 + key + headers），不再选协议，
也**不再向档案同步协议**（原 ``sync_provider_to_profiles`` 已随本裁决删除）。
连接的 ``protocol_base_urls`` 是协议地址池（协议 → 该网关此协议的 base_url，
JSON 覆盖表，迁移 0035），解析侧按档案的协议取址（见
:func:`packages.core.model_router.effective.resolve_effective`）。

写入语义（与档案 service 一致）：
- ``create`` —— 必填 ``name / provider``（provider 为连接的**主协议**，供无协议池
  场景与旧档案回退）；``base_url`` 可空（anthropic / ollama 用默认）；
  ``protocol_base_urls`` 可空（None = 无覆盖，全部走 base_url）；
  ``params`` 由调用方预先过滤 mask/空 api_key（POST 路径剥离）后传入。
- ``update_partial`` —— 按 ``fields`` dict 部分更新；空 dict 返回原行；
  ``params_json`` / ``protocol_base_urls`` 的 JSON 序列化由调用方处理
  （service 只收**已序列化**的字符串或 None）。
- ``delete`` —— 不存在 → False。

读路径统一返回 ``sqlite3.Row`` 转 dict；``_row_to_dict`` **不脱敏**、**不解析
JSON 列**——``params_json`` 的脱敏与 ``protocol_base_urls`` 的反序列化由 router 层
（:func:`packages.core.api.routers.connections._mask_connection`）处理。

异常：``IntegrityError`` 由调用方按业务映射（router 转 422）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id

__all__ = ["ConnectionService"]


class ConnectionService:
    """``model_connections`` 表 CRUD；router 仅做参数校验与错误映射。"""

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
    def get(self, connection_id: str) -> dict | None:
        """按主键取一条；不存在 → None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM model_connections WHERE connection_id = ?", (connection_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)

    # ----------------------------------------------------------------- list
    def list(self, *, include_enabled_only: bool = False) -> list[dict]:
        """列出全部连接（按 rowid ASC）；``include_enabled_only=True`` 时只取 enabled=1。"""
        sql = "SELECT * FROM model_connections"
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
        base_url: str | None,
        params: dict,
        enabled: int,
        protocol_base_urls: dict | None = None,
    ) -> dict:
        """插入一条连接，返回新行 dict。

        ``params`` 应是入参已剥离 mask/空 api_key 后的纯 dict；service 仅做 json 序列化。
        ``protocol_base_urls`` 应是入参已校验过的「协议 → 地址」dict（None = 无覆盖）；
        service 仅做 json 序列化。
        """
        connection_id = new_id("mconn")
        params_str = self._dump_params(params)
        pool_str = self._dump_params(protocol_base_urls) if protocol_base_urls else None
        ts = self._now_iso()
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO model_connections
                    (connection_id, name, provider, base_url, params_json, enabled,
                     created_at, updated_at, protocol_base_urls)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id, name, provider, base_url, params_str, enabled,
                    ts, ts, pool_str,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        row = self.get(connection_id)
        assert row is not None  # INSERT 必成功
        return row

    # --------------------------------------------------------------- update
    def update_partial(self, connection_id: str, fields: dict[str, Any]) -> dict | None:
        """按 ``fields`` dict 部分更新；``fields`` 空 → 直接返回当前行。

        - 不存在 → None（router 转 404）。
        - 自动维护 ``updated_at`` 字段。
        - ``params_json`` 字段值应为调用方已处理过的 JSON 字符串（service 不解析合并）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM model_connections WHERE connection_id = ?", (connection_id,)
            ).fetchone()
            if row is None:
                return None

            if fields:
                fields = dict(fields)
                fields["updated_at"] = self._now_iso()
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                values = list(fields.values()) + [connection_id]
                conn.execute(
                    f"UPDATE model_connections SET {set_clause} WHERE connection_id = ?",
                    values,
                )
                conn.commit()
        finally:
            conn.close()

        return self.get(connection_id)

    # ---------------------------------------------------------------- delete
    def delete(self, connection_id: str) -> bool:
        """删除；不存在 → False；存在 → True。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM model_connections WHERE connection_id = ?", (connection_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # --------------------------------------------------------------- helpers
    def list_profiles_referencing(self, connection_id: str) -> list[str]:
        """列出引用该连接的档案 id 清单（防 DELETE 误删 ⇒ router 转 409）。

        与 :meth:`ProfileService.list_bindings_referencing` 同款范式：
        **不静默解绑** —— 用户必须先改档案的归属，再删连接。
        """
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT profile_id FROM model_profiles WHERE connection_id = ?",
                (connection_id,),
            ).fetchall()
        finally:
            conn.close()
        return [r["profile_id"] for r in rows]

    def get_many(self, connection_ids: list[str]) -> list[dict]:
        if not connection_ids:
            return []
        conn = get_connection(self.db_path)
        try:
            placeholders = ",".join("?" * len(connection_ids))
            rows = conn.execute(
                f"SELECT * FROM model_connections WHERE connection_id IN ({placeholders})",
                list(connection_ids),
            ).fetchall()
        finally:
            conn.close()
        by_id = {r["connection_id"]: self._row_to_dict(r) for r in rows}
        return [by_id[cid] for cid in connection_ids if cid in by_id]
