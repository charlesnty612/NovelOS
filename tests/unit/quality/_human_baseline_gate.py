"""人类基线语料的**唯一判定点**（CI 可见性改批，2026-10-19）。

为什么要有这个模块
------------------
本仓有三个「人类基线看守」测试文件——``test_human_baseline_ai_thresholds.py`` /
``test_readability_baseline.py`` / ``test_dialogue_echo_baseline.py``——它们的
断言全部依赖**内容仓里的真实出版小说**（榜一《快穿之人渣洗白手册》侯府弧
19 章 / 37806 可见字）。内容仓按 2026-09-14 裁决**不推 GitHub**，故 CI（只 checkout
软件仓）里那份语料**根本不存在** ⇒ 三个文件在 CI 中**恒 skip** ⇒
**「人类基线看守在 CI 中从未生效过」，而 CI 报绿**。

这是坑区「**量具的缺陷被当成被测物的性质**」的新形态：**一个恒 skip 的守卫等于
不存在，但 CI 报绿**。本模块把「语料不可得」从一个**无声的 skip** 变成
**三件可判定的事**：

1. **一条显式记录**：每次判定都会往状态记录里**追加一行**（默认
   ``data/human_baseline_status.jsonl``；读侧 :func:`read_status_records` 合并）。
   ``data/`` 在 ``.gitignore`` 内，且树指纹
   口径只含 ``packages/tests/scripts/database`` 的 ``.py``/``.sql`` ⇒ 既不污染
   仓库状态，也不影响「跑测前后指纹逐位一致」这道闸门。记录内容 = 查过哪些路径、
   哪些看守没跑、当时是否处于严格模式。
2. **一条响亮的警告**：``pytest`` 的 warnings summary 里出现一行带
   ``HUMAN-BASELINE-CORPUS-UNAVAILABLE`` 标记的警告 ⇒ CI 日志里「没跑」是**显式的
   一行**，不是空白。skip 理由里也带同一个标记，故 ``-rA`` 的 short summary 同样
   可 grep。
3. **一个可判定的开关**：``NOVELOS_REQUIRE_HUMAN_BASELINE=1`` ⇒ 语料不可得时
   **直接判红**（``pytest.fail``）而不是跳过 ——「缺席是否可接受」由**环境显式
   表态**，不再由「默认跳过」替所有人默默做决定。

**默认仍是 skip**（CI 常态：内容仓不在 checkout 里），但默认那次 skip 现在
**带记录、带警告、带可判定的翻红开关**，不再是「无声消失」。

环境开关一览
------------
============================ ==================================================
``NOVELOS_HUMAN_BASELINE_CORPUS_ROOT``  **只**查这个根目录（设了即覆盖全部默认
                              候选）。两个用途：① 模拟「语料不存在」；
                              ② 阳性对照（指向自造语料，证明看守真会执行断言）。
``NOVELOS_REQUIRE_HUMAN_BASELINE``      设为 1/true/yes/on ⇒ 语料不可得时**判红**。
``NOVELOS_HUMAN_BASELINE_STATUS``       状态记录写到哪（默认 ``data/`` 下）。
============================ ==================================================

⚠️ **本模块是测试侧代码**：只被 ``tests/unit/quality/`` 下的测试导入，不进任何
生产装配路径。三个看守文件**不得**再自带「语料缺失 ⇒ 裸 ``pytest.skip``」的分支
——那会绕开本模块的记录与警告（``test_human_baseline_ci_visibility.py`` 对此有
静态看守）。
"""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from packages.core.content_sync.paths import DEFAULT_CONTENT_DIR

#: 语料缺失时出现在**警告 / skip 理由 / 状态记录**里的统一标记（可 grep）。
CORPUS_UNAVAILABLE_MARKER = "HUMAN-BASELINE-CORPUS-UNAVAILABLE"

#: 人类基线语料在内容仓内的相对路径（三仓分离：正文不进软件仓）。
REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")

#: 环境开关：设为 1/true/yes/on ⇒ 语料不可得时**判红**而不是跳过。
STRICT_ENV = "NOVELOS_REQUIRE_HUMAN_BASELINE"

#: 环境开关：**只**查这个根目录（设了就覆盖全部默认候选）。
CORPUS_ROOT_ENV = "NOVELOS_HUMAN_BASELINE_CORPUS_ROOT"

#: 环境开关：状态记录写到哪（默认见 :data:`DEFAULT_STATUS_RELATIVE`）。
STATUS_ENV = "NOVELOS_HUMAN_BASELINE_STATUS"

#: 状态记录默认路径（相对仓库根）。``data/`` 已在 ``.gitignore`` 内。
#: **JSONL**（每进程追加一行）——理由见 :func:`_write_status`。
DEFAULT_STATUS_RELATIVE = Path("data/human_baseline_status.jsonl")

#: 仓库根（本文件位于 ``<repo>/tests/unit/quality/``）。
_REPO_ROOT = Path(__file__).resolve().parents[3]


class HumanBaselineCorpusUnavailable(UserWarning):
    """**已停用**（2026-10-19）：自定义警告类会**打死 xdist 的控制器**。

    实测（本批踩到）：pytest-xdist 把 worker 里的警告**按「模块 + 类名」序列化**，
    交给控制器**重新 import** 才能还原；而本模块在测试里是被当作
    ``unit.quality._human_baseline_gate`` 导入的（pytest 的 prepend 模式把
    ``tests/`` 放进 ``sys.path``），**控制器那边 import 不到** ⇒
    ``ModuleNotFoundError: No module named 'unit'`` ⇒ **INTERNALERROR，整个
    ``-n 4`` 运行直接崩掉**（实测：``2 failed, 3 passed, 4 skipped`` 之后整个 run
    变成 INTERNALERROR）。

    ⇒ 语料缺席的警告一律用**内置** ``UserWarning``（xdist 对内置类别不重新
    import），可 grep 的标记放在**消息**里。**不要**在这里重新引入自定义警告类。

    保留这个类只为让「曾经踩过这个坑」留在代码里。
    """

    def __new__(cls, *args: object, **kwargs: object):  # pragma: no cover - 已停用
        raise RuntimeError(
            "HumanBaselineCorpusUnavailable 已停用：用内置 UserWarning，"
            "自定义警告类会打死 xdist 控制器（见本类 docstring）"
        )


#: 本进程内已记录过的「受影响的看守」（跨模块累计，写进状态记录）。
_AFFECTED_CALLERS: set[str] = set()

#: 本进程内已记录过的「因语料缺失而没跑的用例」（pytest 的 node id）。
_DEPENDENT_TESTS: set[str] = set()

#: 警告只发一次（否则 13 个用例会刷 13 行同义警告；状态记录仍是全量）。
_WARNED = False



def _current_test_node_id() -> str:
    """当前正在执行的用例 node id（取自 pytest 的 ``PYTEST_CURRENT_TEST``）。

    形如 ``tests/unit/quality/test_x.py::test_y (call)`` → 取前半段。

    它让「**哪些用例**因语料缺失而没跑」可被**机器**读出（状态记录的
    ``dependent_tests`` 段），而不只是「哪个模块」。pytest 未设置该变量时返回空串
    （例如本模块被当作普通脚本 import 时）。
    """
    raw = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not raw:
        return ""
    return raw.rsplit(" (", 1)[0].strip()


def corpus_search_roots() -> list[Path]:
    """人类基线语料的候选根目录（**有序**，先到先用）。

    默认顺序：``NOVELOS_CONTENT_DIR`` → :data:`DEFAULT_CONTENT_DIR`（内容仓默认
    路径）→ 软件仓同级的 ``NovelOS-Content``（按本文件位置推导）。

    ⚠️ 后两个候选在本机上**同址**（内容仓固定在软件仓同级）——它们是**双保险**：
    两处都指不到，才判「语料不可得」。

    ⚠️ ``NOVELOS_HUMAN_BASELINE_CORPUS_ROOT`` 一旦设置，本函数**只**返回它——
    这是「模拟语料不存在」与「阳性对照」的统一入口。
    """
    override = os.environ.get(CORPUS_ROOT_ENV)
    if override:
        return [Path(override)]
    roots: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        roots.append(Path(env_dir))
    roots.append(DEFAULT_CONTENT_DIR)
    roots.append(_REPO_ROOT.parent / "NovelOS-Content")
    return roots


def reference_corpus_dir() -> Path | None:
    """定位人类基线语料目录；不可得时返回 ``None``（**不**静默——调用方必须处理）。"""
    for root in corpus_search_roots():
        target = root / REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def strict_mode() -> bool:
    """是否处于「缺席即判红」模式（:data:`STRICT_ENV`）。"""
    return os.environ.get(STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def status_path() -> Path:
    """状态记录的目标路径（:data:`STATUS_ENV` 可覆盖）。"""
    override = os.environ.get(STATUS_ENV)
    if override:
        return Path(override)
    return _REPO_ROOT / DEFAULT_STATUS_RELATIVE


def _write_status(record: dict[str, Any]) -> None:
    """写状态记录（**尽力而为**的旁路，追加式 JSONL）。

    写不进去**不许**把测试判红——它是「缺席的痕迹」，不是判定依据。失败时发一条
    警告（不静默吞：坑区「降级范围比注释主张宽 ⇒ 故障被静默吞掉」）。

    ⚠️ **为什么是 JSONL（追加）而不是「读—改—写一个 JSON 对象」**：xdist
    （``-n 4``）会让 5 个基线文件落在**不同进程**里，各自写同一份记录 ⇒
    「读旧记录 → 合并 → 覆盖写」存在窗口，两个进程会互相覆盖（**实测丢过 5/14
    条**用例记录）。改成**每进程追加一行**（一行 = 该进程此刻的完整视图）⇒
    **不丢**；读侧把各行按集合取并集即可（:func:`read_status_records`）。

    同一进程会被写多行（每次判定一行）——无害：最后一行就是该进程的完整视图，
    合并取并集结果相同。
    """
    path = status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:  # 只吞「写不进去」，不吞别的
        warnings.warn(
            f"{CORPUS_UNAVAILABLE_MARKER}: 状态记录写入失败（{path}）：{exc}",
            UserWarning,
            stacklevel=2,
        )


def read_status_records(path: Path | None = None) -> dict[str, Any]:
    """读回状态记录（各行按集合取并集合并），读不了就返回空记录。

    合并后的形状与单行记录一致（``marker`` / ``status`` / ``affected_callers`` /
    ``dependent_tests`` / …），调用方不必关心它是几行。
    """
    target = path or status_path()
    merged: dict[str, Any] = {}
    sets: dict[str, set[str]] = {"affected_callers": set(), "dependent_tests": set()}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return merged
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if key in sets and isinstance(value, list):
                sets[key].update(str(v) for v in value)
            elif key not in merged or not merged[key]:
                merged[key] = value
    for key, values in sets.items():
        merged[key] = sorted(values)
    return merged


def _record_unavailable(caller: str, roots: list[Path], reason: str) -> dict[str, Any]:
    """把「语料不可得」记成一份**结构化事实**并落盘。"""
    _AFFECTED_CALLERS.add(caller)
    node_id = _current_test_node_id()
    if node_id:
        _DEPENDENT_TESTS.add(node_id)
    record: dict[str, Any] = {
        "marker": CORPUS_UNAVAILABLE_MARKER,
        "status": "unavailable",
        "reference_subpath": REFERENCE_SUBPATH.as_posix(),
        "searched_roots": [str(r) for r in roots],
        "affected_callers": sorted(_AFFECTED_CALLERS),
        "dependent_tests": sorted(_DEPENDENT_TESTS),
        "reason": reason,
        "strict_mode": strict_mode(),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_status(record)
    return record


def _warn_unavailable(record: dict[str, Any]) -> None:
    """发一次响亮警告（pytest 的 warnings summary 里会显式出现一行）。"""
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    roots = ", ".join(record["searched_roots"]) or "(无候选路径)"
    warnings.warn(
        f"{CORPUS_UNAVAILABLE_MARKER}: 人类基线语料不可得（查过：{roots}）——"
        f"AI 味阈值的人类侧看守在本环境**未执行**"
        f"（本进程已登记的受影响看守：{len(record['affected_callers'])} 个；"
        "完整清单见状态记录）。"
        "「检测器阈值是按人类基线校准过的」这一结论在本环境**不可复现**；"
        "可复现的替代路径见 tests/unit/quality/HUMAN_BASELINE.md。",
        UserWarning,
        stacklevel=2,
    )


def require_reference_corpus(caller: str) -> Path:
    """取人类基线语料目录；不可得时**响亮地**失败或跳过（不无声消失）。

    ``caller`` 建议传调用方的 ``__name__``——它会进状态记录的
    ``affected_callers``，让「哪几个看守没跑」可被机器读出。
    """
    roots = corpus_search_roots()
    for root in roots:
        target = root / REFERENCE_SUBPATH
        if target.is_dir():
            return target

    reason = (
        f"人类基线语料目录不存在：{REFERENCE_SUBPATH.as_posix()}"
        f"（查过：{', '.join(str(r) for r in roots) or '(无候选路径)'}）"
    )
    record = _record_unavailable(caller, roots, reason)
    _warn_unavailable(record)

    message = (
        f"{CORPUS_UNAVAILABLE_MARKER}: {reason} —— 人类基线看守在本环境未执行"
        f"（状态记录：{status_path()}）"
    )
    if strict_mode():
        pytest.fail(
            message
            + f"；{STRICT_ENV} 已开启：语料缺席不可接受，判红"
            + "（不判红 ⇒ 人类侧阈值无人看守）"
        )
    pytest.skip(message)
