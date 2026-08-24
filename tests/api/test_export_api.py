"""export API 集成测试（V1.4 / Sprint 16）。

覆盖（任务书给死）：
- GET /api/projects/{pid}/export?format=txt → 200 + UTF-8 BOM + Content-Disposition
- GET /api/projects/{pid}/export?format=docx → 200 + 合法 docx
- GET /api/projects/{pid}/export?format=fanqie → 200 + 分隔线 + 大纲
- 单章导出（?chapter_no=）
- 项目不存在 → 404
- 非法 format → 400
- 空项目 → 200（fanqie 仅大纲占位）

测试模式：httpx.ASGITransport + ``Settings(data_dir=tmp_path)`` 拉临时 db。
drafts 直接 INSERT 跳过 ``create_draft`` 状态机校验（与 test_quality.py 一致）。
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
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


async def _make_project(app, name: str = "导出项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter_with_draft(
    app,
    pid: str,
    number: int,
    title: str,
    content: str,
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
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, 1, ?, 'agent:writer:v1', NULL, NULL, ?)
            """,
            (new_id("dr"), cid, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# txt / docx / fanqie happy paths
# ---------------------------------------------------------------------------


def test_export_txt_happy_path(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "整书txt")
            await _make_chapter_with_draft(app, pid, 1, "起", "第一段起")
            await _make_chapter_with_draft(app, pid, 2, "承", "第二段承")

            r = await _request(
                app, "GET", f"/api/projects/{pid}/export", params={"format": "txt"}
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "text/plain; charset=utf-8"
            cd = r.headers["content-disposition"]
            assert cd.startswith("attachment;")
            assert "filename=" in cd
            assert r.content.startswith(b"\xef\xbb\xbf")
            text = r.content.decode("utf-8-sig")
            assert "整书txt" in text
            assert "第1章 起" in text
            assert "第2章 承" in text
            # 章节按 number 升序
            assert text.index("第1章") < text.index("第2章")

    asyncio.run(run())


def test_export_docx_is_legal_zip_with_body(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "整书docx")
            await _make_chapter_with_draft(app, pid, 1, "起", "第一段起<尖>")

            r = await _request(
                app, "GET", f"/api/projects/{pid}/export", params={"format": "docx"}
            )
            assert r.status_code == 200, r.text
            ct = r.headers["content-type"]
            assert "officedocument.wordprocessingml" in ct
            assert zipfile.is_zipfile(io.BytesIO(r.content))
            z = zipfile.ZipFile(io.BytesIO(r.content))
            doc = z.read("word/document.xml").decode("utf-8")
            assert "第一段起" in doc
            # 特殊字符必须转义
            assert "&lt;尖&gt;" in doc

    asyncio.run(run())


def test_export_fanqie_includes_outline_section(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "番茄测试")
            cid1 = await _make_chapter_with_draft(app, pid, 1, "起", "字" * 5000)
            cid2 = await _make_chapter_with_draft(app, pid, 2, "承", "字" * 5000)
            # 给 chapter 1 写一个 plan
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (
                        json.dumps(
                            {
                                "chapter_goal": "主角觉醒",
                                "core_conflict": "灵根被封",
                                "key_beats": [{"purpose": "夜入禁地"}],
                            },
                            ensure_ascii=False,
                        ),
                        cid1,
                    ),
                )
                conn.execute(
                    "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                    (
                        json.dumps({"chapter_goal": "初次试炼"}, ensure_ascii=False),
                        cid2,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "GET", f"/api/projects/{pid}/export", params={"format": "fanqie"}
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "text/plain; charset=utf-8"
            text = r.content.decode("utf-8-sig")
            assert "========== 故事大纲 ==========" in text
            head, _, outline = text.partition("==========")
            # 正文部分：含至少一个章节标题
            assert "第1章 起" in head
            # 大纲部分：含两章 plan 渲染
            assert "目标：主角觉醒" in outline
            assert "目标：初次试炼" in outline

    asyncio.run(run())


def test_export_single_chapter_txt(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "单章")
            await _make_chapter_with_draft(app, pid, 1, "一", "一正文")
            await _make_chapter_with_draft(app, pid, 2, "二", "二正文")
            await _make_chapter_with_draft(app, pid, 3, "三", "三正文")

            r = await _request(
                app,
                "GET",
                f"/api/projects/{pid}/export",
                params={"format": "txt", "chapter_no": 2},
            )
            assert r.status_code == 200, r.text
            text = r.content.decode("utf-8-sig")
            assert "第2章 二" in text
            assert "二正文" in text
            assert "第1章" not in text
            assert "第3章" not in text
            # 文件名含 -ch2
            cd = r.headers["content-disposition"]
            assert "-ch2" in cd

    asyncio.run(run())


def test_export_single_chapter_docx(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "单章docx")
            await _make_chapter_with_draft(app, pid, 1, "一", "一正文")
            await _make_chapter_with_draft(app, pid, 2, "二", "二正文")

            r = await _request(
                app,
                "GET",
                f"/api/projects/{pid}/export",
                params={"format": "docx", "chapter_no": 1},
            )
            assert r.status_code == 200, r.text
            assert zipfile.is_zipfile(io.BytesIO(r.content))
            doc = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode("utf-8")
            assert "一正文" in doc
            assert "二正文" not in doc

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_export_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "GET", "/api/projects/prj_nope/export", params={"format": "txt"}
            )
            assert r.status_code == 404
            assert "not found" in r.json()["detail"]

    asyncio.run(run())


def test_export_400_for_unsupported_format(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "非法格式")
            for fmt in ("pdf", "", "docXX"):
                r = await _request(
                    app, "GET", f"/api/projects/{pid}/export", params={"format": fmt}
                )
                assert r.status_code == 400, (fmt, r.text)
                assert "format" in r.json()["detail"].lower()

    asyncio.run(run())


def test_export_empty_project_returns_200(tmp_path: Path):
    """空项目：txt/docx 仅项目名；fanqie 仅大纲占位。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "空书")
            for fmt in ("txt", "docx", "fanqie"):
                r = await _request(
                    app, "GET", f"/api/projects/{pid}/export", params={"format": fmt}
                )
                assert r.status_code == 200, (fmt, r.text)
                if fmt == "fanqie":
                    text = r.content.decode("utf-8-sig")
                    assert "========== 故事大纲 ==========" in text
                    assert "（暂无）" in text
                if fmt == "docx":
                    assert zipfile.is_zipfile(io.BytesIO(r.content))

    asyncio.run(run())


def test_export_filename_uses_utf8_in_content_disposition(tmp_path: Path):
    """文件名走 RFC 5987：ASCII 兜底 + filename*=UTF-8''…"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "番茄书")  # 名字是中文
            await _make_chapter_with_draft(app, pid, 1, "起", "正文")
            r = await _request(
                app, "GET", f"/api/projects/{pid}/export", params={"format": "txt"}
            )
            assert r.status_code == 200
            cd = r.headers["content-disposition"]
            assert cd.startswith("attachment;")
            assert "filename=" in cd  # ASCII 兜底
            assert "filename*=UTF-8''" in cd  # UTF-8 写法
            # 中文经 URL 编码后含 %E8%8C%84（番）等
            assert "%" in cd

    asyncio.run(run())
