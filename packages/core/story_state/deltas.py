"""Story State Delta 序列化 / 反序列化 / 逆 Delta / HIGH 风险门（Sprint 2 + Sprint 7 + Sprint 10）。

职责（god-object 拆分后）：
- ``payload_json_from_delta``——把 delta 拆成 ``{meta, payload}``（仅 7 数组）；
  对齐 ``state_deltas.payload_json`` 设计：仅存 7 个 change 数组。
- ``restore_delta_from_row`` / ``restore_delta_from_row_payload``——从
  ``state_deltas`` 行还原完整 delta dict 或最小重放 delta（分支重放专用）。
- ``high_risk_change_ids``——扫描 delta 所有 change 数组，提取需要 author_approval
  的 change_id（state-delta-v0.md §2.5）。
- ``build_inverse_delta``——rollback 路径下生成 schema 合法的逆 Delta。

设计要点：
- 与原 ``service.py`` **逐字节相同** 的 SQL / 返回值 / 异常语义（仅文件位置
  变更；公开行为 0 变化）。
- 本模块**只读** ``state_deltas``（不写；写由 commits.submit_delta /
  commits.commit_delta 完成）。
- ``build_inverse_delta`` 抛 ``StateConflictError``（from_status 缺失拒绝回滚）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from packages.core.ids import new_id

from .exceptions import StateConflictError
from .snapshots import _parse_required_json


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def payload_json_from_delta(delta: dict) -> dict:
    """把 delta 拆成 ``{meta, payload}`` 元信息 + 7 数组（payload_json 列存的是 7 数组，不含元信息）。

    对齐 ``state_deltas.payload_json`` 设计：仅存 7 个 change 数组。
    """
    keys = (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    )
    return {k: list(delta.get(k) or []) for k in keys}


def restore_delta_from_row(row: sqlite3.Row) -> dict:
    """从 state_deltas 行还原完整 delta dict（含 10 元信息字段 + 7 数组）。"""
    payload = _parse_required_json(row["payload_json"], {}) or {}
    return {
        "delta_id": row["delta_id"],
        "delta_version": row["delta_version"],
        "schema_version": row["schema_version"],
        "chapter_id": row["chapter_id"],
        "workflow_run_id": row["workflow_run_id"],
        "previous_state_version": row["previous_state_version"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "supersedes": row["supersedes"],
        "notes": None,
        **{k: payload.get(k, []) for k in (
            "character_changes",
            "world_changes",
            "relationship_changes",
            "new_events",
            "resolved_hooks",
            "new_hooks",
            "debt_changes",
        )},
    }


def restore_delta_from_row_payload(delta_id: str, payload: dict) -> dict:
    """分支重放路径专用：从已解析 payload 还原 7 数组（最小元信息即可）。

    ``_branch_current_state`` 在重放时不需要全部 10 元信息字段，仅 7 数组；
    本函数提供最小可被 ``apply_delta`` 使用的 delta 形态（chapter_id 等设为
    占位符，不参与 apply）。
    """
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "",
        "workflow_run_id": "",
        "previous_state_version": 0,
        "created_by": "branch:replay",
        "created_at": "",
        "supersedes": None,
        "notes": None,
        **{k: payload.get(k, []) for k in (
            "character_changes",
            "world_changes",
            "relationship_changes",
            "new_events",
            "resolved_hooks",
            "new_hooks",
            "debt_changes",
        )},
    }


def high_risk_change_ids(delta: dict) -> list[str]:
    """扫描 delta 所有 change 数组，提取需要 author_approval 的 change_id。

    触发条件（state-delta-v0.md §2.5）：
    1. ``risk_level == "HIGH"``。
    2. ``character_changes[].facet == "definition"``（Character 长期定义变更，PRD §89 HIGH）。
    3. ``world_changes[].world_kind == "rule"``（World Rule 变更，PRD §89 HIGH）。
    """
    out: list[str] = []
    for ch in delta.get("character_changes") or []:
        if not isinstance(ch, dict):
            continue
        needs_approval = ch.get("risk_level") == "HIGH" or ch.get("facet") == "definition"
        if needs_approval:
            cid = ch.get("change_id")
            if isinstance(cid, str):
                out.append(cid)
    for w in delta.get("world_changes") or []:
        if not isinstance(w, dict):
            continue
        needs_approval = w.get("risk_level") == "HIGH" or w.get("world_kind") == "rule"
        if needs_approval:
            cid = w.get("change_id")
            if isinstance(cid, str):
                out.append(cid)
    for array_name in (
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    ):
        for item in delta.get(array_name) or []:
            if not isinstance(item, dict):
                continue
            if item.get("risk_level") == "HIGH":
                cid = item.get("change_id")
                if isinstance(cid, str):
                    out.append(cid)
    return out


def build_inverse_delta(
    original: dict,
    *,
    chapter_id: str,
    workflow_run_id: str,
    current_version: int,
) -> dict:
    """生成 schema 合法的逆 Delta；返回 ``{"delta": <dict>}``。

    逆 Delta 中 ``new_events`` 与 ``new_hooks`` 数组保持空——schema 禁止 remove op。
    原 delta 的 event_id / hook_id 由调用方（rollback_commit）从原 delta 收集并通过
    ``_inverse_cleanup`` 私有参数传给 commit_delta，commit_delta 在同事务内
    DELETE 领域表行 + mutate 快照 JSON。

    ``current_version`` 为当前最新快照 version（用于填 ``previous_state_version`` 满足
    schema `minimum=1` 约束）。
    """
    # 用于 audit 的占位：scheduler 当前不接受 timestamp；直接生成
    now_ts = datetime.now(timezone.utc).isoformat()
    delta_id = new_id("dlt")
    # 三段循环（character/world/relationship）共用的回滚原因——必须在首个循环外
    # 初始化：原实现在 character_changes 循环内赋值，character_changes 为空而
    # relationship_changes 含 op=add 时 UnboundLocalError（生产复核确认）。
    rollback_reason = f"rollback of {original.get('delta_id')}"

    # character_changes：add→remove；update→update(after↔before)；remove→add(after)
    inv_char: list[dict] = []
    for ch in original.get("character_changes") or []:
        op = ch.get("op")
        base = {
            "change_id": new_id("cc"),
            "target_id": ch.get("target_id"),
            "character_id": ch.get("character_id"),
            "facet": ch.get("facet"),
            "field": ch.get("field"),
            "confidence": ch.get("confidence"),
            "evidence": ch.get("evidence"),
            "risk_level": ch.get("risk_level"),
            "notes": f"inverse of {ch.get('change_id')}",
        }
        if op == "add":
            inv_char.append(
                {**base, "op": "remove", "before": ch.get("after"), "after": None, "reason": rollback_reason}
            )
        elif op == "update":
            inv_char.append(
                {**base, "op": "update", "before": ch.get("after"), "after": ch.get("before")}
            )
        elif op == "remove":
            inv_char.append(
                {
                    **base,
                    "op": "add",
                    "before": None,
                    "after": ch.get("after"),
                    "reason": f"re-add of {ch.get('change_id')}",
                }
            )

    # world_changes：同 character 规则
    inv_world: list[dict] = []
    for w in original.get("world_changes") or []:
        base = {
            "change_id": new_id("wc"),
            "target_id": w.get("target_id"),
            "world_kind": w.get("world_kind"),
            "world_id": w.get("world_id"),
            "field": w.get("field"),
            "confidence": w.get("confidence"),
            "evidence": w.get("evidence"),
            "risk_level": w.get("risk_level"),
            "notes": f"inverse of {w.get('change_id')}",
        }
        op = w.get("op")
        if op == "add":
            inv_world.append(
                {**base, "op": "remove", "before": w.get("after"), "after": None, "reason": rollback_reason}
            )
        elif op == "update":
            inv_world.append(
                {**base, "op": "update", "before": w.get("after"), "after": w.get("before")}
            )
        elif op == "remove":
            inv_world.append(
                {
                    **base,
                    "op": "add",
                    "before": None,
                    "after": w.get("after"),
                    "reason": f"re-add of {w.get('change_id')}",
                }
            )

    # relationship_changes
    inv_rel: list[dict] = []
    for r in original.get("relationship_changes") or []:
        base = {
            "change_id": new_id("rc"),
            "target_id": r.get("target_id"),
            "from_character_id": r.get("from_character_id"),
            "to_character_id": r.get("to_character_id"),
            "relation_type": r.get("relation_type"),
            "confidence": r.get("confidence"),
            "evidence": r.get("evidence"),
            "risk_level": r.get("risk_level"),
            "notes": f"inverse of {r.get('change_id')}",
        }
        op = r.get("op")
        if op == "add":
            inv_rel.append(
                {**base, "op": "remove", "before": r.get("after"), "after": None, "reason": rollback_reason}
            )
        elif op == "update":
            inv_rel.append(
                {**base, "op": "update", "before": r.get("after"), "after": r.get("before")}
            )
        elif op == "remove":
            inv_rel.append(
                {
                    **base,
                    "op": "add",
                    "before": None,
                    "after": r.get("after"),
                    "reason": f"re-add of {r.get('change_id')}",
                }
            )

    # resolved_hooks：update 反向 = update(to_status=from_status)；from_status 缺失则抛错。
    # 逆条目必须显式置 payoff_chapter_id=NULL —— 用 ``notes`` 携带哨兵
    # ``__CLEAR_PAYOFF_CHAPTER__``（schema 允许的字符串字段），由 _write_through 识别并清列。
    inv_rh: list[dict] = []
    for h in original.get("resolved_hooks") or []:
        from_status = h.get("from_status")
        if from_status is None:
            raise StateConflictError(
                f"无法回滚 commit {original.get('delta_id')}：resolved_hooks 条目 from_status 缺失",
                delta_id=original.get("delta_id"),
            )
        inv_rh.append(
            {
                "change_id": new_id("rh"),
                "op": "update",
                "target_id": h.get("target_id"),
                "hook_id": h.get("hook_id"),
                "from_status": h.get("to_status"),
                "to_status": from_status,
                "payoff_chapter_id": None,
                # payoff_summary 必填（schema minLength=1）。填回滚说明。
                "payoff_summary": f"reverted by rollback of {original.get('delta_id')}",
                "confidence": h.get("confidence"),
                "evidence": h.get("evidence"),
                "risk_level": h.get("risk_level"),
                # notes 携带哨兵：_write_through 检测到此标记即把 hooks.payoff_chapter_id 显式置 NULL。
                "notes": f"inverse of {h.get('change_id')};__CLEAR_PAYOFF_CHAPTER__",
            }
        )

    # new_hooks 与 new_events 不在逆 Delta 中承载（schema 禁止 remove op）；
    # 调用方 rollback_commit 会从原 delta 收集 event_id / hook_id 并通过
    # _inverse_cleanup 私有参数传给 commit_delta，在同事务内清理领域表与 mutate 快照。

    # debt_changes
    # Schema-legal fields only: status_before/status_after/severity_before/severity_after/description/reason.
    # No `before`/`after` keys (debt_change schema doesn't define them).
    inv_debt: list[dict] = []
    for d in original.get("debt_changes") or []:
        base = {
            "change_id": new_id("dc"),
            "target_id": d.get("target_id"),
            "debt_id": d.get("debt_id"),
            "confidence": d.get("confidence"),
            "evidence": d.get("evidence"),
            "risk_level": d.get("risk_level"),
            "notes": f"inverse of {d.get('change_id')}",
        }
        op = d.get("op")
        rollback_reason = f"rollback of {original.get('delta_id')}"
        if op == "add":
            # Inverse add → op=remove；remove 时 status_after 必填（schema）——
            # 用原 status_after 或兜底 "forgiven"（合法枚举）。
            inv_debt.append(
                {
                    **base,
                    "op": "remove",
                    "status_after": d.get("status_after") or "forgiven",
                    "reason": rollback_reason,
                }
            )
        elif op == "update":
            # Inverse update → status_before/status_after 互换；severity 同理。
            # status_after 必填；兜底 "open"。
            inv_debt.append(
                {
                    **base,
                    "op": "update",
                    "status_before": d.get("status_after"),
                    "status_after": d.get("status_before") or "open",
                    "severity_before": d.get("severity_after"),
                    "severity_after": d.get("severity_before"),
                }
            )
        elif op == "remove":
            # Inverse remove → op=add；用原值重建（description/severity_after/status_after）。
            # 若原 delta 缺这些字段，兜底为合法值。
            inv_debt.append(
                {
                    **base,
                    "op": "add",
                    "description": d.get("description") or f"re-add of {d.get('change_id')}",
                    "severity_after": d.get("severity_after") if d.get("severity_after") is not None else 0.5,
                    "status_after": d.get("status_after") or "open",
                    "reason": f"re-add of {d.get('change_id')}",
                }
            )

    inverse_delta: dict = {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": workflow_run_id,
        "previous_state_version": current_version,  # 提交时与最新快照 version 匹配
        "created_by": "system:rollback",
        "created_at": now_ts,
        "supersedes": None,
        "notes": f"inverse of {original.get('delta_id')}",
        "character_changes": inv_char,
        "world_changes": inv_world,
        "relationship_changes": inv_rel,
        "new_events": [],
        "resolved_hooks": inv_rh,
        "new_hooks": [],
        "debt_changes": inv_debt,
    }
    # previous_state_version 在 submit 后会被 commit 路径重新校验；这里保留 0 由 Service 兜底
    return {"delta": inverse_delta}


__all__ = [
    "payload_json_from_delta",
    "restore_delta_from_row",
    "restore_delta_from_row_payload",
    "high_risk_change_ids",
    "build_inverse_delta",
]
