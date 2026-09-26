"""scripts/produce_chapters.py 单测（纯函数：选择解析 / 重复率 / 评审决议 / 前向兼容）。

覆盖任务书给死的四条判据：

1. 章节选择解析（``--chapters 1-3,7``）合法与非法形态；
2. 章内重复率算子（13 字 shingle，去空白）——用**实机 fixture**：
   - 重复段落（真实 run 里在同一章出现两次）⇒ 命中拒批阈值；
   - 中性正文（288 可见字）⇒ 近 0；
3. 评审报告决议函数：无 error ⇒ 批准；只有可定向改稿的规则 ⇒ 改稿；
   未知 / 缺 rule_id ⇒ 硬停；**重复率超阈值 ⇒ 拒绝批准**（本脚本存在的理由）；
4. confirm 档门禁的前向兼容识别（硬停、不重试、不代填 override）。

另钉住两条实现口径：
- 重复率算子与 ``packages/core/quality/ai_trace.py:intra_chapter_repetition`` **同口径**
  （脚本内是本地实现，此处的交叉断言是「口径没漂」的看守）；
- 占比 / 频率类指标（如 ``AI-DIALOGUE-LOW``）**不在**可定向改稿集合内（F-19）。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.produce_chapters as pc  # noqa: E402

# ---------------------------------------------------------------------------
# fixture：任务书给死的两条实机字符串
# ---------------------------------------------------------------------------

# 真实 run 里**在同一章出现两次**的段落（dev 库 v6 草稿实测：该章 2374 字、
# 13 字 shingle 重复率 0.2034；单独两次成文时 130 字 → 0.4492）。
DUPLICATED_PARAGRAPH = (
    "茶壶里的水是温的，杯盏里的已经凉透，瓷器抵在嘴唇上带着一股沁凉。"
    "沈砚坐在这间包间的里侧，背靠墙，门在他身后，有人进来他第一个知道。"
)

# 中性正文（288 可见字，自己写的一段干净散文，13 字 shingle 重复率实测 0.0）。
CLEAN_PROSE = (
    "沈砚把最后一叠账册塞进木箱，箱盖压下去的时候发出一声闷响。"
    "\n\n"
    "窗外有人推着板车经过，车轮碾过青石板，颤了三下才停住。"
    "\n\n"
    "他伸手把灯芯拨短，屋里暗了下来，只剩桌角那一小圈黄。"
    "\n\n"
    "账房先生早上交来的钥匙还搁在袖袋里，铜齿硌着腕子，提醒他这处铺面已经换了东家。"
    "\n\n"
    "更鼓从街尾传过来，隔得不近，倒听得清楚。"
    "\n\n"
    "柜台底下压着一只缺口的白瓷碟，是前日收进来的抵账物，上头粘了半圈没洗净的酱色。"
    "\n\n"
    "他把碟子翻过来看了看底款，又放回原处，没有作声。"
    "\n\n"
    "后院的石榴树结得稀，枝杈伸过墙头，遮掉了对面粮行的半块招牌。"
    "\n\n"
    "学徒抱着扫帚在门槛边打盹，被脚步声惊醒，慌忙站直了身子。"
    "\n\n"
    "巷子里传来卖馄饨的吆喝，混着柴烟味，一路飘到了檐下。"
)


# ---------------------------------------------------------------------------
# 1. 章节选择解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("7", [7]),
        ("1-3,7", [1, 2, 3, 7]),
        ("3,1,2-3", [1, 2, 3]),
        (" 1 , 2 - 4 ", [1, 2, 3, 4]),
        ("5-5", [5]),
    ],
)
def test_parse_chapter_selection_ok(spec, expected):
    assert pc.parse_chapter_selection(spec) == expected


@pytest.mark.parametrize("spec", ["0", "-1", "1-", "-3", "3-1", "1,,2", "a", "1-a", "1-2-3", "1.5"])
def test_parse_chapter_selection_rejects_bad_input(spec):
    """坏输入一律抛错（不做「跳过坏项继续」的宽容解析）。"""
    with pytest.raises(ValueError):
        pc.parse_chapter_selection(spec)


def test_planned_stages_by_status():
    """按状态可续跑：已 COMMITTED 不重跑；REVIEWED 只补 commit。"""
    assert pc.planned_stages("PLANNED") == ["plan", "write", "review", "commit"]
    assert pc.planned_stages("DRAFTED") == ["review", "commit"]
    assert pc.planned_stages("REVIEWED") == ["commit"]
    assert pc.planned_stages("COMMITTED") == []
    assert pc.planned_stages("RELEASED") == []


# ---------------------------------------------------------------------------
# 2. 章内重复率
# ---------------------------------------------------------------------------


def test_repetition_ratio_duplicated_paragraph_exceeds_threshold():
    """重复段落成文 ⇒ 远超 8% 阈值（实测 0.4492）。"""
    ratio = pc.shingle_repetition_ratio(f"{DUPLICATED_PARAGRAPH}\n\n{DUPLICATED_PARAGRAPH}")
    assert ratio > pc.DEFAULT_REPETITION_THRESHOLD
    assert ratio == pytest.approx(0.4492, abs=1e-3), ratio


def test_repetition_ratio_clean_prose_is_near_zero():
    ratio = pc.shingle_repetition_ratio(CLEAN_PROSE)
    assert ratio < pc.DEFAULT_REPETITION_THRESHOLD
    assert ratio == pytest.approx(0.0, abs=1e-6), ratio


def test_repetition_ratio_ignores_whitespace_and_short_text():
    """去空白归一化（换行 / 空格不改变结果）；短于窗口 ⇒ 0。"""
    assert pc.shingle_repetition_ratio("一二三四五六") == 0.0
    assert pc.shingle_repetition_ratio("") == 0.0
    spaced = f"{DUPLICATED_PARAGRAPH}\n\n{DUPLICATED_PARAGRAPH}"
    assert pc.shingle_repetition_ratio(spaced) == pc.shingle_repetition_ratio(
        spaced.replace("\n", " ")
    )


def test_repetition_ratio_matches_living_ai_trace_caliber():
    """口径看守：与 quality.ai_trace.intra_chapter_repetition 的 ratio 逐值相等。

    脚本内是**本地实现**（不被并发改动的包牵着走）；本断言是「两边口径没漂」的
    探测——若此测试变红，先确认是 ai_trace 的窗口 / 归一化口径改了。
    """
    from packages.core.quality.ai_trace import intra_chapter_repetition

    for text in (DUPLICATED_PARAGRAPH * 2, CLEAN_PROSE):
        expected, _deduct = intra_chapter_repetition(text)
        assert pc.shingle_repetition_ratio(text) == pytest.approx(expected, abs=0.0), text[:20]


# ---------------------------------------------------------------------------
# 3. 评审报告 → 决议
# ---------------------------------------------------------------------------


def _report(**over):
    base = {
        "draft_version": 1,
        "word_count": 2500,
        "target_word_count": 2500,
        "within_range": True,
        "warnings": [],
        "errors": [],
    }
    base.update(over)
    return base


def test_decide_review_approves_clean_report():
    decision = pc.decide_review(_report(), CLEAN_PROSE)
    assert decision.kind == "approve"
    assert decision.reason == "no_review_errors"
    assert decision.repetition_ratio < pc.DEFAULT_REPETITION_THRESHOLD


def test_decide_review_revises_revisable_errors():
    """只有可定向改稿的规则 ⇒ 改稿，并给出该规则的定向意见。"""
    errors = [
        {"rule_id": "W-LEN-DEVIATION", "severity": "error", "deviation_pct": -24.0,
         "message": "字数欠带"},
        {"rule_id": "AI-PUNCT-ABUSE", "severity": "error", "message": "破折号过多"},
    ]
    decision = pc.decide_review(_report(errors=errors, within_range=False), CLEAN_PROSE)
    assert decision.kind == "revise"
    assert decision.rule_ids == ["W-LEN-DEVIATION", "AI-PUNCT-ABUSE"]
    assert "字数欠带" in decision.note and "破折号" in decision.note


def test_decide_review_stops_on_unknown_rule():
    decision = pc.decide_review(
        _report(errors=[{"rule_id": "RULE_SOMETHING_NEW", "severity": "error"}]), CLEAN_PROSE
    )
    assert decision.kind == "stop"
    assert decision.reason == "non_revisable_rules"
    assert "RULE_SOMETHING_NEW" in decision.note


def test_decide_review_stops_on_error_without_rule_id():
    decision = pc.decide_review(_report(errors=[{"severity": "error", "message": "无 id"}]), CLEAN_PROSE)
    assert decision.kind == "stop"
    assert decision.reason == "error_without_rule_id"


def test_decide_review_refuses_duplicated_draft_even_without_errors():
    """**本脚本存在的理由**：报告无 error，但草稿自身重复 ⇒ 拒绝批准。"""
    decision = pc.decide_review(
        _report(), f"{DUPLICATED_PARAGRAPH}\n\n{DUPLICATED_PARAGRAPH}"
    )
    assert decision.kind == "stop"
    assert decision.reason == "repetition_exceeds_threshold"
    assert decision.repetition_ratio > pc.DEFAULT_REPETITION_THRESHOLD
    assert "拒绝批准" in decision.note


def test_decide_review_threshold_is_strict_greater_than():
    """边界语义：比值 == 阈值不算超（严格大于才拒）。"""
    assert pc.decide_review(_report(), CLEAN_PROSE, repetition_threshold=0.0).kind == "approve"
    dup = f"{DUPLICATED_PARAGRAPH}\n\n{DUPLICATED_PARAGRAPH}"
    ratio = pc.shingle_repetition_ratio(dup)
    assert pc.decide_review(_report(), dup, repetition_threshold=ratio).kind == "approve"
    assert pc.decide_review(_report(), dup, repetition_threshold=ratio - 1e-9).kind == "stop"


def test_proportion_metrics_are_not_in_revisable_set():
    """F-19 纪律：占比 / 频率类指标不得作为「定向改稿」处方（会驱动模型灌水）。"""
    assert "AI-DIALOGUE-LOW" not in pc.REVISABLE_RULE_IDS
    decision = pc.decide_review(
        _report(errors=[{"rule_id": "AI-DIALOGUE-LOW", "severity": "error"}]), CLEAN_PROSE
    )
    assert decision.kind == "stop"


def test_build_revision_note_length_is_bidirectional():
    """字数意见必须双向：超限给压缩口径，欠带给扩写口径（防乒乓球）。"""
    over = pc.build_revision_note(
        [{"rule_id": "W-LEN-DEVIATION", "deviation_pct": 30.0}]
    )
    under = pc.build_revision_note(
        [{"rule_id": "W-LEN-DEVIATION", "deviation_pct": -30.0}]
    )
    assert "超限" in over and "压缩" in over
    assert "欠带" in under and "扩写" in under
    assert over != under


# ---------------------------------------------------------------------------
# 4. confirm 档门禁的前向兼容（硬停）
# ---------------------------------------------------------------------------


def test_confirm_tier_failure_detected_from_real_gate_error():
    """真实 quality_gate confirm 档错误串（chapter_commit/gate.py 的 raise 形态）。"""
    err = (
        "quality gate blocked: ['RULE_STYLE_REPETITION_TRIGRAM'] | gate=confirm"
        " | override_error=未提供 gate_override | evidence=RULE_STYLE_REPETITION_TRIGRAM: 复沓"
        " | guidance=[]"
    )
    assert pc.is_confirm_tier_failure(err)
    assert pc.is_confirm_tier_failure(
        "quality gate blocked: ['AI-BEAT-REPEAT'] | gate=confirm | evidence=… | guidance=[]"
    )
    # 触发器的另一种形态：错误里只有 rule_id（无 gate=confirm 字面）也应被认出。
    assert pc.is_confirm_tier_failure("commit rejected: AI-BEAT-REPEAT")


def test_confirm_tier_failure_ignores_other_errors():
    assert not pc.is_confirm_tier_failure("quality gate blocked: ['RULE_STYLE_POV'] | guidance=[]")
    assert not pc.is_confirm_tier_failure("database is locked")
    assert not pc.is_confirm_tier_failure(None)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("database is locked", True),
        ("integrity error: FOREIGN KEY constraint failed", True),
        ("malformed delta: before=None", True),
        ("quality gate blocked: ['RULE_STYLE_POV'] | guidance=[]", False),
        ("chapter status must be REVIEWED to commit", False),
    ],
)
def test_transient_commit_failure_classification(message, expected):
    assert pc.is_transient_commit_failure(message) is expected


def test_409_only_retried_when_known_race():
    """只有「同章已有活跃 run」这种已知竞态才退避重试；别的 409 立即硬停。"""
    assert pc.is_transient_conflict(
        "chapter 'ch_x' already has an active workflow run (run_id='run_1', status='RUNNING'); "
        "wait for it to reach a terminal state before starting a new one"
    )
    assert not pc.is_transient_conflict(
        "最新草稿 v6 晚于最近一次审校完成时间（2026-09-18T00:00:00Z），存在未审改动，请先重新审校再提交"
    )


def test_pause_kind_classification():
    assert pc.pause_kind({"stage": "chapter-review"}) == "author_review"
    assert pc.pause_kind({"stage": "chapter-commit.high_risk_approval"}) == "risk_gate"
    assert pc.pause_kind({"stage": "some-new-human-node"}) == "unknown"
    assert pc.pause_kind(None) == "unknown"
    assert pc.pause_kind({}) == "unknown"


# ---------------------------------------------------------------------------
# mock_providers 装载（离线 / E2E 用）
# ---------------------------------------------------------------------------


def test_load_mock_providers_roundtrip(tmp_path: Path):
    path = tmp_path / "mocks.json"
    path.write_text(json.dumps({"writer": ['{"a":1}']}), encoding="utf-8")
    assert pc.load_mock_providers(path) == {"writer": ['{"a":1}']}


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"writer": "not-a-list"}', '{"writer": [1, 2]}', "{not json"],
)
def test_load_mock_providers_rejects_bad_shapes(tmp_path: Path, payload: str):
    path = tmp_path / "mocks.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        pc.load_mock_providers(path)


# ---------------------------------------------------------------------------
# 落定循环（守卫 4：风控门有界自动批准；resume 失败不炸进程）
# ---------------------------------------------------------------------------


class _FakeApi:
    """脚本化的假 API：按顺序吐出 run 状态，记录所有 POST。"""

    def __init__(self, run_states: list[dict], *, resume_codes: list[int] | None = None) -> None:
        self.base_url = "http://fake"
        self._states = list(run_states)
        self._resume_codes = list(resume_codes or [])
        self.posts: list[tuple[str, dict]] = []

    def _current(self) -> dict:
        return self._states[0] if len(self._states) == 1 else self._states.pop(0)

    def get(self, path: str):  # noqa: ANN201 - 鸭子类型替身
        assert path.startswith("/api/runs/")
        return 200, self._current()

    def post(self, path: str, json_body: dict | None = None):  # noqa: ANN201
        self.posts.append((path, json_body or {}))
        code = self._resume_codes.pop(0) if self._resume_codes else 200
        return code, {"run_id": "run_x", "status": "RUNNING"}


def _producer(api, **overrides) -> pc.ChapterProducer:
    args = pc.build_parser().parse_args(["--project", "prj_x", "--apply"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return pc.ChapterProducer(api, args, log_stream=io.StringIO())


def _paused_run(stage: str, **extra) -> dict:
    payload = {"stage": stage, "message": "msg", **extra}
    return {"run_id": "run_x", "status": "PAUSED", "pause_payload": payload}


def test_wait_settled_auto_approves_risk_gate_within_cap():
    """风控门 PAUSE ⇒ 自动批准一次，载荷进 auto_approvals（可审计）。"""
    api = _FakeApi([
        _paused_run("chapter-commit.high_risk_approval", delta_id="delta_1",
                    changes={"character_changes": [1, 2], "world_changes": []}),
        {"run_id": "run_x", "status": "COMPLETED"},
    ])
    rec = pc.ChapterOutcome(number=1)
    run = _producer(api, max_auto_approve=3).wait_settled(
        "run_x", rec, timeout=5.0, auto_approve=True
    )
    assert run is not None and run["status"] == "COMPLETED"
    assert rec.auto_approve_rounds == 1
    assert rec.auto_approvals[0]["delta_id"] == "delta_1"
    assert rec.auto_approvals[0]["changes"] == {"character_changes": 2, "world_changes": 0}
    assert api.posts[0][1] == {"human_input": {"approved": True}}
    assert api.posts[0][0] == "/api/runs/run_x/resume"


def test_wait_settled_risk_gate_respects_cap():
    """轮次用尽 ⇒ 交回人工（不无限批准），返回 PAUSED run。"""
    api = _FakeApi([_paused_run("chapter-commit.high_risk_approval", delta_id="delta_1")])
    rec = pc.ChapterOutcome(number=1)
    run = _producer(api, max_auto_approve=0).wait_settled(
        "run_x", rec, timeout=5.0, auto_approve=True
    )
    assert run is not None and run["status"] == "PAUSED"
    assert rec.auto_approve_rounds == 0
    assert api.posts == []


def test_wait_settled_never_auto_approves_author_review():
    """作者决议节点（chapter-review）**绝不**自动批准——那是本脚本的红线。"""
    api = _FakeApi([_paused_run("chapter-review", review_report={"errors": []})])
    rec = pc.ChapterOutcome(number=1)
    run = _producer(api, max_auto_approve=5).wait_settled(
        "run_x", rec, timeout=5.0, auto_approve=True
    )
    assert run is not None and run["status"] == "PAUSED"
    assert api.posts == []


def test_wait_settled_never_auto_approves_unknown_human_node():
    """未知人工节点：不猜、不批准。"""
    api = _FakeApi([_paused_run("some-new-stage")])
    rec = pc.ChapterOutcome(number=1)
    run = _producer(api).wait_settled("run_x", rec, timeout=5.0, auto_approve=True)
    assert run is not None and run["status"] == "PAUSED"
    assert api.posts == []


def test_wait_settled_survives_resume_409():
    """resume 撞 409（run 已不在 PAUSED）不许炸进程，由轮询兜底到真实终态。"""
    api = _FakeApi(
        [
            _paused_run("chapter-commit.high_risk_approval", delta_id="delta_1"),
            {"run_id": "run_x", "status": "FAILED", "error": "observer crashed"},
        ],
        resume_codes=[409],
    )
    rec = pc.ChapterOutcome(number=1)
    run = _producer(api).wait_settled("run_x", rec, timeout=5.0, auto_approve=True)
    assert run is not None and run["status"] == "FAILED"
    assert rec.auto_approve_rounds == 1  # 批准动作已留痕（HTTP 409 也记）


# ---------------------------------------------------------------------------
# CLI 参数层（不触网：这些错误在连服务之前就该返回 exit 1）
# ---------------------------------------------------------------------------


def test_cli_rejects_conflicting_selection_flags():
    assert pc.main(["--project", "prj_x", "--chapters", "1", "--only", "2"]) == pc.EXIT_USAGE


def test_cli_rejects_bad_selection_expression():
    assert pc.main(["--project", "prj_x", "--chapters", "1-0"]) == pc.EXIT_USAGE


def test_cli_rejects_bad_threshold_and_rounds():
    assert pc.main(["--project", "prj_x", "--repetition-threshold", "1.5"]) == pc.EXIT_USAGE
    assert pc.main(["--project", "prj_x", "--max-revise-rounds", "-1"]) == pc.EXIT_USAGE
    assert pc.main(["--project", "prj_x", "--max-auto-approve", "-1"]) == pc.EXIT_USAGE


def test_cli_rejects_missing_mock_file(tmp_path: Path):
    missing = tmp_path / "nope.json"
    assert pc.main(["--project", "prj_x", "--mock-providers", str(missing)]) == pc.EXIT_USAGE
