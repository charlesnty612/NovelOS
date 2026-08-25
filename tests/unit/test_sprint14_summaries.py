"""Sprint 14-A：chapter-commit summarize 节点测试。

覆盖（任务书 §A）：
1. summarize 节点在 commit 成功后写入 ``chapter_summaries`` 行（mock provider 走通路径）。
2. summarize 步骤失败时降级：chapter 仍处于 COMMITTED，summary_status='failed'。
3. summarize 步骤跳过：commit 后 draft 为空 → summary_status='skipped'，不写库。
4. summarize 摘要截断：summary > 200 字 → 截断 + degraded=True。
5. 摘要链上下文装配（L1）：build_director_input 含 ``recent_chapter_summaries`` /
   ``previous_chapter_tail`` / ``open_foreshadow_list`` 三个新键。
6. 截断策略：注入 8 章摘要 → 最近 5 章保留（旧摘要被砍）。
7. preview_context 输出含新条目（kind=chapter_summary / previous_chapter_tail /
   open_foreshadow）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.context_engine.builders import build_director_input
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


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
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path, project_id: str, number: int, title: str = "", status: str = "COMMITTED"
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, '{}', ?, 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, title, status, now, now),
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
            "prompt_version, model_id, created_at) VALUES (?, ?, ?, ?, 'test:writer:v1', "
            "NULL, NULL, ?)",
            (new_id("drf"), chapter_id, 1, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_summary(
    db_path: Path, project_id: str, chapter_id: str, chapter_no: int,
    summary: str, tail_text: str = "tail",
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapter_summaries (summary_id, project_id, chapter_id, chapter_no, "
            "summary, tail_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("sum"), project_id, chapter_id, chapter_no, summary, tail_text, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_hook(
    db_path: Path,
    project_id: str,
    *,
    name: str = "hook",
    status: str = "OPEN",
    importance: float = 0.5,
    introduced_chapter_id: str | None = None,
    introduced_chapter_no: int | None = None,
    visibility: str = "RESTRICTED",
) -> str:
    """直接 SQL 注入 hook 行；introduced_chapter_no 仅用于排序参考（hook 表里没这列）。"""
    hook_id = new_id("hook")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status, importance,
                 expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?)
            """,
            (hook_id, project_id, name, introduced_chapter_id, status,
             importance, visibility, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return hook_id


def _register_summarizer_agent(db_path: Path) -> None:
    """注入 summarizer agent + ACTIVE prompt + workflow_runs + workflow_run_nodes 行，
    让 run_agent 调 _record_call 时 FK 不失败。

    测试场景下手工补齐 ai_call_logs 的 FK 依赖（生产路径由 WorkflowEngine 自动写）。
    """
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
        # workflow FK（ai_call_logs.run_id / node_run_id 引用）
        # workflows 表需存在
        wf_id = new_id("wf")
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at) "
            "VALUES (?, 'chapter-commit', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, current_node, "
            "checkpoint_json, error, retry_count, started_at, ended_at) "
            "VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)",
            (wf_run_id, wf_id, now),
        )
        conn.execute(
            "INSERT INTO workflow_run_nodes (node_run_id, run_id, node_id, status, started_at, ended_at, "
            "input_json, output_json, error) "
            "VALUES (?, ?, 'summarize', 'RUNNING', ?, NULL, '{}', NULL, NULL)",
            (node_run_id, wf_run_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return wf_run_id, node_run_id


# ---------------------------------------------------------------------------
# 摘要链装配（L1）
# ---------------------------------------------------------------------------


def test_director_input_includes_new_l1_keys(tmp_path: Path):
    """build_director_input 含 recent_chapter_summaries / previous_chapter_tail / open_foreshadow_list。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=5)

    out = build_director_input(db_path, pid, cid, "意图")
    assert "recent_chapter_summaries" in out
    assert "previous_chapter_tail" in out
    assert "open_foreshadow_list" in out
    # 无 chapter_summaries 行 → 空 list
    assert out["recent_chapter_summaries"] == []
    # 无前章 → 空 dict
    assert out["previous_chapter_tail"] == {}
    # 无 planted 状态伏笔 → 空 list
    assert out["open_foreshadow_list"] == []


def test_recent_summaries_takes_last_n_by_chapter_no_desc(tmp_path: Path):
    """recent_chapter_summaries 按 chapter_no DESC 取最近 N 章；不含当前章自身。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    current_cid = _insert_chapter(db_path, pid, number=10)
    # 注入 chapter_no=1..8 共 8 个历史摘要（current_cid=10）
    for no in range(1, 9):
        hcid = _insert_chapter(db_path, pid, number=no)
        _insert_summary(db_path, pid, hcid, no, f"chapter {no} summary")
    # 同时塞一条 chapter_no=10（当前章）的摘要——应被排除
    _insert_summary(db_path, pid, current_cid, 10, "current chapter summary")

    out = build_director_input(db_path, pid, current_cid, "意图")
    summaries = out["recent_chapter_summaries"]
    # 最近 5 章：chapter_no 8..4
    assert len(summaries) == 5, summaries
    chapter_nos = [s["chapter_no"] for s in summaries]
    assert chapter_nos == [8, 7, 6, 5, 4], chapter_nos
    # 当前章 10 不在结果中
    assert all(s["chapter_no"] != 10 for s in summaries)


def test_previous_chapter_tail_contains_last_300_chars(tmp_path: Path):
    """previous_chapter_tail = 上一章末尾 300 字。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=3)
    prev_cid = _insert_chapter(db_path, pid, number=2)
    _insert_draft(db_path, prev_cid, "X" * 1000)  # 1000 字
    # 让 current 也需要 prev tail
    out = build_director_input(db_path, pid, cur_cid, "意图")
    tail = out["previous_chapter_tail"]
    assert tail["chapter_id"] == prev_cid
    assert tail["chapter_no"] == 2
    assert len(tail["tail_text"]) == 300


def test_previous_chapter_tail_empty_when_no_prev(tmp_path: Path):
    """无前章（chapter_no=1）→ previous_chapter_tail = {}。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)
    out = build_director_input(db_path, pid, cid, "意图")
    assert out["previous_chapter_tail"] == {}


# ---------------------------------------------------------------------------
# 开放伏笔清单（overdue 计算 + 排序）
# ---------------------------------------------------------------------------


def test_open_foreshadow_includes_only_planted(tmp_path: Path):
    """开放伏笔清单只含 planted 状态（OPEN/ACTIVE/ESCALATED）；RESOLVED/ABANDONED 排除。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=10)
    _insert_hook(db_path, pid, name="open", status="OPEN")
    _insert_hook(db_path, pid, name="active", status="ACTIVE")
    _insert_hook(db_path, pid, name="escalated", status="ESCALATED")
    _insert_hook(db_path, pid, name="resolved", status="RESOLVED")
    _insert_hook(db_path, pid, name="abandoned", status="ABANDONED")

    out = build_director_input(db_path, pid, cid, "意图")
    names = [h["name"] for h in out["open_foreshadow_list"]]
    assert set(names) == {"open", "active", "escalated"}, names
    assert "resolved" not in names
    assert "abandoned" not in names


def test_open_foreshadow_overdue_calculation(tmp_path: Path):
    """overdue 计算：introduced_chapter_no 与当前 chapter_no 差 > 30 即 overdue=True。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # current chapter_no=40；introduced=5 → 差 35 → overdue
    # introduced=10 → 差 30 → NOT overdue (>, not >=)
    cur_cid = _insert_chapter(db_path, pid, number=40)
    intro_old_cid = _insert_chapter(db_path, pid, number=5)
    intro_fresh_cid = _insert_chapter(db_path, pid, number=10)
    _insert_hook(
        db_path, pid, name="overdue_hook",
        introduced_chapter_id=intro_old_cid,
    )
    _insert_hook(
        db_path, pid, name="fresh_hook",
        introduced_chapter_id=intro_fresh_cid,
    )

    out = build_director_input(db_path, pid, cur_cid, "意图")
    items = {h["name"]: h for h in out["open_foreshadow_list"]}
    assert items["overdue_hook"]["overdue"] is True
    assert items["overdue_hook"]["chapters_since_introduced"] == 35
    assert items["fresh_hook"]["overdue"] is False
    assert items["fresh_hook"]["chapters_since_introduced"] == 30


def test_open_foreshadow_overdue_first_in_sort(tmp_path: Path):
    """overdue 项排在 importance 更高的非 overdue 项之前。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=50)
    old_intro = _insert_chapter(db_path, pid, number=1)  # 差 49 → overdue
    new_intro = _insert_chapter(db_path, pid, number=45)  # 差 5 → NOT overdue
    # 重要度 0.9 的非 overdue 应该被 overdue 的 0.5 顶到第二位
    _insert_hook(
        db_path, pid, name="important_fresh", importance=0.9,
        introduced_chapter_id=new_intro,
    )
    _insert_hook(
        db_path, pid, name="overdue_low", importance=0.5,
        introduced_chapter_id=old_intro,
    )
    out = build_director_input(db_path, pid, cur_cid, "意图")
    names_in_order = [h["name"] for h in out["open_foreshadow_list"]]
    # overdue_low 应该排第一（即使 importance 低）
    assert names_in_order[0] == "overdue_low", names_in_order
    assert names_in_order[1] == "important_fresh", names_in_order


# ---------------------------------------------------------------------------
# preview_context 输出含新条目
# ---------------------------------------------------------------------------


def test_preview_includes_chapter_summary_items(tmp_path: Path):
    """preview_context L1 items 含 chapter_summary 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=5)
    hcid = _insert_chapter(db_path, pid, number=4)
    _insert_summary(db_path, pid, hcid, 4, "第 4 章概要：林渊抵达京城")

    out = preview_context(db_path, pid, cur_cid)
    l1_items = next(layer for layer in out["layers"] if layer["id"] == "L1")["items"]
    summary_items = [it for it in l1_items if it["kind"] == "chapter_summary"]
    assert len(summary_items) == 1
    assert summary_items[0]["chapter_no"] == 4


def test_preview_includes_previous_chapter_tail_item(tmp_path: Path):
    """preview_context L1 items 含 previous_chapter_tail 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=3)
    prev_cid = _insert_chapter(db_path, pid, number=2)
    _insert_draft(db_path, prev_cid, "前章正文" * 200)

    out = preview_context(db_path, pid, cur_cid)
    l1_items = next(layer for layer in out["layers"] if layer["id"] == "L1")["items"]
    tail_items = [it for it in l1_items if it["kind"] == "previous_chapter_tail"]
    assert len(tail_items) == 1
    # preview item 字段：id / name / source_len（name 中带 chapter_no 描述）
    assert "第 2 章结尾 300 字" in tail_items[0]["name"]
    assert tail_items[0]["source_len"] == 300


def test_preview_includes_open_foreshadow_item_with_overdue(tmp_path: Path):
    """preview_context L1 items 含 open_foreshadow 条目；含 overdue 标注。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=40)
    old_intro = _insert_chapter(db_path, pid, number=1)
    _insert_hook(db_path, pid, name="伏笔A", introduced_chapter_id=old_intro)

    out = preview_context(db_path, pid, cur_cid)
    l1_items = next(layer for layer in out["layers"] if layer["id"] == "L1")["items"]
    fores = [it for it in l1_items if it["kind"] == "open_foreshadow"]
    assert len(fores) == 1
    assert fores[0]["overdue"] is True
    assert fores[0]["status"] == "OPEN"


# ---------------------------------------------------------------------------
# summarize 步骤单元测试（_summarize_node 直接调）
# ---------------------------------------------------------------------------


def test_summarize_node_persists_row_with_mock_provider(tmp_path: Path):
    """summarize 节点在 mock provider 返回合法摘要时落 chapter_summaries 行。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文" * 300)  # 1200 字
    run_id, node_run_id = _register_summarizer_agent(db_path)

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": "本章概述：林渊与苏婉清夜谈，揭示伏笔。"}, ensure_ascii=False)],
        },
    }
    result = _summarize_node(ctx)
    assert result["summary_status"] == "ok", result
    assert result["summary_id"] is not None
    assert result["degraded"] is False

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM chapter_summaries WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "chapter_summaries 行未落库"
    row_d = dict(row)
    assert row_d["project_id"] == pid
    assert row_d["chapter_no"] == 1
    assert "林渊与苏婉清夜谈" in row_d["summary"]
    assert len(row_d["tail_text"]) == 300


def test_summarize_node_degrades_on_provider_failure(tmp_path: Path):
    """summarize 失败降级：mock provider 返回非 JSON → summary_status='failed'；不抛错。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文")
    run_id, node_run_id = _register_summarizer_agent(db_path)

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {
            "summarizer": ["this is not JSON"],
        },
    }
    result = _summarize_node(ctx)
    assert result["summary_status"] == "failed", result
    assert result["summary_id"] is None

    # 不应落 chapter_summaries 行
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chapter_summaries WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert dict(row)["n"] == 0


def test_summarize_node_skips_when_no_draft(tmp_path: Path):
    """commit 后草稿为空 → summary_status='skipped'。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    # 不插 draft

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "wfr_test",
        "mock_providers": {},
    }
    result = _summarize_node(ctx)
    assert result["summary_status"] == "skipped", result


def test_summarize_node_truncates_long_summary(tmp_path: Path):
    """summary > 200 字 → 截断 + degraded=True。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文")
    run_id, node_run_id = _register_summarizer_agent(db_path)

    from packages.workflows.chapter_commit.pipeline import _summarize_node

    long_summary = "字" * 250
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": run_id,
        "_current_node_run_id": node_run_id,
        "mock_providers": {
            "summarizer": [json.dumps({"summary": long_summary}, ensure_ascii=False)],
        },
    }
    result = _summarize_node(ctx)
    assert result["summary_status"] == "ok", result
    assert result["degraded"] is True

    conn = get_connection(db_path)
    try:
        row = dict(conn.execute(
            "SELECT summary FROM chapter_summaries WHERE chapter_id = ?", (cid,),
        ).fetchone())
    finally:
        conn.close()
    assert len(row["summary"]) == 200


# ---------------------------------------------------------------------------
# 截断策略（任务书 §A）：token 超预算时按"先砍最旧"截断
# ---------------------------------------------------------------------------


def test_summaries_truncation_strategy_drops_oldest_first(tmp_path: Path):
    """_RECENT_SUMMARY_CAP=5：注入 8 章摘要 → 最近 5 章（chapter_no 最高）保留。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=10)
    for no in range(1, 9):  # 1..8
        hcid = _insert_chapter(db_path, pid, number=no)
        # 每条摘要用 150 字（接近 200 上限）以充分消耗 token 预算
        _insert_summary(db_path, pid, hcid, no, "字" * 150)

    out = build_director_input(db_path, pid, cur_cid, "意图")
    summaries = out["recent_chapter_summaries"]
    assert len(summaries) == 5
    # 取最高 5 章号（8..4）
    assert [s["chapter_no"] for s in summaries] == [8, 7, 6, 5, 4]


# ---------------------------------------------------------------------------
# 任务书 §A 验收 #5：摘要链 + 前章尾段 + 开放伏笔清单装配证据（端到端集成）
# ---------------------------------------------------------------------------


def test_end_to_end_director_payload_has_all_sprint14_keys(tmp_path: Path):
    """端到端：build_director_input 同时含三组新键（recent_summaries / prev_tail / open_foreshadow）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=8)
    hcid = _insert_chapter(db_path, pid, number=7)
    prev_cid = _insert_chapter(db_path, pid, number=7)  # for prev tail
    _insert_draft(db_path, prev_cid, "前章正文" * 200)
    _insert_summary(db_path, pid, hcid, 7, "第 7 章概要")
    _insert_hook(db_path, pid, name="重要伏笔", status="ACTIVE", importance=0.8)

    out = build_director_input(db_path, pid, cur_cid, "意图")
    assert len(out["recent_chapter_summaries"]) >= 1
    assert out["previous_chapter_tail"]["chapter_no"] == 7
    assert len(out["open_foreshadow_list"]) == 1
    assert out["open_foreshadow_list"][0]["name"] == "重要伏笔"


# ---------------------------------------------------------------------------
# Sprint 15 / V1.3：summarize 真实降级路径（V1.2 审查遗留 P2-2）
#
# 设计：故意不注册 summarizer agent + ACTIVE prompt，让 run_agent 自然抛
# PromptNotFoundError；_summarize_node 的 ``except Exception`` 兜底应返回
# ``summary_status='failed'`` 且不抛错。同时验证 chapter.status 在降级路径下
# 保持 COMMITTED（commit 已完成，summarize 仅是附加动作）。
# ---------------------------------------------------------------------------


def test_summarize_node_real_degradation_without_prompt(tmp_path: Path):
    """summarize 真实降级：未注册 summarizer ACTIVE prompt → PromptNotFoundError →
    summary_status='failed'，chapter.status 仍为 COMMITTED，chapter_summaries 无新行。

    与 ``test_summarize_node_degrades_on_provider_failure`` 的关键区别：本测试
    不传 mock_script（mock_provider 路径会绕过 prompt 查找），让真实 Runner 走
    prompt 查找 → 命中「无 ACTIVE prompt」分支，验证降级兜底覆盖真实失败模式。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1, status="COMMITTED")
    _insert_draft(db_path, cid, "本章正文" * 100)

    # 故意不调 _register_summarizer_agent —— 让 PromptNotFoundError 自然抛
    from packages.workflows.chapter_commit.pipeline import _summarize_node

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "wfr_real_degrade",
        # 不传 _current_node_run_id / mock_providers；runner 走 prompt 查找
    }
    result = _summarize_node(ctx)

    # 1) summary_status='failed'（真实降级兜底）
    assert result["summary_status"] == "failed", result
    assert result["summary_id"] is None
    # 2) 不抛错（chapter-commit 不会因此 FAILED）
    # 3) chapter.status 仍为 COMMITTED
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert dict(chap_row)["status"] == "COMMITTED", chap_row
    # 4) chapter_summaries 无新行（失败时不落库）
    conn = get_connection(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM chapter_summaries WHERE chapter_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert dict(n)["n"] == 0
