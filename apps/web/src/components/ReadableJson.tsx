// ReadableJson: 人读友好的 JSON 视图。
//
// 设计目标：
// - 把后端自由的 JSON 字段以「中文标签：值」的形式展示给作者；
// - 关系类数组（to_name / relation_type / one_line）特化为列表；
// - 未知键回退原键名，绝不丢字段；
// - 原始 JSON 通过 RawJsonDetails 折叠块保留给高级用户。
//
// 本组件是纯展示组件，不修改输入；保证调用方随时切回原始 `<pre>` 也不丢信息。

import type { ReactNode } from 'react';
import { formatJson } from '../utils/format';

/**
 * 本项目常见 core_json / data_json 字段的中文标签映射。
 * 命中走中文，未命中回退原键名（保留可读性 + 不丢信息）。
 */
export const COMMON_LABELS: Record<string, string> = {
  motivation: '动机',
  goal: '目标',
  conflict: '核心冲突',
  distinctive_trait: '特质',
  fear: '恐惧',
  values: '价值观',
  desire: '欲望',
  flaw: '缺陷',
  one_line: '一句话',
  original_identity: '原身身份',
  transmigration: '穿越设定',
  naming_taboo: '名讳避讳',
  name: '姓名',
  role: '职责',
  statement: '表述',
  rules: '规则',
  locations: '地点',
  factions: '势力',
  to_name: '对象',
  relation_type: '关系',
  notes: '备注',
  personality: '性格',
  fears: '恐惧',
  desires: '欲望',
  reflection: '心结',
  arc: '成长弧',
  secret: '秘密',
};

function labelOf(key: string, labelMap?: Record<string, string>): string {
  if (labelMap && labelMap[key]) return labelMap[key];
  if (COMMON_LABELS[key]) return COMMON_LABELS[key];
  return key;
}

/** 把任意标量值格式化成人读字符串。 */
function renderScalar(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') {
    const t = value.trim();
    return t === '' ? '—' : value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  // 对象/数组交给递归分支处理；这里兜底成 JSON 字符串。
  return formatJson(value);
}

/** 判断数组元素是不是「关系对象」——含 to_name 即按关系渲染。 */
function isRelationArray(arr: unknown[]): boolean {
  return arr.length > 0 && arr.every(
    (item) => item !== null && typeof item === 'object' && !Array.isArray(item) &&
      typeof (item as Record<string, unknown>).to_name === 'string',
  );
}

/**
 * 关系数组特化渲染：每项展示「{to_name} · {relation_type} — {one_line}」。
 * 缺字段自动用兜底占位；非 one_line 字段渲染为小尾巴避免丢信息。
 */
function renderRelations(arr: unknown[]): ReactNode {
  return (
    <ul style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}>
      {arr.map((item, idx) => {
        const rel = item as Record<string, unknown>;
        const to = typeof rel.to_name === 'string' ? rel.to_name : '?';
        const typeKey = typeof rel.relation_type === 'string' ? rel.relation_type : '';
        const typeLabel = typeKey ? labelOf(typeKey) : '未填';
        const line = typeof rel.one_line === 'string' ? rel.one_line : '';
        const extraKeys = Object.keys(rel).filter(
          (k) => k !== 'to_name' && k !== 'relation_type' && k !== 'one_line',
        );
        const extra = extraKeys.length > 0
          ? extraKeys
              .map((k) => `${labelOf(k)}：${renderScalar(rel[k])}`)
              .join('；')
          : '';
        return (
          <li
            key={idx}
            style={{
              padding: '4px 0',
              borderBottom: '1px dashed var(--color-border)',
            }}
          >
            <span style={{ fontWeight: 600 }}>{to}</span>
            <span className="muted small"> · {typeLabel}</span>
            {line ? <span> — {line}</span> : null}
            {extra ? (
              <div className="muted small" style={{ marginTop: 2 }}>
                {extra}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 把对象值按行渲染为「中文标签：值」。空对象 → 「暂无」。
 * 嵌套对象/数组会用一行紧凑 JSON 呈现，避免无限层级爆炸。
 */
function renderObject(
  value: Record<string, unknown>,
  labelMap?: Record<string, string>,
): ReactNode {
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return <div className="muted small">暂无</div>;
  }
  return (
    <ul style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}>
      {entries.map(([k, v]) => {
        const isObjLike = v !== null && (typeof v === 'object');
        return (
          <li
            key={k}
            style={{
              padding: '4px 0',
              borderBottom: '1px dashed var(--color-border)',
            }}
          >
            <span className="muted small">{labelOf(k, labelMap)}：</span>
            {isObjLike ? (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                {formatJson(v)}
              </span>
            ) : (
              <span>{renderScalar(v)}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export interface ReadableJsonProps {
  value: unknown;
  labelMap?: Record<string, string>;
  /** 空对象 / null / undefined 时显示的占位文本。默认「暂无」。 */
  emptyText?: string;
  testId?: string;
}

/**
 * 把 JSON / 普通值渲染成人读卡片：
 * - 标量直接显示；
 * - 空对象/数组/空字符串/0 视为空，渲染占位；
 * - 对象逐键标签展示；
 * - 关系类数组特化为「对象 · 关系 — 一句话」列表；
 * - 其它数组按列表递归。
 */
export function ReadableJson({
  value,
  labelMap,
  emptyText = '暂无',
  testId,
}: ReadableJsonProps) {
  if (value === null || value === undefined) {
    return (
      <div className="muted small" data-testid={testId}>
        {emptyText}
      </div>
    );
  }
  if (typeof value === 'string') {
    const t = value.trim();
    if (t === '') {
      return (
        <div className="muted small" data-testid={testId}>
          {emptyText}
        </div>
      );
    }
    return (
      <div data-testid={testId} style={{ whiteSpace: 'pre-wrap' }}>
        {value}
      </div>
    );
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return (
      <div data-testid={testId}>{String(value)}</div>
    );
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return (
        <div className="muted small" data-testid={testId}>
          {emptyText}
        </div>
      );
    }
    if (isRelationArray(value)) {
      return <div data-testid={testId}>{renderRelations(value)}</div>;
    }
    // 其它数组：每项作为列表条目，若元素是对象则转紧凑 JSON。
    return (
      <ul
        data-testid={testId}
        style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}
      >
        {value.map((item, idx) => (
          <li
            key={idx}
            style={{
              padding: '4px 0',
              borderBottom: '1px dashed var(--color-border)',
            }}
          >
            {item === null || typeof item !== 'object' ? (
              renderScalar(item)
            ) : (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                {formatJson(item)}
              </span>
            )}
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === 'object') {
    return (
      <div data-testid={testId}>
        {renderObject(value as Record<string, unknown>, labelMap)}
      </div>
    );
  }
  return (
    <div className="muted small" data-testid={testId}>
      {emptyText}
    </div>
  );
}

export interface RawJsonDetailsProps {
  value: unknown;
  testId?: string;
  /** 折叠块标题。默认「原始 JSON」。 */
  summary?: string;
}

/**
 * 给高级用户保留的原始 JSON 折叠块。
 * 默认折叠；点击 summary 展开 `<pre className="json-block">`。
 */
export function RawJsonDetails({
  value,
  testId,
  summary = '原始 JSON',
}: RawJsonDetailsProps) {
  const text = formatJson(value);
  return (
    <details data-testid={testId} style={{ marginTop: 6 }}>
      <summary className="muted small" style={{ cursor: 'pointer' }}>
        {summary}
      </summary>
      <pre className="json-block">{text}</pre>
    </details>
  );
}
