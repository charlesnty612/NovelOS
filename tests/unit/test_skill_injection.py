"""skill 的装配注入 + 缓存键指纹（内部消费外部 skill）。

钉住的不变量（每条都对应一条设计约束）：

1. **独立新段**：``payload["skills"]`` 存在；**不**塞进 ``genre_pack`` /
   ``style_constraints`` 等既有段。
2. **缺席时零注入**：未绑定 / 无适用 skill ⇒ payload **不出现** ``skills`` 键
   （既有装配行为逐字段不变）。
3. **``applies_to`` 过滤**：writer 专用 skill 不进 director 装配，反之亦然。
4. **字符预算 + 截断标记**：段 JSON 长度 ≤ 2000；超限 ⇒ ``__skills_truncated__``。
5. **缓存键含 version**：改 skill payload（version 自增）⇒ 同 state_version 重跑
   **必须重装**（硬规则 2「凡进 payload 的装配参数必须入键」）。
6. **未绑定 ⇒ 指纹恒为 ``"__none__"``**（与 ``genre_pack_ref`` 同形）。
7. **检查项只进 ``review_report.warnings[]``**：不进 ``errors``、不进
   ``ai_pattern_hits``、不进 ``quality_reports``、不进任何门禁（R4）。

零真实 LLM、零内容仓接触、零 dev 库（临时库 + 合成行）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.cache import _fingerprint_skills
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

from .skill_fixtures import (
    bind_skills,
    fresh_db,
    insert_chapter,
    insert_project,
    make_skill,
    valid_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

#: director 装配缓存键的**长度**（维度数）。演进沿革：
#: 12 → 13（prompt_label）→ 14（chars_fp）→ … → 18（volume_scope 所属卷）
#: → **23**（2026-10-23 批次：director 侧补 5 个维度——伏笔台账 / 叙事债务 /
#: 情节图+分支 / 项目逾期阈值 / 章标题）→ **24**（2026-10-24 R-2：新增 prev_draft_fp
#: 前章草稿内容维度）。
#: ⚠️ 本仓**三处各有一份**这个常量（另两处：`tests/unit/test_prompt_label_key.py`、
#: `tests/unit/test_volume_scope_cache_key.py`）——加维度必须三处同批更新，
#: 否则「键长断言」会以**过期数字**的形式红/绿（见 AGENTS 硬规则 2 的装配缓存键纪律）。
DIRECTOR_KEY_LEN = 24


def _chapter_with_draft(db_path: Path, pid: str, prose: str = "正文。") -> str:
    """插章节 + 一条 draft（评审侧测试需要正文）。"""
    cid = insert_chapter(db_path, pid, 1)
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'test:writer:v1', 'writer:v1', NULL, ?)",
            (new_id("dr"), cid, prose, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1~3：注入形态 / 缺席语义 / applies_to 过滤
# ---------------------------------------------------------------------------


def test_director_payload_carries_independent_skills_segment(tmp_path):
    """director 装配带 ``skills`` 段，且**不**污染既有段。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_d", name="悬念铺垫",
        instructions="章末留一个未解的问题。",
        applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_d"])
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    assert "skills" in payload, "director 装配必须带 skills 段"
    items = payload["skills"]["items"]
    assert len(items) == 1
    assert items[0]["skill_id"] == "skl_d"
    assert items[0]["version"] == 1
    assert "章末留一个未解的问题。" in items[0]["instructions"]
    # 独立新段：不得塞进题材包 / 文风约束
    assert "skills" not in (payload.get("genre_pack") or {})
    assert "skills" not in (payload.get("style_constraints") or {})
    # 溯源审计
    assert payload["_skills_consumed"] == [
        {"skill_id": "skl_d", "version": 1, "consumed_fields": ["instructions"]},
    ]


def test_no_skills_means_no_key_at_all(tmp_path):
    """未绑定 ⇒ payload **不出现** ``skills`` / ``_skills_consumed``（零行为变化）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    assert "skills" not in payload
    assert "_skills_consumed" not in payload


def test_applies_to_filters_per_consumer(tmp_path):
    """``applies_to`` 过滤：writer 专用 skill 不进 director 装配，反之亦然。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_w", name="给 writer",
        instructions="只给 writer 的手艺。", applies_to=["writer"],
    )
    make_skill(
        db_path, skill_id="skl_d", name="给 director",
        instructions="只给 director 的手艺。", applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_w", "skl_d"])
    _cache_reset()

    director = build_director_input(db_path, pid, cid, "意图")
    assert [i["skill_id"] for i in director["skills"]["items"]] == ["skl_d"]

    writer = build_writer_input(db_path, cid, {})
    assert [i["skill_id"] for i in writer["skills"]["items"]] == ["skl_w"]


def test_applies_to_absent_applies_to_every_consumer(tmp_path):
    """``applies_to`` 缺席 ⇒ **全部环节适用**（避免「校验通过但永不生效」的死资源）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_any", name="通用手艺",
        instructions="不分环节的手艺。",
    )
    bind_skills(db_path, pid, ["skl_any"])
    _cache_reset()

    director = build_director_input(db_path, pid, cid, "意图")
    writer = build_writer_input(db_path, cid, {})
    assert [i["skill_id"] for i in director["skills"]["items"]] == ["skl_any"]
    assert [i["skill_id"] for i in writer["skills"]["items"]] == ["skl_any"]


# ---------------------------------------------------------------------------
# 4：字符预算与截断标记
# ---------------------------------------------------------------------------


def test_oversized_skill_is_truncated_with_marker(tmp_path):
    """超预算 ⇒ 截断 + ``__skills_truncated__`` 标记；段长度仍 ≤ 2000 字符。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_big", name="大手艺",
        instructions="很长的手艺说明。" * 500,
        applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_big"])
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    segment = payload["skills"]
    assert segment.get("__skills_truncated__") is True, "超预算必须打截断标记"
    assert len(json.dumps(segment, ensure_ascii=False)) <= 2000
    assert "…（已截断）" in segment["items"][0]["instructions"]


def test_references_and_checks_are_injected(tmp_path):
    """references / checks 进注入块（references **不是**渐进披露：模型无 tool use）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_r", name="带参考",
        instructions="手艺本体。",
        applies_to=["director"],
        references=[{"path": "skills/x.md", "title": "示例", "body": "示例正文"}],
        checks=[{"check_id": "c1", "description": "查这个", "keywords": ["其实"]}],
    )
    bind_skills(db_path, pid, ["skl_r"])
    _cache_reset()

    block = build_director_input(db_path, pid, cid, "意图")["skills"]["items"][0]
    assert block["references"] == [
        {"path": "skills/x.md", "title": "示例", "body": "示例正文"},
    ]
    assert block["checks"] == [
        {"check_id": "c1", "description": "查这个", "keywords": ["其实"]},
    ]


# ---------------------------------------------------------------------------
# 5~6：缓存键
# ---------------------------------------------------------------------------


def test_fingerprint_skills_is_none_marker_when_unbound():
    """未绑定 ⇒ ``"__none__"``（与 ``genre_pack_ref`` 同形）。"""
    assert _fingerprint_skills(None) == "__none__"
    assert _fingerprint_skills("") == "__none__"


def test_director_cache_key_has_skills_dimension(tmp_path):
    """director 键含 skill 维度：绑定前后**只差一个位置**（键元数不变）。

    判别方式用「键值差异」而不是「键里有没有某个串」——键里本来就有好几个
    ``"__none__"`` 占位（题材包 / canon / 字数带……），按位置比对才抗噪。
    """
    from packages.core.context_engine import cache as cache_mod

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    _cache_reset()

    build_director_input(db_path, pid, cid, "意图")
    before = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"]
    assert len(before) == 1

    make_skill(
        db_path, skill_id="skl_k", name="手艺",
        instructions="手艺。", applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_k"])
    _cache_reset()

    build_director_input(db_path, pid, cid, "意图")
    after = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"]
    assert len(after) == 1
    b, a = before[0], after[0]
    # 键长度常量随维度演进：12 → 13（prompt_label）→ 14（chars_fp）→ … → 18（volume_scope）
    # → **23**（2026-10-23 批次：director 侧补 5 个维度：伏笔台账 / 叙事债务 /
    # 情节图+分支 / 项目逾期阈值 / 章标题）。
    # ⚠️ 改这个数字时**必须**同步 `tests/unit/test_prompt_label_key.py` 与
    # `tests/unit/test_volume_scope_cache_key.py` 里的 `DIRECTOR_KEY_LEN`
    # （三处各有一份，是「手写清单漂移」的复发点）。
    assert len(b) == len(a) == DIRECTOR_KEY_LEN, (
        f"director 键应为 {DIRECTOR_KEY_LEN} 元，实得 {len(b)} / {len(a)}"
    )
    diff_positions = [i for i, (x, y) in enumerate(zip(b, a)) if x != y]
    assert len(diff_positions) == 1, (
        f"绑定 skill 后键应只变一个维度，实得 {diff_positions}：{b} → {a}"
    )
    idx = diff_positions[0]
    assert b[idx] == "__none__"
    assert len(a[idx]) == 16


def test_fingerprint_skills_contains_version():
    """指纹**含 version**：只按 id 算 ⇒ 改 payload 后脏命中。"""
    v1 = _fingerprint_skills("skl_a:1")
    v2 = _fingerprint_skills("skl_a:2")
    assert v1 != v2, "version 变更必须改指纹"
    assert len(v1) == 16
    # 顺序无关（按 skill_id 排序规范化）：主 JOIN 与降级路径聚合顺序不同也同值
    assert _fingerprint_skills("skl_a:1|skl_b:2") == _fingerprint_skills("skl_b:2|skl_a:1")


def test_changing_skill_payload_misses_director_cache(tmp_path):
    """改 skill payload（version 自增）⇒ 同 state_version 重跑 **必须重装**。

    判别点选**直接投影**（``skills.items[0].version``）——不选 excerpt 形态，
    免得被裁剪搅浑（同 ``plan_fp`` 纪律）。
    """
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_c", name="手艺",
        instructions="第一版手艺。", applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_c"])
    _cache_reset()

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert p1["skills"]["items"][0]["version"] == 1

    # 改 payload ⇒ version 自增（不 bump state_version！）
    from packages.core.skill import SkillService
    SkillService(db_path).update("skl_c", {
        "payload": valid_payload(
            name="手艺", instructions="第二版手艺。", applies_to=["director"],
        ),
    })
    _cache_reset()

    p2 = build_director_input(db_path, pid, cid, "意图")
    assert p2["skills"]["items"][0]["version"] == 2, "改 payload 后必须重装"
    assert "第二版手艺。" in p2["skills"]["items"][0]["instructions"]


def test_unbinding_skill_misses_director_cache(tmp_path):
    """解绑 skill ⇒ 同 state_version 重跑必须重装（``skills`` 段整体消失）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_u", name="手艺",
        instructions="手艺。", applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_u"])
    _cache_reset()

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert "skills" in p1

    from packages.core.skill import SkillService
    SkillService(db_path).unbind(pid, "skl_u")
    _cache_reset()

    p2 = build_director_input(db_path, pid, cid, "意图")
    assert "skills" not in p2, "解绑后必须重装（否则命中带旧 skills 的装配）"


def test_writer_cache_key_also_has_skills_dimension(tmp_path):
    """writer 键同样含 skill 维度（与 director 键逐字同形）。

    判别方式：**绑定前后两条键必须不同，且只差一个位置**——比「键里有没有某个
    16 位串」更抗噪（键里本来就有好几个 ``"__none__"`` 占位：题材包 / canon /
    字数带……）。
    """
    from packages.core.context_engine import cache as cache_mod

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    _cache_reset()

    build_writer_input(db_path, cid, {})
    keys_before = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer"]
    assert len(keys_before) == 1, f"应有 1 条 writer 键，实得 {len(keys_before)}"

    make_skill(
        db_path, skill_id="skl_wk", name="手艺",
        instructions="手艺。", applies_to=["writer"],
    )
    bind_skills(db_path, pid, ["skl_wk"])
    _cache_reset()

    build_writer_input(db_path, cid, {})
    keys_after = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer"]
    assert len(keys_after) == 1
    before, after = keys_before[0], keys_after[0]
    assert len(before) == len(after) == 24, (
        f"writer 键应为 24 元（23 + prev_draft_fp 前章草稿维度），实得 {len(before)} / {len(after)}"
    )
    diff_positions = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diff_positions) == 1, (
        f"绑定 skill 后键应只变一个维度，实得 {diff_positions}：{before} → {after}"
    )
    idx = diff_positions[0]
    assert before[idx] == "__none__", f"未绑定占位应逐字为 __none__，实得 {before[idx]!r}"
    assert len(after[idx]) == 16, f"绑定后该维度应是 16 位指纹，实得 {after[idx]!r}"


# ---------------------------------------------------------------------------
# 7：检查项只进 warnings 旁路
# ---------------------------------------------------------------------------


def test_skill_checks_land_in_warnings_only(tmp_path):
    """检查项命中 ⇒ **恒 warning**：进 ``warnings``，不进 ``errors`` / ``ai_pattern_hits``。"""
    from packages.workflows.chapter_review.pipeline import _basic_checks_node

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    prose = "他其实有点难过。"
    cid = _chapter_with_draft(db_path, pid, prose)
    make_skill(
        db_path, skill_id="skl_chk", name="带检查项",
        instructions="手艺。",
        applies_to=["critic"],
        checks=[{"check_id": "exposed", "description": "直白情绪词", "keywords": ["其实"]}],
    )
    bind_skills(db_path, pid, ["skl_chk"])

    report = _basic_checks_node({
        "db_path": str(db_path),
        "chapter_id": cid,
        "target_word_count": len(prose),
    })["review_report"]

    assert any("[SKILL-exposed]" in w for w in report["warnings"]), (
        f"skill 检查命中必须进 warnings，实际 warnings={report['warnings']}"
    )
    assert not [e for e in report["errors"] if str(e.get("rule_id", "")).startswith("SKILL-")], (
        "skill 检查命中**绝不允许**进 errors（errors 是门禁与自动改稿的原料）"
    )
    assert not [
        h for h in report.get("ai_pattern_hits") or []
        if str(h.get("rule_id", "")).startswith("SKILL-")
    ], "skill 检查**绝不**接入 scan_ai_patterns（会自动进 quality_reports 与门禁）"


def test_skill_check_rule_id_is_not_registered_in_any_consequence_table():
    """``SKILL-*`` 命名空间**不在**任何后果表内 ⇒ 表外 rule_id 一律硬停交人工。

    四张表 = 阻断（``BLOCKING_RULES``）/ 确认（``CONFIRM_RULES``）/
    可改稿（``REVISE_RULE_IDS``）/ 可修订（``REVISABLE_RULE_IDS``）。
    """
    from packages.core.api.routers.workflows.repair_policy import REVISE_RULE_IDS
    from packages.core.quality.issues import BLOCKING_RULES, CONFIRM_RULES
    from scripts.produce_chapters import REVISABLE_RULE_IDS

    tables = {
        "BLOCKING_RULES": set(BLOCKING_RULES),
        "CONFIRM_RULES": set(CONFIRM_RULES),
        "REVISE_RULE_IDS": set(REVISE_RULE_IDS),
        "REVISABLE_RULE_IDS": set(REVISABLE_RULE_IDS),
    }
    for name, ids in tables.items():
        offenders = sorted(rid for rid in ids if rid.startswith("SKILL-"))
        assert not offenders, f"{name} 不得含 SKILL-* 规则：{offenders}"

    # 反向：skill 产出的 rule_id 不得与任何一张表里的 id 撞名。
    # ⚠️ canary 用 ``REPEAT``：``"AI-BEAT-" + "REPEAT"`` 正是 CONFIRM_RULES 里的
    # ``AI-BEAT-REPEAT`` ⇒ 一旦命名空间前缀被改成 "AI-BEAT-"，本断言立刻红
    # （这是「skill 检查绝不能进后果表」的可判别形态）。
    from packages.core.skill import skill_rule_id

    assert skill_rule_id("REPEAT") == "SKILL-REPEAT"
    for name, ids in tables.items():
        assert skill_rule_id("REPEAT") not in ids, (
            f"skill 的 rule_id 撞上了 {name} ⇒ 检查项会进自动改稿 / 门禁"
        )


def test_unknown_skill_rule_id_in_errors_leads_to_stop(tmp_path):
    """表外 rule_id 进 ``errors[]`` ⇒ ``decide_repair`` 必须 ``stop``（不自动改稿）。

    这是 R3 的**行为后果**：skill 检查即使将来被误接线进 errors，也只会
    「不认识就不猜、交人工」，不会触发自动改稿回路。
    """
    from packages.core.api.routers.workflows.repair_policy import decide_repair

    decision = decide_repair(
        {"errors": [{"rule_id": "SKILL-exposed", "message": "x"}]},
        remaining_rounds=2,
    )
    assert decision.action == "stop", f"表外 rule_id 必须硬停，实得 {decision.action}"
    assert decision.reason == "unknown_rule_id"


# ---------------------------------------------------------------------------
# 迁移与降级路径
# ---------------------------------------------------------------------------


def test_peek_degraded_path_produces_same_fingerprint_shape(tmp_path):
    """降级路径（主 JOIN 失败）与主路径的**键形态逐字同形**（硬规则 2）。

    做法：把主 JOIN 打断（本章不存在 ⇒ 走空态），再比「无绑定」时的指纹；
    两条路径都必须给 ``"__none__"``。
    """
    from packages.core.context_engine.builders_common.common import _peek_chapter_context

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    cid = insert_chapter(db_path, pid, 1)
    make_skill(
        db_path, skill_id="skl_p", name="手艺",
        instructions="手艺。", applies_to=["director"],
    )
    bind_skills(db_path, pid, ["skl_p"])

    peek = _peek_chapter_context(db_path, cid, project_id=pid)
    assert peek["found"] is True
    assert peek["skills_ref"], "绑定 skill 时 peek 必须带 skills_ref"
    assert _fingerprint_skills(peek["skills_ref"]) != "__none__"

    # 章节不存在 ⇒ 空态 ⇒ 未绑定占位
    empty = _peek_chapter_context(db_path, "ch_ghost", project_id=pid)
    assert _fingerprint_skills(empty["skills_ref"]) == "__none__"


def test_skill_excerpt_degrades_when_table_missing(tmp_path):
    """0032 未跑的极老库（无 skills 表）⇒ 视为未绑定，**不抛错**。"""
    from packages.core.context_engine.builders_common.common import _skill_excerpt

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE project_skills")
        conn.execute("DROP TABLE skills")
        conn.commit()
        assert _skill_excerpt(conn, pid) == (None, None)
    finally:
        conn.close()


def test_skill_excerpt_propagates_non_schema_operational_error(tmp_path):
    """锁 / I/O 类故障**必须上抛**，不得静默吞成「未绑定」。

    静默吞锁 ⇒ ``skills_fp`` 归 ``"__none__"`` ⇒ 缓存键的 skill 维度失真 ⇒
    换 skill 后同 state_version 仍脏命中（硬规则 2）。
    """
    import sqlite3

    from packages.core.context_engine.builders_common.common import _skill_excerpt

    class _BoomConn:
        """只会抛「database is locked」的假连接。"""

        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    try:
        _skill_excerpt(_BoomConn(), "prj_x")
    except sqlite3.OperationalError as exc:
        assert "locked" in str(exc)
    else:
        raise AssertionError("非缺表/缺列的 OperationalError 必须上抛，不得被吞成未绑定")


def test_migration_adds_two_business_tables(tmp_path):
    """0032 加两张业务表（业务表 39 → 41；0033 再加 1 张 → 42）。"""
    from packages.core.db import count_tables

    db_path = tmp_path / "m.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    # count_tables 含 _migrations ⇒ 42
    assert count_tables(db_path) == 43


def test_review_side_skill_read_propagates_lock_error(tmp_path, monkeypatch):
    """评审侧读 skill 遇「锁故障」必须**上抛**，不得静默降级为「未绑定」。

    静默吞锁 ⇒ 作者以为手艺生效、评审却没带，且零告警（本仓反复登记的降级形状）。
    """
    import sqlite3

    import pytest

    from packages.workflows.chapter_review import pipeline as review_pipeline

    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        review_pipeline.SkillService, "list_project_skills", staticmethod(_boom),
    )
    with pytest.raises(sqlite3.OperationalError):
        review_pipeline._collect_skill_review_data(str(db_path), pid)
