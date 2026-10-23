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


# ---------------------------------------------------------------------------
# Windows 路径规范化（**守卫不被绕过的前提**）
# ---------------------------------------------------------------------------
#
# ⚠️ 为什么必须规范化（2026-10-23 对抗审查实证 + Lead 独立复算）：
# 「仓内外」判定若直接用 ``Path.resolve()`` + ``relative_to`` 做**字符串级**比较，
# 就会被**同一路径的另一种写法**绕过：
#
#   ``\\?\C:\repo\docs\x.db``
#     - Windows **认**这个写法（文件真的落在仓内 ``docs\x.db``）；
#     - 但 ``Path.resolve()`` **不剥扩展长度前缀** ⇒ 前缀与仓库根不同
#       ⇒ ``relative_to`` 抛 ``ValueError`` ⇒ 被判成「仓外」⇒ **守卫放行**。
#
# ⇒ 判定前必须先把路径**归一到同一个口径**：去扩展长度前缀 → ``normpath``（折叠
# ``..``、统一分隔符）→ 裁每个分量的**尾随空格/点**（Win32 打开文件时会裁）
# → **大小写不敏感**（Windows 本就如此）。
#
# ⚠️ 失败方向必须是「更严」：裁不出内容时按「当前目录」处理（宁可误拒，不可漏放）。


def _strip_extended_prefix(raw: str) -> str:
    """去掉 Windows **扩展长度前缀**（``\\\\?\\`` / ``\\\\?\\UNC\\`` / ``\\\\.\\``）。

    ⚠️ ``\\\\?\\UNC\\server\\share\\x`` ⇒ ``\\\\server\\share\\x``（UNC 的两个前导斜杠要保留）。
    """
    if raw.startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw[len("\\\\?\\UNC\\"):]
    if raw.startswith("\\\\?\\"):
        return raw[len("\\\\?\\"):]
    if raw.startswith("\\\\.\\"):
        return raw[len("\\\\.\\"):]
    return raw


def _trim_win_trailing(raw: str) -> str:
    """裁掉每个路径分量的**尾随空格与点**（Win32 语义：打开文件时会被裁掉）。

    裁空的分量按「当前目录」处理（保守：宁可误拒，不可漏放）。

    ⚠️ **UNC 根必须原样保留**（``\\\\server\\share\\...`` 的前两段）：
    旧实现用 ``partition("\\\\")`` 切分 ⇒ 把 ``\\\\server\\share`` 压成 ``\\.\\server\\share``
    （**换了一条路径**）⇒ 仓内 UNC 路径会被判成「仓外」⇒ **守卫被绕过**
    （2026-10-23 对抗审查 F-2 实测）。
    """
    if raw.startswith("\\\\"):
        rest = raw[2:]
        server, sep1, remainder = rest.partition("\\")
        if not sep1:
            return raw  # 只有 \\server（不完整 UNC）⇒ 原样
        share, sep2, tail = remainder.partition("\\")
        if not sep2:
            return raw  # 只有 \\server\share ⇒ 原样
        return "\\\\" + server + "\\" + share + "\\" + "\\".join(_trim_components(tail))
    head, sep, rest = raw.partition("\\")
    if not sep:
        return raw.rstrip(" .") or raw
    return head + "\\" + "\\".join(_trim_components(rest))


def _trim_components(rest: str) -> list[str]:
    """裁每个分量的尾随空格/点；裁空 ⇒ 按「当前目录」处理（保守）。"""
    comps: list[str] = []
    for part in rest.split("\\"):
        trimmed = part.rstrip(" .")
        comps.append(trimmed if trimmed else ".")
    return comps


def normalize_for_compare(path: Path | str) -> str:
    """把路径归一为**可用于仓内外比较**的字符串（Windows 口径，小写）。

    判据来源：Windows 打开文件时的真实路径解析规则（扩展长度前缀会被接受、
    分量尾随空格/点会被裁掉、路径比较**不区分大小写**）。
    """
    raw = _strip_extended_prefix(str(path))
    raw = os.path.normpath(raw)
    raw = _trim_win_trailing(raw)
    return raw.lower()


def _relative_normalized(target: Path | str, root: Path | str) -> str | None:
    """规范化后的相对路径（正斜杠）；不在 ``root`` 之下 ⇒ ``None``。

    ``root`` 本身 ⇒ ``"."``。
    """
    n_root = normalize_for_compare(root)
    n_target = normalize_for_compare(target)
    if n_target == n_root:
        return "."
    prefix = n_root if n_root.endswith("\\") else n_root + "\\"
    if n_target.startswith(prefix):
        return n_target[len(prefix):].replace("\\", "/")
    return None


def _same_location(a: Path, b: Path) -> bool:
    """两个路径是否指向**同一个底层位置**（按 ``(st_dev, st_ino)`` 比）。

    ⚠️ 为什么要它：``\\\\localhost\\D$\\<repo>\\docs\\x.db`` 与 ``D:\\<repo>\\docs\\x.db``
    是**同一个目录的两种拼写** ⇒ 即便路径已规范化，字符串比较也判不出「在仓内」
    ⇒ 守卫放行 ⇒ **绕过**（2026-10-23 对抗审查 F-1/F-2 同族）。
    实测本机 ``os.stat`` 对两种拼写给出**同一个** ``(st_dev, st_ino)``。
    """
    try:
        sa = os.stat(a)
        sb = os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _relative_by_identity(target: Path, root: Path) -> str | None:
    """目标（或其某个**存在**的祖先）与仓根**指向同一位置**时的相对路径。

    走「向上找第一个存在的祖先」⇒ 目标本身尚未创建（写盘前的常态）也能判定。
    """
    cur = target
    suffix: list[str] = []
    for _ in range(64):  # 深度上限：防御异常深的路径
        if _same_location(cur, root):
            return "/".join(reversed(suffix)) or "."
        parent = cur.parent
        if parent == cur:
            return None
        suffix.append(cur.name)
        cur = parent
    return None


def is_inside_repo(path: Path | str, repo_root: Path | str | None = None) -> bool:
    """待写路径是否落在软件仓内（含仓库根本身）。

    ⚠️ 走**规范化后**的比较（见本节开头）⇒ ``\\\\?\\`` 前缀等写法**不得**成为绕过；
    规范化判「仓外」时**再按底层身份**核一次（``(st_dev, st_ino)``）⇒ 同一目录的
    **别名拼写**（如 ``\\\\localhost\\D$\\<repo>``）也不得成为绕过。

    ⚠️ **单一事实源**（2026-10-23 对抗审查第二轮 F-7）：仓内外判定只实现**一次**
    —— 本函数**委托** :func:`relative_in_repo`，不再自己抄一遍判定逻辑。
    两条路径各判各的 ⇒ 某条判「仓外」、另一条判「仓内」时，**逃生口告警**
    （``allow_repo_out`` / ``allow_software_repo_key``）会**静默丢失**：
    写盘守卫会放过一个实际能逃逸的路径（或反过来误报）。
    ⇒ 判定逻辑的**唯一**实现点是 :func:`relative_in_repo`
    （返回 ``None`` = 仓外 / ``Path(".")`` = 仓根本身 / 其它 = 仓内子路径）。

    ⚠️ 委托后**对外行为逐位不变**（已逐形态实测比对）：仓内 True / 仓外 False /
    **仓根本身 True** —— ``relative_in_repo(仓根)`` 返回 ``Path(".")``（**不是** ``None``）
    ⇒ ``is not None`` 成立 ⇒ 仓根本身仍判「仓内」。
    """
    return relative_in_repo(path, repo_root) is not None


def relative_in_repo(path: Path | str, repo_root: Path | str | None = None) -> Path | None:
    """相对仓库根的相对路径；不在仓内 ⇒ ``None``（仓库根本身 ⇒ ``Path(".")``）。

    ⚠️ 判定走**规范化后**的比较（见本节开头）；**显示**仍用原始 ``relative_to``
    （保留用户写的大小写），失败时回落规范化结果。
    """
    root = _abs(repo_root if repo_root is not None else REPO_ROOT)
    target = _abs(path)
    rel = _relative_normalized(target, root)
    if rel is None:
        # 规范化判「仓外」⇒ 再按**底层身份**核一次（同一目录的别名拼写，如 UNC 管理共享）
        rel = _relative_by_identity(target, root)
        if rel is None:
            return None
        return Path(rel)
    try:
        return target.relative_to(root)
    except ValueError:
        return Path(rel)

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
