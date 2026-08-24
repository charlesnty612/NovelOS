// 从 workflow run 的 checkpoint_json 中提取 Human 节点暂停时写入的 __pause_payload__。
//
// 背景（详见 packages/core/workflow_runtime/engine.py:307）：
//   Human 节点暂停时，后端把暂停载荷写在 checkpoint_json 的「节点键」下：
//     checkpoint_json["author_review"]["__pause_payload__"] = {stage, message, ...}
//   checkpoint_json 顶层不会有 __pause_payload__（早期兼容可能存在）。
//
// 读取规则：
//   1. 顶层兜底：若 checkpointJson 是对象且自身有 __pause_payload__ 键（防御兼容），直接返回。
//   2. 节点键下取值：遍历对象每个值，若值是对象且含 __pause_payload__ 键，则返回该值。
//   3. 都没有 → 返回 null。
//
// 调用方（如 ChapterDetailPage）拿到 payload 后即可判断 stage / message / review_report 等字段。

export function extractPausePayload(
  checkpointJson: unknown,
): Record<string, unknown> | null {
  if (!checkpointJson || typeof checkpointJson !== 'object') return null;
  const obj = checkpointJson as Record<string, unknown>;

  // 1. 顶层兼容
  const top = obj['__pause_payload__'];
  if (top && typeof top === 'object') {
    return top as Record<string, unknown>;
  }

  // 2. 节点键下取值
  for (const v of Object.values(obj)) {
    if (v && typeof v === 'object') {
      const inner = (v as Record<string, unknown>)['__pause_payload__'];
      if (inner && typeof inner === 'object') {
        return inner as Record<string, unknown>;
      }
    }
  }
  return null;
}
