"""chapter_write 修订模式支持（基于现有 draft 局部修改）。

需求：用户审校驳回带 note → chapter-review 把 note 落 chapters.plan_json.revision_note
→ 再触发 chapter-write 时，writer 应基于「最新 draft 全文 + revision_note」做局部修改，
而不是整章重写。

覆盖用例：
- A：chapter 有 draft（v1） + plan_json.revision_note 非空 → 启动 write →
  writer_input 含 draft_text（= v1 content）、mode='revise'、revision_note 非空。
- B：无 revision_note（首次写）→ mode='write'、draft_text 可为空、writer 仍能正常写出。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection




# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload
async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    """轮询直到 run.status ∈ expected；返回最终 run dict。

    SQLite 跨连接视角 + 后台线程落库时延：单节点 mock 流程通常 < 1s 跑完，
    但 polling 必须等到节点行 FAILED/COMPLETED 也写入——轮询间隔 0.2s 足以。
    """
    import asyncio, time
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
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")
# ---------------------------------------------------------------------------
# Fixtures（与 test_chapter_pipelines.py 口径一致，便于 diff）
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


async def _make_project(app, name: str = "修订测试项目") -> str:
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


def _writer_script(prose: str | None = None) -> list[str]:
    """合规 writer-output.v1 输出。prose 为 None 时使用固定示例正文。"""
    if prose is None:
        prose = (
            "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
            "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。林渊立在博古架前，"
            "背对着她，似乎在翻检什么。"
        )
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


def _read_writer_input_from_checkpoint(db_path: str, run_id: str) -> dict | None:
    """从 workflow_runs.checkpoint_json 读 writer_input（_writer_node 返回值）。"""
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
    ckpt = json.loads(row["checkpoint_json"])
    return ckpt.get("writer_input")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chapter_write_revise_mode_injects_draft_and_note(tmp_path: Path):
    """用例 A：有 draft + revision_note → writer_input 含 draft_text + mode='revise'。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 1) 首次 plan + write v1（产生第一份 draft）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            first_write_run_id = r.json()["run_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            # 捕获 v1 write run_id，验 writer_input 是 write 模式
            v1_run_id = r.json()["run_id"]

            # 确认 drafts 表已落 v1
            conn = get_connection(db_path)
            try:
                drafts = conn.execute(
                    "SELECT content, version FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 1
            v1_content = drafts[0]["content"]
            assert v1_content and len(v1_content) > 0

            # baseline：v1 这次 write 是 write 模式（无 revision_note）
            v1_writer_input = _read_writer_input_from_checkpoint(db_path, v1_run_id)
            assert v1_writer_input is not None, "v1 writer_input 应落 checkpoint_json"
            assert v1_writer_input.get("mode") == "write"
            assert not v1_writer_input.get("draft_text"), "v1 不应有 draft_text"
            assert not v1_writer_input.get("revision_note"), "v1 不应有 revision_note"

            # 2) 模拟 chapter-review 驳回并写 note 进 chapters.plan_json.revision_note
            note_text = "对话节奏偏慢，第二段苏婉清内心戏过长，建议压缩到 2 句。"
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

            # 3) 第二次 write：此时应有 draft + revision_note → 走 revise 模式
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            v2_run_id = r.json()["run_id"]

            writer_input = _read_writer_input_from_checkpoint(db_path, v2_run_id)
            assert writer_input is not None, "v2 writer_input 应落 checkpoint_json"

            # 主断言：模式 = revise、draft_text = v1 content、revision_note 非空
            assert writer_input.get("mode") == "revise", (
                f"expected mode='revise', got {writer_input.get('mode')!r}; "
                f"revision_note={writer_input.get('revision_note')!r}, "
                f"draft_text len={len(writer_input.get('draft_text') or '')}"
            )
            assert writer_input.get("draft_text") == v1_content, (
                "draft_text 应等于最新 draft（v1）的 content 全文"
            )
            assert writer_input.get("revision_note") == note_text, (
                "revision_note 应等于 chapters.plan_json.revision_note"
            )

            # 副断言：plan_json.chapter_goal 等核心字段保留（确保 payload 未被破坏）
            dp = writer_input.get("director_plan", {})
            assert dp.get("chapter_goal"), "director_plan.chapter_goal 应保留"

            # 4) drafts 落库：v2 应比 v1 多一条
            conn = get_connection(db_path)
            try:
                drafts = conn.execute(
                    "SELECT content, version FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(drafts) == 2, drafts

    asyncio.run(run())


def test_chapter_write_default_mode_is_write_no_revision_note(tmp_path: Path):
    """用例 B：无 revision_note → mode='write'、draft_text 可为空、writer 正常写出。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "开篇")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 1) plan
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 2) write（无 revision_note，无 draft → 应为 write 模式）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            write_run_id = r.json()["run_id"]

            writer_input = _read_writer_input_from_checkpoint(db_path, write_run_id)
            assert writer_input is not None
            assert writer_input.get("mode") == "write"
            assert writer_input.get("draft_text") == "", "无 draft → draft_text 应为空串"
            assert not writer_input.get("revision_note"), "无 revision_note → 顶层不应有"

            # 3) drafts 落库正常
            conn = get_connection(db_path)
            try:
                drafts = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ?", (cid,)
                ).fetchall()
                ch = conn.execute(
                    "SELECT status FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()
            finally:
                conn.close()
            assert len(drafts) == 1 and (drafts[0]["content"] or "")
            assert ch["status"] == "DRAFTED"

    asyncio.run(run())


def test_chapter_write_fresh_write_overrides_revise_to_write(tmp_path: Path):
    """用例 A：fresh_write=True 时，即便存在 draft + revision_note，也强制 mode='write'。

    用于跨模型文风对比：忽略旧稿与改稿意见，全章重写。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            # 1) 首次 plan + write v1（产生第一份 draft）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 2) 模拟 chapter-review 驳回并写 note 进 chapters.plan_json.revision_note
            note_text = "重写整章，风格从古朴换为现代。"
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

            # 3) 第二次 write：带 fresh_write=True → 即便有 draft + revision_note，也走 write 模式
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers, "fresh_write": True},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            v2_run_id = r.json()["run_id"]

            writer_input = _read_writer_input_from_checkpoint(db_path, v2_run_id)
            assert writer_input is not None
            assert writer_input.get("mode") == "write", (
                f"fresh_write=True 应强制 mode='write'，got {writer_input.get('mode')!r}"
            )
            # 关键断言：fresh_write=True 时 payload 不应带 draft_text / revision_note 键
            assert "draft_text" not in writer_input or writer_input.get("draft_text") == "", (
                f"fresh_write=True 时 writer_input 不应有 draft_text，got {writer_input.get('draft_text')!r}"
            )
            assert "revision_note" not in writer_input, (
                f"fresh_write=True 时 writer_input 不应有 revision_note 键，got {writer_input.get('revision_note')!r}"
            )

    asyncio.run(run())


def test_chapter_write_without_fresh_write_remains_revise(tmp_path: Path):
    """用例 B（回归）：不带 fresh_write 时，仍按既有逻辑 → mode='revise'。

    与 test_chapter_write_revise_mode_injects_draft_and_note 互补，本用例在
    fresh_write 引入后专做一次回归断言，确保 fresh_write 默认行为不破坏旧 revise 路径。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 写 revision_note
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

            # 不带 fresh_write（缺省 / None）→ 仍走 revise
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            run_id = r.json()["run_id"]

            writer_input = _read_writer_input_from_checkpoint(db_path, run_id)
            assert writer_input is not None
            assert writer_input.get("mode") == "revise", (
                f"不带 fresh_write 应保持 revise 模式，got {writer_input.get('mode')!r}"
            )
            assert writer_input.get("revision_note") == note_text

    asyncio.run(run())


def test_save_draft_mock_path_records_mock_model_id(tmp_path: Path):
    """回归：mock 路径下 drafts.model_id 仍记为 'mock/mock'（writer_model_id=None → 兜底）。

    mock_script 路径不消费 capability，ai_call_logs 无 writer 行 → writer_model_id=None
    → save_draft 兜底 'mock/mock'。与原硬编码口径一致。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "开篇")

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_noop_script(),
            }

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "意图", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 验证：drafts.model_id == "mock/mock"
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT model_id FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert rows[0]["model_id"] == "mock/mock", (
                f"mock 路径 drafts.model_id 应兜底 'mock/mock'，got {rows[0]['model_id']!r}"
            )

    asyncio.run(run())