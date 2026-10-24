// =============================================================================
// 思考档共享词汇（从 AiSettingsPage.tsx 抽出，2026-10-24「按环节覆盖模型强度」）
// -----------------------------------------------------------------------------
// 为什么独立成文件：思考档的 8 档枚举、档位→params 映射、按端点收窄下拉的
// 规则，同时被两处消费——① 档案表单（AiSettingsPage 的 ModelProfileFormModal）；
// ② 环节分配面板（AiSettingsCapabilityBindings 的强度覆盖下拉）。抽到共享处
// 而不是复制第二份（映射规则前后端必须一致：后端单点是
// packages/core/model_router/effective.py::apply_thinking_override，
// 改任何一侧先 grep 另一侧）。
// =============================================================================

export type ThinkingMode =
  | 'default'
  | 'off'
  | 'on'
  | 'adaptive'
  | 'low'
  | 'medium'
  | 'high'
  // V3.8：Kimi 专属「max」档，写入时仍走 reasoning_effort（与 low/medium/high 同路）。
  | 'max';

export const THINKING_OPTIONS: { value: ThinkingMode; label: string; hint?: string }[] = [
  { value: 'default', label: '默认（跟随端点）' },
  { value: 'off', label: '关闭思考' },
  { value: 'on', label: '开启思考' },
  { value: 'adaptive', label: '自适应（MiniMax M 系列）', hint: 'MiniMax M 系专用' },
  { value: 'low', label: '开启·低档' },
  { value: 'medium', label: '开启·中档' },
  { value: 'high', label: '开启·高档' },
  { value: 'max', label: '开启·最高档（Kimi）', hint: 'Kimi 专属档位' },
];

/**
 * 抽取 host（小写）——兼容 ``https://api.openai.com/v1`` / ``http://127.0.0.1:11434`` 等。
 */
export function hostOf(baseUrl: string): string {
  if (!baseUrl) return '';
  const s = baseUrl.trim().toLowerCase();
  const m = s.match(/^[a-z][a-z0-9+\-.]*:\/\/([^/?#]+)/);
  if (m && m[1]) return m[1];
  // 兜底：非标准 url，截取首段
  const noProto = s.replace(/^[a-z][a-z0-9+\-.]*:\/\//, '');
  return noProto.split('/')[0] ?? '';
}

/**
 * 按 provider + base_url 收窄思考模式下拉（V3.8）。
 * 没有「协议层的端点能力查询接口」，因此我们自己维护端点 → 推荐档位映射。
 *
 * 返回的顺序就是下拉展示顺序；与 ``THINKING_OPTIONS`` 的相对顺序保持一致。
 */
export function getThinkingOptions(
  provider: string,
  baseUrl: string,
): { value: ThinkingMode; label: string; hint?: string }[] {
  const host = hostOf(baseUrl);
  const isProvider = (p: string) => provider === p;
  // Provider 自带（不依赖 host）—— Ollama / Anthropic / Mock 一律退回全量。
  if (isProvider('anthropic') || isProvider('ollama') || isProvider('mock')) {
    return THINKING_OPTIONS;
  }
  // MiniMax M 系：仅 default / off / adaptive。
  if (host.includes('minimax')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'adaptive'].includes(o.value),
    );
  }
  // DeepSeek：default / off / low / medium / high
  if (host.includes('deepseek')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'medium', 'high'].includes(o.value),
    );
  }
  // Kimi：default / off / low / high / max（不含 on/adaptive）。
  // host 需同时命中 kimi.com 与 Moonshot 官方域（api.moonshot.ai / api.moonshot.cn）。
  if (
    host.includes('kimi.com') ||
    host.includes('moonshot.ai') ||
    host.includes('moonshot.cn')
  ) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'high', 'max'].includes(o.value),
    );
  }
  // OpenAI：仅当 host 含 ``api.openai.com`` 或独立 ``provider==='openai'`` 时收窄。
  // ``provider==='openai_compatible'`` 不一定指向 OpenAI 官方（如用户指向私有端点），
  // 因此只有 host 命中时才走 OpenAI 档位；其余代理类端点一律退回全量。
  if (isProvider('openai') || host.includes('api.openai.com')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'medium', 'high'].includes(o.value),
    );
  }
  // 未知端点：全量
  return THINKING_OPTIONS;
}

/**
 * 从 params 推断「思考模式」下拉的初值：
 * - thinking.type === 'disabled' → 'off'
 * - thinking.type === 'enabled'  → 'on'
 * - thinking.type === 'adaptive' → 'adaptive'（MiniMax M 系列专用值）
 * - reasoning_effort ∈ {low,medium,high} → 对应档位
 * - 都没有 → 'default'（不显式写入 params）
 */
// 导出仅为测试：回归锁死 reasoning_effort=max 的回显（漏判会静默丢档）。
export function inferThinkingMode(params: Record<string, unknown>): ThinkingMode {
  const thinking = params['thinking'];
  if (thinking && typeof thinking === 'object') {
    const t = thinking as Record<string, unknown>;
    if (t['type'] === 'disabled') return 'off';
    if (t['type'] === 'enabled') return 'on';
    if (t['type'] === 'adaptive') return 'adaptive';
  }
  const effort = params['reasoning_effort'];
  if (
    effort === 'low' ||
    effort === 'medium' ||
    effort === 'high' ||
    effort === 'max'
  ) {
    return effort;
  }
  return 'default';
}

export const THINKING_BADGE_LABEL: Record<ThinkingMode, string> = {
  default: '默认',
  off: '关',
  on: '开',
  adaptive: '自适应',
  low: '低',
  medium: '中',
  high: '高',
  max: '最高',
};
