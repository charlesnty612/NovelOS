"""手工提交路径的「定稿版草稿」透传（D-10 · 2026-10-23 · 对抗审查 A 的 B2 补测）。

## 缺口

D-10 让 ``commits`` 表新增 ``draft_version`` 列（迁移 0034），记「本次提交定稿的是
**哪一版**草稿」。但**只有 workflow 侧那条路径真的写了它**
（``packages/workflows/chapter_commit/commit.py::_reviewed_draft_version_of`` 读
``ctx["quality_reviewed_draft_version"]``）；**API 手工提交路径**
（``POST /projects/{pid}/commits``）此前**根本不传这个参数** ⇒ 该路径落库的
``draft_version`` 恒为 NULL ⇒ 导出侧（``packages/core/exporter/builder.py``）只能回退
取「最新一版」⇒ **外发物不一定是人审过的那一版**。

这正是 AGENTS.md 硬规则 9 配套纪律那条形状：**「端点收下的参数必须有消费方测试」**
——「收下即忘」与「表里有值」是同一级问题（静默丢弃要么实现，要么 400）。

## 本文件钉住的机制

1. 端点**收下** ``draft_version`` ⇒ 落库（读回 ``GET /commits`` 的裸行验证）；
2. 不传 ⇒ 恒为 ``None``（**存量行为逐字不变**，不是「取最新」）；
3. 传**非整数** ⇒ **422 且不产生 commit 行**（不得静默丢弃后照常提交）。

零真实 LLM 调用、零内容仓接触：全程临时库 + 合成夹具。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api.test_story_state_write_response_contract import (
    _request,
    _rich_delta,
    _seeded,
)


async def _commit_rows(app, pid: str) -> list[dict]:
    r = await _request(app, "GET", f"/api/projects/{pid}/commits")
    assert r.status_code == 200, r.text
    return r.json()


def _row_for(rows: list[dict], delta_id: str) -> dict:
    hit = [row for row in rows if row.get("delta_id") == delta_id]
    assert len(hit) == 1, f"按 delta_id 找 commit 行失败：命中 {len(hit)} 条"
    return hit[0]


@pytest.mark.anyio
async def test_commit_endpoint_persists_draft_version(tmp_path: Path):
    """端点收下 ``draft_version`` ⇒ 真的落库（读回裸行验证，不是查代码）。"""
    app, pid, char, chap, base = await _seeded(tmp_path)

    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text

    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            "workflow_run_id": delta["workflow_run_id"],
            "draft_version": 7,
        },
    )
    assert r.status_code == 201, r.text

    rows = await _commit_rows(app, pid)
    row = _row_for(rows, delta["delta_id"])
    assert row["draft_version"] == 7, f"draft_version 未落库：{row.get('draft_version')!r}"


@pytest.mark.anyio
async def test_commit_endpoint_without_draft_version_stores_null(tmp_path: Path):
    """不传 ⇒ 恒为 ``None``（**存量行为逐字不变**，不是「取最新」）。"""
    app, pid, char, chap, base = await _seeded(tmp_path)

    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text

    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={"delta_id": delta["delta_id"], "workflow_run_id": delta["workflow_run_id"]},
    )
    assert r.status_code == 201, r.text

    rows = await _commit_rows(app, pid)
    row = _row_for(rows, delta["delta_id"])
    assert row["draft_version"] is None, f"未指定时不该有取值：{row.get('draft_version')!r}"


@pytest.mark.anyio
async def test_commit_endpoint_rejects_non_integer_draft_version(tmp_path: Path):
    """传非整数 ⇒ **422**，且**不产生 commit 行**（不得静默丢弃后照常提交）。"""
    app, pid, char, chap, base = await _seeded(tmp_path)

    delta = _rich_delta(chap, char, base, "破屋")
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text

    rows_before = await _commit_rows(app, pid)

    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            "workflow_run_id": delta["workflow_run_id"],
            "draft_version": "not-an-int",
        },
    )
    assert r.status_code == 422, r.text

    rows_after = await _commit_rows(app, pid)
    assert len(rows_after) == len(rows_before), "被拒的提交不得留下 commit 行"
    assert not [row for row in rows_after if row.get("delta_id") == delta["delta_id"]]
