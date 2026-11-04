"""批次三十六 · 批 6：story_state / branches / simulation / skill 指纹域回归。

覆盖 4 条已实证缺陷（零真实 LLM、零内容仓、零 dev 库；全部 ``tmp_path`` 临时库）：

- **C-2** 分支含 HIGH 风险 delta ⇒ promote 永远 409（前置预检 + 审批透传）；
- **C-14** skills 指纹缺 ``name`` 维度 ⇒ 改名后脏命中旧装配（硬规则 2）；
- **C-29** 自定义 ``name`` 的推演在列表与重放双出口不可见；
- **C-30** delivery evidence 逐章新建连接（单连接复用，批次二十三同款）。

断言一律打在**真实值**上：DB 实际列 / 异常属性 / 装配 payload 实际内容 / 连接计数。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

import packages.core.delivery.evidence as evidence_mod
import packages.domain.chapter.draft_resolver as draft_resolver_mod
from packages.core.context_engine.builders import _cache_reset, build_director_input
from packages.core.context_engine.cache import _fingerprint_skills
from packages.core.db import get_connection
from packages.core.delivery.evidence import _length_evidence_map
from packages.core.quality.wordcount import classify_prose_length
from packages.core.simulation import SimulationService
from packages.core.skill import SkillService
from packages.core.story_state.exceptions import ApprovalRequiredError
from packages.core.story_state.service import StoryStateService
from tests._db_fixtures import migrated_db

from .skill_fixtures import bind_skills, insert_chapter, insert_project

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _seed_project(db_path: Path, *, chapters: int = 1) -> dict:
    """最小 fixture：project + character + N 章 + genesis v1（形状取自 DDL 列）。"""
    suffix = uuid.uuid4().hex[:8]
    pid = f"prj_{suffix}"
    cid = f"char_{suffix}"
    chapter_ids = [f"ch_{suffix}_{i}" for i in range(1, chapters + 1)]
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (pid, "b36 项目", _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, created_at, updated_at) VALUES (?, ?, 'Alice', 'protagonist', ?, "
            "'VISIBLE', ?, ?)",
            (cid, pid, json.dumps({"personality": "calm"}), _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, created_at) VALUES (?, 1, ?, 'VISIBLE', ?)",
            (cid, json.dumps({"location": "Village", "emotion": "calm"}), _now_iso()),
        )
        for idx, chapter_id in enumerate(chapter_ids, start=1):
            conn.execute(
                "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
                "status, visibility, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', ?, ?)",
                (chapter_id, pid, idx, f"第{idx}章", _now_iso(), _now_iso()),
            )
        conn.commit()
    finally:
        conn.close()
    StoryStateService(db_path).init_genesis(pid, chapter_ids[0])
    return {"project_id": pid, "character_id": cid, "chapter_ids": chapter_ids}


def _meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": _now_iso(),
        "supersedes": None,
        "notes": None,
    }


def _low_state_delta(delta_id: str, chapter_id: str, cid: str, prev: int) -> dict:
    return {
        **_meta(delta_id, chapter_id, prev),
        "character_changes": [
            {
                "change_id": f"{delta_id}_cc",
                "op": "update",
                "target_id": cid,
                "character_id": cid,
                "facet": "state",
                "field": "state.location",
                "before": "Village",
                "after": "Cave",
                "confidence": 0.9,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def _high_definition_delta(delta_id: str, chapter_id: str, cid: str, prev: int) -> dict:
    """HIGH 风险：角色 ``facet=definition``（state-delta-v0 §2.5 触发审批门）。"""
    return {
        **_meta(delta_id, chapter_id, prev),
        "character_changes": [
            {
                "change_id": f"{delta_id}_cc_high",
                "op": "update",
                "target_id": cid,
                "character_id": cid,
                "facet": "definition",
                "field": "core.personality",
                "before": "calm",
                "after": "reckless",
                "confidence": 0.9,
                "evidence": _evidence(chapter_id),
                "risk_level": "HIGH",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def _count_main_commits(db_path: Path, project_id: str) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM commits c
            JOIN branches b ON b.branch_id = c.branch_id
            WHERE c.project_id = ? AND b.name = 'main'
            """,
            (project_id,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# C-2：分支含 HIGH 风险 delta ⇒ promote 前置预检 + 审批透传
# ---------------------------------------------------------------------------


def test_promote_high_risk_precheck_blocks_before_any_replay(tmp_path: Path):
    """未获批 ⇒ 重放**之前**抛 ApprovalRequiredError ⇒ 零 main commit（无半 promote）。

    判别力（突变 M1「撤掉前置预检」）：分支上先放一条 LOW commit、再放 HIGH commit
    ⇒ 若预检被撤，LOW 那条会先重放成功（产生 1 条 main commit），到 HIGH 才炸。
    所以本用例断言「失败后 main commit 数不增长」只在预检存在时成立。
    """
    db_path = migrated_db(tmp_path)
    seed = _seed_project(db_path)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_ids"][0]
    state = StoryStateService(db_path)

    branch = state.create_branch(pid, "feature-high")
    bid = branch["branch_id"]
    # genesis 本身在 main 上落 1 条 commit ⇒ 基线取「分支提交写完、promote 之前」。
    main_commits_baseline = _count_main_commits(db_path, pid)

    low = _low_state_delta("dlt_b36_low", chap, cid, 1)
    sub = state.submit_delta(low, branch_id=bid)
    assert sub["status"] == "validated"
    state.commit_delta(sub["delta_id"], {"approver": "user:branch"}, "wfr_low", branch_id=bid)

    high = _high_definition_delta("dlt_b36_high", chap, cid, 2)
    sub = state.submit_delta(high, branch_id=bid)
    assert sub["status"] == "validated"
    state.commit_delta(
        sub["delta_id"], {"approver": "user:branch", "approved": True}, "wfr_high",
        branch_id=bid,
    )

    assert _count_main_commits(db_path, pid) == main_commits_baseline

    with pytest.raises(ApprovalRequiredError) as excinfo:
        state.promote_branch(pid, bid)

    # 清单完整且只含真正 HIGH 的那条（LOW commit 不得混入）。
    assert excinfo.value.high_risk_change_ids == ["dlt_b36_high_cc_high"]
    # 预检前置：main 上一条新 commit 都没产生（= 不存在「已重放一半」）。
    assert _count_main_commits(db_path, pid) == main_commits_baseline, (
        "预检必须在任何重放之前失败；main 上出现新 commit 说明预检被绕过（半 promote）"
    )
    # 分支仍可重试（保持 ACTIVE，未被误标 MERGED）。
    conn = get_connection(db_path)
    try:
        status = conn.execute(
            "SELECT status FROM branches WHERE branch_id = ?", (bid,)
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "ACTIVE"


def test_promote_high_risk_succeeds_with_approval_and_audits_approver(tmp_path: Path):
    """获批 ⇒ promote 成功，且 ``commits.author_approval_json`` 如实记录审批人。

    判别力（突变 M2「撤掉 approved 并入 ap」）：预检通过后重放 HIGH delta 时，
    ``commit_delta`` 的门禁仍要求 ``author_approval.approved is True`` ⇒ 撤掉并进
    则 promote 在重放阶段抛 ApprovalRequiredError ⇒ 本用例必红。
    """
    db_path = migrated_db(tmp_path)
    seed = _seed_project(db_path)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_ids"][0]
    state = StoryStateService(db_path)

    bid = state.create_branch(pid, "feature-high-approved")["branch_id"]
    main_commits_baseline = _count_main_commits(db_path, pid)
    for delta, prev, wfr in (
        (_low_state_delta("dlt_b36_ok_low", chap, cid, 1), 1, "wfr_ok_low"),
        (_high_definition_delta("dlt_b36_ok_high", chap, cid, 2), 2, "wfr_ok_high"),
    ):
        sub = state.submit_delta(delta, branch_id=bid)
        assert sub["status"] == "validated"
        state.commit_delta(
            sub["delta_id"],
            {"approver": "user:branch", "approved": True},
            wfr,
            branch_id=bid,
        )

    result = state.promote_branch(
        pid,
        bid,
        author_approval={"approved": True, "approver": "user:ui-reviewer"},
    )
    assert result["promoted_commits"] == 2
    assert _count_main_commits(db_path, pid) == main_commits_baseline + 2

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT author_approval_json
            FROM commits c
            JOIN branches b ON b.branch_id = c.branch_id
            WHERE c.project_id = ? AND b.name = 'main'
            ORDER BY c.resulting_state_version DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    audit = json.loads(row["author_approval_json"])
    assert audit["status"] == "approved"
    assert audit["approver"] == "user:ui-reviewer", (
        f"审计必须记录真实审批人，实得 {audit.get('approver')!r}"
    )
    assert audit["high_risk_change_ids"] == ["dlt_b36_ok_high_cc_high"]


# ---------------------------------------------------------------------------
# C-14：skills 指纹含 name 维度
# ---------------------------------------------------------------------------


def test_fingerprint_skills_includes_name_dimension():
    """同一 ``id:version`` 下改名 ⇒ 指纹必须变（M1 同形：只按 id/version 会脏命中）。"""
    old_name = "旧名".encode("utf-8").hex()
    new_name = "新名".encode("utf-8").hex()
    f_old = _fingerprint_skills(f"skl_a:1:{old_name}")
    f_new = _fingerprint_skills(f"skl_a:1:{new_name}")
    assert f_old != f_new, "name 是注入块字段，改名必须改指纹"
    assert len(f_old) == len(f_new) == 16

    # name 可含 ':' / '|'；hex 编码后不产生分隔符歧义（解析出的是原字符串）。
    weird = "甲|乙:丙".encode("utf-8").hex()
    assert _fingerprint_skills(f"skl_a:1:{weird}") not in (f_old, f_new)
    # 旧两段式仍被接受（直接调用方兼容）。
    assert len(_fingerprint_skills("skl_a:1")) == 16


def test_main_and_fallback_skills_ref_are_identical(tmp_path: Path):
    """主 JOIN 与降级路径产出的 ``skills_ref`` 串**逐字相同**（硬规则 2 同形）。

    两条路径任一漏掉 ``name``（或编码方式不同）⇒ 同一 DB 状态算出两个指纹 ⇒
    降级路径脏命中。这里直接比对**串本身**，比比对指纹更容易定位分叉点。
    """
    from packages.core.context_engine.builders_common.common import (
        _peek_chapter_context,
        _try_project_skills_ref,
    )

    db_path = migrated_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    from .skill_fixtures import make_skill

    make_skill(db_path, skill_id="skl_b36_ref", name="带:分隔|符的名字")
    bind_skills(db_path, pid, ["skl_b36_ref"])

    peek = _peek_chapter_context(db_path, cid, project_id=pid)
    conn = get_connection(db_path)
    try:
        fallback = _try_project_skills_ref(conn, pid)
    finally:
        conn.close()

    assert peek["skills_ref"] == fallback
    expected_hex = "带:分隔|符的名字".encode("utf-8").hex()
    # SQLite ``hex()`` 返回大写十六进制（解码两侧等价，故比较按小写归一）。
    assert peek["skills_ref"].lower().endswith(expected_hex), (
        f"name 必须以 hex 编码进串（避免 ':' / '|' 歧义）；实得 {peek['skills_ref']!r}"
    )


def test_renaming_skill_row_invalidates_director_cache(tmp_path: Path):
    """改 ``skills.name``（payload/version 不动）⇒ 下次装配拿到新名字且缓存 miss。

    ⚠️ 夹具刻意使用 **payload 不带 name** 的存量形态：``skill_block`` 的取名顺序是
    ``payload.name or skills.name`` ⇒ 只有这种形态下「改行名」才改变注入块（这也是
    C-14 的实证路径）。判别点打在**装配 payload 的实际内容**上。
    """
    db_path = migrated_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)

    from packages.core.context_engine import cache as cache_mod

    svc = SkillService(db_path)
    svc.create(
        {
            "skill_id": "skl_b36_name",
            "name": "旧显示名",
            "payload": {
                "schema_version": "skill.v1.0.0",
                "instructions": "手艺说明。",
                "applies_to": ["director"],
            },
        }
    )
    bind_skills(db_path, pid, ["skl_b36_name"])

    _cache_reset()
    p1 = build_director_input(db_path, pid, cid, "意图")
    assert p1["skills"]["items"][0]["name"] == "旧显示名"
    keys_before = {k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"}
    assert len(keys_before) == 1

    updated = svc.update("skl_b36_name", {"name": "新显示名"})
    assert updated["name"] == "新显示名"
    assert updated["version"] == 1, "改名不得 bump version（这正是缺陷前提）"

    p2 = build_director_input(db_path, pid, cid, "意图")
    assert p2["skills"]["items"][0]["name"] == "新显示名", (
        "改名后装配仍拿到旧名 ⇒ skills 指纹漏了 name 维度（脏命中）"
    )
    keys_after = {k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"}
    assert len(keys_after) == len(keys_before) + 1, (
        "改名必须产生一个新缓存键（miss）；键集不增长说明命中了旧装配"
    )

    # 反向不变量：未改再装 ⇒ 命中（键集不再增长，不过度失效）。
    p3 = build_director_input(db_path, pid, cid, "意图")
    assert p3["skills"]["items"][0]["name"] == "新显示名"
    keys_final = {k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"}
    assert len(keys_final) == len(keys_after), "输入未变却新增缓存键 ⇒ 过度失效"


# ---------------------------------------------------------------------------
# C-29：自定义 name 的推演在列表与重放双出口可见
# ---------------------------------------------------------------------------


def test_custom_named_simulation_is_listed_and_replayable(tmp_path: Path):
    """``simulate(name="自定义")`` ⇒ 列表可见 + ``get_simulation`` 可重放。

    schema 证据：本仓没有 ``simulations`` 表（推演 = ``branches`` 行），归属列
    ``branches.project_id`` 是 ``NOT NULL``；但 project_id 不能区分推演与普通分支
    ⇒ 用推演的**独占状态标记** ``ARCHIVED``（只有 ``_archive_branch`` 写它）判别。
    """
    db_path = migrated_db(tmp_path)
    seed = _seed_project(db_path)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_ids"][0]
    state = StoryStateService(db_path)
    sim = SimulationService(db_path)

    main_version = int(state.get_current_state(pid)["state_version"])
    delta = _low_state_delta("dlt_b36_sim", chap, cid, main_version)
    result = sim.simulate(pid, [delta], name="自定义推演-甲")

    listed = sim.list_simulations(pid)
    names = {row["name"] for row in listed}
    assert "自定义推演-甲" in names, (
        f"自定义 name 的推演必须出现在列表；实际 {sorted(names)}"
    )

    replay = sim.get_simulation(pid, result.simulation_id)
    assert replay is not None, "自定义 name 的推演必须可重放（否则列表能看、点进去 404）"
    assert replay.name == "自定义推演-甲"
    assert replay.applied == 1

    # 反向不变量：普通特性分支（ACTIVE）不得被误当推演。
    feature = state.create_branch(pid, "feature-normal")
    listed_after = sim.list_simulations(pid)
    assert feature["branch_id"] not in {row["branch_id"] for row in listed_after}
    assert sim.get_simulation(pid, feature["branch_id"]) is None


# ---------------------------------------------------------------------------
# C-30：delivery evidence 单连接复用
# ---------------------------------------------------------------------------


def test_length_evidence_reuses_one_connection(tmp_path: Path, monkeypatch):
    """逐章读草稿复用同一条连接：evidence 建连 1 次，draft_resolver 建连 0 次。

    判别力：撤掉单连接改造（``resolve_draft(db_path, chapter_id)``）⇒
    ``draft_resolver.get_connection`` 会被调用 N 次 ⇒ 计数断言必红。
    """
    db_path = migrated_db(tmp_path)
    seed = _seed_project(db_path, chapters=2)
    pid = seed["project_id"]
    chapter_ids = seed["chapter_ids"]

    prose = "字" * 120
    conn = get_connection(db_path)
    try:
        for idx, chapter_id in enumerate(chapter_ids, start=1):
            conn.execute(
                "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
                "prompt_version, model_id, created_at) VALUES (?, ?, ?, ?, 'writer', NULL, NULL, ?)",
                (f"dr_b36_{idx}", chapter_id, 1, prose, _now_iso()),
            )
        conn.commit()
    finally:
        conn.close()

    real_get_connection = get_connection
    calls = {"evidence": 0, "draft_resolver": 0}

    def _count_evidence(path):
        calls["evidence"] += 1
        return real_get_connection(path)

    def _count_draft_resolver(path):
        calls["draft_resolver"] += 1
        return real_get_connection(path)

    monkeypatch.setattr(evidence_mod, "get_connection", _count_evidence)
    monkeypatch.setattr(draft_resolver_mod, "get_connection", _count_draft_resolver)

    chapters = [
        {"chapter_id": chapter_id, "plan_json": {"expected_word_count": 120}}
        for chapter_id in chapter_ids
    ]
    evidence, versions, unavailable, error = _length_evidence_map(
        str(db_path), pid, chapters, {}
    )

    assert error is None
    assert unavailable == {}
    assert set(evidence) == set(chapter_ids)
    expected = classify_prose_length(prose, 120)
    for chapter_id in chapter_ids:
        assert evidence[chapter_id].visible_chars == int(expected["visible_chars"])
        assert evidence[chapter_id].target_word_count == int(expected["target"])
        assert versions[chapter_id] == 1

    assert calls["evidence"] == 1, f"evidence 应只建连 1 次，实得 {calls['evidence']}"
    assert calls["draft_resolver"] == 0, (
        f"传了 conn 后 resolve_draft 不得自己建连，实得 {calls['draft_resolver']} 次"
    )


def test_resolve_draft_with_caller_conn_does_not_close_it(tmp_path: Path):
    """``resolve_draft(conn=...)`` 不持有连接生命周期（调用方连接仍可用）。"""
    db_path = migrated_db(tmp_path)
    seed = _seed_project(db_path)
    chapter_id = seed["chapter_ids"][0]
    conn = get_connection(db_path)
    try:
        assert draft_resolver_mod.resolve_draft(str(db_path), chapter_id, conn=conn) is None
        # 连接未被关闭：还能继续查询。
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
