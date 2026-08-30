"""H2 测试：PromptRegistry._upsert_agent 刷新已存在行的 role / config_json。

回归点：V3.9.3 把 observer 从 ``reasoning`` 拆为独立 ``observer`` capability（迁移 0018）
后，启动时 prompt sync 调用的 ``_upsert_agent`` 只在 INSERT 时写入 role；生产库旧行
``role='reasoning'`` 永远不会被刷新，造成 agents.role 列的观测性失真（与实际 capability
不一致）。本测试断言修复后：再次 sync 会让已存在行的 role 跟上新映射。

设计：
- 隔离 tmp DB（apply_migrations + 直连 PromptRegistry，不依赖 HTTP）。
- 在 ``_upsert_agent`` 路径上预置一条 ``observer`` 行 ``role='reasoning'``（模拟
  V3.9.3 前的生产库旧值）。
- 调 ``registry._upsert_agent(conn, 'observer')``；断言该行 ``role='observer'`` 且
  ``config_json['capability'] == 'observer'``，且 ``created_at`` 未被改写。
- 顺带断言对已存在但 role/config_json 已是最新值的行，sync 走幂等路径（updated_at 仍
  刷一下便于审计）。
- 关闭 SQLite 连接的 helper 避免 Windows 下 tmp_dir 清理时文件被锁。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable

from packages.core.agent_runtime import PromptRegistry
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id


def _close_all(conns: Iterable[sqlite3.Connection]) -> None:
    for c in conns:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def _make_isolated_db() -> tuple[str, tempfile.TemporaryDirectory[str]]:
    tmp_dir = tempfile.TemporaryDirectory(prefix="novelos_test_role_refresh_")
    tmp_path = Path(tmp_dir.name)
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path), tmp_dir


def test_upsert_agent_refreshes_existing_role_to_current_capability():
    """已存在 observer 行 role='reasoning' → _upsert_agent 后变 role='observer'。"""
    db_path, tmp_dir = _make_isolated_db()
    opened: list[sqlite3.Connection] = []
    try:
        # 预置一条「旧生产库」observer 行：role=reasoning（V3.9.3 前映射）。
        conn = get_connection(db_path)
        opened.append(conn)
        original_created_at = "2025-01-01T00:00:00+00:00"
        old_agent_id = new_id("ag")
        conn.execute(
            """
            INSERT INTO agents (agent_id, name, role, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                old_agent_id,
                "observer",
                "reasoning",
                json.dumps({"capability": "reasoning"}, ensure_ascii=False),
                original_created_at,
                original_created_at,
            ),
        )
        conn.commit()
        # 关掉预置连接再调 _upsert_agent（避免 SQLite 文件锁）
        _close_all(opened)
        opened.clear()

        registry = PromptRegistry(db_path)
        # 触发 _upsert_agent
        upsert_conn = get_connection(db_path)
        opened.append(upsert_conn)
        returned_id = registry._upsert_agent(upsert_conn, "observer")
        upsert_conn.commit()
        _close_all(opened)
        opened.clear()

        assert returned_id == old_agent_id, (
            f"existing agent_id 应保持不变；实际 {returned_id} != {old_agent_id}"
        )

        verify_conn = get_connection(db_path)
        opened.append(verify_conn)
        row = verify_conn.execute(
            "SELECT agent_id, name, role, config_json, created_at, updated_at "
            "FROM agents WHERE name = ?",
            ("observer",),
        ).fetchone()
        _close_all(opened)
        opened.clear()

        assert row is not None
        # role 应被刷新为当前 capability 映射（observer）
        assert row["role"] == "observer", (
            f"expected role='observer' after refresh, got {row['role']!r}"
        )
        cfg = json.loads(row["config_json"])
        assert cfg.get("capability") == "observer", (
            f"config_json.capability 应刷新为 'observer'，实际 {cfg}"
        )
        # created_at 必须保持原值（避免审计漂移）
        assert row["created_at"] == original_created_at, (
            f"created_at 不应被刷新；原始 {original_created_at}，实际 {row['created_at']}"
        )
        # updated_at 至少要 >= created_at（被刷一下便于审计）
        assert row["updated_at"] >= original_created_at, (
            f"updated_at 应 ≥ created_at；实际 updated_at={row['updated_at']!r}"
        )
    finally:
        _close_all(opened)
        tmp_dir.cleanup()


def test_upsert_agent_idempotent_when_role_already_current():
    """role/config_json 已是最新值时，再次 _upsert_agent 不抛错，agent_id 保持。"""
    db_path, tmp_dir = _make_isolated_db()
    opened: list[sqlite3.Connection] = []
    try:
        registry = PromptRegistry(db_path)
        # 第一次：INSERT
        c1 = get_connection(db_path)
        opened.append(c1)
        first_id = registry._upsert_agent(c1, "observer")
        c1.commit()
        _close_all(opened)
        opened.clear()

        # 第二次：UPDATE（role/config_json 已对齐）
        c2 = get_connection(db_path)
        opened.append(c2)
        second_id = registry._upsert_agent(c2, "observer")
        c2.commit()
        _close_all(opened)
        opened.clear()

        assert first_id == second_id
        c3 = get_connection(db_path)
        opened.append(c3)
        row = c3.execute(
            "SELECT role, config_json FROM agents WHERE name = ?",
            ("observer",),
        ).fetchone()
        _close_all(opened)
        opened.clear()
        assert row["role"] == "observer"
        cfg = json.loads(row["config_json"])
        assert cfg.get("capability") == "observer"
    finally:
        _close_all(opened)
        tmp_dir.cleanup()
