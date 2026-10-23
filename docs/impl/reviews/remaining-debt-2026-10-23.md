# 全面 review 未修项台账（2026-10-23）

> **来源**：2026-10-23 全面 review（三路只读审计 A/B/C）。**已修项**见 CHANGELOG 批次 24~28；
> 本文件只登记**明确不做 / 待排期**的项，每条带「为什么不做」与「要做时的判别」。
> 排序 = 风险从高到低。**凡本文件项被修掉，须在此标注批次号并移入 CHANGELOG。**

## 一、审计 A（架构 / 契约 / 文档 / 量具）

| # | 项 | 现状证据 | 为什么现在不做 | 要做时的判别 |
|---|---|---|---|---|
| A-1 | **25 个包目录零突变规格**（`agent_runtime` 6 文件 2104 行 / `content_sync` 6/2069 / `project_init` 2/1491 / `delivery` 5/1448 / `signing_check` 3/1108） | 227 个 .py 中 72 个有自身规格 | 面积大、逐模块锚点要人判；本轮已补 `structured_output`（l16）、genre（l18）、证据表（l17/l19） | 按「改错会静默」排序逐个补；每个新规格必须做到「撤修复必红」 |
| A-2 | **`_COUNTER_MODULES`（10 项）仍是纯计数型手挑清单** | `tests/unit/test_context_peek_convergence.py:49` | 与批次二十四的「固定清单」族同形；改它要重设断言口径 | 清单项加**存在性双向断言**（找不到模块即红），或改断言口径 |
| A-3 | **`domain→core = 72`、`core→domain = 136` 双向耦合无看守** | 审计 A 实测；模块级 2-环 = 0 | 属「基础设施方向」（只用 ids/db/story_state/logging_config）+ HTTP 层调领域服务，无环 ⇒ 判设计而非破口 | 若将来出现环，再立看守；当前只登记「这两条边无机器约束」 |
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
| C-1 | **19 个脚本仍零突变规格** | 12 有 / 19 零（审计实测，订正了板上「8 有」的旧数） | 逐个补成本高；本轮先修了 4 条 major | 按「改错会静默」排序：backup_db > content_sync > open_volume > produce_chapters > smoke_e2e > inspect_stalled_runs > export_openapi/gen_frontend_types |
| C-2 | **`export_openapi.py` / `gen_frontend_types.py` 无直接测试、CI 不跑** | 契约测试只守 interface 名集合 | 生成内容（字段类型/可选性）漂移需新判别 | 判据：拿生成物与 `app.openapi()` 逐字段比（不只比 path/schema 名集合） |
| C-3 | **`eval_regression.py` / `m1_report.py` 默认写仓库内 `docs/`** | `eval_regression.py:108-131`、`m1_report.py:770` | 属「默认值体验」问题，不影响数据安全 | 加 `--out` 默认改临时目录 + 显式 `--write-docs` |
| C-4 | **不可逆脚本无回滚快照**（`open_volume` / `world_switch` 靠幂等续跑） | 两处源码自注「中断点前已落库」 | 加事务化/快照是大改 | 若要改：先做「中断注入测试」证明当前会留下半态 |
| C-5 | **全量突变套件仍未接 CI**（只有只读预检接了） | `ci.yml` 自注「未在 Linux dry-run」 | 12.7 分钟/轮 × 每次 push 不可接受；且平台相关突变未筛 | 先在 CI 跑一次 dry-run 拿「Linux 上存活清单」，再决定接线或加平台判定 |
| C-6 | **smoke 无 fast_mode / auto_commit / volumes 挂卷 / revise 覆盖** | `scripts/smoke_e2e.py` | 扩展要起服务 + 多 run 编排 | 至少补 volumes 挂卷（批次二十四的 `volume_scope` 现无 e2e） |
| C-7 | **真实 openssl 往返从未实跑** | 测试用 fake openssl（monkeypatch `subprocess.run`） | 会写真实备份目录（受限动作） | 在临时目录跑一次 `encrypt→decrypt→PRAGMA integrity_check`（不得碰真实备份） |

## 四、跨批留痕

- **Lead 先验判断被推翻 3 次**（均已入 CHANGELOG/AGENTS）：title 契约（at `director_planner-v3.md:108` 是输入契约）、行数 384（量具失真，真值 577）、审计 A 的 5 条断链（全误报）。
- **流程违规 1 次（Lead 自报）**：开工前未经用户批准 `push` 批次十九~二十三 6 个提交；此后 **push 一律先问用户**（当前批次 24~28 全部只提交、未推送）。
- **能力/口径边界（须持续遵守）**：零真实 LLM 调用（故 v4 起名质量、实时 prompt 行为均未验证）；零内容仓接触；突变实跑与全量 pytest 必须由 Lead 串行（成员只跑 precheck / 定向）。
