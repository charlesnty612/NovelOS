"""批次三十六 批 4：chapter_write / writer 装配域 5 项修复的判别用例。

对应缺陷（B 档审计，2026-11-04）：

* **C-15** writer 测量路径只在 ``mode == "revise"`` 剥 ``---REVISION-CHECKLIST---``
  尾块，而保存路径早已「按内容剥」⇒ write / length-retry 轮模型违约吐尾块时，
  测量把它算进 ``visible_chars`` ⇒ 字数带误判（欠带被读成在带）。
* **C-17** ``length.py`` 两处无日志 ``except Exception``：读库失败 ⇒ 项目级
  ``word_band_json`` 覆盖静默失效，零告警。
* **C-19** ``_condense_node`` 发给模型的 payload 写假标签
  ``agent="condense"`` / ``prompt_version="condense:v1"``，实际调用的是 polisher
  且仓内无 condense 提示词文件。
* **C-20** drafts 溯源取「最后一条 ai_call_logs」，而采纳的可能是较早一次 attempt。
* **C-16** writer payload 两个死字段（``recent_prose.last_scene_excerpt`` /
  ``retrieved_memory``）在 v5 契约里被声明；升版 v6 删声明、补
  ``recalled_passages`` 的如实声明，实现侧同步删键。

断言一律打在真实值上（payload 实际内容 / 落库实际值 / 剥离后文本的实测字数）。
零真实 LLM 调用（run_agent 全部 monkeypatch）、零 dev 库（tmp_path 临时库）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.context_engine.builders import _cache_reset, build_writer_input
from packages.core.db import apply_migrations, get_connection
from packages.core.quality.wordcount import classify_prose_length, visible_chars
from packages.workflows.chapter_write import length as cw_length
from packages.workflows.chapter_write import pipeline as cw_pipeline
from packages.workflows.chapter_write import writer as cw_writer

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"
WRITER_V6 = PROMPTS_DIR / "writer-v6.md"

TARGET = 2500  # 默认带 0.85/1.15/1200 ⇒ band_low=2125 / band_high=2875
BAND_LOW_2500 = 2125

_MARKER = "---REVISION-CHECKLIST---"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "b36.db")
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: str, *, project_id: str = "prj_b36") -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (project_id, "b36", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return project_id


def _insert_chapter(
    db_path: str, *, chapter_id: str, project_id: str, number: int = 1,
) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'PLANNED', ?, ?)",
            (
                chapter_id, project_id, number, "b36",
                json.dumps({"chapter_goal": "测试", "expected_word_count": TARGET}),
                "2026-01-01T00:00:00", "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return chapter_id


def _writer_output(prose: str, *, prompt_version: str = "writer:v6") -> dict[str, Any]:
    return {
        "schema_version": "writer-output.v1",
        "prompt_version": prompt_version,
        "chapter_id": "ch_b36",
        "prose": prose,
        "self_report": {
            "slots_filled": [],
            "word_count": len(prose),
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "",
        },
    }


# ---------------------------------------------------------------------------
# C-15：测量路径与保存路径同口径（按内容剥尾块，不看 mode）
# ---------------------------------------------------------------------------


def test_measure_write_mode_strips_checklist_tail_before_counting():
    """write 模式带尾块：测量字数不含尾块，且「欠带」不被尾块读成「在带」。"""
    body = "中" * 2100  # < band_low(2125) ⇒ under
    tail = "\n\n" + _MARKER + "\n" + json.dumps(
        [{"item": "x", "status": "done", "note": "y"}], ensure_ascii=False
    )
    prose = body + tail
    # 量具自检：含尾块时字数确实越过了 band_low（否则本用例测不到机制）。
    assert visible_chars(prose) > BAND_LOW_2500

    got = cw_writer._measure_writer_attempt(
        attempt=1, mode="write", out={"prose": prose},
        target=TARGET, band_cfg={},
    )

    assert got["visible_chars"] == visible_chars(body) == 2100
    assert got["status"] == "under", got
    assert got["distance"] == BAND_LOW_2500 - 2100


def test_measure_write_mode_without_tail_is_unchanged():
    """无尾块文本：测量结果与改造前逐位一致（不看 mode 也不产生额外裁剪）。"""
    prose = "中" * 2500
    got = cw_writer._measure_writer_attempt(
        attempt=1, mode="write", out={"prose": prose},
        target=TARGET, band_cfg={},
    )
    ref = classify_prose_length(prose, TARGET)
    assert got["visible_chars"] == ref["visible_chars"] == 2500
    assert got["status"] == "in_band"
    assert got["in_band"] is True


def test_measure_revise_mode_still_strips_tail():
    """revise 语义不变：尾块仍被剥（回归看守）。"""
    body = "中" * 2500
    prose = body + "\n\n" + _MARKER + "\n[]"
    got = cw_writer._measure_writer_attempt(
        attempt=1, mode="revise", out={"prose": prose},
        target=TARGET, band_cfg={},
    )
    assert got["visible_chars"] == 2500


# ---------------------------------------------------------------------------
# C-17：读库失败必须留 warning（不阻断）
# ---------------------------------------------------------------------------


def test_word_band_chapter_lookup_failure_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    db_path = _fresh_db(tmp_path)

    def _boom(_db_path):  # noqa: ANN001
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cw_length, "get_connection", _boom)
    with caplog.at_level(
        logging.WARNING, logger="packages.workflows.chapter_write.length"
    ):
        project_id, band = cw_length._resolve_chapter_word_band(db_path, "ch_b36")

    assert project_id == ""
    assert band["low_ratio"] == 0.85 and band["high_ratio"] == 1.15  # 默认带
    messages = [r.getMessage() for r in caplog.records]
    assert any("chapter lookup failed" in m and "ch_b36" in m for m in messages), messages


def test_word_band_project_lookup_failure_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, chapter_id="ch_b36", project_id=pid)

    real = cw_length.get_connection
    calls = {"n": 0}

    def _second_call_fails(path):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:  # conn2：读 projects.word_band_json
            raise sqlite3.OperationalError("disk I/O error")
        return real(path)

    monkeypatch.setattr(cw_length, "get_connection", _second_call_fails)
    with caplog.at_level(
        logging.WARNING, logger="packages.workflows.chapter_write.length"
    ):
        project_id, band = cw_length._resolve_chapter_word_band(db_path, cid)

    assert project_id == pid
    assert band["low_ratio"] == 0.85 and band["high_ratio"] == 1.15
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "project word_band lookup failed" in m and pid in m for m in messages
    ), messages


# ---------------------------------------------------------------------------
# C-19：condense payload 的 agent / prompt_version 与 run_agent 实参同源
# ---------------------------------------------------------------------------


def test_condense_payload_labels_match_actual_polisher_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, chapter_id="ch_b36", project_id=pid)
    # 播种真提示词镜像：polisher 的 ACTIVE 应为 v2（仓内最新版）。
    PromptRegistry(db_path).sync_from_docs(PROMPTS_DIR)

    captured: dict[str, Any] = {}

    def _fake_run_agent(db_path, agent_name, payload, run_id, **kw):  # noqa: ANN001
        captured["agent_name"] = agent_name
        captured["payload"] = payload
        return {"polished_text": "中" * 3200, "changes_summary": "压缩"}

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    long_prose = "中" * 3500
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "run_id": "run_b36_condense",
        "polished_prose": long_prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 3500,
            "target": 3000,
            "band_low": 2550,
            "band_high": 3449,
            "status": "over",
            "within_band": False,
            "deviation_pct": 16.7,
        },
        "condense_rounds": 0,
        "mock_providers": {"condense": ["{}"]},
    }
    out = cw_pipeline._condense_node(ctx)

    assert out["condense_status"] == "ok"
    assert captured["agent_name"] == "polisher"
    # 旧实现的假标签（"condense" / "condense:v1"）必须消失。
    assert captured["payload"]["agent"] == "polisher"
    assert captured["payload"]["prompt_version"] == "polisher:v2", (
        "payload 声明的 prompt_version 必须与 ACTIVE 提示词同源（polisher-v2）"
    )


# ---------------------------------------------------------------------------
# C-20：按「被采纳 attempt 的 call_id」取 ai_call_logs，而非一律取最后一条
# ---------------------------------------------------------------------------


def _seed_run_scaffold(db_path: str, *, run_id: str, node_run_id: str) -> None:
    """插入 run / node 行，使 ai_call_logs 的外键可满足。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES ('wf_b36', 'chapter-write', 'v1', '{}', ?, ?)",
            ("2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, error, retry_count, started_at, ended_at) "
            "VALUES (?, 'wf_b36', NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)",
            (run_id, "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO workflow_run_nodes (node_run_id, run_id, node_id, status, "
            "started_at, ended_at, input_json, output_json, error) "
            "VALUES (?, ?, 'writer', 'RUNNING', ?, NULL, '{}', NULL, NULL)",
            (node_run_id, run_id, "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_ai_call(
    db_path: str, *, call_id: str, run_id: str, node_run_id: str,
    model_id: str, prompt_version: str,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO ai_call_logs (call_id, run_id, node_run_id, agent_id, model_id, "
            "prompt_version, input_context_ids_json, output_json, token_usage_json, "
            "latency_ms, cost, error, retry_count, created_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, '[]', '{}', NULL, 10, NULL, NULL, 0, ?)",
            (
                call_id, run_id, node_run_id, model_id, prompt_version,
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _writer_ctx(
    db_path: str, chapter_id: str, *, run_id: str, node_run_id: str,
    retries: int = 1,
) -> dict[str, Any]:
    return {
        "db_path": db_path,
        "run_id": run_id,
        "chapter_id": chapter_id,
        "scene_plan": {"scenes": [{"scene_id": "scene_001", "purpose": "A"}]},
        "loaded_plan": {"chapter_goal": "测试", "expected_word_count": TARGET},
        "mock_providers": {"writer": ["{}"]},
        "fresh_write": False,
        "model_overrides": {},
        "_current_node_run_id": node_run_id,
        "target_word_count": TARGET,
        "writer_length_retries": retries,
    }


def test_drafts_provenance_uses_accepted_attempt_not_last_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """两次 writer 调用、采纳**第一次** ⇒ 溯源取第一次的 model / prompt_version。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, chapter_id="ch_b36", project_id=pid)
    run_id, node_run_id = "run_b36_prov", "nrun_b36_prov"
    _seed_run_scaffold(db_path, run_id=run_id, node_run_id=node_run_id)

    calls: list[dict[str, Any]] = []

    def _fake_run_agent(db_path, agent_name, payload, run_id_, **kw):  # noqa: ANN001
        idx = len(calls)
        calls.append({"agent_name": agent_name, "payload": payload})
        # 模拟 runner：先 commit 本次调用的日志行，再返回输出。
        _insert_ai_call(
            db_path, call_id=f"aic_{idx}", run_id=run_id_, node_run_id=node_run_id,
            model_id=f"prov{idx}/m{idx}", prompt_version=f"writer:v{5 + idx}",
        )
        # 第 1 次 1800 字（欠带距 325），第 2 次 1000 字（距 1125）⇒ 采纳第 1 次。
        return _writer_output("中" * (1800 if idx == 0 else 1000))

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = cw_pipeline._writer_node(
        _writer_ctx(db_path, cid, run_id=run_id, node_run_id=node_run_id)
    )

    assert len(calls) == 2
    assert out["writer_length_accepted_attempt"] == 1
    # 旧实现取「最后一条」会得到 prov1/m1 + writer:v6（未采纳的那次）。
    assert out["writer_model_id"] == "prov0/m0"
    assert out["writer_prompt_version"] == "writer:v5"


def test_drafts_provenance_falls_back_to_latest_row_when_capture_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """capture 不可用（mock / 表缺 / DB 故障）⇒ 保持原「最后一行」行为。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, chapter_id="ch_b36", project_id=pid)
    run_id, node_run_id = "run_b36_fb", "nrun_b36_fb"
    _seed_run_scaffold(db_path, run_id=run_id, node_run_id=node_run_id)

    monkeypatch.setattr(
        cw_pipeline, "_capture_ai_call_id", lambda *a, **kw: None
    )
    calls: list[int] = []

    def _fake_run_agent(db_path, agent_name, payload, run_id_, **kw):  # noqa: ANN001
        idx = len(calls)
        calls.append(idx)
        _insert_ai_call(
            db_path, call_id=f"aic_fb{idx}", run_id=run_id_, node_run_id=node_run_id,
            model_id=f"prov{idx}/m{idx}", prompt_version=f"writer:v{5 + idx}",
        )
        return _writer_output("中" * (1800 if idx == 0 else 1000))

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    out = cw_pipeline._writer_node(
        _writer_ctx(db_path, cid, run_id=run_id, node_run_id=node_run_id)
    )

    assert out["writer_length_accepted_attempt"] == 1
    assert out["writer_model_id"] == "prov1/m1"  # 原行为：最后一条
    assert out["writer_prompt_version"] == "writer:v6"


# ---------------------------------------------------------------------------
# C-16：payload 删死字段 + writer-v6 契约如实
# ---------------------------------------------------------------------------


def test_writer_payload_no_dead_fields_and_declares_recalled_passages(
    tmp_path: Path,
):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, chapter_id="ch_b36", project_id=pid)

    payload = build_writer_input(
        db_path, cid, {"scenes": [{"scene_id": "scene_001", "purpose": "A"}]},
        target_word_count=TARGET, context_mode="full",
    )

    assert "retrieved_memory" not in payload
    assert set(payload["recent_prose"]) == {"last_chapter_excerpt"}
    assert isinstance(payload["recalled_passages"], list)


def _json_blocks(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    for raw in re.findall(r"```json\n(.*?)\n```", text, re.S):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            blocks.append(parsed)
    return blocks


def test_writer_v6_contract_drops_dead_keys_and_declares_recalled_passages():
    blocks = _json_blocks(WRITER_V6)
    input_contracts = [b for b in blocks if "recent_prose" in b]
    assert len(input_contracts) == 2, "§5 契约 + §8.1 示例都应有 recent_prose"

    for block in input_contracts:
        assert set(block["recent_prose"]) == {"last_chapter_excerpt"}
        assert "retrieved_memory" not in block
        recalled = block["recalled_passages"]
        assert isinstance(recalled, list) and recalled
        assert set(recalled[0]) == {"chapter_id", "chapter_no", "snippet", "rank"}

    text = WRITER_V6.read_text(encoding="utf-8")
    # E-WRT-10 改锚到真实存在的键
    assert "`recent_prose.last_chapter_excerpt` 与本段首句" in text
    assert "`last_scene_excerpt` 与本段首句" not in text


def test_writer_v6_is_the_active_prompt_after_sync(tmp_path: Path):
    db_path = Path(_fresh_db(tmp_path))
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(PROMPTS_DIR)

    _, version_label, content = registry.get_active_prompt("writer")
    assert version_label == "writer:v6"
    assert content == WRITER_V6.read_text(encoding="utf-8")

    # v5 本体未被改动（同版本文件的生命周期互不覆盖）
    writer_v5 = PROMPTS_DIR / "writer-v5.md"
    assert writer_v5.read_text(encoding="utf-8").startswith(
        "# Writer Agent Prompt — `writer:v5`"
    )
