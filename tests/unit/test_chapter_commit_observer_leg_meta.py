"""V3.9 批次 5.6：observer leg 元数据归属（token / latency 不得互换）。

缺陷：``_aggregate_observer_split_meta`` 旧实现按 ``rowid`` 取最后两条并**假定**
「leg_a 先落库、leg_b 后落库」。并发提交下两腿落库顺序由 provider 返回先后决定
（任一条腿都可能先完成），于是 meta 里的 leg_a / leg_b 会拿到对方的 tokens / latency。

修法：``ai_call_logs.output_json`` 就是该次 ``run_agent`` 的返回值，而两腿返回值在
调用方手里——按同一性精确归属（无启发式）；``leg_a_out`` / ``leg_b_out`` 缺省时退回
``(created_at, rowid)`` 双键稳定序。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_commit.observer import _aggregate_observer_split_meta

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _leg_output(marker: str) -> dict:
    """两腿各自的 7 数组输出（marker 让两份输出互不相同，便于按同一性归属）。"""
    out = {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    if marker == "entities":
        out["character_changes"] = [{"change_id": "cc:meta", "op": "add"}]
    else:
        out["new_events"] = [{"change_id": "ev:meta", "op": "add"}]
    return out


def _seed_observer_calls(db_path: Path, rows: list[dict]) -> tuple[str, str]:
    """建 run / node / observer agent 行，并按给定顺序插入 ai_call_logs（rowid = 插入序）。"""
    agent_id = new_id("ag")
    workflow_id = new_id("wf")
    run_id = new_id("wfr")
    node_run_id = new_id("wrn")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agents (agent_id, name, role, config_json, created_at, updated_at) "
            "VALUES (?, 'observer', 'reasoning', '{}', ?, ?)",
            (agent_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-commit', 'v1', '{}', ?, ?)",
            (workflow_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, current_node, "
            "checkpoint_json, error, retry_count, started_at, ended_at) "
            "VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)",
            (run_id, workflow_id, now),
        )
        conn.execute(
            "INSERT INTO workflow_run_nodes (node_run_id, run_id, node_id, status, "
            "started_at, ended_at, input_json, output_json, error) "
            "VALUES (?, ?, 'observer', 'RUNNING', ?, NULL, '{}', NULL, NULL)",
            (node_run_id, run_id, now),
        )
        for row in rows:
            conn.execute(
                "INSERT INTO ai_call_logs (call_id, run_id, node_run_id, agent_id, model_id, "
                "prompt_version, input_context_ids_json, output_json, token_usage_json, "
                "latency_ms, cost, error, retry_count, created_at) "
                "VALUES (?, ?, ?, ?, 'mock/mock', 'v1', '[]', ?, ?, ?, NULL, NULL, 0, ?)",
                (
                    row["call_id"],
                    run_id,
                    node_run_id,
                    agent_id,
                    json.dumps(row["output"], ensure_ascii=False),
                    json.dumps({"total": row["tokens"]}),
                    row["latency_ms"],
                    row["created_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return run_id, node_run_id


def _meta(db_path: Path, run_id: str, node_run_id: str, **kwargs) -> dict:
    return _aggregate_observer_split_meta(
        db_path,
        run_id=run_id,
        node_run_id=node_run_id,
        expected_calls=2,
        merged_at="2026-09-13T00:00:00+00:00",
        **kwargs,
    )


def test_leg_meta_attributed_by_leg_output_not_insert_order(tmp_path: Path):
    """narrative 腿先落库（rowid 1）时，meta 仍须把 leg_a 记为 entities 腿。

    这正是并发路径的真实风险：任一条腿先返回就先落库。旧实现（按 rowid 取最后两条、
    假定 leg_a 先）会把 leg_a 记成 narrative 腿的 tokens/latency。
    """
    db_path = _fresh_db(tmp_path)
    entities_out = _leg_output("entities")
    narrative_out = _leg_output("narrative")
    run_id, node_run_id = _seed_observer_calls(db_path, [
        {
            "call_id": "aic_narrative_first", "output": narrative_out,
            "tokens": 111, "latency_ms": 1111,
            "created_at": "2026-09-13T10:00:00+00:00",
        },
        {
            "call_id": "aic_entities_second", "output": entities_out,
            "tokens": 222, "latency_ms": 2222,
            "created_at": "2026-09-13T10:00:05+00:00",
        },
    ])

    meta = _meta(
        db_path, run_id, node_run_id,
        leg_a_out=entities_out, leg_b_out=narrative_out,
    )

    assert meta["leg_a"]["call_id"] == "aic_entities_second", meta["leg_a"]
    assert meta["leg_a"]["tokens"] == 222
    assert meta["leg_a"]["latency_ms"] == 2222
    assert meta["leg_b"]["call_id"] == "aic_narrative_first", meta["leg_b"]
    assert meta["leg_b"]["tokens"] == 111
    assert meta["leg_b"]["latency_ms"] == 1111


def test_leg_meta_falls_back_to_created_at_rowid_order(tmp_path: Path):
    """未传 leg 输出（无法按同一性归属）时：回放序 = ``(created_at, rowid)`` 升序。

    fixture 故意让两键给出不同顺序（后插入的行 created_at 更早），让排序键可观测；
    生产落库里两键同源于写入时刻，通常一致，此处钉的是「显式双键」这一契约。
    """
    db_path = _fresh_db(tmp_path)
    run_id, node_run_id = _seed_observer_calls(db_path, [
        {
            "call_id": "aic_rowid_1", "output": _leg_output("narrative"),
            "tokens": 303, "latency_ms": 3030,
            "created_at": "2026-09-13T11:00:05+00:00",
        },
        {
            "call_id": "aic_rowid_2", "output": _leg_output("entities"),
            "tokens": 404, "latency_ms": 4040,
            "created_at": "2026-09-13T11:00:00+00:00",
        },
    ])

    meta = _meta(db_path, run_id, node_run_id)

    assert meta["leg_a"]["call_id"] == "aic_rowid_2", meta["leg_a"]
    assert meta["leg_b"]["call_id"] == "aic_rowid_1", meta["leg_b"]
