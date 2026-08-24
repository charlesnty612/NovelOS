"""Reference 域（拆书 canon / extracts，Sprint 11 上半）。

公共 API：
- :class:`ReferenceService` ——
  ``reference_canons`` 与 ``canon_extracts`` 表 CRUD + 摘要组装。
- :func:`summary_of` ——从 canon row + canon_json 抽取 logline/spine/rhythm 等摘要。

被 :mod:`packages.core.api.routers.reference` 调用，路由层不再直接写 SQL。
"""
from __future__ import annotations

from .service import ReferenceService, summary_of

__all__ = ["ReferenceService", "summary_of"]
