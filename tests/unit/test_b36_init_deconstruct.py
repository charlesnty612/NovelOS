"""批次三十六 · 批 7（project_init / deconstruct 域）：C-1 / C-9 / C-10 / C-23 判别用例。

四条各自独立的失效方向：

* **C-1** ``project_init`` 挂载模式重跑时按卷 ``DELETE`` 全部章节、无状态过滤
  ⇒ 「只有草稿未提交」的章连同 ``drafts`` 与 ``outline_json`` 策展槽静默消失。
  修法 = 重建事务内、任何 ``DELETE`` 之前 **fail-closed 预检**：只允许重建
  ``status='PLANNED'`` 且无 ``drafts`` 行的章；其余抛显式异常、零删除。
  判别：有草稿 / 非 PLANNED 的卷重跑 ⇒ ``ValueError`` + 章节/草稿/大纲逐行不变。
* **C-9 / C-10** deconstruct 起跑不带 ``project_id``（run 归属列恒 NULL ⇒ 每个
  项目都看到别家的拆书 run）且无项目级并发守卫（同项目双跑互不拦截）。
  判别：run 行归属列 = 发起项目、只出现在该项目列表；同项目已有活跃 run ⇒ 409，
  跨项目不受影响。
* **C-23** deconstruct 同步起跑占 HTTP 线程（T2 逐章串行 ``run_agent``）。
  修法 = ``start_with_nodes_async``（受理语义 3 键、status 恒 RUNNING）。
  判别：端点**只调用** async 版；同步方法被调用即红。

⚠️ 所有断言打在**真实值**上（DB 实际行 / 实际异常消息 / 端点实际状态码），
临时库 + 合成夹具，零真实 LLM 调用，不碰 ``data/novelos.db``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.project.models import ProjectCreate
from packages.domain.project.service import ProjectService
from packages.workflows.project_init.pipeline import _persist_all_node
from tests._db_fixtures import migrated_db

# ---------------------------------------------------------------------------
# 夹具（临时库 + 合成数据）
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    return str(migrated_db(tmp_path))


def _project(db_path: str, name: str = "批三十六 批7") -> str:
    return ProjectService(db_path).create(ProjectCreate(name=name))["project_id"]


def _outline() -> dict:
    return {
        "volume": {"number": 1, "title": "第一卷", "arc_summary": "卷纲摘要"},
        "chapter_seeds": [
            {
                "number": 1,
                "title": "第一章",
                "role": "setup",
                "one_sentence": "开端",
                "expected_word_count": 1000,
                "key_beats": [],
            },
            {
                "number": 2,
                "title": "第二章",
                "role": "escalation",
                "one_sentence": "推进",
                "expected_word_count": 1000,
                "key_beats": [],
            },
        ],
    }


def _persist(db_path: str, pid: str, outline: dict | None = None) -> dict:
    """直接调 persist 节点（不起 workflow、零 AI 节点、零 LLM）。"""
    return _persist_all_node(
        {
            "db_path": db_path,
            "project_id": pid,
            # 只选 outline ⇒ premise 环节视为「本次未跑」，跳过项目 premise 回写。
            "selected_stages": ["outline"],
            "outline_output": outline or _outline(),
            "chapter_seed_count": 2,
            "chapter_word_count": 1000,
        }
    )


def _volume_chapters(db_path: str, volume_id: str) -> list[dict]:
    conn = get_connection(db_path)
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT chapter_id, number, status, outline_json FROM chapters "
                "WHERE volume_id = ? ORDER BY number",
                (volume_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _add_draft(db_path: str, chapter_id: str, content: str = "作者写了一半的草稿") -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, ?, 'human', ?)",
            (new_id("drf"), chapter_id, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _draft_rows(db_path: str, chapter_id: str) -> list[dict]:
    conn = get_connection(db_path)
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT draft_id, version, content FROM drafts WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# C-1：重跑初始化不得静默删除未提交内容
# ---------------------------------------------------------------------------


def test_init_rebuild_refuses_drafted_chapter_and_deletes_nothing(tmp_path: Path):
    """最危险的形态：章还是 ``PLANNED``（看着可重建），但已有作者草稿。"""
    db = _db(tmp_path)
    pid = _project(db)
    first = _persist(db, pid)
    old_ids = first["chapter_ids"]
    assert len(old_ids) == 2

    _add_draft(db, old_ids[0], content="这段草稿不能丢")
    before = _volume_chapters(db, first["volume_id"])

    with pytest.raises(ValueError) as exc:
        _persist(db, pid)

    msg = str(exc.value)
    assert "init rebuild blocked" in msg, msg
    assert "第1章" in msg, msg
    assert "有草稿" in msg, msg
    assert "重跑初始化前请先处理这些章" in msg, msg

    # 零删除：章节集合 / 状态 / outline_json 策展槽逐行不变
    after = _volume_chapters(db, first["volume_id"])
    assert after == before
    # 草稿也还在（原实现会先 DELETE drafts）
    drafts = _draft_rows(db, old_ids[0])
    assert len(drafts) == 1
    assert drafts[0]["content"] == "这段草稿不能丢"


@pytest.mark.parametrize("status", ["DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"])
def test_init_rebuild_refuses_non_planned_chapters(tmp_path: Path, status: str):
    """状态过滤：只有 PLANNED 可重建；其余状态（哪怕无草稿）一律拒绝。"""
    db = _db(tmp_path)
    pid = _project(db)
    first = _persist(db, pid)
    old_ids = first["chapter_ids"]

    conn = get_connection(db)
    try:
        conn.execute(
            "UPDATE chapters SET status = ? WHERE chapter_id = ?",
            (status, old_ids[1]),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError) as exc:
        _persist(db, pid)

    msg = str(exc.value)
    assert f"status={status}" in msg, msg
    assert "无草稿" in msg, msg  # 该章确实没有草稿 ⇒ 拦截依据是状态而非草稿
    assert len(_volume_chapters(db, first["volume_id"])) == 2


def test_init_rebuild_replaces_planned_chapters_without_drafts(tmp_path: Path):
    """干净卷照常重建：旧 PLANNED 章被替换、新章落库、数量不变。"""
    db = _db(tmp_path)
    pid = _project(db)
    first = _persist(db, pid)
    old_ids = set(first["chapter_ids"])

    second = _persist(db, pid)

    assert len(second["chapter_ids"]) == 2
    assert set(second["chapter_ids"]).isdisjoint(old_ids)
    rows = _volume_chapters(db, second["volume_id"])
    assert [r["number"] for r in rows] == [1, 2]
    assert {r["status"] for r in rows} == {"PLANNED"}
    assert old_ids.isdisjoint({r["chapter_id"] for r in rows})


# ---------------------------------------------------------------------------
# C-9 / C-10 / C-23：deconstruct 起跑接线（临时库 + 合成夹具）
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path):
    from tests.api._shared import _create_app

    return _create_app(tmp_path)


async def _req(app, method: str, path: str, **kwargs):
    from tests.api._shared import _make_client

    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str) -> str:
    r = await _req(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _wait_terminal(app, run_id: str, *, timeout: float = 60.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = await _req(app, "GET", f"/api/runs/{run_id}")
        if r.status_code == 200:
            last = r.json()
            if last["status"] in {"COMPLETED", "FAILED", "PAUSED", "CANCELLED"}:
                return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内到终态：{last}")


#: 无「第N章」标题 ⇒ T1 立即失败（零模型调用、零网络），只量起跑接线与归属。
_BAD_TEXT = "没有章节标题的正文"


def test_deconstruct_run_is_project_owned_and_project_guarded(tmp_path: Path):
    """C-10：run 归属列 = 发起项目；C-9：同项目活跃 run ⇒ 409，跨项目不受影响。"""
    app = _make_app(tmp_path)

    async def go():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "拆书项目A")
            pid_b = await _make_project(app, "拆书项目B")

            r = await _req(
                app,
                "POST",
                f"/api/projects/{pid_a}/deconstruct",
                json={
                    "book_title": "契约拆书",
                    "text": _BAD_TEXT,
                    "mock_providers": {"deconstructor_chapter": []},
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert set(body) == {"run_id", "status", "current_node"}, body
            assert body["status"] == "RUNNING", body
            run_id = body["run_id"]
            await _wait_terminal(app, run_id)

            # C-10：归属列由引擎写入（chapter_id 仍为 NULL）
            conn = get_connection(app.state.settings.db_path)
            try:
                row = conn.execute(
                    "SELECT project_id, chapter_id FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                wf_id = conn.execute(
                    "SELECT workflow_id FROM workflow_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["workflow_id"]
            finally:
                conn.close()
            assert row["project_id"] == pid_a, dict(row)
            assert row["chapter_id"] is None, dict(row)

            # 只出现在 A 的 run 列表，不出现在 B 的
            ra = await _req(app, "GET", f"/api/projects/{pid_a}/runs")
            rb = await _req(app, "GET", f"/api/projects/{pid_b}/runs")
            assert ra.status_code == 200 and rb.status_code == 200
            assert run_id in {x["run_id"] for x in ra.json()}
            assert run_id not in {x["run_id"] for x in rb.json()}

            # C-9：同项目已有活跃 deconstruct-book run ⇒ 409
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, "
                    "project_id, status, current_node, checkpoint_json, error, "
                    "retry_count, started_at, ended_at, instance_id) "
                    "VALUES ('wfr_b36_active', ?, NULL, ?, 'RUNNING', NULL, '{}', "
                    "NULL, 0, ?, NULL, 'b36-probe')",
                    (wf_id, pid_a, now_iso()),
                )
                conn.commit()
            finally:
                conn.close()

            r2 = await _req(
                app,
                "POST",
                f"/api/projects/{pid_a}/deconstruct",
                json={
                    "book_title": "第二个拆书",
                    "text": _BAD_TEXT,
                    "mock_providers": {"deconstructor_chapter": []},
                },
            )
            assert r2.status_code == 409, r2.text
            assert "already has an active" in r2.json()["detail"]

            # 跨项目互不拦截（守卫按 project_id 过滤）
            r3 = await _req(
                app,
                "POST",
                f"/api/projects/{pid_b}/deconstruct",
                json={
                    "book_title": "B 的拆书",
                    "text": _BAD_TEXT,
                    "mock_providers": {"deconstructor_chapter": []},
                },
            )
            assert r3.status_code == 201, r3.text
            await _wait_terminal(app, r3.json()["run_id"])

    asyncio.run(go())


def test_deconstruct_endpoint_uses_async_start_with_guard(tmp_path: Path, monkeypatch):
    """C-23：端点必须走 ``start_with_nodes_async``（同步版被调用即红）。

    真实引擎在两种调用形态下都能返回 201 ⇒ 上面那条集成用例**区分不了**同步/异步；
    这里用记录型假 engine 直接钉住调用面（同时复核 project_id / project_level_guard）。
    """
    import packages.core.api.routers.reference as ref

    calls: dict = {}

    class _RecordingEngine:
        def __init__(self, db_path):  # noqa: ARG002
            pass

        def start_with_nodes(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("deconstruct 必须用 start_with_nodes_async（C-23）")

        def start_with_nodes_async(self, *args, **kwargs):
            calls["workflow_name"] = args[0]
            calls["kwargs"] = kwargs
            return "wfr_b36_async"

    monkeypatch.setattr(ref, "WorkflowEngine", _RecordingEngine)
    app = _make_app(tmp_path)

    async def go():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "异步拆书")
            r = await _req(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={
                    "book_title": "异步",
                    "text": _BAD_TEXT,
                    "mock_providers": {"deconstructor_chapter": []},
                },
            )
            assert r.status_code == 201, r.text
            assert r.json() == {
                "run_id": "wfr_b36_async",
                "status": "RUNNING",
                "current_node": None,
            }
            assert calls["workflow_name"] == "deconstruct-book"
            assert calls["kwargs"]["project_id"] == pid
            assert calls["kwargs"]["project_level_guard"] is True

    asyncio.run(go())
