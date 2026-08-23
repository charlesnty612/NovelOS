# Regression Eval 骨架（Sprint 4-B）

> 状态：v0（MVP 骨架）—— 落地 golden 数据集 + 端到端回归 runner；Sprint 6 Quality
> 子模块启动后再扩 LLM judge 评分与基线比对。

## 1. 目录布局

```
tests/evals/
├── README.md                      # 本文件
├── runner.py                      # 回归 runner（run_case / run_all / discover_cases）
├── test_golden_regression.py      # pytest 参数化集成（CI 跑这条）
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
python scripts/eval_regression.py                 # 默认 tests/evals/golden
python scripts/eval_regression.py --golden-dir /custom/path
```

逐项打印断言明细；任一 case 失败 → exit 1。

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

> **MVP 不含**：overall 评分对比（§6.2 ``decide_release``）；LLM judge 子分（plot /
> character / continuity / style / pacing / foreshadowing）；timeline_consistency /
> knowledge_leakage 阻断（§4.2 / §4.5 MVP 仅 warning）；REQ-Q6 / REQ-Q7 / REQ-Q8
> 合规 Guardrail（属 Sprint 6 Quality 子模块）。

## 5. 何时必须跑（PRD §84 / quality-scoring-v0.md §6.1）

| 触发项 | 是否必跑 |
|---|---|
| 任意 Prompt 文件改动（``docs/agents/prompts/*.md``） | **是** |
| 任意 Agent 注册表变更（增 / 删 / 输入输出契约变更） | **是** |
| 任意 Model 路由调整（``docs/agents/model-router-*.md``） | **是** |
| 任意 Quality / Scoring / Guardrail 规则变更 | **是** |
| ``_meta.scoring_formula_hash`` 变更 | **是** |
| Workflow 节点 / 状态机变更 | **是** |
| 普通文档 / 注释 / 测试夹具增补 | 建议跑 |

## 6. 添加新 case

1. 在 ``tests/evals/golden/`` 下新建子目录（如 ``ch002_xxx/``）。
2. 按 §2 三文件格式写 ``input.json / mocks.json / expected.json``。
3. 跑 ``python scripts/eval_regression.py`` 确认 PASS。
4. 提交（与 S4-A 配套；主控验收时统一 commit）。

## 7. S6 升级路径

- 加入 ``baseline/last_passing_run.json`` —— ``scripts/eval_regression.py --baseline`` 模式：
  加载 baseline 跑回归，对比 ``overall`` / 各子分；按 §6.2 ``decide_release`` 五条
  判定 PASS / BLOCK。
- 加入 LLM judge（plot / character / style / pacing / foreshadowing）—— 双评取低
  对齐 §7.3 防刷分；MVP 暂以 schema_validity + 状态流转 + 快照物化为阻断级。
- 加入 ``continuity_cases`` —— 对抗样例（角色生死矛盾、时间线穿越、知识越界等），
  用于 Guardrail 单测（§5.3）。

---

## 附录 A：版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0 | 2026-08-23 | Sprint 4-B MVP：runner + 1 个种子 case + CLI + pytest 参数化。 |
