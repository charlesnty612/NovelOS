"""chapter_write 修订模式机读核销表（REVISION-CHECKLIST）闭环。

需求：writer 在 revise 模式（mode='revise'）下，必须在 prose 文末追加
`---REVISION-CHECKLIST---` 尾块（机读 JSON 数组）。管线负责：
- 解析尾块 → revision_checklist 进 ctx（run 节点产出可见）；
- 剥离子句必须先于落库（drafts.content 与 word_count 都基于剥离后正文）；
- fail-soft：尾块缺失/JSON 坏 → checklist=None + warn，不阻断落稿；
- write 模式不解析（即便正文含同名行也不切）。

覆盖用例（与规格 §3 测试条款一一对应）：
- A：revise + 合规尾块 → 草稿正文不含尾块、revision_checklist 解析正确进 ctx。
- B：revise + 尾块缺失 → checklist=None、正常落稿、不炸。
- C：revise + 尾块坏 JSON → 同上降级。
- D：write 模式正文含标记行 → 按内容剥离但不进审计面（2026-09-21 m5 改写；
  旧契约「原样落稿」会让 prompt 违约的尾块进 drafts.content 并抬高 word_count）。
- E：word_count 只统计剥离后正文。
- F（2026-09-21「改稿审计断链」检修）：revise 欠带触发翻模（重写轮 mode='write'）
  → 节点产出 revision_audit.checklist_status == 'superseded_by_under_band_rewrite' 且
  revision_checklist 为 None——翻模不再是无痕事件（1a）。
- G：正常 revise → revision_audit.checklist_status == 'recorded'，preserved_ratio
  有值（保真比进审计面）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.agent_runtime.revision_fidelity import REVISE_MIN_PRESERVED_RATIO
from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


@pytest.fixture(autouse=True)
def _disable_generation_length_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """关掉 P0-2 生成期欠带重写（通道：官方开关 ``NOVELOS_WRITER_LENGTH_RETRIES=0``）。

    理由：本文件专注**核销表**解析 / 剥离 / write 模式契约，样板正文只有 ~60~180 字
    而目标字数 3000（带下限 2550），必然触发节点内整章重写——多出来的 writer 调用会
    用 mock 脚本的后续产出抢走断言目标（例如 v2 revise 的 176 字 write 轮取代 61 字
    revise 轮）。生成期字数闭环本身的断言在
    ``tests/workflow/test_chapter_write_length_retry.py``，两边互不遮挡。
    """
    monkeypatch.setenv("NOVELOS_WRITER_LENGTH_RETRIES", "0")


# ---------------------------------------------------------------------------
# 异步化适配（与既有 chapter_write 测试一致）
# ---------------------------------------------------------------------------


async def _get_run_via_http(app, run_id: str) -> dict | None:
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(
    app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0
) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
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


# ---------------------------------------------------------------------------
# Fixtures（与 test_chapter_write_revise_mode.py 口径一致）
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


async def _make_project(app, name: str = "核销表测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


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


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "苏婉清在夜谈中第一次怀疑林渊隐瞒父亲死因",
                "core_conflict": "苏婉清的求真意志 vs 林渊的善意隐瞒",
                "turning_point": "林渊回避黑玉佩细节",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "夜访场景设置",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "建立信任基础",
                    },
                    {
                        "beat_id": "beat_002",
                        "purpose": "黑玉佩引入对话",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "MEDIUM",
                        "narrative_question_served": "伏笔推进",
                    },
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "建议场景数 2",
            },
            ensure_ascii=False,
        )
    ]


# 正文样板（不含尾块），用于 v1 write；尾部拼装后用于 v2 revise。
_BODY_NO_TAIL = (
    "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
    "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。林渊立在博古架前，"
    "背对着她，似乎在翻检什么。"
)
_BODY_V2 = (
    "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
    "苏婉清坐在窗下，茶盏未温。林渊立在博古架前，背对着她。"  # 修订版压缩
)


def _writer_script_with_checklist(
    body: str, checklist: list[dict] | None
) -> list[str]:
    """writer mock 脚本。checklist=None 时输出正文不含尾块，否则拼合规尾块。"""
    if checklist is None:
        prose = body
    else:
        tail_json = json.dumps(checklist, ensure_ascii=False)
        prose = body + "\n\n---REVISION-CHECKLIST---\n" + tail_json
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": prose,
                # word_count 故意写成"含尾块"的字符数，用以验证管线会覆写为剥离后字数。
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


def _writer_script_with_marker_but_bad_json(body: str) -> list[str]:
    """writer mock 脚本：尾块存在但 JSON 故意写坏（用于测 fail-soft）。"""
    prose = body + "\n\n---REVISION-CHECKLIST---\nthis is not valid json"
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


def _writer_script_with_marker_in_write_mode(body: str) -> list[str]:
    """write 模式 mock：正文含同名标记行（违规但应原样透传，不解析不剥离）。"""
    prose = body + "\n\n---REVISION-CHECKLIST---\n[{\"item\":\"x\",\"status\":\"done\",\"note\":\"y\"}]"
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


def _observer_noop_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "observer.v1",
                "prompt_version": "observer:v1",
                "chapter_id": "ch_xxx",
                "narrative_observations": [],
                "continuity_observations": [],
                "world_state_observations": [],
                "relationship_observations": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
                "world_changes": [],
                "relationship_changes": [],
            },
            ensure_ascii=False,
        )
    ]


def _read_writer_node_output(db_path: str, run_id: str) -> dict | None:
    """从 workflow_runs.checkpoint_json 读 writer 节点产出（含 revision_checklist）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["checkpoint_json"]:
        return None
    return json.loads(row["checkpoint_json"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_revise_mode_strips_checklist_and_persists_clean_prose(tmp_path: Path):
    """用例 A：revise + 合规尾块 → drafts.content 不含尾块，revision_checklist 进 ctx。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path
    marker = "---REVISION-CHECKLIST---"

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            # v1 write（无 checklist）
            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            # 写 revision_note 触发 revise 模式
            note_text = "节奏压缩\n对话加快"
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = note_text
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            # v2 revise：合规尾块（2 条记录）
            checklist_payload = [
                {"item": "节奏压缩", "status": "done", "note": "已收紧第2段"},
                {"item": "对话加快", "status": "partial", "note": "前3句收紧后段未变"},
            ]
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_V2, checklist_payload),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            # 主断言 1：ctx["revision_checklist"] 解析正确（list 长度=2，字段对得上）
            ckpt = _read_writer_node_output(db_path, v2_run_id)
            assert ckpt is not None
            assert "revision_checklist" in ckpt, (
                "writer 节点返回应包含 revision_checklist 键（ctx / checkpoint_json 可见）"
            )
            cl = ckpt["revision_checklist"]
            assert isinstance(cl, list) and len(cl) == 2, (
                f"revision_checklist 应为长度 2 的 list，got {type(cl).__name__}: {cl!r}"
            )
            assert cl[0]["item"] == "节奏压缩" and cl[0]["status"] == "done"
            assert cl[1]["status"] == "partial"

            # 主断言 2：writer_output.prose 已剥离（不含 marker）
            wo = ckpt.get("writer_output") or {}
            prose = wo.get("prose") or ""
            assert marker not in prose, (
                f"writer_output.prose 应已剥离核销表尾块，got: {prose!r}"
            )
            assert prose == _BODY_V2.rstrip(), (
                f"剥离后 prose 应等于正文样板，got {prose!r}"
            )

            # 主断言 3：drafts.content 不含 marker（save_draft 落库的是剥离后正文）
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 2
            v2_content = rows[0]["content"]
            assert marker not in v2_content, (
                f"drafts.content（v2）应不含核销表 marker，got: {v2_content!r}"
            )
            assert v2_content == _BODY_V2.rstrip()

    asyncio.run(run())


def test_revise_mode_missing_checklist_degrades_to_none(tmp_path: Path):
    """用例 B：revise + 尾块缺失 → checklist=None、正常落稿、不炸。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path
    marker = "---REVISION-CHECKLIST---"

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            note_text = "节奏压缩"
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = note_text
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            # v2 revise：模型漏写尾块（prose 不含 marker）
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_V2, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            # 不炸 + checklist=None + 草稿正文原样保留（不被剥离成空）
            ckpt = _read_writer_node_output(db_path, v2_run_id)
            assert ckpt is not None
            assert ckpt.get("revision_checklist") is None, (
                f"revise 但尾块缺失 → revision_checklist 应为 None，got {ckpt.get('revision_checklist')!r}"
            )
            wo = ckpt.get("writer_output") or {}
            assert wo.get("prose") == _BODY_V2.rstrip()

            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            v2_content = rows[0]["content"]
            assert marker not in v2_content
            assert v2_content == _BODY_V2.rstrip()
            assert len(rows) == 2

    asyncio.run(run())


def test_revise_mode_bad_json_checklist_degrades_to_none(tmp_path: Path):
    """用例 C：revise + 尾块坏 JSON → checklist=None、正常落稿、不炸。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path
    marker = "---REVISION-CHECKLIST---"

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            note_text = "节奏压缩"
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = note_text
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            # v2 revise：尾块存在但 JSON 坏
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": _writer_script_with_marker_but_bad_json(_BODY_V2),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            ckpt = _read_writer_node_output(db_path, v2_run_id)
            assert ckpt is not None
            assert ckpt.get("revision_checklist") is None, (
                f"revise + 坏 JSON → checklist 应为 None，got {ckpt.get('revision_checklist')!r}"
            )
            # 关键：尾块被剥离（marker 不出现在 prose/draft），草稿正文是剥离后正文
            wo = ckpt.get("writer_output") or {}
            assert marker not in (wo.get("prose") or ""), (
                f"坏 JSON 也应剥离 marker，got {wo.get('prose')!r}"
            )

            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 2
            v2_content = rows[0]["content"]
            assert marker not in v2_content
            assert v2_content == _BODY_V2.rstrip()

    asyncio.run(run())


def test_write_mode_stray_marker_is_stripped(tmp_path: Path):
    """用例 D（2026-09-21 检修 m5 改写）：write 模式正文含标记行 → **按内容剥离**。

    旧契约「write 模式不解析不剥离」依赖 prompt 纪律（writer-v3.md 明令 write 模式
    不要输出尾块），但那是约定不是强制。模型一旦在 write / length-retry 轮吐出尾块，
    旧行为会让它原样落进 ``drafts.content`` 并抬高 word_count，而字数带按含尾块的
    数字判定。新契约：尾块**在内容里出现就剥**（与 mode 无关），但 write 模式下
    ``revision_checklist`` 仍为 None——不进审计面（审计面只登记 revise 的定向核销）。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path
    marker = "---REVISION-CHECKLIST---"

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "开篇")

            # 无 draft、无 revision_note → 走 write 模式；正文故意带同名 marker
            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script_with_marker_in_write_mode(_BODY_NO_TAIL),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            run_id = r.json()["run_id"]

            # write 模式下：尾块被剥（不进正文），但不进审计面
            ckpt = _read_writer_node_output(db_path, run_id)
            assert ckpt is not None
            assert ckpt.get("revision_checklist") is None, (
                f"write 模式 → revision_checklist 应为 None（不进审计面），"
                f"got {ckpt.get('revision_checklist')!r}"
            )
            wo = ckpt.get("writer_output") or {}
            assert marker not in (wo.get("prose") or ""), (
                "write 模式下 stray 尾块应被剥离（m5：防 prompt 违约落库）"
            )

            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert marker not in rows[0]["content"], (
                "write 模式 → drafts.content 不含 stray 尾块"
            )

    asyncio.run(run())


def test_revise_mode_word_count_excludes_checklist(tmp_path: Path):
    """用例 E：word_count 只统计剥离后正文（self_report.word_count 被覆写）。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            note_text = "节奏压缩\n对话加快"
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = note_text
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            # v2 revise：合规尾块；self_report.word_count 由 mock 故意写成"含尾块"长度
            checklist_payload = [
                {"item": "节奏压缩", "status": "done", "note": "已收紧"},
                {"item": "对话加快", "status": "done", "note": "已加快"},
            ]
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_V2, checklist_payload),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            # 主断言：writer_output.self_report.word_count == len(_BODY_V2.rstrip())
            # （剥离后正文字符数），而非 mock 写入的"含尾块"长度。
            ckpt = _read_writer_node_output(db_path, v2_run_id)
            wo = ckpt.get("writer_output") or {}
            sr = wo.get("self_report") or {}
            expected = len(_BODY_V2.rstrip())
            assert sr.get("word_count") == expected, (
                f"word_count 应覆写为剥离后正文长度 {expected}，got {sr.get('word_count')!r}"
            )

            # 副断言：save_draft 返回的 word_count 也等于剥离后长度
            # （save_draft 节点产出经 checkpoint_json 透出，可查）
            assert isinstance(ckpt.get("word_count"), int)
            assert ckpt["word_count"] == expected, (
                f"save_draft 节点 word_count 应等于剥离后正文长度 {expected}，"
                f"got {ckpt.get('word_count')!r}"
            )

    asyncio.run(run())


def test_revise_under_band_flip_records_audit_superseded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """用例 F（改稿审计断链 1a）：revise 首稿欠带触发翻模 → 审计面留痕。

    翻模路径：_build_length_retry_payload 把重写轮强制 mode='write'，被采纳终稿是
    write 轮 → 旧代码 revision_checklist 为 None 且无任何痕迹（核销承诺随首稿蒸发）。
    新契约：节点产出 revision_audit.checklist_status == 'superseded_by_under_band_rewrite'，
    requested_mode=revise / final_mode=write 可读，preserved_ratio 为 None（终稿非
    revise，保真比无语义）。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        # 覆盖 autouse 夹具的 0：本用例必须让「欠带 → 节点内翻模重写」真实发生一轮
        # （autouse 与本用例共用同一 function 级 monkeypatch 实例，后设的生效）
        monkeypatch.setenv("NOVELOS_WRITER_LENGTH_RETRIES", "1")
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            # v1 write（retries=1：欠带 → 重写轮消费 mock 重复项，仍欠带，采纳首次）
            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            # 写 revision_note 触发 revise 请求
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = "节奏压缩"
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            # v2：第 1 次调用走 revise（合规尾块），欠带 → 第 2 次翻模 write 落带
            checklist_payload = [
                {"item": "节奏压缩", "status": "done", "note": "已收紧"},
            ]
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": [
                    json.dumps(
                        {
                            "schema_version": "writer-output.v1",
                            "prompt_version": "writer:v1",
                            "chapter_id": "ch_xxx",
                            "prose": _BODY_V2
                            + "\n\n---REVISION-CHECKLIST---\n"
                            + json.dumps(checklist_payload, ensure_ascii=False),
                            "self_report": {
                                "slots_filled": ["slot_001"],
                                "word_count": len(_BODY_V2),
                                "scene_count": 1,
                                "deviations": [],
                                "forbidden_word_hits": [],
                                "self_check_notes": "",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "schema_version": "writer-output.v1",
                            "prompt_version": "writer:v1",
                            "chapter_id": "ch_xxx",
                            "prose": "长" * 2600,
                            "self_report": {
                                "slots_filled": ["slot_001"],
                                "word_count": 2600,
                                "scene_count": 1,
                                "deviations": [],
                                "forbidden_word_hits": [],
                                "self_check_notes": "",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ],
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            ckpt = _read_writer_node_output(db_path, v2_run_id)
            assert ckpt is not None
            # 翻模确实发生（采纳的是第 2 次 write 轮）
            assert ckpt.get("writer_length_accepted_attempt") == 2, ckpt.get(
                "writer_length_attempts"
            )
            # 主断言 1：终稿是 write → revision_checklist 保持 None（语义不变）
            assert ckpt.get("revision_checklist") is None, (
                f"翻模终稿 write → revision_checklist 应为 None，"
                f"got {ckpt.get('revision_checklist')!r}"
            )
            # 主断言 2：revision_audit 留痕——翻模可见，不再静默蒸发
            audit = ckpt.get("revision_audit")
            assert isinstance(audit, dict), (
                f"writer 节点应产出 revision_audit，got {audit!r}"
            )
            assert audit["requested_mode"] == "revise", audit
            assert audit["final_mode"] == "write", audit
            assert audit["checklist_status"] == "superseded_by_under_band_rewrite", audit
            assert audit["preserved_ratio"] is None, audit
            assert audit["deviations_count"] == 0, audit

    asyncio.run(run())


def test_revise_normal_records_revision_audit(tmp_path: Path):
    """用例 G：正常 revise（终稿仍 revise）→ status='recorded' + 保真比进审计面。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers_v1 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_NO_TAIL, None),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v1},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
                plan = json.loads(row["plan_json"]) if row["plan_json"] else {}
                plan["revision_note"] = "节奏压缩\n对话加快"
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), cid),
                )
                conn.commit()
            finally:
                conn.close()

            checklist_payload = [
                {"item": "节奏压缩", "status": "done", "note": "已收紧第2段"},
            ]
            mock_providers_v2 = {
                "director": _director_script(),
                "writer": _writer_script_with_checklist(_BODY_V2, checklist_payload),
                "observer": _observer_noop_script(),
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers_v2},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"])
            v2_run_id = r.json()["run_id"]

            ckpt = _read_writer_node_output(db_path, v2_run_id)
            assert ckpt is not None
            audit = ckpt.get("revision_audit")
            assert isinstance(audit, dict), f"应产出 revision_audit，got {audit!r}"
            assert audit["requested_mode"] == "revise", audit
            assert audit["final_mode"] == "revise", audit
            assert audit["checklist_status"] == "recorded", audit
            # 保真比：v1 全文 → v2 定向压缩，实测 0.845（高于阈值，放行）
            ratio = audit.get("preserved_ratio")
            assert isinstance(ratio, (int, float)) and ratio > REVISE_MIN_PRESERVED_RATIO, audit
            assert audit["deviations_count"] == 0, audit
            # revision_checklist 语义不变：recorded 时非 None
            assert isinstance(ckpt.get("revision_checklist"), list)

    asyncio.run(run())
