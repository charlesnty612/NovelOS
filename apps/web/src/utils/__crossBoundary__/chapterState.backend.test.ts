// 跨端看守 ①：`workflow_runs.error` 的**自由文本协议**（2026-10-15 批次（七））。
//
// 缺口来源
// --------
// `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md` §三 第 1 行
// 标为**高风险**：前端 `utils/chapterState.ts` 把后端写入 `workflow_runs.error` 的
// **自由文本**当协议解析——
//
//   精确匹配 'rejected'                  ⇒ 人工纯驳回
//   前缀匹配 'rejected-for-revision'     ⇒ 驳回并改稿
//   精确匹配 'cancelled by user'         ⇒ 用户主动停手（非真失败）
//
// 后端任何一处改文案，UI **静默降级**：作者看到红色徽标却不知道发生了什么。
// 更糟的是文档指出的叠加效应：`error` 文本**既不被显示、又被当作分类协议**
// ——同一个字段两头都没做实（G3：run 级 error 从不渲染）。
//
// ⚠️ 这条守卫**不重复抄字面量**：基准是从后端源码里解析出来的
// （`_Rejected` / `_RejectForRevision` 的 `super().__init__(...)` 实参、
// 以及 engine 里 cancel 写入点），前端的匹配函数必须与它一致。
// 后端改文案 ⇒ 这里立刻红，而不是等作者看错徽标。

import { describe, expect, it } from 'vitest';

import {
  isCancelledByUserNode,
  isRejectedForRevisionRun,
  isRejectedRun,
} from '../chapterState';
import {
  BACKEND,
  errorAssignmentLiterals,
  readBackendSource,
  superInitArgInClass,
} from './backendSource';

const reviewSrc = readBackendSource(BACKEND.chapterReviewPipeline);
const engineSrc = readBackendSource(BACKEND.engine);

/** 后端 `_RejectForRevision.__init__` 里 `super().__init__(...)` 的实参。 */
function backendRevisionError(): string {
  return superInitArgInClass(reviewSrc, '_RejectForRevision');
}

/** 后端 `_Rejected.__init__` 里 `super().__init__(...)` 的实参（纯驳回）。 */
function backendPlainRejectError(): string {
  return superInitArgInClass(reviewSrc, '_Rejected');
}

describe('跨端：error 文案协议（后端字面量 ↔ 前端匹配函数）', () => {
  it('纯驳回：后端写的标识值必须被 isRejectedRun 精确命中', () => {
    const backend = backendPlainRejectError();
    expect(
      isRejectedRun(backend),
      `后端 _Rejected 现在写 ${JSON.stringify(backend)}，而前端 isRejectedRun 认的是 ` +
        `'rejected'（精确）⇒ 人工纯驳回会被静默降级成「真失败」`,
    ).toBe(true);
  });

  it('驳回并改稿：后端写的标识值必须被 isRejectedForRevisionRun 命中', () => {
    const backend = backendRevisionError();
    expect(
      isRejectedForRevisionRun(backend),
      `后端 _RejectForRevision 现在写 ${JSON.stringify(backend)}，` +
        `而前端认的是 'rejected-for-revision' 前缀`,
    ).toBe(true);
  });

  it('⚠️ 两个标识必须仍互相区分——否则「顺序陷阱」的注释已失效', () => {
    // 前端的注释写着「必须先判 revision 再判 rejected，否则被裸 'rejected' 吞掉」。
    // 若后端把两者改成同一个值/互为前缀，那条注释就成了错的说明书。
    const plain = backendPlainRejectError();
    const rev = backendRevisionError();
    expect(
      isRejectedRun(rev),
      `后端把「驳回并改稿」的标识改成了 ${JSON.stringify(rev)}，` +
        `它现在会被 isRejectedRun **精确匹配**命中——两条路径撞成一个`,
    ).toBe(false);
    expect(isRejectedForRevisionRun(plain)).toBe(false);
  });

  it('顺序：先 revision 后 rejected 的分类结果必须与后端两态一致', () => {
    // 复刻调用方的顺序（ChapterRunPanel 的判定顺序）
    const classify = (error: string | null) => {
      if (isCancelledByUserNode(error)) return 'cancelled';
      if (isRejectedForRevisionRun(error)) return 'rejected-for-revision';
      if (isRejectedRun(error)) return 'rejected';
      return 'failed';
    };
    expect(classify(backendPlainRejectError())).toBe('rejected');
    expect(classify(backendRevisionError())).toBe('rejected-for-revision');
  });

  it('用户主动取消：engine 所有 cancel 写入点的字面量都必须被 isCancelledByUserNode 命中', () => {
    // ⚠️ engine 有**两处**取消写入（847 行 kwargs 形态、1179 行 SQL 字面量形态）。
    // 第一版读取器按 'cancelled by user' 过滤——突变把其中一处改成
    // 'cancelled-by-user' 后，它**不再包含搜索串**而被静默滤掉，只剩另一处
    // 仍匹配 ⇒ 守卫恒绿（CB-3 实测存活）。所以这里**先取全部、再在测试里过滤**。
    const all = errorAssignmentLiterals(engineSrc);
    // 判据用 'cancel'（突变不会破坏这个更宽的串，故突变留在视野里）
    const cancelAll = all.filter((v) => v.includes('cancel'));
    const cancelLiterals = [...new Set(cancelAll)];

    // 下限断言：engine 有两个取消写入点（847 kwargs / 1179 SQL），加上注释里的
    // 引用共 4 处匹配。用**下界**而非等值——正则也会扫到注释，条数随文档变动，
    // 钉死等值是脆的（第一版钉「恰好 2」就把自己写红了）。
    expect(
      cancelAll.length,
      `engine 里的 cancel 写入（含注释引用）应至少 2 处匹配，实测 ${cancelAll.length}：${JSON.stringify(cancelAll)}`,
    ).toBeGreaterThanOrEqual(2);

    // 核心断言：**每一种** cancel 标识都要被前端精确认出。
    // CB-3（把其中一处改成 'cancelled-by-user'）会在这里被抓——去重后得到两个
    // 取值，其中一个 isCancelledByUserNode 返回 false ⇒ 红。
    for (const literal of cancelLiterals) {
      expect(
        isCancelledByUserNode(literal),
        `engine 把取消标识写成 ${JSON.stringify(literal)}，而前端 isCancelledByUserNode ` +
          '只认精确的 "cancelled by user" ⇒ 用户主动停手会被显示成红色真失败',
      ).toBe(true);
    }

    // ⚠️ 残余边界（如实登记）：正则不区分代码与注释，若后端把写入改成变量拼接
    // （`error=CANCEL_ERROR`），这里仍会扫到注释里的字面量而**保持绿**。
    // 那时须人工核对——读取器的 docstring 已登记这条。
  });

  it('反例对照：真失败的 error 文本不得被误认成任何一种（防匹配写宽）', () => {
    // 后端注释明写：commit 校验失败的 error 含子串 'rejected'
    // （'observer delta failed validation...' 那类）但不是驳回。
    const genuine = 'commit failed: observer delta failed validation, 2 rejected items';
    expect(isRejectedRun(genuine)).toBe(false);
    expect(isRejectedForRevisionRun(genuine)).toBe(false);
    expect(isCancelledByUserNode(genuine)).toBe(false);
  });
});
