"""「被审 / 待核草稿」的唯一解析单点（P1-1：不管谁读草稿，读的必须是被审那一版）。

缺陷形状（2026-09-18 实证，review run ``wfr_adf71afbb7d9`` / 章 ``ch_92bac068ff0d``）：
作者手改草稿（``POST /api/chapters/{id}/drafts``，DRAFTED / REVIEWED 均允许，且
质量报告专门用 Q8 人工占比鼓励它）会**新增一版**；任何「按 chapter_id 取该章草稿」
的读者此后都拿到新版。若调用方手里其实有「本次评审 / 核销 / 评估的那一版」，两处
口径就分叉——同一份评审报告里 ``word_count=2248, draft_version=13``，而题材核销栏
报「实际字数 713（length_report）」（713 是 v11 的可见字数，来自写 v11 的那次
chapter-write run 的 checkpoint）。这不是某一处代码写错，而是一条形状：
**测量对象 ≠ 被审对象**；修一处只治一个点，所以要有一个共享解析单点。

解析规则（全仓唯一实现，调用方不得各写一份 SQL）：

- ``draft_version`` 显式给出 → 取该版本；不存在 → ``None``（**调用方决定**语义：
  评审侧 ``basic_checks`` 抛错、核销层降级为空——本模块不做策略）。
- ``draft_version is None`` → 取**最新**一版，``ORDER BY version DESC``。
  排序依据是 ``version`` 而非 ``created_at``：``version`` = ``COALESCE(MAX(version),0)
  + 1`` 的发布序（单调、与作者看到的「v13」同一口径），``created_at`` 只是墙钟戳，
  同秒并列时序不稳。

**读失败 vs 没有草稿（2026-10-23 critical C-1 / D-6 修复）**：

- **没有草稿**（正常业务状态）→ 返回 ``None``；
- **读失败**（打不开库 / 缺 ``drafts`` 表 / 查询异常）→ 抛 :class:`DraftResolutionError`。

⚠️ 此前两者都返回 ``None``，且本模块 docstring 明写「读侧全容错」——**设计上**把
DB 故障与「该章无草稿」列为同一件事 ⇒ 上游把「没量到」当成「量过了、没问题」
⇒ 质量门给**从未被评估**的章节发**满分 0 issue**（见
``docs/impl/reviews/full-review-round2-2026-10-23.md`` §3.1）。现在两者**必须**分开：
调用方要么显式处理异常（fail-closed），要么显式降级并**留下可见信号**。

调用方纪律（P1-1）：

- 手里有一版「被审 / 被核 / 被评」的对象（评审 run 的 ``ctx['draft_version']``、
  ``quality_reports.draft_version``、指定版本复审）→ **必须**把版本传进来；
- 只想要「当前最新正文」的读者（导出定稿正文、FTS 召回、下一章装配、仪表盘、
  提交后摘要）→ 传 ``None``，并在调用点写一行注释说明「有意取最新」，
  免得下一个读者把它当成本模块要修的漏传好心「修」错。

读侧**不再全容错**：打不开库 / 缺 ``drafts`` 表 / 查询异常 → 抛
:class:`DraftResolutionError`（**故障**），与「该章无草稿」（返回 ``None``，
**正常业务状态**）严格区分——见下文「读失败 vs 没有草稿」。

与「评审侧默认取最新」的分工：``chapter-review`` 的 ``basic_checks`` 无显式版本时
取最新，经本模块语义应等价（``version DESC``）。**2026-09-18 已收敛**：该节点此前
自带一份 ``ORDER BY created_at DESC`` 的「最新」定义（与核销层 / 质量报告按
``version`` 的口径分叉，同一份报告里有两个可能的「最新」），现已改调本模块。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

__all__ = ["resolve_draft", "LATEST_DRAFT_ORDER", "DraftResolutionError"]


LATEST_DRAFT_ORDER = "version DESC"
"""``draft_version=None`` 时的排序口径（全仓唯一声明处：``version`` 单调发布序）。"""


class DraftResolutionError(RuntimeError):
    """读草稿失败（库打不开 / ``drafts`` 表缺失或损坏 / 查询异常）。

    ⚠️ 与「该章没有草稿」**严格区分**（2026-10-23 critical C-1 / D-6 修复）：

    - **没有草稿**（正常业务状态）⇒ :func:`resolve_draft` 返回 ``None``；
    - **读不到**（故障状态）⇒ 抛本异常。

    此前两者都返回 ``None`` ⇒ 上游把「没量到」与「量过了、没问题」混为一谈 ⇒
    质量门给**从未被评估**的章节发满分 0 issue。
    """


def _coerce_version(raw: Any) -> int | None:
    """把显式版本归一为正整数；``bool`` / 非整数 / 非正数 → ``None``（视为不可解析）。"""
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def resolve_draft(
    db_path: str | Path,
    chapter_id: str,
    draft_version: int | None = None,
) -> dict[str, Any] | None:
    """解析「被审 / 待核草稿」行。

    参数：
        db_path / chapter_id：目标章。
        draft_version：被审版本；``None`` → 最新一版（:data:`LATEST_DRAFT_ORDER`）。

    返回：完整 ``drafts`` 行 dict（``draft_id`` / ``chapter_id`` / ``version`` /
    ``content`` / ``created_by`` / ``prompt_version`` / ``model_id`` / ``created_at``，
    字段集 = ``SELECT *``，与 ``ChapterService.list_drafts`` 的行形态一致）；
    以下情形 → ``None``（**正常业务状态**，调用方各自决定语义）：

    - 该章无草稿；
    - ``draft_version`` 指定的版本不存在；
    - ``draft_version`` 不是正整数（含 ``bool``）。

    **读失败**（库打不开 / 缺 ``drafts`` 表 / 查询异常）→ 抛
    :class:`DraftResolutionError`（**故障状态**，与上面三种**不可混淆**）。

    ``row["version"]`` 即本次解析到的版本——调用方回报「我在量哪一版」时必须用它，
    不要另行再查一次（两次查询之间作者可能又改稿出新版本，标签与正文就会分叉）。
    """
    try:
        conn = get_connection(db_path)
    except Exception as exc:  # noqa: BLE001 —— 打不开库 = **故障**，不是「无草稿」
        raise DraftResolutionError(f"无法打开数据库 {db_path}：{exc}") from exc
    try:
        try:
            if draft_version is None:
                row = conn.execute(
                    "SELECT * FROM drafts WHERE chapter_id = ? "
                    f"ORDER BY {LATEST_DRAFT_ORDER} LIMIT 1",
                    (chapter_id,),
                ).fetchone()
            else:
                version = _coerce_version(draft_version)
                if version is None:
                    return None
                row = conn.execute(
                    "SELECT * FROM drafts WHERE chapter_id = ? AND version = ?",
                    (chapter_id, version),
                ).fetchone()
        except sqlite3.Error as exc:
            # 缺表 / 表损坏 / 查询异常 = **故障**：2026-10-23 critical C-1 / D-6——
            # 此前这里返回 None（与「该章无草稿」同值）⇒ 上游把「没量到」当成
            # 「量过了、没问题」⇒ 质量门给从未被评估的章节发满分 0 issue。
            raise DraftResolutionError(
                f"读取草稿失败（chapter_id={chapter_id!r}）：{exc}"
            ) from exc
    finally:
        conn.close()
    return dict(row) if row is not None else None
