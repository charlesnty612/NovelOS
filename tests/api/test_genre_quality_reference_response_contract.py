"""题材包 / 评审 / 拆书 四组端点的 response_model 契约测试（2026-10-15 批次（六）· G7）。

本组测试的三条方法论纪律（AGENTS.md §五坑区，不是风格偏好，是踩过四次坑的产物）：

1. **禁自指断言。** 断言「响应键集 == 模型字段集」是**恒真**的：把字段从模型里删掉，
   两侧同时变小，仍然「相等」（批次（五）突变 M14 实测存活）。
   ⇒ 本文件**所有**键集断言都对 `EXPECTED_*` 常量，那些常量逐个注明**独立来源**
   （DDL / service docstring / 投影字面量），**不是**从跑出来的响应里抄的。

2. **`extra="allow"` 会吃掉「声明被删」的信号。** 删掉一个字段声明，响应照样有那个键
   （以 extra 身份透出），只有 OpenAPI 里的 `properties` / `required` 会变
   （批次（五）突变 M20 实测存活）。
   ⇒ 凡模型开了 `extra="allow"`，本文件**都另**断言 OpenAPI schema。

3. **夹具的期望形状必须独立于实现。** 拆书端点本文件**不真跑**（会触发拆书流程），
   用假 engine 替换 `WorkflowEngine` 与 `get_run`——工作流**不执行**、零 LLM 调用，
   只量响应装配分支。合成 canon 行同理（直接 INSERT，不经 deconstruct 工作流）。

实测结论（键集）见各用例 docstring。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.quality.issues import is_blocking_issue, issue_gate
from packages.core.quality.models import (
    QualityReportIssue,
    QualityReportResponse,
)

from ._contract_runner import client_ctx, create_test_app, one_shot

# ---------------------------------------------------------------------------
# 第三方基准（硬编码）：每个键的出处写在注释里。
#
# ⚠️ 这些常量**不是**「跑一下看返回什么」得来的——那是坑区「夹具从被测代码的实际输出里
# 抄形状 ⇒ 缺陷被写进断言」的成因。它们来自 DDL 与 docstring 契约。
# ---------------------------------------------------------------------------

# quality_reports 表列集（DDL）：
#   0003_quality_reports.sql      → report_id, project_id, chapter_id, commit_id,
#                                   run_id, overall, scores_json, issues_json, created_at
#   0012_judge_scores.sql          → judge_json
#   0024_..._draft_version.sql     → draft_version
# 减去 ``judge_json``（被 ``_attach_judge`` 弹出并改写成 ``judge``），加回 ``judge``。
EXPECTED_QUALITY_REPORT_KEYS = frozenset(
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

# ``GenrePackService.get_project_binding`` 的 docstring 明列：
#   返回 ``{"project_id", "pack_id", "bound", "pack"}``。
EXPECTED_GENRE_BINDING_KEYS = frozenset({"project_id", "pack_id", "bound", "pack"})

# ``genre_packs`` 表 8 列（0025_genre_packs.sql：pack_id, name, genre_tag, version,
# payload_json, source_path, created_at, updated_at），其中 ``payload_json`` 在投影里
# 改名为 ``payload``，另加 1 个派生列 ``bound_project_count`` = 9。
EXPECTED_GENRE_PACK_KEYS = frozenset(
    {
        "pack_id",
        "name",
        "genre_tag",
        "version",
        "payload",
        "source_path",
        "created_at",
        "updated_at",
        "bound_project_count",
    }
)

# ``ReferenceService.get_canon_detail`` 的 docstring 明列 9 个键。
EXPECTED_CANON_KEYS = frozenset(
    {
        "canon_id",
        "project_id",
        "title",
        "reader_profile",
        "status",
        "canon_json",
        "report_md",
        "created_at",
        "extracts",
    }
)

# canon_extracts 表全部 4 列（0004_reference_canon.sql）。
EXPECTED_EXTRACT_KEYS = frozenset(
    {"extract_id", "chapter_index", "extract_json", "created_at"}
)

# author_style_samples 表全部 6 列（0008_author_style_samples_and_overdue.sql）。
EXPECTED_STYLE_SAMPLE_KEYS = frozenset(
    {"sample_id", "project_id", "title", "content", "created_at", "updated_at"}
)

# deconstruct 响应：5 个恒在键（``_start_deconstruct_internal`` 的 out 字面量前半）
# ＋ 3 个条件键（COMPLETED 出前两个、FAILED 出 error）。
EXPECTED_DECONSTRUCT_ALWAYS_KEYS = frozenset(
    {"run_id", "status", "current_node", "project_id", "book_title"}
)
EXPECTED_DECONSTRUCT_CONDITIONAL_KEYS = frozenset(
    {"canon_id", "extracts_count", "error"}
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    """共享 runner 的 ``create_test_app``（**不**显式跑迁移；迁移在 lifespan 里跑）。"""
    return create_test_app(tmp_path)


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """同步用例里发一次请求（端点是 async 的，用例是同步的）。"""
    return asyncio.run(one_shot(app, method, path, enter_lifespan=True, **kwargs))


def _project(app, name: str = "G7") -> str:
    async def run():
        async with client_ctx(app) as c:
            r = await c.post("/api/projects", json={"name": name})
            assert r.status_code in (200, 201), r.text
            return r.json()["project_id"]

    return asyncio.run(run())


def _chapter(app, pid: str, number: int = 1) -> str:
    async def run():
        async with client_ctx(app) as c:
            r = await c.post(
                f"/api/projects/{pid}/chapters", json={"number": number, "title": "一"}
            )
            assert r.status_code in (200, 201), r.text
            return r.json()["chapter_id"]

    return asyncio.run(run())


def _insert_report(app, pid: str, cid: str, *, issues: list | None = None,
                   scores: dict | None = None, judge: dict | None = None) -> str:
    """直接 INSERT 一份 quality_report（合成夹具，不跑任何评估/模型调用）。"""
    from packages.core.ids import new_id

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
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _seed_canon(app, pid: str, canon_id: str = "can_g7") -> None:
    """合成一条 canon + 一条 extract（不经 deconstruct 工作流，无 LLM 调用）。"""
    canon_json = {
        "logline": "一句话",
        "style_params": {
            "pov": "third",
            "dialogue_ratio": 0.3,
            "sentence_length_distribution": {"mean": 18, "median": 16, "max": 40},
        },
        "techniques": [
            {"name_pattern": "A", "location_pattern": "开篇", "effect_pattern": "提速"}
        ],
        "metadata": {"source_book_title": "_g7_probe"},
    }
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """INSERT INTO reference_canons
               (canon_id, project_id, title, reader_profile, canon_json,
                report_md, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (canon_id, pid, "探针书", "male_fantasy",
             json.dumps(canon_json, ensure_ascii=False), "# 报告", "active", now_iso()),
        )
        conn.execute(
            """INSERT INTO canon_extracts
               (extract_id, canon_id, chapter_index, extract_json, created_at)
               VALUES (?,?,?,?,?)""",
            ("cex_g7", canon_id, 1, json.dumps({"k": "v"}), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ===========================================================================
# 1. 评审（quality）
# ===========================================================================


def test_quality_evaluate_returns_every_table_column_plus_judge(tmp_path: Path):
    """实测结论：**11 个键恒在**，无一个因无数据而缺席。

    `judge` / `commit_id` / `run_id` / `draft_version` 在无数据时是 `null`，
    但**键仍在**——这是 `_attach_judge` 与 DB 可空列决定的，不是省略。

    判别点：断言对象是 ``EXPECTED_QUALITY_REPORT_KEYS``（DDL 推出来的），
    **不是** ``QualityReportResponse.model_fields``——后者是自指断言（坑区已登记）。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)

    r = _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_QUALITY_REPORT_KEYS), (
        f"多={set(body) - EXPECTED_QUALITY_REPORT_KEYS} "
        f"少={EXPECTED_QUALITY_REPORT_KEYS - set(body)}"
    )
    assert body["judge"] is None
    assert body["commit_id"] is None and body["run_id"] is None

    # GET 与 evaluate 返回同构
    g = _request(app, "GET", f"/api/chapters/{cid}/quality")
    assert g.status_code == 200, g.text
    assert set(g.json()) == set(EXPECTED_QUALITY_REPORT_KEYS)


def test_quality_report_model_declares_exactly_the_baseline_keys():
    """双保险：模型字段集 vs **硬编码**基准（不是 vs 响应，那会是自指）。

    单独成一条而不是并进上面的响应断言，是为了在 OpenAPI 变了但响应没变时也能定位。
    """
    assert set(QualityReportResponse.model_fields) == set(
        EXPECTED_QUALITY_REPORT_KEYS
    ), (
        f"模型多={set(QualityReportResponse.model_fields) - EXPECTED_QUALITY_REPORT_KEYS} "
        f"模型少={EXPECTED_QUALITY_REPORT_KEYS - set(QualityReportResponse.model_fields)}"
    )
    # issues_json 必须是元素级模型（不是裸 dict）——否则 issue 的字段在 OpenAPI 里全丢
    field = QualityReportResponse.model_fields["issues_json"]
    assert field.annotation == list[QualityReportIssue], field.annotation


def test_quality_openapi_declares_issue_fields_despite_extra_allow(tmp_path: Path):
    """⚠️ ``extra="allow"`` 会吃掉「字段声明被删」的信号 ⇒ 必须断言 OpenAPI。

    把 ``gate`` 从 ``QualityReportIssue`` 里删掉，``GET /chapters/{cid}/quality`` 的
    响应**照样**有 ``gate``（它以 extra 身份透出），只有 schema 会变。
    （批次（五）突变 M20 实测存活的就是这一条。）
    """
    app = create_test_app(tmp_path)
    schema = app.openapi()
    issue_props = schema["components"]["schemas"]["QualityReportIssue"]["properties"]
    assert set(issue_props) == {
        "severity", "category", "location", "rule_id", "message",
        "gate", "suggestion", "evidence_refs", "judge_trace",
    }, sorted(issue_props)

    report_props = schema["components"]["schemas"]["QualityReportResponse"]["properties"]
    assert set(report_props) == set(EXPECTED_QUALITY_REPORT_KEYS), sorted(report_props)
    # judge 是「router 恒写、无则 null」⇒ 必填可空
    required = schema["components"]["schemas"]["QualityReportResponse"]["required"]
    assert "judge" in required, required

    # ⚠️ 端点 ⇄ schema 的**接线**：只断言「schema 里有这个模型」不够——
    # 模型被另一个端点引用着，摘掉本端点的 response_model 照样全绿
    # （「断言了结果 ≠ 判别了机制」的同族）。两条端点都要指到同一个 $ref。
    for path, method, code in (
        ("/api/chapters/{chapter_id}/quality", "get", "200"),
        ("/api/chapters/{chapter_id}/quality/evaluate", "post", "201"),
    ):
        ref = schema["paths"][path][method]["responses"][code]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref.endswith("/QualityReportResponse"), f"{method.upper()} {path} → {ref}"


def test_quality_issue_gate_is_not_a_function_of_severity(tmp_path: Path):
    """⚠️ 核心守卫：**severity 与 gate 是正交的两根轴**（坑区 P0-1 事故）。

    真实可达的两对反直觉组合：
    - ``severity="error"`` 但 rule 不在阻断白名单 ⇒ ``gate="auto"``
      （即「error 有 blocking / informational 两组」，见 ``is_blocking_issue``）；
    - ``severity="info"`` 但单条被显式上修 ⇒ ``gate="confirm"``
      （``make_issue(gate="confirm")`` 的量级依赖上修路径）。

    任何「按 severity 推 gate」的模型（error→block、info→auto）都会在这条用例上红。
    """
    informational_error = {
        "severity": "error",
        "category": "style",
        "rule_id": "RULE_STYLE_SOME_NON_BLOCKING",
        "message": "error 级但不阻断",
        "gate": "auto",
        "location": "<unknown>",
    }
    confirmed_info = {
        "severity": "info",
        "category": "style",
        "rule_id": "RULE_STYLE_REPETITION_TRIGRAM",
        "message": "info 级但必须显式接受",
        "gate": "confirm",
        "location": "<unknown>",
    }
    # 模型不得改写这两对的组合
    assert QualityReportIssue(**informational_error).gate == "auto"
    assert QualityReportIssue(**confirmed_info).gate == "confirm"
    # 领域侧独立复核：权威函数也这么判（模型与 issue_gate 不能各说各话）
    assert issue_gate(QualityReportIssue(**informational_error)) == "auto"
    assert not is_blocking_issue(QualityReportIssue(**informational_error))
    assert issue_gate(QualityReportIssue(**confirmed_info)) == "confirm"

    # 端到端：两份 issue 落库后原样读回，gate 不被 severity 吞掉
    app = create_test_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(app, pid, cid, issues=[informational_error, confirmed_info])
    r = _request(app, "GET", f"/api/chapters/{cid}/quality")
    assert r.status_code == 200, r.text
    got = {i["rule_id"]: i for i in r.json()["issues_json"]}
    assert got["RULE_STYLE_SOME_NON_BLOCKING"]["severity"] == "error"
    assert got["RULE_STYLE_SOME_NON_BLOCKING"]["gate"] == "auto"
    assert got["RULE_STYLE_REPETITION_TRIGRAM"]["severity"] == "info"
    assert got["RULE_STYLE_REPETITION_TRIGRAM"]["gate"] == "confirm"


def test_quality_report_response_passes_through_unknown_issue_keys(tmp_path: Path):
    """``QualityReportIssue`` 必须 ``extra="allow"``。

    落库的 issue 字典可能被未来的规则追加证据字段（如 ``evidence_excerpt``）。
    声明成严格模型 ⇒ ``response_model`` 把它裁掉 ⇒ **数据在库里、API 读不到、零报错**
    （与 ``QualityJudgeRecord`` 守的是同一族形状）。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(
        app,
        pid,
        cid,
        issues=[
            {
                "severity": "warning",
                "category": "pacing",
                "rule_id": "RULE_PACING_X",
                "message": "节奏偏慢",
                "gate": "auto",
                "location": "<unknown>",
                "evidence_excerpt": "他走了。",
                "future_nested": {"a": 1},
            }
        ],
    )
    r = _request(app, "GET", f"/api/chapters/{cid}/quality")
    assert r.status_code == 200, r.text
    issue = r.json()["issues_json"][0]
    assert issue.get("evidence_excerpt") == "他走了。", issue
    assert issue.get("future_nested") == {"a": 1}, issue


def test_quality_report_response_passes_through_future_db_columns(tmp_path: Path):
    """⚠️ 键集 = ``SELECT *`` ⇒ 表里加列，响应就多键。

    声明成默认 ``extra="ignore"`` 的模型会把新列**静默丢掉**。本用例在临时库里
    ALTER 出一列，验证它能透出——这是 ``extra="allow"`` 唯一的可见症状，
    也是撤掉 ``extra="allow"`` 必红的那条。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(app, pid, cid)

    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("ALTER TABLE quality_reports ADD COLUMN future_col TEXT")
        conn.execute("UPDATE quality_reports SET future_col = 'v1'")
        conn.commit()
    finally:
        conn.close()

    r = _request(app, "GET", f"/api/chapters/{cid}/quality")
    assert r.status_code == 200, r.text
    assert r.json().get("future_col") == "v1", sorted(r.json())


def test_quality_judge_is_nullable_but_declared(tmp_path: Path):
    """有 judge 时原样透出（含 service 存下的扩展键），无 judge 时键仍在、值为 null。"""
    app = create_test_app(tmp_path)
    pid = _project(app)
    cid = _chapter(app, pid)
    _insert_report(
        app,
        pid,
        cid,
        judge={
            "pacing": 80, "style": 70, "logic": 90, "dialogue": 60,
            "verdict": "还行", "score_avg": 75.0, "dry_run": False,
        },
    )
    r = _request(app, "GET", f"/api/chapters/{cid}/quality")
    assert r.status_code == 200, r.text
    judge = r.json()["judge"]
    assert judge["pacing"] == 80 and judge["logic"] == 90
    assert judge["score_avg"] == 75.0 and judge["dry_run"] is False


def test_q8_export_is_a_csv_file_and_declares_no_json_model(tmp_path: Path):
    """⚠️ ``q8-export`` **刻意不建模**：它返回文件（CSV），不是 JSON。

    硬套 JSON 模型只会给 OpenAPI 写进一条与实际 ``Content-Type`` 相矛盾的契约。
    本用例把这个决定**钉住**：一旦有人给它加了 ``response_model``，本条必红。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    _chapter(app, pid)

    r = _request(app, "GET", f"/api/projects/{pid}/quality/q8-export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert f'filename="q8-report-{pid}.csv"' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM（Excel 中文兼容）"
    header = r.content.lstrip(b"\xef\xbb\xbf").decode("utf-8").splitlines()[0]
    assert header == (
        "chapter_number,chapter_title,chapter_status,ai_chars,"
        "human_chars,human_ratio,evaluated_at"
    )

    # OpenAPI 里该端点**不得**声明 application/json 响应 schema
    op = app.openapi()["paths"]["/api/projects/{project_id}/quality/q8-export"]["get"]
    json_schema = (
        op["responses"]["200"].get("content", {}).get("application/json", {})
        .get("schema", {})
    )
    assert not json_schema, f"q8-export 不该有 JSON schema，却有：{json_schema}"


# ===========================================================================
# 2. 题材包（genre）
# ===========================================================================


_MIN_PACK_PAYLOAD = {"schema_version": "genre-pack.v1.0.0"}


def _create_pack(app, pid: str, pack_id: str = "gp_g7") -> str:
    r = _request(
        app,
        "POST",
        f"/api/projects/{pid}/genre-packs",
        json={
            "name": "包",
            "genre_tag": "xianxia",
            "pack_id": pack_id,
            "payload": dict(_MIN_PACK_PAYLOAD),
        },
    )
    assert r.status_code == 201, r.text
    return pack_id


def test_genre_binding_endpoints_all_return_the_same_four_keys(tmp_path: Path):
    """实测结论：三条端点（GET / bind / unbind）**同构**，恒 4 键。

    未绑定时 ``pack_id`` 与 ``pack`` 为 ``null``，``bound=false``——**键不缺席**。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    _create_pack(app, pid)

    unbound = _request(app, "GET", f"/api/projects/{pid}/genre-pack")
    assert unbound.status_code == 200, unbound.text
    b = unbound.json()
    assert set(b) == set(EXPECTED_GENRE_BINDING_KEYS), sorted(b)
    assert b == {
        "project_id": pid, "pack_id": None, "bound": False, "pack": None,
    }, b

    bound = _request(
        app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_g7"}
    )
    assert bound.status_code == 200, bound.text
    b = bound.json()
    assert set(b) == set(EXPECTED_GENRE_BINDING_KEYS), sorted(b)
    assert b["bound"] is True and b["pack_id"] == "gp_g7"
    assert set(b["pack"]) == set(EXPECTED_GENRE_PACK_KEYS), sorted(b["pack"])
    # 版本线如实透传（§二硬规则 3：^genre-pack\.v1\.\d+\.\d+$）
    assert b["pack"]["payload"]["schema_version"] == "genre-pack.v1.0.0"

    got = _request(app, "GET", f"/api/projects/{pid}/genre-pack")
    assert set(got.json()) == set(EXPECTED_GENRE_BINDING_KEYS)
    assert got.json()["pack"]["bound_project_count"] == 1

    after = _request(app, "POST", f"/api/projects/{pid}/genre-pack/unbind")
    assert after.status_code == 200, after.text
    assert set(after.json()) == set(EXPECTED_GENRE_BINDING_KEYS)
    assert after.json() == {
        "project_id": pid, "pack_id": None, "bound": False, "pack": None,
    }


def test_genre_binding_model_declares_baseline_keys_and_openapi(tmp_path: Path):
    """双保险：模型字段集 vs 硬编码基准 ＋ OpenAPI properties/required。

    ⚠️ 两条都要：``extra="allow"`` 让「删字段声明」在响应里不可见（只断言响应键集 =
    突变存活），而「模型字段集 vs 响应」是自指断言（突变也存活）。
    """
    from packages.core.genre.models import GenrePackBinding

    assert set(GenrePackBinding.model_fields) == set(EXPECTED_GENRE_BINDING_KEYS), (
        f"多={set(GenrePackBinding.model_fields) - EXPECTED_GENRE_BINDING_KEYS} "
        f"少={EXPECTED_GENRE_BINDING_KEYS - set(GenrePackBinding.model_fields)}"
    )

    app = create_test_app(tmp_path)
    schema = app.openapi()
    props = schema["components"]["schemas"]["GenrePackBinding"]["properties"]
    assert set(props) == set(EXPECTED_GENRE_BINDING_KEYS), sorted(props)
    # project_id 与 bound 恒在（service 无条件返回），须进 required
    assert set(schema["components"]["schemas"]["GenrePackBinding"]["required"]) == {
        "project_id",
        "bound",
    }

    # 三条端点都必须指向同一个 $ref（单一事实源）
    ref_suffix = "/GenrePackBinding"
    for path, method in (
        ("/api/projects/{project_id}/genre-pack", "get"),
        ("/api/projects/{project_id}/genre-pack/bind", "post"),
        ("/api/projects/{project_id}/genre-pack/unbind", "post"),
    ):
        ref = schema["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref.endswith(ref_suffix), f"{method.upper()} {path} → {ref}"


def test_genre_binding_pack_matches_genre_pack_model_fields():
    """绑定里的 ``pack`` 必须复用 ``GenrePack`` 单一事实源（不许复制一份）。

    ``queries.pack_row_to_dict`` 返回的是**字面量 9 键 dict**，故本条是硬断言。
    """
    from packages.core.genre import GenrePack
    from packages.core.genre.models import GenrePackBinding

    assert set(GenrePack.model_fields) == set(EXPECTED_GENRE_PACK_KEYS), (
        f"GenrePack 与 genre_packs 表投影漂移："
        f"多={set(GenrePack.model_fields) - EXPECTED_GENRE_PACK_KEYS} "
        f"少={EXPECTED_GENRE_PACK_KEYS - set(GenrePack.model_fields)}"
    )
    assert GenrePackBinding.model_fields["pack"].annotation == GenrePack | None


def test_genre_binding_passes_through_unknown_keys(tmp_path: Path, monkeypatch):
    """``GenrePackBinding`` 必须 ``extra="allow"``（本组唯一能观察到它的缝）。

    ⚠️ 本仓已登记过「只断言响应键集 ⇒ 突变存活」的反面：本模型开了
    ``extra="allow"`` 后，**声明被删**在响应里看不见。反过来，**关掉**
    ``extra="allow"`` 也一样看不见——除非能往响应里塞一个模型没声明的键。
    绑定 dict 由 ``get_project_binding`` 拼装，故这里替换 service（不是改被测端点），
    让它多返回一个 ``bound_at`` 派生键。
    """
    import packages.core.api.routers.genre as genre_router

    _real = genre_router.GenrePackService

    class _Svc(_real):  # type: ignore[misc, valid-type]
        def get_project_binding(self, project_id):
            b = super().get_project_binding(project_id)
            if b is not None:
                b = {**b, "bound_at": "2026-10-15T00:00:00+00:00"}
            return b

    monkeypatch.setattr(genre_router, "GenrePackService", _Svc)

    app = create_test_app(tmp_path)
    pid = _project(app)
    r = _request(app, "GET", f"/api/projects/{pid}/genre-pack")
    assert r.status_code == 200, r.text
    assert r.json().get("bound_at") == "2026-10-15T00:00:00+00:00", sorted(r.json())


# ===========================================================================
# 3. 拆书（reference）
# ===========================================================================


def test_canon_detail_returns_nine_keys_with_extracts(tmp_path: Path):
    """实测结论：9 键恒在；``extracts`` 无数据时是 ``[]``（不缺席）。

    ``canon_json`` 是**存什么吐什么**的存储载荷（不校验），故出参允许 null。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    _seed_canon(app, pid)

    r = _request(app, "GET", "/api/canons/can_g7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_CANON_KEYS), (
        f"多={set(body) - EXPECTED_CANON_KEYS} 少={EXPECTED_CANON_KEYS - set(body)}"
    )
    assert body["status"] == "active"
    assert body["canon_json"]["logline"] == "一句话"
    assert len(body["extracts"]) == 1
    assert set(body["extracts"][0]) == set(EXPECTED_EXTRACT_KEYS), (
        sorted(body["extracts"][0])
    )

    missing = _request(app, "GET", "/api/canons/can_nope")
    assert missing.status_code == 404


def test_canon_detail_models_match_baseline_and_openapi(tmp_path: Path):
    """双保险 + OpenAPI 声明（``extra="allow"`` ⇒ 只断言响应抓不到声明丢失）。"""
    from packages.domain.reference.models import CanonDetailOut, CanonExtractOut

    assert set(CanonDetailOut.model_fields) == set(EXPECTED_CANON_KEYS)
    assert set(CanonExtractOut.model_fields) == set(EXPECTED_EXTRACT_KEYS)

    app = create_test_app(tmp_path)
    schema = app.openapi()
    assert set(
        schema["components"]["schemas"]["CanonDetailOut"]["properties"]
    ) == set(EXPECTED_CANON_KEYS)
    assert set(
        schema["components"]["schemas"]["CanonExtractOut"]["properties"]
    ) == set(EXPECTED_EXTRACT_KEYS)
    ref = schema["paths"]["/api/canons/{canon_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/CanonDetailOut"), ref


def test_canon_detail_passes_through_unknown_keys(tmp_path: Path, monkeypatch):
    """``CanonDetailOut`` / ``CanonExtractOut`` 必须 ``extra="allow"``。

    与 genre 同款缝：两个模型都开了 ``extra="allow"``，但 ``get_canon_detail``
    拼的是**字面量 dict**，没有天然的注入点 ⇒ 替换 service 让它多返回一个
    ``deconstruct_version`` 派生键。没有这条，「关掉 extra」这个变异在本组里
    **完全不可观察**（等价变异，突变验证会误报存活或中止）。
    """
    import packages.core.api.routers.reference as ref_router

    _real = ref_router.ReferenceService

    class _Svc(_real):  # type: ignore[misc, valid-type]
        def get_canon_detail(self, canon_id):
            d = super().get_canon_detail(canon_id)
            if d is None:
                return None
            out = {**d, "deconstruct_version": "v1.1"}
            out["extracts"] = [
                {**e, "confidence": 0.82} for e in d.get("extracts", [])
            ]
            return out

    monkeypatch.setattr(ref_router, "ReferenceService", _Svc)

    app = create_test_app(tmp_path)
    pid = _project(app)
    _seed_canon(app, pid)
    r = _request(app, "GET", "/api/canons/can_g7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("deconstruct_version") == "v1.1", sorted(body)
    assert body["extracts"][0].get("confidence") == 0.82, body["extracts"][0]


def test_canon_detail_tolerates_null_canon_json(tmp_path: Path):
    """``canon_json`` 存字面量 ``null`` 时，``get_canon_detail`` 返回 ``None``
    （``json.loads('null')`` 不抛，而 except 只接 TypeError/ValueError）。

    出参声明成 ``dict`` 会把这种情况打成 500 ⇒ 必须允许 null。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """INSERT INTO reference_canons
               (canon_id, project_id, title, reader_profile, canon_json,
                report_md, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("can_null", pid, "空 canon", "male_fantasy", "null", "", "active", now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    r = _request(app, "GET", "/api/canons/can_null")
    assert r.status_code == 200, r.text
    assert r.json()["canon_json"] is None
    assert r.json()["extracts"] == []


def test_to_style_sample_returns_the_six_sample_columns(tmp_path: Path):
    """合成风格卡端点返回的是 ``author_style_samples`` 表的 6 列（无 LLM 调用）。

    该端点复用 ``author_style_samples`` 的 ``create_style_sample``（直接函数调用），
    所以响应形状 = 那张表的全部列。第二次调用按标题判重 → 409（行为一并钉住）。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    _seed_canon(app, pid)

    r = _request(app, "POST", f"/api/projects/{pid}/canons/can_g7/to-style-sample")
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_STYLE_SAMPLE_KEYS), (
        f"多={set(body) - EXPECTED_STYLE_SAMPLE_KEYS} "
        f"少={EXPECTED_STYLE_SAMPLE_KEYS - set(body)}"
    )
    assert body["title"] == "《探针书》拆书风格卡"
    assert "logline：一句话" in body["content"]

    dup = _request(app, "POST", f"/api/projects/{pid}/canons/can_g7/to-style-sample")
    assert dup.status_code == 409, dup.text

    from packages.domain.reference.models import StyleSampleOut

    assert set(StyleSampleOut.model_fields) == set(EXPECTED_STYLE_SAMPLE_KEYS)
    app_schema = app.openapi()
    assert set(
        app_schema["components"]["schemas"]["StyleSampleOut"]["properties"]
    ) == set(EXPECTED_STYLE_SAMPLE_KEYS)
    ref = app_schema["paths"][
        "/api/projects/{project_id}/canons/{canon_id}/to-style-sample"
    ]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/StyleSampleOut"), ref


# ===========================================================================
# 4. 拆书启动（deconstruct / deconstruct-upload）
# ===========================================================================


@pytest.fixture
def fake_deconstruct_engine(monkeypatch):
    """⚠️ 假 engine：工作流**不执行**、零 LLM 调用、零 DB 写入（只量响应装配）。

    本组**禁止**真跑 deconstruct / deconstruct-upload（会触发拆书工作流）。
    这里替换 ``WorkflowEngine.start_with_nodes`` 与 ``get_run``，让端点走完整的
    HTTP 栈 + ``response_model`` 序列化，但工作流一个节点都不执行。
    """
    import packages.core.api.routers.reference as ref

    state = {"status": "COMPLETED"}

    class _FakeEngine:
        def __init__(self, db_path):  # noqa: ARG002
            pass

        def start_with_nodes(self, *args, **kwargs):  # noqa: ARG002
            # 本方法**替代**真引擎：只回一个 run_id，工作流一个节点都不执行。
            return "run_g7_probe"

    def _fake_get_run(db_path, run_id):  # noqa: ARG001
        return {
            "run_id": run_id,
            "status": state["status"],
            "current_node": "T4_persist" if state["status"] == "RUNNING" else None,
            "checkpoint_json": {
                "T4_persist": {"canon_id": "can_from_run", "extracts_count": 3}
            },
            "error": "boom" if state["status"] == "FAILED" else None,
        }

    monkeypatch.setattr(ref, "WorkflowEngine", _FakeEngine)
    monkeypatch.setattr(ref, "get_run", _fake_get_run)
    return state


def test_deconstruct_response_keys_per_status(fake_deconstruct_engine, tmp_path: Path):
    """实测结论：5 键恒在 ＋ 3 条件键。

    ⚠️ **本批次唯一改变线上 JSON 形状的地方**：条件键从「命中才写」变成「恒在、
    未命中为 null」。理由与代价写在 ``DeconstructStartResponse`` 的 docstring 里；
    本用例把**新语义**钉住——若有人把条件键改回「命中才写」，本条必红。

    判别点：断言对象是 ``EXPECTED_DECONSTRUCT_*`` 常量，不是模型字段集。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)

    # COMPLETED：canon_id / extracts_count 有值，error 为 null
    fake_deconstruct_engine["status"] = "COMPLETED"
    r = _request(
        app, "POST", f"/api/projects/{pid}/deconstruct",
        json={"book_title": "书", "text": "正文", "mock_providers": {"m": {}}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_DECONSTRUCT_ALWAYS_KEYS) | set(
        EXPECTED_DECONSTRUCT_CONDITIONAL_KEYS
    ), sorted(body)
    assert body["status"] == "COMPLETED"
    assert body["canon_id"] == "can_from_run" and body["extracts_count"] == 3
    assert body["error"] is None and body["current_node"] is None

    # FAILED：只有 error 有值
    fake_deconstruct_engine["status"] = "FAILED"
    r = _request(
        app, "POST", f"/api/projects/{pid}/deconstruct",
        json={"book_title": "书", "text": "正文", "mock_providers": {"m": {}}},
    )
    body = r.json()
    assert set(body) == set(EXPECTED_DECONSTRUCT_ALWAYS_KEYS) | set(
        EXPECTED_DECONSTRUCT_CONDITIONAL_KEYS
    ), sorted(body)
    assert body["error"] == "boom"
    assert body["canon_id"] is None and body["extracts_count"] is None

    # RUNNING：三者皆 null
    fake_deconstruct_engine["status"] = "RUNNING"
    r = _request(
        app, "POST", f"/api/projects/{pid}/deconstruct",
        json={"book_title": "书", "text": "正文", "mock_providers": {"m": {}}},
    )
    body = r.json()
    assert set(body) == set(EXPECTED_DECONSTRUCT_ALWAYS_KEYS) | set(
        EXPECTED_DECONSTRUCT_CONDITIONAL_KEYS
    ), sorted(body)
    assert body["current_node"] == "T4_persist"
    assert (body["canon_id"], body["extracts_count"], body["error"]) == (None, None, None)


def test_deconstruct_validation_errors_still_422(tmp_path: Path):
    """入参校验分支：不经工作流，直接 422（book_title / text 空、超长）。"""
    app = create_test_app(tmp_path)
    pid = _project(app)
    for payload in (
        {"book_title": "", "text": "x"},
        {"book_title": "书", "text": "  "},
        {"book_title": "书", "text": "x" * 5_000_001},
    ):
        r = _request(app, "POST", f"/api/projects/{pid}/deconstruct", json=payload)
        assert r.status_code == 422, (payload.keys(), r.status_code, r.text[:200])


def test_deconstruct_upload_rejects_unsupported_extension(tmp_path: Path):
    """上传端点的**校验分支**可实跑（不启动工作流）：非 .txt/.epub → 400。

    成功路径**不在本文件验证**：上传端点刻意不暴露 ``mock_providers``
    （``_start_deconstruct_internal(..., mock_providers=None)``），临时库里没有
    启用模型 ⇒ 它必然在 ModelRouter 前置检查处 422。成功路径与纯文本端点共用
    ``_start_deconstruct_internal``，由上一条用例覆盖。
    """
    app = create_test_app(tmp_path)
    pid = _project(app)
    r = _request(
        app,
        "POST",
        f"/api/projects/{pid}/deconstruct-upload",
        files={"file": ("b.md", b"x", "text/markdown")},
        data={"book_title": "书"},
    )
    assert r.status_code == 400, r.text
    assert ".txt / .epub" in r.json()["detail"]


def test_deconstruct_both_endpoints_share_one_declared_schema(tmp_path: Path):
    """两条端点共用同一个 ``$ref``（单一事实源），且必填集与常量基准一致。

    ``DeconstructStartResponse`` **不开** ``extra="allow"`` ⇒ 这里断言
    ``properties`` 就是完整的声明面，不存在「响应有、schema 没有」的缝。
    """
    from packages.domain.reference.models import DeconstructStartResponse

    expected = set(EXPECTED_DECONSTRUCT_ALWAYS_KEYS) | set(
        EXPECTED_DECONSTRUCT_CONDITIONAL_KEYS
    )
    assert set(DeconstructStartResponse.model_fields) == expected, (
        f"多={set(DeconstructStartResponse.model_fields) - expected} "
        f"少={expected - set(DeconstructStartResponse.model_fields)}"
    )

    app = create_test_app(tmp_path)
    schema = app.openapi()
    assert set(
        schema["components"]["schemas"]["DeconstructStartResponse"]["properties"]
    ) == expected
    assert set(
        schema["components"]["schemas"]["DeconstructStartResponse"]["required"]
    ) == set(EXPECTED_DECONSTRUCT_ALWAYS_KEYS) - {"current_node"}

    for path, method in (
        ("/api/projects/{project_id}/deconstruct", "post"),
        ("/api/projects/{project_id}/deconstruct-upload", "post"),
    ):
        ref = schema["paths"][path][method]["responses"]["201"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref.endswith("/DeconstructStartResponse"), f"{path} → {ref}"


def test_genre_and_quality_endpoints_keep_their_404_semantics(tmp_path: Path):
    """加 ``response_model`` 不得改动错误路径（404 仍是 404，且 detail 不变）。"""
    app = create_test_app(tmp_path)
    for method, path in (
        ("GET", "/api/projects/prj_nope/genre-pack"),
        ("POST", "/api/projects/prj_nope/genre-pack/unbind"),
        ("GET", "/api/chapters/ch_nope/quality"),
        ("GET", "/api/canons/can_nope"),
    ):
        r = _request(app, method, path)
        assert r.status_code == 404, (method, path, r.status_code, r.text[:200])
