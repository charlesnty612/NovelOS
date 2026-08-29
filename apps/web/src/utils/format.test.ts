import { describe, expect, it } from 'vitest';
import { formatDateTime, formatJson, parseReportMarkdown, tryParseJsonObject } from './format';

describe('formatDateTime', () => {
  it('ISO 时间转本地时区（UTC 带 Z 正确转换）', () => {
    // 带 Z 的 UTC 时间：按本地时区显示（不写死具体值，因 CI/本地时区不同）
    const local = formatDateTime('2026-08-23T10:30:45Z');
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    // 与 Date 解析结果一致（证明走了时区转换而非字符串截取）
    const d = new Date('2026-08-23T10:30:45Z');
    const pad = (n: number) => String(n).padStart(2, '0');
    const expected = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    expect(local).toBe(expected);
  });
  it('无时区 ISO 按原样解析（不崩）', () => {
    // 不带时区后缀：Date 按本地解析，格式一致即可
    expect(formatDateTime('2026-08-23 10:30:45')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
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

describe('parseReportMarkdown', () => {
  it('空 / null / undefined → 空数组', () => {
    expect(parseReportMarkdown('')).toEqual([]);
    expect(parseReportMarkdown(null)).toEqual([]);
    expect(parseReportMarkdown(undefined)).toEqual([]);
  });
  it('# / ## / ### 解析为 heading 块', () => {
    const md = '# 一级\n## 二级\n### 三级';
    const blocks = parseReportMarkdown(md);
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, text: '一级' },
      { kind: 'heading', level: 2, text: '二级' },
      { kind: 'heading', level: 3, text: '三级' },
    ]);
  });
  it('- 列表项解析为 list-item 块', () => {
    const md = '- 第一条\n- 第二条\n* 第三条';
    const blocks = parseReportMarkdown(md);
    expect(blocks).toEqual([
      { kind: 'list-item', text: '第一条' },
      { kind: 'list-item', text: '第二条' },
      { kind: 'list-item', text: '第三条' },
    ]);
  });
  it('普通行 → 段落；连续行合并为同一段（用空格拼接）', () => {
    const md = '第一行\n第二行';
    const blocks = parseReportMarkdown(md);
    expect(blocks).toEqual([{ kind: 'paragraph', text: '第一行 第二行' }]);
  });
  it('空行作段落分隔', () => {
    const md = '段落 A\n\n段落 B';
    const blocks = parseReportMarkdown(md);
    expect(blocks).toEqual([
      { kind: 'paragraph', text: '段落 A' },
      { kind: 'paragraph', text: '段落 B' },
    ]);
  });
  it('混合：标题 + 段落 + 列表', () => {
    const md = '# 标题\n正文第一行\n正文第二行\n\n- 列表 1\n- 列表 2';
    const blocks = parseReportMarkdown(md);
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, text: '标题' },
      { kind: 'paragraph', text: '正文第一行 正文第二行' },
      { kind: 'list-item', text: '列表 1' },
      { kind: 'list-item', text: '列表 2' },
    ]);
  });
});
