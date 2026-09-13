"""``builders_common`` 包门面（2026-09-13 V4.0 模块化拆分；纯搬家、零行为变化）。

拆分自单文件 ``builders_common.py``（2104 行），按职责分四子模块：

- ``common``     共享工具 / 题材包注入（genre helpers）/ peek 预读（含 ``get_connection`` 解析）
- ``excerpts``   L1 实体摘要与触发注入策略（characters / locations / factions / sensory）
- ``ledger``     台账与检索类数据收集（hooks / debts / plot events / branches / commit touch）
- ``summaries``  摘要链 / 尾段 / 文风样例 / L2 计划与场景形态 helper

本文件保留**全部既有顶层名的再导出**（显式 import + 全量 ``__all__``，含全部私有名）：
``from packages.core.context_engine.builders_common import X`` 与 ``builders_common.X``
属性访问零改动可用；新代码可直接从子模块导入。
``get_connection`` 仍绑定 ``packages.core.db.get_connection``（连接工厂补丁点；子模块
数据取数经 :func:`builders_common.common.get_connection` 延迟解析回本门面）。

原 ``builders_common.py`` 模块 docstring（拆分前，逐字保留）：

Context builders（Sprint 4-A + Sprint 15/V1.3 + V2.0 Wave B 任务二 + V2.0 Wave C 任务一/二）。

按 ``docs/agents/agent-contracts-v0.md`` §3.1 / §4.1 / §5.1 组装 Director / Writer / Observer 输入。

设计要点（MVP 简化版）：
- **不做 L0-L9 token 裁剪**：按字段全量塞入 context。完整 token 预算分配与
  层级压缩留待后续 Sprint（README 注明 deviation）。
- ``chapter.target_word_count`` 默认 3000（V3.9 5.1 单源化，``wordcount.DEFAULT_TARGET_WORD_COUNT``；
- ``recent_prose`` 取上一章最新 draft 末尾 500 字（无 draft → 空字符串）。
- ``draft_text`` 取该章最新 draft 的 ``content`` 列（drafts 表 DDL line 274）。
- 全部按章节 DB 状态实时组装；L0/L1 装配结果进程内缓存（V2.0 Wave C 任务二，
  见本模块 ``_cache_*`` 内部实现 + README）。

Sprint 15 / V1.3 新增：
- Writer 注入 ``author_style_samples``：取该项目最近创建的 ≤2 篇、每篇截断 ≤1000 字，
  引导 writer 模仿「句式 / 用词 / 节奏」（非内容）。
- Director 注入 ``open_foreshadow_list`` 的 overdue 阈值项目级可配：
  从 ``projects.foreshadow_overdue_chapters`` 读取，取不到 / NULL 回退 30。
- SQL 截断修复：open_foreshadow_list 直接 ``ORDER BY overdue_first, importance DESC,
  introduced ASC LIMIT 20``，把 overdue 判定推到 SQL，去掉旧「预取 60 再内存排序」
  截断边界 bug（>60 条伏笔时 overdue 项不再丢失）。

V2.0 Wave B 任务二新增：
- 设定条件触发动态注入（对标 NovelAI Lorebook 关键词触发 / NovelCrafter Codex 4 态）。
- canon 实体（characters / locations / factions）按 ``inject_mode`` 列三态注入：
  - ``auto`` + 命中实体名/别名 → 完整注入；
  - ``auto`` + 未命中 → 降级为一行摘要（name + role/statement）；
  - ``always`` → 完整注入（无视命中）；
  - ``never`` → 不注入（仅留在 preview 列表中标记 suppressed）。
- 命中检测扫描面：当前章节 plan_json（chapter_goal / core_conflict / turning_point /
  key_beats / character_changes_planned）+ 前一章尾段 300 字（``previous_chapter_tail``）。
- 检测算法：实体 name 或 aliases 任一词面命中（大小写不敏感的子串匹配；中文直接子串）；
  aliases 词面须 ≥2 字符避免误触发（短词如「的」「是」会被忽略）。
- 回退策略：章节 plan_json 为空时（无章节计划），auto 默认全量注入，保兼容；
  always / never 严格按配置执行（即使无计划文本也按模式生效）。
- L0 world_rules 保持常驻不变——只对实体类做条件化（世界规则属硬设定）。

V2.0 Wave C 任务一新增：
- 召回混合层（章节正文 FTS5）：按当前章节计划文本从 chapter_fts 召回 top-3
  相关历史片段，写入 director / writer 输入顶层 ``recalled_passages`` 键。
- 关键词提取口径：中文 2-gram + 实体名整体 token + 英文分词 + 停用词过滤
  （详见 packages/core/retrieval/service.py + README）。
- 召回片段每段截断 ≤300 字，带 ``chapter_id`` / ``chapter_no`` / ``snippet`` /
  ``rank`` 字段。无索引 / 无命中 → 空 list（不阻断装配）。
"""

from __future__ import annotations

from packages.core.db import get_connection

from .common import (
    _ASSEMBLY_TOKEN_BUDGET,
    _DEFAULT_STYLE_CONSTRAINTS,
    _DEFAULT_TARGET_WORD_COUNT,
    _GENRE_PACK_BEAT_CAP,
    _GENRE_PACK_CONSUMER_DIRECTOR,
    _GENRE_PACK_CONSUMER_PLANNER,
    _GENRE_PACK_CONSUMER_WRITER,
    _GENRE_PACK_FIELD_CHARS,
    _GENRE_PACK_PACING_MAX_CHARS,
    _GENRE_PACK_PAYOFF_CAP,
    _GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS,
    _GENRE_PACK_STYLE_MAX_CHARS,
    _PEEK_CHAPTER_FALLBACK_SQL,
    _PEEK_CHAPTER_SQL,
    _TOKEN_DIVISOR,
    _as_int_or,
    _attach_assembly_meta,
    _chapter_project_id,
    _compose_density_text,
    _empty_chapter_peek,
    _estimate_tokens,
    _fingerprint_word_band_json,
    _genre_pack_excerpt,
    _latest_draft,
    _normalize_genre_pacing,
    _parse_json,
    _peek_active_canon_id,
    _peek_chapter_context,
    _peek_chapter_context_conn,
    _peek_chapter_context_degraded,
    _peek_chapter_no_state_version,
    _peek_chapter_no_state_version_plan,
    _peek_project_context,
    _peek_project_id_from_chapter,
    _peek_project_word_band_json,
    _project_max_state_version,
    _ratio_instruction,
    _row_to_chapter,
    _row_to_project,
    _safe_copy,
    _summarize_genre_structure_templates,
    _textualize_genre_payoff_types,
    _textualize_genre_planner_payoffs,
    _trim_genre_pacing,
    _trim_genre_style,
    _truncate_text,
    _try_active_canon_id,
    _try_project_genre_pack_ref,
    _try_project_word_band_json,
)
from .excerpts import (
    _INJECT_MODES,
    _MIN_ALIAS_LEN,
    _SUMMARY_LINE_MAX_CHARS,
    _apply_injection_policy,
    _build_trigger_corpus,
    _character_state_excerpts,
    _extract_sensory_anchors,
    _is_triggered,
    _load_aliases,
    _load_inject_mode,
    _summarize_entity,
    _world_state_excerpts,
)
from .ledger import (
    _DEBT_LEDGER_CAP,
    _DEBT_OPEN_STATUSES,
    _FORESHADOW_OVERDUE_CHAPTERS,
    _HOOK_LEDGER_CAP,
    _HOOK_OPEN_STATUSES,
    _OPEN_HOOKS_CAP,
    _PLANTED_HOOK_STATUSES,
    _UNRESOLVED_BRANCHES_CAP,
    _collect_touched_entity_ids,
    _hook_ledger_excerpt,
    _narrative_debt_excerpt,
    _open_foreshadow_list,
    _plot_graph_excerpt,
    _project_overdue_chapters,
)
from .summaries import (
    _AUTHOR_STYLE_SAMPLES_INSTRUCTION,
    _DIRECTOR_SUMMARY_TOKEN_BUDGET,
    _RECENT_SUMMARY_CAP,
    _RECENT_SUMMARY_PER_CHARS,
    _STYLE_SAMPLE_PER_CHARS,
    _STYLE_SAMPLES_CAP,
    _author_style_samples,
    _director_plan_summary,
    _inject_scene_word_budget,
    _previous_chapter_tail,
    _recent_chapter_summaries,
    _recent_prose_tail,
    _truncate_summaries_to_token_budget,
)

__all__ = [
    "_ASSEMBLY_TOKEN_BUDGET",
    "_AUTHOR_STYLE_SAMPLES_INSTRUCTION",
    "_DEBT_LEDGER_CAP",
    "_DEBT_OPEN_STATUSES",
    "_DEFAULT_STYLE_CONSTRAINTS",
    "_DEFAULT_TARGET_WORD_COUNT",
    "_DIRECTOR_SUMMARY_TOKEN_BUDGET",
    "_FORESHADOW_OVERDUE_CHAPTERS",
    "_GENRE_PACK_BEAT_CAP",
    "_GENRE_PACK_CONSUMER_DIRECTOR",
    "_GENRE_PACK_CONSUMER_PLANNER",
    "_GENRE_PACK_CONSUMER_WRITER",
    "_GENRE_PACK_FIELD_CHARS",
    "_GENRE_PACK_PACING_MAX_CHARS",
    "_GENRE_PACK_PAYOFF_CAP",
    "_GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS",
    "_GENRE_PACK_STYLE_MAX_CHARS",
    "_HOOK_LEDGER_CAP",
    "_HOOK_OPEN_STATUSES",
    "_INJECT_MODES",
    "_MIN_ALIAS_LEN",
    "_OPEN_HOOKS_CAP",
    "_PEEK_CHAPTER_FALLBACK_SQL",
    "_PEEK_CHAPTER_SQL",
    "_PLANTED_HOOK_STATUSES",
    "_RECENT_SUMMARY_CAP",
    "_RECENT_SUMMARY_PER_CHARS",
    "_STYLE_SAMPLES_CAP",
    "_STYLE_SAMPLE_PER_CHARS",
    "_SUMMARY_LINE_MAX_CHARS",
    "_TOKEN_DIVISOR",
    "_UNRESOLVED_BRANCHES_CAP",
    "_apply_injection_policy",
    "_as_int_or",
    "_attach_assembly_meta",
    "_author_style_samples",
    "_build_trigger_corpus",
    "_chapter_project_id",
    "_character_state_excerpts",
    "_collect_touched_entity_ids",
    "_compose_density_text",
    "_director_plan_summary",
    "_empty_chapter_peek",
    "_estimate_tokens",
    "_extract_sensory_anchors",
    "_fingerprint_word_band_json",
    "_genre_pack_excerpt",
    "_hook_ledger_excerpt",
    "_inject_scene_word_budget",
    "_is_triggered",
    "_latest_draft",
    "_load_aliases",
    "_load_inject_mode",
    "_narrative_debt_excerpt",
    "_normalize_genre_pacing",
    "_open_foreshadow_list",
    "_parse_json",
    "_peek_active_canon_id",
    "_peek_chapter_context",
    "_peek_chapter_context_conn",
    "_peek_chapter_context_degraded",
    "_peek_chapter_no_state_version",
    "_peek_chapter_no_state_version_plan",
    "_peek_project_context",
    "_peek_project_id_from_chapter",
    "_peek_project_word_band_json",
    "_plot_graph_excerpt",
    "_previous_chapter_tail",
    "_project_max_state_version",
    "_project_overdue_chapters",
    "_ratio_instruction",
    "_recent_chapter_summaries",
    "_recent_prose_tail",
    "_row_to_chapter",
    "_row_to_project",
    "_safe_copy",
    "_summarize_entity",
    "_summarize_genre_structure_templates",
    "_textualize_genre_payoff_types",
    "_textualize_genre_planner_payoffs",
    "_trim_genre_pacing",
    "_trim_genre_style",
    "_truncate_summaries_to_token_budget",
    "_truncate_text",
    "_try_active_canon_id",
    "_try_project_genre_pack_ref",
    "_try_project_word_band_json",
    "_world_state_excerpts",
    "get_connection",
]
