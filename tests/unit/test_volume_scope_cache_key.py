# -*- coding: utf-8 -*-
"""装配缓存键的「所属卷」维度：``chapters.volume_id`` ⇒ ``volume_scope``（2026-10-23 P0-1 候选 C）。

被守护的缺陷形状（硬规则 2「凡进 payload 的装配输入必须入键」的第 N 次同形）
------------------------------------------------------------------------
``chapters.volume_id`` 决定三处同卷取数（近窗摘要 / 上一章尾段 / FTS 召回，见
``builders_common/summaries.py`` 与 ``retrieval/service.py``）——它是**装配输入**。
而 ``VolumeService.assign_chapter`` 只改 ``chapters.volume_id`` + ``updated_at``，
**不推** ``state_version``、不动 ``plan_json`` / ``outline_json`` ⇒ 修前缓存键里
没有任何一维会因挂卷而变 ⇒「先装配、后挂卷」在同 ``state_version`` + 同
``chapter_no`` 下重跑会命中**旧卷窗口**的装配：换卷后新卷首章仍把上一世的摘要 /
尾段 / 召回当「近窗」注入（快穿冷开场被破），零告警。

判别设计
--------
* ① **挂卷必须 miss 且窗口真的变**：未挂卷 build（能看见更早章号的摘要 / 尾段）
  → ``assign_chapter`` 挂到另一卷 → 再 build 拿到的必须是**新装配**（同卷过滤后
  窗口为空）；
* ② **正反一对**：再换挂回原卷 → 再 build 窗口恢复（证明维度双向生效，不是
  「挂卷后一律空」这种把 bug 挪位置的修法）；
* ③ **哨兵同形**：未挂卷（真实章）与空态 peek（``_empty_chapter_peek``）两处，
  ``volume_scope`` 都必须是 ``"__none__"`` 且落在**同一位置**；同时钉住
  director 键 18 元 / writer 键 23 元。

每用例的项目 / 章 id 逐用例唯一（``uuid4`` 后缀）：装配缓存是**进程级 dict**，
跨用例同 id 会读到别的用例的条目（AGENTS 坑区「跨用例共享进程级缓存」）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.context_engine import cache as cache_mod
from packages.core.context_engine import director_input as di_mod
from packages.core.context_engine import writer_input as wi_mod
from packages.core.context_engine.builders_common import (
    _empty_chapter_peek,
    _peek_chapter_context,
)
from packages.core.context_engine.cache import _cache_reset
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.volume.service import VolumeService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

DIRECTOR_KEY_LEN = 18
WRITER_KEY_LEN = 23
SENTINEL = "__none__"

_VOL1_PROSE = "上一世：林昭在临江县仓清点军粮，苏婉清送来账册。"
_VOL1_PROSE2 = "上一世：苏婉清与永丰号一系议价到闭市，粮价压了下来。"
_VOL1_PROSE3 = "上一世：军粮入库的数目记在账上，临江县仓的门这才关上。"


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
    db_path = tmp_path / "volume_scope_key.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = f"prj_vkey_{uuid4().hex[:10]}"
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
        (pid, "卷维度缓存键测试", now, now),
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
) -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at, volume_id)"
        " VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?, ?)",
        (cid, pid, number, f"第{number}章",
         json.dumps({"chapter_goal": f"第{number}章目标"}, ensure_ascii=False), now, now,
         volume_id),
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


def _seed(tmp_path: Path) -> dict:
    """卷 1 已写 3 章（有正文 + 摘要）；目标章第 4 章**初始未挂卷**；另建一个空卷 2。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    vol1 = _insert_volume(db, pid, 1)
    vol2 = _insert_volume(db, pid, 2)
    ch1 = _insert_chapter(db, pid, 1, vol1)
    ch2 = _insert_chapter(db, pid, 2, vol1)
    ch3 = _insert_chapter(db, pid, 3, vol1)
    ch4 = _insert_chapter(db, pid, 4, None)
    _insert_draft(db, ch1, _VOL1_PROSE)
    _insert_draft(db, ch2, _VOL1_PROSE2)
    _insert_draft(db, ch3, _VOL1_PROSE3)
    _insert_summary(db, pid, ch1, 1, "第一世：林昭在临江县仓清点军粮，苏婉清送来账册。")
    _insert_summary(db, pid, ch2, 2, "第一世：苏婉清与永丰号一系在临江县仓议价。")
    _insert_summary(db, pid, ch3, 3, "第一世：临江县仓的军粮缺口补上，永丰号一系转供。")
    return {
        "db": db, "project": pid, "vol1": vol1, "vol2": vol2, "chapter": ch4,
    }


def _keys(pid: str, role: str) -> list[tuple]:
    """当前进程缓存里属于该项目的某角色键（键首元=project_id，第 4 元=role）。"""
    return [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == role]


def _single_diff_index(a: tuple, b: tuple) -> int:
    """两个缓存键必须**只差一个位置**；返回该位置（同时断言长度相等）。"""
    assert len(a) == len(b), f"键长不一致：{len(a)} vs {len(b)}"
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert len(diff) == 1, f"换卷后应只变 volume_scope 一个位置，实得 {diff} 处：{a} → {b}"
    return diff[0]


def _director_window(payload: dict) -> tuple[list[int], int | None]:
    """把 director 的同卷窗口压成可比较形状（摘要章号序列 + 前章尾段的章号）。"""
    return (
        [s["chapter_no"] for s in payload["recent_chapter_summaries"]],
        (payload["previous_chapter_tail"] or {}).get("chapter_no"),
    )


# ---------------------------------------------------------------------------
# ① 挂卷必须 miss，且同卷窗口真的变（未挂卷 → 另一卷：窗口清空）
# ---------------------------------------------------------------------------


def test_assigning_volume_misses_cache_and_empties_window(tmp_path: Path):
    ids = _seed(tmp_path)
    db, pid, ch4 = ids["db"], ids["project"], ids["chapter"]
    _cache_reset()

    d0 = build_director_input(db, pid, ch4, "")
    w0 = build_writer_input(db, ch4, {})
    assert _director_window(d0) == ([3, 2, 1], 3), "未挂卷 ⇒ 不过滤（旧行为）"
    assert w0["recent_prose"]["last_chapter_excerpt"], "未挂卷 ⇒ 上一章尾段应非空"

    dk0 = _keys(pid, "director")
    wk0 = _keys(pid, "writer")
    assert len(dk0) == 1 and len(wk0) == 1
    assert len(dk0[0]) == DIRECTOR_KEY_LEN
    assert len(wk0[0]) == WRITER_KEY_LEN

    # 真实生产路径：VolumeService.assign_chapter（只改 volume_id + updated_at，不推 sv）
    VolumeService(db).assign_chapter(ids["vol2"], ch4)

    d1 = build_director_input(db, pid, ch4, "")
    w1 = build_writer_input(db, ch4, {})
    assert _director_window(d1) == ([], None), (
        f"挂到卷 2 后同卷窗口应为空（冷开场），实得 {_director_window(d1)}——"
        f"若仍看得到卷 1 摘要/尾段，说明缓存命中了旧装配（脏命中）"
    )
    assert w1["recent_prose"]["last_chapter_excerpt"] == ""

    dk1 = [k for k in _keys(pid, "director") if k != dk0[0]]
    wk1 = [k for k in _keys(pid, "writer") if k != wk0[0]]
    assert len(dk1) == 1, f"挂卷后应多出一条 director 键（旧键仍在），实得 {len(dk1)}"
    assert len(wk1) == 1
    d_idx = _single_diff_index(dk0[0], dk1[0])
    w_idx = _single_diff_index(wk0[0], wk1[0])
    assert dk0[0][d_idx] == SENTINEL and dk1[0][d_idx] == ids["vol2"]
    assert wk0[0][w_idx] == SENTINEL and wk1[0][w_idx] == ids["vol2"]


# ---------------------------------------------------------------------------
# ② 再换挂回卷 1：窗口恢复（正反一对，防「挂卷后一律空」的假修复）
# ---------------------------------------------------------------------------


def test_reassigning_back_restores_window_and_misses_again(tmp_path: Path):
    ids = _seed(tmp_path)
    db, pid, ch4 = ids["db"], ids["project"], ids["chapter"]
    _cache_reset()

    build_director_input(db, pid, ch4, "")
    build_writer_input(db, ch4, {})
    key_null_d = _keys(pid, "director")[0]

    VolumeService(db).assign_chapter(ids["vol2"], ch4)
    d_vol2 = build_director_input(db, pid, ch4, "")
    assert _director_window(d_vol2) == ([], None)
    key_vol2 = [k for k in _keys(pid, "director") if k != key_null_d][0]

    VolumeService(db).assign_chapter(ids["vol1"], ch4)
    d_vol1 = build_director_input(db, pid, ch4, "")
    w_vol1 = build_writer_input(db, ch4, {})
    assert _director_window(d_vol1) == ([3, 2, 1], 3), "挂回同卷 ⇒ 窗口恢复（过滤不误伤同卷）"
    assert w_vol1["recent_prose"]["last_chapter_excerpt"], "挂回同卷 ⇒ 上一章尾段恢复"

    keys = _keys(pid, "director")
    assert len(keys) == 3, f"三次装配（null / vol2 / vol1）应各留一条键，实得 {len(keys)}"
    key_vol1 = [k for k in keys if k not in (key_null_d, key_vol2)]
    assert len(key_vol1) == 1
    idx = _single_diff_index(key_vol2, key_vol1[0])
    assert key_vol2[idx] == ids["vol2"] and key_vol1[0][idx] == ids["vol1"]
    assert _single_diff_index(key_null_d, key_vol1[0]) == idx, "volume_scope 位置在三次装配中恒定"


# ---------------------------------------------------------------------------
# ③ 哨兵同形：未挂卷（真实章）与空态 peek 同形；键长 18 / 23
# ---------------------------------------------------------------------------


def test_unassigned_and_empty_peek_share_sentinel_shape(tmp_path: Path, monkeypatch):
    ids = _seed(tmp_path)
    db, pid, ch4 = ids["db"], ids["project"], ids["chapter"]
    _cache_reset()

    # 真实「未挂卷」章：先拿到键，再挂卷制造「只差一位」的对照键以定位 volume_scope 位置。
    build_director_input(db, pid, ch4, "")
    build_writer_input(db, ch4, {})
    d_null = _keys(pid, "director")[0]
    w_null = _keys(pid, "writer")[0]
    VolumeService(db).assign_chapter(ids["vol1"], ch4)
    build_director_input(db, pid, ch4, "")
    build_writer_input(db, ch4, {})
    d_vol = [k for k in _keys(pid, "director") if k != d_null][0]
    w_vol = [k for k in _keys(pid, "writer") if k != w_null][0]
    d_idx = _single_diff_index(d_null, d_vol)
    w_idx = _single_diff_index(w_null, w_vol)
    assert d_null[d_idx] == w_null[w_idx] == SENTINEL
    assert d_vol[d_idx] == w_vol[w_idx] == ids["vol1"]

    # 空态 peek 路径：章行仍存在（不带 peek 的 uncached 会读库），只把 peek 换成空态形状。
    ch_ghost = _insert_chapter(db, pid, 5, None)
    empty = _empty_chapter_peek(ch_ghost, pid)
    assert empty["volume_id"] is None
    monkeypatch.setattr(
        di_mod, "_peek_chapter_context",
        lambda *a, **k: _empty_chapter_peek(ch_ghost, pid),
    )
    build_director_input(db, pid, ch_ghost, "")
    # writer 用同一份空态 peek（两次 import 的是同一个函数名，打点各自模块属性）。
    monkeypatch.setattr(
        wi_mod, "_peek_chapter_context",
        lambda *a, **k: _empty_chapter_peek(ch_ghost, pid),
    )
    build_writer_input(db, ch_ghost, {})

    d_keys = [k for k in _keys(pid, "director") if k[d_idx] == SENTINEL and k != d_null]
    w_keys = [k for k in _keys(pid, "writer") if k[w_idx] == SENTINEL and k != w_null]
    assert d_keys, "空态 peek 也必须产出 director 键（volume_scope 落哨兵）"
    assert w_keys, "空态 peek 也必须产出 writer 键（volume_scope 落哨兵）"
    for key in d_keys:
        assert len(key) == DIRECTOR_KEY_LEN, f"director 键应 18 元，实得 {len(key)}"
    for key in w_keys:
        assert len(key) == WRITER_KEY_LEN, f"writer 键应 23 元，实得 {len(key)}"


# ---------------------------------------------------------------------------
# ④ peek 本身：主路径 / 空态都带出 volume_id 且口径一致
# ---------------------------------------------------------------------------


def test_peek_carries_volume_id_for_main_and_empty_paths(tmp_path: Path):
    ids = _seed(tmp_path)
    db, ch4 = ids["db"], ids["chapter"]

    assert _peek_chapter_context(db, ch4)["volume_id"] is None
    VolumeService(db).assign_chapter(ids["vol2"], ch4)
    info = _peek_chapter_context(db, ch4)
    assert info["found"] is True
    assert info["volume_id"] == ids["vol2"]
    # 空态：键必须存在且为 None（调用方免判 None）
    empty = _peek_chapter_context(db, "ch_ghost")
    assert empty["found"] is False
    assert "volume_id" in empty and empty["volume_id"] is None
