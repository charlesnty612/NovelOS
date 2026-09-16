"""摘要链 / 尾段 / 文风样例 / L2 计划与场景形态 helper（拆分自 builders_common.py，2026-09-13 V4.0）。

- 摘要链：``_recent_chapter_summaries`` + ``_truncate_summaries_to_token_budget``
  （V3.9 批次 2.1：预算由调用方以具名常量传入，截断成为真约束）；
- 原文尾段：``_recent_prose_tail``（上一章末尾 500 字）/ ``_previous_chapter_tail``（300 字）；
- 作者文风样例：``_author_style_samples``（Sprint 15 / V1.3）；
- observer 用 ``_director_plan_summary``；writer 用 ``_inject_scene_word_budget``。

**同卷过滤口径（快穿位面隔离，2026-09-16）**：近窗三类取数——摘要链
``_recent_chapter_summaries``、director 前章尾段 ``_previous_chapter_tail``、writer 上一章
正文尾段 ``_recent_prose_tail``——都**按「与当前章同卷」过滤**（判据 ``chapters.volume_id``）。
理由是快穿换位面后，新卷第一章要的是**冷开场**：上一世只经结算单 / 作者意图一笔带过，
不该以「近窗 / 上一章」的姿态出现在正文上下文里（同形回灌面还有
``retrieval.service.search`` 召回）。当前章未挂卷（``volume_id IS NULL``）或旧库缺该列 →
不过滤 = 旧行为逐字保留（卷内各章同卷，卷 1 与存量项目零行为变化）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .common import _TOKEN_DIVISOR, get_connection


def _volume_id_of_chapter(conn: sqlite3.Connection, chapter_id: str) -> str | None:
    """按 ``chapter_id`` 定位当前章，返回其所属卷 ``volume_id``；解析不出 → ``None``。

    ``None`` 的全部来源都表示「无从判断当前章属哪一卷」，此时调用方**不做同卷过滤**
    （保持 2026-09-16 之前的旧行为）：章节不存在 / 章节未挂卷（``volume_id IS NULL``，
    迁移 0015 允许）/ 旧库缺 ``chapters.volume_id`` 列（迁移 0015 之前，读列直接
    ``OperationalError`` → 就地降级，不让装配崩）。
    """
    try:
        row = conn.execute(
            "SELECT volume_id FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:  # 旧库缺列 → 保持旧行为（不过滤）
        return None
    if row is None:
        return None
    return row["volume_id"] or None


def _volume_id_of_chapter_no(
    conn: sqlite3.Connection, project_id: str, chapter_no: int | None,
) -> str | None:
    """按 ``(project_id, number)`` 定位当前章，返回其所属卷；解析不出 → ``None``。

    口径与 :func:`_volume_id_of_chapter` 逐条一致（含未挂卷 / 旧库缺列 → ``None``），
    两者只差定位键：本函数服务于只拿到章号的调用方（摘要链 / 前章尾段）。
    ``chapters`` 只有 ``(project_id, number)`` 普通索引、无唯一约束，重号时按
    ``chapter_id`` 升序取首行（确定性强，与既有 ``number < ?`` 取数口径同宽松度）。
    """
    if chapter_no is None:
        return None
    try:
        row = conn.execute(
            "SELECT volume_id FROM chapters "
            "WHERE project_id = ? AND number = ? "
            "ORDER BY chapter_id ASC LIMIT 1",
            (project_id, int(chapter_no)),
        ).fetchone()
    except sqlite3.OperationalError:  # 旧库缺列 → 保持旧行为（不过滤）
        return None
    if row is None:
        return None
    return row["volume_id"] or None


def _recent_prose_tail(db_path: str | Path, chapter_id: str, length: int = 500) -> str:
    """取上一章（**同卷内** chapter number 小一号的）最新 draft 末尾 length 字符；无则返回 ""。

    同卷过滤（快穿位面隔离，2026-09-16）：候选章必须与当前章同 ``volume_id``——
    换卷后新卷第一章不该把上一世的正文尾段当「上一章」注入（快穿冷开场口径）。
    当前章未挂卷 / 旧库缺 ``chapters.volume_id`` 列 → 不过滤（旧行为逐字保留）。
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT number, project_id FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if cur is None:
            return ""
        # 同卷谓词只在解析出卷时才进 SQL 文本：旧库缺列时读列已失败（volume_id=None），
        # 若照常引用该列，SQLite 在 prepare 阶段就会报 no such column。
        volume_id = _volume_id_of_chapter(conn, chapter_id)
        volume_predicate = "AND volume_id = ?" if volume_id is not None else ""
        prev_params: list[Any] = [cur["project_id"], cur["number"]]
        if volume_id is not None:
            prev_params.append(volume_id)
        prev_row = conn.execute(
            """
            SELECT chapter_id FROM chapters
            WHERE project_id = ? AND number < ?
              {volume_predicate}
            ORDER BY number DESC LIMIT 1
            """.format(volume_predicate=volume_predicate),
            prev_params,
        ).fetchone()
        if prev_row is None:
            return ""
        draft_row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (prev_row["chapter_id"],),
        ).fetchone()
        if draft_row is None:
            return ""
        text = draft_row["content"] or ""
        return text[-length:] if len(text) > length else text
    finally:
        conn.close()


# 摘要链最近章数上限；超预算截断时优先砍最旧摘要。
# V3.9 批次 2.1：5 → 40。旧上限 5 × 每 200 字 ≈ 330 token，永远吃不满预算，
# 截断机制实为死代码；放宽后**预算成为真正的约束**（见 _DIRECTOR_SUMMARY_TOKEN_BUDGET
# 的最坏情况演算），条数上限退化为防御性硬顶。
_RECENT_SUMMARY_CAP = 40
# 摘要每条字符上限。
# V3.9 批次 2.1：200 → 400。生产写入端 summary 恒 ≤200 字
# （packages/workflows/chapter_commit/summary.py ``_SUMMARY_MAX_CHARS``，本批不改）；
# 此处放宽是为外部导入 / 历史超长行留余量，避免超长摘要整段直通注入。
_RECENT_SUMMARY_PER_CHARS = 400
# director 摘要链 token 预算（V3.9 批次 2.1：原为调用点写死的 800）。
# 最坏情况演算（证明机制真能触发）：
#   40 条 × (400 字摘要 + ~60 字 JSON 骨架) ≈ 18400 字符 ≈ 4600 token > 2000 → 截断触发；
#   生产口径（摘要 ≤200 字）：40 × 260 ≈ 10400 字符 ≈ 2600 token > 2000 → 长到 31 章
#   以后同样触发；中篇（≤20 章摘要）约 1300 token，不触发（全部保留）。
_DIRECTOR_SUMMARY_TOKEN_BUDGET = 2000


# writer 输入注入样例条数（取最近 N 篇）；每篇截断上限。
_STYLE_SAMPLES_CAP = 2
_STYLE_SAMPLE_PER_CHARS = 1000
# 引导语：注入 writer 输入时前缀；常量便于对齐测试与未来 i18n。
_AUTHOR_STYLE_SAMPLES_INSTRUCTION = (
    "以下为作者本人散文样例，请模仿其句式、用词与节奏（非内容）。"
)


def _recent_chapter_summaries(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    current_chapter_no: int | None,
) -> list[dict[str, Any]]:
    """取当前章之前最近 ``_RECENT_SUMMARY_CAP`` 章的摘要（chapter_no 倒序）。

    返回 ``[{"chapter_no": int, "summary": str, "chapter_id": str}]``。
    ``current_chapter_no`` 用于排除当前章自身（避免「自己摘要自己」）；传 None 时不过滤。
    超预算截断在调用方（按总 token 配额）执行，本函数只负责取数。

    同卷过滤（快穿位面隔离，2026-09-16）：只取与当前章同 ``volume_id`` 的章摘要。
    此前只看 ``chapter_no < ?``，于是换卷后新卷第一章会把上一世全部摘要当「近窗」
    吃进来——这是五处章号取数泄漏里**体量最大**的一处（实测 23 条摘要全是上一世）。
    当前章未挂卷 / 旧库缺 ``chapters.volume_id`` 列 → 不过滤（旧行为逐字保留，
    卷内各章同卷故卷 1 行为不变）。
    """
    volume_id = (
        _volume_id_of_chapter_no(conn, project_id, current_chapter_no)
        if current_chapter_no is not None else None
    )
    # 同卷谓词只在解析出卷时才进 SQL 文本（值仍一律走占位符）：旧库缺
    # ``chapters.volume_id`` 列时读列已失败（volume_id=None），若照常引用该列，
    # SQLite 在 prepare 阶段就会报 no such column——降级路径必须连文本都不提它。
    volume_predicate = (
        "AND chapter_id IN (SELECT chapter_id FROM chapters WHERE volume_id = ?)"
        if volume_id is not None else ""
    )
    sql = """
        SELECT chapter_id, chapter_no, summary
        FROM chapter_summaries
        WHERE project_id = ?
          AND {extra}
          {volume_predicate}
        ORDER BY chapter_no DESC
        LIMIT ?
    """.format(
        extra=("(chapter_no < ?)" if current_chapter_no is not None else "1=1"),
        volume_predicate=volume_predicate,
    )
    params: list[Any] = [project_id]
    if current_chapter_no is not None:
        params.append(int(current_chapter_no))
    if volume_id is not None:
        params.append(volume_id)
    params.append(_RECENT_SUMMARY_CAP)
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row_d = dict(r)
        text = (row_d.get("summary") or "").strip()
        if not text:
            continue
        out.append({
            "chapter_id": row_d["chapter_id"],
            "chapter_no": int(row_d["chapter_no"]),
            "summary": text[:_RECENT_SUMMARY_PER_CHARS],
        })
    return out


def _author_style_samples(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """取该项目最近 ``_STYLE_SAMPLES_CAP`` 篇文风样例，每篇截断 ≤ ``_STYLE_SAMPLE_PER_CHARS`` 字。

    返回 ``[{"sample_id": str, "title": str, "excerpt": str}]``；
    无样例时返回空 list（writer 装配按空态处理）。
    """
    rows = conn.execute(
        "SELECT sample_id, title, content FROM author_style_samples "
        "WHERE project_id = ? "
        "ORDER BY created_at DESC, sample_id DESC "
        "LIMIT ?",
        (project_id, _STYLE_SAMPLES_CAP),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        content = d.get("content") or ""
        excerpt = content[:_STYLE_SAMPLE_PER_CHARS] if len(content) > _STYLE_SAMPLE_PER_CHARS else content
        out.append({
            "sample_id": d["sample_id"],
            "title": d.get("title") or d["sample_id"],
            "excerpt": excerpt,
        })
    return out


def _previous_chapter_tail(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    current_chapter_no: int,
    length: int = 300,
) -> dict[str, Any]:
    """取前一章（**同卷内** chapter_no 小一号）的最新 draft 末尾 length 字（用于 L1 "前章尾段原文"）。

    返回 ``{"chapter_no": int, "chapter_id": str, "tail_text": str}``；
    无前章 → 空 dict（与 _recent_prose_tail 行为对齐，避免上游判空复杂度）。

    同卷过滤（快穿位面隔离，2026-09-16）：与 ``_recent_prose_tail`` / 摘要链同口径——
    换卷后新卷第一章取不到上一世末章尾段。当前章未挂卷 / 旧库缺列 → 不过滤。
    """
    volume_id = _volume_id_of_chapter_no(conn, project_id, current_chapter_no)
    volume_predicate = "AND volume_id = ?" if volume_id is not None else ""
    prev_params: list[Any] = [project_id, current_chapter_no]
    if volume_id is not None:
        prev_params.append(volume_id)
    prev = conn.execute(
        """
        SELECT chapter_id, number FROM chapters
        WHERE project_id = ? AND number < ?
          {volume_predicate}
        ORDER BY number DESC LIMIT 1
        """.format(volume_predicate=volume_predicate),
        prev_params,
    ).fetchone()
    if prev is None:
        return {}
    pd = dict(prev)
    draft = conn.execute(
        """
        SELECT content FROM drafts
        WHERE chapter_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (pd["chapter_id"],),
    ).fetchone()
    if draft is None:
        return {}
    text = (dict(draft).get("content") or "")
    if not text:
        return {}
    return {
        "chapter_no": int(pd["number"]),
        "chapter_id": pd["chapter_id"],
        "tail_text": text[-length:] if len(text) > length else text,
    }


def _truncate_summaries_to_token_budget(
    items: list[dict[str, Any]],
    *,
    available_tokens: int,
    token_divisor: int = _TOKEN_DIVISOR,
) -> tuple[list[dict[str, Any]], bool]:
    """按 token 预算截断摘要链（budget 由调用方传入，不再有模块级死常量）。

    - ``available_tokens``：本链可用的 token 预算（director 传
      ``_DIRECTOR_SUMMARY_TOKEN_BUDGET``；``<= 0`` 时全部砍掉）；
    - ``token_divisor``：与 :func:`_estimate_tokens` 同款口径（4 字节 ≈ 1 token）；
    - 策略：先砍最旧摘要（index 末尾 → 即 chapter_no 最小），每次重算整链成本，
      直到吃进预算或砍空。

    返回 ``(items_kept, truncated_bool)``；``truncated_bool`` 为 True 表示确有内容
    被预算砍掉（含「budget<=0 且原本非空」），供 ``_assembly_meta.summary_truncated``
    暴露给调用方观测。
    """
    if available_tokens <= 0:
        return [], bool(items)
    kept = list(items)
    while kept:
        cost = max(1, len(json.dumps(kept, ensure_ascii=False)) // token_divisor)
        if cost <= available_tokens:
            return kept, len(kept) < len(items)
        kept.pop()  # 砍最旧（最末尾）
    return kept, bool(items)


def _director_plan_summary(plan_json: dict[str, Any]) -> dict[str, Any]:
    """从 chapters.plan_json 抽取 director_plan_summary（给 observer 用）。"""
    return {
        "chapter_goal": plan_json.get("chapter_goal"),
        "key_beats": plan_json.get("key_beats", []),
        "character_changes_planned": plan_json.get("character_changes_planned", []),
        "hook_handling": plan_json.get("hook_handling", []),
        "debt_handling": plan_json.get("debt_handling", []),
    }


def _inject_scene_word_budget(
    scene_plan: dict[str, Any] | None, target_word_count: int,
) -> dict[str, Any]:
    """为每个 scene 注入 ``target_words`` 预算，透传给 writer。

    来源：scene_planner-v1 §6 Rule 7 要求每个 scene 必填 ``target_words``（整数，
    总和 = target_word_count 的 90~100%）。但降级 stub / 旧版 prompt 可能不填，
    这里做兜底：缺值场景按等分补齐（首场景补余数），已填则按"总数 90~110% 内
    归一化"重算——避免模型被自己瞎填的总和误导。

    输出 scene_plan 永远带 ``scenes[*].target_words`` 字段（恒为 int）；
    target_word_count<=0 时不补（无预算可言）。不影响 ``scene_id / purpose /
    characters`` 等既有字段；只做浅拷贝，不破坏原 scene_plan。
    """
    if not isinstance(scene_plan, dict):
        return {"scenes": []} if not isinstance(scene_plan, dict) else scene_plan  # type: ignore[return-value]
    scenes_raw = scene_plan.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        return scene_plan
    if target_word_count <= 0:
        # 无预算：原样返回（让 writer 走默认 ±15% 硬带）。
        return scene_plan

    scenes: list[dict[str, Any]] = [s for s in scenes_raw if isinstance(s, dict)]
    n = len(scenes)
    # 已声明的 target_words 收集（视作相对权重）
    declared: list[int | None] = []
    for s in scenes:
        tw = s.get("target_words")
        if isinstance(tw, int) and tw > 0:
            declared.append(tw)
        elif isinstance(tw, float) and tw.is_integer() and int(tw) > 0:
            declared.append(int(tw))
        else:
            declared.append(None)

    # 预算分摊（三条分支，保证 budget 恒为全 int，不出现 None）：
    # 1) 已声明子集和 ∈ [0.9T, 1.1T] → 已声明值原样保留（相对权重可信），
    #    未声明 scene 按「剩余预算（T − 已声明之和）均分」补齐；剩余 ≤ 0
    #    （子集已吃满 / 吃超预算）时给 T/n 的保底值（≥1 字）。
    #    scene_planner 契约不强制 per-scene target_words，部分声明的混合形态生产可达。
    # 2) 其余（全缺省 / 总和越界）→ 全场景等分，余数补首场景。
    valid = [d for d in declared if d is not None]
    if valid and 0.9 * target_word_count <= sum(valid) <= 1.1 * target_word_count:
        missing = n - len(valid)
        if missing:
            remaining = target_word_count - sum(valid)
            if remaining <= 0:
                fill = [max(1, target_word_count // n)] * missing
            else:
                base, rem = divmod(remaining, missing)
                fill = [base] * missing
                fill[0] += rem  # 余数补首个未声明场景（确定性强）
            fill_iter = iter(fill)
            budget = [d if d is not None else next(fill_iter) for d in declared]
        else:
            budget = list(declared)  # type: ignore[assignment]
    else:
        base, rem = divmod(target_word_count, n)
        budget = [base] * n
        # 余数补到首场景（确定性强）
        if rem and n > 0:
            budget[0] = budget[0] + rem

    out_scenes: list[dict[str, Any]] = []
    for idx, src in enumerate(scenes):
        new_scene = dict(src)
        new_scene["target_words"] = int(budget[idx])
        out_scenes.append(new_scene)
    out_plan = dict(scene_plan)
    out_plan["scenes"] = out_scenes
    return out_plan
