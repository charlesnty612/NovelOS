# 测试瘦身审查（2026-10-23 晚 · 只读审查，未删任何用例）

> **缘起**：用户直接指令「现在项目的测试太重了，你先来 review 一下测试，看看哪些是没必要的」。
> **本文的边界**：这是**审查报告 + 可裁决清单**，**没有动任何测试文件**。删除动作待用户批准后另开执行批。
> **量测口径**：本文所有数字均为**本会话实测**（HEAD `50a556e`，工作树干净，树指纹 767 文件 / `15b014e0…`）。
> 审查分工见共享任务板 task-4（后端）/ task-5（前端）/ task-6（对抗设卡），本文为 task-7 汇总，并**逐条亲验**了采信的结论。

---

## 一、结论（先看这五句）

1. **「废话用例」几乎没有**：361 个 Python 测试文件里，无断言 **1** 例、全恒真断言 **0** 例、只断 mock **5** 例（其中多数经人工回读是误报）、函数体逐字重复 **1 组**。**没有一批「删了就瘦」的死重**。
2. **真正可删的只有 7 条**（全部在 Python 侧：5 条 api + 2 条恒真；**前端可删 0 条**、可合并约 25 条），**并行墙钟只省 < 2 秒**（全量 172.78s）。**按用例数删测试，救不了「重」**。
3. **「重」的真因是三个，全部有实测**：① **每例地板成本**（`apply_migrations` 216ms + `create_app().openapi()` 422ms）；② **前端单文件** `ChapterDetailPage.test.tsx` 独占前端墙钟 **74%**，其中 **4 条用例真等 2 秒轮询 = 8.2s**；③ **发版前突变闸门 476 条串行 ≈ 53 分钟**（这才是项目里唯一「真重」的闸门）。
4. **减重应走「夹具/写法改造」，不是删用例**：模板库复制实测 **216ms → 0.7ms**（712 KB 文件拷贝），上限省 ≈ 145s CPU ≈ **8s 并行墙钟（5%）**；前端拆 2 个超 1500 行测试文件 + 抽 677 行 `beforeEach` 样板，可把 12.9s 压到 5s 以内。
5. **删测试不可逆** ⇒ 本文给出「承重墙清单」（25 条，删了有机器可验证的损失）+ 淘汰 SOP，**任何删除都必须过 `run_spec.py --all --precheck`**（101 个测试文件是突变落点，删了 CI 必红）。

---

## 二、量测数据（可复现）

```bash
# 全量（本会话 21:04 实测）
.venv/Scripts/python.exe -m pytest -n 20 -q --basetemp=<仓外唯一目录>
#   ⇒ 4359 passed, 1 skipped, 1 xfailed in 172.78s

# 收集面
.venv/Scripts/python.exe -m pytest --collect-only -q
#   ⇒ 4361 个用例 / 360 个被收集文件（另有 1 个 test_*.py 收集为 0）

# 成本分布
.venv/Scripts/python.exe -m pytest -n 20 -q --durations=0 > tmp/audit/durations_all.txt
#   ⇒ 3191 条 ≥5ms 记录、9830 条 <5ms 隐藏；call 3022.1s / setup 174.9s / teardown 0.6s（20 worker 的 CPU 合计）

# 地板成本（本会话实测，5 次取中位）
apply_migrations            216 ms      create_app()+openapi()  422 ms
复制一份已迁移库（712,704 B）  0.7 ms
```

**按目录的 CPU 分布（含 setup，总计 3198s）**

| 目录 | CPU | 占比 | ≥5ms 条目数 |
|---|---|---|---|
| `tests/unit` | 1263.9s | 39.5% | 1616 |
| `tests/api` | 1140.5s | 35.7% | 602 |
| `tests/workflow` | 517.5s | 16.2% | 227 |
| `tests/integration` | 262.8s | 8.2% | 122 |
| `tests/evals` | 12.8s | 0.4% | 10 |

**单测试成本分布**：0.2~1.0s 占 **32.1%**、1.0~3.0s 占 **48.3%**、>3s 占 **19.0%**；**<0.05s 的 1 万条 entry 合计只占 0.9%**。
⇒ **耗时是「平摊」的，没有热点可切**：最贵的单文件 `tests/workflow/test_chapter_pipelines.py` 68.5s CPU = 全量 2.3%（并行墙钟约 3.4s）；Top 52 条用例累计只占 call 时间的 **9.3%**。

**分区墙钟（本会话实测）**：`tests/unit` **75.2s**、`tests/api` **62.9s**、全量 **172.8s** ⇒ **不存在「便宜的本地快档」**（只跑 unit 也要 75 秒）。

**前端 vitest（本会话实测）**：`62 文件 / 853 例 / Duration 12.92s`；其中
`src/pages/ChapterDetailPage.test.tsx` **96 例 / 9.58s（tests 7.92s）**、`ProjectInitPanel.test.tsx` 39 例 / 1.59s、`SimulationPanel.test.tsx` 32 例 / 4.80s。

---

## 三、建议删（7 条，全部经 Lead 亲验）

| # | 文件:行号 | 用例 | 判据（我亲自核过的） | 风险 | 省 |
|---|---|---|---|---|---|
| D-1 | `tests/api/test_openapi_contract.py:277` | `test_openapi_json_file_matches_live_schema` | 只比 `info.version` + `paths`/`components.schemas` 的**名集合**；而 `test_codegen_artifacts.py::test_repo_openapi_artifact_matches_regeneration` 是**全文逐字节**比 `render(build_schema())`，且实测 `scripts/export_openapi.py::build_schema` 就是 `create_app(settings).openapi()` ⇒ **集合相等被严格蕴含** | 低 | 0.67s |
| D-2 | 同文件 `:319` | `test_openapi_json_check_rejects_stale_copy` | 是 D-1 的**自变异自检**（`:328` 直接调用 D-1）⇒ D-1 删除后失去被检对象；「陈旧副本必须红」的判别力由 `test_codegen_artifacts.py` 的漂移用例 + `l21` 突变 `CG-*` 承担 | 低 | 3.12s |
| D-3 | `tests/api/test_frontend_types_contract.py:209` | `test_debt_never_grows` | 断言 `len(HANDWRITTEN_DEBT) <= DEBT_CAP`，而 `DEBT_CAP` 是**同文件常量** ⇒ 自指；同文件 `:195` 用**独立历史值** `_DEBT_CAP_HISTORICAL` 且断言更严（双向）⇒ 完全覆盖 | 低 | ~5ms |
| D-4 | `tests/api/test_state_list_response_contract.py:1041` | `test_e2e_db_cannot_reach_a_real_provider` | 自己 `apply_migrations` 建**全新空库**再断三表 `COUNT=0`（docstring 自认「module 夹具库已被回收，这里重建一个同形态空库」）⇒ 量的不是 e2e 真用的库。**权威版**在 `tests/api/test_fast_mode_review_gate.py:363`（对真实夹具库断言） | 中：失去「迁移若播种 model_configs」的低概率守卫 | ~0.3s |
| D-5 | `tests/api/test_workflow_run_response_contract.py:927` | 同名同上 | 同款实现（`:938-942`） | 同上 | ~0.3s |
| D-6 | `tests/unit/quality/test_normalize.py:237` | `test_both_functions_are_deterministic` | 逐字是 `assert f(src) == f(src)`；`normalize_prose` 是纯文本函数（不读库/环境/时间）⇒ **唯一能让它红的改动 = 让纯函数变随机**，无需求要求它随机 | 低（仅丢「别改成随机」的预警） | <5ms |
| D-7 | `tests/unit/test_content_sync_genre.py:137` | `test_determinism` | 逐字是 `assert derive_genre_slug(x, p) == derive_genre_slug(x, p)`，同一个病 | 低 | <5ms |

**对 D-6/D-7 我更倾向「改造」而非删**：把它们改成断言**具体输出值**（例如规整后的文本 / slug 字面值），判别力从 0 变成「绑住真实行为」，成本相同。

**建议合并（不删，省维护；执行时须逐字核对）**

| 组 | 位置 | 内容 | 前置核对 |
|---|---|---|---|
| M-1 | `test_config_list_response_contract.py:493` | 6 个参数 case 中 3 个（ModelConfigOut/ModelProfileOut/CapabilityBindingOut）与 `test_model_config_response_contract.py:333` 重复，两份硬编码键集据称逐字相同 | **执行时必须逐字 diff 两份键集**，不同则作废 |
| M-2 | `test_ledger_plot_response_contract.py` | dev-a 报「字段集断言与 list 版重复」，但**我按它给的行号抽查未命中**（`:374` 实为 `extra` 宽容测试）⇒ **行号存疑，先核再动** | 需重新定位 |
| M-3 | `test_chapter_review_word_band_override.py:201,216` + `test_writer_input_word_band_override.py:148,162` | 4 条同名钉同一兜底语义 ⇒ 4→3（合到共享解析器侧） | `test_wordcount.py` 现无该覆盖，合并后须补 1 条 |
| M-4 | `test_genre_module_inventory.py:430` + `test_quality_module_inventory.py:347` | 函数体 AST 逐字相同 ⇒ 抽共享 helper（用例数不变） | 两包须仍被独立断言 |
| M-5（前端） | `ChapterDetailPage.test.tsx:606/714` 等约 25 条 | 同 describe / 同 fixture 同目标的用例并成 `it.each`；页面级与组件级同款断言**不合并**（断的是不同层） | 见 §五 |

---

## 四、不可删（承重墙 25 条 + 2 条 Lead 补充）

> 判据不是「看起来有用」，而是**删了有机器可验证的损失**。完整清单见 task-6 交付，此处只列机制与最硬的几条。

**A. 突变落点（删了 CI 必红，12 条）**：`run_spec.py` 的 `precheck()` 对每条规格检查「定向测试文件是否存在」⇒ 退出码 1，而 `ci.yml` 跑 `--precheck`。**101 个测试文件是 476 条规格的落点**，最集中的：`test_state_list_response_contract.py` 18、`test_story_state_write_response_contract.py` 12、`test_world_response_contract.py` 12、`test_character_excerpt_fidelity.py` 12、`test_codegen_artifacts.py` 7、`test_no_core_to_workflows_dep.py` 6。

**B. 判据本身（4 条）**：`test_mutation_harness_judgment.py:187 test_invalid_is_never_reported_as_caught` 是**防恒真总闸**——删了，执行器可把「什么都没跑」判成 CAUGHT ⇒ 476 条规格的结论全部失去可信度。

**C. 凭据归属闸门（5 条）**：`test_check_content_repo_guard.py:1383/:1407/:1437/:1464` 守「零交集 ⇒ 不可判 ⇒ 拒绝删除」——删了则 `--db` 指错库时**一次 prune 清空整个凭据库、退出码仍 0**（2026-10-23 Lead 自踩事故）。

**D. 分层看守（6 条）**：`test_no_core_to_workflows_dep.py` 的静态 + 动态 + 白名单三组，以及**反向用例**（「合法动态 import 不得误报」，`:1070/:1078/:1096/:1291/:1371/:1581`）——后者最容易被「优化掉」，但删了守卫会退化成噪声源。其中 `:1119 test_every_guarded_python_file_is_actually_parsed` 是**量具自检**（守卫不许静默缩小执行域）。

**⚠️ Lead 推翻执行成员的两条判断（留痕）**
1. dev-a 判 `tests/api/test_openapi_contract.py:104 test_generated_ts_matches_openapi` **可删**（理由：「由 openapi 全文闸门 + types 全文闸门联合蕴含」）⇒ **我判不可删**：全文字节闸门两端分别是「仓库产物」与「当前生成器输出」，**若有人改生成器并重新 regen，两侧同变、照样绿**；只有这条集合级断言把「生成的名字集合」与「活 schema 的名字集合」对起来。⇒ 保留，且连带保留它的 3 条自检（`:145/:182/:229`）。
2. dev-a 提的 `test_ledger_plot_response_contract.py` 重复项，**行号核查未命中**（见 M-2）⇒ 降级为「待定位」。

---

## 五、结构问题：两个前端测试文件该拆，不是该删

| 文件 | 实测行数 | 用例 | 单文件耗时 | 问题 |
|---|---|---|---|---|
| `apps/web/src/pages/ChapterDetailPage.test.tsx` | **3776**（AGENTS.md 记 3418，**已过期**） | 96（88 `it` + 3 `it.each`） | **9.58s**（占前端全量 74%） | 19 个 describe；18 份 `beforeEach` 共 **677 行 = 文件 18%**，其中 **4 份逐字相同（各 45 行）** |
| `apps/web/src/components/ProjectInitPanel.test.tsx` | **2381**（AGENTS.md 记 2380） | 39 | 1.59s | 1 个 describe 装 39 例；33 条用例各自重复「等加载→等 tab→等元素」三段 `waitFor` 样板 |

**处置建议**：按 describe 语义拆 4 份（plan / runs / pipeline / gates），全部 < 1500 行；抽 `setupMocks()` 可省 ≈500 行；把 2 秒轮询改可控时钟（`vi.useFakeTimers`）⇒ 前端 12.9s → 5s 以内。**拆前已确认**：该文件的 `vi.mock` 只指向 `../api/endpoints` / `../api/projectSettings`，**不涉及**「被 patch 的名字迁走」风险（AGENTS §二 硬规则 1 的拆门面禁忌）。

---

## 六、真正能减重的三条路线（按性价比排序）

| 路线 | 实测依据 | 预期收益 | 风险 / 代价 |
|---|---|---|---|
| **A. 迁移库模板化** | `apply_migrations` **216ms** vs 复制预迁移库 **0.7ms**；`setup` 阶段合计 **174.9s / 674 条**（均 259ms） | 上限 ≈ **145s CPU ≈ 8s 并行墙钟（5%）**；若把 api/workflow 的 app 级迁移也改模板，收益再上一档 | 模板须按 `database/migrations` 内容哈希失效（否则「迁移改了但测试仍用旧库」= 新型假绿）；WAL 库拷贝要用 sqlite backup API 而非 `copyfile` |
| **B. 前端拆文件 + 抽样板** | 单文件 9.58s / 4 条轮询吃 8.2s / 677 行样板 | 前端 **12.9s → 5s 以内** | 纯测试侧重构，不触业务代码；需过 `tsc -b` + 全量 vitest |
| **C. 突变闸门按需化** | 档③ 全量 **476 条串行 ≈ 53 分钟**（本仓发版前唯一「真重」的闸门）；档① 预检 **0.2s**；档② 增量 1~3 分钟 | 日常从 53 分钟 → 秒级/分钟级 | AGENTS §三 第 7 条**已授权**；发版前是否仍强制全量，须用户拍板 |

**收益参照**：删掉 §三 那 7 条 = **< 2 秒**；路线 A+B+C 全部做完 ≈ **并行墙钟 172.8s → ~160s（A）+ 前端 12.9s → 5s（B）+ 发版闸门 53min → 分钟级（C）**。
⇒ **「测试太重」的正确解法是 C（闸门）> B（前端）> A（夹具）>>> 删用例。**

---

## 七、先验判断被推翻（留痕）

1. **AGENTS.md §二 硬规则 1 的行数过期**：记 `ChapterDetailPage.test.tsx` **3418** 行，**实测 3776**（差 358 行）；记 `ProjectInitPanel.test.tsx` 2380，实测 **2381**。⇒ 该条「事后新增违规」的现状描述需订正（仍是违规，只是数字不对）。
2. **Lead 初判「一定有一大批废话用例」被自己的静态体检推翻**：无断言 1 / 全恒真 0 / 只断 mock 5（其中多条是误报）⇒ 结论改为「重不在废话，在结构与地板成本」。
3. **执行成员 vs Lead 的两处分歧**：见 §四末（`test_generated_ts_matches_openapi` 保留；`test_ledger_plot` 重复项行号存疑）。
4. **`test_prompt_docs_no_project_specific.py:65/:86` 的「假绿」性质**（审查员发现，我复验逻辑链成立）：这些用例守的是 `writer-v1.md` / `director-v1.md` / `scene_planner-v1.md`，而 `get_active_prompt` 取 **ACTIVE 行中版本号最大者**（实测 `prompts.py:160-169` 按 `CAST(substr(version,2) AS INTEGER) DESC` 排序）⇒ 运行时用的是 **writer-v5 / director-v2 / scene_planner-v2**，v1 永不是运行时提示词；且文件缺失时 `pytest.skip` ⇒ **删文件反而全绿**。⇒ **不删，改为**：守 ACTIVE 版本，或把 `skip` 改 `fail`（同族：AGENTS 坑区「skip 理由不是元数据，它是对覆盖率的断言」）。
5. **我给 contract 收敛定的「≤3,000 行」目标是错的**（执行成员复算 + Lead 复核，2026-10-23 21:5x 订正）：原口径「24 文件 / 11,639 行」是**把 `tests/**` 下所有含 `contract` 的文件都算进模板族**（实际 25 文件 / 11,850 行，其中 8~9 个不是模板族：`director_planner` / `openapi` / `frontend_types` / `volume_membership` + 4 个 `tests/unit`）；真正的模板族 = **16 个 `*_response_contract*` / 9,748 行**（Lead 脚本复算逐位吻合）。⇒ 3,000 行要求 11 个文件压掉 78%，**与「断言/证据 docstring 一条不许降」直接冲突**。**订正目标 = 16 文件 ≤6,500 行（-33%）**，写域扩到全 16 个；抽不出来的保留原样报备。

---

## 八、本轮没做的事 / 未验证

- **没有删任何用例、没改任何测试文件**（删除不可逆，等用户裁决）。
- **没跑 CI**（本机仅 Python 3.12.15，CI 的 3.11 腿本地无法验证；CI 另有 pre-existing 失败：依赖同级 `NovelOS-Artifacts`）。
- **「删掉后是否变红」未实测**（只读铁律）⇒ §三的风险列都是**静态判据**（读源码确认有无第二守卫），执行批必须补「删后全量 + `--precheck`」实测。
- **前端 6 个大文件未人工通读**：`AiSettingsPage.test.tsx`(1165) / `SimulationPanel.test.tsx`(660，**4.80s 耗时排第 2，值得下一轮单独查**) / `chapterState.test.ts`(831) / `ledgerState.test.ts`(311) / `ApprovalCard.test.tsx`(599) / `usePoll.test.ts`(427)。
- **未做「近重复」检测**（断言同义但代码略异的复制粘贴）⇒ 可合并项规模可能被低估。
- **`it.each` 展开的 11 条额外用例未逐条读**（按「参数化是正确写法」默认保留）。

---

## 九、建议的下一步（需用户挑一个）

| 选项 | 动作 | 代价 |
|---|---|---|
| **① 只做 §三 的 7 条** | 删 D-1~D-7 + 把 D-6/D-7 改写成断言真实值 | 半小时内；省 < 2s，但清了自指/恒真 |
| **② 做 B（前端拆文件）** | 拆 4 份 + 抽 `setupMocks` + 轮询改可控时钟 | 半天；前端 12.9s → ~5s，顺带修掉两个超 1500 行的违规 |
| **③ 做 A（模板库）** | 迁移库模板化（按 migrations 哈希失效） | 半天~一天；并行墙钟 -5%，**有新型假绿风险，需配「模板失效」看守测试** |
| **④ 做 C（突变闸门按需）** | 日常只跑 `--changed`，发版前全量 | 十分钟；日常 53min → 分钟级 |
| **⑤ 都不动，只把这些结论记进 AGENTS/台账** | 我改文档，代码零变更 | 最低风险 |

> Lead 推荐 **② + ④**（真省时间、不碰判别力），**①** 可顺带做但要按 §三 的「改断言优先于删」处理 D-6/D-7。

---

## 十、用户追问「测试比项目本身还庞大」——体积实测（2026-10-23 21:20）

**结论：感觉是对的，压测口径 1.39×；异常点不在总量，在「模板化重复 + helper 样板」两处。**

| 口径 | 源 | 测试 | 比值 |
|---|---|---|---|
| Python（`packages/`+`scripts/` 94,638 行） | 265 文件 | **468 文件 / 149,989 行** | **1.58×** |
| ├ 其中突变规格 | — | 86 文件 / 8,548 行 | （+9%） |
| 前端（`apps/web/src` 28,722 行） | 85 文件 | 62 文件 / 20,879 行 | **0.73×**（健康） |
| **合计** | 123,360 行 | **170,868 行** | **1.39×** |

**体积分布（测试 149,989 行）**

| 分区 | 行数 | 文件 |
|---|---|---|
| `tests/unit` | 90,602 | 242 |
| `tests/api` | 28,628 | 70 |
| `tests/workflow` | 21,475 | 42 |
| `tests/integration` | 7,726 | 23 |
| `tests/evals` | 1,558 | 5 |

**三个可压缩块（都不减用例、不减判别力）**

1. **`*_response_contract*` 模板族**：**16 文件 / 9,748 行**（⚠️ 本文初版写「24 文件 / 11,639 行」是**分母口径错误**——那 25 个是 `tests/**` 下所有含 `contract` 的文件，见 §七 第 5 条订正），绝大多数是「建 app → 灌富夹具 → 四条模板断言（schema 已声明 / 实际键集 == 硬编码 / model_fields == 硬编码 / extra 不裁）」的复制。⇒ 抽共享 runner + 每模型一张硬编码基线表，**目标 ≤6,500 行（-33%）**。
2. **测试 helper 样板**：非 `test_` 函数体共 **50,220 行 = 测试体积的 33.5%**（最大单个 496 行 `_seed_cross_reference_project`；`_migrated` / `_app` / `_seed_*` 在各文件重复定义）。⇒ 同域收敛可省 **15,000~20,000 行**。
3. **6 个巨型单文件共 10,922 行**（`test_project_init` 2,553 / `test_migrations` 1,899 / `test_chapter_pipelines` 1,836 / `test_no_core_to_workflows_dep` 1,590 / `test_scripts_write_guard` 1,554 / `test_check_content_repo_guard` 1,490）⇒ 属"该拆文件"，与前端同款处置。

**为什么会长成这样**：本仓每修一个缺陷沉淀**三件东西**——回归测试 + 突变规格 + AGENTS 坑区条目。源码可复用，坑不可复用 ⇒ 测试体积增速天然 ≈ 3× 缺陷修复速率。

---

## 十一、突变全量（476 条 / 53 分钟）的价值评估与裁决

**成本**：串行 **≈53 分钟**（并发档已废：会造假 CAUGHT、退出码恒 3）。

**规格质量（Lead 只读扫描，339/476 条可解析样本）**：等价突变 **0 条**、锚点只落在注释行 **0 条**、同一「源文件+锚点」被 2+ 规格复用的只有 **14 组**（且多是同一行的两种变异体，属有意设计）。⇒ **规格本身不是垃圾，删它没有意义**。

**收益证据（留痕，非本轮实测）**：批次二十六抓到 2 条 `[SURVIVED]` 并修复；批次二十八抓到 `SO-DIR-ARRAY`（一条**从未被跑到**的存活突变 ⇒ 暴露筛选口径缺陷）；批次三十 `M2` 存活 ⇒ 暴露 `normalize_newlines` 是死代码，改实现后判别力真实存在。⇒ 每次抓到都是「测试看着在守、其实守不住」。

**全量 ≠ 增量的唯一差别**：老规格会**腐化**——代码重构后锚点仍唯一、替换仍不同，但测试已不再依赖那一行 ⇒ 规格退化成永远 CAUGHT 的装饰品。**`--precheck` 抓不到这种腐化**（它只查锚点唯一 + 替换真变了）。

**裁决（Lead 自主拍板）**：
1. **不删任何规格**（不可逆，且它们是「哪些机制被机械验证过」的审计记录）。
2. **全量从「每批阻塞闸门」降级为「发版前一次性、非阻塞、可后台」**；日常走 `--changed`（**1~3 分钟**）⇒ 日常成本 53min → 分钟级。
3. 若将来要压规格体积：只清**同一锚点的重复变异体**与**已被更强守卫接管的**（本扫描候选 14 组，需逐组裁决），不做整类清理。

---

## 十二、执行结果（2026-10-23 深夜 · 批次三十三之一，已提交 `5b59d2e`）

| 项 | 结果 |
|---|---|
| contract 模板族 | 建共享 runner（313 行 / 6 个断言单点，**不提供**自动派生期望的入口）+ 已转换 **5/16**（9,777 → 9,635，用例数逐个不变） |
| runner 看守 | **新增 16 例**（对抗审查 F-1：它此前零测试却承载 42 条规格的判别力） |
| reveal 盲格 | 新增 11 键硬编码基线（抓「两侧同时收窄」与「可选字段回填 null」） |
| 全量 pytest | **4375 / 1 skipped / 1 xfailed**（= 4359 + 16，逐位对得上） |
| `pytest tests/api` | 723（原 707 不变） |
| 突变 | `--all --precheck` 477 条 / 0 问题；**指向被重构文件的 42 条串行实跑 0 存活**；残留 0 |
| 判别力抽检 | 删 1 个期望键 ⇒ 12 红（world 4 / state_list 3 / story 5）⇒ 还原后 723 绿 |
| 前端 | `ProjectInitPanel.test.tsx` 2381 行 / 39 例 → 3 份（613/1211/565）；vitest **64 文件 / 853 例 / 12.70s** |
| `tests/**` 总量 | 158,537 → **159,200（+663）**：永久资产 +557（runner + 看守）、播种原语 +184、16 文件侧净 -96 |

**未完成**：contract 剩余 11/16（task-12）；`ChapterDetailPage.test.tsx` 3776 行拆分（task-8 第 2 步，进行中）。
**教训（写给自己）**：体积指标一旦定成「某个文件子集的行数」，就会被「搬出行数」骗过去；本轮靠「`tests/**` 总量」这条反制才发现 +663。今后凡定体积目标，必须同时定**总量口径**与**永久资产口径**。

---

## 十三、执行结果之二（2026-10-24 凌晨 · 批次三十三之二，已提交 `e314833` 并推远端）

| 项 | 结果 |
|---|---|
| contract 族 | **13/16 已收敛**：16 文件 **9,777 → 9,429**（本轮 8 完整 + 1 部分 + 2 未动；目标第三次订正为 **≤9,400**） |
| runner 能力 | 新增 `create_test_app(log_level=)` / `client_ctx(base_url=)` / `assert_keys_match(hint=)` / `app_table_columns`，**每个都补看守** ⇒ 16 → **20 例** |
| 建库样板 | `tests/_db_fixtures.py::migrated_db`（每例新库、不共享），**14 文件 / 18 处**；批量探针 18/18 PASS；跳过 141 处（带显式迁移目录等） |
| 全量 pytest | **4379 passed / 1 skipped / 1 xfailed**（= 4375 + 4 条新看守） |
| 突变 | `--all --precheck` 477/0；**指向全部被改 contract 文件的 12 份规格串行实跑 0 存活 / 0 中止 / 0 无效**；残留 0 |
| 判别力抽检 | 删 1 键 ⇒ arc 1 红 / model_config 2 红 / config_list 3 红（6 failed）⇒ 还原后 727 绿 |
| 行数账（如实） | `tests/**` 159,200 → **159,149**（-51）；其中建库样板第一刀 +66、第二刀 -23（helper 成本已付完） |

**未完成项与可复用教训**已落盘到台账 §六（`remaining-debt-2026-10-23.md`），含「拆分测试文件是夹具契约搬运而非文本搬运」这条本轮实证的教训。
