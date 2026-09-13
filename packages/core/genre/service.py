"""GenrePackService —— 题材包 CRUD / 项目绑定 / payload 校验（题材库 P1a）。

职责：
- payload 校验：``docs/state-model/schemas/genre-pack.schema.json``（Draft 2020-12）
  是唯一权威；错误串格式 ``"[schema] <path>: <message>"``（与
  ``packages/core/story_state/validator.py`` / deconstruct 的 canon 校验同款），
  由 router 转 422（``detail={"errors": [...]}``，与 ``routers/story_state.py`` 一致）。
- CRUD：create / list / get / update（提供 payload 时 version 自增）/ delete。
- 绑定：bind（项目单 slot，重复绑定=覆盖）/ unbind / get_project_binding；
  delete 前由 router 调 :meth:`count_bindings` 做 409 前置拦截（DDL 侧 Fk
  ON DELETE SET NULL 兜底）。

设计要点：
- 生命周期与 reference_canons 完全独立：本模块不读写 canon 表，canon 侧也不感知
  题材包；两者仅共享「注入管道」这一实现模式（见 docs/roadmap/题材库-评估与落地计划 §三）。
- 每个方法内部 ``get_connection`` + try/finally；写操作单事务 commit。
- 校验器 ``lru_cache`` 缓存（schema 文件不随进程变化）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from . import queries as q
from .model import (
    GENRE_PACK_SCHEMA_PATH,
    GenrePackCreate,
    GenrePackUpdate,
)

__all__ = ["GenrePackService", "BindStatus", "validate_payload"]


class BindStatus:
    """绑定/解绑结果码（router 据此映射 404 / 409 / 200）。"""

    OK = "ok"
    PROJECT_NOT_FOUND = "project_not_found"
    PACK_NOT_FOUND = "pack_not_found"
    NOT_BOUND = "not_bound"


@lru_cache(maxsize=1)
def _load_validator(schema_path_str: str) -> Draft202012Validator:
    """加载并缓存 schema 校验器（``lru_cache`` 需 hashable 参数，故用字符串路径）。"""
    path = Path(schema_path_str)
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    return Draft202012Validator(schema)


def validate_payload(
    payload: Any, schema_path: Path | str | None = None,
) -> list[str]:
    """按 genre-pack schema 校验 payload，返回错误列表（空 = 通过）。

    - 非 dict（list / str / None）→ 单条 ``[schema] <root>: ...`` 错误；
    - 错误按 ``absolute_path`` 排序，格式 ``"[schema] <path>: <message>"``。
    """
    if not isinstance(payload, dict):
        return ["[schema] <root>: payload 必须是 JSON 对象（dict）"]
    sp = Path(schema_path) if schema_path is not None else GENRE_PACK_SCHEMA_PATH
    validator = _load_validator(str(sp))
    errs: list[str] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        path_str = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errs.append(f"[schema] {path_str}: {err.message}")
    return errs


class GenrePackService:
    """``genre_packs`` 表 CRUD + ``projects.genre_pack_id`` 绑定。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------- 校验
    @staticmethod
    def validate_payload(payload: Any, schema_path: Path | str | None = None) -> list[str]:
        """payload 校验（薄封装 :func:`validate_payload`，便于调用方注入 schema 路径）。"""
        return validate_payload(payload, schema_path)

    # ------------------------------------------------------------- CRUD
    def create(
        self, payload: GenrePackCreate | dict[str, Any],
    ) -> dict[str, Any]:
        """创建题材包；``pack_id`` 显式给出时用其原值（内容仓 slug）。

        返回完整 dict（``payload`` 已解析）。``pack_id`` 冲突 → sqlite3.IntegrityError
        透传给 router（转 422）。
        """
        data = (
            payload if isinstance(payload, GenrePackCreate) else GenrePackCreate(**payload)
        )
        pack_id = (data.pack_id or "").strip() or new_id("gp")
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                q.INSERT_PACK_SQL,
                (
                    pack_id,
                    data.name,
                    data.genre_tag,
                    1,
                    json.dumps(data.payload, ensure_ascii=False),
                    data.source_path,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone()
        finally:
            conn.close()
        return q.pack_row_to_dict(row) if row is not None else {}

    def list_packs(self, *, genre_tag: str | None = None) -> list[dict[str, Any]]:
        """列出题材包摘要（``created_at DESC``；可选按题材标签过滤）。"""
        conn = get_connection(self.db_path)
        try:
            if genre_tag:
                rows = conn.execute(q.LIST_PACKS_BY_TAG_SQL, (genre_tag,)).fetchall()
            else:
                rows = conn.execute(q.LIST_PACKS_SQL).fetchall()
        finally:
            conn.close()
        return [q.pack_row_to_summary(r) for r in rows]

    def get(self, pack_id: str) -> dict[str, Any] | None:
        """取单个题材包全文；不存在 → None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone()
        finally:
            conn.close()
        return q.pack_row_to_dict(row) if row is not None else None

    def update(
        self, pack_id: str, payload: GenrePackUpdate | dict[str, Any],
    ) -> dict[str, Any] | None:
        """部分更新；提供 ``payload`` 时 version 自增（装配缓存键指纹跟随）。

        未提供字段保持原值；不存在 → None。
        """
        data = (
            payload if isinstance(payload, GenrePackUpdate) else GenrePackUpdate(**payload)
        )
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone()
            if row is None:
                return None
            current = q.pack_row_to_dict(row)

            payload_json = json.dumps(current["payload"], ensure_ascii=False)
            version = int(current["version"])
            if data.payload is not None:
                payload_json = json.dumps(data.payload, ensure_ascii=False)
                version += 1

            conn.execute(
                q.UPDATE_PACK_SQL,
                (
                    data.name if data.name is not None else current["name"],
                    data.genre_tag if data.genre_tag is not None else current["genre_tag"],
                    payload_json,
                    data.source_path if data.source_path is not None else current["source_path"],
                    version,
                    now_iso(),
                    pack_id,
                ),
            )
            conn.commit()
            updated = conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone()
        finally:
            conn.close()
        return q.pack_row_to_dict(updated) if updated is not None else None

    def count_bindings(self, pack_id: str) -> int:
        """该项目包被多少个项目绑定（DELETE 前的 409 判定口径）。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.COUNT_BINDINGS_SQL, (pack_id,)).fetchone()
        finally:
            conn.close()
        return int(row["n"]) if row is not None else 0

    def delete(self, pack_id: str) -> bool:
        """删除题材包；不存在 → False。

        **不做绑定检查**——调用方（router）先走 :meth:`count_bindings` 做 409 前置拦截；
        DDL 侧 ``ON DELETE SET NULL`` 是旁路删除的兜底。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(q.DELETE_PACK_SQL, (pack_id,))
            conn.commit()
        finally:
            conn.close()
        return cur.rowcount > 0

    # ------------------------------------------------------------- 绑定
    def bind(self, project_id: str, pack_id: str) -> tuple[str, dict[str, Any] | None]:
        """把题材包绑定到项目（单 slot，重复绑定=覆盖）；返回 ``(status, binding)``。

        status ∈ {:attr:`BindStatus.OK` / :attr:`BindStatus.PROJECT_NOT_FOUND` /
        :attr:`BindStatus.PACK_NOT_FOUND`}。
        """
        conn = get_connection(self.db_path)
        try:
            if conn.execute(q.PROJECT_EXISTS_SQL, (project_id,)).fetchone() is None:
                return BindStatus.PROJECT_NOT_FOUND, None
            if conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone() is None:
                return BindStatus.PACK_NOT_FOUND, None
            conn.execute(q.BIND_SQL, (pack_id, now_iso(), project_id))
            conn.commit()
        finally:
            conn.close()
        return BindStatus.OK, self.get_project_binding(project_id)

    def unbind(self, project_id: str) -> tuple[str, dict[str, Any] | None]:
        """解绑项目的题材包；返回 ``(status, binding)``。

        status ∈ {OK / PROJECT_NOT_FOUND / NOT_BOUND}：``NOT_BOUND`` 表示项目存在但
        本来就没绑（幂等语义，router 转 409 或 200 由调用方裁决）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.SELECT_PROJECT_BINDING_SQL, (project_id,)).fetchone()
            if row is None:
                return BindStatus.PROJECT_NOT_FOUND, None
            if not row["pack_id"]:
                return BindStatus.NOT_BOUND, None
            conn.execute(q.UNBIND_SQL, (now_iso(), project_id))
            conn.commit()
        finally:
            conn.close()
        return BindStatus.OK, self.get_project_binding(project_id)

    def get_project_binding(self, project_id: str) -> dict[str, Any] | None:
        """项目当前题材包绑定；项目不存在 → None。

        返回 ``{"project_id", "pack_id", "bound", "pack"}``：``pack`` 为题材包全文
        （未绑定 / pack 行缺失 → None）。单 slot 语义：``pack_id`` 至多一个。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.SELECT_PROJECT_BINDING_SQL, (project_id,)).fetchone()
            if row is None:
                return None
            pack_id = row["pack_id"]
            pack_row = (
                conn.execute(q.GET_PACK_SQL, (pack_id,)).fetchone() if pack_id else None
            )
        finally:
            conn.close()
        return {
            "project_id": project_id,
            "pack_id": pack_id,
            "bound": bool(pack_id),
            "pack": q.pack_row_to_dict(pack_row) if pack_row is not None else None,
        }
