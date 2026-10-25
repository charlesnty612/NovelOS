"""``tests/conftest.py`` 四道守卫的判别用例（B1 密钥隔离 / B2 basetemp 硬失败 / B3 出网机制 / B4 DB 沙箱）。

判别原则（AGENTS §一.5「门禁自证会 FAIL」）：

- **B2**：仓内 ``--basetemp`` ⇒ 必须**硬失败**（``UsageError``，退出码 4）；
  仓外同一命令 ⇒ 必须放行。两条都有**端到端**（子进程真跑 pytest）证据，
  另有单元级判据（守卫函数本体）。
- **B1**：测试进程内 ``load_api_keys()`` 恒 ``{}``；回落基址（env 缺失时）已被
  重定向到沙箱，**不是**仓根。
- **B3**：本批**只提供机制、不启用 autouse** ⇒ 断言默认状态下 ``socket.connect``
  未被替换；机制本身（装上之后）能拦非 loopback、放行 loopback、且不误杀
  ``httpx.MockTransport``。
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest


def _load_guard_module() -> Any:
    """取 pytest **实际加载**的那份 ``tests/conftest.py`` 模块。

    ⚠️ 不能写 ``from tests import conftest``：``tests/`` 没有 ``__init__.py``
    ⇒ 那会**再导入一份新的**模块实例（模块级状态与 pytest 加载的那份不是同一个，
    判别用例会变成假绿）。这里按**文件路径**在 ``sys.modules`` 里定位，
    保证拿到的是同一份实例。
    """
    target = Path(__file__).resolve().parents[1] / "conftest.py"
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if file and Path(file).resolve() == target:
            return module
    raise AssertionError(f"未找到 pytest 已加载的 conftest 模块：{target}")


guards = _load_guard_module()

REPO_ROOT = guards.REPO_ROOT


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _StubConfig:
    """最小 pytest config 替身：只提供 ``getoption`` / ``option``。"""

    def __init__(self, basetemp: str | None) -> None:
        self.option = SimpleNamespace(basetemp=basetemp)

    def getoption(self, name: str, default: object = None) -> object:
        return getattr(self.option, name, default)


def _run_pytest(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """跑一次子进程 pytest（清掉外层遗留的 pytest 环境变量）。"""
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
    )


# ---------------------------------------------------------------------------
# B2：basetemp 守卫（单元级）
# ---------------------------------------------------------------------------


def test_guard_rejects_repo_internal_basetemp() -> None:
    """仓内 basetemp ⇒ UsageError（硬失败，不是警告）。"""
    cfg = _StubConfig(str(REPO_ROOT / ".b1_probe_basetemp"))
    with pytest.raises(pytest.UsageError) as exc:
        guards.enforce_external_basetemp(cfg)
    assert "basetemp" in str(exc.value)


def test_guard_accepts_external_basetemp() -> None:
    """仓外 basetemp ⇒ 放行，且**不改**调用方给的值。"""
    external = Path(tempfile.gettempdir()) / "novelos_b1_probe_external"
    cfg = _StubConfig(str(external))
    guards.enforce_external_basetemp(cfg)
    assert cfg.option.basetemp == str(external)
    assert cfg._novelos_basetemp_owned is False


def test_guard_default_basetemp_is_outside_repo() -> None:
    """未指定 basetemp ⇒ 自动落仓外唯一目录（且带 PID ⇒ 并行互不覆盖）。"""
    cfg = _StubConfig(None)
    guards.enforce_external_basetemp(cfg)
    chosen = Path(str(cfg.option.basetemp))
    assert not guards.is_inside_repo(chosen)
    assert chosen.parent == Path(tempfile.gettempdir())
    assert guards._BASETEMP_DIRNAME in chosen.name
    assert cfg._novelos_basetemp_owned is True


def test_session_effective_basetemp_is_outside_repo(pytestconfig: pytest.Config) -> None:
    """本次会话真正生效的 basetemp 必须在仓外（端到端断言，不看配置意图）。"""
    effective = str(pytestconfig.getoption("basetemp", "") or "")
    assert effective, "basetemp 未设置：守卫应当强制注入仓外目录"
    assert not guards.is_inside_repo(Path(effective))


# ---------------------------------------------------------------------------
# B2：basetemp 守卫（端到端：子进程真跑 pytest）
# ---------------------------------------------------------------------------


def test_repo_internal_basetemp_fails_end_to_end() -> None:
    """★反例：仓内 ``--basetemp`` ⇒ 子进程 pytest 硬失败，且仓内不落任何目录。

    退出码口径：pytest 抛 ``UsageError`` ⇒ ``ExitCode.USAGE_ERROR = 4``
    （**不是** 1；按 1 写会假绿）⇒ 这里断言 4 + 消息含 ``--basetemp``。
    """
    probe = REPO_ROOT / ".b1_probe_basetemp"
    try:
        proc = _run_pytest(
            ["tests/unit/test_config.py", "-q", f"--basetemp={probe}"],
            cwd=REPO_ROOT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 4, f"仓内 basetemp 退出码应为 4（USAGE_ERROR）：\n{output}"
        assert "--basetemp" in output, f"失败信息未指向 --basetemp：\n{output}"
        assert not probe.exists(), "守卫必须在建目录之前失败，仓内不得留残留目录"
    finally:
        if probe.exists():  # 守卫失效时的兜底清理（自建探针，非仓内既有内容）
            shutil.rmtree(probe, ignore_errors=True)


def test_is_inside_repo_delegates_to_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    """★M1 判别：仓内外判定**必须委托**仓内唯一实现（单一事实源）。

    做法：把生产侧 ``relative_in_repo`` 的返回值改成「仓内」/「仓外」⇒ conftest 的
    ``is_inside_repo`` 必须**跟着变**（自写第二份实现不会跟着变 ⇒ 必红）。
    生产侧口径：``scripts/guard_repo_write_path.py:251``（委托 :270 ``relative_in_repo``）。
    """
    from scripts import guard_repo_write_path as canonical

    # ⚠️ 交叉核对：让生产侧说「反话」——判「仓外」却指向**仓内**路径、判「仓内」却
    # 指向**仓外**路径 ⇒ 只有「委托生产侧」的实现才会跟着变；自写第二份实现
    # 会按路径真实位置回答 ⇒ 本条必红（这才是判别力，光比对两条路径不够：
    # 期望值恰好与自写实现一致时会假绿）。
    monkeypatch.setattr(canonical, "relative_in_repo", lambda *a, **k: None)
    assert guards.is_inside_repo(REPO_ROOT / "docs") is False, (
        "仓内外判定没有跟随生产侧实现 ⇒ 存在第二份实现（F-7 同型）"
    )
    monkeypatch.setattr(canonical, "relative_in_repo", lambda *a, **k: Path("."))
    assert guards.is_inside_repo(Path(tempfile.gettempdir())) is True, (
        "仓内外判定没有跟随生产侧实现 ⇒ 存在第二份实现（F-7 同型）"
    )


def test_repo_internal_basetemp_with_extended_length_prefix_is_rejected() -> None:
    """★M1 判别（端到端）：``\\\\?\\`` 扩展长度前缀的**仓内** basetemp 必须被拒。

    自写第二份仓内外判定 ⇒ 该形状会被判成「仓外」⇒ B2 被绕过（仓内 basetemp ⇒
    50 条集体假红）。判据：退出码 4（USAGE_ERROR）+ 消息含 ``--basetemp``。
    """
    raw = "\\\\?\\" + str(REPO_ROOT / ".b1_probe_prefix")
    probe = REPO_ROOT / ".b1_probe_prefix"
    try:
        proc = _run_pytest(
            ["tests/unit/test_config.py", "-q", f"--basetemp={raw}"],
            cwd=REPO_ROOT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 4, (
            f"扩展长度前缀的仓内 basetemp 必须被拒（exit 4），实际 {proc.returncode}：\n{output}"
        )
        assert "--basetemp" in output
        assert not probe.exists(), "守卫必须在建目录之前失败，仓内不得留残留目录"
    finally:
        if probe.exists():  # 守卫失效时的兜底清理（自建探针，非仓内既有内容）
            shutil.rmtree(probe, ignore_errors=True)


def test_lowercase_drive_letter_variant_is_still_inside_repo() -> None:
    """★M1 DoD：盘符/目录**大小写变体**（``NovelOS`` → ``novelos``）必须仍判「在仓内」。

    委托后的判定走生产侧规范化（``relative_in_repo``）⇒ 大小写变体不得成为绕过
    （Windows 上两者是**同一个物理目录**）。判据：**必须判 True**。
    """
    lowered = Path(str(REPO_ROOT).replace("NovelOS", "novelos"))
    assert guards.is_inside_repo(lowered) is True, (
        f"大小写变体被判成仓外 ⇒ 仓内路径可绕过 B2：{lowered}"
    )
    assert guards.is_inside_repo(lowered / ".b1_probe_lower") is True


def test_repo_internal_basetemp_with_lowercase_drive_letter_is_rejected() -> None:
    """★M1 DoD（端到端）：大小写变体写法的**仓内** basetemp 必须被拒（exit 4）。"""
    lowered = Path(str(REPO_ROOT).replace("NovelOS", "novelos"))
    raw = str(lowered / ".b1_probe_lower")
    probe = REPO_ROOT / ".b1_probe_lower"
    try:
        proc = _run_pytest(
            ["tests/unit/test_config.py", "-q", f"--basetemp={raw}"],
            cwd=REPO_ROOT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 4, (
            f"大小写变体的仓内 basetemp 必须被拒（exit 4），实际 {proc.returncode}：\n{output}"
        )
        assert "--basetemp" in output
        assert not probe.exists(), "守卫必须在建目录之前失败，仓内不得留残留目录"
    finally:
        if probe.exists():  # 守卫失效时的兜底清理（自建探针，非仓内既有内容）
            shutil.rmtree(probe, ignore_errors=True)


def test_external_basetemp_passes_end_to_end() -> None:
    """对照：仓外 ``--basetemp`` ⇒ 同一命令放行（跑完清理）。"""
    external = Path(tempfile.gettempdir()) / f"novelos_b1_e2e_{os.getpid()}"
    external.mkdir(parents=True, exist_ok=True)
    try:
        proc = _run_pytest(
            ["tests/unit/test_config.py", "-q", f"--basetemp={external}"],
            cwd=REPO_ROOT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, f"仓外 basetemp 竟然失败了：\n{output}"
        assert " passed" in output, f"子进程没有真正跑起来：\n{output}"
    finally:
        shutil.rmtree(external, ignore_errors=True)


# ---------------------------------------------------------------------------
# B1：密钥隔离
# ---------------------------------------------------------------------------


def test_repo_secrets_are_not_readable_in_test_process() -> None:
    """★测试进程内 ``load_api_keys()`` 恒空 ⇒ 读不到仓根真实凭据。"""
    from packages.core.secrets_store import load_api_keys

    assert load_api_keys() == {}


def test_secrets_fallback_path_is_sandbox_not_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """★env 缺失时的**默认解析路径**也被重定向（不是靠删 delenv 蒙混）。

    ⚠️ 断言钉到**路径**（落在会话级临时基址下），不只断言"不在仓根"：
    ``_resolve_secrets_path()`` 是恒真返回默认路径（``secrets_store.py:56-57``
    的 ``return default if default.exists() else default``）⇒ **不许断言 None**。
    """
    from packages.core import secrets_store

    monkeypatch.delenv("NOVELOS_SECRETS_FILE", raising=False)
    sandbox = guards.SECRETS_SANDBOX_PATH
    assert sandbox is not None, "沙箱基址未登记 ⇒ 回落基址根本没被重定向"
    resolved = secrets_store._resolve_secrets_path()
    # ⚠️ 不断言 ``resolved is not None``：``secrets_store.py:56-57`` 是恒真分支
    # （恒返回默认路径）⇒ 该断言零判别力。改成**钉到沙箱目录**。
    assert resolved == sandbox / "secrets.json", f"回落路径未落在临时基址下：{resolved}"
    assert resolved.is_relative_to(sandbox), f"回落路径未落在 conftest 沙箱目录下：{resolved}"
    assert resolved.parent == sandbox, f"回落路径未落在 conftest 沙箱目录下：{resolved}"
    assert not guards.is_inside_repo(sandbox), "沙箱竟在仓内"
    assert REPO_ROOT not in resolved.parents, "回落基址仍指向仓根 ⇒ 真实凭据可达"
    assert not resolved.exists()
    assert secrets_store.load_api_keys() == {}


def test_real_repo_secrets_file_is_not_the_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """回落基址不得等于仓根 ``secrets.json``（防有人把沙箱又指回仓根）。

    ⚠️ 先 ``delenv`` ⇒ 断言走的是**回落基址**那条路（环境变量通道被击穿时的样子），
    免得这条用例被环境变量「顺手」满足（假绿）。
    """
    from packages.core import secrets_store

    monkeypatch.delenv("NOVELOS_SECRETS_FILE", raising=False)
    repo_secrets = REPO_ROOT / "secrets.json"
    sandbox = guards.SECRETS_SANDBOX_PATH
    assert sandbox is not None
    resolved = secrets_store._resolve_secrets_path()
    assert resolved == sandbox / "secrets.json", f"回落路径未落在 conftest 沙箱目录下：{resolved}"
    assert resolved != repo_secrets
    assert REPO_ROOT not in resolved.parents


def test_fallback_reads_synthetic_key_when_base_not_redirected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★判别力自证（**不依赖本机真凭据、不拷真文件**）：闸若不在，仓根文件读得出键。

    做法：在仓外临时目录造一个**合成**仓根（``<fake>/secrets.json`` 只含假 key
    ``mprof_fake_probe`` / ``sk-fake-probe``）⇒ 把回落基址指到它
    （= 撤掉 ``tests/conftest.py`` 的重定向）⇒ ``load_api_keys()`` 必然读出那个假 key
    ⇒ 证明「闸撤掉 ⇒ 凭据可见」这条因果是真的 ⇒ 守卫态下的 ``== {}`` 断言
    **不是恒真**（CI 上没有真凭据时也能判别）。

    ⚠️ 绝**不**拷仓根真 ``secrets.json``：这里只造合成文件。
    """
    from packages.core import secrets_store

    fake_root = tmp_path / "fake_repo_root"
    fake_root.mkdir()
    (fake_root / "secrets.json").write_text(
        json.dumps({"api_keys": {"mprof_fake_probe": "sk-fake-probe"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_store, "_repo_root", lambda: fake_root)
    monkeypatch.delenv("NOVELOS_SECRETS_FILE", raising=False)
    secrets_store.reset_cache()
    try:
        got = secrets_store.load_api_keys()
        assert got == {"mprof_fake_probe": "sk-fake-probe"}, (
            f"闸撤掉后必须读得出假 key（否则上面那条 == {{}} 是恒真断言）：{got}"
        )
    finally:
        secrets_store.reset_cache()


def test_guard_script_secrets_path_is_not_repo_root() -> None:
    """★第二套独立回落实现（``scripts/_guard_secrets.py:80-92``）也读不到仓根。

    该模块**不经过** ``secrets_store``，自己按「显式参数 → ``NOVELOS_SECRETS_FILE``
    → 仓根」解析 ⇒ 本批用**它自己的 env 通道**封住（conftest 已指到沙箱）。
    """
    import importlib

    module = importlib.import_module("scripts._guard_secrets")
    sandbox = guards.SECRETS_SANDBOX_PATH
    assert sandbox is not None
    resolved = module._resolve_secrets_path()
    assert resolved == sandbox / "secrets.json", f"守卫脚本仍会读仓根凭据文件：{resolved}"
    assert REPO_ROOT not in resolved.parents
    assert not resolved.exists()


@pytest.mark.xfail(
    reason=(
        "scripts/_guard_secrets.py:80-92 的**回落分支**仍指向仓根 secrets.json"
        "（生产代码，不在本批写域）⇒ 省略显式路径且环境变量被删时会读仓根。"
        "strict=True ⇒ 修好后本条会 XPASS⇒变红，逼着移除这个标记。"
        "到期：本批之后的 B3 债偿还，未还则下批门禁直接判红。"
        "⚠️ 命名消歧：此处的「B3 债」= `scripts/_guard_secrets.py` 回落分支封口这一笔债，"
        "≠ 本文件 :1 与 :453-480 的「B3 出网机制」（socket 出网守卫），两者互不相关。"
    ),
    strict=True,
    raises=AssertionError,
)
def test_guard_script_fallback_branch_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """★把「守卫脚本回落分支仍指向仓根」这条**已知洞**钉成必须偿还的债。"""
    import importlib

    monkeypatch.delenv("NOVELOS_SECRETS_FILE", raising=False)
    module = importlib.import_module("scripts._guard_secrets")
    resolved = module._resolve_secrets_path()
    # ⚠️ 不断言 ``resolved is not None``（恒真、零判别力）⇒ 断言「不落在仓根」。
    assert not resolved.is_relative_to(REPO_ROOT), f"守卫脚本回落分支仍指向仓根：{resolved}"


def test_repo_external_copy_dual_observation() -> None:
    """★第 ③ 条：仓外**整份副本** + 合成假凭据 ⇒ **红绿两段**观测。

    - **副本**放仓外（``%TEMP%``），并**整份拷** ``packages/``：
      ``secrets_store._repo_root()`` = ``Path(__file__).resolve().parents[2]``
      （``packages/core/secrets_store.py:45-48``）靠**物理位置**定位仓根 ⇒ 只拷
      ``tests/``+``scripts/`` 会 import 失败、判别无效（与「C-3 假红」同型）。
    - **拷** ``tests/conftest.py`` 进副本 ⇒ 副本的 ``REPO_ROOT`` 指向副本根，
      沙箱与副本根互不重叠，观测才有意义。
    - **合成** ``secrets.json`` 放副本根：键名 ``mprof_synthetic_probe``、值
      ``sk-SYNTHETIC-*`` ⇒ **不拷**仓根真凭据，报告里只出现键名。
    - **绿段**（副本内 pytest，守卫生效）⇒ ``load_api_keys() == {}`` 且 resolved
      落在 conftest 沙箱目录下；**红段**（进程外探针，无 fixture 生效）⇒ 读得到
      合成 key ⇒ **差值即守卫的贡献**（只有红段没有绿段 ⇒ 分不清是谁挡的）。
    """
    copy_root = Path(tempfile.gettempdir()) / f"novelos_b1_probe_{os.getpid()}"
    if copy_root.exists():
        shutil.rmtree(copy_root, ignore_errors=True)
    copy_root.mkdir(parents=True)
    try:
        # 整份拷 packages/（含 packages/core/secrets_store.py 与其相对导入）
        shutil.copytree(REPO_ROOT / "packages", copy_root / "packages")
        # 拷 tests/conftest.py（副本的 REPO_ROOT 指向副本根）
        (copy_root / "tests").mkdir()
        shutil.copy2(REPO_ROOT / "tests" / "conftest.py", copy_root / "tests" / "conftest.py")
        (copy_root / "tests" / "unit").mkdir()
        # 合成凭据（假 key；绝不拷真文件）
        (copy_root / "secrets.json").write_text(
            json.dumps({"api_keys": {"mprof_synthetic_probe": "sk-SYNTHETIC-probe"}}),
            encoding="utf-8",
        )
        # 副本内的探针用例（跑在副本的 conftest 之下 ⇒ 守卫生效）
        (copy_root / "tests" / "unit" / "test_b1_copy_probe.py").write_text(
            "def test_copy_guard_active():\n"
            "    from packages.core.secrets_store import load_api_keys, _resolve_secrets_path\n"
            "    assert load_api_keys() == {}\n"
            "    resolved = _resolve_secrets_path()\n"
            "    assert 'novelos_secrets_sandbox' in str(resolved)\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(copy_root)
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_CURRENT_TEST", None)
        # ⇒ 强制走**回落基址**那条路（env 通道被清掉 ⇒ 证明副本的合成文件在默认路径上）
        env.pop("NOVELOS_SECRETS_FILE", None)

        # 绿段：副本内 pytest（守卫生效 ⇒ 读不到合成 key）
        green = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/test_b1_copy_probe.py", "-q",
             "-p", "no:cacheprovider"],
            cwd=str(copy_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        assert green.returncode == 0, f"绿段应通过：\n{green.stdout}\n{green.stderr}"

        # 红段：进程外探针（无 fixture 生效 ⇒ 读得到合成 key）
        red = subprocess.run(
            [sys.executable, "-c",
             "import json,sys;sys.path.insert(0,sys.argv[1]);"
             "from packages.core.secrets_store import load_api_keys;"
             "print(json.dumps(sorted(load_api_keys().keys())))",
             str(copy_root)],
            cwd=str(copy_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
        assert red.returncode == 0, f"红段探针失败：\n{red.stdout}\n{red.stderr}"
        keys = json.loads(red.stdout.strip().splitlines()[-1])
        assert keys == ["mprof_synthetic_probe"], (
            f"红段必须读得到合成 key（否则副本的默认路径没生效 ⇒ 判别无效）：{keys}"
        )
    finally:
        shutil.rmtree(copy_root, ignore_errors=True)


def test_ambient_api_keys_are_scrubbed() -> None:
    """会话启动前就存在的真实 key 环境变量必须已被清掉。

    ⚠️ 判别力要求：本机本来就没有 key 时，「已清掉的清单」是空的 ⇒ 空断言假绿。
    所以这里断言的是**进程内不存在任何 API_KEY 类环境变量**（含清点清单的二次核对）。
    """
    leftovers = [name for name in os.environ if "API_KEY" in name.upper()]
    assert not leftovers, f"真实凭据环境变量仍在进程内：{leftovers}"
    for name in guards.SCRUBBED_ENV_NAMES:
        assert name not in os.environ, f"真实凭据环境变量仍在进程内：{name}"


def test_tests_can_still_inject_fake_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """用例内 ``monkeypatch.setenv`` 注入假 key 的能力必须保留。"""
    from packages.core.secrets_store import resolve_profile_api_key

    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-fake-for-test")
    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key=None,
    )
    assert got == "sk-fake-for-test"


# ---------------------------------------------------------------------------
# B3：出网机制（本批不启用 autouse）
# ---------------------------------------------------------------------------


def test_socket_guard_is_not_installed_by_default() -> None:
    """★本批只提供机制：默认状态下出网**未被**拦截（留给 B3 启用）。"""
    assert socket.socket.connect is guards._ORIGINAL_SOCKET_CONNECT
    assert socket.create_connection is guards._ORIGINAL_CREATE_CONNECTION


def test_socket_guard_blocks_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """机制自检：装上后，非 loopback 连接被拦（且**不发出任何真实流量**）。"""
    guards.install_socket_guard(monkeypatch)
    with pytest.raises(RuntimeError, match="出网"):
        socket.create_connection(("203.0.113.1", 80), timeout=1)


def test_socket_guard_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """机制自检：loopback 放行（本地端口用例要 bind + 连）。"""
    guards.install_socket_guard(monkeypatch)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
    finally:
        server.close()


def test_mock_transport_survives_socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """机制自检：``httpx.MockTransport`` 不开 socket ⇒ 不被误杀。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    guards.install_socket_guard(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resp = client.get("https://api.example.com/v1/models")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# B4：DB 沙箱（BUG-26 · ``tests/conftest.py:227`` 的 autouse ``_isolate_repo_db``）
#
# 判别目标：把 ``tests/conftest.py:227`` 的 ``autouse=True`` 撤掉 ⇒ 本组三条必须**全红**
# （**FAILED**，非 ERROR）。闸不在位时 ``get_settings()`` 按 CWD 解析出**仓内** ``./data``
# （``packages/core/config.py:59`` ＋ ``:85-87``）⇒ 漏写 ``tmp_path`` 的用例会建库到生产
# 真库 ``data/novelos.db`` ⇒ 守卫若无人看管，本组必须能变红。
#
# ⚠️ 本组三条**只读**：只比对路径，绝不建库、绝不写仓内 ``data/``（真库一个字节都不动）。
# ⚠️ 相对路径按 CWD 解析 ⇒ 判「仓内/仓外」前**必须**先按 CWD 展开（否则假绿）。
# ---------------------------------------------------------------------------


def test_db_sandbox_redirects_settings_outside_repo() -> None:
    """★B4 判别：测试进程里 ``get_settings()`` 的**数据根与库路径必须落仓外**。

    - 闸在位时（``tests/conftest.py:255-257`` 注入 ``NOVELOS_DATA_DIR`` ＋
      ``NOVELOS_DB_PATH``）⇒ 两者都是**绝对路径**且指向仓外沙箱。
    - 闸不在位时 ``data_dir`` 是**相对**的 ``Path(".") / "data"``
      （``packages/core/config.py:59`` ＋ ``:91``）⇒ **相对路径按 CWD 解析**
      （``config.py:85-87``）⇒ 判定前必须按 CWD 展开，否则「看着像仓外」会假绿。
    """
    from packages.core import config as config_mod

    settings = config_mod.get_settings()
    data_dir = Path(str(settings.data_dir))
    db_path = Path(str(settings.db_path))
    resolved_data = data_dir.resolve() if data_dir.is_absolute() else (Path.cwd() / data_dir).resolve()
    resolved_db = db_path.resolve() if db_path.is_absolute() else (Path.cwd() / db_path).resolve()
    assert not guards.is_inside_repo(resolved_data), (
        f"数据根落仓内 ⇒ B4 守卫没生效（撤 autouse 后此处必红）：{resolved_data}"
    )
    assert not guards.is_inside_repo(resolved_db), (
        f"库路径落仓内 ⇒ B4 守卫没生效（撤 autouse 后此处必红）：{resolved_db}"
    )


def test_db_sandbox_injects_data_env_vars() -> None:
    """★B4 判别：两个沙箱环境变量必须**已注入**，且都落仓外。

    ⚠️ 一律写成 ``assert 名字 in os.environ``（不直接下标取）⇒ 闸不在位时呈现为
    **FAILED**（AssertionError）而不是 **ERROR**（KeyError）⇒ 不撞
    ``AGENTS.md:418`` 的 INVALID 坑。
    """
    from packages.core.config import Settings

    assert "NOVELOS_DATA_DIR" in os.environ, "NOVELOS_DATA_DIR 未注入 ⇒ B4 守卫（autouse）没生效"
    assert "NOVELOS_DB_PATH" in os.environ, "NOVELOS_DB_PATH 未注入 ⇒ B4 守卫（autouse）没生效"
    data_env = Path(os.environ["NOVELOS_DATA_DIR"])
    db_env = Path(os.environ["NOVELOS_DB_PATH"])
    assert not guards.is_inside_repo(data_env), f"沙箱数据根落仓内：{data_env}"
    assert not guards.is_inside_repo(db_env), f"沙箱库路径落仓内：{db_env}"
    assert db_env.name == Settings.DEFAULT_DB_NAME, (
        f"库文件名与生产默认名不一致（真源：config.py:47）：{db_env.name}"
    )


def test_db_sandbox_registers_sandbox_and_keeps_repo_db_untouched() -> None:
    """★B4 判别：守卫必须**登记**沙箱家，且有效库路径必须**落在该沙箱内**。

    - ``guards.DB_SANDBOX_PATH``（``tests/conftest.py:255``）是守卫自报的家 ⇒
      闸不在位时它恒为 ``None`` ⇒ 本条必红。
    - 有效库路径的**父目录必须就是沙箱目录** ⇒ 证明重定向真把库落到了沙箱，
      而不只是「改了环境变量」。
    - 仓内生产真库 ``data/novelos.db`` **一个字节都不动** ⇒ 这里只做**路径比对**，
      绝不建库、绝不写文件。
    """
    from packages.core import config as config_mod

    sandbox = guards.DB_SANDBOX_PATH
    assert sandbox is not None, "B4 守卫未登记 DB_SANDBOX_PATH ⇒ autouse fixture 没有生效"
    sandbox_path = Path(str(sandbox))
    assert not guards.is_inside_repo(sandbox_path), f"沙箱根落仓内：{sandbox_path}"

    settings = config_mod.get_settings()
    raw_db = Path(str(settings.db_path))
    assert raw_db.parent == sandbox_path, (
        f"有效库路径的父目录不是沙箱目录（{raw_db}，沙箱 {sandbox_path}）"
    )
    resolved_db = raw_db.resolve()
    repo_data = (REPO_ROOT / "data").resolve()
    assert not resolved_db.is_relative_to(repo_data), (
        f"有效库路径落在仓内 data/ 下 ⇒ 会写生产真库：{resolved_db}"
    )
    assert resolved_db != (repo_data / config_mod.Settings.DEFAULT_DB_NAME), (
        f"有效库路径竟指向仓内生产真库：{resolved_db}"
    )


def test_bug16_dead_code_removed() -> None:
    """BUG-16 判别：``secrets_store.has_api_key`` 全仓零调用点 ⇒ 清理后不得再导出/存在；
    ``wordcount._ERROR_RATIO`` 按 ``wordcount.py:35-42`` 纪律**必须保留**。

    ⚠️ 注释**说的是不是实话**由另一条用例判定
    （``test_bug63_wordcount_error_ratio_comment_says_the_truth``）——
    旧判据是「裸子串否定」（只挡一句旧文案）⇒ 换成别的不实文案照样绿 ⇒ 已改结构化守护
    （旧裸子串判据已作为**附加**断言恢复在那条用例里 ⇒ 两层判据并存）。
    """
    from packages.core import secrets_store
    from packages.core.quality import wordcount

    assert "has_api_key" not in secrets_store.__all__, "BUG-16 未清理：仍导出 has_api_key"
    assert not hasattr(secrets_store, "has_api_key"), "BUG-16 未清理：仍定义 has_api_key"
    assert hasattr(wordcount, "_ERROR_RATIO"), "wordcount._ERROR_RATIO 按 :35-42 纪律必须保留"


# ---------------------------------------------------------------------------
# BUG-63②：``wordcount.py`` 里 ``_ERROR_RATIO`` 注释的**结构化**守护
#
# ⚠️ 旧判据是「裸子串否定」（只挡「全仓 grep 只有本行定义」这一句旧文案）⇒
#    后人把注释换成**另一种不实文案**时依然绿 ⇒ 守卫形同虚设。
# ⇒ 现在改成**结构化守护**：把注释里的**可核查断言**（百分数归属、「对齐」说法的
#    否定上下文、「仓内代码零调用点」）逐条与**代码事实**对撞
#    ⇒ 换成任何一种不实文案都必红（判据与具体文案**解耦**）。
#
# ★ **零读取点纪律**：``_ERROR_RATIO`` **保留但不得被引用** —— 新增任何读取点
#   （哪怕只是一次取值／``getattr``）**必须先改本登记**（改口径或改纪律），
#   否则「仓内代码零调用点」这句注释会**当场变假**（⇒ 结构化守护必红）。
# ---------------------------------------------------------------------------

_WORDCOUNT_PATH = REPO_ROOT / "packages" / "core" / "quality" / "wordcount.py"

#: 百分数的**归属关键字**（就近归属：数字前**最近**的那个关键字决定它的含义）。
_NUM_OWNERS: tuple[tuple[str, str], ...] = (
    ("band 外缘偏离", "band"),  # band 外缘偏离的百分数
    ("本值", "error"),  # 本常量（_ERROR_RATIO）的百分数
)

#: 「对齐」这类**可能为假**的说法，只允许出现在**被否定**的上下文里
#: （否定的措辞必须在「对齐」二字的**近邻**（±60 字）内 ⇒ 不许拿别处的
#: 一句「不成立」来给一句无保留的「对齐」兜底）。
_NEGATION_MARKS = (
    "不符",
    "不成立",
    "并非",
    "不是",
    "不等于",
    "不对齐",
    "有误",
    "原注释",
    "旧注释",
    "订正",
    "已订正",
)


def _comment_block_above(path: Path, assign_prefix: str) -> list[str]:
    """取 ``<assign_prefix> = ...`` 赋值行**紧邻上方**的连续注释行（已剥掉 ``#``）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next(
        (i for i, line in enumerate(lines) if line.strip().startswith(assign_prefix)),
        None,
    )
    assert idx is not None, f"{path.name} 里找不到 `{assign_prefix} = ...` 赋值行"
    block: list[str] = []
    for line in reversed(lines[:idx]):
        stripped = line.strip()
        if stripped.startswith("#"):
            block.append(stripped.lstrip("#").strip())
            continue
        break
    return list(reversed(block))


def _percent_values(text: str) -> list[tuple[float, int]]:
    """抽出 ``(百分数值, 位置)``：``15%``／``15 %`` → 15.0；``0.15`` → 15.0（分数同义）。"""
    found: list[tuple[float, int]] = []
    for m in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%", text):
        found.append((float(m.group(1)), m.start()))
    for m in re.finditer(r"(?<![\d.])0\.(\d+)(?![\d]*%)", text):
        found.append((float("0." + m.group(1)) * 100.0, m.start()))
    return sorted(found, key=lambda item: item[1])


def _owner_for(text: str, pos: int) -> str | None:
    """数字 ``pos`` 之前**最近**的归属关键字（``band`` / ``error``）；没有则 ``None``。"""
    best: tuple[int, str] | None = None
    for keyword, owner in _NUM_OWNERS:
        at = text.rfind(keyword, 0, pos)
        if at >= 0 and (best is None or at > best[0]):
            best = (at, owner)
    return best[1] if best else None


def _identifier_references(roots: list[Path], name: str) -> list[str]:
    """列出 ``roots`` 下 ``.py`` 里**读取**该标识符的位置（``file:lineno``）。

    只认 **Load** 语境（赋值/定义不算引用）⇒ 「零调用点」这类断言才是真的；
    ``from … import <name>`` 也算引用（它是把名字带进本模块的真用法）。
    """
    found: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                src = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover - 非文本文件
                continue
            if name not in src:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:  # pragma: no cover - 非本仓源码
                continue
            rel = path.relative_to(REPO_ROOT)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                    found.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.Attribute) and node.attr == name:
                    found.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
                    found.append(f"{rel}:{node.lineno}")
    return found


# ---------------------------------------------------------------------------
# 存在性断言的**极性抽取**（供 BUG-63② 结构化守护用）
#
# ⚠️ 为什么要有这一层：只校验「代码侧事实（refs 为空）」时，注释侧**作出相反断言**
#   （如谎称「仍被多处代码引用」「有消费方」「存在调用点」）一律放行
#   ⇒ 守卫只钉住了「读侧」，没钉住「写侧」。
# ⇒ 这里把注释里的**存在性句子**抽出来并判**极性**（肯定式 / 否定式），
#   再与代码事实对撞 ⇒ 谎称与事实**极性相反**时必红。
# ---------------------------------------------------------------------------

#: 「存在性」断言的**主语**（引用／消费方／调用点／读取点）。
_EXISTENCE_SUBJECTS: tuple[str, ...] = ("引用", "消费方", "调用点", "读取点")

#: **文档侧**语境的标志（README／文字引用…）⇒ 文档引用**不是代码读取点**，
#: 不在判别面（残余边界，写在用例 docstring 里）。
_DOC_SCOPE_MARKS: tuple[str, ...] = ("README", "文字", "文档", "章节", "§", "spec")

#: **代码侧**语境的标志（出现即认为这句在谈**代码**的引用／消费方／调用点）。
_CODE_SCOPE_MARKS: tuple[str, ...] = ("代码", "仓内", "调用点", "读取点", "消费方")

#: 否定式措辞（零调用点／不再被引用／无消费方）。
_NEGATIVE_POLARITY: tuple[str, ...] = ("不再", "零", "无", "未")

#: 肯定式措辞（有／仍被／存在／被引用）。
_AFFIRMATIVE_POLARITY: tuple[str, ...] = ("仍被", "仍", "存在", "有", "被")

#: 否定式写法**归一化**（⇒ 归一后否定词里不再夹带肯定词，
#: 否则「没有消费方」会被「有」误判成肯定式 ⇒ 假红）。
_CLAUSE_NORMALIZATIONS: tuple[tuple[str, str], ...] = (
    ("不再存在", "不再"),
    ("不再被", "不再"),
    ("不再有", "不再"),
    ("没有被", "无"),
    ("没被", "无"),
    ("未被", "无"),
    ("不被", "无"),
    ("没有", "无"),
)

#: 主语**之后**也允许看一眼的近邻窗口（字）。
_POLARITY_LOOKAHEAD = 12


def _polarity_at(clause: str, pos: int) -> bool | None:
    """主语 ``pos`` 处的存在性极性。

    ⇒ ``True`` = 肯定式（有／仍被／存在）／``False`` = 否定式（零／不再被／无）
    ／``None`` = **判不出**（主语前后都没有极性措辞 ⇒ 该句跳过）。

    判据：主语**之前最近**的那个极性措辞（与本文件 ``_owner_for`` 同一口径：
    就近归属）；主语前判不出时才看主语**之后**的近邻窗口。
    """
    head = clause[:pos]
    neg = max((head.rfind(mark) for mark in _NEGATIVE_POLARITY), default=-1)
    aff = max((head.rfind(mark) for mark in _AFFIRMATIVE_POLARITY), default=-1)
    if neg < 0 and aff < 0:
        tail = clause[pos : pos + _POLARITY_LOOKAHEAD]
        neg_tail = min(
            (tail.find(mark) for mark in _NEGATIVE_POLARITY if tail.find(mark) >= 0),
            default=-1,
        )
        aff_tail = min(
            (tail.find(mark) for mark in _AFFIRMATIVE_POLARITY if tail.find(mark) >= 0),
            default=-1,
        )
        if neg_tail < 0 and aff_tail < 0:
            return None
        return aff_tail >= 0 and (neg_tail < 0 or aff_tail < neg_tail)
    if neg < 0:
        return True
    if aff < 0:
        return False
    return aff > neg


def _existence_claims(text: str) -> list[tuple[str, bool]]:
    """抽出注释里的**存在性断言句** ⇒ ``[(子句, 是否肯定式), ...]``。

    - **切句**：按换行与 ``；。，、`` 切（中文标点即天然的子句边界）；
    - **主语**：子句里出现「引用／消费方／调用点／读取点」；
    - **文档侧跳过**：子句只谈文档（README／文字…）⇒ 不在判别面；
    - **极性**：见 :func:`_polarity_at`（判不出 ⇒ 该句跳过）。
    """
    claims: list[tuple[str, bool]] = []
    for raw in re.split(r"[\n；。，、]", text):
        clause = raw.strip()
        if not clause:
            continue
        if not any(subject in clause for subject in _EXISTENCE_SUBJECTS):
            continue
        # 文档侧（README／文字…）且**不**谈代码 ⇒ 不在判别面。
        if any(mark in clause for mark in _DOC_SCOPE_MARKS) and not any(
            mark in clause for mark in _CODE_SCOPE_MARKS
        ):
            continue
        normalized = clause
        for old, new in _CLAUSE_NORMALIZATIONS:
            normalized = normalized.replace(old, new)
        for subject in _EXISTENCE_SUBJECTS:
            pos = normalized.find(subject)
            if pos < 0:
                continue
            polarity = _polarity_at(normalized, pos)
            if polarity is None:
                continue
            claims.append((clause, polarity))
            break  # 每个子句只登记**第一个**主语（避免同句多主语重复登记）
    return claims


def test_bug63_wordcount_error_ratio_comment_says_the_truth() -> None:
    """★ BUG-63② 判别：``wordcount.py`` 里 ``_ERROR_RATIO`` 的注释**必须说的是实话**。

    旧判据是「裸子串否定」（只挡一句旧文案）⇒ 后人换成**另一种不实文案**照样绿。
    现在按**代码事实**逐条对撞（判据与文案**解耦**）：

    1. 注释里出现的**每个百分数**必须落在两个真值之一（band 外缘偏离 % ／ 本常量 %）；
    2. 每个百分数的**归属**必须正确：``band 外缘偏离`` 上下文 ⇒ band %；
       ``本值`` 上下文 ⇒ 本常量 % ⇒「band 外缘偏离 = 30%」这类不实文案必红；
    3. 两个真值**都必须出现**（注释必须写明这条关系）；
    4. 「对齐」只允许出现在**被否定**的上下文里（不许出现无保留的「与 band 外缘对齐」）；
    5. 代码侧事实：``_ERROR_RATIO == 0.30``、band 外缘偏离 **恒等于** ``_WARNING_RATIO``、
       且 ``_ERROR_RATIO`` 在 ``packages/`` ＋ ``scripts/`` 内**零**读取点
       （注释里那句「仓内代码零调用点」必须为真）；
    6. ★ **存在性断言的极性绑定**（本轮补的判别力缺口）：注释里
       「引用／消费方／调用点」类句子按**极性**与代码事实对撞 ——
       **肯定式**（有／仍被／存在）而实际零读取点 ⇒ **必红**；
       **否定式**（零调用点／不再被引用／无消费方）而实际有读取点 ⇒ **必红**；
       ⇒ 「仍被多处代码引用」「有消费方」「存在调用点」这类**换个说法的谎称**
       不再是判别盲区（此前只校验代码侧事实 ⇒ 注释侧说反话一律放行）；
    7. ★ **附加旧子串判据**（本轮恢复）：已知不实文案「全仓 grep 只有本行定义」
       不许再出现在源码里（它讲的是「全仓」这一**存在性**，但主语不是
       「引用／消费方／调用点」⇒ 第 6 条覆盖不到 ⇒ 需单列一条）。

    ⚠️ **残余边界**（如实登记，不假装覆盖全）：
    ① **非存在性类**的不实文案**不在判别面**（例：把「经验值（未经语料校准）」
       改成「实测分位校准」——本守卫**不会**因此变红）；
    ② **文档侧**引用（README／文字引用）不是代码读取点 ⇒ 不在判别面；
    ③ 主语前后都判不出极性的句子**跳过**；
    ④ 百分比／归属／「对齐」三类断言各有自己的判别面（见上面 1~4 条）；
    ⑤ 注释里**必须**至少有一条存在性陈述（否则「仓内代码零调用点」这条纪律被整段
       删掉 ⇒ 第 6 条会**静默失效** ⇒ 本守卫直接红，不让判别面被悄悄缩小）。
    """
    from packages.core.quality import wordcount

    # —— 代码侧事实 ——
    error_pct = float(wordcount._ERROR_RATIO) * 100.0
    warn_pct = float(wordcount._WARNING_RATIO) * 100.0
    cfg = wordcount.resolve_band_config(None)
    band_dev_pct = (float(cfg["high_ratio"]) - 1.0) * 100.0
    assert math.isclose(band_dev_pct, warn_pct), (
        f"band 外缘偏离实为 {band_dev_pct}%（由 high_ratio 推导）"
        f"⇒ 与 _WARNING_RATIO（{warn_pct}%）不符"
    )
    assert not math.isclose(error_pct, band_dev_pct), (
        f"_ERROR_RATIO（{error_pct}%）竟与 band 外缘偏离（{band_dev_pct}%）相等"
        f"⇒ 注释里「与 band 外缘对齐」的说法会变真"
    )

    # —— 代码侧：零读取点（极性绑定的**事实基准**）——
    refs = _identifier_references(
        [REPO_ROOT / "packages", REPO_ROOT / "scripts"], "_ERROR_RATIO"
    )

    # —— 注释侧：逐条对撞 ——
    block = _comment_block_above(_WORDCOUNT_PATH, "_ERROR_RATIO")
    assert block, "wordcount.py 里 _ERROR_RATIO 上方没有注释块 ⇒ 纪律注释被删"
    text = " ".join(block)

    values = _percent_values(text)
    assert values, "注释里没有任何百分数 ⇒ 没写明「本值 / band 外缘偏离各是多少」"
    for value, pos in values:
        assert math.isclose(value, error_pct) or math.isclose(value, band_dev_pct), (
            f"注释里的百分数 {value}% 不是任何真值（本常量 {error_pct}% / "
            f"band 外缘偏离 {band_dev_pct}%）⇒ 不实文案"
        )
        owner = _owner_for(text, pos)
        if owner == "band":
            assert math.isclose(value, band_dev_pct), (
                f"band 外缘偏离实为 {band_dev_pct}%（由 _WARNING_RATIO 推导）"
                f"⇒ 注释写成 {value}% 不实"
            )
        elif owner == "error":
            assert math.isclose(value, error_pct), (
                f"_ERROR_RATIO 实为 {error_pct}% ⇒ 注释写成 {value}% 不实"
            )
    assert any(math.isclose(v, error_pct) for v, _ in values), (
        f"注释必须写明本常量的百分数（{error_pct}%）"
    )
    assert any(math.isclose(v, band_dev_pct) for v, _ in values), (
        f"注释必须写明 band 外缘偏离的百分数（{band_dev_pct}%）"
    )

    for match in re.finditer("对齐", text):
        window = text[max(0, match.start() - 60) : match.end() + 60]
        assert any(mark in window for mark in _NEGATION_MARKS), (
            f"「对齐」必须写在被否定的上下文里（否则等于宣称本常量与 band 外缘偏离相等）：{window!r}"
        )

    # —— 注释里那句「仓内代码零调用点」必须为真 ——
    assert not refs, (
        f"_ERROR_RATIO 在仓内代码出现读取点（注释称零调用点）：{refs}"
    )

    # —— 注释侧：存在性断言的**极性绑定** ——
    # 肯定式谎称（「仍被多处代码引用」「有消费方」「存在调用点」）而事实是零读取点 ⇒ 必红；
    # 否定式（「零调用点」「不再被引用」「无消费方」）而事实有读取点 ⇒ 必红。
    claims = _existence_claims("\n".join(block))
    assert claims, (
        "注释里**找不到**任何「引用／消费方／调用点」类的存在性陈述"
        "（⇒ 「仓内代码零调用点」这条纪律被整段删掉了？⇒ 极性绑定会**静默失效**）"
    )
    for clause, affirmative in claims:
        if affirmative:
            assert refs, (
                f"注释里出现**肯定式**存在断言（称有引用／消费方／调用点），"
                f"但代码事实是**零读取点** ⇒ 谎称：{clause!r}"
            )
        else:
            assert not refs, (
                f"注释里出现**否定式**存在断言（称零调用点／不再被引用），"
                f"但代码事实**有**读取点 ⇒ 谎称：{clause!r} ⇒ 读取点={refs}"
            )

    # —— 附加：旧子串判据（已知不实文案不许再出现）——
    src = _WORDCOUNT_PATH.read_text(encoding="utf-8")
    assert "全仓 grep 只有本行定义" not in src, (
        "wordcount.py 的注释又写回「全仓 grep 只有本行定义」这句不实文案"
        "（README §13.2 仍有文字引用 ⇒ 「全仓只有本行」不成立）"
    )
