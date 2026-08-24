# packages/core/retrieval（FTS5 召回服务）

V2.0 Wave C 任务一落地：对标社区"状态库定事实 + 检索召回供呼应"的混合方案，零依赖实现。

## 目标

让 Context Engine 装配 writer / director 输入时，能从历史章节正文里召回**与当前章节计划文本相关的具体叙事片段**，提供跨长程呼应（"上一章里那只古镜" → 命中最初埋设章节）。

## 设计要点

- **零依赖**：使用 Python 标准库 `sqlite3`（Python 3.11+ 默认编译 FTS5）。如本机 SQLite 不含 FTS5，`apply_migrations` 在 0011 步会失败，立即停止报告——**禁止退化为 LIKE 扫描**（任务书硬性要求）。
- **internal content 模式**（非 external content）：FTS5 虚表 `chapter_fts` 内部存 `(chapter_id UNINDEXED, content)`；`content` 列存 bigram 化文本（中文按 2-gram + 空格分隔；英文按原 token），由 service 层 `upsert_chapter()` 写入。不用 external content 的原因：章节正文实际存 `drafts.content` 而非 `chapters.content`（一个 chapter 可多个 draft），external content 模型不适用。
- **同步靠 chapter_commit upsert 钩子**（**不**用 trigger）：`packages/workflows/chapter_commit/pipeline.py:_commit_node` 成功后调 `upsert_chapter(db_path, chapter_id)` 显式 upsert；service 层不依赖 trigger，失败按 summarize 节点相同语义降级（log warning，不阻断 commit）。
- **兜底重建**：`rebuild_index()` 提供显式重建入口——逐行 `DELETE FROM chapter_fts WHERE chapter_id=?` + `INSERT INTO chapter_fts(...) VALUES(...)`，**不**使用 FTS5 的 `INSERT INTO chapter_fts(chapter_fts) VALUES('rebuild')` 一键重建命令（因为我们的 schema 是 internal content，`'rebuild'` 命令在该模式下不重索引整表）。
- **章节正文，不入摘要**：`chapter_summaries.summary` 不进 FTS——摘要已是 context_engine 装配另一路输入（`recent_chapter_summaries`），召回目标是原文片段（具体叙事语境）。

## 公共 API

### `extract_keywords(plan_text, entity_names=None) -> list[str]`

从章节计划文本提取关键词。

**口径（任务书"选一种简单口径"）**：
- **中文按 2-gram（bigram）切词**：相邻 2 字符构成一个 token，保留所有出现（不去重，避免丢失单字/双字共现信号）。
- **英文/数字按空格/标点切词**（FTS5 unicode61 默认行为）。
- **实体名优先拼接**：传 `entity_names=[...]` 时作为整体 token 进入查询（便于 FTS5 整词命中）。
- **停用词过滤**：16 个中文常见停用词（"的了是我在你他她它这那也"等），详见 `service.py:_STOPWORDS_ZH`。
- **去重 + 上限 64 个**：超长 plan 保护。

后续可替换为 jieba/hanlp 词表升级（任务书允许的范围）。

### `rebuild_index(db_path, project_id=None) -> int`

重建 FTS 索引。

- `project_id=None` → 全库重建：先 `DELETE FROM chapter_fts` 清空，再按 `chapters` 表
  逐 chapter 取最新 draft 的 `content`，逐行 `DELETE + INSERT`（每章一个独立事务）。
- `project_id="<id>"` → 仅该项目：按 `chapters.project_id` 过滤后逐章 `DELETE + INSERT`。
- **不**使用 FTS5 的 `INSERT INTO chapter_fts(chapter_fts) VALUES('rebuild')` 一键重建命令
  ——本表为 internal content 模式，FTS5 在该模式下不会触发整表重建，逐行 DELETE+INSERT
  才是可靠路径。

返回处理 chapter 行数（重索引条数）。

### `upsert_chapter(db_path, chapter_id) -> bool`

单章 upsert：删旧 + 插新。失败按 summarize 节点相同语义降级（log warning，不抛错）。

### `search(db_path, project_id, query, *, limit=3, current_chapter_id=None, snippet_max_chars=300) -> list[dict]`

按 query 字符串在 chapter_fts 上检索该项目下的相关历史片段。

**返回**：
```python
[
  {"chapter_id": "ch_xxx", "chapter_no": 3, "snippet": "……命中片段（≤300字）……", "rank": -2.5},
  ...
]
```

按 FTS5 bm25 升序排（值越小越相关）；空 list 表示无命中或无索引。

- `current_chapter_id` 传值时自动排除当前章（避免自召自）。
- snippet 取章节 content 在命中位置前后扩展；超长截断 + 「…」标记。

## 装配接入（context_engine/builders.py）

director / writer 装配时按当前章节计划文本：

1. 调用 `extract_keywords(plan_text, entity_names)` 提取关键词；
2. 拼接成 query 字符串（空格分隔）；
3. 调 `search(db_path, project_id, query, current_chapter_id=chapter_id, limit=3)`；
4. 取回 top 3 片段放进 director / writer 输入顶层 `recalled_passages` 键。

无索引 / 无命中 → `recalled_passages: []`（不阻断装配）。

## chapter_commit 集成（commit 后 upsert 钩子）

`packages/workflows/chapter_commit/pipeline.py:_commit_node` 成功后调用：

```python
from packages.core.retrieval import upsert_chapter
upsert_chapter(db_path, chapter_id)  # 失败 → log warning，不阻断 commit
```

降级语义与 `summarize` 节点一致：FTS 召回是 best-effort，commit 永不因 FTS 失败 FAILED。

## 测试

- `tests/unit/test_v2_wave_c_retrieval.py`：
  - FTS 建索引（迁移 0011 落地 + chapter_fts 虚表存在）；
  - upsert + search 命中正确章节；
  - 关键词提取（中英文 + bigram + 停用词）；
  - 降级路径（FTS 表达式非法 → 空 list 不抛）；
  - 装配注入（builders 输出 `recalled_passages` 键）；
  - 截断（≤300 字 + 「…」）。

## 表数口径

`chapter_fts` + 4 个内部表（`chapter_fts_config/data/docsize/idx`）均登记为 `type='table'`。
`packages.core.db.count_tables` 已加 `name NOT LIKE 'chapter_fts%'` 排除，保持"业务表 34 + _migrations = 35 总表"口径不变（任务书："FTS 虚表算不算业务表按现有口径处理"）。
