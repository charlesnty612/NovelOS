"""State Delta 校验器（Sprint 2）。

职责：
- :func:`validate_delta(delta: dict) -> list[str]`：对 ``delta`` 做两层校验：
  1. JSON Schema（Draft 2020-12）对 ``docs/state-model/schemas/state-delta.schema.json`` 全量校验。
  2. 业务校验：``evidence.chapter_id`` 必须等于顶层 ``chapter_id``。

返回：错误字符串列表；空列表 = 通过。

设计要点：
- 使用 ``jsonschema.Draft202012Validator``（Schema 已声明 ``$schema: draft/2020-12``）。
- Schema 文件路径由本模块常量 ``SCHEMA_PATH`` 持有；测试或外部模块不应硬编码路径。
- Schema 内已校验 ``confidence ∈ [0,1]``、枚举字段、必填项，本层不再重复。
- 错误字符串格式：
  - Schema 错误：``"[schema] <path>: <message>"``（path 用 JSON Pointer 表示）。
  - 业务错误：``"[business] evidence.chapter_id '<x>' != chapter_id '<y>'"`` 等。
- ``format`` 关键字（``date-time``）默认 ``Draft202012Validator`` 不强制；本模块
  通过 ``format_checker`` 启用 ``date-time`` 校验，保证 ISO-8601 字串被拒。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# Sprint 14：resolved_hooks 状态机迁移校验。
# 设计要点（任务书 §B）：
# - 仅校验「端点合法性」（from_status / to_status 必须在 PRD §21 五态枚举内）；
#   schema 已校验端点枚举，此处为业务兜底（防止 schema 放宽时仍守住语义）。
# - 不做严格白名单校验（如 OPEN→RESOLVED）——observer 可在任何 planted 状态直接结算
#   伏笔；这是 state-delta-v0 §2.5.3 的实际口径（与 domain/ledger.HOOK_ALLOWED_NEXT
#   人工维护口径不一致；后者是为人工编辑加保守约束；observer 自动抽取允许更宽）。
# - from_status=null 视为「不校验迁移」——回滚 / 跨分支场景由 service 兜底；
#   schema 允许 null（state-delta-v0 §2.5.3）。
# - ABANDONED 是终态，**显式拒绝 from_status==ABANDONED 且 to_status≠ABANDONED**——
#   防止 observer 误输出「复活已放弃伏笔」（业务上不应发生）。
_HOOK_VALID_STATUSES: tuple[str, ...] = ("OPEN", "ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED")

# 指向 docs/state-model/schemas/state-delta.schema.json
# 路径以项目根为基准；服务运行 cwd 即项目根（uvicorn / pytest 启动目录一致）。
_DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "state-model" / "schemas" / "state-delta.schema.json"
)


@lru_cache(maxsize=1)
def _load_validator(schema_path_str: str) -> Draft202012Validator:
    """加载并缓存 Schema 校验器。``lru_cache`` 接受 hashable 参数，因此用字符串。"""
    path = Path(schema_path_str)
    with path.open("r", encoding="utf-8") as f:
        schema = __import__("json").load(f)
    # 启用 date-time 格式校验：默认 Draft202012Validator 不强制 format
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _schema_errors(delta: dict[str, Any], schema_path: Path) -> list[str]:
    validator = _load_validator(str(schema_path))
    out: list[str] = []
    for err in sorted(validator.iter_errors(delta), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        out.append(f"[schema] {path}: {err.message}")
    return out


def _snapshot_id_sets(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    """从 snapshot 中抽取所有可被引用的实体 ID 集合。

    返回结构：
        {
            "characters": set[character_id],
            "locations": set[location_id],
            "factions": set[faction_id],
            "world_rules": set[world_rule_id],
            "hooks": set[hook_id],
            "debts": set[debt_id],
            "events": set[event_id],
            "relationships": set[relationship_id],
        }

    设计要点：
    - snapshot 字段缺失 / 类型非预期时该集合为空（视为「无相关实体可引用」），
      让引用存在性校验自然降级通过——避免 schema 已放宽时 validator 把缺失字段
      当成 FK 失败阻断业务流。
    - world.locations / world.factions 是 dict（key 即 ID）；
      world.world_rules 是 list（取 world_rule_id）。
    """
    out: dict[str, set[str]] = {
        "characters": set(),
        "locations": set(),
        "factions": set(),
        "world_rules": set(),
        "hooks": set(),
        "debts": set(),
        "events": set(),
        "relationships": set(),
    }
    if not isinstance(snapshot, dict):
        return out

    chars = snapshot.get("characters") or []
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, dict):
                cid = c.get("character_id")
                if isinstance(cid, str) and cid:
                    out["characters"].add(cid)
                rels = c.get("relationships")
                if isinstance(rels, list):
                    for rel in rels:
                        if isinstance(rel, dict):
                            rid = rel.get("relationship_id")
                            if isinstance(rid, str) and rid:
                                out["relationships"].add(rid)

    world = snapshot.get("world") or {}
    if isinstance(world, dict):
        locs = world.get("locations") or {}
        if isinstance(locs, dict):
            out["locations"].update(k for k in locs.keys() if isinstance(k, str))
        facs = world.get("factions") or {}
        if isinstance(facs, dict):
            out["factions"].update(k for k in facs.keys() if isinstance(k, str))
        rules = world.get("world_rules") or []
        if isinstance(rules, list):
            for r in rules:
                if isinstance(r, dict):
                    rid = r.get("world_rule_id")
                    if isinstance(rid, str) and rid:
                        out["world_rules"].add(rid)

    hooks = snapshot.get("hooks") or []
    if isinstance(hooks, list):
        for h in hooks:
            if isinstance(h, dict):
                hid = h.get("hook_id")
                if isinstance(hid, str) and hid:
                    out["hooks"].add(hid)

    debts = snapshot.get("debts") or []
    if isinstance(debts, list):
        for d in debts:
            if isinstance(d, dict):
                did = d.get("debt_id")
                if isinstance(did, str) and did:
                    out["debts"].add(did)

    events_dict = snapshot.get("events") or {}
    if isinstance(events_dict, dict):
        out["events"].update(k for k in events_dict.keys() if isinstance(k, str))
    recent_events = snapshot.get("recent_events") or []
    if isinstance(recent_events, list):
        for eid in recent_events:
            if isinstance(eid, str) and eid:
                out["events"].add(eid)

    return out


def _delta_created_ids(delta: dict[str, Any]) -> dict[str, set[str]]:
    """从 delta 的 add op 数组中收集「本 delta 内被新增的实体 ID」。

    返回结构同 :func:`_snapshot_id_sets`。
    这些 ID 用于「本 delta 内自愈」——若引用指向本 delta 自己 add 的实体，
    不视为 FK 失败（observer 在同一 delta 里 add + 引用是合法的）。
    """
    out: dict[str, set[str]] = {
        "characters": set(),
        "locations": set(),
        "factions": set(),
        "world_rules": set(),
        "hooks": set(),
        "debts": set(),
        "events": set(),
        "relationships": set(),
    }

    def _add_id(bucket: str, value: Any) -> None:
        if isinstance(value, str) and value:
            out[bucket].add(value)

    for ch in delta.get("character_changes") or []:
        if isinstance(ch, dict) and ch.get("op") == "add":
            _add_id("characters", ch.get("character_id") or ch.get("target_id"))

    for ch in delta.get("world_changes") or []:
        if not isinstance(ch, dict) or ch.get("op") != "add":
            continue
        wid = ch.get("world_id") or ch.get("target_id")
        kind = ch.get("world_kind")
        if kind == "location":
            _add_id("locations", wid)
        elif kind == "faction":
            _add_id("factions", wid)
        elif kind == "rule":
            _add_id("world_rules", wid)

    for ch in delta.get("relationship_changes") or []:
        if isinstance(ch, dict) and ch.get("op") == "add":
            _add_id("relationships", ch.get("relationship_id"))

    for ch in delta.get("new_events") or []:
        if isinstance(ch, dict):
            _add_id("events", ch.get("event_id") or ch.get("target_id"))

    for ch in delta.get("new_hooks") or []:
        if isinstance(ch, dict):
            _add_id("hooks", ch.get("hook_id") or ch.get("target_id"))

    for ch in delta.get("debt_changes") or []:
        if isinstance(ch, dict) and ch.get("op") == "add":
            _add_id("debts", ch.get("debt_id") or ch.get("target_id"))

    return out


def _reference_existence_errors(
    delta: dict[str, Any], snapshot: dict[str, Any] | None
) -> list[str]:
    """校验 delta 中各 change 引用的实体 ID 在 snapshot 中存在（或本 delta 自建）。

    设计要点（M3 引擎包：解决 ch056 FK 失败无法自愈问题）：
    - 把 DB 层 FOREIGN KEY constraint failed 前置为可自愈的校验错误。
      Observer 看到错误可重写 / 引用其他实体，比 DB 报错晚回退更友好。
    - 引用合法条件：「存在于 snapshot 对应集合」或「由本 delta 的 add op 创建」。
    - 字段缺失 / 类型非预期 → 跳过该条（不抛异常）。
    - snapshot 缺失 → 整体跳过该校验（向后兼容；现有调用方不传 snapshot）。
    - 错误格式仿照现有「op='update' 但 before 为 None」文案风格。
    """
    if not isinstance(snapshot, dict):
        return []
    snap_ids = _snapshot_id_sets(snapshot)
    created_ids = _delta_created_ids(delta)

    def _exists(bucket: str, value: Any) -> bool:
        if not isinstance(value, str) or not value:
            return True  # 字段缺失/空串视为不校验（容错优先）
        return value in snap_ids.get(bucket, set()) or value in created_ids.get(bucket, set())

    errs: list[str] = []

    for idx, ch in enumerate(delta.get("character_changes") or []):
        if not isinstance(ch, dict):
            continue
        cid = ch.get("character_id")
        if not _exists("characters", cid):
            errs.append(
                f"[business] character_changes[{idx}].character_id "
                f"{cid!r} 不在 snapshot 且未被本 delta add 创建（违反 FK characters.character_id）"
            )

    for idx, ch in enumerate(delta.get("world_changes") or []):
        if not isinstance(ch, dict):
            continue
        kind = ch.get("world_kind")
        wid = ch.get("world_id")
        if kind == "location":
            if not _exists("locations", wid):
                errs.append(
                    f"[business] world_changes[{idx}].world_id "
                    f"{wid!r} (world_kind=location) 不在 snapshot 且未被本 delta add 创建"
                )
        elif kind == "faction":
            if not _exists("factions", wid):
                errs.append(
                    f"[business] world_changes[{idx}].world_id "
                    f"{wid!r} (world_kind=faction) 不在 snapshot 且未被本 delta add 创建"
                )
        elif kind == "rule":
            if not _exists("world_rules", wid):
                errs.append(
                    f"[business] world_changes[{idx}].world_id "
                    f"{wid!r} (world_kind=rule) 不在 snapshot 且未被本 delta add 创建"
                )

    for idx, ch in enumerate(delta.get("relationship_changes") or []):
        if not isinstance(ch, dict):
            continue
        f = ch.get("from_character_id")
        t = ch.get("to_character_id")
        if not _exists("characters", f):
            errs.append(
                f"[business] relationship_changes[{idx}].from_character_id "
                f"{f!r} 不在 snapshot 且未被本 delta add 创建"
            )
        if not _exists("characters", t):
            errs.append(
                f"[business] relationship_changes[{idx}].to_character_id "
                f"{t!r} 不在 snapshot 且未被本 delta add 创建"
            )
        if ch.get("op") != "add":
            rid = ch.get("relationship_id")
            if isinstance(rid, str) and rid and not _exists("relationships", rid):
                errs.append(
                    f"[business] relationship_changes[{idx}].relationship_id "
                    f"{rid!r} 不在 snapshot 且未被本 delta add 创建"
                )

    for idx, ch in enumerate(delta.get("new_events") or []):
        if not isinstance(ch, dict):
            continue
        eid = ch.get("event_id") or ch.get("target_id")
        if isinstance(eid, str) and eid and eid in snap_ids.get("events", set()):
            errs.append(
                f"[business] new_events[{idx}].event_id {eid!r} 已存在于 snapshot "
                f"（违反 op=add 唯一性，请改用 op=update 或重命名）"
            )
        parts = ch.get("participants") or []
        if isinstance(parts, list):
            for j, p in enumerate(parts):
                if not _exists("characters", p):
                    errs.append(
                        f"[business] new_events[{idx}].participants[{j}] "
                        f"{p!r} 不在 snapshot 且未被本 delta add 创建"
                    )

    for idx, ch in enumerate(delta.get("resolved_hooks") or []):
        if not isinstance(ch, dict):
            continue
        hid = ch.get("hook_id")
        if not _exists("hooks", hid):
            errs.append(
                f"[business] resolved_hooks[{idx}].hook_id "
                f"{hid!r} 不在 snapshot 且未被本 delta add 创建"
            )

    for idx, ch in enumerate(delta.get("new_hooks") or []):
        if not isinstance(ch, dict):
            continue
        hid = ch.get("hook_id") or ch.get("target_id")
        if isinstance(hid, str) and hid and hid in snap_ids.get("hooks", set()):
            errs.append(
                f"[business] new_hooks[{idx}].hook_id {hid!r} 已存在于 snapshot "
                f"（违反 op=add 唯一性）"
            )

    for idx, ch in enumerate(delta.get("debt_changes") or []):
        if not isinstance(ch, dict):
            continue
        did = ch.get("debt_id") or ch.get("target_id")
        if ch.get("op") == "add":
            if isinstance(did, str) and did and did in snap_ids.get("debts", set()):
                errs.append(
                    f"[business] debt_changes[{idx}].debt_id {did!r} 已存在于 snapshot "
                    f"（违反 op=add 唯一性）"
                )
        else:
            if not _exists("debts", did):
                errs.append(
                    f"[business] debt_changes[{idx}].debt_id "
                    f"{did!r} 不在 snapshot 且未被本 delta add 创建"
                )

    return errs


def _business_errors(delta: dict[str, Any]) -> list[str]:
    out: list[str] = []
    chapter_id = delta.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        # schema 已校验必填；此处兜底
        return out

    # evidence.chapter_id 必须等于顶层 chapter_id
    for array_name in (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    ):
        items = delta.get(array_name) or []
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, dict):
                continue
            ev_chap = evidence.get("chapter_id")
            if ev_chap != chapter_id:
                out.append(
                    f"[business] {array_name}[{idx}].evidence.chapter_id "
                    f"'{ev_chap}' != chapter_id '{chapter_id}'"
                )

    # 业务规则（state-delta-v0.md §2.5 约束，schema 故意放宽到 nullable 但语义必填）：
    # - op=update：before/after 均必非 None（schema 允许 null 但语义上 update 必须有值）。
    # - op=add：after 必非 None（character/world/relationship）；debt_changes 无 after 字段，
    #   改用 description 必填。
    # - op=remove：reason 必非空。
    # 这些规则确保 Observer 不会输出「形式合法但语义空洞」的 change。
    for array_name in (
        "character_changes",
        "world_changes",
        "relationship_changes",
    ):
        items = delta.get(array_name) or []
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            op = item.get("op")
            if op == "update":
                if item.get("before") is None:
                    out.append(
                        f"[business] {array_name}[{idx}].op='update' 但 before 为 None"
                    )
                if item.get("after") is None:
                    out.append(
                        f"[business] {array_name}[{idx}].op='update' 但 after 为 None"
                    )
            elif op == "add":
                if item.get("after") is None:
                    out.append(
                        f"[business] {array_name}[{idx}].op='add' 但 after 为 None"
                    )
            elif op == "remove":
                reason = item.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    out.append(
                        f"[business] {array_name}[{idx}].op='remove' 但 reason 为空"
                    )

    # debt_changes：op=add 需 description 非空；op=update 需 status_before/status_after；
    # op=remove 需 reason 非空。schema 用 status_after 替代 after。
    for idx, item in enumerate(delta.get("debt_changes") or []):
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        if op == "add":
            desc = item.get("description")
            if not isinstance(desc, str) or not desc.strip():
                out.append(
                    f"[business] debt_changes[{idx}].op='add' 但 description 为空"
                )
            if item.get("status_after") is None:
                out.append(
                    f"[business] debt_changes[{idx}].op='add' 但 status_after 为 None"
                )
        elif op == "update":
            if item.get("status_before") is None:
                out.append(
                    f"[business] debt_changes[{idx}].op='update' 但 status_before 为 None"
                )
            if item.get("status_after") is None:
                out.append(
                    f"[business] debt_changes[{idx}].op='update' 但 status_after 为 None"
                )
        elif op == "remove":
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                out.append(
                    f"[business] debt_changes[{idx}].op='remove' 但 reason 为空"
                )

    # change_id 全 Delta 唯一（防止 Observer 笔误，schema 未强制）
    seen: dict[str, str] = {}
    for array_name in (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    ):
        items = delta.get(array_name) or []
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            cid = item.get("change_id")
            if not isinstance(cid, str) or not cid:
                continue
            if cid in seen:
                out.append(
                    f"[business] {array_name}[{idx}].change_id '{cid}' 重复 "
                    f"(先前出现在 {seen[cid]})"
                )
            else:
                seen[cid] = f"{array_name}[{idx}]"

    # Sprint 14：resolved_hooks 状态机迁移校验（任务书 §B）。
    # 校验语义：
    # - to_status 必须 ∈ PRD §21 五态枚举；不在枚举 → reject。
    # - from_status=null → 跳过（回滚 / 跨分支场景）。
    # - from_status 非 null 时若不在枚举 → reject。
    # - from_status == 'ABANDONED' 且 to_status != 'ABANDONED' → reject（已放弃不可复活）。
    # - 其他（如 OPEN→RESOLVED / ESCALATED→OPEN）合法；observer 可在任何 planted 状态直接结算。
    valid_hook_statuses = set(_HOOK_VALID_STATUSES)
    for idx, item in enumerate(delta.get("resolved_hooks") or []):
        if not isinstance(item, dict):
            continue
        hook_id = item.get("hook_id")
        from_status = item.get("from_status")
        to_status = item.get("to_status")
        # from_status null → 跳过
        if from_status is None:
            continue
        if not isinstance(from_status, str) or from_status not in valid_hook_statuses:
            out.append(
                f"[business] resolved_hooks[{idx}] (hook_id={hook_id!r}).from_status "
                f"{from_status!r} 不在合法枚举 {sorted(valid_hook_statuses)}"
            )
            continue
        if not isinstance(to_status, str) or to_status not in valid_hook_statuses:
            out.append(
                f"[business] resolved_hooks[{idx}] (hook_id={hook_id!r}).to_status "
                f"{to_status!r} 不在合法枚举 {sorted(valid_hook_statuses)}"
            )
            continue
        if from_status == "ABANDONED" and to_status != "ABANDONED":
            out.append(
                f"[business] resolved_hooks[{idx}] (hook_id={hook_id!r}) ABANDONED 不可复活为 {to_status!r}"
            )

    return out


def validate_delta(
    delta: dict,
    schema_path: Path | str | None = None,
    *,
    snapshot: dict | None = None,
) -> list[str]:
    """校验 Delta。

    参数：
        delta：待校验 Delta 字典（应为完整 schema 形态，含 10 个元信息字段与 7 个数组）。
        schema_path：可选，覆盖默认 schema 路径（用于测试注入临时 schema）。
        snapshot（M3 新增）：可选，传入时执行「引用实体存在性」业务校验——
            把 DB 层 FOREIGN KEY constraint failed 前置为可自愈的业务错误。
            不传时跳过该校验（向后兼容现有调用方）。

    返回：错误字符串列表。空列表 = 通过。

    设计取舍：
    - 不抛异常，让调用方决定如何处理错误列表（State Committer 写 ``state_deltas.status='rejected'``；
      Router 转 422）。这是与 S1 ``IntegrityError`` 直接向上抛不同的取舍——Delta 校验错误
      是「数据层问题」而非「存储层抛错」，更适合收集到统一错误流。
    """
    if not isinstance(delta, dict):
        return ["[schema] <root>: delta must be a JSON object"]

    sp = Path(schema_path) if schema_path is not None else _DEFAULT_SCHEMA_PATH
    errors = _schema_errors(delta, sp)
    # Schema 不通过时跳过业务校验（避免对缺失字段二次报错）
    if errors:
        return errors
    errs = _business_errors(delta)
    # M3 引用存在性校验：仅在 snapshot 非空时执行；调用方可选择传入。
    if snapshot is not None:
        errs.extend(_reference_existence_errors(delta, snapshot))
    return errs


__all__ = ["validate_delta"]
