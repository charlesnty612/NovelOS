// 跨端契约看守的**共享读取器**（2026-10-15 批次（七））。
//
// 为什么要读后端源文件
// --------------------
// 前端有三处**复刻了后端的私有约定**（不是公开 API 契约）：
//   1. `utils/chapterState.ts` 把后端写入 `workflow_runs.error` 的**自由文本**
//      当协议解析（精确匹配 'rejected' / 前缀匹配 'rejected-for-revision' /
//      精确匹配 'cancelled by user'）；
//   2. `utils/ledgerState.ts` 手抄了 Hook 五态机 + Debt 四态机的合法后继表；
//   3. `utils/pausePayload.ts` 复刻了 `workflow_runtime/engine.py` 的
//      `__pause_payload__` 私有序列化布局。
//
// `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md` §三
// 给这三处标了「中/高漂移风险」，并写明对策是**测试看守，不是删除**。这批就是那些看守。
//
// ⚠️ 关键设计：**基准必须来自被读的源文件本身，不能硬编码在前端测试里**
// ---------------------------------------------------------------------
// 如果守卫写成「断言后端写的就是 'rejected'」（把字面量再抄一遍到测试里），
// 那就只是把同一个字面量抄到了第三处——后端改了、前端没改，测试照样绿，
// 因为它比的是「我自己抄的第二份」。
// 正确形状是**从后端源码里解析出事实**（G 状态机的邻接表、异常类携带的标识值、
// 写入点的键名），再断言前端的复刻与它一致。这样后端一改，守卫立刻红。
//
// ⚠️ 本文件**不引入任何构建期依赖**：只用 `node:fs` 读文本 + 正则提取。
// 读源码做契约基准在本仓有先例（`tests/api/test_readability_rules.py` 的
// 「源码级形状断言」），不是新发明的做法。
//
// 残余边界（如实登记，不夸大）
// --------------------------
// * 正则提取的是**字面形态**，不是语义。后端把 `_Rejected` 改成从变量拼字符串
//   （如 `super().__init__(PREFIX + "rejected")`）会让本读取器**抛错**（找不到
//   匹配）——那是**响亮的失败**，好过静默放过；但它也可能让守卫在「重命名了类」
//   这种无关改动上误报。判据是：读取器抛错时，先看后端是不是换了写法。
// * 只覆盖这三个字段/表；后端将来**新增**一处自由文本协议，本看守不知道。

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** vitest 的 cwd 是 `apps/web`（实测），后端源在仓库根的 `packages/` 下。 */
const REPO_ROOT = resolve(process.cwd(), '..', '..');

export function readBackendSource(relativePath: string): string {
  const abs = resolve(REPO_ROOT, relativePath);
  try {
    return readFileSync(abs, 'utf-8');
  } catch (err) {
    // 读不到就是守卫失效——必须响亮，不能静默返回空串让断言恒真。
    throw new Error(
      `跨端看守读不到后端源文件 ${relativePath}（解析为 ${abs}）。` +
        `若目录结构变了，改 backendSource.ts 的 REPO_ROOT；` +
        `若文件改名/搬家，改调用方传的路径。原始错误：${(err as Error).message}`,
    );
  }
}

/**
 * 从 Python 源码里提取某个 `dict[str, set[str]]` 字面量的邻接关系。
 *
 * 形如::
 *
 *     HOOK_ALLOWED_NEXT: dict[str, set[str]] = {
 *         "OPEN": {"OPEN", "ACTIVE", "ABANDONED"},
 *         ...
 *     }
 *
 * 返回 `{ OPEN: ['OPEN','ACTIVE','ABANDONED'], ... }`（数组已排序，便于比对）。
 *
 * ⚠️ 找不到该变量名 ⇒ **抛错**（不是返回空对象）。空对象会让
 * 「前端表 == 后端表」这类断言在两边都空时恒真。
 */
export function parseAllowedNextMap(source: string, varName: string): Record<string, string[]> {
  const start = source.indexOf(`${varName}:`);
  if (start < 0) {
    throw new Error(`后端源里找不到 ${varName}（可能改名了）——守卫须随之更新`);
  }
  const braceStart = source.indexOf('{', start);
  if (braceStart < 0) throw new Error(`${varName} 声明后没有找到 '{'`);

  // 括号配对找字面量结尾（值里含嵌套的 {}）
  let depth = 0;
  let end = -1;
  for (let i = braceStart; i < source.length; i++) {
    const ch = source[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  if (end < 0) throw new Error(`${varName} 的字面量括号不配对`);

  const body = source.slice(braceStart + 1, end);
  const out: Record<string, string[]> = {};
  // 逐行解析 `"KEY": {"A", "B"},` —— 允许单双引号、允许空集合
  const lineRe = /["']([^"']+)["']\s*:\s*\{([^}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = lineRe.exec(body)) !== null) {
    const key = m[1];
    const vals = [...m[2].matchAll(/["']([^"']+)["']/g)].map((x) => x[1]);
    out[key] = [...new Set(vals)].sort();
  }
  const keys = Object.keys(out);
  if (keys.length === 0) {
    throw new Error(`${varName} 解析出 0 个键——正则没匹配上，读取器需要更新`);
  }
  return out;
}

/**
 * 取某个异常类的 `super().__init__(...)` 的**实参字面量**。
 *
 * 用途：后端把终态标识放在里面的（`chapter_review/pipeline.py` 的
 * `_Rejected` / `_RejectForRevision`），而 `error` 字段取 `str(exc)`
 * ⇒ **这个字面量就是前端要匹配的协议值**。
 *
 * ⚠️ 不用「锚点后 N 字符窗口」：第一版就是这么写的，而类 docstring 是**中文**，
 * 一行就吃掉大半窗口 ⇒ 找不到 `super().__init__`（本批实测踩到）。改为
 * 「从类名往后找 `super().__init__(`，再取它的括号内容」，与 docstring 长度无关。
 * 找不到 ⇒ 抛错（空值会让断言在两边都空时恒真）。
 */
export function superInitArgInClass(source: string, className: string): string {
  const cls = source.indexOf(`class ${className}(`);
  if (cls < 0) throw new Error(`后端源里找不到 class ${className}(——类已改名？`);
  const call = source.indexOf('super().__init__(', cls);
  if (call < 0) throw new Error(`${className} 里找不到 super().__init__(`);
  const open = call + 'super().__init__('.length;
  const close = source.indexOf(')', open);
  if (close < 0) throw new Error(`${className} 的 super().__init__( 括号不配对`);
  const inner = source.slice(open, close).trim();
  const lit = inner.match(/^["']([^"']+)["']$/);
  if (!lit) {
    throw new Error(
      `${className} 的 super().__init__ 实参不是单个字符串字面量（实测 ${JSON.stringify(inner)}）。` +
        '若后端改成了变量拼接，本读取器需要更新——注意这时前端匹配**会失效**，别放过。',
    );
  }
  return lit[1];
}

/**
 * 取出源里**全部** `error = "..."` / `error='...'` 赋值字面量（含 SQL 里的写法），
 * **不去重、不按内容过滤**。
 *
 * ⚠️ 这里**故意不做内容过滤**。第一版带一个 `mustContain` 参数，而突变验证当场
 * 抓到它把判别域缩没了（CB-3 存活）：把 `'cancelled by user'` 改成
 * `'cancelled-by-user'` 之后，该值**不再包含搜索串**而**被静默滤掉**，
 * 只剩另一处仍匹配 ⇒ 守卫恒绿。**过滤条件用了「突变会改变的那个串」，
 * 于是突变把自己藏出了判别域**——同坑区「看守自己被悄悄缩小了执行域」。
 *
 * 正确形状：读取器只负责「如实取出全部」，**过滤放到测试里可见**，
 * 且判据用**突变不会破坏**的更宽子串（如 `cancel`）。
 *
 * 一条都没有 ⇒ 抛错（空数组会让断言在两边都空时恒真）。
 */
export function errorAssignmentLiterals(source: string): string[] {
  const hits = [...source.matchAll(/error\s*=\s*["']([^"']+)["']/g)].map((x) => x[1]);
  if (hits.length === 0) {
    throw new Error(
      '后端源里找不到任何 error= 字符串字面量赋值——写法可能变了' +
        '（例如改成变量拼接），前端匹配须随之核对',
    );
  }
  return hits;
}

export const BACKEND = {
  ledgerModels: 'packages/domain/ledger/models.py',
  chapterReviewPipeline: 'packages/workflows/chapter_review/pipeline.py',
  engine: 'packages/core/workflow_runtime/engine.py',
} as const;
