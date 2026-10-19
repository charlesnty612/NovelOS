# quality —— 质量门禁怎么读、怎么放行

> **读法**：本文件是「审校 / 被拦 / 要不要放行」阶段的唯一依据。
> **必须读到 EOF**（含末尾「本轮约束」）。
> 判定权威在服务端：`packages/core/quality/issues.py::issue_gate` 是**唯一**权威。

## 1. 两条轴，正交

| 轴 | 取值 | 回答什么 |
|---|---|---|
| `severity` | `error` / `warning` / `info` | 哪里出问题、多严重 |
| `gate` | `auto` / `confirm` / `block` | **谁能说通过** |

**`gate` 不由 `severity` 推导**，也不由 category 矩阵推导 ⇒
「很严重」不等于「必须签字」，「只是 warning」也不等于「可以静默过」。

### `gate` 三档

| 档 | 语义 | 谁能放行 |
|---|---|---|
| `auto` | 不产生后果，静默通过 | 无需动作 |
| `confirm` | **不许静默通过** | 作者**显式接受**：`{"rule_ids":[…],"reason":"…"}` |
| `block` | 硬停 | **不可覆盖**。修好再提交 |

### `issue_gate` 的判定顺序（先到先得，不可降级）

1. `severity == "error"` **且** `rule_id ∈ BLOCKING_RULES` ⇒ `block`
2. `rule_id ∈ CONFIRM_RULES` ⇒ `confirm`（**与 severity 无关**——warning 级也可以要求签字）
3. issue 自带 `gate == "confirm"` ⇒ `confirm`（**单条上修**：量级依赖的规则走这条）
4. 其余 ⇒ `auto`

`Issue.gate` 字段是**材料化回显**（落库后可直读），**权威仍是 `issue_gate`**。
字段只能**上修**（把表外规则单条升为 `confirm`），**不能把表内规则降级**。

---

## 2. 两张白名单（当前内容）

### `BLOCKING_RULES`（error 且在表内 ⇒ 硬停）

| rule_id | 含义 |
|---|---|
| `SCHEMA_VALIDATION_FAILED` | delta 不符合 schema |
| `RULE_CHAR_DEAD_ACTIVE` | 已死亡角色被写活动字段（状态写坏） |
| `RULE_WORLD_HARD_RULE_CHANGED` | hard 世界规则被改写（世界观写坏） |
| `RULE_Q6_OVERLAP_RATE` | 参照书重叠率超红线（抄袭风险） |
| `RULE_Q8_HUMAN_RATIO_LOW` | 人工占比红线（**默认 warning**；`NOVELOS_QUALITY_Q8_STRICT=1` 才升 error） |
| `scoring_missing_subscore` | 子分缺失（无法给出有效评分，系统级） |

**入表标准**：错误会破坏「提交物本身可用 / 合规」。

### `CONFIRM_RULES`（命中即须签字）

| rule_id | 含义 |
|---|---|
| `AI-BEAT-REPEAT` | 节拍/段落级逐字复现（确定性算子，可复算） |

**入表标准（严格）**：确定性、可复算、作者无从辩驳 ⇒ 正确处理是「要求显式接受」，
而不是调参。

> ⚠️ **量级依赖的规则不进静态表**。例：三字重复率（trigram）分两档
> ——warn 0.16 / confirm 0.25，都取自本仓实测分位；只有**超过 confirm 阈值的那一条**
> 由 `scoring.score_style` 单条上修为 `confirm`。
> **不要凭印象把某个「看起来该拦」的规则写进放行清单**——先看报告里的 `gate` 字段。

---

## 3. 读一份质量报告

```bash
python skills/novelos/scripts/novelos.py quality report --chapter ch_x --pretty
python skills/novelos/scripts/novelos.py quality report --project prj_x --limit 20
```

REST：`GET /api/chapters/{chapter_id}/quality`（最新一份，没有 ⇒ 404）、
`GET /api/projects/{project_id}/quality?limit=`（列表）。
现场评估：`POST /api/chapters/{chapter_id}/quality/evaluate`。

报告结构（`QualityReportResponse`）：

| 字段 | 说明 |
|---|---|
| `overall` | 总分 0~100。**命中 blocking error ⇒ 归零**；`confirm` **不归零**；子分缺失 ⇒ 归零 |
| `scores_json` | 七子分 + `_meta`：`plot` / `character` / `continuity` / `style` / `pacing` / `foreshadowing` / `ai_trace` |
| `issues_json[]` | 每条含 `severity` / `category` / `rule_id` / `message` / `location` / **`gate`** / `suggestion` / `evidence_refs` |
| `draft_version` | 本次评的是**哪一版**草稿（**核对它对不对得上你的对象**） |
| `judge` | LLM judge 四维分（`pacing`/`style`/`logic`/`dialogue` 0-100）+ `verdict` / `top_issues`；不计入 `overall` |
| `formula_hash` | 评分口径指纹。**变了 ⇒ 历史报告不可直接横比** |

`overall` 公式（七子分等权平均，四舍五入）：
`round((plot + character + continuity + style + pacing + foreshadowing + ai_trace) / 7)`

> **`overall` 高不代表没问题。** `confirm` 档的重复类问题**不压分**（构造上就进不了
> blocking 白名单），但它**不许静默通过**。⇒ **看 `issues_json[].gate`，别只看 `overall`。**

---

## 4. `gate_override` 的正确用法

**只有 `confirm` 档能被它放行**；`block` 档**不可覆盖**。

放行条件（服务端 `chapter_commit/gate.py` 判定，**全部**要满足）：

1. `rule_ids` **覆盖本次命中的全部 confirm rule_id**（少一个 ⇒ 照样阻断）
2. `reason` **非空**（空白字符串不算）
3. 处于 `enforce` 模式（`report` 模式本来就不阻断）

生成（不提交）：

```bash
python skills/novelos/scripts/novelos.py quality gate-override \
  --rule-id AI-BEAT-REPEAT --reason "刻意的复沓，角色口头禅" --pretty
# → {"gate_override":{"rule_ids":["AI-BEAT-REPEAT"],"reason":"刻意的复沓，角色口头禅"}}
```

提交时带上（`chapter commit`）：

```bash
python skills/novelos/scripts/novelos.py chapter commit \
  --project prj_x --chapter-no 3 \
  --gate-override '{"rule_ids":["AI-BEAT-REPEAT"],"reason":"刻意的复沓，角色口头禅"}'
```

> **别自己拼 JSON**——`rule_ids` 少写、写成别的 rule_id、或 `reason` 留空，
> 服务端会照常阻断并把清单回给你。**先读阻断消息里的 rule_id 清单，再生成。**

---

## 5. 阻断消息长什么样

`block` 与 `confirm` 同时命中时，响应是 `gate_kind="block+confirm"`：

- `gate_blocked.rule_ids` = **两组并集**（blocking ∪ confirm）
- 错误消息 = blocking 段 + `| guidance=` + confirm 段（含**每条要准备什么**的指引）
- 前端按 `| guidance=` 切分 ⇒ **零影响**

`block` 单独命中时消息**只有 blocking 段**（不加 guidance 字段）。

> **拿到阻断消息先读 `rule_ids` 清单**——那是「你到底要准备什么」的说明书。
> 逐条修，别猜。

---

## 6. 门禁在哪一步生效

| 位置 | 行为 |
|---|---|
| `chapter-commit` 的 `quality_gate` 节点 | 唯一执行门禁的节点 |
| `quality_gate_mode="report"`（**默认**） | 照算、照落库，**不阻断** |
| `quality_gate_mode="enforce"` | 命中 block/confirm ⇒ 挂起 run，写 `plan_json.gate_blocked` + `revision_note` |
| 快通道开 | `should_block` **恒假**（走 `report` 语义）；**报告照落库** |
| `POST /api/chapters/{cid}/quality/evaluate` | 现场评估（**纯规则计算，不调 LLM**），落一份新报告 |
| `POST /api/chapters/{cid}/quality/judge` | 落 LLM judge 四维分（**分数由调用方给，本端点不调 LLM**；**不计入 overall**） |

**`block` 的处理**：**没有 override 通道**。修好（改稿/改设定/改 schema）再提交。
**`confirm` 的处理**：作者显式接受（上一节）。

---

## 7. 已知陷阱（别踩）

- **"登记了规则" ≠ "门禁看得到它"**：算子命中必须真的进到 `report.issues`，
  门禁才看得到。报告里没有的 rule_id ⇒ 门禁不会因它阻断。
- **同一个 `rule_id` 在报告与门禁消息里可能形态不同**：报告按 `category` 分桶，
  但 `rule_id` 是权威键。**按 `rule_id` 认，不按 `category` 认。**
- **`draft_version` 要核对**：报告评的是**被审的那一版**；拿「最新稿」去对报告，
  会对不上。
- **人工改稿后要重新评估**：旧报告对应旧版本。

---

## 本轮约束

1. **看 `gate`，不只看 `overall`**；`overall` 高也可能藏着不许静默通过的问题。
2. **`block` 不可覆盖** ⇒ 修，不要找 override。
3. **`confirm` 要作者的显式接受**：`rule_ids` 覆盖**全部**命中项 + `reason` 非空。
   **agent 不得代作者签字。**
4. **用 `quality gate-override` 生成 JSON**，别手拼。
5. **核对 `draft_version`** 与你正在处理的对象是不是同一版。
6. **按 `rule_id` 认问题**，不按 `category` 认。
7. **别把可测算子（对白占比 / 段落长度 / 频率类）当硬指标**——它们是体检项，
   不是处方；要求模型「凑指标」只会把稿子写坏。
