# core.skill（内部消费外部 skill）

> 职责：**内部消费外部 skill** —— 一份「外部可移植的通用手艺」（怎么写对白潜台词 /
> 怎么埋悬念 / 怎么写章末钩子 …）作为**跨作品资产**进入软件层，按项目绑定后注入
> 章节生产链路的各环节。
> 状态：v1.0.0（2026-10-20 批次十三）。**对外**可分发的 skill 包见仓库根
> `skills/novelos/`（那是「用自然语言驱动 NovelOS」；**本模块**是「让 NovelOS
> 消费别人的手艺文档」——两件事，别混）。

## 它是什么 / 不是什么

**是**：机制层的一个**新资源类型**（与 `genre_pack` 同级），承载「怎么做」的手艺说明 +
可选参考材料 + 可选检查项。

**不是**：

- **不是**题材公约（那是 `genre_pack`：题材配比 / 结构模板 / 核销口径）；
- **不是**可执行代码（schema **刻意不提供** `scripts` 字段 ⇒ 执行外部代码 ⇒ 安全红线
  + 三仓分离红线）；
- **不是**「项目设定 / 人物 / 剧情」的搬运通道（那些属内容仓编辑面与项目级设定）；
- **不是**指标载体（schema **刻意不提供** `ratio` / `threshold` / `score` / `weight` /
  `target_ratio` ⇒ AGENTS F-19 红线：可测算子只作体检、不作处方；一旦进 skill 就
  会被模型当成指标去凑）。

## 三条硬边界

1. **独立资源、独立 schema 版本线**
   `skills` 自有表 + 自有 payload schema（`docs/state-model/schemas/skill.schema.json`，
   版本线 `^skill\.v1\.[0-9]+\.[0-9]+$`，`additionalProperties:false`）。
   **不复用** `genre_packs.payload_json`、**不复用** `projects.genre_pack_id`
   （「一列不得两用」：策展面与生成面必须分列）。
2. **项目级绑定用关联表而非 JSON 列**
   `project_skills` 是 (project_id, skill_id) 关联表 + `ord` 决定注入顺序。理由：
   ① 一个项目可绑**多个** skill（多 slot 有序），JSON 列做不到有序多值；
   ② 避免「JSON 列里一条脏数据 ⇒ 读侧退空值 ⇒ 缓存键形态与主路径不同形 ⇒ 脏命中」
   这一族缺陷（本仓坑区「一行脏 JSON 让整个列表 500」与「漏键=脏命中」）。
3. **软件层只承载「运行面 payload」**
   SKILL.md 全文与 references 全文**属内容仓**（NovelOS-Content），软件仓不解析、
   不入库。`source_path` 只是审计用字符串。

## 数据模型（迁移 `0032_skills.sql`）

```sql
CREATE TABLE skills (
    skill_id      TEXT PRIMARY KEY,      -- skl_<12hex>
    name          TEXT NOT NULL,
    description   TEXT,
    version       INTEGER NOT NULL DEFAULT 1,   -- payload 版本；PUT 改 payload 时自增
    payload_json  TEXT NOT NULL,
    source_path   TEXT,                  -- 内容仓来源路径（审计用，软件层不解析）
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE project_skills (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    skill_id   TEXT NOT NULL REFERENCES skills(skill_id)     ON DELETE CASCADE,
    ord        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, skill_id)
);
```

**`version` 是缓存键的指纹维度**（漏键 = 脏命中，硬规则 2）：改了 payload ⇒ version
自增 ⇒ `skills_fp` 变 ⇒ 装配重算。**只按 id 算指纹会脏命中。**

## payload 形状

```json
{
  "schema_version": "skill.v1.0.0",
  "name": "对白潜台词写法",
  "description": "一句话描述（可空）",
  "instructions": "手艺本体：可移植的通用做法（≤20000 字符）",
  "applies_to": ["writer", "critic"],
  "references": [ { "path": "…", "title": "…", "body": "…" } ],
  "checks":     [ { "check_id": "…", "description": "…", "keywords": ["…"] } ]
}
```

- **`applies_to` 缺席 = 不限定范围 = 全部环节适用**。刻意如此：否则「校验通过但永不生效」
  的死资源会成为常态（那是本仓反复登记的「数据源存在 ≠ 已进装配」形状）。
- **`checks` 是「存在/不存在」型判定，不是占比型指标** ⇒ 恒 warning、只落
  `review_report.warnings[]`（**旁路通道**），**绝不**接入 `scan_ai_patterns`
  （那会自动进 `quality_reports` 与提交门禁）。

## 注入：段形态与 5 个消费环节

注入段是**独立新段**（不塞任何既有段）：

```json
"skills": {
  "items": [ { "skill_id": "…", "name": "…", "version": 1,
               "instructions": "…", "references": [ … ], "checks": [ … ] } ],
  "__skills_truncated__": true      // 可选；true = 段内容被字符预算截断
}
```

| consumer | 落点 | 备注 |
|---|---|---|
| director | `context_engine/director_input.py` | 键 **14 → 15** 元 |
| scene_planner（独立节点） | `workflows/chapter_write/scene_planner.py` | `applies_to` 逐环节过滤 |
| scene_planner（director_planner 合并体） | `workflows/chapter_plan/planner_input.py` | 按**合并角色** `("director","scene_planner")` 重算 |
| writer | `context_engine/writer_input.py` | 键 **17 → 18** 元 |
| critic / deep_reviewer | `workflows/chapter_review/pipeline.py` | 经 `review_inputs["skills"]` 下传 |

**缺席 ⇒ 键不出现 ⇒ 零行为变化**（未绑定任何 skill 的项目与升级前逐字一致）。

## 缓存键：`skills_fp`

```
sha256(json.dumps([{"skill_id": sid, "version": ver} … 按 skill_id 排序], sort_keys=True))[:16]
```

- **含 version** ⇒ 改 payload 必 miss；
- **顺序无关** ⇒ 主 JOIN（`group_concat`）与降级路径（逐项读）即便聚合顺序不同也同值
  （硬规则 2「两条路径的键形态必须逐字同形」的落地方式）；
- 未绑定 ⇒ `"__none__"`（与 `genre_pack_ref` 的未绑定占位**同形**）；
- 串形态非法 ⇒ `"uncached"`（**绝不**静默降级成某个稳定值——那正是脏命中）。

⚠️ **如实登记的边界**：绑定**顺序**（`project_skills.ord`）**不进**指纹——两个 skill 的
相对顺序对被注入方无语义（各自独立的通用手艺，不存在「后者覆盖前者」）。
若将来引入「有序 skill 栈」（后一条覆盖前一条的写法），**必须**把 `ord` 进指纹。

## 死参数防线：提示词必须声明 `skills`

本仓的提示词是「一文件一整段」，**没有模板引擎、没有片段位** ⇒ 往 payload 加 `skills`
键**必须**有提示词声明它，否则模型看不见（与 `author_intent` 事故同形）。

判别由 `tests/unit/test_skill_prompt_contract.py` 承担，判别面**分两层**：

- `SKILL_CONSUMER_AGENTS` = 5 个**角色**（writer / director / scene_planner / critic /
  deep_reviewer）；
- `SKILL_MERGED_CALL_SITES` = **合并体调用点**（director_planner）——⚠️ 它**不在**角色
  名册里，**但它是活路径**（chapter-plan 调的就是它）⇒ 断言遍历二者之**并集**。

## 模块结构

| 文件 | 职责 |
|---|---|
| `model.py` | `SkillPayload` / `SkillCreate` / `SkillUpdate` / `SkillSummary` / `Skill`；`SKILL_SCHEMA_VERSION` + `SKILL_SCHEMA_PATH`；`parse_payload` |
| `queries.py` | 只读行投影（`skill_row_to_dict` / `skill_row_to_summary`），JSON 列容错解码 |
| `service.py` | `SkillService`：create / list / get / update / delete / bind / unbind / list_project_skills / count_bindings；`validate_payload`（错误串 `[schema] <path>: <message>`） |
| `consumers.py` | 装配侧：`skill_applies_to` / `skill_block` / `build_skills_segment`（字符预算 + 截断标记）/ `collect_skill_checks` / `skill_check_hits` |
| `../api/routers/skill.py` | 8 条 REST 端点（`response_model` 全声明） |

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/skills` | 建（201）；payload 不合 schema ⇒ 422 |
| GET | `/api/skills?name=` | 列（摘要形态） |
| GET | `/api/skills/{skill_id}` | 取详情 |
| PUT | `/api/skills/{skill_id}` | 改；**payload 变更 ⇒ version 自增**，只改元信息 ⇒ 不升版本 |
| DELETE | `/api/skills/{skill_id}` | 删（204）；**仍被项目绑定 ⇒ 409**（先解绑） |
| GET | `/api/projects/{project_id}/skills` | 列该项目绑定的 skill（有序） |
| POST | `/api/projects/{project_id}/skills/bind` | 绑定（多 slot 有序，**覆盖式**去重） |
| POST | `/api/projects/{project_id}/skills/unbind` | 解绑（**幂等**） |

## 相关测试

- `tests/unit/test_skill_schema.py`（13 例）——合法/非法 payload、顶层 `version` 被拒、
  ratio/threshold/score/weight 与 scripts / prompt 被拒、`applies_to` 枚举、错误串形态；
- `tests/unit/test_skill_service.py`（13 例）——version 自增规则、多 slot 有序绑定、
  失败不留半套绑定、解绑幂等、删 skill 级联；
- `tests/unit/test_skill_injection.py`（20 例）——独立段、缺席零注入、逐 consumer 过滤、
  字符预算与截断标记、**键维度元数**、改 payload ⇒ 重装、解绑 ⇒ 重装、checks 只进
  warnings、`SKILL-*` 不在任何后果表、缺表降级 / 锁上抛；
- `tests/unit/test_skill_prompt_contract.py`（4 例）——**死参数守卫**（见上）；
- `tests/api/test_skill_api.py`（17 例）——8 端点 + 404/409/422 + 键集基准；
- 突变规格 `tests/mutation_specs/l7_skill_*.py`（7 条）+ `l10_skill_merged_prompt.py`（2 条）。
