"""装配指纹**真的共用一条连接**（dev-b 批次二十三 · 热路径）。

背景
----
AGENTS 坑区登记：「``chars_fp`` 单独一项就是 **+4.05ms / +64%**，且 **85% 的增量是
多开了一次连接**、不是多查了一次数据」。批次二十三把指纹查询**合并进 peek 已打开的
那条连接**（六个指纹函数全部接受**可选 ``conn``；``None`` ⇒ 保持旧行为自建连）。

⚠️ **「传参了但没用」不会被发现**——所以本文件不测「签名有没有 conn」，而测
**副作用**：装配时到底开了几条连接。

三条判别
--------
1. **writer 装配（命中路径）⇒ 建连 ≤ 2 条**（一条贯穿 peek + 五个指纹；另一条是
   ``active_prompt_label`` 读 ``prompts`` 表，**不属于指纹**）；
2. **director 装配（命中路径）⇒ 建连 ≤ 2 条**（同上）；
3. **反向不变量（负对照）**：不传 ``conn`` 直接调指纹函数 ⇒ **每次调用各开一条
   连接**（旧行为保留 = API 兼容）⇒ 同时证明**计数器是灵的**（否则第 1/2 条可能
   恒真而毫无意义）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.context_engine import cache as cache_mod
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

PID = "prj_perf"
CID = "ch_perf"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, '项目', '前提', '科幻', NULL, "
            "'ACTIVE', ?, ?)", (PID, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, '章', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (CID, PID, now, now),
        )
        for i in range(12):
            conn.execute(
                "INSERT INTO characters (character_id, project_id, name, role, "
                "core_json, visibility, who_knows, created_at, updated_at) "
                "VALUES (?, ?, ?, 'supporting', '{}', 'VISIBLE', NULL, ?, ?)",
                (f"char_{i:02d}", PID, f"角色{i}", now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _count_connections(fn) -> int:
    """跑 ``fn`` 并返回它**开了几条连接**。

    ⚠️ 判别面**按全集推导、不手挑名单**（AGENTS 坑区「看守自己被悄悄缩小了执行
    域」）：各模块用 ``from packages.core.db import get_connection`` 各自持有一份
    引用 ⇒ 只补「我记得的」那几个模块会**漏数**。⇒ 遍历 ``sys.modules``，把所有
    指向同一原函数的属性一并替换。
    """
    import sys

    counter = {"n": 0}
    original = get_connection

    def counting(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            attrs = vars(mod)
        except TypeError:
            continue
        for name, value in list(attrs.items()):
            # 比较**原始函数对象**（不能比较 db.get_connection——它自己会被替换掉）
            if name == "get_connection" and value is original:
                attrs[name] = counting
    fn()
    return counter["n"]


def test_writer_assembly_uses_at_most_two_connections(tmp_path):
    """writer 装配（命中路径）⇒ 建连 **≤ 2 条**（改造前是 7 条）。"""
    db_path = _fresh_db(tmp_path)
    cache_mod._cache_reset()
    build_writer_input(db_path, CID, {})  # 预热：把缓存条目建起来

    n = _count_connections(lambda: build_writer_input(db_path, CID, {}))
    assert n <= 2, (
        f"writer 装配应只开 1~2 条连接（一条贯穿 peek + 五个指纹），实得 {n} 条"
    )


def test_director_assembly_uses_at_most_two_connections(tmp_path):
    """director 装配（命中路径）⇒ 建连 **≤ 2 条**（改造前是 5 条）。"""
    db_path = _fresh_db(tmp_path)
    cache_mod._cache_reset()
    build_director_input(db_path, PID, CID, "意图")

    n = _count_connections(lambda: build_director_input(db_path, PID, CID, "意图"))
    assert n <= 2, (
        f"director 装配应只开 1~2 条连接，实得 {n} 条"
    )


def test_fingerprint_functions_without_conn_still_open_their_own(tmp_path):
    """**负对照**：不传 ``conn`` ⇒ 每次调用各开一条连接（旧行为保留 = API 兼容）。

    这条同时证明**计数器是灵的**——否则前两条可能恒真而毫无判别力。
    """
    db_path = _fresh_db(tmp_path)
    n = _count_connections(
        lambda: (
            cache_mod.characters_fingerprint(db_path, PID),
            cache_mod.world_fingerprint(db_path, PID),
            cache_mod.reveal_policies_fingerprint(db_path, PID),
            cache_mod.author_style_samples_fingerprint(db_path, PID),
            cache_mod.project_meta_fingerprint(db_path, PID),
            cache_mod.chapter_title_fingerprint(db_path, CID),
        )
    )
    assert n == 6, (
        f"不传 conn 时六个指纹函数应各开一条连接（= 6），实得 {n} 条——"
        f"若这里也变小，说明计数器没在数（负对照失效）"
    )
