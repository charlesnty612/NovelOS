"""P1 规划合并端到端（引擎级）：chapter-plan 单 AI 节点 + scene_plan 同 run 落库 +
chapter-write 读落库 scene_plan（命中跳过 / 未命中保留降级）。

覆盖（对应 `_refs/p1_merged_prompt_v2.md`「上线前剩余事项」1 / 2 / 4 / 5）：

1. chapter-plan 合并输出（plan + scene_plan）→ plan_json 与 chapter_scene_plans 同 run 落库；
2. 计划-only 输出（无 scene_plan）→ 计划照常落库、不写 scene 行、node 标
   ``missing_in_output``（降级不阻断）；
3. 重规划语义：旧 scene 行被覆盖；本轮无 scene 时旧行被删除（陈旧场景不得配新计划）；
4. 白名单命中 → 既有 output-invalid 重试：二次合规则 run COMPLETED（重试计数落
   ai_call_logs）；两次都命中则 run FAILED（不静默放行）；
5. chapter-write：命中落库 scene_plan → 跳过自身 scene_planner 调用
   （``scene_planner_status='from_plan'``、ai_call_logs 无 scene_planner:v1 行）；
6. chapter-write：未命中 → 走原 scene_planner 单节点调用（降级路径必须保留）。

设计：引擎级直连 tmp 库（不依赖 HTTP 轮询），章节计划由本用例的 mock 产出
（即真实跑 chapter-plan 工作流，不直插 plan_json）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "p1.db"
    apply_migrations(path)
    PromptRegistry(path).sync_from_docs(PROMPTS_DIR)
    return path


@pytest.fixture
def engine(db_path: Path) -> WorkflowEngine:
    return WorkflowEngine(db_path)


def _make_project(db_path: Path, name: str = "P1 项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _make_character(db_path: Path, pid: str, name: str = "林渊") -> str:
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, "
            "created_at, updated_at) VALUES (?, ?, ?, 'protagonist', ?, ?)",
            (cid, pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _make_location(db_path: Path, pid: str, name: str = "玉惜轩") -> str:
    lid = new_id("loc")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO locations (location_id, project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lid, pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return lid


def _make_chapter(db_path: Path, pid: str, number: int = 1, title: str = "第一章") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, '{}', 'PLANNED', ?, ?)",
            (cid, pid, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _scene(chapter_id: str, idx: int, character_id: str | None, location_id: str | None,
           target_words: int) -> dict:
    return {
        "scene_id": f"scene_{idx:03d}",
        "purpose": f"第 {idx} 场叙事功能",
        "characters": [character_id] if character_id else [],
        "location": location_id,
        "conflict": "追问 vs 回避",
        "turn": "信任出现裂痕",
        "time_in_story": "夜谈当夜",
        "pov": "third_person_limited",
        "pov_character_id": character_id,
        "information_boundary": ["黑玉佩真正来历"],
        "ending_hook": "决定暗中调查",
        "target_words": target_words,
        "slots": [
            {
                "slot_id": f"scene_{idx:03d}_dialogue_01",
                "type": "dialogue",
                "purpose": "引出父亲遗物话题",
                "characters": [character_id] if character_id else [],
                "target_mood": "克制中的试探",
                "constraints": [],
            }
        ],
    }


def _merged_payload(
    chapter_id: str,
    *,
    character_id: str | None = None,
    location_id: str | None = None,
    hook_handling: list[dict] | None = None,
    scenes: int = 2,
    chapter_goal: str = "女主第一次怀疑男主隐瞒父亲死因",
) -> dict:
    """合规合并输出（plan + scene_plan 双契约）。"""
    per_scene = 3000 // scenes
    return {
        "schema_version": "director-plan.v1",
        "prompt_version": "director_planner:v2",
        "chapter_id": chapter_id,
        "chapter_goal": chapter_goal,
        "core_conflict": "求真 vs 善意隐瞒",
        "turning_point": "男主回避细节被察觉",
        "expected_role": "escalation",
        "expected_word_count": 3000,
        "key_beats": [
            {
                "beat_id": f"beat_{i:03d}",
                "purpose": f"第 {i} 拍",
                "involved_characters": [character_id] if character_id else [],
                "involved_locations": [location_id] if location_id else [],
                "involved_hooks": [],
                "involved_debts": [],
                "risk_level": "LOW",
                "narrative_question_served": "伏笔推进",
            }
            for i in range(1, scenes + 1)
        ],
        "character_changes_planned": [],
        "information_releases": [],
        "hook_handling": hook_handling or [],
        "debt_handling": [],
        "proposed_new_entities": [],
        "deviations": [],
        "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        "open_questions": [],
        "notes_for_planner": "按 3000 字分摊",
        "scene_plan": {
            "schema_version": "scene-plan.v1",
            "prompt_version": "director_planner:v2",
            "chapter_id": chapter_id,
            "scenes": [
                _scene(chapter_id, i, character_id, location_id, per_scene)
                for i in range(1, scenes + 1)
            ],
            "notes_for_writer": "对话占比不低于 35%",
            "deviations": [],
        },
    }


def _plan_only_payload(chapter_id: str, **kwargs) -> dict:
    payload = _merged_payload(chapter_id, **kwargs)
    payload.pop("scene_plan")
    return payload


def _writer_output(chapter_id: str) -> str:
    prose = (
        "戌时的更鼓从街尾传过来。玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
        "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。"
    )
    return json.dumps(
        {
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": chapter_id,
            "prose": prose,
            "self_report": {
                "slots_filled": ["scene_001_dialogue_01", "scene_002_dialogue_01"],
                "word_count": len(prose),
                "scene_count": 2,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        },
        ensure_ascii=False,
    )


def _run_plan(engine: WorkflowEngine, db_path: Path, pid: str, cid: str, script: list[str]) -> str:
    from packages.workflows import get_workflow

    wf = get_workflow("chapter-plan")
    assert wf is not None
    return engine.start_with_nodes(
        "chapter-plan",
        wf["nodes"],
        chapter_id=cid,
        initial_ctx={"db_path": str(db_path), "project_id": pid, "chapter_id": cid},
        mock_providers={"director_planner": script},
        checkpoint_exclude=wf.get("checkpoint_exclude"),
    )


def _run_write(
    engine: WorkflowEngine, db_path: Path, pid: str, cid: str, mock_providers: dict
) -> str:
    from packages.workflows.chapter_write.pipeline import WORKFLOW as WRITE_WORKFLOW

    return engine.start_with_nodes(
        "chapter-write",
        WRITE_WORKFLOW["nodes"],
        chapter_id=cid,
        initial_ctx={"db_path": str(db_path), "project_id": pid, "chapter_id": cid},
        mock_providers=mock_providers,
        checkpoint_exclude=WRITE_WORKFLOW.get("checkpoint_exclude"),
    )


def _node_outputs(db_path: Path, run_id: str) -> dict[str, dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT node_id, output_json FROM workflow_run_nodes WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        r["node_id"]: json.loads(r["output_json"]) for r in rows if r["output_json"]
    }


def _scene_plan_row(db_path: Path, cid: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM chapter_scene_plans WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _plan_json(db_path: Path, cid: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["plan_json"]) if row and row["plan_json"] else {}


def _ai_call_count(db_path: Path, prompt_version: str) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_call_logs WHERE prompt_version = ?",
            (prompt_version,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


# ---------------------------------------------------------------------------
# 1/2/3. chapter-plan：合并输出落库 + 降级 + 覆盖/失效
# ---------------------------------------------------------------------------


def test_plan_merged_output_persists_plan_and_scene_plan(
    engine: WorkflowEngine, db_path: Path
):
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    loc_id = _make_location(db_path, pid)
    cid = _make_chapter(db_path, pid)
    payload = _merged_payload(cid, character_id=char_id, location_id=loc_id)

    run_id = _run_plan(engine, db_path, pid, cid, [json.dumps(payload, ensure_ascii=False)])
    run = get_run(db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    # plan_json：导演段落库（scene_plan 不进 plan_json）
    plan = _plan_json(db_path, cid)
    assert plan["chapter_goal"] == payload["chapter_goal"]
    assert plan["expected_word_count"] == 3000
    assert len(plan["key_beats"]) == 2
    assert "scene_plan" not in plan

    # chapter_scene_plans：同 run 落库 + 溯源字段
    row = _scene_plan_row(db_path, cid)
    assert row is not None, "scene_plan 未落库"
    assert row["run_id"] == run_id
    assert row["source"] == "director_planner"
    assert row["prompt_version"] == "director_planner:v2"
    assert row["scene_count"] == 2
    stored = json.loads(row["payload_json"])
    assert stored == payload["scene_plan"]

    # 节点产出：scene_plan_status 可观测
    outputs = _node_outputs(db_path, run_id)
    assert outputs["director_planner"]["scene_plan_status"] == "ok"
    assert outputs["save_plan"]["scene_plan_saved"] is True


def test_plan_plan_only_output_keeps_plan_without_scene_row(
    engine: WorkflowEngine, db_path: Path
):
    """计划-only 输出（prompt 违规但计划可用）→ 计划落库、scene 不落库、状态可观测。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)
    payload = _plan_only_payload(cid, character_id=char_id)

    run_id = _run_plan(engine, db_path, pid, cid, [json.dumps(payload, ensure_ascii=False)])
    run = get_run(db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run
    assert _plan_json(db_path, cid)["chapter_goal"] == payload["chapter_goal"]
    assert _scene_plan_row(db_path, cid) is None

    outputs = _node_outputs(db_path, run_id)
    assert outputs["director_planner"]["scene_plan_status"] == "missing_in_output"
    assert outputs["save_plan"]["scene_plan_saved"] is False


def test_replan_overwrites_scene_plan_row(engine: WorkflowEngine, db_path: Path):
    """重规划：同 chapter 只保留最新一行的 scene_plan（1 章 1 面，不累积历史）。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)

    first = _merged_payload(cid, character_id=char_id, scenes=2, chapter_goal="第一版目标")
    _run_plan(engine, db_path, pid, cid, [json.dumps(first, ensure_ascii=False)])
    row1 = _scene_plan_row(db_path, cid)
    assert row1 is not None and row1["scene_count"] == 2

    second = _merged_payload(cid, character_id=char_id, scenes=3, chapter_goal="第二版目标")
    _run_plan(engine, db_path, pid, cid, [json.dumps(second, ensure_ascii=False)])
    row2 = _scene_plan_row(db_path, cid)
    assert row2 is not None
    assert row2["scene_plan_id"] == row1["scene_plan_id"], "应 UPSERT 同一行"
    assert row2["scene_count"] == 3
    assert row2["created_at"] == row1["created_at"]
    assert _plan_json(db_path, cid)["chapter_goal"] == "第二版目标"


def test_replan_without_scene_plan_invalidates_stale_row(
    engine: WorkflowEngine, db_path: Path
):
    """本轮合并调用没产出 scene_plan → 旧 scene 行必须删除（陈旧场景不得配新计划）。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)

    merged = _merged_payload(cid, character_id=char_id)
    _run_plan(engine, db_path, pid, cid, [json.dumps(merged, ensure_ascii=False)])
    assert _scene_plan_row(db_path, cid) is not None

    plan_only = _plan_only_payload(cid, character_id=char_id, chapter_goal="改版目标")
    run_id = _run_plan(engine, db_path, pid, cid, [json.dumps(plan_only, ensure_ascii=False)])
    assert get_run(db_path, run_id)["status"] == "COMPLETED"
    assert _scene_plan_row(db_path, cid) is None, "旧 scene_plan 行必须失效"
    assert _plan_json(db_path, cid)["chapter_goal"] == "改版目标"


# ---------------------------------------------------------------------------
# 4. 白名单 → 既有 output-invalid 重试
# ---------------------------------------------------------------------------


def test_plan_whitelist_violation_retries_then_succeeds(
    engine: WorkflowEngine, db_path: Path
):
    """第一次输出自造 hook_id（输入两表为空）→ 重试；第二次合规 → run COMPLETED。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)

    bad = _merged_payload(
        cid,
        character_id=char_id,
        hook_handling=[
            {"hook_id": "hook_hallucinated", "action": "introduce", "rationale": "自造"}
        ],
    )
    good = _merged_payload(cid, character_id=char_id)
    run_id = _run_plan(
        engine,
        db_path,
        pid,
        cid,
        [json.dumps(bad, ensure_ascii=False), json.dumps(good, ensure_ascii=False)],
    )
    run = get_run(db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT retry_count, error FROM ai_call_logs "
            "WHERE prompt_version = 'director_planner:v2'"
        ).fetchone()
    finally:
        conn.close()
    assert row["retry_count"] == 1, "白名单命中应走既有 output-invalid 重试"
    assert "whitelist" in (row["error"] or "")
    assert _plan_json(db_path, cid)["chapter_goal"] == good["chapter_goal"]
    assert _scene_plan_row(db_path, cid) is not None


def test_plan_whitelist_violation_twice_fails_run(engine: WorkflowEngine, db_path: Path):
    """两次都命中白名单 → run FAILED（不静默放行、不写计划）。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)

    bad = _merged_payload(
        cid,
        character_id=char_id,
        hook_handling=[
            {"hook_id": "hook_hallucinated", "action": "introduce", "rationale": "自造"}
        ],
    )
    raw = json.dumps(bad, ensure_ascii=False)
    run_id = _run_plan(engine, db_path, pid, cid, [raw, raw])
    run = get_run(db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert run["current_node"] == "director_planner"
    assert _plan_json(db_path, cid) == {}
    assert _scene_plan_row(db_path, cid) is None


# ---------------------------------------------------------------------------
# 5/6. chapter-write：读落库 scene_plan（命中跳过 / 未命中降级）
# ---------------------------------------------------------------------------


def test_write_uses_persisted_scene_plan_and_skips_llm(engine: WorkflowEngine, db_path: Path):
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    loc_id = _make_location(db_path, pid)
    cid = _make_chapter(db_path, pid)
    merged = _merged_payload(cid, character_id=char_id, location_id=loc_id)
    _run_plan(engine, db_path, pid, cid, [json.dumps(merged, ensure_ascii=False)])

    write_run_id = _run_write(
        engine, db_path, pid, cid, {"writer": [_writer_output(cid)]}
    )
    write_run = get_run(db_path, write_run_id)
    assert write_run is not None and write_run["status"] == "COMPLETED", write_run

    outputs = _node_outputs(db_path, write_run_id)
    scene_node = outputs["scene_planner"]
    assert scene_node["scene_planner_status"] == "from_plan"
    assert scene_node["scene_planner_output"] is None
    assert [s["scene_id"] for s in scene_node["scene_plan"]["scenes"]] == [
        "scene_001",
        "scene_002",
    ]
    assert scene_node["scene_planner_source"]["source"] == "director_planner"

    assert _ai_call_count(db_path, "scene_planner:v1") == 0, "命中落库场景不应再调 scene_planner"
    assert _ai_call_count(db_path, "writer:v1") == 1

    conn = get_connection(db_path)
    try:
        draft = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert draft is not None and "玉惜轩" in draft["content"]


def test_write_falls_back_to_scene_planner_when_no_persisted_plan(
    engine: WorkflowEngine, db_path: Path
):
    """未命中（计划-only / 老章）→ 保留 scene_planner 单节点调用（降级路径必须保留）。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)
    plan_only = _plan_only_payload(cid, character_id=char_id)
    _run_plan(engine, db_path, pid, cid, [json.dumps(plan_only, ensure_ascii=False)])
    assert _scene_plan_row(db_path, cid) is None

    scene_planner_script = json.dumps(
        {
            "schema_version": "scene-plan.v1",
            "prompt_version": "scene_planner:v1",
            "chapter_id": cid,
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "purpose": "降级路径规划出的场景",
                    "characters": [char_id],
                    "location": None,
                    "conflict": "追问 vs 回避",
                    "turn": None,
                    "time_in_story": "夜谈当夜",
                    "pov": "third_person_limited",
                    "pov_character_id": char_id,
                    "information_boundary": [],
                    "ending_hook": None,
                    "slots": [
                        {
                            "slot_id": "scene_001_dialogue_01",
                            "type": "dialogue",
                            "purpose": "试探",
                            "characters": [char_id],
                            "target_mood": None,
                            "constraints": [],
                        }
                    ],
                }
            ],
            "notes_for_writer": "",
            "deviations": [],
        },
        ensure_ascii=False,
    )
    write_run_id = _run_write(
        engine,
        db_path,
        pid,
        cid,
        {
            "scene_planner": [scene_planner_script],
            "writer": [_writer_output(cid)],
        },
    )
    write_run = get_run(db_path, write_run_id)
    assert write_run is not None and write_run["status"] == "COMPLETED", write_run

    outputs = _node_outputs(db_path, write_run_id)
    assert outputs["scene_planner"]["scene_planner_status"] == "ok"
    assert outputs["scene_planner"]["scene_plan"]["scenes"][0]["purpose"] == "降级路径规划出的场景"
    assert _ai_call_count(db_path, "scene_planner:v1") == 1


def test_write_ignores_persisted_row_with_empty_scenes(engine: WorkflowEngine, db_path: Path):
    """落库行 scenes 为空（手改 / 旧坏数据）→ 读侧防御：回原 scene_planner 路径。"""
    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)
    merged = _merged_payload(cid, character_id=char_id)
    _run_plan(engine, db_path, pid, cid, [json.dumps(merged, ensure_ascii=False)])

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapter_scene_plans SET payload_json = ? WHERE chapter_id = ?",
            (json.dumps({"schema_version": "scene-plan.v1", "scenes": []}), cid),
        )
        conn.commit()
    finally:
        conn.close()

    # 不配 scene_planner mock → 走「无 model config」失败 → 机械映射降级（既有路径）
    write_run_id = _run_write(engine, db_path, pid, cid, {"writer": [_writer_output(cid)]})
    write_run = get_run(db_path, write_run_id)
    assert write_run is not None and write_run["status"] == "COMPLETED", write_run
    outputs = _node_outputs(db_path, write_run_id)
    assert outputs["scene_planner"]["scene_planner_status"] == "failed"
    # 机械映射：director.key_beats → 每 beat 一个 scene
    assert len(outputs["scene_planner"]["scene_plan"]["scenes"]) == 2


def test_director_planner_retry_hint_carries_merged_constraints(
    engine: WorkflowEngine, db_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """重试提示携带合并调用专属约束（整段单对象 + 空输入 white 列表）——可修复性看守。

    runner 的通用提示只说「请只输出合法 JSON」；director_planner 两次实测的失败形态集中在
    「输出两个相邻顶层对象」与「空输入仍编造 hook/debt」，故 ``_RETRY_HINT_EXTRA`` 随重试
    提示一并下发。本测试捕获第二次调用的 user message 断言该文案在位。
    """
    from packages.core.agent_runtime import runner as runner_mod
    from packages.core.model_router.providers import MockProvider

    captured: list[str] = []

    class _SpyMockProvider(MockProvider):
        def complete(self, messages, params=None):
            captured.append(messages[1]["content"])
            return super().complete(messages, params)

    monkeypatch.setattr(runner_mod, "MockProvider", _SpyMockProvider)

    pid = _make_project(db_path)
    char_id = _make_character(db_path, pid)
    cid = _make_chapter(db_path, pid)
    bad = _merged_payload(
        cid,
        character_id=char_id,
        hook_handling=[
            {"hook_id": "hook_hallucinated", "action": "introduce", "rationale": "自造"}
        ],
    )
    good = _merged_payload(cid, character_id=char_id)
    run_id = _run_plan(
        engine,
        db_path,
        pid,
        cid,
        [json.dumps(bad, ensure_ascii=False), json.dumps(good, ensure_ascii=False)],
    )
    assert get_run(db_path, run_id)["status"] == "COMPLETED"
    assert len(captured) == 2, "应发生一次重试"
    retry_message = captured[1]
    assert "[System note]" in retry_message
    assert "必须为 []" in retry_message
    assert "顶层 JSON 对象" in retry_message


# ---------------------------------------------------------------------------
# 7. API 层全链（ASGI）：计划合并 → 写作消费落库场景（不依赖端口/外部服务）
# ---------------------------------------------------------------------------


def test_api_full_chain_with_merged_plan(tmp_path: Path):
    """经 HTTP 端点跑 plan → write → review(approve) → commit，断言合并链路的两个新钩子：

    - plan 后 ``chapter_scene_plans`` 已落库（同 run）；
    - write 的 ``scene_planner`` 节点标 ``from_plan``（消费落库场景，不调 LLM）。

    其余断言与 ``test_smoke_full_chain`` 同口径（终态 COMMITTED）。
    """
    import asyncio

    import httpx

    from packages.core.api.main import create_app
    from packages.core.config import Settings

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)
    db_path = settings.db_path
    docs_dir = PROMPTS_DIR.as_posix()

    async def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def _request(method: str, path: str, **kwargs) -> httpx.Response:
        async with await _client() as client:
            return await client.request(method, path, **kwargs)

    async def _wait_terminal(run_id: str, expected=("COMPLETED", "PAUSED", "FAILED")) -> dict:
        import time

        deadline = time.monotonic() + 60.0
        last: dict | None = None
        while time.monotonic() < deadline:
            r = await _request("GET", f"/api/runs/{run_id}")
            last = r.json()
            if last["status"] in expected:
                return last
            await asyncio.sleep(0.2)
        raise AssertionError(f"run {run_id} 未落终态，最后状态={last}")

    async def run() -> None:
        async with app.router.lifespan_context(app):
            r = await _request("POST", f"/api/agents/sync?docs_dir={docs_dir}")
            assert r.status_code == 200, r.text

            r = await _request("POST", "/api/projects", json={"name": "P1 API 链"})
            pid = r.json()["project_id"]
            r = await _request(
                "POST", f"/api/projects/{pid}/characters",
                json={"name": "林渊", "role": "protagonist"},
            )
            char_id = r.json()["character_id"]
            r = await _request(
                "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
            )
            cid = r.json()["chapter_id"]

            merged = _merged_payload(cid, character_id=char_id)
            mocks = {
                "director_planner": [json.dumps(merged, ensure_ascii=False)],
                "writer": [_writer_output(cid)],
                "observer": [
                    json.dumps(
                        {
                            "character_changes": [],
                            "world_changes": [],
                            "relationship_changes": [],
                            "new_events": [],
                            "resolved_hooks": [],
                            "new_hooks": [],
                            "debt_changes": [],
                        },
                        ensure_ascii=False,
                    )
                ],
            }

            # 1. plan（合并调用）
            r = await _request(
                "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
                json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mocks},
            )
            assert r.status_code == 201, r.text
            plan_run = await _wait_terminal(r.json()["run_id"], expected=("COMPLETED",))
            assert plan_run["status"] == "COMPLETED"
            assert _scene_plan_row(db_path, cid) is not None

            # 2. write（消费落库 scene_plan）
            r = await _request(
                "POST", f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mocks},
            )
            assert r.status_code == 201, r.text
            write_run_id = r.json()["run_id"]
            write_run = await _wait_terminal(write_run_id, expected=("COMPLETED",))
            assert write_run["status"] == "COMPLETED"
            outputs = _node_outputs(db_path, write_run_id)
            assert outputs["scene_planner"]["scene_planner_status"] == "from_plan"
            assert _ai_call_count(db_path, "scene_planner:v1") == 0

            # 3. review（人工节点 approved）
            r = await _request(
                "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": mocks},
            )
            assert r.status_code == 201, r.text
            review_run_id = r.json()["run_id"]
            await _wait_terminal(review_run_id, expected=("PAUSED", "COMPLETED"))
            r = await _request(
                "POST", f"/api/runs/{review_run_id}/resume",
                json={"human_input": {"approved": True}},
            )
            assert r.status_code == 200, r.text
            assert (await _wait_terminal(review_run_id, expected=("COMPLETED",)))["status"] == "COMPLETED"

            # 4. commit
            r = await _request(
                "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mocks},
            )
            assert r.status_code == 201, r.text
            commit_run_id = r.json()["run_id"]
            await _wait_terminal(commit_run_id, expected=("COMPLETED", "PAUSED"))
            r = await _request("GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED", r.text

    asyncio.run(run())
