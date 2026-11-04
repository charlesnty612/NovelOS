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

  // C-44（2026-11-05）：优先级必须与服务端 `_extract_pause_payload` 一致。
  describe('current_node 优先（对齐服务端）', () => {
    it('多个节点都有 payload 时，取 current_node 那个（不是插入序第一个）', () => {
      const oldPayload = { stage: 'chapter-plan', message: '旧节点' };
      const currentPayload = { stage: 'chapter-review', message: '当前节点' };
      const ckpt = {
        // 插入序在前的旧节点
        plan: { __pause_payload__: oldPayload },
        author_review: { __pause_payload__: currentPayload },
      };
      expect(extractPausePayload(ckpt, 'author_review')).toEqual(currentPayload);
      // 不传 current_node 时仍是服务端的降级口径：插入序第一个
      expect(extractPausePayload(ckpt)).toEqual(oldPayload);
    });

    it('current_node 指向的节点没有 payload ⇒ 继续走顶层 / 插入序兜底', () => {
      const payload = { stage: 'chapter-review', message: '兜底' };
      const ckpt = {
        plan: { finished_at: 'x' },
        author_review: { __pause_payload__: payload },
      };
      expect(extractPausePayload(ckpt, 'plan')).toEqual(payload);
    });

    it('顶层兼容排在 current_node 之后（权威来源不被防御兼容遮蔽）', () => {
      const top = { stage: 'top', message: '顶层' };
      const node = { stage: 'node', message: '节点' };
      const ckpt = {
        __pause_payload__: top,
        author_review: { __pause_payload__: node },
      };
      expect(extractPausePayload(ckpt, 'author_review')).toEqual(node);
    });
  });
});
