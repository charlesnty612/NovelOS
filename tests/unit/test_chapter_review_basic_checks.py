"""chapter_review.pipeline._basic_checks_node 字数带硬约束（V3.7）单测。

覆盖点：
- ±15%~±30% 区间：产生 ``[W-LEN-DEVIATION]`` warning，但 errors 为空。
- 超 ±30%（under / over 边界）：同时产生 warning 与 errors 条目；
  errors[0].rule_id = "W-LEN-DEVIATION", severity = "error"。
- 在 band 内：warnings 与 errors 均为空。
- 字数口径统一：visible_chars 折叠空白（与 visible_chars() 一致）。
- word_band 字段、deviation_pct 字段、within_range 字段都被填充。
- 缺 draft 仍按既有契约抛 ValueError。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import _basic_checks_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# DB helpers
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
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, ?, '', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) VALUES "
            "(?, ?, ?, ?, 'test:writer:v1', NULL, NULL, ?)",
            (new_id("drf"), chapter_id, 1, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) 在 band 内 → 无 warning / 无 error
# ---------------------------------------------------------------------------


def test_in_band_no_warning_no_error(tmp_path: Path):
    """visible=2000, target=2000（±0%）→ warnings=[], errors=[]。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    out = _basic_checks_node(ctx)
    rep = out["review_report"]
    assert rep["word_count"] == 2000
    assert rep["within_range"] is True
    assert rep["warnings"] == []
    assert rep["errors"] == []
    assert rep["word_band"] == {"low": 1700, "high": 2300}
    assert rep["deviation_pct"] == 0.0


# ---------------------------------------------------------------------------
# 2) ±15%~±30% → 升级 warning，errors 仍为空
# ---------------------------------------------------------------------------


def test_warning_band_deviation_above_15pct(tmp_path: Path):
    """visible=1720, target=2000（-14%）→ in_band，warnings=[]（边界内）"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1720)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["within_range"] is True
    assert rep["warnings"] == []
    assert rep["errors"] == []


def test_warning_band_deviation_just_over_15pct(tmp_path: Path):
    """visible=1690, target=2000（-15.5%）→ 超 ±15% 边界，warning 升级。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1690)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["within_range"] is False
    assert len(rep["warnings"]) == 1
    msg = rep["warnings"][0]
    assert "[W-LEN-DEVIATION]" in msg
    assert "visible=1690" in msg
    assert "target=2000" in msg
    assert "band 1700~2300" in msg
    # 仍在 ±30% 内 → errors 空
    assert rep["errors"] == []


def test_warning_just_under_30pct_no_error(tmp_path: Path):
    """visible=1420, target=2000（-29%）→ ±15%~±30%，warning 但无 error。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1420)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["within_range"] is False
    assert rep["warnings"] and "[W-LEN-DEVIATION]" in rep["warnings"][0]
    assert rep["errors"] == []


# ---------------------------------------------------------------------------
# 3) 超 ±30% → warning + error 双重
# ---------------------------------------------------------------------------


def test_error_under_30pct_boundary(tmp_path: Path):
    """visible=1390, target=2000（-30.5%）→ 超 -30%，errors 含 W-LEN-DEVIATION error 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1390)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["within_range"] is False
    assert len(rep["errors"]) == 1
    err = rep["errors"][0]
    assert err["rule_id"] == "W-LEN-DEVIATION"
    assert err["severity"] == "error"
    assert err["target"] == 2000
    assert err["visible_chars"] == 1390
    assert err["word_band"] == {"low": 1700, "high": 2300}
    assert err["deviation_pct"] == -30.5
    # warning 也保留
    assert rep["warnings"] and "[W-LEN-DEVIATION]" in rep["warnings"][0]


def test_error_over_30pct_boundary(tmp_path: Path):
    """visible=2610, target=2000（+30.5%）→ 超 +30%，errors 含 W-LEN-DEVIATION error 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2610)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["within_range"] is False
    assert len(rep["errors"]) == 1
    err = rep["errors"][0]
    assert err["rule_id"] == "W-LEN-DEVIATION"
    assert err["severity"] == "error"
    assert err["deviation_pct"] == 30.5


# ---------------------------------------------------------------------------
# 4) 缺 draft：仍按既有契约抛 ValueError
# ---------------------------------------------------------------------------


def test_missing_draft_raises_value_error(tmp_path: Path):
    """无 draft 时节点抛 ValueError（沿用既有契约）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    import pytest

    with pytest.raises(ValueError, match="has no draft"):
        _basic_checks_node(ctx)


# ---------------------------------------------------------------------------
# 5) 字数口径：折叠空白
# ---------------------------------------------------------------------------


def test_visible_chars_folds_whitespace(tmp_path: Path):
    """prose 含大量空白时，word_count 取 visible_chars（折叠后）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 2000 中文字符 + 大量空白
    content = ("中" * 2000) + ("   \n\t\r\n" * 100)
    _insert_draft(db_path, cid, content)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    # word_count 应等于 2000（折叠后），而非 len(content) ≈ 2400
    assert rep["word_count"] == 2000
    assert rep["within_range"] is True


# ---------------------------------------------------------------------------
# 6) 默认 target_word_count 取 ctx 缺省回退到 2200
# ---------------------------------------------------------------------------


def test_default_target_fallback(tmp_path: Path):
    """ctx 不带 target_word_count 时回退到 _DEFAULT_TARGET_WORD_COUNT=2200。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2200)
    ctx = {"db_path": db_path, "chapter_id": cid}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["target_word_count"] == 2200
    assert rep["word_count"] == 2200
    assert rep["within_range"] is True
    # 2200 默认 band：1870~2530
    assert rep["word_band"] == {"low": 1870, "high": 2530}


# ---------------------------------------------------------------------------
# 7) 禁用词仍按既有契约命中 → 仅追加 warning，不入 errors
# ---------------------------------------------------------------------------


def test_forbidden_word_hit_adds_warning(tmp_path: Path):
    """禁用词命中：追加 1 条 warning（与字数无关）。文案补足避开 ±30% 边界。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 在 band 内（target=2000, visible≈1900）→ 不产生 W-LEN-DEVIATION
    filler = "正文" * 950  # 1900 字符
    _insert_draft(db_path, cid, ("仿佛" * 5) + filler)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert any("禁用词命中" in w for w in rep["warnings"])
    assert rep["forbidden_word_hits"] == ["仿佛"]
    assert rep["errors"] == []


# ---------------------------------------------------------------------------
# 8) ±15% / ±30% 边界值（target=1800；band_low=1530, band_high=2070）
# ---------------------------------------------------------------------------


def test_boundary_15pct_just_inside_no_warning(tmp_path: Path):
    """visible=1530, target=1800 → 偏差 -15.0%（abs<=15.0）→ 在 band 内，无 warning。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1530)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1800}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["deviation_pct"] == -15.0
    assert rep["within_range"] is True
    assert rep["warnings"] == []
    assert rep["errors"] == []


def test_boundary_15pct_just_outside_has_warning(tmp_path: Path):
    """visible=1529, target=1800 → 偏差 -15.06%（abs>15.0）→ 升级 warning；未超 ±30%。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1529)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1800}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["deviation_pct"] == -15.1  # round((1529-1800)/1800*100, 1) = -15.06 → -15.1
    assert rep["within_range"] is False
    assert len(rep["warnings"]) == 1
    assert "[W-LEN-DEVIATION]" in rep["warnings"][0]
    assert rep["errors"] == []


def test_boundary_30pct_just_inside_no_error(tmp_path: Path):
    """visible=1260, target=1800 → 偏差 -30.0%（abs<=30.0）→ 有 warning 无 error。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1260)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1800}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["deviation_pct"] == -30.0
    assert rep["within_range"] is False
    assert len(rep["warnings"]) == 1
    assert rep["errors"] == []


def test_boundary_30pct_just_outside_has_error(tmp_path: Path):
    """visible=1259, target=1800 → 偏差 -30.06%（abs>30.0）→ warning + error 双重。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1259)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1800}
    rep = _basic_checks_node(ctx)["review_report"]
    # round((1259-1800)/1800*100, 1) = -30.055... → -30.1
    assert rep["deviation_pct"] == -30.1
    assert rep["within_range"] is False
    assert len(rep["warnings"]) == 1
    assert len(rep["errors"]) == 1
    assert rep["errors"][0]["severity"] == "error"
    assert rep["errors"][0]["rule_id"] == "W-LEN-DEVIATION"


def test_boundary_in_band_center_visible_1700(tmp_path: Path):
    """visible=1700, target=1800 → 偏差 -5.56%（典型 in_band），无任何 warning/error。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1700)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1800}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["deviation_pct"] == -5.6
    assert rep["within_range"] is True
    assert rep["warnings"] == []
    assert rep["errors"] == []

# ---------------------------------------------------------------------------
# V3.8：去 AI 味确定性检测集成
# ---------------------------------------------------------------------------


def test_ai_pattern_hits_field_always_present(tmp_path: Path):
    """即便无 AI 腔，review_report 也含 ai_pattern_hits 字段（可能为空列表）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert "ai_pattern_hits" in rep
    assert rep["ai_pattern_hits"] == []


def test_ai_pattern_forbidden_word_merged_into_report(tmp_path: Path):
    """禁用词命中既保留 forbidden_word_hits，也进入 ai_pattern_hits 与 warnings。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 在 band 内，避免字数 warning 干扰
    filler = "正文" * 950  # 1900 字符
    _insert_draft(db_path, cid, ("仿佛" * 3) + filler)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]

    # 向后兼容字段
    assert rep["forbidden_word_hits"] == ["仿佛"]
    assert any("禁用词命中" in w for w in rep["warnings"])

    # 新增 ai_pattern_hits
    assert "ai_pattern_hits" in rep
    fw_hits = [h for h in rep["ai_pattern_hits"] if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    assert "仿佛" in fw_hits[0]["words"]
    assert any("[AI-FORBIDDEN-WORD]" in w for w in rep["warnings"])
    assert rep["errors"] == []


def test_ai_pattern_ending_summary_and_punct_abuse(tmp_path: Path):
    """章尾总结体 + 破折号滥用均进入 ai_pattern_hits；破折号滥用 severity=error。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 约 200 字正文 + 10 处破折号/省略号，每千字约 50 处
    base = "风吹过山岗，他站起身，望向远方。" * 5
    prose = base + "\n\n这一刻，命运画上了句号。——……"
    _insert_draft(db_path, cid, prose)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 5000}
    rep = _basic_checks_node(ctx)["review_report"]

    rule_ids = {h["rule_id"] for h in rep["ai_pattern_hits"]}
    assert "AI-ENDING-SUMMARY" in rule_ids
    assert "AI-PUNCT-ABUSE" in rule_ids

    pa_hit = next(h for h in rep["ai_pattern_hits"] if h["rule_id"] == "AI-PUNCT-ABUSE")
    assert pa_hit["severity"] == "error"
    assert pa_hit["rule_id"] in {e["rule_id"] for e in rep["errors"]}
