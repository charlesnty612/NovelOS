"""`GET /arc` 的 response_model 契约测试（2026-10-15 批次（五））。

背景
----
`build_arc_view` 此前返回裸 dict、router 无 ``response_model`` ⇒ OpenAPI 无 schema ⇒
前端只能在 `apps/web/src/api/types.ts` 手写 `ArcView` 而无任何机器校验。

本组用例钉住三件事：

1. OpenAPI 里存在 ``ArcView`` schema 且被该端点引用；
2. **端点实际返回的键集与模型字段集逐字相等**——不是「超集包含」。
   这是防 ``response_model`` 过滤的关键：少声明一个字段，响应就静默少一个，
   而用 ``>=`` 断言的测试照样全绿（本仓既有的 `test_arc_api.py` 用的正是 ``>=``，
   所以**不能**靠它守这件事，本文件才是那道闸门）；
3. ``extra="allow"``——service 随新告警种类加顶层键时不得被静默裁掉。

字段形状的**来源**：临时库 + 合成夹具跑通端点后 dump 实际键集（卷 / 逾期伏笔 /
逾期剧透 / 草稿 / 质量报告全部喂上），不是照源码读出来的。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.arc.models import ArcView
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _client(app) as c:
        return await c.request(method, path, **kwargs)


def _rich_project(app) -> str:
    """跑一遍**富夹具**并把项目 id 交出来（同步驱动，内部自开 event loop）。"""

    import asyncio

    async def run() -> str:
        async with _client(app) as c:
            pid = (
                await c.post("/api/projects", json={"name": "弧光契约"})
            ).json()["project_id"]
            cids = []
            for n, purpose in [(1, "[payoff] 兑现"), (2, "[charge] 蓄力")]:
                r = await c.post(
                    f"/api/projects/{pid}/chapters",
                    json={
                        "number": n,
                        "title": f"第{n}章",
                        "plan_json": {"key_beats": [{"purpose": purpose}]},
                    },
                )
                cids.append(r.json()["chapter_id"])

            # 卷 + 派章（否则 volume_id / volume_number 恒 null，量不到那两个键）
            vol = (
                await c.post(
                    f"/api/projects/{pid}/volumes", json={"number": 1, "title": "第一卷"}
                )
            ).json()
            await c.post(
                f"/api/projects/{pid}/volumes/{vol['volume_id']}/assign",
                json={"chapter_id": cids[0]},
            )

            # 逾期伏笔 / 逾期剧透 / 草稿 / 质量报告：把每个可空字段都喂成非空，
            # 这样「键缺席」与「键存在但为 null」两种情况都能被第 2 条断言分辨开。
            await c.post(
                f"/api/projects/{pid}/hooks",
                json={"name": "三年之约", "expected_payoff_chapter_id": cids[0]},
            )
            char = await c.post(f"/api/projects/{pid}/characters", json={"name": "林昭"})
            await c.post(
                f"/api/projects/{pid}/reveal-policies",
                json={
                    "target_kind": "character",
                    "target_id": char.json()["character_id"],
                    "reveal_by_chapter": 1,
                },
            )
            for cid in cids:
                await c.patch(f"/api/chapters/{cid}", json={"status": "DRAFTED"})
                await c.post(f"/api/chapters/{cid}/drafts", json={"content": "正文" * 40})

            conn = get_connection(app.state.settings.db_path)
            try:
                for cid in cids:
                    conn.execute(
                        """INSERT INTO quality_reports
                           (report_id, project_id, chapter_id, commit_id, run_id,
                            overall, scores_json, issues_json, created_at)
                           VALUES (?,?,?,NULL,NULL,?,?,'[]',?)""",
                        (
                            new_id("qr"),
                            pid,
                            cid,
                            80,
                            json.dumps({"overall": 80, "pacing": 80}),
                            now_iso(),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
            return pid

    return asyncio.run(run())


def test_arc_view_schema_declared_and_referenced(tmp_path: Path):
    """1) OpenAPI 里有 ArcView schema，且被 GET /arc 引用。"""
    app = _create_app(tmp_path)
    schema = app.openapi()
    assert "ArcView" in schema["components"]["schemas"], "ArcView 未进 OpenAPI"
    ref = schema["paths"]["/api/projects/{project_id}/arc"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ArcView"), ref


# ⚠️ 期望键集**硬编码在这里**，不写成 `set(ArcView.model_fields)`。
# 首版就是自指的（拿响应和模型比），突变验证 M14 实测存活：把 `volume_number`
# 从模型里删掉，响应与期望**同时**少一个键 ⇒ 仍然「逐字相等」⇒ 断言恒真。
# ⇒ 「防静默丢字段」必须有**模型之外的第三方基准**，否则它测的是「模型自洽」
# 而不是「契约没漂」。见 AGENTS 坑区「断言了结果 ≠ 判别了机制」。
EXPECTED_ARC_KEYS = {
    "project_id",
    "generated_at",
    "chapters",
    "payoff",
    "hooks",
    "debts",
    "reveal_policies",
    "volumes",
    "alerts",
}
EXPECTED_ARC_CHAPTER_KEYS = {
    "number",
    "title",
    "has_payoff_beat",
    "charge_beat",
    "overall",
    "pacing",
    "prose_chars",
    "volume_id",
    "volume_number",
}
EXPECTED_PAYOFF_KEYS = {"total", "charge_streak", "max_charge_streak", "last_payoff_chapter"}
EXPECTED_HOOK_KEYS = {"open", "resolved", "overdue"}
EXPECTED_DEBT_KEYS = {"open", "paid"}
EXPECTED_REVEAL_COUNT_KEYS = {"planned", "revealed", "cancelled", "overdue"}
EXPECTED_OVERDUE_REVEAL_KEYS = {
    "policy_id",
    "target_kind",
    "target_id",
    "reveal_by_chapter",
    "audience",
}
EXPECTED_VOLUME_KEYS = {"volume_id", "number", "title", "status", "chapter_count"}
EXPECTED_ALERT_KEYS = {"level", "code", "message"}


def test_arc_response_keys_match_hardcoded_expectation(tmp_path: Path):
    """2) 实际返回的键集与**硬编码期望**逐字相等。

    两侧都对：撤模型字段（M14）会让响应少键 ⇒ 红；响应侧漂移也会红。
    """
    app = _create_app(tmp_path)
    pid = _rich_project(app)
    r = asyncio_run(_request(app, "GET", f"/api/projects/{pid}/arc"))
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body) == EXPECTED_ARC_KEYS, (
        f"顶层键不符：多={set(body) - EXPECTED_ARC_KEYS} 少={EXPECTED_ARC_KEYS - set(body)}"
    )

    # 章节行：逐字相等，且**不得**出现 chapter_id（后端刻意脱敏）
    assert body["chapters"], "夹具应至少产出一章"
    for row in body["chapters"]:
        assert set(row) == EXPECTED_ARC_CHAPTER_KEYS, (
            f"章节行键不符：多={set(row) - EXPECTED_ARC_CHAPTER_KEYS} "
            f"少={EXPECTED_ARC_CHAPTER_KEYS - set(row)}"
        )
        assert "chapter_id" not in row, "后端刻意不暴露 chapter_id，出现即契约漂移"

    assert set(body["payoff"]) == EXPECTED_PAYOFF_KEYS
    assert set(body["hooks"]) == EXPECTED_HOOK_KEYS
    assert set(body["debts"]) == EXPECTED_DEBT_KEYS
    assert set(body["reveal_policies"]) == EXPECTED_REVEAL_COUNT_KEYS
    for row in body["reveal_policies"]["overdue"]:
        assert set(row) == EXPECTED_OVERDUE_REVEAL_KEYS
    for row in body["volumes"]:
        assert set(row) == EXPECTED_VOLUME_KEYS
    for row in body["alerts"]:
        assert set(row) == EXPECTED_ALERT_KEYS


def test_arc_model_fields_stay_in_sync_with_expectation():
    """2b) 模型字段集与硬编码期望一致（两侧都变不算漂移，这里再钉一道）。"""
    assert set(ArcView.model_fields) == EXPECTED_ARC_KEYS
    from packages.core.arc.models import ArcChapterRow

    assert set(ArcChapterRow.model_fields) == EXPECTED_ARC_CHAPTER_KEYS


def test_arc_extra_top_level_key_is_not_stripped(tmp_path: Path):
    """3) ``extra="allow"``：service 新增顶层键时不得被静默裁掉。

    直接对模型断言即可（路由层走同一套序列化）——去掉 `extra` 必红。
    """
    body = {
        "project_id": "p1",
        "generated_at": "2026-10-15T00:00:00Z",
        "chapters": [],
        "payoff": {
            "total": 0,
            "charge_streak": 0,
            "max_charge_streak": 0,
            "last_payoff_chapter": None,
        },
        "hooks": {"open": 0, "resolved": 0, "overdue": 0},
        "debts": {"open": 0, "paid": 0},
        "reveal_policies": {"planned": 0, "revealed": 0, "cancelled": 0, "overdue": []},
        "volumes": [],
        "alerts": [],
        "brand_new_section": {"x": 1},
    }
    dumped = ArcView(**body).model_dump()
    assert dumped["brand_new_section"] == {"x": 1}, "新增顶层键被裁掉了 ⇒ extra 没了"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
