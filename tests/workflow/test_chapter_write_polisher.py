"""chapter-write P1 Polisher 节点测试。

覆盖：
1. mock_providers 含 polisher：润色输出被落库为 drafts.content；
2. mock_providers 仅含 writer（既有 mock 流）：polisher 直通，draft.content 不变（回归）；
3. mock_providers 含 polisher 但输出超长度（Δ>10%）：兜底回退 writer 原始 prose。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection

# ---------------------------------------------------------------------------
# 异步轮询 / 装配 helpers（与既有 chapter_write 测试一致）
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


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    import asyncio as _aio
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
        await _aio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")


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


async def _make_project(app, name: str = "润色测试项目") -> str:
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


# ---------------------------------------------------------------------------
# Mock 脚本
# ---------------------------------------------------------------------------


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "夜谈中女主第一次怀疑男主",
                "core_conflict": "求真 vs 隐瞒",
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
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "建议场景数 1",
            },
            ensure_ascii=False,
        )
    ]


_WRITER_BASE_PROSE = (
    "他走进门。她看着他。他沉默了一会儿。"
    "他忽然开口，仿佛一切早已注定，如同命运的齿轮缓缓转动。"
)
# polisher 默认润色版（与 _WRITER_BASE_PROSE 长度守恒 ±10% 范围内）：
# 原 45 字符 → 允许 40-50 字符。下面这版 44 字符（在范围内）。
_POLISHED_BASE = (
    "门里没有声音。林渊抬脚跨进门槛。"
    "她追着他的目光，没有出声。屋里安静片刻，他才慢慢开口。"
)


def _writer_script(prose: str | None = None) -> list[str]:
    """合规 writer-output.v1 输出。prose 为 None 时使用含 AI 味的固定示例正文。"""
    if prose is None:
        prose = _WRITER_BASE_PROSE
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


def _polisher_script(polished: str | None = None, *, summary: str = "去掉『仿佛』『如同』『忽然』，拆开主语排比") -> list[str]:
    """合规 polisher-output.v1 输出。polished=None 时使用与 _WRITER_BASE_PROSE 长度守恒的轻度润色版。"""
    if polished is None:
        polished = _POLISHED_BASE
    return [
        json.dumps(
            {
                "schema_version": "polisher-output.v1",
                "prompt_version": "polisher:v1",
                "polished_text": polished,
                "changes_summary": summary,
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chapter_write_polisher_writes_polished_text_into_drafts(tmp_path: Path):
    """mock_providers 含 polisher → drafts.content 应等于润色版（≠ writer 原始 prose）。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            polished_text = _POLISHED_BASE
            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "polisher": _polisher_script(polished=polished_text),
                "observer": _observer_noop_script(),
            }

            # plan + write
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            # 主断言：drafts.content == 润色版（而非 writer 原始 prose）
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert rows[0]["content"] == polished_text, (
                f"drafts.content 应等于润色版；\n  got: {rows[0]['content']!r}\n  expected: {polished_text!r}"
            )
            # 反向断言：与 writer 原始 prose 不一致（证明润色确实生效）
            assert rows[0]["content"] != _WRITER_BASE_PROSE, (
                "drafts.content 不应等于 writer 原始 prose；polisher 应已被调用"
            )

    asyncio.run(run())


def test_chapter_write_polisher_mock_passthrough_keeps_original_prose(tmp_path: Path):
    """mock_providers 仅含 writer、不含 polisher → polisher 直通，drafts.content = writer 原始 prose。

    回归用例：保证既有的『mock writer-only』测试流不被 polisher 节点阻断。
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
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock_providers},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))

            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert rows[0]["content"] == _WRITER_BASE_PROSE, (
                f"mock 直通场景下 drafts.content 应等于 writer 原始 prose（未润色）；"
                f"got: {rows[0]['content']!r}"
            )

    asyncio.run(run())


def test_chapter_write_polisher_length_drift_falls_back_to_original(tmp_path: Path):
    """polisher 输出与原文 visible_chars 差距 >10% → 兜底回退 writer 原始 prose。

    模拟润色模型丢内容 / 大幅扩写的边界场景；保证 save_draft 不被阻断且 draft 可用。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            # 构造一个明显短于原文的 polished_text（远超 10% 差距）
            too_short = "短了。"

            mock_providers = {
                "director": _director_script(),
                "writer": _writer_script(),
                "polisher": _polisher_script(polished=too_short, summary="length drift 测试"),
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

            # 主断言：length drift 触发兜底回退，drafts.content == writer 原始 prose
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC",
                    (cid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert rows[0]["content"] == _WRITER_BASE_PROSE, (
                f"长度漂移场景下 drafts.content 应回退到 writer 原始 prose；"
                f"got: {rows[0]['content']!r}"
            )

    asyncio.run(run())
