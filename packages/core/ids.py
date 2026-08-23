"""ID 与时间戳工具（Sprint 1）。

职责：
- ``new_id(prefix: str) -> str``：生成带语义前缀的短 ID，形如 ``"prj_<12hex>"``。
- ``now_iso() -> str``：返回 UTC ISO-8601 时间戳，对齐 ``database/migrations/0001_init.sql``
  中各表 ``created_at`` / ``updated_at`` 的存储口径。

设计要点：
- 使用 ``uuid4().hex[:12]``：12 个十六进制字符 = 48 bit，碰撞概率极低；保持可读短形式。
- 时间戳统一 UTC，避免本地时区漂移导致写入数据库与排序混乱。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成带前缀的短 ID。

    形如 ``"prj_a1b2c3d4e5f6"``。prefix 仅作语义标签（必须为非空字符串），
    不参与碰撞防护——碰撞防护由 uuid4 的随机性保证。
    """
    if not prefix:
        raise ValueError("prefix must be a non-empty string")
    return f"{prefix}_{uuid4().hex[:12]}"


def now_iso() -> str:
    """返回 UTC ISO-8601 时间戳字符串，秒级精度（带 ``+00:00`` 偏移）。"""
    return datetime.now(timezone.utc).isoformat()
