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
from packages.core.model_router.effective import THINKING_MODES
from packages.core.model_router.router import CAPABILITY_LABELS

from .profiles import ProfileService

__all__ = ["BindingService", "UNSET_THINKING_OVERRIDE"]

# 「thinking_override 键缺席」的哨兵：与显式 None（清除覆盖）区分。
# PUT body 不带 thinking_override 键 ⇒ 保留 DB 现值；显式 null ⇒ 清除。
UNSET_THINKING_OVERRIDE = object()

# ``capability_bindings`` 的**单语句** upsert（2026-10-24 M10）。
#
# - 末位参数 ``keep=1``（``thinking_override`` 键缺席）⇒ ``ON CONFLICT DO UPDATE``
#   **不碰** ``thinking_override`` 列 ⇒ 保留 DB 现值；**不做「读出来再写回去」**
#   （那正是并发丢更新的窗口来源）。
# - ``keep=0`` ⇒ 用本次传入值覆盖（含显式 null = 清除覆盖）。
#
# ⚠️ 依赖的 SQLite 语义（写错了会静默改坏数据，故写在这里）：
#   ``ON CONFLICT DO UPDATE SET`` 里的**非限定列名**指的是**原行**的值，
#   ``excluded.`` 前缀才是「本次将被插入的新值」⇒
#   ``CASE WHEN ? = 1 THEN thinking_override`` 即「保留原值」。
#   无冲突（INSERT）时取 VALUES 里的值 ⇒ 新行的覆盖值为 NULL（跟随档案）。
_UPSERT_BINDING_SQL = """
    INSERT INTO capability_bindings (capability, profile_ids, thinking_override, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(capability) DO UPDATE SET
        profile_ids = excluded.profile_ids,
        thinking_override = CASE WHEN ? = 1 THEN thinking_override ELSE excluded.thinking_override END,
        updated_at  = excluded.updated_at
"""


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
        """按 capability 取一条；不存在 → None。

        返回 ``{capability, profile_ids, thinking_override, updated_at}``；
        ``thinking_override`` 为 None 表示「跟随档案」（迁移 0036 存量行/未设置）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT capability, profile_ids, thinking_override, updated_at "
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
            "thinking_override": row["thinking_override"],
            "updated_at": row["updated_at"],
        }

    # ----------------------------------------------------------------- list
    def list_with_profiles(self) -> list[dict]:
        """列出全部 binding（与 CAPABILITY_LABELS 取并集——含未绑定的环节）。

        每项返回 ``{capability, label, agents, profile_ids, profiles,
        legacy_available, thinking_override, updated_at}``。
        ``profiles`` 是按 ``profile_ids`` 顺序解析的档案详情列表（缺失跳过）。
        ``legacy_available`` 表示 ``model_configs`` 中该 capability 是否有 enabled 行
        （前端用于引导迁移到 binding）。``thinking_override`` 为 None = 跟随档案。
        """
        capabilities = list(CAPABILITY_LABELS.keys())
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT capability, profile_ids, thinking_override, updated_at "
                "FROM capability_bindings"
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
                "thinking_override": r["thinking_override"],
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
                "thinking_override": binding["thinking_override"] if binding else None,
                "updated_at": binding["updated_at"] if binding else None,
            })
        return out

    # ---------------------------------------------------------------- upsert
    def upsert(
        self,
        capability: str,
        profile_ids: list[str],
        thinking_override: Any = UNSET_THINKING_OVERRIDE,
    ) -> dict:
        """写入或更新 binding；自动校验 profile_ids 全部存在且 enabled=1。

        ``thinking_override``（环节级思考档覆盖，迁移 0036）三种入参语义：

        - :data:`UNSET_THINKING_OVERRIDE`（**键缺席**）→ 保留 DB 现值（行不存在则
          None）——调用方只改候选链时不会意外清掉已设的强度；
        - ``None`` → 清除覆盖（回到「跟随档案」）；
        - ``str`` → 必须在 :data:`effective.THINKING_MODES` 8 档枚举内，
          否则 :class:`InvalidBindingError`（路由层映射 422）。

        ⚠️ **单语句 upsert，不做读改写**（2026-10-24 M10）：「保留现值」由 SQL 的
        ``ON CONFLICT DO UPDATE`` 子句**不碰该列**来表达（``keep=1``），
        **不再「先 SELECT 读出现值、再把合并结果整行写回」**。
        旧写法在「读」与「写」之间留了窗口：两次并发 PUT（同一 capability，
        其一带 ``thinking_override``、另一带不带）里，后写的那次会把先写者
        刚设的覆盖值换成**读到的过期值**（丢更新，且无任何报错）。
        现在这一步「读」根本不存在 ⇒ 无窗口 ⇒ 语义与单线程逐字相同。
        """
        # 校验 capability 已知
        if capability not in CAPABILITY_LABELS:
            raise UnknownCapabilityError(capability)
        # 校验 thinking_override 值域（8 枚举或 None；缺席 = 保留现值）
        if thinking_override is UNSET_THINKING_OVERRIDE:
            # 键缺席 ⇒ 保留 DB 现值：**不在 Python 侧读**（读改写窗口的来源）；
            # INSERT 分支传 NULL ⇒ 行不存在时仍是「跟随档案」（与旧行为一致）。
            eff_override: str | None = None
            keep = 1
        elif thinking_override is None:
            eff_override = None
            keep = 0
        elif isinstance(thinking_override, str) and thinking_override in THINKING_MODES:
            eff_override = thinking_override
            keep = 0
        else:
            raise InvalidBindingError(
                f"thinking_override 必须是 8 档枚举之一或 null，收到: {thinking_override!r}"
            )
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
            conn.execute(_UPSERT_BINDING_SQL, (capability, ids_json, eff_override, ts, keep))
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
