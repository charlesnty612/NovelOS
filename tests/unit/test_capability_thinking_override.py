"""环节级思考档覆盖（capability_bindings.thinking_override，迁移 0036）单测。

覆盖：
1. ``apply_thinking_override`` 翻译规则 8 档 + 非法值 ValueError + 不改入参 +
   两键互斥（写 reasoning_effort 删 thinking，反之亦然，default 两键都删）。
2. ``ModelRouter._candidates`` / ``resolve`` 覆盖三态：
   - ``low`` ⇒ params.reasoning_effort='low' 且无 thinking 键；
   - ``off`` ⇒ thinking.type='disabled' 且无 reasoning_effort；
   - NULL ⇒ 档案 params 原样逐字（存量零行为突变）。
3. fallback 链上**每个**候选都被同一覆盖（后备档案也覆盖）。
4. 归组正交不变量：归组键只含**连接层**属性（provider/base_url/api_key_env/
   生效 key）⇒ **不含思考参数**（档案的 thinking / reasoning_effort），
   也不含思考强度覆盖（``capability_bindings.thinking_override``，环节级）。
   ⚠️ 2026-10-25 重写：此前是**零判别力**（同一批行读两遍比对——归组函数是
   纯函数 ⇒ 前后输入逐字相同 ⇒ 相等恒真）。现版本：临时库建**两个只在思考
   参数上不同**的档案 + 同一连接 ⇒ 断言它们被归到**同一组**（归组键若误吞
   思考参数必红）。
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
    connection_id: str | None = None,
) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_profiles "
            "(profile_id, name, provider, model, params_json, enabled, created_at, updated_at, "
            "connection_id) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)",
            (profile_id, name, provider, model, params_json, enabled, connection_id),
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
# 4. 归组正交不变量（真实库版本 —— 2026-10-25 修正恒真断言）
# ---------------------------------------------------------------------------


def _read_profile_rows(db_path: str, profile_ids: list[str]) -> list[dict]:
    """从**库里**读回档案行（dict），供归组函数消费。

    ⚠️ 归组函数 ``group_profiles_by_connection`` **只吃传入的行、不读库** ⇒
    「写 override 前后各跑一次」若仍在内存里造行 ⇒ 前后两次输入**逐字相同**
    ⇒ 相等是恒真的（本次修正的正是这一点）。
    """
    conn = get_connection(db_path)
    try:
        out: list[dict] = []
        for pid in profile_ids:
            row = conn.execute(
                "SELECT * FROM model_profiles WHERE profile_id = ?", (pid,)
            ).fetchone()
            assert row is not None, f"档案 {pid} 不存在"
            out.append({k: row[k] for k in row.keys()})
        return out
    finally:
        conn.close()


def test_connection_grouping_ignores_thinking_parameters(tmp_path: Path):
    """★归组键**不含思考参数**：两个只在思考参数上不同的档案必须归到**同一组**。

    语义（Lead 2026-10-24 裁决）：``group_profiles_by_connection`` 的语义是
    「哪些**档案**可共用一条**连接**」，键 = (provider, 归一化 base_url,
    api_key_env, 生效 api_key)——全部是**连接层**属性。思考参数是**模型级**
    属性（档案的 ``params_json`` 里的 ``thinking`` / ``reasoning_effort``），
    思考强度覆盖是**环节级**属性（``capability_bindings.thinking_override``）
    ⇒ 三者都不该进入归组键。

    ⚠️ **2026-10-25 重写（原实现零判别力）**：原用例是「同一批档案行读两遍
    分组比对」——归组函数是**纯函数**（只吃传入的行、不读库、不看
    ``capability_bindings``）⇒ 前后两次输入逐字相同 ⇒ 相等恒真 ⇒
    无论归组键怎么改，用例都绿。

    现版本的**判别点**：临时库建**两个只在思考参数上不同**的档案
    （A 的 params 含 ``thinking.type=enabled``；B 的 params 含
    ``reasoning_effort=max``；**其余字段逐字同**）+ 绑**同一条连接**
    ⇒ 断言它们被归到**同一组**。
    ⇒ 归组键若误吞思考参数 ⇒ 两个档案分成两组 ⇒ 本用例必红。

    另附断言：组键字段集合不含任何思考相关键（防止「键里混进 thinking」但
    恰好没造成分组差异的弱化形态）。
    """
    db = _fresh_db(tmp_path)
    # 真实连接（两个档案绑**同一条**连接）
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO model_connections (connection_id, name, provider, base_url, "
            "params_json, enabled, created_at, updated_at) "
            "VALUES ('mconn_group', '归组用例', 'openai', ?, '{}', 1, "
            "datetime('now'), datetime('now'))",
            ("https://api.example.com/v1",),
        )
        conn.commit()
    finally:
        conn.close()

    # 两个档案：**只有思考参数不同**，其余（provider / model / base_url /
    # api_key_env / api_key）逐字相同 ⇒ 归组键的四个维度必然全等。
    base_params = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "NOVELOS_TEST_KEY",
        "api_key": "test-placeholder-key",
    }
    params_a = {**base_params, "thinking": {"type": "enabled"}}
    params_b = {**base_params, "reasoning_effort": "max"}
    pid_a = _insert_profile(
        db,
        profile_id="mprof_think_a",
        provider="openai",
        model="m",
        params_json=json.dumps(params_a),
        connection_id="mconn_group",
    )
    pid_b = _insert_profile(
        db,
        profile_id="mprof_think_b",
        provider="openai",
        model="m",
        params_json=json.dumps(params_b),
        connection_id="mconn_group",
    )

    # 夹具自检：两者确实**只在思考参数上不同**（否则「同组」是平凡的）。
    assert params_a != params_b
    differing = {k for k in set(params_a) | set(params_b) if params_a.get(k) != params_b.get(k)}
    assert differing == {"thinking", "reasoning_effort"}, differing

    rows = _read_profile_rows(db, [pid_a, pid_b])
    # secrets={} ⇒ 生效 key 只来自 params（不读本机 secrets.json ⇒ 夹具自足）
    groups = group_profiles_by_connection(rows, secrets={})

    # ★判别点：两个档案必须落在**同一组**（归组键不含思考参数）
    assert len(groups) == 1, f"思考参数不应影响归组，却分成了 {len(groups)} 组：{groups}"
    assert sorted(groups[0]["profile_ids"]) == sorted([pid_a, pid_b])

    # 组键只含连接层属性 —— 不含任何思考相关字段
    assert set(groups[0]["group_key"]) == {
        "provider",
        "base_url",
        "api_key_env",
        "api_key_set",
    }

    # 环节级覆盖写在 capability_bindings 表上（不在档案行上）⇒ 归组不受其影响，
    # 且**档案行本身不被改写**（思考参数逐字保持）。
    _upsert_binding(db, "reasoning", [pid_a, pid_b], thinking_override="high")
    from packages.core.model_router.bindings import BindingService

    assert BindingService(db).get("reasoning")["thinking_override"] == "high"

    after_rows = _read_profile_rows(db, [pid_a, pid_b])
    assert json.loads(after_rows[0]["params_json"]) == params_a
    assert json.loads(after_rows[1]["params_json"]) == params_b
    assert group_profiles_by_connection(after_rows, secrets={}) == groups


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
