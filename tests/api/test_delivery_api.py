"""交付判定 API 集成测试（GET /api/projects/{pid}/delivery-verdict）。

覆盖：
- 逐章 rows + 项目 roll-up 的形状与档位；
- 「证据缺失」必须落 not_evaluated 理由（不能因为量不出来就报 deliverable）；
- PLANNED 章显式 not_a_candidate（不是 not_deliverable）；
- confirm 档由 ``issue_gate`` 重推（落库报告没有 ``gate`` 字段——历史报告形状）；
- project 不存在 → 404。

测试模式与 tests/api/test_signing_check_api.py 一致：httpx.ASGITransport + 临时 db。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

#: 干净章的开篇：冲突词（羞辱/震惊）+ 对话句 + 主角名 + 金手指词，全在 1000 字内，
#: 保证 signing_check 的黄金三章三项都不落 fail。
_CLEAN_HEAD = "李晨被人当面羞辱，全场震惊。「你算什么？」他咬牙立誓。系统绑定，面板浮现。"
_TARGET_VISIBLE = 2500


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "交付判定") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_protagonist(app, pid: str, name: str) -> None:
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters
                (character_id, project_id, name, role, core_json,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 'protagonist', '{}', 'PUBLIC', NULL, ?, ?)
            """,
            (new_id("char"), pid, name, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _clean_prose(visible: int = _TARGET_VISIBLE) -> str:
    """恰好 ``visible`` 个可见字的正文（开篇满足黄金三章机检）。"""
    pad = max(0, visible - len(_CLEAN_HEAD))
    return _CLEAN_HEAD + "字" * pad


async def _make_chapter(
    app,
    pid: str,
    number: int,
    *,
    content: str | None = None,
    status: str = "COMMITTED",
    expected_word_count: int = _TARGET_VISIBLE,
) -> str:
    """建章（可选草稿 + 直接推进状态，绕开状态机，与既有测试 fixture 同款）。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters", json={"number": number, "title": f"第{number}章"}
    )
    assert r.status_code == 201, r.text
    cid = r.json()["chapter_id"]
    conn = get_connection(app.state.settings.db_path)
    try:
        if content is not None:
            conn.execute(
                """
                INSERT INTO drafts
                    (draft_id, chapter_id, version, content, created_by,
                     prompt_version, model_id, created_at)
                VALUES (?, ?, 1, ?, 'writer:v1', NULL, NULL, ?)
                """,
                (new_id("dr"), cid, content, now_iso()),
            )
        conn.execute(
            "UPDATE chapters SET status = ?, plan_json = ? WHERE chapter_id = ?",
            (
                status,
                json.dumps({"expected_word_count": expected_word_count}, ensure_ascii=False),
                cid,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


async def _insert_quality_report(
    app,
    pid: str,
    cid: str,
    *,
    issues: list[dict] | None = None,
    overall: int = 90,
    draft_version: int | None = 1,
    meta_extra: dict | None = None,
) -> str:
    """直插一行 quality_report（issues 不带 ``gate`` 字段 = 历史报告形状）。"""
    report_id = new_id("qr")
    meta = {"evaluated_at": now_iso(), "scoring_version": "quality-scoring-v0"}
    meta.update(meta_extra or {})
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id,
                 overall, scores_json, issues_json, created_at, draft_version)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                pid,
                cid,
                overall,
                json.dumps({"overall": overall, "_meta": meta}, ensure_ascii=False),
                json.dumps(issues or [], ensure_ascii=False),
                now_iso(),
                draft_version,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return report_id


_WARNING_ISSUE = {
    "severity": "warning",
    "category": "payoff",
    "rule_id": "RULE_H1_NO_END_HOOK",
    "message": "末 200 字未命中任何钩子标记",
}
# confirm 档的 trigram 命中（30.37% = 事故实测值）。2026-09-18 起该规则不在
# ``CONFIRM_RULES`` 内——只有超 confirm 阈值的那一条带上修字段 ``gate="confirm"``。
_TRIGRAM_ISSUE = {
    "severity": "warning",
    "category": "style",
    "rule_id": "RULE_STYLE_REPETITION_TRIGRAM",
    "message": "trigram 重复率 30.37% > 确认阈值 25%",
    "evidence_refs": ["他抬起眼×12"],
    "gate": "confirm",
}


def _row(body: dict, number: int) -> dict:
    return next(c for c in body["chapters"] if c["number"] == number)


# --------------------------------------------------------------------------- 正常路径


def test_delivery_endpoint_returns_rows_and_rollup(tmp_path: Path):
    """干净章 deliverable + 未写的章 not_a_candidate → 项目 needs_work。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid1 = await _make_chapter(app, pid, 1, content=_clean_prose())
            await _insert_quality_report(app, pid, cid1, issues=[_WARNING_ISSUE])
            await _make_chapter(app, pid, 2, content=None, status="PLANNED")  # 未写正文

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["project_id"] == pid
            assert body["project_name"] == "交付判定"
            assert "generated_at" in body
            # 顶层契约
            assert body["project_verdict"] == "needs_work"
            assert {s["key"] for s in body["evidence_sources"]} == {
                "quality_reports",
                "length_band",
                "signing_check",
            }
            assert all(s["reachable"] for s in body["evidence_sources"])
            assert body["signing_check_scope"]

            row1 = _row(body, 1)
            assert row1["verdict"] == "deliverable"
            assert row1["blocking_reason"] is None
            assert row1["status"] == "COMMITTED"
            assert row1["length"]["visible_chars"] == _TARGET_VISIBLE
            assert row1["length"]["band_low"] == 2125 and row1["length"]["band_high"] == 2875
            assert row1["length"]["tier"] == "in_band"
            assert row1["quality"]["overall"] == 90
            assert row1["signing"]["scope"] == "in_scope"
            # warning 级 issue 可见但不降级
            assert [i["rule_id"] for i in row1["quality"]["issues"]] == ["RULE_H1_NO_END_HOOK"]
            assert row1["quality"]["issues"][0]["gate"] == "auto"

            row2 = _row(body, 2)
            assert row2["verdict"] == "not_a_candidate"
            assert row2["reasons"][0]["rule_id"] == "DELIVERY_CHAPTER_NOT_WRITTEN"

            roll = body["roll_up"]
            assert roll["chapter_count"] == 2
            assert roll["written_chapter_count"] == 1
            assert roll["deliverable_count"] == 1
            assert roll["not_a_candidate_count"] == 1
            assert roll["deliverable_chapter_ids"] == [cid1]
            assert roll["not_deliverable_count"] == 0
            # 未写的章也给出显式原因，而不是静默
            assert list(roll["blocking_reason_by_chapter"]) == [row2["chapter_id"]]
            assert "还不是交付对象" in roll["blocking_reason_by_chapter"][row2["chapter_id"]]
            assert [x["rule_id"] for x in body["project_reasons"]] == [
                "DELIVERY_PROJECT_INCOMPLETE"
            ]

    asyncio.run(run())


def test_confirm_tier_issue_needs_work_and_gate_is_rederived(tmp_path: Path):
    """事故量级 trigram（带上修字段）→ gate 由 issue_gate 重推为 confirm → needs_work。

    分档一律走 ``issue_gate`` 权威重推（不读落库 ``gate`` 字段值做判断），故历史报告
    与新报告同一套解析；差别只在「历史报告没有上修字段 ⇒ 重推为 auto」。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid = await _make_chapter(app, pid, 1, content=_clean_prose())
            await _insert_quality_report(app, pid, cid, issues=[_TRIGRAM_ISSUE])

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            body = r.json()

            row = _row(body, 1)
            assert row["verdict"] == "needs_work"
            assert row["quality"]["issues"][0]["gate"] == "confirm"
            reason = next(
                x for x in row["reasons"] if x["rule_id"] == "RULE_STYLE_REPETITION_TRIGRAM"
            )
            assert reason["status"] == "confirm"
            assert "他抬起眼×12" in reason["evidence"]
            assert row["blocking_reason"] == reason["message"]
            assert body["project_verdict"] == "needs_work"

    asyncio.run(run())


def test_explicit_acceptance_in_report_unblocks_confirm_tier(tmp_path: Path):
    """报告 ``_meta.gate_accepted_override`` 覆盖该 rule_id → 章回到 deliverable。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid = await _make_chapter(app, pid, 1, content=_clean_prose())
            await _insert_quality_report(
                app,
                pid,
                cid,
                issues=[_TRIGRAM_ISSUE],
                meta_extra={
                    "gate_accepted_override": {
                        "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
                        "reason": "刻意的排比",
                        "accepted_at": now_iso(),
                    }
                },
            )

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            row = _row(r.json(), 1)

            assert row["verdict"] == "deliverable"
            reason = next(
                x for x in row["reasons"] if x["rule_id"] == "RULE_STYLE_REPETITION_TRIGRAM"
            )
            assert reason["status"] == "accepted"
            assert "刻意的排比" in reason["message"]

    asyncio.run(run())


# --------------------------------------------------------------------------- 诚实面


def test_missing_quality_report_is_reported_as_not_evaluated(tmp_path: Path):
    """没有质量报告的已写章 → 显式 not_evaluated，判定不得是 deliverable。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            await _make_chapter(app, pid, 1, content=_clean_prose())  # 故意不插 quality_report

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            row = _row(r.json(), 1)

            assert row["verdict"] == "needs_work"
            assert row["verdict"] != "deliverable"
            assert row["quality"] is None
            reason = next(
                x for x in row["reasons"] if x["rule_id"] == "DELIVERY_QUALITY_REPORT_MISSING"
            )
            assert reason["status"] == "not_evaluated"
            assert "未评估" in reason["message"]

    asyncio.run(run())


def test_stale_quality_report_is_reported_as_not_evaluated(tmp_path: Path):
    """报告评 v1、当前最新正文 v2（作者手改过）→ 报告不能代表交付文本。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid = await _make_chapter(app, pid, 1, content=_clean_prose())
            await _insert_quality_report(app, pid, cid, issues=[_WARNING_ISSUE], draft_version=1)
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO drafts
                        (draft_id, chapter_id, version, content, created_by,
                         prompt_version, model_id, created_at)
                    VALUES (?, ?, 2, ?, 'human', NULL, NULL, ?)
                    """,
                    (new_id("dr"), cid, _clean_prose(2400), now_iso()),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            row = _row(r.json(), 1)

            assert row["draft_version"] == 2
            assert row["quality"]["draft_version"] == 1
            assert row["verdict"] == "needs_work"
            reason = next(
                x for x in row["reasons"] if x["rule_id"] == "DELIVERY_QUALITY_REPORT_STALE"
            )
            assert reason["status"] == "not_evaluated"

    asyncio.run(run())


def test_length_out_of_band_is_needs_work_and_beyond_edge_is_not_deliverable(tmp_path: Path):
    """1791 字（出带未超阈）→ needs_work；900 字（超阈）→ not_deliverable。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid1 = await _make_chapter(app, pid, 1, content=_clean_prose(1791))
            await _insert_quality_report(app, pid, cid1, issues=[_WARNING_ISSUE])
            cid2 = await _make_chapter(app, pid, 2, content=_clean_prose(900))
            await _insert_quality_report(app, pid, cid2, issues=[_WARNING_ISSUE])

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            body = r.json()

            row1 = _row(body, 1)
            assert row1["verdict"] == "needs_work"
            assert row1["length"]["tier"] == "warn"
            assert row1["length"]["status"] == "under"
            assert row1["blocking_reason"].startswith("字数 1791 不在目标带")

            row2 = _row(body, 2)
            assert row2["verdict"] == "not_deliverable"
            assert row2["length"]["tier"] == "error"
            assert "硬口径不达标" in row2["blocking_reason"]

            assert body["project_verdict"] == "not_deliverable"
            assert body["project_reasons"][0]["rule_id"] == "DELIVERY_PROJECT_NOT_DELIVERABLE"

    asyncio.run(run())


def test_planned_chapter_is_not_a_candidate(tmp_path: Path):
    """全 PLANNED 的项目 → not_a_candidate（不是 not_deliverable）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_chapter(app, pid, 1, content=None, status="PLANNED")

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            body = r.json()

            row = _row(body, 1)
            assert row["verdict"] == "not_a_candidate"
            assert row["length"] is None
            assert row["quality"] is None
            assert body["project_verdict"] == "not_a_candidate"
            assert body["project_reasons"][0]["rule_id"] == "DELIVERY_PROJECT_EMPTY"
            assert "不是可交付对象" in body["project_reasons"][0]["message"]

    asyncio.run(run())


def test_out_of_scope_signing_for_late_chapter(tmp_path: Path):
    """第 4 章：签约体检标记 out_of_scope（显式），不影响判定。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_protagonist(app, pid, "李晨")
            cid = await _make_chapter(app, pid, 4, content=_clean_prose())
            await _insert_quality_report(app, pid, cid, issues=[_WARNING_ISSUE])

            r = await _request(app, "GET", f"/api/projects/{pid}/delivery-verdict")
            assert r.status_code == 200, r.text
            row = _row(r.json(), 4)

            assert row["verdict"] == "deliverable"
            assert row["signing"]["scope"] == "out_of_scope"
            assert row["signing"]["items"] == []
            assert any(
                x["rule_id"] == "DELIVERY_SIGNING_CHECK_OUT_OF_SCOPE" for x in row["reasons"]
            )

    asyncio.run(run())


def test_delivery_endpoint_404_for_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_nope/delivery-verdict")
            assert r.status_code == 404
            assert "not found" in r.json()["detail"].lower()

    asyncio.run(run())
