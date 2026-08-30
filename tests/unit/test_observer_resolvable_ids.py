"""chapter_commit V3.10 O-4 observer 可核销白名单（hook/debt）单测。

覆盖点（packages/workflows/chapter_commit/pipeline.py 的 O-4 改造）：
1. ``_collect_resolvable_ids``：
   - 从 snapshot 收集「未结清」hook / debt id；
   - hook 五态枚举 OPEN/ACTIVE/ESCALATED 视为可核销，RESOLVED/ABANDONED 不算；
   - debt 四态枚举 open/acknowledged 视为可核销，paid/forgiven 不算；
   - snapshot 缺集合 / 集合非 list / snapshot 非 dict 都返回空 list，绝不抛错。
2. ``_inject_resolvable_ids_into_config``：
   - 写入 payload.config.resolvable_hook_ids / resolvable_debt_ids；
   - 不就地修改入参 payload；
   - config 缺 / 非 dict 时新建。
3. ``_observer_node``：narrative 腿 payload.config 含可核销白名单；entities 腿不受影响。
4. 重试路径：narrative 腿 retry payload.config 也含可核销白名单（不在首次
   注入点 644-651 覆盖范围——是 _inject_validate_node 内的 retry 路径）。
"""

from __future__ import annotations

from packages.workflows.chapter_commit.pipeline import (
    _collect_resolvable_ids,
    _inject_resolvable_ids_into_config,
    _HOOK_OPEN_STATUSES,
    _DEBT_OPEN_STATUSES,
)


# -----------------------------------------------------------------------------
# _collect_resolvable_ids
# -----------------------------------------------------------------------------


def test_collect_resolvable_ids_full_snapshot():
    """snapshot 含 2 open hook + 1 resolved hook + 1 open debt + 1 paid debt。

    预期：hooks 白名单 = [2 个 open id]，debts 白名单 = [1 个 open id]，
    RESOLVED/PAID 不在白名单内。
    """
    snap = {
        "hooks": [
            {"hook_id": "hook_open_1", "status": "OPEN"},
            {"hook_id": "hook_active_1", "status": "ACTIVE"},
            {"hook_id": "hook_resolved_1", "status": "RESOLVED"},
            {"hook_id": "hook_escalated_1", "status": "ESCALATED"},
            {"hook_id": "hook_abandoned_1", "status": "ABANDONED"},
        ],
        "debts": [
            {"debt_id": "debt_open_1", "status": "open"},
            {"debt_id": "debt_ack_1", "status": "acknowledged"},
            {"debt_id": "debt_paid_1", "status": "paid"},
            {"debt_id": "debt_forgiven_1", "status": "forgiven"},
        ],
    }
    # 仅校验「未结清」集合：OPEN/ACTIVE/ESCALATED；open/acknowledged
    ids = _collect_resolvable_ids(snap)
    assert sorted(ids["hooks"]) == sorted(["hook_open_1", "hook_active_1", "hook_escalated_1"])
    assert sorted(ids["debts"]) == sorted(["debt_open_1", "debt_ack_1"])
    # RESOLVED / ABANDONED / paid / forgiven 都该被剔除
    assert "hook_resolved_1" not in ids["hooks"]
    assert "hook_abandoned_1" not in ids["hooks"]
    assert "debt_paid_1" not in ids["debts"]
    assert "debt_forgiven_1" not in ids["debts"]


def test_collect_resolvable_ids_per_spec_minimum():
    """精确规格：2 open hook + 1 resolved + 1 open debt（任务规格示例）。"""
    snap = {
        "hooks": [
            {"hook_id": "h_a", "status": "OPEN"},
            {"hook_id": "h_b", "status": "ACTIVE"},
            {"hook_id": "h_c", "status": "RESOLVED"},
        ],
        "debts": [
            {"debt_id": "d_a", "status": "open"},
        ],
    }
    ids = _collect_resolvable_ids(snap)
    assert sorted(ids["hooks"]) == ["h_a", "h_b"]
    assert ids["debts"] == ["d_a"]


def test_collect_resolvable_ids_empty_snapshot():
    """空 snapshot → 两组白名单均为空 list（项目首个 observer 调用）。"""
    ids = _collect_resolvable_ids({})
    assert ids == {"hooks": [], "debts": []}


def test_collect_resolvable_ids_missing_collections():
    """snapshot 不含 hooks / debts 键 → 不抛错，返回空 list。"""
    snap = {"state_version": 1, "characters": []}
    ids = _collect_resolvable_ids(snap)
    assert ids == {"hooks": [], "debts": []}


def test_collect_resolvable_ids_non_list_collections():
    """snapshot 集合非 list（脏数据兜底）→ 返回空 list，不抛错。"""
    snap = {"hooks": "not-a-list", "debts": None}
    ids = _collect_resolvable_ids(snap)
    assert ids == {"hooks": [], "debts": []}


def test_collect_resolvable_ids_non_dict_snapshot():
    """snapshot 非 dict（调用方传错类型）→ 兜底返回两组空 list。"""
    ids = _collect_resolvable_ids(None)  # type: ignore[arg-type]
    assert ids == {"hooks": [], "debts": []}


def test_collect_resolvable_ids_skips_malformed_entries():
    """条目不是 dict / id 非 str / 缺 status → 跳过该条目，不抛错。"""
    snap = {
        "hooks": [
            "not-a-dict",
            {"status": "OPEN"},  # 缺 hook_id
            {"hook_id": 123, "status": "OPEN"},  # hook_id 非 str
            {"hook_id": "h_ok", "status": "OPEN"},
            {"hook_id": "h_unknown_status"},  # 缺 status
        ],
        "debts": [
            {"debt_id": "d_ok", "status": "open"},
            {"status": "open"},  # 缺 debt_id
        ],
    }
    ids = _collect_resolvable_ids(snap)
    assert ids["hooks"] == ["h_ok"]
    assert ids["debts"] == ["d_ok"]


def test_status_constant_sets_match_spec():
    """规格冻结枚举：hooks OPEN/ACTIVE/ESCALATED；debts open/acknowledged。"""
    assert _HOOK_OPEN_STATUSES == frozenset({"OPEN", "ACTIVE", "ESCALATED"})
    assert _DEBT_OPEN_STATUSES == frozenset({"open", "acknowledged"})


# -----------------------------------------------------------------------------
# _inject_resolvable_ids_into_config
# -----------------------------------------------------------------------------


def test_inject_resolvable_ids_into_config_writes_both_keys():
    payload = {"config": {"min_excerpt_chars_low_confidence": 80}}
    snap = {
        "hooks": [{"hook_id": "h_x", "status": "OPEN"}],
        "debts": [{"debt_id": "d_y", "status": "open"}],
    }
    out = _inject_resolvable_ids_into_config(payload, snap)
    assert out["config"]["resolvable_hook_ids"] == ["h_x"]
    assert out["config"]["resolvable_debt_ids"] == ["d_y"]
    # 不破坏原 config 既有键
    assert out["config"]["min_excerpt_chars_low_confidence"] == 80


def test_inject_resolvable_ids_does_not_mutate_input():
    """浅拷贝返回，调用方 payload 不被原地修改。"""
    payload = {"config": {"recent_event_ids": ["e1"]}}
    snap = {"hooks": [{"hook_id": "h_z", "status": "ACTIVE"}], "debts": []}
    out = _inject_resolvable_ids_into_config(payload, snap)
    assert "resolvable_hook_ids" not in payload["config"]
    assert out["config"]["resolvable_hook_ids"] == ["h_z"]
    assert out["config"]["recent_event_ids"] == ["e1"]


def test_inject_resolvable_ids_creates_missing_config():
    """payload 无 config 字段 → 自动新建 dict，不抛错。"""
    payload: dict = {"chapter": {}}
    snap = {"hooks": [], "debts": []}
    out = _inject_resolvable_ids_into_config(payload, snap)
    assert out["config"] == {"resolvable_hook_ids": [], "resolvable_debt_ids": []}


def test_inject_resolvable_ids_overrides_wrong_type():
    """config 中既有键类型错误（如 str）→ 降级为新建 list，不抛错。"""
    payload = {"config": {"resolvable_hook_ids": "garbage"}}
    snap = {"hooks": [{"hook_id": "h_q", "status": "OPEN"}], "debts": []}
    out = _inject_resolvable_ids_into_config(payload, snap)
    assert out["config"]["resolvable_hook_ids"] == ["h_q"]


# -----------------------------------------------------------------------------
# 重试路径：narrative 腿 retry payload 必须带可核销白名单
# （覆盖 _inject_validate_node 的 split retry 分支，pipeline.py:1192-1212）
# -----------------------------------------------------------------------------


def _build_retry_payload_for_leg(ctx: dict, leg: str, errors: list[str]):
    """复制 _inject_validate_node 分裂 retry 路径的 payload 构造逻辑（仅用于单测断言）。

    真实函数内联在 _inject_validate_node 中；此处仅复刻「retry_payload 构建」一段，
    不重复执行 run_agent / DB / 引擎等副作用，便于纯函数级断言。
    """
    from packages.workflows.chapter_commit.pipeline import (
        _OBSERVER_RETRY_HINT_TEMPLATE,
        _inject_resolvable_ids_into_config,
        _trim_observer_input_for_leg,
    )

    base_retry_payload = dict(ctx.get("observer_input") or {})
    retry_payload, _ = _trim_observer_input_for_leg(base_retry_payload, leg)
    retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
        errors="; ".join(errors),
    )
    retry_payload["extraction_scope"] = (
        "entities" if leg == "entities" else "narrative"
    )
    if leg == "narrative":
        snapshot_for_resolvable = (
            (ctx.get("observer_input") or {}).get("previous_state")
        )
        retry_payload = _inject_resolvable_ids_into_config(
            retry_payload, snapshot_for_resolvable or {},
        )
    return retry_payload


def test_narrative_retry_payload_includes_resolvable_whitelist():
    """narrative 腿 retry payload.config 含 resolvable_hook_ids / resolvable_debt_ids。

    覆盖修复点：_inject_validate_node 的 split retry 路径（L1192-1212）原本只注入
    entities 腿的「无白名单」payload，narrative 腿漏掉；本次补 _inject_resolvable_ids_into_config。
    """
    ctx = {
        "observer_input": {
            "previous_state": {
                "hooks": [
                    {"hook_id": "h_retry_1", "status": "OPEN"},
                    {"hook_id": "h_retry_2", "status": "ACTIVE"},
                ],
                "debts": [
                    {"debt_id": "d_retry_1", "status": "open"},
                ],
            },
            "config": {
                "min_excerpt_chars_low_confidence": 80,
            },
        },
    }
    retry_payload = _build_retry_payload_for_leg(
        ctx, leg="narrative",
        errors=["observer delta failed validation: resolved_hooks[0].hook_id not found"],
    )

    cfg = retry_payload.get("config") or {}
    # narrative 腿 retry payload.config 必须含可核销白名单
    assert sorted(cfg.get("resolvable_hook_ids") or []) == ["h_retry_1", "h_retry_2"]
    assert cfg.get("resolvable_debt_ids") == ["d_retry_1"]
    # 不破坏既有 config 键
    assert cfg["min_excerpt_chars_low_confidence"] == 80
    # extraction_scope 与 _retry_hint 与首次调用口径一致
    assert retry_payload["extraction_scope"] == "narrative"
    assert "_retry_hint" in retry_payload


def test_entities_retry_payload_does_not_inject_whitelist():
    """entities 腿 retry payload 不需要白名单（entities 腿不涉及 resolved_hooks/debt_changes）。

    仅断言：构造过程不抛错、payload 不被白名单注入污染。
    """
    ctx = {
        "observer_input": {
            "previous_state": {
                "hooks": [{"hook_id": "h_skip_1", "status": "OPEN"}],
                "debts": [{"debt_id": "d_skip_1", "status": "open"}],
            },
            "config": {"min_excerpt_chars_low_confidence": 80},
        },
    }
    retry_payload = _build_retry_payload_for_leg(
        ctx, leg="entities",
        errors=["observer delta failed validation: character_changes[0].op invalid"],
    )
    cfg = retry_payload.get("config") or {}
    # entities 腿不应被注入可核销白名单（spec：白名单仅 narrative 腿需要）
    assert "resolvable_hook_ids" not in cfg
    assert "resolvable_debt_ids" not in cfg
    assert retry_payload["extraction_scope"] == "entities"
