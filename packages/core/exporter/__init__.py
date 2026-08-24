"""导出发布链路（V1.4，Sprint 16）。

把 chapter / plan 数据组装成「作者发布」友好的纯文档产物：
- 整书 txt：所有 COMMITTED 章节按 chapter_no 升序拼接
- 单章 txt：指定 chapter 的正文（取该 chapter 最新 draft 的 content）
- 整书 docx：同上内容的 OOXML docx（手工打包，无 python-docx 依赖）
- 番茄投稿包 txt：前 N 章拼满 ~1 万字正文 + 分隔线 + 全书大纲（章节计划）

设计约束：
- 只读：全程只 SELECT，不写库、不调 LLM；
- 不引入新依赖：docx 用标准库 zipfile + 最小 OOXML 模板（足以被 Word/Pages 识别为合法 docx）；
- 与 packages/domain/chapter/service 对齐：COMMITTED 章节取该 chapter 下 drafts 最新版本（version DESC）
  的 content；大纲取 chapters.plan_json 的 chapter_plan 工作流输出（chapter_goal / core_conflict /
  turning_point / key_beats 等）。
"""

from __future__ import annotations

from .builder import (
    ExportScope,
    build_docx,
    build_fanqie_package,
    build_txt,
    plan_to_outline,
)
from .docx import build_minimal_docx

__all__ = [
    "ExportScope",
    "build_txt",
    "build_docx",
    "build_fanqie_package",
    "plan_to_outline",
    "build_minimal_docx",
]