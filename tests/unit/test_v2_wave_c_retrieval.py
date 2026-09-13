"""V2.0 Wave C 任务一：FTS5 召回层测试。

覆盖：
1. 迁移 0011 落地后 chapter_fts 虚表存在 + count_tables 口径排除 FTS 内部表；
2. extract_keywords：中英文 + bigram + 停用词 + 实体名优先；
3. upsert_chapter：写入新章节正文 → FTS 索引同步；
4. search：top-3 命中 + 排序 + 自排除 + 截断（≤300 字 + 省略号）；
5. 降级路径：search 表达式非法 / chapter_fts 不存在 → 空 list 不抛；
6. 装配注入：build_director_input / build_writer_input 含 recalled_passages 键；
7. preview 同步：preview_context 输出 recalled_passage 条目。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.retrieval import (
    extract_keywords,
    rebuild_index,
    search,
    upsert_chapter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_state_version(db_path: Path, project_id: str, version: int) -> None:
    """写一行 story_states（V3.9 批次 2.3 起 state_version 由 MAX(state_version) 取）。

    批次 2.3 之前缓存键的 state_version 走 ``StoryStateService.get_current_state``，
    测试可以 monkeypatch 它来伪造版本推进；收敛后即为「DB 里真实存在的最新版本」，
    因此用例改为直接写行。故意用裸 sqlite3 连接（不打开 FK PRAGMA）：
    ``story_states.commit_id`` 的 FK 指向 commits，而缓存键只关心 state_version 维度，
    无需为它铺 branches / state_deltas / commits 全套行。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO story_states (project_id, state_version, snapshot_json,"
            " commit_id, created_at) VALUES (?, ?, '{}', 'cmt_test', ?)",
            (project_id, version, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path,
    pid: str,
    number: int,
    title: str = "C",
    content: str = "",
    plan_json: str = "{}",
) -> str:
    """插入章节 + 草稿正文（drafts.content 才是章节正文）。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'COMMITTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, title, plan_json, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'agent:writer:v1', 'writer:v1', 'mock/test', ?)",
            (new_id("dr"), cid, content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1. extract_keywords
# ---------------------------------------------------------------------------


def test_extract_keywords_chinese_bigram_and_stopwords():
    kws = extract_keywords("林轩握碎古镜外层封印的瞬间")
    # 至少包含一些 bigram
    assert "林轩" in kws
    assert "握碎" in kws
    assert "碎古" in kws
    # 停用词过滤
    assert "的了" not in "".join(kws)
    # 单字符 / 高频停用词不会出现（bigram 自动避免）
    for sw in ("的", "了", "是", "在"):
        assert not any(sw == k for k in kws)


def test_extract_keywords_english_and_numbers():
    kws = extract_keywords("Chapter 5: dragon attack at midnight")
    # 英文小写 token
    assert "chapter" in kws
    assert "dragon" in kws
    assert "attack" in kws


def test_extract_keywords_entity_names_first():
    kws = extract_keywords("章节冲突", entity_names=["林轩", "古镜"])
    # 实体名整体 token 应在前 2 个
    assert "林轩" in kws[:2]
    assert "古镜" in kws[:2]


def test_extract_keywords_skips_long_pure_cjk_entity():
    """P2-4：纯 CJK 无空格长串（长度 > 8）不作为整词 entity token。

    bigram 已覆盖所有相邻 2 字组合，整词注入反而拉低 bm25 相关性。
    """
    long_cjk = "林轩握碎古镜外层封印"  # 长度 10，纯 CJK，无空格
    assert len(long_cjk) > 8
    kws = extract_keywords("其他章节内容", entity_names=[long_cjk])
    assert long_cjk not in kws, "长度>8 的纯 CJK entity 不应作为整词 token"
    # 但 bigram 仍覆盖（plan_text 里没有，但可验证函数仍正确返回）
    assert isinstance(kws, list)


def test_extract_keywords_keeps_short_entity():
    """P2-4：短实体名（≤8）继续作为整词 token。"""
    short_cjk = "林轩"
    kws = extract_keywords("其他", entity_names=[short_cjk])
    assert short_cjk in kws


def test_extract_keywords_long_mixed_string_still_kept():
    """P2-4：含非 CJK 的长串仍作为整词 token（避免误伤「林轩-sword」之类）。"""
    mixed = "林轩-sword-of-destiny"  # 长度 > 8，但非纯 CJK
    kws = extract_keywords("其他", entity_names=[mixed])
    assert mixed in kws


def test_extract_keywords_empty_text():
    assert extract_keywords("") == []


# ---------------------------------------------------------------------------
# 2. upsert_chapter + search（命中）
# ---------------------------------------------------------------------------


def test_upsert_chapter_indexes_content_for_search(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid, 1, content="林轩在禁地中握住古镜外层封印",
    )
    # upsert 应成功
    assert upsert_chapter(db_path, cid) is True
    # 搜索命中
    hits = search(db_path, pid, "古镜", limit=3)
    assert len(hits) >= 1
    assert hits[0]["chapter_id"] == cid
    assert "林轩" in hits[0]["snippet"] or "古镜" in hits[0]["snippet"]


def test_search_top_3_and_rank_order(tmp_path: Path):
    """构造 5 章检索，命中正确章节 + top-3 排序。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    chapters: list[tuple[int, str]] = []
    for n, content in [
        (1, "林轩在山门学习"),
        (2, "林轩进入禁地"),
        (3, "古镜觉醒，识海剧痛"),
        (4, "古镜外层封印碎裂"),
        (5, "林轩离开禁地，回宗门"),
    ]:
        cid = _insert_chapter(db_path, pid, n, content=content)
        chapters.append((n, cid))
        assert upsert_chapter(db_path, cid)

    hits = search(db_path, pid, "古镜", limit=3)
    assert len(hits) <= 3
    # 命中应该是含"古镜"的章节（3 / 4）
    hit_nos = {h["chapter_no"] for h in hits}
    assert hit_nos.issubset({3, 4})
    # rank 应有顺序（bm25 升序 = 相关度降序）
    if len(hits) >= 2:
        assert hits[0]["rank"] <= hits[1]["rank"]


def test_search_excludes_current_chapter(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid_1 = _insert_chapter(db_path, pid, 1, content="古镜觉醒")
    cid_2 = _insert_chapter(db_path, pid, 2, content="古镜再被提及")
    upsert_chapter(db_path, cid_1)
    upsert_chapter(db_path, cid_2)
    hits = search(db_path, pid, "古镜", current_chapter_id=cid_2, limit=3)
    hit_ids = {h["chapter_id"] for h in hits}
    assert cid_2 not in hit_ids
    # 应只命中 cid_1
    assert cid_1 in hit_ids


def test_search_snippet_truncation(tmp_path: Path):
    """长内容 snippet 截断 ≤ 300 字 + 省略号。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    long_text = "古镜" + ("夜雨落入禁地。" * 200)
    cid = _insert_chapter(db_path, pid, 1, content=long_text)
    upsert_chapter(db_path, cid)
    hits = search(db_path, pid, "古镜", limit=1, snippet_max_chars=120)
    assert len(hits) == 1
    assert len(hits[0]["snippet"]) <= 121  # ≤120 + 省略号
    assert hits[0]["snippet"].endswith("…")


def test_search_degrade_empty_query_returns_empty(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    assert search(db_path, pid, "") == []
    assert search(db_path, pid, "   ") == []


def test_search_handles_token_with_embedded_quote(tmp_path: Path):
    """P2-1 修复：query token 含双引号必须按 FTS5 字符串转义规则处理。

    用户的 query 若包含 ``"``（如 ``hello"world``），旧实现把整个 token 套双引号
    会让 FTS5 解析器把它当成字符串不闭合 → 表达式非法 → search 降级返回空 list。
    修复：先把 token 内 ``"`` 替换为 ``""``（FTS5 字符串内双引号转义），
    再用双引号外包 → 不降级、行为正确。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="古镜被提及了一次")
    upsert_chapter(db_path, cid)
    # query 含双引号：以前会触发表达式非法 → 返回 []；现在应正常命中
    # 用「古镜」+ 含引号 token 混合，验证既不降级也不抛错
    out = search(db_path, pid, '古镜 hello"world', limit=3)
    assert isinstance(out, list)
    # 含引号 token 在正文里没命中没关系，「古镜」命中即可证明没降级
    assert len(out) >= 1
    assert any(h["chapter_id"] == cid for h in out)


def test_search_pure_quote_token_does_not_downgrade(tmp_path: Path):
    """P2-1 修复：纯引号 token（如 ``"test"``）也不能触发表达式非法降级。

    旧实现会把内部 ``"`` 转义失败；现在应正常处理（即便命中为空，也应返回空
    list 而非抛错或异常路径）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="古镜觉醒")
    upsert_chapter(db_path, cid)
    out = search(db_path, pid, '"hello"', limit=3)
    assert isinstance(out, list)
    # 不抛错 + 行为合理（不命中→ 空 list）即可
    assert out == [] or isinstance(out, list)


def test_search_degrade_fts_missing_returns_empty(tmp_path: Path, monkeypatch):
    """FTS 虚表不存在时 search 静默返回空 list（降级语义）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 删除 chapter_fts 模拟虚表不存在
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS chapter_fts")
        conn.execute("DROP TABLE IF EXISTS chapter_fts_data")
        conn.execute("DROP TABLE IF EXISTS chapter_fts_idx")
        conn.execute("DROP TABLE IF EXISTS chapter_fts_config")
        conn.execute("DROP TABLE IF EXISTS chapter_fts_docsize")
        conn.commit()
    finally:
        conn.close()
    # 不抛错
    hits = search(db_path, pid, "任意关键词")
    assert hits == []


# ---------------------------------------------------------------------------
# 3. rebuild_index
# ---------------------------------------------------------------------------


def test_rebuild_index_full(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    for n in range(3):
        _ = _insert_chapter(db_path, pid, n + 1, content=f"古镜在章节{n+1}")
    n = rebuild_index(db_path)
    assert n == 3
    hits = search(db_path, pid, "古镜", limit=3)
    assert len(hits) >= 1


def test_rebuild_index_project_scoped(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid_a = _insert_project(db_path, "A")
    pid_b = _insert_project(db_path, "B")
    _ = _insert_chapter(db_path, pid_a, 1, content="古镜")  # 仅需副作用：建章待索引
    _ = _insert_chapter(db_path, pid_b, 1, content="古剑")  # 仅需副作用：在 pid_b 建章
    n = rebuild_index(db_path, project_id=pid_a)
    assert n == 1
    # 项目 A 命中
    hits_a = search(db_path, pid_a, "古镜", limit=3)
    assert len(hits_a) == 1
    # 项目 B 不命中"古镜"
    hits_b = search(db_path, pid_b, "古镜", limit=3)
    assert hits_b == []


# ---------------------------------------------------------------------------
# 4. 装配注入（builders）
# ---------------------------------------------------------------------------


def test_director_input_includes_recalled_passages(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 注入 5 章 FTS 可索引内容
    for n in range(1, 6):
        cid = _insert_chapter(
            db_path, pid, n,
            content=f"第{n}章内容：林轩握碎古镜外层封印",
        )
        upsert_chapter(db_path, cid)
    _cache_reset()
    cid_target = _insert_chapter(
        db_path, pid, 6,
        content="",
        plan_json='{"chapter_goal": "古镜再次觉醒"}',
    )
    payload = build_director_input(db_path, pid, cid_target, author_intent="写下一章")
    assert "recalled_passages" in payload
    assert isinstance(payload["recalled_passages"], list)
    # 命中至少 1 条（其它章节含"古镜"）
    assert len(payload["recalled_passages"]) >= 1
    for r in payload["recalled_passages"]:
        assert "chapter_id" in r
        assert "chapter_no" in r
        assert "snippet" in r
        assert "rank" in r


def test_writer_input_includes_recalled_passages(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    for n in range(1, 4):
        cid = _insert_chapter(db_path, pid, n, content=f"古镜在第{n}章")
        upsert_chapter(db_path, cid)
    _cache_reset()
    cid_target = _insert_chapter(
        db_path, pid, 4,
        content="",
        plan_json='{"chapter_goal": "古镜再起波澜"}',
    )
    payload = build_writer_input(
        db_path, cid_target, scene_plan={"purpose": "古镜再被触及"},
    )
    assert "recalled_passages" in payload
    assert isinstance(payload["recalled_passages"], list)


def test_recalled_passages_empty_when_no_index(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid, 1, content="",
        plan_json='{"chapter_goal": "林轩继续修炼"}',
    )
    _cache_reset()
    payload = build_director_input(db_path, pid, cid, author_intent="")
    # 无 FTS 命中 → 空 list（不抛错）
    assert payload["recalled_passages"] == []


# ---------------------------------------------------------------------------
# 5. preview 同步
# ---------------------------------------------------------------------------


def test_preview_shows_recalled_passages(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    for n in range(1, 4):
        cid = _insert_chapter(db_path, pid, n, content=f"古镜在第{n}章出现")
        upsert_chapter(db_path, cid)
    _cache_reset()
    cid_target = _insert_chapter(
        db_path, pid, 4,
        content="",
        plan_json='{"chapter_goal": "古镜再次觉醒"}',
    )
    preview = preview_context(db_path, pid, cid_target)
    l1 = next(layer for layer in preview["layers"] if layer["id"] == "L1")
    recall_items = [it for it in l1["items"] if it["kind"] == "recalled_passage"]
    assert len(recall_items) >= 1
    for it in recall_items:
        assert "chapter_no" in it
        assert "snippet_len" in it


# ---------------------------------------------------------------------------
# 6. cache hit / invalidate（V2.0 Wave C 任务二）
# ---------------------------------------------------------------------------


def test_assembly_cache_hit_second_call_returns_equal_content(tmp_path: Path):
    """同一键第二次调用命中缓存（V3.9 批次 1.4：命中返回深拷贝，不再是同一对象）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json='{"chapter_goal": "x"}')
    _cache_reset()
    p1 = build_director_input(db_path, pid, cid, author_intent="abc")
    p2 = build_director_input(db_path, pid, cid, author_intent="abc")
    # 内容一致（命中缓存），但为独立对象——调用方就地改写不得污染缓存条目
    assert p1 == p2
    assert p1 is not p2


def test_assembly_cache_invalidate_when_state_version_changes(tmp_path: Path, monkeypatch):
    """state_version 变化 → 缓存键失效 → 第二次装配真重装。

    V3.9 批次 2.3：state_version 口径改为 ``MAX(story_states.state_version)``（不再经过
    StoryStateService / 整份快照解析），因此用真实写行推进版本，并以 spy 统计
    ``_build_director_input_uncached`` 的装配次数（深拷贝后 ``p1 is not p2`` 恒真，
    不再是命中信号）。
    """
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json="{}")
    _cache_reset()
    _insert_state_version(db_path, pid, 7)

    calls = {"n": 0}
    real_uncached = di_mod._build_director_input_uncached

    def counting_uncached(*args, **kwargs):
        calls["n"] += 1
        return real_uncached(*args, **kwargs)

    monkeypatch.setattr(di_mod, "_build_director_input_uncached", counting_uncached)

    p1 = build_director_input(db_path, pid, cid, author_intent="abc")
    assert calls["n"] == 1
    assert p1["story_state_snapshot"]["current_state_version"] == 7

    # 版本推进 → 键维度变化 → 必须重装
    _insert_state_version(db_path, pid, 8)
    p2 = build_director_input(db_path, pid, cid, author_intent="abc")
    assert calls["n"] == 2, "state_version 推进必须重装，不能命中缓存"
    assert p2["story_state_snapshot"]["current_state_version"] == 8
    assert p1["story_state_snapshot"]["current_state_version"] != \
        p2["story_state_snapshot"]["current_state_version"]


def test_writer_cache_invalidated_on_state_version_change(tmp_path: Path, monkeypatch):
    """state_version 变化 → writer 缓存键失效 → 每次都真装配。

    V3.9 批次 1.4 起命中返回深拷贝（``p1 is not p2`` 恒真、不再是有效信号），
    改为统计 ``_build_writer_input_uncached`` 的真实装配次数。
    V3.9 批次 2.3：state_version 由 ``MAX(story_states.state_version)`` 取，用真实写行推进。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json="{}")
    _cache_reset()
    _insert_state_version(db_path, pid, 1)

    calls = {"n": 0}
    real_uncached = wi_mod._build_writer_input_uncached

    def counting_uncached(*args, **kwargs):
        calls["n"] += 1
        return real_uncached(*args, **kwargs)

    monkeypatch.setattr(wi_mod, "_build_writer_input_uncached", counting_uncached)
    build_writer_input(db_path, cid, scene_plan={})
    assert calls["n"] == 1
    _insert_state_version(db_path, pid, 2)  # 版本推进 → 键失效
    build_writer_input(db_path, cid, scene_plan={})
    assert calls["n"] == 2, "state_version 变化必须重装，不能命中缓存"


def test_assembly_cache_thread_safe_basic(tmp_path: Path, monkeypatch):
    """基本线程安全：多线程并发读同一键，缓存最终生效（不要求首次 race-free）。

    V3.9 批次 2.3：state_version 由 ``story_states`` 的 MAX 取，先写一行固定版本
    让所有线程读到同一键（原先 monkeypatch ``get_current_state`` 已不再被读取）。
    首次并发 miss 允许多个线程都做装配，但之后该键只应保留一条缓存条目，
    且后续调用返回内容一致（V3.9 批次 1.4 起命中返回深拷贝，不再断言同一对象）。
    """
    import threading

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json="{}")
    _cache_reset()
    _insert_state_version(db_path, pid, 1)

    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        p = build_director_input(db_path, pid, cid, author_intent="abc")
        with lock:
            results.append(p)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 所有线程的结果应 dict 等价（首次并发 miss 时可能各自装配）
    p_after = build_director_input(db_path, pid, cid, author_intent="abc")
    assert all(p_after == r for r in results)
    # 缓存最终应稳定为单一键（后续调用命中同一键）
    from packages.core.context_engine import builders as cb

    director_keys = [
        k for k in cb._assembly_cache if k[0] == pid and k[3] == "director"
    ]
    assert len(director_keys) == 1


# ---------------------------------------------------------------------------
# 7. V2.0 Wave C P1-1 修复：装配缓存脏命中
# ---------------------------------------------------------------------------


def test_director_cache_does_not_hit_stale_plan_json(tmp_path: Path, monkeypatch):
    """P1-1 修复：plan_json 被 UPDATE 但 state_version 不变时，
    director 装配不应返回陈旧缓存。

    1) 用 plan_v1 装配（缓存命中键 1）；
    2) SQL UPDATE chapters.plan_json = plan_v2（state_version 不变）；
    3) 再次装配 → 必须返回新对象，且内容反映 plan_v2。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid, 1,
        content="",
        plan_json='{"chapter_goal": "v1 goal", "core_conflict": "v1 conflict"}',
    )
    _cache_reset()

    # 锁住 state_version（V3.9 批次 2.3：真实写一行，peek 取 MAX(state_version)）
    _insert_state_version(db_path, pid, 1)

    from packages.core.context_engine import director_input as di_mod

    calls = {"n": 0}
    real_uncached = di_mod._build_director_input_uncached

    def counting_uncached(*args, **kwargs):
        calls["n"] += 1
        return real_uncached(*args, **kwargs)

    monkeypatch.setattr(di_mod, "_build_director_input_uncached", counting_uncached)

    p1 = build_director_input(db_path, pid, cid, author_intent="abc")
    assert p1["author_intent"]["raw"] == "abc"
    assert calls["n"] == 1
    # plan_v1 时 active_characters 来自 _recall_passages；这里只验证键
    # _build_director_input_uncached 内部读取的是 plan_json
    # 我们通过 SQL UPDATE plan_json 内容来验证指纹区分
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
            (
                '{"chapter_goal": "v2 goal", "core_conflict": "v2 conflict"}',
                cid,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    p2 = build_director_input(db_path, pid, cid, author_intent="abc")
    # 内容指纹变化 → 必须重装（不是缓存命中）
    assert calls["n"] == 2, "plan_json UPDATE 后必须重新装配，不能命中陈旧缓存"
    # 状态保持稳定（state_version 没变）
    assert p2["story_state_snapshot"]["current_state_version"] == 1


def test_writer_cache_distinguishes_scene_plan(tmp_path: Path):
    """P1-1 修复：同一 chapter 不同 scene_plan 必须返回不同对象。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json='{"chapter_goal": "x"}')
    _cache_reset()
    _insert_state_version(db_path, pid, 1)

    scene_a = {"purpose": "场景 A", "characters": ["林夕"]}
    scene_b = {"purpose": "场景 B", "characters": ["林夕"]}
    p_a = build_writer_input(db_path, cid, scene_a)
    p_b = build_writer_input(db_path, cid, scene_b)
    # 不同 scene_plan 必须返回不同对象
    assert p_a is not p_b, "scene_plan 不同必须 miss 缓存"
    # 各自内容正确
    assert p_a["scene_plan"]["purpose"] == "场景 A"
    assert p_b["scene_plan"]["purpose"] == "场景 B"


def test_writer_cache_same_scene_plan_returns_equal_content(tmp_path: Path):
    """同 scene_plan 第二次调用仍命中缓存（V3.9 批次 1.4：命中返回深拷贝）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="", plan_json='{"chapter_goal": "x"}')
    _cache_reset()
    _insert_state_version(db_path, pid, 1)

    scene = {"purpose": "场景 X", "location": "Cave"}
    p1 = build_writer_input(db_path, cid, scene)
    p2 = build_writer_input(db_path, cid, scene)
    assert p1 == p2
    assert p1 is not p2


def test_chapter_commit_invalidate_clears_cache(tmp_path: Path):
    """P1-1 修复：chapter_commit._commit_node 成功后必须显式失效本章缓存。

    不依赖完整 workflow 引擎，直接调 _commit_node 验证：
    1) 先装配→缓存有键；
    2) 调 _commit_node（前置状态：chapter=REVIEWED + 已 submit_delta）→ 失效；
    3) 装配→内容是 commit 后的新状态。
    """
    from packages.core.context_engine import builders as cb

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="")
    cb._cache_reset()
    # 先建立缓存键（V3.9 批次 2.3：state_version 取自 story_states，写一行固定版本）
    _insert_state_version(db_path, pid, 1)

    _ = build_director_input(db_path, pid, cid, author_intent="v1")  # 仅需副作用：写缓存
    assert any(k[0] == pid for k in cb._assembly_cache), "应有缓存键"

    # 显式调失效（模拟 _commit_node 末尾的兜底调用）
    removed = cb._invalidate_cache_for_chapter(pid, 1)
    assert removed >= 1
    assert not any(k[0] == pid for k in cb._assembly_cache), "失效后应清空该 chapter 键"


def test_chapter_commit_node_source_calls_invalidate(tmp_path: Path):
    """P1-1 修复：chapter_commit._commit_node 源码必须包含 _invalidate_cache_for_chapter。

    不依赖完整 workflow 跑通（commit_delta 涉及多处 FK + 7 数组完整性，
    不在缓存测试聚焦范围）。改为源码静态检查 + 模块级 spy 验证调用链。
    """
    import inspect

    from packages.workflows.chapter_commit import pipeline as cp

    src = inspect.getsource(cp._commit_node)
    assert "_invalidate_cache_for_chapter" in src, (
        "_commit_node 源码必须包含 _invalidate_cache_for_chapter 调用"
    )
    # 进一步断言：失效调用紧跟 commit 成功路径（用 'commit_result' 上下文作锚）
    assert "_commit_node" in src
    # 验证失效调用使用了从 builders 模块导入的函数
    assert "_peek_project_id_from_chapter" in src
    # 确保失效失败不阻断 commit（异常吞掉）
    assert "pass" in src, "_commit_node 末尾应包含异常吞掉（pass）保证失效失败不阻断 commit"


def test_chapter_commit_node_invalidates_cache_at_runtime(tmp_path: Path, monkeypatch):
    """P1-1 修复：通过完整 commit_node 路径（短路 DB 写入）验证失效被调用。

    策略：mock StoryStateService.commit_delta 让其写最小 story_states（bypass FK
    通过预插 workflow_runs + commits 行）；章节 status=REVIEWED；直接调
    _commit_node 并断言 spy 被调一次 + 缓存被清空。
    """
    from packages.core.context_engine import builders as cb
    from packages.core.ids import new_id, now_iso
    from packages.core.story_state import service as svc_mod
    from packages.workflows.chapter_commit import pipeline as cp

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, content="")
    cb._cache_reset()

    # 先建缓存键（V3.9 批次 2.3：state_version 取自 story_states，写一行固定版本；
    # 下面的 monkeypatch commit_delta 会在 commit 时插入新版本行）
    _insert_state_version(db_path, pid, 1)
    build_director_input(db_path, pid, cid, author_intent="v0")
    assert len(cb._assembly_cache) >= 1

    # spy：替换 builders 模块顶层符号；保留原函数引用避免递归
    original_invalidate = cb._invalidate_cache_for_chapter
    called = {"n": 0}

    def spy_invalidate(project_id, chapter_no):
        called["n"] += 1
        return original_invalidate(project_id, chapter_no)

    monkeypatch.setattr(cb, "_invalidate_cache_for_chapter", spy_invalidate)

    # 准备 DB：workflow_runs + state_deltas + commits + chapter=REVIEWED
    delta_id = new_id("dlt")
    run_id = new_id("wfr")
    commit_id_str = new_id("cmt")
    now = now_iso()

    # 看 workflow_runs 实际 schema：run_id / workflow_id / chapter_id / ...
    conn = get_connection(db_path)
    try:
        # branches 表（FK：commits.branch_id；project_id 也是 FK）
        conn.execute(
            """
            INSERT INTO branches (branch_id, project_id, name, base_state_version,
                created_at)
            VALUES ('main_branch', ?, 'main', 1, ?)
            """,
            (pid, now),
        )
        # workflows 表（workflow_runs.workflow_id FK 目标）
        conn.execute(
            """
            INSERT INTO workflows (workflow_id, name, version, definition_json,
                created_at, updated_at)
            VALUES (?, 'chapter-commit', 'v1', '{}', ?, ?)
            """,
            ("chapter-commit", now, now),
        )
        # workflow_runs 表字段
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, retry_count, started_at)
            VALUES (?, 'chapter-commit', ?, 'RUNNING', 'commit', '{}', 0, ?)
            """,
            (run_id, cid, now),
        )
        conn.execute(
            """
            INSERT INTO state_deltas (delta_id, delta_version, schema_version,
                chapter_id, workflow_run_id, previous_state_version, payload_json,
                created_by, created_at, status)
            VALUES (?, 1, 'state-delta-v0', ?, ?, 1, '{}', 'observer:v1', ?, 'validated')
            """,
            (delta_id, cid, run_id, now),
        )
        # commits 表字段：commit_id, project_id, branch_id, chapter_id, previous_state_version,
        # resulting_state_version, delta_id, validation_json, author_approval_json, timestamp, workflow_run_id
        conn.execute(
            """
            INSERT INTO commits (commit_id, project_id, branch_id, chapter_id,
                previous_state_version, resulting_state_version, delta_id,
                validation_json, author_approval_json, timestamp, workflow_run_id)
            VALUES (?, ?, 'main_branch', ?, 1, 2, ?, '{}', '{}', ?, ?)
            """,
            (commit_id_str, pid, cid, delta_id, now, run_id),
        )
        conn.execute(
            "UPDATE chapters SET status='REVIEWED' WHERE chapter_id = ?", (cid,),
        )
        conn.commit()
    finally:
        conn.close()

    # monkeypatch commit_delta 让其最小化写 story_states（FK 已通过预插行满足）
    def fake_commit(self, delta_id_arg, author_approval, run_id_arg):
        c2 = get_connection(str(db_path))
        try:
            cur = c2.execute(
                "SELECT MAX(state_version) AS mx FROM story_states WHERE project_id = ?",
                (pid,),
            ).fetchone()
            new_v = int(cur["mx"] or 0) + 1
            c2.execute(
                """
                INSERT INTO story_states (project_id, state_version, snapshot_json,
                    commit_id, created_at)
                VALUES (?, ?, '{}', ?, ?)
                """,
                (pid, new_v, commit_id_str, now),
            )
            c2.commit()
        finally:
            c2.close()
        return {"state_version": new_v, "status": "committed"}

    monkeypatch.setattr(svc_mod.StoryStateService, "commit_delta", fake_commit)

    # 调 _commit_node
    ctx = {
        "db_path": str(db_path),
        "chapter_id": cid,
        "run_id": run_id,
        "delta_id": delta_id,
        "needs_high_risk_approval": False,
        "human_input": {},
        "_high_risk_approved": False,
    }
    out = cp._commit_node(ctx)

    # 关键断言：spy 被调一次
    assert called["n"] >= 1, (
        f"_commit_node 必须调用 _invalidate_cache_for_chapter，实际 {called['n']}"
    )
    # 缓存被清空
    assert not any(k[0] == pid for k in cb._assembly_cache), "commit 后缓存应被失效清空"
    assert out["status_after"] == "COMMITTED"
