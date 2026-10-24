# NovelOS 全面审查 · 第三轮（2026-10-24）

> **定位**：2026-10-23 已跑过两轮全面审查（三路审计 A/B/C + 第二轮 9 面）+ 对抗审查 B 三轮 + 修复批 + 测试瘦身批次三十三。
> 本轮**不做重复扫描**，差异化定位四方向：① 修复闭环验证（上轮声称 vs 实测）；② 动态健康基线（瘦身后真绿态）；
> ③ 增量代码审查（50a556e..HEAD 未被全面审过的新改动）；④ 高危面复查（缓存键根治完整度、安全护栏有效性、文档一致性）。
>
> **方法**：6 路只读子代理（动态基线 / 修复闭环 21 项 / 缓存键专项 / 增量对抗审查 / 安全护栏 8 项 / 文档一致性），
> critical 与 major 由 Lead 亲自读码/复算后入册（亲验记录见 §3.4、§5.1、§6.3）。
> **纪律遵守**：零真实 LLM 调用；零内容仓正文接触；未改 dev 库；未做 git 写操作；突变只跑只读预检。

---

## 0. 一句话结论

**代码与闸门全部真实健康，无 critical；上轮 21 项修复经独立复核全部真实落地且已提交（零反向声称）——
本轮真正的问题是「文档与台账滞后于代码」：8 项已修未回填 + 6 条过期声称，
外加 2 条中危的缓存一致性残余（branches 版本推进挂账未清、前章草稿面无键维度——后者为本轮新发现）。**

---

## 1. 动态健康基线（7/7 全绿）

| 闸门 | 结果 |
|---|---|
| 全量 pytest `-n 20` | **4379 passed / 1 skipped / 1 xfailed / 0 failed**（3:00）；台账「4379/1/1」口径查明 = 1 skipped（`NOVELOS_REAL_DB` 未设）+ 1 xfailed（快照滞后已知边界，strict） |
| ruff | 0 违规 |
| 突变只读预检 | 85 个规格文件、工作区零改动 ⇒ 0 条增量、exit 0 |
| 树指纹 | 跑测前后逐位一致（`ca58ae2f…`） |
| tsc -b | 通过 |
| vitest | 64 文件 / **853 用例全过**（28s） |
| npm run build | 成功（1 条信息级 Rollup 混合导入提示） |
| 版本一致性检查 | 52 项全过，六处 = 3.10.1 |

⚠️ 环境注记（非仓库缺陷）：本机 pytest 默认临时根 `%TEMP%\pytest-of-AiStation` ACL 损坏（WinError 5，
与台账 D-17 记录的目录同源），标准命令 xdist 启动即 INTERNALERROR；带 `--basetemp` 绕行后全量正常。
后续本机跑测建议固定带 `--basetemp`。

---

## 2. 修复闭环验证（21 项，零反向声称）

上轮台账 D 系列 + 修复批核心项逐项独立复核：**19 项实测「已修且已提交」，2 项他路已验（D-0/D-18），无一项「声称已修但实际未修」**。

### 2.1 已修待复核 4 项 → 全部落地

| 项 | 证据 |
|---|---|
| D-5 xdist 进 dev extra | `pyproject.toml:36-53`；`[dependency-groups]` 已删；ci.yml:134-141 Windows 自检 |
| D-12 域层 | `character/service.py:361-363` 查两端点抛 ReferencedError |
| D-16 web 三处版本 | `skills/novelos/scripts/check_skill_package.py:908-933` + M9-M12 + `tests/api/test_frontend_version_consistency.py` |
| D-17 basetemp | 台账记 2 处，实测 **4 处**全带（tests 2 + harness/probe 2）——覆盖更多，非缺陷 |

### 2.2 台账 stale 项（8 项已修未回填——「文档滞后于代码」，方向全部为好）

| 项 | 台账记 | 实测 |
|---|---|---|
| D-2/D-3 恢复工具+原子写 | 未修 | `scripts/restore_db.py` 已落地（os.replace:231 / wal_checkpoint:188 / 恢复后校验:123-126） |
| D-4 备份默认明文+静默轮换 | 未修 | `backup_db.py:461` `encrypt = not args.plaintext` 默认开；密钥缺失报错退出码 2 |
| D-7 缓存键 5 维度 | 未修 | 键 23 元含 5 fp（`director_input.py:523-540`）+ LEDGER-KEY-1~5 突变 + ledger/plot 写路径推版本 |
| D-8 CLI 退出码看守 | 未修 | `test_codegen_artifacts.py:281/291` 进程级正反对照 |
| D-10 外发取错稿 | 部分完成 | `exporter/builder.py:396` prefer_committed=True + 0034 迁移 + 10 用例看守（含存量回退零退化） |
| D-13 signing_check 静默 | 未修 | `_load_project_opening_rules` 返 (rules, error)；`checks.py:700-778` genre_opening_unavailable 显式标记 |
| D-18 明文凭据 | 悬空 | 库内 api_key 计数 **0**；secrets.json 3 键全部匹配活库实体、零悬空 |

D-6（质量不可检硬停）与硬规则 11（凭据守卫）同样实测已提交且机制完整（`draft_resolver.py:65` / `gate.py:539-541`；守卫门面 644 行 <700）。

---

## 3. 缓存键根治完整度（D-7 复查）

### 3.1 已闭合面

- director 键 23 元，D-7 五维度（hooks/debts/plot/foreshadow/title）全在位；writer 键 23 元按消费面对齐（writer 不消费 hook/debt/plot，不补是**正确**而非缺口）；planner 复用 director 键。
- 判别力充分：`l22_director_ledger_key.py` 9 条突变（撤维度×5 + 撤写路径推进×4）；`test_ctx_c_ledger_key.py` 内容级断言 + 「未改必命中」反向不变量（:375）。
- 失效链路无旁路：进程内缓存 bump 即换键；commit 后显式失效只管本章、其余靠 state_version 主线，口径自洽。

### 3.2 残余缺口（Lead 已亲验）

| # | 缺口 | 性质 | 证据 |
|---|---|---|---|
| R-1 | **branches 全系写路径不推 state_version**（create/merge/archive 共 5 处） | **已知挂账未清**（修复批 docstring 自认「需要 Lead 协调」，至今无人接手）；键侧 plot_fp 已封脏命中，但根治条件②不完整——任何只按 state_version 失效的读者在分支操作后拿旧数据 | `cache.py:649-655` 自认注释；`branches.py:70/336/489/578`、`simulation/service.py:167` 无 bump |
| R-2 | **前章草稿面进 payload、无键维度、写路径不推版本** [新发现] | 前章重出 draft（revise/人工重写）而当前章装配已缓存且期间无 commit ⇒ 命中旧尾段/旧召回。波及 director `previous_chapter_tail`、两侧 `recalled_passages`、writer `recent_prose_tail`。流水线「先 commit(N-1) 再 plan(N)」顺序缓解，且 FTS 召回另有索引滞后叠加 | `director_input.py:342/345`；`summaries.py:289-296` 读 `ORDER BY created_at DESC LIMIT 1`；键 23 元无 drafts 维度 |
| R-3 | planner 空标题回填直写 title 不推 version | 低危（title_fp 兜底） | `chapter_plan/pipeline.py:248-253` |
| R-4 | summarize 独立事务晚于版本推进的窄窗口 | 低危（顺序工作流下概率极低） | `chapter_commit/summary.py:143-164` |

---

## 4. 增量代码审查（50a556e..HEAD，对抗式）

**结论：通过。** 五个重点面（contract runner 等价性 / migrated_db 替换等价 / 前端拆分守恒 / reveal 基线自指性 / 常规正确性）均未发现 critical/major。

Lead 补齐其待确认项（亲测）：
- **断言守恒**：拆分前 `ProjectInitPanel.test.tsx` expect 计数 **274** = 现状三份合计 **274**，逐位守恒 ✅
- handoff 声称「全量绿」由 §1 动态基线证实 ✅
- 收敛前后逐字等价为抽样级（深读 4 文件 + 抽查 7 处替换点，未发现行为不等价）

5 条 minor（不阻塞，留待下批顺手处理）：
1. `_contract_runner.py:252-270` `assert_response_keys` 全仓零消费、零看守例——建议删除或补看守
2. `_contract_runner.py:179-181/209-211` components.schemas 子断言无专属反例（突变可存活但无现行消费方）
3. runner 纪律文字（:31-32「model_fields 一处都不许出现」）与 reveal 文件三处保留现状字面冲突——改口径为「不许单独出现、出现处必须有硬编码基线同行」
4. `test_config_list_response_contract.py:455-463` 留本地 model_fields 实现绕开 runner 单点
5. `_contract_runner.py:301` `app_table_columns` 内嵌 asyncio.run，docstring 未警示「仅限同步用例」

---

## 5. 安全护栏复查（8/8 有效）

内容仓 push 护栏（no-push remote + pre-push 钩子）、痕迹仓零 remote、docs/evaluation 防回流（ls-files 仅剩 README+机制文档+gitkeep）、gitignore 四组新条目、**库内明文凭据 0**、secrets.json 3 键全匹配、仓内硬编码凭据扫描 0 可疑、CI 无危险触发器且零 secrets 消费——全部有效，无一退化。

D-18 记录需回填（现状优于记录）：台账记「悬空」，实测已全部对齐。

---

## 6. 文档一致性（6 条过期，全部是数字未回填类）

| # | 位置 | 声称 | 实测 |
|---|---|---|---|
| 1 | AGENTS.md:43、:253 | 迁移链 0001~0033 | **0001~0034**（0034 由修复批 e4b63f7 引入，两处未回填） |
| 2 | AGENTS.md:188（§三.7） | 突变规格 84 文件/476 条 | **85/477**（validate_specs 实测；§六 L450 已是 85/477 ⇒ AGENTS 内部两处不一致） |
| 3 | AGENTS.md:266 括注 | 远端 23 个 tag | **24/24**（v3.10.1 已打已推；「逐位一致」结论仍真） |
| 4 | AGENTS.md:43 | 业务表 42 / 含 _migrations 43 | 数字成立但**口径未注明不含 FTS5 虚拟表族**（直数得 49 会误判） |
| 5 | tests/README.md:4、README.md:243 | 「当前基线」3578 / 3800 | 当前 **4379/4381**（带日期标注非假事实，落后 5 批次） |
| 6 | AGENTS §四 命令区 | `scripts/check_skill_package.py` | 实际在 **`skills/novelos/scripts/`**（AGENTS 两处引用缺前缀，本会话派发即踩） |

另有台账 §四主表 6 行（D-2/3/4/7/8/13）+ §四之二 1 行（D-10）需按 §2.2 回填。docs/ 空目录计数（递归 6 个）与三处 `--ignore` 措辞一致性抽查**均一致**。

---

## 7. 汇总问题清单（分级）

- **critical：0**
- **major：2** —— R-1 branches 版本推进挂账未清（已知）；R-2 前章草稿面无键维度（新发现，有流水线顺序缓解）
- **minor：11** —— 增量审查 5 条（§4）+ 文档过期 6 条（§6）
- **环境（非仓库）：1** —— pytest 临时根 ACL 损坏，全量需 `--basetemp` 绕行

## 8. 建议动作（按优先级，供用户裁决）

1. **下批顺手回填**（纯文档，零代码风险）：AGENTS 六处数字 + 台账 7 行 stale + README 基线数——「已修未回填」积累到 8 项正是 AGENTS 坑区「修复落地后没回填」同形状的再犯。
2. **R-2 立项裁决**：前章草稿面纳入键维度（drafts 指纹）或 draft 写路径推 state_version——需权衡 drafts 写频度（每次 write/revise 都写）对缓存命中率的冲击，建议 Lead 单独批次设计。
3. **R-1 接手**：branches 写路径推版本是修复批自留的「需要 Lead 协调」挂账，脏命中面虽已被 plot_fp 封住，但根治条件②悬空，宜与 R-2 同批裁决。
4. 增量 minor 5 条可与测试瘦身 T-1（contract 剩余 3 项）同批处理。
