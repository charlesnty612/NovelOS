import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  agentsApi,
  capabilityBindingsApi,
  connectionsApi,
  modelProfilesApi,
} from '../api/endpoints';
import type {
  Agent,
  CapabilityBinding,
  ModelConnectionOut,
  ModelProfile,
  ModelProfileCreatePayload,
  ModelProfileTestResult,
  ModelProfileUpdatePayload,
  PromptVersion,
  SyncPromptsResult,
} from '../api/types';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ConnectionsPanel } from '../components/ConnectionsPanel';
import { Loading } from '../components/Loading';
import { Modal } from '../components/Modal';
import { PROVIDER_OPTIONS, PROVIDER_SHORT_LABEL } from '../components/providerOptions';
import { useApiCall } from '../hooks/useApiCall';
import { formatDateTime } from '../utils/format';
import { ApiError } from '../api/client';
import { CapabilityBindingsPanel } from './AiSettingsCapabilityBindings';
import { BINDING_LABELS_FALLBACK, describeProfileInUse } from './AiSettingsBindingMeta';

export function AiSettingsPage() {
  const { pid } = useParams();
  // 档案卡「设为默认」与「环节分配」是同一条 capability_bindings 的两个写入口：
  // 档案卡改完链后 +1 ⇒ 环节分配面板重新拉取，否则它显示的还是旧链（用户按旧链
  // 移除成员会把整条链解绑，死结重现）。
  const [bindingsVersion, setBindingsVersion] = useState(0);
  return (
    <div>
      <h1 className="section-title">AI 设置</h1>
      <p className="section-subtitle">
        在这里决定每个生产环节用哪个模型：先在「模型连接」里配好接口与密钥，再到「模型档案」
        里挂上具体模型（同一厂商的多个模型可以共用一条连接），最后到「环节分配」把档案指派给
        题材定位、正文写作等环节。Agent 提示词版本也在本页查看与同步。
      </p>

      <InfoBanner>
        <span data-testid="ai-settings-logs-link">
          想看每次 AI 调用实际发了什么、回了什么？前往{' '}
          <Link to={`/ai-logs?project=${pid}`}>AI 调用日志</Link>。
        </span>
      </InfoBanner>

      {/* ⚠️ `.panel-grid` 是两列网格（index.css:728-737），1100px 以下自动折成单列。
          四个面板按 2×2 排布 ⇒ 无需改 CSS，窄屏折行由既有 media query 兜住。 */}
      <div className="panel-grid">
        <ConnectionsPanel />
        <ModelProfilesPanel onBindingsChanged={() => setBindingsVersion((v) => v + 1)} />
        <CapabilityBindingsPanel bindingsVersion={bindingsVersion} />
        <AgentsPanel />
      </div>
    </div>
  );
}

// =============================================================================
// ModelProfilesPanel —— 档案列表
// =============================================================================

function ModelProfilesPanel({
  onBindingsChanged,
}: {
  /** 档案卡改了候选链后回调（通知「环节分配」面板重新拉取）。 */
  onBindingsChanged?: () => void;
}) {
  const list = useApiCall<ModelProfile[]>(
    () => modelProfilesApi.list(),
    [],
  );
  // 档案卡「设为默认」按钮需要读到当前 capability_bindings（哪个环节首选了哪个档案）。
  // 这里独立拉一份，与 CapabilityBindingsPanel 互不共享——后者维护自己的候选链编辑态，
  // 由它上调会增加耦合；本面板只要最新 GET 结果即可。
  // ⚠️ 本面板改链后会回调 onBindingsChanged，让「环节分配」面板重新拉取（否则它显示旧链）。
  const bindings = useApiCall<CapabilityBinding[]>(
    () => capabilityBindingsApi.list(),
    [],
  );
  // 连接列表：档案行要显示「所属连接」，档案表单要能选连接。
  // ⚠️ 与 ConnectionsPanel 各自拉一份（两个面板互不共享状态，增删连接互不牵连）。
  const connections = useApiCall<ModelConnectionOut[]>(
    () => connectionsApi.list(),
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
  // V3.22「交互反馈统一」：删除档案二次确认 + 保存成功短暂条幅。
  const [deleteProfileId, setDeleteProfileId] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  // connection_id → 连接名（档案行显示「所属连接」）。
  const connectionNameById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of connections.data ?? []) map[c.connection_id] = c.name;
    return map;
  }, [connections.data]);

  // 成功条幅 2.5s 自动消失。用 effect 承载而非在调用点 window.setTimeout：
  // 卸载时自动清理定时器（否则组件已卸载仍会 setState）。
  useEffect(() => {
    if (!savedNotice) return;
    const timer = window.setTimeout(() => setSavedNotice(null), 2500);
    return () => window.clearTimeout(timer);
  }, [savedNotice]);

  return (
    <div className="panel" data-testid="model-profiles-panel">
      <div className="panel__title">
        模型档案
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
      {/* 连接列表拉不到时档案表单的「使用连接」态不可用 —— 如实报出来，不静默。 */}
      <ErrorBanner>{connections.error}</ErrorBanner>

      {list.loading ? (
        <Loading />
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
                    {Number(m.enabled) === 1 ? '启用' : '停用'}
                  </span>
                  {/* 所属连接：绑了连接显示连接名，没绑显示「独立配置」。 */}
                  <span
                    className={`badge ${
                      m.connection_id ? 'badge--info' : 'badge--archived'
                    }`}
                    data-testid={`profile-connection-badge-${m.profile_id}`}
                    title={
                      m.connection_id
                        ? '地址 / 密钥 / 请求头取自这条连接'
                        : '独立配置：自带地址与密钥'
                    }
                  >
                    {m.connection_id
                      ? (connectionNameById[m.connection_id] ?? m.connection_id)
                      : '独立配置'}
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
                      // 直接用本行数据打开编辑（列表读路径已含 name/provider/model/params，
                      // 且 params.api_key 已被后端脱敏成 "***"，不回填输入框）——此前这里
                      // 又发一次全量 list() 再 find，纯属多余请求。
                      onClick={() => setEditing(m)}
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
                      onClick={() => setDeleteProfileId(m.profile_id)}
                      data-testid={`model-profile-delete-${m.profile_id}`}
                    >
                      删除
                    </button>
                  </span>
                </div>
                {/* 展开行：8 个 capability chip；点 chip 把该档案置为该 capability 的首选。 */}
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
                              // 同一份 capability_bindings 的另一个视图（「环节分配」面板）
                              // 也要跟着刷新，否则它显示的还是旧链。
                              onBindingsChanged?.();
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
        // 成功=信息通道（蓝）；失败=错误通道（红）。此前无论成败都塞进 InfoBanner，
        // 与全站「红色=错误」的语义冲突（删除档案 409 也复用此处展示）。
        <div
          className={testResult.ok ? 'alert alert--info' : 'alert alert--error'}
          role={testResult.ok ? 'status' : 'alert'}
        >
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
        </div>
      ) : null}

      {creating ? (
        <ModelProfileFormModal
          title="新建模型档案"
          connections={connections.data ?? []}
          onCancel={() => setCreating(false)}
          onSubmit={async (payload: ModelProfileCreatePayload) => {
            await modelProfilesApi.create(payload);
            setCreating(false);
            void list.reload();
            setSavedNotice('已保存');
          }}
        />
      ) : null}

      {editing ? (
        <ModelProfileFormModal
          title={`编辑：${editing.name || editing.profile_id}`}
          initial={editing}
          connections={connections.data ?? []}
          onCancel={() => setEditing(null)}
          onSubmit={async (payload: ModelProfileUpdatePayload) => {
            await modelProfilesApi.update(editing.profile_id, payload);
            setEditing(null);
            void list.reload();
            setSavedNotice('已保存');
          }}
          onUnbind={async () => {
            // ⚠️ 只带 connection_id: null（后端部分更新：缺键 = 不改）。
            await modelProfilesApi.update(editing.profile_id, { connection_id: null });
            setEditing(null);
            void list.reload();
            setSavedNotice('已解除连接关联');
          }}
        />
      ) : null}

      {/* V3.22「交互反馈统一」：删除档案二次确认 + 保存/删除成功短暂条幅 */}
      {savedNotice ? (
        <div
          className="alert alert--info"
          role="status"
          aria-live="polite"
          data-testid="model-profiles-saved-banner"
          style={{ marginTop: 8 }}
        >
          {savedNotice}
        </div>
      ) : null}
      {deleteProfileId ? (
        <ConfirmDialog
          open={true}
          title="删除模型档案"
          body={`确认删除模型档案 ${deleteProfileId}？`}
          confirmText="删除"
          danger
          testId="model-profile-delete-confirm"
          onCancel={() => setDeleteProfileId(null)}
          onConfirm={async () => {
            const id = deleteProfileId;
            setDeleteProfileId(null);
            try {
              await modelProfilesApi.remove(id);
              void list.reload();
              setSavedNotice('已删除');
            } catch (e: unknown) {
              // 409 profile_in_use：后端给的是**真实状态**（档案仍被某环节的候选链引用），
              // 不做静默解绑。前端只负责把 detail 讲成人话 + 指向真做得到的动作：
              // 去「环节分配」把这些环节上的该档案移除。
              // ⚠️ e.detail 可能是对象，也可能是 api/client.ts JSON.stringify 后的字符串；
              //    两种都由 describeProfileInUse 归一化（不甩原始 JSON 串给用户）。
              if (e instanceof ApiError && e.status === 409) {
                setTestResult({
                  profile_id: id,
                  ok: false,
                  latency_ms: 0,
                  detail: describeProfileInUse(e.detail, (cap) =>
                    // 后端 GET 给的 label 优先，退到静态兜底表。
                    (bindings.data ?? []).find((b) => b.capability === cap)?.label ||
                    BINDING_LABELS_FALLBACK[cap] ||
                    cap,
                  ),
                  status_code: e.status,
                });
              } else {
                const msg =
                  e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                setTestResult({
                  profile_id: id,
                  ok: false,
                  latency_ms: 0,
                  detail: `删除失败 · ${msg}`,
                  status_code: null,
                });
              }
            }
          }}
        />
      ) : null}
    </div>
  );
}

// 提示词版本状态（prompts.status，DDL 枚举 DRAFT/ACTIVE/DEPRECATED）→ 中文标签。
// 未知值原样透出，避免后端新增枚举时前端显示空。
const PROMPT_STATUS_LABEL: Record<string, string> = {
  DRAFT: '草稿',
  ACTIVE: '启用',
  DEPRECATED: '已弃用',
};

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
        Agent 与提示词
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
          {syncing ? '同步中…' : '同步提示词'}
        </button>
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      <ErrorBanner>{syncErr}</ErrorBanner>

      {syncResult ? (
        <InfoBanner>
          同步完成：扫描 {syncResult.scanned.length} 个、新注册{' '}
          {syncResult.registered.length} 个、内容更新 {syncResult.updated.length} 个。
        </InfoBanner>
      ) : null}

      <div className="panel__section">
        <div className="panel__section-title">已注册的 Agent</div>
        {list.loading ? (
          <Loading />
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            title="还没有 Agent"
            hint="点击右上角「同步提示词」，从 docs/agents/prompts 扫描。"
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
          {selected ? `${selected.name} 的提示词` : '选择一个 Agent 查看提示词'}
        </div>
        {selected ? (
          prompts.loading ? (
            <Loading />
          ) : !prompts.data || prompts.data.length === 0 ? (
            <div className="muted small">
              该 Agent 暂无提示词，可点击右上角「同步提示词」拉取。
            </div>
          ) : (
            <div>
              <div className="muted small" style={{ marginBottom: 6 }}>
                共 {prompts.data.length} 个版本（新版本在前）
              </div>
              <div className="table-wrap">
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
                      <td>{PROMPT_STATUS_LABEL[p.status] ?? p.status}</td>
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
            </div>
          )
        ) : (
          <div className="muted small">从左侧列表选择一个 Agent。</div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// ModelProfileFormModal —— 档案表单：双模（使用连接 / 自定义配置）
// =============================================================================

/** 档案表单的两种配置来源。 */
type ProfileConfigMode = 'connection' | 'custom';

interface ModelProfileFormModalProps {
  title: string;
  initial?: ModelProfile;
  /** 可选连接列表（连接态的下拉候选）。 */
  connections: ModelConnectionOut[];
  onCancel: () => void;
  /** 编辑/新建由父组件传不同的 submit（create 必填 name，update 部分更新） */
  onSubmit:
    | ((payload: ModelProfileCreatePayload) => Promise<void>)
    | ((payload: ModelProfileUpdatePayload) => Promise<void>);
  /**
   * 显式解绑：PATCH **只带** `connection_id: null`（其余字段不动）。
   * 缺省（新建档案 / 未绑定档案）时表单不渲染解绑入口。
   */
  onUnbind?: () => Promise<void>;
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
  connections,
  onCancel,
  onSubmit,
  onUnbind,
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

  // ---------------------------------------------------------------------------
  // 双模：使用连接 / 自定义配置（2026-10-21 批次十五）
  //
  // - **连接态**：地址 / 密钥 / 自定义头全归连接 ⇒ 表单里**不显示**这三个输入框，
  //   只填模型名 + 思考模式；提交的 provider 取自所选连接。
  // - **自定义态**：完全保持既有行为（自带 base_url / api_key）。
  // - **默认态**：编辑一个**存量**档案（connection_id 为 null）⇒ 落在「自定义配置」，
  //   存量档案的读写路径逐字不变。
  // ---------------------------------------------------------------------------
  const [mode, setMode] = useState<ProfileConfigMode>(
    initial?.connection_id ? 'connection' : 'custom',
  );
  const [connectionId, setConnectionId] = useState<string>(
    initial?.connection_id ?? '',
  );
  // 「解除连接关联」进行中标志（按钮禁用 + 文案）。
  const [unbinding, setUnbinding] = useState(false);

  const isConnectionMode = mode === 'connection';
  const selectedConnection = useMemo(
    () => connections.find((c) => c.connection_id === connectionId) ?? null,
    [connections, connectionId],
  );
  // 连接态下 provider / base_url 都取自连接；自定义态取表单自身的。
  const effectiveProvider = isConnectionMode
    ? (selectedConnection?.provider ?? '')
    : provider;
  const effectiveBaseUrl = isConnectionMode
    ? (selectedConnection?.base_url ?? '')
    : params.baseUrl;

  const isMock = effectiveProvider === 'mock';
  const isAnthropic = effectiveProvider === 'anthropic';
  const isOllama = effectiveProvider === 'ollama';
  const requireBaseUrl = !isMock && !isAnthropic && !isOllama;
  const baseUrlPlaceholder = isAnthropic
    ? '留空 = 官方 api.anthropic.com'
    : isOllama
      ? '留空 = 本地 http://127.0.0.1:11434'
      : 'https://api.openai.com/v1';

  // V3.8「拉取模型」：调用后端代理端点，把模型 id 写回 <datalist>。
  // - Anthropic 允许不填 base_url（走官方默认）;
  // - Ollama 允许不填 base_url（走本地默认）;
  // - Mock 不需要拉取（按钮始终禁用，datalist 显示 mock-model）;
  // - 其余要求填 base_url。
  // - **连接态**：只发 connection_id —— 后端从连接取 provider / 地址 / 密钥 / 头。
  const baseUrlFilledForFetch = params.baseUrl.trim().length > 0;
  const canFetchModels = isConnectionMode
    ? connectionId !== ''
    : isMock || (isAnthropic || isOllama) || baseUrlFilledForFetch;

  const handleFetchModels = async () => {
    setErr(null);
    setFetchHint(null);
    setFetchingModels(true);
    try {
      const resp = isConnectionMode
        ? await modelProfilesApi.fetchAvailableModels({ connection_id: connectionId })
        : await modelProfilesApi.fetchAvailableModels({
            provider,
            ...(params.baseUrl.trim() ? { base_url: params.baseUrl.trim() } : {}),
            ...(initial?.profile_id ? { profile_id: initial.profile_id } : {}),
            // 用户若在表单上填了新 api_key，随请求发（后端会优先用之）；否则后端走
            // resolve_api_key 解析。注意：前端永远不显示 / 也不打日志密文。
            ...(params.apiKey.trim() ? { api_key: params.apiKey.trim() } : {}),
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

  // 切换 provider / 连接时清掉旧候选，避免与新端点不匹配。
  // 留 base_url 时的逐字符清空不禁用——候选人放掉就放掉，由用户主动重拉。
  useEffect(() => {
    setAvailableModels([]);
    setFetchHint(null);
    // 注：依赖 provider 即覆盖性切换；base_url 不入依赖以免抖动。
  }, [effectiveProvider, connectionId]);

  const hasApiKey = !!initial?.has_api_key;
  const canShowClearKeyButton =
    !!initial && hasApiKey && !isMock && !isOllama && !isConnectionMode;
  const apiKeyPlaceholder = isAnthropic
    ? hasApiKey
      ? clearKeyRequested
        ? '清除已请求（点击保存生效）'
        : '已配置（留空则不修改）'
      : '（可选；留空则运行时从环境变量读取）'
    : hasApiKey
      ? clearKeyRequested
        ? '清除已请求（点击保存生效）'
        : '已配置（留空则不修改）'
      : '（可选；留空则运行时从环境变量读取）';

  /**
   * 把思考模式写进 params（两种模式共用）。
   *
   * 与后端约定：payload 键值显式 null = 删除该键；缺键 = 保留 DB 原值（部分更新）。
   * 因此「回默认」用 null 显式删除，避免与「未提供该字段」混淆。
   */
  const applyThinkingToParams = (
    paramsOut: Record<string, unknown>,
    target: { isMock: boolean },
  ) => {
    if (!target.isMock) {
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
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('名称不能为空');
      return;
    }
    if (!model.trim()) {
      setErr('模型不能为空');
      return;
    }

    // ========================================================================
    // 连接态：地址 / 密钥 / 头归连接 ⇒ 档案级只带模型名与思考参数
    // ========================================================================
    if (isConnectionMode) {
      if (!connectionId) {
        setErr('请选择一条连接');
        return;
      }
      const conn = selectedConnection;
      if (!conn) {
        setErr('所选连接不存在（可能已被删除），请重新选择');
        return;
      }
      // paramsOut 以 initialParams 为基底（快照），只由表单管理思考参数；
      // 地址 / 密钥 / 头是连接级的，档案侧不重复携带（后端绑定态也会忽略它们）。
      const connParamsOut: Record<string, unknown> = { ...initialParams };
      applyThinkingToParams(connParamsOut, { isMock });
      setSubmitting(true);
      try {
        await onSubmit({
          name: name.trim(),
          // provider 由连接决定（连接是唯一权威；不一致后端会 422）。
          provider: conn.provider,
          model: model.trim(),
          params: connParamsOut,
          enabled: enabled ? 1 : 0,
          connection_id: conn.connection_id,
        });
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : '保存失败');
      } finally {
        setSubmitting(false);
      }
      return;
    }

    // ========================================================================
    // 自定义态：既有行为逐字保留（存量档案 connection_id 为 null 走的就是这条）
    // ========================================================================
    if (requireBaseUrl && !params.baseUrl.trim()) {
      setErr('该接口类型需要填写接口地址');
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
    applyThinkingToParams(paramsOut, { isMock });
    setSubmitting(true);
    try {
      const base: ModelProfileCreatePayload & ModelProfileUpdatePayload = {
        name: name.trim(),
        provider,
        model: model.trim(),
        params: paramsOut,
        enabled: enabled ? 1 : 0,
      };
      // ⚠️ 自定义态保存**不带** connection_id 键（缺键 = 不改绑定）。
      // 解绑是**另一个动作**（表单里的「解除连接关联」按钮 ⇒ PATCH connection_id: null）。
      await onSubmit(base);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  /**
   * 显式解绑：PATCH 只带 `connection_id: null`（其余字段不动）。
   * ⚠️ 不走表单保存路径——解绑后档案退回「独立配置」，地址 / 密钥要另行补齐。
   */
  const handleUnbind = async () => {
    if (!onUnbind) return;
    setErr(null);
    setUnbinding(true);
    try {
      await onUnbind();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '解除连接关联失败');
    } finally {
      setUnbinding(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onCancel}
      width={520}
      testId="model-profile-form-modal"
      // 保存中不允许 Esc / 点遮罩关闭（仍可用「取消」按钮退出）
      closable={!submitting}
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>

        {/* ---- 配置来源：使用连接 / 自定义配置（双模） ---- */}
        <div className="form-row">
          <label>配置来源</label>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                marginBottom: 0,
                fontSize: 13,
                color: 'inherit',
              }}
            >
              <input
                type="radio"
                checked={isConnectionMode}
                onChange={() => setMode('connection')}
                data-testid="profile-mode-connection"
              />
              使用连接
            </label>
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                marginBottom: 0,
                fontSize: 13,
                color: 'inherit',
              }}
            >
              <input
                type="radio"
                checked={mode === 'custom'}
                onChange={() => setMode('custom')}
                data-testid="profile-mode-custom"
              />
              自定义配置
            </label>
          </div>
        </div>

        {isConnectionMode ? (
          <div className="form-row">
            <label>连接 *</label>
            <select
              value={connectionId}
              onChange={(e) => setConnectionId(e.target.value)}
              data-testid="profile-connection"
            >
              <option value="">请选择一条连接…</option>
              {connections.map((c) => (
                <option key={c.connection_id} value={c.connection_id}>
                  {c.name}（{PROVIDER_SHORT_LABEL[c.provider] ?? c.provider}）
                </option>
              ))}
            </select>
            <div className="muted small" data-testid="profile-connection-hint">
              {selectedConnection
                ? `接口地址、密钥与自定义请求头都取自「${selectedConnection.name}」。`
                : '接口地址、密钥与自定义请求头都取自所选连接。'}
            </div>
          </div>
        ) : null}

        <div className="form-row">
          <label>名称 *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如 default-creative / fast-creative"
            data-testid="profile-name"
          />
        </div>

        {isConnectionMode ? (
          <div className="form-row">
            <label>接口类型</label>
            <div className="muted small" data-testid="profile-provider-readonly">
              {selectedConnection
                ? (PROVIDER_SHORT_LABEL[selectedConnection.provider] ??
                  selectedConnection.provider)
                : '（先选一条连接）'}
            </div>
          </div>
        ) : (
          <div className="form-row">
            <label>接口类型 *</label>
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
        )}

        {/* 已绑定的档案：显式解绑入口（PATCH connection_id: null）。 */}
        {initial?.connection_id ? (
          <div className="form-row">
            <label>连接关联</label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="muted small" data-testid="profile-bound-connection">
                当前绑定：
                {connections.find((c) => c.connection_id === initial.connection_id)
                  ?.name ??
                  initial.connection_id}
              </span>
              <button
                type="button"
                className="btn btn--sm btn--danger"
                onClick={() => {
                  void handleUnbind();
                }}
                disabled={unbinding}
                data-testid="profile-unbind-connection"
                title="解除后该档案退回独立配置（自带地址与密钥）"
              >
                {unbinding ? '解除中…' : '解除连接关联'}
              </button>
            </div>
          </div>
        ) : null}

        <div className="form-row">
          <label>模型 *</label>
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
                  ? isConnectionMode
                    ? '请先选择一条连接'
                    : '请先填写接口地址'
                  : '按当前接口类型与地址拉取可选模型'
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

        {isConnectionMode ? (
          <InfoBanner>
            接口地址、API 密钥与自定义请求头由所选连接统一提供 —— 这里只填模型名与思考模式。
          </InfoBanner>
        ) : !isMock ? (
          <div className="form-grid">
            <div className="form-row">
              <label>接口地址 {requireBaseUrl ? '*' : ''}</label>
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
              <InfoBanner>Ollama 本地服务无需 API 密钥 —— 表单不接收密钥。</InfoBanner>
            ) : (
              <div className="form-row">
                <label>API 密钥</label>
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
                      title="点击后保存将清空已存的密钥"
                    >
                      {clearKeyRequested ? '已请求清除' : '清除已存密钥'}
                    </button>
                  ) : null}
                </div>
                {initial ? (
                  <div className="muted small" data-testid="profile-api-key-hint">
                    {hasApiKey
                      ? clearKeyRequested
                        ? '清除已请求：点击保存将清空已存的密钥；如想保留请改填新值。'
                        : '已配置密钥。留空保存将保留原值；填入新值则覆盖；点右侧按钮可显式清除。'
                      : '当前未配置密钥。留空保存保持未配置。'}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        ) : (
          <InfoBanner>Mock 类型不需要接口地址与密钥。</InfoBanner>
        )}

        <div className="form-row">
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{' '}
            启用（启用后才可被环节分配选中）
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
                ? 'Mock 类型不支持思考模式'
                : '按当前接口类型与地址自动收窄候选；保存到该档案的思考参数'
            }
            data-testid="profile-thinking-select"
          >
            {(() => {
              // V3.8：按端点收窄候选档位。
              // ⚠️ 连接态下端点信息取自**所选连接**（provider + base_url）。
              const narrowed = getThinkingOptions(effectiveProvider, effectiveBaseUrl);
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
            const values = getThinkingOptions(effectiveProvider, effectiveBaseUrl).map(
              (o) => o.value,
            );
            if ((values as string[]).includes(thinkingMode)) return null;
            return (
              <div className="muted small" data-testid="profile-thinking-out-of-list">
                当前保存的档位不在该接口支持范围内，保存时按所选档位写入。
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
    </Modal>
  );
}