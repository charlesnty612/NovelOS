r"""结构化输出提取（Sprint 3）。

职责：从 LLM 原始文本中提取 JSON 对象并解析；提供契约校验钩子。

设计要点：
- LLM 经常在 JSON 外包裹 ``\`\`\`json ... \`\`\`` 围栏或在前后追加解释句。
  提取策略：
  1. 去除 ```json / ``` 围栏（大小写不敏感）；保留围栏内文本。
  2. 在剩余文本中找首个 ``{`` 与末个 ``}``，截取子串（忽略控制字符之外的乱码）。
  3. ``json.loads``；失败抛 :class:`AgentOutputError`（由 runner 捕获并决定是否重试）。
- 契约校验：三档 ``expected``（``"observer"`` / ``"director"`` / ``"writer"``）按
  ``agent-contracts-v0.md`` §5.2 / §3.2 / §4.2 给出最小集断言。
  - ``observer``：顶层必须恰为 7 个 change 数组键；不得含 10 个元信息字段
    （``delta_id / delta_version / schema_version / chapter_id / workflow_run_id /
    previous_state_version / created_by / created_at / supersedes / notes``），
    亦不得含 Prompt 辅助字段（``deviations / self_check / unresolved_plan_intents``）。
  - ``director``：必须含 ``schema_version == "director-plan.v1"``。
  - ``writer``：必须含 ``schema_version == "writer-output.v1"`` 与 ``prose`` / ``self_report``。
  - ``None``：只要求合法 JSON。
- 校验失败抛 :class:`AgentOutputError`，由 runner 捕获并重试 1 次（按 agent-contracts §6 重试原则）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .exceptions import AgentOutputError

# Observer 顶层白名单（7 数组）；其余视为越权
OBSERVER_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    }
)

# Observer 越权字段（agent-contracts §5.2.2 + §5.2.3）
OBSERVER_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        # 元信息 10 字段
        "delta_id",
        "delta_version",
        "schema_version",
        "chapter_id",
        "workflow_run_id",
        "previous_state_version",
        "created_by",
        "created_at",
        "supersedes",
        "notes",
        # Prompt 辅助字段
        "deviations",
        "self_check",
        "unresolved_plan_intents",
    }
)


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*|\s*```", flags=re.MULTILINE)
_OPEN_BRACE_RE = re.compile(r"\{")
_CLOSE_BRACE_RE = re.compile(r"\}")


def strip_code_fence(text: str) -> str:
    r"""去除 ``\`\`\`json / \`\`\``` 围栏（首末各一次）。多段围栏不做递归处理。"""
    return _FENCE_RE.sub("", text)


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取首个 JSON 对象并解析。

    步骤：
    1. 去围栏。
    2. 取首个 ``{`` 与末个 ``}`` 之间的子串。
    3. ``json.loads``；失败抛 :class:`AgentOutputError`。
    """
    if not isinstance(text, str):
        raise AgentOutputError(f"output is not a string: {type(text).__name__}")
    cleaned = strip_code_fence(text).strip()
    if not cleaned:
        raise AgentOutputError("empty output after stripping fences")
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise AgentOutputError(f"no JSON object braces found in output: {cleaned[:80]!r}")
    candidate = cleaned[first : last + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(f"invalid JSON: {exc}; raw={candidate[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AgentOutputError(f"JSON top-level is not an object: {type(parsed).__name__}")
    return parsed


def _validate_observer(payload: dict[str, Any]) -> None:
    """Observer 契约：顶层必须恰为 7 个 change 数组；不得含越权字段。"""
    keys = set(payload.keys())
    extra = keys - OBSERVER_ALLOWED_KEYS
    if extra:
        raise AgentOutputError(
            f"observer output contains forbidden top-level keys: {sorted(extra)}"
        )
    missing = OBSERVER_ALLOWED_KEYS - keys
    if missing:
        # 7 数组即便为空也必须存在为 []（agent-contracts §5.2.1）
        raise AgentOutputError(f"observer output missing required arrays: {sorted(missing)}")
    # 7 数组必须均为 list（即便为空）
    for k in OBSERVER_ALLOWED_KEYS:
        if not isinstance(payload[k], list):
            raise AgentOutputError(f"observer array {k!r} must be a list, got {type(payload[k]).__name__}")


def _validate_director(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "director-plan.v1":
        raise AgentOutputError(
            f"director schema_version must be 'director-plan.v1', got {payload.get('schema_version')!r}"
        )


def _validate_writer(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "writer-output.v1":
        raise AgentOutputError(
            f"writer schema_version must be 'writer-output.v1', got {payload.get('schema_version')!r}"
        )
    if "prose" not in payload:
        raise AgentOutputError("writer output missing required field 'prose'")
    if "self_report" not in payload:
        raise AgentOutputError("writer output missing required field 'self_report'")


_VALIDATORS = {
    "observer": _validate_observer,
    "director": _validate_director,
    "writer": _validate_writer,
}


def validate_contract(expected: str | None, payload: dict[str, Any]) -> None:
    """按 ``expected`` 分派契约校验；``None`` 跳过。失败抛 :class:`AgentOutputError`。"""
    if expected is None:
        return
    validator = _VALIDATORS.get(expected)
    if validator is None:
        # 未知 expected → 当成 None 处理；调用方应只传三档之一
        return
    validator(payload)


__all__ = [
    "extract_json",
    "strip_code_fence",
    "validate_contract",
    "OBSERVER_ALLOWED_KEYS",
    "OBSERVER_FORBIDDEN_KEYS",
]
