# 业务链审计（部分）— 2026-11-04

> **范围**：`plan → write → review → commit` 主链 + `project_init / deconstruct_book / outline_batch / simulation` 旁支链 + 支撑底座。
> **性质**：只读审计。**零真实 LLM 调用、零内容仓接触**（基线端到端走 `scripts/smoke_e2e.py` 的临时库 + mock provider）。
> **口径**：A 档 = Lead 亲验（读过源码/实测过）；B 档 = 子代理测绘产出、Lead **未亲验**（静态阅读推断）；C 档 = 前批已登记，**不重复报**。
> **未完成**：原定 4 阶段工作流（基线 → 9 段测绘 → 8 维度横切 → 对抗验证 → 汇总）在本报告前被用户叫停 ⇒
> **「找问题」与「对抗验证」两个阶段未跑**；第 9 条链（`story_state` 底座 + `versioning` + 装配缓存）**未测绘**。

---

## 一、结论摘要

1. **业务链后端基线健康**：本地全量 **4616 passed / 0 failed / 1 skipped / 2 xfailed**（collected 4619，254.07s，`-n 20` 仓外 basetemp），跑测前后树指纹逐位一致（800 文件 / `13c9cac3…`，与 CHANGELOG 基线逐位吻合）；前端 **899 passed / 66 文件** + tsc 0 错 + build 通过；端到端 smoke PASS（四步链路 + observer 在场 + SYNC OK）。
2. **但 CI 是红的，而且红了 10 天没人看**：run `40208499569`（head `d79a7bb`，2026-10-25）`backend (pytest + ruff)` job 失败，其余三个 job 全绿。**根因已定位**（A-1）——是**一条 Windows-only 语义的守卫测试**跑在 Linux CI 上，不是产品缺陷，但它是"闸门存在 ≠ 闸门在门框上"的活实例。
3. **主链上有 3 处真缺陷已被亲验**（A-2/A-3/A-4）：前端"生成计划"的按次模型覆盖**静默失效**；前端 `Commit` 类型是**幽灵形状**（与后端返回对不上）；`commit_delta` 与章节状态置 `COMMITTED` **不在同一事务**。
4. **文档基线有回填缺口**（A-5/A-6）：`AGENTS.md §六` 的 OpenAPI 基线落后实际 13 paths / 11 schemas；CHANGELOG 把突变**文件数 86** 当成了**条目数**（实为 480）。
5. **还有约 60 条候选问题未验证**（B 档）：测绘者逐条读过源码并标了 file:line，但**没经过"对抗验证"那一关**，也**没有 Lead 亲验** ⇒ 不可直接当结论用。

---

## 二、基线实测（A 档，Lead 亲跑）

| 腿 | 结果 |
|---|---|
| 后端全量 pytest | **4616 passed / 0 failed / 1 skipped / 2 xfailed**（collected 4619），254.07s（`-n 20`，basetemp 落仓外 `C:/Users/aistation/AppData/Local/Temp/novelos_audit_e1`） |
| 树指纹 | 跑测前后**逐位一致** = **800 文件 / `13c9cac3df060f7b543c00796d2f56d4d7bf15a15804f8356690060c88c8f31e`**（与 CHANGELOG 2026-10-25 验收行逐位相同）⇒ 结论对当前树成立 |
| ruff | `All checks passed!`（exit 0） |
| codegen 漂移 | `export_openapi.py --check` / `gen_frontend_types.py --check` **双绿**；实际 **111 paths / 147 schemas** / `types.generated.ts` 147 interfaces；`git status --porcelain` **空** ⇒ 零漂移 |
| 突变预检（只读） | 规格文件 **86 个 / 条目 480 条 / 问题 0 处**，exit 0，档位行"串行（结论可信）" ⇒ **只证明可安全交给执行器，不等于 480/480 被捕获**（本次未跑档②/③） |
| 端到端 smoke | **PASS**：GET / → health(tables=42) → 建项目 → 建角色 → 世界规则 → model-configs → 建章 → plan COMPLETED → write COMPLETED → review **PAUSED at author_review** → resume approved → review COMPLETED → commit COMPLETED → `chapter.status=COMMITTED` → state 含 observer 内容（hook/event/character）→ 挂卷后重装（tail before/after=1/0）→ 再 plan COMPLETED → **SYNC OK**（19 条 [smoke] OK + 1 条 PAUSED 信息行） |
| check_state_sync（dev 库，只读） | **SYNC OK**；有快照项目 DB=1013 / 快照=1013 / 差=0；5 个无快照项目单列不计漂移 |
| 思考档一致性 | 后端 8 档 / 前端 8 档，set-equality + 逐值拼写**全等** |
| skill 包自检 | 52 项全过 + selftest 12/12 变异全被抓住；六处版本副本一致于 3.10.1 |
| 前端 | `tsc -b --force` 0 错；`vitest run` **899 passed / 66 文件**（磁盘 66 个测试文件 = vitest 收集 66 ⇒ 无静默漏收）；`npm run build` 通过 |

### 首轮跑出的一条假红（已定性）
`tests/unit/test_cache_key_perf.py::test_default_run_uses_temp_db_and_never_touches_data_dir` 在**第一轮**全量红（4615/1 failed），第二轮全量与定向单跑均绿（16 passed）。
真因：该断言对**共享的仓内 `data/` 目录做全局逐文件快照**；同机并发跑的 `smoke_e2e.py` 子进程写了 `data/smoke_e2e_*.log` 与 `data/.last_seen_commit` ⇒ 快照不一致。
⇒ **量具侧脆弱**（断言打在全局可变状态上），与 AGENTS 坑区"夹具自带噪声 ⇒ 失败点漂移"同族。**AGENTS.md 对 `cache_key_perf` 0 命中 ⇒ 未登记**。
⚠️ 这是本轮审计自己制造的一轮假红 —— 若当时只有单轮全量就下结论，会把"并发噪声"记成"新回归"。

---

## 三、A 档：Lead 亲验确认的问题（6 条）

### A-1　CI backend job 自 2026-10-25 起一直红，根因是 Windows-only 语义的守卫测试

> **⚠️ 订正（2026-11-04 批次三十五 · 先验判断被推翻，必须留痕）**：本条标题写「根因是 Windows-only 语义的守卫测试」，
> **该判断只对了一半**：CI 红灯的**直接触发**确是那两条用例（其构造的 `/home/runner/work/novelos/novelos`
> 在 Linux 上不存在），但**真根因在生产代码**——`scripts/guard_repo_write_path.py::_relative_normalized`
> 拼前缀时**硬编码 `"\\"`**，而 `normalize_for_compare` 走 `os.path.normpath`（Windows 产 `\`、POSIX 保留 `/`）
> ⇒ **POSIX 上仓内子路径一律判仓外** ⇒ 守卫在 Linux/macOS 上**形同虚设**（B2 basetemp 守卫 + 8 个生产脚本的
> `check_write_path` 写盘点同时失效）。
> ⇒ **「把用例标 skip」只能让红灯消失，门还是没装上** ⇒ 本批按**治本**处置（改判定口径为成对 `PathFlavor` +
> 平台化测试 + POSIX 反向用例），**不是**「让 CI 变绿」。
> 详见 CHANGELOG `## [Unreleased]` 的「批次三十五」段与 AGENTS §五新增的两条坑。
> 下列「附带判定」段落的事实（POSIX 上仓内子路径依赖身份核）**成立且是本条缺陷面的准确描述**。

- **位置**：`tests/unit/test_conftest_guards.py:200-212`（`test_lowercase_drive_letter_variant_is_still_inside_repo`）与 `:214-230`（`test_repo_internal_basetemp_with_lowercase_drive_letter_is_rejected`）；判定实现 `scripts/guard_repo_write_path.py:202-218`（`_relative_normalized`）、`:233-250`（`_relative_by_identity`）
- **证据（实测）**：CI run `40208499569`，head `d79a7bb71f5e4f6fb6115e5720a0137e8ec6d9be`，`conclusion=failure`；4 个 job 中 `backend (pytest + ruff)` 的 **Pytest 步骤失败**（Ruff check 成功），`windows` / `web` / `e2e smoke` 三个 job 全绿。失败日志：CI 工作目录实测为 `/home/runner/work/NovelOS/NovelOS`（**大小写原样保留**），而测试用 `str(REPO_ROOT).replace("NovelOS", "novelos")` 构造出 `/home/runner/work/novelos/novelos` ⇒ **该目录在 Linux（大小写敏感）上不存在** ⇒ `_relative_by_identity` 的 `(st_dev, st_ino)` 身份核必然失败 ⇒ `is_inside_repo` 判"仓外" ⇒ `assert ... is True` 得 `False`。日志另见 `/home/runner/work/novelos/novelos/.b1_probe_lower\`（**尾随反斜杠** = `_relative_normalized` 里 `prefix = n_root + "\\"` 硬编码 Windows 分隔符在 POSIX 上混进比较串）。连带 **22 个 `ERROR at setup`**（`FileNotFoundError: .../novelos/novelos/.b1_probe_lower`）。本地 Windows 绿（`D:\zcodeproject\NovelOS` → `D:\zcodeproject\novelos` 是**同一个物理目录**）。
- **失败场景**：每次 push 到 `master` ⇒ backend job 恒红 ⇒ **红灯成为噪声 ⇒ 真红被淹没**（这正是 AGENTS 坑区"CI 红了没人看"的**活实例**，且已挂了 10 天）。
- **附带判定**：这不是产品缺陷，但**暴露了一个真缺陷面**——`is_inside_repo` 在 POSIX 上对"仓内子路径"依赖身份核；只要路径**不存在**（写盘前的常态）且**大小写与真实路径不同**，就会判"仓外" ⇒ B2 basetemp 守卫在 Linux 上**形同虚设**（仓内 basetemp 不会被拒，退出码 4 那条断言实测拿到 1）。
- **判别动作**：`pytest tests/unit/test_conftest_guards.py -q` 在 Linux 上必红 2 条；把该两条标 `skipif(os.name == "nt")` 或改成"路径存在性无关"的纯字符串口径断言。
- **同类已登记（C 档）**：AGENTS 坑区"突变套件接进 CI —— 未在 Linux dry-run，先不接"（`l0_pid_alive` 一族针对 Windows 专属语义 ⇒ 在 Linux 上恒红）——**同族新实例**。

### A-2　前端「生成计划」的按次模型覆盖**静默失效**（选了下拉也不生效）

- **位置**：前端 `apps/web/src/pages/ChapterDetailPage.tsx:333-338`（`capabilityByAction`：`plan: 'reasoning'` / `write: 'creative_writing'` / `review: 'light'` / `commit: 'observer'`）；后端 `packages/workflows/chapter_plan/pipeline.py:104-106`（取 `.get(capability_for("director_planner"))`）；映射权威 `packages/core/model_router/router.py:79`（`"director_planner": "creative_writing"`）
- **机制**：chapter-plan 的**唯一** AI 节点是 `director_planner`（`pipeline.py:362`），它按 **capability 名**去 `model_overrides` 里取档案 id。前端发的是 `{"reasoning": <id>}`，后端要的是 `{"creative_writing": <id>}` ⇒ **键对不上 ⇒ `.get()` 返回 None ⇒ 走默认档案** ⇒ 用户在下拉里选了档案，界面上也显示"已选"，但**实际调用的仍是默认模型**，零告警。
- **失败场景**：作者在章节页点「生成计划」并在下拉选了"推理规划"档案 ⇒ 请求体带 `model_overrides={reasoning: id}` ⇒ 后端按 `creative_writing` 取 ⇒ 取不到 ⇒ 计划仍由默认档案生成 ⇒ **作者以为按次覆盖生效了**。
- **旁证（同一机制的第二处半伤）**：`review: 'light'` 只能覆盖 `critic`（`AGENT_CAPABILITY['critic']='light'`），**覆盖不到 `deep_reviewer`（`'reasoning'`）** ⇒ 「深度复审」环节不跟随所选档案。
- **判别动作**：后端一条断言——`model_overrides={'creative_writing': pid}` 时 director_planner 节点收到的 `profile_id == pid`；反例 `{'reasoning': pid}` 时**必须断言它不被采纳**（否则修好后这条会红）。
- **交叉命中**：前端测绘者与 plan 链测绘者**各自独立**从两端命中同一处。

### A-3　前端 `Commit` 类型是「幽灵形状」——与后端返回逐字对不上

- **位置**：前端 `apps/web/src/api/types.ts:257-265`（手写 `Commit`）、`apps/web/src/api/endpoints.ts:342`（`api.get<Commit[]>(/projects/${pid}/commits)`）；后端 `packages/core/api/routers/story_state.py:309`（`response_model=list[CommitListItem]`）、`packages/core/story_state/models.py:307+`（`CommitListItem`）
- **机制**：前端 `Commit` 声明 7 键 `commit_id / project_id / version / parent_version / message / author / created_at`；后端 `CommitListItem` 返 **13 键** `commit_id / project_id / branch_id / chapter_id / previous_state_version / resulting_state_version / delta_id / validation_json / author_approval_json / timestamp / workflow_run_id / rollback_of / draft_version` ⇒ **前端那 5 个字段（version / parent_version / message / author / created_at）在后端返回里一个都没有**，后端返的是 `timestamp` 与拆成两列的 state_version。
- **失败场景**：前端任何读 `c.version` / `c.author` / `c.created_at` 的地方 ⇒ 恒 `undefined` ⇒ 提交历史面板的版本号/作者/时间**空白**；因为是**手写类型**，TypeScript 编译期自洽 ⇒ **不报错**，codegen 也不会发现（生成类型里有 `CommitListItem`，前端没用它）。
- **判别动作**：把 `commitsApi.list` 的返回类型改成生成的 `CommitListItem`，并让 `apps/web/src/api/types.ts` 的手写 `Commit` 消失（AGENTS §二 约定：能生成就生成）。
- **同族**：AGENTS 坑区"夹具从被测代码的实际输出里抄形状 ⇒ 缺陷被写进断言"的前端版。

### A-4　`commit_delta` 与章节置 `COMMITTED` **不在同一事务**

- **位置**：`packages/workflows/chapter_commit/commit.py:427-439`
- **机制**：`svc.commit_delta(...)` 内部自带事务并提交（`story_state/commits.py:806` 附近）；紧接着**另开一条连接**执行 `UPDATE chapters SET status='COMMITTED', updated_at=? WHERE chapter_id=?`（**无 `WHERE status` 条件**），再单独 `conn.commit()`。
- **失败场景**：
  ① 两次写之间进程崩溃 ⇒ `story_states` 已推进、`commits` 行已落，**而 `chapters.status` 仍停在 `REVIEWED`** ⇒ 章看起来没提交、状态快照却已变 ⇒ 重跑 commit 会以**新的** `previous_state_version` 再提交一次 delta ⇒ **`state_version` 再 +1**（版本线出现一次无意的重复推进）。
  ② 并发改稿（`create_draft` 把章降回 `DRAFTED`）与 commit 并发 ⇒ 无条件 UPDATE 会把作者**刚写的稿**盖回 `COMMITTED`。
- **判别动作**：一条故障注入用例——`commit_delta` 成功后人为抛异常 ⇒ 断言 `chapters.status` 与 `story_states.state_version` **要么都变、要么都不变**（当前实现：前者不变、后者已变 ⇒ 红）。

### A-5　`AGENTS.md §六` 一致性矩阵的 OpenAPI 基线过期 13 paths / 11 schemas

- **位置**：`AGENTS.md:470`（§六 OpenAPI 行，写「98 paths / 136 schemas」）；`CHANGELOG.md:856`（写「109 paths / 145 schemas」）
- **实测**：实际 **111 paths / 147 schemas / `info.version=3.10.1`**；`types.generated.ts` 147 interfaces；`--check` 双绿 ⇒ **codegen 产物本身零漂移**，是**文档基线没跟**。
- **与坑区同族**（方向相反）：AGENTS 坑区「『登记不修 / 未在本批实施』写进文档，修复落地后没回填 ⇒ 文档与代码正相反」——这里是**代码前进、文档基线没跟**。
- **判别动作**：`scripts/export_openapi.py --check` 的输出里已经有 paths/schemas 数 ⇒ 可加一条把它与 AGENTS.md 声明比对的看守。

### A-6　CHANGELOG 把突变的**文件数 86** 当成了**条目数**

- **位置**：`CHANGELOG.md` 2026-10-25「交接收尾批」验收行（写「突变预检 86 条 0 问题」）
- **实测**：`run_spec.py --all --precheck` 报「规格文件 **86** 个 / 突变 **480** 条 / 问题 0 处」；2026-10-24 基线是 **85 文件 / 477 条** ⇒ 本批实际新增 **1 个规格文件 + 3 条突变**，CHANGELOG 未回填条目数。
- **判别动作**：预检输出里同时打印两个数；文档引用时必须写"文件数 / 条目数"双口径。

---

## 四、B 档：测绘者提出、**Lead 未亲验**的候选（约 60 条，按链分组）

> ⚠️ **不可直接当结论**：这些是子代理逐行读过源码后写下的 file:line，但**没经过"对抗验证"那一关**（路径可达性 / 既有登记 / 过度主张三查），Lead 也未复算。
> ⚠️ 多数标注"静态阅读推断，未实测"。使用前请先回源码复核对应行。

### B-1　评审与提交链（chapter_review + chapter_commit + quality）— 9 条
- `gate.py:342-401` `_latest_review_report_draft_version` 取"最近 5 条 chapter-review run"**只按 `started_at DESC`，SQL 无 `wr.status` 过滤**；而同域的 `_latest_completed_review_end`（`control.py:115-143`）与 commit 时序守卫（`commit.py:379-390`）都筛 `status='COMPLETED'` ⇒ 被驳回/异常终止的 review run 只要留下 `author_review.__pause_payload__`，其 `review_report.draft_version` 会被质量门当"被审版本"。**缓解不覆盖这一支**（无 COMPLETED review 时 `control.py:613-618` 直接放行）。
- 显式 `draft_version` 指向**不存在的草稿** ⇒ `resolve_draft` 返 `None` 但 `draft_unavailable` 仍为 False ⇒ `draft=""` ⇒ `score_style("")=100`、`score_pacing("")=100` ⇒ `should_block` 为假 ⇒ **质量门"量空正文"并放行**。对照：评审侧同场景 `raise ValueError`（`chapter_review/pipeline.py:386-389`）⇒ **同一请求体字段两侧态度相反**。
- delta 在质量门与人工审批**之前**就落库（`commit.py:309` `submit_delta` ⇒ `status='validated'`）；门禁阻断 / high_risk 被拒 / run FAILED 时该行永久残留，**全仓未见 `state_deltas` 的删除/过期清理路径**。
- `control.py:279-281` 与 `common.py:59-61` 注释称 `draft_version`「仅 chapter-review 读取」，但 `gate.py:425` 会读 ⇒ **注释与代码相反**。
- `common.py:45-48` 注释称 `quality_gate_mode` 默认 `report`，实现默认 `enforce`（`gate.py:326` `os.environ.get("NOVELOS_QUALITY_GATE", "enforce")`）。
- 快通道 +「无 COMPLETED review」时，`_guard_commit_draft_freshness`（`control.py:613-618`）直接 return ⇒ 两道"草稿被审过"守卫**同时失效**。
- `QualityService.save_report`（`service.py:134-135`）在 `report.draft_version is None` 时二次读"最新 draft 版本"补全 ⇒ 与"正文不可检"路径（把 draft_version 置 None）叠加。

### B-2　LLM 调用面与模型路由 — 12 条
- `runner.py:483-496`：空流重试用 `exc.__class__(f'{exc_str} [empty stream retried once, still empty]')` 重建异常 ⇒ **生产非 mock 路径可能抛 `TypeError`**（自定义异常构造器签名不符）。
- `router.py:413-426`：构造 `AnthropicProvider` / `OllamaProvider` 时**未传 timeout** ⇒ 档案级 `timeout_s` 对这两家**静默忽略**（默认 60.0s）。
- `structured_output.py:835` `_VALIDATORS` 无 `summarizer`，`validate_contract:1048-1052` 对未知 `expected` **静默 return**（只保证合法 JSON）。
- `router.py:651-660` `call_with_fallback` 的 `except Exception` 把 `TypeError`/`AttributeError` 等**编程错误**记成 `'unexpected error'` 并换下一个候选 ⇒ 单候选时**真实错误被吞**。
- `cache.py:897-916` `_fingerprint_skills` 只含 `skill_id:version`，**缺 name 维度**；而注入块 `consumers.py:195` 的 name 取 `payload.get('name') or row['name']` ⇒ **同名不同 id 会互相命中缓存**。
- `prompts.py:346-393` `_upsert_agent` 在"提示词内容未变"分支**直接 return 不 commit** ⇒ 此前 `role`/`config_json` 的刷新被回滚。
- `runner.py:460-467` output-invalid 重试**每 attempt 新建 ModelRouter** ⇒ 重试从候选链第一位重来 ⇒ 两次输出可能来自不同模型。
- `effective.py:489-495` `provider` 形参**从未被读取**；`effective.py:457/589` 的 `upstream_params` **计算了但生产路径不消费**（只有测试断言它）。

### B-3　编排引擎与启动入口 — 9 条
- `engine.py` `_mark_run_running` 的 SQL **无状态条件** ⇒ resume 不是原子的"PAUSED→RUNNING"比较交换 ⇒ 并发 resume 可能双跑。
- `engine.py:988` `_insert_node_row` 每插一个带 `agent_name` 的节点就**泄漏一条连接**（`get_connection` 未关）。
- `engine.py:1042-1068` / `:1143+` 的 `_dump_json(capped)` **无 try/except** ⇒ ctx 序列化失败 ⇒ run **永久停在 RUNNING 且无终态**。
- `engine.py:303-319` `_resolve_checkpoint_exclude` 在"run 的 workflow 未注册"时**静默返回 [] 且无日志** ⇒ resume 后 scrub 全失效。
- `engine.py:809-815` `_prepare_resume_ctx` 找不到 `current_node` 时**静默 `start_index = 0`** ⇒ 节点改名后 resume 会从头重跑。
- `control.py:65-112` 与 `engine.py:242-279` **两份同形 SQL**（"同章是否有活跃 run"）；`control.py:601-625` 与 `commit.py:379-403` **两份**（"最新稿是否晚于最近一次 review"）。
- `runs.py:54-63` 的 `OR wr.chapter_id IS NULL` 会返回**所有项目**的项目级 run，正确性**完全依赖 API 层事后过滤**（`runs.py:338`）。
- project-init 是唯一**同步起跑**且**没有**项目级守卫 ⇒ 同 `project_id` 并发 POST 不被拦。

### B-4　写作链 chapter_write — 8 条
- `writer.py:193-197` `_measure_writer_attempt` **只在 `mode=='revise'` 时剥** stray `---REVISION-CHECKLIST---` 尾块 ⇒ **write 模式量字数时把尾块算进去** ⇒ 字数判据偏大。（注意：AGENTS 记的是"**保存**路径按内容剥离"，与"**测量**路径"是两处 —— 若成立则是新面，不是已登记项。）
- `pipeline.py:897-899` `_condense_node` 的 payload 声明 `"agent": "condense"` + `"prompt_version": "condense:v1"`，但**实际调 `run_agent(db, "polisher", ...)`** ⇒ 落库标签与真实调用不符。
- `writer_input.py:285-288` `recent_prose.last_scene_excerpt` **恒为 ""**、`:297` `retrieved_memory` **恒为 []**，而 `writer-v5.md §5` 把两者写进了输入契约。
- `length.py:61-64 / 79-82` 两处**无日志**的 `except Exception` ⇒ 读库失败时返回默认字数带。
- `pipeline.py:518-541` 取 `writer_model_id` / `writer_prompt_version` 用 `ORDER BY rowid DESC LIMIT 1` 取本节点**最后一条** `ai_call_logs` ⇒ 被采纳的尝试可能不是最后一条。
- `packages/workflows/chapter_write/README.md:58` 称"REVIEWED / COMMITTED 章节重跑 write 会被拒绝"，实际 `save_draft.py:174-178` **允许 REVIEWED→DRAFTED**。
- `scene_planner-v2.md` 的示例仍写 `"prompt_version": "scene_planner:v1"`，而节点已改取 ACTIVE 标签。

### B-5　计划链 chapter_plan — 8 条
- **"生成计划"的按次模型覆盖静默失效**（= A-2，两条测绘交叉命中）。
- `pipeline.py:176-187` `_save_plan_node` 的字数优先级**不含 `ctx['target_word_count']`** ⇒ 请求体显式传的 target 被忽略。
- `control.py:215-252` `start_plan` **不检查 `chapters.status`** ⇒ 服务端无章节状态闸门（前端却限制在 PLANNED）。
- `planner_input.py:40` 回落常量 `DIRECTOR_PLANNER_PROMPT_VERSION` 仍是 `'director_planner:v2'`，而仓内提示词已到 v4。
- `builders_common/excerpts.py:222-277` `_build_trigger_corpus` **只读 `chapters.plan_json`，不读 `outline_json`** ⇒ outline-batch 建出的新章（plan_json 为空）触发语料为空。
- `planner_input.py:292-294` 在 `build_ctx` 阶段算 `prompt_version`，`runner.py` 的自愈推进发生在**之后** ⇒ 一次性过渡态。
- `common.py:42` `expected_role` 全程自由字符串，直接覆盖 `chapter.expected_role`。

### B-6　project_init / deconstruct_book / outline_batch — 10 条
- **`reference.py:471-478` deconstruct 的 run 归属列恒为 NULL** ⇒ 该 run 出现在**每个**项目的运行记录里（上一轮修过"run 列表跨项目可见"，deconstruct 漏了）。
- **`reference.py:471` deconstruct 无项目级并发守卫**（未传 `project_level_guard=True`，且 `chapter_id=None` ⇒ 不受 `idx_workflow_runs_active` 保护）。
- `reference.py:471` deconstruct 是三条链里**唯一仍同步占用 HTTP 请求线程**的长任务（T2 节点对每一章串行调一次 `run_agent`）。
- **`project_init/pipeline.py:1219-1236` 重跑会删掉本卷全部章节，且无状态过滤** ⇒ 已被写过的章被删（FK 会挡住并让 run FAILED，但已写章节有丢失风险）。
- `outline_batch/pipeline.py` 的 `_HOOK_OPEN_STATUSES` / `_DEBT_OPEN_STATUSES` 是 `builders_common/ledger.py:408-409` 的**副本**。
- `chapter_summaries` 每次 commit **追加一行**（`summary.py:145-161` 纯 INSERT），两个消费方口径不同。
- `outline_batch/pipeline.py:142` `prompt_version` **硬编码常量**，落库标签却是现算的 —— 两处不同源。
- `outline_batch/pipeline.py:278` 建的章 `volume_id` 恒为 NULL（project_init 建的章带 volume_id）。
- `0001_init.sql:549` `chapters(project_id, number)` **无 UNIQUE 约束**（只有普通索引）⇒ outline_batch 重跑可造重号章。

### B-7　旁支链 simulation / delivery / content_sync / backup + 非 workflows 的 API 面 — 8 条
- `core/simulation/service.py:261` 自定义 `name` 使推演在"列表"与"重放"两条出口上都**不可见**。
- `core/simulation/service.py:474-483` `_safe_archive` 是裸 `except Exception` ⇒ 归档失败被吞 ⇒ 临时分支可能停留 ACTIVE 并进入 director 装配。
- `core/simulation/service.py:281-341` 推演失败留残骸，且同名重跑必 409。
- `core/delivery/verdict.py:397` 交付判定的 gate 由**运行期** `issue_gate` 重推 ⇒ 历史报告的分档会随 severity 矩阵漂移。
- `core/delivery/evidence.py:243` 交付判定的证据收集是"逐章 2~3 次新建 SQLite 连接"。
- **`core/content_sync/service.py:752-775` `push_book` 第 6 步写出的完整项目备份包，可被 `POST /api/projects/import-backup` 无鉴权重新导入运行库。**
- `core/content_sync/service.py:307-360` `_reconcile_stale_novel_files` 的"物理删除"归属凭证**唯一来源是上一版 manifest.json 本身**（无外部签名/校验）。
- `routers/quality.py:162-166` 的 `Literal` 打在存量落库数据上 ⇒ 脏 `issues_json` 会让端点 500。

### B-8　前端接线 — 13 条
- **A-3 的 Commit 幽灵形状**（已升 A 档）。
- **A-2 的 plan 覆盖键错**（已升 A 档）。
- `ChapterDeliveryActions.tsx:90-91` "撤销发布标记"的文案写「章节回到已提交待处理」，实际 `move('PLANNED')`；后端 `ALLOWED_NEXT` 不允许 `RELEASED→COMMITTED`。
- `apps/web/src/utils/chapterState.ts:286-292` 的 `ALLOWED_NEXT` 自称"与后端对齐"但**缺两条**：`REVIEWED` 缺 `DRAFTED`、`COMMITTED` 缺 `REVIEWED`。
- 后端 `story_state.py:441-448` 的 409 `{error:'approval_required', high_risk_change_ids}` **前后端都无出路**（promote 走不通）。
- `chapters.outline_json` 在前端**零消费**（grep 只在 `api/types.ts:333` 有一行声明）⇒ 策展大纲槽在 UI 上既不可见也不可编辑。
- `qualityApi.evaluate` **无版本入参**（`endpoints.ts:658-659`，后端 `quality.py:179-226` 也无）⇒ 永远测最新版，而出参已带 `draft_version` ⇒ 质量评估与"被评的那一版"脱钩。
- `ChapterRunBanner` 用**不透明** `workflow_id`（`wf_<hex>`）判断动作。
- `pause_payload` 抽取有**两套实现**：前端 `utils/pausePayload.ts:15-37` 取"迭代序第一个含 `__pause_payload__` 的值"，服务端 `_extract_pause_payload` 另有实现。
- `ChapterPlanSection.tsx:38` PATCH `{plan_json}` 是**整体替换**（后端 `domain/chapter/service.py:230-231`），而 `plan_json` 同时承载 revision 相关字段。
- `WorkflowStartArgs` 声明了 `author_intent` / `target_word_count`（`ChapterPipelineHeader.tsx:22-23`），但四按钮的 `onStart` payload **从不带这两项** ⇒ 写正文无「按次字数 / 铁律」入口。
- `projects.foreshadow_overdue_chapters`（进 director 装配、AGENTS §二.2 列为缓存键维度）**前端无入口**。
- 后端 8 个 skill 端点（`routers/skill.py:101-234`）**前端零消费**。

---

## 五、覆盖与未覆盖

**已覆盖**：后端全量回归 + 树指纹、端到端 smoke、codegen 漂移、ruff、突变预检、state_sync、思考档一致性、skill 包自检、前端 tsc/vitest/build；链段测绘 8/9（编排引擎、plan、write、review+commit、LLM 调用面、其余三条链、旁支链、前端接线）。

**未覆盖（重要）**：
1. **第 9 条链没跑完**：`story_state` 底座 + `versioning` + **装配缓存**（`context_engine/cache.py` + `builders_common/`）——这是 AGENTS 硬规则 2「缓存键漏维度」的主战场，**本轮完全没有覆盖**。
2. **「找问题」的 8 维度横切没跑**（状态机 / 装配数据流 / 缓存键失效 / 错误降级 / 闸门 / 契约一致性 / 事务并发 / LLM 结构化输出）⇒ B 档是测绘者顺手记的隐患，**不等于该维度的系统扫描**。
3. **对抗验证整层没跑** ⇒ B 档**全部**未经"路径可达性 + 既有登记 + 过度主张"三查。
4. **全量突变未跑**（只跑了只读预检 480 条 / 0 问题）⇒ **不声称"480/480 全捕获"**。
5. 真实上游模型行为、真实 opencode / Command-Code 端点：**按 §一 硬约束 2 一律未验证**。

---

## 六、建议处置顺序（危害 ÷ 代价）

1. **A-1 修 CI 红**（两条守卫测试标 Windows-only，或改成路径存在性无关的口径）—— 不修则**每一次 push 的红灯都是噪声**，真回归会被淹没。代价最小、收益最高。
2. **A-2 修 plan 覆盖键**（前端 `plan: 'reasoning'` → `'creative_writing'`；另补 `deep_reviewer`）—— 用户可见的"选了不生效"，一行改动。
3. **A-4 把章节状态更新并入 commit 事务**（或加 `WHERE status='REVIEWED'` 条件 + 失败可重入）—— 数据一致性的根。
4. **A-3 前端 `Commit` 换生成类型** —— 消除幽灵形状。
5. **B-1 的"质量门量空正文放行"**（若复核成立）—— 与已登记的"不可检 ≠ 合格"是同一族，但**这一条把 `draft_version` 指向不存在草稿的情形排除了**，值得优先复核。
6. **A-5 / A-6 文档回填**（AGENTS.md OpenAPI 基线 + CHANGELOG 突变条目数双口径）。
7. **把 `cache_key_perf` 的全局 `data/` 快照断言改成"只盯自己那个文件"**（本轮实测到的量具侧脆弱）。
8. **补第 9 条链的测绘 + 8 维度横切 + 对抗验证**（下一轮），特别是**装配缓存键维度**这一维。

---

*本报告由一次被中途叫停的多 agent 审计产出。A 档由 Lead 逐条读码/实测复核；B 档为子代理测绘结论，未经验证，不得直接作为缺陷依据。*
