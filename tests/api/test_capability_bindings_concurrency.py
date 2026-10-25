"""Capability Bindings 并发 upsert 测试（M10 · 2026-10-24）。

**形状订正（先验判断被推翻必须留痕）**
任务书给的形状是「读全量绑定 → 内存改 → 整段写回」；**实际实现不是这样**：
绑定写入路径全仓**只有一处**（``packages/core/model_router/bindings.py::
BindingService.upsert``），它**本来就是**「按 capability 键拼
``INSERT ... ON CONFLICT DO UPDATE``」⇒ 跨 capability 的并发 PUT 本来就
不会互相覆盖（下面的用例 1 是这条不变量的**守门**，不是缺陷复现）。

真正存在的读改写窗口在**同一 capability 的 ``thinking_override`` 列**：
「键缺席 = 保留现值」原先是「先 ``SELECT`` 读出现值、再把合并结果整行写回」
⇒ 两次并发 PUT（其一带 ``thinking_override``、另一带不带）里，**后写的那次**
会把先写者刚设的覆盖值换成**读到的过期值** ⇒ 丢更新，且全程无任何报错。
修法：**单语句 upsert**——「保留现值」由 ``ON CONFLICT DO UPDATE`` **不碰该列**
表达 ⇒ **写路径上**那一步「读出现值再写回」根本不存在 ⇒ 无窗口。

⚠️ **新实现仍然会读**（2026-10-24 审查修复 M4 · docstring 订正）：``upsert`` 末尾
有一句 ``result = self.get(capability)``（``bindings.py`` 的 upsert 尾部）——
**那是为了取返回值**（把刚写的行读回来组装响应），发生在**写之后**，
**写路径上不存在读-改-写窗口**。⇒ 下面第 2 条用例里被补丁的 ``get``
**不是**「写路径依赖那次读」的证据，而是**制造交错、观察落库结果的观测点**。

覆盖：
1. 跨 capability 的并发 PUT ⇒ 两键都落库（真实 DB 列断言）。
2. 同 capability 的并发 upsert ⇒ 先写者的 ``thinking_override`` 不被过期值覆盖（判别点）。
3. ``thinking_override`` 三态语义逐字不变（缺席=保留 / null=清除 / 字符串=设置）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import httpx

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.bindings import UNSET_THINKING_OVERRIDE, BindingService

from ._shared import _create_app

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> str:
    db_path = tmp_path / "bindings_concurrency.db"
    apply_migrations(db_path)
    return str(db_path)


def _insert_profile(db_path: str, profile_id: str) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_profiles "
            "(profile_id, name, provider, model, params_json, enabled, created_at, updated_at) "
            "VALUES (?, ?, 'mock', 'm1', '{}', 1, datetime('now'), datetime('now'))",
            (profile_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()
    return profile_id


def _binding_row(db_path: str, capability: str) -> dict:
    """读**真实 DB 行**（断言打在 DB 列上，不只看响应体）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT capability, profile_ids, thinking_override FROM capability_bindings "
            "WHERE capability = ?",
            (capability,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    return {
        "capability": row["capability"],
        "profile_ids": json.loads(row["profile_ids"] or "[]"),
        "thinking_override": row["thinking_override"],
    }


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _put_binding_in_own_loop(app, capability: str, profile_ids: list[str]) -> httpx.Response:
    """在**独立事件循环**里发一次 PUT（供多线程并发调用）。"""

    async def run():
        return await _request(
            app,
            "PUT",
            f"/api/capability-bindings/{capability}",
            json={"profile_ids": profile_ids},
        )

    return asyncio.run(run())


async def _create_profile(app, name: str) -> str:
    r = await _request(
        app,
        "POST",
        "/api/model-profiles",
        json={"name": name, "provider": "mock", "model": "m1"},
    )
    assert r.status_code == 201, r.text
    return r.json()["profile_id"]


# ---------------------------------------------------------------------------
# 1. 跨 capability 的并发 PUT ⇒ 两键都落库
# ---------------------------------------------------------------------------


def test_concurrent_puts_to_different_capabilities_both_land(tmp_path: Path):
    """两个线程各 PUT 一个 capability ⇒ **两个键都在库里**（真实 DB 列断言）。

    ⚠️ 这条在修复前后都绿（写入本就按 capability 键 upsert）⇒ 它是**不变量的
    守门**，不是缺陷复现；缺陷复现见下面第 2 条。
    """
    app = _create_app(tmp_path)

    async def setup():
        async with app.router.lifespan_context(app):
            return await _create_profile(app, "p-a"), await _create_profile(app, "p-b")

    pid_a, pid_b = asyncio.run(setup())

    barrier = threading.Barrier(2)
    results: dict[str, httpx.Response] = {}

    def worker(capability: str, pid: str) -> None:
        barrier.wait(timeout=10)
        results[capability] = _put_binding_in_own_loop(app, capability, [pid])

    threads = [
        threading.Thread(target=worker, args=("creative_writing", pid_a)),
        threading.Thread(target=worker, args=("reasoning", pid_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "PUT 线程未在 30s 内结束（疑似死锁）"

    assert results["creative_writing"].status_code == 200, results["creative_writing"].text
    assert results["reasoning"].status_code == 200, results["reasoning"].text

    db_path = str(app.state.settings.db_path)
    assert _binding_row(db_path, "creative_writing")["profile_ids"] == [pid_a]
    assert _binding_row(db_path, "reasoning")["profile_ids"] == [pid_b]


# ---------------------------------------------------------------------------
# 2. 同 capability 的并发 upsert ⇒ thinking_override 不被过期值覆盖（判别点）
# ---------------------------------------------------------------------------


def test_concurrent_upsert_keeps_written_thinking_override(tmp_path: Path, monkeypatch):
    """★两次并发 upsert（同一 capability，一次带覆盖值、一次不带）⇒ 覆盖值**存活**。

    旧实现（先 SELECT 读现值、再整行写回）里，后写的那次会把自己**读到的过期值**
    写回去 ⇒ 先写者刚设的 ``thinking_override`` 被清成「跟随档案」（丢更新）。

    交错是**确定性**的：被补丁的 ``get`` 让「不带覆盖值」那次在读到之后**停住**，
    等对手写完再继续 ⇒ 旧实现必然用过期值覆盖（必红）。

    ⚠️ **新实现也调 ``self.get(capability)``**（upsert 末尾**取返回值**用），
    但那次读发生在**写之后** ⇒ **写路径上没有读-改-写窗口** ⇒ 落库的
    ``thinking_override`` 不受并发影响（必绿）。
    ⇒ 被补丁的 ``get`` 在新实现里是**交错制造 + 观测**的探针（它让 unset-writer
    在取返回值处停住、等对手写完），**不是**「写路径依赖那次读」的证据。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_profile(db, "mprof_conc_case")

    read_done = threading.Event()
    competitor_done = threading.Event()
    orig_get = BindingService.get
    state = {"patched_once": False}

    def patched_get(self, capability):  # noqa: ANN001
        row = orig_get(self, capability)
        if (
            threading.current_thread().name == "unset-writer"
            and not state["patched_once"]
        ):
            state["patched_once"] = True
            read_done.set()
            competitor_done.wait(timeout=10)
        return row

    monkeypatch.setattr(BindingService, "get", patched_get)

    def unset_writer() -> None:
        # 键缺席 ⇒ 语义「保留 DB 现值」（不碰 thinking_override 列）
        BindingService(db).upsert("reasoning", [pid])

    def setter() -> None:
        read_done.wait(timeout=10)
        BindingService(db).upsert("reasoning", [pid], thinking_override="high")
        competitor_done.set()

    t_unset = threading.Thread(target=unset_writer, name="unset-writer")
    t_set = threading.Thread(target=setter, name="setter")
    t_unset.start()
    t_set.start()
    t_unset.join(timeout=30)
    t_set.join(timeout=30)
    assert not t_unset.is_alive(), "unset-writer 线程未结束（疑似死锁）"
    assert not t_set.is_alive(), "setter 线程未结束（疑似死锁）"

    row = _binding_row(db, "reasoning")
    assert row["thinking_override"] == "high", (
        "并发 PUT 丢了 thinking_override：后写者把先写者刚设的值换成了过期值"
        f"（实际落库 {row['thinking_override']!r}）"
    )
    assert row["profile_ids"] == [pid]


# ---------------------------------------------------------------------------
# 3. thinking_override 三态语义逐字不变
# ---------------------------------------------------------------------------


def test_thinking_override_three_states_unchanged(tmp_path: Path):
    """键缺席=保留现值 / 显式 null=清除 / 字符串=设置（单语句 upsert 后语义不变）。"""
    db = _fresh_db(tmp_path)
    pid = _insert_profile(db, "mprof_states_case")
    svc = BindingService(db)

    assert svc.upsert("reasoning", [pid], thinking_override="high")[
        "thinking_override"
    ] == "high"
    # 键缺席 ⇒ 保留现值
    assert svc.upsert("reasoning", [pid])["thinking_override"] == "high"
    # 显式 null ⇒ 清除
    assert svc.upsert("reasoning", [pid], thinking_override=None)[
        "thinking_override"
    ] is None
    # 键缺席 ⇒ 仍是「跟随档案」（行已存在且现值为 None）
    assert svc.upsert("reasoning", [pid])["thinking_override"] is None
    # 新行 + 键缺席 ⇒ NULL（跟随档案）
    assert svc.upsert("light", [pid])["thinking_override"] is None
    assert _binding_row(db, "light")["thinking_override"] is None
    assert UNSET_THINKING_OVERRIDE is not None  # 哨兵仍在用（不是死代码）
