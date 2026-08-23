"""domain.world 内部工具（Sprint 1）。

本模块不依赖 ``packages.core.ids``（并行代理 A 正在创建该模块，为避免竞争，
本包内自建 ``new_id`` 与 ``now_iso`` 的极简实现）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成 ``{prefix}_{uuid4_hex[:12]}`` 形式的 ID。

    例: ``loc_3f2c1a0b9e7d``、``fac_a1b2c3d4e5f6``。
    """
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{uuid4().hex[:12]}"


def now_iso() -> str:
    """返回 UTC ISO-8601 时间戳（带 ``+00:00`` 偏移）。"""
    return datetime.now(timezone.utc).isoformat()
