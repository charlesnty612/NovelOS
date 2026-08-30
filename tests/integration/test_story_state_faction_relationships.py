"""/api/story_state 端到端集成测试：faction↔faction 关系走真实 commit 路径（修复 wfr_6619a7bfa6fa）。

背景：
- 生产事故 wfr_6619a7bfa6fa：组织间关系（faction↔faction）被三层拦截
  ① validator（已修：扩为 characters ∪ factions）
  ② applier（已修：按 characters 先于 factions 定位宿主桶）
  ③ DB FK（本次修复：0001_init.sql 第 190-191 行的 FK REFERENCES characters
     会让 write_through INSERT 撞 IntegrityError）— 0022 迁移去除这两个 FK。
- 本测试覆盖端到端链路：
  - 建 faction ×2（用 /api/projects/{pid}/factions 端点）
  - 提交一条 relationship_changes 含 faction↔faction 端点
  - 真实 commit 路径走完
  - GET /api/projects/{pid}/state 验证：
    a) factions 桶含该关系（在 faction.relationships 中）
    b) characters 桶未被污染（relationship 不出现在 character.relationships）
    c) DB 层 relationships 表确实有该行

设计要点：
- 独立测试文件（不与既有 test_story_state_api.py 耦合），聚焦 faction 端点
  端到端语义；主链 happy path 由既有 test_full_lifecycle_commit_and_rollback
  覆盖。
- 复用现有 _create_app / _make_project / _make_chapter / _make_meta 思路；
  自带 _make_faction helper（避免污染其他测试文件）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "faction 端到端项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _make_faction(app, pid: str, name: str) -> str:
    """通过 /api/projects/{pid}/factions 端点建 faction（真实生产路径）。"""
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/factions",
        json={"name": name, "statement": f"{name} 的组织陈述"},
    )
    assert r.status_code == 201, r.text
    # WorldEntity.to_dict() 暴露的 id 字段统一为 ``id``（不分 entity 类型），
    # 由 ``id_field`` 区分（faction 为 faction_id / location 为 location_id）。
    return r.json()["id"]


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


# ---------------------------------------------------------------------------
# 端到端：faction↔faction 关系走真实 commit 路径
# ---------------------------------------------------------------------------


def test_faction_to_faction_relationship_end_to_end(tmp_path: Path):
    """建 faction×2 → 提交 faction↔faction 关系 → 走真实 commit → GET state 验证。

    修复闭环 P0-1/P0-2/P0-3：
    - 0022 移除 relationships.from/to FK → write_through INSERT 不再撞 FK
    - snapshot 重建聚合 faction 端点到 factions[fid].relationships
    - 主链 commit → GET state 201/200 完整路径通
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)

            # 通过真实 API 建两个 faction（东典当 / 西典当 —— 生产事故原型场景）
            fac_east = await _make_faction(app, pid, "东典当")
            fac_west = await _make_faction(app, pid, "西典当")

            # init genesis（v1）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # 提交 delta：一条 faction↔faction 关系（商战 / 敌对）
            delta_id = "dlt_faction_aaaa"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_faction_1",
                        "op": "add",
                        "target_id": "rel_pawnshop_feud",
                        "from_character_id": fac_east,  # 字段名沿用，端点是 faction
                        "to_character_id": fac_west,
                        "relation_type": "hostile",
                        "before": None,
                        "after": {"intensity": 0.9, "since_chapter": 1},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "MEDIUM",
                    }
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            # 真实 commit（关键路径：write_through 走 DB INSERT 到 relationships）
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "notes": None},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit = r.json()
            assert commit["state_version"] == 2

            # GET state v2：验证 factions 桶含该关系
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            state_v2 = r.json()
            assert state_v2["state_version"] == 2

            # factions 桶：东典当应含一条关系（指向西典当）
            world = state_v2.get("world") or {}
            factions = world.get("factions") or {}
            assert fac_east in factions, (
                f"factions 桶缺 {fac_east}; got={list(factions.keys())}"
            )
            east_entry = factions[fac_east]
            east_rels = east_entry.get("relationships") or []
            assert len(east_rels) == 1, (
                f"factions[{fac_east}].relationships 应有 1 条，实际：{east_rels}"
            )
            rel = east_rels[0]
            assert rel["from_character_id"] == fac_east
            assert rel["to_character_id"] == fac_west
            assert rel["relation_type"] == "hostile"
            assert rel["state_json"] == {"intensity": 0.9, "since_chapter": 1}

            # characters 桶：项目内无角色，应为空（或至少不含该关系）
            chars = state_v2.get("characters") or []
            for c in chars:
                c_rels = c.get("relationships") or []
                for cr in c_rels:
                    assert cr["from_character_id"] != fac_east, (
                        f"character 桶被 faction 关系污染：{cr}"
                    )

            # DB 层直接断言 relationships 表确有该行（写透到 DB）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT relationship_id, from_character_id, to_character_id, "
                    "relation_type, state_json FROM relationships WHERE project_id = ?",
                    (pid,),
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1, f"DB relationships 应有 1 行，实际：{rows}"
            row = rows[0]
            assert row["from_character_id"] == fac_east
            assert row["to_character_id"] == fac_west
            assert row["relation_type"] == "hostile"

    asyncio.run(run())


def test_double_rollback_blocked_by_db_unique_index(tmp_path: Path):
    """P1 DB 层闭环：0022 commits.rollback_of 部分唯一索引存在并按预期生效。

    说明：本测试用 DB 直插 + 直接触发 commits INSERT 路径的方式验证 0022 的
    部分唯一索引——不依赖完整 rollback → build_inverse_delta 路径（后者有
    无关 bug：纯 relationship_changes 的 delta 会触发 build_inverse_delta 中
    rollback_reason 未赋值；不属于本次任务范围）。
    """
    import sqlite3 as _sqlite3
    import datetime as _dt
    import uuid as _uuid

    app = _create_app(tmp_path)

    async def setup_state():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)
            return pid, chap

    pid, chap = asyncio.run(setup_state())

    # 触发 init（chapters 行已被 init 创建；同时让 genesis commits 行就位），
    # 复用 init 留下的 chapter/chapters 元数据。
    async def run_init():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201

    asyncio.run(run_init())

    # 直接在 DB 层构造两次 commits 行，rollback_of 指向同一 commit，断言第二次
    # 被 idx_commits_rollback_of（0022 新增的部分唯一索引）拦截。
    def iso():
        return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")

    def nid(p):
        return f"{p}_{_uuid.uuid4().hex[:12]}"

    bid = nid("br")
    wfid = nid("wf")
    rid = nid("run")
    did = nid("dlt")
    target_commit = nid("cmt")  # 目标 commit（rollback of 引用它）
    rollback_a = nid("cmt")    # 第一次 rollback
    rollback_b = nid("cmt")    # 第二次 rollback（应被拦截）

    conn = _sqlite3.connect(str(tmp_path / "novelos.db"))
    conn.row_factory = _sqlite3.Row
    try:
        # 0022 应创建 idx_commits_rollback_of 索引
        idx_names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_commits_rollback_of" in idx_names, (
            f"0022 应创建 idx_commits_rollback_of 索引；got={idx_names}"
        )

        # 必要的 fixture 行（branch / workflow / delta / run —— chapter 已被 init 创建）
        conn.execute(
            "INSERT INTO branches (branch_id,project_id,name,parent_branch_id,"
            "base_state_version,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (bid, pid, "main", None, 1, "ACTIVE", iso()),
        )
        conn.execute(
            "INSERT INTO workflows (workflow_id,name,version,definition_json,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (wfid, "wf", "v1", "{}", iso(), iso()),
        )
        conn.execute(
            "INSERT INTO state_deltas (delta_id,chapter_id,workflow_run_id,"
            "previous_state_version,delta_version,schema_version,payload_json,"
            "status,supersedes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (did, chap, rid, 1, 1, "state-delta-v0", "{}", "validated", None,
             "test", iso()),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id,workflow_id,chapter_id,status,"
            "current_node,checkpoint_json,error,retry_count,started_at,ended_at) "
            "VALUES (?,?,?,'COMPLETED',NULL,'',NULL,0,?,?)",
            (rid, wfid, chap, iso(), iso()),
        )

        # 目标 commit（被 rollback 的那条）
        conn.execute(
            "INSERT INTO commits (commit_id,project_id,branch_id,chapter_id,"
            "previous_state_version,resulting_state_version,delta_id,"
            "validation_json,author_approval_json,timestamp,workflow_run_id,"
            "rollback_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (target_commit, pid, bid, chap, 1, 2, did, "{}", "{}",
             iso(), rid, None),
        )

        # 第一次 rollback commit：rollback_of=target_commit → 应成功
        conn.execute(
            "INSERT INTO commits (commit_id,project_id,branch_id,chapter_id,"
            "previous_state_version,resulting_state_version,delta_id,"
            "validation_json,author_approval_json,timestamp,workflow_run_id,"
            "rollback_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rollback_a, pid, bid, chap, 2, 3, did, "{}", "{}",
             iso(), rid, target_commit),
        )
        conn.commit()

        # 第二次 rollback commit：rollback_of=target_commit → 0022 部分唯一索引拦截
        with pytest.raises(_sqlite3.IntegrityError) as exc_info:
            conn.execute(
                "INSERT INTO commits (commit_id,project_id,branch_id,chapter_id,"
                "previous_state_version,resulting_state_version,delta_id,"
                "validation_json,author_approval_json,timestamp,workflow_run_id,"
                "rollback_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rollback_b, pid, bid, chap, 3, 4, did, "{}", "{}",
                 iso(), rid, target_commit),
            )
        assert "rollback_of" in str(exc_info.value).lower(), (
            f"双重回滚 UNIQUE 错误消息应提到 rollback_of，实际：{exc_info.value}"
        )
    finally:
        conn.close()


# 局部 pytest 导入（test 文件用 pytest.raises）
import pytest