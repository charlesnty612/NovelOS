"""题材包（genre_pack）资源模块（题材库 P1a + 核销层 P1b + 读侧投影 P2）。

公共入口：
- :class:`packages.core.genre.service.GenrePackService` —— CRUD / 项目绑定 /
  payload 校验（jsonschema 权威）；
- :func:`packages.core.genre.service.validate_payload` —— 纯函数校验（错误串格式
  与 canon / state-delta 校验器同款）；
- :mod:`packages.core.genre.model` —— payload 消费子集模型 + schema 版本线常量；
- :mod:`packages.core.genre.queries` —— SQL 与行投影（无连接、无事务）；
- :func:`packages.core.genre.verifier.verify_chapter` —— 核销层 v1（配比偏差 +
  节奏红线，report-only 不阻断；``GENRE-`` 前缀 issue 恒 warning）；
- :mod:`packages.core.genre.consumers` —— 读侧投影（P2）：``opening_rules``
  （signing_check）/ ``critic_rubric``（critic + deep_review 的 genre_rubric）/
  ``forbidden_words`` + ``count_forbidden_word_hits``（basic_checks 禁词扫描）。

边界：与 ``reference_canons``（单部参照作品的描述性拆解产物）生命周期完全独立，
两者仅共享上下文注入管道这一模式（双 slot 并存）。详见模块 README。
"""

from __future__ import annotations

from .consumers import (
    CRITIC_RUBRIC_MAX_CHARS,
    CRITIC_RUBRIC_TRUNCATED_KEY,
    count_forbidden_word_hits,
    critic_rubric,
    forbidden_words,
    opening_rules,
)
from .model import (
    GENRE_PACK_SCHEMA_PATH,
    GENRE_PACK_SCHEMA_VERSION,
    GenrePack,
    GenrePackCreate,
    GenrePackPayload,
    GenrePackSummary,
    GenrePackUpdate,
    GenrePayoffType,
)
from .service import BindStatus, GenrePackService, validate_payload
from .verifier import (
    RATIO_DEVIATION_THRESHOLD,
    GenreCheckResult,
    GenreIssue,
    verify_chapter,
)

__all__ = [
    "CRITIC_RUBRIC_MAX_CHARS",
    "CRITIC_RUBRIC_TRUNCATED_KEY",
    "GENRE_PACK_SCHEMA_PATH",
    "GENRE_PACK_SCHEMA_VERSION",
    "RATIO_DEVIATION_THRESHOLD",
    "BindStatus",
    "GenreCheckResult",
    "GenreIssue",
    "GenrePack",
    "GenrePackCreate",
    "GenrePackPayload",
    "GenrePackService",
    "GenrePackSummary",
    "GenrePackUpdate",
    "GenrePayoffType",
    "count_forbidden_word_hits",
    "critic_rubric",
    "forbidden_words",
    "opening_rules",
    "validate_payload",
    "verify_chapter",
]
