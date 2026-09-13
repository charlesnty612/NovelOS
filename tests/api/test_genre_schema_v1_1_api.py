"""题材包 API：schema v1.1.0 新段（题材库 P2）。

覆盖：
1. 含新段（opening_rules / critic_rubric）的 v1.1.0 payload → 201，回读 payload 一致；
2. 旧 v1.0.0 payload（无新段）→ 201（minor 兼容，写路径不因新 schema 破坏旧数据）；
3. 新段类型错误 → 422 + ``detail.errors`` 逐条列出路径；
4. PUT 用 v1.1.0 新段更新 → version 自增且回读新段。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations

_V1_1_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "verify_hint": "打脸后至少三人当场反应",
        }
    ],
    "opening_rules": [
        {
            "check_id": "sys_bind_ch1",
            "description": "系统绑定不得晚于第 1 章",
            "chapter_no": 1,
            "requirement": "第 1 章必须出现系统绑定",
        }
    ],
    "critic_rubric": {
        "payoff_focus": ["face_slap"],
        "taboo_notes": "不得出现具体平台名",
        "style_notes": "短句为主",
    },
}

_OLD_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [{"type_id": "face_slap", "name": "打脸"}],
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


async def _make_project(app, name: str = "题材项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _create_pack(app, pid: str, payload: dict, pack_id: str = "gp_p2") -> httpx.Response:
    return await _request(
        app,
        "POST",
        f"/api/projects/{pid}/genre-packs",
        json={"name": "男主快穿", "genre_tag": "快穿", "payload": payload, "pack_id": pack_id},
    )


def test_create_pack_with_new_sections_returns_201(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        r = await _create_pack(app, pid, _V1_1_PAYLOAD)
        assert r.status_code == 201, r.text
        assert r.json()["payload"]["opening_rules"][0]["check_id"] == "sys_bind_ch1"

        detail = await _request(app, "GET", "/api/genre-packs/gp_p2")
        assert detail.status_code == 200
        payload = detail.json()["payload"]
        assert payload["critic_rubric"]["payoff_focus"] == ["face_slap"]
        assert payload["schema_version"] == "genre-pack.v1.1.0"

    _run(run())


def test_create_pack_with_old_v1_0_0_payload_still_201(tmp_path: Path):
    """旧 v1.0.0 payload 继续可写（v1 线内 minor 兼容）。"""
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        r = await _create_pack(app, pid, _OLD_PAYLOAD, pack_id="gp_old")
        assert r.status_code == 201, r.text
        assert r.json()["payload"]["schema_version"] == "genre-pack.v1.0.0"

    _run(run())


def test_create_pack_422_on_bad_opening_rule_chapter_no(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        bad = {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [{"check_id": "r1", "chapter_no": 4, "requirement": "x"}],
        }
        r = await _create_pack(app, pid, bad, pack_id="gp_bad1")
        assert r.status_code == 422, r.text
        errors = r.json()["detail"]["errors"]
        assert any("chapter_no" in e for e in errors), errors

    _run(run())


def test_create_pack_422_on_bad_critic_rubric_types(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        bad = {
            "schema_version": "genre-pack.v1.1.0",
            "critic_rubric": {"payoff_focus": "face_slap", "taboo_notes": 7},
        }
        r = await _create_pack(app, pid, bad, pack_id="gp_bad2")
        assert r.status_code == 422, r.text
        errors = r.json()["detail"]["errors"]
        assert any("critic_rubric/payoff_focus" in e for e in errors), errors

    _run(run())


def test_put_with_v1_1_0_sections_bumps_version(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        r = await _create_pack(app, pid, _OLD_PAYLOAD, pack_id="gp_up")
        assert r.status_code == 201, r.text
        assert r.json()["version"] == 1

        put = await _request(
            app, "PUT", "/api/genre-packs/gp_up", json={"payload": _V1_1_PAYLOAD},
        )
        assert put.status_code == 200, put.text
        assert put.json()["version"] == 2
        assert put.json()["payload"]["critic_rubric"]["style_notes"] == "短句为主"

    _run(run())
