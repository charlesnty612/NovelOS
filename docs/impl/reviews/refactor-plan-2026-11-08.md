# 重构方案（2026-11-08）

> 触发：用户「考虑对项目进行重构」。
> 方法：8 个域并行设计（重复收敛 / 质量域 / 装配缓存 / 引擎与流水线 / 项目建模 / 测试体系 /
> 半成品面 / 架构边界）⇒ 42 条提案 ⇒ **每条三镜头对抗审查**（行为等价性 / 风险与遗漏 /
> 收益与时机）⇒ **9 条通过 / 33 条驳回** ⇒ 综合排期。共 135 个只读 agent。
> 纪律：主会话对 P0/P1 提案的事实前提**逐条亲验**（见 §二「亲验」列）。
> 零真实 LLM 调用、零内容仓接触、活库业务数据零改动。

---

## 一、结论先行

1. **能安全做的重构，比看起来少得多。** 42 条提案里 33 条被驳回，驳回主因不是「想法不好」，
   而是**数量或口径的事实错误**、或**声称零行为变更但实际会变**。本仓的注释密度很高，
   这让「按注释描述去重构」特别容易踩空——审查阶段抓到的典型错误包括：
   把 3 处 monkeypatch 说成 6 处（实际 16 处）、把 `verifier.py` 的单章查询说成同形 SQL、
   把 `mockReset()` 的语义搞错导致等价性 oracle 本身失效。
2. **最诱人的几个重构恰恰现在不能做**：建章单点、最新稿单点、章状态单点、缓存键单点、
   engine 拆包、chapter_review 拆包——全部被驳回。原因集中在一处：
   **本仓的「重复」大多不是同一件事，而是同一个名字下的不同语义**（`version DESC` vs
   `created_at DESC`、校验集合 vs 写触发集合、24 元键的两套 cacheable 谓词）。
   合并它们必须先做语义裁决，那是产品/契约决定，不是重构。
3. **第一批 4 项可在一次会话完成且零行为变更**，验收口径统一为「测试数零增减 + 全量绿 +
   树指纹一致」。

---

## 二、通过审查的 9 条

| # | 编号 | 提案 | 类别 | 行为变更 | 风险 | 阶段 | 亲验 |
|---|---|---|---|---|---|---|---|
| 1 | **INV** | 用例清单守恒闸门（pytest node-id + vitest fullName 双端快照差分） | test | none | low | **P0** | ✅ 确认现无任何清单看守；`test_chapter_pipelines.py:19` 的「17=10+7」是**手写记账** |
| 2 | **R1-break-cycle** | 断 control↔revise 潜伏环：启动实现迁到 `start.py`，活跃 run 守卫下沉 `workflow_runtime` | architecture | none | low | **P0** | ✅ `control.py:11-13` 自认环、`:1139` 线程体内延迟 import、`revise.py:46` 顶层 import、`chapters.py:18` 反向 import 私有符号 |
| 3 | **GOV-SIZE** | 把 1500 行硬规则机器化：文件体量看守 + 只减债账 | architecture | none（+N 用例） | low | **P1** | ✅ 现无任何体量看守；实测超标 4 个（见下） |
| 4 | **R-1** | `project_init/pipeline.py`（1558 行）拆 **3 模块 + 门面**（原提案 5 模块被审查订正） | split | none（半拆） | medium | **P1** | ✅ 打点实测：`get_connection` 8 处、`run_agent` 3 处、`new_id` 1 处 ⇒ 这些名字**必须留门面** |
| 5 | **REF-04** | 审校新鲜度查询收敛：3 份逐字相同 SQL → 1 份；第 4 份（`gate.py`）**只共享谓词、不合并** | dedupe | none | low | **P1** | ✅ 三处 SQL 逐字比对成立（`control.py:165-176` / `service.py:416-427` / `commit.py:383-392`）；`gate.py:383` 的 `started_at DESC + LIMIT N + checkpoint 解析` 确为另一语义 |
| 6 | **RQ-5** | `ai_trace` 三张阶梯表自描述化：把「注释按下沿写、代码按上沿算」钉成机器看守（**数值不动**） | architecture | none | low | **P2** | ✅ `_bucket_deduct` 是 `value >= threshold` 升级；三表首行注释与实现错位一档；`1.0001` 桶在 ratio≤1 下不可达 |
| 7 | **R-5** | deconstruct 边界裁决：不与建章层合并，只补 domain 写入口 + 搬出解析层 | architecture | none | low | **P2** | 未亲验（P2） |
| 8 | **RQ-6** | style 跨章趋势处置：接线 或 删除（现在是纯死代码） | remove | **minor** | medium | **P3** | 未亲验（需产品裁决） |
| 9 | **R-HF-01** | 分支 + what-if 仿真：先做零行为降级（隐藏两个面板），保留/补齐/删除待裁决 | remove | none | medium | **P3** | 未亲验（需产品裁决） |

**GOV-SIZE 实测的 4 个超标文件**（`types.generated.ts` 为 codegen 产物、豁免）：

```
3895  apps/web/src/pages/ChapterDetailPage.test.tsx
1912  apps/web/src/api/types.ts
1698  packages/core/workflow_runtime/engine.py
1558  packages/workflows/project_init/pipeline.py
```

---

## 三、第一批（4 项，单会话，逐项独立 commit）

顺序有硬约束：**`control.py` 被 #2 与 #4 同时改动 ⇒ 必须串行**；`GOV-SIZE` 的债账含
`project_init:1558` ⇒ 必须排在 `R-1` 之前并事后回填。

### 1. INV · 用例清单守恒闸门
- **为什么先做**：它是其余所有提案「测试数零增减」的**唯一机器判据**；现在全靠手写记账。
- **设计调整（我对原提案的订正）**：原提案做**逐 node-id 全量快照**（约 4761 条），
  每次合法新增用例都要改快照，摩擦过大。改为**两层**：
  ① 总数 + 逐文件计数快照（低摩擦，能抓「这个文件少了 3 条」）；
  ② 一份显式的「允许变更清单」，改计数必须同时改清单并写理由。
  逐 node-id 差分只在**重构批次**临时启用，不常驻。
- **验收**：故意删一条用例 ⇒ 闸门红；显示理由后放行。

### 2. R1-break-cycle · 断 control↔revise 环
- **目标**：把「起跑实现」迁到一个中性模块（`workflows/start.py`），`control.py` 与
  `revise.py` 都向下依赖它；把 `_check_active_run_for_chapter` 下沉到
  `workflow_runtime`（`routers/chapters.py` 现在反向 import 另一个 router 的私有符号）。
- **兼容**：`control.py` 必须**继续导出** `_start_workflow` / `_start_workflow_core` /
  `_check_active_run_for_chapter`（`revise.py:46`、`chapters.py:18`、
  `tests/unit/test_fast_mode.py:29` 依赖这些名字）。
- **验收**：全量绿、用例数零增减；新增一条「`control` 与 `revise` 的 import 图无环」的静态断言。

### 3. GOV-SIZE · 1500 行硬规则机器化
- **现状**：AGENTS §二 硬规则 1 写了「不得超 1500 行」，但**没有任何看守**；超标清单还漏登了
  `engine.py` 与 `project_init/pipeline.py`。
- **改法**：测试遍历 `packages/**/*.py` 与 `apps/web/src/**`，超 1500 行即红；
  存量 4 个文件登进「只减债账」（豁免但记录），新增文件一律不豁免。
- **验收**：新增一个 1501 行的临时文件 ⇒ 红；把 `engine.py` 加到债账 ⇒ 绿但计入。

### 4. REF-04 · 审校新鲜度查询收敛
- **落点**：新建 `packages/domain/chapter/review_state.py`（与 `draft_resolver` 同层），
  提供 `REVIEW_VALID_PREDICATE` 常量 + `last_review_ended_at(conn, ...)` +
  `latest_draft_after(conn, threshold)`。**conn 必传**（三处持有者不同：自建连 / 自建连 / 已开事务）。
- **明确不合并**：`gate.py:383` 的 reviewed-version 查询（`started_at DESC` + `LIMIT N` +
  checkpoint 解析）——它是另一套语义，合并会改变快照/快通道下取到的版本。
- **验收**：三处返回同一值的一致性断言 + 一条「gate 仍按 started_at 取候选」的护栏用例。

---

## 四、第二/三批

| 阶段 | 项目 | 前置 |
|---|---|---|
| P1 后半 | **R-1**（project_init 拆 3 模块 + 门面） | GOV-SIZE 先落地 |
| P2 | **RQ-5**（ai_trace 表自描述化，数值不动） | 无 |
| P2 | **R-5**（deconstruct 边界） | 无（与 R-1 文件不重叠，可并行） |
| P3 | **RQ-6**（style 趋势：接线 or 删除） | 产品裁决：接线会新增 issue 并可能影响门禁 |
| P3 | **R-HF-01**（分支/仿真面板降级） | 产品裁决 |

---

## 五、被驳回的 33 条：为什么现在不做

按驳回主因归并（完整理由见工作流输出）：

**A. 声称零行为、实际会变（14 条）** —— 建章单点（三份 INSERT 列集不同）、最新稿单点
（`version DESC` vs `created_at DESC` 不是等价物）、章状态单点（校验集合 ≠ 写触发集合）、
`_ensure_project` + plan_json 读改写（B 部分会变）、rule_id 注册表（`category` 单值无法
表达 `scoring_missing_subscore`）、缓存键单点（两套 cacheable 谓词不同形）等。

**B. 事实/计数错误，按原文执行会失败（9 条）** —— chapter_review 拆包（打点 16 处被说成 6 处）、
`_finalize_run` 表单点（动机数字低估一倍多、9 条直接用例被说成 3 条）、
`REVIEW_VALID_PREDICATE` 之外的两份「同形」实为不同语义等。

**C. 收益不抵风险（6 条）** —— engine 拆包（1698 行 → 5 子模块，但 `engine_mod` 有 3 处打点、
且它服务的是「PAUSED 后 resume」这一核心能力）、`ChapterDetailPage.test.tsx` 拆分
（等价性 oracle 在 vitest 2.1.9 上不成立）、后端大测试文件拆分（CI windows job 是写死文件清单，
拆完新文件不在清单里 ⇒ **覆盖静默消失**）。

**D. 与产品决策耦合（4 条）** —— skill / 知识权限 / judge / model_configs / 死表删除。

---

## 六、本仓重构的硬边界（写进纪律，不是建议）

1. **monkeypatch 契约**：拆前先 `grep -rn "monkeypatch.setattr(<模块>" tests/`。
   实测热点：`chapter_write/pipeline` 23 处（全是 `run_agent`）、`runner` 10 处、
   `workflows/control` 4 处、`chapter_review/pipeline` 4 处、`cache` 4 处、`engine` 3 处。
   **被打点的名字不能迁走**——要么留门面，要么先改测试打点目标（那是另一件事）。
2. **突变规格锚点会随重构漂移**：本轮已亲历一次（`D9M1/D9M2` 因判据重构锚点命中 0 次）。
   重构后**必须**跑 `run_spec.py --all --precheck`，并对动过的文件跑 `--changed`。
3. **CI 的 windows job 是写死文件清单**：拆测试文件后必须同步 `.github/workflows/ci.yml`，
   否则新文件在 CI 上**零覆盖**（本地全绿不等于 CI 绿）。
4. **零行为变更的验收三件套**：测试数零增减（INV 闸门）+ 全量绿 + 树指纹跑测前后逐位一致。
5. **门面再导出**：拆出的子模块通过门面 `__all__` 再导出；`__all__` 收缩到「有消费方的名字」，
   并且**不得含自指元素**（既有纪律）。
6. **删除属破坏性动作**：批量删代码/删表不在「重构」里顺手做，须单独立项 + 迁移 + 回滚方案。

---

## 七、建议

**先做第一批 4 项**（特别是 INV 与 R1-break-cycle）：它们是「让后续重构可验证」的基础设施，
本身几乎零风险。**不要**从「看起来最乱」的地方（engine / chapter_review / 缓存键）开始——
审查已经证明那三处按现有方案改都会变行为或打断打点契约。

产品的判断留给用户：分支/仿真、skill、知识权限、judge、死表——这五项的成本已经付了，
收益为零，但**删不删是产品决定**，不是工程决定。
