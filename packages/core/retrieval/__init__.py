"""V2.0 Wave C 任务一：FTS5 召回（章节正文 → 跨长程呼应片段）。

对标社区「状态库定事实 + 检索召回供呼应」的混合方案：
- 状态库（story_state）定义事实，避免 LLM 幻觉；
- 全文检索（FTS5）召回历史正文片段，让 writer/director 看到具体叙事语境。

公共入口：
- :func:`rebuild_index` —— 清空 chapter_fts 并按 chapters 表当前全文重建。
- :func:`upsert_chapter` —— 单章 upsert（commit 路径调用；upsert 失败按
  summarize 节点降级语义 log warning 不阻断）。
- :func:`search` —— 按关键词列表查 top-N 片段，每段截断 + 带章节号标注。
- :func:`extract_keywords` —— 从章节计划文本提取关键词（中文按 2-gram +
  实体名单口径，详见 README）。
"""

from __future__ import annotations

from .service import (
    extract_keywords,
    rebuild_index,
    search,
    upsert_chapter,
)

__all__ = [
    "extract_keywords",
    "rebuild_index",
    "search",
    "upsert_chapter",
]
