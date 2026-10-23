"""番茄签约体检 REST 路由。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET /projects/{project_id}/signing-check`` —— 现场组装检查项 + summary，返回 dict。

错误码映射：
- 404 — project 不存在（service 抛 ``ValueError``，router 转 404）；
- 500 — DB 异常。

设计要点：
- 复用 :func:`packages.core.signing_check.service.run_signing_check`；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 quality.py / export.py 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
- 注：未给 router 加 ``prefix``，因 ``main.py`` 已统一 prepend ``/api``，否则会变成 ``/api/api/...``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from packages.core.logging_config import get_logger
from packages.core.signing_check.service import run_signing_check

log = get_logger("novelos.routers.signing_check")

router = APIRouter(tags=["signing-check"])


# ---------------------------------------------------------------------------
# 出参模型（2026-10-15 批次（六））
# ---------------------------------------------------------------------------
# 形状来源：临时库 + 合成夹具跑通端点后 dump 实际键集。实测结论：
#   恒在 7 键：project_id / project_name / total_chars / generated_at /
#              items / summary / word_band_source
#   条件键 4 个：word_band_rejected（包声明了 chapter_word_band 但被 normalize
#                拒绝时置位）、genre_opening（绑定题材包且 payload 声明了
#                opening_rules 时整段出现）、genre_opening_unavailable +
#                genre_opening_error（读题材包失败 ⇒ **不可检**标记，D-13）——
#                缺席时**键不存在**，不是 null。
#   items[] 恒 4 键；summary 恒 4 键；genre_opening 恒 9 键；
#   genre_opening.rules[] 恒 6 键，keywords_source 仅在抽出/自带词表时出现
#   （实测：「必须出现」这类纯噪声要求抽不出词 ⇒ 缺席；「…倒计时」⇒ 出现）。
#
# ⚠️ **2026-10-23 返工（concern 3）**：``genre_opening_unavailable`` /
# ``genre_opening_error`` 此前**只在 service 返回的 dict 里**存在，**没进本模型**
# ⇒ ``response_model_exclude_unset=True`` 把它们**过滤掉** ⇒ API 消费者永远看不到
# 「题材开篇检查没跑」这个事实（报告外观仍是「全部通过」）。
# ⇒ 两个键必须**进模型**，且**顶层可见**（不是塞进 items 里当普通条目）。
#
# ⇒ 端点配 ``response_model_exclude_unset=True``：否则缺席的条件键会被渲染成
#   凭空的 null，「未绑定题材包」与「绑定了但包没写 opening_rules」就再也分不开
#   ——而这两者对作者是完全不同的提示。


class SigningCheckItem(BaseModel):
    """单条体检项（``signing_check.checks.CheckItem`` 的 dict 投影，恒 4 键）。"""

    key: str
    level: str
    detail: str
    advice: str


class SigningCheckSummary(BaseModel):
    """四档计数（``level`` 分桶；恒 4 键）。"""

    pass_count: int
    warn_count: int
    fail_count: int
    info_count: int


class GenreOpeningRule(BaseModel):
    """题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。

    ``status ∈ pass / fail / unverifiable / not_written``。``keywords_source``
    是**条件键**：``"curated"``（用规则自带词表）/ ``"auto"``（从规则文本抽词）；
    抽不出任何词时该键缺席（此时 status 必为 ``unverifiable``，由人工核对）。
    """

    check_id: str
    chapter_no: int | None = None
    description: str
    requirement: str
    status: str
    detail: str
    keywords_source: str | None = None


class GenreOpeningSection(BaseModel):
    """题材开篇检查段（恒 9 键）。"""

    pack_id: str
    pack_name: str
    rule_count: int
    rules: list[GenreOpeningRule]
    pass_count: int
    fail_count: int
    unverifiable_count: int
    info_count: int


class SigningCheckResult(BaseModel):
    """``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。"""

    project_id: str
    project_name: str
    total_chars: int
    generated_at: str
    items: list[SigningCheckItem]
    summary: SigningCheckSummary
    # "<pack_id>@<version>" 或 "default"
    word_band_source: str
    word_band_rejected: bool | None = None
    genre_opening: GenreOpeningSection | None = None
    # 题材开篇检查**不可检**（读题材包失败）：布尔标记 + 原因串（D-13）。
    # 未绑定题材包 / 包未声明规则时**两个键都缺席**（行为零变化）。
    genre_opening_unavailable: bool | None = None
    genre_opening_error: str | None = None


@router.get(
    "/projects/{project_id}/signing-check",
    response_model=SigningCheckResult,
    response_model_exclude_unset=True,
)
def get_signing_check(project_id: str, request: Request) -> dict:
    """返回该项目的签约体检结果（含 items / summary）。

    - project 不存在 → 404 ``{"detail": "project '...' not found"}``；
    - 成功 → 200 + ``run_signing_check`` 返回的 dict（经 :class:`SigningCheckResult`
      过滤；两个条件键缺席时保持缺席，见模型区注释）。
    """

    db_path = str(request.app.state.settings.db_path)
    try:
        return run_signing_check(db_path, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("signing check failed: %s", exc)
        raise HTTPException(status_code=500, detail="signing check failed") from exc


__all__ = ["router"]
