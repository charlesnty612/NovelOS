"""chapter_commit observer 双腿并发（V3.5 P0-1）测试。

覆盖点（packages/workflows/chapter_commit/pipeline.py 的并行改造）：
1. 默认（NOVELOS_OBSERVER_PARALLEL=on + split on）：双腿同时调 ``run_agent``，
   wall_time ≈ max(leg_a, leg_b)；测试通过 MockProvider 注入 ``time.sleep`` 验证
   wall_time 显著小于 sum(leg_a_sleep, leg_b_sleep)。
2. NOVELOS_OBSERVER_PARALLEL=off：回退串行，wall_time ≈ sum（同时也不强制死等
   MockProvider sleep——验证「不再用 ThreadPoolExecutor」即可）。
3. ctx['observer_parallel']=False 显式覆盖优先于 env。
4. parallel mode 下 ``observer_split_meta`` 记录 ``parallel=True`` +
   ``parallel_wall_ms`` 数值。
5. 单腿失败冒泡（与原串行路径异常语义一致）。
6. 已有 split 测试全部 PASS（无回归）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.workflows.chapter_commit import observer as _observer_mod
from packages.workflows.chapter_commit.pipeline import (
    _observer_parallel_enabled,
    _run_observer_legs_in_parallel,
)


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
    import asyncio
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
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")
# ---------------------------------------------------------------------------
# 纯函数：开关 + 并发辅助（不依赖 DB）
# ---------------------------------------------------------------------------


def test_observer_parallel_enabled_default_on():
    """默认（env 未设）下 parallel=on；ctx 未设时取 env。"""
    import os as _os

    saved = _os.environ.pop("NOVELOS_OBSERVER_PARALLEL", None)
    try:
        assert _observer_parallel_enabled() is True
    finally:
        if saved is not None:
            _os.environ["NOVELOS_OBSERVER_PARALLEL"] = saved


def test_observer_parallel_enabled_off_env(monkeypatch):
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "off")
    assert _observer_parallel_enabled() is False
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "0")
    assert _observer_parallel_enabled() is False
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "false")
    assert _observer_parallel_enabled() is False


def test_observer_parallel_enabled_ctx_override(monkeypatch):
    """ctx['observer_parallel']=False 优先于 env=on → off。"""
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "on")
    assert _observer_parallel_enabled({"observer_parallel": False}) is False
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "off")
    assert _observer_parallel_enabled({"observer_parallel": True}) is True


def test_observer_parallel_enabled_ctx_string(monkeypatch):
    """ctx 字符串值"off"/"on"等价 bool。"""
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "off")
    assert _observer_parallel_enabled({"observer_parallel": "on"}) is True
    assert _observer_parallel_enabled({"observer_parallel": "off"}) is False


# ---------------------------------------------------------------------------
# 端到端：ThreadPoolExecutor 真并发的 wall_time 验证
#
# 手段：用 ``mock_script`` 作为 callable（接受 i 返回响应字符串）。但 callable
# 模式下被 MockProvider 用来取 text，不便于注入 sleep。改为「FakeProvider」——
# 通过 monkey-patch ``packages.core.model_router.providers.MockProvider.complete``
# 让两条腿同时各 sleep 一段时间，验证 wall_time 接近 max 而非 sum。
# ---------------------------------------------------------------------------


def _make_full_observer_noop_text() -> str:
    """完整 7 数组 JSON 文本；抽取后供 validator 通过。"""
    return json.dumps(
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


def _install_sleepy_observer(monkeypatch, sleep_seconds: float) -> list[tuple[float, str]]:
    """用 sleep 包装 ``run_agent``（仅当 expected == 'observer' 时注入 sleep + noop 文本）。

    返回每条 observer 腿调用的 (start_monotonic, scope) 列表——供测试断言：
    - 调用次数 = 2（off 路径下回退同步也至少调 2 次）
    - 两条腿的 start 时间应近乎重叠（并发）
    """
    # 不打全局 MockProvider.complete（director/writer 也用 mock，他们必须按 mock_script 走
    # 各自契约才能让 chapter 走到 REVIEWED → commit 节点）。
    # 改为：monkey-patch ``packages.workflows.chapter_commit.pipeline.run_agent``，让
    # observer 路径上的 run_agent 同步执行 sleep + 返回 7 数组 noop；其它节点原样透传。
    timings: list[tuple[float, str]] = []
    real_run_agent = _observer_mod.run_agent

    def _patched(db_path, agent_name, payload, run_id, **kwargs):
        expected = kwargs.get("expected")
        if expected == "observer":
            scope = payload.get("extraction_scope") or "single"
            start = time.monotonic()
            time.sleep(sleep_seconds)
            timings.append((start, scope))
            return {
                "text": _make_full_observer_noop_text(),
                "usage": {"prompt": 0, "completion": 0, "total": 0},
            }
        return real_run_agent(db_path, agent_name, payload, run_id, **kwargs)

    monkeypatch.setattr(_observer_mod, "run_agent", _patched)
    return timings


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs):
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str = "ParallelProject") -> str:
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


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "并行测试章",
                "core_conflict": "双腿并发",
                "turning_point": "双腿并发，wall_time 接近 max",
                "expected_role": "setup",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "并行场景",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "并行测试",
                    }
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "test",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script() -> list[str]:
    prose = "测试章节正文，用于双腿并发验证。"
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


def _observer_full_noop_script() -> list[str]:
    return [_make_full_observer_noop_text()]


async def _push_chapter_to_reviewed(app, pid: str, cid: str, mock_providers: dict) -> None:
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
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    paused = r.json()
    paused = await _wait_run_terminal(app, paused["run_id"], expected=("PAUSED",))
    r = await _request(
        app, "POST", f"/api/runs/{paused['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200, r.text
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    assert r2["status"] == "COMPLETED"


def test_observer_parallel_wall_time_significantly_less_than_sum(tmp_path: Path, monkeypatch):
    """双腿并发 wall_time 显著小于双腿串行相加。

    实现：
    - monkey-patch pipeline.run_agent 在 expected == "observer" 时 sleep 0.6s
      并返回 noop 7 数组；其它 agent（director/writer）走原路径。
    - 并发预期：两条 observer 腿几乎同时启动（start 时间差 << sleep）；
      wall_time ≈ sleep（max），< sleep * 1.7 容许线程调度开销。
    - 串行预期：第二条腿 start ≥ 第一条 end ⇒ 双腿 sum > sleep * 1.5。
    - 主断言：
      1. observer_split_meta.parallel is True
      2. observer_split_meta.parallel_wall_ms < sleep * 1.7
      3. 两条腿的 start 时间差 < sleep * 0.5（极强并发证据）
    """
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "on")
    sleep_seconds = 0.6
    timings = _install_sleepy_observer(monkeypatch, sleep_seconds=sleep_seconds)
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "并行")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_full_noop_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": base_mocks},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            assert r2["status"] == "COMPLETED"
            run_id = r.json()["run_id"]

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            ckpt = json.loads(row["checkpoint_json"] or "{}")
            smeta = ckpt.get("observer_split_meta") or {}
            assert smeta.get("parallel") is True, smeta
            wall_ms = smeta.get("parallel_wall_ms")
            assert isinstance(wall_ms, int), smeta
            # wall_ms 应 < sleep_seconds * 1700ms（容忍调度开销，但显著低于串行 sum=2*sleep）。
            assert wall_ms < sleep_seconds * 1700, (
                f"并发 wall_ms={wall_ms} 远超期望上限 {sleep_seconds * 1700}ms；"
                "ThreadPoolExecutor 可能未生效"
            )
            # timings 应有 2 条记录（leg_a + leg_b），且两条腿 start 时间差 < sleep 50%
            observer_timings = [t for t in timings if t[1] in ("entities", "narrative")]
            assert len(observer_timings) == 2, (
                f"observer 应被调 2 次，实际 {len(observer_timings)} 次 timings={timings}"
            )
            starts = sorted([t[0] for t in observer_timings])
            start_diff = starts[1] - starts[0]
            assert start_diff < sleep_seconds * 0.5, (
                f"两腿启动间隔 {start_diff:.3f}s 接近 sleep={sleep_seconds}s；可能回退串行"
            )

    asyncio.run(run())


def test_observer_parallel_off_returns_to_serial(tmp_path: Path, monkeypatch):
    """env 关 parallel：wall_ms 字段不存在；observer_split_meta.parallel=False；两腿串行。"""
    monkeypatch.setenv("NOVELOS_OBSERVER_SPLIT", "on")
    monkeypatch.setenv("NOVELOS_OBSERVER_PARALLEL", "off")
    sleep_seconds = 0.4
    timings = _install_sleepy_observer(monkeypatch, sleep_seconds=sleep_seconds)
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            await _make_character(app, pid, "林夕")
            cid = await _make_chapter(app, pid, 1, "串行")

            base_mocks = {
                "director": _director_script(),
                "writer": _writer_script(),
                "observer": _observer_full_noop_script(),
            }
            await _push_chapter_to_reviewed(app, pid, cid, base_mocks)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": base_mocks},
            )
            assert r.status_code == 201, r.text
            await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
            r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
            assert r2["status"] == "COMPLETED"
            run_id = r.json()["run_id"]

            db_path = app.state.settings.db_path
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            ckpt = json.loads(row["checkpoint_json"] or "{}")
            smeta = ckpt.get("observer_split_meta") or {}
            assert smeta.get("parallel") is False, smeta
            # off 路径不写 parallel_wall_ms（仅并发路径有值）
            assert smeta.get("parallel_wall_ms") is None, smeta
            # timings 仍应有 2 条 observer（leg_a + leg_b），但顺序串行：第二条腿 start
            # ≥ sleep 后
            observer_timings = [t for t in timings if t[1] in ("entities", "narrative")]
            assert len(observer_timings) == 2
            starts = sorted([t[0] for t in observer_timings])
            start_diff = starts[1] - starts[0]
            # 串行：第二条腿必然在前一条腿 sleep 之后才被调用 ⇒ 间隔 ≈ sleep
            assert start_diff >= sleep_seconds * 0.8, (
                f"串行路径两条腿应间隔 sleep≈{sleep_seconds}s，实际 {start_diff:.3f}s"
            )

    asyncio.run(run())


def test_observer_parallel_unit_helper_invokes_run_agent_twice(monkeypatch):
    """单测：``_run_observer_legs_in_parallel`` 同时启动腿 A / 腿 B，
    并收齐两条腿的输出。伪造 runner 替代真 ``run_agent`` 验证并发语义。"""
    timings: list[tuple[float, str]] = []
    sleep_per_leg = 0.3  # ≤5s

    def _fake_runner(db_path, agent_name, payload, run_id, **kwargs):
        # payload["extraction_scope"] 区分两条腿
        scope = payload.get("extraction_scope")
        start = time.monotonic()
        time.sleep(sleep_per_leg)
        timings.append((start, scope))
        # 返回 scope 专属 7 数组（entities 腿负责 character_changes/等；narrative 负责 events 等）
        if scope == "entities":
            return {
                "character_changes": [{"change_id": "cc_001"}],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
        return {
            "character_changes": [],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [{"change_id": "ev_001"}],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }

    monkeypatch.setattr(
        "packages.workflows.chapter_commit.observer.run_agent", _fake_runner
    )

    leg_a_payload = {"extraction_scope": "entities"}
    leg_b_payload = {"extraction_scope": "narrative"}
    a, b, wall_ms = _run_observer_legs_in_parallel(
        db_path=":memory:",
        run_id="r1",
        node_run_id="n1",
        leg_a_payload=leg_a_payload,
        leg_b_payload=leg_b_payload,
        leg_a_mock=None,
        leg_b_mock=None,
    )
    # 两条腿都已被调
    assert len(timings) == 2
    scopes = sorted([s for _, s in timings])
    assert scopes == ["entities", "narrative"]
    # 并发：两条腿的开始时间几乎重叠——不应该相差超过 sleep_per_leg 的 30%
    start_a = next(t for t, s in timings if s == "entities")
    start_b = next(t for t, s in timings if s == "narrative")
    assert abs(start_a - start_b) < sleep_per_leg * 0.3, (
        f"两腿应近乎同时启动，diff={abs(start_a - start_b):.3f}s"
    )
    # wall_ms ≈ sleep_per_leg（不是 2*sleep_per_leg）
    assert wall_ms < sleep_per_leg * 1000 * 1.7, (
        f"wall_ms={wall_ms} 远大于单腿 sleep_per_leg={sleep_per_leg}s；并发可能失败"
    )
    # 合并语义正确
    assert a["character_changes"][0]["change_id"] == "cc_001"
    assert b["new_events"][0]["change_id"] == "ev_001"


def test_observer_parallel_unit_helper_one_leg_failure_propagates(monkeypatch):
    """单腿异常应向 future 透传，被 as_completed 收集阶段 re-raise。"""

    def _fake_runner(db_path, agent_name, payload, run_id, **kwargs):
        scope = payload.get("extraction_scope")
        if scope == "entities":
            raise RuntimeError("leg_a synthetic failure")
        time.sleep(0.05)
        return {
            "character_changes": [],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }

    monkeypatch.setattr(
        "packages.workflows.chapter_commit.observer.run_agent", _fake_runner
    )

    raised = False
    try:
        _run_observer_legs_in_parallel(
            db_path=":memory:",
            run_id="r1",
            node_run_id="n1",
            leg_a_payload={"extraction_scope": "entities"},
            leg_b_payload={"extraction_scope": "narrative"},
            leg_a_mock=None,
            leg_b_mock=None,
        )
    except RuntimeError as exc:
        raised = "leg_a synthetic failure" in str(exc)
    assert raised, "单腿异常应原样透传"
