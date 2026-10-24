"""outline-batch workflow 测试（批量续规划下一批章节）。

零真实 LLM 调用：planner 节点全部走 mock_providers 脚本。

覆盖：
- 正常链：mock 合法 seeds ⇒ 章节表 +N、number 连续、status 全 PLANNED、
  plan_json 的 source 标记正确、outline_json 存 seed 原文；
- mock 回断号 seeds ⇒ AgentOutputError（run FAILED），章节表零新增；
- mock 回数量不符 seeds ⇒ 同上；
- 已有 10 章时 start_number=11；
- 注册看守：outline-batch 在工作流注册表中；未注册名返回 None（反向）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run

from ._shared import _make_engine

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"


def _make_project(db_path, name: str = "批量续规划测试书") -> str:
    conn = get_connection(db_path)
    try:
        pid = new_id("prj")
        now = now_iso()
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, name, "少年得古籍，逆天改命", "玄幻", now, now),
        )
        conn.commit()
        return pid
    finally:
        conn.close()


def _make_chapter(db_path, project_id: str, number: int, title: str = "") -> str:
    conn = get_connection(db_path)
    try:
        cid = new_id("ch")
        now = now_iso()
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'COMMITTED', ?, ?)",
            (cid, project_id, number, title, now, now),
        )
        conn.commit()
        return cid
    finally:
        conn.close()


def _list_chapters(db_path, project_id: str) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT chapter_id, number, title, status, plan_json, outline_json "
            "FROM chapters WHERE project_id = ? ORDER BY number ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _planner_script(start: int, count: int) -> list[str]:
    """合法 seeds 脚本：number 从 start 起连续 count 个。"""
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 集市暗桩",
            "synopsis": (
                f"主角在旧城集市追查线索（第{start + i}章），撞上暗桩伏击，"
                "逼出幕后接引人的一角；情绪由疑惑转向警觉，"
                "同时推进铜哨伏笔的回收；章末停在接引人遁走、身份成谜的悬念上。"
            ),
        }
        for i in range(count)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _gapped_planner_script(start: int, count: int) -> list[str]:
    """断号 seeds 脚本：跳过 start+2 一章（数量仍够但 number 不连续）。"""
    seeds = [
        {
            "number": start + i if i != 2 else start + i + 1,
            "title": f"第{start + i}章 断号",
            "synopsis": "断号测试用梗概，足够长以通过非空校验，但 number 序列不连续。",
        }
        for i in range(count)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _short_count_planner_script(start: int, actual: int) -> list[str]:
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 数量不足",
            "synopsis": "数量不足测试用梗概，足够长以通过非空校验。",
        }
        for i in range(actual)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _start_outline_batch(
    engine: WorkflowEngine,
    project_id: str,
    *,
    mock_providers: dict[str, list[str]] | None,
    chapter_count: int = 5,
) -> str:
    workflow = get_workflow("outline-batch")
    assert workflow is not None
    return engine.start_with_nodes(
        "outline-batch",
        workflow["nodes"],
        initial_ctx={
            "db_path": str(engine.db_path),
            "project_id": project_id,
            "chapter_count": chapter_count,
        },
        mock_providers=mock_providers,
    )


def test_outline_batch_end_to_end_creates_planned_chapters(tmp_path: Path):
    """正常链：空项目 mock 合法 seeds ⇒ +5 章、number 1..5、PLANNED、source 标记正确。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _planner_script(1, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    chapters = _list_chapters(engine.db_path, pid)
    assert len(chapters) == 5
    assert [c["number"] for c in chapters] == [1, 2, 3, 4, 5]
    assert all(c["status"] == "PLANNED" for c in chapters)
    for c in chapters:
        plan = json.loads(c["plan_json"])
        assert plan["source"] == "outline-batch"
        assert plan["synopsis"]
        assert plan["prompt_version"] == "outline_batch_planner:v1"
        # outline_json = seed 原文结构（number/title/synopsis 逐位可对回 mock 脚本）
        seed = json.loads(c["outline_json"])
        assert seed["number"] == c["number"]
        assert seed["title"] == c["title"]
        assert seed["synopsis"] == plan["synopsis"]
    # checkpoint 带 created chapter_ids
    ckpt = run.get("checkpoint_json") or {}
    persist_out = ckpt.get("persist") or {}
    assert len(persist_out.get("created_chapter_ids") or []) == 5


def test_outline_batch_start_number_after_existing_chapters(tmp_path: Path):
    """已有 10 章（编号 1..10）时 start_number=11：新章 11..15，老章不动。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)
    for i in range(1, 11):
        _make_chapter(engine.db_path, pid, i, f"既有第{i}章")

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _planner_script(11, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    chapters = _list_chapters(engine.db_path, pid)
    assert len(chapters) == 15
    assert [c["number"] for c in chapters] == list(range(1, 16))
    new_rows = [c for c in chapters if c["number"] >= 11]
    assert all(c["status"] == "PLANNED" for c in new_rows)
    old_rows = [c for c in chapters if c["number"] <= 10]
    assert all(c["status"] == "COMMITTED" for c in old_rows)


def test_outline_batch_gapped_numbers_fail(tmp_path: Path):
    """mock 回断号 seeds ⇒ AgentOutputError（run FAILED），章节表零新增。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _gapped_planner_script(1, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert "consecutive" in str(run.get("error") or "")
    assert _list_chapters(engine.db_path, pid) == []


def test_outline_batch_wrong_count_fail(tmp_path: Path):
    """mock 回数量不符 seeds（要 5 回 3）⇒ run FAILED，章节表零新增。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _short_count_planner_script(1, 3)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert "expected 5 chapter_seeds, got 3" in str(run.get("error") or "")
    assert _list_chapters(engine.db_path, pid) == []


def test_outline_batch_registered_and_unknown_names_are_none():
    """注册看守：outline-batch 在注册表中（3 节点线性）；未注册名返回 None。"""
    wf = get_workflow("outline-batch")
    assert wf is not None
    assert wf["name"] == "outline-batch"
    assert [n.node_id for n in wf["nodes"]] == ["assemble", "planner", "persist"]
    # 反向：未注册名不得假装存在（对应端点的「未注册 500」判据）
    assert get_workflow("outline-batch-not-registered") is None
