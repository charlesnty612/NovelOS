"""chapter_review 的题材 P2 消费：genre_rubric 段 + 题材禁词扫描。

覆盖：
1. critic payload：绑定时含 `genre_rubric`（文本化 payoff_focus + taboo/style）；
   未绑定 / 未声明 critic_rubric → 无该键（零行为变化）；
2. deep_review payload 同样可得 `genre_rubric`；
3. 预算截断：超大 rubric → payload 内带 `__genre_rubric_truncated__` 且不超过预算；
4. basic_checks 禁词扫描：命中 → `[GENRE-FORBIDDEN-WORD] <词> ×N` 进 warnings；
   未命中 / 未声明 → 零输出；
5. 题材禁词命中不产生 errors（warning 级，不阻断）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from packages.core.db import apply_migrations, get_connection
from packages.core.genre.consumers import CRITIC_RUBRIC_MAX_CHARS, CRITIC_RUBRIC_TRUNCATED_KEY
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import (
    _basic_checks_node,
    _critic_review_node,
    _deep_review_node,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PACK_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "verify_hint": "打脸后至少三人当场反应",
        }
    ],
    "critic_rubric": {
        "payoff_focus": ["face_slap"],
        "taboo_notes": "不得出现具体平台名",
        "style_notes": "短句为主，对话推进",
    },
    "style_constraints": {"forbidden_words": ["命运的齿轮"]},
}

_PROSE = "他睁开眼。命运的齿轮开始转动。命运的齿轮，又一次转动了起来。"


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


def _insert_chapter(db_path: Path, pid: str, prose: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, prose, now),
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
            "source_path, created_at, updated_at) VALUES ('gp_kc', '男主快穿', '快穿', 1, "
            "?, NULL, ?, ?)",
            (json.dumps(payload, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_kc' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()


def _ctx(db_path: Path, chapter_id: str) -> dict:
    return {
        "db_path": db_path,
        "chapter_id": chapter_id,
        "target_word_count": 2000,
        "critic_mode": "always",
        "run_id": new_id("run"),
        "_current_node_run_id": new_id("nr"),
    }


def _critic_output_ok() -> dict:
    return {
        "schema_version": "critic-report.v1",
        "prompt_version": "critic:v1",
        "chapter_id": "ch_x",
        "overall_comment": "整体尚可。",
        "strengths": [],
        "issues": [],
    }


# ---------------------------------------------------------------------------
# critic payload：genre_rubric
# ---------------------------------------------------------------------------


def test_critic_payload_includes_genre_rubric_when_bound(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)
    _bind_pack(db_path, pid, _PACK_PAYLOAD)

    ctx = _ctx(db_path, cid)
    ctx.update(_basic_checks_node(ctx))

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)

        payload = mock_run.call_args.args[2]
        rubric = payload["genre_rubric"]
        assert rubric["taboo_notes"] == "不得出现具体平台名"
        assert rubric["style_notes"] == "短句为主，对话推进"
        assert rubric["payoff_focus"][0].startswith("face_slap：打脸")
        assert "核销提示 打脸后至少三人当场反应" in rubric["payoff_focus"][0]
        assert CRITIC_RUBRIC_TRUNCATED_KEY not in rubric
        assert len(json.dumps(rubric, ensure_ascii=False)) <= CRITIC_RUBRIC_MAX_CHARS


def test_critic_payload_omits_genre_rubric_when_unbound(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)

    ctx = _ctx(db_path, cid)
    ctx.update(_basic_checks_node(ctx))

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)
        payload = mock_run.call_args.args[2]
        assert "genre_rubric" not in payload


def test_critic_payload_omits_genre_rubric_without_critic_section(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)
    # 绑定题材包但未声明 critic_rubric → 不注入
    _bind_pack(
        db_path,
        pid,
        {"schema_version": "genre-pack.v1.1.0", "payout_types": []},
    )

    ctx = _ctx(db_path, cid)
    ctx.update(_basic_checks_node(ctx))

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)
        assert "genre_rubric" not in mock_run.call_args.args[2]


def test_critic_payload_genre_rubric_carries_truncation_marker(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)
    _bind_pack(
        db_path,
        pid,
        {
            "schema_version": "genre-pack.v1.1.0",
            "critic_rubric": {
                "payoff_focus": [f"type_{i:03d}" for i in range(80)],
                "taboo_notes": "禁" * 300,
                "style_notes": "风" * 300,
            },
        },
    )

    ctx = _ctx(db_path, cid)
    ctx.update(_basic_checks_node(ctx))

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)
        rubric = mock_run.call_args.args[2]["genre_rubric"]
        assert rubric[CRITIC_RUBRIC_TRUNCATED_KEY] is True
        assert len(json.dumps(rubric, ensure_ascii=False)) <= CRITIC_RUBRIC_MAX_CHARS


def test_deep_review_payload_includes_genre_rubric(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)
    _bind_pack(db_path, pid, _PACK_PAYLOAD)

    ctx = _ctx(db_path, cid)
    ctx["deep_review"] = True
    ctx.update(_basic_checks_node(ctx))

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = {"schema_version": "deep-review-report.v1", "issues": []}
        result = _deep_review_node(ctx)
        assert result["deep_review_status"] == "ok"
        payload = mock_run.call_args.args[2]
        assert payload["genre_rubric"]["payoff_focus"][0].startswith("face_slap：打脸")


# ---------------------------------------------------------------------------
# basic_checks：题材禁词扫描
# ---------------------------------------------------------------------------


def test_genre_forbidden_word_hits_go_to_warnings(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE + "，屋外的雨声一直没有停。" * 200)
    _bind_pack(db_path, pid, _PACK_PAYLOAD)

    report = _basic_checks_node(_ctx(db_path, cid))["review_report"]
    assert "[GENRE-FORBIDDEN-WORD] 命运的齿轮 ×2" in report["warnings"]
    # warning 级：不进 errors、不阻断
    assert report["errors"] == []
    assert all("GENRE-FORBIDDEN-WORD" not in str(e) for e in report["errors"])


def test_genre_forbidden_word_miss_has_no_warning(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, "他睁开眼，屋顶的木梁压得很低。")
    _bind_pack(db_path, pid, _PACK_PAYLOAD)

    report = _basic_checks_node(_ctx(db_path, cid))["review_report"]
    assert not [w for w in report["warnings"] if "GENRE-FORBIDDEN-WORD" in w]


def test_no_forbidden_words_declared_has_no_warning(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, _PROSE)
    _bind_pack(
        db_path,
        pid,
        {
            "schema_version": "genre-pack.v1.1.0",
            "payoff_types": [{"type_id": "face_slap", "name": "打脸"}],
        },
    )

    report = _basic_checks_node(_ctx(db_path, cid))["review_report"]
    assert not [w for w in report["warnings"] if "GENRE-FORBIDDEN-WORD" in w]
