"""checkpoint_json 体积软上限（512KB）测试（2026-09-06 审查批次二）。

覆盖：
1. ``_cap_checkpoint_payload``：小于上限原样返回（零改动）；超出上限时
   最大的顶层值被替换为截断标记（携带 original_bytes），小值保留；
   截断后序列化体积回到上限内。
2. 引擎集成：``start_with_nodes`` 节点产出超大 payload（>512KB）→ 终态
   ``workflow_runs.checkpoint_json`` 序列化体积 ≤ 上限 + 标记存在。
3. PAUSED 不截断：PauseRequested 场景下 checkpoint_json 保留全量 payload
   （人工审阅 resume 需要全量 ctx）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.workflow_runtime.engine import (
    _CHECKPOINT_MAX_BYTES,
    _CHECKPOINT_TRUNCATED_KEY,
    PauseRequested,
    WorkflowEngine,
    WorkflowNode,
    _cap_checkpoint_payload,
)


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


def _get_checkpoint(db_path: Path, run_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json, status FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return {"status": row["status"], "ctx": json.loads(row["checkpoint_json"])}


def test_cap_below_limit_returns_unchanged() -> None:
    small = {"a": "x" * 100, "b": [1, 2, 3]}
    capped, n = _cap_checkpoint_payload(small)
    assert capped is small
    assert n == 0


def test_cap_replaces_largest_values_with_marker() -> None:
    big = {"huge": "x" * (600 * 1024), "medium": "y" * (100 * 1024), "tiny": "keep"}
    capped, n = _cap_checkpoint_payload(big, max_bytes=_CHECKPOINT_MAX_BYTES)
    assert n >= 1
    # 截断标记形状
    marked = [k for k, v in capped.items() if isinstance(v, dict) and v.get(_CHECKPOINT_TRUNCATED_KEY)]
    assert "huge" in marked
    # 小值保真
    assert capped["tiny"] == "keep"
    # 体积回到上限内
    assert len(json.dumps(capped, ensure_ascii=False).encode("utf-8")) <= _CHECKPOINT_MAX_BYTES


def test_engine_terminal_checkpoint_capped(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)

    def big_node(ctx: dict) -> dict:
        return {"prose": "x" * (700 * 1024), "meta": {"ok": True}}

    nodes = [WorkflowNode(node_id="n1", kind="State", fn=big_node)]
    run_id = engine.start_with_nodes("cap_test", nodes)

    result = _get_checkpoint(engine.db_path, run_id)
    assert result["status"] == "COMPLETED"
    raw = json.dumps(result["ctx"], ensure_ascii=False).encode("utf-8")
    assert len(raw) <= _CHECKPOINT_MAX_BYTES
    # prose 被截断、meta（小值）保留
    assert isinstance(result["ctx"]["prose"], dict)
    assert result["ctx"]["prose"].get(_CHECKPOINT_TRUNCATED_KEY) is True
    assert result["ctx"]["meta"] == {"ok": True}


def test_engine_paused_checkpoint_not_capped(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)

    def pausing_node(ctx: dict) -> dict:
        raise PauseRequested({"review_payload": "z" * (700 * 1024)})

    nodes = [WorkflowNode(node_id="human_gate", kind="Human", fn=pausing_node)]
    run_id = engine.start_with_nodes("cap_pause_test", nodes)

    result = _get_checkpoint(engine.db_path, run_id)
    assert result["status"] == "PAUSED"
    # PAUSED 落盘不截断：payload 完整保留（挂在 node_id 键下）
    payload = result["ctx"]["human_gate"]["__pause_payload__"]
    assert payload["review_payload"] == "z" * (700 * 1024)
