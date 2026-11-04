"""批次三十六 · 批 3（评审/提交门禁域）：C-31 / C-38 / C-42 的判别用例。

三条各自独立的失效方向：

* **C-31** 脏 ``issues_json``（形状合法、Literal 表外值）⇒ 质量报告端点 500。
  修法 = 读路径逐条降级（``QualityService._sanitize_issue_literals``）。
  判别：脏行 ⇒ 端点 200 + 脏字段退安全值；**同批的干净行原样返回**。
* **C-38** ``save_report`` 的「取最新」兜底与不可检路径叠加 ⇒ 报告标签指向一份
  从未被量过的正文。修法 = 不可检时跳过补全（``draft_version`` 保持 ``None``）。
  判别：不可检报告落库后 ``draft_version is None``，而正常报告仍照旧补最新。
* **C-42** 质量评估端点收可选 ``draft_version``（query）。判别：传存在的版本 ⇒
  量该版；传不存在的版本 ⇒ 不可检硬停语义（批次三十五）；不传 ⇒ 评最新（现状不变）。

⚠️ 所有断言打在**真实值**上（响应实际状态码 / 实际字段值 / DB 实际列），
临时库 + 合成夹具，零真实 LLM 调用，不碰 ``data/novelos.db``。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import packages.core.quality.service as service_mod
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.models import QualityReport
from packages.core.quality.scoring import DRAFT_UNAVAILABLE
from packages.core.quality.service import QualityService, build_quality_context
from tests._db_fixtures import migrated_db
from tests.unit.quality.test_engine import _full_delta

#: 「正文不可检」的 rule_id + message 前缀（与 scoring 侧已登记的判别口径一致）。
UNAVAILABLE_RULE_ID = "scoring_missing_subscore"
UNAVAILABLE_MESSAGE_PREFIX = "正文读不到"


# ---------------------------------------------------------------------------
# fixtures（临时库 + 合成数据）
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    return str(migrated_db(tmp_path))


def _project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="批三十六 批3"))["project_id"]


def _chapter(db_path: str, pid: str, *, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', '{}', 'REVIEWED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _draft(db_path: str, cid: str, version: int, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'human', ?)",
            (new_id("drf"), cid, version, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_report(
    db_path: str,
    *,
    pid: str,
    cid: str,
    issues: object,
    overall: int = 66,
) -> str:
    """直接 INSERT 一行 ``quality_reports``（绕过 service，模拟**手工改库**）。"""
    report_id = new_id("qr")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, overall, scores_json,
                 issues_json, draft_version, created_at)
            VALUES (?, ?, ?, ?, '{}', ?, NULL, ?)
            """,
            (
                report_id,
                pid,
                cid,
                overall,
                json.dumps(issues, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return report_id


# ---------------------------------------------------------------------------
# C-31：脏 issues_json 不得让整个列表端点 500
# ---------------------------------------------------------------------------


def test_dirty_issue_literals_degrade_instead_of_500(tmp_path: Path):
    """脏行 ⇒ 两个 GET 端点都 200；脏字段退安全值，干净行逐字不变。"""
    from tests.api._shared import _create_app, _make_client

    app = _create_app(tmp_path)
    db_path = str(app.state.settings.db_path)
    pid = _project(db_path)
    cid_dirty = _chapter(db_path, pid, number=1)
    cid_clean = _chapter(db_path, pid, number=2)

    dirty_issues = [
        {
            "severity": "catastrophic",  # 表外
            "category": "made_up_category",  # 表外
            "rule_id": "RULE_B36_DIRTY",
            "message": "手工改库写进来的脏值",
            "gate": "maybe",  # 表外
            "suggestion": "其余字段必须原样返回",
        },
        {
            "severity": "error",
            "category": "continuity",
            "rule_id": "RULE_B36_CLEAN",
            "message": "干净条目",
            "gate": "block",
        },
        "not-a-dict",  # 非对象元素
    ]
    clean_issues = [
        {
            "severity": "warning",
            "category": "style",
            "rule_id": "RULE_B36_PRISTINE",
            "message": "另一行的干净条目",
            "gate": "confirm",
        }
    ]
    _insert_report(db_path, pid=pid, cid=cid_dirty, issues=dirty_issues)
    _insert_report(db_path, pid=pid, cid=cid_clean, issues=clean_issues, overall=80)

    async def run():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                # 列表端点：改造前一行脏数据会让整个请求 500
                r = await client.get(f"/api/projects/{pid}/quality")
                assert r.status_code == 200, r.text
                rows = r.json()
                assert len(rows) == 2, rows
                by_chapter = {row["chapter_id"]: row for row in rows}

                dirty_row = by_chapter[cid_dirty]
                issues = dirty_row["issues_json"]
                # 非 dict 元素被丢弃 ⇒ 只剩两条
                assert len(issues) == 2, issues
                assert issues[0]["severity"] == "warning"
                assert issues[0]["category"] == "style"
                assert issues[0]["gate"] == "auto"
                # 未被降级的字段原样保留
                assert issues[0]["rule_id"] == "RULE_B36_DIRTY"
                assert issues[0]["suggestion"] == "其余字段必须原样返回"
                # 本来就合法的条目：原值逐字不变（响应模型会补可选字段默认值，
                # 故只断言本用例关心的键，不做整 dict 相等——那会变成断言模型默认值）
                assert issues[1]["severity"] == "error"
                assert issues[1]["category"] == "continuity"
                assert issues[1]["rule_id"] == "RULE_B36_CLEAN"
                assert issues[1]["message"] == "干净条目"
                assert issues[1]["gate"] == "block"

                # 干净行不受任何影响
                clean_row = by_chapter[cid_clean]
                assert clean_row["overall"] == 80
                assert len(clean_row["issues_json"]) == 1
                clean_issue = clean_row["issues_json"][0]
                assert clean_issue["severity"] == "warning"
                assert clean_issue["category"] == "style"
                assert clean_issue["rule_id"] == "RULE_B36_PRISTINE"
                assert clean_issue["gate"] == "confirm"

                # 单体端点同样 200（同一读路径）
                r = await client.get(f"/api/chapters/{cid_dirty}/quality")
                assert r.status_code == 200, r.text
                assert r.json()["issues_json"][0]["category"] == "style"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# C-38：不可检报告不得把 draft_version 补成「最新」
# ---------------------------------------------------------------------------


def test_unavailable_report_does_not_backfill_latest_draft_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """不可检 + 无显式版本 + 「最新版」恰好读得到 ⇒ 报告版本必须保持 ``None``。

    构造的是真实叠加面：``compute_char_stats_detail`` 故障 ⇒ ``draft_unavailable``；
    而 ``resolve_draft``（最新版）本身**可用**——所以改造前 ``save_report`` 会把
    ``draft_version`` 补成最新版 1，让报告标签指向一份**从未被量过**的正文。
    """
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _draft(db_path, cid, 1, "第一章正文。")

    def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("drafts 统计不可用（合成故障）")

    monkeypatch.setattr(service_mod, "compute_char_stats_detail", _boom)

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_full_delta(cid),
        snapshot_pre={},
        # 不传 draft_version：叠加面正是「不可检 + 无显式版本」
    )
    assert ctx.draft == DRAFT_UNAVAILABLE
    assert ctx.draft_version is None

    report = QualityEngine().evaluate(ctx)
    assert report.draft_version is None
    assert any(
        i.rule_id == UNAVAILABLE_RULE_ID
        and i.message.startswith(UNAVAILABLE_MESSAGE_PREFIX)
        for i in report.issues
    )

    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)
    stored = QualityService(db_path).latest_report(cid)
    assert stored is not None
    # 改造前这里是 1（「取最新」兜底）——标签指向未量过的正文
    assert stored["draft_version"] is None, stored


def test_normal_report_without_version_still_backfills_latest(tmp_path: Path):
    """C-38 反向不变量：正常报告（无不可检标记）的「取最新」兜底**逐字保留**。"""
    db_path = _db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _draft(db_path, cid, 1, "第一章正文。")
    _draft(db_path, cid, 2, "第二章正文。")

    report = QualityReport(chapter_id=cid, chapter_number=1, overall=70)
    assert report.draft_version is None
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)

    stored = QualityService(db_path).latest_report(cid)
    assert stored is not None
    assert stored["draft_version"] == 2


# ---------------------------------------------------------------------------
# C-42：evaluate 端点收可选 draft_version（query）
# ---------------------------------------------------------------------------


def test_evaluate_endpoint_accepts_optional_draft_version(tmp_path: Path):
    """不传 ⇒ 评最新；传存在的版本 ⇒ 量该版；传不存在的版本 ⇒ 不可检硬停语义。"""
    from tests.api._shared import _create_app, _make_client

    app = _create_app(tmp_path)
    db_path = str(app.state.settings.db_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _draft(db_path, cid, 1, "第一版正文：" + "他把窗子推开，风灌进来。" * 5)
    _draft(db_path, cid, 2, "第二版正文：" + "桌上的纸角翻了两下就压住了。" * 5)

    async def run():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                # ① 不传 ⇒ 现行为不变：评最新（v2）
                r = await client.post(f"/api/chapters/{cid}/quality/evaluate")
                assert r.status_code == 201, r.text
                assert r.json()["draft_version"] == 2, r.json()

                # ② 传存在的版本 ⇒ 报告标记与内容都取那一版
                r = await client.post(
                    f"/api/chapters/{cid}/quality/evaluate",
                    params={"draft_version": 1},
                )
                assert r.status_code == 201, r.text
                assert r.json()["draft_version"] == 1, r.json()

                # ③ 传不存在的版本 ⇒ 批次三十五 fail-closed：201 + overall=0 +
                #    阻断级不可检记录（不是「量空正文拿满分」）
                r = await client.post(
                    f"/api/chapters/{cid}/quality/evaluate",
                    params={"draft_version": 999},
                )
                assert r.status_code == 201, r.text
                body = r.json()
                assert body["overall"] == 0, body
                assert body["draft_version"] == 999, body
                unavailable = [
                    i for i in body["issues_json"]
                    if i["rule_id"] == UNAVAILABLE_RULE_ID
                    and i["message"].startswith(UNAVAILABLE_MESSAGE_PREFIX)
                ]
                assert unavailable, body["issues_json"]

    asyncio.run(run())
