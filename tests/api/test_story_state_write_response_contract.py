"""写面七个端点的 response_model 契约测试（2026-10-15 批次（六）· G3 快照写面）。

实测键集（临时库 + 合成夹具跑通端点后 dump，**不是读源码推的**）
=============================================================

| 端点 | 状态码 | 实测键集 |
|---|---|---|
| ``POST /projects/{pid}/state/init`` | 201 | 7 键，与 ``SnapshotResponse`` 逐键相同 |
| ``POST /projects/{pid}/deltas`` | 201 | ``delta_id`` / ``status`` / ``errors`` |
| ``POST /projects/{pid}/commits`` | 201 | ``commit_id`` / ``delta_id`` / ``state_version`` / ``snapshot_ref`` |
| ``POST /commits/{cid}/rollback`` | 201 | 上面 4 键 + ``rollback_of`` |
| ``POST /projects/{pid}/branches`` | 201 | ``branch_id`` / ``project_id`` / ``name`` / ``parent_branch_id`` / ``base_state_version`` / ``status`` / ``created_at`` |
| ``POST /projects/{pid}/branches/{bid}/promote`` | 201 | **两个形状**，见下 |
| ``GET  /projects/{pid}/state/diff`` | 200 | 无差异时**恰好 3 键** |

三处「不是想当然」的实测结论
----------------------------

1. **``state/diff`` 的「空则省略」与端点级 ``response_model_exclude_none`` 不兼容。**
   六个实体段确实是「有差异才写入」，但三个恒在键里 ``branch_id`` **恒为 null**
   （``queries.diff_versions`` 把 ``branch_id`` 写死成 ``None``，传非 None 一律 409）。
   端点级开关是一刀切的——实测加了它之后**空 diff 从 3 个键掉到 2 个**（``branch_id``
   被抹掉），那是既有契约的静默变更。⇒ 改用模型侧**字段级** ``exclude_if``，
   端点**刻意不加**该开关。判别：``test_empty_diff_keeps_branch_id_and_omits_sections``
   + ``test_diff_endpoint_declares_no_endpoint_level_exclude_none``。

2. **``promote`` 的空分支形状里 ``commit_id`` / ``delta_id`` 就是 ``null``。**
   同一条 ``exclude_none`` 的反例：加了它那两个键会消失。所以 promote 也没有该开关。
   判别：``test_promote_empty_branch_keeps_null_keys``。

3. **``commits`` / ``rollback`` 的响应里没有 delta 列表。**（任务书原以为有，实测没有：
   两者的响应就是 4/5 个标量键，逆 delta 的 7 数组落在 ``state_deltas.payload_json``，
   要读走 ``GET .../chapters/{cid}/deltas``。）真正带「列表的元素结构」的是
   ``state/diff`` 的 ``changed[]``，已建模到 ``ChangedDiffItemResponse``（= ChangedItem 那一层）。

另有两处**同名不同义**，前端读错就一定错（写进了模型 docstring，这里再钉一遍）
-------------------------------------------------------------------------------

* ``promote`` 的 ``branch_id``：空分支时 = **被 promote 的源分支**；非空时 = **main 分支**
  （源分支 id 在 ``promoted_from``）。实测两者不是同一个值。
* ``promote`` 的 ``state_version``：空分支时 = ``branches.base_state_version``（**没产生新版本**）；
  非空时 = 最后一次重放落在 main 上的版本。

禁自指断言（本轮最大的坑）
------------------------

**本文件里 ``model_fields`` 只出现在 ``test_models_stay_in_sync_with_hardcoded_expectation``
一条用例中**（可用 ``grep -n "model_fields"`` 自查）。其余用例一律拿
**硬编码常量**当模型之外的第三方基准：

* ``test_*_keys_match_hardcoded_expectation``：响应键集 vs 硬编码；
* ``test_openapi_schema_properties_match_hardcoded_expectation``：OpenAPI 里
  ``components.schemas`` 的 ``properties`` vs 硬编码——这一条是 ``extra="allow"`` 的必要
  配套（删掉字段声明时响应照样有该键，光断言响应键集抓不到，见坑区 M20）。

判别用例：``tests/mutation_specs/g3_story_state.py``（11 条）。
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.ids import new_id, now_iso
from packages.core.story_state.models import (
    BranchCreatedResponse,
    BranchPromoteResponse,
    ChangedDiffItemResponse,
    CommitResponse,
    ListDiffResponse,
    RollbackResponse,
    SnapshotResponse,
    StateDiffResponse,
    SubmitDeltaResponse,
)

# ---------------------------------------------------------------------------
# 硬编码期望键集：**模型之外的第三方基准**，不得写成 set(Model.model_fields)
# ---------------------------------------------------------------------------

EXPECTED_INIT_KEYS = {
    "state_version",
    "characters",
    "world",
    "hooks",
    "debts",
    "recent_events",
    "events",
}
EXPECTED_DELTA_KEYS = {"delta_id", "status", "errors"}
EXPECTED_COMMIT_KEYS = {"commit_id", "delta_id", "state_version", "snapshot_ref"}
EXPECTED_ROLLBACK_KEYS = EXPECTED_COMMIT_KEYS | {"rollback_of"}
EXPECTED_BRANCH_KEYS = {
    "branch_id",
    "project_id",
    "name",
    "parent_branch_id",
    "base_state_version",
    "status",
    "created_at",
}
# promote 的**并集**：9 键。任一实际响应都拿不到全集（两个形状各缺席三个）。
EXPECTED_PROMOTE_KEYS = {
    "commit_id",
    "delta_id",
    "state_version",
    "branch_id",
    "promoted_commits",
    "replayed_delta_ids",
    "status",
    "promoted_from",
    "snapshot_ref",
}
# 两个形状各自的实测键集（互不相同，这是 promote 的关键事实）
EXPECTED_PROMOTE_EMPTY_KEYS = {
    "branch_id",
    "status",
    "promoted_commits",
    "commit_id",
    "state_version",
    "delta_id",
    "replayed_delta_ids",
}
EXPECTED_PROMOTE_NONEMPTY_KEYS = {
    "commit_id",
    "delta_id",
    "state_version",
    "promoted_from",
    "promoted_commits",
    "branch_id",
    "replayed_delta_ids",
    "snapshot_ref",
}
EXPECTED_DIFF_EMPTY_KEYS = {"version_a", "version_b", "branch_id"}
EXPECTED_DIFF_SECTIONS = {
    "characters",
    "world",
    "hooks",
    "debts",
    "events_changed",
    "recent_events",
}

_ENDPOINTS = (
    ("/api/projects/{project_id}/state/init", "post", "SnapshotResponse"),
    ("/api/projects/{project_id}/deltas", "post", "SubmitDeltaResponse"),
    ("/api/projects/{project_id}/commits", "post", "CommitResponse"),
    ("/api/commits/{commit_id}/rollback", "post", "RollbackResponse"),
    ("/api/projects/{project_id}/branches", "post", "BranchCreatedResponse"),
    ("/api/projects/{project_id}/branches/{branch_id}/promote", "post", "BranchPromoteResponse"),
    ("/api/projects/{project_id}/state/diff", "get", "StateDiffResponse"),
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    """⚠️ 迁移只在 lifespan 里跑（`httpx.ASGITransport` 不触发 lifespan）。"""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _client(app) as c:
        return await c.request(method, path, **kwargs)


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


async def _seeded(tmp_path: Path):
    """建项目 + 角色 + 章节 + genesis。返回 ``(app, pid, char_id, chapter_id, base_version)``。

    ⚠️ 全 async：这些用例跑在 ``anyio`` 事件循环里，**不得**在内部再 ``asyncio.run``。
    """
    app = _create_app(tmp_path)
    async with _client(app) as c:
        pid = (await c.post("/api/projects", json={"name": "写面契约"})).json()["project_id"]
        char = (
            await c.post(
                f"/api/projects/{pid}/characters",
                json={"name": "林夕", "role": "protagonist"},
            )
        ).json()["character_id"]
        chap = (
            await c.post(f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"})
        ).json()["chapter_id"]
        r = await c.post(f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
        assert r.status_code == 201, r.text
        return app, pid, char, chap, r.json()["state_version"]


def _rich_delta(chapter_id: str, character_id: str, prev_version: int, location: str) -> dict:
    """一条能过校验、且能同时点亮 characters/hooks/debts/events_changed 四段的 delta。"""
    return {
        "delta_id": new_id("dl"),
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": new_id("wfr"),
        "previous_state_version": prev_version,
        "created_by": "contract-test",
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": new_id("cc"),
                "op": "update",
                "target_id": character_id,
                "character_id": character_id,
                "facet": "state",
                "field": "state.location",
                "before": "Unknown",
                "after": location,
                "confidence": 0.9,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                "change_id": new_id("ne"),
                "op": "add",
                "target_id": new_id("et"),
                "event_id": new_id("evt"),
                "type": "other",
                "participants": [character_id],
                "time": {"timeline_day": 1, "in_story_date": "辰时"},
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "resolved_hooks": [],
        "new_hooks": [
            {
                "change_id": new_id("nh"),
                "op": "add",
                "target_id": new_id("ht"),
                "hook_id": new_id("hk"),
                "name": "三年之约",
                "importance": 0.8,
                "description": "埋线",
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "debt_changes": [
            {
                "change_id": new_id("dc"),
                "op": "add",
                "target_id": new_id("dt"),
                "debt_id": new_id("db"),
                "description": "欠他一条命",
                "status_after": "open",
                "severity_after": 0.8,
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. OpenAPI 声明
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_openapi_reports_expected_models(tmp_path: Path):
    """七个端点的响应都引用各自的模型（去掉任一 ``response_model`` 这里立刻红）。"""
    spec = (await _request(_create_app(tmp_path), "GET", "/openapi.json")).json()
    schemas = spec["components"]["schemas"]

    for path, method, model_name in _ENDPOINTS:
        op = spec["paths"][path][method]
        responses = op["responses"]
        status = next(k for k in responses if k in ("200", "201"))
        ref = responses[status]["content"]["application/json"]["schema"].get("$ref", "")
        assert ref.endswith(f"/{model_name}"), (
            f"{method.upper()} {path} 的 {status} 未引用 {model_name}（实际 {ref!r}）"
        )
        assert model_name in schemas, f"{model_name} 未进 components.schemas"


def test_openapi_schema_properties_match_hardcoded_expectation(tmp_path: Path):
    """⚠️ ``extra="allow"`` 的必要配套：断言 **OpenAPI schema 的 properties / required**。

    只断言「响应键集 == 硬编码」抓不到「模型少声明一个字段」——因为 ``extra="allow"``
    下响应照样带着那个键（坑区 M20 实测存活的那条）。schema 的 properties 是
    **模型之外**的第三方基准，删掉声明会让它同步变小 ⇒ 这里必红。

    本用例刻意**不引用** ``model_fields``（那是自指，见文件 docstring）。
    """
    # 只读 schema，不需要 lifespan（迁移只在 lifespan 里跑，本用例不进）
    spec = _create_app(tmp_path).openapi()
    schemas = spec["components"]["schemas"]

    expected_properties = {
        "SnapshotResponse": EXPECTED_INIT_KEYS,
        "SubmitDeltaResponse": EXPECTED_DELTA_KEYS,
        "CommitResponse": EXPECTED_COMMIT_KEYS,
        "RollbackResponse": EXPECTED_ROLLBACK_KEYS,
        "BranchCreatedResponse": EXPECTED_BRANCH_KEYS,
        "BranchPromoteResponse": EXPECTED_PROMOTE_KEYS,
        "StateDiffResponse": EXPECTED_DIFF_EMPTY_KEYS | EXPECTED_DIFF_SECTIONS,
    }
    expected_required = {
        # 「键恒在」= 无默认值的键。带 `= None` 默认值的键不进 required——
        # promote 的 `commit_id` / `delta_id` / `branch_id` 虽实测恒在，但为了让
        # 「某形状缺席」的键可缺席而带了默认值，故 schema 里是 optional（保守声明）。
        "SnapshotResponse": {"state_version"},
        "SubmitDeltaResponse": {"delta_id", "status", "errors"},
        "CommitResponse": {"commit_id", "delta_id", "state_version"},
        "RollbackResponse": {"commit_id", "delta_id", "state_version", "rollback_of"},
        "BranchCreatedResponse": EXPECTED_BRANCH_KEYS,
        "BranchPromoteResponse": {"state_version", "promoted_commits", "replayed_delta_ids"},
        "StateDiffResponse": {"version_a", "version_b"},
    }

    for name, keys in expected_properties.items():
        props = set(schemas[name]["properties"])
        assert props == keys, f"{name} 的 schema properties 不符：多={props - keys} 少={keys - props}"
        required = set(schemas[name].get("required", []))
        assert required == expected_required[name], (
            f"{name} 的 schema required 不符：多={required - expected_required[name]} "
            f"少={expected_required[name] - required}"
        )

    # ListDiff 的三个子键同样都是条件键（实测：只 added 的段里没有 changed/removed）
    list_diff = schemas["ListDiffResponse"]
    assert set(list_diff["properties"]) == {"added", "removed", "changed"}
    assert not list_diff.get("required"), list_diff
    assert set(schemas["ChangedDiffItemResponse"]["properties"]) == {"id", "changes"}
    assert set(schemas["ChangedDiffItemResponse"].get("required", [])) == {"id"}


def test_models_stay_in_sync_with_hardcoded_expectation():
    """⚠️ **本文件唯一使用 ``model_fields`` 的用例**（可用 grep 自查）。

    拿模型字段集与硬编码常量比——是「模型 vs 硬编码」，不是「响应 vs 模型」，
    因此删掉任一字段声明时它会红（响应侧因 ``extra="allow"`` 抓不到）。
    """
    assert set(SnapshotResponse.model_fields) == EXPECTED_INIT_KEYS
    assert set(SubmitDeltaResponse.model_fields) == EXPECTED_DELTA_KEYS
    assert set(CommitResponse.model_fields) == EXPECTED_COMMIT_KEYS
    assert set(RollbackResponse.model_fields) == EXPECTED_ROLLBACK_KEYS
    assert set(BranchCreatedResponse.model_fields) == EXPECTED_BRANCH_KEYS
    assert set(BranchPromoteResponse.model_fields) == EXPECTED_PROMOTE_KEYS
    assert set(StateDiffResponse.model_fields) == EXPECTED_DIFF_EMPTY_KEYS | EXPECTED_DIFF_SECTIONS
    assert set(ListDiffResponse.model_fields) == {"added", "removed", "changed"}
    assert set(ChangedDiffItemResponse.model_fields) == {"id", "changes"}


# ---------------------------------------------------------------------------
# 2. 实际返回的键集 vs 硬编码
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_init_response_keys_match_hardcoded_expectation(tmp_path: Path):
    """``state/init`` 返回的就是一份 Canonical State ⇒ 与 ``SnapshotResponse`` 同形（实测 7 键）。"""
    app = _create_app(tmp_path)
    pid = (await _request(app, "POST", "/api/projects", json={"name": "init"})).json()["project_id"]
    cid = (
        await _request(
            app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
        )
    ).json()["chapter_id"]

    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EXPECTED_INIT_KEYS, (
        f"多={set(body) - EXPECTED_INIT_KEYS} 少={EXPECTED_INIT_KEYS - set(body)}"
    )
    assert body["state_version"] == 1
    assert isinstance(body["characters"], list)
    assert isinstance(body["events"], dict)


@pytest.mark.anyio
async def test_delta_and_commit_and_rollback_keys_match_hardcoded_expectation(tmp_path: Path):
    """deltas(3) / commits(4) / rollback(5) 三条 201 响应的键集。"""
    app, pid, char, chap, base = await _seeded(tmp_path)

    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text
    delta_body = r.json()
    assert set(delta_body) == EXPECTED_DELTA_KEYS, (
        f"多={set(delta_body) - EXPECTED_DELTA_KEYS} 少={EXPECTED_DELTA_KEYS - set(delta_body)}"
    )
    # 201 路径上 status 恒为 validated（rejected 走 422）
    assert delta_body["status"] == "validated"
    assert delta_body["errors"] == []

    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={"delta_id": delta["delta_id"], "workflow_run_id": delta["workflow_run_id"]},
    )
    assert r.status_code == 201, r.text
    commit_body = r.json()
    assert set(commit_body) == EXPECTED_COMMIT_KEYS, (
        f"多={set(commit_body) - EXPECTED_COMMIT_KEYS} 少={EXPECTED_COMMIT_KEYS - set(commit_body)}"
    )
    assert isinstance(commit_body["snapshot_ref"], str), "main commit 的 snapshot_ref 必须是字符串"

    r = await _request(
        app,
        "POST",
        f"/api/commits/{commit_body['commit_id']}/rollback",
        json={"author_approval": {"approver": "contract-test", "approved": True}},
    )
    assert r.status_code == 201, r.text
    rb = r.json()
    assert set(rb) == EXPECTED_ROLLBACK_KEYS, (
        f"多={set(rb) - EXPECTED_ROLLBACK_KEYS} 少={EXPECTED_ROLLBACK_KEYS - set(rb)}"
    )
    assert rb["rollback_of"] == commit_body["commit_id"]
    assert rb["commit_id"] != commit_body["commit_id"], "回滚产生的是**新** commit"


@pytest.mark.anyio
async def test_branch_commit_keeps_null_snapshot_ref(tmp_path: Path):
    """⚠️ 分支 commit 的 ``snapshot_ref`` 是 **null**，且**键必须在**。

    声明成 ``str`` ⇒ 校验失败 500；给端点加 ``response_model_exclude_none`` ⇒ 键消失。
    两条都要被本用例抓住。
    """
    app, pid, char, chap, base = await _seeded(tmp_path)
    bid = (await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})).json()[
        "branch_id"
    ]

    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(
        app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid}
    )
    assert r.status_code == 201, r.text

    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            "workflow_run_id": delta["workflow_run_id"],
            "branch_id": bid,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EXPECTED_COMMIT_KEYS
    assert "snapshot_ref" in body, "分支 commit 的 snapshot_ref 键被抹掉了？"
    assert body["snapshot_ref"] is None, "分支 commit 不写 story_states，snapshot_ref 应为 null"


@pytest.mark.anyio
async def test_branch_create_keys_match_hardcoded_expectation(tmp_path: Path):
    app, pid, _char, _chap, base = await _seeded(tmp_path)
    r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EXPECTED_BRANCH_KEYS, (
        f"多={set(body) - EXPECTED_BRANCH_KEYS} 少={EXPECTED_BRANCH_KEYS - set(body)}"
    )
    assert body["base_state_version"] == base
    assert body["status"] == "ACTIVE"
    assert body["parent_branch_id"] != body["branch_id"]


@pytest.mark.anyio
async def test_promote_empty_branch_keeps_null_keys(tmp_path: Path):
    """⚠️ 空分支 promote：**7 键**，`commit_id` / ``delta_id`` **键在、值为 null**。

    这一条同时钉住「不能加 ``response_model_exclude_none``」——加了这两个键就消失。
    """
    app, pid, _char, _chap, base = await _seeded(tmp_path)
    bid = (await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})).json()[
        "branch_id"
    ]

    r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EXPECTED_PROMOTE_EMPTY_KEYS, (
        f"多={set(body) - EXPECTED_PROMOTE_EMPTY_KEYS} "
        f"少={EXPECTED_PROMOTE_EMPTY_KEYS - set(body)}"
    )
    for key in ("commit_id", "delta_id"):
        assert key in body, f"{key} 不该消失（键恒在、值为 null）"
        assert body[key] is None
    # ⚠️ 同名不同义：空分支时 branch_id 就是**被 promote 的源分支**
    assert body["branch_id"] == bid
    # ⚠️ 同名不同义：空分支时 state_version 是 base，**没有产生新版本**
    assert body["state_version"] == base
    assert body["promoted_commits"] == 0
    assert body["replayed_delta_ids"] == []
    assert body["status"] == "MERGED"


@pytest.mark.anyio
async def test_promote_nonempty_branch_keys_match_hardcoded_expectation(tmp_path: Path):
    """非空分支 promote：**8 键**，比空分支多 ``promoted_from`` / ``snapshot_ref``，少 ``status``。"""
    app, pid, char, chap, base = await _seeded(tmp_path)
    bid = (await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})).json()[
        "branch_id"
    ]
    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid})
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            "workflow_run_id": delta["workflow_run_id"],
            "branch_id": bid,
        },
    )
    assert r.status_code == 201, r.text

    r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == EXPECTED_PROMOTE_NONEMPTY_KEYS, (
        f"多={set(body) - EXPECTED_PROMOTE_NONEMPTY_KEYS} "
        f"少={EXPECTED_PROMOTE_NONEMPTY_KEYS - set(body)}"
    )
    assert body["promoted_from"] == bid, "源分支 id 只在 promoted_from 里"
    # ⚠️ 同名不同义：非空时 branch_id 是 **main 分支**，不是源分支
    assert body["branch_id"] != bid, "非空 promote 的 branch_id 应是 main 分支 id"
    assert body["promoted_commits"] == 1
    assert len(body["replayed_delta_ids"]) == 1
    assert "status" not in body, "非空形状没有 status 键"


# ---------------------------------------------------------------------------
# 3. state/diff —— 「空则省略」是承重的
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_diff_keeps_branch_id_and_omits_sections(tmp_path: Path):
    """⚠️ 核心守卫：无差异时 diff **恰好 3 键**（含恒为 null 的 ``branch_id``）。

    撤掉任一 ``exclude_if`` ⇒ 多出 ``"<段>": null`` ⇒ 必红。
    给端点加 ``response_model_exclude_none=True`` ⇒ 少掉 ``branch_id`` ⇒ 必红。
    """
    app, pid, _char, _chap, base = await _seeded(tmp_path)

    r = await _request(app, "GET", f"/api/projects/{pid}/state/diff", params={"a": base, "b": base})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == EXPECTED_DIFF_EMPTY_KEYS, (
        f"diff 的键应恰好 3 个，实际 {sorted(body)}——"
        f"多出来的多半是 exclude_if 被撤掉后序列化成 null 的实体段；"
        f"少掉的多半是被端点级 response_model_exclude_none 抹掉的 branch_id"
    )
    assert "branch_id" in body, "branch_id 恒为 null 但**键必须在**（前端按 nullable 声明读它）"
    assert body["branch_id"] is None
    assert body["version_a"] == body["version_b"] == base
    for section in EXPECTED_DIFF_SECTIONS:
        assert section not in body, f"{section} 无差异时不该出现"
    # 模型侧此刻这六段全为 None（与「序列化时被 exclude_if 抹掉」一致）
    parsed = StateDiffResponse(**body)
    for section in EXPECTED_DIFF_SECTIONS:
        assert getattr(parsed, section) is None


def test_write_endpoints_declare_no_endpoint_level_exclude_none(tmp_path: Path):
    """⭐ 与 ``response_model_exclude_none=True`` 同形的**另一种候选修法**必须被抓住。

    任务书写「``state/diff`` 必须带 ``response_model_exclude_none=True``」——实测那是错的：
    ``branch_id`` 恒为 null，端点级开关会把它一起抹掉（空 diff 3 键 → 2 键）。
    本用例把「写面七个端点一律不许出现端点级 exclude_none」钉死。

    ⚠️ **量具纪律（两处）**：
    1. 断言读的是 ``APIRoute.response_model_exclude_none`` **属性**，不是 OpenAPI JSON 的
       文本——后者的 ``description`` 里就写着这个字符串名（docstring 被 FastAPI 抄进
       description），文本匹配会**恒真地失败**。
    2. 只扫**本批的七个端点**：``/simulate``（批次五） legitimately 带着 exclude_none，
       按路径前缀扫全仓会误伤别的成员。
    """
    app = _create_app(tmp_path)
    mine = {(path, method.upper()) for path, method, _ in _ENDPOINTS}
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if (route.path, method) not in mine:
                continue
            checked += 1
            assert route.response_model is not None, f"{route.path} [{method}] 没有 response_model"
            assert route.response_model_exclude_none is not True, (
                f"{route.path} [{method}] 不许加端点级 response_model_exclude_none——"
                f"本批实测形状里有「键恒在、值为 null」的键（diff 的 branch_id、"
                f"promote 空分支的 commit_id/delta_id），端点级开关会把它们抹掉"
            )
    assert checked == len(_ENDPOINTS), (
        f"应当正好命中 {len(_ENDPOINTS)} 个本批端点，实际 {checked}——"
        f"路径写错或端点没挂上时这里会报数不符而不是静默通过"
    )


@pytest.mark.anyio
async def test_nonempty_diff_sections_and_changed_element_type(tmp_path: Path):
    """非空 diff：实体段按需出现，``changed[]`` 的元素**已建模到 ChangedItem 那一层**。

    不建模的话前端只能拿到 ``{[k: string]: unknown}``，``c.id`` 与 ``changes``
    全是 unknown，渲染处到处写 ``as``。
    """
    app, pid, char, chap, base = await _seeded(tmp_path)
    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={"delta_id": delta["delta_id"], "workflow_run_id": delta["workflow_run_id"]},
    )
    assert r.status_code == 201, r.text

    r = await _request(
        app, "GET", f"/api/projects/{pid}/state/diff", params={"a": base, "b": base + 1}
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body) <= EXPECTED_DIFF_EMPTY_KEYS | EXPECTED_DIFF_SECTIONS, (
        f"多出来的键不是六个实体段：{set(body) - EXPECTED_DIFF_EMPTY_KEYS - EXPECTED_DIFF_SECTIONS}"
    )
    assert {"characters", "hooks", "debts", "events_changed"} <= set(body), (
        f"实测一次 delta 应点亮四段，实际 {sorted(body)}"
    )
    # 未点亮的段在模型里是 None、序列化时被 omit ——不是 null
    for section in EXPECTED_DIFF_SECTIONS - {"characters", "hooks", "debts", "events_changed"}:
        assert section not in body

    # ⚠️ 列表段的**子键**同样「空则省略」：撤掉 ListDiffResponse 任一子键上的 exclude_if
    # ⇒ 这里多出 "<added|removed|changed>": null。断言的是**段内的键集**，
    # 只断言顶层键集抓不到这一层。
    assert set(body["hooks"]) == {"added"}, (
        f"hooks 段实测只有 added，实际 {sorted(body['hooks'])}"
    )
    assert set(body["debts"]) == {"added"}, (
        f"debts 段实测只有 added，实际 {sorted(body['debts'])}"
    )
    assert set(body["characters"]) == {"changed"}, (
        f"characters 段实测只有 changed，实际 {sorted(body['characters'])}"
    )

    parsed = StateDiffResponse(**body)
    assert isinstance(parsed.characters, ListDiffResponse)
    assert parsed.characters.added is None and parsed.characters.removed is None
    changed = parsed.characters.changed
    assert changed and len(changed) == 1
    assert isinstance(changed[0], ChangedDiffItemResponse), (
        f"changed 元素应被建模为 ChangedDiffItemResponse，实际 {type(changed[0])}"
    )
    assert changed[0].id == char
    # _diff_dicts 是任意深度递归：changes 里是嵌套结构，**不是**扁平的 {before, after}
    assert changed[0].changes == {"current_state": {"location": {"before": None, "after": "破屋"}}}
    assert parsed.hooks and parsed.hooks.added
    assert parsed.debts and parsed.debts.added
    assert parsed.events_changed  # 按 event_id 键的 dict，不是 ListDiff


# ---------------------------------------------------------------------------
# 4. extra="allow" —— 未声明键不得被 response_model 裁掉
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_state_init_keeps_undeclared_collection_keys(tmp_path: Path):
    """``extra="allow"`` 的端点级看守（回归看守，不是新引入的）。

    ``response_model`` 默认**过滤**未声明字段。故事状态是开放 JSON 文档，
    某天有人去掉 ``extra="allow"`` ⇒ 新增的集合静默消失，比类型欠定义更难发现。
    走 ``state/init`` 的幂等路径（已有快照时直接返回当前 state）把自定义集合键透出来。
    """
    app = _create_app(tmp_path)
    pid = (await _request(app, "POST", "/api/projects", json={"name": "extra"})).json()["project_id"]
    cid = (
        await _request(
            app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
        )
    ).json()["chapter_id"]
    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
    assert r.status_code == 201, r.text

    conn = sqlite3.connect(tmp_path / "novelos.db")
    try:
        conn.execute(
            "UPDATE story_states SET snapshot_json = ? WHERE project_id = ?",
            (
                '{"state_version": 1, "characters": [], "world": {}, "hooks": [], '
                '"debts": [], "recent_events": [], "events": {}, '
                '"future_collection": [{"x": 1}]}',
                pid,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("future_collection") == [{"x": 1}], (
        "未声明的集合键被 response_model 裁掉了——extra='allow' 失效？"
    )
    # ⚠️ 注意：本用例只断言**那一个**未声明键；整体键集由上面那条硬编码用例钉
    # （键集恒等断言会把 future_collection 算进去，故分开写）


@pytest.mark.anyio
async def test_promote_keeps_undeclared_keys(tmp_path: Path):
    """``BranchPromoteResponse`` 也必须是 ``extra="allow"``。

    端点侧无自然产生的额外键，故直接构造模型实例验（**模型级**看守，不是端点级）：
    """
    parsed = BranchPromoteResponse(
        commit_id=None,
        delta_id=None,
        state_version=3,
        branch_id="br_x",
        promoted_commits=0,
        replayed_delta_ids=[],
        status="MERGED",
        future_field={"a": 1},
    )
    assert parsed.model_dump()["future_field"] == {"a": 1}


@pytest.mark.anyio
async def test_diff_keeps_undeclared_top_level_keys(tmp_path: Path):
    """``StateDiffResponse`` 的 ``extra="allow"``：``diff_snapshots`` 未来加段时不至于被裁掉。

    同样走模型级实例（本端点当前不会产出顶层额外键）。
    """
    parsed = StateDiffResponse(version_a=1, version_b=2, branch_id=None, future_section={"x": 1})
    dumped = parsed.model_dump()
    assert dumped["future_section"] == {"x": 1}
    # exclude_if 仍然生效：没给的段不进 dump
    assert "characters" not in dumped
