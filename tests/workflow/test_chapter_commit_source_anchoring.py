"""chapter_commit 源锚定检测接线测试（2026-09-26 批次）。

覆盖点（任务书 A.4 接线用例）：
- inject_validate 节点 output 带 ``source_anchoring`` findings（低覆盖 after 命中
  OBS-UNSOURCED-PHRASE），且 **run 不被阻断**（COMPLETED，恒 warning 契约）；
- summarize 节点 output 带 ``source_anchoring_count``；
- HIGH 风险 pause payload 的 message 附加 findings 摘要（rule_id 计数 + sample）；
- 高覆盖 after → findings 为空（source_anchoring == []，零误报契约）。

形态照 tests/workflow/test_chapter_commit_observer_retry.py：HTTP 全链 + mock_providers。

突变验证：撤掉 commit.py 中 check_delta_source_anchoring 接线（不写 source_anchoring
键）时，前两个用例必红。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection

# 低覆盖 after（34 个 CJK bigram 中仅 2 个在 draft 内，覆盖率 ≈0.059 < 0.08 阈值；
# 阈值与分布实测依据见 delta_anchoring 模块 docstring）。
_LOW_COVERAGE_AFTER = "第一代老镖头泽被苍生福泽绵延三百年基业庇佑子孙万代昌隆恩泽遍及山南河北"
# 高覆盖 after：逐字转写 draft 句子（覆盖率 1.0）。
_HIGH_COVERAGE_AFTER = "第一镖头沈青崖押着最后一趟镖进了城"
# draft（writer mock 的 prose）——含 excerpt 声明的逐字句与「第一镖头」本体。
_WRITER_PROSE = (
    "第一镖头沈青崖押着最后一趟镖进了城，码头上人来人往。"
    "天色渐暗，更鼓声起，他勒紧了缰绳。"
)


# ---------------------------------------------------------------------------
# 一致性看守（core 不得 import workflows，名册双声明——一致性只能由测试看守）
# ---------------------------------------------------------------------------


def test_delta_anchoring_arrays_match_observer_all_arrays():
    """core 侧 ``_DELTA_ARRAYS`` 与 workflows 侧 ``_OBSERVER_ALL_ARRAYS`` 恒一致。

    delta_anchoring.py 模块注释声称「一致性由接线测试看守」——本测试即该看守：
    两侧任一名册增删数组而不同步时必红（突变验证：单侧删一项即红）。
    """
    from packages.core.story_state.delta_anchoring import _DELTA_ARRAYS
    from packages.workflows.chapter_commit.pipeline_common import (
        _OBSERVER_ALL_ARRAYS,
    )

    assert set(_DELTA_ARRAYS) == set(_OBSERVER_ALL_ARRAYS), (
        f"core 侧 _DELTA_ARRAYS={_DELTA_ARRAYS!r} 与 workflows 侧 "
        f"_OBSERVER_ALL_ARRAYS={_OBSERVER_ALL_ARRAYS!r} 漂移，需同步双声明"
    )
    # core 侧声明无重复（重复项会让锚定检查重复扫同一数组）
    assert len(_DELTA_ARRAYS) == len(set(_DELTA_ARRAYS))


# ---------------------------------------------------------------------------
# HTTP helpers（与 test_chapter_commit_observer_retry.py 同款）
# ---------------------------------------------------------------------------


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _get_run(app, run_id: str) -> dict | None:
    r = await _request(app, "GET", f"/api/runs/{run_id}")
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(
    app, run_id: str,
    *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0,
) -> dict:
    import time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run(app, run_id)
        last_run = run
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s "
        f"(last={last_run['status']!r})"
    )


async def _make_project(app, name: str = "锚定测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_location(app, pid: str, name: str = "青石巷") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/locations",
        json={"name": name, "statement": "一条青石铺就的小巷",
              "data": {"atmosphere": "阴暗"}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Mock scripts
# ---------------------------------------------------------------------------


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "第一镖头沈青崖押镖进城",
                "core_conflict": "押镖途中遇袭",
                "turning_point": "沈青崖负伤仍护住镖车",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "进城场景设置",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "建立场景",
                    }
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {
                    "uses_hidden_knowledge": False, "leakage_details": None,
                },
                "open_questions": [],
                "notes_for_planner": "建议场景数 1",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = _WRITER_PROSE
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
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


def _observer_anchor_script(
    chapter_id: str, location_id: str, after: str, risk_level: str = "LOW",
) -> list[str]:
    """observer 输出：world_changes update（before=None 走 repair_delta 可修路径），
    after 为指定文本、excerpt 逐字摘自 draft。"""
    return [
        json.dumps(
            {
                "character_changes": [],
                "world_changes": [
                    {
                        "change_id": "wc_anchor_001",
                        "op": "update",
                        "target_id": location_id,
                        "world_kind": "location",
                        "world_id": location_id,
                        "field": "data_json.atmosphere",
                        "before": None,
                        "after": after,
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "excerpt": "第一镖头沈青崖押着最后一趟镖进了城",
                        },
                        "risk_level": risk_level,
                    }
                ],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]


async def _push_chapter_to_reviewed(app, pid: str, cid: str) -> None:
    """plan + write + review(approve) → REVIEWED（plan/write/review 用基础 mock）。"""
    base_mocks = {"director": _director_script(), "writer": _writer_script()}
    for step, path in (
        ("plan", f"/api/projects/{pid}/chapters/{cid}/plan"),
        ("write", f"/api/projects/{pid}/chapters/{cid}/write"),
        ("review", f"/api/projects/{pid}/chapters/{cid}/review"),
    ):
        r = await _request(app, "POST", path, json={"mock_providers": base_mocks})
        assert r.status_code == 201, f"{step}: {r.text}"
        rid = r.json()["run_id"]
        await _wait_run_terminal(app, rid, expected=("COMPLETED", "PAUSED", "FAILED"))
        await _wait_run_terminal(app, rid, expected=("COMPLETED", "PAUSED", "FAILED"))
        await _wait_run_terminal(app, rid, expected=("COMPLETED", "PAUSED", "FAILED"))
        if step == "review":
            paused = await _wait_run_terminal(app, rid, expected=("PAUSED",))
            r = await _request(
                app, "POST", f"/api/runs/{paused['run_id']}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            rid = r.json()["run_id"]
            await _wait_run_terminal(app, rid, expected=("COMPLETED",))
            await _wait_run_terminal(app, rid, expected=("COMPLETED",))


async def _start_commit(app, pid: str, cid: str, observer_script: list[str]) -> str:
    mock_providers = {"director": _director_script(), "writer": _writer_script()}
    mock_providers["observer"] = observer_script
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _node_output(db_path, run_id: str, node_id: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT output_json FROM workflow_run_nodes "
            "WHERE run_id = ? AND node_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id, node_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["output_json"] is None:
        return None
    return json.loads(row["output_json"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_low_coverage_after_recorded_and_run_not_blocked(tmp_path: Path, monkeypatch):
    """低覆盖 after → inject_validate 记录 findings，run 仍 COMPLETED（恒 warning）。

    断言：
    - run COMPLETED / chapters COMMITTED（源锚定绝不阻断）；
    - inject_validate 节点 output.source_anchoring 含 OBS-UNSOURCED-PHRASE
      （path=world_changes[0]、coverage<0.30）且无 excerpt 误报（excerpt 逐字命中）；
    - summarize 节点 output.source_anchoring_count == 1。
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    monkeypatch.setenv("NOVELOS_QUALITY_GATE", "report")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            lid = await _make_location(app, pid, "青石巷")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _push_chapter_to_reviewed(app, pid, cid)

            run_id = await _start_commit(
                app, pid, cid,
                _observer_anchor_script(cid, lid, _LOW_COVERAGE_AFTER),
            )
            final = await _wait_run_terminal(
                app, run_id,
                expected=("COMPLETED", "PAUSED", "FAILED"),
            )
            final = await _wait_run_terminal(app, final["run_id"], expected=("COMPLETED",))

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "COMMITTED", r.json()["status"]

            db_path = app.state.settings.db_path
            inj = _node_output(db_path, final["run_id"], "inject_validate")
            assert inj is not None, "inject_validate 节点行缺失"
            findings = inj.get("source_anchoring")
            assert isinstance(findings, list) and findings, (
                f"source_anchoring 应非空，实得 {findings!r}"
            )
            bigram_hits = [
                f for f in findings
                if isinstance(f, dict) and f.get("rule_id") == "OBS-UNSOURCED-PHRASE"
            ]
            assert len(bigram_hits) == 1, findings
            assert bigram_hits[0]["path"] == "world_changes[0]", bigram_hits
            assert 0 <= bigram_hits[0]["coverage"] < 0.08, bigram_hits
            # excerpt 逐字命中 → 不得出现 excerpt 违规（零误报契约）
            assert not [
                f for f in findings
                if isinstance(f, dict)
                and f.get("rule_id") == "OBS-EXCERPT-NOT-VERBATIM"
            ], findings

            summary_out = _node_output(db_path, final["run_id"], "summarize")
            assert summary_out is not None, "summarize 节点行缺失"
            assert summary_out.get("source_anchoring_count") == 1, summary_out

    asyncio.run(run())


def test_high_risk_pause_payload_carries_anchoring_summary(tmp_path: Path, monkeypatch):
    """HIGH 风险 + findings 非空 → PAUSED，pause_payload.message 附加 findings 摘要。

    断言：
    - run PAUSED（HIGH 人工审批语义不变，源锚定不改变判定）；
    - pause_payload.message 含「源锚定告警」+ OBS-UNSOURCED-PHRASE 计数 + sample。
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    monkeypatch.setenv("NOVELOS_QUALITY_GATE", "report")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            lid = await _make_location(app, pid, "青石巷")
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _push_chapter_to_reviewed(app, pid, cid)

            run_id = await _start_commit(
                app, pid, cid,
                _observer_anchor_script(
                    cid, lid, _LOW_COVERAGE_AFTER, risk_level="HIGH",
                ),
            )
            final = await _wait_run_terminal(app, run_id, expected=("PAUSED",))
            assert final["status"] == "PAUSED", final["status"]

            payload = final.get("pause_payload") or {}
            message = str(payload.get("message", ""))
            assert "源锚定告警" in message, message
            assert "OBS-UNSOURCED-PHRASE" in message, message
            assert _LOW_COVERAGE_AFTER[:20] in message, message

    asyncio.run(run())


def test_well_anchored_after_yields_no_findings(tmp_path: Path, monkeypatch):
    """after 逐字转写 draft → findings 为空（零误报），计数为 0，run COMPLETED。"""
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "off")
    monkeypatch.setenv("NOVELOS_QUALITY_GATE", "report")
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            lid = await _make_location(app, pid, "青石巷")
            cid = await _make_chapter(app, pid, 1, "夜叩青石")
            await _push_chapter_to_reviewed(app, pid, cid)

            run_id = await _start_commit(
                app, pid, cid,
                _observer_anchor_script(cid, lid, _HIGH_COVERAGE_AFTER),
            )
            final = await _wait_run_terminal(
                app, run_id, expected=("COMPLETED", "PAUSED", "FAILED"),
            )
            final = await _wait_run_terminal(app, final["run_id"], expected=("COMPLETED",))

            db_path = app.state.settings.db_path
            inj = _node_output(db_path, final["run_id"], "inject_validate")
            assert inj is not None
            assert inj.get("source_anchoring") == [], inj.get("source_anchoring")

            summary_out = _node_output(db_path, final["run_id"], "summarize")
            assert summary_out is not None
            assert summary_out.get("source_anchoring_count") == 0, summary_out

    asyncio.run(run())
