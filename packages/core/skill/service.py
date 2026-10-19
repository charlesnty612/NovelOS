"""SkillService —— skill CRUD / 项目多 slot 绑定 / payload 校验。

职责：
- payload 校验：``docs/state-model/schemas/skill.schema.json``（Draft 2020-12）
  是**唯一权威**；错误串格式 ``"[schema] <path>: <message>"``（与
  ``packages/core/genre/service.py`` 同款），由 router 转 422
  （``detail={"errors": [...]}``）。
- CRUD：create / list / get / update（提供 payload 时 version 自增）/ delete。
- 绑定：bind（项目**多 slot 有序**，覆盖式）/ unbind / list_project_skills；
  delete 前由 router 调 :meth:`count_bindings` 做 409 前置拦截
  （DDL 侧 ``ON DELETE CASCADE`` 兜底）。

设计要点：
- **绑定用关联表**（``project_skills``）而不是 JSON 列：① 多 slot 有序；
  ② 避免「JSON 列脏数据 ⇒ 读侧退空值 ⇒ 缓存键形态与主路径不同形 ⇒ 脏命中」
  这一族缺陷。
- **payload 变更 ⇒ version 自增** ⇒ 装配缓存键的 ``skills_fp`` 必 miss
  （指纹含 id **与** version；只按 id 算会脏命中）。
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
    SKILL_SCHEMA_PATH,
    SkillCreate,
    SkillUpdate,
)

__all__ = ["BindStatus", "SkillService", "validate_payload"]


class BindStatus:
    """绑定 / 解绑结果码（router 据此映射 404 / 409 / 200）。"""

    OK = "ok"
    PROJECT_NOT_FOUND = "project_not_found"
    SKILL_NOT_FOUND = "skill_not_found"
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
    """按 skill schema 校验 payload，返回错误列表（空 = 通过）。

    - 非 dict（list / str / None）→ 单条 ``[schema] <root>: ...`` 错误；
    - 错误按 ``absolute_path`` 排序，格式 ``"[schema] <path>: <message>"``。
    """
    if not isinstance(payload, dict):
        return ["[schema] <root>: payload 必须是 JSON 对象（dict）"]
    sp = Path(schema_path) if schema_path is not None else SKILL_SCHEMA_PATH
    validator = _load_validator(str(sp))
    errs: list[str] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        path_str = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errs.append(f"[schema] {path_str}: {err.message}")
    return errs


class SkillService:
    """``skills`` 表 CRUD + ``project_skills`` 多 slot 有序绑定。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------- 校验
    @staticmethod
    def validate_payload(payload: Any, schema_path: Path | str | None = None) -> list[str]:
        """payload 校验（薄封装 :func:`validate_payload`，便于调用方注入 schema 路径）。"""
        return validate_payload(payload, schema_path)

    # ------------------------------------------------------------- CRUD
    def create(
        self, payload: SkillCreate | dict[str, Any],
    ) -> dict[str, Any]:
        """创建 skill；``skill_id`` 显式给出时用其原值（内容仓 slug）。

        返回完整 dict（``payload`` 已解析）。``skill_id`` 冲突 → sqlite3.IntegrityError
        透传给 router（转 422）。
        """
        data = payload if isinstance(payload, SkillCreate) else SkillCreate(**payload)
        skill_id = (data.skill_id or "").strip() or new_id("skl")
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                q.INSERT_SKILL_SQL,
                (
                    skill_id,
                    data.name,
                    data.description,
                    1,
                    json.dumps(data.payload, ensure_ascii=False),
                    data.source_path,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(q.GET_SKILL_SQL, (skill_id,)).fetchone()
        finally:
            conn.close()
        return q.skill_row_to_dict(row) if row is not None else {}

    def list_skills(self, *, name: str | None = None) -> list[dict[str, Any]]:
        """列出 skill 摘要（``created_at DESC``；可选按显示名精确过滤）。"""
        conn = get_connection(self.db_path)
        try:
            if name:
                rows = conn.execute(q.LIST_SKILLS_BY_NAME_SQL, (name,)).fetchall()
            else:
                rows = conn.execute(q.LIST_SKILLS_SQL).fetchall()
        finally:
            conn.close()
        return [q.skill_row_to_summary(r) for r in rows]

    def get(self, skill_id: str) -> dict[str, Any] | None:
        """取单个 skill 全文；不存在 → None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.GET_SKILL_SQL, (skill_id,)).fetchone()
        finally:
            conn.close()
        return q.skill_row_to_dict(row) if row is not None else None

    def update(
        self, skill_id: str, payload: SkillUpdate | dict[str, Any],
    ) -> dict[str, Any] | None:
        """部分更新；提供 ``payload`` 时 version 自增（装配缓存键指纹跟随）。

        未提供字段保持原值；不存在 → None。
        """
        data = payload if isinstance(payload, SkillUpdate) else SkillUpdate(**payload)
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.GET_SKILL_SQL, (skill_id,)).fetchone()
            if row is None:
                return None
            current = q.skill_row_to_dict(row)

            payload_json = json.dumps(current["payload"], ensure_ascii=False)
            version = int(current["version"])
            if data.payload is not None:
                payload_json = json.dumps(data.payload, ensure_ascii=False)
                version += 1

            conn.execute(
                q.UPDATE_SKILL_SQL,
                (
                    data.name if data.name is not None else current["name"],
                    data.description if data.description is not None else current["description"],
                    payload_json,
                    data.source_path if data.source_path is not None else current["source_path"],
                    version,
                    now_iso(),
                    skill_id,
                ),
            )
            conn.commit()
            updated = conn.execute(q.GET_SKILL_SQL, (skill_id,)).fetchone()
        finally:
            conn.close()
        return q.skill_row_to_dict(updated) if updated is not None else None

    def count_bindings(self, skill_id: str) -> int:
        """该 skill 被多少个项目绑定（DELETE 前的 409 判定口径）。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(q.COUNT_BINDINGS_SQL, (skill_id,)).fetchone()
        finally:
            conn.close()
        return int(row["n"]) if row is not None else 0

    def delete(self, skill_id: str) -> bool:
        """删除 skill；不存在 → False。

        **不做绑定检查**——调用方（router）先走 :meth:`count_bindings` 做 409 前置拦截；
        DDL 侧 ``ON DELETE CASCADE`` 是旁路删除的兜底。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(q.DELETE_SKILL_SQL, (skill_id,))
            conn.commit()
        finally:
            conn.close()
        return cur.rowcount > 0

    # ------------------------------------------------------------- 绑定
    def bind(
        self, project_id: str, skill_ids: list[str],
    ) -> tuple[str, list[dict[str, Any]] | None]:
        """把一组 skill 绑定到项目（**多 slot 有序**，覆盖式）；返回 ``(status, skills)``。

        - ``skill_ids`` 的**列表顺序**即注入顺序（写入 ``project_skills.ord``）；
        - 重复的 skill_id 去重（保留首次出现的位置）；
        - 绑定前先清掉该项目的既有绑定（覆盖式）。

        status ∈ {:attr:`BindStatus.OK` / :attr:`BindStatus.PROJECT_NOT_FOUND` /
        :attr:`BindStatus.SKILL_NOT_FOUND`}。``SKILL_NOT_FOUND`` 时**不写入任何绑定**
        （整体失败，不留半套绑定）。
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for sid in skill_ids:
            sid = (sid or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            ordered.append(sid)

        conn = get_connection(self.db_path)
        try:
            if conn.execute(q.PROJECT_EXISTS_SQL, (project_id,)).fetchone() is None:
                return BindStatus.PROJECT_NOT_FOUND, None
            for sid in ordered:
                if conn.execute(q.SKILL_EXISTS_SQL, (sid,)).fetchone() is None:
                    return BindStatus.SKILL_NOT_FOUND, None
            conn.execute(q.DELETE_PROJECT_BINDINGS_SQL, (project_id,))
            for ord_idx, sid in enumerate(ordered):
                conn.execute(q.BIND_SQL, (project_id, sid, ord_idx))
            conn.commit()
        finally:
            conn.close()
        return BindStatus.OK, self.list_project_skills(project_id)

    def unbind(self, project_id: str, skill_id: str) -> tuple[str, list[dict[str, Any]] | None]:
        """解绑项目的某个 skill；返回 ``(status, skills)``。

        status ∈ {OK / PROJECT_NOT_FOUND / NOT_BOUND}：``NOT_BOUND`` 表示项目存在但
        本来就没绑这个 skill（幂等语义）。
        """
        conn = get_connection(self.db_path)
        try:
            if conn.execute(q.PROJECT_EXISTS_SQL, (project_id,)).fetchone() is None:
                return BindStatus.PROJECT_NOT_FOUND, None
            cur = conn.execute(q.UNBIND_SQL, (project_id, skill_id))
            conn.commit()
            removed = cur.rowcount > 0
        finally:
            conn.close()
        if not removed:
            return BindStatus.NOT_BOUND, None
        return BindStatus.OK, self.list_project_skills(project_id)

    def list_project_skills(self, project_id: str) -> list[dict[str, Any]]:
        """项目当前绑定的 skill 集合（**有序**：``ord`` 小者在前）+ 全文 payload。

        项目不存在 → 空列表（调用方按「未绑定」处理；存在性由端点另行判定）。
        """
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(q.LIST_PROJECT_SKILLS_SQL, (project_id,)).fetchall()
        finally:
            conn.close()
        return [q.skill_row_to_dict(r) for r in rows]
