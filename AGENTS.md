# NovelOS 项目记忆（AGENTS.md）

> 本文件是跨会话的项目记忆（方法论 `06-工程记忆` 的约定区+坑区载体）。
> 时间线/闭环留痕在 CHANGELOG 与 docs/roadmap/；一致性矩阵见文末。
> 规则宁少勿滥，每条挂实证依据；被修掉的坑要标注，不做防御性考古。

## 一、定位与受众

本地优先的小说写作 OS（单用户本机自用，2026-09-11 用户裁决：**纯自用工具**——CLI/SKILL.md、LICENSE、Docker 不做，转公开发布时 LICENSE 为硬阻塞需重估）。
章节生产闭环：plan → write → review → commit；SQLite 唯一事实源 + Story State 快照版本化。

**裁决授权（2026-09-13 用户授）**：工程实现类裁决——含推翻既有小拍板（如 1722px 定高改自适应，
首例已执行 `70ee277`）——由主会话自主拍板，按「先验判断被推翻必须留痕」规矩记录即可，不逐项等用户。
边界：**产品定位级**（转公开发布、LICENSE、题材方向选择、内容创作决策）仍须用户拍板。

## 二、架构约定（分层与依赖方向）

```
apps/web/          前端（React18+TS+Vite；types.ts 手写为权威，types.generated.ts 是 drift 对照面）
packages/core/     机制层：api(routers) / agent_runtime / context_engine / genre / quality /
                   story_state / workflow_runtime(engine) / model_router / simulation /
                   exporter / content_sync / signing_check / backup …
packages/domain/   领域服务：project / chapter / character / world / plot / hooks …
packages/workflows/ 工作流组装：chapter_plan/write/review/commit + deconstruct_book 等
                   （只有这里能 import 上层；core 不得 import workflows——有专门测试看守）
database/migrations/ 唯一 DDL 来源（0001~0026；表数口径：业务表 38 / 含 _migrations 39）
docs/state-model/schemas/  运行时依赖的 JSON Schema（genre-pack v1.x / state-delta 等）
```

硬规则（每条都有测试或事故证据）：

1. **巨型文件拆门面**：新增模块不得超 1500 行；>1500 行的既有文件拆「多模块 + 门面再导出」（builders 3678→7 先例）——**存量挂账已于 2026-09-13 V4.0 批次全部清零**：builders_common.py 2104→四模块包（excerpts/ledger/summaries/common+门面）、ProjectInitPanel.tsx 1858→227 主文件+12 子组件、routers/workflows.py 1675→四域包（control/runs/revise/common）；门面 `__all__` 收缩至 30 个有消费方名（深路径 import 全保留，F401 由 pyproject per-file-ignores 承接）；门面 `__all__` 不得含自指元素；缓存模块改原地 `clear()` 不得 `global` 重绑定（dict 旧引用失联事故）。
2. **装配缓存键纪律**：凡进 payload 的装配参数必须入键（author_intent/target_word_count/genre_pack_ref 教训——漏键=脏命中）；命中返回深拷贝、写入存拷贝；preview 与生产拆命名空间；主 JOIN 与降级路径的键形态必须逐字同形；失效以 state_version 为主线 + commit 后显式兜底。
3. **新资源类型范式**（genre_pack 先例）：自有表 + 自有 schema 版本线（`^<name>.v1.\d+\.\d+$`，minor 兼容）+ `additionalProperties:false` 守「软件层只承载消费子集」+ CRUD router + 项目绑定 + builder 注入 + 缓存键指纹；内容资产正文一律不进软件仓（三仓分离）。
4. **质量口径**：error 分 blocking/informational（白名单 `quality/issues.py:BLOCKING_RULES`）；**severity 矩阵变更必须同步三处文档 + formula_hash 变 + 回归测试**；评审类约束（题材核销/GENRE-*）恒 warning 不进白名单。
5. **软件/内容/痕迹三仓分离**：运行 payload 存 DB；人编辑面在 NovelOS-Content（10 维文件=编辑面，pack.json=运行面，双轨不自动聚合）；操作痕迹在 NovelOS-Artifacts。审核红线只进内容仓。**内容仓不推 GitHub**（2026-09-14 用户裁决：本地 git 历史照留作审计迹，push 停止；已上云历史不动）。
6. **迁移**：只加不改；新列可空兼容存量；迁移清单测试（test_migrations）随新增同步。
7. **文档引用纪律**（R5 检修根因：38ec703 三仓迁移未回填引用造成 ≥14 处断链——反引号路径引用 markdown checker 查不出，只能靠人工/代理抽查）：引用 NovelOS-Content / NovelOS-Artifacts 的文件必须写清仓库名；引 docs/ 内文件前先确认未被迁空（9 个空目录是迁移残留）；旧计划/旧报告类文档必须带「历史注记」横幅（日期+状态+被什么取代）。
8. **环境同步纪律**：改 `pyproject.toml`（尤其 version）后必须 `uv sync --extra dev`（ruff/pytest/xdist 在 dev extra 里，裸 `uv sync` 会 pruning 掉 ruff）——editable 元数据不刷新时 `/api/health` 的 version 腿对外报旧值，且自洽断言抓不住（R5 实证）。
9. **「数据源存在 ≠ 已进装配」**（2026-09-15 书1 全弧跑偏事故的根因，新形状）：任何被当作**权威依据**的字段，必须有测试钉住它**真的进了消费方 payload**——仅「表里有值」不构成生效。本次实证：`chapters.plan_json` 的 `chapter_goal / key_beats` 从未进 `build_director_input` 的 `chapter` 段（只被当 FTS 召回语料 + 缓存指纹），于是五版大纲补丁（v5~v9）全部空转，只有 `title` 生效，正文由 planner 顺着 story state 惯性另写一套——**跑道上的大纲与产出的正文完全脱节，且全程无告警**。
   配套两条纪律：
   - **一列不得两用**：策展面（stable，人/脚本写）与生成面（generated，workflow 覆盖写）必须分列。`plan_json` 曾同时是「init 的大纲」与「planner 的输出」，planner 的白名单覆盖把大纲静默销毁；现拆为 `chapters.outline_json`（策展，chapter-plan 不写）与 `chapters.plan_json`（生成）。
   - **检测算子的覆盖面必须与规则名相符**（2026-09-15 外部研究移植批次）：正则/统计算子匹配的是**字面形态**，
     不解析语义——算子过宽会把正常写法计入，而频率数字本身不提示这个落差。来源研究
     （lieflat-less-ai-tone，283 万字对照）公开的六次测量失误**全部是这一形状**，对策是硬性的：
     **任何算子在采信频率结果前，先抽样检视 20 条命中实例**。本仓首轮即抓到三处：
     「段首零回指评论」算子实际测的是「短句独立成段」（改名 AI-SHORT-PARA）、
     「过长前置定语」97 条命中 95 条误报（整条废弃）、破折号阈值 6 落在实测分布之外（**死规则**，下调至 3.5）。
     回调入口：`scripts/ai_tone_calibrate.py`（校准 + 抽样双功能）；基线数据见
     `docs/analysis/ai-tone-calibration-2026-09-15.md`。
   - **改权威输入必须 miss 缓存**：`outline_json` 进 payload → 单列缓存键维度（`_fingerprint_outline_json`），否则改大纲后同 state_version 脏命中旧装配。判别：改该列后 `build_director_input` 必须返回新值（`tests/unit/test_chapter_outline.py` 看守）。

## 三、协作与审查工作流（AI 协作项目）

继承方法论 `~/.agents/methodology/`（三条元规则凌驾一切）：

1. **主会话不采信子代理结论**：critical/major 逐条亲验（读码/探针/复算）后才入册。
2. **声称与实测分离**：「已修/恒/永不」要么有测试看守要么标注为声称；新测试必须**突变验证**（撤修复必红）；零变更重构以**测试数零增减**为硬验收。
3. **缺陷形状升格**：同一形状抓到第二次 → 沉淀为清单/硬规则/本文件条目（F-1~F-8 先例在 v3.9 计划文档 §六）。
4. **并行开发按文件路径互斥分组**；子代理任务书写死专属文件/铁律/测试命令/汇报格式；子代理禁做任何版本控制写操作。
5. **先验判断被推翻必须留痕**（项目铁律：roadmap 评估表 + 「两处主会话先验判断被推翻」先例）。

## 四、命令与流程

```bash
python scripts/migrate.py            # 迁移（新库全链 0001~0028）
python -m ruff check packages scripts tests   # lint（测试文件豁免 E501）
python -m pytest -n 4 -q             # 全量（约 4 分钟；xdist 需在 venv）
cd apps/web && npx tsc -b && npx vitest run && npm run build
python scripts/export_openapi.py && python scripts/gen_frontend_types.py   # schema 漂移后 regen
python scripts/smoke_e2e.py          # 端到端（临时库+独立端口，自动清理）
python scripts/check_state_sync.py --db data/novelos.db   # 快照↔集合漂移核查（只读）
```

版本纪律（方法论 05）：pyproject 为单源，`main.py` 经 importlib 读取；`apps/web/package.json` 随发版对齐；uv.lock 升版后 `uv lock`；发版前「版本三处一致」核对。

## 五、坑区（症状 → 真因 → 对策）

| 症状 | 真因 | 对策与判别 |
|------|------|-----------|
| 打开章节页后「生成计划」丢作者意图/字数退回 | preview（空意图/默认字）与生产共用装配缓存键 | 键补维度+ns 拆分（V3.9 1B 已修）；判别：改键维度测试 |
| 缓存命中比 uncached 还慢 | 键预判 4 个 `_peek_*` 各自建连+解析整份快照 | peek 收敛（V3.9 2A 已修）；命中应 ~2ms 级 |
| checkpoint 体积百 KB×8 节点 | 引擎把节点产出存两份（顶层+镜像键），write 无 exclude | 镜像键排 exclude（V3.9 2B 已修）；判别：checkpoint <40KB |
| condense 永不压缩 | mock 守卫把「无 mock」当「跳过」 | 守卫对齐 polisher 语义（V3.9 1A 已修）；判别：生产形态有 LLM 调用记录 |
| leg 元数据 token 归属互换 | rowid/created_at 同源同秒，序不稳 | 按 output_json 同一性归属（V3.9 5.6）；**ORDER BY 处方被实证推翻，勿复用** |
| 「最近 5 条」取错 | hook_id 含随机 hex，字典序≠时间序 | (created_at,id) 双键（V3.9 5.5） |
| 字数闭环注记永不触发 | 线性节点序单 run 内 condense 只跑 1 轮 | 循环收进节点内（V3.9 1A） |
| smoke 全链 409 连锁 | start 端点异步化后脚本不等待终态 | `_run_step` 轮询+resume（V3.9 F-6）；判别：实机 4 连绿 |
| venv 缺 pytest-xdist | 加依赖后未 sync，`-n` 报 unrecognized | `pip install pytest-xdist` 或 uv sync |
| Windows 临时库删不掉 | python.exe 是启动器 stub，terminate 留孤儿进程 | taskkill /F /T 连树杀（F-6 已修） |
| 事务内另开连接写库 → `database is locked` | WAL 单写者：外层连接的未提交写持有锁，内层新连接写等待 5s 后 OperationalError（daemon 线程吞掉更难查） | 嵌套写复用同一事务连接（M1-a 排障实录，2026-09-13）；判别：调用点是否已有 open conn |
| 崩溃 run 重启不自愈（0026 后） | 启动自愈按 instance_id 归属过滤，新进程 id 不同 | 显式 `NOVELOS_INSTANCE_ID` 固定身份；或 `db_maintenance fix --apply`（F6 语义代价，知情裁决） |
| 备份同库重导入含 `#dup` 后缀项目撞唯一索引 | commits.rollback_of 全库级唯一索引 vs 0022 去重后缀（pre-existing） | 登记不修（R-1，触发面窄+修法带 FK 风险）；恢复路径：导入新库 |
| dev 库快照与集合漂移 | 两次留痕手工改库发生在最后 commit 后（F-8 已定性非代码缺陷） | **✅ 已修复**：v1 快照按 DB 重建+清孤儿 state，SYNC OK；再犯路径=系统外手工改库，用 check_state_sync 核查 |
| 拆书 canon 书名乱码（Git Bash curl 中文 argv 被转 GBK） | starlette 对 multipart 字段/文件名的解码策略是 utf-8 失败回退 latin-1（`_user_safe_decode`），原始字节不丢失 | 端侧 `_repair_mojibake` 逆变换（latin-1 编码回字节 → gb18030 解码，CJK 守卫防误修）已修（2026-09-13）；判别：纯 ASCII/含 >U+00FF 字符不动 |
| 量产驱动撞 409「章节有活动 run」/停驱动的孤儿 run 卡死 | 客户端驱动被 TaskStop 后，服务端 run 继续跑；build_ctx 类节点失去调度方后可卡 RUNNING 数十分钟 | 续跑前先查 workflow_runs 该章 RUNNING/PAUSED 行：等其终态或 cancel；判别：POST 前 SELECT |
| 门禁改稿（revise）傻等超时 | revise 驳回后 auto-revise 子 run 的 review **会重新 PAUSED 等人工**，章状态停在 DRAFTED 不翻转 | 驱动须接力：发现新 PAUSED run→读报告→无错即批/有错再改（≤2 轮）；W-LEN 意见必须**双向**（超限给下限、欠带给上限，防 4272→1853 乒乓球）；判别：轮询 PAUSED run 而非章状态 |
| commit 风控门（high_risk_approval）批量阻塞 | observer 把角色目标推进等常规 delta 误报为 world_kind=rule change（宁可错杀设计） | 批量 commit 驱动带自动批准（读 pause_payload 记留痕，≤3 轮/章）；observer 偶发畸形 delta（update 但 before=None）重试即过 |
| 大纲补丁「生效了但没生效」：章节标题换了，正文还是旧设定 | 大纲字段（plan_json.chapter_goal/key_beats）**从未进 planner 装配输入**，且 plan_json 被 planner 输出整体覆盖 | 0028 拆出 `chapters.outline_json` 策展槽 + `build_director_input` 注入 `chapter.outline` + planner prompt 规则 0「大纲优先」+ 缓存键 outline 维度；判别：`tests/unit/test_chapter_outline.py`（撤注入必红） |
| 全项目重置脚本批量 DELETE 报 FOREIGN KEY constraint failed | 自引用 FK（workflow_run_nodes.parent_node_run_id / state_deltas.supersedes）在 SQLite 即时检查下逐行触发；且删序须按依赖子表先行 | 重置脚本 `PRAGMA foreign_keys=OFF` → 按依赖序删 → 开回 + `PRAGMA foreign_key_check` 校验；判别：脚本收尾的校验段必须无输出 |
| 重产早期章时正文冒出弧末情节与计划术语 | `_hook_ledger_excerpt` / `_open_foreshadow_list` / `_narrative_debt_excerpt` 注入项目**全部**未闭环项，不按引入章号过滤——顺行生成无害（后面还没写），重产 ch1 时后文钩子倒灌 | 三处注入加 `current_chapter_no` 位置过滤（NULL 保持全量口径，项目级项保留）；实证=书1 重产 ch1 引用了 ch21 的 `hook_..._second_arc_identity`，正文出现「三年之约已经兑现，破屋锚点再次确认」；判别：`tests/unit/test_ledger_chapter_filter.py`（撤过滤必红） |
| 改稿轮（auto-revise）把整段原文重出一遍 + 字数仍欠带 | `mode=revise` 被路由到 `light` 能力档（=审校档，原设计当「定向局部修改」省额度）；但门禁触发的改稿实际是**整章扩写**，审校档不做长文创作 | **先验推翻（2026-09-15）**：revise 改为与 write 同走 `creative_writing`；实证=书1 ch1 同一句 v1 出现 1 次 → v2 出现 2 次、CJK 1460→1815（带下限 2125）；判别：`test_chapter_write_writer_capability.py` 三用例 |

| git push 报 `Failed to connect to github.com:443 over proxy 127.0.0.1` | 本机 `git config http.proxy/https.proxy` 指向的代理已死，git 仍走它 | 绕过：`env -u http_proxy -u https_proxy git -c http.proxy= -c https.proxy= push origin master`（2026-09-15 实测推通积压提交）；判别：直连能到 github.com:443 |

| 检测规则存在但从不触发（或命中的全是误报） | 算子匹配字面形态却不解析语义：阈值落在实测分布之外=死规则；算子比规则名更宽=误报。频率数字本身不提示这两种落差 | 阈值取自**本仓实测分布**（p85/p90），不照搬外部研究数值；算子入册前抽 20 条命中人工过一遍，名实不符就改名、误报率高就废弃（先例：AI-SHORT-PARA 改名 /AI-TRANSLATIONESE 废弃 / 破折号阈值 6→3.5）；判别：`scripts/ai_tone_calibrate.py` |

## 六、一致性矩阵（当前核销）

> 全量检修（2026-09-13，方法论 04 全量档）报告：docs/reviews/全量检修-2026-09-13.md；
> 模块化重构计划：docs/roadmap/v4.0-模块化重构计划.md（存量挂账 4 热点+2 类负债）。


| 项 | 声明 | 实际 | 状态 |
|---|------|------|------|
| 版本 | pyproject 3.9.0 = package.json 3.9.0 = importlib 读取 = uv.lock 3.9.0 | 同左（**含 editable 元数据**——发版后必须 `uv sync --extra dev` 刷新 dist-info，否则 /api/health 对外报旧版；R5 检修实测抓到的错核销已修正） | ✅ 2026-09-13 二次核销（uv sync 后实测） |
| 表数 | 业务表 39 / 含 _migrations 40 | test_health + smoke EXPECTED_BUSINESS_TABLES=39 | ✅ 2026-09-15（0028 只加列不增表） |
| 测试基线 | pytest 2198 passed / 2 skipped；vitest 485 | 大纲槽批次（0028 + outline 注入）后实测 | ✅ 2026-09-15 |
| OpenAPI | 96 paths / 40 schemas | export_openapi 实测，契约测试 3 绿 | ✅ 2026-09-13 |
| dev 库数据 | DRIFT:2（prj_8365f42af5a6） | **已修复**：v1 快照按 DB 重建 + 6 孤儿 state 清除，check_state_sync SYNC OK（2026-09-13，修前备份 /tmp/novelos_backup_pre_f8repair_20260913.db） | ✅ 已核销 |
