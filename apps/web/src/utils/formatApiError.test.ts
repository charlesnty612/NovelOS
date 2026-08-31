// formatApiError 单元测试（Sprint 22「交互反馈统一」批次）。
// 覆盖：
// - ApiError → 「操作失败（{status}）：{detail}」统一口径
// - 普通 Error → 「操作失败：{message}」
// - 其它（字符串/对象/null/undefined）→ 「操作失败：未知错误」

import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { formatApiError } from './formatApiError';

describe('formatApiError', () => {
  it('ApiError 走「操作失败（status）：detail」统一口径', () => {
    const e = new ApiError(409, 'chapter status PLANNED is not commitable');
    expect(formatApiError(e)).toBe(
      '操作失败（409）：chapter status PLANNED is not commitable',
    );
  });

  it('普通 Error 走「操作失败：message」', () => {
    expect(formatApiError(new Error('网络超时'))).toBe('操作失败：网络超时');
  });

  it('字符串（非 Error）走「未知错误」', () => {
    expect(formatApiError('boom')).toBe('操作失败：未知错误');
  });

  it('对象走「未知错误」', () => {
    expect(formatApiError({ code: 'X' })).toBe('操作失败：未知错误');
  });

  it('null / undefined 走「未知错误」', () => {
    expect(formatApiError(null)).toBe('操作失败：未知错误');
    expect(formatApiError(undefined)).toBe('操作失败：未知错误');
  });
});