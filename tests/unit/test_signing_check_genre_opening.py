"""签约体检的题材开篇检查段（题材库 P2）。

覆盖：
1. 未绑定题材包 → 无 ``genre_opening`` 键、无题材 items（零行为变化）；
2. 绑定但 payload 无 ``opening_rules`` → 无 ``genre_opening`` 键；
3. 绑定 + 规则：逐条 pass / fail 状态进 ``genre_opening.rules``；
4. 失败为 info 级（summary.fail_count 不因题材规则上升，items 里同样是 info）；
5. 未写到的章 / 语义型规则 → not_written / unverifiable；
6. 题材 items 进 format_summary 文本摘要。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.signing_check.service import format_summary, run_signing_check

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_RULES_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "opening_rules": [
        {
            "check_id": "sys_bind_ch1",
            "description": "系统绑定不得晚于第 1 章",
            "chapter_no": 1,
            "requirement": "第 1 章必须出现系统绑定",
        },
        {
            "check_id": "hook_tail_ch1",
            "description": "第 1 章末留钩子",
            "chapter_no": 1,
            "requirement": "第 1 章必须出现钩子",
        },
        {
            "check_id": "ch3_face_slap",
            "chapter_no": 3,
            "requirement": "第 3 章必须出现打脸",
        },
        {
            "check_id": "soft_requirement",
            "chapter_no": 1,
            "requirement": "主角的动机要可信且层层递进",
        },
    ],
}

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


def _insert_chapter(db_path: Path, pid: str, number: int, content: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _bind_pack(db_path: Path, pid: str, payload: dict | None) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_kc', '男主快穿', '快穿', 1, "
            "?, NULL, ?, ?)",
            (
                json.dumps(payload or {"schema_version": "genre-pack.v1.1.0"}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_kc' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 缺席语义
# ---------------------------------------------------------------------------


def test_unbound_project_has_no_genre_opening_key(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "李晨被人羞辱，系统绑定。「你算什么？」")

    result = run_signing_check(db_path, pid)
    assert "genre_opening" not in result
    assert not [it for it in result["items"] if it["key"].startswith("genre_opening_")]


def test_bound_pack_without_opening_rules_has_no_section(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "李晨被人羞辱，系统绑定。")
    _bind_pack(db_path, pid, {"schema_version": "genre-pack.v1.1.0"})

    result = run_signing_check(db_path, pid)
    assert "genre_opening" not in result


# ---------------------------------------------------------------------------
# 逐条判定
# ---------------------------------------------------------------------------


def test_genre_opening_rules_report_pass_and_fail_statuses(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 第 1 章：有「系统绑定」，无「钩子」
    _insert_chapter(db_path, pid, 1, "系统绑定成功，他睁开眼。")
    _bind_pack(db_path, pid, _RULES_PAYLOAD)

    result = run_signing_check(db_path, pid)
    section = result["genre_opening"]
    assert section["pack_id"] == "gp_kc"
    assert section["pack_name"] == "男主快穿"
    assert section["rule_count"] == 4

    by_id = {r["check_id"]: r for r in section["rules"]}
    assert by_id["sys_bind_ch1"]["status"] == "pass"
    assert by_id["hook_tail_ch1"]["status"] == "fail"
    assert "未命中" in by_id["hook_tail_ch1"]["detail"]
    # 第 3 章未写 → not_written；抽象表述规则照常机检（此处未命中 → fail）
    assert by_id["ch3_face_slap"]["status"] == "not_written"
    assert by_id["soft_requirement"]["status"] == "fail"
    assert section["pass_count"] == 1
    assert section["fail_count"] == 2
    assert section["info_count"] == 1


def test_rule_without_machine_checkable_keywords_is_unverifiable(tmp_path: Path):
    """规则文本抽不出关键词（纯约束语法）→ unverifiable（不猜、不误报失败）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "系统绑定成功，他睁开眼。")
    _bind_pack(
        db_path,
        pid,
        {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [
                {"check_id": "no_keywords", "chapter_no": 1, "requirement": "必须，且需，不少于"}
            ],
        },
    )

    section = run_signing_check(db_path, pid)["genre_opening"]
    rule = section["rules"][0]
    assert rule["status"] == "unverifiable"
    assert "人工核对" in rule["detail"]
    assert section["fail_count"] == 0


def test_failed_genre_rule_is_info_level_not_blocking(tmp_path: Path):
    """失败为 info 级：items 里是 info、summary.fail_count 不因题材规则上升。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "系统绑定成功，他睁开眼。")
    _bind_pack(db_path, pid, _RULES_PAYLOAD)

    result = run_signing_check(db_path, pid)
    items = result["items"]
    passed = [it for it in items if it["key"] == "genre_opening_sys_bind_ch1"]
    failed = [it for it in items if it["key"] == "genre_opening_hook_tail_ch1"]
    assert passed and passed[0]["level"] == "pass"
    assert failed and failed[0]["level"] == "info"
    assert "未满足" in failed[0]["detail"]
    assert "info 级提示" in failed[0]["advice"]
    # 题材规则不产出 fail 级条目（fail_count 只由平台规则贡献）
    assert result["summary"]["fail_count"] == sum(
        1 for it in items if it["level"] == "fail"
    )
    assert all(
        it["level"] != "fail" for it in items if it["key"].startswith("genre_opening_")
    )


def test_genre_opening_items_render_in_format_summary(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "系统绑定成功，他睁开眼。")
    _bind_pack(db_path, pid, _RULES_PAYLOAD)

    text = format_summary(run_signing_check(db_path, pid))
    assert "genre_opening_sys_bind_ch1" in text
    assert "[pass] genre_opening_sys_bind_ch1" in text
    assert "[info] genre_opening_hook_tail_ch1" in text


def test_opening_rule_on_whole_window_uses_any_of_first_three(tmp_path: Path):
    """无 chapter_no → 黄金三章整体窗口：命中任意前 3 章即 pass。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, "第一章平淡。")
    _insert_chapter(db_path, pid, 2, "第二章：打脸全场，众人倒吸。")
    _bind_pack(
        db_path,
        pid,
        {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [
                {"check_id": "window_slap", "requirement": "打脸必须尽早出现"}
            ],
        },
    )

    section = run_signing_check(db_path, pid)["genre_opening"]
    rule = section["rules"][0]
    assert rule["chapter_no"] is None
    assert rule["status"] == "pass"
    assert "第1~3章" in rule["detail"]
