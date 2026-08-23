# Regression Eval（Sprint 4-B + §6 基线判定）

> 状态：v0.1 —— Sprint 4-B MVP 骨架（golden 数据集 + 端到端回归 runner）+ 落地
> ``docs/evaluation/quality-scoring-v0.md`` §6 Regression 基线判定的 **MVP 可执行子集**
> （结构签名比对；分数级回归待 LLM judge 接入后扩展）。

## 1. 目录布局

```
tests/evals/
├── README.md                      # 本文件
├── runner.py                      # 回归 runner（run_case / run_all / discover_cases）
├── regression_baseline.py         # §6 基线判定（签名比对 / 基线读写 / 报告落盘）
├── test_golden_regression.py      # pytest 参数化集成（CI 跑这条）
├── test_regression_baseline.py    # pytest 基线判定单测（临时 baseline + 构造结果）
└── golden/
    └── ch001_basic/               # 种子 case（中文男频玄幻第一章：筑基少年获金手指）
        ├── input.json             # 项目/角色/章节/作者意图
        ├── mocks.json             # director / writer / observer 三个 agent 的 mock 输出
        └── expected.json          # MVP 阻断级断言（详见 §4）
```

## 2. golden case 三文件格式

### 2.1 `input.json`

```jsonc
{
  "project": {
    "name": "九幽仙途",
    "premise": "修仙少年意外获上古传承，以杀伐证道",
    "genre": "玄幻",
    "target_words": 2200
  },
  "characters": [
    {
      "name": "林轩",
      "role": "protagonist",
      "core": { "personality": "...", "background": "...", "goal": "..." }
    }
  ],
  "chapter": { "number": 1, "title": "识海古镜" },
  "author_intent": "本章要让主角林轩意外在洞府禁地获得一枚上古识海古镜..."
}
```

字段语义：
- ``project`` —— 与 :class:`packages.domain.project.models.ProjectCreate` 对齐；
  ``target_words`` 仅作 runner 信息展示，不参与 workflow。
- ``characters[]`` —— 与 :class:`packages.domain.character.models.CharacterCreate` 对齐；
  ``core`` 字段写入 ``characters.core_json``。runner 自动给每个角色派生
  ``character_id = char_<slug>_<index>``（slug 允许 CJK），供 golden mock 引用。
- ``chapter`` —— ``number / title`` 必填；runner 派生 ``chapter_id = ch_<slug>``。
- ``author_intent`` —— 透传给 ``chapter-plan`` 节点的 ``initial_ctx.author_intent``。

### 2.2 `mocks.json`

```jsonc
{
  "director": [
    { "schema_version": "director-plan.v1", ... }
  ],
  "writer": [
    { "schema_version": "writer-output.v1", "prose": "...", "self_report": {...} }
  ],
  "observer": [
    {
      "character_changes": [...],
      "world_changes": [...],
      "relationship_changes": [...],
      "new_events": [...],
      "resolved_hooks": [...],
      "new_hooks": [...],
      "debt_changes": [...]
    }
  ]
}
```

- 顶层三键 ``director / writer / observer`` 各自一个 list；list 中每个元素是 agent
  在该次工作流中第 N 次调用的 mock 输出（runner 默认只跑一次，因此长度通常为 1）。
- **值是合法 JSON 对象**（非字符串），runner 内部负责 ``json.dumps(ensure_ascii=False)``
  转字符串后透传给 :class:`MockProvider`。
- runner 会**自动重写所有** ``evidence.chapter_id`` 字段为实际 chapter_id（保证
  State Delta Validator 的 ``[business] evidence.chapter_id == chapter_id`` 通过）。
- director / writer mock 需满足各自 prompt 的契约（``docs/agents/agent-contracts-v0.md``
  §3.2 / §4.2 + §5.2）：MVP 阶段最简只校验 ``schema_version``（director /
  writer）+ 7 数组结构（observer）。
- observer mock 不得含 10 元信息字段（delta_id / schema_version 等）—— runner 走
  ``strip_observer_violations`` 自动剥离（agent-contracts §5.3）。

### 2.3 `expected.json`

```jsonc
{
  "final_chapter_status": "COMMITTED",
  "state_version_min": 2,
  "delta_arrays_nonempty": ["new_hooks", "new_events"],
  "snapshot_contains": {
    "hooks_named": ["识海古镜传承", "周元的窥伺", "禁地异象惊宗门"]
  },
  "no_guardrail_block": true
}
```

字段语义：
- ``final_chapter_status`` —— 期望 ``chapters.status``（MVP = ``COMMITTED``）。
- ``state_version_min`` —— 期望 ``story_states`` 最新 ``state_version >= 该值``
  （MVP 至少 2 = genesis v1 + 1 commit）。
- ``delta_arrays_nonempty[]`` —— observer 业务载荷中必须非空的 7 数组名。
- ``snapshot_contains.hooks_named[]`` —— 期望快照 ``snapshot_json.hooks[].name``
  包含的全部 hook 名（顺序无关）。
- ``no_guardrail_block`` —— MVP 阻断级：schema_validity 必须在
  ``commits.validation_json.guardrail_results`` 中非 fail；observer 7 数组结构必须
  齐全且均为 list。Sprint 6 Quality 子模块落地后扩展至 character / world / REQ-Q6 / REQ-Q8。

## 3. runner 用法

### 3.1 Python API

```python
from pathlib import Path
from tests.evals.runner import run_case, run_all, discover_cases

# 跑单个 case
result = run_case(Path("tests/evals/golden/ch001_basic"))
assert result.passed
for c in result.checks:
    print(c.check, c.passed, c.detail)

# 跑全部
passed, results = run_all(Path("tests/evals/golden"))
print(f"{passed}/{len(results)} passed")

# 发现（pytest 参数化用）
case_dirs = discover_cases(Path("tests/evals/golden"))
```

### 3.2 CLI

```bash
python scripts/eval_regression.py                 # 默认 tests/evals/golden；check 模式
python scripts/eval_regression.py --golden-dir /custom/path
python scripts/eval_regression.py --update-baseline   # 全绿且一致时更新基线
python scripts/eval_regression.py --run-id run_manual_20260823   # 指定 run_id
```

- 逐项打印断言明细 + 每个 case 的**基线签名**（state_version / delta_arrays /
  hooks / guardrails_pass）。
- 每次运行落盘报告 ``docs/evaluation/runs/<run_id>.json``（§6.4）；决策
  PASS → exit 0，BLOCK → exit 1。
- 基线文件 ``docs/evaluation/baseline/last_passing_run.json``：首跑自动创建
  （baseline-created）；之后默认 **check 模式不更新**，仅 ``--update-baseline``
  更新（详见 §5）。

### 3.3 pytest

```bash
pytest tests/evals/test_golden_regression.py -v
```

每 case 一个测试函数；测试 ID 即 case 子目录名。

## 4. MVP 阻断集合（`docs/evaluation/quality-scoring-v0.md` §6.3）

回归 runner 当前断言项 = MVP 阻断级：

| 检查 | 含义 | 对应 PRD §85 反例 |
|---|---|---|
| 4 条 workflow 全部 COMPLETED | plan / write / review(approve) / commit | "Workflow 不得 Commit" |
| chapter.status == COMMITTED | 状态机正确流转 | §86 |
| state_version >= 2 | 至少 1 次 commit | §91 |
| observer 业务载荷关键数组非空 | observer 真的产出 delta | §86 schema_validity |
| snapshot 物化 hook 名 | apply_delta 落 snapshot | §91 |
| schema_validity = pass | guardrail 不阻断 | §86 |

> **MVP 不含**：overall 评分对比（§6.2 ``decide_release`` 的分数侧）；LLM judge 子分
> （plot / character / continuity / style / pacing / foreshadowing）；timeline_consistency /
> knowledge_leakage 阻断（§4.2 / §4.5 MVP 仅 warning）；REQ-Q6 / REQ-Q7 / REQ-Q8
> 合规 Guardrail 的分数级比对（属 Sprint 6 Quality 子模块）。
>
> **§6 基线判定已含**（见下节）：结构签名比对（state_version / delta_arrays / hooks）+
> guardrails_pass 由 pass 变 fail 阻断 —— 是 §6.3 条件 3/4 在 MVP 数据面上的映射。

## 5. §6 Regression 基线判定（MVP 可执行子集）

实现于 ``tests/evals/regression_baseline.py``，把 quality-scoring-v0 §6「分数与
Guardrail 通过率对基线」映射为**结构签名比对**（现有 runner 是流程+结构断言，
不产分数）：

| 基线字段 | 来源 | 含义 |
|---|---|---|
| ``state_version`` | ``story_states`` 最新版本 | 结构演化程度（§6.3 条件 4 数据面） |
| ``delta_arrays`` | observer 载荷非空数组名集合 | Delta 产出结构 |
| ``hooks`` | 快照 hooks 名集合 | 快照物化结构 |
| ``guardrails_pass`` | MVP 阻断级 Guardrail（schema_validity + observer 7 数组结构） | §6.3 条件 3 |

**判定规则**：

1. 任一 case 失败 → BLOCK（现状已有）；
2. ``state_version`` / ``delta_arrays`` / ``hooks`` 任一与基线不一致 → BLOCK
   （结构回归，§6.3 条件 4 映射）；case 不在基线中（新增 case）也 BLOCK；
3. ``guardrails_pass`` 由 true 变 false → BLOCK（§6.3 条件 3）；
   false→true 是改善，不阻断；
4. 全部一致 → PASS。

**基线更新语义**（取更安全的一档）：

- **首跑无基线** → 全绿即建基线并 PASS（bootstrap，与 ``--check`` 无关；输出注明
  baseline-created）；
- **有基线** → 默认 check 模式：不一致只报告不更新；全绿且一致也**不更新**，
  仅显式 ``--update-baseline`` 才更新；
- **任一 case 失败或漂移** → 无论如何不更新基线。

基线文件内容 = ``{run_id, date, cases: {case_name: {state_version, delta_arrays,
hooks, guardrails_pass}}, aggregate: {cases_passed, cases_total}, scoring_version,
baseline_semantics}``；``baseline_semantics: "structural-signature-mvp"`` 标明
本基线的判定语义（分数级回归扩展后需重建基线或迁移语义）。

## 6. 何时必须跑（PRD §84 / quality-scoring-v0.md §6.1）

| 触发项 | 是否必跑 |
|---|---|
| 任意 Prompt 文件改动（``docs/agents/prompts/*.md``） | **是** |
| 任意 Agent 注册表变更（增 / 删 / 输入输出契约变更） | **是** |
| 任意 Model 路由调整（``docs/agents/model-router-*.md``） | **是** |
| 任意 Quality / Scoring / Guardrail 规则变更 | **是** |
| ``_meta.scoring_formula_hash`` 变更 | **是** |
| Workflow 节点 / 状态机变更 | **是** |
| 普通文档 / 注释 / 测试夹具增补 | 建议跑 |

> 触发后跑法：先 ``python scripts/eval_regression.py``（check 模式，确认无漂移），
> 再显式 ``--update-baseline`` 把新的全绿结果固化为基线。

## 7. 添加新 case

1. 在 ``tests/evals/golden/`` 下新建子目录（如 ``ch002_xxx/``）。
2. 按 §2 三文件格式写 ``input.json / mocks.json / expected.json``。
3. 跑 ``python scripts/eval_regression.py`` 确认 PASS。
4. 提交（与 S4-A 配套；主控验收时统一 commit）。
5. **注意**：新增 case 后，旧基线不含该 case → 会 BLOCK（case 不在基线中）；
   确认无回归后跑 ``--update-baseline`` 重建基线。

## 8. S6 升级路径

- 加入 **LLM judge 分数级回归**（plot / character / style / pacing / foreshadowing
  + overall）：扩展 ``regression_baseline.py`` 的签名字段（子分 / judge model
  version），按 §6.2 ``decide_release`` 判定（TOLERANCE_OVERALL=2、关键子分
  ≥ baseline-3、Guardrail hit_rate 恶化 >10%）；届时基线
  ``baseline_semantics`` 升级（旧结构签名基线需迁移或重建）。
- 加入 ``continuity_cases`` —— 对抗样例（角色生死矛盾、时间线穿越、知识越界等），
  用于 Guardrail 单测（§5.3）。
- 将 §6.4 报告纳入 CI 流水线（OV-8）。

---

## 附录 A：版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0 | 2026-08-23 | Sprint 4-B MVP：runner + 1 个种子 case + CLI + pytest 参数化。 |
| v0.1 | 2026-08-23 | 落地 quality-scoring-v0 §6 基线判定 MVP 子集：runner 暴露基线签名（state_version / delta_arrays / hooks / guardrails_pass）；新增 ``regression_baseline.py``（签名比对 / 判定 / 基线读写 / 报告落盘）与 ``test_regression_baseline.py``；CLI 增 ``--update-baseline / --check / --run-id``；基线 ``docs/evaluation/baseline/last_passing_run.json`` + 报告 ``docs/evaluation/runs/<run_id>.json``。 |
