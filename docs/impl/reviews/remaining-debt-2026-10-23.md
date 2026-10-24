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
| C-1 | **13 个脚本仍零突变规格**（批次三十二 · task-3 已销 1 项） | ⚠️ **数字已被第二轮复算订正（2026-10-23 晚）**：`scripts/` 共 **32** 个 .py ⇒ **19** 有规格 / **13** 零规格（旧数 12/19 的分母 31 也不对；原 14 零规格中的 `produce_chapters` 已于批次三十二补上 `l25_scripts_produce_chapters.py` 的 `SC-PRODUCE-REPETITION`，定向实跑 `[CAUGHT]`，Lead 独立复跑亦 CAUGHT；全仓规格现 **85 文件 / 477 条**）。零规格清单（13）：`ai_pattern_recall` / `ai_tone_calibrate` / **`export_openapi`** / **`gen_frontend_types`** / `human_baseline_synthetic` / `inspect_stalled_runs` / `m2_judge` / `migrate` / `outline_check` / `readability_audit` / `real_llm_e2e` / `serve` / `smoke_e2e`。⚠️ 注意：批次三十的 `l21` 只打 `scripts/_codegen_check.py`，**没有打这两个脚本本身** ⇒ 它们仍在零覆盖清单里 | 逐个补成本高；本轮先修了 4 条 major | 按「改错会静默」排序：backup_db > content_sync > open_volume > produce_chapters > smoke_e2e > inspect_stalled_runs > export_openapi/gen_frontend_types |
| C-2 | **✅ 已修（批次（三十））**：两脚本各加 `--check`（全文级、换行归一、绝不写盘）+ `tests/api/test_codegen_artifacts.py` 8 例 + `l21_codegen_check.py` 3 条突变（3/3 CAUGHT） | 原状：两脚本有 `--out` 无 `--check`。**边界须精确**：既有 `tests/api/test_openapi_contract.py` 有**集合级**看守（`info.version` / `paths` / schema **名集合**）；实测把 `AdoptRequest.content` 的 `type` 由 `string` 改 `number`（名字集合不变）后那三条**全部通过** ⇒ **字段级漂移此前无看守**。CI 仍不跑（归 C-5，需批准） | 生成内容（字段类型/可选性）漂移需新判别 | 已完成；余下「CI 接线」见 C-5（未动） |
| C-3 | **✅ 已修（批次二十九）**：`eval_regression.py` / `m1_report.py` 默认改**系统临时目录**，`--write-docs` 才写仓库 | 2026-10-23 晚复算：两脚本已改（`eval_regression.py:76-88`、`m1_report.py:781-786`）⇒ **本行是 stale（已修未销）**，批次二十九提交 `71aa28d` 即落地 | — | 已完成；判别：默认跑一次后 `git status` 干净 |
| C-4 | **不可逆脚本无回滚快照**（`open_volume` / `world_switch` 靠幂等续跑） | 两处源码自注「中断点前已落库」 | 加事务化/快照是大改 | 若要改：先做「中断注入测试」证明当前会留下半态 |
| C-5 | **全量突变套件仍未接 CI**（只有只读预检接了） | **盘点已完成（批次三十二 · task-3），`ci.yml` 未动**：规模 = **85 规格 / 477 条**（目标测试 pytest **469** + vitest **8**）；ubuntu backend job **没有 vitest** ⇒ 那 8 条会 `INVALID`（响亮失败、不会假绿）。⚠️ **平台障碍订正：是 2 条不是 4 条**——`l0_pid_alive.py` 只有 `L0-ALIVE-1/2` 的锚点落在 `if sys.platform == "win32":` 分支里（Linux 上属**等价突变** ⇒ 判 SURVIVED ⇒ CI 假红），`L0-ALIVE-3/4` 是 POSIX 侧、Linux 可跑（**读码推断，未在 Linux 实测**）；`l22_backup_chain.py` 经复核**无**平台障碍（承重用例不在 `test_restore_db.py` 的 skipif 上）。 | ⚠️ **前置缺口（接线前必须先补）**：执行器目前**不支持「按突变 / 按规格排除」** ⇒ 无法排除那 8 条 vitest + 2 条平台等价突变，第一次跑必然红且红的含义不明确。另：耗时 ≈53min 串行（ubuntu 2 vCPU 更慢）⇒ **不适合挂 push**。 | 接线建议：独立 job 只挂 `schedule` + `workflow_dispatch`、首次 `continue-on-error: true`（台账记 CI 历史 47 次 34 红）、串行 `--jobs 1`；先补排除能力（或给规格加平台字段）再接线 |
| C-6 | **部分已修（批次三十二 · task-3）：volumes 挂卷已补；fast_mode / auto_commit / revise 仍缺** | `scripts/smoke_e2e.py` 新增 **(9b)「挂卷后再装配」**：建第 2 章 → `context-preview` 断言 L1 **有**前章尾段 → 建卷 + `assign`（并断该卷 `chapter_count==1`）→ 再预览断言 L1 **无**前章尾段 → 生产装配 `plan`(ch2) 断 `COMPLETED`。Lead 独立实机复跑：`tail before/after=1/0` / `COMPLETED` / `SYNC OK` / `PASS` / exit 0。判别力经进程内 monkeypatch 对照实验证明（把「维度被摘掉」模拟成 peek 谎报 `volume_id=None` ⇒ 尾段复现）⇒ **该步骤不是装饰断言**。 | 其余三项扩展要起服务 + 多 run 编排；生产命名空间「载荷内容」差异未断言（`checkpoint_json` 键集随 workflow 演进，断言太脆） | 剩余 fast_mode / auto_commit / revise 三项；判别口径沿用本批：**新步骤必须给出「维度缺失即红」的对照实验**，不许只断言「跑通了」 |
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

## 四之五、用户授权执行批（2026-10-23 晚 · 「这些也做了吧」⇒ 授权执行 + 服务化）

> **触发**：用户「这些也做了吧」（指 Lead 列出的「未做（等你定）」清单）⇒ 授权执行；随后追加
> 「我现在没在用，你直接改，改完拉起服务，我来用一下」+「你改完也让一个审查员审查一下你的改动」。
> **口径**：✅ = Lead **亲自**读码 / 跑证据验证过；🟡 = 子代理交付、Lead 已复验核心机制，但**待对抗审查复审**。
> ⚠️ 本节所有「已修」在**收尾闸门跑完 + 提交**之前**不代表已入库**。

### A. 本轮落地（逐项对应用户清单）

| # | 项 | 结果 | 证据（Lead 亲验） |
|---|---|---|---|
| 1 | **D-18 凭据清库** | ✅ | 库内明文 api_key **2 行 → 0 行**；`secrets.json` 存 2 个档案键；**走生产读路径** `resolve_profile_api_key` 复核 ⇒ 两个档案仍解析出非空密钥，sha256 `cd786d9d652b` 与迁移前**一致** ⇒ 软件照常能调模型；巡检复跑「合规」（悬空 0 / 明文 0 / exit 0） |
| 2 | **悬空 secrets 条目** | ✅ | `prune_dangling_secrets()` + CLI `--prune-dangling`（默认 dry-run，`--apply` 作写权限总闸；**单独 `--apply` = 迁移**，向后兼容）⇒ 删 1 条死键（`mprof_f95e3c7b4127`）；坏文件**抛错退出 2** 且文件不被改写 |
| 3 | **存量孤儿 relationships** | ✅ 实测 0 行 ⇒ **不造无意义迁移** | 两种口径（两端都悬空 / 任一端悬空）**独立复算**均为 0；`relationships` 共 54 行；诊断脚本 exit 0 ⇒ **活库不动** |
| 4 | **4 个落盘脚本接写盘守卫**（D-19） | ✅ | `backup_db --out` / `db_maintenance prune-logs --export` / `human_baseline_synthetic --out` / `inspect_stalled_runs --export` ⇒ 仓内非 `data/tmp` 路径 ⇒ **退出码 2 + 零文件落盘**；Lead 亲验**守卫早于 `mkdir`**（`backup_db.py:459` 守卫 vs `:482` 建目录）⇒ 不是恒真断言 |
| 5 | **前端三项定性** | ✅ 三条里两条**台账过期** | ① `useApiCall` 专属单测**已存在**（5 例，`apps/web/src/hooks/useApiCall.test.tsx`）；② 「恢复未设置」第三态**已实现且有测试**（`ChapterDetailPage.test.tsx` 断言「未设置」+「当前生效：开」；后端 `_coerce_fast_mode` 三态）；③ `isRunningBannerVisible` **有真缺陷** ⇒ 见第 6 项 |
| 6 | **运行中横幅显示上一个 run**（本轮新发现并修） | ✅ | 根因三环：① `usePoll` 的 effect 依赖**不含被轮询对象身份**（`[enabled, intervalMs, stopOnError, nonce]`）⇒ 目标换了不清数据、不立即重拉；② **await 之后只检查 `aliveRef`、不复查 `stopped`** ⇒ 上一轮在途请求 resolve 后**照样把旧目标的结果写进来**；③ 消费端 `poll.data ?? activeRun` **无条件优先**用它 ⇒ 横幅显示上一个 run 的动作名 / 节点 / **按上一个 run `started_at` 起算的「已运行 N 分」**。修法：`targetKey`（身份变了 ⇒ 清数据 + 立即重拉）+ await 后复查 `stopped` + 在途占位**用 token 不用 boolean**（旧轮 `finally` 不得清新轮占位）+ 消费端按**归属**选（`pickBannerDetail` 纯函数）。判别：造「旧请求在途时切换目标」⇒ 旧结果**不得**出现在 data；撤 `stopped` 复查 ⇒ 必红；撤 `targetKey` 依赖 ⇒ 3 条一起红 |
| 7 | **D-13 形态②：题材包行缺失 ⇒ 静默** | ✅ | 此前 `pack = binding.get("pack") or {}` 把「绑定在、题材包行不在」（悬空引用）降级成空 pack ⇒ 与「压根没绑」**不可区分** ⇒ 报告外观「全通过」；修法：`pack is None` 单独成支 ⇒ **复用已有的**显式不可检标记路径（不新增第二套语义、不动 `formula_hash`）。16 例测试，**四情形两两可区分**（①读库异常 ②包行缺失 ③未绑定 ④包可读）；变异退回 `or {}` ⇒ 4 red / 12 形态① 仍绿（外科手术式） |
| 8 | **`scoring_missing_subscore` 一号两义** | ✅ 定性 + 钉死判别器 | **生产只有一种语义**（`engine.py:252-261` 恒传 7 个 int 子分 ⇒ 「子分缺失」生产不可达）；**唯一可用判别器是 `suggestion`**（A 恒 `None`、B 恒以「不可检：」开头）⇒ 补 3 条断言钉死（严格 `is None` + 引擎路径反向不变量 + A 侧仍可单独产出）。⚠️ 见 §D-1：原裁决理由错了 |
| 9 | **缓存键性能真实规模实测** | ✅ 数字已出 | 48 章**合成夹具**（临时库，**全程未读 `data/novelos.db`**，脚本前后 `data/` 逐文件 `(mtime_ns,size)` 快照逐位相同）⇒ **命中 p50 = 3.69ms（director）/ 3.05ms（writer）**、未命中 **8.36 / 8.58ms** ⇒ **命中比未命中快 2.3~2.8 倍**（坑区「命中比 uncached 还慢」的**方向性担忧不成立**）。⚠️「命中应 ~2ms 级」**偏乐观 1.5~1.8 倍**：命中路径 **81~85% 在重算缓存键**（11 个内容指纹 0.55ms + 2 次 SQLite 建连），**纯缓存查找仅 0.12~0.25ms**。**耗时对章数 O(1)**（12/24/48 章 hit p50 3.69/3.80/3.69，应用层 SQL 恒 30/59 条）、**对项目级实体数敏感**（角色 4→48 ⇒ director hit 3.51→5.82ms，+66%）。脚本 `scripts/measure_cache_key_perf.py` + 9 例机制守卫（**守机制不守数值**）<br>⚠️ **数字已按对抗审查 B · F-5 复测订正（上面这句具体 ms 作废）**：**绝对 ms 不可跨测量上下文比较**——同一脚本、同一机器、同一夹具规模，两次独立实测绝对值相差 **15%~87%**（审查员 B 复算 director 命中 p50 **5.44/5.57/5.96ms** vs Lead 本轮复测 **3.16~3.20ms**）⇒ **只信相对关系**。两处被证伪的具体断言：① 「director 3.69ms / writer 3.05ms」——两次实测下 **director 与 writer 谁更快并不一致**（本轮两者持平于 3.16~3.28ms）⇒ 轴间差异在噪声内；② 「角色 4→48 ⇒ +66%」——本轮**同批次**实测 **+39.4%**（director 4.32→6.02ms；writer 3.18→3.84ms **+20.7%**）⇒ 百分比不作定量结论。<br>**本轮 Lead 一手复测**（2026-10-23 晚，Python 3.12.15 / SQLite 3.53.1 / win32，25 迭代 + 5 预热，每装配器独立进程，临时库 + 合成夹具，全程未读活库）：命中 p50 = director **3.20/3.16/3.19ms**、writer **3.16/3.18/3.28ms**（12/24/48 章）；未命中 p50 = director **7.66/7.47/7.59ms**、writer **8.95/9.08/8.45ms**；命中快 **2.36~2.86×**；重算键占命中 **82.6~85.7%**；纯查找 p50 **0.12~0.20ms**；应用层 SQL 恒 **30/59 条**（director）、**22/50 条**（writer）。<br>**可作结论的定性口径（三次实测一致；⚠️ 本行已于 2026-10-23 **二次订正**——旧值「2.2~2.9×」与「≤3.9%」**作废，不得引用**）**：① 命中比未命中快 **2.3~3.2×**（合并两个测量上下文后的带：Lead 本轮 2.36~2.86× + 审查员独立实测 2.44~3.16×；**倍数不是承诺**，只作方向性结论；坑区「命中比 uncached 还慢」的**方向性担忧不成立**）；② 命中路径 **76~86% 花在重算缓存键**，纯缓存查找仅 ~0.1~0.2ms；③ **耗时对章数 O(1)**（12/24/48 章波动 **≤5%**，同条件口径）；④ **对项目级实体数敏感**（方向成立，比例不作定量）<br>**本轮二次订正的依据**（对抗审查 B 第二轮复审 F-5 minor，Lead 复算）：审查员独立实测把「2.2~2.9×」上破到 **3.16×**、把「章数轴 ≤3.9%」上破到 **极差 5.00%** ⇒ 结论行同步订正；⚠️ 同一次复审还指出本台账**完全没有**本轮性能带订正（`grep "2.3~3.2"` 命中 0）⇒ 本行即该缺口的补齐。 |
| 10 | **CLI 进程退出码看守**（D-8 最后一环） | ✅ | 既有 15 条用例**只断言进程内 `main()` 的返回值** ⇒ **真起 CLI 进程的退出码从未被断言**（中间隔着 `raise SystemExit(main())` 包装，那层坏掉时进程内断言照样绿）⇒ 新增 **4 条**（漂移 ⇒ 非 0；已同步 ⇒ 0 的**反向对照**，且故意在非仓库 CWD 下执行）⇒ 突变自证 **5 failed** |
| 11 | **突变量具失真（harness / probe）** | ✅ | 两处 pytest spawn **不给 `--basetemp`** ⇒ 用到 `tmp_path` 的用例 ERROR ⇒ harness 的 `_assert_tests_ran` 只看输出含 `" passed"` ⇒ **「环境炸了」被报成「突变被抓住」**（假阳性）。实测旧版在 `arc` 契约文件上 1 passed / 3 errors ⇒ 报 `[CAUGHT]`。修：`--basetemp`（独立、仓库外、**父目录先建**）+ **退出码分流**（errors ⇒ `[INVALID]`，与 `run_spec.py` 同形）。⚠️ **第二层失真见第 14 项**（还原不是字节级 ⇒ 量具污染被测物） |
| 12 | **文档漂移订正（2 处）** | ✅ | ① `packages/core/context_engine/director_input.py` 的 `build_director_input` docstring 把缓存键写成 **15 元**，实际 **23 元**（`DIRECTOR_KEY_LEN = 23` 钉住）⇒ 漏掉的 8 元里**正好含 D-7 的 5 个内容指纹** ⇒ 这份漂移**掩盖了缺陷内容**；已订正并注明权威源（键 = 代码里的 `cache_key = (...)`），并加「别在这里手抄键清单」。② `packages/workflows/chapter_commit/pipeline_common.py` 的「既有规则**没有任何一条**设置 suggestion（生产侧恒为 None）」与**紧邻的上一段自相矛盾**（那段自己说 `suggestion` 由产出侧写死）⇒ 会诱导后人把**生产承重**的分流 `iss.suggestion or …` 当死代码删掉 ⇒ 已订正 |
| 13 | **服务化**（用户要「改完拉起服务」） | ✅ | 后端 **18081** 在跑：`/api/health` ⇒ `{"status":"ok","version":"3.10.0","tables":42}`；SPA 托管 `/` ⇒ 200；28 个路由被发现；启动时 `migrations applied=1 skipped=33`（0034 = `commits` 加可空 `draft_version`，**只加列、不回填** ⇒ 业务数据逐字不变）。前端 `dist` 已重建（`index-CsjtWQQj.js`，`tsc -b` 零错 + `vite build` 通过）；`dist/` 被 `apps/web/.gitignore` 忽略 ⇒ 不污染工作树 |
| 14 | **突变量具第二层失真：probe 还原不是字节级** | ✅ | probe 的「临时改一处 → 跑定向测试 → 还原」原用 `read_text`/`write_text` ⇒ Windows 上 `write_text` 把 `\n` 翻成 `\r\n` ⇒ **还原后文件字节与开工前不一致**（SHA256 漂移）⇒ **量具自己污染被测物**（被测文件被悄悄换成 CRLF）。⇒ 改**字节通道**（`read_bytes`/`write_bytes`）写入与还原 ⇒ sha256 逐位一致（实测）；`l12_mutation_probe.py` 的 MP-1 锚点随之更新为字节通道形态（**只改锚点字符串，条目数不变**），MP-1 实跑仍 `[CAUGHT]`。⚠️ **本条是「量具失真」的第二个形状**：第一层是「报错被当红」（假绿），第二层是「量具改了被测物」（污染）⇒ 两者都让闸门结论不可信 |
| 15 | **判定函数的判别力自证** | ✅ | 新增 `tests/unit/test_mutation_harness_judgment.py`（**33 例**）：把**旧版判定逻辑**（只看 `" passed"`）放进**仓外副本**跑 ⇒ **4 failed** ⇒ 证明新用例真能抓住旧缺陷（不是恒真断言）。⚠️ harness / probe 的判定函数**刻意各写一份**（不建共享模块：探针须能在**任何**目录独立运行）⇒ 靠仓内「不许分叉」用例保证同口径 |
| 16 | **CLI 契约变更（单列留痕）** | ✅ | `check_content_repo_guard.py secrets` 的**纯巡检**（无 `--apply`）在「读不到表 / 已知集合为空」时，退出码 **0/1 → 2（巡检自身出错 / 不可判）**。理由：**判不出来时不得报绿**（「没读到」≠「全部多余」，即 F-1 的真身）。改的是**进程退出码**（CI / pre-commit 只看这个）⇒ 已验判据会作废 ⇒ **必须单列**；同步写进 `scripts/README.md` 退出码语义表 |
| 17 | **F-10 文档补齐 + README 计数订正** | ✅ | 新增 CLI flag **逐处与 `HEAD` 对比实测 = 7 个 flag 名 / 11 处出现**（`--allow-software-repo-out` ×4、`--allow-software-repo-key` ×1、`--write-docs` ×2、`--artifacts-dir` ×2、`--migrate`/`--prune-dangling` ×1）⇒ 全部进 `scripts/README.md`（此前 **0 处提及**）；⚠️ `--apply` **不是新增 flag**（既有），但在 `secrets` 模式里角色变为**写权限总闸** ⇒ 一并写明。顺带订正两处既有漂移：① 脚本计数「现行 31 个」**错** ⇒ 实测 `scripts/*.py` = **37** 个（**35 CLI 入口 + 2 共用模块** `_codegen_check.py`/`guard_repo_write_path.py`），且 **5 个脚本此前从未登记** ⇒ 已补进总览表；② `test_inspect_stalled_runs.py` 例数「59」**过期** ⇒ 实测收集 **56** 例 |
| 18 | **清理时发现并修掉的真泄漏：测试侧临时目录**（授权项「260 个泄漏临时目录」的**根因**） | ✅ | 清理过程中实测到：**全量回归期间系统 Temp 的 `novelos_openapi_env_*` 计数在涨** ⇒ 逐个溯源（不是猜）：`tests/api/test_spa_hosting.py:286` 用 `tempfile.mkdtemp(prefix="novelos_openapi_env_")` 造 app 的临时 data 目录、**从不删**；`tests/unit/test_backup_db.py::test_keep_plaintext_on_failure_flag_keeps_the_copy` **故意**让脚本保留明文副本（那是被断言的行为）却**不回收** ⇒ 每跑一次全量回归就多一个 `novelos_backup_*`（**躺着明文 DB 副本**）。⇒ 两处都补清理；**判别力实测**：撤掉清理 ⇒ 两条守卫**同时变红**（`test_openapi_env_probe_leaves_no_temp_dir_behind` 报「临时目录泄漏」、`test_keep_plaintext_on_failure_flag_keeps_the_copy` 报「测试自身泄漏了明文副本目录」）。⚠️ 新守卫用「间谍 `tempfile.mkdtemp` + 只盯自己建的那一个」而非「扫全局 Temp 前后差」——后者在 `pytest -n 20` 下会因**别的 worker 同时建目录**而偶发假红 |
| 19 | **生产侧泄漏已止住（复核结论）** | ✅ 已验证 | `scripts/export_openapi.py` 的临时目录**实测不泄漏**：单次调用 `build_schema()` ⇒ 系统 Temp 的 `novelos_openapi_*` 计数 **153 → 153（泄漏 0）** ⇒ 批次三十一的「必用完即删」修复**确实生效**；此前的 295 个是**修复前**的历史残留 |
| 20 | **连接层明文密钥收敛（本轮补做）** | ✅ | 用户在服务上**新建了 2 个模型连接**（`mconn_0e1e79327d9c` / `mconn_0253589abe94`，15:14~15:15）⇒ **连接层**的 `params_json.api_key` 又是明文 ⇒ 巡检报 `ok:false / 库内明文 api_key 2 行` ⇒ **D-18 只覆盖了 profile 层，连接层没覆盖**。⇒ 处置：**先在活库副本上验证**（复制 128MB 库 + secrets.json 到仓外 ⇒ 跑 `--migrate --apply` ⇒ 用**生产解析函数** `resolve_profile_api_key` 取每个连接/档案解析结果的 **sha256** 比对 ⇒ **逐位一致**、两表明文归零、secrets.json 2→4 键）⇒ 验证通过才动活库。**执行结果**：活库两连接解析结果 sha256 **逐位一致**（`e6d6570d264d` / `cd786d9d652b`），`model_profiles` 与 `model_connections` 的明文 api_key **均归零**，`secrets.json` 4 键，巡检「合规」，服务健康。⚠️ `secrets_store.load_api_keys` 是 **mtime 缓存** ⇒ 写盘后**自动重载** ⇒ **无需重启服务**。⚠️ 已手工留副本 `Temp/secrets.json.bak.before_d18_conn` |

### B. 对抗审查员 A 的 findings 与 Lead 裁决

审查对象：4 个落盘脚本 + D-18 凭据脚本 + 提示词自愈抽查。审查员用**隔离副本**做变异实验（真实软件仓全程零写入），8 个受审文件 SHA256 开工/收尾逐位一致。

**VERDICT（审查员原文）**：组 1（D-19 守卫）主线**攻不倒**（变异 M1「守卫不拒绝仓内路径」⇒ 7 red；M2「守卫搬到写入之后」⇒ 1 red，证「零文件落盘」是真断言）；组 3（提示词自愈）**攻不倒**；**组 2（D-18）不可进主干**。

| # | 严重度 | 问题 | Lead 裁决 |
|---|---|---|---|
| **F-1** | 🔴 **critical** | `--prune-dangling --apply` 在「已知集合为空」时（库存在但**无 `model_profiles` 表**，或表 0 行）⇒ **每个键都判成悬空** ⇒ `api_keys` 整段清空、**退出码 0 = 合规（绿）**。**读表失败与「集合为空」在原实现里是同一个信号** | **必修**（这是本轮引入的洞）⇒ ① 已知集合为空 / 读表失败 ⇒ **拒绝并退出 2**（绝不把「没读到」当「全部多余」）；② 落盘前**强制备份** `secrets.json.bak-<ts>`（同目录 + 原子）；③ **不加 `--confirm`**（避免破坏既有自动化流程，①② 已足够） |
| **F-2** | 🟠 **major** | 悬空判据只认 `profile_id ∪ connection_id ∪ provider`，**漏了运行期第 3 优先级 `secrets[<env_name>]`**（如 `NOVELOS_API_KEY_KIMI`）⇒ 仍在用的密钥被误删，且 `--migrate` **永远造不回来**（它只写前两种形态） | **必修** ⇒ ① known 集合补 env_name 派生形态；② **prune 前用生产解析函数 `resolve_profile_api_key` 复核**（能解析出非空 ⇒ 不许删）⇒ 复用生产口径，**不另造第二套「已知集合」**（AGENTS 坑区「两套口径」的形状） |
| **F-3** | 🟠 **major** | `backup_db --key-file` 的写路径**完全没接守卫** ⇒ 可把「能解开所有备份的密钥」写进仓内，且 `.gitignore` **不覆盖** `backup.key` ⇒ `git add -A` 即进 GitHub 历史、**不可撤回** | **必修** ⇒ ① 密钥文件也过 `check_write_path`；② `.gitignore` 补 `*.key`（成本近零、后果极重） |
| **F-4** | 🟡 minor | `db_maintenance` 的退出码 2 现在有 **4 种含义**（argparse 用法错 / DB 不存在 / 内部异常 / 写路径被拒），且 `--allow-software-repo-out` 必须放在子命令**之前** ⇒ 用户最自然的写法就撞 argparse 错误（也退 2） | **本轮只修 flag 位置**（低成本、直接消掉「最自然写法撞车」）；**退出码独立化（4）留待下一批**——改契约会让本轮已验的守卫结论作废、需重新审，**收益不足以在闸门前动** |
| **F-5** | 🟡 minor | `inspect_stalled_runs` 的退出码 3 同时表示「痕迹仓不可用」与「`--apply` 未实现」 | 维持登记（两者都是「拒绝、无副作用」，危害小于 F-4） |
| **F-6** | 🟡 minor | `--out` **静默吞掉** `--write-docs`（`human_baseline_synthetic` 与 `inspect_stalled_runs` 各一处）⇒ 用户以为两份都落了，实际少了「该进痕迹仓的那份」，**零告警** | **修**（两份都落 + 告警，或明确报错——由实现者论证后定） |
| **F-7** | 🟡 minor | 写盘守卫测试的探针产物写在 `REPO_ROOT/docs/_guard_probe_*` 且**无清理 fixture** ⇒ 守卫一旦回归（最需要证据的时刻），软件仓 `docs/` 被污染（含**明文** DB 副本），且**下一次跑**改为挂在「前提：目标不得已存在」上 ⇒ **掩盖真实断言**（审查员实测：变异后残留 ⇒ 还原后重跑 4 failed，全是前提失败） | **修**（优先把落点改到 `tmp_path` + monkeypatch `REPO_ROOT` ⇒ 探针根本不该落在受版本跟踪的 `docs/` 下） |
| **F-8** | 🟡 minor | 同文件 `_snapshot` **只收文件、不收目录** ⇒ 「连目录都不该被建出来」这条性质**全仓无断言** | **修**（`_snapshot` 同时收目录 + 对目标父目录断言 `not exists()`） |
| **F-9** | 🟡 minor | 4 个新脚本的 `--allow-software-repo-out` **一例测试都没有** | **修**（每脚本补 1 例：落点真落 + stderr 告警） |
| **F-10** | 🟡 minor | 本批新增的 8 个 CLI flag 在 `scripts/README.md` 里 **0 处提及** | **修**（由 Lead 统一改，避免与并发批次冲突） |

**审查员自报的 pre-existing（非本批引入，但影响 CI）**：`test_escape_hatch_allows_writing_into_software_repo` 与 `test_default_run_does_not_write_into_software_repo` 依赖本机存在同级 `D:/zcodeproject/NovelOS-Artifacts` ⇒ **CI 上没有这个目录 ⇒ 会红**（`ci.yml` 里 grep `Artifacts` 无命中）。⇒ 登记，**须由有 GitHub 日志权限的人核实 CI 实况**，本机无法验证。

**收口状态（Lead 复验后）**：F-1 / F-2 / F-3 / F-6 / F-7 / F-8 / F-9 / F-10 **已修**；F-4 **只修 flag 位置**（退出码独立化留待下一批）、F-5 **维持登记**（两义都是「拒绝、无副作用」，危害小于 F-4）。

> ⚠️ **F-1 的 Lead 独立复验（不采信审查员结论）**：造一个「**无 `model_profiles` 表**」的库跑 `--prune-dangling --apply` ⇒ **退出码 2**、stderr 明说「已中止清理（未写任何文件）」、`secrets.json` **逐字未改**、两个键**都还在**、**零备份文件**产生 ⇒ 确认「已知集合为空 ⇒ 清空整个凭据库还报绿」这条 critical **已堵死**，且不是恒真断言。

### C. 台账条目核销（复算后判定「已实现 / 已过期」⇒ 移入 CHANGELOG）

> **口径**：不是「Lead 记错了」，而是**这些条目描述的是当时的代码状态，后来被别的批次修掉了，但台账没回填**
> （AGENTS 坑区已有同形状条目：「登记不修写进文档，修复落地后没回填 ⇒ 文档与代码正相反」）。

| 条目 | 台账原状态 | 复算结论 | 依据 |
|---|---|---|---|
| **A-3** | `core↛domain` 无机器看守，判「设计而非破口」 | **已过期** | `tests/unit/test_no_core_to_workflows_dep.py` 现有 5 条方向（含 `core→domain`）+ 44 条精确白名单 + **字面量 pin** + 动态/相对逃逸**双根**扫描 + 缺失/幽灵双向核对 |
| **D-2** | 备份「有写无读」：无 restore 工具 / 无 runbook / 恢复可静默失效且假绿 | **已过期** | `scripts/restore_db.py` 存在：同卷临时文件 + `os.replace` 原子替换；删 sidecar 前先 `wal_checkpoint(TRUNCATE)`；恢复后 `integrity_check` + 表数核对（退出码 4/5）；失败回滚快照 |
| **D-3** | 解密失败仍会破坏活库 | **已过期** | 同上（原子替换 + 失败回滚） |
| **D-4** | 默认明文备份 + 加密后从不验证密文可解 + 密钥缺失静默轮换 | **已过期** | `backup_db.py:441-443`：`--encrypt` **默认开**，明文须显式 `--plaintext`；密钥缺失不再静默生成 |
| **D-7** | director 装配缓存键 **5 处漏维度**（第 6/7 次同形状） | **已过期** | 键已含 `hooks_fp / debts_fp / plot_fp / foreshadow_fp / title_fp`（`director_input.py:517-528`）；`tests/mutation_specs/l22_director_ledger_key.py` 的 **LEDGER-KEY-1..5** 五条突变规格钉住（撤维度必红）。⚠️ **机制说明**：这 5 个维度是**内容指纹每次现算**（不是靠 `state_version` 推进）⇒ **改数据 ⇒ 指纹变 ⇒ 必 miss**，比「推 state_version」更直接；代价是每次命中都要重算指纹（见 §A 第 9 项的性能数字） |
| **D-8** | 批次三十的 CLI 失败退出码全仓无看守 | **完全闭合**（本批补最后一环） | 既有 15 条只断言**进程内**返回值；本批新增 4 条**真起进程**的退出码断言（含反向对照）⇒ 突变「`return 0`」⇒ 5 failed。⚠️ 另注：`tests/mutation_specs/l23_codegen_cli.py` **早已**含 4 条 CLI 退出码突变 ⇒ 该项在本批之前**并非完全无看守**，台账原表述偏宽；本批补的是**通用 CLI 层**（codegen 脚本之外的 `check_content_repo_guard` / `backup_db` 等） |
| **D-10** | 外发投稿包取「最新 draft」而非已 commit 那版 | **已过期** | 迁移 **0034**（`commits` 加可空 `draft_version`） + `exporter/builder.py::_committed_draft_version`（存量 NULL ⇒ 回退取最新，行为逐字不变）；活库 134/134 行为 NULL（**不回填是设计**，事后按时间戳倒推会把「审完又改稿」那版误认成定稿版） |
| **D-11** | `content_sync` 三条静默形态 | **已过期** | 隔离区 `content_sync/paths.py`（`_quarantine/`，只进不删）+ manifest 留 quarantined 记录；缺表降级 `service.py:247-260`（记 error 后继续，不再整次 push 崩） |
| **D-13** | 题材包读失败 ⇒ 题材开篇整段静默消失 | **形态①已修；形态②本批补上** | 见 §A-7 |
| **D-16** | 版本一致性机器看守只覆盖 6 处中的 3 处 | **已过期** | `check_skill_package.py` 已含 web 三处（package.json / package-lock 两个副本 / uv.lock）+ `--selftest` 变异 M9–M12 + `tests/api/test_frontend_version_consistency.py` |
| **D-17** | 嵌套 pytest 继承本机默认 basetemp | **已过期（口径需订正）** | 3 处已带 `--basetemp`（台账四之二记「2 failed → 0」）；实测全仓 **5 处** pytest 子进程 spawn（台账写「两处」是旧口径）；**但** `mutation_harness.py` / `mutation_probe.py` 两处当时**未带** ⇒ 本批修（见 §A-11） |
| **「260 个泄漏临时目录」** | 待清理 | **口径错**（但本批已真清理） | 不是 260、也不是「仓库根 0 个」（我查错了范围）⇒ **系统 Temp 实测 447 个** `novelos_*`：`novelos_openapi_*` 295 / **`novelos_backup*` 40（每个含 `.db` + `.db.enc` 一对 ⇒ 备份脚本自注「可能还躺着含明文 API 密钥的整库」）** / 其余 112。⇒ **这是凭据残留，不是「垃圾目录」**。⚠️ **本批清理结果**：40 个 `novelos_backup*`（**凭据级**）**全部清零（现为 0）**；其余可删项经 mavis-trash（**可恢复**通道）批量删除；**残留 151 个 `novelos_openapi_*` 清不掉**（⚠ 该数**随时间漂移**，以实测时点为准）⇒ 见 §E 的登记行 |

### B2. 对抗审查员 B 的 findings 与 Lead 裁决（第二轮 · 2026-10-23 深夜）

> **审查对象**：第三轮修复批（写盘守卫 / D-18 凭据脚本 / 前端横幅 / 突变量具 / 性能脚本 / 文档三件套）。
> **VERDICT（审查员原文）**：**需修 3 处后再审**；主体（F-1 凭据堵死、D-13 四情形、突变量具分流、前端横幅归属、性能脚本不碰活库）**攻不倒**。
> 审查员用**隔离副本**做变异实验，真实软件仓全程零写入。

| # | 严重度 | 问题（审查员复现） | Lead 裁决与修法 |
|---|---|---|---|
| **F-1** | 🟠 major | `\\?\` **扩展长度前缀** ⇒ 守卫放行、文件**真落进仓内**（4 新 + 4 既有脚本共 **8 个**受影响；端到端 **5 个 flag** 全 exit 0 且文件落进 `<repo>/docs/`）⇒ 且 `.gitignore` **不覆盖** `docs/prune.json` 等 ⇒ 一次 `git add -A` 即入库 | **必修** ⇒ 守卫判定改**规范化口径**（去 `\\?\` / `\\.\` 前缀 → `normpath` → 裁每分量尾随空格/点 → 小写）⇒ `\\?\` 不再是绕过 |
| **F-2** | 🟠 major | 在途修复**未落到执行点**；且 `_trim_win_trailing` 把 UNC 根 `\\server\share` 压成 `\.\server\share`（**换了一条路径**）⇒ 新绕过 | **必修** ⇒ 三层口径：① 规范化；② **UNC 根保真**；③ 规范化判「仓外」时按 **`(st_dev, st_ino)` 身份**再核一次 ⇒ 同一目录的**别名拼写**（如 `\\localhost\D$\<repo>`）也不放行 |
| **F-3** | 🟠 major | `--migrate` **无写前备份**；且用库内**旧明文静默覆盖** `secrets.json` 里**更新的值** | **必修** ⇒ `--migrate` 写盘前**强制备份**；同名且值不同 ⇒ **保留 `secrets.json` 里的值**（运行期本就以它为准）+ 记 `[冲突]`、**不静默覆盖**（库内明文照常清） |
| **F-4** | 🟡 minor | 纯巡检在 `secrets.json` **解析失败**时仍报「合规」exit 0（pre-existing，但落在本轮 §A-16 契约上） | **修** ⇒ 读不出来 ⇒ **不可判** ⇒ 退出码 **2**；⚠ **反向不变量保留**：文件**不存在**仍 exit 0（那是合法状态，**不得过修**） |
| **F-5** | 🟡 minor | 性能数字与复算**对不上**；§A 第 9 项**混了两个装配器的轴** | **修** ⇒ Lead **自己重跑**（章数轴 12/24/48 + 实体轴 角色 4/48）⇒ **绝对 ms 不可跨测量上下文比较**（两次独立实测的绝对值不同 ⇒ ⚠️ 该差异**不是同条件方差**，**不得**引用百分比带）⇒ 文档改为「只信相对关系」+ 标机器/负载上下文 + **分轴列表**（见 §A 第 9 项与 CHANGELOG §7 的订正段） |
| **F-6** | 🟡 minor | `measure_cache_key_perf.py --db` **父目录不存在** ⇒ 抛未捕获 `sqlite3.OperationalError` 栈 | **修** ⇒ 明确报错 + 退出码 **2**，**不建目录** |
| **F-7** | 🟡 minor | `usePoll.ts` 切目标只清 `data` **不清 `error`** | **修** ⇒ 切目标分支补 `setError(null)` |

**修复证据（Lead 亲自复验；每条新用例都做过「撤掉修复 ⇒ 必红 ⇒ 恢复 ⇒ 绿」双向自证）**

- **守卫**：`tests/unit/test_scripts_write_guard.py` **47 → 52 例**（新增 5 例：`\\?\` 前缀拒绝〔含**防恒真的前置证据**〕、尾随空格从严、仓外不误拒、UNC 根保真、UNC 管理共享别名拒绝）。**判别力**：撤规范化 ⇒ 恰对应 **2 例**变红；撤身份核 ⇒ UNC 别名例变红。
- **D-18**：`tests/unit/test_check_content_repo_guard.py` **28 → 32 例**。**判别力**：撤三处修复 ⇒ 恰 **3 例**变红（反向不变量「文件不存在仍合规」保持绿）。
- **前端**：`apps/web/src/hooks/usePoll.test.ts` **14 → 15 例**。**判别力**：撤 `setError(null)` ⇒ 恰该例变红。
- **性能脚本**：`--db` 父目录缺失 ⇒ exit 2 的用例并入既有机制守卫文件。

**审查员复算后 Lead 独立复核的项（两边一致才入册）**：脚本 37 个 / 突变规格 84 文件 476 条 / 各测试例数（56·47·28·33·16）/ CLI flag 11 处 / AGENTS 三处条数 / Temp `novelos_backup*` = 0 / openapi 产物 151（存量漂移、不再增长）/ 性能**定性**（命中快 2.24~2.85×、83~84% 花在重算键）⇒ **复现**。

### B3. 收尾闸门挖出的新缺口（对抗审查 B 修复批 · 2026-10-23 深夜）

> 闸门（指纹前/后、ruff、全量 pytest、规格预检、**全量突变实跑**、残留清点）**全绿**，但全量突变首轮出现 **1 条 INVALID** ⇒ 查明后**如实登记**（**未修**，理由见末条）。

| 现象 | 复现路径 | 结论 |
|---|---|---|
| 全量突变 `--all --jobs 16` 首轮：`SKILL-KEY-1` 判 **INVALID**（存活 0 / 无效 1） | ① **串行单跑** `run_spec.py tests/mutation_specs/l7_skill_cache_key.py` ⇒ **2/2 CAUGHT**；② **重跑并发全量**（完整输出落盘）⇒ `SKILL-KEY-1` **CAUGHT**、**476/476 全捕获**、**exit 0** | **并发偶发**，**不是规格失效**：锚点原文**仍在** `director_input.py:538`（`chars_fp, world_fp, skills_fp, prompt_label,`），规格本身完好 |

**根因（Lead 推断，有两次复现支撑，但**未做定向实验** ⇒ 明确标注为推测）**：`run_spec.py --jobs N` 的分片按**目标源文件**切；同一批里 `SKILL-KEY-1`（改 `director_input.py`）与 `SKILL-KEY-2`（改 `writer_input.py`）**共用同一个目标测试文件** `tests/unit/test_skill_injection.py`，而该测试文件**同时 import director 与 writer 两侧** ⇒ 并发时一个 worker 的 pytest 进程可能 import 到另一个 worker **临时改坏**的模块 ⇒ 用例 ERROR（而非 FAIL）⇒ 执行器按既定分流归 **INVALID**。

⚠️ **量具本身没失真**：执行器把「跑挂了」判成 INVALID 而**不是** CAUGHT ⇒ **不虚报绿** ⇒ 闸门结论仍安全（exit 1，不会被误当通过）；代价是「16 路并发」这条档位的数字带**噪声**。

> 🚨 **2026-10-23 深夜 · 对抗审查 B 第二轮复审：本段的两条结论都被推翻（Lead 复算确认）**
>
> 1. **「不虚报绿 ⇒ 闸门结论仍安全」——证伪。** 并发档位**会产出假 CAUGHT**（= 虚报绿）：对抗审查员在隔离副本上实测——一条「**只改注释、单独跑必然 SURVIVED**」的突变（NOOP-1），与另一条并发突变同跑时 **8/8 轮被报 CAUGHT**。⇒ 本仓写下的「量具如实报 INVALID 而非虚报 CAUGHT」**只是本次没撞上那个方向**，不是「量具不会虚报绿」。**本轮「476/476 全捕获、exit 0」这个并发数字据此作废。**
> 2. **「根因 = 两条突变共用同一目标测试文件」——证伪。** 污染源不是「共用测试文件」，而是「**被目标测试文件 `import` 的其它源文件**」被另一个 worker 临时改坏 ⇒ 实测**两条突变的测试文件不同照样被污染** ⇒ **「按测试文件分片」修不好这个洞**（原「待排期 ①」的方向是错的）。
> 3. 同一污染还产出**判定不可复现**（同一输入、同代码，一次 SURVIVED、一次 CAUGHT）与**归因错配**（真实规格 15 条上错配 **3/15**，三次一致）⇒ 并发档位**整体不可作闸门结论**。
> 4. 审查员对 full16 的 3 条 `SURVIVED` 明确回答：**那 3 条是它自己的探针**（NOOP-1 / CTRL-W / CTRL-U）⇒ **仓内规格 0 存活**；但「并发掩盖真存活」的线索落在 **INVALID 桶**（`L2-9` / `L2-16` 教科书级交叉污染）⇒ **串行重跑时对照这两条**。

**处置（2026-10-23 深夜已改执行器）**：
- **当时的判断（已被推翻）**：以为「当前行为**偏保守**（INVALID ⇒ 闸门 exit 1 ⇒ 绝不会被误当成通过）」⇒ 判为「可排期」⇒ **只登记、不改执行器**。⇒ 该判断建立在「量具只会往保守方向失真」这个**未经验证的前提**上 ⇒ 被假 CAUGHT 实测推翻。
- **现在的处置**：**闸门一律串行**（`run_spec.py --all`，≈ 53min；增量档 `--changed` 串行 2~4 分钟）。⇒ 执行器把「结论不可信」变成**机器可判的硬约束**：并发档位（未经 `--accept-concurrent`）**退出码恒为 3**（哪怕 0 存活）⇒ CI / 闸门脚本不可能把它当成绿的结论；**汇总行必须写明档位**；`--help` 写明退出码语义；仓内用例钉死这四条（`tests/unit/test_mutation_run_spec_jobs_guard.py`）。
- **待排期（副本隔离方案，已实测可行）**：「每 worker 一份独立树副本」——本仓 editable 是**老式 `.pth`**（内容仅一行仓库 `packages` 路径）⇒ `PYTHONPATH` 优先于 `.pth` ⇒ 副本能接管 `import packages.*`；拷 `packages`（11M）仅 **0.42s** ⇒ 这是**能保留全量档位速度的唯一正确解**。⚠️ 需先解决副本内的 `data/` 与 `conftest` 路径依赖。
- ⚠️ **原「待排期 ①」（按目标测试文件 + import 闭包聚类）方向错误**（见上方推翻 2）⇒ **不做**。

⚠️ **另一条边界（本轮新发现，已实测）**：`run_spec.py` 的 pytest `--basetemp` 根目录取 `tempfile.gettempdir()` ⇒ 本机实测落在**系统 Temp**（`C:/Users/aistation/AppData/Local/Temp/novelos-mutbasetemp`）⇒ **不在仓库根** ⇒ 突变跑完**不污染工作树**（`git status` 逐条核对：只有本批 **10 个**预期改动文件，无任何突变残留）。⚠️ 该实测与「本机 `tempfile.gettempdir()` 恒等于 CWD」的旧记录**不一致** ⇒ **以本次实测为准**（旧记录可能只对某些调用路径成立，未复核）。

### B4. 对抗审查员 B 的**第二轮复审** findings 与 Lead 裁决（2026-10-23 深夜）

> 审查对象 = `5ed32d8`（第三轮修复批 5 个提交）⇒ **VERDICT: FAIL**。
> 受审 10 个文件的 SHA256 开工 / 收尾**逐位一致**（`FILES_CHANGED = 0`），审查员**零写入**真实仓；仓外证据留存在 `C:/Users/aistation/Documents/_probe_revB2/`（约 0.42MB）。

| # | 严重度 | finding | Lead 裁决 | 修法 | 判别力（Lead 亲验） |
|---|---|---|---|---|---|
| **F-1** | **major** | 并发档位**造假 CAUGHT**（= 虚报绿）+ 归因错配 + 判定不可复现 | ✅ 接受（**并推翻自己两条结论**） | 并发档位**退出码恒为 `3`**（除非 `--accept-concurrent`）+ 汇总写明档位 + 文案改正错误归因 + `--help` 写退出码语义 | 新增 12 例；**5 项变异各红** |
| **F-2** | **major** | 守卫**规范化层四子层零测试覆盖**（逐个撤掉全 0 红） | ✅ 接受 | 补 **6 条** `normalize_for_compare()` **返回值**级用例 | 6 个缺陷版各红 |
| **F-3** | **major** | `secrets.json` **结构非法**仍报绿；三条路径口径分裂 | ✅ 接受 | 三态判定 + 三路径同口径 **exit 2** | 缺陷版 A ⇒ **8 红** |
| F-4 | minor | 性能脚本「父路径是**文件**」仍抛未捕获栈 | ✅ 接受 | `exists()` → `is_dir()` | 2 个缺陷版红 |
| F-5 | minor | 性能文档**两条「带」**被独立实测上破 | ✅ 接受 | 三条带逐条订正 | 复算 |
| F-6 | minor | `--json` 丢 `conflicts` / `backup_path`；`action_count` 计候选 | ✅ 接受 | 补字段 + 拆成 `action_count` / `candidate_count` | 缺陷版 B ⇒ **4 红** |
| F-7 | minor | `is_inside_repo` 与 `relative_in_repo` **双口径可分叉** | ✅ 接受 | **委托**（1 行）+ 6 形态用例 | 6 个缺陷版各红 |
| F-8 | minor | `run_spec.py` 目标文件缺失 ⇒ **整轮崩** | ✅ 接受 | 记 `[INVALID]` | 1 例 |
| F-9 | minor | `scripts/README.md` 计数 47/28 → 实测 52/32 | ✅ 接受 | 改成实际 | grep |

⚠️ **Lead 对审查员建议的一处订正**：F-7 建议用「**两口径同结论**」不变量作看守 ⇒
**实测抓不到**（旧实现与委托版在逻辑上**等价** ⇒ 撤掉委托后该不变量**仍然成立** ⇒
用例是绿的）⇒ 改用**证明派生关系**的用例（monkeypatch `relative_in_repo` 的返回值 ⇒
`is_inside_repo` 必须跟着变）作**真正的**看守；「同结论」不变量**保留**（它守的是
「未来某一边被单独改动」）。

⚠️ **审查员的两条自我纠错**（如实登记——**审查结论本身也要有判别力**）：
① 它自己的驱动脚本踩了**行尾陷阱**（`bytes.decode` 不归一 + `write_text` 翻 `\n`）
⇒ 造成 **38 例假红** ⇒ 改用**字节通道**重做；② 闭包 `rep()` 改共享列表导致变体
**累积叠加** ⇒ V-D/V-E 的 38 红是叠加结果 ⇒ 重做成逐条独立变异。

⚠️ **审查员明确回答**（避免下一个人重复查）：full16 的 3 条 `SURVIVED` 是**它自己的探针**
（NOOP-1 / CTRL-W / CTRL-U）⇒ **仓内规格 0 存活**；但「并发掩盖真存活」的线索落在
**INVALID 桶**（`L2-9` / `L2-16` 教科书级交叉污染）⇒ **串行重跑时对照这两条**。

**审查员攻不倒的部分（如实登记）**：守卫 10 种绕过形态端到端全 exit 2 + 零落盘；
D-18 三处撤除各恰 1 红 + 冲突保留 / 备份 / `[冲突]` / 坏文件 exit 2 / 反向不变量全成立；
`setError(null)` 恰 1 红；F-5 文档订正方向；`--changed` 增量 0 条成立；84/476 成立；
probe 字节通道修复是真的；性能脚本不碰活库。

#### B4-1. 本轮 Lead **自踩事故**暴露的新 critical（审查员**未**发现）

| 项 | 内容 |
|---|---|
| **事故** | Lead 跑 F-3 复验探针时**忘了传 `--secrets-file`** ⇒ 脚本读**仓库真实**的 `secrets.json`，而 `--db` 指向 Lead 现造的**合成夹具库** ⇒ 夹具库已知集合与真实 4 个键**一个都对不上** ⇒ 4 个键**全部被判悬空** ⇒ `--prune-dangling --apply` **一次把 4 个真实凭据全清空**，退出码仍是 0（报「合规」） |
| **靠什么救回来** | 上一轮 F-3 修法的「**写前强制备份**」（`secrets.json.bak-<ts>`）⇒ 备份里 4 个键的值**逐位完整** ⇒ 已恢复，`secrets.json` 的 4 个键与值 **sha256 逐位一致**，活库巡检复跑**合规（exit 0）**，生产解析函数仍解析出 4 个非空密钥 |
| **根因** | 上一轮 F-1 只挡住「已知集合**为空**」与「读表失败」，**漏了第三种**：「已知集合**非空**但与 secrets 的键**零交集**」⇒ **「已知集合非空」≠「这个库属于这个 secrets 文件」** |
| **修法** | ① **交集闸门**：零交集 ⇒ 悬空**不可判** ⇒ 巡检 exit 2、清理拒绝（文件逐字节不变、零备份）；② **`--force-all-dangling`**：「全部键都死」与「指错了库」在数据上**不可区分** ⇒ 闸门必须带**可解除的出口**；③ `--migrate`（**加法**动作）**不受**拦截 |
| **判别力** | 新增 5 例；**三层变异自证**（撤采集层闸门 ⇒ 3 红 / 撤 prune 层守卫 ⇒ 1 红 / force 接成「永远跳过」⇒ 3 红） |
| **连带代价（如实登记）** | **两条既有用例被迫加 `--force-all-dangling`**（它们的夹具形状落在新闸门的「不可判」区）⇒ 已在 docstring 写明原因，并新增「默认拒绝」「显式放行」两条用例 |
| **升格** | 「集合差集 ⇒ 多余项」是**同形状第 3 次出现** ⇒ 已升格为 **AGENTS 硬规则 11** + 坑区一条 |

⚠️ **本闸门的残留边界（如实登记，不粉饰）**：交集闸门只在**零交集**时拦。
**部分交集**（secrets 里有 N 个键、其中 1 个碰巧对得上那个库）⇒ 闸门**不拦** ⇒
其余 N−1 个键照常被判悬空并删除。而这个形状**与「正常删死键」在数据上不可区分**
（用户删了档案 P1、库里有 P2 ⇒ secrets = {P1, P2}、known ⊇ {P2} ⇒ 交集 1 ⇒
「P1 是死键」是**正确**判断）⇒ **数据层无法再进一步**。
⇒ 真正的解法是**项目配对**（`secrets.json` 与 `--db` 必须属于**同一个安装**：
同一仓库根 / 同一 data 目录）——那是**产品级设计决定**（谁在什么场景下会用
「另一个库的 secrets」），本批**不做**，登记待排期。
⇒ 在此之前，**人的闸门是 `--apply` 本身**（写权限总闸）⇒ 误指 `--db` 后再 `--apply`
仍是**人的失误**，工具只能挡住最常见的**完全指错**形态。
⚠️ 审查员若认为这个残留必须本批就补，请明确给出可落地的判据（否则 Lead 只能按
「不可区分 ⇒ 需人工确认」处理）。

### D. Lead 先验判断被推翻（本轮 4 条，全部留痕）

1. **「`formula_hash` 不变成立 ⇒ 不新增 rule_id」——错。** `formula_hash()` 的输入含 `severity_config_fingerprint()`（`aggregate.py:86-93`），而后者**覆盖 `BLOCKING_RULES` 与 `CONFIRM_RULES`**（`issues.py:307-318`）⇒ 新增 blocking rule_id **必改 hash**；本仓自己的测试 `tests/unit/quality/test_aggregate.py::test_formula_hash_covers_blocking_whitelist` 就断言了这点 ⇒ 台账原句与本仓测试**冲突**。⇒ 裁决：**不拆 rule_id**（拆要改 hash + 改 5 处既有测试 + 双 id 兼容，性价比最低），改走「把判别器钉成结构化断言」。
2. **「当前无消费方依赖 rule_id 判别 ⇒ 无行为后果」——错。** `packages/workflows/chapter_commit/pipeline_common.py:484` 的 `hint = iss.suggestion or _RULE_REVISION_HINTS.get(iss.rule_id)` **确实按 `suggestion` 分流**（两类语义方向相反：「正文读不到」应去修草稿表，「子分缺失」应去补 `plan_json`/`submit_delta`）⇒ **判别器是生产承重的**，不是「潜在陷阱」。⇒ 反而**加强**了钉死它的必要性。
3. **「260 个泄漏目录已过期（仓库根 0 个）」——错，我查错了范围。** 只查了仓库根 ⇒ 漏了**系统 Temp**（447 个，其中 40 个含 `.db`/`.db.enc`）。⇒ 已进 AGENTS 坑区。
4. **「D-10 未做」——错，已实现**（迁移 0034 + exporter）。**差点派 worker 重做一遍。**

> ⚠️ **本轮还暴露一个流程坑（已进 AGENTS 坑区 + agent memory）**：**派子代理做「基于台账条目」的活之前，必须自己读码核实该条目今天是否仍成立**——不能采信另一份子代理报告的「已过期」结论。本轮我据此**停掉了一个正在做正确修复的 worker**（D-13 形态②），又差点让另一个 worker **删掉 4 条有价值的新用例**。⇒ 事实源永远是代码，不是文档。

### E. 维持登记 / 需外部输入（复算后）

| 条目 | 档位 | 理由 / 需要什么 |
|---|---|---|
| **B-3** 语料 9/9 不可第三方复现 | **维持登记** | 硬约束（内容仓不推 GitHub + dev 库是测试数据）⇒ 第三方复现**在本机不可能**；只登记可达性；结论只能声称「表内自洽」 |
| **乐观锁新失败模式** | **维持登记** | 是**并发语义的产品选择**（冲突时自动重试？自动重观察？），不是工程实现细节；方向安全（多失效而非少失效）；报错文案直接给出 expected/actual |
| **A-1** / **C-1** 零突变覆盖面积 | **维持登记**（口径再订正） | 覆盖面缺口真实；「锚点要人判」是工作量而非外部依赖 ⇒ 可排期切片。⚠️ **零覆盖清单需再订正一次**（本次复算：22 个目录（≥2 文件的 8 个），比台账清单多 11 少 1） |
| **C-4** 不可逆脚本无回滚快照 | **维持登记** | 加事务化/快照是大改；判别口径（中断注入测试证明会留半态）本机可做 |
| **C-6** smoke 无 fast_mode / auto_commit / volumes 挂卷 / revise 覆盖 | **本机可做** | 无外部依赖（smoke 本就是临时库 + 独立端口 + mock provider）⇒ 台账把它记成「不可自决」实为工作量 ⇒ 排期 |
| **C-5** 全量突变套件接 CI | **需外部输入** | 需先在 Linux CI 跑一次 `--all` 拿存活清单。接线方案已可写（新增 `mutation` job + `setup-node` 以免 8 条 vitest 目标 INVALID + **首次必须 `continue-on-error: true`**）。⚠️ 平台障碍已坐实：`l0_pid_alive.py` 的 4 条打的是 Windows 分支 ⇒ Linux 上必假存活 ⇒ 需显式列出要跑的规格文件或加平台字段。⚠️ **另：`ci.yml:140` 自注「CI 历史 47 次 34 红」⇒ 基线本身是红的** ⇒ 新门禁挂在红基线上无意义 ⇒ 需 admin 先核实 CI 实况 |
| **D-5** CI 装不上 xdist | **需外部输入** | 代码侧已修（dev extra 已含 xdist + `ci.yml` 加了 import 自检与 `-n 4`）；但**「并发路径已在 CI 验证」需要 GitHub job 日志**（admin）才能确认 ⇒ 在此之前不得声称 |
| **CI 3.11 腿** | **本机做不到** | 本机只有 Python 3.12.15 ⇒ 环境不具备 ⇒ **如实登记，不得声称已验** |
| **B-1** separation 口径 | **需人裁定** | 复算发现**表内自相矛盾**：AI-BEAT-REPEAT 登记 separation 23.4，按登记 `hits`（2/307）精确算是 **22.0364**（差 6%）⇒ 现有看守**不交叉核对** ⇒ 漏过；且三个条目的 separation 实为「舍入率之比」，只有 AI-PUNCT-ABUSE 是「未舍入对」⇒ **两种定义混用**。AI-PUNCT-ABUSE 的 `hits` 可由「整数命中唯一性」反推（7/110 ⇒ 精确 9.3374 ⇒ 与登记 9.34 吻合），但**分母对应关系靠 note 自述、机器不可证** ⇒ 仍需人点头 |
| **B-2** 外部值带 `source URL` | **本机可做** | 来源研究的 URL **已在仓内**（`CHANGELOG.md:4598`）⇒ 补 `source_url` + **离线形态校验**不需要联网（只有「URL 可达」需要，留人工、不进 CI）；另发现 **1 条 external 是错标**（`0.0030` 其实是本仓对照算子读数，不是外部研究值） |
| **A-3** | **降为口径澄清** | `domain→core` 是**设计允许方向**（domain 用 core 基础设施）⇒ 不需要禁止型看守；原句「这两条边无机器约束」已不成立 |
| **D-12** 删角色留孤儿 relationships | **检测已实现；清理需授权** | 只读诊断 `scripts/check_orphan_relationships.py` 就绪；**本轮实测存量 0 行** ⇒ 无需清理迁移 |
| **Temp 残留 151 个 `novelos_openapi_*`**（+ 少量 probe 目录；⚠ **该数随时间漂移**，登记值取实测快照） | **需管理员** | **ACL 拒绝访问**，不是 junction、不是占用：`ATTR=Directory`、`LINKTYPE` 空；`ls` 报 `Permission denied`、`Get-Acl` 抛 `UnauthorizedAccessException`、`icacls` 报「拒绝访问」、`takeown` 报「拒绝访问」⇒ **当前用户无法取得所有权**。本机唯一可用的可恢复删除通道（`mavis-trash` → VB `FileSystem::DeleteDirectory`）**必须先枚举目录**⇒ 枚举被拒 ⇒ 报「This function is not supported on this system」。⇒ **处置**：① 危害**低**（空目录 / 构造 schema 用的临时 data 目录，**不含凭据**——真正含明文密钥副本的 40 个 `novelos_backup*` **已全清**）；② 需管理员执行 `takeown /f <dir> /r /d y` + `icacls <dir> /grant %USERNAME%:F` 后再删；③ **生产侧已不再新增**（见 §A-19），**测试侧泄漏已修**（见 §A-18）⇒ 存量是一次性的，**不再增长** |
| **`--migrate` 路径缺写前备份** | **已修**（对抗审查 B · F-3） | 原症状：F-1 的修复给 `--prune-dangling` 加了「落盘前强制备份 `secrets.json.bak-<ts>`」，但 **`migrate_secrets()` 没有这层备份** ⇒ 迁移写 `secrets.json` 时**没有旧版本可回退**；且迁移会用**库内明文**覆盖 `secrets.json` 的**同名条目** ⇒ 若用户在 `secrets.json` 里改过 key、库里还留着**旧**明文副本 ⇒ 迁移会把**较新的值覆盖成较旧的**。⇒ **修法**：`--migrate` 写盘前**也调** `_backup_secrets_file`（同目录 + 原子）；同名且值不同 ⇒ **保留 `secrets.json` 里的值**（运行期本就以它为准，优先级更高）+ 记 `[冲突]`、**不静默覆盖**（库内明文照常清）；CLI 打印备份路径与冲突。**判别力**：迁移前放一个同名条目且其值与库内明文**不同** ⇒ 迁移后 `secrets.json` 里仍是**较新的值**、备份里能取回**迁移前**的值（用例已入 `tests/unit/test_check_content_repo_guard.py`，**28 → 32 例**） |

---

### B5. 对抗审查员 B 的 findings 与 Lead 裁决（**第三轮** · 2026-10-23 晚）

审查对象：`945d74d`（第二轮修复批的 6 个提交）。审查员**只读**、在**仓库外**沙箱做隔离实验；结论一律当**待验证假设**。

> ⚠️ **本轮有一条是 Lead 亲自复现后才采纳的**（major，见下）——**没有**采信审查员的转述。
> ⚠️ 本轮还**混进了一条用户报的真 bug**（删除旧模型档案 409），已一并在本批修掉。

| # | finding | 定性 | 处置 |
|---|---|---|---|
| **1（major）** | **归属闸门可被「别名型键」伪造**：`known` 集合把**实体身份**（`profile_id`/`connection_id`）与**槽位别名**（`provider`/`env_name`）混成同一并集 ⇒ 交集闸门拿别名当归属证据 ⇒ **错库 + 一个 provider 级键 ⇒ 真凭据 `mprof_real` 被整条删除、退出码 0、结论「合规」** | 🟠 **major**（Lead 亲自复现成立） | **采纳**。拆两集合：`entity_keys`=实体身份（**归属判据**）／`known_keys`=含别名（**悬空判据**）。**Lead 独立复现矩阵**：错库+provider 级键 ⇒ 修前 exit 0 且真凭据被删 → 修后 exit 2 且文件逐字节不变；错库+env 级别名键同形；对照组（纯实体键 / 正确库）**行为不变** |
| **2（minor）** | `--force-all-dangling` 解锁不了「已知集合为空」⇒ **文档承诺的能力不存在** | 🟡 minor | **采纳**。force 语义**统一**为「**归属确认**」⇒ 归属闸门一律放行（零交集 **与** 空库都放行）⇒ 「只剩死键」场景真能清 |
| **3（minor）** | `--migrate` 不受归属约束 ⇒ **错库的明文 api_key 被写进真 secrets.json**（跨项目凭据混入） | 🟡 minor | **采纳**。migrate 新增**独立**的**归属闸门**：`api_keys` 段非空 ⇒ 要求实体级交集 ≠ ∅，否则**拒绝执行**（exit 2、零落盘、零备份、不清库内明文）；段为空 ⇒ 首次迁移放行 + note。⚠️ **「加法动作不受『悬空』闸门拦截」这条既有原则不变** |
| **4（minor）** | `--all --changed --jobs N` 且**改动集为空** ⇒ 早退 `return 0` ⇒ **并发档位的「退出码恒为 3」被绕过**，且无档位行 | 🟡 minor | **采纳**（**Lead 自办**）。抽 `_tier_lines()` + `_tier_exit()` ⇒ **一切非用法错误（≠2）的结束路径**都经统一出口 ⇒ 无旁路；**7 条新用例**（12 → 19），**2 处变异自证**（撤空集出口 ⇒ 1 红 / 撤预检出口 ⇒ 1 红） |
| **5（minor）** | 性能带的**结论行**仍是旧值（2.2~2.9× / ≤3.9%）且台账**完全没有**本轮订正 ⇒ 同一文件自相矛盾 | 🟡 minor | **采纳**。两处结论行同步订正 + **标注旧值作废**；另把两处把「15%~87%」当**同条件方差带**引用的表述改成「**跨上下文不可比**」 |
| **6（minor）** | 巡检与生产的「可用键」口径分裂：`{"api_keys":{"mprof_x":""}}` ⇒ 巡检报「合规 + 1 键」，生产 `load_api_keys` 实际 **0 键** | 🟡 minor | **采纳**。采集处按**生产口径**分流（可用键 / 值畸形 / 总条目数）；值畸形 ⇒ **判不合规（exit 1）且可见**，但**绝不**判悬空 ⇒ 绝不会被 prune 删掉；⚠️ 反向不变量（api_keys 段不存在/null/空 ⇒ 合法空状态 ⇒ exit 0）不变 |
| **7（用户报）** | 「添加了新模型并绑定后，删除老的模型档案就报 409」 | 🟠 **真缺陷（UI 侧）** | **Lead 读码定位三层事实** ⇒ 409 **本身正确**（档案真在后备位、运行期真会用到）；**缺陷在 UI**：候选链只渲染 `profile_ids[0]` ⇒ 后备位**不可见**、**不可移除**（唯一写路径是「整链替换」且选中原值不触发 onChange）⇒ 可解的死结被做成不可解；附带 409 的 detail 是对象而被 `JSON.stringify` 成原始 JSON 串 ⇒ **修法见 CHANGELOG 本批 §1** |

#### B5-1. Lead 对 worker 交付的**打回意见**（3 条，全部已修）

| 意见 | 症状 | 处置 |
|---|---|---|
| **R1** | force 的醒目提示在**动作之后**丢失（动作后会重新采集 ⇒ 重新采集的结果不能代表「动作发生时」的口径）；且 force 迁移把错库的实体 id 写进文件 ⇒ 重新采集反而显示「**归属: 已验证**」⇒ **误导** | **动作前冻结**归属口径、动作后按动作前口径渲染；提示同时出现在**头部**与**动作行**；JSON 顶层给**动作前**口径 |
| **R2** | 脚本被改到 **1574 行**，越过「>1500 行的既有文件要拆」的硬规则 | **拆**：门面 `scripts/check_content_repo_guard.py`（**644** 行）+ 实现 `scripts/_guard_secrets.py`（**1040** 行）⇒ 两者都 < 1500；CLI 形状未变 |
| **R3** | prune 的文案仍写「已知集合为空」走 `blocked_reasons`，与新结构（空集合已移到**归属闸门**）不符 | 两条拒绝路径各说各的（读表失败 / 归属无据），docstring 明写空集合**不走** blocked 那条 |

#### B5-2. 本轮 Lead 先验判断被推翻（2 次，均属**我自己的期望写错**）

1. **「S10 的别名键应被保留」——错**：库里 `mprof_real` 的 provider 是 `minimax`，`openai_compatible` **不在 `known_keys` 里** ⇒ 它对运行期确实解析不出来 ⇒ **它就是悬空键、应当被删**。我最初写反了 ⇒ 修正矩阵期望后重跑。
2. **「S16 迁移后应 exit 0」——错**：force 放行了迁移（写盘成功）⇒ 但文件里仍有 1 个悬空键 ⇒ 结论「不合规」⇒ **exit 1**（不是 0）。

⇒ 两次都是**我的 oracle 写错**，**不是实现错**；是靠**自己另写一套独立矩阵脚本**（不复用 worker 的自报）才发现的 ⇒ 这条本身就是「Lead 不采信子代理结论」纪律的又一次实证价值。

#### B5-3. 残留边界（如实登记，未修）

- **只有别名型键的 secrets 文件**（无实体 id 键）⇒ 即使 `--db` 指对了库也判「归属无据」⇒ 清理被拒 ⇒ **需显式 `--force-all-dangling`**。**这是有意的取舍**（宁可多一次确认，也不要拿错证据删凭据）。
- **force 已被日常化**：本批有 **2 条既有用例**被迫加 `--force-all-dangling`（它们的夹具只有 provider 级键）⇒ 危险开关的使用面在扩大 ⇒ **建议后续补一条「删除比例闸门」**（待删数 / 文件键数 ≥ 阈值 ⇒ 判不可判）作第二道保险。
- **「归属无据」的优先级高于「值畸形」**：可用键既非实体 id、又存在值畸形条目 ⇒ 归属闸门先拦 ⇒ exit 2（不是 1）。
- **并发写仍是 last-write-wins**（前端乐观覆盖 + 后端无版本号）：两人同时改同一条链会互相覆盖。
- **`secrets_key_count` 的语义变了**（现为「可用键」数）；仓内无其它消费方，若外部有消费方按「总条目数」读，须改读 `secrets_entry_count`。

---

## 五、跨批留痕


- **Lead 先验判断被推翻 3 次**（均已入 CHANGELOG/AGENTS）：title 契约（at `director_planner-v3.md:108` 是输入契约）、行数 384（量具失真，真值 577）、审计 A 的 5 条断链（全误报）。
- **流程违规 1 次（Lead 自报）**：开工前未经用户批准 `push` 批次十九~二十三 6 个提交；此后 **push 一律先问用户**（当前批次 24~28 全部只提交、未推送）。
- **能力/口径边界（须持续遵守）**：零真实 LLM 调用（故 v4 起名质量、实时 prompt 行为均未验证）；零内容仓接触；突变实跑与全量 pytest 必须由 Lead 串行（成员只跑 precheck / 定向）。

---

## 六、测试瘦身遗留（2026-10-24 凌晨登记 · 批次三十三收尾时移交）

> **来源**：用户「测试太重了」审查（`docs/impl/reviews/test-slimming-review-2026-10-23.md`）后分两批执行（三十三之一 / 之二，提交 `5b59d2e` / `e314833`，均已推远端）。
> **本节是「已做 / 未做」的持久登记** —— 会话内的共享任务板（task-12/13/10）**不跨会话**，故此处落盘。

### 6.1 已完成（有实测闸门）
- contract 模板族 **13/16 已收敛**：16 文件 **9,777 → 9,429**；共享 `tests/api/_contract_runner.py`（6 个断言单点，**刻意不提供**从模型自动取期望的入口）+ `_rich_fixture.py` + **runner 自己的看守 20 例**；`reveal` 补 11 键硬编码基线（抓「两侧同时收窄」与「可选字段回填 null」两个盲格）。
- 测试建库样板：`tests/_db_fixtures.py::migrated_db`（**每例新库、不共享**），**14 文件 / 18 处**替换，批量探针「路径/类型/表集合」18/18 PASS。
- 前端 `ProjectInitPanel.test.tsx` 2381 行 / 39 例 → 3 份（613/1211/565），原文件删除、用例守恒。
- 闸门：全量 pytest **4379/1/1**；`--all --precheck` 477/0；**指向被改 contract 文件的 12 份规格串行实跑 0 存活**；判别力抽检 6 failed→还原绿。

### 6.2 未完成（下一轮直接接）
| # | 项 | 位置 | 目标 / 判据 | 风险 |
|---|---|---|---|---|
| T-1 | contract 剩余 3 项 | `test_workflow_run_response_contract.py`（11 处多行键集样板，现仅 helper 层）/ `test_misc_router_response_contract.py`（801 行未动）/ `test_quality_reference_list_response_contract.py`（774 行未动） | 16 文件 **9,429 → ≤9,400**（差 29） | 低（协议已跑通 13 次） |
| T-2 | helper 档② | `_app`/`_client`（`lifespan_context` + `ASGITransport`）⇒ 先吃 `tests/unit/_shared.py` 的 `_make_settings`/`_db` 族与 `tests/integration/_shared.py::_create_app` | 这是「helper 50,220 → ≤35,000」的大头 | **中**：新文件若照抄工厂默认 + `beforeEach` reset 集不成对，会出「静默夹具契约不等价」 |
| T-3 | helper 档③ | `_seed_*` **同域**合并（backup / content_sync / genre 各归各；**跨域不合并**） | 同上 | 中：请求体逐字比对是红线 |
| T-4 | 前端 `ChapterDetailPage.test.tsx`（3776 行 / 96 例）拆分 | 5 份按 describe 分组（plan 13 / runs 21 / pipeline 17 / gates.error 19 / gates.flow 23） | 每份 <1500 行；vitest 853 例守恒；Duration 目标 ≤7s | **高**：本仓已尝试**一次并回退**——切出 `runs` 21 例后 4 例红（横幅节点位缺席），真因是**原 21 例依赖 mock 工厂里的默认实现**，而新文件只搬了 `beforeEach` 的 reset 集 ⇒ **必须「工厂默认 + reset 集」成对搬**，且**先单跑新文件 21 passed 再删原文件的块** |
| T-5 | 跳过清单（勿再尝试） | 141 处 `apply_migrations(db_path, <显式迁移目录>)`（含 52 处在 `test_migrations.py` 等专门测迁移的文件里）、6 处形态不同（含一处自定义 fixture 就叫 `migrated_db`） | — | — |

### 6.3 三条可复用的教训（本轮新增）
1. **体积指标一旦定成「某个文件子集的行数」，就会被「搬出行数」骗过去** —— 本轮靠「`tests/**` 总量」这条反制才发现 +663。今后定体积目标必须**同时**定「总量口径」与「永久资产口径」（新增守卫测试算资产、不算肥肉）。
2. **抽共享断言/夹具时，被抽走的能力必须补看守** —— `_contract_runner.py` 一度零测试却承载 42 条规格的判别力（改一行可让 42 条同时 SURVIVED，而 `--precheck` 抓不到）。
3. **测试文件拆分是「夹具契约搬运」而不是「文本搬运」** —— 拆 3776 行文件失败的那 4 例，全是因为只搬了代码、没搬「mock 工厂默认值」这层隐式契约。
