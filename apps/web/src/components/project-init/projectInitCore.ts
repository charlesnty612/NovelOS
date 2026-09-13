// project-init 面板的共享核心：常量 / 类型 / 纯函数 / run 轮询（V4.0 前端模块化
// 批次从 ProjectInitPanel.tsx 原样搬出，行为零变化）。
import { workflowsApi } from '../../api/endpoints';
import type { ProjectInitResponse, WorkflowRun } from '../../api/types';

export const DEFAULT_PLATFORM = '番茄·男频';
export const DEFAULT_CHAPTER_SEED_COUNT = 10;
export const CHAPTER_SEED_COUNT_MIN = 1;
export const CHAPTER_SEED_COUNT_MAX = 500;
export const DEFAULT_CHAPTER_WORD_COUNT = 3000;
// 后端 resume 已异步化（POST /runs/{id}/resume 固定立即返回 status=RUNNING，
// 真实终态靠 GET /runs/{id} 轮询）。前端在 init / resume / regenerate 三个入口
// 都可能拿到 RUNNING，需要轮询到终态再走原有 PAUSED/COMPLETED 分支。
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 600000;
const POLL_TIMEOUT_MESSAGE =
  '生成仍在后台进行，已等待超时；请稍后刷新页面在运行记录中查看结果';

/**
 * 测试覆盖：单测中通过在 window 上挂 __novelosPollIntervalMs / __novelosPollTimeoutMs
 * 注入更短的间隔/超时（默认 1ms/1000ms），避免在 fake timers 或短超时下被卡住。
 * 仅在 NODE_ENV !== 'production' 时生效（避免被滥用为生产可调参数）。
 */
function getPollIntervalMs(): number {
  if (
    typeof process !== 'undefined' &&
    process.env.NODE_ENV !== 'production' &&
    typeof window !== 'undefined'
  ) {
    const v = (window as unknown as { __novelosPollIntervalMs?: number })
      .__novelosPollIntervalMs;
    if (typeof v === 'number' && v > 0) return v;
  }
  return POLL_INTERVAL_MS;
}
function getPollTimeoutMs(): number {
  if (
    typeof process !== 'undefined' &&
    process.env.NODE_ENV !== 'production' &&
    typeof window !== 'undefined'
  ) {
    const v = (window as unknown as { __novelosPollTimeoutMs?: number })
      .__novelosPollTimeoutMs;
    if (typeof v === 'number' && v > 0) return v;
  }
  return POLL_TIMEOUT_MS;
}

/**
 * 轮询结果 + 即时响应统一形态：包含 init 同步响应（ProjectInitResponse，无 stage_models）
 * 与 GET 轮询响应（WorkflowRun，含 stage_models）的并集。
 */
export type FinalRun =
  | ProjectInitResponse
  | (WorkflowRun & { project_id?: string | null });

/**
 * 轮询 GET /runs/{runId} 直到 run 进入「终态」。
 * 终态：PAUSED（带 pause_payload）/ COMPLETED / FAILED / CANCELLED。
 * 仍 RUNNING / PENDING 时继续等待；超过 timeoutMs 抛 Error。
 *
 * 用于后端 resume / init 已异步化的场景：HTTP 响应只回 RUNNING，
 * UI 必须轮询拿到真实终态才能进入 review / done。
 */
export async function pollRunUntilTerminal(
  runId: string,
  opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<WorkflowRun> {
  const intervalMs = opts.intervalMs ?? getPollIntervalMs();
  const timeoutMs = opts.timeoutMs ?? getPollTimeoutMs();
  const deadline = Date.now() + timeoutMs;
  while (true) {
    if (opts.signal?.aborted) {
      throw new Error('轮询已取消');
    }
    const cur = await workflowsApi.get(runId);
    if (opts.signal?.aborted) {
      throw new Error('轮询已取消');
    }
    const status = cur.status;
    if (status === 'PAUSED' && cur.pause_payload) return cur;
    if (status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED') {
      return cur;
    }
    // RUNNING / PENDING：再等一轮
    if (Date.now() >= deadline) {
      throw new Error(POLL_TIMEOUT_MESSAGE);
    }
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, intervalMs);
      if (opts.signal) {
        const onAbort = () => {
          clearTimeout(t);
          reject(new Error('轮询已取消'));
        };
        opts.signal.addEventListener('abort', onAbort, { once: true });
      }
    });
  }
}
// 后端 ProjectInitRequest.chapter_seed_count 校验为 Field(ge=1, le=500)；
// 推导出的 chapter_seed_count 落在 [10, 500]，与后端推导 clamp 一致（100 万字 ÷ 3000 ≈ 333 章可真实提交）。
const DERIVED_SEED_COUNT_MIN = 10;
const DERIVED_SEED_COUNT_MAX = 500;
export const DETAIL_TRUNCATE = 200;

// 挂起恢复：sessionStorage key 模板（按 projectId 区分；仅本会话生效）。
export const DISMISSED_SUSPENDED_KEY = (projectId: string) =>
  `novelos:project-init:dismissed:${projectId}`;
// 哪一类 run 算可恢复的（与后端 workflow.name 对齐）。
export const RESUMABLE_WORKFLOW = 'project-init';

// ---- 分步审阅向导配置 ------------------------------------------------------
// 4 关卡 → 中文名 / output_key（与后端 pause_payload.stage / revisions key 对齐）。
export const STAGE_LABELS: Array<{
  stage: string;
  outputKey: string;
  label: string;
  capability: string;
}> = [
  { stage: 'premise', outputKey: 'premise_output', label: '题材定位', capability: 'premise_design' },
  { stage: 'world', outputKey: 'world_output', label: '世界观', capability: 'world_building' },
  { stage: 'character', outputKey: 'character_output', label: '核心角色', capability: 'character_design' },
  { stage: 'outline', outputKey: 'outline_output', label: '卷纲与章节种子', capability: 'volume_outline' },
];

export const STAGES_TOTAL = STAGE_LABELS.length;

// init-status 三态：
// - 'loading'：尚未拉取
// - 'ok'：已拉到，按 stage 状态决定默认勾选
// - 'failed'：请求失败 → 默认全选（保守不跳过任何环节）
export type InitStatusLoadState = 'loading' | 'ok' | 'failed';

// stage 勾选维度（与 STAGE_LABELS.stage 对齐）：
// - stage：环节 id
// - forced：保留字段恒为 false（向后兼容旧测试断言；语义上所有环节都允许
//          自由勾选/取消，不再锁定未 done 环节）
// - done：来自 init-status（用于展示「已有设定 / 未生成」徽标）
// - label / detail：来自 init-status 的展示字段
export type StageSelection = {
  stage: string;
  forced: boolean;
  done: boolean;
  label: string;
  detail: string | null;
};

export type PanelState =
  | 'idle'
  | 'busy'
  | 'review'
  | 'busy-resume'
  | 'done';

export type PanelMode = 'idle-form' | 'review' | 'done';

/**
 * 解析表单中的 chapter_seed_count 字符串。
 * - 空 / NaN / < 1 / > 500 视为无效（返回 null）。
 * - 与后端 ProjectInitRequest.chapter_seed_count 的 Field(ge=1, le=500) 对齐
 *   （packages/core/api/routers/workflows.py），让前端校验在 Pydantic 422 之前先拦截。
 */
export function parseSeedCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(n)) return null;
  if (n < CHAPTER_SEED_COUNT_MIN || n > CHAPTER_SEED_COUNT_MAX) return null;
  return n;
}

/**
 * 由 (target_words, chapter_word_count) 推导章节种子数。
 * 公式与后端 _derive_chapter_seed_count 一致：round(target_words / chapter_word_count)，
 * clamp 到 [DERIVED_SEED_COUNT_MIN, DERIVED_SEED_COUNT_MAX]（前端版）。
 * - 任一输入为空/非正整数 → 返回 null（不重算，保持用户当前值）。
 * - 后端允许范围 [10, 500]（pipeline._clamp）；前端推导保持同一区间，
 *   与 chapter_seed_count 表单字段的可填范围 [1, 500] 一致，不会因推导值 422。
 */
export function deriveSeedCount(
  targetWordsRaw: string,
  chapterWordCountRaw: string,
): number | null {
  const tw = Number.parseInt(targetWordsRaw.trim(), 10);
  const cw = Number.parseInt(chapterWordCountRaw.trim(), 10);
  if (!Number.isFinite(tw) || tw <= 0) return null;
  if (!Number.isFinite(cw) || cw <= 0) return null;
  const derived = Math.round(tw / cw);
  if (derived < DERIVED_SEED_COUNT_MIN) return DERIVED_SEED_COUNT_MIN;
  if (derived > DERIVED_SEED_COUNT_MAX) return DERIVED_SEED_COUNT_MAX;
  return derived;
}

/**
 * 安全地把任意值规整成 Record<string, unknown>（用于编辑态初始值）。
 * 后端 draft 是超集；非对象 → {}。
 */
export function asRecord(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    return v as Record<string, unknown>;
  }
  return {};
}
