"""V3.9 检修：engine run 收官 COMPLETED 的 INFO 日志。

背景：``_run_nodes`` 收尾此前只有 PAUSED / FAILED / CANCELLED 有日志，正常跑完
（最常见的路径）反而无任何 INFO 留痕，排障只能翻 DB。

突变化验（撤修复必红）：删掉 ``_run_nodes`` 收尾的 ``log.info(...COMPLETED...)``
后，本用例捕获不到含 run_id 的 COMPLETED 记录。
"""

from __future__ import annotations

import logging
from pathlib import Path

from packages.core.db import apply_migrations
from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowNode


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    apply_migrations(tmp_path / "test.db")
    return WorkflowEngine(str(tmp_path / "test.db"))


def test_run_nodes_logs_completed_with_workflow_and_stats(tmp_path: Path, caplog):
    engine = _make_engine(tmp_path)
    nodes = [
        WorkflowNode("step_one", "Transform", lambda ctx: {"one": 1}),
        WorkflowNode("step_two", "Transform", lambda ctx: {"two": 2}),
    ]

    with caplog.at_level(logging.INFO, logger="novelos.workflow_runtime"):
        run_id = engine.start_with_nodes("chapter-plan", nodes)

    completed = [
        r.getMessage()
        for r in caplog.records
        if r.name == "novelos.workflow_runtime"
        and "COMPLETED" in r.getMessage()
        and run_id in r.getMessage()
    ]
    assert completed, [r.getMessage() for r in caplog.records]
    msg = completed[-1]
    assert "workflow=chapter-plan" in msg
    assert "nodes=2" in msg
    assert "elapsed_ms=" in msg
