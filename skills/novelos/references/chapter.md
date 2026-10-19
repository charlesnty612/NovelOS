# chapter —— 单章 plan → write → review → commit 操作手册

> **读法**：本文件是「写章」阶段的唯一依据。**必须读到 EOF**（含末尾「本轮约束」）。
> 端点名与请求体字段**逐字**取自 `packages/core/api/routers/workflows/control.py`、
> `common.py`、`revise.py`、`runs.py`。

## 0. 闭环总览

```
plan ──▶ write ──▶ review ──▶ commit
 │          │         │          │
 └──────────┴─────────┴──────────┴──▶ Story State 推进（observer 在 commit 里）
```

四个阶段各对应一个起跑端点，**同一个 run 家族**：

| 阶段 | REST | workflow 名 |
|---|---|---|
| plan | `POST /api/projects/{project_id}/chapters/{chapter_id}/plan` | `chapter-plan` |
| write | `POST /api/projects/{project_id}/chapters/{chapter_id}/write` | `chapter-write` |
| review | `POST /api/projects/{project_id}/chapters/{chapter_id}/review` | `chapter-review` |
| commit | `POST /api/projects/{project_id}/chapters/{chapter_id}/commit` | `chapter-commit` |
| 门禁改稿 | `POST /api/projects/{project_id}/chapters/{chapter_id}/gate-revise` | 内含 write+review |

**每个起跑端点都立即返回**：

```json
{"run_id": "run_xxx", "status": "RUNNING", "current_node": null}
```

`status` 恒为 `RUNNING`（启动失败一律 400/409/422 抛错，不会返回体）。
**真终态必须轮询** `GET /api/runs/{run_id}`。

---

## 1. 通用请求体（`StartWorkflowRequest`）

全部字段**可省**。省掉即走服务端默认。

| 字段 | 类型 | 含义 |
|---|---|---|
| `author_intent` | str | **本次运行**的作者铁律（与项目圣经**拼接**，圣经在前） |
| `target_word_count` | int(100~100000) | 本章目标字数 |
| `expected_role` | str | 本章期望的角色/职责 |
| `quality_gate_mode` | `"enforce"`\|`"report"` | 门禁模式；`enforce` 阻断、`report` 只报告 |
| `fresh_write` | bool | 全章重写（忽略改稿意见与既有草稿） |
| `draft_version` | int≥1 | 审校指定稿版本（review 用） |
| `deep_review` | bool | 开启二审（`deep_reviewer`） |
| `critic_mode` | `"off"`\|`"sample"`\|`"always"` | critic 采样模式（review 用） |
| `gate_override` | dict | **显式接受**声明，见 `references/quality.md` |
| `fast_mode` | bool? | 本次运行快通道（三态：`true`开/`false`显式关/省略回落项目列） |
| `auto_commit` | bool? | 写完自动衔接提交（**仅 write 有意义**） |
| `model_overrides` | dict | 单次运行的 capability→profile 覆盖 |
| `mock_providers` | dict | 测试用脚本化输出（**不要在生产用**） |

**CLI 映射**（`scripts/novelos.py`）：

| 字段 | CLI 参数 |
|---|---|
| `author_intent` | `--author-intent "…"` |
| `target_word_count` | `--target-word-count 3000` |
| `expected_role` | `--expected-role "…"` |
| `quality_gate_mode` | `--quality-gate-mode enforce\|report` |
| `fresh_write` | `--fresh-write` |
| `draft_version` | `--draft-version 3` |
| `deep_review` | `--deep-review` |
| `gate_override` | `--gate-override '{"rule_ids":["…"],"reason":"…"}'` |
| `fast_mode` | `--fast-mode` / `--no-fast-mode` |
| `auto_commit` | `--auto-commit` |
| `critic_mode` / `model_overrides` / `mock_providers` | 未封装 ⇒ 需时手写请求 |

---

## 2. 逐阶段操作

### 2.1 plan —— 生成导演计划

```bash
python skills/novelos/scripts/novelos.py chapter plan \
  --project prj_x --chapter-no 3 \
  --target-word-count 3000 --author-intent "本章只揭示契约，不动手" --wait
```

产出落在 `chapters.plan_json`（导演计划：chapter_goal / key_beats / …）。
**plan_json 是「生成面」**——planner 会整体覆盖它。

> **人工大纲写在 `outline_json`（策展面）**，不进 plan_json：
> `PATCH /api/chapters/{chapter_id}` body `{"outline_json":{…}}`。
> `outline_json` 会进 planner 的装配输入（有独立缓存键维度，改它必定 miss 缓存）。

### 2.2 write —— 写正文

```bash
python skills/novelos/scripts/novelos.py chapter write \
  --project prj_x --chapter-no 3 --target-word-count 3000 --wait
```

产出新草稿（`drafts` 表，`version` 递增；`created_by` 形如 `writer:<prompt 版本>`）。
**每次 write 都产出新版本**——旧版本仍在库里，可回看。

写完想看稿子：`GET /api/chapters/{chapter_id}/drafts`（`novelos.py` 未封装）。

### 2.3 review —— 审校（**通常停在 PAUSED 等人**）

```bash
python skills/novelos/scripts/novelos.py chapter review \
  --project prj_x --chapter-no 3 --wait
```

`review` 的最后一个人工节点是 `author_review`，**它总是抛 PauseRequested**
⇒ run 进入 `PAUSED`，`pause_payload` 里带：

| 键 | 含义 |
|---|---|
| `stage` | 停在哪个环节（如 `chapter-review.author_review`） |
| `review_report` | 主评审报告 |
| `critic_*` | critic（写法层）评审结果（`critic_status='skipped'` 时不出现） |
| `deep_review_*` | 二审结果（未开 deep_review 时不出现） |
| `message` | 人类可读提示 |

**PAUSED 不是失败**——它在等人。**把评审意见摘要给作者，由作者拍板**：

```bash
# 通过
python skills/novelos/scripts/novelos.py run approve <run_id>
# 驳回（run 收尾为 FAILED，error='rejected'）
python skills/novelos/scripts/novelos.py run approve <run_id> --reject --note "第三章节奏太赶"
# 驳回并要求改稿（自动改稿回路接管）
python skills/novelos/scripts/novelos.py run approve <run_id> --reject --revise --note "第三章节奏太赶"
```

`human_input` 的三态形状（服务端 `chapter_review/pipeline.py`）：

```json
{"approved": true}
{"approved": false}
{"approved": false, "revise": true, "note": "…"}
```

### 2.4 commit —— 提交（落 Story State）

```bash
python skills/novelos/scripts/novelos.py chapter commit --project prj_x --chapter-no 3 --wait
```

commit 里跑 **`observer`**：抽角色目标推进 / 伏笔收放 / 世界观 delta 并写
`story_state`。**observer 是唯一推进状态的节点**——任何「跳过 review」的改动都必须
保留它，砍掉它第 2 章起失忆。

**两道守卫**：
1. 章节状态机：非 `REVIEWED`（快通道下 `DRAFTED` 也放行）会拒。
2. 时序守卫：**最近一次 COMPLETED review 之后又产生了更新草稿** ⇒ 409，
   提示「存在未审改动，请先重新审校再提交」。

**门禁（`quality_gate`）**：默认 `quality_gate_mode="report"`（只报告不阻断）。
`enforce` 时命中阻断规则会挂起 run，并把改稿意见写进 `plan_json.revision_note`。
详见 `references/quality.md`。

---

## 3. 轮询 run 终态

**起跑端点立即返回，真终态在 `GET /api/runs/{run_id}`。**

```bash
python skills/novelos/scripts/novelos.py run status <run_id>
python skills/novelos/scripts/novelos.py run status <run_id> --wait --wait-timeout 900
```

run 状态六值：`PENDING` / `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED` / `CANCELLED`。

`GET /api/runs/{run_id}` 的响应：

| 字段 | 说明 |
|---|---|
| `status` | 上列六值之一 |
| `current_node` | 当前节点 id（`author_review` / `quality_gate` / `observer` …） |
| `error` | 失败原因（成功时**键在、值为 null**） |
| `nodes[]` | 逐节点记录：状态 / 输入 / 输出 / prompt 版本 / token 用量 / 延迟 |
| `checkpoint_json` | 暂停/恢复的持久化点（键集随 workflow 演进） |
| `pause_payload` | **仅 PAUSED 时出现** |
| `stage_models` | **仅 PAUSED 时出现**（本次实际用的 model_id 映射） |
| `workflow_name` | `chapter-plan` / `chapter-write` / `chapter-review` / `chapter-commit` / `project-init` |

> **排障先看两处**：`run status` 的 `error` 与 `nodes[]` 里那个 `status="FAILED"`
> 的节点的 `error` 字段。**别猜**。

**取消**：`POST /api/runs/{run_id}/cancel` —— **只对 `RUNNING` 有效**；
`PAUSED` 取消不了（要走 resume 后的 reject/approve 决议路径）。
`novelos.py` 未封装 cancel。

---

## 4. 快通道（`fast_mode`）

**关闸门，不关判定。** 注意：**快通道本身不等于「写完自动提交」**——
「写完自动起 commit run」是另一个独立开关 `--auto-commit`（per-request，默认关）。

| 项 | 快通道下 |
|---|---|
| `quality_gate` 阻断 | **恒不阻断**（走既有 `report` 语义），报告照算、照落库 |
| `author_review` 人工审批门 | 跳过 |
| `high_risk_approval` 风控门 | 跳过（但**记 `system:fast_mode`**，不写 `"human"`——那是没人做过的审批） |
| `observer` | **照跑**（伏笔/角色目标/世界观 delta 照写） |
| `inject_validate` / `commit_delta` | 照跑 |
| 章节状态校验 | 放行 `DRAFTED`（正常路径要求 `REVIEWED`） |

**三态**：`projects.fast_mode` 是可空三态列（NULL 未设置 / 1 开 / 0 关）。
解析优先级（单点 `packages/core/api/routers/workflows/fast_mode.py::resolve_fast_mode`）：
**请求体 > 项目列 > 环境变量 > 关**。

```bash
# 单次开 / 单次关
python skills/novelos/scripts/novelos.py chapter write --project prj_x --chapter-no 3 --fast-mode
python skills/novelos/scripts/novelos.py chapter write --project prj_x --chapter-no 3 --no-fast-mode
# 项目级开
python skills/novelos/scripts/novelos.py project create --name "书" --fast-mode
```

**`auto_commit` 是另一个独立开关**（per-request，默认关）：写完自动起 commit run。
`--auto-commit` 只对 `write` 有意义。

> **快通道 ≠ 自动批准**。快通道跳过的是闸门，不是「跑一遍审校再自动批」。
> `confirm` 档的门禁在快通道下也**不阻断**——但**别把它读成「已签字」**。

---

## 5. 按门禁意见改稿（`gate-revise`）

门禁 `enforce` 阻断后，run 的 `plan_json` 会带 `gate_blocked` 标记 + `revision_note`。

```bash
python skills/novelos/scripts/novelos.py run status <run_id>   # 看 pause_payload / error
```

一键改稿：`POST /api/projects/{project_id}/chapters/{chapter_id}/gate-revise`
（`novelos.py` **未封装**）。body（`GateReviseRequest`）：
`mock_providers` / `model_overrides` / `critic_mode` / `deep_review` /
**`target_word_count`** / **`author_intent`**。

> **务必带上 `target_word_count` 与 `author_intent`**：不带就会退回服务端默认
> （3000 字）并丢掉铁律 ⇒ 改稿在错误口径上进行。

写改稿意见：`PATCH /api/chapters/{chapter_id}/revision-note` body `{"note":"…"}`
（章节状态须 ∈ `PLANNED`/`DRAFTED`/`REVIEWED`；已提交的章 409）。

---

## 6. 手动改稿（人改完再审）

```bash
# 读现有草稿
GET /api/chapters/{chapter_id}/drafts
# 写一版人工草稿（章节状态须为 DRAFTED 或 REVIEWED，否则 409）
POST /api/chapters/{chapter_id}/drafts  {"content": "……"}
# 写改稿意见
PATCH /api/chapters/{chapter_id}/revision-note  {"note": "……"}
```

人工草稿的 `created_by` 固定为 `"human"` ⇒ 质量报告里的「人工加工占比」（Q8）
会把它计入人工字符数。

---

## 7. Story State 快照与差分

```bash
python skills/novelos/scripts/novelos.py state show --project prj_x
python skills/novelos/scripts/novelos.py state show --project prj_x --version 7
python skills/novelos/scripts/novelos.py state diff --project prj_x --a 6 --b 7
```

- `GET /api/projects/{id}/state` → `{"state_version":N,"characters":[…],"world":{…},
  "hooks":[…],"debts":[…],"recent_events":[…],"events":{…}}`。
  `state_version` 是**唯一**的版本字段名；无快照时返回 `0`。
- `GET /api/projects/{id}/state/versions/{n}` → 同一形状的历史版本。
- `GET /api/projects/{id}/state/diff?a=&b=` → `{"version_a","version_b","branch_id"}`
  + 六个实体段里**变了**的那些（`characters` / `world` / `hooks` / `debts` /
  `events_changed` / `recent_events`）。**没变就少那几个键**（不是 null）。
- 首次建快照：`POST /api/projects/{id}/state/init` body `{"chapter_id":"ch_x"}`。
- 提交历史：`GET /api/projects/{id}/commits`；回滚：`POST /api/commits/{commit_id}/rollback`。

**乐观锁**：`state_version` 不匹配 ⇒ 409 `{"error":"optimistic_lock", …}`。
有人（或另一个 run）改了状态 ⇒ **重读再提交**，不要强推。

---

## 8. 续写候选（`continue`）

`POST /api/projects/{id}/chapters/{cid}/continue` —— 基于最新草稿 + 本章 plan
调 N 次 LLM，返回**候选段落**（不落库）。`num_variants ∈ [1,3]`；
章节状态须 ∈ {DRAFTED, REVIEWED}，否则 409。
**这是真实 LLM 调用**（烧额度）。`novelos.py` 未封装。

---

## 本轮约束

1. **起跑 ≠ 完成**：拿到 `run_id` 后**必须轮询**到终态/PAUSED 才汇报。
2. **PAUSED 是「等人」，不是失败**：把 `pause_payload` 的评审意见摘要给作者，
   **由作者拍板**；不要自己替作者 `approve`（除非作者明确说了「通过」）。
3. **字数目标与铁律每次都带**：`--target-word-count` + `--author-intent`；
   长期铁律写进项目 `writing_bible`（见 `references/setup.md`）。
4. **人工大纲写 `outline_json`，不写 `plan_json`**。
5. **改稿必须带 `target_word_count` 与 `author_intent`**，否则口径退回默认、铁律丢失。
6. **快通道 ≠ 自动批准**；`auto_commit` 是独立开关；**observer 永远不能砍**。
7. **409 是有信息的**：并发活跃 run / 未审改动 / 状态机非法 / 乐观锁 —— 逐字读
   `detail` 再决定，**不要盲目重试**。
8. **写章、审校、续写都是真实 LLM 调用**（烧额度）——确认作者要跑再跑。
