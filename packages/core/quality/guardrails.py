"""8 条 Guardrail（packages.core.quality）。

对齐权威文档 ``docs/evaluation/quality-scoring-v0.md`` §4.1-§4.8。

> **MVP 收窄（与 spec §4 一致，2026-08-23 主会话拍板）**：
> - schema_validity / character_contradiction / world_rule_contradiction /
>   compliance（REQ-Q6 / REQ-Q8）在 MVP 阶段允许 error 级阻断；
> - timeline_consistency / knowledge_leakage MVP 阶段仅 warning；
> - REQ-Q7 MVP 阶段仅 warning。
> 升级到 error 只需把规格行从 ``warning`` 改为 ``error``；本文件按规格写死 severity。

签名约定：

- 每个 guardrail 返回 ``list[Issue]``（空列表 = pass）。
- 输入是纯 dict：``snapshot_pre`` / ``delta`` / ``draft`` / ``reference_texts`` / 等。
- 函数均为纯函数（不写文件、不读 DB、不联网；唯一例外是 ``schema_validity`` 通过
  ``validate_delta`` 读默认 schema 路径——属仓库内复用）。
"""

from __future__ import annotations

import re
import statistics
from typing import Iterable

from packages.core.story_state.validator import validate_delta

from .issues import Issue, Severity, make_issue

# ============================================================================
# 常量区（集中维护；阈值均为建议值待校准）
# ============================================================================


# REQ-Q7 AI 标记词（spec §4.7）
AI_MARKERS: tuple[str, ...] = (
    "首先",
    "其次",
    "再次",
    "最后",
    "不仅",
    "更重要的是",
    "然而",
    "但是",
    "总而言之",
    "综上所述",
    "值得注意的是",
    "由此可见",
)

# 爽感钩子标记词（H-1 与 pacing/style 共用）
HOOK_MARKERS: tuple[str, ...] = (
    "？",
    "?",
    "竟然",
    "难道",
    "突然",
    "就在此时",
    "不好",
    "危险",
    "秘密",
    "真相",
    "杀机",
    "变故",
    "异变",
    "危机",
)

# H-4 黄金三章冲突标记词
CONFLICT_MARKERS: tuple[str, ...] = (
    "冲突",
    "杀",
    "逃",
    "怒",
    "危机",
    "威胁",
    "死",
    "血",
    "仇",
    "逼",
    "辱",
    "骗",
    "战",
)

# 境界类关键词（H-5）：name 命中以下任一即视为"境界"实体
REALM_KEYWORDS: tuple[str, ...] = ("境", "期", "阶", "层", "重天")

# REQ-Q6 阈值（建议值待校准）
Q6_SHINGLE_LEN: int = 13
"""REQ-Q6 滑动 shingle 长度（spec §4.6：13 字）。"""

Q6_OVERLAP_RATE_THRESHOLD: float = 0.02
"""REQ-Q6 重叠字符占比 error 阈值（spec §4.6：> 2% ⇒ error）。"""

# REQ-Q7 阈值（建议值待校准）
Q7_MARKER_PER_KCHARS: float = 5.0
"""REQ-Q7 AI marker 每千字 ≥ 此值 ⇒ warning。"""

Q7_PARA_OPENER_THRESHOLD: float = 0.20
"""REQ-Q7 段落首词为'然而/但是'占比 > 此值 ⇒ warning。"""

Q7_FLAT_SENTENCE_STD: float = 3.0
"""REQ-Q7 句长标准差 < 此值且总长 ≥ 1000 ⇒ warning（过于均一的句长）。"""

Q7_MIN_CHARS_FOR_FLAT: int = 1000
"""REQ-Q7 句长标准差检查的最小文本长度。"""

# REQ-Q8 阈值（spec §4.8 给出）
Q8_ERROR_RATIO: float = 0.30
Q8_WARN_RATIO: float = 0.40


# ============================================================================
# 工具函数
# ============================================================================


_DROP_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """去空白归一化（用于 shingles / 字符匹配）。"""
    return _DROP_WS_RE.sub("", text or "")


def compute_shingles(text: str, n: int) -> list[str]:
    """返回长度恰好 ``n`` 的连续字符切片列表（按去空白归一化）。"""
    t = _norm(text)
    if len(t) < n:
        return []
    return [t[i : i + n] for i in range(len(t) - n + 1)]


def _dialogue_chars(draft: str) -> int:
    """计算 draft 内被成对双引号包裹的字符总数（对话字符数）。

    处理逻辑：按 ``"`` 切；偶数段视为成对（直接相加）；奇数段时把最后一段丢弃避免误统计。
    """
    if not draft:
        return 0
    parts = draft.split('"')
    if len(parts) < 3:
        return 0
    total = 0
    pairs = len(parts) // 2
    for i in range(pairs):
        total += len(parts[1 + i * 2])
    return total


def _paragraphs(draft: str) -> list[str]:
    """按 ``\\n\\n`` 优先、``\\n`` 次之切段。空段丢弃。"""
    if not draft:
        return []
    parts = re.split(r"\n\s*\n", draft)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for sub in p.split("\n"):
            sub = sub.strip()
            if sub:
                out.append(sub)
    # 若草稿无任何空行，作为单段处理
    if not out and draft.strip():
        out.append(draft.strip())
    return out


def _sentence_lengths(draft: str) -> list[int]:
    """返回各句（中英文常用终止符）的字符长度序列。"""
    if not draft:
        return []
    pieces = re.split(r"[。！？；…\n!?;]+", draft)
    return [len(p.strip()) for p in pieces if p and p.strip()]


def _sentence_stdev(draft: str) -> float:
    """句长总体标准差（pstdev）。样本 < 2 视为 0。"""
    lens = _sentence_lengths(draft)
    if len(lens) < 2:
        return 0.0
    return statistics.pstdev(lens)


def _marker_count(draft: str, markers: Iterable[str]) -> int:
    """统计 markers 中每个词在 draft 出现的总次数（子串匹配）。"""
    if not draft:
        return 0
    return sum(draft.count(m) for m in markers)


def _lookups_by_id(snapshot: dict) -> dict[str, dict]:
    """把 snapshot.characters 按 character_id 索引。"""
    out: dict[str, dict] = {}
    for c in (snapshot.get("characters") or []):
        cid = c.get("character_id")
        if isinstance(cid, str):
            out[cid] = c
    return out


def _world_rules_by_name(snapshot: dict) -> dict[str, dict]:
    """把 snapshot.world.world_rules 按 name（已 strip）索引。"""
    out: dict[str, dict] = {}
    for r in (snapshot.get("world", {}).get("world_rules") or []):
        name = (r.get("name") or "").strip()
        if name:
            out[name] = r
    return out


def _world_rules_by_id(snapshot: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in (snapshot.get("world", {}).get("world_rules") or []):
        rid = r.get("world_rule_id")
        if isinstance(rid, str):
            out[rid] = r
    return out


def _events_dict(snapshot: dict) -> dict:
    """snapshot.events 兼容 dict 或 list。list 时转成 ``{event_id: event}``。"""
    ev = snapshot.get("events")
    if isinstance(ev, dict):
        return ev
    if isinstance(ev, list):
        out: dict[str, dict] = {}
        for item in ev:
            if isinstance(item, dict) and isinstance(item.get("event_id"), str):
                out[item["event_id"]] = item
        return out
    return {}


def _max_timeline_day(snapshot: dict) -> int | None:
    """返回 snapshot 中出现的最大 timeline_day；无 ⇒ None。"""
    ev = _events_dict(snapshot)
    max_day: int | None = None
    for e in ev.values():
        time = e.get("time") if isinstance(e, dict) else None
        if not isinstance(time, dict):
            continue
        d = time.get("timeline_day")
        if isinstance(d, int):
            if max_day is None or d > max_day:
                max_day = d
    return max_day


# ============================================================================
# 4.1 schema_validity
# ============================================================================


def schema_validity(delta: dict) -> list[Issue]:
    """§4.1 schema_validity。

    复用 ``packages.core.story_state.validator.validate_delta``：
    错误列表非空 ⇒ 1 条 error issue（取第一条原文作为 message）。
    """
    if not isinstance(delta, dict):
        return [
            make_issue(
                severity="error",
                category="schema_validity",
                rule_id="SCHEMA_VALIDATION_FAILED",
                message="delta 不是 dict",
            )
        ]
    errs = validate_delta(delta)
    if not errs:
        return []
    first = errs[0]
    return [
        make_issue(
            severity="error",
            category="schema_validity",
            rule_id="SCHEMA_VALIDATION_FAILED",
            message=first,
            chapter_id=delta.get("chapter_id"),
        )
    ]


# ============================================================================
# 4.3 character_contradiction
# ============================================================================


def character_contradiction(snapshot: dict, delta: dict) -> list[Issue]:
    """§4.3 character_contradiction（MVP 直入 error 级）。

    判定：
    - snapshot 中 state_json.health / state_json.status 含 ``dead``/``死亡`` 的角色，
      delta 在其 character_changes 上设了 ``after`` 中含 ``location`` 或 ``goal`` 键
      ⇒ RULE_CHAR_DEAD_ACTIVE error。
    - snapshot 中已有该字段，且与 delta.character_changes[i].before 不一致
      ⇒ RULE_CHAR_BEFORE_MISMATCH warning。
    """
    out: list[Issue] = []
    if not isinstance(delta, dict):
        return out
    chapter_id = delta.get("chapter_id")
    snapshot_chars = _lookups_by_id(snapshot or {})

    changes = delta.get("character_changes") or []
    if not isinstance(changes, list):
        return out

    for ch in changes:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("character_id") or ch.get("target_id")
        op = ch.get("op")
        after = ch.get("after")
        before = ch.get("before")
        field = ch.get("field")
        snap_char = snapshot_chars.get(cid) if isinstance(cid, str) else None
        snap_state = (snap_char or {}).get("current_state") or {}
        chapter_id = ch.get("evidence", {}).get("chapter_id") or chapter_id

        # 已死亡角色仍在被设置 location/goal/action 类字段 → error
        if isinstance(snap_char, dict) and isinstance(op, str) and op == "update":
            health = str(snap_state.get("health", "")) if isinstance(snap_state, dict) else ""
            status = str(snap_state.get("status", "")) if isinstance(snap_state, dict) else ""
            dead_token = "dead" in health.lower() or "死亡" in health or "死亡" in status or "dead" in status.lower()
            after_dict = after if isinstance(after, dict) else {}
            active_keys = {"location", "goal", "action"}
            if dead_token and any(k in after_dict for k in active_keys):
                out.append(
                    make_issue(
                        severity="error",
                        category="character_contradiction",
                        rule_id="RULE_CHAR_DEAD_ACTIVE",
                        message=f"角色 {snap_char.get('name', cid)} 已死亡仍被设置活动字段",
                        chapter_id=chapter_id,
                        evidence_refs=[cid or ""],
                    )
                )

        # before 与 snapshot 实际值不一致 → warning
        if (
            isinstance(snap_char, dict)
            and isinstance(field, str)
            and isinstance(before, (str, int, float, bool))
            and field in snap_state
            and snap_state.get(field) != before
        ):
            out.append(
                make_issue(
                    severity="warning",
                    category="character_contradiction",
                    rule_id="RULE_CHAR_BEFORE_MISMATCH",
                    message=(
                        f"{cid}.{field} before '{before}' 与 snapshot '{snap_state.get(field)}' 不一致"
                    ),
                    chapter_id=chapter_id,
                    evidence_refs=[cid or "", field],
                )
            )

    return out


# ============================================================================
# 4.4 world_rule_contradiction
# ============================================================================


def world_rule_contradiction(snapshot: dict, delta: dict) -> list[Issue]:
    """§4.4 world_rule_contradiction（MVP 直入 error 级）。

    判定：
    - delta.world_changes 中 ``world_kind == "rule"`` 且对应规则的
      ``data_json.hard == True``：op 是 remove 或 ``field == "statement"`` ⇒ error。
    - 同上但 hard == False / 缺省 ⇒ warning（soft 修改）。
    """
    out: list[Issue] = []
    if not isinstance(delta, dict):
        return out
    chapter_id = delta.get("chapter_id")
    rules_by_id = _world_rules_by_id(snapshot or {})
    rules_by_name = _world_rules_by_name(snapshot or {})
    changes = delta.get("world_changes") or []
    if not isinstance(changes, list):
        return out

    for ch in changes:
        if not isinstance(ch, dict):
            continue
        if ch.get("world_kind") != "rule":
            continue
        op = ch.get("op")
        field = ch.get("field")
        wid = ch.get("world_id") or ch.get("target_id")
        chapter_id = ch.get("evidence", {}).get("chapter_id") or chapter_id

        rule = rules_by_id.get(wid) if isinstance(wid, str) else None
        if rule is None and isinstance(wid, str):
            rule = rules_by_name.get(wid.strip())

        if not isinstance(rule, dict):
            continue  # 未知规则不在硬规则检查范围

        data_json = rule.get("data_json") if isinstance(rule.get("data_json"), dict) else {}
        hard = bool(data_json.get("hard", False))

        modifies_statement = op == "remove" or (op == "update" and field == "statement")

        if modifies_statement:
            severity: Severity = "error" if hard else "warning"
            rule_id_str = "RULE_WORLD_HARD_RULE_CHANGED" if hard else "RULE_WORLD_SOFT_RULE_CHANGED"
            out.append(
                make_issue(
                    severity=severity,
                    category="world_rule_contradiction",
                    rule_id=rule_id_str,
                    message=f"世界规则 {rule.get('name', wid)} 被修改（hard={hard}）",
                    chapter_id=chapter_id,
                    evidence_refs=[str(wid or "")],
                )
            )

    return out


# ============================================================================
# 4.2 timeline_consistency
# ============================================================================


def timeline_consistency(snapshot: dict, delta: dict) -> list[Issue]:
    """§4.2 timeline_consistency（MVP: warning）。

    判定：
    - new_events 中 ``time.timeline_day < 0`` ⇒ RULE_TIMELINE_NEGATIVE_DAY warning。
    - new_events 中 ``time.timeline_day < max_snapshot_timeline_day`` ⇒
      RULE_TIMELINE_NON_MONOTONIC warning。
    """
    out: list[Issue] = []
    if not isinstance(delta, dict):
        return out
    chapter_id = delta.get("chapter_id")
    new_events = delta.get("new_events") or []
    if not isinstance(new_events, list):
        return out

    max_day = _max_timeline_day(snapshot or {})
    for ev in new_events:
        if not isinstance(ev, dict):
            continue
        time_obj = ev.get("time")
        day = time_obj.get("timeline_day") if isinstance(time_obj, dict) else None
        if not isinstance(day, int):
            continue
        ev_chap = ev.get("evidence", {}).get("chapter_id") or chapter_id
        if day < 0:
            out.append(
                make_issue(
                    severity="warning",
                    category="timeline_consistency",
                    rule_id="RULE_TIMELINE_NEGATIVE_DAY",
                    message=f"事件 timeline_day={day} 为负",
                    chapter_id=ev_chap,
                    evidence_refs=[ev.get("event_id", "")],
                )
            )
        elif max_day is not None and day < max_day:
            out.append(
                make_issue(
                    severity="warning",
                    category="timeline_consistency",
                    rule_id="RULE_TIMELINE_NON_MONOTONIC",
                    message=f"事件 timeline_day={day} 小于 snapshot 最大 {max_day}",
                    chapter_id=ev_chap,
                    evidence_refs=[ev.get("event_id", "")],
                )
            )

    return out


# ============================================================================
# 4.5 knowledge_leakage
# ============================================================================


def knowledge_leakage(snapshot: dict, delta: dict) -> list[Issue]:
    """§4.5 knowledge_leakage（MVP: warning）。

    仅对 snapshot 中 ``who_knows`` 非 None 的 event / hook 做严格校验。
    未声明 who_knows 一律 pass（避免 Sprint 6 通路未通造成假阳性，spec §4.5 收窄）。
    """
    out: list[Issue] = []
    if not isinstance(delta, dict):
        return out
    chapter_id = delta.get("chapter_id")

    restricted: list[tuple[str, str, str, set[str]]] = []
    for ev in _events_dict(snapshot or {}).values():
        if not isinstance(ev, dict):
            continue
        wk = ev.get("who_knows")
        if isinstance(wk, list):
            rid = ev.get("event_id") or ""
            name = ev.get("name") or ""
            desc = ev.get("description") or ""
            restricted.append((str(rid), str(name), str(desc), set(wk)))

    for hk in (snapshot or {}).get("hooks") or []:
        if not isinstance(hk, dict):
            continue
        wk = hk.get("who_knows")
        if isinstance(wk, list):
            rid = hk.get("hook_id") or ""
            name = hk.get("name") or ""
            desc = hk.get("description") or ""
            restricted.append((str(rid), str(name), str(desc), set(wk)))

    if not restricted:
        return out  # 全 pass：未声明 who_knows

    char_changes = delta.get("character_changes") or []
    if not isinstance(char_changes, list):
        return out

    for ch in char_changes:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("character_id") or ch.get("target_id")
        after = ch.get("after")
        if not (isinstance(cid, str) and isinstance(after, dict)):
            continue
        new_knowledge = after.get("knowledge")
        if not isinstance(new_knowledge, list):
            continue
        for text in new_knowledge:
            if not isinstance(text, str) or len(text) < 4:
                continue  # 太短不视为越界事实
            hit = False
            matched_rid = ""
            for rid, rname, rdesc, rknows in restricted:
                if cid in rknows:
                    continue
                if text in rname or text in rdesc:
                    hit = True
                    matched_rid = rid
                    break
            if hit:
                out.append(
                    make_issue(
                        severity="warning",
                        category="knowledge_leakage",
                        rule_id="RULE_KNOWLEDGE_LEAK",
                        message=f"角色 {cid} 越界获取知识 '{text}'（不在 who_knows 中）",
                        chapter_id=ch.get("evidence", {}).get("chapter_id") or chapter_id,
                        evidence_refs=[cid, matched_rid],
                    )
                )

    return out


# ============================================================================
# 4.6 REQ-Q6 参照书相似度
# ============================================================================


def req_q6(draft: str, reference_texts: list[str], whitelist: list[str] | None = None) -> list[Issue]:
    """§4.6 REQ-Q6 参照书相似度（MVP: error）。

    13 字滑动 shingle；与任一 reference 出现公共 shingle（剔除 whitelist） ⇒ warning。
    总重叠字符占比 > 2% ⇒ error。reference_texts 空 ⇒ info 记录。
    embedding 双轨 MVP 不实现（README 声明）。
    """
    out: list[Issue] = []
    if not reference_texts:
        out.append(
            make_issue(
                severity="info",
                category="compliance",
                rule_id="Q6_NO_REFERENCES",
                message="未配置参照书，跳过 Q6 检查",
            )
        )
        return out

    # 把 whitelist 切成 shingles 集合（元素可多句），与 ref shingles 求差集做豁免
    white_shingles: set[str] = set()
    for w in whitelist or []:
        if isinstance(w, str):
            white_shingles.update(compute_shingles(w, Q6_SHINGLE_LEN))

    draft_shingles = compute_shingles(draft or "", Q6_SHINGLE_LEN)
    if not draft_shingles:
        return out

    total_chars = len(_norm(draft or ""))
    overlap_chars: set[int] = set()  # 用字符偏移位置统计

    ngram_issue_count = 0
    for ref in reference_texts:
        ref_shingles = compute_shingles(ref or "", Q6_SHINGLE_LEN)
        ref_set = set(ref_shingles) - white_shingles
        common = set(draft_shingles) & ref_set
        if common:
            ngram_issue_count += 1
            # 标记重叠字符位置（按 norm 后的 offset）
            # 同一 shingle 在 draft 中可能出现多次，遍历所有出现位置
            # 以避免低估总重叠字符数（之前 find 只返回首个位置）
            norm_draft = _norm(draft or "")
            for sh in common:
                start = 0
                while True:
                    idx = norm_draft.find(sh, start)
                    if idx < 0:
                        break
                    for k in range(idx, idx + Q6_SHINGLE_LEN):
                        overlap_chars.add(k)
                    start = idx + 1  # 偏移 1 以继续扫描重叠区间

    if ngram_issue_count > 0:
        out.append(
            make_issue(
                severity="warning",
                category="compliance",
                rule_id="RULE_Q6_NGRAM_OVERLAP",
                message=f"与参照书存在 {ngram_issue_count} 个公共 13 字片段",
                evidence_refs=[f"ngram_overlap_count={ngram_issue_count}"],
            )
        )

    if total_chars > 0 and (len(overlap_chars) / total_chars) > Q6_OVERLAP_RATE_THRESHOLD:
        out.append(
            make_issue(
                severity="error",
                category="compliance",
                rule_id="RULE_Q6_OVERLAP_RATE",
                message=(
                    f"重叠字符占比 {len(overlap_chars) / total_chars:.2%} "
                    f"> {Q6_OVERLAP_RATE_THRESHOLD:.2%}"
                ),
            )
        )

    return out


# ============================================================================
# 4.7 REQ-Q7 AI 痕迹自检
# ============================================================================


def req_q7(draft: str) -> list[Issue]:
    """§4.7 REQ-Q7 AI 痕迹自检（MVP: warning）。

    三项子指标各自返回 1 条 warning：
    - 每千字 marker 数 ≥ 5
    - 段落首词为 "然而/但是" 的占比 > 20%
    - 句长总体标准差 < 3（文本 ≥ 1000 字）
    """
    out: list[Issue] = []
    if not draft:
        return out

    # 指标 1：每千字 marker
    n_chars = len(draft)
    if n_chars > 0:
        per_kchars = _marker_count(draft, AI_MARKERS) / (n_chars / 1000.0)
        if per_kchars >= Q7_MARKER_PER_KCHARS:
            out.append(
                make_issue(
                    severity="warning",
                    category="compliance",
                    rule_id="RULE_Q7_AI_MARKERS",
                    message=f"AI marker 密度 {per_kchars:.2f} / 千字 ≥ {Q7_MARKER_PER_KCHARS}",
                )
            )

    # 指标 2：段落首词
    paras = _paragraphs(draft)
    if paras:
        opener_count = sum(1 for p in paras if p.startswith("然而") or p.startswith("但是"))
        ratio = opener_count / len(paras)
        if ratio > Q7_PARA_OPENER_THRESHOLD:
            out.append(
                make_issue(
                    severity="warning",
                    category="compliance",
                    rule_id="RULE_Q7_PARA_OPENER",
                    message=f"'然而/但是' 段落首词占比 {ratio:.0%} > {Q7_PARA_OPENER_THRESHOLD:.0%}",
                )
            )

    # 指标 3：句长标准差
    if n_chars >= Q7_MIN_CHARS_FOR_FLAT:
        std = _sentence_stdev(draft)
        if std < Q7_FLAT_SENTENCE_STD:
            out.append(
                make_issue(
                    severity="warning",
                    category="compliance",
                    rule_id="RULE_Q7_FLAT_SENTENCE",
                    message=f"句长总体标准差 {std:.2f} < {Q7_FLAT_SENTENCE_STD}",
                )
            )

    return out


# ============================================================================
# 4.8 REQ-Q8 人工加工占比
# ============================================================================


def req_q8(ai_chars: int, human_chars: int) -> list[Issue]:
    """§4.8 REQ-Q8 人工加工占比。

    total == 0 ⇒ info（Q8_NO_DATA）。
    ratio = human / total：< 0.30 ⇒ error；0.30-0.40 ⇒ warning；其他 pass。
    """
    total = (ai_chars or 0) + (human_chars or 0)
    if total <= 0:
        return [
            make_issue(
                severity="info",
                category="compliance",
                rule_id="RULE_Q8_NO_DATA",
                message="未记录 ai_chars / human_chars",
            )
        ]

    ratio = (human_chars or 0) / total
    if ratio < Q8_ERROR_RATIO:
        return [
            make_issue(
                severity="error",
                category="compliance",
                rule_id="RULE_Q8_HUMAN_RATIO_LOW",
                message=f"人工占比 {ratio:.2%} < {Q8_ERROR_RATIO:.0%}",
            )
        ]
    if ratio < Q8_WARN_RATIO:
        return [
            make_issue(
                severity="warning",
                category="compliance",
                rule_id="RULE_Q8_HUMAN_RATIO_LOW_WARN",
                message=f"人工占比 {ratio:.2%} 接近红线 {Q8_ERROR_RATIO:.0%}",
            )
        ]
    return []


__all__ = [
    "schema_validity",
    "character_contradiction",
    "world_rule_contradiction",
    "timeline_consistency",
    "knowledge_leakage",
    "req_q6",
    "req_q7",
    "req_q8",
    "AI_MARKERS",
    "HOOK_MARKERS",
    "CONFLICT_MARKERS",
    "REALM_KEYWORDS",
]
