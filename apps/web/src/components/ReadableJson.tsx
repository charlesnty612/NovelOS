// ReadableJson: 人读友好的 JSON 视图。
//
// 设计目标：
// - 把后端自由的 JSON 字段以「中文标签：值」的形式展示给作者；
// - 关系类数组（to_name / relation_type / one_line 及同义键）特化为「对象 · 关系 — 一句话」列表；
// - 对象数组（同构或近同构）表格化：列 = 按键首次出现顺序的并集，行数超阈值折叠；
// - 未知键回退原键名，绝不丢字段；
// - 嵌套超过 MAX_NESTED_DEPTH 层回落原始 JSON 折叠块，避免超深结构炸版面；
// - 原始 JSON 通过 RawJsonDetails 折叠块保留给高级用户。
//
// 本组件是纯展示组件，不修改输入；保证调用方随时切回原始 `<pre>` 也不丢信息。

import type { ReactNode } from 'react';
import { formatJson } from '../utils/format';

/** 嵌套展开最大层数：再往下的对象/数组一律回落原始 JSON 折叠块。 */
const MAX_NESTED_DEPTH = 2;

/** 对象数组表格的常显行数：超出部分折叠进「展开其余 N 行」。 */
const TABLE_PREVIEW_ROWS = 8;

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
  role: '角色',
  statement: '表述',
  rules: '规则',
  locations: '地点',
  factions: '势力',
  to_name: '对象',
  relation_type: '关系',
  relationships: '关系',
  description: '说明',
  notes: '备注',
  personality: '性格',
  personality_tags: '性格标签',
  fears: '恐惧',
  desires: '欲望',
  reflection: '心结',
  arc: '成长弧',
  secret: '秘密',
  core_drive: '核心驱动',
  foil_techniques: '反差手法',
  identity: '身份',
  visibility: '可见性',
  state_version: '状态版本',
  state_json: '状态内容',
  // 题材包 / canon 常见键（CanonTab 详情、genre payload 展示用）
  logline: '一句话卖点',
  spine: '主线节拍',
  payoff: '爽点设计',
  rhythm: '节奏基调',
  protagonist: '主角人设',
  style_params: '文风参数',
  techniques: '技法',
  emotion_curve: '情绪曲线',
  opening_rules: '开篇规则',
  critic_rubric: '审校要点',
  payoff_types: '爽点类型',
  ratio_declarations: '配比声明',
  pacing: '节奏',
  structure_templates: '结构模板',
  density_cap: '密度上限',
  min_interval_chapters: '同型间隔',
  verify_hint: '核验口径',
  mapped_tropes: '关联桥段',
  applicable: '适用段落',
  fatigue_risk: '疲劳风险',
};

/**
 * 关系对象的三组同义键：命中组合即按关系列表渲染。
 * 现役形状（project_init 契约）是 to_name / relation_type / one_line；
 * 早期人工形状是 name / type / description。
 */
const RELATION_SPECIFIC_NAME_KEYS = ['to_name', 'to', 'character_id'];
const RELATION_NAME_KEYS = [...RELATION_SPECIFIC_NAME_KEYS, 'name'];
const RELATION_TYPE_KEYS = ['relation_type', 'type'];
const RELATION_LINE_KEYS = ['one_line', 'description'];

/**
 * relation_type 的英文枚举值 → 中文（值域自由，未命中回退原值）。
 * 后端写的是 ally / rival / family 一类的机器枚举，直接展示对作者不友好。
 */
const RELATION_TYPE_LABELS: Record<string, string> = {
  ally: '盟友',
  friend: '朋友',
  family: '家人',
  kin: '亲属',
  rival: '对手',
  enemy: '敌人',
  foe: '敌人',
  complex: '纠葛',
  mentor: '导师',
  student: '学生',
  lover: '恋人',
  spouse: '配偶',
  superior: '上级',
  subordinate: '下属',
  neutral: '中立',
};

function labelOf(key: string, labelMap?: Record<string, string>): string {
  if (labelMap && labelMap[key]) return labelMap[key];
  if (COMMON_LABELS[key]) return COMMON_LABELS[key];
  return key;
}

/** 普通对象（排除 null 与数组）。 */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** 值是否需要递归回 <ReadableJson>（对象 / 数组；null 不算）。 */
function isContainer(value: unknown): boolean {
  return value !== null && typeof value === 'object';
}

/** 把任意标量值格式化成人读字符串。 */
function renderScalar(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') {
    const t = value.trim();
    return t === '' ? '—' : value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  // 对象/数组正常走递归分支；这里兜底成 JSON 字符串（防御性）。
  return formatJson(value);
}

/** 按候选键顺序取第一个非空字符串字段。 */
function firstStringField(
  obj: Record<string, unknown>,
  keys: string[],
): { key: string; value: string } | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v.trim() !== '') return { key: k, value: v };
  }
  return null;
}

/** 关系对象的三组字段（缺失的组为 null）。 */
function relationFields(obj: Record<string, unknown>) {
  return {
    name: firstStringField(obj, RELATION_NAME_KEYS),
    type: firstStringField(obj, RELATION_TYPE_KEYS),
    line: firstStringField(obj, RELATION_LINE_KEYS),
  };
}

/**
 * 判断数组元素是不是「关系对象」：
 * - 关系专属名字键（to_name / to / character_id）+ （关系键 | 一句话键）；
 * - 通用名字键（name）+ 关系键（早期人工形状 {name, type}）。
 * 只有 name + description 的普通记录（如 state 的 new_hooks）不算——避免误判成关系列表。
 */
function isRelationArray(arr: unknown[]): boolean {
  return arr.length > 0 && arr.every((item) => {
    if (!isPlainObject(item)) return false;
    const { name, type, line } = relationFields(item);
    if (name === null) return false;
    if (RELATION_SPECIFIC_NAME_KEYS.includes(name.key)) {
      return type !== null || line !== null;
    }
    return type !== null;
  });
}

/** 数组元素是否全为普通对象（同构或近同构）——命中走表格渲染。 */
function isObjectArray(arr: unknown[]): boolean {
  return arr.length > 0 && arr.every(isPlainObject);
}

/**
 * 关系数组特化渲染：每项展示「{对象} · {关系} — {一句话}」。
 * 缺字段自动用兜底占位；其余键以小字尾巴渲染，避免丢信息。
 */
function renderRelations(
  arr: unknown[],
  depth: number,
  labelMap?: Record<string, string>,
): ReactNode {
  return (
    <ul style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}>
      {arr.map((item, idx) => {
        const rel = item as Record<string, unknown>;
        const { name, type, line } = relationFields(rel);
        const used = new Set(
          [name?.key, type?.key, line?.key].filter((k): k is string => Boolean(k)),
        );
        const extraKeys = Object.keys(rel).filter((k) => !used.has(k));
        return (
          <li
            key={idx}
            style={{
              padding: '4px 0',
              borderBottom: '1px dashed var(--color-border)',
            }}
          >
            <span style={{ fontWeight: 600 }}>{name ? name.value : '?'}</span>
            <span className="muted small">
              {' · '}
              {type ? RELATION_TYPE_LABELS[type.value] ?? type.value : '未填'}
            </span>
            {line ? <span> — {line.value}</span> : null}
            {extraKeys.length > 0 ? (
              <div className="muted small" style={{ marginTop: 2 }}>
                {extraKeys.map((k) => (
                  <div key={k}>
                    <span>{labelOf(k, labelMap)}：</span>
                    {isContainer(rel[k]) ? (
                      <ReadableJson value={rel[k]} labelMap={labelMap} depth={depth + 1} />
                    ) : (
                      <span>{renderScalar(rel[k])}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 对象数组表格化：列 = 按键首次出现顺序的并集（异构行缺列显示「—」）。
 * 行数超过 TABLE_PREVIEW_ROWS 时前 N 行常显，其余折叠进 details/summary。
 */
function renderObjectTable(
  rows: Record<string, unknown>[],
  depth: number,
  labelMap?: Record<string, string>,
): ReactNode {
  const columns: string[] = [];
  for (const row of rows) {
    for (const k of Object.keys(row)) {
      if (!columns.includes(k)) columns.push(k);
    }
  }
  if (columns.length === 0) return <div className="muted small">暂无</div>;

  const head = (
    <thead>
      <tr>
        {columns.map((c) => (
          <th key={c} scope="col">
            {labelOf(c, labelMap)}
          </th>
        ))}
      </tr>
    </thead>
  );
  const renderRows = (list: Record<string, unknown>[]) =>
    list.map((row, idx) => (
      <tr key={idx}>
        {columns.map((c) => (
          <td key={c}>
            {isContainer(row[c]) ? (
              <ReadableJson value={row[c]} labelMap={labelMap} depth={depth + 1} />
            ) : (
              renderScalar(row[c])
            )}
          </td>
        ))}
      </tr>
    ));

  const preview = rows.slice(0, TABLE_PREVIEW_ROWS);
  const rest = rows.slice(TABLE_PREVIEW_ROWS);
  return (
    <div className="table-wrap">
      <table className="table">
        {head}
        <tbody>{renderRows(preview)}</tbody>
      </table>
      {rest.length > 0 ? (
        <details style={{ marginTop: 6 }}>
          <summary className="muted small" style={{ cursor: 'pointer' }}>
            展开其余 {rest.length} 行
          </summary>
          <table className="table" style={{ marginTop: 6 }}>
            {head}
            <tbody>{renderRows(rest)}</tbody>
          </table>
        </details>
      ) : null}
    </div>
  );
}

/**
 * 把对象值按行渲染为「中文标签：值」。空对象 → 「暂无」。
 * 嵌套对象/数组递归回 <ReadableJson>；超过 MAX_NESTED_DEPTH 层自动回落原始 JSON。
 */
function renderObject(
  value: Record<string, unknown>,
  depth: number,
  labelMap?: Record<string, string>,
): ReactNode {
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return <div className="muted small">暂无</div>;
  }
  return (
    <ul style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}>
      {entries.map(([k, v]) => (
        <li
          key={k}
          style={{
            padding: '4px 0',
            borderBottom: '1px dashed var(--color-border)',
          }}
        >
          <span className="muted small">{labelOf(k, labelMap)}：</span>
          {isContainer(v) ? (
            <div style={{ marginTop: 2 }}>
              <ReadableJson value={v} labelMap={labelMap} depth={depth + 1} />
            </div>
          ) : (
            <span>{renderScalar(v)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export interface ReadableJsonProps {
  value: unknown;
  labelMap?: Record<string, string>;
  /** 空对象 / null / undefined 时显示的占位文本。默认「暂无」。 */
  emptyText?: string;
  testId?: string;
  /**
   * 嵌套深度（递归内部用；调用方通常不传）。
   * 超过 MAX_NESTED_DEPTH 的对象/数组回落 <RawJsonDetails>，防超深结构炸版面。
   */
  depth?: number;
}

/**
 * 把 JSON / 普通值渲染成人读卡片：
 * - 标量直接显示；
 * - 空对象/数组/空字符串/null 视为空，渲染占位；
 * - 对象逐键标签展示，嵌套值递归渲染；
 * - 关系类数组特化为「对象 · 关系 — 一句话」列表；
 * - 全对象数组表格化（列 = 键并集，超 8 行折叠）；
 * - 其它数组（标量 / 混合）按列表递归；
 * - 嵌套超过 2 层回落原始 JSON 折叠块。
 */
export function ReadableJson({
  value,
  labelMap,
  emptyText = '暂无',
  testId,
  depth = 0,
}: ReadableJsonProps) {
  // 深度护栏：再往下递归就是「JSON 套 JSON」，不如把原始 JSON 交给折叠块。
  if (depth > MAX_NESTED_DEPTH && isContainer(value)) {
    return (
      <div data-testid={testId}>
        <RawJsonDetails value={value} />
      </div>
    );
  }
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
      return <div data-testid={testId}>{renderRelations(value, depth, labelMap)}</div>;
    }
    if (isObjectArray(value)) {
      return (
        <div data-testid={testId}>
          {renderObjectTable(value as Record<string, unknown>[], depth, labelMap)}
        </div>
      );
    }
    // 标量数组 / 混合数组：每项作为列表条目；对象元素递归渲染而非紧凑 JSON。
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
            {isContainer(item) ? (
              <ReadableJson value={item} labelMap={labelMap} depth={depth + 1} />
            ) : (
              renderScalar(item)
            )}
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === 'object') {
    return (
      <div data-testid={testId}>
        {renderObject(value as Record<string, unknown>, depth, labelMap)}
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
