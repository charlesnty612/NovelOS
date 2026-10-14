"""V3.1 P1-1: commit 事务内以 DB 为权威重建快照的回归测试（按节拆分）。

共享 helper 与 7 集合对照口径。原文 test_snapshot_rebuild.py 的 helper 节迁入；
各节测试用例分处 test_snapshot_selfheal / _ordering / _rollback / _who_knows。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

# ----------------------------------------------------------------- helpers


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "snapshot_rebuild") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-24T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


def _open_db(db_path: Path) -> sqlite3.Connection:
    """直接打开底层 sqlite3(只读路径外的写路径用于造漂移)。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_latest_snapshot_json(db_path: Path, project_id: str) -> dict:
    """直接读最新 story_states.snapshot_json(绕过 API 便于精确断言 DB 列内容)。"""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT state_version, snapshot_json
            FROM story_states
            WHERE project_id = ?
            ORDER BY state_version DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        assert row is not None, "story_states 无快照"
        return json.loads(row["snapshot_json"])
    finally:
        conn.close()


# ----------------------------------------------------------------- 7 集合 DB-snapshot 对照口径
# 与 scripts/check_state_sync.COLLECTIONS 严格一致;测试独立持有副本,
# 避免运行时依赖 scripts 子模块(测试不应跨域引用运维脚本)。

_COLLECTIONS_CFG: list[dict] = [
    {
        "name": "characters",
        "db_table": "characters",
        "db_id_column": "character_id",
        "snapshot_path": ("characters",),
        "kind": "list_of_dicts",
        "id_field": "character_id",
    },
    {
        "name": "locations",
        "db_table": "locations",
        "db_id_column": "location_id",
        "snapshot_path": ("world", "locations"),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "factions",
        "db_table": "factions",
        "db_id_column": "faction_id",
        "snapshot_path": ("world", "factions"),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "world_rules",
        "db_table": "world_rules",
        "db_id_column": "world_rule_id",
        "snapshot_path": ("world", "world_rules"),
        "kind": "list_field",
        "id_field": "world_rule_id",
    },
    {
        "name": "plot_events",
        "db_table": "plot_events",
        "db_id_column": "event_id",
        "snapshot_path": ("events",),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "hooks",
        "db_table": "hooks",
        "db_id_column": "hook_id",
        "snapshot_path": ("hooks",),
        "kind": "list_of_dicts",
        "id_field": "hook_id",
    },
    {
        "name": "narrative_debts",
        "db_table": "narrative_debts",
        "db_id_column": "debt_id",
        "snapshot_path": ("debts",),
        "kind": "list_of_dicts",
        "id_field": "debt_id",
    },
]


def _drift_ids_for(db_path: Path, project_id: str, snapshot: dict) -> list[tuple[str, list[str], list[str]]]:
    """返回 [(collection_name, only_in_db, only_in_snapshot), ...] 仅包含有漂移的项。"""
    conn = _open_db(db_path)
    drifts: list[tuple[str, list[str], list[str]]] = []
    try:
        for cfg in _COLLECTIONS_CFG:
            table = cfg["db_table"]
            id_col = cfg["db_id_column"]
            try:
                db_rows = conn.execute(
                    f"SELECT {id_col} FROM {table} WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                db_ids = {str(r[0]) for r in db_rows if r[0] is not None}
            except sqlite3.Error:
                continue
            # 快照侧抽取
            node: snapshot = snapshot  # type: ignore[assignment]
            for key in cfg["snapshot_path"]:
                if not isinstance(node, dict):
                    node = None  # type: ignore[assignment]
                    break
                node = node.get(key)  # type: ignore[assignment]
            if node is None:
                snap_ids: set[str] = set()
            elif cfg["kind"] == "dict_keys":
                snap_ids = {str(k) for k in node.keys()} if isinstance(node, dict) else set()
            elif cfg["kind"] == "list_of_dicts":
                snap_ids = set()
                if isinstance(node, list):
                    for item in node:
                        if isinstance(item, dict):
                            v = item.get(cfg["id_field"])  # type: ignore[arg-type]
                            if v is not None:
                                snap_ids.add(str(v))
            elif cfg["kind"] == "list_field":
                snap_ids = set()
                if isinstance(node, list):
                    for item in node:
                        if isinstance(item, dict):
                            v = item.get(cfg["id_field"])  # type: ignore[arg-type]
                            if v is not None:
                                snap_ids.add(str(v))
            else:
                snap_ids = set()
            only_db = sorted(db_ids - snap_ids)
            only_snap = sorted(snap_ids - db_ids)
            if only_db or only_snap:
                drifts.append((cfg["name"], only_db, only_snap))
    finally:
        conn.close()
    return drifts

