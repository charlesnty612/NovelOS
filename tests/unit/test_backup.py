"""Backup 模块单元测试（门面）。

按文件内既有分节拆分为：

- ``backup_fixtures``：共享 fixture（``db_path``）与 helper（``_insert_row`` /
  ``_seed_minimal_project``）
- ``test_backup_export_import``：节 1 导出 / 导入 / 往返一致性 / 格式版本校验
- ``test_backup_selfref``：节 2 自引用外键重映射 + 跨项目间接过滤
- ``test_backup_jsonids``：节 3 JSON 内嵌 id 重映射 / volumes 白名单 /
  F3 提交前 FK 校验 / F4 projects 后加列动态写

**``test_json_id_columns_cover_schema`` 有意留在门面**：
``packages/core/backup/README.md`` 按「文件::用例名」引用它（json_ids 清单与
真实 schema 的漂移看守），用例一旦迁走该引用即断链。留在门面后 collect 数仍
与拆分前一致（门面 1 + 子模块 18 = 19）。

门面只 re-export 共享 fixture/helper，**不 re-export 其它 ``test_*`` 函数**——
否则 pytest 会把同一条用例收集两次（子模块一次、门面一次），破坏「collect 数
拆前拆后相等」这条硬验收。

跑用例请直接指向子模块::

    pytest tests/unit/test_backup_export_import.py
    pytest tests/unit/test_backup_selfref.py
    pytest tests/unit/test_backup_jsonids.py
"""

from __future__ import annotations

from pathlib import Path

from packages.core.backup import EXPORTED_TABLES
from packages.core.backup.json_ids import JSON_ID_COLUMNS
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from tests.unit.backup_fixtures import _insert_row, _seed_minimal_project

__all__ = [
    "_insert_row",
    "_seed_minimal_project",
    "test_json_id_columns_cover_schema",
]


def test_json_id_columns_cover_schema(tmp_path: Path) -> None:
    """json_ids 列清单必须与迁移后的真实 schema 对齐（漂移看守）。

    口径：导出表的每个 ``*_json`` 列与 ``who_knows`` 列都必须在
    ``JSON_ID_COLUMNS`` 里登记，反之清单里不得出现不存在的列。新增 JSON 列
    若忘记登记，本测试即红（防止内嵌 id 重映射悄悄漏列）。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(str(settings.db_path))
    try:
        for table in EXPORTED_TABLES:
            actual_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            expected = {
                c for c in actual_cols if c.endswith("_json") or c == "who_knows"
            }
            listed = set(JSON_ID_COLUMNS.get(table, ()))
            assert listed == expected, (
                f"{table}: JSON_ID_COLUMNS 与 schema 不一致 "
                f"(缺少={sorted(expected - listed)}, 多余={sorted(listed - expected)})"
            )
            # 清单里的列必须真实存在（防改名列 / 删列后清单变孤儿）
            assert listed <= actual_cols, f"{table}: 清单列不存在 {listed - actual_cols}"
    finally:
        conn.close()
