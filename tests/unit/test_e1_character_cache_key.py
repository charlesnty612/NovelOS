"""E1：角色设定进装配 payload 但**不进装配缓存键** ⇒ 重跑同一章时脏命中（H1）。

外部报告（用户原话）：
    「角色圣经里面角色间的关系是闺蜜，结果在正文里，那个角色却以业务员出场；
      另外，此角色是用户后面手动添加的角色」

缺陷形状与 AGENTS.md 已登记的 0028 ``outline_json`` **逐字同形**：
``characters.core_json`` 整份进 payload（``character_state_excerpts.core_json``），
而缓存键无角色维度 ⇒ 改设定 / 手动新增角色后**同 state_version + 同 chapter_no**
重跑 ⇒ 命中旧装配 ⇒ 正文沿用改动前的角色设定。
``outline_json`` 因此有专属键维度 ``_fingerprint_outline_json`` 与专属看守
``tests/unit/test_chapter_outline.py``；角色侧本文件补上这两者。

⚠️ **H1 的适用边界（本文件量化，见 test_e1_new_chapter_no_misses_without_character_fp）**：
只在「重跑同一章」发作。写新章（``chapter_no`` 不同）时键本就不同 ⇒ 旧实现也不脏命中。

全部用 tmp_path 临时库 + 合成夹具；不接触 dev 库、不发起任何模型调用。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 角色卡首版设定（「闺蜜」关系写在 core_json.relationships 里）
CORE_V1 = {
    "personality": "外柔内刚",
    "relationships": "与林昭是闺蜜",
    "occupation": "古董店店员",
}
CORE_V2 = {
    "personality": "外柔内刚",
    "relationships": "与林昭是死对头",
    "occupation": "保险公司业务员",
}

SCENE = {"purpose": "开场", "scenes": [{"purpose": "s1"}]}

# 夹具默认 ``inject_mode='always'``：``_character_state_excerpts`` 对 ``auto`` 模式
# 角色按 trigger_corpus 判定「命中→full / 未命中→summary」，未命中时 excerpt 只有
# ``{character_id, name, role, statement, _injection}``——**core_json 压根不在 payload 里**，
# 那样断言红在「excerpt 被裁剪」而不是「缓存脏命中」（失败点漂移）。
# ``test_e1_auto_mode_*`` 另用 auto 模式覆盖真路径（名字出现在 scene_plan.characters）。


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, '第一章', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, '{"chapter_goal": "开场"}', now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, '', 'agent:writer:v1', 'writer:v1', 'mock/test', ?)",
            (new_id("dr"), cid, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(
    db_path: Path,
    pid: str,
    *,
    name: str = "沈窈",
    role: str = "protagonist",
    core: dict | None = None,
    visibility: str = "PUBLIC",
    aliases: list[str] | None = None,
    inject_mode: str = "always",
) -> str:
    """插一个角色（+ character_states v1 空状态，与 CharacterService.create 同事务同形）。

    两处夹具纪律（都是为了不让断言红在「excerpt 被裁剪」而不是「缓存脏命中」）：

    - ``inject_mode`` 默认 ``always``：``auto`` 模式下未命中 trigger_corpus 的角色被降级成
      摘要（无 ``core_json``）。见模块顶部说明。
    - ``role`` 默认 ``protagonist``：writer 的相关性裁剪只对主角 / ``inject_mode='always'``
      / 本章 involved 的角色保留完整 excerpt，未命中者被降级成 ``{id, name,
      relevance_summary}``。
    """
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, who_knows, created_at, updated_at, aliases, inject_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                cid, pid, name, role,
                json.dumps(CORE_V1 if core is None else core, ensure_ascii=False),
                visibility, now, now,
                json.dumps(aliases or [], ensure_ascii=False), inject_mode,
            ),
        )
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, who_knows, created_at) VALUES (?, 1, '{}', 'VISIBLE', NULL, ?)",
            (cid, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _set_core(db_path: Path, character_id: str, core: dict) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE characters SET core_json = ?, updated_at = ? WHERE character_id = ?",
            (json.dumps(core, ensure_ascii=False), now_iso(), character_id),
        )
        conn.commit()
    finally:
        conn.close()


def _touch_updated_at(db_path: Path, character_id: str) -> None:
    """只推进 updated_at，**内容一字不动**（用于证伪「拿时间戳当指纹」的假修）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE characters SET updated_at = ? WHERE character_id = ?",
            ("2099-01-01T00:00:00Z", character_id),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_character(db_path: Path, character_id: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM character_states WHERE character_id = ?", (character_id,))
        conn.execute("DELETE FROM characters WHERE character_id = ?", (character_id,))
        conn.commit()
    finally:
        conn.close()


def _update_character_col(db_path: Path, character_id: str, **cols: str) -> None:
    """通用改列（值全是 TEXT；column 名来自调用点字面量，不做用户输入插值）。"""
    sets = ", ".join(f"{col} = ?" for col in cols)
    conn = get_connection(db_path)
    try:
        conn.execute(
            f"UPDATE characters SET {sets}, updated_at = ? WHERE character_id = ?",
            (*cols.values(), now_iso(), character_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_state_json(db_path: Path, character_id: str, state: dict, state_version: int = 2) -> None:
    """追加一条 character_states（observer delta 的形态：状态侧追加式快照）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, who_knows, created_at) VALUES (?, ?, ?, 'VISIBLE', NULL, ?)",
            (character_id, state_version, json.dumps(state, ensure_ascii=False), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计真实重装次数——命中/未命中的有效信号（深拷贝下对象身份恒不可用）。"""
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _full_character(payload: dict, character_id: str) -> dict:
    """取 payload 里该角色的**完整** excerpt（找不到即说明被裁剪，测试自身失效）。"""
    for c in payload.get("character_state_excerpts") or []:
        if isinstance(c, dict) and c.get("character_id") == character_id:
            return c
    raise AssertionError(
        f"角色 {character_id} 不在 payload 的完整 excerpt 里——"
        f"多半是被相关性裁剪降级了，夹具须改成主角/inject_mode=always"
    )


# ---------------------------------------------------------------------------
# 1. 复现：改 core_json 必须 miss（缺陷复发即红）
# ---------------------------------------------------------------------------


def test_e1_writer_core_json_change_misses_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert _full_character(p1, char_id)["core_json"]["relationships"] == CORE_V1["relationships"]
    build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检：确认夹具走的是缓存路径）"

    # 用户在角色页把关系从「闺蜜」改成「死对头」（state_version / chapter_no 均不变）
    _set_core(db_path, char_id, CORE_V2)

    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "改角色设定后必须重装——否则命中旧装配（脏命中）"
    got = _full_character(p2, char_id)["core_json"]
    assert got["relationships"] == CORE_V2["relationships"], (
        f"重装后必须读到新设定，实得 {got['relationships']!r}"
    )


def test_e1_director_core_json_change_misses_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert _full_character(p1, char_id)["core_json"]["relationships"] == CORE_V1["relationships"]
    build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检）"

    _set_core(db_path, char_id, CORE_V2)

    p2 = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "director 同样吃 character_state_excerpts ⇒ 必须重装"
    got = _full_character(p2, char_id)["core_json"]
    assert got["relationships"] == CORE_V2["relationships"], (
        f"重装后必须读到新设定，实得 {got['relationships']!r}"
    )


# ---------------------------------------------------------------------------
# 2. 复现：手动新增 / 删除角色必须 miss（报告人点名的场景）
# ---------------------------------------------------------------------------


def test_e1_writer_added_character_misses_cache(tmp_path: Path, monkeypatch):
    """报告人原话「此角色是用户后面手动添加的角色」——新增角色必须让装配重装。"""
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid, name="林昭", role="supporting")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert len(p1["character_state_excerpts"]) == 1

    new_id = _insert_character(
        db_path, pid, name="周砚", role="protagonist",
        core={"personality": "后来加的角色", "relationships": "与林昭是闺蜜"},
    )
    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "手动新增角色后必须重装"
    assert len(p2["character_state_excerpts"]) == 2, "新角色必须进 payload"
    assert _full_character(p2, new_id)["core_json"]["relationships"] == "与林昭是闺蜜"


def test_e1_director_added_character_misses_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid, name="林昭", role="supporting")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert len(p1["character_state_excerpts"]) == 1

    new_id = _insert_character(db_path, pid, name="周砚", role="protagonist")
    p2 = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "手动新增角色后必须重装"
    assert len(p2["character_state_excerpts"]) == 2
    # director 还把角色 id 投影进 story_state_snapshot.active_characters
    assert new_id in p2["story_state_snapshot"]["active_characters"]


def test_e1_writer_deleted_character_misses_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid, name="林昭", role="supporting")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert len(p1["character_state_excerpts"]) == 1

    _delete_character(db_path, char_id)
    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "删除角色后必须重装（否则 payload 里留着已删角色）"
    assert p2["character_state_excerpts"] == []


def test_e1_director_deleted_character_misses_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid, name="林昭", role="supporting")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert len(p1["character_state_excerpts"]) == 1

    _delete_character(db_path, char_id)
    p2 = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "删除角色后必须重装"
    assert p2["character_state_excerpts"] == []
    assert p2["story_state_snapshot"]["active_characters"] == []


# ---------------------------------------------------------------------------
# 2b. 每一列为什么必须在指纹里（列集选择不是实现细节，逐列给出可执行的判别）
# ---------------------------------------------------------------------------


def test_e1_auto_mode_character_reaches_payload_when_named_in_scene(tmp_path: Path, monkeypatch):
    """真路径（``inject_mode='auto'`` 生产默认）：名字出现在 scene_plan.characters
    ⇒ 该角色 full 注入、``core_json`` 进 payload ⇒ 改它必须 miss。

    与默认夹具（``always``）构成正反：证明修复不是 ``always`` 模式专属的。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid, inject_mode="auto")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    scene = {"purpose": "开场", "characters": ["沈窈"], "scenes": [{"purpose": "s1"}]}

    p1 = build_writer_input(db_path, cid, scene)
    assert _full_character(p1, char_id)["_injection"] == "full", (
        "名字在 scene_plan.characters 里 ⇒ 应 full 注入（量具自检：夹具得真进这条分支）"
    )
    assert p1["character_state_excerpts"][0]["core_json"]["relationships"] == CORE_V1["relationships"]

    _set_core(db_path, char_id, CORE_V2)
    p2 = build_writer_input(db_path, cid, scene)
    assert calls["n"] == 2, "auto 模式 full 注入时改 core_json 同样必须重装"
    assert p2["character_state_excerpts"][0]["core_json"]["relationships"] == CORE_V2["relationships"]


def test_e1_aliases_change_misses_cache(tmp_path: Path, monkeypatch):
    """``aliases`` 决定 auto 模式角色是 full 还是 summary ⇒ 改别名会改 payload 形状。

    若指纹只覆盖 ``core_json``，这一条就漏：core_json 没动而 excerpt 从全文翻成摘要。
    场景文本**全程不变**（否则 scene_fp 一变就 miss，测的就不是 aliases 了）——
    命中的只能是别名，所以角色本名不得出现在 scene 里。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # ⚠️ 2026-10-16 批次十一：`role` 必须**非主角**。夹具默认 `role="protagonist"`，
    # 而主角在 L1 就无条件放行（relevance 层「主角始终完整保留」的既有承诺），
    # ⇒ 本例会恒为 full、测不到 aliases ⇒ 本条曾以「存活」形态挂着。
    char_id = _insert_character(db_path, pid, role="other",
                                inject_mode="auto", aliases=["小林"])
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    scene = {"purpose": "开场", "characters": ["小林"], "scenes": [{"purpose": "s1"}]}

    # ⚠️ 显式关掉 relevance 与 paged 两层：它们各自也会按 id/name/involved 削剪，
    # 会把「aliases 决定 L1 形态」这个待测机制**遮蔽**掉（曾表现为
    # ``KeyError: '_injection'`` —— 角色在 relevance 层已被削成无标记摘要）。
    # 隔离掉之后，全绿/全红只可能来自 aliases ⇒ 判别面干净。
    p1 = build_writer_input(db_path, cid, scene, context_mode="full", relevance_trim=False)
    assert _full_character(p1, char_id)["_injection"] == "full", (
        "别名命中场景文本 ⇒ 应 full 注入（量具自检）"
    )

    # 别名换成场景里没有的词（本名「沈窈」也不在场景里）⇒ 不再触发 ⇒ 降级 summary
    _update_character_col(db_path, char_id, aliases=json.dumps(["阿窈"]))
    p2 = build_writer_input(db_path, cid, scene, context_mode="full", relevance_trim=False)
    assert calls["n"] == 2, "改 aliases 必须重装（它决定 excerpt 的 full/summary 形态）"
    assert _full_character(p2, char_id)["_injection"] == "summary", (
        f"实得形态 {_full_character(p2, char_id).get('_injection')!r}"
    )


def test_e1_inject_mode_change_misses_cache(tmp_path: Path, monkeypatch):
    """``inject_mode``（auto→never）把角色从 payload 里整个剔除 ⇒ 必须重装。"""
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid, role="supporting", inject_mode="always")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert len(p1["character_state_excerpts"]) == 1

    _update_character_col(db_path, char_id, inject_mode="never")
    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "inject_mode 决定该角色是否进 payload ⇒ 必须进指纹"
    # never → 收进 _suppressed_characters 旁块，不在 excerpts 列表里
    assert all(c.get("character_id") != char_id for c in p2["character_state_excerpts"])


def test_e1_visibility_change_misses_cache(tmp_path: Path, monkeypatch):
    """``visibility`` 进 payload 的 ``knowledge_scope`` ⇒ 改它必须重装。

    同时是分页模式下 ``_filter_hidden_by_reveal_policies`` 的判据（PUBLIC→HIDDEN）。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid, visibility="PUBLIC")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert _full_character(p1, char_id)["knowledge_scope"] == "PUBLIC"

    _update_character_col(db_path, char_id, visibility="HIDDEN")
    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "visibility 进 payload.knowledge_scope ⇒ 必须进指纹"
    assert _full_character(p2, char_id)["knowledge_scope"] == "HIDDEN"


def test_e1_character_state_change_misses_cache(tmp_path: Path, monkeypatch):
    """``character_states.state_json`` 进 payload 的 ``current_state`` ⇒ 必须重装。

    这条决定指纹查询必须带 ``LEFT JOIN character_states``（只哈希 characters 表会漏）。
    生产上角色状态由 observer delta 推进且同时推 ``story_states.state_version``，
    故常态下已被 state_version 维度覆盖；但**手工写 character_states 不推**，本用例
    把这条潜在脏命中面钉住。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, SCENE)
    assert _full_character(p1, char_id)["current_state"] == {}
    assert _full_character(p1, char_id)["latest_state_version"] == 1

    _set_state_json(db_path, char_id, {"goal": "夺回铺子"}, state_version=2)
    p2 = build_writer_input(db_path, cid, SCENE)
    assert calls["n"] == 2, "角色状态进 payload.current_state ⇒ 必须进指纹"
    assert _full_character(p2, char_id)["current_state"] == {"goal": "夺回铺子"}
    assert _full_character(p2, char_id)["latest_state_version"] == 2


# ---------------------------------------------------------------------------
# 3. 反向不变量（防「一律 xxx」与「拿时间戳当指纹」的假修）
# ---------------------------------------------------------------------------




def test_e1_director_untouched_still_hits_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _insert_character(db_path, pid, name="林昭", role="supporting")
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    for _ in range(5):
        build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, "无改动时必须只装配一次"




def test_e1_director_updated_at_bump_alone_still_hits_cache(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    char_id = _insert_character(db_path, pid)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    build_director_input(db_path, pid, cid, "意图")
    _touch_updated_at(db_path, char_id)
    build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, "内容未变（只动 updated_at）时必须仍命中缓存"


# ---------------------------------------------------------------------------
# 4. H1 的适用边界（量化）：换章号本就 miss，不靠角色维度
# ---------------------------------------------------------------------------


def test_e1_new_chapter_no_misses_without_character_change(tmp_path: Path, monkeypatch):
    """写**新章**时键的 chapter_no 维度已变 ⇒ 即使角色一个字节没改也 miss。

    ⇒ H1 的成立范围被钉死在「重跑同一章」：这是给报告人的边界，不是修复。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid1 = _insert_chapter(db_path, pid, 1)
    cid2 = _insert_chapter(db_path, pid, 2)
    _insert_character(db_path, pid)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db_path, cid1, SCENE)
    build_writer_input(db_path, cid2, SCENE)
    assert calls["n"] == 2, "不同 chapter_no 本就各装一次（旧实现亦然）"


# ---------------------------------------------------------------------------
# 5. 缓存是**进程内**的（脏命中的存活窗口）
# ---------------------------------------------------------------------------


def test_e1_assembly_cache_is_in_process_only(tmp_path: Path):
    """装配缓存是模块级 dict，进程退出即失 ⇒ 脏命中只在**同一进程内跨调用**存活。

    判别：DB 里不存在任何装配缓存表/列（若有持久化缓存，脏命中会跨重启传播，
    上面「重跑同一章」的复现就不再需要同进程了）。本用例不区分修复前后，
    作用是把「刷新页面能不能中招」这个前提钉成可复核的事实。
    """
    from packages.core.context_engine import cache as cache_mod

    assert isinstance(cache_mod._assembly_cache, dict), "缓存容器是进程内 dict"

    db_path = _fresh_db(tmp_path)
    conn = get_connection(db_path)
    try:
        names = [
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        ]
    finally:
        conn.close()
    offenders = [n for n in names if "cache" in n.lower() or "assembly" in n.lower()]
    assert offenders == [], f"库中不得存在装配缓存持久化面，实得 {offenders}"


# ---------------------------------------------------------------------------
# 6. 指纹覆盖面（全集推导，禁止手挑名单）
# ---------------------------------------------------------------------------


def test_e1_uncached_characters_fingerprint_disables_caching(tmp_path: Path, monkeypatch):
    """指纹算不出来（``_FINGERPRINT_UNCACHED``）⇒ **跳过缓存**，每次都重装。

    少了这一条，守卫 ``chars_fp != _FINGERPRINT_UNCACHED`` 就是纯装饰：把 ``cacheable``
    判据里的 chars_fp 删掉，本文件其余用例**仍全绿**（它们只关心 miss / hit 的方向）。
    """
    from packages.core.context_engine import cache as cache_mod
    from packages.core.context_engine import director_input as di_mod
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _cache_reset()

    monkeypatch.setattr(
        wi_mod, "characters_fingerprint",
        lambda *a, **k: cache_mod._FINGERPRINT_UNCACHED,
    )
    monkeypatch.setattr(
        di_mod, "characters_fingerprint",
        lambda *a, **k: cache_mod._FINGERPRINT_UNCACHED,
    )
    w_calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    d_calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    for _ in range(3):
        build_writer_input(db_path, cid, SCENE)
        build_director_input(db_path, pid, cid, "意图")
    assert w_calls["n"] == 3, "指纹不可算时 writer 不得缓存"
    assert d_calls["n"] == 3, "指纹不可算时 director 不得缓存"
    assert cache_mod._assembly_cache == {}, "不可缓存的装配不得写进缓存"


def _sql_select_columns(sql: str, *, qualifier: str | None = None) -> set[str]:
    """取一段 SQL 的 SELECT 列名（小写、丢 ``AS x`` 别名）。

    ``qualifier`` 给定时只取该表限定符下的列（``"c"`` ⇒ 只看 ``c.*``）；``None`` 时
    返回全部列名（限定符与列名都归一，跨表同名列会合并）。
    """
    body = sql.strip()
    start = body.lower().index("select") + len("select")
    end = body.lower().index("from")
    cols: set[str] = set()
    for raw in body[start:end].split(","):
        token = raw.strip().split()[0] if raw.strip() else ""
        if not token:
            continue
        if "." in token:
            tbl, col = token.split(".", 1)
            if qualifier is not None and tbl.lower() != qualifier.lower():
                continue
            cols.add(col.lower())
        else:
            if qualifier is not None:
                continue
            cols.add(token.lower())
    return cols


def _sql_literal_from_source(func, marker: str) -> str:
    """从函数源码里取出含 ``marker`` 的三引号 SQL 字面量（避开函数 docstring 本身）。"""
    import inspect

    src = inspect.getsource(func)
    for block in re.findall(r'"""(.*?)"""', src, re.S):
        if marker.lower() in block.lower():
            return block
    raise AssertionError(f"未能在 {func.__name__} 源码里定位含 {marker!r} 的 SQL 字面量")


def test_e1_fingerprint_covers_every_character_business_column(tmp_path: Path):
    """指纹必须覆盖 characters 表的**全部业务列**（全集由 PRAGMA 推导，非手挑名单）。

    漏一列就是又一次 ``outline_json``：那一列改了就脏命中，而键看着「有角色维度」。
    排除项只有三个且必须逐条列名：
      - ``created_at`` / ``updated_at``：纯记账时间戳，不进 payload（纳入则「只 bump
        updated_at」也 miss，且「拿 MAX(updated_at) 当指纹」的假修能通过全部判别）；
      - ``project_id``：查询条件本身，恒等于键首元。
    """
    from packages.core.context_engine import cache as cache_mod

    conn = get_connection(_fresh_db(tmp_path))
    try:
        table_cols = {
            r["name"].lower()
            for r in conn.execute("PRAGMA table_info(characters)").fetchall()
        }
    finally:
        conn.close()

    fp_sql = cache_mod._CHARACTERS_FINGERPRINT_SQL
    fp_cols = _sql_select_columns(fp_sql, qualifier="c")
    excluded = {"created_at", "updated_at", "project_id"}
    missing = (table_cols - excluded) - fp_cols
    assert missing == set(), (
        f"characters 表新增/遗漏业务列未进指纹查询：{sorted(missing)}——"
        f"漏列即脏命中（outline_json 同款）"
    )
    assert not (fp_cols - table_cols), (
        f"指纹查询里有 characters 表不存在的列：{sorted(fp_cols - table_cols)}"
    )


def test_e1_fingerprint_sql_superset_of_excerpt_sql():
    """指纹 SELECT 必须**覆盖**装配真读的 excerpt SELECT（安全方向的单调性）。

    列集合两处都从源码里的 SQL 文本推导（``inspect.getsource`` + 三引号字面量提取），
    不是手抄名单——手抄名单就是「判别面按全集推导」纪律的反例：它会随源码演进悄悄缩小。
    """
    from packages.core.context_engine import builders_common
    from packages.core.context_engine import cache as cache_mod

    excerpt_sql = _sql_literal_from_source(
        builders_common._character_state_excerpts, "SELECT c.character_id",
    )
    excerpt_cols = _sql_select_columns(excerpt_sql)
    fp_cols = _sql_select_columns(cache_mod._CHARACTERS_FINGERPRINT_SQL)
    assert excerpt_cols, "未能从 excerpt 源码解析出列集合（SQL 提取本身失效）"
    missing = excerpt_cols - fp_cols
    assert missing == set(), (
        f"装配读取了但指纹没覆盖的列：{sorted(missing)}——改这些列会脏命中"
    )


# ---------------------------------------------------------------------------
# 7. 指纹函数自身的契约
# ---------------------------------------------------------------------------


def test_e1_fingerprint_characters_distinguishes_none_from_empty():
    """``None``（读不到角色表）与 ``[]``（项目确实没角色）必须给**不同**指纹。

    两者都落到同一个占位符 ⇒ 降级路径与「空项目」共用键空间（虽因键首元含
    project_id 而不跨项目撞车，但同项目内降级前后会互相命中——方向仍是脏命中）。
    """
    from packages.core.context_engine.cache import (
        _FINGERPRINT_NONE,
        _FINGERPRINT_UNCACHED,
        _fingerprint_characters,
    )

    assert _fingerprint_characters(None) == _FINGERPRINT_NONE
    empty = _fingerprint_characters([])
    assert empty not in (_FINGERPRINT_NONE, _FINGERPRINT_UNCACHED), (
        f"空角色列表必须有自己的稳定指纹，实得 {empty!r}"
    )
    assert _fingerprint_characters(None) != empty


def test_e1_fingerprint_characters_is_order_stable_and_content_sensitive():
    """同一组行（含顺序）指纹恒定；任一格改动指纹改变。"""
    from packages.core.context_engine.cache import _fingerprint_characters

    row = ["char_1", "沈窈", "protagonist", '{"relationships": "闺蜜"}', "PUBLIC", "[]", "auto"]
    assert _fingerprint_characters([row]) == _fingerprint_characters([list(row)])
    assert _fingerprint_characters([row]) != _fingerprint_characters([["char_2", *row[1:]]])
    assert _fingerprint_characters([row]) != _fingerprint_characters([[*row[:-1], "always"]])


def test_e1_fingerprint_characters_unserializable_goes_uncached():
    """不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存（不得静默降级）。"""
    from packages.core.context_engine.cache import (
        _FINGERPRINT_UNCACHED,
        _fingerprint_characters,
    )

    class Boom:
        pass

    assert _fingerprint_characters([[Boom()]]) == _FINGERPRINT_UNCACHED


# ---------------------------------------------------------------------------
# 8. 键形态：两个键都带角色维度，且落在 prompt_label 之前
# ---------------------------------------------------------------------------


def test_e1_writer_cache_key_carries_characters_dimension(tmp_path: Path):
    from packages.core.context_engine import cache as cache_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _cache_reset()

    build_writer_input(db_path, cid, SCENE)
    keys = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer"]
    assert len(keys) == 1
    key = keys[0]
    assert key[-2] == "writer:v1", "倒数第二位恒为 prompt 标签"
    assert key[-1] == cache_mod._cache_namespace_tag(""), "键尾恒为命名空间标记"
    chars_fp = cache_mod.characters_fingerprint(db_path, pid)
    assert chars_fp in key, "角色指纹必须在 writer 键里"
    # ⚠️ **不要用绝对下标**：writer 键在批次十一又加了 ``plan_fp``，下标逐刻漂移。
    # 按语义断言——「角色维度在键内」且「落在 prompt_label 之前」，这才是要守的性质。
    assert key.index(chars_fp) < key.index("writer:v1"),         "角色维度必须落在 prompt_label 之前（键尾两元语义不变）"


def test_e1_director_cache_key_carries_characters_dimension(tmp_path: Path):
    from packages.core.context_engine import cache as cache_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _cache_reset()

    build_director_input(db_path, pid, cid, "意图")
    keys = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"]
    assert len(keys) == 1
    key = keys[0]
    assert key[-2] == "director:v1"
    assert key[-1] == cache_mod._cache_namespace_tag("")
    chars_fp = cache_mod.characters_fingerprint(db_path, pid)
    assert key.index(chars_fp) < key.index("director:v1"),         "director 键同样带角色维度（落在 prompt_label 之前）"


def test_e1_writer_and_director_share_one_characters_fingerprint(tmp_path: Path):
    """回答「director 与 writer 的角色数据是不是同一份」。

    两者都调 ``_character_state_excerpts``（同一 SELECT 同一 JOIN），只是
    ``trigger_corpus`` 不同 ⇒ **条目可能不同、来源列完全相同** ⇒ 共用一个指纹维度。
    """
    from packages.core.context_engine import cache as cache_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _cache_reset()

    build_writer_input(db_path, cid, SCENE)
    build_director_input(db_path, pid, cid, "意图")
    w_key = next(k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer")
    d_key = next(k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director")
    chars_fp = cache_mod.characters_fingerprint(db_path, pid)
    # 同上：不比绝对下标（两键长度不同，writer 比 director 多若干维度），比「同一个值」。
    assert (w_key[w_key.index(chars_fp)]
            == d_key[d_key.index(chars_fp)] == chars_fp), "两个键的角色维度必须同值（单一数据源）"
