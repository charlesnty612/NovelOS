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
  - ``observer``：顶层必须恰为 7 个 change 数组键（结构错误仍抛 :class:`AgentOutputError`）；
    含 10 元信息字段或 3 辅助字段（**越权字段**）时**剥离后继续**，不抛错、不触发重试。
  - ``director``：必须含 ``schema_version == "director-plan.v1"``。
  - ``writer``：必须含 ``schema_version == "writer-output.v1"`` 与 ``prose`` / ``self_report``。
  - ``None``：只要求合法 JSON。
- 校验失败抛 :class:`AgentOutputError`，由 runner 捕获并重试 1 次（按 agent-contracts §6 重试原则）。
  **剥离策略** 是 observer 的特殊路径（不重试，仅剥离）；见 :func:`strip_observer_violations`。
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

# Observer 越权字段（agent-contracts §5.2.2 + §5.2.3）—— 剥离目标，不抛错
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
_THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)
_OPEN_BRACE_RE = re.compile(r"\{")
_CLOSE_BRACE_RE = re.compile(r"\}")


def strip_code_fence(text: str) -> str:
    r"""去除 ``\`\`\`json / \`\`\``` 围栏（首末各一次）。多段围栏不做递归处理。"""
    return _FENCE_RE.sub("", text)


def strip_think_blocks(text: str) -> str:
    """移除推理模型可能附带的 ``<think>...</think>`` 文本块。

    只移除完整、跨行、区分大小写不敏感的思考块；不会把普通正文中的字样
    ``<think>`` 误删。该函数位于结构化输出边界，散文正文也会自然复用。
    """
    return _THINK_RE.sub("", text).strip()


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取首个 JSON 对象并解析。

    步骤：
    1. 去围栏。
    2. 取首个 ``{`` 与末个 ``}`` 之间的子串。
    3. ``json.loads``；失败抛 :class:`AgentOutputError`。
    """
    if not isinstance(text, str):
        raise AgentOutputError(f"output is not a string: {type(text).__name__}")
    cleaned = strip_think_blocks(strip_code_fence(text))
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
        # LLM 偶发在字符串内输出未转义控制字符（生产实测 observer 输出含裸控制符，
        # ch074 commit 首次失败即此因）：strict=False 放宽字符串内控制字符，兜底重试一次。
        try:
            parsed = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            raise AgentOutputError(f"invalid JSON: {exc}; raw={candidate[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AgentOutputError(f"JSON top-level is not an object: {type(parsed).__name__}")
    return parsed


def _validate_observer(payload: dict[str, Any]) -> None:
    """Observer 契约：仅校验**结构**（缺数组键 / 非 list）；越权字段不在此抛错。

    越权字段（10 元信息 + 3 辅助）由 :func:`strip_observer_violations` 剥离——本函数
    职责收缩为「结构合法即视为合规」，剥离与重试决策统一在 runner 层完成。
    """
    keys = set(payload.keys())
    missing = OBSERVER_ALLOWED_KEYS - keys
    if missing:
        # 7 数组即便为空也必须存在为 []（agent-contracts §5.2.1）
        raise AgentOutputError(f"observer output missing required arrays: {sorted(missing)}")
    # 7 数组必须均为 list（即便为空）
    for k in OBSERVER_ALLOWED_KEYS:
        if not isinstance(payload[k], list):
            raise AgentOutputError(f"observer array {k!r} must be a list, got {type(payload[k]).__name__}")


def strip_observer_violations(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """剥离 observer 输出中的越权顶层字段（不抛错，不重试）。

    返回 ``(cleaned, stripped_keys)``：
    - ``cleaned``：仅含 7 数组键的 dict（如果输入缺某数组键，会**自动补**为 ``[]``，
      以便下游 schema 校验 / 落库不被结构错误阻断；补字段不计入 stripped_keys）。
    - ``stripped_keys``：实际被剥除的越权键名（按字母升序）。

    契约说明（agent-contracts §5.3）：
    - 越权字段一律剥除，不抛错；
    - 若剥除后仍缺必备 7 数组（输入未提供），自动补空数组并**不**记入 stripped_keys；
    - 数组必须为 list，否则视为结构错误（交由 :func:`_validate_observer` 抛错，
      本函数仅做剥除与补全，不做类型断言）。
    """
    keys = set(payload.keys())
    # 补齐缺失的 7 数组（不计入 stripped）
    cleaned: dict[str, Any] = {k: [] for k in OBSERVER_ALLOWED_KEYS}
    for k in OBSERVER_ALLOWED_KEYS:
        if k in payload:
            cleaned[k] = payload[k]
    # 剥离越权字段
    stripped: list[str] = []
    for k in keys & OBSERVER_FORBIDDEN_KEYS:
        stripped.append(k)
        # 不复制到 cleaned
    # 保留任何「既非白名单也非越权」的字段——但本契约下不应存在；若存在同样剥除
    # （与原 _validate_observer 的「extra 抛错」语义等价：从严，统一按越权处理）
    extras = keys - OBSERVER_ALLOWED_KEYS - OBSERVER_FORBIDDEN_KEYS
    for k in extras:
        stripped.append(k)
    return cleaned, sorted(stripped)


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


_VALIDATOR_ALLOWED_CATEGORIES = frozenset(
    {"pacing", "character", "logic", "foreshadowing", "ai_flavor", "other"}
)
_VALIDATOR_ALLOWED_SEVERITIES = frozenset({"high", "medium", "low"})
_VALID_SCENE_SLOT_TYPES = frozenset(
    {"dialogue", "action", "description", "emotion", "suspense", "humor", "romance"}
)
_VALID_SCENE_POVS = frozenset(
    {"first_person", "third_person_limited", "third_person_omniscient"}
)


def _validate_scene_planner(payload: dict[str, Any]) -> None:
    """Scene Planner（P0）契约：结构合规 + 枚举合法。

    Schema 与 `docs/agents/prompts/scene_planner-v1.md` §7 对齐：
    - required: schema_version, prompt_version, chapter_id, scenes
    - scenes 必须为非空 list
    - scenes[*].scene_id / purpose / characters / conflict / slots 必填
    - scenes[*].pov 在枚举内；slot.type 在枚举内
    - scenes[*].slots 为非空 list（兜底 scene 至少 1 个 slot）

    字段兼容性：输出字段与 chapter_write._scene_planner_stub 输出完全一致
    （scene_id / purpose / characters / location / conflict / turn / time_in_story /
    pov / pov_character_id / slots），保证下游 build_writer_input 无需改动。
    """
    for key in ("schema_version", "prompt_version", "chapter_id", "scenes"):
        if key not in payload:
            raise AgentOutputError(f"scene_planner output missing required field: {key!r}")
    if payload.get("schema_version") != "scene-plan.v1":
        raise AgentOutputError(
            f"scene_planner schema_version must be 'scene-plan.v1', got {payload.get('schema_version')!r}"
        )
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise AgentOutputError("scene_planner output 'scenes' must be a non-empty list")
    for sidx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise AgentOutputError(f"scene_planner scene[{sidx}] must be an object")
        for key in ("scene_id", "purpose", "characters", "conflict", "slots"):
            if key not in scene:
                raise AgentOutputError(
                    f"scene_planner scene[{sidx}] missing required field: {key!r}"
                )
        if scene.get("pov") not in _VALID_SCENE_POVS:
            raise AgentOutputError(
                f"scene_planner scene[{sidx}].pov {scene.get('pov')!r} not in {sorted(_VALID_SCENE_POVS)}"
            )
        slots = scene.get("slots")
        if not isinstance(slots, list) or not slots:
            raise AgentOutputError(
                f"scene_planner scene[{sidx}].slots must be a non-empty list"
            )
        for lidx, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise AgentOutputError(
                    f"scene_planner scene[{sidx}].slots[{lidx}] must be an object"
                )
            for key in ("slot_id", "type", "purpose", "characters", "constraints"):
                if key not in slot:
                    raise AgentOutputError(
                        f"scene_planner scene[{sidx}].slots[{lidx}] missing required field: {key!r}"
                    )
            if slot.get("type") not in _VALID_SCENE_SLOT_TYPES:
                raise AgentOutputError(
                    f"scene_planner scene[{sidx}].slots[{lidx}].type {slot.get('type')!r} "
                    f"not in {sorted(_VALID_SCENE_SLOT_TYPES)}"
                )


def _validate_critic(payload: dict[str, Any]) -> None:
    """Critic（V1.3 评审员）契约：结构合规 + 枚举合法。

    Schema 与 `docs/agents/prompts/critic-v1.md` §7 对齐：
    - required: schema_version, prompt_version, chapter_id, overall_comment, strengths, issues
    - strengths / issues 必须是 list
    - issues[].category ∈ 6 枚举；issues[].severity ∈ 3 枚举

    ``quote`` 可溯源 / 长度上限由调用方（chapter_review._critic_review_node）做软校验；
    本契约只校验**结构层**，不校验语义层。
    """
    for key in ("schema_version", "prompt_version", "chapter_id", "overall_comment"):
        if key not in payload:
            raise AgentOutputError(f"critic output missing required field: {key!r}")
    if payload.get("schema_version") != "critic-report.v1":
        raise AgentOutputError(
            f"critic schema_version must be 'critic-report.v1', got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload["overall_comment"], str):
        raise AgentOutputError("critic overall_comment must be a string")
    for arr_key in ("strengths", "issues"):
        if arr_key not in payload:
            raise AgentOutputError(f"critic output missing required array: {arr_key!r}")
        if not isinstance(payload[arr_key], list):
            raise AgentOutputError(
                f"critic {arr_key} must be a list, got {type(payload[arr_key]).__name__}"
            )
    for idx, issue in enumerate(payload["issues"]):
        if not isinstance(issue, dict):
            raise AgentOutputError(f"critic issues[{idx}] must be an object")
        for k in ("category", "severity", "quote", "suggestion"):
            if k not in issue:
                raise AgentOutputError(f"critic issues[{idx}] missing required field: {k!r}")
        if issue["category"] not in _VALIDATOR_ALLOWED_CATEGORIES:
            raise AgentOutputError(
                f"critic issues[{idx}].category {issue['category']!r} not in "
                f"{sorted(_VALIDATOR_ALLOWED_CATEGORIES)}"
            )
        if issue["severity"] not in _VALIDATOR_ALLOWED_SEVERITIES:
            raise AgentOutputError(
                f"critic issues[{idx}].severity {issue['severity']!r} not in "
                f"{sorted(_VALIDATOR_ALLOWED_SEVERITIES)}"
            )


def _validate_premise_designer(payload: dict[str, Any]) -> None:
    """project-init premise_designer 契约：schema_version + 关键字段。"""
    if payload.get("schema_version") != "premise-design.v1":
        raise AgentOutputError(
            f"premise_designer schema_version must be 'premise-design.v1', got {payload.get('schema_version')!r}"
        )


def _validate_world_builder(payload: dict[str, Any]) -> None:
    """project-init world_builder 契约：schema_version + 关键字段。"""
    if payload.get("schema_version") != "world-build.v1":
        raise AgentOutputError(
            f"world_builder schema_version must be 'world-build.v1', got {payload.get('schema_version')!r}"
        )


def _validate_character_designer(payload: dict[str, Any]) -> None:
    """project-init character_designer 契约：schema_version + characters 数组。"""
    if payload.get("schema_version") != "character-design.v1":
        raise AgentOutputError(
            f"character_designer schema_version must be 'character-design.v1', got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("characters"), list):
        raise AgentOutputError("character_designer output missing required array 'characters'")


def _validate_volume_outliner(payload: dict[str, Any]) -> None:
    """project-init volume_outliner 契约：schema_version + volume + chapter_seeds。"""
    if payload.get("schema_version") != "volume-outline.v1":
        raise AgentOutputError(
            f"volume_outliner schema_version must be 'volume-outline.v1', got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("volume"), dict):
        raise AgentOutputError("volume_outliner output missing required object 'volume'")
    if not isinstance(payload.get("chapter_seeds"), list):
        raise AgentOutputError("volume_outliner output missing required array 'chapter_seeds'")


def _validate_polisher(payload: dict[str, Any]) -> None:
    """Polisher（P1 文风润色）契约：schema_version + 关键字段。

    Schema 与 ``docs/agents/prompts/polisher-v1.md`` §7 对齐：
    - required: schema_version, prompt_version, polished_text, changes_summary
    - polished_text 必须是字符串（允许空串以便 mock 测试兜底）
    - changes_summary 必须是字符串（≤100 字由调用方软校验）
    """
    for key in ("schema_version", "prompt_version", "polished_text", "changes_summary"):
        if key not in payload:
            raise AgentOutputError(f"polisher output missing required field: {key!r}")
    if payload.get("schema_version") != "polisher-output.v1":
        raise AgentOutputError(
            f"polisher schema_version must be 'polisher-output.v1', got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("polished_text"), str):
        raise AgentOutputError("polisher polished_text must be a string")
    if not isinstance(payload.get("changes_summary"), str):
        raise AgentOutputError("polisher changes_summary must be a string")


_VALIDATORS = {
    "observer": _validate_observer,
    "director": _validate_director,
    "writer": _validate_writer,
    "critic": _validate_critic,
    "scene_planner": _validate_scene_planner,
    "premise_designer": _validate_premise_designer,
    "world_builder": _validate_world_builder,
    "character_designer": _validate_character_designer,
    "volume_outliner": _validate_volume_outliner,
    "polisher": _validate_polisher,
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
    "strip_think_blocks",
    "validate_contract",
    "strip_observer_violations",
    "OBSERVER_ALLOWED_KEYS",
    "OBSERVER_FORBIDDEN_KEYS",
]
