"""V3.1 P1-1: commit 事务内以 DB 为权威重建快照的回归测试（门面）。

按文件内既有分节拆分为：

- ``snapshot_rebuild_helpers``：共享 helper + 7 集合 DB-snapshot 对照口径
- ``test_snapshot_selfheal``：节 1~3 漂移自愈 / 正向一致 / 既有行为兼容
- ``test_snapshot_ordering``：节 4 recent_events 顺序稳定性（rebuild 路径）
- ``test_snapshot_rollback``：节 5 rollback 逆清理全集合覆盖（审计 §第3条）
- ``test_snapshot_who_knows``：P1 节 snapshot 携带 who_knows

**门面只导出共享 helper，不 re-export 测试函数**——若把 ``test_*`` 函数
import 进门面，pytest 会把同一条用例收集两次（子模块一次、门面一次），
从而破坏「collect 数拆前拆后相等」这条硬验收。

跑用例请直接指向子模块::

    pytest tests/unit/test_snapshot_selfheal.py
    pytest tests/unit/test_snapshot_ordering.py
    pytest tests/unit/test_snapshot_rollback.py
    pytest tests/unit/test_snapshot_who_knows.py
"""

from __future__ import annotations

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _drift_ids_for,
    _evidence,
    _fetch_latest_snapshot_json,
    _make_client,
    _make_meta,
    _open_db,
)

__all__ = [
    "_create_app",
    "_drift_ids_for",
    "_evidence",
    "_fetch_latest_snapshot_json",
    "_make_client",
    "_make_meta",
    "_open_db",
]
