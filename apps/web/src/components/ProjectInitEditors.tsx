/**
 * ProjectInitEditors —— 分步审阅向导的字段编辑器集合（P1 project-init）。
 *
 * 设计要点（与主会话方案对齐）：
 * - 已知白名单字段 → 专属控件 + 中文 label（见 PREMISE_FIELDS / WORLD_FIELDS / ...）。
 * - 未知键 → string → textarea(rows=3) + label 显示键名；
 *               number → 数字输入框；
 *               其他类型 → 紧凑 JSON 域（rows=4，parse 校验，非法禁用放行）。
 * - array of string → 多行文本域（rows=5，每行一条）。
 * - array of object（rules/locations/factions/characters/chapter_seeds）→ 卡片列表：
 *   每张卡片内白名单字段专属控件 + 未知键按未知键规则；每卡「删除」按钮；
 *   列表尾「+ 添加一条」按钮（新卡给该数组元素类型的空模板）。
 * - 嵌套 object（protagonist / volume / 元素内 object 值）→ 展开一层键值对渲染，
 *   二层及更深的值 → JSON 域。
 * - 类型不符预期（如 characters 不是数组）→ 该字段整体回退 JSON 文本域（rows=14）。
 *
 * 数据完整性铁律（最高优先级）：
 *   提交时组装 revision 必须是完整 draft。编辑器未识别/未渲染的键必须原样保留透传；
 *   任意类型错误（顶层字段类型不符预期）都强制回退 JSON 域，让用户直接编辑。
 *
 * testid 命名约定：
 *   - 已知控件：revision-{label 拼音 or stage}-{key}（premise 仍沿用旧 testid）
 *   - 卡片：revision-card-{stage}-{key}-{idx}
 *   - 卡片内字段：revision-card-{stage}-{key}-{idx}-{subKey}
 *   - 卡片删除：revision-card-remove-{stage}-{key}-{idx}
 *   - 添加按钮：revision-card-add-{stage}-{key}
 *   - JSON 兜底：revision-json-fallback-{stage}-{key}
 *   - 错误提示：revision-error-{stage}-{key}
 */
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';

import { ErrorBanner } from './ErrorBanner';

// =============== 工具类型 =========================================================

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue };

export type DraftValue = unknown;

// =============== 通用工具 =========================================================

function isPlainObject(v: unknown): boolean {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/**
 * 解析 JSON 字符串；失败返回 { ok: false, error }，成功返回 { ok: true, value }。
 */
export function safeJsonParse(
  raw: string,
): { ok: true; value: unknown } | { ok: false; error: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: {} };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, error: msg };
  }
}

// =============== 控件规范 =========================================================

/** 控件类型：单行文本框 / 多行文本域 / 数字输入框 / 多行文本域（每行一条 array）/ JSON 兜底 */
export type FieldKind =
  | 'text'
  | 'textarea'
  | 'number'
  | 'lines'
  | 'json-fallback'
  | 'nested-object'
  | 'object-list';

export interface FieldSpec {
  /** 字段在 draft 里的键名 */
  key: string;
  /** 中文 label */
  label: string;
  /** 控件类型；缺省 'text' */
  kind?: FieldKind;
  /** textarea/lines 的最小行数；JSON 兜底固定 rows=14 */
  rows?: number;
  /**
   * 嵌套 object 展开时的子字段白名单（嵌套一层）。
   * kind === 'nested-object' 时必填（即使为空对象）。
   * 子字段 key 留 '' 表示未知键按未知键规则展开。
   */
  nested?: FieldSpec[];
  /**
   * 数组元素 object 的白名单字段（kind === 'object-list' 时必填）。
   * 每个元素都按这套字段渲染；未在白名单的键按未知键规则渲染。
   */
  itemFields?: FieldSpec[];
  /** testid 后缀（默认 = key）。已预写过字段名 */
  testId?: string;
  /**
   * 是否必填；缺省 false。
   * 对 textarea/text 表现为：提交时若值为空字符串，则不合法。
   * 对 number 表现为：值非有限数字即非法。
   */
  required?: boolean;
}

// =============== JSON 域非法注册表 ===============================================

/**
 * 编辑器层维护一个非法 JSON 域的集合，回调到 ReviewPane。
 * - domainKey: 域的唯一 id（路径形式，例如 "world.rules" 或 "character.characters[0].extra"）
 * - ok=true 表示该域当前 parse 通过；ok=false 表示非法。
 *
 * 设计动机：JSON 兜底域 / 未知键 JSON 域在 parse 失败时不能 onChange 写回 draft，
 * 否则 draft 保留旧值、用户以为改了实际提交的是旧值。
 */
export type JsonValidityChange = (
  domainKey: string,
  ok: boolean,
) => void;

export interface JsonValidityContext {
  onJsonValidityChange: JsonValidityChange;
}

// =============== Stage 白名单（中文 label 定稿）===================================

const PREMISE_FIELDS: FieldSpec[] = [
  { key: 'title', label: '题材', kind: 'text', testId: 'title' },
  { key: 'genre', label: '类型', kind: 'text', testId: 'genre' },
  { key: 'logline', label: '一句话简介', kind: 'textarea', rows: 5, testId: 'logline', required: true },
  { key: 'positioning', label: '定位', kind: 'textarea', rows: 5, testId: 'positioning' },
  {
    key: 'selling_points',
    label: '卖点（每行一条）',
    kind: 'lines',
    rows: 6,
    testId: 'selling-points',
  },
  {
    key: 'protagonist',
    label: '主角',
    kind: 'nested-object',
    nested: [
      { key: 'name', label: '姓名', kind: 'text' },
      { key: 'age', label: '年龄', kind: 'number' },
      { key: 'gender', label: '性别', kind: 'text' },
      { key: 'background', label: '背景', kind: 'textarea', rows: 3 },
      { key: 'motivation', label: '动机', kind: 'textarea', rows: 3 },
      { key: 'goal', label: '目标', kind: 'textarea', rows: 3 },
      { key: 'conflict', label: '核心冲突', kind: 'textarea', rows: 3 },
    ],
    testId: 'protagonist',
  },
];

const WORLD_FIELDS: FieldSpec[] = [
  { key: 'core_premise', label: '世界观核心设定', kind: 'textarea', rows: 6, testId: 'core-premise' },
  {
    key: 'rules',
    label: '世界规则',
    kind: 'object-list',
    itemFields: [
      { key: 'name', label: '规则名', kind: 'text' },
      { key: 'statement', label: '规则陈述', kind: 'textarea', rows: 4 },
    ],
    testId: 'rules',
  },
  {
    key: 'locations',
    label: '地点',
    kind: 'object-list',
    itemFields: [
      { key: 'name', label: '地点名', kind: 'text' },
      { key: 'statement', label: '地点描述', kind: 'textarea', rows: 3 },
    ],
    testId: 'locations',
  },
  {
    key: 'factions',
    label: '势力',
    kind: 'object-list',
    itemFields: [
      { key: 'name', label: '势力名', kind: 'text' },
      { key: 'statement', label: '势力描述', kind: 'textarea', rows: 3 },
    ],
    testId: 'factions',
  },
];

const CHARACTER_FIELDS: FieldSpec[] = [
  {
    key: 'characters',
    label: '核心角色',
    kind: 'object-list',
    itemFields: [
      { key: 'name', label: '姓名', kind: 'text' },
      { key: 'role', label: '定位', kind: 'text' },
      // 动机/目标/核心冲突/辨识特征实际存放在 core_json 子对象里（pipeline _normalize_characters
      // 把 prompt 平铺字段收进 core_json），顶层无这些键——白名单必须挂到 core_json 下，
      // 否则编辑器读顶层为空、放行时还会把空值覆盖掉已有内容。
      { key: 'core_json', label: '角色设定', kind: 'nested-object', nested: [
        { key: 'motivation', label: '动机', kind: 'textarea', rows: 3 },
        { key: 'goal', label: '目标', kind: 'textarea', rows: 3 },
        { key: 'conflict', label: '核心冲突', kind: 'textarea', rows: 3 },
        { key: 'distinctive_trait', label: '辨识特征', kind: 'textarea', rows: 3 },
      ]},
    ],
    testId: 'characters',
  },
];

const OUTLINE_FIELDS: FieldSpec[] = [
  {
    key: 'volume',
    label: '第一卷',
    kind: 'nested-object',
    nested: [
      { key: 'number', label: '卷号', kind: 'number' },
      { key: 'title', label: '卷名', kind: 'text', testId: 'volume-title' },
      { key: 'arc_summary', label: '卷纲摘要', kind: 'textarea', rows: 6 },
    ],
    testId: 'volume',
  },
  {
    key: 'chapter_seeds',
    label: '章节种子',
    kind: 'object-list',
    itemFields: [
      { key: 'number', label: '章号', kind: 'number' },
      { key: 'title', label: '章名', kind: 'text' },
      { key: 'role', label: '类型', kind: 'text' },
      { key: 'one_sentence', label: '一句话剧情', kind: 'textarea', rows: 4 },
      { key: 'expected_word_count', label: '目标字数', kind: 'number' },
      { key: 'key_beats', label: '关键节拍（每行一条）', kind: 'lines', rows: 3 },
    ],
    testId: 'chapter-seeds',
  },
];

export const STAGE_FIELDS: Record<string, FieldSpec[]> = {
  premise: PREMISE_FIELDS,
  world: WORLD_FIELDS,
  character: CHARACTER_FIELDS,
  outline: OUTLINE_FIELDS,
};

/**
 * 已知前置元数据（_degraded/error/schema_version/prompt_version 等）按未知键规则渲染，
 * 但这里给一个明确列表提示"它们属于后端元信息，原样透传即可"，便于代码可读。
 */
const META_KEY_HINT = '后端元信息（编辑后会写回）';

// =============== 空模板构造 ======================================================

function emptyForField(spec: FieldSpec): unknown {
  switch (spec.kind ?? 'text') {
    case 'number':
      return 0;
    case 'textarea':
    case 'text':
    case 'lines':
      return '';
    case 'json-fallback':
      return {};
    case 'nested-object': {
      const obj: Record<string, unknown> = {};
      for (const f of spec.nested ?? []) obj[f.key] = emptyForField(f);
      return obj;
    }
    case 'object-list':
      return [];
    default:
      return '';
  }
}

/** 给一个数组元素 object 的空模板（白名单字段空串/0） */
function emptyForItem(itemFields: FieldSpec[]): Record<string, unknown> {
  const obj: Record<string, unknown> = {};
  for (const f of itemFields) obj[f.key] = emptyForField(f);
  return obj;
}

// =============== 通用 Field 包装 =================================================

function FieldWrap({
  label,
  hint,
  errorText,
  children,
}: {
  label: string;
  hint?: string;
  errorText?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div className="muted small">
        {label}
        {hint ? <span className="muted small"> · {hint}</span> : null}
      </div>
      {children}
      {errorText ? (
        <div
          className="small"
          style={{ color: 'crimson', marginTop: 2 }}
          data-testid={`revision-error-${label}`}
        >
          {errorText}
        </div>
      ) : null}
    </div>
  );
}

// =============== 顶层 JSON 兜底（类型不符预期时回退）=============================

function JsonFallbackField({
  stage,
  fieldKey,
  label,
  value,
  onChange,
  errorMessage,
  required,
  onJsonValidityChange,
  domainKey,
}: {
  stage: string;
  fieldKey: string;
  label: string;
  value: unknown;
  onChange: (next: unknown) => void;
  errorMessage: string | null;
  required?: boolean;
  onJsonValidityChange?: JsonValidityChange;
  domainKey?: string;
}) {
  const [text, setText] = useState<string>(() => JSON.stringify(value, null, 2));
  const [parseErr, setParseErr] = useState<string | null>(null);

  const effectiveErr = errorMessage ?? parseErr;

  // 卸载时注销（保证不残留无效标记）
  useEffect(() => {
    return () => {
      if (onJsonValidityChange && domainKey) {
        onJsonValidityChange(domainKey, true);
      }
    };
  }, [onJsonValidityChange, domainKey]);

  return (
    <FieldWrap label={label} hint={required ? '必填' : undefined} errorText={effectiveErr}>
      <textarea
        className="input"
        rows={14}
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          const r = safeJsonParse(next);
          if (r.ok) {
            setParseErr(null);
            onJsonValidityChange?.(domainKey ?? `${stage}.${fieldKey}`, true);
            onChange(r.value);
          } else {
            const msg = `JSON 格式错误，修正后才能放行`;
            setParseErr(msg);
            onJsonValidityChange?.(domainKey ?? `${stage}.${fieldKey}`, false);
            // parse 失败：不写回 draft，避免静默提交旧值。
          }
        }}
        disabled={false}
        data-testid={`revision-json-fallback-${stage}-${fieldKey}`}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
      />
    </FieldWrap>
  );
}

// =============== 单字段渲染器（已知白名单或未知键）===============================

interface SingleFieldProps {
  stage: string;
  fieldKey: string;
  fieldLabel: string;
  kind: FieldKind;
  value: unknown;
  onChange: (next: unknown) => void;
  rows?: number;
  required?: boolean;
  testIdSuffix?: string;
  /** 错误提示（如必填校验失败） */
  errorText?: string | null;
  /**
   * 完全自定义 testid；缺省 = `revision-${stage}-${testIdSuffix ?? fieldKey}`。
   * PremiseEditor 用 NestedObjectField 渲染 protagonist 时需要 testid 不带 stage 前缀。
   */
  customTestId?: string;
}

function SingleField(props: SingleFieldProps) {
  const {
    stage,
    fieldKey,
    fieldLabel,
    kind,
    value,
    onChange,
    rows = 3,
    required,
    testIdSuffix,
    errorText,
    customTestId,
  } = props;
  const testId = customTestId ?? `revision-${stage}-${testIdSuffix ?? fieldKey}`;
  const requiredHint = required ? '必填' : undefined;

  if (kind === 'number') {
    const n = typeof value === 'number' && Number.isFinite(value) ? value : '';
    return (
      <FieldWrap label={fieldLabel} hint={requiredHint} errorText={errorText ?? null}>
        <input
          className="input"
          inputMode="numeric"
          value={n === '' ? '' : String(n)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              onChange(0);
              return;
            }
            const num = Number(raw);
            if (Number.isFinite(num)) onChange(num);
          }}
          data-testid={testId}
          style={{ width: '100%' }}
        />
      </FieldWrap>
    );
  }

  if (kind === 'lines') {
    const text = Array.isArray(value)
      ? value.filter((x): x is string => typeof x === 'string').join('\n')
      : '';
    return (
      <FieldWrap label={fieldLabel} hint={requiredHint} errorText={errorText ?? null}>
        <textarea
          className="input"
          rows={Math.max(rows, 5)}
          value={text}
          onChange={(e) => {
            const lines = e.target.value
              .split('\n')
              .map((s) => s.trim())
              .filter((s) => s.length > 0);
            onChange(lines);
          }}
          data-testid={testId}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </FieldWrap>
    );
  }

  if (kind === 'textarea') {
    const text = typeof value === 'string' ? value : '';
    return (
      <FieldWrap label={fieldLabel} hint={requiredHint} errorText={errorText ?? null}>
        <textarea
          className="input"
          rows={rows}
          value={text}
          onChange={(e) => onChange(e.target.value)}
          data-testid={testId}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </FieldWrap>
    );
  }

  // text
  const text = typeof value === 'string' ? value : '';
  return (
    <FieldWrap label={fieldLabel} hint={requiredHint} errorText={errorText ?? null}>
      <input
        className="input"
        value={text}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        style={{ width: '100%' }}
      />
    </FieldWrap>
  );
}

// =============== 未知键渲染器 ===================================================

function UnknownKeyField({
  stage,
  fieldKey,
  value,
  onChange,
  onJsonValidityChange,
  customTestId,
}: {
  stage: string;
  fieldKey: string;
  value: unknown;
  onChange: (next: unknown) => void;
  onJsonValidityChange?: JsonValidityChange;
  customTestId?: string;
}) {
  const label = `${fieldKey} · ${META_KEY_HINT}`;
  const testId = customTestId ?? `revision-unknown-${stage}-${fieldKey}`;
  // 单字段（number/string）的 testid 也走 customTestId（如 `-{subKey}`）
  const singleFieldCustomTestId = customTestId;

  if (typeof value === 'number') {
    return (
      <SingleField
        stage={stage}
        fieldKey={fieldKey}
        fieldLabel={label}
        kind="number"
        value={value}
        onChange={onChange}
        testIdSuffix={`unknown-${fieldKey}`}
        customTestId={singleFieldCustomTestId}
      />
    );
  }
  if (typeof value === 'string') {
    return (
      <SingleField
        stage={stage}
        fieldKey={fieldKey}
        fieldLabel={label}
        kind="textarea"
        rows={3}
        value={value}
        onChange={onChange}
        testIdSuffix={`unknown-${fieldKey}`}
        customTestId={singleFieldCustomTestId}
      />
    );
  }
  // 其他类型 → 紧凑 JSON 域 rows=4
  return (
    <UnknownKeyJsonField
      stage={stage}
      fieldKey={fieldKey}
      label={label}
      testId={testId}
      value={value}
      onChange={onChange}
      onJsonValidityChange={onJsonValidityChange}
    />
  );
}

/**
 * 未知键的 JSON 域：与 JsonFallbackField 同等的 parse 校验/上抛逻辑，
 * 但 textarea rows=4（紧凑）、testid 为 revision-unknown-{stage}-{fieldKey}。
 */
function UnknownKeyJsonField({
  stage,
  fieldKey,
  label,
  testId,
  value,
  onChange,
  onJsonValidityChange,
}: {
  stage: string;
  fieldKey: string;
  label: string;
  testId: string;
  value: unknown;
  onChange: (next: unknown) => void;
  onJsonValidityChange?: JsonValidityChange;
}) {
  const [text, setText] = useState<string>(() => JSON.stringify(value, null, 2));
  const [parseErr, setParseErr] = useState<string | null>(null);
  const domainKey = `${stage}.unknown.${fieldKey}`;

  useEffect(() => {
    return () => {
      onJsonValidityChange?.(domainKey, true);
    };
  }, [onJsonValidityChange, domainKey]);

  return (
    <FieldWrap label={label} errorText={parseErr}>
      <textarea
        className="input"
        rows={4}
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          const r = safeJsonParse(next);
          if (r.ok) {
            setParseErr(null);
            onJsonValidityChange?.(domainKey, true);
            onChange(r.value);
          } else {
            setParseErr('JSON 格式错误，修正后才能放行');
            onJsonValidityChange?.(domainKey, false);
          }
        }}
        data-testid={testId}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
      />
    </FieldWrap>
  );
}

// =============== 嵌套 Object（展开一层）=========================================

function NestedObjectField({
  stage,
  fieldKey,
  fieldLabel,
  spec,
  value,
  onChange,
  onJsonValidityChange,
  containerTestId,
  childTestIdPrefix,
  childUnknownTestIdPrefix,
  getChildErrorText,
}: {
  stage: string;
  fieldKey: string;
  fieldLabel: string;
  spec: FieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
  onJsonValidityChange?: JsonValidityChange;
  /**
   * 可选 testid 覆盖：缺省 = `revision-${stage}-${fieldKey}`。
   * PremiseEditor 等「需要不带 stage 前缀的容器」场景下传 `revision-${fieldKey}`。
   */
  containerTestId?: string;
  /** 子字段（白名单）testid 前缀；缺省 = `revision-${stage}-${fieldKey}` */
  childTestIdPrefix?: string;
  /** 一层未知键 testid 前缀；缺省 = `revision-${stage}-unknown` */
  childUnknownTestIdPrefix?: string;
  /** 子字段错误查询回调（按 subKey 查错误文本） */
  getChildErrorText?: (subKey: string) => string | null;
}) {
  const obj = isPlainObject(value) ? (value as Record<string, unknown>) : {};
  const nestedSpecs = spec.nested ?? [];
  const whitelistKeys = new Set(nestedSpecs.map((f) => f.key));

  const updateKey = (k: string, v: unknown) => {
    onChange({ ...obj, [k]: v });
  };

  const containerTid = containerTestId ?? `revision-${stage}-${fieldKey}`;
  // 子字段 testid 拼接策略：若调用方指定 childTestIdPrefix，则用 prefix-subKey（不带 stage 前缀）；
  // 否则走默认 `revision-${stage}-${fieldKey}-${subKey}`。
  const useCustomChildPrefix = !!childTestIdPrefix;
  const childPrefix = childTestIdPrefix ?? `revision-${stage}-${fieldKey}`;
  const unknownPrefix = childUnknownTestIdPrefix ?? `revision-${stage}-unknown`;

  return (
    <FieldWrap label={fieldLabel}>
      <div
        data-testid={containerTid}
        style={{
          border: '1px solid #ddd',
          borderRadius: 4,
          padding: 8,
          marginTop: 4,
          background: 'var(--color-bg-soft, #fafafa)',
        }}
      >
        {nestedSpecs.map((sub) => (
          <SingleField
            key={sub.key}
            stage={stage}
            fieldKey={`${fieldKey}-${sub.key}`}
            fieldLabel={`${sub.label}（${fieldKey}.${sub.key}）`}
            kind={sub.kind === 'lines' ? 'lines' : sub.kind === 'number' ? 'number' : sub.kind === 'textarea' ? 'textarea' : 'text'}
            value={obj[sub.key]}
            onChange={(v) => updateKey(sub.key, v)}
            rows={sub.rows}
            required={sub.required}
            testIdSuffix={`${fieldKey}-${sub.key}`}
            errorText={getChildErrorText?.(sub.key) ?? null}
            customTestId={useCustomChildPrefix ? `${childPrefix}-${sub.key}` : undefined}
          />
        ))}
        {/* 一层未知键展开 */}
        {Object.keys(obj)
          .filter((k) => !whitelistKeys.has(k))
          .map((k) => (
            <UnknownKeyField
              key={k}
              stage={stage}
              fieldKey={`${fieldKey}.${k}`}
              value={obj[k]}
              onChange={(v) => updateKey(k, v)}
              onJsonValidityChange={onJsonValidityChange}
              customTestId={
                useCustomChildPrefix ? `${unknownPrefix}-${k}` : undefined
              }
            />
          ))}
      </div>
    </FieldWrap>
  );
}

// =============== 数组 of string =================================================

function StringArrayField({
  stage,
  fieldKey,
  fieldLabel,
  value,
  onChange,
  rows,
}: {
  stage: string;
  fieldKey: string;
  fieldLabel: string;
  value: unknown;
  onChange: (next: unknown) => void;
  rows?: number;
}) {
  return (
    <SingleField
      stage={stage}
      fieldKey={fieldKey}
      fieldLabel={fieldLabel}
      kind="lines"
      rows={Math.max(rows ?? 5, 5)}
      value={value}
      onChange={onChange}
      testIdSuffix={fieldKey}
    />
  );
}

// =============== 数组 of object（卡片列表）======================================

function ObjectListField({
  stage,
  fieldKey,
  fieldLabel,
  itemFields,
  value,
  onChange,
  onJsonValidityChange,
  getItemErrorText,
}: {
  stage: string;
  fieldKey: string;
  fieldLabel: string;
  itemFields: FieldSpec[];
  value: unknown;
  onChange: (next: unknown) => void;
  onJsonValidityChange?: JsonValidityChange;
  /** 数组元素子字段错误查询（按 idx + subKey 查错误文本） */
  getItemErrorText?: (idx: number, subKey: string) => string | null;
}) {
  const arr = Array.isArray(value)
    ? (value as unknown[]).map((it) => (isPlainObject(it) ? (it as Record<string, unknown>) : {}))
    : [];

  const updateAt = (idx: number, next: Record<string, unknown>) => {
    const copy = arr.slice();
    copy[idx] = next;
    onChange(copy);
  };

  const removeAt = (idx: number) => {
    const copy = arr.slice();
    copy.splice(idx, 1);
    onChange(copy);
  };

  const addOne = () => {
    const copy = arr.slice();
    copy.push(emptyForItem(itemFields));
    onChange(copy);
  };

  const whitelistKeys = new Set(itemFields.map((f) => f.key));

  return (
    <FieldWrap label={fieldLabel}>
      {arr.length === 0 ? (
        <div className="muted small" style={{ marginTop: 4 }}>
          （暂无，可点击下方按钮添加）
        </div>
      ) : null}
      {arr.map((item, idx) => {
        const cardKey = `${stage}-${fieldKey}-${idx}`;
        return (
          <div
            key={idx}
            data-testid={`revision-card-${cardKey}`}
            className="card"
            style={{ marginTop: 6, padding: 8, background: '#fff' }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <span className="muted small">#{idx + 1}</span>
              <div style={{ flex: 1 }} />
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => removeAt(idx)}
                data-testid={`revision-card-remove-${cardKey}`}
              >
                删除
              </button>
            </div>
            {itemFields.map((sub) =>
              sub.kind === 'nested-object' ? (
                <NestedObjectField
                  key={sub.key}
                  stage={stage}
                  fieldKey={`${fieldKey}-${idx}-${sub.key}`}
                  fieldLabel={sub.label}
                  spec={sub}
                  value={item[sub.key]}
                  onChange={(v) => updateAt(idx, { ...item, [sub.key]: v })}
                  onJsonValidityChange={onJsonValidityChange}
                />
              ) : (
                <SingleField
                  key={sub.key}
                  stage={stage}
                  fieldKey={`${fieldKey}-${idx}-${sub.key}`}
                  fieldLabel={sub.label}
                  kind={
                    sub.kind === 'lines'
                      ? 'lines'
                      : sub.kind === 'number'
                        ? 'number'
                        : sub.kind === 'textarea'
                          ? 'textarea'
                          : 'text'
                  }
                  rows={sub.rows}
                  value={item[sub.key]}
                  onChange={(v) => updateAt(idx, { ...item, [sub.key]: v })}
                  required={sub.required}
                  testIdSuffix={`${fieldKey}-${idx}-${sub.key}`}
                  errorText={getItemErrorText?.(idx, sub.key) ?? null}
                />
              ),
            )}
            {Object.keys(item)
              .filter((k) => !whitelistKeys.has(k))
              .map((k) => (
                <UnknownKeyField
                  key={k}
                  stage={stage}
                  fieldKey={`${fieldKey}-${idx}.${k}`}
                  value={item[k]}
                  onChange={(v) => updateAt(idx, { ...item, [k]: v })}
                  onJsonValidityChange={onJsonValidityChange}
                />
              ))}
          </div>
        );
      })}
      <div style={{ marginTop: 6 }}>
        <button
          type="button"
          className="btn btn--sm"
          onClick={addOne}
          data-testid={`revision-card-add-${stage}-${fieldKey}`}
        >
          + 添加一条
        </button>
      </div>
    </FieldWrap>
  );
}

// =============== 顶层 Field 调度 ================================================

interface FieldEntryProps {
  stage: string;
  spec: FieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
  errorMessage: string | null;
  onJsonValidityChange?: JsonValidityChange;
}

function FieldEntry({
  stage,
  spec,
  value,
  onChange,
  errorMessage,
  onJsonValidityChange,
}: FieldEntryProps) {
  const { key: k, label, kind, rows } = spec;
  const fieldLabel = label;

  // 类型不符预期：值存在但类型不对应（数组字段非数组 / object 字段非 object）→ 回退 JSON
  if (kind === 'object-list') {
    if (value !== undefined && value !== null && !Array.isArray(value)) {
      return (
        <JsonFallbackField
          stage={stage}
          fieldKey={k}
          label={fieldLabel}
          value={value}
          onChange={onChange}
          errorMessage={errorMessage}
          required={spec.required}
          onJsonValidityChange={onJsonValidityChange}
          domainKey={`${stage}.${k}`}
        />
      );
    }
    return (
      <ObjectListField
        stage={stage}
        fieldKey={k}
        fieldLabel={fieldLabel}
        itemFields={spec.itemFields ?? []}
        value={value}
        onChange={onChange}
        onJsonValidityChange={onJsonValidityChange}
      />
    );
  }

  if (kind === 'nested-object') {
    if (value !== undefined && value !== null && !isPlainObject(value)) {
      return (
        <JsonFallbackField
          stage={stage}
          fieldKey={k}
          label={fieldLabel}
          value={value}
          onChange={onChange}
          errorMessage={errorMessage}
          required={spec.required}
          onJsonValidityChange={onJsonValidityChange}
          domainKey={`${stage}.${k}`}
        />
      );
    }
    return (
      <NestedObjectField
        stage={stage}
        fieldKey={k}
        fieldLabel={fieldLabel}
        spec={spec}
        value={value}
        onChange={onChange}
        onJsonValidityChange={onJsonValidityChange}
        getChildErrorText={(subKey) => {
          // 调用方当前未提供嵌套字段错误；预留接口
          void subKey;
          return null;
        }}
      />
    );
  }

  if (kind === 'lines') {
    if (value !== undefined && value !== null && !Array.isArray(value) && typeof value !== 'string') {
      return (
        <JsonFallbackField
          stage={stage}
          fieldKey={k}
          label={fieldLabel}
          value={value}
          onChange={onChange}
          errorMessage={errorMessage}
          required={spec.required}
          onJsonValidityChange={onJsonValidityChange}
          domainKey={`${stage}.${k}`}
        />
      );
    }
    return (
      <StringArrayField
        stage={stage}
        fieldKey={k}
        fieldLabel={fieldLabel}
        value={value}
        onChange={onChange}
        rows={rows}
      />
    );
  }

  // 简单字段：类型校验
  if (kind === 'number') {
    if (
      value !== undefined &&
      value !== null &&
      typeof value !== 'number' &&
      typeof value !== 'string'
    ) {
      return (
        <JsonFallbackField
          stage={stage}
          fieldKey={k}
          label={fieldLabel}
          value={value}
          onChange={onChange}
          errorMessage={errorMessage}
          required={spec.required}
          onJsonValidityChange={onJsonValidityChange}
          domainKey={`${stage}.${k}`}
        />
      );
    }
  }
  if (kind === 'textarea' || kind === 'text') {
    if (
      value !== undefined &&
      value !== null &&
      typeof value !== 'string' &&
      typeof value !== 'number'
    ) {
      return (
        <JsonFallbackField
          stage={stage}
          fieldKey={k}
          label={fieldLabel}
          value={value}
          onChange={onChange}
          errorMessage={errorMessage}
          required={spec.required}
          onJsonValidityChange={onJsonValidityChange}
          domainKey={`${stage}.${k}`}
        />
      );
    }
  }

  return (
    <SingleField
      stage={stage}
      fieldKey={k}
      fieldLabel={fieldLabel}
      kind={kind ?? 'text'}
      rows={rows}
      value={value}
      onChange={onChange}
      required={spec.required}
      testIdSuffix={spec.testId ?? k}
      errorText={errorMessage}
    />
  );
}

// =============== StageEditor：组合白名单 + 未知键 ===============================

export interface StageEditorProps {
  stage: string;
  draft: Record<string, unknown>;
  /** 必填字段的错误（按 stage.key） */
  requiredErrors?: Record<string, string>;
  busy?: boolean;
  onChange: (next: Record<string, unknown>) => void;
  /** JSON 域非法状态回调（注册/注销） */
  onJsonValidityChange?: JsonValidityChange;
}

/**
 * 一个 stage 的完整编辑器：白名单字段按专属控件渲染；其余键按未知键规则。
 * 顶层字段类型不符预期（如 characters 不是数组）→ 该字段整体回退 JSON 域。
 *
 * 必填校验由调用方在 onChange 触发的 compose 流程里同步判定，本组件只负责渲染。
 */
export function StageEditor({
  stage,
  draft,
  requiredErrors,
  onChange,
  onJsonValidityChange,
}: StageEditorProps) {
  const specs = STAGE_FIELDS[stage] ?? [];
  const whitelistKeys = useMemo(() => new Set(specs.map((s) => s.key)), [specs]);

  const updateTopKey = (k: string, v: unknown) => {
    onChange({ ...draft, [k]: v });
  };

  return (
    <>
      {specs.map((spec) => (
        <FieldEntry
          key={spec.key}
          stage={stage}
          spec={spec}
          value={draft[spec.key]}
          onChange={(v) => updateTopKey(spec.key, v)}
          errorMessage={requiredErrors?.[spec.key] ?? null}
          onJsonValidityChange={onJsonValidityChange}
        />
      ))}
      {/* 未知顶层键原样展开 */}
      {Object.keys(draft)
        .filter((k) => !whitelistKeys.has(k))
        .filter((k) => !['_degraded', 'error'].includes(k)) // 元信息不在编辑区
        .map((k) => (
          <UnknownKeyField
            key={k}
            stage={stage}
            fieldKey={k}
            value={draft[k]}
            onChange={(v) => updateTopKey(k, v)}
            onJsonValidityChange={onJsonValidityChange}
          />
        ))}
    </>
  );
}

// =============== 校验 + 组合（任 stage 通用）===================================

export interface ComposeResult {
  ok: boolean;
  parsed: Record<string, unknown> | null;
  errors: Record<string, string>;
}

/**
 * 按 stage 白名单做必填校验；当前未做结构性 JSON 校验（白名单控件已结构化，
 * 未知键 / 兜底 JSON 域由各自 parse 错误触发 disabled 状态，本函数只聚合必填错误）。
 */
export function composeAndValidate(
  stage: string,
  draft: Record<string, unknown>,
): ComposeResult {
  const errors: Record<string, string> = {};
  const specs = STAGE_FIELDS[stage] ?? [];

  const checkRequired = (spec: FieldSpec, val: unknown) => {
    if (!spec.required) return;
    if (spec.kind === 'number') {
      if (typeof val !== 'number' || !Number.isFinite(val)) {
        errors[spec.key] = `${spec.label}必填（数字）`;
      }
      return;
    }
    if (spec.kind === 'lines') {
      const arr = Array.isArray(val) ? val : [];
      if (arr.length === 0) errors[spec.key] = `${spec.label}必填（至少一条）`;
      return;
    }
    if (typeof val !== 'string' || val.trim().length === 0) {
      errors[spec.key] = `${spec.label}必填`;
    }
  };

  for (const spec of specs) {
    const v = draft[spec.key];
    if (spec.kind === 'nested-object') {
      if (!isPlainObject(v)) {
        if (spec.required) errors[spec.key] = `${spec.label}必须是对象`;
        continue;
      }
      const obj = v as Record<string, unknown>;
      for (const sub of spec.nested ?? []) {
        checkRequired(sub, obj[sub.key]);
        if (errors[`${spec.key}.${sub.key}`]) {
          errors[spec.key] = `${spec.label} 子字段非法`;
        }
      }
      continue;
    }
    if (spec.kind === 'object-list') {
      // 数组卡片：当前实现不强制非空，但若值是数组，每元素必须是对象
      if (v !== undefined && v !== null && !Array.isArray(v)) {
        errors[spec.key] = `${spec.label}必须是数组`;
        continue;
      }
      continue;
    }
    checkRequired(spec, v);
  }

  return {
    ok: Object.keys(errors).length === 0,
    parsed: draft,
    errors,
  };
}

// =============== 4 个 stage 的便捷包装（命名兼容 + logline 必填）===============

export interface PremiseEditorProps {
  premise: Record<string, unknown>;
  setPremise: Dispatch<SetStateAction<Record<string, unknown>>>;
  errors?: Record<string, string>;
  busy?: boolean;
  onJsonValidityChange?: JsonValidityChange;
}

/**
 * PremiseEditor —— premise 关专用包装。
 * - logline 必填：使用专属 testid revision-logline / revision-positioning /
 *   revision-selling-points / revision-protagonist / revision-title / revision-genre
 *   （与改造前 testid 对齐，无 stage 前缀；protagonist 嵌套字段 testid = revision-protagonist-{subKey}）。
 * - 内部独立渲染 premise 白名单（不走 StageEditor），其他 stage 仍走 StageEditor 统一渲染。
 * - protagonist 嵌套对象用通用 NestedObjectField 展开：白名单子键保留中文 label，
 *   未知键（如 AI 生成的 core_desire / distinctive_trait 等）按未知键规则渲染，键名做 label。
 */
export function PremiseEditor(props: PremiseEditorProps) {
  const { premise, setPremise, errors = {}, onJsonValidityChange } = props;
  const updateKey = (k: string, v: unknown) => {
    setPremise((prev) => ({ ...prev, [k]: v }));
  };

  const loglineError =
    typeof premise.logline !== 'string' ||
    (premise.logline as string).trim().length === 0
      ? '一句话简介必填'
      : (errors.logline ?? null);

  return (
    <div data-testid="revision-premise-editor">
      {/* title */}
      <FieldWrap label="题材" hint={undefined}>
        <input
          className="input"
          value={typeof premise.title === 'string' ? (premise.title as string) : ''}
          onChange={(e) => updateKey('title', e.target.value)}
          data-testid="revision-title"
          style={{ width: '100%' }}
        />
      </FieldWrap>
      {/* genre */}
      <FieldWrap label="类型">
        <input
          className="input"
          value={typeof premise.genre === 'string' ? (premise.genre as string) : ''}
          onChange={(e) => updateKey('genre', e.target.value)}
          data-testid="revision-genre"
          style={{ width: '100%' }}
        />
      </FieldWrap>
      {/* logline (textarea) */}
      <FieldWrap label="一句话简介（必填）" errorText={loglineError}>
        <textarea
          className="input"
          rows={5}
          value={typeof premise.logline === 'string' ? (premise.logline as string) : ''}
          onChange={(e) => updateKey('logline', e.target.value)}
          data-testid="revision-logline"
          style={{ width: '100%', resize: 'vertical' }}
        />
      </FieldWrap>
      {/* positioning */}
      <FieldWrap label="定位">
        <textarea
          className="input"
          rows={5}
          value={
            typeof premise.positioning === 'string' ? (premise.positioning as string) : ''
          }
          onChange={(e) => updateKey('positioning', e.target.value)}
          data-testid="revision-positioning"
          style={{ width: '100%', resize: 'vertical' }}
        />
      </FieldWrap>
      {/* selling_points (lines) */}
      <FieldWrap label="卖点（每行一条）">
        <textarea
          className="input"
          rows={6}
          value={
            Array.isArray(premise.selling_points)
              ? (premise.selling_points as unknown[])
                  .filter((x): x is string => typeof x === 'string')
                  .join('\n')
              : ''
          }
          onChange={(e) => {
            const lines = e.target.value
              .split('\n')
              .map((s) => s.trim())
              .filter((s) => s.length > 0);
            updateKey('selling_points', lines);
          }}
          data-testid="revision-selling-points"
          style={{ width: '100%', resize: 'vertical' }}
        />
      </FieldWrap>
      {/* protagonist (nested-object 展开一层，白名单 + 未知键 fallback) */}
      <NestedObjectField
        stage="premise"
        fieldKey="protagonist"
        fieldLabel="主角"
        spec={{
          key: 'protagonist',
          label: '主角',
          kind: 'nested-object',
          nested: [
            { key: 'name', label: '姓名', kind: 'text' },
            { key: 'age', label: '年龄', kind: 'number' },
            { key: 'gender', label: '性别', kind: 'text' },
            { key: 'background', label: '背景', kind: 'textarea', rows: 3 },
            { key: 'motivation', label: '动机', kind: 'textarea', rows: 3 },
            { key: 'goal', label: '目标', kind: 'textarea', rows: 3 },
            { key: 'conflict', label: '核心冲突', kind: 'textarea', rows: 3 },
          ],
        }}
        value={premise.protagonist}
        onChange={(v) => updateKey('protagonist', v)}
        onJsonValidityChange={onJsonValidityChange}
        containerTestId="revision-protagonist"
        childTestIdPrefix="revision-protagonist"
        childUnknownTestIdPrefix="revision-protagonist-unknown"
      />
      {/* 未知顶层键原样展开（_degraded / error 不出现） */}
      {Object.keys(premise)
        .filter((k) => !PREMISE_FIELDS.some((f) => f.key === k))
        .filter((k) => !['_degraded', 'error'].includes(k))
        .map((k) => (
          <UnknownKeyField
            key={k}
            stage="premise"
            fieldKey={k}
            value={premise[k]}
            onChange={(v) => updateKey(k, v)}
            onJsonValidityChange={onJsonValidityChange}
          />
        ))}
    </div>
  );
}

export function WorldEditor({
  draft,
  onChange,
  errors,
  onJsonValidityChange,
}: {
  draft: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  errors?: Record<string, string>;
  onJsonValidityChange?: JsonValidityChange;
}) {
  return (
    <div data-testid="revision-world-editor">
      <StageEditor
        stage="world"
        draft={draft}
        requiredErrors={errors}
        onChange={onChange}
        onJsonValidityChange={onJsonValidityChange}
      />
    </div>
  );
}

export function CharacterEditor({
  draft,
  onChange,
  errors,
  onJsonValidityChange,
}: {
  draft: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  errors?: Record<string, string>;
  onJsonValidityChange?: JsonValidityChange;
}) {
  return (
    <div data-testid="revision-character-editor">
      <StageEditor
        stage="character"
        draft={draft}
        requiredErrors={errors}
        onChange={onChange}
        onJsonValidityChange={onJsonValidityChange}
      />
    </div>
  );
}

export function OutlineEditor({
  draft,
  onChange,
  errors,
  onJsonValidityChange,
}: {
  draft: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  errors?: Record<string, string>;
  onJsonValidityChange?: JsonValidityChange;
}) {
  return (
    <div data-testid="revision-outline-editor">
      <StageEditor
        stage="outline"
        draft={draft}
        requiredErrors={errors}
        onChange={onChange}
        onJsonValidityChange={onJsonValidityChange}
      />
    </div>
  );
}

// =============== 兜底：渲染整段 JSON（极端类型错误时使用）=====================

export function FullJsonFallbackEditor({
  stage,
  draft,
  onChange,
  onJsonValidityChange,
}: {
  stage: string;
  draft: unknown;
  onChange: (next: Record<string, unknown>) => void;
  onJsonValidityChange?: JsonValidityChange;
}) {
  const obj = isPlainObject(draft) ? (draft as Record<string, unknown>) : {};
  const [text, setText] = useState<string>(() => JSON.stringify(obj, null, 2));
  const [parseErr, setParseErr] = useState<string | null>(null);
  const domainKey = `${stage}.__full_fallback__`;

  useEffect(() => {
    return () => {
      onJsonValidityChange?.(domainKey, true);
    };
  }, [onJsonValidityChange, domainKey]);

  return (
    <FieldWrap
      label={`${stage} 草稿（JSON 对象 · 兜底视图）`}
      errorText={parseErr}
    >
      <textarea
        className="input"
        rows={14}
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          const r = safeJsonParse(next);
          if (r.ok && isPlainObject(r.value)) {
            setParseErr(null);
            onJsonValidityChange?.(domainKey, true);
            onChange(r.value as Record<string, unknown>);
          } else if (r.ok) {
            setParseErr('JSON 必须是对象');
            onJsonValidityChange?.(domainKey, false);
          } else {
            setParseErr('JSON 格式错误，修正后才能放行');
            onJsonValidityChange?.(domainKey, false);
          }
        }}
        data-testid={`revision-json-${stage}`}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
      />
    </FieldWrap>
  );
}

// =============== ErrorBanner 重导出以避免上游 import 改动 =====================
export { ErrorBanner };