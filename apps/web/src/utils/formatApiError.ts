// 统一错误文案格式化（Sprint 22「交互反馈统一」批次引入）。
//
// 口径：`操作失败（{status}）：{detail}`。
// - ApiError：用其 status + detail。
// - 普通 Error：用其 message。
// - 其它：兜底为「未知错误」。
//
// 设计要点：
// - 保持纯函数、无副作用；调用方负责传入上下文前缀（若有）。
// - 不在抛错处再写 `操作失败（${e.status}）：${e.detail}` 这种拼写——
//   用本函数统一一处。
import { ApiError } from '../api/client';

export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    return `操作失败（${err.status}）：${err.detail}`;
  }
  if (err instanceof Error) {
    return `操作失败：${err.message}`;
  }
  return '操作失败：未知错误';
}