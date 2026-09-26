"""缺陷 1（高）复现：600s 等待超时后 RUNNING 透传。

证据：docs/testing/audit-workflows-engine-20260829.md 第 1 条。

待验证：
- _run_workflow_return_payload 在等待终态超时（run 仍 RUNNING）时：
  - 返回 payload["status"] == "RUNNING"
  - 返回 payload["timeout"] is True
  - 返回 payload["detail"] 含「超时」字样
- _auto_revise_loop 收到 timeout 标记的 write/review payload 时：
  - 不继续下一轮 write→review
  - 直接返回该 payload（含 logging.warning，已留痕）
- 常量 _RUN_WAIT_DEADLINE_SECONDS 存在且可注入。

修复后该测试应全部通过。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from packages.core.api.routers.workflows import (
    _run_workflow_return_payload,
)
from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.core.workflow_registry import register_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine


def _setup_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


# 永不到终态的占位节点：返回字典即可（WorkflowEngine 后台线程只是 sleep）
def _noop_node(ctx: dict) -> dict:
    import time
    time.sleep(0.05)
    return ctx


async def _wait_status(db_path: str, run_id: str, *expected: str, timeout: float = 5.0) -> str:
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        from packages.core.workflow_runtime.runs import get_run
        run = get_run(db_path, run_id)
        if run and run["status"] in expected:
            return run["status"]
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {expected}")


def test_constant_exposed_for_injection() -> None:
    """模块必须暴露 _RUN_WAIT_DEADLINE_SECONDS 常量，便于测试注入。"""
    from packages.core.api.routers import workflows
    assert hasattr(workflows, "_RUN_WAIT_DEADLINE_SECONDS"), (
        "缺少 _RUN_WAIT_DEADLINE_SECONDS 常量；测试无法注入短超时"
    )
    assert isinstance(workflows._RUN_WAIT_DEADLINE_SECONDS, (int, float))
    assert workflows._RUN_WAIT_DEADLINE_SECONDS > 0


def test_timeout_payload_marks_running_with_timeout_flag(tmp_path: Path, monkeypatch) -> None:
    """_run_workflow_return_payload 超时时返回 timeout=True + 人类可读 detail。"""
    settings = _setup_app(tmp_path)

    # 注册一个永不到终态的 workflow（只有一个节点，节点自身快速返回，
    # 但 run 行始终保持 RUNNING 因为没有写终态的逻辑——mock 场景下 run
    # 落到 RUNNING 后不被任何节点改为 COMPLETED）。
    register_workflow(
        "test-stub-never-terminal",
        builder=lambda: {
            "name": "test-stub-never-terminal",
            "nodes": [("noop_node", _noop_node)],
            "checkpoint_exclude": [],
        },
    )

    engine = WorkflowEngine(settings.db_path)
    # 直接调内部函数：等待期 600s 太长，将 deadline 参数化注入短超时。
    # chapter_id 传 None，避免触发 chapters 表 FK 约束（仅测试 stub）。
    payload = _run_workflow_return_payload(
        engine,
        settings.db_path,
        "test-stub-never-terminal",
        project_id="proj_does_not_matter",
        chapter_id=None,
        mock_providers=None,
        wait_deadline_seconds=0.5,  # 测试注入：500ms 超时
    )

    # 验收
    assert payload["status"] == "RUNNING", f"应保留 RUNNING 状态，实际={payload['status']!r}"
    assert payload.get("timeout") is True, "应标记 timeout=True"
    assert "超时" in payload.get("detail", ""), "detail 应说明超时"
    assert payload.get("run_id"), "应返回 run_id"


def _insert_review_parent_run(db_path: str) -> str:
    """落一行父 review run（FAILED rejected-for-revision + 可读的 review_report）。

    2026-09-18 起回路的动作由 ``repair_policy.decide_repair`` 按 pending review 的
    ``review_report`` 判定；这里给的是「报告无 error、作者主观驳回」形状（映射到 revise），
    让回路按既有节奏去启动 write 子 run——本测试要验的是那条路上的超时短路。
    """
    from packages.core.db import get_connection
    from packages.core.ids import new_id, now_iso

    report = {
        "word_count": 2500,
        "target_word_count": 2500,
        "within_range": True,
        "word_band": {"low": 2125, "high": 2875},
        "warnings": [],
        "errors": [],
        "ai_pattern_hits": [],
    }
    conn = get_connection(db_path)
    try:
        wf_row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'chapter-review'"
        ).fetchone()
        wf_id = wf_row["workflow_id"] if wf_row is not None else new_id("wf")
        if wf_row is None:
            now = now_iso()
            conn.execute(
                "INSERT INTO workflows "
                "(workflow_id, name, version, definition_json, created_at, updated_at) "
                "VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)",
                (wf_id, now, now),
            )
        run_id = new_id("wfr")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, NULL, 'FAILED', NULL, ?, 'rejected-for-revision', 0, ?, ?)
            """,
            (
                run_id,
                wf_id,
                json.dumps(
                    {
                        "author_review": {
                            "__pause_payload__": {"review_report": report}
                        }
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def test_auto_revise_loop_short_circuits_on_timeout(tmp_path: Path, monkeypatch, caplog) -> None:
    """_auto_revise_loop 收到 timeout 标记的 payload 时直接返回，不继续下一轮。

    策略：monkeypatch ``_run_workflow_return_payload`` 让它直接返回 timeout payload，
    不实际跑 chapter-write/review——验证 _auto_revise_loop 自身的短路逻辑。
    """
    settings = _setup_app(tmp_path)

    # monkeypatch 注入短超时到全局常量
    from packages.core.api.routers import workflows as wf_mod
    monkeypatch.setattr(wf_mod.common, "_RUN_WAIT_DEADLINE_SECONDS", 0.5)

    # 把 _run_workflow_return_payload 替换为：直接返回 timeout=True payload
    fake_payload = {
        "run_id": "fake_run_id",
        "status": "RUNNING",
        "current_node": None,
        "timeout": True,
        "detail": "测试注入：模拟子 run 超时",
    }
    monkeypatch.setattr(wf_mod.revise, "_run_workflow_return_payload", lambda *a, **kw: fake_payload)

    engine = WorkflowEngine(settings.db_path)

    # 监听 logging.warning
    caplog.set_level(logging.WARNING, logger="novelos.routers.workflows")

    parent = _insert_review_parent_run(str(settings.db_path))
    payload = wf_mod._auto_revise_loop(
        engine,
        settings.db_path,
        project_id="proj_x",
        chapter_id="ch_x",
        mock_providers=None,
        max_iter=3,
        parent_run_id=parent,
    )

    assert payload.get("status") == "RUNNING"
    assert payload.get("timeout") is True
    # 必须有 warning 留痕（搜索含 "auto_revise" 或 "timeout" 字样）
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("auto_revise" in m or "timeout" in m for m in warnings), (
        f"应有 logging.warning 留痕，实际 warnings={warnings}"
    )
