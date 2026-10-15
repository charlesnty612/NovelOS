"""备份 / 导出 / 杂项 router 的 response_model 契约测试（2026-10-15 批次（六））。

覆盖的 6 个端点
---------------
===============================  ==========================================
端点                            本文件的处置
===============================  ==========================================
``GET  /projects/{id}/backup``   建模型 ``BackupPackage``（**仅 OpenAPI 文档级**）
``POST /projects/import-backup`` 建模型 ``ImportedProject``（运行时过滤生效）
``GET  /projects/{id}/export``   **跳过**——返回文件体，非 JSON（见下）
``GET  /projects/{id}/signing-check`` 建模型 ``SigningCheckResult``
``GET  /ai-call-logs/{log_id}``  建模型 ``AiCallLogDetail``
``POST /projects/{pid}/style-samples`` 建模型 ``StyleSample``
===============================  ==========================================

字段形状从哪来
--------------
**全部来自临时库 + 合成夹具跑通端点后 dump 的实际键集**，不是照源码读出来的。
三处「键的可见性」是实测出来的、且与直觉相反，逐条钉在下面：

1. **条件键是真·缺席，不是 null**：``signing-check`` 的 ``genre_opening`` /
   ``word_band_rejected``、``import-backup`` 的 ``import_warnings``、
   ``genre_opening.rules[].keywords_source``——无数据时**整个键不存在**。
   两个端点因此必须配 ``response_model_exclude_unset=True``，否则缺席会被
   渲染成凭空的 ``null``（本文件 ``*_conditional_keys_absent_*`` 两条用例就是
   撤掉 exclude_unset 必红的判别点）。
2. **null 是常态，不是异常**：``ai-call-logs/{id}`` 的 14 键里除 5 键外均可为 null；
   签约体检的 ``project_name``/``total_chars`` 在无正文项目上是 ``""``/``0``。
3. **⚠️ GET /backup 的 response_model 是文档级、执行级为零**：handler 直接返回
   ``JSONResponse``（为挂 Content-Disposition 触发下载），FastAPI 见到
   ``Response`` 实例就跳过序列化与校验——**声明少一个字段，响应体一个字都不会变**。
   所以针对该端点的判别点必须落在 **OpenAPI 的 ``properties`` / ``required``**，
   响应体那条断言对它恒真（本文件 ``test_backup_openapi_properties_*``）。

禁自指断言
----------
所有期望键集都**硬编码在下面**（``EXPECTED_*``），不是 ``set(Model.model_fields)``。
首版自指写法在批次（五）被突变 M14 实测存活：把 ``volume_number`` 从模型里删掉，
响应与期望**同时**少一个键 ⇒ 仍然「逐字相等」⇒ 断言恒真。
本文件对每个模型配**两道**：① 硬编码期望 × 实际响应（或 OpenAPI properties）；
② 硬编码期望 × ``model_fields``（模型之外的第三方基准，见
AGENTS 坑区「断言里只有被测物和它自己 ⇒ 自指断言恒真」）。

⚠️ ``ImportedProject`` 是 ``extra="allow"``：删掉字段声明后响应**照样**有该键
（它以 extra 身份透出），只有 OpenAPI 会少声明 ⇒ 必须另断言 schema 的
``properties`` / ``required`` / ``additionalProperties``（M20 实测存活那一族）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.api.routers.ai_call_logs import AiCallLogDetail
from packages.core.api.routers.author_style_samples import StyleSample
from packages.core.api.routers.backup import BackupMetadata, BackupPackage, ImportedProject
from packages.core.api.routers.signing_check import (
    SigningCheckItem,
    SigningCheckResult,
    SigningCheckSummary,
)
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import now_iso

# ===========================================================================
# 期望键集 —— 模型之外的第三方基准（禁自指）
# ===========================================================================

# GET /backup：实测顶层恒在 7 键（无一条条件键）
EXPECTED_BACKUP_KEYS = {
    "format",
    "version",
    "exported_at",
    "exported_from_project_id",
    "metadata",
    "project",
    "tables",
}
# 实测 metadata 恒在 10 键 = 4 个导出事实 + 6 个安全排除标志
EXPECTED_BACKUP_METADATA_KEYS = {
    "schema_migrations",
    "table_count_exported",
    "exported_table_names",
    "exported_at_iso",
    "api_keys_stripped",
    "ai_call_logs_excluded",
    "evaluations_excluded",
    "workflow_runs_excluded",
    "model_configs_excluded",
    "reference_canons_excluded",
}
# 实测 project 段 = projects 表整行（迁移 0001~0031 累积后的 13 列）
EXPECTED_PROJECT_ROW_KEYS = {
    "project_id",
    "name",
    "status",
    "created_at",
    "updated_at",
    "premise",
    "genre",
    "target_words",
    "foreshadow_overdue_chapters",
    "word_band_json",
    "genre_pack_id",
    "writing_bible",
    "fast_mode",
}
# 备份包导出的表白名单张数（packages/core/backup/schema.py::EXPORTED_TABLES）
EXPECTED_BACKUP_TABLE_COUNT = 23

# POST /import-backup：实测恒 13 键（projects 整行）；import_warnings 是条件键
EXPECTED_IMPORT_KEYS = EXPECTED_PROJECT_ROW_KEYS | {"import_warnings"}

# GET /signing-check：恒 7 键 + 2 个条件键
EXPECTED_SIGNING_CHECK_KEYS = {
    "project_id",
    "project_name",
    "total_chars",
    "generated_at",
    "items",
    "summary",
    "word_band_source",
}
EXPECTED_SIGNING_CHECK_CONDITIONAL_KEYS = {"genre_opening", "word_band_rejected"}
EXPECTED_SIGNING_ITEM_KEYS = {"key", "level", "detail", "advice"}
EXPECTED_SIGNING_SUMMARY_KEYS = {"pass_count", "warn_count", "fail_count", "info_count"}
# 实测 genre_opening 恒 9 键
EXPECTED_GENRE_OPENING_KEYS = {
    "pack_id",
    "pack_name",
    "rule_count",
    "rules",
    "pass_count",
    "fail_count",
    "unverifiable_count",
    "info_count",
}
# 实测 rule 恒 6 键；keywords_source 是条件键（抽不出词时缺席）
EXPECTED_GENRE_RULE_BASE_KEYS = {
    "check_id",
    "chapter_no",
    "description",
    "requirement",
    "status",
    "detail",
}

# GET /ai-call-logs/{log_id}：实测恒 14 键、无条件键
EXPECTED_AI_CALL_LOG_KEYS = {
    "call_id",
    "run_id",
    "node_run_id",
    "agent_id",
    "model_id",
    "prompt_version",
    "latency_ms",
    "retry_count",
    "error",
    "token_usage",
    "cost",
    "created_at",
    "input_context_ids",
    "output",
}
# 安全红线：这些名字永远不许出现在响应/契约里（router 的 _FORBIDDEN_FIELD_KEYS）
FORBIDDEN_KEYS = {"api_key", "key", "secret", "api_secret", "access_token"}

# POST /style-samples：实测恒 6 键、无条件键
EXPECTED_STYLE_SAMPLE_KEYS = {
    "sample_id",
    "project_id",
    "title",
    "content",
    "created_at",
    "updated_at",
}


# ===========================================================================
# 夹具
# ===========================================================================


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


def sync_request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """在同步用例里发一次请求（端点是同步 def，包在 ASGI 线程池里）。"""
    return asyncio.run(_request(app, method, path, **kwargs))


def _make_project(app, name: str = "契约项目") -> str:
    async def run() -> str:
        async with _client(app) as c:
            r = await c.post("/api/projects", json={"name": name})
            assert r.status_code in (200, 201), r.text
            return r.json()["project_id"]

    return asyncio.run(run())


def _seed_chapter_with_draft(app, pid: str, number: int, content: str) -> str:
    async def run() -> str:
        async with _client(app) as c:
            cid = (
                await c.post(
                    f"/api/projects/{pid}/chapters",
                    json={"number": number, "title": f"第{number}章"},
                )
            ).json()["chapter_id"]
            await c.patch(f"/api/chapters/{cid}", json={"status": "DRAFTED"})
            await c.post(f"/api/chapters/{cid}/drafts", json={"content": content})
            return cid

    return asyncio.run(run())


def _bind_genre_pack(app, pid: str, pack_id: str, payload: dict) -> None:
    async def run() -> None:
        async with _client(app) as c:
            created = await c.post(
                f"/api/projects/{pid}/genre-packs",
                json={"name": "契约题材包", "genre_tag": "契约", "pack_id": pack_id, "payload": payload},
            )
            assert created.status_code == 201, created.text
            bound = await c.post(f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": pack_id})
            assert bound.status_code == 200, bound.text

    asyncio.run(run())


def _seed_ai_call_log(app, call_id: str, **overrides) -> None:
    """插一条 ai_call_logs 行（FK 关闭——run_id 指向不存在的 run 是本测试要的形态）。"""
    row = {
        "run_id": "wfr_synthetic",
        "node_run_id": None,
        "agent_id": None,
        "model_id": "openai/gpt-synthetic",
        "prompt_version": "writer:v7",
        "input_context_ids_json": '["prj_synthetic"]',
        "output_json": '{"ok": 1}',
        "token_usage_json": '{"total": 10}',
        "latency_ms": 123,
        "cost": 0.5,
        "error": None,
        "retry_count": 0,
        "created_at": now_iso(),
    }
    row.update(overrides)
    # 迁移是 lifespan 里跑的；本 helper 可能先于任何请求被调，故自己补一次
    apply_migrations(app.state.settings.db_path)
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT INTO ai_call_logs
               (call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
                input_context_ids_json, output_json, token_usage_json, latency_ms,
                cost, error, retry_count, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (call_id, *(row[k] for k in (
                "run_id", "node_run_id", "agent_id", "model_id", "prompt_version",
                "input_context_ids_json", "output_json", "token_usage_json",
                "latency_ms", "cost", "error", "retry_count", "created_at"))),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_commit_with_foreign_rollback(app, pid: str, cid: str) -> None:
    """插一条 ``commits.rollback_of`` 指向**包外** id 的提交行。

    这是 ``import_warnings`` 的唯一触发路径之一（``BackupService`` 归一化失败
    留痕）。选它而不是「同项目内冲突」那条，是因为它不依赖 0022 的 ``#dup<rowid>``
    后缀形态，触发条件只有「映射里查不到」——判别更直接。
    """
    now = now_iso()
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT OR REPLACE INTO branches (branch_id, project_id, name, "
            "parent_branch_id, base_state_version, status, created_at) "
            "VALUES (?,?,'main',NULL,0,'ACTIVE',?)",
            ("br_synthetic", pid, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO state_deltas (delta_id, chapter_id, workflow_run_id, "
            "previous_state_version, delta_version, schema_version, payload_json, "
            "status, supersedes, created_by, created_at) "
            "VALUES (?,?,?,0,1,'state-delta-v0','{}','applied',NULL,'tester',?)",
            ("dlt_synthetic", cid, "wfr_synthetic", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO commits (commit_id, project_id, branch_id, "
            "chapter_id, previous_state_version, resulting_state_version, delta_id, "
            "validation_json, author_approval_json, timestamp, workflow_run_id, rollback_of) "
            "VALUES (?,?,?,?,0,1,?,'{}','{}',?,'wfr_synthetic','cmt_NOT_IN_PACKAGE')",
            ("cmt_synthetic", pid, "br_synthetic", cid, "dlt_synthetic", now),
        )
        conn.execute("UPDATE chapters SET status='COMMITTED' WHERE chapter_id=?", (cid,))
        conn.commit()
    finally:
        conn.close()


# ===========================================================================
# 1. GET /projects/{id}/backup —— 文档级 response_model
# ===========================================================================


def _schemas(app) -> dict:
    return app.openapi()["components"]["schemas"]


def test_backup_package_declared_and_referenced(tmp_path: Path):
    """OpenAPI 里有 BackupPackage / BackupMetadata，且被该端点 200 引用。"""
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    assert "BackupPackage" in schemas, "BackupPackage 未进 OpenAPI"
    assert "BackupMetadata" in schemas, "BackupMetadata 未进 OpenAPI"
    ref = app.openapi()["paths"]["/api/projects/{project_id}/backup"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/BackupPackage"), ref


def test_backup_openapi_properties_match_hardcoded(tmp_path: Path):
    """⚠️ 本端点的**判别点**：OpenAPI ``properties`` × 硬编码期望。

    GET /backup 的 handler 返回 ``JSONResponse``，FastAPI 跳过响应模型 ⇒
    「少声明一个字段 ⇒ 响应少一个键」这条链在这里**恒为真**，抓不到任何东西。
    真正会坏的是**发布出去的契约**：前端 codegen 按 ``properties`` 生成类型，
    少一个键就是类型里少一个字段。故断言面必须是 OpenAPI，不是响应体。
    """
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    props = set(schemas["BackupPackage"]["properties"])
    assert props == EXPECTED_BACKUP_KEYS, (
        f"BackupPackage.properties 不符：多={props - EXPECTED_BACKUP_KEYS} "
        f"少={EXPECTED_BACKUP_KEYS - props}"
    )
    assert set(schemas["BackupPackage"]["required"]) == EXPECTED_BACKUP_KEYS, (
        "BackupPackage 7 键全是恒在键，应全部进 required"
    )

    meta_props = set(schemas["BackupMetadata"]["properties"])
    assert meta_props == EXPECTED_BACKUP_METADATA_KEYS, (
        f"BackupMetadata.properties 不符：多={meta_props - EXPECTED_BACKUP_METADATA_KEYS} "
        f"少={EXPECTED_BACKUP_METADATA_KEYS - meta_props}"
    )
    assert set(schemas["BackupMetadata"]["required"]) == EXPECTED_BACKUP_METADATA_KEYS


def test_backup_package_model_fields_match_hardcoded():
    """模型字段集 vs 硬编码期望（第二道，防「模型自洽但契约已漂」）。"""
    assert set(BackupPackage.model_fields) == EXPECTED_BACKUP_KEYS
    assert set(BackupMetadata.model_fields) == EXPECTED_BACKUP_METADATA_KEYS


def test_backup_response_body_keys_match_hardcoded(tmp_path: Path):
    """实际响应体：顶层 7 键 + metadata 10 键 + project 13 键 + tables 23 张。"""
    app = _create_app(tmp_path)
    pid = _make_project(app, "备份契约")
    _seed_chapter_with_draft(app, pid, 1, "正文" * 30)

    r = sync_request(app, "GET", f"/api/projects/{pid}/backup")
    assert r.status_code == 200, r.text
    # 仍是一份可被导入的 JSON 包（不是二进制下载体）——与 /export 的区别就在这
    assert r.headers["content-type"].startswith("application/json"), r.headers
    assert "attachment" in r.headers["content-disposition"]

    pkg = r.json()
    assert set(pkg) == EXPECTED_BACKUP_KEYS, (
        f"多={set(pkg) - EXPECTED_BACKUP_KEYS} 少={EXPECTED_BACKUP_KEYS - set(pkg)}"
    )
    assert set(pkg["metadata"]) == EXPECTED_BACKUP_METADATA_KEYS
    assert set(pkg["project"]) == EXPECTED_PROJECT_ROW_KEYS
    assert len(pkg["tables"]) == EXPECTED_BACKUP_TABLE_COUNT, (
        f"导出表数 {len(pkg['tables'])} ≠ {EXPECTED_BACKUP_TABLE_COUNT}"
    )
    assert pkg["format"] == "novelos-backup"


# ===========================================================================
# 2. POST /projects/import-backup —— 运行时过滤生效
# ===========================================================================


def test_import_backup_declared_and_keys_match(tmp_path: Path):
    """OpenAPI 声明 + 实测响应键集（13 键，**无** import_warnings）。"""
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    assert "ImportedProject" in schemas, "ImportedProject 未进 OpenAPI"
    ref = app.openapi()["paths"]["/api/projects/import-backup"]["post"]["responses"][
        "201"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ImportedProject"), ref

    props = set(schemas["ImportedProject"]["properties"])
    assert props == EXPECTED_IMPORT_KEYS, (
        f"多={props - EXPECTED_IMPORT_KEYS} 少={EXPECTED_IMPORT_KEYS - props}"
    )

    pid = _make_project(app, "导入契约")
    _seed_chapter_with_draft(app, pid, 1, "正文" * 30)
    pkg = sync_request(app, "GET", f"/api/projects/{pid}/backup").json()
    imported = sync_request(app, "POST", "/api/projects/import-backup", json=pkg)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert set(body) == EXPECTED_PROJECT_ROW_KEYS, (
        f"多={set(body) - EXPECTED_PROJECT_ROW_KEYS} "
        f"少={EXPECTED_PROJECT_ROW_KEYS - set(body)}"
    )
    assert body["project_id"] != pid, "导入必须是**新**项目"


def test_import_backup_conditional_keys_absent_not_null(tmp_path: Path):
    """无警告时 ``import_warnings`` **整个键缺席**——不是 null。

    撤掉 ``response_model_exclude_unset=True`` 必红：缺席的键会被序列化成 null，
    于是「没有警告」与「警告列表为空」在前端不可区分，而后者正是要作者看见的。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "导入无警告")
    pkg = sync_request(app, "GET", f"/api/projects/{pid}/backup").json()
    body = sync_request(app, "POST", "/api/projects/import-backup", json=pkg).json()
    assert "import_warnings" not in body, (
        f"无警告时 import_warnings 应缺席，实际 {body.get('import_warnings')!r}——"
        "多半是缺 response_model_exclude_unset=True"
    )


def test_import_backup_warning_key_survives_response_model(tmp_path: Path):
    """⚠️ 条件键**在场**时必须透出，且被钉进 OpenAPI properties。

    夹具走 ``commits.rollback_of`` 指向包外 id 的那条路径（BackupService 归一化
    失败 ⇒ ``_import_warnings`` 非空 ⇒ 键挂上）。这条同时验两件事：声明没被
    过滤掉，以及 exclude_unset 没有反向把它吃掉。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "导入有警告")
    cid = _seed_chapter_with_draft(app, pid, 1, "正文" * 30)
    _seed_commit_with_foreign_rollback(app, pid, cid)

    pkg = sync_request(app, "GET", f"/api/projects/{pid}/backup").json()
    body = sync_request(app, "POST", "/api/projects/import-backup", json=pkg).json()
    assert set(body) == EXPECTED_IMPORT_KEYS, (
        f"多={set(body) - EXPECTED_IMPORT_KEYS} 少={EXPECTED_IMPORT_KEYS - set(body)}"
    )
    warnings = body["import_warnings"]
    assert isinstance(warnings, list) and warnings, f"警告未透出：{body!r}"
    assert any("rollback_of" in w for w in warnings), warnings


def test_imported_project_allows_unknown_project_columns():
    """⚠️ ``extra="allow"``：projects 表下一个迁移加的列不得被静默裁掉。

    症状形状：数据在库里、API 读不到、零报错（与「响应里必须有该键」的断言
    **互相独立**——本仓已有 :class:`ImportedProject` 这类 extra 模型的前车之鉴，
    见 AGENTS 坑区「登记了却不生效」族）。撤掉 ``extra="allow"`` 必红。
    """
    stored = dict.fromkeys(EXPECTED_PROJECT_ROW_KEYS - {"import_warnings"}, None)
    stored.update(
        {
            "project_id": "prj_1",
            "name": "新列项目",
            "status": "ACTIVE",
            "created_at": "2026-10-15T00:00:00Z",
            "updated_at": "2026-10-15T00:00:00Z",
            "foreshadow_overdue_chapters": 30,
            "brand_new_column_from_migration_0032": "keep me",
        }
    )
    dumped = ImportedProject(**stored).model_dump()
    assert dumped["brand_new_column_from_migration_0032"] == "keep me", (
        "迁移新增列被裁掉 ⇒ extra='allow' 没了"
    )


def test_imported_project_schema_allows_additional_properties(tmp_path: Path):
    """extra="allow" 必须在 OpenAPI 里留痕（``additionalProperties: true``）。

    ⚠️ 只断言「响应里有该键」抓不到「声明没了」：extra 身份透出的键在两种情况下
    都在响应里（突变 M20 实测存活）。schema 这一侧才是声明的证据。
    """
    app = _create_app(tmp_path)
    schema = _schemas(app)["ImportedProject"]
    assert schema.get("additionalProperties") is True, (
        f"ImportedProject 未声明 additionalProperties: true ⇒ {schema.get('additionalProperties')!r}"
    )


def test_imported_project_model_fields_match_hardcoded():
    assert set(ImportedProject.model_fields) == EXPECTED_IMPORT_KEYS


# ===========================================================================
# 3. GET /projects/{id}/export —— 跳过建模，把「跳过」钉成断言
# ===========================================================================


def test_export_returns_file_not_json(tmp_path: Path):
    """⚠️ 本端点**刻意不建模**：返回的是文件体，给 Pydantic 模型只会造出假契约。

    三个 format 各自验：Content-Type 不是 JSON、body 是字节流、OpenAPI 的 200
    响应**不含** ``application/json``（含了就说明有人后来又加了 JSON 声明）。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "导出契约")
    _seed_chapter_with_draft(app, pid, 1, "正文" * 30)

    for fmt, expect_head in (
        ("txt", b"\xef\xbb\xbf"),  # UTF-8 BOM
        ("docx", b"PK\x03\x04"),  # ZIP/OOXML 魔数
        ("fanqie", b"\xef\xbb\xbf"),
    ):
        r = sync_request(app, "GET", f"/api/projects/{pid}/export?format={fmt}")
        assert r.status_code == 200, f"{fmt}: {r.text}"
        assert not r.headers["content-type"].startswith("application/json"), (
            f"{fmt} 的 Content-Type 是 JSON，导出端点形状已变"
        )
        assert r.content.startswith(expect_head), f"{fmt} body 魔数不对：{r.content[:8]!r}"
        assert "attachment" in r.headers["content-disposition"]

    # ⚠️ 实测（此处先验被自己推翻过一次）：FastAPI 对 ``-> Response`` 端点的 200 响应
    # **照样**吐一个 ``application/json: {schema: {}}`` 占位——既非 $ref 也无任何属性，
    # codegen 拿它产不出类型（故无害），但它确实会误导读 OpenAPI 的人。
    # ⇒ 判据不是「content 里有没有 application/json 这个键」，而是
    #   「**有没有声明出 JSON 结构**」。真要给 /export 配准确的 media type 得写
    #   decorator 上的 ``responses=``（三种 format 三种类型），本批次不做、只留痕。
    declared = (
        app.openapi()["paths"]["/api/projects/{project_id}/export"]["get"]["responses"][
            "200"
        ]
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    assert declared == {}, (
        f"文件端点的 200 响应竟声明出了 JSON 结构：{declared} ⇒ 有人给 /export "
        "加了 response_model，会让前端 codegen 生成「下载返回 JSON 对象」的假契约"
    )


# ===========================================================================
# 4. GET /projects/{id}/signing-check
# ===========================================================================


def test_signing_check_declared_and_keys_match(tmp_path: Path):
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    assert "SigningCheckResult" in schemas, "SigningCheckResult 未进 OpenAPI"
    ref = app.openapi()["paths"]["/api/projects/{project_id}/signing-check"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/SigningCheckResult"), ref
    assert set(schemas["SigningCheckResult"]["properties"]) == (
        EXPECTED_SIGNING_CHECK_KEYS | EXPECTED_SIGNING_CHECK_CONDITIONAL_KEYS
    )

    pid = _make_project(app, "体检契约")
    _seed_chapter_with_draft(app, pid, 1, "林昭被人当面羞辱。" + "字" * 1700)
    r = sync_request(app, "GET", f"/api/projects/{pid}/signing-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == EXPECTED_SIGNING_CHECK_KEYS, (
        f"多={set(body) - EXPECTED_SIGNING_CHECK_KEYS} "
        f"少={EXPECTED_SIGNING_CHECK_KEYS - set(body)}"
    )
    assert body["items"], "夹具应至少产出一条体检项"
    for item in body["items"]:
        assert set(item) == EXPECTED_SIGNING_ITEM_KEYS, sorted(item)
    assert set(body["summary"]) == EXPECTED_SIGNING_SUMMARY_KEYS


def test_signing_check_conditional_keys_absent_not_null(tmp_path: Path):
    """未绑题材包 ⇒ ``genre_opening`` / ``word_band_rejected`` **整个键缺席**。

    这是 exclude_unset 的判别点：撤掉它，两个缺席的条件键会变成 null，于是
    「没绑定题材包」与「绑定了但包里没写 opening_rules」再也分不开。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "无题材包")
    _seed_chapter_with_draft(app, pid, 1, "正文" * 100)
    body = sync_request(app, "GET", f"/api/projects/{pid}/signing-check").json()
    for key in EXPECTED_SIGNING_CHECK_CONDITIONAL_KEYS:
        assert key not in body, (
            f"{key} 在无题材包时缺席，实际 {body.get(key)!r}——"
            "多半是缺 response_model_exclude_unset=True"
        )


def test_signing_check_genre_opening_section_shape(tmp_path: Path):
    """题材包声明 opening_rules ⇒ genre_opening 段 9 键；rules 的两种形态都覆盖。

    两条规则刻意做出两种 ``keywords_source`` 形态：
    - ``有词可抽`` ⇒ 该键出现（auto/curated）；
    - ``必须出现``（纯噪声要求，抽不出词）⇒ 该键**缺席**，status 转 unverifiable。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "题材开篇")
    _seed_chapter_with_draft(app, pid, 1, "系统绑定。「你算什么？」" + "字" * 1700)
    _bind_genre_pack(
        app,
        pid,
        "gp_contract",
        {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [
                {
                    "check_id": "sys_bind_ch1",
                    "description": "系统绑定不得晚于第 1 章",
                    "chapter_no": 1,
                    "requirement": "第 1 章必须出现系统绑定",
                },
                {"check_id": "semantic_only", "chapter_no": 1, "requirement": "必须出现"},
            ],
        },
    )

    body = sync_request(app, "GET", f"/api/projects/{pid}/signing-check").json()
    assert "genre_opening" in body, "绑定了带 opening_rules 的包却没有该段"
    section = body["genre_opening"]
    assert set(section) == EXPECTED_GENRE_OPENING_KEYS, sorted(section)
    assert section["rule_count"] == 2

    by_id = {r["check_id"]: r for r in section["rules"]}
    assert by_id["sys_bind_ch1"]["status"] == "pass"
    assert by_id["sys_bind_ch1"]["keywords_source"] in ("auto", "curated")
    assert by_id["semantic_only"]["status"] == "unverifiable"
    assert "keywords_source" not in by_id["semantic_only"], (
        f"抽不出词时该键应缺席：{by_id['semantic_only']}"
    )
    for rule in section["rules"]:
        assert set(rule) <= EXPECTED_GENRE_RULE_BASE_KEYS | {"keywords_source"}
        assert set(rule) - {"keywords_source"} == EXPECTED_GENRE_RULE_BASE_KEYS, sorted(rule)


def test_signing_check_word_band_rejected_is_conditional(tmp_path: Path):
    """题材包声明了**次序颠倒**的 chapter_word_band ⇒ ``word_band_rejected: true``。

    对照：同批另建的「无题材包」用例里该键缺席（见上一条）。正反一对，
    少任何一条都可能被「条件键被整体摘掉」蒙过去。
    """
    app = _create_app(tmp_path)
    pid = _make_project(app, "脏字数带")
    _seed_chapter_with_draft(app, pid, 1, "正文" * 100)
    _bind_genre_pack(
        app,
        pid,
        "gp_dirty_band",
        {
            "schema_version": "genre-pack.v1.1.0",
            # low > high ⇒ normalize_word_band 判脏 ⇒ 按默认口径 + 显性化留痕
            "pacing": {"chapter_word_band": {"low": 2400, "high": 2000, "floor": 1800}},
        },
    )
    body = sync_request(app, "GET", f"/api/projects/{pid}/signing-check").json()
    assert body["word_band_source"] == "default", body["word_band_source"]
    assert body["word_band_rejected"] is True, body.get("word_band_rejected")
    # 未声明 opening_rules ⇒ genre_opening 仍缺席（两个条件键互不牵连）
    assert "genre_opening" not in body, sorted(body)


def test_signing_check_model_fields_match_hardcoded():
    assert set(SigningCheckResult.model_fields) == (
        EXPECTED_SIGNING_CHECK_KEYS | EXPECTED_SIGNING_CHECK_CONDITIONAL_KEYS
    )
    assert set(SigningCheckItem.model_fields) == EXPECTED_SIGNING_ITEM_KEYS
    assert set(SigningCheckSummary.model_fields) == EXPECTED_SIGNING_SUMMARY_KEYS


# ===========================================================================
# 5. GET /ai-call-logs/{log_id}
# ===========================================================================


def test_ai_call_log_detail_declared_and_keys_match(tmp_path: Path):
    """满字段行与全空行**都**必须给出全部 14 键（null 也是键，不是缺席）。"""
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    assert "AiCallLogDetail" in schemas, "AiCallLogDetail 未进 OpenAPI"
    ref = app.openapi()["paths"]["/api/ai-call-logs/{log_id}"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/AiCallLogDetail"), ref
    assert set(schemas["AiCallLogDetail"]["properties"]) == EXPECTED_AI_CALL_LOG_KEYS

    _seed_ai_call_log(app, "aic_full")
    _seed_ai_call_log(
        app,
        "aic_sparse",
        node_run_id=None,
        agent_id=None,
        model_id=None,
        prompt_version=None,
        input_context_ids_json="[]",
        output_json=None,
        token_usage_json=None,
        latency_ms=None,
        cost=None,
        error="boom",
        retry_count=2,
    )

    full = sync_request(app, "GET", "/api/ai-call-logs/aic_full")
    assert full.status_code == 200, full.text
    body = full.json()
    assert set(body) == EXPECTED_AI_CALL_LOG_KEYS, (
        f"多={set(body) - EXPECTED_AI_CALL_LOG_KEYS} "
        f"少={EXPECTED_AI_CALL_LOG_KEYS - set(body)}"
    )
    assert body["token_usage"] == {"total": 10}, "token_usage_json 应被解析成 token_usage"
    assert body["input_context_ids"] == ["prj_synthetic"]

    sparse = sync_request(app, "GET", "/api/ai-call-logs/aic_sparse").json()
    assert set(sparse) == EXPECTED_AI_CALL_LOG_KEYS, sorted(sparse)
    for key in ("node_run_id", "agent_id", "model_id", "prompt_version",
                "latency_ms", "cost", "token_usage", "output"):
        assert sparse[key] is None, f"{key} 在空值时应为 null，实际 {sparse[key]!r}"
    assert sparse["retry_count"] == 2 and sparse["error"] == "boom"
    assert sparse["input_context_ids"] == [], "解析失败/NULL 回落成 [] 而不是 null"


def test_ai_call_log_detail_never_exposes_secrets(tmp_path: Path):
    """安全红线：不许出现 api_key / key / secret / access_token。"""
    app = _create_app(tmp_path)
    schema = _schemas(app)["AiCallLogDetail"]
    leaked = FORBIDDEN_KEYS & set(schema["properties"])
    assert not leaked, f"OpenAPI 里出现敏感字段：{leaked}"
    assert not FORBIDDEN_KEYS & set(AiCallLogDetail.model_fields)


def test_ai_call_log_detail_model_fields_match_hardcoded():
    assert set(AiCallLogDetail.model_fields) == EXPECTED_AI_CALL_LOG_KEYS


# ===========================================================================
# 6. POST /projects/{pid}/style-samples
# ===========================================================================


def test_style_sample_create_declared_and_keys_match(tmp_path: Path):
    app = _create_app(tmp_path)
    schemas = _schemas(app)
    assert "StyleSample" in schemas, "StyleSample 未进 OpenAPI"
    ref = app.openapi()["paths"]["/api/projects/{pid}/style-samples"]["post"][
        "responses"
    ]["201"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/StyleSample"), ref
    assert set(schemas["StyleSample"]["properties"]) == EXPECTED_STYLE_SAMPLE_KEYS
    assert set(schemas["StyleSample"]["required"]) == EXPECTED_STYLE_SAMPLE_KEYS

    pid = _make_project(app, "文风契约")
    created = sync_request(
        app, "POST", f"/api/projects/{pid}/style-samples",
        json={"title": "样张", "content": "短句。" * 20},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == EXPECTED_STYLE_SAMPLE_KEYS, (
        f"多={set(body) - EXPECTED_STYLE_SAMPLE_KEYS} "
        f"少={EXPECTED_STYLE_SAMPLE_KEYS - set(body)}"
    )
    assert body["project_id"] == pid
    assert body["created_at"] == body["updated_at"], "创建时两枚时间戳应相等"


def test_style_sample_model_fields_match_hardcoded():
    assert set(StyleSample.model_fields) == EXPECTED_STYLE_SAMPLE_KEYS
