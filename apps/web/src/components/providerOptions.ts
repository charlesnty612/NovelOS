// 协议清单的唯一权威（前端侧）。
//
// ⚠️ **本清单是「协议」，不是「厂商」**：厂商（opencode / DeepSeek / MiniMax…）≠ 协议
// （openai_compatible / anthropic / ollama / mock）。2026-10-24 裁决「协议下放到模型档案层」后，
// 协议是**模型级属性**（档案自持），连接是厂商网关（地址池 + key + headers）——
// 但连接**仍持一列「主协议」**（不是「连接已无协议」）：它是地址池的**缺省平面**。
// **模型协议由档案自持；绑定态按「档案的协议」查池**（不按连接主协议查）。
// 连接主协议只在「池里查不到档案的协议」时兜底：回退连接主 base_url，而主 base_url
// 服务的是**主协议**平面 ⇒ 主协议 ≠ 档案协议时打到的是另一个协议的端点
// ⇒ 后端 `resolve_effective` 打 `log.warning`「协议-地址错配」（前端不可见）。
//
// **单点的原因**：同一套协议词汇被前端复用在下拉里——① 档案编辑的「接口类型」
// （协议 = 模型级属性）；② 连接编辑的「各协议地址」行键（协议 → 该网关此协议的 base_url）；
// ③ **新建时的默认协议**（`PROVIDER_OPTIONS[0].value`）——`ConnectionsPanel.tsx`
// （新建连接的 provider 缺省）与 `AiSettingsPage.tsx`（新建档案的 provider 缺省）
// 都直接取**首项**。两处清单一旦分叉，就会出现「档案侧多出一项 ⇒ 运行期被兜底成 OpenAI 兼容」
// 「地址池侧多出一项 ⇒ 写时 422」这种前端自己制造的错误；两份副本里手挑改一处最容易犯的错，
// 故收敛到本文件。
// ⚠️ **③ 的连带风险**：首项**顺序**变更会**静默改掉**新建档案 / 新建连接的默认协议
// （无任何提示、既有数据不变）⇒ 调整本清单顺序前先确认第 ③ 处引用点仍指向预期协议。
//
// ⚠️ **档案的协议不必等于连接的主协议**：同一多协议网关下不同模型支持的协议不同
// （opencode Go：GLM/Kimi 走 OpenAI 兼容、Claude/MiniMax/Qwen 走 Anthropic）⇒ 绑定态地址按
// **档案的协议**从连接的协议地址池取（池里没有 ⇒ 回退连接主 base_url）。旧口径
// 「绑定档案时档案 provider 必须等于连接 provider，否则后端 422」**已随协议下放删除**。
//
// ⚠️ **与后端的关系**：后端协议词汇表 `PROTOCOL_IDS`
// （packages/core/model_router/effective.py）与 router 工厂四路 dispatch 一一对应，
// 连接的协议地址池键必须落在其中（写时 422）⇒ **后端新增协议时两端必须同步改**。
// 但档案 / 连接两列 `provider` 写时仍是**自由字符串**（后端不设枚举约束），厂商名也不在本清单里。

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

/** 协议的中文短名（列表行 / 徽标用；未登记者原样透出）。 */
export const PROVIDER_SHORT_LABEL: Record<string, string> = {
  openai_compatible: 'OpenAI 兼容',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
  mock: 'Mock',
};
