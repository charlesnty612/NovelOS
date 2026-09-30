"""chapter_write · polisher 段（2026-09-27 B-1 拆分自 pipeline.py）。

含 polisher 的确定性预检两段：_polish_precheck_forbidden_words（题材禁词）/
_polish_precheck（三源聚合）。节点本体 _polish_node 因 monkeypatch 契约留在门面。
同包内搬移，逻辑逐字不变。
"""

from __future__ import annotations

import logging
from typing import Any

from packages.core.db import get_connection
from packages.core.genre.consumers import count_forbidden_word_hits, forbidden_words
from packages.core.genre.service import GenrePackService
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.guardrails import req_q7

_log = logging.getLogger(__name__)
def _polish_precheck_forbidden_words(db_path: str, chapter_id: str) -> list[str]:
    """项目绑定题材包的禁词表（题材库 P1b 读侧投影，与 chapter_review 同口径）。

    路径：chapters.project_id → ``GenrePackService.get_project_binding`` →
    ``forbidden_words(pack.payload)``（= ``payload.style_constraints.forbidden_words``，
    genre.consumers 单点解析）。未绑定 / 读库失败 / 极老库 → ``[]``（预检退化为
    「题材禁词零命中」，不阻断润色、不改变既有行为）。
    """
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 预检失败不阻断润色
        _log.warning(
            "chapter_write.polish_precheck chapter lookup failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return []
    project_id = row["project_id"] if row is not None else None
    if not project_id:
        return []
    try:
        binding = GenrePackService(db_path).get_project_binding(project_id)
    except Exception as exc:  # noqa: BLE001 —— 题材包读失败不阻断润色
        _log.warning(
            "chapter_write.polish_precheck genre_pack read failed: project_id=%s err=%s",
            project_id, exc,
        )
        return []
    if not binding or not binding.get("bound"):
        return []
    return forbidden_words(((binding.get("pack") or {}).get("payload")) or {})


def _polish_precheck(raw_prose: str, db_path: str, chapter_id: str) -> dict[str, Any]:
    """polisher 条件触发的确定性预检（零 LLM、零新规则）。

    三源全部复用既有检测器与既有阈值（见模块顶部常量区注释）：

    1. ``scan_ai_patterns``：AI 腔模式级命中（权威信号源，含禁用词 / 三连句式 /
       他她排比 / 章尾升华 / 标点滥用 / 解释腔，severity 与阈值内置）；
    2. ``count_forbidden_word_hits`` × 题材包禁词表：题材禁词子串计数
       （与 chapter_review._basic_checks 同源）；
    3. ``req_q7``：AI marker 密度 / 「然而|但是」段首占比 / 句长标准差三子指标
       （阈值常量在 quality.guardrails，单一权威）。

    返回值全部为 JSON 原生类型（进 ctx → checkpoint_json 可序列化；``req_q7``
    的 Issue（pydantic）在此投影为 dict）。
    """
    ai_hits = scan_ai_patterns(raw_prose)
    forbidden = _polish_precheck_forbidden_words(db_path, chapter_id)
    forbidden_hits = count_forbidden_word_hits(raw_prose, forbidden)
    q7_issues = req_q7(raw_prose)
    return {
        "ai_pattern_hits": ai_hits,
        "ai_pattern_hit_count": len(ai_hits),
        "forbidden_words": forbidden,
        "forbidden_word_hits": [
            {"word": word, "count": count} for word, count in forbidden_hits
        ],
        "forbidden_word_hit_count": sum(count for _, count in forbidden_hits),
        "q7_issues": [
            {
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "message": issue.message,
            }
            for issue in q7_issues
        ],
        "q7_issue_count": len(q7_issues),
    }
