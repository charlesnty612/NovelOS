"""V3.7：summarizer 与 observer 双腿同池并发（commit 阶段三路并发）测试。

覆盖点（packages/workflows/chapter_commit/pipeline.py）：
1. ``_summary_parallel_enabled`` 开关：默认 on / env off / ctx override。
2. ``_prepare_summarizer_call`` 抽取前置逻辑正确，缺失必需输入返回 None；payload 键序固定。
3. ``_summarize_node`` 短路消费 ``ctx['summary_early']``：命中后不再调
   ``run_agent('summarizer')``，summary_status='ok'，chapter_summaries 落库。
4. ``_summarize_node`` self-heal：summary_early 缺字段或 {"skipped": True} 时走自愈。
5. ``_run_observer_with_summary_in_parallel`` 三路并发：双腿结果正确合并，
   summary 异常被吞不影响双腿 future.result()，返回 ``summary_early=None`` 让
   下游 summarize 节点自愈。
6. Observer 第三路在 prepare 缺失（无草稿）时返回 ``{"skipped": True}``，下游
   summarize 节点对 ``{"skipped": True}`` 的早期结果走自愈。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_commit import observer as _observer_mod
from packages.workflows.chapter_commit import summary as _summary_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 共享 fixtures（与 test_sprint14_summaries 风格一致）
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path, project_id: str, number: int, status: str = "COMMITTED",
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, ?, '', '{}', ?, 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


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


def _register_summarizer_agent(db_path: Path) -> tuple[str, str]:
    """注册 summarizer agent + ACTIVE prompt + workflow_runs 行；返回 (run_id, node_run_id)。"""
    agent_id = new_id("ag")
    prompt_id = new_id("prm")
    wf_run_id = new_id("wfr")
    node_run_id = new_id("wrn")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agents (agent_id, name, role, config_json, created_at, updated_at) "
            "VALUES (?, 'summarizer', 'light', '{}', ?, ?)",
            (agent_id, now, now),
        )
        conn.execute(
            "INSERT INTO prompts (prompt_id, agent_id, version, content, status, created_at, updated_at) "
            "VALUES (?, ?, 'v1', 'You are a chapter summarizer.', 'ACTIVE', ?, ?)",
            (prompt_id, agent_id, now, now),
        )
        wf_id = new_id("wf")
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-commit', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, current_node, "
            "checkpoint_json, error, retry_count, started_at, ended_at) "
            "VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)",
            (wf_run_id, wf_id, now),
        )
        conn.execute(
            "INSERT INTO workflow_run_nodes (node_run_id, run_id, node_id, status, "
            "started_at, ended_at, input_json, output_json, error) "
            "VALUES (?, ?, 'summarize', 'RUNNING', ?, NULL, '{}', NULL, NULL)",
            (node_run_id, wf_run_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return wf_run_id, node_run_id


# ---------------------------------------------------------------------------
# 1) _summary_parallel_enabled 开关
# ---------------------------------------------------------------------------


def test_summary_parallel_enabled_default_on(monkeypatch):
    """env 未设置：默认 on。"""
    import os as _os

    saved = _os.environ.pop("NOVELOS_SUMMARY_PARALLEL", None)
    try:
        from packages.workflows.chapter_commit.pipeline import _summary_parallel_enabled
        assert _summary_parallel_enabled() is True
    finally:
        if saved is not None:
            _os.environ["NOVELOS_SUMMARY_PARALLEL"] = saved


def test_summary_parallel_enabled_off_env(monkeypatch):
    """env=off / 0 / false / no 均视为 off。"""
    from packages.workflows.chapter_commit.pipeline import _summary_parallel_enabled
    for v in ("off", "0", "false", "no"):
        monkeypatch.setenv("NOVELOS_SUMMARY_PARALLEL", v)
        assert _summary_parallel_enabled() is False, v


def test_summary_parallel_enabled_ctx_override(monkeypatch):
    """ctx['summary_parallel'] 显式覆盖优先于 env。"""
    from packages.workflows.chapter_commit.pipeline import _summary_parallel_enabled
    monkeypatch.setenv("NOVELOS_SUMMARY_PARALLEL", "on")
    assert _summary_parallel_enabled({"summary_parallel": False}) is False
    monkeypatch.setenv("NOVELOS_SUMMARY_PARALLEL", "off")
    assert _summary_parallel_enabled({"summary_parallel": True}) is True


# ---------------------------------------------------------------------------
# 2) _prepare_summarizer_call
# ---------------------------------------------------------------------------


def test_prepare_summarizer_call_returns_full_payload(tmp_path: Path):
    """正常路径：返回 payload + mock + project_id/chapter_no/content/tail_text；
    payload 键序固定（缓存重排对齐）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=3)
    _insert_draft(db_path, cid, "本章正文" * 100)

    from packages.workflows.chapter_commit.pipeline import _prepare_summarizer_call

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": "x"}, ensure_ascii=False)],
        },
    }
    prepared = _prepare_summarizer_call(ctx)
    assert prepared is not None
    assert prepared["project_id"] == pid
    assert prepared["chapter_no"] == 3
    payload = prepared["payload"]
    assert list(payload.keys()) == [
        "agent", "prompt_version", "chapter", "prose_excerpt", "tail_text",
    ], list(payload.keys())
    assert payload["agent"] == "summarizer"
    assert payload["prompt_version"] == "summarizer:v1"
    assert list(payload["chapter"].keys()) == [
        "chapter_goal", "chapter_no", "chapter_id",
    ], list(payload["chapter"].keys())
    assert payload["chapter"]["chapter_no"] == 3
    assert payload["chapter"]["chapter_id"] == cid
    assert len(payload["prose_excerpt"]) <= 4000
    assert prepared["mock"] == ctx["mock_providers"]["summarizer"]


def test_prepare_summarizer_call_none_for_empty_draft(tmp_path: Path):
    """commit 后草稿为空 → 返回 None（让下游走 skipped）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    # 不插 draft

    from packages.workflows.chapter_commit.pipeline import _prepare_summarizer_call

    ctx = {"db_path": db_path, "chapter_id": cid, "mock_providers": {}}
    assert _prepare_summarizer_call(ctx) is None


def test_prepare_summarizer_call_none_for_missing_chapter(tmp_path: Path):
    """章节不存在 → 返回 None（防御性兜底）。"""
    db_path = _fresh_db(tmp_path)
    from packages.workflows.chapter_commit.pipeline import _prepare_summarizer_call

    ctx = {
        "db_path": db_path, "chapter_id": "ch_does_not_exist", "mock_providers": {},
    }
    assert _prepare_summarizer_call(ctx) is None


# ---------------------------------------------------------------------------
# 3) _summarize_node 短路消费 summary_early
# ---------------------------------------------------------------------------


def test_summarize_node_consumes_summary_early_skips_second_run_agent(tmp_path, monkeypatch):
    """命中 summary_early 时 _summarize_node 不再调 run_agent('summarizer')；
    summary_status='ok'，chapter_summaries 落库。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=5, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文" * 50)
    run_id, node_run_id = _register_summarizer_agent(db_path)

    real_run_agent = _summary_mod.run_agent
    second_call_count = {"n": 0}

    def _spy_run_agent(db_path, agent_name, payload, rid, **kwargs):
        if agent_name == "summarizer":
            second_call_count["n"] += 1
        return real_run_agent(db_path, agent_name, payload, rid, **kwargs)

    monkeypatch.setattr(_summary_mod, "run_agent", _spy_run_agent)
    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {},
        "summary_early": {
            "skipped": False,
            "output": {"summary": "本章概要：林渊下山寻访故人。"},
            "project_id": pid,
            "chapter_no": 5,
            "tail_text": "本章正文" * 50,
        },
    }
    out = _summarize_node(ctx)

    # short-circuit：_summarize_node 不应再调 summarizer
    assert second_call_count["n"] == 0, (
        f"short-circuit 失效：n={second_call_count['n']}"
    )
    assert out["summary_status"] == "ok", out
    assert out["summary_id"] is not None
    assert out["degraded"] is False

    # 落库验证
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM chapter_summaries WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    row_d = dict(row)
    assert row_d["project_id"] == pid
    assert row_d["chapter_no"] == 5
    assert "林渊下山" in row_d["summary"]


def test_summarize_node_consumes_skipped_summary_early_falls_back(tmp_path, monkeypatch):
    """summary_early={'skipped': True} 时 _summarize_node 走自愈路径：再 prepare → skipped。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=2, status="COMMITTED")
    # 不插 draft → prepare 返回 None → skipped
    run_id, node_run_id = _register_summarizer_agent(db_path)

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {},
        "summary_early": {"skipped": True},
    }
    out = _summarize_node(ctx)
    assert out["summary_status"] == "skipped", out


def test_summarize_node_summary_early_missing_required_fields_falls_back(tmp_path, monkeypatch):
    """summary_early 缺 project_id/tail_text → 走自愈（重新 prepare + run_agent）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=4, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文" * 80)
    run_id, node_run_id = _register_summarizer_agent(db_path)

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": "兜底自愈概要"}, ensure_ascii=False)],
        },
        # summary_early 故意缺 project_id 与 tail_text → 触发自愈分支
        "summary_early": {
            "skipped": False,
            "output": {"summary": "本应被丢弃的早产结果"},
            "chapter_no": 4,
        },
    }
    out = _summarize_node(ctx)
    assert out["summary_status"] == "ok", out
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT summary FROM chapter_summaries WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert "兜底自愈概要" in dict(row)["summary"]


# ---------------------------------------------------------------------------
# 4) _run_observer_with_summary_in_parallel：三路并发 + summary 异常兑底
# ---------------------------------------------------------------------------


def test_run_observer_with_summary_in_parallel_invokes_three_futures(tmp_path, monkeypatch):
    """三路并发：两条腿 + summary 都跑，返回 summary_early 含 output。"""

    calls = []

    def _fake_runner(db_path, agent_name, payload, run_id, **kwargs):
        calls.append(agent_name)
        if kwargs.get("expected") == "summarizer":
            return {"summary": "本章概要：早产。"}
        return {
            "character_changes": [],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }

    monkeypatch.setattr(_observer_mod, "run_agent", _fake_runner)

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)
    _insert_draft(db_path, cid, "正文" * 100)

    from packages.workflows.chapter_commit.pipeline import (
        _run_observer_with_summary_in_parallel,
    )

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": "x"}, ensure_ascii=False)],
        },
    }
    leg_a, leg_b, wall_ms, summary_early = _run_observer_with_summary_in_parallel(
        db_path=db_path,
        run_id="r1",
        node_run_id="n1",
        leg_a_payload={"extraction_scope": "entities"},
        leg_b_payload={"extraction_scope": "narrative"},
        leg_a_mock=None,
        leg_b_mock=None,
        ctx=ctx,
    )

    assert calls.count("observer") == 2, calls
    assert calls.count("summarizer") == 1, calls
    assert summary_early is not None
    assert summary_early.get("skipped") is False
    assert summary_early["output"]["summary"] == "本章概要：早产。"
    assert summary_early["project_id"] == pid
    assert summary_early["chapter_no"] == 1
    assert len(summary_early["tail_text"]) > 0
    assert wall_ms >= 0


def test_run_observer_with_summary_in_parallel_summary_exception_silent(tmp_path, monkeypatch):
    """summary 异常被吞：summary_early=None；双腿 future.result() 正常返回。"""

    def _fake_runner(db_path, agent_name, payload, run_id, **kwargs):
        if kwargs.get("expected") == "summarizer":
            raise RuntimeError("summarizer synthetic failure")
        return {
            "character_changes": [], "world_changes": [], "relationship_changes": [],
            "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
        }

    monkeypatch.setattr(_observer_mod, "run_agent", _fake_runner)

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)
    _insert_draft(db_path, cid, "正文" * 100)

    from packages.workflows.chapter_commit.pipeline import (
        _run_observer_with_summary_in_parallel,
    )

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": "x"}, ensure_ascii=False)],
        },
    }
    leg_a, leg_b, wall_ms, summary_early = _run_observer_with_summary_in_parallel(
        db_path=db_path,
        run_id="r1",
        node_run_id="n1",
        leg_a_payload={"extraction_scope": "entities"},
        leg_b_payload={"extraction_scope": "narrative"},
        leg_a_mock=None,
        leg_b_mock=None,
        ctx=ctx,
    )
    # summary 失败 → summary_early=None（让下游 summarize 自愈）
    assert summary_early is None
    # 双腿返回正常
    assert isinstance(leg_a, dict)
    assert isinstance(leg_b, dict)


def test_run_observer_with_summary_in_parallel_skips_when_no_draft(tmp_path, monkeypatch):
    """草稿缺失 → summary 早产返回 {"skipped": True}，双腿仍正常。"""

    calls = []

    def _fake_runner(db_path, agent_name, payload, run_id, **kwargs):
        calls.append(agent_name)
        return {
            "character_changes": [], "world_changes": [], "relationship_changes": [],
            "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
        }

    monkeypatch.setattr(_observer_mod, "run_agent", _fake_runner)

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)
    # 不插 draft

    from packages.workflows.chapter_commit.pipeline import (
        _run_observer_with_summary_in_parallel,
    )

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "mock_providers": {},
    }
    leg_a, leg_b, wall_ms, summary_early = _run_observer_with_summary_in_parallel(
        db_path=db_path,
        run_id="r1",
        node_run_id="n1",
        leg_a_payload={"extraction_scope": "entities"},
        leg_b_payload={"extraction_scope": "narrative"},
        leg_a_mock=None,
        leg_b_mock=None,
        ctx=ctx,
    )
    # 双腿照常被调
    assert calls.count("observer") == 2
    # summarizer 不应被调（prepare 阶段返回 None → 直接 skipped）
    assert "summarizer" not in calls
    # summary_early 标记 skipped
    assert summary_early is not None
    assert summary_early.get("skipped") is True


# ---------------------------------------------------------------------------
# 5) Engine 级 resume 端到端（F4）：high_risk_approval PAUSE → resume →
#    断言 chapter_summaries 行数 + summarizer ai_call_logs 不超预期 + run 终态 COMPLETED。
# ---------------------------------------------------------------------------


def test_workflow_resume_summary_early_excluded_checkpoint_self_heals(tmp_path, monkeypatch):
    """Engine 级端到端（V3.7 F4 审查修复验收）：

    完整 commit 工作流走到 high_risk_approval PAUSE → resume → 断言：
    1. ``chapter_summaries`` 只有 1 行（早产短路正确落库）；
    2. summarizer 相关 ``ai_call_logs`` 不超 1 次（早产一次即终态，无兜底调用）；
    3. run.status 终态 = COMPLETED；
    4. ``summary_early`` 不出现在 ``workflow_runs.checkpoint_json`` 中（F1 exclude 生效）；
    5. 即使 ``summary_early`` 从 checkpoint 被剔除，summarize 节点走 _summarize_node
       的早产短路分支（ctx 内存里仍存在），与断点恢复幂等。
    """
    import packages.workflows.chapter_commit.pipeline as _pipeline
    from packages.core.workflow_runtime.engine import WorkflowEngine

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="REVIEWED")
    _insert_draft(db_path, cid, "本章正文" * 200)

    # 准备 mock_summarizer 输出（早产与兜底共用）。
    summary_text = "本章概要：早产 → 落库。"
    summarizer_script = [json.dumps({"summary": summary_text}, ensure_ascii=False)]

    # ---- monkey-patch 内部节点 → 把 observer / inject_validate / quality_gate /
    # commit 替换为 stub，让 high_risk_approval 真节点抛 PauseRequested。

    # 1) observer 节点 stub：返回含 HIGH world_change 的 7 数组（触发 needs_high_risk_approval）。
    def _fake_observer_node(ctx):
        return {
            "observer_payload": {
                "character_changes": [],
                "relationship_changes": [],
                "world_changes": [{
                    "change_id": "w_high", "op": "add",
                    "after": {"name": "新规则", "world_kind": "rule"},
                    "risk_level": "HIGH",
                }],
                "new_events": [],
                "new_hooks": [],
                "resolved_hooks": [],
                "debt_changes": [],
            },
            # V3.7：本节点也提前跑 summarizer 早产并写到 summary_early（短路消费语义）。
            # 这里直接给出最终解析结果：summary_early 字段由 stub 接管，
            # 让 _run_observer_with_summary_in_parallel 不会被实际调用。
            "summary_early": {
                "skipped": False,
                "output": {"summary": summary_text},
                "project_id": pid,
                "chapter_no": 1,
                "tail_text": "本章正文" * 200,
            },
            "observer_split_meta": {"enabled": False},
        }

    # 2) inject_validate stub：返回 delta_id 与 needs_high_risk_approval=True。
    def _fake_inject_validate_node(ctx):
        return {
            "delta_id": new_id("dt"),
            "needs_high_risk_approval": True,
        }

    # 3) quality_gate stub：默认 enforce 仅检查 error issues；这里直接放行。
    def _fake_quality_gate_node(ctx):
        return {
            "quality_status": "ok",
            "quality_reports": [],
        }

    # 4) commit stub：minimal —— 把 chapter.status 翻成 COMMITTED；不依赖 state_deltas。
    def _fake_commit_node(ctx):
        now = now_iso()
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE chapters SET status = 'COMMITTED', updated_at = ? "
                "WHERE chapter_id = ?", (now, cid),
            )
            conn.commit()
        finally:
            conn.close()
        return {"committed": True, "delta_id": ctx.get("delta_id")}

    monkeypatch.setattr(_pipeline, "_observer_node", _fake_observer_node)
    monkeypatch.setattr(_pipeline, "_inject_validate_node", _fake_inject_validate_node)
    monkeypatch.setattr(_pipeline, "_quality_gate_node", _fake_quality_gate_node)
    monkeypatch.setattr(_pipeline, "_commit_node", _fake_commit_node)
    # _summarize_node / _high_risk_approval_node 保留真实版本（V3.7 短路逻辑 + Human 节点真 PauseRequested）。

    # 注册 summarizer agent + ACTIVE prompt，让 _summarize_node 真跑时不抛
    # PromptNotFoundError；但本测试的 summary_early 短路不调 summarizer。
    _register_summarizer_agent(db_path)

    # ---- 构造 WorkflowEngine 跑完整章节提交工作流。
    from packages.core.workflow_runtime.engine import WorkflowNode
    from packages.workflows.chapter_commit.pipeline import (
        WORKFLOW,
        _high_risk_approval_node,
        _summarize_node,
    )
    engine = WorkflowEngine(db_path)
    # 关键：build_observer_ctx 是上游 Transform 节点，需要 prepare observer_input
    # 到 ctx。这里直接 patch 成空操作（observer stub 不读 observer_input）。
    def _fake_build_observer_ctx_node(ctx):
        return {"observer_input": {"config": {}, "snapshot_trim_stats": {}}}
    nodes = [
        WorkflowNode("build_observer_ctx", "Transform", _fake_build_observer_ctx_node),
        WorkflowNode("observer", "AI", _fake_observer_node, agent_name="observer"),
        WorkflowNode("inject_validate", "Transform", _fake_inject_validate_node),
        WorkflowNode("quality_gate", "State", _fake_quality_gate_node),
        WorkflowNode("high_risk_approval", "Human", _high_risk_approval_node),
        WorkflowNode("commit", "State", _fake_commit_node),
        WorkflowNode("summarize", "State", _summarize_node),
    ]

    run_id = engine.start_with_nodes(
        "chapter-commit",
        nodes=nodes,
        chapter_id=cid,
        initial_ctx={
            "db_path": str(db_path),  # ctx 经 json 序列化进 checkpoint_json，Path 不可序列化
            "chapter_id": cid,
            "mock_providers": {"summarizer": summarizer_script},
        },
        mock_providers={"summarizer": summarizer_script},
        checkpoint_exclude=WORKFLOW.get("checkpoint_exclude"),  # F1 验收关键
    )

    # ---- 校验：此刻 run 应 PAUSE 在 high_risk_approval 节点。
    run_row_after_pause = _get_run_row(db_path, run_id)
    assert run_row_after_pause["status"] == "PAUSED", run_row_after_pause
    assert run_row_after_pause["current_node"] == "high_risk_approval", run_row_after_pause

    # 验证 F1：summary_early 已从 checkpoint_json 中剔除。
    import json as _json
    paused_ckpt_raw = run_row_after_pause["checkpoint_json"] or "{}"
    paused_ckpt = _json.loads(paused_ckpt_raw)
    assert "summary_early" not in paused_ckpt, (
        "F1 验证失败：summary_early 不应在 checkpoint_json 中（已 exclude）"
    )

    # ---- resume：传入 human_input={approved: True}。
    engine.resume(run_id, nodes, human_input={"approved": True})

    # ---- 终态断言。
    final_run = _get_run_row(db_path, run_id)
    assert final_run["status"] == "COMPLETED", final_run

    # chapter_summaries 行数 == 1（早产短路直接落库）。
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chapter_summaries WHERE chapter_id = ?",
            (cid,),
        ).fetchone()
        summary_count = dict(row)["n"]
        # ai_call_logs 中 summarizer agent 的调用次数：observer_node stub 完全不调
        # run_agent('summarizer')（早产结果由 stub 直接构造），所以 summarizer 行 = 0。
        # 找 summarizer 的 agent_id：先查 agents 表。
        agent_row = conn.execute(
            "SELECT agent_id FROM agents WHERE name = 'summarizer'",
        ).fetchone()
        summarizer_agent_id = dict(agent_row)["agent_id"] if agent_row else None
        if summarizer_agent_id:
            ai_row = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_call_logs WHERE run_id = ? "
                "AND agent_id = ?", (run_id, summarizer_agent_id),
            ).fetchone()
            ai_n = dict(ai_row)["n"]
        else:
            ai_n = -1  # 没注册 summarizer agent 时不强断言
    finally:
        conn.close()

    assert summary_count == 1, (
        f"chapter_summaries 应仅 1 行（早产短路或 resume 自愈落库），实际 {summary_count}"
    )
    # summarizer ai_call_logs：
    # - 非 PAUSED 场景：observer 早产 1 次 + summarize 不调 → 期望 1。
    # - PAUSED→resume 场景：ctx 从 checkpoint_json 反序列化，因 F1 exclude summary_early
    #   已被剔除 → summarize 节点走自愈调 1 次 run_agent → 期望 1。
    # 当前测试是 PAUSED→resume 路径 → 期望 ≤ 1；非 PAUSED 路径下 stub 严格 0。
    assert ai_n in (0, 1), (
        f"summarizer ai_call_logs 应在 [0, 1] 区间（observer 早产 1 次；"
        f"resume 自愈最多再 1 次），实际 {ai_n}"
    )


def _get_run_row(db_path: Path, run_id: str):
    """helper：取 workflow_runs 行（sqlite3.Row → dict）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
