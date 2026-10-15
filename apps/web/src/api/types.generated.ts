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

export interface ArcAlert {
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  level: "warn" | "fail";
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  code: string;
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  message: string;
}

export interface ArcChapterRow {
  /** 弧光视图里的一行章节。 */
  number: number;
  /** 弧光视图里的一行章节。 */
  title: string;
  /** 由 plan_json.key_beats[].purpose 里的 [payoff] 标记推断，非作者声明的结构 */
  has_payoff_beat: boolean;
  /** 由 plan_json.key_beats[].purpose 里的 [charge] 标记推断 */
  charge_beat: boolean;
  /** 弧光视图里的一行章节。 */
  overall: number | null;
  /** 弧光视图里的一行章节。 */
  pacing: number | null;
  /** 最新草稿的去空白字符数（visible_chars 口径）；无草稿为 null */
  prose_chars: number | null;
  /** 弧光视图里的一行章节。 */
  volume_id: string | null;
  /** 弧光视图里的一行章节。 */
  volume_number: number | null;
}

export interface ArcDebtCounts {
  open: number;
  paid: number;
}

export interface ArcHookCounts {
  open: number;
  resolved: number;
  /** 计划回收章号 < 当前最大章号的伏笔数 */
  overdue: number;
}

export interface ArcOverdueReveal {
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  policy_id: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  target_kind: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  target_id: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  reveal_by_chapter: number | null;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  audience: string;
}

export interface ArcPayoff {
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  total: number;
  /** 尾部连续蓄力（charge）章数 */
  charge_streak: number;
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  max_charge_streak: number;
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  last_payoff_chapter: number | null;
}

export interface ArcRevealCounts {
  planned: number;
  revealed: number;
  cancelled: number;
  overdue: Array<ArcOverdueReveal>;
}

export interface ArcView {
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  project_id: string;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  generated_at: string;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  chapters: Array<ArcChapterRow>;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  payoff: ArcPayoff;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  hooks: ArcHookCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  debts: ArcDebtCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  reveal_policies: ArcRevealCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  volumes: Array<ArcVolumeRow>;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  alerts: Array<ArcAlert>;
}

export interface ArcVolumeRow {
  volume_id: string;
  number: number;
  title: string | null;
  /** 卷状态；后端列是 TEXT，未知值原样透出 */
  status: string;
  chapter_count: number;
}

export interface Body_start_deconstruct_upload_api_projects__project_id__deconstruct_upload_post {
  /** 参照书文件（.txt / .epub） */
  file: string;
  /** 书名；留空则从 epub dc:title 兜底 */
  book_title?: string;
  /** 读者档 */
  reader_profile?: string;
}

export interface ChangedItem {
  /** ``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``。 */
  id: string;
  /** ``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``。 */
  changes?: Record<string, unknown> | null;
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
  outline_json?: Record<string, unknown> | null;
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

export interface ChapterReopenRequest {
  /** 重开返修请求体（全部可选，可省略整个 body）。 */
  reason?: string | null;
}

export interface ChapterUpdate {
  /** 部分更新请求体——所有字段均可选。 */
  title?: string | null;
  /** 部分更新请求体——所有字段均可选。 */
  plan_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  outline_json?: Record<string, unknown> | null;
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

export interface DeliveryChapter {
  /** 一章的交付判定行。 */
  chapter_id: string;
  /** 一章的交付判定行。 */
  number: number;
  /** 一章的交付判定行。 */
  title: string;
  /** 章节状态（流程位置，非交付判定） */
  status: string;
  /** 一章的交付判定行。 */
  verdict: "deliverable" | "needs_work" | "not_deliverable" | "not_a_candidate";
  /** 首个降级理由的中文说明；可交付时为 null */
  blocking_reason?: string | null;
  /** 一章的交付判定行。 */
  draft_version?: number | null;
  /** 一章的交付判定行。 */
  length?: DeliveryLength | null;
  /** 一章的交付判定行。 */
  quality?: DeliveryQuality | null;
  /** 一章的交付判定行。 */
  signing: DeliverySigning;
  /** 一章的交付判定行。 */
  reasons?: Array<DeliveryReason>;
}

export interface DeliveryEvidenceSource {
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  key: string;
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  reachable: boolean;
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  detail: string;
}

export interface DeliveryIssue {
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  rule_id: string;
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  severity: string;
  /** 后果档 auto / confirm / block */
  gate: string;
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  message: string;
}

export interface DeliveryLength {
  /** 字数测量（``quality.wordcount`` 口径）。 */
  visible_chars: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  target_word_count: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  band_low: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  band_high: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  deviation_pct: number;
  /** in_band / under / over */
  status: string;
  /** in_band / warn / error（两级阈值同 review 侧） */
  tier: string;
}

export interface DeliveryQuality {
  /** 该章最新质量报告的证据面。 */
  report_id: string;
  /** 该章最新质量报告的证据面。 */
  overall: number;
  /** 该章最新质量报告的证据面。 */
  evaluated_at?: string | null;
  /** 本报告评估的草稿版本 */
  draft_version?: number | null;
  /** 报告 _meta.gate_summary（block / confirm 聚合摘要） */
  gate_summary?: Record<string, unknown> | null;
  /** 报告 _meta.gate_accepted_override（confirm 档接受留痕） */
  accepted_override?: Record<string, unknown> | null;
  /** 该章最新质量报告的证据面。 */
  issues?: Array<DeliveryIssue>;
}

export interface DeliveryReason {
  /** 规则 / 证据来源标识（前端按此分支） */
  rule_id: string;
  /** 证据出处 */
  source: "quality_issue" | "length" | "signing_check" | "chapter_status" | "evidence";
  /** 该证据自身的严重度（error / warning / info） */
  severity: string;
  /** 后果档（决定判定是否降级） */
  status: "block" | "confirm" | "accepted" | "warn" | "length_out_of_band" | "length_beyond_band_edge" | "signing_fail" | "not_committed" | "not_evaluated" | "out_of_scope" | "not_a_candidate" | "project_not_deliverable" | "project_needs_work";
  /** 面向作者的中文说明 */
  message: string;
  /** 证据摘录（引文片段或数字） */
  evidence?: string | null;
}

export interface DeliveryRollUp {
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  chapter_count: number;
  /** 非 PLANNED 的章节数 */
  written_chapter_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  deliverable_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  needs_work_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_deliverable_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_a_candidate_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  deliverable_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  needs_work_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_deliverable_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_a_candidate_chapter_ids: Array<string>;
  /** 每章 id → 首个降级理由（可交付章不出现） */
  blocking_reason_by_chapter?: Record<string, unknown>;
}

export interface DeliverySigning {
  /** in_scope / out_of_scope / unavailable */
  scope: string;
  /** 签约体检在本章的适用面。 */
  note?: string;
  /** 签约体检在本章的适用面。 */
  items?: Array<DeliverySigningItem>;
}

export interface DeliverySigningItem {
  /** 签约体检的单项（仅第 1~3 章）。 */
  key: string;
  /** pass / warn / fail / info */
  level: string;
  /** 签约体检的单项（仅第 1~3 章）。 */
  detail: string;
  /** 签约体检的单项（仅第 1~3 章）。 */
  advice?: string;
}

export interface DeliveryVerdictResponse {
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_id: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_name: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  generated_at: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_verdict: "deliverable" | "needs_work" | "not_deliverable" | "not_a_candidate";
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_reasons?: Array<DeliveryReason>;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  roll_up: DeliveryRollUp;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  evidence_sources?: Array<DeliveryEvidenceSource>;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  signing_check_scope?: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  chapters?: Array<DeliveryChapter>;
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

export interface GateReviseRequest {
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  mock_providers?: Record<string, unknown> | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  model_overrides?: Record<string, unknown> | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  critic_mode?: string | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  deep_review?: boolean | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  target_word_count?: number | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  author_intent?: string | null;
}

export interface GenreBindRequest {
  /** 绑定请求体：``{"pack_id": "..."}``。 */
  pack_id: string;
}

export interface GenrePack {
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  pack_id: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  name: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  genre_tag: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  version: number;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  payload: Record<string, unknown>;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  source_path: string | null;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  created_at: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  updated_at: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  bound_project_count?: number;
}

export interface GenrePackCreate {
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  name: string;
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  genre_tag: string;
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  payload?: Record<string, unknown>;
  /** 显式 pack_id（内容仓 slug）；省略则生成 gp_<12hex>。 */
  pack_id?: string | null;
  /** 内容仓来源路径（审计用；软件层不解析）。 */
  source_path?: string | null;
}

export interface GenrePackSummary {
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  pack_id: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  name: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  genre_tag: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  version: number;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  source_path?: string | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  created_at: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  updated_at: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  payoff_type_count?: number;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  structure_model?: string | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  chapter_words_target?: number | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  bound_project_count?: number;
}

export interface GenrePackUpdate {
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  name?: string | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  genre_tag?: string | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  payload?: Record<string, unknown> | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  source_path?: string | null;
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

export interface ListDiff {
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  added?: Array<Record<string, unknown>> | null;
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  removed?: Array<Record<string, unknown>> | null;
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  changed?: Array<ChangedItem> | null;
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
  /** 绑定的题材包 id（题材库单 slot）；None = 未绑定 / 极老库缺列。绑定与解绑走 POST /projects/{pid}/genre-pack/bind|unbind。 */
  genre_pack_id?: string | null;
  /** 项目写作圣经（迁移 0029 的可空列）；None = 无圣经 / 极老库缺列。写入走 POST /projects 与 PATCH /projects/{pid}；不被任何工作流覆盖。 */
  writing_bible?: string | null;
  /** 快通道开关（迁移 0031 的可空三态列）；None = 未设置 / 极老库缺列 → 解析时回落 env 与默认值（关）。读侧布尔化（NULL→False）。 */
  fast_mode?: boolean | null;
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
  /** 项目写作圣经：跨章节长期有效的作者硬性约束（题材铁律 / 禁写项 / 称谓与视角约定等）。省略/None=无圣经；空白串按无圣经处理（落 NULL）。装配时与运行期 author_intent 按「基线 + 增量」拼接，见模型模块 docstring。 */
  writing_bible?: string | null;
  /** 快通道：写完直接提交，跳过人工审批门（迁移 0031 的可空三态列）。True=开闸门旁路、False=显式关、省略/None=未设置（走默认关）。只影响闸门是否阻断/挂起，**不影响** observer 等状态更新。 */
  fast_mode?: boolean | null;
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
  /** 项目写作圣经。显式 null / 空白串 → 清除（落 NULL）；省略 → 保留原值。装配优先级见模型模块 docstring（基线 + 增量拼接）。 */
  writing_bible?: string | null;
  /** 快通道开关。true=开 / false=显式关 / 显式 null=清除覆盖（落 NULL，回到「未设置」）；省略=保留原值。 */
  fast_mode?: boolean | null;
}

export interface QualityJudgeRecord {
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  pacing: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  style: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  logic: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  dialogue: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  verdict?: string | null;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  top_issues?: Array<string> | null;
}

export interface QualityJudgeResponse {
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  report_id: string;
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  chapter_id: string;
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  judge: QualityJudgeRecord | null;
}

export interface ResumeRequest {
  human_input?: Record<string, unknown> | null;
  auto_revise_max?: number | null;
  mock_providers?: Record<string, unknown> | null;
  regenerate?: boolean | null;
  model_overrides?: Record<string, unknown> | null;
  author_intent?: string | null;
  target_word_count?: number | null;
}

export interface RevealPolicyCreateIn {
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  target_kind: "character" | "location" | "faction" | "world_rule" | "event" | "hook" | "debt" | "relationship";
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  target_id: string;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  reveal_by_chapter?: number | null;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  audience?: string;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  status?: "planned" | "revealed" | "cancelled";
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  revealed_chapter?: number | null;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  notes?: string | null;
}

export interface RevealPolicyOut {
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  policy_id: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  project_id: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  target_kind: "character" | "location" | "faction" | "world_rule" | "event" | "hook" | "debt" | "relationship";
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  target_id: string;
  /** 计划在第几章前揭开；null = 未定 */
  reveal_by_chapter: number | null;
  /** `reader` / `character:<id>` / 逗号混写；DDL default 为 'reader' */
  audience: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  status: "planned" | "revealed" | "cancelled";
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  revealed_chapter: number | null;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  notes: string | null;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  created_at: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  updated_at: string;
}

export interface RevealPolicyUpdateIn {
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  status?: "planned" | "revealed" | "cancelled" | null;
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  revealed_chapter?: number | null;
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  notes?: string | null;
}

export interface RevisionNoteUpdate {
  /** 写入或清除改稿意见的请求体。 */
  note: string;
}

export interface SimulationIssue {
  /** 被拒的 delta（``issues[index]``）。 */
  index: number;
  /** 被拒的 delta（``issues[index]``）。 */
  errors: Array<string>;
}

export interface SimulationResult {
  /** 与 branch_id 同值 */
  simulation_id: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  branch_id: string;
  /** ⚠️ **必须以 `sim-` 开头**，否则该次推演在 `GET /simulations` 与 `GET /simulations/{id}` 里**查不到**（两者都按 `name LIKE 'sim-%'` 过滤，实测自定义名会 404） */
  name: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  base_version: number;
  /** 已成功应用的 delta 条数；失败时是部分应用数 */
  applied: number;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  diff: StateDiff;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  issues: Array<SimulationIssue>;
  /** 恒为 ARCHIVED（推演结束即归档） */
  branch_status: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  created_at: string;
}

export interface SimulationSummary {
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  simulation_id: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  branch_id: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  name: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  base_version: number;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  status: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  created_at: string;
}

export interface SnapshotResponse {
  /** 快照版本号；无快照时后端返回 0（genesis 未初始化前的形态） */
  state_version: number;
  /** 角色列表（按 character_id ASC） */
  characters?: Array<Record<string, unknown>>;
  /** 世界观：{locations: dict, factions: dict, world_rules: list} */
  world?: Record<string, unknown>;
  /** 伏笔列表（按 hook_id ASC） */
  hooks?: Array<Record<string, unknown>>;
  /** 叙事债务列表（按 debt_id ASC） */
  debts?: Array<Record<string, unknown>>;
  /** 最近事件（时间线视角） */
  recent_events?: Array<unknown>;
  /** 剧情事件，**按 event_id 键的字典**（不是列表） */
  events?: Record<string, unknown>;
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
  gate_override?: Record<string, unknown> | null;
  fast_mode?: boolean | null;
  auto_commit?: boolean | null;
}

export interface StateDiff {
  /** 两个快照的结构化差异。 */
  version_a: number;
  /** 两个快照的结构化差异。 */
  version_b: number;
  /** 两个快照的结构化差异。 */
  branch_id?: string | null;
  /** 两个快照的结构化差异。 */
  characters?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  world?: Record<string, unknown> | null;
  /** 两个快照的结构化差异。 */
  hooks?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  debts?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  events_changed?: Record<string, unknown> | null;
  /** 两个快照的结构化差异。 */
  recent_events?: ListDiff | null;
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
  arc_summary: string | null;
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
  /** 创建卷请求体。 */
  arc_summary?: string | null;
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
  arc_summary: string | null;
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
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  arc_summary?: string | null;
}
