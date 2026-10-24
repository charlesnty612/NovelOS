"""tests/unit 包共用测试 helper（2026-10-23 task-6 阶段一 · 复制粘贴收敛）。

本模块里的每个函数都来自**本包内多个测试文件里逐字相同**的副本 ⇒ 搬过来
**行为逐位不变**（函数名也不变 ⇒ 调用点一行未改）。

⚠️ 只收「自由变量可解析」的簇：函数体用到的名字要么是内建，要么由本模块
顶部同样的 import 绑定 ⇒ 否则宁可不搬（不能凭空造依赖）。

⚠️ 2026-10-24（跨套逐字副本上移）：`_evidence` 是**跨套逐字副本**（与 api、
integration 两套的同名定义逐字节一致 ⇒ 逐块 SHA256 校验落在同一哈希组）⇒ 已上移到
单点 `tests/_shared_common.py`，此处改为**再导出**（函数名不变 ⇒ 消费方
`from ._shared import ...` 一行未改）。
本套**域特有**的 `_insert_project` / `_insert_draft` / `_insert_chapter` /
`_make_settings` / `_db` 留在原地。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from tests._shared_common import _evidence as _evidence


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid

def _insert_draft(db_path: Path, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) VALUES "
            "(?, ?, ?, ?, 'test:writer:v1', NULL, NULL, ?)",
            (new_id("drf"), chapter_id, 1, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    """直插一章（默认 ``status='PLANNED'`` / ``title='t'``）。

    ⚠️ 同名私有版存在于 ``tests/api/test_chapter_reopen.py``（默认
    ``status='COMMITTED'``，**有意**绕状态机聚焦 reopen 端点）—— 收敛时
    **禁止静默统一默认值**（两者是有意差异，不是逐字副本）。
    """
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 't', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid

def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings

def _db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)
