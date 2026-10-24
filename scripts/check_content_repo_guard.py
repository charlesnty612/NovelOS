#!/usr/bin/env python
"""内容仓 push 防护巡检 + 凭据收敛巡检（**CLI 门面**）。

⚠️ **本文件是门面**：``secrets`` 模式（凭据收敛 / D-18）的全部逻辑在同目录的
私有模块 ``scripts/_guard_secrets.py`` ⇒ 本文件只保留
① **push 模式**（内容仓 push 防护，**只读**）与 ② **CLI 装配**（参数解析 /
模式分派 / 输出）。
拆分原因：AGENTS.md 硬规则 1「新增模块不得超 1500 行」——两份逻辑合在一起会超。

⚠️ ``scripts/`` **不是包** ⇒ 门面靠「把本目录塞进 ``sys.path`` 再 import」加载
同目录模块（否则 ``from _guard_secrets import ...`` 会 ImportError）。
**两种入口都必须成立**：
- ``python scripts/check_content_repo_guard.py ...``（仓库根跑）；
- ``pytest``（仓库根在 ``pythonpath`` 上 ⇒ ``scripts.check_content_repo_guard``）。

用法
----
::

    # 模式一（默认）：人读形态 —— 内容仓 push 防护
    python scripts/check_content_repo_guard.py
    python scripts/check_content_repo_guard.py --content-dir D:/zcodeproject/NovelOS-Content
    python scripts/check_content_repo_guard.py --json

    # 模式二：凭据收敛（D-18）—— 悬空 secrets 条目 + 库内明文 api_key
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db
    python scripts/check_content_repo_guard.py secrets --json
    # **两个动作各自独立**（默认全是 dry-run；--apply 是写权限总闸）：
    #   迁移（库内明文 → secrets.json，并清空库内明文）
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --migrate --apply
    #   清理悬空条目（只动 secrets.json，不动库内明文）
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --prune-dangling --apply

⚠️ **只读纪律**：本脚本的**巡检**只调用 ``git rev-parse`` / ``git remote -v`` /
``git rev-list`` 这三条**只读**子命令，并以 SQLite **只读 URI**（``mode=ro``）
打开运行库；**绝不**调用 ``fetch`` / ``push`` / ``commit`` / ``add`` / ``reset`` 等
任何会改远端或本地引用的命令，也**绝不**在内容仓里写文件或改 git 状态。

唯一的**写**动作是 ``secrets`` 模式的 ``--apply``（凭据迁移 / 清理悬空条目）——
属**改用户凭据数据**，必须由用户明确授权后显式执行；默认是 dry-run。

⚠️ **凭据模式的判定口径 / 三道保险 / 归属闸门 / 可用键口径**见
``scripts/_guard_secrets.py`` 的模块 docstring。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ⚠️ 同目录模块加载（``scripts/`` **不是包**）——见模块 docstring。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _guard_secrets import (  # noqa: E402 —— 必须在本目录进 sys.path **之后**
    DEFAULT_DB_PATH,
    KeySlot,
    SecretsState,
    _action_lines,
    _conclusion_line,
    _ownership_marker,
    _resolve_secrets_path,
    collect_secrets_state,
    decide_secrets,
    migrate_secrets,
    prune_dangling_secrets,
    render_secrets_text,
)

__all__ = [
    "DEFAULT_CONTENT_DIR",
    "DEFAULT_DB_PATH",
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_UNSAFE",
    "GuardState",
    "KeySlot",
    "RC_GIT_UNAVAILABLE",
    "RemoteInfo",
    "SecretsState",
    "collect_secrets_state",
    "decide_secrets",
    "main",
    "migrate_secrets",
    "prune_dangling_secrets",
    "render_secrets_text",
]

DEFAULT_CONTENT_DIR = Path("D:/zcodeproject/NovelOS-Content")

EXIT_OK = 0
"""合规：有技术屏障，不能被误推。"""

EXIT_UNSAFE = 1
"""不合规：无技术屏障，能被误推。"""

EXIT_ERROR = 2
"""巡检自身出错（路径不可用 / 不是 git 工作树 / git 不可用 / 不可判）。"""

RC_GIT_UNAVAILABLE = 127
"""``_run_git`` 的**伪**退出码：git 根本没跑起来（cwd 不可用 / git 缺失）。

真实 git 的失败码是 128（``not a git repository``）；用 127 把「跑起来了但失败」
与「压根没跑」分开 ⇒ 消息才能区分「不是 git 工作树」与「路径不可用」。
"""

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

    ⚠️ **cwd 不可用必须先自查**（C1）：Windows 上 ``subprocess`` 拿一个不存在 /
    不是目录的路径当 ``cwd``，``CreateProcess`` 直接抛
    ``NotADirectoryError: [WinError 267]``（POSIX 上是 ``FileNotFoundError``）
    ⇒ 脚本以**裸 traceback** 收场、退出码 1 ⇒ 巡检「爆炸」而不是给出可判别的错误。
    ⇒ 先判「存在且是目录」再启动子进程，把「路径不可用」变成**正常返回值**
    （:data:`RC_GIT_UNAVAILABLE` + 含被检查路径的 stderr）。
    """
    try:
        usable = cwd.is_dir()
    except OSError as exc:  # 权限不足 / 坏路径：连 stat 都炸
        return (
            RC_GIT_UNAVAILABLE,
            "",
            f"内容仓路径不可用（{type(exc).__name__}: {exc}）：{cwd}",
        )
    if not usable:
        return RC_GIT_UNAVAILABLE, "", f"内容仓路径不可用（不存在或不是目录）：{cwd}"

    try:
        proc = subprocess.run(  # noqa: S603 —— 固定 git + 只读子命令
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:  # git 可执行文件缺失 / 无执行权限
        return (
            RC_GIT_UNAVAILABLE,
            "",
            f"无法执行 git（{type(exc).__name__}: {exc}）：{cwd}",
        )
    return proc.returncode, proc.stdout, proc.stderr


@dataclass
class GuardState:
    """巡检采集到的事实（**不含结论**；结论由 :func:`decide` 推导）。"""

    content_dir: str
    path_available: bool = True
    git_available: bool = True
    is_git_repo: bool = False
    remotes: list[RemoteInfo] = field(default_factory=list)
    remote_list_error: str | None = None
    fetch_url: str | None = None
    push_url: str | None = None
    pre_push_hook_path: str | None = None
    pre_push_hook_present: bool = False
    upstream: str | None = None
    ahead_count: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RemoteInfo:
    """一个远端的 fetch / push 端点（**只读采集的事实**）。"""

    name: str
    fetch_url: str | None = None
    push_url: str | None = None


def _parse_remotes_v(text: str) -> list[RemoteInfo]:
    """解析 ``git remote -v`` → **全部**远端的 ``[RemoteInfo]``。

    形态：``<name>\\t<url> (fetch)`` / ``<name>\\t<url> (push)``。

    ⚠️ **必须扫全部远端**（MAJOR · OS审查 2026-10-25）：旧实现只认 ``origin``
    （``name != "origin" → continue``）⇒ 远端名不叫 origin 时 ``push_url`` 读成
    ``None`` ⇒ 被 :func:`_is_disabled_push_url` 判成「push URL 已禁用」⇒
    判「不能被误推」exit 0 —— **假绿**（把「读不到」折叠成「没有」，给从未评估的
    对象发满分）。⇒ 这里返回**列表**（``origin`` 不作唯一识别，只作展示优先级）。
    """
    order: list[str] = []
    by_name: dict[str, RemoteInfo] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2]
        if kind not in ("(fetch)", "(push)"):
            continue
        info = by_name.get(name)
        if info is None:
            info = RemoteInfo(name=name)
            by_name[name] = info
            order.append(name)
        if kind == "(fetch)":
            info.fetch_url = url
        else:
            info.push_url = url
    return [by_name[n] for n in order]


def _pick_display_urls(remotes: list[RemoteInfo]) -> tuple[str | None, str | None]:
    """展示用 ``(fetch_url, push_url)``：``origin`` 优先，缺项回退到任一有 URL 的远端。

    ⚠️ 展示优先级**不是**判据：判据是「扫全部远端」（见 :func:`decide`）。
    """
    if not remotes:
        return None, None
    origin = next((r for r in remotes if r.name == "origin"), None)
    order = ([origin] if origin is not None else []) + [r for r in remotes if r is not origin]
    fetch = next((r.fetch_url for r in order if r.fetch_url), None)
    push = next((r.push_url for r in order if r.push_url), None)
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

    # ⚠️ C1：路径可用性**单独判**（不靠 _run_git 吞掉）——「路径不存在 / 不是目录」
    # 与「目录存在但不是 git 工作树」是两种**不同**的不可判原因，消息必须能区分。
    try:
        path_exists = content_dir.exists()
        path_is_dir = content_dir.is_dir() if path_exists else False
    except OSError:  # 权限不足等：当作不可用，绝不外抛成 traceback
        path_exists = path_is_dir = False
    if not path_is_dir:
        state.path_available = False
        if path_exists:
            state.notes.append(f"内容仓路径不是目录：{content_dir}")
        else:
            state.notes.append(f"内容仓路径不存在：{content_dir}")
        return state

    rc, out, err = _run_git(["rev-parse", "--is-inside-work-tree"], content_dir)
    if rc == RC_GIT_UNAVAILABLE:
        state.git_available = False
        state.notes.append(f"git 不可用（无法巡检）：{content_dir}")
        if err.strip():
            state.notes.append(f"git 诊断：{err.strip()}")
        return state
    if rc != 0 or out.strip() != "true":
        state.notes.append(f"不是 git 工作树：{content_dir}")
        return state
    state.is_git_repo = True

    rc_remote, remote_out, remote_err = _run_git(["remote", "-v"], content_dir)
    state.remotes = _parse_remotes_v(remote_out)
    if rc_remote != 0:
        state.remote_list_error = (
            f"无法列出远端（git remote -v 失败：{remote_err.strip() or '无输出'}）：{content_dir}"
        )
    state.fetch_url, state.push_url = _pick_display_urls(state.remotes)

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
        # ⚠️ C1：三种不可判原因**必须给出不同**的消息，且都含**被检查的路径**
        # （否则 CI/机器消费方分不清「没这个目录」与「目录在但不是 git 仓」）。
        if not state.path_available:
            reason = f"内容仓路径不存在或不是目录（无法巡检）：{state.content_dir}"
        elif not state.git_available:
            reason = f"git 不可用（无法巡检）：{state.content_dir}"
        else:
            reason = f"目标目录不是 git 工作树（无法巡检）：{state.content_dir}"
        return {
            "can_be_pushed": None,
            "verdict": "unknown",
            "exit_code": EXIT_ERROR,
            # ⚠️ C1：verdict 的**形状必须处处一致**（下面两个键不能只在「可判」分支
            # 才有）——JSON 输出无条件读 ``barriers`` / ``warnings`` ⇒ 不可判时缺键
            # ⇒ ``KeyError`` traceback（不可判分支自己又炸一次）。
            "barriers": [],
            "warnings": [],
            "reason": reason,
        }

    # ⚠️ MAJOR（OS审查 2026-10-25 · Chief 裁定直接修）：**没有远端 ⇒ 不可判定**，
    # 不许算绿。旧实现只认 ``origin`` ⇒ 远端名不叫 origin 时 ``push_url=None``
    # ⇒ 被当成「push URL 已禁用」⇒ 判「不能被误推」exit 0 —— **假绿**
    # （把「读不到」折叠成「没有」，给从未评估的对象发满分）。
    if not state.remotes:
        reason = state.remote_list_error or (
            f"内容仓没有任何远端（不可判定 push 是否被禁用）：{state.content_dir}"
        )
        return {
            "can_be_pushed": None,
            "verdict": "unknown",
            "exit_code": EXIT_ERROR,
            "barriers": [],
            "warnings": [],
            "reason": reason,
        }

    # ⚠️ 扫**全部**远端：任一远端的 push URL 可推 ⇒ 不安全
    # （``origin`` 只作展示优先级，**不作**唯一识别）。
    pushable = [r for r in state.remotes if not _is_disabled_push_url(r.push_url)]
    push_disabled = not pushable

    barriers: list[str] = []
    if push_disabled:
        barriers.append("push URL 已禁用")
    if state.pre_push_hook_present:
        barriers.append("存在 pre-push 钩子")

    can_be_pushed = bool(pushable) and not state.pre_push_hook_present

    reason_bits: list[str] = []
    if push_disabled:
        reason_bits.append(f"全部 {len(state.remotes)} 个远端的 push URL 已禁用")
    else:
        detail = "、".join(f"{r.name}={r.push_url}" for r in pushable)
        reason_bits.append(f"push 仍可推到远端：{detail}")
    if state.pre_push_hook_present:
        reason_bits.append("有 pre-push 钩子拦截")
    else:
        reason_bits.append("无 pre-push 钩子拦截")
    if state.ahead_count:
        reason_bits.append(f"本地领先上游 {state.ahead_count} 个提交")

    warnings: list[str] = []
    if pushable and state.pre_push_hook_present:
        warnings.append(
            "当前唯一的屏障是 pre-push 钩子——钩子被删除 / 绕过即失守；"
            "建议同时把 push URL 置为禁用值（no_push）"
        )
    for r in pushable:
        if r.push_url and "github.com" in r.push_url.lower():
            warnings.append(
                f"远端 {r.name} 的 push 目标含 github.com——一次误 push 即发布全书（不可撤回）"
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
    # ⚠️ **没有任何远端时禁止出现「推不出去」**：那句话是在给「安全」下结论，
    # 而「没有远端」是**不可判定**（不是「安全」）——文案不许把不可判说成安全。
    if state.remotes:
        push_display = state.push_url or "（无 ⇒ 推不出去）"
        remote_display = " | ".join(
            f"{r.name}={r.push_url or r.fetch_url or '（无 URL）'}" for r in state.remotes
        )
    else:
        push_display = "（无远端 ⇒ 不可判定）"
        remote_display = "（无远端 ⇒ 不可判定）"
    lines = [
        "===== 内容仓 push 防护巡检（只读）=====",
        f"  内容仓: {state.content_dir}",
        f"  远端: {remote_display}",
        f"  fetch URL: {state.fetch_url or '（无）'}",
        f"  push  URL: {push_display}",
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




def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_content_repo_guard",
        description=(
            "只读巡检：内容仓的 push 防护状态（能否被误推）+ 凭据收敛"
            "（悬空 secrets 条目 / 库内明文 api_key）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 合规 / 1 = 不合规 / 2 = 巡检出错"
            "（路径不存在或不是目录 / 不是 git 工作树 / git 不可用 / 不可判）\n"
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
            "**归属确认**：显式声明「--db 指向的就是**本项目**的运行库」⇒ 归属闸门"
            "一律放行（既放行「secrets 的可用键与库内**实体身份**零交集」，也放行"
            "「库里没有任何档案 / 连接」）。默认**拒绝**（退出码 2，零落盘）——因为"
            "「全部键都死」与「--db 指到了别的项目的库」在数据上**不可区分**，"
            "而后者会**一次清空整个凭据库**却仍报「合规」。"
            "⚠️ 用过它 ⇒ 归属**未经自动验证**（输出里会明确提示）。"
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
    try:
        state = collect_state(content_dir)
    except OSError as exc:
        # ⚠️ CLI 边界的最后一道保险（C1）：巡检**不得**以 Python traceback 收场。
        print(
            f"ERROR: 巡检内容仓失败（{type(exc).__name__}: {exc}）：{content_dir}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    verdict = decide(state)
    if verdict["verdict"] == "unknown":
        # 不可判 ⇒ 错误消息走 stderr（人读文本与 JSON 的 stdout 都不污染），含路径。
        print(f"ERROR: {verdict['reason']}", file=sys.stderr)

    if args.as_json:
        payload = {
            "content_dir": state.content_dir,
            # ⚠️ C1：机器消费方据此区分「路径不可用」/「git 不可用」/「不是 git 工作树」
            # （``reason`` 里已含被检查路径）。
            "path_available": state.path_available,
            "git_available": state.git_available,
            "is_git_repo": state.is_git_repo,
            "fetch_url": state.fetch_url,
            "push_url": state.push_url,
            # ⚠️ MAJOR：``push_disabled`` 的口径从「origin 的 push URL」改成
            # **全部远端**（任一远端可推 ⇒ False）；**没有远端**时是 ``None``
            # （不可判定），**不许**报 ``true``（＝安全）——旧口径在这里给从未
            # 评估过的对象发过满分。
            "push_disabled": (
                all(_is_disabled_push_url(r.push_url) for r in state.remotes)
                if state.remotes
                else None
            ),
            "remotes": [
                {"name": r.name, "fetch_url": r.fetch_url, "push_url": r.push_url}
                for r in state.remotes
            ],
            "remote_count": len(state.remotes),
            "pushable_remotes": [
                r.name for r in state.remotes if not _is_disabled_push_url(r.push_url)
            ],
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


def _secrets_verdict(state) -> dict:
    """凭据模式的判定 + **CLI 退出码**（0 合规 / 1 不合规 / 2 不可判）。"""
    verdict = decide_secrets(state)
    if verdict.get("blocked"):
        verdict["exit_code"] = EXIT_ERROR
    else:
        verdict["exit_code"] = EXIT_OK if verdict["ok"] else EXIT_UNSAFE
    return verdict


def _freeze_ownership(state) -> dict:
    """**动作前**冻结归属口径（R1）。

    ⚠️ 动作执行后会**重新采集** ⇒ 重新采集的结果**不能**代表「动作发生时」的
    口径：force 清掉最后一个键 ⇒ 重新采集时归属分支**不进** ⇒ 提示凭空消失；
    force 迁移把错库的实体 id 写进文件 ⇒ 重新采集时反而「归属: 已验证」
    ⇒ **误导**。⇒ 动作之前先冻结，动作之后仍用**动作前**的口径渲染。
    """
    return {
        "verified": state.ownership_verified,
        "reason": state.ownership_reason,
        "warning": state.ownership_warning,
    }


def _restore_ownership(state, frozen: dict, force_used: bool) -> None:
    """把**动作前**的归属口径写回 state（R1）⇒ 动作后的重新采集不能改写它。

    ⚠️ ``force_used`` ⇒ **必须**有醒目提示（R1.2）：本次的写盘/删除是在归属
    未经自动验证的情况下做的 ⇒ 哪怕动作后归属「看起来」已验证也不许沉默。
    """
    state.ownership_verified = frozen["verified"]
    state.ownership_reason = frozen["reason"]
    state.ownership_warning = frozen["warning"]
    if not force_used:
        return
    if frozen["verified"]:
        state.ownership_warning = (
            "⚠️ --force-all-dangling 已放行归属闸门（本次的写盘/删除未经过归属闸门把关）；"
            "请确认 --db 指向的就是**本项目**的运行库"
        )
    else:
        state.ownership_warning = (
            "⚠️ 归属未经自动验证（--force-all-dangling 已放行）——"
            "本次的写盘/删除是在归属未经自动验证的情况下做的；"
            "请确认 --db 指向的就是**本项目**的运行库"
        )


def _run_secrets_mode(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    secrets_path = _resolve_secrets_path(args.secrets_file)
    state = collect_secrets_state(
        db_path, secrets_path, force_all_dangling=args.force_all_dangling
    )
    verdict = _secrets_verdict(state)
    # ⚠️ **动作前冻结归属口径**（R1）——动作后的重新采集**不得**改写它。
    ownership_frozen = _freeze_ownership(state)

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
            verdict = _secrets_verdict(state)
        if prune.get("backup_path"):
            # ⚠️ 备份路径必须**看得见**（用户要知道凭据的旧版本在哪）。
            print(
                f"[备份] 清理前已备份 secrets.json -> {prune['backup_path']}",
                file=sys.stderr,
            )

    migration: dict | None = None
    if want_migrate:
        try:
            migration = migrate_secrets(
                state, apply=args.apply, force_all_dangling=args.force_all_dangling
            )
        except Exception as exc:  # noqa: BLE001 —— CLI 边界：异常 → 诊断 + 退出码 2
            print(f"ERROR: 凭据迁移失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        if migration.get("applied"):
            state = collect_secrets_state(
                db_path, secrets_path, force_all_dangling=args.force_all_dangling
            )
            verdict = _secrets_verdict(state)
        if migration.get("backup_path"):
            # ⚠️ 备份路径必须**看得见**（用户要知道凭据的旧版本在哪；对抗审查 F-3）。
            print(
                f"[备份] 迁移前已备份 secrets.json -> {migration['backup_path']}",
                file=sys.stderr,
            )
        for conflict in migration.get("conflicts", []):
            print(f"[冲突] {conflict}", file=sys.stderr)

    # ⚠️ **归属口径用「动作前」的**（R1）+ force 的醒目提示（R1.2）。
    # ⇒ 动作后的重新采集**不得**改写它（force 清空最后一个键 ⇒ 提示会凭空消失；
    # force 迁移把错库实体写进文件 ⇒ 反而显示「归属: 已验证」⇒ 误导）。
    _restore_ownership(state, ownership_frozen, bool(args.force_all_dangling))

    if args.as_json:
        payload = {
            "db_path": state.db_path,
            "secrets_path": state.secrets_path,
            "secrets_exists": state.secrets_exists,
            # ⚠️ ``secrets_key_count`` = **可用键**数（与生产解析口径一致）；
            # ``secrets_entry_count`` = 文件 api_keys 段的**条目总数**（含不可用的）。
            "secrets_key_count": len(state.secrets_keys),
            "secrets_entry_count": state.secrets_entry_count,
            "unusable_keys": state.unusable_keys,
            # 归属闸门的机器可读口径（``ownership_verified: false`` ⇒ 归属未经自动验证）。
            "ownership_verified": state.ownership_verified,
            "ownership_reason": state.ownership_reason,
            "ownership_warning": state.ownership_warning,
            "force_all_dangling": bool(args.force_all_dangling),
            "dangling_keys": verdict["dangling_keys"],
            "plaintext_profiles": verdict["plaintext_profiles"],
            "plaintext_connections": verdict["plaintext_connections"],
            "ok": verdict["ok"],
            "issues": verdict["issues"],
            "notes": state.notes,
            "blocked": verdict.get("blocked", False),
            "blocked_reasons": verdict.get("blocked_reasons", []),
            # ⚠️ **本次动作的结果必须与结论同段可见**（修法 E）⇒ 结论行 + 动作行。
            # ⚠️ 动作结果**不进** ``issues``（那里只装「问题」⇒ 进了会污染 ``ok``）。
            "conclusion": _conclusion_line(verdict),
            "action_results": _action_lines(
                migration, prune, marker=_ownership_marker(state)
            ),
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
        # ⚠️ **动作结果并进同一段文本**（修法 E）：旧实现在结论**之后**另起两行
        # ⇒ 读的人先看到「结论: 合规」⇒ 误以为什么都没发生。
        print(render_secrets_text(state, verdict, migration=migration, prune=prune))

    return int(verdict["exit_code"])


def _configure_stdio_encoding() -> None:
    """把 stdout / stderr 固定成 UTF-8（**只在脚本入口调用**）。

    ⚠️ **为什么必须**：Windows 上 stdout 被**重定向**（管道 / CI 日志 / 采集器）时，
    编码取 locale 编码（本机 cp936）⇒ 输出里的 ``⇒`` / ``✅`` 等非 GBK 字符会抛
    ``UnicodeEncodeError`` ⇒ 脚本以 **traceback** 收场（正是 C1 要消灭的形态，且
    真实控制台因 PEP 528 本来就是 UTF-8 ⇒ 平时看不出来，一进 CI 就炸）。
    ⇒ 入口统一 reconfig 成 ``utf-8`` + ``errors="replace"``：管道 / 日志里是正确的
    UTF-8；真实控制台行为不变（本来就是 utf-8）。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 被替换成不支持 reconfigure 的对象（罕见）
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # 流已关闭 / 不支持改编码
            pass


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "secrets":
        return _run_secrets_mode(args)
    return _run_push_mode(args)


if __name__ == "__main__":
    _configure_stdio_encoding()
    raise SystemExit(main())
