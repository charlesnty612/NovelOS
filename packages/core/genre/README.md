# core.genre（题材包 / genre_pack）

> 职责：专项题材库（跨作品聚合的题材公约资产）的软件层——题材包 CRUD、payload
> 校验（自有 schema 版本线 v1.1.0）、项目级绑定、director / scene_planner / writer
> 三 consumer 的注入段、**核销层 v1**（配比偏差 + 节奏红线，report-only）、以及
> **P2 读侧投影**（signing_check 题材开篇检查段 / critic + deep_review 的
> genre_rubric / basic_checks 题材禁词扫描）。
> 状态：题材库 **P1a + P1b + P2** 落地（2026-09-13）。
> 上游裁决：`docs/roadmap/题材库-评估与落地计划-2026-09-13.md` §三/§四/§五。

## 职责与边界

**做**：
- `genre_packs` 表（题材包结构化 payload + 版本号）的 CRUD；
- `projects.genre_pack_id` 单 slot 绑定 / 解绑 / 绑定读取；
- payload 校验：`docs/state-model/schemas/genre-pack.schema.json`（Draft 2020-12）是唯一权威，
  错误串口径与 canon / state-delta 校验器一致；
- 摘要投影（爽点条数 / 结构模型 / 章目标字数 / 被绑定项目数）供列表端点消费；
- 注入段的取数（director 结构模板摘要 + 爽点清单 + pacing；scene_planner 爽点摘要 +
  配比声明 + 配比指令；writer 题材体例）——取数函数
  `packages/core/context_engine/builders_common._genre_pack_excerpt`，本模块只提供行数据；
- **核销层 v1**（P1b）：`packages/core/genre/verifier.py` 的
  :func:`verify_chapter` —— 配比偏差 + 节奏红线两类规定性约束的写后核销，
  产出 `GENRE-` 前缀 issue（恒 `warning`，report-only **不阻断**）；
  挂点：`chapter_review` 的 `basic_checks` → `review_report.genre_check`；
- **读侧投影**（P2）：`packages/core/genre/consumers.py` —— 纯函数、无 IO：
  - `opening_rules` → signing_check 的 `genre_opening` 检查段（黄金三章特化规则，
    失败为 info 级提示）；
  - `critic_rubric` → chapter_review 的 critic / deep_review payload 的 `genre_rubric`
    段（payoff_focus 文本化 + taboo_notes + style_notes，总预算 1000 字符，超限截断带
    `__genre_rubric_truncated__`）；
  - `forbidden_words` / `count_forbidden_word_hits` → basic_checks 的题材禁词确定性
    扫描（命中进 `review_report.warnings`：`[GENRE-FORBIDDEN-WORD] <词> ×N`，warning 级）。

**不做**：
- 不写题材正文/审核禁忌（**红线**：题材内容只进内容仓 `NovelOS-Content/genres/<题材>/`；
  本模块只承载结构化消费子集，软件仓 git diff 不含题材正文）；
- 不产 error / 不进质量门禁阻断白名单（`packages.core.quality.issues.BLOCKING_RULES`
  不含 `GENRE-` 规则；核销 issue 与题材开篇检查段只作 informational 通道）；
- 不做题材包 → 项目预设的确定性联动（照 canon→风格卡的零 LLM 模板法）→ 仍未做（P2 未含）；
- ~~不做内容仓同步命令~~（V3.9 P3 已落地：`scripts/content_sync.py genre-push <pack_id>` /
  `genre-pull <slug或路径>`，见 `packages/core/content_sync/README.md`——10 维文件=人编辑面，
  pack.json=运行面，双轨不做自动聚合）。

**内容不变量**：内容仓为**编辑面**（题材正文 / 禁忌清单 / 策展源文件），pack payload 为
**运行面**（机制可消费的结构化子集）；新增消费维度走 **minor 版本**（v1 线内只加可选段，
旧 v1.0.0 payload 继续有效）——这保证内容仓维护节奏与软件仓 schema 演进解耦，
且软件仓永远不会因为「题材需要新维度」而不得不携带题材正文。

## 与 reference_canons 的边界（双 slot）

| 维度 | `reference_canons`（拆书参照系） | `genre_packs`（题材库） |
|---|---|---|
| 性质 | 单部参照作品的**描述性**拆解产物 | 多部爆款 + 策展的**规定性**公约 |
| 归属 | 项目级（`project_id` 非空，一条 canon 属于一个项目） | 跨作品资源（无 project_id）；项目经 `projects.genre_pack_id` 绑定 |
| 生命周期 | deconstruct 工作流产出 → active/archived | CRUD（PUT 改 payload → version 自增）；被绑定时删除 409 |
| schema | `docs/reference-canon/schemas/reference-canon.schema.json` | `docs/state-model/schemas/genre-pack.schema.json`（v1.1.0） |
| 消费 | director / scene_planner / writer 三 consumer 各吃自己的字段 | 三 consumer 均已落地：director（结构模板 / 爽点清单 / pacing）、scene_planner（爽点摘要 + 配比声明 + 配比指令）、writer（题材体例 → `style_constraints.genre_style`） |
| 共存 | **并存**：canon 是叠加参照，题材包是项目常驻；两者互不覆盖、各自独立缺席 | 同左 |
| 核销 | （无）canon 只作为参照注入 | **题材包有核销层**：`verify_chapter` 逐章对账配比 / 字数带 / 红线，产出 `GENRE-` issue（report-only） |

共享的只有「上下文注入管道」这一实现模式（consumer 裁剪 + 审计键），不共享表 / schema /
校验器——这是刻意的：拆书链路与题材链路耦合会互相掣肘（schema 收紧、聚合器契约、G-sim 校验）。

## 对外接口

```python
from packages.core.genre import (
    GenrePackService, GenrePackCreate, GenrePackUpdate, validate_payload,
    GENRE_PACK_SCHEMA_VERSION, GENRE_PACK_SCHEMA_PATH,
    verify_chapter, GenreCheckResult, GenreIssue, RATIO_DEVIATION_THRESHOLD,
    # P2 读侧投影（纯函数；消费方各自读绑定）
    opening_rules, critic_rubric, forbidden_words, count_forbidden_word_hits,
    CRITIC_RUBRIC_MAX_CHARS, CRITIC_RUBRIC_TRUNCATED_KEY,
)

svc = GenrePackService(db_path)

# 1) payload 校验（jsonschema 权威；空列表 = 通过）
errors: list[str] = validate_payload(payload)      # ["[schema] payoff_types/0/type_id: ..."]

# 2) CRUD
pack:  dict  = svc.create(GenrePackCreate(name="男主快穿", genre_tag="快穿", payload=...,
                                          pack_id="genre-male-quicktrans-v1",
                                          source_path="genres/male-quicktrans"))
packs: list[dict] = svc.list_packs(genre_tag="快穿")   # 摘要（无 payload 全文）
pack:  dict | None = svc.get(pack_id)
pack:  dict | None = svc.update(pack_id, GenrePackUpdate(payload=...))  # 提供 payload → version+1
ok:    bool = svc.delete(pack_id)                  # 调用方先 count_bindings 判 409

# 3) 绑定（单 slot；重复绑定=覆盖）
code, binding = svc.bind(project_id, pack_id)      # code ∈ ok / project_not_found / pack_not_found
code, binding = svc.unbind(project_id)             # code ∈ ok / project_not_found / not_bound
binding: dict | None = svc.get_project_binding(project_id)
# → {project_id, pack_id, bound, pack: 题材包全文 | None}
n: int = svc.count_bindings(pack_id)               # DELETE 409 判定口径

# 4) 核销层 v1（report-only；未绑定 → bound=False 零 issue）
from packages.core.genre import verify_chapter
result: GenreCheckResult = verify_chapter(db_path, chapter_id)   # 读项目绑定 pack
result = verify_chapter(db_path, chapter_id, pack_row_or_payload)  # 或显式传入
result.to_dict()      # → review_report["genre_check"] 落点形态
result.issues         # [GenreIssue(rule_id="GENRE-…", severity="warning", …)]

# 5) 读侧投影（P2；纯函数、无 IO）
rules:  list[dict] = opening_rules(payload)        # signing_check 题材开篇检查段
rubric: dict | None = critic_rubric(payload)       # critic / deep_review 的 genre_rubric
words:  list[str]  = forbidden_words(payload)      # basic_checks 禁词词表
hits:   list[tuple[str, int]] = count_forbidden_word_hits(prose, words)
```

REST 端点（`packages/core/api/routers/genre.py`，`discover_routers` 自动挂 `/api`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{pid}/genre-packs` | 创建（201）；payload 不合 schema → 422（`detail.errors`） |
| GET | `/api/genre-packs` | 列表摘要（`?genre_tag=` 精确过滤） |
| GET | `/api/genre-packs/{pack_id}` | 详情（payload 已解析） |
| PUT | `/api/genre-packs/{pack_id}` | 更新；提供 payload → version 自增 |
| DELETE | `/api/genre-packs/{pack_id}` | 204；被项目绑定 → 409 |
| GET | `/api/projects/{pid}/genre-pack` | 当前绑定（含 pack 全文） |
| POST | `/api/projects/{pid}/genre-pack/bind` | 绑定（覆盖式） |
| POST | `/api/projects/{pid}/genre-pack/unbind` | 解绑（幂等 200） |

## payload 结构（消费子集）

schema 版本线 **v1.1.0**（`genre-pack.v1.x.y` 主版本 v1 线内 minor / patch 兼容；
v1.1.0 相对 v1.0.0 只加 `opening_rules` / `critic_rubric` 两个可选段，旧 payload 继续有效）。
顶层键白名单 = 消费子集；其余维度的内容留在内容仓，不入 payload：

| 键 | 必填 | 说明 |
|---|---|---|
| `schema_version` | ✅ | 版本锚点（`^genre-pack\.v1\.\d+\.\d+$`） |
| `payoff_types[]` | ❌ | 爽点类型：`type_id` / `name` 必填；`strength`（S/M/s）/ `density_cap` / `min_interval_chapters` / `fatigue_risk` / `verify_hint` / `mapped_tropes` / `source` / `stale` 可选 |
| `structure_templates` | ❌ | `structure_model` / `full_book_skeleton` / `arc_beat_template[]` / `mainline_suspense` / `volume_count_expectation` |
| `pacing` | ❌ | `chapter_words` / `chapter_word_band` / `density_rules[]` / `arc_words` / `book_words` / `redlines[]` |
| `ratio_declarations` | ❌ | 配比声明：键=维度，值 ∈ [0,1]（scene_planner 配比指令 + 核销层配比偏差检查的输入） |
| `style_constraints` | ❌ | 题材体例（writer 消费：合并为 `style_constraints.genre_style`；`forbidden_words` 另供 basic_checks 禁词扫描） |
| `opening_rules[]` | ❌ | **P2 新增**：黄金三章题材特化规则——`check_id` / `requirement` 必填；`description` / `chapter_no`（1~3，缺席=1~3 章整体窗口）可选 |
| `critic_rubric` | ❌ | **P2 新增**：critic / deep_review 的题材审查要点——`payoff_focus[]`（引用 `payoff_types[].type_id`）/ `taboo_notes` / `style_notes` |

**缺席字段语义宽容**：未出现的段 = 该题材包不声明该维度，消费方跳过（不注入、不报错）。

## 注入口径（三 consumer）

项目绑定题材包时，各 consumer 只吃自己声明的段 + 各自拿到 `_genre_pack_consumed`
溯源审计（与 `_reference_canon_consumed` 同款）；**未绑定 / 对应段未声明 → 该 consumer
零注入**（既有装配行为逐字段不变）。

### director（P1a）

director payload 增加 `genre_pack` 段（含 `pack_id` / `name` / `genre_tag` / `version`）：

- `structure_templates`：结构模板摘要（阶段骨架 / 节拍模板 ≤12 条 / 卷数期望 / 主线悬念）；
- `payoff_types`：爽点类型清单，**密度约束文本化**（`density_constraint` =
  「密度上限 <原文>；同型最小间隔 N 章」）+ `fatigue_risk` / `verify_hint`；条数上限 40；
- `pacing`：节奏摘要，JSON 字符预算 1200（超限按优先级保留：章字数带 / 章字数 / 密度规则 /
  红线 / 单元预算 / 全书预算）；
- 超限裁剪处打 `__*_truncated__` 标记（非注入侧契约，前端/调用方不看）。

### scene_planner（P1b）

scene_planner payload 增加 `genre_pack` 段（`_collect_scene_planner_inputs`，见
`packages/workflows/chapter_write/pipeline.py`）：

- `payoff_types`：规划期爽点摘要——逐条 `type_id` / `name` / `strength` / `density_cap`
  （原文）/ `min_interval_chapters`（整数）+ 文本化 `density_constraint`；条数上限 40，
  JSON 字符预算 **1500**，被预算 / 条数砍条 → `__genre_pack_truncated__ = true`
  （P1b 口径标记名，与 director 的 `__payoff_types_truncated__` 并存）；
- `ratio_declarations`：配比声明原样透传（若声明）；
- `ratio_instruction`：由声明派生的**配比指令文本**——
  「题材配比声明：action=70%、transition=30%。逐 scene 标注 scene_type（取值与配比维度
  一致），并为每个 scene 给出 target_words（整数，总和≈本章目标字数；各 scene 字数占比
  遵守上述配比）。」（声明份额之和 ≠ 1 时附「按份额相对比例理解」注记）

scene 的 `scene_type` 是**可选字段**（不强制——缺席不报错；核销层缺席时跳过配比项）。

### writer（P1b）

题材 `style_constraints` 段**合并**进 writer payload 既有 `style_constraints` 的
`genre_style` 子键（`writer_input._merge_genre_style`）：

- **不覆盖**既有键（语言 / 视角 / 禁用词等默认值与项目配置保持原值）；
- 既有 `genre_style` 键（调用方显式配置）→ 不合并（显式配置优先）；
- 合并段自带溯源：`pack_id` / `version` / `source = "<pack_id>@<version>"`；
- 既有 `author_style_samples`（作者本人样例）非空 → 合并段带
  `priority = "below_author_style_samples"`（**作者样例优先**，题材体例只约束下限）；
- 题材包未声明 `style_constraints` → 整段不注入（不往 style_constraints 塞空壳）。

**缓存键**：director 与 writer 两个装配缓存键都增加题材包指纹维度
`genre_pack_ref = "<pack_id>@<version>"`（未绑定 → `"__none__"`，键尾命名空间标记仍在最后）。
换题材包 / 换版本（PUT 改 payload → version 自增）/ 绑定 / 解绑都必须 miss；漏该维度会导致
换包后读回旧题材包内容（V3.9 批次 1B「漏键=脏命中」教训）。指纹口径单点定义在
`builders_common._PEEK_CHAPTER_SQL`（内联子查询 `pack_id || '@' || version`），降级路径
`_try_project_genre_pack_ref` 与之逐字同形——两路径不同形同样会脏命中；writer 键复用同一
`_peek_chapter_context` 结果（`writer_input.build_writer_input`），故天然同形。

**scene_type 输出契约（P2 显式化）**：配比核销的前提是 scene_planner 逐 scene 标注
`scene_type`（取值 = `ratio_declarations` 的键）——契约写在 `scene_planner-v1.md`
§6 Rule 12 / §9 E-SPL-10（缺失则核销层按「跳过」处理，配比无从对账）。

## 读侧投影（P2）

`packages/core/genre/consumers.py` —— 纯函数、无 IO、全容错（脏数据跳过不抛错）；
消费方各自读绑定（signing_check 经 `GenrePackService.get_project_binding`，
chapter_review 经同一接口单次读取后复用），本模块只做「payload → 消费形态」的投影。
三个消费点：

| 消费点 | 投影 | 挂点 / 落点 |
|---|---|---|
| signing_check 题材开篇检查段 | `opening_rules(payload)` | `run_signing_check` → `result["genre_opening"]` + 逐条 CheckItem（key = `genre_opening_<check_id>`） |
| critic / deep_review 题材 rubric | `critic_rubric(payload)`（≤1000 字符预算） | `review_inputs["genre_rubric"]` → 两 AI 节点 payload 的 `genre_rubric` 键 |
| basic_checks 题材禁词扫描 | `forbidden_words(payload)` + `count_forbidden_word_hits(text, words)` | `review_report.warnings` 的 `[GENRE-FORBIDDEN-WORD] <词> ×N` 行 |

### signing_check 题材开篇检查段

- **触发**：项目绑定题材包且 payload 声明非空 `opening_rules`（未绑定 / 未声明 →
  `genre_opening` 键整体缺席，体检结果与题材库前逐字节一致）；
- **判定**：逐条规则定位目标章（`chapter_no` 或 1~3 章整体窗口），从
  `requirement` / `description` 抽取可机检关键词（剔章号数字 + 约束语法词后取 ≥2 字片段），
  **任一命中即 pass**（宽松口径：宁可漏报不误报）；`status ∈ pass / fail /
  unverifiable（抽不出关键词）/ not_written（目标章未写）`；
- **档位**：`pass` → `pass`；其余（含 `fail`）→ **`info`**（report-only 提示，不进
  `summary.fail_count`、不阻断签约流程）；
- 结构化段形状：`{pack_id, pack_name, rule_count, rules[], pass_count, fail_count, info_count}`，
  每条规则 `{check_id, chapter_no, description, requirement, status, detail}`。

### critic / deep_review 的 genre_rubric

- **触发**：绑定 pack 且 payload 声明 `critic_rubric`（三段至少一段非空）；缺席 → 不注入该键；
- **投影**：`payoff_focus[]` 的 `type_id` 逐条文本化为
  `<type_id>：<name>｜强度 <S/M/s>｜密度上限 <原文>｜同型间隔 N 章｜核销提示 <verify_hint>`
  （未被 `payoff_types` 声明的 id 原样保留）；`taboo_notes` / `style_notes` 原文透传；
- **预算**：整段 JSON 字符长度 ≤ 1000（`CRITIC_RUBRIC_MAX_CHARS`）；优先级
  taboo_notes > style_notes > payoff_focus 条目，文本超限按字符截断加 `…（已截断）`，
  条目超限整条不注入；任何裁剪打 `__genre_rubric_truncated__ = true`；
- **下传**：`basic_checks` **单次**读绑定投影 → `review_inputs["genre_rubric"]` →
  critic 与 deep_review 两个 AI 节点同得（与 `settings_digest` 同款共享口径；
  不重复查库，见 chapter_review V3.9 批次 5.7 的单次取数约定）。prompt 侧：
  `critic-v1.md` §3.10 / §5 / §6.13 / §9 E-CRT-10。

### basic_checks 题材禁词扫描

- **触发**：绑定 pack 且 `style_constraints.forbidden_words` 非空（未声明 → 零输出）；
- **口径**：子串计数（`str.count`，与 `quality.ai_flavor.marker_hits_per_kchars`
  同款词表机制），命中 → `review_report.warnings` 追加 `[GENRE-FORBIDDEN-WORD] <词> ×N`
  （**warning 级**：不进 `errors`、不阻断；同一章的前缀 `GENRE-` 与核销层 issue 并列）；
- **边界**：这是题材体例的确定性下限检查，与 `quality.ai_patterns` 的 AI 腔检测
  （`AI-FORBIDDEN-WORD`）通道独立、可同时命中（两套词表来源不同：题材包 vs 仓内建）。

## 核销层 v1（P1b，report-only）

`packages/core/genre/verifier.py`：

```python
from packages.core.genre import verify_chapter, GenreCheckResult, GenreIssue

result = verify_chapter(db_path, chapter_id)          # 读绑定题材包
result = verify_chapter(db_path, chapter_id, pack)     # 显式 pack（行 dict / 裸 payload）
result.to_dict()   # → review_report["genre_check"] 落点形态
```

- 数据来源（全只读、全容错）：`chapters.plan_json`（deviations）、最新 draft、
  最近一条 chapter-write run 的 `checkpoint_json`（`scene_plan` + `length_report`）、
  项目绑定题材包；
- 检查 a **配比偏差**：scene_plan 中带 `scene_type` + `target_words` 的 scene 按字数
  分摊换算实际配比，与 `ratio_declarations`（按总和归一化）比对；
  **总偏离 > 10%**（`RATIO_DEVIATION_THRESHOLD`）→ `GENRE-RATIO-DEVIATION`；
- 检查 b **红线**：
  - `pacing.chapter_word_band`（low/high）与实际字数（优先 `length_report.visible_chars`，
    否则最新草稿 `visible_chars`）比对，出带 → `GENRE-WORD-BAND-DEVIATION`；
  - `pacing.redlines[]` 文本命中该章 `plan_json.deviations` / `length_report` →
    `GENRE-REDLINE-HIT`（v1 保守口径：整条红线归一后子串匹配；语义判定留 P2）；
- issue 恒 `severity="warning"`、`rule_id` 前缀 `GENRE-`、`category ∈ {payoff, pacing}`
  （quality `Category` 合法值），`to_quality_issue()` 产出与 quality `Issue` 同形 dict；
- **不阻断**：`BLOCKING_RULES` 不含任何 `GENRE-` 规则（白名单不许动），
  `is_blocking_issue` 对其恒为 False，不写 `quality_reports`、不参与评分；
- 挂点：`chapter_review._basic_checks_node` → `review_report.genre_check`
  （`bound=false` 时**整段缺席**，零行为变化），issue 另以 `[GENRE-…] <message>`
  文本行进 `review_report.warnings`（评审 UI 可读通道）。

## 失效模式（读侧全容错，写侧严格）

| 触发 | 行为 |
|---|---|
| payload 不合 schema（写路径 POST/PUT） | 422，`detail.errors` 逐条列出 `[schema] <path>: <message>` |
| payload_json 非法（旁路写入 / 手工改库） | 读侧 payload 解析失败 → 空 dict；director / scene_planner 注入段整段缺席（不抛错、不阻断装配），writer 侧零注入 |
| `genre_pack_id` 指向不存在的 pack（DDL 层被外键拦住，理论上不可达） | `_genre_pack_excerpt` 的 JOIN 取不到行 → 零注入；`get_project_binding` 返回 `bound=true, pack=null` |
| 极老库缺 `genre_packs` 表 / `projects.genre_pack_id` 列（0025 未跑） | peek / 注入的 `sqlite3.OperationalError` 被吞掉 → 视为未绑定（与无题材包行为零差异）；缓存键维度取 `__none__`；核销 `bound=false` |
| 删除被绑定的 pack | 409（`count_bindings > 0` 前置拦截）；DDL 侧外键 `ON DELETE SET NULL` 是旁路删除的兜底 |
| pack_id 重复 | `sqlite3.IntegrityError` → 422 |
| 核销时缺 scene_plan / 无 scene_type / 无 target_words | 该项跳过并在 `genre_check.skipped` 记录原因（`no_scene_plan` / `no_scene_type` / `no_scene_target_words` / `ratio_declarations_not_declared` …），零 issue |
| 核销读路径整体失败 | `GenreCheckResult(error=...)`，不抛错；chapter_review 挂点再兜一层异常 → 无 `genre_check` 段，评审照常 |
| P2 读侧投影遇 `opening_rules` / `critic_rubric` / `style_constraints` 脏数据 | `consumers` 逐条跳过非法项；三段全空 / 段缺席 → 消费点整段缺席（signing_check 无 `genre_opening` 键、critic payload 无 `genre_rubric` 键、warnings 无 `GENRE-FORBIDDEN-WORD` 行），不抛错 |
| `critic_rubric` 超 1000 字符预算 | 截断（文本加 `…（已截断）` / 条目整条丢弃）并打 `__genre_rubric_truncated__ = true`；prompt 侧要求只按已给出条目审查 |
| 题材开篇规则抽不出可机检关键词 / 目标章未写 | 该条 `status = unverifiable / not_written` → info 级 item + 结构化 status；不算 fail、不进 `summary.fail_count` |

## 依赖

- 上游：`packages.core.db.get_connection`、`packages.core.ids.new_id/now_iso`、`jsonschema`、
  `packages.core.quality.wordcount.visible_chars`（核销字数口径）。
- 表 schema：迁移 `database/migrations/0025_genre_packs.sql`
  （`genre_packs(pack_id PK, name, genre_tag, version, payload_json, source_path,
  created_at, updated_at)` + `projects.genre_pack_id` 可空列，外键 SET NULL）。
- 消费方：`packages/core/context_engine/director_input.py`（注入段 + 缓存键）、
  `packages/core/context_engine/writer_input.py`（题材体例合并 + 缓存键）、
  `packages/workflows/chapter_write/pipeline.py`（scene_planner 注入）、
  `packages/workflows/chapter_review/pipeline.py`（genre_check 挂点 + P2 禁词扫描 +
  genre_rubric 下传）、
  `packages/core/signing_check/`（P2 题材开篇检查段）、
  `packages/core/context_engine/preview.py`（L1 状态行）、
  `packages/core/api/routers/genre.py`（REST）、
  `packages/domain/project/`（Project 响应暴露 `genre_pack_id`）。

