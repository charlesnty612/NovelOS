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
5. **软件/内容/痕迹三仓分离**：运行 payload 存 DB；人编辑面在 NovelOS-Content（10 维文件=编辑面，pack.json=运行面，双轨不自动聚合）；操作痕迹在 NovelOS-Artifacts。审核红线只进内容仓。
6. **迁移**：只加不改；新列可空兼容存量；迁移清单测试（test_migrations）随新增同步。
7. **文档引用纪律**（R5 检修根因：38ec703 三仓迁移未回填引用造成 ≥14 处断链——反引号路径引用 markdown checker 查不出，只能靠人工/代理抽查）：引用 NovelOS-Content / NovelOS-Artifacts 的文件必须写清仓库名；引 docs/ 内文件前先确认未被迁空（9 个空目录是迁移残留）；旧计划/旧报告类文档必须带「历史注记」横幅（日期+状态+被什么取代）。
8. **环境同步纪律**：改 `pyproject.toml`（尤其 version）后必须 `uv sync --extra dev`（ruff/pytest/xdist 在 dev extra 里，裸 `uv sync` 会 pruning 掉 ruff）——editable 元数据不刷新时 `/api/health` 的 version 腿对外报旧值，且自洽断言抓不住（R5 实证）。

## 三、协作与审查工作流（AI 协作项目）

继承方法论 `~/.agents/methodology/`（三条元规则凌驾一切）：

1. **主会话不采信子代理结论**：critical/major 逐条亲验（读码/探针/复算）后才入册。
2. **声称与实测分离**：「已修/恒/永不」要么有测试看守要么标注为声称；新测试必须**突变验证**（撤修复必红）；零变更重构以**测试数零增减**为硬验收。
3. **缺陷形状升格**：同一形状抓到第二次 → 沉淀为清单/硬规则/本文件条目（F-1~F-8 先例在 v3.9 计划文档 §六）。
4. **并行开发按文件路径互斥分组**；子代理任务书写死专属文件/铁律/测试命令/汇报格式；子代理禁做任何版本控制写操作。
5. **先验判断被推翻必须留痕**（项目铁律：roadmap 评估表 + 「两处主会话先验判断被推翻」先例）。

## 四、命令与流程

```bash
python scripts/migrate.py            # 迁移（新库全链 0001~0026）
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

## 六、一致性矩阵（当前核销）

> 全量检修（2026-09-13，方法论 04 全量档）报告：docs/reviews/全量检修-2026-09-13.md；
> 模块化重构计划：docs/roadmap/v4.0-模块化重构计划.md（存量挂账 4 热点+2 类负债）。


| 项 | 声明 | 实际 | 状态 |
|---|------|------|------|
| 版本 | pyproject 3.9.0 = package.json 3.9.0 = importlib 读取 = uv.lock 3.9.0 | 同左（**含 editable 元数据**——发版后必须 `uv sync --extra dev` 刷新 dist-info，否则 /api/health 对外报旧版；R5 检修实测抓到的错核销已修正） | ✅ 2026-09-13 二次核销（uv sync 后实测） |
| 表数 | 业务表 38 / 含 _migrations 39 | test_health + smoke EXPECTED_BUSINESS_TABLES=38 | ✅ V3.9（0025 后） |
| 测试基线 | pytest 2059 passed / 2 skipped；vitest 479 | 前端优化+人读化后实测（pytest 基线另见 3.9.0 发版 2104） | ✅ 2026-09-13 三次核销 |
| OpenAPI | 96 paths / 40 schemas | export_openapi 实测，契约测试 3 绿 | ✅ 2026-09-13 |
| dev 库数据 | DRIFT:2（prj_8365f42af5a6） | **已修复**：v1 快照按 DB 重建 + 6 孤儿 state 清除，check_state_sync SYNC OK（2026-09-13，修前备份 /tmp/novelos_backup_pre_f8repair_20260913.db） | ✅ 已核销 |
