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
  // 档案卡「设为默认」按钮需要读到当前 capability_bindings（哪个环节首选了哪个档案）。
  // 这里独立拉一份，与 CapabilityBindingsPanel 互不共享——后者维护 selectedMap 编辑态，
  // 由它上调会增加耦合；本面板只要最新 GET 结果即可。
  const bindings = useApiCall<CapabilityBinding[]>(
    () => capabilityBindingsApi.list(),
    [],
  );
  const [editing, setEditing] = useState<ModelProfile | null>(null);
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<ModelProfileTestResult | null>(null);
  // 该档案卡片正在展开「设为默认」下拉时持有的打开态。
  // 同时只能展开一张档案，避免多行同时弹出 7 个 chip 行干扰视线。
  const [openSetDefaultFor, setOpenSetDefaultFor] = useState<string | null>(null);
  // 该档案对应的「设为默认」操作进行中的 capability 列表；用于 chip 行内单独禁用。
  const [setDefaultBusyFor, setSetDefaultBusyFor] = useState<
    Record<string, string | null>
  >({});
  // 该档案在「设为默认」分支中上一次失败的错误信息；展示在档案卡底部 ErrorBanner。
  const [setDefaultError, setSetDefaultError] = useState<string | null>(null);

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
          {list.data.map((m) => {
            // 当前档案在每个 capability 的 profile_ids 中是否排第一 → 默认环节徽标。
            // profile_ids 可能为 [] / ['mpf_xxx'] / ['mpf_xxx', 'mpf_yyy', ...]，
            // 仅当第 0 项等于本档案 id 时算「该环节首选 = 本档案」，用于徽标展示。
            const defaultCapabilities = (bindings.data ?? []).filter(
              (b) => b.profile_ids[0] === m.profile_id,
            );
            const open = openSetDefaultFor === m.profile_id;
            const busyCap = setDefaultBusyFor[m.profile_id] ?? null;
            return (
              <div
                key={m.profile_id}
                className="kv-list__row"
                data-testid={`model-profile-row-${m.profile_id}`}
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
                  <span
                    className="badge badge--archived"
                    data-testid={`profile-thinking-badge-${m.profile_id}`}
                  >
                    思考：
                    {THINKING_BADGE_LABEL[inferThinkingMode(
                      (m.params as Record<string, unknown>) ?? {},
                    )]}
                  </span>
                  {defaultCapabilities.length > 0 ? (
                    <span
                      className="muted small"
                      data-testid={`model-profile-default-caps-${m.profile_id}`}
                    >
                      默认：
                      {defaultCapabilities.map((b) => (
                        <span
                          key={b.capability}
                          className="badge badge--chapter-committed"
                          style={{ marginLeft: 4 }}
                          data-testid={`model-profile-default-cap-badge-${m.profile_id}-${b.capability}`}
                        >
                          {b.label || BINDING_LABELS_FALLBACK[b.capability] || b.capability}
                        </span>
                      ))}
                    </span>
                  ) : null}
                  <span className="kv-list__meta">{m.profile_id}</span>
                  <span style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
                    <button
                      className="btn btn--sm"
                      onClick={() => {
                        // 切换展开：再次点击收起；切到别的档案时直接换对象。
                        setSetDefaultError(null);
                        setOpenSetDefaultFor((cur) =>
                          cur === m.profile_id ? null : m.profile_id,
                        );
                      }}
                      data-testid={`profile-set-default-${m.profile_id}`}
                      aria-expanded={open}
                    >
                      设为默认 ▾
                    </button>
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
                {/* 展开行：7 个 capability chip；点 chip 把该档案置为该 capability 的首选。 */}
                {open ? (
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 6,
                      marginTop: 8,
                      marginLeft: 4,
                    }}
                    data-testid={`profile-default-chip-row-${m.profile_id}`}
                  >
                    {(bindings.data ?? []).map((b) => {
                      const isCurrentDefault = b.profile_ids[0] === m.profile_id;
                      const isBusy = busyCap === b.capability;
                      return (
                        <button
                          key={b.capability}
                          type="button"
                          className={`badge ${
                            isCurrentDefault
                              ? 'badge--chapter-committed'
                              : 'badge--archived'
                          }`}
                          disabled={isBusy || Number(m.enabled) !== 1}
                          title={
                            Number(m.enabled) !== 1
                              ? '档案需启用（enabled=1）才能绑定'
                              : isCurrentDefault
                                ? '已是该环节首选'
                                : `把 ${m.name} 设为「${b.label}」首选`
                          }
                          data-testid={`profile-default-cap-${m.profile_id}-${b.capability}`}
                          onClick={async () => {
                            // 幂等：若本档案已经是该 capability 的首选，则不发起请求。
                            if (isCurrentDefault) return;
                            const rest = b.profile_ids.filter(
                              (pid) => pid !== m.profile_id,
                            );
                            const next = [m.profile_id, ...rest];
                            setSetDefaultError(null);
                            setSetDefaultBusyFor((s) => ({ ...s, [m.profile_id]: b.capability }));
                            try {
                              await capabilityBindingsApi.bind(b.capability, next);
                              await bindings.reload();
                            } catch (e: unknown) {
                              const msg =
                                e instanceof ApiError
                                  ? `${e.status} ${e.detail}`
                                  : String(e);
                              setSetDefaultError(
                                `「${b.label}」绑定失败：${msg}`,
                              );
                            } finally {
                              setSetDefaultBusyFor((s) => ({
                                ...s,
                                [m.profile_id]: null,
                              }));
                            }
                          }}
                          style={{ cursor: 'pointer', border: 'none' }}
                        >
                          {isBusy
                            ? '保存中…'
                            : `${b.label || BINDING_LABELS_FALLBACK[b.capability] || b.capability}${
                                isCurrentDefault ? ' · 首选' : ''
                              }`}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
                {open && setDefaultError && setDefaultError.startsWith(`「`) && openSetDefaultFor === m.profile_id ? (
                  <ErrorBanner>{setDefaultError}</ErrorBanner>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {!openSetDefaultFor && setDefaultError ? (
        <ErrorBanner>{setDefaultError}</ErrorBanner>
      ) : null}

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
          <option value="">未绑定（回落旧版 model_configs 链）</option>
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

type ThinkingMode =
  | 'default'
  | 'off'
  | 'on'
  | 'adaptive'
  | 'low'
  | 'medium'
  | 'high'
  // V3.8：Kimi 专属「max」档，写入时仍走 reasoning_effort（与 low/medium/high 同路）。
  | 'max';

const THINKING_OPTIONS: { value: ThinkingMode; label: string; hint?: string }[] = [
  { value: 'default', label: '默认（跟随端点）' },
  { value: 'off', label: '关闭思考' },
  { value: 'on', label: '开启思考' },
  { value: 'adaptive', label: '自适应（MiniMax M 系列）', hint: 'MiniMax M 系专用' },
  { value: 'low', label: '开启·低档' },
  { value: 'medium', label: '开启·中档' },
  { value: 'high', label: '开启·高档' },
  { value: 'max', label: '开启·最高档（Kimi）', hint: 'Kimi 专属档位' },
];

/**
 * 抽取 host（小写）——兼容 ``https://api.openai.com/v1`` / ``http://127.0.0.1:11434`` 等。
 */
function hostOf(baseUrl: string): string {
  if (!baseUrl) return '';
  const s = baseUrl.trim().toLowerCase();
  const m = s.match(/^[a-z][a-z0-9+\-.]*:\/\/([^/?#]+)/);
  if (m && m[1]) return m[1];
  // 兜底：非标准 url，截取首段
  const noProto = s.replace(/^[a-z][a-z0-9+\-.]*:\/\//, '');
  return noProto.split('/')[0] ?? '';
}

/**
 * 按 provider + base_url 收窄思考模式下拉（V3.8）。
 * 没有「协议层的端点能力查询接口」，因此我们自己维护端点 → 推荐档位映射。
 *
 * 返回的顺序就是下拉展示顺序；与 ``THINKING_OPTIONS`` 的相对顺序保持一致。
 */
function getThinkingOptions(
  provider: string,
  baseUrl: string,
): { value: ThinkingMode; label: string; hint?: string }[] {
  const host = hostOf(baseUrl);
  const isProvider = (p: string) => provider === p;
  // Provider 自带（不依赖 host）—— Ollama / Anthropic / Mock 一律退回全量。
  if (isProvider('anthropic') || isProvider('ollama') || isProvider('mock')) {
    return THINKING_OPTIONS;
  }
  // MiniMax M 系：仅 default / off / adaptive。
  if (host.includes('minimax')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'adaptive'].includes(o.value),
    );
  }
  // DeepSeek：default / off / low / medium / high
  if (host.includes('deepseek')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'medium', 'high'].includes(o.value),
    );
  }
  // Kimi：default / off / low / high / max（不含 on/adaptive）。
  // host 需同时命中 kimi.com 与 Moonshot 官方域（api.moonshot.ai / api.moonshot.cn）。
  if (
    host.includes('kimi.com') ||
    host.includes('moonshot.ai') ||
    host.includes('moonshot.cn')
  ) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'high', 'max'].includes(o.value),
    );
  }
  // OpenAI：仅当 host 含 ``api.openai.com`` 或独立 ``provider==='openai'`` 时收窄。
  // ``provider==='openai_compatible'`` 不一定指向 OpenAI 官方（如用户指向私有端点），
  // 因此只有 host 命中时才走 OpenAI 档位；其余代理类端点一律退回全量。
  if (isProvider('openai') || host.includes('api.openai.com')) {
    return THINKING_OPTIONS.filter((o) =>
      ['default', 'off', 'low', 'medium', 'high'].includes(o.value),
    );
  }
  // 未知端点：全量
  return THINKING_OPTIONS;
}

/**
 * 从 params 推断「思考模式」下拉的初值：
 * - thinking.type === 'disabled' → 'off'
 * - thinking.type === 'enabled'  → 'on'
 * - thinking.type === 'adaptive' → 'adaptive'（MiniMax M 系列专用值）
 * - reasoning_effort ∈ {low,medium,high} → 对应档位
 * - 都没有 → 'default'（不显式写入 params）
 */
// 导出仅为测试：回归锁死 reasoning_effort=max 的回显（漏判会静默丢档）。
export function inferThinkingMode(params: Record<string, unknown>): ThinkingMode {
  const thinking = params['thinking'];
  if (thinking && typeof thinking === 'object') {
    const t = thinking as Record<string, unknown>;
    if (t['type'] === 'disabled') return 'off';
    if (t['type'] === 'enabled') return 'on';
    if (t['type'] === 'adaptive') return 'adaptive';
  }
  const effort = params['reasoning_effort'];
  if (
    effort === 'low' ||
    effort === 'medium' ||
    effort === 'high' ||
    effort === 'max'
  ) {
    return effort;
  }
  return 'default';
}

const THINKING_BADGE_LABEL: Record<ThinkingMode, string> = {
  default: '默认',
  off: '关',
  on: '开',
  adaptive: '自适应',
  low: '低',
  medium: '中',
  high: '高',
  max: '最高',
};

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
  // 思考模式：从 initial.params 推断初值，编辑表单打开时即正确回显。
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    inferThinkingMode(initialParams),
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 编辑模式下「清除已存密钥」独立标志位：仅显式请求时才传 api_key=""。
  const [clearKeyRequested, setClearKeyRequested] = useState(false);
  // V3.8「拉取模型」状态：候选项、加载中、提示。切换 provider 或成功拉取时刷新 hint。
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchHint, setFetchHint] = useState<string | null>(null);

  const isMock = provider === 'mock';
  const isAnthropic = provider === 'anthropic';
  const isOllama = provider === 'ollama';
  const requireBaseUrl = !isMock && !isAnthropic && !isOllama;
  const baseUrlPlaceholder = isAnthropic
    ? '留空 = 官方 api.anthropic.com'
    : isOllama
      ? '留空 = 本地 http://127.0.0.1:11434'
      : 'https://api.openai.com/v1';

  // V3.8「拉取模型」：调用后端代理端点，把模型 id 写回 <datalist>。
  // - Anthropic 允许不填 base_url（走官方默认）；
  // - Ollama 允许不填 base_url（走本地默认）；
  // - Mock 不需要拉取（按钮始终禁用，datalist 显示 mock-model）；
  // - 其余要求填 base_url。
  const baseUrlFilledForFetch = params.baseUrl.trim().length > 0;
  const canFetchModels =
    isMock ||
    (isAnthropic || isOllama) ||
    baseUrlFilledForFetch;

  const handleFetchModels = async () => {
    setErr(null);
    setFetchHint(null);
    setFetchingModels(true);
    try {
      const payload: {
        provider: string;
        base_url?: string;
        profile_id?: string;
      } = {
        provider,
      };
      if (params.baseUrl.trim()) payload.base_url = params.baseUrl.trim();
      if (initial?.profile_id) payload.profile_id = initial.profile_id;
      // 用户若在表单上填了新 api_key，随请求发（后端会优先用之）；否则后端走
      // resolve_api_key 解析。注意：前端永远不显示 / 也不打日志密文。
      const inlineApiKey = params.apiKey.trim();
      const resp = await modelProfilesApi.fetchAvailableModels({
        ...payload,
        ...(inlineApiKey ? { api_key: inlineApiKey } : {}),
      });
      const ids = Array.isArray(resp?.models)
        ? resp.models.filter((x): x is string => typeof x === 'string' && x.length > 0)
        : [];
      setAvailableModels(ids);
      setFetchHint(ids.length > 0 ? `已拉取 ${ids.length} 个模型` : '未取到模型，请检查 base_url / 密钥');
    } catch (e: unknown) {
      // 复用 err 横幅；fetchHint 同时给一行 inline 提示。
      const msg = e instanceof Error ? e.message : '拉取模型列表失败';
      setErr(msg);
      setFetchHint(msg);
    } finally {
      setFetchingModels(false);
    }
  };

  // 切换 provider / base_url 时清掉旧候选，避免与新端点不匹配。
  // 留 base_url 时的逐字符清空不禁用——候选人放掉就放掉，由用户主动重拉。
  useEffect(() => {
    setAvailableModels([]);
    setFetchHint(null);
    // 注：依赖 provider 即覆盖性切换；base_url 不入依赖以免抖动。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

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
    // paramsOut 以 initialParams 为基底（快照），表单管理的键（base_url / api_key /
// thinking / reasoning_effort）覆盖，其余键（如 timeout_s / service_tier 等）
// 原样保留——避免编辑档案时静默丢掉后端 params 中已有的键。
    const paramsOut: Record<string, unknown> = { ...initialParams };
    if (!isMock) {
      const trimmedUrl = params.baseUrl.trim();
      if (trimmedUrl) {
        paramsOut['base_url'] = trimmedUrl;
      } else {
        delete paramsOut['base_url'];
      }
      if (!isOllama) {
        if (clearKeyRequested) {
          paramsOut['api_key'] = '';
        } else if (params.apiKey.trim() !== '') {
          paramsOut['api_key'] = params.apiKey.trim();
        } else {
          // 用户留空且未请求清除 → 删除残留掩码 '***'，让后端按「不修改」处理。
          if (paramsOut['api_key'] === '***') delete paramsOut['api_key'];
        }
      } else {
        // Ollama 不接受 api_key，从输出里剔除
        delete paramsOut['api_key'];
      }
    } else {
      // Mock provider 不关心 base_url/api_key，从输出里剔除以保持干净
      delete paramsOut['base_url'];
      delete paramsOut['api_key'];
    }
    // 思考模式：表单是该字段的唯一管理面，按选项写/删 thinking / reasoning_effort。
    // 与后端约定：payload 键值显式 null = 删除该键；缺键 = 保留 DB 原值（部分更新）。
    // 因此「回默认」用 null 显式删除，避免与「未提供该字段」混淆。
    if (!isMock) {
      delete paramsOut['thinking'];
      delete paramsOut['reasoning_effort'];
      if (thinkingMode === 'off') {
        paramsOut['thinking'] = { type: 'disabled' };
      } else if (thinkingMode === 'on') {
        paramsOut['thinking'] = { type: 'enabled' };
      } else if (thinkingMode === 'adaptive') {
        // MiniMax M 系列专用值：enabled 不是合法枚举（仅 adaptive/disabled）
        paramsOut['thinking'] = { type: 'adaptive' };
      } else if (
        thinkingMode === 'low' ||
        thinkingMode === 'medium' ||
        thinkingMode === 'high' ||
        thinkingMode === 'max'
      ) {
        paramsOut['reasoning_effort'] = thinkingMode;
      } else if (thinkingMode === 'default') {
        // 「默认（跟随端点）」= 显式删除 thinking / reasoning_effort，触发后端 null 语义
        if ('thinking' in initialParams) paramsOut['thinking'] = null;
        if ('reasoning_effort' in initialParams) paramsOut['reasoning_effort'] = null;
      }
    } else {
      // mock provider 没有「思考」概念，强制清除避免残留
      delete paramsOut['thinking'];
      delete paramsOut['reasoning_effort'];
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
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="如 gpt-4o-mini / mock-echo / deepseek-chat"
              style={{ flex: 1 }}
              data-testid="profile-model"
            />
            <button
                type="button"
                className="btn btn--sm"
                onClick={handleFetchModels}
                disabled={!canFetchModels || fetchingModels}
                title={
                  !canFetchModels
                    ? '请先填写 base_url'
                    : '从当前 provider + base_url 拉取可选模型'
                }
                data-testid="profile-fetch-models"
              >
                {fetchingModels ? '拉取中…' : '拉取模型'}
              </button>
          </div>
          {/* V3.8：拉取成功后显式列出完整候选（原生 datalist 会按输入框当前值
              过滤候选——当前值命中某一项时其余候选全部不可见，故弃用）。
              仅作选取辅助，不限定手输。 */}
          {availableModels.length > 0 ? (
            <select
              className="input"
              style={{ marginTop: 6 }}
              value=""
              onChange={(e) => {
                if (e.target.value) setModel(e.target.value);
              }}
              data-testid="profile-model-candidates"
            >
              <option value="" disabled>
                从已拉取的 {availableModels.length} 个模型中选择…
              </option>
              {availableModels.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          ) : null}
          {fetchHint ? (
            <div
              className="muted small"
              data-testid="profile-fetch-models-hint"
              role={fetchHint.startsWith('已拉取') ? 'status' : undefined}
            >
              {fetchHint}
            </div>
          ) : null}
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

        <div className="form-row">
          <label>思考模式</label>
          <select
            value={thinkingMode}
            onChange={(e) => setThinkingMode(e.target.value as ThinkingMode)}
            disabled={isMock}
            title={
              isMock
                ? 'Mock provider 不支持思考模式'
                : '按当前 provider + base_url 自动收窄候选；写入 params 的 thinking / reasoning_effort 字段'
            }
            data-testid="profile-thinking-select"
          >
            {(() => {
              // V3.8：按端点收窄候选档位。
              const narrowed = getThinkingOptions(provider, params.baseUrl);
              const values = narrowed.map((o) => o.value);
              // 编辑档案时，初值 inferThinkingMode 可能落在当前端点不支持的选项里
              // （如 MiniMax 域编辑老档案存的是 'on' / 'medium'）—— 渲染期回退为
              // 'default'，并附一行 muted 提示。
              const currentInList = (values as string[]).includes(thinkingMode);
              return (
                <>
                  {narrowed.map((o) => (
                    <option key={o.value} value={o.value} title={o.hint ?? ''}>
                      {o.label}
                    </option>
                  ))}
                  {!currentInList ? (
                    <option value={thinkingMode} hidden>
                      {thinkingMode}
                    </option>
                  ) : null}
                </>
              );
            })()}
          </select>
          {(() => {
            const values = getThinkingOptions(provider, params.baseUrl).map((o) => o.value);
            if ((values as string[]).includes(thinkingMode)) return null;
            return (
              <div className="muted small" data-testid="profile-thinking-out-of-list">
                当前保存的思考参数不在该端点支持列表内，保存时将按所选选项写入。
              </div>
            );
          })()}
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