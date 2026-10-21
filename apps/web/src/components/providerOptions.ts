// provider（下拉项「接口类型」）的唯一权威清单。
//
// ⚠️ **单点的原因**（2026-10-21 批次十五「连接层」）：连接与档案两侧都有 provider
// 下拉，且**绑定档案时档案的 provider 必须等于连接的 provider**（后端 422）⇒
// 两处清单一旦分叉，就会出现「连接选了 A、档案选了 B，提交即被拒」这种
// 前端自己制造的错误。两份副本里手挑改一处是最容易犯的错，故收敛到本文件。
//
// ⚠️ 这不是「后端枚举的镜像」：后端 `provider` 是自由字符串（连接表不设枚举
// 约束），本清单是**前端给出的常用候选**。后端新增 provider 时前端照旧可以
// 手输/由连接侧带入，不需要改这里。

export interface ProviderOption {
  value: string;
  label: string;
}

export const PROVIDER_OPTIONS: ProviderOption[] = [
  { value: 'openai_compatible', label: 'OpenAI 兼容（OpenAI / DeepSeek / Ollama 等）' },
  { value: 'anthropic', label: 'Anthropic（Claude 原生 Messages API）' },
  { value: 'ollama', label: 'Ollama（本地 /api/chat，无需密钥）' },
  { value: 'mock', label: 'Mock（本地兜底）' },
];

/** provider 的中文短名（列表行 / 徽标用；未登记者原样透出）。 */
export const PROVIDER_SHORT_LABEL: Record<string, string> = {
  openai_compatible: 'OpenAI 兼容',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
  mock: 'Mock',
};
