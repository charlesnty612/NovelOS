// 复用小工具：日期格式化、JSON 美化展示。

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  // 后端 ISO 形如 "2026-08-23T10:30:00" 或 "...Z"；只截到分钟足够 UI 展示。
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  if (m) return `${m[1]} ${m[2]}`;
  return iso;
}

export function formatJson(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function tryParseJsonObject(input: string):
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string } {
  const trimmed = input.trim();
  if (trimmed === '') return { ok: true, value: {} };
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, error: '必须是 JSON 对象（不可为数组或基本类型）' };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'JSON 解析失败' };
  }
}
