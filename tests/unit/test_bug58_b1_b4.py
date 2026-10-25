"""BUG-58-B1／B4 判别用例（OS开发B · 2026-10-25）。

覆盖：
- B1：调试产物落点（``NOVELOS_DEBUG_DIR``）默认**仓外绝对**；显式 env 仍被尊重。
- B1 源码守卫：旧的相对默认 ``"data"`` 必须消失。
  ⚠️ **形态口径（如实，BUG-66 订正）**：源码守卫**仅覆盖第 1 条用例**；第 2、3 条在旧字节下是
  ``AttributeError``，pytest 仍归类 **failed**（非 collection ERROR）⇒ 形态合格、
  判别力不受影响。
- B4：``scripts/m1_long_run.py`` 的 5 处裸连接**必须**在首个语句前开
  ``PRAGMA foreign_keys = ON``（⇒ 用假连接记录每条连接实际执行的语句，
  既验「开了」也验「顺序对」）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from packages.core.agent_runtime import runner


def _repo_root() -> Path:
    """仓根（本文件 ``tests/unit/test_bug58_b1_b4.py`` 上溯三级）。"""
    return Path(__file__).resolve().parents[2]


def _load_m1() -> ModuleType:
    """按文件路径加载 ``scripts/m1_long_run.py``（脚本目录非包 ⇒ 走 importlib）。"""
    name = "m1_long_run_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _repo_root() / "scripts" / "m1_long_run.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- B1
def test_bug58_b1_旧相对默认值已移除() -> None:
    """★ BUG-58-B1 判别（源码守卫）：旧的 ``NOVELOS_DEBUG_DIR`` 默认 ``"data"`` 必须消失。

    ★ 用**源码守卫**而非行为断言 ⇒ 旧字节下同样是 **FAILED**（缺陷存在），
    而不是 AttributeError（缺功能）⇒ 红段不会被误读。
    """
    src = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'NOVELOS_DEBUG_DIR", "data"' not in src, (
        "NOVELOS_DEBUG_DIR 仍默认相对 \"data\" ⇒ 调试产物落仓内 data/（BUG-58-B1）"
    )


def test_bug58_b1_调试目录默认仓外绝对(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ BUG-58-B1 判别：缺 ``NOVELOS_DEBUG_DIR`` ⇒ 落点**绝对**且**仓外**。"""
    monkeypatch.delenv("NOVELOS_DEBUG_DIR", raising=False)
    d = runner._debug_dir()
    assert d.is_absolute(), f"调试落点必须绝对化，实得 {d}"
    assert _repo_root() not in d.parents, f"调试落点落仓内 ⇒ 会脏仓内 data/：{d}"
    assert d.name == "debug"


def test_bug58_b1_显式env可覆盖且仍绝对(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ BUG-58-B1：显式给 ``NOVELOS_DEBUG_DIR`` ⇒ 尊重它（相对形态按 CWD 解析成绝对）。"""
    monkeypatch.setenv("NOVELOS_DEBUG_DIR", str(tmp_path / "dbg"))
    assert runner._debug_dir() == (tmp_path / "dbg").resolve()


# ---------------------------------------------------------------- B4
class _FakeConn:
    """记录每个连接实际执行的 SQL（假连接 ⇒ 不需要真库）。"""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.row_factory = None

    def execute(self, sql: str, *args: object, **kwargs: object) -> "_FakeConn":
        self.log.append(sql)
        return self

    def fetchall(self) -> list[object]:
        return []

    def fetchone(self) -> None:
        return None

    def close(self) -> None:
        pass


class _FakeSqlite3:
    """``sqlite3`` 模块替身：只换 ``connect``，其余属性（``Row`` 等）透传真模块。

    ⚠️ **作用域纪律（BUG-65）**：**不** patch 全局 ``sqlite3.connect``
    （那是**全进程共享**的属性 ⇒ 同用例内任何其它代码开连接都会被计进来 ⇒
    「全部连接数 == 5」这种**全局计数**断言随之误报；而某处少开一条连接又可能
    被别处的连接数掩盖 ⇒ 漏报）。
    改为替换 ``scripts/m1_long_run.py`` **自己模块内的** ``sqlite3`` 引用
    ⇒ 只截获被测模块的连接，进程内其它 ``sqlite3`` 使用者完全不受影响。
    """

    def __init__(self, real: ModuleType, connect: Any) -> None:
        self._real = real
        self.connect = connect

    def __getattr__(self, name: str) -> object:
        # 只透传未显式设置的属性（``Row`` / ``OperationalError`` 等）。
        return getattr(self._real, name)


#: 被测的 5 处裸连接（BUG-58-B4）：函数名 + 调用实参。
_B4_TARGETS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("_aggregate_ai_logs", ("db", ["run1"])),
    ("_latest_quality", ("db", "ch1")),
    ("_story_state", ("db", "prj1")),
    ("_latest_prose_chars", ("db", "ch1")),
    ("_latest_prose_word_stats", ("db", "ch1", 3000)),
)


def test_bug58_b4_五处连接均先开foreign_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ BUG-58-B4 判别：5 处裸连接**必须**在首个语句前开 ``PRAGMA foreign_keys = ON``。

    ★ 用**假连接**记录每条连接实际执行的语句 ⇒ 既验「开了」也验「顺序对」
    （SQLite 的 ``PRAGMA foreign_keys`` 在事务内**静默不生效** ⇒ 它必须是
    该连接的第一条语句 ⇒ 见 B4 卡点陷阱②）。

    ⚠️ **断言形态（BUG-65 订正）**：**不**再断言「全部连接数 == 5」这种**全局计数**
    ——同用例内新增任何一处连接都会误报，而某处少开一条又可能被别处的连接数
    掩盖 ⇒ 漏报。改为**逐个被测函数**断言：本次调用触发的连接**至少一条**，
    且**每一条**的首条语句都等价于 ``PRAGMA foreign_keys = ON``。
    ⇒ 新增第 6 处连接不再误报；任一函数少开/漏开 PRAGMA 必红。
    """
    import sqlite3 as real_sqlite3

    m1 = _load_m1()
    conns: list[list[str]] = []

    def fake_connect(*args: object, **kwargs: object) -> _FakeConn:
        log: list[str] = []
        conns.append(log)
        return _FakeConn(log)

    original_connect = real_sqlite3.connect
    # ⚠️ 只替换**被测模块自己的** ``sqlite3`` 引用 ⇒ 全局 ``sqlite3`` 不受影响。
    monkeypatch.setattr(m1, "sqlite3", _FakeSqlite3(m1.sqlite3, fake_connect))

    for func_name, args in _B4_TARGETS:
        before = len(conns)
        getattr(m1, func_name)(*args)
        opened = conns[before:]
        assert opened, (
            f"{func_name} 本次调用未开启任何连接 ⇒ 连接被重定向/绕开（BUG-58-B4 失守）"
        )
        for idx, log in enumerate(opened, 1):
            assert log, f"{func_name} 的第 {idx} 处连接没有执行任何语句"
            assert log[0].strip().upper().replace(" ", "") == "PRAGMAFOREIGN_KEYS=ON", (
                f"{func_name} 的第 {idx} 处连接的第一条语句必须等价于 "
                f"PRAGMA foreign_keys = ON ⇒ 外键约束静默不生效：{log[0]!r}"
            )

    # 作用域自查：全局 ``sqlite3.connect`` 必须**未被**替换（否则上面的
    # 逐函数断言仍会被同进程其它代码的连接污染 ⇒ 判别面被悄悄缩小）。
    assert real_sqlite3.connect is original_connect, (
        "全局 sqlite3.connect 被替换了 ⇒ 连接截获范围越界（BUG-65 订正项）"
    )
