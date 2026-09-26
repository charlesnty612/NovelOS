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
database/migrations/ 唯一 DDL 来源（0001~0029；表数口径：业务表 39 / 含 _migrations 40）
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
     `NovelOS-Artifacts: docs/analysis/ai-tone-calibration-2026-09-15.md`。
   - **改权威输入必须 miss 缓存**：`outline_json` 进 payload → 单列缓存键维度（`_fingerprint_outline_json`），否则改大纲后同 state_version 脏命中旧装配。判别：改该列后 `build_director_input` 必须返回新值（`tests/unit/test_chapter_outline.py` 看守）。
   - **第二次实证（2026-09-16 新书01，同形状）**：`author_intent` 是**死参数**——`POST /write` 收下并写进 run ctx，但 `build_writer_input()` 无此形参、writer 的 prompt 模板里没有该字段；消费它的只有 `director`/`director_planner`。结果：本书铁律在 ch1-6 一个模型都没看见（驱动发给 write，唯一认它的 plan 端点收到空 `{}`）。配套纪律两条：**① 端点收下的参数必须有消费方测试**（「收下即忘」与「表里有值」同级——静默丢弃要么实现要么 400）；**② 排查固定查两处**：`build_*_input` 的形参表 + 该 agent 的 prompt 模板（DB `prompts` 表按 `agent_id` 查）。

## 三、协作与审查工作流（AI 协作项目）

继承方法论 `~/.agents/methodology/`（三条元规则凌驾一切）：

1. **主会话不采信子代理结论**：critical/major 逐条亲验（读码/探针/复算）后才入册。
2. **声称与实测分离**：「已修/恒/永不」要么有测试看守要么标注为声称；新测试必须**突变验证**（撤修复必红）；零变更重构以**测试数零增减**为硬验收。
3. **缺陷形状升格**：同一形状抓到第二次 → 沉淀为清单/硬规则/本文件条目（F-1~F-8 先例在 v3.9 计划文档 §六）。
4. **并行开发按文件路径互斥分组**；子代理任务书写死专属文件/铁律/测试命令/汇报格式；子代理禁做任何版本控制写操作。
5. **先验判断被推翻必须留痕**（项目铁律：roadmap 评估表 + 「两处主会话先验判断被推翻」先例）。

## 四、命令与流程

```bash
python scripts/migrate.py            # 迁移（新库全链 0001~0029）
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
| 改稿回路每轮只涨几个百分点、永远「轮次耗尽」收尾 | 修复轮**全部**按 revise 模式驱动，而 writer prompt 把 revise 钉死为局部修改：规则 20「revise 净增 ≤ +5%」+ §6.1「未提及部分逐字保留 / 不整章重写」（writer 的 `self_report.deviations[]` 原文：「draft_text 为上游既成稿 … writer 不做破坏性扩写」）⇒ 2 轮 ×+5% ≈ +10% 去追 -65%，数学上追不回 | **✅ 已修**（2026-09-18）：`_auto_revise_loop` 每轮 write 前读 pending review 的 `review_report`（父 run checkpoint 的 `author_review.__pause_payload__`），字数带**下限**缺口 > 剩余轮次可达幅度（`(1.05**n)-1`）→ 本轮带 `fresh_write`（writer 回 mode='write'，绕开 §6.1）；只认长度类缺口、只认增侧（压缩不受 +5% cap 约束）；实证=ch_92bac068ff0d（932→1300 字、-65.8% 四轮不收敛）；判别：`tests/unit/test_auto_revise_loop_cancel.py` 六用例 + 端到端一例（`tests/api/test_gate_revise_closure.py`，撤逃逸必红：`assert 'revise' == 'write'`） |

| git push 报 `Failed to connect to github.com:443 over proxy 127.0.0.1` | 本机 `git config http.proxy/https.proxy` 指向的代理已死，git 仍走它 | 绕过：`env -u http_proxy -u https_proxy git -c http.proxy= -c https.proxy= push origin master`（2026-09-15 实测推通积压提交）；判别：直连能到 github.com:443 |

| 检测规则「从不触发」「全是误报」「刷屏噪声」三种病 | 算子匹配字面形态却不解析语义：① 阈值落在实测分布之外=死规则；② 算子比规则名更宽=误报；③ **把正常写法与真信号混在一张「命中即报」表里=噪声淹没信号**。频率/命中数本身都不提示这三者 | 阈值取自**本仓实测分布**（p85/p90），不照搬外部研究数值；算子入册前抽 20 条命中人工过一遍：名实不符就改名、误报率高就废弃、**正常词与真套话分层**（先例：AI-SHORT-PARA 改名 / AI-TRANSLATIONESE 废弃 / 破折号阈值 6→3.5 / 禁用词两层制——忽然等常用弱词改为「同章堆积才报」，章节命中率 67%→14%）；判别：`scripts/ai_tone_calibrate.py` |

| 生成/改稿越改越坏：句子变长、对白变灌水 | **可优化指标被当质量判据**（同一形状两次：把「对话占比 ≥20%」写成题材包硬指标并加 error 档 → 模型为凑指标灌对白；指标撤退后我给出的**正向方法**又错成「同情节改对白交锋」→ 作者手写的样张被判「注水太严重、全是重复句、完全没法看」） | **可测算子只作体检、不作处方**（F-19）：① 凡「占比/频率」类指标，不得写成题材包硬指标、不得进生成驱动 `REVISABLE`、不得作选章依据（`AI-DIALOGUE-LOW` 已从 `_refs/arc6_driver.py:REVISABLE` 移除；`readability_repro.py` 无 `--only` 直接拒绝执行）；② **指标撤退 ≠ 方法正确**——换正向方法后必须拿**锚点书逐段对照**验（F-19 反证：榜一 ch1 整章 0% 对白却极好读，9 个有名人各有动作）；③ 加任何可测指标前先问「凑它最省力的手段是什么」；判别：`scripts/readability_audit.py` 只读回放 + 题材包 pack 版本 |

| 算出来的问题没人能说「不」：9 段逐字重复的稿子 `errors: []`、`overall: 90` 照常提交 | severity 有**三**档、后果只有**一**档：只有「`error` ∧ rule_id ∈ `BLOCKING_RULES`」才阻断，其余 error 一律 informational；而 `MVP_SEVERITY_MATRIX` 把 style/pacing/payoff/ai_trace **上限封在 warning** ⇒ 重复类在构造上永远进不了白名单 | 后果轴与 severity **正交**（P0-1）：`Gate = auto \| confirm \| block`（`quality/issues.py::issue_gate` 唯一权威）；`confirm` 命中在 `enforce` 下须带 `gate_override={"rule_ids":[…],"reason":"…"}`（集合包含 + reason 非空）才放行，阻断消息必须带 **rule_id 清单 + 证据摘录**——不许在不知道批准什么的情况下批准；判别：`tests/unit/quality/test_issue_gate.py` + `tests/api/test_gate_confirm_e2e.py` |
| 「一个样本定的阈值」把门禁变成橡皮图章：人写的稿子也被要求「接受重复」 | 按**单个** 30.37% 事故章把 trigram confirm 阈值定在 0.08，而 0.08 落在实测分布**内部**：人类锚点书 19 章 mean 7.54% / max 9.49%（9/19 章超），生成侧 92 章 p50 13.33%（仅 2/92 章低于）⇒ 门槛低于噪声底，人人必点 = 训练作者闭眼放行 | ① 阈值只取**本仓实测分位**并**分层**：warn 0.16（生成侧 p85）/ confirm 0.25（生成侧 max 0.2097 与事故 0.3037 的几何中点）；② **量级依赖的后果不进 rule_id 静态表**——该规则撤出 `CONFIRM_RULES`，由 `scoring.score_style` 只对超 confirm 阈值的那一条上修 `gate="confirm"`；③ 判别：`tests/unit/quality/test_trigram_tiers.py`（四带 + 不变式「人类上沿 < warn < 生成上沿 < confirm < 事故值」）；**教训**：阈值定锚前先量**人类侧与生成侧两条分布**，一个样本不构成先验 |
| 修复动作只有一个 → 越修越坏（改稿轮追不上字数，还把原文抄一遍造出重复） | 无论评审报的是长度、标点还是连续性，`_auto_revise_loop` 一律 `revise: true`；而 revise 在 writer 侧被规则 20（净增 ≤ +5%）与 §6.1（未提及部分逐字保留）钉死——既是「追不上字数」之因，也是「造出 9 段重复」之因（writer 自述「draft_text 为上游既成稿…不做破坏性扩写」） | **失败形状 → 修复动作**（`routers/workflows/repair_policy.py::decide_repair`，闭集）：`regenerate`（`fresh_write`，writer 回 write 模式）/ `revise`（定向局部改稿）/ `stop`（不启动子 run，交人工）；**表外 rule_id（连续性/逻辑/设定类与全部占比指标）一律 stop**；停止与轮次耗尽把结论追加进该 run 的 `error`（作者在 run 列表里读得到原因）；判别：`tests/unit/test_repair_policy.py` + `tests/api/test_repair_paths.py` |
| 同一份报告里两个「最新」：`word_count: 2248` 与 genre 的「实际字数 713」并存 | 凡按 chapter_id 取「该章草稿」的读者，在调用方手里其实有「本次评审/核销的那一版」时都会与它分叉——**测量对象 ≠ 被审对象**；两个站点各自定义「最新」（`version DESC` vs `created_at DESC`），且提交门禁量最新稿 ⇒ 可对**没审过的正文**放行 | 共享解析单点 `domain/chapter/draft_resolver.py::resolve_draft`（`None` → 最新，口径 **`version DESC`** = 发布序）；**凡是读草稿做判定的一律带被审版本**；判别：`tests/unit/test_draft_under_review.py` 的**形状级**用例（递归遍历报告里所有「字数/版本」字段，三版形状下必须全取同一版——将来新增测量点漏传版本会直接现形） |
| 整本书的铁律每次都得重新粘贴，漏一次即静默丢失 | `author_intent` 只在 `StartWorkflowRequest`（per-run），**没有项目级落点** | 0029 加 `projects.writing_bible`：圣经=基线、运行期=**增量**，**拼接**（单点 `builders_common.resolve_author_intent`，文本内声明冲突以增量为准）；writer/director 键各加 `bible_fp` 维度；判别：`tests/unit/test_project_writing_bible.py`（撤 `bible_fp` 必红：`assert 1 == 2`） |
| 规则登记了却不生效（惰性声明）：`AI-BEAT-REPEAT` 进了 `CONFIRM_RULES` 但门禁永远看不到它 | `scan_ai_patterns` 的命中只进 `chapter_review`，`QualityEngine.evaluate` 从不消费 ⇒ 门禁从 `report.issues` 判，而报告里永远没有该 rule_id——**「登记」不等于「在消费路径上」** | 接线经 `quality/engine.py::ai_pattern_issues`（通用转换，rule_id 原样透传、不硬编码）；新规则入册时**必须核实生产端到消费端的完整链路**，而不是只看规则表里有它；判别：`tests/unit/quality/test_ai_pattern_wiring.py`（撤接线 6 例红） |
| director 计划的 key_beats 落库成碎片数组（字符串/裸数组混进元素位），全程无告警 | LLM 非法 JSON 被 `extract_json` 三级兜底 `json_repair` 「修」成顶层可解析、内部腐烂的 dict；旧 `_validate_director` 只查顶层 schema_version ⇒ 腐烂结构静默过闸落库 `chapters.plan_json`（正文靠 chapter_goal 兜底不跑飞，故零告警） | **✅ 已修**（2026-09-21 批次）：`_DIRECTOR_PLAN_ARRAYS` 形状核销（元素必须 dict、key_beats 另须 str beat_id/purpose；键缺席放行不收紧必填面），违规抛 `AgentOutputError` 走 runner 重试；observer 7 数组同款守卫抽共用单点 `_array_shape_errors()`。注意定性：observer 侧碎片**本就会被 delta jsonschema 拦**（非静默落库），真危害是诊断退化+多烧一轮重试+风控门把碎片端给人工；判别：`tests/unit/test_director_planner_contract.py` 事故复现用例 + 拿真实事故 payload 喂 `validate_contract` 必拦 |
| 改稿轮悄悄重写未提及段落，人工只能全文 diff 才发现 | revise 契约的 `---REVISION-CHECKLIST---` 核销表经 write 流水线 polisher 段后落库为 **null**；`deviations[]` 越界申报无机器强约束（2026-09-21 实证：指示「禁止改动打脸段落」，writer 仍整段重写且零申报） | 登记不修：① 核销表在 polisher 段的丢失要修（机读审计断链）② writer §6.1「越界必申报」需要违约成本（如未申报的超注改动比例进 self_report 硬校验）；过渡对策：人工审改版对与 v1 做相似度 diff，相似度异常低即超注重写嫌疑 |
| 阻断信息「只说一半」：blocking 与 confirm 同时命中时，confirm 的 rule_id / override_error / evidence 全被吞 | `should_block` 分支 `if blocking_issues:` 优先后直接 return，`gate_blocked.rule_ids` 与 ValueError 消息只含 blocking 侧——作者修掉硬伤后再提交才第一次撞上 confirm，且不知道要准备什么 `gate_override` | **✅ 已修**（2026-09-21 检修）：`gate_kind="block+confirm"`——blocking 优先不可协商不变，rule_ids 取两组并集、note 合成两类建议、error 串在 `| guidance=` 之前追加 confirm 段（前端按 `| guidance=` 切分零影响）；判别：`test_gate_blocking.py::test_block_still_wins_over_confirm` + `test_block_only_still_uses_plain_block_message`（仅 blocking 时不加字段） |
| 规则名说自己是连续性，报告里归成文风 | `engine._AI_PATTERN_CATEGORY` 未登记 `CONT-*`，落入默认回退 `style`——与 `continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS` 的命名空间声明矛盾（名实不符），按 category 分桶时连续性命中跑进「文风」桶 | **✅ 已修**（2026-09-21 检修）：映射从 taxonomy 名册**派生**（`{rid: "continuity" for rid in DETERMINISTIC_CONTINUITY_RULE_IDS}`），新连续性规则只需改 taxonomy 一处；判别：`test_ai_pattern_wiring.py::test_deterministic_continuity_rules_are_categorized_as_continuity` + `test_continuity_category_mapping_is_derived_from_taxonomy`（撤映射必红） |
| prompt 纪律当强制用：write 模式吐出 stray 核销表尾块 → 原样落进 `drafts.content` 并抬高 word_count | 尾块剥离条件写的是 `mode == "revise"`，而 writer-v3.md「write 模式不输出尾块」只是 prompt 约定不是机器强制；模型一旦在 write / length-retry 轮违约，字数带按含尾块数字判定 | **✅ 已修**（2026-09-21 检修）：改为**看内容剥离**（分隔行出现就剥，坏 JSON 也剥——此前原样落稿）；write 模式剥掉的尾块**不进** `revision_checklist` 审计面（只登记 revise 的定向核销承诺）；判别：`test_chapter_write_revision_checklist.py::test_write_mode_stray_marker_is_stripped`（撤剥离必红） |
| 「一键改稿」把作者的字数目标与铁律丢掉 | `GateReviseRequest` 没有 `target_word_count` / `author_intent` 字段，构造 `StartWorkflowRequest` 时也不填 ⇒ 作者按 `--target-word-count 300` 起稿后被门禁拦下一键改稿，target 退回服务端默认 3000、铁律整段丢失 | **✅ 已修**（2026-09-21 检修）：两字段入请求体（同 `StartWorkflowRequest` 约束），值按「请求体 > 该章最近一次 run 的 ctx 继承」解析，write 与接力 review 共用同一份；判别：`test_gate_revise_closure.py::test_gate_revise_passes_target_word_count_and_author_intent` |
| 巡检工具假阳性把真信号埋掉：`check_state_sync` 把「从未 commit 的项目」全报成漂移 | 无 `story_states` 行的项目没有 state 版本化基线，「DB 有实体而快照没有」是**未开始**而非不一致，却按 only_in_db 计入 DRIFT（dev 库实测单项目 29 实体全列进明细、结论 DRIFT: 5 处） | **✅ 已修**（2026-09-21 检修）：无快照项目单列「无快照项目（不计漂移）」小节 + JSON `projects_without_snapshot` 字段；dev 库复测 SYNC OK；判别：`tests/unit/test_state_sync_drift_count.py` 四用例（撤掉 `if rep.no_snapshot: continue` 必红） |

## 六、一致性矩阵（当前核销）

> 全量检修（2026-09-13，方法论 04 全量档）报告：docs/reviews/全量检修-2026-09-13.md；
> 模块化重构计划：docs/roadmap/v4.0-模块化重构计划.md（存量挂账 4 热点+2 类负债）。


| 项 | 声明 | 实际 | 状态 |
|---|------|------|------|
| 版本 | pyproject 3.9.0 = package.json 3.9.0 = importlib 读取 = uv.lock 3.9.0 | 同左（**含 editable 元数据**——发版后必须 `uv sync --extra dev` 刷新 dist-info，否则 /api/health 对外报旧版；R5 检修实测抓到的错核销已修正） | ✅ 2026-09-13 二次核销（uv sync 后实测） |
| 表数 | 业务表 39 / 含 _migrations 40 | test_health + smoke EXPECTED_BUSINESS_TABLES=39 | ✅ 2026-09-15（0028 只加列不增表） |
| 测试基线 | pytest 2807 passed / 2 skipped；vitest 485 | 全量检修复核批次（M1 confirm 并存吞信息 / M2 CONT-* 错类 / m3 dialogue-low evidence / m5 stray 尾块防呆 / m6 gate-revise 透传 target+intent / m7 docstring / m8 巡检无快照不计漂移）后实测（`pytest -n 4 -q`，301s）；2026-09-26 落库前独立复核再实测 **2807 / 2 / 0 failed**（341s，与 CHANGELOG 误记的 2817 订正同批） | ✅ 2026-09-26 复核 |
| OpenAPI | 97 paths / 50 schemas | export_openapi 实测（m6 给 GateReviseRequest 加 target_word_count / author_intent），契约测试 3 绿；2026-09-26 复核 regen：types.generated.ts 逐字节一致、openapi.json 仅 version 字段环境元数据差异、内容零漂移 | ✅ 2026-09-26 复核 |
| dev 库数据 | DRIFT:2（prj_8365f42af5a6） | **已修复**：v1 快照按 DB 重建 + 6 孤儿 state 清除，check_state_sync SYNC OK（2026-09-13，修前备份 /tmp/novelos_backup_pre_f8repair_20260913.db） | ✅ 已核销 |
