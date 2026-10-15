// 跨端看守 ③：`__pause_payload__` 的**私有序列化布局**（2026-10-15 批次（七））。
//
// 缺口来源
// --------
// `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md` §三 第 3 行
// 标为**高风险**：`utils/pausePayload.ts` 复刻了 `workflow_runtime/engine.py` 的
// checkpoint 私有布局来取暂停载荷。文档原话：「解析的是**私有序列化格式**，不是公开
// API 契约——引擎侧一改布局，审批卡静默拿不到 payload」。
//
// 审批卡拿不到 payload = **作者看不到要批什么**（评估 §一① 说审批卡的信息充分度是
// 本仓强项，而这条风险正好打在那个强项上）。
//
// ⚠️ 基准来自 engine 源码里那行写入语句本身：
//
//     ctx[node.node_id] = {"__pause_payload__": payload}
//
// 即布局是「**节点键** → 单一内层键 `__pause_payload__`」。本守卫解析出这个键名
// 与嵌套层数，再验证前端的提取函数与之相符——而不是把 `__pause_payload__`
// 再抄一遍到测试里（那样后端改名守卫照样绿）。
//
// 残余边界：前端**另外**还做了「顶层 `__pause_payload__` 兼容兜底」，那是为早期
// 格式留的，后端现在不再那样写。本守卫不断言那条兜底的对错，只钉住当前写入布局。

import { describe, expect, it } from 'vitest';

import { extractPausePayload } from '../pausePayload';
import { BACKEND, readBackendSource } from './backendSource';

const engineSrc = readBackendSource(BACKEND.engine);

/**
 * 从 engine 源码里解析出暂停载荷的写入布局：
 *   - `markerKey`：内层那个标记键（如 `__pause_payload__`）；
 *   - `nesting`：需要下钻的层数（当前为 1：顶层 → 节点键 → 标记键）。
 */
function parsePauseLayout(source: string): { markerKey: string; writeLine: string } {
  // 找 `ctx[<...>] = {"<MARKER>": ...}` 这行的形态
  const re = /ctx\[[^\]]+\]\s*=\s*\{\s*["']([^"']+)["']\s*:/g;
  const hits = [...source.matchAll(re)];
  if (hits.length === 0) {
    throw new Error(
      'engine 里找不到「ctx[...] = {"<marker>": ...}」形态的暂停写入语句——' +
        '布局可能改了（或换成变量/常量拼接）。注意：这时**前端提取会失效**，' +
        '审批卡会静默拿不到 payload，别放过。',
    );
  }
  const markerKey = hits[0][1];
  const writeLine = source.slice(hits[0].index, source.indexOf('\n', hits[0].index!));
  return { markerKey, writeLine };
}

const layout = parsePauseLayout(engineSrc);

describe('跨端：checkpoint 暂停布局（engine.py 写入 ↔ pausePayload.ts 提取）', () => {
  it('写入键名必须被前端提取器认出来', () => {
    // 构造后端当前布局的忠实样本：顶层 → 节点键 → 标记键
    const nodeKey = 'author_review';
    const body = { stage: 'review', message: '请审阅' };
    const checkpoint = { [nodeKey]: { [layout.markerKey]: body } };

    expect(
      extractPausePayload(checkpoint),
      `engine 现在把载荷写在 ctx[node] = {${JSON.stringify(layout.markerKey)}: ...}，` +
        `而前端 extractPausePayload 认的键名不同 ⇒ 审批卡拿不到 payload（作者看不到要批什么）。` +
        `写入语句：${layout.writeLine}`,
    ).toEqual(body);
  });

  it('标记键在**节点键之下**（不是顶层）——当前布局是嵌套的', () => {
    // 这是「下钻一层」的判别。若后端改成顶层平铺，前端仍能靠兼容分支兜住；
    // 本用例把「当前就是嵌套的」记下来，改布局时这里会提示需要同步。
    const checkpoint = { alpha: { [layout.markerKey]: { a: 1 } } };
    expect(extractPausePayload(checkpoint)).toEqual({ a: 1 });
  });

  it('非字典载荷也要能取到（engine 对非 dict 包了 {"payload": ...}）', () => {
    // engine: `payload = exc.payload if isinstance(exc.payload, dict) else {"payload": exc.payload}`
    // ⇒ 前端不必知道这个包装细节，但取到的必须是那个对象本身。
    const wrapped = { payload: 'raw-string' };
    const checkpoint = { n: { [layout.markerKey]: wrapped } };
    expect(extractPausePayload(checkpoint)).toEqual(wrapped);
  });

  it('布局不匹配时返回 null 而不是抛错（前端契约：拿不到就给空态）', () => {
    // 反向：这条守的是「提取器本身的行为契约没变」——
    // 若有人把它改成抛异常，审批卡会整页崩而不是显示空态。
    expect(extractPausePayload({ unrelated: { nested: 1 } })).toBeNull();
    expect(extractPausePayload(null)).toBeNull();
    expect(extractPausePayload('not-an-object')).toBeNull();
  });

  it('多节点 checkpoints 里取到的是**带载荷的那个**（不是第一个键）', () => {
    // 真实 checkpoint_json 里有多个节点键（build_ctx / writer / author_review…），
    // 只有暂停的那个带标记。若提取器退化成「取第一个子对象」，这条会红。
    const body = { stage: 'review' };
    const checkpoint = {
      build_ctx: { assembled: true },
      write_node: { draft_id: 'd1' },
      author_review: { [layout.markerKey]: body },
    };
    expect(extractPausePayload(checkpoint)).toEqual(body);
  });
});
