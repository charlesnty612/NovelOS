"""``POST /projects/{pid}/branches/{bid}/promote`` 响应的 ``state_version``
必须是 **promote 提交后 DB 里的最新版本**（2026-10-24 缺陷修复的判别用例）。

缺陷（修前实测）
----------------
非空分支 promote：响应 ``state_version=3`` / DB ``MAX(state_version)=4`` ⇒ **低 1**。
根因：promote 自身的版本推进（分支 ``ACTIVE→MERGED`` 改了 ``branches`` 表 ⇒ 该表
进 director 装配 payload ⇒ 必须推 ``story_states.state_version``）发生在**响应
取值之后**，而响应取的是「最后一次重放落在 main 上的那一版」⇒ 少算了这一次推进。

空分支 promote：响应 = 分支的 ``base_state_version``（**建分支 + 合并**两次推进
都没算进去）⇒ 实测响应 4 / DB 最新 6。

判别口径
--------
* 断言打在**真实值**上：直查 DB 的 ``MAX(state_version)``，**不写死「+1」**
  （写死就等于把「差一版」这个缺陷重新编进测试）。
* 正反两面都钉：响应值 == DB 真值；且 == ``GET /state``（主线读路径）报的值。
* **推进本身也要被钉住**（第三条用例）：非空 promote 推进后的版本 ==
  **最后一次重放落的版本 + 1**（两条都读 DB 真值）——只断言「响应 == DB 真值」这条
  **关系**时，摘掉 promote 自身的 bump 会让两者**一起**降 1 ⇒ 关系不变 ⇒ 仍绿
  ⇒ 缺的是**绝对量**断言。
* **撤修复必红**：把 bump 移回响应取值之后 / 响应改回「重放最后一版」⇒ 本文件
  用例同时红；只摘 bump（响应照旧取 bump 返回值）⇒ 第三条用例红、前两条仍绿。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成夹具）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _max_state_version(db_path: Path, pid: str) -> int:
    """直查 DB：该项目的最新 ``story_states.state_version``（**真实值**，非推算）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS sv FROM story_states WHERE project_id = ?",
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _state_versions(db_path: Path, pid: str) -> list[int]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT state_version FROM story_states WHERE project_id = ? ORDER BY state_version",
            (pid,),
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def _promote_replay_versions(db_path: Path, pid: str, bid: str) -> list[int]:
    """promote **重放到 main 上**的 commit 各自落的 ``resulting_state_version``（按序）。

    识别口径：``commits.workflow_run_id = 'system:promote:<branch_id>'``——这是
    ``packages/core/story_state/branches.py::promote_branch`` 重放时写死的审计标记
    （原分支 commit 的 ``workflow_run_id`` 是作者自己的，不带这个前缀）。

    ⚠️ 口径自身的自证：调用方拿它与响应里的 ``promoted_commits`` 对账
    （条数不符 ⇒ 识别口径失效，不是 promote 少重放了）。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT resulting_state_version FROM commits "
            "WHERE project_id = ? AND workflow_run_id = ? "
            "ORDER BY resulting_state_version ASC",
            (pid, f"system:promote:{bid}"),
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def _delta(delta_id: str, chapter_id: str, character_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "promote-version-test",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": "cc_pv",
                "op": "update",
                "target_id": character_id,
                "character_id": character_id,
                "facet": "state",
                "field": "state.location",
                "before": "Unknown",
                "after": "Cave",
                "confidence": 0.95,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": None,
                    "excerpt": "excerpt",
                    "span": None,
                },
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


async def _seed(app) -> tuple[str, str, str, int]:
    """建项目 + 角色 + 章节 + genesis。返回 ``(pid, char_id, chapter_id, version)``。

    ⚠️ 必须在 ``app.router.lifespan_context(app)`` **之内**调用（迁移只在
    lifespan 里跑；``httpx.ASGITransport`` 不触发 lifespan）。
    """
    pid = (await _request(app, "POST", "/api/projects", json={"name": "promote 版本"})).json()[
        "project_id"
    ]
    char = (
        await _request(
            app,
            "POST",
            f"/api/projects/{pid}/characters",
            json={"name": "林夕", "role": "protagonist"},
        )
    ).json()["character_id"]
    chap = (
        await _request(
            app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
        )
    ).json()["chapter_id"]
    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
    assert r.status_code == 201, r.text
    return pid, char, chap, r.json()["state_version"]


@pytest.mark.anyio
async def test_promote_response_state_version_equals_db_latest(tmp_path: Path):
    """非空分支 promote：响应 ``state_version`` == DB ``MAX(state_version)``。

    **判别点**：撤掉「响应取 bump 后真值」⇒ 响应比 DB 真值低 1 ⇒ 必红。
    """
    app = _create_app(tmp_path)
    async with app.router.lifespan_context(app):
        pid, char, chap, base = await _seed(app)
        bid = (
            await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})
        ).json()["branch_id"]

        delta = _delta("dlt_pv_1", chap, char, base)
        r = await _request(
            app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid}
        )
        assert r.status_code == 201, r.text
        r = await _request(
            app,
            "POST",
            f"/api/projects/{pid}/commits",
            json={
                "delta_id": "dlt_pv_1",
                "workflow_run_id": "wfr_dlt_pv_1",
                "branch_id": bid,
            },
        )
        assert r.status_code == 201, r.text

        r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["promoted_commits"] == 1

        db_path = tmp_path / "novelos.db"
        db_max = _max_state_version(db_path, pid)
        assert body["state_version"] == db_max, (
            f"promote 响应 state_version={body['state_version']} ≠ DB 最新 {db_max}"
            f"（story_states 版本序列={_state_versions(db_path, pid)}）"
        )

        # 正反两面：主线读路径报的就是同一个版本（前端显示的是它）
        r = await _request(app, "GET", f"/api/projects/{pid}/state")
        assert r.status_code == 200, r.text
        assert r.json()["state_version"] == body["state_version"]


@pytest.mark.anyio
async def test_promote_bump_is_exactly_one_above_last_replayed_commit(tmp_path: Path):
    """非空 promote：**推进后的版本 == 最后一次重放落的版本 + 1**。

    ⚠️ **判别点**：把 ``packages/core/story_state/branches.py::promote_branch`` 里那次
    ``bump_project_state_version`` 摘掉 ⇒ ``story_states`` 的最新版本 == 最后一次重放
    落的版本（**不再 +1**）⇒ 本用例必红。
    ⚠️ **为什么既有两条用例挡不住**：它们断言的是「响应 == DB 真值」这条**关系**；
    摘掉 bump 后响应与 DB 真值**一起**降 1 ⇒ 关系不变 ⇒ 仍绿。⇒ 缺的是「推进**发生
    了**且**恰好 +1**」这条**绝对量**断言。

    两条数都读 **DB 真值**（不写死、不推算）：
    * 「最后一次重放落的版本」= ``commits.resulting_state_version``（重放 commit 落库的值）；
    * 「推进后的版本」= ``story_states`` 的 ``MAX(state_version)``。
    """
    app = _create_app(tmp_path)
    async with app.router.lifespan_context(app):
        pid, char, chap, base = await _seed(app)
        bid = (
            await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})
        ).json()["branch_id"]

        delta = _delta("dlt_pv_3", chap, char, base)
        r = await _request(
            app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid}
        )
        assert r.status_code == 201, r.text
        r = await _request(
            app,
            "POST",
            f"/api/projects/{pid}/commits",
            json={
                "delta_id": "dlt_pv_3",
                "workflow_run_id": "wfr_dlt_pv_3",
                "branch_id": bid,
            },
        )
        assert r.status_code == 201, r.text

        r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["promoted_commits"] == 1

        db_path = tmp_path / "novelos.db"
        replay_versions = _promote_replay_versions(db_path, pid, bid)
        # 识别口径自证：条数必须与 promote 自报的「重放了几条」一致。
        assert len(replay_versions) == body["promoted_commits"], (
            f"重放 commit 的识别口径失效：按 workflow_run_id 找到 {len(replay_versions)} 条，"
            f"而 promote 自报 {body['promoted_commits']} 条"
        )
        last_replay = replay_versions[-1]
        db_max = _max_state_version(db_path, pid)
        assert db_max == last_replay + 1, (
            f"promote 推进后的版本 {db_max} ≠ 最后一次重放落的版本 {last_replay} + 1"
            f"（重放版本序列={replay_versions}，story_states 序列={_state_versions(db_path, pid)}）"
            "——摘掉 promote_branch 里的 bump_project_state_version 即为此状"
        )


@pytest.mark.anyio
async def test_promote_empty_branch_response_state_version_equals_db_latest(tmp_path: Path):
    """空分支 promote：响应 ``state_version`` == DB ``MAX(state_version)``。

    空分支**也**推进版本（分支 ``ACTIVE→MERGED`` 改了 ``branches`` 表 ⇒ 该表进
    director 装配 payload ⇒ 必须推 version）⇒ 响应不再是「建分支时的 base」。
    """
    app = _create_app(tmp_path)
    async with app.router.lifespan_context(app):
        pid, _char, _chap, _base = await _seed(app)
        bid = (
            await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "empty"})
        ).json()["branch_id"]

        r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["promoted_commits"] == 0

        db_path = tmp_path / "novelos.db"
        db_max = _max_state_version(db_path, pid)
        assert body["state_version"] == db_max, (
            f"空分支 promote 响应 state_version={body['state_version']} ≠ DB 最新 {db_max}"
            f"（story_states 版本序列={_state_versions(db_path, pid)}）"
        )


@pytest.mark.anyio
async def test_promote_advances_version_and_materialized_point_matches(tmp_path: Path):
    """旁证：promote 后 ``branch_snapshots`` 物化点 == ``story_states`` 最新版。

    两条读路径（``GET /state`` 走 ``story_states`` / ``GET /state?branch_id=main``
    走 ``branch_snapshots``）版本一致 ⇒ 不再出现「同一状态两个版本号」。
    """
    app = _create_app(tmp_path)
    async with app.router.lifespan_context(app):
        pid, char, chap, base = await _seed(app)
        bid = (
            await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "alt"})
        ).json()["branch_id"]
        delta = _delta("dlt_pv_2", chap, char, base)
        r = await _request(
            app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid}
        )
        assert r.status_code == 201, r.text
        r = await _request(
            app,
            "POST",
            f"/api/projects/{pid}/commits",
            json={
                "delta_id": "dlt_pv_2",
                "workflow_run_id": "wfr_dlt_pv_2",
                "branch_id": bid,
            },
        )
        assert r.status_code == 201, r.text
        r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote", json={})
        assert r.status_code == 201, r.text
        promote_version = r.json()["state_version"]

        main_bid = next(
            b["branch_id"]
            for b in (await _request(app, "GET", f"/api/projects/{pid}/branches")).json()
            if b["name"] == "main"
        )
        conn = sqlite3.connect(str(tmp_path / "novelos.db"))
        try:
            row = conn.execute(
                "SELECT state_version FROM branch_snapshots WHERE branch_id = ?",
                (main_bid,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "main 分支 promote 后无 branch_snapshots 行"
        # ⚠️ 判别点：物化点必须落在 **DB 最新版本**上（推进排在物化之前）。
        # 修前物化排在推进之前 ⇒ 物化点比 DB 最新版本低 1 ⇒ 两条读路径版本恒差 1。
        db_max = _max_state_version(tmp_path / "novelos.db", pid)
        assert int(row[0]) == db_max, (
            f"物化点 {row[0]} ≠ DB 最新 {db_max}"
            "（物化排在推进之前 ⇒ 两条读路径版本恒差 1）"
        )
        assert int(row[0]) == promote_version

        r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={main_bid}")
        assert r.status_code == 200, r.text
        assert r.json()["state_version"] == promote_version
