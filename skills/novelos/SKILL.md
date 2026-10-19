---
name: novelos
description: 用 NovelOS（本地优先的小说写作 OS，plan→write→review→commit 章节闭环）驱动从开书到投稿的完整长/中/短篇小说生产：建项目与题材包、逐章出计划与写正文、审校并按门禁意见改稿、快通道批量推进、Story State 快照与差分、交付判定与签约体检、导出 txt/docx/投稿包。触发说法：「写第 3 章」「开新书」「出计划」「审校」「改稿」「续写」「重写这一章」「快通道」「自动写下一章」「伏笔/人物状态」「导出投稿包」「签约体检」「写到哪了」。需要一个可运行的 NovelOS 服务，脚本会自动探测并可拉起。
license: AGPL-3.0
compatibility: 需要本机可运行的 NovelOS 服务（默认 http://127.0.0.1:18081，单用户本机、无鉴权）。仅用 Python 3.9+ 标准库，无第三方依赖；首次使用前用 scripts/novelos.py doctor 探测、ensure 拉起（需本地已有 NovelOS 仓库及依赖）。前端 UI 非必需。
metadata:
  version: "0.1.0"
  source: "https://github.com/charlesnty612/NovelOS"
  novelosVersion: "3.9.0"
allowed-tools: Bash, Read, Grep, Glob
---

# NovelOS —— 由 coding agent 驱动的章节生产 OS

NovelOS 是**本地优先**的小说写作 OS：SQLite 唯一事实源，章节走
**plan → write → review → commit** 四段闭环，Story State 版本化（v1 快照 + delta + 回滚 + 乐观锁），
质量门禁按 **severity × gate** 正交判定（block / confirm / auto）。
**所有判定逻辑都在服务端**——本 skill 只是它的手和眼。

## 写前必读（强制，先读后写）

> **在写任何正文、提交任何章节、给出任何剧情结论之前，先完成下面三步。**
> 跳过任何一步，代价都是「作者的话被丢在半路上」或「设定被悄悄改掉」。

### 第 0 步：先判断场景

| 用户在说什么 | 场景 | 必须读完的 references |
|---|---|---|
| 「开新书」「建项目」「配题材包」「配模型」「写圣经」 | 建项目 | `references/setup.md` |
| 「写第 N 章」「出计划」「审校」「改稿」「续写」「重写」 | 章节生产 | `references/chapter.md` + `references/quality.md` |
| 「门禁怎么算」「为什么被拦」「我能不能直接放行」 | 质量门禁 | `references/quality.md` |
| 「能交稿了吗」「签约体检」「导出投稿包」「导出 txt/docx」 | 交付 | `references/delivery.md` |
| 任何时候拿不准「这事能不能干 / 数据会不会外流 / 会烧多少额度」 | 边界 | `references/limits.md` |

**多个场景同时命中 ⇒ 全部读完**（例如「写完这章直接导出投稿包」＝ chapter + delivery）。

### 第 1 步：把对应的那一份**读到 EOF**

- **必须读到 EOF**（读到最后一行）。`rg` 检索、只看标题、只摘一段、凭印象写，
  **都不算读完**。
- 每次读完后，用你自己的话复述该文件里的**本轮约束**；复述不出来就是没读完。
- 端点名、请求体字段名**一律以 references 与服务端实际返回为准**，不许凭印象拼参数。

### 第 2 步：重读「本轮约束」并记进上下文

写正文 / 下指令之前，**重新打开**刚才那份 references 的 **「本轮约束」** 小节，
把里面的硬约束逐条抄进你这一轮的上下文（不是"看过就行"）。那一节是本阶段的
**不可协商项**：违反它产出的东西会被服务端门禁拦下，或直接写坏 Story State。

> references 的每一份都以 **「本轮约束」** 结尾——那就是本阶段要抄进上下文的东西。

## 本 skill 不做什么

- **不替代作者做创作决策。** 题材走向、人物结局、章节取舍属作者/用户；本 skill 只执行。
- **不替你写正文。** 正文由 NovelOS 的 writer agent 写；你的活是发对请求、读对报告、
  把作者的话完整地传下去。**不要在回复里"顺手"编一段小说正文**当成产出。
- **不改数据库、不改源码、不跑迁移。** 一切状态变化走 REST 端点。
- **不复刻服务端的判定逻辑。** 字数带、门禁三档、章节状态机、Story State 推进
  全部在服务端；本地一旦有第二份判定，两份就会漂。
- **不替你"确认"质量门禁。** `confirm` 档要作者显式签字（rule_id 清单 + 理由），
  不能由 agent 默默放行。
- **不发起真实 LLM 调用去"验证软件行为"。** 写章/审校会真的调模型并烧额度——
  那是**产品正常用途**，由作者决定要不要开；**为测试而调**不在此列。
- **不碰已有内容仓的既有书籍。** 新建项目可以；改写/续写既有测试书不在授权内。
- **不上传任何数据。** 全部在本机；详见 `references/limits.md`。

## 0. bootstrap：先把 NovelOS 跑起来

**每轮的第一件事**是确认服务在跑——不通就谈不上任何操作。

```bash
# 工作目录 = NovelOS 仓库根（skill 装在别处时把路径换成该 skill 目录下的实际路径）
NOVELOS_CLI="skills/novelos/scripts/novelos.py"

python "$NOVELOS_CLI" doctor          # 探测 http://127.0.0.1:18081/api/health
python "$NOVELOS_CLI" ensure           # 不在跑 → 尝试拉起（本地已有仓库时）
```

- `doctor` 成功 ⇒ 输出 `{"ok":true, "health":{"status":"ok","version":...,"tables":...}}`。
- `doctor` 失败（退出码 3）⇒ **先 `ensure`**；`ensure` 也失败 ⇒ 把它的错误原文
  （含日志尾部）交给用户，**不要自己猜怎么修**。
- `ensure` 只在本机已有 NovelOS 仓库时可用（它 `python -m packages.core.api.main`），
  **不下载任何东西**。用户还没克隆仓库 ⇒ 让他先克隆并装依赖。

## 1. 命令速查（全部走 `scripts/novelos.py`）

**不要手拼 curl**——薄客户端已经处理了 base URL、JSON 编码、错误诊断。
默认输出**紧凑 JSON**（机器可读），加 `--pretty` 给人看。

| 想做什么 | 命令 |
|---|---|
| 探测服务 | `python "$NOVELOS_CLI" doctor` |
| 拉起服务 | `python "$NOVELOS_CLI" ensure` |
| 列项目 / 建项目 / 看项目 | `project list` / `project create --name "书名" [--genre 玄幻] [--writing-bible "铁律…"]` / `project show --project prj_x` |
| 列章节 / 看章节 | `chapter list --project prj_x` / `chapter status --project prj_x --chapter-no 3` |
| 出计划 | `chapter plan --project prj_x --chapter-no 3` |
| 写正文 | `chapter write --project prj_x --chapter-no 3 --target-word-count 3000` |
| 审校 | `chapter review --project prj_x --chapter-no 3 --wait` |
| 提交 | `chapter commit --project prj_x --chapter-no 3` |
| 看 run / 恢复 / 人工批准 | `run status <run_id>` / `run resume <run_id> --human-input '{…}'` / `run approve <run_id>` |
| Story State | `state show --project prj_x [--version 7]` / `state diff --project prj_x --a 6 --b 7` |
| 质量报告 | `quality report --chapter ch_x` / `quality report --project prj_x` |
| 门禁显式接受 | `quality gate-override --rule-id AI-BEAT-REPEAT --reason "刻意的复沓"` |
| 交付判定 / 签约体检 | `delivery verdict --project prj_x` / `delivery signing-check --project prj_x` |
| 导出 | `export --kind txt\|docx\|submission --project prj_x [--chapter-no 3] [--out 路径]` |

`--chapter-no N` 由客户端查一次章节列表换算成 `chapter_id`（**唯一**的本地便利变换；
其余参数逐字透传给服务端）。

## 2. 典型节奏

```
plan → write → review → commit
```

1. **plan**（可跳过，但跳过后没有章节目标/节拍可依）：产出导演计划，落
   `chapters.plan_json`。要人工定调就写 `chapters.outline_json`（策展面，planner 不覆盖它）。
2. **write**：写正文，落草稿（`drafts` 表，每次新版本）。
3. **review**：审校，**通常停在 `PAUSED` 等作者决议**——读 `run status` 的
   `pause_payload`，把评审意见摘要给作者，**由作者拍板**（`run approve` / `run resume`）。
4. **commit**：提交，把本章结果写进 Story State（推进角色目标 / 收放伏笔 / 记世界观 delta）。

**每个起跑端点都立即返回 `{"run_id","status":"RUNNING","current_node":null}`**——
真终态要**轮询** `GET /api/runs/{id}`。客户端已经封装好：

```bash
python "$NOVELOS_CLI" chapter review --project prj_x --chapter-no 3 --wait
# → {"run_id":…,"started":{…},"final":{"status":"PAUSED","pause_payload":{…}}}
```

`--wait` 会在**终态（COMPLETED/FAILED/CANCELLED）或 PAUSED（等人工）**时停下。
**PAUSED 不是失败**——它在等人。

## 3. 快通道（`--fast-mode`）

快通道 = **关闸门，不关判定**：跳过人工审批门与门禁**阻断**，
但 `observer` 照跑（伏笔照收、角色目标照推进、Story State 照写），
质量报告也照算、照落库。

- 想要「少一次人工往返」：项目级开 `projects.fast_mode`（`project create --fast-mode`），
  或单次运行加 `--fast-mode`；单次关用 `--no-fast-mode`。
- **「写完自动提交」是另一个独立开关** `--auto-commit`（per-request，默认关）；
  快通道本身不负责起 commit run。
- **快通道 ≠ 自动批准**：它**不替你签字**。`confirm` 档在快通道下**不再阻断**
  （`should_block` 恒假，走 `report` 语义），但报告照落库——
  **「没被拦」不等于「已签字」**。
- 详见 `references/chapter.md` 的「本轮约束」。

## 4. 质量门禁：block / confirm / auto

后果轴与严重度**正交**（`packages/core/quality/issues.py::issue_gate` 是唯一权威）：

| 档 | 含义 | 谁能放行 |
|---|---|---|
| `block` | 硬停 | **不可覆盖**。修好再提交 |
| `confirm` | 不许静默通过 | 作者显式接受：`{"rule_ids":[…],"reason":"…"}`（须覆盖**全部**命中的 confirm rule_id 且理由非空） |
| `auto` | 静默通过 | 无需动作 |

`block` 与 `confirm` 同时命中时，服务端的阻断消息会带**两类** rule_id 清单 +
证据摘录。**照着清单修，不要猜。** 详见 `references/quality.md`。

## 5. 交付

```bash
python "$NOVELOS_CLI" delivery verdict --project prj_x          # 逐章交付判定 + 项目汇总
python "$NOVELOS_CLI" delivery signing-check --project prj_x    # 签约体检
python "$NOVELOS_CLI" export --kind submission --project prj_x  # 投稿包（服务端 format=fanqie）
```

`--kind` → 服务端 `format`：`txt`→`txt`、`docx`→`docx`、`submission`→`fanqie`。
导出是**文件下载**（不是 JSON），客户端会落盘并回报路径。详见 `references/delivery.md`。

## 6. 自检（改本 skill 时）

```bash
python skills/novelos/scripts/check_skill_package.py            # 契约自检
python skills/novelos/scripts/check_skill_package.py --selftest  # 造坏副本并断言自己报红
```

## 参考文件（按需读，读到 EOF）

- `references/setup.md` —— 建项目 / 题材包 / 模型配置 / 写作圣经
- `references/chapter.md` —— 单章 plan→write→review→commit 操作手册（含轮询与快通道）
- `references/quality.md` —— 质量门禁怎么读、gate_override 正确用法
- `references/delivery.md` —— 交付判定 / 签约体检 / 投稿包导出
- `references/limits.md` —— 边界与已知限制（无鉴权 / 数据不出本机 / 额度 / 不做什么）
