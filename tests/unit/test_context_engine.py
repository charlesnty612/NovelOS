"""Context Engine 单测（Sprint 11 下半）。

聚焦 ``build_director_input`` 的 Reference Canon 注入行为：

- 有 canon：注入 ``reference_canon`` 键，``spine`` 截前 20 条、``payoff_list`` 截前 30 条；
- 无 canon：不注入任何 canon 相关键（行为与改造前完全一致）；
- canon status='archived'：不注入；
- canon_json 字段缺失：容错跳过该字段，``consumed_fields`` 不计；
- 顶层 ``_reference_canon_consumed`` 写入溯源审计，可随 ctx 落 checkpoint_json。

设计要点：
- 直连 in-memory DB（apply_migrations + 手工插 reference_canons 行），不依赖 HTTP 层；
- ``build_director_input`` 需 project + chapter 行 + StoryStateService.get_current_state，
  一并手工 setup（与 workflow 集成测试对齐）；其余 excerpts 空表即合法。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.context_engine.builders import build_director_input
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_write.pipeline import _collect_scene_planner_inputs

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1, title: str = "第一章") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_canon(
    db_path: Path,
    project_id: str,
    *,
    canon_json: dict,
    title: str = "测试参照书",
    status: str = "active",
    created_at: str | None = None,
) -> str:
    canon_id = new_id("can")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons
                (canon_id, project_id, title, reader_profile, canon_json,
                 report_md, status, created_at)
            VALUES (?, ?, ?, 'male_fantasy', ?, '', ?, ?)
            """,
            (
                canon_id,
                project_id,
                title,
                json.dumps(canon_json, ensure_ascii=False),
                status,
                created_at or now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return canon_id


def _full_canon(spine_n: int = 5, payoff_n: int = 5) -> dict:
    return {
        "logline": "草根少年获逆袭金手指→碾压同辈→势力洗牌",
        "spine": [
            {
                "chapter_index": i,
                "title_pattern": f"模式 {i}",
                "function_tag": "hook",
                "summary_pattern": f"摘要 {i}",
            }
            for i in range(1, spine_n + 1)
        ],
        "faction_map": {"factions": [], "relations": [], "power_layers": []},
        "emotion_curve": [
            {"chapter_index": i, "valence": 0, "marker_type": "buildup"}
            for i in range(1, 4)
        ],
        "payoff_list": [
            {
                "payoff_id": f"payoff_{i:03d}",
                "chapter_index": i,
                "type": "face_slap",
                "intensity": 3,
                "setup_chapter": i,
                "payoff_chapter": i,
            }
            for i in range(1, payoff_n + 1)
        ],
        "techniques": [],
        "rhythm": {
            "mini_climax_interval": {"median": 3, "p25": 2, "p75": 5},
            "major_climax_interval": {"median": 5, "p25": 4, "p75": 7},
            "chapter_end_hook_rate": 0.85,
            "golden_three_compliance": {
                "first_300_chars_conflict": True,
                "ch1_end_hook": True,
                "ch2_end_hook": True,
                "ch3_end_hook": True,
                "mini_climax_in_first_three": True,
            },
        },
        "style_params": {
            "sentence_length_distribution": {"mean": 18.0, "median": 16.0, "max": 80},
            "dialogue_ratio": 0.25,
            "action_ratio": 0.45,
            "pov": "third_limited",
            "paragraph_length_distribution": {"mean": 120.0, "median": 100.0, "max": 600},
            "psychological_ratio": 0.15,
            "environment_ratio": 0.15,
        },
        "metadata": {"source_book_title": "test"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_canon_does_not_inject_any_canon_key(tmp_path: Path):
    """项目无 canon → director_input 不加 reference_canon / _reference_canon_consumed 键。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    out = build_director_input(db_path, pid, cid, "意图")

    assert "reference_canon" not in out, "无 canon 时不应注入 reference_canon"
    assert "_reference_canon_consumed" not in out, "无 canon 时不应注入溯源键"


def test_active_canon_is_injected_with_caps(tmp_path: Path):
    """active canon 注入：spine 截前 20 条、payoff_list 截前 30 条、rhythm 透传。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_canon(
        db_path,
        pid,
        canon_json=_full_canon(spine_n=25, payoff_n=40),
    )

    out = build_director_input(db_path, pid, cid, "意图")

    canon = out["reference_canon"]
    assert isinstance(canon, dict)
    assert canon["logline"].startswith("草根少年")
    assert len(canon["spine"]) == 20, f"spine 截断错误：{len(canon['spine'])}"
    assert len(canon["payoff_list"]) == 30, f"payoff_list 截断错误：{len(canon['payoff_list'])}"
    assert canon["rhythm"]["chapter_end_hook_rate"] == 0.85
    # canon_id 注入且格式合法
    assert canon["canon_id"].startswith("can_")

    audit = out["_reference_canon_consumed"]
    assert audit["canon_id"] == canon["canon_id"]
    # consumed_fields 只列出实际注入的 4 个字段
    assert set(audit["consumed_fields"]) == {"logline", "spine", "payoff_list", "rhythm"}


def test_active_canon_injects_protagonist_when_present(tmp_path: Path):
    """canon 含 protagonist → director 注入该块；personality_tags ≤6、foil_techniques ≤4。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    canon = _full_canon(spine_n=3, payoff_n=3)
    canon["protagonist"] = {
        "identity": "草根逆袭型主角",
        "personality_tags": ["隐忍", "重情", "好强", "克制", "机敏", "守信"],
        "core_drive": "打破同辈压制登上巅峰，证明出身不决定上限",
        # 故意放 6 项，验证注入时截到 ≤4
        "foil_techniques": [
            "前期压制-中期对等-后期反压",
            "同辈对照镜映主角成长",
            "反派长辈的镜像对照",
            "导师托举与牺牲",
            "金手指获得仪式感",
            "宿敌命运缠绕",
        ],
    }
    _insert_canon(db_path, pid, canon_json=canon, title="人设参照")

    out = build_director_input(db_path, pid, cid, "意图")

    canon_inject = out["reference_canon"]
    assert "protagonist" in canon_inject, "director 应注入 protagonist 块"
    p = canon_inject["protagonist"]
    assert p["identity"] == "草根逆袭型主角"
    assert p["core_drive"].startswith("打破同辈压制")
    # 6 项 → 注入 6 项（≤6 不截）
    assert len(p["personality_tags"]) == 6
    # 6 项 → 注入 4 项（≤4 截断）
    assert len(p["foil_techniques"]) == 4
    assert p["foil_techniques"] == [
        "前期压制-中期对等-后期反压",
        "同辈对照镜映主角成长",
        "反派长辈的镜像对照",
        "导师托举与牺牲",
    ]

    audit = out["_reference_canon_consumed"]
    assert "protagonist" in audit["consumed_fields"]
    assert "logline" in audit["consumed_fields"]


def test_canon_without_protagonist_does_not_inject_it(tmp_path: Path):
    """canon 无 protagonist → director 不注入该键；consumed_fields 不计。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    # _full_canon() 不含 protagonist（存量 canon 形态）
    _insert_canon(db_path, pid, canon_json=_full_canon(), title="存量 canon")

    out = build_director_input(db_path, pid, cid, "意图")
    canon_inject = out["reference_canon"]
    assert "protagonist" not in canon_inject
    assert "protagonist" not in out["_reference_canon_consumed"]["consumed_fields"]
    # 其余字段照常
    assert "logline" in canon_inject
    assert "spine" in canon_inject


def test_protagonist_partial_fields_still_inject(tmp_path: Path):
    """protagonist 仅含部分子字段 → 注入时跳过缺失字段；至少 1 个有效子字段才注入键。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    canon = _full_canon(spine_n=2, payoff_n=2)
    # 仅 identity；其余全缺
    canon["protagonist"] = {"identity": "隐忍型主角"}
    _insert_canon(db_path, pid, canon_json=canon, title="部分人设")

    out = build_director_input(db_path, pid, cid, "意图")
    p = out["reference_canon"]["protagonist"]
    assert p == {"identity": "隐忍型主角"}
    assert "protagonist" in out["_reference_canon_consumed"]["consumed_fields"]


def test_archived_canon_is_not_injected(tmp_path: Path):
    """archived canon 不注入。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_canon(
        db_path,
        pid,
        canon_json=_full_canon(),
        status="archived",
    )

    out = build_director_input(db_path, pid, cid, "意图")
    assert "reference_canon" not in out
    assert "_reference_canon_consumed" not in out


def test_takes_latest_active_canon_when_multiple_exist(tmp_path: Path):
    """多条 active canon → 按 created_at DESC 取最新 1 条。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    old_canon = _full_canon(spine_n=3)
    old_canon["logline"] = "老 canon"
    new_canon = _full_canon(spine_n=3)
    new_canon["logline"] = "新 canon"

    _insert_canon(
        db_path,
        pid,
        canon_json=old_canon,
        title="老书",
        created_at="2026-08-20T00:00:00+00:00",
    )
    _insert_canon(
        db_path,
        pid,
        canon_json=new_canon,
        title="新书",
        created_at="2026-08-23T00:00:00+00:00",
    )

    out = build_director_input(db_path, pid, cid, "意图")
    assert out["reference_canon"]["logline"] == "新 canon"


def test_missing_fields_are_skipped_silently(tmp_path: Path):
    """canon_json 缺字段 → 跳过该字段，consumed_fields 不计。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    # 只保留 logline；缺 spine / payoff_list / rhythm
    _insert_canon(
        db_path,
        pid,
        canon_json={"logline": "仅 logline", "metadata": {"x": 1}},
    )

    out = build_director_input(db_path, pid, cid, "意图")
    canon = out["reference_canon"]
    assert canon["logline"] == "仅 logline"
    assert "spine" not in canon
    assert "payoff_list" not in canon
    assert "rhythm" not in canon
    assert "canon_id" in canon
    assert out["_reference_canon_consumed"]["consumed_fields"] == ["logline"]


def test_canon_json_garbage_does_not_raise(tmp_path: Path):
    """canon_json 解析失败 → 视为无 canon，不抛错。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons
                (canon_id, project_id, title, reader_profile, canon_json,
                 report_md, status, created_at)
            VALUES ('can_broken', ?, '坏', 'male_fantasy', '{not valid json}', '', 'active', ?)
            """,
            (pid, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    out = build_director_input(db_path, pid, cid, "意图")
    assert "reference_canon" not in out
    assert "_reference_canon_consumed" not in out


def test_chapter_plan_pipeline_writes_consumed_audit_to_checkpoint(tmp_path: Path):
    """端到端：跑 chapter-plan 后，checkpoint_json 必须包含 _reference_canon_consumed。

    这是 Sprint 11 下半 §6.3 溯源审计的最小闭环：director_input 顶层
    ``_reference_canon_consumed`` 由 ``build_director_input`` 写入；chapter_plan
    pipeline 把整个 ctx dict 交给 WorkflowEngine，引擎按 ``_dump_json(ctx)``
    写入 workflow_runs.checkpoint_json（见 engine.py:411）。
    """
    import asyncio
    import json

    from packages.core.workflow_runtime.engine import WorkflowEngine

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_canon(db_path, pid, canon_json=_full_canon(spine_n=3, payoff_n=3), title="闭环测试")

    async def run() -> str:
        from packages.workflows import get_workflow

        wf = get_workflow("chapter-plan")
        assert wf is not None, "chapter-plan 未注册"
        # 用一个返回极简 director-plan JSON 的 mock，避免任何 LLM 路由
        director_payload = {
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": cid,
            "chapter_goal": "测试",
            "core_conflict": "测试",
            "turning_point": "测试",
            "expected_role": "setup",
            "key_beats": [],
            "character_changes_planned": [],
            "hook_handling": [],
            "debt_handling": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        }
        engine = WorkflowEngine(str(db_path))
        run_id = engine.start_with_nodes(
            "chapter-plan",
            wf["nodes"],
            chapter_id=cid,
            initial_ctx={
                "db_path": str(db_path),
                "project_id": pid,
                "chapter_id": cid,
                "author_intent": "意图",
                "mock_providers": {"director": [json.dumps(director_payload, ensure_ascii=False)]},
            },
        )
        return run_id

    # 构造 settings 在 app 之外用不上，但 StoryStateService.get_current_state 要 db_path
    run_id = asyncio.run(run())

    conn = get_connection(db_path)
    try:
        ckpt_row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert ckpt_row is not None
    ckpt = json.loads(ckpt_row["checkpoint_json"])
    # checkpoint 顶层含 director_input dict；director_input 含 _reference_canon_consumed
    di = ckpt.get("director_input") or {}
    assert "_reference_canon_consumed" in di, (
        f"checkpoint.director_input 缺 _reference_canon_consumed；keys={list(di.keys())}"
    )
    audit = di["_reference_canon_consumed"]
    assert "canon_id" in audit
    assert "consumed_fields" in audit and "logline" in audit["consumed_fields"]
    # 同时 director_input 顶层有 reference_canon 注入键
    assert "reference_canon" in di


def test_scene_planner_node_emits_canon_consumed_audit_to_ctx(tmp_path: Path):
    """F4 修复：scene_planner 节点返回 dict 含 ``_reference_canon_consumed`` 审计键。

    该审计键会随节点输出进入 ctx → workflow_runs.checkpoint_json，与
    director / writer 节点的审计闭环对齐。仅透出审计键（{canon_id,
    consumed_fields}），不把 reference_canon 业务数据塞进 checkpoint。
    """
    from packages.workflows.chapter_write.pipeline import _scene_planner_node

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 项目有 canon → scene_planner 应当拿 emotion_curve + payoff_list
    _insert_canon(
        db_path,
        pid,
        canon_json=_full_canon(spine_n=3, payoff_n=3),
        title="F4 审计闭环",
    )

    plan = {
        "chapter_goal": "测试",
        "core_conflict": "测试",
        "turning_point": "测试",
        "key_beats": [],
    }
    ctx = {
        "db_path": str(db_path),
        "chapter_id": cid,
        "run_id": "test_run_f4",
        "loaded_plan": plan,
        # scene_planner 失败时也会透出审计键；用空 mock_providers 触发降级
        "mock_providers": {},  # 不配 scene_planner mock → 走降级路径
    }
    out = _scene_planner_node(ctx)
    # 降级路径下 scene_planner_status='failed' 也应透出审计键
    assert "_reference_canon_consumed" in out, (
        f"scene_planner 节点应透出 _reference_canon_consumed；keys={list(out.keys())}"
    )
    audit = out["_reference_canon_consumed"]
    assert "canon_id" in audit
    assert "consumed_fields" in audit
    # scene_planner 消费 emotion_curve / payoff_list；不含 style_params
    assert "emotion_curve" in audit["consumed_fields"]
    assert "payoff_list" in audit["consumed_fields"]
    assert "style_params" not in audit["consumed_fields"], (
        "scene_planner 不消费 style_params；style_params 是 writer 的"
    )


# ---------------------------------------------------------------------------
# 多 consumer 扩展（Sprint 11+）：scene_planner / writer 消费 ReferenceCanon
# ---------------------------------------------------------------------------


def test_writer_input_no_canon_does_not_inject_any_canon_key(tmp_path: Path):
    """项目无 canon → build_writer_input 不加 reference_canon / _reference_canon_consumed。"""
    from packages.core.context_engine.builders import build_writer_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    out = build_writer_input(db_path, cid, {})
    assert "reference_canon" not in out
    assert "_reference_canon_consumed" not in out


def test_canon_change_invalidates_director_and_writer_cache(tmp_path: Path):
    """F5 修复：director / writer 装配缓存键混入 active canon_id；
    拆书落新 canon 后旧装配缓存自然失效。
    """
    from packages.core.context_engine.builders import (
        _assembly_cache,
        _cache_reset,
        build_director_input,
        build_writer_input,
    )

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    # 第一次装配：无 canon
    out1 = build_director_input(db_path, pid, cid, "意图")
    assert "reference_canon" not in out1
    out1w = build_writer_input(db_path, cid, {})
    assert "reference_canon" not in out1w

    # 缓存中应有 2 条（director + writer）
    assert len(_assembly_cache) >= 2

    # 落新 canon（模拟「拆书完成」）。再次装配 → 缓存键变化 → 不命中
    canon_id = _insert_canon(
        db_path,
        pid,
        canon_json=_full_canon(),
        title="F5 拆书落新 canon",
    )
    out2 = build_director_input(db_path, pid, cid, "意图")
    assert "reference_canon" in out2
    assert out2["reference_canon"]["canon_id"] == canon_id

    out2w = build_writer_input(db_path, cid, {})
    assert "reference_canon" in out2w
    assert out2w["reference_canon"]["canon_id"] == canon_id

    # 同一 canon_id 再次装配 → 命中缓存（不重新算）
    out3 = build_director_input(db_path, pid, cid, "意图")
    out3w = build_writer_input(db_path, cid, {})
    # 断言命中缓存（返回的就是缓存里的对象引用，与上一次结果完全相同）
    assert out3 is out2
    assert out3w is out2w


def test_writer_input_injects_style_params_from_canon(tmp_path: Path):
    """active canon 注入：writer 拿 style_params；字段缺则该字段不计。"""
    from packages.core.context_engine.builders import build_writer_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_canon(db_path, pid, canon_json=_full_canon(), title="风格参照")

    out = build_writer_input(db_path, cid, {})
    canon = out["reference_canon"]
    assert isinstance(canon, dict)
    assert canon["canon_id"].startswith("can_")
    assert "style_params" in canon
    sp = canon["style_params"]
    # writer 优先保留 pov / dialogue / action / psychological / sentence_length
    assert sp["pov"] == "third_limited"
    assert sp["dialogue_ratio"] == 0.25
    assert sp["action_ratio"] == 0.45
    assert "sentence_length_distribution" in sp
    # 默认 _full_canon 的 style_params 整体 < 1500 字符，应不触发 truncated
    assert "__style_params_truncated__" not in canon
    # audit 写入
    audit = out["_reference_canon_consumed"]
    assert audit["canon_id"] == canon["canon_id"]
    assert audit["consumed_fields"] == ["style_params"]


def test_writer_input_truncates_style_params_when_too_large(tmp_path: Path):
    """style_params 整体 JSON 超过 1500 字符 → 逐项删减并打 truncated 标记。"""
    from packages.core.context_engine.builders import build_writer_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    # 构造巨型 paragraph_length_distribution 把整体撑爆
    huge_style = {
        "sentence_length_distribution": {"mean": 18.0, "median": 16.0, "max": 80},
        "dialogue_ratio": 0.25,
        "action_ratio": 0.45,
        "pov": "third_limited",
        # 制造超大字段
        "paragraph_length_distribution": {
            "mean": 120.0,
            "median": 100.0,
            "max": 600,
            "padded": "x" * 5000,
        },
        "psychological_ratio": 0.15,
        "environment_ratio": 0.15,
    }
    _insert_canon(
        db_path, pid,
        canon_json={"logline": "x", "style_params": huge_style},
        title="巨型风格",
    )

    out = build_writer_input(db_path, cid, {})
    canon = out["reference_canon"]
    assert canon["__style_params_truncated__"] is True
    sp = canon["style_params"]
    # 段落长度应被裁掉（最低优先级）；高优先级字段保留
    assert "paragraph_length_distribution" not in sp
    assert sp["pov"] == "third_limited"
    assert sp["dialogue_ratio"] == 0.25
    # 序列化后字符数 ≤ 上限
    encoded = json.dumps(sp, ensure_ascii=False)
    assert len(encoded) <= 1500


def test_scene_planner_payload_no_canon_omits_reference_canon(tmp_path: Path):
    """项目无 canon → _collect_scene_planner_inputs 不注入 reference_canon 键。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    payload = _collect_scene_planner_inputs(
        db_path, cid, {"expected_role": "setup", "key_beats": []},
    )
    assert "reference_canon" not in payload
    assert "_reference_canon_consumed" not in payload


def test_scene_planner_payload_injects_emotion_curve_and_payoff_list(tmp_path: Path):
    """active canon → scene_planner 注入 emotion_curve + payoff_list（payoff_list 截 ≤30）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_canon(
        db_path, pid,
        canon_json=_full_canon(spine_n=5, payoff_n=40),
        title="Planner 参照",
    )

    payload = _collect_scene_planner_inputs(
        db_path, cid, {"expected_role": "setup", "key_beats": []},
    )
    canon = payload["reference_canon"]
    assert canon["canon_id"].startswith("can_")
    # emotion_curve 透传（_full_canon 默认 3 条，≤ cap=50）
    assert isinstance(canon["emotion_curve"], list)
    assert len(canon["emotion_curve"]) == 3
    assert canon["emotion_curve"][0]["chapter_index"] == 1
    # payoff_list 截前 30 条
    assert len(canon["payoff_list"]) == 30
    # audit
    audit = payload["_reference_canon_consumed"]
    assert audit["canon_id"] == canon["canon_id"]
    assert set(audit["consumed_fields"]) == {"emotion_curve", "payoff_list"}


def test_scene_planner_payload_truncates_emotion_curve_to_cap(tmp_path: Path):
    """emotion_curve > 50 → 截到末尾 ≤50 条（保留最近节奏信号）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    big_canon = _full_canon(spine_n=3, payoff_n=3)
    big_canon["emotion_curve"] = [
        {"chapter_index": i, "valence": 0, "marker_type": "buildup"}
        for i in range(1, 200)
    ]
    _insert_canon(db_path, pid, canon_json=big_canon, title="超长曲线")

    payload = _collect_scene_planner_inputs(
        db_path, cid, {"expected_role": "setup", "key_beats": []},
    )
    canon = payload["reference_canon"]
    assert len(canon["emotion_curve"]) == 50
    # 取末尾 → chapter_index 应为 150..199
    assert canon["emotion_curve"][0]["chapter_index"] == 150
    assert canon["emotion_curve"][-1]["chapter_index"] == 199


def test_scene_planner_payload_no_style_params(tmp_path: Path):
    """scene_planner 不消费 style_params——确认仅 emotion_curve + payoff_list 出现。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_canon(db_path, pid, canon_json=_full_canon(), title="仅风格")

    payload = _collect_scene_planner_inputs(
        db_path, cid, {"expected_role": "setup", "key_beats": []},
    )
    canon = payload["reference_canon"]
    assert "style_params" not in canon
    assert "spine" not in canon
    assert "logline" not in canon
    assert "rhythm" not in canon
