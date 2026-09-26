"""签约体检的章字数带单点来源（修法 A：题材包校准带 ↔ signing_check 口径收敛）。

覆盖：
1. run_checks 带 band：pass（low ≤ chars ≤ high）/ warn（floor ≤ chars < low 或
   chars > high）/ fail（chars < floor）三档 + 文案含校准带与来源指纹；
2. 边界：low/floor 含端点，1299 < floor 为 fail；
3. 无 band（None / 缺键 / 形态非法 / 次序颠倒）→ 平台默认口径**逐字回归**；
4. resolve_chapter_word_band：无绑定 / 无段 / 三键不齐 / 次序颠倒 → None；
   合法 → {low, high, floor, source}；
5. service 级：绑定带校准带的 pack → word_band_source = "<pack_id>@<version>"，
   字数判定走校准带；未绑定 → "default"。

背景：signing_check 平台口径 1200-2600（最佳 1500-2200）与题材包
``pacing.chapter_word_band``（如 2000-3000，floor 1300）对同一章可给出矛盾结论
（1900 字：平台说「区间内」，verifier 说「低于带」）；绑定包时以包口径为准。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.genre.consumers import (
    chapter_word_band,
    normalize_word_band,
    resolve_chapter_word_band,
)
from packages.core.ids import new_id, now_iso
from packages.core.quality.wordcount import visible_chars
from packages.core.signing_check.checks import run_checks
from packages.core.signing_check.service import run_signing_check

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_BAND: dict = {"low": 2000, "high": 3000, "floor": 1300, "source": "gp_wordband@1"}


def _text_of_length(target: int) -> str:
    """构造恰好 target 个可见字符的正文（无空白构造，len == visible_chars）。"""

    unit = "他推开门看见系统面板亮起金光。"
    text = unit * (target // len(unit) + 1)
    trimmed = text[:target]
    assert visible_chars(trimmed) == target
    return trimmed


def _chapters(*lengths: int) -> list[dict]:
    return [
        {"number": i + 1, "text": _text_of_length(n)} for i, n in enumerate(lengths)
    ]


def _length_item(items: list, n: int = 1) -> dict:
    return next(it for it in items if it.key == f"chapter_length_ch{n}")


# ---------------------------------------------------------------------------
# run_checks 带 band：三档判定
# ---------------------------------------------------------------------------


def test_band_pass_inside_low_high():
    # 2800：平台默认口径为 warn（>2600），校准带口径为 pass——证明 band 真的生效。
    item = _length_item(run_checks(_chapters(2800), [], word_band=_BAND))
    assert item.level == "pass"
    assert item.detail == "第1章字数 2800（题材包校准带 2000-3000，gp_wordband@1）"


def test_band_pass_boundaries_are_inclusive():
    assert _length_item(run_checks(_chapters(2000), [], word_band=_BAND)).level == "pass"
    assert _length_item(run_checks(_chapters(3000), [], word_band=_BAND)).level == "pass"


def test_band_warn_below_low_but_above_floor():
    # 1500：平台默认口径为 pass（1200-2600），校准带口径为 warn——口径打架的实证点。
    item = _length_item(run_checks(_chapters(1500), [], word_band=_BAND))
    assert item.level == "warn"
    assert item.detail == "第1章字数 1500（低于题材包校准带下限 2000，gp_wordband@1）"
    assert "题材包校准带 2000-3000" in item.advice


def test_band_warn_above_high():
    item = _length_item(run_checks(_chapters(3200), [], word_band=_BAND))
    assert item.level == "warn"
    assert item.detail == "第1章字数 3200（超题材包校准带上限 3000，gp_wordband@1）"


def test_band_fail_below_floor():
    # 900：平台默认口径为 warn「过短」，校准带口径为 fail（低于 floor 保护）。
    item = _length_item(run_checks(_chapters(900), [], word_band=_BAND))
    assert item.level == "fail"
    assert item.detail == "第1章字数 900（低于题材包校准带保护下限 1300，gp_wordband@1）"
    assert "题材包校准带 2000-3000" in item.advice
    assert "floor 保护 1300" in item.advice


def test_band_floor_boundary_is_warn_not_fail():
    # floor 含端点：chars == floor → warn（保护下限的「低于」是严格小于）。
    assert _length_item(run_checks(_chapters(1300), [], word_band=_BAND)).level == "warn"
    assert _length_item(run_checks(_chapters(1299), [], word_band=_BAND)).level == "fail"


# ---------------------------------------------------------------------------
# 无 band：平台默认口径逐字回归（含显式 None 与非法 band 形态）
# ---------------------------------------------------------------------------


def test_no_band_keeps_legacy_pass_text():
    item = _length_item(run_checks(_chapters(2100), []))
    assert item.level == "pass"
    assert item.detail == "第1章字数 2100（1500-2200 最佳）"


def test_no_band_keeps_legacy_warn_texts():
    too_long = _length_item(run_checks(_chapters(2700), []))
    assert too_long.level == "warn"
    assert too_long.detail == "第1章字数 2700（过长，最佳 1500-2200）"
    too_short = _length_item(run_checks(_chapters(900), []))
    assert too_short.level == "warn"
    assert too_short.detail == "第1章字数 900（过短，最佳 1500-2200）"


def test_explicit_none_band_matches_no_band():
    with_none = run_checks(_chapters(2700), [], word_band=None)
    without_arg = run_checks(_chapters(2700), [])
    assert [it.detail for it in with_none] == [it.detail for it in without_arg]


def test_malformed_band_falls_back_to_default():
    broken_bands = [
        {"low": 2000, "high": 3000},                                  # 缺 floor
        {"low": 3000, "high": 2000, "floor": 1300, "source": "x"},    # 次序颠倒
        {"low": "2000", "high": 3000, "floor": 1300, "source": "x"},  # 非 int
        {"low": True, "high": 3000, "floor": 1300, "source": "x"},    # bool 不算 int
        "not-a-dict",
        None,
    ]
    for band in broken_bands:
        item = _length_item(run_checks(_chapters(2700), [], word_band=band))
        assert item.level == "warn", f"band={band!r} 应回退默认口径"
        assert item.detail == "第1章字数 2700（过长，最佳 1500-2200）"


def test_band_without_source_is_accepted_with_empty_source_label():
    # checks 层宽容：resolve_chapter_word_band 恒附 source，但手工构造的 band 允许缺省
    # （source 空串占位），三键齐全即按校准带判定，且文案不出现空来源尾巴。
    item = _length_item(
        run_checks(_chapters(2800), [], word_band={"low": 2000, "high": 3000, "floor": 1300})
    )
    assert item.level == "pass"
    assert item.detail == "第1章字数 2800（题材包校准带 2000-3000）"


# ---------------------------------------------------------------------------
# resolve_chapter_word_band / normalize_word_band / chapter_word_band
# ---------------------------------------------------------------------------


def test_normalize_word_band_rejects_incomplete_or_disordered():
    assert normalize_word_band(None) is None
    assert normalize_word_band({"low": 2000, "high": 3000}) is None
    assert normalize_word_band({"low": 3000, "high": 2000, "floor": 1300}) is None
    assert normalize_word_band({"low": 2000, "high": 3000, "floor": -1}) is None
    assert normalize_word_band({"low": 0, "high": 3000, "floor": 0}) is None
    assert normalize_word_band({"low": 2000, "high": 3000, "floor": True}) is None
    assert normalize_word_band({"low": 2000, "high": 3000, "floor": 1300}) == {
        "low": 2000, "high": 3000, "floor": 1300,
    }


def test_chapter_word_band_reads_payload_pacing():
    assert chapter_word_band(
        {"pacing": {"chapter_word_band": {"low": 2000, "high": 3000, "floor": 1300}}}
    ) == {"low": 2000, "high": 3000, "floor": 1300}
    assert chapter_word_band({"pacing": {}}) is None
    assert chapter_word_band({"schema_version": "genre-pack.v1.1.0"}) is None
    assert chapter_word_band(None) is None


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


def _bind_pack(db_path: Path, pid: str, payload: dict | None, version: int = 2) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_wordband', '题材包', "
            "'快穿', ?, ?, NULL, ?, ?)",
            (version, json.dumps(payload or {"schema_version": "genre-pack.v1.1.0"},
                                 ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_wordband' WHERE project_id = ?",
            (pid,),
        )
        conn.commit()
    finally:
        conn.close()


_BAND_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "pacing": {"chapter_word_band": {"low": 2000, "high": 3000, "floor": 1300}},
}


def test_resolve_word_band_unbound_or_incomplete_returns_none(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    assert resolve_chapter_word_band(db_path, None) is None
    assert resolve_chapter_word_band("", "prj_x") is None

    pid = _insert_project(db_path)
    assert resolve_chapter_word_band(db_path, pid) is None  # 未绑定

    _bind_pack(db_path, pid, {"schema_version": "genre-pack.v1.1.0"})
    assert resolve_chapter_word_band(db_path, pid) is None  # 无 pacing.chapter_word_band


def test_resolve_word_band_returns_band_with_source_fingerprint(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _bind_pack(db_path, pid, _BAND_PAYLOAD, version=2)

    band = resolve_chapter_word_band(db_path, pid)
    assert band == {"low": 2000, "high": 3000, "floor": 1300, "source": "gp_wordband@2"}


def test_resolve_word_band_rejects_disordered_pack_band(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _bind_pack(
        db_path, pid,
        {"schema_version": "genre-pack.v1.1.0",
         "pacing": {"chapter_word_band": {"low": 3000, "high": 2000, "floor": 1300}}},
    )
    assert resolve_chapter_word_band(db_path, pid) is None


# ---------------------------------------------------------------------------
# service 级：word_band_source 字段 + 字数判定走校准带
# ---------------------------------------------------------------------------


def test_service_reports_band_source_and_applies_band(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, _text_of_length(2800))
    _bind_pack(db_path, pid, _BAND_PAYLOAD, version=2)

    result = run_signing_check(db_path, pid)
    assert result["word_band_source"] == "gp_wordband@2"
    item = next(it for it in result["items"] if it["key"] == "chapter_length_ch1")
    assert item["level"] == "pass"  # 平台口径会判 warn（2800 > 2600），校准带判 pass
    assert "题材包校准带 2000-3000" in item["detail"]


def test_service_reports_default_when_no_band(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, _text_of_length(2800))

    result = run_signing_check(db_path, pid)
    assert result["word_band_source"] == "default"
    item = next(it for it in result["items"] if it["key"] == "chapter_length_ch1")
    assert item["level"] == "warn"
    assert item["detail"] == "第1章字数 2800（过长，最佳 1500-2200）"


def test_service_flags_word_band_rejected_for_dirty_pack_band(tmp_path: Path):
    """绑定包声明了 chapter_word_band 键但被 normalize 拒绝（脏带）→ word_band_rejected。

    2026-09-26 收尾批次：``word_band_source`` 保持 "default"（回退默认口径不变），
    同时报告 ``word_band_rejected: True``——让「包声明了却按不了」对策展人显性化，
    而不是默默按默认口径走。合法带 / 未声明 / 未绑定时该键缺席。
    突变验证：撤 service 的 resolve_chapter_word_band_rejected 接线 → 本用例必红。
    """
    from packages.core.genre.consumers import resolve_chapter_word_band_rejected

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _insert_chapter(db_path, pid, 1, _text_of_length(2800))

    # 脏带：缺 floor（三键不齐）——键在场但被拒
    _bind_pack(
        db_path, pid,
        {"schema_version": "genre-pack.v1.1.0",
         "pacing": {"chapter_word_band": {"low": 2000, "high": 3000}}},
    )
    assert resolve_chapter_word_band_rejected(db_path, pid) is True
    result = run_signing_check(db_path, pid)
    assert result["word_band_source"] == "default"
    assert result.get("word_band_rejected") is True

    # 合法带 → 无 rejected 标注（独立库文件：_fresh_db 固定 test.db 同名同库，
    # _bind_pack 每次 INSERT 同 id 包行会撞 UNIQUE）
    db_path2 = tmp_path / "legal.db"
    apply_migrations(db_path2, MIGRATIONS_DIR)
    pid2 = _insert_project(db_path2)
    _insert_chapter(db_path2, pid2, 1, _text_of_length(2800))
    _bind_pack(db_path2, pid2, _BAND_PAYLOAD, version=2)
    assert resolve_chapter_word_band_rejected(db_path2, pid2) is False
    result2 = run_signing_check(db_path2, pid2)
    assert result2["word_band_source"] == "gp_wordband@2"
    assert "word_band_rejected" not in result2

    # 未声明 / 未绑定 → 无 rejected 标注
    db_path3 = tmp_path / "plain.db"
    apply_migrations(db_path3, MIGRATIONS_DIR)
    pid3 = _insert_project(db_path3)
    _insert_chapter(db_path3, pid3, 1, _text_of_length(2800))
    result3 = run_signing_check(db_path3, pid3)
    assert result3["word_band_source"] == "default"
    assert "word_band_rejected" not in result3
