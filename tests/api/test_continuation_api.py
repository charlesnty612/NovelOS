"""``/api/projects/{pid}/chapters/{cid}/continue[/adopt]`` 集成测试。

覆盖（任务书给死）：

- continue：mock provider 下 200，variants 数量正确、字段齐全；
- continue：num_variants=0 与 4 → 422（pydantic）；
- continue：chapter PLANNED → 409；project 不存在 → 404；
- continue：chapter 不存在 → 404；chapter 不属于该项目 → 404；
- adopt：成功后 drafts 出现新 version、拼接正确、REVIEWED→DRAFTED 降级生效；
- adopt：空 content → 422；超长 content → 422；
- adopt：chapter PLANNED → 409；project 不存在 → 404。

测试模式参考 ``tests/api/test_chapter_drafts.py``：httpx.ASGITransport + tmp_path db。
mock model_config 配法参考 ``tests/integration/test_agent_runtime_api.py``：
``POST /api/model-configs`` with ``{"capability":"creative_writing","provider":"mock",...}``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "continuation 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _ensure_mock_writer(app) -> None:
    """注册 capability=creative_writing / provider=mock 的 model_config。

    测试在空 db 启动；model_configs 迁移会自动建表。若该 capability 已存在
    enabled=1 行则跳过（不强制幂等，避免多 case 共用同一 tmp_path 时的副作用）。
    """
    r = await _request(
        app,
        "POST",
        "/api/model-configs",
        json={"capability": "creative_writing", "provider": "mock", "model": "mock-cw-1"},
    )
    assert r.status_code == 201, r.text


async def _make_chapter(
    app,
    pid: str,
    *,
    status: str | None = None,
    plan_json: dict | None = None,
) -> str:
    body: dict = {"number": 1, "title": "C1"}
    if plan_json is not None:
        body["plan_json"] = plan_json
    r = await _request(app, "POST", f"/api/projects/{pid}/chapters", json=body)
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]
    if status:
        r = await _request(app, "PATCH", f"/api/chapters/{cid}", json={"status": status})
        assert r.status_code == 200, r.text
    return cid


async def _seed_draft(app, cid: str, content: str = "已有草稿内容") -> int:
    r = await _request(
        app, "POST", f"/api/chapters/{cid}/drafts", json={"content": content}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["version"])


# ---------------------------------------------------------------------------
# continue 端点
# ---------------------------------------------------------------------------


def test_continue_returns_variants_with_mock_provider(tmp_path: Path):
    """mock 模型配置下 continue 返回 200 且 variants 数量正确、字段齐全。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(
                app,
                pid,
                status="DRAFTED",
                plan_json={"key_beats": {"purpose": ["揭露真相", "紧张对峙"]}},
            )
            await _seed_draft(app, cid, content="上文草稿内容" * 30)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 3, "instruction": "加点心理描写"},
            )
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["model"] == "mock-cw-1"
            assert isinstance(body["total_elapsed_ms"], int) and body["total_elapsed_ms"] >= 0
            assert isinstance(body["variants"], list)
            assert len(body["variants"]) == 3

            for i, v in enumerate(body["variants"]):
                assert v["index"] == i
                assert "text" in v
                assert "tokens" in v
                assert "elapsed_ms" in v and isinstance(v["elapsed_ms"], int)
                # mock provider 无脚本 → 回显 "{}"（参考 test_model_router.py:36-41）
                assert v["text"] == "{}"
                assert v["error"] is None

    asyncio.run(run())


def test_continue_default_num_variants_is_three(tmp_path: Path):
    """body 不传 num_variants → 默认 3 个 variant。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={},
            )
            assert r.status_code == 200, r.text
            assert len(r.json()["variants"]) == 3

    asyncio.run(run())


def test_continue_num_variants_one_returns_single(tmp_path: Path):
    """num_variants=1 → 仅 1 个 variant（index=0，温度 0.8）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 1},
            )
            assert r.status_code == 200, r.text
            variants = r.json()["variants"]
            assert len(variants) == 1
            assert variants[0]["index"] == 0

    asyncio.run(run())


def test_continue_num_variants_zero_returns_422(tmp_path: Path):
    """num_variants=0 → 422（pydantic ge=1）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 0},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


def test_continue_num_variants_four_returns_422(tmp_path: Path):
    """num_variants=4 → 422（pydantic le=3）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 4},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


def test_continue_unknown_project_returns_404(tmp_path: Path):
    """project 不存在 → 404（先于 chapter 校验）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/projects/prj_nope/chapters/ch_nope/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 404, r.text
            assert "project" in r.json()["detail"]

    asyncio.run(run())


def test_continue_unknown_chapter_returns_404(tmp_path: Path):
    """project 存在但 chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/ch_nope/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 404, r.text
            assert "chapter" in r.json()["detail"]

    asyncio.run(run())


def test_continue_chapter_from_other_project_returns_404(tmp_path: Path):
    """chapter 存在但 project_id 不匹配 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, name="A")
            pid_b = await _make_project(app, name="B")
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid_a, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid_b}/chapters/{cid}/continue",
                json={"num_variants": 1},
            )
            assert r.status_code == 404, r.text
            assert "does not belong" in r.json()["detail"]

    asyncio.run(run())


def test_continue_planned_status_returns_409(tmp_path: Path):
    """chapter.status = PLANNED → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid)  # 默认 PLANNED

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "PLANNED" in detail
            assert "not continuable" in detail

    asyncio.run(run())


def test_continue_committed_status_returns_409(tmp_path: Path):
    """chapter.status = COMMITTED → 409（白名单外）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            for nxt in ["REVIEWED", "COMMITTED"]:
                r = await _request(
                    app, "PATCH", f"/api/chapters/{cid}", json={"status": nxt}
                )
                assert r.status_code == 200, r.text

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 409, r.text
            assert "COMMITTED" in r.json()["detail"]

    asyncio.run(run())


def test_continue_reviewed_status_is_allowed(tmp_path: Path):
    """chapter.status = REVIEWED → 200（DRAFTED/REVIEWED 均允许）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"status": "REVIEWED"}
            )
            assert r.status_code == 200, r.text

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 200, r.text
            assert len(r.json()["variants"]) == 2

    asyncio.run(run())


def test_continue_without_any_draft_uses_empty_context(tmp_path: Path):
    """chapter 还没 draft → 上文视为空，prompt 仍能构造，200。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(
                app,
                pid,
                status="DRAFTED",
                plan_json={"key_beats": {"purpose": "开篇立人设"}},
            )

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 200, r.text
            assert len(r.json()["variants"]) == 2

    asyncio.run(run())


def test_continue_tolerates_missing_purpose_in_plan(tmp_path: Path):
    """plan_json.key_beats.purpose 缺失/格式异常时优雅降级（仍 200）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            # key_beats 是 list[dict] 但缺 purpose；purpose 为 None 等
            cid = await _make_chapter(
                app,
                pid,
                status="DRAFTED",
                plan_json={"key_beats": [{"foo": "bar"}, {"purpose": ""}]},
            )

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 200, r.text

    asyncio.run(run())


def test_continue_without_model_config_returns_502(tmp_path: Path):
    """未配 creative_writing 模型时 3 次全部失败 → 502。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            # 不调 _ensure_mock_writer：creative_writing 无 enabled config
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue",
                json={"num_variants": 2},
            )
            assert r.status_code == 502, r.text
            body = r.json()
            assert "all variants failed" in body["detail"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# adopt 端点
# ---------------------------------------------------------------------------


def test_adopt_appends_to_latest_draft(tmp_path: Path):
    """adopt 成功：新 draft version=max+1、content = 旧 + "\n" + 新。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            await _seed_draft(app, cid, content="v1 旧内容")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "采纳片段正文"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["version"] == 2
            assert body["status"] == "DRAFTED"
            assert body["appended_chars"] == len("采纳片段正文")

            # 校验 drafts 拼接内容
            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert r.status_code == 200, r.text
            drafts = r.json()
            assert [d["version"] for d in drafts] == [2, 1]
            assert drafts[0]["content"] == "v1 旧内容\n采纳片段正文"
            assert drafts[1]["content"] == "v1 旧内容"

    asyncio.run(run())


def test_adopt_first_version_when_no_draft_yet(tmp_path: Path):
    """尚无 draft 时 adopt → version=1、content 仅新内容（无前导换行）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "首段正文"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["version"] == 1
            assert body["status"] == "DRAFTED"
            assert body["appended_chars"] == len("首段正文")

            r = await _request(app, "GET", f"/api/chapters/{cid}/drafts")
            assert r.status_code == 200, r.text
            drafts = r.json()
            assert len(drafts) == 1
            assert drafts[0]["version"] == 1
            assert drafts[0]["content"] == "首段正文"
            # 无前导换行
            assert not drafts[0]["content"].startswith("\n")

    asyncio.run(run())


def test_adopt_reviewed_status_downgrades_to_drafted(tmp_path: Path):
    """REVIEWED → 采纳后降级为 DRAFTED。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"status": "REVIEWED"}
            )
            assert r.status_code == 200
            assert r.json()["status"] == "REVIEWED"
            await _seed_draft(app, cid, content="审过稿")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "采纳后的补充"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "DRAFTED"  # 降级生效
            assert body["version"] == 2

            # DB 直查确认
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

    asyncio.run(run())


def test_adopt_drafted_status_unchanged(tmp_path: Path):
    """DRAFTED → 采纳后 status 保持 DRAFTED（不误升不误降）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")
            await _seed_draft(app, cid, content="初稿")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "补丁"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "DRAFTED"

    asyncio.run(run())


def test_adopt_empty_content_returns_422(tmp_path: Path):
    """adopt content 空字符串 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": ""},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


def test_adopt_over_max_length_returns_422(tmp_path: Path):
    """adopt content 长度超过 20000 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid, status="DRAFTED")

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "x" * 20001},
            )
            assert r.status_code == 422, r.text

            # 边界：恰好 20000 字符应通过
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "y" * 20000},
            )
            assert r.status_code == 200, r.text

    asyncio.run(run())


def test_adopt_planned_status_returns_409(tmp_path: Path):
    """chapter.status = PLANNED → adopt 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)
            cid = await _make_chapter(app, pid)  # 默认 PLANNED

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/continue/adopt",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "PLANNED" in r.json()["detail"]

    asyncio.run(run())


def test_adopt_unknown_project_returns_404(tmp_path: Path):
    """adopt project 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/projects/prj_nope/chapters/ch_nope/continue/adopt",
                json={"content": "x"},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())


def test_adopt_unknown_chapter_returns_404(tmp_path: Path):
    """adopt chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _ensure_mock_writer(app)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/ch_nope/continue/adopt",
                json={"content": "x"},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())
