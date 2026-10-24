"""环节级思考档覆盖（capability_bindings.thinking_override，迁移 0036）单测。

覆盖：
1. ``apply_thinking_override`` 翻译规则 8 档 + 非法值 ValueError + 不改入参 +
   两键互斥（写 reasoning_effort 删 thinking，反之亦然，default 两键都删）。
2. ``ModelRouter._candidates`` / ``resolve`` 覆盖三态：
   - ``low`` ⇒ params.reasoning_effort='low' 且无 thinking 键；
   - ``off`` ⇒ thinking.type='disabled' 且无 reasoning_effort；
   - NULL ⇒ 档案 params 原样逐字（存量零行为突变）。
3. fallback 链上**每个**候选都被同一覆盖（后备档案也覆盖）。
4. 归组正交不变量：thinking_override 是 binding 级属性，不流经
   ``group_profiles_by_connection``（连接归组键只含连接层属性
   provider/base_url/api_key_env/生效 key）——同一档案行在归组中的归属与
   override 无关（override 读写不改变归组输出）。
5. 迁移：列存在 + NULL 兼容（存量行读出 None）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router import ModelRouter
from packages.core.model_router.effective import (
    THINKING_MODES,
    apply_thinking_override,
    group_profiles_by_connection,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "database" / "migrations"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> str:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return str(db_path)


def _insert_profile(
    db_path: str,
    *,
    profile_id: str,
    name: str = "p",
    provider: str = "mock",
    model: str = "m",
    params_json: str = "{}",
    enabled: int = 1,
) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_profiles "
            "(profile_id, name, provider, model, params_json, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (profile_id, name, provider, model, params_json, enabled),
        )
        conn.commit()
    finally:
        conn.close()
    return profile_id


def _upsert_binding(
    db_path: str,
    capability: str,
    profile_ids: list[str],
    thinking_override: str | None = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, thinking_override, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(capability) DO UPDATE SET "
            "profile_ids = excluded.profile_ids, "
            "thinking_override = excluded.thinking_override, "
            "updated_at = excluded.updated_at",
            (capability, json.dumps(list(profile_ids)), thinking_override),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. apply_thinking_override 翻译规则
# ---------------------------------------------------------------------------


def test_apply_override_effort_modes_write_reasoning_effort_and_drop_thinking():
    for mode in ("low", "medium", "high", "max"):
        out = apply_thinking_override(
            {"temperature": 0.7, "thinking": {"type": "enabled"}}, mode
        )
        assert out["reasoning_effort"] == mode, mode
        assert "thinking" not in out, mode  # 两键互斥：写 effort 必须删 thinking
        assert out["temperature"] == 0.7  # 其余键原样保留


def test_apply_override_thinking_modes_write_thinking_type_and_drop_effort():
    expect = {"off": "disabled", "on": "enabled", "adaptive": "adaptive"}
    for mode, ttype in expect.items():
        out = apply_thinking_override(
            {"reasoning_effort": "high", "temperature": 0.5}, mode
        )
        assert out["thinking"] == {"type": ttype}, mode
        assert "reasoning_effort" not in out, mode  # 互斥：写 thinking 必须删 effort
        assert out["temperature"] == 0.5


def test_apply_override_default_drops_both_keys():
    out = apply_thinking_override(
        {"thinking": {"type": "enabled"}, "reasoning_effort": "low", "max_tokens": 100},
        "default",
    )
    assert "thinking" not in out
    assert "reasoning_effort" not in out
    assert out["max_tokens"] == 100


def test_apply_override_invalid_mode_raises_value_error():
    for bad in ("turbo", "LOW", "", "off ", "enabled"):
        with pytest.raises(ValueError, match="8 档枚举"):
            apply_thinking_override({}, bad)


def test_apply_override_does_not_mutate_input():
    src = {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    frozen = json.dumps(src, sort_keys=True)
    apply_thinking_override(src, "off")
    apply_thinking_override(src, "high")
    apply_thinking_override(src, "default")
    assert json.dumps(src, sort_keys=True) == frozen  # 入参逐字不变


def test_thinking_modes_is_the_eight_value_closed_set():
    assert THINKING_MODES == frozenset(
        {"default", "off", "on", "adaptive", "low", "medium", "high", "max"}
    )


# ---------------------------------------------------------------------------
# 2. _candidates / resolve 覆盖三态
# ---------------------------------------------------------------------------


def _params_of(row: dict) -> dict:
    return json.loads(row["params_json"])


def test_resolve_override_low_writes_effort_and_drops_thinking(tmp_path: Path):
    db = _fresh_db(tmp_path)
    pid = _insert_profile(
        db,
        profile_id="mprof_low_case",
        params_json=json.dumps({"thinking": {"type": "enabled"}, "temperature": 0.3}),
    )
    _upsert_binding(db, "reasoning", [pid], thinking_override="low")
    row = ModelRouter(db).resolve("reasoning")
    params = _params_of(row)
    assert params["reasoning_effort"] == "low"
    assert "thinking" not in params
    assert params["temperature"] == 0.3


def test_resolve_override_off_writes_thinking_disabled_and_drops_effort(tmp_path: Path):
    db = _fresh_db(tmp_path)
    pid = _insert_profile(
        db,
        profile_id="mprof_off_case",
        params_json=json.dumps({"reasoning_effort": "high"}),
    )
    _upsert_binding(db, "reasoning", [pid], thinking_override="off")
    row = ModelRouter(db).resolve("reasoning")
    params = _params_of(row)
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params


def test_resolve_null_override_keeps_profile_params_verbatim(tmp_path: Path):
    db = _fresh_db(tmp_path)
    raw = '{"thinking": {"type": "adaptive"}, "reasoning_effort": "max", "seed": 7}'
    pid = _insert_profile(db, profile_id="mprof_null_case", params_json=raw)
    _upsert_binding(db, "reasoning", [pid], thinking_override=None)
    row = ModelRouter(db).resolve("reasoning")
    # NULL 覆盖 ⇒ params_json 原样逐字（不重序列化、不丢键）
    assert row["params_json"] == raw
    assert _params_of(row) == {"thinking": {"type": "adaptive"}, "reasoning_effort": "max", "seed": 7}


def test_resolve_override_does_not_touch_db_row(tmp_path: Path):
    db = _fresh_db(tmp_path)
    pid = _insert_profile(
        db,
        profile_id="mprof_db_case",
        params_json=json.dumps({"thinking": {"type": "enabled"}}),
    )
    _upsert_binding(db, "creative_writing", [pid], thinking_override="high")
    ModelRouter(db).resolve("creative_writing")
    conn = get_connection(db)
    try:
        stored = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(stored["params_json"]) == {"thinking": {"type": "enabled"}}  # 库未动


# ---------------------------------------------------------------------------
# 3. fallback 链每个候选都被同一覆盖
# ---------------------------------------------------------------------------


def test_override_applies_to_every_candidate_in_fallback_chain(tmp_path: Path):
    db = _fresh_db(tmp_path)
    p1 = _insert_profile(
        db,
        profile_id="mprof_chain_a",
        params_json=json.dumps({"thinking": {"type": "enabled"}, "marker": "a"}),
    )
    p2 = _insert_profile(
        db,
        profile_id="mprof_chain_b",
        params_json=json.dumps({"reasoning_effort": "medium", "marker": "b"}),
    )
    _upsert_binding(db, "light", [p1, p2], thinking_override="off")
    rows = ModelRouter(db).list_enabled("light")
    assert [r["config_id"] for r in rows] == [p1, p2]
    for row in rows:
        params = _params_of(row)
        assert params["thinking"] == {"type": "disabled"}, row["config_id"]
        assert "reasoning_effort" not in params, row["config_id"]
        assert params["marker"] in ("a", "b")  # 各自的其余键保留


# ---------------------------------------------------------------------------
# 4. 归组正交不变量（读归组实现后的确认结论）
# ---------------------------------------------------------------------------


def test_connection_grouping_is_orthogonal_to_thinking_override(tmp_path: Path):
    """连接归组键只含连接层属性，override 不流经归组函数。

    确认结论（2026-10-24）：``group_profiles_by_connection`` 的语义是「哪些
    **档案**可共用一条**连接**」，键 = (provider, 归一化 base_url, api_key_env,
    生效 api_key)——全部是连接层属性。thinking_override 存在
    ``capability_bindings`` 表上（环节级），不在档案行上，根本不流经该函数；
    归组动作（apply-grouping）只写 connection_id，不动 params 的思考键。
    ⇒ 「同档案不同强度被归并成一组」在该归组语境下不构成危害路径，
    本测试钉住的是这个正交不变量：override 读写不改变归组输出。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_profile(
        db,
        profile_id="mprof_group_case",
        provider="openai",
        params_json=json.dumps({"base_url": "https://api.example.com/v1"}),
    )
    rows_before = [{"profile_id": pid, "provider": "openai",
                    "params_json": json.dumps({"base_url": "https://api.example.com/v1"})}]
    groups_before = group_profiles_by_connection(rows_before)

    # 给 binding 设 override 后，档案行本身不变 ⇒ 归组输出也不变
    _upsert_binding(db, "reasoning", [pid], thinking_override="high")
    from packages.core.model_router.bindings import BindingService

    assert BindingService(db).get("reasoning")["thinking_override"] == "high"
    profile_row = {
        "profile_id": pid,
        "provider": "openai",
        "params_json": json.dumps({"base_url": "https://api.example.com/v1"}),
    }
    assert group_profiles_by_connection([profile_row]) == groups_before
    # 同一档案行（不管几个环节几种 override）在归组中就是一行、一组
    assert len(groups_before) == 1
    assert groups_before[0]["profile_ids"] == [pid]


# ---------------------------------------------------------------------------
# 5. 迁移：列存在 + NULL 兼容
# ---------------------------------------------------------------------------


def test_migration_0036_adds_nullable_column(tmp_path: Path):
    db = _fresh_db(tmp_path)
    # 先写（独立连接），再只读断言——外层连接不持有写事务
    _insert_profile(db, profile_id="mprof_mig_case")
    _upsert_binding(db, "reasoning", ["mprof_mig_case"])
    conn = get_connection(db)
    try:
        cols = {
            r["name"]: r
            for r in conn.execute("PRAGMA table_info(capability_bindings)").fetchall()
        }
        row = conn.execute(
            "SELECT thinking_override FROM capability_bindings WHERE capability = 'reasoning'"
        ).fetchone()
    finally:
        conn.close()
    assert "thinking_override" in cols
    assert cols["thinking_override"]["notnull"] == 0  # 可空
    assert row["thinking_override"] is None  # NULL 兼容：存量语义 = 跟随档案
