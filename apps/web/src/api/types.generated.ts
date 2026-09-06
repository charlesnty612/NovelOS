/* eslint-disable */
/**
 * 由 scripts/gen_frontend_types.py 自动生成——请勿手工编辑。
 * 数据源：apps/web/openapi.json（scripts/export_openapi.py 导出）。
 * 用途：后端 Pydantic schema 的机器可读对照面（契约检查 / diff 审查）；
 * 运行时类型仍以手写 api/types.ts 为权威。
 */

export interface AdoptRequest {
  /** 采纳请求体：纯文本追加到最新草稿尾部。 */
  content: string;
}

export interface AdoptResponse {
  /** 采纳响应。 */
  version: number;
  /** 采纳响应。 */
  status: string;
  /** 采纳响应。 */
  appended_chars: number;
}

export interface Body_start_deconstruct_upload_api_projects__project_id__deconstruct_upload_post {
  /** 参照书文件（.txt / .epub） */
  file: string;
  /** 书名；留空则从 epub dc:title 兜底 */
  book_title?: string;
  /** 读者档 */
  reader_profile?: string;
}

export interface Chapter {
  /** 章节完整表示，对应数据库行。 */
  chapter_id: string;
  /** 章节完整表示，对应数据库行。 */
  project_id: string;
  /** 章节完整表示，对应数据库行。 */
  number: number;
  /** 章节完整表示，对应数据库行。 */
  title: string | null;
  /** 章节完整表示，对应数据库行。 */
  plan_json: Record<string, unknown>;
  /** 章节完整表示，对应数据库行。 */
  status: "PLANNED" | "DRAFTED" | "REVIEWED" | "COMMITTED" | "RELEASED";
  /** 章节完整表示，对应数据库行。 */
  visibility: string;
  /** 章节完整表示，对应数据库行。 */
  who_knows: Array<string> | null;
  /** 章节完整表示，对应数据库行。 */
  created_at: string;
  /** 章节完整表示，对应数据库行。 */
  updated_at: string;
}

export interface ChapterCreate {
  /** 创建章节请求体。 */
  number: number;
  /** 创建章节请求体。 */
  title?: string | null;
  /** 创建章节请求体。 */
  plan_json?: Record<string, unknown> | null;
}

export interface ChapterUpdate {
  /** 部分更新请求体——所有字段均可选。 */
  title?: string | null;
  /** 部分更新请求体——所有字段均可选。 */
  plan_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  status?: "PLANNED" | "DRAFTED" | "REVIEWED" | "COMMITTED" | "RELEASED" | null;
}

export interface Character {
  /** 角色完整表示。 */
  character_id: string;
  /** 角色完整表示。 */
  project_id: string;
  /** 角色完整表示。 */
  name: string;
  /** 角色完整表示。 */
  role: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other";
  /** 角色完整表示。 */
  core_json: Record<string, unknown>;
  /** 角色完整表示。 */
  visibility: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN";
  /** 角色完整表示。 */
  who_knows: Array<string> | null;
  /** 角色完整表示。 */
  created_at: string;
  /** 角色完整表示。 */
  updated_at: string;
  /** 角色完整表示。 */
  latest_state_version: number;
  /** 角色完整表示。 */
  latest_state_json: Record<string, unknown>;
  /** 角色完整表示。 */
  aliases?: Array<string>;
  /** 角色完整表示。 */
  inject_mode?: "auto" | "always" | "never";
}

export interface CharacterCreate {
  /** 创建角色请求体。 */
  name: string;
  /** 创建角色请求体。 */
  role?: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other" | null;
  /** 创建角色请求体。 */
  core_json?: Record<string, unknown> | null;
  /** 创建角色请求体。 */
  visibility?: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN" | null;
  /** 创建角色请求体。 */
  who_knows?: Array<string> | null;
  /** 创建角色请求体。 */
  aliases?: Array<string> | null;
  /** 创建角色请求体。 */
  inject_mode?: "auto" | "always" | "never" | null;
}

export interface CharacterState {
  /** 角色状态快照行（character_states 表）。 */
  character_id: string;
  /** 角色状态快照行（character_states 表）。 */
  state_version: number;
  /** 角色状态快照行（character_states 表）。 */
  state_json: Record<string, unknown>;
  /** 角色状态快照行（character_states 表）。 */
  visibility: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN";
  /** 角色状态快照行（character_states 表）。 */
  who_knows: Array<string> | null;
  /** 角色状态快照行（character_states 表）。 */
  created_at: string;
}

export interface CharacterUpdate {
  /** 部分更新请求体——所有字段均可选。 */
  name?: string | null;
  /** 部分更新请求体——所有字段均可选。 */
  role?: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other" | null;
  /** 部分更新请求体——所有字段均可选。 */
  core_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  visibility?: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN" | null;
  /** 部分更新请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
  /** 部分更新请求体——所有字段均可选。 */
  aliases?: Array<string> | null;
  /** 部分更新请求体——所有字段均可选。 */
  inject_mode?: "auto" | "always" | "never" | null;
}

export interface ContinueRequest {
  /** 续写请求体。 */
  num_variants?: number;
  /** 续写请求体。 */
  instruction?: string;
}

export interface ContinueResponse {
  /** 续写响应。 */
  variants: Array<ContinueVariant>;
  /** 续写响应。 */
  model: string;
  /** 续写响应。 */
  total_elapsed_ms: number;
}

export interface ContinueVariant {
  /** 单个候选 variant 的响应字段。 */
  index: number;
  /** 单个候选 variant 的响应字段。 */
  text: string | null;
  /** 单个候选 variant 的响应字段。 */
  tokens: number | null;
  /** 单个候选 variant 的响应字段。 */
  elapsed_ms: number;
  /** 单个候选 variant 的响应字段。 */
  error?: string | null;
}

export interface DebtCreate {
  /** 创建 Debt 请求体。 */
  description: string;
  /** 创建 Debt 请求体。 */
  created_chapter_id?: string | null;
  /** 创建 Debt 请求体。 */
  deadline_chapter_id?: string | null;
  /** 创建 Debt 请求体。 */
  status?: "open" | "acknowledged" | "paid" | "forgiven" | null;
  /** 创建 Debt 请求体。 */
  severity?: number;
  /** 创建 Debt 请求体。 */
  visibility?: string | null;
  /** 创建 Debt 请求体。 */
  who_knows?: Array<string> | null;
}

export interface DebtUpdate {
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  description?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  created_chapter_id?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  deadline_chapter_id?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  status?: "open" | "acknowledged" | "paid" | "forgiven" | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  severity?: number | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  visibility?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
}

export interface Draft {
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  draft_id: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  chapter_id: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  version: number;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  content: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  created_by: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  prompt_version: string | null;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  model_id: string | null;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  created_at: string;
}

export interface DraftCreate {
  /** 创建草稿请求体（人工改稿入口）。 */
  content: string;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HookCreate {
  /** 创建 Hook 请求体。 */
  name: string;
  /** 创建 Hook 请求体。 */
  introduced_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  expected_payoff_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  payoff_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  status?: "OPEN" | "ACTIVE" | "ESCALATED" | "RESOLVED" | "ABANDONED" | null;
  /** 创建 Hook 请求体。 */
  importance?: number;
  /** 创建 Hook 请求体。 */
  visibility?: string | null;
  /** 创建 Hook 请求体。 */
  who_knows?: Array<string> | null;
}

export interface HookUpdate {
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  name?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  introduced_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  expected_payoff_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  payoff_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  status?: "OPEN" | "ACTIVE" | "ESCALATED" | "RESOLVED" | "ABANDONED" | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  importance?: number | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  visibility?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
}

export interface Project {
  /** 项目完整表示，对应数据库行。 */
  project_id: string;
  /** 项目完整表示，对应数据库行。 */
  name: string;
  /** 项目完整表示，对应数据库行。 */
  premise: string | null;
  /** 项目完整表示，对应数据库行。 */
  genre: string | null;
  /** 项目完整表示，对应数据库行。 */
  target_words: number | null;
  /** 项目完整表示，对应数据库行。 */
  status: "ACTIVE" | "PAUSED" | "ARCHIVED";
  /** 项目完整表示，对应数据库行。 */
  created_at: string;
  /** 项目完整表示，对应数据库行。 */
  updated_at: string;
  /** 项目完整表示，对应数据库行。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor）；None = 项目无覆盖，消费点走模块默认 0.85 / 1.15 / 1200。 */
  word_band?: Record<string, unknown> | null;
}

export interface ProjectCreate {
  /** 创建项目请求体。 */
  name: string;
  /** 创建项目请求体。 */
  premise?: string | null;
  /** 创建项目请求体。 */
  genre?: string | null;
  /** 创建项目请求体。 */
  target_words?: number | null;
  /** 伏笔 overdue 阈值（章节数）；省略时使用 DDL 默认 30。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；省略/None=无覆盖。校验在 router 层走 resolve_band_config。 */
  word_band?: Record<string, unknown> | null;
}

export interface ProjectInitRequest {
  /** project-init 触发请求体。 */
  brief: Record<string, unknown>;
  /** project-init 触发请求体。 */
  project_id?: string | null;
  /** project-init 触发请求体。 */
  chapter_seed_count?: number | null;
  /** project-init 触发请求体。 */
  mock_providers?: Record<string, unknown> | null;
  /** project-init 触发请求体。 */
  step_mode?: boolean | null;
  /** project-init 触发请求体。 */
  selected_stages?: Array<string> | null;
  /** project-init 触发请求体。 */
  model_profile_id?: string | null;
}

export interface ProjectUpdate {
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  name?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  premise?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  genre?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  target_words?: number | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  status?: "ACTIVE" | "PAUSED" | "ARCHIVED" | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；None=清除覆盖；省略=保留原值。校验在 router 层走 resolve_band_config。 */
  word_band?: Record<string, unknown> | null;
}

export interface ResumeRequest {
  human_input?: Record<string, unknown> | null;
  auto_revise_max?: number | null;
  mock_providers?: Record<string, unknown> | null;
  regenerate?: boolean | null;
  model_overrides?: Record<string, unknown> | null;
}

export interface RevisionNoteUpdate {
  /** 写入或清除改稿意见的请求体。 */
  note: string;
}

export interface StartWorkflowRequest {
  author_intent?: string | null;
  expected_role?: string | null;
  target_word_count?: number | null;
  mock_providers?: Record<string, unknown> | null;
  quality_gate_mode?: string | null;
  critic_mode?: string | null;
  model_overrides?: Record<string, unknown> | null;
  fresh_write?: boolean | null;
  draft_version?: number | null;
  deep_review?: boolean | null;
}

export interface StyleSampleCreate {
  /** POST 请求体。 */
  title: string;
  /** POST 请求体。 */
  content: string;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface Volume {
  /** 卷完整表示，对应数据库行。 */
  volume_id: string;
  /** 卷完整表示，对应数据库行。 */
  project_id: string;
  /** 卷完整表示，对应数据库行。 */
  number: number;
  /** 卷完整表示，对应数据库行。 */
  title: string | null;
  /** 卷完整表示，对应数据库行。 */
  status: "active" | "sealed";
  /** 卷完整表示，对应数据库行。 */
  terminal_snapshot_json: Record<string, unknown> | null;
  /** 卷完整表示，对应数据库行。 */
  created_at: string;
  /** 卷完整表示，对应数据库行。 */
  updated_at: string;
}

export interface VolumeAssignRequest {
  /** 挂章请求体。 */
  chapter_id: string;
}

export interface VolumeCreate {
  /** 创建卷请求体。 */
  number: number;
  /** 创建卷请求体。 */
  title?: string | null;
}

export interface VolumeListItem {
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  volume_id: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  project_id: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  number: number;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  title: string | null;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  status: "active" | "sealed";
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  terminal_snapshot_json: Record<string, unknown> | null;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  chapter_count: number;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  created_at: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  updated_at: string;
}

export interface VolumeUpdate {
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  title?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  status?: "active" | "sealed" | null;
}
