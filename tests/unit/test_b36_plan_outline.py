"""批次三十六 · 批 5：plan / outline_batch / workflows API 域的 5 处修复看守。

覆盖（每条都断言**真实值**：API 状态码 / 落库 plan_json / 建库后的 chapters 列）：

- **C-8**：``chapter-plan`` 服务端状态闸门——只有 ``PLANNED`` 可启动；
  DRAFTED / REVIEWED / COMMITTED / RELEASED 一律 409，且**引擎未被调用**。
- **C-18**：``_save_plan_node`` 字数优先级——请求体显式 ``target_word_count`` 最高；
  缺省时原优先级（director > 继承 > 题材包 > 3000）逐位不变。
- **C-21**：``_director_planner_node`` 在**调用前**重填 ``payload["prompt_version"]``：
  DB 镜像落后于仓内源码时（ACTIVE=v2、仓内已到 v4），发给模型的声明必须是 v4。
- **C-24**：outline_batch 的未闭环状态集是 ``builders_common/ledger.py`` 的**同一对象**
  （撤掉单点 import、改回本地副本 ⇒ identity 断言必红）。
- **C-26**：``_assemble_node`` 的 payload ``prompt_version`` 现算 ACTIVE（不再是 v1 常量）。
- **C-27**：``_persist_node`` 新章挂项目第一卷；项目无卷 ⇒ NULL。

零真实 LLM 调用：AI 节点全部走 mock 或直接驱动节点函数（不触 provider）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException

import packages.workflows  # noqa: F401 —— 触发工作流注册（get_workflow 依赖）
from packages.core.agent_runtime.prompts import PromptRegistry, active_prompt_label
from packages.core.api.routers.workflows.common import StartWorkflowRequest
from packages.core.api.routers.workflows.control import (
    _guard_plan_chapter_status,
    _start_workflow_core,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_plan import pipeline as plan_pipeline
from packages.workflows.outline_batch import pipeline as outline_pipeline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "docs" / "agents" / "prompts"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "b36_b5.db"
    apply_migrations(db_path)
    return db_path


def _make_project(db_path: Path, name: str = "批次三十六测试书") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, status, "
            "created_at, updated_at) VALUES (?, ?, '前提', '玄幻', 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _make_chapter(
    db_path: Path,
    project_id: str,
    *,
    number: int = 1,
    status: str = "PLANNED",
    plan_json: str = "{}",
    title: str = "第一章",
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, project_id, number, title, plan_json, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _make_volume(db_path: Path, project_id: str, number: int, title: str = "") -> str:
    vid = new_id("vol")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO volumes (volume_id, project_id, number, title, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (vid, project_id, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return vid


def _read_plan(db_path: Path, chapter_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["plan_json"])


class _RecordingEngine:
    """只记录启动调用、不真正跑节点（API 闸门用例不需要后台线程）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def start_with_nodes_async(self, workflow_name: str, nodes, **kwargs):
        self.calls.append((workflow_name, kwargs))
        return "wfr_fake_b36"


# ---------------------------------------------------------------------------
# C-8：chapter-plan 状态闸门（服务端，API 级）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"])
def test_plan_endpoint_rejects_non_planned_chapter(tmp_path: Path, status: str):
    """非 PLANNED 章启动 chapter-plan ⇒ 409，错误串带**当前状态 + 允许状态**，
    且引擎一次都没被调用（拒绝发生在起 run 之前）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status=status)
    engine = _RecordingEngine()

    with pytest.raises(HTTPException) as ei:
        _start_workflow_core(
            workflow_name="chapter-plan",
            engine=engine,  # type: ignore[arg-type]
            db_path=str(db_path),
            project_id=pid,
            chapter_id=cid,
            body=StartWorkflowRequest(),
        )

    assert ei.value.status_code == 409, ei.value
    assert status in ei.value.detail, ei.value.detail
    assert "PLANNED" in ei.value.detail, ei.value.detail
    assert engine.calls == [], "闸门必须在起 run 之前拦下（否则 plan_json 已被排队改写）"


def test_plan_endpoint_allows_planned_chapter(tmp_path: Path):
    """PLANNED 章正常启动（正向对照：证明闸门不是「一律拒」）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="PLANNED", plan_json='{"chapter_goal": "旧计划"}')
    engine = _RecordingEngine()

    payload = _start_workflow_core(
        workflow_name="chapter-plan",
        engine=engine,  # type: ignore[arg-type]
        db_path=str(db_path),
        project_id=pid,
        chapter_id=cid,
        body=StartWorkflowRequest(target_word_count=2500),
    )

    assert payload["run_id"] == "wfr_fake_b36"
    assert payload["status"] == "RUNNING"
    assert len(engine.calls) == 1
    started_name, started_kwargs = engine.calls[0]
    assert started_name == "chapter-plan"
    assert started_kwargs["initial_ctx"]["target_word_count"] == 2500


def test_plan_status_gate_does_not_apply_to_other_workflows(tmp_path: Path):
    """闸门按 workflow_name 收口：write/review/commit 不受它影响（各自有专门校验）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    # 不抛即通过（直接调 helper，绕开各 workflow 的其它前置条件）。
    _guard_plan_chapter_status(str(db_path), "chapter-write", cid)
    _guard_plan_chapter_status(str(db_path), "chapter-review", cid)
    _guard_plan_chapter_status(str(db_path), "chapter-commit", cid)


# ---------------------------------------------------------------------------
# C-18：plan 落库字数优先级
# ---------------------------------------------------------------------------


def _run_save_plan(
    db_path: Path, pid: str, cid: str, output: dict, **ctx_extra
) -> dict:
    return plan_pipeline._save_plan_node(
        {
            "db_path": str(db_path),
            "project_id": pid,
            "chapter_id": cid,
            "run_id": "wfr_b36_save",
            "director_planner_output": output,
            **ctx_extra,
        }
    )


def test_save_plan_explicit_target_beats_director_output(tmp_path: Path):
    """ctx 显式 target=2500、director 回吐 3000 ⇒ 落库 2500（显式最高）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)

    _run_save_plan(
        db_path, pid, cid,
        {"chapter_goal": "目标", "expected_word_count": 3000},
        target_word_count=2500,
    )

    plan = _read_plan(db_path, cid)
    assert plan["expected_word_count"] == 2500, (
        f"显式 target 必须是最高优先级（实际 {plan['expected_word_count']}）"
    )


def test_save_plan_without_explicit_target_keeps_director_priority(tmp_path: Path):
    """ctx 无 target ⇒ 原优先级不变：director 3000 > 既有 1800。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, plan_json='{"expected_word_count": 1800}')

    _run_save_plan(
        db_path, pid, cid, {"chapter_goal": "目标", "expected_word_count": 3000},
    )

    assert _read_plan(db_path, cid)["expected_word_count"] == 3000


def test_save_plan_without_explicit_or_director_inherits_existing(tmp_path: Path):
    """ctx 无 target 且 director 未给 ⇒ 继承既有 1800。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, plan_json='{"expected_word_count": 1800}')

    _run_save_plan(db_path, pid, cid, {"chapter_goal": "目标"})

    assert _read_plan(db_path, cid)["expected_word_count"] == 1800


def test_save_plan_explicit_target_is_not_fooled_by_bool(tmp_path: Path):
    """``True`` 不是字数（bool 是 int 子类）⇒ 不得当 1 用，回落到 director 3000。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)

    _run_save_plan(
        db_path, pid, cid,
        {"chapter_goal": "目标", "expected_word_count": 3000},
        target_word_count=True,
    )

    assert _read_plan(db_path, cid)["expected_word_count"] == 3000


# ---------------------------------------------------------------------------
# C-21：director_planner payload 的 prompt_version 在调用前重填
# ---------------------------------------------------------------------------


def test_director_planner_node_refills_prompt_version_after_selfheal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """DB 镜像停在 v2（内容与仓内 v2 逐字一致）⇒ 节点调用前自愈到仓内 v4，
    发给模型的 payload 声明必须是 v4（而不是 build_ctx 阶段算出的 v2）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)

    # 用**仓内 v2 文件的字节副本**建立 ACTIVE=v2 —— 满足自愈的「忠实镜像」守卫。
    stale_docs = tmp_path / "prompts_v2_only"
    stale_docs.mkdir()
    shutil.copyfile(
        _PROMPTS_DIR / "director_planner-v2.md",
        stale_docs / "director_planner-v2.md",
    )
    PromptRegistry(db_path).sync_from_docs(stale_docs)
    assert active_prompt_label(
        db_path, "director_planner", fallback="director_planner:v2"
    ) == "director_planner:v2"

    captured: dict = {}

    def _fake_run_agent(db, agent_name, payload, run_id, **kwargs):
        captured["agent"] = agent_name
        captured["payload"] = payload
        return {"chapter_goal": "合成目标"}

    monkeypatch.setattr(plan_pipeline, "run_agent", _fake_run_agent)

    plan_pipeline._director_planner_node(
        {
            "db_path": str(db_path),
            "project_id": pid,
            "chapter_id": cid,
            "run_id": "wfr_b36_dir",
            # build_ctx 阶段的**陈旧声明**：模拟「装配时 DB 还停在 v2」。
            "director_planner_input": {
                "agent": "director_planner",
                "prompt_version": "director_planner:v2",
            },
            "mock_providers": {"director_planner": ["{}"]},
        }
    )

    assert captured["agent"] == "director_planner"
    assert captured["payload"]["prompt_version"] == "director_planner:v4", (
        "prompt_version 必须在调用前重填为自愈后的 ACTIVE（否则模型回声/落库三面分叉）"
    )
    # 自愈确实推进了 DB（不是只改了 payload 字符串）
    assert active_prompt_label(
        db_path, "director_planner", fallback="x"
    ) == "director_planner:v4"


def test_director_planner_node_respects_prompt_sync_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``NOVELOS_PROMPT_SYNC=off`` ⇒ 不推进（与 runner 同一开关），声明保持 v2。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    stale_docs = tmp_path / "prompts_v2_only_off"
    stale_docs.mkdir()
    shutil.copyfile(
        _PROMPTS_DIR / "director_planner-v2.md",
        stale_docs / "director_planner-v2.md",
    )
    PromptRegistry(db_path).sync_from_docs(stale_docs)

    monkeypatch.setenv("NOVELOS_PROMPT_SYNC", "off")
    captured: dict = {}

    def _fake(db, agent, payload, run_id, **kw):
        captured["p"] = payload
        return {}

    monkeypatch.setattr(plan_pipeline, "run_agent", _fake)

    plan_pipeline._director_planner_node(
        {
            "db_path": str(db_path),
            "project_id": pid,
            "chapter_id": cid,
            "run_id": "wfr_b36_dir_off",
            "director_planner_input": {"prompt_version": "director_planner:v2"},
        }
    )

    assert captured["p"]["prompt_version"] == "director_planner:v2"
    assert active_prompt_label(
        db_path, "director_planner", fallback="x"
    ) == "director_planner:v2"


# ---------------------------------------------------------------------------
# C-24 / C-26 / C-27：outline_batch
# ---------------------------------------------------------------------------


def test_outline_batch_open_statuses_are_the_single_source():
    """C-24：未闭环状态集必须与 builders_common/ledger.py 是**同一个对象**。

    identity 断言（``is``）比「内容相等」更硬：本地再抄一份 tuple/frozenset，
    内容相等但对象不同 ⇒ 本用例红——正是「两侧各改一半就静默分叉」的防线。
    """
    from packages.core.context_engine.builders_common import ledger

    assert outline_pipeline._HOOK_OPEN_STATUSES is ledger._HOOK_OPEN_STATUSES
    assert outline_pipeline._DEBT_OPEN_STATUSES is ledger._DEBT_OPEN_STATUSES


def _assemble_ctx(db_path: Path, pid: str) -> dict:
    return {
        "db_path": str(db_path),
        "project_id": pid,
        "chapter_count": 2,
    }


def test_assemble_node_prompt_version_follows_active_not_constant(tmp_path: Path):
    """C-26：payload 声明 = ACTIVE 现算值（v2），不再是硬编码 v1。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    # 自造 v2（仓内没有 outline_batch_planner-v2.md ⇒ 自愈不会推进/覆盖）。
    docs = tmp_path / "outline_prompts_v2"
    docs.mkdir()
    (docs / "outline_batch_planner-v2.md").write_text("# 自造 v2\n", encoding="utf-8")
    PromptRegistry(db_path).sync_from_docs(docs)

    out = outline_pipeline._assemble_node(_assemble_ctx(db_path, pid))

    assert out["outline_batch_input"]["prompt_version"] == "outline_batch_planner:v2", (
        "payload 的 prompt_version 必须随 ACTIVE 走（撤掉现算 ⇒ 退回常量 v1）"
    )


def _persist_ctx(db_path: Path, pid: str, start: int = 1, count: int = 2) -> dict:
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章",
            "synopsis": f"第{start + i}章梗概（批次三十六 C-27 用例）",
        }
        for i in range(count)
    ]
    return {
        "db_path": str(db_path),
        "project_id": pid,
        "outline_batch_input": {
            "chapter_count": count,
            "start_number": start,
        },
        "outline_batch_planner_output": {"schema_version": 1, "chapter_seeds": seeds},
    }


def test_persist_node_assigns_first_volume(tmp_path: Path):
    """C-27：项目有卷 ⇒ 新章挂 ``volumes.number`` 最小的那一卷（不是随便一卷）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_volume(db_path, pid, number=2, title="第二卷")
    first = _make_volume(db_path, pid, number=1, title="第一卷")

    out = outline_pipeline._persist_node(_persist_ctx(db_path, pid, start=1, count=2))
    assert out["persisted"] is True

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT number, volume_id FROM chapters WHERE project_id = ? ORDER BY number",
            (pid,),
        ).fetchall()
    finally:
        conn.close()
    assert [r["number"] for r in rows] == [1, 2]
    assert all(r["volume_id"] == first for r in rows), [
        (r["number"], r["volume_id"]) for r in rows
    ]


def test_persist_node_without_volumes_keeps_null(tmp_path: Path):
    """C-27：项目无卷 ⇒ ``volume_id`` 保持 NULL（volume_scope 的「未挂卷」哨兵语义）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)

    outline_pipeline._persist_node(_persist_ctx(db_path, pid, start=1, count=1))

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT volume_id FROM chapters WHERE project_id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["volume_id"] is None
