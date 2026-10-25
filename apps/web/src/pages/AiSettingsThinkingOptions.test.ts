// =============================================================================
// 思考档收窄规则测试（协议 ∧ host）——2026-10-25 批 M4
// -----------------------------------------------------------------------------
// 覆盖：
// ① host 侧供应商专属档（max=Kimi / adaptive=MiniMax）——协议**不得**短路掉；
// ② 协议侧参数族可表达性（anthropic 不可表达 reasoning_effort 族）；
// ③ 两侧约束取交集；只有一侧有约束时取那侧；两侧都没有 ⇒ 全量 8 档；
// ④ 遗留 provider==='openai' 口径只在 host 未识别时生效。
//
// 判别力：① 的用例在「协议优先短路」实现下必红（anthropic + Kimi/MiniMax host
// 会退回全量 8 档，把供应商专属档显示错）；② 的用例在「只看 host」实现下必红。
// =============================================================================

import { describe, expect, it } from 'vitest';
import {
  THINKING_OPTIONS,
  getThinkingOptions,
  hostOf,
  inferThinkingMode,
  type ThinkingMode,
} from './AiSettingsThinkingOptions';

/** 取下拉里的档位值（顺序即展示顺序）。 */
function values(provider: string, baseUrl: string): string[] {
  return getThinkingOptions(provider, baseUrl).map((o) => o.value);
}

/** 8 档全集（用于「全量」断言，避免手抄漂移）。 */
const ALL_MODES: ThinkingMode[] = THINKING_OPTIONS.map((o) => o.value);

describe('getThinkingOptions · host 侧判定不被协议短路', () => {
  it('anthropic 协议 + MiniMax host ⇒ 按 MiniMax 收窄（adaptive 在列、high 不在列）', () => {
    // 修前：协议命中 anthropic ⇒ 提前 return 全量 8 档 ⇒ high/on/low 也在列。
    // 修后：协议约束 ∩ host 约束 ⇒ adaptive（host 专属档）活下来了。
    expect(values('anthropic', 'https://api.minimax.chat/v1')).toEqual([
      'default',
      'off',
      'adaptive',
    ]);
  });

  it('ollama 协议 + DeepSeek host ⇒ 按 DeepSeek 收窄（不再退回全量）', () => {
    // 修前：协议命中 ollama ⇒ 全量 8 档。
    expect(values('ollama', 'https://api.deepseek.com/v1')).toEqual([
      'default',
      'off',
      'low',
      'medium',
      'high',
    ]);
  });

  it('mock 协议 + Kimi host ⇒ 按 Kimi 收窄（max 仍在列）', () => {
    // 修前：协议命中 mock ⇒ 全量 8 档（把 Kimi 不支持的 on/adaptive 也列出来）。
    expect(values('mock', 'https://api.kimi.com/v1')).toEqual([
      'default',
      'off',
      'low',
      'high',
      'max',
    ]);
  });

  it('anthropic 协议 + Moonshot 官方域 ⇒ host 侧档位仍参与（on/adaptive 被 host 剔除）', () => {
    // 修前：全量 8 档；修后：Kimi host 侧不含 on/adaptive，且 anthropic 协议侧
    // 不含 reasoning_effort 族 ⇒ 交集只剩 default/off。
    expect(values('anthropic', 'https://api.moonshot.cn/v1')).toEqual([
      'default',
      'off',
    ]);
    expect(values('anthropic', 'https://api.moonshot.ai/v1')).toEqual([
      'default',
      'off',
    ]);
  });
});

describe('getThinkingOptions · 协议侧参数族可表达性', () => {
  it('anthropic 协议 ⇒ 不含 reasoning_effort 族（low/medium/high/max）', () => {
    // Anthropic Messages API 对未知请求体字段直接 400 ⇒ 这四档不可表达。
    const got = values('anthropic', 'https://api.anthropic.com');
    expect(got).toEqual(['default', 'off', 'on', 'adaptive']);
  });

  it('openai_compatible 协议 + 未知 host ⇒ 全量 8 档（协议侧无约束）', () => {
    expect(values('openai_compatible', 'https://gw.example.test/v1')).toEqual(ALL_MODES);
  });

  it('mock 协议 + 未知 host ⇒ 全量 8 档', () => {
    expect(values('mock', '')).toEqual(ALL_MODES);
  });
});

describe('getThinkingOptions · 协议 ∧ host 取交集', () => {
  it('openai_compatible + Kimi host ⇒ 交集 = Kimi 档位集', () => {
    expect(values('openai_compatible', 'https://api.kimi.com/v1')).toEqual([
      'default',
      'off',
      'low',
      'high',
      'max',
    ]);
  });

  it('anthropic + MiniMax host ⇒ 交集保留 adaptive（两侧都支持）', () => {
    expect(values('anthropic', 'https://api.minimax.chat/v1')).toEqual([
      'default',
      'off',
      'adaptive',
    ]);
  });

  it('anthropic + Kimi host ⇒ 交集只剩 default/off（max 需 reasoning_effort，anthropic 不可表达）', () => {
    expect(values('anthropic', 'https://api.kimi.com/v1')).toEqual(['default', 'off']);
  });

  it('openai_compatible + OpenAI 官方 host ⇒ OpenAI 档位集（不含 on/adaptive/max）', () => {
    const got = values('openai_compatible', 'https://api.openai.com/v1');
    expect(got).toEqual(['default', 'off', 'low', 'medium', 'high']);
  });
});

describe('getThinkingOptions · 遗留 provider==="openai" 口径', () => {
  it('host 未识别 + provider==="openai" ⇒ 按 OpenAI 档位收窄', () => {
    expect(values('openai', 'https://gw.example.test/v1')).toEqual([
      'default',
      'off',
      'low',
      'medium',
      'high',
    ]);
  });

  it('host 命中 Kimi 时，provider==="openai" 不得压掉 host 判定（max 保留）', () => {
    expect(values('openai', 'https://api.kimi.com/v1')).toContain('max');
  });
});

describe('hostOf / inferThinkingMode（回归）', () => {
  it('hostOf 抽出小写 host', () => {
    expect(hostOf('https://API.Kimi.com/v1')).toBe('api.kimi.com');
    expect(hostOf('http://127.0.0.1:11434')).toBe('127.0.0.1:11434');
    expect(hostOf('')).toBe('');
  });

  it('inferThinkingMode 回显 max 档（漏判会静默丢档）', () => {
    expect(inferThinkingMode({ reasoning_effort: 'max' })).toBe('max');
    expect(inferThinkingMode({ reasoning_effort: 'high' })).toBe('high');
    expect(inferThinkingMode({ thinking: { type: 'adaptive' } })).toBe('adaptive');
    expect(inferThinkingMode({})).toBe('default');
  });
});
