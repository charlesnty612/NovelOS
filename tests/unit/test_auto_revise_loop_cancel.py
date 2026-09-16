"""V3.9 批次 4.2：auto_revise 回路级取消（进程内注册表 + 每轮前置检查）。

覆盖：
- 注册表原语：登记 / 记录子 run / 按子 run 反查标记取消 / 终止后清理；
- ``_auto_revise_loop`` 每轮启动子 run **前**的取消检查：
  1) 注册表 cancelled 标记（cancel 端点取消任一子 run 时置位）→ 不再启动下一轮；
  2) 已记录子 run 在 DB 里为 CANCELLED（兜底竞态）→ 不再启动下一轮；
- 未取消时仍按 ``max_iter`` 上限跑满（既有语义不回归）；
- 回路终止后注册表无残留（daemon 打挂也不留僵尸记录）。

测试手法：monkeypatch ``_run_workflow_return_payload`` 为脚本化假实现（不真跑
workflow），用真实临时 DB 落 workflow_runs 行，让 ``get_run`` 能读到
error='rejected-for-revision'（回路据此进入下一轮）与 CANCELLED 状态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.api.routers import workflows as wf
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


def _db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _ensure_workflow(db_path: str, name: str = "chapter-review") -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return row["workflow_id"]
        wf_id = new_id("wf")
        now = now_iso()
        conn.execute(
            "INSERT INTO workflows "
            "(workflow_id, name, version, definition_json, created_at, updated_at) "
            "VALUES (?, ?, 'v1', '{}', ?, ?)",
            (wf_id, name, now, now),
        )
        conn.commit()
        return wf_id
    finally:
        conn.close()


def _insert_run(
    db_path: str, *, status: str, error: str | None = None, run_id: str | None = None
) -> str:
    wf_id = _ensure_workflow(db_path)
    rid = run_id or new_id("wfr")
    now = now_iso()
    ended = now if status in ("COMPLETED", "FAILED", "CANCELLED") else None
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, NULL, ?, NULL, '{}', ?, 0, ?, ?)
            """,
            (rid, wf_id, status, error, now, ended),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _cleanup_loops() -> None:
    with wf._AUTO_REVISE_LOOPS_LOCK:  # noqa: SLF001 —— 测试直接清空进程内注册表
        wf._AUTO_REVISE_LOOPS.clear()  # noqa: SLF001


@pytest.fixture(autouse=True)
def _clean_registry():
    _cleanup_loops()
    yield
    _cleanup_loops()


# ---------------------------------------------------------------------------
# 注册表原语
# ---------------------------------------------------------------------------


def test_registry_record_and_mark_cancelled_for_child_run():
    """子 run 被记录后，按 run_id 反查可把回路标记 cancelled（cancel 端点调用点）。"""
    wf._register_auto_revise_loop(  # noqa: SLF001
        loop_id="arloop_a",
        parent_run_id="wfr_parent",
        chapter_id="ch_1",
        project_id="prj_1",
        max_iter=2,
    )
    wf._record_auto_revise_child("arloop_a", "wfr_child_1")  # noqa: SLF001

    hit = wf._mark_auto_revise_loops_cancelled_for_run("wfr_child_1")  # noqa: SLF001
    assert hit == ["arloop_a"]
    # 幂等：重复取消不再重复入列，但返回仍命中
    assert wf._mark_auto_revise_loops_cancelled_for_run("wfr_child_1") == ["arloop_a"]  # noqa: SLF001
    # 未知 run 不命中任何回路
    assert wf._mark_auto_revise_loops_cancelled_for_run("wfr_nope") == []  # noqa: SLF001


def test_registry_loop_cancelled_lookup(tmp_path: Path):
    """``_auto_revise_loop_cancelled``：未取消 → False；登记取消 → True + reason。"""
    db_path = _db(tmp_path)
    wf._register_auto_revise_loop(  # noqa: SLF001
        loop_id="arloop_b",
        parent_run_id=None,
        chapter_id="ch_1",
        project_id="prj_1",
        max_iter=2,
    )
    assert wf._auto_revise_loop_cancelled("arloop_b", db_path) == (False, "")  # noqa: SLF001

    wf._mark_auto_revise_loops_cancelled_for_run("wfr_x", reason="ignored")  # noqa: SLF001
    assert wf._auto_revise_loop_cancelled("arloop_b", db_path)[0] is False  # noqa: SLF001

    wf._record_auto_revise_child("arloop_b", "wfr_x")  # noqa: SLF001
    wf._mark_auto_revise_loops_cancelled_for_run("wfr_x")  # noqa: SLF001
    cancelled, reason = wf._auto_revise_loop_cancelled("arloop_b", db_path)  # noqa: SLF001
    assert cancelled is True
    assert reason == "child-run-cancelled"


def test_registry_db_fallback_detects_cancelled_child(tmp_path: Path):
    """注册表未置位，但已记录子 run 在 DB 里是 CANCELLED → 判定取消（兜底路径）。"""
    db_path = _db(tmp_path)
    wf._register_auto_revise_loop(  # noqa: SLF001
        loop_id="arloop_c",
        parent_run_id=None,
        chapter_id="ch_1",
        project_id="prj_1",
        max_iter=2,
    )
    rid = _insert_run(db_path, status="CANCELLED")
    wf._record_auto_revise_child("arloop_c", rid)  # noqa: SLF001

    cancelled, reason = wf._auto_revise_loop_cancelled("arloop_c", db_path)  # noqa: SLF001
    assert cancelled is True
    assert rid in reason


# ---------------------------------------------------------------------------
# _auto_revise_loop：每轮前置取消检查
# ---------------------------------------------------------------------------


def _make_fake_runner(
    db_path: str,
    calls: list[tuple[str, str]],
    ctx_extras: list[dict | None] | None = None,
):
    """脚本化 ``_run_workflow_return_payload``：write COMPLETED / review rejected。

    每轮 review 落一行 error='rejected-for-revision' 的 FAILED run，让回路能进入下一轮；
    on_run_started 回调按真实实现语义调用（注册表登记子 run）。

    ``ctx_extras`` 非 None 时，按启动顺序记录每次调用收到的 ``initial_ctx_extra``
    （子 run ctx 透传断言用：write / review 各一条）。
    """

    def fake(
        engine,  # noqa: ANN001
        db_path_arg,
        workflow_name,
        project_id,
        chapter_id,
        mock_providers,
        initial_ctx_extra=None,
        *,
        wait_deadline_seconds=None,
        on_run_started=None,
    ):
        if ctx_extras is not None:
            ctx_extras.append(initial_ctx_extra)
        if workflow_name == "chapter-write":
            rid = _insert_run(db_path, status="COMPLETED")
            if on_run_started is not None:
                on_run_started(rid)
            calls.append(("write", rid))
            return {"run_id": rid, "status": "COMPLETED", "current_node": None}
        rid = _insert_run(
            db_path, status="FAILED", error="rejected-for-revision"
        )
        if on_run_started is not None:
            on_run_started(rid)
        calls.append(("review", rid))
        return {"run_id": rid, "status": "FAILED", "current_node": None}

    return fake


def test_loop_marks_cancelled_via_registry_and_stops_before_next_round(
    tmp_path: Path, monkeypatch
):
    """第 1 轮 review 子 run 被取消（cancel 端点置位）→ 不再启动第 2 轮 write。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    fake = _make_fake_runner(db_path, calls)

    def fake_with_cancel(*args, **kwargs):
        out = fake(*args, **kwargs)
        if kwargs.get("on_run_started") is not None and out["status"] == "FAILED":
            # 模拟用户在 review 子 run RUNNING 期间点了 /runs/{id}/cancel
            wf._mark_auto_revise_loops_cancelled_for_run(out["run_id"])  # noqa: SLF001
        return out

    monkeypatch.setattr(wf.revise, "_run_workflow_return_payload", fake_with_cancel)

    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id="wfr_parent",
    )

    assert [c[0] for c in calls] == ["write", "review"], calls
    assert payload["status"] == "CANCELLED"
    assert payload["run_id"] == "wfr_parent"
    # 回路终止后注册表无残留
    assert wf._AUTO_REVISE_LOOPS == {}  # noqa: SLF001


def test_loop_stops_when_recorded_child_is_cancelled_in_db(tmp_path: Path, monkeypatch):
    """已记录子 run 在 DB 中变为 CANCELLED（竞态兜底）→ 不再启动下一轮。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    fake = _make_fake_runner(db_path, calls)

    def fake_with_db_cancel(*args, **kwargs):
        out = fake(*args, **kwargs)
        if out["status"] == "FAILED":
            # 取消发生在 payload 返回之后（注册表未及置位）→ 只有 DB 状态可见
            conn = get_connection(db_path)
            try:
                conn.execute(
                    "UPDATE workflow_runs SET status='CANCELLED' WHERE run_id = ?",
                    (out["run_id"],),
                )
                conn.commit()
            finally:
                conn.close()
        return out

    monkeypatch.setattr(wf.revise, "_run_workflow_return_payload", fake_with_db_cancel)

    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 3, parent_run_id="wfr_parent",
    )

    assert [c[0] for c in calls] == ["write", "review"], calls
    assert payload["status"] == "CANCELLED"


def test_loop_without_cancel_still_runs_to_max_iter(tmp_path: Path, monkeypatch):
    """未取消：既有语义不回归——跑满 max_iter 轮（每轮 write+review）。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_fake_runner(db_path, calls)
    )

    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id="wfr_parent",
    )

    assert [c[0] for c in calls] == ["write", "review", "write", "review"]
    # 达上限仍 rejected：返回最后一轮 review 的 FAILED payload（既有行为）
    assert payload["status"] == "FAILED"
    assert payload["run_id"] == calls[-1][1]
    assert wf._AUTO_REVISE_LOOPS == {}  # noqa: SLF001


def test_cancel_of_unknown_run_does_not_touch_loops(tmp_path: Path, monkeypatch):
    """cancel 端点语义：取消的 run 不属于任何回路 → 注册表不动（端点 200 行为不变）。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_fake_runner(db_path, calls)
    )
    assert wf._mark_auto_revise_loops_cancelled_for_run("wfr_unrelated") == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# _auto_revise_loop：子 run ctx 透传（author_intent 补齐 / model_overrides 不回归）
# ---------------------------------------------------------------------------


def test_loop_ctx_extra_carries_author_intent_and_model_overrides(
    tmp_path: Path, monkeypatch
):
    """F-11（2026-09-16 实证）：回路每轮 write / review 子 run 的 ctx 必须**同时**带
    ``author_intent`` 与 ``model_overrides``——两个键并列存在，互不覆盖。

    突变验证：撤掉 ``revise.py`` 里 author_intent 并入 ``initial_ctx_extra`` 的两行 → 本测试红。
    """
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    ctx_extras: list[dict | None] = []
    monkeypatch.setattr(
        wf.revise,
        "_run_workflow_return_payload",
        _make_fake_runner(db_path, calls, ctx_extras),
    )

    overrides = {"creative_writing": "mprof_loop_ctx_test"}
    intent = "本书铁律：无CP、字数 2200~2800、禁止内部标识入文"
    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, overrides, intent,
        parent_run_id="wfr_parent",
    )

    assert [c[0] for c in calls] == ["write", "review"], calls
    assert ctx_extras == [
        {"model_overrides": overrides, "author_intent": intent},
        {"model_overrides": overrides, "author_intent": intent},
    ], ctx_extras


def test_loop_ctx_extra_author_intent_only_omits_model_overrides(
    tmp_path: Path, monkeypatch
):
    """只给 author_intent（resume 未传 overrides）→ 子 run ctx 只出现 author_intent 键。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    ctx_extras: list[dict | None] = []
    monkeypatch.setattr(
        wf.revise,
        "_run_workflow_return_payload",
        _make_fake_runner(db_path, calls, ctx_extras),
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, None, "本书铁律",
        parent_run_id="wfr_parent",
    )

    assert ctx_extras == [{"author_intent": "本书铁律"}, {"author_intent": "本书铁律"}]
    assert all("model_overrides" not in (e or {}) for e in ctx_extras)


def test_loop_ctx_extra_is_none_when_both_absent(tmp_path: Path, monkeypatch):
    """两个键都为空 → ``initial_ctx_extra`` 保持 None，不得退化成空 dict（缺省零行为变更）。"""
    db_path = _db(tmp_path)
    calls: list[tuple[str, str]] = []
    ctx_extras: list[dict | None] = []
    monkeypatch.setattr(
        wf.revise,
        "_run_workflow_return_payload",
        _make_fake_runner(db_path, calls, ctx_extras),
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, parent_run_id="wfr_parent",
    )

    assert ctx_extras == [None, None], ctx_extras
