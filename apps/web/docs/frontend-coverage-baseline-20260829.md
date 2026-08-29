# 前端测试覆盖率基线（2026-08-29 · Sprint 5 阶段 0）

> 范围：`apps/web`（Vite + vitest 2.1.x + @vitest/coverage-v8 v8 provider）。
> 触发命令：`npx vitest run --coverage --coverage.reporter=text --coverage.reporter=json`
> 输出：`apps/web/coverage/coverage-final.json`（同时 stdout 已贴出 text 报告）
> 仅做只读治理；本期新增两个 dev 依赖：`@vitest/coverage-v8@^2.1.1`、`@testing-library/dom@^10.4.0`（修复 18 个 suite 因缺隐式 peer 依赖而集体加载失败的根因，未改动任何业务代码）。

---

## 1. 总体覆盖率（v8 · 全量）

| 维度    | 覆盖数 / 总数  | 覆盖率 |
| ------- | -------------- | ------ |
| Statements | 6828 / 9689 | **70.47 %** |
| Branches   | 1328 / 1759 | **75.50 %** |
| Functions  |  245 /  432 | **56.71 %** |

- 测试文件：23 / 23 全部通过；用例：268 / 268 通过；耗时 6.03 s。
- 函数覆盖率显著低于语句/分支覆盖率，提示大量"声明但未被调用"的小函数（多数在 `api/endpoints.ts` 中，见下文）。

---

## 2. Top 10 低覆盖文件清单

按 Stmts % 升序排序（前 10；同分时按 Branches 升序作 tiebreaker）。路径相对 `apps/web/`。

| 排名 | 文件 | Stmts | Branch | Funcs | 关键未覆盖区（一句话说明） |
| --- | --- | ---: | ---: | ---: | --- |
| 1 | `src/api/types.ts` | **0.00 %** | 100.00 % | 100.00 % | 纯 TS 类型声明文件，无运行时语句；0% 为正常现象，不计入治理目标。 |
| 2 | `src/App.tsx` | 0.00 % | 0.00 % | 0.00 % | 顶层路由壳，无单测；不在本期治理重点。 |
| 3 | `src/main.tsx` | 0.00 % | 0.00 % | 0.00 % | 入口挂载，无单测；不在本期治理重点。 |
| 4 | `src/layout/Layout.tsx` | 0.00 % | 0.00 % | 0.00 % | 整页壳无单测；不在本期治理重点。 |
| 5 | `src/pages/NotFoundPage.tsx` | 0.00 % | 0.00 % | 0.00 % | 13 行静态页，无单测；低风险。 |
| 6 | `src/pages/ChaptersPage.tsx` | 0.00 % | 0.00 % | 0.00 % | 245 行整页无单测；**该页面看起来已被 `ChapterDetailPage` 替代，建议先确认是否仍被路由使用**，再决定补测或删除。 |
| 7 | `src/pages/StoryBiblePage.tsx` | 0.00 % | 0.00 % | 0.00 % | 68 行总页壳，分发到 4 个 Tab；与下面 3 个 Tab 一起 0% 是同一根因。 |
| 8 | `src/pages/bible/CharacterTab.tsx` | 0.00 % | 0.00 % | 0.00 % | 409 行；无单测。 |
| 9 | `src/pages/bible/PlotTab.tsx` | 0.00 % | 0.00 % | 0.00 % | 323 行；无单测。 |
| 10 | `src/pages/bible/WorldTab.tsx` | 0.00 % | 0.00 % | 0.00 % | 305 行；无单测。 |

> 1–7 与 8–10 两类本质不同：1–7 是壳/入口/可疑死代码；8–10 是 Bible 4 大 Tab 中尚未被覆盖的 3 个（仅 `CanonTab` 与 `LedgerTab` 有单测）。治理优先级见 §3。

### 治理重点 5 个文件（非 0%，但属于本期点名）

| 文件 | Stmts | Branch | Funcs | 未覆盖关键区（依据 v8 JSON 抽样确认） |
| --- | ---: | ---: | ---: | --- |
| `src/api/endpoints.ts` | 37.78 % | 100.00 % | **3.06 %** | 95 个函数几乎全部为 0 命中：projects/chapters/branches/quality/style-samples/canon/character/plot/world/ledger/bible/context/agents 等 16 个资源的所有 list/get/create/update/delete/init/promote/... 端点都只在 `client.test.ts` 间接覆盖；正面 mock 后再补 endpoint 级单测即可一次性拉高。 |
| `src/api/client.ts` | 85.29 % | 85.00 % | 81.82 % | 未覆盖：L34–38（base URL 派生）、L78–81（错误体非 JSON 分支）、L112–113（非 JSON 成功响应回退）、L124–125（put）、L127–128（patch）；错误分支与 put/patch 仍未被独立 mock 命中。 |
| `src/utils/chapterState.ts` | 100 % | 91.11 % | 100 % | 仅 4 条分支未命中：`pickActiveRun` L136 与 `pickLatestRun` L154 的"两个 run 时间字符串完全相等"的稳定排序回退分支；与核心正确性关联弱，但建议补一条相等时间戳用例。 |
| `src/components/ApprovalCard.tsx` | 91.83 % | 79.84 % | 100 % | 未覆盖：L148–155（review report.errors 严重项渲染）、L210 / L253 / L326 / L411–416（多处条件渲染分支与 disabled/loading 状态组合）、L492–527（`review-report-errors` 红色告警块整段）；均为"异常/边界态"，是本期白盒测试补强的核心。 |
| `src/pages/ChapterDetailPage.tsx` | 80.83 % | 74.13 % | 54.83 % | 未覆盖：L65–73（mount 失败分支）、L109–164（分支与上下文载入失败）、L260/273/278/311–312（quality gate / evaluation 结果空态）、L435–459（回调链路 onCreated/onUpdated/onEvaluated 失败分支）、L665–667（`handleSave` JSON 解析失败分支）、L666–668（`handleSave` 异常提交分支）；**整页回调函数仅 ~46% 被覆盖，组件级联 mount 失败、回调失败路径几乎全裸**。 |
| `src/pages/AiSettingsPage.tsx` | 78.63 % | 77.78 % | 70.97 % | 未覆盖：L107–110（拉取失败）、L192–201（缺模型列表渲染空态）、L209/233/641/686 等 9 个 `onClick/onChange/onCancel` 回调；L937–939 / L973 之后的滚动/拖拽分支；与 ChapterDetailPage 同属"组件交互链路未建模"。 |

---

## 3. 治理优先级建议（仅建议，不在本期落地）

| 优先级 | 标的 | 建议动作 |
| --- | --- | --- |
| P0 | `api/endpoints.ts`（F 3.06%） | 在 `client.test.ts` 已覆盖 `request` 的基础上，新增薄 endpoint 集成测试；一轮即可拉到 60 %+。 |
| P0 | `ChapterDetailPage` / `AiSettingsPage`（回调裸跑） | 优先补 `onSave/onCancel/onClick` 失败路径与 mount-failure 分支；这两处是用户主要操作面。 |
| P1 | `ApprovalCard` 异常态 | 补 review-report-errors 与 disabled/loading 组合用例。 |
| P1 | `bible/{Character,Plot,World}Tab.tsx` | 与已有 `CanonTab/LedgerTab` 对齐补测；零测试先收敛到 30%+。 |
| P2 | `ChaptersPage.tsx` 是否仍被路由使用 | 查 router 配置确认；如已弃用应直接删除而非补测。 |
| P3 | `client.ts` 错误分支 + `chapterState` 相等时间戳回退 | 单元级补一条即闭环。 |

---

## 4. 数据来源

- text 报告：本会话 stdout 末尾（`File | % Stmts | % Branch | % Funcs | % Lines | Uncovered Line #s` 块）。
- 原始 JSON：`apps/web/coverage/coverage-final.json`（v8 报告）。
- 抽查依据：本会话对 `chapterState.ts:125–156`、`client.ts:70–128`、`endpoints.ts:1–25`、`ApprovalCard.tsx:140–154 / 505–519`、`ChapterDetailPage.tsx:435–459 / 660–675` 的只读确认。