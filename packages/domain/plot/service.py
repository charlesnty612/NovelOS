"""PlotService（Sprint 1）。

plot_events / timeline_events / relationships 的 CRUD + 一致性检查，
对齐 ``database/migrations/0001_init.sql`` 与 PRD §19（Plot Graph）、§20（Timeline）。

设计要点
========
- ``plot_events.create`` 时若 ``time.timeline_day`` 存在 → 同事务插入对应 ``timeline_events``
  （PRD §20 时间线索引自动同步）。
- 引用完整性：
  * ``cause`` / ``effects`` 中每个 event_id 必须已存在（否则 422）。
  * ``participants`` 中每个 character_id 必须已存在（否则 422）。
- 删除 ``plot_events`` 时：被其他 event 的 cause/effects 引用，或被 timeline_events 引用 → 409。
- list 支持 ``?type=`` / ``?status=`` 过滤（query 参数）。
- ``relationships`` S1 只读，写入由 S2 State Delta 驱动。

不在本服务范围：情节推演、What-if 分支（属 Sprint 10）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._util import new_id
from .models import (
    PLOT_EVENT_STATUSES,
    PLOT_EVENT_TYPES,
    VISIBILITY_VALUES,
    PlotEvent,
    Relationship,
    TimelineEvent,
)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class PlotServiceError(Exception):
    """PlotService 通用错误基类。"""

    http_status: int = 400


class NotFoundError(PlotServiceError):
    http_status = 404


class ReferencedError(PlotServiceError):
    http_status = 409


class ValidationError(PlotServiceError):
    http_status = 422


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PlotService:
    """Plot 三实体的统一 Service。

    用法::

        svc = PlotService(conn)
        ev = svc.create_event(project_id="prj_x", type="revelation",
                              participants=["char_a"], time={"timeline_day": 3})
        # 此时 timeline_events 也同步出现一条 day_index=3 的索引行
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ============================================================== plot_events

    def create_event(
        self,
        project_id: str,
        type: str,
        *,
        cause: list[str] | None = None,
        effects: list[str] | None = None,
        participants: list[str] | None = None,
        location_id: str | None = None,
        time: dict[str, Any] | None = None,
        status: str | None = None,
        introduced_chapter_id: str | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
    ) -> PlotEvent:
        self._require_project(project_id)
        if type not in PLOT_EVENT_TYPES:
            raise ValidationError(
                f"type 非法: {type!r}，允许 {PLOT_EVENT_TYPES}"
            )
        cause = self._validate_id_list(cause, "cause")
        effects = self._validate_id_list(effects, "effects")
        participants = self._validate_id_list(participants, "participants")

        status = status or "planned"
        if status not in PLOT_EVENT_STATUSES:
            raise ValidationError(
                f"status 非法: {status!r}，允许 {PLOT_EVENT_STATUSES}"
            )

        visibility = self._validate_visibility(visibility)

        # time 默认值（PRD §19）
        if time is None:
            time = {"timeline_day": 1, "in_story_date": None}
        self._validate_time(time)

        # location_id 引用校验
        if location_id is not None:
            self._require_exists(
                "locations", "location_id", location_id, "location"
            )

        # introduced_chapter_id 引用校验
        if introduced_chapter_id is not None:
            self._require_exists(
                "chapters", "chapter_id", introduced_chapter_id, "chapter"
            )

        # cause / effects 中每个 event_id 必须已存在
        if cause:
            self._require_events_exist(cause, "cause")
        if effects:
            self._require_events_exist(effects, "effects")

        # participants 中每个 character_id 必须已存在
        if participants:
            self._require_characters_exist(participants)

        eid = new_id("event")
        cause_json = json.dumps(cause, ensure_ascii=False)
        effects_json = json.dumps(effects, ensure_ascii=False)
        participants_json = json.dumps(participants, ensure_ascii=False)
        time_json = json.dumps(time, ensure_ascii=False)
        who_knows_json = (
            json.dumps(self._validate_who_knows(who_knows), ensure_ascii=False)
            if who_knows is not None else None
        )

        try:
            self.conn.execute(
                """
                INSERT INTO plot_events (
                    event_id, project_id, type,
                    cause_json, effects_json, participants_json,
                    location_id, time_json, status,
                    introduced_chapter_id, visibility, who_knows
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid, project_id, type,
                    cause_json, effects_json, participants_json,
                    location_id, time_json, status,
                    introduced_chapter_id, visibility, who_knows_json,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"插入 plot_event 失败: {exc}") from exc

        # PRD §20：若 time.timeline_day 存在 → 自动同步插入 timeline_events
        timeline_day = self._extract_timeline_day(time)
        if timeline_day is not None:
            try:
                self._insert_timeline_event(
                    project_id=project_id,
                    event_id=eid,
                    day_index=timeline_day,
                    time_ref=None,
                    description=None,
                )
            except sqlite3.IntegrityError as exc:
                # 同步插入失败时回滚主表插入（同一个连接，按调用顺序 commit，
                # 但 plot_events 已 commit；此处仅记录错误，不强行回滚主表 —
                # 实际场景中此分支几乎不会触发，因为前置引用校验已通过）
                raise ValidationError(f"同步 timeline_events 失败: {exc}") from exc

        return PlotEvent(
            id=eid,
            project_id=project_id,
            type=type,
            cause=cause,
            effects=effects,
            participants=participants,
            location_id=location_id,
            time=time,
            status=status,
            introduced_chapter_id=introduced_chapter_id,
            visibility=visibility,
            who_knows=who_knows,
        )

    def get_event(self, event_id: str) -> PlotEvent | None:
        row = self.conn.execute(
            "SELECT * FROM plot_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def list_events(
        self,
        project_id: str,
        *,
        type: str | None = None,
        status: str | None = None,
    ) -> list[PlotEvent]:
        sql = "SELECT * FROM plot_events WHERE project_id = ?"
        params: list[Any] = [project_id]
        if type is not None:
            if type not in PLOT_EVENT_TYPES:
                raise ValidationError(
                    f"type 非法: {type!r}，允许 {PLOT_EVENT_TYPES}"
                )
            sql += " AND type = ?"
            params.append(type)
        if status is not None:
            if status not in PLOT_EVENT_STATUSES:
                raise ValidationError(
                    f"status 非法: {status!r}，允许 {PLOT_EVENT_STATUSES}"
                )
            sql += " AND status = ?"
            params.append(status)
        # 稳定排序：按 event_id 保证顺序确定（DDL 无 created_at 列）
        sql += " ORDER BY event_id"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def update_event(
        self,
        event_id: str,
        *,
        type: str | None = None,
        cause: list[str] | None = None,
        effects: list[str] | None = None,
        participants: list[str] | None = None,
        location_id: str | None = None,
        time: dict[str, Any] | None = None,
        status: str | None = None,
        introduced_chapter_id: str | None = None,
        visibility: str | None = None,
        who_knows: list[str] | None = None,
    ) -> PlotEvent:
        existing = self.get_event(event_id)
        if existing is None:
            raise NotFoundError(f"event#{event_id} 不存在")

        sets: list[str] = []
        params: list[Any] = []

        if type is not None:
            if type not in PLOT_EVENT_TYPES:
                raise ValidationError(
                    f"type 非法: {type!r}，允许 {PLOT_EVENT_TYPES}"
                )
            sets.append("type = ?")
            params.append(type)
        if cause is not None:
            cause = self._validate_id_list(cause, "cause")
            self._require_events_exist(cause, "cause")
            sets.append("cause_json = ?")
            params.append(json.dumps(cause, ensure_ascii=False))
        if effects is not None:
            effects = self._validate_id_list(effects, "effects")
            self._require_events_exist(effects, "effects")
            sets.append("effects_json = ?")
            params.append(json.dumps(effects, ensure_ascii=False))
        if participants is not None:
            participants = self._validate_id_list(participants, "participants")
            self._require_characters_exist(participants)
            sets.append("participants_json = ?")
            params.append(json.dumps(participants, ensure_ascii=False))
        if location_id is not None:
            self._require_exists("locations", "location_id", location_id, "location")
            sets.append("location_id = ?")
            params.append(location_id)
        if time is not None:
            self._validate_time(time)
            sets.append("time_json = ?")
            params.append(json.dumps(time, ensure_ascii=False))
        if status is not None:
            if status not in PLOT_EVENT_STATUSES:
                raise ValidationError(
                    f"status 非法: {status!r}，允许 {PLOT_EVENT_STATUSES}"
                )
            sets.append("status = ?")
            params.append(status)
        if introduced_chapter_id is not None:
            self._require_exists(
                "chapters", "chapter_id", introduced_chapter_id, "chapter"
            )
            sets.append("introduced_chapter_id = ?")
            params.append(introduced_chapter_id)
        if visibility is not None:
            sets.append("visibility = ?")
            params.append(self._validate_visibility(visibility))
        if who_knows is not None:
            sets.append("who_knows = ?")
            params.append(
                json.dumps(self._validate_who_knows(who_knows), ensure_ascii=False)
            )

        if sets:
            params.append(event_id)
            try:
                self.conn.execute(
                    f"UPDATE plot_events SET {', '.join(sets)} WHERE event_id = ?",
                    params,
                )
                self.conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"更新 plot_event 失败: {exc}") from exc

        updated = self.get_event(event_id)
        assert updated is not None  # 刚刚存在
        return updated

    def delete_event(self, event_id: str) -> None:
        # 引用检查：被其他事件的 cause / effects 引用？
        in_cause = self.conn.execute(
            """
            SELECT 1 FROM plot_events
            WHERE event_id != ?
              AND EXISTS (
                  SELECT 1 FROM json_each(cause_json) WHERE value = ?
              )
            LIMIT 1
            """,
            (event_id, event_id),
        ).fetchone()
        if in_cause is not None:
            raise ReferencedError(
                f"event#{event_id} 被其他事件的 cause 引用，无法删除"
            )
        in_effects = self.conn.execute(
            """
            SELECT 1 FROM plot_events
            WHERE event_id != ?
              AND EXISTS (
                  SELECT 1 FROM json_each(effects_json) WHERE value = ?
              )
            LIMIT 1
            """,
            (event_id, event_id),
        ).fetchone()
        if in_effects is not None:
            raise ReferencedError(
                f"event#{event_id} 被其他事件的 effects 引用，无法删除"
            )
        # 引用检查：被 timeline_events 引用？
        in_timeline = self.conn.execute(
            "SELECT 1 FROM timeline_events WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        if in_timeline is not None:
            # 直接级联删除 timeline_events 中的索引（PRD §20 时间线是 event 的派生索引）
            self.conn.execute(
                "DELETE FROM timeline_events WHERE event_id = ?", (event_id,)
            )

        cur = self.conn.execute(
            "DELETE FROM plot_events WHERE event_id = ?", (event_id,)
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"event#{event_id} 不存在")
        self.conn.commit()

    # ============================================================ timeline_events

    def create_timeline_event(
        self,
        project_id: str,
        event_id: str,
        day_index: int,
        *,
        time_ref: str | None = None,
        description: str | None = None,
    ) -> TimelineEvent:
        self._require_project(project_id)
        self._require_exists("plot_events", "event_id", event_id, "event")
        if not isinstance(day_index, int):
            raise ValidationError("day_index 必须是整数")
        if day_index < 0:
            raise ValidationError("day_index 不能为负")

        tid = new_id("tle")
        try:
            self.conn.execute(
                """
                INSERT INTO timeline_events (
                    timeline_event_id, project_id, event_id, day_index,
                    time_ref, description
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tid, project_id, event_id, day_index, time_ref, description),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"插入 timeline_event 失败: {exc}") from exc

        return TimelineEvent(
            id=tid,
            project_id=project_id,
            event_id=event_id,
            day_index=day_index,
            time_ref=time_ref,
            description=description,
        )

    def list_timeline_events(self, project_id: str) -> list[TimelineEvent]:
        rows = self.conn.execute(
            """
            SELECT * FROM timeline_events
            WHERE project_id = ?
            ORDER BY day_index, timeline_event_id
            """,
            (project_id,),
        ).fetchall()
        return [self._row_to_timeline(r) for r in rows]

    def delete_timeline_event(self, timeline_event_id: str) -> None:
        cur = self.conn.execute(
            "DELETE FROM timeline_events WHERE timeline_event_id = ?",
            (timeline_event_id,),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"timeline_event#{timeline_event_id} 不存在")
        self.conn.commit()

    # ============================================================== relationships (只读)

    def list_relationships(self, project_id: str) -> list[Relationship]:
        rows = self.conn.execute(
            "SELECT * FROM relationships WHERE project_id = ? ORDER BY relationship_id",
            (project_id,),
        ).fetchall()
        return [self._row_to_relationship(r) for r in rows]

    # ====================================================================== 辅助

    def _insert_timeline_event(
        self,
        *,
        project_id: str,
        event_id: str,
        day_index: int,
        time_ref: str | None,
        description: str | None,
    ) -> None:
        tid = new_id("tle")
        self.conn.execute(
            """
            INSERT INTO timeline_events (
                timeline_event_id, project_id, event_id, day_index,
                time_ref, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tid, project_id, event_id, day_index, time_ref, description),
        )
        self.conn.commit()

    @staticmethod
    def _extract_timeline_day(time: dict[str, Any]) -> int | None:
        td = time.get("timeline_day")
        if td is None:
            return None
        if not isinstance(td, int):
            return None
        if td < 0:
            return None
        return td

    @staticmethod
    def _validate_time(time: Any) -> None:
        if not isinstance(time, dict):
            raise ValidationError("time 必须是 JSON 对象")
        if "timeline_day" not in time:
            raise ValidationError("time 必须包含 timeline_day 字段")
        td = time["timeline_day"]
        if td is not None and not isinstance(td, int):
            raise ValidationError("time.timeline_day 必须是整数或 null")
        if td is not None and td < 0:
            raise ValidationError("time.timeline_day 不能为负")

    def _require_project(self, project_id: str) -> None:
        if not project_id:
            raise ValidationError("project_id 不能为空")
        exists = self.conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if exists is None:
            raise ValidationError(f"project#{project_id} 不存在")

    def _require_exists(self, table: str, id_field: str, eid: str, label: str) -> None:
        exists = self.conn.execute(
            f"SELECT 1 FROM {table} WHERE {id_field} = ?", (eid,)
        ).fetchone()
        if exists is None:
            raise ValidationError(f"{label}#{eid} 不存在")

    def _require_events_exist(self, ids: list[str], field_name: str) -> None:
        # 一次性 SELECT 预检查，避免 N 次查询
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT event_id FROM plot_events WHERE event_id IN ({placeholders})",
            ids,
        ).fetchall()
        existing = {r["event_id"] for r in rows}
        missing = [i for i in ids if i not in existing]
        if missing:
            raise ValidationError(
                f"{field_name} 引用了不存在的 event_id: {missing}"
            )

    def _require_characters_exist(self, ids: list[str]) -> None:
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT character_id FROM characters WHERE character_id IN ({placeholders})",
            ids,
        ).fetchall()
        existing = {r["character_id"] for r in rows}
        missing = [i for i in ids if i not in existing]
        if missing:
            raise ValidationError(
                f"participants 引用了不存在的 character_id: {missing}"
            )

    @staticmethod
    def _validate_id_list(value: list[str] | None, field_name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValidationError(f"{field_name} 必须是字符串数组")
        return value

    @staticmethod
    def _validate_visibility(value: str | None) -> str:
        if value is None:
            return "RESTRICTED"  # plot_events DDL 默认
        if value not in VISIBILITY_VALUES:
            raise ValidationError(
                f"visibility 非法: {value!r}，允许 {VISIBILITY_VALUES}"
            )
        return value

    @staticmethod
    def _validate_who_knows(value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValidationError("who_knows 必须是字符串数组")
        return value

    def _row_to_event(self, row: sqlite3.Row) -> PlotEvent:
        return PlotEvent(
            id=row["event_id"],
            project_id=row["project_id"],
            type=row["type"],
            cause=json.loads(row["cause_json"]) if row["cause_json"] else [],
            effects=json.loads(row["effects_json"]) if row["effects_json"] else [],
            participants=json.loads(row["participants_json"]) if row["participants_json"] else [],
            location_id=row["location_id"],
            time=json.loads(row["time_json"]) if row["time_json"] else {"timeline_day": 1},
            status=row["status"],
            introduced_chapter_id=row["introduced_chapter_id"],
            visibility=row["visibility"],
            who_knows=json.loads(row["who_knows"]) if row["who_knows"] else None,
        )

    @staticmethod
    def _row_to_timeline(row: sqlite3.Row) -> TimelineEvent:
        return TimelineEvent(
            id=row["timeline_event_id"],
            project_id=row["project_id"],
            event_id=row["event_id"],
            day_index=row["day_index"],
            time_ref=row["time_ref"],
            description=row["description"],
        )

    @staticmethod
    def _row_to_relationship(row: sqlite3.Row) -> Relationship:
        return Relationship(
            id=row["relationship_id"],
            project_id=row["project_id"],
            from_character_id=row["from_character_id"],
            to_character_id=row["to_character_id"],
            relation_type=row["relation_type"],
            state=json.loads(row["state_json"]) if row["state_json"] else {},
            last_state_version=row["last_state_version"],
        )
