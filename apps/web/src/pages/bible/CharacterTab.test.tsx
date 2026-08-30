// CharacterTab 核心回归测试：
// - 编辑角色保存时 MUST 保留原 core_json 里的"非表单"键
//   （如后端 / project_init 写入的 motivation / goal / conflict / relationship）。
// - 表单字段（name / role / personality 等 5 个 CORE_FIELDS）正常更新。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/endpoints', () => {
  return {
    charactersApi: {
      listByProject: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      listStates: vi.fn(),
    },
  };
});

import { charactersApi } from '../../api/endpoints';
import { CharacterTab } from './CharacterTab';

const SAMPLE_CHARACTER = {
  character_id: 'char_001',
  project_id: 'p1',
  name: '林远',
  role: 'protagonist',
  // 表单 5 键对齐后端契约（project_init pipeline L1274-1284）：
  // motivation / goal / conflict / distinctive_trait / relationships。
  // 此外混入 1 个自定义非表单键（_project_init_marker）以验证 merge 不丢键。
  core_json: {
    motivation: '为亡母复仇',
    goal: '揭开组织真相',
    conflict: '内外双重矛盾',
    distinctive_trait: '左眼旧疤',
    relationships: { ally: '苏挽', foe: '赵靖' },
    // —— 非表单键，保存时不应被覆盖清除 ——
    _project_init_marker: 'untouched',
  },
  visibility: 'PUBLIC',
  who_knows: null,
  created_at: '2026-08-20T10:00:00',
  updated_at: '2026-08-25T10:00:00',
  latest_state_version: 3,
  latest_state_json: {},
};

describe('CharacterTab 编辑保存——core_json merge 语义', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (
      charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue([SAMPLE_CHARACTER]);
    (
      charactersApi.update as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue({ ...SAMPLE_CHARACTER, name: '林远（新名）' });
  });

  it('编辑角色保存时，提交体 core_json 保留所有非表单键，仅覆盖表单字段', async () => {
    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    // 列表加载完，点该行的"编辑"按钮（避免与详情面板里的同名文本冲突）
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '编辑' }).length).toBeGreaterThan(0);
    });
    const editButtons = screen.getAllByRole('button', { name: '编辑' });
    await user.click(editButtons[0]);

    // 编辑器打开：标题显示当前名 + 名称输入框已有 '林远'
    await waitFor(() => {
      expect(screen.getByText(/编辑角色：林远/)).toBeInTheDocument();
    });
    const nameInput = screen.getByTestId('character-name');
    expect((nameInput as HTMLInputElement).value).toBe('林远');

    // 改名 + 调整 motivation（模拟用户在表单内修改）
    await user.clear(nameInput);
    await user.type(nameInput, '林远（新名）');
    // 通过 form 作用域定位 motivation textarea（表单第 1 行），避免与详情面板里其他 textarea 混淆
    const dialog = screen.getByRole('dialog');
    const motivationTa = dialog.querySelectorAll('textarea')[0];
    await user.clear(motivationTa);
    await user.type(motivationTa, '为亡母复仇，偏执追寻真相');

    // 提交
    await user.click(screen.getByText('保存'));

    // 关键断言：update 被调用时，core_json 是"完整"对象
    // —— 表单字段更新（name 与 motivation）
    // —— 非表单键（_project_init_marker 等）原样保留
    await waitFor(() => {
      expect(charactersApi.update).toHaveBeenCalledTimes(1);
    });
    const [calledId, calledPayload] = (
      charactersApi.update as unknown as ReturnType<typeof vi.fn>
    ).mock.calls[0];

    expect(calledId).toBe('char_001');
    expect(calledPayload.name).toBe('林远（新名）');
    expect(calledPayload.core_json).toBeDefined();

    const cj = calledPayload.core_json as Record<string, unknown>;
    // 表单字段已更新
    expect(cj.motivation).toBe('为亡母复仇，偏执追寻真相');
    // 其他表单字段保留
    expect(cj.goal).toBe('揭开组织真相');
    expect(cj.conflict).toBe('内外双重矛盾');
    expect(cj.distinctive_trait).toBe('左眼旧疤');
    expect(cj.relationships).toEqual({ ally: '苏挽', foe: '赵靖' });
    // 关键：非表单键必须原样保留（防止 merge 逻辑误清空历史数据）
    expect(cj._project_init_marker).toBe('untouched');
  });

  it('新建角色时无需合并（提交体不含 motivation/goal 等非表单键假数据）', async () => {
    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    // 列表加载完（通过行内名称查询，限定到 td）
    await waitFor(() => {
      const cells = document.querySelectorAll('td');
      expect(Array.from(cells).some((c) => c.textContent === '林远')).toBe(true);
    });
    // 点右侧"+ 新建角色"
    const newButtons = screen.getAllByRole('button', { name: '+ 新建角色' });
    await user.click(newButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('新建角色')).toBeInTheDocument();
    });
    await user.type(screen.getByTestId('character-name'), '苏挽');
    await user.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(charactersApi.create).toHaveBeenCalledTimes(1);
    });
    const [, createPayload] = (
      charactersApi.create as unknown as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(createPayload.name).toBe('苏挽');
    // 新建路径核心不依赖 list 快照，因此不应凭空出现 motivation 等键
    const cj = (createPayload.core_json ?? {}) as Record<string, unknown>;
    expect(cj).not.toHaveProperty('motivation');
    expect(cj).not.toHaveProperty('goal');
  });

  it('编辑角色保存时，relationships 由 JSON 字符串往返回对象（不被字符串化破坏）', async () => {
    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '编辑' }).length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByRole('button', { name: '编辑' })[0]);

    await waitFor(() => {
      expect(screen.getByText(/编辑角色：林远/)).toBeInTheDocument();
    });

    // 编辑器打开后，relationships textarea 应预填 JSON 美化文本
    const dialog = screen.getByRole('dialog');
    const tas = dialog.querySelectorAll('textarea');
    // 表单顺序：motivation / goal / conflict / distinctive_trait / relationships
    // ⇒ 第 5 个 textarea 是 relationships
    const relTa = tas[4];
    const initialText = (relTa as HTMLTextAreaElement).value;
    expect(initialText).toContain('"ally"');
    expect(initialText).toContain('苏挽');

    // 修改：把对象改成数组，再保存
    await user.clear(relTa);
    // 用 paste 避开 userEvent.keyboard 对 [] 的描述符解析限制
    await user.paste(
      '[{"name":"苏挽","type":"ally"},{"name":"赵靖","type":"foe"}]',
    );

    await user.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(charactersApi.update).toHaveBeenCalledTimes(1);
    });
    const [, calledPayload] = (
      charactersApi.update as unknown as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    const cj = calledPayload.core_json as Record<string, unknown>;

    // relationships 必须是数组对象，不是字符串化的 JSON 文本
    expect(typeof cj.relationships).not.toBe('string');
    expect(Array.isArray(cj.relationships)).toBe(true);
    expect(cj.relationships).toEqual([
      { name: '苏挽', type: 'ally' },
      { name: '赵靖', type: 'foe' },
    ]);
  });

  it('编辑角色保存时，relationships 无法解析为 JSON 时按字符串保存（兜底）', async () => {
    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '编辑' }).length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByRole('button', { name: '编辑' })[0]);

    await waitFor(() => {
      expect(screen.getByText(/编辑角色：林远/)).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    const tas = dialog.querySelectorAll('textarea');
    const relTa = tas[4];
    await user.clear(relTa);
    await user.type(relTa, '与苏挽结盟，与赵靖为敌');

    await user.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(charactersApi.update).toHaveBeenCalledTimes(1);
    });
    const [, calledPayload] = (
      charactersApi.update as unknown as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    const cj = calledPayload.core_json as Record<string, unknown>;
    // 不是合法 JSON 的纯文本：按字符串存，避免被强行 stringify 破坏
    expect(cj.relationships).toBe('与苏挽结盟，与赵靖为敌');
  });

  it('编辑角色打开表单时，core_json 中的 motivation/goal/conflict/distinctive_trait/relationships 全部回显', async () => {
    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '编辑' }).length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByRole('button', { name: '编辑' })[0]);

    await waitFor(() => {
      expect(screen.getByText(/编辑角色：林远/)).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    const tas = dialog.querySelectorAll('textarea');
    expect(tas.length).toBeGreaterThanOrEqual(5);
    const [motivation, goal, conflict, trait, relationships] = Array.from(
      tas,
    ).slice(0, 5) as HTMLTextAreaElement[];

    expect(motivation.value).toBe('为亡母复仇');
    expect(goal.value).toBe('揭开组织真相');
    expect(conflict.value).toBe('内外双重矛盾');
    expect(trait.value).toBe('左眼旧疤');
    // relationships 是对象，textarea 内显示 JSON 美化文本
    expect(relationships.value).toContain('ally');
    expect(relationships.value).toContain('苏挽');
  });
});

// ----------------------------------------------------------------------------
// 列表渲染 / 删除 / 失败分支关键流
// ----------------------------------------------------------------------------

describe('CharacterTab 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (
      charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue([SAMPLE_CHARACTER]);
  });

  it('a) 列表加载：自动选中第一条 + 详情面板渲染 core_json + visibility 等字段', async () => {
    render(<CharacterTab projectId="p1" />);

    // 等待渲染：表格行出现 + 详情面板显示 core_json
    await waitFor(() => {
      // 行：包含名称「林远」的单元格
      const cells = document.querySelectorAll('td');
      expect(Array.from(cells).some((c) => c.textContent === '林远')).toBe(true);
    });
    // 详情面板 testid（CharacterDetail 内）
    expect(screen.getByTestId('character-core-json')).toBeInTheDocument();
    // 详情区显示 role + visibility（左侧列表 + 右侧详情均会出现，用 getAllByText）
    expect(screen.getAllByText('protagonist').length).toBeGreaterThan(0);
    expect(screen.getAllByText('PUBLIC').length).toBeGreaterThan(0);
  });

  it('b) 点行上的「删除」+ 确认 → 调 charactersApi.delete + reload', async () => {
    // 第二轮 reload 应返回空数组（删除生效）
    const listFn = charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>;
    listFn
      .mockResolvedValueOnce([SAMPLE_CHARACTER])
      .mockResolvedValueOnce([]);
    (
      charactersApi.delete as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue(undefined);

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    render(<CharacterTab projectId="p1" />);

    // 列表到位后取行上的「删除」按钮
    await waitFor(() => {
      const cells = document.querySelectorAll('td');
      expect(Array.from(cells).some((c) => c.textContent === '林远')).toBe(true);
    });
    // 限定到行（tr）内的删除按钮，避免与详情面板里的「删除」混淆
    const rowDeleteBtn = document.querySelector('tr button.btn--danger') as HTMLButtonElement;
    expect(rowDeleteBtn).toBeTruthy();
    await user.click(rowDeleteBtn);

    await waitFor(() => {
      expect(charactersApi.delete).toHaveBeenCalledWith('char_001');
    });
    // reload → listByProject 被调第二次
    expect(listFn).toHaveBeenCalledTimes(2);

    confirmSpy.mockRestore();
  });

  it('c) API 失败：listByProject 抛错 → ErrorBanner 显示错误，不渲染表格', async () => {
    (
      charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error('角色服务 503'));

    render(<CharacterTab projectId="p1" />);

    // 错误文本出现
    await waitFor(() => {
      expect(screen.getByText('角色服务 503')).toBeInTheDocument();
    });
    // 行级「删除」按钮不应渲染（说明 list 未成功）
    expect(document.querySelector('tr button.btn--danger')).toBeNull();
  });
});
