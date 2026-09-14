"""章目标字数的题材包解析（题材包驱动章字数 target）。

背景：书1 的 21 章 target 全为 3000（全仓默认常量），正压题材包字数带
2000~3000 的上沿、9/21 章超上限。正确值应由绑定的题材包声明
（``pacing.chapter_words.target``），而非软件写死。

覆盖：
1. 绑定题材包声明 target=2500 → write 侧（``_resolve_target_word_count`` /
   ``_writer_node``）与 review 侧（``basic_checks`` 的 review_report + critic /
   deep_review payload）都解析 2500；
2. 未绑定 → 3000（两侧与改造前一致）；
3. plan / ctx 显式值优先于题材包；
4. 读侧容错（fail-soft）：pacing 缺失 / target 非法 / 章节不存在 → 3000。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.genre.target_words import (
    pack_chapter_words_target,
    resolve_target_word_count,
)
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import (
    _basic_checks_node,
    _critic_review_node,
    _deep_review_node,
)
from packages.workflows.chapter_write import pipeline as cw_pipeline
from packages.workflows.chapter_write.pipeline import _resolve_target_word_count

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PACK_ID = "gp_words"
_PROSE = "他睁开眼，屋顶的木梁压得很低，屋外雨声没停。"


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
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, plan_json: str = "{}") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', ?, 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, plan_json, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, _PROSE, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _bind_pack(db_path: Path, pid: str, payload: dict) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES (?, '男主快穿', '快穿', 1, "
            "?, NULL, ?, ?)",
            (_PACK_ID, json.dumps(payload, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = ? WHERE project_id = ?", (_PACK_ID, pid)
        )
        conn.commit()
    finally:
        conn.close()


def _target_pack(target: object = 2500) -> dict:
    return {
        "schema_version": "genre-pack.v1.1.0",
        "pacing": {"chapter_words": {"min": 2000, "target": target, "max": 3000}},
    }


def _review_ctx(db_path: Path, chapter_id: str) -> dict:
    """review ctx：**不带** target_word_count（题材包应作为解析来源生效）。"""
    return {
        "db_path": db_path,
        "chapter_id": chapter_id,
        "critic_mode": "always",
        "run_id": new_id("run"),
        "_current_node_run_id": new_id("nr"),
    }


def _writer_ctx(db_path: Path, chapter_id: str, loaded_plan: dict | None = None) -> dict:
    return {
        "db_path": db_path,
        "run_id": new_id("run"),
        "chapter_id": chapter_id,
        "scene_plan": {"scenes": []},
        "loaded_plan": loaded_plan or {},
        "mock_providers": {"writer": "{any}"},
        "fresh_write": False,
        "model_overrides": {},
        "_current_node_run_id": new_id("nr"),
    }


def _writer_output_ok() -> dict:
    return {
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v1",
        "chapter_id": "ch_x",
        "prose": "captured prose",
        "self_report": {
            "slots_filled": [],
            "word_count": 6,
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "",
        },
    }


# ---------------------------------------------------------------------------
# 1) 绑定题材包 → write / review 两侧都解析 2500
# ---------------------------------------------------------------------------


def test_write_side_resolves_pack_target(tmp_path: Path):
    """write 侧：绑定包 target=2500 且 plan/ctx 无显式值时 → 2500。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid, _target_pack(2500))

    ctx = {"db_path": db_path, "chapter_id": cid}
    assert _resolve_target_word_count(ctx, {}) == 2500
    # 共享解析入口（review 侧同款调用形态）
    assert resolve_target_word_count(db_path, cid, {}) == 2500


def test_writer_node_payload_uses_pack_target(tmp_path: Path):
    """write 侧端到端：_writer_node 产出的 writer_input 目标字数为 2500。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid, _target_pack(2500))

    with patch.object(cw_pipeline, "run_agent", return_value=_writer_output_ok()):
        out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert out["writer_input"]["chapter"]["target_word_count"] == 2500


def test_review_side_resolves_pack_target(tmp_path: Path):
    """review 侧：basic_checks / critic / deep_review 三处目标字数均 2500。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid, _target_pack(2500))

    ctx = _review_ctx(db_path, cid)
    checks = _basic_checks_node(ctx)
    assert checks["review_report"]["target_word_count"] == 2500
    ctx.update(checks)

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = {
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": "ch_x",
            "overall_comment": "尚可。",
            "strengths": [],
            "issues": [],
        }
        _critic_review_node(ctx)
        assert mock_run.call_args.args[2]["chapter"]["target_word_count"] == 2500

        ctx["deep_review"] = True
        mock_run.return_value = {"schema_version": "deep-review-report.v1", "issues": []}
        assert _deep_review_node(ctx)["deep_review_status"] == "ok"
        assert mock_run.call_args.args[2]["chapter"]["target_word_count"] == 2500


# ---------------------------------------------------------------------------
# 2) 未绑定 → 3000 不变
# ---------------------------------------------------------------------------


def test_unbound_falls_back_to_default_both_sides(tmp_path: Path):
    """未绑定题材包 → write / review 两侧都退回 3000（与改造前一致）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    assert _resolve_target_word_count({"db_path": db_path, "chapter_id": cid}, {}) == 3000
    assert resolve_target_word_count(db_path, cid, {}) == 3000

    ctx = _review_ctx(db_path, cid)
    checks = _basic_checks_node(ctx)
    assert checks["review_report"]["target_word_count"] == 3000
    ctx.update(checks)

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = {
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": "ch_x",
            "overall_comment": "尚可。",
            "strengths": [],
            "issues": [],
        }
        _critic_review_node(ctx)
        assert mock_run.call_args.args[2]["chapter"]["target_word_count"] == 3000


# ---------------------------------------------------------------------------
# 3) 显式值优先于题材包
# ---------------------------------------------------------------------------


def test_explicit_values_beat_pack_target(tmp_path: Path):
    """ctx 覆盖 > plan 显式 > 题材包（包声明 2500 均被压过）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid, _target_pack(2500))

    # ctx 显式覆盖
    assert _resolve_target_word_count(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 1234}, {},
    ) == 1234
    # plan 显式值（expected_word_count / 兼容字段 target_word_count）
    assert _resolve_target_word_count(
        {"db_path": db_path, "chapter_id": cid}, {"expected_word_count": 1800},
    ) == 1800
    assert _resolve_target_word_count(
        {"db_path": db_path, "chapter_id": cid}, {"target_word_count": 1700},
    ) == 1700


def test_review_plan_explicit_beats_pack_target(tmp_path: Path):
    """review basic_checks：plan_json.expected_word_count 优先于题材包。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid, json.dumps({"expected_word_count": 1800}, ensure_ascii=False),
    )
    _bind_pack(db_path, pid, _target_pack(2500))

    report = _basic_checks_node(_review_ctx(db_path, cid))["review_report"]
    assert report["target_word_count"] == 1800


# ---------------------------------------------------------------------------
# 4) 读侧容错（fail-soft）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "genre-pack.v1.1.0"},  # 无 pacing 段
        {"schema_version": "genre-pack.v1.1.0", "pacing": {}},  # 无 chapter_words
        _target_pack(0),  # target 非正
        _target_pack("abc"),  # target 非数
    ],
)
def test_pack_without_usable_target_falls_back(tmp_path: Path, payload: dict):
    """包无可用 target（段缺失 / 非法值）→ 回退 3000，不抛错。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid, payload)

    assert pack_chapter_words_target(db_path, pid) is None
    assert resolve_target_word_count(db_path, cid, {}) == 3000


def test_missing_chapter_and_project_fall_back(tmp_path: Path):
    """章节不存在 / 未给 project_id → None / 3000（fail-soft）。"""
    db_path = _fresh_db(tmp_path)
    assert resolve_target_word_count(db_path, "ch_missing", {}) == 3000
    assert pack_chapter_words_target(db_path, None) is None
    assert pack_chapter_words_target(None, "prj_x") is None
