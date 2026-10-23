#!/usr/bin/env python
"""内容仓 push 防护巡检（**只读**）。

背景
----
AGENTS.md 硬规则 5「内容仓不推 GitHub」此前**只有纪律、没有技术守卫**：内容仓
``D:/zcodeproject/NovelOS-Content`` 的 ``origin`` 仍指向
``https://github.com/charlesnty612/NovelOS-Content.git``（fetch / push 同址），
且本地**领先远端 17 个提交** ⇒ 一次误 push 即发布全书（不可撤回）。

本脚本**只读**地回答一个问题：**当前内容仓能不能被误推？**

用法
----
::

    # 模式一（默认）：人读形态 —— 内容仓 push 防护
    python scripts/check_content_repo_guard.py
    python scripts/check_content_repo_guard.py --content-dir D:/zcodeproject/NovelOS-Content

    # 机器可读形态
    python scripts/check_content_repo_guard.py --json

    # 模式二：凭据收敛（D-18）—— 悬空 secrets 条目 + 库内明文 api_key
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db
    python scripts/check_content_repo_guard.py secrets --json
    # **两个动作各自独立**（默认全是 dry-run；--apply 是写权限总闸）：
    #   迁移（库内明文 → secrets.json，并清空库内明文）
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --migrate --apply
    #   清理悬空条目（只动 secrets.json，不动库内明文）
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --prune-dangling --apply

⚠️ **清理悬空条目的两道保险**（2026-10-23 对抗审查 F-1 / F-2）
-----------------------------------------------------------
1. **诊断不可信 ⇒ 拒绝执行**（退出码 2，**不写任何文件**）：运行库不存在 /
   缺表 / 读表失败，**或**「已知集合为空」⇒ 一律**拒绝清理**。
   「已知集合为空」**绝不可**被解读为「全部悬空」——否则一次误指 ``--db``
   （打错路径 / 未迁移的库 / 另一个项目库 / 空库）就会**清空用户全部凭据**，
   且无备份、无确认、还返回「合规」（绿）。
2. **删前复核 + 写前备份**：每个候选键都过一遍**生产解析函数**
   ``packages.core.secrets_store.resolve_profile_api_key`` ⇒ **仍能解析出非空
   密钥 ⇒ 不许删**（覆盖运行期第 3 优先级 ``secrets[<env_name>]``）；
   真正落盘前先备份成 ``secrets.json.bak-<时间戳>``（同目录、原子、失败即中止）。
    #   两件一起做
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --migrate --prune-dangling --apply
    #   向后兼容：**单独 --apply** = 「迁移 + 执行」
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --apply

⚠️ **``secrets.json`` 结构非法 ⇒ 不可判（退出码 2）**（2026-10-23 对抗审查 F-3）
--------------------------------------------------------------------------
``secrets.json`` **存在但结构非法**（顶层不是对象 / ``api_keys`` 段不是对象）
⇒ 生产侧 ``packages.core.secrets_store._read_file_fresh()`` 会 log warning 并
**返回空凭据**（运行期拿不到 key）⇒ 那不是「没有凭据」，是「**凭据读不出来**」
⇒ 巡检必须判「**不可判**」⇒ **退出码 2**（否则真出问题却报绿）；``--migrate`` /
``--prune-dangling`` **一律拒绝执行**（不写任何文件、不写备份、不清库）
⇒ **三条路径同口径**。

⚠️ **反向不变量（不得过修）**：``secrets.json`` **不存在**、**没有 ``api_keys``
段**、或该段为 ``null`` ⇒ 运行期 ``_read_file_fresh()`` **不报 warning**、返回
``{}`` ⇒ **合法的空凭据状态** ⇒ 巡检**仍然报合规（退出码 0）**。

⚠️ **``--json`` 的计数口径**（2026-10-23 对抗审查 F-6）：``action_count`` =
**实际落地的条数**（dry-run ⇒ ``0``），「打算做 N 条」用 ``candidate_count`` 表达；
``migration`` / ``prune`` 两段都带 ``conflicts`` 与 ``backup_path``（与文本模式同口径）。

检查项（全部只读）
------------------
1. ``origin`` 的 **fetch / push URL** 是什么（``git remote -v``）；
2. 是否存在**有效的** ``pre-push`` 钩子（``git rev-parse --git-path hooks`` → 文件存在
   且非空；``.sample`` 结尾不算）；
3. 本地**领先**上游多少提交（``git rev-list --count @{u}..HEAD``；无上游 ⇒ 0）；
4. （``secrets`` 模式）``secrets.json`` 的条目是否**悬空**（对不上任何档案 / 连接 /
   provider / **env_name 形态**）、运行库里 ``model_profiles`` / ``model_connections``
   的 ``params_json`` 是否仍有**明文 api_key**。

结论口径
--------
- **能被误推**（退出码 ``1``）：push 通道**仍开启**（push URL 指向真实远端）
  **且**没有 ``pre-push`` 钩子拦截 ⇒ 一次误 push 即可发布全部内容。
- **不能被误推**（退出码 ``0``）：存在技术屏障 —— push URL 已禁用（哨兵值）**或**
  装了 ``pre-push`` 钩子。
- **巡检自身出错**（退出码 ``2``）：目标目录不是 git 仓库 / git 不可用。

「禁用 push」的哨兵值（``--push`` URL 等于其中任一即视为已禁用）：
``no_push`` / ``nopush`` / ``no-push`` / ``disabled`` / ``none`` / ``off`` /
``://disabled``；**空值**同样视为禁用（没有 push 目标 ⇒ 推不出去）。
另支持**禁用 scheme 前缀**（``no-push:`` / ``nopush:`` / ``no_push:`` / ``disabled:``）
—— 本机实际安装的哨兵是 ``no-push://novelos-content-push-disabled``，**必须**认得。

⚠️ **只读纪律**：本脚本的**巡检**只调用 ``git rev-parse`` / ``git remote -v`` /
``git rev-list`` 这三条**只读**子命令，并以 SQLite **只读 URI**（``mode=ro``）打开
运行库；**绝不**调用 ``fetch`` / ``push`` / ``commit`` / ``add`` / ``reset`` 等任何会
改远端或本地引用的命令，也**绝不**在内容仓里写文件或改 git 状态（痕迹迁移与 push
防护的落地动作由 Lead 执行）。

唯一的**写**动作是 ``secrets`` 模式的 ``--apply``（凭据迁移：写 ``secrets.json`` +
清库内明文 api_key）—— 属**改用户凭据数据**，必须由用户明确授权后显式执行；
默认是 dry-run（只报「将要做什么」，不写文件、不改库）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
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

DEFAULT_CONTENT_DIR = Path("D:/zcodeproject/NovelOS-Content")

EXIT_OK = 0
"""合规：有技术屏障，不能被误推。"""

EXIT_UNSAFE = 1
"""不合规：无技术屏障，能被误推。"""

EXIT_ERROR = 2
"""巡检自身出错（不是 git 仓库 / git 不可用）。"""

#: 「push 已禁用」的哨兵值（大小写不敏感；**精确值**形态）。
DISABLED_PUSH_SENTINELS: frozenset[str] = frozenset(
    {
        "no_push",
        "nopush",
        "no-push",
        "disabled",
        "none",
        "off",
        "://disabled",
    }
)

#: 「push 已禁用」的**哨兵 URL 前缀**（scheme 形态）——覆盖
#: ``no-push://novelos-content-push-disabled`` 这类「禁用 scheme + 说明」的写法。
DISABLED_PUSH_PREFIXES: tuple[str, ...] = (
    "no-push:",
    "nopush:",
    "no_push:",
    "disabled:",
)

#: 允许调用的 git 子命令（**只读**白名单；``tests/unit/test_check_content_repo_guard.py``
#: 会逐条核对脚本实际发出的调用都落在这个集合内）。
READONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {"rev-parse", "remote", "rev-list", "config", "status", "symbolic-ref"}
)


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """跑一条**只读** git 命令 → ``(returncode, stdout, stderr)``。

    刻意**不**用 ``check=True``：巡检要区分「命令失败」与「命令成功但输出为空」
    （例如没有上游时 ``rev-list @{u}..HEAD`` 必然失败）。
    """
    proc = subprocess.run(  # noqa: S603 —— 固定 git + 只读子命令
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


@dataclass
class GuardState:
    """巡检采集到的事实（**不含结论**；结论由 :func:`decide` 推导）。"""

    content_dir: str
    is_git_repo: bool = False
    fetch_url: str | None = None
    push_url: str | None = None
    pre_push_hook_path: str | None = None
    pre_push_hook_present: bool = False
    upstream: str | None = None
    ahead_count: int | None = None
    notes: list[str] = field(default_factory=list)


def _parse_remote_v(text: str) -> tuple[str | None, str | None]:
    """解析 ``git remote -v`` → ``(fetch_url, push_url)``。

    形态：``<name>\\t<url> (fetch)`` / ``<name>\\t<url> (push)``；``origin`` 优先。
    """
    fetch: str | None = None
    push: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2]
        if name != "origin":
            continue
        if kind == "(fetch)":
            fetch = url
        elif kind == "(push)":
            push = url
    return fetch, push


def _is_disabled_push_url(url: str | None) -> bool:
    """push URL 是否表示「已禁用推送」。

    三种形态都算禁用：
    1. ``None`` / 空串（没有 push 目标 ⇒ 推不出去）；
    2. 精确命中 :data:`DISABLED_PUSH_SENTINELS`（如 ``no_push``）；
    3. 使用**禁用 scheme** 前缀（如 ``no-push://novelos-content-push-disabled``）——
       本机实际安装的哨兵就是这个形态，**必须**认得，否则巡检会把「已禁用」
       误报成「push 通道仍开启」。
    """
    if url is None:
        return True
    value = url.strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered in DISABLED_PUSH_SENTINELS:
        return True
    return any(lowered.startswith(prefix) for prefix in DISABLED_PUSH_PREFIXES)


def collect_state(content_dir: Path) -> GuardState:
    """采集内容仓的 push 防护状态（**只读**）。"""
    state = GuardState(content_dir=str(content_dir))

    rc, out, _err = _run_git(["rev-parse", "--is-inside-work-tree"], content_dir)
    if rc != 0 or out.strip() != "true":
        state.notes.append(f"不是 git 仓库（或 git 不可用）：{content_dir}")
        return state
    state.is_git_repo = True

    _rc, remote_out, _err = _run_git(["remote", "-v"], content_dir)
    state.fetch_url, state.push_url = _parse_remote_v(remote_out)

    # hooks 路径：尊重 core.hooksPath；相对路径按仓库根解析。
    _rc, hook_out, _err = _run_git(["rev-parse", "--git-path", "hooks"], content_dir)
    hooks_rel = hook_out.strip()
    if hooks_rel:
        hook_path = Path(hooks_rel)
        if not hook_path.is_absolute():
            hook_path = content_dir / hook_path
        candidate = hook_path / "pre-push"
        if candidate.is_file() and candidate.stat().st_size > 0:
            state.pre_push_hook_present = True
            state.pre_push_hook_path = str(candidate)
        elif candidate.is_file():
            state.notes.append(
                f"pre-push 钩子存在但为空（{candidate}）——不构成屏障"
            )
    else:
        state.notes.append("无法定位 hooks 目录（git rev-parse --git-path hooks 无输出）")

    _rc, up_out, _err = _run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], content_dir
    )
    upstream = up_out.strip()
    state.upstream = upstream or None

    rc, ahead_out, _err = _run_git(["rev-list", "--count", "@{u}..HEAD"], content_dir)
    if rc == 0 and ahead_out.strip().isdigit():
        state.ahead_count = int(ahead_out.strip())
    else:
        state.ahead_count = 0
        state.notes.append("无法统计领先提交数（无上游或仓库无提交）")

    return state


def decide(state: GuardState) -> dict:
    """由采集到的事实推导结论（**纯函数**）。"""
    if not state.is_git_repo:
        return {
            "can_be_pushed": None,
            "verdict": "unknown",
            "exit_code": EXIT_ERROR,
            "reason": "目标目录不是 git 仓库（无法巡检）",
        }

    push_disabled = _is_disabled_push_url(state.push_url)
    barriers: list[str] = []
    if push_disabled:
        barriers.append("push URL 已禁用")
    if state.pre_push_hook_present:
        barriers.append("存在 pre-push 钩子")

    can_be_pushed = not push_disabled and not state.pre_push_hook_present

    reason_bits: list[str] = []
    if push_disabled:
        reason_bits.append("push URL 已禁用")
    else:
        reason_bits.append(f"push URL 仍指向远端（{state.push_url}）")
    if state.pre_push_hook_present:
        reason_bits.append("有 pre-push 钩子拦截")
    else:
        reason_bits.append("无 pre-push 钩子拦截")
    if state.ahead_count:
        reason_bits.append(f"本地领先上游 {state.ahead_count} 个提交")

    warnings: list[str] = []
    if not push_disabled and state.pre_push_hook_present:
        warnings.append(
            "当前唯一的屏障是 pre-push 钩子——钩子被删除 / 绕过即失守；"
            "建议同时把 push URL 置为禁用值（no_push）"
        )
    if not push_disabled and state.push_url and "github.com" in state.push_url.lower():
        warnings.append(
            "push 目标含 github.com——一次误 push 即发布全书（不可撤回）"
        )

    return {
        "can_be_pushed": can_be_pushed,
        "verdict": "yes" if can_be_pushed else "no",
        "exit_code": EXIT_UNSAFE if can_be_pushed else EXIT_OK,
        "barriers": barriers,
        "reason": "；".join(reason_bits),
        "warnings": warnings,
    }


def render_text(state: GuardState, verdict: dict) -> str:
    """人读形态。"""
    lines = [
        "===== 内容仓 push 防护巡检（只读）=====",
        f"  内容仓: {state.content_dir}",
        f"  fetch URL: {state.fetch_url or '（无）'}",
        f"  push  URL: {state.push_url or '（无 ⇒ 推不出去）'}",
        f"  pre-push 钩子: {state.pre_push_hook_path or '（无）'}",
        f"  上游: {state.upstream or '（无）'}",
        f"  本地领先远端: {state.ahead_count if state.ahead_count is not None else 0} 个提交",
        "",
    ]
    if verdict["verdict"] == "unknown":
        lines.append("结论: 无法判定")
        lines.append(f"依据: {verdict['reason']}")
    elif verdict["can_be_pushed"]:
        lines.append("结论: 能被误推（是）")
        lines.append(f"依据: {verdict['reason']}")
    else:
        lines.append("结论: 不能被误推（否）")
        lines.append(f"依据: {verdict['reason']}")
        lines.append("屏障: " + "、".join(verdict["barriers"]))
    for w in verdict.get("warnings") or []:
        lines.append(f"警告: {w}")
    for n in state.notes:
        lines.append(f"备注: {n}")
    lines.append("")
    return "\n".join(lines)


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
    known_keys: set[str] = field(default_factory=set)
    #: **真正的读取失败**（库不存在 / 缺表 / sqlite 错误）——与「集合为空」**分开记**。
    read_errors: list[str] = field(default_factory=list)
    #: 「无法判定」的原因（读表失败 **或** 已知集合为空）⇒ 清理动作必须拒绝执行。
    blocked_reasons: list[str] = field(default_factory=list)
    #: 「悬空条目**不可判**」的原因（见 :func:`collect_secrets_state` 的**交集闸门**）。
    #:
    #: ⚠️ 与 ``blocked_reasons`` **刻意分开**：它只否证「悬空」这一个判断，
    #: **不**否证「库内明文」——``--migrate`` 是**加法**动作（把库内明文搬进文件），
    #: 不依赖「悬空」判断 ⇒ **不受**它拦截（否则一条手写的 secrets 文件会把
    #: 迁移也堵死，而迁移本来不破坏任何东西）。
    dangling_undecidable: str | None = None
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
    sqlite 错误）或已知集合为空，「每个键都悬空」是**误判** ⇒ 一次误指 ``--db``
    （打错路径 / 未迁移的库 / 另一个项目库 / 空库）就可能**清空用户全部凭据**，
    且无备份、无确认、还返回「合规」（绿）。

    ⇒ 「读表失败」与「已知集合为空」**分别记录**（``read_errors`` / ``known_keys``），
    两者任一成立 ⇒ 判为「**无法判定**」（``blocked_reasons`` 非空）⇒ 悬空条目
    **不判**（宁可不判，不可全判）、清理动作**拒绝执行**（退出码 2，不写任何文件）。

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
                    state.secrets_keys = sorted(str(k) for k in api_keys)
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
    for slot in list(state.profile_slots) + list(state.connection_slots):
        known |= {n.lower() for n in _slot_names(slot)}
    state.known_keys = known

    # --- 悬空条目 ---
    if state.read_errors:
        # 读库失败 ⇒ 「已知集合」**不完整** ⇒ **不判悬空**（宁可不判，不可全判）。
        state.blocked_reasons = list(state.read_errors)
        return state
    if not known:
        # 已知集合为空 ⇒ 「全是死键」与「根本没有引用数据」**不可区分** ⇒ 不判。
        state.blocked_reasons = [
            "运行库已知集合为空（没有任何档案 / 连接 / provider 引用）——"
            "无法区分「全部是死键」与「根本没有引用数据」"
        ]
        return state
    # ⚠️ **交集闸门**（2026-10-23 Lead 自踩事故暴露的 critical）：
    # 「已知集合**非空**」**不等于**「这个库属于这个 secrets 文件」。
    # 误把 ``--db`` 指到**另一个库**（打错路径 / 别的项目 / 合成夹具）时，
    # 已知集合照样非空，但**与 secrets 的键一个都对不上** ⇒ 每个键都被判悬空
    # ⇒ ``--prune-dangling --apply`` 会**清空整个凭据库**，而退出码仍是 0（报「合规」）。
    # ⇒ **交集为空 ⇒ 悬空条目不可判**（**不是**「全悬空」）⇒ 清理动作必须拒绝执行。
    if state.secrets_keys and not force_all_dangling:
        secrets_lower = {k.lower() for k in state.secrets_keys}
        if not (secrets_lower & known):
            state.dangling_undecidable = (
                f"secrets.json 里的 {len(state.secrets_keys)} 个键**一个都**对不上运行库里的"
                "任何档案 / 连接 / provider ⇒ 这个运行库很可能**不是**本 secrets 文件所属项目的库"
                "（或 secrets 存的是别的形态的键）⇒ 悬空条目**不可判**"
            )
            state.notes.append(state.dangling_undecidable)
            return state
    state.dangling_keys = [k for k in state.secrets_keys if k.lower() not in known]
    return state


def decide_secrets(state: SecretsState) -> dict:
    """由采集到的事实推导凭据收敛结论（**纯函数**）。

    ⚠️ **「无法判定」是独立结论**（F-1）：读库失败 / 已知集合为空 ⇒ 悬空条目
    **不可判** ⇒ 退出码 **2**（巡检自身出错），**不是** 0（合规）也不是 1（不合规）。
    「合规」在这里意味着「已判定且没有悬空条目」；判不出来时不得报绿。
    """
    issues: list[str] = []
    if state.dangling_keys:
        issues.append(f"悬空 secrets 条目 {len(state.dangling_keys)} 条")
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
            "exit_code": EXIT_ERROR,
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
        "exit_code": EXIT_OK if not issues else EXIT_UNSAFE,
        "issues": issues,
        "dangling_keys": state.dangling_keys,
        "plaintext_profiles": state.plaintext_profiles,
        "plaintext_connections": state.plaintext_connections,
        "blocked": False,
        "blocked_reasons": [],
    }


def render_secrets_text(state: SecretsState, verdict: dict) -> str:
    """凭据收敛巡检的人读形态（**不含任何密钥值**）。"""
    lines = [
        "===== 凭据收敛巡检（只读；D-18）=====",
        f"  运行库: {state.db_path}",
        f"  secrets.json: {state.secrets_path}"
        f"{'' if state.secrets_exists else '（不存在）'}",
        f"  secrets 条目数: {len(state.secrets_keys)}",
        f"  悬空条目数: {len(state.dangling_keys)}",
        f"  库内明文 api_key（档案）: {len(state.plaintext_profiles)} 行",
        f"  库内明文 api_key（连接）: {len(state.plaintext_connections)} 行",
        "",
    ]
    if verdict["ok"]:
        lines.append("结论: 合规（无悬空条目、库内无明文 api_key）")
    elif verdict.get("blocked"):
        # ⚠️ **不可判的原因不止一种**：读不到运行库 / 已知集合为空 /
        # ``secrets.json`` 结构非法（F-3）⇒ 结论行**不预设**具体原因，
        # 逐条原因在下方「无法判定:」明细里给出（**文本模式必须说清**）。
        lines.append("结论: 无法判定（诊断不可信 ⇒ 不判定悬空条目；原因见下方明细）")
    else:
        lines.append("结论: 不合规（" + "；".join(verdict["issues"]) + "）")
    if verdict.get("blocked"):
        for r in verdict.get("blocked_reasons") or []:
            lines.append(f"无法判定: {r}")
    if state.dangling_keys:
        lines.append("悬空条目（secrets.json 里的键对不上任何档案 / 连接 / provider）:")
        for k in state.dangling_keys:
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


def migrate_secrets(state: SecretsState, apply: bool) -> dict:
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
    """
    if state.blocked_reasons:
        raise RuntimeError(
            "无法判定凭据状态，已中止迁移（**未写任何文件、未清任何库内明文**）："
            + "；".join(state.blocked_reasons)
            + "。⚠️ 结构非法时**绝不可**把「读不出来的空凭据」当成空基线落盘"
            "（那会静默抹掉用户原有键）。"
        )
    actions: list[str] = []
    for pid in state.plaintext_profiles:
        actions.append(f"secrets.json[api_keys][{pid}] = <当前 params_json.api_key>；清空该行明文")
    for cid in state.plaintext_connections:
        actions.append(f"secrets.json[api_keys][{cid}] = <当前 params_json.api_key>；清空该行明文")
    if not actions:
        return {"applied": False, "actions": [], "note": "没有需要迁移的明文 api_key"}
    if not apply:
        return {
            "applied": False,
            "actions": actions,
            "note": "dry-run（未加 --apply ⇒ 未写任何文件、未改任何库）",
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
        "note": "已写入 secrets.json 并清空库内明文",
    }
    if backup_path is not None:
        result["backup_path"] = str(backup_path)
    if conflicts:
        result["conflicts"] = conflicts
        result["note"] = (
            "已写入 secrets.json 并清空库内明文；⚠️ 有同名条目与库内明文**不一致** ⇒ "
            "已**保留 secrets.json 里的值**（运行期本来就用它，解析结果不变）"
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

    ⚠️ **两道保险**（F-1 / F-2，2026-10-23 对抗审查）：
    1. **诊断不可信 ⇒ 拒绝执行**：读库失败（库不存在 / 缺表 / sqlite 错误）或
       「已知集合为空」⇒ 抛错中止（CLI ⇒ 退出码 2），**不写任何文件**。
       「已知集合为空」**绝不可**被解读为「全部悬空」——那会让一次误指 ``--db``
       直接清空用户全部凭据且返回「合规」（绿）。
    2. **删前复核**：每个候选键都过一遍**生产解析函数**
       :func:`packages.core.secrets_store.resolve_profile_api_key` ⇒
       **仍能解析出非空密钥 ⇒ 不许删**（覆盖 env_name 形态的运行期第 3 优先级）。

    ⚠️ 被删的是**已删档案的死密钥**：那个档案若将来被恢复，其密钥需重新提供。
    这是刻意的取舍——「一个对不上任何东西的密钥」与「一个有用的密钥」不可兼得，
    而密钥留在文件里还多一份可被误用的副本。

    ⚠️ **坏文件必须响亮失败**：``secrets.json`` 解析不了 ⇒ **抛错中止**（CLI ⇒ 退出码 2），
    **不得**当成「没有悬空条目，什么都不用做」——那会让用户以为清理已做、实际文件
    仍坏着（且坏文件里的密钥状态无人知晓）。

    ⚠️ **第三道保险：交集闸门**（2026-10-23 Lead 自踩事故暴露的 critical）：
    「已知集合**非空**」**不等于**「这个库属于这个 secrets 文件」。把 ``--db``
    指到**另一个库**（打错路径 / 别的项目 / 合成夹具）时，已知集合照样非空，
    却与 secrets 的键**一个都对不上** ⇒ 每个键都被判悬空 ⇒ **一次清理清空整个
    凭据库**，而退出码仍是 0（报「合规」）。
    ⇒ 交集为空 ⇒ 悬空条目**不可判** ⇒ **拒绝执行**（退出码 2，零落盘）。
    """
    # --- 0) 先判「诊断是否可信」：读表失败 / 已知集合为空 ⇒ **拒绝清理** ---
    if state.blocked_reasons:
        raise RuntimeError(
            "无法判定悬空条目，已中止清理（**未写任何文件**）："
            + "；".join(state.blocked_reasons)
            + "。⚠️ 「已知集合为空」**绝不可**被解读为「全部悬空」"
            "（否则一次误指 --db 就会清空用户全部凭据）。"
        )
    # ⚠️ **交集闸门**（2026-10-23 Lead 自踩事故暴露的 critical）：
    # 「已知集合**非空**」**不等于**「这个库属于这个 secrets 文件」——
    # 把 ``--db`` 指到另一个库时，已知集合照样非空，却与 secrets 的键**一个都对不上**
    # ⇒ 每个键都被判悬空 ⇒ **一次清理清空整个凭据库**，而退出码仍是 0（报「合规」）。
    # ⇒ 交集为空 ⇒ 悬空条目**不可判** ⇒ **拒绝执行**（零落盘、退出码 2）。
    if state.dangling_undecidable:
        raise RuntimeError(
            "secrets.json 的键与运行库的已知集合**零交集** ⇒ 悬空条目不可判，"
            "已中止清理（**未写任何文件**）：" + state.dangling_undecidable
            + "。⚠️ 请确认 --db 指的是**本项目**的运行库"
            "（指错库会把好端端的凭据判成悬空并清空）。"
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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_content_repo_guard",
        description=(
            "只读巡检：内容仓的 push 防护状态（能否被误推）+ 凭据收敛"
            "（悬空 secrets 条目 / 库内明文 api_key）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 合规 / 1 = 不合规 / 2 = 巡检出错\n"
            "模式：push（默认，内容仓 push 防护）| secrets（凭据收敛）"
        ),
    )
    p.add_argument(
        "mode",
        nargs="?",
        choices=["push", "secrets"],
        default="push",
        help="巡检模式：push = 内容仓 push 防护（默认）；secrets = 凭据收敛（D-18）",
    )
    p.add_argument(
        "--content-dir",
        default=str(DEFAULT_CONTENT_DIR),
        help=f"内容仓根目录（默认 {DEFAULT_CONTENT_DIR}）",
    )
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"运行库路径（secrets 模式用；默认 {DEFAULT_DB_PATH}）",
    )
    p.add_argument(
        "--secrets-file",
        default=None,
        help="secrets.json 路径（secrets 模式用；默认 $NOVELOS_SECRETS_FILE 或仓库根 secrets.json）",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help=(
            "写权限总闸：让**本次请求的**写动作真的执行"
            "（默认全是 dry-run：只报「将要做什么」，不写文件、不改库）"
        ),
    )
    p.add_argument(
        "--migrate",
        action="store_true",
        help=(
            "secrets 模式：请求「凭据迁移」动作（把库内明文 api_key 搬进 secrets.json "
            "并清空库内明文）——默认 dry-run"
        ),
    )
    p.add_argument(
        "--prune-dangling",
        action="store_true",
        help=(
            "secrets 模式：请求「清理悬空条目」动作（删掉 secrets.json 里对不上任何"
            "档案/连接/provider 的死密钥）——默认 dry-run。"
            "⚠️ **只动 secrets.json，不动库内明文**"
        ),
    )
    p.add_argument(
        "--force-all-dangling",
        action="store_true",
        help=(
            "**危险**：确认「secrets.json 的键与运行库的已知集合零交集」时**仍**按常规"
            "判定悬空并执行清理。默认**拒绝**（退出码 2，零落盘）——因为「全部键都死」"
            "与「--db 指到了别的项目的库」在数据上**不可区分**，而后者会**一次清空"
            "整个凭据库**却仍报「合规」。只有确信 --db 指向的就是本项目的库、"
            "且确实要清掉全部键时才用。"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="机器可读输出（JSON）",
    )
    return p


def _run_push_mode(args: argparse.Namespace) -> int:
    content_dir = Path(args.content_dir)
    state = collect_state(content_dir)
    verdict = decide(state)

    if args.as_json:
        payload = {
            "content_dir": state.content_dir,
            "is_git_repo": state.is_git_repo,
            "fetch_url": state.fetch_url,
            "push_url": state.push_url,
            "push_disabled": _is_disabled_push_url(state.push_url),
            "pre_push_hook_present": state.pre_push_hook_present,
            "pre_push_hook_path": state.pre_push_hook_path,
            "upstream": state.upstream,
            "ahead_count": state.ahead_count,
            "can_be_pushed": verdict["can_be_pushed"],
            "verdict": verdict["verdict"],
            "barriers": verdict["barriers"],
            "reason": verdict["reason"],
            "warnings": verdict.get("warnings") or [],
            "notes": state.notes,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(state, verdict))

    return int(verdict["exit_code"])


def _run_secrets_mode(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    secrets_path = _resolve_secrets_path(args.secrets_file)
    state = collect_secrets_state(
        db_path, secrets_path, force_all_dangling=args.force_all_dangling
    )
    verdict = decide_secrets(state)

    # 动作选择（**一个开关只管一件事**）：
    #   --migrate          ⇒ 请求「迁移」（库内明文 → secrets.json）
    #   --prune-dangling   ⇒ 请求「清理悬空条目」（只动 secrets.json）
    #   --apply            ⇒ 写权限总闸（让本次请求的动作真的执行）
    # 向后兼容：**单独给 --apply**（没写 --migrate 也没写 --prune-dangling）等价于
    # 「迁移 + 执行」——这是本脚本自 2026-10-23 起就有的 CLI 口径，不得改。
    # ⚠️ **不要**把「迁移」与「清理」绑成一个动作：二者会互相干扰——迁移会用**库内明文**
    # 覆盖同名文件条目 ⇒ 若「--prune-dangling --apply」顺带触发迁移，用户看到的
    # 文件条目会变（而他只想删一条死密钥）⇒ 动作必须可独立选择。
    want_prune = bool(args.prune_dangling)
    want_migrate = bool(args.migrate) or (bool(args.apply) and not want_prune)

    prune: dict | None = None
    if want_prune:
        try:
            prune = prune_dangling_secrets(state, apply=args.apply)
        except Exception as exc:  # noqa: BLE001 —— CLI 边界：异常 → 诊断 + 退出码 2
            print(f"ERROR: 悬空条目清理失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        if prune.get("applied"):
            # 清理后**重新采集并重新判定** ⇒ 输出的结论反映**清理后**的真实状态，
            # 而不是清理前的旧快照（否则用户看到「已清理」却仍是「不合规」）。
            state = collect_secrets_state(
                db_path, secrets_path, force_all_dangling=args.force_all_dangling
            )
            verdict = decide_secrets(state)
        if prune.get("backup_path"):
            # ⚠️ 备份路径必须**看得见**（用户要知道凭据的旧版本在哪）。
            print(
                f"[备份] 清理前已备份 secrets.json -> {prune['backup_path']}",
                file=sys.stderr,
            )

    migration: dict | None = None
    if want_migrate:
        try:
            migration = migrate_secrets(state, apply=args.apply)
        except Exception as exc:  # noqa: BLE001 —— CLI 边界：异常 → 诊断 + 退出码 2
            print(f"ERROR: 凭据迁移失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        if migration.get("applied"):
            state = collect_secrets_state(
                db_path, secrets_path, force_all_dangling=args.force_all_dangling
            )
            verdict = decide_secrets(state)
        if migration.get("backup_path"):
            # ⚠️ 备份路径必须**看得见**（用户要知道凭据的旧版本在哪；对抗审查 F-3）。
            print(
                f"[备份] 迁移前已备份 secrets.json -> {migration['backup_path']}",
                file=sys.stderr,
            )
        for conflict in migration.get("conflicts", []):
            print(f"[冲突] {conflict}", file=sys.stderr)

    if args.as_json:
        payload = {
            "db_path": state.db_path,
            "secrets_path": state.secrets_path,
            "secrets_exists": state.secrets_exists,
            "secrets_key_count": len(state.secrets_keys),
            "dangling_keys": verdict["dangling_keys"],
            "plaintext_profiles": verdict["plaintext_profiles"],
            "plaintext_connections": verdict["plaintext_connections"],
            "ok": verdict["ok"],
            "issues": verdict["issues"],
            "notes": state.notes,
            "blocked": verdict.get("blocked", False),
            "blocked_reasons": verdict.get("blocked_reasons", []),
        }
        if migration is not None:
            payload["migration"] = {
                "applied": migration["applied"],
                # ⚠️ **``action_count`` = 实际落地的条数**（F-6）：dry-run 实际写入 **0** 条
                # ⇒ 旧实现报的是**候选**数 ⇒ 机器消费方会把 dry-run 读成「已写入 N 条」。
                # 「打算做 N 条」改用 ``candidate_count`` 表达 ⇒ 两个口径**同时可读**。
                "action_count": len(migration["actions"]) if migration["applied"] else 0,
                "candidate_count": len(migration["actions"]),
                # ⚠️ **与文本模式同口径**（F-6）：冲突与备份位置**不得**只出现在文本里
                # （机器消费方拿不到冲突信息、也不知道备份在哪）。
                "conflicts": migration.get("conflicts", []),
                "backup_path": migration.get("backup_path"),
                "note": migration["note"],
            }
        if prune is not None:
            payload["prune"] = {
                "applied": prune["applied"],
                "action_count": (
                    len(prune.get("removed", [])) if prune["applied"] else 0
                ),
                "candidate_count": len(prune["actions"]),
                "removed": prune.get("removed", []),
                "kept": prune.get("kept", []),
                "conflicts": prune.get("conflicts", []),
                "backup_path": prune.get("backup_path"),
                "note": prune["note"],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_secrets_text(state, verdict))
        if migration is not None:
            print(f"迁移: {'已执行' if migration['applied'] else '未执行'}（{migration['note']}）")
        if prune is not None:
            print(f"清理悬空条目: {'已执行' if prune['applied'] else '未执行'}（{prune['note']}）")

    return int(verdict["exit_code"])


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "secrets":
        return _run_secrets_mode(args)
    return _run_push_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
