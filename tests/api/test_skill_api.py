"""skill API 路由测试（内部消费外部 skill）。

8 条端点：
- ``POST   /api/skills``                        —— 创建（201）；payload 不合 schema → 422
- ``GET    /api/skills``                        —— 列表摘要
- ``GET    /api/skills/{skill_id}``             —— 详情
- ``PUT    /api/skills/{skill_id}``             —— 更新（提供 payload ⇒ version 自增）
- ``DELETE /api/skills/{skill_id}``             —— 204；被项目绑定 → 409
- ``GET    /api/projects/{pid}/skills``          —— 当前绑定集合（有序 + 全文）
- ``POST   /api/projects/{pid}/skills/bind``    —— 绑定（多 slot 有序，覆盖式）
- ``POST   /api/projects/{pid}/skills/unbind``  —— 解绑（幂等）

⚠️ **键集断言用硬编码常量**（第三方基准），不是「跑一下看返回什么」——
那是坑区「夹具从被测代码的实际输出里抄形状 ⇒ 缺陷被写进断言」的成因。
键集来源：``skills`` 表 8 列（0032 DDL：skill_id / name / description / version /
payload_json / source_path / created_at / updated_at），其中 ``payload_json`` 在投影里
改名为 ``payload``，另加 1 个派生列 ``bound_project_count`` = 9 键。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations

EXPECTED_SKILL_KEYS = frozenset(
    {
        "skill_id",
        "name",
        "description",
        "version",
        "payload",
        "source_path",
        "created_at",
        "updated_at",
        "bound_project_count",
    }
)

EXPECTED_SUMMARY_KEYS = frozenset(
    {
        "skill_id",
        "name",
        "description",
        "version",
        "source_path",
        "created_at",
        "updated_at",
        "applies_to",
        "reference_count",
        "check_count",
        "bound_project_count",
    }
)

_PAYLOAD: dict = {
    "schema_version": "skill.v1.0.0",
    "name": "对白潜台词",
    "instructions": "不要直白说出情绪，用动作与停顿承载。",
    "applies_to": ["writer", "critic"],
    "references": [{"path": "skills/dialogue/ref.md", "title": "示例", "body": "示例正文"}],
    "checks": [{"check_id": "exposed", "description": "直白情绪词", "keywords": ["其实"]}],
}


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _run(coro):
    return asyncio.run(coro)


async def _make_project(app, name: str = "skill 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _create_skill(
    app, *, skill_id: str = "skl_api", payload: dict | None = None, name: str = "对白潜台词",
) -> httpx.Response:
    return await _request(
        app,
        "POST",
        "/api/skills",
        json={
            "skill_id": skill_id,
            "name": name,
            "description": "别直白说情绪",
            "payload": _PAYLOAD if payload is None else payload,
            "source_path": "skills/dialogue",
        },
    )


# ---------------------------------------------------------------------------
# 创建 / 列表 / 详情
# ---------------------------------------------------------------------------


def test_create_skill_returns_201(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _create_skill(app)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["skill_id"] == "skl_api"
        assert body["version"] == 1
        assert body["description"] == "别直白说情绪"
        assert body["source_path"] == "skills/dialogue"
        assert body["bound_project_count"] == 0
        assert body["payload"]["instructions"]
        assert set(body) == set(EXPECTED_SKILL_KEYS), (
            f"多={sorted(set(body) - EXPECTED_SKILL_KEYS)} "
            f"少={sorted(EXPECTED_SKILL_KEYS - set(body))}"
        )

    _run(run())


def test_create_skill_422_on_invalid_payload(tmp_path: Path):
    """payload 不合 schema → 422（``detail={"errors": [...]}``）。"""
    app = _create_app(tmp_path)

    async def run():
        # 顶层 version 是禁字段（additionalProperties: false）
        r = await _create_skill(app, payload={**_PAYLOAD, "version": 2})
        assert r.status_code == 422, r.text
        errors = r.json()["detail"]["errors"]
        assert errors and any("version" in e for e in errors), f"实际：{errors}"

        # 缺必填字段
        bad = {k: v for k, v in _PAYLOAD.items() if k != "instructions"}
        r2 = await _create_skill(app, skill_id="skl_bad", payload=bad)
        assert r2.status_code == 422, r2.text
        assert any("instructions" in e for e in r2.json()["detail"]["errors"])

    _run(run())


def test_create_skill_422_on_duplicate_skill_id(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        assert (await _create_skill(app)).status_code == 201
        r = await _create_skill(app)
        assert r.status_code == 422, r.text

    _run(run())


def test_list_skills_returns_summary_shape(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        await _create_skill(app)
        r = await _request(app, "GET", "/api/skills")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert set(rows[0]) == set(EXPECTED_SUMMARY_KEYS), (
            f"多={sorted(set(rows[0]) - EXPECTED_SUMMARY_KEYS)} "
            f"少={sorted(EXPECTED_SUMMARY_KEYS - set(rows[0]))}"
        )
        assert rows[0]["applies_to"] == ["writer", "critic"]
        assert rows[0]["reference_count"] == 1
        assert rows[0]["check_count"] == 1

    _run(run())


def test_get_skill_detail_and_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        await _create_skill(app)
        r = await _request(app, "GET", "/api/skills/skl_api")
        assert r.status_code == 200, r.text
        assert set(r.json()) == set(EXPECTED_SKILL_KEYS)

        missing = await _request(app, "GET", "/api/skills/skl_nope")
        assert missing.status_code == 404, missing.text

    _run(run())


# ---------------------------------------------------------------------------
# 更新 / 删除
# ---------------------------------------------------------------------------


def test_update_skill_bumps_version_on_payload_change(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        await _create_skill(app)
        r = await _request(
            app,
            "PUT",
            "/api/skills/skl_api",
            json={"payload": {**_PAYLOAD, "instructions": "换一套说法。"}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == 2, "payload 变更必须 version 自增"
        assert body["payload"]["instructions"] == "换一套说法。"

        # 只改元信息 ⇒ version 不变
        r2 = await _request(app, "PUT", "/api/skills/skl_api", json={"name": "新名字"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["version"] == 2, "元信息改动不得 bump version"
        assert r2.json()["name"] == "新名字"

    _run(run())


def test_update_skill_422_on_invalid_payload(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        await _create_skill(app)
        r = await _request(
            app, "PUT", "/api/skills/skl_api", json={"payload": {"name": "x"}},
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["errors"]

    _run(run())


def test_update_unknown_skill_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _request(app, "PUT", "/api/skills/skl_nope", json={"name": "x"})
        assert r.status_code == 404, r.text

    _run(run())


def test_delete_skill_204_and_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        await _create_skill(app)
        r = await _request(app, "DELETE", "/api/skills/skl_api")
        assert r.status_code == 204, r.text
        again = await _request(app, "DELETE", "/api/skills/skl_api")
        assert again.status_code == 404, again.text

    _run(run())


def test_delete_bound_skill_409(tmp_path: Path):
    """被项目绑定的 skill ⇒ 409（先解绑再删）。"""
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_skill(app)
        await _request(app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_api"]})
        r = await _request(app, "DELETE", "/api/skills/skl_api")
        assert r.status_code == 409, r.text
        assert "bound" in r.json()["detail"]

    _run(run())


# ---------------------------------------------------------------------------
# 绑定 / 解绑
# ---------------------------------------------------------------------------


def test_bind_project_skills_is_ordered(tmp_path: Path):
    """绑定是**多 slot 有序**：请求体列表顺序 = 响应顺序 = 注入顺序。"""
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_skill(app, skill_id="skl_a", name="A")
        await _create_skill(app, skill_id="skl_b", name="B")
        r = await _request(
            app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_b", "skl_a"]},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert [s["skill_id"] for s in rows] == ["skl_b", "skl_a"]
        assert set(rows[0]) == set(EXPECTED_SKILL_KEYS)

        # 读端点与绑定响应同序
        g = await _request(app, "GET", f"/api/projects/{pid}/skills")
        assert g.status_code == 200, g.text
        assert [s["skill_id"] for s in g.json()] == ["skl_b", "skl_a"]

    _run(run())


def test_bind_is_overwriting(tmp_path: Path):
    """覆盖式：二次绑定只保留新集合。"""
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_skill(app, skill_id="skl_a", name="A")
        await _create_skill(app, skill_id="skl_b", name="B")
        await _request(
            app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_a", "skl_b"]},
        )
        r = await _request(
            app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_b"]},
        )
        assert r.status_code == 200, r.text
        assert [s["skill_id"] for s in r.json()] == ["skl_b"]

    _run(run())


def test_bind_404_for_unknown_project_or_skill(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_skill(app)
        r1 = await _request(
            app, "POST", "/api/projects/prj_nope/skills/bind", json={"skill_ids": ["skl_api"]},
        )
        assert r1.status_code == 404, r1.text
        r2 = await _request(
            app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_ghost"]},
        )
        assert r2.status_code == 404, r2.text

    _run(run())


def test_unbind_is_idempotent(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_skill(app)
        await _request(app, "POST", f"/api/projects/{pid}/skills/bind", json={"skill_ids": ["skl_api"]})
        r = await _request(
            app, "POST", f"/api/projects/{pid}/skills/unbind", json={"skill_id": "skl_api"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == []
        again = await _request(
            app, "POST", f"/api/projects/{pid}/skills/unbind", json={"skill_id": "skl_api"},
        )
        assert again.status_code == 200, "重复解绑必须幂等（200）"
        assert again.json() == []

    _run(run())


def test_unbind_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _request(
            app, "POST", "/api/projects/prj_nope/skills/unbind", json={"skill_id": "skl_api"},
        )
        assert r.status_code == 404, r.text

    _run(run())


def test_get_project_skills_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _request(app, "GET", "/api/projects/prj_nope/skills")
        assert r.status_code == 404, r.text

    _run(run())


def test_openapi_documents_references_are_not_progressive(tmp_path: Path):
    """R7：端点响应侧（OpenAPI 描述）必须显式说明「references 不是渐进披露」。

    NovelOS 的模型**没有 tool use** ⇒ references 只能在装配期注入。API 消费方若
    误以为「模型能按需读取」，就会写出与实际可见性不符的预期。
    """
    app = _create_app(tmp_path)
    schema = app.openapi()
    text = json.dumps(schema, ensure_ascii=False)
    assert "不是渐进披露" in text, (
        "OpenAPI 描述里必须写明「references 不是渐进披露」"
    )
    # 描述挂在 skill 的两个读端点上（创建 / 详情）
    for path, method in (("/api/skills", "post"), ("/api/skills/{skill_id}", "get")):
        desc = schema["paths"][path][method].get("description", "")
        assert "不是渐进披露" in desc, (
            f"{method.upper()} {path} 的描述必须写明「references 不是渐进披露」"
        )
