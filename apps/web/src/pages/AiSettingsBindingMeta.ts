// =============================================================================
// 环节绑定的元数据与文案（从 AiSettingsPage.tsx 拆出）
// -----------------------------------------------------------------------------
// 放这里的原因：环节分配面板文件（AiSettingsCapabilityBindings.tsx）已接近项目
//   铁律的 400 行上限；本文件只放「与 UI 无关的纯函数 + 标签表」，好让两边各自
//   保持单一职责。
//
// 契约 D 的实现（409 profile_in_use 提示）：
//   后端 DELETE /model-profiles/{id} 在档案仍被任何环节的候选链引用时返回 409，
//   detail 形态是 {error, message, referenced_by: string[]}。后端语义是对的
//   （不做静默解绑），前端要补的是「把真实状态讲成人话 + 给出真做得到的动作」。
// =============================================================================

/** capability → 中文标签兜底（后端 GET 一般会给 label，这里只作兜底）。 */
export const BINDING_LABELS_FALLBACK: Record<string, string> = {
  premise_design: '题材定位',
  world_building: '世界观',
  character_design: '角色设计',
  volume_outline: '卷纲',
  creative_writing: '正文写作',
  reasoning: '推理规划',
  // V3.9.3：observer 拆为独立 capability；前端 AI 设置页能单独给 observer 分配模型。
  observer: '状态提取',
  light: '轻量评审',
};

/** capability → 中文标签（先看后端给的 label，再退到静态兜底表）。 */
export function capabilityLabel(capability: string): string {
  return BINDING_LABELS_FALLBACK[capability] || capability;
}

/**
 * 409 profile_in_use 的 detail 归一化 → 人话 + 可执行动作（契约 D）。
 *
 * 后端 wire 形态：detail 是 `{error, message, referenced_by: string[]}` 对象；但
 * api/client.ts 的 parseError 对非数组 detail 走 JSON.stringify ⇒ 前端拿到的通常
 * **是对象的 JSON 字符串**。两种形态都要认。
 *
 * 硬约束：不许把原始 JSON 串甩进提示框；不许出现 `[object Object]`；指引必须指向
 * 用户**真做得到**的动作（去「环节分配」把这些环节上的该档案移除）。
 *
 * @param detail  后端 detail（对象或字符串）
 * @param labelOf capability → 中文标签的解析器（优先用后端 GET 给的 label）
 */
export function describeProfileInUse(
  detail: unknown,
  labelOf?: (capability: string) => string,
): string {
  const { message, referenced_by } = normalizeProfileInUseDetail(detail);
  const labels = referenced_by.map((cap) =>
    labelOf ? labelOf(cap) : capabilityLabel(cap),
  );
  const hint =
    labels.length > 0
      ? `请在「环节分配」中把该档案从以下环节移除：${labels.join('、')}`
      : '请在「环节分配」中把该档案从候选链中移除';
  if (message) return `${message}（${hint}）`;
  return `该档案仍被环节分配引用（${hint}）`;
}

/** 把 409 detail（对象 / JSON 字符串 / 纯字符串）归一化成 message + referenced_by。 */
function normalizeProfileInUseDetail(detail: unknown): {
  message: string | null;
  referenced_by: string[];
} {
  let value = detail;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '') return { message: null, referenced_by: [] };
    if (!trimmed.startsWith('{')) return { message: trimmed, referenced_by: [] };
    try {
      value = JSON.parse(trimmed) as unknown;
    } catch {
      return { message: trimmed, referenced_by: [] }; // 不是合法 JSON ⇒ 当普通文案用
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { message: null, referenced_by: [] };
  }
  const rec = value as { message?: unknown; referenced_by?: unknown };
  const message =
    typeof rec.message === 'string' && rec.message.trim() !== '' ? rec.message : null;
  const referenced_by = Array.isArray(rec.referenced_by)
    ? rec.referenced_by.filter(
        (x): x is string => typeof x === 'string' && x.trim() !== '',
      )
    : [];
  return { message, referenced_by };
}
