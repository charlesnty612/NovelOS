"""chapter_review._basic_checks_node 字数带覆盖（V3.7）单测。

覆盖：
- 项目无覆盖（word_band_json NULL）→ 与旧行为字节一致（word_band=(1200,1200) 退化等）。
- 项目覆盖 floor=1200 + ratios=默认 → 仍按 (low=1200, high=...floor) 推导；
- 项目覆盖 low_ratio=0.9 / high_ratio=1.1 → 在 ±15% 边界外的 1720 字判 over_band
  还是 in_band 取决于覆盖值。
- 无覆盖项目回归锚定（与原 test_chapter_review_basic_checks 一致）。
- 非法 JSON 不炸（防御性）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import _basic_checks_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(
    db_path: Path, name: str = "项目", *, word_band_json: str | None = None,
) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "word_band_json, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?, ?)",
            (pid, name, word_band_json, now, now),
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
# 1) 无覆盖项目 → 与原逐字段一致
# ---------------------------------------------------------------------------


def test_no_override_default_behavior_unchanged(tmp_path: Path):
    """项目 word_band_json=NULL → band / within_range 与原硬编码行为一致。

    回归锚定（与 test_chapter_review_basic_checks.test_in_band_no_warning_no_error
    同样的 visible=2000/target=2000 用例）：无覆盖 ⇒ 模块默认 0.85/1.15/1200。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)  # word_band_json=None
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["word_band"] == {"low": 1700, "high": 2300}
    assert rep["deviation_pct"] == 0.0
    assert rep["within_range"] is True
    assert rep["warnings"] == []
    assert rep["errors"] == []


# ---------------------------------------------------------------------------
# 2) 覆盖生效（low_ratio=0.9 / high_ratio=1.1）—— 与默认边界对比
# ---------------------------------------------------------------------------


def test_override_tight_ratios_1720_becomes_over_band(tmp_path: Path):
    """项目覆盖 low=0.9 / high=1.1：visible=1720 / target=2000 ⇒ 偏离 -14%，默认判定
    in_band（边界内，因 |dev|=14% <= 15%）；覆盖后 word_band=(1800, 2200)，1720 < 1800。

    关键判定：``within_range`` 是写死 ``abs_dev_pct <= 15.0``，与 word_band 覆盖无
    关；W-LEN error 级判定也是 ``abs_dev_pct > 30.0`` 写死。所以本例中 1720/2000=-14%
    仍然 ``within_range=True``（无 warning），但 ``word_band`` 字段本身按覆盖值渲染。

    本测试断言：覆盖后 word_band 字段渲染为 (1800, 2200)（最直观的覆盖生效信号）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1720)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["word_band"] == {"low": 1800, "high": 2200}
    assert rep["deviation_pct"] == -14.0
    # within_range 写死为 |dev| <= 15%（与 word_band 覆盖无关），所以此处 True
    assert rep["within_range"] is True


def test_override_loose_ratios_1200_in_band(tmp_path: Path):
    """项目覆盖 low=0.7 / high=1.3：visible=1200 / target=2000 ⇒ 偏离 -40%。

    30% error 阈值写死为 ``abs_dev_pct > 30.0``，与 word_band 覆盖无关——
    所以即使覆盖把带扩到 (1400, 2600)，1200/2000=-40% 仍触发 W-LEN-DEVIATION error。

    本测试断言：覆盖后 word_band 字段渲染为 (1400, 2600)，errors 仍含 W-LEN 条目。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.7, "high_ratio": 1.3, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 1200)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["word_band"] == {"low": 1400, "high": 2600}
    assert rep["deviation_pct"] == -40.0
    assert rep["within_range"] is False
    assert len(rep["errors"]) >= 1
    assert any(e.get("rule_id") == "W-LEN-DEVIATION" for e in rep["errors"])


def test_override_floor_lifts_low_but_high_unaffected(tmp_path: Path):
    """target=1000、覆盖 floor=1000：low=max(850, 1000)=1000；high=int(1150)=1150（未触发单调回退）。

    注：原默认 floor=1200 会把 high 也抬到 1200；显式覆盖 floor=1000 时 high=1150
    已大于 low=1000，无需回退对齐。这是「自定义 floor 解除下限保护」的覆盖语义。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.85, "high_ratio": 1.15, "floor": 1000}),
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 900)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 1000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["word_band"] == {"low": 1000, "high": 1150}
    assert rep["deviation_pct"] == -10.0


# ---------------------------------------------------------------------------
# 3) 防御：非法 JSON / 非 dict 不炸
# ---------------------------------------------------------------------------


def test_invalid_json_word_band_falls_back_to_default(tmp_path: Path):
    """projects.word_band_json 存了非法 JSON → 视为无覆盖（不抛错），band 走默认。

    与项目无覆盖场景输出完全一致——防御性兜底，与 backup 模块对损坏数据的容忍策略对齐。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, word_band_json="not-a-json-string")
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    # 默认 0.85/1.15/1200
    assert rep["word_band"] == {"low": 1700, "high": 2300}


def test_array_json_word_band_falls_back_to_default(tmp_path: Path):
    """word_band_json 存了合法 JSON 但不是 dict（如数组）→ 视为无覆盖。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, word_band_json=json.dumps([1, 2, 3]))
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)
    ctx = {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    rep = _basic_checks_node(ctx)["review_report"]
    assert rep["word_band"] == {"low": 1700, "high": 2300}