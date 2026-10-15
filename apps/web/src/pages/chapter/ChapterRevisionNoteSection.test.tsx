// 改稿指令面板。
//
// 判别点：
// 1) **诚实说明常驻**，且明说「只有一格 / 可能不是你写的 / 自己不会改稿」——把它画成
//    「意见箱」就是骗作者，测试必须钉住这三句；
// 2) **已有内容回显**：来自 `GET /chapters/{cid}` 的 plan_json（没有 list 端点）；
// 3) **清空走删除路径且有确认**：提交空白 = 后端 `plan.pop`，不是存空串；
// 4) **状态锁定真的 disabled**，且给出路（COMMITTED → 重开返修）；
// 5) 父页面重渲染时**不能冲掉作者正在敲的字**（只在 prop 真的变了才回填）。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { chaptersApi } from '../../api/endpoints';
import { ApiError } from '../../api/client';
import type { Chapter, ChapterStatus } from '../../api/types';
import {
  ChapterRevisionNoteSection,
  readRevisionNote,
} from './ChapterRevisionNoteSection';

vi.mock('../../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../../api/endpoints')>(
    '../../api/endpoints',
  );
  return { ...actual, chaptersApi: { setRevisionNote: vi.fn() } };
});

const setRevisionNote = chaptersApi.setRevisionNote as unknown as ReturnType<typeof vi.fn>;

function makeChapter(
  plan: Record<string, unknown> = {},
  status: ChapterStatus = 'DRAFTED',
): Chapter {
  return {
    chapter_id: 'ch_1',
    project_id: 'p1',
    number: 1,
    title: '第一章',
    plan_json: plan,
    status,
    visibility: 'PRIVATE',
    who_knows: null,
    created_at: '2026-10-01T08:00:00Z',
    updated_at: '2026-10-01T08:00:00Z',
  };
}

const input = () => screen.getByTestId('revision-note-input');

describe('readRevisionNote（口径单点）', () => {
  it('字符串原样取；缺失 / 非字符串 ⇒ 空串（宁可少显示，不显示错东西）', () => {
    expect(readRevisionNote({ revision_note: '换个视角' })).toBe('换个视角');
    expect(readRevisionNote({})).toBe('');
    expect(readRevisionNote({ revision_note: 123 })).toBe('');
    expect(readRevisionNote(null)).toBe('');
  });
});

describe('ChapterRevisionNoteSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('已有内容回显（来自 plan_json，不是自己编的）', () => {
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: '结尾别再解释一遍伏笔' })}
      />,
    );
    expect(input()).toHaveValue('结尾别再解释一遍伏笔');
  });

  it('编辑后提交 ⇒ 发出去的是 trim 后的值，成功后回显服务端那份', async () => {
    const stored = makeChapter({ revision_note: '把视角切回周野' });
    setRevisionNote.mockResolvedValue(stored);
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      <ChapterRevisionNoteSection chapter={makeChapter({})} onSaved={onSaved} />,
    );

    await user.type(input(), '  把视角切回周野  ');
    await user.click(screen.getByTestId('revision-note-save'));

    await waitFor(() =>
      expect(setRevisionNote).toHaveBeenCalledWith('ch_1', '把视角切回周野'),
    );
    await waitFor(() => expect(input()).toHaveValue('把视角切回周野'));
    expect(onSaved).toHaveBeenCalledWith(stored);
  });

  it('清空 ⇒ 先弹确认，确认后才发空串（提交空白 = 后端删键，不是存空串）', async () => {
    setRevisionNote.mockResolvedValue(makeChapter({}));
    const user = userEvent.setup();
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: '旧指令' })}
      />,
    );

    await user.click(screen.getByTestId('revision-note-clear'));

    // 确认框没点之前一个字节都不发。
    expect(await screen.findByTestId('revision-note-confirm')).toBeInTheDocument();
    expect(setRevisionNote).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('revision-note-confirm-confirm'));
    await waitFor(() => expect(setRevisionNote).toHaveBeenCalledWith('ch_1', ''));
    expect(await screen.findByTestId('revision-note-flash')).toHaveTextContent('已清空');
  });

  it('取消确认 ⇒ 不发请求，内容原样留在框里', async () => {
    const user = userEvent.setup();
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: '旧指令' })}
      />,
    );

    await user.click(screen.getByTestId('revision-note-clear'));
    await user.click(await screen.findByTestId('revision-note-confirm-cancel'));

    expect(setRevisionNote).not.toHaveBeenCalled();
    expect(input()).toHaveValue('旧指令');
  });

  it('把内容删空后点保存 ⇒ 同样走确认，且提示写明是「删掉」', async () => {
    const user = userEvent.setup();
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: '旧指令' })}
      />,
    );

    await user.clear(input());
    expect(screen.getByTestId('revision-note-delete-hint')).toHaveTextContent('删掉');
    await user.click(screen.getByTestId('revision-note-save'));

    expect(await screen.findByTestId('revision-note-confirm')).toBeInTheDocument();
    expect(setRevisionNote).not.toHaveBeenCalled();
  });

  it('本章已定稿（COMMITTED）⇒ 表单禁用并给出路（不是只显示一句）', () => {
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: 'x' }, 'COMMITTED')}
      />,
    );
    expect(input()).toBeDisabled();
    expect(screen.getByTestId('revision-note-save')).toBeDisabled();
    const locked = screen.getByTestId('revision-note-locked');
    expect(locked).toHaveTextContent('COMMITTED');
    expect(locked).toHaveTextContent('重开返修');
  });

  it('RELEASED 也锁死，且文案不谎称可以重开返修（后端只接 COMMITTED）', () => {
    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({}, 'RELEASED')}
      />,
    );
    expect(input()).toBeDisabled();
    expect(screen.getByTestId('revision-note-locked')).toHaveTextContent('撤销发布标记');
  });

  it('没有内容时清空键禁用（清空一个空格子是无意义的请求）', () => {
    render(<ChapterRevisionNoteSection chapter={makeChapter({})} />);
    expect(screen.getByTestId('revision-note-clear')).toBeDisabled();
    expect(input()).toBeEnabled();
  });

  it('写入失败 ⇒ 错误可见，框里的字不丢', async () => {
    setRevisionNote.mockRejectedValue(new ApiError(500, 'db exploded'));
    const user = userEvent.setup();
    render(<ChapterRevisionNoteSection chapter={makeChapter({})} />);

    await user.type(input(), '新指令');
    await user.click(screen.getByTestId('revision-note-save'));

    expect(await screen.findByRole('alert')).toHaveTextContent('db exploded');
    expect(input()).toHaveValue('新指令');
  });

  it('409（父页面拿的是旧快照）⇒ 文案指向出路而不是抛状态码', async () => {
    setRevisionNote.mockRejectedValue(
      new ApiError(409, "chapter 'ch_1' status is 'COMMITTED'"),
    );
    const user = userEvent.setup();
    render(<ChapterRevisionNoteSection chapter={makeChapter({})} />);

    await user.type(input(), '新指令');
    await user.click(screen.getByTestId('revision-note-save'));

    expect(await screen.findByRole('alert')).toHaveTextContent('重开返修');
  });

  // 反向用例：三态（空 / 已有 / 已定稿）都必须把「只有一格 / 自己不会改稿」摆在明面上。
  it('诚实说明：三态都渲染，且明说只有一格、不会自己触发改稿', async () => {
    const { unmount } = render(<ChapterRevisionNoteSection chapter={makeChapter({})} />);
    let note = screen.getByTestId('revision-note-honest');
    expect(note).toHaveTextContent('这里只有一格');
    expect(note).toHaveTextContent('可能不是你刚才写的');
    expect(note).toHaveTextContent('它自己不会改稿');
    expect(note).toHaveTextContent('删掉');
    unmount();

    render(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: 'reopen: 上次返修' }, 'REVIEWED')}
      />,
    );
    note = screen.getByTestId('revision-note-honest');
    expect(note).toHaveTextContent('reopen: ');
  });

  it('父页面重渲染（同一份 chapter）⇒ 不冲掉作者正在敲的字', async () => {
    const chapter = makeChapter({});
    const user = userEvent.setup();
    const { rerender } = render(<ChapterRevisionNoteSection chapter={chapter} />);

    await user.type(input(), '正在写');
    rerender(<ChapterRevisionNoteSection chapter={{ ...chapter }} />);
    expect(input()).toHaveValue('正在写');
  });

  it('父页面真的换了一份 chapter ⇒ 才回填新值', () => {
    const { rerender } = render(<ChapterRevisionNoteSection chapter={makeChapter({})} />);
    rerender(
      <ChapterRevisionNoteSection
        chapter={makeChapter({ revision_note: '别处的指令' })}
      />,
    );
    expect(input()).toHaveValue('别处的指令');
  });
});
