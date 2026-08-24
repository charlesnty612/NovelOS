"""core.backup —— 项目级 JSON 备份 / 恢复（V1.4 Sprint 16 / MVP）。

公共 API：
- :class:`BackupService`（主入口；构造接收 ``db_path``）。
- :data:`BACKUP_FORMAT` / :data:`BACKUP_VERSION`（包格式契约）。
- :data:`EXPORTED_TABLES`（导出 + 导入白名单）。
- :func:`validate_backup`（导入前格式校验）。

详见 :mod:`packages.core.backup.service` 与 :mod:`packages.core.backup.schema`
的 docstring。
"""

from __future__ import annotations

from .ids import TABLE_META
from .schema import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    EXPORTED_TABLES,
    REQUIRED_TOP_KEYS,
    validate_backup,
)
from .service import BackupService

__all__ = [
    "BackupService",
    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "EXPORTED_TABLES",
    "REQUIRED_TOP_KEYS",
    "TABLE_META",
    "validate_backup",
]
