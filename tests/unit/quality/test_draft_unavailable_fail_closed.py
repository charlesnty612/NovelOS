"""质量门 fail-closed：「读不到草稿」必须与「该章没有草稿」分开（critical C-1 / D-6）。

缺陷形状（2026-10-23 第二轮审查实证，Lead 已复现）::

    packages/domain/chapter/draft_resolver.py    读失败 ⇒ return None（与「无草稿」同一个值）
    packages/core/quality/service.py:811          (resolved or {}).get("content") or "" ⇒ 空正文
    packages/core/quality/scoring.py:439-441      if not draft: return 100, issues ⇒ 满分 0 issue
    packages/workflows/chapter_commit/gate.py:473 门禁消费点 ⇒ 放行

⇒ **该章从未被评估，却拿到满分 0 issue**（critical）。

本文件钉三件事（正例 + 反向不变量）：

1. **读失败**（临时库 ``DROP TABLE drafts`` 故障注入）⇒
   - ``resolve_draft`` **抛** ``DraftResolutionError``（不再返回 ``None``）；
   - 质量上下文被显式标记「不可检」（正文 = :data:`DRAFT_UNAVAILABLE`）；
   - 报告**不是满分**（``overall``/``style``/``pacing`` 都不是 100/正数），
     且 **issues 里有一条可见记录**（``scoring_missing_subscore``，属阻断白名单）。
2. **反向不变量（防过度收紧）**：``drafts`` 表在、该章**确实没有草稿** ⇒ 走**既有**行为
   （空正文、``style``/``pacing`` = 100、**无**「不可检」标记）——**逐字不变**。
   别把「无草稿」也变成报错。
3. **正常路径**：有草稿 ⇒ 正文原样进上下文，**无**「不可检」标记。

断言口径（全局约束 8）：断言打在**实际分数 / issue 列表**上，不看返回类型、
不看「函数被调用」这类恒真断言。

⚠️ 故障注入用的是**临时库**（``tmp_path``），不碰 ``data/novelos.db``，零真实 LLM 调用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.aggregate import compute_overall
from packages.core.quality.engine import QualityEngine
from packages.core.quality.models import QualityContext
from packages.core.quality.scoring import DRAFT_UNAVAILABLE
from packages.core.quality.service import build_quality_context
from packages.domain.chapter.draft_resolver import DraftResolutionError, resolve_draft
from packages.workflows.chapter_commit.gate import _quality_gate_node
from tests.unit.quality.test_engine import _full_delta

# 「不可检」标记在 issues 里的 rule_id：与 aggregate.compute_overall 的
# 「子分缺失」口径**同一个 id**（它在 issues.BLOCKING_RULES 内 ⇒ 门禁会拦下）。
# ⚠️ 复用而不新增 id 的理由见报告「需要 Lead 协调」段（新增 rule_id 会改
# scoring_formula_hash 并牵动三处文档，超出本任务范围）。
UNAVAILABLE_RULE_ID = "scoring_missing_subscore"

#: 「不可检」语义的**可判定标记**（2026-10-23 对抗审查 A · T1-4 登记）：
#: 同一 rule_id 覆盖两类语义 ⇒ 客户端按 message / suggestion 判别。
UNAVAILABLE_MESSAGE_PREFIX = "正文读不到"
UNAVAILABLE_SUGGESTION_PREFIX = "不可检："

_DRAFT_TEXT = "他把窗子推开，风灌进来，桌上的纸角翻了两下就压住了。"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    return str(db_path)


def _project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="不可检项目"))["project_id"]


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


def _drop_drafts_table(db_path: str) -> None:
    """故障注入：让 ``drafts`` 表不可用（部分迁移 / 缺该表的备份 / 表损坏）。"""
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE drafts")
        conn.commit()
    finally:
        conn.close()


def _rule_ids(report) -> set[str]:
    return {i.rule_id for i in report.issues}


def _plan_json(db_path: str, cid: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["plan_json"] or "{}")


def _set_plan_json(db_path: str, cid: str, payload: dict) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
            (json.dumps(payload, ensure_ascii=False), cid),
        )
        conn.commit()
    finally:
        conn.close()


def _gate_ctx(db_path: str, pid: str, cid: str, mode: str) -> dict:
    return {
        "db_path": db_path,
        "project_id": pid,
        "chapter_id": cid,
        "delta": _full_delta(cid),
        "snapshot_pre": {},
        "quality_gate_mode": mode,
    }


# ---------------------------------------------------------------------------
# 1) 读失败 ⇒ 抛域异常（不再与「无草稿」共用 None）
# ---------------------------------------------------------------------------


def test_resolver_raises_when_drafts_table_is_unavailable(tmp_path: Path):
    """故障注入：``drafts`` 表不可用 ⇒ 抛 ``DraftResolutionError``，**不是** ``None``。

    改造前这里返回 ``None``（与「该章没有草稿」同值）⇒ 上游把「没量到」当成
    「量过了、没问题」。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    with pytest.raises(DraftResolutionError):
        resolve_draft(db_path, cid)


def test_resolver_raises_when_database_cannot_be_opened(tmp_path: Path):
    """库打不开（路径不是文件）⇒ 同样抛域异常，不静默降级成「无草稿」。"""
    not_a_db = tmp_path / "i-am-a-directory"
    not_a_db.mkdir()

    with pytest.raises(DraftResolutionError):
        resolve_draft(str(not_a_db), "ch_whatever")


# ---------------------------------------------------------------------------
# 2) 读失败 ⇒ 报告「不可检」（不是满分 0 issue）
# ---------------------------------------------------------------------------


def test_quality_context_is_marked_unavailable_when_draft_cannot_be_read(tmp_path: Path):
    """读失败 ⇒ 上下文被显式标记（正文 = 占位符），**不是**空正文。"""
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_full_delta(cid),
        snapshot_pre={},
    )

    assert ctx.draft == DRAFT_UNAVAILABLE
    assert ctx.draft != ""
    assert ctx.draft_version is None


def test_report_is_not_a_perfect_score_when_draft_cannot_be_read(tmp_path: Path):
    """核心判别：读失败 ⇒ **不**产出「满分 0 issue」的报告，且有一条可见记录。"""
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_full_delta(cid),
        snapshot_pre={},
    )
    report = QualityEngine().evaluate(ctx)

    # ① 不是「看起来一切正常的满分报告」
    assert report.overall == 0
    assert report.style == 0
    assert report.pacing == 0
    # ② 有可见信号：issues 里有一条「子分不可得」的记录
    assert UNAVAILABLE_RULE_ID in _rule_ids(report)
    unavailable = [i for i in report.issues if i.rule_id == UNAVAILABLE_RULE_ID]
    assert unavailable, "读失败必须留下可见的不可检记录"
    assert all(i.severity == "error" for i in unavailable)


def test_gate_blocks_when_draft_cannot_be_read(tmp_path: Path):
    """门禁消费点：读失败 ⇒ 门禁**拦下**（enforce 模式，默认）。

    错误消息走「不可检」这一支（**不是** 「quality gate blocked: [...]」）——见
    `test_gate_unavailable_is_a_separate_error_category` 的完整断言。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    with pytest.raises(ValueError) as exc:
        _quality_gate_node(_gate_ctx(db_path, pid, cid, "enforce"))
    assert "不可检" in str(exc.value)


def test_gate_blocks_even_in_report_mode(tmp_path: Path):
    """report 模式（含快通道）也必须拦：「不可检」不是质量意见，是**根本没评估**。

    report 模式放行的是「评估过、但有意见」的章；正文读不到时放行 = 给
    **从未被评估**的章节发通行证 ⇒ 一律硬停。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    with pytest.raises(ValueError) as exc:
        _quality_gate_node(_gate_ctx(db_path, pid, cid, "report"))
    assert "不可检" in str(exc.value)


# ---------------------------------------------------------------------------
# 2b) 「不可检」是**单独一类**（2026-10-23 对抗审查 A · T1-3）
# ---------------------------------------------------------------------------


def test_gate_unavailable_is_a_separate_error_category(tmp_path: Path):
    """不可检 ⇒ 错误消息自带「不可检」类别，**不带**「子分缺失」那条误导建议。"""
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    with pytest.raises(ValueError) as exc:
        _quality_gate_node(_gate_ctx(db_path, pid, cid, "enforce"))
    message = str(exc.value)

    # ① 独立类别 + 说清真因
    assert "quality gate unavailable" in message
    assert "不可检" in message
    assert "草稿" in message
    # ② **不**复用「子分缺失 ⇒ 补 plan_json / submit_delta」那条建议（方向完全错）
    assert "submit_delta" not in message
    assert "补全 chapters.plan_json" not in message
    assert "guidance=" not in message


def test_gate_unavailable_does_not_write_revision_note_or_gate_blocked(tmp_path: Path):
    """不可检 ⇒ **不写** ``plan_json.revision_note`` / ``gate_blocked``。

    那是「质量不合格」的出路；「没量到」没有改稿出路（作者照着改稿建议去改
    plan/正文是白费功夫）⇒ plan_json 必须**逐字未变**。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    # 先放一个可辨认的既有内容 ⇒ 「未变」这条断言才有意义（不是空字典自证）
    _set_plan_json(db_path, cid, {"author_note": "keep-me"})
    before = _plan_json(db_path, cid)
    _drop_drafts_table(db_path)

    with pytest.raises(ValueError):
        _quality_gate_node(_gate_ctx(db_path, pid, cid, "enforce"))

    after = _plan_json(db_path, cid)
    assert after == before == {"author_note": "keep-me"}
    assert "revision_note" not in after
    assert "gate_blocked" not in after


def test_gate_unavailable_does_not_emit_revision_guidance(tmp_path: Path):
    """不可检 ⇒ 不产出改稿建议（``ctx['quality_gate']['revision_guidance']`` 必须为空）。

    「补全 chapters.plan_json 或 StoryStateService.submit_delta 后重跑」是
    **错误方向**的误导建议（那是「子分缺失」的出路，不是「没量到」的出路）。
    该 dict 会被工作流引擎写进失败 run 的 ``checkpoint_json`` ⇒ 作者能在章节页看到
    ⇒ 必须为空，而不是塞一条指向无关方向的建议。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _drop_drafts_table(db_path)

    ctx = _gate_ctx(db_path, pid, cid, "enforce")
    with pytest.raises(ValueError):
        _quality_gate_node(ctx)

    quality_gate = ctx["quality_gate"]
    assert quality_gate["blocked"] is True
    assert quality_gate["revision_guidance"] == []
    # 整段 JSON 也不许出现那条误导方向
    assert "submit_delta" not in json.dumps(quality_gate, ensure_ascii=False)


def test_unavailable_issue_is_distinguishable_from_aggregate_missing_subscore():
    """T1-4：同一 rule_id 覆盖两类语义 ⇒ message / suggestion 必须明显可区分。

    - aggregate 侧（子分为 None）：message = 「{name} 子分缺失」；
    - scoring 侧（正文读不到）：message 以「正文读不到」开头、suggestion 以「不可检：」开头。
    ⇒ 客户端按 message / suggestion 判别，**别只看 rule_id**。
    """
    # aggregate 侧：只有 style 为 None ⇒ 恰好一条「style 子分缺失」
    _overall, agg_issues = compute_overall(
        {
            "plot": 85,
            "character": 100,
            "continuity": 100,
            "style": None,
            "pacing": 100,
            "foreshadowing": 100,
            "ai_trace": 100,
        },
        [],
    )
    agg = [i for i in agg_issues if i.rule_id == UNAVAILABLE_RULE_ID]
    assert len(agg) == 1
    assert agg[0].message == "style 子分缺失"

    # scoring 侧：正文读不到 ⇒ 两条（style / pacing）都带可判别标记
    report = QualityEngine().evaluate(
        QualityContext(
            chapter_id="ch_unavailable",
            chapter_number=1,
            draft=DRAFT_UNAVAILABLE,
        )
    )
    mine = [i for i in report.issues if i.rule_id == UNAVAILABLE_RULE_ID]
    assert len(mine) == 2
    assert all(i.message.startswith(UNAVAILABLE_MESSAGE_PREFIX) for i in mine)
    assert all(
        (i.suggestion or "").startswith(UNAVAILABLE_SUGGESTION_PREFIX) for i in mine
    )
    # 两类语义的消息**不相同** ⇒ 客户端可据此判别
    assert all(i.message != agg[0].message for i in mine)


# ---------------------------------------------------------------------------
# 3) 反向不变量：「该章确实没有草稿」⇒ 既有行为逐字不变
# ---------------------------------------------------------------------------


def test_no_draft_chapter_keeps_the_existing_behaviour(tmp_path: Path):
    """``drafts`` 表在、该章没有草稿 ⇒ 空正文 + style/pacing = 100 + 无「不可检」标记。

    这是**防过度收紧**的反向不变量：别把「无草稿」也变成报错/0 分。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)

    # 无草稿 ⇒ 正常业务状态，返回 None（**不抛错**）
    assert resolve_draft(db_path, cid) is None

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_full_delta(cid),
        snapshot_pre={},
    )
    assert ctx.draft == ""
    assert ctx.draft != DRAFT_UNAVAILABLE
    assert ctx.draft_version is None

    report = QualityEngine().evaluate(ctx)
    assert report.style == 100
    assert report.pacing == 100
    assert UNAVAILABLE_RULE_ID not in _rule_ids(report)


def test_existing_draft_still_flows_into_the_context(tmp_path: Path):
    """正常路径：正文原样进上下文，**无**「不可检」标记（防误伤）。"""
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _draft(db_path, cid, 1, _DRAFT_TEXT)

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_full_delta(cid),
        snapshot_pre={},
    )
    assert ctx.draft == _DRAFT_TEXT
    assert ctx.draft_version == 1

    report = QualityEngine().evaluate(ctx)
    assert UNAVAILABLE_RULE_ID not in _rule_ids(report)
    assert report.overall > 0


# ---------------------------------------------------------------------------
# 4) 端到端：API 评估端点（D-6 判别里点名的那个端点）
# ---------------------------------------------------------------------------


def test_api_evaluate_returns_an_explicit_error_not_a_perfect_report(tmp_path: Path):
    """``POST /api/chapters/{cid}/quality/evaluate`` 在草稿表不可用时的**契约**。

    实测（临时库 + ``DROP TABLE drafts``，真实返回码与响应体见报告回执）：
    **201** + ``overall == 0`` + ``issues_json`` 里出现 ``scoring_missing_subscore``
    且其 ``message`` 以「正文读不到」开头。

    ⚠️ **强断言**（2026-10-23 对抗审查 A · T1-1 返工）：改造前的写法是
    ``assert resp.status_code in (201, 500)`` + ``if 201: ... else: 非空文本``
    ⇒ 实现退化成**裸 500**（用户只看到「服务器错误」）也全绿 ⇒ 契约没被守住。
    现在把返回码、rule_id、overall、message 四项**逐字**钉死。

    ⚠️ **判别靠 rule_id + message，不靠 overall**：该端点自己传 ``delta={}``
    ⇒ ``SCHEMA_VALIDATION_FAILED``（阻断级）⇒ **overall 恒为 0**（改造前就如此）。
    ⇒ 「唯一新增的可观察信号」= ``issues_json`` 里那条不可检记录。
    """
    import asyncio

    from tests.api._shared import _create_app, _make_client

    app = _create_app(tmp_path)
    db_path = str(app.state.settings.db_path)

    async def setup() -> str:
        async with _make_client(app) as client:
            pid_res = await client.post("/api/projects", json={"name": "不可检 API 项目"})
            assert pid_res.status_code == 201, pid_res.text
            pid = pid_res.json()["project_id"]
            cid_res = await client.post(
                f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "不可检章"},
            )
            assert cid_res.status_code == 201, cid_res.text
            return cid_res.json()["chapter_id"]

    async def evaluate(cid: str) -> object:
        async with _make_client(app) as client:
            return await client.post(f"/api/chapters/{cid}/quality/evaluate", json={})

    cid = asyncio.run(setup())

    # ① 对照组：drafts 表可用（该章无草稿）⇒ 端点 201，且**不得**出现不可检记录
    ok_resp = asyncio.run(evaluate(cid))
    assert ok_resp.status_code == 201, ok_resp.text
    ok_rule_ids = {i["rule_id"] for i in ok_resp.json()["issues_json"]}
    assert UNAVAILABLE_RULE_ID not in ok_rule_ids, ok_resp.json()["issues_json"]

    # ② 故障注入：草稿表不可用 ⇒ 端点**仍 201**，但必须出现不可检记录
    _drop_drafts_table(db_path)
    resp = asyncio.run(evaluate(cid))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["overall"] == 0, body
    unavailable = [
        i for i in body["issues_json"] if i["rule_id"] == UNAVAILABLE_RULE_ID
    ]
    assert unavailable, body["issues_json"]
    # 可见信号的具体形态（不只是 rule_id 命中）
    assert all(
        i["message"].startswith(UNAVAILABLE_MESSAGE_PREFIX) for i in unavailable
    )
    assert all(
        (i.get("suggestion") or "").startswith(UNAVAILABLE_SUGGESTION_PREFIX)
        for i in unavailable
    )

