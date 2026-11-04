// 从 workflow run 的 checkpoint_json 中提取 Human 节点暂停时写入的 __pause_payload__。
//
// 背景（详见 packages/core/workflow_runtime/engine.py:307）：
//   Human 节点暂停时，后端把暂停载荷写在 checkpoint_json 的「节点键」下：
//     checkpoint_json["author_review"]["__pause_payload__"] = {stage, message, ...}
//   checkpoint_json 顶层不会有 __pause_payload__（早期兼容可能存在）。
//
// C-44（2026-11-05）：**优先级必须与服务端一致**。
// 服务端 `routers/workflows/common.py::_extract_pause_payload` 的口径是：
//   1. `run.current_node` 对应节点的 payload（若该节点存在且带 __pause_payload__）；
//   2. 否则按 checkpoint 的插入序取**第一个**带 payload 的节点。
// 前端此前只做了「顶层 → 插入序」两级，且没有把 current_node 传进来 ⇒ 当一次 run
// 先后暂停过两个节点（前面的旧 payload 仍在 checkpoint 里）时，前端会拿到**旧节点**
// 的 stage / message，而服务端用的是当前节点 ⇒ 审批卡与决议动作对不上。
//
// 读取规则（本函数是唯一单点）：
//   1. `checkpointJson[currentNode]` 的 __pause_payload__（若给了 currentNode）；
//   2. 顶层兜底（服务端没有这一级，纯防御兼容；排在 current_node 之后，
//      避免它抢在权威来源前面）；
//   3. 遍历对象每个值，若值是对象且含 __pause_payload__ 键，则返回该值；
//   4. 都没有 → 返回 null。

export function extractPausePayload(
  checkpointJson: unknown,
  currentNode?: string | null,
): Record<string, unknown> | null {
  if (!checkpointJson || typeof checkpointJson !== 'object') return null;
  const obj = checkpointJson as Record<string, unknown>;

  // 1. 当前挂起节点优先（与服务端 `_extract_pause_payload` 的主路径逐字对齐）
  if (currentNode) {
    const nodeVal = obj[currentNode];
    if (nodeVal && typeof nodeVal === 'object') {
      const inner = (nodeVal as Record<string, unknown>)['__pause_payload__'];
      if (inner && typeof inner === 'object') {
        return inner as Record<string, unknown>;
      }
    }
  }

  // 2. 顶层兼容（防御旧数据；排在 current_node 之后）
  const top = obj['__pause_payload__'];
  if (top && typeof top === 'object') {
    return top as Record<string, unknown>;
  }

  // 3. 节点键下取值（服务端的降级路径：插入序第一个）
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
