// ConnectionsPanel —— 模型连接管理面板（2026-10-21 批次十五「连接层」）。
//
// **为什么有这一层**（用户裁决原文）：
// 「同一厂商，想分配不同模型承担不同职责，需要要重新完全新建。」
// ⇒ 后端把「连到哪里 / 怎么认证 / 带哪些头」（连接）与「用什么模型 / 怎么调」
// （档案）拆成两层。本面板是连接那一层的 UI：**建一条连接 ⇒ 在它上面点「+ 模型」
// ⇒ 只填模型名就建出档案**；多个档案挂在同一条连接上 ⇒ 改一次 key / base_url /
// headers，三条档案同时生效。
//
// 设计要点（与 ModelProfilesPanel 同构）：
// - `useApiCall` 拉列表；`.kv-list` 行；行内一行内联展示 + 一排按钮。
// - 「+ 模型」是**主按钮**（直击痛点）；它开的弹窗**只填模型名**（档案名默认
//   「<连接名> / <模型名>」），提交体带 `connection_id` + `provider`（取自连接）。
// - 密钥输入框的 value **恒为 ''**（后端回的是 "***"，不回填明文）；
//   「清除已存密钥」走独立的 `clearKeyRequested` 标志位（照档案表单的写法）。
// - 自定义请求头用**键值对编辑器**：敏感头的值后端回 "***" ⇒ 编辑时**保留原值**
//   （提交时把 "***" 原样带上，后端按「逐键保留」语义处理）。
// - 删除被档案引用的连接 → 后端 409（`detail.referenced_by`）⇒ 如实展示引用者，
//   不静默失败。

import { useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import { connectionsApi, modelProfilesApi } from '../api/endpoints';
import type {
  ConnectionCreatePayload,
  ConnectionPresetOut,
  ConnectionTestResult,
  ConnectionUpdatePayload,
  ModelConnectionOut,
  ModelProfileCreatePayload,
} from '../api/types';
import { useApiCall } from '../hooks/useApiCall';
import { ConfirmDialog } from './ConfirmDialog';
import { EmptyState } from './EmptyState';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import { Loading } from './Loading';
import { Modal } from './Modal';
import { PROVIDER_OPTIONS, PROVIDER_SHORT_LABEL } from './providerOptions';

// 掩码哨兵：后端读路径把 api_key 与 headers 里的敏感头换成这个值。
const MASK = '***';

/** 连接级 params 里允许出现的键（后端对其它键 422）。 */
type ConnectionParams = {
  api_key?: string | null;
  headers?: Record<string, string | null> | null;
  models_url?: string | null;
};

/** 从连接行读出 headers（值可能是 "***"，即「原样保留」）。 */
function readHeaders(row: ModelConnectionOut | null): Record<string, string> {
  const raw = row?.params_json?.['headers'];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'string') out[k] = v;
  }
  return out;
}

function readModelsUrl(row: ModelConnectionOut | null): string {
  const v = row?.params_json?.['models_url'];
  return typeof v === 'string' ? v : '';
}

/** 从预设里读出 headers（值可能是模板串，如 `{{session_id}}`）。 */
function readPresetHeaders(preset: ConnectionPresetOut | null): Record<string, string> {
  const raw = preset?.params?.['headers'];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'string') out[k] = v;
  }
  return out;
}

/** 从预设里读出模型列表地址。 */
function readPresetModelsUrl(preset: ConnectionPresetOut | null): string {
  const v = preset?.params?.['models_url'];
  return typeof v === 'string' ? v : '';
}

/**
 * 解析 409「连接仍被引用」的 detail。
 *
 * ⚠️ 后端抛的 `detail` 是**对象**（`{error, message, referenced_by}`），而
 * `ApiError.detail` 是字符串（client 层的 `parseError` 对非字符串 detail 走
 * `JSON.stringify`）⇒ 这里再解一次 JSON；解不开就退回空数组（只显示原始文本）。
 */
export function parseReferencedProfiles(detail: string): string[] {
  try {
    const parsed: unknown = JSON.parse(detail);
    if (parsed && typeof parsed === 'object') {
      const refs = (parsed as { referenced_by?: unknown }).referenced_by;
      if (Array.isArray(refs)) {
        return refs.filter((x): x is string => typeof x === 'string');
      }
    }
  } catch {
    // 不是 JSON → 由调用方原样展示 detail
  }
  return [];
}

export function ConnectionsPanel() {
  const list = useApiCall<ModelConnectionOut[]>(() => connectionsApi.list(), []);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ModelConnectionOut | null>(null);
  const [addModelFor, setAddModelFor] = useState<ModelConnectionOut | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const rows = list.data ?? [];

  return (
    <div className="panel" data-testid="model-connections-panel">
      <div className="panel__title">
        模型连接
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          onClick={() => setCreating(true)}
          data-testid="new-model-connection"
        >
          + 新建连接
        </button>
      </div>

      <div className="muted small" style={{ marginBottom: 6 }}>
        连接 = 连到哪里 / 怎么认证 / 带哪些头。一条连接上可以挂多个模型档案 ——
        改一次密钥或地址，下面的档案同时生效。
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      {deleteError ? (
        <div data-testid="model-connection-delete-error">
          <ErrorBanner>{deleteError}</ErrorBanner>
        </div>
      ) : null}

      {list.loading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <EmptyState
          title="还没有模型连接"
          hint="点击右上角「新建连接」创建第一条，再在它上面挂模型档案。"
        />
      ) : (
        <div className="kv-list" data-testid="model-connections-list">
          {rows.map((c) => {
            const headers = readHeaders(c);
            const modelsUrl = readModelsUrl(c);
            return (
              <div
                key={c.connection_id}
                className="kv-list__row"
                data-testid={`connection-row-${c.connection_id}`}
                style={{ flexDirection: 'column', alignItems: 'stretch' }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    flexWrap: 'wrap',
                  }}
                >
                  <span className="kv-list__title">{c.name}</span>
                  <span className="muted small">{PROVIDER_SHORT_LABEL[c.provider] ?? c.provider}</span>
                  <span
                    className="muted small"
                    data-testid={`connection-base-url-${c.connection_id}`}
                  >
                    {c.base_url ?? '（默认端点）'}
                  </span>
                  <span
                    className={`badge ${
                      c.has_api_key ? 'badge--chapter-committed' : 'badge--archived'
                    }`}
                    data-testid={`connection-key-badge-${c.connection_id}`}
                  >
                    {c.has_api_key ? '已配置密钥' : '未配置密钥'}
                  </span>
                  <span
                    className={`badge ${
                      Number(c.enabled) === 1 ? 'badge--chapter-committed' : 'badge--archived'
                    }`}
                    data-testid={`connection-enabled-badge-${c.connection_id}`}
                  >
                    {Number(c.enabled) === 1 ? '启用' : '停用'}
                  </span>
                  <span className="kv-list__meta">{c.connection_id}</span>
                  <span style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
                    <button
                      className="btn btn--sm btn--primary"
                      onClick={() => setAddModelFor(c)}
                      data-testid={`connection-add-model-${c.connection_id}`}
                      title="在这条连接上新建一个模型档案（只需填模型名）"
                    >
                      + 模型
                    </button>
                    <button
                      className="btn btn--sm"
                      onClick={() => setEditing(c)}
                      data-testid={`connection-edit-${c.connection_id}`}
                    >
                      编辑
                    </button>
                    <button
                      className="btn btn--sm"
                      onClick={async () => {
                        setTestResult(null);
                        try {
                          const r = await connectionsApi.test(c.connection_id);
                          setTestResult(r);
                        } catch (e: unknown) {
                          const msg =
                            e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                          setTestResult({
                            connection_id: c.connection_id,
                            ok: false,
                            latency_ms: 0,
                            detail: `FAIL · ${msg}`,
                            status_code: null,
                          });
                        }
                      }}
                      data-testid={`connection-test-${c.connection_id}`}
                    >
                      测试连接
                    </button>
                    <button
                      className="btn btn--sm btn--danger"
                      onClick={() => {
                        setDeleteError(null);
                        setDeleteId(c.connection_id);
                      }}
                      data-testid={`connection-delete-${c.connection_id}`}
                    >
                      删除
                    </button>
                  </span>
                </div>
                {modelsUrl || Object.keys(headers).length > 0 ? (
                  <div
                    className="muted small"
                    style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}
                  >
                    {modelsUrl ? (
                      <span data-testid={`connection-models-url-${c.connection_id}`}>
                        模型列表：{modelsUrl}
                      </span>
                    ) : null}
                    {Object.keys(headers).length > 0 ? (
                      <span data-testid={`connection-headers-${c.connection_id}`}>
                        自定义头：{Object.keys(headers).join('、')}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {testResult ? (
        <div
          className={testResult.ok ? 'alert alert--info' : 'alert alert--error'}
          role={testResult.ok ? 'status' : 'alert'}
        >
          <code data-testid="connection-test-result">
            {testResult.connection_id}：
            <span
              className={
                testResult.ok ? 'badge badge--chapter-committed' : 'badge badge--chapter-failed'
              }
              style={{ marginRight: 6 }}
            >
              {testResult.ok ? '成功' : '失败'}
            </span>
            {testResult.latency_ms}ms
            {testResult.status_code != null ? ` · HTTP ${testResult.status_code}` : ''}
            {testResult.probe ? ` · 探测 ${testResult.probe}` : ''}
            {testResult.detail ? ` · ${testResult.detail}` : ''}
          </code>
        </div>
      ) : null}

      {notice ? (
        <div
          className="alert alert--info"
          role="status"
          aria-live="polite"
          data-testid="model-connections-notice"
          style={{ marginTop: 8 }}
        >
          {notice}
        </div>
      ) : null}

      {creating ? (
        <ConnectionFormModal
          title="新建模型连接"
          onCancel={() => setCreating(false)}
          onSubmit={async (payload: ConnectionCreatePayload) => {
            await connectionsApi.create(payload);
            setCreating(false);
            void list.reload();
            setNotice('已保存');
          }}
        />
      ) : null}

      {editing ? (
        <ConnectionFormModal
          title={`编辑：${editing.name || editing.connection_id}`}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={async (payload: ConnectionUpdatePayload) => {
            await connectionsApi.update(editing.connection_id, payload);
            setEditing(null);
            void list.reload();
            setNotice('已保存');
          }}
        />
      ) : null}

      {addModelFor ? (
        <AddModelFromConnectionModal
          connection={addModelFor}
          onCancel={() => setAddModelFor(null)}
          onSubmit={async (payload: ModelProfileCreatePayload) => {
            await modelProfilesApi.create(payload);
            setAddModelFor(null);
            setNotice('已保存');
          }}
        />
      ) : null}

      {deleteId ? (
        <ConfirmDialog
          open={true}
          title="删除模型连接"
          body={`确认删除连接 ${deleteId}？被档案引用的连接无法删除（需先解绑）。`}
          confirmText="删除"
          danger
          testId="model-connection-delete-confirm"
          onCancel={() => setDeleteId(null)}
          onConfirm={async () => {
            const id = deleteId;
            setDeleteId(null);
            setDeleteError(null);
            try {
              await connectionsApi.remove(id);
              void list.reload();
              setNotice('已删除');
            } catch (e: unknown) {
              // 409 = 连接仍被档案引用 ⇒ 如实列出引用者（后端不做静默解绑）。
              if (e instanceof ApiError && e.status === 409) {
                const refs = parseReferencedProfiles(e.detail);
                setDeleteError(
                  refs.length > 0
                    ? `连接仍被 ${refs.length} 个档案引用，请先在「模型档案」里解除关联：${refs.join('、')}`
                    : `删除失败：${e.detail}`,
                );
              } else {
                const msg =
                  e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                setDeleteError(`删除失败 · ${msg}`);
              }
            }
          }}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// ConnectionFormModal —— 连接表单（新建 / 编辑共用）
// =============================================================================

interface ConnectionFormModalProps {
  title: string;
  initial?: ModelConnectionOut;
  onCancel: () => void;
  onSubmit:
    | ((payload: ConnectionCreatePayload) => Promise<void>)
    | ((payload: ConnectionUpdatePayload) => Promise<void>);
}

interface HeaderRow {
  key: string;
  value: string;
}

function ConnectionFormModal({
  title,
  initial,
  onCancel,
  onSubmit,
}: ConnectionFormModalProps) {
  const [name, setName] = useState<string>(initial?.name ?? '');
  const [provider, setProvider] = useState<string>(
    initial?.provider ?? PROVIDER_OPTIONS[0]!.value,
  );
  const [baseUrl, setBaseUrl] = useState<string>(initial?.base_url ?? '');
  // ⚠️ 密钥输入框的 value **恒为 ''**：后端回的是 "***"，不回填明文。
  const [apiKey, setApiKey] = useState<string>('');
  const [clearKeyRequested, setClearKeyRequested] = useState(false);
  const [modelsUrl, setModelsUrl] = useState<string>(readModelsUrl(initial ?? null));
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(() =>
    Object.entries(readHeaders(initial ?? null)).map(([key, value]) => ({ key, value })),
  );
  const [enabled, setEnabled] = useState<boolean>(
    initial ? Number(initial.enabled) === 1 : true,
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // 厂商预设（一键填表）
  //
  // 权威清单在后端（packages/core/model_router/presets.py）⇒ 前端只渲染、
  // 不硬编码任何 URL。选中预设 ⇒ 自动填 provider / base_url / headers /
  // models_url，**用户随后仍可逐项改**（预设不锁字段）。
  //
  // ⚠️ 预设**不含 api_key** ⇒ 密钥框绝不被预设覆盖。
  // ⚠️ 「自动填的」与「用户手填的」分开记账：选「无预设 / 自定义」时**只**清掉
  // 自动填的那部分，不动用户自己写的内容（尤其密钥）。
  // ---------------------------------------------------------------------------
  const presets = useApiCall<ConnectionPresetOut[]>(
    () => connectionsApi.presets(),
    [],
  );
  const [presetId, setPresetId] = useState<string>('');
  // 哪些内容是「预设自动填的」（选「无预设」时只清这些）。
  const [autoBaseUrl, setAutoBaseUrl] = useState(false);
  const [autoModelsUrl, setAutoModelsUrl] = useState(false);
  const [autoHeaderKeys, setAutoHeaderKeys] = useState<string[]>([]);

  const selectedPreset = useMemo(
    () => (presets.data ?? []).find((p) => p.preset_id === presetId) ?? null,
    [presets.data, presetId],
  );

  /** 清掉上一次预设自动填的内容（不动用户手填的）。 */
  const clearAutoFilled = () => {
    if (autoBaseUrl) setBaseUrl('');
    if (autoModelsUrl) setModelsUrl('');
    if (autoHeaderKeys.length > 0) {
      setHeaderRows((rows) =>
        rows.filter((r) => !autoHeaderKeys.includes(r.key.trim())),
      );
    }
    setAutoBaseUrl(false);
    setAutoModelsUrl(false);
    setAutoHeaderKeys([]);
  };

  const handlePresetChange = (nextId: string) => {
    setPresetId(nextId);
    // 先撤掉上一个预设填的东西（换成新预设时不该留着旧预设的地址 / 头）。
    clearAutoFilled();
    if (!nextId) return;
    const preset = (presets.data ?? []).find((p) => p.preset_id === nextId);
    if (!preset) return;

    setProvider(preset.provider);

    const presetBaseUrl = preset.base_url ?? '';
    if (presetBaseUrl) {
      setBaseUrl(presetBaseUrl);
      setAutoBaseUrl(true);
    }

    const presetModelsUrl = readPresetModelsUrl(preset);
    if (presetModelsUrl) {
      setModelsUrl(presetModelsUrl);
      setAutoModelsUrl(true);
    }

    // 头：**以预设的 headers 为基底**，用户手填的其它头保留（同名以预设为准）。
    const presetHeaders = readPresetHeaders(preset);
    if (Object.keys(presetHeaders).length > 0) {
      setHeaderRows((rows) => {
        const merged: Record<string, string> = { ...presetHeaders };
        for (const r of rows) {
          const k = r.key.trim();
          if (!k || k in merged) continue;
          merged[k] = r.value;
        }
        return Object.entries(merged).map(([key, value]) => ({ key, value }));
      });
      setAutoHeaderKeys(Object.keys(presetHeaders));
    }
  };

  // 编辑时的「原有请求头键名」快照：被用户删掉的键要显式发 null（后端逐键删除语义）。
  const initialHeaderKeys = useMemo(
    () => Object.keys(readHeaders(initial ?? null)),
    [initial],
  );
  const initialModelsUrl = useMemo(() => readModelsUrl(initial ?? null), [initial]);

  const hasApiKey = !!initial?.has_api_key;
  const canShowClearKeyButton = !!initial && hasApiKey;
  const apiKeyPlaceholder = hasApiKey
    ? clearKeyRequested
      ? '清除已请求（点击保存生效）'
      : '已配置（留空则不修改）'
    : '（可选；留空则运行时从环境变量读取）';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('名称不能为空');
      return;
    }
    const trimmedUrl = baseUrl.trim();
    // anthropic / ollama / mock 可以不填地址；其余 provider 没有默认端点 ⇒ 必填。
    if (
      provider !== 'anthropic' &&
      provider !== 'ollama' &&
      provider !== 'mock' &&
      !trimmedUrl
    ) {
      setErr('该接口类型需要填写接口地址');
      return;
    }

    // ---- params 组装（PATCH 语义：缺键 = 保留原值；显式 null = 删除该键） ----
    const params: ConnectionParams = {};

    // api_key："" = 清除；"***" = 不改；缺键 = 不改。
    if (clearKeyRequested) {
      params.api_key = '';
    } else if (apiKey.trim() !== '') {
      params.api_key = apiKey.trim();
    }

    // headers（逐键）：保留 / 修改的行按原值提交（"***" 由后端判为「不改」）；
    // 被删掉的原有键显式发 null（删除该键）。
    const headerPayload: Record<string, string | null> = {};
    for (const row of headerRows) {
      const k = row.key.trim();
      if (!k) continue;
      headerPayload[k] = row.value;
    }
    for (const k of initialHeaderKeys) {
      if (!(k in headerPayload)) headerPayload[k] = null;
    }
    if (Object.keys(headerPayload).length > 0) {
      params.headers = headerPayload;
    }

    // models_url：留空 = 用默认 `{base_url}/models`；编辑时清空 ⇒ 显式 null 删除。
    const trimmedModelsUrl = modelsUrl.trim();
    if (trimmedModelsUrl) {
      params.models_url = trimmedModelsUrl;
    } else if (initialModelsUrl) {
      params.models_url = null;
    }

    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        provider,
        base_url: trimmedUrl || null,
        params,
        enabled: enabled ? 1 : 0,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onCancel}
      width={560}
      testId="model-connection-form-modal"
      closable={!submitting}
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>

        {/* ---- 厂商预设：一键填表（provider / 地址 / 头 / 模型列表地址） ---- */}
        <ErrorBanner>{presets.error}</ErrorBanner>
        <div className="form-row">
          <label>厂商预设</label>
          <select
            value={presetId}
            onChange={(e) => handlePresetChange(e.target.value)}
            data-testid="connection-preset"
          >
            <option value="">无预设 / 自定义</option>
            {(presets.data ?? []).map((p) => (
              <option key={p.preset_id} value={p.preset_id}>
                {p.label}
              </option>
            ))}
          </select>
          {selectedPreset?.note ? (
            <div data-testid="connection-preset-note">
              <InfoBanner>{selectedPreset.note}</InfoBanner>
            </div>
          ) : null}
        </div>

        <div className="form-row">
          <label>名称 *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如 opencode / 主力厂商"
            data-testid="connection-name"
          />
        </div>

        <div className="form-row">
          <label>接口类型 *</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            data-testid="connection-provider"
          >
            {PROVIDER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="form-row">
          <label>接口地址</label>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://opencode.ai/inference/go/v1"
            data-testid="connection-base-url"
          />
          <div className="muted small">
            Anthropic / Ollama / Mock 可留空（用服务端默认端点）；其余类型必填。
          </div>
        </div>

        <div className="form-row">
          <label>API 密钥</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => {
                setClearKeyRequested(false);
                setApiKey(e.target.value);
              }}
              placeholder={apiKeyPlaceholder}
              data-testid="connection-api-key"
              style={{ flex: 1 }}
            />
            {canShowClearKeyButton ? (
              <button
                type="button"
                className="btn btn--sm btn--danger"
                onClick={() => {
                  setClearKeyRequested(true);
                  setApiKey('');
                }}
                disabled={clearKeyRequested}
                data-testid="connection-clear-api-key"
                title="点击后保存将清空已存的密钥"
              >
                {clearKeyRequested ? '已请求清除' : '清除已存密钥'}
              </button>
            ) : null}
          </div>
          {initial ? (
            <div className="muted small" data-testid="connection-api-key-hint">
              {hasApiKey
                ? clearKeyRequested
                  ? '清除已请求：点击保存将清空已存的密钥；如想保留请改填新值。'
                  : '已配置密钥。留空保存将保留原值；填入新值则覆盖；点右侧按钮可显式清除。'
                : '当前未配置密钥。留空保存保持未配置。'}
            </div>
          ) : null}
        </div>

        <div className="form-row">
          <label>自定义请求头</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {headerRows.map((row, idx) => (
              <div
                key={idx}
                style={{ display: 'flex', gap: 6, alignItems: 'center' }}
              >
                <input
                  value={row.key}
                  onChange={(e) =>
                    setHeaderRows((rows) =>
                      rows.map((r, i) => (i === idx ? { ...r, key: e.target.value } : r)),
                    )
                  }
                  placeholder="头名，如 x-opencode-session"
                  data-testid={`connection-header-key-${idx}`}
                  style={{ flex: 1 }}
                />
                <input
                  value={row.value}
                  onChange={(e) =>
                    setHeaderRows((rows) =>
                      rows.map((r, i) => (i === idx ? { ...r, value: e.target.value } : r)),
                    )
                  }
                  placeholder="值，可用 {{run_id}} / {{session_id}}"
                  data-testid={`connection-header-value-${idx}`}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn btn--sm btn--danger"
                  onClick={() => setHeaderRows((rows) => rows.filter((_, i) => i !== idx))}
                  data-testid={`connection-header-remove-${idx}`}
                  title="删除该请求头"
                >
                  删除
                </button>
              </div>
            ))}
            <div>
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => setHeaderRows((rows) => [...rows, { key: '', value: '' }])}
                data-testid="connection-header-add"
              >
                + 添加请求头
              </button>
            </div>
            <div className="muted small">
              值里可写占位符 {'{{run_id}}'} / {'{{session_id}}'}（仅此两个）；已保存的敏感头
              显示为 {MASK}，保持原样提交即「不修改」。
            </div>
          </div>
        </div>

        <div className="form-row">
          <label>模型列表地址</label>
          <input
            value={modelsUrl}
            onChange={(e) => setModelsUrl(e.target.value)}
            placeholder="留空 = {base_url}/models"
            data-testid="connection-models-url"
          />
        </div>

        <div className="form-row">
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{' '}
            启用（停用的连接不能被测试）
          </label>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="connection-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// =============================================================================
// AddModelFromConnectionModal —— 「+ 模型」：只填模型名
// =============================================================================

function AddModelFromConnectionModal({
  connection,
  onCancel,
  onSubmit,
}: {
  connection: ModelConnectionOut;
  onCancel: () => void;
  onSubmit: (payload: ModelProfileCreatePayload) => Promise<void>;
}) {
  const [model, setModel] = useState<string>('');
  // 档案名默认「<连接名> / <模型名>」；用户一旦手改就不再跟随。
  const [name, setName] = useState<string>('');
  const [nameTouched, setNameTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const defaultName = `${connection.name} / ${model.trim()}`.trim();
  const effectiveName = nameTouched ? name : defaultName;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!model.trim()) {
      setErr('模型名不能为空');
      return;
    }
    const finalName = (nameTouched ? name.trim() : defaultName) || defaultName;
    setSubmitting(true);
    try {
      await onSubmit({
        name: finalName,
        model: model.trim(),
        // provider 由连接决定（连接是唯一权威；不一致后端会 422）。
        provider: connection.provider,
        connection_id: connection.connection_id,
        // 连接接管地址 / 密钥 / 头 ⇒ 档案级不再带这些键。
        params: {},
        enabled: 1,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={`在「${connection.name}」上新建模型档案`}
      onClose={onCancel}
      width={480}
      testId="connection-add-model-modal"
      closable={!submitting}
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>

        <InfoBanner>
          接口地址、密钥与自定义头都来自这条连接 —— 这里只填模型名。
        </InfoBanner>

        <div className="form-row">
          <label>模型名 *</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="如 gpt-4o-mini"
            data-testid="add-model-name"
          />
        </div>

        <div className="form-row">
          <label>档案名称</label>
          <input
            value={effectiveName}
            onChange={(e) => {
              setNameTouched(true);
              setName(e.target.value);
            }}
            placeholder={defaultName}
            data-testid="add-model-profile-name"
          />
        </div>

        <div className="muted small" style={{ marginBottom: 8 }}>
          接口类型：{PROVIDER_SHORT_LABEL[connection.provider] ?? connection.provider}
          （取自连接）
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="add-model-save"
          >
            {submitting ? '创建中…' : '创建档案'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
