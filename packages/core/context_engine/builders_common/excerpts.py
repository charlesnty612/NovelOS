"""L1 实体摘要与触发注入策略（拆分自 builders_common.py，2026-09-13 V4.0）。

V2.0 Wave B 任务二：canon 实体（characters / locations / factions）按 ``inject_mode``
三态注入（auto 触发 / always 常驻 / never 剔除），未命中降级一行摘要；
触发扫描面 = 当前章节 ``plan_json`` 关键文本 + 前一章尾段（``_build_trigger_corpus``）。
P2 Context Engine：``sensory_anchors`` 零 DDL 方案（从 locations.data_json 解析）。

2026-10-16 角色设定丢失排查（E3）修三处**契约缺陷**（均非产品裁决）：

1. **``inject_mode`` 从不进 excerpt** ⇒ ``relevance._is_character_relevant`` 里
   ``char.get("inject_mode") == "always"`` 分支**恒假**——它的 docstring 明写
   「``inject_mode='always'`` 的实体始终完整保留」，而数据根本到不了判据
   （同族：本仓已登记的「规则登记了却不生效（惰性声明）」与「注释主张宽于/异于代码」）。
   0010 迁移注释亦写「always：无视命中，**永远完整注入**」⇒ DB 列已定义、契约已声明，
   缺的只是**把该列带进 payload**。character / location / faction 三处同修（同一函数里的
   兄弟字段漏同一件事，是本仓反复出现的形状）。
2. **``core_traits_summary`` 读的是已退役的 key ``personality``** ⇒ 恒为 ``""``。
   当前 ``core_json`` 写入契约是 ``{motivation, goal, conflict, distinctive_trait,
   relationships}``（见 ``apps/web/src/pages/bible/CharacterTab.tsx::CORE_FIELDS``
   与 ``workflows/project_init/pipeline.py::_normalize_characters``），前端注释明写
   「历史上的 personality/values/fears/desires/flaws 是早期方案，DB 现存 0 条」。
   **三个写入方**（CharacterTab 表单 / ``CharacterService.create|update`` /
   ``project_init._normalize_characters``）都不再写 ``personality``。
3. **JSON 列只做「解析失败兜底」不做「形状兜底」**：``state_json`` 列里若是合法 JSON
   但不是对象（``[1,2,3]``），``_parse_json`` 原样返回 list ⇒ ``current_state`` 变成
   **数组**，而 writer 提示词契约把它声明成 ``"object"``。改用单点
   ``story_state.write_helpers.decode_json_field``（批次九为 API 读路径建的同款单点），
   并在**降级发生时发一条带稳定 tag 的 WARNING**——此前是纯静默吞成 ``{}``。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Iterable

from packages.core.story_state.write_helpers import decode_json_field

from .common import _parse_json

_log = logging.getLogger(__name__)

# V2.0 Wave B 任务二：触发键相关常量
_INJECT_MODES = ("auto", "always", "never")  # 与 0010 CHECK 对齐
# 别名命中最小长度：避免「的」「是」「了」这种常用词误触发
_MIN_ALIAS_LEN = 2
#: entity_id 作为触发候选词的最小长度（防退化 id 命中一切；真实 id 形如
#: ``char_<12hex>``，恒远大于此）
_MIN_ENTITY_ID_LEN = 4
# 摘要行最大字符数（用于 auto+未命中 降级后的单行 description）
_SUMMARY_LINE_MAX_CHARS = 80

# JSON 列降级告警的**稳定标识**（判别测试与 grep 都按这串字面量找；写成常量后只有
# 一个改处，且 ruff 的 F401「定义未用」不会误伤——日志消息里以 %s 引用）。
_JSON_DEGRADED_WARN_TAG = "context_engine.entity_json_degraded"

# ``core_traits_summary`` 的取值 key 顺序：现行契约键在前，历史键在后兜底。
# 为什么保留 ``personality`` 兜底：备份导入 / 外部种子数据可能仍带旧键，删掉会让
# 这些行从「有摘要」退回「空摘要」——比修前更差。
_CORE_TRAIT_KEYS = ("distinctive_trait", "personality")


def _core_traits_summary(core: dict[str, Any]) -> str:
    """``core_json`` → 角色特质摘要字符串（取第一个非空文本值）。

    口径见模块 docstring 第 2 条：现行 ``core_json`` 契约里**没有** ``personality``
    （前端 ``CORE_FIELDS`` 与 project_init ``_normalize_characters`` 都只写
    motivation / goal / conflict / distinctive_trait / relationships），
    原实现 ``core.get("personality")`` 因此恒返 ``""``——该字段是**惰性声明**。
    非 str 值（dict / list / 数字）按「无摘要」处理，不做 ``str()`` 强转
    （避免把 ``{"a": 1}`` 渲染成 ``"{'a': 1}"`` 这种 Python repr 噪声进 prompt）。
    """
    for key in _CORE_TRAIT_KEYS:
        val = core.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _json_column_declares_content(raw: Any) -> bool:
    """该 JSON 列是否**声明过内容**（只用于决定要不要为降级发告警）。

    「空」的口径 = 空列 / 空白 / ``{}`` / ``[]`` / ``null`` —— 这些是**合法的空态**，
    不该被当成「数据丢了」。其余（非法 JSON、数组、标量、字符串对象）都算有内容。
    """
    if isinstance(raw, (dict, list)):
        return bool(raw)
    if not isinstance(raw, str):
        return False
    return raw.strip() not in ("", "{}", "[]", "null")


def _decode_entity_json(raw: Any, *, kind: str, entity_id: str) -> dict[str, Any]:
    """实体 JSON 列 → dict；解析失败 / 形状不符 → ``{}`` **并留痕**。

    单点走 ``decode_json_field``（与批次九 API 读路径同一判别式，``allowed=(dict,)``），
    避免「同一件事留两份判据各自漂移」。降级时发 WARNING：
    此前是纯静默吞成 ``{}``，作者看到的正文缺设定却**零告警、无从归因**——
    这正是本仓最忌的静默方向（同 ``_genre_pack_excerpt`` 收窄批次的理由）。

    告警只在**真的丢了内容**时发（合法空态 ``{}`` / NULL / 空串不告警），
    否则满库空列会把真信号淹掉（坑区：噪声淹没信号）。
    """
    decoded = decode_json_field(raw, fallback={})
    if decoded == {} and _json_column_declares_content(raw):
        _log.warning(
            "%s: kind=%s entity_id=%s 该 JSON 列无法作为对象使用（非法 JSON 或形状非对象），已降级为 {}",
            _JSON_DEGRADED_WARN_TAG, kind, entity_id,
        )
    return decoded


def _load_aliases(raw: Any) -> list[str]:
    """解析 ``aliases`` 列（JSON 字符串数组）。失败 / 空 → ``[]``。

    兼容：合法 list / JSON 字符串 / None / 非法值均按"无别名"处理；不抛错。
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [
                str(x).strip()
                for x in parsed
                if isinstance(x, (str, int, float)) and str(x).strip()
            ]
    return []


def _load_inject_mode(raw: Any) -> str:
    """解析 ``inject_mode`` 列；非法值回退 ``auto``（保行为一致）。"""
    if isinstance(raw, str) and raw in _INJECT_MODES:
        return raw
    return "auto"


def _is_triggered(
    name: str | None,
    aliases: Iterable[str],
    corpus: str,
    *,
    entity_id: str | None = None,
    name_hit: dict[str, bool] | None = None,
) -> bool:
    """实体是否被 corpus 触发（任一关键词命中即可）。

    - 命中：name / aliases **或 entity_id** 任一词面在 corpus 中出现（大小写不敏感
      子串匹配；corpus 与候选词都被自动 lower()，调用方无须预处理）。
    - aliases 词面长度 < ``_MIN_ALIAS_LEN`` 跳过（避免常用词误命中）；
      entity_id 长度 < ``_MIN_ENTITY_ID_LEN`` 同理跳过（防退化 id 命中一切）。
    - name / aliases / entity_id 全空 → 未触发。

    副作用：可把每个候选词的命中状态写到 ``name_hit``（调试 / 测试用；生产不读）。

    ⚠️ **``entity_id`` 是 2026-10-16 批次十一补的**（外部报告「角色圣经写闺蜜、
    正文里却是业务员」）。补它的依据是**语料侧本来就在放 id**：
    ``_build_trigger_corpus`` 明确把 ``plan.character_changes_planned[]`` 的
    ``character_id`` 收进语料，``writer_input`` 也把 ``scene_plan["characters"]``
    （存的是 **id** 而非名字）直接拼进语料——**但本函数从不看 id**。
    ⇒ 本场出场、且在 ``scene_plan.characters`` 里以 id 形式登记的角色，
    语料里有它的 id、判据却只认 name/aliases ⇒ 必然判为「未命中」⇒ 降级成
    ``{character_id, name, role}``，``core_json`` 整块丢弃 ⇒ 模型只见名字。
    同族形状见 AGENTS 硬规则 9「数据源存在 ≠ 已进装配」，只是失效面在**判据**侧。
    纯增量：只可能把「未命中」变成「命中」，**不会**减少任何注入。
    """
    if not corpus:
        return False
    corpus_lower = corpus.lower()
    candidates: list[str] = []
    if isinstance(name, str) and name.strip():
        candidates.append(name.strip())
    if isinstance(entity_id, str) and len(entity_id.strip()) >= _MIN_ENTITY_ID_LEN:
        candidates.append(entity_id.strip())
    for a in aliases:
        if isinstance(a, str) and len(a.strip()) >= _MIN_ALIAS_LEN:
            candidates.append(a.strip())
    if not candidates:
        return False
    for c in candidates:
        hit = c.lower() in corpus_lower
        if name_hit is not None:
            name_hit[c] = hit
        if hit:
            return True
    return False


def _beat_scan_text(beat: dict[str, Any]) -> list[str]:
    """``key_beats`` 的一个 object 元素 → 参与触发扫描的文本片段。

    取值键集与 ``relevance._extract_involved_entities`` 对 key_beats 的理解**同形**
    （同一个 dict 契约，不留两份各自漂移的口径）：文本键 ``beat_id`` / ``purpose``
    （``_validate_director`` 强制必填的两个字符串）+ 实体键 ``involved_characters`` /
    ``characters`` / ``involved_locations`` / ``location``（字符串或字符串数组）。
    非字符串值一律跳过——触发匹配是**字面子串**，塞 dict 的 repr 进扫描面只会制造
    噪声命中。
    """
    out: list[str] = []
    for key in ("beat_id", "purpose"):
        v = beat.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v)
    for key in ("involved_characters", "characters",
                "involved_locations", "location"):
        v = beat.get(key)
        if isinstance(v, str):
            if v.strip():
                out.append(v)
        elif isinstance(v, list):
            out.extend(x.strip() for x in v
                       if isinstance(x, str) and x.strip())
    return out


def _build_trigger_corpus(
    chapter_row: sqlite3.Row | None,
    *,
    previous_tail_text: str = "",
) -> str:
    """组装触发检测扫描面 = 当前章节 plan_json 关键文本 + 前一章尾段 300 字。

    无章节行 / 无 plan_json → 返回空串（调用方按"无计划文本 → 全注入"回退）。

    ⚠️ ``key_beats`` / ``character_changes_planned`` 的**形状分歧（2026-10-16 修）**：
    本函数原实现只收这两个数组里的**字符串**元素，而
    ``core.agent_runtime.structured_output._DIRECTOR_PLAN_ARRAYS`` 明写
    ``("key_beats", ("beat_id", "purpose"))`` 与 ``("character_changes_planned", ())``
    ——**校验器要求元素必须是 object**。⇒ 对任何通过校验的 director 计划，这两段
    **恒不贡献任何扫描面**，本函数自称的「plan 关键文本」实际只有 ``chapter_goal`` /
    ``core_conflict`` / ``turning_point`` / ``notes_for_planner`` 四项。
    实测（合成夹具，合法 director 计划）：corpus 长度 25，四项之外的角色名一个都没进去；
    把 key_beats 换成**校验器会拒绝**的字符串形态，角色名反而全部命中。

    字符串分支保留：``json_repair`` 抢救过的旧数据 / 早期计划里确有字符串元素，
    它们一直是有效扫描面（删掉等于回退已有能力）。
    """
    parts: list[str] = []
    if chapter_row is not None:
        try:
            plan = json.loads(chapter_row["plan_json"]) if chapter_row["plan_json"] else {}
        except (json.JSONDecodeError, TypeError):
            plan = {}
        if isinstance(plan, dict):
            for key in (
                "chapter_goal",
                "core_conflict",
                "turning_point",
                "notes_for_planner",
            ):
                v = plan.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
            beats = plan.get("key_beats")
            if isinstance(beats, list):
                for b in beats:
                    if isinstance(b, str) and b.strip():
                        parts.append(b)
                    elif isinstance(b, dict):
                        parts.extend(_beat_scan_text(b))
            changes = plan.get("character_changes_planned")
            if isinstance(changes, list):
                for c in changes:
                    if isinstance(c, str) and c.strip():
                        parts.append(c)
                    elif isinstance(c, dict):
                        for key in ("name", "character_id"):
                            v = c.get(key)
                            if isinstance(v, str) and v.strip():
                                parts.append(v)
    if previous_tail_text:
        parts.append(previous_tail_text)
    return "\n".join(parts)


def _summarize_entity(
    *,
    kind: str,
    name: str,
    entity_id: str,
    role: str | None = None,
    statement: str | None = None,
) -> dict[str, Any]:
    """auto + 未命中 时的降级摘要行（保留可识别信息 + 一句话描述）。

    - character: name + role（如 "主角（protagonist）"）
    - location/faction: name + statement（"王城 — 帝国首都"）
    - 截断到 ``_SUMMARY_LINE_MAX_CHARS`` 字符；其余字段剥离。
    - 包含 ``character_id`` / ``location_id`` / ``faction_id`` 便于预览 / 反查。
    """
    id_field = {
        "character": "character_id",
        "location": "location_id",
        "faction": "faction_id",
    }.get(kind, "id")
    line = name or ""
    if kind == "character" and role:
        line = f"{line}（{role}）"
    elif kind in ("location", "faction") and statement:
        st = statement.strip()
        if st:
            line = f"{line} — {st[:_SUMMARY_LINE_MAX_CHARS]}"
    line = line[:_SUMMARY_LINE_MAX_CHARS]
    return {
        id_field: entity_id,
        "name": name,
        "role": role if kind == "character" else None,
        "summary_line": line,
        "injection": "summary",
    }


def _apply_injection_policy(
    *,
    kind: str,
    name: str,
    entity_id: str,
    aliases: list[str],
    inject_mode: str,
    full_excerpt: dict[str, Any],
    summary_overrides: dict[str, Any] | None,
    trigger_corpus_lower: str,
    trigger_corpus_empty: bool,
) -> tuple[str, dict[str, Any]]:
    """对单个 canon 实体应用注入策略，返回 ``(status, payload)``。

    status: ``'full' | 'summary' | 'suppressed'``。
    payload:
    - ``full``: 完整 excerpt（叠加 ``_injection: 'full'`` 字段便于 preview 标记）。
    - ``summary``: 摘要 dict（来自 ``summary_overrides`` 或 :func:`_summarize_entity`）。
    - ``suppressed``: ``{id_field: entity_id, injection: 'suppressed'}``
      供 preview 用（**只发 id，不发 name**，2026-09-16 快穿位面隔离）；不入 ctx 顶层。

    回退策略：trigger_corpus_empty=True（无章节计划文本）→ auto 全部按 full 注入（保兼容）；
    always / never 严格按配置执行。
    """
    if inject_mode == "always":
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    if inject_mode == "never":
        id_field = {
            "character": "character_id",
            "location": "location_id",
            "faction": "faction_id",
        }.get(kind, "id")
        # 元标记只带 id（2026-09-16 快穿位面隔离）：整个 payload 被 agent_runtime.runner
        # 序列化进 user message，带 name 的 suppressed 块 = 把「上一世实体名」随归档一起
        # 送进模型，与 ``inject_mode='never'`` 的目的相反。preview 区分「已剔除 / 未命中
        # 降级」靠的是块归属 + ``injection`` 字段，name 非其所需（前端同时渲染 id）。
        return "suppressed", {id_field: entity_id, "injection": "suppressed"}
    # ⚠️ **2026-10-16 批次十一**：主角在此**必须**直接放行。
    #
    # :mod:`relevance` 的 docstring 承诺「主角（role=protagonist）与
    # ``inject_mode='always'`` 的实体始终完整保留」，而本函数是**它的上游**——
    # 主角若在这里被降级成 ``{id, name, role}``，下游 relevance 层再想放行也
    # 救不回来（``core_json`` 整块已经丢了）。⇒ **两层裁剪互相抵消**，
    # 「主角始终完整保留」这条既有承诺在整条链上一处都不兑现。
    #
    # 代价是主角的设定恒进 prompt，即使本章计划没提到他——这与「作者最新手工设定
    # 为最新鲜的设定」（用户 2026-10-16 裁决）一致：主角的身份/关系恰恰最不该
    # 靠计划文本兜。token 成本 = 一个主角的 core_json。
    if kind == "character" and full_excerpt.get("role") == "protagonist":
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    # inject_mode == "auto"
    if trigger_corpus_empty:
        # 无章节计划文本：保兼容，全注入（README 注明回退策略）
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    # entity_id 一并作为候选词：语料侧本就收了 character_id（见 _is_triggered
    # docstring 的批次十一注记），判据侧不接就是「有数据源、无消费」。
    if _is_triggered(name, aliases, trigger_corpus_lower, entity_id=entity_id):
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    # 未命中 → 降级为一行摘要
    if summary_overrides is None:
        summary_overrides = {
            "role": full_excerpt.get("role"),
            "statement": full_excerpt.get("statement"),
        }
    summary = _summarize_entity(
        kind=kind,
        name=name,
        entity_id=entity_id,
        role=summary_overrides.get("role"),
        statement=summary_overrides.get("statement"),
    )
    summary["_injection"] = "summary"
    return "summary", summary


def _character_state_excerpts(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    trigger_corpus: str | None = None,
) -> list[dict[str, Any]]:
    """characters JOIN 最新 state 行。

    V2.0 Wave B 任务二：
    - 始终读 ``aliases`` / ``inject_mode`` 两列（0010 加的）；
    - 传 ``trigger_corpus`` 时按注入策略三态过滤；不传 → 全部按 full 注入（向后兼容）。
    - 每条目额外带 ``_injection: 'full'|'summary'|'suppressed'`` 字段供 preview 标记。

    2026-10-16（E3 排查）三处契约修复，详见模块 docstring：
    - ``inject_mode`` 随 full 形态一并进 excerpt（此前 :mod:`relevance` 的
      「always 实体始终完整保留」分支恒假）；
    - ``core_traits_summary`` 改读现行契约键 ``distinctive_trait``
      （原读已退役的 ``personality``，恒为 ``""``）；
    - ``core_json`` / ``state_json`` 走 ``_decode_entity_json``（形状兜底 + 降级留痕）。
    """
    rows = conn.execute(
        """
        SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,
               c.aliases, c.inject_mode,
               cs.state_version AS latest_state_version,
               cs.state_json   AS latest_state_json
        FROM characters c
        LEFT JOIN character_states cs
          ON cs.character_id = c.character_id
         AND cs.state_version = (
              SELECT MAX(state_version) FROM character_states WHERE character_id = c.character_id
         )
        WHERE c.project_id = ?
        ORDER BY c.character_id ASC
        """,
        (project_id,),
    ).fetchall()
    apply_policy = trigger_corpus is not None
    corpus_lower = (trigger_corpus or "").lower() if apply_policy else ""
    corpus_empty = apply_policy and not trigger_corpus  # type: ignore[truthiness-function]
    out: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for r in rows:
        cid = r["character_id"]
        inject_mode = _load_inject_mode(r["inject_mode"])
        core = _decode_entity_json(r["core_json"], kind="character", entity_id=cid)
        state = _decode_entity_json(
            r["latest_state_json"], kind="character_state", entity_id=cid,
        )
        full_excerpt: dict[str, Any] = {
            "character_id": cid,
            "name": r["name"],
            "role": r["role"],
            "core_traits_summary": _core_traits_summary(core),
            "current_state": state,
            "knowledge_scope": r["visibility"],
            "core_json": core,
            "latest_state_version": r["latest_state_version"],
            # 必须带：relevance._is_character_relevant / _is_location_relevant /
            # _is_faction_relevant 都按 ``char.get("inject_mode") == "always"`` 放行，
            # 此前本键根本不在 excerpt 里 ⇒ 那三条「always 实体完整保留」的规则全是死代码。
            "inject_mode": inject_mode,
        }
        if not apply_policy:
            out.append(full_excerpt)
            continue
        aliases = _load_aliases(r["aliases"])
        status, payload = _apply_injection_policy(
            kind="character",
            name=r["name"],
            entity_id=cid,
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full_excerpt,
            summary_overrides={"role": r["role"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            suppressed.append(payload)
        else:
            out.append(payload)
    if apply_policy and suppressed:
        out.append({"_suppressed_characters": suppressed})
    return out


def _extract_sensory_anchors(locs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 locations 已解析的 data_json 中提取感官锚点（不加 DDL 的兜底方案）。

    优先级：
    1. ``data_json.sensory_anchors`` 为 list 时直接透传（标准扩展点，未来可在
       locations.data_json 中维护专用感官字段而不改 schema）。
    2. 否则从 ``data_json`` 中常见感官字段（sensory_details / atmosphere / smell /
       sound / light / texture / temperature）组合成一段 anchor_text。
    3. 仍无则回退到 ``statement`` 一句话陈述。

    当前 schema（0001_init.sql）locations 表无专用感官列，因此本函数是
    「零 DDL」约束下的兼容实现：有则取、无则空 list，不抛错。
    """
    anchors: list[dict[str, Any]] = []
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        lid = loc.get("location_id")
        name = loc.get("name")
        data = loc.get("data_json") or {}
        if not isinstance(data, dict):
            data = {}
        raw_anchors = data.get("sensory_anchors")
        if isinstance(raw_anchors, list):
            for item in raw_anchors:
                entry: dict[str, Any] = {"location_id": lid, "location_name": name}
                if isinstance(item, dict):
                    entry.update(item)
                elif isinstance(item, str):
                    entry["anchor_text"] = item
                else:
                    continue
                anchors.append(entry)
            continue
        parts: list[str] = []
        for key in (
            "sensory_details",
            "atmosphere",
            "smell",
            "sound",
            "light",
            "texture",
            "temperature",
        ):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(f"{key}: {val.strip()}")
        statement = loc.get("statement")
        if not parts and isinstance(statement, str) and statement.strip():
            parts.append(f"statement: {statement.strip()}")
        if parts:
            anchors.append(
                {
                    "location_id": lid,
                    "location_name": name,
                    "anchor_text": "；".join(parts),
                }
            )
    return anchors


def _world_state_excerpts(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    trigger_corpus: str | None = None,
) -> dict[str, Any]:
    """locations / factions / world_rules 全量组装。

    V2.0 Wave B 任务二：
    - locations / factions 应用触发策略（0010 加了 aliases + inject_mode）；
    - world_rules 保持常驻不变（PRD 视世界规则为硬设定）；
    - 不传 ``trigger_corpus`` → 全部 full 注入（向后兼容）。
    - 每条目带 ``_injection`` 字段；suppressed 项统一汇集到顶层 ``_suppressed_{kind}`` 列表
      （**只带 id，不带 name**——见 :func:`_apply_injection_policy`）。

    P2 Context Engine：
    - ``sensory_anchors`` 从 locations.data_json 解析（零 DDL 方案）。
    """
    locs = conn.execute(
        "SELECT location_id, name, statement, data_json, aliases, inject_mode "
        "FROM locations WHERE project_id = ? ORDER BY location_id ASC",
        (project_id,),
    ).fetchall()
    facs = conn.execute(
        "SELECT faction_id, name, statement, data_json, aliases, inject_mode "
        "FROM factions WHERE project_id = ? ORDER BY faction_id ASC",
        (project_id,),
    ).fetchall()
    rules = conn.execute(
        "SELECT world_rule_id, name, statement, data_json FROM world_rules "
        "WHERE project_id = ? ORDER BY world_rule_id ASC",
        (project_id,),
    ).fetchall()
    apply_policy = trigger_corpus is not None
    corpus_lower = (trigger_corpus or "").lower() if apply_policy else ""
    corpus_empty = apply_policy and not trigger_corpus  # type: ignore[truthiness-function]

    def _loc_payload(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "location_id": r["location_id"],
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json(r["data_json"]) or {},
            # 与 character 侧同因：relevance._is_location_relevant 的 always 分支
            # 同样读 ``loc.get("inject_mode")``，不带这个键 ⇒ 恒假（见模块 docstring）。
            "inject_mode": _load_inject_mode(r["inject_mode"]),
        }

    def _fac_payload(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "faction_id": r["faction_id"],
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json(r["data_json"]) or {},
            "inject_mode": _load_inject_mode(r["inject_mode"]),
        }

    loc_out: list[dict[str, Any]] = []
    loc_supp: list[dict[str, Any]] = []
    for r in locs:
        full = _loc_payload(r)
        if not apply_policy:
            loc_out.append(full)
            continue
        aliases = _load_aliases(r["aliases"])
        inject_mode = _load_inject_mode(r["inject_mode"])
        status, payload = _apply_injection_policy(
            kind="location",
            name=r["name"],
            entity_id=r["location_id"],
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full,
            summary_overrides={"statement": r["statement"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            loc_supp.append(payload)
        else:
            loc_out.append(payload)

    fac_out: list[dict[str, Any]] = []
    fac_supp: list[dict[str, Any]] = []
    for r in facs:
        full = _fac_payload(r)
        if not apply_policy:
            fac_out.append(full)
            continue
        aliases = _load_aliases(r["aliases"])
        inject_mode = _load_inject_mode(r["inject_mode"])
        status, payload = _apply_injection_policy(
            kind="faction",
            name=r["name"],
            entity_id=r["faction_id"],
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full,
            summary_overrides={"statement": r["statement"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            fac_supp.append(payload)
        else:
            fac_out.append(payload)

    result: dict[str, Any] = {
        "current_time_in_story": None,  # 初始空；后续可由 world_changes(time) 维护
        "current_location": None,
        "locations": loc_out,
        "active_factions": fac_out,
        "world_rules_relevant": [
            {
                "world_rule_id": r["world_rule_id"],
                "name": r["name"],
                "statement": r["statement"],
            }
            for r in rules
        ],
        # P2 Context Engine：感官锚点零 DDL 方案——从已注入 locations 的 data_json 解析。
        # 未命中/降级为 summary 的 location 因 data_json 为空而不贡献锚点。
        "sensory_anchors": _extract_sensory_anchors(loc_out),
    }
    if apply_policy:
        if loc_supp:
            result["_suppressed_locations"] = loc_supp
        if fac_supp:
            result["_suppressed_factions"] = fac_supp
    return result
