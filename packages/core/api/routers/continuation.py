"""章节续写多版本（Scene Beats 风格现场续写）REST 路由。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``POST /projects/{project_id}/chapters/{chapter_id}/continue`` ——
  基于当前草稿末尾 ~1200 字 + 本章 ``plan_json.key_beats`` 的 purpose 列表，
  按 ``creative_writing`` capability 调 ``num_variants`` 次 LLM，每次温度
  分别取 0.8 / 1.0 / 1.2（按 variant index 循环），返回候选段落列表。
- ``POST /projects/{project_id}/chapters/{chapter_id}/continue/adopt`` ——
  作者从候选中挑选一段（前端已展示原文，本接口只接收纯文本），
  拼到最新 draft 之后插入新 draft 行，``version = max+1``；
  若 chapter.status == REVIEWED 则降级为 DRAFTED（采纳后需重审）。

错误码映射：
- 422 — body 字段非法（pydantic 校验：num_variants 不在 1-3、content 为空或 >20000）。
- 404 — project / chapter 不存在，或 chapter 不属于该 project。
- 409 — chapter.status ∉ {DRAFTED, REVIEWED}，detail: ``"chapter status <X> not continuable"``。
- 502 — ``continue`` 端点的 num_variants 次调用全部失败。

设计要点：
- 模块顶层 ``router = APIRouter(tags=["continuation"])``，不加 ``prefix``，
  ``discover_routers`` 统一挂 ``/api``（参照 ``signing_check.py``）。
- 复用 :class:`ChapterService`：草稿的读 / 写由 ``list_drafts`` / ``create_draft`` 完成；
  router 层只做 HTTP 包装与错误码映射。
- ``call_with_fallback("creative_writing", messages, params={"temperature": ...})``
  是 :class:`ModelRouter` 的标准入口（``packages.core.model_router.router``）；
  mock provider 走 ``list_enabled`` 命中第一条 enabled=1 行，单次调用零耗时。
- 单次调用失败不中断整体：每个 variant 独立 try/except，失败时该 variant
  返回 ``{"index": i, "text": null, "tokens": null, "elapsed_ms": int,
  "error": "<msg>"}``；全部失败才 raise HTTPException(502)。
- ``db_path`` 走 ``request.app.state.settings.db_path``（与其他 router 一致）。

设计取舍：
1. **不入 ai_call_logs 的原因** —— ``ai_call_logs.run_id`` 是 NOT NULL FK，
   本 API 是用户驱动的「现场续写」调用，不属于任何 workflow / agent run；
   若要可观测，未来补 ``chapter_continuation_logs`` 之类专用表更合适。
2. **REVIEWED → DRAFTED 降级语义** —— 采纳续写内容后正文已变，原 REVIEWED
   失效，必须降级让用户重新审稿。:data:`ALLOWED_NEXT["REVIEWED"]` 不含 DRAFTED
   （只有 PLANNED/REVIEWED/COMMITTED），所以本接口不走
   :meth:`ChapterService.update`（会触发 :class:`ChapterTransitionError`），
   而是直接 UPDATE chapters SET status='DRAFTED'。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.core.model_router import ModelRouter
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.continuation")

router = APIRouter(tags=["continuation"])


# ---------------------------------------------------------------------------
# Pydantic models（私有，仅本 router 内部使用）
# ---------------------------------------------------------------------------


class ContinueRequest(BaseModel):
    """续写请求体。

    - ``num_variants`` 1-3 候选数，默认 3。
    - ``instruction`` 用户附加指令（可空字符串），默认空。
    """

    num_variants: int = Field(default=3, ge=1, le=3)
    instruction: str = Field(default="", max_length=2000)


class ContinueVariant(BaseModel):
    """单个候选 variant 的响应字段。"""

    index: int
    text: str | None
    tokens: int | None
    elapsed_ms: int
    error: str | None = None


class ContinueResponse(BaseModel):
    """续写响应。"""

    variants: list[ContinueVariant]
    model: str
    total_elapsed_ms: int


class AdoptRequest(BaseModel):
    """采纳请求体：纯文本追加到最新草稿尾部。"""

    content: str = Field(..., min_length=1, max_length=20000)


class AdoptResponse(BaseModel):
    """采纳响应。"""

    version: int
    status: str
    appended_chars: int


# ---------------------------------------------------------------------------
# 共享辅助函数
# ---------------------------------------------------------------------------

# 续写温度序列（任务书钉死），index 超出时循环。
_TEMPERATURE_CYCLE: tuple[float, ...] = (0.8, 1.0, 1.2)

# 上文最大字符数（任务书给死）。
_CONTEXT_TAIL_CHARS = 1200

# status 白名单（任务书给死）。
_CONTINUABLE_STATUSES: frozenset[str] = frozenset({"DRAFTED", "REVIEWED"})


def _ensure_project(request: Request, project_id: str) -> None:
    """project 不存在 → 404。"""
    svc = ProjectService(request.app.state.settings.db_path)
    if svc.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")


def _resolve_chapter(
    request: Request, project_id: str, chapter_id: str
) -> dict[str, Any]:
    """校验 chapter 存在 + 归属 + status 可续写/采纳，返回完整 chapter dict。

    错误码：
    - 404 — chapter 不存在 / 不属于该 project
    - 409 — chapter.status ∉ {DRAFTED, REVIEWED}
    """
    chapter = ChapterService(request.app.state.settings.db_path).get(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    if chapter.get("project_id") != project_id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} does not belong to project {project_id!r}"
            ),
        )
    status_value = chapter.get("status")
    if status_value not in _CONTINUABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"chapter status {status_value} not continuable",
        )
    return chapter


def _extract_purposes(plan_json: dict | None) -> list[str]:
    """从 chapter.plan_json.key_beats 抽 purpose 字符串列表。

    key_beats 可能是 dict（含 ``purpose`` 键，值为 list[str] / str）或
    list[dict]（每项含 ``purpose``）。格式异常一律降级为 ``[]``。
    """
    if not isinstance(plan_json, dict):
        return []
    key_beats = plan_json.get("key_beats")
    purposes: list[Any] = []
    if isinstance(key_beats, dict):
        raw = key_beats.get("purpose")
        if isinstance(raw, str):
            purposes = [raw]
        elif isinstance(raw, list):
            purposes = list(raw)
    elif isinstance(key_beats, list):
        for beat in key_beats:
            if isinstance(beat, dict):
                p = beat.get("purpose")
                if p is not None:
                    purposes.append(p)
            elif isinstance(beat, str):
                purposes.append(beat)
    # 仅保留非空字符串
    return [p for p in purposes if isinstance(p, str) and p.strip()]


def _latest_draft_text(drafts: list[dict] | None) -> str:
    """取最新 draft（list_drafts 已按 version DESC）的 content。无 draft 返回 ``""``。"""
    if not drafts:
        return ""
    first = drafts[0]
    return first.get("content") or ""


def _build_messages(
    chapter: dict[str, Any],
    last_text: str,
    instruction: str,
) -> list[dict[str, Any]]:
    """构造 writer 风格 prompt 的 messages 列表。

    - system：风格说明 + 本章目标节拍 + 用户附加指令（若有）
    - user：上文（末尾 ~1200 字）
    """
    purposes = _extract_purposes(chapter.get("plan_json"))
    beats_block = ""
    if purposes:
        beats_block = "\n本章目标节拍：\n" + "\n".join(f"- {p}" for p in purposes)
    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = f"\n作者额外要求：{instruction.strip()}"
    system_text = (
        "以下是一部小说当前章节的结尾与本章目标节拍，请续写约 300-500 字的"
        "正文片段，直接给正文不要解释。"
        f"{beats_block}{instruction_block}"
    )
    tail = last_text[-_CONTEXT_TAIL_CHARS:] if last_text else ""
    user_text = f"上文：\n{tail}"
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def _extract_tokens(completion: dict[str, Any] | None) -> int | None:
    """从 completion.usage 抽 token 数；缺失或非 int → None。"""
    if not completion:
        return None
    usage = completion.get("usage") or {}
    for key in ("completion", "total"):
        val = usage.get(key)
        if isinstance(val, int):
            return val
    return None


# ---------------------------------------------------------------------------
# 端点 1: continue
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/continue",
    response_model=ContinueResponse,
)
def continue_chapter(
    project_id: str,
    chapter_id: str,
    payload: ContinueRequest,
    request: Request,
) -> dict[str, Any]:
    """基于最新草稿 + 本章 plan 调 N 次 LLM，返回候选段落列表。

    - project / chapter 不存在或归属错 → 404。
    - chapter.status ∉ {DRAFTED, REVIEWED} → 409。
    - num_variants ∉ [1,3] → 422（pydantic）。
    - 单次 LLM 失败：记 error，整体仍 200。
    - 全部 variant 失败 → 502。
    """
    db_path = str(request.app.state.settings.db_path)
    _ensure_project(request, project_id)
    chapter = _resolve_chapter(request, project_id, chapter_id)

    # 读最新 draft（可能为空）
    drafts = ChapterService(db_path).list_drafts(chapter_id)
    if drafts is None:
        # 章节刚 resolve 过理论上不会走到这里；防御性兜底
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    last_text = _latest_draft_text(drafts)

    messages = _build_messages(chapter, last_text, payload.instruction)

    router_obj = ModelRouter(db_path)
    variants: list[dict[str, Any]] = []
    used_model: str = "unknown"
    started = time.perf_counter()

    for i in range(payload.num_variants):
        temperature = _TEMPERATURE_CYCLE[i % len(_TEMPERATURE_CYCLE)]
        per_start = time.perf_counter()
        text: str | None = None
        tokens: int | None = None
        error_msg: str | None = None
        try:
            completion, used_config = router_obj.call_with_fallback(
                "creative_writing",
                messages,
                params={"temperature": temperature},
            )
            used_model = used_config.get("model") or used_model
            text = completion.get("text") or ""
            tokens = _extract_tokens(completion)
        except Exception as exc:  # noqa: BLE001 —— 单次失败仅记日志
            error_msg = f"{type(exc).__name__}: {exc}"
            log.warning(
                "continuation variant failed: index=%d error=%s",
                i,
                error_msg,
            )
        elapsed_ms = int((time.perf_counter() - per_start) * 1000)
        variant: dict[str, Any] = {
            "index": i,
            "text": text,
            "tokens": tokens,
            "elapsed_ms": elapsed_ms,
        }
        if error_msg is not None:
            variant["error"] = error_msg
        variants.append(variant)

    total_elapsed_ms = int((time.perf_counter() - started) * 1000)

    # 全部失败 → 502（detail 汇总每个 variant 的 error）
    if all(v.get("error") for v in variants):
        joined = " | ".join(
            f"#{v['index']}:{v.get('error')}" for v in variants
        )
        raise HTTPException(
            status_code=502,
            detail=f"all variants failed: {joined}",
        )

    return {
        "variants": variants,
        "model": used_model,
        "total_elapsed_ms": total_elapsed_ms,
    }


# ---------------------------------------------------------------------------
# 端点 2: continue/adopt
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/continue/adopt",
    response_model=AdoptResponse,
)
def adopt_continuation(
    project_id: str,
    chapter_id: str,
    payload: AdoptRequest,
    request: Request,
) -> dict[str, Any]:
    """把候选正文追加为新 draft；REVIEWED → DRAFTED 降级。

    - 校验同 continue（404 / 409）。
    - 新 draft 的 version = ``COALESCE(MAX(version), 0) + 1``；content =
      ``old + "\\n" + new``（old 为空则仅 new）。
    - REVIEWED → DRAFTED 降级由 ``ChapterService.create_draft`` 内部同事务完成，
      本接口不再写 chapters.status（避免重复降级 & 绕过 ALLOWED_NEXT 白名单）。
    """
    db_path = str(request.app.state.settings.db_path)
    _ensure_project(request, project_id)
    chapter = _resolve_chapter(request, project_id, chapter_id)

    svc = ChapterService(db_path)

    # 读最新 draft
    drafts = svc.list_drafts(chapter_id)
    if drafts is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    old_text = _latest_draft_text(drafts)

    new_content = (
        f"{old_text}\n{payload.content}" if old_text else payload.content
    )

    # create_draft 内部已处理 DraftStatusNotAllowed / DraftVersionConflict；
    # 本接口前置 status 校验已保证 chapter 在白名单内，正常路径不会触发 409。
    created = svc.create_draft(chapter_id, new_content)
    if created is None:
        # 防御性兜底：章节在校验后消失（并发删除）。
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    new_version = int(created["version"])

    # 响应 status：create_draft 已处理 REVIEWED → DRAFTED 降级，
    # 这里按预取的预审状态给出最终态，避免响应与 DB 不一致。
    prior_status = chapter.get("status") or "DRAFTED"
    new_status = "DRAFTED" if prior_status == "REVIEWED" else prior_status

    return {
        "version": new_version,
        "status": new_status,
        "appended_chars": len(payload.content),
    }


__all__ = ["router"]
