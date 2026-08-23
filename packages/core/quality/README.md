# core.quality — Quality Engine 纯核心

> 职责：实现 `docs/evaluation/quality-scoring-v0.md` 的 Quality Score 计算与 Guardrail 检查，
> 提供纯函数式评估能力（无 DB、无 pipeline 集成）。
> Sprint 6 上半产出；DB / pipeline 接入由 Sprint 6 下半集成任务在本包之上完成。

---

## 1. 模块结构

```
packages/core/quality/
├── __init__.py          # 公共 API 导出
├── README.md            # 本文件
├── models.py            # pydantic：Issue / QualityContext / QualityReport
├── issues.py            # Issue 构造器 / severity 矩阵常量
├── aggregate.py         # §2.1 公式 compute_overall + scoring_formula_hash
├── scoring.py           # 六子分 rule-based
├── guardrails.py        # 8 条 Guardrail（spec §4.1-§4.8）
├── payoff.py            # 爽感 H-1~H-5（spec §3.7）
└── engine.py            # QualityEngine.evaluate(ctx) -> QualityReport
```

---

## 2. 公共 API（任务书与 `__init__.py` 对齐）

```python
from packages.core.quality import (
    QualityEngine,          # 主入口
    QualityContext,         # 评估输入
    QualityReport,          # 评估输出（含 _meta）
    Issue,                  # 单条问题
    compute_overall,        # §2.1 公式（直接调用，便于测试）
    MVP_SEVERITY_MATRIX,    # severity 矩阵常量（自检/文档用）
    guardrails, scoring,    # 子模块（内部 helper）
)
```

`QualityEngine.evaluate(ctx: QualityContext) -> QualityReport`：

- 纯函数，无副作用。
- 编排顺序固定：guardrails → compliance → payoff → 6 子分（continuity 复用已收集 issues）→ 聚合 → 报告。
- 报告 `_meta` 字段固定 4 键：`scoring_version="quality-scoring-v0"` / `llm_judge="deferred"` / `evaluated_at=now_iso()` / `scoring_formula_hash=<§2.1 公式 sha256 前 16 位>`。

---

## 3. 输入与输出

**输入 `QualityContext`**（pydantic）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chapter_id` | str | 是 | 章节 ID（如 `ch_<12hex>`）。 |
| `chapter_number` | int | 是 | 章节序号（1-based）；H-4 黄金三章专用。 |
| `draft` | str | 否 | 章节正文。 |
| `plan` | dict | 否 | `chapter_plan` 形态；plot 子分读 `key_beats[]`。 |
| `snapshot_pre` | dict | 否 | 提交前 Canonical State dict。 |
| `delta` | dict | 否 | Observer Delta dict（顶 7 数组 + 元信息）。 |
| `payoff_history` | list[int] | 否 | 最近 N 章每章 payoff 计数。 |
| `reference_texts` | list[str] | 否 | REQ-Q6 参照书。 |
| `whitelist` | list[str] | 否 | REQ-Q6 公共 shingle 白名单。 |
| `ai_chars` / `human_chars` | int | 否 | REQ-Q8 字符数。 |
| `commit_id` / `run_id` | str \| None | 否 | 仅做回显。 |

**输出 `QualityReport`**（pydantic）：八字段 + `_meta` + 回显字段；`_meta` 使用 JSON 别名 `_meta`（Python 属性名 `meta`）。

---

## 4. Issue 与 severity 矩阵

### 4.1 Issue 字段（spec §1.3）

```python
class Issue(BaseModel):
    severity: Literal["error","warning","info"]
    category: Literal[...]  # 13 个值，与 guardrail / 子分 / 爽感一一对齐
    location: str            # "<chapter_id>" 或 "<chapter_id>:<scene_id>"，缺则 "<unknown>"
    rule_id: str             # "RULE_<NAME>" 或特殊：SCHEMA_VALIDATION_FAILED / scoring_missing_subscore
    message: str             # 人类可读一句话
    suggestion: Optional[str]
    evidence_refs: Optional[list[str]]
    judge_trace: Optional[dict]
```

13 个 `category`：`schema_validity / timeline_consistency / character_contradiction / world_rule_contradiction / knowledge_leakage / plot / character / continuity / style / pacing / foreshadowing / payoff / compliance`。

### 4.2 MVP severity 矩阵（主会话拍板口径）

| category | MVP 最高 | 实现来源 |
|---|---|---|
| schema_validity | **error** | spec §4.1 |
| character_contradiction | **error** | spec §4.3 |
| world_rule_contradiction | **error** | spec §4.4 |
| compliance（REQ-Q6 / Q7 / Q8） | **error**（仅 Q6 / Q8 error；Q7 仅 warning） | spec §4.6/4.7/4.8 |
| timeline_consistency | warning | spec §4.2，MVP 收窄 |
| knowledge_leakage | warning | spec §4.5，MVP 收窄 |
| 其余子分（plot/character/continuity/style/pacing/foreshadowing） | warning | spec §3.1-§3.6 |
| payoff（H-1~H-5） | warning（H-3 连续 ≥3 章 / H-5 越级可升 error） | spec §3.7 |

矩阵常量导出为 `from packages.core.quality import MVP_SEVERITY_MATRIX` 与 `mvp_max_severity(cat)`。

**升级到 error**：把规格行的 severity 改为 `error` 即可；M0→V1 升级示例见 §6。

---

## 5. Guardrail 8 条与子分 6 维的实现要点

### 5.1 Guardrail 实现要点

| Guardrail | 关键判定 | severity |
|---|---|---|
| `schema_validity(delta)` | 复用 `packages.core.story_state.validator.validate_delta`；首条错误⇒error。 | error |
| `character_contradiction(snapshot, delta)` | dead 角色被 set active 字段 ⇒ RULE_CHAR_DEAD_ACTIVE error；`before` 与 snapshot 不一致 ⇒ RULE_CHAR_BEFORE_MISMATCH warning。 | error + warning |
| `world_rule_contradiction(snapshot, delta)` | hard rule（`data_json.hard==true`）被 remove / 改 statement ⇒ RULE_WORLD_HARD_RULE_CHANGED error；soft ⇒ warning。 | error / warning |
| `timeline_consistency(snapshot, delta)` | `new_events.time.timeline_day < 0` ⇒ RULE_TIMELINE_NEGATIVE_DAY warning；`< max_snapshot_timeline_day` ⇒ RULE_TIMELINE_NON_MONOTONIC warning。 | warning |
| `knowledge_leakage(snapshot, delta)` | 仅对显式声明 `who_knows` 的 event/hook 做严格校验；新 knowledge 命中受限实体描述但角色不在 who_knows ⇒ RULE_KNOWLEDGE_LEAK warning。 | warning |
| `req_q6(draft, references, whitelist)` | 13 字滑动 shingle；任意公共 ⇒ RULE_Q6_NGRAM_OVERLAP warning；总重叠字符占比 > 2% ⇒ RULE_Q6_OVERLAP_RATE error；`reference_texts` 空 ⇒ info。 | warning + error + info |
| `req_q7(draft)` | 每千字 AI marker ≥ 5 ⇒ warning；段落首词"然而/但是"占比 > 20% ⇒ warning；句长总体标准差 < 3（≥1000 字）⇒ warning。 | warning |
| `req_q8(ai_chars, human_chars)` | ratio < 30% ⇒ RULE_Q8_HUMAN_RATIO_LOW error；30%–40% ⇒ warning；ratio 缺失 ⇒ info。 | error / warning / info |

### 5.2 六子分 rule-based 实现要点

| 子分 | 关键算法 | 起点/下限 |
|---|---|---|
| plot | 100 起；key_beats 未命中 -5/条；`len(new_events) > key_beats + 2` ⇒ 每超额 -10；plan 缺 key_beats ⇒ 85 + info。 | 0 |
| character | 一致率 × 100；mismatch 与 `RULE_CHAR_BEFORE_MISMATCH` 共用判定（guardrail 已写 issue，子分不重复 push）。 | 0 |
| continuity | 100 起；按 `_CONTINUITY_DEDUCTIONS` 表扣分；同 rule_id 一章只扣一次；warning × 0.5 / error 全额；累计扣至 0 下限。 | 0 |
| style | 100 起；句均字长出 [12, 28] -10；trigram 重复率 > 8% -15 + warning；AI markers 每千字 ≥ 5 -15；对话占比出 [0.15, 0.65] -5。 | 0 |
| pacing | 100 起；5 段对话密度极差 < 0.05 -20；末段无钩子 -15；段落 < 1 不扣分。MVP 代理实现。 | 0 |
| foreshadowing | 兑现率 × 100 = resolved / (new_hooks + resolved_hooks)；涉及 0 ⇒ 85 + info。 | 0 |

### 5.3 爽感 H-1~H-5

| # | 判定 | severity |
|---|---|---|
| H-1 | 末 200 字无 HOOK_MARKERS ⇒ RULE_H1_NO_END_HOOK warning。 | warning |
| H-2 | 最近 3 章 payoff 全 0 且本章无 resolved/debt paid ⇒ RULE_H2_NO_CLIMAX_3CH warning。 | warning |
| H-3 | 连续 2 章 0 payoff ⇒ RULE_H3_FILLER_2CH warning；连续 ≥ 3 章 ⇒ RULE_H3_FILLER_3CH error。 | warning / error |
| H-4 | 仅 `chapter_number ∈ {1,2,3}` 启用：前 300 字无 CONFLICT_MARKERS ⇒ warning；末段无钩子 ⇒ warning；`chapter_number == 3` 三章 payoff 全 0 ⇒ warning。 | warning |
| H-5 | 境界类实体（name 命中"境/期/阶/层/重天"任一）跨 statement 不一致 ⇒ RULE_H5_REALM_INCONSISTENT warning；越级碾压检测 MVP 不实现。 | warning |

---

## 6. MVP 收窄与 defer 清单（**显著声明**）

以下能力在 MVP 阶段**不实现**或**降级**；V1 / Sprint 7 接入时请按本清单回填：

1. **LLM judge 全部 deferred**（spec §3.1/§3.2/§3.4/§3.5/§3.6 的 LLM 0.6 权重部分）。
   接入路径：替换 `scoring.score_*` 中的计算结果合并一份 LLM judge 分（双评取低）。
   `_meta` 字段 `llm_judge="deferred"` 需在接入后改为 `"active"` 并补充 `judge_model_versions`。
2. **REQ-Q6 embedding 双轨**（spec §4.6）未实现；仅 13 字滑动 shingle。
   接入路径：在 `req_q6` 内追加 embedding 相似度分支（阈值 0.85 / 0.92）。
3. **H-5 越级碾压检测**（spec §3.7）未实现；只实现境界名词一致性 warning。
   接入路径：在 `payoff._h5_realm_consistency` 中追加 `opponent_layer vs protagonist_layer` 比对。
4. **§3.5 pacing 真实张力曲线**（spec §3.5）未实现；MVP 代理为 5 段对话密度极差 + 末段钩子。
   接入路径：用 Scene 级 plan 张力等级做 Pearson 相关。
5. **§4.5 knowledge_leakage V1 升级 error**；MVP 仅 warning。
   接入路径：在 `guardrails.knowledge_leakage` 把检查后的 Issue severity 由 `warning` 改为 `error`，并同步更新 `MVP_SEVERITY_MATRIX["knowledge_leakage"]["mvp_max"]`。
6. **§4.2 timeline_consistency V1 升级 error**；MVP 仅 warning。同上路径。
7. **未建模字段一律 pass**（spec §3.3 / §4.5 收窄）：连续性规则严格按已建模字段生效，未建模字段不报告；Hook Ledger 未落地前 foreshadowing 数据源回退到 `plot_event`。

---

## 7. 阈值与公式（**建议值，待校准**）

所有数字均为"建议值待校准"，**未经首批 golden 章节评测前禁止用作产品口径**：

- §2.1 权重 `plot/character/continuity = 0.20; style/pacing = 0.15; foreshadowing = 0.10`
- Q6：13 字 shingles / 2% 重叠率
- Q7：每千字 5 marker / 20% 段落首词 / 3.0 标准差 / 1000 字阈值
- Q8：30% 红线 / 40% 缓冲
- §3.3 扣分表见 `scoring._CONTINUITY_DEDUCTIONS`
- §3.4/§3.5 风格与节奏常量集中在 `scoring.py` 模块顶

**维护约定**：阈值/权重变更后必须：

1. 同步更新 `aggregate.formula_text()`（`_FORMULA_TEXT`），`_meta.scoring_formula_hash` 自动重算。
2. 在 SPEC v0 → v1 的 PR 中显式列出 baseline 偏离（避免评分漂移被遗忘）。
3. `tests/unit/quality/test_aggregate.py` 中硬编码 `87` 等断言需要更新（先调测试，再调实现，最后跑 §6 Regression）。

---

## 8. 维护注意点

1. **不要直接 import `packages.core.db` / `packages.core.api`**：本包刻意与 DB、HTTP 解耦；
   集成在 Sprint 6 下半任务（Quality Engine ↔ Workflow 衔接）里进行。
2. **不要修改 `packages/core/story_state/validator.py`**：guardrails 的 schema_validity 复用了它。
   如需扩展校验，改后端 schema 文件 + 在 `validator.py` 加规则即可。
3. **Issue dataclass 已合并为唯一 pydantic 类**：`packages.core.quality.issues.Issue` 与 `models.Issue` 是同一类。
   不要在代码里出现 `dataclass`-based Issue 占位——会让 QualityReport 序列化报错。
4. **`_meta` 用 pydantic alias**：Python 端属性名是 `meta`；JSON / dump 时通过 `model_dump(by_alias=True)` 输出 `_meta`。
5. **issue 严重性升级路径明确**：把 `_make_issue(severity="error")` 改为 `"warning"` 即降低；改 matrix 不需要改引擎逻辑。
6. **本包零数据库迁移**：所有评估均为纯函数，State Delta 字段以 dict 形态传入。
7. **新加 rule_id 必须以 `RULE_` 开头**（除 SCHEMA_VALIDATION_FAILED / scoring_missing_subscore 等系统级）。

---

## 9. 测试

- 单元测试：`tests/unit/quality/` 覆盖 8 条 Guardrail / 5 项爽感 / 6 子分 / 聚合 / engine 端到端。
- Regression：Sprint 6 集成任务后须接入 `tests/evals/golden/`（spec §5）。
- 命名约定：以 `test_<feature>_<scenario>` 命名，与 `tests/unit/test_validator.py` 对齐。

---

## 10. 关联文档

- 权威规范：`docs/evaluation/quality-scoring-v0.md`
- Knowledge Permission：`docs/state-model/knowledge-permission-v0.md`
- State Delta：`docs/state-model/state-delta-v0.md` + `docs/state-model/schemas/state-delta.schema.json`
- Story State 复用入口：`packages/core/story_state/validator.py::validate_delta`
- ID 与时间戳：`packages/core/ids.py::new_id/now_iso`
- Sprint 6 集成任务（后续）：与 `packages/workflows/chapter_review/` 衔接，由 State Committer 调用。

---

## 11. 集成方式（Sprint 6 下半落地说明）

Quality Engine 在 Sprint 6 下半完成了从「纯函数核心」到「可观测 / 门禁 / 查询」的集成。
本节列出集成位置与调用入口，给后续维护者一个全局图景。

### 11.1 落库层：``packages.core.quality.service``

- 持久化由 :mod:`packages.core.quality.service` 提供：
  - :class:`QualityService.save_report` —— 把 :class:`QualityReport` 落 ``quality_reports``（含
    六子分 + ``_meta`` + ``Issue[]``）。
  - :class:`QualityService.latest_report` / :class:`QualityService.list_reports` —— 按 chapter /
    project 查询；按 ``created_at`` 降序。
  - :func:`compute_char_stats` —— REQ-Q8 字符数统计（按 ``drafts.version`` 升序、首个版本
    ``len(content)``、后续 ``difflib.SequenceMatcher`` 算 ``replace+insert`` 增量）。
  - :func:`build_quality_context` —— 现场组装 :class:`QualityContext`（pipeline 与 API 共用）。
  - :func:`load_reference_texts` / :func:`compute_payoff_history` —— 组装 helper。

### 11.2 数据库迁移

``database/migrations/0003_quality_reports.sql``（_Sprint 6 下半_）：
- 表 ``quality_reports``（``report_id`` 主键 + ``scores_json`` + ``issues_json``）
- 索引 ``idx_quality_reports_chapter (chapter_id, created_at)``

### 11.3 门禁位置：``packages.workflows.chapter_commit.pipeline``

节点列表新增 ``quality_gate``（位于 ``inject_validate`` 之后、``high_risk_approval`` 之前）：

- 现场调 :func:`packages.core.quality.service.build_quality_context`；
- 跑 :class:`QualityEngine.evaluate`；
- 落库到 ``quality_reports``（独立事务，与 :meth:`StoryStateService.commit_delta`
  的事务互不污染）；
- ``NOVELOS_QUALITY_GATE`` = ``"enforce"`` 时任一 ``severity=='error'`` ⇒ 抛
  ``ValueError('quality gate blocked: [...]')`` 让 run FAILED、chapter 保持 REVIEWED。
- ``"report"`` 模式（**默认**）：error 仅落库不阻断，方便评审 / REQ-Q8 等 MVP 阻断观察。
- 模式优先级：``ctx["quality_gate_mode"]``（``/api/.../commit`` 请求体字段）→
  ``NOVELOS_QUALITY_GATE`` 环境变量 → 默认 ``"report"``。

### 11.4 报告查询 API：``packages.core.api.routers.quality``

- ``GET /api/chapters/{chapter_id}/quality`` —— 最新一份；404 表示尚无 report。
- ``GET /api/projects/{project_id}/quality`` —— 项目全部（created_at DESC）。
- ``POST /api/chapters/{chapter_id}/quality/evaluate`` —— 现场组装 ctx + 评估 + 落库；201。
- 该 router 由 ``discover_routers()`` 自动发现（模块顶层 ``router`` 变量即可被挂载到 ``/api``）。

### 11.5 前端入口

- ``apps/web/src/api/endpoints.ts``：``qualityApi.{latest, evaluate, listByProject}``。
- ``apps/web/src/api/types.ts``：``QualityReport/Issue/Scores/Meta`` 类型。
- ``apps/web/src/components/QualityPanel.tsx``：章节详情页质量评估面板（数据流见
  ``apps/web/README.md`` 的「Sprint 6 下半 — 质量评估面板」节）。
- ``apps/web/src/components/QualityPanel.test.tsx``：6 例覆盖 overall 着色阈值、六子分
  渲染、issue 分组（payoff 单列）、evaluate 按钮回调、空态。

### 11.6 测试覆盖（Sprint 6 下半增量）

- ``tests/api/test_quality.py`` —— 8 例：evaluate / latest 404 / list 降序 / list 404 /
  unknown chapter / chapter-commit enforce 阻断 / chapter-commit report 不阻断 / payload
  完整性（六子分 + _meta）。
- ``tests/unit/test_migrations.py`` —— 计数更新到 29（28 business + quality_reports）。
- ``tests/integration/test_health.py`` —— ``data["tables"]`` 从 28 升到 29。
- ``apps/web/src/components/QualityPanel.test.tsx`` —— 6 例覆盖前端面板行为。
