"""Context Engine（Sprint 4-A + Sprint 13 下半）。

- :func:`build_director_input` — 组装 Director 输入（按 agent-contracts §3.1）。
- :func:`build_writer_input` — 组装 Writer 输入（按 §4.1）。
- :func:`build_observer_input` — 组装 Observer 输入（按 §5.1）。
- :func:`preview_context` — dry-run 预览（按 L0/L1/L2 分层返回 token 估算 + 条目清单；只读、不调 LLM、不写库）。

MVP 简化版（README 注明 deviation）：不做 L0-L9 token 裁剪；按字段全量塞入 context，完整版留后续 Sprint。
"""

from .builders import (
    build_director_input,
    build_observer_input,
    build_writer_input,
)
from .preview import preview_context

__all__ = [
    "build_director_input",
    "build_writer_input",
    "build_observer_input",
    "preview_context",
]
