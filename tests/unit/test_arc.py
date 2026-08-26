"""叙事弧光视图——纯函数单元测试。

覆盖：
- ``_parse_beat_flags`` 三态容错（list[dict] / list[str] / 缺失）；
- ``_count_charge_streaks`` 多种边界（含 payoff+charge 同章、纯 charge 章序、
  尾部连击、历史最长）；
- ``_payoff_ratio_recent`` 窗口 / 章数不足；
- ``_compute_alerts`` 三规则正反例 + pacing 合并告警；
- ``build_arc_view`` 数据库无 quality_report / 无 draft / 无 chapters 的容错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.arc import service as arc_service
from packages.core.arc.service import (
    _CHARGE_STREAK_FAIL,
    _PACING_FAIL_THRESHOLD,
    _PAYOFF_DENSITY_MIN_RATIO,
    _PAYOFF_DENSITY_WINDOW,
    _compute_alerts,
    _count_charge_streaks,
    _parse_beat_flags,
    _payoff_ratio_recent,
    build_arc_view,
)
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.project.service import ProjectService

# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def _flag(number: int, payoff: bool = False, charge: bool = False) -> dict:
    return {
        "number": number,
        "has_payoff_beat": payoff,
        "charge_beat": charge,
    }


def _ch_with_beat(number: int, title: str, key_beats) -> dict:
    """构造 chapters 行 payload（直接喂给 build_arc_view 之外的纯函数用）。"""
    return {"number": number, "title": title, "plan_json": {"key_beats": key_beats}}


# ---------------------------------------------------------------------------
# _parse_beat_flags：容错三态
# ---------------------------------------------------------------------------


def test_parse_beat_flags_list_of_dict_with_payoff():
    key_beats = [
        {"beat_id": "b1", "purpose": "普通推进"},
        {"beat_id": "b2", "purpose": "[payoff] 兑现旧伏笔"},
    ]
    assert _parse_beat_flags(key_beats) == (True, False)


def test_parse_beat_flags_list_of_dict_with_charge():
    key_beats = [
        {"beat_id": "b1", "purpose": "[CHARGE] 蓄力下一章反转"},
    ]
    assert _parse_beat_flags(key_beats) == (False, True)


def test_parse_beat_flags_mixed_tags():
    key_beats = [
        {"beat_id": "b1", "purpose": "[payoff] 兑现"},
        {"beat_id": "b2", "purpose": "[charge] 蓄力"},
    ]
    assert _parse_beat_flags(key_beats) == (True, True)


def test_parse_beat_flags_list_of_strings():
    key_beats = ["[charge] 蓄力", "普通推进"]
    assert _parse_beat_flags(key_beats) == (False, True)


def test_parse_beat_flags_missing_returns_false_false():
    assert _parse_beat_flags(None) == (False, False)
    assert _parse_beat_flags([]) == (False, False)
    assert _parse_beat_flags({}) == (False, False)  # 非 list → False/False
    assert _parse_beat_flags("not a list") == (False, False)


def test_parse_beat_flags_ignores_unknown_beat_shapes():
    # dict 但无 purpose 字段、str 元素混合 → 容错仍命中
    key_beats = [
        {"beat_id": "b1"},  # 无 purpose
        42,  # 非 dict 非 str
        {"beat_id": "b2", "purpose": "[payoff] xx"},
    ]
    assert _parse_beat_flags(key_beats) == (True, False)


# ---------------------------------------------------------------------------
# _count_charge_streaks：连击边界
# ---------------------------------------------------------------------------


def test_charge_streaks_empty_returns_zero():
    out = _count_charge_streaks([])
    assert out == {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}


def test_charge_streaks_ending_with_payoff_resets_tail():
    # 5 章 charge_only 后 1 章 payoff → 尾部连击归零
    flags = [
        _flag(1, charge=True),
        _flag(2, charge=True),
        _flag(3, charge=True),
        _flag(4, charge=True),
        _flag(5, charge=True),
        _flag(6, payoff=True),
    ]
    out = _count_charge_streaks(flags)
    assert out["charge_streak"] == 0
    assert out["max_charge_streak"] == 5
    assert out["last_payoff_chapter"] == 6


def test_charge_streaks_tail_exceeds_threshold():
    # 尾部 3 章 charge_only → charge_streak = 3
    flags = [
        _flag(1, payoff=True),
        _flag(2, charge=True),
        _flag(3, charge=True),
        _flag(4, charge=True),
    ]
    out = _count_charge_streaks(flags)
    assert out["charge_streak"] == 3
    assert out["max_charge_streak"] == 3
    assert out["last_payoff_chapter"] == 1


def test_charge_streaks_payoff_breaks_chain_in_middle():
    # charge charge payoff charge charge charge → max=3 (尾部)
    flags = [
        _flag(1, charge=True),
        _flag(2, charge=True),
        _flag(3, payoff=True),
        _flag(4, charge=True),
        _flag(5, charge=True),
        _flag(6, charge=True),
    ]
    out = _count_charge_streaks(flags)
    assert out["charge_streak"] == 3
    assert out["max_charge_streak"] == 3  # 中段断了 + 尾段 3
    assert out["last_payoff_chapter"] == 3


def test_charge_streaks_payoff_plus_charge_same_chapter_resets():
    # 同章同时含 payoff+charge → 不算 charge_only（兑现优先），打破连击
    flags = [
        _flag(1, charge=True),
        _flag(2, charge=True, payoff=True),  # 同章兑现
        _flag(3, charge=True),
        _flag(4, charge=True),
        _flag(5, charge=True),
    ]
    out = _count_charge_streaks(flags)
    # 章 1 charge_only；章 2 兑现打断；章 3-5 连续 charge_only
    assert out["charge_streak"] == 3
    assert out["max_charge_streak"] == 3
    assert out["last_payoff_chapter"] == 2


def test_charge_streaks_no_payoff_returns_null():
    flags = [_flag(i, charge=True) for i in range(1, 4)]
    out = _count_charge_streaks(flags)
    assert out["last_payoff_chapter"] is None
    assert out["charge_streak"] == 3


def test_charge_streaks_only_payoffs_no_streak():
    flags = [_flag(i, payoff=True) for i in range(1, 4)]
    out = _count_charge_streaks(flags)
    assert out["charge_streak"] == 0
    assert out["max_charge_streak"] == 0
    assert out["last_payoff_chapter"] == 3


def test_charge_streaks_neither_payoff_nor_charge_no_streak():
    # 平铺章（无标签）→ 不算 charge_only
    flags = [_flag(i) for i in range(1, 4)]
    out = _count_charge_streaks(flags)
    assert out["charge_streak"] == 0
    assert out["max_charge_streak"] == 0


# ---------------------------------------------------------------------------
# _payoff_ratio_recent：窗口 / 章数不足
# ---------------------------------------------------------------------------


def test_payoff_ratio_recent_uses_window():
    # 5 章全无 payoff → ratio 0
    flags = [_flag(i) for i in range(1, 6)]
    cnt, ratio = _payoff_ratio_recent(flags, 5)
    assert cnt == 0
    assert ratio == 0.0


def test_payoff_ratio_recent_partial_window():
    # 3 章其中 1 章 payoff → 章数不足 window，ratio = 1/3
    flags = [_flag(1), _flag(2, payoff=True), _flag(3)]
    cnt, ratio = _payoff_ratio_recent(flags, 5)
    assert cnt == 1
    assert ratio == pytest.approx(1 / 3)


def test_payoff_ratio_recent_full_window_partial_payoff():
    # 5 章中 1 章 payoff → 20% 低于 40%
    flags = [_flag(1), _flag(2), _flag(3, payoff=True), _flag(4), _flag(5)]
    cnt, ratio = _payoff_ratio_recent(flags, 5)
    assert cnt == 1
    assert ratio == pytest.approx(0.2)


def test_payoff_ratio_recent_empty():
    cnt, ratio = _payoff_ratio_recent([], 5)
    assert cnt == 0
    assert ratio == 0.0


# ---------------------------------------------------------------------------
# _compute_alerts：规则正反例
# ---------------------------------------------------------------------------


def _ch_full(
    number: int,
    *,
    payoff: bool = False,
    charge: bool = False,
    pacing: int | None = None,
) -> dict:
    """构造 _compute_alerts 期望的 chapter 行格式（含 number/has_payoff_beat/charge_beat/pacing）。"""
    return {
        "number": number,
        "has_payoff_beat": payoff,
        "charge_beat": charge,
        "pacing": pacing,
    }


def test_alerts_charge_streak_fail_positive():
    chapters = [_ch_full(i, charge=True) for i in range(1, 4)]  # 3 章连续 charge
    streak_metrics = {"charge_streak": 3, "max_charge_streak": 3, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    codes = [a["code"] for a in alerts]
    levels = [a["level"] for a in alerts]
    assert "charge_streak_exceeded" in codes
    assert "fail" in levels


def test_alerts_charge_streak_fail_boundary_exact():
    # charge_streak == _CHARGE_STREAK_FAIL（边界）→ 触发
    chapters = [_ch_full(i, charge=True) for i in range(1, 4)]
    streak_metrics = {"charge_streak": _CHARGE_STREAK_FAIL, "max_charge_streak": 3, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert any(a["code"] == "charge_streak_exceeded" for a in alerts)


def test_alerts_charge_streak_negative_below_threshold():
    chapters = [_ch_full(i, charge=True) for i in range(1, 3)]  # 仅 2 章
    streak_metrics = {"charge_streak": 2, "max_charge_streak": 2, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert not any(a["code"] == "charge_streak_exceeded" for a in alerts)


def test_alerts_low_payoff_density_positive():
    # 5 章，0 章 payoff → 0% < 40%
    chapters = [_ch_full(i, charge=True) for i in range(1, 6)]
    streak_metrics = {"charge_streak": 5, "max_charge_streak": 5, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert any(a["code"] == "low_payoff_density" and a["level"] == "warn" for a in alerts)


def test_alerts_low_payoff_density_negative_at_threshold():
    # 5 章中 2 章 payoff → 40% == 阈值，按「<」不触发
    chapters = [
        _ch_full(1, payoff=True),
        _ch_full(2),
        _ch_full(3),
        _ch_full(4, payoff=True),
        _ch_full(5),
    ]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": 4}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert not any(a["code"] == "low_payoff_density" for a in alerts)


def test_alerts_low_payoff_density_positive_just_below():
    # 5 章中 1 章 payoff → 20% < 40%
    chapters = [
        _ch_full(1, payoff=True),
        _ch_full(2),
        _ch_full(3),
        _ch_full(4),
        _ch_full(5),
    ]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": 1}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert any(a["code"] == "low_payoff_density" for a in alerts)


def test_alerts_foreshadow_overdue_positive():
    chapters = [_ch_full(1)]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 5, "resolved": 0, "overdue": 2})
    assert any(a["code"] == "foreshadow_overdue" and a["level"] == "warn" for a in alerts)


def test_alerts_foreshadow_overdue_negative_zero():
    chapters = [_ch_full(1)]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 5, "resolved": 0, "overdue": 0})
    assert not any(a["code"] == "foreshadow_overdue" for a in alerts)


def test_alerts_low_pacing_positive():
    chapters = [
        _ch_full(1, pacing=80),
        _ch_full(2, pacing=_PACING_FAIL_THRESHOLD - 1),  # 49 → 触发
        _ch_full(3, pacing=60),
    ]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    pacing_alerts = [a for a in alerts if a["code"] == "low_pacing_chapter"]
    assert len(pacing_alerts) == 1
    assert pacing_alerts[0]["level"] == "warn"
    assert "2" in pacing_alerts[0]["message"]


def test_alerts_low_pacing_merges_multiple_chapters():
    chapters = [
        _ch_full(1, pacing=30),
        _ch_full(2, pacing=80),
        _ch_full(3, pacing=10),
    ]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    pacing_alerts = [a for a in alerts if a["code"] == "low_pacing_chapter"]
    assert len(pacing_alerts) == 1
    msg = pacing_alerts[0]["message"]
    # 两个不达标章号都进 message（顺序按章号）
    assert "1" in msg and "3" in msg


def test_alerts_low_pacing_negative_all_above():
    chapters = [_ch_full(1, pacing=_PACING_FAIL_THRESHOLD), _ch_full(2, pacing=80)]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert not any(a["code"] == "low_pacing_chapter" for a in alerts)


def test_alerts_low_pacing_negative_none_reported():
    # pacing 为 None（无 quality_report）→ 不计入
    chapters = [_ch_full(1, pacing=None), _ch_full(2, pacing=None)]
    streak_metrics = {"charge_streak": 0, "max_charge_streak": 0, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    assert not any(a["code"] == "low_pacing_chapter" for a in alerts)


def test_alerts_multiple_rules_can_coexist():
    chapters = [_ch_full(i, charge=True) for i in range(1, 4)]  # streak=3 fail
    streak_metrics = {"charge_streak": 3, "max_charge_streak": 3, "last_payoff_chapter": None}
    alerts = _compute_alerts(
        chapters, streak_metrics, {"open": 5, "resolved": 0, "overdue": 2}
    )
    codes = {a["code"] for a in alerts}
    # 多条规则同时触发：charge_streak + low_payoff + overdue
    assert "charge_streak_exceeded" in codes
    assert "low_payoff_density" in codes
    assert "foreshadow_overdue" in codes


def test_alerts_charge_streak_fail_overrides_density_when_streak_present():
    # 同时含 charge_streak=3 fail + 低 density → 不应被低 density 阻断
    chapters = [_ch_full(i, charge=True) for i in range(1, 4)]
    streak_metrics = {"charge_streak": 3, "max_charge_streak": 3, "last_payoff_chapter": None}
    alerts = _compute_alerts(chapters, streak_metrics, {"open": 0, "resolved": 0, "overdue": 0})
    codes = [a["code"] for a in alerts]
    assert codes.index("charge_streak_exceeded") < codes.index("low_payoff_density")


# ---------------------------------------------------------------------------
# build_arc_view：DB 集成容错（最小 tmp DB）
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _make_project(db_path: str, name: str = "测试") -> str:
    from packages.domain.project.models import ProjectCreate

    row = ProjectService(db_path).create(ProjectCreate(name=name))
    return row["project_id"]


def _make_chapter(db_path: str, pid: str, number: int, title: str, plan_json: dict) -> str:
    """直插 chapters（含 plan_json），绕开 ChapterService 的状态机。"""
    cid = new_id("ch")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (
                cid,
                pid,
                number,
                title,
                json.dumps(plan_json, ensure_ascii=False),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def test_build_arc_view_empty_project(tmp_path: Path):
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path), "空项目")

    result = build_arc_view(settings.db_path, pid)
    assert result["project_id"] == pid
    assert result["chapters"] == []
    assert result["payoff"] == {
        "total": 0,
        "charge_streak": 0,
        "max_charge_streak": 0,
        "last_payoff_chapter": None,
    }
    assert result["hooks"] == {"open": 0, "resolved": 0, "overdue": 0}
    assert result["debts"] == {"open": 0, "paid": 0}
    # 空项目无任何 alerts（无章→ low_payoff_density 不触发；其他规则也无输入）
    assert result["alerts"] == []
    assert "generated_at" in result


def test_build_arc_view_plan_json_malformed_safe(tmp_path: Path):
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path), "烂数据")

    # plan_json 是非 JSON 字符串（直接绕过 JSON 字段，由 _parse_beat_flags 兜底）
    cid = new_id("ch")
    conn = get_connection(str(settings.db_path))
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, 1, "烂章", "{not valid json", now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    result = build_arc_view(settings.db_path, pid)
    assert len(result["chapters"]) == 1
    ch = result["chapters"][0]
    assert ch["number"] == 1
    assert ch["title"] == "烂章"
    # 无 quality_report → None；无 draft → None
    assert ch["overall"] is None
    assert ch["pacing"] is None
    assert ch["prose_chars"] is None
    assert ch["has_payoff_beat"] is False
    assert ch["charge_beat"] is False


def test_build_arc_view_plan_json_missing_field(tmp_path: Path):
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path), "无key_beats")

    # plan_json 是合法 JSON 但没有 key_beats
    _make_chapter(str(settings.db_path), pid, 1, "无 key_beats", {"chapter_goal": "xx"})

    result = build_arc_view(settings.db_path, pid)
    ch = result["chapters"][0]
    assert ch["has_payoff_beat"] is False
    assert ch["charge_beat"] is False


def test_build_arc_view_project_not_found_raises(tmp_path: Path):
    settings = _make_settings(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        build_arc_view(settings.db_path, "prj_nonexistent")


def test_build_arc_view_picks_quality_and_draft(tmp_path: Path):
    """存在 quality_report / draft 的章：overall / pacing / prose_chars 应填实。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path, "有数据")

    cid = _make_chapter(
        db_path,
        pid,
        1,
        "第 1 章",
        {"key_beats": [{"beat_id": "b1", "purpose": "[payoff] 兑现"}]},
    )

    # 直插 draft（content 含空白，统计应剥除）
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, 1, ?, 'agent:writer:v1', NULL, NULL, ?)
            """,
            (new_id("dr"), cid, "李晨一笑。" + "字" * 1700 + "\n\n谁料？", now_iso()),
        )
        # 直插 quality_report（scores_json 合法 + pacing 字段）
        scores = {"overall": 81, "pacing": 65, "plot": 80}
        conn.execute(
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id,
                 overall, scores_json, issues_json, created_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, '[]', ?)
            """,
            (
                new_id("qr"),
                pid,
                cid,
                81,
                json.dumps(scores, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = build_arc_view(settings.db_path, pid)
    ch = result["chapters"][0]
    assert ch["has_payoff_beat"] is True
    assert ch["overall"] == 81
    assert ch["pacing"] == 65
    # 1700 字 + "李晨一笑。" (5) + "谁料？" (3) + 空白 → 非空白字符数 ≈ 1708
    assert isinstance(ch["prose_chars"], int)
    assert ch["prose_chars"] > 1700
    assert ch["prose_chars"] == 1708


def test_build_arc_view_window_5_payoff_density_triggers(tmp_path: Path):
    """最近 5 章 payoff 占比 < 40% → 触发 low_payoff_density。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path, "密度触发")

    # 6 章：第 1 章 payoff，第 2-6 章 charge_only → 最近 5 章（2-6）payoff 占比 0
    _make_chapter(db_path, pid, 1, "1", {"key_beats": [{"purpose": "[payoff] a"}]})
    for i in range(2, 7):
        _make_chapter(db_path, pid, i, str(i), {"key_beats": [{"purpose": "[charge] b"}]})

    result = build_arc_view(settings.db_path, pid)
    codes = [a["code"] for a in result["alerts"]]
    assert "low_payoff_density" in codes
    # charge 连击 5 → 阈值 3 → 触发 fail
    assert "charge_streak_exceeded" in codes


def test_build_arc_view_pacing_low_triggers_warn(tmp_path: Path):
    """任一章 pacing < 50 → 触发 low_pacing_chapter。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path, "pacing 低")

    cid = _make_chapter(db_path, pid, 1, "1", {"key_beats": []})
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id,
                 overall, scores_json, issues_json, created_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, '[]', ?)
            """,
            (
                new_id("qr"),
                pid,
                cid,
                30,
                json.dumps({"overall": 30, "pacing": 30}, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = build_arc_view(settings.db_path, pid)
    pacing_alerts = [a for a in result["alerts"] if a["code"] == "low_pacing_chapter"]
    assert len(pacing_alerts) == 1
    assert "1" in pacing_alerts[0]["message"]


def test_build_arc_view_hooks_overdue_via_summary(tmp_path: Path):
    """hooks.overdue 由 _summarize_hooks 计算；本测试直接看 result.hooks 字段。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path, "钩子项目")

    # 引入章节 chapter 1（章号 1）
    cid = _make_chapter(db_path, pid, 1, "1", {"key_beats": []})

    conn = get_connection(db_path)
    try:
        # 开放伏笔引入章=1；当前最大章号=1 → 不 overdue
        conn.execute(
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status,
                 importance, expected_payoff_chapter_id, payoff_chapter_id,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'OPEN', 0.8, NULL, NULL,
                    'RESTRICTED', NULL, ?, ?)
            """,
            (new_id("hook"), pid, "未逾期伏笔", cid, now_iso(), now_iso()),
        )
        conn.execute(
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status,
                 importance, expected_payoff_chapter_id, payoff_chapter_id,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, NULL, 'RESOLVED', 0.5, NULL, NULL,
                    'RESTRICTED', NULL, ?, ?)
            """,
            (new_id("hook"), pid, "已兑现伏笔", now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    result = build_arc_view(settings.db_path, pid)
    hooks = result["hooks"]
    assert hooks["open"] == 1
    assert hooks["resolved"] == 1
    assert hooks["overdue"] == 0  # 阈值 30，当前最大章号 1


# ---------------------------------------------------------------------------
# 模块常量存在性校验（防常量被无意删/改名）
# ---------------------------------------------------------------------------


def test_module_constants_exist_with_expected_values():
    assert _CHARGE_STREAK_FAIL == 3
    assert _PAYOFF_DENSITY_WINDOW == 5
    assert _PAYOFF_DENSITY_MIN_RATIO == pytest.approx(0.4)
    assert _PACING_FAIL_THRESHOLD == 50


def test_module_exports_build_arc_view():
    # 顶层 import 路径生效
    from packages.core.arc import build_arc_view as exported

    assert exported is build_arc_view


def test_module_service_public_surface():
    # 模块 __all__ 仅暴露 build_arc_view
    assert arc_service.__all__ == ["build_arc_view"]
