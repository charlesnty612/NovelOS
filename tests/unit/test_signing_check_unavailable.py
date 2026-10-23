"""signing_check「读题材包失败 ⇒ 显式不可检标记」回归测试（Task 6 / D-13）。

背景：``_load_project_opening_rules`` 读题材包失败时 ``except Exception: return None``
⇒ 题材开篇检查**整段静默消失**、无告警、无 errors 字段 ⇒ 体检报告外观「全部通过」
（实际未检）。同文件 ``word_band_rejected`` 已有显式标记 ⇒ 处置不一致。

覆盖：

1. 读题材包失败 ⇒ 结果里出现**显式不可检标记**（``genre_opening_unavailable``）；
2. 报告**不得**看起来「全部通过」（存在非 pass 的显式条目 + summary 计数反映）；
3. 摘要渲染（``format_summary``）里能看到该标记；
4. 未绑定题材包（正常缺席）⇒ **不**出现该标记（不得过度触发）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.signing_check.service import format_summary, run_signing_check


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _seed_project(db_path: Path, *, name: str = "体检书", genre_pack_id: str | None = None) -> str:
    """种一个 project + 一章正文（体检要逐章取正文）。"""
    now = now_iso()
    pid = new_id("prj")
    cid = new_id("ch")
    conn = get_connection(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO projects(project_id,name,genre,target_words,status,genre_pack_id,
            created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (pid, name, "科幻", 100_000, "ACTIVE", genre_pack_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
            visibility,who_knows,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (cid, pid, 1, "第一章", "{}", "COMMITTED", "VISIBLE", None, now, now),
    )
    conn.execute(
        """
        INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
            prompt_version,model_id,created_at)
        VALUES (?,?,?,?,?,NULL,NULL,?)
        """,
        (new_id("dr"), cid, 1, "林澈推开门，屋里没有人。", "writer", now),
    )
    conn.commit()
    conn.close()
    return pid


def _break_genre_pack_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """让「读项目题材包绑定」抛异常（模拟缺表 / 读库失败）。"""

    def _boom(self, project_id: str):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("no such table: genre_packs")

    monkeypatch.setattr(
        "packages.core.signing_check.service.GenrePackService.get_project_binding",
        _boom,
    )


class TestGenreOpeningUnavailable:
    def test_explicit_marker_present_on_read_failure(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        assert result.get("genre_opening_unavailable") is True, (
            "读题材包失败时结果里必须出现显式不可检标记"
        )
        assert result.get("genre_opening_error"), "必须带上失败原因（可排查）"

    def test_report_does_not_look_all_pass(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """报告外观不得是「全部通过」——必须有一条非 pass 的显式条目。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        marked = [it for it in result["items"] if it["key"] == "genre_opening_unavailable"]
        assert marked, f"结果里没有不可检条目：{[it['key'] for it in result['items']]}"
        assert marked[0]["level"] != "pass", "不可检条目不得是 pass（否则外观仍是全通过）"

        summary = result["summary"]
        assert summary["pass_count"] < len(result["items"]) or summary["info_count"] > 0, (
            f"summary 仍是「全部通过」：{summary}"
        )

    def test_summary_text_shows_marker(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """摘要渲染（投稿包内嵌段）里能看到该标记。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)
        text = format_summary(result)

        assert "genre_opening_unavailable" in text
        assert "不可检" in text

    def test_no_marker_when_genre_pack_read_succeeds(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """读成功（未绑定题材包）⇒ **不**出现该标记（不得过度触发）。"""
        pid = _seed_project(db_path)

        def _unbound(self, project_id: str):  # noqa: ANN001, ANN202
            return None

        monkeypatch.setattr(
            "packages.core.signing_check.service.GenrePackService.get_project_binding",
            _unbound,
        )

        result = run_signing_check(str(db_path), pid)

        assert "genre_opening_unavailable" not in result
        assert not [it for it in result["items"] if it["key"] == "genre_opening_unavailable"]


# ---------------------------------------------------------------------------
# API 契约（2026-10-23 返工 · concern 3）：两个新键必须**进响应模型**
# ---------------------------------------------------------------------------


def test_api_response_top_level_carries_unavailable_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /projects/{pid}/signing-check`` 的**响应体顶层**必须出现两个新键。

    ⚠️ **改造前的真实失效形态**（不是假想）：``run_signing_check`` 返回的 dict 里
    **确实有** ``genre_opening_unavailable`` / ``genre_opening_error``，但它们**不在**
    :class:`packages.core.api.routers.signing_check.SigningCheckResult` 模型里 ⇒
    FastAPI 的 ``response_model_exclude_unset=True`` 把**未声明的键过滤掉** ⇒
    API 消费者拿到的响应体里**根本没有**这两个键 ⇒ 「题材开篇检查没跑」这件事
    在 API 侧**完全不可见**（只有 service 内部调用者看得到）。

    ⇒ 断言必须打在**响应体顶层**（``body["genre_opening_unavailable"]``），
    **不是** ``items`` 里的那条同名条目（那条改造前就在，属于另一个断言面）。

    反向不变量：读题材包正常（未绑定题材包）⇒ 两个键**缺席**
    （``response_model_exclude_unset=True`` 的语义：缺席 ≠ null）。
    """
    import asyncio

    from tests.api._shared import _create_app, _make_client

    app = _create_app(tmp_path)

    async def run() -> dict:
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                r = await client.post("/api/projects", json={"name": "不可检 API 项目"})
                assert r.status_code == 201, r.text
                pid = r.json()["project_id"]
                cr = await client.post(
                    f"/api/projects/{pid}/chapters",
                    json={"number": 1, "title": "第一章"},
                )
                assert cr.status_code == 201, cr.text
                cid = cr.json()["chapter_id"]
                conn = get_connection(app.state.settings.db_path)
                try:
                    conn.execute(
                        """
                        INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
                            prompt_version,model_id,created_at)
                        VALUES (?,?,?,?,?,NULL,NULL,?)
                        """,
                        (new_id("dr"), cid, 1, "林澈推开门，屋里没有人。", "writer", now_iso()),
                    )
                    conn.commit()
                finally:
                    conn.close()

                # ① 对照组：读题材包正常（未绑定）⇒ 两个键**缺席**
                ok = await client.get(f"/api/projects/{pid}/signing-check")
                assert ok.status_code == 200, ok.text
                ok_body = ok.json()
                assert "genre_opening_unavailable" not in ok_body, ok_body
                assert "genre_opening_error" not in ok_body, ok_body

                # ② 故障注入：读题材包失败 ⇒ 两个键必须在**响应体顶层**
                _break_genre_pack_read(monkeypatch)
                bad = await client.get(f"/api/projects/{pid}/signing-check")
                assert bad.status_code == 200, bad.text
                return bad.json()

    body = asyncio.run(run())

    assert "genre_opening_unavailable" in body, (
        f"响应体顶层没有 genre_opening_unavailable：{sorted(body)}"
    )
    assert body["genre_opening_unavailable"] is True, body
    assert "genre_opening_error" in body, (
        f"响应体顶层没有 genre_opening_error：{sorted(body)}"
    )
    assert body["genre_opening_error"], body


def test_openapi_schema_declares_the_two_new_keys() -> None:
    """OpenAPI schema 里 ``SigningCheckResult`` 必须声明这两个键（codegen 输入面）。

    判别：把模型里的两个字段删掉 ⇒ 本用例红（schema 里就没了）⇒ 前端类型也不会
    再生成它们 ⇒ API 消费者失去编译期提示。
    """
    import json
    from pathlib import Path as _Path

    schema_path = _Path(__file__).resolve().parents[2] / "apps" / "web" / "openapi.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    props = schema["components"]["schemas"]["SigningCheckResult"]["properties"]

    assert "genre_opening_unavailable" in props, sorted(props)
    assert "genre_opening_error" in props, sorted(props)
