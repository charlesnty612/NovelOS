"""V1.4 集成测试：参照系消费可观测 + enforce 改稿引导（chapter_commit pipeline）。

覆盖（任务书 V1.4）：
1. ``quality_gate`` 节点的 ``reference_consumption`` 字段落进 ``checkpoint_json``：
   - 文件存在时，``files`` 列出文件名 + 字符数，``total_chars`` 与单文件 chars 之和一致。
   - 目录不存在时，``files == []``、``files_count == 0``，节点仍正常返回。
2. enforce 模式阻断时，``revision_guidance`` 出现：
   - ``runs.error`` 含 ``"| guidance=<json>"`` 段，且 JSON 可解析；
   - 解析后包含至少 1 条 ``dimension / score / threshold / top_issues / rule_hint``；
   - ``score < threshold`` 且 ``rule_hint`` 非空（可执行建议）。
3. report 模式不阻断时，``revision_guidance`` 可能为空（report 模式仅落库不抛错，
   但 ``error_issues`` 为空 ⇒ guidance 为空列表；这是预期行为，不是测试失败）。
4. 端点契约：
   - ``GET /api/chapters/{cid}/quality`` 返回的 ``scores_json._meta.reference_consumption``
     与 ``runs.checkpoint_json['quality_gate'].reference_consumption`` 字段一致。
5. ``POST /api/chapters/{cid}/quality/evaluate`` 也带 ``_meta.reference_consumption``。

测试模式：与 ``tests/api/test_quality.py`` 一致（httpx.ASGITransport + tmp_path），
但归类到 ``tests/integration/``（任务书明示"固化"路径）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations


# -------- 复用 tests/api/test_quality.py 的 fixture / script 模式（独立副本，避免耦合）


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str = "v1.4 project") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "v1.4 chapter"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps({
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": chapter_id,
            "chapter_goal": "演练 V1.4 quality gate",
            "core_conflict": "无",
            "turning_point": "无",
            "expected_role": "setup",
            "key_beats": [{"beat_id": "beat_001", "purpose": "测试", "involved_characters": [],
                          "involved_locations": [], "involved_hooks": [], "involved_debts": [],
                          "risk_level": "LOW", "narrative_question_served": "测试"}],
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "",
        }, ensure_ascii=False)
    ]


def _writer_script(chapter_id: str) -> list[str]:
    prose = "无边的林海托着第一缕晨曦，远处的溪声把夜雾推向山脚，露珠沿着叶柄滑落。"
    return [
        json.dumps({
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": chapter_id,
            "prose": prose,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(prose),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }, ensure_ascii=False)
    ]


def _observer_dead_character_script(chapter_id: str, dead_char_id: str) -> list[str]:
    return [
        json.dumps({
            "character_changes": [
                {
                    "change_id": "cc_dead_1",
                    "op": "update",
                    "target_id": dead_char_id,
                    "character_id": dead_char_id,
                    "facet": "state",
                    "field": "state.location",
                    "before": {},
                    "after": {"location": "京城"},
                    "confidence": 0.9,
                    "evidence": {"chapter_id": chapter_id, "scene_id": "scene_001",
                                 "excerpt": "在京城出现", "span": {"start": 0, "end": 5}},
                    "risk_level": "LOW",
                    "notes": "尝试 set dead character activity",
                    "visibility": "VISIBLE",
                    "who_knows": None,
                    "reason": None,
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }, ensure_ascii=False)
    ]


async def _drive_plan_write_review(app, pid: str, cid: str, mock_providers: dict) -> None:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "v1.4 test", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "COMPLETED", r.json()

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "COMPLETED", r.json()

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    review_run = r.json()
    assert review_run["status"] == "PAUSED", review_run
    r = await _request(
        app, "POST", f"/api/runs/{review_run['run_id']}/resume",
        json={"human_input": {"approved": True}},
    )
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text


# -------- 测试用例


def _seed_reference_files(tmp_path: Path, project_id: str, files: dict[str, str]) -> None:
    """在 ``<db 父目录>/references/<project_id>/`` 写入若干 .txt 模拟爆款参照系。"""
    refs_dir = tmp_path / "references" / project_id
    refs_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (refs_dir / name).write_text(content, encoding="utf-8")


def test_v1_4_reference_consumption_persists_to_checkpoint_and_meta(tmp_path: Path):
    """happy path：项目下放 2 份 .txt → quality_gate 节点的 reference_consumption 列名 + 字符数正确。

    覆盖：
    - 节点返回 dict 的 ``reference_consumption.files`` 列出文件名 + chars。
    - ``runs.checkpoint_json['quality_gate'].reference_consumption`` 与节点返回一致。
    - ``/api/chapters/{cid}/quality`` 的 ``scores_json._meta.reference_consumption`` 也带上。
    - 走 report 模式避免阻断 commit，便于拿到节点返回 dict。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            # 提前写入项目级参照书（必须在 quality_gate 评估之前；report 模式不阻断，
            # 节点正常返回 dict 让前端能读到 reference_consumption）。
            _seed_reference_files(
                tmp_path, pid,
                {
                    "ref_a.txt": "爆款参照 A 的样本文字" * 50,  # 11 * 50 = 550 字
                    "ref_b.txt": "B 的风格示意",                # 7 字
                },
            )
            cid = await _make_chapter(app, pid)

            # 建一个普通角色（不触发 dead）——走 report 模式让 commit 通过
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "正常角色", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            normal_char = r.json()["character_id"]

            await _drive_plan_write_review(
                app, pid, cid,
                {
                    "director": _director_script(cid),
                    "writer": _writer_script(cid),
                    "observer": _observer_dead_character_script(cid, normal_char),
                },
            )
            # report 模式：quality_gate 落库但 commit 不被阻断
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={
                    "mock_providers": {"observer": _observer_dead_character_script(cid, normal_char)},
                    "quality_gate_mode": "report",
                },
            )
            assert r.status_code == 201, r.text
            commit_body = r.json()
            assert commit_body["status"] == "COMPLETED", commit_body

            # 拿 commit run 的 run_id
            commit_run_id = commit_body["run_id"]
            r = await _request(app, "GET", f"/api/runs/{commit_run_id}")
            assert r.status_code == 200, r.text
            run_obj = r.json()
            ckpt = run_obj.get("checkpoint_json") or {}
            qg = ckpt.get("quality_gate") if isinstance(ckpt, dict) else None
            assert qg is not None, f"quality_gate missing in checkpoint_json: {ckpt}"
            ref = qg.get("reference_consumption") if isinstance(qg, dict) else None
            assert ref is not None, f"reference_consumption missing: {qg}"
            assert ref["source"] == "project_refs_dir"
            assert ref["files_count"] == 2
            files = {f["name"]: f["chars"] for f in ref["files"]}
            assert "ref_a.txt" in files, files
            assert "ref_b.txt" in files, files
            assert files["ref_a.txt"] == len("爆款参照 A 的样本文字" * 50)
            assert files["ref_b.txt"] == len("B 的风格示意")
            assert ref["total_chars"] == files["ref_a.txt"] + files["ref_b.txt"]

            # 端点契约：scores_json._meta.reference_consumption 与 checkpoint 一致
            r = await _request(app, "GET", f"/api/chapters/{cid}/quality")
            assert r.status_code == 200, r.text
            meta = (r.json().get("scores_json") or {}).get("_meta") or {}
            meta_ref = meta.get("reference_consumption")
            assert meta_ref is not None, f"_meta.reference_consumption missing: {meta}"
            # 端点也走同样的 capture_reference_consumption；字段完全等价
            assert meta_ref["files_count"] == 2
            assert meta_ref["total_chars"] == ref["total_chars"]

    asyncio.run(run())


def test_v1_4_reference_consumption_empty_when_no_refs_dir(tmp_path: Path):
    """无参照目录时，reference_consumption.files 为空、files_count=0，节点仍正常返回。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            # 故意不写 references 目录
            cid = await _make_chapter(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "正常角色", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            normal_char = r.json()["character_id"]

            await _drive_plan_write_review(
                app, pid, cid,
                {
                    "director": _director_script(cid),
                    "writer": _writer_script(cid),
                    "observer": _observer_dead_character_script(cid, normal_char),
                },
            )
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={
                    "mock_providers": {"observer": _observer_dead_character_script(cid, normal_char)},
                    "quality_gate_mode": "report",
                },
            )
            assert r.status_code == 201, r.text
            run_id = r.json()["run_id"]
            run_obj = (await _request(app, "GET", f"/api/runs/{run_id}")).json()
            ckpt = run_obj.get("checkpoint_json") or {}
            qg = ckpt.get("quality_gate") if isinstance(ckpt, dict) else None
            assert qg is not None
            ref = qg["reference_consumption"]
            assert ref["files"] == []
            assert ref["files_count"] == 0
            assert ref["total_chars"] == 0

    asyncio.run(run())


def test_v1_4_enforce_revision_guidance_in_error_and_checkpoint(tmp_path: Path):
    """enforce 阻断时：runs.error 含 '| guidance=<json>' 段；checkpoint 含 revision_guidance。

    阻断场景：dead character delta 触发 character_contradiction error。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "死角色", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            dead_char_id = r.json()["character_id"]
            conn = app.state.settings.db_path and __import__("sqlite3").connect(app.state.settings.db_path)
            try:
                conn.execute(
                    "UPDATE character_states SET state_json = ? WHERE character_id = ?",
                    ('{"status":"dead"}', dead_char_id),
                )
                conn.commit()
            finally:
                conn.close()

            await _drive_plan_write_review(
                app, pid, cid,
                {
                    "director": _director_script(cid),
                    "writer": _writer_script(cid),
                },
            )
            # commit 用触发 dead 的 observer
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={
                    "mock_providers": {"observer": _observer_dead_character_script(cid, dead_char_id)},
                    "quality_gate_mode": "enforce",
                },
            )
            assert r.status_code == 201, r.text
            commit_body = r.json()
            assert commit_body["status"] == "FAILED", commit_body

            run_id = commit_body["run_id"]
            run_obj = (await _request(app, "GET", f"/api/runs/{run_id}")).json()
            assert run_obj["status"] == "FAILED"

            # 1) runs.error 含 "| guidance=<json>"
            err = run_obj.get("error") or ""
            assert "quality gate blocked" in err, err
            assert "| guidance=" in err, f"error missing guidance segment: {err}"
            guidance_json = err.split("| guidance=", 1)[1]
            guidance = json.loads(guidance_json)
            assert isinstance(guidance, list)
            assert len(guidance) >= 1, f"guidance empty: {guidance}"
            for entry in guidance:
                assert set(entry.keys()) >= {
                    "dimension", "score", "threshold", "top_issues", "rule_hint",
                }, entry
                # guardrails 维度 score=0（不是低分子分），其它低分子分 score<threshold
                if entry["dimension"] != "guardrails":
                    assert entry["score"] < entry["threshold"], entry
                assert entry["rule_hint"], entry
                assert isinstance(entry["top_issues"], list)
            # enforce 阻断时 guardrails 维度必现（保证作者必拿得到可执行建议）
            dims = [g["dimension"] for g in guidance]
            assert "guardrails" in dims, f"guardrails missing: {guidance}"

            # 2) checkpoint 也含 revision_guidance（前端无需解析 error 字符串）
            ckpt = run_obj.get("checkpoint_json") or {}
            qg = ckpt.get("quality_gate") if isinstance(ckpt, dict) else None
            assert qg is not None, f"quality_gate missing: {ckpt}"
            assert qg.get("blocked") is True
            assert qg.get("mode") == "enforce"
            ck_guidance = qg.get("revision_guidance")
            assert isinstance(ck_guidance, list)
            assert len(ck_guidance) >= 1
            # 与 error 段解析结果一致
            assert [g["dimension"] for g in ck_guidance] == [g["dimension"] for g in guidance]
            # reference_consumption 仍落 checkpoint（即便阻断）
            assert "reference_consumption" in qg
            assert qg["reference_consumption"]["files_count"] >= 0

    asyncio.run(run())


def test_v1_4_evaluate_endpoint_also_emits_reference_consumption(tmp_path: Path):
    """POST /api/chapters/{cid}/quality/evaluate 端点也带 _meta.reference_consumption。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            _seed_reference_files(tmp_path, pid, {"x.txt": "参照文字"})
            cid = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/chapters/{cid}/quality/evaluate")
            assert r.status_code == 201, r.text
            body = r.json()
            meta = (body.get("scores_json") or {}).get("_meta") or {}
            ref = meta.get("reference_consumption")
            assert ref is not None, f"_meta.reference_consumption missing: {meta}"
            assert ref["files_count"] == 1
            assert ref["files"][0]["name"] == "x.txt"

    asyncio.run(run())