"""ChapterService（Sprint 1 + Sprint 5 drafts 扩展）。

职责：chapters 表 CRUD + 状态机迁移约束 + 同项目内 number 唯一 +
Sprint 5 起的 chapter drafts 人工改稿能力（list / create）。
对齐 ``database/migrations/0001_init.sql`` 中 ``chapters`` 表结构（line 240-254）、
``drafts`` 表结构（line 270-280）。

设计要点：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭。
- ``plan_json`` 列：写入前 ``json.dumps(ensure_ascii=False)``，读出后 ``json.loads``；
  pydantic 模型对应字段为 ``dict``。
- 列表按 ``number ASC`` 排序（任务书给死）。
- 状态机迁移白名单见 ``models.ALLOWED_NEXT``；非法跳变抛出 ``ChapterTransitionError``，
  router 转 409。
- 同 project 内 ``number`` 重复：DB 层没有 UNIQUE 索引，由 Service 在 INSERT 前查重，
  重复则抛 ``ChapterNumberConflict``，router 转 409。CHECK 违反（status 枚举外值）则
  透传 ``sqlite3.IntegrityError``，router 转 422。
- ``create`` / ``update`` 中的 ``status`` 变更：create 默认 ``PLANNED``，update 走状态机。
- Sprint 5 drafts：
  - ``list_drafts(chapter_id)``：按 ``version DESC``；chapter 不存在返回 ``None``
    （让 router 转 404，与 ``ChapterService.get`` 一致）。
  - ``create_draft(chapter_id, content)``：仅当 chapter.status ∈ {DRAFTED, REVIEWED}
    时允许；其余状态抛 ``DraftStatusNotAllowed``（router 转 409，detail 携带当前 status）。
  - ``version`` 计算：``COALESCE(MAX(version), 0) + 1``，在 INSERT 同事务内执行，
    避免并发竞态下产生重复 version。
  - ``created_by`` 固定 ``"human"``；``prompt_version`` / ``model_id`` 写 NULL。
  - 「哪一版是被审 / 待核的那一版」不在本类解析：共享单点是
    :func:`packages.domain.chapter.draft_resolver.resolve_draft`
    （``(db_path, chapter_id, draft_version | None)``，``None`` = 最新一版）。
    ``list_drafts`` 只负责「列出全部版本」，不做取舍。
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

from ..project.service import bump_project_state_version
from .models import (
    ALLOWED_NEXT,
    ChapterCreate,
    ChapterUpdate,
)
from .review_state import last_review_ended_at

#: 本模块日志（2026-10-22 批次二十三：吞异常分诊 → B 类留痕；本层此前无 logger，
#: 引入的是仓内既有唯一约定 ``packages.core.logging_config.get_logger``）。
log = get_logger("novelos.domain.chapter.service")


class ChapterError(Exception):
    """Chapter Service 业务异常基类。"""


class ChapterNumberConflict(ChapterError):
    """同一 project 下 number 已存在。"""


class ChapterTransitionError(ChapterError):
    """状态机非法迁移。"""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"illegal chapter status transition {current!r} -> {target!r}")
        self.current = current
        self.target = target


class DraftStatusNotAllowed(ChapterError):
    """当前 chapter.status 不允许新增 draft（仅 DRAFTED / REVIEWED 允许）。

    Sprint 5 任务书给死：人工改稿只能在 chapter 已进入「已写 / 已审」状态时追加；
    PLANNED 阶段不允许手写（应在 chapter 推进到 DRAFTED 之后），COMMITTED/RELEASED
    阶段正文已锁定。
    """

    def __init__(self, current: str) -> None:
        super().__init__(
            f"draft creation not allowed when chapter status is {current!r} "
            f"(only 'DRAFTED' or 'REVIEWED' accepted)"
        )
        self.current = current


class RevisionNoteStatusNotAllowed(ChapterError):
    """当前 chapter.status 不允许写入/清除 revision_note（仅 PLANNED / DRAFTED /
    REVIEWED 允许；COMMITTED/RELEASED 视为正文已锁定，不再接受改稿意见）。

    Sprint 6 任务书给死：revision_note 必须有专用端点（``PATCH
    /api/chapters/{chapter_id}/revision-note``），不再走评审驳回路径。
    """

    def __init__(self, current: str) -> None:
        super().__init__(
            f"revision_note update not allowed when chapter status is {current!r} "
            f"(only 'PLANNED', 'DRAFTED' or 'REVIEWED' accepted)"
        )
        self.current = current


class DraftVersionConflict(ChapterError):
    """同 chapter 下 (chapter_id, version) 重复。

    Sprint 5 review F2：0002 迁移加了 ``idx_drafts_chapter_version`` 唯一索引，
    手工/并发 INSERT 触发 ``sqlite3.IntegrityError`` 时被本类吞下，由 router 转 409。
    """

    def __init__(self, version: int) -> None:
        super().__init__(
            f"draft version {version} already exists for this chapter"
        )
        self.version = version


class ChapterService:
    """章节领域服务（chapters 表 CRUD）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _dump_json(value: dict | None) -> str:
        return json.dumps(value or {}, ensure_ascii=False)

    #: ``plan_json`` 里由**后端**写入、前端编辑面通常看不到的门禁键。
    #:
    #: C-22（2026-11-05）：``PATCH /chapters/{id}`` 的 ``plan_json`` 曾是整体替换 ⇒
    #: 作者在「章节计划」里改一个字，就会把 ``gate_blocked``（阻断原因与 rule_ids）、
    #: ``revision_note``（门禁改稿建议）、``suggested_hashes``（评审驳回的建议）
    #: 一并静默清掉。这三个键的**写方全在后端**，所以只要请求体没显式提到它们，
    #: 一律保留 DB 现值。
    _PLAN_JSON_GATE_KEYS: tuple[str, ...] = (
        "revision_note",
        "gate_blocked",
        "suggested_hashes",
    )

    def _merge_plan_json(
        self, conn: sqlite3.Connection, chapter_id: str, incoming: dict | None
    ) -> str:
        """把请求体的 ``plan_json`` 与 DB 现值做**保留合并**，返回待写 JSON 串。

        - 请求体覆盖普通键（其余不在请求体里的普通键**仍然丢弃**，保持「整体替换」
          的可预期语义）。
        - ``_PLAN_JSON_GATE_KEYS`` 中**请求体未显式出现**的键，从 DB 现值补回。
        - 请求体显式给出（含显式 ``null``）⇒ 以请求体为准，不补。
        - 无现存行 / 现值不是 dict / JSON 损坏 ⇒ 视为空 plan（与
          ``update_revision_note`` 的降级口径一致）。
        """
        merged: dict = dict(incoming or {})
        missing = [k for k in self._PLAN_JSON_GATE_KEYS if k not in merged]
        if not missing:
            return self._dump_json(merged)

        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
        if row is None:
            return self._dump_json(merged)

        raw = row["plan_json"]
        if not raw:
            return self._dump_json(merged)
        try:
            current = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            current = None
        if not isinstance(current, dict):
            return self._dump_json(merged)
        for key in missing:
            if key in current:
                merged[key] = current[key]
        return self._dump_json(merged)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        # ``plan_json`` / ``outline_json`` / ``who_knows`` 读路径一律走共享解码助手
        # （``packages.core.story_state.write_helpers``）：一行**脏 JSON**（非法文本或
        # 形状不符）不得让整个列表端点 500——这正是 plot 侧 2026-10-15 同类缺陷的处置。
        # ⚠️ 2026-10-16 批次九：``plan_json`` 原先是裸 ``json.loads``（同一函数内
        # outline_json / who_knows 早已兜底，唯它漏了）。实测一行脏 plan_json 让
        # ``GET /projects/{pid}/chapters`` 与 ``GET /chapters/{id}`` 双双 500，干净行陪葬。
        d["plan_json"] = _decode_json_field(d.get("plan_json"), fallback={})
        # outline_json（0028 策展大纲槽）：NULL / 空串 / 脏 → None（= 未策展）
        d["outline_json"] = _decode_json_field(d.get("outline_json"), fallback=None)
        # who_knows：JSON 字符串数组；形状不符（对象 / 标量）→ None
        d["who_knows"] = _decode_json_field(
            d.get("who_knows"), fallback=None, allowed=(list,)
        )
        return d

    # ------------------------------------------------------------------ create
    def create(self, project_id: str, payload: ChapterCreate) -> dict:
        """创建章节。

        - 同 project 内 ``number`` 重复 → ``ChapterNumberConflict``（409）。
        - status 默认 ``PLANNED``（不支持创建时直接指定 status；状态机迁移从 PLANNED 起步）。
        """
        now = now_iso()
        plan_json = self._dump_json(payload.plan_json)

        conn = get_connection(self.db_path)
        try:
            # 1) number 唯一性预检（同事务内，避免并发竞态时产生重复行）
            cur = conn.execute(
                "SELECT 1 FROM chapters WHERE project_id = ? AND number = ?",
                (project_id, payload.number),
            )
            if cur.fetchone() is not None:
                raise ChapterNumberConflict(
                    f"chapter number {payload.number} already exists in project {project_id}"
                )

            row = {
                "chapter_id": new_id("ch"),
                "project_id": project_id,
                "number": payload.number,
                "title": payload.title,
                "plan_json": plan_json,
                "status": "PLANNED",
                "visibility": "VISIBLE",
                "who_knows": None,
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO chapters
                    (chapter_id, project_id, number, title, plan_json,
                     status, visibility, who_knows, created_at, updated_at)
                VALUES
                    (:chapter_id, :project_id, :number, :title, :plan_json,
                     :status, :visibility, :who_knows, :created_at, :updated_at)
                """,
                row,
            )
            conn.commit()
        finally:
            conn.close()
        # 走 get() 路径再读一次以保证 plan_json / who_knows 等列反序列化一致
        return self.get(row["chapter_id"])  # type: ignore[return-value]

    # --------------------------------------------------------------------- get
    def get(self, chapter_id: str) -> dict | None:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------- list by proj
    def list_by_project(self, project_id: str) -> list[dict]:
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT * FROM chapters WHERE project_id = ? ORDER BY number ASC",
                (project_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------- update
    def update(self, chapter_id: str, payload: ChapterUpdate) -> dict | None:
        """部分更新。

        - 不存在 → None（router 转 404）。
        - ``plan_json`` 提供时**保留合并**（C-22，2026-11-05）：请求体内的键覆盖现值，
          但 ``revision_note`` / ``gate_blocked`` / ``suggested_hashes`` 三个**门禁键**
          在请求体未显式提供时由 DB 现值保留。
          理由：此前是「整体替换」——前端编辑计划文本时手头只有 plan_json 的可见部分，
          PATCH 会把后端写下的门禁标记（阻断原因 / 改稿建议 / 建议哈希）静默清掉。
          请求体**显式**带这三个键时以请求体为准（含显式置 ``null``）。
        - ``status`` 提供时校验状态机白名单，非法跳变 → ``ChapterTransitionError``。
        """
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            return self.get(chapter_id)

        # 状态机预检（需要先读 current status）
        if "status" in fields:
            current = self.get(chapter_id)
            if current is None:
                return None
            target = fields["status"]
            if target not in ALLOWED_NEXT.get(current["status"], set()):
                raise ChapterTransitionError(current["status"], target)
            # COMMITTED→REVIEWED 虽在 ALLOWED_NEXT 白名单内，但保留给
            # ``reopen_for_repair`` 专用（返修入口，router 侧另有活动 run 守卫）。
            # 通用 PATCH update 不承诺该语义——放行就是绕开守卫的旁路（先例：
            # create_draft 对白名单边也做绕白名单直写/拦截，见本文件降级写法注释）。
            if target == "REVIEWED" and current["status"] == "COMMITTED":
                raise ChapterTransitionError(current["status"], target)

        conn = get_connection(self.db_path)
        try:
            # 处理 plan_json（read-modify-write 保留门禁键；同一连接内读，避免
            # 「读到旧值 / 写到新连接」的中间态）。
            if "plan_json" in fields:
                fields["plan_json"] = self._merge_plan_json(
                    conn, chapter_id, fields["plan_json"]
                )
            # outline_json：None（显式清空）→ 写 NULL；dict → JSON 串
            if "outline_json" in fields:
                fields["outline_json"] = (
                    None if fields["outline_json"] is None
                    else self._dump_json(fields["outline_json"])
                )

            fields["updated_at"] = now_iso()

            # 2026-10-23（M-1 / D-7）：改了 ``title`` ⇒ 同事务内推
            # ``story_states.state_version``（装配缓存失效主线）——``chapters.title`` 进
            # director payload 的 ``chapter.title`` 段，而本写路径**只 commit()、不推
            # version** ⇒ 缺该推进时「改标题后重跑同一章」会命中旧装配（planner 被告知
            # 旧标题，零告警）。
            # ⚠️ **刻意只对 title 推**：``plan_json`` / ``outline_json`` 各有独立键维度
            # （``plan_fp`` / ``outline_fp``）⇒ 推它们是重复失效；而 ``status`` 是高频
            # 状态机迁移（每章若干次）⇒ 对它推 version = 每次都重装（过度失效）。
            push_state_version = "title" in fields

            set_clause = ", ".join(f"{k} = :{k}" for k in fields)
            params: dict = dict(fields)
            params["chapter_id"] = chapter_id

            cur = conn.execute(
                f"UPDATE chapters SET {set_clause} WHERE chapter_id = :chapter_id",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            if push_state_version:
                prow = conn.execute(
                    "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
                ).fetchone()
                if prow is not None:
                    bump_project_state_version(conn, prow["project_id"])
            conn.commit()
            cur = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------------- delete
    def delete(self, chapter_id: str) -> bool:
        """删除章节；不存在返回 False。

        存在 scenes / drafts 子记录时抛 ``sqlite3.IntegrityError``（FOREIGN KEY 约束），
        Service 不吞此异常，由 router 转 409。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (chapter_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ---------------------------------------------------- V1.5 越层整改
    # 轻量查询：仅取 project_id；不存在 → None。被 workflows router 的
    # _check_chapter 与 context-preview 端点调用，避免路由层直接写 SQL。
    # ------------------------------------------------------------ get_project_id
    def get_project_id(self, chapter_id: str) -> str | None:
        """返回该 chapter 所属 project_id；chapter 不存在 → None。

        比 ``get`` 更轻量——只 SELECT project_id，避免反序列化 plan_json / who_knows。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return row["project_id"] if row else None

    # ------------------------------------------------------------ last review
    def get_last_review_completed_at(self, chapter_id: str) -> str | None:
        """返回指定 chapter 最近一次 COMPLETED 状态 chapter-review run 的 ``ended_at``。

        用于章节详情页版本列表「未审」角标：与最新 draft.created_at 比较，
        凡是 created_at 严格大于该时间戳的 draft 都属于「审校后又改稿 / 续写」，
        在 UI 上提示用户该版本尚未经过审校。

        SQL 与 ``packages.core.api.routers.workflows._latest_completed_review_end``
        同款（避免路由间横向依赖，在领域层独立查询）：
        ``JOIN workflows wf ON wf.workflow_id = wr.workflow_id``，
        ``wf.name = 'chapter-review'``，``wr.status = 'COMPLETED'``，
        按 ``ended_at`` DESC 取 1。

        返回：
        - 最近一次 COMPLETED review 的 ``ended_at``（ISO 字符串，
          与 ``workflows.ended_at`` 写入路径一致，可直接字典序比较）；
        - 无 COMPLETED review run → ``None``（UI 视为「该章节从未审过，所有
          draft 都属于未审，与旧版行为一致」）。
        """
        # REF-04（2026-11-08）：查询口径单点在 domain/chapter/review_state.py
        # （本方法此前是「三份逐字相同 SQL」中的一份）。
        conn = get_connection(self.db_path)
        try:
            return last_review_ended_at(conn, chapter_id)
        finally:
            conn.close()

    # ============================================================== Sprint 5
    # drafts：人工改稿能力（task A1）。
    # ----------------------------------------------------------------------

    # 仅 DRAFTED / REVIEWED 状态允许新增 draft（任务书给死）。
    _DRAFT_ALLOWED_STATUS: frozenset[str] = frozenset({"DRAFTED", "REVIEWED"})

    def list_drafts(self, chapter_id: str) -> list[dict] | None:
        """返回该 chapter 下全部 draft，按 ``version`` 降序。

        - chapter 不存在 → ``None``（router 转 404）。
        - 存在但无 draft → ``[]``。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT 1 FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            if cur.fetchone() is None:
                return None
            cur = conn.execute(
                "SELECT * FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                (chapter_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def create_draft(
        self,
        chapter_id: str,
        content: str,
        *,
        created_by: str = "human",
        prompt_version: str | None = None,
        model_id: str | None = None,
    ) -> dict | None:
        """在 chapter 下新增一份 draft（人工改稿入口）。

        - chapter 不存在 → ``None``（router 转 404，与 ``ChapterService.get`` 风格一致）。
        - chapter.status ∉ {DRAFTED, REVIEWED} → ``DraftStatusNotAllowed``（router 转 409）。
        - ``version`` = ``COALESCE(MAX(version), 0) + 1``，在 INSERT 同事务内执行，
          避免并发竞态下产生重复 version。
        - ``created_by`` 缺省 ``"human"``（人工改稿）；AI 侧入口（continuation 采纳）
          必须显式传 ``agent:*`` 出处 —— B-5（2026-11-08）：这类正文是模型产出的，
          记成 human 会污染 Q8 人机占比与 q8-export。
        - ``prompt_version`` / ``model_id`` 缺省 ``None``；AI 侧入口按实际调用填写。
        - ``draft_id`` 用 ``new_id("dr")``；``created_at`` 用 ``now_iso()``。
        - 若原 status 为 REVIEWED，INSERT 同事务内将 chapters.status 降级为 DRAFTED
          （与 ``continuation`` adopt 同款降级），确保未重新审校的改稿无法直接
          commit 定稿。**该迁移本就在 ``ALLOWED_NEXT`` 内**（``models.py:31``
          的 ``REVIEWED: {..., "DRAFTED", ...}``）——此处直写 UPDATE 而非走
          ``update()`` 的守卫，理由是**原子性**（降级必须与新 draft 落库同事务），
          不是「白名单漏了这条边」。
        """
        if not content:
            # 防御性二次校验：router 层已用 pydantic ``min_length=1`` 拦截；此处兜底
            raise ValueError("content must be non-empty")

        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            # 1) chapter 存在性 & 当前 status 校验
            cur = conn.execute(
                "SELECT status FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            current_status = row["status"]
            if current_status not in self._DRAFT_ALLOWED_STATUS:
                raise DraftStatusNotAllowed(current_status)

            # 2) 计算下一 version（COALESCE 处理「尚无 draft」的情况）
            cur = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM drafts WHERE chapter_id = ?",
                (chapter_id,),
            )
            next_version = int(cur.fetchone()["v"]) + 1

            # 3) INSERT + 同事务降级（REVIEWED → DRAFTED）
            # 降级必须与新 draft 落库原子化：避免 draft 已落库但 status 未降
            # 留下「未重新审校的改稿可直接 commit」的口子。
            # REVIEWED→DRAFTED **在** ALLOWED_NEXT 内（models.py:31）；此处直写
            # UPDATE（同 continuation adopt 原写法）是为与 INSERT 共处一个事务，
            # 不走 update() 的守卫是为了原子性，不是为了绕开白名单
            # （2026-09-27 B-5 措辞订正）。
            draft_id = new_id("dr")
            try:
                conn.execute(
                    """
                    INSERT INTO drafts
                        (draft_id, chapter_id, version, content,
                         created_by, prompt_version, model_id, created_at)
                    VALUES
                        (:draft_id, :chapter_id, :version, :content,
                         :created_by, :prompt_version, :model_id, :created_at)
                    """,
                    {
                        "draft_id": draft_id,
                        "chapter_id": chapter_id,
                        "version": next_version,
                        "content": content,
                        "created_by": created_by,
                        "prompt_version": prompt_version,
                        "model_id": model_id,
                        "created_at": now,
                    },
                )
                if current_status == "REVIEWED":
                    conn.execute(
                        "UPDATE chapters SET status = 'DRAFTED', updated_at = ? "
                        "WHERE chapter_id = ?",
                        (now_iso(), chapter_id),
                    )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                # idx_drafts_chapter_version UNIQUE 违反：手工/并发导致的版本冲突。
                # 转领域异常，router 层映射 409。
                conn.rollback()
                raise DraftVersionConflict(next_version) from exc
        finally:
            conn.close()

        # 走读路径返回完整行（保证字段类型一致）
        return {
            "draft_id": draft_id,
            "chapter_id": chapter_id,
            "version": next_version,
            "content": content,
            "created_by": created_by,
            "prompt_version": prompt_version,
            "model_id": model_id,
            "created_at": now,
        }

    # ============================================================== Sprint 6
    # revision_note：改稿意见专用入口（task R1），替代走评审驳回路径的 hack。
    # ----------------------------------------------------------------------

    # revision_note 仅在 chapter 处于「未锁定」状态可写：COMMITTED / RELEASED
    # 正文已锁定，不接受改稿意见。PLANNED / DRAFTED / REVIEWED 放行。
    _REVISION_NOTE_ALLOWED_STATUS: frozenset[str] = frozenset(
        {"PLANNED", "DRAFTED", "REVIEWED"}
    )

    def update_revision_note(self, chapter_id: str, note: str) -> dict | None:
        """写入或清除 chapter 的改稿意见（``plan_json.revision_note``）。

        - chapter 不存在 → ``None``（router 转 404，与 ``get`` / ``create_draft`` 一致）。
        - chapter.status ∉ {PLANNED, DRAFTED, REVIEWED} →
          ``RevisionNoteStatusNotAllowed``（router 转 409）。
        - ``note`` 非空白字符串 → 写入 ``plan_json["revision_note"] = note``；
          空白/空串 → 删除该键（语义=清除改稿意见）。
        - 原 ``plan_json`` 为 NULL/空 → 视为 ``{}`` 再操作；写回保留其它键。
        - 返回更新后的 chapter dict（走 ``_row_to_dict`` 保证序列化一致）。
        """
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            # 1) chapter 存在性 + 当前 status 校验
            cur = conn.execute(
                "SELECT status, plan_json FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            current_status = row["status"]
            if current_status not in self._REVISION_NOTE_ALLOWED_STATUS:
                raise RevisionNoteStatusNotAllowed(current_status)

            # 2) 解析 plan_json（NULL/空 → {}）；不破坏其它键
            raw_plan = row["plan_json"]
            if raw_plan:
                try:
                    plan = json.loads(raw_plan)
                    if not isinstance(plan, dict):
                        # 历史脏数据：plan_json 不是 dict 形态，视为 {}，避免破坏 schema
                        plan = {}
                except json.JSONDecodeError as exc:
                    # 2026-10-22 批次二十三（B 类留痕）：控制流不变（仍视作空 plan），
                    # 但**留痕**——否则下游按「该章无 plan」判定，与「plan 脏了」不可区分。
                    log.warning(
                        "chapters.plan_json 解析失败 ⇒ 视为空 plan（下游按「无 plan」走）："
                        "chapter_id=%s err=%s",
                        chapter_id,
                        exc,
                    )
                    plan = {}
            else:
                plan = {}

            # 3) 写入或清除 revision_note（空白字符串视为清除）
            if note and note.strip():
                plan["revision_note"] = note
            else:
                plan.pop("revision_note", None)

            # 4) UPDATE plan_json + updated_at（ensure_ascii=False 与既有口径一致）
            conn.execute(
                """
                UPDATE chapters
                   SET plan_json = ?, updated_at = ?
                 WHERE chapter_id = ?
                """,
                (json.dumps(plan, ensure_ascii=False), now, chapter_id),
            )
            conn.commit()

            # 5) 走读路径返回完整行（保证字段类型一致）
            cur = conn.execute(
                "SELECT * FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            )
            updated_row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(updated_row) if updated_row else None

    # ============================================================== 重开返修
    # COMMITTED → REVIEWED（2026-09-26 批次）：定稿章局部返修入口。
    # 语义：commits 历史不撤销（append-only），重开后走既有评审/提交链产生新 commit；
    # 该边只由本方法使用（ALLOWED_NEXT 内有注释说明），通用 PATCH update 不承诺该语义。
    # ------------------------------------------------------------------ reopen

    def reopen_for_repair(self, chapter_id: str, reason: str | None = None) -> dict | None:
        """把 COMMITTED 章重开为 REVIEWED（返修入口）。

        - chapter 不存在 → ``None``（router 转 404，与 ``get`` / ``create_draft`` 一致）。
        - chapter.status != COMMITTED → ``ChapterTransitionError``（router 转 409）。
          显式拦截是必要的：其余状态到 REVIEWED 可能本身是合法推进边
          （如 DRAFTED→REVIEWED），若放行会让 reopen 被当成普通推进混用。
        - 迁移走绕白名单直写（同 ``create_draft`` 的降级写法先例）：2026-09-26 收尾
          批次起 ``update()`` 显式拦截 COMMITTED→REVIEWED 借道（该边保留给本方法），
          reopen 不能再借 ``update()``，改为预检后直写 UPDATE（``updated_at`` 同步
          刷新，与 ``update()`` 落库口径一致）。
        - ``reason`` 非空白时附加写入 ``plan_json.revision_note``（前缀 ``reopen: ``；
          已有 note 则换行拼接，不覆盖历史意见）。reopen 完成后 status 已是 REVIEWED，
          满足 ``_REVISION_NOTE_ALLOWED_STATUS``，故复用 ``update_revision_note``。
        - 返回更新后的 chapter dict（走 ``_row_to_dict`` 保证序列化一致）。
        """
        current = self.get(chapter_id)
        if current is None:
            return None
        if current["status"] != "COMMITTED":
            raise ChapterTransitionError(current["status"], "REVIEWED")

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                """
                UPDATE chapters
                SET status = 'REVIEWED', updated_at = :updated_at
                WHERE chapter_id = :chapter_id
                """,
                {"updated_at": now_iso(), "chapter_id": chapter_id},
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                # 预检后、更新前被并发删除：按 404 语义返回 None
                return None
            conn.commit()
            cur = conn.execute(
                "SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)
            )
            row = cur.fetchone()
        finally:
            conn.close()
        updated = self._row_to_dict(row) if row else None
        if updated is None:
            return None

        if reason and reason.strip():
            existing_note = (updated.get("plan_json") or {}).get("revision_note")
            text = f"reopen: {reason.strip()}"
            merged = f"{existing_note}\n{text}" if existing_note else text
            merged_result = self.update_revision_note(chapter_id, merged)
            if merged_result is not None:
                updated = merged_result

        return updated


__all__ = [
    "ChapterService",
    "ChapterNumberConflict",
    "ChapterTransitionError",
    "DraftStatusNotAllowed",
    "DraftVersionConflict",
    "RevisionNoteStatusNotAllowed",
    "ChapterError",
]
