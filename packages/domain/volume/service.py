"""VolumeService（V3.4 多卷与规模——组织层）。

职责：volumes 表 CRUD + 业务规则 + 章节归属（chapters.volume_id）。
对齐 ``database/migrations/0015_volumes.sql`` 中：

- ``volumes`` 表结构（volume_id / project_id / number / title / status /
  terminal_snapshot_json / created_at / updated_at）。
- ``chapters.volume_id`` 外键列（可空）。

设计要点（与并行代理共用的统一模式）：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection``
  开连接、``try / finally`` 关闭。
- 主键由 ``new_id("vol")`` 生成；时间戳由 ``now_iso()`` 生成。
- 校验 / 业务规则：
  1. **同 project 下 number 唯一**：依赖 DB UNIQUE(project_id, number)；
     Service 层预检 + IntegrityError 兜底（与 ChapterService 风格一致），
     重复 → VolumeConflictError（router 转 409）。
  2. **同 project 下 active 卷单例**：create 前查 project.active 卷是否
     存在；存在 → VolumeConflictError（显式要求先 seal）。list/list+assign
     不强制（已 sealed 的历史卷不受影响）。
  3. **seal(volume_id)**：同事务内做（a）status='sealed'；
     （b）terminal_snapshot_json=该项目 story_states 最新快照 JSON
     （``json.dumps(..., ensure_ascii=False)``）；无快照时存 ``{}``；
     （c）updated_at 刷新。已 sealed 的 volume 重复 seal → VolumeConflictError。
  4. **sealed 卷禁止挂章**：assign_chapter 检查 volume.status='sealed' →
     VolumeSealedError（router 转 409）。
  5. **assign_chapter(volume_id, chapter_id)**：chapter 必须存在；chapter
     .project_id 必须 == volume.project_id（同 project 校验，跨项目挂章
     → VolumeValidationError）；同 chapter 只能属一卷；幂等（chapter 已在
     该卷上视为成功）。
  6. **状态机**：PATCH status='active' 仅在 volume 当前 status='active' 时
     允许（仅 title 改写）；status='sealed' → 'active' 反向跳变一律拒绝。
- list 端点：通过 LEFT JOIN chapters + GROUP BY 计算 chapter_count；
  按 number ASC 排序。
- terminal_snapshot_json：列存 TEXT；读路径 ``json.loads`` 解出为 dict
  （失败 fallback None，与 arc service 对 snapshot_json 的容错一致）。
- terminal_snapshot_json 写入：``json.dumps(snapshot, ensure_ascii=False)``。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.snapshots import (
    latest_snapshot_version,
)

from .models import VolumeCreate, VolumeUpdate

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class VolumeError(Exception):
    """Volume Service 业务异常基类。"""

    http_status: int = 400


class VolumeNotFoundError(VolumeError):
    """卷不存在（router 转 404）。"""

    http_status = 404


class VolumeConflictError(VolumeError):
    """409 冲突——number 重复 / active 卷已存在 / 重复 seal 等。"""

    http_status = 409


class VolumeValidationError(VolumeError):
    """422 字段非法——跨项目挂章 / 章节不存在 / CHECK 违反等。"""

    http_status = 422


class VolumeSealedError(VolumeError):
    """409——sealed 卷拒绝挂章 / sealed 后试图回到 active。"""

    http_status = 409


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class VolumeService:
    """卷领域服务（volumes 表 CRUD + 章节归属）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _dump_snapshot(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        if "terminal_snapshot_json" in d and d["terminal_snapshot_json"]:
            try:
                d["terminal_snapshot_json"] = json.loads(d["terminal_snapshot_json"])
            except json.JSONDecodeError:
                d["terminal_snapshot_json"] = None
        else:
            d["terminal_snapshot_json"] = None
        return d

    @staticmethod
    def _require_project(db_path: str, project_id: str) -> None:
        """project 不存在 → VolumeNotFoundError。"""
        if not project_id:
            raise VolumeNotFoundError("project_id 不能为空")
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise VolumeNotFoundError(f"project#{project_id} 不存在")

    # ------------------------------------------------------------------ create
    def create(self, project_id: str, payload: VolumeCreate) -> dict:
        """创建卷（默认 status='active'）。

        - project 不存在 → VolumeNotFoundError（404）。
        - 同 project 下 number 已存在 → VolumeConflictError（409）。
        - 同 project 下已存在 active 卷 → VolumeConflictError（409，提示先 seal）。
        """
        self._require_project(self.db_path, project_id)
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            # 1) number 唯一性预检
            cur = conn.execute(
                "SELECT 1 FROM volumes WHERE project_id = ? AND number = ?",
                (project_id, payload.number),
            )
            if cur.fetchone() is not None:
                raise VolumeConflictError(
                    f"volume number {payload.number} already exists in project {project_id}"
                )

            # 2) active 卷单例校验（仅同 project 下同时只允许一个 active）
            cur = conn.execute(
                "SELECT 1 FROM volumes WHERE project_id = ? AND status = 'active'",
                (project_id,),
            )
            if cur.fetchone() is not None:
                raise VolumeConflictError(
                    f"project {project_id} already has an active volume; "
                    f"seal the existing one before creating a new one"
                )

            row = {
                "volume_id": new_id("vol"),
                "project_id": project_id,
                "number": payload.number,
                "title": payload.title,
                "status": "active",
                "terminal_snapshot_json": None,
                "created_at": now,
                "updated_at": now,
            }
            try:
                conn.execute(
                    """
                    INSERT INTO volumes
                        (volume_id, project_id, number, title, status,
                         terminal_snapshot_json, created_at, updated_at)
                    VALUES
                        (:volume_id, :project_id, :number, :title, :status,
                         :terminal_snapshot_json, :created_at, :updated_at)
                    """,
                    row,
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                # UNIQUE(project_id, number) 兜底
                raise VolumeConflictError(
                    f"volume number {payload.number} already exists in project {project_id}"
                ) from exc
        finally:
            conn.close()
        return self._row_to_dict(row)  # type: ignore[arg-type]

    # -------------------------------------------------------------------- list
    def list(self, project_id: str) -> list[dict]:
        """列项目下所有卷（含 chapter_count 聚合），按 number ASC。

        - project 不存在 → VolumeNotFoundError。
        - chapter_count：通过 LEFT JOIN + GROUP BY 计算；0 章时为 0。
        """
        self._require_project(self.db_path, project_id)
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT
                    v.volume_id, v.project_id, v.number, v.title, v.status,
                    v.terminal_snapshot_json, v.created_at, v.updated_at,
                    COALESCE(COUNT(c.chapter_id), 0) AS chapter_count
                FROM volumes v
                LEFT JOIN chapters c ON c.volume_id = v.volume_id
                WHERE v.project_id = ?
                GROUP BY v.volume_id
                ORDER BY v.number ASC, v.volume_id ASC
                """,
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = self._row_to_dict(r)  # type: ignore[arg-type]
            d["chapter_count"] = int(r["chapter_count"])
            out.append(d)
        return out

    # --------------------------------------------------------------------- get
    def get(self, volume_id: str) -> dict | None:
        """按主键查询；不存在返回 None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM volumes WHERE volume_id = ?", (volume_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)  # type: ignore[arg-type]

    # ------------------------------------------------------------------- update
    def update(self, volume_id: str, payload: VolumeUpdate) -> dict | None:
        """部分更新（title / status）。

        - volume 不存在 → None（router 转 404）。
        - status='sealed' 走显式 seal() 路径（也允许 PATCH 设置 sealed，但
          不会自动冻结快照——service 层不暴露 PATCH->sealed 冻结语义，
          显式 seal() 才是冻结入口）；PATCH status='sealed' 仅作状态修正
          场景（不冻结快照，仅改 status/updated_at）。
        - status='sealed' → 'active' 反向跳变一律拒绝（VolumeSealedError）。
        - title 唯一允许的字段（与 number / project_id / volume_id 等同
          PATCH 不允许修改的字段保持一致）。
        """
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            return self.get(volume_id)

        # 状态机预检
        if "status" in fields:
            current = self.get(volume_id)
            if current is None:
                return None
            target = fields["status"]
            if current["status"] == "sealed" and target == "active":
                raise VolumeSealedError(
                    f"volume {volume_id!r} is sealed; cannot revert to active"
                )

        fields["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params: dict = dict(fields)
        params["volume_id"] = volume_id

        conn = get_connection(self.db_path)
        try:
            try:
                cur = conn.execute(
                    f"UPDATE volumes SET {set_clause} WHERE volume_id = :volume_id",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise VolumeValidationError(f"integrity error: {exc}") from exc
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return None
            conn.commit()
            cur = conn.execute(
                "SELECT * FROM volumes WHERE volume_id = ?", (volume_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)  # type: ignore[arg-type]

    # -------------------------------------------------------------------- seal
    def seal(self, volume_id: str) -> dict | None:
        """封存卷（status='sealed' + 冻结终态快照）。

        - volume 不存在 → None（router 转 404）。
        - 已是 sealed → VolumeConflictError（409，幂等失败）。
        - 同事务内：
          (a) 读 story_states 最新快照 JSON（无快照则存 ``{}``）；
          (b) UPDATE volumes SET status='sealed', terminal_snapshot_json=?,
              updated_at=?。
        """
        current = self.get(volume_id)
        if current is None:
            return None
        if current["status"] == "sealed":
            raise VolumeConflictError(
                f"volume {volume_id!r} is already sealed"
            )

        # 1) 取最新快照
        conn = get_connection(self.db_path)
        try:
            _, snap = latest_snapshot_version(conn, current["project_id"])
            snapshot_json = self._dump_snapshot(snap if isinstance(snap, dict) else {})

            # 2) UPDATE
            cur = conn.execute(
                """
                UPDATE volumes
                SET status = 'sealed',
                    terminal_snapshot_json = ?,
                    updated_at = ?
                WHERE volume_id = ? AND status = 'active'
                """,
                (snapshot_json, now_iso(), volume_id),
            )
            if cur.rowcount == 0:
                # 同事务内被其他连接改 sealed → 并发兜底
                conn.rollback()
                raise VolumeConflictError(
                    f"volume {volume_id!r} state changed concurrently"
                )
            conn.commit()
            cur = conn.execute(
                "SELECT * FROM volumes WHERE volume_id = ?", (volume_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)  # type: ignore[arg-type]

    # --------------------------------------------------------------- assign_ch
    def assign_chapter(self, volume_id: str, chapter_id: str) -> dict:
        """将章节挂到指定卷（chapters.volume_id = volume_id）。

        - volume 不存在 → VolumeNotFoundError（404）。
        - volume.status='sealed' → VolumeSealedError（409）。
        - chapter 不存在 → VolumeValidationError（422）。
        - chapter 与 volume 不属同一 project → VolumeValidationError（422）。
        - 幂等：chapter.volume_id 已 == volume_id 视为成功。
        - chapter 已在其它卷 → 直接覆盖（DDL 同一章只能属一卷无 DB 层约束，
          但业务语义「同章只能属一卷」由 service 保证「最近一次 assign
          生效」）。
        """
        volume = self.get(volume_id)
        if volume is None:
            raise VolumeNotFoundError(f"volume {volume_id!r} not found")
        if volume["status"] == "sealed":
            raise VolumeSealedError(
                f"volume {volume_id!r} is sealed; cannot assign chapters"
            )

        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                """
                SELECT chapter_id, project_id, volume_id
                FROM chapters
                WHERE chapter_id = ?
                """,
                (chapter_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise VolumeValidationError(
                    f"chapter {chapter_id!r} not found"
                )
            if row["project_id"] != volume["project_id"]:
                raise VolumeValidationError(
                    f"chapter {chapter_id!r} belongs to project "
                    f"{row['project_id']!r}, cannot assign to volume in "
                    f"project {volume['project_id']!r}"
                )
            # 幂等：已挂在该卷 → 直接返回 volume 完整行
            if row["volume_id"] == volume_id:
                return volume
            # 覆盖挂载（同章只能属一卷；最近一次 assign 生效）
            conn.execute(
                "UPDATE chapters SET volume_id = ?, updated_at = ? WHERE chapter_id = ?",
                (volume_id, now_iso(), chapter_id),
            )
            conn.commit()
        finally:
            conn.close()
        return volume

    # ============================================================== helpers
    @staticmethod
    def parse_snapshot(raw: str | None) -> dict | None:
        """对外暴露 terminal_snapshot_json 的解析 helper（便于 router 集成）。

        ``raw`` 为 None 或非 JSON 字符串 → 返回 None；合法 JSON → 返回 dict。
        与 :func:`packages.core.story_state.snapshots._parse_required_json`
        对齐语义，但 fallback 行为不同（None vs default）。
        """
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None


__all__ = [
    "VolumeService",
    "VolumeError",
    "VolumeNotFoundError",
    "VolumeConflictError",
    "VolumeValidationError",
    "VolumeSealedError",
]
