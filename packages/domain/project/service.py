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

题材库 P1b（genre_pack_id 读侧暴露）：
- ``projects.genre_pack_id`` 可空列（迁移 0025）由绑定端点写（见
  ``packages/core/api/routers/genre.py``）；本服务只保证所有返回行都带
  ``genre_pack_id`` 键（极老库缺列 → None），供 Project 响应模型直接消费。
  绑定 / 解绑**不**经本服务（单一属主：genre 路由 + GenrePackService）。

项目写作圣经（writing_bible，迁移 0029）：
- 读写都归一为 ``str | None``：空白串与显式 null 同义（落 NULL），非空文本去首尾
  空白后原样存。DB 中只存在 NULL / 非空文本两种形态——避免 "" 与 NULL 在装配缓存
  键指纹里分裂成两种「都无圣经」的键形态。
- 本服务只做搬运与归一，**不做**与运行期 ``author_intent`` 的拼接（拼接单点在
  ``packages.core.context_engine.builders_common.resolve_author_intent``）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.versioning import (
    bump_project_state_version as _core_bump_project_state_version,
)

from .models import ProjectCreate, ProjectUpdate

#: 这些 ``projects`` 列**进 director 装配 payload**：``name`` / ``genre`` / ``premise``
#: → payload 的 ``project`` 段；``writing_bible`` → ``author_intent.raw``（经
#: ``resolve_author_intent`` 拼接）；``genre_pack_id`` → payload 的 ``genre_pack`` 段；
#: ``foreshadow_overdue_chapters`` → ``open_foreshadow_list`` 的 ``overdue`` 判定。
#: ⇒ 改它们要推 ``story_states.state_version``（见 :func:`bump_project_state_version`）。
#: **刻意不含** ``fast_mode`` / ``status`` / ``target_words`` / ``word_band_json``：
#: 它们不进 director payload（``word_band_json`` 是 writer 侧的 ``wb_fp`` 维度）⇒
#: 对它们推 version = 无谓的全局 miss（过度失效）。
_PROJECT_PAYLOAD_COLUMNS: frozenset[str] = frozenset({
    "name",
    "genre",
    "premise",
    "writing_bible",
    "genre_pack_id",
    "foreshadow_overdue_chapters",
})


# ---------------------------------------------------------------------------
# 写路径推 ``story_states.state_version``（装配缓存失效主线）
# ---------------------------------------------------------------------------
#
# **为什么需要**（AGENTS 硬规则 2 的根治条件②，2026-10-23 第二轮审查 M-1 / D-7）
# -------------------------------------------------------------------------
# 装配缓存键以 ``story_states.state_version`` 为主线维度；而 ``hooks`` /
# ``narrative_debts`` / ``plot_events`` / ``branches`` / ``projects`` 的部分列 /
# ``chapters.title`` 这些**进 payload 的权威输入**，其写路径只 ``commit()``、**不推**
# ``state_version`` ⇒ **只补键维度是治标**（键维度一旦漏了别的输入，照样脏命中）。
# ⇒ 写路径必须同时推 ``state_version``：**改数据 ⇒ version 变 ⇒ 必然 miss**。
#
# **沿用仓内既有写法**（不自造机制）
# ---------------------------------
# 与 commit 流程（``packages/core/story_state/commits.py::commit_delta`` →
# ``snapshot.materialize_snapshot``）**逐字同款**：读最新快照行 → 新版本 = 旧版本 + 1
# → 用 :func:`materialize_snapshot` 落一行新快照。差别只有两点，且都是「不污染 commit
# 审计表」的必要让步：
# - **commit_id 复用上一行的 commit_id**（``story_states.commit_id`` 是 NOT NULL +
#   FK → ``commits`` ⇒ 不能凭空造一个 commit 行污染审计表）；
# - **snapshot_json 沿用上一份内容**（本函数只推进版本号，**不改** Canonical State；
#   DB 实表 → 快照的权威重建仍归 commit 流程的
#   ``rebuild_snapshot_collections_from_db``）。
#
# **边界**（如实登记）
# ------------------
# - 项目**还没有 story_states 行**（从未 commit 过）⇒ **不造快照行**，返回 ``None``
#   （该路径下 ``state_version`` 保持不变）。
# - 快照 JSON 损坏 ⇒ **不造行**（绝不拿空 dict 覆盖 Canonical State）。
# - 必须在**写事务内**调用（与被改的行同一连接/同一事务）⇒ 写失败则 version 一起回滚。
#
# **归属说明**：本函数是**转发**——**唯一实现**已于 2026-10-24 迁至
# ``packages/core/story_state/versioning.py::bump_project_state_version``
# （core 层归属地：``branches`` / ``simulation`` 的写路径在 core 层，不得 import
# domain ⇒ 唯一实现必须落在 core）。本函数保留同名公开 API，既有 import 与突变
# 规格锚点（``LEDGER-SV-3`` 等）零变化。


def bump_project_state_version(conn: sqlite3.Connection, project_id: str) -> int | None:
    """在**当前事务内**推进该项目的 ``story_states.state_version``。

    返回新的 ``state_version``；**不推进**时返回 ``None``（项目尚无快照行 /
    快照 JSON 损坏 ⇒ 不造快照行）。

    ⚠️ 调用方必须在**写事务内**调用（本函数不 commit、不开连接）⇒ 业务写失败时
    版本推进一起回滚 ⇒ 不会出现「数据没改但版本涨了」。

    实现见 :func:`packages.core.story_state.versioning.bump_project_state_version`
    （core 层唯一实现；本函数只做转发，行为逐字一致）。
    """
    return _core_bump_project_state_version(conn, project_id)


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


def _coerce_genre_pack_id(raw: object) -> str | None:
    """把 ``projects.genre_pack_id`` 列值归一为 str | None（题材库 P1b 读侧暴露）。

    - None / 空串 → None；
    - 字符串 → 去空白后返回（空 → None）；
    - 其它类型（脏数据）→ None（防御性，不炸 Project 响应）。
    """
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    return None


def _coerce_writing_bible(raw: object) -> str | None:
    """把 ``projects.writing_bible`` 列值归一为 str | None（迁移 0029）。

    - None / 空白串 → None（「无圣经」在 DB 与读侧只有一个表示：NULL）；
    - 字符串 → 去首尾空白后返回（内部换行 / 段落原样保留）；
    - 其它类型（脏数据）→ None（防御性，不炸 Project 响应）。

    写侧同口径（见 :meth:`ProjectService.update` / ``create``）：空白串落 NULL，
    避免 ""（无圣经）与 None 在装配缓存键指纹里分裂成两种「都无圣经」的形态。
    """
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    return None


def _coerce_fast_mode(raw: object) -> bool | None:
    """把 ``projects.fast_mode`` 列值归一为 bool | None（迁移 0031）。

    三态保持：``None``（未设置 / 极老库缺列）≠ ``False``（作者显式关闭）。
    两者在 DB 与读侧都必须是可区分的形态——否则将来改默认值时，无法判断某项目的
    0 是「作者明确关的」还是「从来没这回事」。解析时 None 才回落 env 与默认值。

    - None → None；
    - int 0/1（sqlite INTEGER 的真值形态）→ bool；
    - bool → 原样；
    - 其它类型（脏数据）→ None（防御性，不炸 Project 响应）。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
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
            # 题材库 P1b：新建项目恒未绑定（绑定走 genre 路由）；显式置键供响应模型消费。
            "genre_pack_id": None,
            # 项目写作圣经（0029）：空白串归一为 None（DB 里只有 NULL / 非空文本两种形态）。
            "writing_bible": _coerce_writing_bible(
                getattr(payload, "writing_bible", None)
            ),
            # 快通道（0031）：None=未设置（解析时回落 env/默认关），True/False=作者显式选择。
            "fast_mode": _coerce_fast_mode(getattr(payload, "fast_mode", None)),
            "created_at": now,
            "updated_at": now,
        }
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, name, premise, genre, target_words, status,
                     foreshadow_overdue_chapters, word_band_json, writing_bible,
                     fast_mode, created_at, updated_at)
                VALUES
                    (:project_id, :name, :premise, :genre, :target_words, :status,
                     :foreshadow_overdue_chapters, :word_band_json, :writing_bible,
                     :fast_mode, :created_at, :updated_at)
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
        # 题材库 P1b：genre_pack_id 归一（极老库缺列 → None）
        d["genre_pack_id"] = _coerce_genre_pack_id(d.get("genre_pack_id"))
        # 项目写作圣经（0029）：空白串 / 缺列 → None
        d["writing_bible"] = _coerce_writing_bible(d.get("writing_bible"))
        # 快通道（0031）：缺列 / NULL → None（未设置，与显式 False 可区分）
        d["fast_mode"] = _coerce_fast_mode(d.get("fast_mode"))
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
            d["genre_pack_id"] = _coerce_genre_pack_id(d.get("genre_pack_id"))
            d["writing_bible"] = _coerce_writing_bible(d.get("writing_bible"))
            d["fast_mode"] = _coerce_fast_mode(d.get("fast_mode"))
            out.append(d)
        return out

    # ------------------------------------------------------------------- update
    def update(self, project_id: str, payload: ProjectUpdate) -> dict | None:
        """部分更新；只更新 payload 中显式提供的字段。

        V3.7：``word_band`` 字段语义——
        - 字段未在 ``model_fields_set`` → 不动 DB
        - ``word_band=None``（显式 null）→ 置 NULL 清除覆盖
        - ``word_band=dict``（显式对象）→ json.dumps 后存 ``word_band_json``

        2026-09-18（0029）：``writing_bible`` 字段语义——
        - 字段未在 ``model_fields_set`` → 不动 DB
        - ``writing_bible=None``（显式 null）或空白串 → 置 NULL 清除圣经
        - 含内容字符串 → 去首尾空白后原样存 ``writing_bible``

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
            elif fname == "writing_bible":
                # 归一：空白串与显式 null 同义（清除）；非空文本去首尾空白后原样存。
                fields["writing_bible"] = _coerce_writing_bible(
                    getattr(payload, "writing_bible")
                )
            elif fname == "fast_mode":
                # 快通道（0031）：True→1 / False→0 / 显式 None→NULL（清除覆盖）。
                # 不在这里回落默认值——「清除」必须真的落 NULL，否则无法把
                # 「作者显式关」与「作者从未设置」区分开。
                fields["fast_mode"] = _coerce_fast_mode(getattr(payload, "fast_mode"))
            else:
                fields[fname] = getattr(payload, fname)

        if not fields:
            # 没有字段要更新：直接返回当前行（None 表示不存在）
            return self.get(project_id)

        fields["updated_at"] = now_iso()

        # 2026-10-23（M-1 / D-7）：本 PATCH 若改到**进 director 装配 payload 的列**
        # ⇒ 同事务内推 ``story_states.state_version``（装配缓存失效主线，见
        # :func:`bump_project_state_version`）。刻意**只**对 payload 消费列推：
        # ``fast_mode`` / ``status`` / ``target_words`` / ``word_band_json`` 不进
        # director payload（``word_band_json`` 是 writer 侧的 ``wb_fp`` 维度）⇒
        # 对它们推 version 会造成无谓的全局 miss（过度失效）。
        push_state_version = bool(_PROJECT_PAYLOAD_COLUMNS & set(fields))

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
            if push_state_version:
                bump_project_state_version(conn, project_id)
            conn.commit()
            cur = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        d = dict(row)
        d["word_band"] = _coerce_word_band(d.get("word_band_json"))
        d["genre_pack_id"] = _coerce_genre_pack_id(d.get("genre_pack_id"))
        d["writing_bible"] = _coerce_writing_bible(d.get("writing_bible"))
        d["fast_mode"] = _coerce_fast_mode(d.get("fast_mode"))
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


__all__ = [
    "ProjectService",
    "_coerce_word_band",
    "_coerce_genre_pack_id",
    "_coerce_writing_bible",
    "_coerce_fast_mode",
]
