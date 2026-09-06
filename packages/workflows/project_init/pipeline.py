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

部分生成（``ctx["selected_stages"]`` 仅含子集环节）：未选环节的输出由
``_rebuild_stage_from_db`` 从落库数据重建为下游输入，不调 AI、不暂停；
``_persist_all_node`` 检测到重建结果 ``_degraded=True`` 时**不写入占位卷 / 占位
章节 / plot_event**，避免出现空骨架。premise 降级但项目已存在时也不覆盖
projects 行（保留用户原始 premise），仅在新建项目场景按 brief 创建挂载点。

单次 run 级模型档案覆盖：若 ctx 含 ``model_profile_id``（由
``POST /projects/init`` 的 ``model_profile_id`` 字段注入），4 个 AI 节点
``run_agent`` 调用会透传该 ``profile_id`` 给 ModelRouter，覆盖全局
capability_bindings；不影响其他 run。缺省 / None 时维持既有 capability_bindings
/ model_configs 链路，零行为变更。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode
from packages.domain.character.models import CharacterCreate, CharacterUpdate
from packages.domain.character.service import CharacterService
from packages.domain.plot.service import PlotService
from packages.domain.project.models import ProjectCreate, ProjectUpdate
from packages.domain.project.service import ProjectService
from packages.domain.volume.models import VolumeCreate, VolumeUpdate
from packages.domain.volume.service import VolumeService
from packages.domain.world.service import WorldService

_log = logging.getLogger(__name__)

DEFAULT_CHAPTER_SEED_COUNT = 10
DEFAULT_CHAPTER_WORD_COUNT = 3000
MIN_CHAPTER_SEED_COUNT = 10
MAX_CHAPTER_SEED_COUNT = 500
MIN_CHAPTER_WORD_COUNT = 500
MAX_CHAPTER_WORD_COUNT = 20000


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _coerce_chapter_word_count(raw: Any) -> int:
    """把 brief/ctx 的 chapter_word_count 规范为合法 int；非法回退 DEFAULT。"""
    if raw is None:
        return DEFAULT_CHAPTER_WORD_COUNT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CHAPTER_WORD_COUNT
    if v < MIN_CHAPTER_WORD_COUNT or v > MAX_CHAPTER_WORD_COUNT:
        return DEFAULT_CHAPTER_WORD_COUNT
    return v


def _derive_chapter_seed_count(
    target_words: Any, chapter_word_count: int
) -> int:
    """目标字数 / 单章字数 → 章节种子数；clamp 到 [MIN, MAX]；target_words 缺失或非正则回退 MIN。"""
    try:
        tw = int(target_words) if target_words is not None else 0
    except (TypeError, ValueError):
        tw = 0
    if tw <= 0:
        return MIN_CHAPTER_SEED_COUNT
    derived = round(tw / chapter_word_count)
    return _clamp(derived, MIN_CHAPTER_SEED_COUNT, MAX_CHAPTER_SEED_COUNT)


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

    regenerate 场景：当 ``ctx.regenerate_stage == node_id``（当前关正在被重跑），
    跳过第 1 层 revisions——重生成时不应沿用自身旧修订，否则意见无效。
    注：``ctx.regenerate_stage`` 由引擎写入的是 node_id（如 ``world_builder``），
    故此处必须与 ``node_id`` 比较，而非 ``stage``（如 ``world``）。
    """
    spec = STAGE_SPECS.get(stage) or {}
    output_key = spec.get("output_key") or ""
    node_id = spec.get("node_id") or ""

    # regenerate 模式下，重跑当前关时跳过自身 revisions 优先层
    skip_revisions_layer = ctx.get("regenerate_stage") == node_id

    if not skip_revisions_layer:
        human_input = ctx.get("human_input") or {}
        if isinstance(human_input, dict):
            revisions = human_input.get("revisions") or {}
            if isinstance(revisions, dict):
                rev = revisions.get(output_key)
                # 仅在用户真正提供了"非空 dict"时才采纳第 1 层修订；
                # 空 dict 视为"未提供"，回退到 ctx/draft 兜底，避免
                # 静默用空数据落库并触发占位章（参见
                # docs/testing/audit-project-init-frontend-20260829.md §一.3）。
                if isinstance(rev, dict) and rev:
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


def _stage_selected(ctx: dict[str, Any], stage: str) -> bool:
    """判定当前关卡是否需要真正跑 AI 节点。

    ``ctx["selected_stages"]`` 不存在时默认全选（保持与既有行为一致）；
    存在时仅当 ``stage`` 在白名单内才返回 True。
    """
    selected = ctx.get("selected_stages")
    if selected is None:
        return True
    if not isinstance(selected, (list, tuple)):
        return True
    return stage in set(selected)


def _rebuild_premise_from_db(db_path: str, project_id: str | None) -> dict[str, Any]:
    """从 projects 表重建 premise_output。project_id 缺失或无行时返回降级空结构。

    兼容历史 `projects.premise` 已存为「定位：…卖点：…一句话：…」复合文本的情况：
    从中尝试提取最后一段「一句话：」之后的内容作为 `logline`，供下游 AI 输入更干净。
    `positioning` 字段保留复合文本原始形态（项目 update 主修已保证不会回写）。
    """
    base: dict[str, Any] = {
        "title": "",
        "genre": "",
        "logline": "",
        "positioning": "",
        "selling_points": [],
        "protagonist": {},
        "_degraded": True,
    }
    if not project_id:
        return base
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name, genre, premise, target_words FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return base
    premise_text = row["premise"] or ""
    # 从复合文本提取最后一段「一句话：」之后的内容作为 logline。
    # 无匹配时回退空字符串（与既有行为一致）。
    logline = ""
    marker = "一句话："
    idx = premise_text.rfind(marker)
    if idx != -1:
        logline = premise_text[idx + len(marker):].strip()
    return {
        "title": row["name"] or "",
        "genre": row["genre"] or "",
        "logline": logline,
        "positioning": premise_text,
        "selling_points": [],
        "protagonist": {},
        "target_words": row["target_words"],
        "_degraded": False,
    }


def _rebuild_world_from_db(db_path: str, project_id: str | None) -> dict[str, Any]:
    """从 locations/factions/world_rules 三表重建 world_output。"""
    empty: dict[str, Any] = {
        "core_premise": "",
        "rules": [],
        "locations": [],
        "factions": [],
        "_degraded": True,
    }
    if not project_id:
        return empty
    conn = get_connection(db_path)
    try:
        loc_rows = conn.execute(
            "SELECT name, statement, data_json FROM locations WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        fac_rows = conn.execute(
            "SELECT name, statement, data_json FROM factions WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        rule_rows = conn.execute(
            "SELECT name, statement, data_json FROM world_rules WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    def _row_to_item(row: Any) -> dict[str, Any]:
        data_raw = row["data_json"]
        data: Any = {}
        if data_raw:
            try:
                data = json.loads(data_raw)
            except (TypeError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        return {
            "name": row["name"] or "",
            "statement": row["statement"] or "",
            "data": data,
        }

    locations = [_row_to_item(r) for r in loc_rows]
    factions = [_row_to_item(r) for r in fac_rows]
    rules = [_row_to_item(r) for r in rule_rows]
    has_any = bool(locations or factions or rules)
    return {
        "core_premise": "",
        "rules": rules,
        "locations": locations,
        "factions": factions,
        "_degraded": not has_any,
    }


def _rebuild_character_from_db(db_path: str, project_id: str | None) -> dict[str, Any]:
    """从 characters 表重建 character_output。"""
    empty: dict[str, Any] = {"characters": [], "_degraded": True}
    if not project_id:
        return empty
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name, role, core_json FROM characters WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    chars: list[dict[str, Any]] = []
    for row in rows:
        core_raw = row["core_json"]
        core: Any = {}
        if core_raw:
            try:
                core = json.loads(core_raw)
            except (TypeError, ValueError):
                core = {}
        if not isinstance(core, dict):
            core = {}
        chars.append({
            "name": row["name"] or "",
            "role": row["role"] or "supporting",
            "core_json": core,
        })
    return {
        "characters": chars,
        "_degraded": not chars,
    }


def _rebuild_outline_from_db(
    db_path: str,
    project_id: str | None,
    fallback_seed_count: int,
    chapter_word_count: int = DEFAULT_CHAPTER_WORD_COUNT,
) -> dict[str, Any]:
    """从 volumes + chapters 表重建 outline_output。"""
    empty: dict[str, Any] = {
        "volume": {"number": 1, "title": "", "arc_summary": ""},
        "chapter_seeds": [],
        "_degraded": True,
    }
    if not project_id:
        return empty
    conn = get_connection(db_path)
    try:
        vol_row = conn.execute(
            "SELECT volume_id, number, title FROM volumes WHERE project_id = ? ORDER BY number ASC LIMIT 1",
            (project_id,),
        ).fetchone()
        chap_rows = conn.execute(
            "SELECT number, title, plan_json FROM chapters WHERE project_id = ? ORDER BY number ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    if vol_row is None and not chap_rows:
        return empty

    volume: dict[str, Any] = {
        "number": int(vol_row["number"]) if vol_row is not None else 1,
        "title": vol_row["title"] if vol_row is not None and vol_row["title"] else "",
        "arc_summary": "",
    }

    seeds: list[dict[str, Any]] = []
    for row in chap_rows:
        plan: Any = {}
        if row["plan_json"]:
            try:
                plan = json.loads(row["plan_json"])
            except (TypeError, ValueError):
                plan = {}
        if not isinstance(plan, dict):
            plan = {}
        seeds.append({
            "number": int(row["number"]),
            "title": row["title"] or "",
            "role": str(plan.get("expected_role") or "setup"),
            "one_sentence": str(plan.get("chapter_goal") or ""),
            "expected_word_count": int(
                plan.get("expected_word_count") or chapter_word_count
            ),
            "key_beats": plan.get("key_beats") or [],
        })

    if not seeds:
        seeds = _fallback_chapter_seeds(fallback_seed_count, chapter_word_count)

    return {
        "volume": volume,
        "chapter_seeds": seeds,
        "_degraded": False,
    }


def _rebuild_stage_from_db(
    ctx: dict[str, Any], stage: str
) -> dict[str, Any]:
    """从落库数据重建某环节输出，结构与 AI 节点产出同构（含 `_degraded` 字段）。"""
    db_path = ctx.get("db_path") or ""
    project_id = ctx.get("project_id")
    if not db_path:
        return {"_degraded": True}
    if stage == "premise":
        return _rebuild_premise_from_db(db_path, project_id)
    if stage == "world":
        return _rebuild_world_from_db(db_path, project_id)
    if stage == "character":
        return _rebuild_character_from_db(db_path, project_id)
    if stage == "outline":
        return _rebuild_outline_from_db(
            db_path,
            project_id,
            int(ctx.get("chapter_seed_count") or DEFAULT_CHAPTER_SEED_COUNT),
            chapter_word_count=int(
                ctx.get("chapter_word_count") or DEFAULT_CHAPTER_WORD_COUNT
            ),
        )
    return {"_degraded": True}


def _regenerate_note(ctx: dict[str, Any]) -> str:
    """读取重生成意见（trim 后的字符串）。无意见返回 ``""``。

    供各 AI 节点的 payload 函数透传到 prompt/agent。
    """
    human_input = ctx.get("human_input") or {}
    if not isinstance(human_input, dict):
        return ""
    raw = human_input.get("regenerate_note")
    if not isinstance(raw, str):
        return ""
    return raw.strip()


# ---------------------------------------------------------------------------
# Transform: load_brief
# ---------------------------------------------------------------------------


def _load_brief_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Transform：规范化 brief，补默认值。

    期望 ctx["brief"] 为 dict，可含 genre / logline / platform / target_words /
    title / author_notes。``chapter_seed_count`` 可从 brief 或 ctx 顶层取，
    默认 10；用户未显式提供时按 target_words / chapter_word_count 推导并
    clamp 到 [10, 500]。``chapter_word_count`` 默认 3000，可被 brief / ctx
    覆盖；仅在 [500, 20000] 区间内才采纳。
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

    # chapter_word_count：brief 优先，回退到 ctx 顶层；非法或越界回退 DEFAULT。
    raw_cwc = brief.get("chapter_word_count")
    if raw_cwc is None:
        raw_cwc = ctx.get("chapter_word_count")
    chapter_word_count = _coerce_chapter_word_count(raw_cwc)

    # chapter_seed_count：用户显式传了（brief 或 ctx 都有视为显式），尊重用户值；
    # 否则按 target_words / chapter_word_count 推导并 clamp 到 [10, 500]。
    raw_seed = brief.get("chapter_seed_count")
    user_explicit_seed = raw_seed is not None
    if raw_seed is None:
        raw_seed = ctx.get("chapter_seed_count")
        if raw_seed is not None:
            user_explicit_seed = True
    if user_explicit_seed:
        try:
            seed_count = int(raw_seed)
        except (TypeError, ValueError):
            seed_count = DEFAULT_CHAPTER_SEED_COUNT
        if seed_count < 1:
            seed_count = DEFAULT_CHAPTER_SEED_COUNT
    else:
        seed_count = _derive_chapter_seed_count(
            normalized["target_words"], chapter_word_count
        )

    normalized["chapter_word_count"] = chapter_word_count
    normalized["chapter_seed_count"] = seed_count
    return {
        "brief": normalized,
        "chapter_seed_count": seed_count,
        "chapter_word_count": chapter_word_count,
    }


# ---------------------------------------------------------------------------
# AI: premise_designer
# ---------------------------------------------------------------------------


def _premise_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    payload: dict[str, Any] = {
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
    note = _regenerate_note(ctx)
    if note:
        payload["regenerate_note"] = note
    return payload


def _run_premise_designer(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：题材定位 + 核心卖点 + 主角雏形。"""
    if not _stage_selected(ctx, "premise"):
        # 未选环节：跳过 AI 调用，从落库重建为下游 AI 的输入；不抛 PauseRequested。
        return {"premise_output": _rebuild_stage_from_db(ctx, "premise")}
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
            profile_id=ctx.get("model_profile_id") or None,
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
    payload: dict[str, Any] = {
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
    note = _regenerate_note(ctx)
    if note:
        payload["regenerate_note"] = note
    return payload


def _run_world_builder(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：世界观生成（核心设定、规则、地理/势力骨架）。"""
    if not _stage_selected(ctx, "world"):
        return {"world_output": _rebuild_stage_from_db(ctx, "world")}
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
            profile_id=ctx.get("model_profile_id") or None,
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
    payload: dict[str, Any] = {
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
    note = _regenerate_note(ctx)
    if note:
        payload["regenerate_note"] = note
    return payload


def _run_character_designer(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：核心角色 3-5 个（含动机、关系）。"""
    if not _stage_selected(ctx, "character"):
        return {"character_output": _rebuild_stage_from_db(ctx, "character")}
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
            profile_id=ctx.get("model_profile_id") or None,
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
    payload: dict[str, Any] = {
        "agent": "volume_outliner",
        "prompt_version": "volume_outliner:v1",
        "brief": {
            "genre": brief.get("genre"),
            "logline": brief.get("logline"),
            "target_words": brief.get("target_words"),
            "chapter_seed_count": brief.get("chapter_seed_count", DEFAULT_CHAPTER_SEED_COUNT),
            "chapter_word_count": brief.get(
                "chapter_word_count", DEFAULT_CHAPTER_WORD_COUNT
            ),
            "author_notes": brief.get("author_notes"),
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
    note = _regenerate_note(ctx)
    if note:
        payload["regenerate_note"] = note
    return payload


def _run_volume_outliner(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：第一卷卷纲 + 前 N 章章节种子。"""
    if not _stage_selected(ctx, "outline"):
        return {"outline_output": _rebuild_stage_from_db(ctx, "outline")}
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
            profile_id=ctx.get("model_profile_id") or None,
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
                "chapter_seeds": _fallback_chapter_seeds(
                    seed_count,
                    int(
                        ctx.get("chapter_word_count")
                        or DEFAULT_CHAPTER_WORD_COUNT
                    ),
                ),
                "error": str(exc),
            }
        }


def _fallback_chapter_seeds(
    count: int, chapter_word_count: int = DEFAULT_CHAPTER_WORD_COUNT
) -> list[dict[str, Any]]:
    return [
        {
            "number": i + 1,
            "title": f"第{i + 1}章",
            "role": "setup",
            "one_sentence": "",
            "expected_word_count": chapter_word_count,
            "key_beats": [],
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# State: persist_all
# ---------------------------------------------------------------------------


def _upsert_volume(db_path: Any, project_id: str, payload: VolumeCreate) -> dict:
    """按 (project_id, number) upsert 卷。

    - 已存在 → 更新 title（保持 volume_id 不变，避免下游引用断裂）。
    - 不存在 → 走 VolumeService.create 路径。

    修复「只重跑卷纲」时新生成的 outline 撞 UNIQUE(project_id, number) 唯一约束。
    """
    svc = VolumeService(db_path)
    conn = get_connection(str(db_path))
    try:
        row = conn.execute(
            "SELECT volume_id, title, status FROM volumes WHERE project_id = ? AND number = ?",
            (project_id, payload.number),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        created = svc.create(project_id, payload)
        return created
    volume_id = row["volume_id"]
    # title 变化才更新，避免无谓写盘
    if row["title"] != payload.title:
        svc.update(volume_id, VolumeUpdate(title=payload.title))
    return svc.get(volume_id) or {
        "volume_id": volume_id,
        "project_id": project_id,
        "number": payload.number,
        "title": payload.title,
        "status": row["status"],
    }


def _delete_chapters_for_volume(db_path: Any, volume_id: str) -> int:
    """删除指定 volume 下已挂的所有 chapters（精确到 volume，不误删其他卷）。

    - ChapterService 没有 list_by_volume / delete_by_volume；走直接 SQL。
    - 删除顺序：先删关联 drafts（FK 在 0001_init.sql 中是 ON DELETE CASCADE，
      这里仍然显式写是为了兼容可能的旧库迁移顺序）；再删 chapters。
    - 返回删除的章节数。
    """
    conn = get_connection(str(db_path))
    try:
        # 收集待删 chapter_id，避免 DELETE...IN 误读 SQL 兼容性问题
        rows = conn.execute(
            "SELECT chapter_id FROM chapters WHERE volume_id = ?",
            (volume_id,),
        ).fetchall()
        chapter_ids = [r["chapter_id"] for r in rows]
        if not chapter_ids:
            return 0
        # drafts 表对 chapter_id 有 FK；先清掉子记录再删 chapter 行
        placeholders = ",".join("?" for _ in chapter_ids)
        conn.execute(
            f"DELETE FROM drafts WHERE chapter_id IN ({placeholders})",
            chapter_ids,
        )
        cur = conn.execute(
            f"DELETE FROM chapters WHERE chapter_id IN ({placeholders})",
            chapter_ids,
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def _persist_all_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：复用现有 domain service 落库。

    - project：存在 project_id 则更新；否则创建。
    - characters：写入 characters 表（含 state v1）。
    - world：写入 locations / factions / world_rules。
    - volume：创建第一卷。
    - chapters：按 chapter_seeds 创建章节（PLANNED）。
    - plot_events（可选）：把 volume 的 arc_summary 写成一条 type=other 的 plot_event。

    任何 service 异常直接上抛，不降级。

    部分生成语义：当 ``outline._degraded=True``（未选 outline 环节、由
    ``_rebuild_stage_from_db`` 从空库重建）时，跳过 volume / chapters /
    plot_event 写入，相应 ID 返回 ``None`` / ``[]``，并在返回结构中标记
    ``outline_skipped=True``，避免落占位空卷 / 占位章节。premise 降级但
    project_id 已存在时同样跳过 project update（保留用户已写入的 premise），
    仅在新建项目时按 brief 创建挂载点。
    """
    db_path = ctx["db_path"]
    brief = ctx.get("brief") or {}
    premise = _resolve_stage_input(ctx, "premise")
    world = _resolve_stage_input(ctx, "world")
    character = _resolve_stage_input(ctx, "character")
    outline = _resolve_stage_input(ctx, "outline")

    project_id = ctx.get("project_id")
    project_svc = ProjectService(db_path)

    # 0) 部分生成降级标记：未选环节的输出由 _rebuild_stage_from_db 重建，
    #    _degraded=True 表示数据库里也没有可重建内容，绝不能落占位数据。
    premise_degraded = bool(premise.get("_degraded"))
    outline_degraded = bool(outline.get("_degraded"))

    # 1) project
    title = (premise.get("title") or brief.get("title") or "未命名项目").strip()
    if not title:
        title = "未命名项目"
    genre = (premise.get("genre") or brief.get("genre") or "").strip()
    target_words = premise.get("target_words") or brief.get("target_words")

    premise_text = _build_premise_text(premise, brief)

    if project_id:
        existing = project_svc.get(project_id)
        if existing is None:
            raise ValueError(f"project {project_id!r} not found")
        # premise 环节本次未被选中（selected_stages 不含 premise）：
        # DB 重建出的 premise 是「定位：…卖点：…一句话：…」复合文本，
        # 若再走 _build_premise_text 落库，每跑一次叠一层「定位：」+「一句话：」，
        # 形成套娃污染。仅在 premise 环节本次真跑了（_stage_selected True）或
        # premise 是合法 AI/重试产出（_degraded=False）时才允许回写。
        # 退化（_degraded=True）也照跳，覆盖"AI 节点异常但 project 已存在"
        # 的防御分支（保留用户原始 premise）。
        premise_stage_selected = _stage_selected(ctx, "premise")
        if premise_stage_selected and not premise_degraded:
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

    # 2) characters —— 按 (project_id, name) 已存在则更新 AI 可管内容字段
    #    （name/role/core_json），保留 id 与 created_at 不变；不存在则新建。
    #    修复前：存在即跳过 → 分段审阅中修订过的 AI 内容重新生成时被
    #    静默丢弃（与头注释"更新该项目并挂载生成内容"语义不符）。
    character_svc = CharacterService(db_path)
    existing_chars_by_name = {
        c["name"]: c for c in character_svc.list_by_project(project_id)
    }
    character_ids: list[str] = []
    for char in _normalize_characters(character.get("characters") or []):
        name = char["name"]
        role = char.get("role") or "supporting"
        core_json = char.get("core_json") or {}
        existing = existing_chars_by_name.get(name)
        if existing is not None:
            updated = character_svc.update(
                existing["character_id"],
                CharacterUpdate(name=name, role=role, core_json=core_json),
            )
            assert updated is not None
            character_ids.append(updated["character_id"])
            continue
        created = character_svc.create(
            project_id,
            CharacterCreate(
                name=name,
                role=role,
                core_json=core_json,
                visibility="PUBLIC",
            ),
        )
        existing_chars_by_name[name] = created
        character_ids.append(created["character_id"])

    # 3) world entities —— 同 characters：存在则更新 statement / data / name，
    #    不重建行（保留 id / created_at）。
    world_svc = WorldService(db_path)
    existing_locs_by_name = {
        e.name: e for e in world_svc.list_locations(project_id)
    }
    location_ids: list[str] = []
    for loc in _normalize_locations(world.get("locations") or []):
        name = loc["name"]
        statement = loc.get("statement", "")
        data = loc.get("data") or {}
        existing = existing_locs_by_name.get(name)
        if existing is not None:
            updated = world_svc.update_location(
                existing.id, name=name, statement=statement, data=data,
            )
            location_ids.append(updated.id)
            continue
        ent = world_svc.create_location(
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility="PUBLIC",
        )
        existing_locs_by_name[name] = ent
        location_ids.append(ent.id)

    existing_facs_by_name = {
        e.name: e for e in world_svc.list_factions(project_id)
    }
    faction_ids: list[str] = []
    for fac in _normalize_factions(world.get("factions") or []):
        name = fac["name"]
        statement = fac.get("statement", "")
        data = fac.get("data") or {}
        existing = existing_facs_by_name.get(name)
        if existing is not None:
            updated = world_svc.update_faction(
                existing.id, name=name, statement=statement, data=data,
            )
            faction_ids.append(updated.id)
            continue
        ent = world_svc.create_faction(
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility="VISIBLE",
        )
        existing_facs_by_name[name] = ent
        faction_ids.append(ent.id)

    existing_rules_by_name = {
        e.name: e for e in world_svc.list_world_rules(project_id)
    }
    rule_ids: list[str] = []
    for rule in _normalize_rules(world.get("rules") or []):
        name = rule["name"]
        statement = rule.get("statement", "")
        data = rule.get("data") or {}
        existing = existing_rules_by_name.get(name)
        if existing is not None:
            updated = world_svc.update_world_rule(
                existing.id, name=name, statement=statement, data=data,
            )
            rule_ids.append(updated.id)
            continue
        ent = world_svc.create_world_rule(
            project_id=project_id,
            name=name,
            statement=statement,
            data=data,
            visibility="PUBLIC",
        )
        existing_rules_by_name[name] = ent
        rule_ids.append(ent.id)

    # 4) volume —— upsert：同 (project, number) 已存在则更新 title，否则新建。
    #    解决「只重跑卷纲」时旧空壳卷造成 UNIQUE 冲突的问题。
    #    outline 降级（未选该环节且 DB 无重建内容）时跳过整个卷 / 章 / plot_event
    #    链路，绝不落占位空卷。
    volume_id: str | None = None
    chapter_ids: list[str] = []
    event_id: str | None = None
    outline_skipped = False
    if outline_degraded:
        outline_skipped = True
    else:
        volume_raw = outline.get("volume") or {"number": 1, "title": None, "arc_summary": ""}
        volume = _upsert_volume(
            db_path,
            project_id,
            VolumeCreate(
                number=int(volume_raw.get("number") or 1),
                title=volume_raw.get("title"),
            ),
        )
        volume_id = volume["volume_id"]

        # 5) chapters —— 重建序列必须包进单事务。
        #    修复前：先 DELETE + commit，再多次 chapter create + commit，再
        #    assign_chapter + commit——中途失败时旧章已被删、新章半写入，
        #    数据丢失。修复后：单连接 BEGIN→DELETE 旧 drafts/chapters→INSERT
        #    新 chapters（直接挂 volume_id）→COMMIT；任何环节异常触发
        #    ROLLBACK，旧章与 drafts 完整保留，新章一行不入库。
        normalized_chapter_seeds = _normalize_chapter_seeds(
            outline.get("chapter_seeds") or [],
            ctx.get("chapter_seed_count", DEFAULT_CHAPTER_SEED_COUNT),
            chapter_word_count=int(
                ctx.get("chapter_word_count") or DEFAULT_CHAPTER_WORD_COUNT
            ),
        )
        conn = get_connection(str(db_path))
        try:
            # 收集待删 chapter_id（精确到本 volume）
            old_rows = conn.execute(
                "SELECT chapter_id FROM chapters WHERE volume_id = ?",
                (volume_id,),
            ).fetchall()
            old_chapter_ids = [r["chapter_id"] for r in old_rows]

            if old_chapter_ids:
                placeholders = ",".join("?" for _ in old_chapter_ids)
                # drafts 子记录先清（FK ON DELETE CASCADE 也兜底，显式写兼容迁移顺序）
                conn.execute(
                    f"DELETE FROM drafts WHERE chapter_id IN ({placeholders})",
                    old_chapter_ids,
                )
                conn.execute(
                    f"DELETE FROM chapters WHERE chapter_id IN ({placeholders})",
                    old_chapter_ids,
                )

            # 逐章 INSERT（同连接 → 同一事务；异常会冒泡到下方 except 触发 rollback）
            for seed in normalized_chapter_seeds:
                plan_payload = {
                    "chapter_goal": seed.get("one_sentence", ""),
                    "expected_role": seed.get("role", "setup"),
                    "key_beats": seed.get("key_beats") or [],
                    "expected_word_count": int(
                        seed.get("expected_word_count")
                        or ctx.get("chapter_word_count")
                        or DEFAULT_CHAPTER_WORD_COUNT
                    ),
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
                chapter_id = new_id("ch")
                now = now_iso()
                conn.execute(
                    """
                    INSERT INTO chapters
                        (chapter_id, project_id, number, title, plan_json,
                         status, visibility, who_knows,
                         created_at, updated_at, volume_id)
                    VALUES
                        (:chapter_id, :project_id, :number, :title, :plan_json,
                         :status, :visibility, :who_knows,
                         :created_at, :updated_at, :volume_id)
                    """,
                    {
                        "chapter_id": chapter_id,
                        "project_id": project_id,
                        "number": int(seed["number"]),
                        "title": seed.get("title"),
                        "plan_json": json.dumps(plan_payload, ensure_ascii=False),
                        "status": "PLANNED",
                        "visibility": "VISIBLE",
                        "who_knows": None,
                        "created_at": now,
                        "updated_at": now,
                        "volume_id": volume_id,
                    },
                )
                chapter_ids.append(chapter_id)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 6) 把卷纲摘要记录为 plot_event（类型 other），便于 timeline / 大纲视图
        arc_summary = (volume_raw.get("arc_summary") or "").strip()
        if arc_summary:
            try:
                plot_svc = PlotService(db_path)
                # 防重：同 project 下若已有 type='other' AND status='planned' AND
                # description=arc_summary 的事件，复用其 id（避免重复 init 堆占位）。
                # list_events 的内存比对足够（命中期望 0/1 条；用 PlotService
                # 现有查询而不是再开 db 连接）。
                existing_id: str | None = None
                for _ev in plot_svc.list_events(
                    project_id, type="other", status="planned"
                ):
                    if (_ev.description or "").strip() == arc_summary:
                        existing_id = _ev.id
                        break
                if existing_id is not None:
                    event_id = existing_id
                else:
                    ev = plot_svc.create_event(
                        project_id=project_id,
                        type="other",
                        cause=[],
                        effects=[],
                        participants=[],
                        time={"timeline_day": 1, "in_story_date": None},
                        status="planned",
                        visibility="RESTRICTED",
                        description=arc_summary,
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
        "outline_skipped": outline_skipped,
        "persisted": True,
    }


def _build_premise_text(premise: dict[str, Any], brief: dict[str, Any]) -> str:
    # 防御：positioning 若以「定位：」开头（任何残余路径把复合文本塞回来）
    # 先剥一层前缀，避免「定位：定位：…」套娃；只剥一层。
    raw_positioning = str(premise.get("positioning") or "")
    if raw_positioning.startswith("定位："):
        raw_positioning = raw_positioning.removeprefix("定位：").lstrip()
    parts: list[str] = []
    if raw_positioning:
        parts.append(f"定位：{raw_positioning}")
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


def _normalize_chapter_seeds(
    seeds: list[Any],
    fallback_count: int,
    chapter_word_count: int = DEFAULT_CHAPTER_WORD_COUNT,
) -> list[dict[str, Any]]:
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
            word_count = int(word_count) if word_count is not None else chapter_word_count
        except (TypeError, ValueError):
            word_count = chapter_word_count
        out.append({
            "number": number,
            "title": title,
            "role": role,
            "one_sentence": str(item.get("one_sentence") or "").strip(),
            "expected_word_count": word_count,
            "key_beats": item.get("key_beats") or [],
        })
    if not out:
        out = _fallback_chapter_seeds(fallback_count, chapter_word_count)
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
