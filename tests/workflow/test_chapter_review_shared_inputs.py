"""V3.9 批次 5.7：chapter-review 共享取数（basic_checks 一次取齐，critic / deep_review 消费）。

背景（roadmap 5.7）：改造前 ``_critic_review_node`` 与 ``_deep_review_node`` 各自调用
``_collect_critic_inputs`` + ``_collect_open_hooks`` + ``_collect_settings_digest``——
同一 run 内相同 SQL 跑两遍；``_fetch_chapter_number`` 在 always 模式下白查。

本文件钉住的契约（撤修复必红）：
1. ``basic_checks`` 产出 ``ctx["review_inputs"]``（draft_text / project_id / plan_summary /
   suggested_hashes / open_hooks / settings_digest）；
2. critic / deep_review **只消费不重查**：把取数函数换成「调用即 AssertionError」的哨兵后，
   两节点仍能正常出报告；整条节点序列里每个取数源恰好命中一次（改造前各两次）；
3. ``_fetch_chapter_number`` 只在 ``sample`` 模式调用（always / off 下 0 次）；
4. 共享输入不可用（未跑 basic_checks / 章节行缺失）→ 两节点按既有「inputs 失败」路径
   降级，不抛错、不炸 run。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import packages.workflows.chapter_review.pipeline as review_pipeline
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import (
    _basic_checks_node,
    _critic_review_node,
    _deep_review_node,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

PROSE = "戌时的更鼓从街尾传过来。苏婉清坐在窗下，手里那只茶盏已温了许久。"
PLAN = {
    "chapter_goal": "女主第一次怀疑男主",
    "key_beats": [{"beat_id": "beat_001", "purpose": "夜访"}],
    "revision_note": "把结尾收紧",
    "suggested_hashes": ["abc123def456"],
}


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, ?, '夜叩青石', ?, 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, json.dumps(PLAN, ensure_ascii=False), now, now),
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
            "(?, ?, 1, ?, 'test:writer:v1', NULL, NULL, ?)",
            (new_id("drf"), chapter_id, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_hook(db_path: Path, project_id: str, name: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO hooks (hook_id, project_id, name, status, importance, created_at, "
            "updated_at) VALUES (?, ?, ?, 'OPEN', 0.9, ?, ?)",
            (new_id("hk"), project_id, name, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_world_rule(db_path: Path, project_id: str, name: str, statement: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO world_rules (world_rule_id, project_id, name, statement, data_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?)",
            (new_id("wrule"), project_id, name, statement, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


class _Fixture:
    def __init__(self, db_path: Path, pid: str, cid: str, ctx: dict) -> None:
        self.db_path = db_path
        self.pid = pid
        self.cid = cid
        self.ctx = ctx


def _fixture(tmp_path: Path, *, number: int = 1, critic_mode: str = "always") -> _Fixture:
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=number)
    _insert_draft(db_path, cid, PROSE)
    _seed_hook(db_path, pid, "黑玉佩来历")
    _seed_world_rule(db_path, pid, "青云宗不收外徒", "青云宗门规：只收本族弟子。")
    ctx: dict = {
        "db_path": str(db_path),
        "chapter_id": cid,
        "critic_mode": critic_mode,
        "run_id": new_id("run"),
        "_current_node_run_id": new_id("nr"),
    }
    return _Fixture(db_path, pid, cid, ctx)


def _run_agent_recorder(calls: list):
    """返回 run_agent 替身：记录 ``{agent_name, payload}`` 并按 agent 回合规输出。"""

    def _fn(db_path, agent_name, payload, run_id, **kw):  # noqa: ANN001
        calls.append({"agent_name": agent_name, "payload": payload, "kwargs": kw})
        if agent_name == "critic":
            return {
                "schema_version": "critic-report.v1",
                "prompt_version": "critic:v1",
                "chapter_id": "ch_xxx",
                "overall_comment": "节奏尚可。",
                "strengths": [],
                "issues": [],
            }
        if agent_name == "deep_reviewer":
            return {
                "schema_version": "deep-review-report.v1",
                "prompt_version": "deep_reviewer:v1",
                "chapter_id": "ch_xxx",
                "verdict": "pass",
                "layers": [],
                "issues": [],
            }
        raise AssertionError(f"unexpected agent {agent_name!r}")

    return _fn


def _patch_collectors(monkeypatch, *, counter: dict | None = None):
    """把取数函数替换为「调用即 AssertionError」哨兵（counter 非 None 时改为计数）。"""
    real_open_hooks = review_pipeline._collect_open_hooks
    real_settings = review_pipeline._collect_settings_digest

    def _open_hooks(*a, **kw):
        if counter is None:
            raise AssertionError("critic/deep_review 不得自行重查 open_hooks")
        counter["open_hooks"] += 1
        return real_open_hooks(*a, **kw)

    def _settings(*a, **kw):
        if counter is None:
            raise AssertionError("critic/deep_review 不得自行重查 settings_digest")
        counter["settings_digest"] += 1
        return real_settings(*a, **kw)

    monkeypatch.setattr(review_pipeline, "_collect_open_hooks", _open_hooks)
    monkeypatch.setattr(review_pipeline, "_collect_settings_digest", _settings)


# ---------------------------------------------------------------------------
# 1. basic_checks 取齐共享输入
# ---------------------------------------------------------------------------


def test_basic_checks_prefetches_shared_inputs(tmp_path: Path, monkeypatch):
    f = _fixture(tmp_path)
    counter = {"open_hooks": 0, "settings_digest": 0}
    _patch_collectors(monkeypatch, counter=counter)

    out = _basic_checks_node(dict(f.ctx))

    # 每个取数源在 basic_checks 内各命中一次
    assert counter == {"open_hooks": 1, "settings_digest": 1}
    inputs = out["review_inputs"]
    assert isinstance(inputs, dict)
    assert inputs["draft_text"] == PROSE
    assert inputs["project_id"] == f.pid
    assert inputs["plan_summary"] == {
        "chapter_goal": PLAN["chapter_goal"],
        "key_beats": PLAN["key_beats"],
    }
    assert inputs["suggested_hashes"] == ["abc123def456"]
    assert [h["name"] for h in inputs["open_hooks"]] == ["黑玉佩来历"]
    assert any(
        d["kind"] == "world_rule" and d["name"] == "青云宗不收外徒"
        for d in inputs["settings_digest"]
    )
    # 可用性标记：正常路径无错误
    assert out["review_inputs_error"] is None


# ---------------------------------------------------------------------------
# 2. critic / deep_review 消费共享输入、不重查
# ---------------------------------------------------------------------------


def test_critic_consumes_prefetched_inputs_without_requery(tmp_path: Path, monkeypatch):
    f = _fixture(tmp_path)
    ctx = dict(f.ctx)
    ctx.update(_basic_checks_node(ctx))
    # 哨兵：critic 一旦重查即 AssertionError（撤修复 → 降级 failed → 本用例红）
    _patch_collectors(monkeypatch)

    calls: list = []
    with patch(
        "packages.workflows.chapter_review.pipeline.run_agent", _run_agent_recorder(calls)
    ):
        out = _critic_review_node(ctx)

    assert out["critic_status"] == "ok"
    payload = calls[0]["payload"]
    assert payload["draft_text"] == PROSE
    assert payload["plan_summary"]["chapter_goal"] == PLAN["chapter_goal"]
    assert [h["name"] for h in payload["open_hooks"]] == ["黑玉佩来历"]
    assert any(d["kind"] == "world_rule" for d in payload["settings_digest"])


def test_deep_review_consumes_prefetched_inputs_without_requery(tmp_path: Path, monkeypatch):
    f = _fixture(tmp_path)
    ctx = dict(f.ctx)
    ctx["deep_review"] = True
    ctx.update(_basic_checks_node(ctx))
    _patch_collectors(monkeypatch)

    calls: list = []
    with patch(
        "packages.workflows.chapter_review.pipeline.run_agent", _run_agent_recorder(calls)
    ):
        out = _deep_review_node(ctx)

    assert out["deep_review_status"] == "ok"
    payload = calls[0]["payload"]
    assert payload["draft_text"] == PROSE
    assert payload["plan_summary"]["chapter_goal"] == PLAN["chapter_goal"]
    assert any(d["kind"] == "world_rule" for d in payload["settings_digest"])


def test_full_node_sequence_fetches_each_source_once(tmp_path: Path, monkeypatch):
    """basic_checks → critic → deep_review 整条序列：每个取数源恰好一次（改造前两次）。"""
    f = _fixture(tmp_path)
    counter = {"open_hooks": 0, "settings_digest": 0}
    _patch_collectors(monkeypatch, counter=counter)

    ctx = dict(f.ctx)
    ctx["deep_review"] = True
    ctx.update(_basic_checks_node(ctx))
    with patch("packages.workflows.chapter_review.pipeline.run_agent", _run_agent_recorder([])):
        ctx.update(_critic_review_node(ctx))
        ctx.update(_deep_review_node(ctx))

    assert ctx["critic_status"] == "ok"
    assert ctx["deep_review_status"] == "ok"
    assert counter == {"open_hooks": 1, "settings_digest": 1}


# ---------------------------------------------------------------------------
# 3. _fetch_chapter_number 只在 sample 模式调用
# ---------------------------------------------------------------------------


def _count_fetch_calls(monkeypatch) -> list:
    calls: list = []
    real = review_pipeline._fetch_chapter_number

    def _spy(db_path, chapter_id):  # noqa: ANN001
        calls.append(chapter_id)
        return real(db_path, chapter_id)

    monkeypatch.setattr(review_pipeline, "_fetch_chapter_number", _spy)
    return calls


def test_fetch_chapter_number_not_called_in_always_mode(tmp_path: Path, monkeypatch):
    """critic_mode=always：_fetch_chapter_number 不查库（改造前恒查一次）。"""
    f = _fixture(tmp_path, number=3)
    ctx = dict(f.ctx)
    ctx.update(_basic_checks_node(ctx))
    calls = _count_fetch_calls(monkeypatch)

    with patch("packages.workflows.chapter_review.pipeline.run_agent", _run_agent_recorder([])):
        out = _critic_review_node(ctx)

    assert out["critic_status"] == "ok"
    assert calls == []


def test_fetch_chapter_number_not_called_in_off_mode(tmp_path: Path, monkeypatch):
    f = _fixture(tmp_path, number=3, critic_mode="off")
    calls = _count_fetch_calls(monkeypatch)

    out = _critic_review_node(dict(f.ctx))

    assert out["critic_status"] == "skipped"
    assert calls == []


def test_fetch_chapter_number_called_in_sample_mode(tmp_path: Path, monkeypatch):
    """critic_mode=sample：章节号是判定依据，必须查一次（number=5 → 命中调 critic）。"""
    f = _fixture(tmp_path, number=5, critic_mode="sample")
    ctx = dict(f.ctx)
    ctx.update(_basic_checks_node(ctx))
    calls = _count_fetch_calls(monkeypatch)

    with patch("packages.workflows.chapter_review.pipeline.run_agent", _run_agent_recorder([])):
        out = _critic_review_node(ctx)

    assert out["critic_status"] == "ok"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 4. 共享输入不可用 → 既有「inputs 失败」降级路径
# ---------------------------------------------------------------------------


def test_critic_degrades_when_shared_inputs_missing(tmp_path: Path):
    """未跑 basic_checks（ctx 无 review_inputs）→ 降级 failed，不抛错、不调 LLM。"""
    f = _fixture(tmp_path)
    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        out = _critic_review_node(dict(f.ctx))

    assert mock_run.called is False
    assert out["critic_status"] == "failed"
    assert out["critic_report"] is None
    assert out["critic_error"].startswith("inputs: ")


def test_deep_review_degrades_when_shared_inputs_missing(tmp_path: Path):
    f = _fixture(tmp_path)
    ctx = dict(f.ctx)
    ctx["deep_review"] = True
    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        out = _deep_review_node(ctx)

    assert mock_run.called is False
    assert out["deep_review_status"] == "failed"
    assert out["deep_review_report"] is None
    assert out["deep_review_error"].startswith("inputs: ")


def test_missing_chapter_row_marks_inputs_unavailable(tmp_path: Path):
    """章节行缺失（draft 孤儿）→ review_inputs=None + 错误标记（与改造前取数口径一致）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, PROSE)
    conn = get_connection(db_path)
    try:
        # drafts.chapter_id 有 FK（无 ON DELETE）→ 临时关闭外键约束造「章节行缺失」现场
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()

    out = _basic_checks_node({"db_path": str(db_path), "chapter_id": cid})

    assert out["review_inputs"] is None
    assert "not found" in out["review_inputs_error"]
