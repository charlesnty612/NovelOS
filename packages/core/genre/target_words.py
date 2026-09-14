"""章目标字数的题材包解析（目标字数目标源 = 绑定题材包，而非软件常量）。

背景：章目标字数原先只有「请求体 / plan_json → 全仓常量 3000」两级来源，
项目绑定了题材包（``pacing.chapter_words.target`` 声明了题材字数带中值）时也
被常量压过——书1 实证：21 章 target 全为 3000，正压题材包字数带 2000~3000 的
上沿，9/21 章超上限。本模块把「绑定题材包声明的章目标字数」插进优先级链：

    显式（ctx 覆盖 / plan_json）→ 绑定题材包 pacing.chapter_words.target → 3000

设计要点：
- **单点定义**：write（length_check / writer）与 review（basic_checks / critic /
  deep_review）两侧共用本函数，避免各写一份口径漂移；
- **fail-soft**：未绑定 / payload 无该字段 / 类型非法 / 读库异常（含迁移未跑的
  极老库）→ ``None`` 回退下一级，绝不阻断 write / review；
- **无包零行为变化**：未绑定时返回值与改造前逐字节一致（ctx/plan 显式值不动，
  都缺则 3000）。

边界：本模块只读项目绑定题材包的 ``pacing.chapter_words.target``（整数）；
字数带（``pacing.chapter_word_band`` / ``projects.word_band_json``）仍由
``packages.core.quality.wordcount.resolve_band_config`` 负责，两者互不覆盖。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.quality.wordcount import DEFAULT_TARGET_WORD_COUNT

from .service import GenrePackService

__all__ = ["pack_chapter_words_target", "resolve_target_word_count"]

_log = logging.getLogger(__name__)


def _positive_int(value: Any) -> int | None:
    """非空且 > 0 的整数化取值；``None`` / bool / 非数 / <= 0 → ``None``。

    与旧口径的 ``or`` falsy 语义对齐（0 视为「未给出」）；不可解析值（字符串
    非数 / None / dict）一律视为缺席，不抛错。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _chapter_project_id(db_path: Path | str, chapter_id: str) -> str | None:
    """章节所属项目 id；章节不存在 / 读库异常 → ``None``（fail-soft）。"""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 极老库缺表 / 锁竞争均视为无绑定
        _log.warning(
            "genre_pack.chapter project lookup failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return None
    return row["project_id"] if row is not None else None


def pack_chapter_words_target(
    db_path: Path | str | None, project_id: str | None,
) -> int | None:
    """项目绑定题材包声明的章目标字数（``pacing.chapter_words.target``）。

    未绑定 / payload 无 ``pacing`` 或无 ``chapter_words`` / ``target`` 非法 /
    读库异常 → ``None``（调用方回退默认）。
    """
    if not db_path or not project_id:
        return None
    try:
        binding = GenrePackService(db_path).get_project_binding(project_id)
    except Exception as exc:  # noqa: BLE001 —— 题材包读失败不阻断 write / review
        _log.warning(
            "genre_pack target read failed: project_id=%s err=%s", project_id, exc,
        )
        return None
    if not binding or not binding.get("bound"):
        return None
    payload = (binding.get("pack") or {}).get("payload")
    if not isinstance(payload, dict):
        return None
    pacing = payload.get("pacing")
    if not isinstance(pacing, dict):
        return None
    chapter_words = pacing.get("chapter_words")
    if not isinstance(chapter_words, dict):
        return None
    return _positive_int(chapter_words.get("target"))


def resolve_target_word_count(
    db_path: Path | str | None,
    chapter_id: str | None,
    plan: dict[str, Any] | None = None,
    *,
    explicit: Any = None,
    project_id: str | None = None,
) -> int:
    """解析本章目标字数（write / review 两侧共用）。

    优先级：``explicit``（ctx 显式覆盖）→ ``plan["expected_word_count"]`` →
    ``plan["target_word_count"]``（兼容旧字段）→ 绑定题材包
    ``pacing.chapter_words.target`` → :data:`DEFAULT_TARGET_WORD_COUNT`（3000）。

    ``project_id`` 已知时传入可省一次 chapters 查询（review basic_checks 持
    章节行）；未传则由 ``chapter_id`` 反查。
    """
    plan_dict = plan if isinstance(plan, dict) else {}
    for raw in (
        explicit,
        plan_dict.get("expected_word_count"),
        plan_dict.get("target_word_count"),
    ):
        value = _positive_int(raw)
        if value is not None:
            return value

    pid = project_id
    if not pid and db_path and chapter_id:
        pid = _chapter_project_id(db_path, chapter_id)
    pack_target = pack_chapter_words_target(db_path, pid)
    if pack_target is not None:
        return pack_target
    return DEFAULT_TARGET_WORD_COUNT
