"""生效配置解析层（连接 × 档案 → 一份可执行的配置）。

**为什么有这个模块**（2026-10-21 批次十五）：

引入「连接层」（``model_connections``）后，同一个语义散落成了多份副本：
「哪些 ``params_json`` 键是 NovelOS 本地消费、不进上游请求体」写在
``providers.py`` 与 ``router.py`` 各一份；「从档案行构造出同形的 config row」
写在 ``router.py`` 两处 + ``model_profiles.py`` 一处；``params_json`` 的
``try/except json.loads`` 解析又写了三遍。

副本意味着**下一个人加一个本地保留键时必须记得改 N 处**——漏一处就把该键
当请求体字段发给上游（OpenAI 兼容 API 直接 400）。本模块把这些副本收敛成
**一处定义、多处 import**。

对外三个概念：

- :data:`RESERVED_PARAM_KEYS` —— 本地保留键的**唯一**权威清单。
- :class:`EffectiveConfig` —— 一个档案「实际会用到的配置」（provider / base_url /
  api_key / headers / timeout / 传给上游的其余参数）。
- :func:`resolve_effective` —— 从「档案行 + 连接行」解出 :class:`EffectiveConfig`。

**连接与档案的职责划分**（本仓口径）：

============  ==========================================================
连接级        provider / base_url / api_key / headers / models_url
档案级        model / thinking / reasoning_effort / timeout_s /
              temperature / max_tokens / seed …
============  ==========================================================

⚠️ ``timeout_s`` 归**档案**级：不同模型响应速度不同，允许不同 timeout；
放连接级会在「同一厂商多模型」时产生「哪个档案的值算数」的歧义。

**密钥解析**：绑定连接的档案**不再拥有 key** ⇒ 解析链里**不查** ``secrets[profile_id]``，
改查 ``secrets[connection_id]``（链序见 :func:`packages.core.secrets_store.resolve_profile_api_key`）。
未绑定的档案维持原链不变（存量零行为变化）。

**请求头模板**：值里可写 ``{{run_id}}`` / ``{{session_id}}`` 两个占位符
（封闭集合；其它占位符在**写时**被拒，见 :func:`validate_headers`）。
``{{session_id}}`` 在 run 上下文渲染成 ``ses_<sha256(run_id)[:32]>``；
无 run 上下文（探针路径）渲染成 ``ses_probe_<sha256(connection_id)[:16]>``
—— **不是**字面 ``"unknown"``（那会让所有请求共用一个 session，
既违背「每个写作 run 一个」的裁决，也会制造一个「头合法 ⇒ 探测假成功」的状态）。

**归组**（:func:`group_profiles_by_connection`）：把「生效 key + base_url +
provider」完全相同的档案归到一条连接。分组键**必须**用生效 key
（``resolve_profile_api_key`` 的解析结果）而不是 ``params_json.api_key``——
因为 ``secrets[profile_id]`` 优先于后者，两个档案的 params 里写着同一个 key
也可能**生效不同的 key**（本仓 secrets.json 现存档案级条目即此情形）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RESERVED_PARAM_KEYS",
    "CONNECTION_ONLY_PARAMS",
    "SENSITIVE_HEADER_NAMES",
    "HEADER_PLACEHOLDERS",
    "EffectiveConfig",
    "HeaderContext",
    "parse_params",
    "derive_session_id",
    "derive_probe_session_id",
    "render_headers",
    "validate_headers",
    "resolve_effective",
    "group_profiles_by_connection",
]


# ---------------------------------------------------------------------------
# 保留键（唯一权威清单）
# ---------------------------------------------------------------------------

RESERVED_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "base_url",       # 构造 URL 用
        "api_key",        # 走 Authorization / x-api-key 头，不进请求体
        "api_key_env",    # 本地保留键（密钥来源提示）
        "timeout_s",      # 本地超时
        "headers",        # 自定义请求头，不进请求体
        "models_url",     # 模型列表端点（仅探测用）
    }
)
"""``params_json`` 里由 NovelOS 本地消费、**不得**透传给上游请求体的键。

⚠️ **唯一权威定义**。``providers.py`` 的 4 个 provider 与 ``router.py`` 的
extras 计算都从这里取——新增本地保留键只改这一处。
"""

CONNECTION_ONLY_PARAMS: frozenset[str] = frozenset({"api_key", "headers", "models_url"})
"""**只允许出现在连接级** ``params_json`` 的键。

档案级出现这三个键 ⇒ 绑定连接时它们被忽略（连接是唯一权威）；
未绑定连接时 ``api_key`` 仍按存量路径生效（``headers`` / ``models_url`` 亦然）。
"""

SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
        "x-goog-api-key",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-auth-token",
    }
)
"""读路径需要掩码的请求头名（**小写**比较）。

⚠️ ``x-opencode-session`` **不在**此列表：它是每 run 的路由 / 缓存 id，
不是凭据——掩掉用户就没法核对模板对不对。
"""

HEADER_PLACEHOLDERS: frozenset[str] = frozenset({"run_id", "session_id"})
"""请求头值里允许的占位符（**封闭集合**）。

其它形如 ``{{xxx}}`` 的占位符在**写时**被拒（422），不许静默发出字面量
——上游收到 ``x-opencode-session: {{session_id}}`` 会 400 或静默错路由。
"""


# ---------------------------------------------------------------------------
# 解析与派生
# ---------------------------------------------------------------------------


def parse_params(raw: Any) -> dict[str, Any]:
    """把 ``params_json``（JSON 字符串 / dict / None）解析成 dict。

    解析失败或形状不符 → ``{}``（不抛）。**统一入口**：
    此前 ``router.py`` 两处 + ``providers.py`` 一处各写了一遍 try/except。
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return v if isinstance(v, dict) else {}
    return {}


def derive_session_id(run_id: str) -> str:
    """由 run_id 派生稳定的会话 id：``ses_<sha256(run_id)[:32]>``。

    - 同一 run_id ⇒ 恒同结果（**稳定**，这是 opencode 的硬要求）。
    - 不同 run_id ⇒ 不同结果（不同写作 run 不串味）。
    - 固定前缀 ``ses_``（opencode 的头值格式要求）。
    """
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
    return f"ses_{digest}"


def derive_probe_session_id(connection_id: str) -> str:
    """探针（无 run 上下文）用的稳定会话 id：``ses_probe_<sha256(conn)[:16]>``。

    探针本身是与上游的一次独立对话，给它一个**按连接派生且恒定**的 id：
    - 不是字面 ``"unknown"``（那会让所有探针共用一个 session，制造「假成功」）；
    - 恒定 ⇒ 探针的 prompt cache 也能命中。
    """
    digest = hashlib.sha256(connection_id.encode("utf-8")).hexdigest()[:16]
    return f"ses_probe_{digest}"


@dataclass(frozen=True)
class HeaderContext:
    """请求头模板渲染所需的运行期上下文。"""

    run_id: str | None = None
    connection_id: str | None = None

    @property
    def has_run(self) -> bool:
        return bool(self.run_id)


def _render_value(value: str, ctx: HeaderContext) -> str:
    """渲染单个头值里的占位符。未知占位符原样保留（写时已拦，见 validate_headers）。"""
    if "{{" not in value:
        return value
    out = value
    if ctx.run_id:
        out = out.replace("{{run_id}}", ctx.run_id)
        out = out.replace("{{session_id}}", derive_session_id(ctx.run_id))
    else:
        # 探针上下文：run 相关占位符不解析（保持字面 ⇒ 写时校验会拒掉这种配置）
        if ctx.connection_id:
            out = out.replace("{{session_id}}", derive_probe_session_id(ctx.connection_id))
    return out


def render_headers(headers: dict[str, Any] | None, ctx: HeaderContext) -> dict[str, str]:
    """渲染头集合（值里的占位符 → 实际值）。非 str 值跳过（写时已拦）。"""
    if not headers:
        return {}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if not isinstance(v, str):
            continue
        out[k] = _render_value(v, ctx)
    return out


def validate_headers(headers: Any) -> list[str]:
    """校验头集合的形状，返回**错误串列表**（空 = 合法）。

    两类问题：
    1. **未知占位符** —— ``{{xxx}}``（xxx 不在 :data:`HEADER_PLACEHOLDERS`）。
       写时必须拒（422），否则上游收到字面量占位符。
    2. **header 注入 / 形状** —— 名字不是合法 HTTP token，或值含 ``\\r`` / ``\\n``
       / 控制字符 / 非 str。httpx 遇非法头值会抛难懂的 TypeError；
       ``\\r\\n`` 更是**请求拆分**风险。
    """
    errors: list[str] = []
    if headers is None:
        return errors
    if not isinstance(headers, dict):
        return ["headers 必须是对象（键值对）"]
    for k, v in headers.items():
        if not isinstance(k, str) or not k:
            errors.append(f"header 名必须是非空字符串：{k!r}")
            continue
        if any(ch in k for ch in " \t\r\n:") or not all(
            32 < ord(ch) < 127 for ch in k
        ):
            errors.append(f"header 名不是合法 HTTP token：{k!r}")
        if not isinstance(v, str):
            errors.append(f"header 值必须是字符串：{k!r} = {v!r}")
            continue
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            errors.append(f"header 值含控制字符（禁 \\r \\n）：{k!r}")
        idx = 0
        while True:
            start = v.find("{{", idx)
            if start < 0:
                break
            end = v.find("}}", start)
            if end < 0:
                errors.append(f"header 值的占位符未闭合：{k!r} = {v!r}")
                break
            name = v[start + 2 : end]
            if name not in HEADER_PLACEHOLDERS:
                errors.append(
                    f"header 值的占位符未知：{k!r} = {v!r}（允许：{sorted(HEADER_PLACEHOLDERS)}）"
                )
            idx = end + 2
        # ⚠️ 重复前缀防护：``{{session_id}}`` 渲染出的值**已含** ``ses_`` 前缀
        #    （opencode 的头值格式要求）⇒ 用户若照文档字面写 ``ses_{{session_id}}``
        #    会得到 ``ses_ses_xxx``。这类错误在**写时**拦掉（422），
        #    而不是等上游 400 或静默错路由。
        for redundant in ("ses_{{session_id}}", "ses_{{run_id}}"):
            if redundant in v:
                errors.append(
                    f"header 值的占位符前缀重复：{k!r} = {v!r}"
                    f"（请直接写 {{{{session_id}}}}，前缀会自动加）"
                )
    return errors


# ---------------------------------------------------------------------------
# 生效配置
# ---------------------------------------------------------------------------


@dataclass
class EffectiveConfig:
    """一个档案「实际会用到的配置」。"""

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None
    models_url: str | None = None
    upstream_params: dict[str, Any] = field(default_factory=dict)
    """档案级、**要透传给上游请求体**的其余参数（temperature / thinking / …）。

    已剔除 :data:`RESERVED_PARAM_KEYS`。
    """

    connection_id: str | None = None
    bound: bool = False
    """True = 该档案绑定了连接（此时连接是 provider / base_url / api_key /
    headers / models_url 的**唯一权威**）。"""


def _norm_base_url(url: str | None) -> str:
    """base_url 归一化：去尾部斜杠（与 provider 构造时的 ``rstrip("/")`` 同口径）。"""
    return (url or "").rstrip("/")


def resolve_effective(
    profile_row: dict[str, Any],
    connection_row: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
    provider: str | None = None,
) -> EffectiveConfig:
    """把「档案行 + 连接行」解成 :class:`EffectiveConfig`。

    - **未绑定连接**（``connection_row is None``）⇒ 全部取自档案的
      ``params_json``，**与改造前逐字相同**（存量零行为变化）。
    - **已绑定连接** ⇒ provider / base_url / api_key / headers / models_url
      **全部取自连接**；档案的同名键**被忽略**（防「一列两用」）；
      档案的**非**保留键（temperature / thinking …）仍然生效。
    - **悬空 connection_id**（指向不存在的连接）⇒ 抛 :class:`ValueError`
      ——**不静默回落**到档案自己的 base_url（那会掩盖配置错误）。
    """
    from packages.core.model_router.providers import resolve_api_key

    profile_params = parse_params(profile_row.get("params_json"))
    conn_params = parse_params((connection_row or {}).get("params_json"))
    connection_id = profile_row.get("connection_id") or None

    if connection_id and connection_row is None:
        raise ValueError(
            f"model profile {profile_row.get('profile_id')!r} 引用了不存在的连接 "
            f"{connection_id!r}（配置错误；不静默回落到档案自身的 base_url）"
        )

    bound = connection_row is not None
    if bound:
        eff_provider = (connection_row or {}).get("provider") or profile_row.get("provider")
        base_url = _norm_base_url((connection_row or {}).get("base_url")) or None
        headers = render_headers(
            conn_params.get("headers"),
            HeaderContext(run_id=run_id, connection_id=connection_id),
        )
        models_url = conn_params.get("models_url") or None
        # 绑定态：档案不再拥有 key ⇒ 解析链里不查 secrets[profile_id]
        # （resolve_api_key 内部从传入的 params 取 params["api_key"]，此处传连接自己的）
        api_key = resolve_api_key(
            eff_provider,
            conn_params,
            profile_id=None,
            connection_id=connection_id,
        )
        timeout_s = profile_params.get("timeout_s")
    else:
        eff_provider = profile_row.get("provider")
        base_url = _norm_base_url(profile_params.get("base_url")) or None
        headers = render_headers(
            profile_params.get("headers"),
            HeaderContext(run_id=run_id, connection_id=None),
        )
        models_url = profile_params.get("models_url") or None
        api_key = resolve_api_key(
            eff_provider,
            profile_params,
            profile_id=profile_row.get("profile_id") or profile_row.get("config_id"),
        )
        timeout_s = profile_params.get("timeout_s")

    upstream_params = {k: v for k, v in profile_params.items() if k not in RESERVED_PARAM_KEYS}

    return EffectiveConfig(
        provider=eff_provider or "",
        model=profile_row.get("model") or "",
        base_url=base_url,
        api_key=api_key,
        headers=headers,
        timeout_s=timeout_s,
        models_url=models_url,
        upstream_params=upstream_params,
        connection_id=connection_id,
        bound=bound,
    )


# ---------------------------------------------------------------------------
# 归组（把生效配置相同的档案归到一条连接）
# ---------------------------------------------------------------------------


def group_profiles_by_connection(
    profiles: list[dict[str, Any]],
    *,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按**生效配置**给档案分组，供「按连接归组」动作预览。

    分组键 = (provider, 归一化 base_url, api_key_env, **生效 api_key**)。

    ⚠️ 键里必须用**生效 key**而不是 ``params_json.api_key``：
    ``secrets[profile_id]`` 优先于后者 ⇒ 两个档案 params 里写着同一个 key
    也可能生效不同的 key（本仓 secrets.json 现存档案级条目即此情形）。
    按 params 里的 key 分组会把它们合并 ⇒ **凭据静默串用**。

    返回每组：``{"group_key": {...}, "profile_ids": [...], "name": "..."}``。
    """
    from packages.core.secrets_store import resolve_profile_api_key

    groups: dict[tuple, dict[str, Any]] = {}
    for p in profiles:
        params = parse_params(p.get("params_json"))
        provider = p.get("provider") or ""
        base_url = _norm_base_url(params.get("base_url"))
        api_key_env = params.get("api_key_env") or ""
        effective_key = resolve_profile_api_key(
            p.get("profile_id") or p.get("config_id"),
            provider,
            [api_key_env] if api_key_env else None,
            params.get("api_key"),
            secrets=secrets,
        )
        key = (provider, base_url, api_key_env, effective_key or "")
        g = groups.get(key)
        if g is None:
            label = f"{provider} · {base_url}" if base_url else provider
            g = {
                "group_key": {
                    "provider": provider,
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "api_key_set": bool(effective_key),
                },
                "profile_ids": [],
                "name": label,
            }
            groups[key] = g
        g["profile_ids"].append(p.get("profile_id") or p.get("config_id"))
    return list(groups.values())
