# 番茄签约体检（signing_check）

> 基于番茄男频公开规则的「开篇体检」启发式检查器。无 LLM、无新增第三方依赖、无 schema 变更。

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

每个 fail/warn 都附带 `advice` 字段说明「如何修复」（启发式建议，非平台保证）。

## 平台规则依据

- 黄金三章：第一章 300 字内出冲突、主角尽早出场；前两章亮金手指；第三章小高潮/打脸；每章末留钩子。
- 单章字数 1500-2200 最佳（软阈值 1200-2600，超出仅为 warn）。
- 签约窗口 2万 / 5万 / 8万 共 3 次机会，被拒 3 次永久无法签约。
- AI 低质文严打：高频副词/描述词（坚定/瞬间/顿时…）的堆叠会触发 warn。

词典词条选自公开网文写作共识与番茄编辑指南要点，可按平台口径微调扩展（见下文「扩展点」）。

## 对外接口

### Python

```python
from packages.core.signing_check import run_checks, run_signing_check, format_summary

# 纯函数（无 IO）
items = run_checks(
    chapters=[{"number": 1, "text": "..."}, {"number": 2, "text": "..."}],
    protagonist_names=["李晨"],
)

# 服务层（读 DB）
result = run_signing_check(db_path, project_id)
# result = {project_id, project_name, total_chars, generated_at, items, summary}

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
| `tests/api/test_signing_check_api.py`      | 端点：正常路径 + 404 + 空项目；fanqie 导出包含摘要段                                     |

## 边界与扩展点

- **启发式非保证**：规则按公开编辑共识实现，不替代编辑判断；fail/warn 仅作作者自检信号。
- **词典可扩展**：`checks.py` 模块级 `CONFLICT_WORDS` / `GOLDEN_FINGER_WORDS` /
  `FACE_SLAP_WORDS` / `HOOK_WORDS` / `ECHO_WORDS` 均为 `tuple[str, ...]`，
  后续按平台口径调整直接改此处即可（无需改函数实现）。
- **不引入分词**：未使用 jieba 等第三方分词；冲突/钩子词按子串扫描，对短词可能误命中（如「打」字）。
  调整粒度可加白名单或引入更精细的字符窗口判定。
- **章节状态**：`service._filter_chapters` 取 `DRAFTED / REVIEWED / COMMITTED / RELEASED`，
  与 `builder._committed_chapters` 取「全章节」的口径略有差异——导出以字数窗口为准，体检以「已有正文」为准。
- **数量统计**：正文数字符口径按「去除所有空白后取长度」（贴近番茄字数计）；与 `_count_chars` 一致。