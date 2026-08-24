# workflows.chapter_commit（章节提交）

> 职责：Observer 抽取 State Delta 业务载荷 → 注入元信息 → submit_delta → (HIGH) Human Approval → commit_delta。chapters.status REVIEWED→COMMITTED。
> 状态：Sprint 4-A 已实现。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `build_observer_ctx` | Transform | 调 `context_engine.build_observer_input` 组装 Observer 输入（含 previous_state / draft_text / director_plan_summary） |
| `observer` | AI | 调 Observer agent（`run_agent(..., expected="observer", mock_script=...)`），输出 7 个 change 数组（无元信息） |
| `inject_validate` | Transform | 注入 10 元信息字段（delta_id / schema_version / workflow_run_id / previous_state_version / created_by="observer:v1" / created_at 等）；先用纯函数 `validate_delta` 校验，**失败时把错误以 `_retry_hint` 注入 observer_input 重试 observer 一次（共 2 次尝试，真实 LLM 常见 op=update 缺 before 等业务校验错误）**，仍失败 → run FAILED；`submit_delta` 仅在最终通过的 delta 上调用一次（避免落 rejected 行）。同时把组装后的完整 ``delta`` + 最终 ``observer_payload`` + ``snapshot_pre`` + ``project_id`` 写到 ctx，供 ``quality_gate`` / ``high_risk_approval`` 使用 |
| `quality_gate` | State | **Sprint 6 下半新增**：现场组装 :class:`QualityContext`（chapter_id / chapter_number / draft / plan / snapshot_pre / delta / payoff_history(近5章) / reference_texts(``<db 父目录>/references/<project_id>/*.txt``，目录不存在则空) / ai_chars/human_chars(由 :func:`packages.core.quality.service.compute_char_stats` 算) / run_id / whitelist=[]），调 :class:`QualityEngine` 评估，**落库**至 ``quality_reports``（独立事务，与 commit 路径互不污染）。**模式**由 ``ctx["quality_gate_mode"]`` 或环境变量 ``NOVELOS_QUALITY_GATE`` 控制：``"enforce"`` 模式任一 ``severity=='error'`` ⇒ 抛 :class:`ValueError("quality gate blocked: [rule_ids...]"`) 阻断 → run FAILED，chapter 保持 REVIEWED；``"report"`` 模式 error 只落库不阻断。默认 ``report``（任务书拍板，避免 golden eval / REQ-Q8 等 MVP 阻断误伤） |
| `high_risk_approval` | Human | **仅当 observer_payload 含 HIGH / character facet=definition / world_kind=rule 任一时暂停**；payload 含 change 清单；`human_input={"approved": true}` 通过 |
| `commit` | State | 调 `StoryStateService.commit_delta`；chapters.status REVIEWED→COMMITTED；当前 DRAFTED（未过 review）→ run FAILED。**V2.0 Wave C 任务一**：commit 成功后调 `packages.core.retrieval.upsert_chapter` 把章节正文 upsert 进 `chapter_fts`（FTS5 全文索引）；**降级语义同 summarize 节点**：FTS 写入异常 → log warning + ``fts_upsert_ok=False``，不抛错、不阻断 commit（commit 状态保持 COMPLETED）。 |
| `summarize` | State | **Sprint 14-A 新增**：commit 成功后追加；调 summarizer agent 生成 ≤ 200 字本章摘要，**落库**至 ``chapter_summaries``（迁移 `0007_chapter_summaries.sql`）；同时把已提交正文末尾 300 字作为 `tail_text` 落库（不调 LLM）。**任何失败（LLM 异常 / 解析失败 / DB 写入失败）均降级不抛错**：warning 日志 + ``summary_status='failed'`` + 不写库；chapter 仍保持 COMMITTED（与 commit 成功状态独立）。摘要 > 200 字由 builder 截断 + 标记 ``degraded=True``。Mock provider 可走通。summarizer ACTIVE prompt 已注册（`docs/agents/prompts/summarizer-v1.md`，capability=reasoning；首次启动服务时由 `POST /api/agents/sync` 写入 `agents` / `prompts` 表）；注册机制已用隔离 tmp DB 验证（见 `docs/agents/scripts/verify_summarizer_sync.py`）。 |

注册名：`chapter-commit`

## 输入 / 输出

- 输入：`chapter_id`（必须 chapters.status=REVIEWED）。
- 输出：`run_id` + status。无 HIGH 时直接 COMPLETED；含 HIGH 时 PAUSED（payload 含 delta_id + change 清单），resume approved → COMPLETED + status=COMMITTED。

## 失败语义

- chapters.status 非 REVIEWED → run FAILED。
- Observer 输出 7 数组缺一或 schema 校验失败（重试一次后仍失败）→ run FAILED。
- **Sprint 6 下半** —— quality_gate 节点在 ``enforce`` 模式下触发 error 级 issue（H-3
  连续 ≥3 章水章 / REQ-Q6 重叠率超阈值 / REQ-Q8 人工占比 <30% 也会阻断）→ 抛
  ``ValueError("quality gate blocked: [...]")`` → run FAILED，chapter **保持 REVIEWED**
  （block 在 commit 之前，已写透的 snapshot 不变，已落库 report 仍可在 UI 展示）。
- HIGH 风险变更未通过 author_approval → run FAILED。
- 乐观锁冲突 → run FAILED。
- 默认 ``report`` 模式（``NOVELOS_QUALITY_GATE`` 未设）：以上 quality_gate 阻断不会发生，
  error issue 仅落库，便于评审阶段观察与回归。
- 模式切换：
  - ``ctx["quality_gate_mode"] = "enforce"|"report"``（workflow API 请求体字段）：
    调用方/测试显式覆盖，按 call 控制。
  - ``NOVELOS_QUALITY_GATE`` 环境变量：``"enforce"`` 或 ``"report"``，默认 ``"report"``。

## Sprint V1.4 增强

### 1. 参照系消费可观测（reference_consumption）

``quality_gate`` 节点现场采集本次评估消费的参照文本清单（项目级
``<db 父目录>/references/<project_id>/*.txt``），由
:func:`packages.core.quality.service.capture_reference_consumption` 提供。

落点（双路径）：

- 节点返回 dict（report 模式不阻断时）：``{"reference_consumption": {...}}``
- ``workflow_runs.checkpoint_json["quality_gate"].reference_consumption``
  （即 ``GET /api/runs/{run_id}`` 的返回；enforce 阻断时也随 ctx 落盘）
- ``quality_reports._meta.reference_consumption``
  （即 ``GET /api/chapters/{cid}/quality`` 的返回；前端可直接读）
- API 触发评估 ``POST /api/chapters/{cid}/quality/evaluate`` 同样把同一字段写入
  ``_meta.reference_consumption``，口径与 pipeline 同源。

字段形状：

```json
{
  "source": "project_refs_dir",
  "files": [{"name": "ref_a.txt", "chars": 1200}, {"name": "ref_b.txt", "chars": 34}],
  "total_chars": 1234,
  "files_count": 2
}
```

无参照目录或 ``project_id`` 不在白名单时 → ``files=[] / total_chars=0 / files_count=0``；
前端 QualityPanel 在 ``files_count > 0`` 时才渲染「本章消费参照系」区块。

### 2. enforce 改稿引导（revision_guidance）

``enforce`` 模式阻断时，``quality_gate`` 节点把每条阻断建议整理为结构化
``revision_guidance``：

```json
[{
  "dimension": "guardrails",       // 或子分维度（plot / character / ... / ai_trace）
  "score": 0,                       // guardrails 维度固定 0（不是低分子分）
  "threshold": 60,
  "top_issues": [<QualityIssue>...],
  "rule_hint": "不要让已死亡角色在本章发生 action/location/goal 等活跃状态变更"
}]
```

生成规则：

1. 任一子分 < 60 ⇒ 进入列表（按低分子分维度）。
2. 任一 ``severity=='error'`` issue 的 ``rule_id`` 命中 :data:`packages.workflows.chapter_commit.pipeline._RULE_REVISION_HINTS`
   ⇒ 进入列表（``dimension='guardrails'``）。
3. ``rule_id`` 未命中时按 ``category`` 命中 :data:`packages.workflows.chapter_commit.pipeline._CATEGORY_REVISION_HINTS`
   兜底。
4. 全部未命中（极少见）⇒ 1 条通用 guardrail 引导，至少保证作者拿得到「按 issue.message 修复」。

落点（双路径）：

- 节点返回 dict（report 模式不阻断时也写入）：``{"revision_guidance": [...]}``
- ``workflow_runs.checkpoint_json["quality_gate"].revision_guidance``
- ``runs.error``（enforce 阻断时）：``"quality gate blocked: <rule_ids> | guidance=<json>"``
  —— 前端 / 测试按 ``| guidance=`` 分隔即可拿到 JSON。

前端 QualityPanel 在 ``revision_guidance.length > 0`` 时渲染「改稿引导」区块：
``dimension='guardrails'`` 展示「当前阻断 rule」+ rule_hint；
低分子分维度展示「当前分 X / 阈值 60」+ rule_hint + top_issues（≤ 3 条 issue 摘要）。

### 3. summarizer 注册验证固化

原 ad-hoc 脚本 ``docs/agents/scripts/verify_summarizer_sync.py`` 的核心断言已固化为
``tests/integration/test_summarizer_prompt_registration.py``（3 个测试）：

- ``test_summarizer_prompt_registered_after_sync`` —— scanned/registered/agents/ACTIVE
  行 + content 与源文件一致 + ``get_active_prompt('summarizer')`` 一致。
- ``test_summarizer_prompt_sync_is_idempotent`` —— 第二次 sync 不应有 updated，
  prompts 行数 / status 不漂移。
- ``test_summarizer_get_active_prompt_returns_non_empty`` —— ACTIVE prompt content 非空
  且与 ``docs/agents/prompts/summarizer-v1.md`` 完全一致。

CI 集成后可防止 summarizer prompt 漂移（capability / prompt 内容 / ACTIVE 行 缺失）
而悄悄 FAILED。

## 依赖

- `packages/core/context_engine/builders.build_observer_input`
- `packages/core/agent_runtime/`
- `packages/core/story_state/`
- `packages/core/workflow_runtime/`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/commit
Content-Type: application/json

{
  "mock_providers": {
    "observer": ["{...7 数组 JSON（无元信息）...}"]
  }
}
```

无 HIGH 时返回 `{run_id, status: "COMPLETED"}`；含 HIGH 时返回 `{run_id, status: "PAUSED", pause_payload: {...}}`。

## 维护注意点

- Observer 输出必须仅含 7 数组（`character_changes` / `world_changes` / `relationship_changes` / `new_events` / `resolved_hooks` / `new_hooks` / `debt_changes`）；元信息由本工作流 `inject_validate` 节点统一注入。
- HIGH 触发条件：任意 change `risk_level == "HIGH"`，或 character_changes 中 `facet == "definition"`，或 world_changes 中 `world_kind == "rule"`。
- `state_version` 严格 +1/commit（`docs/state-model/state-delta-v0.md` §6.2）；任何异常路径不会污染 state。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、`docs/agents/agent-contracts-v0.md` §5、`docs/state-model/state-delta-v0.md`。