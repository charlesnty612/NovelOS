"""M4 回归：默认迁移目录锚定仓库根，不随 CWD 漂移（V3.9 全量检修）。

背景（R1/R3 双探针实证）：``packages/core/db.py::_DEFAULT_MIGRATIONS_DIR`` 曾是
``Path("database") / "migrations"``（CWD 相对）。从仓库根以外的 CWD 启动服务时：

- 目录不存在 → ``_list_sql_files`` 返回 ``[]`` → 迁移静默 0 执行；
- ``/api/health`` 仍报 ``status=ok``（``count_tables`` 只查 sqlite_master）；
- 业务端点全部 500（表不存在）。

修法：默认目录 = ``Path(__file__).resolve().parents[2] / "database" / "migrations"``
（``packages/core/db.py`` → parents[2] = 仓库根），显式参数仍可覆盖；
且目录缺失 / 0 个 SQL 时 ``apply_migrations`` 记 WARNING（不抛，兼容嵌入式用法）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from packages.core.db import apply_migrations

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _sql_file_count() -> int:
    return len([p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file()])


def test_default_migrations_dir_module_attribute_is_repo_absolute() -> None:
    """默认目录必须是绝对路径且等于 <repo>/database/migrations。"""
    from packages.core import db as db_mod

    default_dir = db_mod._DEFAULT_MIGRATIONS_DIR
    assert default_dir.is_absolute(), f"默认迁移目录应为绝对路径，实际 {default_dir!r}"
    assert default_dir == MIGRATIONS_DIR, (
        f"默认迁移目录应锚定仓库根，期望 {MIGRATIONS_DIR}，实际 {default_dir}"
    )


def test_default_migrations_dir_anchored_when_cwd_is_elsewhere(tmp_path: Path) -> None:
    """子进程 cwd=临时目录（其下无 database/migrations）→ 默认目录仍应全量迁移。

    走子进程真跑，避免 pytest 进程内 CWD 已被其它用例改动造成的干扰。
    """
    db_path = tmp_path / "cwd_anchor.db"
    code = (
        "import json;"
        "from packages.core.db import apply_migrations;"
        f"r = apply_migrations({str(db_path)!r});"
        "print(json.dumps({'applied': len(r['applied']), 'tables': r['tables']}))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stderr}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    expected = _sql_file_count()
    assert payload["applied"] == expected, (
        f"异 CWD 启动应跑完全部 {expected} 个迁移，实际 applied={payload['applied']}；"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    # 业务表 38（总数 39 = 38 + _migrations）——迁移真执行过
    assert payload["tables"] == 39, payload


def test_apply_migrations_warns_when_dir_missing(tmp_path: Path, caplog) -> None:
    """目录不存在 → 0 迁移但记 WARNING（不抛，兼容嵌入式用法）。"""
    db_path = tmp_path / "missing_dir.db"
    with caplog.at_level(logging.WARNING, logger="novelos.db"):
        result = apply_migrations(db_path, tmp_path / "no_such_migrations_dir")
    assert result["applied"] == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("migrations" in m for m in warnings), (
        f"目录缺失时应记 WARNING 留痕（防再次静默 0 迁移），实际={warnings}"
    )


def test_apply_migrations_warns_when_dir_has_no_sql(tmp_path: Path, caplog) -> None:
    """目录存在但 0 个 *.sql → 同样记 WARNING。"""
    empty_dir = tmp_path / "empty_migrations"
    empty_dir.mkdir()
    db_path = tmp_path / "empty_dir.db"
    with caplog.at_level(logging.WARNING, logger="novelos.db"):
        result = apply_migrations(db_path, empty_dir)
    assert result["applied"] == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("0" in m or "no" in m.lower() for m in warnings), (
        f"0 个 SQL 时应记 WARNING 留痕，实际={warnings}"
    )
