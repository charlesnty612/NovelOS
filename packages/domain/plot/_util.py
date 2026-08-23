"""domain.plot 内部工具（Sprint 1）。

本模块不依赖 ``packages.core.ids``（并行代理 A 正在创建该模块），自建极简实现。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成 ``{prefix}_{uuid4_hex[:12]}`` 形式的 ID。"""
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{uuid4().hex[:12]}"


def now_iso() -> str:
    """返回 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()
