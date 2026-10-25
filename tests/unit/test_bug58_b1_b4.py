"""BUG-58-B1／B4 判别用例（OS开发B · 2026-10-25）。

覆盖：
- B1：调试产物落点（``NOVELOS_DEBUG_DIR``）默认**仓外绝对**；显式 env 仍被尊重。
- B1 源码守卫：旧的相对默认 ``"data"`` 必须消失（⇒ 旧字节下也能 FAILED，
  而非「缺函数」的 AttributeError ⇒ 红段判据不被误读成「缺功能」）。
- B4：``scripts/m1_long_run.py`` 的 5 处裸连接**必须**在首个语句前开
  ``PRAGMA foreign_keys = ON``（⇒ 用假连接记录每条连接实际执行的语句，
  既验「开了」也验「顺序对」）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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


def test_bug58_b4_五处连接均先开foreign_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ BUG-58-B4 判别：5 处裸连接**必须**在首个语句前开 ``PRAGMA foreign_keys = ON``。

    ★ 用**假连接**记录每条连接实际执行的语句 ⇒ 既验「开了」也验「顺序对」
    （SQLite 的 ``PRAGMA foreign_keys`` 在事务内**静默不生效** ⇒ 它必须是
    该连接的第一条语句 ⇒ 见 B4 卡点陷阱②）。
    """
    m1 = _load_m1()
    conns: list[list[str]] = []

    def fake_connect(*args: object, **kwargs: object) -> _FakeConn:
        log: list[str] = []
        conns.append(log)
        return _FakeConn(log)

    monkeypatch.setattr(m1.sqlite3, "connect", fake_connect)
    m1._aggregate_ai_logs("db", ["run1"])
    m1._latest_quality("db", "ch1")
    m1._story_state("db", "prj1")
    m1._latest_prose_chars("db", "ch1")
    m1._latest_prose_word_stats("db", "ch1", 3000)

    assert len(conns) == 5, f"应有 5 处连接，实得 {len(conns)}"
    for i, log in enumerate(conns, 1):
        assert log, f"第 {i} 处连接没有执行任何语句"
        assert log[0].strip().upper().replace(" ", "") == "PRAGMAFOREIGN_KEYS=ON", (
            f"第 {i} 处连接的第一条语句必须等价于 PRAGMA foreign_keys = ON ⇒ "
            f"外键约束静默不生效：{log[0]!r}"
        )
