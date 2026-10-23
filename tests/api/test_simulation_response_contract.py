"""`/simulate` 与 `/simulations` 的 response_model 契约测试（2026-10-15 批次（五））。

本组用例钉住三件事，其中第 3 条是**最容易在不知情时被改掉**的：

1. OpenAPI 里有 ``SimulationResult`` / ``SimulationSummary`` schema 且被端点引用；
2. 实际返回的键集与模型字段集**逐字相等**（防 ``response_model`` 过滤）；
3. ⚠️ **无实体变更时，``diff`` 只有 3 个键**（``version_a`` / ``version_b`` /
   ``branch_id``），六个实体段是**整个省略**而不是 ``null``。

   这条对应 `story_state/snapshots.py::diff_snapshots` 的「有差异才写入」，
   而保住它靠的是端点上的 ``response_model_exclude_none=True``。
   **去掉那个开关 ⇒ FastAPI 把每个 None 序列化成 ``"characters": null`` 之类，
   凭空多出六个键**，把既有契约改掉。前端 `SimulationPanel` 读法是
   ``diff.hooks?.added?.length ?? 0``，多出来的 null 恰好也能扛住——
   也就是说**这个 bug 在前端是隐形的**，只有本用例能抓到。

   判别：撤掉任一 ``response_model_exclude_none=True`` ⇒ 本用例必红。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from packages.core.ids import new_id, now_iso
from packages.core.simulation.models import (
    ChangedItem,
    SimulationResult,
    SimulationSummary,
    StateDiff,
)

from ._contract_runner import (
    assert_keys_match,
    assert_model_fields_match,
    client_ctx,
    create_test_app,
)


def _create_app(tmp_path: Path):
    """共享 runner 的 ``create_test_app``（**不**显式跑迁移；迁移在 lifespan 里跑）。"""
    return create_test_app(tmp_path)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with client_ctx(app) as c:
        return await c.request(method, path, **kwargs)


def _seeded_pid(app) -> str:
    """建项目 + 章节 + genesis，返回 pid。"""
    import asyncio

    async def run() -> str:
        async with client_ctx(app) as c:
            pid = (await c.post("/api/projects", json={"name": "推演契约"})).json()["project_id"]
            cid = (
                await c.post(
                    f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
                )
            ).json()["chapter_id"]
            r = await c.post(f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
            assert r.status_code == 201, r.text
            return pid

    return asyncio.run(run())


def _minimal_delta(chapter_id: str) -> dict:
    """一条能过校验的 delta（验证器要求 update 必须带 before）。"""
    return {
        "delta_id": new_id("dl"),
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": new_id("wfr"),
        "previous_state_version": 1,
        "created_by": "contract-test",
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": new_id("chg"),
                "op": "update",
                "target_id": new_id("tgt"),
                "character_id": new_id("char"),
                "facet": "state",
                "field": "state.location",
                "before": "客栈",
                "after": "破屋",
                "confidence": 0.9,
                "evidence": {"chapter_id": chapter_id, "excerpt": "他回到破屋。"},
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def test_simulation_schemas_declared_and_referenced(tmp_path: Path):
    app = _create_app(tmp_path)
    schema = app.openapi()
    for name in ("SimulationResult", "SimulationSummary", "StateDiff", "ListDiff"):
        assert name in schema["components"]["schemas"], f"{name} 未进 OpenAPI"
    post_ref = schema["paths"]["/api/projects/{project_id}/simulate"]["post"]["responses"][
        "201"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert post_ref.endswith("/SimulationResult"), post_ref
    # 列表端点返回的是**数组**，schema 形如 {"type":"array","items":{"$ref":…}}，
    # 不是直接的 $ref（写直接 $ref 会 KeyError——2026-10-15 实测）。
    list_schema = schema["paths"]["/api/projects/{project_id}/simulations"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert list_schema["type"] == "array", list_schema
    assert list_schema["items"]["$ref"].endswith("/SimulationSummary"), list_schema


# 期望键集**硬编码**，不写成 `set(SimulationResult.model_fields)`。
# 自指断言（拿响应与模型比）在「模型少声明一个字段」时两侧同时变小 ⇒ 恒真。
# 突变验证 M14 实测存活过这个形状（见 test_arc_response_contract.py 的同款注释）。
EXPECTED_RESULT_KEYS = {
    "simulation_id",
    "branch_id",
    "name",
    "base_version",
    "applied",
    "diff",
    "issues",
    "branch_status",
    "created_at",
}
EXPECTED_SUMMARY_KEYS = {
    "simulation_id",
    "branch_id",
    "name",
    "base_version",
    "status",
    "created_at",
}


def test_simulation_result_keys_match_hardcoded_expectation(tmp_path: Path):
    """实际返回的键集与**硬编码期望**逐字相等（POST / GET-by-id / list 三条路径）。"""
    import asyncio

    app = _create_app(tmp_path)
    pid = _seeded_pid(app)

    async def run():
        async with client_ctx(app) as c:
            cid = (
                await c.get(f"/api/projects/{pid}/chapters")
            ).json()[0]["chapter_id"]
            r = await c.post(
                f"/api/projects/{pid}/simulate",
                json={"deltas": [_minimal_delta(cid)], "name": "sim-contract-1"},
            )
            assert r.status_code == 201, r.text
            sid = r.json()["simulation_id"]

            body = r.json()
            assert_keys_match(body, EXPECTED_RESULT_KEYS, "POST /simulate 响应")

            got = (await c.get(f"/api/projects/{pid}/simulations/{sid}")).json()
            assert set(got) == EXPECTED_RESULT_KEYS

            rows = (await c.get(f"/api/projects/{pid}/simulations")).json()
            assert rows, "推演历史不应为空"
            for row in rows:
                assert_keys_match(row, EXPECTED_SUMMARY_KEYS, "GET /simulations 行")
            return body

    body = asyncio.run(run())
    # 顺带把「自定义名取不到」这条既有行为钉住（不是本次引入的，是防被改掉）
    assert body["name"] == "sim-contract-1"


def test_simulation_models_stay_in_sync_with_expectation():
    """模型字段集与硬编码期望一致（与上面的响应断言构成两道）。"""
    assert_model_fields_match(SimulationResult, EXPECTED_RESULT_KEYS)
    assert_model_fields_match(SimulationSummary, EXPECTED_SUMMARY_KEYS)


def test_empty_diff_omits_entity_sections_not_nulls(tmp_path: Path):
    """⚠️ 核心守卫：无实体变更时 diff **只有 3 个键**，实体段是省略而非 null。

    撤掉端点上的 ``response_model_exclude_none=True`` ⇒ 这里立刻多出六个 null 键 ⇒ 必红。
    """
    import asyncio

    app = _create_app(tmp_path)
    pid = _seeded_pid(app)

    async def run():
        async with client_ctx(app) as c:
            cid = (
                await c.get(f"/api/projects/{pid}/chapters")
            ).json()[0]["chapter_id"]
            r = await c.post(
                f"/api/projects/{pid}/simulate",
                json={"deltas": [_minimal_delta(cid)], "name": "sim-contract-2"},
            )
            assert r.status_code == 201, r.text
            return r.json()["diff"]

    diff = asyncio.run(run())

    assert_keys_match(
        diff,
        {"version_a", "version_b", "branch_id"},
        "diff 的键应恰好 3 个",
        hint="——多出来的多半是 exclude_none 被撤掉后序列化成 null 的实体段",
    )
    # 模型侧声明的六个条件段此刻应全为 None（与「序列化时被 exclude_none 抹掉」一致）
    for field in ("characters", "world", "hooks", "debts", "events_changed", "recent_events"):
        assert field not in diff, f"{field} 不该出现"
    assert StateDiff(**diff).hooks is None


def test_state_diff_model_accepts_full_diff():
    """非空 diff 的键集与模型对得上（直接调纯函数量，避免去凑 API）。"""
    from packages.core.story_state.snapshots import diff_snapshots

    a = {
        "characters": [{"character_id": "c1", "name": "旧名"}],
        "hooks": [],
        "debts": [],
        "events": {"e1": {"t": "x"}},
        "recent_events": [],
        "world": {"locations": {"l1": {"name": "客栈"}}},
    }
    b = {
        "characters": [
            {"character_id": "c1", "name": "新名"},
            {"character_id": "c2", "name": "新角色"},
        ],
        "hooks": [{"hook_id": "h1", "status": "RESOLVED"}],
        "debts": [],
        "events": {"e1": {"t": "y"}},
        "recent_events": [],
        "world": {"locations": {"l1": {"name": "破屋"}}},
    }
    raw = diff_snapshots(a, b, version_a=1, version_b=2, branch_id="br_x")
    parsed = StateDiff(**raw)
    dumped = parsed.model_dump()
    for field in ("characters", "world", "hooks", "events_changed"):
        assert field in dumped, f"{field} 应被解析出来"
        assert dumped[field] is not None
    assert parsed.characters is not None
    assert parsed.characters.added is not None
    assert parsed.characters.added[0]["character_id"] == "c2"
    assert parsed.characters.changed is not None
    # changed 的元素已建模为 `ChangedItem`（不是裸 dict）——这是本批刻意加的：
    # 不建模的话前端只能拿到 `{[k: string]: unknown}`，`c.id` 与 `pair.before`
    # 全是 `{}`，渲染处要到处写 `as`。
    changed = parsed.characters.changed[0]
    assert isinstance(changed, ChangedItem)
    assert changed.id == "c1"
    assert changed.changes is not None
    assert changed.changes["name"] == {"before": "旧名", "after": "新名"}
