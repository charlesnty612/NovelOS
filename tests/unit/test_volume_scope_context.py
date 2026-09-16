# -*- coding: utf-8 -*-
"""快穿位面隔离：上下文装配的「同卷过滤」口径（2026-09-16）。

被守护的缺陷形状：五处取数按**章号**而非按**卷**，于是换位面后新卷第一章仍以
「近窗 / 上一章 / 相关历史」的姿态吃到上一世的正文、摘要与实体名。快穿要的是
**冷开场**——上一世只经结算单 / 作者意图一笔带过，不进正文上下文。

本文件用**真装配证据**（``build_director_input`` / ``build_writer_input`` 的 payload）
覆盖四处消费面 + 一处元标记：

1. ``recent_chapter_summaries``（director）—— 同卷过滤；
2. ``previous_chapter_tail``（director）—— 同卷过滤；
3. ``recent_prose.last_chapter_excerpt``（writer）—— 同卷过滤；
4. ``recalled_passages``（writer + director，FTS 召回）—— 同卷过滤；
5. ``_suppressed_characters / _suppressed_locations / _suppressed_factions``
   元标记块**不带 name**（只带 id）。

零回归面（同样重要，各有用例）：
- 卷 1 内部的第 2 章**仍能**拿到卷 1 第 1 章的摘要 / 尾段 / 召回（同卷过滤不误伤）；
- 未挂卷的（``volume_id IS NULL``）章节**不过滤**（旧项目行为逐字保留）。

突变验证（见交付说明）：把同卷过滤摘掉（三处 helper 恒返回 None / 元标记补回 name）
→ 本文件第二个用例必红。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.retrieval import rebuild_index, search

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 上一世（卷 1 / 临江）专属专名：换位面后一个都不该出现在新卷第一章的正文上下文里。
VOL1_CHAR = "苏婉清"
VOL1_LOCATION = "临江县仓"
VOL1_FACTION = "永丰号一系"
VOL1_NAMES = (VOL1_CHAR, VOL1_LOCATION, VOL1_FACTION)

PROTAGONIST = "林昭"

_CH1_PROSE = (
    "林昭清点临江县仓的军粮，苏婉清抱着账册站在一旁。"
    "永丰号一系的人送来三百石存粮，米价终于压下来。"
    "他把最后一批军粮入库的数目记在账上，临江县仓的门这才关上。"
)
_CH2_PROSE = (
    "苏婉清在临江县仓清点存粮，与永丰号一系议价到闭市。"
    "林昭把赊账承付权逐个过目，临江县仓的空垛重新垒了起来。"
)
_CH3_PROSE = (
    "林昭与苏婉清在临江县仓盘点军粮，永丰号一系的粮车堵在门口。"
    "账册上的缺口补上了，临江县仓的兵马粮草总算有了着落。"
)


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


def _exec(db_path: Path, sql: str, params: tuple) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "volume_scope.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _ns() -> str:
    """每用例独立命名空间：装配缓存是进程级，跨用例同键会脏命中。"""
    return f"volume-scope-{uuid4().hex[:8]}"


def _insert_project(db_path: Path, pid: str = "prj_volume_scope") -> str:
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
        (pid, "快穿同卷过滤测试", now, now),
    )
    return pid


def _insert_volume(db_path: Path, pid: str, number: int) -> str:
    vid = new_id("vol")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO volumes (volume_id, project_id, number, title, status,"
        " terminal_snapshot_json, created_at, updated_at, arc_summary)"
        " VALUES (?, ?, ?, ?, 'active', NULL, ?, ?, NULL)",
        (vid, pid, number, f"第{number}卷", now, now),
    )
    return vid


def _insert_chapter(
    db_path: Path, pid: str, number: int, volume_id: str | None,
    *, plan_json: dict | None = None,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at, volume_id)"
        " VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?, ?)",
        (cid, pid, number, f"第{number}章",
         json.dumps(plan_json or {}, ensure_ascii=False), now, now, volume_id),
    )
    return cid


def _insert_draft(db_path: Path, chapter_id: str, content: str) -> None:
    _exec(
        db_path,
        "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,"
        " prompt_version, model_id, created_at)"
        " VALUES (?, ?, 1, ?, 'test:writer:v1', NULL, NULL, ?)",
        (new_id("drf"), chapter_id, content, now_iso()),
    )


def _insert_summary(
    db_path: Path, pid: str, chapter_id: str, chapter_no: int, summary: str,
) -> None:
    _exec(
        db_path,
        "INSERT INTO chapter_summaries (summary_id, project_id, chapter_id, chapter_no,"
        " summary, tail_text, created_at) VALUES (?, ?, ?, ?, ?, '', ?)",
        (new_id("sum"), pid, chapter_id, chapter_no, summary, now_iso()),
    )


def _insert_entity(
    db_path: Path, table: str, id_col: str, prefix: str, pid: str, name: str, *,
    role: str | None = None, inject_mode: str = "auto",
) -> str:
    eid = new_id(prefix)
    now = now_iso()
    if table == "characters":
        _exec(
            db_path,
            "INSERT INTO characters (character_id, project_id, name, role, core_json,"
            " visibility, who_knows, created_at, updated_at, aliases, inject_mode)"
            " VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?, '[]', ?)",
            (eid, pid, name, role or "supporting", now, now, inject_mode),
        )
    else:
        _exec(
            db_path,
            f"INSERT INTO {table} ({id_col}, project_id, name, statement, data_json,"
            " visibility, who_knows, created_at, updated_at, aliases, inject_mode)"
            " VALUES (?, ?, ?, '', '{}', 'PUBLIC', NULL, ?, ?, '[]', ?)",
            (eid, pid, name, now, now, inject_mode),
        )
    return eid


def _seed(tmp_path: Path) -> dict:
    """最小两卷项目：卷 1 已写完 3 章（第 3 章未挂卷，模拟存量项目），卷 2 第 1 章 = 第 25 章。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    vol1 = _insert_volume(db, pid, 1)
    vol2 = _insert_volume(db, pid, 2)
    ch1 = _insert_chapter(db, pid, 1, vol1, plan_json={
        "chapter_goal": "林昭在临江县仓清点军粮", "key_beats": ["苏婉清送来账册"],
    })
    ch2 = _insert_chapter(db, pid, 2, vol1, plan_json={
        "chapter_goal": "苏婉清在临江县仓清点存粮，与永丰号一系议价",
    })
    ch3 = _insert_chapter(db, pid, 3, None, plan_json={
        "chapter_goal": "林昭在临江县仓清点军粮，苏婉清与永丰号一系议价",
    })
    ch25 = _insert_chapter(db, pid, 25, vol2, plan_json={
        "chapter_goal": "林昭在雁门卫的军寨醒来，接手原身留下的军粮亏空",
        "key_beats": ["林昭清点军粮，发现账面与实物对不上"],
    })
    _insert_draft(db, ch1, _CH1_PROSE)
    _insert_draft(db, ch2, _CH2_PROSE)
    _insert_draft(db, ch3, _CH3_PROSE)
    _insert_summary(db, pid, ch1, 1, "第一世：林昭在临江县仓清点军粮，苏婉清送来账册。")
    _insert_summary(db, pid, ch2, 2, "第一世：苏婉清与永丰号一系在临江县仓议价。")
    _insert_summary(db, pid, ch3, 3, "第一世：临江县仓的军粮缺口补上，永丰号一系转供。")
    ids = {
        "db": db, "project": pid, "vol1": vol1, "vol2": vol2,
        "ch1": ch1, "ch2": ch2, "ch3": ch3, "ch25": ch25,
        # 跨位面主账本角色（快穿主角豁免归档）——始终在注入面内
        "hero": _insert_entity(db, "characters", "character_id", "char", pid,
                               PROTAGONIST, role="protagonist"),
        # 上一世实体：世界切换后 inject_mode='never'（本用例直接按该终态起库）
        "vol1_char": _insert_entity(db, "characters", "character_id", "char", pid,
                                    VOL1_CHAR, inject_mode="never"),
        "vol1_location": _insert_entity(db, "locations", "location_id", "loc", pid,
                                        VOL1_LOCATION, inject_mode="never"),
        "vol1_faction": _insert_entity(db, "factions", "faction_id", "fac", pid,
                                       VOL1_FACTION, inject_mode="never"),
    }
    rebuild_index(str(db), pid)
    return ids


# ---------------------------------------------------------------------------
# 断言工具：payload 里专名出现次数（字符串叶子级递归扫描）
# ---------------------------------------------------------------------------


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out += _strings(item)
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out += _strings(item)
        return out
    return []


def _name_hits(value, names: tuple[str, ...] = VOL1_NAMES) -> dict[str, int]:
    """统计 names 在 value 的全部字符串叶子里各出现多少次。"""
    hits = {n: 0 for n in names}
    for s in _strings(value):
        for n in names:
            if n in s:
                hits[n] += 1
    return hits


def _suppressed_blocks(payload: dict) -> list[dict]:
    """收集 payload 里全部 suppressed 元标记条目（角色 + 地点 + 势力）。"""
    out: list[dict] = []
    for c in payload.get("character_state_excerpts") or []:
        if isinstance(c, dict):
            out += [s for s in c.get("_suppressed_characters") or [] if isinstance(s, dict)]
    world = payload.get("world_state_excerpts") or {}
    out += [s for s in world.get("_suppressed_locations") or [] if isinstance(s, dict)]
    out += [s for s in world.get("_suppressed_factions") or [] if isinstance(s, dict)]
    return out


# ---------------------------------------------------------------------------
# 1. 核心：卷 2 第 1 章 = 冷开场
# ---------------------------------------------------------------------------


def test_second_volume_first_chapter_excludes_previous_volume(tmp_path: Path):
    """卷 2 第 1 章：近窗三处为空、召回为空、元标记不带上一世专名。"""
    ids = _seed(tmp_path)
    ns = _ns()
    director = build_director_input(ids["db"], ids["project"], ids["ch25"], "", namespace=ns)
    writer = build_writer_input(ids["db"], ids["ch25"], {}, namespace=ns)

    # 近窗链：上一卷没有同卷前章 → 空（不是「上一世的最后一章」）
    assert director["recent_chapter_summaries"] == []
    assert director["previous_chapter_tail"] == {}
    assert writer["recent_prose"]["last_chapter_excerpt"] == ""
    assert director["recalled_passages"] == []
    assert writer["recalled_passages"] == []

    # 四处消费面逐点无上一世专名
    for field, value in (
        ("director.recent_chapter_summaries", director["recent_chapter_summaries"]),
        ("director.previous_chapter_tail", director["previous_chapter_tail"]),
        ("writer.recent_prose.last_chapter_excerpt",
         writer["recent_prose"]["last_chapter_excerpt"]),
        ("director.recalled_passages", director["recalled_passages"]),
        ("writer.recalled_passages", writer["recalled_passages"]),
    ):
        assert _name_hits(value) == dict.fromkeys(VOL1_NAMES, 0), (field, value)

    # 元标记块：只带 id，不带 name（块内任何字符串都不含上一世专名）
    director_blocks = _suppressed_blocks(director)
    assert {e.get("character_id") for e in director_blocks if e.get("character_id")} == {
        ids["vol1_char"]}
    assert {e.get("location_id") for e in director_blocks if e.get("location_id")} == {
        ids["vol1_location"]}
    assert {e.get("faction_id") for e in director_blocks if e.get("faction_id")} == {
        ids["vol1_faction"]}
    for entry in director_blocks:
        assert "name" not in entry, entry
        assert entry["injection"] == "suppressed", entry
        assert _name_hits(entry) == dict.fromkeys(VOL1_NAMES, 0), entry

    # writer 默认 relevance_trim=on：character 元标记会被相关性裁剪降级为
    # ``{character_id: None, name: None}``（存量行为，非本批改动），但 world 的两块
    # 原样保留——两处都必须只带 id。
    writer_blocks = _suppressed_blocks(writer)
    assert {e.get("location_id") for e in writer_blocks if e.get("location_id")} == {
        ids["vol1_location"]}
    assert {e.get("faction_id") for e in writer_blocks if e.get("faction_id")} == {
        ids["vol1_faction"]}
    for entry in writer_blocks:
        assert "name" not in entry, entry

    writer_untrimmed = build_writer_input(
        ids["db"], ids["ch25"], {}, relevance_trim=False, namespace=_ns(),
    )
    untrimmed_blocks = _suppressed_blocks(writer_untrimmed)
    assert ids["vol1_char"] in {e.get("character_id") for e in untrimmed_blocks}, untrimmed_blocks
    for entry in untrimmed_blocks:
        assert "name" not in entry, entry
        assert _name_hits(entry) == dict.fromkeys(VOL1_NAMES, 0), entry

    # 全 payload 扫描（最强口径：整个 user message 里不该出现上一世专名）
    for payload, label in ((director, "director"), (writer, "writer")):
        assert _name_hits(payload) == dict.fromkeys(VOL1_NAMES, 0), (label, payload)
    # 主角仍在该扫描范围内正常注入（证明上一条不是「整份 payload 被清空」）
    assert PROTAGONIST in json.dumps(director, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 2. 零回归：卷 1 内部仍能取到同卷前一章
# ---------------------------------------------------------------------------


def test_same_volume_chapter_still_sees_previous_chapter(tmp_path: Path):
    """卷 1 第 2 章：摘要 / 前章尾段 / 召回都还能拿到卷第 1 章（过滤不误伤同卷）。"""
    ids = _seed(tmp_path)
    ns = _ns()
    director = build_director_input(ids["db"], ids["project"], ids["ch2"], "", namespace=ns)
    writer = build_writer_input(ids["db"], ids["ch2"], {}, namespace=ns)

    summaries = director["recent_chapter_summaries"]
    assert [s["chapter_no"] for s in summaries] == [1], summaries
    assert VOL1_CHAR in summaries[0]["summary"]

    tail = director["previous_chapter_tail"]
    assert tail.get("chapter_no") == 1, tail
    assert tail.get("chapter_id") == ids["ch1"], tail
    assert VOL1_LOCATION in tail["tail_text"], tail

    writer_tail = writer["recent_prose"]["last_chapter_excerpt"]
    assert VOL1_LOCATION in writer_tail, writer_tail

    # 召回：卷 1 第 1 章正文仍可被召回（同卷过滤不禁同卷召回）
    assert director["recalled_passages"], "卷 1 内部召回不应被同卷过滤清空"
    assert any(VOL1_CHAR in p["snippet"] or VOL1_LOCATION in p["snippet"]
               for p in director["recalled_passages"]), director["recalled_passages"]


# ---------------------------------------------------------------------------
# 3. 未挂卷章节：不过滤（存量项目 / 旧库零行为变化）
# ---------------------------------------------------------------------------


def test_chapter_without_volume_keeps_legacy_unfiltered_window(tmp_path: Path):
    """``volume_id IS NULL``（存量项目）→ 不过滤：仍能取到更早章号的摘要 / 尾段。"""
    ids = _seed(tmp_path)
    ns = _ns()
    director = build_director_input(ids["db"], ids["project"], ids["ch3"], "", namespace=ns)

    assert [s["chapter_no"] for s in director["recent_chapter_summaries"]] == [2, 1]
    tail = director["previous_chapter_tail"]
    assert tail.get("chapter_no") == 2, tail
    assert VOL1_CHAR in tail["tail_text"], tail


# ---------------------------------------------------------------------------
# 4. retrieval.search 直测：同卷过滤 + 未挂卷不过滤
# ---------------------------------------------------------------------------


def test_search_filters_to_current_volume(tmp_path: Path):
    """卷 2 第 1 章召回不到卷 1 正文；给定项目内无同卷正文 → 空 list（非报错）。"""
    ids = _seed(tmp_path)
    # query 取卷 1 正文里的词：不过滤时必然命中卷 1 三章
    unfiltered = search(str(ids["db"]), ids["project"], "临江县仓 军粮 苏婉清")
    assert unfiltered, "未加位置/同卷过滤时应召回卷 1 正文（用例前提）"

    # 只给 current_chapter_no（无 chapter_id）→ 走 (project_id, number) 定位卷分支
    scoped = search(str(ids["db"]), ids["project"], "临江县仓 军粮 苏婉清",
                    current_chapter_no=25)
    assert scoped == [], scoped

    # 已挂卷的章（卷 1 第 2 章）→ 同卷召回照常
    same_volume = search(str(ids["db"]), ids["project"], "临江县仓 军粮 苏婉清",
                         current_chapter_id=ids["ch2"], current_chapter_no=2)
    assert [p["chapter_no"] for p in same_volume] == [1], same_volume


def test_volume_id_column_missing_degrades_to_old_behavior(tmp_path: Path):
    """旧库缺 ``chapters.volume_id`` 列（迁移 0015 之前）→ 同卷过滤就地降级，不抛错。

    人造探针：把 0015 加的那一列与它的索引去掉（SQLite 3.35+ 支持 DROP COLUMN），
    模拟「极老库」——装配必须退化为旧行为（不过滤），不得因读列失败而崩。
    """
    ids = _seed(tmp_path)
    conn: sqlite3.Connection = get_connection(ids["db"])
    try:
        conn.execute("DROP INDEX idx_chapters_volume_id")
        conn.execute("ALTER TABLE chapters DROP COLUMN volume_id")
        conn.commit()
    finally:
        conn.close()

    # 不过滤（列缺失 → None）：卷 2 第 1 章又「看得见」卷 1 摘要——旧行为，但不崩
    director = build_director_input(ids["db"], ids["project"], ids["ch25"], "", namespace=_ns())
    assert [s["chapter_no"] for s in director["recent_chapter_summaries"]] == [3, 2, 1]
    assert director["previous_chapter_tail"].get("chapter_no") == 3
    # 召回同样退化为旧行为（非空；若在 search() 里引用缺列 → 会被 _recall_passages 的
    # 降级捕获而静默清空，故这里直测 search 与装配两处）
    assert search(str(ids["db"]), ids["project"], "临江县仓 军粮 苏婉清",
                  current_chapter_no=25), "旧库缺列 → 不过滤，召回不得被清空"
    assert director["recalled_passages"], director["recalled_passages"]
