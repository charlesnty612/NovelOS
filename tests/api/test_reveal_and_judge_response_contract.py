"""剧透策略与人工评分的 response_model 契约测试（2026-10-15 批次（五））。

两处各有**一个会静默丢数据**的坑，本组用例各钉一条：

1. **`RevealPolicyOut` 必须与 `RevealPolicy.to_dict()` 逐字同形**。
   本仓现在有**两份**剧透策略模型：service 层在用的 `dataclass` 与 API 契约用的
   Pydantic `RevealPolicyOut`。两份一旦漂移，就会出现「写进去是一套、取出来是另一套」
   的静默分叉——而 dataclass 那一侧**没有任何测试**覆盖。
   ⇒ 第 1 条用例把两边的键集直接比对，新增字段只改一边必红。

2. ⚠️ **`QualityJudgeRecord` 必须 `extra="allow"`**。
   `QualityService.save_judge_score` 的 docstring 明写「额外 verdict / top_issues /
   **score_avg / usage / dry_run** 等字段一并存入 JSON（宽松校验）」。
   若把 `judge` 声明成严格模型，``response_model`` 会**过滤**掉这些扩展键——
   而它们已经躺在 `quality_reports.judge_json` 里了，只是取不回来。
   症状：数据在库里、API 读不到、没有任何报错。
   ⇒ 第 4 条用例直接塞扩展键往返一次，撤掉 `extra` 必红。
   （与 `tests/api/test_volume_membership_contract.py` 守的是同一族形状。）

另外两条钉住**行为**（防被未来的重构悄悄改掉）：
- 越界分是**静默夹取**不是 422；
- PATCH 传未知字段从「静默忽略」变成 422（`extra="forbid"` 的本意）。

3. **剧透策略出参必须打「硬编码第三方基准」**（``EXPECTED_REVEAL_POLICY_KEYS``，11 键字面量）。
   第 1 条用例原本比的是「dataclass ``to_dict()`` 键集 == ``set(RevealPolicyOut.model_fields)``」
   ⇒ 这两侧是**两个独立来源**（router 返回的是 dataclass 的 ``p.to_dict()``；模型只是
   ``response_model`` 的校验/序列化侧）⇒ **单侧收窄**（只改模型 **或** 只改 dataclass）
   任一侧都会红。**但**它有两个盲格：
   - **① 两侧同时删同一个键** ⇒ 11 → 10，契约静默缩水、零告警；
   - **② 模型新增一个带默认值的可选字段** ⇒ ``extra="allow"`` 让 ``response_model``
     校验时**回填 null** ⇒ 响应与 ``model_fields`` **同步变大** ⇒ 仍然绿。
   ⇒ 本组在**响应侧**与**``model_fields`` 侧**都改打**硬编码**基线 ⇒ 两个盲格都变红。
   （⚠️ 这是**加强**判别力：原断言**保留**，只是旁边多了一个独立基准。）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.models import QualityJudgeRecord
from packages.domain.knowledge.models import RevealPolicy, RevealPolicyOut

from ._contract_runner import (
    assert_keys_match,
    assert_model_fields_match,
    client_ctx,
    create_test_app,
)


def _create_app(tmp_path: Path):
    """共享 runner 的 ``create_test_app``（**不**显式跑迁移；迁移在 lifespan 里跑）。"""
    return create_test_app(tmp_path)


def _client(app):
    """⚠️ 迁移只在 lifespan 里跑（`httpx.ASGITransport` 不触发 lifespan）。"""
    return client_ctx(app, enter_lifespan=True)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _client(app) as c:
        return await c.request(method, path, **kwargs)


#: 剧透策略出参的**硬编码第三方基准**（11 键字面量；依据：dataclass ``to_dict()`` 的
#: 键集 + 临时库实跑端点 dump 出的响应键集，二者实测一致）。
#:
#: ⚠️ 为什么必须硬编码（**不是**「原断言恒真」）：原断言比的是「dataclass 键集 ==
#: ``set(Model.model_fields)``」⇒ 两侧是**独立来源** ⇒ **单侧收窄能红**；但
#: **两侧同时删同一个键**、或**模型新增带默认值的可选字段**（``extra="allow"`` 会让
#: 校验回填 null ⇒ 响应与模型同步变大）这两个盲格，**只有硬编码基线能抓**。
EXPECTED_REVEAL_POLICY_KEYS = {
    "policy_id",
    "project_id",
    "target_kind",
    "target_id",
    "reveal_by_chapter",
    "audience",
    "status",
    "revealed_chapter",
    "notes",
    "created_at",
    "updated_at",
}


def _seed(app) -> tuple[str, str]:
    """建项目 + 一章 + 一个角色，返回 (pid, character_id)。"""
    import asyncio

    async def run():
        async with _client(app) as c:
            pid = (await c.post("/api/projects", json={"name": "契约"})).json()["project_id"]
            await c.post(f"/api/projects/{pid}/chapters", json={"number": 1, "title": "一"})
            char = await c.post(f"/api/projects/{pid}/characters", json={"name": "林昭"})
            return pid, char.json()["character_id"]

    return asyncio.run(run())


# --------------------------------------------------------------- 剧透策略


def test_reveal_out_matches_dataclass_to_dict_exactly():
    """两份模型逐字同形：Pydantic 出参字段集 == dataclass to_dict() 键集。

    撤任一字段声明、或 dataclass 加字段而忘了同步 ⇒ 必红。

    ⚠️ 上面那一句比对**保留原样**（单侧收窄能红）；**另外**加两道**硬编码基线**
    （``EXPECTED_REVEAL_POLICY_KEYS``）⇒ 抓住「两侧同时收窄」与「模型新增带默认值的
    可选字段」这两个原断言抓不到的盲格（见模块 docstring 第 3 条）。
    """
    dc = RevealPolicy(
        policy_id="rp_1",
        project_id="prj_1",
        target_kind="character",
        target_id="char_1",
    )
    assert set(dc.to_dict()) == set(RevealPolicyOut.model_fields), (
        f"多={set(dc.to_dict()) - set(RevealPolicyOut.model_fields)} "
        f"少={set(RevealPolicyOut.model_fields) - set(dc.to_dict())}"
    )
    # 硬编码第三方基准（两侧都打 ⇒ 「两侧同时删同一个键」必红）
    assert_keys_match(dc.to_dict(), EXPECTED_REVEAL_POLICY_KEYS, "dataclass to_dict")
    assert_model_fields_match(RevealPolicyOut, EXPECTED_REVEAL_POLICY_KEYS)


def test_reveal_endpoints_return_all_eleven_keys(tmp_path: Path):
    """list / create / patch 三条路径都返回全部 11 个键（不是超集断言）。"""
    app = _create_app(tmp_path)
    pid, cid = _seed(app)

    created = asyncio.run(
        _request(
            app,
            "POST",
            f"/api/projects/{pid}/reveal-policies",
            json={"target_kind": "character", "target_id": cid, "reveal_by_chapter": 3},
        )
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == set(RevealPolicyOut.model_fields), (
        f"多={set(body) - set(RevealPolicyOut.model_fields)} "
        f"少={set(RevealPolicyOut.model_fields) - set(body)}"
    )
    # 硬编码第三方基准（响应侧）⇒ 「两侧同时收窄」/「模型新增可选字段回填 null」必红
    assert_keys_match(body, EXPECTED_REVEAL_POLICY_KEYS, "POST /reveal-policies 响应")

    listed = asyncio.run(_request(app, "GET", f"/api/projects/{pid}/reveal-policies"))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert set(listed.json()[0]) == set(RevealPolicyOut.model_fields)
    assert_keys_match(listed.json()[0], EXPECTED_REVEAL_POLICY_KEYS, "GET /reveal-policies 行")

    patched = asyncio.run(
        _request(
            app,
            "PATCH",
            f"/api/projects/{pid}/reveal-policies/{body['policy_id']}",
            json={"status": "revealed", "revealed_chapter": 2, "notes": "第一章末"},
        )
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == set(RevealPolicyOut.model_fields)
    assert_keys_match(patched.json(), EXPECTED_REVEAL_POLICY_KEYS, "PATCH /reveal-policies 响应")
    assert patched.json()["status"] == "revealed"
    assert patched.json()["revealed_chapter"] == 2


def test_reveal_patch_rejects_unknown_field_instead_of_silently_ignoring(tmp_path: Path):
    """PATCH 传未知字段从「静默忽略」变 422 —— 这是本次**有意的行为变更**。

    service 只读 status / revealed_chapter / notes，过去传 `reveal_by_chapter`
    会返回 200 却什么都没改；`extra="forbid"` 把它显式化。
    """
    app = _create_app(tmp_path)
    pid, cid = _seed(app)
    created = asyncio.run(
        _request(
            app,
            "POST",
            f"/api/projects/{pid}/reveal-policies",
            json={"target_kind": "character", "target_id": cid},
        )
    )
    assert created.status_code == 201, created.text

    rejected = asyncio.run(
        _request(
            app,
            "PATCH",
            f"/api/projects/{pid}/reveal-policies/{created.json()['policy_id']}",
            json={"audience": "character:char_1"},
        )
    )
    assert rejected.status_code == 422, rejected.text


# --------------------------------------------------------------- 人工评分


def _seeded_chapter(app) -> tuple[str, str]:
    """建项目 + 一章 + 一份 quality_report，返回 (pid, cid)。"""
    async def run():
        async with _client(app) as c:
            pid = (await c.post("/api/projects", json={"name": "评分契约"})).json()[
                "project_id"
            ]
            cid = (
                await c.post(
                    f"/api/projects/{pid}/chapters", json={"number": 1, "title": "一"}
                )
            ).json()["chapter_id"]
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    """INSERT INTO quality_reports
                       (report_id, project_id, chapter_id, commit_id, run_id,
                        overall, scores_json, issues_json, created_at)
                       VALUES (?,?,?,NULL,NULL,?,?,'[]',?)""",
                    (
                        new_id("qr"),
                        pid,
                        cid,
                        80,
                        json.dumps({"overall": 80}),
                        now_iso(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            return pid, cid

    return asyncio.run(run())


def test_judge_response_declared_and_keys_match(tmp_path: Path):
    app = _create_app(tmp_path)
    schema = app.openapi()
    assert "QualityJudgeResponse" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/chapters/{chapter_id}/quality/judge"]["post"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/QualityJudgeResponse"), ref

    # ⚠️ schema 里**必须声明** judge，而不只是「响应里有这个键」。
    # 因为 `QualityJudgeResponse` 是 `extra="allow"`：把 `judge` 字段从模型里
    # 删掉，响应**照样**有这个键（它以 extra 身份透出），但 OpenAPI 里不再有
    # 声明 ⇒ 前端 codegen 丢掉 `judge` 的类型，契约悄悄消失。
    # 只断言响应键集抓不到这一条（突变 M20 实测存活）。
    props = schema["components"]["schemas"]["QualityJudgeResponse"]["properties"]
    assert "judge" in props, f"OpenAPI 里 QualityJudgeResponse 未声明 judge：{sorted(props)}"
    assert "judge" in schema["components"]["schemas"]["QualityJudgeResponse"].get(
        "required", []
    ), "judge 恒在（router 必写），应进 required"

    pid, cid = _seeded_chapter(app)
    r = sync_request(
        app,
        "POST",
        f"/api/chapters/{cid}/quality/judge",
        json={"pacing": 80, "style": 70, "logic": 90, "dialogue": 60, "verdict": "节奏偏慢"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"report_id", "chapter_id", "judge"}, body
    assert body["judge"]["pacing"] == 80
    assert body["judge"]["verdict"] == "节奏偏慢"


def test_judge_record_keeps_extension_keys():
    """⚠️ 核心守卫：扩展键（score_avg / usage / dry_run）不得被 response_model 裁掉。

    service 明写这些键「一并存入 JSON」；声明成严格模型就会静默丢失——
    数据在库里、API 读不到、零报错。撤掉 ``extra="allow"`` 必红。
    """
    stored = {
        "pacing": 80,
        "style": 70,
        "logic": 90,
        "dialogue": 60,
        "verdict": "还行",
        "score_avg": 75.0,
        "usage": {"prompt_tokens": 1234, "completion_tokens": 567},
        "dry_run": False,
    }
    dumped = QualityJudgeRecord(**stored).model_dump()
    for key in ("score_avg", "usage", "dry_run"):
        assert key in dumped, f"扩展键 {key} 被裁掉了 ⇒ extra='allow' 没了"
    assert dumped["usage"] == stored["usage"]


def test_judge_out_of_range_is_clamped_not_422(tmp_path: Path):
    """钉住「越界静默夹取」这条既有行为（不是本次引入，防被改成 422 或反之）。

    `save_judge_score` 做 ``max(0, min(100, int(v)))``；只有**非整数**才 ValueError→422。
    """
    app = _create_app(tmp_path)
    pid, cid = _seeded_chapter(app)
    r = sync_request(
        app,
        "POST",
        f"/api/chapters/{cid}/quality/judge",
        json={"pacing": 150, "style": -20, "logic": 90.0, "dialogue": 60},
    )
    assert r.status_code == 200, r.text
    assert r.json()["judge"]["pacing"] == 100
    assert r.json()["judge"]["style"] == 0
    # 浮点整数被接受（72.0 → 72）
    assert r.json()["judge"]["logic"] == 90


def test_judge_non_integer_is_422(tmp_path: Path):
    app = _create_app(tmp_path)
    pid, cid = _seeded_chapter(app)
    r = sync_request(
        app,
        "POST",
        f"/api/chapters/{cid}/quality/judge",
        json={"pacing": "高", "style": 70, "logic": 90, "dialogue": 60},
    )
    assert r.status_code == 422, r.text


def sync_request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """在同步用例里发一次请求（端点是 async 的，用例是同步的）。"""
    return asyncio.run(_request(app, method, path, **kwargs))
