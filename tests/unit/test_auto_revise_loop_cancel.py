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

import json
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
    db_path: str,
    *,
    status: str,
    error: str | None = None,
    run_id: str | None = None,
    checkpoint: dict | None = None,
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
            VALUES (?, ?, NULL, ?, NULL, ?, ?, 0, ?, ?)
            """,
            (
                rid,
                wf_id,
                status,
                json.dumps(checkpoint or {}, ensure_ascii=False),
                error,
                now,
                ended,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _cleanup_loops() -> None:
    with wf._AUTO_REVISE_LOOPS_LOCK:  # noqa: SLF001 —— 测试直接清空进程内注册表
        wf._AUTO_REVISE_LOOPS.clear()  # noqa: SLF001


def _author_revision_report() -> dict:
    """策略表里「仍走 capped revise」的最小报告切片：无 error、字数在带内。

    回路每轮的动作由 ``repair_policy.decide_repair`` 按 pending review 的
    ``review_report`` 判定（2026-09-18）。本文件测的是取消 / ctx 透传，不测策略，
    因此给回路喂一份「作者主观驳回」形状的报告 —— 该形状映射到 revise，
    与原「无报告时按 revise 走」的行为在**本文件的断言范围内**等价，
    同时避免 ``no_review_report`` 硬停把取消用例提前短路。
    """
    return {
        "word_count": 2500,
        "target_word_count": 2500,
        "within_range": True,
        "word_band": {"low": 2125, "high": 2875},
        "warnings": [],
        "errors": [],
        "ai_pattern_hits": [],
    }


def _insert_parent_review_run(db_path: str, report: dict | None = None) -> str:
    """落一行「父 review run」：checkpoint 带 author_review.__pause_payload__.review_report。"""
    return _insert_run(
        db_path,
        status="FAILED",
        error="rejected-for-revision",
        checkpoint={
            "author_review": {
                "__pause_payload__": {
                    "review_report": report if report is not None else _author_revision_report()
                }
            }
        },
    )


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

    2026-09-18 起回路的动作由 ``repair_policy`` 按 pending review 的 ``review_report``
    判定，因此这里的每个 review run（含父 run）都带上 ``_author_revision_report()``
    ——「报告无 error、作者主观驳回」是策略里**仍走 capped revise** 的形状，与本文件
    要测的取消 / ctx 透传语义无关，只负责让回路按既有节奏跑下去。

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
            db_path,
            status="FAILED",
            error="rejected-for-revision",
            checkpoint={
                "author_review": {"__pause_payload__": {"review_report": _author_revision_report()}}
            },
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

    parent = _insert_parent_review_run(db_path)
    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert [c[0] for c in calls] == ["write", "review"], calls
    assert payload["status"] == "CANCELLED"
    assert payload["run_id"] == parent
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

    parent = _insert_parent_review_run(db_path)
    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 3, parent_run_id=parent,
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

    parent = _insert_parent_review_run(db_path)
    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert [c[0] for c in calls] == ["write", "review", "write", "review"]
    # 达上限仍 rejected：返回最后一轮 review 的 FAILED payload
    # （2026-09-18 起额外带轮次耗尽的 ``detail``，点名未能修复的形状）
    assert payload["status"] == "FAILED"
    assert payload["run_id"] == calls[-1][1]
    assert "轮次耗尽" in payload["detail"], payload
    assert "author_requested_revision" in payload["detail"], payload
    # 审计键：最后一轮的决策随 payload 返回（action/reason/shape/detail 齐全）
    assert payload["repair_decision"]["action"] == "revise", payload
    assert payload["repair_decision"]["reason"] == "author_requested_revision", payload
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
    parent = _insert_parent_review_run(db_path)
    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, overrides, intent,
        parent_run_id=parent,
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

    parent = _insert_parent_review_run(db_path)
    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, None, "本书铁律",
        parent_run_id=parent,
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

    parent = _insert_parent_review_run(db_path)
    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1, parent_run_id=parent,
    )

    assert ctx_extras == [None, None], ctx_extras


# ---------------------------------------------------------------------------
# _auto_revise_loop：字数大缺口 ⇒ regenerate（2026-09-18 策略表的一行）
# ---------------------------------------------------------------------------
#
# 线上实证（ch_92bac068ff0d，target=2500 / 带 2125~2875）：writer 首稿 932 字，改稿回路
# 四轮 revise 后 1044 → 1242 → 1300 字（单轮 +4.7%，被 writer 规则 20 的「revise 净增
# ≤ +5%」clamp），每轮 review 仍报 W-LEN-DEVIATION -65.8%，回路必然耗尽轮次。
# 对策：pending review 的字数带下限缺口 > 剩余轮次在 +5%/轮下可达幅度 → 本轮 write 带
# fresh_write（writer 回到 mode='write'，绕开 revise 的「逐字保留 / 不整章重写」）。
# 该分支现由 ``workflows/repair_policy.decide_repair`` 的 ``length_shortfall_beyond_revise_cap``
# 一行产出（策略表逐行覆盖见 tests/unit/test_repair_policy.py）；本节只测回路侧的接线。
#
# 判定的输入是**父 review run** 落盘的 review_report（author_review 的 __pause_payload__），
# 与 resume / 前端 reviewer UI 同一落点——真实 DB 样例见
# workflow_runs.checkpoint_json → author_review → __pause_payload__ → review_report。


def _insert_review_run_with_report(db_path: str, report: dict) -> str:
    """落一行 review run：checkpoint 内带 author_review.__pause_payload__.review_report。"""
    return _insert_run(
        db_path,
        status="FAILED",
        error="rejected-for-revision",
        checkpoint={
            "author_review": {"__pause_payload__": {"review_report": report}}
        },
    )


def _length_report(
    word_count: int, band: tuple[int, int] = (2125, 2875), *, target: int = 2500
) -> dict:
    """review_report 的最小长度相关切片（字段名与 basic_checks 落点一致）。"""
    low, high = band
    return {
        "word_count": word_count,
        "target_word_count": target,
        "deviation_pct": round((word_count - target) / target * 100, 1),
        "within_range": low <= word_count <= high,
        "word_band": {"low": low, "high": high},
        "warnings": [] if low <= word_count <= high else ["[W-LEN-DEVIATION] ..."],
        "errors": [],
    }


def _make_escape_runner(
    db_path: str,
    calls: list[tuple[str, dict | None]],
    review_reports: list[dict] | None = None,
):
    """脚本化 runner：write COMPLETED / review FAILED(rejected-for-revision)。

    记录每次调用的 ``(workflow_name, initial_ctx_extra)``；``review_reports`` 非 None 时，
    第 n 次 review 子 run 的 checkpoint 带第 n 份 review_report（模拟回路内新稿被重新审）。
    ``review_reports`` 未覆盖的轮次用 ``_author_revision_report()``（策略判 revise 的形状，
    让回路按既有节奏跑到 max_iter；2026-09-18 起回路按报告判动作，缺报告 = 硬停）。
    """

    review_seen = 0

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
        nonlocal review_seen
        calls.append((workflow_name, initial_ctx_extra))
        if workflow_name == "chapter-write":
            rid = _insert_run(db_path, status="COMPLETED")
            if on_run_started is not None:
                on_run_started(rid)
            return {"run_id": rid, "status": "COMPLETED", "current_node": None}
        report = _author_revision_report()
        if review_reports is not None and review_seen < len(review_reports):
            report = review_reports[review_seen]
        review_seen += 1
        checkpoint = {"author_review": {"__pause_payload__": {"review_report": report}}}
        rid = _insert_run(
            db_path,
            status="FAILED",
            error="rejected-for-revision",
            checkpoint=checkpoint,
        )
        if on_run_started is not None:
            on_run_started(rid)
        return {"run_id": rid, "status": "FAILED", "current_node": None}

    return fake


def _write_ctx_extras(calls: list[tuple[str, dict | None]]) -> list[dict | None]:
    return [extra for name, extra in calls if name == "chapter-write"]


def test_loop_starts_fresh_write_when_length_shortfall_exceeds_capped_reach(
    tmp_path: Path, monkeypatch
):
    """大缺口（854 vs 带下限 2125，需 +148.8% > 2 轮 × +5% ≈ +10.25%）→ 本轮走 fresh_write。

    突变验证：撤掉 ``revise.py`` 里按 ``decision.regenerate`` 加 ``fresh_write`` 的分支
    → 本测试红。
    """
    db_path = _db(tmp_path)
    parent = _insert_review_run_with_report(db_path, _length_report(854))
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_escape_runner(db_path, calls)
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert [name for name, _ in calls] == [
        "chapter-write", "chapter-review", "chapter-write", "chapter-review",
    ], calls
    write_extras = _write_ctx_extras(calls)
    # 第 1 轮：pending review = 父 run（大缺口）→ fresh_write
    assert write_extras[0] == {"fresh_write": True}, write_extras
    # 第 2 轮：pending review = 回路内那轮 review（策略判「作者主观驳回」→ revise）
    assert write_extras[1] is None, write_extras
    # fresh_write 只对 write 生效，不进 review 子 run 的 ctx
    assert [extra for name, extra in calls if name == "chapter-review"] == [None, None]


def test_loop_keeps_capped_revise_when_shortfall_within_capped_reach(
    tmp_path: Path, monkeypatch
):
    """小缺口（2000 vs 带下限 2125，需 +6.25% < 2 轮 × +5% ≈ +10.25%）→ 仍走 capped revise。

    突变验证：把策略表的「缺口 > 剩余轮次可达幅度」判据去掉（见长度缺口就 regenerate）
    → 本测试红。
    """
    db_path = _db(tmp_path)
    parent = _insert_review_run_with_report(db_path, _length_report(2000))
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_escape_runner(db_path, calls)
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert _write_ctx_extras(calls) == [None, None], calls


def test_loop_stops_without_child_runs_on_continuity_rule(tmp_path: Path, monkeypatch):
    """连续性 / 设定类 error（``RULE_CHAR_DEAD_ACTIVE``）→ **硬停，不启动任何子 run**。

    2026-09-18 起策略表把这类规则划为「停手交人工」（不猜、不自动修）；``stop`` 时回路
    返回 FAILED payload 并带 ``repair_decision``，结论同时追加进该 run 的 ``error``
    （作者在 run 列表里能读到原因）。

    突变验证：把 ``decide_repair`` 里「表外 rule_id ⇒ stop」的分支改成 revise →
    子 run 会被启动 → 本测试红。
    """
    db_path = _db(tmp_path)
    report = _length_report(2400)
    report["errors"] = [{"rule_id": "RULE_CHAR_DEAD_ACTIVE", "severity": "error"}]
    parent = _insert_review_run_with_report(db_path, report)
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_escape_runner(db_path, calls)
    )

    payload = wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert calls == [], calls
    assert payload["status"] == "FAILED", payload
    assert payload["repair_decision"]["action"] == "stop", payload
    assert payload["repair_decision"]["reason"] == "unknown_rule_id", payload
    assert "RULE_CHAR_DEAD_ACTIVE" in payload["detail"], payload
    run = wf.revise.get_run(db_path, parent)
    assert "auto_revise:" in (run["error"] or ""), run
    assert "RULE_CHAR_DEAD_ACTIVE" in (run["error"] or ""), run


def test_loop_keeps_capped_revise_when_draft_is_too_long(tmp_path: Path, monkeypatch):
    """超带（5000 > 上限 2875）不触发 fresh_write：revise 的净增 cap 只管增侧，
    压缩按明确指令幅度净减（writer 规则 20）⇒「capped revise 追不回」不成立。"""
    db_path = _db(tmp_path)
    parent = _insert_review_run_with_report(db_path, _length_report(5000))
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_escape_runner(db_path, calls)
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    assert _write_ctx_extras(calls) == [None, None], calls


def test_loop_recomputes_escape_decision_each_round(tmp_path: Path, monkeypatch):
    """逃逸判定按轮刷新：第 1 轮大缺口 → fresh；回路内 review 报了接近带内的新稿 →
    第 2 轮回到 capped revise（不能一直沿用父 run 的旧报告）。

    突变验证：把 ``pending_review_run_id`` 的刷新（``= review_payload["run_id"]``）撤掉
    → 第 2 轮仍读父 run 的旧报告 → 本测试红。
    """
    db_path = _db(tmp_path)
    parent = _insert_review_run_with_report(db_path, _length_report(854))
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise,
        "_run_workflow_return_payload",
        _make_escape_runner(db_path, calls, review_reports=[_length_report(2100)]),
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 2, parent_run_id=parent,
    )

    write_extras = _write_ctx_extras(calls)
    assert write_extras[0] == {"fresh_write": True}, write_extras
    assert write_extras[1] is None, write_extras


def test_loop_fresh_write_keeps_other_ctx_extras(tmp_path: Path, monkeypatch):
    """fresh_write 与 model_overrides / author_intent 并列（不得覆盖透传的作者约束）。"""
    db_path = _db(tmp_path)
    parent = _insert_review_run_with_report(db_path, _length_report(854))
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        wf.revise, "_run_workflow_return_payload", _make_escape_runner(db_path, calls)
    )

    wf._auto_revise_loop(  # noqa: SLF001
        object(), db_path, "prj_1", "ch_1", None, 1,
        {"creative_writing": "mprof_escape_test"}, "本书铁律",
        parent_run_id=parent,
    )

    assert _write_ctx_extras(calls) == [{
        "model_overrides": {"creative_writing": "mprof_escape_test"},
        "author_intent": "本书铁律",
        "fresh_write": True,
    }], calls
    assert calls[1][1] == {
        "model_overrides": {"creative_writing": "mprof_escape_test"},
        "author_intent": "本书铁律",
    }
