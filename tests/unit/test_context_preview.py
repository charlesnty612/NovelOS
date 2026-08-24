"""preview_context 单元测试（Sprint 13 下半）。

复用 :mod:`tests.unit.test_context_engine` 的 helper 模式：in-memory DB +
``apply_migrations`` + 手工插行；不依赖 HTTP 层。

覆盖：
- 3 层（L0/L1/L2）结构完整；
- L1 items 能抽取 character / location / faction / world_rule / hook / debt / plot_event；
- 有 active canon 时 L1 注入 reference_canon 条目；
- chapter 不存在抛 ``ValueError``；
- **零 LLM 调用**：调用前后 ``ai_call_logs`` 行数不变（证明 dry-run 不触发）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.context_engine import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# Helpers（与 test_context_engine.py 对齐，避免 fixture 跨文件耦合）
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
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path,
    project_id: str,
    number: int = 1,
    title: str = "第一章",
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(db_path: Path, project_id: str, name: str = "主角") -> str:
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role,
                                    core_json, visibility, who_knows,
                                    created_at, updated_at)
            VALUES (?, ?, ?, 'protagonist', '{}', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_location(db_path: Path, project_id: str, name: str = "王城") -> str:
    lid = new_id("loc")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement,
                                   data_json, visibility, who_knows,
                                   created_at, updated_at)
            VALUES (?, ?, ?, 'desc', '{}', 'VISIBLE', NULL, ?, ?)
            """,
            (lid, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return lid


def _insert_faction(db_path: Path, project_id: str, name: str = "帝国") -> str:
    fid = new_id("fac")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO factions (faction_id, project_id, name, statement,
                                  data_json, visibility, who_knows,
                                  created_at, updated_at)
            VALUES (?, ?, ?, 'desc', '{}', 'VISIBLE', NULL, ?, ?)
            """,
            (fid, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return fid


def _insert_world_rule(db_path: Path, project_id: str, name: str = "魔法上限") -> str:
    rid = new_id("wrl")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO world_rules (world_rule_id, project_id, name, statement,
                                     data_json, visibility, who_knows,
                                     created_at, updated_at)
            VALUES (?, ?, ?, 'desc', '{}', 'VISIBLE', NULL, ?, ?)
            """,
            (rid, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _insert_hook(db_path: Path, project_id: str, name: str = "伏笔A") -> str:
    hid = new_id("hk")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hooks (hook_id, project_id, name, status, importance,
                               visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 'OPEN', 0.8, 'VISIBLE', NULL, ?, ?)
            """,
            (hid, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return hid


def _insert_debt(db_path: Path, project_id: str, description: str = "待还债") -> str:
    did = new_id("dbt")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO narrative_debts (debt_id, project_id, description, severity,
                                         status, visibility, who_knows,
                                         created_at, updated_at)
            VALUES (?, ?, ?, 0.7, 'open', 'VISIBLE', NULL, ?, ?)
            """,
            (did, project_id, description, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return did


def _insert_canon(db_path: Path, project_id: str, *, logline: str = "参照书 logline") -> str:
    canon_id = new_id("can")
    canon_json = {
        "logline": logline,
        "spine": [{"chapter_index": 1, "title_pattern": "p1", "function_tag": "hook", "summary_pattern": "s1"}],
        "payoff_list": [{"payoff_id": "p_001", "chapter_index": 1, "type": "face_slap",
                         "intensity": 3, "setup_chapter": 1, "payoff_chapter": 1}],
        "rhythm": {"mini_climax_interval": {"median": 3, "p25": 2, "p75": 5}},
    }
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons
                (canon_id, project_id, title, reader_profile, canon_json,
                 report_md, status, created_at)
            VALUES (?, ?, '参照书', 'male_fantasy', ?, '', 'active', ?)
            """,
            (canon_id, project_id, json.dumps(canon_json, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()
    return canon_id


def _ai_log_count(db_path: Path) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM ai_call_logs").fetchone()
        return int(row["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_preview_returns_three_layers(tmp_path: Path):
    """最小场景：3 层齐全 + token_estimate >= 1。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    out = preview_context(str(db_path), pid, cid)

    assert out["chapter_id"] == cid
    assert out["project_id"] == pid
    assert sorted(out["agents"]) == ["director", "observer", "writer"]
    assert len(out["layers"]) == 3
    ids = [layer["id"] for layer in out["layers"]]
    assert ids == ["L0", "L1", "L2"]
    for layer in out["layers"]:
        assert isinstance(layer["token_estimate"], int)
        assert layer["token_estimate"] >= 1
        assert isinstance(layer["items"], list)
        assert isinstance(layer["truncated"], bool)
    assert out["total_tokens"] >= 3
    assert out["token_budget"] == 8000
    assert isinstance(out["within_budget"], bool)


def test_preview_items_extract_all_kinds(tmp_path: Path):
    """插全套业务实体，断言每种 kind 都被抽取到 L1.items。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    char_id = _insert_character(db_path, pid)
    loc_id = _insert_location(db_path, pid)
    fac_id = _insert_faction(db_path, pid)
    rule_id = _insert_world_rule(db_path, pid)
    hook_id = _insert_hook(db_path, pid)
    debt_id = _insert_debt(db_path, pid)
    canon_id = _insert_canon(db_path, pid, logline="测试参照")

    out = preview_context(str(db_path), pid, cid)

    # L1 应包含全部 7 类条目
    l1 = next(l for l in out["layers"] if l["id"] == "L1")
    kinds = {item["kind"] for item in l1["items"]}
    assert {"character", "location", "faction", "world_rule", "hook", "debt", "reference_canon"} <= kinds

    by_id = {(item["kind"], item["id"]): item for item in l1["items"]}
    assert by_id[("character", char_id)]["name"] == "主角"
    assert by_id[("location", loc_id)]["name"] == "王城"
    assert by_id[("faction", fac_id)]["name"] == "帝国"
    assert by_id[("world_rule", rule_id)]["name"] == "魔法上限"
    assert by_id[("hook", hook_id)]["name"] == "伏笔A"
    assert by_id[("debt", debt_id)]["name"] == "待还债"
    assert by_id[("reference_canon", canon_id)]["name"].startswith("测试参照")


def test_preview_with_canon_only_appears_in_l1(tmp_path: Path):
    """有 canon → L1 含 reference_canon 条目；L0/L2 不含。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_canon(db_path, pid, logline="唯一 canon 标记 x9k22")

    out = preview_context(str(db_path), pid, cid)

    for layer in out["layers"]:
        for item in layer["items"]:
            assert item["kind"] != "reference_canon" or layer["id"] == "L1"
    l1 = next(l for l in out["layers"] if l["id"] == "L1")
    assert any(it["kind"] == "reference_canon" for it in l1["items"])


def test_preview_does_not_write_ai_call_logs(tmp_path: Path):
    """硬断言：调用 preview_context 不触发 LLM，不写 ai_call_logs。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)
    _insert_hook(db_path, pid)

    before = _ai_log_count(db_path)
    out = preview_context(str(db_path), pid, cid)
    after = _ai_log_count(db_path)

    assert out is not None  # 确保调用成功
    assert after == before, (
        f"preview_context 不应触发 ai_call_logs 写入；before={before} after={after}"
    )


def test_preview_raises_value_error_on_missing_chapter(tmp_path: Path):
    """chapter 不存在 → ValueError（router 转 404）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    missing_cid = "ch_does_not_exist"

    with pytest.raises(ValueError, match="not found"):
        preview_context(str(db_path), pid, missing_cid)


def test_preview_total_tokens_equals_sum_of_layers(tmp_path: Path):
    """total_tokens = sum(layers[].token_estimate)。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid)

    out = preview_context(str(db_path), pid, cid)
    expected = sum(layer["token_estimate"] for layer in out["layers"])
    assert out["total_tokens"] == expected
    assert out["within_budget"] == (out["total_tokens"] <= out["token_budget"])