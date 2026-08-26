"""QualityService.save_judge_score (V3.1 P1-2) 服务层单元测试。

覆盖：
- 正常路径：四维分 + 扩展字段写入 judge_json，再读最新 report 时 ``judge`` 为 dict；
- 缺四维分键 → ValueError；
- 四维分类型非法 → ValueError；
- 浮点型整数（如 72.0）允许；
- 找不到该 chapter 的 quality_report → ValueError；
- 与七子分 / overall 完全解耦：写入 judge 不改 overall / scores_json / formula_hash。

不依赖 FastAPI 客户端；直接用 ``packages.core.db.apply_migrations`` + 直连 DB 准备
最小 chapter + report 上下文（与 ``tests/api/test_quality.py`` 同源，但不需要
ASGI）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.models import QualityReport
from packages.core.quality.service import (
    JUDGE_REQUIRED_SCORE_KEYS,
    QualityService,
)


def _bootstrap_db(tmp_path: Path) -> tuple[Path, str, str]:
    """建库 + 插一个 project + 一个 chapter；返回 (db_path, project_id, chapter_id)。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path)
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "judge-test", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "plan_json, created_at, updated_at) VALUES (?, ?, 1, 'x', 'COMMITTED', "
            "'{}', ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path, pid, cid


def _seed_one_report(db_path: Path, project_id: str, chapter_id: str, *, overall: int = 60) -> str:
    """写入一份最小 QualityReport（绕过 engine，直接调 save_report）。"""
    report = QualityReport(
        overall=overall,
        plot=60,
        character=60,
        continuity=60,
        style=60,
        pacing=60,
        foreshadowing=60,
        ai_trace=60,
        issues=[],
    )
    report.report_id = new_id("qr")
    report.chapter_id = chapter_id
    report.chapter_number = 1
    QualityService(db_path).save_report(
        report,
        project_id=project_id,  # 必须与 chapters.project_id 对齐，否则 FK 违反
        chapter_id=chapter_id,
    )
    return report.report_id


# ------------------------------------------------------------ 正常路径


def test_save_judge_score_writes_and_reads(tmp_path: Path):
    db_path, pid, cid = _bootstrap_db(tmp_path)
    rid = _seed_one_report(db_path, pid, cid)

    payload = {
        "pacing": 72, "style": 78, "logic": 70, "dialogue": 65,
        "verdict": "节奏平稳，开场铺设完整，对话偏少。",
        "top_issues": ["冲突未明确给出", "对话占比偏低", "悬念铺设可加强"],
        "score_avg": 71.25,
        "usage": {"prompt": 200, "completion": 80, "total": 280},
        "dry_run": False,
    }
    out_rid = QualityService(db_path).save_judge_score(cid, payload)
    assert out_rid == rid

    row = QualityService(db_path).latest_report(cid)
    assert row is not None
    # judge_json 在 service 层 _row_to_dict 解析为 dict
    judge = row.get("judge_json")
    assert isinstance(judge, dict)
    # 四维分字段被写入
    for k in ("pacing", "style", "logic", "dialogue"):
        assert judge[k] == payload[k]
    assert judge["verdict"] == payload["verdict"]
    assert judge["top_issues"] == payload["top_issues"]
    # scores_json 完全不变（双轨：judge 不影响七子分）
    scores = row["scores_json"]
    assert isinstance(scores, dict)
    assert scores.get("plot") == 60
    assert row["overall"] == 60


def test_save_judge_score_accepts_float_integers(tmp_path: Path):
    """`72.0` 这类 JSON-序列化的浮点整数允许通过（与 m2_judge.py 口径一致）。"""
    db_path, pid, cid = _bootstrap_db(tmp_path)
    _seed_one_report(db_path, pid, cid)

    payload = {
        "pacing": 80.0, "style": 75.0, "logic": 60.0, "dialogue": 55.0,
    }
    rid = QualityService(db_path).save_judge_score(cid, payload)
    assert rid
    row = QualityService(db_path).latest_report(cid)
    judge = row["judge_json"]
    # 被清洗成整数
    assert judge["pacing"] == 80 and isinstance(judge["pacing"], int)
    assert judge["dialogue"] == 55


# ------------------------------------------------------------ 错误路径


@pytest.mark.parametrize(
    "missing_payload",
    [
        {"style": 70, "logic": 60, "dialogue": 50},          # 缺 pacing
        {"pacing": 70, "logic": 60, "dialogue": 50},         # 缺 style
        {"pacing": 70, "style": 60, "dialogue": 50},         # 缺 logic
        {"pacing": 70, "style": 60, "logic": 50},            # 缺 dialogue
        {},                                                  # 全缺
    ],
)
def test_save_judge_score_rejects_missing_score_keys(tmp_path: Path, missing_payload):
    db_path, pid, cid = _bootstrap_db(tmp_path)
    _seed_one_report(db_path, pid, cid)

    with pytest.raises(ValueError, match="missing required score keys"):
        QualityService(db_path).save_judge_score(cid, missing_payload)


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"pacing": "高", "style": 70, "logic": 60, "dialogue": 50},  # 字符串
        {"pacing": None, "style": 70, "logic": 60, "dialogue": 50},  # None
        {"pacing": True, "style": 70, "logic": 60, "dialogue": 50},  # bool
        {"pacing": 70.5, "style": 70, "logic": 60, "dialogue": 50},  # 真·浮点（非整）
    ],
)
def test_save_judge_score_rejects_invalid_score_types(tmp_path: Path, bad_payload):
    db_path, pid, cid = _bootstrap_db(tmp_path)
    _seed_one_report(db_path, pid, cid)

    with pytest.raises(ValueError, match="must be int 0-100"):
        QualityService(db_path).save_judge_score(cid, bad_payload)


def test_save_judge_score_rejects_non_dict(tmp_path: Path):
    db_path, pid, cid = _bootstrap_db(tmp_path)
    _seed_one_report(db_path, pid, cid)

    with pytest.raises(ValueError, match="must be dict"):
        QualityService(db_path).save_judge_score(cid, [1, 2, 3])  # type: ignore[arg-type]


def test_save_judge_score_raises_when_no_report(tmp_path: Path):
    """该 chapter 还没有 quality_report 时拒绝（与 QualityService 行为一致）。"""
    db_path, _pid, cid = _bootstrap_db(tmp_path)

    payload = {
        "pacing": 80, "style": 75, "logic": 60, "dialogue": 55,
    }
    with pytest.raises(ValueError, match="no quality report"):
        QualityService(db_path).save_judge_score(cid, payload)


# ------------------------------------------------------------ 元数据


def test_constants_match_m2_judge_expected_keys():
    """保证 JUDGE_REQUIRED_SCORE_KEYS 与 m2_judge.SCORE_KEYS 口径一致。

    - 服务层只校验最小集合（pacing/style/logic/dialogue）；
    - m2_judge 脚本另需 verdict/top_issues；这里仅锁住四维分一致即可。
    """
    expected = ("pacing", "style", "logic", "dialogue")
    assert JUDGE_REQUIRED_SCORE_KEYS == expected
