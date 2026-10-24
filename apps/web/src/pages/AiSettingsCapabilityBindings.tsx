// =============================================================================
// 环节分配面板（capability bindings）—— 从 AiSettingsPage.tsx 拆出
// -----------------------------------------------------------------------------
// 拆分原因：AiSettingsPage.tsx 已 1648 行，超过项目铁律 1500 行上限；本面板 +
//   候选链 UI 独立成文件，主文件回到上限以内。
//
// 修的缺陷（Lead 2026-10-23 实证根因）：capability_bindings.profile_ids 是
// **有序数组**，顺序即 fallback 序，后端运行期真的会用后备位。旧实现：① 只渲染
//   profile_ids[0] ⇒ 后备成员不可见；② 档案卡「设为默认」是 prepend ⇒ 旧档案被
//   推到后备位而非被替换；③ 改写下拉 = bind(cap, [id]) 整链替换成单个 ⇒ 后备成员
//   被静默丢弃。⇒ 旧档案还挂在链上、看不见、删不掉（后端 409 是真实状态，不是误报）。
//
// 契约：A 链可见（名字取 binding.profiles，缺失退化为 id）｜B 链可移除（链空 ⇒
//   unbind，不能 bind：后端 PUT 对空 profile_ids 返 422）｜C 下拉改选 = 设为首选 +
//   其余成员按原相对顺序保留。契约 D（409 文案）在 AiSettingsBindingMeta.ts。
// 契约 E（2026-10-24 环节级强度覆盖）：每行「强度」下拉直接设该环节的思考档
//   （thinking_override）；「跟随档案」= null = 用首选档案自己的思考档。
//   选项按**首选档案的 provider + base_url** 收窄（与档案表单同一份
//   getThinkingOptions，勿复制）；未绑定的环节下拉禁用。
//   PUT 语义：改强度带 thinking_override（null=清除）；改链不带该键（保留现值）。
// =============================================================================

import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import {
  capabilityBindingsApi,
  connectionsApi,
  modelProfilesApi,
} from '../api/endpoints';
import type {
  CapabilityBinding,
  ModelConnectionOut,
  ModelProfile,
} from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { useApiCall } from '../hooks/useApiCall';
import {
  THINKING_BADGE_LABEL,
  getThinkingOptions,
  type ThinkingMode,
} from './AiSettingsThinkingOptions';
import { BINDING_LABELS_FALLBACK } from './AiSettingsBindingMeta';

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

/** 两个候选链是否逐位相同（顺序敏感：顺序即 fallback 序）。 */
function sameChain(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/**
 * 解出首选档案的「协议 + 端点地址」，供 getThinkingOptions 收窄强度下拉。
 * base_url 优先取档案自定义态的 params.base_url；绑定连接的档案其地址归连接
 * （连接是唯一权威）⇒ 回退该连接的 base_url。都拿不到 ⇒ 空串（getThinkingOptions
 * 对未知端点退回全量 8 档——选项偏多好过选项缺失）。
 */
export function resolveProfileEndpoint(
  profile: ModelProfile | undefined,
  connections: ModelConnectionOut[],
): { provider: string; baseUrl: string } {
  if (!profile) return { provider: '', baseUrl: '' };
  const params = (profile.params ?? {}) as Record<string, unknown>;
  const ownBaseUrl =
    typeof params['base_url'] === 'string' ? String(params['base_url']) : '';
  const conn = profile.connection_id
    ? connections.find((c) => c.connection_id === profile.connection_id)
    : undefined;
  return {
    provider: profile.provider ?? '',
    baseUrl: ownBaseUrl || conn?.base_url || '',
  };
}

export function CapabilityBindingsPanel({
  bindingsVersion,
}: {
  /**
   * 外部改动计数：档案卡「设为默认」也会改同一条候选链，改完 +1 ⇒ 本面板重新拉取。
   * 不传则本面板只在自己发起写操作后刷新（旧的独立副本行为）。
   */
  bindingsVersion?: number;
}) {
  const list = useApiCall<CapabilityBinding[]>(
    () => capabilityBindingsApi.list(),
    [bindingsVersion],
  );
  // profiles 用于下拉选项：enabled=1 的档案集合
  const profiles = useApiCall<ModelProfile[]>(
    () => modelProfilesApi.list(),
    [],
  );
  // connections 用于强度下拉的端点收窄：绑定连接的档案，其 base_url 归连接
  // （连接是唯一权威），档案 params 里没有 ⇒ 必须有连接表才能算出首选档案的 host。
  const connections = useApiCall<ModelConnectionOut[]>(
    () => connectionsApi.list(),
    [],
  );

  /**
   * 乐观覆盖：{capability: 候选链}。只存「本面板发起的写操作」，**不存服务端状态**
   * —— 服务端状态永远从 list.data 取。这样别的入口（档案卡「设为默认」）改了链，
   * 本面板也能看到，不会卡在旧链上。
   */
  const [pendingChains, setPendingChains] = useState<Record<string, string[]>>({});
  const [saveMap, setSaveMap] = useState<Record<string, SaveState>>({});

  // 服务端链追上乐观值 ⇒ 撤掉覆盖，回到「以服务端为准」。
  useEffect(() => {
    if (!list.data) return;
    const rows = list.data;
    setPendingChains((prev) => {
      const next: Record<string, string[]> = {};
      let changed = false;
      for (const [cap, chain] of Object.entries(prev)) {
        const server = rows.find((b) => b.capability === cap)?.profile_ids ?? [];
        if (sameChain(chain, server)) {
          changed = true; // 已同步 ⇒ 撤掉覆盖
          continue;
        }
        next[cap] = chain;
      }
      return changed ? next : prev;
    });
  }, [list.data]);

  const enabledProfiles = useMemo(
    () => (profiles.data ?? []).filter((p) => Number(p.enabled) === 1),
    [profiles.data],
  );

  // 全量档案索引：候选链里存在但 binding.profiles 缺项的成员，用它兜底显示名字。
  const profileById = useMemo(() => {
    const map: Record<string, ModelProfile> = {};
    for (const p of profiles.data ?? []) map[p.profile_id] = p;
    return map;
  }, [profiles.data]);

  /** 该 capability 当前生效的候选链（乐观覆盖优先，否则服务端链）。 */
  const currentChainOf = (capability: string): string[] => {
    const pending = pendingChains[capability];
    if (pending) return pending;
    const b = (list.data ?? []).find((x) => x.capability === capability);
    return b?.profile_ids ?? [];
  };

  /**
   * 唯一的写路径：提交一条候选链。
   * - nextChain 非空 ⇒ bind(capability, nextChain)（整链提交，顺序即 fallback 序）
   * - nextChain 为空 ⇒ unbind(capability)（后端 PUT 对空 profile_ids 返 422，
   *   所以「链空」只能走 DELETE）
   */
  const commitChain = async (
    capability: string,
    nextChain: string[],
  ): Promise<void> => {
    // 乐观更新：下拉与候选链立刻反映用户操作；失败回滚。
    setPendingChains((m) => ({ ...m, [capability]: nextChain }));
    setSaveMap((m) => ({ ...m, [capability]: { kind: 'saving' } }));
    try {
      if (nextChain.length === 0) {
        await capabilityBindingsApi.unbind(capability);
      } else {
        await capabilityBindingsApi.bind(capability, nextChain);
      }
      setSaveMap((m) => ({ ...m, [capability]: { kind: 'saved' } }));
      void list.reload();
    } catch (e: unknown) {
      // 失败回滚：撤掉乐观覆盖，回到服务端链（服务端此刻仍是旧链）。
      setPendingChains((m) => {
        const next = { ...m };
        delete next[capability];
        return next;
      });
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setSaveMap((m) => ({ ...m, [capability]: { kind: 'error', message: msg } }));
    }
  };

  /**
   * C：下拉改选 = 「设为首选，链中其余成员按原相对顺序保留」。
   * 新链 = [新选中的 id, ...原链中其余成员（按原链顺序去掉新选中的）]。
   * 选空值（未绑定）⇒ 整条链解除（unbind），不是只删首选。
   */
  const handleChange = async (
    capability: string,
    profileId: string,
  ): Promise<void> => {
    const current = currentChainOf(capability);
    if (profileId === '') {
      await commitChain(capability, []);
      return;
    }
    const rest = current.filter((pid) => pid !== profileId);
    await commitChain(capability, [profileId, ...rest]);
  };

  /**
   * B：移除候选链里的某个成员（含首选）。
   * - 移除非首选 ⇒ 提交「去掉该 id 后的有序剩余列表」
   * - 移除首选 ⇒ 顺位下一位自动成为首选（提交同一份剩余列表，顺序不变）
   * - 移除后链为空 ⇒ unbind（见 commitChain 注释）
   */
  const handleRemove = async (
    capability: string,
    profileId: string,
  ): Promise<void> => {
    const current = currentChainOf(capability);
    await commitChain(capability, current.filter((pid) => pid !== profileId));
  };

  /**
   * E：改环节强度覆盖。
   * - value 非空 ⇒ PUT 带 thinking_override=<value>（8 档枚举，后端校验值域）；
   * - value 为空串（「跟随档案」）⇒ PUT 带 thinking_override=null（显式清除，
   *   不是「不带键」——那会被后端解释为保留现值）；
   * - 不改候选链；未绑定环节的入口已禁用（防御：链空直接返回）。
   */
  const handleThinkingChange = async (
    capability: string,
    value: string,
  ): Promise<void> => {
    const chain = currentChainOf(capability);
    if (chain.length === 0) return;
    setSaveMap((m) => ({ ...m, [capability]: { kind: 'saving' } }));
    try {
      await capabilityBindingsApi.bind(
        capability,
        chain,
        value === '' ? null : value,
      );
      setSaveMap((m) => ({ ...m, [capability]: { kind: 'saved' } }));
      void list.reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setSaveMap((m) => ({
        ...m,
        [capability]: { kind: 'error', message: msg },
      }));
    }
  };

  const connectionById = useMemo(() => {
    const map: Record<string, ModelConnectionOut> = {};
    for (const c of connections.data ?? []) map[c.connection_id] = c;
    return map;
  }, [connections.data]);

  return (
    <div className="panel" data-testid="capability-bindings-panel">
      <div className="panel__title">环节分配</div>
      <div className="muted small" style={{ marginBottom: 6 }}>
        把模型档案指派到 8 个生产环节（题材定位、正文写作、状态提取等）；解绑表示该环节改用旧版默认配置。
        每个环节是一条**有序候选链**：第 1 位是首选，失败时按顺序退到后备位；点成员上的「移除」可把它摘出这条链。
        「强度」是环节级思考档覆盖——环节强度覆盖档案的思考档，同一档案可给不同环节配不同强度。
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      <ErrorBanner>{profiles.error}</ErrorBanner>
      <ErrorBanner>{connections.error}</ErrorBanner>

      {list.loading ? (
        <Loading />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="尚未获取到环节清单"
          hint="接口返回为空或请求失败，请稍后重试。"
        />
      ) : (
        <div className="kv-list" data-testid="capability-bindings-list">
          {list.data.map((b) => (
            <CapabilityBindingRow
              key={b.capability}
              binding={b}
              chain={currentChainOf(b.capability)}
              profiles={enabledProfiles}
              profileById={profileById}
              connectionById={connectionById}
              state={saveMap[b.capability] ?? { kind: 'idle' }}
              onChange={(id) => void handleChange(b.capability, id)}
              onRemove={(id) => void handleRemove(b.capability, id)}
              onThinkingChange={(v) => void handleThinkingChange(b.capability, v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CapabilityBindingRow({
  binding,
  chain,
  profiles,
  profileById,
  connectionById,
  state,
  onChange,
  onRemove,
  onThinkingChange,
}: {
  binding: CapabilityBinding;
  chain: string[];
  profiles: ModelProfile[];
  profileById: Record<string, ModelProfile>;
  connectionById: Record<string, ModelConnectionOut>;
  state: SaveState;
  onChange: (profileId: string) => void;
  onRemove: (profileId: string) => void;
  onThinkingChange: (value: string) => void;
}) {
  // 首选 = 链的第 1 位；首选被移除后这里自动变成新的顺位（或空串）。
  const selected = chain[0] ?? '';
  const isUnbound = chain.length === 0;
  const showLegacyHint = isUnbound && binding.legacy_available;
  const label =
    binding.label ||
    BINDING_LABELS_FALLBACK[binding.capability] ||
    binding.capability;
  const busy = state.kind === 'saving';
  // 强度下拉：按首选档案的 provider + base_url 收窄（与档案表单同一份规则）。
  const { provider, baseUrl } = resolveProfileEndpoint(
    selected ? profileById[selected] : undefined,
    Object.values(connectionById),
  );
  const thinkingOptions = selected ? getThinkingOptions(provider, baseUrl) : [];
  // 受控值：服务端 thinking_override；null/'' =「跟随档案」。
  const overrideValue = binding.thinking_override ?? '';
  // 首选档案不在档案列表（已删除）时收窄规则算不出 ⇒ 仍给出全量选项 + 保持现值可选。
  const currentOverrideInList =
    overrideValue === '' || thinkingOptions.some((o) => o.value === overrideValue);
  return (
    <div
      className="kv-list__row"
      data-testid={`cap-binding-${binding.capability}`}
      style={{ flexWrap: 'wrap' }}
    >
      <span className="kv-list__title">{label}</span>
      <span className="muted small">
        {binding.agents && binding.agents.length > 0
          ? `关联 Agent：${binding.agents.join('、')}`
          : ''}
      </span>
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
        <select
          value={selected}
          onChange={(e) => onChange(e.target.value)}
          disabled={busy}
          data-testid={`cap-binding-select-${binding.capability}`}
        >
          <option value="">未绑定（使用旧版默认配置）</option>
          {profiles.map((p: ModelProfile) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name}（{p.model}）
            </option>
          ))}
        </select>
        <select
          value={overrideValue}
          onChange={(e) => onThinkingChange(e.target.value)}
          disabled={busy || isUnbound}
          title={
            isUnbound
              ? '先绑定模型档案，才能设置环节强度'
              : '环节强度覆盖该环节候选链所有档案的思考档'
          }
          data-testid={`cap-binding-thinking-select-${binding.capability}`}
        >
          <option value="">跟随档案</option>
          {thinkingOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
          {!currentOverrideInList && overrideValue !== '' ? (
            <option value={overrideValue}>{overrideValue}（当前值）</option>
          ) : null}
        </select>
        <ThinkingOverrideBadge value={binding.thinking_override} capability={binding.capability} />
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
      {/* 候选链：首选 + 后备成员逐个可见、逐个可移除（顺序即 fallback 序）。 */}
      <div
        style={{
          flexBasis: '100%',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 6,
          alignItems: 'center',
        }}
        data-testid={`cap-binding-chain-${binding.capability}`}
      >
        <span className="muted small">候选链（按顺序后备）：</span>
        {chain.length === 0 ? (
          <span
            className="muted small"
            data-testid={`cap-binding-chain-empty-${binding.capability}`}
          >
            （空）
          </span>
        ) : (
          chain.map((pid, idx) => (
            <ChainMember
              key={pid}
              capability={binding.capability}
              label={label}
              profileId={pid}
              index={idx}
              meta={(binding.profiles ?? []).find((p) => p.profile_id === pid)}
              fallback={profileById[pid]}
              disabled={busy}
              onRemove={onRemove}
            />
          ))
        )}
      </div>
    </div>
  );
}

/**
 * 环节强度徽标：有覆盖时显示档位短标签（复用档案卡 THINKING_BADGE_LABEL 风格）；
 * 「跟随档案」显灰（字段存在但不覆盖——比完全不渲染更诚实，用户能看到入口）。
 */
function ThinkingOverrideBadge({
  value,
  capability,
}: {
  value: string | null;
  capability: string;
}) {
  if (!value) {
    return (
      <span
        className="muted small"
        data-testid={`cap-binding-thinking-badge-${capability}`}
      >
        跟随档案
      </span>
    );
  }
  const mode = value as ThinkingMode;
  return (
    <span
      className="badge badge--info"
      data-testid={`cap-binding-thinking-badge-${capability}`}
    >
      强度·{THINKING_BADGE_LABEL[mode] ?? value}
    </span>
  );
}

/** 候选链里的一个成员：显示档案名 + 位次 + 「移除」按钮。 */
function ChainMember({
  capability,
  label,
  profileId,
  index,
  meta,
  fallback,
  disabled,
  onRemove,
}: {
  capability: string;
  label: string;
  profileId: string;
  index: number;
  meta: { profile_id: string; name: string; model: string } | undefined;
  fallback: ModelProfile | undefined;
  disabled: boolean;
  onRemove: (profileId: string) => void;
}) {
  // 名字优先取 binding.profiles（后端给的链成员快照），缺失时退到档案列表，再缺失
  // （档案已不存在）就显示 id —— 不能崩，也不能只显示裸 id。
  const name = meta?.name ?? fallback?.name;
  const model = meta?.model ?? fallback?.model;
  const display = name ? (model ? `${name}（${model}）` : name) : `${profileId}（档案不存在）`;
  return (
    <span
      className="badge badge--info"
      data-testid={`cap-binding-chain-item-${capability}-${profileId}`}
    >
      <span data-testid={`cap-binding-chain-name-${capability}-${profileId}`}>{display}</span>
      <span> · {index === 0 ? '首选' : `后备 ${index}`}</span>
      <button
        type="button"
        className="btn btn--sm btn--danger"
        style={{ marginLeft: 6 }}
        disabled={disabled}
        title={`从「${label}」的候选链中移除 ${display}`}
        data-testid={`cap-binding-chain-remove-${capability}-${profileId}`}
        onClick={() => onRemove(profileId)}
      >
        移除
      </button>
    </span>
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
        style={{ color: 'var(--color-success)' }}
      >
        已保存 ✓
      </span>
    );
  if (state.kind === 'error')
    return (
      <span
        className="small"
        style={{ color: 'var(--color-error)' }}
        data-testid="cap-binding-error"
      >
        失败：{state.message}
      </span>
    );
  return null;
}
