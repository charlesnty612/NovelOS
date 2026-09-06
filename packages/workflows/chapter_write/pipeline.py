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
- ``length_check`` (Transform) —— 字数闭环实测节点（V3.7+ P1）：
  用 :func:`packages.core.quality.wordcount.classify_prose_length` 权威口径实测
  polished_prose（缺则 writer_output.prose）字数，读项目级 word_band_json 覆盖
  （与 chapter_review._basic_checks_node 同口径）。
  输出 ``length_report = {visible_chars, target, band_low, band_high, status,
  within_band}`` + ``target_word_count`` + ``word_band_cfg`` 三个独立 key
  （便于 condense 节点与 save_draft 复用），并把 ``length_check_passed`` 布尔
  写入 ctx 供 condense 路由判定。
- ``condense`` (AI) —— 仅在 length_check 未通过且未超过 2 轮时执行。
  复用 polisher 的 capability 绑定（``capability_for('polisher')``），prompt
  携带实测字数、目标带上下限、需净减比例，遵循 writer-v1 规则 20 的压缩纪律
  （优先砍铺垫/重复意象/冗词，不砍节拍、不删场景、保持文风与既有设定用语）。
  任何失败 → 兜底保留当前 polished_prose 放行，``condense_status='failed'``，
  **不**阻断 save_draft。两轮后仍超带 → ``condense_status='over_band_after_2_rounds'``
  放行，save_draft 在 deviations 中标注实测字数与偏差。
- ``save_draft`` (State) —— 写 drafts 表 + chapters.status PLANNED→DRAFTED。
  优先取 ``polished_prose``（无则回落 ``writer_output.prose``）。
  word_count 改用权威实测（``ctx['length_report']['visible_chars']``），
  不再采信 self_report.word_count（实证 ch4-6：writer 自报 3008-3172
  vs 实际 4721-5842，偏差 50%+）。超带偏差注记追加到 chapter plan_json.deviations。
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
from packages.core.context_engine.builders import (
    _REFERENCE_CANON_CONSUMER_PLANNER,
    _reference_canon_excerpt,
)
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.model_router.router import capability_for
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.wordcount import (
    classify_prose_length,
    resolve_band_config,
    visible_chars,
)
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

    # Reference Canon 注入：scene_planner 消费 emotion_curve（张力曲线）+ payoff_list
    # （Scene 级爽点排布）。与 director / writer 共用同一 excerpt 函数（consumer=planner
    # 走 emotion_curve + payoff_list 分支）。无 active canon → 不注入 reference_canon 键，
    # 与 director 缺席语义保持一致。
    reference_canon_inject: dict | None = None
    reference_canon_audit: dict | None = None
    if project_id:
        conn3 = get_connection(db_path)
        try:
            reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(
                conn3, project_id, consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
            )
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 视为无 canon，不阻断 Scene Planner 装配。
            reference_canon_inject = None
            reference_canon_audit = None
        finally:
            conn3.close()

    # style_constraints：优先读项目级；暂无则本地默认。
    style_constraints: dict[str, Any]
    if project_id:
        style_constraints = _load_project_style_constraints(db_path, project_id)
    else:
        style_constraints = dict(_SCENE_PLANNER_DEFAULT_STYLE)

    # world_state_excerpts：复用既有字段结构（P2-补洞），让 Scene Planner 能看到
    # world_rules_relevant（项目级硬设定，如原著要素锁），保证"沿传 Director 约束"
    # 不会因本节点缺 world_rules 而退化为凭印象。L0 world_rules 全量常驻，与
    # director/writer 装配口径保持一致。无项目 / 异常 → 返回最小结构，不阻断。
    world_state_excerpts: dict[str, Any] = {
        "current_time_in_story": None,
        "current_location": None,
        "locations": [],
        "active_factions": [],
        "world_rules_relevant": [],
        "sensory_anchors": [],
    }
    if project_id:
        conn2 = get_connection(db_path)
        try:
            rule_rows = conn2.execute(
                "SELECT world_rule_id, name, statement FROM world_rules "
                "WHERE project_id = ? ORDER BY world_rule_id ASC",
                (project_id,),
            ).fetchall()
            world_state_excerpts["world_rules_relevant"] = [
                {
                    "world_rule_id": r["world_rule_id"],
                    "name": r["name"],
                    "statement": r["statement"],
                }
                for r in rule_rows
            ]
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 保留空列表，不阻断 Scene Planner 装配。
            pass
        finally:
            conn2.close()

    payload: dict[str, Any] = {
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
        "world_state_excerpts": world_state_excerpts,
        "style_constraints": style_constraints,
        "recent_prose": {"last_chapter_excerpt": "", "last_scene_excerpt": ""},
    }

    # Reference Canon（emotion_curve + payoff_list）。无 canon 时不注入该键——
    # 与 director 缺席语义保持一致（scene_planner 不得索要 canon）。
    if reference_canon_inject is not None:
        payload["reference_canon"] = reference_canon_inject
    if reference_canon_audit is not None:
        payload["_reference_canon_consumed"] = reference_canon_audit
    return payload


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
        # F4 修复：scene_planner 节点输出透出 ``_reference_canon_consumed`` 审计键
        # 到 ctx（→ checkpoint_json），与 director / writer 节点的审计闭环对齐。
        # 仅透出审计键（{canon_id, consumed_fields}），不把 reference_canon 业务
        # 数据塞进 checkpoint（payload 内的 reference_canon 业务键仅供 scene_planner
        # 本节点消费，不进入 ctx）。
        scene_planner_audit: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp = get_connection(db_path)
        try:
            chap_row = conn_sp.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp.close()
        if project_id_for_audit:
            try:
                conn_sp2 = get_connection(db_path)
                try:
                    _, scene_planner_audit = _reference_canon_excerpt(
                        conn_sp2,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit = None
                finally:
                    conn_sp2.close()
            except Exception:  # noqa: BLE001 —— 审计键获取失败不阻断主流程
                scene_planner_audit = None
        ret: dict[str, Any] = {
            "scene_plan": {"scenes": scenes},
            "scene_planner_output": out,
            "scene_planner_status": "ok",
        }
        if scene_planner_audit is not None:
            ret["_reference_canon_consumed"] = scene_planner_audit
        return ret
    except Exception as exc:  # noqa: BLE001 —— 任何失败均降级，不阻断 writer
        _log.warning(
            "chapter_write.scene_planner degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        fallback = _scene_planner_fallback(ctx)
        # scene_planner 失败/降级时也尝试透出审计键（与 writer 失败兜底语义对齐），
        # 便于「该章生成消费了哪份 canon」在审计层可观测。
        scene_planner_audit_fb: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp3 = get_connection(db_path)
        try:
            chap_row = conn_sp3.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp3.close()
        if project_id_for_audit:
            try:
                conn_sp4 = get_connection(db_path)
                try:
                    _, scene_planner_audit_fb = _reference_canon_excerpt(
                        conn_sp4,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit_fb = None
                finally:
                    conn_sp4.close()
            except Exception:  # noqa: BLE001
                scene_planner_audit_fb = None
        ret_fb: dict[str, Any] = {
            **fallback,
            "scene_planner_output": None,
            "scene_planner_status": "failed",
            "scene_planner_error": str(exc),
        }
        if scene_planner_audit_fb is not None:
            ret_fb["_reference_canon_consumed"] = scene_planner_audit_fb
        return ret_fb


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


# 修订模式机读核销表（writer-v1.md §6.1 第 11 条）尾块标记。
# 约定：writer revise 模式输出 prose 末尾追加独占一行的
# "---REVISION-CHECKLIST---" 分隔行 + 一行 JSON 数组。管线按此分隔行切分，
# 前半 = 真正正文（落 drafts.content），后半 = 核销表（落 run 节点产出）。
_REVISION_CHECKLIST_MARKER = "---REVISION-CHECKLIST---"


def _parse_revision_checklist(prose: str) -> tuple[str, list[dict[str, Any]] | None]:
    """从 prose 末尾剥离 REVISION-CHECKLIST 尾块；返回 (clean_prose, checklist_or_None)。

    行为契约（与 writer-v1.md §6.1 第 11 条 + 测试断言对齐）：
    - 仅当 prose 含 `---REVISION-CHECKLIST---` 分隔行（独占一行）时进入切分逻辑；
      缺失 → 返回 ``(prose.rstrip(), None)``（不报错，revise 模式允许模型漏写）。
    - 切分后，分隔行后的剩余文本先 rstrip（剥掉模型可能留的尾部空白），
      再尝试 ``json.loads``：成功且为 list → 返回核销表；失败/非 list → ``None``（fail-soft）。
    - 前半（正文侧）rstrip 一次，去掉分隔行前的多余空行；不改变正文内容本身。

    调用方须依据 ``checklist is None`` 决定是否在 ctx 写 ``revision_checklist=None``
    + log warning（不阻断 writer run）。
    """
    if not isinstance(prose, str):
        return ("", None)
    if _REVISION_CHECKLIST_MARKER not in prose:
        return (prose.rstrip(), None)
    # 仅在分隔行**独占一行**时切分（避免误切正文内偶然出现的同名行；不过核销表
    # 约定就是独占一行，故首处命中即认定为契约边界）。
    head, _, tail = prose.partition(_REVISION_CHECKLIST_MARKER)
    checklist_text = tail.strip()
    if not checklist_text:
        # 分隔行后为空 → 模型把分隔行写出但忘了 JSON → 当作缺失，fail-soft
        return (head.rstrip(), None)
    try:
        parsed = json.loads(checklist_text)
    except (TypeError, ValueError):
        return (head.rstrip(), None)
    if not isinstance(parsed, list):
        return (head.rstrip(), None)
    # 仅保留 dict 元素；非 dict 元素丢弃（不阻断）
    items: list[dict[str, Any]] = [it for it in parsed if isinstance(it, dict)]
    return (head.rstrip(), items)


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

    # 修订模式机读核销表（writer-v1.md §6.1 第 11 条）：从 prose 末尾剥离
    # `---REVISION-CHECKLIST---` 尾块，前半 = 真正正文（落 drafts.content），
    # 后半 = 核销表（落 run 节点 revision_checklist 产出，便于审查改稿意见是否
    # 被真正执行）。fail-soft：尾块缺失/JSON 坏 → checklist=None + log warning，
    # 正文保持原样不阻断。
    revision_checklist: list[dict[str, Any]] | None = None
    if mode == "revise" and isinstance(out, dict):
        raw_prose = out.get("prose") or ""
        clean_prose, revision_checklist = _parse_revision_checklist(raw_prose)
        if revision_checklist is None:
            _log.warning(
                "chapter_write.writer revision_checklist parse failed: "
                "chapter_id=%s mode=revise (missing marker or bad JSON)",
                chapter_id,
            )
        # 无论剥离成功与否，都用 clean_prose 覆盖 out["prose"]——保证下游
        # polisher / save_draft 不会把核销表尾块混进草稿正文 / word_count 统计。
        out["prose"] = clean_prose
        # word_count 只统计剥离后正文：模型在 self_report.word_count 里通常按
        # 全文长度（含核销表 JSON 行）统计，会拉高 word_count 字段；此处覆写为
        # clean_prose 的字符数，确保 save_draft 落库的 word_count 与
        # drafts.content 实际长度一致。
        sr = out.get("self_report")
        if isinstance(sr, dict):
            sr["word_count"] = len(clean_prose)
            out["self_report"] = sr

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
    # revision_checklist：仅 revise 模式且尾块解析成功时为 list；其余情况为 None
    # （write 模式 / revise 但尾块缺失或 JSON 坏）。进 ctx → checkpoint_json / run
    # 节点产出，run detail 可见，便于审查改稿意见是否被真正执行。
    return {
        "writer_output": out,
        "_writer_context_mode": context_mode,
        "writer_input": payload,
        "writer_model_id": writer_model_id,
        "revision_checklist": revision_checklist,
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


# ---------------------------------------------------------------------------
# 字数闭环：length_check + condense（V3.7+ P1 字数闭环节点）
# ---------------------------------------------------------------------------

# condense 最大重试轮次（含 condense 节点的执行次数）；超过则放行并标注偏差。
_MAX_CONDENSE_ROUNDS = 2


def _resolve_chapter_word_band(
    db_path: str, chapter_id: str
) -> tuple[int, dict[str, Any]]:
    """读 chapter → project → projects.word_band_json，覆盖 → resolve_band_config。

    返回 ``(chapter_project_id_or_empty, band_cfg)``。band_cfg 始终是合法可
    ``**word_band(...)`` 展开的 dict（无覆盖 = 模块默认 0.85/1.15/1200）。

    与 chapter_review/pipeline.py _basic_checks_node L111-156 同口径（共用
    ``resolve_band_config``），保证 review 侧与 write 侧字数带读源唯一。

    防御：``resolve_band_config`` 在 ratios/floor 非法值时会抛 ``ValueError``
    （如 low_ratio > high_ratio 倒挂、floor=负数等）。此处捕获并落默认带 + 告警，
    与项目内「非法 JSON → 视为无覆盖」的 fail-soft 风格一致。chapter_review 侧
    ``_basic_checks_node`` 是独立函数、未复用本 helper，行为差异不在本任务范围
    （已记 Known Issue / 后续可一并覆盖）。
    """
    chapter_project_id = ""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["project_id"]:
            chapter_project_id = row["project_id"]
    except Exception:  # noqa: BLE001
        return chapter_project_id, resolve_band_config(None)

    if not chapter_project_id:
        return chapter_project_id, resolve_band_config(None)

    try:
        conn2 = get_connection(db_path)
        try:
            try:
                band_row = conn2.execute(
                    "SELECT word_band_json FROM projects WHERE project_id = ?",
                    (chapter_project_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                band_row = None
        finally:
            conn2.close()
    except Exception:  # noqa: BLE001
        band_row = None

    if band_row is None or not band_row["word_band_json"]:
        return chapter_project_id, resolve_band_config(None)
    try:
        parsed = json.loads(band_row["word_band_json"])
    except (TypeError, ValueError):
        return chapter_project_id, resolve_band_config(None)
    if not isinstance(parsed, dict):
        return chapter_project_id, resolve_band_config(None)
    try:
        return chapter_project_id, resolve_band_config(parsed)
    except ValueError as exc:
        # 非法值（low_ratio > high_ratio 倒挂 / floor 负数等）→ 落默认 + 告警。
        _log.warning(
            "chapter_write._resolve_chapter_word_band: invalid word_band_json "
            "project_id=%s err=%s; fall back to default band",
            chapter_project_id, exc,
        )
        return chapter_project_id, resolve_band_config(None)


def _resolve_target_word_count(ctx: dict[str, Any], plan: dict[str, Any]) -> int:
    """解析本章目标字数（与 _writer_node / review._basic_checks 同口径）。

    优先级：ctx 显式传入 → plan_json.expected_word_count → plan_json.target_word_count
    → 默认 3000。
    """
    raw = ctx.get("target_word_count")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    expected = plan.get("expected_word_count") if isinstance(plan, dict) else None
    if expected:
        try:
            return max(1, int(expected))
        except (TypeError, ValueError):
            pass
    legacy = plan.get("target_word_count") if isinstance(plan, dict) else None
    if legacy:
        try:
            return max(1, int(legacy))
        except (TypeError, ValueError):
            pass
    return 3000


def _length_check_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：权威口径实测 polished_prose 字数，断是否在项目字数带内。

    输入优先级：``polished_prose``（polisher/fallback 后）→ ``writer_output.prose``。
    不调 LLM，pure Transform；纯依赖 ``visible_chars`` / ``classify_prose_length``。

    输出字段（ctx → checkpoint_json / run 节点产出均可见）：
    - ``length_report``：classify_prose_length 完整 dict（visible_chars/target/
      band_low/band_high/deviation_pct/status）外加 ``within_band`` bool
      （=status == 'in_band'）。
    - ``target_word_count``：本节点选用的目标字数，供 condense / save_draft 复用。
    - ``word_band_cfg``：本节点选用的 band 配置 dict，供 condense prompt 注入。
    - ``length_check_passed``：bool，True ⇒ condense 跳过，False ⇒ condense 触发。

    不抛错：DB 异常 / 空 prose 走保守路径（status='under', visible=0，
    within_band=False），下游 condense 若无可压缩文本会走 0 长度不动。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    target = _resolve_target_word_count(ctx, plan)
    _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)

    writer_output = ctx.get("writer_output") or {}
    raw_prose = ctx.get("polished_prose")
    if not isinstance(raw_prose, str) or not raw_prose.strip():
        raw_prose = writer_output.get("prose") or ""
    if not isinstance(raw_prose, str):
        raw_prose = ""

    report = classify_prose_length(
        raw_prose, target, **band_cfg,
    )
    report["within_band"] = report["status"] == "in_band"
    # condense_rounds 已跑几轮（ctx 累积；首轮 length_check 默认 0）。
    rounds_used = int(ctx.get("condense_rounds") or 0)
    return {
        "length_report": report,
        "target_word_count": target,
        "word_band_cfg": band_cfg,
        "length_check_passed": report["within_band"],
        # 主会话约定：唯一计数键 ctx['condense_rounds']，length_check 仅 echo 不改写。
        "condense_rounds": rounds_used,
    }


def _condense_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：超带时调 polisher capability 做压缩（最多 2 轮）。

    触发条件：``length_check_passed`` False 且 ``condense_rounds < _MAX_CONDENSE_ROUNDS (2)``。
    不触发条件：带内 / 已达 2 轮 → 直接放行。

    计数器约定（smart 审查 P2 一致性收口）：
    - **唯一计数键** ``ctx['condense_rounds']``（length_check / condense /
      save_draft / 偏差注记 全部读同一把 key）。
    - 计轮规则：真正调 LLM（无论是 mock 还是真实）→ ``condense_rounds + 1``；
      skipped_in_band / skipped_empty / skipped_no_mock / over_band_after_2_rounds
      **均不计轮**（不 +1；早返回路径 preserve rounds_used 不动）。
    - 返回值补 ``condense_status`` 供观测（早返回路径也带，便于 run detail 一眼看清）。

    输出确定性守卫（防 LLM 返回空 / 更长 / 非字符串把坏草灌进 drafts）：
    - 空串 / 非 str / visible_chars >= 压缩前 visible_chars → 拒收，保留当前 prose，
      ``condense_status='failed_no_shrink'``，**计 1 轮**（调用过 LLM 但产出无效）。

    行为契约：
    - payload 必须携带实测数字与目标带（**不**让 LLM 自报字数自决目标）。
    - prompt 注入 writer-v1 §6.1 规则 20 压缩纪律（优先砍铺垫/重复意象/冗词，
      不砍节拍、不删场景、保持文风与既有设定用语）。
    - 任何失败（prompt 缺失 / provider 异常 / 契约失败）→ 兜底保留当前
      polished_prose 不动；``condense_status='failed'``，**不**阻断 save_draft。
    - 输出 ``condensed_prose``（=新 polished_prose 候选），并把 ``condense_rounds``
      +1 写回 ctx，供下一次 length_check 计数。
    """
    db_path = ctx["db_path"]
    run_id = ctx.get("run_id", "")
    chapter_id = ctx["chapter_id"]
    mock_providers = ctx.get("mock_providers") or {}

    report = ctx.get("length_report") or {}
    rounds_used = int(ctx.get("condense_rounds") or 0)
    within = bool(ctx.get("length_check_passed"))

    # ---- 早返回路径：均不计轮，preserve rounds_used ----
    if within:
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "skipped_in_band",
            "condense_rounds": rounds_used,
        }

    if rounds_used >= _MAX_CONDENSE_ROUNDS:
        # 已达 2 轮上限仍超带：放行 + 标注偏差（save_draft 会写到 deviations）。
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "over_band_after_2_rounds",
            "condense_rounds": rounds_used,
            "condense_report": report,
        }

    current_prose = ctx.get("polished_prose")
    if not isinstance(current_prose, str) or not current_prose.strip():
        # 没东西可压缩（length_check 走兜底 0 字路径）→ 跳过不调 LLM；不计轮。
        return {
            "polished_prose": current_prose or "",
            "condense_status": "skipped_empty",
            "condense_rounds": rounds_used,
        }

    visible = int(report.get("visible_chars") or visible_chars(current_prose))
    target = int(report.get("target") or ctx.get("target_word_count") or 3000)
    band_low = int(report.get("band_low") or 0)
    band_high = int(report.get("band_high") or target)
    excess = max(0, visible - band_high)
    excess_pct = round(visible / target * 100, 1) if target > 0 else 0.0
    target_pct = round(target / visible * 100, 1) if visible > 0 else 0.0

    payload: dict[str, Any] = {
        "agent": "condense",
        "prompt_version": "condense:v1",
        "draft_text": current_prose,
        "length_context": {
            "visible_chars": visible,
            "target": target,
            "band_low": band_low,
            "band_high": band_high,
            "excess_chars": excess,
            "excess_pct": excess_pct,
            "target_pct": target_pct,
            "round": rounds_used + 1,
            "max_rounds": _MAX_CONDENSE_ROUNDS,
        },
        "condense_directive": (
            "实测当前正文「{visible} 字」，目标字数带 [low={low}, high={high}] "
            "（target={target}）。当前超出 band_high 约 {excess_pct}%（约 {excess} 字），"
            "需净减至 band_high 以内（净减至 target 的 {target_pct}% 量级）。\n\n"
            "压缩纪律（writer-v1 §6.1 规则 20）：\n"
            "1. 优先砍铺垫、重复意象、冗词、装饰性修辞；\n"
            "2. 不砍节拍、不删场景、不改情节线、不删关键人物动作；\n"
            "3. 保持既有文风与人称设定；\n"
            "4. 保持既有设定用语（角色名、地名、专有名词不替换）。\n\n"
            "输出 JSON：{{\"polished_text\": <压缩后正文>, "
            "\"changes_summary\": <改动说明>}}"
        ).format(
            visible=visible,
            low=band_low,
            high=band_high,
            target=target,
            excess_pct=excess_pct,
            excess=excess,
            target_pct=target_pct,
        ),
    }

    # mock 直通：condense 不在 mock_providers 字典 → 原样透传（与既有
    # writer-only mock 流一致；mock_providers 含 condense 时走真实 mock）。
    condense_mock = mock_providers.get("condense") if isinstance(mock_providers, dict) else None
    if not condense_mock:
        # 既无 mock 也无真实模型：保守不动（fail-soft，不阻断）；不计轮
        # （没调 LLM，不应占用本就跑不完的 2 轮预算）。
        return {
            "polished_prose": current_prose,
            "condense_status": "skipped_no_mock",
            "condense_rounds": rounds_used,
        }

    if not run_id:
        # 单测路径可能没传 run_id；安全降级为「不入库」占位
        run_id = ""

    next_rounds = rounds_used + 1  # 调 LLM 必计 1 轮

    try:
        out = run_agent(
            db_path,
            "polisher",  # 复用 polisher capability 绑定（capability_for('polisher')=creative_writing）
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="polisher",
            mock_script=condense_mock,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("polisher")
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "chapter_write.condense degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "polished_prose": current_prose,
            "condense_status": "failed",
            "condense_rounds": next_rounds,
            "condense_error": str(exc),
        }

    if not isinstance(out, dict):
        return {
            "polished_prose": current_prose,
            "condense_status": "failed",
            "condense_rounds": next_rounds,
        }
    new_prose = out.get("polished_text")
    if not isinstance(new_prose, str):
        return {
            "polished_prose": current_prose,
            "condense_status": "failed",
            "condense_rounds": next_rounds,
        }

    # ---- 输出确定性守卫：拒空 / 拒更长 ----
    # 0 字 / 空串显然无效；visible_chars 没缩短（>= 原 visible）意味着 LLM 没做
    # 实压缩或反而扩写 —— 一律拒收，避免坏草灌进 drafts。
    new_visible = visible_chars(new_prose)
    if new_visible == 0 or new_visible >= visible:
        _log.warning(
            "chapter_write.condense rejected output: chapter_id=%s "
            "orig_visible=%d new_visible=%d",
            chapter_id, visible, new_visible,
        )
        return {
            "polished_prose": current_prose,
            "condense_status": "failed_no_shrink",
            "condense_rounds": next_rounds,
            "condense_error": (
                f"condense output visible_chars={new_visible} (orig={visible})"
            ),
        }

    return {
        "polished_prose": new_prose,
        "condense_status": "ok",
        "condense_rounds": next_rounds,
        "condense_changes_summary": out.get("changes_summary") or "",
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
    # V3.7+ P1 字数闭环：word_count 改用权威实测（length_report.visible_chars）。
    # 实证 ch4-6：writer self_report.word_count 自报 3008-3172 vs 实测 4721-5842，
    # 偏差 50%+，不能再采信 self_report。
    length_report = ctx.get("length_report") or {}
    if "visible_chars" in length_report:
        word_count = int(length_report["visible_chars"])
    else:
        # 兜底：length_check 节点未运行 / 被跳过（如 scene_planner 失败降级到 stub
        # 仍会跑 length_check），回到作者自报（仅作 fallback）。
        word_count = int(self_report.get("word_count") or visible_chars(prose))
    prompt_version = writer_output.get("prompt_version") or "writer:v1"

    draft_id = new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        # 字数闭环偏差注记：仅当 condense_rounds >= 2（已穷尽压缩预算）且仍超带
        # 时才写「2 轮后仍超带」注记（smart 审查 P2：避免 round=0 / mock 缺失
        # 跳过等早返回路径也错误触发此文案）。其他场景（带内 / 单轮已落带）
        # 不写 deviations，避免污染下游 W-LEN 报告。
        condense_rounds_now = int(ctx.get("condense_rounds") or 0)
        if (
            condense_rounds_now >= _MAX_CONDENSE_ROUNDS
            and not bool(ctx.get("length_check_passed"))
            and isinstance(length_report, dict)
            and length_report.get("status") in ("over", "under")
        ):
            dev_entry = {
                "from": "word_count_target",
                "to": (
                    f"实测 {int(length_report.get('visible_chars') or word_count)} 字 "
                    f"(target={int(length_report.get('target') or 0)}, "
                    f"band=[{int(length_report.get('band_low') or 0)},"
                    f"{int(length_report.get('band_high') or 0)}], "
                    f"deviation_pct={float(length_report.get('deviation_pct') or 0.0)}%, "
                    f"status={length_report.get('status')})"
                ),
                "reason": (
                    "字数闭环 condense 2 轮后仍超带，按生产实证 ch4-6 规律"
                    "（writer 原始产出 4721-5842 字 vs 自报 3008-3172，偏差 50%+）"
                    "标记为 report-only 兜底；下游评审 W-LEN 报告应据此评估。"
                ),
                "source": "chapter_write.length_check",
                "round": condense_rounds_now,
                "created_at": now,
            }
            try:
                chap_row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?",
                    (chapter_id,),
                ).fetchone()
                plan_obj: dict[str, Any] = {}
                if chap_row is not None and chap_row["plan_json"]:
                    try:
                        parsed_plan = json.loads(chap_row["plan_json"])
                        if isinstance(parsed_plan, dict):
                            plan_obj = parsed_plan
                    except (TypeError, ValueError):
                        plan_obj = {}
                deviations = plan_obj.get("deviations")
                if not isinstance(deviations, list):
                    deviations = []
                deviations.append(dev_entry)
                plan_obj["deviations"] = deviations
                conn.execute(
                    "UPDATE chapters SET plan_json = ?, updated_at = ? "
                    "WHERE chapter_id = ?",
                    (json.dumps(plan_obj, ensure_ascii=False), now, chapter_id),
                )
            except sqlite3.OperationalError:
                # 缺列 / 缺表：兜底不阻断 save_draft
                pass
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
        # chapters.status 推进/回退：
        # - PLANNED→DRAFTED：首写；
        # - DRAFTED：重跑追加新 draft 版本（人工改稿循环）；
        # - REVIEWED→DRAFTED：批准后改稿（双层评审 SOP 的 smart 二审可能推翻
        #   critic 批准）——新草稿使旧批准失效，回退 DRAFTED 强制重审；
        # 与 domain chapter 服务 _DRAFT_ALLOWED_STATUS={DRAFTED, REVIEWED} 口径一致；
        # COMMITTED/RELEASED 仍硬拒（须先 rollback commit）。
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        status = cur["status"]
        if status == "PLANNED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status == "REVIEWED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status != "DRAFTED":
            raise ValueError(
                f"chapter {chapter_id} status={status} 不允许 write，"
                "仅 PLANNED/DRAFTED/REVIEWED 可写"
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
        WorkflowNode("length_check", "Transform", _length_check_node),
        WorkflowNode("condense", "AI", _condense_node, agent_name="polisher"),
        WorkflowNode("save_draft", "State", _save_draft_node),
    ]


WORKFLOW = {
    "name": "chapter-write",
    "version": "v1",
    "description": (
        "Director Plan → Scene Planner(AI) → Writer prose → Polisher(AI) → "
        "length_check(权威实测+项目字数带) → condense(超带≤2轮,polisher capability) → "
        "drafts table; chapter status PLANNED→DRAFTED; "
        "scene_planner / polisher / condense 失败均降级不阻断 writer"
    ),
    "nodes": _build_nodes(),
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_write.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
