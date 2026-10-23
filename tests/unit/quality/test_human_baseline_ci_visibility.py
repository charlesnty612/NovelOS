"""CI 可见性元测试：人类基线语料缺失时，「没跑」必须**响亮且可判定**。

问题（2026-10-19 实测确认）
---------------------------
本仓有 5 个测试文件消费**内容仓里的真实出版小说**（榜一《快穿之人渣洗白手册》
侯府弧 19 章 / 37806 可见字）当**人类基线**：

- ``test_human_baseline_ai_thresholds.py``（破折号 / 短段阈值的人类侧看守）
- ``test_readability_baseline.py``（长段 / 对话占比的人类侧看守）
- ``test_dialogue_echo_baseline.py``（接词回声的人类侧看守）
- ``test_ai_ending_summary_paragraph.py``（末段切分口径的人类侧对照）
- ``test_continuity_time.py``（时点矛盾算子的「人类侧机会数为 0」）

内容仓按 2026-09-14 裁决**不推 GitHub** ⇒ CI（只 checkout 软件仓）里那份语料
**根本不存在** ⇒ 这 5 个文件在 CI 中**恒 skip** ⇒ **「人类基线看守在 CI 中从未
生效过」，而 CI 报绿**。

这是坑区「**量具的缺陷被当成被测物的性质**」的新形态：**一个恒 skip 的守卫等于
不存在，但 CI 报绿**。本文件把这件事变成**可判定的**：

============================ ==========================================
``absence_recorded``         语料缺席，5 个看守**全部跳过**，且**记录了**
                            （状态 JSON + 警告 + skip 理由里都有
                            ``HUMAN-BASELINE-CORPUS-UNAVAILABLE`` 标记）。
``absence_forced_red``      缺席被**显式判红**（``NOVELOS_REQUIRE_HUMAN_BASELINE=1``）
                            —— 缺席是否可接受由环境表态，不由「默认跳过」代办。
``silent_green``            ⛔ **最坏**：语料缺席，看守却**报绿**（= skip 被改成
                            pass，或守卫生效却什么也没查）。本文件必须抓得住它。
``guard_attempted_red``     语料在场（或被冒充）⇒ 看守**真跑了**并因基线数字
                            不符而报错——这是**阳性对照**，证明「跳过」不是无条件的。
============================ ==========================================

怎么做到「不靠手挑名单」
------------------------
「哪些文件消费人类基线」是**派生**的：扫描 ``tests/unit/quality/`` 下所有
``test_*.py``，凡调用 ``gate.require_reference_corpus(`` 的即算（见
:func:`_corpus_consumer_modules`）。⇒ 新增一个消费语料的测试文件**自动**进判别面，
不会像「另列一份手挑名单」那样悄悄变小（坑区「看守自己被悄悄缩小了执行域」）。

突变自检
--------
本文件带**故障注入**（:data:`_MUTATION_PLUGIN_SOURCE`）：它把「语料缺失 ⇒ 跳过」
变成「语料缺失 ⇒ 照常报绿」（运行时吞掉用例调用，**不改任何仓库文件**），用来
证明 :func:`guard_state` 这台检测器**不是瞎的**——它必须把那种形态判成
``silent_green``。另有一个**外层**注入点：设环境变量
``NOVELOS_HUMAN_BASELINE_FAULT=skip_to_pass`` 再跑本文件，则
「缺席不得报绿」那条断言**必须转红**（这是本文件自检自身的入口）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ._human_baseline_gate import (
    CORPUS_UNAVAILABLE_MARKER,
    REFERENCE_SUBPATH,
    read_status_records,
)

_QUALITY_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 故障注入开关（外层注入点）：设为 ``skip_to_pass`` ⇒ 内层运行里「语料缺失 ⇒
#: 跳过」被换成「照常报绿」，本文件的「缺席不得报绿」断言必须转红。
FAULT_ENV = "NOVELOS_HUMAN_BASELINE_FAULT"

#: 判定结果（见模块 docstring 的表）。
STATE_ABSENCE_RECORDED = "absence_recorded"
STATE_ABSENCE_FORCED_RED = "absence_forced_red"
STATE_ABSENCE_SILENT = "absence_silent"
STATE_SILENT_GREEN = "silent_green"
STATE_GUARD_RED = "guard_attempted_red"
STATE_MIXED = "mixed"

#: 任务书点名的三个看守文件——它们**必须**在派生面里（新增文件只能加不能减）。
_REQUIRED_CONSUMERS = frozenset(
    {
        "test_human_baseline_ai_thresholds.py",
        "test_readability_baseline.py",
        "test_dialogue_echo_baseline.py",
    }
)

_OUTCOME_RE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b", re.M)

#: 故障注入插件源码（**不改任何仓库文件**：运行时写到临时目录，用 ``-p`` 加载）。
_MUTATION_PLUGIN_SOURCE = '''\
"""故障注入插件：把「语料缺失 ⇒ 跳过」变成「语料缺席 ⇒ 照常报绿」。

这是元测试的**自检注入点**：插件把目标用例的**被测函数**换成一个空实现 ⇒ 用例
照常走完 setup/teardown 并报 passed（=「skip 改成 pass」的**可观测后果**），
从而检验「语料缺席却报绿」这一形态**确实会被本仓的检测器抓到**。

⚠️ 为什么不用 ``pytest_runtest_call`` 返回 True 吞掉调用：``pytest_runtest_call``
**不是 firstresult 钩子**，pytest 自带的默认实现（trylast）照样会执行 ⇒ 返回值
拦不住任何东西（本批实测：注入"生效"了但用例仍报 skipped）。换成替换被测函数
（``item.obj`` 有 setter）才是真拦。

由环境变量 ``NOVELOS_HUMAN_BASELINE_FAULT=skip_to_pass`` 激活。
"""

import os

FAULT_ENV = "NOVELOS_HUMAN_BASELINE_FAULT"
GUARD_FLAG = "HUMAN_BASELINE_GUARD"


def _fault() -> str:
    return os.environ.get(FAULT_ENV, "").strip()


def _noop() -> None:
    """空实现：替掉被测函数 ⇒ 用例什么也没查，却报 passed。"""
    return None


def pytest_collection_modifyitems(session, config, items):
    """把人类基线模块里每个用例的被测函数换成空实现。"""
    if _fault() != "skip_to_pass":
        return
    for item in items:
        module = getattr(item, "module", None)
        if module is None or not getattr(module, GUARD_FLAG, False):
            continue
        item.obj = _noop
'''


# ---------------------------------------------------------------------------
# 派生面：哪些文件消费人类基线语料
# ---------------------------------------------------------------------------


def _corpus_consumer_modules() -> list[Path]:
    """``tests/unit/quality/`` 下**消费人类基线语料**的测试文件（派生，不手挑）。

    判据 = 文件里出现 ``gate.require_reference_corpus(``。**本文件自身被排除**：
    它在源码里提到这个调用（派生逻辑本身），但它不是语料的消费者——自指会把
    检测器自己的判别面算进去。
    """
    out: list[Path] = []
    self_path = Path(__file__).resolve()
    for path in sorted(_QUALITY_DIR.glob("test_*.py")):
        if path.resolve() == self_path:
            continue
        text = path.read_text(encoding="utf-8")
        if "gate.require_reference_corpus(" in text:
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# 内层运行器
# ---------------------------------------------------------------------------


@dataclass
class BaselineRun:
    """一次内层 pytest 运行的结果（**只读回放**，不碰任何仓库文件）。"""

    exit_code: int
    output: str
    outcomes: dict[str, str] = field(default_factory=dict)

    def outcome(self, node_id: str) -> str | None:
        return self.outcomes.get(node_id)

    @property
    def marker_present(self) -> bool:
        return CORPUS_UNAVAILABLE_MARKER in self.output


def _parse_outcomes(stdout: str) -> dict[str, str]:
    """从 ``pytest -v`` 输出里取「node id → 结果」（结果统一转**小写**）。

    只解析 **short test summary 之前**的逐用例行（``-v`` 模式）：short summary 会把
    同一个位置的多次 skip 聚合成一行（``SKIPPED [4] <位置>: 理由``），拿不到
    逐用例的 node id。

    ⚠️ pytest 输出的是**大写**（``SKIPPED`` / ``FAILED`` / ``PASSED``）——判据侧一律
    用小写比较，故此处统一转小写。**大小写不一致的失效方向与「断言测不到机制」
    同形**：分类函数会全部落到兜底分支（本批实测：状态恒 ``mixed``，看着像
    「语料在场」，实则是量具口径没对齐）。
    """
    head = stdout.split("short test summary info", 1)[0]
    out: dict[str, str] = {}
    for m in _OUTCOME_RE.finditer(head):
        out[m.group("nodeid")] = m.group("outcome").lower()
    return out


def _run_baselines(
    tmp_path: Path,
    *,
    corpus_root: Path,
    status_path: Path,
    strict: bool = False,
    mutation: str | None = None,
    node_ids: list[str] | None = None,
) -> BaselineRun:
    """在**语料被显式指定**的条件下跑一遍人类基线看守（子进程，只读）。

    - ``corpus_root``：指向语料根目录（指向不存在的目录 ⇒ 模拟「语料缺席」）。
    - ``strict``：开 ``NOVELOS_REQUIRE_HUMAN_BASELINE=1`` ⇒ 缺席判红。
    - ``mutation``：故障注入模式（见 :data:`_MUTATION_PLUGIN_SOURCE`）。
    - ``node_ids``：只跑这些用例（``None`` ⇒ 跑全部消费语料的文件）。
    """
    env = dict(os.environ)
    # ⚠️ 两侧同时钉死：子进程 stdout 的编码跟随继承的环境变量，而父进程的解码器
    # 跟随 locale（本机 = cp936）——只给一边照样中招（见 AGENTS 坑区「子进程输出编码」）。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NOVELOS_HUMAN_BASELINE_CORPUS_ROOT"] = str(corpus_root)
    env["NOVELOS_HUMAN_BASELINE_STATUS"] = str(status_path)
    env.pop("NOVELOS_CONTENT_DIR", None)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop(FAULT_ENV, None)
    if strict:
        env["NOVELOS_REQUIRE_HUMAN_BASELINE"] = "1"
    else:
        env.pop("NOVELOS_REQUIRE_HUMAN_BASELINE", None)

    cmd = [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider"]
    # ⚠️ 子进程必须**自带** ``--basetemp``：外层的 ``--basetemp`` 对子进程无效
    # （Lead 实测），而本机 ``%LOCALAPPDATA%\Temp\pytest-of-<user>`` 根目录一旦被
    # 创建就**连 owner 都拒绝访问**（``PermissionError: [WinError 5]``）⇒ 内层运行
    # 在 collection 期就整体 ERROR、退出码 1 ⇒ 「缺席不该把运行判红」那条断言红。
    # 目录必须**每次唯一**（``mkdtemp``）——本函数在同一个用例里会被连跑多次
    # （缺席 / 严格模式 / 故障注入 / 阳性对照）⇒ 共享目录会互撞。
    child_basetemp = tempfile.mkdtemp(prefix="novelos_human_baseline_inner_")
    cmd.append(f"--basetemp={child_basetemp}")
    if node_ids:
        cmd += list(node_ids)
    else:
        cmd += [p.relative_to(_REPO_ROOT).as_posix() for p in _corpus_consumer_modules()]

    if mutation:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "human_baseline_fault.py").write_text(
            _MUTATION_PLUGIN_SOURCE, encoding="utf-8"
        )
        env["PYTHONPATH"] = os.pathsep.join(
            [str(plugin_dir), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        env[FAULT_ENV] = mutation
        cmd += ["-p", "human_baseline_fault"]

    proc = subprocess.run(
        cmd,
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    # 子进程已退出 ⇒ 句柄已释放；``ignore_errors=True`` 保证 Windows 上残留句柄
    # **不会**把清理失败升级成用例失败（清理不是判别点）。
    shutil.rmtree(child_basetemp, ignore_errors=True)
    stdout = proc.stdout or ""
    return BaselineRun(
        exit_code=proc.returncode,
        output=stdout + (proc.stderr or ""),
        outcomes=_parse_outcomes(stdout),
    )


def _make_fake_corpus(root: Path) -> Path:
    """造一份**自造**的假语料（19 章，每章 ≥200 可见字）。

    唯一用途是**阳性对照**：语料「在场」时，看守必须**真跑**并因登记基线
    （37806 可见字等）与假语料不符而**报错**——以此证明「跳过」不是无条件的
    （否则「恒 skip」与「恒 pass」在这个量具上长得一模一样）。
    """
    target = root / REFERENCE_SUBPATH
    target.mkdir(parents=True, exist_ok=True)
    filler = "这里是自造的占位正文，用途仅是阳性对照：证明人类基线看守在语料在场时确实会执行断言。"
    body = "\n".join(filler * 4 for _ in range(2))
    for no in range(1, 20):
        (target / f"第{no}章-占位.txt").write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------


def guard_state(run: BaselineRun, dependent_ids: set[str]) -> str:
    """判定「人类基线看守」在某次运行后的状态（检测器本体）。

    判据（顺序有讲究）：
    1. **先看有没有报错**——有报错说明看守**真跑了**并提出异议（阳性对照 / 判红）；
    2. **全 passed** ⇒ ``silent_green``（最坏：语料缺席却报绿）；
    3. **全 skipped** ⇒ 看有没有标记：``absence_recorded`` / ``absence_silent``。
    """
    if not dependent_ids:
        return STATE_MIXED
    reported = [run.outcome(tid) for tid in sorted(dependent_ids)]
    if any(v in {"failed", "error"} for v in reported):
        return STATE_ABSENCE_FORCED_RED if run.marker_present else STATE_GUARD_RED
    if all(v == "passed" for v in reported):
        return STATE_SILENT_GREEN
    if all(v == "skipped" for v in reported):
        return STATE_ABSENCE_RECORDED if run.marker_present else STATE_ABSENCE_SILENT
    return STATE_MIXED


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@dataclass
class BlockedRun:
    """「语料缺席」下的内层运行 + 它写下的状态记录。

    - ``run``：**被检视**的那次运行（外层故障注入会作用在它身上）。
    - ``reference``：永远不受注入影响的参照运行（提供状态记录与「哪些用例依赖语料」）。
    - ``record``：参照运行写下的状态记录。
    """

    run: BaselineRun
    reference: BaselineRun
    record: dict


@pytest.fixture(scope="module")
def blocked(tmp_path_factory) -> BlockedRun:
    """语料缺席（默认形态）下跑一次全部人类基线看守，并读回状态记录。

    **外层故障注入**（``NOVELOS_HUMAN_BASELINE_FAULT=skip_to_pass``）会额外跑一次
    带注入的运行，并把它作为「被检视的那次」⇒ 「缺席不得报绿」那条断言**必须
    转红**（这是本文件自检自身的入口，见模块 docstring）。
    """
    tmp = tmp_path_factory.mktemp("human_baseline_blocked")
    status_path = tmp / "status.jsonl"
    reference = _run_baselines(
        tmp,
        corpus_root=tmp / "no-such-corpus-root",
        status_path=status_path,
    )
    record = read_status_records(status_path)
    fault = (os.environ.get(FAULT_ENV) or "").strip() or None
    if fault:
        examined = _run_baselines(
            tmp,
            corpus_root=tmp / "no-such-corpus-root",
            status_path=tmp / "status_fault.jsonl",
            mutation=fault,
        )
    else:
        examined = reference
    return BlockedRun(run=examined, reference=reference, record=record)


def _dependent_ids(blocked: BlockedRun) -> set[str]:
    return set(blocked.record.get("dependent_tests") or [])


# ---------------------------------------------------------------------------
# 判别用例
# ---------------------------------------------------------------------------


def test_corpus_absence_is_recorded_and_never_reported_as_success(blocked: BlockedRun) -> None:
    """语料缺席 ⇒ 全部跳过 + **留下记录**；**绝不**报成「跑过了」。

    这是本文件的主判别点。若哪天有人把 gate 里的 ``pytest.skip`` 改回「静默通过」，
    本条必红（``silent_green``）。
    """
    dependent = _dependent_ids(blocked)
    assert dependent, (
        "状态记录里没有任何「因语料缺失而没跑」的用例——判定点失效，"
        "本文件会变成「测不到机制」的绿断言"
    )
    state = guard_state(blocked.run, dependent)
    assert state == STATE_ABSENCE_RECORDED, (
        f"语料缺席时的看守状态实测 {state}（期望 {STATE_ABSENCE_RECORDED}）——"
        "缺席必须被记录，而不是被报成绿"
        f"（{STATE_SILENT_GREEN} = 语料缺席却报绿 = 本文件要抓的形态）"
    )
    assert blocked.run.marker_present, (
        f"内层运行输出里没有 {CORPUS_UNAVAILABLE_MARKER} 标记："
        "「没跑」在 CI 日志里不是显式的一行"
    )
    for node_id in sorted(dependent):
        assert blocked.run.outcome(node_id) == "skipped", (
            f"{node_id} 在语料缺席时结果为 {blocked.run.outcome(node_id)}——"
            "语料缺席时它必须跳过（跳过 = 显式声明没跑）"
        )


def test_status_record_is_machine_readable(blocked: BlockedRun) -> None:
    """状态记录必须是**结构化事实**：谁没跑、查了哪些路径、什么时候、严不严格。"""
    record = blocked.record
    assert record["marker"] == CORPUS_UNAVAILABLE_MARKER
    assert record["status"] == "unavailable"
    assert record["reference_subpath"] == REFERENCE_SUBPATH.as_posix()
    assert record["searched_roots"], "记录里没有「查过哪些路径」——缺席不可复核"
    assert record["affected_callers"], "记录里没有「哪些模块的看守没跑」"
    assert record["dependent_tests"], "记录里没有「哪些用例因语料缺失而没跑」"
    assert record["recorded_at"], "记录里没有时间戳"
    assert record["strict_mode"] is False
    assert "不存在" in record["reason"]
    # **默认策略**：缺席不判红（CI 常态：内容仓不在 checkout 里）——但缺席是
    # **显式记录**的，且可用 NOVELOS_REQUIRE_HUMAN_BASELINE=1 翻红（另有一条用例）。
    assert blocked.reference.exit_code == 0, (
        "默认形态下语料缺席不该把整个运行判红（那是策略开关的事）"
    )


def test_every_corpus_consumer_reports_its_absence(blocked: BlockedRun) -> None:
    """**每个**消费语料的模块都必须登记自己的缺席（派生面不许漏网）。

    这条堵的是「新增一个消费语料的测试文件，却忘了接 gate」——那种文件会自己
    带一个裸 ``pytest.skip``，于是又变成一次无声消失。
    """
    dependent = _dependent_ids(blocked)
    consumers = _corpus_consumer_modules()
    assert consumers, "派生面为空——判别面被悄悄缩小了"
    for path in consumers:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        assert any(tid.startswith(rel + "::") for tid in dependent), (
            f"{rel} 消费了人类基线语料，但在「语料缺席」时没有登记任何没跑的用例——"
            "它很可能自带了裸 pytest.skip（绕开了状态记录与警告）"
        )


def test_strict_mode_turns_the_absence_into_a_red_run(tmp_path, blocked: BlockedRun) -> None:
    """``NOVELOS_REQUIRE_HUMAN_BASELINE=1`` ⇒ 缺席**判红**（缺席可判定、可翻红）。

    默认形态仍是 skip（CI 常态：内容仓不在 checkout 里），但「缺席是否可接受」
    由**环境显式表态**，不再由「默认跳过」替所有人默默决定。
    """
    run = _run_baselines(
        tmp_path,
        corpus_root=tmp_path / "no-such-corpus-root",
        status_path=tmp_path / "status.jsonl",
        strict=True,
        node_ids=sorted(_dependent_ids(blocked)),
    )
    assert run.exit_code != 0, "严格模式下语料缺席必须判红（退出码 0 = 没人拦）"
    assert run.marker_present, "判红信息里必须带标记，否则日志里看不出红在哪"
    state = guard_state(run, _dependent_ids(blocked))
    assert state == STATE_ABSENCE_FORCED_RED, f"严格模式下的看守状态实测 {state}"


def test_detector_flags_the_skip_to_pass_mutation(tmp_path, blocked: BlockedRun) -> None:
    """**突变自检**：把「跳过」换成「照常报绿」，检测器必须抓得住。

    变异 = 运行时吞掉人类基线模块的用例调用（``skip_to_pass`` 故障注入）⇒ 用例
    报 passed。**不改任何仓库文件**。若这条转红，说明 :func:`guard_state` 已被
    削瞎（那才是真正危险的事：看守变成了永远绿的装饰）。
    """
    run = _run_baselines(
        tmp_path,
        corpus_root=tmp_path / "no-such-corpus-root",
        status_path=tmp_path / "status.jsonl",
        mutation="skip_to_pass",
        node_ids=sorted(_dependent_ids(blocked)),
    )
    assert run.exit_code == 0, "故障注入后的运行应当是「绿的」（那正是要被抓的形态）"
    state = guard_state(run, _dependent_ids(blocked))
    assert state == STATE_SILENT_GREEN, (
        f"「skip 改成 pass」的变异没有被抓出来（检测器判成 {state}）——"
        "检测器被削瞎了"
    )


def test_positive_control_a_present_corpus_makes_the_guard_actually_run(
    tmp_path, blocked: BlockedRun
) -> None:
    """**阳性对照**：语料在场 ⇒ 看守**真跑**并因基线数字不符而报错。

    这条堵的是「恒 skip」与「恒 pass」在量具上同形：若守卫生效却什么也没查
    （空断言），它会报绿；阳性对照证明这条路径**真的会红**。
    """
    fake_root = tmp_path / "fake-corpus-root"
    _make_fake_corpus(fake_root)
    run = _run_baselines(
        tmp_path,
        corpus_root=fake_root,
        status_path=tmp_path / "status.jsonl",
        node_ids=sorted(_dependent_ids(blocked)),
    )
    assert run.exit_code != 0, (
        "假语料在场时看守竟然全绿——说明断言没在查东西（空断言）"
    )
    state = guard_state(run, _dependent_ids(blocked))
    assert state == STATE_GUARD_RED, f"阳性对照下的看守状态实测 {state}"


def test_corpus_consumers_must_delegate_to_the_single_gate() -> None:
    """静态看守：语料定位与「不可得怎么办」**只能**由 gate 一处决定。

    判别点必须**专属**：只针对「消费人类基线」的文件（派生面），不牵连全仓。
    """
    consumers = _corpus_consumer_modules()
    assert consumers, "派生面为空——判别面被悄悄缩小了"
    names = {p.name for p in consumers}
    assert _REQUIRED_CONSUMERS <= names, (
        f"任务书点名的三个看守文件不在派生面里：{sorted(_REQUIRED_CONSUMERS - names)}"
    )
    for path in consumers:
        text = path.read_text(encoding="utf-8")
        assert "HUMAN_BASELINE_GUARD = True" in text, (
            f"{path.name} 缺少 HUMAN_BASELINE_GUARD 标记——故障注入插件据此定位目标用例"
        )
        assert "内容仓参考书目录不存在" not in text, (
            f"{path.name} 里又出现了旧的裸 skip 理由——它会绕开状态记录与警告"
        )
        assert "榜一-快穿之人渣洗白手册" not in text, (
            f"{path.name} 里出现了语料路径字面量——语料定位只归 gate 一处管"
        )
