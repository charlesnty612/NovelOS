"""Model Profiles REST 路由（V3.7「模型档案 + 环节绑定」）。

挂在 ``/api`` 前缀下。对齐 ``model_profiles`` 表（``database/migrations/0016_model_profiles.sql``）。

端点：
- ``POST /model-profiles`` ——创建一条档案（201）。
- ``GET /model-profiles`` ——列出所有档案（``include_enabled_only`` 可选过滤）。
- ``GET /model-profiles/{id}`` ——按主键取单条（404 不存在）。
- ``PATCH /model-profiles/{id}`` ——部分更新（name / provider / model / params / enabled）。
- ``DELETE /model-profiles/{id}`` ——删除（204）；若被任何 binding 引用 → 409。
- ``POST /model-profiles/{id}/test`` ——ping：发「回复 ok」，返回延迟（ms）与首 100 字。

设计要点：
- 与 :mod:`packages.core.api.routers.model_configs` 同构：所有 CRUD 调用
  :class:`packages.core.model_router.ProfileService`，路由只做参数校验 + 错误映射。
- ``params_json`` 字段接受 JSON 字符串或 dict（前端友好）；写入时统一 ``json.dumps``。
- 读路径脱敏 / 写路径 ``api_key`` 特殊值（mask / 空字符串 / 新值）一律复用
  :mod:`packages.core.model_router.security`，与 ``/model-configs`` 行为一致。
- ``DELETE`` 前的 binding 引用检查由 :meth:`ProfileService.list_bindings_referencing` 提供：
  有引用 → 409，``detail`` 列出引用 capability 清单，便于前端展示与一键解绑。
- ``/test`` 端点直接构造对应 Provider，发一条最小消息；mock 直接 ok；其它走
  ``Provider.health_check``（与 model_configs 同语义）。
- enabled=0 的行仍可返回（与 binding 配合：disabled 的 profile 自动从 binding 候选链跳过）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router import ConnectionService, ModelRouter, ProfileService, ProviderError
from packages.core.model_router.effective import group_profiles_by_connection, parse_params
from packages.core.model_router.models import (
    AvailableModelsOut,
    ModelProfileOut,
    ProfileTestResult,
    ProviderErrorEnvelope,
)
from packages.core.model_router.providers import (
    AnthropicProvider,
    OllamaProvider,
    resolve_api_key,
)
from packages.core.model_router.security import (
    _dump_params_json,
    _mask_response,
    _normalize_params,
    _prepare_patch_params,
    _prepare_post_params,
)
from packages.core.secrets_store import resolve_profile_api_key

log = get_logger("novelos.routers.model_profiles")

# ---------------------------------------------------------------------------
# V3.8（拉取可用模型）内部常量
# ---------------------------------------------------------------------------

# 拉取模型列表总超时（秒）。各家列表接口通常很快，但需兜底防挂起。
_FETCH_LIST_TIMEOUT_S = 10.0


def _extract_ids_openai_compatible(data: object) -> list[str]:
    """OpenAI 兼容 /models 响应：``{"data": [{"id": "..."}, ...]}``。"""
    if not isinstance(data, dict):
        return []
    items = data.get("data")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for it in items:
        if isinstance(it, dict):
            v = it.get("id")
            if isinstance(v, str) and v:
                ids.append(v)
    return ids


def _extract_ids_anthropic(data: object) -> list[str]:
    """Anthropic /v1/models 响应：``{"data": [{"id": "..."}, ...]}``。"""
    return _extract_ids_openai_compatible(data)


def _extract_ids_ollama(data: object) -> list[str]:
    """Ollama /api/tags 响应：``{"models": [{"name": "..."}, ...]}``。"""
    if not isinstance(data, dict):
        return []
    items = data.get("models")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for it in items:
        if isinstance(it, dict):
            v = it.get("name")
            if isinstance(v, str) and v:
                ids.append(v)
    return ids


def _dedup_sorted(ids: list[str]) -> list[str]:
    """排序去重（保持稳定插入序以稳定输出，但用户体验上看到有序列表更直接）。"""
    return sorted({i for i in ids if isinstance(i, str) and i})


router = APIRouter(tags=["model_profiles"])


# ---------------------------------------------------------------------------
# 写入侧形状校验 + unknown-model 提示（task-28）
# ---------------------------------------------------------------------------


def _clean_model_field(raw: Any, *, field: str, logger: Any = None) -> str:
    """``model`` 字段：strip 后非空校验，**拒绝纯空白**。

    原判断是 ``isinstance(model, str) and model``——纯空白（``"   "``）是 truthy
    字符串，会被直接放行落库，到调用时远端才以 unknown model 400 收场，而那个
    400 在本文件被泛化成了「provider_error」。写入侧先把形状问题拦下。

    形状校验**只管「非空且非纯空白」**：不校验该模型名是否真实存在——可用模型随
    部署 / 账号 / 上下线状态变化，本地没有权威名表，硬编码白名单必然过期
    （AGENTS.md「可调项不得硬编码」）。名字对错由 ``/test`` 的远端响应判定。

    含前后空白 → 归一化落库并 ``log.warning`` 留痕：原值被静默改写，排查时需要
    能看出是谁写了带空格的模型名。

    ``logger``：``/model-configs`` 复用本函数但有自己的 logger（两个 router 各带
    自己的 ``novelos.routers.*`` 命名空间），由调用方传入以免 warning 打到邻居。
    共享 helper 的最终归属应是 :mod:`packages.core.model_router.security`（两个
    router 已共用其中的 ``_normalize_params`` 等），本批未扩白名单故先跨 router 引用。
    """
    warn = logger if logger is not None else log
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail=f"{field} must be a non-empty string")
    stripped = raw.strip()
    if not stripped:
        raise HTTPException(
            status_code=422,
            detail=f"{field} 不能为纯空白（strip 后为空）；请填写真实的模型名",
        )
    if stripped != raw:
        warn.warning("%s 含前后空白，已 strip 归一化：%r -> %r", field, raw, stripped)
    return stripped


# 远端 400 错误文本里「模型名问题」的公共形态（各 provider 措辞不同，取交集式关键词）。
# 这是**错误语义**匹配，不是模型名清单——可用模型随部署变化，本地不建名表。
_UNKNOWN_MODEL_MARKERS = (
    "unknown model",
    "model_not_found",
    "model not found",
    "invalid model",
    "no such model",
    "unsupported model",
    "model_not_supported",
    "does not exist",
)


def _unknown_model_hint(detail: str) -> str | None:
    """远端 400 命中「模型名问题」时给中文提示；不命中返回 ``None``（正常透传）。

    仅在 400 上生效：400 是参数类错误，401/403/5xx 各自含义不同（鉴权 / 限流 /
    服务端故障），把它们一并归到「模型名」是误诊——那才是真正的提示噪声。
    """
    low = (detail or "").lower()
    if not any(marker in low for marker in _UNKNOWN_MODEL_MARKERS):
        return None
    return (
        "模型名问题：provider 不认识这个模型名（拼写错误 / 已下线 / 当前 API key "
        "无权访问）。请核对 model 字段，或先用 available-models 拉取该 key "
        "实际可用的模型列表。"
    )


@router.post("/model-profiles", status_code=status.HTTP_201_CREATED, response_model=ModelProfileOut)
def create_model_profile(payload: dict, request: Request) -> dict:
    """创建一条档案。必填字段：``name / provider / model``。

    入参 ``params_json.api_key`` 的特殊值与 ``/model-configs`` 同语义：
    - ``""`` 或 ``"***"`` → 视为未设置，落库时不写 api_key。
    - 其他字符串 → 原样落库（明文）。

    **连接绑定**（2026-10-21 批次十五）：可传 ``connection_id`` ⇒ 该档案的
    base_url / api_key / headers **取自连接**（档案自身的同名键被忽略）。
    ⚠️ **协议是模型级属性**（2026-10-24 裁决）：``provider`` 由档案自持——
    传与连接主协议**不同**的 provider 是**合法**配置（同一条多协议网关连接上，
    不同模型声明不同协议）；绑定态的 base_url 按档案协议从连接的协议地址池
    （``protocol_base_urls``）取，池里没有 ⇒ 回退连接主 base_url。
    """
    settings = request.app.state.settings
    name = payload.get("name")
    provider = payload.get("provider")
    model = payload.get("model")
    if not (isinstance(name, str) and name):
        raise HTTPException(status_code=422, detail="name required")
    if not (isinstance(provider, str) and provider):
        raise HTTPException(status_code=422, detail="provider required")
    model = _clean_model_field(model, field="model")
    connection_id = _resolve_connection_id(payload.get("connection_id"))
    if connection_id:
        conn_row = ConnectionService(settings.db_path).get(connection_id)
        if conn_row is None:
            raise HTTPException(
                status_code=422, detail=f"model_connection {connection_id!r} not found"
            )
        # 协议是模型级属性：provider 用请求体的（不再校验与连接一致、
        # 也不再由连接覆盖——原 422「provider 与所选连接不一致」已随协议下放删除）。
    try:
        # 契约兼容：params_json（旧）优先；缺失则读 params（新契约，前端按约定发）。
        # 两者都无 → None → _normalize_params 返回空 dict（保留原行为）。
        raw_params = _normalize_params(
            payload.get("params_json", payload.get("params"))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    params_dict = _prepare_post_params(raw_params)
    enabled = payload.get("enabled", 1)
    if enabled not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
    enabled_int = 1 if enabled in (1, True) else 0

    svc = ProfileService(settings.db_path)
    try:
        row = svc.create(
            name=name,
            provider=provider,
            model=model,
            params=params_dict,
            enabled=enabled_int,
            connection_id=connection_id,
        )
    except Exception as exc:
        from sqlite3 import IntegrityError

        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
        raise
    return _mask_profile_row(row, settings.db_path)


def _resolve_connection_id(raw: Any) -> str | None:
    """规范化入参 ``connection_id``；``None`` / 空串 / 非字符串 → 422 或 None。

    - 缺键 / ``None`` → ``None``（独立档案，存量形态）；
    - 非空字符串 → 原样（调用方负责校验存在性）；
    - 其它类型 → 422。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    raise HTTPException(status_code=422, detail="connection_id must be a string or null")


def _mask_profile_rows(rows: list[dict], db_path: str) -> list[dict]:
    """档案列表的读路径出口：绑定连接的档案，其 ``has_api_key`` **由连接派生**。

    ⚠️ 2026-10-21 批次十五：绑定后档案自己 params 里那份 key **不生效**
    （运行时解析链不再查档案级 secrets）⇒ 拿它判定会让「连接上清空了 key」
    仍显示「已配置密钥」（显示与实际失真）。
    """
    conn_ids = {r.get("connection_id") for r in rows if r.get("connection_id")}
    conns: dict[str, dict] = {}
    if conn_ids:
        conns = {
            c["connection_id"]: c
            for c in ConnectionService(db_path).get_many(sorted(conn_ids))
        }
    out = []
    for r in rows:
        cid = r.get("connection_id")
        out.append(
            _mask_response(r, connection_row=conns.get(cid)) if cid else _mask_response(r)
        )
    return out


def _mask_profile_row(row: dict, db_path: str) -> dict:
    """单条档案的读路径出口（同 :func:`_mask_profile_rows`）。"""
    cid = row.get("connection_id")
    if not cid:
        return _mask_response(row)
    conn_row = ConnectionService(db_path).get(cid)
    return _mask_response(row, connection_row=conn_row)


@router.get("/model-profiles", response_model=list[ModelProfileOut])
def list_model_profiles(
    request: Request,
    include_enabled_only: bool = False,
) -> list[dict]:
    """列出档案（``include_enabled_only`` 可只回 enabled 行）。

    列表行的 9 键与单条 ``GET /model-profiles/{id}`` **逐字相同**（同走
    :func:`_mask_response`，同源于 service 的 ``SELECT *``）⇒ 复用
    :class:`ModelProfileOut`，不为列表另立模型。

    ⚠️ 该模型是 ``extra="allow"``：少声明字段在响应侧看不出来（键以 extra 透出），
    只能从 OpenAPI ``properties`` 断言——判别用例见
    ``tests/api/test_config_list_response_contract.py``。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    rows = svc.list(include_enabled_only=include_enabled_only)
    return _mask_profile_rows(rows, settings.db_path)


@router.get("/model-profiles/{profile_id}", response_model=ModelProfileOut)
def get_model_profile(profile_id: str, request: Request) -> dict:
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    row = svc.get(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return _mask_profile_row(row, settings.db_path)


@router.patch("/model-profiles/{profile_id}", response_model=ModelProfileOut)
def patch_model_profile(profile_id: str, payload: dict, request: Request) -> dict:
    """部分更新。

    入参 ``params_json.api_key`` 的特殊值与 ``/model-configs`` 同语义：
    - ``"***"`` → 保留 DB 原值；
    - 空字符串 → 清空；
    - 其它 → 用入参值覆盖。

    其余字段（name / provider / model / enabled）沿用既有非空校验。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    fields: dict = {}
    if "name" in payload:
        v = payload["name"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="name must be non-empty string")
        fields["name"] = v
    if "provider" in payload:
        v = payload["provider"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="provider must be non-empty string")
        fields["provider"] = v
    if "model" in payload:
        fields["model"] = _clean_model_field(payload["model"], field="model")
    if "params_json" in payload or "params" in payload:
        if "params_json" in payload and "params" in payload:
            raise HTTPException(
                status_code=422,
                detail="params 与 params_json 只能传其一",
            )
        existing = svc.get(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
        try:
            # 契约兼容：params_json 优先；缺失则读 params（新契约）。
            # 与 POST 对齐：两个键都视作「更新 params」的入口。
            raw_params = _normalize_params(
                payload.get("params_json", payload.get("params"))
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        merged = _prepare_patch_params(raw_params, existing.get("params_json") or "{}")
        fields["params_json"] = _dump_params_json(merged)
    if "enabled" in payload:
        v = payload["enabled"]
        if v not in (0, 1, True, False):
            raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
        fields["enabled"] = 1 if v in (1, True) else 0
    if "connection_id" in payload:
        # 缺键 = 不动；显式 null = **解绑**（退回独立配置）。
        connection_id = _resolve_connection_id(payload["connection_id"])
        fields["connection_id"] = connection_id
        if connection_id:
            conn_row = ConnectionService(settings.db_path).get(connection_id)
            if conn_row is None:
                raise HTTPException(
                    status_code=422, detail=f"model_connection {connection_id!r} not found"
                )
            # 协议是模型级属性（2026-10-24 裁决）：provider 不再由服务端从连接强制继承，
            # 也**不再**校验与连接一致（原 422「provider 与所选连接不一致」已删）。
            # 请求体给了 provider 就用它（上方已做非空校验）；没给 ⇒ 回退为连接的
            # 主 provider（兼容旧前端/脚本，且对存量行为无突变）。
            if "provider" not in fields:
                fields["provider"] = conn_row["provider"]

    if not fields:
        row = svc.get(profile_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
        return _mask_profile_row(row, settings.db_path)

    row = svc.update_partial(profile_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return _mask_profile_row(row, settings.db_path)


@router.delete("/model-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_profile(profile_id: str, request: Request):
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    refs = svc.list_bindings_referencing(profile_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "profile_in_use",
                "message": f"profile {profile_id!r} 被 {len(refs)} 个 binding 引用",
                "referenced_by": refs,
            },
        )
    ok = svc.delete(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return None


@router.post(
    "/model-profiles/{profile_id}/test",
    status_code=status.HTTP_200_OK,
    response_model=ProfileTestResult,
    # ⚠️ 必需：status_code 恒在（可空），hint 命中才存在。exclude_unset 精确复刻
    # 「dict 里没有的键不出参」；改成 exclude_none 会把 status_code: null 也删掉
    # （改变实测行为），不写则 hint 会凭空变成 null。依据见 models.ProfileTestResult。
    response_model_exclude_unset=True,
    responses={502: {"model": ProviderErrorEnvelope}},
)
def test_model_profile(profile_id: str, request: Request) -> dict:
    """ping：用该档案做一次轻量级健康检查。

    行为契约与 ``/model-configs/{id}/test`` 一致：
    - ``mock`` provider → 直接 ``ok=True``；
    - 其它 provider → 调 :meth:`Provider.health_check`；
    - 健康检查失败 → 200 + ``ok=False``（前端可区分「端点不可达」与「鉴权失败」）；
    - 仅 ProviderError 透传到 502。
    - ``enabled=0`` → 422。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    profile_row = svc.get(profile_id)
    if profile_row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    if profile_row.get("enabled") == 0:
        raise HTTPException(
            status_code=422,
            detail=f"model_profile {profile_id!r} is disabled (enabled=0); cannot /test",
        )

    # 构造与 model_configs 行同键名的 dict，复用 ModelRouter.get_provider。
    # ⚠️ 2026-10-21 批次十五：带上 connection_id ⇒ get_provider 会解析连接，
    #    绑定连接的档案（自身 params 里没有 base_url）也能被正确构造。
    config_row = {
        "config_id": profile_row["profile_id"],
        "capability": "creative_writing",  # 仅用于 provider 构造，无业务影响
        "provider": profile_row["provider"],
        "model": profile_row["model"],
        "params_json": profile_row["params_json"],
        "enabled": profile_row["enabled"],
        "connection_id": profile_row.get("connection_id"),
    }
    # ⚠️ 2026-10-15 修：`get_provider` 会抛 `ValueError`（档案里 provider 配置不合法，
    # 例如 `openai_compatible` 缺 `base_url`——见 providers.py 的构造校验），而它原先
    # 落在 `try` **之外**且只捕 `ProviderError` ⇒ 未捕获 ⇒ FastAPI 返回
    # **500 且 Content-Type 是 text/plain**（不是 JSON）。
    # 实测：建一个 `provider=openai_compatible` 且 `params_json={}` 的档案后调 /test，
    # 响应体就是裸的 `Internal Server Error`。前端 `client.ts` 按 JSON `detail` 解析，
    # 拿到非 JSON 会直接崩在错误展示上。
    # 处置：把构造纳入同一 try，`ValueError` 映射为 **422**（与相邻那行
    # 「enabled=0 不能 /test」同属「实体自身状态不满足」这一类，不是上游故障）。
    try:
        provider = ModelRouter(settings.db_path).get_provider(config_row)
        result = provider.health_check()
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc), "status_code": exc.status_code},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"model_profile {profile_id!r} 的 provider 配置无效：{exc}",
        ) from exc
    status_code = result.get("status_code")
    detail = result.get("detail") or ""
    payload = {
        "profile_id": profile_id,
        "ok": bool(result.get("ok")),
        "latency_ms": int(result.get("latency_ms") or 0),
        "detail": detail,
        "status_code": status_code,
    }
    # ⚠️ 2026-10-21 批次十五：``probe`` 是**条件键** —— provider 的 health_check
    # 返回了它（models 探测 / 退化的 chat 探测）才带上；不返回时整个键不存在
    # （与 hint 同样的范式：response_model_exclude_unset=True）。
    probe = result.get("probe")
    if isinstance(probe, str) and probe:
        payload["probe"] = probe
    # 400 且错误文本指向模型名 → 追加中文提示。这是**新增可选字段**：既有 5 个字段
    # 逐字不变，不命中时响应体与本批之前完全一致（正常错误继续原样透传）。
    if status_code == 400:
        hint = _unknown_model_hint(detail)
        if hint is not None:
            payload["hint"] = hint
    return payload


# ---------------------------------------------------------------------------
# V3.8 拉取可选模型列表（POST /model-profiles/available-models）
# ---------------------------------------------------------------------------


@router.post("/model-profiles/group-by-connection")
def preview_group_by_connection(request: Request):
    """**按连接归组**的分组预览（只读，不写库）。

    ⚠️ **为什么不做自动回填**（2026-10-21 批次十五，先验判断被推翻必须留痕）：
    生效 key 的权威解析链是 ``secrets_store.resolve_profile_api_key``，5 级里
    **最高两级读的是 secrets.json（一个文件）** ⇒ SQL 迁移看不见它们。
    本仓 secrets.json **现存的条目正是档案级**（实测 ``mprof_f95e3c7b4127``）
    ⇒ 存在「两个档案的 params 里写着同一个 key、但生效 key 不同」的真实情形。
    按 params 里的 key 分组会把它们合并 ⇒ **凭据静默串用**（表现为某档案突然 401）。

    ⇒ 归组键由**运行期同一个解析函数**算出（:func:`group_profiles_by_connection`），
    且**先给用户看预览**、用户点头后才写。

    响应：
    - ``groups[]`` —— 每组含 ``group_key``（provider / base_url / api_key_env /
      api_key_set）、``profile_ids``、建议的连接名、``key_fingerprint``
      （生效 key 的 sha256 前 4 位，**不泄密**）、``key_consistent``
      （组内各档案的生效 key 是否完全相同 —— 不同则**不能**归到一条连接）。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    rows = svc.list()
    groups = group_profiles_by_connection(rows)
    out = []
    for g in groups:
        pids = g["profile_ids"]
        fingerprints = set()
        for pid in pids:
            row = svc.get(pid)
            if row is None:
                continue
            params = parse_params(row.get("params_json"))
            eff_key = resolve_profile_api_key(
                row.get("profile_id"),
                row.get("provider"),
                [params["api_key_env"]] if params.get("api_key_env") else None,
                params.get("api_key"),
            )
            fingerprints.add(
                hashlib.sha256((eff_key or "").encode("utf-8")).hexdigest()[:4]
                if eff_key
                else ""
            )
        g = dict(g)
        g["key_fingerprint"] = next(iter(fingerprints)) if len(fingerprints) == 1 else ""
        g["key_consistent"] = len(fingerprints) <= 1
        out.append(g)
    return {"groups": out}


@router.post("/model-profiles/apply-grouping")
def apply_grouping(payload: dict, request: Request) -> dict:
    """把档案归到连接上（``assignments``: ``[{connection_id, profile_ids}]``）。

    ⚠️ **生效 key 必须不变** —— 绑定连接后档案不再查 ``secrets[profile_id]``，
    改用 ``secrets[connection_id]`` ⇒ 若连接的生效 key 与档案归组前的生效 key
    不同，该档案的密钥**会静默换掉**（⇒ 401 或计费到别人的号）。
    ⇒ 本端点**逐条校验**，不一致直接 422 并说明是哪一条、差在哪。

    校验通过才写 ``connection_id``；``provider`` 按**回退**语义落库——
    归组请求体不带 provider ⇒ 写连接的主 provider（协议是模型级属性、
    档案自持的裁决下，本端点是回退路径；2026-10-24 裁决）。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    conn_svc = ConnectionService(settings.db_path)
    assignments = payload.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise HTTPException(
            status_code=422, detail="assignments must be a non-empty array"
        )

    applied: list[str] = []
    for item in assignments:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="each assignment must be an object")
        connection_id = item.get("connection_id")
        profile_ids = item.get("profile_ids")
        if not isinstance(connection_id, str) or not connection_id:
            raise HTTPException(
                status_code=422, detail="assignment.connection_id must be a non-empty string"
            )
        if not isinstance(profile_ids, list) or not profile_ids:
            raise HTTPException(
                status_code=422, detail="assignment.profile_ids must be a non-empty array"
            )
        conn_row = conn_svc.get(connection_id)
        if conn_row is None:
            raise HTTPException(
                status_code=422, detail=f"model_connection {connection_id!r} not found"
            )
        conn_params = parse_params(conn_row.get("params_json"))
        conn_key = resolve_profile_api_key(
            None,
            conn_row.get("provider"),
            None,
            conn_params.get("api_key"),
            connection_id=connection_id,
        )
        conn_base = (conn_row.get("base_url") or "").rstrip("/")
        for pid in profile_ids:
            row = svc.get(str(pid))
            if row is None:
                raise HTTPException(
                    status_code=422, detail=f"model_profile {pid!r} not found"
                )
            params = parse_params(row.get("params_json"))
            prof_key = resolve_profile_api_key(
                row.get("profile_id"),
                row.get("provider"),
                [params["api_key_env"]] if params.get("api_key_env") else None,
                params.get("api_key"),
            )
            if (prof_key or "") != (conn_key or ""):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"档案 {pid!r} 的生效密钥与连接 {connection_id!r} 不一致 —— "
                        "归组会换掉该档案的密钥（绑定后不再查档案级 secrets）。"
                        "请把连接的密钥改成与该档案一致后再归组。"
                    ),
                )
            prof_base = (params.get("base_url") or "").rstrip("/")
            if prof_base != conn_base:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"档案 {pid!r} 的 base_url（{prof_base!r}）与连接 "
                        f"{connection_id!r}（{conn_base!r}）不一致 —— 归组会换掉地址。"
                    ),
                )
            # 协议下放档案（2026-10-24）：归组请求体不带 provider ⇒ 回退写连接的
            # 主 provider（与 PATCH 绑定路径的「没给则回退」同一语义，存量行为不变）。
            svc.update_partial(
                str(pid),
                {"connection_id": connection_id, "provider": conn_row["provider"]},
            )
            applied.append(str(pid))
    return {"applied": applied, "count": len(applied)}


@router.post("/model-profiles/available-models", response_model=AvailableModelsOut)
def list_available_models(payload: dict, request: Request) -> dict:
    """按 ``provider / base_url / api_key / profile_id`` 调用各家模型列表接口。

    用途：模型档案编辑表单「拉取模型」按钮的支撑端点。后端代理请求以
    避免浏览器跨域、CORS 与密文外带；前端只看到列表不接触 key。

    请求体（所有键均可选除 ``provider``）：
    - ``provider`` (str, 必填) —— ``mock / openai_compatible / anthropic / ollama`` 等。
    - ``base_url`` (str, 可选) —— 若空，按 provider 选用官方默认；OpenAI 兼容 / 自建
      端点必须提供。
    - ``api_key`` (str, 可选) —— 明文直接用；缺省走 ``resolve_api_key``：先查档案
      ``profile_id``、再 ``provider`` 默认 env（``NOVELOS_API_KEY_<PROVIDER>``）。
    - ``profile_id`` (str, 可选) —— 已有档案 ID；用于上面提到的解析优先级。
    - ``connection_id`` (str, 可选) —— **连接路径**（2026-10-21 批次十五）：
      provider / base_url / api_key / headers / models_url **全部取自该连接**；
      给了连接时 ``provider`` 不再必填。

    ⚠️ **不传 ``connection_id`` 时的行为与改造前逐字相同**（inline 路径保留）。

    错误处理：
    - ``400`` —— 必填缺失 / 该 provider 必须有 key 但解析不到；detail 仅文案不含 key。
    - ``502`` —— 拉取失败（超时、非 2xx、解析坏 JSON）。detail 仅文案不含 key。

    响应：
    - ``{"models": ["id1", "id2", ...]}`` —— 排序去重后的字符串数组。
    """
    # ---- 连接路径（2026-10-21 批次十五）：一切取自连接 ----
    conn_models_url: str | None = None
    conn_headers: dict[str, str] = {}
    raw_connection_id = payload.get("connection_id")
    if raw_connection_id is not None:
        if not isinstance(raw_connection_id, str) or not raw_connection_id.strip():
            raise HTTPException(
                status_code=422, detail="connection_id must be a non-empty string"
            )
        settings = request.app.state.settings
        conn_row = ConnectionService(settings.db_path).get(raw_connection_id.strip())
        if conn_row is None:
            raise HTTPException(
                status_code=422, detail=f"model_connection {raw_connection_id!r} not found"
            )
        conn_params = parse_params(conn_row.get("params_json"))
        provider = conn_row["provider"]
        base_url = (conn_row.get("base_url") or "").rstrip("/") or None
        inline_api_key = conn_params.get("api_key")
        if isinstance(inline_api_key, str) and inline_api_key == "***":
            inline_api_key = None
        conn_models_url = conn_params.get("models_url") or None
        raw_conn_headers = conn_params.get("headers")
        if isinstance(raw_conn_headers, dict):
            conn_headers = {
                k: v for k, v in raw_conn_headers.items() if isinstance(v, str)
            }
    else:
        provider = payload.get("provider")
        if not (isinstance(provider, str) and provider):
            raise HTTPException(status_code=422, detail="provider required")

        raw_base_url = payload.get("base_url")
        base_url: str | None = None
        if isinstance(raw_base_url, str) and raw_base_url.strip():
            base_url = raw_base_url.strip()

        raw_api_key = payload.get("api_key")
        inline_api_key: str | None = None
        if isinstance(raw_api_key, str) and raw_api_key.strip():
            inline_api_key = raw_api_key.strip()
            # 安全：不接受脱敏占位符作为「真密钥」。
            if inline_api_key == "***":
                inline_api_key = None

        raw_profile_id = payload.get("profile_id")
        profile_id: str | None = None
        if isinstance(raw_profile_id, str) and raw_profile_id.strip():
            profile_id = raw_profile_id.strip()

    # ---- mock：固定假列表，无需任何外部调用 ----
    if provider == "mock":
        return {"models": ["mock-model"]}

    # ---- ollama：无需 key；缺 base_url 用本地默认 ----
    if provider == "ollama":
        target_base = base_url or OllamaProvider.DEFAULT_BASE_URL
        url = f"{target_base.rstrip('/')}/api/tags"
        try:
            resp = httpx.get(url, headers=conn_headers or None, timeout=_FETCH_LIST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：无法连接 Ollama（{type(exc).__name__}）",
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
            ) from exc
        return {"models": _dedup_sorted(_extract_ids_ollama(data))}

    # ---- 解析 API key（优先入参，再走 secrets / env） ----
    api_key: str | None = inline_api_key
    if api_key is None:
        api_key = resolve_api_key(
            provider,
            None,
            profile_id=profile_id,
        )

    # ---- anthropic：必须 key（/v1/models 走鉴权） ----
    if provider == "anthropic":
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="拉取模型列表失败：需先配置 Anthropic 密钥",
            )
        target_base = base_url or AnthropicProvider.DEFAULT_BASE_URL
        url = f"{target_base.rstrip('/')}/v1/models"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": AnthropicProvider.ANTHROPIC_VERSION,
        }
        headers.update(conn_headers)
        try:
            resp = httpx.get(url, headers=headers, timeout=_FETCH_LIST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：无法连接 Anthropic（{type(exc).__name__}）",
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
            ) from exc
        return {"models": _dedup_sorted(_extract_ids_anthropic(data))}

    # ---- openai_compatible / 其他：必须 base_url；key 可选（本地 / Ollama 等） ----
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail=f"拉取模型列表失败：{provider!r} 需提供 base_url",
        )
    # ⚠️ 2026-10-21 批次十五：``models_url`` 覆盖默认的 ``{base_url}/models``
    #    （opencode 的模型列表**不在** {base_url}/models 上 ⇒ 不配就打错 URL）。
    url = conn_models_url or f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 连接级自定义头（如 x-opencode-session）随探测请求一起发
    headers.update(conn_headers)
    try:
        resp = httpx.get(url, headers=headers, timeout=_FETCH_LIST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：无法连接（{type(exc).__name__}）",
        ) from exc
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
        ) from exc
    return {"models": _dedup_sorted(_extract_ids_openai_compatible(data))}


__all__ = ["router"]
