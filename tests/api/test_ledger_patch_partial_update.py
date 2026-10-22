"""`PATCH /hooks` 与 `PATCH /debts` 的**部分更新语义**（2026-10-15 批次（六））。

背景：一个**数据丢失**缺陷，2026-08-23 起存在，直到本批被 G2 成员在建模时撞见。
`update_hook` / `update_debt` 里那两段章节外键循环是无条件写入：

    for fld in ("introduced_chapter_id", ...):
        v = getattr(payload, fld)
        ...
        fields[fld] = v          # ← 客户端没传这个字段时，v 就是默认值 None

而 `HookUpdate` / `DebtUpdate` 的默认值是 `None` ⇒ **「没传」与「显式置空」不可区分**
⇒ 客户端只发 ``{"status": "ACTIVE"}``，三列章节外键就被一起抹成 NULL。

为什么能活两个月：`tests/api/test_ledger.py` 覆盖了 create → get → list → update → delete
的主链路，但**没有一条用例 PATCH 章节外键字段**，而主链路里的 update 用例只 PATCH 了
被 ``if payload.X is not None`` 保护着的字段——那些字段本来就是对的。

判定不是笔误而是设计：同一函数里其余五个字段都走「没传就跳过」，只有这三个不一致。

本组用例构成**正反一对**（AGENTS 坑区「夹具永远不触发闸门 ⇒ 空断言」的配套要求）：

* 正向：只 PATCH status，未提及的章节外键**必须保持原值**；
* 反向：显式传 ``null`` **必须真的清空**——否则「干脆不写这三列」也算过，
  修复就变成了「PATCH 永远无法清字段」的另一种退化。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

from ._shared import _request


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))




def _seed(app) -> tuple[str, str, str]:
    """建项目 + 两章，返回 (pid, hook_id, debt_id)。"""

    async def run() -> tuple[str, str, str]:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                pid = (await c.post("/api/projects", json={"name": "部分更新"})).json()["project_id"]
                ch1 = (
                    await c.post(f"/api/projects/{pid}/chapters", json={"number": 1, "title": "一"})
                ).json()["chapter_id"]
                await c.post(f"/api/projects/{pid}/chapters", json={"number": 2, "title": "二"})
                hook = (
                    await c.post(
                        f"/api/projects/{pid}/hooks",
                        json={
                            "name": "破屋锚点",
                            "importance": 0.9,
                            "introduced_chapter_id": ch1,
                            "expected_payoff_chapter_id": ch1,
                        },
                    )
                ).json()
                debt = (
                    await c.post(
                        f"/api/projects/{pid}/debts",
                        json={
                            "description": "欠一条命",
                            "created_chapter_id": ch1,
                            "deadline_chapter_id": ch1,
                        },
                    )
                ).json()
                return pid, hook["hook_id"], debt["debt_id"]

    return asyncio.run(run())


def _setup(tmp_path: Path):
    app = _create_app(tmp_path)
    pid, hook_id, debt_id = _seed(app)
    return app, pid, hook_id, debt_id


# ---------------------------------------------------------------- hooks


def test_patch_hook_status_keeps_untouched_chapter_refs(tmp_path: Path):
    """正向：只改 status，两列章节外键必须原样保留。"""
    app, pid, hook_id, _debt_id = _setup(tmp_path)

    before = asyncio.run(_request(app, "GET", f"/api/hooks/{hook_id}")).json()
    assert before["introduced_chapter_id"], "夹具前提：建钩子时带了章节外键"

    r = asyncio.run(
        _request(app, "PATCH", f"/api/hooks/{hook_id}", json={"status": "ACTIVE"})
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACTIVE", "status 本身必须真的改了（对照组）"
    assert r.json()["introduced_chapter_id"] == before["introduced_chapter_id"], (
        "未提及的 introduced_chapter_id 被抹成了 "
        f"{r.json()['introduced_chapter_id']!r}——部分更新不该碰它"
    )
    assert (
        r.json()["expected_payoff_chapter_id"] == before["expected_payoff_chapter_id"]
    ), "未提及的 expected_payoff_chapter_id 被抹成了 " f"{r.json()['expected_payoff_chapter_id']!r}"


def test_patch_hook_explicit_null_still_clears(tmp_path: Path):
    """反向：显式传 null **必须真的清空**。

    没有这条，「修法 = 干脆不写这三列」也会让上一条变绿——那就把 PATCH
    变成了永远无法清字段的另一种退化。
    """
    app, pid, hook_id, _debt_id = _setup(tmp_path)

    r = asyncio.run(
        _request(
            app,
            "PATCH",
            f"/api/hooks/{hook_id}",
            json={"introduced_chapter_id": None},
        )
    )
    assert r.status_code == 200, r.text
    assert r.json()["introduced_chapter_id"] is None, (
        "显式置空必须生效（PATCH 语义要求能主动清字段）"
    )
    # 同一请求里没提及的那一列**仍须保留**
    assert r.json()["expected_payoff_chapter_id"], "同请求未提及的列被连带清空了"


# ---------------------------------------------------------------- debts


def test_patch_debt_status_keeps_untouched_chapter_refs(tmp_path: Path):
    app, pid, _hook_id, debt_id = _setup(tmp_path)

    before = asyncio.run(_request(app, "GET", f"/api/debts/{debt_id}")).json()
    assert before["created_chapter_id"], "夹具前提：建债时带了章节外键"

    r = asyncio.run(
        _request(app, "PATCH", f"/api/debts/{debt_id}", json={"status": "acknowledged"})
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "acknowledged", "status 本身必须真的改了（对照组）"
    assert r.json()["created_chapter_id"] == before["created_chapter_id"], (
        f"未提及的 created_chapter_id 被抹成了 {r.json()['created_chapter_id']!r}"
    )
    assert r.json()["deadline_chapter_id"] == before["deadline_chapter_id"], (
        f"未提及的 deadline_chapter_id 被抹成了 {r.json()['deadline_chapter_id']!r}"
    )


def test_patch_debt_explicit_null_still_clears(tmp_path: Path):
    app, pid, _hook_id, debt_id = _setup(tmp_path)

    r = asyncio.run(
        _request(
            app, "PATCH", f"/api/debts/{debt_id}", json={"deadline_chapter_id": None}
        )
    )
    assert r.status_code == 200, r.text
    assert r.json()["deadline_chapter_id"] is None, "显式置空必须生效"
    assert r.json()["created_chapter_id"], "同请求未提及的列被连带清空了"
