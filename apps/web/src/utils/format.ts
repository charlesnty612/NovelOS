// 复用小工具：日期格式化、JSON 美化展示。

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  // 后端 ISO 可能带时区（UTC: "...Z" / "+00:00"）或不带；用 Date 解析以正确转换到本地时区。
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    // 非标准 ISO：退化为原样截取
    const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    if (m) return `${m[1]} ${m[2]}`;
    return iso;
  }
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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

// ---------------------------------------------------------------------------
// Report Markdown 轻量格式化（Sprint 11 下半，参照系面板用）
//
// 白名单渲染：只识别以 `#` / `##` / `###` 开头的标题与以 `- ` 开头的无序列表；
// 其它行按段落原样输出。不引第三方 markdown 库。
//
// 设计要点：
// - 每行独立解析；不跨行合并（保留作者换行）。
// - 行内不做 bold/italic/code 等富文本处理——`canon.report_md` 是机器渲染的纯文本，
//   仅需层级结构（章节标题 / 列表），不引入 XSS 风险。
// ---------------------------------------------------------------------------

export type ReportBlock =
  | { kind: 'heading'; level: 1 | 2 | 3; text: string }
  | { kind: 'list-item'; text: string }
  | { kind: 'paragraph'; text: string };

export function parseReportMarkdown(input: string | null | undefined): ReportBlock[] {
  if (!input) return [];
  const blocks: ReportBlock[] = [];
  const lines = input.split(/\r?\n/);
  let currentParagraph: string[] = [];

  const flushParagraph = () => {
    const text = currentParagraph.join(' ').trim();
    if (text) blocks.push({ kind: 'paragraph', text });
    currentParagraph = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.trim() === '') {
      flushParagraph();
      continue;
    }
    if (line.startsWith('### ')) {
      flushParagraph();
      blocks.push({ kind: 'heading', level: 3, text: line.slice(4).trim() });
      continue;
    }
    if (line.startsWith('## ')) {
      flushParagraph();
      blocks.push({ kind: 'heading', level: 2, text: line.slice(3).trim() });
      continue;
    }
    if (line.startsWith('# ')) {
      flushParagraph();
      blocks.push({ kind: 'heading', level: 1, text: line.slice(2).trim() });
      continue;
    }
    if (line.startsWith('- ') || line.startsWith('* ')) {
      flushParagraph();
      blocks.push({ kind: 'list-item', text: line.slice(2).trim() });
      continue;
    }
    currentParagraph.push(line);
  }
  flushParagraph();
  return blocks;
}
