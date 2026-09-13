"""V3.9 批次 2.1：token 预算真兜底（``_assembly_meta`` + 预算参数化）测试。

覆盖：
- ``_estimate_tokens`` 口径 == preview 展示口径（单点提供）；
- ``_assembly_meta`` 三键语义：小 payload 不超标 / 大 payload 超标置位 / 幂等（不算自身）；
- 三个 build_* 入口的 payload 都带 ``_assembly_meta``；
- 超预算**不阻断也不改写**已装配内容（只置标记）；
- preview ``_TOKEN_BUDGET`` 与 ``_assembly_meta.token_budget`` 同源。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.context_engine import preview as preview_mod
from packages.core.context_engine.builders import (
    build_director_input,
    build_observer_input,
    build_writer_input,
)
from packages.core.context_engine.builders_common import (
    _ASSEMBLY_TOKEN_BUDGET,
    _attach_assembly_meta,
    _estimate_tokens,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1, *, plan_json: str = "{}") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, f"第{number}章", plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(
    db_path: Path, project_id: str, name: str, core_json: str = "{}",
    *, inject_mode: str = "auto",
) -> str:
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                    visibility, who_knows, inject_mode, created_at, updated_at)
            VALUES (?, ?, ?, 'supporting', ?, 'VISIBLE', NULL, ?, ?, ?)
            """,
            (cid, project_id, name, core_json, inject_mode, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1. 估算口径
# ---------------------------------------------------------------------------


def test_estimate_tokens_matches_preview_estimator():
    """``_estimate_tokens`` 与 preview ``_token_estimate`` 逐值一致（单点口径）。"""
    for value in ({}, [], {"a": "中文字符串"}, {"k": ["x" * 100]}):
        assert _estimate_tokens(value) == preview_mod._token_estimate(value)
    # 口径：len(json.dumps(ensure_ascii=False)) // 4，至少 1
    payload = {"a": "字" * 10}
    assert _estimate_tokens(payload) == len(json.dumps(payload, ensure_ascii=False)) // 4
    assert _estimate_tokens({}) == 1  # 空对象仍给 1，便于 UI 区分 0 / 1


def test_preview_budget_and_meta_budget_share_one_constant():
    """preview 展示预算与 ``_assembly_meta.token_budget`` 同源。"""
    assert preview_mod._TOKEN_BUDGET == _ASSEMBLY_TOKEN_BUDGET == 8000


# ---------------------------------------------------------------------------
# 2. `_assembly_meta` 语义
# ---------------------------------------------------------------------------


def test_assembly_meta_small_payload_not_exceeded():
    """小 payload → exceeded=False，estimate 与 token_budget 齐备。"""
    payload = {"agent": "writer", "chapter": {"chapter_id": "ch_1"}}

    returned = _attach_assembly_meta(payload)

    assert returned is payload
    meta = payload["_assembly_meta"]
    assert meta["estimate_tokens"] == _estimate_tokens({"agent": "writer", "chapter": {"chapter_id": "ch_1"}})
    assert meta["token_budget"] == _ASSEMBLY_TOKEN_BUDGET
    assert meta["token_budget_exceeded"] is False
    assert "summary_truncated" not in meta  # 非 director 路径不写该键


def test_assembly_meta_large_payload_marks_exceeded_without_rewriting():
    """大 payload → exceeded=True，且**不改写**任何既有键（只加 meta）。"""
    body = {"filler": "x" * (_ASSEMBLY_TOKEN_BUDGET * 4 + 100)}
    payload = dict(body)

    _attach_assembly_meta(payload)

    assert payload["_assembly_meta"]["token_budget_exceeded"] is True
    # 内容零改写：除 meta 外逐字段一致
    assert {k: v for k, v in payload.items() if k != "_assembly_meta"} == body


def test_assembly_meta_idempotent_excludes_itself():
    """重复附加不累计自身体积：第二次的 estimate 与第一次相同。"""
    payload = {"agent": "director", "filler": "y" * 4000}

    _attach_assembly_meta(payload)
    first = payload["_assembly_meta"]["estimate_tokens"]
    _attach_assembly_meta(payload)
    second = payload["_assembly_meta"]["estimate_tokens"]

    assert first == second


def test_assembly_meta_carries_summary_truncated_flag():
    """director 路径可带 ``summary_truncated``（bool 归一化）。"""
    payload = {"a": 1}

    _attach_assembly_meta(payload, summary_truncated=0)

    assert payload["_assembly_meta"]["summary_truncated"] is False


# ---------------------------------------------------------------------------
# 3. 三个 build_* 入口都带 meta
# ---------------------------------------------------------------------------


def test_all_builders_attach_assembly_meta(tmp_path: Path):
    """director / writer / observer 三入口 payload 均带 `_assembly_meta`（小 fixture 不超标）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')

    for payload in (
        build_director_input(db_path, pid, cid, "意图"),
        build_writer_input(db_path, cid, {"purpose": "场景"}),
        build_observer_input(db_path, cid),
    ):
        meta = payload["_assembly_meta"]
        assert meta["token_budget"] == _ASSEMBLY_TOKEN_BUDGET
        assert meta["token_budget_exceeded"] is False
        assert meta["estimate_tokens"] > 0


def test_writer_paged_payload_meta_reflects_trimmed_size(tmp_path: Path):
    """paged 模式：meta 在分页裁剪**之后**重算（estimate ≤ full 模式）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    for i in range(10):
        _insert_character(db_path, pid, f"角色{i}", core_json=json.dumps({"personality": "x" * 200}))

    full = build_writer_input(db_path, cid, {}, context_mode="full")
    paged = build_writer_input(db_path, cid, {}, context_mode="paged")

    assert paged["context_mode"] == "paged"
    # meta 在分页裁剪后**重算**：估算对象是最终 payload 本体（不含 meta 自身），
    # 若沿用 uncached 阶段的旧估算会与之不符（本用例即钉住「不陈旧」）。
    paged_body = {k: v for k, v in paged.items() if k != "_assembly_meta"}
    full_body = {k: v for k, v in full.items() if k != "_assembly_meta"}
    assert paged["_assembly_meta"]["estimate_tokens"] == _estimate_tokens(paged_body)
    assert full["_assembly_meta"]["estimate_tokens"] == _estimate_tokens(full_body)


# ---------------------------------------------------------------------------
# 4. 大 payload 真超标：不阻断装配
# ---------------------------------------------------------------------------


def test_director_oversized_payload_flags_without_blocking(tmp_path: Path):
    """构造超大 director payload → exceeded=True，且装配内容仍完整（不阻断 / 不裁剪）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    # 40 个角色 × ~900 字 core_json ≈ 36000 字符 ≈ 9000 token > 8000
    # （inject_mode='always'：绕过触发降级，保证大头 core_json 真的进 payload）
    for i in range(40):
        _insert_character(
            db_path, pid, f"角色{i}",
            core_json=json.dumps({"personality": "设定" * 450}, ensure_ascii=False),
            inject_mode="always",
        )

    payload = build_director_input(db_path, pid, cid, "意图")

    assert payload["_assembly_meta"]["token_budget_exceeded"] is True
    # 不阻断：40 条角色 excerpt 全在（真裁剪仍靠各字段 cap / 摘要链预算）
    assert len(payload["character_state_excerpts"]) == 40
    assert payload["story_state_snapshot"]["current_state_version"] == 0
