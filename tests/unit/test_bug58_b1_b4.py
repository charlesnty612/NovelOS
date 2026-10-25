"""BUG-58-B1／B4 判别用例（OS开发B · 2026-10-25）。

覆盖：
- B1：调试产物落点（``NOVELOS_DEBUG_DIR``）默认**仓外绝对**；显式 env 仍被尊重。
- B1 源码守卫：旧的相对默认 ``"data"`` 必须消失（⇒ 旧字节下也能 FAILED，
  而非「缺函数」的 AttributeError ⇒ 红段判据不被误读成「缺功能」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.agent_runtime import runner


def _repo_root() -> Path:
    """仓根（本文件 ``tests/unit/test_bug58_b1_b4.py`` 上溯三级）。"""
    return Path(__file__).resolve().parents[2]


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
