# 更新日志（CHANGELOG）

> **留痕规矩（自 V1.0 起强制）**：此后所有迭代——功能、修复、迁移、行为变更——合并前必须在本文件追加条目。

## [Unreleased]

### Fixed
- **PATCH params「删除参数」语义缺失（思考档位改回默认不生效）**：`_prepare_patch_params` 按键合并（payload 缺键→DB 旧值保留），前端「默认（跟随端点）」档以"不发键"表达删除——删除意图永远被旧值复活（实测：kimi 档 reasoning_effort='high' 改默认保存两次无效）。修复：建立显式删除约定 **payload 参数值 `null` = 删除该键**（后端合并后 pop；api_key 既有分支不变），前端 default 档且 DB 原有键时发 `reasoning_effort: null`。3 新用例（null 删除保留他键 / 缺键保留旧值 / null 与 api_key 掩码共存）；残留数据已手工清理。
- **全局提示词火影项目专属残留清理（跨项目污染治理）**：writer-v1 规则 18/19（日式神话时代风物、陈瑞避讳/魂穿）、director-v1 与 scene_planner-v1 的「原著要素锁」具体举例（辉夜特征/追兵时间线）均为火影专属硬编码，靠 style_constraints 逃生口才未污染其他项目。已全部泛化（上游锁定风物/称谓/要素则严格跟随，未锁定则中性叙事/不适用），火影专属约束原文**无损迁入火影项目 world_rules 表** 3 条（语感基线/称谓与名讳禁忌/原著要素锁，迁移前备份 data/backup_huoying_rules_migration_20260830.json）。**连带发现并补上注入真洞**：scene_planner payload 从不携带 world_rules——补 `world_state_excerpts` 顶层字段（复用既有 schema）。新增防回归测试锁死 12 个火影专属关键词不得再进全局提示词。整仓 1378 passed。
- **`inferThinkingMode` 漏判 'max'（静默丢档）**：kimi 档案存 `reasoning_effort:'max'` 后重开编辑回显降级为 default，直接保存即删键（与 params 丢失同族）。已修并导出函数 + 2 回归用例。同批：getThinkingOptions kimi 分支补 `moonshot.ai/.cn` 官方域名匹配；model_router README agents 表同步。
- **LLM 空输出报错误导**：自适应思考耗尽 max_tokens 预算（finish_reason=length、正文零字符）时报 "empty output after stripping fences"，排障误导。现 provider 层捕获 finish_reason 透传至报错：length+空内容 → 「输出为空：max_tokens 预算被思考耗尽——请调大档案 params 的 max_tokens（建议 16384）」；finish_reason 随 token_usage 落库 ai_call_logs（OpenAI 兼容 + Ollama，Anthropic 暂 None）。7 新用例。
- **服务进程静默死亡 → 前端请求永久悬空**：会话后台 shell 启动的后端随 shell 收割静默退出（连发三次），浏览器 fetch 永不落定（「拉取模型」永久卡「拉取中」）。双修复：① 服务改 PowerShell `Start-Process` 独立进程启动（运维口径入记忆，日志落 data/serve.log）；② `api.post` 扩展 options（signal 透传），fetchAvailableModels 加 15s AbortSignal.timeout，任何情况 promise 必落定。
- **工作流重启孤儿 RUNNING（僵尸 run）**：进程重启杀死后台工作线程后，RUNNING run 永远卡死且同章节 409 防护挡死新 run（实测事故：手工清库解锁）。新增引擎启动自愈 `recover_interrupted_runs`：lifespan 启动时（迁移后）把全部 RUNNING run 标 `FAILED(error='interrupted: service restart killed worker thread')`、其 RUNNING/PENDING 节点行同步收口；PAUSED/COMPLETED 一律不动。3 新用例（含幂等）。
- **flaky 测试加固**：`test_run_observer_with_bad_then_good_script_retries` 等 3 处取 ai_call_logs「最新一行」未按 run_id 过滤，并发顺序下被其他用例写入干扰偶发失败；全部改为 `WHERE run_id = ?` 过滤。
- **模型档案前端 params 映射错误（数据丢失级，连带「拉取模型」置灰）**：后端读路径 `_mask_response` 返回键为 `params_json`（dict），前端 `normalizeProfile` 却读不存在的 `row.params` → 所有档案的参数进入前端后被清成空对象。症状：编辑弹窗 base_url 回显为空占位符（连带 OpenAI 兼容档案「拉取模型」按钮因 base_url 前置校验置灰）、思考模式徽标恒显「默认」；**若用户在编辑态点保存会静默清掉 base_url/thinking/max_tokens/service_tier 等全部参数**（库中数据未丢，因未触发过该路径保存）。修复：normalizeProfile 改读 `params_json`（兼容测试夹具旧 `params` 形态）并导出；新增 `endpoints.test.ts` 4 回归用例（dict/JSON 字符串/旧形态/空兜底）。全量 vitest 324 passed。
- **project-init 面板 resume/init 收到 RUNNING 误报「run 终态异常」**：后端 resume 已异步化（POST /runs/{id}/resume 固定立即返回 RUNNING，终态靠 GET 轮询），章节前端已适配，但 ProjectInitPanel 的 handleResume/handleRegenerate/handleInit 仍是同步旧契约，收到 RUNNING 即报「run 终态异常：status=RUNNING」（run 实际正常跑完，用户点「确认修订并继续」后误见报错）。修复：面板新增 `pollRunUntilTerminal`（2s 间隔 / 600s 上限 / AbortSignal 卸载安全），三路径收到 RUNNING 自动轮询到终态再走既有 PAUSED/COMPLETED 分支；同步终态返回保持向后兼容。测试：面板 39 用例（新增 RUNNING→PAUSED / RUNNING→COMPLETED / init 轮询 3 用例）。
- **文风样例面板竞态显示「暂无」**：`StyleSamplesPanel` 列表状态仅取挂载瞬间的 `initialSamples` 初值且无自动拉取——父页项目详情先返回、面板先挂载时，样例列表后到永不同步（后端重启后时序翻转即复现，用户报"样例丢失"，数据实际都在库）。修复：面板挂载时自取一次列表（initialSamples 退为即时占位）+ 响应防御 `Array.isArray`。
- **projects.premise 复合文本套娃污染**：部分生成（selected_stages 不含 premise）时 `persist_all` 把库中旧简介（"定位：…一句话：…"复合文本）当作 positioning 重新包一层「定位：」前缀再追加「一句话：」，每跑一次叠一层（用户实测多层嵌套）。修复三件套：persist_all 的 project update 仅在 premise 环节本次被选中时执行（未选跳过回写）；`_build_premise_text` 对 positioning 已带「定位：」前缀时剥层防御；`_rebuild_premise_from_db` 提取最后一段「一句话：」为 logline（下游 AI 输入更干净）。测试 3 新用例（污染回归/正常路径/剥层单元）；污染现场备份 data/backup_premise_pollution_20260829.json 后手工修复。
- **「按建议修改」忽略改稿意见框**：意见框内容此前只有「驳回并改稿」会带上，「按建议修改」仅发勾选建议——用户想"4 条建议 + 自补口径"无入口。现合并下发：note = 勾选建议 + 意见框文本（意见框为空行为不变）。新增合并用例。
- **prompt sync 测试期望集缺 polisher**：`docs/agents/prompts/polisher-v1.md` 落地后 `test_sync_registers_all_prompts_from_docs` 期望集未跟上（整仓红测），补 `polisher`。
- **白盒测试治理落地（P0/P1/P2 全量 19 项 + 复盘改进 4 项）**：三阶段白盒测试（覆盖率基线后端 86%/前端 70.5% → 四路只读审计 41 条发现 → 全量修复）。**P0**：PlotTab 新建事件必 422（表单 cause 类型错+time 缺 timeline_day，功能不可用级）；CharacterTab 保存整体覆盖 core_json 静默清掉 AI 字段（改 merge 快照）；resolved_hooks 把 ABANDONED 钩子复活成 RESOLVED（write_through/delta_repair 双守卫）；写正文等待超时 RUNNING 透传成僵尸活跃 run（timeout 标记+auto_revise 短路）；project_init 空 dict revisions 短路静默落空数据（空修订=未提供语义）。**P1**：commit 回滚半途残留（relationships/locations/factions/world_rules/narrative_debts 逆清理补全，含 debt hints 误读逆 delta 伪 before 的回归二次修复）；payoff severity 硬编码 error 对齐矩阵；迁移 0017（relationships 唯一索引去重 + workflow_runs 活跃态部分唯一索引兜底 409 TOCTOU，engine 新增 WorkflowRunConflict→409）；project_init persist_all 已存在实体改为更新内容字段（id/created_at 不变）+ 章节重建单事务；resume 非 PAUSED 400→409；model_profiles PATCH 双键互斥 422。**P2**：auto_revise 彻底异步化（resume 立即返回，回路入 daemon 线程，2 小时阻塞清除）；engine _parse_json 死代码删除；story_state/前端补测（write_through world_rules/relationships 11 用例、前端 Bible 三 Tab/ChaptersPage/ProjectsListPage/LedgerTab 17 用例，ChaptersPage 确认非死代码）。**V7/V8 复盘改进**：critic 注入 settings_digest（world_rules 全量+角色 one_line 前 8，prompt 新增 OOC 审查维度 medium 起）——修复"审校按错误计划强化错误"的结构性盲区；writer prompt 增语感基线（禁清宫剧用语，日式神话时代风物）；director/scene_planner prompt 增原著要素锁（辉夜辨识特征锁定、追兵属后期威胁早期不得出现）；世界观"神树果实"奖励措辞收紧为"神树果实微粒"。计划层漏网 OOC「辉夜是被追杀的亡命神族」已当场修正（V8 硬伤根因=计划非模型）。审计误报 1 条（快照重建破坏 recent_events——recent_events 不在重建集合内）已裁决并补回归测试。全程全量验收：pytest 最终 1307 passed / vitest 290 passed；迁移 0017 随重启生效。审计明细：docs/testing/audit-*.md；修复计划表：docs/testing/bugfix-plan-20260829.md。


### Changed
- **scene_planner 能力归属移入正文写作（creative_writing）**：scene_planner 实际运行于 chapter-write 管线，按管线阶段归组（V3.9.2 前归 reasoning）。双侧 AGENT_CAPABILITY + CAPABILITY_LABELS 同步（creative_writing = writer/polisher/scene_planner）；此后「写正文」单次模型覆盖自动钉住整条写作链（含场景规划），对比文风时管线口径一致。正文写作绑定同步调整为 M2.7-highspeed 首选 + k3-256k 回落（第 1 章 8 版 A/B 结论：M2.7 上限最高、k3 逻辑最稳、M2.5/M3 出局）。
- **「写正文」默认写作模式改为「全新重写」**：改稿已有专属入口（按建议修改/驳回并改稿自动触发改稿回路），手动写正文的场景（换模型对比/推倒重来）几乎都要整章重写——默认对调，「按意见改稿」降为可选；相关 4 条前端测试按新默认更新。
- **轻量评审（light）标注补 writer(revise·改稿)**：revise 模式 writer 走 light 的路由已上线但环节分配页 agents 标注未同步，用户误读归属；CAPABILITY_LABELS 与 README 表格补齐。
- **数据侧：两写作档案 max_tokens 8192 → 32768**（MiniMax M3 与 Kimi highspeed 端点均实测 200 接受）——起因：自适应思考在 1.6 万 token 输入下烧满 8192 预算、正文零字符（wfr_c33f 事故），上限放宽不改变计费与行为，只消除"预算耗尽即夭折"的硬顶。
- **revise 改稿并入 light 能力（改稿模型与 critic 同源）**：chapter-write 的 writer 节点在 mode='revise'（按建议修改/驳回并改稿触发的局部修改）时 `run_agent(capability_override='light')`——改稿走「轻量评审」绑定（当前 MiniMax-M3），省 Kimi 创作额度且指令遵循型任务与创作型模型解耦；write/fresh_write（全新写/全章重写）与 polisher 维持 creative_writing 不变；单次 model_overrides 优先级最高不受影响；mock 路径不消费 capability。测试 3 用例（revise→light / write→None / fresh_write→None）；全量 pytest 1325 passed。
- **Story Bible 只读 JSON 人读化（ReadableJson 组件）**：角色详情 core_json（原 `<pre>` 直显 JSON）、最新 state（空对象显 `{}`）、世界页 data_json 预览三处改为人读渲染——新增通用组件 `ReadableJson`（中文字段标签映射 25 键、关系数组特化 `{to_name}·{relation_type}—{one_line}`、未知键回退原键名、空值「暂无」）+ `RawJsonDetails` 折叠保留原始 JSON；节标题「core_json（…）」改「核心设定」。编辑表单 textarea 不动；CanonTab 有意折叠设计不动。测试：组件 17 用例；全量 vitest 316 passed。
- **章节工作流启动/resume 异步化（实时进度根基）**：`start_with_nodes`/`resume` 原在 HTTP 请求内同步执行完整个工作流（单章写正文 1-4 分钟），导致 POST 阻塞到终态、前端 `submitting` 全程 true、run 列表刷不出 RUNNING 行、轮询不启动——「正在执行」横幅整场停在「工作流启动中…」（用户实测确认的缺陷）。现引擎新增 `start_with_nodes_async`/`resume_async`（后台 daemon 线程推进，同步版保留供脚本/测试/project-init 使用）；`_start_workflow` 与 resume 端点切异步并加同章节并发 409 防护；`GET /runs/{id}` 补 `workflow_name`。配套修三个被同步时代掩盖的缺陷：(1) `runner.py` 每次 agent 调用成功/失败后直接盖 run 行 COMPLETED/FAILED 的遗留行为——异步轮询方会把中间态误读为终态（observer 并行测试确定性失败+实时横幅误停的根因），现引擎托管调用（node_run_id 非空）一律不盖戳（`_finalize_agent_run_status`），run 终态由 `engine._finalize_run` 收口，commit 管线的 `_recover_run_status` 随之退化为 no-op；(2) resume 执行期间 run 行一直标 PAUSED——轮询方误停，现 `resume`/`resume_async` 校验通过即在调用线程置回 RUNNING（`_mark_run_running`）；(3) auto_revise 回路启动的 write/review run 在异步后即时返回 RUNNING，`_run_workflow_return_payload` 补终态轮询等待（600s 上限）。前端：run 停到 PAUSED 时轮询回调重拉 detail（RUNNING 阶段拉的 checkpoint 不含 pause_payload，审批卡渲染不出的缺口），横幅动作名支持 workflow_name 映射中文。测试：`test_engine_async.py` 10 用例（其中 `test_resume_async_returns_immediately` 修正了"慢节点放在挂起点自身、实际永被执行"的构造错误，改用节点 sleep 作时钟断言返回时仍 RUNNING）；全量 pytest 1247 passed / vitest 261 passed；浏览器实测：点审校→横幅实时显示节点与秒数→审批卡无刷新自动出现。

### Fixed
- **「驳回」终态语义修复（rejected 标记）**：chapter-review 的纯驳回（approved=false 无 revise）原走 `ValueError("author_review 未通过，无法标记 REVIEWED")`，run 以一句像代码事故的报错收场（实际效果 run FAILED、章节保持 DRAFTED 本就是设计语义）。现与 revise 分支的 rejected-for-revision 同机制：新增 `_Rejected` 异常（args[0]='rejected'），run FAILED + error='rejected'。前端徽标/横幅配套三态区分：`rejected-for-revision`→「已驳回·改稿」、`rejected`→「已驳回」（同中性灰样式，判断顺序先长后短防子串误配）、真失败仍红色；纯驳回不再误显自动改稿回路横幅（`reviseLooping` 仅 revise 触发）。测试：pipeline 用例补 error=='rejected' 断言；全量 pytest 1250 passed / vitest 263 passed。
- **藤原千鹤角色全库清除 + 辉夜框架对齐原著（OOC 治理第二轮）**：用户对照原著复核后拍板清除 AI 初始化自行发明的「凡人妻子」角色藤原千鹤（与 premise「陈瑞娶辉夜」的婚姻结构直接冲突；原著对照：动画 680 话祖之国天子纳降临之神辉夜为侧室、漫画《阵之书》辉夜禁欲无婚——辉夜是神女，绝无「政治联姻/主妇/棋子」属性）。已删：characters 行 + 陈瑞 relationships 条目 + 第 2/4/5/7/9/10 章种子中的千鹤节拍（剧情职能移交大名府凡人暗线/朝堂，第 4 章情报网掩护改由「大名府凡人暗线」承担）；辉夜角色 one_line 由「起初视他为联姻棋子」改为「降临忍界的神女、卯之女神，起初视凡人如蝼蚁」（神女框架对齐原著）；第 1 章标题《穿越即巅峰？我是大名的大名》→《穿越即巅峰？我娶了神女辉夜》（"大名的大名"为赘婿→大名批量替换事故产物）。删除前备份 `data/backup_fujihara_chizuru_delete_20260829.json`；连同上轮共清除 14 处，全库 14 张表文本字段终验零残留（联姻/赘婿/主妇/大名大名/千鹤）。
- **「政治联姻」OOC 框架清除（V4/V5/V6 OOC 根因）**：项目初始化的卷纲/章节种子自行加戏，把「和辉夜结婚的大名」（premise 原文）扩写为「与辉夜政治联姻」，混入第 1/6/7 章种子与导演计划、陈瑞与藤原千鹤角色实体，writer 忠实执行导致成稿 OOC。已修：ch1 计划（政治联姻→成婚；赘婿→大名批量替换事故造成的「大名大名」叠词×2）、ch6 种子（政治联姻→陌生戒备）、ch7 种子与千鹤角色（政治联姻妻子→大名家主妇/凡人夫妻起步，角色去留待作者拍板）、陈瑞角色 one_line（联姻的大名大名→迎娶辉夜的大名）。数据修复前已备份至 `data/backup_chapter_plans_before_ooc_fix_20260829.json`。排查结论：「按建议修改」不改计划正文（审校管线仅写/删 `plan_json.revision_note` 键，见 chapter_review/pipeline.py），计划正文唯一改写路径是「生成计划」导演节点。
- **`rejected-for-revision` 不再渲染为红色"失败"**：「按建议修改/驳回并改稿」会主动让 review run 以 `FAILED(rejected-for-revision)` 结束（改稿回路随后自动重跑 write→review），但 run 列表与节点时间线都把它渲染成红色失败徽标/错误横幅，用户误以为系统出错。现 `WorkflowRunStatusBadge` / `NodeStatusBadge` 增加可选 `error` 入参，FAILED 且 error 含 `rejected-for-revision` 时渲染中性灰徽标「已驳回·改稿」（新增 `.badge--chapter-rejected`），节点横幅改为 InfoBanner 说明"系统正在自动改稿重跑"，run 行悬浮提示补充语义；真失败仍为红色。
- **审校建议列表行内展示建议正文**：`ApprovalCard.tsx` 的「审校建议」条目此前只渲染来源徽标（"AI 审稿"/"warnings"/"严重"）与固定提示语，无法分辨每条对应改什么（用户反馈）。现每条行内渲染分类/严重度徽标（节奏/人物/伏笔等 + 高/中/低）、问题位置引文（`approval-suggestion-quote`）与建议正文（`approval-suggestion-text`），与上方 critic 问题卡片一一对应，「应用此条」可一眼对准目标；删除每行重复的操作提示语。`ApprovalCard.test.tsx` 引文/正文断言改 `getAllByText`（同文本现出现在 critic 卡片与建议列表两处），24 用例全绿。
- **依赖无上界导致新环境路由静默丢失**：`pyproject.toml` 原以 `>=` 声明 fastapi/starlette 等，`uv sync` 解析到 starlette 1.6.0 后其 `include_router` 对本仓库路由组装**静默失效**（24 个业务路由挂载为 0，`/api` 仅剩 health，业务接口全部 404/405，`discover_routers` 日志仍显示 discovered 24 具强误导性）。已将运行依赖锁定到经全量测试验证的基线版本（fastapi==0.133.1 / starlette==1.3.1 / uvicorn==0.41.0 / pydantic==2.13.4 / jsonschema==4.26.0 / httpx==0.28.1），`uv sync --extra dev` 生成 `.venv` 与 `uv.lock`；锁定后整仓 pytest 1222 passed 与原基线一致，126 条 `/api` 路由挂载恢复。

### Added
- **writer 字数硬约束（规则 20，跨项目通用）**：正文净字数锁 `expected_word_count ±7%`（缺省 3000 即 2800–3200），严禁越带宽上下限（±15%）；revise 净增 ≤+5%；压缩指令下禁止以增补新场景应付。起因：第 1 章 A/B 中 V2/V4 低于下限、V7/V8 超上限 +25%，revise 逐轮膨胀。
- **「拉取模型」候选改显式下拉**：原生 `<datalist>` 按输入框当前值过滤候选（当前值命中一项时其余候选全部不可见——实测"拉到 8 个只显示 1 个"）。改为拉取成功后渲染完整 `<select>` 候选列表（data-testid `profile-model-candidates`），选中填入、手输保留。
- **API 密钥集中管理（secrets.json）**：根目录新增 `secrets.json`（`.gitignore` 双条目防提交，`git check-ignore` 验证）专存密钥，结构 `{"api_keys": {"<profile_id|provider|环境变量名>": "..."}}`。新增 `packages/core/secrets_store.py`（mtime 缓存加载 + `resolve_profile_api_key` 五级优先级：文件[profile_id] → 文件[provider] → 文件[env_name] → params_json 存量 → 环境变量兜底；`NOVELOS_SECRETS_FILE` 可覆盖路径；坏 JSON 降级不抛）；`providers.resolve_api_key` 收口接入（router 两处调用透传 profile_id）；`_mask_response` 的 `has_api_key` 涵盖文件来源。kimi 档案明文 key 已迁入文件并从 DB 删除（备份 data/novelos.db.bak_secrets_20260830）。测试 13 用例；全量 pytest 1338 passed。
- **档案表单「拉取模型」+ 思考模式端点收窄**：① 新端点 `POST /model-profiles/available-models`（provider 分支适配 OpenAI 兼容 `/models`、Anthropic `/v1/models`、Ollama `/api/tags`、mock；密钥走 resolve_api_key 链含 secrets.json；上游失败 502、错误信息零 key 泄露），前端 model 输入框挂 datalist 候选 + 「拉取模型」按钮（base_url 未填置灰、拉取中态、切 provider 清候选、手输保留）。② 思考模式下拉按 base_url host 收窄选项（minimax→默认/关/自适应；deepseek/openai→默认/低/中/高；kimi.com→默认/低/高/最高；未知端点全量保守），选项带 hint；初值不在收窄列表时回退默认+提示行。测试：后端 8 用例 + 前端 3 用例；全量 pytest 1346 passed / vitest 320 passed。
- **project-init 单次 run 模型覆盖（model_profile_id）**：`ProjectInitRequest.model_profile_id`（可选）→ 路由经 ProfileService 校验（不存在 400）→ ctx → 四个 AI 节点 `run_agent(profile_id=...)` 单档案钉死全程（不回落，语义同章节工作流 model_overrides，但不影响全局绑定）。前端「AI 初始化设定」表单新增「本次初始化模型」下拉（默认绑定（全局）+ enabled 档案）。测试 3 用例（透传/缺省 None/400）；管线 19 用例。
- **project-init 环节自由勾选 + 部分生成占位跳过**：① 未完成环节不再强制勾选（原 forced 锁死），全部环节可自由勾选，仅生成勾选环节（selected_stages 白名单既有机制）；② persist_all 配套：outline 降级时跳过 volume/chapters/plot_event 落库（返回 `outline_skipped` 标记，杜绝部分生成写入空卷+10 个占位章节）；premise 降级且项目已存在时跳过 project update。测试 2 用例（只选 world 零占位落库 / 全量回归不受影响）。
- **审阅卡片显示「本次实际使用」模型（stage_models）**：`GET /runs/{id}` PAUSED 时附 `stage_models`（ai_call_logs JOIN agents 按 agent 取最新成功调用 model_id，error 排除；通用字段，章节工作流同样可消费）；project-init 审阅卡片在下拉旁渲染短名（剥 provider 前缀，title 全名，无调用不渲染）——解决「下拉显示全局绑定值被误认为本次生成所用模型」。测试后端 2 + 前端 2。
- **writer 润色节点（polisher）+ critic 追读力/节拍核销维度**：① chapter_write 管线 writer 与 save_draft 之间新增 polisher 节点（提示词 `docs/agents/prompts/polisher-v1.md`，输出契约 polisher-output.v1、±10% 字数守恒；mock 透传；AGENT_CAPABILITY polisher→creative_writing 双侧注册）。② critic-v1 prompt 新增 §3.7 节拍核销（key_beats 逐条核销，缺失/偏离以 `[beat N 缺失/偏离]` 前缀标注 ≥medium）与 §3.8 追读力审查（章首钩子/爽点兑现/章末钩子，前+后 20% 篇幅范围）；creative 创作档案 max_tokens 提至 8192（数据侧，思考与输出共用预算防截断）。V2 实测：4 条建议逐条核销全部落实、节拍 4/4。
- **档案表单「思考模式」下拉 + params 丢失修复**：AI 设置档案表单新增思考模式下拉（默认跟随端点/关闭/开启/开启·低中高档），写入 params 的 `thinking={type}` 或 `reasoning_effort`；档案卡显示「思考：默认/关/开/低/中/高」徽标。**连带修复同族数据丢失 bug**：档案表单保存原从零组装 params（只含 base_url/api_key），编辑档案会静默丢掉 timeout_s/thinking/service_tier 等既有参数——改为快照 merge（表单键覆盖、其余保留）。背景：V3.7「creative 开思考/reasoning+light 关思考」的按环节策略在档案合并时被抹平，全环节（含写正文）现均关思考运行，此下拉用于恢复按环节思考策略（如新建「MiniMax-M3·创作」档案开思考绑 creative_writing）。vitest 294 passed 含 4 新用例（回显/参数不丢回归/effort 映射/回默认清键）。
- **模型档案卡「设为默认」**：AI 设置页每张档案卡新增「设为默认 ▾」→ 展开 7 个环节 chip，点击即把该档案设为该环节的**首选**（PUT binding，profile_ids 首位插入、其余顺延为后备链，幂等）；档案卡显示其当前默认环节徽标；已停用档案 chip 禁用（贴合后端 422）。顺带修正误导文案：「未绑定 · 使用默认配置」→「未绑定（回落旧版 model_configs 链）」。纯前端（复用既有 bindings API），vitest 268 passed 含 4 新用例（徽标只展示首选/展开收起/bind 首位顺序/幂等）。
- **审校指定草稿版本（多版本对比闭环）**：用户生成 v7/v8 两版对比模型后发现审校永远只审最新版（两处 `ORDER BY created_at DESC LIMIT 1` 写死）。现：`StartWorkflowRequest.draft_version` → ctx → `_basic_checks_node`/`_critic_review_node` 按 version 取稿（不存在该版本则 run FAILED 带明确报错），`review_report` 新增 `draft_version` 字段，run 标签优先取 ctx 实际审的版本。前端：草稿选中状态提升至父组件受控，点「审校」始终带选中版本，审校按钮卡动态提示「将审校：草稿 vN」且随点选实时切换（内置浏览器实测：默认 v8 → 点 v7 → 提示切 v7）。测试：后端 2 新用例（指定版本/不存在版本），前端 1 新用例；全量 pytest 1252 passed / vitest 264 passed。
- **run 列表人类可读标签 + 草稿模型徽标（run↔草稿对应关系）**：用户反馈无法分辨哪个 run 对应哪版草稿。`GET /projects/{pid}/runs` 每行新增 `label`（推导规则：写正文 run 查 run 起止窗口内产出的草稿版本 → 「写正文 → 草稿 v6」，失败未产出则兜底「写正文」；审校 run 查启动前最新草稿 → 「审校（审 v5）」；plan/commit/init 用中文名；推导异常不抛错）。前端 run 行主文案优先 label（run_id 短形式退为副信息），草稿版本列表行显示 model_id 徽标（mock/mock 不显示）——配合 model_id 真实记录修复，版本与模型一目了然。测试：全量 pytest 1250 passed / vitest 263 passed；真实数据冒烟 17 行 run 的版本号与草稿精确对齐。
- **写正文「全新重写」模式 + drafts.model_id 真实记录**：(1) `StartWorkflowRequest.fresh_write`（可选布尔）→ ctx 注入 → `_writer_node` 在 fresh_write 时清空 revision_note 与旧稿底稿，强制 mode='write'——解决「选不同模型对比文风却被 revise 模式拉回 84% 相似」（V4/V5 实测教训：改稿模式下换模型只做局部修订）。前端写正文卡新增写作模式下拉（`wf-write-mode`：按意见改稿（默认）/ 全新重写）。(2) `_save_draft_node` 此前硬编码 `model_id='mock/mock'`，真实调用也无法区分是哪个模型写的；现 `_writer_node` 调用后从本节点 `ai_call_logs` 行（run_id+node_run_id 取最新）取真实 model_id 经 ctx 传入，mock 路径无日志行自然回落 'mock/mock'。测试：后端 3 新用例（fresh_write 覆盖 revise / 缺省回归 revise / mock model_id 回归），前端 2 新用例；全量 pytest 1250 passed / vitest 263 passed。
- **单次 run 级模型档案覆盖（模型效果对比）**：启动 chapter-plan / chapter-write / chapter-review 时可按次指定模型档案，不改动全局环节绑定。链路：`StartWorkflowRequest.model_overrides`（capability → `model_profiles.profile_id`）→ `_start_workflow` 注入 `ctx["model_overrides"]` → 三个 pipeline 的真实链路 `run_agent` 调用点（director / scene_planner / writer / critic 共 4 处）取 `capability_for(agent)` 对应档案 → `run_agent(profile_id=...)` → `ModelRouter.call_with_fallback(profile_id=...)` 候选锁定该档案（enabled=1，行形状与绑定分支一致，`config_id←profile_id`），缺失/disabled 抛 `ModelNotConfiguredError`（detail 含 profile_id），跳过 light→reasoning 回退；`profile_id=None` 时全既有路径零行为变化（负向用例实测）。mock 路径不消费。前端章节详情页「工作流操作」三个按钮卡（生成计划→reasoning / 写正文→creative_writing / 审校→light，提交不加速选）各挂档案下拉（`wf-model-select-{action}`，默认「环节绑定（默认）」，列表来自 `GET /model-profiles` enabled 过滤，拉取失败静默隐藏），选择后 payload 带 `model_overrides`，用于不同模型产出效果对比。测试：router 单测 5 个 + ctx 透传 workflow 测试 2 个 + 前端 ChapterDetailPage 新增 describe 2 用例；全量 pytest 1251 passed / vitest 261 passed。
- **V3.7 模型档案 + 环节绑定（两层架构）**：解耦「模型是什么」与「环节用谁」，同一条模型可被多个环节复用，不必复制多份。迁移 0016（`database/migrations/0016_model_profiles.sql`）新增 `model_profiles`（档案库：`profile_id / name / provider / model / params_json / enabled`）与 `capability_bindings`（环节分配：`capability → profile_ids JSON 数组`，顺序即 fallback 序）两张表，并把现有 `model_configs` 行一次性搬运成档案（`name` 取 `model` 字段）。`ModelRouter` 新增 `_candidates(capability)` 统一收口：优先读 `capability_bindings + model_profiles`，无 binding 回落 `model_configs`；返回行键名与 `model_configs` 完全一致（`config_id ← profile_id`、`capability ← 本 capability`），保证 `runner` 把 `config_id` 写 `ai_call_logs` 与 `get_provider` 零 schema 改动。`AGENT_CAPABILITY` 在 `model_router.router` 与 `agent_runtime.prompts` 双侧同步扩展四个 project-init agent 映射：`premise_designer → premise_design` / `world_builder → world_building` / `character_designer → character_design` / `volume_outliner → volume_outline`，避免 `capability_for` 默认回退 `reasoning`。新增 `CAPABILITY_LABELS`（七环节有序 dict：premise_design / world_building / character_design / volume_outline / creative_writing / reasoning / light，每项含 `label` + `agents`）供前端与 `GET /capability-bindings` 使用。新增 service `ProfileService` / `BindingService`（`packages/core/model_router/profiles.py` / `bindings.py`），新增路由 `model_profiles.py` / `capability_bindings.py`（自动发现，无需改 `main.py`），新增共享掩码工具 `packages/core/model_router/security.py`（`/model-configs` / `/model-profiles` 共用）。`light` capability 回退 `reasoning` 的 V3 P0-2 语义在 `_candidates` 收口后保留：仅「无 binding + `model_configs` 也无 light 行」时回退，有显式 binding 即便 binding 全 disabled 也不回退。`DELETE /model-profiles/{id}` 在被任何 binding 引用时返回 409（detail 含 `referenced_by` capability 清单，便于前端一键解绑）。旧 `/model-configs` 端点保留不动进入只读兼容期；新代码优先走两层 API，下一里程碑下线其写入语义。前端项目总览页的「AI 初始化设定」面板同步升级到按环节分配档案的引导式向导（前端联动由前端代理负责实现）。
- **项目总览页「AI 初始化设定」入口**：新增 `apps/web/src/components/ProjectInitPanel.tsx` 挂载在简介卡片之后、`StyleSamplesPanel` 之前。用户填写 brief（title/genre/logline/platform/target_words/author_notes/chapter_seed_count），提交触发 `POST /projects/init`（`projectsApi.init`，引擎同步阻塞到终态）。面板展开时探测 `charactersApi.listByProject` 角色数；>0 时显示「可能造成重复」warning 并要求勾选确认才可提交。成功展示 `run_id` 并回调父组件 `useApiCall.reload` 重载项目数据；失败以 `ApiError(status, detail)`（detail >200 字截断）走 ErrorBanner。`api/types.ts` 新增 `ProjectInitBrief`/`ProjectInitPayload`/`ProjectInitResponse`；`api/endpoints.ts` 的 `projectsApi.init` 仅做薄封装，未引入新依赖。`chapter_seed_count` 走 payload 顶层字段以命中后端 `Field(ge=1, le=100)` 校验（不进 brief），前端同步做 1-100 范围校验。新增 `ProjectInitPanel.test.tsx` 覆盖 5 个用例：logline 空禁用 / 正确参数调用 + projectId + seed_count 在顶层 / 有既有角色需勾选 / 成功展示 run_id 且 onDone 被调用 / seed_count 越界(150)禁用。
- **project-init 分段审阅暂停（step_mode）**：`packages/workflows/project_init/pipeline.py` 新增 `STAGE_SPECS` / `_gate_should_pause` / `_resolve_stage_input`，4 个 AI 节点在 `step_mode=True` 时成功产出后抛 `PauseRequested({"stage", "stage_index" 0..3, "stages_total":4, "degraded", "draft"})`，下游 `_world_payload` / `_character_payload` / `_outline_payload` / `_persist_all_node` 全部改走 `_resolve_stage_input` 三层 fallback（`human_input.revisions[output_key]` → `ctx[output_key]` → `ctx[node_id].__pause_payload__.draft`），确保人工修订穿透到下游 AI 输入与 `persist_all` 落库。`packages/core/api/routers/workflows.py` 的 `ProjectInitRequest` 新增 `step_mode: bool | None`（默认 None = 一次性跑完，与既有行为一致），`_start_project_init` 返回体在 PAUSED 时附 `pause_payload`，复用通用 `POST /runs/{run_id}/resume` 端点回灌 `human_input={"revisions": {"<output_key>": <修订后完整 dict>}}`，引擎不变。`packages/workflows/project_init/README.md` 同步更新节点图、4 关卡表、pause_payload 与 human_input 契约、修订生效路径。`tests/workflow/test_project_init.py` 新增 4 用例（第一关即停 / 修订推进到第二关 / 四关走完落库生效 projects.name 来自修订 / 默认不分段回归）。前端侧：项目总览页面板升级为分步审阅向导（题材定位 → 世界观 → 核心角色 → 卷纲与章节种子），mock 自测全绿。
- **Story State Delta 确定性自动修复层（arbiter-lite）**：新增 `packages/core/story_state/delta_repair.py`，`repair_delta(delta, snapshot, db_path)` 在 `validate_delta` 前做确定性自动修复：world/character/relationship `op='update'` 且 `before=None` 时从 snapshot/DB 补当前值；`op='add'` 且目标 id 已存在时降级为 `update`（内容与现有行一致则丢弃）；同步覆盖 debt_changes `status_before`/`severity_before`、resolved_hooks `from_status`、new_events/new_hooks 重复 id 丢弃。`packages/workflows/chapter_commit/pipeline.py` 的 `_inject_validate_node` 在首次校验与重试后均先 repair 再 validate，修复记录写入 `ctx['delta_repairs']` 并 logging.info 留痕；修不了的仍走原校验/按腿重试路径。新增单元测试 `tests/unit/test_delta_repair.py` 与 workflow 层一次性修复回归测试。
- **去 AI 味确定性检测**：新增 `packages/core/quality/ai_patterns.py` 纯函数模块，`scan_ai_patterns(prose)` 基于正则/统计检测 AI 腔（高频套话、连续同词开头、「他/她」排比、章尾总结体、破折号/省略号滥用、解释腔），命中数超阈值可升 error；规则表 `AI_PATTERN_RULES` / `AI_PATTERN_FORBIDDEN_WORDS` 模块级可扩展。
- **chapter_review 接入 AI 腔检测**：`_basic_checks_node` 调用 `scan_ai_patterns`，`review_report` 新增 `ai_pattern_hits` 字段并合并进 `warnings`/`errors`；原 `forbidden_word_hits` 向后兼容保留。
- **critic payload 注入确定性摘要**：`_critic_review_node` 向 critic LLM 传入 `deterministic_hints`（命中数 + 规则摘要），`docs/agents/prompts/critic-v1.md` 同步说明该字段仅供 LLM 参考；critic 输出契约无需放宽（仅新增输入字段）。
- **ScenePlanner 真实化**：`packages/workflows/chapter_write/pipeline.py` 新增 AI 节点 `scene_planner`（P0），调用 `scene_planner` agent 将 Director Plan 翻译为结构化 Scene Plan（含 slots / conflict / turn / information_boundary / ending_hook）。新增 `docs/agents/prompts/scene_planner-v1.md`、agent capability 注册、`expected="scene_planner"` 契约校验。支持 `mock_providers['scene_planner']`；agent 失败时降级到原 stub 逻辑，不阻断 writer。
- **自动改稿回路**：`packages/core/api/routers/workflows.py` resume 端点新增 `auto_revise_max` 与 `mock_providers` 字段。chapter-review 以 `rejected-for-revision` 失败且 `auto_revise_max > 0` 时，自动依次重跑 chapter-write → chapter-review，直到 approved 或达到上限。上限默认 `NOVELOS_AUTO_REVISE_MAX=2`，`0` 表示禁用（保持现有 FAILED 终态语义不变）。
- **服务启动自动 prompt 同步**：`packages/core/api/main.py` lifespan 在迁移后自动执行 `PromptRegistry.sync_from_docs`（幂等），新 agent 不再依赖手工 `POST /agents/sync`（ch072 scene_planner 未注册静默降级的教训）。`NOVELOS_PROMPT_SYNC=off` 可关闭；sync 失败只告警不阻断启动。
- **m1_long_run `--quality-gate-mode` 开关**：此前驱动脚本硬编码 `quality_gate_mode="report"`，enforce 默认值在生产长跑链路无法被验证。现可通过 `--quality-gate-mode enforce` 覆盖（默认仍 report，保持长跑不阻断语义）。

### Changed
- **critic 默认全量**：`packages/workflows/chapter_review/pipeline.py` 默认 critic 模式从 `sample` 改为 `always`，`NOVELOS_CRITIC_MODE` / `ctx['critic_mode']` 覆盖逻辑保留。
- **quality_gate 默认 enforce**：`packages/workflows/chapter_commit/pipeline.py` 默认 quality_gate 模式从 `report` 改为 `enforce`，与文件顶部 docstring 对齐。eval / golden runner / 相关测试已显式设为 `report` 模式以避免 MVP 阻断规则误伤。

### Migration
- 使用自动化脚本或外部 runner 调用 commit 且依赖「默认不阻断」行为的调用方，需显式传入 `quality_gate_mode="report"` 或设置 `NOVELOS_QUALITY_GATE=report`。
- 原有 revise 后依赖手动重跑 write+review 的测试/脚本，若需保持旧行为，resume 时应传 `auto_revise_max: 0`。

### Fixed
- **Provider HTTP 连接池上限修复（单章耗时根因）**：`packages/core/model_router/providers.py` 三个 provider 的 `_ensure_client` 统一加 `httpx.Limits(max_connections=10, max_keepalive_connections=5)`。此前 client 从不关闭、keep-alive 无限堆积（实测长进程 114 条到 MiniMax 的 ESTABLISHED 死连接），新请求被路由到被对端静默挂起的连接上无限等待（httpx read timeout 为字节间隔语义，对整连接挂起不生效），是单章 600-900s 的主因。详见《单章耗时诊断报告.md》根因 1。
- **`MINIMAX_API_KEY` 映射下沉到 api.main**：此前 `MINIMAX_API_KEY → NOVELOS_API_KEY_OPENAI_COMPATIBLE` 映射只在 `scripts/serve.py`，`python -m packages.core.api.main` 直启（m1_long_run docstring 的推荐姿势）不带映射导致 401 秒失败（ch072 实测复现）。现映射在 `packages/core/api/main.py` 模块导入时执行，覆盖所有入口。
- **extract_json 兼容裸控制字符**：`packages/core/agent_runtime/structured_output.py` 的 `extract_json` 在严格解析失败后用 `json.loads(strict=False)` 兜底一次——LLM 偶发在字符串内输出未转义控制字符（ch074 observer 输出实测，首次 commit 因此失败），strict=False 允许字符串内控制字符通过。其余解析失败行为不变。
- **delta_repair 补 update-to-add-field 规则（ch073 实测缺口）**：observer 对实体的 data_json/state **新子键**用 `op='update'` 且 `before=None` 时，旧值本就为空属诚实表达，但校验器拒绝 update+before=None，导致 commit 反复重试（ch072 连挂 3 次、ch073 挂 1 次）。现 `_repair_world_changes` / `_repair_character_changes` 在「实体存在但字段无现值」时把 update 转为字段级 `add`（validator 接受 add+after；write_through 对已存在 id 的 add 走 UPDATE，安全）。`after` 也为 None 的语义空洞条目仍不修，留给校验器报错。

### Added（P2 Context Engine 补全）
- `build_director_input.plot_graph_excerpt.unresolved_branches` 不再恒空：从 `branches` 表查询 `status='ACTIVE'` 的未解决分支并注入 `{branch_id, name, parent_branch_id, base_state_version, status}`。
- `build_writer_input.world_state_excerpts.sensory_anchors` 感官锚点：在零 DDL 约束下，从 `locations.data_json.sensory_anchors` 或常见感官字段（`sensory_details / atmosphere / smell / sound / light / texture / temperature`）解析，最后回退到 `statement`；无数据时返回空 list。
- Writer 章节级相关性裁剪 `relevance_trim`：默认开启，按 `plan_json` / `scene_plan` 的 `involved_characters` / `involved_locations` 过滤角色与世界观条目；主角（`role='protagonist'`）与 `inject_mode='always'` 实体始终完整保留，未涉及实体降级为 `{id, name, relevance_summary: True}`。
- `relevance_trim` 开关：显式参数 > 环境变量 `NOVELOS_CONTEXT_RELEVANCE=off` > 默认开启；Writer 缓存键追加第 7 元 `relevance` 避免脏命中。
- `preview_context` L1 新增 `unresolved_branch` 与 `sensory_anchor` 条目展示。
- 新增测试 `tests/unit/test_context_engine_p2.py`（13 用例）覆盖三项缺口；`tests/unit/test_writer_input_paged.py` 模块级关闭 `NOVELOS_CONTEXT_RELEVANCE` 以保持 paged 体积断言独立。

### Added（P1 project-init workflow）
- 新增 `project-init` 工作流：`load_brief → premise_designer → world_builder → character_designer → volume_outliner → persist_all`，实现从题材 brief 到可开写 Story Bible 的全自动建书。
- 新增 4 个 AI agent 与 prompt：`premise_designer`（题材定位/卖点/主角雏形）、`world_builder`（世界观/规则/地点/势力）、`character_designer`（3-5 核心角色）、`volume_outliner`（第一卷卷纲 + 前 N 章种子，默认 10 章）。
- 新增 `POST /api/projects/init` 端点：支持不带 `project_id` 新建项目，或传入已有 `project_id` 挂载更新。
- AI 节点失败按 critic 模式降级（返回 `_degraded` 结构、不阻断流程）；`persist_all` 失败直接抛错。
- 复用现有 domain service 落库：`ProjectService`、`CharacterService`、`WorldService`、`VolumeService`、`ChapterService`、`PlotService`。
- 新增测试 `tests/workflow/test_project_init.py`：覆盖新建项目、挂载已有项目、AI 降级、persist 失败。

> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：版本号 + 日期 + 分类小节
> （Added 新增 / Changed 变更 / Fixed 修复 / Removed 移除 / Migration 迁移 / Known Issues 已知问题）。
> 版本号语义化：破坏性变更升 major，新功能升 minor，修复升 patch。

## [3.8.0] - 2026-08-27

### Fixed（V3.8「配置参数透传修复 + 思考模式灰度定稿」）
- **ModelRouter.call_with_fallback 配置参数丢失 bug（潜伏缺陷）**：`model_configs.params_json` 中除 `base_url/timeout_s/api_key` 外的所有键（`thinking`/`service_tier`/`temperature` 等）此前从未透传到上游请求体——配置写了 `thinking:disabled` 与 `service_tier:priority` 实际从未生效。现按行解析非构造键并入请求体，调用方显式 params 同名覆盖。`resolve()` 判定无需修（无生产调用方）。

### Added
- 思考模式灰度定稿（证据驱动，三章实测）：`reasoning`(director/critic) 与 `light`(observer 双腿+summarizer) 配置 `{"thinking":{"type":"disabled"}}`；`creative_writing`(writer) 保持思考开启。
- provider 原始响应采样开关（V3.7 引入）用于本版取证：completion 字符的 **78%~94% 为 `<think>` 思考串**。

### 实测对比（m1 ch069/ch070/ch071）

| 阶段 | 思考开 | 禁思考 |
|---|---|---|
| observer 双腿(合计 wall) | ~185s | **13~39s**（腿间缓存命中达 98%） |
| summarizer | 15~24s | **3~4s** |
| director plan | 104~224s | **22s** |
| writer | 125~135s（字数带内 +8.8%） | 10s 但字数 -40% 跌破 band |

单章全链：V3.6 基线 658~866s → 仅 light 禁思考 320s → 全禁 63s（writer 破带）→ **定稿组合 198s 且 prose 在带内、quality=82**。

### Known Issues
- 全禁思考下 writer 产出偏短（实测 -40% 跌破 band 下限）；若未来要全速模式需配套 revision 重写闭环。
- ch069 首次 commit 三连秒失败为本次配置误操作（light 行 base_url 被整串替换）所致现场，已从备份恢复；相关 error 记录留 DB 作审计。

## [3.7.0] - 2026-08-27

### Added（V3.7「字数带硬约束与口径统一」）
- **权威字数模块** `packages/core/quality/wordcount.py`：`visible_chars`（去空白口径）/ `word_band`（[0.7,1.3] 带 + 1200 下限保护）/ `classify_prose_length`。
- writer payload `chapter.word_band` 注入（置于 chapter_id 之后零破坏缓存键序，paged 模式原样继承）；review `_basic_checks_node` 升格产出 `W-LEN-DEVIATION`：>±15% warning、>±30% 进 errors（报告型：随 pause payload 展示），前端 ApprovalCard 红色渲染 errors 区块；m1 metrics 新增 `word_status`/`deviation_pct`。
- provider 原始响应采样开关 `NOVELOS_DEBUG_PROVIDER_DUMP_DIR`（默认关；保留最新 10 个文件、单文件 2MB 截断、异常不阻主流程）。
- `scripts/db_maintenance.py`：stale RUNNING 清理工具（list / fix --older-than-minutes N [--apply]，dry-run 默认，幂等）。（提前交付自迭代计划 V3.9 C1）
- 迭代计划文档 docs/roadmap/v3.7-v3.9-迭代计划.md（含分段写作实验否决记录与候选项闭环映射表）。

### Changed
- 全仓 prose 字数口径统一：signing_check/_count_chars、arc/service、m1_long_run._latest_prose_chars、chapter_review 四处收敛引用 wordcount.visible_chars。

### Fixed
- word_band 低目标 floor 抬升导致 low>high 倒挂：high 钳制 `max(high, low)` 并锚定边界测试（恰 ±15%/±30% 判定方向用例固定）。


### Added（V3.6「流式根治挂起 + 前缀缓存重排 + commit 三路并发」）
- **OpenAI 兼容 Provider 流式化**：`complete()` 改 `client.stream` + SSE 逐行解析（`data: {...}` / `[DONE]`），并加 `time.monotonic()` 总时长硬顶 deadline——根治非流式下整连接挂起而 read timeout（字节间隔语义）不生效的问题。`health_check` 保持非流式。
- **commit 三路并发**：summarizer 的 LLM 调用提前并入 observer 双腿并发池（`NOVELOS_SUMMARY_PARALLEL` 默认 on，ctx/env 可关），下游 summarize 节点短路消费 `summary_early`，缺字段/失败自愈重调；`summary_early` 列入 checkpoint_exclude 保证 PAUSED→resume 幂等；早产失败恢复 run 状态防污染。
- **竞品对标与单章提速决策文档**：docs/roadmap/v3.6-单章提速与竞品对标.md（oh-story / determinFlow / bishu-novel 管线对比、思考 token 数据真相、V3.8 方向）。

### Fixed
- 流式响应 usage 全丢：请求强制注入 `stream_options: {"include_usage": true}`（OpenAI 流式协议默认不下发 usage；实测 MiniMax 如此）。
- prompt 前缀缓存几乎零命中：5 个 agent 的 user payload 键序重排——稳定块（project/knowledge_permissions/constraints 等）前置、每章动态块（chapter 及其 chapter_id）后置。ch067 实测 plan 二次装配 cached_tokens=16896/17981=94%、summarizer=100%。
- serve.py 把通用 `MINIMAX_API_KEY` 映射到 provider 期望的 `NOVELOS_API_KEY_OPENAI_COMPATIBLE`（此前缺映射导致 401 秒失败）。

### Changed
- tests：SSE mock 全面适配（test_model_router / test_model_router_providers / test_cached_tokens_extraction）；空流语义统一为"零 content chunk 即抛错"；新增三路并发 13 个测试。全量 707 passed, 2 skipped。

### Known Issues
- writer 单次 completion 达 42~49k tokens，其中 ~95% 为 MiniMax-M2 内部思考 token（正文仅 ~2KB）；未启用 max_tokens 硬顶（截断风险破坏 JSON 契约）。V3.8 方向：字数带硬约束 + writer 分段写作（见 docs/roadmap/v3.6 文档 §四）。
- 实测本章剩余耗时由 MiniMax 服务端解码主导（plan+write ≈ 550~580s/章）；本地环节 <15s。

## [3.5.0] - 2026-08-26

### Added（V3.5「并发化与缓存观测」）
- **observer 双腿并发**：split 路径下腿 A/腿 B 经 ThreadPoolExecutor 并行提交（`NOVELOS_OBSERVER_PARALLEL` 开关默认 on，ctx/env 可关），单腿异常仍走原 per-leg 重试归类；实测两腿几乎同时启动。
- **cached_tokens 观测**：OpenAI 兼容 provider 提取 `usage.prompt_tokens_details.cached_tokens` 写入 ai_call_logs.token_usage_json（缺省不写字段），为 MiniMax 自动前缀缓存命中率提供数据基础。

### Fixed
- smoke_e2e / test_health / README 的业务表数断言链统一修正为 35（volumes 为唯一新增表；0014 系重建既有 reveal_policies 不增数）。

### Known Issues
- prompt 前缀缓存命中率待真实链路采样（cached_tokens 已可观测）；若命中率低需将 user 消息内动态字段后置。

## [3.4.0] - 2026-08-26

### Added（V3.4「多卷与规模」组织层）
- **卷管理**：迁移 0015（volumes 表 + chapters.volume_id）；VolumeService 与 REST 六端点（CRUD/seal/assign）；封存时冻结终态快照入卷归档；单项目同时仅一个 active 卷；sealed 卷拒绝挂章。
- **arc 按卷分组**：弧光视图章节元素携带 volume 归属，顶层新增 volumes 小节。

### 设计决策
- 未做按卷拆分快照存储——O-1 滚动窗口已实证上下文有界（63 章 trimmed 输入 31k 字符），存储级拆分的复杂度税不成立；150+ 章实测恶化时再按设计文档 §三升级快照分代。健康端点业务表数 35→36（volumes）。

## [3.3.0] - 2026-08-26

### Added（V3.3「知识权限补全」）
- **reveal_policies 数据模型**：迁移 0014——relationships/timeline_events/scenes 三表补 visibility+who_knows（I6 收口）；reveal_policies 表（target_kind 枚举/reveal_by_chapter/audience/status 机）+ CRUD API（GET/POST/PATCH/DELETE /api/projects/{pid}/reveal-policies）。
- **writer paged 装配可见性过滤**：HIDDEN 且存在 planned reader-audience 策略的实体从 writer 上下文整条移除（stats 记 hidden_filtered）；无策略时零行为变化；observer 保持作者全知视角（设计决策）。
- **arc 视图扩展**：reveal_policies 小节（planned/revealed/overdue 清单）。
- **模块轻量开关**：`NOVELOS_DISABLED_MODULES=simulation,arc` 按模块名过滤路由注册（默认空=零变化；仅过滤 HTTP 面，workflow 注册隔离为已知边界）。

### Changed
- 两条内置模型配置启用 `service_tier=priority`（MiniMax 官方优先准入，对策拥堵窗口大请求饿死；成本 1.5x）。

## [3.2.3] - 2026-08-26

### Fixed（V3.1.1 O-3）
- **observer 按腿输入裁剪**：腿 A 移除 events/hooks/debts（previous_state -65.5%）、腿 B 实体降级为标识摘要——双腿合计 payload 47.9k 字符，总 token ~120k→89k（-26%）。
- **近期 event_id 白名单**：payload.config 注入最近 30 个 event_id + prompt 强制避让，消除 observer 生成 id 与库中历史事件撞车导致的 UNIQUE constraint failed（ch063 实战首次尝试即 COMMITTED）。

### Known Issues
- provider 拥堵窗口下单腿仍可能 480s 超时（网络抖动同源），断点续跑可自愈；根治候选为流式调用或备用 provider 失败转移。

## [3.2.2] - 2026-08-26

### Added（V3.1.1 observer 减负专项 O-1/O-2）
- **事件摘要滚动窗口**：observer trimmed 输入的 events 摘要只保留最近 6 章触达 + 被 open hooks/debts 引用者，open hooks/debts 字段压缩；实测输入 92.6k→31.3k 字符（-66%）。
- **observer 双腿拆分**：`NOVELOS_OBSERVER_SPLIT` 开关（默认 on）——腿 A 实体状态（characters/relationships/world_changes）、腿 B 叙事对象（events/hooks/debts）各自独立调用与重试（按校验错误归类到出错腿），light capability 可配；run_agent 支持 capability_override。ch063 实战：单腿拥堵超时不再导致整次死锁。

## [3.2.1] - 2026-08-26

### Fixed
- **事件描述下沉 plot_events**（V3.1 P1-1.1 遗留）：新增迁移 0013 为 plot_events 加 description 列；write_through 写入 observer 的 `new_events[].description`；快照 DB 权威重建后事件描述不再丢失（漂移自愈测试同步更新语义）。

### Known Issues
- writer paged 模式的 LLM 输出质量 A/B 对比因 provider 长请求拥堵窗口暂缓，待稳定窗口补测（结构断言与体积量化已在 [3.2.0] 覆盖）。

## [3.2.0] - 2026-08-26

### Added（V3.2「写作现场完善与上下文分页」）
- **writer 上下文分页**：chapter-write 支持 `writer_context_mode=full|paged`（默认 paged，env `NOVELOS_WRITER_CONTEXT_MODE` 可全局回退 full）；paged 模式下实体集合按「最近触达全量 + 其余摘要」裁剪并注入裁剪统计。实测当前数据集缩减 5.4%（大项目收益更大，20 角色合成场景单键 -37%）。
- **叙事弧光视图**：`GET /api/projects/{pid}/arc` 聚合逐章 payoff/charge 标注、质量分、伏笔与债务台账，输出蓄力连击、兑现密度、逾期告警等弧线健康信号（含 51 条测试）。
- **CI 增强**：新增 pip-audit 依赖 CVE 扫描 job 与 e2e smoke job（真实起服务跑 scripts/smoke_e2e.py 全链路 + 状态同步巡检）。

### Changed
- chapter-write 默认上下文模式变更为 paged（原 full）；`writer_context_mode="full"` 或环境变量可回退旧行为。

### Known Issues
- paged 模式对 writer 输出质量的 LLM 实评未做（需对比 eval），当前仅结构断言与体积量化。

## [3.1.0] - 2026-08-26

### Added（V3.1「一致性治理与质量语义升级」）
- **快照漂移自愈**：commit 事务内 write_through 之后、逆路径清理之前，以 DB 为权威重建快照的 7 类实体集合（characters/locations/factions/world_rules/plot_events/hooks/narrative_debts）——历史漂移在下次 commit 自动收敛；分支路径不受影响。含漂移自愈回归测试。
- **LLM judge 双轨落库**：迁移 0012 为 quality_reports 新增 judge_json 列；`POST /api/chapters/{cid}/quality/judge` 持久化四维评审（pacing/style/logic/dialogue），GET quality 响应透出 judge 字段；m2_judge.py 支持 `--persist-api` 直连落库。judge 为旁路数据，不参与 overall 计算（七子分公式哈希不变）。
- **快照一致性巡检工具**：scripts/check_state_sync.py 只读对比 DB 实体表与 snapshot JSON 的 7 集合双向差集，退出码 0/1/2 可接 CI；已接入 smoke_e2e 第 10 步（每次冒烟自动验证零漂移）。

### Changed
- 快照 world_rules 等集合字段口径统一为 DB 权威语义（snapshot.events[].description 在首次重建后收敛为 NULL，属预期行为）。

### Known Issues
- new_events[].description 信息源未下沉 plot_events 表（DB 权威重建后该字段丢失），V3.1 P1-1.1 候选。

### Fixed
- 巡检脚本 world_rules id_field 错配（rule_id→world_rule_id）导致的假 DRIFT。

## [3.0.0] - 2026-08-26

### Added（V3.0「提速与减负」）
- **critic 采样模式**：`NOVELOS_CRITIC_MODE=off/sample/always`（默认 sample 每 5 章 1 次），请求 body `critic_mode` 可覆盖；跳过时工作流状态机不受影响。
- **light capability 分级路由**：critic/summarizer 映射到可配轻量模型，缺失自动回退 reasoning 链（零破坏，回退有日志）。
- **快照一致性巡检**：`scripts/check_state_sync.py` 只读对比 DB 实体表 vs snapshot JSON 七类实体集合，退出码 0/1/2 可接 CI；实测最新快照与 DB 对齐（早期版本漂移已随重写消除）。

### Changed（性能检修实测，单章 804s → 609s，-24%）
- **输出量瘦身**：observer `max_changes_per_array` 50→24+宁缺毋滥纪律；critic 限长 800 字；writer self_report 限 150 字；模型配置默认 thinking disabled。分环节实测：write 236-363s→32-136s、review 56-139s→29-37s。
- **超时策略**：模型配置 timeout 建议 1800s→480s（长等无恢复案例），驱动客户端 900s 快速失败重试。

### Fixed
- write_through 将 observer 自由文本 location 直塞 `plot_events.location_id` 外键导致 `FOREIGN KEY constraint failed`：加存在性守卫（无效置 NULL 不阻断）+ observer prompt 约束；含回归测试。

### Known Issues
- 单章耗时距 ≤300s 目标尚差（当前 609s）：commit 环节 observer 仍占 338-494s，结构性解法（quality 异步旁路/上下文分页/输出硬上限）排 V3.1 P1-3 与 V3.2 P2-1——见 docs/roadmap/v3-plan.md 实测表。

## [2.2.0] - 2026-08-25

### Added（M1~M4 里程碑：真实长跑验证与上限能力）
- **M1 真实模型长跑**：MiniMax-M3 连续生成 50+6 章，产出《一致性衰减观测报告》（docs/evaluation/m1-long-run-report.md）。核心结论：状态机零衰减实证（continuity 全程 99.4-99.7）；文风碎片化退化被定位并以 prompt 规则修复；payoff 闭环缺失被定位到 director 层。新增驱动脚本 scripts/m1_long_run.py（断点续跑/熔断/指标采集/单实例锁）与报告工具 scripts/m1_report.py。
- **M2 上限能力干预**：director 增 [payoff] 前置/字数纪律/叙事推进纪律/停滞强制升级/交互密度下限规则；writer 增反碎片化/字数下限/对话占比/payoff 可感知兑现规则。验证（docs/evaluation/m2-verification.md）：LLM judge 四维均分 34→69，对话维度 5→72，单章字数 849→1738-1930。新增 judge 探针 scripts/m2_judge.py（爽感/文风/逻辑/对话四维结构化评审）。
- **M3 量产管线硬化**：observer 输入快照分代裁剪（trimmed 模式，解 >110KB 快照超时）；validator 引用存在性校验（FK 失败前置为可自愈校验错误）；commit 管线接线两项能力——阻塞章 ch056 实战验收从 90 分钟不可解变为 253 秒一次通过。新增单实例文件锁（防双进程 SQLite 挂死）与按环节成本计量（observer 占 token 51.4%）。
- **M4 写作现场·续写助手**：`POST /api/projects/{pid}/chapters/{cid}/continue` 一键生成最多 3 个续写候选（temperature 差异化），`/continue/adopt` 采纳追加为新草稿版本（REVIEWED 自动降级 DRAFTED 待重审）；前端 ContinuePanel 三候选并排对比面板挂载章节详情页。
- quality 引擎新增 style 连续衰减监测（report-only，公式哈希不变）；golden 回归 eval 扩充至 3 case。

### Changed
- provider 请求体清理：params_json 中 base_url/api_key/api_key_env 不再泄漏进上游请求体。

### Known Issues / 路线登记（新增）
- LLM judge 目前是旁路探针，尚未正式接入 quality 评分语义（style 子分与 judge 口径不一致，见 m2-verification.md 遗留2）。
- 叙事主线惯性（量桩微循环）需故事弧光级管理工具，prompt 层只能缓解——归入 M4 后续写作现场迭代。

## [2.1.0] - 2026-08-24

### Added（番茄签约体检包）
- **新模块 packages/core/signing_check/**：面向番茄男频投稿的纯规则体检（无 LLM / 无新依赖 / 无 schema 变更）。检查项：第一章前 300 字冲突（ch1_conflict_300）、主角前 500 字出场（ch1_protagonist_500）、金手指前 1000 字亮相（ch2_golden_finger）、第三章小高潮/打脸（ch3_climax）、逐章章末钩子（chapter_hooks_chN）、单章字数区间 1200-2600 warn（chapter_length_chN，1500-2200 最佳）、高频副词堆叠检测 echo_words（>5 次/千字告警，对抗 AI 低质文的"复读词"问题）、签约窗口提示 signing_window（2万/5万/8万共 3 次机会）。
- **REST 端点**：`GET /api/projects/{project_id}/signing-check`，返回 items + summary 计数；项目不存在 404。见 packages/core/api/routers/signing_check.py。
- **番茄投稿包集成**：build_fanqie_package 导出末尾追加「===== 签约体检摘要 =====」段；摘要生成异常时跳过不阻断导出。
- 设计依据：2026 番茄平台 AI 低质文严打（黄金三章硬标准/签约窗口规则）与竞品调研（NovelCrafter Codex/Sudowrite Story Bible 的一致性治理已由 Story State 覆盖，本模块补齐平台规则体检缺口）。词典为模块级常量可扩展；启发式非保证，边界见 packages/core/signing_check/README.md。

### Known Issues / 路线登记（新增候选）
- signing_check 词典为子串匹配，存在误命中可能（如单字词「打」「血」）；后续可引入白名单或窗口规则校准。
- signing_check 暂无前端 UI 面板（API-first）；候选项：web 端体检报告面板。
- scripts 下 e2e/smoke 临时数据库建议改用系统临时目录，避免与主库同目录。

## [2.0.1] - 2026-08-24

### Fixed（全维度检修包）

- **lint 清零**：修复 260 处 ruff 错误（unused-import / 行超长 / import 排序等，涉及 packages / tests / scripts 共 23 个文件），`ruff check packages tests scripts` 恢复 All checks passed，CI 后端 job 恢复可过；story_state/service.py 的 facade re-export 统一加 `# noqa: F401` 防止自动修复误删。
- **CI 补前端 job**：`.github/workflows/ci.yml` 新增 web job（Node 20，npm ci → build → vitest），此前 195 个前端测试不在 CI 内。
- **版本号元数据对齐 2.0.0**：pyproject.toml / apps/web/package.json / packages/core/api/main.py `__version__` 三处由 0.1.0 同步为 2.0.0；test_health 与 test_spa_hosting 的版本断言改为引用 `__version__` 常量，后续升版只改一处。
- **文档一致性**：docs/data-model/data-model-v0.md 加现状注记（v0 的 28 张表 → 当前 34 张业务表 + chapter_fts，附增量迁移清单）；IMPLEMENTATION-PLAN §4.2 deviation #4（model-configs 明文返回）标记已关闭（router 层 `_mask_response` 已落地）；补 packages/core/workflow_registry/README.md；README 移除对停维护 apps/desktop/vite.config.ts 的消费性表述。
- **新增 ADR-0002**：「model provider API Key 在本地 SQLite 明文存储（MVP 决策）」，记录既有防线（出口掩码 / ai_call_logs 敏感字段黑名单 / 备份排除 model_configs）与未来升级路径。
- **工作区卫生**：清理 data/ 下 smoke_e2e_* / real_llm_e2e_* 等测试残留文件（主库 novelos.db 不受影响）。

## [2.0.0] - 2026-08-24

结构性升级（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。含内部架构破坏性调整，对外 API 契约保持兼容。

### Changed（架构）

- story_state god-object 拆分：`service.py` 2332→138 行 façade（`StoryStateService` 全委派 + 私有符号兼容 re-export），按职责拆为 `snapshots` / `deltas` / `write_through` / `commits` / `branches` / `queries` 六模块；调用方零改动、零行为变化（545 项回归全绿证明）。
- 分支读路径快照物化：迁移 `0009` 新增 `branch_snapshots` 表；创建分支物化基线、`promote` 后物化新基线；分支读取 = 最近物化快照 + 增量重放（回退全量重放），消除 O(N) 全量重放；增量计数有 spy 断言守护（增量 2 次 vs 全量 5 次）。
- 双写面统一：canon 写透与 domain CRUD 的实体写入收敛到 `story_state/write_helpers.py` 共享助手（`who_knows` 三态 / JSON 序列化 / 时间戳 / NULL 守卫两路逐字节一致）；`plot_events` / `timeline_events` 因派生索引语义差异保留双写（README 已声明口径：canon 以 observer delta 为权威）。
- 上下文装配缓存：`(project_id, state_version, chapter_no, role, 内容指纹)` 五元键进程内缓存（线程安全、256 上限）；失效三维——`state_version` 推进 + `plan_json` / `scene_plan` 内容指纹变化 + commit 后显式失效兜底。
- 端口收敛：唯一配置源 `Settings.api_port`（`NOVELOS_PORT` > `NOVELOS_API_PORT` > 默认 18081）；`main.py` / `vite.config` / smoke 脚本统一走配置。

### Added

- 设定条件触发动态注入（对标 Lorebook / Codex）：迁移 `0010` 给 `characters` / `locations` / `factions` 加 `aliases` + `inject_mode`（`auto` / `always` / `never`）；命中实体完整注入、未命中降级一行摘要、`never` 剔除；L0 世界规则常驻；`context-preview` 与前端面板带 `full` / `summary` / `suppressed` 徽标。
- FTS5 召回混合层：迁移 `0011` 建 `chapter_fts` 虚表（CJK bigram 索引化，零第三方依赖）；commit 成功后 upsert 索引（失败降级不阻断）；装配注入 `recalled_passages`（按章节计划关键词召回 top3 历史片段 × ≤300 字，跨长程呼应）；新模块 `packages/core/retrieval/`。

### Fixed

- 装配缓存脏命中（审查 P1）：`plan_json` 更新或 `scene_plan` 变化但 `state_version` 不变时返回陈旧上下文——键补内容指纹 + commit 后显式失效。
- FTS 查询含引号 token 静默降级；纯 CJK 长串不再作为无效整词 token 进入召回。

### Known Issues / 路线登记

- vitest 全量并发偶发单测顺序敏感（V1.5 起观察到 2 次，重跑即绿），待定位根因。
- `plot_events` / `timeline_events` 双写面未统一（派生索引语义差异，见 `story_state` README）。
- 物化按 N commit 间隔多次物化未启用（MVP 仅分叉点 / promote 两点）。
- 召回为 FTS5 关键词基线；向量 embedding 召回留待后续（需先选型论证）。

## [1.5.0] - 2026-08-24

架构债务与 UI 补缺包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- What-if 分支 UI：项目总览页新增 `BranchesPanel`（分支列表 / 创建 / 分支状态查看 / promote 一键重放，409 冲突反馈）；纯前端落地（后端 `branches` / `state` 端点此前已齐备），补 `branchesApi` 封装与 `getBranchState`。
- workflow 注册表下沉 core：新增 `packages/core/workflow_registry/`（惰性 builder 注册中心），core 业务模块对 `packages.workflows` 零引用（AST + 字面值双重静态扫描测试守护）；`api/main.py` 以 `importlib` 装配触发注册；`packages/workflows` 保留兼容 façade。
- 前端「清除已存密钥」按钮：`AiSettingsPage` 编辑对话框显式清除入口（`clearKeyRequested` 机制，提交 `api_key=''` 触发后端清除；重新输入自动取消清除意图）。

### Changed

- router 越层 SQL 清零：`reference` / `workflows` / `model_configs` 三路由的 SQL 全部下沉——新增 `domain/reference/service.py`（`ReferenceService`）与 `core/model_router/configs.py`（`ModelConfigService`），`chapter` / `workflow_runtime` 各补一个查询函数；路由只留参数校验 + 脱敏 + 错误映射，API 契约零变化。
- `checkpoint_exclude` 推广到章节工作流（resume 影响已逐字段论证并测试守护）：`chapter-review` 剔除 `review_report` / `critic_*`；`chapter-commit` 剔除 `observer_input` / `observer_payload` / `delta` / `submit_result` / `snapshot_pre`；`pause_payload` 独立不受影响，checkpoint 写放大下降。

### Fixed

- （无用户可见修复；两项代码健壮性整理：`delete_canon_cascade` 连接关闭统一 finally、`runs.py` 函数定义位置规范）

## [1.4.0] - 2026-08-24

发布链路与可观测包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- 导出发布链路：`GET /api/projects/{pid}/export` 支持整书/单章导出 txt（utf-8-sig）与 docx（手工 OOXML zip，零新依赖），以及番茄投稿包（前 ~1 万字按章节边界截断 + 分隔线 + 全书大纲）；文件名 RFC 5987 双写法；项目总览页新增「导出」面板。新模块 `packages/core/exporter/`。
- 项目备份/恢复 MVP（PRD §100/§101）：`GET /api/projects/{pid}/backup` 导出 22 张业务表 JSON 包；`POST /api/projects/import-backup` 导入为新项目（单事务、id 全量重映射、自引用外键两轮写入、源项目永不覆盖、坏包 422）。api_key 彻底剔除（`model_configs` 不入白名单 + metadata 六项自证 + 测试递归扫描）；运行时/敏感表 10 张不导出。新模块 `packages/core/backup/`。
- 参照系消费可观测：`quality_gate` 捕获 `reference_consumption`（消费了哪些参照文件 + 字数），checkpoint 与 `quality_reports._meta` 双路径；QualityPanel 新增「本章消费参照系」区块。
- enforce 改稿引导：质量门禁阻断时生成结构化 `revision_guidance`（低分维度 + 阻断规则 → 可执行建议，16 条规则模板 + 9 条类目模板，纯规则零 LLM），写 checkpoint 与 `runs.error`；QualityPanel 新增「改稿引导」区块。
- summarizer 注册验证固化为正式集成测试（`tests/integration/test_summarizer_prompt_registration.py`，3 例）。

### Fixed

- 前端备份测试类型对齐（HealthResponse.tables Record 形态、快照 mock 注解修正）；ExportPanel 下载补 `credentials: 'same-origin'` 并去重组件内 URL 构造（改走 `endpoints.ts` 的 `exportApi.url`）。
- 备份导入自引用外键（`state_deltas.supersedes` / `branches.parent_branch_id`）第二轮改写空操作修复：原实现按 `WHERE col IS NOT NULL` 扫描库，但第一轮 INSERT 已统一置 NULL，永远查不到，导入后自引用列仍为 NULL；改为第一轮把 `(table, pk_col, new_pk_value, old_target)` 记入 `BackupService._self_ref_rewrites` 内存清单，第二轮按该清单 + 全局 `project_id_map` 直接 UPDATE 新行（详见 `packages/core/backup/README.md` §5.2）。

### Known Issues / 路线登记（新增）

- 备份快照内嵌 id 不重映射：`story_states.snapshot_json` 等 `*_json` 列内嵌实体 id 保持源项目命名空间（行级 MVP 口径，详见 `packages/core/backup/README.md`「已知边界」）；深度重映射留待需要时用稳定 id 前缀浅层替换。
- `reference_canons` / `canon_extracts` 暂不随备份导出（V1.5 升级路径见 backup README）。

## [1.3.0] - 2026-08-24

审稿与文风包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- LLM 评审员 critic：chapter-review 在人工审批前生成建议性结构化审稿报告（总评/亮点/问题清单，category+severity 枚举校验，quote 溯源软校验），经 pause payload 的 critic_report 键展示在审批卡；advisory only 不拦截；非 JSON/无 prompt/provider 异常全降级为 critic_status='failed'，审批照常。新增 docs/agents/prompts/critic-v1.md（agent 注册 7→8）。
- 个人文风样例库：迁移 0008 新增 author_style_samples 表；CRUD API /api/projects/{pid}/style-samples（每篇 ≤5000 字、每项目 ≤10 篇）；writer 上下文注入最近 ≤2 篇 × ≤1000 字并带模仿引导语（与 Q7 爆款目标风格互补：个人声音+目标风格双轨）；context-preview 与前端面板同步展示；项目总览页新增 StyleSamplesPanel。
- 伏笔 overdue 阈值项目级可配：projects.foreshadow_overdue_chapters（默认 30，迁移 0008 加列），builders 读取并多重回退 30。

### Fixed

- 开放伏笔清单截断边界：overdue 判定下推 SQL（ORDER BY overdue_flag DESC ... LIMIT 20），去掉预取 60+内存排序，伏笔超 60 条时逾期项不再被丢弃。
- summarize 节点真实降级路径测试补齐（不注册 agent → PromptNotFoundError → failed 且 chapter COMMITTED）。
- ContextPreviewPanel 空态文案口径修正；ContextPreviewItem 类型补 excerpt_len 字段。
- critic 字数默认值复用共享常量；项目更新端点消费 foreshadow_overdue_chapters。

### Known Issues / 路线登记（新增）

- critic 输入的伏笔摘要暂以 hook name 充当（hooks 表无 description 列，加列后回填）。
- PromptNotFoundError 等 provider 前异常不落 ai_call_logs（既有 runner 行为，运维可观测性缺口，V1.4+ 处理）。

## [1.2.0] - 2026-08-24

竞品对标迭代：基于海外（Sudowrite / NovelAI / NovelCrafter）、国内（蛙蛙 / 彩云小梦 / 平台 AI 政策）与开源社区（13 个代表项目）三路调研的优化落地。分析文档：`docs/analysis/competitive-analysis-2026-08-24.md`。

### Added

- **质量系统第七维 `ai_trace`（AI 痕迹）**：章内 shingle 重复率 + 跨章重复率（最近 3 章）+ AI 套话命中三子信号合成；`overall` 升级为七维平均；前端 `QualityPanel` 增加「AI 痕迹」标签。（应对番茄低质 AI 文治理：重复率 / 文风机械性检测）
- **上下文装配可见化**：`GET /chapters/{id}/context-preview` dry-run API（零 LLM 调用，返回分层 token 估算与纳入条目清单）+ 章节详情页「AI 上下文明细」面板（`ContextPreviewPanel`）。
- **AI 调用日志查看器**：`GET /api/ai-call-logs` 列表（分页 / 过滤）与详情 API + 前端 `/ai-logs` 页面；敏感字段黑名单三重防御（schema 无 key 字段 + 路由黑名单 + 前端类型不声明）。
- **章节摘要链 + 前章尾段**：chapter_commit 新增 `summarize` 节点（≤200 字摘要 + 末 300 字尾段，迁移 `0007_chapter_summaries` 表；摘要失败降级不阻断 commit）；L1 装配注入最近 5 章摘要与前一章尾段，超预算先砍最旧摘要。
- **伏笔状态机强化**：observer 写透路径增加 hooks 状态迁移校验（前进制结算、拒绝 ABANDONED 复活）；`overdue` 计算属性（planted 超 30 章未收）；L1 新增「开放伏笔清单」装配（overdue 优先）。
- **summarizer agent prompt 注册**（`summarizer-v1.md`，capability=reasoning）。

### Changed

- `context-preview` 与 `ContextPreviewPanel` 同步展示摘要链 / 尾段 / 开放伏笔三类新内容源。
- 伏笔 / 开放线索由纯实体升级为带迁移校验的状态机（双口径说明见 `story_state` README §7.5.3）。

### Known Issues / 路线登记（新增候选）

- **V1.3+**：LLM 评审员（建议性审稿报告，人工裁决）；docx 导出与番茄投稿链路；任务分模型路由；个人文风样例注入（对标 Sudowrite Style Examples）；伏笔 overdue 阈值项目级可配。
- **V2.x**：向量召回混合层（状态库定事实 + 向量供呼应）；设定条件触发动态注入（关键词 / 关系链 / 场景绑定，对标 Lorebook / Codex）。
- V1.3+ 遗留（审查登记）：开放伏笔清单 SQL 预取 LIMIT 60 与 overdue 排序键不一致的截断边界（伏笔超 60 条时逾期项可能进不了清单）；summarize 节点 PromptNotFoundError 真实降级路径暂无测试覆盖（现测试用 mock 绕过）；ContextPreviewPanel 空态文案边界细节。

## [1.1.0] - 2026-08-24

PRD 全量符合性核对（§1–§125 逐节追溯）+ 全维度检修（安全 / 数据完整性 / 测试盲区 /
文档一致性，叠加 V1.0 的性能 / 业务闭环 / 架构三轴）后的修复版本。
§110 MVP 验收链由 12/13 补齐为 **13/13**。

### Added

- **Q8 人工占比 CSV 导出**（PRD §125 合规自证）：`GET /api/projects/{pid}/quality/q8-export`
  按章导出 ai_chars/human_chars/human_ratio（utf-8-sig BOM，Excel 兼容）；数字口径与评估链路
  同源（`compute_char_stats`）。合规立场已调研定调并写入 `packages/core/quality/README.md` §12：
  《人工智能生成合成内容标识办法》（2025-09-01 施行）的显式/隐式标识义务主体是面向公众的
  生成服务提供者与传播平台（番茄已在投稿环节要求作者勾选是否使用 AI）；NovelOS 是本地单机
  工具，V1 不在正文内嵌标识，产品侧提供统计与导出自证，投稿声明义务在作者侧。
- smoke_e2e §110 验收链补齐第 3 步「创建世界」断言（9 组断言全绿）。

### Fixed

- **API key 读路径脱敏**：model_configs 的 GET/PATCH/POST 响应统一脱敏（`api_key` → `***` +
  `has_api_key` 布尔）；PATCH 传 `***` 保留原 key、空串清空；前端编辑弹窗不再回填明文，
  留空=不修改。DB 写路径保持明文存储不变。
- **零外呼回归锁定**：无 model_configs 时断言 `ModelNotConfiguredError` 且 ai_call_logs 零新增；
  deconstruct 端点未配置模型时返回 422（原漏成 500），并前置 capability 预检。
- **拆书文本上限**：deconstruct `text` 超过 5MB → 422。
- **草稿体长上限**：`DraftCreate.content` max_length=500_000。
- **references 路径防护**：`load_reference_texts` 对 project_id 做字符白名单校验（防路径穿越）。
- **文档修正**：`packages/core/api/README.md` 健康端点表数 28→31（三处）。

### Known Issues / 路线登记更新

- V1.x 清单移除「model-configs 列表 api_key 脱敏」（本版已完成）；其余 V1.x/V2.x 项维持
  V1.0.0 登记不变。新增登记：前端无「清空密钥」UI 入口（仅能覆盖/保留）；`***` 为保留哨兵值；
  PRD 符合性核对发现的中长期缺口（Intent Engine §41、Impact Analysis §42、Event System §68、
  reveal_policies 运行时 §23.2、Timeline 冲突检测 §20、Dashboard 健康度 §14、Workflow DAG
  可视化 §61、Style System §97、Export/备份 §100/§101 等）按 PRD 自标的 V1/V2 阶段推进，
  明细见检修结论（各节判定矩阵已在审查记录归档）。

## [1.0.0] - 2026-08-24

首个正式版本。交付形态：**本地部署 Web 应用**（FastAPI 托管 React SPA，SQLite 落盘，单端口 18081）。
S0–S12 全部收官（S12 Tauri 壳经用户拍板关闭，Web 版即交付形态）；真实 LLM（MiniMax-M3）
端到端验证通过。

### Added（S0–S11 累计能力）

- 章节生产四工作流：chapter-plan（director）→ chapter-write（writer）→ chapter-review
  （Human 三态审批：批准 / 驳回 / 驳回附改稿意见）→ chapter-commit（observer 提取 state delta
  → 校验 → 质量门禁 → 提交）。
- Canonical Story State：快照 + delta 状态机、乐观锁、逆 delta 回滚、分支（What-if）与
  按序重放 promote。
- 质量引擎：6 子分加权、8 条 guardrail、H-1~H-5 爽感体检；quality gate 支持
  report（默认）/ enforce 双模式。
- 伏笔与叙事债务台账（hooks / narrative_debts）及 REST 端点。
- 参照系拆书 deconstruct-book（T1 切分 → T2 逐章拆解 → T3 聚合校验 → G-sim 相似度阻断 → T4 落库），
  参照 canon 自动注入 director 上下文。
- What-if 推演（simulation）：临时分支零污染推演 + diff + 归档。
- 多模型路由：mock / OpenAI 兼容 / Anthropic / Ollama，失败转移链，健康检查。
- 前端工作台：项目列表 / 项目总览 / Story Bible（人物·世界·情节·台账·Canon 五 tab）/
  章节详情（计划·草稿·工作流·质量面板）/ AI 设置。
- 回归基线门禁（结构签名比对）与 HTTP 全链路 smoke。
- 真实 LLM 验证：MiniMax-M3 全链路 PASS（报告 `docs/evaluation/runs/real-llm-minimax-m3-2026-08-24.md`，
  质量分 overall 87）。

### Fixed（V1.0 检修：性能 / 业务闭环 / 架构三路审计）

- **[P0] 审批卡片 UI 断头**：后端把 `__pause_payload__` 写在 `checkpoint_json[节点名]` 下，
  前端误从顶层读取导致审批卡片（review / HIGH 风险审批）永不渲染。新增纯函数
  `extractPausePayload` 按真实结构取值（`apps/web/src/utils/pausePayload.ts`）。
- **observer 校验重试**：commit 流程对 observer 输出先经 `validate_delta` 纯函数预检，
  失败带 `_retry_hint` 重试一次（共 2 次），`submit_delta` 仅在通过的 delta 上调用一次
  （真实 MiniMax-M3 运行中该重试实际救回一次 commit）。
- **写透 NULL 兜底**：world add 分支（location/faction/rule）对缺失的 name/statement/data_json
  做 None 兜底，消除 `NOT NULL constraint failed`。
- **归档过滤**：项目列表默认排除 ARCHIVED（`?include_archived=true` 可含），行为与 UI 文案对齐。
- **模型测试连接字段对齐**：前端 `ModelConfigTestResult` 对齐后端 `{ok, latency_ms, detail, status_code}`。
- **轮询节流**：`usePoll` 在页面隐藏（document.hidden）时跳过 tick。
- **SQLite 韧性**：连接统一 `PRAGMA busy_timeout = 5000`。
- **e2e 脚本**：HTTP 客户端超时 120s→1200s（推理模型 observer 调用合法耗时可达 300s）、
  sqlite row_factory、临时库清理重试。
- **架构**：`_diff_snapshots` / `_strip_state_version` / `_shingles` 提为公共 API
  （`diff_snapshots` / `strip_state_version` / `compute_shingles`），消除跨模块私有引用与
  simulation 内的复制函数。

### Migration

- `0006_quality_reports_project_idx.sql`：`quality_reports(project_id)` 索引（list_reports 查询路径）。

### Known Issues / 路线登记（V1.x / V2.x 候选）

- **V1.x**：simulation/branches 仅 API 无 UI 入口；ai_call_logs 无查看通道（PRD §93）；
  enforce 阻断后的改稿引导；参照系消费可观测（`_reference_canon_consumed` 未进 checkpoint）；
  ~~model-configs 列表 api_key 脱敏~~（V1.1.0 已完成）；checkpoint_exclude 推广到章节工作流
  （需先论证 resume 影响）；
  workflow 注册表下沉 core；router 越层 SQL（reference/workflows/model_configs）抽 service。
- **V2.x**：story_state/service.py（2300+ 行）按职责拆分；分支读路径 O(N²) 重放的快照物化；
  story_state 写透与 domain CRUD 的双写面统一；context engine 装配缓存；端口变量收敛。
- 不计划做：packages/agents 空骨架强填充（角色逻辑当前内联于各 pipeline，已在 README 标注）。
