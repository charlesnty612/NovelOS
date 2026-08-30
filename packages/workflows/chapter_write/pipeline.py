"""chapter_write 工作流（Sprint 4-A + P0 ScenePlanner 真实化 + P1 Polisher）。

节点列表：
- ``load_plan`` (Transform) —— 读 chapters.plan_json 准备 director_plan 输入。
- ``scene_planner`` (AI) —— P0 新增：调 scene_planner agent 把 director_plan 翻译为
  结构化 Scene Plan（含 slots / 冲突 / 信息边界 / 结尾钩子）。
  任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级到原 stub 机械映射逻辑，
  **不**阻断 writer run。
- ``writer`` (AI) —— 调 writer agent 生成本章 prose。
- ``polisher`` (AI) —— P1 新增：以 writer 产出的 prose + scan_ai_patterns(prose)
  确定性命中为输入，做去 AI 腔的文风级润色；不改情节 / 人物 / 事实。
  任何失败（prompt 缺失 / provider 异常 / 输出不合规 / 长度守恒失败）→ 兜底回退
  writer 原始 prose，**不**阻断 save_draft。
  mock 直通：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，跳过润色
  原样透传（既有 mock 测试不受影响；真实链路始终润色）。
- ``save_draft`` (State) —— 写 drafts 表 + chapters.status PLANNED→DRAFTED。
  优先取 ``polished_prose``（无则回落 ``writer_output.prose``）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.agent_runtime.structured_output import strip_think_blocks
from packages.core.context_engine import build_writer_input
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.model_router.router import capability_for
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.wordcount import visible_chars
from packages.core.workflow_runtime.engine import WorkflowNode

_log = logging.getLogger(__name__)


def _load_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    plan_json_raw = row["plan_json"]
    plan_json = json.loads(plan_json_raw) if plan_json_raw else {}
    if not plan_json.get("chapter_goal"):
        raise ValueError(
            f"chapter {chapter_id!r} has empty plan_json; run chapter-plan first"
        )
    return {"loaded_plan": plan_json}


_SCENE_PLANNER_PROMPT_VERSION = "scene_planner:v1"

# Scene Planner 失败降级：与原 stub 输出字段完全一致，保证 build_writer_input 零改动。
_SCENE_PLANNER_DEFAULT_STYLE = {
    "language": "zh-Hans",
    "pov": "third_person_limited",
    "dialogue_ratio": 0.4,
    "forbidden_words": ["仿佛", "如同", "本章目标"],
}


def _scene_planner_fallback(ctx: dict[str, Any]) -> dict[str, Any]:
    """降级路径：把 director.key_beats 逐个映射为 scene；每 scene slots=[1 个 action]。

    字段与 AI scene_planner 输出保持一致（scene_id / purpose / characters / location /
    conflict / turn / time_in_story / pov / pov_character_id / slots），下游
    build_writer_input 无需改动。
    """
    plan = ctx.get("loaded_plan") or {}
    beats = plan.get("key_beats") or []
    scenes: list[dict[str, Any]] = []
    for i, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        scene_id = f"scene_{i:03d}"
        slot_id = f"slot_{i:03d}"
        scenes.append(
            {
                "scene_id": scene_id,
                "purpose": beat.get("purpose") or "",
                "characters": beat.get("involved_characters", []),
                "location": (beat.get("involved_locations") or [None])[0],
                "conflict": "",
                "turn": "",
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": slot_id,
                        "type": "action",
                        "purpose": beat.get("purpose") or "",
                        "characters": beat.get("involved_characters", []),
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        )
    if not scenes:
        # 兜底：若 key_beats 为空则建 1 个空 scene
        scenes = [
            {
                "scene_id": "scene_001",
                "purpose": "本章内容（Director plan 未提供 beats，兜底规划）",
                "characters": [],
                "location": None,
                "conflict": "",
                "turn": None,
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": "slot_001",
                        "type": "action",
                        "purpose": "本章主要内容",
                        "characters": [],
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        ]
    return {"scene_plan": {"scenes": scenes}}


def _collect_scene_planner_inputs(
    db_path: str, chapter_id: str, plan: dict[str, Any]
) -> dict[str, Any]:
    """组装 scene_planner agent 输入（最小可运行口径）。

    - chapter 元信息、director_plan 来自 load_plan。
    - available_characters / available_locations 从 DB 取 id/name。
    - style_constraints 取项目配置或本地默认。
    - recent_prose 当前留空（Scene Planner 不依赖前章尾段亦可工作；非空为后续优化）。
    """
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT project_id, number, title FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        char_rows = conn.execute(
            """
            SELECT character_id, name FROM characters
            WHERE project_id = (SELECT project_id FROM chapters WHERE chapter_id = ?)
            ORDER BY name
            """,
            (chapter_id,),
        ).fetchall()
        loc_rows = conn.execute(
            """
            SELECT location_id, name FROM locations
            WHERE project_id = (SELECT project_id FROM chapters WHERE chapter_id = ?)
            ORDER BY name
            """,
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()

    project_id = chap_row["project_id"] if chap_row else None

    # style_constraints：优先读项目级；暂无则本地默认。
    style_constraints: dict[str, Any]
    if project_id:
        style_constraints = _load_project_style_constraints(db_path, project_id)
    else:
        style_constraints = dict(_SCENE_PLANNER_DEFAULT_STYLE)

    return {
        "agent": "scene_planner",
        "prompt_version": _SCENE_PLANNER_PROMPT_VERSION,
        "chapter": {
            "chapter_id": chapter_id,
            "title": chap_row["title"] if chap_row else None,
            "number": int(chap_row["number"]) if chap_row and chap_row["number"] is not None else 1,
            # plan_json 存的是 expected_word_count（3000）；target_word_count 为兼容旧字段
            "target_word_count": int(
                plan.get("expected_word_count")
                or plan.get("target_word_count")
                or 3000
            ),
            "expected_role": plan.get("expected_role"),
        },
        "director_plan": {
            "chapter_goal": plan.get("chapter_goal"),
            "core_conflict": plan.get("core_conflict"),
            "turning_point": plan.get("turning_point"),
            "key_beats": plan.get("key_beats") or [],
            "notes_for_planner": plan.get("notes_for_planner"),
            "revision_note": plan.get("revision_note"),
        },
        "available_characters": [
            {"character_id": r["character_id"], "name": r["name"]} for r in char_rows
        ],
        "available_locations": [
            {"location_id": r["location_id"], "name": r["name"]} for r in loc_rows
        ],
        "style_constraints": style_constraints,
        "recent_prose": {"last_chapter_excerpt": "", "last_scene_excerpt": ""},
    }


def _load_project_style_constraints(
    db_path: str, project_id: str
) -> dict[str, Any]:
    """读 projects.style_constraints_id → style_constraints 配置；缺失则返回默认。

    对老库（无 style_constraints 表 / 无列）做防御性捕获，避免 DDL 差异阻断 write。
    """
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return dict(_SCENE_PLANNER_DEFAULT_STYLE)
    try:
        row = conn.execute(
            "SELECT style_constraints_id FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return dict(_SCENE_PLANNER_DEFAULT_STYLE)
    style_id = row["style_constraints_id"] if row else None
    if style_id:
        try:
            sc_row = conn.execute(
                "SELECT config_json FROM style_constraints WHERE style_constraints_id = ?",
                (style_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            conn.close()
            return dict(_SCENE_PLANNER_DEFAULT_STYLE)
        if sc_row and sc_row["config_json"]:
            try:
                cfg = json.loads(sc_row["config_json"])
                if isinstance(cfg, dict):
                    conn.close()
                    return cfg
            except (TypeError, ValueError):
                pass
    conn.close()
    return dict(_SCENE_PLANNER_DEFAULT_STYLE)


def _scene_planner_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """P0 Scene Planner AI 节点。

    行为：
    1. 取 loaded_plan + chapter 元信息 + 项目角色/地点，组装 scene_planner payload。
    2. 调 ``run_agent(..., agent_name='scene_planner', expected='scene_planner',
       mock_script=...)``；runner 内部走 parse_json → validate_contract("scene_planner")
       → 写 ai_call_logs。
    3. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败 / 输出字段缺失）→ 降级
       到 ``_scene_planner_fallback``，**不**抛错、**不**阻断 writer。
    4. 输出顶层保留 ``scene_plan`` key（与 stub 兼容），同时把原始 AI 输出放入
       ``scene_planner_output`` 供观测。

    支持 mock：``ctx["mock_providers"]["scene_planner"]`` 与 writer/critic 同机制。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    mock_script = (ctx.get("mock_providers") or {}).get("scene_planner")

    payload = _collect_scene_planner_inputs(db_path, chapter_id, plan)
    try:
        out = run_agent(
            db_path,
            "scene_planner",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="scene_planner",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("scene_planner")
            ),
        )
        if not isinstance(out, dict):
            raise ValueError(f"scene_planner output not dict: {type(out).__name__}")
        scenes = out.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("scene_planner output missing non-empty 'scenes'")
        # 只把 scene_planner 的核心 scenes 包装成 scene_plan；保留完整输出供观测。
        return {
            "scene_plan": {"scenes": scenes},
            "scene_planner_output": out,
            "scene_planner_status": "ok",
        }
    except Exception as exc:  # noqa: BLE001 —— 任何失败均降级，不阻断 writer
        _log.warning(
            "chapter_write.scene_planner degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        fallback = _scene_planner_fallback(ctx)
        return {
            **fallback,
            "scene_planner_output": None,
            "scene_planner_status": "failed",
            "scene_planner_error": str(exc),
        }


def _resolve_writer_context_mode(ctx: dict[str, Any]) -> str:
    """解析 writer context_mode（V3.2 P2-1）。

    优先级：
    1. workflow 上下文 ``writer_context_mode`` 字段（显式传参 > 一切）；
    2. 环境变量 ``NOVELOS_WRITER_CONTEXT_MODE``（全局开关，便于回归测试一键切换）；
    3. 默认 ``"paged"``（V3.2 行为变更：writer 默认按 L0/L1/L2 分页注入）。

    返回值仅做白名单校验，非合法值回退默认 ``"paged"`` 并发出警告（不抛错——
    装配阶段抛错会让运行中的 writer workflow 失败，违反「上下文裁剪是性能
    优化、不应是阻断级」的初衷）。
    """
    raw = ctx.get("writer_context_mode")
    if raw is None:
        raw = os.environ.get("NOVELOS_WRITER_CONTEXT_MODE")
    if raw is None:
        return "paged"
    if raw in ("full", "paged"):
        return raw
    # 非法值：兜底 paged + 不抛错（仅开发期日志可见）
    _log.warning(
        "writer_context_mode=%r is invalid; falling back to 'paged'", raw,
    )
    return "paged"


def _latest_draft_text(db_path: str, chapter_id: str) -> str:
    """读该章最新 draft content（drafts 表，按 version DESC LIMIT 1）。

    无 draft 行或 content 为空 → 返回空串。DB 异常（缺失表/列）→ 返回空串，
    与既有"上下文缺失不阻断 writer run"口径一致。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()
    if row is None:
        return ""
    return row["content"] or ""


def _writer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    scene_plan = ctx["scene_plan"]
    context_mode = _resolve_writer_context_mode(ctx)
    payload = build_writer_input(
        db_path,
        chapter_id,
        scene_plan,
        # 目标字数优先取 plan 的 expected_word_count（3000）；ctx 覆盖其次；默认 3000
        target_word_count=ctx.get("target_word_count")
        or int((ctx.get("loaded_plan") or {}).get("expected_word_count") or 0)
        or 3000,
        context_mode=context_mode,
    )

    # 修订模式（基于现有 draft 局部修改）注入：读最新 draft content +
    # plan_json.revision_note，二者皆非空 → mode='revise'。
    if ctx.get("fresh_write"):
        # 全新重写：忽略旧稿与改稿意见（用于跨模型文风对比）
        draft_text = ""
        revision_note = ""
    else:
        draft_text = _latest_draft_text(db_path, chapter_id)
        revision_note = (ctx.get("loaded_plan") or {}).get("revision_note") or ""
    mode = "revise" if (revision_note and draft_text) else "write"
    payload["mode"] = mode
    payload["draft_text"] = draft_text
    if revision_note:
        # revision_note 在 build_writer_input 内部已落入 director_plan.revision_note；
        # 顶层再冗余一份，便于 writer prompt / 测试断言不走嵌套结构。
        payload["revision_note"] = revision_note

    mock_script = (ctx.get("mock_providers") or {}).get("writer")
    # 能力路由：revise=定向局部修改，走 light（与 critic/summarizer 同源，省创作额度）；
    # write / fresh_write 走默认 creative_writing。mock 路径不走 capability，
    # capability_override 仅在真实链路下被 ModelRouter 使用，不影响 mock 行为。
    writer_capability_override: str | None = "light" if mode == "revise" else None
    out = run_agent(
        db_path,
        "writer",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="writer",
        mock_script=mock_script,
        capability_override=writer_capability_override,
        profile_id=(ctx.get("model_overrides") or {}).get(
            capability_for("writer")
        ),
    )
    # V3.1.1 V-P0：writer 本节点真实落库模型 id（mock 路径无 ai_call_logs 行 → None）。
    # 用于 drafts.model_id 记录真实 provider/model，避免列表页无法区分模型。
    writer_model_id: str | None = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT model_id FROM ai_call_logs "
            "WHERE run_id = ? AND node_run_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, ctx.get("_current_node_run_id")),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is not None:
        writer_model_id = row["model_id"]
    # writer_input 透出到 ctx（→ checkpoint_json）供测试断言；生产仅作为可观测钩子。
    return {
        "writer_output": out,
        "_writer_context_mode": context_mode,
        "writer_input": payload,
        "writer_model_id": writer_model_id,
    }


def _polish_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """P1 Polisher AI 节点（去 AI 腔文风润色）。

    行为：
    1. 取 ``writer_output.prose``，跑 ``scan_ai_patterns`` 拿确定性命中。
    2. 组装 polisher payload（draft_text + ai_findings + style_constraints）。
    3. mock 直通判定：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，
       跳过润色原样透传（既有用例零回归；真实链路始终润色）。
    4. 调 ``run_agent(..., agent_name='polisher', expected='polisher', mock_script=...)``。
    5. 长度守恒：``polished_text`` 与原文 ``visible_chars`` 差距 >10% → 兜底回退原文。
    6. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败）→ 兜底回退原文，
       写 ``polisher_status='failed'``，**不**阻断 save_draft。
    7. 输出 ``polished_prose`` 进 ctx，``save_draft_node`` 优先取。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    writer_output = ctx.get("writer_output") or {}
    raw_prose = strip_think_blocks(writer_output.get("prose") or "")
    mock_providers = ctx.get("mock_providers") or {}

    # mock 直通：既有 mock 流（仅含 writer、不含 polisher）原样透传，
    # 保证 test_chapter_write_revise_mode / test_chapter_write_scene_planner 等
    # 既有测试零回归；真实链路（mock_providers 为 None 或包含 polisher）始终润色。
    if mock_providers and ("writer" in mock_providers) and ("polisher" not in mock_providers):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "mock 直通：未配置 polisher mock，原样透传 writer prose",
            "polisher_status": "passthrough",
            "ai_findings": [],
        }

    ai_findings = scan_ai_patterns(raw_prose)
    payload: dict[str, Any] = {
        "agent": "polisher",
        "prompt_version": "polisher:v1",
        "draft_text": raw_prose,
        "ai_findings": ai_findings,
        "style_constraints": {
            "language": "zh-Hans",
            "pov": "third_person_limited",
            "forbidden_words": ["仿佛", "如同", "宛如"],
        },
    }

    mock_script = mock_providers.get("polisher")
    try:
        out = run_agent(
            db_path,
            "polisher",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="polisher",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("polisher")
            ),
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败均兜底回退原文
        _log.warning(
            "chapter_write.polisher degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": f"润色失败已回退原文：{exc}",
            "polisher_status": "failed",
            "polisher_error": str(exc),
            "ai_findings": ai_findings,
        }

    if not isinstance(out, dict):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出非 dict，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }
    polished = out.get("polished_text")
    if not isinstance(polished, str):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出 polished_text 非字符串，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }

    # 长度守恒：polished 与原文 visible_chars 差距 >10% → 回退原文（兜底）
    orig_len = visible_chars(raw_prose)
    new_len = visible_chars(polished)
    if orig_len > 0:
        delta = abs(new_len - orig_len) / orig_len
        if delta > 0.10:
            _log.warning(
                "chapter_write.polisher length drift: chapter_id=%s orig=%d new=%d delta=%.2f",
                chapter_id, orig_len, new_len, delta,
            )
            return {
                "polished_prose": raw_prose,
                "polish_changes_summary": (
                    f"润色超长度限制（Δ{delta:.0%}），已回退原文"
                ),
                "polisher_status": "length_drift",
                "ai_findings": ai_findings,
            }

    changes_summary = out.get("changes_summary") or ""
    return {
        "polished_prose": polished,
        "polish_changes_summary": changes_summary,
        "polisher_status": "ok",
        "ai_findings": ai_findings,
        "polisher_output": out,
    }


def _save_draft_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    writer_output = ctx.get("writer_output") or {}
    # P1：polished_prose 优先；缺失（passthrough / 失败兜底）则回落 writer 原始 prose。
    prose = strip_think_blocks(
        ctx.get("polished_prose") or writer_output.get("prose") or ""
    )
    self_report = writer_output.get("self_report") or {}
    word_count = int(self_report.get("word_count") or len(prose))
    prompt_version = writer_output.get("prompt_version") or "writer:v1"

    draft_id = new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        # 先看是否有 draft 行（按 chapter + version 累计）
        max_row = conn.execute(
            "SELECT MAX(version) AS v FROM drafts WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        next_v = (max_row["v"] or 0) + 1
        conn.execute(
            """
            INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,
                                prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, 'writer:v1', ?, ?, ?)
            """,
            (
                draft_id,
                chapter_id,
                next_v,
                prose,
                prompt_version,
                ctx.get("writer_model_id") or "mock/mock",
                now,
            ),
        )
        # chapters.status PLANNED→DRAFTED（走白名单）
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        status = cur["status"]
        if status == "PLANNED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status != "DRAFTED":
            # 与 chapter_commit 硬校验口径一致：仅 PLANNED / DRAFTED 允许 write；
            # DRAFTED 重跑允许追加新 draft 版本（支撑人工改稿循环）。
            raise ValueError(
                f"chapter {chapter_id} status={status} 不允许 write，仅 PLANNED/DRAFTED 可写"
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "draft_id": draft_id,
        "draft_version": next_v,
        "word_count": word_count,
        "prompt_version": prompt_version,
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("load_plan", "Transform", _load_plan_node),
        WorkflowNode(
            "scene_planner", "AI", _scene_planner_node, agent_name="scene_planner"
        ),
        WorkflowNode("writer", "AI", _writer_node, agent_name="writer"),
        WorkflowNode("polisher", "AI", _polish_node, agent_name="polisher"),
        WorkflowNode("save_draft", "State", _save_draft_node),
    ]


WORKFLOW = {
    "name": "chapter-write",
    "version": "v1",
    "description": (
        "Director Plan → Scene Planner(AI) → Writer prose → Polisher(AI) → "
        "drafts table; chapter status PLANNED→DRAFTED; scene_planner / polisher "
        "失败均降级不阻断 writer"
    ),
    "nodes": _build_nodes(),
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_write.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
