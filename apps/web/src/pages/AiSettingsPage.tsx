import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  agentsApi,
  capabilityBindingsApi,
  modelProfilesApi,
} from '../api/endpoints';
import type {
  Agent,
  CapabilityBinding,
  ModelProfile,
  ModelProfileCreatePayload,
  ModelProfileTestResult,
  ModelProfileUpdatePayload,
  PromptVersion,
  SyncPromptsResult,
} from '../api/types';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { useApiCall } from '../hooks/useApiCall';
import { formatDateTime } from '../utils/format';
import { ApiError } from '../api/client';

const PROVIDER_OPTIONS = [
  { value: 'openai_compatible', label: 'OpenAI 兼容（OpenAI / DeepSeek / Ollama 等）' },
  { value: 'anthropic', label: 'Anthropic（Claude 原生 Messages API）' },
  { value: 'ollama', label: 'Ollama（本地 /api/chat，无需密钥）' },
  { value: 'mock', label: 'Mock（本地兜底）' },
];

export function AiSettingsPage() {
  return (
    <div>
      <h1 className="section-title">AI 设置</h1>
      <p className="section-subtitle">
        模型档案（model_profiles）与环节绑定（capability_bindings）统一管理本页；
        Agent prompt 版本管理与本页同步。本期页面挂在项目路由下，仅展示该 scope 内可配置项；
        模型配置是项目无关的全局表，这里仍展示完整列表。
      </p>

      <InfoBanner>
        <span data-testid="ai-settings-logs-link">
          想查看每次 AI 调用的 prompt/response？前往{' '}
          <Link to="/ai-logs">AI 调用日志</Link>。
        </span>
      </InfoBanner>

      <div className="panel-grid">
        <ModelProfilesPanel />
        <CapabilityBindingsPanel />
        <AgentsPanel />
      </div>
    </div>
  );
}

// =============================================================================
// ModelProfilesPanel —— 档案列表
// =============================================================================

function ModelProfilesPanel() {
  const { pid } = useParams();
  void pid;
  const list = useApiCall<ModelProfile[]>(
    () => modelProfilesApi.list(),
    [],
  );
  const [editing, setEditing] = useState<ModelProfile | null>(null);
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<ModelProfileTestResult | null>(null);

  return (
    <div className="panel" data-testid="model-profiles-panel">
      <div className="panel__title">
        模型档案（model_profiles）
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          onClick={() => setCreating(true)}
          data-testid="new-model-profile"
        >
          + 新建档案
        </button>
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>

      {list.loading ? (
        <div className="muted">加载中…</div>
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="还没有模型档案"
          hint="点击右上角「新建档案」创建第一条。"
        />
      ) : (
        <div className="kv-list">
          {list.data.map((m) => (
            <div
              key={m.profile_id}
              className="kv-list__row"
              data-testid={`model-profile-row-${m.profile_id}`}
            >
              <span className="kv-list__title">
                {m.name} · {m.provider} · {m.model}
              </span>
              <span
                className={`badge ${
                  Number(m.enabled) === 1 ? 'badge--chapter-committed' : 'badge--archived'
                }`}
              >
                {Number(m.enabled) === 1 ? 'enabled' : 'disabled'}
              </span>
              <span className="kv-list__meta">{m.profile_id}</span>
              <span style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
                <button
                  className="btn btn--sm"
                  onClick={async () => {
                    // 读路径 params.api_key 已被后端脱敏（"***"），前端只用
                    // has_api_key 字段判断「已配置」状态，不回填明文到输入框。
                    try {
                      const detail = await modelProfilesApi
                        .list()
                        .then((rows) => rows.find((r) => r.profile_id === m.profile_id));
                      setEditing(detail ?? null);
                    } catch (e: unknown) {
                      const msg =
                        e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                      setTestResult({
                        profile_id: m.profile_id,
                        ok: false,
                        latency_ms: 0,
                        detail: `加载详情失败 · ${msg}`,
                        status_code: null,
                      });
                    }
                  }}
                  data-testid={`model-profile-edit-${m.profile_id}`}
                >
                  编辑
                </button>
                <button
                  className="btn btn--sm"
                  onClick={async () => {
                    setTestResult(null);
                    try {
                      const r = await modelProfilesApi.test(m.profile_id);
                      setTestResult(r);
                    } catch (e: unknown) {
                      const msg =
                        e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                      setTestResult({
                        profile_id: m.profile_id,
                        ok: false,
                        latency_ms: 0,
                        detail: `FAIL · ${msg}`,
                        status_code: null,
                      });
                    }
                    void list.reload();
                  }}
                  data-testid={`model-profile-test-${m.profile_id}`}
                >
                  测试连接
                </button>
                <button
                  className="btn btn--sm btn--danger"
                  onClick={async () => {
                    if (!window.confirm(`确认删除模型档案 ${m.profile_id}？`)) return;
                    try {
                      await modelProfilesApi.remove(m.profile_id);
                      void list.reload();
                    } catch (e: unknown) {
                      // 后端删除遇 409 时按 wire 契约 detail 列出 capability；
                      // 若 detail 已给出明确指引，直接透传；否则附前端兜底文案。
                      if (e instanceof ApiError && e.status === 409) {
                        setTestResult({
                          profile_id: m.profile_id,
                          ok: false,
                          latency_ms: 0,
                          detail: `${e.detail}（请先在「环节分配」中解除绑定）`,
                          status_code: e.status,
                        });
                      } else {
                        const msg =
                          e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                        setTestResult({
                          profile_id: m.profile_id,
                          ok: false,
                          latency_ms: 0,
                          detail: `删除失败 · ${msg}`,
                          status_code: null,
                        });
                      }
                    }
                  }}
                  data-testid={`model-profile-delete-${m.profile_id}`}
                >
                  删除
                </button>
              </span>
            </div>
          ))}
        </div>
      )}

      {testResult ? (
        <InfoBanner>
          <code data-testid="model-profile-test-result">
            {testResult.profile_id}：
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
            {testResult.detail ? ` · ${testResult.detail}` : ''}
          </code>
        </InfoBanner>
      ) : null}

      {creating ? (
        <ModelProfileFormModal
          title="新建模型档案"
          onCancel={() => setCreating(false)}
          onSubmit={async (payload: ModelProfileCreatePayload) => {
            await modelProfilesApi.create(payload);
            setCreating(false);
            list.reload();
          }}
        />
      ) : null}

      {editing ? (
        <ModelProfileFormModal
          title={`编辑：${editing.name || editing.profile_id}`}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={async (payload: ModelProfileUpdatePayload) => {
            await modelProfilesApi.update(editing.profile_id, payload);
            setEditing(null);
            list.reload();
          }}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// CapabilityBindingsPanel —— 7 项固定清单，PUT 即绑 / DELETE 即解绑
// =============================================================================

const BINDING_LABELS_FALLBACK: Record<string, string> = {
  premise_design: '题材定位',
  world_building: '世界观',
  character_design: '角色设计',
  volume_outline: '卷纲',
  creative_writing: '正文写作',
  reasoning: '推理规划',
  light: '轻量评审',
};

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

function CapabilityBindingsPanel() {
  const list = useApiCall<CapabilityBinding[]>(
    () => capabilityBindingsApi.list(),
    [],
  );
  // profiles 用于下拉选项：enabled=1 的档案集合
  const profiles = useApiCall<ModelProfile[]>(
    () => modelProfilesApi.list(),
    [],
  );

  // 本地缓存 profile_ids → 提交成功后立即回写，失败时回滚到 GET 状态。
  const [selectedMap, setSelectedMap] = useState<
    Record<string, string>
  >({});
  const [saveMap, setSaveMap] = useState<Record<string, SaveState>>({});

  // GET bindings 拉到数据时，把每项的 profile_ids 用 , 拼成单值（多档选一个，按下拉语义）。
  useEffect(() => {
    if (!list.data) return;
    const rows = list.data;
    setSelectedMap((prev) => {
      const next: Record<string, string> = {};
      for (const b of rows) {
        // 若本地已编辑过且不等于原值，保留本地值；否则用后端值（取第一项；后端允许数组）
        next[b.capability] = prev[b.capability] ?? b.profile_ids[0] ?? '';
      }
      return next;
    });
  }, [list.data]);

  const enabledProfiles = useMemo(
    () => (profiles.data ?? []).filter((p) => Number(p.enabled) === 1),
    [profiles.data],
  );

  const handleChange = async (
    capability: string,
    profileId: string,
  ): Promise<void> => {
    const prev = selectedMap[capability] ?? '';
    setSelectedMap((m) => ({ ...m, [capability]: profileId }));
    setSaveMap((m) => ({ ...m, [capability]: { kind: 'saving' } }));
    try {
      if (profileId === '') {
        await capabilityBindingsApi.unbind(capability);
      } else {
        await capabilityBindingsApi.bind(capability, [profileId]);
      }
      setSaveMap((m) => ({ ...m, [capability]: { kind: 'saved' } }));
      void list.reload();
    } catch (e: unknown) {
      // 失败回滚
      setSelectedMap((m) => ({ ...m, [capability]: prev }));
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setSaveMap((m) => ({ ...m, [capability]: { kind: 'error', message: msg } }));
    }
  };

  return (
    <div className="panel" data-testid="capability-bindings-panel">
      <div className="panel__title">环节分配（capability_bindings）</div>
      <div className="muted small" style={{ marginBottom: 6 }}>
        把模型档案指派到 7 个生产环节；解绑即该环节回落后端历史默认链（适用旧版 model_configs 仍存在时）。
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      <ErrorBanner>{profiles.error}</ErrorBanner>

      {list.loading ? (
        <div className="muted">加载中…</div>
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="尚未获取到环节清单"
          hint="后端 /capability-bindings 返回空或失败。"
        />
      ) : (
        <div className="kv-list" data-testid="capability-bindings-list">
          {list.data.map((b) => (
            <CapabilityBindingRow
              key={b.capability}
              binding={b}
              profiles={enabledProfiles}
              selected={selectedMap[b.capability] ?? b.profile_ids[0] ?? ''}
              state={saveMap[b.capability] ?? { kind: 'idle' }}
              onChange={(id) => void handleChange(b.capability, id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CapabilityBindingRow({
  binding,
  profiles,
  selected,
  state,
  onChange,
}: {
  binding: CapabilityBinding;
  profiles: ModelProfile[];
  selected: string;
  state: SaveState;
  onChange: (profileId: string) => void;
}) {
  const isUnbound = selected === '';
  const showLegacyHint = isUnbound && binding.legacy_available;
  return (
    <div
      className="kv-list__row"
      data-testid={`cap-binding-${binding.capability}`}
    >
      <span className="kv-list__title">
        {binding.label ||
          BINDING_LABELS_FALLBACK[binding.capability] ||
          binding.capability}
      </span>
      <span className="muted small">
        {binding.agents && binding.agents.length > 0
          ? `agents: ${binding.agents.join(', ')}`
          : ''}
      </span>
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
        <select
          value={selected}
          onChange={(e) => onChange(e.target.value)}
          disabled={state.kind === 'saving'}
          data-testid={`cap-binding-select-${binding.capability}`}
        >
          <option value="">未绑定 · 使用默认配置</option>
          {profiles.map((p: ModelProfile) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name}（{p.model}）
            </option>
          ))}
        </select>
        <SaveIndicator state={state} />
      </span>
      {showLegacyHint ? (
        <span
          className="muted small"
          style={{ display: 'block', width: '100%' }}
          data-testid={`cap-binding-legacy-hint-${binding.capability}`}
        >
          检测到旧版配置仍可用
        </span>
      ) : null}
    </div>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state.kind === 'saving')
    return (
      <span
        className="muted small"
        data-testid="cap-binding-saving"
      >
        保存中…
      </span>
    );
  if (state.kind === 'saved')
    return (
      <span
        className="muted small"
        data-testid="cap-binding-saved"
        style={{ color: 'seagreen' }}
      >
        已保存 ✓
      </span>
    );
  if (state.kind === 'error')
    return (
      <span
        className="small"
        style={{ color: 'crimson' }}
        data-testid="cap-binding-error"
      >
        失败：{state.message}
      </span>
    );
  return null;
}

// =============================================================================
// AgentsPanel —— 与上一轮不变
// =============================================================================

function AgentsPanel() {
  const list = useApiCall<Agent[]>(() => agentsApi.list(), []);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncPromptsResult | null>(null);
  const [syncErr, setSyncErr] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const selected = useMemo(
    () => list.data?.find((a) => a.name === selectedName) ?? null,
    [list.data, selectedName],
  );
  const prompts = useApiCall<PromptVersion[]>(
    async () => (selectedName ? agentsApi.listPrompts(selectedName) : Promise.resolve([])),
    [selectedName],
  );

  useEffect(() => {
    if (!selectedName && list.data && list.data.length > 0) {
      setSelectedName(list.data[0].name);
    }
  }, [list.data, selectedName]);

  return (
    <div className="panel" data-testid="agents-panel">
      <div className="panel__title">
        Agents & Prompts
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          disabled={syncing}
          data-testid="sync-prompts"
          onClick={async () => {
            setSyncErr(null);
            setSyncResult(null);
            setSyncing(true);
            try {
              const r = await agentsApi.sync();
              setSyncResult(r);
              list.reload();
              if (selectedName) prompts.reload();
            } catch (e: unknown) {
              setSyncErr(e instanceof Error ? e.message : '同步失败');
            } finally {
              setSyncing(false);
            }
          }}
        >
          {syncing ? '同步中…' : '同步 prompts'}
        </button>
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      <ErrorBanner>{syncErr}</ErrorBanner>

      {syncResult ? (
        <InfoBanner>
          同步完成：扫描 {syncResult.scanned.length}、注册{' '}
          {syncResult.registered.length}、内容更新 {syncResult.updated.length}。
        </InfoBanner>
      ) : null}

      <div className="panel__section">
        <div className="panel__section-title">已注册 agents</div>
        {list.loading ? (
          <div className="muted">加载中…</div>
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            title="还没有 agent"
            hint="点击右上角「同步 prompts」从 docs/agents/prompts 扫描。"
          />
        ) : (
          <div className="kv-list">
            {list.data.map((a) => (
              <div
                key={a.agent_id}
                className={`kv-list__row ${a.name === selectedName ? 'kv-list__row--active' : ''}`}
                onClick={() => setSelectedName(a.name)}
                data-testid={`agent-row-${a.name}`}
              >
                <span className="kv-list__title">{a.name}</span>
                <span className="muted small">{a.role}</span>
                <span className="kv-list__meta">
                  {formatDateTime(a.updated_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel__section">
        <div className="panel__section-title">
          {selected ? `${selected.name} 的 prompts` : '选择一个 agent 查看 prompts'}
        </div>
        {selected ? (
          prompts.loading ? (
            <div className="muted">加载中…</div>
          ) : !prompts.data || prompts.data.length === 0 ? (
            <div className="muted small">该 agent 没有 prompt；尝试同步 prompts。</div>
          ) : (
            <div>
              <div className="muted small" style={{ marginBottom: 6 }}>
                共 {prompts.data.length} 个版本（按 version 数字降序）
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>版本</th>
                    <th>状态</th>
                    <th>更新时间</th>
                    <th>内容预览</th>
                  </tr>
                </thead>
                <tbody>
                  {prompts.data.map((p) => (
                    <tr key={p.prompt_id} data-testid={`prompt-row-${p.version}`}>
                      <td>{p.version}</td>
                      <td>{p.status}</td>
                      <td className="muted small">{formatDateTime(p.updated_at)}</td>
                      <td>
                        <code className="small">
                          {p.content.slice(0, 80)}
                          {p.content.length > 80 ? '…' : ''}
                        </code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          <div className="muted small">从左侧列表选择一个 agent。</div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// ModelProfileFormModal —— 档案表单：去 capability 下拉、加 name 必填
// =============================================================================

interface ModelProfileFormModalProps {
  title: string;
  initial?: ModelProfile;
  onCancel: () => void;
  /** 编辑/新建由父组件传不同的 submit（create 必填 name，update 部分更新） */
  onSubmit:
    | ((payload: ModelProfileCreatePayload) => Promise<void>)
    | ((payload: ModelProfileUpdatePayload) => Promise<void>);
}

interface ParamsState {
  baseUrl: string;
  apiKey: string;
}

function ModelProfileFormModal({
  title,
  initial,
  onCancel,
  onSubmit,
}: ModelProfileFormModalProps) {
  const initialParams = (initial?.params as Record<string, unknown>) ?? {};
  const [name, setName] = useState<string>(initial?.name ?? '');
  const [provider, setProvider] = useState<string>(
    initial?.provider ?? PROVIDER_OPTIONS[0].value,
  );
  const [model, setModel] = useState<string>(initial?.model ?? '');
  // 读路径 params.api_key 已被脱敏（"***"），不回填明文到输入框。
  const [params, setParams] = useState<ParamsState>({
    baseUrl:
      typeof initialParams['base_url'] === 'string'
        ? String(initialParams['base_url'])
        : '',
    apiKey: '',
  });
  const [enabled, setEnabled] = useState<boolean>(
    initial ? Number(initial.enabled) === 1 : true,
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 编辑模式下「清除已存密钥」独立标志位：仅显式请求时才传 api_key=""。
  const [clearKeyRequested, setClearKeyRequested] = useState(false);

  const isMock = provider === 'mock';
  const isAnthropic = provider === 'anthropic';
  const isOllama = provider === 'ollama';
  const requireBaseUrl = !isMock && !isAnthropic && !isOllama;
  const baseUrlPlaceholder = isAnthropic
    ? '留空 = 官方 api.anthropic.com'
    : isOllama
      ? '留空 = 本地 http://127.0.0.1:11434'
      : 'https://api.openai.com/v1';

  const hasApiKey = !!initial?.has_api_key;
  const canShowClearKeyButton = !!initial && hasApiKey && !isMock && !isOllama;
  const apiKeyPlaceholder = isAnthropic
    ? hasApiKey
      ? clearKeyRequested
        ? '清除已请求（点击保存生效）'
        : '已配置（留空则不修改）'
      : '（可选；留空则由 resolve_api_key 从 NOVELOS_API_KEY_ANTHROPIC 解析）'
    : hasApiKey
      ? clearKeyRequested
        ? '清除已请求（点击保存生效）'
        : '已配置（留空则不修改）'
      : '（可选；若不填，运行时由 resolve_api_key 从 env 解析）';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('name 不能为空');
      return;
    }
    if (!model.trim()) {
      setErr('model 不能为空');
      return;
    }
    if (requireBaseUrl && !params.baseUrl.trim()) {
      setErr('该 provider 需要 base_url');
      return;
    }
    const paramsOut: Record<string, unknown> = {};
    if (!isMock) {
      const trimmedUrl = params.baseUrl.trim();
      if (trimmedUrl) paramsOut['base_url'] = trimmedUrl;
      if (!isOllama) {
        if (clearKeyRequested) {
          paramsOut['api_key'] = '';
        } else if (params.apiKey.trim() !== '') {
          paramsOut['api_key'] = params.apiKey.trim();
        }
      }
    }
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        provider,
        model: model.trim(),
        params: paramsOut,
        enabled: enabled ? 1 : 0,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={modalBackdrop}
      onClick={onCancel}
    >
      <form
        className="card"
        style={{ width: 520, maxWidth: '92vw' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-row">
          <label>name *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如 default-creative / fast-creative"
            data-testid="profile-name"
          />
        </div>

        <div className="form-row">
          <label>provider *</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            data-testid="profile-provider"
          >
            {PROVIDER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="form-row">
          <label>model *</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="如 gpt-4o-mini / mock-echo / deepseek-chat"
            data-testid="profile-model"
          />
        </div>

        {!isMock ? (
          <div className="form-grid">
            <div className="form-row">
              <label>base_url {requireBaseUrl ? '*' : ''}</label>
              <input
                value={params.baseUrl}
                onChange={(e) =>
                  setParams((p) => ({ ...p, baseUrl: e.target.value }))
                }
                placeholder={baseUrlPlaceholder}
                data-testid="profile-base-url"
              />
            </div>
            {isOllama ? (
              <InfoBanner>Ollama 本地服务无需 API Key —— 表单不接收 key。</InfoBanner>
            ) : (
              <div className="form-row">
                <label>api_key</label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="password"
                    value={params.apiKey}
                    onChange={(e) => {
                      setClearKeyRequested(false);
                      setParams((p) => ({ ...p, apiKey: e.target.value }));
                    }}
                    placeholder={apiKeyPlaceholder}
                    data-testid="profile-api-key"
                    style={{ flex: 1 }}
                  />
                  {canShowClearKeyButton ? (
                    <button
                      type="button"
                      className="btn btn--sm btn--danger"
                      onClick={() => {
                        setClearKeyRequested(true);
                        setParams((p) => ({ ...p, apiKey: '' }));
                      }}
                      disabled={clearKeyRequested}
                      data-testid="profile-clear-api-key"
                      title="点击后保存将清空已存的 api_key"
                    >
                      {clearKeyRequested ? '已请求清除' : '清除已存密钥'}
                    </button>
                  ) : null}
                </div>
                {initial ? (
                  <div className="muted small" data-testid="profile-api-key-hint">
                    {hasApiKey
                      ? clearKeyRequested
                        ? '清除已请求：点击保存将清空已存的 api_key；如想保留请改填新值。'
                        : '已配置密钥。留空保存将保留原值；填入新值则覆盖；点右侧按钮可显式清除。'
                      : '当前未配置密钥。留空保存保持未配置。'}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        ) : (
          <InfoBanner>Mock provider 不需要 base_url / api_key。</InfoBanner>
        )}

        <div className="form-row">
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{' '}
            启用（enabled=1 时可被环节分配命中）
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
            data-testid="profile-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}

const modalBackdrop: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};