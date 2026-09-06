"""相关性裁剪（relevance trim）——按 plan/scene_plan 的 involved 实体对
角色/地点/势力摘要做降级裁剪（拆分自 builders.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import os
from typing import Any


def _resolve_relevance_trim(relevance_trim: bool | None) -> bool:
    """解析 ``relevance_trim`` 参数：显式传值优先，否则读环境变量。

    - ``NOVELOS_CONTEXT_RELEVANCE=off``（大小写不敏感）→ 关闭；
    - 未设置 / 其他值 → 默认开启。
    """
    if relevance_trim is not None:
        return bool(relevance_trim)
    env = (os.environ.get("NOVELOS_CONTEXT_RELEVANCE") or "").strip().lower()
    return env != "off"


def _extract_involved_entities(
    plan_json: dict[str, Any],
    scene_plan: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """从章节 plan + scene_plan 中提取涉及的角色 / 地点标识集合。

    扫描面（与 chapter_write.pipeline 中 scene_plan 构造口径对齐）：
    - ``plan_json.key_beats[*].involved_characters / involved_locations``
    - ``plan_json.character_changes_planned[*].name / character_id``
    - ``scene_plan.characters / location``
    - ``scene_plan.beats[*].involved_characters / involved_locations``

    返回 ``(involved_characters, involved_locations)``，元素为 id 或 name（字符串）。
    """
    chars: set[str] = set()
    locs: set[str] = set()

    if isinstance(plan_json, dict):
        for beat in plan_json.get("key_beats") or []:
            if isinstance(beat, dict):
                for c in beat.get("involved_characters") or []:
                    if isinstance(c, str):
                        chars.add(c.strip())
                for loc_id in beat.get("involved_locations") or []:
                    if isinstance(loc_id, str):
                        locs.add(loc_id.strip())
        for change in plan_json.get("character_changes_planned") or []:
            if isinstance(change, dict):
                name = change.get("name")
                if isinstance(name, str) and name.strip():
                    chars.add(name.strip())
                cid = change.get("character_id")
                if isinstance(cid, str) and cid.strip():
                    chars.add(cid.strip())
            elif isinstance(change, str) and change.strip():
                chars.add(change.strip())

    if isinstance(scene_plan, dict):
        for c in scene_plan.get("characters") or []:
            if isinstance(c, str):
                chars.add(c.strip())
        loc = scene_plan.get("location")
        if isinstance(loc, str) and loc.strip():
            locs.add(loc.strip())
        for beat in scene_plan.get("beats") or []:
            if isinstance(beat, dict):
                for c in beat.get("involved_characters") or []:
                    if isinstance(c, str):
                        chars.add(c.strip())
                for loc_id in beat.get("involved_locations") or []:
                    if isinstance(loc_id, str):
                        locs.add(loc_id.strip())

    return chars, locs


def _is_character_relevant(char: dict[str, Any], involved: set[str]) -> bool:
    """角色是否属于本章核心相关：主角 / always 模式 / id 或 name 命中 involved。"""
    if not isinstance(char, dict):
        return False
    if char.get("role") == "protagonist":
        return True
    if char.get("inject_mode") == "always":
        return True
    cid = char.get("character_id")
    name = char.get("name")
    if isinstance(cid, str) and cid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _is_location_relevant(loc: dict[str, Any], involved: set[str]) -> bool:
    """地点是否属于本章核心相关：always 模式 / id 或 name 命中 involved。"""
    if not isinstance(loc, dict):
        return False
    if loc.get("inject_mode") == "always":
        return True
    lid = loc.get("location_id")
    name = loc.get("name")
    if isinstance(lid, str) and lid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _is_faction_relevant(fac: dict[str, Any], involved: set[str]) -> bool:
    """势力是否属于本章核心相关：always 模式 / id 或 name 命中 involved。"""
    if not isinstance(fac, dict):
        return False
    if fac.get("inject_mode") == "always":
        return True
    fid = fac.get("faction_id")
    name = fac.get("name")
    if isinstance(fid, str) and fid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _summarize_entity_for_relevance(entity: dict[str, Any], *, id_field: str) -> dict[str, Any]:
    """未涉及实体的极简降级：仅保留 id + name + 标记位。"""
    return {
        id_field: entity.get(id_field),
        "name": entity.get("name"),
        "relevance_summary": True,
    }


def _apply_relevance_trim(
    payload: dict[str, Any],
    *,
    relevance_trim: bool,
    plan_json: dict[str, Any],
    scene_plan: dict[str, Any],
) -> dict[str, Any]:
    """按本章 plan/scene 对 writer payload 做相关性裁剪（纯函数）。

    - 主角（role=protagonist）与 ``inject_mode='always'`` 的实体始终完整保留；
    - 其余实体若 id 或 name 命中 ``involved_characters / involved_locations`` 则保留完整；
    - 未命中实体降级为 ``{id, name, relevance_summary: True}``，不直接剔除，便于调用方
      / preview 仍识别到存在；
    - 注入 ``_relevance_trim_enabled`` 与 ``_relevance_trim_stats`` 用于观测。

    注意：本函数会原地修改 ``payload`` 并返回它。
    """
    payload["_relevance_trim_enabled"] = relevance_trim
    if not relevance_trim:
        return payload

    involved_chars, involved_locs = _extract_involved_entities(plan_json, scene_plan)

    chars_in = payload.get("character_state_excerpts") or []
    chars_out: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "characters_full": 0,
        "characters_summary": 0,
        "locations_full": 0,
        "locations_summary": 0,
        "factions_full": 0,
        "factions_summary": 0,
        "involved_characters": sorted(involved_chars),
        "involved_locations": sorted(involved_locs),
    }

    for c in chars_in:
        if not isinstance(c, dict):
            continue
        if _is_character_relevant(c, involved_chars):
            chars_out.append(c)
            stats["characters_full"] += 1
        else:
            chars_out.append(
                _summarize_entity_for_relevance(c, id_field="character_id")
            )
            stats["characters_summary"] += 1

    world_in = payload.get("world_state_excerpts") or {}
    if isinstance(world_in, dict):
        locs_in = world_in.get("locations") or []
        locs_out: list[dict[str, Any]] = []
        for loc in locs_in:
            if not isinstance(loc, dict):
                continue
            if _is_location_relevant(loc, involved_locs):
                locs_out.append(loc)
                stats["locations_full"] += 1
            else:
                locs_out.append(
                    _summarize_entity_for_relevance(loc, id_field="location_id")
                )
                stats["locations_summary"] += 1
        world_in["locations"] = locs_out

        facs_in = world_in.get("active_factions") or []
        facs_out: list[dict[str, Any]] = []
        for fac in facs_in:
            if not isinstance(fac, dict):
                continue
            if _is_faction_relevant(fac, involved_locs):
                facs_out.append(fac)
                stats["factions_full"] += 1
            else:
                facs_out.append(
                    _summarize_entity_for_relevance(fac, id_field="faction_id")
                )
                stats["factions_summary"] += 1
        world_in["active_factions"] = facs_out

        # 感官锚点已经只在完整注入的 location 上生成；relevance_trim 后若某 location
        # 被降级，它的 data_json 为空，不会贡献锚点。这里不需要重新提取。
        payload["world_state_excerpts"] = world_in

    payload["character_state_excerpts"] = chars_out
    payload["_relevance_trim_stats"] = stats
    return payload
