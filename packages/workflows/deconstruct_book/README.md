# deconstruct-book 工作流（Sprint 11 上半）

把整本参照书按章节切分 → 逐章抽取抽象 `ChapterExtract` → 聚合为 `ReferenceCanon` →
G-sim 校验阻断相似度 → 落库 `reference_canons` + `canon_extracts` + 渲染 Markdown 报告。

权威参考：
- `docs/reference-canon/reference-canon-v0.md`（§3 节点规范 + §6.1 metadata + §1.2 三条硬边界）
- `docs/reference-canon/schemas/reference-canon.schema.json`（schema 权威）
- `docs/agents/prompts/deconstructor-chapter-v0.md`（T2 agent）
- `docs/agents/prompts/deconstructor-aggregate-v0.md`（T3 agent）

## 节点表

| 节点 ID | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| `T1_split_chapters` | Transform | 正则切分 `第N章` 标题行 | `ctx.text` | `ctx.segments[]`（chapter_index/title/raw_text） |
| `T2_extract_chapters` | Transform | 对每章调 `deconstructor_chapter` agent | `ctx.segments` + `ctx.reader_profile` | `ctx.chapter_extracts[]`（ChapterExtract） |
| `T3_aggregate` | AI | 调 `deconstructor_aggregate` agent | `ctx.chapter_extracts` + `ctx.book_title` 等 | `ctx.canon_json`（ReferenceCanon） |
| `G_sim_check` | Transform | 13 字 shingle 重叠检测（B-2 边界） | `ctx.canon_json` + `ctx.text` | `g_sim_passed`；阻断时抛 ValueError |
| `T4_persist` | State | 注入 metadata + 渲染报告 + 落库 | `ctx.canon_json` + `ctx.chapter_extracts` 等 | `ctx.canon_id` + `ctx.report_md` |

## 输入契约（workflow start）

```
{
  "project_id":   "<prj_xxx>",
  "book_title":   "<string>",
  "text":         "<string, 全文 UTF-8；MVP 仅 .txt>",
  "reader_profile": "male_fantasy" | "male_urban" | "male_system"
                   | "female_romance" | "female_palace" | "female_suspense" | "general"
  (可选)
  "mock_providers": { "deconstructor_chapter": [...], "deconstructor_aggregate": [...] }
}
```

## 失败语义

| 节点 | 触发条件 | run 状态 |
|---|---|---|
| T1 | 无 `第N章` 标题行 / 空文本 | FAILED |
| T2 | 单章 agent 输出不合规（run_agent 1 次重试后仍失败） | FAILED |
| T3 | aggregate 输出 schema 不合规（run_agent 1 次重试后仍失败） | FAILED |
| G-sim | canon_json 与原书存在 ≥13 字公共 shingle | FAILED（B-2 边界） |
| T4 | reader_profile 不在 schema 枚举 / DB 写失败 | FAILED |

## 硬边界落实点（reference-canon-v0.md §1.2）

| 边界 | 落实位置 |
|---|---|
| **B-1** 只输出抽象模式 | T2 / T3 prompt 已约束；本 workflow 不二次强制（依赖 prompt 自检 + schema `maxLength`） |
| **B-2** 产出须过相似度检测 | `G_sim_check` 节点：13 字 shingle 重叠即阻断（与 `quality.guardrails._shingles` 一致） |
| **B-3** 不含原文片段 | T2 / T3 prompt 约束 string 字段 ≤80 字；schema `maxLength: 80` 强制；本 workflow 仅检查长度不越界 |

## metadata 五字段（T4 注入）

| 字段 | 值来源 |
|---|---|
| `source_book_title` | `ctx.book_title` |
| `deconstruct_date` | `ctx.deconstruct_date`（默认 `now_iso()`） |
| `deconstruct_version` | `ctx.deconstruct_version`（默认 `"deconstruct-book-v0"`） |
| `target_reader_profile` | `ctx.reader_profile` |
| `license_check_status` | 固定 `{checked:false, license:"unknown", compatible:false}`（MVP 未实装预检节点） |

## MVP 偏差（deferred）

1. **epub 解析不支持**：MVP 仅支持 `.txt` 纯文本；epub / docx 后续 Sprint 通过独立 T0-preprocess 节点补；
2. **Human 补切分未做**：T1 切不出章节 → run FAILED；V0.1 ADR 评估是否挂入 Human 节点；
3. **多书加权（OV-2）defer**：单 workflow 仅产出一本书的 canon；多书加权合并留给 V1；
4. **embedding 双轨 defer**：G-sim 当前仅做 13 字 n-gram；embedding 轨道（轨道 B）待 Sprint 5 golden 校准后补；
5. **超长章节切片（>5000 字）**：单章单轮 LLM 抽取；V0.1 ADR 评估 T1 预切片语义段方案；
6. **抽象化类型编号 `abstract_map`**：T2 未强制输出，T3 由 prompt B 自行推断；推断失败退化"宽松抽象"。

## 消费侧

参考 `docs/reference-canon/reference-canon-v0.md` §4（消费侧契约）。本 Sprint 不实现消费侧
（Director / Planner / Hook Ledger 接入），仅做 workflow 后端 + API 落库 + 列表/详情/删除端点。