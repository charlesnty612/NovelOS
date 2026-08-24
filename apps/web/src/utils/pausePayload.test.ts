import { describe, expect, it } from 'vitest';
import { extractPausePayload } from './pausePayload';

describe('extractPausePayload', () => {
  it('顶层兼容：checkpointJson 自身含 __pause_payload__ 时直接返回', () => {
    const payload = { stage: 'chapter-review', message: '请人工决议' };
    expect(extractPausePayload({ __pause_payload__: payload })).toEqual(payload);
  });

  it('节点键下取值：从 author_review 节点键下的 __pause_payload__ 取出（主路径）', () => {
    const payload = {
      stage: 'chapter-review',
      message: '请人工决议',
      review_report: { word_count: 1000 },
    };
    const ckpt = {
      author_review: {
        __pause_payload__: payload,
        finished_at: '2026-08-24T10:00:00Z',
      },
      plan: { finished_at: '2026-08-24T09:00:00Z' },
    };
    expect(extractPausePayload(ckpt)).toEqual(payload);
  });

  it('无 __pause_payload__ 时返回 null（含 null / 非对象 / 空对象）', () => {
    expect(extractPausePayload(null)).toBeNull();
    expect(extractPausePayload(undefined)).toBeNull();
    expect(extractPausePayload('not-an-object')).toBeNull();
    expect(extractPausePayload({})).toBeNull();
    expect(
      extractPausePayload({
        author_review: { finished_at: '2026-08-24T10:00:00Z' },
      }),
    ).toBeNull();
  });
});
