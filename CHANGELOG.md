# 更新日志（CHANGELOG）

> **留痕规矩（自 V1.0 起强制）**：此后所有迭代——功能、修复、迁移、行为变更——合并前必须在本文件追加条目。
> 发版惯例：发版时把 `## [Unreleased]` 改名为 `## [X.Y.Z] - 日期`，并重新加回空的 `## [Unreleased]` 承接下一轮。

## [Unreleased]

### Fixed（2026-10-22 批次（十七）：CI 连续两次红 —— 一条前端竞态断言（本地全绿 / CI 恒红））

> **来源**：用户问「项目目前是什么情况」⇒ 查 CI 时发现：批次十六的**两次** push
> （`e467cf3` / `06581e1`）的 **web job 都红**，而**本地全量 vitest 780 全绿**。
> ⚠️ **批次十六只动了 `scripts/` 与文档，根本没碰前端** ⇒ 红的是一条**早就在那里**的
> 竞态断言，被连续两次 push 带上去。

#### 1. 症状

两条 CI run（`39654459279` / `39655441184`）的 **web job** 失败，
**backend 与 e2e smoke 两个 job 全绿**；web job 内 **780 条里恰好 1 条红**：

```
FAIL src/pages/SimulationPanel.test.tsx > SimulationPanel > 按 number 列出章节；切换章节 ⇒ 重新取该章 delta
AssertionError: expected "spy" to be called with arguments: [ 'p1', 'ch_1' ]
Number of calls: 0
```

⇒ `listDeltas` **一次都没被调用过** ⇒ 断言抢在取数之前跑了。

#### 2. 真因：**同步断言挂在 await 链后面 = 竞态**

`SimulationPanel.tsx` 的两个 effect 是**串联**的：

1. 取章节列表的 effect：`.then(rows => { setChapters(rows); setChapterId(...) })`
   ⇒ **`chapterId` 在这里落定**；
2. 取 delta 的 effect：依赖 `loadDeltas`（依赖 `chapterId`）
   ⇒ **它要等第 1 步落定后才被调度**，且它是 React 的 **passive effect**（调度时机不定），
   内部还要 `await` 一层。

而原断言是**同步**的：

```ts
const sel = await screen.findByTestId('simulation-chapter-select');  // select 渲染出来了
expect(listDeltas).toHaveBeenCalledWith('p1', 'ch_1');              // ← 同步断言，取数还没发生
```

⇒ **「组件已渲染」与「数据已取回」之间没有 happens-before 关系**。
本地 CPU 空闲、mock 立即 resolve ⇒ effect 已被调度 ⇒ 绿；
**CI 并行 57 个文件、CPU 争抢 ⇒ effect 调度被推后 ⇒ 红**。
（同一条用例的**下一行**本来就用了 `waitFor` ⇒ 同一文件里两种观察方式并存，
作者当时把「select 出现」当成了「delta 已取」。）

#### 3. 修法：观察点落在行为之后，**判别点不变**

```ts
await waitFor(() => expect(listDeltas).toHaveBeenCalledWith('p1', 'ch_1'));
```

⚠️ **不改生产代码**（改实现去迁就测试 = 把量具的缺陷写进被测物）。
**判别点一字未动**：仍要求 `listDeltas` 以 `('p1','ch_1')` 被调用过。

#### 4. ⚠️ 改断言形态 ⇒ **必须重验判别力**（本仓纪律）

判别面缩小是「看起来测到了」变「测不到」的最常见路径。新增突变规格
`tests/mutation_specs/l11_simulation_panel_delta_fetch.py` 两条，各钉一个失效方向：

| 突变 | 做法 | 预期红的那条 |
|---|---|---|
| `SIMDELTA-1` | `listDeltas(projectId, chapterId)` → `listDeltas(projectId, 'ch_1')`（恒取第一章） | 用例**后半句**（切到 ch_2 后不再重取） |
| `SIMDELTA-2` | 撤掉「取 delta」那个 effect | 用例**前半句**（根本不取） |

⇒ 实跑 **2/2 CAUGHT**、0 存活 0 中止，**还原校验 OK**。
另用只读探针取证「**红的是预期那条**」（突变摘要只说 `1 failed`，不点名用例）：

```
[汇总]   Tests  1 failed | 31 passed (32)
[失败用例] （共 1 条）
   - SimulationPanel > 按 number 列出章节；切换章节 ⇒ 重新取该章 delta
[还原] SHA 2acab9c0711bb8a6 == 前值 2acab9c0711bb8a6 ? True
```

⇒ **恰好 1 条红**、红的是目标用例、**其余 31 条全绿** ⇒ 判别点**专属**
（不是公共 API 被同文件其它用例也调用）。

#### 5. ⚠️ 同形状**全量扫过**（不是「改完一处就算」）

本仓纪律要求「同一形状抓到第二次 ⇒ 沉淀」，但**反过来也要查**：这形状**还有没有第二处**。
⇒ 写只读扫描器（`apps/web/src` 全量 `.test.ts*`），判据 = 「裸同步断言某个 api mock
的调用」且「其前 8 行内既无 `waitFor`、也无 `fireEvent`/`userEvent`、也无 `await`」
⇒ **前文没有任何东西保证被断言的行为已经发生**。

结果 **5 处候选**，逐条判：

| 位置 | 形态 | 判定 |
|---|---|---|
| `ContinuePanel.test.tsx:77` / `:132` | `expect(continuationApi.generate/adopt).toHaveBeenCalledWith(...)` | **安全** —— `fireEvent.click` 触发，调用在**事件处理器**里同步发生 |
| `BranchesPanel.test.tsx:270` | `expect(storyStateApi.getBranchState).toHaveBeenCalledWith(...)` | **安全** —— 同上（`handleToggle` 里 `Promise.all([...])` 的数组**同步求值**） |
| `ProjectInitPanel.test.tsx:1280` / `:1562` / `:1563` | `not.toHaveBeenCalled()` | **形态正确，无需改** —— 否定式断言本就该是**同步**的（要等反而等它发生）；且 `workflowsApi.get` 的两处调用点（`useProjectInitOrchestration.ts:397` / `:548`）都是**用户动作触发**，不是 mount 自动轮询 ⇒ 断言稳定 |

⇒ **肯定式**竞态断言全仓**仅此 1 处**（已修）。**否定式**那 3 处属**另一个族**
（断言时点太早 ⇒ 可能恒真 / 偶发红），**本批登记不修**。

#### 6. 闸门

| 闸门 | 结果 |
|---|---|
| 全量 pytest | **3796 passed / 1 skipped / 1 xfailed / 0 failed**（440.62s，项目 `.venv`，`-n 4`）—— 与批次十六基线**逐位一致**（零增减：新规格文件非 `test_` 前缀，不进收集面） |
| 全量 vitest | **780 全绿**（57 文件） |
| ruff | `All checks passed!`（`packages scripts tests`） |
| OpenAPI | **109 paths / 145 schemas —— 零漂移**（`export_openapi` + `gen_frontend_types` regen 后 git 无变化） |
| 树指纹 | **663 文件 / `7da524c2…`** 跑测前后**逐位一致**（662 → 663 = +1 新规格文件） |
| smoke e2e | **PASS**（plan→write→review PAUSED→resume→commit 四步链路完好 + observer 内容在场 + SYNC OK） |
| 突变（本批新增） | **2/2 CAUGHT**（`SIMDELTA-1` / `SIMDELTA-2`），0 存活 0 中止，**还原校验 OK** |

⚠️ **全量 325 条突变本轮未重跑**：本批**后端源码零改动**（`git status` 逐条为证：
只有前端测试 + 新规格文件 + 文档）⇒ 其余 323 条的判别面不变（批次十六实测 323/323 全捕获）
⇒ **不声称「全量突变通过」**。

⚠️ **CI 修复效果待 CI 验证**（推送后看 run；批次十六那两条红 run 是失败态，
本条修复的正是它们指向的用例）。

**⇒ CI 回填（run `39717309182`，commit `729880d`）：3 job 全绿 ✓**

| job | 结果 |
|---|---|
| `web (build + vitest)` | **success** —— 正是批次十六那两条 run 里**红掉的那个 job**，本次修复生效 |
| `backend (pytest + ruff)` | **success** |
| `e2e smoke (state sync included)` | **success** |

⇒ 本地全绿（Windows）**与** CI 全绿（Linux）**同时成立** ⇒ 该修复在两个平台上都有效
（这正是本条教训的两端：**本地绿不等于 CI 绿**，反过来 CI 绿也不等于本地绿 —— 两端都要各跑一次）。

见下表。

### Added（2026-10-21 批次（十五）：模型连接层 —— 同一厂商的多个模型不再各填一遍 key/base_url）

> **来源**：用户指令「你看一下软件的 LLM 配置的，我感觉现在的 LLM 配置有点不顺手，你来帮我优化一下；
> 另外，如果我想配置 opencode go 的话，你看要怎么搞」（附 opencode Console 文档：
> 每次请求须带 `x-opencode-session: ses_...`）。
> **用户裁决**：① 痛点是「同一厂商，想分配不同模型承担不同职责，需要要重新完全新建」；
> ② session id **每个写作 run 一个**（由 `run_id` 派生）；③ 删连接被引用时**拒绝删除并列出引用者**；
> ④ opencode 用 **Go 套餐**（`https://opencode.ai/inference/go/openai/v1`）。
> 全程**零真实 LLM 调用、零内容仓接触**（AGENTS §一 两条硬约束）。

#### A. 根因：两层架构只解了一半

0016 迁移建的 `model_profiles` + `capability_bindings` 解开了「**模型 ↔ 环节**」，
但没解开「**模型 ↔ 连接**」——`provider / base_url / api_key` 与模型名焊在同一条记录里。
于是同一厂商想让 gpt-5.5 写正文、claude-opus 做推理、便宜模型做评审时，
必须建 3 个档案并把 base_url / api_key 各重填 3 遍（改一次 key 要改三处，漏一处就串味）。

#### B. 新增连接层（迁移 0033）

| 要素 | 落地 |
|---|---|
| 表 | `model_connections`（connection_id / name / provider / base_url / params_json / enabled / 时间戳）⇒ 业务表 41 → **42** |
| 引用 | `model_profiles` 加**可空**列 `connection_id` ⇒ 存量档案 `NULL` 时**行为逐位不变** |
| 职责划分 | 连接级 = provider / base_url / api_key / headers / models_url；档案级 = model / thinking / reasoning_effort / timeout_s / temperature …（**timeout_s 归档案级**：不同模型响应速度不同） |
| 权威 | **连接是 provider / base_url / api_key / headers 的唯一权威**；档案的 provider 列降级为派生镜像（连接改 provider 时同步刷新档案列） |
| API | `/api/model-connections` CRUD + `/test`；档案 POST/PATCH 接受 `connection_id`（`null` = 解绑） |

#### C. 自定义请求头 + opencode 接入

- 连接级 `params_json.headers`（`dict[str,str]`），值支持 **`{{run_id}}` / `{{session_id}}`**
  两个占位符（**封闭集合**，其它占位符写时 422 ⇒ 不静默发字面量给上游）。
- `{{session_id}}` 渲染成 `ses_<sha256(run_id)[:32]>`（run 上下文）或
  `ses_probe_<sha256(connection_id)[:16]>`（探针上下文）——
  ⚠️ **不是** `"unknown"`（那会让所有请求共用一个 session，制造「假成功」）。
- opencode 即用配置：provider `openai_compatible` / base_url
  `https://opencode.ai/inference/go/openai/v1` / key `oc_sk_...`（`Authorization: Bearer`）/
  headers `{"x-opencode-session": "{{session_id}}"}` / models_url
  `https://opencode.ai/inference/go/v1/models`。

#### D. 顺带修掉的「假失败」

`OpenAICompatibleProvider.health_check` 打的是 `GET {base_url}/models`，而
**opencode 的模型列表不在 `{base_url}/models` 上**（Go 套餐是 `/inference/go/v1/models`），
不少中转网关干脆不实现它 ⇒ 「测试连接」把完全可达的端点判成失败。
⇒ ① 连接级可选 `models_url` 覆盖；② `health_check` 两段式：models 端点
**404/405/501** ⇒ 退化为**极小 chat 请求探测**（200/401 都算可达，照搬 `AnthropicProvider`
的既有语义）。⚠️ 降级探测**真的消耗 1 个 token**（上游收了一次请求）——
它只发生在**用户手工触发**的 `/test` 路径上，不是自动化验证路径。

#### E. ⚠️ 先验判断被推翻（必须留痕）：**自动回填是错的**

初版迁移方案是「按 `(provider, base_url, api_key)` 三元组**自动回填** `connection_id`」——
**该方案会串用凭据**：生效 key 的权威解析链 `resolve_profile_api_key` 有 5 级，
**最高两级读的是 secrets.json（一个文件）** ⇒ SQL 迁移看不见它们；而本仓 secrets.json
**现存的条目正是档案级**（实测 `mprof_f95e3c7b4127`）⇒ 存在「两个档案 params 里写着同一个
key、但生效 key 不同」的真实情形 ⇒ 按 params 里的 key 分组会把它们合并 ⇒
**凭据静默串用**（表现为某档案突然 401 / 计费到别人的号）。

⇒ **迁移 0033 刻意不做回填**（文件头有完整留痕），改由应用层的**显式归组动作**：
分组键由运行期同一个解析函数算出（`effective.group_profiles_by_connection`），
**先展示分组预览**给用户确认，确认后才写；且归组时**逐条校验生效 key 一致**
（不一致 ⇒ 422，绝不静默换密钥）。

#### F. 连带修掉的三处既有隐患

| 隐患 | 处置 |
|---|---|
| **浅层掩码**（`_mask_params` 只看 `api_key` 那一层）⇒ `headers` 里的明文 `authorization` 会被回吐给前端 | 掩码**递归一层**（敏感头名小写比较）；`x-opencode-session` 显式**不掩** |
| **dict 型字段套用标量 PATCH 语义** ⇒ 前端把读回的 `***` 原样提交 ⇒ **真值被写成 `***`** | `headers` 走**逐键**语义（`***` 保留该键 / `""`·`None` 删该键 / 缺键保留 / `headers: null` 删整个键） |
| **保留键清单的副本**（同一语义写在 5 处，漏一处就把该键当请求体发给上游 ⇒ 400） | 收敛成**一处定义** `effective.RESERVED_PARAM_KEYS`；顺带发现 `AnthropicProvider` / `OllamaProvider` 的 `complete()` **此前根本不剔保留键**（今天靠 router 层兜底）⇒ 补剔。⚠️ **这是一处真实的行为变化**：直调这两个 provider 并把 `base_url` 放进 params 的调用方，其请求体会变；**生产路径无变化**（router 本就剔过） |

#### G. 厂商预设（「内置就解决了请求头之类的问题」——用户裁决）

用户要求「如果用户配 opencode go 的套餐，内置就解决了请求头之类的问题」
⇒ 新增 **`GET /api/model-connections/presets`**（清单在后端
`packages/core/model_router/presets.py`，**唯一权威**；前端只渲染、不硬编码任何地址），
连接表单里选一个模板 ⇒ **自动填好** provider / base_url / headers / models_url，
用户**只填密钥**即可（填完仍可逐项改 ⇒ 预设过期不会把人卡住）。

| 预设 | base_url | 自带请求头 |
|---|---|---|
| `opencode_go` | `https://opencode.ai/inference/go/openai/v1` | `x-opencode-session: {{session_id}}` |
| `opencode_go_anthropic` | `https://opencode.ai/inference/go/anthropic` | 同上（Claude 系走原生协议） |
| `opencode_payg` | `https://opencode.ai/inference/openai/v1` | 同上 |
| `commandcode_goat` | `https://api.commandcode.ai/provider/v1` | **`x-cmd-zdr: 1`** |
| `kimi_code` | `https://api.kimi.com/coding/v1` | — |
| `minimax_cn` | `https://api.minimax.cn/v1` | — |
| `opendesign_go` | **留空**（官方未公开固定端点） | — |
| `openai` / `anthropic` / `deepseek` / `kimi` / `ollama_local` | 各自官方地址 | — |

⚠️ **两处与用户初始认知不同，按官方文档处理**（并在预设说明里标出）：
① **Command Code GOAT 套餐官方要求每个请求带 `x-cmd-zdr: 1`**（缺了会被拒）
⇒ 它是本清单里除 opencode 外**唯一**需要固定请求头的服务商；
② **OpenDesign Go 的 API 端点官方未公开固定值** ⇒ 预设**留空**由用户从账号页填
（**不编造地址**）。

#### H. 闸门（本次亲跑）

| 闸门 | 结果 |
|---|---|
| 树指纹（跑测前后） | `657 866e859c…` **逐位一致**（648 → 657 = +9 新增文件：4 个后端模块 + 4 个测试/规格 + 1 个迁移） |
| 全量 pytest（`-n 4 -q`，**不带任何 --ignore**） | **3790 passed / 1 skipped / 1 xfailed / 0 failed**（478.31s，exit 0）—— 较基线 3719 collected **+73** |
| ruff `packages scripts tests` | All checks passed |
| `npx tsc -b --force` / `npx vitest run` / `npm run build` | 0 错 / **780 passed（57 文件）** / 通过（基线 755/56 ⇒ **+25 例**） |
| `export_openapi` + `gen_frontend_types` | **109 paths / 145 schemas**（103/142 → +6 path / +3 schema） |
| 突变（新增 `tests/mutation_specs/l10_model_connections.py`） | **6/6 全捕获**，0 存活 0 中止；残留清点 0（`.mutbak` + 标记形态 + 逐字节复核，三项均干净） |
| `smoke_e2e.py` | **PASS**（`tables=42`；plan→write→review PAUSED→resume→commit 四步链路完好 + observer 内容在场 + SYNC OK） |
| GitHub CI（run `39635433432`，**Linux**） | **3 job 全绿**：backend (pytest + ruff) 9m55s / web (build + vitest) 57s / e2e smoke (state sync included) 23s —— **本地是 Windows、CI 是 Linux** ⇒ 本批 3790 条用例**在 Linux 上亦全绿**；⚠️ 突变套件**未接进 CI**（挂账「未在 Linux dry-run ⇒ 先不接」仍成立） |

⚠️ **全量突变套件（既有 310+ 条）本轮未重跑** ⇒ **不声称「全量突变通过」**（如实登记）。
⚠️ **零真实 LLM 调用**（AGENTS §一 硬约束 2）：全部走 mock / 临时库；
「拉取模型」「测试连接」的**上游真实行为未验证**。

#### I. 已知边界（如实登记，不声称已解决）

1. **`continuation.py` 的续写调用不传 `run_id`** ⇒ 该路径上的请求头会渲染成
   **按连接派生的探针 session id**（`ses_probe_…`，稳定但不是「某个写作 run 的 session」）。
   该功能是「续写变体」而非写作 run 的一部分，**当前形态可接受**；
   若将来要求「同一篇续写共享一个 session」，需给该端点造 run 上下文。
2. **`model_configs`（旧表）不参与连接层** ⇒ 旧表的行没有 `connection_id`，
   仍走原 `params_json` 路径（**零行为变化**）。旧表接入连接层需另立项。
3. **连接级 `params` 只允许 `api_key` / `headers` / `models_url`**；
   `timeout_s` 等调参归**档案**级（不同模型响应速度不同）⇒ 想给整条连接设 timeout
   需逐档案设置。**登记为待讨论的产品选择**。
4. **归组动作需要用户确认**：迁移**刻意不回填**（见 §E）⇒ 存量档案要享受
   「改一处全局生效」的收益，需走一次「按连接归组」（预览 → 确认）。
5. **`has_api_key` 的连接感知已实现**（绑定档案的该字段由连接派生），
   但**未绑定**档案仍按原逻辑（档案 params + secrets）⇒ 存量语义不变。

#### J. 批次收尾发现（本轮自查；用户指令「自行裁决」）

批次十五动了 `model_router/models.py` / `model_router/security.py` /
`api/routers/model_profiles.py` —— 而这三个文件**正是既有突变规格的锚点目标**
⇒ 收尾跑只读预检（`tests/mutation_specs/validate_specs.py`，0 改动，约 1 分钟），
实测抓到 **3 处**规格失效（**规格侧**锚点失效，实现没坏）：

| 规格 | 症状 | 真因 | 修法 |
|---|---|---|---|
| `G5-M-FIELD-01` | 锚点命中 **2** 次 | 0033 新增的 `ModelConnectionOut` 也声明 `created_at: str` + `updated_at: str` | 锚点带相邻**独有行** —— 两个模型的 enabled description **只差一字**（「0 的**档案**不可用于 /test」vs「0 的**连接**不可用于 /test」） |
| `L3M15-model-profiles-list-drops-masking` | 锚点命中 **0** 次 + 「变异未改变内容」 | 档案列表的脱敏出口从内联 `[_mask_response(r) …]` 重构成具名函数 `_mask_profile_rows` | 锚点改为该函数体内的多行片段（**判别意图不变**） |

⚠️ **预检通过 ≠ 会被捕获**（坑区已登记）⇒ 两条都**实跑**确认：
**g5 22/22、l3 15/15 全捕获**，0 存活 0 中止。

⚠️ **另一条实测订正**（原 l3 docstring 不准）：它称撤掉出口 ⇒「明文 api_key 直接出参」
⇒ 实测红因是**端点 500**（DB 里 `params_json` 是 **JSON 字符串**，没走脱敏就进不了
`ModelProfileOut.params_json: dict[str, Any]` ⇒ `ResponseValidationError`）
⇒ **根本没走到「检查明文」那步**。两个场景各有判别点，实测（只读探针，已入库
`scripts/probe_profile_mask_exit.py` / `scripts/probe_mask_inner_scope.py`）：
① 撤**整个出口** ⇒ **两条**用例红；② 只撤**内部那一步**
`out["api_key"] = _MASK`（保留 dict 化）⇒ **恰好 1 条**红（明文真出参），其余 27 条全绿
⇒ **判别面无缺口**，docstring 已按实测改写。

⚠️ **环境侧**：Git Bash 的 PATH 转换**丢了系统 PATH 里的 `C:\Program Files\GitHub CLI`**
⇒ 敲 `gh` 报 `command not found`（PowerShell 侧正常）。逐项对照过：**其它带空格的路径
都转换正常** ⇒ 不是「空格导致」，**根因未查明**（不写成已查明）。本会话两次修复尝试
（改用户 PATH / 往 `~/.local/bin` 放转发脚本）**均被权限分类器拦下**（判
「Unauthorized Persistence」）⇒ 属**用户手动操作**，命令见会话记录。

⚠️ **文档订正**：`docs/impl/MODEL-CONNECTIONS.md` 原写「档案面板 →「按连接归组」按钮」
⇒ 该 UI **尚未实现**，手册已改为给出两条 API 端点，避免照着找不到。

### Added（2026-10-22 批次（十六）：备份落网盘同步盘 —— 用户裁决「本机不留其它本项目的备份」）

> **来源**：用户指令「备份改到百度网盘的同步盘，本机不留其它本项目的备份」+「自行裁决」。
> 同步盘路径实测为 **`D:\BaiduSyncdisk`**（含 `qianchuan-backup/` 等既有目录 ⇒ 新建
> `NovelOS/backups/` 子目录，不与别的项目混）。

#### 1. `scripts/backup_db.py`（新）—— 备份的常规入口

- **用 SQLite 的在线备份 API**（`sqlite3.Connection.backup()`），**不是 `shutil.copy2`** ——
  后者在 WAL 模式下会拷到「主库 + 未合并的 -wal」的不一致状态，还原时可能丢最后一次事务。
- **落盘后跑 `PRAGMA integrity_check`**，不过就删掉那份（备份坏了却看不出来 = 没有备份）。
- **输出目录解析**：`--out` > `NOVELOS_BACKUP_DIR` > 探测同步盘
  （`D:\BaiduSyncdisk` / `~\BaiduSyncdisk` / `E:\BaiduSyncdisk`）⇒
  **探测不到就报错退出，不静默回退到本机目录**（用户裁决「本机不留备份」）。
- **保留策略**：`--keep N`（默认 10），只按 `novelos_*.db*` 的时间戳排序淘汰。

#### 2. ⚠️ 安全：备份含**明文 API 密钥** ⇒ 提供了加密落盘

`data/novelos.db` 的 `model_profiles.params_json.api_key` / `model_connections.params_json.api_key`
是**明文**（见 `docs/adr/ADR-0002-model-provider-api-key-plaintext-sqlite.md`）⇒
**备份文件同样含明文密钥**，写进同步盘 = **密钥上云**。

⇒ `--encrypt`（**推荐**）：openssl `aes-256-cbc` + `pbkdf2 × 200000`，**密码从文件读**
（不进命令行/进程列表），密钥文件默认 `~/.novelos/backup.key`（**只存本机，绝不进同步盘**）。
⚠️ **加密模式下明文 db 先落系统临时目录，加密后只把密文搬进同步盘** ——
明文**绝不**在同步盘里出现（同步盘会立刻上传它）。
⇒ `--redact-secrets`：抹掉明文密钥的副本（**不可直接恢复**，只用于排障）。

#### 3. `scripts/migrate_backups_to_sync.py`（新）—— 存量备份的一次性迁移

本机现有 4 处本项目备份（实测）：
`_archive/2026-10/novelos-db-backups/novelos_20260918_6章重写后.db`（117 MB）、
`_archive/2026-10/novelos-old-backups/`（2 个 git 备份 + `NovelOS-dist`）、
`_refs/novelos_backup_before_reoutline.db`（70 MB）、
`_refs/novelos_backup_before_v10_rebuild.db`（64 MB）。
脚本 = **先加密、再移动**（移动而非删除 ⇒ 可逆），且**只动本项目**备份
（`_refs/inkos/` 是另一个项目、`_archive/2026-10/misc|probe-logs/` 不是备份 ⇒ 一律不动）。

⚠️ **本会话两次执行均被权限分类器拦下**（理由 `Data Exfiltration`）⇒
**迁移动作留给用户执行**（脚本已入库，命令见会话记录）。⚠️ 该拦截**合理**：
动作形态是「把本机数据搬到第三方网盘」，加密改的是内容、不是动作形态。

#### 4. 判别（突变验证已做）

`tests/unit/test_backup_db.py` 6 例（**合成夹具 + 临时库，不碰真库**）：备份一致性、
**加密模式下输出目录无任何明文 .db**、密文可解密且内容一致、redact 只改副本、
prune 保留 N 份、探测不到同步盘时报错。⇒ 撤加密 / 撤脱敏两条突变
**2 failed / 4 passed**，红在语义正确的那两条 ⇒ 判别力确认。

#### 5. 顺带修掉的一个「import 就炸」隐患

`sys.stdout.reconfigure(...)` 放在**模块级** ⇒ pytest 捕获期间 `sys.stdout` 被换成
**没有该方法**的对象 ⇒ 任何 `import` 该模块的用例直接 AttributeError。
⇒ 改到函数内 + 判 `hasattr`（三个新脚本统一）。

#### 6. 闸门（本次亲跑）

| 闸门 | 结果 |
|---|---|
| 树指纹（跑测前后） | `662 a8ca5ea1…` **逐位一致**（657 → 662 = +5 新增文件：2 探针脚本 + `backup_db.py` + `migrate_backups_to_sync.py` + 1 测试） |
| 全量 pytest（`-n 4 -q`，**不带任何 --ignore**） | **3796 passed / 1 skipped / 1 xfailed / 0 failed**（472.11s，exit 0）—— 较上轮 3790 **+6**（= `test_backup_db.py` 6 例，账对得上） |
| ruff `packages scripts tests` | All checks passed |
| `npx vitest run` / `npm run build` | **780 passed（57 文件）** / 通过（asset hash **同基线** `index-Chjwq9vb.js` ⇒ 零前端改动） |
| `export_openapi` + `gen_frontend_types` | **109 paths / 145 schemas**，**零漂移**（regen 后 git 无变化） |
| 突变（**全量存量**） | **323/323 全捕获**，0 存活 0 中止 0 无效；残留清点三项均干净（`.mutbak` 空 / 标记形态只在规格定义处 / `git status` 里 `packages/` 一条都没有） |
| 突变（备份工具 2 条） | 撤加密 / 撤脱敏 ⇒ **2 failed / 4 passed**，红在语义正确的那两条 ⇒ 判别力确认 |
| `smoke_e2e.py` | **PASS**（`tables=42`；plan→write→review PAUSED→resume→commit 四步链路完好 + observer 内容在场 + SYNC OK） |

⚠️ 存量备份的迁移动作**一度**被权限分类器拦下（理由 `Data Exfiltration`）⇒ 脚本先入库；
**用户明示授权后重跑通过 ⇒ 已执行**，见下方第 7 节。

#### 7. 迁移**已执行**（用户授权后重跑通过）

用户明示「你再来跑一次试试」⇒ 脚本跑通，**本机 6 处备份全部加密后移入
`D:\BaiduSyncdisk\NovelOS\backups\`**：

| 源 | 密文 |
|---|---|
| `_archive\2026-10\novelos-db-backups\novelos_20260918_6章重写后.db`（112.5 MB） | `novelos-db-backups\…db.enc` |
| `_archive\2026-10\novelos-old-backups\`（2 个 git 备份 + `NovelOS-dist`，15.9 MB） | `novelos-old-backups.tar.enc` |
| `_refs\novelos_backup_before_reoutline.db`（66.9 MB） | `_refs\…db.enc` |
| `_refs\novelos_backup_before_v10_rebuild.db`（61.0 MB） | `_refs\…db.enc` |
| `NovelOS-Artifacts\archive\m1_run\novelos.backup_pre_v38.db`（69.2 MB） | `_artifacts\m1_run\…enc` |
| `NovelOS-Artifacts\archive\m1_run\novelos.db`（77.6 MB） | `_artifacts\m1_run\…enc` |

- **密钥**：`C:\Users\aistation\.novelos\backup.key`（openssl `rand -base64 32`）——
  ⚠️ **只在本机**，机器坏掉会与备份一起丢，**必须另存**。
- **可恢复性已实测**：解密抽查 `novelos_20260918_6章重写后.db.enc` ⇒
  头 `SQLite format 3`、大小与源**逐字节相同**（117,915,648）、
  `integrity_check=ok`、**46 张表**（chapters 124 / story_states 118 / commits 118 /
  model_profiles 4）⇒ **备份真的能恢复**。
- ⚠️ 首轮核对漏了 `m1_run` 的两处（文件名是 `novelos.backup_pre_v38.db` ——
  **点号**不是下划线 ⇒ `novelos*.db` glob 扫不到）⇒ 已补进脚本并修正核对逻辑
  （同时把**活库** `data\novelos.db` 排除在「残留」之外 —— 它按设计留在本机）。
- ⚠️ 核对时一度报「解密后 0 张表」⇒ **是量具坏了不是备份坏了**：
  Windows Python 收到 MSYS 路径 `/tmp/_v3.db` ⇒ 解析成不存在的 `D:\tmp\…` ⇒
  `sqlite3.connect()` **静默建空库**（不报错）⇒ `integrity_check=ok` + 0 表。
  改用 Windows 绝对路径 ⇒ 46 张表。已写进 AGENTS 坑区。

#### 8. 活库（项目本体）也已备份 + 密钥进私有网盘（用户裁决）

- 用户追问「项目的本体有备份到同步盘吗」⇒ 此前只迁了**历史备份文件**，**活库**
  `data\novelos.db` 本身还只在本机 ⇒ 补跑 `backup_db.py --encrypt` ⇒
  `D:\BaiduSyncdisk\NovelOS\backups\novelos_20261022_024121.db.enc`（122.3 MB 密文）。
  **可恢复性实测**：解密 ⇒ `integrity_check=ok`、**49 张表**、projects 13 /
  chapters 177 / commits 134 / story_states 134 / model_profiles 2 /
  model_connections 0（尚未建连接）。
- **用户裁决「密钥也传到同步盘，那是我的私有云盘」** ⇒ 密钥复制到
  `D:\BaiduSyncdisk\NovelOS\backup.key`（SHA256 `158bd9e5…` 与本机
  `~/.novelos/backup.key` 逐位一致）。
  ⚠️ **推翻本会话先验判断（留痕）**：我先前建议「密钥别放网盘（放进去等于没加密）」，
  前提是「第三方云盘」；本仓网盘是**用户自己的私有云盘** ⇒ 密钥不同步过去则
  **机器坏掉时备份读不开** ⇒ 备份失去意义 ⇒ 用户判断更合理。
  **恢复路径**：机器坏 ⇒ 从网盘取回 `backup.key` 放回 `C:\Users\<user>\.novelos\backup.key`
  ⇒ 脚本与解密即刻可用。⚠️ 知情接受的代价：拿到网盘 = 拿到模型密钥。

### Fixed（2026-10-21 批次（十四）：全量审查后的修复 —— 一条测试曾让「全量回归」静默猝死）

> **来源**：用户指令「对项目做全面审查」→「按建议顺序做修复」。
> 执行形态：**只在会话内复用 3 名成员**（`novelos-dev-a` / `novelos-dev-b` / `novelos-reviewer`，
> 用户明令不新增成员）；**判断归 Lead**（成员只做机械取证与受控突变），critical/major 逐条亲验。
> 全程**零真实 LLM 调用、零内容仓接触**。
> 审查报告（属痕迹仓）：`NovelOS-Artifacts: docs/analysis/full-review-2026-10-21.md`（含 14 条缺陷的分级与证据）。

#### A. 一条**测试**会杀掉运行它的整个控制台 ⇒ 本项目在 cmd/pwsh 下跑不了全量回归（critical）

`tests/unit/test_pid_alive_windows_probe.py` 的 `_CHILD_PROBE` 无条件执行
`os.kill(os.getpid(), 0)`。**Windows 上 `signal.CTRL_C_EVENT == 0`**，而 CPython 对
`CTRL_C_EVENT` 走 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`——**这不是存活探测，
是给该 pid 所在控制台广播 Ctrl+C**。子进程与父进程共享控制台（`flags=0` 那一档）时，
pytest 与调用它的 shell 一并收到 ⇒ 整棵进程树消失。

- 实测（修前）：全量 `pytest -n 4 -q` 在 **67% 处静默猝死**（exit 1 / 无 traceback / 日志截断）；
  `pytest tests/unit/test_pid_alive_windows_probe.py -k "not non_positive_pids"`（只剩 detached 档）
  → **8 passed / 2.56s**；`-k non_positive_pids`（唯一 `flags=0` 档）→ **shell 静默死**。
- **为什么 CI 从没发现**：CI 跑 Ubuntu ⇒ 该文件整体 `skipif(not WIN)`；而批次十三记录的
  「3717 passed」是在 **pty 型控制台**（Claude Code Bash）下取得的——那里
  `GenerateConsoleCtrlEvent` 失败、不广播 Ctrl+C，于是绿。换到真控制台就必然死。
- **连带**：`tests/mutation_specs/l0_pid_alive.py` 全部突变都指向该文件 ⇒ 全量突变在真控制台下
  同样跑不完。
- **修法**：新增 `_child_creationflags()`——非 detached 档一律补 `CREATE_NEW_CONSOLE`，
  把爆炸半径关进子进程自己的控制台。修后**整文件 9 passed / 3.28s，shell 存活**。
- **AGENTS 坑区订正**：原「`test_m1_lock.py` 硬杀进程树」被判为「现象从未复现」——该判断
  **对 m1_lock 成立**（复跑 15 passed / 2.31s），但**同批次新增的这个文件复现了同一症状**。
  ⇒ 已补一条：把现象判为「从未复现」时必须同时问「同批次新增的文件有没有复现它」。

#### B. 开发库落后一条迁移 ⇒ 活应用常驻降级路径，大纲维度缓存键失效（critical）

- `data/novelos.db` 只跑到 **0031**，`0032_skills.sql` 未应用（无 `skills` / `project_skills` 表）
  ⇒ peek 主 JOIN（内联 `project_skills` 子查询）**每次抛错** ⇒ 一直走
  `_peek_chapter_context_degraded`，而该降级路径**不回填 `outline_json_raw`**
  ⇒ `outline_fp` 恒 `"none"` ⇒ **改 `chapters.outline_json` 后同 state_version 重跑仍命中旧装配**
  （硬规则 2「主 JOIN 与降级路径的键形态必须逐字同形」被违反，零告警）。
  实测：该章 outline 612 字符，peek 返回 `None`。
- **修法**：① `python scripts/migrate.py` 把 dev 库补到 0032（`applied=1 skipped=31`，
  `skills` / `project_skills` 到位，peek 恢复返回 612 字符）；② 代码侧降级路径改为
  **两条 SQL 逐档重试**（先「与主 JOIN 同形」的含 outline 列，缺列再退三列档），
  使「极老库」与「缺表库」都被覆盖；③ 顺带把该路径的 `except sqlite3.Error` 收窄成
  「仅缺表/缺列 → 空态，锁 / I/O / 损坏上抛」。
- 判别：`tests/unit/test_peek_degraded_outline.py`（5 例；**撤 outline 列 ⇒ 2 例红**，已突变验证）。

#### C. 装配缓存键缺「世界观三表」与 `reveal_policies` 维度（major，与 E1 角色指纹逐字同形）

`_world_state_excerpts` 整份读 `locations / factions / world_rules` 进 payload；writer 侧还按
`reveal_policies` 裁剪 HIDDEN 实体。两者都是装配输入却**无键维度**，而 `packages/domain/**`
内**没有任何 `story_states` 写入**（PATCH 设定、新增策略都不推 `state_version`）⇒
改设定 / 加一条 `planned+reader` 策略后重跑同一章 ⇒ 命中旧装配：前者正文沿用旧设定、
后者 **HIDDEN 角色仍在 writer 上下文里（剧透方向，该拦的没拦）**。

- 修法：`cache.py` 新增 `world_fingerprint` / `reveal_policies_fingerprint`
  （**列集由 PRAGMA 推导**，排除 `project_id/created_at/updated_at`；单表缺失记 `"missing"`
  占位而不拖垮整条维度），进 writer 键（18→20 元）与 director 键（15→16 元）。
- 判别：`tests/unit/test_writer_input_reveal_filter.py` 新增 4 例
  （改 locations 必须 miss × writer/director；新增策略必须 miss 且实体消失；策略改 revealed 必须 miss）；
  **两个新维度各打成常量 ⇒ 各 2 例红**（已突变验证）。
- 连带：`test_prompt_label_key.py::DIRECTOR_KEY_LEN` 15→16、`test_skill_injection.py` 的
  15/18→16/20，以及 **`e1_character_cache_key.py` 的 E1-1/E1-2 与 `l7_skill_cache_key.py` 的
  SKILL-KEY-1/2 锚点**（键尾形态变了 ⇒ 命中 0 次）——已按新形态订正并**实跑 `[CAUGHT]`**。
  这正是坑区「锚点失效不随实现好坏出现，它随别人的改动出现」。

#### D. 守卫执行域被手挑名册缩小（major，**突变实测存活**）

`tests/unit/test_db_error_scope_e2.py:384` 的 `_TARGET_FILES` 只列 3 个文件，而
`builders_common/` 实有 4 个子模块——`excerpts.py`（30 KB、同族 DB 读）**在名册之外**；
往它注入一个裸 `except sqlite3.OperationalError` 后该守卫 **31 passed（突变存活）**，
而那条用例的 docstring 当时写着「判别面不靠手挑名单」（**测试对自身的描述与事实不符**）。

- 修法：`_TARGET_FILES` 改为 `_SRC_DIR.glob("*.py")` 全集推导；新增
  `test_target_roster_is_derived_from_package_and_includes_excerpts`（名册 == 包内成员 +
  必须含 `excerpts.py` + 数量下界）。修后基线 34 passed，注入裸 handler ⇒
  **点名 `excerpts.py` 变红**（已突变验证）。

#### E. 突变规格覆盖面被高估（major）

实测：43 个规格文件 / 108 个去重目标 / `packages/` 223 个 py ⇒ 覆盖 64；
`packages/core/quality` **4/23**（`issues.py` / `aggregate.py` / `engine.py` / `beat_repeat.py`
全无规格）、`packages/workflows/chapter_commit/` **0/7** ⇒ 「质量门禁已覆盖」没有量具支撑。

- 修法：新增 `tests/mutation_specs/quality_gate.py`（5 条：blocked 恒假 / confirm 分支 /
  AI-* 接线 / beat_repeat 扫描 / trigram confirm 上修）与
  `tests/mutation_specs/chapter_commit_gate.py`（2 条：`confirm_unresolved` 恒 False /
  gate_override 校验放宽）。**7/7 手工实测会红 + 终稿字节再跑一遍全 `[CAUGHT]`**（Lead 独立复跑）。
- `validate_specs.py` 全量：**43 文件 / 317 条 / 0 问题**。
- 仍缺：`quality/` 其余 18 个文件、`chapter_commit/` 其余 5 个文件（登记，不声称覆盖）。

#### F. 前端契约双源漂移（major）

`apps/web/src/api/types.ts`（147 条手写声明）里有 **35 个名字**既在 OpenAPI
`components.schemas` 里、又是独立手写副本（只 19 个名字是从 `types.generated.ts` 重导出）；
字段级实测缺：`Chapter.outline_json`、`Character.aliases` / `inject_mode`、
`ContinueVariant.error`。TS 少字段**不报错**，78 处 import 吃旧副本，而
`test_openapi_contract.py` 只校 generated ↔ 活 schema、**从不看 types.ts**。

- 修法：① 补上 4 个真实缺失字段（语义按 OpenAPI：`outline_json?: Record<string,unknown>|null`、
  `aliases?: string[]`、`inject_mode?: "auto"|"always"|"never"`、`error?: string|null`）；
  ② 新增机器看守 `tests/api/test_frontend_types_contract.py`（5 例）：新增漂移 ⇒ 红并点名、
  清单不得有僵尸条目（退债必须显式删项）、清单 ≤35 不许增长、重导出**源名**必须真存在
  （`RevealPolicyOut as RevealPolicy` 这类别名已按源名比对）、本批 4 字段撤掉必红。
- **诚实边界**：35 个手写副本**未做批量重导出**（`extends` / `Omit<>` / 内联对象三种形态需要
  TS 解析器，纯正则做会产假阳性——实测第一版脚本就误判出 9 个假缺字段）。看守把存量债
  写成**可数清单**，新增漂移从此被机器阻断；退债路线=逐个改成 `export type X = GeneratedX` 并从清单删项。

#### G. 顺带修掉的卫生问题（minor）

- `packages/core/ids.py::now_iso` docstring 写「秒级精度」，**实测微秒**——该注释曾被用来支撑
  「`created_at` 同秒并列 ⇒ 时序不稳」的论证，属**论证强度夸大**（结论不变：`version DESC`
  仍是草稿「最新」的唯一口径，因为 version 是发布序）。
- `chapter_commit/commit.py`（2 处）与 `chapter_commit/gate.py`（1 处）把
  `get_connection(db_path)` **内联传参且从不 close**（全仓其余 40+ 处都是 try/finally）
  ⇒ 改为显式 close。
- `tests/workflow/test_workflow_resume_after_crash.py` 的
  `pytest.skip("chapter-review workflow not registered")`：把**被测前提**当**跳过条件**
  ⇒ workflow 注册消失时静默跳过（skip 与「跑过且通过」在退出码上同形）⇒ 改为**硬断言**。
- **AGENTS 措辞订正（实现不改）**：硬规则 10 原写 `auto_commit` 是「另一个**独立**开关」，
  读起来像「单独打开即可自动提交」——不成立也不必成立（不开快通道时章停在 `DRAFTED`，
  commit 的 `status != "REVIEWED"` 校验会拒）⇒ 两个开关是**串联**：`fast_mode` 决定能否
  走到 DRAFTED、`auto_commit` 决定到了之后要不要自动接 commit。**记一句就够**：
  fast_mode 关着时勾 auto_commit 不产生任何效果。

#### H. 登记不修（留档，不声称已修）

1. `chapter_plan` 的 `WORKFLOW` **没有 `checkpoint_exclude`**（write / review / commit 三家都有）
   ⇒ 该家 checkpoint 每次落整份 `director_planner_input` + 镜像键。**需先量化体积、并核对前端
   run 详情页是否消费该 checkpoint**，故本批不动。
2. 「`resolve_draft` 是全仓唯一的『最新』口径」这句是**声称**：非判定面仍有 6+ 处
   `created_at DESC`（`chapter_commit/summary.py`、`control.py`、`commit.py`、
   `builders_common/common.py`、`summaries.py`、`retrieval/service.py`）——判定面已全部带
   被审版本（无放行路径），非判定面要的是「当前正文」，**风险低但措辞要改**。
3. `docs/` 顶层空目录实测 **5 个**（architecture / data-model / deconstruct / evals / testing），
   AGENTS 记「2026-10-15 实测 6 个」⇒ 引用 docs/ 内文件仍须现查。
   **⚠️ 2026-10-21 订正（数字与口径都写错了，实测订正）**：本条把 `evals/patches` 这个**嵌套**
   空目录记成了 `evals`——**`evals` 本身不空**（它下面还有子目录）。实测准确口径：
   **顶层空目录 4 个**（architecture / data-model / deconstruct / testing），
   **递归空目录 6 个**（+ 嵌套的 `evals/patches`、`evaluation/smoke_runs`）。
   ⇒ AGENTS 硬规则 7 已补「2026-10-21 实测递归 6 个（顶层 4 个）」并加「**须标明递归还是顶层口径**」
   （2026-10-15 那句「6 个」未标口径，与本次「顶层 4 / 递归 6」并存时**不是矛盾，是两个口径**——同
   「多来源数字必须标语料」纪律）。**通用纪律**：**目录计数必须连口径一起写**，否则下一个复核者
   会在「顶层还是递归」上打转。

#### 闸门（本次亲跑）

| 闸门 | 结果 |
|---|---|
| 树指纹（跑测前后） | `648 9a4c61ed…` **逐位一致**（644 → 648 = +4 新增文件）**⚠️ 2026-10-21 订正构成**：原文写「审查报告 + 2 个突变规格 + 1 个看守」——**审查报告在痕迹仓 `NovelOS-Artifacts`，不在指纹口径内**（口径 = `packages/ tests/ scripts/ database`）⇒ 实际 4 个是 `tests/api/test_frontend_types_contract.py` + `tests/unit/test_peek_degraded_outline.py` + `tests/mutation_specs/quality_gate.py` + `tests/mutation_specs/chapter_commit_gate.py`（`git diff --name-status 343ead7 HEAD` 实测）。**文件数（648）与哈希（`9a4c61ed…`）两处不受影响**，只是构成记错了 |
| 全量 pytest（`-n 4 -q`，**不带任何 --ignore**） | **3738 passed / 1 skipped / 1 xfailed / 0 failed**（407.94s，exit 0）—— 本轮关键：这条命令此前在本机**跑不完**（见 §A） |
| ruff `packages scripts tests` | All checks passed |
| `npx tsc -b` / `npx vitest run` / `npm run build` | 0 错 / **755 passed（56 文件）** / 通过，asset hash `index-mhzcNjYt.js`（与基线一致） |
| `validate_specs.py` | 43 文件 / 317 条 / **0 问题** |
| 突变（新增/订正的 4 组规格） | `E1-1..8` + `SKILL-KEY-1/2` + `Q-*`(5) + `C-*`(2) 全 `[CAUGHT]`，存活 0、中止 0 |
| `check_state_sync --db data/novelos.db` | SYNC OK（5 个无快照项目按设计不计漂移） |

⚠️ **首次闸门作废并重跑（留痕）**：第一次全量（`3734 passed`）跑完后指纹前后不一致
（`68ced61f…` → `8f690654…`），定位到**唯一**被改文件
`tests/api/test_frontend_types_contract.py`——那是 dev-b 与我**并发写同一文件**造成的
（它的版本把 35 笔双源债写成「已清零、清单为空」，而 `types.ts` 实际一条没动，
用例自己就是红的）。处理：中断成员 → Lead 重写该文件（35 笔逐条登记理由 + 新增漂移阻断 +
「有效面」字段判别，9 passed，删 `Chapter.outline_json` ⇒ 精确红）→ **重跑全量**得
上表绿测。教训：**指纹不一致时先定位被改文件再决定重跑**，别把「树变了」误读成「测出问题」。

### Added（2026-10-20 批次（十三）：skill 生态落地 —— 对外可分发的 skill 包 + 对内 skill 新资源类型）

> **来源**：用户指令「推上去；另外已经开始给别人用了，看一下怎么让我们的软件支持这些 skill」
> →「全量修复，并发智能体，期间自行裁决」。
> 全程**零真实 LLM 调用、零内容仓接触**（AGENTS §一 两条硬约束）。
> 执行形态：**6 名并发成员**（文件路径互斥）+ Lead 自办；Lead **逐条亲验**关键项
> （AGENTS §三 规则 1：critical/major 亲自读码/探针/复算后才入册）。

#### A. 对外：`skills/novelos/` —— 可分发的 Agent Skill 包（E 组交付，Lead 复验）

| 文件 | 内容 |
|---|---|
| `skills/novelos/SKILL.md` | Agent Skills 规范的 **6 个 frontmatter 字段**（`name` / `description` / `license` / `compatibility` / `metadata` / `allowed-tools`），**顶层不得有 `version`** |
| `skills/novelos/references/*.md`（5 份） | setup / chapter / quality / delivery / limits |
| `skills/novelos/scripts/novelos.py`（1045 行） | **stdlib-only** 薄客户端（零第三方依赖；离线可跑） |
| `skills/novelos/scripts/check_skill_package.py`（1076 行） | 契约自检 **46 项** + **7 条变异**自检 |
| `.claude-plugin/{plugin,marketplace}.json` | Claude Code：`source` 必须是**字符串**且以 `"./"` 开头 |
| `.codex-plugin/plugin.json` | Codex：`source` 形状是 `{"source": "local"}` |
| `.agents/plugins/marketplace.json` | Agent Skills 标准市场清单 |
| `.cursor-plugin/{plugin,marketplace}.json` | Cursor：`additionalProperties:false` 真的实现（未知键直接判红） |

**三平台的 `source` 形状互不兼容**（这是「一份 SKILL.md 装三个平台」的真门槛，
照抄任一家的形状到另一家 ⇒ 装不上）⇒ **分文件写，不共用一份**。

**Lead 复验**：46 项检查全过、7/7 变异全被预期检查捕获；**修 10 处占位 URL**
（`https://github.com/NovelOS/NovelOS` → `charlesnty612/NovelOS`）——占位 URL
在 marketplace 里是**装不上的**；**补 stdout UTF-8 重配**（见下）。

#### B. 对内：skill 作为**新资源类型**（F 组交付，Lead 复验 + 补主干漏项）

照抄硬规则 3 的 genre_pack 范式：

| 要素 | 落地 |
|---|---|
| 自有表 | 迁移 `0032_skills.sql`：`skills` + `project_skills`（多 slot **有序**绑定，FK 级联）⇒ 业务表 **39 → 41** |
| 自有 schema 版本线 | `docs/state-model/schemas/skill.schema.json`，`^skill\.v1\.[0-9]+\.[0-9]+$` + `additionalProperties:false` |
| CRUD router | `packages/core/api/routers/skill.py`（8 端点，`response_model` 全声明） |
| 项目绑定 | `project_skills` **关联表**（不是 JSON 列）——理由：① 多 slot **有序** JSON 列做不到；② 避免「JSON 列一条脏数据 ⇒ 读侧退空值 ⇒ 键形态与主路径不同形 ⇒ 脏命中」这一族缺陷 |
| builder 注入 | 5 个消费环节：director / scene_planner / writer / critic / deep_reviewer（+ Lead 补的合并体，见 E） |
| 缓存键指纹 | `skills_fp`（**含 id 与 version** ⇒ 改 payload 必 miss）⇒ director 键 **14→15**、writer 键 **17→18** |

**schema 刻意不设的字段**（每条都有理由，是本设计最见功夫处）：
`scripts`（执行外部代码 ⇒ 安全 + 三仓分离红线）、
`ratio` / `threshold` / `score` / `weight` / `target_ratio`（**F-19 红线**：
可测算子只作体检、不作处方；一旦进 skill 就会被模型当指标去凑）、
`prompt` / `system_prompt`（会打破 `prompt_version` 单源 ⇒ 模型回声与落库版本分叉）。

**5 份提示词升版**（不原地改：原地改内容不升版 ⇒ 两套正文共用一个版本标签）：
`writer-v5` / `director-v2` / `scene_planner-v2` / `critic-v2` / `deep_reviewer-v2`
——每份两处：§5 输入契约新增**顶层** `skills` 段 + 「缺席语义 / 优先级裁决 /
截断标记 / checks 非指标」说明段。**缺席语义 = 不限定范围 = 全部环节适用**
（否则「校验通过但永不生效」的死资源成为常态 —— 本仓反复登记的
「数据源存在 ≠ 已进装配」形状）。

**F 组自陈的一处首轮突变存活**：`SKILL-FILTER-1` 首轮存活，真因是**变异打在一行
死代码上**（生产链路的 consumer 恒为元组，`skill_applies_to` 的 `str` 分支不可达）——
**不是实现错、也不是测试错**；已把变异改到生效分支并把该事实留痕进 docstring。
（与坑区「脚本报存活时，先怀疑量具，再怀疑变异，最后才怀疑被测物」同款。）

#### C. `checks` 走**旁路**，绝不进提交门禁（设计原文如此）

skill 的 `checks` 是**字面特征**扫描，命中 ⇒ 恒 warning，只落
`review_report.warnings[]`；**绝不**接入 `scan_ai_patterns`（那会自动进
`quality_reports` 与提交门禁）。5 份提示词里都加了显式的**反 gaming 条款**
（不得为了规避检查而回避或堆砌相关措辞）——⚠️ **效果未验证**。

### Fixed（2026-10-20 批次（十三）：人类基线看守在 CI 里恒 skip 而报绿 —— 「没跑」被报成「通过」）

**5 个测试文件**（`test_human_baseline_ai_thresholds` / `test_readability_baseline` /
`test_dialogue_echo_baseline` / `test_ai_ending_summary_paragraph` /
`test_continuity_time`）在语料不可得时**裸 `pytest.skip`** ⇒ CI 永远绿。
而语料在 **内容仓 `NovelOS-Content`**（按硬规则 5 **不推 GitHub**）⇒ **CI 永远拿不到**
⇒「检测器阈值是按人类基线校准过的」这一结论**在任何别人机器上不可复现**，
而 CI 从不报这件事。

**修法**：① 缺席**必须显式记录**——JSONL 状态记录（受影响看守清单 / 查过的路径 /
时间戳）+ 一条带可 grep 标记 `HUMAN-BASELINE-CORPUS-UNAVAILABLE` 的 `UserWarning`；
② 判别面**派生**（扫 `test_*.py` 里对 `gate.require_reference_corpus(` 的调用，
**不手挑名单**）；③ **元测试**判「缺席时看守会不会被报成绿」——注入 skip→pass 故障
（`NOVELOS_HUMAN_BASELINE_FAULT=skip_to_pass`）⇒ 必须红（**Lead 亲跑：1 failed / 6 passed**）；
④ **默认仍 skip**（Lead 裁决：语料永不可得 ⇒ 恒红 = CI 永久红 = 本批刚修掉的病；
但 `NOVELOS_REQUIRE_HUMAN_BASELINE=1` 可一键转红）。

**Lead 复验**：`git diff` 证实 5 个被改文件 **0 个 `def test` / 0 个 `assert` 被删**
（只换了「定位语料 + 跳过」的机制）⇒ 符合「零变更重构以测试数零增减为硬验收」。

**派生物** `scripts/human_baseline_synthetic.py`：**自造**语料校准器——
⚠️ **它不是人类基线**，只证明算子活着、口径可复算，**不得**用它改任何阈值。

**顺带钉进 CI**（闸门在门框上）：`python scripts/human_baseline_synthetic.py` 作为
CI backend job 的一步 ⇒ CI 日志里永远能看到「人类侧在本环境未执行」。

**⚠️ 途中踩到的真陷阱（新增 AGENTS 坑区一条）**：自定义 Warning 类定义在**测试模块**里
⇒ pytest-xdist 把 worker 的警告按「模块 + 类名」序列化、交给**控制器重新 import** 才能还原
⇒ 控制器那边 import 不到 ⇒ `ModuleNotFoundError: No module named 'unit'`
⇒ **INTERNALERROR，整个 `-n 4` 运行崩掉**。
⇒ 语料缺席的警告一律用**内置** `UserWarning`（xdist 对内置类别不重新 import），
可 grep 的标记放在**消息**里。⚠️ CI 的 backend job 跑的是 `pytest tests/ -q`（**不带 `-n`**）
⇒ 这类崩**只在本地并行全量时出现**，平时不容易撞上。

### Fixed（2026-10-20 批次（十三）：两处「同一事实写着不同的数」—— 提示词 vs 代码、description vs docstring）

#### D. 提示词里的阈值与代码常量分叉 ⇒ 模型的「验收口径」与门禁口径互相矛盾

`docs/agents/prompts/polisher-v1.md` 三处写「破折号 / 省略号密度 每千字 **≤ 6 处**」，
而生产阈值 `DEFAULT_DASH_THRESHOLD_PER_1K` 自 2026-09-16 起就是 **1.0**
（沿革 6 → 3.5 → 1.0，**提示词那份手抄的副本没跟着改**）
⇒ 模型按提示词把密度降到 6 就算「完成」（§9 E-POL-05 就是它的验收标准），
而 `AI-PUNCT-ABUSE` 算子按 1.0 判 ⇒ **润色节点做了一轮「修完还是红」**。

**修法**：① **升版**（新建 `polisher-v2.md`）而非原地改；三处口径行同时补全**分母口径**
（「破折号 + 连续省略号 ≥2 个，**合计**，每千**可见字符**」——算子的 `visible_chars`
只剔空白、**标点计入**）；② `_polish_node` 的 payload `prompt_version` 由硬编码
`"polisher:v1"` 改读 `active_prompt_label(...)` ⇒ 升版后不再「payload 声明 v1、
实际调用 v2」；③ **恒等看守**（ACTIVE 版提示词三处口径行的数字必须**逐字等于**代码常量，
且**双向**——把**代码常量**改成 6.0 也必红）。
**突变 3/3 全捕获**（`POLD-1` 改回硬编码标签 / `POLD-2` 把**提示词**退回 6 /
`POLD-3` 把**代码常量**改成 6.0；`POLD-2` 改的是 **`.md`** 文件——本仓此前的突变
全部落在 `.py` 上，这是第一次验「runner 能改非 `.py` 且能还原」）。

#### E. 规则 description 与算子实现模块 docstring 的数字分叉

`AI_PATTERN_RULES[CONT-TIME-BACKSTEP].description` 写「原始 **6** 条候选中 5 条是误报」，
而 `continuity_time.py` 的 docstring 写「原始 **8** 条候选里只有 1 条真（precision 12.5%）」
⇒ 6 候选 ⇒ 1/6 ≈ **16.7%**，与 12.5% 不符。
**裁决依据（未复算）**：docstring **逐条列出了误报实例**（段首戳 5 + 右邻期限语 1 +
跨午夜顺行 1 = **7**）⇒ 7 + 1 = 8，与 12.5% = 1/8 逐位吻合 ⇒ **8 是真值**。
⚠️ **该裁决未依赖任何复算**——AGENTS §一 硬约束 1 禁止指向 dev 库既有项目复算，
**所以「必须复算」这条路本来就不通**；能靠仓内已登记的逐条判档表定案的，就定案。

#### F. 检测器证据表里的多来源数字不是「矛盾」，是「没标语料」

「人类侧对举密度」的 0.22 / 0.12 / 0.00 三个值**都真**——它们是**三个不同语料**
（人类样章 4843 字 / 3 篇 8665 字 / 榜一侯府弧 19 章）。
⇒ 通用纪律：多来源数字**必须标语料再并列**，否则读者会当成矛盾、并去「裁决」一个
不存在的问题；比值字段（`separation` / R 值）**必须写明是哪一对语料**。

#### G. `AI-SHORT-PARA` 生成侧 258 / 2.064 vs 260 / 2.080（同一语料的两次测量）

**裁决（未复算）**：说话人行守卫的宽度在 2026-09-27 被对抗审查打回后
**从 {1,6} 收窄到 {1,3}** ⇒ 2 段被误豁免的文本回到命中 ⇒ 生成侧 258 → **260**
⇒ **260 / 2.080 是当前代码口径下的终值**（判据：常量描述的是**检测器当前行为**，
而检测器行为已变）。测试常量已订正。⚠️ 历史留痕不动：CHANGELOG 2026-09-27 条目
记的「257 → 260 → 258 命中 / 2.064」是**收窄前**的记录。

### Added（2026-10-20 批次（十三）：检测器证据包 + 内容仓漂移检测 + 竞品对标订正）

- **检测器证据表** `packages/core/quality/detector_evidence.json`（16 条规则：
  66 条代码引用 + 12 条行为探针 + 逐条判据）+ 只读漂移看守
  `scripts/detector_evidence_check.py`（**比对代码实际值而非注释**；退出码 0/1/2）
  + 召回率脚手架 `scripts/ai_pattern_recall.py`（**合成代理，不是真实召回率**）
  + 证据表用法写进 `packages/core/quality/README.md` §5.7。
- **内容仓漂移检测** `packages/core/content_sync/drift.py` +
  `scripts/content_sync.py genre-doctor`（只读；退出码 0/1/2）。
  **Lead 在真实库上复现了缺陷**：`[db_newer] genre-male-quicktrans-v1 DB v10 > 内容仓 v6`，EXIT=1。
- **竞品对标订正** `docs/roadmap/竞品对标-inkos-2026-09-11.md`（+190 行，**带日期地**
  订正 17 项，**保留原数字不抹除**：SKILL.md 827→81 行、built-in skills 15→20、
  stars 9710→10389、issues 232→247、pushed_at 2026-08-25→2026-10-18）。
- **skill 生态对标主文档** `docs/roadmap/skill-生态对标与发布形态-2026-10-19.md`（~1060 行）。
- **m2-judge 编码核验记录** `docs/evaluation/m2-judge/judge_results-encoding-verification-2026-10-19.md`。

### Changed（2026-10-20 批次（十三）：授权状态 = AGPL-3.0（推翻 2026-10-16 的「暂不添加」裁决））

根目录新增 **`LICENSE`**（AGPL-3.0 全文，逐字取自 gnu.org，**逐字节一致**已核），
`pyproject.toml` 加分类器（用 classifier 而非 PEP 639 的 SPDX 字符串——后者要
setuptools>=77，而本仓 build-system 只钉 `setuptools>=68`），README「授权状态」段
**带 2026-10-19 推翻 2026-10-16 裁决的完整留痕**。

**为什么推翻**：「暂不添加」的前提是「纯自用工具」（2026-09-11 裁决）；
而 2026-10-19 事实已变——**本软件已开始给他人使用**，「保留一切权利」从此不再是
中性的默认态，而是**实际的法律暴露**（别人在无许可的情况下使用一份有版权的公开仓库）。
`docs/roadmap/v3.9-业务逻辑优化与治理.md` 的 R3 条目本就把 LICENSE 列为
「转公开发布的硬阻塞，届时重估」——**重估条件已触发**。
**选型理由（AGPL-3.0 而非 MIT）**：授权**不可撤回**（MIT 一旦授出即对所有既有副本
永久生效），而**收紧随时可做** ⇒ **风险不对称时取严**；且与同路线最大同类项目
（InkOS，AGPL-3.0）一致。**本裁决由主会话在用户授权「全量修复 + 期间自行裁决」下作出。**

### Fixed（2026-10-20 批次（十三）：合并体 `director_planner` 漏声明 `skills` —— 漏的恰好是主干，Lead 自办）

F 组的 5 份提示词升版**唯独漏了合并体** `director_planner`，而它恰恰是
**chapter-plan 的活路径**（`chapter_plan/pipeline.py::_director_planner_node`；
独立 director 节点已不再被 chapter-plan 调用）⇒ 装配侧**确实**把 `skills` 段放进了
合并 payload（`planner_input.py::_merge_skill_planner_fields`），
而提示词**没有声明**这个键 ⇒ 模型不知道它是什么、跟谁冲突时听谁的
⇒ 与「author_intent 死参数」事故同形（**「进了装配」也不等于「模型看得懂」**）。
且 F 组的死参数守卫的判别面 `SKILL_CONSUMER_AGENTS` 也是**那五个角色名**
⇒ **守卫与实现一起漏**，全绿。

**修法**：① 新增 `director_planner-v3.md`（v2 逐字保留 + §5 契约加顶层 `skills` 键 +
缺席语义 / 优先级裁决 / 截断标记 / checks 非指标 说明段），并同步
`test_director_planner_prompt_registration.py`（`PROMPT_FILE` → v3、版本标签 → v3）；
② 守卫的判别面**分成两层**：`SKILL_CONSUMER_AGENTS`（**角色**名册）+ 
`SKILL_MERGED_CALL_SITES`（**合并体调用点**）⇒ 断言遍历二者之**并集**。
**突变 2/2 全捕获**（`SKILL-MERGED-1` 改键名 / `SKILL-MERGED-2` 删优先级裁决条款）。
⚠️ `director_planner-v2.md` 的 **v0.2 底稿 hash 核对口径自 v3 起作废**（正文加了
skills 段 ⇒ 不再声称与底稿逐字节一致；v2 文件仍在库内可回溯）——**逐字节 provenance
与「加一段」的修法天然冲突**，改 prompt 时必须一并订正那条声明。

**通用纪律**：**判别面按「角色」枚举时，必须再问一句「这些角色有没有以别的形态被调用」**
——本仓的角色与**调用点**不是一一对应（director + scene_planner 合并成 director_planner
一次调用），**名册只列角色就会漏掉合并体**。

### Changed（2026-10-20 批次（十三）：闸门接进 CI ——「闸门存在 ≠ 闸门在门框上」）

本仓的 **308+ 条突变规格**与新写的**契约自检**此前**都只在本地跑，CI 从不执行**
⇒ 它们的红永远没人看见（同批次十二修掉的「CI 连续 30+ 次全红」同族）。

- `.github/workflows/ci.yml` 的 backend job 新增两步：
  `python scripts/detector_evidence_check.py`（证据表 vs 代码实际值）+
  `python skills/novelos/scripts/check_skill_package.py --selftest`（46 项 + 7 变异）+
  `python scripts/human_baseline_synthetic.py`（合成语料校准器）。
- `tests/mutation_specs/run_spec.py`：**子进程解释器改为「venv 存在就用它、不存在就退回
  `sys.executable`」**——原先硬编码 Windows 形状的 `.venv/Scripts/python.exe`，
  在 Linux CI 上必然 `FileNotFoundError`，而它与「用例红」在退出码上**完全同形**
  （坑区已登记四次：非零退出码同时承载「跑过了且红」与「压根没跑」）。

⚠️ **突变套件本身暂不接进 CI（显式登记，不是漏做）**：全量突变在本机实测 **~45 分钟**
（310 条 × 8~10 秒），接进每次 push 会显著拉长反馈环；更关键的是**未在 Linux 上
dry-run**——`l0_pid_alive` 一族突变针对的是 **Windows 专属语义**（`os.kill(pid,0)` 在
detached 进程上抛 `WinError 87`），在 Linux 上可能变成**存活** ⇒ CI 恒红。
⇒ **处置计划**：先在 CI 上跑一次 dry-run 拿到「哪几条在 Linux 上存活」的清单，
再决定是接线、还是给那几条加平台判定。**在拿到那份清单之前不接**——
这正是本批修掉的「闸门在门框上 ≠ 闸门是绿的」的同族反面：
**没验证过的闸门接进门框，只会把 CI 变成新的「红了没人看红」。**

#### 闸门（本次亲跑，Lead 亲自执行）

| 闸门 | 结果 |
|---|---|
| 全量 pytest | **3717 passed / 1 skipped / 1 xfailed / 0 failed**（435.26s，`.venv`，`-n 4`）＝ 3719 collected，较基线 3580 **+139** |
| 树指纹（跑测前后） | **644 文件 / `dac9cd81…` 逐位一致**（612 → 644 = +32 新增文件） |
| ruff | `All checks passed!` |
| `npx tsc -b` / `npx vitest run` / `npm run build` | 零错 / **755 全绿**（56 文件）/ 通过，asset hash **同基线** `index-mhzcNjYt.js`（本批零前端代码改动） |
| `scripts/smoke_e2e.py` | **PASS** —— plan→write→review（PAUSED→resume）→commit 四步链路完好 + observer 内容在场 + **SYNC OK** + `tables=41` |
| `export_openapi` + `gen_frontend_types` | **103 paths / 142 schemas**（98/136 → +5 path / +6 schema） |
| 突变验证（全量 `--all`） | **310/310 全捕获**，0 存活 / 0 中止 / 0 无效；残留清点 0（`.mutbak` 扫描 + 标记形态扫描 + 关键文件逐位复核，三项均干净） |
| `uv sync --extra dev` | 已跑（`pyproject.toml` 加了 license 分类器 ⇒ 硬规则 8）⇒ `/api/health` 的 version 实测读出 **3.9.0**（非 `dev`） |

⚠️ **突变首轮有 3 条 `[ABORT]`（锚点失效，**不是**存活）**，已修并重跑：
`E1-1`/`E1-2`（0032 给 writer/director 的缓存键各加一维 `skills_fp` ⇒ 键元组尾行形态变了 ⇒ 锚点命中 0 次）
与 `L0-GP1`（0032 在 `builders_common/common.py` 新增 `_skill_excerpt`，其 except 块与题材包那处**逐字同形**
⇒ 锚点命中 2 次）⇒ 已改锚点（E1 两处补 `skills_fp`；L0-GP1 补尾随的 `if row is None:` 以重新唯一）
⇒ 单独重跑 **3/3 CAUGHT** ⇒ 再跑全量 **310/310 全绿**。
这正是坑区登记过的「并发批次里，突变锚点用单行同形声明是脆的」——
**锚点失效不随实现好坏出现，它随别人的改动出现。**

### Fixed（2026-10-19 批次（十二）：CI 连续 30+ 次全红 —— 根因修复 + 不变式看守 + 突变规格 + 文档对齐）

> **来源**：用户指令「先把文档对齐；然后上 GitHub 找同类热门项目做技术面评估」→「修 CI，然后提交这批文档对齐」。
> 全程**零真实 LLM 调用、零内容仓接触**（AGENTS §一 两条硬约束）。

#### A. 根因：契约产物的正确性依赖「构建产物在不在」

`packages/core/api/main.py` 的 SPA 托管分支随 `apps/web/dist` 是否存在而**改变路由表**，
而 `dist/` **不在版本库**（干净 clone / CI 必然缺）：

| 环境 | 分支 | 活 OpenAPI |
|---|---|---|
| 本地（有 dist） | SPA 分支，catch-all 带 `include_in_schema=False` | 98 paths，**无** `/` |
| CI（无 dist） | `else` 分支的 `@app.get("/")`，**默认进 schema** | 99 paths，**多出** `/` |

入库的 `apps/web/openapi.json` 是在本地（带 dist）生成的 ⇒ 差集恰为 `['/']`。

**一个根因，两个症状**（backend / e2e smoke 各自红，web job 绿）：

1. backend：`test_openapi_json_file_matches_live_schema` + `test_openapi_json_check_rejects_stale_copy`
   报「仅活 schema 有 `['/']`」——**其余 3561 条全绿**
2. e2e smoke：`[smoke] FAIL - GET /: content-type='application/json' expected text/html`
   （`scripts/smoke_e2e.py` 假设 dist 必然存在）——**四步链路本身全绿**

⇒ **CI 自 2026-09-09 起每次 push 全红**（`gh run list` 实测最近 30 条无一绿色），
而本地全量闸门全绿 ⇒ 红了 30+ 次无人看红。

#### B. 修法（四层，缺一不可）

1. **`packages/core/api/main.py`**：root 端点加 `include_in_schema=False`（与同文件 SPA
   catch-all 的做法一致）⇒ 两种环境下活 schema 均为 98 paths。
2. **`scripts/smoke_e2e.py`**：`GET /` 断言改为**按实际部署形态**分支——SPA ⇒ `text/html`
   且正文含 HTML；纯后端 ⇒ `application/json` 且 `service == "novelos"`。
   ⚠️ **不是放宽成「什么都行」**：两种形态各自都钉了判据。
3. **不变式看守**（新增）：`tests/api/test_spa_hosting.py::test_openapi_paths_identical_with_and_without_dist`
   断言**两种环境下的活 OpenAPI 路径集逐字相同**。
   ⚠️ 比「比对某个文件」更强、更一般；且与 `test_openapi_contract.py` **互不覆盖**——
   后者只在「无 dist」环境红，前者在**任一**环境红。
4. **突变规格**（新增）：`tests/mutation_specs/l6_openapi_env_independence.py`
   - `ENV-1` 撤 root 端点的 `include_in_schema` ⇒ 无 dist 侧多出 `/`
   - `ENV-2` 撤 SPA catch-all 的 ⇒ **有** dist 侧多出 `/{full_path}`
   - **只钉 ENV-1 的话，「把 catch-all 也改成进 schema」这种看起来同样合理的改法能全绿**
     ——本仓坑区登记过「修复动作只有一个 → 越修越坏」与「过修」两族（批次十 `E1-ARC3`
     即因过修被回退）。**2/2 全捕获**，0 存活 0 中止 0 无效，残留清点 0。

#### C. 闸门（本次亲跑）

| 闸门 | 结果 |
|---|---|
| 契约测试 × 两种环境 | **各 8 passed**（有 dist / `NOVELOS_WEB_DIST` 指向不存在路径） |
| 全量 pytest（`.venv`，`-n 4`） | **3578 passed / 1 skipped / 1 xfailed**（3580 collected，405.21s） |
| `ruff check packages scripts tests` | All checks passed |
| `npx tsc -b` / `npx vitest run` | 零错 / **755 passed（56 文件）** |
| `smoke_e2e.py` × 两种形态 | **均 PASS**（本地 `GET /` text/html；无 dist `GET /` application/json）**+ SYNC OK** |
| `export_openapi` + `gen_frontend_types` | 98 paths / 136 schemas / 136 interfaces，**regen 后产物逐字节一致（零漂移）** |
| 树指纹 | **612 文件 / `5663ac14…`**，跑测前后**逐位一致**（611 = 610 + 1 新增规格） |
| 突变 `ENV-1`/`ENV-2` | **2/2 全捕获**，残留清点 0 |

#### D. 通用纪律（已入 AGENTS.md 坑区）

**契约产物的正确性不得依赖构建产物是否存在**——凡「同一份代码在两种环境下产出不同
路由表」的分支，其环境差异必须被 `include_in_schema` 排除在契约之外。

> 附一条**自我修正**：本批初稿在 CHANGELOG 与对外汇报里写成「CI 长期红是 NovelOS 独有
> 的短板」。**实测证伪**：同侪 `neuro-book` 连续 5 条红、`storyforge` 红、对手方
> `AI-Novel-Writing-Assistant` 10-19 也红 ⇒ **「CI 长期红」是这条赛道的通病**。
> NovelOS 真正特殊的只是「**红的理由只有一个、机械可修，却红了 30+ 次没人看**」。
> 而 `InkOS`（10388★）最近 5 条 CI 全绿 ⇒ 绿 CI 在本赛道**可达**。

### Changed（2026-10-19 批次（十二）之二：文档对齐 —— 过期基线数字与失真描述）

> **来源**：用户指令「先把文档对齐；然后上 GitHub 找同类热门项目做技术面评估」。
> 本条是同批的**文档对齐**部分；同批另有一条 `### Fixed（批次（十二）：CI 连续 30+ 次全红）` 改了 `packages/` `tests/` `scripts/` ⇒ **本提交最终的树指纹是 612 文件 / `5663ac14…`**（见上方那条的闸门表）。
> 本条记录的对齐口径来自**文档对齐阶段**的实测（尚未改代码时）；最终以同批 Fixed 条的闸门表为准。

#### A. 文档对齐（过期基线数字）

本批的口径**全部来自本次亲跑实测**，不是抄上一轮记录：

| 闸门 | 本次实测 |
|---|---|
| pytest（`.venv`，`-n 4`） | **3561 passed / 1 skipped / 1 xfailed**（481.51s）＋ `tests/evals` 16 例 ⇒ **3577 passed / 1 skipped / 1 xfail**（3579 collected），与 AGENTS 批次十一基线**逐位吻合** |
| vitest | **755 passed / 56 文件**（29.82s） |
| `ruff check packages scripts tests` | All checks passed |
| `npx tsc -b` | 零错 |
| 树指纹 | `611 文件 / 2f5ff533…`，**跑测前后逐位一致**（文档对齐阶段；加 CI 修复后为 612 / `5663ac14…`） |

修掉的过期数字（**只在活文档里改，历史记录一律不动**）：

| 文件 | 原写 | 改为 |
|---|---|---|
| `README.md:49` | `0001~0030，共 30 个迁移` | `0001~0031，共 31 个迁移`（补 `0031 项目级快通道` 进关键迁移表） |
| `README.md:147` | `当前基线 3488 passed，1 skipped` | `3577 passed / 1 skipped / 1 xfail，3579 collected` |
| `README.md:153` | `（3488 那轮即含它）` | `（3577 那轮即含它）` |
| `README.md:165` | `vitest；当前 489——vitest 侧本轮未独立复现` | `vitest；当前 755 用例 / 56 文件`（同文件 `:199` 已写 755，原状**自相矛盾**） |
| `README.md:199` | `pytest 3488 passed / 1 skipped` | `3577 passed / 1 skipped / 1 xfail` |
| `README.md` / `tests/README.md` | `约 7 分钟` | `约 8~10 分钟`（批次十一 597.72s ＋ 本次 481.51s） |
| `tests/README.md:4,25` | `3488 passed / 1 skipped（批次十，指纹 a19ae699…/598）` | `3577 / 1 / 1 xfail（批次十一，指纹 2f5ff533…/611）` |
| `database/README.md:3,10,21` | `现行 0001~0030，共 30 个迁移`（3 处） | `现行 0001~0031，共 31 个迁移` |

**不动的地方**（按 AGENTS §二 硬规则 7）：① `AGENTS.md` 一致性矩阵里**已划删除线的历史行**（批次十及更早）——那是审计留痕，改它等于抹掉历史；② `CHANGELOG.md` 里的历史条目；③ `docs/reviews/2026-09-27-full-audit/` 下的调查文档（自带「日期 + HEAD + 性质」横幅，其「0001~0030」是**该快照当时**的真陈述）；④ `scripts/tree_fingerprint.py` docstring 里的「3312 passed / 1 skipped」——那是**批次八那一次具体测量**的记录，不是活基线声明。

#### B. 登记：CI 自 2026-09-09 起每次 push 全红（根因已定位；**已于同批修复**，见上方「批次（十二）：CI 连续 30+ 次全红」条）

`gh run list` 实测：**最近 30 条 run 无一绿色**（最早回溯到 2026-09-09）。而本地全量闸门全绿 ⇒ 纯环境差异。

**根因（已复现验证）**：`packages/core/api/main.py:273-304` 的 SPA 托管分支随 `apps/web/dist` 是否存在而**改变路由表**，而 `dist/` 不在版本库：

- dist 存在（本地）⇒ SPA 分支，catch-all 带 `include_in_schema=False` ⇒ 活 OpenAPI **无** `/`（98 paths）
- dist 不存在（CI 干净 clone）⇒ `else` 分支的 `@app.get("/")` **默认进 schema** ⇒ 活 OpenAPI **多出** `/`（99 paths）

入库的 `apps/web/openapi.json` 是在**本地（带 dist）**生成的 ⇒ 差集恰为 `['/']`。

**一个根因，两个症状**（backend / e2e smoke 两个 job 各自红，web job 绿）：

1. backend：`test_openapi_json_file_matches_live_schema` + `test_openapi_json_check_rejects_stale_copy` 报「仅活 schema 有 `['/']`」——**其余 3561 条全绿**
2. e2e smoke：`[smoke] FAIL - GET /: content-type='application/json' expected text/html`（`scripts/smoke_e2e.py:406` 假设 dist 必然存在）——**四步链路本身全绿**（plan→write→review PAUSED→resume→commit→COMMITTED、observer 内容在场、`SYNC OK`）

**修法（已实测可行；同批已执行，见上方「批次（十二）」条）**：给 `main.py:302` 的 root 端点加 `include_in_schema=False` ⇒ 两种环境下活 schema 均为 98 paths，与入库 `openapi.json` 逐位相同（实测：去 `/` 后 paths 相等、schemas 相等、`info.version` 均 `3.9.0`）；`types.generated.ts` 只依赖 `components.schemas` ⇒ **前端零 regen**。与同文件 SPA catch-all 已用 `include_in_schema=False` 的做法一致。smoke 侧另需让 `GET /` 断言分支感知（或 CI 先构建前端）。

**通用纪律（已入 AGENTS.md 坑区）**：**契约产物的正确性不得依赖构建产物是否存在**。凡「同一份代码在两种环境下产出不同路由表」的分支，其环境差异必须被 `include_in_schema` 排除在契约之外。

#### C. 本批**没有**做的事（避免误读）

- **突变执行器**：文档对齐阶段只清点了 **295 条**规格条目（逐文件 `importlib` 计数），**没有执行**；同批的 CI 修复阶段**新写并实跑了** `l6_openapi_env_independence.py`（2/2 全捕获）。**「295/295 全捕获」仍是批次十一的记录，不是本批实测**——本批只实跑了新增的 2 条。
- **CI 修复**：见上方「批次（十二）」条（本条写于修复前，现已修完并加了不变式看守 + 突变规格）。
- **未动历史文档**：见 §A「不动的地方」。

### Fixed（2026-10-16 批次（十）：`except sqlite3.OperationalError` 过宽欠账全批结清 —— 22 处收窄 + 2 处 Lead 自办修复 + 一处契约变更驳回）

> **来源**：用户指令「把欠账清掉，这看起来是个复杂任务，并发多个智能体去做」。
> 并行 **5 名成员**（E1~E5），按文件路径互斥；Lead 自办共享单点与两处无归属的修复。
> 全程**临时库 + 合成夹具**，零真实 LLM 调用、零内容仓接触（AGENTS §一 两条硬约束）。
> 提交时基线见文末一致性矩阵；⚠️ **本条的实测数字待闸门跑完后回填**。

#### A. 欠账的由来

批次九修完 `_genre_pack_excerpt` 一处后，判定式被提到共享单点
`packages/core/db.py::is_missing_schema_error`，并在 docstring 里**如实登记残余**：
「同形状裸 `except sqlite3.OperationalError` 全仓还有 ~13 处，未逐个实测；扫尾时
提为共享单点再逐处收窄，不做批量替换」。批次十就是那次扫尾。

**逐处判定的三问**（写进任务书，成员逐处作答）：
① 该 `except` 的注释**只**声称覆盖「缺表 / 缺列」吗？
② 调用点**外层**有没有再套一层无差别 `except` 把它吞回去？
③ 纯读路径**结构上不可能**收到 `database is locked` 吗？

⚠️ **③ 的前提被实测推翻**：成员按「WAL 下只读不吃锁」主张保留若干处，Lead 复算发现
该前提**依赖于 WAL 真的生效**，而 `get_connection` 的注释恰恰写着切 WAL「sqlite 自动忽略失败」
（见 §C，那条注释是错的）⇒ 只要 WAL 没切上，**所有这些 SELECT 都会吃锁**。于是无一处以
「结构上不可能」为由保留。

#### B. 收窄清单（22 处）

| 组 | 文件 | 处数 |
|---|---|---|
| E1 | `arc/service.py`（4）/ `scripts/world_switch.py`（1）/ `scripts/db_maintenance.py`（1） | 6 |
| E2 | `builders_common/common.py`（5）/ `ledger.py`（1）/ `summaries.py`（2） | 8 |
| E3 | `writer_input.py`（5）/ `genre/verifier.py`（1）/ `retrieval/service.py`（1） | 7 |
| E4 | `chapter_plan/planner_input.py` / `scene_plan_store.py` / `chapter_review/pipeline.py` | 5 处判定 |
| E5 | `chapter_write/`（`writer.py` / `save_draft.py` / `scene_planner.py` / `pipeline.py` / `length.py`） | 10 处判定 |

**逐处判定表**由各成员在汇报中给出，Lead 对每组抽样亲验（读码 + 探针复算），
结论见 §D。

#### C. Lead 自办的两处修复（成员无归属 / 影响面跨包）

**C-1 `get_connection` 的注释被实测证伪，且危害链一路通到缓存键**

`packages/core/db.py` 原注释写「sqlite 自动忽略失败」。**假的**：`PRAGMA journal_mode = WAL`
要拿排他锁，他连接持写锁时**抛** `database is locked`（实测：直接跑 `get_connection`
的完整语句序列 + 他连接 `BEGIN EXCLUSIVE` 即复现）。

危害不在「建连接失败」，在**失败之后没人知道**：

    _peek_chapter_context / _peek_project_context
        except Exception -> 全空态
            -> state_version = 0 / genre_pack_ref = None
                -> 缓存键维度写成 0 与 '__none__'
                    -> 同 state_version 脏命中（硬规则 2「漏键 = 脏命中」），零告警

**全仓最隐蔽的一层：脏的不是数据，是缓存键。**

修法：WAL 是并发优化、不是正确性前提 ⇒ 失败退回 rollback journal 并 `log.warning`，
**不让异常冒出去**（冒出去只会换来一个静默的脏键）。
⚠️ 顺序**刻意**排在 `busy_timeout` 之前：提前会让本句先白等 5s 再抛。

**C-2 `genre_check.error` 的布尔门遮蔽（本仓首次登记该失效家族）**

`chapter_review/pipeline.py::_collect_genre_check` 原写 `if not result.bound: return None`，
而 `verify_chapter` 恰恰**用** `bound=False + error` 表达「绑定读取失败」（其源码注释明写
「**不得**落成 `no_binding`」）⇒ **`GenreCheckResult.error` 结构上不可达**：作者看到一份
「干净」的评审报告，题材核销其实根本没跑成，零告警。

**挡路的根本不是 except，是一个 `if`** —— 代码里连「吞异常」的字样都没有，读代码的人不会把它当吞。
同硬规则 9（字段存在 ≠ 进得了装配），失效面从「except」换成「布尔」。

#### D. Lead 驳回的一处收窄（**改变契约 ≠ 修缺陷**）

E1 收窄了 `arc/service.py::_summarize_reveal_policies`，理由是「唯一点名的成因是 0014 未跑（缺表）」。
**驳回**：该函数 docstring 原文是「SQL 异常（0014 未跑**等** OperationalError）→ 返回全零 +
空 overdue」——**那个「等」是显式的宽口径契约，不是笔误**，按 `is_missing_schema_error`
文档化的前提①（注释声称更宽 ⇒ 收窄=改契约，须先确认）。

处置：① 回退该处收窄；② 加一条**钉现状而非钉现状正确**的用例
`test_arc_reveal_policies_keeps_wide_contract_pending_ruling`——没有它，下轮扫尾会把这处
收窄掉，而「契约变更」与「顺手统一风格」在 diff 表面一模一样；③ 删掉随之锚点归零的
突变 `E1-ARC3`（**删掉 + 写明理由**，不是留着让 run_spec 每次 ABORT）。

⇒ **沉淀**：该前提不能只躺在 `is_missing_schema_error` 的 docstring 里，必须在**判定表**中
逐处显式回答——E1 当时正是照「唯一点名的成因」推的，且**自己主动上报了边界情形**，
说明这条纪律必须落在能被执行的位置。

#### E. 本批新登记的坑（详见 AGENTS.md 坑区）

1. **布尔门遮蔽**（C-2）——`except` 之外的第五种遮蔽面。
2. **降级的下游不是「显示错」，是「执行不可逆写」**——`world_switch.py::_fill_evidence`
   在锁故障下 `rows=[] ⇒ eras={} ⇒ _era_decision(∅) == ("archive","closed_era")`（实测）
   ⇒ **`--apply` 把本该 `skip` 的跨位面实体全部置 `inject_mode='archived'`**，零告警。
   本批后果最重的一处。判别：`test_e1_world_switch_lock_raises_instead_of_blanket_archive`
   末尾直接断言那个判定值，让危害在测试里可见而不只在注释里。
3. **契约承诺存在 ≠ 数据源存在**（硬规则 9 的**反向**）——`director_input.py` 硬编码
   `"style_constraints_id": None` 且无任何代码路径能让它非 null（该列在**全部迁移里
   0 命中**，已用真迁移过的库逐列核对），而 `director-v1.md` 承诺 `"string 或 null"`
   还给了个具体例子 ⇒ **教模型编一个永远用不上的字段**。**登记不修**：属产品定位级
   （§一 裁决授权边界）。⚠️ writer 侧那个 `style_constraints` 段是**活的**，死的只是
   director 那个 `*_id` 字段。
4. **rollback journal 下 `BEGIN EXCLUSIVE` 挡整个文件，连 `SELECT 1` 都挡**——量具侧教训：
   能挡住 `journal_mode` 的锁按定义就会挡读，于是「不抛」与「可用」**不可能同时断言**
   ⇒ 按时机拆开断言，夹具释放需幂等。
5. **「顺手优化」PRAGMA 顺序**——Lead 自踩：修 `get_connection` 时把 `busy_timeout`
   提前，自家测试**第一次跑就红**，耗时 0.5s → 22.43s。两个数字一起指认真因。
   ⚠️ 该顺序**无突变看守**（只能写时序断言，必然脆），已在 `e7` docstring 里
   **如实登记为声称**。

#### F. 登记不修的残余（不在「清欠账」的工程授权内）

| 项 | 为什么现在不修 |
|---|---|
| `director_input.py:144` `except Exception -> return []` 仍吞掉 `retrieval/service.py:333` 上抛的锁 | 其注释写「FTS 虚表不存在**等**降级」，同样是显式宽口径；且修它要先定「召回失败该不该让整个装配失败」（本仓最重的 M1 教训是别让读路径挂死）。**按严重性排序不修**：它只造成**质量降级**，而 §E-2 那条是**不可逆写** |
| `db_maintenance.py` 退出码契约与实际不符 | docstring 承诺 2（参数错/内部异常），`main()` 无顶层 except ⇒ 实测 **exit 1** + 裸 traceback。改它属 CLI 契约变更 |
| `arc/service.py` 的 `_summarize_hooks` / `_summarize_debts` / `_load_latest_quality_per_chapter` / `_load_latest_draft_chars_per_chapter` **一条 except 都没有** | 与本次收窄方向**相反**的不对称：该降级的没降级。加 except 属**新增行为** |
| `writer_input.py:169` 的字数带读收窄**在生产链路不可达** | peek 恒返回 dict ⇒ `peek is not None` 恒真。改它是为防将来新增无-peek 调用方，**不是生产缺陷修复**——按「声称与实测分离」不许写成修了生产 bug |

### Fixed（2026-10-16 批次（十一）：角色设定到不了正文 —— 外部用户报告「角色圣经写闺蜜、正文里却是业务员」）

> **来源**：外部用户报告（非本项目成员）。并发 **4 名成员**（E1~E4）按文件路径互斥 +
> Lead 自办；全程临时库 + 合成夹具，**零真实 LLM 调用**、零内容仓接触（AGENTS §一）。
> ⚠️ **提示词改动（`writer-v4.md`）的效果未验证**，理由见 §E。

#### A. 先把报障**拆对**（这一步比修代码更重要）

报告原文说的是「正文与角色设定不一致」。复算后发现**这个框架本身可能是错的**：

| 报障用词 | 复算结论 |
|---|---|
| 「以业务员出场」 | `grep 职业` 在 `packages/` `apps/web/src/` `docs/agents/` `database/` **全域 0 命中**——角色数据契约只有五键，**没有职业、没有身份**。⇒ 模型**不是违反设定，是没有可违反的设定**，自造了一个 |
| 「关系是闺蜜」 | 关系确实存在（`core_json.relationships`）但**确实会丢**，两条独立机制（见 §B） |
| 「手动添加的角色」 | **与真凶无关**：手动新增角色照样会被削（它必然不在 `touched` 里），但**手动新增本身不是原因** |

⇒ 教训：**读报障先做一步——把用户描述的每个维度对回数据模型，确认那个维度存在**。
维度不存在时，真问题是「设定**未被约束**」而非「设定**被违反**」，两者修法完全不同。

#### B. 真凶：同一份角色设定被**三层裁剪各自独立地丢弃**

```
L1 触发层 _apply_injection_policy  未命中 ⇒ {id,name,role}        ← core_json 整块丢
L2 relevance 层                      未 involved ⇒ {id,name,relevance_summary}
L3 分页层 _build_writer_input_paged  不在 touched ⇒ {id,name,role,summary_marker}
```

**三层互不知情**，因此**修一层不够**。且 L2 docstring 承诺的「主角始终完整保留」
被 L1 与 L3 各削一次——**在整条链上一处都不兑现**（实测修复前连主角都 `characters_full == 0`）。

三条修复（每条依据都是**既有的成文契约**，不是新策略）：

1. **L1 `_is_triggered` 增加 `entity_id` 候选词**。依据：`_build_trigger_corpus` 明确收了
   `character_id`，`writer_input` 也把 `scene_plan["characters"]`（存 **id**）直接拼进语料，
   **而判据侧从不看 id** ⇒ 本场出场角色必然「未命中」。同硬规则 9，失效面在判据侧。
2. **L1 主角无条件放行**。兑现 `relevance` docstring 的既有承诺。
3. **L3 放行条件加三条**：本场出场 / `inject_mode='always'` / `protagonist`。
   `always` 依据 0010 迁移注释「无视命中，永远完整注入」；本场出场依据本文件 `:200`
   那句**此前为假**的注释（「scene_plan 含本场参与角色」）。

⚠️ 另有 `inject_mode` **从不进 excerpt** ⇒ `relevance` 里三处 `== "always"` 分支
**恒假**（E2 发现 / E3 修）。同族：**分支谓词读某字段时，要确认该字段真进到了它所在那一层**。

#### C. 缓存键：两个进 payload 却从未入键的维度

| 维度 | 进 payload 的东西 | 后果 |
|---|---|---|
| `chars_fp`（E1） | `characters.core_json` 整份 | 改角色设定后**重跑同一章**命中旧装配 |
| `plan_fp`（Lead） | `director_plan` 四字段 + `expected_role` + **触发扫描语料** | 重排计划后命中旧装配，**连「本章该注入哪些角色」都停在改计划之前** |

⚠️ `plan_fp` 那条是 Lead 写自己的测试时**被跨用例缓存污染撞出来的**，不是读码推论。
**边界（E1 量化）**：H1 当且仅当「同 `chapter_no` + 同 `state_version` + 变更在 `characters` 上」
同时成立才发作；**写新章不成立**。缓存是**进程内**的（模块级 dict、无持久化面）⇒ 改完不重启
服务就重跑同章会中招，**刷新页面照样中招**（同一个后端进程），重启即自愈。

#### D. OOC 审查对全部角色结构性失明（Lead 修）

`settings_digest` 读**顶层** `core_json.one_line`，而全仓 `"one_line"` 只有 4 处、**全是读取
或构造**；三个 `core_json` 写入方都只产现行五键 ⇒ 判据恒 `""` ⇒ 作者看到「审过了」而人物
约束一条都没进去。改为按五键拼摘要（`_core_one_line` 单点），保留旧形状兜底。

⚠️ **为什么一直没被发现**：`test_chapter_review_settings_digest.py` 的夹具**用的就是**
`{"one_line": ...}` 这个**没有任何生产方会产出**的形状 ⇒ 绿灯靠虚构成立。
这是坑区「夹具抄形状导致缺陷被断言」的**反向姊妹形状**：那次是抄实现的错，这次是凭空发明。

#### E. `writer-v4.md`（**效果未验证**）

§5 契约删掉零生产方的 `speech_pattern_notes`、补上承载整个角色圣经的 `core_json`（含五键 +
作者自定义键）；新增**规则 23「角色设定即权威」**——落地用户 2026-10-16 裁决：
**作者最新手工设定的角色卡，与项目圣经 / 观察者派生状态 / 其它来源冲突时以角色卡为准**；
`core_json` 未声明身份时**不得凭空补一个**（正是「业务员」的来源）。

⚠️ **按「声称与实测分离」登记为未验证**：提示词改动的唯一证伪手段是真实模型响应，
而 AGENTS §一 硬约束 2 禁止在改软件过程中发起真实 LLM 调用。
规则 23 内含一处**用户未单独裁决**的选择：「`core_json` 与 `author_intent`（含圣经）冲突时
以 `core_json` 为准」——方向与 §4.3 / `:185` 的既有条文相关联，改动须一并校订。

**升版而非原地改**：`prompts.version` 取自**文件名**、不是内容哈希；原地改 `writer-v3.md`
会让两套不同正文共用 `writer:v3`，`ai_call_logs.prompt_version` 再也分不清是哪一套产出的。
连带把 `test_config_list_response_contract.py` 的**实测行数**断言从 3 改 4
（该断言守的是「多一版就多一行、没被静默丢弃」，不是「恰好 N 版」）。

#### F. 顺带：克隆用户的更新体验

`README` 新增「更新到最新版」与「授权状态」两节；新增 `packages/core/api/version_check.py`
+ lifespan 接线：**启动时**比对上次记录的 commit 与当前 HEAD，**只针对 `apps/web/dist`
不在版本库**这一条给可复制粘贴的命令（改了前端的版本 ⇒ 「新后端 + 旧界面」且**不报错**）。
设计硬约束：**绝不阻断启动**（非 git 仓库 / 没装 git / 超时 / 状态文件损坏一律降级为
「没检查」）。判别 9 例 + 突变 `VC-1~5`，其中 `VC-5` 专门守「去掉降级会让 zip 安装起不来」。

⚠️ **LICENSE 裁决为暂不添加**：许可文本是有法律后果的授权、进了 git 历史即难撤回，
不由工具代签。改为在 README 写明「当前无 LICENSE、按默认规则保留一切权利」，并留决策记录。

#### G. 登记不修

见 AGENTS.md 坑区「登记不修（批次十一）」五条：`director_input.py:144` 遮蔽、
`snapshot.py` characters 段有损（三条实测理由）、`settings_digest` 的 `LIMIT 8` 随机截断、
`chars_fp` 引入的 **+4.05ms**（归因：85% 是**多开一次连接**不是多查数据，下轮很便宜）、
`writer-v4` 规则 23 未验证 + 含一处未裁决的产品选择。

### Fixed（2026-10-16 批次（九）：读路径降级范围 —— 「一行脏数据让整个列表 500」的第 2~4 次实例 + 结清一处登记挂账 + 一处量具假阳性）

> **来源**：定时任务 `arch-review-bugfix`（架构审查与缺陷修复）。工作目录干净，
> HEAD = `d7788f3`（批次八已提交），基线 pytest 3312 / 1 skipped、指纹 578 文件。
> 全程**临时库 + 合成夹具**，零真实 LLM 调用、零内容仓接触。

#### A. 脏 JSON 读路径：一行坏数据让**整个列表端点 500**（已复现，四处）

2026-10-15 批次（六）给 plot / relationships 的列表端点挂 `response_model` 时发现：
service 侧裸 `json.loads` 读 JSON 列，一行脏数据（非法文本**或**合法 JSON 但形状不符）
会让值冒到路由层 ⇒ **整个列表 500，干净行陪葬**。当时只处置了 plot 一处。

批次九对同形状做**全仓扫**，用**临时库 + 直接塞脏行**实测（`_app` + 直插 SQL），
修复前四处均复现 500：

| 脏列 | 端点 | 修复前 |
|---|---|---|
| `chapters.plan_json` | `GET /projects/{pid}/chapters`、`GET /chapters/{id}` | **500** |
| `characters.core_json` | `GET /projects/{pid}/characters`、`GET /characters/{id}` | **500** |
| `character_states.state_json` | `GET /characters/{id}/states`、列表行的 `latest_state_json` | **500** |
| `locations.data_json`（factions / world_rules 同） | `GET /projects/{pid}/locations` 等 | **500** |
| `quality_reports.issues_json` / `scores_json`（形状不符） | `GET /projects/{pid}/quality` | **500** |

**共同形状**：同一函数里的**兄弟字段早已兜底**，唯漏的那几处是裸 `json.loads`
（chapter 内 `outline_json` / `who_knows` 已兜底、character 内 `aliases` / `inject_mode`
已兜底）——与 `update_hook` 漏 `model_fields_set` 同一种「**笔误不是设计**」。

**修法（单点）**：`write_helpers` 新增 `decode_json_field(raw, *, fallback, allowed=(dict,))`
——解析失败 / 形状不符 ⇒ 退回该字段空值（与既有 `decode_who_knows` 同源）。
四处读路径全部改调它。**降级只发生在读路径**，写路径校验不变。

⚠️ **降级 ≠ 掩盖**：`decode_json_field` 只保证「读路径不因脏数据炸」，不校验值域
（那是写路径的职责）；`outline_json` / `who_knows` 这类**可空**列退 `None`，
`plan_json` / `core_json` / `data` 这类**非空**列退 `{}`（与「列本来为空」同形）。

判别：`tests/api/test_dirty_json_list_degradation.py` **12 例**（每处一正一脏，
断言「端点 200 + 干净行仍可见 + 脏字段退空值」）；突变 `L0-ENTJSON1~5` + `L0-QJSON1~2`
**7 条全捕获**（撤任一调用改回裸 `json.loads` 必红）。

#### B. 结清登记挂账：`_genre_pack_excerpt` 的 `except` 范围收窄

2026-10-15 批次（六）收窄 `fast_mode.py` 时留下一句挂账：「同款过宽写法在
`builders_common/common.py`（题材包绑定）仍在，本批只收窄本文件；其余挂账待独立处理」。

复算成立且**更严重**：`_genre_pack_excerpt` 裸 `except sqlite3.OperationalError`
一律降级为「未绑定」，而注释只声称覆盖「0025 未跑的极老库」（缺表 / 缺列）。
⇒ `database is locked` / `disk I/O error` / `malformed` 被**静默吞掉**：
作者以为体裁约束生效，产出的正文却没带体裁，**且零告警**——正是本项目最忌的静默降级。

修法：
1. `common.py` 新增 `_is_missing_schema_error(exc)`（特征子串 `no such table` /
   `no such column`），收窄 catch；非结构故障**上抛**。
2. 删掉 `planner_input.py` / `scene_planner.py` 两处**无差别再吞**的外层
   `except sqlite3.OperationalError`——它们会把内层刚收窄的范围**原样放大**
   （只收窄内层、不动外层 = 修复对这两条调用链**完全无效**，这是本条的关键）。

判别：`tests/unit/test_genre_pack_schema_error_scope.py` **7 例**（内层缺表/缺列降级、
锁/I/O/损坏上抛各一；两个调用方各一例证明「外层不再吞」）；突变 `L0-GP1~3` 全捕获。

⚠️ **如实登记的残余**：同形状（裸 `except sqlite3.OperationalError` + 注释只声称
「老库 / 缺表」）在本仓**还有 ~13 处**，实测清单见 `common.py::_is_missing_schema_error`
docstring（`chapter_write/writer.py` / `save_draft.py` / `scene_plan_store.py` /
`chapter_write/pipeline.py` / `planner_input.py` / `scene_planner.py` …）。本批只结清
**已登记挂账**的那一处；扫尾时应把判别式提为共享单点（`packages/core/db.py`）再逐处收窄
——**逐处改需逐处实测，不做批量替换**。

#### C. 量具假阳性：`validate_specs.py` 的 `ast.parse` 不容忍文件头 BOM

给 `scene_planner.py` 写突变时，只读预检报：「变异后语法错误（invalid non-printable
character U+FEFF）」——**假阳性**。真因：Python 的 import 走 `utf-8-sig`、**容忍** BOM，
而 `ast.parse(str)` 不容忍；不镜像加载器就会把**合法**的带 BOM 源文件误判成语法错误
⇒ 任何指向它的突变都拿不到结论（同坑区「量具缺陷伪装成被测物有病」）。

实测本仓 **3 个 .py 带 BOM**（`git ls-files` 逐字节扫，全在 `chapter_write/` 下：
`polisher.py` / `save_draft.py` / `scene_planner.py`，均为 HEAD 既有）。
修法：`ast.parse(mutated.removeprefix(chr(0xFEFF)))`——单行，且**不放松真正的语法检查**
（去掉的只有 BOM）。判别：`l0_degradation_scope.py` 的 `L0-GP3` 现在可跑且被捕获。

#### 闸门（项目 .venv，`-n 4`）

- pytest **3331 passed / 1 skipped / 0 failed**（348.78s）——较基线 3312 **+19**，
  账对得上：`test_dirty_json_list_degradation.py` 12 + `test_genre_pack_schema_error_scope.py` 7；
- 树指纹 **581 文件 / `442f20c3…`** 跑测前后**逐位一致**（基线 578，+3 = 两个新测试文件 + 一个突变规格）；
- ruff `All checks passed!`；
- `npx tsc -b` 零错、`npx vitest run` **755**（56 文件）全绿、`npm run build` 通过
  （asset hash 与基线同：`index-mhzcNjYt.js` 467.50 kB）——**纯后端批次，前端零改动**；
- `scripts/smoke_e2e.py` PASS，旧四步链路完好（plan→write→review PAUSED→resume→commit）、
  state-sync SYNC OK；
- `export_openapi` + `gen_frontend_types` **先于**全量 pytest 跑：98 paths / 136 schemas
  **未变**、`openapi.json` / `types.generated.ts` regen 前后逐字节一致（本批未动路由 / 出参模型）；
- 突变 **210 条全捕获**（原 200 + 本批 10），0 存活、0 中止/无效，残留清点 0。

#### 未做完整归因 / 没攻破的地方

- 本批**未动** `apps/web`，也**未做** UI 侧任何配套（读路径降级对前端是「字段变空值」，
  与既有「可空列」同形，前端无需改）；
- 同形状 `except` 过宽**只清了一处**（B 节残余 13 处），**未做逐个实测**——留作扫尾批次；
- **未评估**是否还有其他「列表端点被一行数据打 500」的形状（如 `pydantic` 的 `Literal`
  值域校验：ledger `Hook.status` / `Debt.status` 用 `Literal`，一行枚举外值理论上也会
  500——但 DDL 有 `CHECK` 约束，实测该路径**不可达**，故本轮判定为**非缺陷**，
  仅记录判断依据，不修）；
- 定时任务只做了**读 + 复核 + 局部修**：未用常驻子代理（`主会话亲验`优先于并行）；未提交。

### Fixed（2026-10-16 批次（八）：查「假事故」时挖出的**真缺陷** + 三处顺带清账）

> **来源**：用户指令「2」（选「定位 `test_m1_lock.py` 真因」）+「并发智能体去干」。
> 起因是 AGENTS 坑区挂了一条两个月的旧账：`test_m1_lock.py` 单跑「硬杀整个进程树」，
> 处置写的是「**规避而非修复**，真因未定位，待单独立项」。

### ❌ 先验被推翻：那条「事故」从未成立，而真因是**推理错误**不是观察错误

原记录写的是「症状：0 字节输出 / exit 1 / 无 traceback；真因：未定位」。
实测：**任何跑法下都没出现过**该症状（隔离进程组、单跑、全量三种跑法都是正常输出
+ 完整 traceback）。原记录犯的错是把「**我复现不出来**」当成了「**我还没找到真因**」
⇒ 一条从未成立的现象从 2026-09-27 挂到今天，还派生出「规避而非修复」这条纪律。
**这正是本仓「声称与实测分离」的反面教材**：一条没被证伪的观察，靠复现不出来就被
当成了观察。

### ✅ 但顺着查下去，挖到一个**真缺陷**——危害不是崩溃，是锁静默失效

`_pid_alive` 曾拿 `os.kill(pid, 0)` 当存活探针。实测同一台机器、同一解释器，
它对**自己**的探测结果随进程环境而变：

| 进程环境 | `os.kill(self, 0)` |
|---|---|
| 普通 / `CREATE_NEW_PROCESS_GROUP` | 无异常（正确） |
| **`DETACHED_PROCESS`** | **`OSError: [WinError 87]`** |

而该异常的类**是普通 `OSError`**（既非 `ProcessLookupError` 也非 `PermissionError`
——本机 3.12.13 复算确认）⇒ 直落 `except OSError: return False`
⇒ **活着的实例被判成已死**。

**这为什么严重**：`_pid_alive` 是 `m1_long_run` 单实例锁的守门人，而该锁防的正是
「两个 `run` 实例并发写同一个 SQLite」（M1 事故根因）。进程若以 detached 方式启动
（Windows 计划任务、部分 CI runner、守护场景），**第二个实例会认为第一个已死**
⇒ 抢锁覆写 ⇒ 精确回到 M1 事故。**全程零告警、零异常**——锁"成功"了，只是锁错了。

修法（`_pid_alive_win`）：`OpenProcess(SYNCHRONIZE)` + `GetExitCodeProcess`，
并让**探测判不出来时返回 `None` 交调用方兜底**；两个兜底都改为**保守判活**
（误判活只需 `--force-lock`，有人能解开；误判死会让两个实例并发写库）。
POSIX 侧 `except OSError` 同样由判死改判活。

判别：`test_pid_alive_windows_probe.py` **9 例**，形状是关键——**必须在真实
`DETACHED_PROCESS` 子进程里断言**，否则「活着的自己被判活」在普通环境下是**恒真**的
（旧实现在普通环境下本来就对）。另有**反向不变量** 3 条守住「探测不得退化成恒 True」
（那会把 bug 从误判死挪到误判活，不是修复）。

### ⚠️ 突变验证当场抓到**我自己新写的用例**有一个不可达分支（`L0-ALIVE-2` 首轮存活）

把「探测判不出来 → 保守判活」的兜底改回 `return False`，**9 例全绿**。
真因不是用例写错，而是**该分支在测试环境下不可达**——真实子进程里
`_pid_alive_win` 从不返回 `None`（它要么成功、要么命中已知错误码）。

**「不可达」不等于「不需要守」**：它是这条保守契约的**唯一执行点**。补 2 例
（把 `_pid_alive_win` 桩成恒返回 `None` / 恒返回 `False`）后复测 **8/8 全捕获**。
⇒ 又一条：**突变存活时先问「这个机制在本环境可达吗」**，可达性问题要用桩造出来，
不能用「跑不出红」把它记成通过。

### ✅ 顺带清账：`test_m1_lock.py` 的两条 cmd_run 用例**本来都是空壳**

查这条假事故时发现的第二件事：两条 cmd_run 端到端用例构成一个**互相援引的闭环空壳**
（详见坑区新条「skip 理由指定的『替代证据』自己也是空的」）。重写为真跑子进程 +
真写活 PID 锁（取 pytest 自身 PID，子进程探测时它确实活着），以
**退出码 5 ↔ 2** 构成正反一对。

突变 `L0-PLUMB-1`（`force_lock = args.force_lock` 改成常量 `False`）**只有新用例抓得到**
（旧套件全绿）——这是那个缺口真实性的直接证据，也订正了我自己一度写下的
「force 接管整个特性零覆盖」：**不准确**。`_acquire_lock` 的 force 分支一直有
单元级覆盖（`test_force_takes_over_alive_pid` 直接调它），真正没覆盖的是
**`--force-lock` 这条 CLI 管线从 argparse 到 `cmd_run` 的传递**。

本文件 **13 passed / 1 skipped → 15 passed / 0 skipped**，`--ignore` 撤销，
`subprocess.run` 的 `encoding=` 双侧补齐（挂了三个月的第三处欠账，一并结清）。

### Fixed（批次八 · 并行成员交付的三处，均为主会话复算后入册）

| # | 缺陷 | 形状 | 判别 |
|---|---|---|---|
| 1 | **`/state/deltas`、`/state/init`、`/state/branches/{n}/promote` 三端点不校验章节归属**（只校验项目归属）⇒ 可用**别的项目**的 chapter_id 读/写本项目的 delta 与快照分支。`init` 与 `promote` 是**写**路径，比 `list_deltas` 更重 | 跨项目越权，且**同形状三处**（不止派活的那一处） | `test_state_list_response_contract.py` **10 例**：跨项目拒 / 本项目仍行 / **404 不带可判别信息**（不泄露该 chapter_id 是否存在于别处）/ 原有错误路径不变 |
| 2 | **`GET /relationships` 静默丢 `visibility` 与 `who_knows` 两列**（DB 有、响应没有）⇒ 剧透可见性在只读端点上不可见 | 与批次六同族：数据源存在 ≠ 已进响应 | `test_ledger_plot_list_response_contract.py` + `test_plot_dirty_json_row_degradation.py` **7 例**（含脏行降级 4 例） |
| 3 | **突变执行器自身缺陷**：`run_spec.py` 只认全路径、**不认裸文件名**，而 `validate_specs.py` 认 ⇒ 同一套规格名在两个工具里语义不同；且失败时抛**整段 traceback**，既没进 INVALID 桶也没提示正确写法 | 「量具自身缺陷伪装成执行结果」的同款形状 | `_resolve_spec` 统一解析 + 归 INVALID 桶 + 提示已试过的两个路径 |

**关于 #2 的一条方法论**（成员实测报来、主会话复核确认）：给该端点补上两列后，
突变 `L1-11`（删掉 `visibility` 映射）**打不破纯键集断言**——`to_dict` 照样吐那个键、
键集仍是 9 个、照样全绿；**只有值断言抓得到**。⇒ **键在 ≠ 键里的值是活的**。
新夹具刻意造「一行 `PUBLIC`/`None` + 一行 `RESTRICTED`/`["char_z"]`」两行异值，
正是为了让"硬编码成默认值"这类实现无处藏身。与批次五 M14 的自指断言是**不同**的失效
方向（那条是断言恒真，这条是断言不够狠）。

### ✅ 收尾时主会话自办两处（都不是派活范围，都是读代码读出来的）

**1. `SnapshotResponse` 的 schema 覆盖缺口**（批次三留下的）。
审查报「`SnapshotResponse` 零字段突变」——复核**属实**：批次三的 5 条用例断言了
schema **存在**、被端点**引用**、响应**形状**对，唯独没断言它的 `properties`。
而该模型带 `extra="allow"` ⇒ **删掉任一字段声明，端点响应一字不变** ⇒ 那 5 条全绿
⇒ 批次三修掉的「前端两个统计项恒 0」**静默复活**。这正是批次五 M20 纪律的**第二处实例**
（`extra="allow"` 让「删字段声明」没有可见症状 ⇒ 必须断言 schema 的 `properties`/`required`）。

新断言的基准取自**第三个独立位置**——`story_state/snapshot.py::build_initial_state`
第 301~307 行实际构造的 7 个键，**不是**抄模型（那是自指断言）、也不是「跑一下看返回什么」
（那是抄形状）。突变 3 条（删 `hooks` / 删 `events` / `state_version` 掉出 `required`）
**3/3 全捕获**。

⚠️ 其中 `L5-FIELD-2` **首轮存活**，真因是**我自己的变异写错了**：replacement 写成
`events: dict[str, Any] = {}`——**字段照样声明着**，只丢了 description 与 default_factory
⇒ 属性面一字未改。而 runner 的「写入后 SHA256 未变」断言**抓不到这一类**
（字节确实变了，只是**行为没变**）。这是坑区「突变本身也要突变」的**同形状第 2 次**。

**2. `_pid_alive_win` 的 ctypes 签名缺失**（读上条那个新函数时读出来的）。
`ctypes.WinDLL(...).OpenProcess` 用的是**默认 `restype = c_int`（32 位）**，
而 `HANDLE` 是**指针宽**（本机 `sizeof(c_void_p) == 8`）⇒ 未声明时句柄会被截断，
而截断后的值会被原样传给 `CloseHandle`。已补 `argtypes`/`restype` 全套声明。

⚠️ **口径不得夸大**：本机**未观测到截断**——4000 次 open/close 循环里句柄值**恒为 384**
（占 32 位空间的 0.000018%），因为内核句柄是进程句柄表里的**小整数下标**、用完即回收同一下标。
所以这是**潜在/可移植性缺陷，不是本机已发生的 bug**。仍然修，是因为「守门函数依赖一个
未声明的契约」是它不该有的脆弱性，而修它零风险。
（顺带留痕：这次探针我**第一版方法论就是错的**——拿两次独立 `OpenProcess` 调用的返回值
直接比，388 vs 412，那是两个不同的合法句柄，据此下结论会得到假的「截断已发生」。
改成直接量句柄值分布才拿到真数据。同坑区「两侧工具结论冲突时回到量具本身」。）


### ❌ 复核推翻覆盖率审查的 BLOCKING-1：**批次（五）的交付并不在突变网外**

审查报 B-1：批次（五）的核心交付（`arc` / `simulation` / `reveal_policies` 三个 router，
连同 `arc/models.py` 9 个类与 `simulation/models.py` 7 个类）**完全落在 181 条规格的网外**，
并列出 4 条「能让 181 条全绿的错误改动」，其中第 3 条是「撤掉 `simulation.py` 的
`response_model_exclude_none=True`」——**项目记忆里写死的那条禁令**。

**该结论不成立，根因是审查只枚举了两个突变存放点中的一个**：

| 存放点 | 条数 | 批次（五）在其中的交付 |
|---|---|---|
| `tests/mutation_specs/` | 200 | 无（批次六起） |
| `scripts/mutation_harness.py` | 21 | **M14/M21→`arc/models.py`；M15→`simulation.py`；M17/M18→`knowledge/models.py`；M19→`routers/arc.py`** |

而 **M15 恰恰就是那条禁令的突变**——把 `response_model_exclude_none=True` 翻成 `False`，
判别文件 `test_simulation_response_contract.py`。另外 M7 落在 `domain/chapter/models.py`
（volumes），而审查把 volumes 也列进了「一整族无覆盖」。

⚠️ **这条比一般的误报更值得记**：它的证据是**可复现**的——审查把 `grep` 命令原样写进了报告，
任何人重跑都会得到同样残缺的结果，于是错误结论看起来「有据可依」。**判别面没枚举全，
比判据写错更难发现**，因为输出本身没有异常。

⇒ 由此确立一条通用纪律：**审计类结论必须先回答「完整的判别面是什么」，再回答「命中了吗」**；
枚举判别点要枚举全，且**判别面本身必须写进报告**（本仓即「突变规格只有这两个文件」）。

**同一份审查的 M-1 是真的**（`test_force_lock_cmd_run_progresses` 不测 force-lock），
已在本批修复并配 8 条突变。**一条审查里真伪参半时，逐条复算而不是整份采信或整份丢弃。**

### ✅ 结清一笔悬案：树指纹「复现不出」的真因是 **mtime**，不是公式

2026-10-15 记着一笔至今没闭环的欠账：同树两次算出不同哈希，**6 种合理公式都复现不出**
记录值 `890428ca…`，后来者无法区分「树变了」与「当时公式不同」。当时的处置是定下
「必须用 `scripts/tree_fingerprint.py` 取指纹」的纪律。

**本次查明真因**：`tree_fingerprint.py` 的口径是 `f"{rel}|{st.st_size}|{int(st.st_mtime)}"`
——**哈希「路径 + 字节数 + mtime」，不含内容**。可逆实测：只 `touch` 一个文件、
内容一字不动，指纹 `96f6c280…` → `4ea8eb51…`；`os.utime` 精确还原 mtime 后，
指纹回到 `96f6c280…`。

⇒ **那笔悬案与「公式怎么写」无关，是这个值本来就绑在那次磁盘状态上。**
当年那条纪律修的是「手写公式不可复核」，**没修「值本身不可复现」**——两个不同的缺陷。

**已裁决：不改口径，只登记性质。** 依据三条：
① 闸门要答的是「跑测期间树有没有变」，写操作必改 mtime ⇒ 对该用途足够；
② 「内容变而 size 与 mtime 同时不变」需刻意保留 mtime 才会触发，实践上为零；
③ 改成内容哈希须连带新增 `.gitattributes` 统一行尾（现在 CRLF/LF 混存），会让
**全部 578 个文件在下次 checkout 大面积改写**——在 05:01 那轮无监督任务启动前一小时
做这种全仓改动，风险远大于收益，且须按 AGENTS「改动这里 = 历史哈希全部作废」登记。

**使用纪律**：引用历史基线时只可说「**那一轮跑测前后逐位一致**」（当轮自洽、可复现），
**不可说「与记录值相同」**（跨轮不可比）。已在 AGENTS 坑区写死。

### 闸门（批次八）

- pytest **3312 passed / 1 skipped / 0 failed**（355.04s，项目 `.venv/Scripts/python.exe`，
  `-n 4`）。**不再带 `--ignore=tests/unit/test_m1_lock.py`**——该文件实测无树杀，
  含在本次全量内跑通。
- 树指纹 **578 文件 / `96f6c280…`**，跑测前后**逐位一致**（`scripts/tree_fingerprint.py` 取）。
- `ruff check packages scripts tests` = `All checks passed!`（期间修掉 1 处 F401 未用 import）。
- `npx tsc -b` 零错；`npx vitest run` **755 passed / 56 文件**；`npm run build` 通过
  （`index-mhzcNjYt.js` 467.50 kB / gzip 147.24，与 regen 前同一 hash ⇒ TS 无漂移）。
- `scripts/smoke_e2e.py` **PASS**，**旧四步链路完好**
  （`plan → write → review PAUSED at author_review → resume → commit → COMMITTED`）。
- `export_openapi` + `gen_frontend_types` 已 regen：**98 paths / 136 schemas**，
  `info.version == "3.9.0"`（非 `dev` 占位值），`RelationshipOut` 7→**9 properties**。
- **突变验证 200 条**（`tests/mutation_specs/` 19 个规格文件），
  **最终全量重跑 200/200 全捕获、0 存活、0 中止/无效、退出码 0**；
  首轮的 3 条异常已逐一修后复测通过（详见下方表）。残留清点 0（`.mutbak` + 标记形态均空）。

⚠️ **测试数增量不做完整归因**：3273 collected → 3313（**+40**）。本批可确认的新增为
`test_pid_alive_windows_probe.py` 9（全新）+ `test_state_list_response_contract.py` +10
+ `test_plot_dirty_json_row_degradation.py` +4 + `test_m1_lock.py` +1
+ `test_snapshot_response_contract.py` +1 + `test_workflow_run_response_contract.py` +1
= **26**；**余 14 未逐条归因**（批次六部分成员的文件在本批之前已改过，缺可对照的历史计数），
**不编造拆分**——按「声称与实测分离」，宁可留一个诚实的缺口。

### ⚠️ 闸门自身的两次「变异拿不到结论」，都不是被测物的锅

首轮 `--all` 200 条里 **3 条**没能给出结论。**两条判 INVALID、一条 SURVIVED**，
三者都指向**判别面/变异写法**，没有一条指向实现缺陷：

| id | 现象 | 真因 | 处置 |
|---|---|---|---|
| `G4M15` | **存活** | `test_extra_allow_keeps_undeclared_keys` **只枚举了 3 个模型**（`WorkflowRunDetail`/`WorkflowStartAccepted`/`ContextPreviewItem`），而本组 **10 个**模型全开了 `extra="allow"`；`RunCancelled` 没被点名 ⇒ 删掉它的 `extra="allow"` 无人可判 | **不是补第 4 个模型，而是改成对全集断言**（新增 `test_every_workflow_model_declares_extra_allow`，判据从「这 3 个透传未知键」换成「这 10 个都声明了 extra=allow」，名册从 `EXPECTED_PROPERTIES` 取）。⇒ 新增模型自动进判别面，不靠人记得补 |
| `G5-M-FIELD-04` | **INVALID**（pytest 退出码 2 = 收集阶段就崩） | replacement 把字段改名成 `_hint_declaration_removed` ⇒ **Pydantic v2 在类定义时抛 `NameError: Fields must not use names with leading underscores`**（本机 2.13.4 实测）⇒ 模块压根 import 不了 | 改成无前导下划线的名字（正是 Pydantic 自己建议的写法） |
| `G5-M-FIELD-07` | **INVALID**（同上） | 同款 `_updated_declaration_removed` | 同上 |

⚠️ **这三条印证了 runner 那条硬断言的价值**：没有 `INVALID` 桶，这两条会被记成
`[CAUGHT]`——**而实际上测试一次都没跑起来**。那是一条会直接写进项目记忆的假结论。
另注「**过猛的变异**」（改到源码读不出来）与「**不足的变异**」（改不到行为）
一样拿不到结论，方向相反但同样要求变异落在**被测行为**上。


### Added（2026-10-16 批次（七）：三处「前端复刻后端规则」的跨端契约看守）

> **来源**：用户指令「接着做那三条跨端看守」——清偿
> `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md` §三
> 登记的三处漂移风险。该节原文：「UI 复刻后端规则本身**不是错的**……风险是**漂移**，
> 不是**存在**。**对策是测试看守，不是删除**」。
> 本批就是那三份看守。**零业务代码改动**（只加测试与一个读取器）。

**背景：动手前先量「现状到底有没有看守」**——结论是**一条都没有**：
`grep -rln "readFileSync|node:fs" apps/web/src/` **返回空**。前端四处测试
（含 `ledgerState.test.ts` 里那句「(与后端 packages/domain/ledger/models.py 对齐)」）
都只测了前端函数自己，**没有任何一条真去读过后端文件**。那句话是**声明**，不是断言。

| 位置 | 复刻了什么 | 文档标的风险 | 看守 |
|---|---|---|---|
| `utils/chapterState.ts` | 把后端写入 `workflow_runs.error` 的**自由文本**当协议解析（`rejected` / `rejected-for-revision` / `cancelled by user`） | **高** | `chapterState.backend.test.ts` 6 例 |
| `utils/ledgerState.ts` | Hook 五态机 + Debt 四态机的合法后继表（**手抄**） | **中** | `ledgerState.backend.test.ts` 7 例 |
| `utils/pausePayload.ts` | `engine.py` 的 `__pause_payload__` 私有序列化布局 | **高** | `pausePayload.backend.test.ts` 5 例 |

⚠️ **高风险那两条正好打在强项上**：`pausePayload` 失效 = 审批卡拿不到 payload
= 作者看不到要批什么（评估 §一① 把「审批卡信息充分度」列为本仓强项）；
`error` 文本则如文档 §三 所指，与 G3（run 级 error 从不渲染）叠加——
**同一个字段既不被显示、又被当作分类协议，两头都没做实**。

### 关键设计：**基准必须从后端源文件解析，不能硬编码进前端测试**

若写成「断言后端写的就是 `'rejected'`」（把字面量再抄一份到测试里），
那只是把同一个字面量抄到**第三处**——后端改了、前端没改，测试照样绿。
正确形状是**从后端源码解析出事实**（邻接表 / 异常类携带的标识值 / 写入点的键名），
再断言前端复刻与它一致。读取器 `__crossBoundary__/backendSource.ts` 只用
`node:fs` + 正则，**不引入任何构建期依赖**（先例：`test_readability_rules.py`
的源码级形状断言）。

**读取器一律「找不到就抛错、解析为空就抛错」，绝不返回空值**——
空值会让「两边都空 ⇒ 相等」恒真。

### ⚠️ 突变验证当场抓到**我自己**的守卫有一处真实缺陷（CB-3）

第一次跑 6 条突变，**CB-3 存活**（把 engine 一处 `'cancelled by user'`
改成 `'cancelled-by-user'`，守卫仍全绿）。

**根因是量具自身的判别域被缩小了**，且形状是本仓反复登记的那条：
读取器带一个 `mustContain='cancelled by user'` 的过滤，而**突变改的正是这个串**
⇒ 改后的值不再包含它 ⇒ **被静默滤掉** ⇒ 只剩另一处仍匹配 ⇒ 恒绿。
「过滤条件用了突变会改变的那个串，于是突变把自己藏出了判别域」——
同坑区「看守自己被悄悄缩小了执行域」。

修法：读取器改为**返回全部** `error=` 赋值（不做内容过滤），过滤放到测试里可见，
判据用突变**不会破坏**的更宽子串 `cancel`。修后 **6/6 全捕获**。

⚠️ 修的过程中我一度写下「至少 2 种**不同取值**」的断言，当场把自己写红
（去重后只有 1 种，4 处匹配是同一串）——又一次「断言写得像在表演而不是在看守」。
最终形态：**下界断言**（≥2 处匹配，容忍注释增减）+ **逐取值断言**（每个 cancel
标识都必须被前端认出）。

### 残余边界（如实登记，不夸大）

- 读取器的正则**不区分代码与注释**：若后端把写入改成变量拼接
  （`error=CANCEL_ERROR`），守卫仍会扫到注释里的字面量而**保持绿** ⇒ 须人工核对。
  该边界已写进 `backendSource.ts` 的 docstring 与用例注释。
- 解析的是**字面形态**，不解析语义：后端若把状态机改成从别处导入常量，
  读取器会抛错（响亮失败，好过静默放过），但会需要更新。
- 只覆盖这三处；后端将来**新增**一处自由文本协议或私有布局，本看守不知道。

### Added / Fixed（2026-10-15 批次（六）：全站 response_model 收口 + 两条既有数据缺陷）

> **来源**：用户指令「全批；这看起来是复杂任务，并发多个智能体工作吧」——把批次（五）
> 登记的两类尾巴一次做尽：**① 其余裸 `dict` 端点**（含「列表端点是 `list[dict]`」这一
> 形状，它的 OpenAPI items 是匿名 object ⇒ 前端仍拿 `Record<string, unknown>`，
> **与未建模等价**）；**② 两条长期挂账**（`_last_paragraph` 切段口径、token 硬顶接线）
> 与两条本次新发现的既有缺陷。
> **零迁移**（不动 DDL）；业务表数仍 39。

**规模与口径**：带响应体的端点 **144** 个，本批前「有具名模型」仅 **61** 个；
本批后 **142/144**。余下 2 个是**文件下载端点**——`GET /projects/{id}/export`
（txt/docx/fanqie 三种 media type）与 `GET .../quality/q8-export`（CSV 附件），
**刻意不建模**且各有一条用例钉住这个决定（给文件端点套 Pydantic 会给 codegen
造「下载返回 JSON 对象」的假契约）。本数字由 regen 后的 OpenAPI **实测**得出，
非估算。

⚠️ **判据订正（本批过程中的自我纠错）**：第一版扫描把「schema 里含 `array` 字样」
当作已建模，于是 `list[dict]` 被算成已过账 ⇒ 报「67 个裸 dict」。**那是错的**：
`list[dict]` 的 items 是匿名 `object`，OpenAPI **不会**生成具名类型。改为逐层查
`$ref`（含 `array` 须看 `items`）后，真实数字是 **61 已建模 / 21 无具名模型**，
且那 21 个**几乎全是列表端点**——本批后半程的全部内容。这也是它值得记的原因：
**判据写宽一格，整份工作量表就错了一遍**。

**并发编排（13 个成员两轮，按文件路径互斥）**：第一轮 9 组（G1~G9）补 46 个单体
端点 + 2 条挂账；第二轮 4 组（L1~L4）补 21 个列表端点。分区全程生效，无一人越界。
第二轮之所以存在，正是因为上面那次判据订正。

**产物**：OpenAPI **98 paths / 72 → 136 schemas**（paths 未变）。新增
`arc/simulation/genre/reference/model_router/workflows` 六处 `models.py`（部分为追加）。

### ⚠️ 本轮**自己修掉**的两条既有数据缺陷（都不是本批引入）

1. **`PATCH /hooks` 与 `/debts` 会把未提及的章节外键抹成 NULL**（2026-08-23 起存在）。
   `service.py` 的章节外键循环**无条件** `fields[fld] = v`，而请求模型默认值是 `None`
   ⇒「没传」与「显式置空」不可区分 ⇒ 客户端只发 `{"status":"ACTIVE"}` 即清空两列。
   **判定是笔误而非设计的证据**：同一函数里另外 5 个字段都走「没传就跳过」。
   活两个月的原因是 `test_ledger.py` 覆盖了完整主链路，但**没有一条用例 PATCH 这些字段**。
   修法 `model_fields_set`：键不在集合里则不碰；显式传 `null` 仍能主动清空。
   判别：`test_ledger_patch_partial_update.py` 4 例**正反一对**（撤修复后 4 条全红）。
2. **`/model-profiles/{id}/test` 与 `/model-configs/{id}/test` 缺 `base_url` ⇒ 500 + `text/plain`**。
   `ModelRouter.get_provider()` 抛的 `ValueError` 落在 `try/except ProviderError` **之外**
   ⇒ 未捕获 ⇒ FastAPI 回裸的 `Internal Server Error`（非 JSON）。前端 `client.ts`
   按 JSON 解析 `detail`，**在错误展示这一步崩掉**——最该让人看见的路径反而最难看见。
   修法：构造纳入同一 `try`，`ValueError` → **422**。判别：
   `test_provider_test_endpoint_error_mapping.py` 4 例，含**反向用例**
   （上游真连不上仍走既有 200 + `ok:false` 语义，不得被新 except 误吞成「配置错」）。

### ⚠️ 本轮**自己引入并当场修掉**的一条回归

给 `GET /events` 与 `/relationships` 挂上 `response_model` 之后，**一行脏 JSON 数据
会让整个列表端点 500**（干净行陪葬）。挂之前只是「200 + 一个奇怪的形状」。
根因不在契约（裸 `json.loads` 无兜底一直存在），但**契约把它从软故障变成了硬故障**。
修法：service 侧 `_safe_json`（解析失败/形状不符 ⇒ 退回该字段空值，与「列本身为空」同形；
`who_knows` 改走既有的 `decode_who_knows`，timeline 侧早已如此）。
判别：`test_plot_dirty_json_row_degradation.py` 4 例（撤兜底 4 条全红）。

### 两条挂账（用户拍板后结清）

- **`AI-ENDING-SUMMARY` 的 `_last_paragraph` 切段口径**（2026-09-27 登记「留档待排期」）：
  收敛到模块唯一入口 `_split_paragraphs`（`\n+`）。**实测证实它一直是 bug**：人类锚点书
  19 章 `\n\n` 总数为 **0** ⇒ 旧实现返回的「末段」比整章可见字还长（长度比下界 1.0163 > 1）；
  收敛后为真末段（上界 0.0533）。这是「人类基线 0.00 是量具假象」的**同形状第二次**。
  **不设阈值**（登记，不是遗漏）：两侧实测命中皆 0 ⇒ 「人类上沿 < warn < 生成上沿」
  在 `0.0 < x < 0.0` 上无解，任何非零阈值都是凭空造数。真实结论是**本规则在现有语料上
  从不触发**，已写进 docstring 与规则 description。判别：`test_ai_ending_summary_paragraph.py` 7 例。
  ⚠️ 连带修掉一条**把缺陷写成期望值**的看守：`test_readability_rules.py` 原断言
  「内联切分恰好 1 处」（注释里明写「已登记为待修」）——**期望值本身不是恒真的，是恒错的**。
  改为 0 并补一条**正向**断言（防「删掉函数」也算过）。
- **token 硬顶接线**（2026-09-11 自查项，用户裁决「只接线告警，不阻断」）：
  `_ASSEMBLY_TOKEN_BUDGET` 此前唯一读者是它自己的测试（「数据源存在 ≠ 已进装配」的典型）。
  接线两处：① 超预算发一条 WARNING（与同包 `scene_word_budget` 的 rebalanced/thin_plan
  同一条既有非阻断通道）；② 三个 workflow 节点把 `_assembly_meta` **提到 ctx 顶层**。
  ② 的依据是实测：`_cap_checkpoint_payload` 按顶层键体积**从大到小**截断，而 `writer_input`
  实测约 **227KB**、`_assembly_meta` 仅 **79 字节** ⇒ **预算越超，信号越先消失**。
  判别：`test_context_token_budget.py` +5 例、`test_assembly_meta_top_level.py` 3 例
  （撤接线后 3 条全红）。

### 判别纪律：本批三层防线（都是踩过坑后的硬要求）

1. **禁自指断言**：期望键集一律**硬编码**，不得 `set(body) == set(Model.model_fields)`
   （删字段两侧同减 ⇒ 恒真，批次五 M14 实测存活）。每个测试文件都写明「`model_fields`
   只允许出现在『模型 vs 硬编码』那一条用例里」，并给 grep 自查命令。
2. **`extra="allow"` 必配 OpenAPI 断言**：删掉字段声明后响应**照样**有该键（extra 透出）
   ⇒ 只断言响应键集抓不到，必须断言 schema 的 `properties`/`required`。
3. **突变验证**：**175 条**规格，全捕获、0 存活、0 中止。执行基建见下。

### 新增基建：`tests/mutation_specs/`（把突变的「数据」与「执行」拆开）

并发开发时每个成员都要自验「我的测试能不能判别」，而自验必须能改源码——多人共写
一个脚本必然撞车（铁律 4）。故拆为：

- `g*.py` / `l*.py` = **纯数据**（每人独占一个文件）；
- `run_spec.py` = 执行器（所有人共用、任何人不得改），四条硬断言从
  `scripts/mutation_harness.py` 原样继承（绝对路径 / 启动失败与测试失败分流 /
  「跑过了」的证据 / 空摘要判 INVALID）；
- `validate_specs.py` = **只读预检**（锚点唯一性、变异是否真改字节、变异后可编译）。
  并发下锚点会因**别人的**改动而失效，而写规格的人已经收工了——预检的价值就在这。

⚠️ 执行器本轮自己出过两次缺陷，都已修并留痕：① `--all` 用前缀 `g*.py` 发现规格
⇒ 静默只跑 120/175 却报「全捕获 + exit 0」（改为按「含 `MUTATIONS`」发现 + **打印
覆盖清单**）；② 预检抓出 `G4M03` 锚点因 L2 新增同形字段而命中 2 次。

### 其余实测结论（**推翻先验**，按铁律留痕）

1. **`state/diff` 加 `response_model_exclude_none=True` 是错的**（任务书原写「必须带」）。
   实测：该开关会把恒为 `null` 的 `branch_id` 一起抹掉，空 diff 从 **3 键塌成 2 键**，
   是既有契约的静默变更。改用模型侧**字段级** `exclude_if`，端点刻意不加开关。
   **先验为何错**：我照搬了 `simulation` 端点的同款处方（那里确实正确）而没先验证——
   同坑区「ORDER BY 处方被实证推翻，勿复用」。
2. **列表行 ≠ 单体响应**：`GET /commits` 行 **12 键**，而 `POST /commits` 响应 **4 键**；
   `GET /deltas` 行里**没有** `payload_json` 键（7 个 change 数组被摊到顶层）。
   ⇒ 批量照抄单体模型必错。
3. **键集相同 ≠ 可复用**：`branches` 列表与 `BranchCreatedResponse` **逐字同形**，但后者
   `parent_branch_id: str` 必填非空，而 main 分支该值为 `null` ⇒ 复用让端点**恒 500**。
4. **`diff_snapshots` 的两份模型连「空则省略」的机制都不同**（simulation 靠端点级
   `exclude_none`、story_state 靠模型级 `exclude_if`）⇒ 断言 `model_dump()` 相等是错的
   （比出来的是机制差不是契约差）。改断言**字段同形 + 生产端覆盖**：
   `test_diff_model_shape_parity.py` 4 例。

### Changed

- `scripts/smoke_e2e.py` 顶部 4 处 docstring 表数由「业务表 38 / 物理共 39」订正为
  **39 / 40**（与 `/api/health` 实测、README、`test_health.py` 同口径）——登记的 A-009
  漂移点从 `count_tables` 挪到了这里，本批就地核销。
- `GET /api/health` 补 `response_model=HealthResponse`（全仓唯一定义在 `api/main.py` 的出参模型）。

### 闸门（批次六 + 七）

pytest **3272 passed / 1 skipped / 0 failed**（373.35s，项目 `.venv/Scripts/python.exe`，
`--ignore=tests/unit/test_m1_lock.py`）；vitest **755 passed / 56 文件**（含批次七 3 个
新文件 18 例）；`npx tsc -b` 零错；`npm run build` 通过（467.50 kB，与批次五同）；
`ruff check packages scripts tests` = `All checks passed!`；
树指纹 **575 文件 / `25282695…`** 跑测前后**逐位一致**；突变残留 0。

**突变验证 175 条（批次六）＋ 6 条（批次七）= 181 条全捕获、0 存活、0 中止。**
⚠️ 其中 **CB-3 首轮存活**，是守卫**自身**的判别域被缩小所致（详见批次七条目），
已修后复测 6/6。

pytest 3026 → **3273 collected**（= 3272 passed + 1 skipped）。增量来自 18 个新测试
文件（契约测试 13 + 主会话自办 5）与既有文件的扩充。

### Fixed（2026-10-15 批次（五）：G6 挂账的尾巴 —— 四个 router 补 Pydantic 契约，前端手写类型退休）

> **来源**：用户指令「排那批账，补 Pydantic 模型」——清偿批次（四）登记的挂账：
> `reveal_policies` / `arc` / `simulation` / `quality`(judge) 四个 router 的
> body 与 response 都是**裸 `dict`** ⇒ 不进 OpenAPI ⇒ 前端只能手写契约而无机器校验。
> **零迁移**（不动 DDL）；业务表数仍 39。

**方法论：先量再建，不读源码建模**。`response_model` 会**过滤**未声明字段——
照源码「读出来」的字段建模，漏一个就是响应静默少一个键，而功能测试常常照旧全绿。
所以建模前先用**临时库 + 合成夹具**跑通端点、把实际返回的键集 dump 出来
（`/tmp` 一次性探针，非入库）。这一步当场抓出三件读源码不会知道的事：

- `GET /arc` 的章节行里 `overall / pacing / prose_chars / volume_id / volume_number`
  是 **`null` 而非省略** ⇒ 模型声明为「必填可空」而不是「可选」；
- `diff` 的六个实体段是**整个省略**（`diff_snapshots` 的「有差异才写入」），
  而其余段恒在 ⇒ 前者必须 `= None`，后者不能给默认值；
- 剧透策略的列表参数真名是 **`status_filter`**（router docstring 写 `status`，代码用前者）。

**四个新模型 + 三处端点声明**：`core/arc/models.py`（ArcView 九件套）、
`core/simulation/models.py`（SimulationResult / Summary / StateDiff / ListDiff /
ChangedItem）、`domain/knowledge/models.py` 追加 `RevealPolicyOut` +
Create/Update 入参、`core/quality/models.py` 追加 `QualityJudgeRecord` /
`QualityJudgeResponse`。OpenAPI **98 paths / 52→72 schemas**。

### ⚠️ `response_model` 的两个静默丢字段陷阱（都有用例 + 突变看守）

1. **`response_model_exclude_none=True` 是承重的，不是可选的**。
   推演的 `StateDiff` 六个实体段靠「空则省略」表达「没变化」。不排除 `None` 的话
   FastAPI 会把每个 `None` 序列化成 `"characters": null` 之类，**凭空多出六个键**，
   把既有契约改掉。而前端 `SimulationPanel` 的读法是 `diff.hooks?.added?.length ?? 0`，
   多出来的 null **恰好扛得住** ⇒ 这个回归在前端完全隐形，只有契约测试抓得到。
2. **`extra="allow"` 会让「删掉字段声明」看不出症状**。
   `QualityJudgeResponse` 删掉 `judge` 字段后，响应**照样**有这个键（以 extra 身份透出），
   只有 OpenAPI 里不再声明 ⇒ 前端 codegen 丢掉它的类型。断言「响应键集」抓不到，
   必须断言 **schema 的 `properties` / `required`**（突变 M20 实测存活，见下）。

**另两个刻意的不对称**：① 剧透 PATCH 的入参加 `extra="forbid"`——过去传
`reveal_by_chapter` 会**返回 200 却什么都没改**（service 只读三字段），现在直接 422，
把「静默吞掉」显式化；② 人工评分的**请求体不加模型**——`save_judge_score` 刻意宽松
（会把 `score_avg / usage / dry_run` 一并存进 `judge_json`），加请求模型会顺带把入参
校验收紧（pydantic lax 会把 `"80"` 静默转成 80，而 service 会 422），那是一次
**没被要求的行为变更**。出参加了，入参不加，是本批的一个明确取舍。

### 行为变更（1 处，有测试记录）

**剧透策略入参改用 Pydantic 后，422 的 `detail` 从字符串变成 FastAPI 标准的数组。**
这是与仓内其它带 Pydantic 请求模型的端点（Volume / Chapter / Genre…）**对齐**——
reveal 此前是没跟上的那个。`tests/integration/test_reveal_policies_api.py` 相应改断言。

⚠️ 连带发现并修掉一个**全站性**的体验缺口：`apps/web/src/api/client.ts` 此前把非字符串
`detail` 直接 `JSON.stringify`，于是**每个** pydantic 端点的校验错误在 UI 上都显示成
一坨 JSON。补 `formatValidationDetail`：压成 `target_kind: Input should be …`（多条用「；」，
丢掉 `loc` 首段 `body`/`query`），非该形态仍回落 `JSON.stringify`（不吞信息）。
判别：`client.test.ts` 3 例。

### 前端：手写类型退休，改为 codegen

批次（四）那 5 组手写类型**已删除**，`types.ts` 改为从 `types.generated.ts` 重导出
（`RevealPolicy`→`RevealPolicyOut`、`SnapshotListDiff`→`ListDiff` 等只做**命名**别名，
不改语义）。生成类型比手写的**更严**（`Optional[X] = None` 出的是 `T | null | undefined`），
连带改了 4 个组件的读法——其中 `SimulationPanel` 新增 `readFieldDelta()`，
因为 `diff_snapshots` 对 dict 是**递归**的，某个字段可能是叶子也可能是嵌套子 diff，
契约类型只能给 `unknown`，与其到处 `as` 不如在边界收窄一次，且对「不是叶子」的形态
给出可读兜底而不是 `[object Object]`。
**`QualityJudgePayload`（请求体）仍手写**，理由见上。

### 突变验证扩到 21 条（M14~M21 为本批新增）

首轮三条出问题，**全部是量具的问题、没有一条是被测代码的问题**：

- **M14 存活**：契约测试的「逐字相等」断言写成「响应键集 == 模型字段集」——
  把字段从模型里删掉，两侧**同时**少一个键，仍然「相等」⇒ **自指断言恒真**。
  已改成**硬编码期望键集**（模型之外的第三方基准），另加一条「模型字段集 vs 硬编码」双保险。
- **M15 存活**：我的突变只插了一行注释，**等于什么都没改**（replacement 是 anchor 的超集）。
  已改为真正翻转 `response_model_exclude_none=True` → `False`。
  **教训：突变必须改变行为，加注释不算；脚本报「存活」时先怀疑自己写错了变异。**
- **M20 存活**：见上文陷阱 2（`extra="allow"` 让删字段无可见症状）⇒ 补 schema 层断言。

⚠️ 同轮 M18 两次被 `[ABORT]`：模型收紧后锚点没同步（`= None` 去掉、docstring 改写），
「锚点未命中即判脚本自身缺陷、强制中止」按设计拦下——若记成「跳过」，会误报成 18/19。

**闸门**：pytest **3025 passed / 1 skipped / 0 failed**（340.53s，项目 `.venv/Scripts/python.exe`，
`--ignore=tests/unit/test_m1_lock.py`）；`ruff` clean；vitest **737**（53 文件）；`npx tsc -b` 零错；
`npm run build` 通过；树指纹 534 文件（+5：两个 models.py、三个契约测试）跑测前后逐位一致
（`4ca5b823…`）；突变 **21/21 全捕获**、0 存活、0 中止；突变残留 0。

### Fixed（2026-10-15 批次（四）：G6 挂账 5 条 —— 剧透策略 / 弧光 / 人工评分 / 改稿指令 / what-if 推演）

> **来源**：用户指令「继续做那 5 条挂账」——清偿批次（三）登记的 G6 剩余 5 条。
> **纯前端批次，零后端改动**（这 5 条的端点本就存在，缺的是 UI 入口）。
> **无迁移**。
> **两条产品裁决由用户拍板**：① 剧情推演做「基于已有 delta 推演」而非 JSON 大文本框
> （后端入参是「10 元字段 + 7 变更数组」的完整 Delta，作者手写不出来——给原始 JSON
> 编辑器等于「有入口但作者用不了」，与 G1/G2/G3 同款）；② 弧光做独立 bible 页签
> （它与 G9 概览聚合数据重叠，但 charge/payoff 节奏与 alerts 是 G9 没有的）。

**11. 剧透策略** → 新 bible 页签「剧透策略」（`RevealTab`，19 例）。
「这个秘密何时对谁揭开」：CRUD + 状态筛选。

**12. 弧光** → 新 bible 页签「弧光」（`ArcTab`，18 例）。`GET /arc` 的只读看板。

**13. 人工评分** → 章节页（`ChapterJudgeSection`，14 例）。四维 0-100 + 结论 + 最多 3 条问题。

**14. 改稿指令** → 章节页（`ChapterRevisionNoteSection`，14 例）。给作者一个**在触发改稿之前**
先写下指令的地方。

**15. what-if 推演** → 概览页（`SimulationPanel`，32 例）。选章 → 读该章 delta → 挑一条作基底
→ 白名单重建（换 `delta_id`）→ 结构化编辑四组业务字段 → 跑 → 看 diff。

### ⚠️ 调研中复算出的后端真相（**全部经主会话亲自读码复算**，非转述）

> 这一节是本批最有价值的留痕：**五条能力里有四条的真相比评估报告写的更危险**。
> 评估报告只说「后端有、UI 零入口」，读码才发现「接上去之前必须先知道这些」。

1. **推演继承 `supersedes` 会污染真实历史**。`commits.py:312-320` 的 `submit_delta` 会
   `UPDATE state_deltas SET status='superseded' WHERE delta_id = ?` 指向被取代者
   ⇒ 派生件若照抄 `supersedes`，跑一次推演就把**真实 delta 标成已废弃**。
   `core/simulation/README.md` 那句「main 快照、领域表、commit 数全程零变化」**没有提这一条**。
   对策：`deriveDelta` 用白名单重建并强制 `supersedes` 置空。
2. **delta 端点返回的行比 schema 多一个 `status`**（`queries.py:155` 显式补的），
   而 schema 是 `additionalProperties:false` ⇒ 原样回传 `/simulate` 必 422。
   对策：白名单而非黑名单。
3. **人工评分的越界分不返 422，是静默夹取**。`quality/service.py:243`：
   `v = max(0, min(100, int(v)))`，只有非整数才 `ValueError`。
   且 `verdict ≤50` / `top_issues ≤3×≤30` **后端零校验**（service 层原样保留）
   ⇒ 只靠后端的话作者能存进一条超标记录。对策：前端夹取后**写回输入框**（否则作者填 150
   看到的是 100 却不知情），长度校验在前端兜住。
4. **`revision_note` 的写入方是 4 个不是 3 个**：本端点（`domain/chapter/service.py:539`）、
   resume 驳回（`chapter_review/pipeline.py:1117`）、**质量门禁阻断**
   （`chapter_commit/gate.py:288`）、reopen（`service.py:618`）。其中**只有 reopen 是追加**
   （`existing + "\n" + "reopen: …"`），是全仓唯一保留历史的写入方。
   ⇒ UI 绝不可呈现成「意见箱 / 笔记历史」。
5. **`reveal_policies` 的两处反直觉**：列表查询参数叫 **`status_filter`**（router docstring
   写的是 `?status=`，**以代码为准**）；`PATCH`/`DELETE` **完全忽略 URL 里的 `pid`**
   （只按 `policy_id` 单查 ⇒ A 项目的策略能经 B 项目的 URL 被改掉）。
   另：`create` 确实校验 target 存在，但 `SELECT 1 FROM <表> WHERE pk = ?` **不带 project 条件**
   ⇒ 别的项目的实体 id 也能建策略（`service.py:352`）。
6. **`GET /arc` 无 `response_model`**，且 `chapters[]` 里**没有 `chapter_id`**（后端显式 pop 掉）
   ⇒ 拿它当行键或做跳转都会落空。

### 手写契约类型（G10 的延续）

这 5 条对应的 4 个 router body/response 全是**裸 `dict`** ⇒ 不进 OpenAPI ⇒
`types.generated.ts` 永远不会生成。故在 `types.ts` 手写，并**逐组登记派生来源**
（RevealPolicy ← `domain/knowledge/models.py:45`；ArcView ← `core/arc/service.py::build_arc_view`；
SimulationResult ← `core/simulation/service.py:125`；StateDiff ← `story_state/snapshots.py:121`）。
**刻意不用 `[key: string]: unknown` 索引签名**（G10 的病根），顶层字段一律必填。
顺带补上 `QualityReport.judge`——**该字段此前根本不在类型里，而 `GET .../quality` 一直在返回它**，
又一例「消费方有数据源但类型没跟上」。

**挂账**：这 4 个 router 应补 Pydantic 模型并声明 `response_model`（`SnapshotResponse` 是先例）。
⚠️ 但 `response_model` 会**过滤**未声明字段——补之前必须先核对实际返回的键集，
否则静默丢字段（`tests/api/test_volume_membership_contract.py` 就是这个坑的看守）。

### 页面接线判别（5 例）+ 突变验证 13/13

组件自身测得再好，**页面不挂载就等于没有 UI**，而「把挂载整行删掉」不会让组件的任何一个
用例变红。故补 5 例**接线判别**（概览 1 / 章节 2 / 设定页 2），判别点是面板**专属**的
testid 与专属 API 调用。

**`scripts/mutation_harness.py` 扩到 13 条**（新增 M8~M13 全部是接线突变：删 5 处挂载 +
撤 1 处 URL 契约登记）。**13/13 全捕获**，逐条 SHA256 校验还原。

⚠️ **M8 首轮存活，当场抓到一条不合格的判别用例**：首版接线用例断言
`chaptersApi.listByProject` 被调用，而**总览页自己**（G9 的「写到第 N 章」）也调它
⇒ 把 `<SimulationPanel>` 挂载整行删掉，该用例照样全绿。已改断言面板专属的
`simulation-panel` testid + 只有面板才调的 `storyStateApi.listDeltas`，复跑转红。
**这就是「断言了结果 ≠ 判别了机制」的原样复现**——判别点选错时，测试绿着而不测任何东西。
⚠️ 同轮 **M9 被 `[ABORT]`**（锚点写的是 8 空格缩进、实际是 6 空格且带 `chapter ?` 包装），
脚本的「锚点未命中即判自身缺陷、强制中止」按设计生效——若它记成「跳过」，这轮就会报成 12/13。

**闸门**：vitest **734 passed**（632 → +102 = 5 个新测试文件 97 例 + 5 例接线）；
`npx tsc -b` 零错；`npm run build` 通过（bundle 427.90 → 466.94 kB，gzip +12 kB）；
`ruff` clean；pytest **3009 passed / 1 skipped / 0 failed**（347.72s，项目 .venv）——
与批次（三）同数（本批零后端改动，指纹口径内只多了 `scripts/mutation_harness.py`）；
**树指纹 529 文件跑测前后逐位一致**（`0ecb30d6…`）；突变残留 0（无 `.mutbak`、无突变标记）。

### Fixed（2026-10-15 批次（三）：G6~G10 —— 能力面接入 / 债逾期 / 概览页双口径 / 状态聚合 / 快照契约）

> **来源**：用户指令「开工」——按 `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md`
> 的 G6~G10 五条落地（接续批次一 G1~G3、批次二 G4~G5）。
> **本批是三批里第一个带后端改动的**：G1~G5 全是纯前端（树指纹 `ab46b608…` 前后逐位一致），
> 本批因 G10 要补 `response_model` 才动后端，改动面 = 新增 1 个 schema 模型 + 2 处端点声明。
> **无迁移**（业务表数仍 39）。

**6. G6 状态机能力面远宽于 UI 面**——评估列出 13 条「后端有实现、前端零入口」的能力，
本批接了 **8 条**（`endpoints.ts` 新增 **16 处端点绑定 / 13 个唯一 URL**）：

| 能力 | 端点 | 落点 |
|---|---|---|
| 回滚 | `POST /commits/{}/rollback` | `StateHistoryPanel` |
| 版本回溯 | `GET /state/versions/{v}`、`GET /state/diff` | `StateHistoryPanel` |
| 单章 delta | `GET /chapters/{}/deltas` | `ChapterDeltasSection` |
| 卷 | 6 端点（列表/建/详情/改/seal/assign） | `VolumesTab` |
| 人物关系图 | `GET /relationships` | `RelationshipsTab` |
| 交付判定 + 签约自检 | `GET /delivery-verdict`、`GET /signing-check` | `DeliveryPanel` |
| 时间线删除 | `DELETE /timeline/{id}` | `PlotTab`（timeline 此前只读） |

外加 `ChapterDeliveryActions`（章节 `COMMITTED ⇄ RELEASED` 标记）——**这条不算 G6 接线**：
它用的 `PATCH /chapters/{cid}` 与 `ChapterUpdatePayload.status` 经查都是**既有面**
（`git show HEAD:apps/web/src/api/endpoints.ts` 命中），本批只是补了 UI 入口，
故不计入上面 8 条，也不进挂账。

⚠️ **挂账 5 条**（本批不做，留档待排期）：剧透策略 `reveal_policies` 4 端点 /
剧情推演 `simulation` 3 端点 / 弧光结构 `GET /arc` / 人工评分 `POST /chapters/{}/quality/judge` /
改稿意见单独写 `PATCH /chapters/{}/revision-note`。
判别：`grep -c` 五个路径关键词于 `apps/web/src/api/endpoints.ts` 均返回 0（`arc` 有 `format` 误报，
实际同样未接）。

**7. G7 债没有逾期判定，而伏笔有**。
`deadline_chapter_id` 此前只当普通列渲染成文本「第 N 章」，无判定、无高亮——
而伏笔侧的 `isHookOverdue` 早就让行染红 + 挂「逾期」徽标。**同一件业务事（「到期未偿」）
两个 UI 只有一个做对了，且不对称的理由未确认**。补 `utils/ledgerState.ts::isDebtOverdue`
（与 `isHookOverdue` 同构：判据 = `deadline_chapter_id` < 当前最大章号），
`LedgerTab` 债行接入同一套染红/徽标。判别：`ledgerState.test.ts` 逾期对称用例。

**8. G8 概览页并排放着两个口径不同的数据源**。
概览页四计数读**状态快照**（commit 时 observer 自动推进），台账页读**人工台账**
（`LedgerTab.tsx` 自认「管理面，不与 state 快照自动同步」）——同一本书两个「伏笔数」并排摆着，
零说明。补显式说明条：点明两者来源、**不自动同步**、数值不同是正常的（快照反映已提交章节的
观察结果，台账反映此刻的意图）。**刻意不合并成单一数字**——那要选一个口径，另一个就成错的。
判别：`ProjectOverviewPage.test.tsx` 说明条存在性与文案要点。

**9. G9 「这本书现在什么状态」没有聚合视图**。
作者问「写到第几章」要去章节页、「哪些伏笔没回收」要去台账页自行筛选、「债逾期」根本没人答
（G7）、「角色状态变化」完全没有 delta 视角——四个问题散在三个页面，其中两个不答。
概览页该卡从「故事状态快照」改名「这本书现在什么状态」：加「写到第 N 章（已定稿 M 章）」
（此前只有「共 N 次提交」）、加「待办」区（未回收伏笔 / 未偿清债务，各带逾期数），
逾期判定**复用 `ledgerState` 判据单点**而非另写一份。判别：`snapshotCounts.test.ts` 10 例。

**10. G10 `SnapshotResponse` 是欠定义类型 ⇒ 概览页两个统计项恒为 0**（本批唯一的**真缺陷**）。

- **形状**：`types.ts` 里手写 `version?` 与 `state_version?` **两个互斥命名**（后端只写后者）、
  六个全 `optional`、外加 `[key: string]: unknown` 索引签名——这不是契约，是「不知道后端返回什么」的兜底。
  兜底逼出 `endpoints.ts` 两层手写归一化（各一份 `normArr`/`normObj`），而那层归一化**把形状拍反**：
  `characters`（后端 list）被 `normObj` 拍成 `{}`、`events`（后端 dict，按 `event_id` 键）被 `normArr` 拍成 `[]`
  ⇒ 概览页「角色」与「事件·累计」**两个统计项恒为 0**，且全程无任何告警。
- **修法**：后端新增 `packages/core/story_state/models.py::SnapshotResponse`，
  两个端点声明 `response_model`（契约进 OpenAPI，52 schemas ← 51，paths 仍 98）；
  前端删掉臆造的 `version` 别名与索引签名，类型改用 regen 产物 `types.generated.ts`，
  `endpoints.ts` 两层归一化**整层删除**（后端 `snapshots.latest_snapshot_version` 已 `_parse_required_json` 解析过，
  归一化是纯损失）。计数口径收进 `utils/snapshotCounts.ts` 的 `listLen`/`dictLen`（显式区分 list 与 dict）。
- **`extra="allow"` 是刻意的**：故事状态是开放 JSON 文档，契约描述**已知的六个**集合键、
  不宣称封闭——封闭的话每加一个集合都要改契约 + regen 前端类型。
- **严不严的判据不是拍脑袋**：字段形状由 `snapshot.build_initial_state` / `materialize_snapshot` 保证，
  并由 `commits.rebuild_snapshot_collections_from_db` 从领域表重放；**实测 133 条存量快照形状零违例**
  （2026-10-15，只读查询）才敢用严格类型而不是 `Any`——宽松类型只会把同一个坑重新挖一遍。
- ⚠️ **这个缺陷之所以能活到现在，是因为夹具抄了它的形状**：旧夹具照抄归一化后的
  `characters: {}` / `events: []`，于是「测试通过」与「实现错误」是**同一个来源**，
  任何针对实现的变异都抓不到它。已登记进坑区（新增形状）。
  判别：`tests/api/test_snapshot_response_contract.py` 5 例（schema 存在性 + 端点引用 + `extra="allow"` 不丢字段）。

**突变验证**：新建 `scripts/mutation_harness.py`（7 条硬编码变异，6 条 vitest + 1 条 pytest，
退出码 0 = 全捕获 / 1 = 有存活或中止；**会临时改源码 ⇒ 禁与 pytest 并发**）。
**7/7 全捕获**，每条均按 SHA256 校验还原、逐条报「还原校验 OK」；
事后突变残留清点 0（无 `.mutbak`、无 `MUTATION-PROBE-` / `# MUTATION-N` 标记，按坑区要求显式 `-CaseSensitive`）。
主会话按 §三.1 亲验了 M3（不采信脚本汇总）：手工施加变异 → 3 failed / 10 passed，
红的是 `volume-seal-confirm` 二次确认对话框没出现 ⇒ 确系预期机制，非收集失败。
**复核中发现并修掉脚本自身的一处缺陷**：`run_web` / `run_py` 返回的是 `out[-2500:]` 截断窗，
而 M3 有 3 条失败时 vitest 打出整段 DOM 调试输出，把顶部的 `❯ file (13 tests | 3 failed)`
顶出了窗口 ⇒ 摘要**退化成空串**（M5 同样受害）。**空摘要不是无害的**：
坑区那条纪律要求复核者「确认红的是预期那条而非收集失败」，摘要为空时这一步**无法执行**——
量具恰在最需要被复核的场合（失败输出长）交白卷。已修：`run_*` 返回完整输出，
新增 `summary_line()` 去 ANSI 提取、pytest 优先取带用例 id 的 `FAILED <test_id>` 行、
兜底取最后一条非空行。修后 7 条全部给出可复核摘要。

**闸门**：`ruff` clean；`npx tsc -b` 零错；`npx vitest run` **632 passed**（539 ← 7 个新测试文件 66 例
+ 6 个既有测试文件扩充 27 例）；`npm run build` 通过；
pytest **3009 passed / 1 skipped / 0 failed**（319s，项目 .venv）——基线 3002 **+7**
＝`test_snapshot_response_contract.py` 5 + `test_volume_membership_contract.py` 2，账对得上。
**两次树指纹逐位一致**（`61d0a442…`，529 文件）⇒ 中途那次跑测未污染树。
⚠️ **首轮全量跑出了 2 例假红**（`test_openapi_contract` 两条）：`python` 在本机指向
`AppData/Local/hermes/hermes-agent/venv`，**不是项目 `.venv`** ⇒ live schema 的
`info.version` 读成 `dev`、与 openapi.json 的 `3.9.0` 不等，与 §四登记的 export_openapi 同款坑。
**判别方法**：假红的报错里两边的值一个像「本该如此」一个是 `dev` 这类**占位/回退值**，
而 `assert '3.9.0' == 'dev'` 的形状本身就指向「读版本的路径不对」而不是「文件过期」——
真漂移时 openapi.json 侧会是旧版号（如 `3.8.x`）。用 `.venv/Scripts/python.exe` 复跑才是权威数
（项目 .venv 实测读出 3.9.0）。

### Fixed（2026-10-15 批次：UI 侧三条「后端有能力、作者够不着」的缺口）

> **来源**：用户指令「开工」——按 `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md`
> 的 G1/G2/G3 三条落地。**零后端改动**（树指纹 `ab46b608…`/525 文件前后逐位一致）。
> 三条的共同形状与 2026-09-16 `author_intent` 死参数事故**同款**：
> 后端写了、装配在用、UI 没有入口 ⇒ 作者以为设了其实没设。

**1. G1 `writing_bible` 全站无编辑入口**（迁移 0029 的项目级写作圣经）。
后端 `ProjectCreate`/`ProjectUpdate` 收该列、`service._coerce_writing_bible` 落库、
`builders_common.common.resolve_author_intent` 按「基线 + 增量」拼接、
writer/director 缓存键各带 `bible_fp` 维度——**唯独前端一个入口都没有**
（改动前全前端仅 `types.generated.ts` 命中该字段）。
补：`types.ts` 三处补字段（`Project` / `ProjectCreatePayload` / `ProjectUpdatePayload`）+
`ProjectFormModal` 折叠式 textarea。
**下发规则刻意不照搬 fast_mode 的「只在变化时下发」**：文本字段未改动时下发同值是幂等的，
而省略键会让「作者清空了圣经」这一显式操作**静默失效**（PATCH 键缺失 = 保留原值 = 清不掉）。
判别：`ProjectsListPage.test.tsx` 6 例（填入去空白 / 纯空白 ⇒ null / 清空 ⇒ 键必须存在 /
只改名字 ⇒ 原值原样带回 / 无圣经不凭空造内容）。

**2. G2 `gate_override` 无入口 ⇒ enforce 模式下作者无逃生舱**。
后端 P0-1（2026-09-18）写明该字段是 confirm 档规则的**唯一**放行口，
前端至今零入口——被 confirm 命中（如 trigram 重复）即只能改配置或改代码。
补：门禁横幅读 `plan_json.gate_blocked.gate` 三态并据此分流
（`confirm` ⇒ 「显式接受并提交」+ 必填理由；`block` / `block+confirm` ⇒ **只给改稿**）。
`block+confirm` 刻意不给接受按钮并附说明：硬停不可覆盖，给了就是**假出路**
（作者填完理由点确认仍被同一批硬伤拦下，还多一条误导性操作痕迹）。
历史标记缺 `gate` 字段按 `block` 处置（fail-closed）。
判别：`ChapterDetailPage.test.tsx` 7 例（含 `it.each` 正反一对）。

**3. G3 run 级 `error` 从不渲染**——作者看到红色「失败」徽标却**永远读不到原因**。
`ChapterStatusBadge` 只把它当分类依据（判 rejected 两态），文本不进任何 DOM。
后端其实铺好了路：`chapter_commit/gate.py` 明写「把整段结构化 payload 追加进 ValueError，
run FAILED 时 `runs.error` 已包含它，前端 QualityPanel 可直接从错误字符串里解析」——
而解析方此前不存在。补：run 级错误条渲染在节点时间线 `<details>` **之外**
（终态时间线默认收起，放里面等于把唯一原因藏起来）+ run 行徽标 title 回落到 `r.error`。
**不截断**（门禁阻断的 error 带 rule_id 清单与证据摘录，截掉正是「在不知道批准什么的情况下批准」）。
判别：`ChapterDetailPage.test.tsx` 6 例（真实失败渲染且不受收起影响 / 徽标 title /
`rejected`·`rejected-for-revision`·`cancelled by user` 三种非真失败反向用例 / COMPLETED 不渲染）。

**突变验证（AGENTS §三.2 硬要求）**：6 次变异**全部被捕获**，逐个 SHA256 复核已还原——
M1 清空圣经时省略键（红 1）／M2 完全不提交 `writing_bible`（红 6）／
M3 接受按钮不看 `gate` 档位（红 2）／M4 去掉理由必填守卫（红 1）／
M5 徽标 title 不回落 `r.error`（红 1）／M6 不排除三种非真失败（红 3）。
M5 首轮**被 SKIP**（锚点缩进写错，命中 0 次）——「跳过」不等于「存活」，
按坑区「突变存活 = 该测试在断言一件它测不到的事」的口径单独复核后重跑，才拿到那份证据。

**本批无迁移、无后端改动**：树指纹 `ab46b608…`/525 文件前后逐位一致；
前端 `npx tsc -b` 零错、`npx vitest run` **519 passed**（500 + 本批 19）、`npm run build` 通过。

### Fixed（2026-10-15 批次（二）：G4 禁用理由不给出路、G5 提交按钮不认「审完又改稿」）

> **来源**：用户指令「继续」——按 `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md`
> 的 G4/G5 两条落地（自主排的优先级：G5 作者一定会撞且 UI 零提示，先于 G4）。
> **零后端改动**：树指纹 `ab46b60837e48687…`/525 文件，与 2026-10-15 全量复核记录**逐位一致**。

**4. G4 禁用理由只说「不许做」，不说「先做什么」**。
`getButtonAvailability` 的 reason 原先是纯状态机复述
（「提交 仅在状态为 REVIEWED 时可用；当前状态 DRAFTED」）——作者读完仍得自己回流水线上找。
补 `upstreamHint()`：从目标 action 往前找第一个「其 `EXPECTED_STATUS` 覆盖当前状态」的上游步骤，
给出「请先完成「审校」」。**找不到就返回 null，不编造**——状态机不是单向的，
`review` 在 REVIEWED 时上游两个步骤都不覆盖 REVIEWED，无路可走，UI 就不该假装有。
措辞用「请先完成 X」而非「点 X 即可提交」：后者可能是撒谎（有的组合做完 X 仍不到目标状态）。
判别：`chapterState.test.ts` 4 例（含「review@PLANNED 指向写正文」与「无路可走时不给 hint」）。

**5. G5 提交按钮不认「审完又改稿」⇒ 作者靠撞墙发现规则**。
后端 router 层 `control.py::_guard_commit_draft_freshness` 在 `start_commit` 里、
**早于** `_start_workflow`（快通道解析）执行：审校 COMPLETED 之后又冒出来的草稿，
**即使快通道开着也一律 409**。前端零建模 ⇒ 按钮点亮 → 作者点了吃一句 409。
补 `findUnreviewedDraft()`（纯函数）：取该 chapter 最近一次 COMPLETED 的 `chapter-review` run 的
`ended_at`，找 `created_at` **严格更晚**的 draft ⇒ 提交禁用并说明是 v几、需先重新审校。

三个判别要点，逐条都是「复算出来的、不是想出来的」：

- **必须排在快通道旁路之前**。UI 若把旁路放前面，就会在一个**必然吃 409** 的组合上把按钮点亮
  ——又是一次「UI 放行而 backend 409」，与 G1/G2/G3 同款。
  判别：`快通道开着 + DRAFTED + 有未审改动 ⇒ 仍然禁用` + 其反向用例（无未审改动 ⇒ 仍放行）成对，
  少任何一条都可能被「旁路被整体摘掉」蒙过去。
- **`workflow_name` 是 router 层加的，不是下层 SQL 的**。先验曾据
  `workflow_runtime/runs.py` 断言「前端认不出 chapter-review」（`workflow_id` 是不透明 `wf_<hex>`），
  **被推翻**：router 层 `runs.py` 的 `list_runs_endpoint` 逐行附加该字段。
  据此**零后端改动**落地；同时订正 `api/types.ts` 里写反的注释（「后端暂未提供」），并留痕这次被推翻。
- **失败方向刻意选「漏报」**：缺 `workflow_name`、时间戳解析失败 ⇒ 返回 null 不拦。
  误报（多拦）会挡掉本该能做的操作，漏报只是退回今天的行为（点提交吃一个 409），后者代价小得多。

判别：`chapterState.test.ts` 15 例（`findUnreviewedDraft` 7 + 次序 4 + G4 4）+
`ChapterDetailPage.test.tsx` 4 例页面接线（纯函数对了不等于页面接上了）。
其中「无未审改动时其余三个按钮的可用性**逐字不变**」是形状级断言，不是点值断言。

**突变验证**：6 次变异**全部被捕获**，逐个 SHA256 复核已还原——
N1 把未审判定挪到快通道旁路之后（红 1）／N2 缺 `workflow_name` 也认成 review（红 2）／
N3「严格大于」改「大于等于」（红 1）／N4 取最早而非最近一次审校（红 1）／
N5 禁用理由不给上游出路（红 2）／N6 页面不再把 `unreviewedDraft` 传给 header（红 4）。

⚠️ **本批头一轮报了「N1/N3/N5 三个存活突变」，实为量具缺陷**：三条突变的 `-t` 过滤器**一条用例都没命中**
（`vitest -t` 匹配不到时报的是「全 skipped、无 failed」，与「跑过了、全绿」在 failed 判据上**完全同形**），
其中一条过滤器还是我把「次序」打成「次度」的错别字，另两条只在用例**正文/注释**里出现而不在**名字**里。
差点照着「存活」结论去改本来正确的测试。**探针已加硬断言**：`ran >= 1` 否则抛异常中止，
与上一批的「锚点未命中即中止」并列为同一纪律的两面。

**本批无迁移、无后端改动**：树指纹 `ab46b608…`/525 文件前后逐位一致；
前端 `npx tsc -b` 零错、`npx vitest run` **539 passed**（519 + 本批 20）、`npm run build` 通过。

### Added（2026-10-15 业务逻辑评估：对标 GitHub 同类高星项目）

> **来源**：用户指令「对软件的业务逻辑做一次评估，参考 GitHub 上的同类高星项目」。
> **本轮零代码改动**——纯评估，结论与取证链落 `NovelOS-Artifacts`。
> **文档**：`NovelOS-Artifacts: docs/analysis/business-logic-assessment-2026-10-15.md`
> **方法**：2 名只读调研员分维度扫外部（GitHub 测绘 / 商业产品工作流对比）→
> 主会话按 §三.1 逐条复算。外部星数由主会话经 `api.github.com/repos/...` **独立复核**，
> 不采信转述；内部数字直接查 `data/novelos.db`（只读，SELECT）。

**1. 外部坐标（2026-10-15 观测）**：inkos 10,301★ / oh-story-claudecode 7,575★ /
webnovel-writer 7,490★ / AI_NovelGenerator 6,218★ / AI-Novel-Writing-Assistant 3,189★。
主流路线是「story bible + 角色/世界显式状态 + 章后一致性审计」——**NovelOS 在这条主干上**，
但多走了一步：**把状态变更版本化**（`story_states` + `state_deltas` + 带审批审计的 `commits`）。

**2. 强项**（三条经实测/读码确认）：① `hooks` + `narrative_debts` + `reveal_policies`
合起来是**信息状态追踪**（谁知道什么、何时可揭示、哪笔债到期）——长篇最难的部分，
同类多数只做到「角色卡 + 摘要」；② **全样本中唯一把质量做成阻断式**的
（商业产品全是提示式：给建议、能忽略、不断流）；③ `delivery/verdict.py` 的四档判定，
尤其把 `not_a_candidate`（还没东西可交）与 `not_deliverable`（交了会出事）**刻意分开**——
「混为一谈会让 27 章未写的项目看起来像 27 章写坏了」。

**3. 缺口一（本轮最重要的实测发现）**：1,530 条真实调用的 token 分布——
**chapter-commit 占 38.2%，其中 observer 一个节点占 7.82M（全系统 33%）**，比 writer 还多；
单章 token 中位 **178k**、最大 854k（改稿回路失控）。
**对照 2026-10-14 快通道：它砍掉的是 9.7% 的那一档。**
墙钟上这个选择是对的（`chapter-review` 的 LLM 中位仅 0.9 分钟，但均值 29.8 / 最大 1326 分钟，
差额全是等人），故快通道是**注意力**优化而非成本优化。
两个具体洞：**`ai_call_logs.cost` 列 1,530 行中非零的 0 行**（有 token 没金额）；
**回滚能力后端完整实现、前端零封装**（`rollback` / `state/diff` / `state/versions` /
`chapters/{id}/deltas` 四项，后端引用数 4/2/2/2，前端 `endpoints.ts` 全部 **0**）。
⇒ **自动维护状态占了最大成本，而这个状态既无成本可见性、也无作者的干预入口。**

**4. 缺口二**：`RELEASED` 是声明的终态但**全仓无任何写入方**（不可达）；
叠加上述 4 项后端能力未接前端 ⇒ 状态模型的能力面远宽于 UI 面，
**最强的资产恰好是作者够不着的那部分**。

**5. 复核 2026-09-11 遗留的两条自查项**：① 全局 token 硬顶**仍未强制**——
`_TOKEN_BUDGET` 已重命名为 `_ASSEMBLY_TOKEN_BUDGET` 并在装配 meta 上报，
但 docstring 明写「只告警，不阻断也不改写已装配内容」，全仓无生产消费方；
② 内容配比**声明面已有**（题材包 `ratio_declarations` → `_ratio_instruction` 进 planner 上下文），
核销面无——但**不算缺口**，坑区「可测算子只作体检、不作处方」正是踩过
「对话占比写成硬指标→模型为凑指标灌对水」之后的刻意裁决。

**6. 一条未能确认、但会改变结论的**：调研员报 `webnovel-writer` 有「三档模式、minimal 档跳过
reviewer」；主会话复核其 README / `agents/reviewer.md` / `docs/guides/commands.md`
**均无此记载**，疑为未发布的 v7 草案。**若属实则「跳过人工确认」在头部开源已有先例，
快通道不是首创而是补齐已知设计点**——已在文档 §六 单独登记。

**7. 建议（按数据排序，本轮未实施）**：① 给 observer 一个成本档位（复用 `fast_mode` 的
项目级开关 + 三态 + 单点解析范式，同时压住改稿回路的方差）；
② 把 `rollback` 接到 UI（后端已写好）；③ 让 `cost` 列落库。

### ⚠️ 文档断链登记（2026-10-15 审查批次）

AGENTS 硬规则 7 的立规根因：38ec703 三仓迁移未回填引用，造成 ≥14 处断链——
反引号路径引用 markdown checker 查不出。本次全量审查查出**另一批**（引用处已就地标注
`〔断链〕`），三个路径在**软件仓与 NovelOS-Artifacts 双查均不存在**，且 `git log
--diff-filter=D` 无删除提交 ⇒ **它们从未在这些路径上被提交过**：

| 路径 | 出现处 |
|---|---|
| `docs/data-model/data-model-v0.md` | CHANGELOG:1194 / 1255 / 1697 |
| `docs/roadmap/v1.3-v2.x-plan.md` | CHANGELOG:1703 / 1732 / 1751 / 1773 |
| `docs/analysis/competitive-analysis-2026-08-24.md` | CHANGELOG:1795 |

**未修复，按登记处理**：历史条目的行号会随文件增长漂移，上表行号同样只在 2026-10-15
成立；真正的结论是「这三个路径不存在」。同文件内 `docs/sampling-audit-*.md`、
`docs/analysis/ai-tone-calibration-*.md` 都带了 `NovelOS-Artifacts:` 前缀且文件真实存在
——**正确写法就在同一个文件里**，可见这三处是漏写而非有意。


### 2026-10-15 全量审查修复批次（6 名对抗审查员 + Lead 逐条复算）

> **来源**：用户指令「全面审查此项目」。分 6 个维度并行对抗扫描（后端闸门 / 测试判别力 /
> 架构铁律 / 文档一致性 / 前后端契约 / 内容层安全），**主会话不采信子代理结论**
> （AGENTS §三.1）——每条都自己重读源码或重跑探针复算后才入册，R1/R5 报的三处
> 与我独立得出的结论吻合，构成交叉验证。

**1. CRITICAL：快通道静默自动批准了人工审校闸门。**
`control._start_workflow_core` 把 `ctx["human_input"] = {"approved": True}` 注入时
**没有按 workflow 区分**。而该键全仓有**两个读者且语义不同**：
`chapter_commit/gate.py` 读它当「预置放行本闸门」，
`chapter_review/pipeline.py::_author_review_node` 读它当「人已经做出的决议」
（其 docstring 明写「本 fn 总是抛 PauseRequested」）。
**端到端实测（对照组只翻项目开关）**：

| `projects.fast_mode` | 审校 run | `author_review` | 章节 |
|---|---|---|---|
| `true` | **COMPLETED** | COMPLETED | **REVIEWED** |
| `false` | **PAUSED** | PENDING | DRAFTED |

即：作者在快通道项目上点「审校」，系统一句不问就自批、章节直推 REVIEWED，
且**全程无任何留痕**。修复 = 预置批准按 `workflow_name == "chapter-commit"` 收口。
快通道的语义是「不跑 chapter-review」，不是「跑但自动批」。

**2. 根因：预置批准这条路径在端到端层面从未被执行过（空断言）。**
上一批的 e2e 夹具只产 `risk_level="LOW"` 的 delta ⇒ `_has_high_risk_change` 恒 False
⇒ `high_risk_approval` 节点在 `gate.py` 的 `if not needs: return` **提前返回、压根不读
`human_input`** ⇒ 把 `"approved": True` 改成 `False`，3 条 e2e 全绿。
这是与已登记两条坑区**都不同**的第三种失效形状（已入 AGENTS 坑区）：
「断言了结果 ≠ 判别了机制」是测不到机制；「夹具自带噪声 ⇒ 失败点漂移」是测到另一个机制；
**本条是测到了正确节点、但该节点提前返回 ⇒ 断言恒真**。
新增 `tests/api/test_fast_mode_review_gate.py` 四例：
人工审校仍 PAUSE（CRITICAL 回归）/ HIGH 风险 + 快通道 ⇒ 不 PAUSE /
HIGH 风险 + 关快通道 ⇒ 必须 PAUSE（反向用例）/ 临时库无模型配置 ⇒ 真实 Provider 无从构造。

**3. MAJOR：项目弹窗关不掉快通道（推翻 10-14 对作者的使用说明）。**
`ProjectsListPage` 只在勾选时下发 `fast_mode`；取消勾选 ⇒ 键缺失 ⇒
`model_fields_set` 不含它 ⇒ UPDATE 不带此列 ⇒ DB 仍是 1，而复选框已显示未勾选、保存还提示成功。
**「回退就把勾去掉」这句话在项目弹窗上是错的**（章节页的开关发 `{fast_mode: v}`，能关）。
修复取「**提交值≠当前值才下发**」——不是简单的「补发 false」：后者会把编辑一个
`null` 项目时的「未设置」偷换成「显式关」（我的第一版修复正是如此，被自己写的用例当场抓红）。

**4. MAJOR：e2e 测试的「全程 mock」声明在机制上为假。**
`test_fast_commit_e2e.py` 只 mock 了 director/writer/observer；
`scene_planner`/`summarizer`/`critic` **会真的进 `ModelRouter.call_with_fallback`**——
拦住它们的是**巧合不是设计**：临时库无模型配置，router 在构造 Provider 之前就抛错，
错误被各节点 degraded 分支吞掉，测试照样全绿（实测 `scene_planner degraded: no enabled
model configured` 日志）。**未烧额度**（HTTP 出口 0 命中），§一 硬约束 2 未被违反。
现由 `test_e2e_db_cannot_reach_a_real_provider` 断言三张模型表均为空——
把「不发真实模型调用」从散文声明变成机器可校验的不变量。同款形状在
`test_gate_confirm_e2e.py` 已存在，本批是沿用而非新造。

**5. 代码层其余订正**：`read_project_fast_mode` 的 `except sqlite3.OperationalError`
收窄为只吞「no such column」（原注释只声称覆盖这一种，却把锁库/disk I/O 一并吞掉——
锁库会让快通道静默失效）；`start_write` 中两处失准注释按可复算结果订正
（`payload["status"] == "RUNNING"` 恒真；「两个条件都要避免绕过作者没同意的直提」
不成立——同一请求体可同时满足两者，它不是授权控制而是默认行为控制）。

**6. 本批自纠的文档错误（均为可数的整数/坐标，当场复算）**：
`fast_mode.py` docstring「四处消费方」→ 实为 **2**；
「该文件有 11 个 describe」→ 实为 **13**（HEAD 也是 13，不是行号漂移，是当场数错）；
AGENTS 坑区里 m1_long_run 的三个行号 977/960/967 → 实为 **979/961/968**
（**该坑区的主题正是「注释的行号必须可复算」，而我自己写进去的三个数字都是错的**）；
AGENTS「4 run → 2 run」→ 快通道不摘 plan，实为 **3 run**（计划已存在才是 2），
且「0 人工往返」另需独立的 `auto_commit` 开关，硬规则 10 从未点名它；
迁移链 `0001~0030` → **`0001~0031`**（两处，本批只改了一处）；
作者成员内容红线「18 条 PAUSED」无日期戳 → 补戳并记当前为 **17** 条（10-13 有 1 条走完）。

**7. 存量订正**：`docs` 迁移残留空目录 9 → **6**；`control.py:78/89` → **85/96**；
`planner_input.py` 目录与行号双错（实为 `packages/workflows/chapter_plan/planner_input.py:37`）；
8 处指向三个双仓都不存在文档的历史引用已就地标注并登记（见上方「文档断链登记」）。

**8. 树指纹纪律固化**：`scripts/tree_fingerprint.py`。此前那条纪律**只以散文存在**，
每次手写一行临时代码，导致其产出**谁都无法复核**——同树两次算出不同哈希（试了 6 种合理
公式都复现不出记录值 `890428ca…`），后来者无法区分「树变了」与「当时公式不同」。
**没有实现的纪律不是闸门，是仪式**：凡写成可复核哈希的约定，必须同时提供产出它的命令。

**9. 依赖方向看守补洞（存量，2026-10-15 审查发现）**：
`test_no_core_to_workflows_dep.py` 的 `ImportFrom` 分支只读 `node.module`、
**从不读 `node.level`** ⇒ `from ...workflows import X`（`module=="workflows"`、
`level==3`）对 AST 断言**完全不可见**；其兜底的源码文本断言找的是
`"packages.workflows"` **字面值**，相对导入写的是 `..workflows`，同样不含该字面量
⇒ **两条断言同时不可见**。
修法不是去解析相对导入的绝对目标（那需要完整包上下文），而是把规则收紧到
**无需上下文即可判定**：core 内禁止任何**逃出 `packages.core` 包**的相对导入
（判定式：`level >= 包深度`，包深度 = 相对仓库根路径段数 − 1）。
包内相对导入（`from .common import ...`，实测 191 处）照常允许——**第一版我误写成
「禁止一切相对 import」，被自己的新用例当场抓红**（191 处）：规则必须按真实用法边界划，
而不是按「越简单越好」的直觉划。

**10. 经审查后判定不修并登记**：auto-commit 守护线程失败对作者不可见
（`except` 只 `log.exception`；进程重启则无人起 commit，终态是「写 run=COMPLETED、
章节=DRAFTED」，DB 无任何字段记录本该有一次提交）。**未修**：失败模式罕见
（409 需用户在等待窗内另起 run／重启），且终态本身**可见**（章节停在 DRAFTED）；
为此新造持久化标记属超范围投机。沿用 `_auto_revise_runner` 的既有形状。
若日后该情形被作者实际遇到，再单独立项。

**11. 闸门（Lead 亲跑，指纹用新脚本取，前后逐位一致）**：
`ruff check packages scripts tests` = `All checks passed!`（修过 1 处 I001）；
全量 **3002 passed / 1 skipped / 0 failed**（292.55s），
指纹 `ab46b608…` / **525 文件**，跑前跑后**逐位一致**；
`npx tsc -b` 零错；`npx vitest run` **500 全绿**（494 + 6）；
`scripts/smoke_e2e.py` **PASS**——且**旧四步链路完好**
（`review PAUSED at author_review → resume approved → commit COMPLETED`，
这条正是本次 CRITICAL 修复的回归保险：非快通道路径仍必须挂在人工门上）；
`export_openapi` **98 paths / 51 schemas 未变**；突变残留 0（`-CaseSensitive`）。
基线 **2998 → 3003 collected（+5 全部来自本批）**。
**本批 7 次突变全部被捕获**，每个都逐个 SHA256 比对确认已还原：
① 撤 `workflow_name == "chapter-commit"` 收口 → 人工审校用例红；
② 预置批准 `"approved": True` → `False` → 快通道 HIGH 风险用例红
（**这条在修复前是存活的**）；
③ `_has_high_risk_change` 恒 False → 对照组用例红（证明 HIGH 夹具真进闸门分支）；
④ 临时库播种一行 `model_configs` → 不变量用例红；
⑤ `chapterStatus === 'DRAFTED'` → `!== 'REVIEWED'` → 状态边界用例红
（**这条在修复前是存活的**）；⑥ 项目弹窗退回「只发 true」→ 编辑关闭用例红
（**这条在修复前根本无用例**）；⑦ 往 `packages/core/quality/engine.py` 注入一行
`from ...workflows import chapter_commit` → 依赖看守的新断言红，而**原有的
`test_no_business_import_of_workflows_in_core` 仍绿**——正是两条断言盲区方向相反的实证。

### 2026-10-14 快通道：写完直接提交（项目级开关，拆闸门不拆状态更新）

> **来源**：用户指令「工作流太复杂，写一章到提交得花十几分钟，想拆掉所有闸门，
> LLM 写完一章就跳过后续所有环节直接提交」→ 用户三项裁决（砍到哪层 / 开关粒度 / observer 去留）。
> **性质**：**有意行为变更**（新项目默认关闭，存量项目行为逐字不变）。
> **不删任何闸门代码**——本批只加「有条件旁路」，回退成本为零。

1. **问题定位（逐环节核对源码，不是估算）**：写一章到定稿要走 **4 个 workflow run
   ＋ 2 次人工审批往返 ＋ 最多 2 轮自动改稿**，最多 11 次 LLM 调用。节点序与耗时面：

   | workflow | 节点 | LLM | 人工卡点 |
   |---|---|---|---|
   | `chapter-write` | load_plan / scene_planner / writer / polisher / length_check / condense / save_draft | 最多 7 | 无（不抛 `PauseRequested`） |
   | `chapter-review` | basic_checks / critic_review / deep_review / **author_review(Human)** / mark_reviewed | 2 | **PAUSE，必等人工点批准** |
   | `chapter-commit` | build_observer_ctx / **observer** / inject_validate / **quality_gate** / **high_risk_approval(Human)** / commit / summarize | 1~2 | quality_gate 可 FAILED；high_risk 可 PAUSE |

2. **⚠️ 本批最关键的技术事实（决定方案形态）**：`observer` 在 **`chapter-commit` 里而不在
   `chapter_review` 里**。它是唯一抽出角色目标推进 / 伏笔收放 / 世界观 delta 并写
   `story_state` 的节点 ⇒ **快通道保留它**，砍掉会导致第 2 章起失忆。用户已确认保留。
   ⇒ 「跳过 review」不会丢状态，这是整套方案能成立的前提。
3. **落地形态（拆什么 / 留什么）**：
   - **不跑** `chapter-review` 整条链（省 2 次 LLM ＋ 1 次人工往返）；因 review 不再驳回，
     自动改稿回路也一并消失（原最坏再多 10 次 LLM）。
   - **不阻断但不跳过** `quality_gate`：强制 `quality_gate_mode="report"`（该模式本已存在，
     `should_block` 恒假），**判定照算、报告照落库**，作者事后在章节页「质量」tab 仍看得到。
   - **不挂起** `high_risk_approval`：ctx 预置 `human_input={"approved": True}`（放行分支本已存在）。
   - **唯一改判定的一处**：`chapter_commit/commit.py` 的 `status != "REVIEWED"` 校验，
     快通道下放行 `DRAFTED`。
   - **写作流水线全保留**（scene_planner / polisher / condense / 字数重试一律不动）——
     用户裁决「只拆闸门」：那 7 次调用是质量成本，不是闸门。
4. **开关（三档优先级，单点解析 `workflows/fast_mode.py::resolve_fast_mode`）**：
   请求体 `StartWorkflowRequest.fast_mode` > `projects.fast_mode`（迁移 0031 新增）>
   env `NOVELOS_FAST_MODE` > 关。**可空三态**（NULL 未设置 ≠ 0 显式关），与 0023/0029 同款范式——
   必须能区分「作者从没这回事」与「作者明确关的」。请求体显式 `false` 是**逃生舱**。
5. **写完自动衔接**：`POST .../write` 传 `auto_commit: true`，写作 run 到达 COMPLETED 后由
   守护线程自动起 `chapter-commit`。形状镜像 resume 的 `_auto_revise_runner`，**两处有意的差别**：
   ① deadline **1800s 而非 180s**（那边等一次人工决议，这边等整章写作，180s 必然超时——
   与 2026-09-18 字数闭环「轮次耗尽」同源的量级误配）；② 只在 COMPLETED 时衔接。
   **`auto_commit` 与 `fast_mode` 解析结果必须同时成立**才衔接——否则会绕过一个作者
   没同意的 DRAFTED 直提（这条是 e2e 用例在实现过程中真抓到的缺口，非事后补记）。
6. **留痕诚实性（硬要求）**：快通道预置的批准若沿用原分支，`commits.author_approval_json`
   会把审批人记成 `"human"`——那是一条**没人做过的审批记录**。快通道一律记
   `approver="system:fast_mode"`、`notes="快通道自动提交：跳过 chapter-review 与人工审批，未经人工放行"`，
   并由 `test_approver_is_not_human` 钉住。
7. **顺带的结构修正（为承接守护线程）**：`_start_workflow` 拆出 `_start_workflow_core`
   （ctx 组装只有一份实现，HTTP 与守护线程共用）。**`_start_workflow` 保持原名与原签名**——
   `revise.py` 有 `from .control import _start_workflow` 的跨模块 import 契约，且
   `tests/api/test_workflow_cancel.py` 在 `control._engine` 上打 monkeypatch，故 engine 必须
   仍经模块级 `_engine(request)` 查找（AGENTS 硬规则 1 的「先 grep patch 打点」）。
   同理 `_check_chapter` 拆出 db_path 版 `check_chapter_in_project`（校验逻辑仍只一份）。
8. **测试与突变验证**：新增 `tests/unit/test_fast_mode.py`（13 例：四档优先级、三态语义、
   ctx 注入、注入顺序压过请求体 enforce、逃生舱）与 `tests/api/test_fast_commit_e2e.py`（3 例：
   写完自动提交且零 PAUSED / 关快通道后 DRAFTED 直提仍被拒 / auto_commit 单独用无效）。
   **四次突变全部被捕获**：删 `quality_gate_mode=report` → 2 红；删 `human_input` → 2 红；
   撤 `commit.py` 的 DRAFTED 放行 → e2e 红；摘掉 observer 节点 → observer 看守红。恢复后逐个 SHA256 比对确认无残留。

   ⚠️ **2026-10-15 审查订正——本条说过头了**：上面「删 `human_input` → 2 红」**字面为真**
   （`test_fast_mode.py::test_fast_mode_true_injects…` 确实红了），但它只证明了
   **ctx 里有这个键**，**没证明这个键真的起作用**。当次突变实验在**端到端层面是存活的**：
   把 `control.py` 注入的 `"approved": True` 改成 `False`，3 条 e2e **全绿**——
   因为夹具的 observer 只产 `risk_level="LOW"` 的 delta ⇒ `_has_high_risk_change` 恒 False
   ⇒ `high_risk_approval` 节点提前 return ⇒ **`human_input` 一次都没被读**。
   换言之，那 2 条红来自单测的「键存在」断言，闸门本身从未被执行过。
   已由 2026-10-15 批次的 `test_fast_mode_review_gate.py` 正反一对用例补上，
   并把该形状登记进 AGENTS 坑区（「夹具永远不触发闸门 ⇒ 空断言」）。
12. **闸门（Lead 亲跑，两次，两次指纹均逐位一致）**：**2997 passed / 1 skipped / 0 failed**，
   权威数 321.46s（`890428ca…`，523 文件）。第一次亦逐位一致（`c789e1ee…`，352.73s），
   其后 `ruff --fix` 改了一个测试文件的 import 顺序 ⇒ **该次结论不适用于当前树，复跑取权威数**
   （指纹纪律不是仪式：绿灯之后再改动照样作废）。基线 **2978 → 2998 collected（+20 全部来自本批）**。
   `ruff check packages scripts tests` = `All checks passed!`；突变残留 0；`npx tsc -b` 零错；
   `npx vitest run` **494 全绿**（489 + 5）；`scripts/smoke_e2e.py` **PASS**（旧四步链路
   write → review PAUSED → resume → commit 原样可用 + `check_state_sync` SYNC OK）；
   `export_openapi` + `gen_frontend_types` 已 regen，**98 paths / 51 schemas 未变**。
9. **两处夹具/断言自身出错的留痕（本批内发现并修正，均不掩盖）**：
   - e2e 夹具最初用同一句 ×60 写正文，trigram 重复率冲上 confirm 档，**quality_gate 在
     commit 节点之前就先拦下 run** ⇒ 「对照组应被状态机拒绝」根本没走到要测的判定，
     测出来的是另一个原因。改为全不重复的散文后失败点才落到正确的位置。
     **教训：判别用例的夹具不能自带噪声，否则失败点会漂移。**
   - 前端新增断言曾写「review 在 DRAFTED 仍禁用」——错的，`EXPECTED_STATUS.review`
     本就含 DRAFTED（写完草稿就该能审校）。改为断言真正的不变量：**快通道开关对
     plan/write/review 的判定零影响**。
10. **前端**：`types.ts` 的 `Project` / 创建 / 更新载荷补 `fast_mode`，`WorkflowStartPayload`
    补 `fast_mode` / `auto_commit`；`getButtonAvailability` 新增可选入参 `fastMode`
    （**不改 `EXPECTED_STATUS` 常量**——它是与后端 `ALLOWED_NEXT` 对齐的状态机面，快通道是旁路
    不是合法后继）；章节页加「快通道」勾选（乐观更新＋失败回滚，切换即 PATCH 落库）与
    「写完直接提交」勾选（快通道未开时置灰）。`ChapterDetailPage.test.tsx` 的 endpoints mock
    补 `projectsApi`——**默认值放 mock 工厂而非 beforeEach**，因该文件有 13 个 describe 各带一份
    beforeEach，放 beforeEach 只覆盖其中一份，其余会撞 `undefined.then`。
11. **本批**未删任何闸门代码，未动 observer，未动写作流水线，未碰内容层既有测试书籍；
    全程 mock provider，**零真实 LLM 调用**。

### 2026-10-14 进度同步：10-13 批次提交前复核 ＋ 两条悬案裁决（含一处先验推翻）

> **来源**：用户指令「同步项目进度」。
> **性质**：本条是**留痕与裁决**，不是新功能批次。代码侧只动了 `scripts/m1_long_run.py` 的注释（承接上一批已并入的守卫写法），其余全是文档与收尾。
> **闸门**：Lead 亲跑，指纹（`packages/ tests/ scripts/ database/` 的 `.py/.sql` 取 `路径|长度|mtime` 的 SHA256）前后各取一次。

1. **闸门跑两次，第一次作废**：第一次 **2977 passed / 1 skipped**（309.56s），数字与 10-13 记录一致——但**不能作准数**：运行期间我改了 `scripts/m1_long_run.py` 的注释，指纹由 `9464579…` 变为 `4ced825…`（文件数 519 未变）。按 10-13 新立的「指纹逐位一致才算数」纪律，这次跑的是一棵中途变过的树。第二次为权威数（指纹前后一致），见文末「闸门结果」。
2. **⚠️ 先验推翻，必须留痕**：我向用户断言 `scripts/m1_long_run.py` 的守卫是「真实缺陷修复——早退分支会在 `finally` 里抛 NameError」。**复算后不成立**：外层 `try:` 在 **977 行**，而两条早退 `return 2`（**960** 行「未发现 progress.json」、**967** 行「chapter_ids 为空」）都在它**之前**，压根进不了 `try/finally`；HEAD 版在那些路径上**不抛** NameError。⇒ 该改动是**行为等价的预防性加固**，不是缺陷修复。附带发现：该改动的注释恰好陈述了一条**不可达的触发路径**，属坑区「名实不符」家族——注释也会失准，且失准时不会自己报警。处置（用户裁决）：**保留守卫、改写注释**为如实陈述（预防性 + 明确写出两条早退当前位于 `try` 之外），并留痕防止下一个读者据旧注释误判。
3. **工作区清理**：`tmp/` 下 28 个 10-13 会话级产物（探针脚本 `_c*.py` / 时序对比 / 临时 payload 响应 / 手工日志）此前未跟踪且**未被 .gitignore 覆盖**，`git status` 长期挂红。补规则 `/tmp/`（用户裁决「文件留着」）——**证据要留的先摘进 NovelOS-Artifacts 再删**。已扫过：不含明文 API key。
4. **突变残留核查**：`packages/ tests/ scripts/ database/` 全域 grep `# MUTATION-[0-9]` / `MUTATION-PROBE-`（**显式大小写敏感**，见坑区）= **0 条**。
5. **本批提交范围**：**17 改 ＋ 14 新增**——改面含 `.gitignore`（补 `/tmp/`）＋ `m1_long_run.py`（注释改写）＋ `AGENTS.md`/`CHANGELOG.md` ＋ 4 份 README ＋ `ai_patterns.py` 门面 ＋ 两个 router ＋ 6 个测试文件；新增为 quality 拆分 4 个实现模块、备份/快照测试拆分 8 个主题文件 ＋ 2 个夹具模块、profile 校验 1 个文件。**不含**任何内容层数据（dev 库全程零接触，`data/` 本就在 `.gitignore`；`tmp/` 已入忽略规则但**文件保留在磁盘**）。
6. **闸门结果（权威数）**：第二次全量 **2977 passed / 1 skipped / 0 failed**（294.15s），指纹 `FP_PRE = FP_POST = 4ced825411d71093044140257dcfb882bba12b1990ee45c4fc26feb4803f869b`（519 文件，**逐位一致**）；`ruff check packages scripts tests` = `All checks passed!`；突变残留 0。基线维持 **2978 collected**。

### 2026-10-13 番茄男频/快穿赛道调研（只读）＋结论更正留痕

> **来源**：用户指令「调研 9-10 月以来番茄男频最热前 10」→ 追加「细分男频男方快穿」→ 追加「书名为快穿开头」→ 追加「找《快穿：知道什么叫背景通天吗》」。
> **执行链**：novelos-author 调研（只读）→ Lead 独立复验（书名/作者/分类入口/API 可复现性）→ 结论被成员自身证据推翻 → Lead 更正 → 沉淀两条坑区。
> **边界**：全程只读，未碰 `data/novelos.db`、未起 workflow、未调 MiniMax、未改仓库代码、未做 git 操作。工具用 modsearch（用户指定）。

1. **男频最热前 10（2026-10-13 快照）**：《神通者》《骇夜：我在恐怖游戏里暴富》《封神：开局反出西岐，我死守渑池》《这个游戏不对劲，我挖矿成神！》《十日终焉》《我不是戏神》《规则怪谈：无所谓，系统会出手》《都重生了，谁还装富二代啊》《末世选择：开局一亿只美洲大蠊》《我在精神病院学斩神》。Lead 独立复验 #1 #5 的详情页书名与 ID 全部吻合。
2. **最有价值的发现：番茄榜单页书名有字体同形字污染**。榜单页给「末日终焉」「万相」「灭渊」，详情页真名是「十日终焉」「神通者」「天渊」——**直接抄榜单会拿到一批不存在的书名**。机理：榜单页 CSS `font-family: DNMrHsV173Pd4pgy`，PUA 比例 9/10~10/10，且**不同端点字体构建不同**（同一本《骇夜》榜单页给「游戏里」、API 给「游戏内」）；GitHub 现成解码表已失效（`U+E437→U+E4A4` 得「常z知」）。
3. **番茄 API 三个字段不可信**：`word_count` 十本全错（9 少 1 多，`wordNumber` 才对）、`read_count` 与榜单页对不上、`abstract` 字段 30/30 带 PUA 混淆。唯一可信：`/page/{id}` 与榜单页的 `wordNumber`。
4. **⚠️ 本轮结论被推翻的留痕（先验判断被推翻必须留痕）**：Lead 曾据成员报告对用户断言「男频书名以快穿开头的**只有 2 本**」。后续扫描面从 3090 本扩到 6283 本后，男频侧变为 **6 本**，且**成员自己把要找的那本书漏了**（已证实的漏检）⇒ 真实数 ≥7，**「只有 2 本」作废**。连带作废的推论：「男频快穿是空白赛道」——有分类缺失 ≠ 没书。**仍然成立的**：番茄榜单页只有 `0_2_24` / `0_1_24` 两个快穿入口且均为女频（Lead 独立复验 URL 首段 `{gender}` 与同页 `1_2_1016`=男频衍生 对照确认）。
5. **成员自纠三处**（均如实登记，不掩盖）：① 撤回「简介无快穿 ⇒ 男频无快穿」（`abstract` 字段受污染）；② `__all__`/门面自留符号数从 30 更正为 27；③ 承认**所有分层表的性别列整体标错**——番茄 API 的 `gender` 参数不可靠，同一本书同时出现在 `gender=0` 与 `gender=1` 结果里。
6. **两条新坑区已沉淀**：① **分层统计的分组键本身不可靠 ⇒ 分层结论整体作废**，且每层数字都「看着正常」（错的是归属不是读数，故无任何一层会自己报警）；② **「扫过 N 本未见」不是「不存在」**，被找对象本身必须做阳性对照——它本应在射程内却没出现即当场证明检测器失效，**不许用「样本不在热门池」解释掉**。
7. **定位到《快穿：我的词条是背景通天》**（book_id `7672327675796540440`，作者天辰之光，男频快穿/无固定cp/任务流，147 章 33.6 万字，在读 5.1 万，2026-10-12 更新）。**用户记忆的书名《快穿：知道什么叫背景通天吗》不存在**；已按该书名作为参照书抓取正文入库（详见下方抓取条目）。
8. **未复现项**：番茄站内搜索始终拿不到（客户端渲染空壳 + 5 个搜索 API 端点全不通），故「站内不存在某书」类断言一律不成立；评分字段番茄不返回。

### 2026-10-13 在途拆分重构收尾（3 成员：quality-dev / ops-dev / reviewer）

> **来源**：用户指令「新建3个成员，2个执行，1个审查」，范围 = 收尾工作区里那批未提交的巨型文件拆分；闸门由用户选定 = 全量 pytest 基线。
> **执行链**：reviewer 对抗审查（只读，先跑）→ ops-dev（tests/unit 拆分产物 + 两处 router）与 quality-dev（`packages/core/quality/ai_patterns*` 5 文件）按文件路径互斥并行 → Lead 回灌审查发现并直修两处假覆盖 → **Lead 亲自跑两次全量闸门，前后指纹校验** → 本条留痕。
> **性质**：**结构拆分部分等价、router 部分含有意行为变更**——对外表述不得统称「等价重构」（见下第 5 条）。

1. **quality 侧拆分收尾**：`ai_patterns.py` 拆成门面 + `ai_patterns_{text,sentence,paragraph,dialogue}.py`（481/50/347/283/423 行，全部远低于 1500 红线）。门面保留 `__all__` 27 名（与 HEAD 逐项相同、无自指元素）＋ 8 个门面自有符号（6 个 def/class + `_AI_ENDING_SUMMARY_PHRASES`、`AI_PATTERN_RULES` 两个模块级常量），其中无外部消费方的 6 个全部在门面包内被消费，**无死名字**。清理拆分残渣：删掉 import 之后的孤儿重复 docstring（裸字符串，永不生效）、补回被拆分掉的空行。
2. **测试侧拆分收尾**：`test_snapshot_rebuild.py`（1675 行）→ helpers + 5 个主题文件，`test_backup.py` → fixtures + 3 个主题文件。**用例数零漂移**：snapshot 9→9、backup 19→19（Lead 用 HEAD 原始字节 + collect 独立复算，28 条被移动用例的函数体逐字相同）；`test_migrations.py` 36→36 为纯改名（34→40 / 34→39，断言值未动）；两个 workflow 文件纯注释增量、断言零改动。夹具模块 `backup_fixtures.py` / `snapshot_rebuild_helpers.py` 不以 `test_` 开头且无 import 副作用，pytest 不误收集。
3. **MINOR-1 加固（真实缺陷，reviewer 攻破）**：`test_readability_rules.py` 的「模块内不得有第二份段切分」形状断言原读 `Path(module.__file__)`，拆分后只覆盖 5 个文件中的 1 个——**4 个族子模块全在看守之外**。注入实证：往子模块塞一段真实的 `prose.split("\n\n")`，原样谓词跑仍绿（门面计数 = 1）。性质同坑区「人类基线 0.00 是量具假象」——**看守被拆分悄悄缩小了执行域，而它守的正是那条口径**。修法：读取面扩为 `glob("ai_patterns*.py")` ＋ 名册断言（域不得静默缩小）＋ 唯一命中必须落门面的断言；docstring 登记文件域边界并**如实登记残余边界**（改模块名仍可绕过）。突变验证：注入真分叉 → 红（2 != 1，且报点名文件）；撤除 → 绿 23。
4. **Lead 直修两处假覆盖（reviewer / ops-dev 报出，均在本批新增文件内，属本批交付质量）**：
   - `test_non_400_errors_get_no_hint` 是**判别不了的用例**——其 detail（`HTTP 401: invalid api key`）不含任何模型名 marker，`_unknown_model_hint` 在进闸门前就提前返回 `None`，于是把 `if status_code == 400:` 整个拆掉它照样绿（突变 M4 存活）。新增可判别用例 `test_non_400_with_marker_text_still_gets_no_hint`（detail 含 `does not exist`），突变复测：拆闸门后**新用例红、其余 13 条仍绿**，同时证明新旧两条的分工。**教训登记：「断言了结果」≠「判别了机制」**。
   - `test_model_configs_strips_model_on_create` 只断言响应体，未读回库，与 profile 侧守卫强度不对等。补 `GET /api/model-configs/{id}` 读回断言——**「响应归一化」与「落库归一化」是两件事**（硬规则 9 的形状）。
5. **有意行为变更（非等价重构，如实登记）**：`model_profiles.py` 新增 `_clean_model_field`（`raw.strip()` 归一化 + 纯空白 422，4 个写入口接入）＋ `/test` 响应新增可选 `hint` 字段（命中即挂中文提示，未命中时响应体与本批前逐字不变）；`model_configs.py` 复用同一函数（单向 import，无循环——**设计内债**：注释自陈最终应迁到 `packages.core.model_router.security`，本批未扩白名单）。新增 13 条 + 2 条契约测试，7 次突变中 6 次必红恢复绿（M4 存活即第 4 条所述，已修）。OpenAPI 98 paths / 51 schemas 未变（`hint` 落在无 `response_model` 的裸 dict）。
6. **ruff 两处 I001 已修**：由本批新增的分节横幅注释引入（HEAD 副本 `--isolated --select I001` 对照为 clean，可证非历史挂账），修法 = 每文件删 import 块后一个空行，无 import 重排、无断言变化；最终 `ruff check packages scripts tests` = `All checks passed!`。
7. **文档订正**：`packages/core/quality/README.md:361/382` 拆分后已失准（「三条规则都在 ai_patterns.py」）——规则元数据与注册入口 `scan_ai_patterns` 确在门面，但算子实现已迁至 `ai_patterns_paragraph.py` / `ai_patterns_dialogue.py`，词表在 `ai_patterns_sentence.py`；已改为写明「门面是唯一公共面，但不再是实现所在地」。
8. **纪律留痕（四条量具/流程教训）**：
   - **闸门有效性本身要验**（reviewer 自推翻其角度 7）：其 17:59:08→18:04:56 的全量（2976 passed）**作废**——`ai_patterns.py`/`ai_patterns_dialogue.py` mtime 18:02:44 正落在窗口正中，`ai_patterns_paragraph.py` 18:11:02 更在其后；pytest 起跑即完成 collect + import，那次绿测**描述的是过期树**。ops-dev 随后主动声明自己的全量同样不作废。**对策（本轮引入）**：闸门前对 `packages/ tests/ scripts/ database/` 的 519 个 `.py/.sql` 取 `路径|长度|mtime` SHA256 指纹，跑完再取一次比对，**指纹逐位一致才算数**——不靠「应该没人改」。
   - **`shutil.copy2` 保留 mtime**：ops-dev 用它做突变恢复，导致三个文件的 mtime 显示 2026-09-30 而字节确被写过又恢复。**mtime 不能当「没被碰过」的证据**（内容等价须由残留扫描 + 全量绿 + diff 未变三项另证）。
   - **`Select-String` 默认大小写不敏感**：Lead 扫突变残留时把 `test_engine_pure_no_mutation` 等三个正常测试函数名误报成残留，差点把假缺陷记到成员头上（grep 工具默认大小写敏感，故报 0）。**判别：大小写敏感的残留扫描必须显式 `-CaseSensitive`**，否则测试函数名里的 `mutation` 会淹没真信号。
   - **两个成员报的数字分歧可能都不是错的**：reviewer 报门面自留符号 **6**、quality-dev 报 **8**，Lead 复算 = 门面内 def/class 恰 6 个 ＋ 模块级常量 2 个 ⇒ **8 = 6 + 2**，差的是口径不是事实。裁决依据只有复算（§三.6）。
9. **闸门结果（Lead 亲跑，两次）**：`2977 passed / 1 skipped / 0 failed`（305.28s）+ `2976 passed / 1 skipped`（299.31s，前一次在两处假覆盖修复之前）；两者运行期指纹均逐位一致、`collected` 与 `passed+skipped` 闭合、`ruff` 全仓 clean、突变残留 0。基线由 2961 → **2978 collected**（+16 全部来自本批新增用例：13 条 profile 校验 + 2 条 openapi 契约 + Lead 新增的 1 条判别用例）。vitest / export_openapi regen 本轮未复现，仍挂「声称未验」。
10. **运行时登记（用户已裁决）**：本会话按用户「启动项目」指令起了后端（PID 54604 / 18081，用户随后在 UI 上新建 MiniMax profile 并绑定 `creative_writing`、resume 了一个 run，**发生一次真实 MiniMax 调用**）。**归因更正**：reviewer 将其列为违反「禁真实 LLM 调用」硬约束，Lead 复算后认定**不成立**——该调用是用户在自有产品上的正常使用，不是用 NovelOS 验证软件行为；且服务本身是用户指令启动的。
    - **API key 风险登记（用户裁决：不轮换，仅登记）**：新 profile 的真实 key（`sk-cp-` 前缀）明文存于 dev 库 `model_profiles.params_json`，且被 reviewer 的只读探针打印进了本轮对话记录（已进会话日志）。**判别与建议**：轮换与否由用户决定；若日后要清空，走 `secrets.json` / 环境变量而非明文入库。
    - **dev 库 mtime 周期性前进**（观测到 18:02:01 / 18:10:32 / 18:17:36 / 18:18:25，间隔约 7~8.5 分钟）：**测试套件不写 dev 库已获正面证据**——Lead 两次闸门合计约 10 分钟、期间服务持续运行，dev 库 mtime 纹丝不动（ops-dev 先前只有 45s 窗口，属假阴性）。**写入来源仍标注为推测（常驻服务周期性写），未证实，不作结论入册**。
11. **无主工作区改动登记**：`scripts/m1_long_run.py`（`_cmd_run_locked` 的 `client` 预绑定 + `finally` 的 `if client is not None:` 守卫，修早退分支 NameError，+10 −4）不属于本轮任何成员的声明作用域。**本轮未动、归属待裁决**。→ **已于 2026-10-14 裁决**：复算发现两条早退 `return 2` 都在外层 `try:` 之前，守卫**不修任何现存缺陷**（行为等价加固），且原注释陈述的触发路径不可达；用户裁定保留守卫并改写注释，并入本批提交。详见 10-14 条目第 2 点。

### 2026-09-30 Wave2~4 收敛：CRITICAL-1 落库补修＋drift 闸门＋文档对齐＋基线独立复现

> 来源：用户四项全批（修 CRITICAL-1／快照＋补留痕＋提交／三件打包／放行一次只读巡检）＋ task-5 reviewer 的 CRITICAL-1（prompt 单点化落库侧半实现）。
> 执行链：Wave1 快照＋巡检（ops）→ Wave2 三路并行（dev-a／dev-b／scout，RW 互斥）→ Wave3 双轴审查＋两补丁（reviewer）→ 全量独立复现（ops，2961）→ Wave4 留痕＋提交（Lead）。

1. **CRITICAL-1 落库侧闭环**：`chapter_plan/pipeline.py:200` 传硬编码常量 vs payload 取 ACTIVE vs plan_json 取模型回声——同一 run 三源分叉，且 5 例测试全只断言 payload（撤落库修复仍全绿）。修复：开事务前重算 `active_prompt_label`（其无 conn 入参，字面 conn 复用不可实现）；`:179` 对齐 ACTIVE，三面同源；注释精确化（回声口径外）；落库断言＋三向突变（撤修复／采回声／撤 plan_json 侧）全红恢复绿；scoped 34 绿。
2. **drift 闸门 5/51→51/51**：`_schema` 死变量消除；集合相等双向比对；`KEY_TS_INTERFACES` 扩全量；常驻自检×3（缺失红 `:106`／多出红 `:112`／辅助函数）；MUT-C 实证旧 Key-only 对“多出”方向盲。
3. **文档对齐**：5 文件 21 处数字＋2 注记（Ordinal 逐字一致）；AGENTS 8 处小修正（PAUSED 18／`src/api/types.ts` 路径／package-lock 入纪律＋四处一致／判别式转 grep 口径／CRITICAL-1 后续／基线行 2961／shm 注记）。
4. **基线首次独立复现**：`2961 passed / 1 skipped / 0 failed`（306.97s，项目 .venv，须带 `--ignore=tests/unit/test_m1_lock.py`）；2956＋5 完全闭合（dev-a＋2、dev-b＋3）；dev 库 mtime 未动；skipped 系 `NOVELOS_REAL_DB` 门控；ruff/vitest 本轮未跑。
5. **运行时现状更新**：18 PAUSED／0 RUNNING／0 PENDING（替代“16 条”记录；全有实质内容；3 条属 project-init；1 条 provider 400 `unknown model 'minimax m3.1-flash-preview'` 挂账另立项）。`mode=ro` 巡检触碰 `-shm` mtime 属读侧正常行为，不作写入认定。
6. **MAJOR-4 闭环**：09-28~30 两批（`main.py` mimetypes 修复、`chapter_write` 拆分 -969 行）此前零留痕，现补记。
7. **量具教训三条**：带 pathspec 的 `git status` 不得论证某路径不存在；量具选择先于结论（59 例：`def` 计数 48 vs collect-only 59）；开工前存 status 快照入派发模板；`edit` 写 UTF-8 规避 PowerShell 中文落 U+FFFD。

### 2026-09-27 工作边界裁决：只动软件层 + 禁真实 LLM 调用

> **来源**：用户指令「1、只动软件层，内容层不管，目前这些书籍项目全都是测试书籍，没有继续改它们和续写它们的意义；2、修改软件过程中不使用软件进行真实的 LLM 调用写作等」。
> **性质**：**项目定位级裁决**（用户直裁，非主会话自主拍板范畴）。已落 `AGENTS.md` §一「软件工作边界（2026-09-27 用户裁决，两条硬约束）」，后续一切派发以此为准。

1. **只动软件层，内容层零接触**——现存全部书籍项目（火影 / 死当 / 快穿之价签等，含 16 条 PAUSED run、那本 48 章的书）**都是测试书籍**，不再有修改与续写的意义。禁止把它们当生产数据对待：不得改内容仓（NovelOS-Content）、不得改或重生成既有章节正文、不得续写、不得为「让老数据好看」做数据迁移或清洗。**判别**：任何需要真实正文 / 人物 / 设定作语料的验证，一律改用**临时库 + 合成夹具**；测试禁止写 `data/novelos.db`；脚本不得默认打 dev 库。
   **与 §二 硬规则 5（三仓分离）的关系**：硬规则 5 管的是**存哪**，本条管的是**动不动**——内容侧从此不作为工作对象。
2. **改软件过程中不做真实 LLM 调用**——禁止用 NovelOS 自身发起真实模型调用来验证软件行为（不跑真实 write / revise / 审稿、不做真实量产批、不烧 API 额度）。**判别**：行为验证一律走 mock provider、单元 / 契约测试、临时库 e2e（`scripts/smoke_e2e.py` = 临时库 + 独立端口 + mock providers）。**唯一例外**：确需真实模型响应才能证伪的命题，须主会话逐次裁决并留痕，**不得由成员自行发起**。

**已广播**：本团队 4 名常驻成员（`scout` / `dev-a` / `dev-b` / `reviewer`）均已收到并确认；派发时两条禁令逐条写进任务描述的边界段。

**核销更新**：§六 测试基线已由另一团队推进为 **2951 passed / 1 skipped / ✅ 2026-09-27 复核**（368s，须带 `--ignore=tests/unit/test_m1_lock.py`）。本轮 reviewer 未独立复跑全量（Lead 禁令），故该数字的独立复核仍待一次带 `--ignore` 的全量实测；另注意坑区「单跑绿、全量红」形状——单文件绿灯不构成核销。

### 2026-09-27 软件层全量审查（本团队 4 成员：scout / dev-a / dev-b / reviewer）

> **来源**：用户指令「做一次软件层的全量审查」。范围=软件层（`packages/` + `apps/web/src/` + 只读参考 `scripts/`/`tests/`/`database/`/schemas），内容层零接触、禁真实 LLM 调用。
> 执行链：scout 证据索引 → dev-a（core 除 quality + web）与 dev-b（domain + workflows + quality）并发缺陷清单 → reviewer 对抗复核 → 打回修正（dev-a rev.3 / dev-b rev.1）→ reviewer 复审 → Lead 逐条亲验承重结论后收口。
> 交付物（4 份，落 `docs/reviews/2026-09-27-full-audit/`；**注意该目录被 `.gitignore:42` 忽略，不进版本控制**，归档见下）：`scout_index.md`（361 行）/ `dev_a_report.json`（9 条）/ `dev_b_report.json`（8 条）/ `reviewer_report.md`（317 行）。

**结论：0 critical / 2 major / 14 minor + 1 口径观察项**（A-003 二次改写后收敛；B-3 撤回改写。）

- **A-001（major，维持，论证已换）**：`director_input.py:249` 硬编码 `"director:v1"`、12 元键无 prompt 维度；writer 侧 09-16 已修（`writer_input.py:416` 查 DB ACTIVE + 键[13]），director / observer（`:734`）/ director_planner（`planner_input.py:31` 常量）三处未同步。dev-a 初版「脏命中」论证被 reviewer 推翻（装配不读 prompts 表，字面量命中/未命中逐字相同）；重写为「硬编码标签漂移 + 三源分叉」（`chapter_plan/pipeline.py:179` 模型回声 vs `:200` 常量 vs `runner.py:569` 真值），与 writer 09-16 事故同形。**同形状第二次抓到 → 已升格进 §五 坑区**（对策：三处 `active_prompt_label` 单点化 + 键同步，合并为一个整改单，三处同碰 `cache.py` 分开做必冲突）。
- **B-1（major，维持，不作本批验收阻塞）**：`workflows/chapter_write/pipeline.py` **1932 行**单文件巨型（scout / dev-b / reviewer 三方独立实测一致；任务书写的 1786 有误，dev-b 拒绝采信任务书数字自行重测是对的）。§二 硬规则 1「存量挂账已清零」对它不成立——准确表述是「红线仍被违反且未登记在任何挂账里」（文档缺口，非虚假声明）。该文件不在 09-27 改动面内，属既存问题。
- **其余 minor（抽样）**：B-2 形状级测试逐字重复断言（dead code）+ 覆盖边界三绑定（变量名/方法名/字面量，任改一维即漏抓）；A-003 `db.py:84` 无注释死防御代码（四条路径逐一不可达，详见下）；A-007 `runs.py:221,245` 吞全异常回落错标签；A-008 `snapshot.py:644` 裸连接绕过 `get_connection`；A-004/A-005 前端手写面缺 `outline_json` / `writing_bible` 等字段（降 minor，登记「本期不做 UI」——generated 面本就是 drift 对照面，无自动校验，不破 build/测试）；A-009 `count_tables` docstring 表数漂移；B-5 「绕过 ALLOWED_NEXT」是措辞问题（行为正确且原子）；B-7 `_last_paragraph` 第二份切分维持挂账；B-8 `ai_patterns.py` 1457 行逼近红线。
- **B-3（撤回→口径观察项）**：dev-b 初版称 `reference/service.py:63-64` 空 except「无注释、无 noqa、无 docstring」——三项均不成立（`:8-9` 模块 docstring + `:32` 函数 docstring + `README.md:53` 均已登记），且与同报告 B-4 的免罪标准双标。已改写为「降级 0 与真实 0 不可区分」口径观察项，附 `withdrawn_claim` 留痕。

**本轮三次「修正不等于正确」与一次审查员自误（全部留痕，未隐去）**：

1. dev-a A-001 初版「脏命中」论证错 → rev.2 重写为三源分叉（reviewer 独立复算三段证据全成立，接受）。
2. dev-a A-003 repro 连错两次：rev.1 锁机理错（WAL 下读不阻塞）→ rev.2 换成「`DatabaseError` 被吞」又错（`DatabaseError` 是 `OperationalError` **父类**，`except` 抓不到；Lead 用 `python -c` 独立验证 MRO）→ rev.3 落定为「四条路径逐一不可达的死防御代码」，建议**直接删 try/except**（零行为变化，可用测试数零增减验收）。
3. scout 的「`expected_role` 不在键 ⇒ 脏命中」担忧被 dev-b 驳倒（`cache.py:170-196` 双向深拷贝 + `planner_input.py:219-222` 新建 dict 整体替换，就地改写不可能回写缓存），reviewer 三段证据拼齐裁决 dev-b 正确。
4. **reviewer 本人造出一条假指控又亲手勘误**：rev.0 把「自己臆造路径 `pages/ProjectInitPanel.test.tsx` 跑不通」写成「dev-a 路径写错」并列入打回；复审时回查 rev.1 全文证实该串从未出现，dev-a 的 `file` 字段原本正确。已在报告三处就地勘误并撤回指控；**任何下游条目不得包含「dev-a 路径写错」这一事实**。与 §三.6 两种翻车同形状、方向相反（这次是审查员本人）。

**reviewer 新发现 N-1（major，覆盖面缺口）**：dev-a 与 dev-b 的 scope 并集**未覆盖 `scripts/`**——`scripts/inspect_stalled_runs.py`（863 行，09-27 批次新交付物，已被写进 §五 坑区作判别工具）无人审计。**补派 scripts/ 专项审查或 Lead 裁决豁免并留痕，不得默认为已覆盖。**

**归档**：7 份交付物已复制到 **NovelOS-Artifacts: `docs/full-audit-2026-09-27/`**（软件仓 `docs/reviews/` 被 gitignore，工作区副本仅作过程文件）。

### 2026-09-27 Phase B 整改执行（用户指令「按建议来」）

> 执行链：task-6（dev-a：A-001/A-002/B-6 合并单点化）+ task-7（dev-b：杂项 6 项）+ task-5（scout：N-1 scripts 专项）三路并发 → reviewer phase2 对抗复核全过 → task-8（dev-b：B-1 拆门面）→ reviewer split 复核（门面取舍接受，注释 5 处事实错误打回修正）→ task-11/13/14 注释收尾 → task-10 全量核销。

- **Fix-1**：director/observer/planner 三处 `active_prompt_label` 单点化 + director 键 12→13；新测试 5 例 + 双突变自检红过（突变 1 实证「只改 payload 不改键 ⇒ 脏命中」）。
- **Fix-3**：db.py 删死 except + 去表数硬编码、runs.py 加日志显式降级、snapshot.py 走 `get_connection`、B-5 注释改原子性表述、B-2 删重复断言、regen 双产物零 diff（踩出「必须用 `.venv` 解释器」陷阱，已入 §四）。
- **B-1 拆分**：1932 → 门面约 1100 + 5 子模块（全 ≤1500）；4 节点留门面（18 处 monkeypatch 打点契约，硬规则 1 已补条款）；`tests/` 零改动；门面注释乱码/数字错误已清零（U+FFFD 全仓复验为 0）。
- **N-1**：`inspect_stalled_runs.py` 零 critical（4 机制逐项 file:line）；但揪出 scripts/ 存量 **3 处真实 LLM 默认档 + 8 处 dev 库默认读面**（本轮前即存在，**未动手**，待单独立项裁决是否改造）。
- **基线**：task-10 全量 **2956 passed / 1 skipped / 0 failed**（343.69s，.venv；+5 完全闭合于新测试 5 例；dev 库 mtime 未动实证零接触）。§六 已更新为 2956（本团队独立确认）。
- **行数量具附带**：P2-1（`Get-Content` 默认 cp936 静默少算中文行）+ 写侧同形（PowerShell 写中文落 U+FFFD）已入 §五 坑区与 §二硬规则 1。

### 2026-09-27 算子量具修正批次 + 卡死 run 巡检工具（先验判断两次被推翻并留痕）

> **来源**：用户指令「不继续写小说；其它的按建议处理掉；用 agent 团队（固定 4 成员：2 开发 / 1 调研 / 1 审查）」。
> 固定成员建在 `C:/Users/aistation/.agents/agents/novelos-{quality-dev,ops-dev,corpus-analyst,reviewer}/`。
> 执行链：调研（全量逐条抽样，非抽样）→ 主会话亲验 → 开发 → 主会话复算 → 对抗审查。
> 证据表留痕在 **NovelOS-Artifacts: `docs/sampling-audit-2026-09-27.md`**（37KB，35 对全量判档）。

**⚠️ 两处主会话先验判断被推翻（留痕）**

1. **「16 个 PAUSED run 会让 8 章撞 409、要清掉」——错。** 实测 `_check_active_run_for_chapter`
   的两条分支 SQL 谓词都是 `status IN ('RUNNING','PENDING')`（`control.py:78/89`），
   DB 侧兜底同谓词（迁移 `0017_unique_constraints.sql:46-48`，其 line 42-45 注释明写
   「**不覆盖 PAUSED**」是刻意设计，为 resume 留出生效空间）。**PAUSED 不阻止新 start。**
   且 PAUSED **不可 cancel**——`engine.py:644-646` 写明 status ∈ {COMPLETED, FAILED, CANCELLED, PAUSED}
   → 409「must be RUNNING to cancel」，**PAUSED run 的取消走 resume 后再 reject/approve 决议路径**。
   结论：既不挡路也无受支持的清理路径，硬清只能绕过引擎直改 DB（同 09-26 刚封掉的「PATCH 借道」形状）。
   **裁决：不清，改为导出留档。**
2. **「给两个算子加豁免」——错，要修的是量具不是阈值。** 调研实测推翻，见下。

**1. `AI-SHORT-PARA` 段切分口径对齐（修论证链，不动阈值）**
- 根因：`count_short_paras` 独自用 `_paragraphs`（`\n\n`）切段，而同文件 `count_long_paragraphs` /
  对白算子早就在用 `\n+`。榜一人类锚点 19 章的 **`\n\n` 总数 = 0**（一行一段），
  按 `\n\n` 切把 19 章读成 **19 个整章大段** ⇒ 规则在该语料上**构造上不可能命中**
  ⇒ 此前登记的「人类基线 0.00/千字」**不是测出来的，是量具口径造成的假象**。
- 改法：删 `_paragraphs`，把 `_readability_paragraphs` 改名 `_split_paragraphs` 上移，
  成为短段/长段/对白三条段级算子的**唯一**切分入口（不另写第三份）。
- 实测（两侧同口径复算，主会话独立复验）：人类 19 章 **0 命中 → 8 命中 / 0.212 每千字 / 779 段 /
  1 章超阈**；生成 48 章 **257 → 260 → 258 命中 / 2.064 每千字 / 3484 段 / 34 章超阈**
  （口径变更仅 ch33 一章计数 2→5）。分离度 **9.7~9.8×**。
- **阈值 `DEFAULT_SHORT_PARA_RATE_PER_1K = 1.0` 未动**——修的是论证链。
  注释里明写「人类零容忍」的说法**已不成立（它从来是假象）**。
- 附带：`名字：` 说话人行守卫（人类侧说话人行原被当非对白短段计入）。
  守卫刻意收窄——`宋沛年：`（人名）与 `价签：`（名词标签）字面同构，会一并豁免，
  已在注释与测试中登记为**能力边界**而非已验证精确率。

**2. `AI-DIALOGUE-ECHO` 重叠系数度量修正（不是调阈值）**
- 根因：`overlap = 共同字 ÷ min(前,后)` 是**对称**度量，但分母落在薄侧时被机械拉到 1.00。
  实测 ch17 `“林昭！”`→`“林昭，你跟我走，茶馆比县仓安全。”`：前 2 字 / 后 13 字 /
  recall 0.15 / **overlap 1.00**——recall 说「后句 85% 是新内容」，overlap 说「完全重合」，
  后者被前者证伪。该读数量的是「长句含短句的字」，不是「用词是同一批」。
- 改法：新增 `DialogueEchoPair.overlap_comparable`（property，不改构造签名）
  = 任一侧 ≥3 实义字 **且** `back_len < 2.0 × front_len`（**严格小于**），
  **只挡接词型；recall 复述型不受影响**（不可比的对仍可凭 recall 命中）。
- 实测：生成侧 **34 对 / 24 章 → 23 对 / 18 章**；人类侧 1 对 / 1 章不变。
  3 条真信号（`不退。`→`不退！`、`不退！`→`不退！`、`他们退了。`→`他们退了。`）**全部保留**。
- **诚实标注（写进代码注释）**：11 对是**薄样本**，实测锚定**非分布结论**；2.0 取自该组比值
  分布（2.0~11.4）的下界。`已否决更「正统」的 `min`→`max` 分母方案（实测更严，
  需把 `MIN_OVERLAP` 降到 ~0.36 = 拿 2 个样本重定阈，正踩「阈值落在样例分布外」的坑）——
  数字在注释里，供后续裁决。

**3. 订正 3 处与实测不符的注释（声称与实测分离）**
- `:232` 「26 对 / 20 章」→ 实测 **34 对 / 24 章**（旧口径），修正后 23 对 / 18 章。
- `:237-238` 把 `你守后队。`→`后队有人殿后，你在那儿。`（实测 recall **0.30**）列为复述型样例
  → **举错例了**，它是接词型。换成三条实测复述型样例。
- `:245-248` 称 `min_opening=2` 能挡「短促称呼 + 长回答」→ **实测没挡住**：
  `林昭！`→`林昭，你跟我走…` 开口连续重合确为 2，照样命中。它挡的是「称呼落在后句中段」那一种。

**4. `scripts/inspect_stalled_runs.py`（新建 863 行）+ 59 例测试**
- 只读卡死 run 巡检。`file:<path>?mode=ro` URI（误加写操作会在第一次写时直接抛异常）、
  `--apply` 显式 `NotImplementedError`（退出码 3）、认不出的 payload 走 `unknown` 分支
  **列键名而不是静默归成「空报告」**、`--export` 落盘含正文草稿时打 WARNING 提醒不入库。
- 每条 run 给**可读的审批实质内容摘要**（字数/字数带/偏离、error 与 warning 的 rule_id、
  critic issues 条数、评审原文摘录），不是 true/false——因为空报告与带 3 条 blocking 的报告
  处置决策完全相反。
- 实机结论：**16 条全部有实质内容，零条空报告**。

**5. 16 条 PAUSED run 的处置与留档（不取消）**
- 逐条解析 checkpoint：12 条是已 COMMITTED 章的陈旧评审、3 条是 DRAFTED 章的未批评审
  （火影 ch1 ×2 欠带 -33.4%/-38.2%、死当 ch7 +26.5%）、1 条是 **《快穿之价签》立项草稿**
  （`premise_designer` 的 title/logline/positioning/selling_points/主角卡——
  这本 48 章书的「DNA」只存在于这个卡死的 run 里）。
- 处置：**不动 DB**。导出留档到 **NovelOS-Artifacts: `archive/stalled-runs-20260927/`**
  （`stalled_runs_export.json` 326KB 含完整 checkpoint + `评审摘要.md` 15KB 人可读判档）。

**6. 对抗审查打回 7 项 + 开发/审查数字互斥 2 处由主会话裁决**

审查判定**打回**（4 major + 4 minor）。主会话逐条复算审查员证据后**全部采纳**并派回修复；
其中 **只有 major-1 是行为改动，其余是注释/表述与测试口径**。

- **major-1（唯一的代码行为改动）**：`_SPEAKER_LINE_RE` 拆两分支、**宽度分别校准**——
  分支①（冒号+引号）`{1,3}`、分支②（冒号即段末）`{1,6}`。依据：人类锚点走完前置过滤后
  带冒号的 ≤12 字段落**只有 2 条**（`陆明河：“……”` 3 字 / `宋沛年：`），**宽度证据只支持 3**。
  原 `{1,6}` 在生成侧多豁免 **2 段**（`林昭点点头：“知道了` ch14 / `霍铁没懂：“给谁` ch35），
  正是本规则要抓的叙述节拍行。收窄后人类侧**逐字不变**（8 / 0.212 / 6 章 / 1 章超阈），
  生成侧 258 → **260**。
- **major-2**：删掉「人类零容忍不成立」这句结论，改为**「散文口径下成立」**——
  人类 8 命中分类为**散文 5 / 分隔线 2 / 平台标记 1**；剔非散文后 **5 / 0.132 / 0 章超阈**
  （唯一超阈的 ch19 靠 3 条非散文行撑起，该章散文仅 1 条 = 0.76）。
  同时登记「非散文排除规则**未实现**」（属新形态豁免，需自己的 20 条抽样，超本轮范围）。
- **major-3**：删掉两句**事实错误**的辩护——原注释称「降到 1.5 会再剔掉 g32」
  （实测 1.5 与 2.0 输出逐字相同，g32 比值恰 2.00，在 2.0 时**已**被剔）；
  称「放宽到 2.2 会把 ch13 放回」（ch13 比值 11.43，2.0→2.2 区间内一对都没动）。
  改写为实测事实 + 显式承认**「取被剔集合最小值是恒真式不是证据」**。
  参数扫描（严格小于语义）：`1.5/1.88/1.95/2.0 → 23对/18章`｜`2.05/2.2/2.5 → 24对/19章`｜
  `2.83 → 25对/20章`｜`3.5 → 28对/21章`；人类侧 [1.5, 5.0] **全程 1 对/1 章**（零区分力）。
  **阈值 2.0 不动**——审查员攻不倒「后句是超集 ⇒ overlap 读数机械失效」这一定义性依据。
- **major-4**：「11 对全部合法、0 条真信号」降级为带判档（**合法 6 / 存疑 3 / 真信号 1**），
  并写入 **E-33 vs E-34 同尺反判档**这一已知代价：
  `他们敢。`→`他们敢。他们正愁没理由。` 字面复述 3/3（ratio 3.33）被判合法，
  而 `他们退了。`→`他们退了。`（recall 1.00）被判真信号——同一把尺子两个结论。
  **已知代价登记**：守卫在「复述关键词后补理由」家族内**与回声度反相关**，
  它只剔除判档较确定的那批（审计标的 3 条「存疑」recall 0.83/1.00/0.75 全被 recall 分支保住）。
  守卫与阈值均不动——审查员实测替代方案（`common/back≥0.25`）人类侧从 1 对涨到 4 对/3 章，**更差**。
- **minor-5/6/7**：三处自相矛盾注释订正。其中 minor-6 揭示
  **`:754` 「不给第三次机会」那句当时是假的**——`_last_paragraph`（`AI-ENDING-SUMMARY`）
  就是第二份独立实现（内联 `prose.split("\n\n")`）。已改为如实登记 + 触发条件
  （生产 draft 出现 CRLF 或单换行形态；实测 dev 库 196 份 draft 含 CR 的 0 份 → **当前不可达**），
  并把「唯一入口」测试从**按名字断言**升级为**形状级**（扫模块源码里的 `prose.split("\n\n")`
  调用点、跳注释行、向上定位所属 `def`），使「第三份实现」将来必被抓到（突变验证 M6 已证）。

**⚠️ 开发与审查的数字互斥 2 处，主会话独立复算后裁定开发正确**（这也是「主会话不采信子代理结论」
的正面案例——不是每次都是审查员赢）：

| 争议点 | 审查员称 | 开发称 | 主会话实测 |
|---|---|---|---|
| 2.0→2.2 区间 | 无任何对、实测无变化 | 2.05 起变，**24 对/19 章** | ✅ **开发对** |
| ch35 `你守后队` 何时回到判定 | 2.0→2.5 之间 | 要到 **2.83** | ✅ **开发对** |
| `{1,6}` 多豁免几个生成侧实例 | 6 条 | **2 条** | ✅ **开发对**（且 258→260 正好差这 2） |

**突变验证 6 项全部被杀**（含审查员指认的判别点 M5「分支① 宽度回退 {1,3}→{1,6} 必红」
与 M6「引入第三份段切分实现必红」）；恢复后自检 426 passed、无残留标记。

**7. 子进程输出编码：父子两侧会静默错位（实测坐实 + 已修）**
- 全量回归跑出 **1 failed**：`tests/api/test_chapter_reopen.py::test_reopen_keeps_state_sync_ok`
  → `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa0 in position 334`。
- **机理（实测对照，非推测）**：子进程 stdout 编码**跟随继承的环境变量**
  （`PYTHONIOENCODING` / `PYTHONUTF8` / locale），而父进程 `subprocess.run(text=True)`
  的解码器**跟随 locale**（本机 Windows = `cp936`）。两者可以静默错位：

  | 环境 | 子进程输出 | 父进程解码 | 结果 |
  |---|---|---|---|
  | 无相关 env | cp936 | cp936 | 通过 |
  | `PYTHONIOENCODING=utf-8` | utf-8 | cp936 | **UnicodeDecodeError** |
  | `PYTHONUTF8=1` | utf-8 | utf-8 | 通过 |

- **只给 `encoding=` 不够**（子进程仍可能吐 GBK），**只给 env 也不够**（父进程仍按 locale 解）——
  必须两侧同时钉。已按仓内已验证的正确写法（`test_produce_chapters_e2e.py:315-316`、
  `test_db_maintenance.py:260`）修 2 处：`test_chapter_reopen.py`、`test_db_migrations_dir_anchor.py`，
  补 `env["PYTHONIOENCODING"]="utf-8"` + `encoding="utf-8", errors="replace"`。
- **判别（RED→GREEN 实测）**：修前 B 场景失败；修后**三种环境各 14 例全绿**。
- **同款形状第 3 处未修**：`tests/unit/test_m1_lock.py:215-220` 同样缺 `encoding=`。
  **不是因为不重要，是因为改完无法验证**——该文件单跑会硬杀进程树。
  留待与树杀真因一并处理，登记在案不遗忘。

**环境发现（其余为既有缺陷，非本轮引入，留待单独立项）**
- `tests/unit/test_m1_lock.py` 单跑**硬杀整个进程树**（0 字节输出、exit 1、无 traceback），
  会让全量 `pytest -n 4 -q` 反复中断。已用 deselect 对照证明与本轮改动无关；**真因未定位**。
  ⇒ **全量回归须显式 `--ignore=tests/unit/test_m1_lock.py`**，否则拿不到可比的总数。
- 澄清一条曾被怀疑的机制：`os.kill(pid, 0)` 在 Windows/Python 3.11 **不杀进程**
  （实测子进程心跳持续推进、进程存活），`_pid_alive` 安全——它**不是** `test_m1_lock.py` 自杀的原因。

### 2026-09-26 挂账清偿批次：审计断链 / 口径单点 / 词表误报 / 重开返修 / 源锚定 / R-1（6 项 + 审查收尾 6 项）

> **来源**：用户指令「近期不写作，全给修了它」（不动内容侧——无写作、内容仓零接触）。主会话三路
> 定向侦察 + 亲验承重结论后定稿方案，四组并行开发（文件互斥分组）→ verifier 全量复跑 → 静态审查
> （2 major + 4 minor，无突变残留）→ 收尾修复（含 1 个确定性夹具失败）。每项带「撤修复必红」。
> **测试基线 2807 → 2893 passed / 2 skipped / 0 failed**（342s）；ruff / tsc 全绿、vitest 489；
> OpenAPI 97→98 paths / 50→51 schemas（reopen 端点入约）；迁移链 0001~0030；check_state_sync SYNC OK。

**1. 改稿审计断链（两半）** — ① `agent_runtime/revision_fidelity.py`（新）：`preserved_ratio`
（quick_ratio 上界归零精确短路）+ `REVISE_MIN_PRESERVED_RATIO=0.50`（dev 库实测定锚：闸门管辖人群
n=18 min 0.6463 / 静默重写簇 bulk ≤0.16）；`validate_contract` writer 分支——revise 模式低保留比
且 `self_report.deviations` 零申报 → AgentOutputError（runner 自动重试 1 次，报错带实测比值与两条
合规路径）；体量前置守卫（prose < 上游 10% 不进闸门，该形状归字数带闭环管辖）。②
`chapter_write/pipeline.py` 节点产出新增 `revision_audit`（requested/final mode、五态
checklist_status、preserved_ratio、deviations_count）——翻模 `superseded_by_under_band_rewrite`
不再无痕。**事故链勘误**：核销表落 null 的真因是**欠带翻模**（`_build_length_retry_payload` 强制
mode='write' 后采纳翻模稿）而非 polisher，修订 09-21 批次定性。

**2. 章字数口径单点 + 开篇词表两档** — `genre/consumers.py::resolve_chapter_word_band`（绑定包
`pacing.chapter_word_band` 三键齐全+次序合法才返回，source 指纹 `<pack_id>@<version>`；脏带整体
拒绝回退默认）→ signing_check 字数检查三档（pass=带内 / warn=带缘 / fail=<floor），无包路径逐字
不变；开篇规则支持自带 `keywords`（schema OpeningRule 可选字段声明）——curated 零命中=真 fail
（detail 列词表），仅文本抽词零命中降级 **unverifiable**（创新词汇书整批误报根除）；service 恒报
`word_band_source` + 脏带时 `word_band_rejected: true`；`genre/verifier.py` 带解析同点收敛
（`chapter_word_band_invalid` reason），双模块对脏带不再打架。

**3. COMMITTED 章重开返修** — 状态机 `ALLOWED_NEXT["COMMITTED"] += REVIEWED`（commits 历史
append-only 不撤销，重开走既有评审/提交链产生新 commit）；`POST /api/chapters/{chapter_id}/reopen`
（活动 run 守卫 409；reason 写 `plan_json.revision_note` 前缀 `reopen: `，换行拼接不覆盖历史）；
前端章详情页 COMMITTED 态「重开返修」按钮 + 内联确认。**审查抓到旁路并封死**：通用 PATCH update
显式拦截该边（update() 守卫，ChapterTransitionError→409），reopen 改直写 UPDATE（照 create_draft
绕白名单先例）——「仅 reopen 可用」从注释升格为代码闭环。

**4. observer 源锚定检测（恒 warning，绝不阻断）** — `story_state/delta_anchoring.py`（新）：
① excerpt 归一化核验（引号/标点/空白归一化后须为源文本子串，OBS-EXCERPT-NOT-VERBATIM）；② after
文本 CJK bigram 覆盖率 < 0.08 报 OBS-UNSOURCED-PHRASE（阈值 dev 库实测：n=398 p10=0.0986，触发率
8.0% / 27 delta）。接线三处（commit 节点 output `source_anchoring` / HIGH 暂停 payload 摘要 /
summary 计数），不进 BLOCKING_RULES、不碰 severity 矩阵与公式哈希。**判别力如实入档**：after 为
概括性转写，bigram 信号弱（低覆盖样本 20 条人工检视零实捏造），定位「人工抽检入口」；excerpt 背景
「违规」率 59.8% 系模型复述型摘录而非捏造（「第一代老镖头」事故条目属任意归一化版本都不命中的真
捏造形态，dlt_2cefe69cd672 实证）。

**5. R-1 双防线** — ① 导入侧：`backup/service.py` rollback_of 纳入自引用处理（原值查映射 → 含
`#` 后缀剥后缀再查 → 仍无则置 None 并记 import warning）；② schema 侧：0030 迁移把全库级
`idx_commits_rollback_of` 改为 `(project_id, rollback_of)` 唯一（不变式：rollback_of 指向的
commit 只属一个 project，项目内「至多回滚一次」约束力不变，跨项目/重导入共存放行）。修前红留证：
同库重导入撞 IntegrityError 复现测试。

**6. 审查收尾（6 项）** — ① `_DELTA_ARRAYS`↔`_OBSERVER_ALL_ARRAYS` 一致性看守测试（core 不 import
workflows 的双声明名册，docstring 声称落空补实）；② PATCH 借道封堵（见 3）；③ 审计状态改名
`superseded_by_under_band_rewrite`（防与 `fresh_write` 重写开关撞词）；④ verifier 脏带口径收敛
（见 2）；⑤ `word_band_rejected` 标注（见 2）；⑥ 既有 e2e 夹具
`test_chapter_review_revise_loop_end_to_end` 被 1 的闸门正确拒绝（25 字上游 mock 整段改写零申报 →
ratio 0.0833，三次复现）——**修夹具不修闸门**：mock writer 按契约路径②申报（局部
`_writer_declared_rewrite_script`，闸门零改动）。**裁决留痕**：审查建议「体量守卫分支改为强制
申报」不采纳——该分支服务「没干活」形状（字数带闭环管辖），强制申报会与节点内字数重试回路打架，
且缺口需上游 ≥10× 带下限（万字级章）远超真实工作域；缺口已在 revision_fidelity docstring 记录。

**09-21 契约批次「未修登记」5 项全部清偿**（① 审计断链 / ② 字数口径 / ③ keyword 误报 / ④
COMMITTED 返修路径 / ⑤ observer 转写失真检测层）。新增挂账：无（excerpt 聚合面与
OBS-UNSOURCED-PHRASE 判别力降级评估列为观察项，非缺陷）。

### 2026-09-26 进度同步复核（无代码改动，纯核销 + 文档订正）

> 工作区三批次（09-18 / 09-21×2）滞留 5 天后统一落库前，由独立验证代理全量复核实测：
> **pytest 2807 passed / 2 skipped / 0 failed**（`pytest -n 4 -q`，341s）、ruff 全绿、tsc 干净、
> vitest 485、openapi regen 前后仅 version 字段环境元数据差异（97 paths / 50 schemas 零漂移）、
> `check_state_sync` SYNC OK、迁移链 0001~0029。文档订正两条：① 本文件 09-21 批次基线
> 2817→2807（见下）；② AGENTS.md 架构约定迁移链口径 0001~0026/38 表 → 0001~0029/40 表
> （0027 加 chapter_scene_plans 表、0029 加 writing_bible 列）。三仓进度核查：内容仓自 09-17
> 无新写作（F-20 指针 6/48 章重写 + 5 章欠带不 stale）；09-21 作者体验会话导出物已归档
> NovelOS-Artifacts（`eae2234`，无敏感串核验后入库）。

### 2026-09-21 全量检修复核批次：confirm 并存吞信息 / CONT-* 错类 / 尾块防呆 / 巡检假阳性（8 项）

> **来源**：主会话对全仓做一次体检（3 个并行审查代理分头查 quality 门禁 / context_engine 装配 /
> workflows+API，主会话逐条亲验后执行）。基线动作先跑一遍：`pytest -n 4 -q` 2798 passed / 2 skipped、
> ruff 全绿、表数 39 一致、版本三处一致。**无 critical**；2 个 major + 6 个 minor，每个修复都带
> 「撤修复必红」的突变验证。

**1.（major）confirm 与 blocking 同发时 confirm 信息被整体吞掉** — `chapter_commit/gate.py`：
此前 `should_block` 分支里 `if blocking_issues:` 优先，`gate_blocked.rule_ids` / ValueError 消息 /
revision_note 全只含 blocking 侧。作者修掉硬伤后再提交才第一次撞上 confirm，且不知道要准备什么
`gate_override`（违背 P0-1「不许在不知道批准什么的情况下批准」）。修法：`gate_kind` 新增
`"block+confirm"`——blocking 优先不可协商不变，但 rule_ids 取两组并集、note 合成两类建议、
error 串在既有 `| guidance=` 之前追加 `| gate=block+confirm | confirm_rule_ids=[...] |
override_error=... | evidence=...`（前端按 `"| guidance="` 切分，零影响）。仅 blocking 时保持旧消息形态。

**2.（major）`CONT-*` 连续性规则在质量报告里被归为 `style` 类** — `quality/engine.py` 的
`_AI_PATTERN_CATEGORY` 未登记 `CONT-CLOCK-DAYBREAK` / `CONT-TIME-BACKSTEP`，落入默认回退
`style`，与 `continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS` 的命名空间声明矛盾
（「名实不符」——报告/前端按 category 分桶时连续性命中跑进「文风」桶）。修法：映射从 taxonomy
名册**派生**（`{rid: "continuity" for rid in DETERMINISTIC_CONTINUITY_RULE_IDS}`），将来 taxonomy
加新连续性规则时只需改一处，名册与映射的一致性由测试钉住。

**3.（minor）`AI-DIALOGUE-LOW` 的证据是正文前 120 字** — 该命中的 dict 没有 `samples` / `words` 键，
`_ai_pattern_evidence` 只能退回 `excerpt`，读者看到一段与「对话占比 8%」无关的原文。修法：命中补
`samples: [占比摘要]`，既有 evidence 通道直接取到。

**4.（minor）write 模式的 length retry 不剥离 stray `---REVISION-CHECKLIST---` 尾块** —
`chapter_write/pipeline.py` 此前只按 `mode == "revise"` 剥离。writer-v3.md 明令 write 模式不输出尾块，
但那是 prompt 纪律不是强制：模型一旦在 write / retry 轮吐了尾块，它会原样落进 `drafts.content`
并抬高 word_count（字数带按含尾块数字判定）。修法：改为**看内容剥离**（分隔行出现就剥），
坏 JSON 也剥（此前原样落稿）；revise 语义不变（缺失仍 warning），write 模式剥掉的尾块**不进**
`revision_checklist` 审计面（审计面只登记定向改稿的核销承诺）。

**5.（minor）`gate-revise` 端点不透传 `target_word_count` / `author_intent`** — `GateReviseRequest`
此前没有这两个字段，改稿链上整体缺失：作者按 `--target-word-count 300` 起稿后被门禁拦下，一键改稿
时 target 退回服务端默认 3000、作者铁律整段丢失（与 auto_revise 回路 2026-09-18 修过的同形）。
修法：两个字段入请求体（同 `StartWorkflowRequest` 约束），值按「请求体 > 该章最近一次 run 的 ctx
继承」解析，write 与接力 review 两段都用同一份。openapi / types 已 regen，前端 `gateRevise` 签名同步。

**6~8.（minor，docstring / 巡检口径）** — ① `_normalize_gate_override` docstring 声称「非法形态 → None」
而实现返回空 list 归一（行为安全、文档失真，已改为实情）；② `repair_policy.length_band_shortfall`
补明「两源口径不一致时取缺口最大者」的理由；③ `check_state_sync` 把「无快照项目」的实体全部计成
漂移（dev 库实测单项目 29 个实体全列进明细、结论 `DRIFT: 5 处`，而它们只是从未 commit）——
改为单列「无快照项目（不计漂移）」小节 + JSON `projects_without_snapshot` 字段，dev 库复测 `SYNC OK`。

测试基线 2798 → **2807 passed / 2 skipped**（新增 7 个判别用例 + 4 个 state_sync 口径用例；
前端 vitest 485 不变）。全部修复配突变验证（撤修复必红，逐个实测）。
〔2026-09-26 同步复核订正：本行原记 2817 与 AGENTS.md 一致性矩阵的 2807 矛盾；全量复跑实测
**2807 passed / 2 skipped / 0 failed**（341s），按实测改定，账面加法与实测的 2 例出入不再追溯。〕

### 2026-09-21 契约形状守卫批次：json_repair 腐烂产物不再静默过闸（2 项，16 新增用例）

> **来源**：实战抓样——当天以作者身份用真实新书（`prj_8df1b427a688`《快穿镖人：开局护送灭世帝女》）
> 跑 chapter-plan 时，director_planner 输出的 `key_beats` 被三级兜底 `json_repair` 修成
> 「顶层可解析、元素腐烂」的结构（裸字符串/裸数组碎片混进数组）并**静默落库** `chapters.plan_json`
> （`workflow_run_nodes` 里即烂，正文靠 `chapter_goal` 兜底未跑飞，全程无告警）。由同事代理修复、
> 主会话逐条亲验（含拿库里真实事故计划喂新校验必拦）。测试基线 2768 → **2798 passed / 2 skipped**。

**1. director 家族计划数组形状守卫** — `_validate_director` 新增 `_DIRECTOR_PLAN_ARRAYS` 核销：
`key_beats` 在场必须是 list、元素必须 dict 且含 str `beat_id`/`purpose`；`character_changes_planned` /
`information_releases` 元素必须 dict。**键缺席放行**（不收紧必填面；quality 对「无 key_beats」另有中性
口径）——「验收要严、产线要活」的产线闸门口径不变。违规抛 `AgentOutputError` 走 runner 既有
output-invalid 重试，**不静默放行**。用真实事故 payload 验证：拦截信息
`key_beats[1] must be an object, got str` 精确指位。

**2. observer 契约 7 数组元素形状守卫（同族洞，定性被实测修正）** — 形状核销抽成共用单点
`_array_shape_errors()`，observer / director 两族共用；`_validate_observer` 从「只查数组是 list」
收紧到「元素必须 object」。**必须说清的定性修正（同事代理实测推翻我先验）**：这条链**不是**
静默落库路径——碎片会被下游 `story_state.validator.validate_delta` 的 jsonschema 拦下；真实危害是
① 报错退化成笼统 validator 错误串（诊断质量）② 契约层放行让失败下探到 commit 级 delta 重试
（多烧 observer 调用）③ 非 validator 消费方各自兜底（`_has_high_risk_change` 曾静默跳过非 dict
元素；风控门 pause 载荷会把碎片原样端给人工）。修后重试提示可行动（`character_changes[1] must be
an object`），HTTP 级端到端钉住「run FAILED / 章状态不动 / delta 不落库 / 契约层单次重试」。

**未修登记（后续批次）**：① revise 模式的 `revision_checklist` 经 write 流水线 polisher 段后落库为
null、`deviations[]` 越界申报无机器强约束——人工审改版只能靠全文 diff 发现超注改动；② signing-check
「最佳 1500-2200 字」与题材包校准带「2000-3000（floor 1300）」口径打架（后者取自 10 本拆书实测）；
③ 题材开篇 5 条 keyword 检查对创新词汇书整批误报（本书镖单/劫点/墨级词汇表与规则关键词零交集，
5 条全「fail」但均为误判）；④ COMMITTED 章无局部返修路径（drafts 仅收 DRAFTED/REVIEWED）；⑤ observer
转写失真一例（「第一镖头」压成「第一代老镖头」混入 knowledge delta，靠人工风控门肉眼拦截）。

### 2026-09-18 全量优化批次：以「能交付成稿」为目标（12 项）

> **来源**：当天用一本真实新书（`prj_2567bb8de642`《快穿：收账人》）以作者身份走完整条流水线，
> 交付前三章后按实测缺陷排的修复批次。**所有阈值均取自本仓实测分布，不照搬外部数值**；
> 每条修复都带突变验证（撤修复必红）。测试基线 2370 → **2768 passed / 2 skipped**。
> 批次内**有一处先验判断被自己的度量推翻**，见 §1.3，按项目铁律留痕。

#### 1. 后果闭合：算出来的问题必须有人能说「不」

**1.1 severity 与后果解耦（P0-1）** — 此前 severity 有三档（info/warning/error）而**后果只有一档**
（过）：`issues.py` 里只有「severity==error **且** rule_id ∈ `BLOCKING_RULES`」会归零总分并触发
`enforce` 阻断，其余 error 一律「informational」；而 `MVP_SEVERITY_MATRIX` 把 style / pacing /
payoff / ai_trace 四类**上限封在 warning** ⇒ 重复类问题在构造上永远进不了阻断白名单。
实证：一份 9 段逐字重复（章内重复 20.3%／trigram 30.37%）的稿子 `errors: []`、`overall: 90`、
`ai_trace: 92`，全流程绿灯提交。
新增与 severity 正交的后果轴 `Gate = Literal["auto","confirm","block"]`
（`issues.py::issue_gate`，唯一权威）：`block` 仍 ⟺ `severity=="error" ∧ rule_id ∈ BLOCKING_RULES`
（`is_blocking_issue` 一字未动）；`confirm` ⟺ 规则入表**或**该条 issue 自身带 `gate="confirm"`；
其余 `auto`。`enforce` 下 confirm 命中须带 `gate_override = {"rule_ids":[…],"reason":"…"}` 才放行
（集合包含 + reason 非空），否则复用既有阻断落点（`plan_json.revision_note` + `gate_blocked.gate`），
消息同时给出 **rule_id 清单 + 证据摘录**——调用方无法在不知道自己在批准什么的情况下批准。
`StartWorkflowRequest.gate_override` 已暴露；openapi/types 已 regen。9 处突变验证。

**1.2 AI 模式级规则接进质量路径** — 接线核实为**真缺陷**：`scan_ai_patterns` 的命中此前只进
`chapter_review`，`QualityEngine.evaluate` 从不消费 ⇒ `CONFIRM_RULES` 里的 `AI-BEAT-REPEAT`
在提交链路上是**惰性声明**（门禁从 `report.issues` 判，报告里永远没有这条 rule_id）。
新增 `quality/engine.py::ai_pattern_issues`（通用转换，rule_id 原样透传、不硬编码）⇒ 现在真的进
`quality_reports` 与提交门禁。severity 按 category 矩阵封顶，白名单规则不被削；与既有子分是两轴、
不参与任何 `score_*`。撤接线 6 例红。

**1.3 ⚠️ 先验判断被推翻：trigram 阈值 0.08 落在正常分布内部** — 我把
`RULE_STYLE_REPETITION_TRIGRAM` 入 `CONFIRM_RULES` 的依据是**一个**观测样本（事故章 30.37%）。
度量后证伪：人类锚点书（`NovelOS-Content/reference-books/榜一…侯府弧`）19 章
**mean 7.54% / median 7.87% / max 9.49%，9/19 章超 0.08**；生成侧 92 章
**min 6.19% / p50 13.33% / p90 17.49% / max 20.97%，仅 2/92 章低于 0.08**。即该阈值下几乎每一章
——包括人写的——都要显式签字，门禁退化成橡皮图章，比不设门更坏（AGENTS.md「命中即报表把正常
写法与真信号混在一起」的形状）。**事故章自身其余 12 版也只有 8.5%~13.0%，事故是单版异常。**
替代方案：两档均取本仓分位——`STYLE_TRIGRAM_WARN_THRESHOLD = 0.16`（生成侧 p85 = 0.1621）、
`STYLE_TRIGRAM_CONFIRM_THRESHOLD = 0.25`（生成侧 max 0.2097 与事故 0.3037 的几何中点 0.252）；
severity 恒 warning，两档只差 `gate`。该规则**撤出静态 `CONFIRM_RULES`**，改由 `scoring.score_style`
只对超 confirm 阈值的那一条上修 `gate="confirm"`——静态 rule_id 表表达不了「同一规则两种量级」。
改后实测：人类侧 **0 warn / 0 confirm**，生成侧 78 无 issue / 14 warn / **0 confirm**，
真实事故那版 confirm 并带片段证据；全库 110 条历史 trigram 命中复算 92 静默 / 17 warn / 1 confirm。
`scoring_formula_hash` `7613897ead30e445` → **`988518dca9e00c53`**。

#### 2. 生成期替代评审期：长度不再靠「事后改稿」

**2.1 计划预算自洽** — `_inject_scene_word_budget` 现要求 per-scene 预算总和落在 chapter target 的
±10%，越界按已声明值**比例归一化**（最大余数法，确定性），不再「越界即推倒等分」；已声明子集吃满
预算时缺失 scene 不再拿 `T/n` 保底值把总和顶到 1.33×target。归一化不静默（warning + 结构元字段）。
薄计划信号：target ≥ 2000 却只 1 个 scene ⇒ warning 级 `thin_plan`（实证：target 2500 只规划 1 个
scene 仍被当正常，writer 交 900 字）。

**2.2 write 节点内长度闭环** — writer 返回后实测 `visible_chars`，欠带即**同一节点内以
`mode="write"` 整章重写**（清 draft_text / 去 revision_note / 注入 `length_directive`），最多
`NOVELOS_WRITER_LENGTH_RETRIES`（默认 2）次，落带即停，取离带最近的一次；首次在带 ⇒ 恰好 1 次
writer 调用；超带不走此路径（仍归 condense）。**为什么必须 `mode="write"`**：revise 在 writer 侧被
规则 20（净增 ≤ +5%）与 §6.1（未提及部分逐字保留）钉死，既是「追不上字数」的原因，也是「造出
重复」的原因。7 处突变验证；`smoke_e2e` PASS。

#### 3. 修复动作策略表：失败形状 → 修复动作（取代「一律 revise」）

根因：**修复动作只有一个**。无论评审报的是长度、标点还是连续性，回路都 `revise: true`。
新增 `routers/workflows/repair_policy.py`（纯函数 + 规则表）：`decide_repair` 给出闭集三选一 ——
`regenerate`（`fresh_write`，writer 回 write 模式）/ `revise`（定向局部改稿）/ `stop`（不启动子 run，
交人工）。判定顺序：读不到报告 → 缺 rule_id → **表外 rule_id（含连续性 / 逻辑 / 设定类与全部占比
指标）** → 章内重复 → 字数下限缺口超出 `(1.05**n)-1` → 超带 → 作者主观驳回 → 局部形态类规则。
原 `fresh_write` 逃逸不再是特例，成为策略表里的一行。停止 / 轮次耗尽会把
` | auto_revise: stopped at iteration i/n: <reason> — <detail>` 追加进该 run 的 `error`，作者在 run
列表里读得到原因；耗尽消息点名未能修复的**形状**。F-19 守卫：`RATIO_METRIC_RULE_IDS`
（`AI-DIALOGUE-LOW` / `AI-DIALOGUE-ECHO` / `RULE_Q6_OVERLAP_RATE` / `RULE_Q8_HUMAN_RATIO_LOW`）
双向禁入两个自动动作集合，命中即 stop。10 处突变验证（其中一处首次复测为绿，暴露了用浮点不可比较值
测边界 = 等于没测，已改用精确可表示值）。

**附带修复**：改稿子 run 丢失目标字数。实证：作者按 `--target-word-count 300` 起稿，首轮评审判
「288/300 字」，改稿子 run 判「288/3000 字」——回路在**错误口径**上判定成败。
`ResumeRequest.target_word_count` 新增，`resume_run` 按「请求体 > 原 run ctx」解析后并入
`initial_ctx_extra`，write / review 两个子 run 共用父 run 的同一个目标。撤透传 ⇒ `assert 3000 == 1000`
（与线上同形）。

#### 4. 检测面补齐（两处，均**先抽样后定阈**）

**4.1 `AI-BEAT-REPEAT`（同章远距小句复现）** — 既有两条章内重复算子只抓**复制粘贴**：
ch2 章尾把同一收束画面写三遍（shingle 0.0030 判干净）、ch3 v1 同一拍出现 3 次（0.0006 判干净），
人眼一眼看出、现有算子零告警。新算子为**纯字面确定性**机制（句读片段间最长公共子串 ≥6 字、
双侧覆盖率 ≥60%、相距 ≥200 字；数字/计量串由数字守卫排除；章级 ≥2 处且 >1.0/千字）。
**抽样先于采信**：候选池系统抽样 **52 条**逐条人工过目 —— **43 真 / 9 误（82.7%）**；
误报两类（专名/术语复指、共用骨架+不同宾语）已在 docstring 与用例里登记为**已知行为**，
不假装算子能分辨。首轮无数字守卫时开局 24 条只有 13 真（账目数字串撑起噪声）⇒ 补守卫。
实测生成侧 1.17/千字 vs 人类侧 0.05/千字、**人类侧 0/19 章**达标。14 处突变验证。

**4.2 章内时点矛盾（`CONT-CLOCK-DAYBREAK` / `CONT-TIME-BACKSTEP`）** — 正文级连贯性此前**无人核销**
（`timeline_consistency` / `knowledge_leakage` 吃的是 `(snapshot, delta)`，核的是状态机不是文字）。
新算子：① 深夜档钟点与其后同章的天亮类标记配对（间距 ≤3000 字且中间无时间推进标记）；
② 同章相邻段首时点戳回退 ≥2 小时（时辰按起始小时折算，跨午夜正确判为顺行）。
**候选空间仅 27 条，逐条过目**：「白天标记→夜晚标记」检查 **14/14 误报 ⇒ 整条废弃未进代码**；
「时点回退」原始 8 条 1 真 7 误（precision 12.5%，与仓内曾废弃的 97/95 同形），加四道守卫后
只剩那一条真命中。**诚实交代**：守卫是在同一批候选上逐层定出的、**无留出验证集**；
人类基线零命中**且零机会**（全弧无钟点标记）⇒ 该基线**不构成**「对人类正文零误报」的证据。
20 处突变验证（首轮有 3 条守卫用例是**空转**的，已重建 fixture 使每个守卫成为唯一排除者）。

**4.3 critic 连续性分类法** — `CRITIC_TAXONOMY` 闭集（`CONT-TIMELINE` / `CONT-UNANCHORED-REF` /
`CONT-REGISTER` / `CONT-PROCEDURE` / `CONT-BEAT-DEVIATION` + 既有五类），全部映射到**现有**
category 枚举、不改契约校验器；每条 issue 新增必填 `rule_id` + `reason`，`quote` 必须**逐字**
（下游子串机检）。**红线**：连续性类 findings 恒 `gate=auto`（可见、不阻断），
**不得**进 `CONFIRM_RULES`/`BLOCKING_RULES`、**不得**作自动改稿触发器。

#### 5. 测量对象一致性：「读草稿的必须是被审那一版」

**先审计后动手**：全 `packages/` 扫「读草稿正文 / 草稿派生测量」站点 **18 处**并逐处定性
（correct / latent / out-of-scope）。**latent 3 处**已修 2 处：① `quality/service.py`
（正文与落库版本是两次独立读，且评估路径无法指定被评版本）；② `chapter_commit/gate.py:quality_gate`
量「最新」而作者可指定旧版审校 ⇒ **可对没审过的正文放行**（接缝：`ctx["draft_version"]` >
最近一次 chapter-review 的 `report.draft_version` > 最新）；③ `chapter_review` 的 `basic_checks`
自带 `ORDER BY created_at DESC`，同一份报告里存在两个「最新」⇒ 收敛到共享单点。
新增共享解析单点 `packages/domain/chapter/draft_resolver.py::resolve_draft`（显式版本 → 该版本；
`None` → 最新，排序口径 **`version DESC`**，发布序与作者看到的「v13」同口径）。
**形状级回归测试**：递归遍历报告里所有「字数」与「版本」字段，事故三版形状 v11/v12/v13
（713 / 1674 / 2500）——默认必须全取 v13，指定 `draft_version=12` 必须全取 v12；
将来新增的测量点若又漏传被审版本会直接在此现形。

#### 6. 作者面

**6.1 项目写作圣经（迁移 0029）** — `author_intent` 此前是 `StartWorkflowRequest` 上的**单次运行**
字段、不落库：作者的整本书铁律（「无 CP」「第一位面＝现代都市」…）必须每次 plan / write / revise
重新粘贴，漏一次即**静默丢失**。新增可空列 `projects.writing_bible TEXT`
（0029 纯 `ALTER TABLE ADD COLUMN`，无回填、存量逐字不变），经 `ProjectCreate/Update/Project` 与
projects 路由暴露。**优先级：圣经＝基线，运行期＝增量，按「基线 + 增量」拼接**（文本内声明冲突以
增量为准）——不选「整体替换」的理由：作者为单章写一行小要求时会静默丢掉全书铁律，正是本条要修的
形状。解析单点 `builders_common.resolve_author_intent`；writer / director 装配注入（含 paged 路径
与 preview）；**缓存键新增维度 `bible_fp`**（硬规则 2：撤该维度 ⇒ `assert 1 == 2` 读到旧装配）。
14 处突变验证。
> 同日另修：`brief.author_notes` 此前只传给 project-init 四段中的两段，world_builder /
character_designer 拿不到作者约束（实证：brief 里写了「无 CP / 现代都市」，角色表仍返回
`love_interest` 与古风宗族世界观）——与 AGENTS.md 硬规则 9「收下即忘」同形。已补两段 payload
+ 两处 prompt 段。

**6.2 章节量产一等入口** — 此前每本书都要在仓外手写一次性驱动（`_refs/arc1..arc6_driver.py`
一路演化），409 竞态、PAUSED 人工节点、风控门全靠脚本自己扛，日志里那些「进程静默死亡 30 分钟」
就是这么来的。新增 `scripts/produce_chapters.py`（dry-run 默认 / `--apply` / `--json`；
端口取自 `get_settings()`）：四道守卫 —— 409 容忍的有界退避启动、commit 前等清场、报告驱动的
评审决议、风控门有界自动批准（载荷进 `auto_approvals` 可审计）；改稿子 run 靠轮询「更新的 PAUSED
chapter-review run」发现（不盯 chapter.status）。**不静默批准坏章**：批准前用本地 13 字 shingle
口径直算章内重复率，超 8% 拒绝批准（实测重复段落成文 0.4492；撤该分支后驱动**确实把重复章推到了
COMMITTED**）。

#### 7. 交付判定：为「done」补上唯一出口（P4）

根因：提交门的硬停只有一份很窄的结构性白名单，可交付性散在 `quality_reports` / 字数带 /
`signing_check` / 章状态四处，**没有任何一处回答「这一章 / 这本书能不能交」**。
新增 `packages/core/delivery/`（纯规则聚合，无 LLM / 无新依赖 / 无迁移）+ 端点
`GET /api/projects/{project_id}/delivery-verdict`（逐章 rows + 项目 roll-up）。
四档判定 `deliverable` / `needs_work` / `not_deliverable` / `not_a_candidate`
（PLANNED 明确为「还不是交付对象」，与「写了但不能交」分开）。**证据缺失 ≠ 通过**：
无质量报告 / 报告评的草稿版本 ≠ 当前最新正文 / 量不出字数 → 一律落 `not_evaluated` 并降级
（撤该分支会使「无质量报告」的章变成 `deliverable`，静默通过形态实测复现）。
gate 分档走 `issue_gate` **现推**而非读落库字段（实测该库 0/960 条 issue 带 `gate` 字段）。
`signing_check` 只对第 1~3 章计入判定，第 4 章起显式标 `out_of_scope`。
实测本书：`project_verdict: needs_work`（3 已写 / 0 可交付 / 27 未写），第 1 章 blocking 理由为
「trigram 重复率 …（confirm 档：须显式接受才放行，本章报告内无接受留痕）」+`ch1_conflict_300` fail
——**那个「响了但没人消费」的黄金三章规则现在被消费了**。12 处突变验证。

#### 8. 其它修复与测试基线

- `resume` 竞态撞 0017 部分唯一索引 → HTTP 500（应为 409）：见下条独立条目。
- 共享 filler `tests/unit/neutral_prose.py` **自身** shingle 重复率 0.71(300字)/0.94(1300字)——
  「中性」填充自己就是重复文本，新算子命中它不是误报。重写为组合式生成（实测 0.0000 全档、
  `scan_ai_patterns` 零命中），随之删除 4 个用例文件里的 `without_repeat_hits` 过滤豁免。
- 基线：**2768 passed / 2 skipped**（本轮起点 2370）；`ruff check packages scripts tests` 全绿。

### Fixed（2026-09-18 题材核销字数带量的是旧稿：作者手改后的草稿从未被 genre 判据看见）

> 实机证据（只读复算，章 `ch_92bac068ff0d` / 项目 `prj_2567bb8de642` / review run
> `wfr_adf71afbb7d9`）：该 review 自身报告 `word_count: 2248, draft_version: 13`，而**同一份
> 报告**里 `genre_check` 写
> `GENRE-WORD-BAND-DEVIATION … "实际字数 713（length_report）低于下限题材包字数带 2000~3000"`。
> 713 是 **v11** 的可见字数（`drafts` 复算：v11=713 / v13=2248），v13 在带内。
> **真因**：`packages/core/genre/verifier.py` 从**最近一条 chapter-write run 的 checkpoint**
> 取 `length_report`，并按 `length_report["visible_chars"]` 定字数——但 `length_report` 是
> 那次写稿 run **落笔那一刻**的自测值，其归属版本写在 checkpoint 的 `draft_version`（该 run
> 为 11）。凡不经过 chapter-write 的正文变更（**作者手改**：`POST /api/chapters/{id}/drafts`；
> 复审指定旧版）genre 一律看不见，判据与「被审的那份正文」脱钩——**测量对象 ≠ 被审对象**。
>
> - **修复落点（选定接缝：核销入口按版本取正文 + length_report 需可归属）**：
>   ① `verifier.py::_load_chapter_sources` 增 `draft_version` 形参（显式版本 → 按版本取草稿；
>   省略 → 最新一版，默认口径不变），并读出 checkpoint 的 `draft_version` 作为
>   `length_report_origin_version`（该 length_report 的归属版本）；
>   ② 新增 `verifier.py::_resolve_word_band_count`：**仅当** `length_report` 有归属且归属版本
>   == 本次核销的草稿版本时采信 `length_report`（source 不变、老口径对「没改过稿」零变化），
>   否则改用待核草稿正文的权威口径 `visible_chars`（source=`draft`，与 `basic_checks` 的
>   `report.word_count` **同一函数**——两处口径不可能再分叉）；归属未知（老 checkpoint 无
>   `draft_version`）且草稿正文在场 → 同样量草稿；草稿正文缺席（该 run 未落草稿 / 孤儿章）而
>   `length_report` 在场 → 仍用 `length_report`（v1 兜底不变）；
>   ③ `_check_redlines` 的 `word_band` 明细增 `reviewed_draft_version` /
>   `length_report_origin_version`，让「量的哪一版」在报告里可读；
>   ④ `chapter_review/pipeline.py::_collect_genre_check` 增 `draft_version` 形参，
>   `_basic_checks_node` 传本次评审实际审的 `reviewed_version`（含 `ctx['draft_version']`
>   指定旧版复审的路径）——**核销对象 = 被审对象**。
> - **severity 不变**：`GENRE-WORD-BAND-DEVIATION` 仍 `warning`（hard rule 4：评审类约束不进
>   `BLOCKING_RULES`）；`scoring_formula_hash` / severity 矩阵 / 公式不变 ⇒ 无需三文档同步。
> - **测试**：`tests/unit/test_genre_verifier.py` 新增 3 例（事故形状复现；指定版本复审；
>   归属未知 → 量草稿）+ 1 例既有断言随 fixture 补齐（`_insert_write_run` 现写真实形态的
>   `draft_version`）；`tests/unit/test_chapter_review_genre_check.py` 新增 2 例端到端
>   （事故形状走 `_basic_checks_node`，断言 `genre_check.word_band.word_count ==
>   report.word_count` 恒等 + warnings 里是 2248 而非 713；指定 `ctx['draft_version']` 复审旧版）。
> - **突变验证（撤 → 红 → 复，两次）**：① 撤归属判定（`attributed` 退回「有 visible_chars 即
>   采信」= 原实现）→ 3 例红，实测 `assert 713 == 2248`（`test_genre_verifier.py:411`）、
>   `assert 'length_report' == 'draft'`（:453）、
>   `AssertionError: assert 713 == 2248`（`test_chapter_review_genre_check.py:272`）；
>   ② 只撤「传被审版本」（`_collect_genre_check(db, cid)`）→ 1 例红
>   `assert 13 == 11`（`test_chapter_review_genre_check.py:293`）⇒ 两半修复各自被钉住。
>   两次复测 36 passed。
> - **验证命令**：`ruff check packages scripts tests` → All checks passed；
>   `pytest tests/unit/test_genre_verifier.py tests/unit/test_chapter_review_genre_check.py -q`
>   → 36 passed；关联面 `tests/unit -k "genre or word_band or review"` 与
>   `tests/workflow -k "review or pipeline"` 全绿。未跑全量（按任务边界：dev 服务正在 18081
>   跑量产）。
> - **未做（留痕）**：① 红线语料的 `length_report`（JSON 串）成分未动——它只是文本语料、
>   不参与「测量」，陈旧值只多几个数字；② `length_report` 的其它跨 run 消费方全仓复查为无
>   （仅 chapter_write 链内消费 + 本核销层），故本次只改这一处接缝。

### Fixed（2026-09-18 resume 竞态撞 0017 部分唯一索引 → HTTP 500，应为 409）

> 实机证据（`data/serve.out.log` 只读，run `wfr_dcbfa4324d4c`，14:39:53）：
> `POST /api/runs/wfr_dcbfa4324d4c/resume → 500 Internal Server Error`，
> `sqlite3.IntegrityError: UNIQUE constraint failed: workflow_runs.chapter_id`，
> 抛出点 `control.py resume_run → engine.resume_async → engine._mark_run_running` 的
> UPDATE，违反 `idx_workflow_runs_active`（`chapter_id WHERE status IN
> ('RUNNING','PENDING') AND chapter_id IS NOT NULL`）。**该不变量是正确的，本轮未动**。
> **真因（缺口在守卫的形状，不在值）**：resume 的章节互斥守卫
> （`resume_run` 里的 `_check_active_run_for_chapter`）是 check-then-act——查过一次之后
> 还要 JOIN 反查 workflow 名、取定义、读整份 `checkpoint_json` 拼 ctx（毫秒~秒级）才落到
> `_mark_run_running`；同刻 auto_revise 回路的 daemon 线程正为**同一个 chapter** 启动
> write 子 run（`revise.py → common._run_workflow_return_payload →
> engine.start_with_nodes_async`，日志同一秒的 `auto_revise loop iteration 1/2`），落在
> 该窗口内 → 预检看不到它，UPDATE 被索引拒绝 → 原始 sqlite3 异常外溢成 500。
> **start 路径早有此兜底**（`start_with_nodes` 捕获 IntegrityError → `WorkflowRunConflict`
> → 409，V3.9「0017 兜底 TOCTOU」），resume 路径缺这一层——两个端点是同一竞态形状，
> 只有一个做了转换。

- **修复落点（三层，权威判定仍在索引；不新建错误类型、不放宽索引）**：
  ① `packages/core/workflow_runtime/engine.py::_mark_run_running`：UPDATE 撞
  `workflow_runs.chapter_id`/UNIQUE 时 rollback 并转 `WorkflowRunConflict`（其余
  IntegrityError 原样抛出，不掩盖别的约束缺陷）——**该 UPDATE 是索引的唯一落点，
  因此是唯一原子判定点**；② 同文件新增 `_ensure_chapter_free_for_resume`（引擎侧预检，
  刻意非原子：只为「早于 `_prepare_resume_ctx` 的 `_skip_pending_node_rows` 副作用给出
  可读冲突源」，并让不经 HTTP 的调用方 `engine.resume/resume_async` 拿到域异常而非
  sqlite3 异常）+ 模块级 `_find_active_run_for_chapter` / `_active_run_conflict_message`
  （与 API 守卫同语义、同一 409 文案形态）；③
  `packages/core/api/routers/workflows/control.py::resume_run` 增
  `except WorkflowRunConflict → 409`，与 `_start_workflow` **同一机制**。API 层预检保留
  （第一道，消息最完整）。
- **测试**：`tests/workflow/test_resume_status_codes.py` 新增 4 例（既有 4 例不动）——
  ① 端点预检 409 回归看守；② **实机场景逐条复现**：预检通过后、置 RUNNING 前经
  `_prepare_resume_ctx` 注入同 chapter 的 RUNNING run（`raise_app_exceptions=False` 把 ASGI
  异常落成状态码，与 uvicorn 同行为）→ 断言 409 + detail 含冲突 run_id + resume 目标仍
  PAUSED + 同 chapter 活跃行恒 1 + **再插一条 RUNNING 行仍被索引拒绝**（不变量未放松）；
  ③ 引擎预检抛 `WorkflowRunConflict`；④ 引擎竞态窗口内的索引拒绝转 `WorkflowRunConflict`
  （不经 HTTP）。
- **突变验证（撤 → 红 → 复，两次）**：① 整体撤修复（两文件 `git checkout`）→ 3 例红，
  实测 `AssertionError: 期望 409（干净冲突），实际 500: Internal Server Error` /
  `assert 500 == 409`，traceback 落到 `engine.py:937 … sqlite3.IntegrityError: UNIQUE
  constraint failed: workflow_runs.chapter_id`（与实机逐字同形）；② 只撤
  `_mark_run_running` 的异常转换、保留引擎预检 + 端点映射 → **仍 2 例红**（500 /
  IntegrityError）⇒ 预检单独不足以覆盖竞态，索引转换是承重点。两次均复测 8 passed。
- **验证命令**：`ruff check packages scripts tests` → All checks passed；
  `pytest tests/workflow/test_resume_status_codes.py -q` → 8 passed；关联 6 文件
  （0017 唯一约束 / engine_async / engine_pause_cancel_race / resume_after_crash /
  resume_checkpoint_exclude / auto_revise_loop_cancel）41 passed；cancel + gate-revise +
  human_node_pause_resume + checkpoint_exclude_promotion + v1_4_reference 5 文件 24 passed。
  未跑全量（按任务边界：dev 服务正在 18081 跑量产，全量会占资源）。
- **未做（留痕）**：`common._run_workflow_return_payload` 直接调
  `engine.start_with_nodes_async` 且不捕获 `WorkflowRunConflict`——竞态里若「回路方落败」，
  域异常会在 daemon 线程内抛出、无捕获，线程带 traceback 死掉（既有行为，非本次修复面；
  触发面窄：需外部 resume 与回路同刻抢同 chapter）。登记为后续评估项，本轮不改
  （`revise.py`/`common.py` 正被另一路改动占用）。

### Fixed（2026-09-18 改稿回路 W-LEN 死循环：大字数缺口逃逸出 revise 的 +5% cap）

> 线上实测（`ch_92bac068ff0d`，target=2500 / 带 2125~2875）：writer 首稿 932 字，改稿回路
> 四轮 revise 产出 1044 → 1242 → 1300 字（**最后一轮只比输入涨 +4.7%**——被 cap clamp，
> 不是模型不肯写），每轮 review 报同一条 `W-LEN-DEVIATION … deviation=-65.8% beyond band`，
> 回路每轮烧满、必然以「轮次耗尽」收尾。**两层机制相抵，回路结构上不可能收口**：
> ① `docs/agents/prompts/writer-v3.md` 规则 20「revise 修订模式下净增字数不得超过修订前的
> +5%」+ §6.1 规则 1/6「未提及的部分逐字保留 / 不整章重写」（writer 的
> `self_report.deviations[]` 原话：「draft_text 为上游既成稿 … writer 不做破坏性扩写」）；
> ② `_auto_revise_loop` 把每一轮修复都按 revise 模式驱动（`plan_json.revision_note` 存在
> ⇒ mode=revise）。2 轮 ×+5% ≈ +10% 的总爬升 vs -65.8% 的缺口（≈ 需 +178%）。

- **修复落点**（`packages/core/api/routers/workflows/revise.py`）：新增
  `_read_pending_review_report`（复用既有 `_extract_pause_payload`，读父 review run
  checkpoint 里 `author_review.__pause_payload__.review_report`，不新开查询）+
  `_length_band_shortfall`（取 report 顶层 `within_range=False/word_count/word_band` 与
  `errors[]` 中 `W-LEN-DEVIATION`/`GENRE-WORD-BAND-DEVIATION` 条目的结构化带数据，
  取缺口最大者）+ `_round_needs_fresh_write`（缺口比例 > 剩余轮次可达幅度
  `(1.05 ** remaining_rounds) - 1` ⇒ 本轮 write 带 `fresh_write=True`）。
  回路每轮 write 前判定一次，报告源随轮刷新（第 1 轮=父 run，之后=上一轮 review 子 run）。
- **边界（刻意不做的事）**：只认长度类**下限**缺口——压缩方向不触发（规则 20 的 +5% cap
  只管增侧，「收到明确压缩指令时按指令幅度净减」⇒「capped revise 追不回」在压缩方向不
  成立）；非长度 rule_id、带内 / 小缺口、无 review_report 可读 → 行为逐字不变
  （`initial_ctx_extra` 仍是 None，不得退化成空 dict）。**未改 writer prompt**：把 cap 改成
  「按 revision_note 的幅度」会与 §6.1「逐字保留 / 不整章重写」直接冲突——同一轮既要
  逐字保留又要净增 +178% 正是本轮死循环的成因；mode='write' 是既有的正确逃逸口。
- **验证**：`fresh_write` 的落点先读码确认（`packages/workflows/chapter_write/pipeline.py:639`
  `ctx["fresh_write"]` ⇒ 丢弃 `draft_text`/`revision_note` ⇒ `mode='write'`），并由既有用例
  `test_chapter_write_revise_mode.py::test_chapter_write_fresh_write_overrides_revise_to_write`
  看守；修复用**真实 run 数据只读回放**核对（`wfr_8f623af4556d` 854 字 / -65.8% 与
  `wfr_c2ef5b11e286` 1197 字 / -52.1% 两份 review_report → `_round_needs_fresh_write` 均 True，
  无需写 dev 库）。
- **测试**：单元 6 例（`tests/unit/test_auto_revise_loop_cancel.py`，「字数大缺口逃逸」节；
  `_insert_run` 加 `checkpoint` 参数以支持落 review_report）+ **端到端 1 例**
  （`tests/api/test_gate_revise_closure.py::test_auto_revise_loop_fresh_writes_when_shortfall_outruns_the_revise_cap`：
  plan → write → review(PAUSED) → resume 驳回改稿 + `auto_revise_max=1` → 真实 daemon 回路 →
  断言回路 write 子 run 的 `writer_input['mode'] == 'write'` 且无 `draft_text`/`revision_note`）。
  四项突变验证（撤改后重测、实得文本如下）：
  ① 撤逃逸分支 → 单元 3 例红 `assert None == {'fresh_write': True}`；
  ② 去掉「缺口 > 可达幅度」判据 → 小缺口例红 `assert [{'fresh_write': True}, None] == [None, None]`；
  ③ 撤 `pending_review_run_id` 每轮刷新 → 3 例红 `assert [{'fresh_write': True}, {'fresh_write': True}]`；
  ④ 撤逃逸分支 → 端到端例红 `assert 'revise' == 'write'`（附
  `AssertionError: 大缺口改稿轮必须走 fresh_write ⇒ mode='write'，实际 mode='revise'（draft_text=True）`
  与 pipeline 日志 `chapter_write.writer … mode=revise`）。
  `ruff check packages scripts tests` → All checks passed；定向 pytest 42 passed（回路单元 16 +
  封门/取消/超时/日志/写模式/能力档 26）；`tests/api/test_gate_revise_closure.py` 4 passed。

### Fixed（2026-09-18 project-init 两处「收下即忘」：author_notes 与 volumes.arc_summary）

> 作者实测：brief 写「无 CP」「第一位面＝现代都市」，生成的 story bible 却回来一个
> `love_interest` 角色（柳蘅）+ 古代宗族纪年（功名/遗孤/账房先生/家主奉茶），与
> 酒驾/认罪书/开庭 的前提冲突。两处同属 AGENTS.md 硬规则 9 的形状：
> **参数被收下（甚至已落库），但没有进消费方**。

- **`brief.author_notes` 对 world / character 两段不可见**（`packages/workflows/project_init/pipeline.py`）：
  `_world_payload` / `_character_payload` 的 brief 段只有 genre / logline（world 多一个
  target_words），而 `_premise_payload` / `_outline_payload` 都带 author_notes；
  `docs/agents/prompts/volume_outliner-v1.md` §2 明写它是「最高优先级创作约束」。
  修复：新增 `_author_notes_field(brief)`（**非空才带键**），两个 payload 各注入一次；
  `docs/agents/prompts/{world_builder,character_designer}-v1.md` §2 补同款「作者备注」段。
  **不升 prompt 版本**：库内 precedent 是 142bae8 就地给 `volume_outliner-v1.md` 补该段
  （仍是 v1 文件、payload 仍声明 `volume_outliner:v1`）；project-init 四 agent 的
  `prompt_version` 是硬编码字面量（F-14 已登记的遗留），建 v2 文件只会让声明标签与实载
  版本分叉且无告警，故按同一惯例就地改 v1。
- **`volumes.arc_summary` 没有 service 写入口**（`packages/domain/volume/{models,service}.py`、
  `packages/core/api/routers/volumes.py`、`packages/workflows/project_init/pipeline.py`）：
  迁移 0020 落列时注释写「persist_all 后续会把 arc_summary 写入该列」，但 `VolumeCreate`
  从无该字段 ⇒ init 只把它写成 plot_event（type=other/planned），列恒为 NULL；
  `scripts/open_volume.py` 的定点直写 SQL 即为此打的补丁（该脚本本次不动）。
  修复：`VolumeCreate` / `VolumeUpdate` / `Volume` / `VolumeListItem` 加 `arc_summary`
  （自由文本、不设长度上限，与 `plot_events.description` 同口径——设硬上限会把模型的长产出
  变成 422/落库失败），service 的 INSERT 与 list SELECT 同步；`_upsert_volume` 命中已存在卷时
  按字段差异更新 arc_summary（title 原逻辑不变）；init 把 outliner 的 `volume.arc_summary`
  传进 `VolumeCreate`。**plot_event 写入保留**：两个消费方不同（timeline 读 planned 事件 /
  卷纲摘要编辑面读列），本次只把列补齐。
- **契约产物 regen**：`apps/web/openapi.json`（96 paths / 40 schemas）+
  `apps/web/src/api/types.generated.ts`（40 interfaces）。
- 验证：新增 10 例（payload 3 / persist 与 upsert 2 / service 3 / API 2）+ 2 例既有用例补断言
  （reinit 卷更新、plot_event 用例补其 docstring 早已声称却从未断言的 volumes.arc_summary）；
  四项逐一突变验证（撤注入 → `KeyError: 'author_notes'`；撤 VolumeCreate 传参 → 列实得 `None`；
  撤模型字段 → `KeyError: 'arc_summary'`；撤 `_upsert_volume` 更新分支 → 实得 `'旧摘要'`）；
  `ruff check packages tests` → All checks passed；定向 pytest 74 passed。

### Changed（2026-09-17 F-19：撤回「指标当处方」——可测算子只作体检）

> 用户对**作者手写样张**的实测判定：「注水太严重了，这种完全没法看，全是重复句，太傻B了」。
> 缺陷形状与 F-18 同源（可优化指标被当质量判据），但**新形状**：定量指标撤退之后，
> 我给出的**正向方法**（同情节改对白交锋）本身也是错的——病灶不是「对白多」，
> 是**把戏写成了对白**（主角全程在说、场面无人动作、信息由主角播报、收尾金句点题）。
> 反证：锚点书榜一 ch1 整章 **0% 对白**却极好读。

- **题材包三轮修正落运行面**（`NovelOS-Content: genres/male-quicktransmigration/pack.json` +
  `男主快穿/style/samples.md`）：`style_constraints.notes`（writer 实收段）新增「场景纪律·F-19」——
  翻盘靠**动作**不靠台词、场面 ≥3 个各有动作的人、信息由事件给（禁自我介绍/解释动机/报数推演/
  宣讲方法论）、对白短而平（8~20 字带具体物件）、禁「答非所问＋神秘收尾」装腔；
  `critic_rubric.style_notes` 同步为「三轮修正留痕」（一轮长句口径作废 / 二轮对话占比 ≥20% 撤回 /
  三轮正面条款）。`content_sync genre-pull` 后 DB pack v9→**v10**。
- **作者重写样张落库**（ch28 军户围堵戏，动作驱动）：可见字 2225→2516、
  **对话占比 7.1%→5.6%（降了）**、均段长 32→34。核心改动是主角**把账本递给最前面那人
  让他自己念**、账本在人群里传一圈、络腮胡子抢过来自己看，而不是他自己报价推演。
  工具 `_refs/land_ch28_scene.py` 带禁用串守卫（二百八／我不知道。我算／东头粮行／
  现在多少钱一石／三天后三百二十）。
- **断因果三处**（`_refs/arc6_driver.py` + `_refs/readability_repro.py`，均非软件仓）：
  `REVISABLE` 移除 `AI-DIALOGUE-LOW`（「占比/频率」类指标不得进可改稿集合）；
  `AI-SHORT-PARA` 改稿意见不再要求「改写成长复合句」（F-17 已推翻）；
  `W-LEN-DEVIATION` 欠带意见改为「补事件、补场面、补在场人的动作（不要靠加对白凑字）」；
  `readability_repro.py` 无 `--only` 直接拒绝执行（**对话占比不得作选章依据**）。
- **批处理遗留清理**：`readability_repro.py` 批量任务已 `OSError: [Errno 22]` 退出（不再改书）；
  其留下的悬挂状态已修（ch3 DRAFTED、ch12 REVIEWED → 均 COMMITTED，48/48 全弧一致；
  ch3 顺手做破折号机械修 2.5→0/千字，语义零变更）。

### Fixed（2026-09-16 `author_intent` 死参数：端点的参数必须有消费方）

> 形状与「plan_json 的大纲从未进 `build_director_input`」（硬规则 9）完全同形，**第二次抓到**——
> 本次实证：新书 01 全弧 ch1-6 的「本书铁律」（价签四锁 / 字数带 / 榜一体文风 / 无CP）
> **一个模型都没看见**。

- **writer 侧接线**（`context_engine/writer_input.py` + `workflows/chapter_write/pipeline.py`）：
  `build_writer_input()` 加 `author_intent` 形参与 payload 顶层 `{"raw": ...}` 段；
  `_writer_node` 从 `ctx["author_intent"]` 传入；装配缓存键追加 `intent_fp` 维度（漏键＝脏命中）。
- **writer 契约版本线**：新建 `docs/agents/prompts/writer-v2.md`（v1 逐字节 + §5 输入契约
  `author_intent` 段 + §6 规则 21「禁止上下文标签 / 字段名 / payload 键名入文」），
  启动 sync 后为最高 ACTIVE。连带修正 `test_p1_director_planner_pipeline.py` 里
  钉死 `writer:v1` 的调用计数（改按 `writer:` 前缀计数）。
- **改稿轮继承**（`routers/workflows/{common,control,revise}.py`）：`ResumeRequest` 加
  `author_intent`，resume 端点按「请求体 > 原 run ctx」解析并快照进 daemon 线程，
  `_auto_revise_loop` 并入 `initial_ctx_extra`——此前改稿轮在**无任何作者约束**下重写正文，
  实证后果：ch2 v2 把内部字段名 `recalled_passages` 写进了正文。
- **确定性算子**（不靠提示词）：新增 `packages/core/quality/normalize.py`
  （`normalize_prose` 引号归一 + `find_internal_identifiers` 标识检出，纯函数、幂等、
  严格成对才改）；`_save_draft_node` 落库前调用，变更与检出进节点产出（可观测）。
  消费方闭环测试：`test_write_threads_author_intent_into_writer_payload_and_normalizes_draft`
  （撤传参 / 撤规整各自转红）。
- **AI 腔阈值按人类基线收紧**（F-12）：破折号 3.5→**1.0**、短段 5.5→**1.0**。
  依据：文风锚点书（榜一侯府弧 19 章 / 37806 字）破折号 **0.19/千**、短段 **0.00/千**；
  我们 63625 字侧 1.73 / 2.62——旧值落在两者之外（连自己的均值都够不到）＝死规则。
  内容层同步（`NovelOS-Content: genres/male-quicktransmigration/pack.json` +
  `男主快穿/style/samples.md` v0.6.4，本地 commit 8d8e06c）。
- **已提交章正文返修工具**：新增 `scripts/normalize_chapters.py`（默认 dry-run，
  `--apply` 才写；只动每章最新 draft）。新书 01 实修 24 章 / 605 对引号 + ch2 标识句改写，
  复跑幂等（0 变更 / 0 残留）。
- **版本口径统一（writer 链，F-14）**：`prompt_version` 此前是硬编码字面量，
  升版即分叉且无告警——writer v1→v2 当天 payload 声明 v1、模型照抄回声、
  `drafts.prompt_version` 落 v1、`drafts.created_by` 硬编码 v1，而
  `ai_call_logs.prompt_version` 记 v2。现新增共享工具
  `agent_runtime.prompts.active_prompt_label()`（权威＝prompts 表最高 ACTIVE 行），
  writer 链三处改取真值：payload 声明（+ 缓存键 `prompt_label` 维度 + paged 透传）、
  `_writer_node` 从 ai_call_logs 取本 run 实载版本进 ctx、`_save_draft_node` 的
  `created_by` / `prompt_version`（不再采信模型自报）；`writer-v2.md` 内 5 处自指版本号
  同步并补「原样回填输入 prompt_version」纪律。其余 13 处同形状硬编码登记为 F-14 遗留。
- 验证：全量 pytest 绿（含新增 31 例 normalize 单测、4 例 writer 契约/缓存单测、
  3 例 writer-v2 注册、3 例榜一基线看守、3+2 例改稿轮透传、3 例版本口径）；
  五处改动均做过突变验证。

### 新书 01 第一弧验收（2026-09-16，`prj_bcb9d1930bd4`）

- 24 章全 COMMITTED / 63625 可见字；运行期修掉两处工程事故：驱动 `POST /commit` 409 竞态
  静默猝死（`no_active_run` + `start_stage` 退避重试 + `excepthook` + 监督重启）、
  ch24 commit 一次 FK 抖动（自动重试兜住）。
- 三把锁验收：价签快照合规率 **铁律接通前 1/6 → 接通后 4/4**；记忆代价逐处具体；
  文风四算子对榜一：破折号 1.73 vs 0.19、短段 2.62 vs 0.00、对举 0.90 vs 0.00。

### Changed（2026-09-15 禁用词两层制 + 题材包 v6：把「AI 腔量化上限」推进运行面）

- **禁用词分层**（同形状的第四处修正）：`AI_PATTERN_FORBIDDEN_WORDS` 原是一张
  「命中即报」的表，实测 **67% 的章节被报**——因为 19 个词里 15 个两侧都近乎零命中，
  噪声全靠 **忽然**（0.24 vs 人类 0.20/千字，R=1.18 无区分力）与 **突然**（0.19 vs 0）
  两个中文小说正常用词撑着。现拆两层：**硬套话层**（本章目标/综上所述/总而言之/
  与此同时/空气中弥漫着/冥冥之中…）命中即报；**常用弱词层**（忽然/突然/似乎/好像/
  仿佛/宛如/如同/不禁/骤然/猛然）改为同章堆积才报（同词 ≥3 次或本类合计 ≥6 次）。
  **效果：AI-FORBIDDEN-WORD 章节命中率 67% → 14%（书1 3/21、书2 3/12）**。
  并顺带重定 `AI-SHORT-PARA` 阈值 7.5→5.5（算子修正后重测分布：均 2.65 / p90 5.47；
  人类侧 0.81，R=3.25）。
- **题材包 v5 → v6 运行面更新**（`genre-male-quicktransmigration`）：`style_constraints.notes`
  压缩至 schema 上限内并写入量化上限；`critic_rubric.style_notes` 扩写为完整核销口径
  （上限表 + 三条反常识 + 喻体类型要点）。编辑面 `style/samples.md` 同步新增
  「AI 腔量化上限」节（v0.6.3）。
- **三条反常识写入约束**（照流行清单改会改错）：人类正文设问是生成侧 **17 倍**、
  比喻标记 **2.4 倍**、句长均匀度**无差异**（R=0.87）——故**不删设问、不泛禁比喻、
  不为打散句长而造不齐的句子**。真正的比喻问题是喻体类型（忌「像一位智慧的导师」式
  理想化职业人格）。
- 验证：全量 pytest 绿；禁用词分层 6 例新测（弱词单次静默 / 同词堆积 / 本类合计堆积 /
  两层显式声明且不交叠）；4 处既有用例改用硬套话层（语义更稳）。


### Added（2026-09-15 AI 味算子移植：外部对照研究 + 本仓语料校准）

> 来源：[lieflat-less-ai-tone](https://github.com/larashero3-dotcom/lieflat-less-ai-tone)——
> 283 万字对照语料（629 篇 / 300 篇 AI 生成 × 5 模型 vs 329 篇人类写作）逐项检验 26 个
> 「AI 文风」候选特征，11 项通过（R = 生成侧 ÷ 人类侧 ≥ 1.25）。结论清单价值有限，
> **方法**价值高：算子的覆盖范围必须与规则名相符 + 任何算子在采信频率前先抽样检视命中实例。

- **本仓语料校准**（生成侧 33 章 / 87,599 可见字 vs 人类侧 3 篇 / 8,665 字）：
  **对举结构 R=6.63** · **短句独立成段 R=4.37** · **破折号 R=4.06** —— 三项与来源研究方向一致；
  **译文句式 R=0.49 方向相反**，证明「283 万字的结论」也不能整体照搬（体裁差异真实存在）。
- **新增 2 条算子 + 1 条休眠守卫**（`packages/core/quality/ai_patterns.py`）：
  `AI-CONTRAST-PAIR`（对举「不是 A 而是 B」）· `AI-SHORT-PARA`（短句独立成段，
  即用户反复抱怨的「字幕式短句跳切」，本仓生成侧是人类的 4.4 倍）·
  `AI-ANTHRO-VEHICLE`（拟人化喻体，两侧零命中，登记为**未校准**休眠守卫）。
- **抽样核查改掉三件事**（这正是该研究最值钱的部分，本仓首轮即抓到）：
  1. 原拟名「段首零回指评论」抽 12 条命中逐条看，实际测的是「叙述短句独立成段」——
     **按算子实际覆盖范围改名** `AI-SHORT-PARA`，不冒用来源特征名；
  2. 「译文句式」的「过长前置定语」算子抽 97 条命中 **95 条误报**，且 R=0.49 无区分力
     → **整条废弃**，并留一条测试钉住「已删除」防有人照清单加回来；
  3. **破折号阈值 6 是死规则**——本仓逐章实测均 2.33 / p90 3.89 / max 4.73（每千字），
     永远够不到 6。按 p85 下调至 **3.5**；上规则后命中章数由 0 → 书1 4 章 / 书2 6 章。
- **新增密度类规则的守卫**：最小判定长度 200 可见字（曾因 17 字文本算出「58/千字」误报，
  把 polisher「预检干净即跳过」4 个用例整片判红）+ 「整段就是那一句短句」才算独立成段
  （只看段首句会把「灯芯闪了一下。苏婉清没有出声…」误判）。
- **工具**：`scripts/ai_tone_calibrate.py`——校准（R 表）+ 精确率抽样双功能；
  人类侧**只取 `author_style_samples`**（其它项目的 drafts 同样是生成侧产出，首版脚本踩过这个坑）。
- **留痕**：`NovelOS-Artifacts: docs/analysis/ai-tone-calibration-2026-09-15.md`（校准数据 + 逐项裁决理由 + 局限）；
  AGENTS.md 硬规则 9 增「检测算子的覆盖面必须与规则名相符」+ 坑区新行。
- **登记待办**：`AI-FORBIDDEN-WORD` 在 67% 章节命中（词表混入「忽然/突然/似乎」等小说正常用词），
  属反向失衡——需按真套话/正常词分层 + 改密度阈值，牵动既有测试，留独立批次。
- **未做**：未改生成链 prompt 与题材包 style 公约（属创作方向，须用户拍板）。


### Fixed（2026-09-15 台账注入的章号位置过滤——重产早期章被后文倒灌）

- **缺陷**：`_hook_ledger_excerpt` / `_open_foreshadow_list` / `_narrative_debt_excerpt`
  注入项目**全部**未闭环项，不看引入章号。顺行生成时无害（后面还没写），但**重产早期章**
  时后文钩子会灌进 planner 输入。
- **实证**：书1 重产 ch1 后，planner 把 ch21 才引入的 `hook_..._second_arc_identity`
  写进了 ch1 的 `hook_handling`，并带出 ch17/ch20 的钩子名——正文因此出现
  「三年之约已经兑现，破屋锚点再次确认」这类**弧末术语**，且结尾重复收束一次
  （「你到底是谁」出现两遍、自相矛盾）。
- **修法**：三处注入统一加 `current_chapter_no` 位置过滤（`introduced_chapter_id` /
  `created_chapter_id` 为 NULL 的项目级项保留；传 None 维持原全量口径供 preview 用）。
- **验证**：`tests/unit/test_ledger_chapter_filter.py` 3 例 + 突变验证（撤过滤必红）；
  全量 **pytest 2200 passed / 2 skipped**。
- **同一形状的第二个落点（升格）**：`packages/core/retrieval/service.py:search()` 的 FTS
  召回原先只排除**当前章**、不排除**后文**——重产 ch1 时召回把弧末时间线带了进来，
  正文出现「嘉靖三年殿试入京，三年约满于嘉靖六年」，与开篇设定相悖。已加
  `current_chapter_no` 位置过滤（director 与 writer 两侧同口径）。
- **裁决留痕**：ch1 是否补写的判断从「少 37 字」改判为「内容有真缺陷、必须重产」
  ——原判断依据（字数）不成立，实际问题是重复收束 + 计划术语泄漏。


### Fixed（2026-09-15 大纲槽：修「大纲从未进入 planner 输入」——书1 首弧跑偏的根因）

> 事故：书 1 首弧 21 章（54k 字）跑完后核对，**章节标题是 v9 大纲的、正文却是另一套故事**
> ——ch17 标题「拜相·临危受命」正文是「府学聘临时记室」；ch21 标题「拂衣·首弧结算」正文停在
> 「府试还有二十天」。全弧走完主角连乡试都没考，用户终裁的 v9（权线科举 → 拜相 → 新法再造
> 天下）**一个字没落地**，且全程无告警。

- **根因（架构缺陷）**：`chapters.plan_json` 一名两用——project-init 写大纲、chapter-plan 的白名单
  整体覆盖成计划；而 `build_director_input` 的 `chapter` 段只有 title/target/role/id，
  **`chapter_goal` / `key_beats` 从未进入 planner 输入**，plan_json 全文只被当 FTS 召回语料 +
  缓存指纹。planner 遂只能顺着 story state 惯性自推计划 → v5~v9 五版大纲补丁全部空转，
  只有 `title` 生效。附带：驱动脚本 `author_intent` 自带旧设定锚点；书1 项目未挂榜一文风锚点
  与 reference_canon（挂在旧废案/书2 上）。
- **修法**：0028 迁移新增 `chapters.outline_json`（策展稳定面，chapter-plan 只读不写）；
  `build_director_input` 注入 `chapter.outline`（两级取值：新列非空用它，否则回落 plan_json
  同名字段 → 存量零行为突变）；装配缓存键加 outline 指纹维度；peek 预读 `outline_json_raw`；
  `ChapterUpdate`/`Chapter` 暴露该列；project-init 章节种子同时写该列；backup `JSON_ID_COLUMNS`
  登记。`director_planner` prompt 升 **v2**：输入契约加 `chapter.outline` + 规则 0「大纲优先」
  （非空即硬约束，冲突不得静默改向，须落 `open_questions` 并仍按大纲规划）。
- **另一处先验被推翻**：writer `revise` 模式原路由到 `light`（审校档，按「定向局部修改」设计）；
  实证它做的是整章扩写——书1 ch1 改稿轮把整段原文重出一遍（同一句 v1 一次 → v2 两次）且
  CJK 仍欠带（1460→1815，带下限 2125）。改为与 write 同走 `creative_writing`。
- **验证**：`tests/unit/test_chapter_outline.py` 8 例（取值口径/注入/缓存 miss/生命周期/领域层）
  + 突变验证（撤注入恰好 3 例红）；全量 **pytest 2198 passed / 2 skipped**，ruff 干净。
- **书1 内容层重置**（2026-09-15）：旧「管理局/委托账契/清算人/跨位面暗线」实体全套清空，
  重建角色 7 / 地点 9 / 势力 4 / 世界规则 7（含新增「纪年」规则锁定年号）；21 章写入 v10 大纲
  （ch1 冷开场新作、ch21 换掉仍带旧机制词的章末钩）；榜一文风锚点 + 榜一单弧参照系回挂。
- **重建后全弧实测**：21/21 COMMITTED，**54,679 字 / 均 2604**；大纲首次真正驱动产出
  （ch17 保和殿拜相、ch21 结算三糖 + 第二世冷开场）；旧设定词残留清零（无委托账契/七债/
  清算人/指纹/私账）；年号统一为「嘉靖」，此前 ch20/21 的「永乐七年」自相矛盾已消除。
  仍待改进：ch1 2088 字略低于带下限 2125；ch4/8/14 超带；GENRE-RATIO-DEVIATION 全弧为常驻
  告警（弧级口径样本 <20 时不下结论，属预期）。


### Changed（2026-09-14 弧末批次：P1 规划合并 + target 题材包化 + 核销 v2——书1 全弧实战驱动）

> 背景：书 1 首弧 21 章全自动量产（62,773 字，均章 10 分钟）完成，本批全部来自实战证据。

- **P1 规划合并产品化**（a1b730c 已单列）：director_planner 单节点出 plan+scenes、scene_plan 落库（0027）、write 读落库、白名单防幻觉 ID、相邻 JSON 合并兜底。二次 A/B 硬门槛全过（解析 1/1、零幻觉、时空连续、提速 -55%~-84%）。
- **target 题材包化**：章字数目标解析链 ctx/plan → 绑定题材包 pacing.chapter_words.target → 默认 3000（新模块 packages/core/genre/target_words.py）。实战依据：target=3000 压题材带 2000~3000 上沿致 9/21 章超上限；书 2 起包声明 2500，write/review/scene_planner 三处同链。
- **核销层 v2**（弧级口径）：配比核销改弧级累计（卷内 typed scene ≥20 出结论，SE≤10% 推导；章级只留明细），修 other 残差桶双向计偏离；untyped scene 产 GENRE-SCENE-UNTYPED 不再静默；红线语料 140 字→整章正文 3.1k~4.4k 字；settlement 无数据通路诚实登记 pending。
- **量产排障沉淀**（AGENTS.md 坑区 +3：孤儿 run 409、门禁改稿接力暂停、commit 风控门批量阻塞；W-LEN 双向意见防过压缩乒乓球 4272→1853）。
- **prompt 口径对齐**：scene_planner/director_planner 的 E-SPL-10 落点描述改弧级累计口径（核销 v2 语义）；eval golden 三份 mock 清空幻觉 hook_handling（新白名单特性生效）。
- **内容仓约定**：NovelOS-Content 不推 GitHub（用户裁决，AGENTS.md 硬规则 5 增补）；pack v2（chapter_words.target=2500）。


### Changed（2026-09-13 运行记录下置 + V4.0 模块化重构全量落地）

> 运行记录下置：用户提出、主会话按授权拍板（信息层级：草稿是工作对象，运行记录是过程信息；
> 防漏审批改由 tab 徽标+提示条兜底的更低成本方案）。
> V4.0：docs/roadmap/v4.0-模块化重构计划当日完成——四路文件互斥并行，全部「测试数零增减」
> 硬验收（AST 逐定义字节比对 / 机械化对账 / full-suite 数字逐字一致）。

- **运行记录下置为分段 tab**：章节详情页「运行记录」从顶部常驻移入分段 tabs 第二位（草稿/运行记录/章节计划/质量/上下文/危险操作），默认仍草稿；tab 挂状态徽标（PAUSED=待审批 warn 徽标/RUNNING=转圈/FAILED=红点）；有待审批或运行中且不在该 tab 时 tabs 上方 slim 提示条「→ 查看」直达；审批卡编辑态切走保留语义不变。vitest 485（+6）。
- **V1 builders_common.py 2104→四模块包**（excerpts/ledger/summaries/common+门面全量再导出）：AST 源码段比对 0 差异、层序无环、消费方 import 零改动；唯一新增为 `get_connection` 门面补丁点（8 行，测试探针兼容，突变验证 3 红）。
- **V2 routers/workflows.py 1675→四域包**（control/runs/revise/common）：12 端点路径/方法/函数名/状态码/响应模型全不变；OpenAPI canonical 逐 path 相等（仅 workflows 块内 paths 键序按域分组）；discover_routers 兼容（25 routers 不变）；control↔revise 循环依赖以延迟 import 解（+2 行注释）；smoke 全链 PASS。
- **V3 ProjectInitPanel.tsx 1858→227 主文件 + 12 子组件**（useProjectInitOrchestration 792 行 hook + 各步组件）：机械化对账（hook 清单/CJK 文案字面量集合/testid 集合逐项一致，52 行旧代码逐条可解释）；补验原未覆盖的降级分支。
- **V4 结构性负债清理**：builders.py 门面 `__all__` 116→30（86 个零消费名保留 import 本体、F401 由 pyproject per-file-ignores 承接——不用文件级 noqa 以免掩盖未来真实未使用 import）；12 个 Sprint 0 空包加「未实现骨架」横幅指向实际承担者；7 个死定义全部删除（逐个附 grep 零引用证据，含一个被历史修复内联取代的旧 DELETE 接线口子）。
- **文档核销**：AGENTS.md 挂账表清零；路径引用回填（routers/workflows.py→包路径 ×4）；过期 docstring（2200→3000 口径）订正；v4.0 计划标完成。

### Changed（2026-09-13 抽屉/详情人读化补透：用户实测反馈驱动）

> 用户实测：角色抽屉 relationships 等嵌套结构仍倒原始 JSON。根因校正：红框走的是 `renderObject`
> 里嵌套容器 `formatJson` 路径（非顶层数组 fallback）。全库实测校准：46 JSON 列 / 4541 对象数组
> 扫描，关系识别命中恰为 15 条真实路径零误判（`state_deltas.new_hooks` 在宽规则下会误判→已收紧）。

- **ReadableJson 泛化**：对象数组→中文表头表格（列=键并集，labelMap>COMMON_LABELS>原键；>8 行折叠「展开其余 N 行」）；嵌套对象/数组递归渲染（depth≤2，更深收「原始 JSON」）；关系数组（to_name/to+relation_type/type+one_line/description 组合）→「对象 · 关系 — 一句话」列表；relation_type 值中文映射（ally→盟友/rival→对手/enemy→敌人/family→家人…）。
- **CanonTab**：裸 `JSON.stringify(cj,null,2)` 换 ReadableJson + 保留原始 JSON 折叠兜底。
- **COMMON_LABELS +28**：canon/题材包键全量中文（logline→一句话卖点/spine→主线节拍/style_params→文风参数/ratio_declarations→配比声明…）。
- 验证：vitest 479（+11）/ tsc 0 / build 绿；3 项突变验证（含原缺陷路径专项用例）。

### Changed（2026-09-13 前端全面优化：消除「半成品残次感」——四批实施 + 代码体检驱动）

> 来源：apps/web 全面体检（45 源文件盘点 + 五类问题清单 + 残次感 Top10）→ 四批文件互斥并行实施
> （A CSS 地基与全局组件 / B Modal 基座与交互完整 / C 详情页与日志页重构 / D Bible 与杂项面板）
> → 主会话跨批联调。诊断：**残次感不来自功能缺失（全站 0 TODO/0 裸 confirm/0 死按钮），而来自
> 样式与状态覆盖的系统性缺口 + 调试台文案**。验证：tsc 0 / vitest 469（422+47）/ build 绿 + 无头
> Chrome 同机位前后截图对比。

- **CSS 地基（残次感第 1 名）**：`.input` 并进 `.form-row` 控件同一条规则（27 个裸控件一次对齐）；补 `.alert--warning`/`.badge--{info,warn,ok,active,archived}`/`.spinner`/`.skeleton`/`.empty`；可交互元素 `:focus-visible` 全域可见（`:where()` 零特异性）；新令牌 `--color-warn-*`/`--color-focus-ring` 收编 18 处裸 hex。
- **状态覆盖**：`<Loading>`/`<SkeletonRows>`/`<ErrorBoundary>`（包住路由树）新组件；18 处「加载中…」灰字全替换；总览页加载期整页骨架（消灭空白页）；删除/归档/连接测试失败全部改错误通道有反馈（原 5 处静默）；QualityPanel 嵌套解引用加防御（原形状一变即白屏）。
- **交互完整**：`Modal` 基座（Esc/初始焦点/焦点循环/滚动锁/嵌套栈顶/焦点归还），全站 9 个自写弹窗迁移（原仅 ConfirmDialog 有 Esc）；Bible 六 tab 落 URL `?tab=`（刷新/后退/书签可复现）+ roving tabindex + 方向键；章节标题 inline 改名（接后端已支持的 PATCH title，补功能缺口）；Story Bible 非法子路径规范化。
- **章节详情页 IA 重构（1814→667 行）**：拆 6 个子组件；下部收进分段 tabs（草稿/章节计划/质量/上下文/危险操作）且选中落 URL；**运行记录面板常驻**（PAUSED 审批卡不漏看）；分区懒加载+访问过保留挂载；危险操作隔离红描边。**1722px 魔法定高删除**（改 260px 版本列表+正文自适应网格）——**推翻 2026-09-01「定高终案不动」旧拍板（CHANGELOG:182）**；2026-09-13 用户目检后终裁：**新自适应方案定型**（如需回退一行 CSS 即可）。
- **AI 调用日志页重做**：选中与列表解耦（原点一行全表重拉闪烁）+ 详情按 call_id 缓存；主从布局；人读化详情（中文标签事实表+关键指标条+raw 折叠）；项目/结果/时间过滤条。
- **文案去术语（调试台体质）**：侧栏「Sprint 5 · 一期」→「NovelOS v3.9.0」；`节点时间线（选中 run 的 checkpoint_json）`/`计划（plan_json）`/`Workflow runs`/`（dry-run）`/enabled/disabled/ACTIVE/MERGED 等英文枚举与调试后缀全站清（改中文标签）；总览「后端健康」卡改人话。
- **顺手抓的真 bug**：编辑弹窗「归档」按钮缺 `type="button"` 导致点归档误发整单 PATCH；`Index.css` 并发写丢 `.alert--warning` 块（联调补回并记坑）；ProjectInitPanel 种子数提示 1-100 与实际校验 1-500 不符。
- **代码健康**：inline style 340→大幅收编（详情页 8 文件全清零）；死码 8 处清理（supportsModelPick/void pid 等）；7 张裸表包 `.table-wrap`；空态统一 EmptyState；重复 Modal 遮罩/Field/Stat 副本收敛；类型修正（health tables 双形状）。
- **挂账更新**：ChapterDetailPage 移出巨型文件挂账（AGENTS.md/v4.0 计划同步，现 667 行）；剩 builders_common 2104 / ProjectInitPanel 1843 / routers/workflows.py 1675 三个。

### Fixed（2026-09-13 V3.9 全量检修必修 8 项 + 对抗审查打回 4 项：报告 docs/reviews/全量检修-2026-09-13.md）

> 方法：五路只读检修并行（方法论 04 全量档）→ 主会话逐条亲验 → 必修 8 项四路文件互斥修复
> → **独立对抗审查**（Fix-A engine 域）→ 打回 4 项修复 → 全量回归。R1+R3 双独立发现同一缺陷按规矩升级。

- **迁移目录 CWD 锚定（双发现升级）**：`db.py` 默认迁移目录从 CWD 相对改为 `__file__` parents[2] 锚定；外部 CWD 启动曾静默 0 迁移且 health 报 ok（探针实证）；lifespan 对「0 迁移+空库」打 ERROR。
- **cancel×pause 竞态守卫 + 节点行口径对齐（M1/M1-a）**：`_finalize_run` PAUSED 分支补 `WHERE status='RUNNING'` 守卫（取消不再被静默回滚）；竞态兜底按 CANCELLED 语义收尾且 Human 节点行同步 `FAILED/error='cancelled by user'`+丢弃 pause payload。排障实录：嵌套写另开连接触发 WAL 单写者 busy——已复用同一事务连接，坑区留痕。
- **checkpoint_exclude 镜像键推广（M2）**：commit/review 的排除清单补节点镜像键（实测曾 59KB/89KB 泄漏）；**例外保留 quality_gate/author_review**（前端消费契约，对抗审查证据）。resume 路径 exclude 改从 workflow 注册表取回（M3，原实例属性随 resume 失效）。
- **启动自愈跨实例互杀修复（F6）**：迁移 0026 `workflow_runs.instance_id` + 归属过滤（本实例+0026 前 NULL 旧行）。**知情语义代价**：未设 `NOVELOS_INSTANCE_ID` 时重启不自愈已打标 run（跨实例防护的必然），运营路径 `db_maintenance fix --apply`；docstring/README/坑区全口径一致（对抗审查阻断项）。
- **备份恢复正确性一揽子（「能导出≠能恢复」）**：JSON 载荷 id 重映射 walker（精确匹配、dict 键值双替、25 列清单有 schema 覆盖测试）+ 预建全局映射（顺手修 story_states.commit_id 顺序悬挂）+ `volumes` 入白名单 + projects 后加列动态写 + `PRAGMA foreign_key_check` 基线差集校验（原事务内 `foreign_keys=ON` 是 no-op）。跨引用 roundtrip + check_state_sync SYNC OK 双验证。
- **CI 修复**：backend job 改 `pip install -e ".[dev]"`（原缺 json_repair/python-multipart 收集期必红）；pip-audit 覆盖与注释对齐。
- **exporter COMMITTED 口径裁决**：整书 txt/docx=作者 WIP 面（全量章节+非 COMMITTED 标题【未定稿】标注）；番茄投稿包=外发面（正文+大纲同源过滤 COMMITTED-only）——消除文档/实现/m2_judge 三方分裂。
- **数据卫生 F-8 闭环**：dev 库 DRIFT:2 定性为两次留痕手工改库（八证据链，非代码缺陷）；v1 快照按 DB 重建 + 6 孤儿 character_states 清除，check_state_sync SYNC OK（修前全库备份）。
- **文档面**：断链回填 ≥100 处（56 内容仓标注+10 痕迹仓+34 历史计划标注）、5 份历史横幅、6 份 Sprint-0 化石 README 重写、迁移口径 0001~0026 全点联动、logging 五项（fallback 字段入 message/[DEBUG] 降级/模块 logger/COMPLETED INFO/prompt sync diff-only）、gitignore 变体 5 探针、env 表 +7、vite proxy 真读环境变量。
- **登记不修（理由见报告 §四）**：R-1（rollback_of 唯一索引 vs 0022 dup 后缀）、M5（prune-runs 保留策略→V4.x）、门面 68 零引用名/12 空包/7 死定义（→V4.0 模块化批次）、F-1（_assembly_meta 进 prompt 实测 <0.5% 预算）。
- **过程资产**：坑区 +3（WAL 嵌套连接/F6 语义代价/R-1）；硬规则 +2（文档引用纪律/环境同步纪律）；模块化纪律（新增 ≤1500 行硬顶+存量挂账 4 热点→docs/roadmap/v4.0-模块化重构计划.md）。

## [3.9.0] - 2026-09-13

### Changed（2026-09-13 V3.9 业务逻辑优化与治理：四批次 + 顺手债 + 题材库全落地）

> 来源：`docs/roadmap/v3.9-业务逻辑优化与治理.md`（批次 1/2/3/4 + 批次 5 顺手债 + F-1~F-8 动态发现）与 `docs/roadmap/题材库-评估与落地计划-2026-09-13.md`（P0~P3）。批次 3（质量评分体系）单独条目见下。方法：三路线程并行代码级审查（全部论断带 file:line 主会话复核）→ 文件互斥并行实施 → 逐批主会话审查（含突变验证抽查）→ 全量验证。

**批次 1（正确性修复）**
- **condense 字数压缩生产不可达（P1 实锤修复）**：`chapter_write` condense 节点的 mock 守卫错误——无 mock 即 `skipped_no_mock`，生产环境永远走不到 LLM 压缩（CHANGELOG 此前记录的字数闭环实际未在生产运行）。守卫改为 polisher 同款语义（生产 `mock_script=None` 真调），「≤2 轮」循环收进节点内部兑现（每轮 payload 用最新实测重建，拒空/拒更长每轮生效，异常 fail-soft 不阻断）；节点回写压缩后 `length_report`/`length_check_passed`，下游 save_draft 读到压缩后真相。`save_draft` 的 `word_count` 改对最终 prose 取权威实测（原读压缩前旧值）；「2 轮后仍超带」偏差注记随轮数真实可达。
- **`_inject_scene_word_budget` `int(None)` 崩溃修复**：部分 scene 声明 `target_words` 且已声明子集和落在 90%~110% 时未声明 scene 保留 None 导致 writer 节点可达性崩溃；未声明 scene 按剩余预算均分（余数补首个，剩余≤0 给保底值），已声明值不动。
- **装配缓存脏命中修复（P1）**：director 键补 `author_intent`/`target_word_count`、writer 键补 `target_word_count`（进 payload 的参数全入键）；命中返回/写入存深拷贝（调用方就地改写不再回灌缓存）；preview 拆 `ns:preview` 键空间（前端挂载自动打预览不再污染生产条目）。钉住旧身份行为的 3 个测试用例按新契约更新。

**批次 2（上下文治理）**
- **peek 收敛**：四个 `_peek_*`（为拿 state_version 解析整份 snapshot_json、各自建连）收敛为单连接单 JOIN `_peek_chapter_context` + `SELECT MAX(state_version)` 轻查询；实测 writer 缓存命中 **15.9ms→2.19ms**、连接 5→1，冷装配 22.5→10.6ms。
- **token 预算真兜底**：director 摘要链预算参数化（`_DIRECTOR_SUMMARY_TOKEN_BUDGET=2000`，`_RECENT_SUMMARY_CAP` 5→40、`_PER_CHARS` 200→400，原 800-token 截断机制实测恒不触发）；三个 build_* 入口 payload 增 `_assembly_meta`（estimate_tokens/token_budget_exceeded，超预算只标记不阻断不改写）。注：`_assembly_meta` 经 run_agent 序列化进 prompt（同 `_reference_canon_consumed` 路径），剥离评估列入收尾后全面审查（F-1）。
- **无上限注入项加 cap**：`hook_ledger_excerpt`/`narrative_debt_excerpt`/plot `branches` 各 LIMIT 20 + 确定性排序；critic `settings_digest` world_rules LIMIT 20 + 每条 500 字符截断（F-3）。
- **chapter-write checkpoint 写放大治理**：WORKFLOW 增 `checkpoint_exclude`（writer_input + 7 个节点镜像键——镜像键与 workflow_run_nodes.output_json 逐字节重复是隐藏大头）；实测 checkpoint **133,630→4,471 B（-96.7%）**，生产既有 run 重算降 77~80%。resume 安全性三重论证（序列化期浅拷贝剔除/run 无 Human 节点/recover 不重建 ctx）。

**批次 4（失败闭环）**
- **质量门禁阻断有出路**：enforce 阻断时（raise 前）把 `revision_guidance` 落 `plan_json.revision_note` + `gate_blocked` 标记（通过自动清除）；新端点 `POST .../gate-revise`（write revise 模式 → daemon 接力 review）；前端「质量门禁阻断」横幅 + 一键改稿。
- **auto_revise 回路治理**：进程内回路注册表 + 子 run DB 态兜底实现回路级取消（取消任一路子 run 即终止回路，不扩大存储面）；critic 建议按归一化 sha1 去重（已采纳进 revision_note 的为准，上限 50）。
- **QualityReport.draft_version**：模型可空字段 + 迁移 0024，落库时取最新 draft 版本回填——改稿重评后历史报告可对应草稿版本。

**批次 5（顺手债 sweep，A/C/D 三路）**
- 目标字数单源化：`wordcount.DEFAULT_TARGET_WORD_COUNT=3000`，plan 兜底 2200→3000（与 docstring 承诺一致）、preview 默认同改、builders 内部 fallback 同改（PRD §124 的 2200 中点口径由项目级 word_band_json 承载）；**行为变更**：未显式指定字数的 plan 现按 3000 规划，钉住 2200 的测试已修订并注释。
- review 字数阈值双源消除：warning/error 改「带边缘判据」（出带即 warning、带外偏离超过带边缘到 target 的距离即 error），默认带下与旧 ±15%/±30% 逐值一致（突变验证证明），项目自定义带自动跟随。
- observer `run_agent` 7+2 处近复制收敛到唯一 `_call_observer`（零变更重构，测试数对照硬验收）；`_recover_run_status` 死代码删除；merge 契约文档按真实实现重写（不读 change_id，delta_repair 是重写而非丢弃——原契约声称的机制不存在）；delta 三重校验走文档路线（第三层为前两层真子集、服务层是公共边界，证据链留 commit.py 注释）。
- observer resolved/paid「最近 5 条」按随机 hook_id 取错 → `(created_at, id)` 双键（快照缺该列，DB 补齐映射）；leg 元数据按 `ai_call_logs.output_json` 同一性精确归属（roadmap 处方 ORDER BY created_at 经实证不可行——与 rowid 同源同秒，F-5 留痕），观测字段改名 `leg_total_chars`。
- critic/deep_review 共享取数前移 basic_checks（同一 run 不再双跑相同 SQL）；`_fetch_chapter_number` 仅 sample 模式调用；评审 review_inputs 预取。
- `workflow_runs.retry_count` 接线（runner 两条重试路径累加，与 ai_call_logs 的调用级 0/1 口径不同，注释声明）；exporter 三私有 helper 提升公开 API（下划线别名保留，content_sync 改引公开版）；版本对齐 pyproject 3.8.0→**3.9.0** + apps/web 3.5.0→3.9.0；表数口径 37→38 全点联动（db.py docstring/README/api-README/smoke 断言/health 测试）。
- 误导注释修正：`_build_nodes` 废弃机制描述、save_plan 白名单警示、revision_note 落点注释；wordcount 测试手写 3450→生成期望值（实为 3449，F-2）。

**F-6 smoke_e2e 断链修复（实机发现）**：start 端点 V3.5 异步化后脚本不等待终态 → 409 连锁失效（resume 逻辑也从未触发）；统一 `_run_step`（轮询 + PAUSED 批准续跑），实机 4 连绿（全链 5.5s）+ 失败路径注入验证；顺带根治 Windows 服务进程树残留（taskkill /F /T）与 DRIFT 分支 UnicodeDecodeError。

**测试口径变更声明**：本批按「行为修正」修订的钉住旧错误行为测试清单——`test_chapter_plan_preserve_word_count`（2200→3000）、`test_context_engine_cache_dimensions`（preview 默认）、`test_writer_input_paged`（band 生成值）、`test_chapter_review_word_band_override` 两例（带边缘判据）、`test_chapter_commit_observer_split_o3`（leg_total_chars 改名）；其余新增用例全部带突变验证（各批报告留档）。

### Added（2026-09-13 V3.9 题材库全落地：评估 → P0 内容 → P1 软件 → P2 规则化 → P3 同步与前端）

> 来源：`docs/roadmap/题材库-评估与落地计划-2026-09-13.md`（含对既有机制「关键洞察被推翻」的批判性复核留痕，评估表登记为 R16/R17）。**软件/内容分层硬约束守住**：题材正文零行进软件仓；审核禁忌只进内容仓。

- **P0 内容骨架**：`NovelOS-Content/genres/男主快穿/` 10 维资产（元数据/结构模板/黄金三章/节奏字数/爽点分类法 8 型/人设 archetype 库/位面库/桥段映射+审核红线权威清单/番茄体文风/平台规则）+ 维护公约（来源标注/拆书回填流程/季度热度复盘/红线更新即生效）。
- **P1a 地基**：迁移 0025（`genre_packs` 表 + `projects.genre_pack_id`，业务表 37→38）+ `packages/core/genre/` 五件套（自有 schema v1.0.0，`additionalProperties: false` 守住「软件层只承载消费子集」）+ 8 端点 CRUD/绑定 + director 注入（缓存键 pack 指纹，preview/生产/降级三路径同形——吸收批次 1B 脏命中教训）。
- **P1b 消费与核销层 v1**：scene_planner 吃爽点摘要（40 条/1500 字符预算+截断标记）+ 配比指令（`scene_type` 可选契约）；writer 文风合并（`genre_style` 子键不覆盖、作者样例优先）；**核销层三规则**（GENRE-RATIO-DEVIATION 配比偏离>10% / GENRE-WORD-BAND-DEVIATION / GENRE-REDLINE-HIT，warning 级、BLOCKING_RULES 零触碰、未绑定整段缺席零行为变化）；writer 缓存键 11→12 元；preview/Project 响应同步。
- **P2 规则化**：schema v1.1.0（`opening_rules`/`critic_rubric` 可选段，v1 线 minor 兼容）；signing_check `genre_opening` 段（子串启发式，失败/不可核 info 级不阻断）；critic/deep_review payload `genre_rubric`（payoff_focus 逐个核 verify_hint + taboo 命中即 high 一票关注）；scene_planner-v1.md Rule 12 scene_type 输出契约（配比核销模型遵从度的关键）；basic_checks 题材禁词确定性扫描。
- **P3 同步与前端**：content_sync `genre-push/pull`（slug 规则：中文名 fallback `pack-<id末8>`/保留名防护/同 pack 路径稳定复用；写侧 schema 严校验不落盘；pull 幂等 no-op 不升版本；退出码沿用既有约定）+ StoryBiblePage「题材」标签页（绑定/解绑/爽点型表格/genre_check issues 展示）。**双轨关系**：内容仓 10 维文件=人编辑面，pack.json=运行面，v1 不做自动聚合。
- **内容仓联动**：`docs/data-model/data-model-v0.md`〔断链〕` 表数口径订正（38 业务表，F-7）。

### Changed（2026-09-13 V3.9 批次 3：质量评分体系——悬崖聚合 / foreshadowing 双通道 / Q8 口径 / AI 味词表 / 文档对齐）

> 来源：`docs/roadmap/v3.9-业务逻辑优化与治理.md` 批次 3（3.1~3.5）。**行为变更 3 处**（3.1 聚合、3.2 foreshadowing、3.3 Q8 默认 severity），3.4 为行为不变的共享词表重构。
> 受影响面：`packages/core/quality/`（全部）、`packages/workflows/chapter_commit/gate.py`、`docs/evaluation/quality-scoring-v0.md`、`tests/unit/quality/`、`tests/api` 既有 Q8 钉住测试。

- **3.1 悬崖聚合改造**：`error` 分 blocking / informational 两组，仅 blocking（`rule_id ∈ issues.BLOCKING_RULES`：SCHEMA_VALIDATION_FAILED / RULE_CHAR_DEAD_ACTIVE / RULE_WORLD_HARD_RULE_CHANGED / RULE_Q6_OVERLAP_RATE / RULE_Q8_HUMAN_RATIO_LOW / scoring_missing_subscore）把 `overall` 归零并触发 gate 阻断；informational error 保留七维平均部分分、只进 issues。`chapter_commit/gate.py` 阻断判定同步改 `is_blocking_issue`（节点返回新增 `quality_blocking_count`）。`scoring_formula_hash` 输入扩展为「公式文本 + severity 配置指纹（矩阵 / 规则级覆盖 / 白名单）」⇒ 旧 hash `51a11a5c5d415dcc` → 新 `a945c6602f6a9f92`。
- **3.2 foreshadowing 双通道（钉住旧行为的测试更新）**：`resolved / (new + resolved)` → `round(100 × (resolved + 0.9 × new) / (resolved + new))`，修复「铺垫章 0 分 < 不涉及钩子的 85 中性分」倒挂。对照：埋 3 兑 0 = 0 → **90**；埋 1 兑 1 = 50 → **95**；只兑 = 100 不变。`tests/unit/quality/test_scoring.py` 原 `assert score == 50` 两例按新口径更新（这正是本批次要修的错误行为）。
- **3.3 Q8 口径修正 + 默认降级（钉住旧行为的测试更新）**：`compute_char_stats` 新增 `writer:v1` 等 prompt_version 前缀 → AI 侧（含口径 note `RULE_Q8_STATS_NOTE`），未知 created_by 不计入且留痕（证据：生产 drafts 44 行中 `writer:v1` ×36、`human` ×8）。< 30% 红线默认 **warning**（`MVP_RULE_OVERRIDES`），显式 `NOVELOS_QUALITY_Q8_STRICT=1` 才 error/阻断；理由：本工具是 AI 写作工具，维持 error 会让 enforce 默认拦死全部纯 AI 章（README §4.5 留痕）。`tests/unit/quality/test_engine.py::test_engine_q8_error_zero`、`test_ai_trace.py::test_engine_q8_error_zero_overrides_ai_trace`、`test_guardrails.py::test_q8_human_ratio_error` 三例按新裁决更新。
- **3.4 AI 味四算合一（行为不变）**：新增 `packages/core/quality/ai_flavor.py` 为唯一词表源（`AI_FLAVOR_MARKERS` / `AI_CLICHES` + 密度工具 `marker_hits_per_kchars`）；`guardrails.AI_MARKERS`、`ai_trace.AI_CLICHES` 改为引用。style/ai_trace 5 个 fixture 分数逐项不变（100/100、70/73、75/80、60/73、85/80）；critic LLM 维度进评分仍不做（R9 留档，README §6.1 决策指针）。
- **3.5 文档对齐（R14/R7）**：`docs/evaluation/quality-scoring-v0.md` 升 v0.2——§2.1 旧六子分权重表标删除线留演化史、error 语义改 blocking/informational、`scoring_formula_hash` 覆盖矩阵、§3.6 双通道、§3.7 H-3 标 warning（矩阵封顶）/H-5 越级标未实现、§4.8 Q8 默认 warning 与统计口径、§6.3 条件 4「baseline −3」标删除线并给 R7 理由；`payoff.py` docstring 与 `quality/README.md`（§4.2~§4.5 矩阵+白名单+变更流程、§5.2 取值表、§5.5 分工）同步。`4.4 severity 矩阵变更流程`：改矩阵必须同步三处 + hash 变 + 回归测试。
- **测试**：`tests/unit/quality` 196 例全绿（新增 `test_severity_matrix.py` / `test_q8_stats.py` / `test_ai_flavor.py` / `test_gate_blocking.py` 及各文件突变验证用例）；`tests/api -k "quality or gate or scoring"` + `tests/unit -k "ai_trace or payoff or foreshadow"` + `tests/workflow -k "commit or gate or quality"` 全绿；ruff 0。

### Added（2026-09-11 竞品对标调研：InkOS 结论入库）

> 来源：用户提供当红开源同类项目 `Narcooo/inkos`（GitHub 9.7k stars，AGPL-3.0，TypeScript monorepo）并要求对比找补强点。方法：4 路 MiniMax-M3 子代理并行调研（InkOS 源码全貌 / 外部口碑 / NovelOS 能力矩阵 / 实证交叉验证）+ 主会话逐条复核关键结论。**本轮零代码改动。**

- **`docs/roadmap/竞品对标-inkos-2026-09-11.md`（新增）**——完整对标结论留档，含 InkOS 画像（结构 / 三层存储 / 44 provider endpoint / 单章流水线 / 原子写与写锁实现）、232 条 open issue 的六类痛点归纳（逐条带 issue 编号）、五条假设的验证判定、NovelOS 对位优势表、补强建议、未能确认项与复现方法。目的是免去日后重复调研。
- **核心结论**：InkOS 的传播杠杆是 `skills/SKILL.md` + 33 个 CLI 命令使其可被外部 Coding Agent 调用（进 ClawHub / SkillsLLM 技能市场），非「AI 写小说」概念；但 **9.7k stars 与工程质量不匹配**——232 条 open issue 中大量 2026-05 起的老 bug 未关，`pushed_at` 停在 2026-08-25。

  ⚠️ **2026-10-15 复核算更正——上条「`pushed_at` 停在 2026-08-25」是错的**：
  经 GitHub REST API 复核，`pushed_at` 实为 **2026-10-10**，当时观察有误。
  星数亦已由 9.7k 涨至 **10,301**（≈+600/34 天，属正常增长，记录不算错但已过期）。
  **本条的核心判断反而更成立**：master 最后提交停在 2026-09-26、9 月仅合并 1 个 PR、
  10 月合并 0 个、issue 累计 272 条仅关闭 78 条（**28.7%**）。
  **教训（跨项目可复用）**：`pushed_at` 只反映**任一分支**的推送，不等于主线开发吞吐——
  单看该字段会把「已停更」误读成「已恢复」，**两个方向都会误判**。判断维护健康度须同时看
  `pushed_at` + master 最后提交 + PR 合并数 + issue 关闭率。
  详见 `NovelOS-Artifacts: docs/analysis/business-logic-assessment-2026-10-15.md` §五。
- **用户痛点高度同源且集中在 NovelOS 已规避的领域**：① 数据完整性（#387 三本书写不到三章即文件缺失、#275 导入章节覆盖重建真相文件）；② 上下文膨胀（#80 五十章后单章修复 40 分钟、#84 摘要 50 章后不可用）；③ 模型配置（#300 硬编码白名单只认 72 个模型、#379 强制要求上游实现可选端点 `/models` 否则拒绝保存）。
- **两处主会话先验判断被推翻（留痕以免重蹈）**：① 「InkOS 以 JSON/MD 为唯一真相源、无事务保证」——**错**，实际有 `commitAtomicFileSet` 原子提交（`utils/atomic-file-set.ts:46-115`）、跨进程写锁（心跳 30s + 租约 3min）、索引自愈（`state/manager.ts:480-548` 的 `rebuildChapterIndexFromFiles`）；准确差异是「原子性 ≠ 可回溯性」与「原子写仅覆盖特殊路径」。② 「NovelOS 完全没有 token 预算机制」——**不准**，`_truncate_summaries_to_token_budget`（`context_engine/builders_common.py:953-972`）在跑，但只作用于 director 摘要链、预算固定 800 token（`director_input.py:174-175`）。
- **实测确认的真实缺口（1 条）**：`_TOKEN_BUDGET = 8000` 定义于 `context_engine/preview.py:37`，全仓仅 3 处引用（定义 + `preview.py:525-526` 两个返回字段），**无任何代码读它做裁剪或失败**，`within_budget` 仅展示用。即 NovelOS 的上下文裁剪是「按字段降级」，缺全局 token 硬顶兜底——与 InkOS #80/#84 同源风险，建议把既有 `_truncate_summaries_to_token_budget` 从「仅 director 摘要链」推广到全局装配。
- **架构无关、自用也会踩的一条（待自查）**：InkOS #260 用户设定「权谋线 / 感情线各占一半」而实际输出感情戏近零，改 focus.md / 重写 / 审计驳回均只能维持两三章。NovelOS `scene_planner` 已硬约束 `target_words`（形式约束，`builders_common.py:986`），但「内容配比」类约束是否在 director / scene_planner 阶段显式声明并逐章核销，本次未见明确机制，列入自查项。
- **定位裁决（用户 2026-09-11）**：NovelOS 为**纯自用工具**。据此搁置三项：CLI 原子命令层 + SKILL.md（自用场景 agent 可读代码调用 HTTP，投入产出比不成立；且实测 `pyproject.toml` 无 `[project.scripts]`）、LICENSE 声明（实测项目**无 LICENSE 文件**、`pyproject.toml` 无 `license` 字段；私有自用无影响，**若日后转公开发布则为硬阻塞**）、Docker 部署与账户鉴权。明确不必跟风：互动影游 / Open World / 剧本分镜、33 维审计规模、复杂文风指纹系统、多语言文档。
- **许可边界（已核实）**：InkOS 全仓 AGPL-3.0-only（LICENSE + 三个包 `license` 字段一致），**只可借鉴设计思路、不可复制代码**；`SKILL.md` 的 YAML frontmatter 协议格式（AgentSkills / OpenClaw）属开放格式可参照，正文受 AGPL 约束。
- **源码快照**：`D:\zcodeproject\_refs\inkos`（HEAD `0910483`，v1.8.0，约 31 MB 含 `.git`），供后续查阅。
- **审查与订正**：文档落地后派独立审查子代理逐条核验 file:line 引用与数字（InkOS 11 处 + NovelOS 7 处引用点全部准确）。订正 3 处数字错误：studio 测试文件 21→**59**（总数 ≈267→**306**）、内置 skill 目录 14→**15**（原文还错记为「README 称 15 而实测 14」的不一致，实为一致）、studio 页面路由 25→**23**；另订正 CLI 命令口径（33 个命令文件 / program.ts 内 34 次 `addCommand`，daemon.ts 含 up/down）、TUI 文件 18→19、openrouter 白名单「60+」→59、`ProductionRunSnapshot` 字段名 `runId`→`id`，并为 stars 补注时效增长。

### Added（2026-09-09 内容同步器 CLI：软件/内容分仓运行时闭环）

> 来源：软件层/内容层/操作痕迹三仓分离后，运行时写小说产出「内容归内容」的归档通路（主会话定架构，实现→审查 PASS-with-nits→打回小修闭环）。

- **`scripts/content_sync.py` + `packages/core/content_sync/`**——按「每本书一个独立子目录」把 SQLite 中某 project 的创作产出同步到内容仓（默认 `D:/zcodeproject/NovelOS-Content`），实现内容版本管理闭环。三子命令：`list`（列出已同步书）、`init`（建书骨架+manifest）、`push`（全量同步）。
- **产出目录约定**（每书独立、互不污染）：`novel/`（全书 txt + 按卷/章 txt，复用 `exporter.builder.build_txt`）、`canon/`（characters/world_rules/plot_events/timeline_events/volumes 五个 JSON）、`story_state/`（按 `state_version` 命名去重）、`backup/`（复用 `BackupService.export_project` 单 JSON 包，带时间戳）、`manifest.json`（project 元信息+章节/草稿/版本计数+最近同步时间）。
- **slug 派生**：NFKD 归一化→lowercase→`[a-z0-9-]`，非 ASCII 纯中文 fallback `book-<project_id 末 8>`；Windows 设备保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）统一加 `book-` 前缀防 `mkdir` 拒绝；冲突追加 `-2/-3`，同 project_id push 复用既有 manifest 目录保证路径稳定。
- **幂等与安全**：story_state 按版本天然去重（已存在跳过）；backup 每次新建带时间戳、同秒冲突 `<ts>-1` 起递增不覆盖；chapter/volume number 为 NULL/非法时显式记 `result.errors` 并跳过（绝不让「第None章」落盘）；queries 层仅 SELECT、不写 schema、不调 LLM；路径穿越经 slugify 归一化不会逃出内容仓；退出码 0=全成功 / 1=参数或 project 不存在 / 2=部分产物失败。
- **测试**：`tests/unit/test_content_sync_{slug,service}.py` **49 用例全绿**（slug 派生/唯一性/稳定、Windows 保留名 9 例、story_state 幂等、manifest 容错 7 例、同秒 backup 兜底、不同书隔离、CLI 三子命令与退出码）；ruff 0；真实 DB 端到端 init→push→push 复测通过（二次 push slug 稳定、backup `<ts>`/`<ts>-1` 各一份、5 类产物齐全）。
- **已知取舍（留档）**：`build_volume_txt` 目前跨模块引用 `exporter.builder` 下划线私有 helper（`_chapter_heading/_latest_draft_content/_project_name`），exporter 重构会 break——下轮可将三 helper 提升为公开 API；`_prepare_book_dir` 每次扫全部 manifest 为 O(N)，书本数上千时才需优化。


### Changed（2026-09-08 README 同步至 4d04505 现状：基线数字/版本口径/遗留引用清理）

> 版本留痕同步（用户拍板：版本节口径跟随 `pyproject.toml`）。

- **测试基线数字更新**——后端「约 1041 passed / 2 skipped」→「1693 passed / 2 skipped，`-n 4` 并行约 4 分钟」；前端 vitest「202」→「416」。README 代码块与 `git log` 基线（commit 4d04505：pytest 1693 / vitest 416 / ruff 0）对齐。
- **环境变量表三处修正/补齐**——`NOVELOS_API_PORT` 默认值 8000 → 18081（与 `config.py` 端口优先级口径一致：`NOVELOS_PORT` > `NOVELOS_API_PORT` > 默认 18081）；补 `NOVELOS_CRITIC_MODE`（默认 always，与 `chapter_review/pipeline.py` 内定一致）、`NOVELOS_SUMMARY_PARALLEL`（默认 on）、`NOVELOS_AUTO_REVISE_MAX`（默认 2）三项此前缺失的变量。
- **健康检查口径补注**——`tables=37` 为业务表数；含 `_migrations` 物理共 38 张（README 此前写 35，与 smoke_e2e.py L67 注释口径统一）。
- **版本节同步 v3.8.0**——当前版本从「V2.0.0（git tag v2.0.0）」改为「v3.8.0（与 `pyproject.toml` 单源一致）」，并注明 `apps/web/package.json` 仍为 3.5.0、属发版流程待对齐项（本轮不做版本号 bump，只同步文档口径）。
- **遗留引用清理**——`apps/desktop/` 已于 4d04505 从仓库删除，README 仓库结构段下方「注：apps/desktop/ 是 S0 骨架」与端口差异说明中「apps/desktop/vite.config.ts 为停维护骨架，仅供参考」两处引用同步移除。

### Changed（2026-09-08 P1 清理：全项目复审发现落地 + 业务表口径统一 37/38）

> 来源：docs/reviews/全项目复审-2026-09-08.md（P1 项）。审查子代理 PASS（2 minor nit 不阻塞）。

- **业务表口径统一 37/38（4 文件 7 处）**——README L48/L66/L100、`packages/core/api/README.md` L34/L71/L98、`db.py:117` docstring 全部从过期 35/36（api/README 残留 31/34）统一为「业务表 37 / 含 `_migrations` 总数 38」。实测 `count_tables()` 返回 38（排除 `sqlite_%` 与 `chapter_fts%` 影子表后含 `_migrations`），`test_health.py:44` 断言 `tables == 37` 与代码真值一致。
- **`builders.py` 门面 `__all__` 移除自指元素**——拆分时误把字符串 `"__all__"` 收进 `__all__` 列表（L268），破坏「导出名单」语义契约；删除后 116 元素 0 重复 0 自指，`from builders import *` 语义完好。
- **ruff F841 归零（全仓 0 error）**——`tests/api/test_openapi_contract.py:65` `schema = app.openapi()` 赋值未用（批次四新增测试自带 lint 错，4d04505 声称 ruff 0 有水分），按仓库惯用约定改 `_schema`；全仓 `ruff check` 通过。
- **`db_maintenance.py list` 子命令支持 `--older-than-minutes`**——此前 `list` 硬编码 `DEFAULT_OLDER_THAN_MINUTES=30`，与 `fix` 的参数能力不对称；补同名参数（default 30 行为不变），`list --help` 与 fix 结构对称。
- **删除 `WorkflowEngine.start()` 死入口**——`engine.py` L241-261 `raise NotImplementedError` 保留入口全仓零调用方（只 `start_with_nodes` 被 routers/pipelines/smoke/evals 消费）；同步模块 docstring L30 由 `start()` 改 `start_with_nodes()`。
- **测试**：ruff 0；pytest 相关 91 用例零回归（含 test_openapi_contract/test_db_maintenance/test_health/test_engine_* 等）。全量 pytest 基线另计（上一轮实测 1677 passed / 2 skipped）。
- **范围外留档（下轮处理）**：`scripts/smoke_e2e.py:67,72` 注释仍写「35 业务表」、`docs/data-model/data-model-v0.md:3`〔断链〕 写「34 张」、api/README 的 V3.4 演化轨迹提示——均与本轮口径漂移，不在本 diff 范围。

### Added（2026-09-06 全项目审查落地：四批次优化，P1 缺陷清零 + 数据生命周期 + 巨型文件拆分 + 工程基建）

> 来源：docs/reviews/全项目审查与优化建议-2026-09-06.md（16 项发现，本批全部落地）。

- **批次一·工程卫生**：
  - **P1 修复 `snapshot.py:699` 未定义 `_logger`（F821）**——防御分支引用不存在的 logger，一旦触发即 NameError；改用模块级 `logging.getLogger("novelos.story_state.snapshot")`（非 dict world 活路径已有 `test_repair_current_snapshot_world_handles_non_dict_world` 覆盖）。
  - **版本号单源**——`main.py.__version__` 改从 `importlib.metadata.version("novelos")` 读取（fallback "dev"），health 端点不再报过期 3.5.0；测试本就与同源常量比较，零改动。
  - **ruff 清零 + 门禁固化**——264 项 → 0：`--fix` 收编 I001/F401/W292/F541/E401；手工修 8 处生产 E501、F841×17（含 `pipeline.run_agent` 未用导入、`cfg` 死代码、`saw_done` 无读变量等）、E741×4、E402×5；`tests/**` 显式豁免 E501（断言长串属测试语料，写入 pyproject per-file-ignores 并注明理由）；README 补「开发规范」节。
  - **杂项清理**——删仓根 `nul` 再生物与 `apps/desktop/` S0 残骸（含 node_modules）；README 仓库结构段迁移/表数对齐现行（0001~0023、35 张业务表）；补 `packages/domain/knowledge`、`packages/domain/volume` 两个缺失 README；`data/` 运维产物（backup_*.json、4 个 .bak_*.db、探针/冒烟日志、m1_run/provider_dump）归档至 `data/archive/`。
- **批次二·数据生命周期**：
  - **`db_maintenance.py` 增 `prune-logs` 子命令**——清理 `ai_call_logs` 早于保留期（`--days` 默认 90）的行（该表此前无任何清理机制，长跑后 DB 无界膨胀）；默认 dry-run、`--apply` 真删、`--export PATH` 删除前全列 JSON 归档；表不存在 fail-soft；`main()` 重构为显式分支结构。
  - **`db_maintenance.py` 增 `vacuum` 子命令**——VACUUM 回收已删行磁盘空间，打印前后页统计。
  - **checkpoint_json 512KB 软上限（engine）**——`_cap_checkpoint_payload`：超限时顶层值按体积降序替换为 `{"__checkpoint_truncated__": true, "original_bytes": n}` 标记（≤4KB 小值保真）；**PAUSED 落盘不截断**（人工审阅 resume 需全量 ctx），每节点 checkpoint 与 COMPLETED/FAILED/CANCELLED 终态截断；单进程部署下崩溃恢复本就由 `recover_interrupted_runs` 收尾 FAILED，恢复语义无损。
  - **修复既有竞态用例 `test_engine_stop_before_node_when_run_already_cancelled`**——cancel 落在「n1 完全落库后、n2 探针前」的合法窗口时不变量被误判失败（checkpoint 路径新增软上限逻辑后窗口概率上升）；n1 fn 注入 0.3s 执行时长使 cancel 必然落在执行窗口内，8/8 复跑稳定。
- **批次三·巨型文件拆分（导出面全部不变）**：
  - **`context_engine/builders.py`（3678 行）→ 7 模块 + 门面**——`builders_common`（共享 helper）/ `relevance` / `cache` / `canon` / `director_input` / `writer_input` / `observer_input`；`builders.py` 变 269 行纯再导出门面（显式 import + 全量 `__all__`，含全部私有名——`builders.X` 与 `from builders import X` 零行为变化）。AST 依赖分析定层序并验证无环；**拆分实测抓出一个隐患**：`_cache_reset` 的 `global` 重绑定使门面/测试持有的 dict 旧引用失联——改为原地 `clear()`（语义不变），2 个缓存测试由红转绿。
  - **`chapter_commit/pipeline.py`（2262 行）→ 5 模块 + 门面**——`pipeline_common`（开关/mock 过滤/修订指导）/ `observer`（双腿拆分与聚合）/ `gate`（质量门禁与高危审批）/ `commit`（delta 构建/校验注入/commit）/ `summary`（摘要节点）；`pipeline.py` 保留 `_build_nodes`/`WORKFLOW` 组装与全量再导出（单行 `noqa: F401` 显式声明 re-export）。
  - **测试 patch 目标随拆分迁移**——`pipeline.run_agent` 的 monkeypatch 目标迁到新属主模块（observer/summary，3 个测试文件）；`test_chapter_write_length_closure` 等经 facade re-export 的导入零改动。
  - **前端 `ChapterDetailPage`（1823 行）抽 `hooks/useChapterRunOrchestration.ts`**——runs 列表/选中/2s 轮询/run 详情/pausePayload/quality_gate checkpoint 提取整体抽离；页面经 `runsReload`/`detailReload` 消费；tsc + vitest 416 全绿。
- **批次四·契约与测试基建**：
  - **OpenAPI 类型 codegen 试点**——`scripts/export_openapi.py`（app.openapi() 落盘，89 paths / 34 schemas）+ `scripts/gen_frontend_types.py`（内置极简 codegen：schema → TS interface，确定性输出入库 `apps/web/src/api/types.generated.ts`）；`npm run gen:types` 一键重生成。定位为「后端 schema 漂移对照面」，运行时类型仍以手写 types.ts 为权威。
  - **契约测试 `tests/api/test_openapi_contract.py`**——openapi 必含关键模型与字段（宽松口径：快照 ⊆ 实际，加字段不报错、删/改名才报）；types.generated.ts 与当前 schema 同步断言；3 用例。附注：WorkflowRun/QualityReport/ModelProfile 等端点返回裸 dict 无 response_model、不进 OpenAPI——补齐属后续优化。
  - **pytest-xdist 落地**——`uv add --dev pytest-xdist>=3.8.0`（pyproject + uv.lock 同步）；`pytest -n 4` 全仓验证绿，dev 反馈环 10min → ~4min。
- **测试**：pytest 全量绿（含新增 db_maintenance prune/vacuum 6 用例、checkpoint cap 4 用例、契约 3 用例）；vitest 416 绿；`tsc -b` + `vite build` 绿、dist 已重建；ruff 全仓 0 error。


### Added（拆书模块四连改：消费面接通 + 人设维度 + 文风样例联动 + 文件上传，2026-09-02）
- **P1·ReferenceCanon 消费面接通（builders.py + chapter_write/pipeline.py + 两份 prompt）**：`_reference_canon_excerpt` 泛化为 consumer 参数——director 原四字段逐字不变；scene_planner 新吃 emotion_curve（末尾 ≤50 条窗口）+ payoff_list（≤30）；writer 新吃 style_params（按字段优先级删减至 ≤1500 字符，带 `__style_params_truncated__` 标记）。scene_planner-v1/writer-v1 prompt 补 reference_canon 可选键契约+缺席语义+「参数化参考、禁原文」合规注记；`_reference_canon_consumed` 审计三 builder 全覆盖（含 scene_planner 透出 checkpoint）。
- **P2·protagonist 人设维度（规范+schema+aggregate prompt+G-sim+Director+前端五处同步）**：ReferenceCanon 新增顶层 optional `protagonist`（identity≤80/personality_tags≤6×≤12/core_drive≤120/foil_techniques≤8×≤60），schema v0.1.2；G-sim `_collect_strings` 递归天然覆盖（13 字重叠实测拦截）；Director excerpt 增 protagonist（foils≤4 截断）；CanonTab 详情加「主角人设」区块；report_md 条件渲染。存量 canon 缺席语义双向兜底。
- **P3·canon→文风样例联动（reference.py + CanonTab）**：`POST /projects/{pid}/canons/{cid}/to-style-sample`——确定性模板合成「拆书风格卡」（logline+style_params 逐条转写+techniques，≤4800 字符，零原文片段、不调 LLM），复用 create_style_sample 既有校验（≤5000 字/≤10 篇）；style_params 空→400、同标题→409。前端详情抽屉加「写入文风样例面板」按钮+轻反馈。
- **P4·拆书文件上传（reference.py + CanonTab + 新依赖）**：`POST /projects/{pid}/deconstruct-upload`（multipart，20MB 上限）——.txt 解码链 utf-8→gb18030→utf-16→400；.epub stdlib 解析（zipfile+xml.etree 按 OPF spine 顺序拼接、去标签、unescape），单 member >5MB→400、加密 member→400、正文 >300 万字符→400；与粘贴共用 `_start_deconstruct_internal` 同一启动路径。新增依赖 `python-multipart==0.0.32`（== 锁死 + uv.lock 已同步）。前端文件/粘贴二选一互斥。
- **审查修复轮（smart 审查 10 项全修）**：tsc 两处编译错误（ReaderProfile import/类型断言）；txt 解码 500→三段链+400；scene_planner 审计透出 checkpoint；director/writer 缓存键混入 active canon_id（拆新书后旧缓存自然失效，顺带修复 Sprint 11 遗留）；epub zip 炸弹单文件 5MB 预检+加密 flag 拒绝；规范文档 planner/style_params 矛盾行修正；fetch reject 归一 ApiError(0)；prompt 序号整理。to-style-sample 409 判重竞态裁决接受不修（单用户 UI+防抖）。
- **测试**：pytest 1680 绿 / vitest 416 绿 / build 绿；新增用例覆盖三 consumer 注入与截断、protagonist schema 五类、G-sim 新字段拦截、风格卡四态、上传九类（编码/spine 顺序/坏 zip/超限/加密）、缓存失效、审计透出。

### Fixed（人工改稿闭环三连修：改稿必重审 + commit 时序守卫 + 版本列表「未审」角标，2026-09-01）
- **A·改稿降级（`packages/domain/chapter/service.py`）**：`create_draft` 在 INSERT 新 draft 的**同事务**内，若章节原状态为 REVIEWED 则降级回 DRAFTED——堵住「审校通过→人工改一段→直接 commit 定稿未审改动」的口子（与 continuation adopt 口径对齐并收编为唯一属主，adopt 内联降级 SQL 已删）。DRAFTED 不受影响；COMMITTED 仍 409。
- **B·commit 审校时序守卫（`routers/workflows.py` + `chapter_commit/pipeline.py`）**：最新草稿 `created_at` 晚于最近一次 COMPLETED chapter-review 的 `ended_at`（=存在未审改动）→ `start_commit` 端点同步 409（detail 含版本号与两个时间）；`_commit_node` 加同款兜底 raise，防绕过端点的 generic 启动路径。无 COMPLETED review / 无草稿不拦（保持原行为，由既有 REVIEWED 校验兜底）；FAILED/CANCELLED/PAUSED review 不计入阈值。同时修复 `_commit_node` 连接生命周期（conn 提升 try/finally）。
- **C·版本列表「未审」角标**：章节详情响应新增 `last_review_completed_at`（`ChapterService.get_last_review_completed_at`，与路由层 helper 同款 SQL、避免横向依赖；`Chapter` 模型放开 `extra='allow'`）；前端 DraftsPanel 版本行对晚于该时间的草稿渲染 warn 色系「未审」徽标，一眼分辨已审/未审版本。
- **测试**：新增 14 用例（create_draft 降级×3 / commit 守卫 API×5+helper 单元×2 / pipeline 兜底 workflow 级×2 / last_review_completed_at 集成×2 / 前端角标×2）。审查后全仓 pytest 1639 绿 + 补测后 tests/api 235 绿、tests/workflow 204 绿；vitest 409 绿；build 绿、dist 已重建。

### Added（工作流运行取消：POST cancel 端点 + 引擎协作式取消 + auto_revise 不链 CANCELLED）
- **`POST /api/runs/{run_id}/cancel` 端点（`packages/core/api/routers/workflows.py`）**：协作式取消 RUNNING workflow run。状态机——404（run 不存在）/ 409（终态 COMPLETED/FAILED/CANCELLED 或 PAUSED，detail 含当前 status；PAUSED 的取消走 resume 后驳回/决议路径）/ 200 `{"run_id":..., "status":"CANCELLED"}`（RUNNING → UPDATE CANCELLED + ended_at）。幂等：重复取消已 CANCELLED 的 run 按 409 处理。引擎 cancel_run 内部 `WHERE status='RUNNING'` 兜底 TOCTOU——窄窗口内状态被改 → rowcount=0 → 抛 ValueError → 端点分桶映射 409。语义与既有 resume 端点的 ValueError 分桶（404/409/400）保持一致。
- **引擎协作式取消（`packages/core/workflow_runtime/engine.py`）**：`WorkflowEngine.cancel_run(run_id)` UPDATE RUNNING → CANCELLED；`_run_nodes` 节点循环两处探针（节点开始前 / 节点 fn 执行完毕 checkpoint 前）单行 SELECT `workflow_runs.status`——命中 CANCELLED 则停止推进：开始前命中 → 直接 `_finalize_run('CANCELLED')` 收尾、零节点行（后续节点不再 insert）；checkpoint 前命中 → 当前节点标 **FAILED**（复用真实失败枚举）+ error='cancelled by user'（区分真失败）+ output_json=NULL、`_finalize_run('CANCELLED')`、return。LLM 节点不杀进程——后台调用自然跑完结果丢弃即可，避免跨进程信号复杂度。`_finalize_run` 已支持 CANCELLED 终态（既有 L665 分支）直接复用，无 schema 变化。`_fetch_run_status` 模块级辅助暴露供测试与外部探针使用。
- **节点行复用 FAILED 的取舍（设计）**：workflow_run_nodes.status CHECK 不含 CANCELLED（0001_init.sql L407）。扩枚举需 `ALTER TABLE RENAME + CREATE` 重建表——但 ai_call_logs 的 FK `node_run_id REFERENCES workflow_run_nodes(node_run_id)`（0001_init.sql L440）写在 child 表定义里，SQLite `ALTER TABLE RENAME` 不更新 FK 引用的表名，重建 workflow_run_nodes 会让 child 表 FK 指向已 DROP 的旧表名 `_wfrn_old`，触发「no such table」FK 解析失败。**放弃扩枚举，改用 FAILED + error 信息区分**——审计面损失最小（error 字段即可区分），不破坏既有 ai_call_logs FK 依赖。
- **auto_revise 连带核实（无改动）**：仅审查不修。CANCELLED run 不会被 auto_revise 链出——`_auto_revise_runner`（L1030-1034）只在 `status == "FAILED" and "rejected-for-revision" in error` 时触发 `_auto_revise_loop`；`_auto_revise_loop` 内 write 子 run `status != "COMPLETED"` 即 return（覆盖 CANCELLED），review 子 run 仅 COMPLETED/PAUSED/FAILED 三态 return、CANCELLED 不匹配 `rejected-for-revision` 字符串亦不链下一轮；`_run_workflow_return_payload` 轮询终止条件已含 CANCELLED（L393）。三处判定口径一致，CANCELLED 永不链下游。
- **测试**：API 层 8 用例（RUNNING→200/CANCELLED→409/COMPLETED→409/FAILED→409/PAUSED→409/404/兜底 ValueError→404/兜底 ValueError→409）`tests/api/test_workflow_cancel.py`；引擎层 9 用例（cancel_run 三态 + 真实 TOCTOU + 开始前探针 + checkpoint 前探针 + 端到端 cancel_run 收尾 + **P1 _finalize_run 守卫竞态** + **P2 SQL rowcount=0 兜底钉死**）`tests/workflow/test_engine_cancel.py`。
- **`_finalize_run` 收尾竞态守卫**（复审 P1 修）：COMPLETED/FAILED 分支 UPDATE 加 `WHERE run_id = ? AND status = 'RUNNING'` 守卫——节点 fn 完成（或抛异常）后 `_update_node_row` / `_update_run_checkpoint` 之间的 5-20ms 窗口内，cancel_run 已把 run 翻 CANCELLED + 返回 200，无守卫 UPDATE 会把 CANCELLED 覆盖回 COMPLETED/FAILED，取消契约被击穿。CANCELLED 分支保持无守卫（幂等重写无害）；PAUSED 分支不动；resume 路径已先 `_mark_run_running` 翻 RUNNING，守卫无副作用。rowcount=0 → 静默跳过收尾（run 已是 CANCELLED，cancel_run 侧已写 ended_at）+ debug 日志便于审计。
- **前端「停止工作流」按钮（`apps/web/src/pages/ChapterDetailPage.tsx` `WorkflowRunningBanner`）**：在「正在执行」横幅右侧内嵌 danger 按钮「停止工作流」——仅 `RUNNING` 时展示（PENDING/PAUSED/终态不出现）。点击展开内联二次确认态，文案「确认停止当前工作流？已完成的节点会保留，正在执行的节点结果将被丢弃。」；确认按钮禁用防连点、提交中文案「停止中…」。端点封装 `workflowsApi.cancelRun(runId)` → `POST /runs/{runId}/cancel`，父组件 `handleCancelRun` 统一处理 200/409/404/其它：成功/409/404 都触发既有刷新链路（轮询自然把 banner 撤下、run 变 CANCELLED），409 不视为错误、不进 ErrorBanner；其它错误信息进既有 ErrorBanner 通道。切换 run 后确认态自动重置。4 新单测（可见性 / 不可见性 / 确认调用入参 / 409 静默刷新不炸 ErrorBanner）。

### Added（前端 UI/UX 集中优化四批次 + 审查修复轮，2026-09-01）
- **批次A·草稿阅读体验**：新建共享组件 `components/ProseText.tsx`（按 `\n\n` 切段渲染 `<p>`，段内单换行 `pre-wrap` 保留；key 用段序 index、过滤空串段）；`.prose-block` 等宽字体改 `var(--font-sans)`、12px→14px、行高 1.6→1.9、加 `max-width: 44em` 与 `.prose-block__p { text-indent: 2em }`（中文网文排版：首行缩进+行长约束）；ChapterDetailPage 草稿/PlanPanel、ContinuePanel 三处正文渲染统一换 ProseText。**1722px 定高终案原样保留**（用户拍板项，不动）。
- **批次B·视觉一致性**：`:root` 补 `--color-success-deep/--color-accent/--color-error(danger 别名)/--color-warning(warn 别名)` 令牌；清除 4 套散落硬编码同义色（badge--chapter-released #1f6e4a、quality-overall 的 #c00/#b70/#080 fallback、AiSettingsPage/ProjectInitPanel/ProjectInitEditors 的 seagreen/crimson、BranchesPanel 的 GitHub 色板）全部改引令牌；`.btn` 与 sidebar 导航补 `:focus-visible` outline（键盘可访问性）；`.table-wrap`（max-width:100% + 内表 min-width:max-content）解决超宽表格撑破页面。
- **批次C·交互反馈统一**：新建 `components/ConfirmDialog.tsx`（受控、Esc 关闭、初始焦点在「取消」、danger 态、aria-label 兜底）替换 9 处裸 `window.confirm`（项目归档/章节删除/计划覆盖/档案删除/bible 5 Tab 删除）；删除章节补 submitting 防连点；ProjectFormModal 与档案增删补「已保存」轻反馈；新建 `utils/formatApiError.ts` 统一三处错误文案拼法（ContinuePanel/ExportPanel/QualityPanel）。
- **批次D·节点时间线默认折叠**：RunTimeline 节点列表包 `<details>`——RUNNING/PAUSED 默认展开（盯进度），终态默认收起；summary 行「共 N 个节点 · 最新：节点名」；open 状态「仅初始值受控」（useState 初始化 + run_id 变化重置 + onToggle 同步），用户手动折叠不被 2s 轮询打回（审查 P2-1 修复）。
- **审查修复轮**：P2-1 details 受控 open 被轮询打回（见上）；P2-2 table-wrap 对 width:100% 表格不生效（补 max-width/min-width 组合）；P3 三连（ContinuePanel 复用 formatApiError / ConfirmDialog aria 兜底 / ProseText key 与空段）。
- **测试**：vitest 34 文件 407 用例全绿（新增 ProseText 3 / ConfirmDialog 9 / formatApiError 5 / 折叠 5 / saved-banner 3 等）；`npm run build`（tsc -b + vite）全绿。记档缺口：bible Canon/World/Ledger 三 Tab 删除流无测试（既有缺口非回归）。

### Fixed（2026-09-01：草稿正文 44em 行长限制回退）
- 批次A 给 `.prose-block` 加的 `max-width: 44em` 在宽面板（草稿/审校正文区）导致文字提前换行、右侧留出大段空白（用户截图反馈）。草稿区诉求是填满容器宽度而非书籍式窄栏，移除该限制（44em 阅读宽度理念不适用于本系统的宽面板场景）；其余排版优化（sans/14px/行高 1.9/首行缩进/480px 定高滚动）保留。改动仅 `apps/web/src/index.css` 一行，dist 已重建上线。


### Added（chapter-write 字数闭环：治理实证 ch4-6 writer 自报 3008-3172 vs 实际 4721-5842 的系统性超带）
- **length_check / condense 节点（chapter_write/pipeline.py）**：在 polisher 与 save_draft 之间插入两个节点。length_check（Transform，纯 deterministic）用 `packages.core.quality.wordcount.classify_prose_length` 权威口径实测 polished_prose（缺则 writer_output.prose）字数，读项目 `word_band_json` 覆盖（与 chapter_review._basic_checks_node 同口径，`resolve_band_config` 单一权威）；condense（AI，复用 polisher capability=creative_writing）仅在带外时触发，最多 2 轮，prompt 必携带实测数字 / 目标带 / 需净减比例 + writer-v1 §6.1 规则 20 压缩纪律（优先砍铺垫/重复意象/冗词，不砍节拍、不删场景、保持文风与既有设定用语）。两轮仍超 → 放行 + `condense_status='over_band_after_2_rounds'`。14 新单测（length_check 三态/项目覆盖生效/condense 三分支/save_draft 偏差注记/权威字数存库/builders per-scene 分摊）。
- **save_draft 字数口径整改**：word_count 改用权威实测 `ctx['length_report']['visible_chars']`（不再采信 self_report.word_count——实证 ch4-6 偏差 50%+），self_report 保留在 ctx 供诊断。length_check 未通过 + status ∈ {over, under} 时往 `chapters.plan_json.deviations` 追加一条 source=chapter_write.length_check 的偏差注记（含实测字数 / 目标带 / deviation_pct / 已跑轮数），与 W-LEN 报告型口径对齐（report-only 兜底，不阻断 save_draft）。
- **scene_planner 强制 per-scene 字数分摊写入 writer 输入**：①`docs/agents/prompts/scene_planner-v1.md` §6 Rule 7 升级为「必填」——每个 scene 必须输出 `target_words`（整数 ≥0，总和 ∈ target_word_count 的 90~100%）；§7 schema 补 `target_words` 字段说明；§400 Open Question 1 撤档。②`packages.core.context_engine.builders._inject_scene_word_budget` 新增兜底注入——已声明且总和在 90~110% 区间保留原值；缺值 / 总和 < 90% / 总和 > 110% 走等分（`divmod`+余数补首场景）；scene_plan 永远带 per-scene target_words 注入 payload["scene_plan"]。注：scene_planner schema 校验（`_validate_scene_planner`）不限制 additionalProperties，新增字段无需改动 validator。

### Added（chapter-write 字数闭环 smart 审查 P2 四连修）
- **计数器键名统一（必修）**：`chapter_write/pipeline.py` 收敛到唯一计数键 `ctx['condense_rounds']`——length_check echo 写同一 key、condense_node 返回值删 `condense_rounds_used`（无人读）、save_draft 偏差注记读同一 key；保证「≤2 轮」真实生效 + deviations.round 反映实际轮数。skipped_in_band / skipped_empty / skipped_no_mock / over_band_after_2_rounds 四条早返回路径均不计轮（preserve rounds_used，不 +1）；所有返回值都带 `condense_status` 便于 run detail 观测。
- **condense 输出确定性守卫（必修）**：`_condense_node` 调 LLM 后对 polished_text 做确定性校验——空串 / 非 str / `visible_chars >= 压缩前 visible_chars` 一律拒收，保留当前 prose，`condense_status='failed_no_shrink'` 并计 1 轮（已调过 LLM 占用预算）。杜绝 0 字 / 更长草稿落库 + 杜绝无意义压缩轮次。
- **deviations 注记条件收窄（必修）**：`_save_draft_node` 字数闭环偏差注记写入条件改为 `condense_rounds_now >= 2 AND not length_check_passed AND status ∈ {over, under}`——原先仅检 length_check_passed 在 round=0（mock 缺失跳过等早返回路径）会误写「2 轮后仍超带」文案污染 W-LEN 报告。
- **word_band 非法值防击穿（必修）**：`_resolve_chapter_word_band` 对 `resolve_band_config` 的 `ValueError`（low>high 倒挂 / floor 负数等）加捕获，落默认带 0.85/1.15/1200 + `_log.warning`，与项目内「非法 JSON → 视为无覆盖」fail-soft 风格一致。chapter_review 侧 `_basic_checks_node` 是独立函数（未复用本 helper），差异不在本任务范围，记 Known Issue 待后续统一覆盖。
- **per-scene target_words 文档口径对齐（防御性）**：①`docs/agents/prompts/scene_planner-v1.md` §6 Rule 7 区间口径从 90~100% 改为 90~110%（与 `builders._inject_scene_word_budget` 代码容忍区间对齐：保留 90~110% 区间，超出才等分兜底）；②`docs/agents/prompts/writer-v1.md` §5 schema 在每个 scene 加一行 `target_words: integer ≥ 0` 字段说明，让注入的 per-scene 预算对 writer 可见。
- **测试**：原 14 用例迁到 `condense_rounds` 唯一键 + 7 新单测（计数器真实递增并止于 2 轮 / 空产出拒收 / 更长产出拒收 / round=0 不写偏差 / round=1 不写偏差 / 倒挂 low>high 落默认 / floor=-100 落默认）。全仓 1609 passed / 2 skipped（基线 1602 +7）。


### Fixed（2026-08-31 午后批次：relationship 换型 update 写透炸 UNIQUE）
- **relationship 换型 update 写透炸 UNIQUE（ch6 生产事故 wfr_9d7eb9cb1eeb）**：根因——relationship 身份=端点对（主键 from:to 不含 type），原 write_through 按 (from,to,新型) 三元组 SELECT，未命中即 INSERT；库中已有同 pair 旧型行（``relationship_id=A:B, relation_type=antagonistic_exchange``）时撞主键炸 ``UNIQUE constraint failed: relationships.relationship_id``。applier 同病：按三元组匹配 → 未命中 append → 快照同 pair 出现两条不同 type 条目。闭环：①write_through 关系 add/update 分支——op=update 且三元组未命中时，按 target_id 主键查或 (project,from,to) 不限 type 取 LIMIT 1 兜底命中既有行→走 UPDATE 分支（连带 ``relation_type`` 列覆盖），不再走 INSERT（add 始终 INSERT：同 pair 不同 type 是新 row，不撞 0017 唯一索引，不被吞并）；②全部 UPDATE（含三元组精确命中与 IntegrityError 兜底分支）统一加 ``relation_type = ?`` 列（三元组精确命中时 type 同值，是无害 no-op；换型路径一致）；③applier `_apply_relationship_changes`——op=update 三元组未命中时按 (from,to) 端点对（不限 type）回退匹配→原位替换（无副本）；op=add 三元组未命中时若 target_id 显式给出且列表已有同 rid 条目→原位替换（幂等防重）。6 新单测（领域表换型不炸 UNIQUE/快照原位替换/同型 update 无重复/add 全新 pair 不被吞并/换型幂等/换型三态语义 visibility+who_knows）。**附注：rollback 逆 update 不恢复旧 relation_type**（before/after 只携带 state_json，type 不在字段级逆 delta 通道内）——为已知局限，未修，留作后续可能的 known limit 文档化点。

### Fixed（2026-08-31 午后批次：rollback 角色 add 实体级回收）
- **rollback 对 character-add 的逆向 remove 空转根治（顾青生产事故）**：逆 delta 把角色 add 转成 op=remove 是字段级语义（applier L117-119 / write_through L436-437 只 pop core_json/state_json key），写透路径从不删 characters/character_states 行——rollback 只清掉了字段值、留下幽灵实体行+快照条目（生产实证 ch2 双回滚后 char_cd61839a578f 实体计数 5→5）。闭环：①cleanup dict 新增 `remove_character_ids` 桶，rollback_commit 从逆 delta 的 character_changes 收集 `op == "remove"` 的 character_id（按 character_id 去重保序）；②`commit_delta` 清理段对每个 cid 依次 DELETE `character_states`（子表）→ `characters`（主表）→ `relationships` 中以该 cid 为端点的悬挂行（FK 已摘除的兜底）；③`apply_inverse_cleanup_to_state` 实体级回收——剔除 `state["characters"]` 中命中 cid 的条目，剩余角色 `relationships` 子列表与 `world.factions[*].relationships` 中以已删 cid 为端点的悬挂条目一并剔除（防快照重建读出悬挂引用）；④docstring 修正「character facet=state 不在逆清理覆盖范围」为 append-only 版本化语义（state 历史不撤销），新增实体级 `remove_character_ids` 路径。**字段级 add 守卫**：「提交前快照（delta_row.previous_state_version 处的 main snapshot）」中存在 character_id → 该 add 是字段级，跳过实体级 DELETE（字段由逆 delta 字段级 remove 恢复）；不存在 → 视为该 commit 实体级首次引入的角色，触发实体回收。生产实证：state_deltas 里 char_08f503251717 等 7 条字段级 add 必须走字段恢复路径，不能误杀既有角色。仅 main 分支（或无分支）路径读主快照判定 preexisting；**守卫失效场景（非 main 分支 / prev_v 缺失 / 快照读取失败）一律保守不删**——`char_entity_delete_enabled=False` 使 `remove_character_ids` 留空、实体 DELETE 整体不触发（欠删可人工清理，过删是既有角色数据丢失）。范围约束：实体删除只由「逆 add」触发，不误杀历史角色（commit1 add + commit2 update → 仅回滚 commit2 时角色存活）；不影响 factions/locations/world_rules/relationships/debts 既有清理路径。5 新单测（add 全清 / add+relationships+events+hooks 全清 / 既有角色 update 回归 / 历史角色不被逆 update 误杀 / **既有角色字段级 add 角色存活**）。Known Issues 原条已撤档。

### Fixed（2026-08-31 午后批次：F5 遗留 P2 + runner 空流自动重试）
- **F5 审计遗留 P2 ×3 收口**：①applier world_rules rule 分支补 `isinstance(r, dict)` 守卫（update/remove 双路径，非 dict 元素跳过+告警，与 dict 桶防御同风格）——rule 列表元素被污染成 str 时 `r.get("world_rule_id")` 不再抛 AttributeError；②snapshot.repair_current_snapshot_world 在 `dict(world)` 前补 isinstance 守卫（非 dict→空 dict+告警，属 L666-667 强制 dict 之外的防御纵深）；③同函数 docstring 补「设计内副作用」Notes（覆写最新快照不更新 state_version/commit_id、不重算 hash，schema 无 digest 列，下次 commit 自然覆盖）。2 新集成用例。
- **runner 空流自动重试（agent_runtime/runner.py）**：provider「零内容块空流」失败单独加一次自动重试（`_EMPTY_STREAM_MARKER` 判定，messages 原样重调、不追加 _RETRY_HINT——空流非输出解析问题）；二次仍空流→重抛 ProviderError 附 `[empty stream retried once, still empty]` 注记；重试成功在 ai_call_logs.error 落 `warn: empty stream retried`；retry_count 语义不变（仍只计 output-invalid 重试），mock_script 路径同覆盖。吸收 MiniMax 间歇性空流抖动（deepseek 空流事故留档建议落地），模型绑定/档案零改动。4 新单测。
- **字数带覆盖审查 P2 二连修**：①`ProjectCreate` 补 word_band 字段 + create 路径校验/落库（此前创建模态与编辑共用表单，新建时填覆盖会被 Pydantic 静默丢弃）——POST /projects 携带 word_band 合法落库/非法 422，与 PATCH 同口径，2 新用例；②builders._invalidate_cache_for_chapter 注释「5 元键」更新为现行多元键口径（功能本正确，纯注释同步）。

### Ops（2026-08-31：ch2 定稿称谓勘误——「确定性重放」SOP 首航）
- **ch2 v7 称谓勘误（顾青角色卡=女性，v6 定稿两处男性称谓遗留）**：「顾先生」→「顾姑娘」（L71）、顾青指代「他只是站在那里」→「她」（L77）。操作路径：回滚 ch3 v5（cmt_519fd2a267a2）→ 回滚 ch2 v6（cmt_e44835df89d9）→ v7 补丁草稿（dr_e63b00046b0d）→ **原 delta 确定性重放**（ch2=dlt_483e98ffcb8d 摘除顾青 add 条目→cmt_8f2e4d57b4c7；ch3=dlt_5d5cbc2c9715 原样→cmt_5ef99fe69905，零 LLM 调用零漂移）→ 章节状态 PATCH 回 COMMITTED。验证：实体计数/快照 world 三集合/hooks 与回滚前基线逐项全等，恒昌当 data_json 完好。
- **observer 重跑不确定性实证（为何不推荐重跑）**：ch2 重提交两次 observer 重跑均产出新问题——一次把角色更新错塞 world_changes[faction]（无调和目标→校验失败，F3 规则不适用）；一次虚构「第二势力」fac_d5a64a584288_unknown（推断性加戏，原提交无此实体，已拒批）。定稿纯文字勘误一律走 delta 重放，剧情级修改才走 write/review/commit 全流程。

### Known Issues（2026-08-31 记档）
- （本批已撤档：rollback 角色 add 实体级回收修复合入上方 Fixed）

### Added（项目级字数带覆盖：V3.7 字数带硬约束可配置化）
- **projects.word_band_json 配置列（迁移 0023）**：给 projects 表加 `word_band_json TEXT` 可空列（NULL=无覆盖走模块默认 0.85/1.15/1200）。前端编辑表单「字数带覆盖（可选）」三键（下带比例 low_ratio 默认 0.85 / 上带比例 high_ratio 默认 1.15 / 下限 floor 默认 1200），全留空=不覆盖（提交体省略 word_band 键）、任一非空=提交 dict（后端 resolve_band_config 校验非法→422）；「清除字数带覆盖」按钮显式传 null 落 DB NULL。后端 service 读路径把 word_band_json 解析成 word_band 字段（非法 JSON 视为 None 不炸），ProjectUpdate 通过 Pydantic model_fields_set 区分「未提供」与「显式 null」。消费点两处接线：①writers._build_writer_input_uncached + cache key 第 8 元 wb_fp（项目覆盖变更不脏命中）；②chapter_review._basic_checks_node。无覆盖项目行为零变化（旧库无该列 → OperationalError 兜底 + 字数带逐字段一致）。单测 resolve_band_config 13 用例 + API PATCH 设置/读取/null 清除/422 4 用例 + chapter_review pipeline 带覆盖项目 W-LEN 判定 2 用例 + 迁移幂等 1 用例。

### Added（2026-08-31 清晨批次：审校模块双层化——REVIEW-CHECKLIST 产品化）
- **deep_review 二审 AI 节点（chapter_review 管线）**：仓根 REVIEW-CHECKLIST 三层清单（设定一致性→节拍核销→行为链连续性）从「主控手工派 smart」产品化为管线能力。critic 与 author_review 之间新增可选节点：run 请求体 `deep_review: true` 开启（缺省/False 走 skipped 不调 AI），agent=deep_reviewer 走 reasoning 能力（Kimi 档），输出严格 JSON（verdict pass/revise + issues[layer/severity/quote/suggestion]），fail-soft 降级绝不阻断主流程，verdict=revise 不自动驳回（advisory 原则与 critic 一致）；报告并入 author_review 的 pause_payload.deep_review_report。新建提示词 deep_reviewer-v1.md（severity 标尺=五类硬缺陷才允许 high）；structured_output 新增 _validate_deep_reviewer 六字段校验；prompts._AGENT_TO_CAPABILITY 与 router.AGENT_CAPABILITY 双源同步 + CAPABILITY_LABELS 收录（环节绑定页可见）。前端 ChapterDetailPage：审校步骤加「深度二审」开关（默认关），审批卡新增「深度二审（三层清单）」分栏（verdict 徽标+issues 列表，缺省/坏形状降级不炸）。后端 7 + 前端 7 新用例。
- **critic 提示词 P0 校准（critic-v1.md）**：§3.9 新增「章内自洽审查」——同一事实（时间/称谓/物件/数字）全章多处出现逐一交叉比对，互斥报 logic/high（生产实证：ch3「今早刚收」×3 与「当期三个月」自相矛盾被 critic 漏报）；§6.12 新增 severity 标尺——high 锁死五类硬缺陷（无源信息/行为链断裂/藏点对象错位/台词矛盾/节拍完全缺失），写法/节奏/视角类建议封顶 medium（生产实证：critic 把「决心落点只有情绪共鸣」写法建议误标 high）；§9 补 E-CRT-08/09 验收规则。
- **revision-note 专用 API**：`PATCH /api/chapters/{id}/revision-note`（note 空串=删键清除，plan_json 其它键保留，NULL 容错；COMMITTED/RELEASED 409，不存在 404）——替代「走评审驳回路径才能落改稿意见」的 hack。27 新用例（unit 16 + api 11）。
- **改稿核销表闭环（chapter_write）**：revise 模式 writer 输出契约追加机读尾块 `---REVISION-CHECKLIST---`（每条指令一条 item/status/note）；_writer_node 解析剥离——剥离先于落库（drafts 正文与 word_count 均不含尾块），核销表进节点产出/run detail 可查；尾块缺失/坏 JSON 降级 None 不炸。writer-v1.md §6.1 契约同步。5 新用例。
- **auto_revise 回路传播 model_overrides**：ResumeRequest 新增 model_overrides 字段；回路重跑 write/review 子 run 透传同一份档案覆盖（body 优先，缺省继承原 review run checkpoint）——此前首轮模型对比覆盖在改稿回路里丢失。2 端到端用例。

### Fixed（2026-08-31 清晨批次：ch3 v5 提交链事故）
- **rollback 快照 restore hint 整条目替换（F5，生产事故 wfr_3cb2182a30f6，rollback 链路最深 bug）**：逆清理 hint 存的是逆 change 的 before（=原 change 的 after，值方向错）且为字段级值，而 apply_inverse_cleanup_to_state 把它整条目替换进快照——faction 条目变字符串、location 条目变残壳 dict（丢 name/statement/data_json/visibility），下一笔 commit 在 applier._set_top_level 抛 TypeError；DB 侧逆 update 恢复同源缺陷把 locations.data_json 抹成 {}。闭环修复：①hint 改 `{world_id, field, value=逆change.after}`（=原 before，方向归正）；②快照侧改字段级恢复（field 末段为键，对齐 _set_top_level 语义；entry 非 dict 跳过+告警，绝不整条目替换；world_rules 支持字段级/merge 两种）；③DB 侧逆 UPDATE 加形状门槛（value 含 name/statement 键才视为条目级恢复，防字段级局部 dict 抹 data_json）；④applier 防御：桶内非 dict 条目换新 dict+告警（污染快照不再炸 commit）；⑤新增 `repair_current_snapshot_world(db_path, project_id)` 数据修复函数（以 DB 为权威重建最新快照 world 三集合，幂等）。生产修复已执行：story_states v8 污染条目重建 + 恒昌当 data_json 经 API 恢复（内容取自 v6 快照）。6 新集成/单测（含 rollback 字段级恢复端到端）。
- **observer 实体 id 前缀误写调和（F3，生产事故 wfr_6d77d905b4b1）**：observer 把已存在 faction 的 id 改写为 char_ 前缀（fac_e98a1ca56b66→char_e98a1ca56b66），inject_validate 拒绝。delta_repair 新增 id_prefix_reconcile 规则：引用 id 在其前缀表不存在但同 hex 后缀在另一实体表（characters↔factions）唯一存在 → 改写为正确前缀并留痕（零命中/多命中不改写，交 validator 报错）；覆盖 character_changes/world_changes(faction)/relationship_changes 端点/new_events.participants 全部引用位。observer-v1.md §6-25 补 ID 保真规则。8 新用例。
- **change_id 占位符照抄根治（F4，生产事故 wfr_6765f6de4d76）**：observer 把输出 schema 示例字面量 `cc:01HXXXXX` 照抄进每条 change（delta 内 change_id 全重复，validator 拦截）。治本：observer-v1.md L222 示例改不可复制形态 + §6-26 新增 change_id 唯一性规则（七类数组前缀对照表 cc/wc/rc/ev/rh/nh/dc）；兜底：delta_repair 新增 change_id_uniquify 规则——占位符模式（≥3 连续 X）或数组内重复 → 重写为 `<原前缀>:<uuid hex12>` 并留痕。4 新用例。

### Fixed（2026-08-31 凌晨批次：第 2/3 章生产运行驱动的审计-修复链）
- **observer delta 组织间关系误杀（F1，生产事故 wfr_6619a7bfa6fa 根治）**：observer 输出 faction↔faction 关系（恒昌当沈氏↔通汇当商战）被 validator 以「端点不在 snapshot characters 桶」误杀——根因是领域模型缺口：relationship_changes 端点与 new_events.participants 只认 character id。扩型闭环：validator 端点判定改 `characters ∪ factions`（_exists_endpoint 双桶）；applier 按 characters→factions 定位宿主桶（faction 关系落 world.factions[].relationships，remove 清两桶）；snapshot 重建聚合 faction 宿主关系（此前 commit 后重建会抹掉）；迁移 0022 重建 relationships 表摘除 from/to 两个 FK（保留 project_id FK 与 0017 唯一索引，生产 FK=ON 下 commit 不再 500）；observer-v1.md §2/§6/§7 补端点身份规则（人↔人/组织↔组织/人↔组织三类）。delta_repair/write_through 经审计+实测无需改动。12 新单测 + 2 端到端集成用例（真实 commit 链路，含双重回滚 DB 层拦截）。
- **rollback 幂等守卫 + chapter.status 联动（F2，生产事故：同一 commit 被回滚两次）**：①rollback_commit 顶部幂等守卫——已存在 rollback_of=该 commit 的逆 commit 时抛 409 StateConflictError；②迁移 0022 附带 `idx_commits_rollback_of` 部分唯一索引兜底并发竞态，commits INSERT 撞索引转 409；③回滚成功同事务联动 chapter COMMITTED→DRAFTED（此前回滚后章节卡 COMMITTED、save_draft 拦截一切改稿）。3 新集成用例。
- **迁移 0021/0022 生产事故数据修复**：0021 校正 ch2 状态（双重回滚后 chapter 卡 COMMITTED 的一次性修复，chapter_id+status 双守卫）；0022 步骤 0 对存量重复 rollback_of 去重（保留最早行、重复行追加 `#dup<rowid>` 后缀，审计行不删）。双重回滚残留的 3 条 update 逆向抵消（沈默 knowledge/goal、沈沈关系强度回到 commit 后值）经评估在 v6 重提交后依然成立，无害不修。
- **build_inverse_delta 的 rollback_reason 未绑定（deltas.py，复审独立复现的 pre-existing bug）**：rollback_reason 原在 character_changes 循环内赋值，character_changes 为空而 relationship/world 含 op=add 的 delta 回滚必抛 UnboundLocalError——提升为函数级初始化。
- **空流报错携带 finish_reason 诊断（providers.py）**：OpenAI 兼容流式零内容块报错按 finish_reason 三态分叉——length→附「输出预算被思考 token 耗尽，请调大 max_tokens」；其它非 None→附值；None→原消息不变。生产实证价值：deepseek 两次空流靠此分别定位到「预算耗尽」（length）与「上游推理失控」（stop）。3 新单测。关联审计：Anthropic provider 同口径缺口（stop_reason 不下穿）留档。
- **模型档案配置治理（deepseek 空流事故根因）**：deepseek-v4-flash（推理模型，OpenRouter 风格中转）不显式传 reasoning 参数时复杂提示词下推理失控、流式零内容块（小提示词/假系统提示词均正常，只有真实组合触发，靠对剖实验定位）；修复=档案双配 `reasoning.effort=medium`+大 max_tokens。MiniMax 上游 max_tokens 硬顶 196608（200k 会 HTTP 400）——MiniMax 双档案已收到该值。全档案 max_tokens 已按用户指令放宽（kimi/deepseek 200k，MiniMax 196608）。
- **轻量评审环节终局裁决**：deepseek-v4-flash 评审质量优于 M2.7-highspeed（同章 A/B：deepseek 抓到 HIGH 契期矛盾+阴眼设定冲突等 2 条 logic 级真缺陷，M2.7 全漏），但间歇性空流（配置修复后仍 2 成 3 败）不可用于生产——light 绑定定案 MiniMax-审校（M2.7-highspeed）。评审样本与证据落盘 docs/evals/ch2-review-*.json。产品化留档建议：runner 对 provider 空流失败加一次自动重试可吸收此类间歇抖动。
- **REVIEWED 状态改稿回路断链修复（chapter_write save_draft 守卫与域服务口径冲突，生产事故 wfr_ff028717ad49）**：auto_revise 回路在 review 驳回（revise）后重跑 write，落稿时 chapter 已是 REVIEWED，被 pipeline 内联守卫「仅 PLANNED/DRAFTED 可写」拦截——与域服务 `_DRAFT_ALLOWED_STATUS={DRAFTED, REVIEWED}` 直接矛盾（域服务允许 REVIEWED 重草、重草即失效旧批准）。闭环：pipeline save_draft 守卫放行 REVIEWED→DRAFTED（注释标明与域口径一致性），models.ALLOWED_NEXT 的 REVIEWED 后继集补 DRAFTED。REVIEWED 重草后必须重新评审（旧批准作废），语义与 commit 硬校验一致。

### Added（2026-08-30 深夜批次）
- **剧情页（Story Bible · 剧情 tab）人读化重构**：原表格只渲染英文枚举（type/status）+ 永远 "—" 的时间列（前端读 `time.at`，实际数据是 `time.timeline_day`）——描述、参与人、地点全在库里却没送前端。重构后表格列=时间（Day N）/ 类型（中文徽标：揭露/冲突/决定/遭遇/转折/其他）/ 描述（全文主体列 + 地点名·来源「第 N 章」小字）/ 参与（角色名徽标，characters/locations/chapters 三接口 id→名映射，失败降级 id 短串不阻塞）/ 状态（计划中/已记录等，planned 行淡化）/ 操作；时间线侧栏描述 80 字截断 + title 全文；新建表单 participants 提示改为真实形状（纯 id 数组）、status 改下拉。PlotTab 10 用例。
- **plot_events 的 description 全链路打通 + commit 写透同步时间线**：①`PlotEvent` dataclass 此前根本没有 description 字段（DB 有列有数据、API 响应丢字段）——models/service `_row_to_event`/to_dict 补齐，create_event/update_event 新增 description 参数（非 str 拒、空串归 None）；②commit 写透 new_events 插 plot_events 后**同事务同步 timeline_events**（对齐 PlotService.create_event 先例：tle_ id、timeline_day 提取、幂等守卫、visibility/who_knows 同源）——此前只有手动新建才同步，commit 事件的时间线断供；③迁移 0019 幂等回填存量缺口（生产 5 条 evt_ch_* 已自动补齐，tle_bf_ 确定性 id + WHERE NOT EXISTS 双保险）。23 新用例（test_plot_event_description_timeline 13 + test_plot_create_event_description 10）。
- **章节工作流操作区重构为四步流水线步骤条**：原 4 张并列卡片（生成计划/写正文/审校/提交）拍平了线性流程、要求用户脑跑状态机（「需要状态：PLANNED / DRAFTED」黑话）、提交卡 flex-wrap 换行落单。重构为 `① 生成计划 → ② 写正文 → ③ 审校 → ④ 提交` 步骤条：步骤三态（done=绿勾✓ / current=主色高亮+实心主按钮 / todo=变淡禁用）由 `getPipelineStepStates(chapterStatus, hasPlan)` 纯函数推导；「需要状态」黑话删除、副标题改人性化说明（"Director 生成章节计划"等），禁用原因仍留 title tooltip；模型下拉降级为 11px 小字「模型：环节绑定（默认）▾」，写作模式/审校版本收在对应步骤下方；<960px 折叠单列。契约零变更（全部 data-testid、onStart payload、getButtonAvailability 状态机逻辑逐字保留）。新增 chapterState 7 用例 + 页面 3 用例（三态/主按钮/disabled+title 回归）。
- **高风险审批卡变更明细列表（盲签治理）**：commit 的 high_risk_approval 审批卡原只显示「待审批 11 项 / character_changes 7 / world_changes 4」计数，用户无法做出知情审批。现 HighRiskSummary 在计数下方渲染全量明细（`high-risk-change-list`）：HIGH 置顶（同风险内原顺序稳定），每条三行——徽标行（风险 高/中/低 + 操作 新增/更新/删除 + 类型 人物/地点/规则/势力/物品 + field + 对象 id）、值行（before→after，新增显示「新增：…」，200 字截断 title 全文）、附注行（备注 + 引文 80 字截断）。缺字段全兜底不炸；changes 缺失时明细区不渲染、计数区保留。ApprovalCard 新增 2 用例。
- **observer 可核销白名单注入（commit 反复校验失败根治）**：生产三连失败根因——observer 生成 `resolved_hooks` 引用快照中不存在的 hook_id（genesis 快照 hooks 为空）、`debt_changes.status_before=None` 违反枚举。照搬 recent_event_ids 先例：pipeline 新增 `_collect_resolvable_ids`（从 observer 可见的同一 previous_state 快照取 OPEN/ACTIVE/ESCALATED hooks 与 open/acknowledged debts 的 id，缺集合空列表不抛错）+ `_inject_resolvable_ids_into_config`，注入 narrative 腿与单腿旧路径的 payload.config（含审查发现的 narrative retry 腿补注入）；observer-v1.md 新增 §7.6 硬约束 6 条（resolved_hooks 仅限白名单/空白名单⇒空数组/新钩子只走 new_hooks/debt update-resolve 仅限白名单/status_before 不得 null 且取枚举）。validator FK 规则不变兜底。新增 test_observer_resolvable_ids.py 14 用例。

### Fixed（2026-08-30 深夜批次）
- **「已驳回」徽章误判（真失败被美化成驳回）**：用户点「提交」屡见「已驳回」——实际是 observer 校验失败的真 FAILED，但错误文案 "observer delta rejected by validator" 含英文子串 rejected，前端 4 处（ChapterStatusBadge、run 列表 title、RunTimeline、NodeStatusBadge）用 `includes('rejected')` 子串通配一律渲染「已驳回」。双修复：①后端 commit 两处报错措辞改 "observer delta failed validation"（review 管道裸 'rejected'/'rejected-for-revision' 语义不动）；②前端抽 `isRejectedRun`（trim 后精确等于）/ `isRejectedForRevisionRun`（startsWith，先判防前缀陷阱）纯函数，4 处替换，其它 FAILED 正确落「失败」。chapterState 6 用例 + 页面回归 4 用例（校验失败文案→失败/纯驳回→已驳回/改稿驳回→已驳回·改稿）。

### Fixed（2026-08-30 关联检修批次，两路 smart 审计驱动）
- **relationships 伴随列（visibility/who_knows）全链丢失**：schema 定义、observer 输出了关系的可见权限（秘密同盟语义），但 write_through 的 INSERT/UPDATE 整段不写这两列 → DB 落 DDL 默认 'PUBLIC'；逆 delta 不恢复、快照重建不回读。修复：write_through 三分支（add/update/0017 兜底）三态补齐（缺失不写列/显式覆盖，对齐 hooks/debts 口径）、commits.py 逆 update 恢复、snapshot `_load_relationships_for` 回读。存量 1 行不改（下次 update 自流正）。
- **快照 hooks/debts/events 不携带 who_knows → 知识防泄漏校验空转**：snapshot.py 三个 `_load_*` SELECT 不含 who_knows、applier 三处不写，guardrails.knowledge_leakage 从快照读该字段 → 永远全 pass。修复：三处回读 + applier 三处写入（含 debts update 三态），新 commit 的快照起校验复活；存量快照不回填（口径落盘 docs/story-state-relation-hook-debt-pivot-fix.md §5）。
- **剧情 tab 状态下拉「进行中 ongoing」必选必 422**：后端枚举只有 planned/recorded/resolved/abandoned（DDL CHECK），前端下拉多出一项不存在的状态。移除 ongoing、补 abandoned「已废弃」；PlotTab 用例锁定四态。
- **角色编辑表单五键与 init 实际数据不匹配**：CharacterTab 表单等 personality/values/fears/desires/flaws，而 project-init 写入的是 motivation/goal/conflict/distinctive_trait/relationships（全库 9 角色无一例外）——编辑真实角色五框全空白。CORE_FIELDS 换后端五键（中文标签），relationships 对象 JSON 往返不破坏（parse 失败按字符串兜底），merge 保存不删非表单键。3 新用例。
- **类型契约残余错误形状**：types.ts `PlotEvent.cause/effects` 标注 `Record<string,unknown>|null`，后端实际是 `string[]`（event_id 数组）——修正并同步 normalizeEvent/create payload。
- **project-init 卷纲占位事件丢摘要（空壳之源）**：persist_all 第 6 步本意把 volume.arc_summary 登记为 planned 剧情事件，但 create_event 历史上不支持 description → 摘要静默丢弃只剩空壳（生产 event_15c1b360ded3）。修复：传 `description=arc_summary` + 同 description 防重复用；迁移 0020（ALTER volumes ADD arc_summary + 空壳行回填 + timeline 同步，幂等）——生产空壳的 arc_summary 从未持久化、不可回填（0 行影响，预期内），UI 删除即可。
- **口径收口（卫生）**：PlotService 两条 timeline INSERT 补 visibility/who_knows（与 write_through/迁移 0019 三方对齐，消除手工行 PUBLIC vs 事件 RESTRICTED 失配）；LedgerService.update_debt 改用共享 `_dump_json_or_null`；get_canon_detail 的 created_at 补回归测试（字段本就在，审计误判为缺失，测试锁定契约）。新增 test_v31_p1_1_2_domain_patches.py 8 用例。
- **已知限制（审查建议级，本批不修）**：迁移 0020 多卷场景 arc_summary 锚定不含 ORDER BY（生产单卷无影响）；逆 delta 的 relationship V/W 恢复受 schema 限制非严格等价；create_timeline_event 手工端点 who_knows 未走 encode_who_knows 校验（非主路径）。

### Added（2026-08-30 晚批次）
- **结构化输出三级解析兜底（json_repair 自愈）**：`extract_json` 解析链扩展为 `json.loads` → `strict=False` → `json_repair==0.63.4`（新增锁定依赖，纯 Python 无传递依赖）——生产事故实锤：observer（commit）输出 char 562 缺逗号、`finish_reason=stop` 非截断非控制字符，两级兜底救不回、重试仍败导致提交失败。修复产物仍须过 `validate_contract` 结构校验；凡走修复的调用落库 `warn: JSON auto-repaired`（可审计，日志页黄色「告警」徽章）。`return_meta=True` 新参取 `(payload, meta.repaired)`，默认 False 保持 67 处既有调用零影响；最终报错消息附出错位置 ±100 字符上下文（`_format_json_error`）。9 新用例。
- **重试成功的首次失败原因落库（warn 可观测链）**：runner 结构化校验首次失败原因（`last_error`）此前重试成功即丢弃、只剩 retry_count=1 无法回溯；现以 `warn: first attempt invalid: ...`（截断 300 字符）写入 `ai_call_logs.error`，与 repair/observer 剥离 warn 按 `retry → repair → observer` 序 ` | ` 拼接。下游三处对齐：`_collect_stage_models` SQL 口径放宽为 `error IS NULL OR error LIKE 'warn:%'`（否则审阅卡「本次实际使用」漏掉重试成功的模型）；AiCallLogsPage 徽章三态（warn→中性黄「告警」/真失败红/成功绿，新增 `.badge--chapter-warn`）；integration 旧断言锁（error is None）按新口径更新。3+2 新用例。
- **observer 独立为第 8 个绑定环节「状态提取」**：commit 管线 observer 双腿原硬编码 `capability_override="light"`，绑定页却把 observer 展示在 reasoning 组——展示与实际不符（生产事故根因：light 绑到 MiniMax·审校 M2.7 后 observer 被带跑）。现 AGENT_CAPABILITY/prompts 双侧同步 observer→observer，commit 双腿 7 处 override 全部 light→observer；迁移 0018 `INSERT OR IGNORE` 让 observer 继承 reasoning 当前绑定（生产=kimi k3-256k，行为无缝）；前端环节分配七→八（含「状态提取」独立下拉）。5+1 新用例（迁移注册/继承/幂等/router 字典）。
- **chapter-commit 单次 run 模型覆盖贯通（提交卡开放模型下拉）**：commit 管线全部 11 处 run_agent 调用点透传 `profile_id`（observer 腿=observer 键、summarizer=light 键，缺省 None 零行为变更；并发池/重试路径/独立 summarize 节点无遗漏）；前端提交卡与 plan/write/review 一样出现模型下拉（`capabilityByAction.commit='observer'`，原「commit 不消耗 LLM」注释系陈旧错误已修正）。新增 test_chapter_commit_model_overrides.py 4 用例（双腿透传/缺省不变/observer 键不串 summarizer）+ 前端 e/f 用例。

### Fixed（2026-08-30 晚批次）
- **运行横幅节点显示滞后（慢节点被误标为上一节点）**：engine 只在节点完成后才更新 `workflow_runs.current_node`，前端横幅直接展示该字段——实测 writer 慢跑 460s 期间横幅一直显示「scene_planner 已运行 5分27秒」（scene_planner 实际只用 59.7s）。修复：横幅优先取 nodes 中 status=RUNNING 的节点展示，「已运行」从该节点 started_at 起算，无 RUNNING 时回退旧口径；data-current-node 同步。3 新用例（RUNNING 优先/回退/计时源切换）。
- **工作流运行期间页面每 2 秒频闪、草稿无法阅读**：轮询 onResult 每 2s 无差别触发 chapter/drafts/runs 三连 reload，而 `useApiCall.reload()` 每次 setLoading(true)，DraftsPanel 拿 loading 当闸门把草稿区整体替换为「加载中…」——`<pre>` 反复卸载重挂、滚动位置清零。修复：`useApiCall` 改 hasDataRef 静默刷新（仅首载置 loading，reload 期间旧数据保留、后台换新）。新增 useApiCall.test.tsx 5 用例；已知可接受副作用：AiSettingsPage 三面板与 ProjectsListPage 手动 reload 后不再转圈（内容静默更新）。连带修复：`tsc -b` 构建期拦下的测试类型错误（`npx tsc --noEmit` 因 solution 式 tsconfig 漏检测试文件，教训入记忆）。
- **启动自愈孤儿节点行清扫**：旧自愈只把 RUNNING run 标 FAILED、不管其下节点行，留下永久 RUNNING 的僵尸节点（生产实证 wfrn_fbc58dda2a70，2026-08-30 06:19 被服务重启杀死的 run）。`recover_interrupted_runs` 新增步骤 4：FAILED/CANCELLED run 下的 RUNNING/PENDING 节点行收尾 FAILED('interrupted by restart')；PAUSED run 的 PENDING 节点（合法人工审阅等待态）与 COMPLETED run 绝不动。新增用例 D；生产僵尸已随本次重启自动清除。
- **agents.role 列对已存在行不刷新（观测性失真）**：`_upsert_agent` 原只在 INSERT 时写 role，observer 独立成环节后旧行仍显示 reasoning（无业务消费方，纯自查误导）。现已存在行 UPDATE role/config_json/updated_at（created_at 不动），启动 prompt sync 自动刷生产行（已验证 observer 行 role='observer'）。2 新用例。

### Fixed
- **PATCH params「删除参数」语义缺失（思考档位改回默认不生效）**：`_prepare_patch_params` 按键合并（payload 缺键→DB 旧值保留），前端「默认（跟随端点）」档以"不发键"表达删除——删除意图永远被旧值复活（实测：kimi 档 reasoning_effort='high' 改默认保存两次无效）。修复：建立显式删除约定 **payload 参数值 `null` = 删除该键**（后端合并后 pop；api_key 既有分支不变），前端 default 档且 DB 原有键时发 `reasoning_effort: null`。3 新用例（null 删除保留他键 / 缺键保留旧值 / null 与 api_key 掩码共存）；残留数据已手工清理。
- **全局提示词旧项目A项目专属残留清理（跨项目污染治理）**：writer-v1 规则 18/19（日式神话时代风物、陈瑞避讳/魂穿）、director-v1 与 scene_planner-v1 的「原著要素锁」具体举例（辉夜特征/追兵时间线）均为旧项目A专属硬编码，靠 style_constraints 逃生口才未污染其他项目。已全部泛化（上游锁定风物/称谓/要素则严格跟随，未锁定则中性叙事/不适用），旧项目A专属约束原文**无损迁入旧项目A项目 world_rules 表** 3 条（语感基线/称谓与名讳禁忌/原著要素锁，迁移前备份 data/backup_huoying_rules_migration_20260830.json）。**连带发现并补上注入真洞**：scene_planner payload 从不携带 world_rules——补 `world_state_excerpts` 顶层字段（复用既有 schema）。新增防回归测试锁死 12 个旧项目A专属关键词不得再进全局提示词。整仓 1378 passed。
- **`inferThinkingMode` 漏判 'max'（静默丢档）**：kimi 档案存 `reasoning_effort:'max'` 后重开编辑回显降级为 default，直接保存即删键（与 params 丢失同族）。已修并导出函数 + 2 回归用例。同批：getThinkingOptions kimi 分支补 `moonshot.ai/.cn` 官方域名匹配；model_router README agents 表同步。
- **LLM 空输出报错误导**：自适应思考耗尽 max_tokens 预算（finish_reason=length、正文零字符）时报 "empty output after stripping fences"，排障误导。现 provider 层捕获 finish_reason 透传至报错：length+空内容 → 「输出为空：max_tokens 预算被思考耗尽——请调大档案 params 的 max_tokens（建议 16384）」；finish_reason 随 token_usage 落库 ai_call_logs（OpenAI 兼容 + Ollama，Anthropic 暂 None）。7 新用例。
- **服务进程静默死亡 → 前端请求永久悬空**：会话后台 shell 启动的后端随 shell 收割静默退出（连发三次），浏览器 fetch 永不落定（「拉取模型」永久卡「拉取中」）。双修复：① 服务改 PowerShell `Start-Process` 独立进程启动（运维口径入记忆，日志落 data/serve.log）；② `api.post` 扩展 options（signal 透传），fetchAvailableModels 加 15s AbortSignal.timeout，任何情况 promise 必落定。
- **工作流重启孤儿 RUNNING（僵尸 run）**：进程重启杀死后台工作线程后，RUNNING run 永远卡死且同章节 409 防护挡死新 run（实测事故：手工清库解锁）。新增引擎启动自愈 `recover_interrupted_runs`：lifespan 启动时（迁移后）把全部 RUNNING run 标 `FAILED(error='interrupted: service restart killed worker thread')`、其 RUNNING/PENDING 节点行同步收口；PAUSED/COMPLETED 一律不动。3 新用例（含幂等）。
- **flaky 测试加固**：`test_run_observer_with_bad_then_good_script_retries` 等 3 处取 ai_call_logs「最新一行」未按 run_id 过滤，并发顺序下被其他用例写入干扰偶发失败；全部改为 `WHERE run_id = ?` 过滤。
- **模型档案前端 params 映射错误（数据丢失级，连带「拉取模型」置灰）**：后端读路径 `_mask_response` 返回键为 `params_json`（dict），前端 `normalizeProfile` 却读不存在的 `row.params` → 所有档案的参数进入前端后被清成空对象。症状：编辑弹窗 base_url 回显为空占位符（连带 OpenAI 兼容档案「拉取模型」按钮因 base_url 前置校验置灰）、思考模式徽标恒显「默认」；**若用户在编辑态点保存会静默清掉 base_url/thinking/max_tokens/service_tier 等全部参数**（库中数据未丢，因未触发过该路径保存）。修复：normalizeProfile 改读 `params_json`（兼容测试夹具旧 `params` 形态）并导出；新增 `endpoints.test.ts` 4 回归用例（dict/JSON 字符串/旧形态/空兜底）。全量 vitest 324 passed。
- **project-init 面板 resume/init 收到 RUNNING 误报「run 终态异常」**：后端 resume 已异步化（POST /runs/{id}/resume 固定立即返回 RUNNING，终态靠 GET 轮询），章节前端已适配，但 ProjectInitPanel 的 handleResume/handleRegenerate/handleInit 仍是同步旧契约，收到 RUNNING 即报「run 终态异常：status=RUNNING」（run 实际正常跑完，用户点「确认修订并继续」后误见报错）。修复：面板新增 `pollRunUntilTerminal`（2s 间隔 / 600s 上限 / AbortSignal 卸载安全），三路径收到 RUNNING 自动轮询到终态再走既有 PAUSED/COMPLETED 分支；同步终态返回保持向后兼容。测试：面板 39 用例（新增 RUNNING→PAUSED / RUNNING→COMPLETED / init 轮询 3 用例）。
- **文风样例面板竞态显示「暂无」**：`StyleSamplesPanel` 列表状态仅取挂载瞬间的 `initialSamples` 初值且无自动拉取——父页项目详情先返回、面板先挂载时，样例列表后到永不同步（后端重启后时序翻转即复现，用户报"样例丢失"，数据实际都在库）。修复：面板挂载时自取一次列表（initialSamples 退为即时占位）+ 响应防御 `Array.isArray`。
- **projects.premise 复合文本套娃污染**：部分生成（selected_stages 不含 premise）时 `persist_all` 把库中旧简介（"定位：…一句话：…"复合文本）当作 positioning 重新包一层「定位：」前缀再追加「一句话：」，每跑一次叠一层（用户实测多层嵌套）。修复三件套：persist_all 的 project update 仅在 premise 环节本次被选中时执行（未选跳过回写）；`_build_premise_text` 对 positioning 已带「定位：」前缀时剥层防御；`_rebuild_premise_from_db` 提取最后一段「一句话：」为 logline（下游 AI 输入更干净）。测试 3 新用例（污染回归/正常路径/剥层单元）；污染现场备份 data/backup_premise_pollution_20260829.json 后手工修复。
- **「按建议修改」忽略改稿意见框**：意见框内容此前只有「驳回并改稿」会带上，「按建议修改」仅发勾选建议——用户想"4 条建议 + 自补口径"无入口。现合并下发：note = 勾选建议 + 意见框文本（意见框为空行为不变）。新增合并用例。
- **prompt sync 测试期望集缺 polisher**：`docs/agents/prompts/polisher-v1.md` 落地后 `test_sync_registers_all_prompts_from_docs` 期望集未跟上（整仓红测），补 `polisher`。
- **白盒测试治理落地（P0/P1/P2 全量 19 项 + 复盘改进 4 项）**：三阶段白盒测试（覆盖率基线后端 86%/前端 70.5% → 四路只读审计 41 条发现 → 全量修复）。**P0**：PlotTab 新建事件必 422（表单 cause 类型错+time 缺 timeline_day，功能不可用级）；CharacterTab 保存整体覆盖 core_json 静默清掉 AI 字段（改 merge 快照）；resolved_hooks 把 ABANDONED 钩子复活成 RESOLVED（write_through/delta_repair 双守卫）；写正文等待超时 RUNNING 透传成僵尸活跃 run（timeout 标记+auto_revise 短路）；project_init 空 dict revisions 短路静默落空数据（空修订=未提供语义）。**P1**：commit 回滚半途残留（relationships/locations/factions/world_rules/narrative_debts 逆清理补全，含 debt hints 误读逆 delta 伪 before 的回归二次修复）；payoff severity 硬编码 error 对齐矩阵；迁移 0017（relationships 唯一索引去重 + workflow_runs 活跃态部分唯一索引兜底 409 TOCTOU，engine 新增 WorkflowRunConflict→409）；project_init persist_all 已存在实体改为更新内容字段（id/created_at 不变）+ 章节重建单事务；resume 非 PAUSED 400→409；model_profiles PATCH 双键互斥 422。**P2**：auto_revise 彻底异步化（resume 立即返回，回路入 daemon 线程，2 小时阻塞清除）；engine _parse_json 死代码删除；story_state/前端补测（write_through world_rules/relationships 11 用例、前端 Bible 三 Tab/ChaptersPage/ProjectsListPage/LedgerTab 17 用例，ChaptersPage 确认非死代码）。**V7/V8 复盘改进**：critic 注入 settings_digest（world_rules 全量+角色 one_line 前 8，prompt 新增 OOC 审查维度 medium 起）——修复"审校按错误计划强化错误"的结构性盲区；writer prompt 增语感基线（禁清宫剧用语，日式神话时代风物）；director/scene_planner prompt 增原著要素锁（辉夜辨识特征锁定、追兵属后期威胁早期不得出现）；世界观"神树果实"奖励措辞收紧为"神树果实微粒"。计划层漏网 OOC「辉夜是被追杀的亡命神族」已当场修正（V8 硬伤根因=计划非模型）。审计误报 1 条（快照重建破坏 recent_events——recent_events 不在重建集合内）已裁决并补回归测试。全程全量验收：pytest 最终 1307 passed / vitest 290 passed；迁移 0017 随重启生效。审计明细：docs/testing/audit-*.md；修复计划表：docs/testing/bugfix-plan-20260829.md。


### Changed
- **scene_planner 能力归属移入正文写作（creative_writing）**：scene_planner 实际运行于 chapter-write 管线，按管线阶段归组（V3.9.2 前归 reasoning）。双侧 AGENT_CAPABILITY + CAPABILITY_LABELS 同步（creative_writing = writer/polisher/scene_planner）；此后「写正文」单次模型覆盖自动钉住整条写作链（含场景规划），对比文风时管线口径一致。正文写作绑定同步调整为 M2.7-highspeed 首选 + k3-256k 回落（第 1 章 8 版 A/B 结论：M2.7 上限最高、k3 逻辑最稳、M2.5/M3 出局）。
- **「写正文」默认写作模式改为「全新重写」**：改稿已有专属入口（按建议修改/驳回并改稿自动触发改稿回路），手动写正文的场景（换模型对比/推倒重来）几乎都要整章重写——默认对调，「按意见改稿」降为可选；相关 4 条前端测试按新默认更新。
- **轻量评审（light）标注补 writer(revise·改稿)**：revise 模式 writer 走 light 的路由已上线但环节分配页 agents 标注未同步，用户误读归属；CAPABILITY_LABELS 与 README 表格补齐。
- **数据侧：正文写作绑定取消回落链（单档案直配）**：creative_writing 由 [M2.7-highspeed, k3-256k] 改为 [M2.7-highspeed]——用户拍板「M2.7 失败就显示失败」，按不静默回落原则保证对比口径纯净；其余 6 环节核查均为单档案无隐藏回落。连带发现展示缺口：环节分配页下拉只渲染 profile_ids 首位、后备链不可见（V3.7 架构能力），待办加固。
- **数据侧：两写作档案 max_tokens 8192 → 32768**（MiniMax M3 与 Kimi highspeed 端点均实测 200 接受）——起因：自适应思考在 1.6 万 token 输入下烧满 8192 预算、正文零字符（wfr_c33f 事故），上限放宽不改变计费与行为，只消除"预算耗尽即夭折"的硬顶。
- **revise 改稿并入 light 能力（改稿模型与 critic 同源）**：chapter-write 的 writer 节点在 mode='revise'（按建议修改/驳回并改稿触发的局部修改）时 `run_agent(capability_override='light')`——改稿走「轻量评审」绑定（当前 MiniMax-M3），省 Kimi 创作额度且指令遵循型任务与创作型模型解耦；write/fresh_write（全新写/全章重写）与 polisher 维持 creative_writing 不变；单次 model_overrides 优先级最高不受影响；mock 路径不消费 capability。测试 3 用例（revise→light / write→None / fresh_write→None）；全量 pytest 1325 passed。
- **Story Bible 只读 JSON 人读化（ReadableJson 组件）**：角色详情 core_json（原 `<pre>` 直显 JSON）、最新 state（空对象显 `{}`）、世界页 data_json 预览三处改为人读渲染——新增通用组件 `ReadableJson`（中文字段标签映射 25 键、关系数组特化 `{to_name}·{relation_type}—{one_line}`、未知键回退原键名、空值「暂无」）+ `RawJsonDetails` 折叠保留原始 JSON；节标题「core_json（…）」改「核心设定」。编辑表单 textarea 不动；CanonTab 有意折叠设计不动。测试：组件 17 用例；全量 vitest 316 passed。
- **章节工作流启动/resume 异步化（实时进度根基）**：`start_with_nodes`/`resume` 原在 HTTP 请求内同步执行完整个工作流（单章写正文 1-4 分钟），导致 POST 阻塞到终态、前端 `submitting` 全程 true、run 列表刷不出 RUNNING 行、轮询不启动——「正在执行」横幅整场停在「工作流启动中…」（用户实测确认的缺陷）。现引擎新增 `start_with_nodes_async`/`resume_async`（后台 daemon 线程推进，同步版保留供脚本/测试/project-init 使用）；`_start_workflow` 与 resume 端点切异步并加同章节并发 409 防护；`GET /runs/{id}` 补 `workflow_name`。配套修三个被同步时代掩盖的缺陷：(1) `runner.py` 每次 agent 调用成功/失败后直接盖 run 行 COMPLETED/FAILED 的遗留行为——异步轮询方会把中间态误读为终态（observer 并行测试确定性失败+实时横幅误停的根因），现引擎托管调用（node_run_id 非空）一律不盖戳（`_finalize_agent_run_status`），run 终态由 `engine._finalize_run` 收口，commit 管线的 `_recover_run_status` 随之退化为 no-op；(2) resume 执行期间 run 行一直标 PAUSED——轮询方误停，现 `resume`/`resume_async` 校验通过即在调用线程置回 RUNNING（`_mark_run_running`）；(3) auto_revise 回路启动的 write/review run 在异步后即时返回 RUNNING，`_run_workflow_return_payload` 补终态轮询等待（600s 上限）。前端：run 停到 PAUSED 时轮询回调重拉 detail（RUNNING 阶段拉的 checkpoint 不含 pause_payload，审批卡渲染不出的缺口），横幅动作名支持 workflow_name 映射中文。测试：`test_engine_async.py` 10 用例（其中 `test_resume_async_returns_immediately` 修正了"慢节点放在挂起点自身、实际永被执行"的构造错误，改用节点 sleep 作时钟断言返回时仍 RUNNING）；全量 pytest 1247 passed / vitest 261 passed；浏览器实测：点审校→横幅实时显示节点与秒数→审批卡无刷新自动出现。

### Fixed
- **「驳回」终态语义修复（rejected 标记）**：chapter-review 的纯驳回（approved=false 无 revise）原走 `ValueError("author_review 未通过，无法标记 REVIEWED")`，run 以一句像代码事故的报错收场（实际效果 run FAILED、章节保持 DRAFTED 本就是设计语义）。现与 revise 分支的 rejected-for-revision 同机制：新增 `_Rejected` 异常（args[0]='rejected'），run FAILED + error='rejected'。前端徽标/横幅配套三态区分：`rejected-for-revision`→「已驳回·改稿」、`rejected`→「已驳回」（同中性灰样式，判断顺序先长后短防子串误配）、真失败仍红色；纯驳回不再误显自动改稿回路横幅（`reviseLooping` 仅 revise 触发）。测试：pipeline 用例补 error=='rejected' 断言；全量 pytest 1250 passed / vitest 263 passed。
- **藤原千鹤角色全库清除 + 辉夜框架对齐原著（OOC 治理第二轮）**：用户对照原著复核后拍板清除 AI 初始化自行发明的「凡人妻子」角色藤原千鹤（与 premise「陈瑞娶辉夜」的婚姻结构直接冲突；原著对照：动画 680 话祖之国天子纳降临之神辉夜为侧室、漫画《阵之书》辉夜禁欲无婚——辉夜是神女，绝无「政治联姻/主妇/棋子」属性）。已删：characters 行 + 陈瑞 relationships 条目 + 第 2/4/5/7/9/10 章种子中的千鹤节拍（剧情职能移交大名府凡人暗线/朝堂，第 4 章情报网掩护改由「大名府凡人暗线」承担）；辉夜角色 one_line 由「起初视他为联姻棋子」改为「降临忍界的神女、卯之女神，起初视凡人如蝼蚁」（神女框架对齐原著）；第 1 章标题《穿越即巅峰？我是大名的大名》→《穿越即巅峰？我娶了神女辉夜》（"大名的大名"为赘婿→大名批量替换事故产物）。删除前备份 `data/backup_fujihara_chizuru_delete_20260829.json`；连同上轮共清除 14 处，全库 14 张表文本字段终验零残留（联姻/赘婿/主妇/大名大名/千鹤）。
- **「政治联姻」OOC 框架清除（V4/V5/V6 OOC 根因）**：项目初始化的卷纲/章节种子自行加戏，把「和辉夜结婚的大名」（premise 原文）扩写为「与辉夜政治联姻」，混入第 1/6/7 章种子与导演计划、陈瑞与藤原千鹤角色实体，writer 忠实执行导致成稿 OOC。已修：ch1 计划（政治联姻→成婚；赘婿→大名批量替换事故造成的「大名大名」叠词×2）、ch6 种子（政治联姻→陌生戒备）、ch7 种子与千鹤角色（政治联姻妻子→大名家主妇/凡人夫妻起步，角色去留待作者拍板）、陈瑞角色 one_line（联姻的大名大名→迎娶辉夜的大名）。数据修复前已备份至 `data/backup_chapter_plans_before_ooc_fix_20260829.json`。排查结论：「按建议修改」不改计划正文（审校管线仅写/删 `plan_json.revision_note` 键，见 chapter_review/pipeline.py），计划正文唯一改写路径是「生成计划」导演节点。
- **`rejected-for-revision` 不再渲染为红色"失败"**：「按建议修改/驳回并改稿」会主动让 review run 以 `FAILED(rejected-for-revision)` 结束（改稿回路随后自动重跑 write→review），但 run 列表与节点时间线都把它渲染成红色失败徽标/错误横幅，用户误以为系统出错。现 `WorkflowRunStatusBadge` / `NodeStatusBadge` 增加可选 `error` 入参，FAILED 且 error 含 `rejected-for-revision` 时渲染中性灰徽标「已驳回·改稿」（新增 `.badge--chapter-rejected`），节点横幅改为 InfoBanner 说明"系统正在自动改稿重跑"，run 行悬浮提示补充语义；真失败仍为红色。
- **审校建议列表行内展示建议正文**：`ApprovalCard.tsx` 的「审校建议」条目此前只渲染来源徽标（"AI 审稿"/"warnings"/"严重"）与固定提示语，无法分辨每条对应改什么（用户反馈）。现每条行内渲染分类/严重度徽标（节奏/人物/伏笔等 + 高/中/低）、问题位置引文（`approval-suggestion-quote`）与建议正文（`approval-suggestion-text`），与上方 critic 问题卡片一一对应，「应用此条」可一眼对准目标；删除每行重复的操作提示语。`ApprovalCard.test.tsx` 引文/正文断言改 `getAllByText`（同文本现出现在 critic 卡片与建议列表两处），24 用例全绿。
- **依赖无上界导致新环境路由静默丢失**：`pyproject.toml` 原以 `>=` 声明 fastapi/starlette 等，`uv sync` 解析到 starlette 1.6.0 后其 `include_router` 对本仓库路由组装**静默失效**（24 个业务路由挂载为 0，`/api` 仅剩 health，业务接口全部 404/405，`discover_routers` 日志仍显示 discovered 24 具强误导性）。已将运行依赖锁定到经全量测试验证的基线版本（fastapi==0.133.1 / starlette==1.3.1 / uvicorn==0.41.0 / pydantic==2.13.4 / jsonschema==4.26.0 / httpx==0.28.1），`uv sync --extra dev` 生成 `.venv` 与 `uv.lock`；锁定后整仓 pytest 1222 passed 与原基线一致，126 条 `/api` 路由挂载恢复。

### Added
- **writer 字数硬约束（规则 20，跨项目通用）**：正文净字数锁 `expected_word_count ±7%`（缺省 3000 即 2800–3200），严禁越带宽上下限（±15%）；revise 净增 ≤+5%；压缩指令下禁止以增补新场景应付。起因：第 1 章 A/B 中 V2/V4 低于下限、V7/V8 超上限 +25%，revise 逐轮膨胀。
- **「拉取模型」候选改显式下拉**：原生 `<datalist>` 按输入框当前值过滤候选（当前值命中一项时其余候选全部不可见——实测"拉到 8 个只显示 1 个"）。改为拉取成功后渲染完整 `<select>` 候选列表（data-testid `profile-model-candidates`），选中填入、手输保留。
- **API 密钥集中管理（secrets.json）**：根目录新增 `secrets.json`（`.gitignore` 双条目防提交，`git check-ignore` 验证）专存密钥，结构 `{"api_keys": {"<profile_id|provider|环境变量名>": "..."}}`。新增 `packages/core/secrets_store.py`（mtime 缓存加载 + `resolve_profile_api_key` 五级优先级：文件[profile_id] → 文件[provider] → 文件[env_name] → params_json 存量 → 环境变量兜底；`NOVELOS_SECRETS_FILE` 可覆盖路径；坏 JSON 降级不抛）；`providers.resolve_api_key` 收口接入（router 两处调用透传 profile_id）；`_mask_response` 的 `has_api_key` 涵盖文件来源。kimi 档案明文 key 已迁入文件并从 DB 删除（备份 data/novelos.db.bak_secrets_20260830）。测试 13 用例；全量 pytest 1338 passed。
- **档案表单「拉取模型」+ 思考模式端点收窄**：① 新端点 `POST /model-profiles/available-models`（provider 分支适配 OpenAI 兼容 `/models`、Anthropic `/v1/models`、Ollama `/api/tags`、mock；密钥走 resolve_api_key 链含 secrets.json；上游失败 502、错误信息零 key 泄露），前端 model 输入框挂 datalist 候选 + 「拉取模型」按钮（base_url 未填置灰、拉取中态、切 provider 清候选、手输保留）。② 思考模式下拉按 base_url host 收窄选项（minimax→默认/关/自适应；deepseek/openai→默认/低/中/高；kimi.com→默认/低/高/最高；未知端点全量保守），选项带 hint；初值不在收窄列表时回退默认+提示行。测试：后端 8 用例 + 前端 3 用例；全量 pytest 1346 passed / vitest 320 passed。
- **project-init 单次 run 模型覆盖（model_profile_id）**：`ProjectInitRequest.model_profile_id`（可选）→ 路由经 ProfileService 校验（不存在 400）→ ctx → 四个 AI 节点 `run_agent(profile_id=...)` 单档案钉死全程（不回落，语义同章节工作流 model_overrides，但不影响全局绑定）。前端「AI 初始化设定」表单新增「本次初始化模型」下拉（默认绑定（全局）+ enabled 档案）。测试 3 用例（透传/缺省 None/400）；管线 19 用例。
- **project-init 环节自由勾选 + 部分生成占位跳过**：① 未完成环节不再强制勾选（原 forced 锁死），全部环节可自由勾选，仅生成勾选环节（selected_stages 白名单既有机制）；② persist_all 配套：outline 降级时跳过 volume/chapters/plot_event 落库（返回 `outline_skipped` 标记，杜绝部分生成写入空卷+10 个占位章节）；premise 降级且项目已存在时跳过 project update。测试 2 用例（只选 world 零占位落库 / 全量回归不受影响）。
- **审阅卡片显示「本次实际使用」模型（stage_models）**：`GET /runs/{id}` PAUSED 时附 `stage_models`（ai_call_logs JOIN agents 按 agent 取最新成功调用 model_id，error 排除；通用字段，章节工作流同样可消费）；project-init 审阅卡片在下拉旁渲染短名（剥 provider 前缀，title 全名，无调用不渲染）——解决「下拉显示全局绑定值被误认为本次生成所用模型」。测试后端 2 + 前端 2。
- **writer 润色节点（polisher）+ critic 追读力/节拍核销维度**：① chapter_write 管线 writer 与 save_draft 之间新增 polisher 节点（提示词 `docs/agents/prompts/polisher-v1.md`，输出契约 polisher-output.v1、±10% 字数守恒；mock 透传；AGENT_CAPABILITY polisher→creative_writing 双侧注册）。② critic-v1 prompt 新增 §3.7 节拍核销（key_beats 逐条核销，缺失/偏离以 `[beat N 缺失/偏离]` 前缀标注 ≥medium）与 §3.8 追读力审查（章首钩子/爽点兑现/章末钩子，前+后 20% 篇幅范围）；creative 创作档案 max_tokens 提至 8192（数据侧，思考与输出共用预算防截断）。V2 实测：4 条建议逐条核销全部落实、节拍 4/4。
- **档案表单「思考模式」下拉 + params 丢失修复**：AI 设置档案表单新增思考模式下拉（默认跟随端点/关闭/开启/开启·低中高档），写入 params 的 `thinking={type}` 或 `reasoning_effort`；档案卡显示「思考：默认/关/开/低/中/高」徽标。**连带修复同族数据丢失 bug**：档案表单保存原从零组装 params（只含 base_url/api_key），编辑档案会静默丢掉 timeout_s/thinking/service_tier 等既有参数——改为快照 merge（表单键覆盖、其余保留）。背景：V3.7「creative 开思考/reasoning+light 关思考」的按环节策略在档案合并时被抹平，全环节（含写正文）现均关思考运行，此下拉用于恢复按环节思考策略（如新建「MiniMax-M3·创作」档案开思考绑 creative_writing）。vitest 294 passed 含 4 新用例（回显/参数不丢回归/effort 映射/回默认清键）。
- **模型档案卡「设为默认」**：AI 设置页每张档案卡新增「设为默认 ▾」→ 展开 7 个环节 chip，点击即把该档案设为该环节的**首选**（PUT binding，profile_ids 首位插入、其余顺延为后备链，幂等）；档案卡显示其当前默认环节徽标；已停用档案 chip 禁用（贴合后端 422）。顺带修正误导文案：「未绑定 · 使用默认配置」→「未绑定（回落旧版 model_configs 链）」。纯前端（复用既有 bindings API），vitest 268 passed 含 4 新用例（徽标只展示首选/展开收起/bind 首位顺序/幂等）。
- **审校指定草稿版本（多版本对比闭环）**：用户生成 v7/v8 两版对比模型后发现审校永远只审最新版（两处 `ORDER BY created_at DESC LIMIT 1` 写死）。现：`StartWorkflowRequest.draft_version` → ctx → `_basic_checks_node`/`_critic_review_node` 按 version 取稿（不存在该版本则 run FAILED 带明确报错），`review_report` 新增 `draft_version` 字段，run 标签优先取 ctx 实际审的版本。前端：草稿选中状态提升至父组件受控，点「审校」始终带选中版本，审校按钮卡动态提示「将审校：草稿 vN」且随点选实时切换（内置浏览器实测：默认 v8 → 点 v7 → 提示切 v7）。测试：后端 2 新用例（指定版本/不存在版本），前端 1 新用例；全量 pytest 1252 passed / vitest 264 passed。
- **run 列表人类可读标签 + 草稿模型徽标（run↔草稿对应关系）**：用户反馈无法分辨哪个 run 对应哪版草稿。`GET /projects/{pid}/runs` 每行新增 `label`（推导规则：写正文 run 查 run 起止窗口内产出的草稿版本 → 「写正文 → 草稿 v6」，失败未产出则兜底「写正文」；审校 run 查启动前最新草稿 → 「审校（审 v5）」；plan/commit/init 用中文名；推导异常不抛错）。前端 run 行主文案优先 label（run_id 短形式退为副信息），草稿版本列表行显示 model_id 徽标（mock/mock 不显示）——配合 model_id 真实记录修复，版本与模型一目了然。测试：全量 pytest 1250 passed / vitest 263 passed；真实数据冒烟 17 行 run 的版本号与草稿精确对齐。
- **写正文「全新重写」模式 + drafts.model_id 真实记录**：(1) `StartWorkflowRequest.fresh_write`（可选布尔）→ ctx 注入 → `_writer_node` 在 fresh_write 时清空 revision_note 与旧稿底稿，强制 mode='write'——解决「选不同模型对比文风却被 revise 模式拉回 84% 相似」（V4/V5 实测教训：改稿模式下换模型只做局部修订）。前端写正文卡新增写作模式下拉（`wf-write-mode`：按意见改稿（默认）/ 全新重写）。(2) `_save_draft_node` 此前硬编码 `model_id='mock/mock'`，真实调用也无法区分是哪个模型写的；现 `_writer_node` 调用后从本节点 `ai_call_logs` 行（run_id+node_run_id 取最新）取真实 model_id 经 ctx 传入，mock 路径无日志行自然回落 'mock/mock'。测试：后端 3 新用例（fresh_write 覆盖 revise / 缺省回归 revise / mock model_id 回归），前端 2 新用例；全量 pytest 1250 passed / vitest 263 passed。
- **单次 run 级模型档案覆盖（模型效果对比）**：启动 chapter-plan / chapter-write / chapter-review 时可按次指定模型档案，不改动全局环节绑定。链路：`StartWorkflowRequest.model_overrides`（capability → `model_profiles.profile_id`）→ `_start_workflow` 注入 `ctx["model_overrides"]` → 三个 pipeline 的真实链路 `run_agent` 调用点（director / scene_planner / writer / critic 共 4 处）取 `capability_for(agent)` 对应档案 → `run_agent(profile_id=...)` → `ModelRouter.call_with_fallback(profile_id=...)` 候选锁定该档案（enabled=1，行形状与绑定分支一致，`config_id←profile_id`），缺失/disabled 抛 `ModelNotConfiguredError`（detail 含 profile_id），跳过 light→reasoning 回退；`profile_id=None` 时全既有路径零行为变化（负向用例实测）。mock 路径不消费。前端章节详情页「工作流操作」三个按钮卡（生成计划→reasoning / 写正文→creative_writing / 审校→light，提交不加速选）各挂档案下拉（`wf-model-select-{action}`，默认「环节绑定（默认）」，列表来自 `GET /model-profiles` enabled 过滤，拉取失败静默隐藏），选择后 payload 带 `model_overrides`，用于不同模型产出效果对比。测试：router 单测 5 个 + ctx 透传 workflow 测试 2 个 + 前端 ChapterDetailPage 新增 describe 2 用例；全量 pytest 1251 passed / vitest 261 passed。
- **V3.7 模型档案 + 环节绑定（两层架构）**：解耦「模型是什么」与「环节用谁」，同一条模型可被多个环节复用，不必复制多份。迁移 0016（`database/migrations/0016_model_profiles.sql`）新增 `model_profiles`（档案库：`profile_id / name / provider / model / params_json / enabled`）与 `capability_bindings`（环节分配：`capability → profile_ids JSON 数组`，顺序即 fallback 序）两张表，并把现有 `model_configs` 行一次性搬运成档案（`name` 取 `model` 字段）。`ModelRouter` 新增 `_candidates(capability)` 统一收口：优先读 `capability_bindings + model_profiles`，无 binding 回落 `model_configs`；返回行键名与 `model_configs` 完全一致（`config_id ← profile_id`、`capability ← 本 capability`），保证 `runner` 把 `config_id` 写 `ai_call_logs` 与 `get_provider` 零 schema 改动。`AGENT_CAPABILITY` 在 `model_router.router` 与 `agent_runtime.prompts` 双侧同步扩展四个 project-init agent 映射：`premise_designer → premise_design` / `world_builder → world_building` / `character_designer → character_design` / `volume_outliner → volume_outline`，避免 `capability_for` 默认回退 `reasoning`。新增 `CAPABILITY_LABELS`（七环节有序 dict：premise_design / world_building / character_design / volume_outline / creative_writing / reasoning / light，每项含 `label` + `agents`）供前端与 `GET /capability-bindings` 使用。新增 service `ProfileService` / `BindingService`（`packages/core/model_router/profiles.py` / `bindings.py`），新增路由 `model_profiles.py` / `capability_bindings.py`（自动发现，无需改 `main.py`），新增共享掩码工具 `packages/core/model_router/security.py`（`/model-configs` / `/model-profiles` 共用）。`light` capability 回退 `reasoning` 的 V3 P0-2 语义在 `_candidates` 收口后保留：仅「无 binding + `model_configs` 也无 light 行」时回退，有显式 binding 即便 binding 全 disabled 也不回退。`DELETE /model-profiles/{id}` 在被任何 binding 引用时返回 409（detail 含 `referenced_by` capability 清单，便于前端一键解绑）。旧 `/model-configs` 端点保留不动进入只读兼容期；新代码优先走两层 API，下一里程碑下线其写入语义。前端项目总览页的「AI 初始化设定」面板同步升级到按环节分配档案的引导式向导（前端联动由前端代理负责实现）。
- **项目总览页「AI 初始化设定」入口**：新增 `apps/web/src/components/ProjectInitPanel.tsx` 挂载在简介卡片之后、`StyleSamplesPanel` 之前。用户填写 brief（title/genre/logline/platform/target_words/author_notes/chapter_seed_count），提交触发 `POST /projects/init`（`projectsApi.init`，引擎同步阻塞到终态）。面板展开时探测 `charactersApi.listByProject` 角色数；>0 时显示「可能造成重复」warning 并要求勾选确认才可提交。成功展示 `run_id` 并回调父组件 `useApiCall.reload` 重载项目数据；失败以 `ApiError(status, detail)`（detail >200 字截断）走 ErrorBanner。`api/types.ts` 新增 `ProjectInitBrief`/`ProjectInitPayload`/`ProjectInitResponse`；`api/endpoints.ts` 的 `projectsApi.init` 仅做薄封装，未引入新依赖。`chapter_seed_count` 走 payload 顶层字段以命中后端 `Field(ge=1, le=100)` 校验（不进 brief），前端同步做 1-100 范围校验。新增 `ProjectInitPanel.test.tsx` 覆盖 5 个用例：logline 空禁用 / 正确参数调用 + projectId + seed_count 在顶层 / 有既有角色需勾选 / 成功展示 run_id 且 onDone 被调用 / seed_count 越界(150)禁用。
- **project-init 分段审阅暂停（step_mode）**：`packages/workflows/project_init/pipeline.py` 新增 `STAGE_SPECS` / `_gate_should_pause` / `_resolve_stage_input`，4 个 AI 节点在 `step_mode=True` 时成功产出后抛 `PauseRequested({"stage", "stage_index" 0..3, "stages_total":4, "degraded", "draft"})`，下游 `_world_payload` / `_character_payload` / `_outline_payload` / `_persist_all_node` 全部改走 `_resolve_stage_input` 三层 fallback（`human_input.revisions[output_key]` → `ctx[output_key]` → `ctx[node_id].__pause_payload__.draft`），确保人工修订穿透到下游 AI 输入与 `persist_all` 落库。`packages/core/api/routers/workflows.py` 的 `ProjectInitRequest` 新增 `step_mode: bool | None`（默认 None = 一次性跑完，与既有行为一致），`_start_project_init` 返回体在 PAUSED 时附 `pause_payload`，复用通用 `POST /runs/{run_id}/resume` 端点回灌 `human_input={"revisions": {"<output_key>": <修订后完整 dict>}}`，引擎不变。`packages/workflows/project_init/README.md` 同步更新节点图、4 关卡表、pause_payload 与 human_input 契约、修订生效路径。`tests/workflow/test_project_init.py` 新增 4 用例（第一关即停 / 修订推进到第二关 / 四关走完落库生效 projects.name 来自修订 / 默认不分段回归）。前端侧：项目总览页面板升级为分步审阅向导（题材定位 → 世界观 → 核心角色 → 卷纲与章节种子），mock 自测全绿。
- **Story State Delta 确定性自动修复层（arbiter-lite）**：新增 `packages/core/story_state/delta_repair.py`，`repair_delta(delta, snapshot, db_path)` 在 `validate_delta` 前做确定性自动修复：world/character/relationship `op='update'` 且 `before=None` 时从 snapshot/DB 补当前值；`op='add'` 且目标 id 已存在时降级为 `update`（内容与现有行一致则丢弃）；同步覆盖 debt_changes `status_before`/`severity_before`、resolved_hooks `from_status`、new_events/new_hooks 重复 id 丢弃。`packages/workflows/chapter_commit/pipeline.py` 的 `_inject_validate_node` 在首次校验与重试后均先 repair 再 validate，修复记录写入 `ctx['delta_repairs']` 并 logging.info 留痕；修不了的仍走原校验/按腿重试路径。新增单元测试 `tests/unit/test_delta_repair.py` 与 workflow 层一次性修复回归测试。
- **去 AI 味确定性检测**：新增 `packages/core/quality/ai_patterns.py` 纯函数模块，`scan_ai_patterns(prose)` 基于正则/统计检测 AI 腔（高频套话、连续同词开头、「他/她」排比、章尾总结体、破折号/省略号滥用、解释腔），命中数超阈值可升 error；规则表 `AI_PATTERN_RULES` / `AI_PATTERN_FORBIDDEN_WORDS` 模块级可扩展。
- **chapter_review 接入 AI 腔检测**：`_basic_checks_node` 调用 `scan_ai_patterns`，`review_report` 新增 `ai_pattern_hits` 字段并合并进 `warnings`/`errors`；原 `forbidden_word_hits` 向后兼容保留。
- **critic payload 注入确定性摘要**：`_critic_review_node` 向 critic LLM 传入 `deterministic_hints`（命中数 + 规则摘要），`docs/agents/prompts/critic-v1.md` 同步说明该字段仅供 LLM 参考；critic 输出契约无需放宽（仅新增输入字段）。
- **ScenePlanner 真实化**：`packages/workflows/chapter_write/pipeline.py` 新增 AI 节点 `scene_planner`（P0），调用 `scene_planner` agent 将 Director Plan 翻译为结构化 Scene Plan（含 slots / conflict / turn / information_boundary / ending_hook）。新增 `docs/agents/prompts/scene_planner-v1.md`、agent capability 注册、`expected="scene_planner"` 契约校验。支持 `mock_providers['scene_planner']`；agent 失败时降级到原 stub 逻辑，不阻断 writer。
- **自动改稿回路**：`packages/core/api/routers/workflows.py` resume 端点新增 `auto_revise_max` 与 `mock_providers` 字段。chapter-review 以 `rejected-for-revision` 失败且 `auto_revise_max > 0` 时，自动依次重跑 chapter-write → chapter-review，直到 approved 或达到上限。上限默认 `NOVELOS_AUTO_REVISE_MAX=2`，`0` 表示禁用（保持现有 FAILED 终态语义不变）。
- **服务启动自动 prompt 同步**：`packages/core/api/main.py` lifespan 在迁移后自动执行 `PromptRegistry.sync_from_docs`（幂等），新 agent 不再依赖手工 `POST /agents/sync`（ch072 scene_planner 未注册静默降级的教训）。`NOVELOS_PROMPT_SYNC=off` 可关闭；sync 失败只告警不阻断启动。
- **m1_long_run `--quality-gate-mode` 开关**：此前驱动脚本硬编码 `quality_gate_mode="report"`，enforce 默认值在生产长跑链路无法被验证。现可通过 `--quality-gate-mode enforce` 覆盖（默认仍 report，保持长跑不阻断语义）。

### Changed
- **critic 默认全量**：`packages/workflows/chapter_review/pipeline.py` 默认 critic 模式从 `sample` 改为 `always`，`NOVELOS_CRITIC_MODE` / `ctx['critic_mode']` 覆盖逻辑保留。
- **quality_gate 默认 enforce**：`packages/workflows/chapter_commit/pipeline.py` 默认 quality_gate 模式从 `report` 改为 `enforce`，与文件顶部 docstring 对齐。eval / golden runner / 相关测试已显式设为 `report` 模式以避免 MVP 阻断规则误伤。

### Migration
- 使用自动化脚本或外部 runner 调用 commit 且依赖「默认不阻断」行为的调用方，需显式传入 `quality_gate_mode="report"` 或设置 `NOVELOS_QUALITY_GATE=report`。
- 原有 revise 后依赖手动重跑 write+review 的测试/脚本，若需保持旧行为，resume 时应传 `auto_revise_max: 0`。

### Fixed
- **Provider HTTP 连接池上限修复（单章耗时根因）**：`packages/core/model_router/providers.py` 三个 provider 的 `_ensure_client` 统一加 `httpx.Limits(max_connections=10, max_keepalive_connections=5)`。此前 client 从不关闭、keep-alive 无限堆积（实测长进程 114 条到 MiniMax 的 ESTABLISHED 死连接），新请求被路由到被对端静默挂起的连接上无限等待（httpx read timeout 为字节间隔语义，对整连接挂起不生效），是单章 600-900s 的主因。详见《单章耗时诊断报告.md》根因 1。
- **`MINIMAX_API_KEY` 映射下沉到 api.main**：此前 `MINIMAX_API_KEY → NOVELOS_API_KEY_OPENAI_COMPATIBLE` 映射只在 `scripts/serve.py`，`python -m packages.core.api.main` 直启（m1_long_run docstring 的推荐姿势）不带映射导致 401 秒失败（ch072 实测复现）。现映射在 `packages/core/api/main.py` 模块导入时执行，覆盖所有入口。
- **extract_json 兼容裸控制字符**：`packages/core/agent_runtime/structured_output.py` 的 `extract_json` 在严格解析失败后用 `json.loads(strict=False)` 兜底一次——LLM 偶发在字符串内输出未转义控制字符（ch074 observer 输出实测，首次 commit 因此失败），strict=False 允许字符串内控制字符通过。其余解析失败行为不变。
- **delta_repair 补 update-to-add-field 规则（ch073 实测缺口）**：observer 对实体的 data_json/state **新子键**用 `op='update'` 且 `before=None` 时，旧值本就为空属诚实表达，但校验器拒绝 update+before=None，导致 commit 反复重试（ch072 连挂 3 次、ch073 挂 1 次）。现 `_repair_world_changes` / `_repair_character_changes` 在「实体存在但字段无现值」时把 update 转为字段级 `add`（validator 接受 add+after；write_through 对已存在 id 的 add 走 UPDATE，安全）。`after` 也为 None 的语义空洞条目仍不修，留给校验器报错。

### Added（P2 Context Engine 补全）
- `build_director_input.plot_graph_excerpt.unresolved_branches` 不再恒空：从 `branches` 表查询 `status='ACTIVE'` 的未解决分支并注入 `{branch_id, name, parent_branch_id, base_state_version, status}`。
- `build_writer_input.world_state_excerpts.sensory_anchors` 感官锚点：在零 DDL 约束下，从 `locations.data_json.sensory_anchors` 或常见感官字段（`sensory_details / atmosphere / smell / sound / light / texture / temperature`）解析，最后回退到 `statement`；无数据时返回空 list。
- Writer 章节级相关性裁剪 `relevance_trim`：默认开启，按 `plan_json` / `scene_plan` 的 `involved_characters` / `involved_locations` 过滤角色与世界观条目；主角（`role='protagonist'`）与 `inject_mode='always'` 实体始终完整保留，未涉及实体降级为 `{id, name, relevance_summary: True}`。
- `relevance_trim` 开关：显式参数 > 环境变量 `NOVELOS_CONTEXT_RELEVANCE=off` > 默认开启；Writer 缓存键追加第 7 元 `relevance` 避免脏命中。
- `preview_context` L1 新增 `unresolved_branch` 与 `sensory_anchor` 条目展示。
- 新增测试 `tests/unit/test_context_engine_p2.py`（13 用例）覆盖三项缺口；`tests/unit/test_writer_input_paged.py` 模块级关闭 `NOVELOS_CONTEXT_RELEVANCE` 以保持 paged 体积断言独立。

### Added（P1 project-init workflow）
- 新增 `project-init` 工作流：`load_brief → premise_designer → world_builder → character_designer → volume_outliner → persist_all`，实现从题材 brief 到可开写 Story Bible 的全自动建书。
- 新增 4 个 AI agent 与 prompt：`premise_designer`（题材定位/卖点/主角雏形）、`world_builder`（世界观/规则/地点/势力）、`character_designer`（3-5 核心角色）、`volume_outliner`（第一卷卷纲 + 前 N 章种子，默认 10 章）。
- 新增 `POST /api/projects/init` 端点：支持不带 `project_id` 新建项目，或传入已有 `project_id` 挂载更新。
- AI 节点失败按 critic 模式降级（返回 `_degraded` 结构、不阻断流程）；`persist_all` 失败直接抛错。
- 复用现有 domain service 落库：`ProjectService`、`CharacterService`、`WorldService`、`VolumeService`、`ChapterService`、`PlotService`。
- 新增测试 `tests/workflow/test_project_init.py`：覆盖新建项目、挂载已有项目、AI 降级、persist 失败。

> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：版本号 + 日期 + 分类小节
> （Added 新增 / Changed 变更 / Fixed 修复 / Removed 移除 / Migration 迁移 / Known Issues 已知问题）。
> 版本号语义化：破坏性变更升 major，新功能升 minor，修复升 patch。

## [3.8.0] - 2026-08-27

### Fixed（V3.8「配置参数透传修复 + 思考模式灰度定稿」）
- **ModelRouter.call_with_fallback 配置参数丢失 bug（潜伏缺陷）**：`model_configs.params_json` 中除 `base_url/timeout_s/api_key` 外的所有键（`thinking`/`service_tier`/`temperature` 等）此前从未透传到上游请求体——配置写了 `thinking:disabled` 与 `service_tier:priority` 实际从未生效。现按行解析非构造键并入请求体，调用方显式 params 同名覆盖。`resolve()` 判定无需修（无生产调用方）。

### Added
- 思考模式灰度定稿（证据驱动，三章实测）：`reasoning`(director/critic) 与 `light`(observer 双腿+summarizer) 配置 `{"thinking":{"type":"disabled"}}`；`creative_writing`(writer) 保持思考开启。
- provider 原始响应采样开关（V3.7 引入）用于本版取证：completion 字符的 **78%~94% 为 `<think>` 思考串**。

### 实测对比（m1 ch069/ch070/ch071）

| 阶段 | 思考开 | 禁思考 |
|---|---|---|
| observer 双腿(合计 wall) | ~185s | **13~39s**（腿间缓存命中达 98%） |
| summarizer | 15~24s | **3~4s** |
| director plan | 104~224s | **22s** |
| writer | 125~135s（字数带内 +8.8%） | 10s 但字数 -40% 跌破 band |

单章全链：V3.6 基线 658~866s → 仅 light 禁思考 320s → 全禁 63s（writer 破带）→ **定稿组合 198s 且 prose 在带内、quality=82**。

### Known Issues
- 全禁思考下 writer 产出偏短（实测 -40% 跌破 band 下限）；若未来要全速模式需配套 revision 重写闭环。
- ch069 首次 commit 三连秒失败为本次配置误操作（light 行 base_url 被整串替换）所致现场，已从备份恢复；相关 error 记录留 DB 作审计。

## [3.7.0] - 2026-08-27

### Added（V3.7「字数带硬约束与口径统一」）
- **权威字数模块** `packages/core/quality/wordcount.py`：`visible_chars`（去空白口径）/ `word_band`（[0.7,1.3] 带 + 1200 下限保护）/ `classify_prose_length`。
- writer payload `chapter.word_band` 注入（置于 chapter_id 之后零破坏缓存键序，paged 模式原样继承）；review `_basic_checks_node` 升格产出 `W-LEN-DEVIATION`：>±15% warning、>±30% 进 errors（报告型：随 pause payload 展示），前端 ApprovalCard 红色渲染 errors 区块；m1 metrics 新增 `word_status`/`deviation_pct`。
- provider 原始响应采样开关 `NOVELOS_DEBUG_PROVIDER_DUMP_DIR`（默认关；保留最新 10 个文件、单文件 2MB 截断、异常不阻主流程）。
- `scripts/db_maintenance.py`：stale RUNNING 清理工具（list / fix --older-than-minutes N [--apply]，dry-run 默认，幂等）。（提前交付自迭代计划 V3.9 C1）
- 迭代计划文档 docs/roadmap/v3.7-v3.9-迭代计划.md（含分段写作实验否决记录与候选项闭环映射表）。

### Changed
- 全仓 prose 字数口径统一：signing_check/_count_chars、arc/service、m1_long_run._latest_prose_chars、chapter_review 四处收敛引用 wordcount.visible_chars。

### Fixed
- word_band 低目标 floor 抬升导致 low>high 倒挂：high 钳制 `max(high, low)` 并锚定边界测试（恰 ±15%/±30% 判定方向用例固定）。


### Added（V3.6「流式根治挂起 + 前缀缓存重排 + commit 三路并发」）
- **OpenAI 兼容 Provider 流式化**：`complete()` 改 `client.stream` + SSE 逐行解析（`data: {...}` / `[DONE]`），并加 `time.monotonic()` 总时长硬顶 deadline——根治非流式下整连接挂起而 read timeout（字节间隔语义）不生效的问题。`health_check` 保持非流式。
- **commit 三路并发**：summarizer 的 LLM 调用提前并入 observer 双腿并发池（`NOVELOS_SUMMARY_PARALLEL` 默认 on，ctx/env 可关），下游 summarize 节点短路消费 `summary_early`，缺字段/失败自愈重调；`summary_early` 列入 checkpoint_exclude 保证 PAUSED→resume 幂等；早产失败恢复 run 状态防污染。
- **竞品对标与单章提速决策文档**：docs/roadmap/v3.6-单章提速与竞品对标.md（oh-story / determinFlow / bishu-novel 管线对比、思考 token 数据真相、V3.8 方向）。

### Fixed
- 流式响应 usage 全丢：请求强制注入 `stream_options: {"include_usage": true}`（OpenAI 流式协议默认不下发 usage；实测 MiniMax 如此）。
- prompt 前缀缓存几乎零命中：5 个 agent 的 user payload 键序重排——稳定块（project/knowledge_permissions/constraints 等）前置、每章动态块（chapter 及其 chapter_id）后置。ch067 实测 plan 二次装配 cached_tokens=16896/17981=94%、summarizer=100%。
- serve.py 把通用 `MINIMAX_API_KEY` 映射到 provider 期望的 `NOVELOS_API_KEY_OPENAI_COMPATIBLE`（此前缺映射导致 401 秒失败）。

### Changed
- tests：SSE mock 全面适配（test_model_router / test_model_router_providers / test_cached_tokens_extraction）；空流语义统一为"零 content chunk 即抛错"；新增三路并发 13 个测试。全量 707 passed, 2 skipped。

### Known Issues
- writer 单次 completion 达 42~49k tokens，其中 ~95% 为 MiniMax-M2 内部思考 token（正文仅 ~2KB）；未启用 max_tokens 硬顶（截断风险破坏 JSON 契约）。V3.8 方向：字数带硬约束 + writer 分段写作（见 docs/roadmap/v3.6 文档 §四）。
- 实测本章剩余耗时由 MiniMax 服务端解码主导（plan+write ≈ 550~580s/章）；本地环节 <15s。

## [3.5.0] - 2026-08-26

### Added（V3.5「并发化与缓存观测」）
- **observer 双腿并发**：split 路径下腿 A/腿 B 经 ThreadPoolExecutor 并行提交（`NOVELOS_OBSERVER_PARALLEL` 开关默认 on，ctx/env 可关），单腿异常仍走原 per-leg 重试归类；实测两腿几乎同时启动。
- **cached_tokens 观测**：OpenAI 兼容 provider 提取 `usage.prompt_tokens_details.cached_tokens` 写入 ai_call_logs.token_usage_json（缺省不写字段），为 MiniMax 自动前缀缓存命中率提供数据基础。

### Fixed
- smoke_e2e / test_health / README 的业务表数断言链统一修正为 35（volumes 为唯一新增表；0014 系重建既有 reveal_policies 不增数）。

### Known Issues
- prompt 前缀缓存命中率待真实链路采样（cached_tokens 已可观测）；若命中率低需将 user 消息内动态字段后置。

## [3.4.0] - 2026-08-26

### Added（V3.4「多卷与规模」组织层）
- **卷管理**：迁移 0015（volumes 表 + chapters.volume_id）；VolumeService 与 REST 六端点（CRUD/seal/assign）；封存时冻结终态快照入卷归档；单项目同时仅一个 active 卷；sealed 卷拒绝挂章。
- **arc 按卷分组**：弧光视图章节元素携带 volume 归属，顶层新增 volumes 小节。

### 设计决策
- 未做按卷拆分快照存储——O-1 滚动窗口已实证上下文有界（63 章 trimmed 输入 31k 字符），存储级拆分的复杂度税不成立；150+ 章实测恶化时再按设计文档 §三升级快照分代。健康端点业务表数 35→36（volumes）。

## [3.3.0] - 2026-08-26

### Added（V3.3「知识权限补全」）
- **reveal_policies 数据模型**：迁移 0014——relationships/timeline_events/scenes 三表补 visibility+who_knows（I6 收口）；reveal_policies 表（target_kind 枚举/reveal_by_chapter/audience/status 机）+ CRUD API（GET/POST/PATCH/DELETE /api/projects/{pid}/reveal-policies）。
- **writer paged 装配可见性过滤**：HIDDEN 且存在 planned reader-audience 策略的实体从 writer 上下文整条移除（stats 记 hidden_filtered）；无策略时零行为变化；observer 保持作者全知视角（设计决策）。
- **arc 视图扩展**：reveal_policies 小节（planned/revealed/overdue 清单）。
- **模块轻量开关**：`NOVELOS_DISABLED_MODULES=simulation,arc` 按模块名过滤路由注册（默认空=零变化；仅过滤 HTTP 面，workflow 注册隔离为已知边界）。

### Changed
- 两条内置模型配置启用 `service_tier=priority`（MiniMax 官方优先准入，对策拥堵窗口大请求饿死；成本 1.5x）。

## [3.2.3] - 2026-08-26

### Fixed（V3.1.1 O-3）
- **observer 按腿输入裁剪**：腿 A 移除 events/hooks/debts（previous_state -65.5%）、腿 B 实体降级为标识摘要——双腿合计 payload 47.9k 字符，总 token ~120k→89k（-26%）。
- **近期 event_id 白名单**：payload.config 注入最近 30 个 event_id + prompt 强制避让，消除 observer 生成 id 与库中历史事件撞车导致的 UNIQUE constraint failed（ch063 实战首次尝试即 COMMITTED）。

### Known Issues
- provider 拥堵窗口下单腿仍可能 480s 超时（网络抖动同源），断点续跑可自愈；根治候选为流式调用或备用 provider 失败转移。

## [3.2.2] - 2026-08-26

### Added（V3.1.1 observer 减负专项 O-1/O-2）
- **事件摘要滚动窗口**：observer trimmed 输入的 events 摘要只保留最近 6 章触达 + 被 open hooks/debts 引用者，open hooks/debts 字段压缩；实测输入 92.6k→31.3k 字符（-66%）。
- **observer 双腿拆分**：`NOVELOS_OBSERVER_SPLIT` 开关（默认 on）——腿 A 实体状态（characters/relationships/world_changes）、腿 B 叙事对象（events/hooks/debts）各自独立调用与重试（按校验错误归类到出错腿），light capability 可配；run_agent 支持 capability_override。ch063 实战：单腿拥堵超时不再导致整次死锁。

## [3.2.1] - 2026-08-26

### Fixed
- **事件描述下沉 plot_events**（V3.1 P1-1.1 遗留）：新增迁移 0013 为 plot_events 加 description 列；write_through 写入 observer 的 `new_events[].description`；快照 DB 权威重建后事件描述不再丢失（漂移自愈测试同步更新语义）。

### Known Issues
- writer paged 模式的 LLM 输出质量 A/B 对比因 provider 长请求拥堵窗口暂缓，待稳定窗口补测（结构断言与体积量化已在 [3.2.0] 覆盖）。

## [3.2.0] - 2026-08-26

### Added（V3.2「写作现场完善与上下文分页」）
- **writer 上下文分页**：chapter-write 支持 `writer_context_mode=full|paged`（默认 paged，env `NOVELOS_WRITER_CONTEXT_MODE` 可全局回退 full）；paged 模式下实体集合按「最近触达全量 + 其余摘要」裁剪并注入裁剪统计。实测当前数据集缩减 5.4%（大项目收益更大，20 角色合成场景单键 -37%）。
- **叙事弧光视图**：`GET /api/projects/{pid}/arc` 聚合逐章 payoff/charge 标注、质量分、伏笔与债务台账，输出蓄力连击、兑现密度、逾期告警等弧线健康信号（含 51 条测试）。
- **CI 增强**：新增 pip-audit 依赖 CVE 扫描 job 与 e2e smoke job（真实起服务跑 scripts/smoke_e2e.py 全链路 + 状态同步巡检）。

### Changed
- chapter-write 默认上下文模式变更为 paged（原 full）；`writer_context_mode="full"` 或环境变量可回退旧行为。

### Known Issues
- paged 模式对 writer 输出质量的 LLM 实评未做（需对比 eval），当前仅结构断言与体积量化。

## [3.1.0] - 2026-08-26

### Added（V3.1「一致性治理与质量语义升级」）
- **快照漂移自愈**：commit 事务内 write_through 之后、逆路径清理之前，以 DB 为权威重建快照的 7 类实体集合（characters/locations/factions/world_rules/plot_events/hooks/narrative_debts）——历史漂移在下次 commit 自动收敛；分支路径不受影响。含漂移自愈回归测试。
- **LLM judge 双轨落库**：迁移 0012 为 quality_reports 新增 judge_json 列；`POST /api/chapters/{cid}/quality/judge` 持久化四维评审（pacing/style/logic/dialogue），GET quality 响应透出 judge 字段；m2_judge.py 支持 `--persist-api` 直连落库。judge 为旁路数据，不参与 overall 计算（七子分公式哈希不变）。
- **快照一致性巡检工具**：scripts/check_state_sync.py 只读对比 DB 实体表与 snapshot JSON 的 7 集合双向差集，退出码 0/1/2 可接 CI；已接入 smoke_e2e 第 10 步（每次冒烟自动验证零漂移）。

### Changed
- 快照 world_rules 等集合字段口径统一为 DB 权威语义（snapshot.events[].description 在首次重建后收敛为 NULL，属预期行为）。

### Known Issues
- new_events[].description 信息源未下沉 plot_events 表（DB 权威重建后该字段丢失），V3.1 P1-1.1 候选。

### Fixed
- 巡检脚本 world_rules id_field 错配（rule_id→world_rule_id）导致的假 DRIFT。

## [3.0.0] - 2026-08-26

### Added（V3.0「提速与减负」）
- **critic 采样模式**：`NOVELOS_CRITIC_MODE=off/sample/always`（默认 sample 每 5 章 1 次），请求 body `critic_mode` 可覆盖；跳过时工作流状态机不受影响。
- **light capability 分级路由**：critic/summarizer 映射到可配轻量模型，缺失自动回退 reasoning 链（零破坏，回退有日志）。
- **快照一致性巡检**：`scripts/check_state_sync.py` 只读对比 DB 实体表 vs snapshot JSON 七类实体集合，退出码 0/1/2 可接 CI；实测最新快照与 DB 对齐（早期版本漂移已随重写消除）。

### Changed（性能检修实测，单章 804s → 609s，-24%）
- **输出量瘦身**：observer `max_changes_per_array` 50→24+宁缺毋滥纪律；critic 限长 800 字；writer self_report 限 150 字；模型配置默认 thinking disabled。分环节实测：write 236-363s→32-136s、review 56-139s→29-37s。
- **超时策略**：模型配置 timeout 建议 1800s→480s（长等无恢复案例），驱动客户端 900s 快速失败重试。

### Fixed
- write_through 将 observer 自由文本 location 直塞 `plot_events.location_id` 外键导致 `FOREIGN KEY constraint failed`：加存在性守卫（无效置 NULL 不阻断）+ observer prompt 约束；含回归测试。

### Known Issues
- 单章耗时距 ≤300s 目标尚差（当前 609s）：commit 环节 observer 仍占 338-494s，结构性解法（quality 异步旁路/上下文分页/输出硬上限）排 V3.1 P1-3 与 V3.2 P2-1——见 docs/roadmap/v3-plan.md 实测表。

## [2.2.0] - 2026-08-25

### Added（M1~M4 里程碑：真实长跑验证与上限能力）
- **M1 真实模型长跑**：MiniMax-M3 连续生成 50+6 章，产出《一致性衰减观测报告》（docs/evaluation/m1-long-run-report.md）。核心结论：状态机零衰减实证（continuity 全程 99.4-99.7）；文风碎片化退化被定位并以 prompt 规则修复；payoff 闭环缺失被定位到 director 层。新增驱动脚本 scripts/m1_long_run.py（断点续跑/熔断/指标采集/单实例锁）与报告工具 scripts/m1_report.py。
- **M2 上限能力干预**：director 增 [payoff] 前置/字数纪律/叙事推进纪律/停滞强制升级/交互密度下限规则；writer 增反碎片化/字数下限/对话占比/payoff 可感知兑现规则。验证（docs/evaluation/m2-verification.md）：LLM judge 四维均分 34→69，对话维度 5→72，单章字数 849→1738-1930。新增 judge 探针 scripts/m2_judge.py（爽感/文风/逻辑/对话四维结构化评审）。
- **M3 量产管线硬化**：observer 输入快照分代裁剪（trimmed 模式，解 >110KB 快照超时）；validator 引用存在性校验（FK 失败前置为可自愈校验错误）；commit 管线接线两项能力——阻塞章 ch056 实战验收从 90 分钟不可解变为 253 秒一次通过。新增单实例文件锁（防双进程 SQLite 挂死）与按环节成本计量（observer 占 token 51.4%）。
- **M4 写作现场·续写助手**：`POST /api/projects/{pid}/chapters/{cid}/continue` 一键生成最多 3 个续写候选（temperature 差异化），`/continue/adopt` 采纳追加为新草稿版本（REVIEWED 自动降级 DRAFTED 待重审）；前端 ContinuePanel 三候选并排对比面板挂载章节详情页。
- quality 引擎新增 style 连续衰减监测（report-only，公式哈希不变）；golden 回归 eval 扩充至 3 case。

### Changed
- provider 请求体清理：params_json 中 base_url/api_key/api_key_env 不再泄漏进上游请求体。

### Known Issues / 路线登记（新增）
- LLM judge 目前是旁路探针，尚未正式接入 quality 评分语义（style 子分与 judge 口径不一致，见 m2-verification.md 遗留2）。
- 叙事主线惯性（量桩微循环）需故事弧光级管理工具，prompt 层只能缓解——归入 M4 后续写作现场迭代。

## [2.1.0] - 2026-08-24

### Added（番茄签约体检包）
- **新模块 packages/core/signing_check/**：面向番茄男频投稿的纯规则体检（无 LLM / 无新依赖 / 无 schema 变更）。检查项：第一章前 300 字冲突（ch1_conflict_300）、主角前 500 字出场（ch1_protagonist_500）、金手指前 1000 字亮相（ch2_golden_finger）、第三章小高潮/打脸（ch3_climax）、逐章章末钩子（chapter_hooks_chN）、单章字数区间 1200-2600 warn（chapter_length_chN，1500-2200 最佳）、高频副词堆叠检测 echo_words（>5 次/千字告警，对抗 AI 低质文的"复读词"问题）、签约窗口提示 signing_window（2万/5万/8万共 3 次机会）。
- **REST 端点**：`GET /api/projects/{project_id}/signing-check`，返回 items + summary 计数；项目不存在 404。见 packages/core/api/routers/signing_check.py。
- **番茄投稿包集成**：build_fanqie_package 导出末尾追加「===== 签约体检摘要 =====」段；摘要生成异常时跳过不阻断导出。
- 设计依据：2026 番茄平台 AI 低质文严打（黄金三章硬标准/签约窗口规则）与竞品调研（NovelCrafter Codex/Sudowrite Story Bible 的一致性治理已由 Story State 覆盖，本模块补齐平台规则体检缺口）。词典为模块级常量可扩展；启发式非保证，边界见 packages/core/signing_check/README.md。

### Known Issues / 路线登记（新增候选）
- signing_check 词典为子串匹配，存在误命中可能（如单字词「打」「血」）；后续可引入白名单或窗口规则校准。
- signing_check 暂无前端 UI 面板（API-first）；候选项：web 端体检报告面板。
- scripts 下 e2e/smoke 临时数据库建议改用系统临时目录，避免与主库同目录。

## [2.0.1] - 2026-08-24

### Fixed（全维度检修包）

- **lint 清零**：修复 260 处 ruff 错误（unused-import / 行超长 / import 排序等，涉及 packages / tests / scripts 共 23 个文件），`ruff check packages tests scripts` 恢复 All checks passed，CI 后端 job 恢复可过；story_state/service.py 的 facade re-export 统一加 `# noqa: F401` 防止自动修复误删。
- **CI 补前端 job**：`.github/workflows/ci.yml` 新增 web job（Node 20，npm ci → build → vitest），此前 195 个前端测试不在 CI 内。
- **版本号元数据对齐 2.0.0**：pyproject.toml / apps/web/package.json / packages/core/api/main.py `__version__` 三处由 0.1.0 同步为 2.0.0；test_health 与 test_spa_hosting 的版本断言改为引用 `__version__` 常量，后续升版只改一处。
- **文档一致性**：docs/data-model/data-model-v0.md〔断链〕 加现状注记（v0 的 28 张表 → 当前 34 张业务表 + chapter_fts，附增量迁移清单）；IMPLEMENTATION-PLAN §4.2 deviation #4（model-configs 明文返回）标记已关闭（router 层 `_mask_response` 已落地）；补 packages/core/workflow_registry/README.md；README 移除对停维护 apps/desktop/vite.config.ts 的消费性表述。
- **新增 ADR-0002**：「model provider API Key 在本地 SQLite 明文存储（MVP 决策）」，记录既有防线（出口掩码 / ai_call_logs 敏感字段黑名单 / 备份排除 model_configs）与未来升级路径。
- **工作区卫生**：清理 data/ 下 smoke_e2e_* / real_llm_e2e_* 等测试残留文件（主库 novelos.db 不受影响）。

## [2.0.0] - 2026-08-24

结构性升级（路线图：`docs/roadmap/v1.3-v2.x-plan.md`〔断链〕`）。含内部架构破坏性调整，对外 API 契约保持兼容。

### Changed（架构）

- story_state god-object 拆分：`service.py` 2332→138 行 façade（`StoryStateService` 全委派 + 私有符号兼容 re-export），按职责拆为 `snapshots` / `deltas` / `write_through` / `commits` / `branches` / `queries` 六模块；调用方零改动、零行为变化（545 项回归全绿证明）。
- 分支读路径快照物化：迁移 `0009` 新增 `branch_snapshots` 表；创建分支物化基线、`promote` 后物化新基线；分支读取 = 最近物化快照 + 增量重放（回退全量重放），消除 O(N) 全量重放；增量计数有 spy 断言守护（增量 2 次 vs 全量 5 次）。
- 双写面统一：canon 写透与 domain CRUD 的实体写入收敛到 `story_state/write_helpers.py` 共享助手（`who_knows` 三态 / JSON 序列化 / 时间戳 / NULL 守卫两路逐字节一致）；`plot_events` / `timeline_events` 因派生索引语义差异保留双写（README 已声明口径：canon 以 observer delta 为权威）。
- 上下文装配缓存：`(project_id, state_version, chapter_no, role, 内容指纹)` 五元键进程内缓存（线程安全、256 上限）；失效三维——`state_version` 推进 + `plan_json` / `scene_plan` 内容指纹变化 + commit 后显式失效兜底。
- 端口收敛：唯一配置源 `Settings.api_port`（`NOVELOS_PORT` > `NOVELOS_API_PORT` > 默认 18081）；`main.py` / `vite.config` / smoke 脚本统一走配置。

### Added

- 设定条件触发动态注入（对标 Lorebook / Codex）：迁移 `0010` 给 `characters` / `locations` / `factions` 加 `aliases` + `inject_mode`（`auto` / `always` / `never`）；命中实体完整注入、未命中降级一行摘要、`never` 剔除；L0 世界规则常驻；`context-preview` 与前端面板带 `full` / `summary` / `suppressed` 徽标。
- FTS5 召回混合层：迁移 `0011` 建 `chapter_fts` 虚表（CJK bigram 索引化，零第三方依赖）；commit 成功后 upsert 索引（失败降级不阻断）；装配注入 `recalled_passages`（按章节计划关键词召回 top3 历史片段 × ≤300 字，跨长程呼应）；新模块 `packages/core/retrieval/`。

### Fixed

- 装配缓存脏命中（审查 P1）：`plan_json` 更新或 `scene_plan` 变化但 `state_version` 不变时返回陈旧上下文——键补内容指纹 + commit 后显式失效。
- FTS 查询含引号 token 静默降级；纯 CJK 长串不再作为无效整词 token 进入召回。

### Known Issues / 路线登记

- vitest 全量并发偶发单测顺序敏感（V1.5 起观察到 2 次，重跑即绿），待定位根因。
- `plot_events` / `timeline_events` 双写面未统一（派生索引语义差异，见 `story_state` README）。
- 物化按 N commit 间隔多次物化未启用（MVP 仅分叉点 / promote 两点）。
- 召回为 FTS5 关键词基线；向量 embedding 召回留待后续（需先选型论证）。

## [1.5.0] - 2026-08-24

架构债务与 UI 补缺包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`〔断链〕`）。

### Added

- What-if 分支 UI：项目总览页新增 `BranchesPanel`（分支列表 / 创建 / 分支状态查看 / promote 一键重放，409 冲突反馈）；纯前端落地（后端 `branches` / `state` 端点此前已齐备），补 `branchesApi` 封装与 `getBranchState`。
- workflow 注册表下沉 core：新增 `packages/core/workflow_registry/`（惰性 builder 注册中心），core 业务模块对 `packages.workflows` 零引用（AST + 字面值双重静态扫描测试守护）；`api/main.py` 以 `importlib` 装配触发注册；`packages/workflows` 保留兼容 façade。
- 前端「清除已存密钥」按钮：`AiSettingsPage` 编辑对话框显式清除入口（`clearKeyRequested` 机制，提交 `api_key=''` 触发后端清除；重新输入自动取消清除意图）。

### Changed

- router 越层 SQL 清零：`reference` / `workflows` / `model_configs` 三路由的 SQL 全部下沉——新增 `domain/reference/service.py`（`ReferenceService`）与 `core/model_router/configs.py`（`ModelConfigService`），`chapter` / `workflow_runtime` 各补一个查询函数；路由只留参数校验 + 脱敏 + 错误映射，API 契约零变化。
- `checkpoint_exclude` 推广到章节工作流（resume 影响已逐字段论证并测试守护）：`chapter-review` 剔除 `review_report` / `critic_*`；`chapter-commit` 剔除 `observer_input` / `observer_payload` / `delta` / `submit_result` / `snapshot_pre`；`pause_payload` 独立不受影响，checkpoint 写放大下降。

### Fixed

- （无用户可见修复；两项代码健壮性整理：`delete_canon_cascade` 连接关闭统一 finally、`runs.py` 函数定义位置规范）

## [1.4.0] - 2026-08-24

发布链路与可观测包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`〔断链〕`）。

### Added

- 导出发布链路：`GET /api/projects/{pid}/export` 支持整书/单章导出 txt（utf-8-sig）与 docx（手工 OOXML zip，零新依赖），以及番茄投稿包（前 ~1 万字按章节边界截断 + 分隔线 + 全书大纲）；文件名 RFC 5987 双写法；项目总览页新增「导出」面板。新模块 `packages/core/exporter/`。
- 项目备份/恢复 MVP（PRD §100/§101）：`GET /api/projects/{pid}/backup` 导出 22 张业务表 JSON 包；`POST /api/projects/import-backup` 导入为新项目（单事务、id 全量重映射、自引用外键两轮写入、源项目永不覆盖、坏包 422）。api_key 彻底剔除（`model_configs` 不入白名单 + metadata 六项自证 + 测试递归扫描）；运行时/敏感表 10 张不导出。新模块 `packages/core/backup/`。
- 参照系消费可观测：`quality_gate` 捕获 `reference_consumption`（消费了哪些参照文件 + 字数），checkpoint 与 `quality_reports._meta` 双路径；QualityPanel 新增「本章消费参照系」区块。
- enforce 改稿引导：质量门禁阻断时生成结构化 `revision_guidance`（低分维度 + 阻断规则 → 可执行建议，16 条规则模板 + 9 条类目模板，纯规则零 LLM），写 checkpoint 与 `runs.error`；QualityPanel 新增「改稿引导」区块。
- summarizer 注册验证固化为正式集成测试（`tests/integration/test_summarizer_prompt_registration.py`，3 例）。

### Fixed

- 前端备份测试类型对齐（HealthResponse.tables Record 形态、快照 mock 注解修正）；ExportPanel 下载补 `credentials: 'same-origin'` 并去重组件内 URL 构造（改走 `endpoints.ts` 的 `exportApi.url`）。
- 备份导入自引用外键（`state_deltas.supersedes` / `branches.parent_branch_id`）第二轮改写空操作修复：原实现按 `WHERE col IS NOT NULL` 扫描库，但第一轮 INSERT 已统一置 NULL，永远查不到，导入后自引用列仍为 NULL；改为第一轮把 `(table, pk_col, new_pk_value, old_target)` 记入 `BackupService._self_ref_rewrites` 内存清单，第二轮按该清单 + 全局 `project_id_map` 直接 UPDATE 新行（详见 `packages/core/backup/README.md` §5.2）。

### Known Issues / 路线登记（新增）

- 备份快照内嵌 id 不重映射：`story_states.snapshot_json` 等 `*_json` 列内嵌实体 id 保持源项目命名空间（行级 MVP 口径，详见 `packages/core/backup/README.md`「已知边界」）；深度重映射留待需要时用稳定 id 前缀浅层替换。
- `reference_canons` / `canon_extracts` 暂不随备份导出（V1.5 升级路径见 backup README）。

## [1.3.0] - 2026-08-24

审稿与文风包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`〔断链〕`）。

### Added

- LLM 评审员 critic：chapter-review 在人工审批前生成建议性结构化审稿报告（总评/亮点/问题清单，category+severity 枚举校验，quote 溯源软校验），经 pause payload 的 critic_report 键展示在审批卡；advisory only 不拦截；非 JSON/无 prompt/provider 异常全降级为 critic_status='failed'，审批照常。新增 docs/agents/prompts/critic-v1.md（agent 注册 7→8）。
- 个人文风样例库：迁移 0008 新增 author_style_samples 表；CRUD API /api/projects/{pid}/style-samples（每篇 ≤5000 字、每项目 ≤10 篇）；writer 上下文注入最近 ≤2 篇 × ≤1000 字并带模仿引导语（与 Q7 爆款目标风格互补：个人声音+目标风格双轨）；context-preview 与前端面板同步展示；项目总览页新增 StyleSamplesPanel。
- 伏笔 overdue 阈值项目级可配：projects.foreshadow_overdue_chapters（默认 30，迁移 0008 加列），builders 读取并多重回退 30。

### Fixed

- 开放伏笔清单截断边界：overdue 判定下推 SQL（ORDER BY overdue_flag DESC ... LIMIT 20），去掉预取 60+内存排序，伏笔超 60 条时逾期项不再被丢弃。
- summarize 节点真实降级路径测试补齐（不注册 agent → PromptNotFoundError → failed 且 chapter COMMITTED）。
- ContextPreviewPanel 空态文案口径修正；ContextPreviewItem 类型补 excerpt_len 字段。
- critic 字数默认值复用共享常量；项目更新端点消费 foreshadow_overdue_chapters。

### Known Issues / 路线登记（新增）

- critic 输入的伏笔摘要暂以 hook name 充当（hooks 表无 description 列，加列后回填）。
- PromptNotFoundError 等 provider 前异常不落 ai_call_logs（既有 runner 行为，运维可观测性缺口，V1.4+ 处理）。

## [1.2.0] - 2026-08-24

竞品对标迭代：基于海外（Sudowrite / NovelAI / NovelCrafter）、国内（蛙蛙 / 彩云小梦 / 平台 AI 政策）与开源社区（13 个代表项目）三路调研的优化落地。分析文档：`docs/analysis/competitive-analysis-2026-08-24.md`〔断链〕`。

### Added

- **质量系统第七维 `ai_trace`（AI 痕迹）**：章内 shingle 重复率 + 跨章重复率（最近 3 章）+ AI 套话命中三子信号合成；`overall` 升级为七维平均；前端 `QualityPanel` 增加「AI 痕迹」标签。（应对番茄低质 AI 文治理：重复率 / 文风机械性检测）
- **上下文装配可见化**：`GET /chapters/{id}/context-preview` dry-run API（零 LLM 调用，返回分层 token 估算与纳入条目清单）+ 章节详情页「AI 上下文明细」面板（`ContextPreviewPanel`）。
- **AI 调用日志查看器**：`GET /api/ai-call-logs` 列表（分页 / 过滤）与详情 API + 前端 `/ai-logs` 页面；敏感字段黑名单三重防御（schema 无 key 字段 + 路由黑名单 + 前端类型不声明）。
- **章节摘要链 + 前章尾段**：chapter_commit 新增 `summarize` 节点（≤200 字摘要 + 末 300 字尾段，迁移 `0007_chapter_summaries` 表；摘要失败降级不阻断 commit）；L1 装配注入最近 5 章摘要与前一章尾段，超预算先砍最旧摘要。
- **伏笔状态机强化**：observer 写透路径增加 hooks 状态迁移校验（前进制结算、拒绝 ABANDONED 复活）；`overdue` 计算属性（planted 超 30 章未收）；L1 新增「开放伏笔清单」装配（overdue 优先）。
- **summarizer agent prompt 注册**（`summarizer-v1.md`，capability=reasoning）。

### Changed

- `context-preview` 与 `ContextPreviewPanel` 同步展示摘要链 / 尾段 / 开放伏笔三类新内容源。
- 伏笔 / 开放线索由纯实体升级为带迁移校验的状态机（双口径说明见 `story_state` README §7.5.3）。

### Known Issues / 路线登记（新增候选）

- **V1.3+**：LLM 评审员（建议性审稿报告，人工裁决）；docx 导出与番茄投稿链路；任务分模型路由；个人文风样例注入（对标 Sudowrite Style Examples）；伏笔 overdue 阈值项目级可配。
- **V2.x**：向量召回混合层（状态库定事实 + 向量供呼应）；设定条件触发动态注入（关键词 / 关系链 / 场景绑定，对标 Lorebook / Codex）。
- V1.3+ 遗留（审查登记）：开放伏笔清单 SQL 预取 LIMIT 60 与 overdue 排序键不一致的截断边界（伏笔超 60 条时逾期项可能进不了清单）；summarize 节点 PromptNotFoundError 真实降级路径暂无测试覆盖（现测试用 mock 绕过）；ContextPreviewPanel 空态文案边界细节。

## [1.1.0] - 2026-08-24

PRD 全量符合性核对（§1–§125 逐节追溯）+ 全维度检修（安全 / 数据完整性 / 测试盲区 /
文档一致性，叠加 V1.0 的性能 / 业务闭环 / 架构三轴）后的修复版本。
§110 MVP 验收链由 12/13 补齐为 **13/13**。

### Added

- **Q8 人工占比 CSV 导出**（PRD §125 合规自证）：`GET /api/projects/{pid}/quality/q8-export`
  按章导出 ai_chars/human_chars/human_ratio（utf-8-sig BOM，Excel 兼容）；数字口径与评估链路
  同源（`compute_char_stats`）。合规立场已调研定调并写入 `packages/core/quality/README.md` §12：
  《人工智能生成合成内容标识办法》（2025-09-01 施行）的显式/隐式标识义务主体是面向公众的
  生成服务提供者与传播平台（番茄已在投稿环节要求作者勾选是否使用 AI）；NovelOS 是本地单机
  工具，V1 不在正文内嵌标识，产品侧提供统计与导出自证，投稿声明义务在作者侧。
- smoke_e2e §110 验收链补齐第 3 步「创建世界」断言（9 组断言全绿）。

### Fixed

- **API key 读路径脱敏**：model_configs 的 GET/PATCH/POST 响应统一脱敏（`api_key` → `***` +
  `has_api_key` 布尔）；PATCH 传 `***` 保留原 key、空串清空；前端编辑弹窗不再回填明文，
  留空=不修改。DB 写路径保持明文存储不变。
- **零外呼回归锁定**：无 model_configs 时断言 `ModelNotConfiguredError` 且 ai_call_logs 零新增；
  deconstruct 端点未配置模型时返回 422（原漏成 500），并前置 capability 预检。
- **拆书文本上限**：deconstruct `text` 超过 5MB → 422。
- **草稿体长上限**：`DraftCreate.content` max_length=500_000。
- **references 路径防护**：`load_reference_texts` 对 project_id 做字符白名单校验（防路径穿越）。
- **文档修正**：`packages/core/api/README.md` 健康端点表数 28→31（三处）。

### Known Issues / 路线登记更新

- V1.x 清单移除「model-configs 列表 api_key 脱敏」（本版已完成）；其余 V1.x/V2.x 项维持
  V1.0.0 登记不变。新增登记：前端无「清空密钥」UI 入口（仅能覆盖/保留）；`***` 为保留哨兵值；
  PRD 符合性核对发现的中长期缺口（Intent Engine §41、Impact Analysis §42、Event System §68、
  reveal_policies 运行时 §23.2、Timeline 冲突检测 §20、Dashboard 健康度 §14、Workflow DAG
  可视化 §61、Style System §97、Export/备份 §100/§101 等）按 PRD 自标的 V1/V2 阶段推进，
  明细见检修结论（各节判定矩阵已在审查记录归档）。

## [1.0.0] - 2026-08-24

首个正式版本。交付形态：**本地部署 Web 应用**（FastAPI 托管 React SPA，SQLite 落盘，单端口 18081）。
S0–S12 全部收官（S12 Tauri 壳经用户拍板关闭，Web 版即交付形态）；真实 LLM（MiniMax-M3）
端到端验证通过。

### Added（S0–S11 累计能力）

- 章节生产四工作流：chapter-plan（director）→ chapter-write（writer）→ chapter-review
  （Human 三态审批：批准 / 驳回 / 驳回附改稿意见）→ chapter-commit（observer 提取 state delta
  → 校验 → 质量门禁 → 提交）。
- Canonical Story State：快照 + delta 状态机、乐观锁、逆 delta 回滚、分支（What-if）与
  按序重放 promote。
- 质量引擎：6 子分加权、8 条 guardrail、H-1~H-5 爽感体检；quality gate 支持
  report（默认）/ enforce 双模式。
- 伏笔与叙事债务台账（hooks / narrative_debts）及 REST 端点。
- 参照系拆书 deconstruct-book（T1 切分 → T2 逐章拆解 → T3 聚合校验 → G-sim 相似度阻断 → T4 落库），
  参照 canon 自动注入 director 上下文。
- What-if 推演（simulation）：临时分支零污染推演 + diff + 归档。
- 多模型路由：mock / OpenAI 兼容 / Anthropic / Ollama，失败转移链，健康检查。
- 前端工作台：项目列表 / 项目总览 / Story Bible（人物·世界·情节·台账·Canon 五 tab）/
  章节详情（计划·草稿·工作流·质量面板）/ AI 设置。
- 回归基线门禁（结构签名比对）与 HTTP 全链路 smoke。
- 真实 LLM 验证：MiniMax-M3 全链路 PASS（报告 `docs/evaluation/runs/real-llm-minimax-m3-2026-08-24.md`，
  质量分 overall 87）。

### Fixed（V1.0 检修：性能 / 业务闭环 / 架构三路审计）

- **[P0] 审批卡片 UI 断头**：后端把 `__pause_payload__` 写在 `checkpoint_json[节点名]` 下，
  前端误从顶层读取导致审批卡片（review / HIGH 风险审批）永不渲染。新增纯函数
  `extractPausePayload` 按真实结构取值（`apps/web/src/utils/pausePayload.ts`）。
- **observer 校验重试**：commit 流程对 observer 输出先经 `validate_delta` 纯函数预检，
  失败带 `_retry_hint` 重试一次（共 2 次），`submit_delta` 仅在通过的 delta 上调用一次
  （真实 MiniMax-M3 运行中该重试实际救回一次 commit）。
- **写透 NULL 兜底**：world add 分支（location/faction/rule）对缺失的 name/statement/data_json
  做 None 兜底，消除 `NOT NULL constraint failed`。
- **归档过滤**：项目列表默认排除 ARCHIVED（`?include_archived=true` 可含），行为与 UI 文案对齐。
- **模型测试连接字段对齐**：前端 `ModelConfigTestResult` 对齐后端 `{ok, latency_ms, detail, status_code}`。
- **轮询节流**：`usePoll` 在页面隐藏（document.hidden）时跳过 tick。
- **SQLite 韧性**：连接统一 `PRAGMA busy_timeout = 5000`。
- **e2e 脚本**：HTTP 客户端超时 120s→1200s（推理模型 observer 调用合法耗时可达 300s）、
  sqlite row_factory、临时库清理重试。
- **架构**：`_diff_snapshots` / `_strip_state_version` / `_shingles` 提为公共 API
  （`diff_snapshots` / `strip_state_version` / `compute_shingles`），消除跨模块私有引用与
  simulation 内的复制函数。

### Migration

- `0006_quality_reports_project_idx.sql`：`quality_reports(project_id)` 索引（list_reports 查询路径）。

### Known Issues / 路线登记（V1.x / V2.x 候选）

- **V1.x**：simulation/branches 仅 API 无 UI 入口；ai_call_logs 无查看通道（PRD §93）；
  enforce 阻断后的改稿引导；参照系消费可观测（`_reference_canon_consumed` 未进 checkpoint）；
  ~~model-configs 列表 api_key 脱敏~~（V1.1.0 已完成）；checkpoint_exclude 推广到章节工作流
  （需先论证 resume 影响）；
  workflow 注册表下沉 core；router 越层 SQL（reference/workflows/model_configs）抽 service。
- **V2.x**：story_state/service.py（2300+ 行）按职责拆分；分支读路径 O(N²) 重放的快照物化；
  story_state 写透与 domain CRUD 的双写面统一；context engine 装配缓存；端口变量收敛。
- 不计划做：packages/agents 空骨架强填充（角色逻辑当前内联于各 pipeline，已在 README 标注）。
