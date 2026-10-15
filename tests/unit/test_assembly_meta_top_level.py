"""装配级 token 预算元字段必须提到 ctx **顶层**（2026-10-15 批次（六）G9）。

为什么这件事需要看守
--------------------
`_assembly_meta`（装配估算 token / 预算 / 是否超预算）此前只**藏在** `writer_input`、
`director_planner_input`、`observer_input` 这三个大容器里。容器进
`workflow_runs.checkpoint_json` 时会过 `_cap_checkpoint_payload`（engine.py），
它按顶层键体积**从大到小**替换超过阈值的键。

实测（临时库 + 40 条 `inject_mode=always` 大角色）：``writer_input`` 装配 payload
约 **227,710 字节**，而 ``_assembly_meta`` 自身只有 **79 字节**。也就是说——

    **预算越超 → writer_input 越大 → meta 越是第一个被整个换成截断标记的那个。**

信号在自己的触发条件下最先消失，这是「装了看不见」的最坏形态。
（同款正确做法就摆在隔壁：``scene_word_budget`` 一直是顶层键。）

本组用例守两件事：

1. **功能**：observer 节点真的把 meta 提到了顶层，且**值与容器内那份一致**
   （只提键、不复制错内容）；
2. **形状**：三个 workflow 文件各自都声明了那个顶层键——任何一处被删都变红。
   这是静态断言，理由与 `test_readability_rules.py` 的「零内联切分」同款：
   成本极低、去掉即红，且它守的正是「别把接线悄悄撤掉」。

⚠️ 这里**不断言**「meta 一定不会被截断」——那要造一个 227KB 的真 run，成本与
不稳定性都不划算（截断阈值见 engine.py 的 `_CHECKPOINT_MIN_TRUNCATE_BYTES`）。
本组只保证「键在顶层、值正确」，剩下的由该常量自身的测试负责。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ruff: noqa: E402  —— import 在 sys.path 注入后必须放在条件块之后
import pytest  # noqa: E402

from packages.core.context_engine.builders import build_observer_input  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"

# 三个「提到顶层」的键：(文件, 顶层键名)
TOP_LEVEL_META_KEYS = [
    ("packages/workflows/chapter_write/pipeline.py", "writer_assembly_meta"),
    ("packages/workflows/chapter_plan/pipeline.py", "director_planner_assembly_meta"),
    ("packages/workflows/chapter_commit/observer.py", "observer_assembly_meta"),
]


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """临时库 + 一章（有 state 快照），供 observer 装配使用。"""
    db_path = tmp_path / "meta.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        pid, cid = new_id("prj"), new_id("ch")
        now = now_iso()
        conn.execute(
            "INSERT INTO projects (project_id, name, created_at, updated_at) VALUES (?,?,?,?)",
            (pid, "元字段", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (cid, pid, 1, "一", "COMMITTED", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_observer_node_lifts_meta_to_top_level(seeded_db: Path):
    """功能：observer 节点产出顶层 ``observer_assembly_meta``，值与容器内那份一致。"""
    from packages.workflows.chapter_commit.observer import _build_observer_ctx_node

    out = _build_observer_ctx_node(
        {"db_path": str(seeded_db), "chapter_id": _only_chapter_id(seeded_db)}
    )

    assert "observer_assembly_meta" in out, (
        "observer 节点没有把装配 meta 提到顶层 ⇒ 超预算信号会随 observer_input "
        "一起被 checkpoint 截断规则替换掉"
    )
    inner = out["observer_input"].get("_assembly_meta")
    assert inner is not None, "夹具前提：容器内那份 meta 应存在"
    assert out["observer_assembly_meta"] == inner, (
        "顶层那份与容器内那份不一致：只应提键，不应改内容"
    )
    for field in ("estimate_tokens", "token_budget", "token_budget_exceeded"):
        assert field in out["observer_assembly_meta"], f"顶层 meta 缺字段 {field}"


def test_observer_meta_matches_a_direct_build(seeded_db: Path):
    """顶层 meta 的取值与直接调 ``build_observer_input`` 得到的完全一致。

    防「提键时重新算了一遍」——两次估算口径不同的话，run 记录里的数字会与
    实际装配用的那份对不上，而两边都自洽、谁也不会报错。
    """
    from packages.workflows.chapter_commit.observer import _build_observer_ctx_node

    chapter_id = _only_chapter_id(seeded_db)
    out = _build_observer_ctx_node({"db_path": str(seeded_db), "chapter_id": chapter_id})
    direct = build_observer_input(str(seeded_db), chapter_id, snapshot_mode="trimmed")

    assert out["observer_assembly_meta"] == direct["_assembly_meta"]


def test_each_workflow_declares_its_top_level_meta_key():
    """形状：三处接线都在（撤掉任一处即红）。

    静态断言，成本极低——它守的不是「值对不对」（功能用例已覆盖），而是
    「接线还在不在」，而后者正是本批要防的那种无声回退。
    """
    missing = []
    for rel, key in TOP_LEVEL_META_KEYS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        # 顶层 ctx 键的声明形态：return dict 里出现 "key":
        if f'"{key}":' not in src:
            missing.append(f"{rel} :: {key}")
    assert not missing, f"以下顶层 meta 键不见了（接线被撤掉？）: {missing}"


def _only_chapter_id(db_path: Path) -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT chapter_id FROM chapters LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    return row["chapter_id"]
