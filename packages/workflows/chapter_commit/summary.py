"""章节摘要节点（summarizer 调用与落库）
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import json
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.model_router.router import capability_for

# 摘要最大字符数（中文按字符）；超长由 service 层截断 + 标 degraded。
_SUMMARY_MAX_CHARS = 200
# tail_text 取已提交正文末尾字符数（不调 LLM）。
_TAIL_TEXT_CHARS = 300


def _prepare_summarizer_call(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """抽取 summarizer LLM 调用所需的全部准备产物（V3.7）。

    把 ``_summarize_node`` 中「调 LLM 之前」的步骤下沉：取章节 + 草稿 + 计划 goal +
    组装 ``summary_payload`` + 取 mock_script。供 observer 节点提前并发调用，
    命中后下游 ``_summarize_node`` 短路消费 ``ctx['summary_early']``。

    返回 ``None`` 表示必需输入缺失（章节不存在 / 草稿为空），调用方应跳过本次早产调用
    而非抛错。键序与原 ``_summarize_node`` 内联构造时保持一致——尤其是 ``chapter`` 子
    dict 的 ``chapter_goal / chapter_no / chapter_id`` 顺序（缓存重排对齐）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("summarizer")

    # 1) 章节 + 项目 + chapter_no
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT project_id, number FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if chap_row is None:
            return None
        project_id = chap_row["project_id"]
        chapter_no = int(chap_row["number"] or 0)
        draft_row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    content = (dict(draft_row) if draft_row else {}).get("content") or ""
    if not content.strip():
        return None

    # 2) tail_text（不调 LLM）
    tail_text = content[-_TAIL_TEXT_CHARS:] if len(content) > _TAIL_TEXT_CHARS else content

    # 3) plan_goal（读取失败不阻塞）
    plan_goal = ""
    try:
        conn = get_connection(db_path)
        try:
            pj = conn.execute(
                "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if pj:
            raw = pj["plan_json"] or "{}"
            try:
                pj_d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                pj_d = {}
            plan_goal = (pj_d or {}).get("chapter_goal") or ""
    except Exception:  # noqa: BLE001 —— 计划读取失败不阻塞 summarize
        plan_goal = ""

    # 键序固定：agent → prompt_version → chapter{chapter_goal, chapter_no, chapter_id}
    # → prose_excerpt → tail_text（缓存重排对齐）。
    summary_payload: dict[str, Any] = {
        "agent": "summarizer",
        "prompt_version": "summarizer:v1",
        "chapter": {
            "chapter_goal": plan_goal,
            "chapter_no": chapter_no,
            "chapter_id": chapter_id,
        },
        "prose_excerpt": content[:4000],  # 取前 4000 字足够上下文（避免超长 prompt）
        "tail_text": tail_text,
    }
    return {
        "payload": summary_payload,
        "mock": mock_script,
        "project_id": project_id,
        "chapter_no": chapter_no,
        "content": content,
        "tail_text": tail_text,
    }


def _resolve_summary_out(
    out: Any,
    *,
    summary_max_chars: int,
) -> tuple[str, bool]:
    """把 summarizer LLM 输出 dict 解析为 ``(summary_text, degraded)``。

    解析失败抛 ``ValueError``，由调用方按已有 degraded 分支处理。
    """
    if not isinstance(out, dict):
        raise ValueError(f"summarizer output not dict: {type(out).__name__}")
    candidate = out.get("summary")
    if not isinstance(candidate, str):
        raise ValueError("summarizer output missing 'summary' string")
    summary_text = candidate.strip()
    if not summary_text:
        raise ValueError("summarizer output 'summary' empty")
    degraded = False
    if len(summary_text) > summary_max_chars:
        summary_text = summary_text[:summary_max_chars]
        degraded = True
    return summary_text, degraded


def _insert_chapter_summary_row(
    db_path: Any,
    *,
    project_id: str,
    chapter_id: str,
    chapter_no: int,
    summary_text: str,
    tail_text: str,
) -> str:
    """落库 ``chapter_summaries`` 行，返回新生成的 ``summary_id``。"""
    summary_id = new_id("sum")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapter_summaries
                (summary_id, project_id, chapter_id, chapter_no,
                 summary, tail_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                project_id,
                chapter_id,
                chapter_no,
                summary_text,
                tail_text,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return summary_id


def _summarize_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """summarize 节点（Sprint 14；V3.7：可选短路消费 ``summary_early``）—— commit 成功后追加。

    行为：
    1. 若 ``ctx['summary_early']`` 存在且 ``not early.get('skipped')``：跳过 ``run_agent``，
       直接复用「解析 out → 截断 → 落库」段。
    2. 否则走原路径：先 :func:`_prepare_summarizer_call` 取准备产物（缺失则 skipped）→
       ``run_agent('summarizer', ...)`` → 解析 ``{"summary"}`` → 写 ``chapter_summaries`` 表
       → 返回 ``{"summary_status","summary_id","degraded"}``。内部已有 PromptNotFoundError
       等降级路径（degraded=True 不抛错）。
    3. 任何异常（LLM 失败 / JSON 解析失败 / DB 写入失败）→ 降级：warning 日志 +
       summary_status='failed' + 不抛错（保证 chapter 提交不因摘要失败而 FAILED）。

    返回 ``{"summary_status": "ok|failed|skipped", "summary_id": str|None, "degraded": bool}``。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]

    # V3.7：短路消费 observer 提前并发产出的 summary_early。
    early = ctx.get("summary_early")
    if isinstance(early, dict) and not early.get("skipped"):
        out = early.get("output")
        try:
            summary_text, degraded = _resolve_summary_out(
                out, summary_max_chars=_SUMMARY_MAX_CHARS,
            )
        except Exception as exc:  # noqa: BLE001
            # V3.7 修复 A1：短路路径解析失败 → fallthrough 自愈（重新 prepare +
            # run_agent），与「缺字段分支」一致；自然走到原路径的 degraded 兜底
            # （run_agent 异常 → summary_status='failed'）。
            import logging
            logging.getLogger(__name__).warning(
                "chapter_commit.summarize early output malformed, fallback to "
                "self-heal: chapter_id=%s err=%s", chapter_id, exc,
            )
            early = None  # fallthrough 到下方原路径 self-heal
        else:
            # 解析成功 → 落库所需字段从 early 附带（observer 节点透传 prepared 上下文）。
            project_id = early.get("project_id")
            chapter_no = int(early.get("chapter_no") or 0)
            tail_text = early.get("tail_text") or ""
            if not project_id or not tail_text:
                # 早产数据缺关键字段 → 走自愈路径（重新自己 prepare）
                early = None  # fallthrough 到下方原路径 self-heal
            else:
                try:
                    summary_id = _insert_chapter_summary_row(
                        db_path,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        chapter_no=chapter_no,
                        summary_text=summary_text,
                        tail_text=tail_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(
                        "chapter_commit.summarize early DB insert failed: "
                        "chapter_id=%s err=%s", chapter_id, exc,
                    )
                    return {
                        "summary_status": "failed",
                        "summary_id": None,
                        "degraded": False,
                        "summary_error": str(exc),
                    }
                return {
                    "summary_status": "ok",
                    "summary_id": summary_id,
                    "degraded": degraded,
                }

    # 原路径：自己 prepare + run_agent + 写库。
    prepared = _prepare_summarizer_call(ctx)
    if prepared is None:
        return {"summary_status": "skipped", "summary_id": None, "degraded": False}

    mock_script = prepared["mock"]
    summary_payload = prepared["payload"]

    degraded = False
    summary_text = ""
    try:
        # summarizer 无 ACTIVE prompt 时 runner 会抛 PromptNotFoundError → 降级。
        out = run_agent(
            db_path,
            "summarizer",
            summary_payload,
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="summarizer",
            mock_script=mock_script,
            # V3.9.4：summarizer 单次 run 级覆盖走 light 键透传
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("summarizer")
            ),
        )
        summary_text, degraded = _resolve_summary_out(
            out, summary_max_chars=_SUMMARY_MAX_CHARS,
        )
    except Exception as exc:  # noqa: BLE001 —— 降级：任何失败不抛
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    # 4) 落库 chapter_summaries
    try:
        summary_id = _insert_chapter_summary_row(
            db_path,
            project_id=prepared["project_id"],
            chapter_id=chapter_id,
            chapter_no=int(prepared["chapter_no"]),
            summary_text=summary_text,
            tail_text=prepared["tail_text"],
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize DB insert failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    return {
        "summary_status": "ok",
        "summary_id": summary_id,
        "degraded": degraded,
    }
