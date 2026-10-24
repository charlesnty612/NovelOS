"""writer 装配缓存键的**键长钉子**（dev-b 批次十九）。

**这是测试侧钉子**（与既有惯例一致）：生产侧 `packages/core/context_engine/` 内
**没有** `*_KEY_LEN` 常量——director 的 `DIRECTOR_KEY_LEN = 18` 同样只存在于
``tests/unit/test_prompt_label_key.py:31``。本文件照该先例给 writer 补一个
``WRITER_KEY_LEN = 23``，让「键长」不再只靠人眼核对。

**为什么需要**：writer 键每加一个内容指纹维度就多一元（world_fp → reveal_fp →
plan_fp → skills_fp → style_fp → title_fp…），而键元数变化**没有任何机器看守** ⇒
「键与 payload 的维度逐字同形」这条硬规则 2 的前提（键里有的维度 == payload 里
消费的权威输入）会悄悄漂移。本文件把当前键长钉住，并同时钉住**键尾两元**
（prompt 标签 / 命名空间标记）与「各内容指纹维度都落在 prompt_label 之前」。

⚠️ **这是「等值」断言的刻意例外**：AGENTS 坑区登记过「断言『恰好 N 处』在正则会
扫到注释时必然自伤」——那条针对的是**源码扫描计数**；本文件断言的是**运行时
元组长度**，不扫源码、不受注释影响 ⇒ 等值断言在这里是安全且必要的。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine import build_writer_input
from packages.core.context_engine.cache import _cache_reset
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

#: writer 缓存键元数。20 → 21 = 2026-10-22 批次十九新增「作者文风样例」维度
#: （``style_fp``）；21 → 22 = 2026-10-22 批次二十一新增「本章标题」维度
#: （``title_fp``，P0-1-B）；22 → 23 = 2026-10-23 新增「所属卷」维度
#: （``volume_scope``，P0-1-C）；23 → 24 = 2026-10-24 新增「前章草稿内容」维度
#: （``prev_draft_fp``，R-2）。历史沿革：19 → 20 = world_fp + reveal_fp（2026-10-21）。
WRITER_KEY_LEN = 24


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "键长项目", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _writer_keys(db_path: Path, pid: str) -> list[tuple]:
    """当前进程缓存里属于该项目的 writer 键（键首元=project_id，键第 4 元=role）。"""
    from packages.core.context_engine import cache as cache_mod

    return [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer"]


def test_writer_cache_key_length_is_pinned(tmp_path):
    """writer 键元数必须等于 ``WRITER_KEY_LEN``（加维度时必须同批改这里）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    build_writer_input(db_path, cid, {})
    keys = _writer_keys(db_path, pid)
    assert len(keys) == 1, f"应有 1 条 writer 键，实得 {len(keys)}"
    assert len(keys[0]) == WRITER_KEY_LEN, (
        f"writer 键应为 {WRITER_KEY_LEN} 元，实得 {len(keys[0])}——"
        f"加/减了内容指纹维度时必须同批更新 WRITER_KEY_LEN"
    )


def test_writer_cache_key_tail_is_prompt_label_and_namespace(tmp_path):
    """键尾两元恒为（prompt 标签 / 命名空间标记）——加维度不得挤掉它们。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    payload = build_writer_input(db_path, cid, {})
    keys = _writer_keys(db_path, pid)
    assert len(keys) == 1
    key = keys[0]
    assert key[-2] == payload["prompt_version"], "倒数第二位恒为 prompt 标签"
    assert key[-1].startswith("ns:"), "键尾恒为命名空间标记"


def test_style_sample_dimension_precedes_prompt_label(tmp_path):
    """「作者文风样例」维度落在 prompt_label 之前（键尾两元语义不变）。

    ⚠️ **行为定位**而不是「按名字找」：键元组里放的是指纹的**值**（16 位 sha256
    前缀），不是变量名 ⇒ 写成 ``assert "style_fp" in key`` 会恒假（量具侧自伤）。
    正解 = 用「加一条样例 ⇒ 键只变一个位置」把该维度的**位置**找出来，再断言
    它落在 prompt_label 之前（与 ``test_skill_injection.py`` 的同款判别口径）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    build_writer_input(db_path, cid, {})
    keys = _writer_keys(db_path, pid)
    assert len(keys) == 1
    before = keys[0]

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO author_style_samples "
            "(sample_id, project_id, title, content, created_at, updated_at) "
            "VALUES (?, ?, '文风样例甲', '短句多。', '2026-10-01T00:00:00', "
            "'2026-10-01T00:00:00')",
            (new_id("asty"), pid),
        )
        conn.commit()
    finally:
        conn.close()

    payload = build_writer_input(db_path, cid, {})
    keys = _writer_keys(db_path, pid)
    assert len(keys) == 2, (
        f"加样例后应多出一条新键（旧键仍在缓存里），实得 {len(keys)} 条"
    )
    after = next(k for k in keys if k != before)

    assert len(before) == len(after) == WRITER_KEY_LEN
    diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diff) == 1, (
        f"加一条样例后键应只变一个维度，实得 {diff} 处：{before} → {after}"
    )
    idx = diff[0]
    assert len(after[idx]) == 16, f"该维度应是 16 位内容指纹，实得 {after[idx]!r}"
    label_idx = after.index(payload["prompt_version"])
    assert idx < label_idx, (
        f"样例维度（位置 {idx}）必须落在 prompt_label（位置 {label_idx}）之前"
    )
