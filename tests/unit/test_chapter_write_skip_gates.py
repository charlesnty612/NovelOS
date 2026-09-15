"""P0 提速门单测：condense 压缩预算门 + polisher 条件触发预检。

两门都是「跳过一次 LLM 往返」的确定性判定，判据全部复用既有检测器 / 既有阈值：

A. condense（``_condense_node``）—— 判据 ``_CONDENSE_PAYLOAD_TRIGGER_RATIO`` × band_high：
   1. 欠字 payload（status='under'，2000 < band_high 3449）→ ``skipped_below_budget``，
      ``condense_skipped=True`` + 原因含 payload 尺寸与阈值，零 LLM 调用、不计轮；
   2. 阈值算术边界（report 陈旧形态）：payload == band_high → 跳过；
      band_high + 1 → 门放行，仍走原压缩路径（mock 产出落带）；
   3. 跳过路径下游兼容：引擎 ctx.update 语义下 ``save_draft`` 落库 = 原 prose，
      plan_json.deviations 不新增（rounds=0，与既有注记条件一致）。

B. polisher（``_polish_node`` 预检三源：scan_ai_patterns / 题材禁词 / req_q7）：
   4. 三源零命中 → ``skipped_clean``，零 LLM 调用，``polish_precheck`` 统计全零且 JSON 可序列化；
   5. AI 模式命中（忽然）→ 走原润色路径（run_agent 被调、ai_findings 原样进 payload）；
   6. 题材禁词命中（题材包 ``style_constraints.forbidden_words``）→ 走原润色路径；
   7. 句长标准差 < ``Q7_FLAT_SENTENCE_STD``（文本 ≥1000 字）→ 走原润色路径；
   8. mock 直通优先级不变（writer-only mock 流仍 passthrough）；
   9. 跳过路径下游兼容：save_draft 落库 = writer prose。

突变验证（撤门必红，主会话复验口径）：
- ``_CONDENSE_PAYLOAD_TRIGGER_RATIO`` 改 0.0（门失效）→ 用例 1 / 2 红；
- 预检门条件撤 q7 腿 → 用例 7 红；撤题材禁词腿 → 用例 6 红；撤 AI 腿 → 用例 5 红。

mock 策略：两门都只在「生产形态」（``mock_providers`` 为 None / {}）或「配了对应 mock
脚本」时生效，故用例统一传 ``mock_providers=None`` 并用 monkeypatch 替换
``cw_pipeline.run_agent``——既断言调用与否，也不触发真实 provider。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("NOVELOS_CONTEXT_RELEVANCE", "off")

import packages.workflows.chapter_write.pipeline as cw_pipeline  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.genre import GenrePackCreate, GenrePackService  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.core.quality.wordcount import (  # noqa: E402
    classify_prose_length,
    visible_chars,
    word_band,
)
from packages.workflows.chapter_write.pipeline import (  # noqa: E402
    _condense_node,
    _polish_node,
    _polish_precheck,
    _save_draft_node,
)

MIGRATIONS_DIR = ROOT / "database" / "migrations"

_TARGET = 3000
# F-2 口径：band 数值一律由权威口径生成（target=3000 的 band_high = int(3000*1.15) = 3449）。
_BAND_LOW, _BAND_HIGH = word_band(_TARGET)

# 预检三源全零命中：无 AI 模式词 / 无禁词（<1000 字也不触发句长腿）。
_CLEAN_PROSE = "灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


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
            "status, created_at, updated_at) VALUES (?, 'p', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    plan_json = json.dumps({"expected_word_count": _TARGET, "key_beats": []})
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 't', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _create_genre_pack(db_path: Path, *, pack_id: str, forbidden: list[str]) -> None:
    """建题材包（payload 结构照既有 P1b 测试的已知合法骨架，仅换禁词表）。"""
    GenrePackService(db_path).create(
        GenrePackCreate(
            name="门测试题材包",
            genre_tag="测试",
            pack_id=pack_id,
            payload={
                "schema_version": "genre-pack.v1.0.0",
                "payoff_types": [
                    {
                        "type_id": "face_slap",
                        "name": "打脸",
                        "strength": "S",
                        "density_cap": "每卷 2~3 次",
                        "min_interval_chapters": 3,
                    }
                ],
                "style_constraints": {"forbidden_words": list(forbidden)},
            },
        )
    )


def _boom_runner(calls: list[int]) -> Callable[..., dict]:
    """被调用即失败（并计数）：用于「该路径必须零 LLM 调用」断言。"""

    def _boom(*args: Any, **kwargs: Any) -> dict:
        calls.append(1)
        raise AssertionError("该路径不应调用 LLM（run_agent）")

    return _boom


def _echo_runner(captured: list[dict]) -> Callable[..., dict]:
    """记录调用并原样回传 draft_text（长度守恒 Δ0 → polisher_status='ok'）。"""

    def _echo(db_path, agent_name, payload, run_id, **kw):
        captured.append({"agent_name": agent_name, "payload": payload, "kwargs": kw})
        return {"polished_text": payload["draft_text"], "changes_summary": "noop"}

    return _echo


def _condense_ctx(db_path: Path, chapter_id: str, prose: str, **overrides: Any) -> dict[str, Any]:
    """condense 节点 ctx（report 由权威口径生成；mock_providers=None = 生产形态）。"""
    report = classify_prose_length(prose, _TARGET)
    report["within_band"] = report["status"] == "in_band"
    ctx: dict[str, Any] = {
        "db_path": db_path,
        "chapter_id": chapter_id,
        "run_id": "run_gate",
        "polished_prose": prose,
        "length_check_passed": report["within_band"],
        "length_report": report,
        "condense_rounds": 0,
        "mock_providers": None,
    }
    ctx.update(overrides)
    return ctx


def _polish_ctx(db_path: Path, chapter_id: str, prose: str, **overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "db_path": db_path,
        "chapter_id": chapter_id,
        "run_id": "run_gate",
        "writer_output": {"prose": prose, "prompt_version": "writer:v1"},
        "mock_providers": None,
    }
    ctx.update(overrides)
    return ctx


def _flat_sentence_prose() -> str:
    """句长完全均一（std=0 < Q7_FLAT_SENTENCE_STD=3）且 ≥1000 字。

    交替两种 8 字句：避开「三连同一开头」（AI-TRIPLET-OPENING）与「他/她」排比
    （AI-PRONOUN-PILE），也不含禁用词 / 解释腔 / marker —— 保证预检只有 req_q7
    的句长腿命中（另两腿在用例内自证为零）。
    """
    a = "院子里没有声响。"
    b = "苏婉清抬眼看钟。"
    return "".join(a if i % 2 == 0 else b for i in range(126))


# ---------------------------------------------------------------------------
# A. condense 压缩预算门
# ---------------------------------------------------------------------------


def test_condense_budget_gate_skips_under_band_payload(tmp_path: Path, monkeypatch):
    """欠字 payload（status='under'）→ 预算门跳过：零 LLM 调用、不计轮、prose 不动。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    prose = "中" * 2000  # < band_low 2550 ⇒ status='under'

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))

    out = _condense_node(_condense_ctx(db_path, cid, prose))

    assert out["condense_status"] == "skipped_below_budget"
    assert out["condense_skipped"] is True
    assert out["condense_rounds"] == 0  # 没调 LLM，不计轮
    assert out["polished_prose"] == prose  # 跳过时替代数据形态 = 原文（下游同键消费）
    assert calls == [], "预算门跳过必须零 LLM 调用"
    # 原因含 payload 尺寸与阈值（run detail 一眼看清为什么没压缩）
    assert out["condense_payload_chars"] == 2000
    assert out["condense_budget_chars"] == _BAND_HIGH
    reason = out["condense_skip_reason"]
    assert "2000" in reason and str(_BAND_HIGH) in reason and "未超带" in reason


def test_condense_budget_gate_threshold_arithmetic(tmp_path: Path, monkeypatch):
    """阈值算术边界：payload == band_high → 跳过；band_high + 1 → 门放行走原压缩路径。

    report 取「陈旧形态」（length_check 按 5500 字判超带，而 payload 已变短）——
    门以 payload 实测为准重新判据，与 length_check_passed 布尔解耦。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    stale_report = classify_prose_length("中" * 5500, _TARGET)
    stale_report["within_band"] = False
    stale = {
        "length_check_passed": False,
        "length_report": stale_report,
        "mock_providers": None,
    }

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))
    out_at_threshold = _condense_node(
        _condense_ctx(db_path, cid, "中" * _BAND_HIGH, **stale)
    )
    assert out_at_threshold["condense_status"] == "skipped_below_budget"
    assert out_at_threshold["condense_payload_chars"] == _BAND_HIGH
    assert calls == []

    monkeypatch.setattr(
        cw_pipeline,
        "run_agent",
        lambda *a, **kw: {"polished_text": "中" * _TARGET, "changes_summary": "压缩"},
    )
    out_over = _condense_node(
        _condense_ctx(db_path, cid, "中" * (_BAND_HIGH + 1), **stale)
    )
    assert out_over["condense_status"] == "ok"  # 超阈值路径原行为不变
    assert out_over["condense_rounds"] == 1
    assert out_over["polished_prose"] == "中" * _TARGET


def test_condense_skip_path_feeds_save_draft_unchanged(tmp_path: Path, monkeypatch):
    """跳过路径数据形态兼容：ctx.update 后 save_draft 落库 = 原 prose 且无偏差注记。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    prose = "中" * 2000

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))

    ctx = _condense_ctx(db_path, cid, prose)
    ctx.update(_condense_node(ctx))  # 引擎语义：ctx.update(node output)
    ctx["writer_output"] = {"prose": prose, "prompt_version": "writer:v1"}
    ctx["writer_model_id"] = "mock/mock"

    out = _save_draft_node(ctx)

    assert out["word_count"] == visible_chars(prose)
    conn = get_connection(db_path)
    try:
        draft_row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ?", (cid,),
        ).fetchone()
        plan_row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert draft_row["content"] == prose
    assert (json.loads(plan_row["plan_json"]).get("deviations") or []) == []
    assert calls == []


# ---------------------------------------------------------------------------
# B. polisher 条件触发预检
# ---------------------------------------------------------------------------


def test_polish_skips_when_precheck_clean(tmp_path: Path, monkeypatch):
    """三源零命中 → skipped_clean：零 LLM 调用、prose 沿用、命中统计进 ctx。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))

    out = _polish_node(_polish_ctx(db_path, cid, _CLEAN_PROSE))

    assert out["polisher_status"] == "skipped_clean"
    assert out["polish_skipped"] is True
    assert out["polished_prose"] == _CLEAN_PROSE
    assert out["ai_findings"] == []
    assert calls == [], "预检全过必须零 LLM 调用"
    precheck = out["polish_precheck"]
    assert precheck["ai_pattern_hit_count"] == 0
    assert precheck["forbidden_word_hit_count"] == 0
    assert precheck["q7_issue_count"] == 0
    assert precheck["forbidden_words"] == []  # 未绑定题材包 → 空禁词表
    assert "预检三源零命中" in out["polish_skip_reason"]
    # 进 ctx → checkpoint_json / output_json：必须 JSON 原生可序列化
    # （req_q7 的 Issue 是 pydantic BaseModel，已在 _polish_precheck 投影为 dict）。
    json.dumps(out["polish_precheck"], ensure_ascii=False)
    json.dumps(out, ensure_ascii=False)


def test_polish_runs_when_ai_pattern_hit(tmp_path: Path, monkeypatch):
    """AI 腔命中（硬套话层，命中即报）→ 门放行，走原润色路径。

    注：2026-09-15 分层后，弱词层（忽然/似乎/好像…）需同章堆积才报——本测改用
    硬套话层，语义更稳。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    prose = "空气中弥漫着一股血腥味。"

    captured: list[dict] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _echo_runner(captured))

    out = _polish_node(_polish_ctx(db_path, cid, prose))

    assert len(captured) == 1, "AI 模式命中必须走原润色路径"
    assert captured[0]["agent_name"] == "polisher"
    findings = captured[0]["payload"]["ai_findings"]
    assert findings and findings[0]["rule_id"] == "AI-FORBIDDEN-WORD"
    assert out["polisher_status"] == "ok"
    assert "polish_skipped" not in out


def test_polish_runs_when_genre_forbidden_word_hit(tmp_path: Path, monkeypatch):
    """题材禁词命中（题材包 style_constraints.forbidden_words）→ 门放行。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_genre_pack(db_path, pack_id="gp_gate", forbidden=["灵气"])
    GenrePackService(db_path).bind(pid, "gp_gate")

    prose = "灵气在他掌心流转。灯芯闪了一下。"
    precheck = _polish_precheck(prose, str(db_path), cid)
    assert precheck["forbidden_words"] == ["灵气"]
    assert precheck["forbidden_word_hit_count"] == 1
    # 自证：本用例只有题材禁词腿非零（否则断言不到该腿）
    assert precheck["ai_pattern_hit_count"] == 0
    assert precheck["q7_issue_count"] == 0

    captured: list[dict] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _echo_runner(captured))

    out = _polish_node(_polish_ctx(db_path, cid, prose))

    assert len(captured) == 1, "题材禁词命中必须走原润色路径"
    assert out["polisher_status"] == "ok"


def test_polish_runs_when_q7_flat_sentence_hit(tmp_path: Path, monkeypatch):
    """句长标准差 < Q7_FLAT_SENTENCE_STD（≥1000 字）→ 门放行（句长腿生效）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    prose = _flat_sentence_prose()

    precheck = _polish_precheck(prose, str(db_path), cid)
    assert precheck["q7_issue_count"] == 1
    assert precheck["q7_issues"][0]["rule_id"] == "RULE_Q7_FLAT_SENTENCE"
    # 自证：本用例只有句长腿非零
    assert precheck["ai_pattern_hit_count"] == 0
    assert precheck["forbidden_word_hit_count"] == 0

    captured: list[dict] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _echo_runner(captured))

    out = _polish_node(_polish_ctx(db_path, cid, prose))

    assert len(captured) == 1, "句长腿命中必须走原润色路径"
    assert out["polisher_status"] == "ok"


def test_polish_mock_passthrough_precedes_gate(tmp_path: Path, monkeypatch):
    """mock 直通优先级不变：writer-only mock 流照旧 passthrough（既有语义零回归）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))

    out = _polish_node(
        _polish_ctx(db_path, cid, _CLEAN_PROSE, mock_providers={"writer": ["{}"]})
    )

    assert out["polisher_status"] == "passthrough"
    assert "polish_skipped" not in out
    assert calls == []


def test_polish_skip_path_feeds_save_draft_unchanged(tmp_path: Path, monkeypatch):
    """跳过路径数据形态兼容：save_draft 落库 = writer prose。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    calls: list[int] = []
    monkeypatch.setattr(cw_pipeline, "run_agent", _boom_runner(calls))

    ctx = _polish_ctx(db_path, cid, _CLEAN_PROSE)
    ctx.update(_polish_node(ctx))  # 引擎语义：ctx.update(node output)
    ctx["writer_model_id"] = "mock/mock"

    out = _save_draft_node(ctx)

    assert out["word_count"] == visible_chars(_CLEAN_PROSE)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["content"] == _CLEAN_PROSE
    assert calls == []
