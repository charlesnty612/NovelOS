"""chapter-write 字数闭环节点单测（V3.7+ P1；V3.9 批次 1 修订）。

覆盖（≥6 例）：
1. length_check：prose 在带内（默认 0.85/1.15/1200）→ length_check_passed=True。
2. length_check：prose 超带 → status='over'，length_check_passed=False，band 数值正确。
3. length_check：项目 word_band_json 覆盖生效（low_ratio=0.9/high_ratio=1.1）。
4. condense：带内 → condense_status='skipped_in_band'，prose 不变。
5. condense：rounds_used≥2 → condense_status='over_band_after_2_rounds'，prose 不变。
6. condense：超带首轮 → 调 polisher capability，polished_text 替换 prose，rounds+1。
7. save_draft：length_check 未通过 + 超带放行 → plan_json.deviations 追加偏差注记。
8. save_draft：word_count 用最终 prose 权威实测（V3.9 批次 1.2：压缩后口径），
   不采信 self_report.word_count（实证 ch4-6 偏差 50%+）。
9. builders._inject_scene_word_budget：等分场景 + 余数补首场景。
10. builders._inject_scene_word_budget：场景已声明 target_words 且在 90~100% 区间 → 保留原值。
11. builders._inject_scene_word_budget：场景已声明但总和 < 90% → 归一化为等分。
12. builders.build_writer_input：scene_plan 内每个 scene 带 target_words 注入 payload。
13. V3.9 批次 1.1：生产形态（mock_providers=None）⇒ condense 真实调 LLM。
14. V3.9 批次 1.1：内循环（第 1 轮仍超带 → 第 2 轮落带）+ 压缩后 length_report 回写。
15. V3.9 批次 1.1：两轮耗尽仍超带 → over_band_after_2_rounds + 实测回写。
16. V3.9 批次 1.1：拒更长 / 拒空守卫即终止循环（不空转消耗轮次预算）；
    LLM 异常 fail-soft 保留原文 + 计 1 轮。
17. V3.9 批次 1.2：save_draft 落库 word_count = 压缩后 prose 实测。
18. V3.9 批次 1.3：scene 预算部分声明（子集和落 90~110% 区间）不崩溃且分摊合理。

mock 策略：_condense_node 只在「有 mock_providers 但没配 condense 脚本」时透传
skipped_no_mock；生产形态（mock_providers=None / {}）会真调 run_agent，因此超带
用例统一 monkeypatch ``cw_pipeline.run_agent``——既便于断言调用，也不触发真实 provider。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("NOVELOS_CONTEXT_RELEVANCE", "off")

from packages.core.context_engine.builders import (  # noqa: E402
    _cache_reset,
    _inject_scene_word_budget,
    build_writer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.core.quality.wordcount import (  # noqa: E402
    classify_prose_length,
    word_band,
)
from packages.workflows.chapter_write.pipeline import (  # noqa: E402
    _condense_node,
    _length_check_node,
    _resolve_chapter_word_band,
    _resolve_target_word_count,
    _save_draft_node,
)

MIGRATIONS_DIR = ROOT / "database" / "migrations"

# F-2（V3.9 批次 5）：band 数值一律由权威口径生成，不再手抄——target=3000 的 band_high
# 是 ``int(3000 * 1.15) == 3449``（不是 3450），手写值会随口径漂移。
_BAND_3000_LOW, _BAND_3000_HIGH = word_band(3000)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
            INSERT INTO projects (project_id, name, premise, genre, target_words,
                                  status, word_band_json, created_at, updated_at)
            VALUES (?, 'p', NULL, NULL, NULL, 'ACTIVE', ?, ?, ?)
            """,
            (pid, word_band_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path, project_id: str, *,
    plan_json: str = "{}",
    status: str = "PLANNED",
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,
                                  status, visibility, who_knows,
                                  created_at, updated_at)
            VALUES (?, ?, 1, 't', ?, ?, 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, plan_json, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _plan_with_target(expected: int) -> str:
    return json.dumps({"expected_word_count": expected, "key_beats": []})


# ---------------------------------------------------------------------------
# 1) length_check：带内
# ---------------------------------------------------------------------------


def test_length_check_in_band_default(tmp_path: Path):
    """target=2000、visible=2000 → 带内，length_check_passed=True。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(2000))
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "loaded_plan": {"expected_word_count": 2000, "key_beats": []},
        "polished_prose": "中" * 2000,
    }
    out = _length_check_node(ctx)
    assert out["length_check_passed"] is True
    assert out["length_report"]["status"] == "in_band"
    assert out["length_report"]["visible_chars"] == 2000
    assert out["length_report"]["band_low"] == 1700
    assert out["length_report"]["band_high"] == 2300
    assert out["target_word_count"] == 2000


# ---------------------------------------------------------------------------
# 2) length_check：超带
# ---------------------------------------------------------------------------


def test_length_check_over_band(tmp_path: Path):
    """target=3000、visible=5500 → 超带 status='over'，length_check_passed=False。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "loaded_plan": {"expected_word_count": 3000, "key_beats": []},
        "polished_prose": "中" * 5500,
    }
    out = _length_check_node(ctx)
    assert out["length_check_passed"] is False
    assert out["length_report"]["status"] == "over"
    assert out["length_report"]["visible_chars"] == 5500
    assert out["length_report"]["deviation_pct"] == 83.3
    assert out["target_word_count"] == 3000


# ---------------------------------------------------------------------------
# 3) length_check：项目级覆盖生效
# ---------------------------------------------------------------------------


def test_length_check_project_word_band_override(tmp_path: Path):
    """项目 word_band_json={0.9/1.1/1200} → band=(2700, 3300)。
    visible=3250 ⇒ 带内；visible=3500 ⇒ 超带。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    ctx_in = {
        "db_path": db_path,
        "chapter_id": cid,
        "loaded_plan": {"expected_word_count": 3000, "key_beats": []},
        "polished_prose": "中" * 3250,
    }
    out_in = _length_check_node(ctx_in)
    assert out_in["length_report"]["band_low"] == 2700
    assert out_in["length_report"]["band_high"] == 3300
    assert out_in["length_check_passed"] is True

    ctx_out = {
        "db_path": db_path,
        "chapter_id": cid,
        "loaded_plan": {"expected_word_count": 3000, "key_beats": []},
        "polished_prose": "中" * 3500,
    }
    out_out = _length_check_node(ctx_out)
    assert out_out["length_check_passed"] is False
    assert out_out["length_report"]["status"] == "over"


# ---------------------------------------------------------------------------
# 4) condense：带内 → skipped_in_band
# ---------------------------------------------------------------------------


def test_condense_skipped_when_in_band(tmp_path: Path):
    """length_check_passed=True ⇒ condense 跳过，prose 不变。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(2000))
    prose = "中" * 2000
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "polished_prose": prose,
        "length_check_passed": True,
        "length_report": {
            "visible_chars": 2000,
            "target": 2000,
            "band_low": 1700,
            "band_high": 2300,
            "status": "in_band",
            "within_band": True,
            "deviation_pct": 0.0,
        },
        "condense_rounds": 0,
    }
    out = _condense_node(ctx)
    assert out["condense_status"] == "skipped_in_band"
    assert out["polished_prose"] == prose
    assert out["condense_rounds"] == 0  # 唯一计数键 condense_rounds（smart 审查统一）


# ---------------------------------------------------------------------------
# 5) condense：已达 2 轮上限 → over_band_after_2_rounds
# ---------------------------------------------------------------------------


def test_condense_over_band_after_2_rounds_passthrough(tmp_path: Path):
    """rounds_used=2 ⇒ 不再调 LLM，原样放行。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    prose = "中" * 5500
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "polished_prose": prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 2,
    }
    out = _condense_node(ctx)
    assert out["condense_status"] == "over_band_after_2_rounds"
    assert out["polished_prose"] == prose  # 不动
    assert out["condense_rounds"] == 2  # 不计轮（早返回 preserve）


# ---------------------------------------------------------------------------
# 6) condense：超带首轮 → 调 polisher capability，prose 替换 + rounds+1
# ---------------------------------------------------------------------------


def test_condense_first_round_replaces_prose_and_increments_rounds(tmp_path: Path, monkeypatch):
    """mock condense 输出 polished_text → 替换 prose，rounds_used=0→1。"""
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    long_prose = "中" * 3500
    shrunk_prose = "中" * 3200  # 缩到带内

    condensed_payload = json.dumps({
        "schema_version": "polisher-output.v1",
        "prompt_version": "polisher:v1",
        "polished_text": shrunk_prose,
        "changes_summary": "去掉冗词",
    }, ensure_ascii=False)

    captured: dict[str, Any] = {}

    def _fake_run_agent(db_path, agent_name, payload, run_id, **kw):
        captured["agent_name"] = agent_name
        captured["payload"] = payload
        captured["expected"] = kw.get("expected")
        return json.loads(condensed_payload)

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "polished_prose": long_prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 3500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 16.7,
        },
        "condense_rounds": 0,
        "mock_providers": {"condense": [condensed_payload]},
    }
    out = _condense_node(ctx)

    assert out["condense_status"] == "ok"
    assert out["polished_prose"] == shrunk_prose
    assert out["condense_rounds"] == 1  # 调 LLM 必 +1
    # 验证复用 polisher capability
    assert captured["agent_name"] == "polisher"
    assert captured["expected"] == "polisher"
    # 验证 prompt 携带实测数字与目标带
    assert captured["payload"]["length_context"]["visible_chars"] == 3500
    assert captured["payload"]["length_context"]["target"] == 3000
    assert "实测当前正文「3500 字」" in captured["payload"]["condense_directive"]
    assert "band_high" in captured["payload"]["condense_directive"]


# ---------------------------------------------------------------------------
# 7) save_draft：超带放行 → plan_json.deviations 追加偏差注记
# ---------------------------------------------------------------------------


def test_save_draft_appends_deviation_on_over_band_passthrough(tmp_path: Path):
    """length_check_passed=False + 超带放行 → chapter plan_json.deviations 追加 1 条。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    prose = "中" * 5500
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "writer_output": {"prose": prose, "prompt_version": "writer:v1",
                          "self_report": {"word_count": 3008}},
        "polished_prose": prose,
        # 关键：length_check 报告超带 + rounds 已耗尽（2 轮后放行）
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 2,
        "writer_model_id": "mock/mock",
    }
    out = _save_draft_node(ctx)

    # word_count 必须用权威实测 5500，不能采信 self_report.word_count=3008
    assert out["word_count"] == 5500

    # plan_json.deviations 应追加 1 条
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    plan = json.loads(row["plan_json"])
    devs = plan.get("deviations") or []
    assert len(devs) == 1
    assert devs[0]["from"] == "word_count_target"
    assert "实测 5500 字" in devs[0]["to"]
    assert devs[0]["source"] == "chapter_write.length_check"
    assert devs[0]["round"] == 2


# ---------------------------------------------------------------------------
# 8) save_draft：word_count 用权威实测（length_report.visible_chars），
#    即便 self_report.word_count 乱报也以实测为准。
# ---------------------------------------------------------------------------


def test_save_draft_authoritative_word_count_overrides_self_report(tmp_path: Path):
    """实证 ch4-6：self_report.word_count=3008 但 actual=4721 → 存库 = 4721。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    actual_prose = "中" * 4721
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "writer_output": {"prose": actual_prose, "prompt_version": "writer:v1",
                          "self_report": {"word_count": 3008}},
        "polished_prose": actual_prose,
        "length_check_passed": True,
        "length_report": {
            "visible_chars": 4721,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "in_band",
            "within_band": True,
            "deviation_pct": 57.4,
        },
        "condense_rounds": 0,
        "writer_model_id": "mock/mock",
    }
    out = _save_draft_node(ctx)
    assert out["word_count"] == 4721
    # drafts 表里也应是 4721（落库实测）
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["content"] == actual_prose
    assert len(row["content"]) == 4721  # visible_chars 口径一致


# ---------------------------------------------------------------------------
# 9) builders._inject_scene_word_budget：等分场景 + 余数补首场景
# ---------------------------------------------------------------------------


def test_inject_scene_word_budget_equal_split_with_remainder():
    """3 场景 + target=3000 → 等分 1000/1000/1000（divmod 余数 0）。
    4 场景 + target=3000 → 等分 750 + 余数补首场景（首场景 751）。
    """
    scene_plan_3 = {
        "scenes": [
            {"scene_id": "scene_001", "purpose": "A"},
            {"scene_id": "scene_002", "purpose": "B"},
            {"scene_id": "scene_003", "purpose": "C"},
        ]
    }
    out = _inject_scene_word_budget(scene_plan_3, 3000)
    assert [s["target_words"] for s in out["scenes"]] == [1000, 1000, 1000]

    scene_plan_4 = {
        "scenes": [
            {"scene_id": "scene_001", "purpose": "A"},
            {"scene_id": "scene_002", "purpose": "B"},
            {"scene_id": "scene_003", "purpose": "C"},
            {"scene_id": "scene_004", "purpose": "D"},
        ]
    }
    out4 = _inject_scene_word_budget(scene_plan_4, 3000)
    # divmod(3000, 4) = (750, 0) → 全 750
    assert [s["target_words"] for s in out4["scenes"]] == [750, 750, 750, 750]

    scene_plan_5 = {
        "scenes": [
            {"scene_id": "scene_001", "purpose": "A"},
            {"scene_id": "scene_002", "purpose": "B"},
            {"scene_id": "scene_003", "purpose": "C"},
            {"scene_id": "scene_004", "purpose": "D"},
            {"scene_id": "scene_005", "purpose": "E"},
        ]
    }
    out5 = _inject_scene_word_budget(scene_plan_5, 3000)
    # divmod(3000, 5) = (600, 0) → 全 600
    assert [s["target_words"] for s in out5["scenes"]] == [600, 600, 600, 600, 600]

    scene_plan_7 = {
        "scenes": [{"scene_id": f"scene_{i:03d}"} for i in range(1, 8)],
    }
    out7 = _inject_scene_word_budget(scene_plan_7, 3000)
    # divmod(3000, 7) = (428, 4) → 首场景 428+4=432，其余 428
    assert out7["scenes"][0]["target_words"] == 432
    for s in out7["scenes"][1:]:
        assert s["target_words"] == 428


# ---------------------------------------------------------------------------
# 10) builders._inject_scene_word_budget：已填且在 90-100% 区间 → 保留
# ---------------------------------------------------------------------------


def test_inject_scene_word_budget_keeps_declared_in_range():
    """3 场景已声明 [1000, 1000, 1000]，target=3000 → 90% 区间下限 2700，
    总和 3000 ∈ [2700, 3300] ⇒ 保留原值。
    """
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "target_words": 1000},
            {"scene_id": "scene_002", "target_words": 1000},
            {"scene_id": "scene_003", "target_words": 1000},
        ]
    }
    out = _inject_scene_word_budget(scene_plan, 3000)
    assert [s["target_words"] for s in out["scenes"]] == [1000, 1000, 1000]


# ---------------------------------------------------------------------------
# 11) builders._inject_scene_word_budget：已填但总和 < 90% → 归一化
# ---------------------------------------------------------------------------


def test_inject_scene_word_budget_normalizes_underfilled_declared():
    """3 场景声明 [500, 500, 500]，target=3000，总和 1500 < 2700 (90%) → 等分。"""
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "target_words": 500},
            {"scene_id": "scene_002", "target_words": 500},
            {"scene_id": "scene_003", "target_words": 500},
        ]
    }
    out = _inject_scene_word_budget(scene_plan, 3000)
    assert [s["target_words"] for s in out["scenes"]] == [1000, 1000, 1000]


# ---------------------------------------------------------------------------
# 12) builders.build_writer_input：scene_plan 内每个 scene 带 target_words
# ---------------------------------------------------------------------------


def test_build_writer_input_injects_target_words_per_scene(tmp_path: Path):
    """build_writer_input 透传时，每个 scene 都带 target_words。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "purpose": "A"},
            {"scene_id": "scene_002", "purpose": "B"},
        ]
    }
    payload = build_writer_input(
        db_path, cid, scene_plan, target_word_count=3000,
    )
    out_scenes = payload["scene_plan"]["scenes"]
    assert all("target_words" in s for s in out_scenes)
    # 等分（divmod 无余数）
    assert out_scenes[0]["target_words"] == 1500  # divmod(3000, 2)=(1500, 0)
    assert out_scenes[1]["target_words"] == 1500
    # 其他字段保留
    assert out_scenes[0]["scene_id"] == "scene_001"
    assert out_scenes[0]["purpose"] == "A"


# ---------------------------------------------------------------------------
# 辅助：resolve_band_config 透传 sanity check（防御性）
# ---------------------------------------------------------------------------


def test_resolve_chapter_word_band_returns_default_when_no_override(tmp_path: Path):
    """无覆盖 → cfg 含 low_ratio=0.85 / high_ratio=1.15 / floor=1200。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _pid, cfg = _resolve_chapter_word_band(db_path, cid)
    assert cfg["low_ratio"] == 0.85
    assert cfg["high_ratio"] == 1.15
    assert cfg["floor"] == 1200


def test_resolve_target_word_count_priority(tmp_path: Path):
    """ctx.target_word_count > plan.expected_word_count > 默认 3000。"""
    # ctx 优先
    assert _resolve_target_word_count(
        {"target_word_count": 1234}, {"expected_word_count": 2000},
    ) == 1234
    # ctx 缺 → plan
    assert _resolve_target_word_count(
        {}, {"expected_word_count": 2000},
    ) == 2000
    # 都缺 → 3000
    assert _resolve_target_word_count({}, {}) == 3000


# ---------------------------------------------------------------------------
# smart 审查 P2 一致性收口（4 条新用例）：
# 1) condense 计数器真实递增并止于 2 轮；
# 2) condense 输出确定性守卫（空串 / 更长 → 拒收 → failed_no_shrink）；
# 3) save_draft 偏差注记仅在 condense_rounds >= 2 时写（round=0 不写）；
# 4) _resolve_chapter_word_band 非法值（low>high）不击穿，落默认 + 告警。
# ---------------------------------------------------------------------------


# ----- (1) 计数器真实递增并止于 2 轮 -----

def test_condense_counter_increments_and_caps_at_two(tmp_path: Path, monkeypatch):
    """第 1 次调 LLM → condense_rounds=1；第 2 次调 LLM → condense_rounds=2；
    第 3 次进入节点 → 早返回 over_band_after_2_rounds，condense_rounds 不再 +1。

    skipped_no_mock（mock 流缺 condense 脚本）/ skipped_empty / skipped_in_band /
    over_band_after_2_rounds 均不计轮（保留 rounds_used），且都不调 LLM。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    long_prose = "中" * 5500
    shrunk_prose = "中" * 3000

    condensed_payload = json.dumps({
        "schema_version": "polisher-output.v1",
        "prompt_version": "polisher:v1",
        "polished_text": shrunk_prose,
        "changes_summary": "砍冗词",
    }, ensure_ascii=False)

    calls: list[int] = []

    def _fake_run_agent(*a, **kw):
        calls.append(1)
        return json.loads(condensed_payload)

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    base_ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "polished_prose": long_prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "mock_providers": {"condense": [condensed_payload]},
    }

    # 第 1 轮：rounds 0 → 1（压缩后 3000 落带 → 内循环不再追加轮次）
    ctx1 = dict(base_ctx, condense_rounds=0)
    out1 = _condense_node(ctx1)
    assert out1["condense_rounds"] == 1
    assert out1["condense_status"] == "ok"

    # 第 2 轮：rounds 1 → 2
    ctx2 = dict(base_ctx, condense_rounds=1, polished_prose=long_prose)
    out2 = _condense_node(ctx2)
    assert out2["condense_rounds"] == 2
    assert out2["condense_status"] == "ok"
    assert len(calls) == 2  # ctx1 / ctx2 各调 1 次

    # 第 3 轮：rounds=2 → 早返回 over_band_after_2_rounds，不再 +1（也不调 LLM）
    ctx3 = dict(base_ctx, condense_rounds=2, polished_prose=long_prose)
    out3 = _condense_node(ctx3)
    assert out3["condense_rounds"] == 2  # 不变
    assert out3["condense_status"] == "over_band_after_2_rounds"
    assert len(calls) == 2

    # skipped_no_mock：mock 流（有 mock_providers）但没配 condense 脚本 → 透传；
    # 生产形态（mock_providers=None / {}）不走这条路径（V3.9 批次 1.1 起真调 LLM）。
    ctx_mock_passthrough = dict(
        base_ctx, condense_rounds=0, mock_providers={"writer": ["{}"]},
    )
    out_no_mock = _condense_node(ctx_mock_passthrough)
    assert out_no_mock["condense_rounds"] == 0
    assert out_no_mock["condense_status"] == "skipped_no_mock"
    assert len(calls) == 2  # 透传不调 LLM

    # skipped_empty 不计轮
    ctx_empty = dict(base_ctx, condense_rounds=0, polished_prose="")
    out_empty = _condense_node(ctx_empty)
    assert out_empty["condense_rounds"] == 0
    assert out_empty["condense_status"] == "skipped_empty"

    # skipped_in_band 不计轮
    ctx_in = dict(base_ctx, condense_rounds=0, length_check_passed=True)
    out_in = _condense_node(ctx_in)
    assert out_in["condense_rounds"] == 0
    assert out_in["condense_status"] == "skipped_in_band"


# ----- (2) condense 输出确定性守卫 -----

def test_condense_rejects_empty_output(tmp_path: Path, monkeypatch):
    """LLM 返回 polished_text='' → 拒收，保留原 prose，condense_status='failed_no_shrink'，计 1 轮。"""
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    long_prose = "中" * 3500

    empty_payload = json.dumps({
        "schema_version": "polisher-output.v1",
        "prompt_version": "polisher:v1",
        "polished_text": "",
        "changes_summary": "",
    }, ensure_ascii=False)

    monkeypatch.setattr(
        cw_pipeline, "run_agent",
        lambda *a, **kw: json.loads(empty_payload),
    )

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "polished_prose": long_prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 3500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 16.7,
        },
        "condense_rounds": 0,
        "mock_providers": {"condense": [empty_payload]},
    }
    out = _condense_node(ctx)
    assert out["condense_status"] == "failed_no_shrink"
    assert out["condense_rounds"] == 1  # 调过 LLM 计 1 轮
    assert out["polished_prose"] == long_prose  # 保留原文


def test_condense_rejects_longer_output(tmp_path: Path, monkeypatch):
    """LLM 返回 polished_text 比原文更长（visible_chars 反而增加）→ 拒收。

    杜绝把扩写版本当作压缩版落库。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    long_prose = "中" * 3500
    longer_prose = "中" * 3600  # 反而更长

    longer_payload = json.dumps({
        "schema_version": "polisher-output.v1",
        "prompt_version": "polisher:v1",
        "polished_text": longer_prose,
        "changes_summary": "错把扩写当压缩",
    }, ensure_ascii=False)

    monkeypatch.setattr(
        cw_pipeline, "run_agent",
        lambda *a, **kw: json.loads(longer_payload),
    )

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "polished_prose": long_prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 3500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 16.7,
        },
        "condense_rounds": 0,
        "mock_providers": {"condense": [longer_payload]},
    }
    out = _condense_node(ctx)
    assert out["condense_status"] == "failed_no_shrink"
    assert out["condense_rounds"] == 1
    assert out["polished_prose"] == long_prose


# ----- (3) deviations 注记条件收窄 -----

def test_save_draft_no_deviation_when_condense_rounds_below_two(tmp_path: Path):
    """condense_rounds=0（mock 缺失 / 早返回跳过场景）→ 不写「2 轮后仍超带」注记。

    即便 length_check_passed=False + status='over' 也不写，避免 round=0 时
    错误触发 2 轮后文案污染 W-LEN 报告。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    prose = "中" * 5500

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "writer_output": {"prose": prose, "prompt_version": "writer:v1",
                          "self_report": {"word_count": 3008}},
        "polished_prose": prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 0,  # round=0 → 不应写偏差注记
        "writer_model_id": "mock/mock",
    }
    _save_draft_node(ctx)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    plan = json.loads(row["plan_json"])
    devs = plan.get("deviations") or []
    assert len(devs) == 0, f"round=0 不应写偏差注记；got {devs!r}"


def test_save_draft_deviation_only_when_condense_rounds_two(tmp_path: Path):
    """condense_rounds=1（仅跑过 1 轮但超带）→ 仍不写偏差注记；只有 rounds>=2 才写。

    验证条件收窄：旧口径只检 length_check_passed，新口径必须 condense_rounds >= 2。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))
    prose = "中" * 5500

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "writer_output": {"prose": prose, "prompt_version": "writer:v1",
                          "self_report": {"word_count": 3008}},
        "polished_prose": prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 1,  # 仅跑 1 轮 → 仍不写
        "writer_model_id": "mock/mock",
    }
    _save_draft_node(ctx)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    plan = json.loads(row["plan_json"])
    devs = plan.get("deviations") or []
    assert len(devs) == 0, f"condense_rounds=1 不应写偏差注记；got {devs!r}"


# ----- (4) word_band ValueError 防击穿 -----

def test_resolve_chapter_word_band_invalid_value_falls_back_to_default(tmp_path: Path):
    """非法 word_band_json（low_ratio > high_ratio 倒挂，触发 resolve_band_config ValueError）
    → _resolve_chapter_word_band 捕获并落默认带 0.85/1.15/1200，不击穿。
    """
    db_path = _fresh_db(tmp_path)
    # low_ratio=1.2 > high_ratio=1.0 倒挂 → resolve_band_config 抛 ValueError
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 1.2, "high_ratio": 1.0, "floor": 1200}),
    )
    cid = _insert_chapter(db_path, pid)
    _pid, cfg = _resolve_chapter_word_band(db_path, cid)
    # 落默认带
    assert cfg["low_ratio"] == 0.85
    assert cfg["high_ratio"] == 1.15
    assert cfg["floor"] == 1200

    # 进一步：用 length_check_node 跑一遍（覆盖实际消费路径），不能抛
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "loaded_plan": {"expected_word_count": 2000, "key_beats": []},
        "polished_prose": "中" * 2000,
    }
    out = _length_check_node(ctx)
    # 默认 0.85/1.15/1200 生效
    assert out["length_report"]["band_low"] == 1700
    assert out["length_report"]["band_high"] == 2300
    assert out["length_check_passed"] is True


def test_resolve_chapter_word_band_negative_floor_falls_back(tmp_path: Path):
    """非法 floor=-100 → resolve_band_config ValueError → 落默认。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(
        db_path,
        word_band_json=json.dumps({"low_ratio": 0.85, "high_ratio": 1.15, "floor": -100}),
    )
    cid = _insert_chapter(db_path, pid)
    _pid, cfg = _resolve_chapter_word_band(db_path, cid)
    assert cfg["floor"] == 1200


# ---------------------------------------------------------------------------
# V3.9 批次 1.1：condense 生产可达（无 mock ⇒ 真调 LLM）+ 节点内 ≤2 轮压缩循环
# ---------------------------------------------------------------------------


def _condense_ctx(db_path: Path, cid: str, prose: str, **overrides: Any) -> dict[str, Any]:
    """超带场景的 condense 节点 ctx。

    length_report 直接由权威口径 ``classify_prose_length`` 生成（与 length_check
    节点同源），避免手抄 band 数值与生产真值漂移（如 target=3000 的
    ``int(3000*1.15) == 3449``）。
    默认 mock_providers=None（生产形态；要 mock 流请显式 override）。
    """
    report = classify_prose_length(prose, 3000)
    report["within_band"] = report["status"] == "in_band"
    ctx: dict[str, Any] = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_condense",
        "polished_prose": prose,
        "length_check_passed": report["within_band"],
        "length_report": report,
        "condense_rounds": 0,
        "mock_providers": None,
    }
    ctx.update(overrides)
    return ctx


def test_condense_production_calls_run_agent_without_mock_script(
    tmp_path: Path, monkeypatch,
):
    """V3.9 批次 1.1：mock_providers=None（生产链路）⇒ condense 真调 run_agent。

    旧行为：mock_providers 无 'condense' 键即 skipped_no_mock 早返回，生产环境
    永远走不到 LLM 压缩（字数闭环失效）。现在只有「mock_providers 非空但没配
    condense 脚本」的 mock 流才透传。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    long_prose = "中" * 5500
    shrunk_prose = "中" * 3200  # 落带（<= band_high）
    band_high = classify_prose_length(long_prose, 3000)["band_high"]
    calls: list[dict[str, Any]] = []

    def _fake_run_agent(db_path_arg, agent_name, payload, run_id, **kw):
        calls.append({"agent_name": agent_name, "payload": payload, "kwargs": kw})
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": "polisher:v1",
            "polished_text": shrunk_prose,
            "changes_summary": "砍铺垫与冗词",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = _condense_node(_condense_ctx(db_path, cid, long_prose))

    assert len(calls) == 1, "生产链路必须真实调用 condense（不再 skipped_no_mock）"
    assert calls[0]["agent_name"] == "polisher"  # 复用 polisher capability
    assert calls[0]["kwargs"]["mock_script"] is None  # None ⇒ run_agent 走真实 provider
    assert calls[0]["kwargs"]["expected"] == "polisher"
    lc = calls[0]["payload"]["length_context"]
    assert lc["visible_chars"] == 5500  # payload 必带实测数字
    assert lc["target"] == 3000
    assert lc["band_high"] == band_high
    assert "实测当前正文「5500 字」" in calls[0]["payload"]["condense_directive"]

    assert out["condense_status"] == "ok"
    assert out["polished_prose"] == shrunk_prose
    assert out["condense_rounds"] == 1
    # 压缩后真相回写（覆盖 length_check 旧值）→ save_draft 读到压缩后数据
    assert out["length_report"]["visible_chars"] == 3200
    assert out["length_report"]["status"] == "in_band"
    assert out["length_check_passed"] is True


def test_condense_internal_loop_reaches_in_band_on_second_round(
    tmp_path: Path, monkeypatch,
):
    """内循环：第 1 轮压到 5000（仍超带边缘 ``_BAND_3000_HIGH``）→ 第 2 轮压到 3000（落带）。

    断言第二轮 payload 用压缩后的最新实测重建，且最终 length_report 是压缩后
    实测（不是 length_check 旧值 5500）。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    first = "中" * 5000
    second = "中" * 3000
    outputs = [first, second]
    payloads: list[dict[str, Any]] = []

    def _fake_run_agent(db_path_arg, agent_name, payload, run_id, **kw):
        payloads.append(payload)
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": "polisher:v1",
            "polished_text": outputs[len(payloads) - 1],
            "changes_summary": f"第 {len(payloads)} 轮压缩",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = _condense_node(_condense_ctx(db_path, cid, "中" * 5500))

    assert len(payloads) == 2
    assert payloads[0]["length_context"]["visible_chars"] == 5500
    assert payloads[0]["length_context"]["round"] == 1
    assert payloads[1]["length_context"]["visible_chars"] == 5000  # 最新实测
    assert payloads[1]["length_context"]["round"] == 2
    assert payloads[1]["draft_text"] == first  # 第二轮压的是第一轮产出

    assert out["condense_status"] == "ok"
    assert out["condense_rounds"] == 2
    assert out["polished_prose"] == second
    assert out["length_report"]["visible_chars"] == 3000
    assert out["length_report"]["status"] == "in_band"
    assert out["length_check_passed"] is True


def test_condense_two_rounds_exhausted_still_over_band(tmp_path: Path, monkeypatch):
    """两轮都压不到位 → over_band_after_2_rounds；length_report 回写压缩后实测。

    该形态使 save_draft 的「2 轮后仍超带」偏差注记分支真实可达（修复 1 前
    condense_rounds 在生产中永远是 0，注记分支不可达）。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    outputs = ["中" * 5200, "中" * 4000]  # 4000 仍 > band_high（_BAND_3000_HIGH）
    calls: list[dict[str, Any]] = []

    def _fake_run_agent(db_path_arg, agent_name, payload, run_id, **kw):
        calls.append(payload)
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": "polisher:v1",
            "polished_text": outputs[len(calls) - 1],
            "changes_summary": "压缩",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = _condense_node(_condense_ctx(db_path, cid, "中" * 5500))

    assert len(calls) == 2
    assert out["condense_status"] == "over_band_after_2_rounds"
    assert out["condense_rounds"] == 2
    assert out["polished_prose"] == outputs[1]
    assert out["length_report"]["visible_chars"] == 4000
    assert out["length_report"]["status"] == "over"
    assert out["length_check_passed"] is False


def test_condense_rejected_output_stops_loop(tmp_path: Path, monkeypatch):
    """拒更长守卫即终止循环：第 1 轮返回扩写版 → failed_no_shrink，不再调第 2 轮。

    守卫拒收后若继续下一轮，既浪费轮次预算又拿不到更短文本；此处钉住「一轮拒收
    即收工」语义（拒空同理，见 test_condense_rejects_empty_output）。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    original = "中" * 3500
    calls: list[int] = []

    def _fake_run_agent(db_path_arg, agent_name, payload, run_id, **kw):
        calls.append(1)
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": "polisher:v1",
            "polished_text": "中" * 3600,  # 反而更长
            "changes_summary": "错把扩写当压缩",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = _condense_node(_condense_ctx(db_path, cid, original))

    assert len(calls) == 1  # 不空转消耗第 2 轮预算
    assert out["condense_status"] == "failed_no_shrink"
    assert out["condense_rounds"] == 1
    assert out["polished_prose"] == original  # 保留原文


def test_condense_llm_exception_fail_soft_and_counts_round(tmp_path: Path, monkeypatch):
    """LLM 抛异常 → fail-soft 不阻断（保留原文）+ 计 1 轮 + condense_error 留痕。

    计数先于调用（旧实现 next_rounds 在 try 之前），异常路径同样计 1 轮。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    def _boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(cw_pipeline, "run_agent", _boom)

    original = "中" * 5500
    out = _condense_node(_condense_ctx(db_path, cid, original))

    assert out["condense_status"] == "failed"
    assert out["condense_rounds"] == 1
    assert out["polished_prose"] == original
    assert "provider down" in out["condense_error"]
    # 失败也回写实测：下游 save_draft 拿到的仍是真相（不是空 report / 旧值）
    assert out["length_report"]["visible_chars"] == 5500
    assert out["length_check_passed"] is False


# ---------------------------------------------------------------------------
# V3.9 批次 1.2：save_draft 字数断链修复（word_count = 最终 prose 实测）
# ---------------------------------------------------------------------------


def test_save_draft_word_count_uses_compressed_prose(tmp_path: Path):
    """condense 已把正文压到 3200，但 ctx 里 length_report 仍是压缩前旧值 5500
    → 落库 word_count 必须是最终 prose 实测 3200（而不是旧 report 的 5500）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    compressed = "中" * 3200
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_test",
        "writer_output": {"prose": "中" * 5500, "prompt_version": "writer:v1",
                          "self_report": {"word_count": 3008}},
        "polished_prose": compressed,
        "length_check_passed": True,
        # 压缩前旧值（修复前 save_draft 直接读这个 → 落库 5500）
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": _BAND_3000_LOW,
            "band_high": _BAND_3000_HIGH,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 1,
        "writer_model_id": "mock/mock",
    }
    out = _save_draft_node(ctx)

    assert out["word_count"] == 3200
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["content"] == compressed
    assert len(row["content"]) == out["word_count"]  # 落库正文与字数同口径


def test_condense_then_save_draft_records_compressed_truth(
    tmp_path: Path, monkeypatch,
):
    """修复 1 + 2 串联（引擎 ctx.update 语义）：condense 2 轮仍超带 →
    save_draft 落库压缩后实测 word_count，偏差注记写压缩后数字 + round=2。
    """
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json=_plan_with_target(3000))

    outputs = ["中" * 5200, "中" * 4000]
    calls: list[int] = []

    def _fake_run_agent(db_path_arg, agent_name, payload, run_id, **kw):
        calls.append(1)
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": "polisher:v1",
            "polished_text": outputs[len(calls) - 1],
            "changes_summary": "压缩",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    ctx = _condense_ctx(db_path, cid, "中" * 5500)
    ctx.update(_condense_node(ctx))  # 引擎：ctx.update(node output)
    ctx["writer_output"] = {"prose": "中" * 5500, "prompt_version": "writer:v1",
                            "self_report": {"word_count": 3008}}
    ctx["writer_model_id"] = "mock/mock"
    out = _save_draft_node(ctx)

    assert out["word_count"] == 4000  # 压缩后实测
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    devs = json.loads(row["plan_json"])["deviations"]
    assert len(devs) == 1
    assert devs[0]["round"] == 2
    assert "实测 4000 字" in devs[0]["to"]  # 注记写压缩后真相，不是 5500


# ---------------------------------------------------------------------------
# V3.9 批次 1.3：scene 预算部分声明（int(None) 崩溃修复）
# ---------------------------------------------------------------------------


def test_inject_scene_word_budget_partial_declaration_no_crash():
    """已声明子集和落 90~110% 区间 + 存在未声明 scene → 未声明 scene 吃剩余预算。

    复现用例（旧代码 TypeError: int() argument must be ... not 'NoneType'）：
    scenes=[{target_words:2000},{target_words:1000},{}]，target=3000。
    """
    scene_plan = {
        "scenes": [
            {"scene_id": "scene_001", "purpose": "A", "target_words": 2000},
            {"scene_id": "scene_002", "purpose": "B", "target_words": 1000},
            {"scene_id": "scene_003", "purpose": "C"},
        ]
    }
    out = _inject_scene_word_budget(scene_plan, 3000)
    tws = [s["target_words"] for s in out["scenes"]]
    assert all(isinstance(v, int) for v in tws), f"全 int 不出现 None；got {tws!r}"
    assert tws[:2] == [2000, 1000]  # 已声明值不动
    assert tws[2] == 1000  # 剩余 = 3000-3000 = 0 ≤ 0 → T/n = 1000 保底值
    # 不就地改写输入
    assert "target_words" not in scene_plan["scenes"][2]

    # 剩余 > 0：已声明子集吃掉大部分预算 → 未声明场景均分剩余
    scene_plan2 = {
        "scenes": [
            {"scene_id": "scene_001", "target_words": 3800},
            {"scene_id": "scene_002"},
            {"scene_id": "scene_003"},
        ]
    }
    out2 = _inject_scene_word_budget(scene_plan2, 4000)
    tws2 = [s["target_words"] for s in out2["scenes"]]
    assert all(isinstance(v, int) for v in tws2)
    assert tws2[0] == 3800
    assert tws2[1:] == [100, 100]  # 剩余 4000-3800=200 → 均分
    assert sum(tws2) == 4000

