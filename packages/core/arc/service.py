"""叙事弧光聚合视图——服务层。

职责：

- :func:`build_arc_view` —— 纯函数入口，串起五类 DB 数据 → 一屏 dict；
- :func:`_parse_beat_flags` —— 容错解析 ``key_beats`` 提取 ``has_payoff_beat`` /
  ``charge_beat``，支持 ``list[dict]`` / ``list[str]`` / 缺失 三种形态；
- :func:`_count_charge_streaks` —— 在章序上算「连续蓄力章」当前连击 + 历史最长；
- :func:`_compute_alerts` —— 模块常量驱动规则：
  - charge_streak >= 3 → fail ``charge_streak_exceeded``
  - 最近 5 章 payoff 占比 < 40% → warn ``low_payoff_density``
  - 逾期伏笔 > 0 → warn ``foreshadow_overdue``
  - 任一章 pacing < 50 → warn ``low_pacing_chapter``
- :func:`_load_latest_quality_per_chapter` / :func:`_load_latest_draft_chars_per_chapter` /
  :func:`_summarize_hooks` / :func:`_summarize_debts` —— DB 拉数辅助。

DB 取数说明（任务书要求**纯函数 + 显式 SQL**，不依赖私有函数）：

- chapters：``SELECT chapter_id, number, title, plan_json FROM chapters
  WHERE project_id = ? ORDER BY number ASC``；
- quality_reports：取每章最新一条 ``overall`` / ``scores_json.pacing``（沿用
  ``packages.core.quality.service.QualityService`` 的解析思路但不 import 私有方法）；
- drafts：每章取 ``version`` 最大那条 ``content``，按非空白字符计数（与
  ``packages.core.signing_check.checks._count_chars`` 对齐口径，但本模块自实现、
  不 import 跨包私有函数）；
- hooks：``SELECT status, importance, introduced_chapter_id FROM hooks WHERE project_id=?``；
- narrative_debts：``SELECT status FROM narrative_debts WHERE project_id=?``；
- project overdue 阈值：复用 ``packages.core.context_engine.builders`` 模块常量的语义
  （fallback 30），但本模块**不** import 业务 builder（避免引入依赖），复制常量
  到本模块顶部作为默认值。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.domain.project.service import ProjectService

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# 模块常量（任务书给死，alerts 规则可调）
# ---------------------------------------------------------------------------

# charge（蓄力）连续未兑现阈值：≥ N 章连续有 charge 而无 payoff → fail。
_CHARGE_STREAK_FAIL: int = 3

# payoff 密度：最近 N 章中 payoff 章占比下限（不足则 warn）。
_PAYOFF_DENSITY_WINDOW: int = 5
_PAYOFF_DENSITY_MIN_RATIO: float = 0.4  # 40%

# 章节 pacing 单章下限：低于该值则 warn（任一章触发即记录）。
_PACING_FAIL_THRESHOLD: int = 50

# 伏笔逾期阈值（fallback）：与 ``packages.core.context_engine.builders._FORESHADOW_OVERDUE_CHAPTERS``
# 对齐（避免 import 跨包私有函数；项目级 ``projects.foreshadow_overdue_chapters`` 列缺
# /NULL 时回退该值）。
_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS: int = 30

# 开放伏笔状态集合（与 builders._PLANTED_HOOK_STATUSES 对齐；本模块独立维护避免跨包耦合）。
_PLANTED_HOOK_STATUSES: frozenset[str] = frozenset({"OPEN", "ACTIVE", "ESCALATED"})
# 已兑现伏笔状态。
_RESOLVED_HOOK_STATUSES: frozenset[str] = frozenset({"RESOLVED"})
# 已支付叙事债务状态（与 narrative_debts.status 枚举对齐：'paid'/'forgiven' 视为已结清）。
_PAID_DEBT_STATUSES: frozenset[str] = frozenset({"paid", "forgiven"})

# 章节最新 draft SQL（与 signing_check 同款；按 version DESC LIMIT 1）。
_LATEST_DRAFT_SQL: str = (
    "SELECT content FROM drafts "
    "WHERE chapter_id = ? "
    "ORDER BY version DESC LIMIT 1"
)
# 章节最新 quality_report SQL。
_LATEST_REPORT_SQL: str = (
    "SELECT overall, scores_json FROM quality_reports "
    "WHERE chapter_id = ? "
    "ORDER BY created_at DESC LIMIT 1"
)

# 解析 key_beats 的 purpose 字段前缀标记（任务书给死）。
_PAYOFF_TAG_RE: re.Pattern[str] = re.compile(r"\[payoff\]", re.IGNORECASE)
_CHARGE_TAG_RE: re.Pattern[str] = re.compile(r"\[charge\]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 纯函数：key_beats 解析
# ---------------------------------------------------------------------------


def _beat_purpose_text(beat: Any) -> str:
    """从容错形态的 beat 取 purpose 文本。

    支持：
    - dict（取 ``beat["purpose"]``，缺则空串）
    - str（自身）
    - 其它（空串）
    """

    if isinstance(beat, dict):
        purpose = beat.get("purpose")
        return str(purpose) if purpose else ""
    if isinstance(beat, str):
        return beat
    return ""


def _parse_beat_flags(key_beats: Any) -> tuple[bool, bool]:
    """从 ``plan_json.key_beats`` 提取 ``(has_payoff_beat, charge_beat)``。

    容错：
    - ``key_beats`` 非 list → ``(False, False)``；
    - list 元素可能是 dict / str / 其它，逐一抽取 purpose 文本扫描标签；
    - 同章同时含 payoff + charge 时按「同时存在」如实记录（不互斥）。
    """

    if not isinstance(key_beats, list):
        return False, False
    has_payoff = False
    has_charge = False
    for beat in key_beats:
        text = _beat_purpose_text(beat)
        if not text:
            continue
        if _PAYOFF_TAG_RE.search(text):
            has_payoff = True
        if _CHARGE_TAG_RE.search(text):
            has_charge = True
        # 短路：两边都已命中，无需继续
        if has_payoff and has_charge:
            break
    return has_payoff, has_charge


# ---------------------------------------------------------------------------
# 纯函数：charge 连击计数
# ---------------------------------------------------------------------------


def _count_charge_streaks(chapter_flags: list[dict[str, Any]]) -> dict[str, int]:
    """扫一遍章序（已按 number ASC）算 charge 连击指标。

    ``chapter_flags`` 元素形如 ``{"number": int, "has_payoff_beat": bool, "charge_beat": bool}``。

    返回 ``{"charge_streak": 当前连续蓄力章数（尾部连续）,
             "max_charge_streak": 历史最长, "last_payoff_chapter": 章号|null}``。

    规则：
    - 「蓄力章」定义：本章 ``charge_beat=True`` 且 ``has_payoff_beat=False``。
      同时含 payoff+charge 视为兑现章（不计入蓄力连击）。
    - 「当前连续蓄力章数」= 尾部连续蓄力章数；若最后一章是 payoff 章则视为 0。
    - 「历史最长」= 全章序内蓄力连击的最大值（章序号连续，无中断）。
    - 「最近 payoff 章号」= 章序上最后一个 ``has_payoff_beat=True`` 的章号；无则 None。
    """

    # 单遍 forward loop 算出三个量：
    # - current_run：当前位置结束的连续蓄力章数（含当前章），遇非蓄力章即清零；
    # - max_charge_streak：扫过的历史最大连击；
    # - last_payoff_chapter：章序上最近一次 payoff 的章号。
    current_run = 0
    max_charge_streak = 0
    last_payoff_chapter: int | None = None
    for ch in chapter_flags:
        is_charge_only = bool(ch.get("charge_beat")) and not bool(ch.get("has_payoff_beat"))
        is_payoff = bool(ch.get("has_payoff_beat"))
        if is_charge_only:
            current_run += 1
        else:
            current_run = 0
        if current_run > max_charge_streak:
            max_charge_streak = current_run
        if is_payoff:
            last_payoff_chapter = ch.get("number")
    # 「当前连续蓄力章数」= 章序尾部扫到的连续 charge_only 章数。
    # 直接复用 current_run 的语义即可（最后一次循环结束后保留的就是尾部连击数），
    # 无需反向二次扫描。
    return {
        "charge_streak": current_run,
        "max_charge_streak": max_charge_streak,
        "last_payoff_chapter": last_payoff_chapter,
    }


# ---------------------------------------------------------------------------
# 纯函数：payoff 密度
# ---------------------------------------------------------------------------


def _payoff_ratio_recent(chapter_flags: list[dict[str, Any]], window: int) -> tuple[int, float]:
    """最近 ``window`` 章 payoff 占比。

    章号 < window 时取全部章。返回 ``(payoff_count, ratio)``。
    """

    if not chapter_flags:
        return 0, 0.0
    tail = chapter_flags[-window:]
    payoff_count = sum(1 for c in tail if c.get("has_payoff_beat"))
    ratio = payoff_count / len(tail) if tail else 0.0
    return payoff_count, ratio


# ---------------------------------------------------------------------------
# DB 取数辅助
# ---------------------------------------------------------------------------


def _load_latest_quality_per_chapter(
    conn: sqlite3.Connection,
    chapter_ids: list[str],
) -> dict[str, dict[str, int | None]]:
    """每章取最新 quality_report 的 ``overall`` / ``scores_json.pacing``。

    返回 ``{chapter_id: {"overall": int|None, "pacing": int|None}}``。
    pacing 解不出来（scores_json 不是合法 JSON / 无 pacing 键）置 None。
    """

    out: dict[str, dict[str, int | None]] = {cid: {"overall": None, "pacing": None} for cid in chapter_ids}
    for cid in chapter_ids:
        row = conn.execute(_LATEST_REPORT_SQL, (cid,)).fetchone()
        if row is None:
            continue
        try:
            scores = json.loads(row["scores_json"]) if row["scores_json"] else {}
        except json.JSONDecodeError:
            scores = {}
        pacing_raw = scores.get("pacing") if isinstance(scores, dict) else None
        try:
            pacing = int(pacing_raw) if pacing_raw is not None else None
        except (TypeError, ValueError):
            pacing = None
        try:
            overall = int(row["overall"]) if row["overall"] is not None else None
        except (TypeError, ValueError):
            overall = None
        out[cid] = {"overall": overall, "pacing": pacing}
    return out


def _load_latest_draft_chars_per_chapter(
    conn: sqlite3.Connection,
    chapter_ids: list[str],
) -> dict[str, int | None]:
    """每章取最新 draft 的 ``content``，按非空白字符计数。

    无 draft → None。SQL 与 :mod:`packages.core.signing_check.service._fetch_latest_draft_content`
    等价（任务书「复制不 import」）。
    """

    out: dict[str, int | None] = {cid: None for cid in chapter_ids}
    for cid in chapter_ids:
        row = conn.execute(_LATEST_DRAFT_SQL, (cid,)).fetchone()
        if row is None:
            continue
        text = row["content"] or ""
        # 与 signing_check._count_chars 对齐：去空白后取长度。
        out[cid] = len("".join(text.split()))
    return out


def _project_overdue_chapters(conn: sqlite3.Connection, project_id: str) -> int:
    """读 ``projects.foreshadow_overdue_chapters``；列缺 / NULL / 非正 → fallback 30。"""

    try:
        row = conn.execute(
            "SELECT foreshadow_overdue_chapters FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS
    if row is None:
        return _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS
    val = row["foreshadow_overdue_chapters"]
    try:
        ival = int(val)
    except (TypeError, ValueError):
        return _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS
    return ival if ival > 0 else _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS


def _summarize_hooks(
    conn: sqlite3.Connection,
    project_id: str,
    overdue_threshold: int,
    chapter_no_by_id: dict[str, int],
    current_max_chapter_no: int | None,
) -> dict[str, int]:
    """统计伏笔 open / resolved / overdue。"""

    open_count = 0
    resolved_count = 0
    overdue_count = 0
    rows = conn.execute(
        "SELECT status, introduced_chapter_id FROM hooks WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    for r in rows:
        status = (r["status"] or "").upper()
        intro_id = r["introduced_chapter_id"]
        intro_no = chapter_no_by_id.get(intro_id) if intro_id else None
        if status in _PLANTED_HOOK_STATUSES:
            open_count += 1
            if (
                intro_no is not None
                and current_max_chapter_no is not None
                and (current_max_chapter_no - intro_no) > overdue_threshold
            ):
                overdue_count += 1
        elif status in _RESOLVED_HOOK_STATUSES:
            resolved_count += 1
        # ABANDONED 既不算 open 也不算 resolved
    return {"open": open_count, "resolved": resolved_count, "overdue": overdue_count}


def _summarize_debts(conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
    """统计叙事债务 open / paid（含 forgiven）。"""

    open_count = 0
    paid_count = 0
    rows = conn.execute(
        "SELECT status FROM narrative_debts WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    for r in rows:
        status = (r["status"] or "").lower()
        if status in _PAID_DEBT_STATUSES:
            paid_count += 1
        else:
            # 'open' / 'acknowledged' 视为 open（未结清）
            open_count += 1
    return {"open": open_count, "paid": paid_count}


def _summarize_reveal_policies(
    conn: sqlite3.Connection,
    project_id: str,
    current_max_chapter_no: int | None,
) -> dict[str, Any]:
    """V3.3 P0-2 知识权限补全：reveal_policies 摘要 + overdue 清单。

    返回结构::

        {
            "planned": int,
            "revealed": int,
            "cancelled": int,
            "overdue": [
                {
                    "policy_id": str,
                    "target_kind": str,
                    "target_id": str,
                    "reveal_by_chapter": int,
                    "audience": str,
                },
                ...
            ],
        }

    overdue 规则：
    - ``status='planned'`` 且 ``reveal_by_chapter`` 非 NULL 且
      ``current_max_chapter_no`` 非 NULL 且 ``reveal_by_chapter <= current_max_chapter_no``
      即视为「到期未揭示」；
    - 无章节 / 无 reveal_by_chapter → 不进 overdue 列表；
    - ``cancelled`` 与 ``revealed`` 不进 overdue 列表。

    SQL 异常（0014 未跑等 OperationalError）→ 返回全零 + 空 overdue，不阻断 arc 装配。
    """
    out: dict[str, Any] = {
        "planned": 0,
        "revealed": 0,
        "cancelled": 0,
        "overdue": [],
    }
    try:
        rows = conn.execute(
            """
            SELECT policy_id, target_kind, target_id, status,
                   reveal_by_chapter, audience
            FROM reveal_policies
            WHERE project_id = ?
            ORDER BY policy_id ASC
            """,
            (project_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out

    overdue: list[dict[str, Any]] = []
    for r in rows:
        status = r["status"]
        if status == "planned":
            out["planned"] += 1
            rbc = r["reveal_by_chapter"]
            if (
                current_max_chapter_no is not None
                and rbc is not None
                and int(rbc) <= int(current_max_chapter_no)
            ):
                overdue.append(
                    {
                        "policy_id": r["policy_id"],
                        "target_kind": r["target_kind"],
                        "target_id": r["target_id"],
                        "reveal_by_chapter": int(rbc),
                        "audience": r["audience"],
                    }
                )
        elif status == "revealed":
            out["revealed"] += 1
        elif status == "cancelled":
            out["cancelled"] += 1
    out["overdue"] = overdue
    return out


# ---------------------------------------------------------------------------
# 纯函数：alerts 计算
# ---------------------------------------------------------------------------


def _compute_alerts(
    chapter_rows: list[dict[str, Any]],
    charge_streak_metrics: dict[str, int],
    hooks_summary: dict[str, int],
) -> list[dict[str, str]]:
    """按模块常量计算告警列表。

    规则（任务书给死）：
    1. ``charge_streak >= _CHARGE_STREAK_FAIL`` → fail ``charge_streak_exceeded``
       （「蓄力超过 3 章未兑现」）；
    2. 最近 5 章 payoff 占比 < 40% → warn ``low_payoff_density``；
    3. overdue hooks > 0 → warn ``foreshadow_overdue``；
    4. 任一章 pacing < _PACING_FAIL_THRESHOLD → warn ``low_pacing_chapter``（合并
       所有 pacing 不达标章节的章号到同一条 message 里）。

    返回顺序按规则编号，便于稳定断言。
    """

    alerts: list[dict[str, str]] = []

    # 规则 1：蓄力连击超阈值
    streak = charge_streak_metrics.get("charge_streak", 0)
    if streak >= _CHARGE_STREAK_FAIL:
        alerts.append(
            {
                "level": "fail",
                "code": "charge_streak_exceeded",
                "message": f"蓄力超过 {streak} 章未兑现（阈值 {_CHARGE_STREAK_FAIL}）",
            }
        )

    # 规则 2：最近 N 章 payoff 密度不足
    _, ratio = _payoff_ratio_recent(chapter_rows, _PAYOFF_DENSITY_WINDOW)
    if chapter_rows and ratio < _PAYOFF_DENSITY_MIN_RATIO:
        window = min(_PAYOFF_DENSITY_WINDOW, len(chapter_rows))
        # 仅在「至少有 window 章」或「payoff 章数为 0」时才显式 warn：
        # 章数 < window 且 ratio=0 也应触发（章数太少本身就是低密度信号）。
        alerts.append(
            {
                "level": "warn",
                "code": "low_payoff_density",
                "message": (
                    f"最近 {window} 章 payoff 占比 {ratio:.0%}（阈值 {_PAYOFF_DENSITY_MIN_RATIO:.0%}）"
                ),
            }
        )

    # 规则 3：逾期伏笔
    overdue = hooks_summary.get("overdue", 0)
    if overdue > 0:
        alerts.append(
            {
                "level": "warn",
                "code": "foreshadow_overdue",
                "message": f"有 {overdue} 条伏笔已逾期未兑现",
            }
        )

    # 规则 4：单章 pacing 不达标（合并）
    low_pacing_chapters: list[int] = []
    for ch in chapter_rows:
        pacing = ch.get("pacing")
        if isinstance(pacing, int) and pacing < _PACING_FAIL_THRESHOLD:
            low_pacing_chapters.append(int(ch.get("number")))
    if low_pacing_chapters:
        nums = "、".join(str(n) for n in low_pacing_chapters)
        alerts.append(
            {
                "level": "warn",
                "code": "low_pacing_chapter",
                "message": (
                    f"章节 pacing 低于 {_PACING_FAIL_THRESHOLD}：第 {nums} 章"
                ),
            }
        )

    return alerts


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build_arc_view(db_path: str | Path, project_id: str) -> dict[str, Any]:
    """组装一个项目的叙事弧光聚合视图。

    返回结构（任务书给死）：
        ``{
            "project_id": str,
            "generated_at": str (ISO-8601),
            "chapters": [
              {
                "number": int, "title": str,
                "has_payoff_beat": bool, "charge_beat": bool,
                "overall": int|null, "pacing": int|null, "prose_chars": int|null,
              } ...
            ],
            "payoff": {
              "total": int, "charge_streak": int, "max_charge_streak": int,
              "last_payoff_chapter": int|null,
            },
            "hooks": {"open": int, "resolved": int, "overdue": int},
            "debts": {"open": int, "paid": int},
            "reveal_policies": {            # V3.3 P0-2 知识权限补全
                "planned": int,
                "revealed": int,
                "cancelled": int,
                "overdue": [
                    {"policy_id": str, "target_kind": str, "target_id": str,
                     "reveal_by_chapter": int, "audience": str},
                    ...
                ],
            },
            "alerts": [ {"level": "warn"|"fail", "code": str, "message": str}, ... ],
        }``

    异常：
        ``ValueError`` — project 不存在（router 转 404）。
        字段全部不抛异常：plan_json 缺失 / key_beats 形态异常 / 无 quality_report / 无
        draft 一律填空值（``None`` / ``False`` / ``0``）。
    """

    db_path = str(db_path)

    # 1) project 存在性
    proj = ProjectService(db_path).get(project_id)
    if proj is None:
        raise ValueError(f"project {project_id!r} not found")

    conn = get_connection(db_path)
    try:
        # 2) chapters（按 number ASC）
        chapter_rows = conn.execute(
            "SELECT chapter_id, number, title, plan_json FROM chapters "
            "WHERE project_id = ? ORDER BY number ASC",
            (project_id,),
        ).fetchall()

        chapter_ids: list[str] = [r["chapter_id"] for r in chapter_rows]
        chapter_no_by_id: dict[str, int] = {r["chapter_id"]: int(r["number"]) for r in chapter_rows}

        # 3) 解析 key_beats 容错（同步记录 chapter_id，便于回填 quality/draft）
        chapter_payload: list[dict[str, Any]] = []
        for r in chapter_rows:
            plan_raw = r["plan_json"] or "{}"
            try:
                plan = json.loads(plan_raw) if isinstance(plan_raw, str) else {}
            except json.JSONDecodeError:
                plan = {}
            if not isinstance(plan, dict):
                plan = {}
            key_beats = plan.get("key_beats", [])
            has_payoff, has_charge = _parse_beat_flags(key_beats)
            chapter_payload.append(
                {
                    "chapter_id": r["chapter_id"],
                    "number": int(r["number"]),
                    "title": r["title"] or "",
                    "has_payoff_beat": has_payoff,
                    "charge_beat": has_charge,
                }
            )

        # 4) quality_reports / drafts 最新一条
        quality_map = _load_latest_quality_per_chapter(conn, chapter_ids)
        drafts_map = _load_latest_draft_chars_per_chapter(conn, chapter_ids)
        for ch in chapter_payload:
            q = quality_map.get(ch["chapter_id"], {"overall": None, "pacing": None})
            ch["overall"] = q.get("overall")
            ch["pacing"] = q.get("pacing")
            ch["prose_chars"] = drafts_map.get(ch["chapter_id"])
        # 输出时把 chapter_id 内部字段脱敏（不在 JSON 中暴露）
        for ch in chapter_payload:
            ch.pop("chapter_id", None)

        # 5) payoff 段：total / 连击 / last
        payoff_total = sum(1 for c in chapter_payload if c["has_payoff_beat"])
        streak_metrics = _count_charge_streaks(chapter_payload)

        # 6) hooks / debts 摘要（用 chapter_no_by_id + 最大章号算 overdue）
        overdue_threshold = _project_overdue_chapters(conn, project_id)
        current_max_chapter_no = max(chapter_no_by_id.values()) if chapter_no_by_id else None
        hooks_summary = _summarize_hooks(
            conn, project_id, overdue_threshold, chapter_no_by_id, current_max_chapter_no
        )
        debts_summary = _summarize_debts(conn, project_id)
        # V3.3 P0-2：reveal_policies 摘要 + overdue 清单
        reveal_policies_summary = _summarize_reveal_policies(
            conn, project_id, current_max_chapter_no,
        )
    finally:
        conn.close()

    # 7) alerts
    alerts = _compute_alerts(chapter_payload, streak_metrics, hooks_summary)

    return {
        "project_id": project_id,
        "generated_at": now_iso(),
        "chapters": chapter_payload,
        "payoff": {
            "total": payoff_total,
            "charge_streak": streak_metrics["charge_streak"],
            "max_charge_streak": streak_metrics["max_charge_streak"],
            "last_payoff_chapter": streak_metrics["last_payoff_chapter"],
        },
        "hooks": hooks_summary,
        "debts": debts_summary,
        # V3.3 P0-2：reveal_policies 摘要与到期未揭示清单
        "reveal_policies": reveal_policies_summary,
        "alerts": alerts,
    }


__all__ = ["build_arc_view"]
