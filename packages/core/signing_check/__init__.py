"""番茄签约体检模块（V2.0 Wave D — 番茄签约体检）。

对外接口：
- :func:`run_checks` —— 纯函数，输入章节与主角名，输出 :class:`CheckItem` 列表；
- :func:`run_signing_check` —— 入口服务，串联 DB 读数与 :func:`run_checks`；
- :func:`format_summary` —— 把检查结果渲染为可嵌入导出包的纯文本摘要。

设计要点（任务书给死）：
- 纯启发式规则、无 LLM、无新依赖（不引入 jieba 等中文分词）；
- 检查函数不依赖 DB IO；数据组装由 :mod:`service` 层完成；
- 平台依据：番茄男频公开投稿规则（黄金三章、字数节点、签约窗口 2/5/8 万）。
"""

from __future__ import annotations

from .checks import CheckItem, run_checks
from .service import format_summary, run_signing_check

__all__ = [
    "CheckItem",
    "run_checks",
    "run_signing_check",
    "format_summary",
]
