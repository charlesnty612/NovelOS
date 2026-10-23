"""inspect_stalled_runs 单元测试。

**只读安全**：全部夹具在 ``tmp_path`` 上现造，绝不接触 ``data/novelos.db``。
唯一的 dev 库相关断言是「生产 ``connect_readonly`` 拒绝写入」——该测试用
``tmp_path`` 造库，不是真库。

夹具刻意覆盖三类形态，因为它们对应完全不同的处置决策：

* ``PAUSED`` + 满的 review_report（error 非空）⇒ 有实质内容，取消有信息损失；
* ``PAUSED`` + 空 review_report ⇒ 空报告，取消几乎无损；
* ``PAUSED`` + 未知 payload 形态 ⇒ 必须报 ``unknown`` 而不是静默归成「空」。

外加守卫口径的形状级看守：PAUSED **不**阻止新 start，且 cancel 端点不接受
PAUSED——这两条与「PAUSED run 会 409」的错误说法冲突，必须有测试钉住。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# 走项目约定（`pythonpath = ["."]` + 命名空间包），与 test_produce_chapters /
# test_open_volume 一致。**不要** sys.path.insert(scripts/)：那会把 scripts/ 顶到
# sys.path 最前，让 migrate / serve / content_sync 等模块名在全仓可见——xdist 下
# 同一 worker 进程还会连跑别的测试文件，这个全局改动会外溢（曾实测致全量跑中途崩）。
import scripts.inspect_stalled_runs as isr  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _review_payload(
    *,
    word_count: int = 1998,
    target: int = 3000,
    errors: list | None = None,
    warnings: list | None = None,
    critic_issues: list | None = None,
) -> dict:
    if errors is None:
        errors = [
            {
                "rule_id": "W-LEN-DEVIATION",
                "severity": "error",
                "message": f"[W-LEN-DEVIATION] visible={word_count} target={target} deviation=-33.4%",
            }
        ]
    if warnings is None:
        warnings = ["禁用词命中：忽然", "[AI-PRONOUN-PILE] 主语排比堆砌 4 句"]
    if critic_issues is None:
        critic_issues = [
            {
                "category": "ai_flavor",
                "severity": "medium",
                "quote": "烛焰忽然齐齐往地砖方向倒伏。",
                "suggestion": "「忽然」属高频模板连接词，删去或改为具体触发。",
            }
        ]
    return {
        "stage": "chapter-review",
        "message": "请审查章节草稿并批准或驳回",
        "review_report": {
            "chapter_id": "ch_fixture",
            "draft_version": 3,
            "word_count": word_count,
            "target_word_count": target,
            "word_band": {"low": 2550, "high": 3449},
            "within_range": False,
            "deviation_pct": -33.4,
            "errors": errors,
            "warnings": warnings,
            "ai_pattern_hits": ["AI-PRONOUN-PILE"],
            "forbidden_word_hits": ["忽然"],
        },
        "critic_status": "ok",
        "critic_mode": "always",
        "critic_skipped": False,
        "critic_report": {
            "chapter_id": "ch_fixture",
            "issues": critic_issues,
            "overall_comment": "结构可用，AI 味偏重，长度明显欠带。",
            "strengths": ["对白推进干净"],
        },
        "deep_review_status": "skipped",
        "deep_review_report": None,
        "deep_review_skipped": True,
    }


def _ckpt(node: str, payload: dict | None) -> str:
    body: dict = {"_current_node_id": node, "chapter_id": "ch_fixture", "project_id": "prj_fixture"}
    if payload is not None:
        body[node] = {isr.PAUSE_PAYLOAD_KEY: payload}
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 夹具工厂
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, name TEXT, status TEXT, created_at TEXT
);
CREATE TABLE chapters (
    chapter_id TEXT PRIMARY KEY, project_id TEXT, number INTEGER, title TEXT, status TEXT
);
CREATE TABLE workflow_runs (
    run_id TEXT PRIMARY KEY, workflow_id TEXT, chapter_id TEXT, status TEXT,
    current_node TEXT, checkpoint_json TEXT, error TEXT, retry_count INTEGER,
    started_at TEXT, ended_at TEXT, instance_id TEXT
);
"""


def _make_db(path: Path, runs: list[dict]) -> None:
    """在临时路径上造最小可查库。写入用普通连接——这是夹具自己的库。"""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO projects VALUES ('prj_fixture','测试项目','ACTIVE',?)", (_iso(40),))
        conn.executemany(
            "INSERT INTO chapters VALUES (?,?,?,?,?)",
            [
                ("ch_fixture", "prj_fixture", 6, "假账过户", "COMMITTED"),
                ("ch_drafted", "prj_fixture", 7, "撕去的一页", "DRAFTED"),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["run_id"], r["workflow_id"], r.get("chapter_id"), r["status"],
                    r.get("current_node"), r.get("checkpoint_json"), r.get("error"),
                    r.get("retry_count", 0), r.get("started_at"), None, r.get("instance_id"),
                )
                for r in runs
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _sub_db(base: Path) -> Path:
    """单 run 最小库（满 review_report），给渲染层 / 导出层的形状测试用。

    ``base`` 传 pytest 的 ``tmp_path``——会话结束自动清理，不在系统临时目录里
    留一次性目录。
    """
    path = Path(base) / "sub.db"
    _make_db(
        path,
        [
            {
                "run_id": "wfr_full",
                "workflow_id": "wf_write",
                "chapter_id": "ch_fixture",
                "status": "PAUSED",
                "current_node": "author_review",
                "checkpoint_json": _ckpt("author_review", _review_payload()),
                "started_at": _iso(29),
            },
            {
                "run_id": "wfr_empty",
                "workflow_id": "wf_write",
                "chapter_id": "ch_fixture",
                "status": "PAUSED",
                "current_node": "author_review",
                "checkpoint_json": _ckpt("author_review", {"stage": "chapter-review", "review_report": {}}),
                "started_at": _iso(10),
            },
        ],
    )
    return path


@pytest.fixture
def full_db(tmp_path: Path) -> Path:
    """满形态库：PAUSED×3（满报告 / 空报告 / 未知形态）+ RUNNING×1 + NULL 章 PAUSED×1。"""
    path = tmp_path / "full.db"
    _make_db(
        path,
        [
            {
                "run_id": "wfr_full",
                "workflow_id": "wf_write",
                "chapter_id": "ch_fixture",
                "status": "PAUSED",
                "current_node": "author_review",
                "checkpoint_json": _ckpt("author_review", _review_payload()),
                "started_at": _iso(29),
            },
            {
                "run_id": "wfr_empty",
                "workflow_id": "wf_write",
                "chapter_id": "ch_fixture",
                "status": "PAUSED",
                "current_node": "author_review",
                "checkpoint_json": _ckpt("author_review", {"stage": "chapter-review", "review_report": {}}),
                "started_at": _iso(10),
            },
            {
                "run_id": "wfr_unknown",
                "workflow_id": "wf_write",
                "chapter_id": "ch_drafted",
                "status": "PAUSED",
                "current_node": "mystery_gate",
                "checkpoint_json": _ckpt("mystery_gate", {"alpha": 1, "beta": "两段文字"}),
                "started_at": _iso(3),
            },
            {
                "run_id": "wfr_running",
                "workflow_id": "wf_write",
                "chapter_id": "ch_drafted",
                "status": "RUNNING",
                "current_node": "writer",
                "checkpoint_json": _ckpt("writer", None),
                "started_at": _iso(0.5),
                "error": "boom: transient",
            },
            {
                "run_id": "wfr_init",
                "workflow_id": "wf_init",
                "chapter_id": None,
                "status": "PAUSED",
                "current_node": "character_designer",
                "checkpoint_json": json.dumps(
                    {
                        "_current_node_id": "character_designer",
                        "run_id": "wfr_init",
                        "brief": {
                            "title": "测试项目",
                            "genre": "快穿",
                            "target_words": 1000000,
                        },
                        "character_designer": {
                            isr.PAUSE_PAYLOAD_KEY: {
                                "stage": "character",
                                "stage_index": 2,
                                "stages_total": 4,
                                "degraded": False,
                                "draft": {
                                    "schema_version": "v1",
                                    "characters": [{"name": "沈砚"}, {"name": "柳三娘"}],
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                "started_at": _iso(12),
            },
        ],
    )
    return path


# ---------------------------------------------------------------------------
# 只读性
# ---------------------------------------------------------------------------


def test_connect_is_readonly_and_rejects_writes(tmp_path: Path):
    """生产连接必须拒写——只读靠 SQLite 的 mode=ro 兜底，不靠自觉。"""
    db = tmp_path / "ro.db"
    _make_db(db, [{"run_id": "w1", "workflow_id": "wf", "chapter_id": "ch_fixture",
                   "status": "PAUSED", "current_node": "n", "checkpoint_json": None, "started_at": _iso(1)}])
    conn = isr.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM workflow_runs")
    finally:
        conn.close()
    # 数据仍在
    assert len(isr.fetch_active_runs(db)) == 1


def test_connect_uri_uses_mode_ro(tmp_path: Path):
    """URI 形态本身要被测试钉住：漏了 mode=ro 就是可写连接。"""
    db = tmp_path / "ro2.db"
    _make_db(db, [])
    conn = isr.connect_readonly(db)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 0  # 非 query_only 模式
        # 但 mode=ro 让写操作在执行期失败（见上一用例），连接建立本身成功
        assert conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 状态 / 时长 / 排序
# ---------------------------------------------------------------------------


def test_only_active_statuses_listed(full_db: Path):
    rows = isr.fetch_active_runs(full_db)
    assert {r.status for r in rows} == {"PAUSED", "RUNNING"}
    assert len(rows) == 5


def test_rows_sorted_by_started_at_and_joined(full_db: Path):
    rows = isr.fetch_active_runs(full_db)
    starts = [r.started_at for r in rows]
    assert starts == sorted(starts)
    full = rows[0]
    assert full.run_id == "wfr_full"
    assert full.project_name == "测试项目"
    assert full.chapter_number == 6
    assert full.chapter_title == "假账过户"
    assert full.chapter_status == "COMMITTED"


def test_null_chapter_run_gets_synthetic_label(full_db: Path):
    """project-init 类 run 的 chapter_id 为 NULL，章节标签不能崩也不能假装有章。

    项目名此处非 None——由 brief.title 兜底（见下一条），章节标签仍须标明「无章节」。
    """
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    assert rows["wfr_init"].chapter_id is None
    assert "无章节" in rows["wfr_init"].chapter_label
    assert rows["wfr_init"].chapter_number is None
    assert rows["wfr_init"].chapter_status is None


def test_init_run_project_name_recovered_from_brief_title(full_db: Path):
    """project-init run 无 chapter_id → JOIN 不出项目；brief.title 是唯一线索。

    少了这条兜底，dev 库那条 character_designer 卡死 run 在报告里就是
    「(项目缺失)」，主会话无从判断它属于哪本书、该不该清。
    """
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    init = rows["wfr_init"]
    assert init.project_name == "测试项目"
    assert init.project_name_source == "checkpoint_brief_title"


def test_init_project_hint_edge_cases():
    assert isr._init_project_hint(None) is None
    assert isr._init_project_hint("{bad json") is None
    assert isr._init_project_hint("[1,2]") is None
    assert isr._init_project_hint(json.dumps({"brief": "not a dict"})) is None
    assert isr._init_project_hint(json.dumps({"brief": {}})) is None
    assert isr._init_project_hint(json.dumps({"brief": {"title": "  "}})) is None
    assert isr._init_project_hint(json.dumps({"brief": {"title": 42}})) is None
    assert isr._init_project_hint(json.dumps({"brief": {"title": "  收账人  "}})) == "收账人"


def test_joined_project_name_wins_over_brief_fallback(full_db: Path):
    """有 chapter_id 时必须用 JOIN 出来的项目名，不能被 brief.title 覆盖。"""
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    assert rows["wfr_full"].project_name == "测试项目"
    assert rows["wfr_full"].project_name_source == "join"


def test_join_wins_when_brief_title_disagrees(tmp_path: Path):
    """守 MUT-10：JOIN 与 brief.title **同时存在且冲突**时，JOIN 必须赢。

    这是 MUT-10（兜底无条件覆盖 JOIN）唯一能被观察到的形状：前面的夹具里
    author_review run 根本没有 brief 键，兜底恒为 None，撤掉守卫也看不出差别。
    """
    db = tmp_path / "conflict.db"
    _make_db(
        db,
        [
            {
                "run_id": "wfr_conflict",
                "workflow_id": "wf_write",
                "chapter_id": "ch_fixture",
                "status": "PAUSED",
                "current_node": "author_review",
                "checkpoint_json": json.dumps(
                    {
                        "_current_node_id": "author_review",
                        # brief.title 故意与 projects.name 不同
                        "brief": {"title": "另一个项目", "genre": "快穿"},
                        "author_review": {isr.PAUSE_PAYLOAD_KEY: _review_payload()},
                    },
                    ensure_ascii=False,
                ),
                "started_at": _iso(5),
            }
        ],
    )
    row = isr.fetch_active_runs(db)[0]
    assert row.project_name == "测试项目", "brief.title 覆盖了 JOIN 结果"
    assert row.project_name_source == "join"


def test_fallback_project_name_is_marked_in_report(full_db: Path):
    """兜底来源必须在报告里带 * 标记——猜测依据与核对过的依据不是一回事。"""
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    md = isr.render_markdown(report)
    assert "测试项目*" in md, "checkpoint 兜底项目名未加 * 标记"
    assert "brief.title" in md, "缺少 * 标记的图例说明"
    # 有 chapter_id 的那些必须走 JOIN，不带星号
    assert "| 测试项目 | " in md, "JOIN 出来的项目名被误标为兜底"
    assert md.count("测试项目*") < md.count("测试项目")


def test_stall_duration_and_humanize(full_db: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    by_id = {r["run_id"]: r for r in report["runs"]}
    assert by_id["wfr_full"]["stalled_human"] == "29d 00h"
    assert by_id["wfr_running"]["stalled_human"] == "12h 00m"
    assert by_id["wfr_full"]["stalled_seconds"] == pytest.approx(29 * 86400)


def test_humanize_duration_formats():
    assert isr._humanize_duration(0) == "0m"
    assert isr._humanize_duration(59) == "0m"
    assert isr._humanize_duration(3660) == "1h 01m"
    assert isr._humanize_duration(86400 * 2 + 3600 * 5) == "2d 05h"
    assert isr._humanize_duration(-500) == "0m"  # 负值不外溢


def test_missing_started_at_yields_none_not_crash(tmp_path: Path):
    db = tmp_path / "nostart.db"
    _make_db(db, [{"run_id": "w1", "workflow_id": "wf", "chapter_id": "ch_fixture",
                   "status": "PAUSED", "current_node": "n", "checkpoint_json": None, "started_at": None}])
    report = isr.build_report(isr.fetch_active_runs(db), db, NOW)
    assert report["runs"][0]["stalled_seconds"] is None
    assert report["runs"][0]["stalled_human"] is None


@pytest.mark.parametrize(
    "raw,ok",
    [
        ("2026-09-27T12:00:00+00:00", True),
        ("2026-09-27T12:00:00Z", True),
        ("2026-09-27T12:00:00", True),  # naive 按 UTC
        ("not-a-time", False),
        (None, False),
        ("", False),
        ("   ", False),
    ],
)
def test_parse_ts_tolerance(raw, ok):
    assert (isr._parse_ts(raw) is not None) is ok


# ---------------------------------------------------------------------------
# 核心：审批实质内容
# ---------------------------------------------------------------------------


def test_full_review_report_yields_readable_substance(full_db: Path):
    """不是 true/false：要能读出字数、error rule_id、评审意见摘录。"""
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    s = isr.summarize_checkpoint(rows["wfr_full"].current_node, rows["wfr_full"].checkpoint_json)

    assert s.payload_found is True
    assert s.payload_kind == "chapter_review"
    assert s.has_substantive is True
    assert s.blocking_rule_ids == ["W-LEN-DEVIATION"]
    assert "AI-PRONOUN-PILE" in s.warning_rule_ids
    # 实质内容摘要必须含可读事实，不是布尔
    joined = " ".join(s.facts)
    assert "1998" in joined and "3000" in joined
    assert "超带" in joined
    assert "critic" in joined
    assert s.excerpt


def test_empty_review_report_flagged_as_no_substance(full_db: Path):
    """空报告 vs 有报告的差别，是主会话能不能安全 cancel 的依据。"""
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    s = isr.summarize_checkpoint(rows["wfr_empty"].current_node, rows["wfr_empty"].checkpoint_json)
    assert s.payload_found is True
    assert s.has_substantive is False
    assert "空报告" in s.headline
    assert s.blocking_rule_ids == []


def test_unknown_payload_not_silently_treated_as_empty(full_db: Path):
    """认不出来的形态必须报 unknown 并列键，不能假装「没内容」。"""
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    s = isr.summarize_checkpoint(rows["wfr_unknown"].current_node, rows["wfr_unknown"].checkpoint_json)
    assert s.payload_kind == "unknown"
    assert s.has_substantive is False
    assert "alpha" in s.headline and "beta" in s.headline


def test_init_stage_payload_lists_entity_names(full_db: Path):
    rows = {r.run_id: r for r in isr.fetch_active_runs(full_db)}
    s = isr.summarize_checkpoint(rows["wfr_init"].current_node, rows["wfr_init"].checkpoint_json)
    assert s.payload_kind == "project_init_stage"
    assert s.has_substantive is True
    joined = " ".join(s.facts)
    assert "沈砚" in joined and "柳三娘" in joined
    assert "2/4" in joined


def test_degraded_init_stage_is_flagged():
    payload = {
        "stage": "character", "stage_index": 1, "stages_total": 4, "degraded": True,
        "draft": {"characters": [{"name": "甲"}]},
    }
    s = isr.summarize_pause_payload("character_designer", payload)
    assert s.has_substantive is True
    assert "降级产出" in " ".join(s.facts)


def test_missing_checkpoint_reports_readable_message():
    s = isr.summarize_checkpoint("author_review", None)
    assert s.payload_found is False
    assert s.has_substantive is False
    assert "checkpoint_json 为空" in s.headline




def test_checkpoint_without_node_state_reports_node_name():
    s = isr.summarize_checkpoint("author_review", json.dumps({"chapter_id": "ch_x"}))
    assert s.payload_found is False
    assert "author_review" in s.headline


def test_non_dict_checkpoint_tolerated():
    s = isr.summarize_checkpoint("n", json.dumps([1, 2, 3]))
    assert s.payload_kind == "unparseable"




def test_string_typed_error_rules_are_parsed():
    """真实库里 errors 可能是 ``[RULE] msg`` 字符串形态，rule_id 不能丢。"""
    report = _review_payload(errors=["[CONT-STATE-LEAK] 状态泄漏", "[W-LEN] 欠带"])
    s = isr.summarize_pause_payload("author_review", report)
    assert s.blocking_rule_ids == ["CONT-STATE-LEAK", "W-LEN"]


def test_mixed_shape_errors_all_contribute_rule_ids():
    """多条混合形态：任一条被跳过都会丢 rule_id——不能只测单条。

    突变验证 MUT-7（dict 分支里提前 return）就是靠这一条被杀掉的：单元素列表
    下提前 return 与正常 return 等价，必须有多元素 + 异构才能暴露。
    """
    report = _review_payload(
        errors=[
            {"rule_id": "A-RULE", "message": "a"},
            {"message": "[B-FROM-MESSAGE] b"},          # 无 rule_id，走 message 兜底
            "[C-STRING] c",                             # 字符串形态
            "没有方括号的自由文本",                       # 兜底短标签
            {"no_message_no_rid": 1},                   # 兜底占位
        ]
    )
    s = isr.summarize_pause_payload("author_review", report)
    assert s.blocking_rule_ids == ["A-RULE", "B-FROM-MESSAGE", "C-STRING", "没有方括号的自由文本", "(未标 rule_id)"]


def test_rule_ids_dedup_preserving_order():
    report = _review_payload(errors=["[DUP] x", "[DUP] y", "[OTHER] z"])
    s = isr.summarize_pause_payload("author_review", report)
    assert s.blocking_rule_ids == ["DUP", "OTHER"]


def test_rule_ids_from_non_list_returns_empty():
    assert isr._rule_ids_from(None) == []
    assert isr._rule_ids_from({"not": "a list"}) == []
    assert isr._rule_ids_from([]) == []


def test_markdown_carries_substantive_facts_not_just_booleans(tmp_path: Path):
    """守 MUT-6：渲染层把 facts 全删掉只留 has_substantive 布尔，报告就废了。

    巡检的产物是「可读实质内容摘要」，渲染层是它的交付面；这一条把两层钉在一起。
    """
    db = _sub_db(tmp_path)
    report = isr.build_report(isr.fetch_active_runs(db), db, NOW)
    md = isr.render_markdown(report)
    # 概览表里也出现 run_id，必须切到第 2 节再取块，否则测的是概览表不是实质内容段
    section2 = md.split("## 2. 逐 run 待审批实质内容", 1)[1].split("## 3.", 1)[0]
    block = section2.split("wfr_full", 1)[1].split("###", 1)[0]

    assert "1998" in block, "字数事实未渲染"
    assert "3000" in block, "目标字数未渲染"
    assert "W-LEN-DEVIATION" in block, "error rule_id 未渲染"
    assert "AI-PRONOUN-PILE" in block, "warning rule_id 未渲染"
    assert "critic" in block, "critic 报告未渲染"
    assert "评审意见摘录" in block, "评审意见摘录段缺失"
    assert "超带" in block, "超带告警未渲染"
    # 不能只剩一个布尔
    assert "has_substantive=" not in block
    # 空报告那个 run 必须显式区别对待
    empty_block = section2.split("wfr_empty", 1)[1].split("###", 1)[0]
    assert "空报告" in empty_block


def test_export_carries_substantive_facts(tmp_path: Path):
    """守 MUT-6 的 JSON 面：--export 里的 pause_summary 必须带可读事实。"""
    db = _sub_db(tmp_path)
    report = isr.build_report(isr.fetch_active_runs(db), db, NOW)
    out = tmp_path / "e.json"
    isr.export_rows(report, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    s = data["runs"][0]["pause_summary"]
    assert s["facts"], "facts 为空——导出留档失去可读性"
    assert s["blocking_rule_ids"] == ["W-LEN-DEVIATION"]
    assert s["excerpt"]


def test_export_inside_repo_warns_about_content_leak(tmp_path: Path, monkeypatch, capsys):
    """导出到工作区内要提醒：内容是正文草稿，进版本库就混了三仓。"""
    repo_stub = tmp_path / "repo"
    (repo_stub / "scripts").mkdir(parents=True)
    (repo_stub / "scripts" / "inspect_stalled_runs.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(isr, "__file__", str(repo_stub / "scripts" / "inspect_stalled_runs.py"))

    isr.warn_if_inside_repo(repo_stub / "tmp" / "export.json")
    err = capsys.readouterr().err
    assert "WARNING" in err and "工作区内" in err
    assert "三仓" in err or "git add -A" in err


def test_export_outside_repo_does_not_warn(tmp_path: Path, monkeypatch, capsys):
    repo_stub = tmp_path / "repo"
    (repo_stub / "scripts").mkdir(parents=True)
    (repo_stub / "scripts" / "inspect_stalled_runs.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(isr, "__file__", str(repo_stub / "scripts" / "inspect_stalled_runs.py"))

    outside = Path(tempfile.gettempdir()) / "novelos_stalled_export_probe.json"
    isr.warn_if_inside_repo(outside)
    assert "WARNING" not in capsys.readouterr().err


def test_critic_only_payload_still_substantive():
    """review_report 缺失但 critic 有 issues ⇒ 仍是有实质内容。"""
    s = isr.summarize_pause_payload(
        "author_review",
        {"stage": "chapter-review", "critic_status": "ok",
         "critic_report": {"issues": [{"category": "pacing"}], "overall_comment": "中段拖沓"}},
    )
    assert s.has_substantive is True
    assert "1 条" in " ".join(s.facts)




def test_review_report_wins_over_init_branch_when_stage_present():
    """stage=chapter-review 且带 review_report 时不能被 project-init 分支抢走。"""
    s = isr.summarize_pause_payload(
        "author_review",
        {"stage": "chapter-review", "stage_index": 1, "stages_total": 2,
         "review_report": {"word_count": 10, "target_word_count": 20}},
    )
    assert s.payload_kind == "chapter_review"


# ---------------------------------------------------------------------------
# 守卫口径：PAUSED 不阻止新 start、cancel 不接受 PAUSED
# ---------------------------------------------------------------------------


def test_paused_run_does_not_block_new_start(full_db: Path):
    """与「PAUSED 存在 ⇒ 新 start 409」的错误说法冲突，必须钉住真实谓词。"""
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    by_id = {r["run_id"]: r for r in report["runs"]}
    assert by_id["wfr_full"]["blocks_new_start"] is False
    assert by_id["wfr_running"]["blocks_new_start"] is True


def test_chapters_blocking_list_only_contains_running_chapter(full_db: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    assert report["chapters_blocking_new_start"] == ["ch_drafted"]
    ch_fixture = next(c for c in report["chapters"] if c["chapter_id"] == "ch_fixture")
    assert ch_fixture["run_count"] == 2
    assert ch_fixture["blocks_new_start"] is False
    assert ch_fixture["any_substantive"] is True  # 满报告那个


def test_null_chapter_never_blocks_new_start(full_db: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    none_group = next(c for c in report["chapters"] if c["chapter_id"] is None)
    assert none_group["blocks_new_start"] is False


@pytest.mark.parametrize("status,supported", [
    ("RUNNING", True), ("PAUSED", False), ("PENDING", False),
    ("COMPLETED", False), ("FAILED", False), ("CANCELLED", False),
])
def test_cancel_api_supported_matches_endpoint_semantics(status, supported):
    """control.py:780-781 / engine.py:644-646：cancel 只接受 RUNNING。"""
    assert isr.cancel_api_supported(status) is supported


def test_guard_semantics_block_published():
    report = isr.build_report([], Path("x.db"), NOW)
    gs = report["guard_semantics"]
    assert gs["predicate"] == "status IN ('RUNNING','PENDING')"
    assert gs["paused_blocks_new_start"] is False
    assert gs["cancel_accepts_paused"] is False
    assert "_check_active_run_for_chapter" in gs["source"]
    assert "0017" in gs["db_index"]


def test_blocking_predicate_constant_excludes_paused():
    assert "PAUSED" not in isr.BLOCKING_STATUSES
    assert set(isr.BLOCKING_STATUSES) == {"RUNNING", "PENDING"}


# ---------------------------------------------------------------------------
# 导出 / apply 闸
# ---------------------------------------------------------------------------


def test_export_contains_full_checkpoint_json(full_db: Path, tmp_path: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    out = tmp_path / "nested" / "export.json"
    isr.export_rows(report, out)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["read_only"] is True
    assert data["total_active_runs"] == 5
    by_id = {r["run_id"]: r for r in data["runs"]}
    ck = json.loads(by_id["wfr_full"]["checkpoint_json"])
    assert "review_report" in ck["author_review"][isr.PAUSE_PAYLOAD_KEY]
    assert by_id["wfr_full"]["pause_summary"]["blocking_rule_ids"] == ["W-LEN-DEVIATION"]


def test_apply_raises_not_implemented(full_db: Path):
    """apply 分支必须显式拒绝，不得存在任何写路径。"""
    rows = isr.fetch_active_runs(full_db)
    with pytest.raises(NotImplementedError) as exc:
        isr.apply_cleanup(rows, confirmed_by="主会话")
    assert "另行决策" in str(exc.value)


def test_apply_flag_exits_3_and_refuses(full_db: Path, capsys):
    assert isr.main(["--db", str(full_db), "--apply"]) == 3
    assert "REFUSED" in capsys.readouterr().err
    # 数据未变
    assert len(isr.fetch_active_runs(full_db)) == 5


def test_main_missing_db_exits_2(tmp_path: Path, capsys):
    assert isr.main(["--db", str(tmp_path / "nope.db")]) == 2
    assert "DB 不存在" in capsys.readouterr().err


def test_main_json_and_markup_on_empty_db(tmp_path: Path, capsys):
    db = tmp_path / "empty.db"
    _make_db(db, [])
    assert isr.main(["--db", str(db), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"total_active_runs": 0' in out
    json.loads(out)

    assert isr.main(["--db", str(db)]) == 0
    assert "无活动 run" in capsys.readouterr().out


def test_markdown_renders_all_required_columns(full_db: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    md = isr.render_markdown(report)
    for needle in ("停留", "有实质内容", "阻止新 start", "逐 run 待审批实质内容", "409"):
        assert needle in md
    assert "wfr_full" in md and "wfr_init" in md
    # 空报告那个必须显式标出，否则主会话无从区分
    assert "否（空）" in md


def test_error_summary_truncated_in_report(full_db: Path):
    report = isr.build_report(isr.fetch_active_runs(full_db), full_db, NOW)
    running = next(r for r in report["runs"] if r["run_id"] == "wfr_running")
    assert running["error"] == "boom: transient"
    assert running["retry_count"] == 0
