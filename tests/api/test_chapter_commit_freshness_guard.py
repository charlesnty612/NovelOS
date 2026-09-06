"""chapter-commit 时序守卫（draft 晚于最近一次 COMPLETED review）API 测试。

覆盖（任务书给死）：
- REVIEWED 章节 + COMPLETED review run（ended_at=T1）+ 更新草稿（created_at=T2>T1）
  → POST commit 返回 409，detail 含「未审」；
- 草稿 created_at < review ended_at → 不被本守卫拦；
- 无任何 COMPLETED review → 不拦（既有行为，走到既有校验）；
- pipeline 兜底：章节 DRAFTED 时本守卫放行（threshold 为 None），走到既有 REVIEWED 校验。

测试模式与 ``tests/api/test_workflow_cancel.py`` 一致：httpx.ASGITransport +
``Settings(data_dir=tmp_path)`` 拉临时 db；不跑完整 workflow，直接 SQL 注入 drafts +
workflow_runs 行，聚焦守卫行为。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project_and_chapter(
    app, *, chapter_status: str = "REVIEWED"
) -> tuple[str, str]:
    """建项目 + 章节，并把章节推到目标状态（默认 REVIEWED）。"""
    r = await _request(app, "POST", "/api/projects", json={"name": "freshness guard"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "guard chapter"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]

    if chapter_status != "PLANNED":
        transitions = ["DRAFTED"] + ([chapter_status] if chapter_status != "DRAFTED" else [])
        for nxt in transitions:
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"status": nxt}
            )
            assert r.status_code == 200, r.text
    return pid, cid


def _ensure_workflow(conn, name: str) -> str:
    """确保 workflows 表有 name 行，返回 workflow_id。镜像 test_workflow_cancel.py。"""
    row = conn.execute(
        "SELECT workflow_id FROM workflows WHERE name = ?", (name,)
    ).fetchone()
    if row is not None:
        return row["workflow_id"]
    wf_id = new_id("wf")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)
        VALUES (?, ?, 'v1', '{}', ?, ?)
        """,
        (wf_id, name, now, now),
    )
    return wf_id


def _insert_review_run(conn, *, chapter_id: str, status: str, ended_at: str | None) -> str:
    """插入 chapter-review workflow_runs 行；COMPLETED 必传 ended_at。"""
    wf_id = _ensure_workflow(conn, "chapter-review")
    rid = new_id("wfr")
    started = now_iso()
    conn.execute(
        """
        INSERT INTO workflow_runs
            (run_id, workflow_id, chapter_id, status, current_node,
             checkpoint_json, error, retry_count, started_at, ended_at)
        VALUES (?, ?, ?, ?, NULL, '{}', NULL, 0, ?, ?)
        """,
        (rid, wf_id, chapter_id, status, started, ended_at),
    )
    return rid


def _insert_draft(
    conn, *, chapter_id: str, version: int, content: str, created_at: str
) -> str:
    """直接 INSERT drafts 行（绕过 create_draft 状态机）；测试专用。"""
    dr_id = new_id("dr")
    conn.execute(
        """
        INSERT INTO drafts
            (draft_id, chapter_id, version, content, created_by,
             prompt_version, model_id, created_at)
        VALUES
            (:draft_id, :chapter_id, :version, :content, :created_by,
             :prompt_version, :model_id, :created_at)
        """,
        {
            "draft_id": dr_id,
            "chapter_id": chapter_id,
            "version": version,
            "content": content,
            "created_by": "test:guard",
            "prompt_version": None,
            "model_id": None,
            "created_at": created_at,
        },
    )
    return dr_id


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_commit_blocked_when_draft_created_after_completed_review(tmp_path: Path):
    """核心用例：草稿 created_at > 最近一次 COMPLETED review ended_at → 409。

    模拟「审校完成 v5 之后，又人工改出 v6，现在直接 commit v6」——守卫必须拒收。
    """
    app = _create_app(tmp_path)

    async def _run():
        async with app.router.lifespan_context(app):
            pid, cid = await _make_project_and_chapter(app, chapter_status="REVIEWED")

            # SQL 注入：review 已 COMPLETED（ended_at=T1）+ 新草稿（created_at=T2 > T1）
            db_path = app.state.settings.db_path
            review_ended = "2025-01-01T10:00:00+00:00"
            draft_created = "2025-01-01T11:00:00+00:00"
            conn = get_connection(db_path)
            try:
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED", ended_at=review_ended
                )
                _insert_draft(
                    conn, chapter_id=cid, version=6, content="v6 after review",
                    created_at=draft_created,
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={},
            )
            assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
            detail = r.json().get("detail", "")
            assert "未审" in detail, f"detail 应含「未审」：{detail}"
            assert "v6" in detail, f"detail 应含版本号 v6：{detail}"
            assert review_ended in detail, f"detail 应含 review 结束时间：{detail}"

    asyncio.run(_run())


def test_commit_allowed_when_draft_created_before_completed_review(tmp_path: Path):
    """对照：草稿 created_at < review ended_at → 本守卫不拦（行为与现状一致）。"""
    app = _create_app(tmp_path)

    async def _run():
        async with app.router.lifespan_context(app):
            pid, cid = await _make_project_and_chapter(app, chapter_status="REVIEWED")

            review_ended = "2025-01-01T11:00:00+00:00"
            draft_created = "2025-01-01T10:00:00+00:00"  # 早于 review 结束
            conn = get_connection(app.state.settings.db_path)
            try:
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED", ended_at=review_ended
                )
                _insert_draft(
                    conn, chapter_id=cid, version=5, content="v5 before review",
                    created_at=draft_created,
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={},
            )
            # 守卫放行；后续 _start_workflow 仍可能因缺前置（state 等）失败，
            # 但响应码不会是 409「未审」；允许 201（启动 RUNNING）或 4xx/5xx 其它校验。
            # 关键是 detail 不含「未审」。
            if r.status_code == 409:
                detail = r.json().get("detail", "")
                assert "未审" not in detail, (
                    f"守卫不应拦本用例：draft 早于 review 结束。实际 detail={detail}"
                )

    asyncio.run(_run())


def test_commit_no_completed_review_unchanged_behavior(tmp_path: Path):
    """无任何 COMPLETED review run → 本守卫放行；走既有 REVIEWED 校验逻辑。"""
    app = _create_app(tmp_path)

    async def _run():
        async with app.router.lifespan_context(app):
            pid, cid = await _make_project_and_chapter(app, chapter_status="REVIEWED")

            # 不插 workflow_runs 行；只有一个草稿
            conn = get_connection(app.state.settings.db_path)
            try:
                _insert_draft(
                    conn, chapter_id=cid, version=1, content="v1",
                    created_at="2025-01-01T09:00:00+00:00",
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={},
            )
            # 本守卫放行；后续响应可以是 201（启动 RUNNING）或 4xx 其它校验。
            # 关键是 detail 不含「未审」。
            if r.status_code == 409:
                detail = r.json().get("detail", "")
                assert "未审" not in detail, (
                    f"无 COMPLETED review 时本守卫不应拦：实际 detail={detail}"
                )

    asyncio.run(_run())


def test_commit_multiple_reviews_uses_latest_completed(tmp_path: Path):
    """多次 review 时取最近一次 COMPLETED 决定的 ended_at 作阈值。"""
    app = _create_app(tmp_path)

    async def _run():
        async with app.router.lifespan_context(app):
            pid, cid = await _make_project_and_chapter(app, chapter_status="REVIEWED")

            # 第 1 轮 review COMPLETED（早）+ 第 2 轮 review COMPLETED（晚）
            conn = get_connection(app.state.settings.db_path)
            try:
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED",
                    ended_at="2025-01-01T10:00:00+00:00",
                )
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED",
                    ended_at="2025-01-01T12:00:00+00:00",  # 最近一次
                )
                # 草稿在两轮 review 之间（早于最近一次）→ 应被本守卫放行
                _insert_draft(
                    conn, chapter_id=cid, version=5, content="v5 mid-review",
                    created_at="2025-01-01T11:00:00+00:00",
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={},
            )
            if r.status_code == 409:
                detail = r.json().get("detail", "")
                assert "未审" not in detail, (
                    f"草稿早于最近一次 review 完成时本守卫不应拦：detail={detail}"
                )

    asyncio.run(_run())


def test_commit_failed_review_does_not_set_threshold(tmp_path: Path):
    """FAILED 状态的 review run 不计入阈值（只有 COMPLETED 才算「已审完」）。"""
    app = _create_app(tmp_path)

    async def _run():
        async with app.router.lifespan_context(app):
            pid, cid = await _make_project_and_chapter(app, chapter_status="REVIEWED")

            conn = get_connection(app.state.settings.db_path)
            try:
                # review FAILED 不计入阈值；不存在 COMPLETED review → 守卫放行
                _insert_review_run(
                    conn, chapter_id=cid, status="FAILED",
                    ended_at="2025-01-01T10:00:00+00:00",
                )
                _insert_draft(
                    conn, chapter_id=cid, version=6, content="v6 after failed review",
                    created_at="2025-01-01T11:00:00+00:00",
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={},
            )
            if r.status_code == 409:
                detail = r.json().get("detail", "")
                assert "未审" not in detail, (
                    f"FAILED review 不应作为阈值：detail={detail}"
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# P3-2：helper 直接调用级单元断言（钉死守卫逻辑本身的输入→输出）
# ---------------------------------------------------------------------------
#
# 上面 5 个用例只断言 detail 不含「未审」——黑盒且依赖完整 router 链路；万一守卫 SQL
# 写错或被改成对所有 review run 生效，黑盒用例也会「detail 不含未审」（被卡在上游
# 校验）而看不出 helper 内部行为漂移。直接调 helper 验证返回值能钉死底层 SQL 逻辑：
# - 多次 review（status=COMPLETED）→ 取 ended_at 最新一次；
# - FAILED / RUNNING / PENDING review → 不计入；
# - 无 COMPLETED review → None；
# - drafts.created_at > threshold → 返回最新一行（version / created_at）；
# - drafts.created_at <= threshold → None；
# - 多草稿时按 created_at DESC 取首条。
# 这些断言与 5 个端点用例互补，确保「守卫 SQL/排序/过滤」与「端点行为」两层都被锁住。


def test_latest_completed_review_end_returns_latest_completed(tmp_path: Path):
    """多次 review 时取 ended_at 最近一次；FAILED 不计入；无 review → None。"""
    from packages.core.api.routers.workflows import _latest_completed_review_end

    db_path = _create_app(tmp_path).state.settings.db_path
    # 需要至少一个 chapter 行（_latest_completed_review_end 按 chapter_id 过滤，
    # 空 chapter_id 在生产中不会出现；这里用真实 chapter_id 保证 SQL JOIN 正常）。
    pid = "p_test"
    cid = "c_test"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (pid, "p", now_iso(), now_iso()),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "plan_json, created_at, updated_at) VALUES (?, ?, 1, 't', 'PLANNED', '{}', ?, ?)",
            (cid, pid, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    # 1) 没有任何 review run → None
    assert _latest_completed_review_end(db_path, cid) is None

    # 2) 只有 FAILED review → 仍 None（FAILED 不计入阈值）
    conn = get_connection(db_path)
    try:
        _insert_review_run(conn, chapter_id=cid, status="FAILED", ended_at="2025-01-02T10:00:00+00:00")
        _insert_review_run(conn, chapter_id=cid, status="RUNNING", ended_at=None)
        conn.commit()
    finally:
        conn.close()
    assert _latest_completed_review_end(db_path, cid) is None

    # 3) 加一条 COMPLETED review → 返回该 ended_at
    conn = get_connection(db_path)
    try:
        _insert_review_run(conn, chapter_id=cid, status="COMPLETED", ended_at="2025-01-03T10:00:00+00:00")
        conn.commit()
    finally:
        conn.close()
    assert _latest_completed_review_end(db_path, cid) == "2025-01-03T10:00:00+00:00"

    # 4) 再加一条更晚的 COMPLETED review → 返回最新一次（不是最早的）
    conn = get_connection(db_path)
    try:
        _insert_review_run(conn, chapter_id=cid, status="COMPLETED", ended_at="2025-01-04T12:00:00+00:00")
        conn.commit()
    finally:
        conn.close()
    assert _latest_completed_review_end(db_path, cid) == "2025-01-04T12:00:00+00:00"


def test_latest_draft_after_returns_latest_or_none(tmp_path: Path):
    """drafts.created_at 严格 > threshold 才返回最新一行；多草稿按 created_at DESC 取首条。"""
    from packages.core.api.routers.workflows import _latest_draft_after

    db_path = _create_app(tmp_path).state.settings.db_path
    pid = "p_test"
    cid = "c_test"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (pid, "p", now_iso(), now_iso()),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "plan_json, created_at, updated_at) VALUES (?, ?, 1, 't', 'PLANNED', '{}', ?, ?)",
            (cid, pid, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    # 1) 没有任何 draft → None
    assert _latest_draft_after(db_path, cid, "2025-01-01T00:00:00+00:00") is None

    threshold = "2025-01-01T10:00:00+00:00"
    conn = get_connection(db_path)
    try:
        # 2) 一条早于 threshold 的 draft → None（严格 > threshold）
        _insert_draft(conn, chapter_id=cid, version=1, content="early", created_at="2025-01-01T09:00:00+00:00")
        # 3) 一条等于 threshold 的 draft → None（SQL 是 >，不包含等号）
        _insert_draft(conn, chapter_id=cid, version=2, content="equal", created_at=threshold)
        # 4) 一条晚于 threshold 的 draft → 返回
        _insert_draft(conn, chapter_id=cid, version=3, content="late", created_at="2025-01-01T11:00:00+00:00")
        # 5) 一条更晚的 draft → 返回「最新」那条
        _insert_draft(conn, chapter_id=cid, version=4, content="latest", created_at="2025-01-01T12:00:00+00:00")
        conn.commit()
    finally:
        conn.close()

    result = _latest_draft_after(db_path, cid, threshold)
    assert result is not None, "存在 created_at > threshold 的 draft 时应返回非 None"
    assert result["version"] == 4, (
        f"应返回 created_at 最大的那条（version=4），实际 version={result['version']}"
    )
    assert result["created_at"] == "2025-01-01T12:00:00+00:00", (
        f"created_at 应为 '2025-01-01T12:00:00+00:00'，实际 {result['created_at']!r}"
    )

    # 6) 把 threshold 推到所有草稿之后 → 应回到 None（无更新草稿）
    assert _latest_draft_after(db_path, cid, "2026-01-01T00:00:00+00:00") is None
