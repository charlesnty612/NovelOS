// CanonTab 渲染冒烟测试（Sprint 11 下半）：
// - 挂载组件，mock referenceApi；
// - 断言标题、表单、列表渲染；
// - 选中 canon 后断言详情面板渲染（logline + report_md + canon_json）。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/endpoints', () => {
  return {
    referenceApi: {
      deconstruct: vi.fn(),
      uploadDeconstruct: vi.fn(),
      listCanons: vi.fn(),
      getCanon: vi.fn(),
      deleteCanon: vi.fn(),
      writeToStyleSample: vi.fn(),
    },
  };
});

import { referenceApi } from '../../api/endpoints';
import { CanonTab } from './CanonTab';

const SAMPLE_CANON_LIST = [
  {
    canon_id: 'can_001',
    project_id: 'p1',
    title: '测试参照书',
    reader_profile: 'male_fantasy',
    status: 'active',
    created_at: '2026-08-23T10:00:00',
    logline: '草根少年逆袭金手指',
    spine_count: 3,
    rhythm_chapter_count: 3,
  },
];

const SAMPLE_CANON_DETAIL = {
  canon_id: 'can_001',
  project_id: 'p1',
  title: '测试参照书',
  reader_profile: 'male_fantasy',
  status: 'active',
  created_at: '2026-08-23T10:00:00',
  canon_json: {
    logline: '草根少年逆袭金手指',
    spine: [{ chapter_index: 1 }, { chapter_index: 2 }, { chapter_index: 3 }],
    rhythm: {
      mini_climax_interval: { median: 3 },
      major_climax_interval: { median: 5 },
      chapter_end_hook_rate: 0.85,
    },
    style_params: {
      pov: 'third_limited',
      dialogue_ratio: 0.25,
      action_ratio: 0.45,
    },
    protagonist: {
      identity: '草根逆袭型主角',
      personality_tags: ['隐忍', '重情', '好强'],
      core_drive: '打破同辈压制登上巅峰',
      foil_techniques: ['前期压制-中期对等-后期反压', '同辈对照镜映主角成长'],
    },
    metadata: { source_book_title: '测试参照书' },
  },
  report_md:
    '# 整体节奏\n整体走钩子优先。\n\n## 第一卷\n- 开篇冲突\n- 章末留悬念',
  extracts: [
    { extract_id: 'ex_1', chapter_index: 1, extract_json: {}, created_at: '2026-08-23T10:00:00' },
  ],
};

describe('CanonTab (冒烟)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (referenceApi.listCanons as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      SAMPLE_CANON_LIST,
    );
    (referenceApi.getCanon as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      SAMPLE_CANON_DETAIL,
    );
  });

  it('挂载后渲染：拆书表单 + 列表 + 选中后的详情', async () => {
    render(<CanonTab projectId="p1" />);

    // 拆书表单
    expect(screen.getByTestId('deconstruct-form')).toBeInTheDocument();
    expect(screen.getByTestId('deconstruct-submit')).toBeInTheDocument();

    // 列表加载完后展示 1 行
    await waitFor(() => {
      expect(screen.getByTestId('canon-table')).toBeInTheDocument();
    });
    expect(screen.getByText('测试参照书')).toBeInTheDocument();

    // 选中第一行 → 加载详情
    const row = screen.getByTestId('canon-row');
    await userEvent.click(row);

    await waitFor(() => {
      expect(screen.getByTestId('canon-logline')).toBeInTheDocument();
    });
    expect(screen.getByTestId('canon-logline').textContent).toContain('草根少年');
    // report_md 渲染：标题与列表项
    expect(screen.getByText('整体节奏')).toBeInTheDocument();
    expect(screen.getByText('第一卷')).toBeInTheDocument();
    expect(screen.getByText('开篇冲突')).toBeInTheDocument();
    expect(screen.getByText('章末留悬念')).toBeInTheDocument();
    // canon_json 全文 JSON 块
    expect(screen.getByTestId('canon-json-block')).toBeInTheDocument();
    // v0.1.2：主角人设区段（条件渲染）
    await waitFor(() => {
      expect(screen.getByTestId('canon-protagonist-block')).toBeInTheDocument();
    });
    expect(screen.getByTestId('canon-protagonist-identity').textContent).toContain(
      '草根逆袭型主角',
    );
    expect(screen.getByTestId('canon-protagonist-core-drive').textContent).toContain(
      '打破同辈压制',
    );
    expect(screen.getByTestId('canon-protagonist-personality-tags').textContent).toContain(
      '隐忍',
    );
    expect(screen.getByTestId('canon-protagonist-foil-techniques').textContent).toContain(
      '前期压制-中期对等-后期反压',
    );
  });

  it('canon_json 无 protagonist → 不渲染主角人设区段', async () => {
    // 临时覆盖 mock：返回无 protagonist 的 detail
    (referenceApi.getCanon as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SAMPLE_CANON_DETAIL,
      canon_json: {
        logline: '草根少年逆袭金手指',
        spine: [{ chapter_index: 1 }],
        rhythm: { mini_climax_interval: { median: 3 } },
        style_params: { pov: 'third_limited' },
        metadata: { source_book_title: '测试参照书' },
        // 注意：没有 protagonist 键
      },
    });
    render(<CanonTab projectId="p1" />);
    const row = await waitFor(() => screen.getByTestId('canon-row'));
    await userEvent.click(row);
    await waitFor(() => {
      expect(screen.getByTestId('canon-logline')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('canon-protagonist-block')).toBeNull();
  });

  it('空列表展示 EmptyState', async () => {
    (referenceApi.listCanons as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce([]);
    render(<CanonTab projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByText('还没有参照系')).toBeInTheDocument();
    });
  });

  it('点击「写入文风样例面板」调 referenceApi.writeToStyleSample 并展示已保存反馈', async () => {
    (referenceApi.writeToStyleSample as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      sample_id: 'asty_test001',
      project_id: 'p1',
      title: '《测试参照书》拆书风格卡',
      content: 'logline：草根少年逆袭金手指\n',
      created_at: '2026-08-31T10:00:00',
      updated_at: '2026-08-31T10:00:00',
    });
    render(<CanonTab projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('canon-table')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId('canon-row'));
    await waitFor(() => {
      expect(screen.getByTestId('canon-write-to-style-sample')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId('canon-write-to-style-sample'));
    await waitFor(() => {
      expect(referenceApi.writeToStyleSample).toHaveBeenCalledWith('p1', 'can_001');
    });
    expect(
      screen.getByTestId('canon-write-to-style-sample-info'),
    ).toHaveTextContent('已写入文风样例面板');
  });

  it('点击「开始拆书」触发 referenceApi.deconstruct', async () => {
    (referenceApi.deconstruct as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: 'wfr_001',
      status: 'COMPLETED',
      current_node: null,
      project_id: 'p1',
      book_title: '新书',
      canon_id: 'can_002',
    });
    render(<CanonTab projectId="p1" />);
    await userEvent.type(screen.getByTestId('deconstruct-book-title'), '新书');
    await userEvent.type(screen.getByTestId('deconstruct-text'), '第一章 ...');
    await userEvent.click(screen.getByTestId('deconstruct-submit'));
    await waitFor(() => {
      expect(referenceApi.deconstruct).toHaveBeenCalledWith('p1', {
        book_title: '新书',
        text: '第一章 ...',
        reader_profile: 'male_fantasy',
      });
    });
  });

  // V3.x 文件上传：选择 .txt 文件后提交，调用 uploadDeconstruct（不走 deconstruct）。
  it('选择 .txt 文件后提交：调 referenceApi.uploadDeconstruct，传 File + 表单字段', async () => {
    (referenceApi.uploadDeconstruct as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: 'wfr_upload_001',
      status: 'COMPLETED',
      current_node: null,
      project_id: 'p1',
      book_title: '上传书',
      canon_id: 'can_upload_001',
    });
    render(<CanonTab projectId="p1" />);

    // 构造 File 用 DataTransfer（jsdom 环境）。
    const file = new File(['第一章 正文内容'], 'ref-book.txt', { type: 'text/plain' });
    const fileInput = screen.getByTestId('deconstruct-file') as HTMLInputElement;
    await userEvent.upload(fileInput, file);

    // 文件已选 → 文件信息展示出现
    expect(screen.getByTestId('deconstruct-file-info')).toHaveTextContent('ref-book.txt');

    // 选文件后 textarea placeholder 应切换为提示（互斥靠 placeholder + onChange 清空文件）
    expect((screen.getByTestId('deconstruct-text') as HTMLTextAreaElement).placeholder).toContain(
      '已选择文件',
    );

    // 填写书名 + 提交
    await userEvent.type(screen.getByTestId('deconstruct-book-title'), '上传书');
    await userEvent.click(screen.getByTestId('deconstruct-submit'));

    await waitFor(() => {
      expect(referenceApi.uploadDeconstruct).toHaveBeenCalledTimes(1);
    });
    // 不应调 deconstruct 路径
    expect(referenceApi.deconstruct).not.toHaveBeenCalled();

    const callArgs = (referenceApi.uploadDeconstruct as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    // 第一参：projectId；第二参：File 对象（name/contentSize 验证）
    expect(callArgs[0]).toBe('p1');
    expect(callArgs[1]).toBeInstanceOf(File);
    expect(callArgs[1].name).toBe('ref-book.txt');
    // 第三参：{book_title, reader_profile}
    expect(callArgs[2]).toEqual({
      book_title: '上传书',
      reader_profile: 'male_fantasy',
    });
  });

  // 文件与正文互斥：选文件后用户又编辑 textarea，文件应被清空。
  it('选文件后再编辑 textarea：文件被清空', async () => {
    render(<CanonTab projectId="p1" />);

    const file = new File(['内容'], 'a.txt', { type: 'text/plain' });
    const fileInput = screen.getByTestId('deconstruct-file') as HTMLInputElement;
    await userEvent.upload(fileInput, file);
    expect(screen.getByTestId('deconstruct-file-info')).toBeInTheDocument();

    // 用户改主意，在 textarea 里开始输入 → 应触发清空文件
    await userEvent.type(screen.getByTestId('deconstruct-text'), '重新粘贴');
    expect(screen.queryByTestId('deconstruct-file-info')).toBeNull();
    expect(screen.getByTestId('deconstruct-text')).toHaveValue('重新粘贴');
  });

  // 表单验证：选了文件后 placeholder 切换为「已选择文件，无需再粘贴正文」。
  it('选文件后：placeholder 切换为「已选择文件，无需再粘贴正文」', async () => {
    render(<CanonTab projectId="p1" />);
    const file = new File(['x'], 'b.epub', { type: 'application/epub+zip' });
    const fileInput = screen.getByTestId('deconstruct-file') as HTMLInputElement;
    await userEvent.upload(fileInput, file);
    const ta = screen.getByTestId('deconstruct-text') as HTMLTextAreaElement;
    expect(ta.placeholder).toContain('已选择文件');
    // epub 文件信息展示
    expect(screen.getByTestId('deconstruct-file-info')).toHaveTextContent('b.epub');
  });
});
