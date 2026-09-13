# 番茄签约体检（signing_check）

> 基于番茄男频公开规则的「开篇体检」启发式检查器。无 LLM、无新增第三方依赖。
> 题材库 P2（2026-09-13）：项目绑定题材包且 payload 声明 `opening_rules` 时追加
> **题材开篇检查段**（失败为 info 级提示，report-only 不阻断）。

## 职责

按番茄投稿硬规则对项目当前正文做一次体检，输出 `pass / warn / fail / info` 四档结构化结果，供作者自检与导出包内嵌摘要。

## 检查项清单

| key                       | 触发条件（节选）                                                                             | level 档位                |
| ------------------------- | ------------------------------------------------------------------------------------- | ----------------------- |
| `ch1_conflict_300`        | 第一章前 300 字命中冲突词（CONFLICT_WORDS）或含中文对话句                                          | pass / fail             |
| `ch1_protagonist_500`     | 任一主角名出现在第一章前 500 字                                                               | pass / fail             |
| `ch2_golden_finger`       | 前两章合计的前 1000 字内命中金手指词（GOLDEN_FINGER_WORDS）≥ 1                                     | pass / fail             |
| `ch3_climax`              | 第三章命中 FACE_SLAP_WORDS 或 CONFLICT_WORDS                                                | pass / warn / info(无第三章) |
| `chapter_hooks_ch{n}`     | 第 n 章末尾 120 字命中 HOOK_WORDS 或 `？/……/！` 之一                                           | pass / warn             |
| `chapter_length_ch{n}`    | 第 n 章字数 ∈ \[1200, 2600\]                                                              | pass / warn             |
| `echo_words`              | 全文 ECHO_WORDS 每千字频次 top3 中任一 > 5 次/千字                                              | pass / warn / info(空文)  |
| `signing_window`          | 按总字数提示签约窗口（< 2万 / 2万-8万 / ≥ 8万）                                                  | info                    |
| `genre_opening_<check_id>`| 题材包 `opening_rules` 逐条机检（见下「题材开篇检查段」）                                            | pass / info             |

每个 fail/warn 都附带 `advice` 字段说明「如何修复」（启发式建议，非平台保证）。

## 题材开篇检查段（题材库 P2）

**触发**：项目经 `projects.genre_pack_id` 绑定题材包，且 pack payload 声明了非空
`opening_rules`（`docs/state-model/schemas/genre-pack.schema.json` v1.1.0 段）。
未绑定 / 未声明 → 结果**无** `genre_opening` 键、items 无 `genre_opening_*` 行（零行为变化）。

**判定**（纯规则、无 LLM；`packages/core/signing_check/checks.py::evaluate_genre_opening`）：

1. 逐条规则定位目标章：`chapter_no`（1~3）指定章，或（缺席时）黄金三章整体窗口；
2. 从 `requirement` / `description` 抽取可机检关键词——剔除章号 / 数量片段
   （`第1章` / `300字`）与约束语法词（`必须` / `出现` / `开篇` …）后，保留长度 ≥2 的片段；
3. 目标章文本**任一关键词命中即满足**（宽松口径：宁可漏报不误报）。

**判定状态**（结构化段 `result["genre_opening"]["rules[]"]`）：

| status | 含义 | CheckItem level |
|---|---|---|
| `pass` | 目标章已写且命中关键词 | `pass` |
| `fail` | 目标章已写但未命中任何关键词 | **`info`** |
| `unverifiable` | 规则文本抽不出可机检关键词（语义型要求） | `info` |
| `not_written` | 目标章尚未写正文 | `info` |

**不阻断**：`fail` 及以上三态全部进 `info` 档，只作作者自检提示——不进入
`summary.fail_count`（fail 档只由平台规则贡献）、不影响导出 / 签约流程的任何分支。
结构化段形状：
`{pack_id, pack_name, rule_count, rules[], pass_count, fail_count, info_count}`，
每条规则 `{check_id, chapter_no, description, requirement, status, detail}`。

```json
{
  "genre_opening": {
    "pack_id": "genre-male-quicktrans-v1",
    "pack_name": "男主快穿",
    "rule_count": 2,
    "rules": [
      { "check_id": "sys_bind_ch1", "chapter_no": 1,
        "description": "系统绑定不得晚于第 1 章",
        "requirement": "第 1 章必须出现系统绑定",
        "status": "pass", "detail": "第1章命中 系统绑定" },
      { "check_id": "hook_tail_ch1", "chapter_no": 1, "description": "",
        "requirement": "第 1 章必须出现倒计时",
        "status": "fail", "detail": "第1章未命中 倒计时" }
    ],
    "pass_count": 1, "fail_count": 1, "info_count": 0
  }
}
```

## 平台规则依据

- 黄金三章：第一章 300 字内出冲突、主角尽早出场；前两章亮金手指；第三章小高潮/打脸；每章末留钩子。
- 单章字数 1500-2200 最佳（软阈值 1200-2600，超出仅为 warn）。
- 签约窗口 2万 / 5万 / 8万 共 3 次机会，被拒 3 次永久无法签约。
- AI 低质文严打：高频副词/描述词（坚定/瞬间/顿时…）的堆叠会触发 warn。

词典词条选自公开网文写作共识与番茄编辑指南要点，可按平台口径微调扩展（见下文「扩展点」）。

## 对外接口

### Python

```python
from packages.core.signing_check import (
    run_checks, run_signing_check, format_summary, evaluate_genre_opening,
)

# 纯函数（无 IO）
items = run_checks(
    chapters=[{"number": 1, "text": "..."}, {"number": 2, "text": "..."}],
    protagonist_names=["李晨"],
    # 题材库 P2（可选）：题材包 payload.opening_rules 的规范化条目
    opening_rules=[{"check_id": "sys_bind_ch1", "chapter_no": 1,
                    "description": "系统绑定不得晚于第 1 章",
                    "requirement": "第 1 章必须出现系统绑定"}],
)

# 题材开篇规则逐条判定（status: pass / fail / unverifiable / not_written）
verdicts = evaluate_genre_opening(chapters, opening_rules)

# 服务层（读 DB；绑定题材包时自动读 opening_rules）
result = run_signing_check(db_path, project_id)
# result = {project_id, project_name, total_chars, generated_at, items, summary
#           [, genre_opening]}

# 摘要渲染（导出包嵌入）
text = format_summary(result)
```

### HTTP

```
GET /api/projects/{project_id}/signing-check
```

- 200 → 上述 `result` dict
- 404 → `{"detail": "project '...' not found"}`
- 500 → `{"detail": "signing check failed"}`

由 `discover_routers()` 自动发现并挂载到 `/api` 前缀。

## 导出包集成

`packages/core/exporter/builder.py::build_fanqie_package` 在末尾追加 `===== 签约体检摘要 =====` 段，
由 `format_summary(run_signing_check(...))` 渲染；DB 异常时跳过摘要（不影响导出主流程）。

## 测试

| 文件                                          | 覆盖                                                                  |
| ------------------------------------------- | ------------------------------------------------------------------- |
| `tests/unit/test_signing_check.py`          | 纯函数：每项检查至少一正一反；echo 堆叠；签约窗口三区间；空项目；service ValueError          |
| `tests/unit/test_signing_check_genre_opening.py` | 题材开篇检查段：未绑定 / 无 opening_rules / pass+fail 状态 / 失败为 info 级 / not_written + unverifiable / format_summary 渲染 |
| `tests/api/test_signing_check_api.py`      | 端点：正常路径 + 404 + 空项目；fanqie 导出包含摘要段；绑定题材包 → 响应含 `genre_opening` 段        |

## 边界与扩展点

- **启发式非保证**：规则按公开编辑共识实现，不替代编辑判断；fail/warn 仅作作者自检信号。
- **词典可扩展**：`checks.py` 模块级 `CONFLICT_WORDS` / `GOLDEN_FINGER_WORDS` /
  `FACE_SLAP_WORDS` / `HOOK_WORDS` / `ECHO_WORDS` 均为 `tuple[str, ...]`，
  后续按平台口径调整直接改此处即可（无需改函数实现）。
- **不引入分词**：未使用 jieba 等第三方分词；冲突/钩子词按子串扫描，对短词可能误命中（如「打」字）。
  调整粒度可加白名单或引入更精细的字符窗口判定。题材开篇规则的关键词抽取同款启发式
  （`_rule_keywords`）：抽不出关键词时该条报 `unverifiable`，**不**猜判定。
- **题材包读失败不阻断**：`service._load_project_opening_rules` 全容错——未绑定 /
  payload 非法 / 极老库缺 `genre_packs` 表 / 读库异常 → 该段整体缺席，体检结果与
  题材库前逐字节一致。
- **章节状态**：`service._filter_chapters` 取 `DRAFTED / REVIEWED / COMMITTED / RELEASED`，
  与 `builder._committed_chapters` 取「全章节」的口径略有差异——导出以字数窗口为准，体检以「已有正文」为准。
- **数量统计**：正文数字符口径按「去除所有空白后取长度」（贴近番茄字数计）；与 `_count_chars` 一致。