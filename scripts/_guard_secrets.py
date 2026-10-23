#!/usr/bin/env python
"""凭据收敛巡检（D-18）—— **secrets 模式的实现模块**。

⚠️ 本模块由 CLI 门面 ``scripts/check_content_repo_guard.py`` 调用（**不是**独立
入口；``scripts/`` 不是包 ⇒ 门面靠 ``sys.path`` 注入加载本模块）。

职责
----
1. **采集**（:func:`collect_secrets_state`）：``secrets.json`` 的**可用键**、
   运行库的档案 / 连接 / provider / env_name 形态、库内明文 api_key；
2. **判定**（:func:`decide_secrets`）：悬空条目 / 不可用条目 / 库内明文 / 不可判；
3. **动作**：:func:`migrate_secrets`（库内明文 → 文件）、
   :func:`prune_dangling_secrets`（删悬空条目）；
4. **渲染**（:func:`render_secrets_text`）。

⚠️ **清理悬空条目的三道保险**（2026-10-23 对抗审查 F-1 / F-2 + Lead 复现）
---------------------------------------------------------------------
1. **诊断不可信 ⇒ 拒绝执行**（退出码 2，**不写任何文件**）：运行库不存在 / 缺表 /
   读表失败 ⇒ 一律**拒绝清理**（读表失败还会阻断 ``--migrate``：不可判 ⇒
   不许拿空基线落盘）。
2. **归属闸门**：「这个库属于这个 secrets 文件吗？」⇒ 判据 = **secrets 的可用键
   ∩ 库内实体身份**（``profile_id`` / ``connection_id``）⇒ ⚠️ **provider 级 / env
   级键不携带项目身份** ⇒ 不能当归属证据（否则一个别名键就能骗过闸门 ⇒ 一次
   prune 把真凭据整条删掉还报「合规」）⇒ 归属无据 ⇒ 悬空**不可判** ⇒ 拒绝清理。
   ⚠️ **只有别名型键的 secrets 文件**也归入「无据」⇒ 需显式
   ``--force-all-dangling``（= 声明「--db 指向的就是本项目的库」）。
3. **删前复核 + 写前备份**：每个候选键都过一遍**生产解析函数**
   ``packages.core.secrets_store.resolve_profile_api_key`` ⇒ **仍能解析出非空
   密钥 ⇒ 不许删**（覆盖运行期第 3 优先级 ``secrets[<env_name>]``）；
   真正落盘前先备份成 ``secrets.json.bak-<时间戳>``（同目录、原子、失败即中止）。

⚠️ **``secrets.json`` 结构非法 ⇒ 不可判（退出码 2）**（2026-10-23 对抗审查 F-3）
--------------------------------------------------------------------------
``secrets.json`` **存在但结构非法**（顶层不是对象 / ``api_keys`` 段不是对象）
⇒ 生产侧 ``packages.core.secrets_store._read_file_fresh()`` 会 log warning 并
**返回空凭据**（运行期拿不到 key）⇒ 那不是「没有凭据」，是「**凭据读不出来**」
⇒ 巡检必须判「**不可判**」⇒ **退出码 2**；``--migrate`` / ``--prune-dangling``
**一律拒绝执行**（不写任何文件、不写备份、不清库）⇒ **三条路径同口径**。

⚠️ **反向不变量（不得过修）**：``secrets.json`` **不存在**、**没有 ``api_keys``
段**、或该段为 ``null`` ⇒ 运行期 ``_read_file_fresh()`` **不报 warning**、返回
``{}`` ⇒ **合法的空凭据状态** ⇒ 巡检**仍然报合规（退出码 0）**。

⚠️ **可用键口径（与生产一致）**：只有 ``isinstance(k, str) and isinstance(v, str)
and v`` 的条目才叫「可用键」⇒ 值畸形（空串 / 非字符串）的条目**运行期解析不出来**
⇒ 判「不合规」（必须可见）但**绝不**判悬空（⇒ 绝不会被 prune 删掉）。

⚠️ **``--json`` 的计数口径**（2026-10-23 对抗审查 F-6）：``action_count`` =
**实际落地的条数**（dry-run ⇒ ``0``），「打算做 N 条」用 ``candidate_count`` 表达；
``migration`` / ``prune`` 两段都带 ``conflicts`` 与 ``backup_path``（与文本模式同口径）。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:  # 正常路径：仓库根在 sys.path（包导入 / pytest pythonpath=["."]）
    # ⚠️ F-2：**只调用**生产解析函数，不改它、也不另写一套「名字匹配」口径。
    from packages.core.secrets_store import resolve_profile_api_key
except ImportError:  # pragma: no cover —— 直接 `python scripts/check_content_repo_guard.py` 时
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from packages.core.secrets_store import resolve_profile_api_key


# ---------------------------------------------------------------------------
# 凭据收敛（D-18）：悬空 secrets.json 条目 + 库内明文 api_key
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "novelos.db"


def _resolve_secrets_path(explicit: str | None = None) -> Path:
    """``secrets.json`` 路径：显式参数 → ``NOVELOS_SECRETS_FILE`` → 仓库根。

    与 :func:`packages.core.secrets_store._resolve_secrets_path` 同口径（env 优先、
    仓库根兜底）；本脚本自解析路径是为了**能写**（迁移命令），读侧解析仍以
    ``secrets_store.load_api_keys`` 为准。
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("NOVELOS_SECRETS_FILE")
    if env:
        return Path(env).expanduser().resolve()
    return REPO_ROOT / "secrets.json"


@dataclass(frozen=True)
class KeySlot:
    """一个「密钥查找槽位」——运行期解析链会查到的**名字集合**。

    ⚠️ 槽位里的名字必须与**生产解析链**一致（``packages.core.secrets_store.
    resolve_profile_api_key`` 的 5 级 + ``providers.resolve_api_key`` 的 env_name
    派生）⇒ 本脚本只**调用**生产函数判定「这个键还在不在用」，不另写一套规则
    （F-2 的原始缺陷正是「两套口径」：巡检只认 profile / connection / provider，
    运行期还认 ``secrets[<env_name>]``）。
    """

    profile_id: str | None = None
    connection_id: str | None = None
    provider: str | None = None
    env_names: tuple[str, ...] = ()


@dataclass
class SecretsState:
    """凭据收敛巡检采集到的事实（**绝不包含密钥值**）。"""

    db_path: str
    secrets_path: str
    secrets_exists: bool = False
    secrets_keys: list[str] = field(default_factory=list)
    dangling_keys: list[str] = field(default_factory=list)
    profile_ids: list[str] = field(default_factory=list)
    connection_ids: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    plaintext_profiles: list[str] = field(default_factory=list)
    plaintext_connections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 「已知集合」（**键名的小写形态**）——档案 id ∪ 连接 id ∪ provider ∪ env_name 形态。
    #: ⚠️ 它**只**当「悬空判据」用 ⇒ **不得**当归属证据（provider 名 / env 名是
    #: **全局**的，不携带项目身份 ⇒ 用它当归属证据 = 用错证据，见 ``entity_keys``）。
    known_keys: set[str] = field(default_factory=set)
    #: 「实体身份」集合（**小写形态**）= 所有 ``profile_id`` ∪ 所有 ``connection_id``
    #: ⇒ **归属闸门**的唯一判据（secrets 的可用键 ∩ 本集合 ≠ ∅ ⇒ 归属有据）；
    #: ⚠️ provider 级 / env 级键**不在**其中（它们不携带项目身份）。
    entity_keys: set[str] = field(default_factory=set)
    #: **真正的读取失败**（库不存在 / 缺表 / sqlite 错误）——与「集合为空」**分开记**。
    read_errors: list[str] = field(default_factory=list)
    #: 「无法判定」的原因（**读表失败**）⇒ 阻断**整个巡检**（含 ``--migrate``：
    #: 不可判 ⇒ 不许拿空基线落盘）。
    blocked_reasons: list[str] = field(default_factory=list)
    #: 「悬空条目**不可判**」的原因（见 :func:`collect_secrets_state` 的**归属闸门**）。
    #: ⚠️ 与 ``blocked_reasons`` **刻意分开**：它只否证「悬空」这一个判断，
    #: **不**否证「库内明文」——``--migrate`` 是**加法**动作，不依赖「悬空」判断
    #: ⇒ **不受**它拦截（迁移另外有自己的**归属**闸门）。
    dangling_undecidable: str | None = None
    #: 归属是否**自动**验证通过（secrets 的可用键 ∩ 实体身份 ≠ ∅）。
    ownership_verified: bool = False
    #: 归属无据的原因（``None`` ⇒ 有据或无需验证）。
    ownership_reason: str | None = None
    #: ``--force-all-dangling`` 放行归属闸门时的**醒目提示**（文本 + JSON 都可见）。
    ownership_warning: str | None = None
    #: ``api_keys`` 段的**条目总数**（含值畸形的不可用条目）。
    secrets_entry_count: int = 0
    #: 值不是非空字符串的条目（**不可用** ⇒ 运行期解析不出来 ⇒ 判「不合规」，
    #: 但**绝不**判悬空 ⇒ 绝不会被 prune 删掉）。
    unusable_keys: list[str] = field(default_factory=list)
    #: 运行期的查找槽位（供**生产解析函数**复核「这个键还在不在用」）。
    profile_slots: list[KeySlot] = field(default_factory=list)
    connection_slots: list[KeySlot] = field(default_factory=list)


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """**只读**打开 SQLite（``mode=ro`` URI）——绝不写活库。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _params_api_key(params_json: str | None) -> str | None:
    """从 ``params_json`` 取 ``api_key``；无 / 非字符串 / 空 ⇒ ``None``。"""
    if not params_json:
        return None
    try:
        data = json.loads(params_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    key = data.get("api_key")
    if isinstance(key, str) and key.strip():
        return key
    return None


def _params_dict(params_json: str | None) -> dict:
    """把 ``params_json`` 解析成 dict（坏 JSON / 非对象 ⇒ 空 dict）。"""
    if not params_json:
        return {}
    try:
        data = json.loads(params_json)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """表的列名集合（表不存在 ⇒ 空集；**不抛异常**）。"""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[1]) for r in rows}


def _row_opt(row: sqlite3.Row, key: str) -> str | None:
    """取一列的值；该列不存在 / 为 NULL / 空串 ⇒ ``None``。"""
    try:
        value = row[key]
    except (IndexError, KeyError):
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _env_names(provider: str | None, params: dict | None) -> tuple[str, ...]:
    """env_name 槽位——与 ``packages.core.model_router.providers.resolve_api_key`` **同口径**：
    显式 ``params["api_key_env"]``（若配了）+ 默认 ``NOVELOS_API_KEY_<PROVIDER 大写>``。

    ⚠️ 本函数只负责**把名字收集出来**；「这个名字有没有被用上」由**生产解析函数**
    :func:`packages.core.secrets_store.resolve_profile_api_key` 判定（F-2 双保险第二道）
    ⇒ 不另写一套匹配规则。
    """
    names: list[str] = []
    if isinstance(params, dict):
        explicit = params.get("api_key_env")
        if isinstance(explicit, str) and explicit.strip():
            names.append(explicit.strip())
    if provider:
        names.append(f"NOVELOS_API_KEY_{provider.upper()}")
    return tuple(names)


def _slot_names(slot: KeySlot) -> set[str]:
    """该槽位在**生产解析链**里会被查到的全部名字（含 provider 的小写形态）。

    与 :func:`packages.core.secrets_store.resolve_profile_api_key` 的查找槽位对齐：
    ``connection_id`` → ``profile_id`` → ``provider``（含小写形态）→ ``env_name``。
    """
    names: set[str] = set()
    if slot.connection_id:
        names.add(slot.connection_id)
    if slot.profile_id:
        names.add(slot.profile_id)
    if slot.provider:
        names.add(slot.provider)
        names.add(slot.provider.lower())
    names.update(n for n in slot.env_names if n)
    return names


def _is_still_resolvable(state: SecretsState, key: str, value: str) -> bool:
    """这个键**现在还能不能被运行期解析出来**（**走生产解析函数**，F-2）。

    做法：把 ``secrets`` 换成**只含这一个键**的字典，交给生产函数
    :func:`packages.core.secrets_store.resolve_profile_api_key` 解析 ⇒ 返回非空
    ⇒ 该名字确实是某个档案 / 连接的查找槽位（**仍被引用**）⇒ **不许删**。

    ⚠️ 复用生产函数是这条的关键：另写一套「名字匹配」规则就会与运行期口径漂移
    （F-2 的原始缺陷正是「两套口径」）。
    """
    if not value:
        return False
    probe = {key: value}
    for slot in list(state.profile_slots) + list(state.connection_slots):
        resolved = resolve_profile_api_key(
            profile_id=slot.profile_id,
            provider=slot.provider,
            env_names=list(slot.env_names),
            params_api_key=None,
            secrets=probe,
            connection_id=slot.connection_id,
        )
        if resolved:
            return True
    return False


def _json_type_name(value: object) -> str:
    """给 JSON 值一个人类可读的**类型名**（用于错误信息）。

    ⚠️ 用 **JSON 语汇**（``null`` / ``bool`` / ``int`` / ``float`` / ``str`` /
    ``list`` / ``dict``）而不是 Python 类名 ⇒ 错误信息与用户看到的 JSON 文本对得上。
    """
    if value is None:
        return "null"
    if isinstance(value, bool):  # ⚠️ bool 是 int 的子类 ⇒ 必须先判
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def collect_secrets_state(
    db_path: Path,
    secrets_path: Path,
    *,
    force_all_dangling: bool = False,
) -> SecretsState:
    """采集「悬空 secrets 条目」与「库内明文 api_key」（**只读**）。

    ⚠️ **「已知集合」必须可信**（F-1 / 2026-10-23 对抗审查 CRITICAL）：
    ``--prune-dangling`` 会**删掉**被判为悬空的键 ⇒ 若读库失败（库不存在 / 缺表 /
    sqlite 错误）或归属无据，「每个键都悬空」是**误判** ⇒ 一次误指 ``--db``
    （打错路径 / 未迁移的库 / 另一个项目库 / 空库）就可能**清空用户全部凭据**，
    且无备份、无确认、还返回「合规」（绿）。

    ⇒ **两道闸门，各司其职**（**不得**混用）：
    ① **读表失败**（``read_errors`` ⇒ ``blocked_reasons``）⇒ 阻断**整个巡检**
       （含 ``--migrate``：不可判 ⇒ 不许拿空基线落盘）；
    ② **归属闸门**（``secrets`` 的**可用键** ∩ ``entity_keys`` ≠ ∅）⇒ 归属无据 ⇒
       悬空条目**不可判**（``dangling_undecidable``）⇒ 清理动作**拒绝执行**；
       ⚠️ provider 级 / env 级键**不在** ``entity_keys`` 里（不携带项目身份）。
    ⇒ ``--force-all-dangling`` = 「**归属确认**」⇒ 一律放行归属闸门（并醒目提示）。
    ⚠️ **``secrets.json`` 结构非法 = 不可判**（F-3 / 2026-10-23 对抗审查 major）：
    「顶层不是对象」/「``api_keys`` 段不是对象」⇒ 运行期
    ``packages.core.secrets_store._read_file_fresh()`` 会 log warning 并返回 ``{}``
    ⇒ 那是「**凭据读不出来**」，不是「没有凭据」⇒ 计入 ``read_errors`` ⇒ 退出码 2
    （否则**真出问题却报绿**：巡检说合规、运行期拿不到 key）。
    ⚠️ **反向不变量（不得过修）**：``secrets.json`` **不存在**、**无 ``api_keys``
    段**、或该段为 ``null`` ⇒ 运行期 ``if api_keys is None: return {}`` **不报
    warning** ⇒ **合法空状态** ⇒ **不得**判不可判（仍走常规口径）。
    """
    state = SecretsState(db_path=str(db_path), secrets_path=str(secrets_path))

    # --- secrets.json 的键（只读键名，**不读值**）---
    if secrets_path.exists():
        state.secrets_exists = True
        try:
            raw = json.loads(secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # ⚠️ **解析失败 = 不可判**（对抗审查 F-4，2026-10-23）：
            # 「文件读不出来」与「文件不存在」是**两件事**——后者是合法状态（还没迁过），
            # 前者是**巡检自身出错** ⇒ 必须计入 ``read_errors`` ⇒ 退出码 2，
            # **不得**降级成一条 note 后照报「合规」（那正是 F-1 的同族形状）。
            state.notes.append(f"secrets.json 解析失败：{exc}")
            state.read_errors.append(f"secrets.json 解析失败：{exc}")
        else:
            # ⚠️ **结构非法 = 不可判**（对抗审查 F-3，2026-10-23）：
            # 运行侧 ``packages.core.secrets_store._read_file_fresh()`` 对
            # 「顶层不是对象」/「api_keys 段不是对象」会 log warning 并**返回 {}**
            # ⇒ 运行期**拿不到凭据**。那不是「没有凭据」，是「**凭据读不出来**」
            # ⇒ 若只记一条 note 就照报「合规」（exit 0）⇒ **真出问题却报绿**，
            # 且三条路径口径分裂（巡检报绿 / prune 拿「空 api_keys」当基线 /
            # migrate 静默把用户原有键重置为 {}）⇒ 一律计入 ``read_errors``。
            if not isinstance(raw, dict):
                reason = (
                    f"secrets.json 顶层必须是对象，实际是 {_json_type_name(raw)}"
                    "（运行期会打 warning 并按「无凭据」处理 ⇒ 不可判）"
                )
                state.notes.append(reason)
                state.read_errors.append(reason)
            else:
                api_keys = raw.get("api_keys")
                if api_keys is None:
                    # ✅ **合法空状态**（反向不变量，不得过修）：运行期
                    # ``if api_keys is None: return {}`` **不报 warning**
                    # （键不存在与值为 ``null`` 在运行期**不可区分**）⇒ 合法。
                    state.notes.append("secrets.json 无 api_keys 段")
                elif not isinstance(api_keys, dict):
                    reason = (
                        "secrets.json 的 api_keys 段必须是对象，实际是 "
                        f"{_json_type_name(api_keys)}"
                        "（运行期会打 warning 并按「无凭据」处理 ⇒ 不可判）"
                    )
                    state.notes.append(reason)
                    state.read_errors.append(reason)
                else:
                    # ⚠️ **可用键口径必须与生产一致**（``secrets_store._read_file_fresh``：
                    # ``isinstance(k, str) and isinstance(v, str) and v``）⇒ 值畸形
                    # （空串 / 非字符串）的条目**运行期解析不出来** ⇒ 它们**不是**
                    # 「悬空」（「对不上任何档案」≠「值畸形」）⇒ **绝不**进
                    # ``secrets_keys``（⇒ 绝不会被 prune 删掉），但**必须可见**
                    # （``unusable_keys`` ⇒ 判「不合规」，退出码 1）。
                    # ⚠️ **反向不变量（不得过修）**：``api_keys`` 段不存在 / 为 ``null``
                    # ⇒ 合法空状态 ⇒ 不得判不可用、不得判不合规（退出码 0）。
                    state.secrets_entry_count = len(api_keys)
                    usable: list[str] = []
                    unusable: list[str] = []
                    for raw_key, raw_value in api_keys.items():
                        if isinstance(raw_key, str) and isinstance(raw_value, str) and raw_value:
                            usable.append(raw_key)
                        else:
                            unusable.append(str(raw_key))
                    state.secrets_keys = sorted(usable)
                    state.unusable_keys = sorted(unusable)
    else:
        state.notes.append("secrets.json 不存在")

    # --- 运行库（只读 URI）---
    if not db_path.exists():
        state.notes.append(f"运行库不存在：{db_path}")
        state.read_errors.append(f"运行库不存在：{db_path}")
        state.blocked_reasons = list(state.read_errors)
        return state
    try:
        conn = _open_readonly(db_path)
    except sqlite3.Error as exc:
        state.notes.append(f"运行库打开失败：{exc}")
        state.read_errors.append(f"运行库打开失败：{exc}")
        state.blocked_reasons = list(state.read_errors)
        return state
    try:
        prof_sql = "SELECT profile_id, provider, params_json"
        if "connection_id" in _table_columns(conn, "model_profiles"):
            prof_sql += ", connection_id"
        prof_sql += " FROM model_profiles"
        try:
            profiles = conn.execute(prof_sql).fetchall()
        except sqlite3.Error as exc:
            state.notes.append(f"读 model_profiles 失败：{exc}")
            state.read_errors.append(f"读 model_profiles 失败：{exc}")
            profiles = []
        try:
            connections = conn.execute(
                "SELECT connection_id, provider, params_json FROM model_connections"
            ).fetchall()
        except sqlite3.Error as exc:
            state.notes.append(f"读 model_connections 失败：{exc}")
            state.read_errors.append(f"读 model_connections 失败：{exc}")
            connections = []

        for row in profiles:
            pid = str(row["profile_id"])
            state.profile_ids.append(pid)
            provider = str(row["provider"] or "")
            if provider:
                state.providers.append(provider)
            params = _params_dict(row["params_json"])
            if _params_api_key(row["params_json"]) is not None:
                state.plaintext_profiles.append(pid)
            state.profile_slots.append(
                KeySlot(
                    profile_id=pid,
                    connection_id=_row_opt(row, "connection_id"),
                    provider=provider or None,
                    env_names=_env_names(provider, params),
                )
            )
        for row in connections:
            cid = str(row["connection_id"])
            state.connection_ids.append(cid)
            provider = str(row["provider"] or "")
            if provider:
                state.providers.append(provider)
            params = _params_dict(row["params_json"])
            if _params_api_key(row["params_json"]) is not None:
                state.plaintext_connections.append(cid)
            state.connection_slots.append(
                KeySlot(
                    profile_id=None,
                    connection_id=cid,
                    provider=provider or None,
                    env_names=_env_names(provider, params),
                )
            )
    finally:
        conn.close()

    # --- 已知集合（**键名的小写形态**；含 env_name 形态 ⇒ F-2 第一道保险）---
    known: set[str] = set()
    entity: set[str] = set()
    for slot in list(state.profile_slots) + list(state.connection_slots):
        known |= {n.lower() for n in _slot_names(slot)}
        if slot.connection_id:
            entity.add(slot.connection_id.lower())
        if slot.profile_id:
            entity.add(slot.profile_id.lower())
    state.known_keys = known
    state.entity_keys = entity

    # --- 读表失败 ⇒ **整个巡检**不可判（含 --migrate：不可判 ⇒ 不许拿空基线落盘）---
    if state.read_errors:
        # 读库失败 ⇒ 「已知集合」**不完整** ⇒ **不判悬空**（宁可不判，不可全判）。
        state.blocked_reasons = list(state.read_errors)
        return state

    # ⚠️ **归属闸门**（2026-10-23 Lead 亲自复现的 major）：
    # 「这个库属于这个 secrets 文件吗？」——判据必须是**携带项目身份**的名字，
    # 即**实体身份**（profile_id / connection_id）⇒ 交集闸门**改用 entity_keys**。
    # ⚠️ provider 级 / env 级键**不携带项目身份**（provider 名是全局的）
    # ⇒ 拿 known_keys 当归属证据 ⇒ 一个别名键就能骗过闸门 ⇒ 错库时真凭据
    # 被整条删掉、退出码仍是 0（报「合规」）。
    secrets_lower = {k.lower() for k in state.secrets_keys}
    if secrets_lower and not (secrets_lower & state.entity_keys):
        if not state.entity_keys:
            # 空库（0 档案 0 连接）⇒ known_keys 同样为空 ⇒ 「全部是死键」与
            # 「根本没有引用数据」**不可区分** ⇒ 就是同一个「归属无据」判断。
            reason = (
                "运行库已知集合为空（没有任何档案 / 连接）——无法区分「全部是死键」"
                "与「根本没有引用数据」"
            )
        else:
            reason = (
                f"secrets.json 里的 {len(state.secrets_keys)} 个可用键**一个都**对不上运行库里的"
                "任何档案 id / 连接 id ⇒ 这个运行库很可能**不是**本 secrets 文件所属项目的库"
                "（或 secrets 存的是别名形态的键）"
            )
        state.ownership_reason = reason
        if not force_all_dangling:
            state.dangling_undecidable = reason + " ⇒ 悬空条目**不可判**"
            state.notes.append(state.dangling_undecidable)
            return state
        # ⚠️ force = 「归属确认」⇒ 放行，但**必须醒目提示**（文本 + JSON 都可见）。
        state.ownership_warning = (
            "⚠️ 归属未经自动验证（--force-all-dangling 已放行）——"
            "请确认 --db 指向的就是**本项目**的运行库"
        )
    elif secrets_lower:
        state.ownership_verified = True

    # --- 悬空条目（判据 = 已知集合；**只有可用键**参与 ⇒ 不可用条目绝不被删）---
    state.dangling_keys = [k for k in state.secrets_keys if k.lower() not in known]
    return state


def decide_secrets(state: SecretsState) -> dict:
    """由采集到的事实推导凭据收敛结论（**纯函数**）。

    ⚠️ **「无法判定」是独立结论**（F-1）：读库失败 / **归属无据** ⇒ 悬空条目
    **不可判** ⇒ CLI 退出码 **2**（巡检自身出错），**不是** 0（合规）也不是
    1（不合规）。「合规」在这里意味着「已判定且没有悬空条目」；判不出来时不得报绿。

    ⚠️ **退出码由 CLI 门面按 ``blocked`` 映射**（0 合规 / 1 不合规 / 2 不可判）——
    本模块只给**事实与结论**，不自带 CLI 常量（避免与门面循环依赖）。
    """
    issues: list[str] = []
    if state.dangling_keys:
        issues.append(f"悬空 secrets 条目 {len(state.dangling_keys)} 条")
    if state.unusable_keys:
        # ⚠️ **值畸形 ≠ 悬空**：不可用条目**必须可见**（巡检报绿而运行期拿不到
        # 凭据 = F-3 同族形状），但**绝不**判悬空（⇒ 绝不会被 prune 删掉）。
        issues.append(
            f"secrets 条目值不可用 {len(state.unusable_keys)} 条"
            "（运行期解析不出来 ⇒ 等于该条目不存在）"
        )
    n_plain = len(state.plaintext_profiles) + len(state.plaintext_connections)
    if n_plain:
        issues.append(f"库内明文 api_key {n_plain} 行")
    blocked = list(state.blocked_reasons)
    if state.dangling_undecidable:
        # ⚠️ 「悬空**不可判**」也是一种不可判 ⇒ 退出码 2（**不得**报「合规」）。
        # ⚠️ 与「读表失败」分开记：读表失败时**连库内明文都数不清**；
        # 本条只否证「悬空」这一个判断（库内明文那部分照样可信、照样报）。
        blocked.append(state.dangling_undecidable)
    if blocked:
        return {
            "ok": False,
            "verdict": "unknown",
            "issues": issues + [f"无法判定悬空条目：{r}" for r in blocked],
            "dangling_keys": state.dangling_keys,
            "plaintext_profiles": state.plaintext_profiles,
            "plaintext_connections": state.plaintext_connections,
            "blocked": True,
            "blocked_reasons": blocked,
        }
    return {
        "ok": not issues,
        "verdict": "ok" if not issues else "unsafe",
        "issues": issues,
        "dangling_keys": state.dangling_keys,
        "plaintext_profiles": state.plaintext_profiles,
        "plaintext_connections": state.plaintext_connections,
        "blocked": False,
        "blocked_reasons": [],
    }


def _action_lines(
    migration: dict | None, prune: dict | None, *, marker: str = ""
) -> list[str]:
    """本次动作（迁移 / 清理）的人读行——**必须与结论同段**（不得自相矛盾）。

    ⚠️ ``marker``：归属未经验证时的**提示尾巴**（R1.4：只看动作行的人也要知道
    这次写盘/删除是在归属未经自动验证的情况下做的）。
    """
    out: list[str] = []
    for label, res in (("迁移", migration), ("清理悬空条目", prune)):
        if res is not None:
            line = f"{label}: {'已执行' if res['applied'] else '未执行'}（{res['note']}）"
            out.append(f"{line}  {marker}" if marker else line)
    return out


def _conclusion_line(verdict: dict) -> str:
    """结论行（**不预设**不可判的具体原因 ⇒ 逐条原因在下方明细里给出）。"""
    if verdict["ok"]:
        return "结论: 合规（无悬空条目、库内无明文 api_key）"
    if verdict.get("blocked"):
        return "结论: 无法判定（诊断不可信 ⇒ 不判定悬空条目；原因见下方明细）"
    return "结论: 不合规（" + "；".join(verdict["issues"]) + "）"


def _ownership_marker(state: SecretsState) -> str:
    """归属提示的**短标记**（挂到动作行尾巴上 ⇒ 只看动作行的人也看得见）。"""
    if not state.ownership_warning:
        return ""
    if state.ownership_verified:
        return "⚠️ --force-all-dangling 已放行"
    return "⚠️ 归属未经自动验证"


def render_secrets_text(
    state: SecretsState,
    verdict: dict,
    *,
    migration: dict | None = None,
    prune: dict | None = None,
) -> str:
    """凭据收敛巡检的人读形态（**不含任何密钥值**）。

    ⚠️ **本次动作（迁移 / 清理）的结果必须并进本段文本**（修法 E）：旧实现把
    「结论: 合规」印在「清理悬空条目: 已执行（已删除 1 条…）」**之前** ⇒ 读的人
    先看到「合规」⇒ 误以为什么都没发生 ⇒ **一段文本讲完整件事**。
    """
    lines = [
        "===== 凭据收敛巡检（只读；D-18）=====",
        f"  运行库: {state.db_path}",
        f"  secrets.json: {state.secrets_path}"
        f"{'' if state.secrets_exists else '（不存在）'}",
        f"  secrets 条目数: {state.secrets_entry_count}"
        f"（可用 {len(state.secrets_keys)}，不可用 {len(state.unusable_keys)}）",
        f"  悬空条目数: {len(state.dangling_keys)}",
        f"  库内明文 api_key（档案）: {len(state.plaintext_profiles)} 行",
        f"  库内明文 api_key（连接）: {len(state.plaintext_connections)} 行",
    ]
    if state.ownership_verified:
        lines.append("  归属: 已验证（secrets 的可用键命中库内档案 id / 连接 id）")
    else:
        lines.append(
            "  归属: 未经自动验证"
            + (f"（{state.ownership_reason}）" if state.ownership_reason else "")
        )
    if state.ownership_warning:
        lines.append(f"  {state.ownership_warning}")
    lines.append("")
    action_lines = _action_lines(migration, prune, marker=_ownership_marker(state))
    if action_lines:
        lines.append("本次动作:")
        lines.extend(f"  {line}" for line in action_lines)
        lines.append("")
    lines.append(_conclusion_line(verdict))
    if verdict.get("blocked"):
        for r in verdict.get("blocked_reasons") or []:
            lines.append(f"无法判定: {r}")
    if state.dangling_keys:
        lines.append("悬空条目（secrets.json 里的键对不上任何档案 / 连接 / provider）:")
        for k in state.dangling_keys:
            lines.append(f"  - {k}")
    if state.unusable_keys:
        lines.append("不可用条目（值不是非空字符串 ⇒ 运行期解析不出来）:")
        for k in state.unusable_keys:
            lines.append(f"  - {k}")
    if state.plaintext_profiles:
        lines.append("库内明文 api_key 所在档案:")
        for pid in state.plaintext_profiles:
            lines.append(f"  - {pid}")
    if state.plaintext_connections:
        lines.append("库内明文 api_key 所在连接:")
        for cid in state.plaintext_connections:
            lines.append(f"  - {cid}")
    for n in state.notes:
        lines.append(f"备注: {n}")
    lines.append("")
    return "\n".join(lines)


def migrate_secrets(
    state: SecretsState, apply: bool, *, force_all_dangling: bool = False
) -> dict:
    """把库内明文 api_key 迁到 ``secrets.json``（**默认 dry-run**）。

    口径：
    - **dry-run（默认）**：只输出「将要做什么」，**不写任何文件、不改任何库**；
    - ``--apply``：先把 key 写进 ``secrets.json``（原子写 + 复读校验），
      **再**清库内明文 ⇒ 顺序保证「key 已落盘」先于「明文被清」。

    ⚠️ 这是**改用户凭据数据**的动作：执行前必须由用户明确授权。

    ⚠️ **不可判 ⇒ 一律拒绝执行**（对抗审查 F-3，2026-10-23）：
    ``secrets.json`` **结构非法**（顶层 / ``api_keys`` 段不是对象）或「读不到运行库」
    ⇒ 运行期本来就只能解析出**空凭据** ⇒ 若拿它当**空基线**落盘，
    就是**静默把用户原有键重置为 {}** ⇒ 必须**响亮失败**（退出码 2），
    且**不写任何文件、不写备份、不清任何库内明文**。

    ⚠️ **归属闸门**（2026-10-23 Lead 亲自复现的 major，修法 C）：
    迁移是**加法**动作（不删任何东西）⇒ **不受**「悬空不可判」拦截
    ⇒ 但它会把**库内明文**搬进 secrets.json ⇒ 若 ``--db`` 指到**别的库**，
    那个库的明文凭据会被写进本项目的 secrets.json（**跨项目凭据混入**）
    ⇒ 迁移**也**必须有归属证据：
    - ``api_keys`` 段**为空 / 不存在** ⇒ **首次迁移**（无可比键）⇒ **放行**；
    - ``api_keys`` 段**非空** ⇒ 要求 ``secrets`` 的键 ∩ ``entity_keys`` ≠ ∅
      ⇒ 否则**拒绝执行**（退出码 2、零落盘、零备份、不清任何库内明文）；
    - ``--force-all-dangling``（= 归属确认）⇒ 可放行（并醒目提示）。
    """
    if state.blocked_reasons:
        raise RuntimeError(
            "无法判定凭据状态，已中止迁移（**未写任何文件、未清任何库内明文**）："
            + "；".join(state.blocked_reasons)
            + "。⚠️ 结构非法时**绝不可**把「读不出来的空凭据」当成空基线落盘"
            "（那会静默抹掉用户原有键）。"
        )
    # --- 归属闸门（**加法**动作也必须有归属证据 ⇒ 防跨项目凭据混入）---
    if (
        state.secrets_entry_count
        and not force_all_dangling
        and not state.ownership_verified
    ):
        raise RuntimeError(
            "归属未经证实（secrets.json 的 api_keys 段非空，但没有任何键对得上运行库的"
            "档案 id / 连接 id）⇒ 已中止迁移（**未写任何文件、未清任何库内明文**）："
            + (state.ownership_reason or "归属无据")
            + "。⚠️ 若 --db 指到了别的项目的库，迁移会把那个库的明文凭据写进"
            "本项目的 secrets.json（跨项目凭据混入）；确认 --db 指向的就是本项目的"
            "运行库后，可加 --force-all-dangling 显式声明归属。"
        )
    # ⚠️ 首次迁移（secrets 的 api_keys 段为空）⇒ 归属**无从验证** ⇒ note 必须写明。
    ownership_note = (
        "；归属未验证：secrets 为空（首次迁移，无可比键）"
        if not state.secrets_entry_count
        else ""
    )
    actions: list[str] = []
    for pid in state.plaintext_profiles:
        actions.append(f"secrets.json[api_keys][{pid}] = <当前 params_json.api_key>；清空该行明文")
    for cid in state.plaintext_connections:
        actions.append(f"secrets.json[api_keys][{cid}] = <当前 params_json.api_key>；清空该行明文")
    if not actions:
        return {
            "applied": False,
            "actions": [],
            "note": "没有需要迁移的明文 api_key" + ownership_note,
        }
    if not apply:
        return {
            "applied": False,
            "actions": actions,
            "note": "dry-run（未加 --apply ⇒ 未写任何文件、未改任何库）" + ownership_note,
        }

    # --- 1) 写 secrets.json（原子写 + 复读校验）---
    secrets_path = Path(state.secrets_path)
    payload: dict = {}
    if secrets_path.exists():
        try:
            loaded = json.loads(secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"secrets.json 解析失败，已中止迁移：{exc}") from exc
        # ⚠️ **顶层不是对象 ⇒ 拒绝**（F-3）：原先 ``payload = {}`` 会把用户的整个文件
        # 替换成 ``{"api_keys": ...}`` ⇒ 静默抹掉原有内容。
        if not isinstance(loaded, dict):
            raise RuntimeError(
                "secrets.json 顶层不是对象，已中止迁移（**未写任何文件**）"
            )
        payload = loaded
    api_keys = payload.get("api_keys")
    if api_keys is None:
        # ✅ 合法空状态（首次迁移：文件里还没有 api_keys 段）
        api_keys = {}
    elif not isinstance(api_keys, dict):
        # ⚠️ **api_keys 段不是对象 ⇒ 拒绝**（F-3）：原先一律重置为 ``{}``
        # ⇒ **静默抹掉用户原有键**（且用户毫无察觉）。
        raise RuntimeError(
            "secrets.json 的 api_keys 段不是对象，已中止迁移（**未写任何文件**）"
        )
    # ⚠️ **写前强制备份**（对抗审查 F-3，2026-10-23）：迁移要**重写整个** secrets.json
    # ⇒ 旧版本必须可回退（与 ``--prune-dangling`` 的纪律一致）。
    backup_path: Path | None = None
    if secrets_path.exists():
        backup_path = _backup_secrets_file(secrets_path)
    conn = _open_readonly(Path(state.db_path))
    conflicts: list[str] = []
    try:
        rows = conn.execute(
            "SELECT profile_id, params_json FROM model_profiles"
        ).fetchall()
        for row in rows:
            key = _params_api_key(row["params_json"])
            if key is None:
                continue
            rid = str(row["profile_id"])
            existing = api_keys.get(rid)
            if isinstance(existing, str) and existing and existing != key:
                # ⚠️ **同名条目不静默覆盖**（对抗审查 F-3）：文件里已有**不同的**值 ⇒
                # 运行期本来就用**文件里的**那个（``secrets[profile_id]`` 的优先级
                # 高于 ``params_json.api_key``）⇒ 保留文件里的值，**库内明文照常清掉**
                # （解析结果不变）⇒ 绝不拿旧明文顶掉用户更新的 key。
                conflicts.append(f"{rid}（secrets.json 里的值与库内明文不同 ⇒ 保留文件里的值）")
                continue
            api_keys[rid] = key
        rows = conn.execute(
            "SELECT connection_id, params_json FROM model_connections"
        ).fetchall()
        for row in rows:
            key = _params_api_key(row["params_json"])
            if key is None:
                continue
            cid = str(row["connection_id"])
            existing = api_keys.get(cid)
            if isinstance(existing, str) and existing and existing != key:
                conflicts.append(f"{cid}（secrets.json 里的值与库内明文不同 ⇒ 保留文件里的值）")
                continue
            api_keys[cid] = key
    finally:
        conn.close()
    payload["api_keys"] = api_keys
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = secrets_path.with_name(secrets_path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, secrets_path)

    # --- 2) 清库内明文（仅在 secrets.json 落盘成功之后）---
    conn = sqlite3.connect(str(state.db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for table, id_col in (
            ("model_profiles", "profile_id"),
            ("model_connections", "connection_id"),
        ):
            rows = conn.execute(
                f"SELECT {id_col}, params_json FROM {table}"
            ).fetchall()
            for rid, params_json in rows:
                if _params_api_key(params_json) is None:
                    continue
                data = json.loads(params_json)
                data.pop("api_key", None)
                conn.execute(
                    f"UPDATE {table} SET params_json = ? WHERE {id_col} = ?",
                    (json.dumps(data, ensure_ascii=False), rid),
                )
        conn.commit()
    finally:
        conn.close()
    result: dict = {
        "applied": True,
        "actions": actions,
        "note": "已写入 secrets.json 并清空库内明文" + ownership_note,
    }
    if backup_path is not None:
        result["backup_path"] = str(backup_path)
    if conflicts:
        result["conflicts"] = conflicts
        result["note"] = (
            "已写入 secrets.json 并清空库内明文；⚠️ 有同名条目与库内明文**不一致** ⇒ "
            "已**保留 secrets.json 里的值**（运行期本来就用它，解析结果不变）" + ownership_note
        )
    return result


def _backup_secrets_file(secrets_path: Path) -> Path:
    """落盘前把现有 ``secrets.json`` 复制成 ``<name>.bak-<时间戳>``（**同目录、原子**）。

    ⚠️ **失败即中止**（抛异常）⇒ **不落盘**任何删除（凭据丢了不可逆）。
    原子做法：先写 ``.tmp`` 再 ``os.replace`` ⇒ 不会留下半截备份文件。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = secrets_path.with_name(f"{secrets_path.name}.bak-{stamp}")
    tmp = backup.with_name(backup.name + ".tmp")
    try:
        shutil.copy2(secrets_path, tmp)
        os.replace(tmp, backup)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"备份 secrets.json 失败（{backup}），已中止清理（**未写任何文件**）：{exc}"
        ) from exc
    return backup


def prune_dangling_secrets(state: SecretsState, apply: bool) -> dict:
    """把 ``secrets.json`` 里**悬空**的条目删掉（**默认 dry-run**）。

    为什么需要这个动作
    ------------------
    本脚本**检测**「悬空条目」（键对不上任何档案 / 连接 / provider）并把它算作
    「不合规」，但原先**只给了迁移命令**（把库内明文搬进文件），没有给「清掉
    悬空条目」的出路 ⇒ 一次档案删除之后，``secrets.json`` 里会永久留下一条
    指向**已删档案**的死密钥，巡检从此永远报不合规（红灯常亮 ⇒ 守卫被忽略）。

    口径
    ----
    - 「悬空」= 与 :func:`decide_secrets` **同一判据**（键对不上任何档案 / 连接 /
      provider / **env_name 形态**）⇒ 守卫报的悬空，就是这里要删的（不会出现两套口径）；
    - dry-run（默认）：只报「将要删哪些键」，**不写任何文件**；
    - ``apply=True``：**先备份**（``secrets.json.bak-<时间戳>``，同目录、原子），
      再**原子写**（先写 ``.tmp`` 再 ``os.replace``），
      **只删 ``api_keys`` 段里被点名的键**，其余段原样保留（不丢任何配置）。

    ⚠️ **三道保险**（F-1 / F-2，2026-10-23 对抗审查 + Lead 复现）：
    1. **读表失败 ⇒ 拒绝执行**：读库失败（库不存在 / 缺表 / sqlite 错误）
       ⇒ 抛错中止（CLI ⇒ 退出码 2），**不写任何文件**。
       ⚠️ 「已知集合为空」**不走**这条（``blocked_reasons`` 现在**只装读表失败**）
       ——它归第 2 条**归属闸门**（「全部是死键」与「根本没有引用数据」不可区分
       ⇒ 同样拒绝清理）。
    2. **删前复核**：每个候选键都过一遍**生产解析函数**
       :func:`packages.core.secrets_store.resolve_profile_api_key` ⇒
       **仍能解析出非空密钥 ⇒ 不许删**（覆盖 env_name 形态的运行期第 3 优先级）。

    ⚠️ 被删的是**已删档案的死密钥**：那个档案若将来被恢复，其密钥需重新提供。
    这是刻意的取舍——「一个对不上任何东西的密钥」与「一个有用的密钥」不可兼得，
    而密钥留在文件里还多一份可被误用的副本。

    ⚠️ **坏文件必须响亮失败**：``secrets.json`` 解析不了 ⇒ **抛错中止**（CLI ⇒ 退出码 2），
    **不得**当成「没有悬空条目，什么都不用做」——那会让用户以为清理已做、实际文件
    仍坏着（且坏文件里的密钥状态无人知晓）。

    ⚠️ **第三道保险：归属闸门**（2026-10-23 Lead 亲自复现的 major）：
    「已知集合**非空**」**不等于**「这个库属于这个 secrets 文件」。把 ``--db``
    指到**另一个库**（打错路径 / 别的项目 / 合成夹具）时，已知集合照样非空，
    却与 secrets 的键**一个都对不上** ⇒ 每个键都被判悬空 ⇒ **一次清理清空整个
    凭据库**，而退出码仍是 0（报「合规」）。
    ⇒ 判据必须是**携带项目身份**的名字 ⇒ **实体身份**（``profile_id`` /
    ``connection_id``）⇒ provider 级 / env 级键**不携带身份** ⇒ **不得**当归属证据。
    ⇒ 归属无据 ⇒ 悬空条目**不可判** ⇒ **拒绝执行**（退出码 2，零落盘）；
    确认归属后可加 ``--force-all-dangling`` 显式放行。
    """
    # --- 0) 先判「读表是否成功」：读表失败 ⇒ **拒绝清理**（与「归属无据」两条路）---
    if state.blocked_reasons:
        raise RuntimeError(
            "无法判定凭据状态（**读表失败**），已中止清理（**未写任何文件**）："
            + "；".join(state.blocked_reasons)
            + "。⚠️ 读不到运行库 ⇒ 已知集合**不完整** ⇒ 绝不可把「读不出来的空集合」"
            "当成「全部悬空」（否则一次误指 --db 就会清空用户全部凭据）。"
        )
    # ⚠️ **归属闸门**（2026-10-23 Lead 亲自复现的 major）：
    # 「这个库属于这个 secrets 文件吗？」——判据 = secrets 的**可用键** ∩ 库内
    # **实体身份**（profile_id / connection_id）⇒ provider 级 / env 级键**不算**
    # 归属证据（不携带项目身份）⇒ 一个别名键就能骗过闸门 ⇒ 错库时真凭据被整条
    # 删掉、退出码仍是 0（报「合规」）。
    # ⇒ 归属无据 ⇒ 悬空条目**不可判** ⇒ **拒绝执行**（零落盘、退出码 2）。
    if state.dangling_undecidable:
        raise RuntimeError(
            "归属未经证实 ⇒ 悬空条目不可判，已中止清理（**未写任何文件**）："
            + state.dangling_undecidable
            + "。⚠️ 请确认 --db 指的是**本项目**的运行库"
            "（指错库会把好端端的凭据判成悬空并清空）；"
            "确认无误可加 --force-all-dangling 显式声明归属。"
        )
    secrets_path = Path(state.secrets_path)
    if not secrets_path.exists():
        # ✅ **文件不存在 = 合法状态**（还没迁过；运行期 ``load_api_keys`` 返回 {}
        # 且**不报 warning**）⇒ 一个键都没有 ⇒ **没有可清理的条目**（不是错误）。
        return {
            "applied": False,
            "actions": [],
            "kept": [],
            "note": "secrets.json 不存在（没有可清理的条目）",
        }
    try:
        payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"secrets.json 解析失败，已中止清理：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("secrets.json 顶层不是对象，已中止清理（**未写任何文件**）")
    api_keys = payload.get("api_keys")
    if api_keys is None:
        # ✅ **合法空状态**（运行期 ``if api_keys is None: return {}`` **不报 warning**）
        # ⇒ 文件里一个键都没有 ⇒ **没有可清理的条目**（不是错误，也不是不可判）。
        return {
            "applied": False,
            "actions": [],
            "kept": [],
            "note": "secrets.json 无 api_keys 段（没有可清理的条目）",
        }
    if not isinstance(api_keys, dict):
        # ⚠️ **结构非法 ⇒ 拒绝**（F-3）：不得把「读不出来的 api_keys」当成
        # 「没有悬空条目」照报合规，更不得拿它当基线落盘。
        raise RuntimeError(
            "secrets.json 的 api_keys 段不是对象，已中止清理（**未写任何文件**）"
        )

    if not state.dangling_keys:
        return {"applied": False, "actions": [], "note": "没有悬空条目"}
    # --- 1) 候选键复核（**走生产解析函数** ⇒ 仍被引用的键不许删，F-2） ---
    removable: list[str] = []
    kept: list[str] = []
    for k in state.dangling_keys:
        value = api_keys.get(k)
        if isinstance(value, str) and value and _is_still_resolvable(state, k, value):
            kept.append(k)
        else:
            removable.append(k)
    actions = [f"secrets.json[api_keys] 删除键 {k}" for k in removable]
    kept_note = (
        f"（{len(kept)} 条经生产解析函数复核判定**仍被引用** ⇒ 保留：{', '.join(kept)}）"
        if kept
        else ""
    )
    if not removable:
        return {
            "applied": False,
            "actions": [],
            "kept": kept,
            "note": f"没有可清理的悬空条目{kept_note}",
        }
    if not apply:
        return {
            "applied": False,
            "actions": actions,
            "kept": kept,
            "note": f"dry-run（未加 --apply ⇒ 未写任何文件）{kept_note}",
        }

    # --- 2) 写前强制备份（同目录、原子；失败即中止 ⇒ 不落盘） ---
    backup_path = _backup_secrets_file(secrets_path)

    removed = [k for k in removable if k in api_keys]
    for k in removed:
        api_keys.pop(k, None)
    payload["api_keys"] = api_keys
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = secrets_path.with_name(secrets_path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, secrets_path)
    return {
        "applied": True,
        "actions": actions,
        "removed": removed,
        "kept": kept,
        "backup_path": str(backup_path),
        "note": (
            f"已从 secrets.json 删除 {len(removed)} 条悬空条目"
            f"（清理前已备份到 {backup_path.name}）{kept_note}"
        ),
    }


