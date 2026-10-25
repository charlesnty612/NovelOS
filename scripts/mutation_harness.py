"""突变验证骨架（AGENTS §三.2：新测试必须做突变验证）。

逐条：改一处 → 跑**定向**用例 → 记结果 → 按 SHA256 还原并校验。

用法
----
改 ``MUTATIONS`` 表（编号 / 目标文件 / 锚点 / 变异 / 定向用例），然后::

    .venv/Scripts/python.exe scripts/mutation_harness.py --root <仓外副本根> --confirm

退出码非 0 = 有突变存活或脚本自身中止——**不要**把它当成「大致通过」。

为什么要有这个脚本（而不是每次手写一行）
----------------------------------------
AGENTS 坑区已经记了两条同族事故：

* 「突变被 SKIP ≠ 突变存活——两者都只表现为『没红』」
* 「量具没量到目标，却报告成量过了」

本脚本在 2026-10-15 的 G6 批次当场又撞上第三条，而且**发生在本脚本自己身上**：
用相对路径 ``.venv/Scripts/python.exe`` 起子进程时 cwd 不匹配 → ``FileNotFoundError``，
而 ``FileNotFoundError`` 与「用例红」在**非零退出码**上完全同形 → 6 条突变全部
误报 [CAUGHT]，实际上一条用例都没跑。因此这里有三条硬性防御：

1. **绝对路径**：子进程 cwd 由本脚本显式指定，命令用 ``__file__`` 推出来的绝对路径。
2. **启动失败与测试失败分桶**：``FileNotFoundError`` 归 ``INVALID``（本次无效），
   绝不落进 survived/caught 任何一桶。
3. **必须有「跑过了」的证据**：输出里找不到 runner 的执行痕迹（vitest 的
   ``Test Files`` / pytest 的 `` passed``）时同样判 INVALID——被上游杀掉、
   收集阶段就崩了，和「断言失败」在退出码上也是同形的。

另两条纪律：

* 锚点未命中 = **脚本自身缺陷，强制中止**，不许记成「跳过」；
* 还原按 SHA256 校验，不靠 mtime（``copy2`` 会保留 mtime，字节变了而 mtime 不变）。

单跑绿 ≠ 没问题；本脚本只判「突变是否被捕获」，最终结论仍以全量闸门为准。
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# ⚠️ 本机 Windows stdout 默认 cp936（坑区「Windows 默认 cp936 吞中文」家族）。
# **两侧都要钉**：本进程 stdout 重配 + 子进程 env——只给一边照样乱码。
# ⚠️ **重配必须放进函数、且判 hasattr**（2026-10-23 实测修）：模块级副作用会让
# ``import scripts.mutation_harness`` 在 pytest 捕获期**当场崩**（捕获期的
# ``sys.stdout`` 可能没有 ``reconfigure`` 方法）⇒ 该脚本从此「不可测」，
# 而它恰好是**收尾闸门的一环**⇒ 不可测的闸门最危险。
# 形状照 ``scripts/backup_db.py`` 的既有正确写法（函数内 + 判 hasattr）。

# 本文件在 scripts/ 下 ⇒ 仓库根是 parent.parent。写死 `.` 会在从子目录调用时错位
# （本脚本 2026-10-15 搬到 scripts/ 后当场踩到：路径拼成 scripts/apps/web，
#  7 条突变全被 INVALID 判掉——这正是第 2 条防御生效的样子）。
ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
# ⚠️ 必须用**绝对路径**：相对路径在子进程里以 cwd 解析，而 cwd 与本脚本所在
# 目录不保证一致——踩过一次：`FileNotFoundError` 被脚本当成了「用例变红」，
# 于是 6 条突变全部报 [CAUGHT]，而实际上**一个用例都没跑**。
# 这正是坑区「量具没量到目标，却报告成量过了」：启动失败与测试失败在
# 「非零退出码」上完全同形。
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
VITEST = WEB / "node_modules" / ".bin" / ("vitest.cmd" if sys.platform == "win32" else "vitest")

#: 软件仓**活树**根（硬拒目标）。突变验证**只许在仓外副本**上做。
LIVE_REPO_ROOT = pathlib.Path(r"D:\zcodeproject\NovelOS")


def _same_dir(a: pathlib.Path, b: pathlib.Path) -> bool:
    """两个路径是否指向同一目录（按底层身份比，绕开 junction / 别名拼写）。"""
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def assert_target_root_allowed(target: pathlib.Path) -> pathlib.Path:
    """目标根必须是**显式仓外副本**；指向软件仓活树（含其子目录/父目录）⇒ 硬拒。

    ⚠️ BUG-28（2026-10-25 · Chief 06:23 裁「一类设计缺陷」）：本脚本过去**不解析任何参数**
    ⇒ ``--help`` 会直接跑 21 条硬编码突变**改写活树源码**（今晚 04:41 事故的直接成因：
    活树两次出现瞬时脏文件，根因就是它）。

    ⇒ 现在的两条硬约束：
    ① **参数必须显式给** ``--root``（argparse 负责：``--help`` / ``-h`` / 无参 / 未知参数
       一律**只打印用法并退出**——不触碰任何文件、不起任何子进程）；
    ② 目标根指向**活树**（``D:\\zcodeproject\\NovelOS``）或其子目录 ⇒ **直接拒绝运行**。

    ⚠️ 判定用**同一套归一化**（``resolve()`` ＋ 底层身份 ``samefile``）⇒
    ``D:\\zcodeproject\\.\\NovelOS``、``\\\\?\\D:\\...``、junction 别名等写法都判成同一棵树。
    """
    root = pathlib.Path(target).expanduser().resolve()
    live = LIVE_REPO_ROOT.resolve() if LIVE_REPO_ROOT.exists() else LIVE_REPO_ROOT
    inside_live = (root == live) or (live in root.parents) or (root in live.parents)
    if inside_live or _same_dir(root, live):
        raise SystemExit(
            "[ABORT] 拒绝在软件仓（活树）内改写源码："
            f"{root}\n"
            f"       活树＝{live}。\n"
            "       本脚本只允许对**仓外副本**做突变验证（活树内任何参数形式都禁跑）。\n"
            "       正确用法：\n"
            "           1) 把仓库复制到仓外副本根；\n"
            "           2) python scripts/mutation_harness.py --root <仓外副本根> --confirm"
        )
    if not root.is_dir():
        raise SystemExit(f"[ABORT] 目标根不存在或不是目录：{root}")
    return root


class LaunchFailure(RuntimeError):
    """测试命令**没能启动**——与「用例红」是两回事，必须分开报。"""


def _reconfigure_stdio() -> None:
    """把 stdout/stderr 重配成 UTF-8（**函数内 + 判 hasattr**，见模块顶部注释）。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


#: 判定标签（三桶，与 harness 主循环的 survived / caught / aborted 一一对应）。
JUDGE_SURVIVED = "SURVIVED"
JUDGE_CAUGHT = "CAUGHT"
JUDGE_INVALID = "INVALID"


def judge_pytest_run(code: int, out: str) -> tuple[str, str]:
    """判定一次 pytest 子进程跑的结论 ⇒ ``(标签, 理由)``。**纯函数**（不碰文件、不起进程）。

    判定表（每格都有**仓内**看守，见 ``tests/unit/test_mutation_harness_judgment.py``）::

        退出码 0                       ⇒ SURVIVED（真全过）
        退出码 1 且摘要含 " failed"     ⇒ CAUGHT（真红了）
        退出码 1 但摘要**无** " failed" ⇒ INVALID（用例是 ERROR 而非 FAIL ⇒ 环境/收集层炸了）
        退出码 2/3/4/5                 ⇒ INVALID（压根没跑过）
        输出含 "no tests ran"          ⇒ INVALID（零测试被收集）

    ⚠️ **"1 + 既有 passed 又有 errors" 必须判 INVALID**：那正是本机
    ``%LOCALAPPDATA%\\Temp\\pytest-of-<user>`` 属主拒绝访问时的真实输出形态
    （``1 passed, 3 errors``）⇒ 旧实现只看 ``" passed"`` 子串 ⇒ **误报成 CAUGHT**。

    ⚠️ **本函数与 ``scripts/mutation_probe.py`` 的同名函数是「刻意复制」**：两套突变
    执行器各自独立运行（probe 可被复制到别处单跑）⇒ 抽共享模块会扩依赖面
    ⇒ **改一处必须同步改另一处**；同口径由
    ``tests/unit/test_mutation_harness_judgment.py`` 的「两者不许分叉」用例**机器保证**
    （AGENTS 坑区「两套实现口径分叉」的既有对策形状：用机器看守钉住，而不是靠结构）。
    """
    if code not in (0, 1):
        return JUDGE_INVALID, (
            f"pytest 退出码 {code}（2=中断 / 3=内部错误 / 4=用法或收集错误 / 5=零测试被收集）"
            f"——都不是「跑过了」"
        )
    if "no tests ran" in out:
        return JUDGE_INVALID, "pytest 报告「no tests ran」⇒ 目标文件没收集到任何用例"
    if code == 1:
        if " failed" in out:
            return JUDGE_CAUGHT, "pytest 退出码 1 且摘要含「 failed」⇒ 有用例断言失败（真被抓住）"
        return JUDGE_INVALID, (
            "pytest 退出码 1 但摘要里没有「 failed」⇒ 用例是 **ERROR** 而非 FAIL"
            "（环境 / 收集层炸了，一个断言都没跑到）⇒ 不算「被抓住」"
        )
    if " passed" not in out:
        return JUDGE_INVALID, "pytest 退出码 0 但输出里没有「 passed」⇒ 没有任何执行痕迹"
    return JUDGE_SURVIVED, "pytest 退出码 0 且有用例通过数 ⇒ 全过"


def judge_vitest_run(code: int, out: str) -> tuple[str, str]:
    """判定一次 vitest 子进程跑的结论 ⇒ ``(标签, 理由)``。**纯函数**。

    ⚠️ 口径**来自实测**（2026-10-23，vitest 2.1.9），**不是**照搬 pytest：

    ===========================  ====  ==========================================
    情形                          退出码  输出关键行
    ===========================  ====  ==========================================
    全过                          0     ``Test Files 1 passed`` / ``Tests 13 passed``
    断言失败                      1     ``Test Files 1 failed`` / ``Tests 1 failed``
    收集期错误（模块解析失败）      1     ``Test Files 1 failed`` / ``Tests  no tests``
    零测试收集                    1     ``No test files found``（**无** Test Files 行）
    环境炸了（config 加载失败）    1     错误栈（**无** Test Files 行）
    ===========================  ====  ==========================================

    ⇒ **判别点**有两个：①「``Test Files``」行（有没有真的跑起测试文件）；
    ②「``Tests  no tests``」（套件在收集期就炸了 ⇒ **一个用例都没跑到**）。
    ①②合起来才能把「环境炸了」与「断言失败」分开——只看 ① 的话，
    「收集期错误」与「断言失败」的摘要**逐字同形**。

    ⚠️ **已知未覆盖面（2026-10-23 登记）**：vitest 的「Unhandled Error」形态**未**纳入
    实测（已实测 6 种：全绿 / 断言失败 / 收集期错误 / 零测试收集 / 环境炸了 /
    ``--passWithNoTests``）⇒ 若 vitest 改版输出形态变化，本判定表需**重新实测**。

    ⚠️ **本函数与 ``scripts/mutation_probe.py`` 的同名函数是「刻意复制」**
    ⇒ 改一处必须同步改另一处（机器保证见
    ``tests/unit/test_mutation_harness_judgment.py`` 的「两者不许分叉」用例）。
    """
    text = _ANSI.sub("", out)
    if "Test Files" not in text:
        return JUDGE_INVALID, (
            "vitest 输出里没有「Test Files」计数行 ⇒ 环境炸了 / 零测试收集（一个用例都没跑到）"
        )
    if "no tests" in text:
        return JUDGE_INVALID, (
            "vitest 报「Tests  no tests」⇒ 套件在收集期就炸了（Failed Suites），"
            "一个用例都没跑到 ⇒ 不算「被抓住」"
        )
    if code == 0:
        return JUDGE_SURVIVED, "vitest 退出码 0 且有测试计数 ⇒ 全过"
    return JUDGE_CAUGHT, "vitest 退出码非 0 且有「failed」用例 ⇒ 真失败"


def _run(cmd, cwd) -> tuple[int, str]:
    """跑一次命令，返回 **(原始退出码, 输出)**。

    ⚠️ **必须回传原始退出码**（2026-10-23 实测修）：本仓坑区已登记多次「非零退出码
    同时承载『跑过了且红』与『压根没跑』」⇒ 只回传 ``bool`` 会把两者压平成同一个
    False，而 :func:`_assert_tests_ran` 需要靠退出码把「用例真红」与「环境炸了」分开。
    """
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except FileNotFoundError as exc:  # 命令不存在 / 路径错
        raise LaunchFailure(f"{cmd[0]} 启动失败: {exc}") from exc
    return r.returncode, (r.stdout + r.stderr)


def _assert_tests_ran(out: str, runner: str, code: int) -> None:
    """判定失败 ⇒ **响亮抛错**（``LaunchFailure``），绝不静默当成「捕获」。

    ⚠️ 口径全部收在 :func:`judge_pytest_run` / :func:`judge_vitest_run` 两个**纯函数**里
    ⇒ 本函数只是把它们的结果翻译成异常 ⇒ 同一张判定表只有一个出处
    （本仓坑区「两套实现口径分叉」形状的防线）。
    """
    label, reason = judge_pytest_run(code, out) if runner == "pytest" else judge_vitest_run(code, out)
    if label == JUDGE_INVALID:
        raise LaunchFailure(reason)

# (编号, 目标文件, 锚点(原文), 替换(变异), 定向 vitest 用例过滤)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "M1",
        "apps/web/src/pages/bible/VolumesTab.tsx",
        "disabled={v.status === 'sealed'}",
        "disabled={false}",
        "src/pages/bible/VolumesTab.test.tsx",
    ),
    (
        "M2",
        "apps/web/src/pages/bible/VolumesTab.tsx",
        "return chapter.volume_id ?? null;",
        "return null;",
        "src/pages/bible/VolumesTab.test.tsx",
    ),
    (
        "M3",
        "apps/web/src/pages/bible/VolumesTab.tsx",
        "onClick={() => setSealTarget(v)}",
        "onClick={() => void handleSeal()}",
        "src/pages/bible/VolumesTab.test.tsx",
    ),
    (
        "M4",
        "apps/web/src/pages/bible/RelationshipsTab.tsx",
        "                <td>{nameOf(r.from_character_id)}</td>",
        "                <td>{nameOf(r.to_character_id)}</td>",
        "src/pages/bible/RelationshipsTab.test.tsx",
    ),
    (
        "M5",
        "apps/web/src/pages/DeliveryPanel.tsx",
        "(verdict?.evidence_sources ?? []).filter((s) => !s.reachable)",
        "(verdict?.evidence_sources ?? [])",
        "src/pages/DeliveryPanel.test.tsx",
    ),
    (
        "M6",
        "apps/web/src/pages/bible/PlotTab.tsx",
        "await timelineApi.remove(id);",
        "await timelineApi.remove(timeline.find((x) => x.id === id)?.event_id ?? id);",
        "src/pages/bible/PlotTab.test.tsx",
    ),
    (
        "M7",
        "packages/domain/chapter/models.py",
        'model_config: ConfigDict = ConfigDict(extra="allow")',
        'model_config: ConfigDict = ConfigDict(extra="ignore")',
        "tests/api/test_volume_membership_contract.py",
    ),
    # ---- 2026-10-15 批次（四）：M8~M13 全部是**接线突变**。
    # 组件自身测得再好，页面不挂载就等于没有 UI——而「把挂载整行删掉」
    # 不会让组件的任何一个用例变红（同坑区「断言了结果 ≠ 判别了机制」：
    # 测试测不到它该守的那个东西）。这 6 条把「页面确实挂了它」钉成可执行判别。
    (
        "M8",
        "apps/web/src/pages/ProjectOverviewPage.tsx",
        "      <SimulationPanel projectId={projectId} />",
        "      {null}",
        "src/pages/ProjectOverviewPage.test.tsx",
    ),
    (
        "M9",
        "apps/web/src/pages/ChapterDetailPage.tsx",
        "      {chapter ? <ChapterJudgeSection chapterId={chapter.chapter_id} /> : null}",
        "      {null}",
        "src/pages/ChapterDetailPage.test.tsx",
    ),
    (
        "M10",
        "apps/web/src/pages/ChapterDetailPage.tsx",
        "        <ChapterRevisionNoteSection\n          chapter={chapter}\n"
        "          onSaved={() => chapterCall.reload()}\n        />",
        "        {null}",
        "src/pages/ChapterDetailPage.test.tsx",
    ),
    (
        "M11",
        "apps/web/src/pages/StoryBiblePage.tsx",
        "        {activeTab === 'arc' ? <ArcTab projectId={projectId} /> : null}",
        "        {null}",
        "src/pages/StoryBiblePage.test.tsx",
    ),
    (
        "M12",
        "apps/web/src/pages/StoryBiblePage.tsx",
        "        {activeTab === 'reveal' ? <RevealTab projectId={projectId} /> : null}",
        "        {null}",
        "src/pages/StoryBiblePage.test.tsx",
    ),
    # M13 撤掉 URL 契约登记：tab 标签还在（isBibleTab 白名单漏了它 ⇒ 会被重定向
    # 回默认页），断言 aria-selected 的用例必红。
    (
        "M13",
        "apps/web/src/pages/StoryBiblePage.tsx",
        "  { key: 'arc', label: '弧光' },",
        "",
        "src/pages/StoryBiblePage.test.tsx",
    ),
    # ---- 2026-10-15 批次（五）：M14~M19 守的是**后端 response_model 契约**。
    # 这批的失效方向是「静默丢字段」：response_model 会过滤未声明字段，少声明一个
    # 响应就少一个键，而功能测试常常照旧全绿。所以每条突变都对应一种丢法。
    # M14 少声明一个章节字段 ⇒ 响应少一个键（逐字相等断言必红）。
    (
        "M14",
        "packages/core/arc/models.py",
        "    volume_number: Optional[int]",
        "",
        "tests/api/test_arc_response_contract.py",
    ),
    # M15 把 exclude_none 翻成 False ⇒ 六个实体段被序列化成 null，diff 多出六个键。
    # ⚠️ 这一条在前端是**隐形**的（读法是 `?.` + `?? 0`），只有契约测试抓得到。
    #
    # ⚠️ 首版这条突变写成「在锚点上方插一行注释」，**等于什么都没改**（replacement 是
    # anchor 的超集），于是报出 SURVIVED——量具没量到目标却给出了「存活」结论。
    # 对策：突变必须**改变行为**，加注释不算。已改为翻转布尔值。
    (
        "M15",
        "packages/core/api/routers/simulation.py",
        "    response_model_exclude_none=True,\n)\ndef simulate(",
        "    response_model_exclude_none=False,\n)\ndef simulate(",
        "tests/api/test_simulation_response_contract.py",
    ),
    # M16 撤掉 judge 的 extra="allow" ⇒ score_avg/usage/dry_run 被裁掉
    # （数据躺在 judge_json 里，API 读不回来，零报错）。
    (
        "M16",
        "packages/core/quality/models.py",
        '''    model_config = ConfigDict(extra="allow")

    pacing: int
    style: int
    logic: int
    dialogue: int
    verdict: Optional[str] = None
    top_issues: Optional[list[str]] = None''',
        """    pacing: int
    style: int
    logic: int
    dialogue: int
    verdict: Optional[str] = None
    top_issues: Optional[list[str]] = None""",
        "tests/api/test_reveal_and_judge_response_contract.py",
    ),
    # M17 撤掉 reveal PATCH 的 extra="forbid" ⇒ 未知字段回到「静默忽略」，
    # 422 那条用例必红。
    (
        "M17",
        "packages/domain/knowledge/models.py",
        '''    model_config = ConfigDict(extra="forbid")

    status: RevealStatusLiteral | None = None''',
        """    status: RevealStatusLiteral | None = None""",
        "tests/api/test_reveal_and_judge_response_contract.py",
    ),
    # M18 出参少一个键 ⇒ 与 dataclass to_dict() 不再同形（两份模型分叉的守门）。
    (
        "M18",
        "packages/domain/knowledge/models.py",
        "    audience: str = Field(\n"
        "        description=\"`reader` / `character:<id>` / 逗号混写；DDL default 为 'reader'\",\n"
        "    )\n"
        "    status: RevealStatusLiteral",
        "    status: RevealStatusLiteral",
        "tests/api/test_reveal_and_judge_response_contract.py",
    ),
    # M19 整条 response_model 撤掉 ⇒ OpenAPI 里没有 ArcView schema。
    (
        "M19",
        "packages/core/api/routers/arc.py",
        '@router.get("/projects/{project_id}/arc", response_model=ArcView)',
        '@router.get("/projects/{project_id}/arc")',
        "tests/api/test_arc_response_contract.py",
    ),
    # M20 出参少一个键 ⇒ 硬编码键集断言必红（judge 的 report_id/chapter_id/judge）。
    (
        "M20",
        "packages/core/quality/models.py",
        """    report_id: str
    chapter_id: str
    # router 恒写这个键（`row.get("judge_json")`，无分时为 None）⇒ 必填可空。
    judge: Optional[QualityJudgeRecord]""",
        """    report_id: str
    chapter_id: str""",
        "tests/api/test_reveal_and_judge_response_contract.py",
    ),
    # M21 撤掉 ArcView 的 extra="allow" ⇒ service 新增顶层键时被静默裁掉。
    (
        "M21",
        "packages/core/arc/models.py",
        '''    model_config = ConfigDict(extra="allow")

    project_id: str''',
        """    project_id: str""",
        "tests/api/test_arc_response_contract.py",
    ),
]


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_web(test_file: str, root: pathlib.Path | None = None) -> tuple[bool, str]:
    base = (root or ROOT).resolve()
    web = base / "apps" / "web"
    vitest = web / "node_modules" / ".bin" / ("vitest.cmd" if sys.platform == "win32" else "vitest")
    code, out = _run([str(vitest), "run", test_file], web)
    _assert_tests_ran(out, "vitest", code)
    return code == 0, out


#: pytest 子进程的 basetemp 根目录（**仓库外** ⇒ 不污染工作树，也不撞「某个既有
#: 测试假设了仓库内无临时目录」这类既有约定）。
#: ⚠️ pytest 内部是 ``Path.mkdir(mode=0o700)``（**不带 parents**）⇒ 父目录必须先建好，
#: 否则表现为 ``FileNotFoundError``（看起来像用例挂了，其实是用例压根没跑起来）。
_BASETEMP_ROOT = pathlib.Path(tempfile.gettempdir()) / "novelos-mutbasetemp"
_basetemp_counter = itertools.count()


def _basetemp_for(mid: str) -> pathlib.Path:
    """给一次 pytest 子进程分配**唯一**的 basetemp（并发 / 连续跑都不撞）。

    ⚠️ **为什么必须给**（2026-10-23 实测）：本机 ``%LOCALAPPDATA%\\Temp\\pytest-of-<user>``
    一旦被创建就**连 owner 都拒绝访问**（``PermissionError: [WinError 5]``）⇒ 用到
    ``tmp_path`` 的用例在**准备阶段**就 ERROR ⇒ 子进程退出码 1 ⇒ 与「用例红」**同形**
    ⇒ 量具失真（判定口径见 :func:`_assert_tests_ran`）。
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", mid)[:60]
    _BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _BASETEMP_ROOT / f"{safe}-{os.getpid()}-{next(_basetemp_counter)}"


def run_py(test_file: str, mid: str = "", root: pathlib.Path | None = None) -> tuple[bool, str]:
    """跑一次 pytest 子进程（**自带唯一 basetemp**）⇒ 返回 ``(是否全绿, 输出)``。

    ⚠️ 解释器与 cwd 都取**目标副本**（``root``）⇒ 突变与用例都在副本内发生，
    活树零接触（BUG-28 的根本解：变异只在仓外副本跑）。
    """
    base = (root or ROOT).resolve()
    py = base / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = pathlib.Path(PY)
    basetemp = _basetemp_for(mid or test_file)
    try:
        code, out = _run(
            [str(py), "-m", "pytest", test_file, "-q", f"--basetemp={basetemp}"], base
        )
        _assert_tests_ran(out, "pytest", code)
        return code == 0, out
    finally:
        # 子进程已退出 ⇒ 句柄已释放；``ignore_errors=True`` 保证 Windows 上残留句柄
        # **不会**把清理失败升级成「本条突变无效」（清理不是判别点）。
        shutil.rmtree(basetemp, ignore_errors=True)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def summary_line(out: str) -> str:
    """第一条能说明「红了哪条」的**非空**行（去 ANSI）。

    ⚠️ 必须喂**完整**输出。2026-10-15 复核实测：本函数原先读到的是
    ``out[-2500:]`` 截断窗，而 M3 有 3 条失败、vitest 会打出整段 DOM 调试输出，
    把顶部的 ``❯ file.test.tsx (13 tests | 3 failed)`` 顶出了窗口
    ⇒ 摘要退化成空串。

    **空摘要不是无害的**：坑区那条纪律要求复核者「确认红的是预期那条，
    而不是语法错误导致的收集失败」，而摘要为空时这一步**无法执行**——
    量具在最需要被复核的场合（失败输出长）恰好交白卷。
    """
    lines = [_ANSI.sub("", ln).strip() for ln in out.splitlines()]
    # pytest 的 short summary 行以 "FAILED <test_id>" 开头，是**唯一**带用例 id 的
    # 失败行——优先于 `===== FAILURES =====` 横幅（横幅只说「有失败」，不说哪条）。
    for ln in lines:
        if ln.startswith("FAILED "):
            return ln[:150]
    for ln in lines:
        if ln and ("FAIL" in ln or "failed" in ln):
            return ln[:150]
    for ln in reversed(lines):  # 兜底：取最后一条非空行
        if ln:
            return ln[:150]
    return "(输出为空，无法提取摘要——本条须手工复核)"


def main() -> int:
    # ⚠️ stdout 重配必须在**函数内**（模块级会让 import 即崩，见模块顶部注释）。
    _reconfigure_stdio()

    # ⚠️ BUG-28：参数**必须显式解析**。argparse 的三条行为正好覆盖硬约束：
    #   --help / -h  ⇒ 打印用法后 exit 0（不碰文件、不起子进程）
    #   无参           ⇒ 打印用法（缺 --root/--confirm）后 exit 2（**不会**跑突变）
    #   未知参数       ⇒ 打印用法 + 错误后 exit 2（**不会**跑突变）
    #
    # ⚠️ PM 07:33 返修（判据②「缺显式确认参数」）：在 `--root` 必填之外，
    # **再加一道必填的显式确认参数** `--confirm` ⇒ 构成**第三层闸门**：
    #   ① `--root` 必填      ⇒ 目标根必须显式给
    #   ② `--confirm` 必填   ⇒ 必须**显式确认**「我知道这会改副本源码」
    #   ③ 活树硬拒           ⇒ 目标根指向软件仓活树一律拒绝运行
    # ⇒ 缺 `--confirm` ⇒ argparse 打印用法（含所需参数）后 **exit 2**、
    #   **不触碰任何文件、不起任何子进程**（在进入突变循环之前就结束）。
    ap = argparse.ArgumentParser(
        prog="mutation_harness.py",
        description=(
            "突变验证骨架：改一处 → 跑定向用例 → 记结果 → 按 SHA256 还原。\n"
            "目标根必须显式给 --root（仓外副本）；指向软件仓活树一律拒绝运行。\n"
            "还必须显式给 --confirm 确认「本次会改副本源码」——缺任一参数都只打印用法并退出。"
        ),
        epilog=(
            "示例（先复制到仓外副本，再对副本跑）：\n"
            "    xcopy /E /I D:\\zcodeproject\\NovelOS D:\\zcodeproject\\.osdev_mut_<ts>\n"
            "    python scripts/mutation_harness.py --root D:\\zcodeproject\\.osdev_mut_<ts> --confirm"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--root",
        required=True,
        help="目标**仓外副本**根目录（指向软件仓活树 D:\\zcodeproject\\NovelOS 一律拒绝）",
    )
    ap.add_argument(
        "--confirm",
        required=True,
        nargs="?",
        const="1",
        metavar="CONFIRM",
        help=(
            "显式确认开关：必须给（`--confirm` 或 `--confirm=<任意值>`）。\n"
            "缺它 ⇒ 拒绝执行（打印用法后 exit 2），**不触碰任何文件、不起任何子进程**。"
        ),
    )
    args = ap.parse_args()
    # 显式确认参数非空校验（argparse 只管「给了没有」；给了空串同样不算确认）
    if not str(args.confirm).strip():
        raise SystemExit(
            "[ABORT] --confirm 不得为空：必须显式确认「本次会改副本源码」。\n"
            "       正确用法：--confirm（任意非空值）"
        )
    # ── A-1（Chief 2026-10-25 批文 · PM-196-A1-code）─────────────────────
    # 缺陷：``--root`` 空串/纯空白**未被拒** ⇒ ``pathlib.Path("")`` ＝ ``Path(".")``
    #       ⇒ ``resolve()`` ＝ **当前工作目录（CWD）** ⇒ 目标根＝CWD（未必是仓外副本）
    #       ⇒ 会在**那棵树**上做突变改写（伤数据，必须由代码拒）。
    # 同源形态：``--root=.`` 经 ``Path(".").resolve()`` 同样落到 CWD ⇒ 一并拒。
    if not str(args.root).strip():
        raise SystemExit(
            "[ABORT] --root 不得为空或纯空白：空值会被解析成当前工作目录（CWD），"
            "可能对任意树做突变改写。\n"
            "       正确用法：--root <仓外副本根>（必须是显式的仓外副本目录）"
        )
    if _same_dir(pathlib.Path(args.root).expanduser().resolve(), pathlib.Path.cwd().resolve()):
        raise SystemExit(
            "[ABORT] 目标根不得等于当前工作目录（CWD）："
            f"{pathlib.Path(args.root).expanduser().resolve()}\n"
            "       目标根必须显式给**仓外副本根**；等于 CWD 会让突变改写落到当前树。\n"
            "       正确用法：--root <仓外副本根> --confirm"
        )
    target_root = assert_target_root_allowed(pathlib.Path(args.root))

    survived: list[str] = []
    aborted: list[str] = []
    print("=" * 78)
    print(f"目标副本根: {target_root}")
    for mid, rel, anchor, replacement, test_file in MUTATIONS:
        path = (target_root / rel).resolve()
        # ⚠️ 二次防线：目标文件必须在副本根内（防 ``..`` / junction 越界）。
        if not path.is_relative_to(target_root):
            print(f"[ABORT] {mid}: 目标文件不在副本根内：{rel}")
            aborted.append(mid)
            continue
        if not path.is_file():
            print(f"[ABORT] {mid}: 副本内找不到目标文件：{rel}")
            aborted.append(mid)
            continue
        original = path.read_text(encoding="utf-8")
        before = sha(path)

        hits = original.count(anchor)
        if hits != 1:
            # 锚点未命中 = 脚本自身缺陷，不是「跳过」
            print(f"[ABORT] {mid}: 锚点命中 {hits} 次（要求恰好 1），文件={rel}")
            aborted.append(mid)
            continue

        bak = path.with_suffix(path.suffix + ".mutbak")
        shutil.copy2(path, bak)

        try:
            path.write_text(original.replace(anchor, replacement), encoding="utf-8")
            if sha(path) == before:
                print(f"[ABORT] {mid}: 写入后 SHA256 未变——变异没生效")
                aborted.append(mid)
                continue

            try:
                ok, out = (
                    run_py(test_file, mid, target_root)
                    if test_file.startswith("tests/")
                    else run_web(test_file, target_root)
                )
            except LaunchFailure as exc:
                # 启动失败 ≠ 用例红。记成「本次无效」并中止该条，
                # 绝不能落到 survived/caught 两个桶里的任何一个。
                print(f"[INVALID]  {mid}: {exc}")
                aborted.append(mid)
                continue
            if ok:
                print(f"[SURVIVED] {mid}: 突变后用例仍全绿 ⇒ 该测试测不到这个机制")
                survived.append(mid)
            else:
                print(f"[CAUGHT]   {mid}: {summary_line(out)}")
        finally:
            shutil.copy2(bak, path)
            bak.unlink(missing_ok=True)
            after = sha(path)
            status = "还原校验 OK" if after == before else "!!! 还原校验失败 !!!"
            print(f"           还原: {status} ({after[:16]})")

    print("=" * 78)
    print(f"突变存活: {survived or '无'}")
    print(f"脚本中止: {aborted or '无'}")
    return 1 if (survived or aborted) else 0


if __name__ == "__main__":
    sys.exit(main())
