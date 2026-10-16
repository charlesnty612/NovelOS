"""启动时的版本更新检查（2026-10-16 批次十一 · Lead 自办）。

## 为什么需要

克隆安装的用户靠 ``git pull`` 更新，而本仓有两件事**不在版本库里**：

* ``data/``（含 ``novelos.db``）——这是**好事**，用户书稿不会被 pull 碰到；
* ``apps/web/dist/``——这是**陷阱**：后端从磁盘托管这个 SPA，
  所以「改了前端的版本」在 ``git pull`` 之后会变成「**新后端 + 旧界面**」，
  **且不会报任何错**，只表现为行为诡异（新字段不认 / 旧字段还在）。

本模块在启动时比对「上次记录的 commit」与「当前 HEAD」，并**只针对这一条**
给出可执行的提示，避免把「每次更新都要重编前端」的错误印象固化下来。

## 设计约束

1. **绝不阻断启动。** 任何异常（不是 git 仓库 / 没装 git / 超时 / 命令失败）
   一律降级为「没检查」，返回 ``None``。诊断代码不能成为故障源。
2. **只读 git**，不写工作区、不 fetch、不切分支。
3. **不碰内容仓与既有项目**：只在 ``data/`` 下写一个 ``.last_seen_commit`` 文本。
4. ⚠️ **子进程编码两侧都要钉**：``encoding=`` **与** env 的
   ``PYTHONIOENCODING``/``PYTHONUTF8`` 同时设。只给一边照样会在本机
   （locale = cp936）静默错位——本仓坑区已登记这条，父子两侧都出过事。
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("novelos.version_check")

#: 记录「上次启动时的 commit」的文件名（落在 data/ 下，天然随库走、不入库）
STATE_FILENAME = ".last_seen_commit"

#: 前端源码目录——它变了就必须重编 dist（前端改动的唯一真实判据）
FRONTEND_PATH = "apps/web/src"

#: 依赖清单——变了就要重新装依赖
DEP_PATHS = ("pyproject.toml", "uv.lock", "apps/web/package.json", "apps/web/package-lock.json")

#: git 子进程超时（秒）。设死是为了不让一次卡住的 git 把服务启动拖住。
_GIT_TIMEOUT = 5


@dataclass(frozen=True)
class UpdateNotice:
    """一次「代码已更新」的观察结果。

    ``frontend_changed`` / ``deps_changed`` 为 ``None`` 表示**判不出来**
    （典型：旧 commit 已被 rebase 掉、``git diff`` 失败）。调用方必须把
    ``None`` 当「未知」而不是「否」——把未知当否，正是本仓反复登记的
    「量具没量到目标却报告成量过了」。
    """

    previous: str
    current: str
    frontend_changed: bool | None
    deps_changed: bool | None

    def as_log_lines(self) -> list[str]:
        """给人看的提示行。措辞按「是否**需要动手**」组织，不做泛泛的版本播报。"""
        lines = [
            f"检测到代码已更新：{self.previous[:7]} → {self.current[:7]}",
        ]
        if self.frontend_changed:
            lines.append(
                "  ⚠️ 本次更新改动了前端源码，但你本地 apps/web/dist 是旧构建产物。"
                "不重建会得到「新后端 + 旧界面」且不报错。请执行："
                "cd apps/web && npm install && npm run build"
            )
        elif self.frontend_changed is False:
            lines.append("  前端源码未变，无需重建 dist。")
        else:
            lines.append("  未能判定前端是否变化（多半是旧 commit 已不在本地历史里）——请自行确认。")

        if self.deps_changed:
            lines.append("  ⚠️ 本次更新改动了依赖清单，请执行：uv sync --extra dev（不是裸 uv sync）")
        elif self.deps_changed is None:
            lines.append("  依赖清单变化未能判定，请自行确认。")
        return lines


def _git_env() -> dict[str, str]:
    """子进程 env：**两侧同时钉**编码（本仓坑区登记过父子错位事故）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run_git(repo_root: Path, *args: str) -> str | None:
    """跑一条只读 git 命令；**任何**失败都降级为 ``None``，绝不抛出。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            env=_git_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 没装 git / 超时 / 权限
        log.debug("git %s 执行失败：%s", " ".join(args), exc)
        return None
    if proc.returncode != 0:
        log.debug("git %s 返回 %s：%s", " ".join(args), proc.returncode, proc.stderr.strip())
        return None
    return proc.stdout.strip()


def current_head(repo_root: Path) -> str | None:
    """当前 HEAD 的 commit id；不是 git 仓库 / 没装 git ⇒ ``None``。"""
    head = _run_git(repo_root, "rev-parse", "HEAD")
    return head or None


def _paths_changed(repo_root: Path, old: str, new: str, path: str) -> bool | None:
    """``old..new`` 之间该路径有无改动；判不出来返回 ``None``（不是 ``False``）。"""
    out = _run_git(repo_root, "diff", "--name-only", f"{old}..{new}", "--", path)
    if out is None:
        return None
    return bool(out)


def read_state(data_dir: Path) -> str | None:
    """读上次记录的 commit；没有 / 读不了 ⇒ ``None``。"""
    try:
        raw = (data_dir / STATE_FILENAME).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return raw or None


def write_state(data_dir: Path, head: str) -> None:
    """记录当前 commit。**失败只记 debug**——写不进去不该阻断启动。"""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / STATE_FILENAME).write_text(head + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("写入 %s 失败：%s", STATE_FILENAME, exc)


def check_for_update(repo_root: Path, data_dir: Path) -> UpdateNotice | None:
    """启动时调用。

    返回值语义：

    * ``None`` —— 没变化 / 检查不了（**不是**「无更新」的保证，是「没结论」）；
    * ``UpdateNotice`` —— 代码确实变了，附带**能否判定**前端与依赖是否要动。

    ⚠️ 首次启动会**静默记录**当前 commit 而不提示——那时没有「更新」可言。
    """
    head = current_head(repo_root)
    if head is None:
        return None

    previous = read_state(data_dir)
    if previous is None:
        write_state(data_dir, head)
        return None

    if previous == head:
        return None

    # 旧 commit 可能已不在本地历史里（rebase / squash / 换分支）⇒ 判不出前后差异。
    # 这时**只报「变了」，不谎称前端没变**。前端与依赖**各自独立**判定：
    # 一条路径判不出来不等于另一条也判不出来，而「把未知当否」正是本仓
    # 反复登记的「量具没量到目标却报告成量过了」。
    frontend_changed = _paths_changed(repo_root, previous, head, FRONTEND_PATH)
    dep_results = [_paths_changed(repo_root, previous, head, p) for p in DEP_PATHS]
    if any(r is None for r in dep_results):
        deps_changed: bool | None = None
    else:
        deps_changed = any(dep_results)

    write_state(data_dir, head)
    return UpdateNotice(
        previous=previous,
        current=head,
        frontend_changed=frontend_changed,
        deps_changed=deps_changed,
    )
