r"""结构化输出提取（Sprint 3）。

职责：从 LLM 原始文本中提取 JSON 对象并解析；提供契约校验钩子。

设计要点：
- LLM 经常在 JSON 外包裹 ``\`\`\`json ... \`\`\`` 围栏或在前后追加解释句。
  提取策略：
  1. 去除 ```json / ``` 围栏（大小写不敏感）；保留围栏内文本。
  2. 在剩余文本中找首个 ``{`` 与末个 ``}``，截取子串（忽略控制字符之外的乱码）。
  3. ``json.loads``；失败抛 :class:`AgentOutputError`（由 runner 捕获并决定是否重试）。
- 契约校验：多档 ``expected``（``"observer"`` / ``"director"`` / ``"writer"`` /
  ``"director_planner"`` 等）按 ``agent-contracts-v0.md`` §5.2 / §3.2 / §4.2 给出最小集断言。
  - ``observer``：顶层必须恰为 7 个 change 数组键（结构错误仍抛 :class:`AgentOutputError`）；
    含 10 元信息字段或 3 辅助字段（**越权字段**）时**剥离后继续**，不抛错、不触发重试。
  - ``director``：必须含 ``schema_version == "director-plan.v1"``。
  - ``director_planner``（P1 合并调用）：顶层走导演契约；``scene_plan`` 子对象在场时走场景契约
    （``schema_version == "scene-plan.v1"``）；另按输入 payload 做 ID 白名单核销
    （hook / debt / character / location）。
  - ``writer``：必须含 ``schema_version == "writer-output.v1"`` 与 ``prose`` / ``self_report``。
  - ``None``：只要求合法 JSON。
- 校验失败抛 :class:`AgentOutputError`，由 runner 捕获并重试 1 次（按 agent-contracts §6 重试原则）。
  **剥离策略** 是 observer 的特殊路径（不重试，仅剥离）；见 :func:`strip_observer_violations`。
"""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair

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


def extract_json(
    text: str,
    *,
    finish_reason: str | None = None,
    return_meta: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    """从 LLM 输出中提取首个 JSON 对象并解析。

    步骤（三级解析链）：
    1. 去围栏 / 去除 ``<think>`` 块。
    2. 取首个 ``{`` 与末个 ``}`` 之间的子串。
    3. 一级：``json.loads``（严格模式，零开销覆盖正常输出）。
    4. 二级：失败则 ``json.loads(strict=False)``，放宽字符串内控制字符（生产实测
       observer 输出含裸控制符 ch074 commit 首次失败即此因）。
    5. 三级：再失败则用 ``json_repair.loads`` 做语法修复——处理缺逗号、未转义引号、
       截断 JSON 等常见 LLM 语法错误（生产事故：observer 在 char 562 缺逗号，
       ``Expecting ',' delimiter``，strict=False 救不回，重试也失败，导致提交失败）。

    ``finish_reason``（可选）：上游 provider 透传的 ``choices[0].finish_reason``
    （OpenAI 兼容语义；典型值 ``stop`` / ``length``）。仅当**剥围栏/think 后内容为空**时
    用于在报错文案中区分根因—— ``"length"`` 几乎一定是 LLM max_tokens 预算被思考耗尽
    （自适应思考 / 长 reasoning），需提示调大 ``params.max_tokens``，而不是怀疑解析器。
    其它取值（含 ``None``）不改变既有错误文案，只在末尾追加 ``(finish_reason=xxx)``
    便于排障。

    ``return_meta``（默认 ``False``）：为兼容既有调用方（runner / 测试套件），
    默认仍直接返回 ``dict``；开启后返回 ``(payload, meta)``，其中 ``meta`` 是
    ``dict``，至少包含 ``repaired: bool``（三级兜底是否触发）与
    ``merged_object_count: int | None``（相邻顶层对象合并兜底是否触发，见
    :func:`_merge_adjacent_objects`）。runner 层据此写入 ``warn: JSON auto-repaired`` /
    ``warn: merged N adjacent JSON objects`` 落库，使「修复 / 合并」事件可观测——两类产物
    仍需过 :func:`validate_contract` 结构校验，**内容级静默损坏风险由 warn 落库对冲**，
    不静默吞错。
    """
    if not isinstance(text, str):
        raise AgentOutputError(f"output is not a string: {type(text).__name__}")
    cleaned = strip_think_blocks(strip_code_fence(text))
    if not cleaned:
        # 空内容是排障第一坑：原报错 "empty output after stripping fences" 把
        # 「自适应思考耗尽 max_tokens」「provider 真正返回空」「messages 构造错」
        # 三种根因混为一谈。finish_reason='length' 时给出明确可行动提示。
        if finish_reason == "length":
            raise AgentOutputError(
                "输出为空：max_tokens 预算被思考耗尽（finish_reason=length）"
                "——请调大档案 params 的 max_tokens（建议 16384）"
            )
        # 其它情况（None / stop / 上游自定义值）：保留原 message + finish_reason 标注
        suffix = (
            f" (finish_reason={finish_reason})"
            if isinstance(finish_reason, str) and finish_reason
            else ""
        )
        raise AgentOutputError(f"empty output after stripping fences{suffix}")
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise AgentOutputError(f"no JSON object braces found in output: {cleaned[:80]!r}")
    candidate = cleaned[first : last + 1]
    repaired = False
    merged_count: int | None = None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # 二级兜底：strict=False 放宽字符串内控制字符
        try:
            parsed = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            # 二级半兜底（P1 合并调用）：相邻顶层 JSON 对象合并。
            # 合并调用（director_planner）实测会出现「先一个导演计划对象、再一个
            # scene_plan 对象」的相邻双对象输出——「首 { 到末 }」整段交给 json.loads
            # 必然 "Extra data"。本兜底按括号配平切分出各顶层对象，逐段解析后**键无冲突**
            # 才合并；**键冲突不猜**（可能是两次完整回答），判解析失败交给上层重试——
            # 不回落到 json_repair（它会把这种形态悄悄截成半个答案）。
            # 仅在 ≥2 个顶层对象时生效，单对象乱码（缺逗号 / 截断）行为零变化。
            segs = _top_level_objects(cleaned)
            if len(segs) >= 2:
                merged, merge_meta = _merge_adjacent_objects(segs)
                if merged is None:
                    raise AgentOutputError(
                        "multiple adjacent JSON objects cannot be merged "
                        f"({merge_meta.get('merge_reason')}); raw={candidate[:200]!r}"
                    ) from exc
                parsed = merged
                merged_count = int(merge_meta.get("merged_object_count") or 0)
            else:
                # 三级兜底：json_repair 处理常见 LLM 语法错误（缺逗号 / 未转义引号 / 截断）。
                # 仅当 repair 后顶层是 dict 才接受（库对完全乱码可能返回空字符串等非 dict），
                # 否则视为修复失败走抛错路径。
                try:
                    repaired_obj = json_repair.loads(candidate)
                except Exception as repair_exc:  # noqa: BLE001
                    raise AgentOutputError(
                        _format_json_error(exc, candidate)
                    ) from repair_exc
                if not isinstance(repaired_obj, dict):
                    raise AgentOutputError(
                        _format_json_error(exc, candidate)
                    ) from exc
                parsed = repaired_obj
                repaired = True
    if not isinstance(parsed, dict):
        raise AgentOutputError(f"JSON top-level is not an object: {type(parsed).__name__}")
    if return_meta:
        return parsed, {
            "repaired": repaired,
            "merged_object_count": merged_count,
        }
    return parsed


def _top_level_objects(cleaned: str) -> list[str]:
    """扫描出所有**顶层完整 JSON 对象**片段（字符串 / 转义内的括号不计）。

    与 :func:`extract_json` 的「首 ``{`` 到末 ``}``」不同：后者把「相邻两个对象」整体
    当成一个候选串交给 ``json.loads``，必然 ``Extra data``。本函数按 ``{}`` 配平切分，
    供 :func:`_merge_adjacent_objects` 使用；未配平的尾部残片（截断输出）不产出片段。

    额外约束（相对 A/B 驱动参考实现的收紧）：只有 **``[]`` 深度也为 0** 的对象才计入
    ——数组包裹的形态（如 ``[{...}, {...}]``）是另一种语义（多元素列表，不是「相邻
    顶层对象」），不参与合并，保持既有「非 dict 顶层 → 解析失败」行为。
    """
    segs: list[str] = []
    depth = 0
    list_depth = 0
    start: int | None = None
    in_str = False
    esc = False
    for i, ch in enumerate(cleaned):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            list_depth += 1
        elif ch == "]":
            if list_depth > 0:
                list_depth -= 1
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    if list_depth == 0:
                        segs.append(cleaned[start : i + 1])
                    start = None
    return segs


def _merge_adjacent_objects(segs: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """把「N≥2 个相邻顶层对象」合并为一个 dict；不可合并返回 ``(None, meta)``。

    判据（与 A/B 驱动参考实现 ``p1_ab/run_merged_ab_v2.merge_adjacent_objects`` 同形，
    差异仅在分段由调用方 :func:`_top_level_objects` 预扫后传入）：

    1. 段数 < 2 → 不合并（单对象形态交回既有 repair 路径）；
    2. 逐段解析（``json.loads`` → ``strict=False`` → ``json_repair``），任一段不可解析
       或不是对象 → 不合并；
    3. 逐键合并：**键冲突（同名键值不同）不猜**——可能是两次完整回答，判为语义不明，
       返回 ``(None, {"merge_reason": ...})``，由调用方按解析失败处理（触发既有重试）。

    合并成功时 meta 带 ``merged_object_count`` / ``merged_keys``（供 runner 落
    ``warn: merged N adjacent JSON objects`` 观测）。
    """
    if len(segs) < 2:
        return None, {"merge_reason": f"top_level_objects={len(segs)}"}
    objs: list[dict[str, Any]] = []
    for idx, seg in enumerate(segs):
        try:
            obj = json.loads(seg)
        except json.JSONDecodeError:
            try:
                obj = json.loads(seg, strict=False)
            except json.JSONDecodeError:
                try:
                    obj = json_repair.loads(seg)
                except Exception:  # noqa: BLE001 —— 单段修复失败即放弃合并
                    return None, {"merge_reason": f"segment[{idx}] unparsable"}
        if not isinstance(obj, dict):
            return None, {"merge_reason": f"segment[{idx}] not an object"}
        objs.append(obj)
    merged: dict[str, Any] = {}
    conflicts: list[str] = []
    for obj in objs:
        for k, v in obj.items():
            if k in merged and merged[k] != v:
                conflicts.append(k)
            else:
                merged.setdefault(k, v)
    if conflicts:
        return None, {"merge_reason": f"key conflicts: {sorted(set(conflicts))}"}
    return merged, {
        "merged_object_count": len(objs),
        "merged_keys": sorted(merged.keys()),
    }


def _format_json_error(exc: json.JSONDecodeError, candidate: str) -> str:
    """构造 JSON 解析失败的报错文案：错误位置前后各 100 字符上下文 + 头部 200 字符，
    总长控制在 500 字符内，便于排障时定位具体位置而不暴露全量 LLM 输出。
    """
    pos = getattr(exc, "pos", None)
    if isinstance(pos, int) and 0 <= pos <= len(candidate):
        ctx_start = max(0, pos - 100)
        ctx_end = min(len(candidate), pos + 100)
        ctx_snippet = candidate[ctx_start:ctx_end]
        return (
            f"invalid JSON: {exc}; "
            f"pos={pos} ctx={ctx_snippet!r}; "
            f"raw={candidate[:200]!r}"
        )[:500]
    return f"invalid JSON: {exc}; raw={candidate[:200]!r}"


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


_VALID_DEEP_REVIEW_LAYERS = frozenset({"setting", "beat", "behavior"})
_VALID_DEEP_REVIEW_SEVERITIES = frozenset({"high", "medium", "low"})


def _validate_deep_reviewer(payload: dict[str, Any]) -> None:
    """Deep Reviewer（V1.3 二审 AI）契约：结构合规 + 枚举合法。

    Schema 与 ``docs/agents/prompts/deep_reviewer-v1.md`` §7 对齐：
    - required: schema_version, prompt_version, chapter_id, verdict, overall_comment, issues
    - verdict ∈ {pass, revise}
    - issues 必须是 list（允许空）；每条 issues[i] 必含 layer / severity / quote / suggestion
    - issues[i].layer ∈ {setting, beat, behavior}
    - issues[i].severity ∈ {high, medium, low}

    ``quote`` 可溯源 / 长度上限 / severity 标尺 / 层内顺序由调用方
    （chapter_review._deep_review_node）做软校验；本契约只校验**结构层**。
    """
    for key in ("schema_version", "prompt_version", "chapter_id", "verdict", "overall_comment", "issues"):
        if key not in payload:
            raise AgentOutputError(f"deep_reviewer output missing required field: {key!r}")
    if payload.get("schema_version") != "deep-review.v1":
        raise AgentOutputError(
            f"deep_reviewer schema_version must be 'deep-review.v1', "
            f"got {payload.get('schema_version')!r}"
        )
    verdict = payload.get("verdict")
    if verdict not in ("pass", "revise"):
        raise AgentOutputError(
            f"deep_reviewer verdict must be 'pass' or 'revise', got {verdict!r}"
        )
    if not isinstance(payload["overall_comment"], str):
        raise AgentOutputError("deep_reviewer overall_comment must be a string")
    if not isinstance(payload["issues"], list):
        raise AgentOutputError(
            f"deep_reviewer issues must be a list, got {type(payload['issues']).__name__}"
        )
    for idx, issue in enumerate(payload["issues"]):
        if not isinstance(issue, dict):
            raise AgentOutputError(f"deep_reviewer issues[{idx}] must be an object")
        for k in ("layer", "severity", "quote", "suggestion"):
            if k not in issue:
                raise AgentOutputError(
                    f"deep_reviewer issues[{idx}] missing required field: {k!r}"
                )
        if issue["layer"] not in _VALID_DEEP_REVIEW_LAYERS:
            raise AgentOutputError(
                f"deep_reviewer issues[{idx}].layer {issue['layer']!r} "
                f"not in {sorted(_VALID_DEEP_REVIEW_LAYERS)}"
            )
        if issue["severity"] not in _VALID_DEEP_REVIEW_SEVERITIES:
            raise AgentOutputError(
                f"deep_reviewer issues[{idx}].severity {issue['severity']!r} "
                f"not in {sorted(_VALID_DEEP_REVIEW_SEVERITIES)}"
            )


_VALIDATORS = {
    "observer": _validate_observer,
    "director": _validate_director,
    "writer": _validate_writer,
    "critic": _validate_critic,
    "deep_reviewer": _validate_deep_reviewer,
    "scene_planner": _validate_scene_planner,
    "premise_designer": _validate_premise_designer,
    "world_builder": _validate_world_builder,
    "character_designer": _validate_character_designer,
    "volume_outliner": _validate_volume_outliner,
    "polisher": _validate_polisher,
}


# ---------------------------------------------------------------------------
# Director-Planner（P1 合并调用）：顶层导演契约 + scene_plan 子契约 + 输入 ID 白名单
# ---------------------------------------------------------------------------

# 白名单报错条数上限（超出部分折叠计数）——报错文本会拼进 runner 的重试提示，
# 无上限会让「整段 JSON 全幻觉」的输出把重试提示撑爆。
_WHITELIST_ERROR_CAP = 5


def _input_id_sets(input_payload: dict[str, Any]) -> dict[str, Any]:
    """从 agent 输入 payload 抽取 ID 白名单集合（hook / debt / character / location）。

    ``*_empty`` 标记对应输入表是否为空数组 / 键缺席——空输入下**任何**条目都是幻觉
    （prompt §7「输入为空数组或键缺席时，两者必须为 ``[]``」）。
    """
    hooks = input_payload.get("hook_ledger_excerpt") or []
    debts = input_payload.get("narrative_debt_excerpt") or []
    chars = input_payload.get("available_characters") or []
    locs = input_payload.get("available_locations") or []
    return {
        "hooks": {
            h.get("hook_id")
            for h in hooks
            if isinstance(h, dict) and h.get("hook_id")
        },
        "debts": {
            d.get("debt_id")
            for d in debts
            if isinstance(d, dict) and d.get("debt_id")
        },
        "chars": {
            c.get("character_id")
            for c in chars
            if isinstance(c, dict) and c.get("character_id")
        },
        "locs": {
            loc.get("location_id")
            for loc in locs
            if isinstance(loc, dict) and loc.get("location_id")
        },
        "hooks_empty": not hooks,
        "debts_empty": not debts,
    }


def _whitelist_errors(parsed: dict[str, Any], input_payload: dict[str, Any]) -> list[str]:
    """输出引用的 ID 必须 ∈ 输入集合（E-MRG-16 / E-DIR-03；P1 合并调用核销）。

    实现与 A/B 驱动参考实现 ``p1_ab/run_merged_ab_v2.whitelist_errors`` 逐条对齐：

    - ``hook_handling[].hook_id`` ∈ 输入 ``hook_ledger_excerpt`` 的 ID 集合；
      **输入为空 / 键缺席时该项必须为 ``[]``**；
    - ``debt_handling[].debt_id`` ∈ 输入 ``narrative_debt_excerpt`` 的 ID 集合，同款空输入规则；
    - ``key_beats[].involved_characters`` / ``involved_locations`` ∈ ``available_characters`` /
      ``available_locations``；
    - ``scene_plan.scenes[].characters`` / ``location`` / ``slots[].characters`` 同款校验
      （location 允许 null）。

    不做静默丢弃：命中即由调用方按输出无效处理（走既有 output-invalid 重试）。
    """
    ids = _input_id_sets(input_payload)
    errs: list[str] = []

    hook_handling = parsed.get("hook_handling")
    if not isinstance(hook_handling, list):
        errs.append(f"hook_handling must be a list, got {type(hook_handling).__name__}")
    else:
        for i, item in enumerate(hook_handling):
            hid = item.get("hook_id") if isinstance(item, dict) else None
            if ids["hooks_empty"]:
                errs.append(
                    f"hook_handling[{i}] must be [] (input hook_ledger_excerpt is empty), got {hid!r}"
                )
            elif hid not in ids["hooks"]:
                errs.append(f"hook_handling[{i}].hook_id {hid!r} not in input hook set")

    debt_handling = parsed.get("debt_handling")
    if not isinstance(debt_handling, list):
        errs.append(f"debt_handling must be a list, got {type(debt_handling).__name__}")
    else:
        for i, item in enumerate(debt_handling):
            did = item.get("debt_id") if isinstance(item, dict) else None
            if ids["debts_empty"]:
                errs.append(
                    f"debt_handling[{i}] must be [] (input narrative_debt_excerpt is empty), got {did!r}"
                )
            elif did not in ids["debts"]:
                errs.append(f"debt_handling[{i}].debt_id {did!r} not in input debt set")

    for i, beat in enumerate(parsed.get("key_beats") or []):
        if not isinstance(beat, dict):
            continue
        for cid in beat.get("involved_characters") or []:
            if cid not in ids["chars"]:
                errs.append(f"key_beats[{i}].involved_characters {cid!r} not in available_characters")
        for lid in beat.get("involved_locations") or []:
            if lid not in ids["locs"]:
                errs.append(f"key_beats[{i}].involved_locations {lid!r} not in available_locations")

    scenes = (parsed.get("scene_plan") or {}).get("scenes") or []
    if isinstance(scenes, list):
        for i, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            for cid in scene.get("characters") or []:
                if cid not in ids["chars"]:
                    errs.append(f"scene[{i}].characters {cid!r} not in available_characters")
            loc_id = scene.get("location")
            if loc_id is not None and loc_id not in ids["locs"]:
                errs.append(f"scene[{i}].location {loc_id!r} not in available_locations")
            for j, slot in enumerate(scene.get("slots") or []):
                if not isinstance(slot, dict):
                    continue
                for cid in slot.get("characters") or []:
                    if cid not in ids["chars"]:
                        errs.append(
                            f"scene[{i}].slots[{j}].characters {cid!r} not in available_characters"
                        )
    return errs


def _validate_director_planner(
    payload: dict[str, Any], *, input_payload: dict[str, Any] | None = None
) -> None:
    """Director-Planner（P1 合并调用）契约：顶层导演契约 + ``scene_plan`` 子对象场景契约。

    契约口径（见 ``docs/agents/prompts/director_planner-v1.md`` §7 / §9 E-MRG-01）：

    - 顶层：同 :func:`_validate_director`（``schema_version == "director-plan.v1"``）。
      下游（chapter-plan 的 ``save_plan``）按缺省值读各字段，故此处不额外收紧必填面。
    - ``scene_plan``：**在场**时必须整份满足场景契约（:func:`_validate_scene_planner`，
      含 ``schema_version == "scene-plan.v1"`` / scenes 非空 / pov 与 slot.type 枚举）；
      **缺席**时按「计划-only 输出」放行——合并调用只丢了场景段，计划仍可用，
      由 chapter-plan 记录 ``scene_plan_status`` 并由 chapter-write 走既有
      scene_planner 降级路径，**不**把用户的一次「生成计划」整体判失败。
    - ``input_payload`` 非 None 时追加 ID 白名单核销（:func:`_whitelist_errors`）：
      命中即抛 :class:`AgentOutputError`，由 runner 走既有 output-invalid 重试。

    **与 prompt §9 E-MRG-01 的显式分歧（留痕）**：E-MRG-01 是 A/B **验收规则**
    （「scene_plan 任缺 = 不通过」，用于评判 prompt 产出质量）；本校验器是**产线闸门**，
    按「降级不阻断」口径把「缺 scene_plan」判为可降级（章节计划仍可下单写作），
    而把「scene_plan 在场但违规」判为硬失败（走重试）。两条口径的差异是刻意设计：
    验收要严、产线要活；场景段的实际兜底由 chapter-write 的既有 scene_planner 路径承担。
    """
    _validate_director(payload)
    scene_plan = payload.get("scene_plan")
    if scene_plan is not None:
        if not isinstance(scene_plan, dict):
            raise AgentOutputError(
                f"director_planner scene_plan must be an object, got {type(scene_plan).__name__}"
            )
        _validate_scene_planner(scene_plan)
    if input_payload is not None:
        errs = _whitelist_errors(payload, input_payload)
        if errs:
            shown = errs[:_WHITELIST_ERROR_CAP]
            suffix = f"; (+{len(errs) - len(shown)} more)" if len(errs) > len(shown) else ""
            raise AgentOutputError(
                "director_planner output violates input ID whitelist: "
                + "; ".join(shown)
                + suffix
            )


def validate_contract(
    expected: str | None,
    payload: dict[str, Any],
    *,
    input_payload: dict[str, Any] | None = None,
) -> None:
    """按 ``expected`` 分派契约校验；``None`` 跳过。失败抛 :class:`AgentOutputError`。

    ``input_payload``（可选）：本次调用的 agent 输入 payload。目前仅 ``director_planner``
    消费它（ID 白名单核销需要输入集合）；其它 ``expected`` 忽略该参数，行为零变化。
    """
    if expected is None:
        return
    if expected == "director_planner":
        # 该契约需要输入侧集合（白名单），不入 _VALIDATORS（其它校验器签名是单参数）。
        _validate_director_planner(payload, input_payload=input_payload)
        return
    validator = _VALIDATORS.get(expected)
    if validator is None:
        # 未知 expected → 当成 None 处理；调用方应只传已登记档位之一
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
