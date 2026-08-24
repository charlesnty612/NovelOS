"""WorldService（Sprint 1 + V2.0 Wave B 双写面统一）。

三类世界实体（locations / factions / world_rules）的 CRUD + 一致性检查，
对齐 ``database/migrations/0001_init.sql`` 与 PRD §18。

设计要点
========
- 统一模式：``service.create_location/faction/world_rule`` 等接口同构。
- ``*_json`` 列：``json.dumps(ensure_ascii=False)`` 写入；读出 ``json.loads``。
- ``created_at`` / ``updated_at``：调用本地 ``now_iso()``。
- 查询不存在返回 ``None``（由 router 渲染为 404）。
- ``IntegrityError`` → 抛出 ``WorldServiceError``（422 语义）。
- 删除 location 被 plot_events 引用 → 409。
- visibility 取值校验在 Service 层完成；非法值 → 422。
- V2.0 Wave B 双写面统一：JSON 列序列化与 who_knows 三态语义改调
  ``packages.core.story_state.write_helpers`` 共享助手，与 canon
  ``write_through`` 写透口径逐字节一致；散落 ``json.dumps`` 调用收敛到共享实现。

不在本服务范围：地理拓扑计算、规则冲突推理（属后续 AI 增强）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.core.ids import new_id, now_iso

# V2.0 Wave B：双写面共享写入助手（与 write_through 同源）
from packages.core.story_state.write_helpers import (
    decode_who_knows as _decode_who_knows,
    dump_json as _dump_json,
    dump_json_or_null as _dump_json_or_null,
    now_iso_for_db,
)

from .models import VISIBILITY_VALUES, WorldEntity

# ---------------------------------------------------------------------------
# V2.0 Wave B 任务二：触发键助手（与 character service 同源语义）
# ---------------------------------------------------------------------------

_VALID_INJECT_MODES: tuple[str, ...] = ("auto", "always", "never")


def _dump_aliases(value: list[str] | None) -> str:
    """``aliases`` 列：始终写入 JSON 字符串（默认 ``'[]'``，与 0010 DEFAULT 对齐）。"""
    if not value:
        return "[]"
    return json.dumps(
        [str(x) for x in value if isinstance(x, (str, int, float))],
        ensure_ascii=False,
    )


def _load_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    return []


def _load_inject_mode(raw: str | None) -> str:
    if isinstance(raw, str) and raw in _VALID_INJECT_MODES:
        return raw
    return "auto"


# ---------------------------------------------------------------------------
# 异常（router 层据此映射 HTTP 状态码）
# ---------------------------------------------------------------------------


class WorldServiceError(Exception):
    """WorldService 通用错误基类。"""

    http_status: int = 400


class NotFoundError(WorldServiceError):
    """实体不存在 → 404。"""

    http_status = 404


class ReferencedError(WorldServiceError):
    """删除被引用 → 409。"""

    http_status = 409


class ValidationError(WorldServiceError):
    """字段非法 / 引用不存在 → 422。"""

    http_status = 422


# ---------------------------------------------------------------------------
# 实体配置（表名、PK 列名、ID 前缀、默认 visibility）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EntityConfig:
    table: str
    id_field: str          # DDL 主键列名（注意 world_rule_id 而非 world_rules_id）
    id_prefix: str         # ID 前缀
    default_visibility: str


_LOCATIONS = _EntityConfig(
    table="locations",
    id_field="location_id",
    id_prefix="loc",
    default_visibility="PUBLIC",
)
_FACTIONS = _EntityConfig(
    table="factions",
    id_field="faction_id",
    id_prefix="fac",
    default_visibility="VISIBLE",
)
_WORLD_RULES = _EntityConfig(
    table="world_rules",
    id_field="world_rule_id",
    id_prefix="wrule",
    default_visibility="PUBLIC",
)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class WorldService:
    """World 三实体的统一 Service。

    用法::

        svc = WorldService(db_path)
        loc = svc.create_location(project_id="prj_x", name="云海城")
        svc.delete_location(loc.id)   # 被 plot_events 引用时抛 ReferencedError

    构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
    ``try / finally`` 关闭。
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------------ locations

    def create_location(
        self,
        project_id: str,
        name: str,
        statement: str = "",
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        return self._create(
            _LOCATIONS,
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
            aliases=aliases,
            inject_mode=inject_mode,
        )

    def get_location(self, location_id: str) -> WorldEntity | None:
        return self._get(_LOCATIONS, location_id)

    def list_locations(self, project_id: str) -> list[WorldEntity]:
        return self._list(_LOCATIONS, project_id)

    def update_location(
        self,
        location_id: str,
        *,
        name: str | None = None,
        statement: str | None = None,
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        return self._update(
            _LOCATIONS,
            location_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
            aliases=aliases,
            inject_mode=inject_mode,
        )

    def delete_location(self, location_id: str) -> None:
        self._delete(_LOCATIONS, location_id)

    # ------------------------------------------------------------------ factions

    def create_faction(
        self,
        project_id: str,
        name: str,
        statement: str = "",
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        return self._create(
            _FACTIONS,
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
            aliases=aliases,
            inject_mode=inject_mode,
        )

    def get_faction(self, faction_id: str) -> WorldEntity | None:
        return self._get(_FACTIONS, faction_id)

    def list_factions(self, project_id: str) -> list[WorldEntity]:
        return self._list(_FACTIONS, project_id)

    def update_faction(
        self,
        faction_id: str,
        *,
        name: str | None = None,
        statement: str | None = None,
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        return self._update(
            _FACTIONS,
            faction_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
            aliases=aliases,
            inject_mode=inject_mode,
        )

    def delete_faction(self, faction_id: str) -> None:
        self._delete(_FACTIONS, faction_id)

    # --------------------------------------------------------------- world_rules

    def create_world_rule(
        self,
        project_id: str,
        name: str,
        statement: str = "",
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
    ) -> WorldEntity:
        return self._create(
            _WORLD_RULES,
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
        )

    def get_world_rule(self, world_rule_id: str) -> WorldEntity | None:
        return self._get(_WORLD_RULES, world_rule_id)

    def list_world_rules(self, project_id: str) -> list[WorldEntity]:
        return self._list(_WORLD_RULES, project_id)

    def update_world_rule(
        self,
        world_rule_id: str,
        *,
        name: str | None = None,
        statement: str | None = None,
        data: dict[str, Any] | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
    ) -> WorldEntity:
        return self._update(
            _WORLD_RULES,
            world_rule_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
        )

    def delete_world_rule(self, world_rule_id: str) -> None:
        self._delete(_WORLD_RULES, world_rule_id)

    # ====================================================================== 通用实现

    def _create(
        self,
        cfg: _EntityConfig,
        *,
        project_id: str,
        name: str,
        statement: str,
        data: dict[str, Any] | None,
        visibility: str | None,
        who_knows: list[str] | None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        # 同步前置校验（在开连接前完成，避免持有连接时校验失败）
        self._require_project(project_id)
        name = self._require_nonempty_str(name, "name")
        statement = statement or ""
        data = data if data is not None else {}
        if not isinstance(data, (dict, list)):
            raise ValidationError("data 必须是 JSON 对象或数组")
        visibility = self._validate_visibility(visibility, cfg)
        who_knows = self._validate_who_knows(who_knows)
        # V2.0 Wave B 任务二：触发键字段（仅 locations/factions；world_rules 不参与触发）
        inject_mode = self._validate_inject_mode(inject_mode)

        eid = new_id(cfg.id_prefix)
        now = now_iso()
        # V2.0 Wave B：JSON 列写入走 write_helpers 共享助手，与 canon write_through 同源
        data_json = _dump_json(data)
        who_knows_json = _dump_json_or_null(who_knows)
        aliases_json = _dump_aliases(aliases)

        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            if cfg.table in ("locations", "factions"):
                # V2.0 Wave B 任务二：locations / factions 加了 aliases + inject_mode 列
                conn.execute(
                    f"""
                    INSERT INTO {cfg.table}
                        ({cfg.id_field}, project_id, name, statement, data_json,
                         visibility, who_knows, created_at, updated_at,
                         aliases, inject_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, project_id, name, statement, data_json,
                        visibility, who_knows_json, now, now,
                        aliases_json, inject_mode,
                    ),
                )
            else:
                # world_rules：原 9 列（不带 aliases/inject_mode）
                conn.execute(
                    f"""
                    INSERT INTO {cfg.table}
                        ({cfg.id_field}, project_id, name, statement, data_json,
                         visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, project_id, name, statement, data_json,
                        visibility, who_knows_json, now, now,
                    ),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"插入失败: {exc}") from exc
        finally:
            conn.close()

        return WorldEntity(
            id=eid,
            id_field=cfg.id_field,
            id_prefix=cfg.id_prefix,
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility=visibility,
            who_knows=who_knows,
            aliases=_load_aliases(aliases_json) if cfg.table in ("locations", "factions") else [],
            inject_mode=inject_mode if cfg.table in ("locations", "factions") else "auto",
            created_at=now,
            updated_at=now,
        )

    def _get(self, cfg: _EntityConfig, eid: str) -> WorldEntity | None:
        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                f"SELECT * FROM {cfg.table} WHERE {cfg.id_field} = ?", (eid,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_entity(cfg, row)

    def _list(self, cfg: _EntityConfig, project_id: str) -> list[WorldEntity]:
        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT * FROM {cfg.table} WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_entity(cfg, r) for r in rows]

    def _update(
        self,
        cfg: _EntityConfig,
        eid: str,
        *,
        name: str | None,
        statement: str | None,
        data: dict[str, Any] | None,
        visibility: str | None,
        who_knows: list[str] | None,
        aliases: list[str] | None = None,
        inject_mode: str | None = None,
    ) -> WorldEntity:
        existing = self._get(cfg, eid)
        if existing is None:
            raise NotFoundError(f"{cfg.table}#{eid} 不存在")

        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(self._require_nonempty_str(name, "name"))
        if statement is not None:
            sets.append("statement = ?")
            params.append(statement)
        if data is not None:
            if not isinstance(data, (dict, list)):
                raise ValidationError("data 必须是 JSON 对象或数组")
            sets.append("data_json = ?")
            params.append(_dump_json(data))
        if visibility is not None:
            sets.append("visibility = ?")
            params.append(self._validate_visibility(visibility, cfg))
        if who_knows is not None:
            sets.append("who_knows = ?")
            params.append(
                _dump_json_or_null(self._validate_who_knows(who_knows))
            )
        # V2.0 Wave B 任务二：触发键字段（仅 locations/factions）
        if cfg.table in ("locations", "factions"):
            if aliases is not None:
                sets.append("aliases = ?")
                params.append(_dump_aliases(aliases))
            if inject_mode is not None:
                sets.append("inject_mode = ?")
                params.append(self._validate_inject_mode(inject_mode))

        if not sets:
            # 全部字段为 None：相当于 noop，直接返回现有实体
            return existing

        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(eid)

        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                f"UPDATE {cfg.table} SET {', '.join(sets)} WHERE {cfg.id_field} = ?",
                params,
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"更新失败: {exc}") from exc
        finally:
            conn.close()

        updated = self._get(cfg, eid)
        assert updated is not None  # 刚刚更新过
        return updated

    def _delete(self, cfg: _EntityConfig, eid: str) -> None:
        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            # 引用检查：locations 被 plot_events.location_id 引用时拒绝
            if cfg.table == "locations":
                in_use = conn.execute(
                    "SELECT 1 FROM plot_events WHERE location_id = ? LIMIT 1", (eid,)
                ).fetchone()
                if in_use is not None:
                    raise ReferencedError(
                        f"location#{eid} 被 plot_events 引用，无法删除"
                    )

            cur = conn.execute(
                f"DELETE FROM {cfg.table} WHERE {cfg.id_field} = ?", (eid,)
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"{cfg.table}#{eid} 不存在")
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------------- 辅助

    def _row_to_entity(self, cfg: _EntityConfig, row: sqlite3.Row) -> WorldEntity:
        # V2.0 Wave B：data_json 走标准 json.loads（dict 语义，未抽到 write_helpers
        # ——保持简单）；who_knows 走 write_helpers._decode_who_knows 与
        # canon 写透读侧统一（list[str] | None 类型约束）。
        data = json.loads(row["data_json"]) if row["data_json"] else {}
        who_knows_raw = row["who_knows"]
        who_knows = _decode_who_knows(who_knows_raw)
        # V2.0 Wave B 任务二：触发键字段（locations/factions；world_rules 走默认）
        aliases = _load_aliases(row["aliases"]) if cfg.table in ("locations", "factions") else []
        inject_mode = _load_inject_mode(row["inject_mode"]) if cfg.table in ("locations", "factions") else "auto"
        return WorldEntity(
            id=row[cfg.id_field],
            id_field=cfg.id_field,
            id_prefix=cfg.id_prefix,
            project_id=row["project_id"],
            name=row["name"],
            statement=row["statement"],
            data=data,
            visibility=row["visibility"],
            who_knows=who_knows,
            aliases=aliases,
            inject_mode=inject_mode,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _require_project(self, project_id: str) -> None:
        if not project_id:
            raise ValidationError("project_id 不能为空")
        from packages.core.db import get_connection

        conn = get_connection(self.db_path)
        try:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        finally:
            conn.close()
        if exists is None:
            raise ValidationError(f"project#{project_id} 不存在")

    @staticmethod
    def _require_nonempty_str(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field_name} 必须是非空字符串")
        return value

    @staticmethod
    def _validate_inject_mode(inject_mode: str | None) -> str:
        """V2.0 Wave B 任务二：inject_mode 校验，非法值 → ``auto``（保行为一致）。"""
        if inject_mode is None:
            return "auto"
        if inject_mode not in _VALID_INJECT_MODES:
            raise ValidationError(
                f"inject_mode 非法: {inject_mode!r}，允许 {_VALID_INJECT_MODES}"
            )
        return inject_mode

    @staticmethod
    def _validate_visibility(visibility: str | None, cfg: _EntityConfig) -> str:
        if visibility is None:
            return cfg.default_visibility
        if visibility not in VISIBILITY_VALUES:
            raise ValidationError(
                f"visibility 非法: {visibility!r}，允许 {VISIBILITY_VALUES}"
            )
        return visibility

    @staticmethod
    def _validate_who_knows(who_knows: list[str] | None) -> list[str] | None:
        if who_knows is None:
            return None
        if not isinstance(who_knows, list) or not all(
            isinstance(x, str) for x in who_knows
        ):
            raise ValidationError("who_knows 必须是字符串数组")
        return who_knows
