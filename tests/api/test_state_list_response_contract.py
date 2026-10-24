"""三个状态列表端点 + run 列表端点的 ``response_model`` 契约测试（批次（六）· L2 组）。

为什么单独开一个文件
------------------
上一批补的是**单体端点**，而 ``list[dict]`` 在 OpenAPI 里 items 是**匿名 object**
⇒ 前端拿到的仍是 ``Record<string, unknown>``。本组四个端点的判别口径因此固定为：

    **``$ref``（items 有具名 $ref）且 ``array`` 且 ``items`` 有 ``$ref`` 才算已建模。**

``test_openapi_list_items_have_named_ref`` 就是这条口径的看守。

实测键集（临时库 + 合成夹具跑通端点后 dump，**不是读源码推的**）
=============================================================

| 端点 | 实测行键数 | 行模型 |
|---|---|---|
| ``GET /projects/{pid}/commits`` | **13** | ``CommitListItem`` |
| ``GET /projects/{pid}/chapters/{cid}/deltas`` | **18** | ``StateDeltaListItem`` |
| ``GET /projects/{pid}/branches`` | **7** | ``BranchSummary`` |
| ``GET /projects/{pid}/runs`` | **13** | ``WorkflowRunSummary`` |

两处**先验被实测推翻**（都写进了模型 docstring）
------------------------------------------------

1. **``commits`` 列表行不是 ``POST /commits`` 的形状。** 后者是 4 键动作摘要
   （``commit_id`` / ``delta_id`` / ``state_version`` / ``snapshot_ref``）；前者是
   ``SELECT * FROM commits`` 的**裸 DB 行**（13 键），``state_version`` 在那里
   拆成 ``previous_state_version`` / ``resulting_state_version``。⇒ 必须新建模型。
2. **``branches`` 列表行与 ``POST /branches`` 的 ``BranchCreatedResponse``
   键集逐字相同，却不可复用。** 列表恒含 ``main``，而 main 的
   ``parent_branch_id`` 是 **null**；``BranchCreatedResponse`` 声明的是必填非空
   ``str`` ⇒ 复用会让这条端点**恒 500**。判别：
   ``test_main_branch_row_cannot_be_validated_by_branch_created_response``。

    另有一处**与任务书预期不符**的实测：``deltas`` 行里**没有** ``payload_json`` 键
    ——``restore_delta_from_row`` 把 7 个 change 数组摊到了顶层（10 + 7 + 1 = 18）。

禁自指断言（本轮最大的坑）
------------------------
**本文件里 ``model_fields`` 只出现在「模型 vs 硬编码」这一条用例中**
（该访问已收进 ``_contract_runner.assert_model_fields_match`` ⇒ **代码里 0 命中**，
只剩 docstring 的说明文字；自查：``grep -n "model_fields" <本文件>`` ⇒ 命中的全是
注释/docstring）。其余用例一律拿**硬编码常量**当模型之外的第三方基准：

* ``test_*_row_keys_match_hardcoded_expectation``：响应行键集 vs 硬编码；
* ``test_openapi_schema_properties_match_hardcoded_expectation``：OpenAPI 里
  ``components.schemas`` 的 ``properties`` / ``required`` vs 硬编码——四个模型全开了
  ``extra="allow"``，删掉字段声明时**响应里照样有那个键**，光断言响应抓不到。

⚠️ **空数组量不到任何东西**：四个端点在没有数据时返回 ``[]``，此时任何「键集」断言
都是**空断言**（恒真）。故每条键集用例都先断言**行数下界**与**关键行的存在**
（如 rollback 产出的 ``rollback_of`` 非空行、``main`` 分支、可空 ``label`` 的 run 行），
夹具退化成空表时必红而不是静默通过。

判别用例：``tests/mutation_specs/l2_state_list.py``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from packages.core.api.routers import story_state as story_state_router
from packages.core.api.routers.workflows.models import WorkflowRunSummary
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import now_iso
from packages.core.story_state.models import (
    BranchCreatedResponse,
    BranchSummary,
    CommitListItem,
    StateDeltaListItem,
)

from ._contract_runner import (
    assert_keys_match,
    assert_list_items_named_ref,
    assert_model_fields_match,
    assert_openapi_properties_and_required,
)
from ._rich_fixture import build_state_delta
from ._shared import _create_app, _request

# ---------------------------------------------------------------------------
# 硬编码期望键集：**模型之外的第三方基准**，不得写成 set(Model.model_fields)
# （依据：DB 实际列 + 本文件用临时库实测 dump 出的响应，二者独立于模型）
# ---------------------------------------------------------------------------

#: ``GET /projects/{pid}/commits`` 的行。依据：``commits`` 表 **13** 列（0001 建表起即
#: 12 列，含 ``rollback_of``；0022/0030 只动它的索引不添列；**0034 加了
#: ``draft_version``** ⇒ 13 列），其中两个 JSON 列经 ``snapshots._parse_json`` 解析成 dict。
EXPECTED_COMMIT_ROW_KEYS = {
    "commit_id",
    "project_id",
    "branch_id",
    "chapter_id",
    "previous_state_version",
    "resulting_state_version",
    "delta_id",
    "validation_json",
    "author_approval_json",
    "timestamp",
    "workflow_run_id",
    "rollback_of",
    "draft_version",
}
#: ``POST /projects/{pid}/commits`` 的响应——**故意并列在此**，用来钉住「两者不同形」。
EXPECTED_COMMIT_RESPONSE_KEYS = {"commit_id", "delta_id", "state_version", "snapshot_ref"}

#: ``GET .../chapters/{cid}/deltas`` 的行。依据：``restore_delta_from_row`` 的
#: 10 元信息字段 + 7 个 change 数组（顶层摊平，**没有** ``payload_json``）+ ``status``。
EXPECTED_DELTA_ROW_KEYS = {
    "delta_id",
    "chapter_id",
    "workflow_run_id",
    "previous_state_version",
    "delta_version",
    "schema_version",
    "created_by",
    "created_at",
    "supersedes",
    "notes",
    "status",
    "character_changes",
    "world_changes",
    "relationship_changes",
    "new_events",
    "resolved_hooks",
    "new_hooks",
    "debt_changes",
}
#: 7 个 change 数组（刻意不逐字段建模，见 ``StateDeltaListItem`` 的 docstring）
EXPECTED_DELTA_ARRAY_KEYS = {
    "character_changes",
    "world_changes",
    "relationship_changes",
    "new_events",
    "resolved_hooks",
    "new_hooks",
    "debt_changes",
}

#: ``GET /projects/{pid}/branches`` 的行。依据：``branches`` 表 7 列。
EXPECTED_BRANCH_ROW_KEYS = {
    "branch_id",
    "project_id",
    "name",
    "parent_branch_id",
    "base_state_version",
    "status",
    "created_at",
}

#: ``GET /projects/{pid}/runs`` 的行。依据：``workflow_runs`` 12 列（0001 建表 10 列 +
#: 0026 加 ``instance_id`` + 0037 加 ``project_id``）+ 端点追加的 ``workflow_name`` 与 ``label``。
EXPECTED_RUN_ROW_KEYS = {
    "run_id",
    "workflow_id",
    "workflow_name",
    "chapter_id",
    "project_id",
    "status",
    "current_node",
    "checkpoint_json",
    "error",
    "retry_count",
    "started_at",
    "ended_at",
    "instance_id",
    "label",
}

#: 模型名 → OpenAPI path。``response_model`` 只作用在数组的 **items** 上。
LIST_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("/api/projects/{project_id}/commits", "get", "CommitListItem"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/deltas", "get", "StateDeltaListItem"),
    ("/api/projects/{project_id}/branches", "get", "BranchSummary"),
    ("/api/projects/{project_id}/runs", "get", "WorkflowRunSummary"),
)

#: 模型 → 全部已声明字段（= schema 的 ``properties``）
EXPECTED_PROPERTIES: dict[str, set[str]] = {
    "CommitListItem": EXPECTED_COMMIT_ROW_KEYS,
    "StateDeltaListItem": EXPECTED_DELTA_ROW_KEYS,
    "BranchSummary": EXPECTED_BRANCH_ROW_KEYS,
    "WorkflowRunSummary": EXPECTED_RUN_ROW_KEYS,
}
#: 模型 → ``required``。四个模型的字段**全部不给默认值** ⇒ 全部必填；
#: ``Optional[X]`` 仍然必填（**键恒在**、值可为 null），这与「可省略」是两回事。
EXPECTED_REQUIRED: dict[str, set[str]] = dict(EXPECTED_PROPERTIES)


# ---------------------------------------------------------------------------
# 夹具（自包含；零真实 LLM——见文件末尾的 provider 不可达不变量）
# ---------------------------------------------------------------------------




# 客户端与请求助手已收进 ``tests/api/_shared.py``（task-6 阶段一已存在的共用 helper：
# ``_make_client`` = 每请求一个 ASGI 客户端、**不进 lifespan**；``_request`` = 同形的
# 一次性请求）。本文件的所有 run 行都是**直接 INSERT** 的终态行 ⇒ 不进 lifespan
# （lifespan 的 startup 会跑 ``recover_interrupted_runs``，对终态行是多余干扰）。




def _delta(chapter_id: str, character_id: str, prev_version: int, location: str) -> dict:
    """一条能过 ``validate_delta`` 的完整 state-delta（7 数组齐备）。

    构造器已收进 ``tests/api/_rich_fixture.py``（与写面契约文件**逐字同形** ⇒ 共用
    一处）；本文件只保留自己的 ``created_by`` 字面量 ⇒ **产出逐字不变**（含键序：
    这份 dict 是 ``POST .../deltas`` 的请求体）。
    """
    return build_state_delta(
        chapter_id, character_id, prev_version, location, created_by="l2-contract-test"
    )


def _seed_runs(db_path: str, chapter_id: str) -> None:
    """直接 INSERT 四行 ``workflow_runs``，覆盖三种「键恒在、值为 null」的形态。

    **刻意不跑真 workflow**：这是本组唯一需要 ``workflow_runs`` 有行的端点，而 run 的
    产生方式不影响行键集（行 = ``SELECT wr.*`` + 两个派生键）。零 LLM、零耗时、
    且状态完全确定（`_compute_run_label` 会去查 drafts 表，行为可预测）。
    """
    conn = get_connection(db_path)
    try:
        ts = now_iso()
        conn.executemany(
            "INSERT INTO workflows (workflow_id, name, version, definition_json,"
            " created_at, updated_at) VALUES (?, ?, 'v1', '{}', ?, ?)",
            [
                ("wf_l2_plan", "chapter-plan", ts, ts),
                ("wf_l2_write", "chapter-write", ts, ts),
                ("wf_l2_init", "project-init", ts, ts),
                # ⚠️ 这个名字**不在** ``runs._WORKFLOW_ZH_LABELS`` 里 ⇒ label 恒为 null，
                # 是「键恒在、值为 null」的可达实例（也是 label 必须声明成 Optional 的实证）。
                ("wf_l2_other", "deconstruct-book", ts, ts),
            ],
        )
        rows = [
            # chapter-write 形态：label 有值、ended_at 有值
            ("wfr_l2_write", "wf_l2_write", chapter_id, "COMPLETED", None, "{}", None, 0,
             "2026-10-15T00:00:05Z", "2026-10-15T00:00:09Z"),
            # 中文映射表外的 workflow：workflow_name 有值、**label 为 null**、error 有值
            ("wfr_l2_other", "wf_l2_other", chapter_id, "FAILED", None, "{}", "boom", 2,
             "2026-10-15T00:00:04Z", "2026-10-15T00:00:06Z"),
            # project-init 形态：**chapter_id 为 null**、current_node 有值、ended_at 为 null
            ("wfr_l2_init", "wf_l2_init", None, "PAUSED", "persist", '{"stage": "premise"}',
             None, 0, "2026-10-15T00:00:03Z", None),
            # 刚起跑形态：几乎全 null，但键一个不少
            ("wfr_l2_plan", "wf_l2_plan", chapter_id, "RUNNING", "director", "{}", None, 0,
             "2026-10-15T00:00:00Z", None),
        ]
        conn.executemany(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status,"
            " current_node, checkpoint_json, error, retry_count, started_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


async def _harvest(tmp_path: Path) -> dict:
    """跑通四个列表端点，把响应体收上来。返回 ``{"<tag>": [...]}``。"""
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path, log_level="WARNING").db_path)
    out: dict = {}

    pid = (await _request(app, "POST", "/api/projects", json={"name": "L2 列表契约"})).json()[
        "project_id"
    ]
    char_id = (
        await _request(app, "POST", f"/api/projects/{pid}/characters", json={"name": "林昭"})
    ).json()["character_id"]
    chapter_id = (
        await _request(
            app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
        )
    ).json()["chapter_id"]
    out["pid"] = pid
    out["chapter_id"] = chapter_id

    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chapter_id})
    assert r.status_code == 201, r.text
    base = r.json()["state_version"]

    # 一个子分支（main 由 genesis 自动建 ⇒ 列表恒含 main）
    r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})
    assert r.status_code == 201, r.text
    out["branch_id"] = r.json()["branch_id"]
    out["branch_created"] = r.json()

    # main commit
    # ⚠️ 2026-10-24（R-1）：**建分支本身会推进 main 的 state_version**
    # （``branches`` 行进 director 装配 payload ⇒ 写路径必须推版本）
    # ⇒ 乐观锁基线必须**动态取当前值**，不许沿用 genesis 的 base
    # （沿用会被乐观锁挡成 409 optimistic_lock）。
    main_version = (
        await _request(app, "GET", f"/api/projects/{pid}/state")
    ).json()["state_version"]
    d1 = _delta(chapter_id, char_id, main_version, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d1)
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={"delta_id": d1["delta_id"], "workflow_run_id": d1["workflow_run_id"]},
    )
    assert r.status_code == 201, r.text
    out["commit_response"] = r.json()

    # 分支 commit + 回滚（回滚是唯一让 ``rollback_of`` 非空的路径）
    # ⚠️ 这里 ``base`` 仍然正确：分支有**自己独立的版本序列**，
    # 其起点是建分支那一刻的 ``branches.base_state_version``（= 1 = genesis），
    # 不随 main 的版本推进而变（R-1 只推 main，不推分支序列）。
    d2 = _delta(chapter_id, char_id, base, "城门")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**d2, "branch_id": out["branch_id"]})
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={
            "delta_id": d2["delta_id"],
            "workflow_run_id": d2["workflow_run_id"],
            "branch_id": out["branch_id"],
        },
    )
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/commits/{r.json()['commit_id']}/rollback",
        json={"author_approval": {"approver": "l2-contract-test", "approved": True}},
    )
    assert r.status_code == 201, r.text

    _seed_runs(db_path, chapter_id)

    r = await _request(app, "GET", f"/api/projects/{pid}/commits")
    assert r.status_code == 200, r.text
    out["commits"] = r.json()

    r = await _request(app, "GET", f"/api/projects/{pid}/chapters/{chapter_id}/deltas")
    assert r.status_code == 200, r.text
    out["deltas"] = r.json()

    r = await _request(app, "GET", f"/api/projects/{pid}/branches")
    assert r.status_code == 200, r.text
    out["branches"] = r.json()

    r = await _request(app, "GET", f"/api/projects/{pid}/runs")
    assert r.status_code == 200, r.text
    out["runs"] = r.json()

    return out


@pytest.fixture(scope="module")
def harvested(tmp_path_factory) -> dict:
    return asyncio.run(_harvest(tmp_path_factory.mktemp("l2_list_contract")))


# ---------------------------------------------------------------------------
# 1) OpenAPI：四个端点的 items 都有**具名 $ref**（本组的判别口径）
# ---------------------------------------------------------------------------


def test_openapi_list_items_have_named_ref(tmp_path: Path):
    """⭐ 本组的核心判别口径：``list[dict]`` 的 items 是匿名 object，前端拿到的就是
    ``Record<string, unknown>``。**只有 ``array`` + ``items.$ref`` 才算已建模。**

    撤掉任一 ``response_model=list[...]`` ⇒ ``$ref`` 消失 ⇒ 必红。
    """
    openapi = _create_app(tmp_path).openapi()
    for path, method, model_name in LIST_ENDPOINTS:
        assert_list_items_named_ref(openapi, path, method, model_name)


def test_openapi_schema_properties_match_hardcoded_expectation(tmp_path: Path):
    """⚠️ ``extra="allow"`` 的必要配套：断言 **OpenAPI schema 的 properties / required**。

    只断言「响应键集 == 硬编码」抓不到「模型少声明一个字段」——因为 ``extra="allow"``
    下响应照样带着那个键（坑区 M20 实测存活的那一格）。schema 的 properties 是
    **模型之外**的第三方基准，删掉声明会让它同步变小 ⇒ 这里必红。

    本用例刻意**不引用** ``model_fields``（那是自指，见文件 docstring）。
    """
    schemas = _create_app(tmp_path).openapi()["components"]["schemas"]
    for name, keys in EXPECTED_PROPERTIES.items():
        assert name in schemas, f"{name} 未进 components.schemas"
        assert_openapi_properties_and_required(
            schemas[name], keys, EXPECTED_REQUIRED[name], label=name
        )


# ---------------------------------------------------------------------------
# 2) 实际返回的行键集 vs 硬编码
# ---------------------------------------------------------------------------


def test_commits_row_keys_match_hardcoded_expectation(harvested: dict):
    """commits 列表行恒 **13 键**，且**与 ``POST /commits`` 的 4 键响应不同形**。

    ⚠️ 后半句是本组的第一条实测结论：列表行是裸 DB 行，``state_version`` 在那里拆成
    ``previous_state_version`` / ``resulting_state_version``。若谁把它「简化」成复用
    ``CommitResponse``，本用例与 ``test_openapi_list_items_have_named_ref`` 双红。
    """
    rows = harvested["commits"]
    # ⚠️ 先断言行数下界 + 关键行存在，否则空数组时键集断言是**空断言**
    assert len(rows) >= 4, f"夹具没造出足够的 commit 行：{len(rows)}"
    assert any(r["rollback_of"] for r in rows), (
        "夹具没造出 rollback 行 ⇒ rollback_of 恒 null 的那条分支没被验到"
    )
    assert any(r["rollback_of"] is None for r in rows), "普通 commit 行缺失"
    for row in rows:
        assert_keys_match(row, EXPECTED_COMMIT_ROW_KEYS, "commit 行")
        assert isinstance(row["validation_json"], dict), "JSON 列应被解析成 dict"
        assert isinstance(row["author_approval_json"], dict)
        assert row["previous_state_version"] < row["resulting_state_version"]
        assert "payload_json" not in row and "state_version" not in row

    # 并列断言：单体端点的形状**确实不同**（防止后人「统一」成同一个模型）
    assert_keys_match(
        harvested["commit_response"], EXPECTED_COMMIT_RESPONSE_KEYS, "POST /commits 响应"
    )
    assert EXPECTED_COMMIT_RESPONSE_KEYS != EXPECTED_COMMIT_ROW_KEYS


def test_deltas_row_keys_match_hardcoded_expectation(harvested: dict):
    """deltas 列表行恒 **18 键**，且**没有** ``payload_json``（7 数组被摊到顶层）。"""
    rows = harvested["deltas"]
    assert len(rows) >= 3, f"夹具没造出足够的 delta 行：{len(rows)}（genesis + 2 delta + 逆 delta）"
    for row in rows:
        assert_keys_match(row, EXPECTED_DELTA_ROW_KEYS, "delta 行")
        assert "payload_json" not in row, (
            "行里不该有 payload_json——restore_delta_from_row 已把 7 数组摊平"
        )
        # 7 个 change 数组的**类型**是 list（实测）；逐字段内容不建模（delta schema 会演进）
        for key in EXPECTED_DELTA_ARRAY_KEYS:
            assert isinstance(row[key], list), f"{key} 实测是 list，实际 {type(row[key])}"
        assert row["notes"] is None, "restore_delta_from_row 把 notes 写死为 None"
        assert isinstance(row["status"], str)
    # 非空数组：夹具真的带了 character / new_hook / debt 三类 change
    # ⚠️ 逐行取并集而不是只看 rows[0]：genesis delta 的 7 数组**全是空的**，
    # 只看首行会把「数组类型对不对」这条断言落在一个恒空样本上。
    seen_nonempty = {k for row in rows for k in EXPECTED_DELTA_ARRAY_KEYS if row[k]}
    assert {"character_changes", "new_hooks", "debt_changes"} <= seen_nonempty, (
        f"夹具没造出这三种 change：{sorted(seen_nonempty)}"
    )
    assert any(row["character_changes"] for row in rows), "没有一行带 character change"
    # 数组元素是对象（delta schema 的 items.$ref），不是标量/字符串
    for row in rows:
        for key in EXPECTED_DELTA_ARRAY_KEYS:
            for item in row[key]:
                assert isinstance(item, dict), f"{key} 的元素应是 dict，实际 {type(item)}"


def test_branches_row_keys_match_hardcoded_expectation(harvested: dict):
    """branches 列表行恒 **7 键**；⚠️ ``main`` 的 ``parent_branch_id`` **是 null**。"""
    rows = harvested["branches"]
    assert len(rows) >= 2, f"夹具没造出分支行：{len(rows)}"
    by_name = {r["name"]: r for r in rows}
    assert "main" in by_name, f"列表恒含 main，实际 {sorted(by_name)}"
    assert "alt" in by_name, f"夹具的子分支没建出来：{sorted(by_name)}"
    for row in rows:
        assert_keys_match(row, EXPECTED_BRANCH_ROW_KEYS, "branch 行")
    # main 先出（ORDER BY (name='main') DESC）
    assert rows[0]["name"] == "main", "实测 main 排第一（供 UI 默认选中）"
    assert by_name["main"]["parent_branch_id"] is None, (
        "main 是根节点，parent_branch_id 恒为 null —— 这正是不可复用 BranchCreatedResponse 的原因"
    )
    assert by_name["alt"]["parent_branch_id"] == by_name["main"]["branch_id"]


def test_runs_row_keys_match_hardcoded_expectation(harvested: dict):
    """runs 列表行恒 **13 键**；⚠️ ``label`` / ``chapter_id`` / ``ended_at`` 都是
    「键恒在、值为 null」——端点上**不许**加任何 ``response_model_exclude_*``。
    """
    rows = harvested["runs"]
    assert len(rows) >= 4, f"夹具没造出足够的 run 行：{len(rows)}"
    for row in rows:
        assert_keys_match(row, EXPECTED_RUN_ROW_KEYS, "run 行")
        assert isinstance(row["checkpoint_json"], dict)
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["wfr_l2_init"]["chapter_id"] is None, "project-init 的 chapter_id 恒为 null"
    assert by_id["wfr_l2_init"]["ended_at"] is None
    assert by_id["wfr_l2_plan"]["ended_at"] is None
    assert by_id["wfr_l2_plan"]["error"] is None, "成功 run 的 error 键在、值为 null"
    # ⚠️ 中文映射表外的 workflow：workflow_name 有值、label 恒 null
    other = by_id["wfr_l2_other"]
    assert other["workflow_name"] == "deconstruct-book"
    assert other["label"] is None, "label 键恒在；映射表外的 workflow 取值为 null"
    # 有 label 的那几行
    assert by_id["wfr_l2_write"]["label"].startswith("写正文")
    assert by_id["wfr_l2_init"]["label"] == "AI 初始化"


# ---------------------------------------------------------------------------
# 3) 「复用既有模型」这条修法必须被抓住
# ---------------------------------------------------------------------------


def test_main_branch_row_cannot_be_validated_by_branch_created_response(harvested: dict):
    """⚠️ **另一种候选修法**：「branches 列表键集与 ``BranchCreatedResponse`` 逐字相同，
    直接复用不就省一个模型？」——实测那是错的。

    ``POST /branches`` 只会新建**子**分支，所以 ``BranchCreatedResponse.parent_branch_id``
    声明成必填非空 ``str`` 是对的；但列表**恒含 main**，main 的 ``parent_branch_id`` 是
    null ⇒ 用它当 ``response_model`` 会让这条端点 **500**。

    本用例把该行**真的**喂给那个模型：复用即必红。
    """
    main_row = next(r for r in harvested["branches"] if r["name"] == "main")
    assert main_row["parent_branch_id"] is None
    with pytest.raises(Exception) as exc:
        BranchCreatedResponse(**main_row)
    assert "parent_branch_id" in str(exc.value), (
        f"期望因 parent_branch_id 校验失败，实际异常：{exc.value}"
    )
    # 顺带确认：子分支那行是能通过的 —— 说明差异确实只来自 main 的 null
    alt_row = next(r for r in harvested["branches"] if r["name"] == "alt")
    assert BranchCreatedResponse(**alt_row).parent_branch_id == alt_row["parent_branch_id"]


def test_list_endpoints_declare_no_exclude_switches(tmp_path: Path):
    """四个列表端点**一律不许**出现 ``response_model_exclude_none`` / ``exclude_unset``。

    本组**没有条件键**：``rollback_of`` / ``parent_branch_id`` / ``label`` /
    ``chapter_id`` / ``ended_at`` / ``error`` 全是「键恒在、值为 null」，任一开关都会
    把它们**整个抹掉**（键从有变无），那不是「更简洁的响应」而是**契约被改小**。

    ⚠️ **量具纪律**：断言读 ``APIRoute`` 的**属性**，不是 OpenAPI JSON 的文本——
    后者的 ``description`` 里就写着这些字符串名（docstring 被 FastAPI 抄进去），
    文本匹配会得到恒假的绿/红。
    """
    app = _create_app(tmp_path)
    mine = {(path, method.upper()) for path, method, _ in LIST_ENDPOINTS}
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
                f"{route.path} [{method}] 不许加 response_model_exclude_none——"
                f"本组没有条件键，开关会把「键恒在、值为 null」的键整个抹掉"
            )
            assert route.response_model_exclude_unset is not True, (
                f"{route.path} [{method}] 不许加 response_model_exclude_unset——"
                f"同理由；两个开关在这四个端点上都是净损失"
            )
    assert checked == len(LIST_ENDPOINTS), (
        f"应当正好命中 {len(LIST_ENDPOINTS)} 个本组端点，实际 {checked}——"
        f"路径写错或端点没挂上时这里会报数不符而不是静默通过"
    )


# ---------------------------------------------------------------------------
# 4) 模型字段集 vs 同一份硬编码（第二道保险，非自指）
# ---------------------------------------------------------------------------


def test_models_stay_in_sync_with_hardcoded_expectation():
    """⚠️ **本文件唯一使用 ``model_fields`` 的用例**（可用 grep 自查）。

    比的是「模型 vs 硬编码」，不是「模型 vs 响应」——后者因 ``extra="allow"`` 恒等。
    """
    assert_model_fields_match(CommitListItem, EXPECTED_COMMIT_ROW_KEYS)
    assert_model_fields_match(StateDeltaListItem, EXPECTED_DELTA_ROW_KEYS)
    assert_model_fields_match(BranchSummary, EXPECTED_BRANCH_ROW_KEYS)
    assert_model_fields_match(WorkflowRunSummary, EXPECTED_RUN_ROW_KEYS)


def test_extra_allow_keeps_undeclared_row_keys():
    """四个模型都必须是 ``extra="allow"``：行来自 ``SELECT *``，迁移加列就多一键。

    为什么必须**对模型断言**而不靠端点：本组端点当前**不产出任何未声明的键**
    （实测键集与声明逐字相等），所以「关掉 extra」在端点响应里**看不出任何变化**——
    靠响应验它必然是空断言。合成一个未知键是最小的可判别手段。
    """
    dumped = CommitListItem(
        commit_id="c", project_id="p", branch_id="b", chapter_id="ch",
        previous_state_version=0, resulting_state_version=1, delta_id="d",
        validation_json={}, author_approval_json={}, timestamp="t", workflow_run_id="r",
        rollback_of=None, draft_version=None,
        brand_new_column_from_migration_0033={"x": 1},
    ).model_dump()
    assert dumped["brand_new_column_from_migration_0033"] == {"x": 1}, "extra 被关掉了"

    dumped_run = WorkflowRunSummary(
        run_id="r", workflow_id="w", workflow_name=None, chapter_id=None,
        # 0037 加列后为必填可空（键恒在、值可 null），构造点显式给 None。
        project_id=None,
        status="RUNNING",
        current_node=None, checkpoint_json={}, error=None, retry_count=0,
        started_at="t", ended_at=None, instance_id=None, label=None,
        future_key="v",
    ).model_dump()
    assert dumped_run["future_key"] == "v", "extra 被关掉了"

    dumped_delta = StateDeltaListItem(
        delta_id="d", chapter_id="ch", workflow_run_id="r", previous_state_version=0,
        delta_version=1, schema_version="state-delta-v0", created_by="b",
        created_at="t", supersedes=None, notes=None, status="validated",
        character_changes=[], world_changes=[], relationship_changes=[],
        new_events=[], resolved_hooks=[], new_hooks=[], debt_changes=[],
        future_array_key=[],
    ).model_dump()
    assert dumped_delta["future_array_key"] == [], "extra 被关掉了"

    dumped_branch = BranchSummary(
        branch_id="b", project_id="p", name="alt", parent_branch_id="m",
        base_state_version=1, status="ACTIVE", created_at="t", future_col=7,
    ).model_dump()
    assert dumped_branch["future_col"] == 7, "extra 被关掉了"


# ---------------------------------------------------------------------------
# 6) chapter 归属校验（2026-10-16）：路径里的 project_id 与 chapter_id 是**两个独立变量**
# ---------------------------------------------------------------------------
#
# 缺陷形状：``GET .../chapters/{chapter_id}/deltas`` 只做 ``_ensure_project(project_id)``，
# 而 ``queries.list_deltas`` 是裸 ``SELECT * FROM state_deltas WHERE chapter_id = ?``
# —— 服务端**完全不看 project**。修前实测：用**别的** project_id 请求同一章，
# **200 且原样返回同样的 2 行**。
#
# ⚠️ **本组最容易写成空断言的地方**：如果两个项目碰巧没共享数据，「返回 404」可能是恒真的
# （章不在这个项目下 ⇒ 查无行 ⇒ 本来也返回 200+[]，但那是 200 不是 404，所以还好）——
# 真正的风险是**反向的**：夹具若让 A 的章**没有行**，那么「撤掉校验」与「校验生效」
# 的差别只剩状态码，判别力弱。所以 ``_harvest_ownership`` 刻意让 **A 的章真的有 2 行**
# （genesis delta + 1 条 submitted delta），并用 ``assert`` 把这个下界钉在夹具里。
#
# 正反一对（缺一不可）：正向 = 他项目的章 ⇒ 404；反向 = 本项目的章 ⇒ 200 且**真有行**。


async def _harvest_ownership(tmp_path: Path) -> dict:
    """两个**互不相干**的项目，各自的章、分支、delta。零真实 LLM（无 model_configs）。"""
    app = _create_app(tmp_path)
    db_path = str(Settings(data_dir=tmp_path, log_level="WARNING").db_path)

    out: dict = {"app": app, "db_path": db_path}

    # ---------------- 项目 A：完整链路，章上**真的有 delta 行**
    pid_a = (await _request(app, "POST", "/api/projects", json={"name": "归属 A"})).json()["project_id"]
    char_a = (
        await _request(app, "POST", f"/api/projects/{pid_a}/characters", json={"name": "林昭"})
    ).json()["character_id"]
    chap_a = (
        await _request(app, "POST", f"/api/projects/{pid_a}/chapters",
                       json={"number": 1, "title": "A1"})
    ).json()["chapter_id"]
    out.update(pid_a=pid_a, char_a=char_a, chap_a=chap_a)

    r = await _request(app, "POST", f"/api/projects/{pid_a}/state/init", json={"chapter_id": chap_a})
    assert r.status_code == 201, r.text
    base = r.json()["state_version"]

    # ⚠️ 顺序要紧：分支的 ``base_state_version`` 取自建分支那一刻的 main，
    # 所以**先建分支再在 main 上 commit**（否则分支基线是 v2，提交时
    # ``previous_state_version`` 仍给 v1 会被乐观锁挡成 409）。同 ``_harvest`` 的次序。
    # ⚠️ 2026-10-24（R-1）：**建分支本身也推进 main 的 state_version**
    # （每个分支一次）⇒ 下面 main 上那笔 commit 的乐观锁基线必须
    # **动态取当前值**，不许沿用 genesis 的 ``base``。
    branch_ids = {}
    for name in ("alt", "alt2"):
        r = await _request(app, "POST", f"/api/projects/{pid_a}/branches", json={"name": name})
        assert r.status_code == 201, r.text
        branch_ids[name] = r.json()["branch_id"]
    out["branch_ids"] = branch_ids

    main_version = (
        await _request(app, "GET", f"/api/projects/{pid_a}/state")
    ).json()["state_version"]
    d1 = _delta(chap_a, char_a, main_version, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid_a}/deltas", json=d1)
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid_a}/commits",
        json={"delta_id": d1["delta_id"], "workflow_run_id": d1["workflow_run_id"]},
    )
    assert r.status_code == 201, r.text

    # 分支 alt 上挂 1 个 commit（供 promote 的正向「确实写东西」与反向两用）
    # ⚠️ ``base`` 仍正确：分支版本序列独立，起点 = 建分支时的 base_state_version
    # （= genesis 的 1），不随 main 版本推进而变。
    d2 = _delta(chap_a, char_a, base, "城门")
    r = await _request(app, "POST", f"/api/projects/{pid_a}/deltas",
                       json={**d2, "branch_id": branch_ids["alt"]})
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid_a}/commits",
        json={"delta_id": d2["delta_id"], "workflow_run_id": d2["workflow_run_id"],
              "branch_id": branch_ids["alt"]},
    )
    assert r.status_code == 201, r.text

    # ---------------- 项目 B：另一个项目、另一个章（**不建 genesis**，B 的章无行）
    pid_b = (await _request(app, "POST", "/api/projects", json={"name": "归属 B"})).json()["project_id"]
    chap_b = (
        await _request(app, "POST", f"/api/projects/{pid_b}/chapters",
                       json={"number": 1, "title": "B1"})
    ).json()["chapter_id"]
    out.update(pid_b=pid_b, chap_b=chap_b)

    # ⚠️ **夹具下界断言**：A 的章上确有行。没有它，「跨项目 404」这条断言可能落在
    # 一个空表上（撤掉校验时返回 200+[]，差别只剩状态码，判别力大幅下降）。
    r = await _request(app, "GET", f"/api/projects/{pid_a}/chapters/{chap_a}/deltas")
    assert r.status_code == 200, r.text
    assert len(r.json()) >= 2, f"A 的章上应当有 genesis + submitted 两行，实际 {len(r.json())}"
    out["chap_a_row_count"] = len(r.json())

    assert pid_a != pid_b and chap_a != chap_b, "两个项目/章必须是不同的实体"
    return out


@pytest.fixture(scope="module")
def ownership(tmp_path_factory) -> dict:
    return asyncio.run(_harvest_ownership(tmp_path_factory.mktemp("l2_ownership")))


def _delta_count(db_path: str) -> int:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM state_deltas").fetchone()["c"]
    finally:
        conn.close()


def _no_delta_leaked(resp: httpx.Response) -> None:
    """404 响应里**不得**出现任何一行 delta 的数据。

    ⚠️ 不写这条，「返回 404」只钉住了状态码；一个「先查归属、但把行数据塞进
    error detail」的改法照样能过。这里的判别点是**响应体里没有行数据**。
    """
    body = resp.text
    for marker in ("delta_id", "character_changes", "new_hooks", "debt_changes", "state-delta-v0"):
        assert marker not in body, f"404 响应泄漏了行数据（出现 {marker!r}）：{body[:300]}"


# --------------------------------------------------------- list_deltas（目标端点）


def test_list_deltas_rejects_chapter_from_another_project(ownership: dict):
    """⭐ 正向（缺陷回归）：**用 B 的 project_id 请求 A 的章** ⇒ 404，且不泄漏任何行。

    修前实测：``200`` + 与 ``A + A 的章`` **逐字相同**的 2 行。
    撤掉 ``_ensure_chapter_in_project`` ⇒ 本用例必红。
    """
    app = ownership["app"]
    r = asyncio.run(_request(app, "GET",
                             f"/api/projects/{ownership['pid_b']}/chapters/{ownership['chap_a']}/deltas"))
    assert r.status_code == 404, (
        f"跨项目读 delta 必须 404，实际 {r.status_code}：{r.text[:300]}"
    )
    _no_delta_leaked(r)


def test_list_deltas_rejects_own_project_foreign_chapter(ownership: dict):
    """反向方向：A 的 project_id + **B 的章** ⇒ 404。

    与上一条**不是同一条**：上一条钉「拿别人的项目来读我的章」，这条钉
    「拿我的项目去读别人的章」。两个方向都漏才是同一个 bug，只堵一边等于没堵。
    """
    app = ownership["app"]
    r = asyncio.run(_request(app, "GET",
                             f"/api/projects/{ownership['pid_a']}/chapters/{ownership['chap_b']}/deltas"))
    assert r.status_code == 404, f"本项目下的外项目章必须 404，实际 {r.status_code}：{r.text[:300]}"
    _no_delta_leaked(r)


def test_list_deltas_still_returns_rows_for_own_chapter(ownership: dict):
    """⭐ 反向（防「校验写太狠把正常路径也拦了」）：本项目的章 ⇒ **200 且真有行**。

    只写「404」那条的话，一个「一律 404」的改法也能全绿——所以这里必须同时
    钉住**正向仍返回真实行**，且行数下界由夹具给出。
    """
    app = ownership["app"]
    r = asyncio.run(_request(app, "GET",
                             f"/api/projects/{ownership['pid_a']}/chapters/{ownership['chap_a']}/deltas"))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 2, f"本项目的章必须仍能读到行，实际 {len(rows)}"
    assert all(row["chapter_id"] == ownership["chap_a"] for row in rows), (
        "返回的行不属于被请求的章"
    )


def test_unknown_and_foreign_chapter_404_carry_no_discriminator(ownership: dict):
    """⚠️ **不泄漏**：章「不存在」与章「属于别的项目」必须返回**同一形状**的 404。

    这是选 404 而非 403/400 的全部理由（单用户本机自用工具，「不属于」即「不存在」）。
    若只把状态码改成 404 而留着 ``does not belong to project`` 的 detail，
    等于用 detail 把要避免的泄露又漏一遍——本用例专钉这一格。

    三种情形（别项目的章 / 本项目下的外项目章 / 压根不存在的章）逐一走一遍，
    断言**键集与 detail 模板完全一致**且 detail 里不含任何归属线索。
    """
    app = ownership["app"]
    cases = [
        (ownership["pid_b"], ownership["chap_a"]),
        (ownership["pid_a"], ownership["chap_b"]),
        (ownership["pid_a"], "ch_definitely_not_here"),
    ]
    for pid, cid in cases:
        r = asyncio.run(_request(app, "GET", f"/api/projects/{pid}/chapters/{cid}/deltas"))
        assert r.status_code == 404, f"{pid}/{cid} 应 404，实际 {r.status_code}"
        body = r.json()
        assert set(body) == {"detail"}, f"404 体多/少了键，那本身就是泄露面：{body}"
        assert body["detail"] == f"chapter {cid!r} not found", (
            f"detail 不是「不存在」模板（可能带了归属线索）：{body}"
        )
        for leak in ("belong", "project", ownership["pid_a"], ownership["pid_b"]):
            assert leak not in body["detail"], f"detail 泄漏了归属线索 {leak!r}：{body}"


def test_missing_project_error_path_is_unchanged(ownership: dict):
    """项目不存在 ⇒ 仍走**既有**的 404 分支，detail 一字不改。

    归属校验是**加**在 ``_ensure_project`` **之后**的：项目缺失时必须仍然是
    ``project '...' not found``，否则会把一条既有错误路径悄悄换成另一条。
    """
    app = ownership["app"]
    r = asyncio.run(_request(app, "GET",
                             f"/api/projects/prj_does_not_exist/chapters/{ownership['chap_a']}/deltas"))
    assert r.status_code == 404
    assert r.json() == {"detail": "project 'prj_does_not_exist' not found"}, (
        f"项目缺失的既有 404 分支被改动了：{r.text}"
    )


# --------------------------------- 同款缺口：body 里的 chapter_id（init / promote）


def test_init_state_rejects_chapter_from_another_project(ownership: dict):
    """同款缺口 1/2：``POST /projects/{B}/state/init {"chapter_id": A 的章}``。

    修前实测 **201**——给 B 建出 v1 快照，同时留下一条 ``chapter_id`` 指向 A 的章的
    genesis delta 行（``state_deltas.chapter_id`` 的 FK 只保证「章存在」，不保证
    「章属于本项目」）。除了状态码，还要钉住**没有写入任何东西**。
    """
    app = ownership["app"]
    before = _delta_count(ownership["db_path"])
    r = asyncio.run(_request(app, "POST", f"/api/projects/{ownership['pid_b']}/state/init",
                             json={"chapter_id": ownership["chap_a"]}))
    assert r.status_code == 404, f"跨项目 init 必须 404，实际 {r.status_code}：{r.text[:300]}"
    assert _delta_count(ownership["db_path"]) == before, "被拒的请求仍写了 state_deltas 行"
    # B 也不该因此拿到 v1 快照（无快照时本仓口径是 200 + state_version=0 的空态，
    # 所以这里钉 version 而不是状态码——实测出来的口径，不是我以为的那个）
    r = asyncio.run(_request(app, "GET", f"/api/projects/{ownership['pid_b']}/state"))
    assert r.status_code == 200, r.text
    assert r.json()["state_version"] == 0, f"B 不该因这次请求拿到 v1 快照：{r.text[:300]}"


def test_init_state_still_works_for_own_chapter(ownership: dict):
    """反向：init 本项目的章 ⇒ 仍 **201**（全仓 20+ 处测试与 smoke_e2e 都走这条）。"""
    app = ownership["app"]
    pid = (asyncio.run(_request(app, "POST", "/api/projects",
                                json={"name": "归属 C（反向）"}))).json()["project_id"]
    chap = (asyncio.run(_request(app, "POST", f"/api/projects/{pid}/chapters",
                                 json={"number": 1, "title": "C1"}))).json()["chapter_id"]
    r = asyncio.run(_request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}))
    assert r.status_code == 201, r.text
    assert r.json()["state_version"] == 1


def test_promote_branch_rejects_chapter_from_another_project(ownership: dict):
    """同款缺口 2/2：``branches/{bid}/promote {"chapter_id": 别的项目的章}``。

    修前实测 **201**。危害比读接口更重——``promote_branch`` 里
    ``replay_chapter_id = chapter_id or source_chapter_id``，**参数优先于**
    commit 自带的 chapter_id，所以传错会真的往 main 写出一条指向别的项目的章的重放 delta。
    """
    app = ownership["app"]
    before = _delta_count(ownership["db_path"])
    r = asyncio.run(_request(
        app, "POST",
        f"/api/projects/{ownership['pid_a']}/branches/{ownership['branch_ids']['alt']}/promote",
        json={"chapter_id": ownership["chap_b"]},
    ))
    assert r.status_code == 404, f"跨项目 promote 必须 404，实际 {r.status_code}：{r.text[:300]}"
    assert _delta_count(ownership["db_path"]) == before, "被拒的 promote 仍写了重放 delta 行"
    # 分支必须保持 ACTIVE（没被合并）
    r = asyncio.run(_request(app, "GET", f"/api/projects/{ownership['pid_a']}/branches"))
    by_id = {b["branch_id"]: b for b in r.json()}
    assert by_id[ownership["branch_ids"]["alt"]]["status"] == "ACTIVE", "被拒的 promote 仍合并了分支"


def test_promote_branch_still_works_for_own_chapter_and_omitted(ownership: dict):
    """反向：promote 传本项目的章 ⇒ **201**；``chapter_id`` **缺省（None）** 也是合法输入。

    缺省走 ``promote_branch`` 内部的 commit 自带 chapter_id 兜底，所以校验只能
    在「显式给了值」时做——这里两条正路一起钉，防止一个「无条件校验」的实现
    把 ``json={}`` 的调用方全打成 404（``test_story_state_write_response_contract.py``
    正是用 ``json={}`` 调的）。
    """
    app = ownership["app"]
    r = asyncio.run(_request(
        app, "POST",
        f"/api/projects/{ownership['pid_a']}/branches/{ownership['branch_ids']['alt']}/promote",
        json={"chapter_id": ownership["chap_a"]},
    ))
    assert r.status_code == 201, f"本项目的章必须仍能 promote：{r.text[:300]}"
    assert r.json()["promoted_commits"] == 1

    r = asyncio.run(_request(
        app, "POST",
        f"/api/projects/{ownership['pid_a']}/branches/{ownership['branch_ids']['alt2']}/promote",
        json={},
    ))
    assert r.status_code == 201, f"chapter_id 缺省是合法输入：{r.text[:300]}"


# ---------------------------------------------------------------------------
# 7) 归属校验的**复用**（防「另起一份实现」）
# ---------------------------------------------------------------------------


def test_chapter_ownership_check_reuses_the_authoritative_helper():
    """⚠️ 「不要另起一份归属校验实现」的可判别抓手。

    ``workflows/common.py::check_chapter_in_project`` 是本仓**权威实现**（其
    docstring 明写「校验逻辑只此一份，两条路径不得分叉」）。若 ``story_state.py``
    另写一份 ``SELECT project_id FROM chapters ...``，两处会各自漂移。

    本用例按**读取面**判别：``story_state.py`` 的源码里不得出现自己写的章节归属
    SQL / 第二个 ``chapters`` 表查询——只允许 import 那个 helper 并调它。
    """
    src = Path(story_state_router.__file__).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "FROM chapters",
        "get_project_id",
        "ChapterService",
    ):
        assert forbidden not in body, (
            f"story_state.py 自己写了章节归属查询（{forbidden!r}）——"
            "必须复用 workflows.common.check_chapter_in_project（唯一权威实现）"
        )
    assert "check_chapter_in_project" in body, (
        "story_state.py 没有复用 workflows.common.check_chapter_in_project"
    )





# ---------------------------------------------------------------------------
# 5) 夹具自身的不变量：零真实 LLM
# ---------------------------------------------------------------------------


def test_e2e_db_cannot_reach_a_real_provider(harvested: dict, tmp_path_factory):
    """临时库**无任何模型配置** ⇒ 真实 Provider 在构造之前就不可达（零真实调用）。

    与 ``test_workflow_run_response_contract.py`` / ``test_fast_commit_e2e.py`` 同款。
    缺了它，「不发真实 LLM 调用」就只是巧合：任何人往夹具里播种一行 ``model_configs``，
    本组将来若改成真跑 workflow 就会真的发请求。

    ⚠️ module 级 fixture 的临时库已被回收，这里重建一个同形态的空库来量。
    """
    tmp_path = tmp_path_factory.mktemp("l2_invariant")
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(str(settings.db_path))
    try:
        for table in ("model_configs", "model_profiles", "capability_bindings"):
            n = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            assert n == 0, f"{table} 非空 ⇒ 真实 Provider 可被构造，本夹具会发真实请求"
    finally:
        conn.close()
    assert harvested["pid"], "顺带确认夹具确实跑起来了"
