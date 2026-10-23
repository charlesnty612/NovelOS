"""三个**列表端点**的 response_model 契约测试（2026-10-15 批次（六）· L4）。

背景：上一轮补的是**单体端点**。列表端点（``-> list[dict]``）在 OpenAPI 里 items
是匿名 object::

    {"type": "array", "items": {"type": "object", "additionalProperties": true}}

⇒ 前端拿到的仍是 ``Record<string, unknown>``。本批的验收口径是：**``$ref`` 或
``array`` 且 ``items`` 有 ``$ref`` 才算已建模**——见
:func:`test_every_modeled_list_declares_a_named_item_ref`。

本组测试的四条方法论纪律（AGENTS.md §五坑区，是踩坑产物，不是风格偏好）：

1. **禁自指断言。** 断言「响应键集 == 模型字段集」恒真：把字段从模型里删掉，两侧
   同时变小，仍然「相等」（批次（五）突变 M14 实测存活）。
   ⇒ 全部键集断言都对下面的 ``EXPECTED_*`` 常量，那些常量逐个注明**独立来源**
   （DDL / service docstring / 投影字面量），**不是**从跑出来的响应里抄的。

2. **``extra="allow"`` 会吃掉「声明被删」的信号。** 删掉一个字段声明，响应照样有
   那个键（以 extra 身份透出），只有 OpenAPI 的 ``properties`` 会变（M20）。
   ⇒ 凡模型开了 ``extra="allow"``，本文件**都另**断言 OpenAPI schema。

3. **只断言「schema 里存在这个模型」抓不到接线。** 模型被别的端点引用着，摘掉本
   端点的 ``response_model`` 照样全绿（坑区「断言了结果 ≠ 判别了机制」的同族）。
   ⇒ 每条端点都断言 ``path → array → items.$ref`` 的**逐跳**指向。

4. **「应该和单体一样」不是证据。** 同一批实测到反例：``GET /commits`` 列表行 13 键
   而 ``POST /commits`` 响应只有 4 键。本文件里唯一「碰巧同形」的一对（评审列表 vs
   评审单体）是用**实测 + 差集断言**钉住的，另有一对**不同形**的（canon 列表 vs
   canon 单体，两边都是 9 键但键名不同）专门用反例钉住。

⚠️ **零 LLM 调用**：三条端点都不跑任何工作流／模型调用。canon 直接 INSERT 合成
（**绝不能真跑 deconstruct**），quality_report 与文风样例同样直接 INSERT。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.models import QualityReportResponse
from packages.domain.reference.models import CanonSummaryOut

# ---------------------------------------------------------------------------
# 第三方基准（硬编码）：每个键的出处写在注释里。
#
# ⚠️ 这些常量**不是**「跑一下看返回什么」得来的——那是坑区「夹具从被测代码的实际输出
# 里抄形状 ⇒ 缺陷被写进断言」的成因。
# ---------------------------------------------------------------------------

# quality_reports 表列集（DDL）：
#   0003_quality_reports.sql   → report_id, project_id, chapter_id, commit_id, run_id,
#                                overall, scores_json, issues_json, created_at
#   0012_judge_scores.sql      → judge_json
#   0024_..._draft_version.sql → draft_version
# 减去被 ``_attach_judge`` 弹出改写的 ``judge_json``，加回 ``judge``。
#
# ⚠️ 与批次（六）· G7 的 EXPECTED_QUALITY_REPORT_KEYS **内容相同但独立声明**：
# 那份在别人的文件里，本组不该 import 别人的测试常量（一方改名/重构就静默漂移）。
EXPECTED_QUALITY_LIST_ROW_KEYS = frozenset(
    {
        "report_id",
        "project_id",
        "chapter_id",
        "commit_id",
        "run_id",
        "overall",
        "scores_json",
        "issues_json",
        "draft_version",
        "created_at",
        "judge",
    }
)

# ``packages/domain/reference/service.py::summary_of`` 的 docstring 明列：
#   「输出：canon_id / project_id / title / reader_profile / status / created_at /
#     logline / spine_count / rhythm_chapter_count 完整 dict」＝ 9 键、无条件键。
#   （前端 apps/web/src/api/types.ts 的手写 CanonSummary 逐字一致——独立佐证。）
EXPECTED_CANON_SUMMARY_KEYS = frozenset(
    {
        "canon_id",
        "project_id",
        "title",
        "reader_profile",
        "status",
        "created_at",
        "logline",
        "spine_count",
        "rhythm_chapter_count",
    }
)

# author_style_samples 表全部 6 列（0008_author_style_samples_and_overdue.sql）；
# 列表 SQL 是**显式 6 列枚举**（不是 SELECT *）。
EXPECTED_STYLE_SAMPLE_LIST_KEYS = frozenset(
    {"sample_id", "project_id", "title", "content", "created_at", "updated_at"}
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """同步用例里发一次请求（端点是 async 的，用例是同步的）。"""

    async def run():
        async with _client(app) as c:
            return await c.request(method, path, **kwargs)

    return asyncio.run(run())


def _project(app, name: str = "L4") -> str:
    async def run():
        async with _client(app) as c:
            r = await c.post("/api/projects", json={"name": name})
            assert r.status_code in (200, 201), r.text
            return r.json()["project_id"]

    return asyncio.run(run())


def _chapter(app, pid: str, number: int = 1) -> str:
    async def run():
        async with _client(app) as c:
            r = await c.post(
                f"/api/projects/{pid}/chapters", json={"number": number, "title": "一"}
            )
            assert r.status_code in (200, 201), r.text
            return r.json()["chapter_id"]

    return asyncio.run(run())


def _insert_report(
    app,
    pid: str,
    cid: str,
    *,
    created_at: str | None = None,
    issues: list | None = None,
    scores: dict | None = None,
    judge: dict | None = None,
) -> str:
    """直接 INSERT 一份 quality_report（合成夹具，零评估、零模型调用）。"""
    rid = new_id("qr")
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """INSERT INTO quality_reports
               (report_id, project_id, chapter_id, commit_id, run_id,
                overall, scores_json, issues_json, judge_json, draft_version, created_at)
               VALUES (?,?,?,NULL,NULL,?,?,?,?,1,?)""",
            (
                rid,
                pid,
                cid,
                80,
                json.dumps(scores or {"overall": 80}),
                json.dumps(issues if issues is not None else [], ensure_ascii=False),
                json.dumps(judge, ensure_ascii=False) if judge else None,
                created_at or now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _seed_canon(app, pid: str, canon_id: str, canon_json: Any, created_at: str) -> None:
    """合成一条 canon 行（**不经 deconstruct 工作流**，零 LLM 调用）。"""
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """INSERT INTO reference_canons
               (canon_id, project_id, title, reader_profile, canon_json,
                report_md, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                canon_id,
                pid,
                "探针书",
                "male_fantasy",
                canon_json if isinstance(canon_json, str) else json.dumps(
                    canon_json, ensure_ascii=False
                ),
                "# 报告",
                "active",
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_sample(app, pid: str, sample_id: str, created_at: str) -> None:
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """INSERT INTO author_style_samples
               (sample_id, project_id, title, content, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (sample_id, pid, f"样例-{sample_id}", "正文", created_at, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _item_ref(schema: dict, path: str, method: str = "get", code: str = "200") -> str:
    """取出 ``path → array → items.$ref``；顺带断言外层确实是数组。

    ⚠️ 只断言「$ref 非空」不够——``$ref`` 可能挂在数组**外面**。逐跳取才抓得到
    「声明成 ``list[dict]``」这种形状。
    """
    op = schema["paths"][path][method]["responses"][code]["content"]["application/json"]
    arr = op["schema"]
    assert arr.get("type") == "array", f"{path} 顶层不是 array：{arr}"
    return arr["items"]["$ref"]


def _props(schema: dict, name: str) -> set[str]:
    return set(schema["components"]["schemas"][name]["properties"])


def _required(schema: dict, name: str) -> set[str]:
    return set(schema["components"]["schemas"][name].get("required", []))


# ===========================================================================
# 0. 空列表：先钉住「空数组不是 null」，并说明它为什么**量不到任何东西**
# ===========================================================================


def test_empty_lists_are_empty_arrays_not_null(tmp_path: Path):
    """三条列表端点在无数据时都返回 ``[]``（不是 ``null``、不是 404）。

    ⚠️ **空数组量不到任何东西**：``body[0]`` 会 IndexError、键集断言全部落空。
    这就是本文件每条键集用例都先造行的原因——写在这里是为了让「忘了造行」的失败
    落在一条**指名道姓**的用例上，而不是散落在某条看似失败的断言里。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    for path in ("/quality", "/canons", "/style-samples"):
        r = _request(app, "GET", f"/api/projects/{pid}{path}")
        assert r.status_code == 200, (path, r.text)
        assert r.json() == [], (path, r.json())


# ===========================================================================
# 1. 评审报告列表 —— GET /api/projects/{project_id}/quality
# ===========================================================================


def test_quality_list_rows_match_the_hardcoded_baseline(tmp_path: Path):
    """实测结论：**11 键恒在**，两行（有/无 judge）逐键同名。

    ``judge`` / ``commit_id`` / ``run_id`` / ``draft_version`` 在无数据时是 ``null``，
    但**键仍在**——这是 ``_attach_judge`` 与 DB 可空列决定的，不是省略。

    判别点：断言对象是 ``EXPECTED_QUALITY_LIST_ROW_KEYS``（DDL 推出来的），
    **不是** ``QualityReportResponse.model_fields``——后者是自指断言。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    with_judge = _insert_report(
        app,
        pid,
        cid,
        created_at="2026-01-01T00:00:00+00:00",
        judge={
            "pacing": 80, "style": 70, "logic": 90, "dialogue": 60,
            "verdict": "还行", "score_avg": 75.0, "dry_run": False,
        },
    )
    _insert_report(app, pid, cid, created_at="2026-02-01T00:00:00+00:00")

    r = _request(app, "GET", f"/api/projects/{pid}/quality")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2, rows
    for row in rows:
        assert set(row) == set(EXPECTED_QUALITY_LIST_ROW_KEYS), (
            f"多={set(row) - EXPECTED_QUALITY_LIST_ROW_KEYS} "
            f"少={EXPECTED_QUALITY_LIST_ROW_KEYS - set(row)}"
        )
    # created_at DESC：有 judge 的那行 created_at 更早 ⇒ 排在后面
    assert [x["report_id"] for x in rows][-1] == with_judge, rows
    # judge 原样透出（含 service 存下的扩展键）
    judge = rows[-1]["judge"]
    assert judge["pacing"] == 80 and judge["logic"] == 90
    assert judge["score_avg"] == 75.0 and judge["dry_run"] is False
    assert rows[0]["judge"] is None


def test_quality_list_rows_and_single_row_are_actually_the_same_shape(tmp_path: Path):
    """⚠️ **「应该和单体一样」不是证据**——本条是把它**量出来**。

    复用 ``QualityReportResponse`` 的前提是「``list_reports`` 与 ``latest_report``
    走同一个 ``SELECT *`` ＋ 同一个 ``_attach_judge``」。这个前提是**可被破坏**的
    （``GET /commits`` 列表 13 键 / ``POST /commits`` 响应 4 键就是同批的反例）。

    ⇒ 这里断言两条响应的**对称差为空集**。一旦有人改了列表的投影而不改模型，本条
    必红——而如果只断言「列表行 == 硬编码基准」，单侧改了投影会先被基准抓到，
    两侧同时改才会被这条抓到。两条断言方向不同，缺一不可。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(app, pid, cid, created_at="2026-01-01T00:00:00+00:00")

    lst = _request(app, "GET", f"/api/projects/{pid}/quality").json()[0]
    one = _request(app, "GET", f"/api/chapters/{cid}/quality").json()
    assert (set(lst) ^ set(one)) == set(), (
        f"列表与单体不同形：列表多={set(lst) - set(one)} 单体多={set(one) - set(lst)}"
    )


def test_quality_list_model_declares_exactly_the_baseline_keys(tmp_path: Path):
    """双保险：模型字段集 vs **硬编码**基准（不是 vs 响应，那会是自指）。

    另断言 ``judge`` 必填——它「恒在、可空」（router 无条件写这个键），给了默认值
    就不是 required 了，而语义上它与 ``QualityJudgeResponse.judge`` 同一口径。
    """
    assert set(QualityReportResponse.model_fields) == set(
        EXPECTED_QUALITY_LIST_ROW_KEYS
    ), (
        f"模型多={set(QualityReportResponse.model_fields) - EXPECTED_QUALITY_LIST_ROW_KEYS} "
        f"模型少={EXPECTED_QUALITY_LIST_ROW_KEYS - set(QualityReportResponse.model_fields)}"
    )

    app = _create_app(tmp_path)
    schema = app.openapi()
    assert _props(schema, "QualityReportResponse") == set(EXPECTED_QUALITY_LIST_ROW_KEYS)
    required = _required(schema, "QualityReportResponse")
    assert "judge" in required, sorted(required)
    # 默认值给了就不进 required——这几列必须可空
    assert {"commit_id", "run_id", "draft_version"} & required == set(), sorted(required)

    # 接线：path → array → items.$ref（只断言「schema 里有这个模型」抓不到）
    ref = _item_ref(schema, "/api/projects/{project_id}/quality")
    assert ref.endswith("/QualityReportResponse"), ref


def test_quality_list_passes_through_future_db_columns(tmp_path: Path):
    """⚠️ 列表行的键集来自 ``SELECT *`` ⇒ 表里加列，响应就多键。

    这是 ``QualityReportResponse`` 必须 ``extra="allow"`` 的理由，且是**列表侧独有**
    的观察面（单体的同类用例由批次（六）· G7 覆盖）：临时库里 ALTER 出一列，验证
    它能从**列表**透出。关掉 ``extra="allow"`` ⇒ 本条必红。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(app, pid, cid)

    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("ALTER TABLE quality_reports ADD COLUMN future_list_col TEXT")
        conn.execute("UPDATE quality_reports SET future_list_col = 'v1'")
        conn.commit()
    finally:
        conn.close()

    r = _request(app, "GET", f"/api/projects/{pid}/quality")
    assert r.status_code == 200, r.text
    assert r.json()[0].get("future_list_col") == "v1", sorted(r.json()[0])


def test_quality_list_limit_param_still_applies(tmp_path: Path):
    """``limit`` 查询参数照常生效（``response_model`` 不得改动分页语义）。"""
    app = _create_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    for i in range(3):
        _insert_report(app, pid, cid, created_at=f"2026-01-0{i + 1}T00:00:00+00:00")

    assert len(_request(app, "GET", f"/api/projects/{pid}/quality").json()) == 3
    assert len(_request(app, "GET", f"/api/projects/{pid}/quality?limit=2").json()) == 2


def test_quality_list_keeps_issue_severity_and_gate_as_two_independent_axes(tmp_path: Path):
    """⚠️ severity 与 gate 是**正交的两根轴**（坑区 P0-1 事故：算出来的问题没人能说不）。

    列表行走的是同一个 ``QualityReportIssue``，故它也必须**原样**透出这两组
    反直觉组合，不许在任何一层按 severity 推 gate：

    - ``severity="error"`` 但规则不在阻断白名单 ⇒ ``gate="auto"``；
    - ``severity="info"`` 但单条被显式上修 ⇒ ``gate="confirm"``。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(
        app,
        pid,
        cid,
        issues=[
            {
                "severity": "error", "category": "style",
                "rule_id": "RULE_L4_NON_BLOCKING", "message": "error 级但不阻断",
                "gate": "auto", "location": "<unknown>",
            },
            {
                "severity": "info", "category": "style",
                "rule_id": "RULE_L4_MUST_ACCEPT", "message": "info 级但必须显式接受",
                "gate": "confirm", "location": "<unknown>",
            },
        ],
    )
    r = _request(app, "GET", f"/api/projects/{pid}/quality")
    assert r.status_code == 200, r.text
    got = {i["rule_id"]: i for i in r.json()[0]["issues_json"]}
    assert got["RULE_L4_NON_BLOCKING"]["severity"] == "error"
    assert got["RULE_L4_NON_BLOCKING"]["gate"] == "auto"
    assert got["RULE_L4_MUST_ACCEPT"]["severity"] == "info"
    assert got["RULE_L4_MUST_ACCEPT"]["gate"] == "confirm"


# ===========================================================================
# 2. 拆书 canon 列表 —— GET /api/projects/{project_id}/canons
# ===========================================================================


def test_canon_list_rows_match_the_hardcoded_baseline(tmp_path: Path):
    """实测结论：9 键恒在、无条件键；三个派生量由 ``canon_json`` 算出。

    判别点：``EXPECTED_CANON_SUMMARY_KEYS`` 来自 ``summary_of`` 的 docstring
    （独立第三方基准），**不是**从响应里抄的。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    _seed_canon(
        app,
        pid,
        "can_rich",
        {
            "logline": "一句话",
            "spine": [{"n": 1}, {"n": 2}, {"n": 3}],
            "emotion_curve": [1, 2, 3, 4, 5],
        },
        created_at="2026-01-01T00:00:00+00:00",
    )
    _seed_canon(app, pid, "can_thin", {}, created_at="2026-02-01T00:00:00+00:00")

    r = _request(app, "GET", f"/api/projects/{pid}/canons")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2, rows
    for row in rows:
        assert set(row) == set(EXPECTED_CANON_SUMMARY_KEYS), (
            f"多={set(row) - EXPECTED_CANON_SUMMARY_KEYS} "
            f"少={EXPECTED_CANON_SUMMARY_KEYS - set(row)}"
        )
    # created_at DESC
    assert [x["canon_id"] for x in rows] == ["can_thin", "can_rich"]
    # 派生量：spine / emotion_curve 取长度；缺失则回落 0 / ""
    rich, thin = rows[1], rows[0]
    assert rich["logline"] == "一句话"
    assert rich["spine_count"] == 3 and rich["rhythm_chapter_count"] == 5
    assert thin["logline"] == ""
    assert thin["spine_count"] == 0 and thin["rhythm_chapter_count"] == 0
    assert rich["status"] == "active" and rich["reader_profile"] == "male_fantasy"


def test_canon_list_summary_is_NOT_the_same_projection_as_canon_detail(tmp_path: Path):
    """⚠️ 反例守卫：**两边都是 9 键，但键名不同**。

    这是本组最容易踩的坑：「同一条 canon，列表和单体都返回 9 个字段」在读代码时
    极易被当成同形，于是有人拿 ``CanonDetailOut`` 去套列表端点（或反过来）⇒ 另一条
    端点静默丢字段。本条把「不同形」钉死：

    - 列表有 ``logline / spine_count / rhythm_chapter_count``，单体**没有**；
    - 单体有 ``canon_json / report_md / extracts``，列表**没有**。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    _seed_canon(
        app,
        pid,
        "can_x",
        {"logline": "一句话", "spine": [{"n": 1}]},
        created_at="2026-01-01T00:00:00+00:00",
    )

    summary = _request(app, "GET", f"/api/projects/{pid}/canons").json()[0]
    detail = _request(app, "GET", "/api/canons/can_x").json()

    assert len(summary) == len(detail) == 9, (len(summary), len(detail))
    assert set(summary) != set(detail), (
        "列表与单体竟然同形了——若实现真的改成共用投影，本条的判别依据需重写"
    )
    assert {"logline", "spine_count", "rhythm_chapter_count"} <= set(summary)
    assert not {"logline", "spine_count", "rhythm_chapter_count"} & set(detail)
    assert {"canon_json", "report_md", "extracts"} <= set(detail)
    assert not {"canon_json", "report_md", "extracts"} & set(summary)
    # 六个身份列两边同名
    assert {"canon_id", "project_id", "title", "reader_profile", "status",
            "created_at"} <= (set(summary) & set(detail))


def test_canon_summary_model_declares_exactly_the_baseline_keys(tmp_path: Path):
    """双保险 + OpenAPI 声明（``extra="allow"`` ⇒ 只断言响应抓不到声明丢失）。"""
    assert set(CanonSummaryOut.model_fields) == set(EXPECTED_CANON_SUMMARY_KEYS), (
        f"模型多={set(CanonSummaryOut.model_fields) - EXPECTED_CANON_SUMMARY_KEYS} "
        f"模型少={EXPECTED_CANON_SUMMARY_KEYS - set(CanonSummaryOut.model_fields)}"
    )

    app = _create_app(tmp_path)
    schema = app.openapi()
    assert _props(schema, "CanonSummaryOut") == set(EXPECTED_CANON_SUMMARY_KEYS)
    # 三个派生量有默认值 ⇒ 不进 required；6 个身份列必在
    required = _required(schema, "CanonSummaryOut")
    assert {"canon_id", "project_id", "title", "reader_profile", "status",
            "created_at"} <= required, sorted(required)
    assert not {"logline", "spine_count", "rhythm_chapter_count"} & required, sorted(
        required
    )

    ref = _item_ref(schema, "/api/projects/{project_id}/canons")
    assert ref.endswith("/CanonSummaryOut"), ref
    # ⚠️ 不得误挂到单体端点的模型上（两条端点「都是 9 键」的陷阱）。
    # 注意单体那条**不是**数组，直接取 $ref。
    detail_ref = (
        schema["paths"]["/api/canons/{canon_id}"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
    )
    assert detail_ref.endswith("/CanonDetailOut"), detail_ref
    assert detail_ref != ref


def test_canon_list_passes_through_unknown_keys(tmp_path: Path, monkeypatch):
    """``CanonSummaryOut`` 必须 ``extra="allow"``。

    ⚠️ **如实登记残余边界**：``summary_of`` 返回的是**手写字面量 dict**，没有天然的
    注入点 ⇒ 本组**只能靠替换 service** 制造一个多出来的键（与批次（六）· G7 对
    ``CanonDetailOut`` 的做法同款）。不这么做，「关掉 extra」在本组里是**等价变异**
    ——响应一模一样，突变验证会误报存活。

    真实风险：将来给摘要加一个派生键（如 ``technique_count``），不开 ``extra``
    会被 ``response_model`` 静默裁掉——数据在库里、API 读不到、零报错。
    """
    import packages.core.api.routers.reference as ref_router

    _real = ref_router.ReferenceService

    class _Svc(_real):  # type: ignore[misc, valid-type]
        def list_active_summaries(self, project_id):
            return [
                {**s, "technique_count": 7}
                for s in super().list_active_summaries(project_id)
            ]

    monkeypatch.setattr(ref_router, "ReferenceService", _Svc)

    app = _create_app(tmp_path)
    pid = _project(app)
    _seed_canon(app, pid, "can_x", {"logline": "一句话"},
                created_at="2026-01-01T00:00:00+00:00")

    r = _request(app, "GET", f"/api/projects/{pid}/canons")
    assert r.status_code == 200, r.text
    assert r.json()[0].get("technique_count") == 7, sorted(r.json()[0])


# ===========================================================================
# 3. 文风样例列表 —— GET /api/projects/{pid}/style-samples
# ===========================================================================


def test_style_sample_list_rows_match_the_hardcoded_baseline(tmp_path: Path):
    """实测结论：6 键恒在、无条件键；``created_at DESC, sample_id DESC``。

    判别点：``EXPECTED_STYLE_SAMPLE_LIST_KEYS`` 来自 0008 建表 DDL。
    """
    from packages.core.api.routers.author_style_samples import StyleSample

    app = _create_app(tmp_path)
    pid = _project(app)
    # 先走一次 POST，确认「POST 响应 = 列表行」这个**复用前提**真在
    made = _request(
        app, "POST", f"/api/projects/{pid}/style-samples",
        json={"title": "手写样例", "content": "正文"},
    )
    assert made.status_code == 201, made.text
    assert set(made.json()) == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS), sorted(made.json())
    assert set(StyleSample.model_fields) == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS)

    _seed_sample(app, pid, "asty_b", created_at="2026-03-01T00:00:00+00:00")
    _seed_sample(app, pid, "asty_a", created_at="2026-03-01T00:00:00+00:00")
    _seed_sample(app, pid, "asty_old", created_at="2026-01-01T00:00:00+00:00")

    r = _request(app, "GET", f"/api/projects/{pid}/style-samples")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 4, rows
    for row in rows:
        assert set(row) == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS), (
            f"多={set(row) - EXPECTED_STYLE_SAMPLE_LIST_KEYS} "
            f"少={EXPECTED_STYLE_SAMPLE_LIST_KEYS - set(row)}"
        )
    # created_at DESC；同秒时按 sample_id DESC 兜底（字典序 ≠ 时间序的同族坑）
    ids = [x["sample_id"] for x in rows]
    assert ids[0] == made.json()["sample_id"], ids
    assert ids[1:3] == ["asty_b", "asty_a"], ids
    assert ids[3] == "asty_old", ids


def test_style_sample_list_model_and_openapi_match_baseline(tmp_path: Path):
    """双保险 + OpenAPI properties/required + 端点 ⇄ schema 的逐跳接线。

    ``StyleSample`` **不开** ``extra="allow"``（列表 SQL 是显式 6 列枚举，不是
    ``SELECT *``）⇒ 这里 ``properties`` 就是完整的声明面，不存在「响应有、schema
    没有」的缝。
    """
    from packages.core.api.routers.author_style_samples import StyleSample

    assert set(StyleSample.model_fields) == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS)
    assert StyleSample.model_config.get("extra") in (None, "ignore"), (
        f"列表 SQL 是显式 6 列投影，无需 extra=allow；实测配置={StyleSample.model_config}"
    )

    app = _create_app(tmp_path)
    schema = app.openapi()
    assert _props(schema, "StyleSample") == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS)
    # 6 列皆 NOT NULL 且无默认值 ⇒ 全进 required
    assert _required(schema, "StyleSample") == set(EXPECTED_STYLE_SAMPLE_LIST_KEYS)

    list_ref = _item_ref(schema, "/api/projects/{pid}/style-samples")
    assert list_ref.endswith("/StyleSample"), list_ref
    # 单体 POST 复用同一个模型（单一事实源）
    post_ref = (
        schema["paths"]["/api/projects/{pid}/style-samples"]["post"]["responses"]["201"]
        ["content"]["application/json"]["schema"]["$ref"]
    )
    assert post_ref == list_ref, (post_ref, list_ref)


def test_style_sample_list_does_not_leak_new_table_columns(tmp_path: Path):
    """``extra`` 的取舍是**实测出来的**，不是偏好：显式投影 ⇒ 新列**不该**出现。

    对照组：``quality_reports`` 的列表用 ``SELECT *``，新列**会**出现
    （:func:`test_quality_list_passes_through_future_db_columns` 守它）。
    两边方向相反 ⇒ 说明「要不要 ``extra="allow"``」由**投影方式**决定，不是全局口味。

    本条同时挡住「把列表 SQL 改成 ``SELECT *``」这个变异：新列会冒出来。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    _seed_sample(app, pid, "asty_x", created_at="2026-01-01T00:00:00+00:00")

    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("ALTER TABLE author_style_samples ADD COLUMN future_col TEXT")
        conn.commit()
    finally:
        conn.close()

    r = _request(app, "GET", f"/api/projects/{pid}/style-samples")
    assert r.status_code == 200, r.text
    assert "future_col" not in r.json()[0], sorted(r.json()[0])


# ===========================================================================
# 4. 批次验收口径 + 文件端点的例外
# ===========================================================================


def test_every_modeled_list_declares_a_named_item_ref(tmp_path: Path):
    """本批的**验收口径**本身：``$ref`` 或 ``array`` 且 ``items`` 有 ``$ref``。

    建模前三条端点的 items 都是匿名 ``{"type": "object", "additionalProperties":
    true}`` ⇒ 前端拿到的仍是 ``Record<string, unknown>``。本条把「匿名 object」
    这个具体形状钉死——比「有 $ref」的正面断言更容易定位回归。
    """
    app = _create_app(tmp_path)
    schema = app.openapi()
    for path in (
        "/api/projects/{project_id}/quality",
        "/api/projects/{project_id}/canons",
        "/api/projects/{pid}/style-samples",
    ):
        items = (
            schema["paths"][path]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["items"]
        )
        assert "$ref" in items, f"{path} 的 items 仍是匿名 object：{items}"
        assert items.get("type") != "object", path


def test_q8_export_stays_unmodelled_because_it_is_a_file_endpoint(tmp_path: Path):
    """⚠️ **例外，不是漏做**：``q8-export`` 返回 CSV 文件，不该有 JSON 契约。

    与 ``GET /export`` 同款判据：``Content-Type: text/csv`` + ``Content-Disposition:
    attachment`` + UTF-8 BOM ⇒ 文件端点。硬套 JSON 模型只会给 OpenAPI 写进一条与实际
    Content-Type 相矛盾的契约。

    ⚠️ 本条**只**钉住「不声明 JSON schema」这半边（另一半的 CSV 特征由
    ``test_genre_quality_reference_response_contract.py::
    test_q8_export_is_a_csv_file_and_declares_no_json_model`` 完整覆盖，**不重复造**）。
    为什么还要留这半边：那个文件属于另一名成员，若它被重构/改名，本条是本组唯一的
    提醒；而且它把「例外」写进了**本批**的验收用例里——读 L4 的测试就能看到
    「哪一条是有意不做的、为什么」。
    """
    app = _create_app(tmp_path)
    op = app.openapi()["paths"]["/api/projects/{project_id}/quality/q8-export"]["get"]
    json_schema = (
        op["responses"]["200"].get("content", {}).get("application/json", {}).get("schema", {})
    )
    assert not json_schema, f"q8-export 是文件端点，不该有 JSON schema，却有：{json_schema}"


def test_list_endpoints_keep_404_and_unknown_project_semantics(tmp_path: Path):
    """加 ``response_model`` 不得改动错误路径（未知 project 仍 404 且 detail 不变）。"""
    app = _create_app(tmp_path)
    for path in ("/quality", "/canons", "/style-samples"):
        r = _request(app, "GET", f"/api/projects/prj_nope{path}")
        assert r.status_code == 404, (path, r.status_code, r.text[:200])
        assert r.json()["detail"] == "project 'prj_nope' not found", (path, r.text[:200])


def test_canon_list_only_returns_active_status(tmp_path: Path):
    """列表 SQL 带 ``status = 'active'`` 过滤 ⇒ 归档 canon 不出现。

    顺带钉住一条边界：``status`` 刻意**不**用 Literal（DDL 的 CHECK 只约束写入，
    而同文件 ``DeconstructStartResponse`` 已定调「新增状态不该让端点 500」）。
    """
    app = _create_app(tmp_path)
    pid = _project(app)
    _seed_canon(app, pid, "can_active", {"logline": "a"},
                created_at="2026-01-01T00:00:00+00:00")
    _seed_canon(app, pid, "can_archived", {"logline": "b"},
                created_at="2026-02-01T00:00:00+00:00")
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("UPDATE reference_canons SET status='archived' WHERE canon_id=?",
                     ("can_archived",))
        conn.commit()
    finally:
        conn.close()

    rows = _request(app, "GET", f"/api/projects/{pid}/canons").json()
    assert [r["canon_id"] for r in rows] == ["can_active"], rows
