"""CharacterService（Sprint 1 + V2.0 Wave B 双写面统一）。

职责：characters + character_states 两张表的 CRUD 与业务规则。
对齐 ``database/migrations/0001_init.sql``：
- ``characters``（line 51-64）
- ``character_states``（line 70-80）
对齐 PRD §16/§17：Definition 与 State 分离。

设计要点：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- ``core_json`` / ``state_json`` / ``who_knows`` JSON 列：写入前
  ``json.dumps(ensure_ascii=False)``，读出后 ``json.loads``。
- ``create``：同一事务插入 ``characters`` 行 + ``character_states`` 首行
  （state_version=1, state_json={}, visibility=VISIBLE）。
- ``get``：JOIN 取最大 state_version 的 state_json 一起返回。
- ``update``：只允许改 name/role/core_json/visibility/who_knows；
  core_json 更新 = Definition 变更，注释说明 S2 起应走 Delta。
- ``delete``：同一事务级联删除 ``character_states`` 行后删除 ``characters`` 行。
- 列表端点按 project_id 过滤；查询不存在 → 返回 None，由 router 转 404。
- V2.0 Wave B 双写面统一：本 service 的 JSON 列序列化、who_knows 三态语义
  全部走 ``packages.core.story_state.write_helpers`` 共享助手，与 canon
  ``write_through`` 写透口径逐字节一致（who_knows None/[]/非空 三态、JSON 序列化）。
  原 ``_dump_json`` / ``_dump_json_or_null`` / ``_load_who_knows`` 已删除，
  改 import ``write_helpers``；保留本模块的局部 alias 以最小化函数体内调用面。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.write_helpers import (
    decode_who_knows as _load_who_knows,
)

# V2.0 Wave B：双写面共享写入助手（与 write_through 同源）
from packages.core.story_state.write_helpers import (
    dump_json as _dump_json,
)
from packages.core.story_state.write_helpers import (
    dump_json_or_null as _dump_json_or_null,
)

from .models import (
    CharacterCreate,
    CharacterUpdate,
)

# V2.0 Wave B 任务二：触发键助手（aliases 解析 + inject_mode 三态 fallback）。
# 与 packages/core/context_engine/builders._load_aliases / _load_inject_mode 同源语义，
# 但此处独立实现，避免 context_engine 被 domain 反向依赖。
_VALID_INJECT_MODES = ("auto", "always", "never")


def _dump_aliases(value: list[str] | None) -> str:
    """``aliases`` 列：始终写入 JSON 字符串（默认 ``'[]'``，与 0010 DEFAULT 对齐）。

    None / 空 list → ``'[]'``；非空 → JSON 数组字符串。
    """
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


class CharacterService:
    """角色领域服务（characters + character_states 表 CRUD）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_character(row: sqlite3.Row, state_row: sqlite3.Row | None) -> dict:
        d = dict(row)
        # core_json 反序列化
        if "core_json" in d and isinstance(d["core_json"], str):
            d["core_json"] = json.loads(d["core_json"]) if d["core_json"] else {}
        d["who_knows"] = _load_who_knows(d.get("who_knows"))
        # V2.0 Wave B 任务二：0010 加的 aliases / inject_mode 列。
        # 旧库升级时若读不到列（极老库）→ fallback（与 builder 行为一致）。
        d["aliases"] = _load_aliases(d.get("aliases"))
        d["inject_mode"] = _load_inject_mode(d.get("inject_mode"))

        if state_row is not None:
            d["latest_state_version"] = state_row["state_version"]
            raw_state = state_row["state_json"]
            d["latest_state_json"] = json.loads(raw_state) if raw_state else {}
        else:
            d["latest_state_version"] = 0
            d["latest_state_json"] = {}
        return d

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> dict:
        d = dict(row)
        if "state_json" in d and isinstance(d["state_json"], str):
            d["state_json"] = json.loads(d["state_json"]) if d["state_json"] else {}
        d["who_knows"] = _load_who_knows(d.get("who_knows"))
        return d

    # ------------------------------------------------------------------ create
    def create(self, project_id: str, payload: CharacterCreate) -> dict:
        """创建角色 + 初始化 state v1。同一事务。"""
        now = now_iso()
        character_id = new_id("char")
        role = payload.role or "supporting"
        visibility = payload.visibility or "PUBLIC"
        core_json = _dump_json(payload.core_json)
        who_knows = _dump_json_or_null(payload.who_knows)
        # V2.0 Wave B 任务二：触发键字段（0010 加列；缺字段 → fallback）。
        aliases_json = _dump_aliases(payload.aliases)
        inject_mode = payload.inject_mode or "auto"

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO characters
                    (character_id, project_id, name, role, core_json,
                     visibility, who_knows, created_at, updated_at,
                     aliases, inject_mode)
                VALUES
                    (:character_id, :project_id, :name, :role, :core_json,
                     :visibility, :who_knows, :created_at, :updated_at,
                     :aliases, :inject_mode)
                """,
                {
                    "character_id": character_id,
                    "project_id": project_id,
                    "name": payload.name,
                    "role": role,
                    "core_json": core_json,
                    "visibility": visibility,
                    "who_knows": who_knows,
                    "created_at": now,
                    "updated_at": now,
                    "aliases": aliases_json,
                    "inject_mode": inject_mode,
                },
            )
            # state v1：state_json={}, visibility=VISIBLE（DB DEFAULT 也是 VISIBLE；这里显式写出）
            conn.execute(
                """
                INSERT INTO character_states
                    (character_id, state_version, state_json, visibility, who_knows, created_at)
                VALUES
                    (?, 1, '{}', 'VISIBLE', NULL, ?)
                """,
                (character_id, now),
            )
            conn.commit()
        finally:
            conn.close()
        # 复用 get 路径返回完整（含 latest_state_json）
        result = self.get(character_id)
        assert result is not None, "freshly created character must be retrievable"
        return result

    # --------------------------------------------------------------------- get
    def get(self, character_id: str) -> dict | None:
        """按主键查询角色，附带 latest state。

        不存在返回 None。latest state 取 max(state_version) 的那一行。
        """
        conn = get_connection(self.db_path)
        try:
            char_row = conn.execute(
                "SELECT * FROM characters WHERE character_id = ?", (character_id,)
            ).fetchone()
            if char_row is None:
                return None
            state_row = conn.execute(
                """
                SELECT * FROM character_states
                WHERE character_id = ?
                ORDER BY state_version DESC
                LIMIT 1
                """,
                (character_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_character(char_row, state_row)

    # ---------------------------------------------------- list by project
    def list_by_project(self, project_id: str) -> list[dict]:
        """按 project_id 列角色；按 created_at ASC 排序（创建顺序）。"""
        conn = get_connection(self.db_path)
        try:
            char_rows = conn.execute(
                """
                SELECT * FROM characters
                WHERE project_id = ?
                ORDER BY created_at ASC, character_id ASC
                """,
                (project_id,),
            ).fetchall()
            if not char_rows:
                return []
            ids = [r["character_id"] for r in char_rows]
            placeholders = ",".join("?" for _ in ids)
            state_rows = conn.execute(
                f"""
                SELECT cs.*
                FROM character_states cs
                INNER JOIN (
                    SELECT character_id, MAX(state_version) AS v
                    FROM character_states
                    WHERE character_id IN ({placeholders})
                    GROUP BY character_id
                ) latest
                  ON latest.character_id = cs.character_id
                 AND latest.v = cs.state_version
                """,
                ids,
            ).fetchall()
        finally:
            conn.close()
        state_by_id = {r["character_id"]: r for r in state_rows}
        return [self._row_to_character(r, state_by_id.get(r["character_id"])) for r in char_rows]

    # ------------------------------------------------------------------- update
    def update(self, character_id: str, payload: CharacterUpdate) -> dict | None:
        """部分更新定义侧字段。不存在返回 None。

        core_json 更新：Sprint 1 直接覆盖（Definition 变更）；S2 起应走 State Delta。
        who_knows：None 显式置空；未提供（exclude_unset）则不动。
        aliases / inject_mode：V2.0 Wave B 任务二新增（expose_unset=True 时才写入）。
        """
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            return self.get(character_id)

        # JSON 列序列化
        if "core_json" in fields:
            fields["core_json"] = _dump_json(fields["core_json"])
        if "who_knows" in fields:
            fields["who_knows"] = _dump_json_or_null(fields["who_knows"])
        # V2.0 Wave B 任务二：aliases 序列化（None/空 list → '[]'，与 DB DEFAULT 对齐）。
        if "aliases" in fields:
            fields["aliases"] = _dump_aliases(fields["aliases"])
        if "inject_mode" in fields and fields["inject_mode"] not in _VALID_INJECT_MODES:
            # 非法值 fallback auto；不在此处抛错，保证 PATCH 不会因为单字段错失败整请求。
            fields["inject_mode"] = "auto"

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["character_id"] = character_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE characters SET {set_clause} WHERE character_id = :character_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
        finally:
            conn.close()
        return self.get(character_id)

    # ------------------------------------------------------------------- delete
    def delete(self, character_id: str) -> bool:
        """删除角色：同一事务级联删除 character_states 行后删除 characters 行。

        不存在返回 False。SQLite 默认未启用 ON DELETE CASCADE（DDL 未声明），
        必须 Service 显式级联——任务书给死。
        """
        conn = get_connection(self.db_path)
        try:
            conn.execute("DELETE FROM character_states WHERE character_id = ?", (character_id,))
            cur = conn.execute("DELETE FROM characters WHERE character_id = ?", (character_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------- list states
    def list_states(self, character_id: str) -> list[dict] | None:
        """列某角色的全部 state 历史版本（state_version 升序）。

        角色不存在返回 None（让 router 转 404）；存在但无 state 返回空列表。
        """
        conn = get_connection(self.db_path)
        try:
            exists = conn.execute(
                "SELECT 1 FROM characters WHERE character_id = ?", (character_id,)
            ).fetchone()
            if exists is None:
                return None
            rows = conn.execute(
                """
                SELECT * FROM character_states
                WHERE character_id = ?
                ORDER BY state_version ASC
                """,
                (character_id,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_state(r) for r in rows]


__all__ = ["CharacterService"]
