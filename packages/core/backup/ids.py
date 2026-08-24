"""Backup 模块的 ID 重映射辅助（V1.4 Sprint 16 / MVP）。

职责：
- 定义 :data:`TABLE_META`：每张导出表的「主键列名、新主键前缀、外键列清单」。
- :func:`new_pk` 按表名生成新主键（仅对单列字符串主键生效）。
- :func:`new_composite_pk` 处理复合主键（``character_states`` / ``story_states``），
  保持不变，仅生成新 ``project_id`` 与 ``character_id`` / ``commit_id`` 即可。

设计要点：
- 单源真相：TABLE_META 是 id 重映射的唯一规格，``service.py`` 全部按此驱动；
  任何新增表都先在 TABLE_META 注册（顺序按 :data:`EXPORTED_TABLES`）。
- 复合主键的处理：``character_states(character_id, state_version)`` /
  ``story_states(project_id, state_version)`` —— 重映射时前者替换 character_id；
  后者替换 project_id 与 commit_id（state_version 不动，保证时序）。
- 自引用外键（``branches.parent_branch_id`` / ``state_deltas.supersedes``）必须
  在第二轮 UPDATE 中改写，详见 ``service._import_table_rows``。
"""

from __future__ import annotations

from typing import Any

from packages.core.ids import new_id

__all__ = ["TABLE_META", "new_pk", "new_composite_pk"]


# ---------------------------------------------------------------------------
# 表元信息：主键列名 + 新主键前缀 + 外键列清单
#
# 字段语义：
# - pk_col: 主键列名（None 表示复合主键，需走 new_composite_pk 路径）
# - pk_prefix: 新主键前缀（与 new_id() 一致；用于确保 id 命名空间与生成器一致）
# - fk_cols: 本表中需要被重映射的外键列清单（值为重映射表的导出顺序索引号）
#
# 注：``fk_cols`` 不在本文件处理；service.py 会基于通用 "字符串列 → 映射表"
# 规则改写，TABLE_META 只标出"哪些列含 id 引用"即可。
# ---------------------------------------------------------------------------


TABLE_META: dict[str, dict[str, Any]] = {
    # L1：项目根（在 service.py 内单列处理，不通过 _import_table_rows 路径）
    # "projects": 由 service.export_project / import_project 显式处理

    # L2：仅依赖 projects
    "characters":           {"pk_col": "character_id",         "pk_prefix": "char"},
    "locations":            {"pk_col": "location_id",          "pk_prefix": "loc"},
    "factions":             {"pk_col": "faction_id",           "pk_prefix": "fac"},
    "world_rules":          {"pk_col": "world_rule_id",        "pk_prefix": "wrule"},
    "hooks":                {"pk_col": "hook_id",              "pk_prefix": "hook"},
    "narrative_debts":      {"pk_col": "debt_id",              "pk_prefix": "debt"},
    "chapters":             {"pk_col": "chapter_id",           "pk_prefix": "ch"},
    "branches":             {"pk_col": "branch_id",            "pk_prefix": "br"},
    "memories":             {"pk_col": "memory_id",            "pk_prefix": "mem"},
    "reveal_policies":      {"pk_col": "policy_id",            "pk_prefix": "pol"},
    "author_style_samples": {"pk_col": "sample_id",            "pk_prefix": "asty"},
    "chapter_summaries":    {"pk_col": "summary_id",           "pk_prefix": "sum"},
    "story_states":         {"pk_col": None,                   "pk_prefix": None},  # 复合主键

    # L3：依赖 character / chapter
    "character_states":     {"pk_col": None,                   "pk_prefix": None},  # 复合主键
    "scenes":               {"pk_col": "scene_id",             "pk_prefix": "sc"},
    "drafts":               {"pk_col": "draft_id",             "pk_prefix": "dr"},

    # L4：依赖 plot_events / characters / chapters
    "plot_events":          {"pk_col": "event_id",             "pk_prefix": "event"},
    "timeline_events":      {"pk_col": "timeline_event_id",    "pk_prefix": "tle"},
    "relationships":        {"pk_col": "relationship_id",      "pk_prefix": "rel"},
    "state_deltas":         {"pk_col": "delta_id",             "pk_prefix": "dlt"},
    "commits":              {"pk_col": "commit_id",            "pk_prefix": "cmt"},
    "quality_reports":      {"pk_col": "report_id",            "pk_prefix": "qr"},
}


# ---------------------------------------------------------------------------
# 主键生成 helper
# ---------------------------------------------------------------------------


def new_pk(table_name: str) -> str:
    """生成单列主键的新值。

    通过 :data:`TABLE_META` 读取前缀并调 :func:`packages.core.ids.new_id`。

    复合主键表（``character_states`` / ``story_states``）走
    :func:`new_composite_pk`，**不**通过此函数。
    """
    meta = TABLE_META.get(table_name)
    if meta is None:
        raise ValueError(f"unknown table in TABLE_META: {table_name!r}")
    pk_prefix = meta.get("pk_prefix")
    if pk_prefix is None:
        raise ValueError(
            f"table {table_name!r} has composite primary key; "
            "use new_composite_pk instead"
        )
    return new_id(pk_prefix)


def new_composite_pk(table_name: str) -> dict[str, Any]:
    """复合主键占位：返回空 dict，由调用方在 row dict 上保留原有主键字段。

    实际语义：``character_states`` 保留 ``character_id / state_version``；
    ``story_states`` 保留 ``project_id / state_version``。

    本函数仅作为占位接口存在，便于 service.py 用统一路径处理单/复合主键；
    返回的 dict 永远是空的，调用方无需读取。
    """
    return {}