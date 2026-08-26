"""domain.volume 公共导出（V3.4 多卷与规模——组织层）。

承载 volumes 表的领域模型与 Service；与 chapters.volume_id 外键列配套。
本版明确**不做按卷拆快照**（O-1 滚动窗口已实现上下文有界）；
terminal_snapshot_json 仅在 seal 时冻结 story_states 最新快照作归档。
"""
from __future__ import annotations

from .models import (
    Volume,
    VolumeAssignRequest,
    VolumeCreate,
    VolumeListItem,
    VolumeStatus,
    VolumeUpdate,
)
from .service import (
    VolumeConflictError,
    VolumeError,
    VolumeNotFoundError,
    VolumeSealedError,
    VolumeService,
    VolumeValidationError,
)

__all__ = [
    "VolumeStatus",
    "VolumeCreate",
    "VolumeUpdate",
    "VolumeAssignRequest",
    "Volume",
    "VolumeListItem",
    "VolumeService",
    "VolumeError",
    "VolumeNotFoundError",
    "VolumeConflictError",
    "VolumeValidationError",
    "VolumeSealedError",
]
