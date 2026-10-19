"""skill（内部消费外部 skill）资源模块。

公共入口：
- :class:`packages.core.skill.service.SkillService` —— CRUD / 项目多 slot 有序绑定 /
  payload 校验（jsonschema 权威）；
- :func:`packages.core.skill.service.validate_payload` —— 纯函数校验（错误串格式
  ``"[schema] <path>: <message>"``）；
- :mod:`packages.core.skill.model` —— payload 消费子集模型 + schema 版本线常量；
- :mod:`packages.core.skill.queries` —— SQL 与行投影（无连接、无事务）；
- :mod:`packages.core.skill.consumers` —— 读侧投影：装配注入段（``skills`` 段 +
  ``applies_to`` 过滤 + 字符预算）与检查项旁路扫描（``review_report.warnings[]``）。

边界（与题材包 ``packages.core.genre`` 的分工）：
- skill 是**外部可移植的通用手艺**；题材包是**题材公约**。两者不共用表、
  不共用 schema 版本线、不共用注入段——skill 走**独立新段** ``payload["skills"]``。
- **三仓分离**：SKILL.md 全文与 references 全文属内容仓（NovelOS-Content），
  软件仓只承载**运行面 payload**；``source_path`` 仅作审计，软件层不解析。
- **不执行外部代码**：schema 刻意不提供 ``scripts`` 字段。
"""

from __future__ import annotations

from .consumers import (
    SKILL_RULE_ID_PREFIX,
    SKILLS_MAX_CHARS,
    SKILLS_TRUNCATED_KEY,
    build_skills_segment,
    collect_skill_checks,
    skill_applies_to,
    skill_block,
    skill_check_hits,
    skill_rule_id,
)
from .model import (
    SKILL_APPLIES_TO_CONSUMERS,
    SKILL_CHECK_ID_PATTERN,
    SKILL_ID_PATTERN,
    SKILL_SCHEMA_PATH,
    SKILL_SCHEMA_VERSION,
    Skill,
    SkillCheck,
    SkillCreate,
    SkillPayload,
    SkillReference,
    SkillSummary,
    SkillUpdate,
    parse_payload,
)
from .service import BindStatus, SkillService, validate_payload

__all__ = [
    "SKILLS_MAX_CHARS",
    "SKILLS_TRUNCATED_KEY",
    "SKILL_APPLIES_TO_CONSUMERS",
    "SKILL_CHECK_ID_PATTERN",
    "SKILL_ID_PATTERN",
    "SKILL_RULE_ID_PREFIX",
    "SKILL_SCHEMA_PATH",
    "SKILL_SCHEMA_VERSION",
    "BindStatus",
    "Skill",
    "SkillCheck",
    "SkillCreate",
    "SkillPayload",
    "SkillReference",
    "SkillService",
    "SkillSummary",
    "SkillUpdate",
    "build_skills_segment",
    "collect_skill_checks",
    "parse_payload",
    "skill_applies_to",
    "skill_block",
    "skill_check_hits",
    "skill_rule_id",
    "validate_payload",
]
