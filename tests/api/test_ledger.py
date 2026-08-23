"""/api/hooks & /api/debts 集成测试（Sprint 9）。

覆盖：
- Hook / Debt 主链路：create → get → list → update → delete
- Hook 五态机合法 / 非法迁移
- Debt 四态机合法 / 非法迁移
- 章节外键缺失 → 404
- 字段非法（name 空 / importance 超界 / visibility 非法） → 422
- 不存在的 hook_id / debt_id → 404
- list 过滤 ``?status=``

测试模式参考 ``tests/api/test_chapter_drafts.py``：httpx.ASGITransport + tmp_path db。
"""

from __future__ import annotations

import asyncio
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


async def _make_project(app, name: str = "ledger 测试") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str | None = None) -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title or f"C{number}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


# ===========================================================================
# Hooks
# ===========================================================================


def test_hook_create_get_list_update_delete_happy_path(tmp_path: Path):
    """Hook 主链路：create → get → list → update → delete（含 list 过滤）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid_intro = await _make_chapter(app, pid, number=1)
            cid_pay = await _make_chapter(app, pid, number=10)

            # create
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/hooks",
                json={
                    "name": "黑玉佩秘密",
                    "importance": 0.9,
                    "introduced_chapter_id": cid_intro,
                    "expected_payoff_chapter_id": cid_pay,
                },
            )
            assert r.status_code == 201, r.text
            hk = r.json()
            assert hk["hook_id"].startswith("hook_")
            assert hk["project_id"] == pid
            assert hk["name"] == "黑玉佩秘密"
            assert hk["status"] == "OPEN"
            assert hk["importance"] == 0.9
            assert hk["introduced_chapter_id"] == cid_intro
            assert hk["expected_payoff_chapter_id"] == cid_pay
            assert hk["payoff_chapter_id"] is None
            assert hk["visibility"] == "RESTRICTED"
            assert hk["who_knows"] is None
            hid = hk["hook_id"]

            # get
            r = await _request(app, "GET", f"/api/hooks/{hid}")
            assert r.status_code == 200
            assert r.json()["hook_id"] == hid

            # 第二个 hook 用于 list 排序 / status 过滤
            r2 = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/hooks",
                json={"name": "密室线索", "importance": 0.3},
            )
            assert r2.status_code == 201
            hid2 = r2.json()["hook_id"]

            # list 全量
            r = await _request(app, "GET", f"/api/projects/{pid}/hooks")
            assert r.status_code == 200
            lst = r.json()
            assert len(lst) == 2
            assert [h["hook_id"] for h in lst] == [hid, hid2]

            # update（status 合法迁移 OPEN→ACTIVE）
            r = await _request(
                app, "PATCH", f"/api/hooks/{hid}",
                json={"status": "ACTIVE", "payoff_chapter_id": cid_pay},
            )
            assert r.status_code == 200, r.text
            upd = r.json()
            assert upd["status"] == "ACTIVE"
            assert upd["payoff_chapter_id"] == cid_pay

            # list 过滤 status=ACTIVE
            r = await _request(app, "GET", f"/api/projects/{pid}/hooks?status=ACTIVE")
            assert r.status_code == 200
            flt = r.json()
            assert [h["hook_id"] for h in flt] == [hid]
            r = await _request(app, "GET", f"/api/projects/{pid}/hooks?status=OPEN")
            assert [h["hook_id"] for h in r.json()] == [hid2]

            # delete
            r = await _request(app, "DELETE", f"/api/hooks/{hid2}")
            assert r.status_code == 204

            # get after delete → 404
            r = await _request(app, "GET", f"/api/hooks/{hid2}")
            assert r.status_code == 404

            # list 剩余 1 条
            r = await _request(app, "GET", f"/api/projects/{pid}/hooks")
            assert [h["hook_id"] for h in r.json()] == [hid]

    asyncio.run(run())


def test_hook_status_machine_legal_transitions(tmp_path: Path):
    """Hook 五态机合法迁移全部覆盖。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # 主路径：OPEN → ACTIVE → ESCALATED → RESOLVED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "主路径", "importance": 0.5},
            )
            hid = r.json()["hook_id"]
            for nxt in ("ACTIVE", "ESCALATED", "RESOLVED"):
                r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": nxt})
                assert r.status_code == 200, (nxt, r.text)
                assert r.json()["status"] == nxt

            # 旁路：OPEN → ABANDONED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks", json={"name": "放弃线"}
            )
            hid2 = r.json()["hook_id"]
            r = await _request(app, "PATCH", f"/api/hooks/{hid2}", json={"status": "ABANDONED"})
            assert r.status_code == 200, r.text

            # 旁路：OPEN → ACTIVE → ABANDONED
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks", json={"name": "中道放弃"}
            )
            hid3 = r.json()["hook_id"]
            r = await _request(app, "PATCH", f"/api/hooks/{hid3}", json={"status": "ACTIVE"})
            assert r.status_code == 200
            r = await _request(app, "PATCH", f"/api/hooks/{hid3}", json={"status": "ABANDONED"})
            assert r.status_code == 200

            # 兜底 RESOLVED → ABANDONED
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "ABANDONED"})
            assert r.status_code == 200

    asyncio.run(run())


def test_hook_status_machine_illegal_transition_returns_409(tmp_path: Path):
    """Hook 五态机非法跳变 → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "非法跳变测试"},
            )
            hid = r.json()["hook_id"]

            # OPEN → RESOLVED（不在白名单）
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "RESOLVED"})
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "illegal hook status transition" in detail
            assert "OPEN" in detail and "RESOLVED" in detail

            # OPEN → ESCALATED（跳级）
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "ESCALATED"})
            assert r.status_code == 409

            # OPEN → ACTIVE（合法）
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "ACTIVE"})
            assert r.status_code == 200

            # ACTIVE → OPEN（倒退，不在白名单）
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "OPEN"})
            assert r.status_code == 409

            # ABANDONED 终态 → 任何状态都非法
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "ABANDONED"})
            assert r.status_code == 200
            r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": "ACTIVE"})
            assert r.status_code == 409

    asyncio.run(run())


def test_hook_resolved_without_payoff_chapter_allowed(tmp_path: Path):
    """RESOLVED 状态允许 payoff_chapter_id 为 NULL（任务书给死）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks", json={"name": "h"}
            )
            hid = r.json()["hook_id"]
            for nxt in ("ACTIVE", "RESOLVED"):
                r = await _request(app, "PATCH", f"/api/hooks/{hid}", json={"status": nxt})
                assert r.status_code == 200, (nxt, r.text)
                assert r.json()["payoff_chapter_id"] is None

    asyncio.run(run())


def test_hook_chapter_not_found_returns_404(tmp_path: Path):
    """Hook 引用不存在的 chapter → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # create 时引入章节不存在
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "x", "introduced_chapter_id": "ch_nope"},
            )
            assert r.status_code == 404, r.text
            assert "chapter" in r.json()["detail"]

            # create 时预期兑现章节不存在
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "y", "expected_payoff_chapter_id": "ch_nope"},
            )
            assert r.status_code == 404

            # update 时 payoff 章节不存在
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks", json={"name": "z"}
            )
            hid = r.json()["hook_id"]
            r = await _request(
                app, "PATCH", f"/api/hooks/{hid}",
                json={"payoff_chapter_id": "ch_nope"},
            )
            assert r.status_code == 404, r.text

    asyncio.run(run())


def test_hook_validation_errors_return_422(tmp_path: Path):
    """Hook 字段非法 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # name 空
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks", json={"name": ""}
            )
            assert r.status_code == 422

            # importance 超界
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "x", "importance": 1.5},
            )
            assert r.status_code == 422

            # visibility 非法
            r = await _request(
                app, "POST", f"/api/projects/{pid}/hooks",
                json={"name": "x", "visibility": "WHATEVER"},
            )
            assert r.status_code == 422

    asyncio.run(run())


def test_hook_get_unknown_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/hooks/hook_nope")
            assert r.status_code == 404

            r = await _request(app, "PATCH", "/api/hooks/hook_nope", json={"status": "ACTIVE"})
            assert r.status_code == 404

            r = await _request(app, "DELETE", "/api/hooks/hook_nope")
            assert r.status_code == 404

    asyncio.run(run())


# ===========================================================================
# Debts
# ===========================================================================


def test_debt_create_get_list_update_delete_happy_path(tmp_path: Path):
    """Debt 主链路。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid_create = await _make_chapter(app, pid, number=1)
            cid_dl = await _make_chapter(app, pid, number=20)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={
                    "description": "男主答应调查父亲死亡",
                    "created_chapter_id": cid_create,
                    "deadline_chapter_id": cid_dl,
                    "severity": 0.8,
                },
            )
            assert r.status_code == 201, r.text
            db = r.json()
            assert db["debt_id"].startswith("debt_")
            assert db["description"] == "男主答应调查父亲死亡"
            assert db["severity"] == 0.8
            assert db["status"] == "open"
            assert db["created_chapter_id"] == cid_create
            assert db["deadline_chapter_id"] == cid_dl
            assert db["visibility"] == "RESTRICTED"
            did = db["debt_id"]

            r = await _request(app, "GET", f"/api/debts/{did}")
            assert r.status_code == 200
            assert r.json()["debt_id"] == did

            r = await _request(app, "GET", f"/api/projects/{pid}/debts")
            assert r.status_code == 200
            assert [d["debt_id"] for d in r.json()] == [did]

            # status 合法迁移 open → acknowledged
            r = await _request(
                app, "PATCH", f"/api/debts/{did}",
                json={"status": "acknowledged"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "acknowledged"

            # 过滤
            r = await _request(
                app, "GET", f"/api/projects/{pid}/debts?status=acknowledged"
            )
            assert [d["debt_id"] for d in r.json()] == [did]
            r = await _request(app, "GET", f"/api/projects/{pid}/debts?status=paid")
            assert r.json() == []

            # delete
            r = await _request(app, "DELETE", f"/api/debts/{did}")
            assert r.status_code == 204
            r = await _request(app, "GET", f"/api/debts/{did}")
            assert r.status_code == 404

    asyncio.run(run())


def test_debt_status_machine_legal_transitions(tmp_path: Path):
    """Debt 四态机合法迁移覆盖。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # 主路径：open → acknowledged → paid
            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={"description": "d1"},
            )
            did = r.json()["debt_id"]
            for nxt in ("acknowledged", "paid"):
                r = await _request(app, "PATCH", f"/api/debts/{did}", json={"status": nxt})
                assert r.status_code == 200, (nxt, r.text)
                assert r.json()["status"] == nxt

            # 旁路：open → forgiven
            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts", json={"description": "d2"}
            )
            did2 = r.json()["debt_id"]
            r = await _request(app, "PATCH", f"/api/debts/{did2}", json={"status": "forgiven"})
            assert r.status_code == 200

            # 旁路：open → paid 直接
            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts", json={"description": "d3"}
            )
            did3 = r.json()["debt_id"]
            r = await _request(app, "PATCH", f"/api/debts/{did3}", json={"status": "paid"})
            assert r.status_code == 200

    asyncio.run(run())


def test_debt_status_machine_illegal_transition_returns_409(tmp_path: Path):
    """Debt 终态 paid/forgiven 后任何迁移都非法。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts", json={"description": "d"}
            )
            did = r.json()["debt_id"]

            # open → paid（合法）
            r = await _request(app, "PATCH", f"/api/debts/{did}", json={"status": "paid"})
            assert r.status_code == 200

            # paid → acknowledged（终态）
            r = await _request(app, "PATCH", f"/api/debts/{did}", json={"status": "acknowledged"})
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "illegal debt status transition" in detail
            assert "paid" in detail and "acknowledged" in detail

            # paid → open（倒退）
            r = await _request(app, "PATCH", f"/api/debts/{did}", json={"status": "open"})
            assert r.status_code == 409

            # forgiven 终态
            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts", json={"description": "d2"}
            )
            did2 = r.json()["debt_id"]
            r = await _request(app, "PATCH", f"/api/debts/{did2}", json={"status": "forgiven"})
            assert r.status_code == 200
            r = await _request(app, "PATCH", f"/api/debts/{did2}", json={"status": "open"})
            assert r.status_code == 409

    asyncio.run(run())


def test_debt_chapter_not_found_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={"description": "d", "created_chapter_id": "ch_nope"},
            )
            assert r.status_code == 404

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={"description": "d", "deadline_chapter_id": "ch_nope"},
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_debt_validation_errors_return_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts", json={"description": ""}
            )
            assert r.status_code == 422

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={"description": "d", "severity": 2.0},
            )
            assert r.status_code == 422

            r = await _request(
                app, "POST", f"/api/projects/{pid}/debts",
                json={"description": "d", "visibility": "OOPS"},
            )
            assert r.status_code == 422

    asyncio.run(run())


def test_debt_get_unknown_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/debts/debt_nope")
            assert r.status_code == 404
            r = await _request(app, "PATCH", "/api/debts/debt_nope", json={"status": "paid"})
            assert r.status_code == 404
            r = await _request(app, "DELETE", "/api/debts/debt_nope")
            assert r.status_code == 404

    asyncio.run(run())


# ===========================================================================
# 跨项目隔离
# ===========================================================================


def test_ledger_isolated_by_project(tmp_path: Path):
    """不同项目的 hook/debt 互不可见。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "项目 A")
            pid_b = await _make_project(app, "项目 B")

            r = await _request(
                app, "POST", f"/api/projects/{pid_a}/hooks", json={"name": "a"}
            )
            hid_a = r.json()["hook_id"]
            r = await _request(
                app, "POST", f"/api/projects/{pid_b}/hooks", json={"name": "b"}
            )
            hid_b = r.json()["hook_id"]

            r = await _request(app, "GET", f"/api/projects/{pid_a}/hooks")
            assert [h["hook_id"] for h in r.json()] == [hid_a]
            r = await _request(app, "GET", f"/api/projects/{pid_b}/hooks")
            assert [h["hook_id"] for h in r.json()] == [hid_b]

    asyncio.run(run())


# ===========================================================================
# Service 单元层测试
# ===========================================================================


def test_service_transition_error_current_and_target():
    """LedgerTransitionError 携带 current/target/kind 元数据。"""
    from packages.domain.ledger.service import LedgerTransitionError

    exc = LedgerTransitionError("hook", "OPEN", "RESOLVED")
    assert exc.kind == "hook"
    assert exc.current == "OPEN"
    assert exc.target == "RESOLVED"
    assert "OPEN" in str(exc) and "RESOLVED" in str(exc) and "hook" in str(exc)


def test_models_state_machine_whitelist_consistency():
    """HOOK_ALLOWED_NEXT / DEBT_ALLOWED_NEXT 必须覆盖所有声明的状态值。"""
    from packages.domain.ledger.models import (
        DEBT_ALLOWED_NEXT,
        DEBT_STATUS_VALUES,
        HOOK_ALLOWED_NEXT,
        HOOK_STATUS_VALUES,
    )

    assert set(HOOK_ALLOWED_NEXT.keys()) == set(HOOK_STATUS_VALUES)
    for st, allowed in HOOK_ALLOWED_NEXT.items():
        assert st in allowed, f"{st} must allow self-transition"
        for nxt in allowed:
            assert nxt in HOOK_STATUS_VALUES, f"{nxt} not in HOOK_STATUS_VALUES"

    assert set(DEBT_ALLOWED_NEXT.keys()) == set(DEBT_STATUS_VALUES)
    for st, allowed in DEBT_ALLOWED_NEXT.items():
        assert st in allowed
        for nxt in allowed:
            assert nxt in DEBT_STATUS_VALUES