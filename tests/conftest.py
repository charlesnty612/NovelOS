"""NovelOS 测试全局守卫（pytest conftest）。

本文件是**测试基础设施**（不属产品代码），落地四道守卫：

1. **basetemp 硬失败（B2）**：``--basetemp`` 指向**仓内** ⇒ 直接 ``UsageError``；
   未显式指定 ⇒ 统一落到系统 Temp 下的**本次运行唯一**目录（仓外、可写、跑完清理）。
   依据：``AGENTS.md:270`` —— 仓内 basetemp 会让写守卫类用例集体假红，
   并污染 tree_fingerprint 扫描面 ⇒ 必须在**配置期**就拒绝，不能只靠文档提醒。

2. **密钥隔离（B1）**：把 ``secrets_store`` 的**回落基址**重定向到会话级临时目录
   ⇒ 测试进程内 ``load_api_keys()`` 恒为 ``{}``，仓根真实 ``secrets.json`` 读不到。
   依据：``packages/core/secrets_store.py:51-57`` —— ``NOVELOS_SECRETS_FILE`` 缺省时
   回落**仓根** ``secrets.json``；``tests/unit/test_secrets_store.py`` 的 autouse
   ``_isolate`` 还会主动 delenv ⇒ 默认路径原本**可达**。
   ⚠️ 本守卫**不改** ``packages/`` 生产代码，只在测试层 monkeypatch 回落基址；
   ⚠️ 仓根 ``secrets.json`` 与 ``secrets.json.bak-*`` **一个字不动**（铁律 8：
   只封读取路径，不消灭文件）。

3. **出网守卫（B3，本批只提供机制、不启用 autouse）**：``install_socket_guard()``
   拦截非 loopback 的出网连接；放行 loopback（本地端口用例需要），
   ``httpx.MockTransport`` 不开 socket ⇒ mock 链路不受影响。
   ⚠️ 本批**不**把封网挂成 autouse（波及面过大），留待 B3 单独一批启用。

4. **DB 沙箱（B4，2026-10-25 · OS开发 W1 批 · BUG-26）**：把 ``Settings`` 的
   **数据根与库路径**重定向到**仓外临时目录** ⇒ 用例**漏写** ``tmp_path``
   也**不会**写到生产真库 ``data/novelos.db``。
   依据：``packages/core/config.py`` 的 ``data_dir`` 默认是**相对路径**
   ``./data``（按 CWD 解析）⇒ 测试进程的 CWD 通常是仓根 ⇒ 漏写 ``tmp_path``
   的用例会把库建在**仓内** ``data/``（生产真库所在处）。
   ⚠️ 本守卫**不改** ``packages/`` 生产代码的行为分支，只在**测试层**注入
   ``NOVELOS_DATA_DIR`` ＋ ``NOVELOS_DB_PATH`` 两个环境变量并**重置单例**；
   ⚠️ 用例若**自己**显式 ``setenv``（如 ``test_migrate_cli.py``）⇒ 它的显式值
   **优先**（monkeypatch 作用域内），守卫不夺权 ⇒ 既有隔离语义不受影响。
   ⚠️ 仓内真库 ``data/novelos.db`` **一个字不动**（只改「测试进程读到的路径」，
   不动文件本身）。

四道守卫的判别用例在 ``tests/unit/test_conftest_guards.py``。
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

#: 仓库根（``tests/conftest.py`` → 上溯一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本守卫自建 basetemp 的目录名（仓外，系统 Temp 之下）。
_BASETEMP_DIRNAME = "novelos_pytest_basetemp"

#: 环境变量名里出现这些片段 ⇒ 视为「可能装真凭据」，会话启动时清掉。
_SECRET_ENV_MARKER = "API_KEY"

#: 「上一次运行删不净」的标记文件（写在 basetemp 根里 ⇒ 下次运行据此清账）。
_STALE_MARKER = ".novelos_stale"

#: 未被守卫替换时的原始实现（用于判别「本轮未启用 autouse 出网守卫」）。
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_CREATE_CONNECTION = socket.create_connection

#: 会话启动时被清掉的真实 key 环境变量名（供判别用例断言）。
SCRUBBED_ENV_NAMES: list[str] = []

#: 会话级「假 secrets 根」的实际路径（判别用例据此断言「落在临时基址下」）。
SECRETS_SANDBOX_PATH: Path | None = None


# ---------------------------------------------------------------------------
# B2：basetemp 守卫
# ---------------------------------------------------------------------------


def is_inside_repo(path: Path) -> bool:
    """仓内外判定：**委托**仓内唯一实现（``scripts/guard_repo_write_path.py:251``）。

    ⚠️ **不自己再实现一遍**（F-7 同型缺陷，``tests/unit/test_scripts_write_guard.py:454``
    已记过这个形状）：生产侧对 Windows 路径形状（``\\\\?\\`` 扩展长度前缀、UNC 别名
    拼写、尾随空格）做了规范化 + 底层身份（``st_dev``/``st_ino``）复核；自写一份会
    与之**分叉** ⇒ 仓内路径被判成「仓外」⇒ **B2 basetemp 守卫被绕过**
    （仓内 basetemp ⇒ 50 条集体假红，``AGENTS.md:270``）⇒ 本批就是「守卫只交了一半」。

    ⚠️ 判不出来（导入失败 / ``OSError``）⇒ **fail closed**（当仓内 ⇒ 拒绝）：
    守卫宁可误拒，不可误放。
    """
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from scripts.guard_repo_write_path import is_inside_repo as _canonical
    except Exception:  # pragma: no cover - 生产侧不可导入 ⇒ fail closed
        return True
    try:
        return bool(_canonical(path, REPO_ROOT))
    except OSError:  # pragma: no cover - 路径不可解析 ⇒ fail closed
        return True


def default_basetemp() -> Path:
    """默认 basetemp：系统 Temp 下的**本次运行唯一**目录（仓外）。

    带 PID ⇒ 并行跑测互不覆盖（pytest 启动时会清空 basetemp，共用会互相踩）。
    """
    return Path(tempfile.gettempdir()) / f"{_BASETEMP_DIRNAME}_{os.getpid()}"


def enforce_external_basetemp(config: Any) -> None:
    """B2 守卫本体：仓内 basetemp ⇒ 硬失败；未指定 ⇒ 落仓外唯一目录。

    - 显式给了仓外路径 ⇒ 原样放行（调用方自选，尊重既有工作流，**不覆盖**）。
    - 显式给了仓内路径 ⇒ ``pytest.UsageError``（退出码 4），**在建目录之前**失败，
      仓内不留任何残留目录。
    """
    given = config.getoption("basetemp", None)
    if given:
        raw = Path(str(given)).expanduser()
        target = raw if raw.is_absolute() else (Path.cwd() / raw)
        if is_inside_repo(target):
            raise pytest.UsageError(
                f"--basetemp 指向仓内（{target}）：AGENTS.md:270 明令仓内 basetemp 会让"
                f"写守卫类用例集体假红，并污染 tree_fingerprint 扫描面。"
                f"请改用仓外目录，例如 --basetemp={default_basetemp()}"
            )
        config._novelos_basetemp_owned = False
        return

    target = default_basetemp()
    # pytest 内部 mkdir 不带 parents ⇒ 父目录先建出来。
    target.parent.mkdir(parents=True, exist_ok=True)
    # 上一次运行没清掉的残留 ⇒ 本次先清账（残留可确定性归零）。
    _purge_marked_stale(target)
    config.option.basetemp = str(target)
    config._novelos_basetemp_owned = True


# ---------------------------------------------------------------------------
# B1：密钥隔离
# ---------------------------------------------------------------------------


def scrub_ambient_api_keys() -> list[str]:
    """清掉**会话启动前**就存在的 API key 环境变量。

    只清「进程启动时就在」的（真实凭据）；用例内 ``monkeypatch.setenv`` 注入的
    假 key 不受影响（那是测试自己造的，作用域仅限该用例）。
    """
    global SCRUBBED_ENV_NAMES
    if SCRUBBED_ENV_NAMES:
        return list(SCRUBBED_ENV_NAMES)
    names = [name for name in os.environ if _SECRET_ENV_MARKER in name.upper()]
    for name in names:
        os.environ.pop(name, None)
    SCRUBBED_ENV_NAMES = names
    return list(names)


@pytest.fixture(scope="session")
def _secrets_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """会话级「假 secrets 根」：仓外临时目录，进程内不存任何真实凭据。"""
    return tmp_path_factory.mktemp("novelos_secrets_sandbox")


@pytest.fixture(autouse=True)
def _isolate_repo_secrets(
    _secrets_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """B1 守卫：把 ``secrets_store`` 的**回落基址**重定向到临时目录。

    - **闸建在回落基址**（``_repo_root()``）上，**不是**环境变量上：
      ``tests/unit/test_secrets_store.py`` 的 autouse ``_isolate`` 会主动
      ``delenv("NOVELOS_SECRETS_FILE")`` ⇒ 环境变量闸会被自己的夹具击穿，
      且新建的判别文件没有 ``_isolate`` ⇒ 环境变量方案在判别文件里照样绿（假绿）。
    - ⚠️ 第二套**独立**回落实现：``scripts/_guard_secrets.py:80-92`` 自己解析
      「显式参数 → ``NOVELOS_SECRETS_FILE`` → 仓根」，**不经过** ``secrets_store``
      ⇒ 只补 ``_repo_root`` 挡不住它。对它只能用**它自己的正规通道**（env 优先）。
    - **不删** ``test_secrets_store.py::_isolate`` 的 delenv 语义（它测的正是
      「env 缺失时的默认解析」）；改由本守卫把回落基址指向沙箱，于是「env 缺失」
      这条路仍被走到，但落点是沙箱而非仓根。
    - 换基址**前后**都 ``reset_cache()``：``secrets_store`` 有模块级 mtime 缓存
      （:64-68）⇒ 不清缓存会读到旧路径的键，判别用例照样假绿。
    - 仓根真实 ``secrets.json`` 不再被测试进程读到 ⇒ ``load_api_keys()`` 恒 ``{}``。
    - 用例内仍可用 ``monkeypatch.setenv`` 注入假 key（作用域仅限该用例）。
    - ⚠️ **fixture 只许做事、不许断言**（审查 2026-10-24）：断言写在 fixture 里，
      真红时呈现为 error 而非 FAIL ⇒ 撞 ``AGENTS.md:418`` 的 INVALID 坑 ⇒
      判别断言一律放 ``tests/unit/test_conftest_guards.py`` 的具体用例。
    """
    global SECRETS_SANDBOX_PATH
    from packages.core import secrets_store

    monkeypatch.setattr(secrets_store, "_repo_root", lambda: _secrets_sandbox)
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(_secrets_sandbox / "secrets.json"))
    SECRETS_SANDBOX_PATH = _secrets_sandbox
    secrets_store.reset_cache()
    try:
        yield
    finally:
        secrets_store.reset_cache()


# ---------------------------------------------------------------------------
# B4：DB 沙箱（BUG-26）
# ---------------------------------------------------------------------------


#: 会话级「假数据根」：仓外临时目录（DB 沙箱落点）。
DB_SANDBOX_PATH: Path | None = None

#: DB 沙箱里库文件名（与 ``Settings.DEFAULT_DB_NAME`` 同名，便于断言）。
DB_SANDBOX_DB_NAME = "novelos.db"


@pytest.fixture(scope="session")
def _db_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """会话级「假数据根」：仓外临时目录，测试进程内的库一律落这里。"""
    return tmp_path_factory.mktemp("novelos_db_sandbox")


@pytest.fixture(autouse=True)
def _isolate_repo_db(
    _db_sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """B4 守卫：把 ``Settings`` 的数据根与库路径重定向到**仓外沙箱**。

    - **为什么需要**（BUG-26）：``packages/core/config.py`` 的 ``data_dir`` 默认是
      **相对路径** ``./data``（按 CWD 解析）⇒ 测试进程的 CWD 通常是仓根 ⇒
      **漏写** ``tmp_path`` 的用例会建库在**仓内** ``data/``（＝生产真库所在处）。
    - **怎么做**：注入 ``NOVELOS_DATA_DIR`` ＋ ``NOVELOS_DB_PATH`` **两个**变量
      （前者定数据根、后者定库文件 ⇒ 与 BUG-14 的「归一」同源），
      并 ``reset_settings()`` 清掉**单例缓存** ⇒ 下一��� ``get_settings()``
      读到的是沙箱路径。
      ⚠️ **必须重置单例**：`get_settings()` 是全局单例（``config.py:97``），
      第一次访问后就缓存 ⇒ 不重置则沙箱注入**对已缓存的实例无效**。
    - **不夺权**：本守卫用 ``monkeypatch.setenv``（**函数级**作用域）⇒
      用例自己 ``setenv("NOVELOS_DATA_DIR", ...)``（如 ``test_migrate_cli.py``）
      时，**用例的值在用例作用域内优先** ⇒ 既有隔离语义**不受影响**；
      用例结束后 monkeypatch 自动还原 ⇒ 守卫的值在下一个用例重新生效。
    - ⚠️ **fixture 只许做事、不许断言**（审查 2026-10-24）：断言写在
      ``tests/unit/test_conftest_guards.py`` 的具体用例里。
    - ⚠️ **不动真库**：本守卫只改「测试进程读到的路径」，仓内
      ``data/novelos.db`` **一个字节都不动**（铁律：只封路径，不动文件）。
    """
    global DB_SANDBOX_PATH
    from packages.core import config as config_mod

    DB_SANDBOX_PATH = _db_sandbox
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(_db_sandbox))
    monkeypatch.setenv("NOVELOS_DB_PATH", str(_db_sandbox / DB_SANDBOX_DB_NAME))
    # 清单例 ⇒ 让本用例内的 get_settings() 重新按（已注入的）环境变量加载。
    config_mod.reset_settings()
    try:
        yield
    finally:
        # 归还单例：让后续用例重新加载（避免沙箱路径漏给下一个用例）。
        config_mod.reset_settings()


# ---------------------------------------------------------------------------
# B3：出网守卫（本批只提供机制，不启用 autouse）
# ---------------------------------------------------------------------------


def _host_of(address: Any) -> str:
    """从 ``connect`` 的 address 参数里取主机部分。"""
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _is_loopback(host: str) -> bool:
    """主机是否属于 loopback（放行范围）。"""
    normalized = host.strip().strip("[]").lower()
    if normalized in ("", "localhost", "::1", "0.0.0.0"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def install_socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """B3 机制：拦截非 loopback 的出网连接（**本批不启用 autouse**）。

    - 放行 loopback：``tests/integration/test_produce_chapters_e2e.py`` 需要
      bind + 连本地端口。
    - ``httpx.MockTransport`` 不开 socket ⇒ mock provider 链路不受影响。
    - 只拦「连出去」，不拦本地 bind/listen。
    """
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = _host_of(address)
        if not _is_loopback(host):
            raise RuntimeError(
                f"出网被测试守卫拦截（host={host}）：测试进程不得发起真实模型调用"
                f"（铁律 2）。请改用 mock provider。"
            )
        return original_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


# ---------------------------------------------------------------------------
# 残留清理
# ---------------------------------------------------------------------------


def _remove_tree(path: Path, attempts: int = 5) -> bool:
    """删一棵目录树；Windows 上偶发「句柄未释放」⇒ 退避重试。

    ⚠️ 不用 ``ignore_errors=True`` 一把梭：那样会把「删不掉」静默吞掉，
    留下半棵空目录树（实测：20 workers 时残留 2 个空 ``popen-gw*`` 目录）。

    :return: ``True`` = 已删净（或本来就不存在）。
    """
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            # ⚠️ 先「尽力删」：``ignore_errors=True`` 会跳过被占用的部分，
            # 把能删的先删掉 —— 否则一次失败就整棵留下（实测残留 4224 个文件）。
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                return True
            if attempt == attempts - 1:
                break
            time.sleep(0.2 * (attempt + 1))
    return not path.exists()


def _mark_stale(path: Path) -> None:
    """删不净时留一个标记 ⇒ **下一次运行**据此清账（残留可确定性归零）。"""
    try:
        (path / _STALE_MARKER).write_text("cleanup pending\n", encoding="utf-8")
    except OSError:  # pragma: no cover - 兜底路径
        pass


def _purge_marked_stale(keep: Path) -> None:
    """清掉上一次运行留下的「已标记未清掉」目录（标记只可能由已结束的会话写入）。"""
    root = Path(tempfile.gettempdir())
    for candidate in root.glob(f"{_BASETEMP_DIRNAME}_*"):
        if not candidate.is_dir() or candidate == keep:
            continue
        if (candidate / _STALE_MARKER).exists():
            if not _remove_tree(candidate):
                # 这次没删净 ⇒ 重新打标记 ⇒ 下次运行继续清账（不留下无标记的孤儿目录）。
                _mark_stale(candidate)


# ---------------------------------------------------------------------------
# pytest 钩子
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """配置期做两件硬事：basetemp 守卫 + 真实 key 环境变量清理。

    ⚠️ 必须在**测试模块 import 之前**跑完：``packages.core.api.main`` 在 import 期
    会把 ``MINIMAX_API_KEY`` 映射成 provider key（:43-60），环境变量必须先清。
    """
    enforce_external_basetemp(config)
    scrub_ambient_api_keys()
    config._novelos_effective_basetemp = str(config.option.basetemp)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """跑完清理本守卫**自建**的 basetemp（残留自证用；显式指定的目录不动）。

    ⚠️ 必须 ``trylast``：xdist 的 worker 子目录（``popen-gw*``）是在 **xdist 自己的
    ``pytest_sessionfinish``** 收尾阶段才落的 ⇒ 清理若先于它执行，父目录会被重新
    留下（实测：``novelos_pytest_basetemp_<pid>`` 残留两个空 worker 目录）。
    """
    config = session.config
    if not getattr(config, "_novelos_basetemp_owned", False):
        return
    path = getattr(config.option, "basetemp", None)
    if path:
        target = Path(str(path))
        if not _remove_tree(target):
            _mark_stale(target)


def pytest_unconfigure(config: pytest.Config) -> None:
    """兜底：session 收尾时若因句柄未释放没删干净，这里再试一次。"""
    if not getattr(config, "_novelos_basetemp_owned", False):
        return
    path = getattr(config.option, "basetemp", None)
    if path:
        target = Path(str(path))
        if not _remove_tree(target):
            _mark_stale(target)
