"""skill 读侧投影（装配注入 + 检查项旁路扫描）。

职责（纯函数、无 IO、全容错：读侧宽容——脏数据跳过，不抛错）：

- :func:`build_skills_segment` → 装配 payload 的**独立新段** ``payload["skills"]``
  （含 ``applies_to`` 过滤 + 字符预算 + 截断标记）；
- :func:`skill_check_hits` → skill ``checks`` 的**确定性字面扫描**（子串计数），
  命中结果只进 ``review_report.warnings[]``（**旁路通道**）。

三条硬边界（改动前先读）：

1. **绝不进 ``scan_ai_patterns``**：那条通道会自动进 ``quality_reports`` 与提交门禁
   （AGENTS.md 硬规则 4 的 severity 矩阵），而 skill 检查是**作者声明的检查项**，
   不是软件层的质量判定 ⇒ 只走 warnings 旁路。
2. **rule_id 命名空间恒为 ``SKILL-``**：该命名空间**不在**任何后果表
   （``BLOCKING_RULES`` / ``CONFIRM_RULES`` / ``REVISE_RULE_IDS`` /
   ``REVISABLE_RULE_IDS``）内 ⇒ 即使将来误入 ``errors[]``，``decide_repair`` 也只会
   按「表外 rule_id ⇒ 硬停交人工」处理（不会自动改稿、更不会阻断提交）。
3. **不承载可测指标**：schema 刻意不提供 ``ratio`` / ``threshold`` / ``score`` /
   ``weight`` 字段（F-19 红线：可测算子只作体检、不作处方——一旦进入 skill 就会
   被模型当成指标去凑）。本模块也不新增任何阈值口径。
"""

from __future__ import annotations

import json
from typing import Any, Final, Iterable

__all__ = [
    "SKILLS_MAX_CHARS",
    "SKILLS_TRUNCATED_KEY",
    "SKILL_RULE_ID_PREFIX",
    "build_skills_segment",
    "collect_skill_checks",
    "skill_applies_to",
    "skill_block",
    "skill_check_hits",
    "skill_rule_id",
]

#: ``skills`` 段的**总**字符预算（JSON 序列化长度）。genre 侧的同款口径：
#: writer 的题材体例段 1500 / critic rubric 1000 ⇒ skill 段取 2000。
SKILLS_MAX_CHARS: Final[int] = 2000

#: 截断标记键名（仓内 ``__*_truncated__`` 同款约定）。
SKILLS_TRUNCATED_KEY: Final[str] = "__skills_truncated__"

#: 检查项命中时的 rule_id 前缀（命名空间隔离，见模块 docstring 第 2 条）。
SKILL_RULE_ID_PREFIX: Final[str] = "SKILL-"

# 文本被截断时的后缀（与 genre 侧 ``_TRUNCATED_SUFFIX`` 同款口径）。
_TRUNCATED_SUFFIX: Final[str] = "…（已截断）"

# 截断标记本身占的字符数（JSON 序列化后）：给 ``items`` 段加标记时多出的长度。
# 预算裁剪时必须**预留**它——否则「按满预算裁好的内容」加上标记就超预算，
# 而收尾的「回退尾部条目」会把刚裁好的那条也丢掉（实测：items 变空列表）。
_TRUNCATION_MARKER_RESERVE: Final[int] = (
    len(json.dumps({"items": [], SKILLS_TRUNCATED_KEY: True}, ensure_ascii=False))
    - len(json.dumps({"items": []}, ensure_ascii=False))
)

# 单条 reference / check 的条数上限（schema 侧同量级，注入层再兜一刀）。
_REFERENCE_CAP: Final[int] = 20
_CHECK_CAP: Final[int] = 20
_KEYWORD_CAP: Final[int] = 50


def _clean_text(value: Any) -> str:
    """非空字符串 → strip 后原值；其余（None / 数字 / 空串）→ ``""``。"""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _json_len(value: Any) -> int:
    """JSON 序列化字符长度（不可序列化 → 一个不可能满足的大值）。"""
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return 1 << 30


def skill_rule_id(check_id: str) -> str:
    """检查项 id → 落库 rule_id（恒 ``SKILL-<check_id>``）。

    命名空间隔离是**刻意的**：``SKILL-*`` 不在任何后果表内 ⇒ 表外 rule_id 一律
    「硬停交人工」，不会自动改稿、不会阻断提交（见模块 docstring 第 2 条）。
    """
    return f"{SKILL_RULE_ID_PREFIX}{check_id}"


def skill_applies_to(payload: Any, consumer: str | tuple[str, ...]) -> bool:
    """该 skill 是否适用于 ``consumer``。

    - ``applies_to`` **缺席 / 非法 / 空** ⇒ **适用**（不限定范围 = 全部环节）。
      刻意如此：否则「校验通过但永不生效」的死资源会成为常态（那是本仓反复登记的
      「数据源存在 ≠ 已进装配」形状）。
    - ``applies_to`` 有声明 ⇒ consumer 必须在其中，否则不注入。
    - ``consumer`` 可为**元组**（合并角色）：命中其中任一即适用。用于
      ``director_planner`` 这个「导演 + 场景规划」合并体——它同时扮演两个角色，
      故两个角色各自的 skill 都该对它可见（与题材包在合并 payload 里把
      director 段与 planner 段**合并**同一思路）。

    ⚠️ **生产链路的 consumer 恒为元组**（``_skill_excerpt`` 把单个 consumer 归一成
    ``(consumer,)``），故**过滤的生效分支是 ``isinstance(consumer, tuple)`` 那一支**；
    下面的 ``str`` 分支只在「直接调用本函数」时可达（公共 API 便利）。
    **改过滤逻辑必须改元组分支**——改 ``str`` 分支在生产链路上是**改一行死代码**
    （本批实测：突变打在那儿，用例全绿 ⇒ 突变存活）。
    """
    if not isinstance(payload, dict):
        return False
    raw = payload.get("applies_to")
    if not isinstance(raw, list) or not raw:
        return True
    declared = {str(x) for x in raw if isinstance(x, str) and x}
    if not declared:
        return True
    if isinstance(consumer, tuple):
        return any(c in declared for c in consumer)
    return consumer in declared


def _normalize_references(payload: dict[str, Any]) -> list[dict[str, str]]:
    """payload.references → 注入形态（``{path, title, body}``）；非法条目整条跳过。"""
    raw = payload.get("references")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        body = _clean_text(item.get("body"))
        title = _clean_text(item.get("title"))
        path = _clean_text(item.get("path"))
        if not (body or title or path):
            continue
        out.append({"path": path, "title": title, "body": body})
        if len(out) >= _REFERENCE_CAP:
            break
    return out


def _normalize_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """payload.checks → 注入形态（``{check_id, description, keywords}``）。"""
    raw = payload.get("checks")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        check_id = _clean_text(item.get("check_id"))
        if not check_id or check_id in seen:
            continue
        seen.add(check_id)
        entry: dict[str, Any] = {
            "check_id": check_id,
            "description": _clean_text(item.get("description")),
        }
        keywords = item.get("keywords")
        if isinstance(keywords, list):
            words: list[str] = []
            for kw in keywords:
                word = _clean_text(kw)
                if word and word not in words:
                    words.append(word)
                if len(words) >= _KEYWORD_CAP:
                    break
            if words:
                entry["keywords"] = words
        out.append(entry)
        if len(out) >= _CHECK_CAP:
            break
    return out


def skill_block(row: Any, payload: Any) -> dict[str, Any] | None:
    """一条 skill 行 + 其 payload → 注入块；形态非法 → None（整条跳过）。

    注入块形状：``{skill_id, name, version, instructions, references?, checks?}``。
    """
    if not isinstance(row, dict) or not isinstance(payload, dict):
        return None
    skill_id = _clean_text(row.get("skill_id"))
    if not skill_id:
        return None
    instructions = _clean_text(payload.get("instructions"))
    if not instructions:
        return None
    try:
        version = int(row.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    name = _clean_text(payload.get("name")) or _clean_text(row.get("name"))
    block: dict[str, Any] = {
        "skill_id": skill_id,
        "name": name,
        "version": version,
        "instructions": instructions,
    }
    references = _normalize_references(payload)
    if references:
        block["references"] = references
    checks = _normalize_checks(payload)
    if checks:
        block["checks"] = checks
    return block


def _truncate_block_to_fit(
    block: dict[str, Any], kept: list[dict[str, Any]], limit: int,
) -> dict[str, Any] | None:
    """把一个装不下的块裁进剩余预算（对 ``instructions`` 二分截断）。

    返回裁好的块；连空壳都放不下 → ``None``（该块整条丢弃）。
    """
    text = block.get("instructions") or ""
    lo, hi = 0, len(text)
    best: dict[str, Any] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        probe = {**block, "instructions": text[:mid] + _TRUNCATED_SUFFIX}
        if _json_len({"items": [*kept, probe]}) <= limit:
            best = probe
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    # 引用 / 检查项也可能把体积撑大：极端情况下先丢它们再试一次。
    if _json_len({"items": [*kept, best]}) > limit:
        best = {k: v for k, v in best.items() if k not in ("references", "checks")}
        if _json_len({"items": [*kept, best]}) > limit:
            return None
    return best


def build_skills_segment(
    rows: Iterable[Any], consumer: str | tuple[str, ...], *, max_chars: int = SKILLS_MAX_CHARS,
) -> dict[str, Any] | None:
    """项目绑定的 skill 集合 → 装配 payload 的 ``skills`` 段。

    - ``rows``：``{"skill_id", "name", "version", "payload": {...}}`` 形态的
      ``skills`` 表行（由调用方从 DB 读出；本函数不碰 IO）。
    - **过滤**：``applies_to`` 不含 ``consumer`` 的 skill 整条跳过（``consumer``
      可为元组 = 合并角色，命中任一即适用）。
    - **顺序**：调用方给定的顺序即注入顺序（绑定表的 ``ord``）。
    - **预算**：整段 JSON 序列化长度 ≤ ``max_chars``；超限时按顺序保留，
      装不下的那条对 ``instructions`` 二分截断（加 ``…（已截断）``），
      再装不下的整条丢弃，并打 ``__skills_truncated__ = true``。
    - 没有任何 skill 适用 → ``None``（调用方**不写该键**，零行为变化）。
    """
    limit = max(int(max_chars), 0)
    kept: list[dict[str, Any]] = []
    truncated = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not skill_applies_to(payload, consumer):
            continue
        block = skill_block(row, payload)
        if block is None:
            continue
        if _json_len({"items": [*kept, block]}) <= limit:
            kept.append(block)
            continue
        # 装不下 ⇒ 裁这条（**为截断标记预留空间**，否则收尾回退会把裁好的也丢掉）
        trimmed = _truncate_block_to_fit(
            block, kept, limit - _TRUNCATION_MARKER_RESERVE,
        )
        if trimmed is not None:
            kept.append(trimmed)
        truncated = True
        break

    if not kept and not truncated:
        return None
    segment: dict[str, Any] = {"items": kept}
    if truncated:
        segment[SKILLS_TRUNCATED_KEY] = True
        # 兜底：标记优先于内容保留（预算实在太小才回退尾部条目）。
        while kept and _json_len(segment) > limit:
            kept.pop()
            segment["items"] = kept
    if _json_len(segment) > limit:
        segment = {"items": [], SKILLS_TRUNCATED_KEY: True}
    return segment


def collect_skill_checks(rows: Iterable[Any], consumer: str | tuple[str, ...]) -> list[dict[str, Any]]:
    """项目绑定的 skill 里，适用于 ``consumer`` 的**检查项**（扁平化、保序）。

    每条为 ``{"skill_id", "check_id", "description", "keywords"}``（``keywords``
    缺席 = 该检查项不做确定性扫描）。只收**声明了 keywords** 的检查项——没有词表的
    检查项无法做字面扫描（留给模型自己看）。

    用途：评审侧的**旁路**通道（``review_report.warnings[]``）——命中恒 warning、
    绝不进 ``scan_ai_patterns`` / ``quality_reports`` / 提交门禁。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not skill_applies_to(payload, consumer):
            continue
        for check in _normalize_checks(payload):
            if not check.get("keywords"):
                continue
            out.append({
                "skill_id": str(row.get("skill_id") or ""),
                "check_id": check["check_id"],
                "description": check.get("description") or "",
                "keywords": list(check.get("keywords") or []),
            })
    return out


def skill_check_hits(text: str, checks: Any) -> list[dict[str, Any]]:
    """skill ``checks`` 的确定性字面扫描（子串计数，与题材禁词扫描同款口径）。

    返回 ``[{"rule_id", "check_id", "description", "keyword", "count"}]``，
    仅含命中项，顺序 = ``checks`` 声明序 × 词表声明序（确定性）。

    - 无 ``keywords`` 的检查项**不**做扫描（只作为声明随段注入）；
    - 空文本 / 非法 ``checks`` → ``[]``；
    - **命中恒 warning**：本函数不产出 severity，也不进任何后果表
      （调用方只把它写进 ``review_report.warnings[]``）。
    """
    if not text or not isinstance(checks, list):
        return []
    hits: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        check_id = _clean_text(item.get("check_id"))
        if not check_id:
            continue
        keywords = item.get("keywords")
        if not isinstance(keywords, list):
            continue
        description = _clean_text(item.get("description"))
        for raw_kw in keywords:
            word = _clean_text(raw_kw)
            if not word:
                continue
            count = text.count(word)
            if count > 0:
                hits.append({
                    "rule_id": skill_rule_id(check_id),
                    "check_id": check_id,
                    "description": description,
                    "keyword": word,
                    "count": count,
                })
    return hits
