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
    # 迁移命令：**默认 dry-run**（不写文件、不改库）；--apply 才真的执行
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db
    python scripts/check_content_repo_guard.py secrets --db data/novelos.db --apply

检查项（全部只读）
------------------
1. ``origin`` 的 **fetch / push URL** 是什么（``git remote -v``）；
2. 是否存在**有效的** ``pre-push`` 钩子（``git rev-parse --git-path hooks`` → 文件存在
   且非空；``.sample`` 结尾不算）；
3. 本地**领先**上游多少提交（``git rev-list --count @{u}..HEAD``；无上游 ⇒ 0）；
4. （``secrets`` 模式）``secrets.json`` 的条目是否**悬空**（对不上任何档案 / 连接 /
   provider）、运行库里 ``model_profiles`` / ``model_connections`` 的 ``params_json``
   是否仍有**明文 api_key**。

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
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


def collect_secrets_state(db_path: Path, secrets_path: Path) -> SecretsState:
    """采集「悬空 secrets 条目」与「库内明文 api_key」（**只读**）。"""
    state = SecretsState(db_path=str(db_path), secrets_path=str(secrets_path))

    # --- secrets.json 的键（只读键名，**不读值**）---
    if secrets_path.exists():
        state.secrets_exists = True
        try:
            raw = json.loads(secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            state.notes.append(f"secrets.json 解析失败：{exc}")
            raw = {}
        api_keys = raw.get("api_keys") if isinstance(raw, dict) else None
        if isinstance(api_keys, dict):
            state.secrets_keys = sorted(str(k) for k in api_keys)
        else:
            state.notes.append("secrets.json 无 api_keys 段")
    else:
        state.notes.append("secrets.json 不存在")

    # --- 运行库（只读 URI）---
    if not db_path.exists():
        state.notes.append(f"运行库不存在：{db_path}")
        return state
    try:
        conn = _open_readonly(db_path)
    except sqlite3.Error as exc:
        state.notes.append(f"运行库打开失败：{exc}")
        return state
    try:
        try:
            profiles = conn.execute(
                "SELECT profile_id, provider, params_json FROM model_profiles"
            ).fetchall()
        except sqlite3.Error as exc:
            state.notes.append(f"读 model_profiles 失败：{exc}")
            profiles = []
        try:
            connections = conn.execute(
                "SELECT connection_id, provider, params_json FROM model_connections"
            ).fetchall()
        except sqlite3.Error as exc:
            state.notes.append(f"读 model_connections 失败：{exc}")
            connections = []

        for row in profiles:
            pid = str(row["profile_id"])
            state.profile_ids.append(pid)
            provider = str(row["provider"] or "")
            if provider:
                state.providers.append(provider)
            if _params_api_key(row["params_json"]) is not None:
                state.plaintext_profiles.append(pid)
        for row in connections:
            cid = str(row["connection_id"])
            state.connection_ids.append(cid)
            provider = str(row["provider"] or "")
            if provider:
                state.providers.append(provider)
            if _params_api_key(row["params_json"]) is not None:
                state.plaintext_connections.append(cid)
    finally:
        conn.close()

    # --- 悬空条目：secrets.json 的键对不上任何 profile / connection / provider ---
    known = {pid.lower() for pid in state.profile_ids}
    known |= {cid.lower() for cid in state.connection_ids}
    known |= {p.lower() for p in state.providers}
    state.dangling_keys = [k for k in state.secrets_keys if k.lower() not in known]
    return state


def decide_secrets(state: SecretsState) -> dict:
    """由采集到的事实推导凭据收敛结论（**纯函数**）。"""
    issues: list[str] = []
    if state.dangling_keys:
        issues.append(f"悬空 secrets 条目 {len(state.dangling_keys)} 条")
    n_plain = len(state.plaintext_profiles) + len(state.plaintext_connections)
    if n_plain:
        issues.append(f"库内明文 api_key {n_plain} 行")
    return {
        "ok": not issues,
        "exit_code": EXIT_OK if not issues else EXIT_UNSAFE,
        "issues": issues,
        "dangling_keys": state.dangling_keys,
        "plaintext_profiles": state.plaintext_profiles,
        "plaintext_connections": state.plaintext_connections,
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
    else:
        lines.append("结论: 不合规（" + "；".join(verdict["issues"]) + "）")
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
    """
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
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"secrets.json 解析失败，已中止迁移：{exc}") from exc
    api_keys = payload.get("api_keys")
    if not isinstance(api_keys, dict):
        api_keys = {}
    conn = _open_readonly(Path(state.db_path))
    try:
        rows = conn.execute(
            "SELECT profile_id, params_json FROM model_profiles"
        ).fetchall()
        for row in rows:
            key = _params_api_key(row["params_json"])
            if key is not None:
                api_keys[str(row["profile_id"])] = key
        rows = conn.execute(
            "SELECT connection_id, params_json FROM model_connections"
        ).fetchall()
        for row in rows:
            key = _params_api_key(row["params_json"])
            if key is not None:
                api_keys[str(row["connection_id"])] = key
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
    return {"applied": True, "actions": actions, "note": "已写入 secrets.json 并清空库内明文"}


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
        help="secrets 模式：真正执行迁移（**默认 dry-run**，不写文件、不改库）",
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
    state = collect_secrets_state(db_path, secrets_path)
    verdict = decide_secrets(state)

    migration: dict | None = None
    if args.apply:
        try:
            migration = migrate_secrets(state, apply=True)
        except Exception as exc:  # noqa: BLE001 —— CLI 边界：异常 → 诊断 + 退出码 2
            print(f"ERROR: 凭据迁移失败：{exc}", file=sys.stderr)
            return EXIT_ERROR

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
        }
        if migration is not None:
            payload["migration"] = {
                "applied": migration["applied"],
                "action_count": len(migration["actions"]),
                "note": migration["note"],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_secrets_text(state, verdict))
        if migration is not None:
            print(f"迁移: {'已执行' if migration['applied'] else '未执行'}（{migration['note']}）")

    return int(verdict["exit_code"])


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "secrets":
        return _run_secrets_mode(args)
    return _run_push_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
