"""`_contract_runner` 自身的看守测试（2026-10-23 task-9 · N-1）。

为什么必须有这个文件
------------------
`tests/api/_contract_runner.py` 是 contract 模板族的**口径单点**：16 个文件里
「实际键集 == 硬编码期望」「模型字段集 == 硬编码期望」「schema properties/required」
「extra 不裁新键」四段断言**全部**经它执行 ⇒ 它承载着多条突变规格的判别力
（``l2_state_list`` / ``g1_world`` / ``g3_story_state`` 等都指向转换后的文件）。

⚠️ **风险机制**（不是形式要求）：谁把 ``assert_keys_match`` 改成「只比长度」、
或给 ``expected`` 加个默认值从模型自动派生 ⇒ **一批规格同时 SURVIVED**，而
``run_spec.py --precheck`` **抓不到**（锚点仍在源文件、目标测试文件也仍在）
⇒ 正是 AGENTS 坑区「看守自己被悄悄缩小了执行域」的形状。**收敛得越好，这个单点越致命。**

⇒ 本文件的作用：把 runner 的**判别力**本身钉住（不是测 runner 的代码风格）。
按 AGENTS §三 7，突变规格不再是新测试的强制项 ⇒ **本文件不写突变规格**，
改用「哨兵模型 + 必红断言」承担判别力。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from ._contract_runner import (
    assert_extra_allowed,
    assert_keys_match,
    assert_keys_match_ddl,
    assert_list_items_named_ref,
    assert_model_fields_match,
    assert_openapi_properties_and_required,
    assert_openapi_ref,
    db_columns,
)

# ---------------------------------------------------------------------------
# 哨兵模型：**故意**少一个字段（用来钉死「不许从模型自动派生期望」）
# ---------------------------------------------------------------------------


class _SentinelFull(BaseModel):
    """三字段的哨兵（= 「模型正确」那一侧）。"""

    model_config = ConfigDict(extra="allow")

    alpha: str = ""
    beta: int = 0
    gamma: bool = False


class _SentinelMissing(BaseModel):
    """**删掉了 gamma** 的哨兵（= 「模型少声明一个字段」那一侧）。"""

    model_config = ConfigDict(extra="allow")

    alpha: str = ""
    beta: int = 0


class _ForbidModel(BaseModel):
    """``extra="forbid"`` 形态（pydantic 的**默认**形态是 ``"ignore"``）。"""

    model_config = ConfigDict(extra="forbid")

    alpha: str = ""


#: 硬编码的第三方基准（**不**从任何模型派生 ⇒ 这正是被钉死的那条纪律）
SENTINEL_KEYS = {"alpha", "beta", "gamma"}


# ---------------------------------------------------------------------------
# ① assert_keys_match：实际键集 vs 硬编码期望
# ---------------------------------------------------------------------------


def test_assert_keys_match_passes_on_exact_match():
    assert_keys_match({"alpha": 1, "beta": 2}, {"alpha", "beta"}, "哨兵")


def test_assert_keys_match_reports_extra_and_missing_keys():
    """多键与少键**都必须红**，且消息要点名多/少哪些键（复核者要能一眼看出差在哪）。"""
    with pytest.raises(AssertionError) as exc:
        assert_keys_match({"alpha": 1, "extra": 2}, {"alpha", "beta"}, "哨兵")
    msg = str(exc.value)
    assert "多=" in msg and "少=" in msg, msg
    assert "extra" in msg, msg
    assert "beta" in msg, msg


def test_assert_keys_match_rejects_non_mapping():
    with pytest.raises(AssertionError):
        assert_keys_match(["alpha"], {"alpha"}, "哨兵")


# ---------------------------------------------------------------------------
# ③ 哨兵模型：**模型少一个字段时，用硬编码期望必须红**
# ---------------------------------------------------------------------------


def test_model_fields_match_rejects_model_missing_a_field():
    """``_SentinelMissing`` 少了 ``gamma`` ⇒ 用**硬编码**期望比对必须红。"""
    with pytest.raises(AssertionError) as exc:
        assert_model_fields_match(_SentinelMissing, SENTINEL_KEYS)
    msg = str(exc.value)
    assert "gamma" in msg, msg


def test_self_referential_expectation_would_be_blind():
    """⚠️ **本用例是 runner 设计的理由本身**：自指形态（拿模型自己的字段集当期望）
    在哨兵上**照样绿** ⇒ 期望值必须由调用方写死。

    - 用**硬编码**期望 ⇒ 红（上一条用例已钉住）；
    - 用**自指**期望（``set(Model.model_fields)``）⇒ 绿 ⇒ 证明白眼可见的「恒真」。

    这就是 AGENTS 坑区「断言了结果 ≠ 判别了机制」的最小复现（对应 M14 存活形状）。
    """
    # 自指形态：期望从模型自己取 ⇒ 无论模型少不少字段，两侧同变 ⇒ 恒真
    assert_model_fields_match(_SentinelMissing, set(_SentinelMissing.model_fields))
    # 同一个模型 + 硬编码期望 ⇒ 立刻红
    with pytest.raises(AssertionError):
        assert_model_fields_match(_SentinelMissing, SENTINEL_KEYS)


def test_assert_model_fields_match_passes_on_exact_match():
    assert_model_fields_match(_SentinelFull, SENTINEL_KEYS)


# ---------------------------------------------------------------------------
# ① OpenAPI 侧
# ---------------------------------------------------------------------------


def test_openapi_properties_and_required_enforces_required_subset_of_properties():
    """``required ⊆ properties`` 是**不变式**：required 里出现一个不在 properties 的键
    ⇒ 必红（那说明模型声明自相矛盾，前端会拿到一个永远不存在的必填键）。"""
    schema = {"properties": {"alpha": {}, "beta": {}}, "required": ["alpha", "gamma"]}
    with pytest.raises(AssertionError) as exc:
        assert_openapi_properties_and_required(schema, {"alpha", "beta"}, {"alpha", "gamma"})
    assert "required" in str(exc.value)


def test_openapi_properties_and_required_reports_property_drift():
    schema = {"properties": {"alpha": {}}, "required": ["alpha"]}
    with pytest.raises(AssertionError) as exc:
        assert_openapi_properties_and_required(schema, {"alpha", "beta"}, {"alpha"})
    msg = str(exc.value)
    assert "properties" in msg and "beta" in msg, msg


def test_assert_openapi_ref_rejects_missing_ref():
    openapi = {
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        },
        "components": {"schemas": {}},
    }
    with pytest.raises((AssertionError, KeyError)):
        assert_openapi_ref(openapi, "/x", "get", "200", "XOut")


def test_assert_list_items_named_ref_rejects_anonymous_items():
    """裸 ``list[dict]`` ⇒ items 是匿名 object ⇒ 前端拿到 ``Record<string, unknown>``
    ⇒ **等于没有契约** ⇒ 必红。"""
    openapi = {
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"type": "object"}}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {}},
    }
    with pytest.raises(AssertionError):
        assert_list_items_named_ref(openapi, "/x", "get", "XOut")


# ---------------------------------------------------------------------------
# ④ extra="allow" 不裁新键
# ---------------------------------------------------------------------------


def test_assert_extra_allowed_passes_for_extra_allow_model():
    assert_extra_allowed(_SentinelFull, {"alpha": "a"}, unknown_key="future_key")


def test_assert_extra_allowed_fails_for_forbid_model():
    """``extra="forbid"`` 形态下，构造未声明键会直接 ``ValidationError``
    ⇒ 用例必须**炸**（不是静默少键）。"""
    with pytest.raises(ValidationError):
        assert_extra_allowed(_ForbidModel, {"alpha": "a"}, unknown_key="future_key")


# ---------------------------------------------------------------------------
# DDL 列 vs 硬编码键集
# ---------------------------------------------------------------------------


def test_assert_keys_match_ddl_reports_column_drift():
    with pytest.raises(AssertionError) as exc:
        assert_keys_match_ddl({"alpha"}, {"alpha", "beta"}, label="哨兵表")
    msg = str(exc.value)
    assert "beta" in msg, msg


def test_assert_keys_match_ddl_accepts_explicit_rename():
    """DB 列名与 API 键名不同的形态（``event_id`` → ``id``）必须**显式声明**映射，
    不许在比对时悄悄忽略差额。"""
    assert_keys_match_ddl({"id"}, {"event_id"}, rename={"event_id": "id"}, label="哨兵表")


def test_assert_keys_match_ddl_rejects_undeclared_rename():
    with pytest.raises(AssertionError):
        assert_keys_match_ddl({"id"}, {"event_id"}, label="哨兵表")


def test_db_columns_reads_real_columns(tmp_path: Any):
    """``db_columns`` 是「模型之外的第二个第三方基准」的读入口 ⇒ 必须是**真实列名**。"""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "probe.db")
    try:
        conn.execute("CREATE TABLE probe (id INTEGER, name TEXT)")
        conn.commit()
        assert db_columns(conn, "probe") == {"id", "name"}
    finally:
        conn.close()
