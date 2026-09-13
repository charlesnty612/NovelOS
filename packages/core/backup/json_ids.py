"""Backup 包内 JSON 列的内嵌实体 id 重映射（V3.9 全量检修 F1）。

背景：备份包里 ``story_states.snapshot_json`` / ``state_deltas.payload_json`` /
``plot_events.participants_json`` / ``hooks.who_knows`` 等 TEXT 列内嵌着实体 id
（``char_`` / ``ch_`` / ``cmt_`` …）。行级 id 重映射只改列值，这些内嵌 id 若原样
入库，导入副本的 JSON 仍指向**源项目**命名空间——跨库反查失效、快照集合与 DB 实体
集合漂移（``scripts/check_state_sync.py`` 报 DRIFT）。

职责：
- :data:`JSON_ID_COLUMNS`：导出表 → 「内嵌实体 id 的 JSON TEXT 列」显式清单。
- :func:`remap_json_value`：递归 walker（dict / list / str），str 与 dict 的 **key**
  都按全局旧→新 id 映射精确替换。
- :func:`remap_json_column`：TEXT → 解析 → walk → 以 TEXT 形态返回；不可解析的值
  原样返回（调用方继续走通用列逻辑）。

设计要点：
- **只做精确命中替换**：字符串与旧 id 逐字相等才替换，不做子串 / 前缀猜测。
  id 含随机片段，子串替换会误伤正文（``drafts.content`` 这类散文列因此不入清单）。
- **dict 的 key 也要替换**：快照里 ``world.locations`` / ``world.factions`` /
  ``events`` 是 ``{实体 id: {...}}`` 形态，只改 value 会让 check_state_sync 报 DRIFT。
- **不可解析原样透传**：历史库 / 手工包里的 JSON 列可能是空串或非法 JSON，导入端
  必须容错（与 ``story_state.snapshot._parse_json_column`` 的读侧容错口径一致）。
- **序列化口径与写入端一致**：``json.dumps(..., ensure_ascii=False)``（列是 TEXT，
  中文保持可读，不二次转义）。
- 列清单是显式白名单：新增 JSON 列必须同步登记（``tests/unit/test_backup.py``
  的 schema 覆盖测试会对着迁移后的真实列名看守）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["JSON_ID_COLUMNS", "remap_json_column", "remap_json_value"]


# ---------------------------------------------------------------------------
# 含内嵌实体 id 的 JSON 列清单（表名 → 列名元组）
#
# 口径：导出表（EXPORTED_TABLES）里所有 ``*_json`` 列 + ``who_knows``
# （九张权限表的 JSON 字符串数组，元素为 character_id）。
# 不列入的 JSON 形态列：``aliases``（别名文本，非 id）。
# ---------------------------------------------------------------------------

JSON_ID_COLUMNS: dict[str, tuple[str, ...]] = {
    # L2：仅依赖 projects
    "characters":           ("core_json", "who_knows"),
    "locations":            ("data_json", "who_knows"),
    "factions":             ("data_json", "who_knows"),
    "world_rules":          ("data_json", "who_knows"),
    "hooks":                ("who_knows",),
    "narrative_debts":      ("who_knows",),
    "volumes":              ("terminal_snapshot_json",),
    "chapters":             ("plan_json", "who_knows"),
    "memories":             ("source_ids_json",),
    "story_states":         ("snapshot_json",),

    # L3：依赖 character / chapter
    "character_states":     ("state_json", "who_knows"),
    "scenes":               ("plan_json", "who_knows"),

    # L4：依赖 plot_events / characters / chapters
    "plot_events":          (
        "cause_json",
        "effects_json",
        "participants_json",
        "time_json",
        "who_knows",
    ),
    "timeline_events":      ("who_knows",),
    "relationships":        ("state_json", "who_knows"),
    "state_deltas":         ("payload_json",),
    "commits":              ("validation_json", "author_approval_json"),
    "quality_reports":      ("scores_json", "issues_json", "judge_json"),
}


# ---------------------------------------------------------------------------
# walker
# ---------------------------------------------------------------------------


def remap_json_value(value: Any, id_map: Mapping[str, str]) -> Any:
    """递归重映射已解析 JSON 结构里的实体 id。

    - ``str``：在 ``id_map`` 中命中则替换，否则原值；
    - ``list``：逐元素递归；
    - ``dict``：key 与 value 都递归（key 命中映射同样替换——快照的 id→对象 索引形态）；
    - 其余（int / float / bool / None）：原值返回。
    """
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [remap_json_value(item, id_map) for item in value]
    if isinstance(value, dict):
        return {
            (id_map.get(k, k) if isinstance(k, str) else k): remap_json_value(v, id_map)
            for k, v in value.items()
        }
    return value


def remap_json_column(raw: Any, id_map: Mapping[str, str]) -> tuple[bool, Any]:
    """重映射单个 JSON 列值；返回 ``(是否已按 JSON 处理, 处理后的值)``。

    - ``None`` → ``(False, None)``：NULL 保持 NULL（不落 ``"null"`` 文本）；
    - 空串 / 非法 JSON → ``(False, raw)``：原样透传，由调用方的通用列逻辑接手；
    - dict / list（手工构造的包可能直接给结构化值）→ 走 walk 后 dump 成 TEXT；
    - 合法 JSON 文本 → walk 后 dump 回 TEXT（保持 TEXT 形态入库）。

    第二个返回值为 ``False`` 表示「本函数没处理」，调用方应继续走原有的
    外键映射 / 原值透传分支——避免为了走 JSON 分支而丢掉通用规则。
    """
    if raw is None:
        return False, raw
    if isinstance(raw, (dict, list)):
        return True, json.dumps(remap_json_value(raw, id_map), ensure_ascii=False)
    if not isinstance(raw, str):
        return False, raw
    if not raw.strip():
        return False, raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return False, raw
    return True, json.dumps(remap_json_value(parsed, id_map), ensure_ascii=False)
