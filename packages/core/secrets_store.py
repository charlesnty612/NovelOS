"""NovelOS 密钥集中管理（V3.8 — secrets.json）。

职责：
- 把分散在 ``model_profiles.params_json.api_key``、环境变量 ``NOVELOS_API_KEY_*``
  之外的密钥统一收敛到仓库根 ``secrets.json``，避免明文入 DB / 落备份包。
- 提供 :func:`load_api_keys` 与 :func:`resolve_profile_api_key` 两个收口函数，
  所有需要 API key 的代码路径（含 ``model_router.providers.resolve_api_key`` /
  ``model_router.security._mask_params`` 的 ``has_api_key`` 派生）一律从这里解析。

文件结构（推荐）::

    {
      "api_keys": {
        "mprof_f95e3c7b4127": "sk-...",
        "openai_compatible": "sk-...",
        "NOVELOS_API_KEY_KIMI": "sk-..."
      }
    }

优先级（高 → 低）：file[profile_id] → file[provider] → file[env_name] →
``params_json["api_key"]``（存量兼容）→ 环境变量 ``env_name``。

路径解析顺序：
1. 环境变量 ``NOVELOS_SECRETS_FILE``（覆盖默认路径，便于 CI/部署/测试）；
2. 仓库根 ``secrets.json``（即 ``packages/core/secrets_store.py`` 上溯至仓库根）。

读取带 mtime 缓存：文件变更自动重读；解析失败 log warning 并返回空 dict（不抛）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

from .logging_config import get_logger

log = get_logger("novelos.secrets")

# ---------------------------------------------------------------------------
# 内部：路径解析
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """仓库根目录：``packages/core/secrets_store.py`` → ``<root>``。"""
    # 本文件位于 packages/core/secrets_store.py
    return Path(__file__).resolve().parents[2]


def _resolve_secrets_path() -> Path | None:
    """按 ``NOVELOS_SECRETS_FILE`` → ``<repo>/secrets.json`` 顺序解析；都不存在则 None。"""
    env = os.environ.get("NOVELOS_SECRETS_FILE")
    if env:
        return Path(env).expanduser().resolve()
    default = _repo_root() / "secrets.json"
    return default if default.exists() else default  # 始终返回默认路径；不存在由调用方处理


# ---------------------------------------------------------------------------
# 带 mtime 缓存的读取
# ---------------------------------------------------------------------------

_cache_lock = RLock()
_cache_path: Path | None = None
_cache_mtime_ns: int | None = None
_cache_keys: dict[str, str] = {}


def _read_file_fresh(path: Path) -> dict[str, str]:
    """读 JSON 文件并抽出 ``api_keys`` 子表；任何错误返回空 dict + log warning。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("secrets file read failed: path=%s err=%s", path, exc)
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("secrets file JSON invalid: path=%s err=%s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("secrets file root must be object: path=%s", path)
        return {}
    api_keys = data.get("api_keys")
    if api_keys is None:
        return {}
    if not isinstance(api_keys, dict):
        log.warning("secrets file api_keys must be object: path=%s", path)
        return {}
    out: dict[str, str] = {}
    for k, v in api_keys.items():
        if isinstance(k, str) and isinstance(v, str) and v:
            out[k] = v
    return out


def load_api_keys(*, force_reload: bool = False) -> dict[str, str]:
    """读取 ``secrets.json`` 抽出 ``api_keys`` 映射；带 mtime 缓存。

    - ``force_reload=True`` 跳过缓存（迁移/测试用）。
    - 文件不存在 → 返回空 dict（不抛）。
    - 坏 JSON / 字段类型错 → log warning + 返回空 dict（不抛）。
    - 优先级最低的回退仍是环境变量（不在本函数内处理，由调用方做）。
    """
    path = _resolve_secrets_path()
    if path is None or not path.exists():
        with _cache_lock:
            # 缓存视作空 dict
            globals()["_cache_path"] = None
            globals()["_cache_mtime_ns"] = None
            globals()["_cache_keys"] = {}
        return {}
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        log.warning("secrets file stat failed: path=%s err=%s", path, exc)
        return {}
    with _cache_lock:
        if (
            not force_reload
            and _cache_path == path
            and _cache_mtime_ns == mtime_ns
        ):
            return dict(_cache_keys)
        keys = _read_file_fresh(path)
        globals()["_cache_path"] = path
        globals()["_cache_mtime_ns"] = mtime_ns
        globals()["_cache_keys"] = keys
        return dict(keys)


def reset_cache() -> None:
    """清空 mtime 缓存；测试 / 迁移工具用。"""
    with _cache_lock:
        globals()["_cache_path"] = None
        globals()["_cache_mtime_ns"] = None
        globals()["_cache_keys"] = {}


# ---------------------------------------------------------------------------
# 统一解析入口
# ---------------------------------------------------------------------------


def resolve_profile_api_key(
    profile_id: str | None,
    provider: str | None,
    env_names: list[str] | None,
    params_api_key: str | None,
    *,
    secrets: dict[str, str] | None = None,
    connection_id: str | None = None,
) -> str | None:
    """按统一优先级解析 API key（单点收口）。

    优先级（高 → 低）：
      0. ``secrets[<connection_id>]``（连接级；2026-10-21 批次十五新增，**最高**）
      1. ``secrets[<profile_id>]``（档案级，覆盖最具体）
      2. ``secrets[<provider>]``（provider 级通用）
      3. ``secrets[<env_name>]``（逐个 env_name 试；支持兼容旧 key）
      4. ``params_json["api_key"]``（存量 DB 明文；迁移期兼容）
      5. 环境变量 ``env_name``（逐个）

    返回首个非空字符串；全部为空 → ``None``。

    ⚠️ **连接级身份与档案级身份互斥**：绑定了连接的档案**不再拥有 key**
    ⇒ 调用方（:mod:`packages.core.model_router.effective`）在绑定态传
    ``profile_id=None`` 且 ``connection_id=<连接 id>``，此时**不查** ``secrets[profile_id]``。
    这样「档案还挂着旧的档案级 secrets 条目」不会在连接接管后继续生效
    （否则改连接上的 key 却不生效，是最难查的一类「显示与实际失真」）。

    ⚠️ ``secrets[<provider>]`` 在多连接场景有歧义（多个连接同 provider 时都会命中），
    但 ``secrets[<connection_id>]`` 更具体且排在前面 ⇒ 只有一个「provider 级默认 key」
    的连接会命中这一级。
    """
    if secrets is None:
        secrets = load_api_keys()

    if connection_id and secrets.get(connection_id):
        return secrets[connection_id]
    if profile_id and secrets.get(profile_id):
        return secrets[profile_id]
    if provider:
        prov_key = secrets.get(provider)
        if prov_key:
            return prov_key
        prov_lower = provider.lower()
        if prov_lower and secrets.get(prov_lower):
            return secrets[prov_lower]
    if env_names:
        for name in env_names:
            if name and secrets.get(name):
                return secrets[name]
    if isinstance(params_api_key, str) and params_api_key:
        return params_api_key
    if env_names:
        for name in env_names:
            if not name:
                continue
            v = os.environ.get(name)
            if v:
                return v
    return None


def has_api_key(
    profile_id: str | None,
    provider: str | None,
    env_names: list[str] | None,
    params_api_key: str | None,
) -> bool:
    """``has_api_key`` 派生（含文件来源）——``True`` 当且仅当 :func:`resolve_profile_api_key` 非空。"""
    return resolve_profile_api_key(profile_id, provider, env_names, params_api_key) is not None


__all__ = [
    "load_api_keys",
    "resolve_profile_api_key",
    "has_api_key",
    "reset_cache",
]
