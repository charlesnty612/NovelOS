"""chapter-write checkpoint 写放大治理回归测试（V3.9 批次 2.4）。

背景：chapter-write 此前未声明 ``checkpoint_exclude``，引擎每节点完成都把整份 ctx
序列化落 ``workflow_runs.checkpoint_json``（engine.py:712），其中 writer_input 生产
实测 19KB~125KB → 单章 7 节点 ≈ 7 次百 KB 级重复序列化 UPDATE。chapter-commit /
chapter-review 早已示范排除清单。

覆盖：
1. 契约：``WORKFLOW["checkpoint_exclude"]`` 收录大 payload 键 + 全部节点镜像键；
   正文 / 控制键（polished_prose / writer_output / scene_plan / length_report 等）
   必须保留——它们承担 run 详情可读性与节点兜底输入。
2. resume 前提：chapter-write 全链无 Human 节点 ⇒ 引擎不会 PAUSE ⇒ 不存在
   「从 checkpoint_json 反序列化重建 ctx 续跑」的路径，排除清单无恢复语义风险。
3. 端到端（mock）：预置 60KB 级旧稿使 writer_input 复刻生产量级，断言
   - 落库 checkpoint_json 不含任何被排除键；
   - 体积 < 40KB（治理前同量级 payload 必然远超）；
   - 被排除的 writer_input 仍在 run 内 ctx 流转（writer 节点产出可见，体积 >50KB）；
   - 下游 save_draft 照旧落稿（drafts.content == 最终 prose）。
4. 前后对照：同构场景下「不传 exclude」的 run（>60KB）vs「传清单」的 run
   （<40KB，实测约 4.4KB），比值 >5×。
5. condense 读键：排除只作用于落盘序列化，run 内 ctx 未被剥离。

注：章节计划直插 DB（不跑 chapter-plan 工作流）——本用例只验证 chapter-write。
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
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.workflows.chapter_write.pipeline import WORKFLOW

# ---------------------------------------------------------------------------
# 异步轮询 / 装配 helpers（与既有 chapter_write 测试一致）
# ---------------------------------------------------------------------------


async def _get_run_via_http(app, run_id: str) -> dict | None:
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    import time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        last_run = run
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")


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


async def _make_project(app, name: str = "checkpoint 排除测试") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Mock 脚本
# ---------------------------------------------------------------------------


def _director_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": "ch_xxx",
                "chapter_goal": "夜谈中女主第一次怀疑男主",
                "core_conflict": "求真 vs 隐瞒",
                "turning_point": "林渊回避黑玉佩细节",
                "expected_role": "escalation",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "夜访场景设置",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "建立信任基础",
                    },
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "建议场景数 1",
            },
            ensure_ascii=False,
        )
    ]


def _scene_planner_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "scene-plan.v1",
                "prompt_version": "scene_planner:v1",
                "chapter_id": "ch_xxx",
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "purpose": "夜访玉惜轩：黑玉佩引入对话",
                        "characters": [],
                        "location": None,
                        "conflict": "苏婉清追问，林渊回避",
                        "turn": "林渊转移话题",
                        "time_in_story": "戌时",
                        "pov": "third_person_limited",
                        "pov_character_id": None,
                        "information_boundary": ["黑玉佩真正来历"],
                        "ending_hook": "苏婉清决定暗中调查",
                        "slots": [
                            {
                                "slot_id": "scene_001_desc_01",
                                "type": "description",
                                "purpose": "建立夜访场景气氛",
                                "characters": [],
                                "target_mood": "清寂微凉",
                                "constraints": ["不用元叙述"],
                            },
                        ],
                    }
                ],
                "notes_for_writer": "",
                "deviations": [],
            },
            ensure_ascii=False,
        )
    ]


_WRITER_PROSE = "灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。"


def _writer_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": "ch_xxx",
                "prose": _WRITER_PROSE,
                "self_report": {
                    "slots_filled": ["scene_001_desc_01"],
                    "word_count": len(_WRITER_PROSE),
                    "scene_count": 1,
                    "deviations": [],
                    "forbidden_word_hits": [],
                    "self_check_notes": "",
                },
            },
            ensure_ascii=False,
        )
    ]


def _mock_providers() -> dict[str, list[str]]:
    """writer-only mock 流：scene_planner 走 mock，polisher / condense 直通透传。"""
    return {
        "director": _director_script(),
        "scene_planner": _scene_planner_script(),
        "writer": _writer_script(),
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

# 预置旧稿长度：20k 中文字符 ≈ 60KB（UTF-8），复刻生产 writer_input 量级
# （roadmap 批次 2.4 记「整份 writer_input 实测最大 125KB」）。
_BIG_DRAFT_CHARS = 20_000


def _seed_plan_json(db_path, chapter_id: str, plan: dict) -> None:
    """直插章节计划：本用例只关心 chapter-write，绕开 chapter-plan 工作流。

    chapters.status 默认 PLANNED，正是 chapter-write 的首写前置状态。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def _minimal_plan() -> dict:
    return {
        "chapter_goal": "夜谈中女主第一次怀疑男主",
        "core_conflict": "求真 vs 隐瞒",
        "turning_point": "林渊回避黑玉佩细节",
        "expected_role": "escalation",
        "expected_word_count": 3000,
        "key_beats": [],
        "deviations": [],
    }


def _seed_existing_draft(db_path, chapter_id: str, content: str) -> None:
    """直插一行旧稿：让 writer 节点走 revise 模式，draft_text 全文进 writer_input。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts "
            "(draft_id, chapter_id, version, content, created_by, prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'agent:writer:v1', 'writer:v1', 'mock/mock', ?)",
            (new_id("dr"), chapter_id, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_revision_note(db_path, chapter_id: str, note: str) -> None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
        plan = json.loads(row["plan_json"]) if row and row["plan_json"] else {}
        plan["revision_note"] = note
        conn.execute(
            "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def _checkpoint_raw(db_path, run_id: str) -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"run {run_id!r} 不在 workflow_runs"
    return row["checkpoint_json"] or "{}"


def _node_output(db_path, run_id: str, node_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT output_json FROM workflow_run_nodes WHERE run_id = ? AND node_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, node_id),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"run {run_id!r} 无 {node_id!r} 节点行"
    return json.loads(row["output_json"] or "{}")


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------------------
# Tests：静态契约
# ---------------------------------------------------------------------------


def test_checkpoint_exclude_contract_covers_large_payload_and_mirrors() -> None:
    """排除清单必须覆盖大 payload 与全部节点镜像键；正文 / 控制键必须保留。"""
    exclude = set(WORKFLOW["checkpoint_exclude"])

    # 体积大头 + 派生大对象
    for key in ("writer_input", "scene_planner_output", "polisher_output"):
        assert key in exclude, f"{key!r} 应被排除（体积大头），实际清单={sorted(exclude)}"

    # 节点镜像键：ctx[node_id] 与 workflow_run_nodes.output_json 逐字节重复
    node_ids = {node.node_id for node in WORKFLOW["nodes"]}
    assert node_ids <= exclude, f"节点镜像键未全排除：缺 {sorted(node_ids - exclude)}"
    assert len(node_ids) == 7, node_ids

    # 必保留：正文 / 结构化产出 / 下游节点依赖 / 控制流小键
    for kept in (
        "polished_prose",
        "writer_output",
        "scene_plan",
        "loaded_plan",
        "length_report",
        "length_check_passed",
        "condense_rounds",
        "condense_status",
        "target_word_count",
        "word_band_cfg",
        "db_path",
        "project_id",
        "chapter_id",
        "run_id",
        "_reference_canon_consumed",
    ):
        assert kept not in exclude, f"{kept!r} 不应被排除（run 详情 / 下游节点依赖）"


def test_checkpoint_size_before_after_same_scenario(tmp_path: Path):
    """前后对照：同一 mock 场景，未传 checkpoint_exclude 的 run vs 传清单的 run。

    两份 run 的 ctx 内容同构（同项目、同尺寸旧稿、同 mock 脚本），差异只在
    exclude 清单 → checkpoint 体积差即写放大治理的净收益。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, name="checkpoint 前后对照")
            sizes: dict[str, int] = {}
            for label, number, exclude in (
                ("before", 1, None),
                ("after", 2, WORKFLOW["checkpoint_exclude"]),
            ):
                cid = await _make_chapter(app, pid, number, f"第{number}章")
                _seed_plan_json(db_path, cid, _minimal_plan())
                _seed_existing_draft(db_path, cid, "旧稿正文。" * (_BIG_DRAFT_CHARS // 5))
                _seed_revision_note(db_path, cid, "把夜探的试探写得更克制")
                engine = WorkflowEngine(db_path)
                run_id = engine.start_with_nodes(
                    "chapter-write",
                    WORKFLOW["nodes"],
                    chapter_id=cid,
                    initial_ctx={
                        "db_path": str(db_path),
                        "project_id": pid,
                        "chapter_id": cid,
                    },
                    mock_providers=_mock_providers(),
                    checkpoint_exclude=exclude,
                )
                run_row = get_run(db_path, run_id)
                assert run_row is not None and run_row["status"] == "COMPLETED", run_row
                sizes[label] = len(_checkpoint_raw(db_path, run_id).encode("utf-8"))

            # before 量级 = writer_input(≈60KB) + 节点镜像；after 应实现数量级下降
            assert sizes["before"] > 60_000, sizes
            assert sizes["after"] < 40_000, sizes
            assert sizes["before"] > sizes["after"] * 5, sizes

    asyncio.run(run())


def test_checkpoint_exclude_premise_no_human_node_no_pause() -> None:
    """排除清单无 resume 语义风险的前提：本链无 Human 节点 ⇒ 永不 PAUSE。

    引擎唯一「从 checkpoint_json 反序列化重建 ctx」的路径是 ``resume``
    （engine.py:_prepare_resume_ctx），只服务 PAUSED run；崩溃恢复
    ``recover_interrupted_runs`` 只标 FAILED、不重建 ctx。因此本链 run 不可能
    以「被排除键缺失」的 ctx 继续执行。
    """
    assert all(node.kind != "Human" for node in WORKFLOW["nodes"]), (
        [n.node_id for n in WORKFLOW["nodes"] if n.kind == "Human"]
    )
    assert WORKFLOW["checkpoint_exclude"], "checkpoint_exclude 不应为空"


# ---------------------------------------------------------------------------
# Tests：端到端（mock run）
# ---------------------------------------------------------------------------


def test_chapter_write_checkpoint_excludes_large_payloads(tmp_path: Path):
    """60KB 级 writer_input 场景：checkpoint 不含被排除键、体积 <40KB，run 内流转不变。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "夜叩青石")

            # 1) 预置章节计划（绕开 chapter-plan 工作流：本用例只验证 chapter-write）
            _seed_plan_json(db_path, cid, _minimal_plan())

            # 2) 预置旧稿 + revision_note：writer 走 revise 模式，旧稿全文进 writer_input
            #    （复刻生产 125KB payload 的成因，让体积断言有区分度）
            _seed_existing_draft(db_path, cid, "旧稿正文。" * (_BIG_DRAFT_CHARS // 5))
            _seed_revision_note(db_path, cid, "把夜谈的怀疑写得更克制")

            # 3) write（mock writer-only）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": _mock_providers()},
            )
            assert r.status_code == 201, r.text
            terminal = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert terminal["status"] == "COMPLETED", terminal.get("error")
            run_id = terminal["run_id"]

            # ---- 断言 1：落库 checkpoint 不含任何被排除键 ----
            raw = _checkpoint_raw(db_path, run_id)
            ckpt = json.loads(raw)
            for key in WORKFLOW["checkpoint_exclude"]:
                assert key not in ckpt, (
                    f"checkpoint_json 不应含被排除键 {key!r}；实际键={sorted(ckpt)}"
                )

            # ---- 断言 2：必保留键仍在（run 详情可读 + 下游依赖）----
            assert ckpt["polished_prose"] == _WRITER_PROSE, ckpt.get("polished_prose")
            assert ckpt["writer_output"]["prose"] == _WRITER_PROSE
            assert ckpt["scene_plan"]["scenes"][0]["scene_id"] == "scene_001"
            assert ckpt["loaded_plan"]["chapter_goal"]
            assert ckpt["length_report"]["visible_chars"] > 0
            assert ckpt["condense_status"] == "skipped_no_mock"
            assert ckpt["scene_planner_status"] == "ok"

            # ---- 断言 3：体积 <40KB；反事实（writer_input 未被排除）远超上限 ----
            ckpt_bytes = len(raw.encode("utf-8"))
            writer_out = _node_output(db_path, run_id, "writer")
            writer_input_bytes = _json_bytes(writer_out["writer_input"])
            assert writer_input_bytes > 50_000, (
                f"writer_input 体积 {writer_input_bytes} 未达生产量级，本用例失去区分度"
            )
            assert ckpt_bytes < 40_000, f"checkpoint 体积 {ckpt_bytes} 字节，未达到治理目标"
            assert ckpt_bytes + writer_input_bytes > 60_000, (
                "反事实体积应远超上限（说明排除清单确实砍掉了写放大主因）"
            )

            # ---- 断言 4：被排除键在 run 内 ctx 照常流转 ----
            # 节点产出的权威副本在 workflow_run_nodes.output_json；writer_input 是
            # writer 节点产出的一部分，说明被排除键在 run 内存在且体积未变。
            assert "writer_input" in writer_out
            assert writer_out["writer_input"]["draft_text"] == "旧稿正文。" * (_BIG_DRAFT_CHARS // 5)
            assert writer_out["writer_input"]["mode"] == "revise"
            assert "writer_output" in writer_out
            # scene_planner / polisher 节点产出（镜像与派生大对象）同样在落库节点行可见
            assert _node_output(db_path, run_id, "scene_planner")["scene_plan"]["scenes"]
            assert "polished_prose" in _node_output(db_path, run_id, "polisher")
            assert "length_report" in _node_output(db_path, run_id, "length_check")

            # ---- 断言 5：下游 save_draft 照旧落稿（最终 prose 为准）----
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT content, version FROM drafts WHERE chapter_id = ? "
                    "ORDER BY version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                status = conn.execute(
                    "SELECT status FROM chapters WHERE chapter_id = ?", (cid,)
                ).fetchone()["status"]
            finally:
                conn.close()
            assert row["content"] == _WRITER_PROSE, row["content"]
            assert row["version"] == 2, row["version"]  # v1 = 预置旧稿
            assert status == "DRAFTED", status

            # 保留键体积：正文级（<40KB 断言的具体构成，防止未来某天把正文也排除了）
            assert _json_bytes(ckpt["polished_prose"]) < 5_000

    asyncio.run(run())


def test_chapter_write_condense_still_reads_excluded_keys_in_run(tmp_path: Path):
    """condense 节点在 run 内仍读到 length_report / polished_prose（排除只影响落盘）。

    走「mock 无 condense 脚本」的透传分支即可证明 run 内 ctx 未受影响：
    condense 判定基于 ctx['length_check_passed'] / ctx['length_report']，
    若 ctx 被剥离，节点会走 skipped_empty 而非 skipped_no_mock。
    """
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, name="condense 读键测试")
            cid = await _make_chapter(app, pid, 1, "短章")
            _seed_plan_json(db_path, cid, _minimal_plan())

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": _mock_providers()},
            )
            assert r.status_code == 201, r.text
            terminal = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert terminal["status"] == "COMPLETED", terminal.get("error")

            condense_out = _node_output(db_path, terminal["run_id"], "condense")
            # 短正文（~24 字）必然低于字数带 → length_check_passed=False；
            # condense 读到了 ctx 里的 polished_prose（非空）与 length_report，
            # 只因无 condense mock 脚本而透传（而非 skipped_empty）。
            assert condense_out["condense_status"] == "skipped_no_mock", condense_out
            assert condense_out["polished_prose"] == _WRITER_PROSE

    asyncio.run(run())
