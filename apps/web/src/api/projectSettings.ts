// 项目「已解析有效值」的前端调用（2026-10-23 批次三十二 · Task 5 返工）。
//
// 为什么单独一个文件：按仓内约定，**所有端点都该集中在 `api/endpoints.ts`**。
// 但该文件不在本批（Task 5 返工）的文件所有权清单内 ⇒ 先把 URL 收在**这一处**
// （不散落到两个页面里），后续并回 `endpoints.ts` 只是一次移动 + 改两处 import。
//
// 端点语义：`GET /projects/{project_id}/effective-settings`
//   → `{ fast_mode_effective: boolean, fast_mode_source: 'project'|'env'|'off' }`
//   「有效值」= 后端权威解析结果（项目列 > env > 默认关）；
//   「来源」= 这个值是项目列定的、env 回落定的，还是默认关。
// ⚠️ 前端**不得**自己再实现一遍解析（env 词表在后端）——只消费后端给的权威结果。

import { api } from './client';
import type { ProjectEffectiveSettings } from './types.generated';

export function fetchProjectEffectiveSettings(
  projectId: string,
): Promise<ProjectEffectiveSettings> {
  return api.get<ProjectEffectiveSettings>(`/projects/${projectId}/effective-settings`);
}
