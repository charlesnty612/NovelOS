"""提交门禁必须量「被审那一版」（2026-09-18 本批次第 3 项）。

缺陷（形状与 P1-1 同源，站点在提交侧）：``chapter_commit/gate.py`` 调
``build_quality_context(...)`` 时**不传** ``draft_version`` ⇒ 门禁量的是**最新**草稿，
而作者审的是评审 run 提交给他的那一版。作者评审后手改出新版本
（``POST /drafts`` 在 DRAFTED / REVIEWED 下都允许，质量报告还用 Q8 人工占比鼓励它）
就会出现「门禁批准了没人审过的正文」——同一份报告里「测量对象 ≠ 被审对象」。

接缝选择（investigate & justify）：被审版本的**权威来源**是评审 run 自己的
``author_review.__pause_payload__.review_report.draft_version``（评审节点用
``resolve_draft`` 与正文**同源**产出该字段，P1-1 已收敛）；``ctx["draft_version"]``
是调用方显式指定的更高优先层（start 端点该字段对所有 workflow 都进 ctx，
此前 commit 侧无人消费）。两者都取不到（历史数据 / 直接构造 ctx 的测试）⇒ 退回
「取最新」，与改造前逐字节一致。``resolve_draft`` 本身不是接缝：它回答的是
「哪一版是最新 / 指定版是哪一版」，回答不了「作者审的是哪一版」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.service import QualityService
from packages.workflows.chapter_commit import gate as gate_mod
from packages.workflows.chapter_commit.gate import _quality_gate_node, _reviewed_draft_version
from tests.unit.quality.test_engine import _full_delta

# 触发 ``AI-BEAT-REPEAT``（CONFIRM_RULES 成员）的正文：同一段素材隔 ~200 字写两次。
_REPEAT_CLAUSE = (
    "沈砚低头看着桌上那张收据的复印件，付款方一栏的江氏集团有限公司几个字"
    "在台灯下显得格外清楚。"
)
_OTHER = "窗外传来一阵脚步声，走廊尽头的灯忽明忽暗，值班的人把外套裹紧了些。"
_REPEATED_DRAFT = (
    _REPEAT_CLAUSE
    + "\n\n"
    + (_REPEAT_CLAUSE + _OTHER) * 3
    + "\n\n"
    + "桌上那份收据的复印件还摆着，付款方一栏的江氏集团有限公司几个字在晨光里格外清晰。"
)
# 最新一版：干净（无任何 confirm 命中）——若门禁量了它，下面两条断言都会反过来。
_CLEAN_DRAFT = "他把窗子推开，风灌进来，桌上的纸角翻了两下就压住了。"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="门禁版本项目"))["project_id"]


def _chapter(db_path: str, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C1', '{}', 'REVIEWED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _draft(db_path: str, cid: str, version: int, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'human', ?)",
            (new_id("drf"), cid, version, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _review_run(db_path: str, cid: str, *, draft_version: int, started_at: str) -> str:
    """插一条 chapter-review run，其 checkpoint 带该次评审量过的版本（P1-1 口径）。"""
    run_id, wf_id = new_id("wfr"), new_id("wf")
    now = now_iso()
    checkpoint = {
        "author_review": {
            "__pause_payload__": {
                "stage": "chapter-review",
                "review_report": {"draft_version": draft_version, "word_count": 100},
            }
        }
    }
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, 'PAUSED', 'author_review', ?, ?, ?)",
            (run_id, wf_id, cid, json.dumps(checkpoint, ensure_ascii=False), started_at, started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _ctx(db_path: str, pid: str, cid: str, mode: str, **extra) -> dict:
    ctx = {
        "db_path": db_path,
        "project_id": pid,
        "chapter_id": cid,
        "delta": _full_delta(cid),
        "snapshot_pre": {},
        "quality_gate_mode": mode,
    }
    ctx.update(extra)
    return ctx


def _incident(db_path: str, *, with_review: bool = True) -> tuple[str, str]:
    """事故形状：作者审的是 v1（重复稿），评审后又改出干净的最新版 v2。"""
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _draft(db_path, cid, 1, _REPEATED_DRAFT)
    _draft(db_path, cid, 2, _CLEAN_DRAFT)
    if with_review:
        _review_run(db_path, cid, draft_version=1, started_at="2026-09-18T10:00:00+08:00")
    return pid, cid


# ---------------------------------------------------------------------------
# 1) 接缝本体
# ---------------------------------------------------------------------------


def test_explicit_ctx_version_wins_over_the_review_payload(tmp_path: Path):
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path)
    ctx = _ctx(db_path, pid, cid, "report", draft_version=2)
    assert _reviewed_draft_version(db_path, cid, ctx) == 2


def test_review_payload_is_used_when_ctx_has_no_version(tmp_path: Path):
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path)
    assert _reviewed_draft_version(db_path, cid, _ctx(db_path, pid, cid, "report")) == 1


def test_newest_review_run_wins(tmp_path: Path):
    """多次评审：取**最近一次**评审量过的版本（旧 run 的版本不作数）。"""
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path, with_review=False)
    _review_run(db_path, cid, draft_version=2, started_at="2026-09-18T09:00:00+08:00")
    _review_run(db_path, cid, draft_version=1, started_at="2026-09-18T11:00:00+08:00")
    assert _reviewed_draft_version(db_path, cid, _ctx(db_path, pid, cid, "report")) == 1


def test_without_any_review_run_falls_back_to_latest(tmp_path: Path):
    """无评审记录（历史数据 / 直接构造 ctx 的调用方）⇒ ``None`` =「取最新」。

    这是**改造前**的行为，必须逐字节保留（否则旧调用方会在没有评审记录时炸）。
    """
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path, with_review=False)
    assert _reviewed_draft_version(db_path, cid, _ctx(db_path, pid, cid, "report")) is None


def test_malformed_checkpoint_is_skipped(tmp_path: Path):
    """checkpoint 损坏 / 字段形态不符 ⇒ 跳过该 run（门禁不因观测数据缺失而炸）。"""
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path, with_review=False)
    wf_id = new_id("wf")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        for checkpoint in ("not-json", json.dumps({"author_review": {"__pause_payload__": {}}})):
            conn.execute(
                "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
                "current_node, checkpoint_json, started_at, ended_at) "
                "VALUES (?, ?, ?, 'PAUSED', 'author_review', ?, ?, ?)",
                (new_id("wfr"), wf_id, cid, checkpoint, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    assert _reviewed_draft_version(db_path, cid, _ctx(db_path, pid, cid, "report")) is None


# ---------------------------------------------------------------------------
# 2) 门禁节点端到端：报告标签与它实际量过的正文同源
# ---------------------------------------------------------------------------


def test_gate_measures_the_reviewed_version_not_the_latest(tmp_path: Path):
    """report 模式（不阻断）下：落库报告必须指到 v1，且带 v1 正文的特征条目。

    ``AI-BEAT-REPEAT`` 只在 v1 里有 ⇒ 报告里出现它，就是「门禁真的量了被审那版」的
    直接证据（而不是只看 draft_version 字段自证）。
    """
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path)
    out = _quality_gate_node(_ctx(db_path, pid, cid, "report"))

    assert out["quality_reviewed_draft_version"] == 1
    stored = QualityService(db_path).latest_report(cid)
    assert stored is not None
    assert stored["draft_version"] == 1
    assert "AI-BEAT-REPEAT" in {i["rule_id"] for i in stored["issues_json"]}


def test_gate_blocks_the_reviewed_version_confirm_hit(tmp_path: Path):
    """enforce 模式下，被审版本里的 confirm 命中必须阻断（且给出该 rule_id 的证据）。

    改造前两条都不成立：AI-* 不进质量路径 ⇒ 无 confirm 可判；即使进了，量的也是
    干净的 v2 ⇒ 静默通过。本用例同时钉住接线（第 1 项）与版本（第 3 项）。
    """
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path)
    with pytest.raises(ValueError) as exc:
        _quality_gate_node(_ctx(db_path, pid, cid, "enforce"))
    message = str(exc.value)
    assert "AI-BEAT-REPEAT" in message
    assert "gate=confirm" in message

    plan = _plan_json(db_path, cid)
    assert plan["gate_blocked"]["gate"] == "confirm"
    # 被审版本是「同一段素材写了好几遍」的稿子（实测 trigram 0.6972），故同时命中
    # trigram 的 confirm 档——2026-09-18 起该档由产出侧按量级上修（不再靠规则表），
    # 本用例正是这条链路的回归。两条都必须在列。
    assert set(plan["gate_blocked"]["rule_ids"]) == {
        "AI-BEAT-REPEAT",
        "RULE_STYLE_REPETITION_TRIGRAM",
    }


def test_gate_override_releases_the_reviewed_version_hit(tmp_path: Path):
    """显式接受声明能放行——confirm 档不是硬停（P0-1 语义在被审版本上同样成立）。"""
    db_path = _db(tmp_path)
    pid, cid = _incident(db_path)
    out = _quality_gate_node(
        _ctx(
            db_path, pid, cid, "enforce",
            gate_override={
                "rule_ids": ["AI-BEAT-REPEAT", "RULE_STYLE_REPETITION_TRIGRAM"],
                "reason": "刻意的复沓",
            },
        )
    )
    assert out["quality_reviewed_draft_version"] == 1
    assert out["quality_gate_accepted_override"]["reason"] == "刻意的复沓"


def _plan_json(db_path: str, cid: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["plan_json"] or "{}")


def test_quality_gate_module_is_the_seam_under_test():
    """防误报：断言测的是本模块的接缝（import 面漂移时立刻现形）。"""
    assert gate_mod._reviewed_draft_version is _reviewed_draft_version
