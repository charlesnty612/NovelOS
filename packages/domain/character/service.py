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
- ``delete``：同一事务级联删除 ``character_states`` 行后删除 ``characters`` 行；
  **删前查引用**——被 ``relationships``（任一端点）引用 ⇒ 抛 ``ReferencedError``
  （409），不删任何东西（0022 摘掉那两条 FK ⇒ 孤儿行 DB 层查不出来）。
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
from packages.core.logging_config import get_logger
from packages.core.story_state.write_helpers import (
    decode_json_field as _decode_json_field,
)
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

# ⚠️ **跨域引用（character → world）**：① **复用** world 域的「删除被引用」错误类型
# （``packages/domain/world/service.py``；那份文件是**只读参照**给本任务用的，
# 本任务**只**在其中加共用守卫、不改它的错误族）；
# ② **共用** world 域的「删实体前查 relationships 端点」守卫
# （``assert_not_relationship_endpoint``）——character 与 faction 是 0022 之后的
# 两类关系端点 ⇒ 同一道守卫，两域各抛自己的错误类型（路由层按域窄捕获）。
# ⚠️ 域→域 import 在本仓**无成文禁令**（成文的只有 core 不得 import workflows、
# core↛domain）⇒ 这条依赖已被分层看守登记为**受控例外**（``ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS``）。
from packages.domain.world.service import (
    ReferencedError as _WorldReferencedError,
)
from packages.domain.world.service import (
    assert_not_relationship_endpoint,
)

from .models import (
    CharacterCreate,
    CharacterUpdate,
)

#: 本模块日志（2026-10-22 批次二十三：吞异常分诊 → B 类留痕；本层此前无 logger，
#: 引入的是仓内既有唯一约定 ``packages.core.logging_config.get_logger``）。
log = get_logger("novelos.domain.character.service")

# V2.0 Wave B 任务二：触发键助手（aliases 解析 + inject_mode 三态 fallback）。
# 与 packages/core/context_engine/builders._load_aliases / _load_inject_mode 同源语义，
# 但此处独立实现，避免 context_engine 被 domain 反向依赖。
_VALID_INJECT_MODES = ("auto", "always", "never")


class ReferencedError(_WorldReferencedError):
    """删除被引用 → 409（错误类型沿用 world 域同款，见类上方 import 处的说明）。

    ⚠️ 本仓域内错误族是「每个域自带一套」（world / plot / ledger / volume / knowledge /
    chapter 各有一套），而 ``ReferencedError`` 语义上**跨域同义**（都是「被引用 ⇒ 409」）
    ⇒ 这里**继承** world 域那个类而不是另起炉灶：
    - ``isinstance(exc, world.ReferencedError)`` 恒真 ⇒ 两域的「删除被引用」是**同一语义同一类型**；
    - 本仓**不会**出现第三个同名不同类的 ``ReferencedError``（plot 侧已有一个分叉）。

    ⚠️ **路由层按域窄捕获**：``except character.ReferencedError`` **不会**捕到
    world 域的基类实例（那是反过来：子类实例才会被基类的 ``except`` 捕到）
    ⇒ 路由层各自捕获本域的错误类型即可，**不要**为了「一个 except 覆盖两域」而改成
    捕基类——那会把 world 域的其它异常也吞成 409。
    """

    http_status = 409


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
    except json.JSONDecodeError as exc:
        # 2026-10-22 批次二十三（B 类留痕）：控制流不变（仍退空列表），但**留痕**——
        # 本函数无实体 id 入参，故用**阶段名**定位（entities.aliases）。
        log.warning(
            "entities.aliases 解析失败 ⇒ 别名退化为空列表（角色别名静默丢失）：err=%s",
            exc,
        )
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
        # ``core_json`` / ``latest_state_json`` 读路径走共享解码助手：一行脏 JSON
        # （非法文本或形状不符）不得让整个列表端点 500（plot 侧 2026-10-15 同款处置）。
        # ⚠️ 2026-10-16 批次九：这两处原先是裸 ``json.loads``（同函数的 who_knows /
        # aliases / inject_mode 早已兜底），实测 GET /projects/{pid}/characters 与
        # GET /characters/{id} 均因一行脏数据 500。
        d["core_json"] = _decode_json_field(d.get("core_json"), fallback={})
        d["who_knows"] = _load_who_knows(d.get("who_knows"))
        # V2.0 Wave B 任务二：0010 加的 aliases / inject_mode 列。
        # 旧库升级时若读不到列（极老库）→ fallback（与 builder 行为一致）。
        d["aliases"] = _load_aliases(d.get("aliases"))
        d["inject_mode"] = _load_inject_mode(d.get("inject_mode"))

        if state_row is not None:
            d["latest_state_version"] = state_row["state_version"]
            d["latest_state_json"] = _decode_json_field(state_row["state_json"], fallback={})
        else:
            d["latest_state_version"] = 0
            d["latest_state_json"] = {}
        return d

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["state_json"] = _decode_json_field(d.get("state_json"), fallback={})
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

        ⚠️ **引用检查**（2026-10-23 task-7 · D-12/M-10）：迁移 0022 摘掉了
        ``relationships.from_character_id`` / ``to_character_id`` 的外键
        （关系端点可以是 faction，FK 语义不再成立）⇒ 删角色会留**孤儿关系行**，
        而 ``PRAGMA foreign_key_check`` **报不出来**（FK 已不存在 ⇒ 检查无对象）
        ⇒ 与 world 域对齐：删之前显式查引用；被引用 ⇒ 抛 ``ReferencedError``（409）
        且**不删任何东西**。
        ⚠️ 守卫本体与 faction 侧**共用一份**（world 域的
        ``assert_not_relationship_endpoint``）⇒ 两端点列都查，不会再漏第二次。
        """
        conn = get_connection(self.db_path)
        try:
            # 引用检查：relationships 的**两个端点列都要查**（只查 from 是半修）
            assert_not_relationship_endpoint(
                conn, character_id, "character", ReferencedError
            )
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


__all__ = ["CharacterService", "ReferencedError"]
