"""project_init 工作流（P1）。

将「题材想法」加工为可开写的 Story Bible：
- 读取 brief（genre / logline / 目标平台 / 目标字数等）。
- premise_designer：题材定位、核心卖点、主角雏形。
- world_builder：世界观核心设定、规则、地理/势力骨架。
- character_designer：3-5 个核心角色（含动机、关系）。
- volume_outliner：第一卷卷纲 + 前 N 章章节种子（默认 10 章，可配）。
- persist_all：复用现有 domain service 落库项目、角色、世界实体、卷、章。

若 ctx 中已存在 project_id，则更新该项目并挂载生成内容；否则新建项目。
AI 节点失败时按 chapter_review critic 模式降级（记录 warning、返回降级结构），
不阻断后续节点；persist_all 失败直接抛错。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode
from packages.domain.chapter.models import ChapterCreate
from packages.domain.chapter.service import ChapterService
from packages.domain.character.models import CharacterCreate
from packages.domain.character.service import CharacterService
from packages.domain.plot.models import PLOT_EVENT_TYPES
from packages.domain.plot.service import PlotService
from packages.domain.project.models import ProjectCreate, ProjectUpdate
from packages.domain.project.service import ProjectService
from packages.domain.volume.models import VolumeCreate
from packages.domain.volume.service import VolumeService
from packages.domain.world.service import WorldService

_log = logging.getLogger(__name__)

DEFAULT_CHAPTER_SEED_COUNT = 10


# ---------------------------------------------------------------------------
# Step mode：四关卡分段审阅暂停
# ---------------------------------------------------------------------------
#
# 开启 ``step_mode`` 时，每个 AI 节点在成功产出 ``out`` 后抛 :class:`PauseRequested`，
# 把 draft 一并塞进 payload；前端审阅后通过 ``POST /runs/{run_id}/resume`` 注入
# ``human_input={"revisions": {"<output_key>": <新 dict>}}``，再被 ``_resolve_stage_input``
# 三层 fallback 读取，使人工修订在下游节点与 persist_all 生效。
#
# 关卡顺序与节点顺序一致；stage_index 从 0 起。

STAGE_SPECS: dict[str, dict[str, str]] = {
    "premise":   {"output_key": "premise_output",   "node_id": "premise_designer"},
    "world":     {"output_key": "world_output",     "node_id": "world_builder"},
    "character": {"output_key": "character_output", "node_id": "character_designer"},
    "outline":   {"output_key": "outline_output",   "node_id": "volume_outliner"},
}


def _gate_should_pause(ctx: dict[str, Any], stage: str) -> bool:
    """step_mode 开启时才在该关卡抛 PauseRequested。"""
    return bool(ctx.get("step_mode"))


def _resolve_stage_input(ctx: dict[str, Any], stage: str) -> dict[str, Any]:
    """读取某关卡的"当前应使用的输入"，三层 fallback：

    1. ``ctx.human_input.revisions[output_key]``（人工修订，dict 才采纳）；
    2. ``ctx[output_key]``（上一节点产出 / 恢复后已落盘）；
    3. ``ctx[node_id].__pause_payload__.draft``（checkpoint 中的挂起草稿）。

    都拿不到时回退空 dict。
    """
    spec = STAGE_SPECS.get(stage) or {}
    output_key = spec.get("output_key") or ""
    node_id = spec.get("node_id") or ""

    human_input = ctx.get("human_input") or {}
    if isinstance(human_input, dict):
        revisions = human_input.get("revisions") or {}
        if isinstance(revisions, dict):
            rev = revisions.get(output_key)
            if isinstance(rev, dict):
                return rev

    direct = ctx.get(output_key)
    if isinstance(direct, dict):
        return direct

    node_entry = ctx.get(node_id) or {}
    if isinstance(node_entry, dict):
        payload = node_entry.get("__pause_payload__") or {}
        if isinstance(payload, dict):
            draft = payload.get("draft")
            if isinstance(draft, dict):
                return draft

    return {}


# ---------------------------------------------------------------------------
# Transform: load_brief
# ---------------------------------------------------------------------------


def _load_brief_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Transform：规范化 brief，补默认值。

    期望 ctx["brief"] 为 dict，可含 genre / logline / platform / target_words /
    title / author_notes。``chapter_seed_count`` 可从 brief 或 ctx 顶层取，默认 10。
    """
    brief = ctx.get("brief") or {}
    if not isinstance(brief, dict):
        raise ValueError("ctx['brief'] must be a dict")

    normalized: dict[str, Any] = {
        "genre": str(brief.get("genre") or "").strip(),
        "logline": str(brief.get("logline") or "").strip(),
        "platform": str(brief.get("platform") or "").strip(),
        "target_words": brief.get("target_words") or brief.get("target_word_count"),
        "title": str(brief.get("title") or "").strip(),
        "author_notes": str(brief.get("author_notes") or "").strip(),
    }

    # target_words 尝试整数化
    try:
        if normalized["target_words"] is not None:
            normalized["target_words"] = int(normalized["target_words"])
    except (TypeError, ValueError):
        normalized["target_words"] = None

    seed_count = brief.get("chapter_seed_count")
    if seed_count is None:
        seed_count = ctx.get("chapter_seed_count")
    try:
        seed_count = int(seed_count) if seed_count is not None else DEFAULT_CHAPTER_SEED_COUNT
    except (TypeError, ValueError):
        seed_count = DEFAULT_CHAPTER_SEED_COUNT
    if seed_count < 1:
        seed_count = DEFAULT_CHAPTER_SEED_COUNT

    normalized["chapter_seed_count"] = seed_count
    return {"brief": normalized, "chapter_seed_count": seed_count}


# ---------------------------------------------------------------------------
# AI: premise_designer
# ---------------------------------------------------------------------------


def _premise_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    return {
        "agent": "premise_designer",
        "prompt_version": "premise_designer:v1",
        "brief": {
            "genre": brief.get("genre"),
            "logline": brief.get("logline"),
            "platform": brief.get("platform"),
            "target_words": brief.get("target_words"),
            "title": brief.get("title"),
            "author_notes": brief.get("author_notes"),
        },
    }


def _run_premise_designer(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：题材定位 + 核心卖点 + 主角雏形。"""
    db_path = ctx["db_path"]
    mock_script = (ctx.get("mock_providers") or {}).get("premise_designer")
    try:
        out = run_agent(
            db_path,
            "premise_designer",
            _premise_payload(ctx),
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="premise_designer",
            mock_script=mock_script,
        )
        if not isinstance(out, dict):
            raise ValueError(f"premise_designer output not dict: {type(out).__name__}")
        # 软清洗：保证关键字段存在
        out.setdefault("title", ctx.get("brief", {}).get("title", ""))
        out.setdefault("genre", ctx.get("brief", {}).get("genre", ""))
        out.setdefault("logline", ctx.get("brief", {}).get("logline", ""))
        out.setdefault("positioning", "")
        out.setdefault("selling_points", [])
        out.setdefault("protagonist", {})
        out["_degraded"] = False
        if _gate_should_pause(ctx, "premise"):
            raise PauseRequested({
                "stage": "premise",
                "stage_index": 0,
                "stages_total": 4,
                "degraded": False,
                "draft": out,
            })
        return {"premise_output": out}
    except PauseRequested:
        raise
    except Exception as exc:  # noqa: BLE001 —— 降级模式
        _log.warning("project_init.premise_designer degraded: %s", exc)
        brief = ctx.get("brief") or {}
        return {
            "premise_output": {
                "_degraded": True,
                "title": brief.get("title", ""),
                "genre": brief.get("genre", ""),
                "logline": brief.get("logline", ""),
                "positioning": "",
                "selling_points": [],
                "protagonist": {},
                "error": str(exc),
            }
        }


# ---------------------------------------------------------------------------
# AI: world_builder
# ---------------------------------------------------------------------------


def _world_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    premise = _resolve_stage_input(ctx, "premise")
    protagonist = premise.get("protagonist") or {}
    return {
        "agent": "world_builder",
        "prompt_version": "world_builder:v1",
        "brief": {
            "genre": brief.get("genre"),
            "logline": brief.get("logline"),
            "target_words": brief.get("target_words"),
        },
        "premise": {
            "title": premise.get("title"),
            "positioning": premise.get("positioning"),
            "selling_points": premise.get("selling_points") or [],
            "protagonist": protagonist,
        },
    }


def _run_world_builder(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：世界观生成（核心设定、规则、地理/势力骨架）。"""
    db_path = ctx["db_path"]
    mock_script = (ctx.get("mock_providers") or {}).get("world_builder")
    try:
        out = run_agent(
            db_path,
            "world_builder",
            _world_payload(ctx),
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="world_builder",
            mock_script=mock_script,
        )
        if not isinstance(out, dict):
            raise ValueError(f"world_builder output not dict: {type(out).__name__}")
        out.setdefault("core_premise", "")
        out.setdefault("rules", [])
        out.setdefault("locations", [])
        out.setdefault("factions", [])
        out["_degraded"] = False
        if _gate_should_pause(ctx, "world"):
            raise PauseRequested({
                "stage": "world",
                "stage_index": 1,
                "stages_total": 4,
                "degraded": False,
                "draft": out,
            })
        return {"world_output": out}
    except PauseRequested:
        raise
    except Exception as exc:  # noqa: BLE001 —— 降级模式
        _log.warning("project_init.world_builder degraded: %s", exc)
        return {
            "world_output": {
                "_degraded": True,
                "core_premise": "",
                "rules": [],
                "locations": [],
                "factions": [],
                "error": str(exc),
            }
        }


# ---------------------------------------------------------------------------
# AI: character_designer
# ---------------------------------------------------------------------------


def _character_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    premise = _resolve_stage_input(ctx, "premise")
    world = _resolve_stage_input(ctx, "world")
    return {
        "agent": "character_designer",
        "prompt_version": "character_designer:v1",
        "brief": {
            "genre": brief.get("genre"),
            "logline": brief.get("logline"),
        },
        "premise": {
            "title": premise.get("title"),
            "protagonist": premise.get("protagonist") or {},
        },
        "world": {
            "core_premise": world.get("core_premise", ""),
            "rules": world.get("rules") or [],
            "locations": [loc.get("name", "") for loc in world.get("locations") or []],
            "factions": [fac.get("name", "") for fac in world.get("factions") or []],
        },
    }


def _run_character_designer(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：核心角色 3-5 个（含动机、关系）。"""
    db_path = ctx["db_path"]
    mock_script = (ctx.get("mock_providers") or {}).get("character_designer")
    try:
        out = run_agent(
            db_path,
            "character_designer",
            _character_payload(ctx),
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="character_designer",
            mock_script=mock_script,
        )
        if not isinstance(out, dict):
            raise ValueError(f"character_designer output not dict: {type(out).__name__}")
        out.setdefault("characters", [])
        out["_degraded"] = False
        if _gate_should_pause(ctx, "character"):
            raise PauseRequested({
                "stage": "character",
                "stage_index": 2,
                "stages_total": 4,
                "degraded": False,
                "draft": out,
            })
        return {"character_output": out}
    except PauseRequested:
        raise
    except Exception as exc:  # noqa: BLE001 —— 降级模式
        _log.warning("project_init.character_designer degraded: %s", exc)
        return {
            "character_output": {
                "_degraded": True,
                "characters": [],
                "error": str(exc),
            }
        }


# ---------------------------------------------------------------------------
# AI: volume_outliner
# ---------------------------------------------------------------------------


def _outline_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    premise = _resolve_stage_input(ctx, "premise")
    world = _resolve_stage_input(ctx, "world")
    character = _resolve_stage_input(ctx, "character")
    return {
        "agent": "volume_outliner",
        "prompt_version": "volume_outliner:v1",
        "brief": {
            "genre": brief.get("genre"),
            "logline": brief.get("logline"),
            "target_words": brief.get("target_words"),
            "chapter_seed_count": brief.get("chapter_seed_count", DEFAULT_CHAPTER_SEED_COUNT),
        },
        "premise": {
            "title": premise.get("title"),
            "positioning": premise.get("positioning"),
            "protagonist": premise.get("protagonist") or {},
        },
        "world": {
            "core_premise": world.get("core_premise", ""),
            "rules": world.get("rules") or [],
            "locations": world.get("locations") or [],
            "factions": world.get("factions") or [],
        },
        "characters": character.get("characters") or [],
    }


def _run_volume_outliner(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：第一卷卷纲 + 前 N 章章节种子。"""
    db_path = ctx["db_path"]
    mock_script = (ctx.get("mock_providers") or {}).get("volume_outliner")
    seed_count = ctx.get("chapter_seed_count", DEFAULT_CHAPTER_SEED_COUNT)
    try:
        out = run_agent(
            db_path,
            "volume_outliner",
            _outline_payload(ctx),
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="volume_outliner",
            mock_script=mock_script,
        )
        if not isinstance(out, dict):
            raise ValueError(f"volume_outliner output not dict: {type(out).__name__}")
        out.setdefault("volume", {"number": 1, "title": "", "arc_summary": ""})
        out.setdefault("chapter_seeds", [])
        out["_degraded"] = False
        if _gate_should_pause(ctx, "outline"):
            raise PauseRequested({
                "stage": "outline",
                "stage_index": 3,
                "stages_total": 4,
                "degraded": False,
                "draft": out,
            })
        return {"outline_output": out}
    except PauseRequested:
        raise
    except Exception as exc:  # noqa: BLE001 —— 降级模式
        _log.warning("project_init.volume_outliner degraded: %s", exc)
        return {
            "outline_output": {
                "_degraded": True,
                "volume": {"number": 1, "title": "", "arc_summary": ""},
                "chapter_seeds": _fallback_chapter_seeds(seed_count),
                "error": str(exc),
            }
        }


def _fallback_chapter_seeds(count: int) -> list[dict[str, Any]]:
    return [
        {
            "number": i + 1,
            "title": f"第{i + 1}章",
            "role": "setup",
            "one_sentence": "",
            "expected_word_count": 2200,
            "key_beats": [],
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# State: persist_all
# ---------------------------------------------------------------------------


def _persist_all_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：复用现有 domain service 落库。

    - project：存在 project_id 则更新；否则创建。
    - characters：写入 characters 表（含 state v1）。
    - world：写入 locations / factions / world_rules。
    - volume：创建第一卷。
    - chapters：按 chapter_seeds 创建章节（PLANNED）。
    - plot_events（可选）：把 volume 的 arc_summary 写成一条 type=other 的 plot_event。

    任何 service 异常直接上抛，不降级。
    """
    db_path = ctx["db_path"]
    brief = ctx.get("brief") or {}
    premise = _resolve_stage_input(ctx, "premise")
    world = _resolve_stage_input(ctx, "world")
    character = _resolve_stage_input(ctx, "character")
    outline = _resolve_stage_input(ctx, "outline")

    project_id = ctx.get("project_id")
    project_svc = ProjectService(db_path)

    # 1) project
    title = (premise.get("title") or brief.get("title") or "未命名项目").strip()
    if not title:
        title = "未命名项目"
    genre = (premise.get("genre") or brief.get("genre") or "").strip()
    logline = (premise.get("logline") or brief.get("logline") or "").strip()
    target_words = premise.get("target_words") or brief.get("target_words")

    premise_text = _build_premise_text(premise, brief)

    if project_id:
        existing = project_svc.get(project_id)
        if existing is None:
            raise ValueError(f"project {project_id!r} not found")
        project = project_svc.update(
            project_id,
            ProjectUpdate(
                name=title or None,
                premise=premise_text or None,
                genre=genre or None,
                target_words=target_words,
            ),
        )
    else:
        project = project_svc.create(
            ProjectCreate(
                name=title,
                premise=premise_text,
                genre=genre or None,
                target_words=target_words,
            )
        )
        project_id = project["project_id"]

    # 2) characters
    character_ids: list[str] = []
    for char in _normalize_characters(character.get("characters") or []):
        created = CharacterService(db_path).create(
            project_id,
            CharacterCreate(
                name=char["name"],
                role=char.get("role") or "supporting",
                core_json=char.get("core_json") or {},
                visibility="PUBLIC",
            ),
        )
        character_ids.append(created["character_id"])

    # 3) world entities
    world_svc = WorldService(db_path)
    location_ids: list[str] = []
    for loc in _normalize_locations(world.get("locations") or []):
        ent = world_svc.create_location(
            project_id=project_id,
            name=loc["name"],
            statement=loc.get("statement", ""),
            data=loc.get("data") or {},
            visibility="PUBLIC",
        )
        location_ids.append(ent.id)

    faction_ids: list[str] = []
    for fac in _normalize_factions(world.get("factions") or []):
        ent = world_svc.create_faction(
            project_id=project_id,
            name=fac["name"],
            statement=fac.get("statement", ""),
            data=fac.get("data") or {},
            visibility="VISIBLE",
        )
        faction_ids.append(ent.id)

    rule_ids: list[str] = []
    for rule in _normalize_rules(world.get("rules") or []):
        ent = world_svc.create_world_rule(
            project_id=project_id,
            name=rule["name"],
            statement=rule.get("statement", ""),
            data=rule.get("data") or {},
            visibility="PUBLIC",
        )
        rule_ids.append(ent.id)

    # 4) volume
    volume_raw = outline.get("volume") or {"number": 1, "title": None, "arc_summary": ""}
    volume = VolumeService(db_path).create(
        project_id,
        VolumeCreate(
            number=int(volume_raw.get("number") or 1),
            title=volume_raw.get("title"),
        ),
    )
    volume_id = volume["volume_id"]

    # 5) chapters
    chapter_svc = ChapterService(db_path)
    chapter_ids: list[str] = []
    for seed in _normalize_chapter_seeds(outline.get("chapter_seeds") or [], ctx.get("chapter_seed_count", DEFAULT_CHAPTER_SEED_COUNT)):
        plan_payload = {
            "chapter_goal": seed.get("one_sentence", ""),
            "expected_role": seed.get("role", "setup"),
            "key_beats": seed.get("key_beats") or [],
            "core_conflict": "",
            "turning_point": "",
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "",
            "schema_version": "director-plan.v1",
            "prompt_version": "volume_outliner:v1",
        }
        created = chapter_svc.create(
            project_id,
            ChapterCreate(
                number=int(seed["number"]),
                title=seed.get("title"),
                plan_json=plan_payload,
            ),
        )
        # 把章节挂到 volume
        VolumeService(db_path).assign_chapter(volume_id, created["chapter_id"])
        chapter_ids.append(created["chapter_id"])

    # 6) 把卷纲摘要记录为 plot_event（类型 other），便于 timeline / 大纲视图
    arc_summary = (volume_raw.get("arc_summary") or "").strip()
    event_id: str | None = None
    if arc_summary:
        try:
            ev = PlotService(db_path).create_event(
                project_id=project_id,
                type="other",
                cause=[],
                effects=[],
                participants=[],
                time={"timeline_day": 1, "in_story_date": None},
                status="planned",
                visibility="RESTRICTED",
            )
            event_id = ev.id
        except Exception:  # noqa: BLE001 —— plot_event 失败不阻断主流程
            _log.warning("project_init persist_all: plot_event creation failed", exc_info=True)

    return {
        "project_id": project_id,
        "volume_id": volume_id,
        "character_ids": character_ids,
        "location_ids": location_ids,
        "faction_ids": faction_ids,
        "rule_ids": rule_ids,
        "chapter_ids": chapter_ids,
        "event_id": event_id,
        "persisted": True,
    }


def _build_premise_text(premise: dict[str, Any], brief: dict[str, Any]) -> str:
    parts: list[str] = []
    if premise.get("positioning"):
        parts.append(f"定位：{premise['positioning']}")
    if premise.get("selling_points"):
        parts.append("卖点：" + " / ".join(str(x) for x in premise["selling_points"]))
    if brief.get("logline"):
        parts.append(f"一句话：{brief['logline']}")
    return "\n".join(parts)


def _normalize_characters(chars: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in chars:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        core = c.get("core_json") or {}
        if not isinstance(core, dict):
            core = {}
        # 把 prompt 里平铺的 motivation/goal/conflict 收进 core_json
        for key in ("motivation", "goal", "conflict", "distinctive_trait", "relationships"):
            if key in c and key not in core:
                core[key] = c[key]
        out.append({
            "name": name,
            "role": c.get("role") or "supporting",
            "core_json": core,
        })
    return out


def _normalize_locations(locs: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in locs:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "statement": str(item.get("statement") or "").strip(),
            "data": item.get("data") or {},
        })
    return out


def _normalize_factions(facs: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in facs:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "statement": str(item.get("statement") or "").strip(),
            "data": item.get("data") or {},
        })
    return out


def _normalize_rules(rules: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in rules:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "statement": str(item.get("statement") or "").strip(),
            "data": item.get("data") or {},
        })
    return out


def _normalize_chapter_seeds(seeds: list[Any], fallback_count: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(seeds):
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        try:
            number = int(number) if number is not None else (idx + 1)
        except (TypeError, ValueError):
            number = idx + 1
        title = str(item.get("title") or f"第{number}章").strip()
        role = str(item.get("role") or "setup").strip()
        if role not in ("setup", "escalation", "climax", "resolution", "transition", "other"):
            role = "setup"
        word_count = item.get("expected_word_count")
        try:
            word_count = int(word_count) if word_count is not None else 2200
        except (TypeError, ValueError):
            word_count = 2200
        out.append({
            "number": number,
            "title": title,
            "role": role,
            "one_sentence": str(item.get("one_sentence") or "").strip(),
            "expected_word_count": word_count,
            "key_beats": item.get("key_beats") or [],
        })
    if not out:
        out = _fallback_chapter_seeds(fallback_count)
    return out


# ---------------------------------------------------------------------------
# Workflow assembly
# ---------------------------------------------------------------------------


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("load_brief", "Transform", _load_brief_node),
        WorkflowNode("premise_designer", "AI", _run_premise_designer, agent_name="premise_designer"),
        WorkflowNode("world_builder", "AI", _run_world_builder, agent_name="world_builder"),
        WorkflowNode("character_designer", "AI", _run_character_designer, agent_name="character_designer"),
        WorkflowNode("volume_outliner", "AI", _run_volume_outliner, agent_name="volume_outliner"),
        WorkflowNode("persist_all", "State", _persist_all_node),
    ]


WORKFLOW = {
    "name": "project-init",
    "version": "v1",
    "description": (
        "load_brief → premise_designer → world_builder → character_designer → "
        "volume_outliner → persist_all；从题材想法生成 Story Bible 并落库；"
        "step_mode=True 时按 4 关卡暂停供人工审阅修订"
    ),
    "nodes": _build_nodes(),
}


__all__ = ["WORKFLOW", "DEFAULT_CHAPTER_SEED_COUNT"]
