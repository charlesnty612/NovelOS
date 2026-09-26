"""Chapter 领域包（Sprint 1 + Sprint 5 drafts 扩展）。"""

from __future__ import annotations

from .draft_resolver import LATEST_DRAFT_ORDER, resolve_draft
from .models import (
    ALLOWED_NEXT,
    Chapter,
    ChapterCreate,
    ChapterStatus,
    ChapterUpdate,
    Draft,
    DraftCreate,
)
from .service import (
    ChapterError,
    ChapterNumberConflict,
    ChapterService,
    ChapterTransitionError,
    DraftStatusNotAllowed,
    RevisionNoteStatusNotAllowed,
)

__all__ = [
    "ALLOWED_NEXT",
    "Chapter",
    "ChapterCreate",
    "ChapterError",
    "ChapterNumberConflict",
    "ChapterStatus",
    "ChapterTransitionError",
    "ChapterUpdate",
    "ChapterService",
    "Draft",
    "DraftCreate",
    "DraftStatusNotAllowed",
    "LATEST_DRAFT_ORDER",
    "RevisionNoteStatusNotAllowed",
    "resolve_draft",
]
