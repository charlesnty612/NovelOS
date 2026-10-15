// 状态推演面板。
//
// 判别重点有三条，按「最容易静默出错」的顺序排：
//
// 1. **基底对象不能被污染**。本面板的全部前提是「深拷贝一份改」——如果编辑动作
//    直接改在 `GET /deltas` 返回的那个对象上，作者改一次，历史记录就被写脏了，
//    而且**没有任何报错**。用例直接拿夹具对象做断言（改完再读夹具本体），
//    而不是只断言「发出去的 payload 是新的」——后者在浅拷贝下也能过。
// 2. **机器字段必须原样继承**。清空 `evidence`/`confidence`/`risk_level` 不会让请求
//    报错（后端只看 schema 必填性），但会让推演结果失去全部可信度依据。
// 3. **空 diff 不能崩**。`StateDiff` 的每个键都可能在为空时整个省略
//    （后端「空则不写」），读法必须是 `?.` + `?? 0`。
//
// 反向用例：诚实说明必须逐条在场（去掉任何一条，这条就红）。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { chaptersApi, simulationApi, storyStateApi } from '../api/endpoints';
import type { SimulationResult, StateDiff } from '../api/types';
import {
  SimulationPanel,
  countStateDiff,
  defaultSimName,
  deriveDelta,
} from './SimulationPanel';

vi.mock('../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../api/endpoints')>(
    '../api/endpoints',
  );
  return {
    ...actual,
    chaptersApi: { ...actual.chaptersApi, listByProject: vi.fn() },
    storyStateApi: { ...actual.storyStateApi, listDeltas: vi.fn() },
    simulationApi: { run: vi.fn(), list: vi.fn(), get: vi.fn() },
  };
});

const listByProject = chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>;
const listDeltas = storyStateApi.listDeltas as unknown as ReturnType<typeof vi.fn>;
const simRun = simulationApi.run as unknown as ReturnType<typeof vi.fn>;

const chapter = (id: string, number: number, title: string) => ({
  chapter_id: id,
  project_id: 'p1',
  number,
  title,
  plan_json: {},
  status: 'COMMITTED',
  visibility: 'PUBLIC',
  who_knows: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
});

/**
 * 夹具逐字段照抄 `docs/state-model/schemas/state-delta.schema.json` 的必填面，
 * 外加后端 `queries.list_deltas` 会补上的 `status`（它**不在** schema 里）。
 * 每条用例都新建一次，避免上一条用例的污染串到下一条（那条污染正是本文件第 1 条
 * 判别要抓的东西——夹具本身共享就是自欺）。
 */
function baseDelta() {
  return {
    delta_id: 'dlt_original',
    delta_version: 1,
    schema_version: 'state-delta-v0',
    chapter_id: 'ch_1',
    workflow_run_id: 'wf_run_1',
    previous_state_version: 4,
    created_by: 'observer:v1',
    created_at: '2026-10-01T08:00:00Z',
    supersedes: null,
    notes: null,
    // ⚠️ schema 里没有 status，additionalProperties:false ⇒ 原样回传必 422。
    status: 'APPLIED',
    character_changes: [
      {
        change_id: 'chg_c1',
        op: 'update',
        target_id: 'chr_1',
        character_id: 'chr_1',
        facet: 'state',
        field: 'state.location',
        before: '青云镇',
        after: '青云镇',
        confidence: 0.82,
        evidence: { chapter_id: 'ch_1', excerpt: '他仍在青云镇。' },
        risk_level: 'LOW',
      },
    ],
    world_changes: [
      {
        change_id: 'chg_w1',
        op: 'update',
        target_id: 'wrl_1',
        world_kind: 'rule',
        world_id: 'wrl_1',
        field: 'statement',
        before: '灵气只在夜间可引',
        after: '灵气只在夜间可引',
        confidence: 0.6,
        evidence: { chapter_id: 'ch_1', excerpt: '入夜才有灵气。' },
        risk_level: 'MEDIUM',
      },
    ],
    relationship_changes: [
      {
        change_id: 'chg_r1',
        op: 'update',
        target_id: 'rel_1',
        from_character_id: 'chr_1',
        to_character_id: 'chr_2',
        relation_type: 'rival',
        confidence: 0.7,
        evidence: { chapter_id: 'ch_1', excerpt: '两人针锋相对。' },
        risk_level: 'LOW',
      },
    ],
    new_events: [
      {
        change_id: 'chg_e1',
        op: 'add',
        target_id: 'evt_1',
        event_id: 'evt_1',
        type: 'conflict',
        participants: ['chr_1'],
        time: { timeline_day: 3 },
        confidence: 0.5,
        evidence: { chapter_id: 'ch_1', excerpt: '两人交手。' },
        risk_level: 'LOW',
      },
    ],
    resolved_hooks: [
      {
        change_id: 'chg_h1',
        op: 'update',
        target_id: 'hk_1',
        hook_id: 'hk_1',
        to_status: 'ACTIVE',
        payoff_chapter_id: null,
        payoff_summary: '埋下但未收',
        confidence: 0.5,
        evidence: { chapter_id: 'ch_1', excerpt: '他记起了那把刀。' },
        risk_level: 'LOW',
      },
    ],
    new_hooks: [
      {
        change_id: 'chg_nh1',
        op: 'add',
        target_id: 'hk_2',
        hook_id: 'hk_2',
        name: '师父的旧账',
        importance: 0.7,
        description: '师父似乎欠着什么人',
        confidence: 0.4,
        evidence: { chapter_id: 'ch_1', excerpt: '师父避而不答。' },
        risk_level: 'LOW',
      },
    ],
    debt_changes: [
      {
        change_id: 'chg_d1',
        op: 'update',
        target_id: 'dbt_1',
        debt_id: 'dbt_1',
        status_before: 'open',
        status_after: 'open',
        severity_before: 0.4,
        severity_after: 0.4,
        deadline_chapter_id: null,
        confidence: 0.5,
        evidence: { chapter_id: 'ch_1', excerpt: '他还没还。' },
        risk_level: 'LOW',
      },
    ],
  };
}

const simResult = (over: Partial<SimulationResult> = {}): SimulationResult => ({
  simulation_id: 'br_1',
  branch_id: 'br_1',
  name: 'sim-20261015T120000Z',
  base_version: 4,
  applied: 1,
  diff: { version_a: 4, version_b: 5 },
  issues: [],
  branch_status: 'ARCHIVED',
  created_at: '2026-10-15T12:00:00Z',
  ...over,
});

/** 走完「选章 → 选基底 → 取基底」三步。 */
async function pickBase() {
  await screen.findByTestId('simulation-delta-picker');
  await userEvent.click(await screen.findByTestId('simulation-base-dlt_original'));
  await userEvent.click(screen.getByTestId('simulation-use-as-base'));
  await screen.findByTestId('simulation-editor');
}

/** 取「实际发出去」的那条 delta（run 的第一个入参）。 */
function sentDelta(): Record<string, unknown> {
  const [pid, deltas, name] = simRun.mock.calls[0];
  expect(pid).toBe('p1');
  expect(name).toBeTruthy();
  return deltas[0] as Record<string, unknown>;
}

describe('SimulationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listByProject.mockResolvedValue([chapter('ch_1', 1, '入夜'), chapter('ch_2', 2, '旧账')]);
    listDeltas.mockResolvedValue([baseDelta()]);
    simRun.mockResolvedValue(simResult());
  });

  // ── 选择流程 ──────────────────────────────────────────────────────────────
  it('按 number 列出章节；切换章节 ⇒ 重新取该章 delta', async () => {
    render(<SimulationPanel projectId="p1" />);
    const sel = await screen.findByTestId('simulation-chapter-select');
    expect(sel).toHaveTextContent('第1章 入夜');
    expect(sel).toHaveTextContent('第2章 旧账');
    expect(listDeltas).toHaveBeenCalledWith('p1', 'ch_1');

    await userEvent.selectOptions(sel, 'ch_2');
    await waitFor(() => expect(listDeltas).toHaveBeenCalledWith('p1', 'ch_2'));
  });

  it('未选基底时「以此为基底」禁用（空基底不能推演）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await screen.findByTestId('simulation-delta-picker');
    expect(screen.getByTestId('simulation-use-as-base')).toBeDisabled();
    await userEvent.click(screen.getByTestId('simulation-base-dlt_original'));
    expect(screen.getByTestId('simulation-use-as-base')).toBeEnabled();
  });

  it('本章没有 delta ⇒ 说明为什么没有基底，不给空表单', async () => {
    listDeltas.mockResolvedValue([]);
    render(<SimulationPanel projectId="p1" />);
    expect(await screen.findByTestId('simulation-no-deltas')).toHaveTextContent(
      '该章没有状态变更记录',
    );
    expect(screen.queryByTestId('simulation-editor')).not.toBeInTheDocument();
  });

  it('项目无章节 ⇒ 说明先写一章，不渲染选择器', async () => {
    listByProject.mockResolvedValue([]);
    render(<SimulationPanel projectId="p1" />);
    expect(await screen.findByTestId('simulation-no-chapters')).toBeInTheDocument();
    expect(screen.queryByTestId('simulation-chapter-select')).not.toBeInTheDocument();
  });

  // ── 派生：深拷贝 / 换 id / 继承机器字段 ───────────────────────────────────
  it('★ 编辑后基底对象未被污染（深拷贝）', async () => {
    const fixture = baseDelta();
    listDeltas.mockResolvedValue([fixture]);
    render(<SimulationPanel projectId="p1" />);
    await pickBase();

    await userEvent.clear(screen.getByTestId('edit-character-0-after'));
    await userEvent.type(screen.getByTestId('edit-character-0-after'), '临渊城');

    await userEvent.click(screen.getByTestId('simulation-run'));

    // ① 发出去的是改后的值
    const sent = sentDelta() as { character_changes: Array<Record<string, unknown>> };
    expect(sent.character_changes[0].after).toBe('临渊城');
    // ② 基底对象本体逐字未变 —— 只断言 ① 的话，浅拷贝也能过
    expect(fixture.character_changes[0].after).toBe('青云镇');
    expect(fixture.character_changes[0].field).toBe('state.location');
    expect(fixture.delta_id).toBe('dlt_original');
    // ③ 基底里那些本轮没编辑的数组也不能被动（最隐蔽的一种污染）
    expect(fixture.resolved_hooks[0].payoff_summary).toBe('埋下但未收');
    expect(fixture.debt_changes[0].severity_after).toBe(0.4);
  });

  it('★ delta_id 被重新生成，且与原 id 不同（撞主键 = IntegrityError）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    const shown = screen.getByTestId('simulation-draft-id').textContent ?? '';
    expect(shown).not.toBe('dlt_original');
    expect(shown).toMatch(/^dlt_sim_/);

    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta();
    expect(sent.delta_id).not.toBe('dlt_original');
    expect(sent.delta_id).toBe(shown);
  });

  it('★ 机器字段从原元素继承、未被清空', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta() as {
      character_changes: Array<Record<string, unknown>>;
      resolved_hooks: Array<Record<string, unknown>>;
      supersedes: unknown;
      created_by: unknown;
    };
    const c = sent.character_changes[0];
    expect(c.change_id).toBe('chg_c1');
    expect(c.op).toBe('update');
    expect(c.target_id).toBe('chr_1');
    expect(c.character_id).toBe('chr_1');
    expect(c.facet).toBe('state');
    expect(c.confidence).toBe(0.82);
    expect(c.risk_level).toBe('LOW');
    expect(c.evidence).toEqual({ chapter_id: 'ch_1', excerpt: '他仍在青云镇。' });
    // 新值仍要能带出去
    expect(c.after).toBe('青云镇');
    // derived 件不是重试 ⇒ supersedes 必须置空（否则会把原 delta 标成 superseded）
    expect(sent.supersedes).toBeNull();
    expect(sent.created_by).toBe('simulation:ui');
    expect(sent.resolved_hooks[0].confidence).toBe(0.5);
  });

  it('★ 不发 schema 不认的键（行里多出的 status 必须被剔掉，否则必 422）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta();
    // schema additionalProperties:false ⇒ 顶层只能有 10 元信息 + 7 数组
    expect(Object.keys(sent).sort()).toEqual(
      [
        'chapter_id',
        'character_changes',
        'created_at',
        'created_by',
        'debt_changes',
        'delta_id',
        'delta_version',
        'new_events',
        'new_hooks',
        'notes',
        'previous_state_version',
        'relationship_changes',
        'resolved_hooks',
        'schema_version',
        'supersedes',
        'workflow_run_id',
        'world_changes',
      ].sort(),
    );
    expect(sent).not.toHaveProperty('status');
  });

  it('★ 基底是重试件时也不继承 supersedes（否则跑一次推演就把原 delta 标成已废弃）', async () => {
    listDeltas.mockResolvedValue([{ ...baseDelta(), supersedes: 'dlt_prev' }]);
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta();
    expect(sent.supersedes).toBeNull();
  });

  it('只读数组原样透传（照搬，不是丢弃）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    expect(screen.getByTestId('readonly-relationship_changes')).toBeInTheDocument();
    expect(screen.getByTestId('readonly-new_events')).toBeInTheDocument();
    expect(screen.getByTestId('readonly-new_hooks')).toHaveTextContent('师父的旧账');

    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta() as { new_hooks: Array<Record<string, unknown>> };
    expect(sent.new_hooks[0].hook_id).toBe('hk_2');
    expect(sent.new_hooks[0].change_id).toBe('chg_nh1');
  });

  // ── 业务字段编辑 ──────────────────────────────────────────────────────────
  it('角色 / 世界观 / 伏笔 / 债务四组都能改，且改的是发出去的那份', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();

    await userEvent.selectOptions(screen.getByTestId('edit-resolved-0-to_status'), 'RESOLVED');
    const payoff = screen.getByTestId('edit-resolved-0-payoff_summary');
    await userEvent.clear(payoff);
    await userEvent.type(payoff, '第2章还清旧账');
    const deadline = screen.getByTestId('edit-debt-0-deadline_chapter_id');
    await userEvent.clear(deadline);
    await userEvent.type(deadline, 'ch_2');
    await userEvent.selectOptions(screen.getByTestId('edit-debt-0-status_after'), 'paid');
    const world = screen.getByTestId('edit-world-0-after');
    await userEvent.clear(world);
    await userEvent.type(world, '灵气昼夜皆可引');

    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta() as {
      world_changes: Array<Record<string, unknown>>;
      resolved_hooks: Array<Record<string, unknown>>;
      debt_changes: Array<Record<string, unknown>>;
    };
    expect(sent.resolved_hooks[0].to_status).toBe('RESOLVED');
    expect(sent.resolved_hooks[0].payoff_summary).toBe('第2章还清旧账');
    expect(sent.debt_changes[0].status_after).toBe('paid');
    expect(sent.debt_changes[0].deadline_chapter_id).toBe('ch_2');
    expect(sent.world_changes[0].after).toBe('灵气昼夜皆可引');
  });

  it('after 输入：能 parse 的按 JSON 取（对象/数字），不能的按原样字符串', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();

    // 用 fireEvent 而非 userEvent.type：userEvent 把 `{`/`[` 当特殊键描述符起始，
    // 这里要的是「作者原样敲进去的一段文本」，不是键序列。
    fireEvent.change(screen.getByTestId('edit-character-0-after'), {
      target: { value: '{"hp":3}' },
    });
    fireEvent.change(screen.getByTestId('edit-world-0-after'), { target: { value: '7' } });

    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta() as {
      character_changes: Array<Record<string, unknown>>;
      world_changes: Array<Record<string, unknown>>;
    };
    expect(sent.character_changes[0].after).toEqual({ hp: 3 });
    expect(sent.world_changes[0].after).toBe(7);
  });

  it('after 输入：非 JSON 文本按原样字符串（作者写地名不是写 JSON）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.clear(screen.getByTestId('edit-character-0-after'));
    await userEvent.type(screen.getByTestId('edit-character-0-after'), '临渊城');
    await userEvent.click(screen.getByTestId('simulation-run'));
    const sent = sentDelta() as { character_changes: Array<Record<string, unknown>> };
    expect(sent.character_changes[0].after).toBe('临渊城');
  });

  // ── 结果渲染 ──────────────────────────────────────────────────────────────
  it('★ 空 diff（所有键都省略）不崩，显示「无变化」', async () => {
    simRun.mockResolvedValue(simResult({ diff: {} as StateDiff }));
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    const diff = await screen.findByTestId('simulation-diff');
    expect(within(diff).getByTestId('simulation-diff-empty')).toHaveTextContent('无变化');
    expect(screen.queryByTestId('diff-characters')).not.toBeInTheDocument();
  });

  it('diff 按 新增 / 移除 / 变更 分组渲染', async () => {
    simRun.mockResolvedValue(
      simResult({
        diff: {
          version_a: 4,
          version_b: 5,
          characters: {
            added: [{ character_id: 'chr_9' }],
            removed: [{ character_id: 'chr_8' }],
            changed: [
              { id: 'chr_1', changes: { 'state.location': { before: '青云镇', after: '临渊城' } } },
            ],
          },
          world: { 'wrl_1.statement': { before: '夜引', after: '昼夜' } },
        },
      }),
    );
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));

    const c = await screen.findByTestId('diff-characters');
    expect(c).toHaveTextContent('新增 1');
    expect(c).toHaveTextContent('移除 1');
    expect(c).toHaveTextContent('变更 1');
    expect(c).toHaveTextContent('chr_9');
    expect(c).toHaveTextContent('state.location');
    expect(c).toHaveTextContent('青云镇');
    expect(screen.getByTestId('diff-world')).toHaveTextContent('wrl_1.statement');
    expect(screen.queryByTestId('simulation-diff-empty')).not.toBeInTheDocument();
  });

  it('★ issues 非空 ⇒ 逐条显示 errors（含「第 N 条」序号）', async () => {
    simRun.mockResolvedValue(
      simResult({
        applied: 0,
        issues: [
          { index: 0, errors: ['[schema] character_changes/0: after 必填'] },
          { index: 1, errors: ['[schema] chapter_id 必填', '[business] 伏笔不存在'] },
        ],
      }),
    );
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));

    const box = await screen.findByTestId('simulation-issues');
    expect(box).toHaveTextContent('校验报错（2）');
    expect(within(box).getByTestId('simulation-issue-0')).toHaveTextContent('第 1 条');
    expect(within(box).getByTestId('simulation-issue-0')).toHaveTextContent(
      '[schema] character_changes/0: after 必填',
    );
    expect(within(box).getByTestId('simulation-issue-1')).toHaveTextContent('第 2 条');
    expect(within(box).getByTestId('simulation-issue-1')).toHaveTextContent('伏笔不存在');
    // 部分应用也要说出来，不能只显示报错
    expect(screen.getByTestId('simulation-applied')).toHaveTextContent('已应用 0 条');
  });

  it('issues 为空 ⇒ 不渲染报错区（空报错区会被训练成噪声）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    await screen.findByTestId('simulation-result');
    expect(screen.queryByTestId('simulation-issues')).not.toBeInTheDocument();
  });

  // ── 诚实说明（反向用例：少一条就红）──────────────────────────────────────
  it('★ 诚实说明逐条在场：不是只读 / 绕过审批 / 失败留残骸+409 / 不调 LLM', async () => {
    render(<SimulationPanel projectId="p1" />);
    await screen.findByTestId('simulation-panel');
    expect(screen.getByTestId('simulation-honest-1')).toHaveTextContent('不是只读');
    expect(screen.getByTestId('simulation-honest-1')).toHaveTextContent('state_deltas');
    expect(screen.getByTestId('simulation-honest-2')).toHaveTextContent('绕过审批门');
    expect(screen.getByTestId('simulation-honest-2')).toHaveTextContent('simulation_bypassed');
    expect(screen.getByTestId('simulation-honest-3')).toHaveTextContent('失败也留残骸');
    expect(screen.getByTestId('simulation-honest-3')).toHaveTextContent('409');
    expect(screen.getByTestId('simulation-honest-4')).toHaveTextContent('不调用 LLM');
  });

  it('诚实说明在**取基底之前**就能看见（不是折叠里的脚注）', async () => {
    render(<SimulationPanel projectId="p1" />);
    expect(screen.getByTestId('simulation-honest-notes')).toBeInTheDocument();
    // 此刻编辑器还不存在
    expect(screen.queryByTestId('simulation-editor')).not.toBeInTheDocument();
  });

  it('只读区写明「为什么不能改」（否则作者以为没做完）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    expect(screen.getByTestId('simulation-readonly-arrays')).toHaveTextContent('只读');
    expect(screen.getByTestId('simulation-readonly-arrays')).toHaveTextContent('evidence');
  });

  // ── 名字 / 错误态 ──────────────────────────────────────────────────────────
  it('默认名带时间戳（同名即 409，不能让作者自己踩）', async () => {
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    const name = (screen.getByTestId('simulation-name-input') as HTMLInputElement).value;
    expect(name).toMatch(/^sim-\d{8}T\d{6}Z$/);
    // 再取一次基底应换一个新名字（否则重跑必撞自己上一次）
    await userEvent.click(screen.getByTestId('simulation-use-as-base'));
    expect((screen.getByTestId('simulation-name-input') as HTMLInputElement).value).toMatch(
      /^sim-\d{8}T\d{6}Z$/,
    );
  });

  it('推演失败（409 之类）⇒ 错误原文可见，不静默', async () => {
    simRun.mockRejectedValue(new Error('ApiError(409): branch_name_conflict'));
    render(<SimulationPanel projectId="p1" />);
    await pickBase();
    await userEvent.click(screen.getByTestId('simulation-run'));
    expect(await screen.findByText(/branch_name_conflict/)).toBeInTheDocument();
    expect(screen.queryByTestId('simulation-result')).not.toBeInTheDocument();
  });

  it('章节加载失败 ⇒ 错误可见', async () => {
    listByProject.mockRejectedValue(new Error('项目服务异常'));
    render(<SimulationPanel projectId="p1" />);
    expect(await screen.findByText(/项目服务异常/)).toBeInTheDocument();
  });

  it('delta 加载失败 ⇒ 错误可见（不显示成「本章没有 delta」）', async () => {
    listDeltas.mockRejectedValue(new Error('状态变更读取失败'));
    render(<SimulationPanel projectId="p1" />);
    expect(await screen.findByText(/状态变更读取失败/)).toBeInTheDocument();
    expect(screen.queryByTestId('simulation-no-deltas')).not.toBeInTheDocument();
  });
});

describe('deriveDelta（派生口径单点）', () => {
  it('缺元字段时按 schema 补齐，不产出必填面残缺的 delta', () => {
    const d = deriveDelta({ delta_id: 'x' } as never, 'ch_9');
    expect(d.delta_version).toBe(1);
    expect(d.schema_version).toBe('state-delta-v0');
    expect(d.chapter_id).toBe('ch_9');
    expect(d.previous_state_version).toBe(1);
    for (const k of [
      'character_changes',
      'world_changes',
      'relationship_changes',
      'new_events',
      'resolved_hooks',
      'new_hooks',
      'debt_changes',
    ]) {
      expect(Array.isArray(d[k])).toBe(true);
    }
  });

  it('基底自带 chapter_id 时以基底为准', () => {
    expect(deriveDelta(baseDelta(), 'ch_9').chapter_id).toBe('ch_1');
  });

  it('★ 逐层深拷贝（引用不相等），不是浅拷贝换了个顶层对象', () => {
    // 只断言「改了不发脏」是**测不到**深拷贝这条机制的：只要 patch 端保持不可变更新，
    // 浅拷贝也照样不污染基底（2026-10-15 实测：把 clone() 去掉，29 条仍全绿）。
    // 断言必须落在 deriveDelta 自己身上——它是被导出的函数，别处也会调。
    const base = baseDelta();
    const d = deriveDelta(base, 'ch_1');
    for (const k of [
      'character_changes',
      'world_changes',
      'relationship_changes',
      'new_events',
      'resolved_hooks',
      'new_hooks',
      'debt_changes',
    ] as const) {
      expect((d[k] as unknown[])[0]).not.toBe((base[k] as unknown[])[0]);
      expect(d[k]).not.toBe(base[k]);
    }
    const c0 = (d.character_changes as Array<Record<string, unknown>>)[0];
    expect(c0.evidence).not.toBe(base.character_changes[0].evidence);
  });

  it('★ 基底自己是重试件（supersedes 非空）⇒ 派生件也不继承', () => {
    // `submit_delta` 会把被 supersedes 指向的行 UPDATE 成 'superseded'，而
    // `state_deltas` 是真实历史表 —— 继承 = 跑一次推演把别人的 delta 标成已废弃。
    // 夹具必须带非空 supersedes 才判别得了：base 为 null 时「继承」与「置空」同形。
    const d = deriveDelta({ ...baseDelta(), supersedes: 'dlt_prev' }, 'ch_1');
    expect(d.supersedes).toBeNull();
  });
});

describe('defaultSimName', () => {
  it('形如 sim-YYYYMMDDTHHMMSSZ（口径对齐后端 _now_compact_ts）', () => {
    expect(defaultSimName()).toMatch(/^sim-\d{8}T\d{6}Z$/);
  });
});

describe('countStateDiff', () => {
  it('所有键都省略 ⇒ 0', () => {
    expect(countStateDiff({ version_a: 1, version_b: 2 })).toBe(0);
  });

  it('键存在但数组为空 ⇒ 仍是 0（空 diff 与有键空 diff 同义）', () => {
    expect(
      countStateDiff({ version_a: 1, version_b: 2, hooks: { added: [], removed: [], changed: [] } }),
    ).toBe(0);
  });

  it('逐组累加（list diff + world 字段 + events_changed 键）', () => {
    const diff: StateDiff = {
      version_a: 1,
      version_b: 2,
      characters: { added: [{ id: 'a' }], changed: [{ id: 'b' }] },
      debts: { removed: [{ id: 'c' }] },
      world: { a: { before: 1, after: 2 }, b: { before: 1, after: 2 } },
      events_changed: { e1: {} },
    };
    // characters 新增1+变更1=2 · debts 移除1=1 · world 两项=2 · events_changed 一项=1
    expect(countStateDiff(diff)).toBe(6);
  });
});
