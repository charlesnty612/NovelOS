import { describe, expect, it } from 'vitest';
import { formatDateTime, formatJson, tryParseJsonObject } from './format';

describe('formatDateTime', () => {
  it('ISO 时间截到分钟', () => {
    expect(formatDateTime('2026-08-23T10:30:45Z')).toBe('2026-08-23 10:30');
    expect(formatDateTime('2026-08-23 10:30:45')).toBe('2026-08-23 10:30');
  });
  it('空值返回占位', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
  });
});

describe('formatJson', () => {
  it('对象 → 美化字符串', () => {
    expect(formatJson({ a: 1 })).toContain('"a": 1');
  });
  it('字符串原样返回', () => {
    expect(formatJson('hello')).toBe('hello');
  });
  it('null / undefined → 空字符串', () => {
    expect(formatJson(null)).toBe('');
    expect(formatJson(undefined)).toBe('');
  });
});

describe('tryParseJsonObject', () => {
  it('合法对象解析成功', () => {
    const r = tryParseJsonObject('{"a":1}');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value).toEqual({ a: 1 });
  });
  it('空字符串 → 空对象', () => {
    const r = tryParseJsonObject('');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value).toEqual({});
  });
  it('数组被拒绝', () => {
    const r = tryParseJsonObject('[1,2]');
    expect(r.ok).toBe(false);
  });
  it('非法 JSON 报错', () => {
    const r = tryParseJsonObject('{not json}');
    expect(r.ok).toBe(false);
  });
});
