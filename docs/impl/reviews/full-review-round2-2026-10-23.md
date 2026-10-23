# NovelOS 全面审查 · 第二轮（2026-10-23）

> **定位**：今天已跑过一轮「三路全面审查（A 架构/契约/文档/量具、B 质量/题材、C 脚本/运维/数据）」，其结论与未修项见
> `remaining-debt-2026-10-23.md`（下文简称「台账」）。**本文件是第二轮**：审查面不同、方法不同，
> 且对第一轮的**声称本身**做了复算（声称 ≠ 实测）。
>
> **纪律遵守**：零真实 LLM 调用；零内容仓（NovelOS-Content / NovelOS-Artifacts）接触；未改真实 dev 库
> `data/novelos.db` 的业务内容（只读 schema / COUNT / 临时库实验）；未做任何 git 写操作（除本轮收尾的提交）；
> 临时产物一律落系统临时目录。
>
> **方法**：9 个只读子代理铺广度面（域层/前端/工作树/安全/备份/CI/测试判别力/缓存键/三仓边界），
> **critical 与 major 全部由 Lead 亲自读码 + 探针复算**后才入册；子代理给的数字一律自己复算，
> 与子代理不一致处**两边都写**（见 §4.3）。

---

## 0. 一句话结论

**代码本身没有明显崩坏，但「声称与实测」这条纪律正在大面积失守**：本轮实测出 **1 条 critical（质量门 fail-open，已端到端复现）**、
**16 条 major**（其中 5 条是「备份能写但不能安全恢复」「CI 并发路径从未真正跑过」「密钥仍在库里明文」
「装配缓存键第 6/7 次漏维度」「外发投稿包取错稿」「操作痕迹已上 GitHub」「内容仓仍挂 GitHub remote」），
以及**台账 14 条里有 5 条的「现状证据」已经过期或本身写错**——其中 C-7 那条**在写下的当天就是错的**。
本仓的测试与突变体系覆盖面很广，但**广度不等于闭合**：
本轮 3 条 major 的共同形状都是「**有一处实现了，但没有任何测试会在它坏掉时变红**」。

⚠️ **最需要立刻处理的不是代码，是两件隐私事实**：① 内容仓**仍挂着 GitHub remote 且本地领先 17 个提交**
⇒ 一次误 push 即发布全书；② 软件仓 `docs/evaluation/` 里的**真实评审痕迹已经随 `origin/master` 上 GitHub**
（含真实 chapter_id / project_id 与逐章评分）⇒ **已不可撤回**。

---

## 1. 本轮范围与口径

### 1.1 做了什么（9 个面）

| # | 面 | 性质 | 状态 |
|---|---|---|---|
| 1 | 批次三十工作树（未提交）对抗审查 | 声称复核 | ✅ 7 条声称：**5 成立 / 2 部分成立** |
| 2 | `packages/domain`（6196 行）+ 33 个迁移（今天第一轮明确「未摸」） | 深审 | ✅ |
| 3 | 前端薄弱面（台账 task-27 指向处） | 深审 | ✅（见 §4.3 附） |
| 4 | 安全与隐私（凭据 / 监听 / 路径 / SQL / 脱敏 / gitignore） | **新增角度** | ✅ |
| 5 | 数据完整性（备份恢复往返 / 迁移漂移 / 状态同步） | **新增角度** | ✅ 真跑通真实 openssl |
| 6 | CI / 依赖供应链 / 发版纪律 | **新增角度** | ✅ |
| 7 | 测试体系判别力（元审查：恒真 / 自指 / mock / 未覆盖） | **新增角度** | ✅ |
| 8 | 缓存键维度 + 端点参数「收下即忘」 | **新增角度** | ✅ **实测抓到 5 处漏维度** |
| 9 | 三仓分离边界 + 零突变覆盖高风险面 | **新增角度** | ✅ |

### 1.2 不做什么（+ 理由 + 归属）

| 不做 | 理由 | 归属 |
|---|---|---|
| 真实 LLM 调用 / 真实 workflow run | AGENTS.md §一 硬约束 2（改软件时禁真实调用，烧额度） | 已被 AGENTS.md 覆盖 |
| 内容仓接触（读正文/人物/设定）、既有章节改写、数据迁移清洗 | §一 硬约束 1（既有 17 条 PAUSED run 与 48 章书均为测试书籍） | 已被 AGENTS.md 覆盖 |
| `git push` | AGENTS.md §二：推送一律先问用户（此前已自报过违规 push 6 个提交） | **需用户批准** |
| 本轮不擅自改业务代码 | 审查与修复分批次（本仓既有惯例：审查 → 用户「按建议顺序做修复」→ 另立批次） | 见 §7 |
| 全量突变套件实跑 | 会临时改源码，且需与任何读/写源码的操作串行 | 本轮只跑只读预检口径 |

### 1.3 完成定义（DoD）与停止规则

- **DoD**：① 每条发现带「文件:行号 + 复算命令 + 实测输出」；② 每条发现标「事实 / 推测」；
  ③ critical/major 由 Lead 亲验；④ 台账逐条核销并更新；⑤ 报告落盘 + 提交留痕。
- **停止规则**：每个面审到「≥3 条可复算发现」或「该面穷尽（文件数 < 30）」即止；**不追覆盖率数字**，
  不做仪式全量（方法论 02 反模式）。

---

## 2. 缺陷总表（按严重级）

| 严重级 | # | 一句话 | 复算方式 |
|---|---|---|---|
| **critical** | C-1 | 质量门 fail-open：`drafts` 表读不出来 ⇒ 该章被判「满分 0 issue」并放行 commit | 见 §3.1 |
| major | M-1 | director 装配缓存键 **5 处漏维度**（伏笔台账/叙事债务/情节图/逾期阈值/章标题），第 6/7 次同形状 | §3.2 |
| major | M-2 | 备份**有写无读**：无 restore 工具、无 runbook；带 `-wal` 残留时恢复**静默失效**且 `integrity_check` 假绿 | §3.3 |
| major | M-3 | 解密失败**仍会破坏活库**：openssl 先截断目标再报错，无原子写 | §3.3 |
| major | M-4 | `backup_db.py` **默认明文**备份；加密后**从不验证密文可解** ⇒ 备份即丢 | §3.4 |
| major | M-5 | 密钥文件缺失时**静默生成新密钥**（exit 0，无告警）⇒ 旧备份永久不可解 | §3.4 |
| major | M-6 | CI 装不上 `pytest-xdist` ⇒ windows 并发路径 job **从未真正跑过**；CI 历史 47 次 **34 红** | §3.5 |
| major | M-7 | 活库仍有 **2 行明文 api_key**，而 `secrets.json` 唯一条目**已悬空** ⇒ 「密钥进文件不进库」的设计**没生效** | §3.6 |
| major | M-8 | 批次三十的招牌产物「`--check` CLI 闸门」的**失败退出码全仓无看守** | §3.7 |
| major | M-9 | commit 风控门 `_has_high_risk_change` 的两条子句**零可区分夹具** + 该文件无突变规格 | §3.8 |
| major | M-10 | 删角色留孤儿 `relationships` 行（0022 移除 FK ⇒ `foreign_key_check` 看不见） | §3.9 |
| major | M-11 | 核心机制层 **5 个文件 import domain**，**零机器看守**，且文档两处自相矛盾 | §3.9 |
| major | M-12 | 版本一致性机器看守只覆盖 **6 处中的 3 处**（web 三处零看守） | §3.5 |
| major | M-13 | 外发投稿包取「**最新 draft**」而非「**已 commit 的那版**」，而 `commits` 表**没有 `draft_version` 列** ⇒ reopen 后外发带未过审稿 | §3.10 |
| major | M-14 | 操作痕迹落在**软件仓 `docs/evaluation/`** 且**已随 `origin/master` 上 GitHub** ⇒ 违反硬规则 5，**不可撤回** | §3.10 |
| major | M-15 | 内容仓**仍挂着 GitHub remote** 且本地领先 **17 个提交** ⇒「内容仓不推 GitHub」**无技术守卫** | §3.10 |
| major | M-16 | `content_sync` 三条静默形态：改标题后**旧正文永久残留** / 缺表**整次 push 中断** / 原地修过的快照**永不回灌** | §3.11 |
| minor | M-17 | `signing_check` 读题材包失败 ⇒ **题材开篇整段静默消失**、无告警，报告外观「全通过」 | §3.11 |
| minor | M-18 | `*.db.enc` **不被任何 gitignore 规则覆盖** ⇒ 加密备份可被误提交 | §3.10 |
| minor | M-19 | 硬规则 5「审核红线只进内容仓」字面口径**比实现绝对**（DB 承载「机制可呈现子集」，schema 明写如此）⇒ 应改**文档措辞**而非改实现 | §3.11 |
| major | M-20 | 快通道在**章节页是第二个写入口**，且与项目弹窗「提交值≠当前值才下发」**语义相反**；`auto_commit` 在全前端测试中 **0 命中** | §3.12 |
| major | M-21 | `useChapterRunOrchestration`（run 轮询/暂停/恢复状态机 hook）**零专属测试**，`checkpoint_json.quality_gate` 提取路径 0 命中 | §3.12 |

---

## 3. 逐条证据

### 3.1 C-1（critical）质量门 fail-open —— Lead 已端到端复现

**失效链**（全部 Lead 亲验）：

```
packages/domain/chapter/draft_resolver.py:88-91   except Exception → return None   （打不开库）
packages/domain/chapter/draft_resolver.py:108-109 except sqlite3.Error → return None （缺表/查询异常）
        ↓  ⚠️ 该函数 docstring:83 明确写「读库失败 ⇒ None」——DB 故障与「该章无草稿」被**刻意混为一谈**
packages/core/quality/service.py:811              draft = (resolved or {}).get("content") or ""
packages/core/quality/scoring.py:439-441          if not draft: return 100, issues   ← 满分 + 0 issue
packages/workflows/chapter_commit/gate.py:473     quality_ctx = build_quality_context(...)  ← 门禁消费点
```

**Lead 端到端复现**（`C:/Users/aistation/AppData/Local/Temp/lead_probe/probe_failopen.py`，临时库、合成行、零 LLM）：

```
tables after migrations: 43
--- 正常态：resolve_draft ---
resolve_draft -> version=1 content_len=22
--- 制造「drafts 表不可用」---
--- 故障态：resolve_draft 是否抛错？ ---
resolve_draft ->  None （None = 静默吞掉 DB 故障，未抛异常）
--- 故障态经 quality/service.py:811 的等价式 → scoring ---
draft 长度 = 0 （真实正文长度应为 22）
score_style -> score=100 issues=0  ← 空文本 ⇒ 满分 0 issue
```

**触发条件（精确版，比「DB 故障」窄）**：不是「DB 整体不可用」——那样前面的
`compute_char_stats_detail` 会先抛错。真正的触发面是 **`drafts` 表不可用**：
① 部分迁移（`drafts` 未建）；② 从**缺该表的备份**恢复；③ 表损坏。此时其余读正常，
于是**该章从未被评估，却拿到满分 0 issue**，门禁放行。

**为什么至今没被发现**：这条路径的每个单独断言都「正确」（空文本不扣分），
只有「读不到」与「没有」被合并才致命。**这类缺陷用正向测试抓不到，必须用「注入故障」测**。

**判别建议**：临时库 `DROP TABLE drafts` ⇒ 调 `POST /api/quality/evaluate` ⇒ 期望 5xx/显式错误，
**不是** `score=100` 的报告；且新用例必须**突变验证**（把 `return None` 改回吞掉必红）。

---

### 3.2 M-1（major）缓存键漏维度 —— 第 6/7 次同形状

**Lead 独立探针**（`probe_cachekey.py`，临时库、合成行、零 LLM；断言打在 **payload 实际内容**上，
并带「对照组必须 miss」以证明装置有效）：

```
[A1 伏笔台账变化]   重装=False payload变化=False before=0      after=0      => 脏命中(payload 未更新)
[A2 director 侧章标题] 重装=False payload变化=False before='旧标题' after='旧标题' => 脏命中(payload 未更新)
[C1 对照组: 项目前提]  重装=True  payload变化=True  before='旧前提' after='新前提' => 已正确 miss
```

⇒ A1：装配后新增 1 个伏笔 + 核销 1 个伏笔，**director payload 里的伏笔台账仍是空**（新增的伏笔根本不在里面）；
A2：作者改了章标题，planner 仍被告知「旧标题」；
C1 证明装置不是假阳性。

**子代理给出、Lead 未逐条复现但同族成立的另外 3 处**：`narrative_debts`、`plot_events`+`branches`、
`projects.foreshadow_overdue_chapters`（阈值 30→1 后 `overdue` 仍为 false）。

**共因（比「漏了一个维度」更该修的两点）**：

1. **写路径不推 `state_version`**：`domain/ledger/service.py`（hooks 增删改核销）、
   `domain/plot/service.py`、`domain/project/service.py`（PATCH 逾期阈值）、
   `domain/chapter/service.py`（PATCH 章标题）**一律只 `commit()`，不推 `story_states.state_version`**。
   ⇒ **即使将来把维度补进键，只要 state_version 不变、其它维度不变，脏命中依然成立**。
   本仓过去 5 次修复补的只是**键维度**，没补**写路径推 version**——这才是「同形状复发」的真因。
2. **同一张表、同一列，writer 补了 director 没补**：`chapters.title` 在 writer 侧有 `title_fp`
   （`writer_input.py:555/569`），director 侧 payload 里有 `chapter.title`（`director_input.py:281`）
   却**无对应维度**。这不是「漏想」，是**两套键各修各的**。

**可达性（诚实限定）**：比已修的 `chars_fp` 更窄——`plan_json` 在键里，而 `save_plan`
（`chapter_plan/pipeline.py:231`）每次成功计划都无条件覆写 `plan_json` ⇒ 命中要求两次装配之间
`plan_json` **一字不差**（失败重试 / 计划输出未被接受）。**但成立不靠运气**：伏笔台账恰恰是
「计划失败→作者改伏笔→重试」这条最常见路径上的数据。

**判别建议**：新增 `tests/unit/test_ctx_c_ledger_key.py`（用例名如
`test_updating_hook_misses_director_cache_and_payload_shows_new_hook`），
断言打在 payload 内容上 + 配「未改必命中」反向不变量；配突变规格撤 `ledger_fp` 维度必红。
**并且**：把写路径推 `state_version` 一并做，否则补维度只是治标。

---

### 3.3 M-2 / M-3（major）备份能写不能安全恢复

**实测跑通了真实 openssl 往返**（子代理执行，Lead 复核结论）：49 表逐表 sha256 **0 处不一致**，
`integrity_check=ok` 两侧，`foreign_key_check` 0 违例，CJK/emoji/`\x00\x01\xff` 二进制逐字节一致。
⇒ **台账 C-7「真实 openssl 往返从未实跑」是错的**（详见 §4.2）。

但**恢复侧是空的**，且实测出两种「假绿」形态：

| 形态 | 实测结果 | 为什么危险 |
|---|---|---|
| 恢复时目标库旁**残留 `-wal` / `-shm`** | 49 表 → **1 表**，`OLD_LIVE_TABLE` 混入、`projects` 表不存在，而 **`integrity_check=ok`** | SQLite 用旧 WAL 覆盖新主库 ⇒ **恢复静默失效**，而且校验还报绿 |
| 密文**截断/解密失败** | openssl exit 1，但目标文件**已被截断重写**（8192 → 356336 B，`database disk image is malformed`） | 中断恢复 = 活库变 malformed；无 temp+rename 原子写 |

**根因**：仓内**只有导出、没有恢复工具与 runbook**（无 restore 脚本、README 无恢复章节、
无恢复后校验脚本）。「机器坏了」时只能手工拼 openssl 命令，路径/顺序/校验全靠记忆。

**判别建议**：先落一个 `scripts/restore_db.py`（temp 文件 + rename 原子替换 + 恢复前删 `-wal`/`-shm`
+ 恢复后 `integrity_check` + 表数核对 + **故意失败不碰原库**），再谈其它。

---

### 3.4 M-4 / M-5（major）备份默认明文 + 密钥静默轮换

**Lead 亲验**（`scripts/backup_db.py`）：

```
217:        "--encrypt",
218:        action="store_true",          ← 默认关
219:        help="用 openssl 加密后再落盘（**推荐**：备份可恢复，且明文密钥不出本机）",
...
288:            encrypt_file(plain, enc_dest, key_file)
289:            plain.unlink(missing_ok=True)          ← 删掉唯一明文
291:            print("[校验] integrity_check=ok（在加密前的明文副本上做的）")
293:        else:
294:            shutil.move(str(plain), str(dest))     ← 默认落明文
```

⇒ **默认路径把明文密钥 + 全部正文写进同步盘**；help 说「推荐」的是那个 opt-in 的 flag。
⇒ 且**加密后从不验证密文可解**（integrity_check 是在**加密前**的明文副本上做的）⇒ 可能产出不可解的 `.enc` 而无人知晓。
⇒ 且 `ensure_key_file` 在密钥文件缺失时**静默生成新密钥**（实测 key 前 8 位 `ab1d0551`→`c53b90f5`，
用新 key 解旧密文 exit 1 `bad decrypt`）⇒ 旧备份**永久不可恢复**。

**与既有留痕的冲突**：AGENTS.md 批次十六记「备份落网盘同步盘收尾 —— 活库已备份 + **密钥进私有网盘**」。
按当前默认路径跑一次常规备份，就与该策略相反。

**判别建议**：`--encrypt` 改默认开、`--plaintext` 显式 opt-in；加密后加解密自检（临时目录往返）；
密钥缺失时**报错退出**而非静默生成。

---

### 3.5 M-6 / M-12（major）CI 与发版纪律

**M-6 Lead 亲验**（`pyproject.toml` + `.github/workflows/ci.yml` + 已装 metadata）：

```
pyproject.toml:36-40   [project.optional-dependencies] dev = ["pytest>=8.0", "ruff>=0.4"]
pyproject.toml:66-70   [dependency-groups]           dev = ["pytest-cov>=7.1.0", "pytest-xdist>=3.8.0"]
已装 METADATA Requires-Dist: pytest>=8.0; extra == "dev" / ruff>=0.4; extra == "dev"   ← 无 xdist
ci.yml:116-117         python -m pip install --upgrade pip ; pip install -e ".[dev]"
ci.yml:122             python -m pytest tests/unit/quality tests/unit/test_no_core_to_workflows_dep.py -q -n 4
```

⇒ `pip install -e ".[dev]"` **装不上 xdist**（它在 `[dependency-groups]`，不进 wheel metadata）
⇒ `-n 4` 那步必然不认 `-n` ⇒ **windows 并发路径（项目三次真实事故所在面）在 CI 上至今零覆盖**。
子代理另查：最近一次 run（引入该 job 的那次提交）**恰在此步红**，前 5 步全绿；CI 历史 **47 次 34 红**。

**M-12**：`check_skill_package.py` 的版本一致性检查只覆盖 **pyproject / claude-plugin/plugin.json / SKILL.md**
三处；**web 三处（package.json / package-lock.json / uv.lock）全仓 0 命中** ⇒ 零看守。
这正是批次十九「SKILL.md 静默停在 3.9.0」那类漂移在 web 侧的复发入口。

**其它（minor 级）**：CI 不消费 `uv.lock`、dev 依赖浮动、CI 时段两次未钉版 pip 安装、
`pip-audit ... || true` 永不阻断、action 浮动到 `@v4`/`@v5` 未钉 SHA、无 `timeout-minutes`/`concurrency`/`permissions:`。

---

### 3.6 M-7（major）凭据仍在库里明文

- 实测：活库 `model_profiles` 有 **2 行携带非掩码真值 api_key**；`model_connections` **0 行**。
- `secrets.json`（仓库根，已被 gitignore）只有 **1 条** `mprof_f95e3c7b4127`，与两个现存档案 id **均不匹配** ⇒
  解析链 0~3 级全落空 ⇒ 实际命中第 4 级 `params_json["api_key"]`（**DB 明文**）。
  ⇒ **V3.8「密钥收敛到文件」的设计在本机实际未生效**。
- 干净面（已查）：无任何 log 打印 api_key；`ai_call_logs` 不存完整 payload；API 响应侧 api_key 一律掩码；
  **git 全历史从未提交过** `.db`/`.env`/`secrets*.json`/`backup.key`；同步盘上现有备份**全部为 `.enc`**。
- 唯一新缺口：`NOVELOS_DEBUG_PROVIDER_DUMP_DIR` 会把 **LLM 响应全文（=章节正文）**落盘，
  而该目录**不在 gitignore 覆盖内**（除非落在 `data/` 或 `tmp/` 下）⇒ `git add -A` 可能把正文提交进软件仓。
- 监听面：默认 `127.0.0.1:18081`（**局域网可访问默认不成立**），但 `NOVELOS_API_HOST` 可改 `0.0.0.0`
  且**全仓无鉴权中间件**（README 自述接受）；CORS 是本机白名单（非 `*`）。

---

### 3.7 M-8（major）批次三十的 CLI 闸门无看守

**子代理实证**：把测试里的 `export_main`/`gen_types_main` 换成**恒返回 0** 的替身 ⇒
**8 条用例全绿（无一抓到）**。全仓 `grep -rn '"--check"' tests/` 只命中两个辅助函数，
二者只对**已同步的仓内产物**调用 ⇒ 只断言 `== 0`，**从不断言 `!= 0`**。

⇒ CHANGELOG 批次三十「配套 8 例（漂移必被抓 / 缺失算漂移）」在**闸门（CLI）层面**是假的：
漂移在 helper 层被抓住，但 **CLI 的失败信号从未被验证**。而 CLI 正是 CI/pre-commit 要消费的那一层。

**同批其它 minor**：① `l21` 规格「必红用例」表**错一行**（`CG-ALWAYS-OK` 实际不红 `test_missing_artifact_is_drift`，
因为 MISSING 早返回在突变行之上）；② `--check` 每次调用**泄漏一个临时目录**（实测已积 208 个），
故「**绝不写盘**」字面为假（准确表述是「绝不写**产物**盘」）；③ 默认路径 CWD 相对 ⇒ 非仓库目录执行即**假漂移**
（`export` → MISSING exit 1 / `gen` → exit 2），而 CHANGELOG 把该 CLI 定位为「可复用于 CI」；
④ 派生口径比叙述窄：只扫 `packages/` 且只认 `from packages.core.db import get_connection` 一种形式。

---

### 3.8 M-9（major）风控门两条子句零可区分

`packages/workflows/chapter_commit/pipeline_common.py:518-535` 的 `_has_high_risk_change` 判定
`facet == "definition"`（角色定义变更）与 `world_kind == "rule"`（世界规则变更）⇒ 要作者批准。

**Lead 亲验**：全仓测试里带 `facet` 的夹具**全是 `"state"`**；`facet: "definition"` 只出现在
**API 侧孪生实现**（`core/story_state/deltas.py:122`，由 `tests/integration/test_story_state_rollback_fixes.py:591`
与 `tests/api/test_simulation.py:298` 钉住）。**workflow 侧（commit 审批路径）没有任何夹具用
`definition` 或 `world_kind="rule"`** ⇒ 删掉这两条子句，**没有任何测试会红**；
且该文件**无突变规格**。另有 `tests/workflow/test_chapter_commit_summary_parallel.py:535` 把
observer 与 inject_validate **双双 stub** 并直接喂 `needs_high_risk_approval=True` ⇒ 判定函数在这些用例里零执行。

**Lead 补做的量化**（插桩 pytest 插件，记录 `_has_high_risk_change` 的真实入参）：

> ⚠️ 插桩踩了一次坑并留痕：`commit.py` 是 `from .pipeline_common import _has_high_risk_change`
> ⇒ **导入时把名字绑到自己模块里** ⇒ 只补 `pipeline_common` 的模块属性**拦不到调用**（第一次插桩记录 0 条，就是这个原因）。
> 修正为**同时补调用方模块里的那个名字**后，`tests/workflow` + `tests/api` 全量跑完，统计如下：

```
=== _has_high_risk_change 被调用次数: 41
character(facet, risk_level) 分布: {('state', 'LOW'): 13, ('state', 'HIGH'): 7}
world(world_kind, risk_level) 分布: {('location', 'LOW'): 3, ('location', 'HIGH'): 1}
判定结果分布(True=要人工批准): {False: 33, True: 8}
含 facet=='definition' 的调用次数: 0
含 world_kind=='rule' 的调用次数: 0
```

⇒ **量化实证**：这两条子句在 workflow 侧**从未被任何测试以可触发的入参驱动过**；
删掉它们，**41 次调用的判定结果一次都不会变** ⇒ **没有任何测试会红**。
（这比 grep 更硬：grep 只能证明「没有夹具用这两个值」，插桩证明了「运行期从未出现过这两个值」。）

---

### 3.9 M-10 / M-11（major）域层引用完整性与分层边界

**M-10 删角色留孤儿**：迁移 0022 移除了 `relationships` 的 `from/to_character_id` 外键，
而 `domain/character/service.py:308-321` 删角色只级联删 `character_states`、**不查引用**
⇒ 实测：删角色后 `relationships` 留 **1 行孤儿**，而 **`PRAGMA foreign_key_check` 报不出来（空结果）**。
路由可达（`DELETE /characters/{id}`）。对比：`world/service.py:544` 有同款引用检查 ⇒ 处置不一致。

**M-11 分层边界无机器约束**：Lead 亲验 `core → domain` 共 **8 条 import 语句 / 5 个文件**
（`arc/service.py:52`、`delivery/evidence.py:42-44`、`exporter/builder.py:41`、`quality/service.py:47`、
`signing_check/service.py:35-36`），**全部在 API 路由层之外**；
而分层看守 `tests/unit/test_no_core_to_workflows_dep.py:87-91` 的规则集只有三条：
`core↛workflows` / `domain↛workflows` / `packages↛apps` —— **没有 `core↛domain`**。

**口径必须精确（不要写成「破口」）**：AGENTS.md:42 的成文规则只写「core 不得 import workflows」，
**并未**写「core 不得 import domain」；Lead 实测**无 2 环、无 3 环**（模块级图）⇒ 当前无运行时破口。
真正的两个问题是：① **边界没定义清楚**——AGENTS.md §二「只有 workflows 能 import 上层」与
`packages/domain/README.md` 自述「下游：`packages/core/api` 路由」**自相矛盾**；
② **零机器约束** ⇒ 同类耦合会静默增加。

---

### 3.10 M-13 / M-14 / M-15 / M-18（major）三仓边界的三处裂缝（Lead 亲验）

**M-13 外发面取错稿**：`packages/core/exporter/builder.py:122-123` 用
`ORDER BY version DESC LIMIT 1` 取**最新 draft**；而 `commits` 表**没有 `draft_version` 列**
（Lead 实测：全仓 `draft_version` 只出现在迁移 0024 的 `quality_reports`，**不在 `commits`**）
⇒ 章 commit 后若被 reopen（`COMMITTED→REVIEWED`，`domain/chapter/service.py:596`）再改稿，
**外发投稿包可能带未过审的新稿**。这是**外发面**，错的内容离开本机不可撤回。

**M-14 操作痕迹在软件仓且已上 GitHub（不可撤回）**：
`git ls-files docs/evaluation/` ⇒ 11 个跟踪文件；`git ls-tree -r --name-only origin/master -- docs/evaluation/`
⇒ `docs/evaluation/m2-judge/judge_results.json`（真实 `chapter_id` + 逐章 LLM 评审 verdict）、
`docs/evaluation/m1-data.md`（真实 `prj_262251b433a9` + 逐章 token/评分）**已经在远端**。
且 `git check-ignore -v docs/evaluation/m2-judge/judge_results.json` ⇒ **rc=1 未被忽略**。
⇒ AGENTS.md 硬规则 5「操作痕迹在 NovelOS-Artifacts」**不成立**：痕迹实际在 DB + **软件仓内被跟踪的** `docs/evaluation/`，
而规定的痕迹仓 `D:/zcodeproject/NovelOS-Artifacts` **全仓无任何代码写入**（grep 0 命中）。

**M-15 内容仓仍挂 GitHub remote**：`git -C D:/zcodeproject/NovelOS-Content remote -v`
⇒ `origin https://github.com/charlesnty612/NovelOS-Content.git (fetch/push)`，且
`rev-list --count @{u}..HEAD` = **17**（本地领先 17 个提交）。
⇒ 硬规则 5「内容仓不推 GitHub」**只有纪律、没有守卫**（无 pre-push hook、无测试）。
⚠️ **远端配置已存在且本地领先 ⇒ 一次误 push 即发布全书**。这是本轮**最需要立刻处理**的隐私风险。

**M-18 `*.db.enc` 未被忽略**：`git check-ignore -v backups/x.db.enc` ⇒ **rc=1（未被忽略）**
⇒ `.gitignore:15` 只有 `*.db`，`.enc` 后缀不匹配 ⇒ 若有人把 `backup_db.py --out` 指到仓内目录，
**加密备份可被误提交**（含全部正文 + 密钥相关的库内容）。
（另注：`git check-ignore -v backups/` 会回 `.gitignore:36` —— 那是**空行**上的伪匹配，
**仓内无 `backups/` 规则**；`check-ignore` 对已存在的 `docs/` 返回 rc=1，证明该伪匹配不可当规则依据。）

---

### 3.11 M-16 / M-17 / M-19 三仓与零覆盖面（子代理实测，Lead 未逐条复现）

**M-16 `content_sync` 三条静默形态**（2069 行，**零突变规格**）：

| 形态 | 实测 | 为什么危险 |
|---|---|---|
| 改标题后**旧正文永久残留** | `第1章-OLD_TITLE.txt` 与 `第1章-NEW_TITLE.txt` **并存** | 内容仓是**人编辑面**，同章两份正文，作者无从判断哪份权威；grep `unlink` 只命中 tmp 清理 ⇒ **无清理逻辑，不会自愈** |
| 缺 `volumes`/`characters` 表 ⇒ **整次 push 中断** | 未捕获 `OperationalError` | 内容仓半成品、无 manifest |
| `story_state/<v>.json` **按 v 存在即跳过**（`service.py:400-404`）⇒ 原地修复过的快照（`story_state/snapshot.py:748` 同 v UPDATE）**永不回灌内容仓** | 实测 `REPAIRED` 未进仓 | 运行面修好了，内容面还是旧的 |

**M-17 `signing_check` 静默降级**：`service.py:133-134` `_load_project_opening_rules` 读题材包失败
⇒ `except Exception: return None` ⇒ **题材开篇整段消失、无告警、无 errors 字段** ⇒
体检报告外观「全部通过」而实际未检（同文件 `word_band_rejected` 有显式标记，此处没有 ⇒ 处置不一致）。

**M-19 硬规则 5 字面口径比实现绝对**：`genre_packs.payload_json` 含 `forbidden_words`(4 项) /
`pacing.redlines`(3 项) / `critic_rubric.taboo_notes`(88 字) ⇒ 按「审核红线只进内容仓」的**字面**读是违规。
但 `docs/state-model/schemas/genre-pack.schema.json` 明写「只承载机制可呈现的要点，题材正文/平台审核清单留在内容仓」，
`0025_genre_packs.sql:12` 同口径 ⇒ **这是设计意图，违规的是 AGENTS.md 那句太绝对的话**。
⇒ 建议改**文档措辞**（「审核红线**正文**只进内容仓，软件层只承载机制可消费子集」），**不要改实现**。

**口径交叉核对（两个子代理与 Lead 的一致性）**：
「零突变覆盖目录」清单以 Lead 的严格口径（解析 `MUTATIONS` 元组）为准：
`content_sync`(6) / `delivery`(5) / `exporter`(3) / `signing_check`(3) / `simulation`(3) /
`domain/knowledge`(3) / `domain/project`(3) / `workflows/deconstruct_book`(2) / `workflows/project_init`(2)。
**`arc` 不在其中**——`arc/service.py` 已有 3 条突变规格（E1-ARC1/2/4），真正零覆盖的是
`arc/models.py`（9 类）与 `simulation/models.py`（7 类）。子代理独立复算后与 Lead 清单一致。

---

### 3.12 M-20 / M-21（major）前端：快通道双写入口 + 状态机 hook 零覆盖

**M-20 快通道双写入口语义相反**（子代理静态核查 + Lead 采信）：

| 入口 | 行为 | 用例 |
|---|---|---|
| 项目弹窗 | 「**提交值 ≠ 当前值才下发**」⇒ 取消勾选会发 `false` | **4 条用例**守住 |
| **章节页** `ChapterDetailPage.tsx:249-262` | **无条件下发** `projectsApi.update(projectId, { fast_mode: v })` | **0 条**（`wf-fast-mode` 在前端测试中 0 命中；`auto_commit` 全前端 0 命中） |

⇒ 这正是 AGENTS.md 坑区记的「只勾选时下发 true ⇒ 取消勾选变静默空操作」的**同族形状**，
只是这次发生在**第二个入口**上——同一个开关、两套下发规则、只测了一套。

**M-21 run 编排状态机零覆盖**：`apps/web/src/hooks/useChapterRunOrchestration.ts`（148 行）
管 2s 轮询 / `stopWhen` / PAUSED 时 `detail.reload()` 重拉暂停载荷 / `qualityGateCheckpoint` 提取
⇒ **无专属测试文件**；`checkpoint_json.quality_gate` 提取路径在全前端测试中 **0 命中**。
⇒ 审批卡能否渲染、门禁报告能否显示，全靠父页集成测试的间接覆盖。

**台账 task-27 原始盘点的优先级被推翻**：原盘点主张「先补路由表 + NotFound（零风险）」，
但路由表/NotFound 是**零风险静态页**；`chapter/` 6 子组件里 4 个**已被父页间接覆盖**
（`plan-panel` / `chapter-danger-zone` / `drafts-panel` / `workflow-running-banner` 各有 refs），
**真正零覆盖的是 `ChapterPipelineHeader`（426 行，含快通道/深度二审/auto_commit 三个开关）**
与 `ChapterRunPanel`（426 行），以及那个编排 hook。
⇒ **task-27 的建议顺序应改**：先补「快通道双入口」（#1/#2）→ 再把编排 hook 从整页集成里剥出来独立测（#3）。

**其它（minor 级）**：`usePoll` 遇一次错误即 `stopOnError` 永久停摆、**无重试/恢复路径**；
前端版本号副本**无机器看守**（第二次独立指向 M-12）；CI `web` job 只跑 build+test、**不跑覆盖率、不跑 lint**，
前端**无 eslint 配置**；`api/types.ts` 与 `types.generated.ts` 双源（35 个名字重合，有债务上限看守）；
章节页 `fast_mode` 初值在项目值为 `null` 时**永远停在 false**（与后端 env 回落语义不一致）；
`projectInitCore.ts` 的 abort 监听器**只加不删**（长轮询可累积数百个）。

---

## 4. 声称复核表

### 4.1 CHANGELOG 批次三十的 7 条声称（子代理独立检验 + Lead 复核结论）

| # | 声称 | 结论 | 备注 |
|---|---|---|---|
| 1 | `--check` 一致退 0 / 漂移或缺失非 0 / **绝不写盘** | **部分成立** | 行为对（实测 0/1/1/2，产物 bytes+mtime 未变）；「绝不写盘」**字面为假**（每次泄漏一个临时目录，实测已积 208 个）；漂移路径的无写性无测试 |
| 2 | 换行归一真承重（`read_bytes().decode`） | **成立** | 内存注入后恰好 4 条红，与 CHANGELOG 逐字相符；⚠️ 该「4 条」是 **Windows 平台特异**（Linux 检出只红 1 条） |
| 3 | 字段级漂移必被抓 / 旧看守只做集合级 | **成立** | 独立复现：改 `AdoptRequest.content` 类型 ⇒ 旧三条集合断言全过，新闸门报 DRIFT |
| 4 | `_COUNTER_MODULES` 改 AST 派生 + 含 `agent_runtime.prompts` + 3 空转项确无绑定 | **成立** | 独立 AST 派生得 62 模块；3 空转项 grep 0 命中；排除规则成立 |
| 5 | 断言改 `2/11`、`2/15` + 夹具依赖写在测试里 | **成立** | Lead/子代理均用 `sqlite3.connect` 打点独立复现 2 连接真值；依赖说明确在测试文件 `:263-265` |
| 6 | 新增 8 例逐例有判别力 | **部分成立** | 8 例属实且全绿；③④⑤⑦ 只打 helper 层；**无一条断言 CLI 非零退出码** ⇒ M-8 |
| 7 | `l21` 3 条突变锚点唯一、换法真改行为 | **部分成立** | 锚点各命中 1 次、替换真改行为；**规格「必红用例」表错一行** |

### 4.2 台账 14 条「现状证据」逐条复算（Lead 亲算）

| 台账 # | 声称 | Lead 实测 | 判定 |
|---|---|---|---|
| A-1 | 25 个包目录零突变规格；227 个 .py 中 **72** 个有自身规格 | 严格口径（解析 MUTATIONS 元组）：规格文件 **76**、突变 **436** 条、覆盖 **123** 个目标；packages 227 个中 **98** 有规格 / **129** 零规格；零规格目录（含单文件目录）**23** 个（≥2 文件的 9 个）；零规格脚本 **14** 个 | **数字口径需订正**（结论方向不变） |
| A-2 | ✅ 已修（批次三十） | 工作树在；派生 + 反向看守 + 3 条突变规格齐 | 成立 |
| A-3 | `domain→core = 72`、`core→domain = 136` | 导入语句级：**domain→core 41 行命中 / core→domain 47 行命中（8 条 import 语句 / 5 个文件，非 API 层）**；模块级 **2 环 0、3 环 0** | **数字不成立**（结论「无环」成立；「无机器看守」成立 ⇒ 见 M-11） |
| A-4 | 文档断链 5 条报告全为误报 | 本轮未独立复核 | **未复核** |
| B-1 | `separation` 依赖存储精度（0.1852 时包络收紧到 9.11 会被拒） | `scripts/detector_evidence_check.py:403-431` 确为「按已存 rate 的舍入半宽构造包络」；注释里 9.34/3.45 例子逐字吻合 | 成立 |
| B-2 | Q-4 的 5 条 `external` 登记只查「note 非空 + 数字在 description 里」 | `detector_evidence_check.py:860-866`：**只查 `note` 非空**，并无「数字在 description 里」这一条 | 结论成立（精度更高） |
| B-3 | 9/9 语料不可第三方复现 | 本轮未复核（需内容仓） | **未复核** |
| B-4 | `genre/verifier.py` 1224 行 | `wc -l` = **1224** | 成立 |
| C-1 | 19 个脚本零规格（12 有 / 19 零） | scripts 共 **32** 个 .py：**18** 有规格 / **14** 零规格；零规格清单含 `backup_db` 之外的 `export_openapi`/`gen_frontend_types`（l21 只打 `scripts/_codegen_check.py`） | **数字需订正**（总 31 → 32） |
| C-2 | ✅ 已修（批次三十） | 成立，但见 M-8（CLI 层无看守）+ §4.1 第 1 条（临时目录泄漏） | 成立（不完整） |
| C-3 | `eval_regression.py` / `m1_report.py` 默认写仓库内 `docs/` | **已修**：两脚本现在默认写**系统临时目录**，`--write-docs` 才写仓库（批次二十九落地） | **已修未销 ⇒ 台账 stale** |
| C-4 | 不可逆脚本无回滚快照 | 本轮未独立复核（见 §4.3 附） | 未复核 |
| C-5 | 全量突变套件仍未接 CI | `ci.yml` 只有 `validate_specs.py` + `run_spec.py --all --precheck` | 成立 |
| C-6 | smoke 无 fast_mode / auto_commit / volumes / revise | `smoke_e2e.py` 中 4 个关键词 **0 命中** | 成立 |
| C-7 | 「真实 openssl 往返从未实跑」（证据：测试用 fake openssl） | **事实错误**：fake 只用于 `migrate_backups_to_sync.py`；`backup_db.py` 的加密往返测试自 `e467cf3`（2026-10-22 批次十六）起就用**真实 openssl**，本轮实跑 6 passed；子代理另独立跑通完整往返（49 表哈希全等） | **台账当天即写错**；条目应改为「缺的是恢复侧（见 M-2/M-3）」 |

### 4.3 其它复算与订正

- **版本六处**：实测**全部 3.10.0**（pyproject / package.json / package-lock ×2 / uv.lock / plugin.json / SKILL.md）⇒ 一致。
- **远端 tag**：AGENTS.md:195 记「本地 23 个 tag 一个都没推过 ⇒ **远端零 tag**」——
  **已过期**：实测远端 23 个，与本地**逐位一致**（含 v3.10.0）。§六 矩阵里已订正，§四 段未改 ⇒ **同一文件两处口径不一致**。
- **>1500 行文件**：AGENTS.md:63 记「存量挂账已于 2026-09-13 V4.0 批次**全部清零**」——
  现存 5 个超标文件，其中 **`apps/web/src/api/types.ts`（2026-09-13 时 1352 → 现 1861）** 与
  **`AiSettingsPage.tsx`（1384 → 1648）** 是**清零之后新增越过** ⇒ **事后新增违规**，不是存量挂账。
  另：`ProjectInitPanel.test.tsx` **2380 行、一字未改**（源文件 1858→227+12 子组件时测试没跟着拆）；
  `ChapterDetailPage.test.tsx` **3418 行**（全仓最大文件）。
- **CHANGELOG 归档**：24 个已归档版本段，最新 `## [3.10.0] - 2026-10-22`；`Unreleased` 现 945 行（批次十九~三十 15 段）。
  **2.0.1 有版本段但无 tag** ⇒ 版本号与 tag 非一一对应。
- **嵌套 pytest 的临时目录脆弱性（本轮实测，属环境但影响面要记）**：
  本机 `%LOCALAPPDATA%\Temp\pytest-of-AiStation` 一旦被创建就**连 owner 都拒绝访问**（`icacls` 拒绝列举），
  而本仓有**两处测试会再起一个 pytest 子进程**（`test_logger_level_isolation.py:165-182`、
  `test_human_baseline_ci_visibility.py` 的 reference run），子进程**继承默认 basetemp** ⇒
  子进程 exit 1 ⇒ 外层断言红。⇒ **本机全量基线「3902 passed」今日不可复现**（实测 3900 passed / **2 failed**，
  186.33s）。⚠️ 换 `--basetemp` 后两条**仍红**（子进程不受外层 `--basetemp` 影响）⇒ 这不是并发干扰，是真缺陷。
  **CI 在 Linux 上 `/tmp` 正常 ⇒ 该故障模式 CI 永远看不见。**
- **亚代理数字的两处分歧（按纪律两边都记）**：
  ① 域层审计报「core→domain 5 文件 **6 处**」，Lead 复算为 **5 文件 8 条 import 语句**（计数口径不同，以 Lead 为准）；
  ② 元审计报风控门「51 个夹具零可区分」，Lead grep 发现 `facet: "definition"` 在 **API 侧孪生**确有夹具
  ——**workflow 侧零覆盖的结论成立**，但「全仓零」的表述偏宽。

---

## 5. 文档事实订正清单（待修，AGENTS.md / 台账 / 代码注释）

| # | 位置 | 现文 | 应改为 |
|---|---|---|---|
| 1 | `AGENTS.md:195` | 「本地 23 个 tag 一个都没推过 ⇒ 远端零 tag」 | 已于 2026-10-22 批次十八**全部补推**（远端 23 个，与本地逐位一致，2026-10-23 复测） |
| 2 | `AGENTS.md:63` | 「存量挂账已于 2026-09-13 V4.0 批次全部清零」 | 清零当日合规、之后**新增越过**：`types.ts` 1861、`AiSettingsPage.tsx` 1648；测试文件 `ChapterDetailPage.test.tsx` 3418、`ProjectInitPanel.test.tsx` 2380（未拆） |
| 3 | `AGENTS.md:40` | 「packages/domain/ 领域服务：… plot / **hooks** …」 | `hooks/` 是 1 行空壳；真实 hooks 实现在 `packages/domain/ledger/service.py` |
| 4 | `packages/core/api/routers/backup.py:107` | 「23 张白名单表」 | **25**（`len(EXPORTED_TABLES)=25`） |
| 5 | `packages/domain/README.md` | 「空骨架（规划 Sprint 1）」「39 张业务表」「timeline.Service / relationship.Service / hooks.Service」 | 域层已实现 6196 行；业务表 **42**；那三个 Service **不存在**（实为 `CharacterService`/`WorldService`/`PlotService`） |
| 6 | 台账 C-3 | 列为未修 | **已修**（批次二十九） |
| 7 | 台账 C-7 | 「真实 openssl 往返从未实跑（测试用 fake）」 | **事实错误**：自 `e467cf3` 起即用真实 openssl；应改为「缺的是**恢复侧**」 |
| 8 | 台账 A-1 / A-3 / C-1 | 「72 有自身规格」「72 / 136」「12 有 / 19 零」 | **98 有 / 129 零（packages）**、**23 个零规格目录**、**domain→core 41 / core→domain 47（8 条 import / 5 文件）**、**scripts 18 有 / 14 零** |
| 9 | `tests/mutation_specs/l21_codegen_check.py:12` | `CG-ALWAYS-OK` 必红用例含 `test_missing_artifact_is_drift` | 实测该用例**不红**（MISSING 早返回在突变行之上） |
| 10 | `packages/core/context_engine/writer_input.py:915` 附近注释 | 「paged writer 读 `hook_ledger_excerpt` 等价于任务书 open 全量」 | writer 装配**从不设该键** ⇒ 实测 `hooks_open` **恒 0** |

---

## 6. 未验证 / 边界（诚实交代）

1. **未跑全量突变套件**（会临时改源码，须与任何读/写源码串行）⇒ 批次三十「3/3 CAUGHT」本轮**未复现**，
   以静态 + 内存注入等价分析替代。
2. **未跑 vitest 全量**；前端结论来自静态审查 + 定向用例 ⇒ **不声称前端已验**。
3. **真实 dev 库 `data/novelos.db` 未做任何写操作**；备份/迁移/漂移实验全部在临时库与合成夹具上做。
4. **未接触内容仓**（NovelOS-Content / NovelOS-Artifacts）；台账 B-3、A-4 因此**未复核**。
5. **未实测 `NOVELOS_API_HOST=0.0.0.0` 的实际暴露**（未起服务、未做 LAN 探测）。
6. **未验证真实同步盘落盘路径**（`D:\BaiduSyncdisk`）的写入/清理行为。
7. **未端到端复现 M-9 的两条子句被删后的红/绿**：插桩已完成（见 §4.3 附），但「删子句 ⇒ 全绿」这一步本轮**未实跑**
   （需要改源码 ⇒ 与突变执行器同属不可并发操作，留给修复批次）。
8. **缓存键 5 处漏维度中，Lead 亲自复现 2 处**（伏笔台账、章标题）+ 1 组对照；另 3 处为子代理实测（同装置、同口径）。

---

## 7. 建议修复顺序（按「改错会静默 × 后果不可逆」排）

| 序 | 项 | 规模 | 说明 |
|---|---|---|---|
| 0 | **M-15 内容仓 GitHub remote**（摘 remote 或加 pre-push 守卫）+ **M-14 痕迹迁出**（`docs/evaluation/` 迁痕迹仓并补 ignore；GitHub 上那部分**只能记录、不可撤回**） | 极小 | **隐私优先于一切**：一次误 push = 全书发布 |
| 1 | **C-1 质量门 fail-open** | 小 | `resolve_draft` 区分「读失败」与「无草稿」；期望 5xx 而非 100 分报告 |
| 2 | **M-2/M-3/M-4/M-5 备份链**（restore 工具 + 默认加密 + 密文自检 + 密钥缺失报错） | 中 | 数据不可逆；且与既有「密钥进私有网盘」策略直接冲突 |
| 3 | **M-1 缓存键 5 处漏维度 + 写路径推 `state_version`** | 中 | 同形状已复发 6~7 次；只补键维度是治标，**写路径推 version 才是根治** |
| 4 | **M-6 CI xdist 缺口**（把 xdist 挪进 `[project.optional-dependencies].dev`） | 极小 | 一行依赖；解锁 windows 并发路径的唯一覆盖 |
| 5 | **M-7 密钥收敛**（补 `secrets.json` 对应键 + 清库内明文 + 悬空条目检测） | 小 | 设计已在，只是没生效 |
| 6 | **M-8 CLI 退出码看守** + §4.1 其它 minor（批次三十返工） | 小 | 未提交的工作树顺手修干净再提交 |
| 7 | **M-9 风控门两条子句的夹具 + 突变规格** | 小 | 补 2 个夹具即可让「删子句必红」 |
| 8 | **M-13 exporter 取稿口径**（给 `commits` 加 `draft_version` 再取已 commit 那版） | 中 | 外发面，不可撤回 |
| 9 | **M-10 删角色引用检查** / **M-11 分层裁决 + 看守** / **M-16 content_sync 清理与降级** | 中 | M-11 需先裁决边界再写规则，不能直接加白名单掩盖 |
| 10 | **M-12 版本六处机器看守补齐 web 三处** | 小 | |
| 11 | 嵌套 pytest 的 basetemp 隔离（两处子进程 spawn 加 `--basetemp`） | 小 | 让本地基线可复现，也是 CI 之外的 Linux/macOS 保险 |
| 12 | §5 文档订正 10 条 + 台账 5 条过期条目 | 小 | 事实漂移是本仓反复出血的地方 |
| 13 | M-17 / M-18 / M-19 + 零覆盖目录的逐个补规格（按台账 C-1 的既有排序） | 中 | 长期排期 |

---

## 附：本次审查的证据位置

- Lead 自写探针（系统临时目录，仓库零改动）：
  `C:/Users/aistation/AppData/Local/Temp/lead_probe/probe_failopen.py`（C-1 端到端）、
  `probe_cachekey.py`（M-1 缓存键 + 对照组）、`record_highrisk.py`（M-9 插桩插件）。
- 子代理实跑证据目录：`C:/Users/aistation/AppData/Local/Temp/novelos_verify_c7_20261023/`（备份往返）、
  `novelos_verify/`（批次三十）、`novelos_failopen_*/`（域层 fail-open 机制）。
- 台账与交接单：`docs/impl/reviews/remaining-debt-2026-10-23.md`、`docs/impl/reviews/handoff-2026-10-23.md`。
