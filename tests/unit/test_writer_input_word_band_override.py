"""build_writer_input 项目级字数带覆盖（V3.7）单测。

覆盖：
- 项目无覆盖（word_band_json=NULL）→ payload.chapter.word_band 与旧默认一致
  （(1700, 2300) for target=2000）——回归锚定零行为变化。
- 项目覆盖 low_ratio=0.9 / high_ratio=1.1 → payload.chapter.word_band = (1800, 2200)。
- 覆盖 floor=1500（小 target）→ floor 生效；带单调性正确处理 low > high 的退化。
- 非法 word_band_json（坏 JSON / 非 dict）→ 视为无覆盖，不抛。
- 缓存键 wb_fp：覆盖变更后第二次调用返回新 payload（缓存不命中陈旧条目）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "off"

from packages.core.context_engine.builders import (  # noqa: E402
    _cache_reset,
    build_writer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(
    db_path: Path, *, word_band_json: str | None = None,
) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status,
                                  word_band_json, created_at, updated_at)
            VALUES (?, 'p', NULL, NULL, NULL, 'ACTIVE', ?, ?, ?)
            """,
            (pid, word_band_json, now, now),
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
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 't', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _set_project_word_band_json(db_path: Path, project_id: str, raw: str | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET word_band_json = ?, updated_at = ? WHERE project_id = ?",
            (raw, now_iso(), project_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) 无覆盖 → 与旧默认一致（回归锚定零行为变化）
# ---------------------------------------------------------------------------


def test_no_override_default_word_band(tmp_path: Path):
    """项目 word_band_json=NULL → payload.chapter.word_band = (1700, 2300)。

    与 V3.7 之前 ``word_band(target_word_count)`` 默认调用逐字段一致。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)  # word_band_json=None
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload["chapter"]["word_band"] == {"low": 1700, "high": 2300}


# ---------------------------------------------------------------------------
# 2) 覆盖生效
# ---------------------------------------------------------------------------


def test_override_tight_ratios_payload(tmp_path: Path):
    """项目覆盖 low_ratio=0.9 / high_ratio=1.1 → payload.chapter.word_band = (1800, 2200)。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload["chapter"]["word_band"] == {"low": 1800, "high": 2200}


def test_override_floor_small_target(tmp_path: Path):
    """小 target=1000 + floor=1000：低带从 850 被 floor 抬到 1000；高带 1150 > low，
    无需 word_band 内部把高回退对齐（默认 floor=1200 会触发）。

    这是「自定义 floor 解除下限保护」的覆盖语义。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.85, "high_ratio": 1.15, "floor": 1000}),
    )
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=1000,
    )
    assert payload["chapter"]["word_band"] == {"low": 1000, "high": 1150}


# ---------------------------------------------------------------------------
# 3) 防御：非法 JSON / 非 dict → 视为无覆盖
# ---------------------------------------------------------------------------


def test_invalid_json_word_band_falls_back_to_default(tmp_path: Path):
    """projects.word_band_json 存了非法 JSON → payload word_band 走默认。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, word_band_json="not-a-json")
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload["chapter"]["word_band"] == {"low": 1700, "high": 2300}


def test_array_json_word_band_falls_back_to_default(tmp_path: Path):
    """JSON 是数组（非 dict）→ 视为无覆盖。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, word_band_json=json.dumps([1, 2]))
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload["chapter"]["word_band"] == {"low": 1700, "high": 2300}


# ---------------------------------------------------------------------------
# 4) 缓存键 wb_fp：覆盖变更后第二次调用返回新 payload
# ---------------------------------------------------------------------------


def test_cache_key_includes_wb_fp_invalidates_on_change(tmp_path: Path):
    """同一 chapter 两次调用，第二次先把 word_band_json 改为覆盖：payload.word_band
    必须反映新覆盖（不能命中无覆盖的陈旧缓存条目）。

    wb_fp 是缓存键第 8 元（pre=V3.7）：覆盖变更后键变 → 缓存命中失效 → 重新装配。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)  # 无覆盖
    cid = _insert_chapter(db_path, pid)

    payload_a = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload_a["chapter"]["word_band"] == {"low": 1700, "high": 2300}

    # 改为覆盖
    _set_project_word_band_json(
        db_path, pid,
        json.dumps({"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1200}),
    )

    payload_b = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000,
    )
    assert payload_b["chapter"]["word_band"] == {"low": 1800, "high": 2200}
    # 两条 payload 字数带不一致 ⇒ 缓存键确实捕获了 wb_fp（脏命中防控生效）


# ---------------------------------------------------------------------------
# 5) paged 模式覆盖同样生效
# ---------------------------------------------------------------------------


def test_paged_mode_respects_word_band_override(tmp_path: Path):
    """context_mode='paged' 走 _build_writer_input_paged → uncached 路径同样装配
    payload.chapter.word_band → 覆盖值生效。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.95, "high_ratio": 1.05, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid)

    payload = build_writer_input(
        db_path, cid, scene_plan={"purpose": "setup"},
        target_word_count=2000, context_mode="paged",
    )
    # 1900 / 2100
    assert payload["chapter"]["word_band"] == {"low": 1900, "high": 2100}