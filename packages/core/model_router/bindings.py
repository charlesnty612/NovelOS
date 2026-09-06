"""Capability Bindings 数据访问层（V3.7「模型档案 + 环节绑定」）。

职责：
- :class:`BindingService` —— ``capability_bindings`` 表 CRUD + 列表聚合。
  把 :mod:`packages.core.api.routers.capability_bindings` 的直接 SQL 收敛到本 service。
- 与 :class:`ProfileService` 配合：binding 引用 profile_id；service 层负责校验
  profile_ids 都存在且 enabled=1（写入前），引用计数（删除档案前）等。

设计要点：
- ``profile_ids`` 在 DB 是 JSON 数组字符串（顺序即 fallback 序）；
  service 层 ``json.loads`` 解析后用于按序构造响应。
- 读路径 ``list_with_profiles`` 一次性聚合：把 ``capability_bindings`` 的 profile_ids
  解析为 ``profiles`` 详情列表，便于前端直接渲染。
- 旧行为检测 ``legacy_available(capability)``：DB 是否有 ``model_configs``
  capability 匹配的 enabled=1 行（用于 GET 返回 legacy_available 字段，前端引导迁移）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.model_router.router import CAPABILITY_LABELS

from .profiles import ProfileService

__all__ = ["BindingService"]


class BindingService:
    """``capability_bindings`` 表 CRUD（V3.7 起独立 service；router 仅做参数校验）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        self._profiles = ProfileService(db_path)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dump_ids(ids: list[str]) -> str:
        return json.dumps(list(ids), ensure_ascii=False)

    # ------------------------------------------------------------------ get
    def get(self, capability: str) -> dict | None:
        """按 capability 取一条；不存在 → None。返回 ``{capability, profile_ids, updated_at}``。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT capability, profile_ids, updated_at "
                "FROM capability_bindings WHERE capability = ?",
                (capability,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            pids = json.loads(row["profile_ids"] or "[]")
        except (TypeError, ValueError):
            pids = []
        return {
            "capability": row["capability"],
            "profile_ids": [str(x) for x in pids if x],
            "updated_at": row["updated_at"],
        }

    # ----------------------------------------------------------------- list
    def list_with_profiles(self) -> list[dict]:
        """列出全部 binding（与 CAPABILITY_LABELS 取并集——含未绑定的环节）。

        每项返回 ``{capability, label, agents, profile_ids, profiles, legacy_available, updated_at}``。
        ``profiles`` 是按 ``profile_ids`` 顺序解析的档案详情列表（缺失跳过）。
        ``legacy_available`` 表示 ``model_configs`` 中该 capability 是否有 enabled 行
        （前端用于引导迁移到 binding）。
        """
        capabilities = list(CAPABILITY_LABELS.keys())
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT capability, profile_ids, updated_at FROM capability_bindings"
            ).fetchall()
        finally:
            conn.close()
        by_cap: dict[str, dict[str, Any]] = {}
        for r in rows:
            try:
                pids = json.loads(r["profile_ids"] or "[]")
            except (TypeError, ValueError):
                pids = []
            by_cap[r["capability"]] = {
                "profile_ids": [str(x) for x in pids if x],
                "updated_at": r["updated_at"],
            }

        out: list[dict] = []
        for cap in capabilities:
            meta = CAPABILITY_LABELS[cap]
            binding = by_cap.get(cap)
            pids = binding["profile_ids"] if binding else []
            profiles = [
                {"profile_id": p["profile_id"], "name": p.get("name") or "",
                 "model": p.get("model") or "", "provider": p.get("provider") or ""}
                for p in self._profiles.get_many(pids)
            ]
            out.append({
                "capability": cap,
                "label": meta["label"],
                "agents": list(meta["agents"]),
                "profile_ids": pids,
                "profiles": profiles,
                "legacy_available": self.legacy_available(cap),
                "updated_at": binding["updated_at"] if binding else None,
            })
        return out

    # ---------------------------------------------------------------- upsert
    def upsert(self, capability: str, profile_ids: list[str]) -> dict:
        """写入或更新 binding；自动校验 profile_ids 全部存在且 enabled=1。"""
        # 校验 capability 已知
        if capability not in CAPABILITY_LABELS:
            raise UnknownCapabilityError(capability)
        # 校验 profile_ids 非空
        if not profile_ids:
            raise InvalidBindingError("profile_ids must be non-empty")
        # 校验 profile_ids 全部存在且 enabled=1
        profiles = self._profiles.get_many(profile_ids)
        missing = [pid for pid in profile_ids
                   if not any(p["profile_id"] == pid for p in profiles)]
        if missing:
            raise InvalidBindingError(
                f"profile_ids 不存在或已禁用: {missing}"
            )
        disabled = [
            p["profile_id"] for p in profiles
            if p.get("enabled") != 1 and p["profile_id"] in profile_ids
        ]
        if disabled:
            raise InvalidBindingError(
                f"profile_ids 已禁用: {disabled}"
            )

        ts = self._now_iso()
        ids_json = self._dump_ids(profile_ids)
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO capability_bindings (capability, profile_ids, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(capability) DO UPDATE SET
                    profile_ids = excluded.profile_ids,
                    updated_at  = excluded.updated_at
                """,
                (capability, ids_json, ts),
            )
            conn.commit()
        finally:
            conn.close()
        result = self.get(capability)
        assert result is not None  # INSERT/UPDATE 必成功
        return result

    # ---------------------------------------------------------------- delete
    def delete(self, capability: str) -> bool:
        """解除 binding；不存在 → False。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM capability_bindings WHERE capability = ?", (capability,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ---------------------------------------------------------------- legacy
    def legacy_available(self, capability: str) -> bool:
        """``model_configs`` 中该 capability 是否有 enabled=1 行（旧行为兼容）。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM model_configs WHERE capability = ? AND enabled = 1 LIMIT 1",
                (capability,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None


class UnknownCapabilityError(Exception):
    """未知 capability（不在 CAPABILITY_LABELS 中）。"""


class InvalidBindingError(Exception):
    """binding 写入校验失败（profile_ids 空 / 不存在 / 禁用）。"""
