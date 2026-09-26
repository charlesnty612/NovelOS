"""P0-1 端到端：confirm 档在 HTTP 层不许静默通过（真实 app + 真实 DB + mock LLM）。

链路（复用 ``tests/integration/test_v1_4_reference_and_revision.py`` 的 mock 管道驱动）：

1. plan → write（**高重复散文**）→ review(approve)，章到 REVIEWED；
2. ``POST /commit``（enforce，不带 gate_override）⇒ run FAILED，
   ``runs.error`` 带 ``gate=confirm`` + 规则 id + 证据摘录，
   ``plan_json.gate_blocked`` 标为 ``gate="confirm"``；
3. 同一章再次 ``POST /commit`` 带**全量** ``gate_override`` + reason ⇒ 不再被 confirm 拦；
   ``plan_json.gate_blocked`` 被清除。

第 3 步只断言「不再因 confirm 被拦」（不断言 COMPLETED）：exit 之后的 commit 节点是否因
其它口径（字数带等）失败与本特性无关，断言过强会把别的门禁绑进本测试。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.integration.test_v1_4_reference_and_revision import (
    _create_app,
    _director_script,
    _drive_plan_write_review,
    _make_chapter,
    _make_project,
    _request,
    _sync_prompts,
    _wait_run_terminal,
)

# 高重复散文：同一句反复出现 ⇒ trigram 重复率远超 8% 阈值（可复算、作者无从辩驳）。
# 长度刻意接近字数带（12 次 ≈ 216 字远低于下限，会让 write 节点反复重写）——200 次 ≈ 3600 字。
_REPETITIVE_SENTENCE = "破屋的灯还亮着，他把窗纸又糊了一遍。"
_REPETITIVE_PROSE = _REPETITIVE_SENTENCE * 200


def _observer_empty_script() -> list[str]:
    """observer mock：无任何状态变更（commit 的 observer 节点必须走 mock，否则要真模型）。"""
    return [
        json.dumps(
            {
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]


def _writer_script_repetitive(chapter_id: str, prose: str = _REPETITIVE_PROSE) -> list[str]:
    """writer mock：输出高重复散文（schema 同 tests/api/test_quality.py 的 writer 脚本）。"""
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": chapter_id,
                "prose": prose,
                "self_report": {
                    "slots_filled": ["slot_001"],
                    "word_count": len(prose),
                    "scene_count": 1,
                    "deviations": [],
                    "forbidden_word_hits": [],
                    "self_check_notes": "",
                },
            },
            ensure_ascii=False,
        )
    ]


async def _plan_json(app, cid: str) -> dict:
    r = await _request(app, "GET", f"/api/chapters/{cid}")
    assert r.status_code == 200, r.text
    return r.json()["plan_json"]


def test_confirm_gate_blocks_then_passes_with_recorded_override(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script_repetitive(cid),
                "observer": _observer_empty_script(),
            }
            await _drive_plan_write_review(app, pid, cid, mock)

            # --- 1) 无声明 ⇒ 阻断
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock, "quality_gate_mode": "enforce"},
            )
            assert r.status_code == 201, r.text
            blocked = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("FAILED",)
            )
            err = blocked.get("error") or ""
            assert "quality gate blocked" in err, err
            assert "RULE_STYLE_REPETITION_TRIGRAM" in err, err
            assert "gate=confirm" in err, err

            plan = await _plan_json(app, cid)
            marker = plan.get("gate_blocked") or {}
            assert marker.get("gate") == "confirm", plan
            assert marker.get("rule_ids") == ["RULE_STYLE_REPETITION_TRIGRAM"], marker
            assert "RULE_STYLE_REPETITION_TRIGRAM" in (plan.get("revision_note") or ""), plan

            # --- 2) 全量声明 + reason ⇒ 不再因 confirm 被拦，标记被清除
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={
                    "mock_providers": mock,
                    "quality_gate_mode": "enforce",
                    "gate_override": {
                        "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
                        "reason": "本章刻意的复沓，已逐段人工确认",
                    },
                },
            )
            assert r.status_code == 201, r.text
            second = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED", "PAUSED")
            )
            second_err = second.get("error") or ""
            assert "gate=confirm" not in second_err, second_err
            assert "RULE_STYLE_REPETITION_TRIGRAM" not in second_err, second_err

            plan_after = await _plan_json(app, cid)
            assert "gate_blocked" not in plan_after, plan_after

            # --- 3) 报告留痕：接受声明写进 quality_reports._meta
            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 200, r.text
            meta = r.json()["scores_json"]["_meta"]
            assert meta["gate_accepted_override"]["reason"] == "本章刻意的复沓，已逐段人工确认"
            assert meta["gate_summary"]["confirm_rule_ids"] == [
                "RULE_STYLE_REPETITION_TRIGRAM"
            ]

    asyncio.run(run())


def test_confirm_gate_partial_override_via_http_still_blocks(tmp_path: Path):
    """HTTP 层第二个窗口：只声明**别的**规则 ⇒ 仍然阻断（覆盖判定是集合包含）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)
            mock = {
                "director": _director_script(cid),
                "writer": _writer_script_repetitive(cid),
                "observer": _observer_empty_script(),
            }
            await _drive_plan_write_review(app, pid, cid, mock)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={
                    "mock_providers": mock,
                    "quality_gate_mode": "enforce",
                    "gate_override": {"rule_ids": ["AI-BEAT-REPEAT"], "reason": "认了节拍"},
                },
            )
            assert r.status_code == 201, r.text
            blocked = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("FAILED",)
            )
            err = blocked.get("error") or ""
            assert "gate=confirm" in err, err
            assert "未覆盖" in err, err
            assert (await _plan_json(app, cid)).get("gate_blocked", {}).get("gate") == "confirm"

    asyncio.run(run())
