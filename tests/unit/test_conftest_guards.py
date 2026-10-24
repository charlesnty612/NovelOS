"""``tests/conftest.py`` 三道守卫的判别用例（B1 密钥隔离 / B2 basetemp 硬失败 / B3 出网机制）。

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

import json
import os
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
