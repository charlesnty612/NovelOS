"""chapter_write 包入口。"""

from .pipeline import WORKFLOW, register_workflow

register_workflow(WORKFLOW)

__all__ = ["WORKFLOW", "register_workflow"]
