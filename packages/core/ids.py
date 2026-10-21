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
    """返回 UTC ISO-8601 时间戳字符串，**微秒**精度（带 ``+00:00`` 偏移）。

    ⚠️ 2026-10-21 订正：原文写「秒级精度」——实测是微秒
    （``2026-10-21T11:50:13.208030+00:00``）。这条注释曾被用来支撑
    「``created_at`` 同秒并列 ⇒ 时序不稳」的论证（``domain/chapter/draft_resolver.py``
    的 docstring），那套论证因此在**时序**这条腿上被夸大：同秒并列实际几乎不成立。
    结论不变（草稿的「最新」口径仍统一走 ``version DESC``，因为 version 是发布序、
    而 created_at 是写入时刻），但**论证强度**要按微秒精度重述：分叉风险来自
    「两个站点各自定义最新」，不是来自时钟精度。
    """
    return datetime.now(timezone.utc).isoformat()
