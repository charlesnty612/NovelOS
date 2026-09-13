"""M3 回归：resume 路径 checkpoint_exclude 复原（V3.9 全量检修）。

缺陷形状：``engine._checkpoint_exclude`` 只在 ``start_with_nodes`` /
``start_with_nodes_async`` 赋值。``resume`` / ``resume_async`` 走的是**新的**
WorkflowEngine 实例（API 层 ``_engine(request)`` 每请求新建），没有该属性 →
``getattr(self, "_checkpoint_exclude", None)`` 为 None → resume 后的
``_update_run_checkpoint`` / ``_finalize_run`` scrub 全部失效：被排除的大 payload
在 resume 阶段重新经节点镜像（``ctx[node_id] = output``）落回 checkpoint_json。

修法：resume 两路径从注册表按 run 反查 workflow 名取回 ``checkpoint_exclude``
（``get_workflow_name_for_run`` + ``workflow_registry.get_workflow``）后再
``_prepare_resume_ctx``。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.core.workflow_registry import register_workflow
from packages.core.workflow_runtime.engine import (
    PauseRequested,
    WorkflowEngine,
    WorkflowNode,
)
from packages.core.workflow_runtime.runs import get_run

_WF_NAME = "m3-resume-exclude-wf"
_EXCLUDE = ["big", "probe"]


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


def _register_wf() -> None:
    """注册与 run 名一致的 workflow 定义（引擎 resume 从注册表取 exclude）。"""
    register_workflow(
        _WF_NAME,
        lambda: {
            "name": _WF_NAME,
            "version": "v1",
            "nodes": [],
            "checkpoint_exclude": list(_EXCLUDE),
        },
    )


def _human_node() -> WorkflowNode:
    def _fn(_ctx):
        raise PauseRequested({"stage": "m3-gate", "message": "等待人工决议"})

    return WorkflowNode(node_id="human_gate", kind="Human", fn=_fn)


def test_resume_restores_checkpoint_exclude(tmp_path: Path) -> None:
    """PAUSED → resume（新引擎实例）→ 之后节点产生的被排除键不得落 checkpoint。"""
    _register_wf()
    engine = _make_engine(tmp_path)

    def _probe_fn(_ctx):
        # resume 后重放的大 payload：经 ctx["big"]（顶层 merge）与 ctx["probe"]
        # （节点镜像）两路进入 ctx；若 exclude 未复原，两者都会落 checkpoint。
        return {"big": "X" * 20_000, "small": 1}

    nodes = [
        _human_node(),
        WorkflowNode(node_id="probe", kind="State", fn=_probe_fn),
    ]

    run_id = engine.start_with_nodes(
        _WF_NAME,
        nodes,
        initial_ctx={"big": "seed" * 2_000, "keep": "yes"},
        checkpoint_exclude=_EXCLUDE,
    )

    paused = get_run(engine.db_path, run_id)
    assert paused is not None and paused["status"] == "PAUSED", paused
    # 起点对照：start 阶段 exclude 生效（顶层大键与镜像键都不落盘）
    ckpt_paused = paused["checkpoint_json"]
    assert "big" not in ckpt_paused, sorted(ckpt_paused)
    assert ckpt_paused.get("keep") == "yes"

    # resume：新引擎实例（模拟 API 层 _engine(request)），只给 run_id + nodes
    engine2 = _make_engine(tmp_path)
    engine2.resume(run_id, nodes, human_input={"approved": True})

    final = get_run(engine.db_path, run_id)
    assert final is not None and final["status"] == "COMPLETED", final
    ckpt_final = final["checkpoint_json"]

    # 撤修复必红：exclude 未复原时 "big"（20KB）与 "probe" 镜像会落回 checkpoint
    assert "big" not in ckpt_final, (
        f"resume 后 scrub 失效：被排除键 big 落回 checkpoint；keys={sorted(ckpt_final)}"
    )
    assert "probe" not in ckpt_final, (
        f"resume 后 scrub 失效：节点镜像键 probe 落回 checkpoint；keys={sorted(ckpt_final)}"
    )
    # 保留键不受影响
    assert ckpt_final.get("small") == 1
    assert ckpt_final.get("keep") == "yes"
    # 体积：20KB 大键未落盘
    assert len(json.dumps(ckpt_final, ensure_ascii=False).encode("utf-8")) < 10_000


def test_resume_async_restores_checkpoint_exclude(tmp_path: Path) -> None:
    """resume_async 同口径：后台线程推进的 checkpoint 也必须 scrub。"""
    _register_wf()
    engine = _make_engine(tmp_path)
    nodes = [
        _human_node(),
        WorkflowNode(
            node_id="probe", kind="State", fn=lambda _c: {"big": "Y" * 20_000, "small": 2}
        ),
    ]

    run_id = engine.start_with_nodes(
        _WF_NAME, nodes, initial_ctx={"keep": "yes"}, checkpoint_exclude=_EXCLUDE
    )
    engine2 = _make_engine(tmp_path)
    engine2.resume_async(run_id, nodes, human_input={"approved": True})

    deadline = time.monotonic() + 10.0
    final = None
    while time.monotonic() < deadline:
        final = get_run(engine.db_path, run_id)
        if final is not None and final["status"] in ("COMPLETED", "FAILED", "PAUSED"):
            break
        time.sleep(0.05)
    assert final is not None and final["status"] == "COMPLETED", final
    ckpt_final = final["checkpoint_json"]
    assert "big" not in ckpt_final, sorted(ckpt_final)
    assert "probe" not in ckpt_final, sorted(ckpt_final)


def test_resume_unknown_workflow_keeps_default_no_exclude(tmp_path: Path) -> None:
    """未注册 workflow（引擎裸用场景）→ 取不到清单，resume 不炸、行为同旧语义。"""
    engine = _make_engine(tmp_path)
    nodes = [
        _human_node(),
        WorkflowNode(node_id="probe", kind="State", fn=lambda _c: {"small": 1}),
    ]
    run_id = engine.start_with_nodes("m3-unregistered-wf", nodes)
    engine2 = _make_engine(tmp_path)
    engine2.resume(run_id, nodes, human_input={"approved": True})

    final = get_run(engine.db_path, run_id)
    assert final is not None and final["status"] == "COMPLETED", final
    assert final["checkpoint_json"].get("small") == 1
