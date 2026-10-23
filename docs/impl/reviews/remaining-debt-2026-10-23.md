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
| **D-0** | **🔴 内容仓仍挂 GitHub remote 且本地领先 17 个提交** | `git -C D:/zcodeproject/NovelOS-Content remote -v` ⇒ `origin https://github.com/charlesnty612/NovelOS-Content.git`；`rev-list --count @{u}..HEAD` = **17** | **隐私优先**：一次误 push 即发布全书 | 摘 remote 或加 `pre-push` 守卫（拒推非白名单分支）；判别：内容仓 `git push --dry-run` 被拒 |
| **D-1** | **🔴 操作痕迹落在软件仓 `docs/evaluation/` 且已上 GitHub** | `git ls-tree -r --name-only origin/master -- docs/evaluation/` ⇒ 含 `m2-judge/judge_results.json`（真实 chapter_id + 逐章 verdict）、`m1-data.md`（真实 project_id + 逐章评分）；`git check-ignore -v` ⇒ **rc=1 未被忽略** | **已不可撤回**（远端已有）⇒ 只能记录 + 防再犯 | 痕迹迁至 NovelOS-Artifacts；`docs/evaluation/` 加 ignore 或只留机制文档；判别：`git ls-files docs/evaluation/` 无真实 id 文件 |
| **D-2** | **🔴 备份「有写无读」：无 restore 工具 / 无 runbook / 恢复可静默失效且假绿** | 实测：残留 `-wal`/`-shm` 时恢复后 49 表 → **1 表**、`projects` 表不存在，而 **`integrity_check=ok`** | 属「新增能力」，且触碰不可逆动作（恢复会覆盖库） | 先落 `scripts/restore_db.py`（temp+rename 原子替换 + 删 `-wal`/`-shm` + 恢复后 `integrity_check` + 表数核对 + 故意失败不碰原库）；判别：临时目录往返 + 故意失败后原库完好 |
| **D-3** | **🔴 解密失败仍会破坏活库** | 实测：openssl exit 1，但目标文件**已被截断重写**（8192 → 356336 B，`database disk image is malformed`） | 同 D-2 | 恢复必须原子写（临时文件 + rename），失败即不动原库 |
| **D-4** | **🔴 `backup_db.py` 默认明文备份 + 加密后从不验证密文可解 + 密钥缺失静默轮换** | `--encrypt` 是 `store_true`（默认关），else 分支 `shutil.move` 落明文；`integrity_check` 在**加密前**的明文副本上做；`ensure_key_file` 缺文件时**静默生成新密钥**（实测 key `ab1d0551`→`c53b90f5`，旧密文 exit 1 `bad decrypt`） | 与既有策略「密钥进私有网盘」直接冲突，且改动涉及不可逆动作 | `--encrypt` 改默认开、`--plaintext` 显式 opt-in；加密后做解密自检；密钥缺失**报错退出** |
| **D-5** | **🔴 CI 装不上 `pytest-xdist` ⇒ windows 并发路径 job 从未真正跑过** | `pyproject.toml:36-40` `[optional-dependencies].dev` 只有 pytest+ruff，xdist 在 `:66-70` `[dependency-groups]`；已装 METADATA 的 Requires-Dist 无 xdist；`ci.yml:116-117` 装 `pip install -e ".[dev]"`；`ci.yml:122` 用 `-n 4` | 依赖面改动小，但 CI 需重跑验证 | 把 xdist 挪进 `[project.optional-dependencies].dev`；判别：CI windows job 绿 + 该步输出 `4 workers` |
| **D-6** | **🔴 质量门 fail-open：读不到草稿 ⇒ 该章被判「满分 0 issue」**（**critical**） | Lead 端到端复现：`drafts` 表不可用 ⇒ `resolve_draft` 返回 None（不抛错）⇒ `draft=""` ⇒ `score_style` ⇒ **score=100 issues=0**；门禁消费点 `chapter_commit/gate.py:473` | 需设计「读失败」与「无草稿」的区分（抛域异常 or 返回哨兵） | `resolve_draft` 抛 `DraftResolutionError` ⇒ 上游 fail-closed（5xx / 不产出报告）；判别：临时库 `DROP TABLE drafts` ⇒ 期望 5xx 而非 100 分报告，且新用例突变验证必红 |
| **D-7** | **🔴 director 装配缓存键 5 处漏维度（第 6/7 次同形状）** | Lead 实测：改伏笔台账 / 改 director 侧章标题 ⇒ **不重装、payload 不变**（对照组「改项目前提」正确 miss）。缺 `hooks` / `narrative_debts` / `plot_events`+`branches` / `foreshadow_overdue_chapters` / director 侧 `chapters.title` | 同形状已复发多次，**只补键维度是治标**（写路径仍不推 `state_version`） | 补键维度 **+** 让 `domain/ledger` / `domain/plot` / `domain/project` / `domain/chapter` 的写路径推 `state_version`；判别：新用例断言打在 **payload 内容**上（照 `test_ctx_b_meta_title_key.py` 口径）+ 反向「未改必命中」不变量 |
| **D-8** | **批次三十的 CLI 失败退出码全仓无看守** | 把 `export_main`/`gen_types_main` 换成恒返回 0 的替身 ⇒ **8 条用例全绿**；`grep -rn '"--check"' tests/` 只命中两个只对**已同步产物**调用的辅助函数 | 未提交的工作树，顺手返工成本最低 | 加 1 条「CLI 对漂移副本返回非 0」用例 + 1 条打 CLI `return` 行的突变；顺带修 `l21` 规格「必红用例」表错行、`--check` 临时目录泄漏、CWD 相对默认路径 |
| **D-9** | **commit 风控门两条子句零可区分夹具 + 该文件无突变规格** | `_has_high_risk_change`（`pipeline_common.py:518`）的 `facet=="definition"` / `world_kind=="rule"`：workflow 侧**无任何夹具**用这两个值（带 `facet` 的夹具全是 `"state"`）；**Lead 插桩量化**：`tests/workflow`+`tests/api` 全量跑完该函数被调用 **41 次**，`facet=='definition'` **0 次**、`world_kind=='rule'` **0 次** ⇒ 删掉两条子句，41 次调用的判定结果**一次都不会变** ⇒ 无任何测试会红。另：`facet:"definition"` 只在 **API 侧孪生**（`deltas.py:122`，被 integration 测试钉住）⇒ 双实现、覆盖不对称。⚠️ 插桩坑：`commit.py` 是 `from .pipeline_common import _has_high_risk_change`（导入时绑定）⇒ 只补 `pipeline_common` 属性**拦不到**，必须同时补调用方模块 | 补夹具成本极低 | 补 2 个夹具（definition / world_kind=rule）⇒ 断言「命中即要人工批准」；配突变规格撤子句必红 |
| **D-10** | **外发投稿包取「最新 draft」而非「已 commit 的那版」** | `exporter/builder.py:122-123` `ORDER BY version DESC LIMIT 1`；`commits` 表**无 `draft_version` 列**（全仓 `draft_version` 只在 `quality_reports`，迁移 0024）⇒ 章 reopen 后改稿，外发带未过审稿 | 需先给 `commits` 加列（迁移） | 迁移加 `commits.draft_version` ⇒ exporter 取已 commit 那版；判别：COMMITTED 后再改稿，外发包正文仍是旧版 |
| **D-11** | **`content_sync` 三条静默形态**（零突变覆盖） | ① 改标题后旧 txt **永久残留**（实测 `第1章-OLD_TITLE.txt` + `第1章-NEW_TITLE.txt` 并存，无清理逻辑）；② 缺 `volumes`/`characters` 表 ⇒ 整次 push 抛未捕获 `OperationalError`；③ `story_state/<v>.json` 按 v 存在即跳过 ⇒ 原地修过的快照永不回灌 | 需定义「旧文件清理」与「缺表降级」的产品口径 | 断言：改标题后 `novel/` 只含新文件（撤清理必红）；缺表时 push 不抛异常且 `errors` 非空 |
| **D-12** | **删角色留孤儿 `relationships` 行** | 迁移 0022 移除 `relationships` 的 character FK + `character/service.py:308` 删角色不查引用 ⇒ 实测留 1 行孤儿，而 **`PRAGMA foreign_key_check` 报不出来** | 与 `world/service.py:544` 的处置不一致 | 删前查引用并抛 `ReferencedError`；判别：临时库建角色+关系 → 删角色 ⇒ 期望抛错 |
| **D-13** | **`signing_check` 读题材包失败 ⇒ 题材开篇整段静默消失** | `service.py:133-134` `except Exception: return None` ⇒ 无告警、无 errors 字段 ⇒ 报告外观「全通过」 | 同文件 `word_band_rejected` 已有显式标记 ⇒ 处置不一致 | 加 `genre_opening_unavailable: True` 标记；判别：注入读失败 ⇒ 报告必须显式标注不可检 |
| **D-14** | **`*.db.enc` 不被 gitignore 覆盖** | `git check-ignore -v backups/x.db.enc` ⇒ rc=1；`.gitignore:15` 只有 `*.db` | 条件性（需有人把 `--out` 指到仓内） | `.gitignore` 补 `*.db.enc` / `*.enc` |
| **D-15** | **AGENTS.md 硬规则 5 字面口径比实现绝对** | `genre_packs.payload_json` 含 `forbidden_words`(4) / `redlines`(3) / `taboo_notes`(88 字)，而 schema 明写「只承载机制可呈现的要点」 | 属措辞问题 | 把该句改成「审核红线**正文**只进内容仓，软件层只承载机制可消费子集」——**不要改实现** |
| **D-16** | **版本一致性机器看守只覆盖 6 处中的 3 处** | `check_skill_package.py` 只校 pyproject / claude-plugin/plugin.json / SKILL.md；**web 三处（package.json / package-lock.json / uv.lock）全仓 0 命中** | 与批次十九「SKILL.md 静默停在 3.9.0」同族 | 补齐 web 三处；判别：任一处改错 ⇒ 检查红 |
| **D-17** | **嵌套 pytest 继承本机默认 basetemp ⇒ 本地基线不可复现** | 本机 `%TEMP%\pytest-of-AiStation` 一旦创建即**连 owner 都拒绝访问**（`icacls` 拒绝列举）；本仓两处测试会**再起 pytest 子进程**且不隔离临时目录 ⇒ 子进程 exit 1 ⇒ 外层红。实测：全量 **3900 passed / 2 failed**（CHANGELOG 批次三十声称 3902 passed / 0 failed） | 环境缺陷，但**暴露了测试非自足**；CI 在 Linux 上看不见 | 两处子进程 spawn 加 `--basetemp`；判别：全量 0 failed 且不依赖默认临时目录 |
| **D-18** | **活库仍有明文 api_key，`secrets.json` 条目悬空** | 活库 `model_profiles` **2 行**带非掩码真值 api_key；`model_connections` 0 行；`secrets.json` 唯一条目 `mprof_f95e3c7b4127` 与两个现存档案 id **均不匹配** ⇒ 解析链落到第 4 级 `params_json["api_key"]` | 需改库内数据（属用户凭据）⇒ 需用户确认 | 把两个档案的 key 迁到 `secrets.json` 对应键并清库内明文 + 加「悬空条目启动告警」；判别：库内明文计数 = 0 且解析仍返回非空 |
| **D-19** | **`NOVELOS_DEBUG_PROVIDER_DUMP_DIR` 落盘目录不在 gitignore 覆盖内** | 该 env 会把 LLM 响应全文（=章节正文）落盘；`git check-ignore` 对非 `data/`/`tmp/` 路径返回 NOT-IGNORED | 低频 env，但一旦开就是正文泄漏入口 | `.gitignore` 补 `provider_dumps/` 或脚本拒写仓库根下非 `data/`/`tmp/` 路径 |

---

## 五、跨批留痕

- **Lead 先验判断被推翻 3 次**（均已入 CHANGELOG/AGENTS）：title 契约（at `director_planner-v3.md:108` 是输入契约）、行数 384（量具失真，真值 577）、审计 A 的 5 条断链（全误报）。
- **流程违规 1 次（Lead 自报）**：开工前未经用户批准 `push` 批次十九~二十三 6 个提交；此后 **push 一律先问用户**（当前批次 24~28 全部只提交、未推送）。
- **能力/口径边界（须持续遵守）**：零真实 LLM 调用（故 v4 起名质量、实时 prompt 行为均未验证）；零内容仓接触；突变实跑与全量 pytest 必须由 Lead 串行（成员只跑 precheck / 定向）。
