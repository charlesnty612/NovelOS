#!/usr/bin/env python
"""脚本落盘守卫：**软件仓写路径判定** + **痕迹仓根目录解析**（D-19 / 对抗审查 B1·B2）。

背景
----
AGENTS.md 硬规则 5「软件 / 内容 / **痕迹**三仓分离」此前对**脚本**只有纪律、没有守卫：

- 运行报告含真实 ``prj_``/``ch_`` id 与书籍正文片段 ⇒ 必须落**痕迹仓**
  （``NovelOS-Artifacts``，无 remote），不能落软件仓（落进软件仓 = 进 GitHub 历史，
  **不可撤回**）；
- 但 ``scripts/eval_regression.py`` 与 ``scripts/m1_report.py`` 的痕迹仓根解析
  **最后一档回退就是软件仓自身** ⇒ 在**新克隆 / CI / 换机**（拿不到痕迹仓）上跑一次
  ``--write-docs`` 就把痕迹写回软件仓。``.gitignore`` 挡得住 ``git add``，**挡不住落盘**
  （且 ``git add -f`` 可绕过）。

本模块提供两件事（两个评估脚本共用，保证行为一致）：

1. :func:`check_write_path` —— **写路径守卫**（D-19）：
   给定一个待写路径 ⇒ 判定「允许 / 拒绝 + 原因」。
   软件仓内**只允许** ``data/`` 与 ``tmp/`` 两个子树（前者是运行库、后者是会话级
   暂存区，均已被 ``.gitignore`` 覆盖）；软件仓根下的其它路径（``docs/``、``foo.md`` …）
   **一律拒绝** ⇒ 脚本不得把产物写回软件仓。拒绝对抛 :class:`RepoWritePathError`，
   **不得**静默改写到别处。
2. :func:`resolve_artifacts_root` —— **痕迹仓根解析**（B1）：
   ``--artifacts-dir`` > ``NOVELOS_ARTIFACTS_DIR`` > 软件仓**同级目录**
   ``<repo父目录>/NovelOS-Artifacts``；**都找不到就返回 ``None``**——
   **绝不回落到软件仓自身**（那一档正是要防的落点）。调用方据此**明确失败**，
   而不是静默写错地方。

逃生口（默认关，由调用方显式开启）
--------------------------------
找不到痕迹仓时，调用方可让用户显式承担：

- ``--artifacts-dir <path>``：显式指定痕迹仓根（推荐）；
- 环境变量 ``NOVELOS_ARTIFACTS_DIR``：同上（供 CI / 计划任务）；
- ``--allow-software-repo-out``：**明确接受**把痕迹写回软件仓的风险
  （守卫随之放行软件仓内的非 ``data/``/``tmp/`` 路径）。

用法::

    from scripts.guard_repo_write_path import (
        RepoWritePathError,
        check_write_path,
        resolve_artifacts_root,
    )

    check_write_path(runs_dir)          # 允许 ⇒ 返回绝对路径；拒绝对抛异常
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 软件仓根（本模块所在 ``scripts/`` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 软件仓内**允许**写入的子树（其余一律拒绝）。
#: - ``data/``：运行库与运行产物（``.gitignore`` 已忽略）；
#: - ``tmp/``：会话级临时暂存区（``.gitignore`` 已忽略）。
ALLOWED_REPO_SUBDIRS: tuple[str, ...] = ("data", "tmp")

#: 痕迹仓目录名（软件仓的**同级**目录）。
ARTIFACTS_DIR_NAME = "NovelOS-Artifacts"

#: 痕迹仓根的环境变量（覆盖同级目录探测）。
ARTIFACTS_ENV_VAR = "NOVELOS_ARTIFACTS_DIR"

#: 痕迹仓相对布局：``<root>/docs/evaluation/{runs,baseline}``。
ARTIFACTS_EVALUATION_RELPATH = Path("docs") / "evaluation"
ARTIFACTS_RUNS_RELPATH = ARTIFACTS_EVALUATION_RELPATH / "runs"
ARTIFACTS_BASELINE_RELPATH = ARTIFACTS_EVALUATION_RELPATH / "baseline" / "last_passing_run.json"

EXIT_WRITE_PATH_REJECTED = 2
"""退出码：待写路径被守卫拒绝（软件仓内非允许子树）。"""

EXIT_ARTIFACTS_UNAVAILABLE = 3
"""退出码：痕迹仓不可用（找不到且未给逃生口）⇒ 明确失败，不写软件仓。"""


class RepoWritePathError(RuntimeError):
    """待写路径落在软件仓内且不在允许子树 ⇒ 拒绝落盘（D-19）。"""


class ArtifactsUnavailableError(RuntimeError):
    """痕迹仓不可用（找不到且未给逃生口）⇒ 调用方**必须**明确失败。

    刻意用异常而非返回 ``None``：调用方若漏接，异常会直接冒泡（脚本非 0 退出），
    不会静默走到「回落到软件仓」那条老路上。
    """


def format_artifacts_unavailable(res: "ArtifactsResolution", *, subject: str) -> str:
    """痕迹仓不可用时的**统一**说明（四个落盘脚本口径一致）。

    ``subject`` = 该产物「为什么必须落痕迹仓」的一句话（含正文/真实 id 的风险）。
    """
    return (
        "[拒绝写入] 痕迹仓不可用："
        f"{res.detail}。\n"
        f"             {subject}\n"
        "             逃生口：--artifacts-dir <path> | 设置 NOVELOS_ARTIFACTS_DIR | "
        "--allow-software-repo-out（明确承担落软件仓的风险）"
    )


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def _abs(path: Path | str) -> Path:
    """绝对化 + 解析符号链接（解析失败时退回 ``absolute()``）。"""
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:  # pragma: no cover —— 极端路径（权限/断链）才触发
        return p.absolute()


def is_inside_repo(path: Path | str, repo_root: Path | str | None = None) -> bool:
    """待写路径是否落在软件仓内（含仓库根本身）。"""
    root = _abs(repo_root if repo_root is not None else REPO_ROOT)
    target = _abs(path)
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def relative_in_repo(path: Path | str, repo_root: Path | str | None = None) -> Path | None:
    """相对仓库根的相对路径；不在仓内 ⇒ ``None``（仓库根本身 ⇒ ``Path(".")``）。"""
    root = _abs(repo_root if repo_root is not None else REPO_ROOT)
    target = _abs(path)
    try:
        return target.relative_to(root)
    except ValueError:
        return None


def check_write_path(
    path: Path | str,
    *,
    repo_root: Path | str | None = None,
    allow_repo_root: bool = False,
) -> Path:
    """判定待写路径；允许 ⇒ 返回绝对路径，拒绝对抛 :class:`RepoWritePathError`。

    规则（软件仓内）：

    - ``data/``、``tmp/`` 子树 ⇒ 允许（已被 ``.gitignore`` 覆盖）；
    - 其它路径（含仓库根本身）⇒ **拒绝**；
    - ``allow_repo_root=True`` ⇒ 放行软件仓内的任意路径（**逃生口，默认关**，
      调用方须由用户显式开启）。

    软件仓外的路径（系统临时目录、痕迹仓等）一律允许。
    """
    root = _abs(repo_root if repo_root is not None else REPO_ROOT)
    target = _abs(path)
    rel = relative_in_repo(target, root)
    if rel is None:
        return target
    if allow_repo_root:
        return target
    parts = rel.parts
    if parts and parts[0] in ALLOWED_REPO_SUBDIRS:
        return target
    shown = rel if parts else Path(".")
    raise RepoWritePathError(
        f"拒绝写入软件仓内的非允许路径：{target}（相对仓库根 {shown}）；"
        f"软件仓内只允许 {'、'.join(f'{d}/' for d in ALLOWED_REPO_SUBDIRS)} 子树"
        f"（D-19：脚本不得把产物写回软件仓）"
    )


# ---------------------------------------------------------------------------
# 痕迹仓根解析（B1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactsResolution:
    """痕迹仓根解析结果。

    - ``root``：解析到的痕迹仓根；**找不到时为 ``None``**（**不回落到软件仓**）；
    - ``source``：解析来源（``cli`` / ``env`` / ``sibling`` / ``none``）；
    - ``detail``：人类可读说明（找不到时说明「找过哪些地方、为什么没找到」）。
    """

    root: Path | None
    source: str
    detail: str

    @property
    def ok(self) -> bool:
        """痕迹仓是否可用。"""
        return self.root is not None


def resolve_artifacts_root(
    explicit: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    repo_root: Path | str | None = None,
) -> ArtifactsResolution:
    """解析痕迹仓根目录（AGENTS.md 硬规则 5：操作痕迹在 ``NovelOS-Artifacts``）。

    解析顺序：``explicit``（``--artifacts-dir``）→ 环境变量 ``NOVELOS_ARTIFACTS_DIR``
    → 软件仓**同级目录** ``<repo父目录>/NovelOS-Artifacts``（存在才用）。

    ⚠️ **找不到就返回 ``root=None``**——**绝不**回落到软件仓自身（那一档正是要防的
    落点：新克隆 / CI / 换机拿不到痕迹仓，写进去就进 GitHub 历史，不可撤回）。
    调用方必须据此**明确失败**，或让用户显式承担（``--allow-software-repo-out``）。
    """
    environ = os.environ if env is None else env
    root = _abs(repo_root if repo_root is not None else REPO_ROOT)
    if explicit:
        return ArtifactsResolution(_abs(explicit), "cli", f"--artifacts-dir={explicit}")
    env_val = environ.get(ARTIFACTS_ENV_VAR)
    if env_val:
        return ArtifactsResolution(_abs(env_val), "env", f"{ARTIFACTS_ENV_VAR}={env_val}")
    sibling = root.parent / ARTIFACTS_DIR_NAME
    if sibling.is_dir():
        return ArtifactsResolution(_abs(sibling), "sibling", f"同级目录 {sibling}")
    return ArtifactsResolution(
        None,
        "none",
        f"找不到痕迹仓：同级目录 {sibling} 不存在，且未设置 {ARTIFACTS_ENV_VAR}",
    )


__all__ = [
    "ALLOWED_REPO_SUBDIRS",
    "ARTIFACTS_BASELINE_RELPATH",
    "ARTIFACTS_DIR_NAME",
    "ARTIFACTS_ENV_VAR",
    "ARTIFACTS_EVALUATION_RELPATH",
    "ARTIFACTS_RUNS_RELPATH",
    "EXIT_ARTIFACTS_UNAVAILABLE",
    "EXIT_WRITE_PATH_REJECTED",
    "REPO_ROOT",
    "ArtifactsResolution",
    "ArtifactsUnavailableError",
    "RepoWritePathError",
    "check_write_path",
    "format_artifacts_unavailable",
    "is_inside_repo",
    "relative_in_repo",
    "resolve_artifacts_root",
]
