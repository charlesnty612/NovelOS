"""番茄签约体检 API 集成测试。

覆盖：
- GET /api/projects/{pid}/signing-check → 200 + 结构化 items/summary；
- 不存在的项目 → 404；
- 番茄导出包（fanqie）含「===== 签约体检摘要 =====」段。

测试模式：与 tests/api/test_export_api.py 一致：httpx.ASGITransport + 临时 db。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


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


async def _make_project(app, name: str = "体检项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_protagonist(app, pid: str, name: str) -> None:
    """直接 INSERT 主角，绕开 CharacterService（仅测试 fixture）。"""
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters
                (character_id, project_id, name, role, core_json,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 'protagonist', '{}', 'PUBLIC', NULL, ?, ?)
            """,
            (new_id("char"), pid, name, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


async def _make_chapter_with_draft(
    app,
    pid: str,
    number: int,
    title: str,
    content: str,
    status: str = "COMMITTED",
) -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]
    conn = get_connection(app.state.settings.db_path)
    try:
        # 直接 INSERT 草稿（与 test_export_api 一致：跳过 create_draft 状态机校验）
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, 1, ?, 'agent:writer:v1', NULL, NULL, ?)
            """,
            (new_id("dr"), cid, content, now_iso()),
        )
        # 推进 status 到 COMMITTED（跳过状态机白名单校验，测试 fixture 直写）
        conn.execute(
            "UPDATE chapters SET status = ? WHERE chapter_id = ?",
            (status, cid),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 端点：正常路径
# ---------------------------------------------------------------------------


def test_signing_check_endpoint_returns_full_structure(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "API 体检")
            await _make_protagonist(app, pid, "李晨")
            # 三章满足：黄金三章、前两章含金手指、第三章打脸
            await _make_chapter_with_draft(
                app, pid, 1, "屈辱",
                "李晨被人当面羞辱，全场震惊。「你算什么？」他咬牙立誓。" + "字" * 1700 + "突然……？",
            )
            await _make_chapter_with_draft(
                app, pid, 2, "觉醒",
                "李晨脑中响起一道声音：「系统已绑定」。他睁开眼，看见面板浮现。" + "字" * 1700 + "谁料？",
            )
            await _make_chapter_with_draft(
                app, pid, 3, "打脸",
                "李晨冷笑一声，一巴掌扇在对方面门上。全场寂静，后悔不已。" + "字" * 1700 + "紧接着……！",
            )

            r = await _request(app, "GET", f"/api/projects/{pid}/signing-check")
            assert r.status_code == 200, r.text
            body = r.json()
            # 顶层 schema
            assert body["project_id"] == pid
            assert body["project_name"] == "API 体检"
            assert body["total_chars"] > 0
            assert "generated_at" in body
            assert isinstance(body["items"], list) and len(body["items"]) > 0
            # summary 结构
            summary = body["summary"]
            for key in ("pass_count", "warn_count", "fail_count", "info_count"):
                assert isinstance(summary[key], int)
            # 至少 pass_count >= 3（ch1_conflict / ch1_protagonist / ch2_golden / ch3_climax 都应 pass）
            assert summary["pass_count"] >= 3

    asyncio.run(run())


def test_signing_check_endpoint_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_nope/signing-check")
            assert r.status_code == 404
            detail = r.json()["detail"]
            assert "not found" in detail.lower()

    asyncio.run(run())


def test_signing_check_endpoint_empty_project(tmp_path: Path):
    """无章节无主角：返回单条 info「暂无正文」。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "空项目")
            r = await _request(app, "GET", f"/api/projects/{pid}/signing-check")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total_chars"] == 0
            # 仅一条 info
            assert len(body["items"]) == 1
            assert body["items"][0]["level"] == "info"
            assert body["items"][0]["key"] == "empty"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 集成：番茄投稿包含体检摘要
# ---------------------------------------------------------------------------


def test_fanqie_export_contains_signing_check_summary(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "番茄体检集成")
            await _make_protagonist(app, pid, "李晨")
            await _make_chapter_with_draft(
                app, pid, 1, "起",
                "李晨被人当面羞辱，全场震惊。" + "字" * 1700 + "突然？",
            )
            await _make_chapter_with_draft(
                app, pid, 2, "承",
                "系统绑定，李晨看见面板浮现。" + "字" * 1700 + "谁料？",
            )
            # 触发 fanqie 导出
            r = await _request(
                app, "GET", f"/api/projects/{pid}/export", params={"format": "fanqie"}
            )
            assert r.status_code == 200, r.text
            text = r.content.decode("utf-8-sig")
            # 文末追加体检摘要段
            assert "===== 签约体检摘要 =====" in text
            assert "通过" in text and "警告" in text and "失败" in text
            # 摘要内出现主角相关检查项 key
            assert "ch1_conflict_300" in text
            assert "ch2_golden_finger" in text

    asyncio.run(run())
