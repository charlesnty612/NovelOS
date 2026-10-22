# -*- coding: utf-8 -*-
"""``db_maintenance.py`` 的退出码契约（审计 C F-4，2026-10-23）。

缺陷形状（契约承诺 ≠ 实现）
--------------------------
模块 docstring「退出码」节承诺 ``2 —— 参数错误 / DB 文件不存在 / 内部异常``；而
``main()`` 此前**没有顶层 except** ⇒ 未捕获异常一律 **exit 1 + 裸 traceback**
（审计实测：``--db <目录> list`` → sqlite3.OperationalError + exit 1）。运维看到的是
堆栈，不是一行诊断；脚本化调用也分不清「失败」与「参数错」。

本批修法：``main`` 保留 CLI 入口名，内部体改名 ``_main``，外层统一
``except Exception → 单行 ERROR + exit 2``（``SystemExit`` 不继承 Exception ⇒
argparse 的 exit 2 不受影响；正常路径 0 与缺库 2 逐字不变）。

判别（对应 l15 ``DBM-EXIT-1``）：把 ``--db`` 指到**已存在的目录** → get_connection
抛 OperationalError ⇒ 必须 rc 2 + 单行诊断、无 traceback。
"""

from __future__ import annotations

from pathlib import Path

import scripts.db_maintenance as dbm


def test_internal_error_returns_exit_2_with_single_line_diagnostic(tmp_path: Path, capsys):
    # tmp_path 是目录：db_path.exists() 为真 ⇒ 走到 get_connection，必然抛内部错误
    rc = dbm.main(["--db", str(tmp_path), "list"])
    assert rc == 2, "内部异常必须收口为 docstring 承诺的 exit 2（此前裸 traceback exit 1）"
    err = capsys.readouterr().err
    assert "ERROR" in err, f"必须打印单行诊断，实得 {err!r}"
    assert "Traceback" not in err, f"不得把裸 traceback 抛给运维，实得 {err!r}"


def test_missing_db_still_returns_exit_2(tmp_path: Path, capsys):
    rc = dbm.main(["--db", str(tmp_path / "missing.db"), "list"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err, "缺库路径的诊断文案保持原样（不回归）"


def test_argparse_error_still_exits_2(tmp_path: Path):
    """argparse 的参数错误走 SystemExit(2)，不能被顶层 except 改写。"""
    try:
        dbm.main(["--db", str(tmp_path / "x.db")])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - 防御：上面必须抛
        raise AssertionError("缺子命令时 argparse 应 SystemExit(2)")
