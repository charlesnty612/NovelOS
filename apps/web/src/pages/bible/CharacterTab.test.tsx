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
  // 关键：core_json 里除了 5 个表单字段，还含 project_init 写入的非表单键
  core_json: {
    personality: '冷静',
    values: '正义',
    fears: '失去',
    desires: '真相',
    flaws: '固执',
    // —— 以下属于"非表单键"，保存时不应被覆盖清除 ——
    motivation: '为亡母复仇',
    goal: '揭开组织真相',
    conflict: '内外双重矛盾',
    relationship: { ally: '苏挽', foe: '赵靖' },
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

    // 改名 + 调整 personality（模拟用户在表单内修改）
    await user.clear(nameInput);
    await user.type(nameInput, '林远（新名）');
    // 通过 form 作用域定位 personality textarea，避免与详情面板里其他 textarea 混淆
    const dialog = screen.getByRole('dialog');
    const personalityTa = dialog.querySelectorAll('textarea')[0];
    await user.clear(personalityTa);
    await user.type(personalityTa, '冷静偏执');

    // 提交
    await user.click(screen.getByText('保存'));

    // 关键断言：update 被调用时，core_json 是"完整"对象
    // —— 表单字段更新（name 与 personality）
    // —— 非表单键（motivation / goal / conflict / relationship 等）原样保留
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
    expect(cj.personality).toBe('冷静偏执');
    // 其他表单字段保留
    expect(cj.values).toBe('正义');
    expect(cj.fears).toBe('失去');
    expect(cj.desires).toBe('真相');
    expect(cj.flaws).toBe('固执');
    // 关键：非表单键必须原样保留
    expect(cj.motivation).toBe('为亡母复仇');
    expect(cj.goal).toBe('揭开组织真相');
    expect(cj.conflict).toBe('内外双重矛盾');
    expect(cj.relationship).toEqual({ ally: '苏挽', foe: '赵靖' });
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
