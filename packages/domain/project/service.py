"""ProjectService（Sprint 1 + V3.7 字数带覆盖）。

职责：projects 表 CRUD + 业务规则。
对齐 ``database/migrations/0001_init.sql`` 中 ``projects`` 表结构（line 34-44）；
V3.7 通过 ``0023_project_word_band.sql`` 增加可空列 ``word_band_json TEXT``，
本服务负责其序列化写入与读路径反序列化。

设计要点（与并行代理共用的统一模式）：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- 主键由 ``new_id("prj")`` 生成；时间戳由 ``now_iso()`` 生成。
- 列表端点默认过滤 ``status = 'ARCHIVED'``，可选参数 ``include_archived=True`` 时不过滤。
- 删除策略：当存在子记录（characters / chapters 等）时拒绝删除，由 router 转 409。
- 查询不存在 → 返回 ``None``，由 router 转 404；CHECK/FK 违反 → 抛出 ``sqlite3.IntegrityError``，
  router 转 422 并带 ``detail``。

V3.7 字数带覆盖（word_band_json）：
- 写入：ProjectUpdate.word_band 显式提供（dict）→ json.dumps 存；显式 None → 置 NULL。
- 读取：所有读路径（get / list / update 返回行 / create 返回行）通过
  :func:`_coerce_word_band` 把 ``word_band_json`` 解析成 ``word_band`` 字段。
  非法 JSON 视为 None（防御性 + 不炸），与备份模块对损坏数据的容忍策略对齐。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .models import ProjectCreate, ProjectUpdate


def _coerce_word_band(raw: object) -> dict | None:
    """把 projects.word_band_json 列值（TEXT/None）解析成 dict。

    - None / 空串 → None。
    - 合法 JSON 对象（dict）→ 原样返回。
    - 非 dict（数组 / 标量）/ 非法 JSON → None（防御性，不抛）。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    # sqlite3.Row / dict 形态（来自 SELECT * 已在 service 内转为 dict）
    if isinstance(raw, dict):
        return raw
    return None


class ProjectService:
    """项目领域服务（projects 表 CRUD）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------------ create
    def create(self, payload: ProjectCreate) -> dict:
        """创建项目，返回完整行 dict（含 project_id / created_at / updated_at）。"""
        now = now_iso()
        # Sprint 15：foreshadow_overdue_chapters 默认 30（与 DDL DEFAULT 对齐）；None 也走默认。
        from .models import _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS

        overdue = (
            int(payload.foreshadow_overdue_chapters)
            if payload.foreshadow_overdue_chapters is not None
            else _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS
        )
        row = {
            "project_id": new_id("prj"),
            "name": payload.name,
            "premise": payload.premise,
            "genre": payload.genre,
            "target_words": payload.target_words,
            "status": "ACTIVE",
            "foreshadow_overdue_chapters": overdue,
            # getattr 兜底：测试与内部调用方存在鸭子类型载荷（无 word_band 属性）
            "word_band_json": (
                json.dumps(payload.word_band, ensure_ascii=False)
                if getattr(payload, "word_band", None)
                else None
            ),
            "word_band": getattr(payload, "word_band", None),
            "created_at": now,
            "updated_at": now,
        }
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, name, premise, genre, target_words, status,
                     foreshadow_overdue_chapters, word_band_json,
                     created_at, updated_at)
                VALUES
                    (:project_id, :name, :premise, :genre, :target_words, :status,
                     :foreshadow_overdue_chapters, :word_band_json,
                     :created_at, :updated_at)
                """,
                row,
            )
            conn.commit()
        finally:
            conn.close()
        return row

    # --------------------------------------------------------------------- get
    def get(self, project_id: str) -> dict | None:
        """按主键查询；不存在返回 None。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        d = dict(row)
        # V3.7：word_band_json → word_band（dict 暴露给上层）
        d["word_band"] = _coerce_word_band(d.get("word_band_json"))
        return d

    # -------------------------------------------------------------------- list
    def list(self, include_archived: bool = False) -> list[dict]:
        """列出项目，按 created_at 升序（创建顺序）。

        - 默认排除 ``status = 'ARCHIVED'``（与前端「归档后不再出现在主列表」文案一致）。
        - ``include_archived=True`` 不过滤，全量返回（含归档）。
        """
        conn = get_connection(self.db_path)
        try:
            if include_archived:
                cur = conn.execute(
                    "SELECT * FROM projects ORDER BY created_at ASC, project_id ASC"
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM projects "
                    "WHERE status != 'ARCHIVED' "
                    "ORDER BY created_at ASC, project_id ASC"
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            d["word_band"] = _coerce_word_band(d.get("word_band_json"))
            out.append(d)
        return out

    # ------------------------------------------------------------------- update
    def update(self, project_id: str, payload: ProjectUpdate) -> dict | None:
        """部分更新；只更新 payload 中显式提供的字段。

        V3.7：``word_band`` 字段语义——
        - 字段未在 ``model_fields_set`` → 不动 DB
        - ``word_band=None``（显式 null）→ 置 NULL 清除覆盖
        - ``word_band=dict``（显式对象）→ json.dumps 后存 ``word_band_json``

        返回更新后的完整行；不存在返回 None。
        """
        # 区分「未提供」与「显式 None」：model_fields_set 是 Pydantic v2 的权威口径。
        fields_set = payload.model_fields_set

        fields: dict = {}
        for fname in fields_set:
            if fname == "word_band":
                wb = getattr(payload, "word_band")
                if wb is None:
                    # 显式清除：word_band_json 置 NULL
                    fields["word_band_json"] = None
                else:
                    # dict 序列化为 JSON 存 word_band_json
                    if not isinstance(wb, dict):
                        # router 层 model 已守住 dict|None，service 二次防御
                        raise ValueError("word_band must be dict or None")
                    fields["word_band_json"] = json.dumps(wb, ensure_ascii=False)
            else:
                fields[fname] = getattr(payload, fname)

        if not fields:
            # 没有字段要更新：直接返回当前行（None 表示不存在）
            return self.get(project_id)

        fields["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["project_id"] = project_id

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                f"UPDATE projects SET {set_clause} WHERE project_id = :project_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        d = dict(row)
        d["word_band"] = _coerce_word_band(d.get("word_band_json"))
        return d

    # ------------------------------------------------------------------- delete
    def delete(self, project_id: str) -> bool:
        """删除项目。

        - 不存在：返回 False。
        - 存在子记录（characters / chapters / scenes / drafts 等通过 FK 引用
          project_id 的任意行）：sqlite3.IntegrityError（FOREIGN KEY constraint failed），
          由调用方映射为 409 Conflict。Service 不吞此异常。
        - 成功：返回 True。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------ children count (helper)
    def has_children(self, project_id: str) -> bool:
        """是否存在子记录（characters / chapters）。

        供 router 在 DELETE 时把 IntegrityError 翻译成更可读的 409 信息。
        当前 Sprint 实现：检查 characters 与 chapters 两张直接 FK 到 projects 的表。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM characters WHERE project_id = ?) +
                    (SELECT COUNT(*) FROM chapters   WHERE project_id = ?) AS cnt
                """,
                (project_id, project_id),
            ).fetchone()
        finally:
            conn.close()
        return bool(row["cnt"] > 0)


__all__ = ["ProjectService", "_coerce_word_band"]
