"""Backup 包结构常量与校验器（V1.4 Sprint 16 / MVP）。

职责：
- 定义 ``BACKUP_FORMAT`` / ``BACKUP_VERSION``（包格式契约，导入校验主键）。
- 定义 ``EXPORTED_TABLES`` 元组（导出 + 导入白名单），覆盖所有项目相关业务表。
- :func:`validate_backup` 对外暴露格式校验，坏包直接抛 ValueError。

设计要点：
- 顶层契约只允许 5 个 key：``format / version / exported_at / project / tables``。
  ``metadata`` 与 ``exported_from_project_id`` 为可选增强字段，导入时不强制；
  缺失时回退到合理默认（``exported_at`` 由导入端补；``exported_from_project_id`` 仅做审计）。
- 表白名单是单向闭合的——导入时 ``tables`` 的 key 必须在白名单内，**多出任何表即拒绝**，
  避免误传内部表（如 ``model_configs`` 含 API key）造成信息泄漏。
- 不做 JSON schema 验证（避免引入 jsonschema 等额外依赖）；只做最小化的格式校验。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "REQUIRED_TOP_KEYS",
    "EXPORTED_TABLES",
    "validate_backup",
]


# ---------------------------------------------------------------------------
# 包格式契约（向后兼容的唯一锚点）
# ---------------------------------------------------------------------------

BACKUP_FORMAT = "novelos-backup"

# 当前协议版本。导入端必须严格匹配；未来跨大版本需另起 protocol 字段。
BACKUP_VERSION = 1

# 顶层必须存在的 key（schema.py 校验 + service.py 校验共用）。
REQUIRED_TOP_KEYS = {"format", "version", "exported_at", "project", "tables"}

# 导出表白名单（导入端同样使用）。
#
# 顺序即为重映射顺序（service.py 严格按此顺序执行）；
# 修改顺序或表名都会破坏导入兼容性，请谨慎变更。
#
# 覆盖 22 张表（V1.4 MVP）：
# - 第一波（仅依赖 projects）：characters / locations / factions / world_rules /
#   hooks / narrative_debts / chapters / branches / memories / reveal_policies /
#   author_style_samples / chapter_summaries / story_states
# - 第二波（依赖 character/chapter）：character_states / scenes / drafts
# - 第三波（依赖 plot_events/locations）：plot_events / timeline_events
# - 第四波（依赖 character/chapter/state）：relationships / state_deltas / commits /
#   quality_reports
EXPORTED_TABLES: tuple[str, ...] = (
    "characters",
    "locations",
    "factions",
    "world_rules",
    "hooks",
    "narrative_debts",
    "chapters",
    "branches",
    "memories",
    "reveal_policies",
    "author_style_samples",
    "chapter_summaries",
    "story_states",
    "character_states",
    "scenes",
    "drafts",
    "plot_events",
    "timeline_events",
    "relationships",
    "state_deltas",
    "commits",
    "quality_reports",
)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_backup(data: Any) -> None:
    """对导入包做最小化格式校验。

    不通过即抛 ``ValueError``（错误消息可读，便于 router 转 422 时透出）。

    校验项：
    1. ``data`` 必须是 dict；
    2. 顶层必含 5 个 key；
    3. ``format`` 必须等于 ``BACKUP_FORMAT``；
    4. ``version`` 必须等于 ``BACKUP_VERSION``；
    5. ``project`` 必须是 dict 且含 ``project_id / name / created_at``；
    6. ``tables`` 必须是 dict，所有 key 必须在 :data:`EXPORTED_TABLES` 内；
    7. 每张表的 value 必须是 list（允许空列表）。
    """
    if not isinstance(data, dict):
        raise ValueError("backup payload must be a JSON object")

    missing = REQUIRED_TOP_KEYS - set(data.keys())
    if missing:
        raise ValueError(
            f"backup payload missing required keys: {sorted(missing)}"
        )

    fmt = data.get("format")
    if fmt != BACKUP_FORMAT:
        raise ValueError(
            f"unsupported backup format: {fmt!r} (expected {BACKUP_FORMAT!r})"
        )

    version = data.get("version")
    if version != BACKUP_VERSION:
        raise ValueError(
            f"unsupported backup version: {version!r} (expected {BACKUP_VERSION})"
        )

    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("backup payload 'project' must be a JSON object")
    for required_field in ("project_id", "name", "created_at"):
        if required_field not in project:
            raise ValueError(
                f"backup payload 'project' missing required field: {required_field!r}"
            )

    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("backup payload 'tables' must be a JSON object")

    whitelist = set(EXPORTED_TABLES)
    unknown = sorted(k for k in tables.keys() if k not in whitelist)
    if unknown:
        raise ValueError(
            f"backup payload contains unexpected tables: {unknown}"
        )

    for table_name, rows in tables.items():
        if not isinstance(rows, list):
            raise ValueError(
                f"backup payload table {table_name!r} must be a JSON array"
            )
