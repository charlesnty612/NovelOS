"""日志纪律守卫（V3.9 检修 R1 m1-m4/m10）。

两个纯日志修复没有行为副作用，用源码级守卫锁定（突变验证：撤修复必红）：

1. ``packages/workflows/chapter_commit/commit.py``：修复前用 ``logging.info(...)``
   走 root logger（日志 ``name`` 显示为 root，模块级过滤/归因失效）→ 必须用模块
   logger ``_log``；
2. ``packages/core/api/routers/workflows.py``：两处 ``[DEBUG]`` 排障行原走
   ``log.warning``（噪音混进告警通道）→ 降 debug 级，且不得再出现 ``[DEBUG]`` 前缀
   （级别本身已表达该语义）。

守卫是源码级检查而非行为断言：两条日志行都不改变业务输出，触发它们需要完整的
PAUSED run / observer 重试场景，成本远高于收益。
"""

from __future__ import annotations

import inspect
import re

from packages.core.api.routers import workflows as workflows_router
from packages.workflows.chapter_commit import commit as chapter_commit


def test_chapter_commit_repair_logs_use_module_logger():
    src = inspect.getsource(chapter_commit)
    assert "_log = logging.getLogger(__name__)" in src
    assert "logging.info(" not in src, "commit.py 修复日志不得再用 root logger 的 logging.info"
    assert src.count("_log.info(") >= 2, "两处 repair 日志都应走模块 logger"


def test_workflows_router_debug_lines_are_debug_level():
    src = inspect.getsource(workflows_router)
    # 排障行不得再以 warning 级 + [DEBUG] 前缀出现
    for line in src.splitlines():
        assert not ("log.warning(" in line and "[DEBUG]" in line), line
    # 两条排障行必须落在 debug 级（撤修复改回 warning 时正则级别不匹配 → 红）
    for message_prefix in ("auto_revise entry:", "resume final (async):"):
        m = re.search(
            r'log\.(warning|debug|info|error|exception)\(\s*\n?\s*"'
            + re.escape(message_prefix),
            src,
        )
        assert m, message_prefix
        assert m.group(1) == "debug", (message_prefix, m.group(1))
    # 守卫仅针对这两行排障日志；文件仍保留真实告警通道
    assert "log.warning(" in src
