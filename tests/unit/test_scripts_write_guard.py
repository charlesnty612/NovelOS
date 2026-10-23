"""脚本落盘守卫 + 痕迹仓落点边界的回归测试（2026-10-23 批次三十 / 对抗审查 B1·B2 + 台账 D-19）。

缺陷形状（两条同族）：

1. **B1**：``scripts/eval_regression.py`` / ``scripts/m1_report.py`` 的痕迹仓根解析
   **最后一档回退就是软件仓自身** ⇒ 在**新克隆 / CI / 换机**（拿不到痕迹仓）上跑一次
   ``--write-docs``，就把含真实 ``prj_``/``ch_`` id 与正文片段的报告写回软件仓
   （``.gitignore`` 挡得住 ``git add``，**挡不住落盘**）。
2. **B2**：``eval_regression.py`` 的默认 ``--baseline`` 仍指向软件仓，而那个路径下只有
   ``.gitkeep``（基线文件从未被跟踪）⇒ **每次运行都走「无基线 → 建基线 → PASS」**
   ⇒ 结构漂移闸门退化为「与上一次自产基线比对」，**首次出现的漂移被静默吸收**；
   同时把新基线写回软件仓里**无 gitignore 规则**的路径。
3. **D-19**：台账登记项「脚本『拒写仓库根下非 data/tmp 路径』」的另一半**未实现**
   ⇒ 本文件同时钉住可复用守卫 ``scripts/guard_repo_write_path.py``。

本文件钉住：

- 守卫的**真实判定**：仓根下的 ``foo.md`` 被拒且**不产生文件**；``data/``、``tmp/``
  与仓外路径放行；
- 痕迹仓根解析**绝不回落到软件仓**（找不到 ⇒ ``root is None``）；
- 隔离目录（同级无 ``NovelOS-Artifacts``）里跑两个脚本 ⇒ **拒绝写** + stderr 说明
  原因 + 退出码非 0 + **隔离目录内零新增文件**；
- 基线**落在痕迹仓根下**（不是软件仓），且软件仓内零新增文件；
- 显式逃生口 ``--allow-software-repo-out`` 仍然可用（默认关）。

⚠️ 隔离测试用**真实 subprocess**跑脚本（不是 import 后调函数）：拒绝/退出码/stderr
必须来自**真实的 CLI 行为**。子进程只读本仓的 ``tests/evals/golden``，写落点全在
``tmp_path`` 内；**零真实 LLM 调用、零内容仓接触、零 git 写操作**。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
GOLDEN_DIR = REPO_ROOT / "tests" / "evals" / "golden"
EVAL_SCRIPT = SCRIPTS_DIR / "eval_regression.py"
M1_SCRIPT = SCRIPTS_DIR / "m1_report.py"
M2_JUDGE_SCRIPT = SCRIPTS_DIR / "m2_judge.py"
REAL_LLM_SCRIPT = SCRIPTS_DIR / "real_llm_e2e.py"
GUARD_SCRIPT = SCRIPTS_DIR / "guard_repo_write_path.py"
BACKUP_SCRIPT = SCRIPTS_DIR / "backup_db.py"
DB_MAINT_SCRIPT = SCRIPTS_DIR / "db_maintenance.py"
HBS_SCRIPT = SCRIPTS_DIR / "human_baseline_synthetic.py"
ISR_SCRIPT = SCRIPTS_DIR / "inspect_stalled_runs.py"

EXIT_WRITE_PATH_REJECTED = 2
EXIT_ARTIFACTS_UNAVAILABLE = 3


@pytest.fixture()
def guard():
    """导入被测守卫模块（``scripts`` 是命名空间包，仓库根在 ``pythonpath`` 上）。"""
    return importlib.import_module("scripts.guard_repo_write_path")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> set[str]:
    """目录内**全部文件与目录**的相对路径集合（用于「零新增产物」断言）。

    ⚠️ **目录也要收**（F-8）：只收文件时，「只建了一个空目录」这种残留**看不见**
    ⇒ 「连目录都不该被建出来」这条性质全仓无断言。目录用 ``dir/`` 前缀与文件名
    区分（同名文件与同名目录不会混）。

    排除 ``__pycache__``/``*.pyc``：那是解释器导入字节码的产物，**不是**脚本产物
    （要判的是「脚本有没有把东西写进仓里」）。
    """
    if not root.exists():
        return set()
    out: set[str] = set()
    for p in root.rglob("*"):
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = str(p.relative_to(root))
        out.add(rel if p.is_file() else f"dir/{rel}")
    return out


@pytest.fixture(autouse=True)
def _sweep_guard_probe_leftovers():
    """清掉**历史遗留**的守卫探针产物（``REPO_ROOT/docs/_guard_probe_*``）。

    ⚠️ 为什么需要（F-7）：探针产物曾经落在**受版本跟踪的** ``docs/`` 下且**没有**
    清理 fixture ⇒ 一次中断的变异实验就在仓里留下**明文 DB 备份**，紧接着的
    还原后重跑变成「前提：目标不得已存在」失败 ⇒ **掩盖真实断言**。
    现在探针落点已迁到 ``tmp_path``（见 :func:`_fake_repo`），本 fixture 是**兜底**：
    清掉任何来源的残留（旧版本测试 / 变异执行器中断 / 手工排查）。
    """
    yield
    docs = REPO_ROOT / "docs"
    if not docs.is_dir():
        return
    for p in docs.glob("_guard_probe_*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _run_script(
    script: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    unset_env: list[str] | None = None,
    pythonpath: str | None = None,
) -> subprocess.CompletedProcess:
    """跑脚本 CLI（真实 subprocess）⇒ 返回 ``CompletedProcess``。

    环境：``PYTHONPATH`` 指向本仓（子进程要 import ``tests.evals.*``），
    且**清掉** ``NOVELOS_ARTIFACTS_DIR``（否则本机的痕迹仓会掩盖「找不到痕迹仓」这条路径）。
    ``unset_env`` 额外清掉的变量（如 ``MINIMAX_API_KEY``，保证子进程不可能发起真实调用）。
    ``pythonpath`` 可覆盖 ``PYTHONPATH``（见 :func:`_fake_pythonpath`）。
    """
    env = dict(os.environ)
    env.pop("NOVELOS_ARTIFACTS_DIR", None)
    for name in unset_env or []:
        env.pop(name, None)
    env["PYTHONPATH"] = pythonpath or str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )


def _fake_repo(tmp_path: Path, names: list[str]) -> Path:
    """造一个「假软件仓」``<tmp>/fake_repo/NovelOS``（脚本 + 守卫都在里面）。

    ⚠️ 为什么不用真仓库当探针落点（F-7）：守卫的 ``REPO_ROOT`` 由 ``__file__``
    上溯两级得到 ⇒ 脚本与守卫**必须同处**这个假仓，守卫才会把「仓内路径」判成
    非法位置。探针产物因此落在 ``tmp_path`` 里，**不再落进真仓库的 ``docs/``**
    （那里曾经留下过明文 DB 备份）。
    """
    iso = tmp_path / "fake_repo" / "NovelOS"
    (iso / "scripts").mkdir(parents=True)
    for name in names:
        shutil.copy2(SCRIPTS_DIR / name, iso / "scripts" / name)
    return iso


def _fake_pythonpath(iso: Path) -> str:
    """子进程的 ``PYTHONPATH``：**假仓优先**、真仓兜底。

    ⚠️ 顺序很关键：``scripts`` 是**命名空间包** ⇒ 多个 ``sys.path`` 位置会被合并
    成多个 portion ⇒ 真仓在前时 ``scripts.guard_repo_write_path`` 会解析到**真仓**
    那份（``REPO_ROOT`` = 真仓）⇒ 假仓里的探针路径根本不会被判成「仓内非法」。
    假仓在前 ⇒ 守卫取假仓那份（``REPO_ROOT`` = 假仓）⇒ 判定落在假仓上。
    真仓留在后面 ⇒ ``packages.*``（hbs / db_maintenance 需要）照常可导入。
    """
    return str(iso) + os.pathsep + str(REPO_ROOT)



def _isolated_copy(tmp_path: Path, names: list[str]) -> Path:
    """把脚本复制到隔离目录 ``<iso>/scripts/``（模拟「新克隆、无痕迹仓」的机器）。

    隔离目录的**同级**故意不放 ``NovelOS-Artifacts`` ⇒ 痕迹仓探测必然失败。
    """
    iso = tmp_path / "isolated" / "NovelOS"
    (iso / "scripts").mkdir(parents=True)
    for name in names:
        shutil.copy2(SCRIPTS_DIR / name, iso / "scripts" / name)
    assert not (iso.parent / "NovelOS-Artifacts").exists(), "隔离前提：同级不得有痕迹仓"
    assert not (iso / "NovelOS-Artifacts").exists()
    return iso


# ---------------------------------------------------------------------------
# D-19：写路径守卫（真实判定）
# ---------------------------------------------------------------------------


def test_guard_rejects_file_at_repo_root_and_writes_nothing(guard) -> None:
    """仓库根下的 ``foo.md`` 必须被拒，且**不得产生文件**。"""
    target = REPO_ROOT / "foo.md"
    assert not target.exists(), "前提：目标不得已存在"

    with pytest.raises(guard.RepoWritePathError) as excinfo:
        guard.check_write_path(target)

    msg = str(excinfo.value)
    assert "foo.md" in msg, msg
    assert "data/" in msg and "tmp/" in msg, msg
    assert not target.exists(), "守卫必须拒绝落盘，不得留下文件"


def test_guard_rejects_repo_docs_subtree(guard) -> None:
    """仓库内的 ``docs/`` 子树同样被拒（D-19：脚本不得把产物写回软件仓）。"""
    with pytest.raises(guard.RepoWritePathError) as excinfo:
        guard.check_write_path(REPO_ROOT / "docs" / "evaluation" / "runs" / "x.json")
    assert "docs" in str(excinfo.value)


@pytest.mark.parametrize("rel", ["data/report.json", "tmp/probe.md"])
def test_guard_allows_data_and_tmp_subtrees(guard, rel: str) -> None:
    """``data/``、``tmp/`` 是软件仓内**允许**的两个子树。"""
    resolved = guard.check_write_path(REPO_ROOT / rel)
    assert resolved == (REPO_ROOT / rel).resolve()


def test_guard_allows_path_outside_repo(guard, tmp_path: Path) -> None:
    """仓外路径（系统临时目录、痕迹仓）一律放行。"""
    target = tmp_path / "out.md"
    resolved = guard.check_write_path(target)
    assert resolved == target.resolve()


def test_guard_allow_repo_root_is_explicit_opt_in(guard) -> None:
    """逃生口 ``allow_repo_root=True`` 才放行仓内非允许子树（默认关）。"""
    target = REPO_ROOT / "docs" / "escape.md"
    with pytest.raises(guard.RepoWritePathError):
        guard.check_write_path(target)
    resolved = guard.check_write_path(target, allow_repo_root=True)
    assert resolved == target.resolve()


def test_guard_is_inside_repo_helper(guard, tmp_path: Path) -> None:
    """``is_inside_repo`` 的真实判定（仓内 True / 仓外 False）。"""
    assert guard.is_inside_repo(REPO_ROOT / "docs" / "x.md") is True
    assert guard.is_inside_repo(tmp_path / "x.md") is False


# ---------------------------------------------------------------------------
# 守卫的**绕过面**（2026-10-23 对抗审查实证 + Lead 独立复算）
# ---------------------------------------------------------------------------
#
# ⚠️ 这组用例的判据是「**守卫必须拒绝**」+「**该写法在 Windows 上真的会落进仓内**」
# 两条**同时**成立 ⇒ 撤掉规范化（退回旧的 ``relative_to`` 判定）时**必红**。


def test_guard_rejects_extended_length_prefix_path_inside_repo(
    guard, tmp_path: Path, monkeypatch
) -> None:
    """``\\\\?\\`` 扩展长度前缀**不得**成为绕过。

    实证链：
    - Windows **认** ``\\\\?\\C:\\...\\docs\\x.db`` 这个写法 ⇒ 文件**真的**落在仓内；
    - 而 ``Path.resolve()`` **不剥扩展长度前缀** ⇒ 旧判定（``relative_to``）因前缀
      不同而抛 ``ValueError`` ⇒ 被判成「仓外」⇒ **守卫放行**（= 绕过）。
    ⇒ 守卫必须按**规范化后**的路径判定。
    """
    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "docs").mkdir(parents=True)
    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)

    raw = "\\\\?\\" + str(fake_repo / "docs" / "probe.txt")

    # 前置证据（防恒真断言）：这个写法在 Windows 上**真的**落进仓内
    Path(raw).parent.mkdir(parents=True, exist_ok=True)
    Path(raw).write_text("x", encoding="utf-8")
    assert (fake_repo / "docs" / "probe.txt").exists(), (
        "前提失效：该路径写法在本机并没有落进仓内 ⇒ 本用例会变成恒真断言"
    )

    with pytest.raises(guard.RepoWritePathError):
        guard.check_write_path(raw)

    assert guard.is_inside_repo(raw) is True, "仓内外判定也必须按规范化口径"


def test_guard_rejects_trailing_space_component_inside_repo(
    guard, tmp_path: Path, monkeypatch
) -> None:
    """路径分量带**尾随空格**时，守卫必须**从严**（Win32 打开文件时会裁掉尾随空格）。

    ⚠️ 实测：在本机用这种路径**写不进文件**（``FileNotFoundError``）⇒ 危害为零；
    但守卫仍须从严判定 ⇒ 免得哪天换一个**会裁尾随空格**的写入通道就变成真绕过。
    """
    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "docs").mkdir(parents=True)
    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)

    with pytest.raises(guard.RepoWritePathError):
        guard.check_write_path(str(fake_repo / "docs " / "probe.txt"))


def test_guard_still_allows_outside_repo_with_odd_spellings(
    guard, tmp_path: Path, monkeypatch
) -> None:
    """规范化**不得**把仓外路径误判成仓内（过严同样是缺陷）。

    覆盖：仓外路径 + 尾随空格分量 / 扩展长度前缀 / 大小写差异 ⇒ 一律**放行**。
    """
    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "docs").mkdir(parents=True)
    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)

    outside = tmp_path / "outside"
    outside.mkdir()
    assert guard.check_write_path(outside / "a.txt") == (outside / "a.txt").resolve()
    assert guard.check_write_path(str(outside / "b " / "c.txt")).name == "c.txt"
    assert guard.check_write_path("\\\\?\\" + str(outside / "d.txt")).name == "d.txt"
    assert guard.check_write_path(str(fake_repo.parent / "fake_repo2" / "e.txt")).name == "e.txt"


def _same_inode(a: Path | str, b: Path | str) -> bool:
    """两个路径是否指向**同一个底层位置**（``(st_dev, st_ino)``）。"""
    sa = os.stat(a)
    sb = os.stat(b)
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _admin_share_alias(repo_root: Path) -> str | None:
    """本机能否用 UNC 管理共享指到同一目录？不能 ⇒ ``None``。

    ⚠️ 管理共享的拼法：``D:\\x\\y`` ⇒ ``\\\\localhost\\D$\\x\\y``（**盘符的冒号换成 ``$``**，
    盘符本身要去掉）⇒ 直接拼 ``share + str(path)`` 会得到 ``\\\\localhost\\D$D:\\x`` 这种非法路径。
    """
    drive = repo_root.drive
    if not drive or len(drive) < 2:
        return None
    rest = repo_root.as_posix()[len(drive):].replace("/", "\\")
    for host in ("localhost", "127.0.0.1"):
        probe = f"\\\\{host}\\{drive[0]}$" + rest
        try:
            os.stat(probe)
        except OSError:
            continue
        return probe
    return None


def test_normalize_for_compare_preserves_unc_root(guard) -> None:
    """UNC 根（``\\\\server\\share``）必须**原样保留**（对抗审查 F-2 的回归看守）。

    ⚠️ 旧实现把它压成 ``\\.\\server\\share``（**换了一条路径**）⇒ 仓内 UNC 路径
    会被判成「仓外」⇒ 绕过。
    """
    assert guard.normalize_for_compare("\\\\server\\share\\docs\\x.json") == (
        "\\\\server\\share\\docs\\x.json"
    )


def test_guard_rejects_unc_admin_share_alias_of_repo(guard) -> None:
    """同一目录的**别名拼写**（UNC 管理共享）不得成为绕过。

    ``\\\\localhost\\D$\\<repo>\\docs\\x.db`` 与 ``D:\\<repo>\\docs\\x.db`` 是同一个
    目录的两种拼写 ⇒ 字符串比较（哪怕规范化后）判不出「在仓内」⇒ 守卫必须**按底层
    身份**（``(st_dev, st_ino)``）再核一次。

    ⚠️ 依赖本机有可用的管理共享 ⇒ 不可用时**跳过**（并说明），不假装验过。
    """
    alias_root = _admin_share_alias(REPO_ROOT)
    if alias_root is None:
        pytest.skip("本机没有可用的管理共享（\\\\localhost\\D$）⇒ 跳过「别名拼写」这条")
    # 前置证据：两种拼写指向**同一个**底层位置
    assert _same_inode(alias_root, REPO_ROOT), "前提：别名拼写必须与仓根同 inode"
    with pytest.raises(guard.RepoWritePathError):
        guard.check_write_path(alias_root + "\\docs" + "\\probe_alias.txt")


# ---------------------------------------------------------------------------
# B1：痕迹仓根解析**绝不回落到软件仓**
# ---------------------------------------------------------------------------


def test_resolve_artifacts_root_never_falls_back_to_software_repo(guard, tmp_path: Path) -> None:
    """找不到痕迹仓 ⇒ ``root is None``（**不是**软件仓）。"""
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    assert not (fake_repo.parent / "NovelOS-Artifacts").exists()

    res = guard.resolve_artifacts_root(repo_root=fake_repo, env={})

    assert res.root is None, "找不到痕迹仓时不得回落到软件仓"
    assert res.source == "none"
    assert res.root != fake_repo
    assert guard.ARTIFACTS_ENV_VAR in res.detail, "必须说明找过哪些地方 / 怎么修"


def test_resolve_artifacts_root_precedence(guard, tmp_path: Path) -> None:
    """解析顺序：``--artifacts-dir`` > 环境变量 > 同级目录。"""
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    sibling = fake_repo.parent / "NovelOS-Artifacts"
    sibling.mkdir()

    cli = guard.resolve_artifacts_root(tmp_path / "cli", repo_root=fake_repo, env={})
    assert cli.source == "cli" and cli.root == (tmp_path / "cli").resolve()

    env_res = guard.resolve_artifacts_root(
        None, repo_root=fake_repo, env={guard.ARTIFACTS_ENV_VAR: str(tmp_path / "envdir")}
    )
    assert env_res.source == "env" and env_res.root == (tmp_path / "envdir").resolve()

    sib = guard.resolve_artifacts_root(None, repo_root=fake_repo, env={})
    assert sib.source == "sibling" and sib.root == sibling.resolve()


# ---------------------------------------------------------------------------
# B1：隔离目录（无痕迹仓）⇒ 两个脚本都**拒绝写**
# ---------------------------------------------------------------------------


def test_eval_regression_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """隔离目录里 ``--write-docs`` ⇒ 拒绝写 + stderr 说明 + 退出码非 0 + 零新增文件。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(iso / "scripts" / "eval_regression.py", ["--write-docs", "--run-id", "run_iso"])

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "NovelOS-Artifacts" in proc.stderr or "NOVELOS_ARTIFACTS_DIR" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_m1_report_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """``m1_report --write-docs`` 同样拒绝写（两个脚本行为一致）。"""
    iso = _isolated_copy(tmp_path, ["m1_report.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "m1_report.py",
        ["--write-docs", "--data-dir", str(tmp_path / "data")],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_eval_regression_refuses_to_build_baseline_without_artifacts(tmp_path: Path) -> None:
    """痕迹仓缺失 ⇒ **拒绝建基线**（而不是建到软件仓）+ 明确报错 + 非 0 退出。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(iso / "scripts" / "eval_regression.py", ["--run-id", "run_iso_nobase"])

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, proc.stderr
    assert "拒绝建基线" in proc.stderr, proc.stderr
    assert not (iso / "docs" / "evaluation" / "baseline" / "last_passing_run.json").exists()
    assert _snapshot(iso) == before, "拒绝建基线 ⇒ 隔离目录内不得产生任何文件"


# ---------------------------------------------------------------------------
# B2：基线落点 = 痕迹仓根下（**不是**软件仓）
# ---------------------------------------------------------------------------


def test_eval_regression_baseline_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--artifacts-dir`` ⇒ 报告与基线都落在该根下；软件仓内零新增文件。"""
    artifacts = tmp_path / "artifacts"
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        EVAL_SCRIPT,
        [
            "--artifacts-dir",
            str(artifacts),
            "--write-docs",
            "--run-id",
            "run_artifacts_landing",
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "decision: PASS" in proc.stdout, proc.stdout

    baseline = artifacts / "docs" / "evaluation" / "baseline" / "last_passing_run.json"
    assert baseline.is_file(), f"基线应落在痕迹仓根下：{baseline}"
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_artifacts_landing"
    assert payload["aggregate"]["cases_total"] >= 1
    assert payload["aggregate"]["cases_passed"] == payload["aggregate"]["cases_total"]
    assert payload["cases"], "基线必须含逐 case 签名"

    report = artifacts / "docs" / "evaluation" / "runs" / "run_artifacts_landing.json"
    assert report.is_file(), f"报告应落在痕迹仓根下：{report}"

    assert _snapshot(eval_base) == before_repo, "软件仓 docs/evaluation 内不得新增文件"
    assert not (eval_base / "baseline" / "last_passing_run.json").exists()


def test_eval_regression_refuses_baseline_inside_software_repo(tmp_path: Path) -> None:
    """显式把基线指向软件仓内的**不存在**路径 ⇒ 拒绝建（守卫）+ 不产生文件。"""
    target = REPO_ROOT / "docs" / "evaluation" / "baseline" / "last_passing_run.json"
    assert not target.exists(), "前提：软件仓内不得已有基线文件"

    proc = _run_script(
        EVAL_SCRIPT,
        ["--baseline", str(target), "--run-id", "run_repo_baseline"],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内建基线必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生基线文件"


def test_m1_report_write_docs_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``m1_report --write-docs --artifacts-dir`` ⇒ 报告落在痕迹仓根下。"""
    artifacts = tmp_path / "artifacts"
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        M1_SCRIPT,
        [
            "--artifacts-dir",
            str(artifacts),
            "--write-docs",
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out = artifacts / "docs" / "evaluation" / "m1-data.md"
    assert out.is_file(), f"报告应落在痕迹仓根下：{out}"
    assert "M1" in out.read_text(encoding="utf-8")
    assert _snapshot(eval_base) == before_repo, "软件仓 docs/evaluation 内不得新增文件"


def test_m1_report_out_at_repo_root_is_rejected(tmp_path: Path) -> None:
    """``--out`` 指向仓库根下的 ``foo.md`` ⇒ 守卫拒绝 + **不产生文件**。"""
    target = REPO_ROOT / "m1-report-guard-probe.md"
    assert not target.exists(), "前提：目标不得已存在"

    proc = _run_script(
        M1_SCRIPT,
        ["--out", str(target), "--data-dir", str(tmp_path / "data")],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"仓库根下的输出路径必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生文件"


# ---------------------------------------------------------------------------
# 逃生口：默认关，显式开才生效
# ---------------------------------------------------------------------------


def test_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """``--allow-software-repo-out`` 显式开启后，痕迹才允许落回软件仓（并给出告警）。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])

    proc = _run_script(
        iso / "scripts" / "eval_regression.py",
        [
            "--write-docs",
            "--allow-software-repo-out",
            "--run-id",
            "run_escape_hatch",
            "--golden-dir",
            str(GOLDEN_DIR),
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    written = iso / "docs" / "evaluation" / "runs" / "run_escape_hatch.json"
    assert written.is_file(), f"逃生口开启后报告应落在软件仓：{written}"
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_escape_hatch"


def test_default_run_does_not_write_into_software_repo(tmp_path: Path) -> None:
    """默认（无 ``--write-docs``）跑一次 ⇒ 软件仓内零新增文件（报告落系统临时目录）。"""
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        EVAL_SCRIPT,
        ["--run-id", "run_default_landing", "--golden-dir", str(tmp_path / "empty_golden")],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert _snapshot(eval_base) == before_repo, "默认模式不得在软件仓内新增文件"
    assert "novelos_eval_runs" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# 第二批（对抗审查 B1 同族）：m2_judge / real_llm_e2e 的落点也必须落痕迹仓
# ---------------------------------------------------------------------------


def _seed_judge_db(db_path: Path) -> None:
    """造一个最小可评审库：1 章 COMMITTED + 1 条 draft（**合成夹具**，不碰真库）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE chapters ("
            "chapter_id TEXT PRIMARY KEY, number INTEGER, title TEXT, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE drafts ("
            "id INTEGER PRIMARY KEY, chapter_id TEXT, version INTEGER, content TEXT)"
        )
        conn.execute(
            "INSERT INTO chapters VALUES ('ch_synthetic01', 1, '合成章', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO drafts VALUES (1, 'ch_synthetic01', 1, '合成正文片段，用于 dry-run 评审。')"
        )
        conn.commit()
    finally:
        conn.close()


def test_m2_judge_refuses_to_write_without_artifacts(tmp_path: Path) -> None:
    """隔离目录（无痕迹仓）跑 ``m2_judge`` ⇒ 拒绝写 + 零新增文件 + 退出码非 0。"""
    iso = _isolated_copy(tmp_path, ["m2_judge.py", "guard_repo_write_path.py"])
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    before = _snapshot(iso)
    repo_eval = _snapshot(REPO_ROOT / "docs" / "evaluation")

    proc = _run_script(
        iso / "scripts" / "m2_judge.py",
        ["--dry-run", "--db", str(db)],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "chapter_id" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"
    assert _snapshot(REPO_ROOT / "docs" / "evaluation") == repo_eval, "软件仓内不得新增文件"


def test_m2_judge_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--artifacts-dir`` ⇒ 评审产物真的落在该根下（内容非空），软件仓零新增。"""
    artifacts = tmp_path / "artifacts"
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    repo_eval = _snapshot(REPO_ROOT / "docs" / "evaluation")

    proc = _run_script(
        M2_JUDGE_SCRIPT,
        ["--dry-run", "--db", str(db), "--artifacts-dir", str(artifacts)],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out_dir = artifacts / "docs" / "evaluation" / "m2-judge"
    results = out_dir / "judge_results.json"
    summary = out_dir / "judge_summary.md"
    assert results.is_file(), f"评审明细应落在痕迹仓根下：{results}"
    assert summary.is_file(), f"汇总应落在痕迹仓根下：{summary}"
    payload = json.loads(results.read_text(encoding="utf-8"))
    assert payload["results"], "judge_results.json 必须含逐章结果"
    assert payload["results"][0]["number"] == 1
    assert payload["results"][0]["dry_run"] is True
    assert summary.read_text(encoding="utf-8").strip(), "汇总报告不得为空"
    assert _snapshot(REPO_ROOT / "docs" / "evaluation") == repo_eval, "软件仓内不得新增文件"


def test_m2_judge_out_dir_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--out-dir`` 指向软件仓内 ⇒ 守卫拒绝 + 不产生文件。"""
    target_dir = REPO_ROOT / "docs" / "evaluation" / "m2-judge"
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    before = _snapshot(target_dir)

    proc = _run_script(
        M2_JUDGE_SCRIPT,
        ["--dry-run", "--db", str(db), "--out-dir", str(target_dir)],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的产物目录必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert _snapshot(target_dir) == before, "拒绝 ⇒ 软件仓内不得产生文件"


def test_m2_judge_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """``--allow-software-repo-out`` 显式开启后，产物才允许落回软件仓（并给出告警）。"""
    iso = _isolated_copy(tmp_path, ["m2_judge.py", "guard_repo_write_path.py"])
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)

    proc = _run_script(
        iso / "scripts" / "m2_judge.py",
        ["--dry-run", "--db", str(db), "--allow-software-repo-out"],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    written = iso / "docs" / "evaluation" / "m2-judge" / "judge_results.json"
    assert written.is_file(), f"逃生口开启后产物应落在软件仓：{written}"
    assert json.loads(written.read_text(encoding="utf-8"))["results"], "产物不得为空"


def test_real_llm_e2e_refuses_without_artifacts_before_any_llm_call(tmp_path: Path) -> None:
    """隔离目录跑 ``real_llm_e2e`` ⇒ **在任何 LLM 调用之前**就拒绝写。

    ⚠️ 子进程环境**清掉** ``MINIMAX_API_KEY`` ⇒ 无论如何都不可能发起真实模型调用
    （本批硬约束：零真实 LLM）。退出码 3（而不是「未设置 key」的 2）⇒ 证明落点判定
    排在 API key 检查与真实调用**之前**。
    """
    iso = _isolated_copy(tmp_path, ["real_llm_e2e.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "real_llm_e2e.py",
        [],
        unset_env=["MINIMAX_API_KEY", "NOVELOS_API_KEY_OPENAI_COMPATIBLE"],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "MINIMAX_API_KEY" not in proc.stdout, "落点判定必须早于 API key 检查"
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_real_llm_e2e_report_path_lands_in_artifacts_root(tmp_path: Path) -> None:
    """落点判定函数：``--artifacts-dir`` ⇒ 报告路径落在该根下（纯函数，不跑真调用）。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    artifacts = tmp_path / "artifacts"

    out = mod.resolve_report_path(artifacts_dir=str(artifacts))

    assert out == artifacts / "docs" / "evaluation" / "runs" / mod.REPORT_FILENAME


def test_real_llm_e2e_report_path_raises_when_artifacts_missing(tmp_path: Path, monkeypatch) -> None:
    """落点判定函数：痕迹仓不可用 ⇒ 抛异常（**不回落到软件仓**）。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    monkeypatch.setattr(mod, "ROOT", fake_repo)
    monkeypatch.delenv("NOVELOS_ARTIFACTS_DIR", raising=False)

    with pytest.raises(mod.ArtifactsUnavailableError) as excinfo:
        mod.resolve_report_path()

    msg = str(excinfo.value)
    assert "痕迹仓不可用" in msg, msg
    assert "NovelOS-Artifacts" in msg, msg
    assert "--allow-software-repo-out" in msg, msg
    assert "docs/evaluation/runs" not in msg, "不得把软件仓路径当成可用落点"


def test_real_llm_e2e_escape_hatch_falls_back_to_software_repo(tmp_path: Path, monkeypatch) -> None:
    """逃生口开启 ⇒ 明确回落到软件仓（并打告警），而不是静默。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    monkeypatch.setattr(mod, "ROOT", fake_repo)
    monkeypatch.delenv("NOVELOS_ARTIFACTS_DIR", raising=False)

    out = mod.resolve_report_path(allow_software_repo_out=True)

    assert out == fake_repo / "docs" / "evaluation" / "runs" / mod.REPORT_FILENAME


# ---------------------------------------------------------------------------
# 第三批（D-19 另一半）：另外 4 个落盘脚本也必须接守卫
#   backup_db / db_maintenance / human_baseline_synthetic / inspect_stalled_runs
#
# 缺陷形状与前两批**同族**：这 4 个脚本都能把产物写进软件仓里任意位置
# （backup_db --out / db_maintenance --export / human_baseline_synthetic --out /
# inspect_stalled_runs --export）⇒ 落进软件仓 = 进 GitHub 历史，**不可撤回**。
#
# 本批钉住（每个脚本两条：仓内非法位置 ⇒ 拒绝；允许位置 ⇒ 真的落盘）：
# - 拒绝：退出码 == 2（EXIT_WRITE_PATH_REJECTED）、stderr 含「拒绝写入」与目标名、
#   **目标文件/目录未被创建**、软件仓内零新增文件；
# - 允许：退出码 0、产物**真的存在**且内容可解析（断言打在真实值上）；
# - 痕迹/评估类产物（hbs --write-docs / isr --write-docs）在**拿不到痕迹仓**时
#   ⇒ 退出码 3（EXIT_ARTIFACTS_UNAVAILABLE）+ format_artifacts_unavailable 文案，
#   **零文件落盘**、绝不回落软件仓。
#
# ⚠️ 隔离测试用**真实 subprocess**跑脚本；DB 全部是**合成夹具**（tmp_path 内），
# **零真实 LLM 调用、零活库接触（不碰 data/novelos.db）、零内容仓接触、零 git 写操作**。
# ---------------------------------------------------------------------------


def _seed_backup_source_db(path: Path) -> None:
    """造一个最小 SQLite 库当备份**源**（合成夹具，不碰活库）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE probe (x TEXT)")
        conn.execute("INSERT INTO probe VALUES ('synthetic')")
        conn.commit()
    finally:
        conn.close()


def _seed_maint_db(path: Path) -> None:
    """造一个最小 ``ai_call_logs`` 表（合成夹具，不碰活库）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE ai_call_logs ("
            "call_id TEXT, run_id TEXT, node_run_id TEXT, agent_id TEXT, model_id TEXT, "
            "prompt_version TEXT, latency_ms INTEGER, error TEXT, retry_count INTEGER, "
            "created_at TEXT, output_json TEXT)"
        )
        conn.execute(
            "INSERT INTO ai_call_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "call_synth01",
                "run_synth01",
                None,
                "agent_synth",
                "model_synth",
                "v1",
                12,
                None,
                0,
                "2020-01-01T00:00:00+00:00",
                '{"synthetic": true}',
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_stalled_db(path: Path) -> None:
    """造一个含 1 条 PAUSED run 的最小库（合成夹具，不碰活库）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE projects (project_id TEXT, name TEXT)")
        conn.execute(
            "CREATE TABLE chapters ("
            "chapter_id TEXT, number INTEGER, title TEXT, status TEXT, project_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE workflow_runs ("
            "run_id TEXT, workflow_id TEXT, chapter_id TEXT, status TEXT, current_node TEXT, "
            "started_at TEXT, ended_at TEXT, retry_count INTEGER, error TEXT, instance_id TEXT, "
            "checkpoint_json TEXT)"
        )
        conn.execute("INSERT INTO projects VALUES ('prj_synth01', '合成项目')")
        conn.execute(
            "INSERT INTO chapters VALUES ('ch_synth01', 1, '合成章', 'COMMITTED', 'prj_synth01')"
        )
        conn.execute(
            "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run_synth01",
                "wf_synth01",
                "ch_synth01",
                "PAUSED",
                "chapter-review",
                "2026-10-01T00:00:00+00:00",
                None,
                0,
                None,
                "inst_synth01",
                '{"__pause_payload__": {"review_report": {"word_count": 123}}}',
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_backup_db_out_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--out`` 指向软件仓内非法位置 ⇒ 拒绝（退出码 2）+ 目录未被创建。

    ⚠️ 探针落点在**假软件仓**（``tmp_path`` 内）⇒ 真仓库的 ``docs/`` 零污染（F-7）。
    """
    fake = _fake_repo(tmp_path, ["backup_db.py", "guard_repo_write_path.py"])
    src = tmp_path / "src.db"
    _seed_backup_source_db(src)
    target = fake / "docs" / "_guard_probe_backup"
    assert not target.exists(), "前提：目标不得已存在"
    before = _snapshot(fake)

    proc = _run_script(
        fake / "scripts" / "backup_db.py",
        ["--db", str(src), "--out", str(target), "--plaintext"],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的备份落点必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "_guard_probe_backup" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内创建备份目录"
    assert not (fake / "docs").exists(), "拒绝 ⇒ 连**父目录**都不该被建出来（F-8）"
    assert _snapshot(fake) == before, "假软件仓内不得产生任何文件"


def test_backup_db_out_outside_repo_succeeds(tmp_path: Path) -> None:
    """``--out`` 指向仓外允许位置 ⇒ 成功，备份**真的落盘**。"""
    src = tmp_path / "src.db"
    _seed_backup_source_db(src)
    out_dir = tmp_path / "backups"

    proc = _run_script(
        BACKUP_SCRIPT,
        ["--db", str(src), "--out", str(out_dir), "--plaintext"],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    produced = sorted(out_dir.glob("novelos_*.db"))
    assert produced, f"备份应当真的落盘：{out_dir}"
    conn = sqlite3.connect(str(produced[0]))
    try:
        rows = conn.execute("SELECT x FROM probe").fetchall()
    finally:
        conn.close()
    assert rows == [("synthetic",)], "备份内容应是源库的逐字节副本"


def test_backup_db_default_landing_is_not_rejected_by_guard(guard, monkeypatch) -> None:
    """默认落点（同步盘）在仓外 ⇒ 守卫**不得**把它判成非法（不得误伤正常默认）。"""
    mod = importlib.import_module("scripts.backup_db")
    monkeypatch.delenv("NOVELOS_BACKUP_DIR", raising=False)
    try:
        resolved = mod.resolve_out_dir(None)
    except SystemExit as exc:
        # 本机没有同步盘候选 ⇒ 脚本按**既有**契约中止（与守卫无关）
        assert "同步盘" in str(exc), str(exc)
        return
    assert not guard.is_inside_repo(resolved), f"默认落点必须在仓外：{resolved}"
    assert guard.check_write_path(resolved) == resolved.resolve(), "守卫必须放行默认落点"


def test_db_maintenance_export_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--export`` 指向软件仓内 ⇒ 拒绝（退出码 2）+ 导出文件未被创建。

    ⚠️ 探针落点在**假软件仓**（``tmp_path`` 内）⇒ 真仓库的 ``docs/`` 零污染（F-7）。
    """
    fake = _fake_repo(tmp_path, ["db_maintenance.py", "guard_repo_write_path.py"])
    db = tmp_path / "maint.db"
    _seed_maint_db(db)
    target = fake / "docs" / "_guard_probe_maint.json"
    assert not target.exists(), "前提：目标不得已存在"
    before = _snapshot(fake)

    proc = _run_script(
        fake / "scripts" / "db_maintenance.py",
        ["--db", str(db), "prune-logs", "--export", str(target)],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的导出路径必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "_guard_probe_maint.json" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生导出文件"
    assert not (fake / "docs").exists(), "拒绝 ⇒ 连**父目录**都不该被建出来（F-8）"
    assert _snapshot(fake) == before, "假软件仓内不得产生任何文件"


def test_db_maintenance_export_outside_repo_succeeds(tmp_path: Path) -> None:
    """``--export`` 指向仓外允许位置 ⇒ 成功，导出**真的落盘**且内容可解析。"""
    db = tmp_path / "maint.db"
    _seed_maint_db(db)
    out = tmp_path / "exported_logs.json"

    proc = _run_script(
        DB_MAINT_SCRIPT,
        ["--db", str(db), "prune-logs", "--export", str(out)],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert out.is_file(), f"导出应当真的落盘：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == 1, payload["count"]
    assert payload["rows"][0]["call_id"] == "call_synth01"
    assert payload["days"] == 90


def test_human_baseline_synthetic_out_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--out`` 指向软件仓内 ⇒ 拒绝（退出码 2）+ 报告未被创建。

    ⚠️ 探针落点在**假软件仓**（``tmp_path`` 内）⇒ 真仓库的 ``docs/`` 零污染（F-7）。
    """
    fake = _fake_repo(tmp_path, ["human_baseline_synthetic.py", "guard_repo_write_path.py"])
    target = fake / "docs" / "_guard_probe_hbs.md"
    assert not target.exists(), "前提：目标不得已存在"
    before = _snapshot(fake)

    proc = _run_script(
        fake / "scripts" / "human_baseline_synthetic.py",
        ["--out", str(target)],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的报告落点必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "_guard_probe_hbs.md" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生报告"
    assert not (fake / "docs").exists(), "拒绝 ⇒ 连**父目录**都不该被建出来（F-8）"
    assert _snapshot(fake) == before, "假软件仓内不得产生任何文件"


def test_human_baseline_synthetic_out_outside_repo_succeeds(tmp_path: Path) -> None:
    """``--out`` 指向仓外允许位置 ⇒ 成功，报告**真的落盘**且是合法 JSON。"""
    out = tmp_path / "hbs-report.json"

    proc = _run_script(HBS_SCRIPT, ["--out", str(out), "--format", "json"])

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert out.is_file(), f"报告应当真的落盘：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "human-baseline-synthetic/v1"
    assert payload["metrics"], "报告必须含逐算子对照行"
    assert payload["human_side"]["visible_chars"] > 0


def test_human_baseline_synthetic_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """隔离目录（无痕迹仓）跑 ``--write-docs`` ⇒ 退出码 3 + 零文件落盘。"""
    iso = _isolated_copy(tmp_path, ["human_baseline_synthetic.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "human_baseline_synthetic.py",
        ["--write-docs", "--format", "json"],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "", "落点判定必须先于产出（不得先喷一屏报告再失败）"
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_human_baseline_synthetic_write_docs_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--write-docs --artifacts-dir`` ⇒ 报告落在该痕迹仓根下（内容非空）。"""
    artifacts = tmp_path / "artifacts"

    proc = _run_script(
        HBS_SCRIPT,
        ["--write-docs", "--artifacts-dir", str(artifacts), "--format", "json"],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out = artifacts / "docs" / "evaluation" / "human-baseline-synthetic.json"
    assert out.is_file(), f"报告应落在痕迹仓根下：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "human-baseline-synthetic/v1"
    assert payload["metrics"], "落盘内容不得为空"


def test_inspect_stalled_runs_export_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--export`` 指向软件仓内 ⇒ 拒绝（退出码 2）+ 留档未被创建。

    ⚠️ 探针落点在**假软件仓**（``tmp_path`` 内）⇒ 真仓库的 ``docs/`` 零污染（F-7）。
    """
    fake = _fake_repo(tmp_path, ["inspect_stalled_runs.py", "guard_repo_write_path.py"])
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)
    target = fake / "docs" / "_guard_probe_isr.json"
    assert not target.exists(), "前提：目标不得已存在"
    before = _snapshot(fake)

    proc = _run_script(
        fake / "scripts" / "inspect_stalled_runs.py",
        ["--db", str(db), "--export", str(target)],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的留档路径必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "_guard_probe_isr.json" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生留档"
    assert not (fake / "docs").exists(), "拒绝 ⇒ 连**父目录**都不该被建出来（F-8）"
    assert _snapshot(fake) == before, "假软件仓内不得产生任何文件"


def test_inspect_stalled_runs_export_outside_repo_succeeds(tmp_path: Path) -> None:
    """``--export`` 指向仓外允许位置 ⇒ 成功，留档**真的落盘**且含 checkpoint_json。"""
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)
    out = tmp_path / "stalled_export.json"

    proc = _run_script(
        ISR_SCRIPT,
        ["--db", str(db), "--export", str(out)],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert out.is_file(), f"留档应当真的落盘：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["read_only"] is True
    assert payload["total_active_runs"] == 1
    assert payload["runs"][0]["run_id"] == "run_synth01"
    assert "checkpoint_json" in payload["runs"][0]


def test_inspect_stalled_runs_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """隔离目录（无痕迹仓）跑 ``--write-docs`` ⇒ 退出码 3 + 零文件落盘。"""
    iso = _isolated_copy(tmp_path, ["inspect_stalled_runs.py", "guard_repo_write_path.py"])
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "inspect_stalled_runs.py",
        ["--db", str(db), "--write-docs"],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "", "落点判定必须先于读库与产出"
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_inspect_stalled_runs_write_docs_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--write-docs --artifacts-dir`` ⇒ 留档落在该痕迹仓根下（含 checkpoint_json）。"""
    artifacts = tmp_path / "artifacts"
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)

    proc = _run_script(
        ISR_SCRIPT,
        ["--db", str(db), "--write-docs", "--artifacts-dir", str(artifacts)],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out = artifacts / "docs" / "evaluation" / "stalled-runs-export.json"
    assert out.is_file(), f"留档应落在痕迹仓根下：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["runs"][0]["run_id"] == "run_synth01"


# ---------------------------------------------------------------------------
# 第四批（2026-10-23 对抗审查 F-3 / F-6 / F-9）：
#
# - **F-3**：``backup_db.py --key-file`` 指向软件仓内 ⇒ 「能解开所有备份的密钥」
#   落进版本库（``.gitignore`` 此前也不覆盖 ``backup.key`` ⇒ ``git add -A`` 就提交）
#   ⇒ 密钥文件也必须过写路径守卫；
# - **F-6**：``--out`` / ``--export`` 与 ``--write-docs`` **同时**给出 ⇒ 旧实现
#   静默吞掉后者（痕迹仓副本没落、零告警）⇒ 现在**两份都落 + 明确告警**；
# - **F-9**：4 个新脚本的 ``--allow-software-repo-out`` **一例测试都没有** ⇒ 逐个补上
#   （落点真落 + stderr 告警文本）。
#
# ⚠️ 全部在**假软件仓**（``tmp_path`` 内）里跑 ⇒ 真仓库零污染。
# ---------------------------------------------------------------------------


def test_backup_db_key_file_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--key-file`` 指向软件仓内 ⇒ 拒绝（退出码 2）+ 密钥文件与目标目录都未被创建。"""
    fake = _fake_repo(tmp_path, ["backup_db.py", "guard_repo_write_path.py"])
    src = tmp_path / "src.db"
    _seed_backup_source_db(src)
    key_file = fake / "docs" / "backup.key"
    assert not key_file.exists(), "前提：密钥文件不得已存在"

    proc = _run_script(
        fake / "scripts" / "backup_db.py",
        [
            "--db", str(src),
            "--out", str(tmp_path / "bk"),
            "--key-file", str(key_file),
            "--init-key",
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"仓内密钥文件必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "backup.key" in proc.stderr, proc.stderr
    assert not key_file.exists(), "拒绝 ⇒ 不得写入密钥文件"
    assert not (fake / "docs").exists(), "拒绝 ⇒ 连密钥文件的**目标目录**都不该被创建"


def test_backup_db_key_file_escape_hatch_allows_software_repo(tmp_path: Path) -> None:
    """``--allow-software-repo-key`` 显式开启 ⇒ 密钥文件才允许落软件仓（并打告警）。"""
    fake = _fake_repo(tmp_path, ["backup_db.py", "guard_repo_write_path.py"])
    src = tmp_path / "src.db"
    _seed_backup_source_db(src)
    key_file = fake / "backup" / "backup.key"

    proc = _run_script(
        fake / "scripts" / "backup_db.py",
        [
            "--db", str(src),
            "--out", str(tmp_path / "bk"),
            "--key-file", str(key_file),
            "--init-key",
            "--allow-software-repo-key",
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-key" in proc.stderr, proc.stderr
    assert key_file.is_file(), f"逃生口开启后密钥文件应真的落盘：{key_file}"
    assert key_file.read_text(encoding="utf-8").strip(), "密钥文件不得为空"


def test_backup_db_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """F-9：``--allow-software-repo-out`` ⇒ 备份落进软件仓 + stderr 告警。"""
    fake = _fake_repo(tmp_path, ["backup_db.py", "guard_repo_write_path.py"])
    src = tmp_path / "src.db"
    _seed_backup_source_db(src)
    out_dir = fake / "backups"

    proc = _run_script(
        fake / "scripts" / "backup_db.py",
        [
            "--db", str(src),
            "--out", str(out_dir),
            "--plaintext",
            "--allow-software-repo-out",
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    produced = sorted(out_dir.glob("novelos_*.db"))
    assert produced, f"逃生口开启后备份应真的落进软件仓：{out_dir}"
    conn = sqlite3.connect(str(produced[0]))
    try:
        rows = conn.execute("SELECT x FROM probe").fetchall()
    finally:
        conn.close()
    assert rows == [("synthetic",)], "落进仓内的备份内容应是源库的逐字节副本"


def test_db_maintenance_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """F-9：``--allow-software-repo-out`` ⇒ 导出落进软件仓 + stderr 告警。"""
    fake = _fake_repo(tmp_path, ["db_maintenance.py", "guard_repo_write_path.py"])
    db = tmp_path / "maint.db"
    _seed_maint_db(db)
    out = fake / "docs" / "exported_logs.json"

    proc = _run_script(
        fake / "scripts" / "db_maintenance.py",
        [
            "--db", str(db),
            # ⚠️ db_maintenance 的 --allow-software-repo-out 是**顶层**开关
            #（必须排在子命令之前）。
            "--allow-software-repo-out",
            "prune-logs",
            "--export", str(out),
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    assert out.is_file(), f"逃生口开启后导出应真的落进软件仓：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["rows"][0]["call_id"] == "call_synth01"


def test_human_baseline_synthetic_escape_hatch_allows_writing_into_software_repo(
    tmp_path: Path,
) -> None:
    """F-9：``--allow-software-repo-out`` ⇒ 报告落进软件仓 + stderr 告警。"""
    fake = _fake_repo(tmp_path, ["human_baseline_synthetic.py", "guard_repo_write_path.py"])

    proc = _run_script(
        fake / "scripts" / "human_baseline_synthetic.py",
        ["--write-docs", "--allow-software-repo-out", "--format", "json"],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    out = fake / "docs" / "evaluation" / "human-baseline-synthetic.json"
    assert out.is_file(), f"逃生口开启后报告应真的落进软件仓：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "human-baseline-synthetic/v1"
    assert payload["metrics"], "落进仓内的报告不得为空"


def test_inspect_stalled_runs_escape_hatch_allows_writing_into_software_repo(
    tmp_path: Path,
) -> None:
    """F-9：``--allow-software-repo-out`` ⇒ 留档落进软件仓 + stderr 告警。"""
    fake = _fake_repo(tmp_path, ["inspect_stalled_runs.py", "guard_repo_write_path.py"])
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)

    proc = _run_script(
        fake / "scripts" / "inspect_stalled_runs.py",
        ["--db", str(db), "--write-docs", "--allow-software-repo-out"],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    out = fake / "docs" / "evaluation" / "stalled-runs-export.json"
    assert out.is_file(), f"逃生口开启后留档应真的落进软件仓：{out}"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["runs"][0]["run_id"] == "run_synth01"


def test_human_baseline_synthetic_out_and_write_docs_both_land(tmp_path: Path) -> None:
    """F-6：``--out`` 与 ``--write-docs`` 同时给 ⇒ **两份都落** + 明确告警。

    旧实现是 ``if args.out: ... elif args.write_docs:`` ⇒ ``--out`` **静默吞掉**
    ``--write-docs``（痕迹仓副本没落、零告警）。
    """
    fake = _fake_repo(tmp_path, ["human_baseline_synthetic.py", "guard_repo_write_path.py"])
    artifacts = tmp_path / "artifacts"
    out = tmp_path / "hbs-report.json"

    proc = _run_script(
        fake / "scripts" / "human_baseline_synthetic.py",
        [
            "--out", str(out),
            "--write-docs",
            "--artifacts-dir", str(artifacts),
            "--format", "json",
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--out" in proc.stderr and "--write-docs" in proc.stderr, (
        f"两个开关同时给出必须有明确告警：{proc.stderr}"
    )
    assert out.is_file(), f"--out 那份必须落：{out}"
    docs_copy = artifacts / "docs" / "evaluation" / "human-baseline-synthetic.json"
    assert docs_copy.is_file(), f"痕迹仓那份也必须落（旧实现被 --out 静默吞掉）：{docs_copy}"
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "human-baseline-synthetic/v1"
    assert json.loads(docs_copy.read_text(encoding="utf-8"))["schema"] == "human-baseline-synthetic/v1"


def test_inspect_stalled_runs_export_and_write_docs_both_land(tmp_path: Path) -> None:
    """F-6：``--export`` 与 ``--write-docs`` 同时给 ⇒ **两份都落** + 明确告警。"""
    fake = _fake_repo(tmp_path, ["inspect_stalled_runs.py", "guard_repo_write_path.py"])
    db = tmp_path / "stalled.db"
    _seed_stalled_db(db)
    artifacts = tmp_path / "artifacts"
    export = tmp_path / "stalled_export.json"

    proc = _run_script(
        fake / "scripts" / "inspect_stalled_runs.py",
        [
            "--db", str(db),
            "--export", str(export),
            "--write-docs",
            "--artifacts-dir", str(artifacts),
        ],
        pythonpath=_fake_pythonpath(fake),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--export" in proc.stderr and "--write-docs" in proc.stderr, (
        f"两个开关同时给出必须有明确告警：{proc.stderr}"
    )
    assert export.is_file(), f"--export 那份必须落：{export}"
    docs_copy = artifacts / "docs" / "evaluation" / "stalled-runs-export.json"
    assert docs_copy.is_file(), f"痕迹仓那份也必须落（旧实现被 --export 静默吞掉）：{docs_copy}"
    payload = json.loads(docs_copy.read_text(encoding="utf-8"))
    assert payload["runs"][0]["run_id"] == "run_synth01"

