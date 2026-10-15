"""突变验证骨架（AGENTS §三.2：新测试必须做突变验证）。

逐条：改一处 → 跑**定向**用例 → 记结果 → 按 SHA256 还原并校验。

用法
----
改 ``MUTATIONS`` 表（编号 / 目标文件 / 锚点 / 变异 / 定向用例），然后::

    .venv/Scripts/python.exe scripts/mutation_harness.py

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

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys

# ⚠️ 本机 Windows stdout 默认 cp936（坑区「Windows 默认 cp936 吞中文」家族）。
# **两侧都要钉**：本进程 stdout 重配 + 子进程 env——只给一边照样乱码。
# 放在 import 之后：reconfigure 是运行期动作，不是导入期。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


class LaunchFailure(RuntimeError):
    """测试命令**没能启动**——与「用例红」是两回事，必须分开报。"""


def _run(cmd, cwd) -> tuple[bool, str]:
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
    return r.returncode == 0, (r.stdout + r.stderr)


def _assert_tests_ran(out: str, runner: str) -> None:
    """必须有「跑过了」的证据，否则一律按启动/收集失败处理。"""
    if runner == "vitest":
        markers = ("Test Files", "Tests  ")
    else:
        markers = (" passed", " failed", "no tests ran")
    if not any(m in out for m in markers):
        raise LaunchFailure(f"输出里没有任何测试执行痕迹（可能被上游杀掉）：{out[-400:]}")

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
]


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_web(test_file: str) -> tuple[bool, str]:
    ok, out = _run([str(VITEST), "run", test_file], WEB)
    _assert_tests_ran(out, "vitest")
    return ok, out


def run_py(test_file: str) -> tuple[bool, str]:
    ok, out = _run([PY, "-m", "pytest", test_file, "-q"], ROOT)
    _assert_tests_ran(out, "pytest")
    return ok, out


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
    survived: list[str] = []
    aborted: list[str] = []
    print("=" * 78)
    for mid, rel, anchor, replacement, test_file in MUTATIONS:
        path = pathlib.Path(rel)
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
                    run_py(test_file) if test_file.startswith("tests/") else run_web(test_file)
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
