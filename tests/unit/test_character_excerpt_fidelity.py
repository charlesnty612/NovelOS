"""角色摘录保真度（``builders_common/excerpts.py``）——2026-10-16 角色设定丢失批次。

起因（外部用户报告）：「角色圣经里面角色间的关系是闺蜜，结果在正文里，那个角色却以
业务员出场；另外，此角色是用户后面手动添加的角色」。本文件只钉**摘录层**的事实：

- **期望值来源纪律**：一律取自**契约**——``characters`` 表的 PRAGMA 列清单、迁移 0010 的
  ``inject_mode`` CHECK 枚举、``CharacterTab.tsx::CORE_FIELDS`` 的现行 ``core_json``
  键集、writer 提示词 ``docs/agents/prompts/writer-v3.md`` 的 excerpt 形状声明。
  **不得**由「跑一下看返回什么」得出（AGENTS 坑区「夹具从被测代码的实际输出里抄形状」）。
- **判别面按全集推导**：``inject_mode`` 的断言对 ``_INJECT_MODES`` **全集**参数化，
  字段完整性对 ``PRAGMA table_info(characters)`` **全集**推导，不另列手挑名单。
- ⚠️ **所有依赖 relevance 行为的用例必须显式传 ``relevance_trim=``**，不得依赖环境变量
  默认值：``tests/unit/test_writer_input_paged.py`` 在**模块导入时**写
  ``os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "off"``，同进程内会被本文件读到
  （``-n 4`` 分组不同 ⇒ 本文件单跑绿、全量红；已踩过一次，见
  ``test_snapshot_paged_mode_keeps_protagonist_drops_uninvolved_character``）。
- 本文件不含任何真实 LLM 调用，不读 ``data/novelos.db``（全用 tmp_path 合成夹具）。

⚠️ 文件末尾 ``test_*_snapshot_*`` 一组是**行为现状快照**：它们钉住「角色设定在什么条件下
会消失」这一事实本身。若将来 Lead 裁决改注入策略，这几条会红——**那时应该改它们，
不是绕过它们**，且 CHANGELOG 必须同时记下改的是哪条策略。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.context_engine.builders import (  # noqa: E402
    _cache_reset,
    build_writer_input,
)
from packages.core.context_engine.builders_common import (  # noqa: E402
    _character_state_excerpts,
    _world_state_excerpts,
)
from packages.core.context_engine.builders_common.excerpts import (  # noqa: E402
    _INJECT_MODES,
    _JSON_DEGRADED_WARN_TAG,
    _build_trigger_corpus,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"

# 现行 core_json 写入契约的键集（前端 CharacterTab::CORE_FIELDS + project_init
# _normalize_characters）——**不含** personality/values/fears/desires/flaws（早期方案）。
CANONICAL_CORE = {
    "motivation": "替父翻案",
    "goal": "查清父亲死因",
    "conflict": "与兄长的利益冲突",
    "distinctive_trait": "外冷内热，护短",
    "relationships": [{"to": "苏婉清", "relation_type": "bestie", "note": "闺蜜"}],
}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "e3_char.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id,name,premise,genre,target_words,status,"
            "created_at,updated_at) VALUES (?,?,NULL,NULL,NULL,'ACTIVE',?,?)",
            (pid, "合成项目", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, *, number: int = 1,
                    plan: dict | None = None) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id,project_id,number,title,plan_json,status,"
            "visibility,who_knows,created_at,updated_at) VALUES (?,?,?,?,?,'PLANNED',"
            "'VISIBLE',NULL,?,?)",
            (cid, project_id, number, "第一章",
             json.dumps(plan or {}, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(
    db_path: Path, project_id: str, char_id: str, name: str, *,
    role: str = "supporting",
    core_json: str | None = None,
    state_json: str | None = "{}",
    inject_mode: str = "auto",
    aliases: str = "[]",
    who_knows: str | None = None,
) -> str:
    """插一行 characters（可选插 state v1）。

    ``state_json=None`` ⇒ **不插** state 行（手工 SQL / 导入路径的形态），
    对应 ``LEFT JOIN`` 落空 ⇒ ``latest_state_version IS NULL``。
    """
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO characters (character_id,project_id,name,role,core_json,visibility,"
            "who_knows,created_at,updated_at,aliases,inject_mode) "
            "VALUES (?,?,?,?,?,'PUBLIC',?,?,?,?,?)",
            (char_id, project_id, name, role,
             core_json if core_json is not None else json.dumps(CANONICAL_CORE, ensure_ascii=False),
             who_knows, now, now, aliases, inject_mode),
        )
        if state_json is not None:
            conn.execute(
                "INSERT INTO character_states (character_id,state_version,state_json,"
                "visibility,created_at) VALUES (?,1,?,'VISIBLE',?)",
                (char_id, state_json, now),
            )
        conn.commit()
    finally:
        conn.close()
    return char_id


def _insert_commit(db_path: Path, project_id: str, chapter_id: str,
                   payload: dict, version: int = 1) -> None:
    now = now_iso()
    delta_id, commit_id, branch_id = new_id("dlt"), new_id("cmt"), new_id("brn")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO branches (branch_id,project_id,name,parent_branch_id,"
            "base_state_version,status,created_at) VALUES (?,?,'main',NULL,0,'ACTIVE',?)",
            (branch_id, project_id, now),
        )
        conn.execute(
            "INSERT INTO state_deltas (delta_id,chapter_id,workflow_run_id,"
            "previous_state_version,delta_version,schema_version,payload_json,status,"
            "supersedes,created_by,created_at) "
            "VALUES (?,?,'wfr_e3',?,1,'state-delta-v0',?,'applied',NULL,'observer:v1',?)",
            (delta_id, chapter_id, version - 1,
             json.dumps(payload, ensure_ascii=False), now),
        )
        conn.execute(
            "INSERT INTO commits (commit_id,project_id,branch_id,chapter_id,"
            "previous_state_version,resulting_state_version,delta_id,validation_json,"
            "author_approval_json,timestamp,workflow_run_id,rollback_of) "
            "VALUES (?,?,?,?,?,?,?,'{}','{}',?,'wfr_e3',NULL)",
            (commit_id, project_id, branch_id, chapter_id, version - 1, version,
             delta_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def _by_name(excerpts: list[dict], name: str) -> dict:
    hit = [e for e in excerpts if e.get("name") == name]
    assert len(hit) == 1, f"期望恰好 1 条 {name!r}，实得 {len(hit)}"
    return hit[0]


# ---------------------------------------------------------------------------
# 1) core_traits_summary：读现行契约键，不是已退役的 personality
# ---------------------------------------------------------------------------


def test_core_traits_summary_reads_canonical_distinctive_trait(tmp_path: Path):
    """core_json 用现行键时，core_traits_summary 必须非空。

    期望值来自**契约**（CharacterTab::CORE_FIELDS 把 distinctive_trait 标注为「特质」），
    不是「跑一下看返回什么」。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "林渊")

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "林渊")
    finally:
        conn.close()

    assert exc["core_traits_summary"] == CANONICAL_CORE["distinctive_trait"]


def test_core_traits_summary_keeps_legacy_personality_fallback(tmp_path: Path):
    """历史行（只有 personality 键）仍能出摘要——不得因改键而退化。

    反向配对：只钉「读现行键」的话，一个「一律读 personality」的实现也能全绿。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "旧角色",
                      core_json=json.dumps({"personality": "隐忍"}, ensure_ascii=False))

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "旧角色")
    finally:
        conn.close()

    assert exc["core_traits_summary"] == "隐忍"


def test_core_traits_summary_absent_keys_give_empty_string(tmp_path: Path):
    """两个键都没有 ⇒ 空串（不得编造、不得把 dict/list 强转成 repr 进 prompt）。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "无名",
                      core_json=json.dumps({"goal": "查案"}, ensure_ascii=False))
    _insert_character(db, pid, "char_b", "怪形状",
                      core_json=json.dumps({"distinctive_trait": ["甲", "乙"]},
                                            ensure_ascii=False))

    conn = get_connection(db)
    try:
        out = {e["name"]: e for e in _character_state_excerpts(conn, pid)}
    finally:
        conn.close()

    assert out["无名"]["core_traits_summary"] == ""
    assert out["怪形状"]["core_traits_summary"] == ""


# ---------------------------------------------------------------------------
# 2) inject_mode 必须进 excerpt —— relevance 的 always 分支否则是死代码
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(_INJECT_MODES))
def test_excerpt_carries_inject_mode_for_every_db_value(tmp_path: Path, mode: str):
    """判别面 = ``_INJECT_MODES`` **全集**（0010 CHECK 枚举），非手挑名单。

    期望值直接取自 DB 列原文 ⇒ 「删掉 inject_mode 这一行」必红。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "角色甲", inject_mode=mode)

    conn = get_connection(db)
    try:
        db_value = conn.execute(
            "SELECT inject_mode FROM characters WHERE character_id='char_a'"
        ).fetchone()["inject_mode"]
        exc = _by_name(_character_state_excerpts(conn, pid), "角色甲")
    finally:
        conn.close()

    assert db_value == mode  # 夹具自检：量具在该形态上动得了
    assert exc["inject_mode"] == db_value


def test_location_and_faction_excerpts_carry_inject_mode(tmp_path: Path):
    """locations / factions 侧同因同修：``relevance`` 的 always 分支对三者都读这个键。

    判别面从 ``_INJECT_MODES`` 全集参数化 ⇒ 少一处（location / faction）必红。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    now = now_iso()
    conn = get_connection(db)
    try:
        for i, mode in enumerate(_INJECT_MODES):
            conn.execute(
                "INSERT INTO locations (location_id,project_id,name,statement,data_json,"
                "visibility,created_at,updated_at,aliases,inject_mode) "
                "VALUES (?,?,?,'陈述','{}','PUBLIC',?,?,'[]',?)",
                (f"loc_{i}", pid, f"地点{i}", now, now, mode),
            )
            conn.execute(
                "INSERT INTO factions (faction_id,project_id,name,statement,data_json,"
                "visibility,created_at,updated_at,aliases,inject_mode) "
                "VALUES (?,?,?,'陈述','{}','PUBLIC',?,?,'[]',?)",
                (f"fac_{i}", pid, f"势力{i}", now, now, mode),
            )
        conn.commit()
        world = _world_state_excerpts(conn, pid)
    finally:
        conn.close()

    locs = {x["location_id"]: x for x in world["locations"]}
    facs = {x["faction_id"]: x for x in world["active_factions"]}
    for i, mode in enumerate(_INJECT_MODES):
        assert locs[f"loc_{i}"]["inject_mode"] == mode, "location 丢了 inject_mode"
        assert facs[f"fac_{i}"]["inject_mode"] == mode, "faction 丢了 inject_mode"


def test_relevance_trim_spares_always_injected_location_end_to_end(tmp_path: Path):
    """端到端（地点侧）：``inject_mode='always'`` 的 location 完整保留 statement / data_json。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan={"chapter_goal": "完全无关的剧情"})
    now = now_iso()
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO locations (location_id,project_id,name,statement,data_json,"
            "visibility,created_at,updated_at,aliases,inject_mode) "
            "VALUES ('loc_always',?,'常驻地','关键据点','{\"floor\":3}','PUBLIC',?,?,'[]','always')",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, {"purpose": "无关描述"},
                                 relevance_trim=True)
    loc = next(x for x in payload["world_state_excerpts"]["locations"]
               if x.get("location_id") == "loc_always")

    assert loc.get("relevance_summary") is None, "always 地点被 relevance 降级了"
    assert loc["statement"] == "关键据点"
    assert loc["data_json"] == {"floor": 3}


def test_excerpt_inject_mode_normalizes_illegal_value_to_auto(tmp_path: Path):
    """非法列值（CHECK 在旧库 / 导入下可能被绕过）⇒ 归一为 auto，不外泄原始脏值。

    口径与 ``_load_inject_mode`` 一致（0010 CHECK 的兜底）。PRAGMA 是**连接级**的，
    故插入与断言必须在**同一条连接**上完成（另开连接会丢掉该 PRAGMA）。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    now = now_iso()
    conn = get_connection(db)
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO characters (character_id,project_id,name,role,core_json,visibility,"
            "who_knows,created_at,updated_at,aliases,inject_mode) "
            "VALUES (?,?,?,?,?,'PUBLIC',NULL,?,?,'[]',?)",
            ("char_a", pid, "脏模式", "supporting",
             json.dumps(CANONICAL_CORE, ensure_ascii=False), now, now, "AUTO"),
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
        raw = conn.execute(
            "SELECT inject_mode FROM characters WHERE character_id='char_a'"
        ).fetchone()["inject_mode"]
        exc = _by_name(_character_state_excerpts(conn, pid), "脏模式")
    finally:
        conn.close()

    assert raw == "AUTO", "夹具自检：CHECK 应已被绕过，DB 里确实是脏值"
    assert exc["inject_mode"] == "auto"


def test_relevance_trim_spares_always_injected_character_end_to_end(tmp_path: Path):
    """端到端：``inject_mode='always'`` 的角色必须**完整保留**过 relevance 裁剪。

    这是本文件最核心的一条——``relevance._is_character_relevant`` 的 docstring 明写
    「``inject_mode='always'`` 的实体始终完整保留」，撤掉 excerpt 里的 inject_mode
    这一行，它恒假、本用例如期变红。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan={"chapter_goal": "完全无关的剧情"})
    _insert_character(db, pid, "char_always", "常驻角色", inject_mode="always")
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, {"purpose": "无关描述"}, relevance_trim=True)
    always_exc = _by_name(payload["character_state_excerpts"], "常驻角色")

    assert always_exc.get("relevance_summary") is None, "always 角色被 relevance 降级了"
    assert "core_json" in always_exc, "always 角色丢了角色圣经"
    assert payload["_relevance_trim_stats"]["characters_full"] >= 1


def test_relevance_trim_still_degrades_auto_character_not_involved(tmp_path: Path):
    """反向配对：``auto`` 且未出现在本章 involved 里 ⇒ **仍要**降级。

    没有这一条，一个「一律 full 注入」的实现照样能让上一条全绿
    （AGENTS：只钉撤掉修复的话，一个「一律 xxx」的实现照样全绿）。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan={"chapter_goal": "完全无关的剧情"})
    _insert_character(db, pid, "char_auto", "路人甲", inject_mode="auto")
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, {"purpose": "无关描述"}, relevance_trim=True)
    auto_exc = _by_name(payload["character_state_excerpts"], "路人甲")

    assert auto_exc.get("relevance_summary") is True
    assert "core_json" not in auto_exc


# ---------------------------------------------------------------------------
# 3) JSON 列形状兜底 + 降级留痕（静默吞成 {} 是本仓最忌的方向）
# ---------------------------------------------------------------------------


def test_state_json_with_wrong_shape_degrades_to_empty_object(tmp_path: Path):
    """``state_json`` 是合法 JSON 但不是对象（``[1,2,3]``）⇒ current_state 必须是 {}。

    writer 提示词把 ``current_state`` 声明成 ``"object"``；传数组进 prompt 是形状违约。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "形状怪", state_json="[1,2,3]")

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "形状怪")
    finally:
        conn.close()

    assert exc["current_state"] == {}
    assert isinstance(exc["current_state"], dict)


def test_state_json_valid_object_passes_through_unchanged(tmp_path: Path):
    """反向配对：合法对象原样透传（不得被「一律清空」的实现蒙混）。"""
    payload_state = {"location": "loc_shop", "emotion": "压抑"}
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "正常",
                      state_json=json.dumps(payload_state, ensure_ascii=False))

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "正常")
    finally:
        conn.close()

    assert exc["current_state"] == payload_state


def test_dirty_core_json_warns_with_stable_tag(tmp_path: Path, caplog):
    """非法 JSON 的 core_json ⇒ 降级为 {} **且发一条带稳定 tag 的 WARNING**。

    此前是纯静默吞成 ``{}``：作者看到的正文缺设定，零告警、无从归因。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "脏角色", core_json="{not json")

    conn = get_connection(db)
    try:
        with caplog.at_level(logging.WARNING):
            exc = _by_name(_character_state_excerpts(conn, pid), "脏角色")
    finally:
        conn.close()

    assert exc["core_json"] == {}
    assert any(_JSON_DEGRADED_WARN_TAG in r.message for r in caplog.records), (
        f"降级未留痕；caplog={[r.message for r in caplog.records]}"
    )


def test_legal_empty_json_columns_do_not_warn(tmp_path: Path, caplog):
    """反向配对：合法空态（``{}`` / 空列 / NULL）**不得**告警。

    否则满库空列会把真信号淹掉（坑区：噪声淹没信号）。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "空角色", core_json="{}")
    _insert_character(db, pid, "char_b", "无列", core_json=None, state_json=None)
    conn = get_connection(db)
    try:
        with caplog.at_level(logging.WARNING):
            _character_state_excerpts(conn, pid)
    finally:
        conn.close()

    noisy = [r.message for r in caplog.records if _JSON_DEGRADED_WARN_TAG in r.message]
    assert noisy == [], f"合法空态被误报为降级：{noisy}"


# ---------------------------------------------------------------------------
# 4) 字段完整性：判别面从 PRAGMA 全集推导
# ---------------------------------------------------------------------------

# 明知**不进** excerpt 的列 + 理由（新增列若不在此名册 ⇒ 本用例会红，要求显式决策，
# 而不是「悄悄少一个字段」——AGENTS 坑区：判别面必须写在报告/名册里）。
_EXCERPT_EXCLUDED_COLUMNS: dict[str, str] = {
    "visibility": "已改名注入为 knowledge_scope（同一值，不重复）",
    "aliases": "触发匹配用词面，不是给模型读的内容",
    "created_at": "写入时间戳，正文创作无消费方",
    "updated_at": "写入时间戳，正文创作无消费方",
    "who_knows": "知识权限字段，**已知丢弃**（需产品裁决：是否注入 prompt）",
}


def test_every_characters_column_is_either_carried_or_explicitly_excluded(tmp_path: Path):
    """``characters`` 表的**每一列**必须要么进 excerpt、要么在显式名册里。

    名册从 ``PRAGMA table_info`` 全集推导（不手挑），新增列会自动进判别面。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "角色甲", who_knows='["char_b"]')

    conn = get_connection(db)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
        exc = _by_name(_character_state_excerpts(conn, pid), "角色甲")
    finally:
        conn.close()

    cols.discard("project_id")  # 项目归属由调用方的 WHERE 条件承担
    unaccounted = cols - set(exc) - set(_EXCERPT_EXCLUDED_COLUMNS)
    assert unaccounted == set(), (
        f"characters 新增/未登记列未进 excerpt 也未登记为「刻意丢弃」：{sorted(unaccounted)}；"
        f"如确属丢弃请加入 _EXCERPT_EXCLUDED_COLUMNS 并写明理由"
    )


# ---------------------------------------------------------------------------
# 5) 触发扫描面：合法 director 计划的 key_beats 必须真的进 corpus
# ---------------------------------------------------------------------------

# 期望形状取自 ``structured_output._DIRECTOR_PLAN_ARRAYS`` 的**校验契约**：
# key_beats 元素必须是 object 且含字符串 beat_id / purpose；character_changes_planned
# 元素必须是 object。这不是「跑一下看返回什么」。
LEGAL_DIRECTOR_PLAN = {
    "schema_version": "director-plan.v1",
    "chapter_goal": "无关的章节目标",
    "core_conflict": "无关的冲突",
    "turning_point": "无关的转折",
    "notes_for_planner": "无关的备注",
    "key_beats": [
        {"beat_id": "beat_001", "purpose": "苏婉清登场推销玉佩",
         "involved_characters": ["苏婉清"]},
        {"beat_id": "beat_002", "purpose": "与林渊当面对质",
         "involved_characters": ["林渊"]},
    ],
    "character_changes_planned": [{"name": "周掌柜", "character_id": "char_z"}],
}


def _plan_row(plan: dict):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE chapters (chapter_id TEXT, plan_json TEXT)")
    row = conn.execute(
        "SELECT 'ch1' AS chapter_id, ? AS plan_json",
        (json.dumps(plan, ensure_ascii=False),),
    ).fetchone()
    return conn, row


def test_trigger_corpus_includes_key_beats_of_a_valid_director_plan():
    """合法 director 计划里，key_beats 的 purpose / involved_characters 必须进扫描面。

    原实现只收字符串元素 ⇒ 对**通过校验的**计划 key_beats 恒不贡献，角色只写在
    key_beats 里就会被判「未命中」而降级为 summary。
    """
    conn, row = _plan_row(LEGAL_DIRECTOR_PLAN)
    try:
        corpus = _build_trigger_corpus(row)
    finally:
        conn.close()

    assert "苏婉清" in corpus, "key_beats.involved_characters 未进扫描面"
    assert "林渊" in corpus, "key_beats.purpose 里的角色名未进扫描面"
    assert "周掌柜" in corpus, "character_changes_planned.name 未进扫描面"


def test_trigger_corpus_still_accepts_legacy_string_beats():
    """反向配对：字符串形态的 beat / change **仍然**进扫描面（不得回退已有能力）。"""
    legacy = {
        "chapter_goal": "无关的章节目标",
        "key_beats": ["苏婉清登场推销玉佩"],
        "character_changes_planned": ["周掌柜"],
    }
    conn, row = _plan_row(legacy)
    try:
        corpus = _build_trigger_corpus(row)
    finally:
        conn.close()

    assert "苏婉清登场推销玉佩" in corpus
    assert "周掌柜" in corpus


def test_trigger_corpus_skips_non_string_entity_entries():
    """实体键里混入非字符串（dict / 数字 / None）⇒ 跳过，不把 repr 塞进扫描面。

    触发匹配是字面子串，把 ``{"a": 1}`` 的 repr 写进扫描面会制造噪声命中。
    """
    noisy = {
        "chapter_goal": "无关的章节目标",
        "key_beats": [
            {"beat_id": "b1", "purpose": "有效目的",
             "involved_characters": [{"name": "坏数据"}, 42, None, "苏婉清"]},
        ],
    }
    conn, row = _plan_row(noisy)
    try:
        corpus = _build_trigger_corpus(row)
    finally:
        conn.close()

    assert "苏婉清" in corpus
    assert "坏数据" not in corpus
    assert "{'name'" not in corpus


def test_character_named_only_in_key_beats_is_full_injected(tmp_path: Path):
    """端到端：只在 key_beats 里出现的角色，writer 必须拿到完整角色圣经。

    这是本次报告症状的直接最小复现——旧实现下该角色降级为 summary，
    ``core_json``（含 relationships）整份消失。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan=LEGAL_DIRECTOR_PLAN)
    _insert_character(db, pid, "char_su", "苏婉清")
    _insert_character(db, pid, "char_lin", "林渊")
    _insert_character(db, pid, "char_zhou", "周掌柜")
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, {"purpose": "无关描述"},
                                 context_mode="full", relevance_trim=False)
    for name in ("苏婉清", "林渊", "周掌柜"):
        exc = _by_name(payload["character_state_excerpts"], name)
        assert exc["_injection"] == "full", f"{name} 只在 key_beats 出现却被降级了"
        assert "core_json" in exc


# ---------------------------------------------------------------------------
# 6) 行为现状快照（**不是**断言「正确」，是钉住「设定在什么条件下会消失」）
#    改注入策略时这组会红——那时应改它们并在 CHANGELOG 记明改的是哪条策略。
# ---------------------------------------------------------------------------

SCENE = {"purpose": "夜访古董店", "location": "loc_shop"}


def test_snapshot_full_mode_carries_bible_only_when_trigger_hit(tmp_path: Path):
    """full 模式：命中触发词 ⇒ 带 core_json；未命中 ⇒ 降级为 {id,name,role,summary_line}。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan={"chapter_goal": "林渊夜访古董店"})
    _insert_character(db, pid, "char_hit", "林渊")
    _insert_character(db, pid, "char_miss", "苏婉清")
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, SCENE, context_mode="full",
                                 relevance_trim=False)
    hit = _by_name(payload["character_state_excerpts"], "林渊")
    miss = _by_name(payload["character_state_excerpts"], "苏婉清")

    assert hit["_injection"] == "full"
    assert hit["core_json"]["relationships"][0]["relation_type"] == "bestie"
    assert miss["_injection"] == "summary"
    assert "core_json" not in miss, "未命中触发词的角色彻底失去角色圣经（现状）"
    assert set(miss) == {"character_id", "name", "role", "summary_line",
                         "injection", "_injection"}


def test_snapshot_paged_mode_keeps_protagonist_drops_uninvolved_character(tmp_path: Path):
    """生产默认（paged + relevance on）：**主角恒全量**，未触达且不在本场的角色仍摘要。

    ⚠️ **2026-10-16 批次十一改判**（本条原名 ``..._reduces_untouched_character_to_id_name_role``，
    原断言 ``characters_full == 0``，即**连主角都被削平**，docstring 还把这当
    「钉现状」）。Lead 复核判定：那是缺陷不是现状——``relevance`` 层的 docstring 明写
    「主角与 ``inject_mode='always'`` 的实体始终完整保留」，而分页层从未兑现，
    **两层裁剪互相抵消**。用户 2026-10-16 裁决「作者最新手工设定应当为最新鲜的设定」
    即以此为准。

    仍被摘要的那个（苏婉清：supporting / 不在本场 / 无 delta）**刻意保留**——
    防止修复退化成「一律全量」而悄悄废掉分页模式的 token 预算前提。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    cid = _insert_chapter(db, pid, plan={"chapter_goal": "夜访古董店"})
    _insert_character(db, pid, "char_lead", "主角甲", role="protagonist")
    _insert_character(db, pid, "char_new", "苏婉清")   # 手动新增：state v1={} 且无 delta
    _insert_commit(db, pid, cid, {"character_changes": []})

    _cache_reset()
    payload = build_writer_input(db, cid, SCENE, context_mode="paged",
                                 relevance_trim=True)
    lead = _by_name(payload["character_state_excerpts"], "主角甲")
    new = _by_name(payload["character_state_excerpts"], "苏婉清")

    # 主角：全量（角色圣经在场）
    assert payload["context_paging_stats"]["characters_full"] == 1
    assert lead["role"] == "protagonist"
    assert "summary_marker" not in lead
    assert "current_state" in lead
    # 未触达、不在本场、非常驻的角色：仍是摘要（分页模式的 token 前提未被破坏）
    assert payload["context_paging_stats"]["characters_summary"] == 1
    assert new["summary_marker"] is True
    assert "core_json" not in new
    assert "current_state" not in new


def test_snapshot_manual_character_state_is_empty_not_null(tmp_path: Path):
    """手动新增角色（``CharacterService.create`` 形态）：``current_state == {}`` 而非缺字段，
    ``latest_state_version == 1``（Service 会插 state v1）。提示词未解释空 state 的含义。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "苏婉清", state_json="{}")

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "苏婉清")
    finally:
        conn.close()

    assert exc["current_state"] == {}
    assert exc["latest_state_version"] == 1
    assert "current_state" in exc  # 键在，只是空


def test_snapshot_character_without_state_row_is_null_version(tmp_path: Path):
    """无 state 行（手工 SQL / 备份导入路径）⇒ latest_state_version 为 NULL。

    两种「空」形态（``{}``+version 1 与 ``{}``+NULL）在 payload 里**同形**——
    模型无从分辨「刚建的」与「导入丢的」。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    _insert_character(db, pid, "char_a", "无状态", state_json=None)

    conn = get_connection(db)
    try:
        exc = _by_name(_character_state_excerpts(conn, pid), "无状态")
    finally:
        conn.close()

    assert exc["current_state"] == {}
    assert exc["latest_state_version"] is None
