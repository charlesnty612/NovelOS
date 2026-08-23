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

    return out


def validate_delta(delta: dict, schema_path: Path | str | None = None) -> list[str]:
    """校验 Delta。

    参数：
        delta：待校验 Delta 字典（应为完整 schema 形态，含 10 个元信息字段与 7 个数组）。
        schema_path：可选，覆盖默认 schema 路径（用于测试注入临时 schema）。

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
    return _business_errors(delta)


__all__ = ["validate_delta"]
