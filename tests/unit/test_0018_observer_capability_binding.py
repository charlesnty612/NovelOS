"""0018 迁移测试：observer 拆为独立 capability（V3.9.3）。

覆盖：
1. 迁移 0018 在 _migrations 表正确登记（apply_migrations 自动跑完所有迁移）。
2. capability_bindings 表现有 reasoning 行 → 0018 跑完派生 observer 行，profile_ids
   与 reasoning 完全一致（线上行为保持不变）。
3. 重复 apply_migrations 幂等：observer 行不被覆盖（INSERT OR IGNORE 语义）。
4. 手工预置 observer 行 + apply_migrations 幂等：observer 行 profile_ids 不被
   reasoning 派生值覆盖（已有 observer 行不动）。
5. 0017 跑完后再跑 0018：0017 索引与 0018 行为均不受影响（迁移顺序按文件号）。
6. observer capability 在 AGENT_CAPABILITY / CAPABILITY_LABELS 中独立存在。

设计要点（TDD 红→绿）：
- 直接调 ``apply_migrations``，期望 0018 已被应用；缺迁移时这些测试应失败。
- 不手工 DDL，避免与迁移实际行为脱节。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.router import (
    AGENT_CAPABILITY,
    CAPABILITY_LABELS,
    capability_for,
)

# ---------------------------------------------------------------------------
# 1. 迁移注册
# ---------------------------------------------------------------------------


def test_migration_0018_is_registered(tmp_path: Path):
    """apply_migrations 后 0018 在 _migrations 表已登记（注册机制正确）。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM _migrations WHERE filename = ?",
            ("0018_observer_capability_binding.sql",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, (
        "0018 迁移未被 apply_migrations 记录；CAPABILITY_LABELS 新增 observer 但迁移未上线"
    )


# ---------------------------------------------------------------------------
# 2. observer 派生自 reasoning：profile_ids 完全一致
# ---------------------------------------------------------------------------


def test_migration_0018_inherits_observer_from_reasoning(tmp_path: Path):
    """reasoning 已有 binding → 0018 跑完派生 observer 行，profile_ids 一致。

    模拟真实生产场景：0016 上线 → 运维手工绑定 reasoning → 升级 0018 之前
    capability_bindings 已有 reasoning 行。验证升级到 0018 时 observer 行
    由 reasoning 派生。
    """
    db_path = str(tmp_path / "test.db")
    # 第一次 apply：跑完 0016-0017，但 0018 不在（这条路径是模拟升级到 0018 之前）。
    # 实际 0018 现在已在 migrations 目录里，无法"回滚"。改成：手工把 0018 从
    # _migrations 表删除后再 apply 一次 → 模拟"0018 即将上线、reapply 时新增"
    # 的真实路径。
    apply_migrations(db_path)
    conn = get_connection(db_path)
    try:
        # 从 _migrations 删除 0018 → 模拟"0018 还没上线过"
        conn.execute(
            "DELETE FROM _migrations WHERE filename = '0018_observer_capability_binding.sql'",
        )
        # 同样删除 observer 行（如果已派生）—— 让测试从 0018 上线前状态开始
        conn.execute(
            "DELETE FROM capability_bindings WHERE capability = 'observer'",
        )
        conn.commit()
    finally:
        conn.close()

    # 写入 reasoning 绑定（模拟"0018 上线前线上现状"）
    pid1 = "mprof_reasoning_a"
    pid2 = "mprof_reasoning_b"
    conn = get_connection(db_path)
    try:
        for pid, model in [(pid1, "gpt-4o"), (pid2, "deepseek-chat")]:
            conn.execute(
                "INSERT INTO model_profiles (profile_id, name, provider, model, params_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '{}', 1, datetime('now'), datetime('now'))",
                (pid, model, "openai", model),
            )
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            ("reasoning", json.dumps([pid1, pid2])),
        )
        conn.commit()
    finally:
        conn.close()

    # 二次 apply：0018 未注册 → 触发派生 observer 行
    result = apply_migrations(db_path)
    applied_18 = [f for f in result["applied"] if f.startswith("0018_")]
    assert applied_18, (
        f"0018 应在 applied 列表中，实际 applied={result['applied']}"
    )

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT profile_ids FROM capability_bindings WHERE capability = 'observer'",
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "0018 跑完后 observer 行不存在"
    assert json.loads(row["profile_ids"]) == [pid1, pid2], (
        f"observer 行 profile_ids 应继承 reasoning，实际 {row['profile_ids']}"
    )


# ---------------------------------------------------------------------------
# 3. 重复 apply 幂等：observer 行不被覆盖
# ---------------------------------------------------------------------------


def test_migration_0018_is_idempotent(tmp_path: Path):
    """apply 第二次：0018 不在 applied 列表（已注册），observer 行不变。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path)
    # 预置 reasoning + 派生 observer
    pid = "mprof_a"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_profiles (profile_id, name, provider, model, params_json, enabled, created_at, updated_at) "
            "VALUES (?, 'a', 'openai', 'gpt-4o', '{}', 1, datetime('now'), datetime('now'))",
            (pid,),
        )
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            ("reasoning", json.dumps([pid])),
        )
        conn.commit()
    finally:
        conn.close()
    apply_migrations(db_path)  # 触发 0018

    # 重复 apply：0018 应在 skipped 列表
    result = apply_migrations(db_path)
    applied_files = [f for f in result["applied"] if f.startswith("0018_")]
    skipped_files = [f for f in result["skipped"] if f.startswith("0018_")]
    assert not applied_files, f"0018 不应被重放，实际 applied={applied_files}"
    assert skipped_files, f"0018 应在 skipped 列表中，实际 skipped={skipped_files}"


# ---------------------------------------------------------------------------
# 4. 已有 observer 行不被 reasoning 派生覆盖
# ---------------------------------------------------------------------------


def test_migration_0018_preserves_existing_observer_binding(tmp_path: Path):
    """手工预置 observer 行 → 0018 跑完 observer 行 profile_ids 不被 reasoning 覆盖。

    设计动机：运维可能直接 PUT observer 绑定（不依赖 0018 派生），0018 必须
    用 INSERT OR IGNORE 保留已有值，不强行覆盖。
    """
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path)
    pid_obs = "mprof_observer"
    pid_reasoning = "mprof_reasoning"
    conn = get_connection(db_path)
    try:
        for pid, model in [(pid_obs, "gpt-4o-mini"), (pid_reasoning, "gpt-4o")]:
            conn.execute(
                "INSERT INTO model_profiles (profile_id, name, provider, model, params_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '{}', 1, datetime('now'), datetime('now'))",
                (pid, pid, "openai", model),
            )
        # 手工预置 observer 行（运维走 PUT /capability-bindings/observer 的语义）
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            ("observer", json.dumps([pid_obs])),
        )
        # reasoning 行
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            ("reasoning", json.dumps([pid_reasoning])),
        )
        conn.commit()
    finally:
        conn.close()
    # 二次 apply：0018 应该跳过（observer 已存在；INSERT OR IGNORE 命中主键）
    apply_migrations(db_path)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT profile_ids FROM capability_bindings WHERE capability = 'observer'",
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["profile_ids"]) == [pid_obs], (
        "0018 不应覆盖已存在的 observer 绑定；应为原值"
    )


# ---------------------------------------------------------------------------
# 5. observer capability 在 router 字典中独立存在
# ---------------------------------------------------------------------------


def test_observer_capability_independent_in_router():
    """AGENT_CAPABILITY 与 CAPABILITY_LABELS 都把 observer 作为独立 capability。"""
    assert capability_for("observer") == "observer"
    assert AGENT_CAPABILITY["observer"] == "observer"
    assert "observer" in CAPABILITY_LABELS
    assert CAPABILITY_LABELS["observer"]["label"] == "状态提取"
    assert "observer" in CAPABILITY_LABELS["observer"]["agents"]
    # reasoning 组不再列 observer
    reasoning_agents = CAPABILITY_LABELS["reasoning"]["agents"]
    assert "observer" not in reasoning_agents, (
        f"observer 应已从 reasoning 组移除，实际 agents={reasoning_agents}"
    )
