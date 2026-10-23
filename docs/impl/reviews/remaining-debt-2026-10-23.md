# 全面 review 未修项台账（2026-10-23）

> **来源**：2026-10-23 全面 review（三路只读审计 A/B/C）。**已修项**见 CHANGELOG 批次 24~28；
> 本文件只登记**明确不做 / 待排期**的项，每条带「为什么不做」与「要做时的判别」。
> 排序 = 风险从高到低。**凡本文件项被修掉，须在此标注批次号并移入 CHANGELOG。**

## 一、审计 A（架构 / 契约 / 文档 / 量具）

| # | 项 | 现状证据 | 为什么现在不做 | 要做时的判别 |
|---|---|---|---|---|
| A-1 | **25 个包目录零突变规格**（`agent_runtime` 6 文件 2104 行 / `content_sync` 6/2069 / `project_init` 2/1491 / `delivery` 5/1448 / `signing_check` 3/1108） | ⚠️ **数字口径已被第二轮复算订正（2026-10-23 晚）**：严格口径（解析 `MUTATIONS` 元组，非全文正则）= 规格文件 **76**、突变 **436** 条、覆盖目标 **123**；`packages/` 227 个 .py 中 **98** 有规格 / **129** 零规格；零规格目录 **23** 个（含单文件目录；≥2 文件的 **9** 个）。**方向不变**（结论仍是「零覆盖面积大」），但「227 中 72 有」与「25 个目录」两个数字**不准**。零规格目录实测清单：`content_sync` / `delivery` / `exporter` / `signing_check` / `simulation` / `domain/knowledge` / `domain/project` / `workflows/deconstruct_book` / `workflows/project_init`（**`arc` 不在其中**——`arc/service.py` 有 3 条规格，真正零的是 `arc/models.py`） | 面积大、逐模块锚点要人判；本轮已补 `structured_output`（l16）、genre（l18）、证据表（l17/l19）、codegen check（l21） | 按「改错会静默」排序逐个补；每个新规格必须做到「撤修复必红」 |
| A-2 | **✅ 已修（批次（三十））**：`_COUNTER_MODULES` 改为 **AST 派生** + 断言改如实口径 | 原状：10 项纯计数型手挑清单。**修前实测**：清单漏 `agent_runtime.prompts` ⇒ 读数偏小 1（地面真值 director `2 / 11`、writer `2 / 15`；清单法 `1 / 10`、`1 / 14`）；另有 **3 项空转项**（`context_engine/canon.py`、`context_engine/relevance.py`、`story_state/service.py` **0 处** `get_connection`，被 `if real is None: continue` 静默跳过） | 与批次二十四「固定清单」族同形；改它要重设断言口径 | 已完成：派生 + 反向看守（派生须超出手挑 / 须含 `prompts` / 空转项仍须无绑定）；判别 `test_counter_modules_are_derived_and_cover_both_connection_points` + 内存注入突变 3 failed |
| A-3 | **`domain→core = 72`、`core→domain = 136` 双向耦合无看守** | ⚠️ **数字已被第二轮复算订正（2026-10-23 晚）**：导入语句级 = **`domain→core` 41 行命中 / `core→domain` 47 行命中**；`core→domain` 的**非 API 层**部分 = **5 文件 / 8 条 import 语句**（`arc/service.py:52`、`delivery/evidence.py:42-44`、`exporter/builder.py:41`、`quality/service.py:47`、`signing_check/service.py:35-36`）。**「无环」成立**（模块级 2 环 0、3 环 0，Lead 实测）；**「无机器看守」成立**（`test_no_core_to_workflows_dep.py:87-91` 的 `LAYERING_DIRECTIONS` 只有 `core↛workflows`/`domain↛workflows`/`packages↛apps`，**无 `core↛domain`**）。⚠️ 口径必须精确：AGENTS.md:42 成文规则只禁 `core→workflows`，**并未**禁 `core→domain` ⇒ 是「边界未定义清楚 + 零机器约束」，**不是破口** | 属「基础设施方向」（只用 ids/db/story_state/logging_config）+ HTTP 层调领域服务，无环 ⇒ 判设计而非破口 | 若将来出现环，再立看守；当前只登记「这两条边无机器约束」 |
| A-4 | **文档断链审计的 5 条报告全为误报**（内容仓引用 / 外部仓 `owner/repo:path` / 已删文件的历史注记） | Lead 逐条复核 + `Test-Path` 实测 | 审计口径错，不是文档错 | 审计工具必须先排除三类；**结论：当前有效文档 0 条确证断链** |

## 二、审计 B（质量 / 题材）

| # | 项 | 现状证据 | 为什么现在不做 | 要做时的判别 |
|---|---|---|---|---|
| B-1 | **`separation` 依赖「存储精度」**（舍入包络的严格度是 rate 小数位的函数） | 审查实测：`0.1852` 时包络收紧到 9.11 会被拒 | 正解是**登记未舍入 rate 对**（新字段），属数据补录，需人工确认来源 | 登记未舍入 rate 后，separation 改精确比；届时 9.11 判假、CONTRAST 的 6.63-vs-3.45 立即暴露 |
| B-2 | **Q-4 的 5 条 `external` 登记只查「note 非空 + 数字在 description 里」** | 3.5 / 3.4·0.73·7.3 / 0.0030 | 登记式看守的固有上限；外部研究值无法机器核 | 若要收紧：外部值必须带 `source` URL 且 URL 可达（需联网，CI 不可控） |
| B-3 | **9/9 语料仍不可第三方复现** | 8 份在 gitignore 的 dev 库、1 份在内容仓且未推 | 硬约束：内容仓不推 GitHub；dev 库是测试数据 | **只登记可达性**（已做：`in_version_control` / `reproducible_by` + HUMAN_BASELINE §8 真比对）；结论只能声称「表内自洽」，不得声称「数字为真」 |
| B-4 | **`genre/verifier.py` 1224 行**（距 1500 余 276） | 审计 B 实测 + 六簇可拆方案 | 未逼近硬顶；拆分要动 `test_db_error_scope_e3.py:417` 打点的 `get_connection` 归属 | 趁未到顶拆成本最低；拆前先 grep `monkeypatch.setattr(gv,` |

## 三、审计 C（脚本 / 运维 / 数据）

| # | 项 | 现状证据 | 为什么现在不做 | 要做时的判别 |
|---|---|---|---|---|
| C-1 | **19 个脚本仍零突变规格** | ⚠️ **数字已被第二轮复算订正（2026-10-23 晚）**：`scripts/` 共 **32** 个 .py ⇒ **18** 有规格 / **14** 零规格（旧数 12/19 的分母 31 也不对）。零规格清单（14）：`ai_pattern_recall` / `ai_tone_calibrate` / **`export_openapi`** / **`gen_frontend_types`** / `human_baseline_synthetic` / `inspect_stalled_runs` / `m2_judge` / `migrate` / `outline_check` / `produce_chapters` / `readability_audit` / `real_llm_e2e` / `serve` / `smoke_e2e`。⚠️ 注意：批次三十的 `l21` 只打 `scripts/_codegen_check.py`，**没有打这两个脚本本身** ⇒ 它们仍在零覆盖清单里 | 逐个补成本高；本轮先修了 4 条 major | 按「改错会静默」排序：backup_db > content_sync > open_volume > produce_chapters > smoke_e2e > inspect_stalled_runs > export_openapi/gen_frontend_types |
| C-2 | **✅ 已修（批次（三十））**：两脚本各加 `--check`（全文级、换行归一、绝不写盘）+ `tests/api/test_codegen_artifacts.py` 8 例 + `l21_codegen_check.py` 3 条突变（3/3 CAUGHT） | 原状：两脚本有 `--out` 无 `--check`。**边界须精确**：既有 `tests/api/test_openapi_contract.py` 有**集合级**看守（`info.version` / `paths` / schema **名集合**）；实测把 `AdoptRequest.content` 的 `type` 由 `string` 改 `number`（名字集合不变）后那三条**全部通过** ⇒ **字段级漂移此前无看守**。CI 仍不跑（归 C-5，需批准） | 生成内容（字段类型/可选性）漂移需新判别 | 已完成；余下「CI 接线」见 C-5（未动） |
| C-3 | **✅ 已修（批次二十九）**：`eval_regression.py` / `m1_report.py` 默认改**系统临时目录**，`--write-docs` 才写仓库 | 2026-10-23 晚复算：两脚本已改（`eval_regression.py:76-88`、`m1_report.py:781-786`）⇒ **本行是 stale（已修未销）**，批次二十九提交 `71aa28d` 即落地 | — | 已完成；判别：默认跑一次后 `git status` 干净 |
| C-4 | **不可逆脚本无回滚快照**（`open_volume` / `world_switch` 靠幂等续跑） | 两处源码自注「中断点前已落库」 | 加事务化/快照是大改 | 若要改：先做「中断注入测试」证明当前会留下半态 |
| C-5 | **全量突变套件仍未接 CI**（只有只读预检接了） | `ci.yml` 自注「未在 Linux dry-run」 | 12.7 分钟/轮 × 每次 push 不可接受；且平台相关突变未筛 | 先在 CI 跑一次 dry-run 拿「Linux 上存活清单」，再决定接线或加平台判定 |
| C-6 | **smoke 无 fast_mode / auto_commit / volumes 挂卷 / revise 覆盖** | `scripts/smoke_e2e.py` | 扩展要起服务 + 多 run 编排 | 至少补 volumes 挂卷（批次二十四的 `volume_scope` 现无 e2e） |
| C-7 | **~~「真实 openssl 往返从未实跑」~~ ⇒ ⚠️ **本条原文当天即写错，已订正** | **订正（2026-10-23 晚，实测）**：fake openssl 只用于 `migrate_backups_to_sync.py`；**`backup_db.py` 的加密往返测试自 `e467cf3`（2026-10-22 批次十六）起就用真实 openssl**（本轮实跑 `tests/unit/test_backup_db.py` **6 passed**），另独立跑通完整往返（49 表逐表 sha256 全等、`integrity_check=ok`）。**真正缺的���是恢复侧**：仓内**无 restore 工具、无 runbook、无恢复后校验**；带 `-wal`/`-shm` 残留时恢复**静默失效**且 `integrity_check` 报绿；解密失败**仍会破坏活库**（无原子写） | 见「第二轮新增项」D-2 / D-3 | — | 新判别：临时目录跑 `encrypt→decrypt→integrity_check→表数核对`；恢复路径必须「先删 `-wal`/`-shm` + temp+rename 原子替换 + 恢复后校验」 |

## 四、第二轮新增项（2026-10-23 晚 · 全面审查第二轮）

> 报告全文：`docs/impl/reviews/full-review-round2-2026-10-23.md`。
> 本节只登记**未修项**；已在本轮亲验的结论见报告 §3。

| # | 项 | 现状证据 | 为什么现在不做 | 要做时的判别 |
|---|---|---|---|---|
| **D-0** ✅ | **🔴 内容仓仍挂 GitHub remote 且本地领先 17 个提交** | `git -C D:/zcodeproject/NovelOS-Content remote -v` ⇒ `origin https://github.com/charlesnty612/NovelOS-Content.git`；`rev-list --count @{u}..HEAD` = **17** | **隐私优先**：一次误 push 即发布全书 | 摘 remote 或加 `pre-push` 守卫（拒推非白名单分支）；判别：内容仓 `git push --dry-run` 被拒 |
| **D-1** ✅ | **🔴 操作痕迹落在软件仓 `docs/evaluation/` 且已上 GitHub** | `git ls-tree -r --name-only origin/master -- docs/evaluation/` ⇒ 含 `m2-judge/judge_results.json`（真实 chapter_id + 逐章 verdict）、`m1-data.md`（真实 project_id + 逐章评分）；`git check-ignore -v` ⇒ **rc=1 未被忽略** | **已不可撤回**（远端已有）⇒ 只能记录 + 防再犯 | 痕迹迁至 NovelOS-Artifacts；`docs/evaluation/` 加 ignore 或只留机制文档；判别：`git ls-files docs/evaluation/` 无真实 id 文件 |
| **D-2** | **🔴 备份「有写无读」：无 restore 工具 / 无 runbook / 恢复可静默失效且假绿** | 实测：残留 `-wal`/`-shm` 时恢复后 49 表 → **1 表**、`projects` 表不存在，而 **`integrity_check=ok`** | 属「新增能力」，且触碰不可逆动作（恢复会覆盖库） | 先落 `scripts/restore_db.py`（temp+rename 原子替换 + 删 `-wal`/`-shm` + 恢复后 `integrity_check` + 表数核对 + 故意失败不碰原库）；判别：临时目录往返 + 故意失败后原库完好 |
| **D-3** | **🔴 解密失败仍会破坏活库** | 实测：openssl exit 1，但目标文件**已被截断重写**（8192 → 356336 B，`database disk image is malformed`） | 同 D-2 | 恢复必须原子写（临时文件 + rename），失败即不动原库 |
| **D-4** | **🔴 `backup_db.py` 默认明文备份 + 加密后从不验证密文可解 + 密钥缺失静默轮换** | `--encrypt` 是 `store_true`（默认关），else 分支 `shutil.move` 落明文；`integrity_check` 在**加密前**的明文副本上做；`ensure_key_file` 缺文件时**静默生成新密钥**（实测 key `ab1d0551`→`c53b90f5`，旧密文 exit 1 `bad decrypt`） | 与既有策略「密钥进私有网盘」直接冲突，且改动涉及不可逆动作 | `--encrypt` 改默认开、`--plaintext` 显式 opt-in；加密后做解密自检；密钥缺失**报错退出** |
| **D-5** 🟡 | **🔴 CI 装不上 `pytest-xdist` ⇒ windows 并发路径 job 从未真正跑过** | `pyproject.toml:36-40` `[optional-dependencies].dev` 只有 pytest+ruff，xdist 在 `:66-70` `[dependency-groups]`；已装 METADATA 的 Requires-Dist 无 xdist；`ci.yml:116-117` 装 `pip install -e ".[dev]"`；`ci.yml:122` 用 `-n 4` | 依赖面改动小，但 CI 需重跑验证 | 把 xdist 挪进 `[project.optional-dependencies].dev`；判别：CI windows job 绿 + 该步输出 `4 workers` |
| **D-6** 🟡 | **🔴 质量门 fail-open：读不到草稿 ⇒ 该章被判「满分 0 issue」**（**critical** ⇒ ⚠️ **已订正为 major**） | Lead 端到端复现：`drafts` 表不可用 ⇒ `resolve_draft` 返回 None（不抛错）⇒ `draft=""` ⇒ `score_style` ⇒ **score=100 issues=0**；门禁消费点 `chapter_commit/gate.py:473`。**⚠️ 订正（2026-10-23 修复批，Lead 用 `git archive HEAD` 副本复算）**：**门禁路径本已 fail-closed**——`build_quality_context` 内的 `compute_char_stats_detail` 同样先读 `drafts` 表 ⇒ 表不可用时**先抛 `OperationalError`** ⇒ 到不了 `score_style` ⇒ 原「该章从未被评估却拿到满分、门禁放行」**不成立**；真实 fail-open 收窄为**两处独立调用 `resolve_draft`** 的路径（`core/delivery/evidence.py:181`、`workflows/chapter_review/pipeline.py:386`，属 Task 6 所有权） | 需设计「读失败」与「无草稿」的区分（抛域异常 or 返回哨兵） | `resolve_draft` 抛 `DraftResolutionError` ⇒ 上游 fail-closed（5xx / 不产出报告）；判别：临时库 `DROP TABLE drafts` ⇒ 期望 5xx 而非 100 分报告，且新用例突变验证必红。**当前状态**：Task 1 已实施（域异常 + `DRAFT_UNAVAILABLE` 占位符 + 门禁 `should_block` 硬停 + 9 用例 + 6 条突变规格），**待对抗审查 + Lead 复验** |
| **D-7** | **🔴 director 装配缓存键 5 处漏维度（第 6/7 次同形状）** | Lead 实测：改伏笔台账 / 改 director 侧章标题 ⇒ **不重装、payload 不变**（对照组「改项目前提」正确 miss）。缺 `hooks` / `narrative_debts` / `plot_events`+`branches` / `foreshadow_overdue_chapters` / director 侧 `chapters.title` | 同形状已复发多次，**只补键维度是治标**（写路径仍不推 `state_version`） | 补键维度 **+** 让 `domain/ledger` / `domain/plot` / `domain/project` / `domain/chapter` 的写路径推 `state_version`；判别：新用例断言打在 **payload 内容**上（照 `test_ctx_b_meta_title_key.py` 口径）+ 反向「未改必命中」不变量 |
| **D-8** | **批次三十的 CLI 失败退出码全仓无看守** | 把 `export_main`/`gen_types_main` 换成恒返回 0 的替身 ⇒ **8 条用例全绿**；`grep -rn '"--check"' tests/` 只命中两个只对**已同步产物**调用的辅助函数 | 未提交的工作树，顺手返工成本最低 | 加 1 条「CLI 对漂移副本返回非 0」用例 + 1 条打 CLI `return` 行的突变；顺带修 `l21` 规格「必红用例」表错行、`--check` 临时目录泄漏、CWD 相对默认路径 |
| **D-9** ✅ | **commit 风控门两条子句零可区分夹具 + 该文件无突变规格** | `_has_high_risk_change`（`pipeline_common.py:518`）的 `facet=="definition"` / `world_kind=="rule"`：workflow 侧**无任何夹具**用这两个值（带 `facet` 的夹具全是 `"state"`）；**Lead 插桩量化**：`tests/workflow`+`tests/api` 全量跑完该函数被调用 **41 次**，`facet=='definition'` **0 次**、`world_kind=='rule'` **0 次** ⇒ 删掉两条子句，41 次调用的判定结果**一次都不会变** ⇒ 无任何测试会红。另：`facet:"definition"` 只在 **API 侧孪生**（`deltas.py:122`，被 integration 测试钉住）⇒ 双实现、覆盖不对称。⚠️ 插桩坑：`commit.py` 是 `from .pipeline_common import _has_high_risk_change`（导入时绑定）⇒ 只补 `pipeline_common` 属性**拦不到**，必须同时补调用方模块 | 补夹具成本极低 | 补 2 个夹具（definition / world_kind=rule）⇒ 断言「命中即要人工批准」；配突变规格撤子句必红 |
| **D-10** 🟡 | **外发投稿包取「最新 draft」而非「已 commit 的那版」** | `exporter/builder.py:122-123` `ORDER BY version DESC LIMIT 1`；`commits` 表**无 `draft_version` 列**（全仓 `draft_version` 只在 `quality_reports`，迁移 0024）⇒ 章 reopen 后改稿，外发带未过审稿 | 需先给 `commits` 加列（迁移） | 迁移加 `commits.draft_version` ⇒ exporter 取已 commit 那版；判别：COMMITTED 后再改稿，外发包正文仍是旧版 |
| **D-11** | **`content_sync` 三条静默形态**（零突变覆盖） | ① 改标题后旧 txt **永久残留**（实测 `第1章-OLD_TITLE.txt` + `第1章-NEW_TITLE.txt` 并存，无清理逻辑）；② 缺 `volumes`/`characters` 表 ⇒ 整次 push 抛未捕获 `OperationalError`；③ `story_state/<v>.json` 按 v 存在即跳过 ⇒ 原地修过的快照永不回灌 | 需定义「旧文件清理」与「缺表降级」的产品口径 | 断言：改标题后 `novel/` 只含新文件（撤清理必红）；缺表时 push 不抛异常且 `errors` 非空 |
| **D-12** 🟡 | **删角色留孤儿 `relationships` 行** | 迁移 0022 移除 `relationships` 的 character FK + `character/service.py:308` 删角色不查引用 ⇒ 实测留 1 行孤儿，而 **`PRAGMA foreign_key_check` 报不出来** | 与 `world/service.py:544` 的处置不一致 | 删前查引用并抛 `ReferencedError`；判别：临时库建角色+关系 → 删角色 ⇒ 期望抛错 |
| **D-13** | **`signing_check` 读题材包失败 ⇒ 题材开篇整段静默消失** | `service.py:133-134` `except Exception: return None` ⇒ 无告警、无 errors 字段 ⇒ 报告外观「全通过」 | 同文件 `word_band_rejected` 已有显式标记 ⇒ 处置不一致 | 加 `genre_opening_unavailable: True` 标记；判别：注入读失败 ⇒ 报告必须显式标注不可检 |
| **D-14** ✅ | **`*.db.enc` 不被 gitignore 覆盖** | `git check-ignore -v backups/x.db.enc` ⇒ rc=1；`.gitignore:15` 只有 `*.db` | 条件性（需有人把 `--out` 指到仓内） | `.gitignore` 补 `*.db.enc` / `*.enc` |
| **D-15** ✅ | **AGENTS.md 硬规则 5 字面口径比实现绝对** | `genre_packs.payload_json` 含 `forbidden_words`(4) / `redlines`(3) / `taboo_notes`(88 字)，而 schema 明写「只承载机制可呈现的要点」 | 属措辞问题 | 把该句改成「审核红线**正文**只进内容仓，软件层只承载机制可消费子集」——**不要改实现** |
| **D-16** 🟡 | **版本一致性机器看守只覆盖 6 处中的 3 处** | `check_skill_package.py` 只校 pyproject / claude-plugin/plugin.json / SKILL.md；**web 三处（package.json / package-lock.json / uv.lock）全仓 0 命中** | 与批次十九「SKILL.md 静默停在 3.9.0」同族 | 补齐 web 三处；判别：任一处改错 ⇒ 检查红 |
| **D-17** 🟡 | **嵌套 pytest 继承本机默认 basetemp ⇒ 本地基线不可复现** | 本机 `%TEMP%\pytest-of-AiStation` 一旦创建即**连 owner 都拒绝访问**（`icacls` 拒绝列举）；本仓两处测试会**再起 pytest 子进程**且不隔离临时目录 ⇒ 子进程 exit 1 ⇒ 外层红。实测：全量 **3900 passed / 2 failed**（CHANGELOG 批次三十声称 3902 passed / 0 failed） | 环境缺陷，但**暴露了测试非自足**；CI 在 Linux 上看不见 | 两处子进程 spawn 加 `--basetemp`；判别：全量 0 failed 且不依赖默认临时目录 |
| **D-18** | **活库仍有明文 api_key，`secrets.json` 条目悬空** | 活库 `model_profiles` **2 行**带非掩码真值 api_key；`model_connections` 0 行；`secrets.json` 唯一条目 `mprof_f95e3c7b4127` 与两个现存档案 id **均不匹配** ⇒ 解析链落到第 4 级 `params_json["api_key"]` | 需改库内数据（属用户凭据）⇒ 需用户确认 | 把两个档案的 key 迁到 `secrets.json` 对应键并清库内明文 + 加「悬空条目启动告警」；判别：库内明文计数 = 0 且解析仍返回非空 |
| **D-19** ✅ | **`NOVELOS_DEBUG_PROVIDER_DUMP_DIR` 落盘目录不在 gitignore 覆盖内** | 该 env 会把 LLM 响应全文（=章节正文）落盘；`git check-ignore` 对非 `data/`/`tmp/` 路径返回 NOT-IGNORED | 低频 env，但一旦开就是正文泄漏入口 | `.gitignore` 补 `provider_dumps/` 或脚本拒写仓库根下非 `data/`/`tmp/` 路径 |

---

## 四之二、本轮核销台账（2026-10-23 修复批 · 第二轮审查修复）

> 标记口径（**声称与实测分离**，AGENTS 纪律 2）：
> - ✅ **已核销** = Lead **亲自**读码/跑证据验证过，且改动已在工作树；
> - 🟡 **已修待复核** = 子代理已交付改动，但**尚待对抗审查 + Lead 复验**才可核销；
> - 未标记 = 未开始。
>
> ⚠️ **台账条目「核销」≠ 已提交**：本批全部改动**尚未提交**（收尾闸门六步未跑完），
> 故此刻核销只代表「机制已落地并被验证」，不代表「已入库」。

### ✅ 已核销（Lead 亲验）

| # | 落地物 | 证据 |
|---|---|---|
| **D-0** | 内容仓 push 护栏：`D:/zcodeproject/NovelOS-Content` 装 `.git/hooks/pre-push`（自检 exit 1 + 中文拒绝说明 + 三步解除命令写在注释里）+ `git remote set-url --add --push origin no-push://novelos-content-push-disabled` | `git push --dry-run origin master` 实测 `fatal: remote helper 'no-push' aborted session`；钩子自检 rc=1 |
| **D-1** | 软件仓 `git rm` 8 个含真实 id/正文的 `docs/evaluation/` 文件 → 复制进痕迹仓 `D:/zcodeproject/NovelOS-Artifacts/docs/evaluation/`（痕迹仓已提交 `996255d`，**无 remote**）；新建 `docs/evaluation/README.md`；`.gitignore` 防回流 | `git ls-files docs/evaluation/` 已无真实 id 文件；痕迹仓 `git remote -v` 空 |
| **D-9** | `tests/unit/test_commit_high_risk_facets.py`（**11 用例，三层**：L1 判定函数 → L2 `_inject_validate_node` 写入 `needs_high_risk_approval` → L3 `_commit_node` 未批准即拒收）+ `tests/mutation_specs/d9_commit_high_risk_facets.py`（2 条：撤 `facet=="definition"` / 撤 `world_kind=="rule"`） | 本机 `pytest` **11 passed**；两条锚点 `str.count` = 1。⚠️ **突变实跑待收尾闸门**（执行器禁与并发读写冲突） |
| **D-12（路由侧）** | `packages/core/api/routers/characters.py::delete_character` 补 `except ReferencedError → HTTPException(409)`（原会冒泡成 **500**）+ `packages/domain/character/__init__.py` 导出 `ReferencedError` + `tests/api/test_character_delete_conflict.py`（3 用例，含反向对照）+ 突变规格 `d12_character_delete_route_409.py` | `pytest tests/api/test_character_delete_conflict.py` **3 passed** |
| **D-14** | `.gitignore` 补 `*.db.enc` / `*.tar.enc` | `git check-ignore -v backups/x.db.enc` ⇒ 命中 |
| **D-15** | `AGENTS.md` 硬规则 5 措辞：改「审核红线**正文**只进内容仓；软件层只承载**机制可消费子集**」+ D-15 订正留痕（**只改措辞不改实现**） | 已核 schema：`genre-pack.schema.json` 的 `taboo_notes.description` 自带该边界；`pacing.redlines` / `StyleConstraints.forbidden_words` / `CriticRubric.taboo_notes` 三段结构均条目化 |
| **D-19** | `.gitignore` 补 `provider_dumps/` | `git check-ignore -v` 命中 |

### 🟡 已修待复核（子代理已交付，待对抗审查 + Lead 复验）

| # | 交付物 | 待验点 |
|---|---|---|
| **D-5** | Task 4：xdist/cov 移进 `[project.optional-dependencies].dev`，**删除 `[dependency-groups]`**；`ci.yml` windows job 加 xdist 自检 | ⚠️ 删除分组的代价（裸 `uv sync` 连 xdist 一起剪）与 CI 实跑结果**均未直接观测**（GitHub job 日志需 admin） |
| **D-12（域层）** | Task 7：`character/service.py::delete` 查 `relationships` **两端点** ⇒ 抛 `ReferencedError` 且不删任何东西 | Lead 裁决保留「继承 world 的 `ReferencedError`」⇒ 引入**本仓首条 domain→domain 依赖**，须在 AGENTS 留痕；孤儿**存量**未清（只防新孤儿） |
| **D-16** | Task 4：`check_skill_package.py` 补 web 三处版本检查（检查项 49 → 52）+ `tests/api/test_frontend_version_consistency.py` | 子代理自报 `--selftest` 覆盖不到 web 三处（坏副本无 `apps/web`/`uv.lock`）⇒ 该三项靠真实仓库 baseline + pytest 守 |
| **D-17** | Task 4：两处嵌套 pytest 子进程自带唯一 `--basetemp` | 本机 **2 failed → 0**；⚠️ 该突变**依赖本机那个拒绝访问的临时根目录**，换机器不会红 |

### 🟡 部分完成

| # | 已完成 | 未完成 |
|---|---|---|
| **D-10** | 写路径全通：迁移 `0034_commits_draft_version.sql`（`commits` 加可空列 `draft_version`，**不回填**）+ `commit_delta(..., draft_version=)` + service wrapper + `commit.py` 传 `ctx["quality_reviewed_draft_version"]`（该键此前**只写不读**）；测试 `tests/unit/test_commits_draft_version.py` **25 passed** + 突变规格 2 条 | **导出侧接线在 Task 6**（`packages/core/exporter/builder.py`，该文件属 Task 6 所有权）⇒ 本条**未核销** |

### 裁决留痕（先验判断被推翻）

1. **D-10 口径**：Task 6 曾自创「checkpoint 解析 + `commits.timestamp` 回溯 + 定位不出就产出『需人工确认』且正文不外发」三档口径 ⇒ Lead **逐条否掉**：
   - 时间戳回溯是**猜** ⇒ 会把「审完又改稿」的那一版误认成定稿版（正是要修的缺陷本身）；
   - 「定位不出就外发瘫」⇒ 存量 commits 行该列恒 NULL ⇒ **所有存量章外发全瘫**（**存量零退化**是硬要求）；
   - checkpoint 是 **run 级**的 ⇒ 与 commits 行不是一一对应 ⇒ 语义弱于 `commits.draft_version`。
2. **D-10 前提被实测推翻**：台账原文「章 reopen 后改稿，外发带未过审稿」隐含「reopen 后章仍是 COMMITTED」⇒ **实测不成立**：`POST /chapters/{id}/reopen` 把章改成 `REVIEWED`（`routers/chapters.py:267`）⇒ 未定稿章的外发正文**带【未定稿】标记**，非静默外发 ⇒ 最终口径收口为「章仍 COMMITTED 才取定稿版」。
3. **D-12 错误族**：Task 7 提出取舍 ⇒ Lead 裁决**保留**「character 域 `ReferencedError` 继承 world 域那个类」（跨域同义 ⇒ 同一类型；不再添第三个同名分叉；域→域 import 无成文禁令），代价（首条 domain→domain 依赖）已知并接受。

---

## 四之三、第二轮**对抗审查** findings（2026-10-23 晚 · 2 名专职只读审查者）

> 两名审查者**只读**、各自独立复算，结论一律当**待验证假设**；Lead 对 critical/major **逐条亲验**后才入册。
> 报告全文：审查者 A（33 文件）结论见本节摘要；审查者 B 的 864 行全文见
> `docs/impl/reviews/adversarial-review-b-2026-10-23.md`。
> **打回环**：findings ⇒ Lead 逐条裁决 ⇒ 按**文件所有权**路由回**原实现者**（保上下文 + 保所有权）
> ⇒ Lead 复验 ⇒ 追加给**同一审查者**复审。裁决依据只有一条：**自己复算**，不看是谁说的。

### 裁决表（Lead 亲验后定性）

| 来源 | finding | 定性 | 处置 |
|---|---|---|---|
| A·B1 | `test_migrations.py` 第 4 处硬编码迁移清单漏 `0034` ⇒ 红 | **major** | ✅ **Lead 自修**：改成与 `database/migrations/` **磁盘目录动态对账**；人工注册表收敛为 `applied` 那**一份**（加迁移只改一处，漏改会被它抓住） |
| A·C1 | `test_commit_high_risk_facets.py` 与 `d9` 规格称「core 侧 `deltas.py` **不认** `world_kind=="rule"`、覆盖不对称」 | **major** | ✅ **Lead 自修**：**实测证伪**——core 侧 `deltas.py:127-131` **也认**；两份实现**对称**。错误论据会诱导后人「对齐」core 侧 ⇒ **恰好造出真门禁缺口**。已改事实陈述 + **补 core 侧断言**（L4 层） |
| A·C2 | L3 的 world 变体**零判别力**（`_commit_node` 只读 ctx 标志，不看 payload） | minor | ✅ **Lead 自修**：L3 的 ctx 标志改由 payload **现场推导** ⇒ 撤子句即红 |
| A·T1-1 | 「质量不可检」API 契约用 `in (201, 500)` 弱断言 ⇒ 退化成裸 500 也全绿 | **major** | 🔁 打回 **Task 1**（改强断言；若保留 500 支须**单独一条**并断言错误文本含关键词） |
| A·T1-3 | report 模式（快通道）下不可检**也写库** `revision_note` + 给**误导性建议**（「补全 plan_json」） | minor | 🔁 打回 **Task 1**（不可检单独错误类别，**不写** quality 出路） |
| A·T1-2 | 「201 + overall=0」**不是新信号**——该端点 `overall` 因 `delta={}` 恒为 0（改造前即如此） | minor | 📌 **登记**（唯一新增信号 = `issues_json` 含 `scoring_missing_subscore`） |
| A·T1-4 | `scoring_missing_subscore` 现覆盖两类语义（子分缺失 / 正文读不到），只能靠 message 判别 | minor | 📌 **登记**（`formula_hash` 不变成立 ⇒ 不新增 rule_id） |
| A·T2-1 | 台账类写路径推 `state_version` ⇒ `story_states` **同内容新版本行** ⇒ 版本号与 commits **不再一一对应** | minor | 📌 **登记**（方向安全＝多失效而非少失效；无消费方被打破） |
| A·T2-2 | 乐观锁交互变化：observer→commit 之间被他人改动 ⇒ 原本不可能的提交失败现在会出现 | minor | 📌 **登记**（**安全侧**，错误文案即真因；commit 流程**不自触发**推进） |
| A·T2-3 | 子代理自报「+4ms/+64%」性能数**未实测** | minor | 📌 **登记**（审查者实测小库 +1.05ms/+13.7%；真实规模待测） |
| A·T2-5 | `branches` 写路径未推 `state_version` | **不成立** | ❌ **不补**：`plot_fp` 已覆盖 `branches` 表 ⇒ 建分支必 miss（实测 payload 变 + 重装）⇒ 加 version 推进是**纯过度失效** |
| A·T3-1 | `restore_db.py` 目标被占用时抛未捕获 `PermissionError` ⇒ rc=1 + traceback，违反自己承诺的 rc=2 | **major** | 🔁 打回 **Task 3** |
| A·T3-2 | 备份自检失败把**含明文 API 密钥**的整库留在 `%TEMP%` 且无限期保留 | **major** | 🔁 打回 **Task 3**（默认删；保留做成显式 flag） |
| A·T3-3 | 删 `-wal` 早于原子替换 ⇒ POSIX 上留「原库在、WAL 已删」窄窗口 | minor | 🔁 打回 **Task 3**（先 `PRAGMA wal_checkpoint(TRUNCATE)`） |
| A·T3-4 | `--expect-tables` 默认写死 43 ⇒ 与 `db.py` 的 A-009「不写死张数」直接冲突 | minor | 🔁 打回 **Task 3**（默认 None） |
| A·T3-5 | 存量泄漏临时目录：子代理自报 240，审查者实测 **260** | minor | 📌 **登记**（数字不可复算，不当事实用；新实现已不再泄漏） |
| A·A1 | CI 把 pip 钉在带 6 条已知公告的 `25.3` | minor | 🔁 打回 **Task 4**（**先自查 PyPI 再改**，不许写没核实的版本号） |
| A·A2 | web 三处新版本看守**没进 `--selftest`** ⇒ CI 上「跑而不验」 | minor | 🔁 打回 **Task 4**（补 M9） |
| A·A3 | `test_volume_scope_cache_key.py` 失败文案仍写「18 元」 | minor | ✅ **Lead 自修**（改引常量 `DIRECTOR_KEY_LEN`） |
| A·B2 | API 手工提交路径不传 `draft_version` ⇒ 该路径 `commits.draft_version` 恒 NULL | minor | ✅ **Lead 自修**（端点收下可选 `draft_version` 并透传；非整数 → 422；**配了 API 级测试**，因 AGENTS 硬规则 9 要求「端点收下的参数必须有消费方测试」） |
| A·B3 | `d10` 规格「互不串味」措辞与实测不符（各红 2 条而非 1 条） | minor | ✅ **Lead 自修**（改口径：「各守一个**机制**，一条突变可能打红多条用例」） |
| A·B4 | gate 的 `ctx["draft_version"]`（**可由 API 请求体指定**）优先于最近人审版本 ⇒ 「量哪一版」的选择权被传导到产物面 | minor | 📌 **登记，机制自洽不改**（Lead 复算）：① `commits.draft_version` 记的就是**门禁实际量的那一版**（`gate.py:649` 写 `report.draft_version`）⇒ **导出物 == 被门禁量过的文本**，不变量成立；② 该优先级**在 D-10 之前就存在**（那时导出恒取最新，同样可能与门禁量的不一致）⇒ 非 D-10 引入；③ 改成「人审版本优先」会造出**新的**不一致（写→审→改稿→再提交时 delta 按新稿算，改去量旧稿 ⇒ 量的文本与 delta 来源脱节）⇒ 已在 `commit.py::_reviewed_draft_version_of` 的 docstring 留痕 |
| A·D1 | `character/service.py` docstring 称「路由一个 except 覆盖两域、isinstance 恒真」 | minor | 🔁 打回 **Task 7**（当前实现是**更安全**的窄捕获，错的是文档） |
| A·D2 | `character/service.py::__all__` 未含 `ReferencedError` | minor | 🔁 打回 **Task 7** |
| B·A1 | 分层看守的 `core→domain` 边**只守静态 import** ⇒ 动态 import（`importlib.import_module("packages.domain...")`）四个扫描器全放行 | **major** | 🔁 打回 **Task 7**（动态扫描目标泛化成前缀表 + 补负向探针） |
| B·A2 | 相对 import 逃逸扫描器**只跑 core** ⇒ `domain → workflows` 可用 `from ...workflows... import` 绕过 | **major** | 🔁 打回 **Task 7**（`GUARDED_ROOTS` 一并喂给逃逸扫描器） |
| B·A3 | world 域同款孤儿洞：删 faction 不查 `relationships` ⇒ 孤儿行且 `foreign_key_check` **报不出** | **major** | 🔁 打回 **Task 7**（补两端点检查；抛错零删除） |
| B·A4 | **存量孤儿**无检测无清理路径（FK 已被 0022 摘掉 ⇒ 连发现手段都没有） | **major** | 🔁 打回 **Task 7**（加**只读诊断**；**清理迁移涉及活库数据 ⇒ 需用户授权，本批不做**） |
| B·A6 | `ALLOWED_CORE_TO_DOMAIN_IMPORTS` 白名单**无字面量 pin**（另两个白名单有）⇒ 内容可被悄悄换掉 | minor | 🔁 打回 **Task 7** |
| B·A7 | 首条 domain→domain 依赖已引入，但分层方向表**没有 domain→domain 这条边** | minor | 🔁 打回 **Task 7**（加方向边 + 精确白名单） |
| B·B1 | `_artifacts_root()` 的**最后一档回退就是软件仓自身** ⇒ 正是要防的落点（痕迹仓无 remote ⇒ clone 者必然把痕迹写回软件仓） | **major** | 🔁 打回 **运维成员**（末档改「拒绝写 + stderr 警告」+ 显式逃生口） |
| B·B2 | `eval_regression.py` 默认 `--baseline` 仍指向软件仓，而基线已被移走 ⇒ **每次运行都「无基线 → 建基线 → PASS」**（首次漂移被静默吸收） | **major** | 🔁 打回 **运维成员**（默认指 artifacts 根；痕迹仓缺失时**拒绝建基线**） |
| B·B3 | `docs/evaluation/README.md` 写「70+ 个 `run_*.json`」，实测 **68** | minor | 🔁 打回 **运维成员**（改成 68 或「60+」） |
| B·B4 | `.gitignore` 新规则与既有 `data/` 规则重叠（冗余但非缺陷） | minor | 🔁 打回 **运维成员**（加注释说明，**不删规则**） |
| B·C1 | 内容仓 push 护栏注释里的**解除命令第 2 步是无效命令**（`set-url --delete --push` 不带 URL 参数 ⇒ exit **129**）⇒ 照跑会停在「钩子已删、pushurl 还在」的**半开状态**（比原状更危险） | **major** | ✅ **Lead 自修**：**删掉第 2 步**（第 3 步本身即可替换 pushurl）+ 注释留痕 |
| B·C2 | 钩子注释称「`--no-verify` 也推不出去」**不准确**（实测 `--no-verify` + **显式 URL** ⇒ 两层同时绕过、EXIT=0 真推） | minor | ✅ **Lead 自修**：注释改为「**防误操作护栏，不是安全边界**」+ 写清两层都活在 `.git/` 下（换机即失效） |
| B·C3 | D-15 只改 AGENTS.md 一处，**实际消费方** `core/genre/consumers.py:17` 仍是绝对口径 | minor | ✅ **Lead 自修**（consumers.py 同步订正 + roadmap 两处加「历史注记」横幅） |
| B·C4 | AGENTS.md 称 `critic_rubric.taboo_notes`「条目化」**不准确**（实测是 ≤2000 字**自由文本**） | minor | ✅ **Lead 自修**（改「条目化的 forbidden_words / pacing.redlines + **文本化的** taboo_notes」） |
| B·T1 | 章节页「取消勾选」改发 `fast_mode: null` ⇒ 对 NULL 项目 **UI 永远写不出「显式关」**；且 `NOVELOS_FAST_MODE=1` 时「关掉」**不生效**（后端仍在快通道）⇒ **不安全方向的假承诺** | **major** | 🔁 打回 **Task 5**（点「关」= 显式关 ⇒ 发 `false`；两入口都要能发；新增测试曾把该能力损失**锁死**） |
| B·T3 | 前后端口径不一致 = 「**安全闸门被跳过而作者不知情**」（env=1 时前端显示未勾选、后端已跳过审校） | **major** | 🔁 打回 **Task 5**（加**只读「已解析有效值」出口**，复用 `resolve_fast_mode` 权威口径） |
| B·T4 | ① 快通道关掉后 `autoCommit` 仍 true ⇒「已勾选 + 禁用」；② **开快通道会自动勾上「跳过审校与审批」**且无提示 | minor | 🔁 打回 **Task 5**（Lead 裁决：**取消自动勾选**，用户须显式勾选跳过审批） |
| B·T7 | `GET /runs/null` 空 id 首拉（`selectedRunId as string` 是类型谎言 ⇒ 必然 404，且误导性详情错误） | minor | 🔁 打回 **Task 5**（空 id 不请求 + 改掉把 bug 写进夹具的断言） |
| B·T8 | 「`ChapterDetailPage.tsx` 已 3418 行」前提**不成立**（源文件 1135 行；3418 是**测试**文件，本批涨到 3689） | minor | 📌 **登记**（源文件未破 1500 线；测试文件拆分**另立项**，须守 patch 契约纪律） |
| Task6·c2 | 「不可检」标记进不了交付判定（`delivery/evidence.py` 丢弃无章号项目级 item + `verdict.py` 对 info 级不产生理由 ⇒ 章仍判 `deliverable`） | — | 🔁 打回 **Task 6**（不可检 ⇒ **不可判为可交付**） |
| Task6·c3 | 两个新键进不了 API 响应顶层（`SigningCheckResult` 是写死字段模型） | — | 🔁 打回 **Task 6**（加进响应模型 + 断言顶层真有） |
| Task6·c4 | 旧正文清理会删**作者手写文件**（如 `第99章-手记.txt`）⇒ **破坏性** | — | 🔁 打回 **Task 6**（Lead 裁决：**无法证明是工具产出的文件一律不删**；判定不了的**移入 quarantine**，不物理删除） |

### 审查者攻不倒 / 明确登记为「不是缺陷」

- **A·PASS**：Task 4 的 `pip install -e ".[dev]"` 真装得上 xdist（构建 wheel 元数据核实 `Provides-Extra: dev`）；`uv lock --check` 绿；web 三处版本检查**非恒真**（改成恒真 ⇒ 2 条红）。
- **A·T1-5/T1-6**：Task 1 的两处独立调用点**都真 fail-closed**（`delivery/evidence.py` 降级留痕；`chapter_review/pipeline.py` 本就硬失败）；空/空白正文**仍是 100 分 0 issue**（既有行为逐字未变）；l22 六条突变**全部真红**。
- **A·T2-6**：缓存键 5 维度齐全、`title_fp` 与 writer 侧**同源同值**；9 条突变**每条只红 1 条**（比子代理自报的「红 4 条」更精确）。
- **A·T3-6/T3-7**：备份链四种失败路径**目标 sha256 逐位不变**、退出码 3/2/5 可分、成功路径原子替换、**陈旧 `-wal` 场景恢复出正确库**。
- **B·第 3 节**：删角色的引用守卫**攻不破**（两端点都查、抛错前零删除、突变 3 条全杀）；44 条白名单**逐条真精确**；模块图 2 环/3 环 = 0；痕迹仓确无 remote；`.gitignore` 9 条规则逐条命中。
- **B·T2**：章节页与项目弹窗的**库终态逐格相同**（5 格全验）——成立的是「同为 NULL」，不是「同为显式关」。
- **B·T9**：Task 5 的三条前端突变**全部能红**；新测试**不是自证**（`expect(true)`/`toBeTruthy()` 计数全为 0）。

### 收尾闸门里挖出的 4 条根因（全量 pytest 的 7 红 ⇒ 4 根因，**都不是生产代码缺陷**）

| 根因 | 表现 | 处置 |
|---|---|---|
| **替身签名过期**（3 条红） | D-10 给 `commit_delta` 加了 `draft_version` kwarg ⇒ 三处测试替身（`test_ctx_b_commit_cache_invalidate_warning.py::_fake_commit_delta`、`test_v2_wave_c_retrieval.py::fake_commit`）报 `TypeError: got an unexpected keyword argument 'draft_version'` | 替身改 `**kwargs` 吸收新 kwarg。⚠️ **这正是「替身须复刻生产关键副作用时序」的同族**：替身漏一个 kwarg ⇒ 红的是**替身过期**、不是生产行为变了 ⇒ 排查时先看是不是这类，别急着改生产代码 |
| **硬编码键长过期**（1 条红） | `test_skill_injection.py` 写死 `director 键应为 18 元`（实得 23） | 改成引常量 `DIRECTOR_KEY_LEN`（新增 + 沿革注释）。⚠️ 该常量**全仓三份**（另两处 `test_prompt_label_key.py` / `test_volume_scope_cache_key.py`）⇒ 「手写清单漂移」的**第三处**复发点，已在注释里点名 |
| **响应契约键集过期**（2 条红） | D-13 给 `SigningCheckResult` 加了 `genre_opening_unavailable` / `genre_opening_error` 两个**条件键** ⇒ 契约测试的硬编码键集少了它们 | 补进 `EXPECTED_SIGNING_CHECK_CONDITIONAL_KEYS`（条件键 ⇒ 未触发时**整个键缺席**，不是 null） |
| **量具漂移：命中路径 SQL 11 → 23**（1 条红） | director 补 5 个指纹维度 ⇒ 命中路径业务 SQL 11 → 23（**连接数不变，仍 2 条**）。Lead 用 `sqlite3` trace 逐条量过：peek 1 + prompt_label 1 + 指纹 SELECT 11 + **`PRAGMA table_info` 存在性探测 10** | 改量具断言为 23 + 如实构成注释。💡 **为什么不做进程级「表存在性」缓存**（能省那 10 条）：表集合由迁移链固定，正常路径上缓存是对的；但**「表后来才出现」**（换夹具 / 手工建表）会让缓存里的「不在」继续生效 ⇒ **指纹陈旧 ⇒ 脏命中**——正是本仓反复登记的形状 ⇒ **不做**（方向安全 > 省 10 条 SELECT）。真实规模（48 章书）的耗时**仍待测**（小库实测 +1.05ms/+13.7%，审查者 A 测的） |

### 一条「审查过但不是缺陷」的结论：writer / director 的 SQL 差不是漏维度

Lead 复核时注意到：director 命中路径 23 条 SQL **多于** writer 的 15 条，而 M-1 的原始描述是
「director 缺 5 个维度、writer 侧有」⇒ 直觉上像「writer 侧也有同样的洞」。

**Lead 实测结论：两侧都完整，没有一侧漏。** 差异来自**取数来源不同**：
- **director 直读表**（伏笔台账 / 叙事债务 / 情节图 / 分支 / 项目逾期阈值 / 章标题）
  ⇒ 每个来源都要自己的「表在不在」探测 + 查询 ⇒ SQL 多；
- **writer 读快照**（伏笔/情节内容来自 story state 快照）⇒ 靠键里的 **`state_version`**
  感知变化，而本批已让**台账/情节写路径推 `state_version`** ⇒ 不必逐表探测。

⇒ Task 2 写的 `test_writer_key_unaffected_by_overdue_threshold_change`（断言 writer 键
**不受**逾期阈值影响）是**有意的**，不是漏维度。⇒ 已在 `test_context_peek_convergence.py`
的 docstring 里把「director 比 writer 少 4 条 SQL」这句**已失效**的交叉引用改成实测口径。

### 先验判断被推翻（新增，本轮）
1. **「core 侧不认 `world_kind=="rule"`」是错的**（A·C1）——实测 `deltas.py:127-131` **认** ⇒ 两份实现对称。**危害不对称**：照错误论据「对齐」core 侧会**造出真门禁缺口**（`commits.py` 的 HIGH 风险门不再拦世界规则变更 ⇒ 走 API 可绕过人工审批）。
2. **「`branches` 漏推 `state_version`」不是缺口**（A·T2-5）——`plot_fp` 已覆盖 `branches` 表 ⇒ 建分支必 miss；补推进是**纯过度失效**。
3. **「两处绕过 `build_quality_context` 的调用点是 fail-open」不成立**（上一轮已记）⇒ C-1 由 critical 降 major；真实受益点收窄为那两处。
4. **子代理自报数字不可直接入册**：泄漏目录 240（实测 **260**）；缓存键性能「+4ms/+64%」（**未实测**，实测小库 +1.05ms/+13.7%）。
5. **「批量模板手写清单必然漂」第二次复发**（迁移清单 0034 漏改）⇒ 改为**磁盘动态对账**。
6. **`GET /commits` 列表行键数 12 → 13**（0034 加列 ⇒ `SELECT *` 多一键）——Lead 自修时**实测发现**的第二处红；模型 `CommitListItem` 与契约测试同步补齐（并暴露「`extra="allow"` 但模型不声明 ⇒ OpenAPI `properties` 会缺键」这条坑）。

---

## 四之四、用户报「新增章节 → 生成计划 → 章节名不生成」（2026-10-23 晚 · Lead 自办）

> 用户原话：「原计划只有 10 章，结果后面需要新增章节，按软件的『新增章节』后，再进去『生成计划』，
> 此章节的章节名不会自动生成，这个问题也安排修复一下。」
> **零真实 LLM、零内容仓接触**；活库只写 `agents` / `prompts` 两张机制表。

### 根因（实测链，五环）

| 环 | 实测 |
|---|---|
| 运行时取哪份提示词 | `runner.run_agent` → `PromptRegistry.get_active_prompt()` → **读 `prompts` 表**，不是读 `docs/agents/prompts/*.md` |
| 活库现状 | `director_planner` **只有 v1 / v2**，ACTIVE = v2（content 与仓内 v2 文件逐字一致） |
| v2 契约 | **§7 无顶层 `title`**（v2 / v3 都无，只有 v4 有）⇒ 模型**不可能**产出该键 |
| 落库侧 | 回填代码在，但取值恒 `None` ⇒ **回填的 UPDATE 根本不执行** |
| 为何停在 v2 | 镜像**只在服务启动时同步一次** ⇒ **长驻服务不重启 ⇒ 仓内新提示词永远到不了运行时** |

⇒ 批次二十五的 **v4 提示词 + 回填代码早已在仓内，却从未到达运行时**；且失败**零告警**。

### 已修

1. **提示词自愈**（`PromptRegistry.ensure_agent_prompt_current()` + `runner.run_agent` 调用）：
   忠实镜像守卫（DB 当前 ACTIVE 行须与仓内**同名同版本**文件**逐字一致**）⇒ 是则推进到仓内最新版本；
   不一致（人工改过 / 测试库自造）⇒ **一律不动**；单调（绝不降级）；推进后**重新解析真实生效版本**，
   人工停用的版本**不偷偷启用**；`NOVELOS_PROMPT_SYNC=off` 与启动 sync 两端同关。
2. **回填可观测**：`title_backfill` 进节点输出（`planner` / `author_kept` / `unavailable` / `not_applied`
   + 本次提示词版本），`unavailable` 打 WARNING ⇒ **失败不再静默**。
3. **能力前提看守**：「**生效版**提示词 §7 必须声明顶层 `title`」——升版丢契约 ⇒ 测试变红。
4. **活库已推进**：`director_planner` 现 v1~v4 / ACTIVE = **v4** ⇒ 用户下一次「生成计划」即得章名
   （提示词每次调用现读 DB，**无需重启**）；已跑过计划的第 11 章**需重跑一次计划**。

### 附带：突变量具的两处缺陷（本轮闸门 40 条 INVALID 的真因）

| 量具缺陷 | 后果 | 修法 |
|---|---|---|
| 调 pytest **没传 `--basetemp`** | 本机 `%TEMP%\pytest-of-<user>` 连 owner 也拒绝访问 ⇒ `tmp_path` 用例在**准备阶段** ERROR ⇒ 40 条被判 INVALID | 每条突变独立可写 `--basetemp`（父目录先建——pytest 内部 `mkdir` 不带 parents） |
| 把「用例 ERROR」当「用例 FAIL」 | 同文件里另一个用例 ` passed` 就能顶替 ⇒ **假 CAUGHT**（本轮 SH-3 即是） | 退出码 1 时必须见 ` failed` 才算 CAUGHT；只有 errors ⇒ INVALID |

⇒ 修后同一批：**129 CAUGHT / 0 存活 / 0 中止 / 0 无效**，且**每条红的是它该红的那条**。

### 本轮先验判断被推翻

1. **「回填代码没生效是因为前端塞了默认标题」——不成立**：前端「新建章节」传的是 `title.trim() || null`
   ⇒ 库里 `title` 是 NULL；问题不在前端。
2. **「占位名兜底（第 11 章）是对的修法」——撤回**：既有设计决策（已被测试钉住）是「planner 没给名 ⇒ 不碰列」，
   且章节列表页**已有「第 N 章」列** ⇒ 兜底会视觉重复。⇒ 改为**保证提示词新鲜 + 失败可见**。
3. **「提示词契约能提供章名」——当时不成立**：生效版是 v2，§7 无该键 ⇒ 模型**结构上**产不出来。

### 登记（不做但留痕）

- **已跑过计划的那一章要重跑「生成计划」**才有名字（不回填历史）。
- **不造占位名**；章名空着时列表页仍显示「（未命名）」，作者可自行填写。

---

## 五、跨批留痕

- **Lead 先验判断被推翻 3 次**（均已入 CHANGELOG/AGENTS）：title 契约（at `director_planner-v3.md:108` 是输入契约）、行数 384（量具失真，真值 577）、审计 A 的 5 条断链（全误报）。
- **流程违规 1 次（Lead 自报）**：开工前未经用户批准 `push` 批次十九~二十三 6 个提交；此后 **push 一律先问用户**（当前批次 24~28 全部只提交、未推送）。
- **能力/口径边界（须持续遵守）**：零真实 LLM 调用（故 v4 起名质量、实时 prompt 行为均未验证）；零内容仓接触；突变实跑与全量 pytest 必须由 Lead 串行（成员只跑 precheck / 定向）。
