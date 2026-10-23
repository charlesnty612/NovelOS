"""``scripts/mutation_probe.py`` 的判别用例（2026-10-22 工程卫生批次）。

这个探针是**危险工具**（它临时改工作区）⇒ 它的行为必须被钉住，否则一次手滑
就能把改过的源码留在工作区（批次十七已为此付过一次代价：突变态被 ``git add -A``
扫进提交并推送）。

三条判别，对应三类失效：

1. **不还原** ⇒ 用例 1 必红（源文件停在改动态）；
2. **不拦并发** ⇒ 用例 3 必红（执行器在飞时也敢跑）；
3. **不校验锚点唯一** ⇒ 用例 2 必红（在锚点不存在时仍去替换，破坏的不是目标处）。

⚠️ 用例全部在**临时目录**里造一个「微型仓库」跑（``--root`` 参数）——
**绝不在真仓库上验证「它会改文件」**（那正是本工具最危险的副作用）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mutation_probe.py"

TARGET_BODY = "VALUE = 1\n"
#: 故意用**字节通道**造的 LF 文件（``write_text`` 在 Windows 上会写成 CRLF ⇒ 测不出漂移）。
TARGET_BODY_LF = b"VALUE = 1\n"
TEST_BODY = (
    "from target import VALUE\n"
    "\n"
    "\n"
    "def test_value():\n"
    "    assert VALUE == 1\n"
)


def _mini_repo(tmp_path: Path) -> Path:
    """造一个微型仓库：一个待改源文件 + 一个会因它变动的 pytest 用例。"""
    (tmp_path / "target.py").write_text(TARGET_BODY, encoding="utf-8")
    (tmp_path / "test_probe_target.py").write_text(TEST_BODY, encoding="utf-8")
    return tmp_path


def _mini_repo_lf(tmp_path: Path) -> Path:
    """造一个「**LF 行尾**」的微型仓库（其余同 :func:`_mini_repo`）。

    ⚠️ 必须用 ``write_bytes``：``write_text`` 在 Windows 上会把 LF 翻成 CRLF
    ⇒ 那样造出来的文件**测不出**行尾漂移。
    """
    (tmp_path / "target.py").write_bytes(TARGET_BODY_LF)
    (tmp_path / "test_probe_target.py").write_text(TEST_BODY, encoding="utf-8")
    return tmp_path


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_probe_reports_the_failing_case_and_restores_the_file(tmp_path):
    """改坏源码 ⇒ 报出红了哪条用例 ⇒ 源文件逐字节还原。"""
    root = _mini_repo(tmp_path)
    proc = _run(
        root,
        "--target",
        "target.py",
        "--anchor",
        "VALUE = 1",
        "--replacement",
        "VALUE = 2",
        "--test",
        "test_probe_target.py",
        "--runner",
        "pytest",
    )
    out = proc.stdout + proc.stderr
    # ① 报出了**失败用例名**（不只是「有失败」）
    assert "test_probe_target.py::test_value" in out, out
    # ② 源文件被还原成原样
    assert (root / "target.py").read_text(encoding="utf-8") == TARGET_BODY
    assert "还原校验" not in out or "== 前值" in out  # 打印了 SHA 校验行
    # ③ 探针自身成功退出（它成功完成了「改→跑→报→还原」这一循环）
    assert proc.returncode == 0, out


def test_probe_refuses_when_anchor_is_not_unique(tmp_path):
    """锚点命中 0 次 ⇒ 拒绝施加改动（源文件一个字节都不能变）。

    ⚠️ **必须断言「机制」而不是只断言「结果」**：只断言「退出码非 0 + 文件没变」
    是**测不到**锚点检查的——把该检查整个撤掉后，后面那句「写入后 SHA 未变 ⇒
    变异没生效」会**兜住**同样的结果 ⇒ 用例照样全绿（突变 ``MP-3`` 实测存活）。
    ⇒ 故这里断言**锚点检查专属的那句诊断**（别的检查不会说这句话）。
    """
    root = _mini_repo(tmp_path)
    before = (root / "target.py").read_bytes()
    proc = _run(
        root,
        "--target",
        "target.py",
        "--anchor",
        "NO_SUCH_ANCHOR",
        "--replacement",
        "VALUE = 2",
        "--test",
        "test_probe_target.py",
        "--runner",
        "pytest",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    # ① 锚点检查**自己**说了话（不是别的检查兜住的）
    assert "锚点命中次数不是 1" in out, out
    # ② 且没有走到「施加改动」那一步
    assert "写入后 SHA256 未变" not in out, out
    assert (root / "target.py").read_bytes() == before


def test_probe_refuses_to_run_while_mutation_runner_is_in_flight(tmp_path):
    """工作区有 ``*.mutbak``（执行器在飞的信号）⇒ 拒绝运行，且不施加改动。"""
    root = _mini_repo(tmp_path)
    (root / "leftover.mutbak").write_text("x", encoding="utf-8")
    before = (root / "target.py").read_bytes()
    proc = _run(
        root,
        "--target",
        "target.py",
        "--anchor",
        "VALUE = 1",
        "--replacement",
        "VALUE = 2",
        "--test",
        "test_probe_target.py",
        "--runner",
        "pytest",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "mutbak" in out
    assert (root / "target.py").read_bytes() == before


def test_probe_reports_zero_failures_when_the_change_is_harmless(tmp_path):
    """改动不破坏断言 ⇒ 测试仍绿 ⇒ **不得**报出失败用例（区分「红」与「绿」）。"""
    root = _mini_repo(tmp_path)
    proc = _run(
        root,
        "--target",
        "target.py",
        "--anchor",
        "VALUE = 1",
        "--replacement",
        "VALUE = 1  # 注释",
        "--test",
        "test_probe_target.py",
        "--runner",
        "pytest",
    )
    out = proc.stdout + proc.stderr
    assert "共 0 条" in out, out
    assert proc.returncode == 1  # 「没抓到红」= 探针的失败语义
    assert (root / "target.py").read_text(encoding="utf-8") == TARGET_BODY


def test_probe_restores_the_file_byte_for_byte(tmp_path):
    """还原必须**字节级**：``read_bytes()`` 逐位一致（**含行尾**）。

    ⚠️ 既有断言用 ``read_text`` 比较 ⇒ universal-newline 归一 ⇒ **行尾漂移看不见**。
    而旧实现的还原走 ``read_text`` / ``write_text`` 文本通道 ⇒ Windows 上
    ``write_text`` 把 LF 翻成 CRLF ⇒ ①「[还原] SHA == 前值」报 False（还原校验形同虚设），
    ② 被测文件的行尾被**静默污染**（git 归一后连 git 都看不见）。

    ⇒ 本用例故意造一个 **LF** 文件（``write_bytes``，绕开 ``write_text`` 的 CRLF 翻译）
    并把断言**钉在字节上** ⇒ 谁再把字节通道改回文本通道，本用例必红。
    """
    root = _mini_repo_lf(tmp_path)
    proc = _run(
        root,
        "--target",
        "target.py",
        "--anchor",
        "VALUE = 1",
        "--replacement",
        "VALUE = 2",
        "--test",
        "test_probe_target.py",
        "--runner",
        "pytest",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[还原] SHA" in out and "? True" in out, out
    # ⚠️ 钉在**字节**上（``read_text`` 会把 CRLF 归一成 LF ⇒ 看不见漂移）
    assert (root / "target.py").read_bytes() == TARGET_BODY_LF
