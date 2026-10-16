"""章节归属失败的**响应契约**：两种失败必须**逐字相同**的 404（2026-10-16 批次十）。

## 为什么要有这个用例

本仓是**单用户本机自用工具**（AGENTS §一）。「chapter 属于别的项目」在这里
**不是权限问题**，而是「这个对象在本项目 URL 下查不到」。因此：

    chapter 不存在       ⇒ 404
    chapter 属于别的项目 ⇒ 404   ← 不是 400，也不是 403

**返回 400/403 本身就是跨项目存在性泄露**：它告诉调用方「你给的 chapter_id
确实存在，只是不给你」——调用方就能拿它去枚举别的项目的章节。

⚠️ **只换状态码不够**（本用例专钉这一格）：若状态码 404 而 detail 仍写着
``does not belong to project X``，等于用 detail 把要避免的泄露又漏一遍。
所以断言必须**逐字比对 detail 模板**，并逐个检查泄漏词。

## 这条欠账的来历（留痕）

批次九在 ``/chapters/{cid}/deltas`` / ``/state/init`` / ``/branches/{n}/promote``
三个端点上发现同一形状，**没有改权威实现**，而是在 ``story_state.py`` 里包了一层
「捕获 400 → 改写成 404」的 workaround。后果：

- ``workflows/common.py::check_chapter_in_project`` 仍对跨项目抛 **400**；
- ``continuation.py`` 返回 404 但 detail **仍泄露**归属；
- 每接一个谨慎的调用点就要记得自己包一层 —— 政策没落在**唯一的判定处**，
  而是散落在每个调用点的自觉里。

批次十把政策收进 helper 本身，删掉那层 workaround（它已成了死代码：不再改变
任何响应，只是把一个 HTTPException 拆包再包一遍）。

## 三层覆盖

1. **helper 直调**（权威判定，最不依赖 HTTP 管线与校验顺序）；
2. **端点层**（证明政策真的到达响应体）；
3. **源码级形状守卫**（防止将来有人再加一处泄露的写法）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from packages.core.api.main import create_app
from packages.core.api.routers.workflows.common import check_chapter_in_project
from packages.core.config import Settings

#: 两种归属失败共用的 detail 模板——**唯一的真相源**（期望值硬编码，不从实现反推）
NOT_FOUND_TEMPLATE = "chapter {cid!r} not found"

#: 任何出现在 detail 里都算泄露的词
LEAK_WORDS = ("belong", "project", "belongs")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    """建 app 并**跑完迁移**。

    ⚠️ 必须走 lifespan：迁移在 lifespan 里跑（``apply_migrations``），
    直接发请求会撞 ``no such table: projects``——既有测试都这么写，不是本用例特例。
    """
    app = create_app(Settings(data_dir=tmp_path, log_level="WARNING"))
    return app


async def _seed_two_projects(app) -> tuple[str, str, str]:
    """在 lifespan 内建两个项目 + 一个章，返回 (pid_a, pid_b, cid_a)。"""
    async with app.router.lifespan_context(app):
        pid_a = await _make_project(app, "A")
        pid_b = await _make_project(app, "B")
        cid_a = await _make_chapter(app, pid_a)
    return pid_a, pid_b, cid_a


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str) -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1) -> str:
    r = await _request(app, "POST", f"/api/projects/{pid}/chapters", json={"number": number})
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


# ---------------------------------------------------------------------------
# 1. helper 直调（权威判定）
# ---------------------------------------------------------------------------


def test_helper_returns_404_identical_detail_for_both_failures(tmp_path: Path):
    """核心：跨项目 与 不存在 ⇒ **同一个 404、同逐字 detail**。

    正反一对都在：跨项目（原先 400）必须降成 404；不存在（一直是 404）必须**不变**
    ——后者防「为了对齐把 404 也改掉」这种反向过度修正。
    """

    async def run():
        app = _create_app(tmp_path)
        pid_a, pid_b, cid_a = await _seed_two_projects(app)
        return app.state.settings.db_path, pid_a, pid_b, cid_a

    db_path, pid_a, pid_b, cid_a = asyncio.run(run())

    # 跨项目：原先 400 + "does not belong"，现在必须与「不存在」**逐字相同**
    with pytest.raises(HTTPException) as foreign:
        check_chapter_in_project(db_path, pid_b, cid_a)
    with pytest.raises(HTTPException) as absent:
        check_chapter_in_project(db_path, pid_a, "ch_definitely_not_here")

    assert foreign.value.status_code == 404, (
        f"跨项目必须 404（不得 400/403——那泄露存在性），实际 {foreign.value.status_code}"
    )
    assert absent.value.status_code == 404
    assert foreign.value.detail == NOT_FOUND_TEMPLATE.format(cid=cid_a)
    assert foreign.value.detail == absent.value.detail.replace(
        "ch_definitely_not_here", cid_a
    ), "两种归属失败的 detail 必须同模板"

    for word in LEAK_WORDS:
        assert word not in foreign.value.detail, f"detail 泄漏 {word!r}：{foreign.value.detail}"


def test_helper_still_returns_chapter_when_ownership_ok(tmp_path: Path):
    """反向不变量：归属正确时**不得**抛**——防「一律 404」的过度收窄。"""

    async def run():
        app = _create_app(tmp_path)
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "own")
            cid = await _make_chapter(app, pid)
        return app.state.settings.db_path, pid, cid

    db_path, pid, cid = asyncio.run(run())
    got_pid, got_cid = check_chapter_in_project(db_path, pid, cid)
    assert (got_pid, got_cid) == (pid, cid)


# ---------------------------------------------------------------------------
# 2. 端点层：政策真的到达响应体
# ---------------------------------------------------------------------------


def test_endpoint_detail_carries_no_ownership_leak(tmp_path: Path):
    """端点层复钉一遍——helper 对了不等于响应体对了（中间还有 detail 改写层）。"""
    from packages.core.api.routers.story_state import _ensure_chapter_in_project  # noqa: F401

    async def run():
        app = _create_app(tmp_path)
        pid_a, pid_b, cid_a = await _seed_two_projects(app)
        path = "/api/projects/{pid_b}/chapters/{cid_a}/deltas"
        r_foreign = await _request(app, "GET", path.format(pid_b=pid_b, cid_a=cid_a))
        r_absent = await _request(
            app, "GET", f"/api/projects/{pid_a}/chapters/ch_definitely_not_here/deltas"
        )
        return cid_a, r_foreign, r_absent

    cid_a, r_foreign, r_absent = asyncio.run(run())

    for r in (r_foreign, r_absent):
        assert r.status_code == 404, r.text
        assert set(r.json()) == {"detail"}, f"404 体多/少了键，那本身就是泄露面：{r.json()}"
        for word in LEAK_WORDS:
            assert word not in r.json()["detail"], f"detail 泄漏 {word!r}：{r.json()}"

    assert r_foreign.json()["detail"] == NOT_FOUND_TEMPLATE.format(cid=cid_a)
    assert r_absent.json()["detail"] == NOT_FOUND_TEMPLATE.format(cid="ch_definitely_not_here")


# ---------------------------------------------------------------------------
# 3. 源码级形状守卫：防止将来再加一处泄露写法
# ---------------------------------------------------------------------------


def test_no_router_leaks_ownership_via_detail():
    """扫 router 层源码：不得再有「把归属线索写进 detail」的写法。

    为什么需要这一条：前两层都只覆盖**已知的**三条路径。将来有人新写一个端点，
    又按老习惯写了 ``does not belong to project``，前两条用例**照样全绿**——
    这正是本仓登记的「判别面逐个点名，就等于给了它一个可以悄悄变小的形状」。
    这里把判别面改成**全仓扫**，新增端点自动进域。

    判据：router 层出现 ``does not belong`` 或 ``不属于本项目`` 且它所在的
    ``raise HTTPException`` 没被同文件收口函数改写 ⇒ 报出来。已知豁免只有
    ``story_state/branches.py`` 的 ``BranchNotFound``（那是**服务层异常类**的
    消息，router 侧统一映射 404，不属本条形状）。
    """
    import re
    from pathlib import Path as _P

    routers = _P(__file__).resolve().parents[2] / "packages" / "core" / "api" / "routers"
    offenders: list[str] = []
    for py in routers.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(r"detail=\(?\s*f?[\"'].{0,40}(does not belong|不属于)", text):
            offenders.append(f"{py.relative_to(routers)}: {m.group(0)[:70]}")
    assert not offenders, (
        "这些 router 把「归属」线索写进了 detail —— 违反「两种归属失败返回逐字相同 "
        "的 404」：\n  " + "\n  ".join(offenders)
    )
