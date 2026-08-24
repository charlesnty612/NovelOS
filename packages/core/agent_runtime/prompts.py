r"""Prompt Registry（Sprint 3）。

职责：
- 从 ``docs/agents/prompts`` 扫描所有 ``<agent>-v<N>.md`` 文件，upsert 到
  ``agents`` / ``prompts`` 表。
- 按 agent 名取 ACTIVE 行最高版本号。

设计要点：
- 幂等：同名同 ``version`` 重复 sync 时更新 ``content`` 与 ``updated_at``，不新建行。
- 全部 prompt 都注册（director / writer / observer / arbiter / deconstructor_chapter /
  deconstructor_aggregate 等）。
- 文件名解析正则：``^(?P<agent>[a-zA-Z][a-zA-Z_-]*)-v(?P<n>\d+)\.md$``；含连字符的
  agent 名（如 ``deconstructor-chapter``）注册时**规范化**为下划线
  （``deconstructor_chapter``），与文件内 Prompt 声明的 ``"agent": "..."`` 字段一致。
- ``get_active_prompt(agent_name)``：取该 agent 所有 ACTIVE 行的最高 ``version``，按
  ``CAST(substr(version, 2) AS INTEGER) DESC`` 数字排序（而非字典序），确保
  ``v9 > v10`` 不被字典误判。
  ``version`` 字段形如 ``"v9"`` / ``"v10"``（DB prompts.version 列存数字形式）；
  ``prompt_version`` 字段（在 ai_call_logs 中使用）拼接为 ``f"{agent_name}:v{n}"``。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .exceptions import PromptNotFoundError

# agents 表 status / role 列无 CHECK；capability 来自 agents.config_json
_FILE_RE = re.compile(r"^(?P<agent>[a-zA-Z][a-zA-Z_-]*)-v(?P<n>\d+)\.md$")

# Agent → Capability 映射（与 model_router.AGENT_CAPABILITY 保持一致；这里独立维护避免循环依赖）
_AGENT_TO_CAPABILITY: dict[str, str] = {
    "director": "reasoning",
    "observer": "reasoning",
    "writer": "creative_writing",
    "arbiter": "reasoning",
    "deconstructor_chapter": "reasoning",
    "deconstructor_aggregate": "reasoning",
    "summarizer": "reasoning",  # Sprint 14-A 章节摘要链
    "critic": "reasoning",       # V1.3 LLM 评审员
    # 其它 agent 默认 reasoning（Sprint 3 MVP 不细化）
}


def _capability_for(agent_name: str) -> str:
    return _AGENT_TO_CAPABILITY.get(agent_name, "reasoning")


def _normalize_agent_name(raw: str) -> str:
    """把文件名解析出的 agent 名（可能含连字符）规范化为下划线形式。

    例：``deconstructor-chapter`` → ``deconstructor_chapter``，
    ``deconstructor-aggregate`` → ``deconstructor_aggregate``。
    """
    return raw.replace("-", "_")


# ---------------------------------------------------------------------------
# PromptRegistry
# ---------------------------------------------------------------------------


class PromptRegistry:
    """管理 ``agents`` / ``prompts`` 表的 upsert 与查询。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- sync_from_docs
    def sync_from_docs(self, docs_dir: Path | str = "docs/agents/prompts") -> dict[str, Any]:
        """扫描 ``docs_dir`` 下 ``*-v<N>.md``，upsert agents / prompts。

        返回：
        - ``scanned``：扫到的 (agent, version) 元组列表（agent 名已规范化）。
        - ``registered``：(agent, version) 元组列表（含已有 / 新增）。
        - ``updated``：(agent, version) 元组列表（content 变化的子集）。
        - ``agents``：agent 名列表（去重，按字母升序）。
        """
        docs_path = Path(docs_dir)
        scanned: list[tuple[str, int]] = []
        registered: list[tuple[str, int]] = []
        updated: list[tuple[str, int]] = []
        agents_set: set[str] = set()

        if docs_path.exists():
            for entry in sorted(docs_path.iterdir()):
                if not entry.is_file():
                    continue
                m = _FILE_RE.match(entry.name)
                if not m:
                    continue
                agent = _normalize_agent_name(m.group("agent"))
                n = int(m.group("n"))
                content = entry.read_text(encoding="utf-8")
                scanned.append((agent, n))
                agents_set.add(agent)
                reg, was_update = self._upsert_prompt(agent, n, content)
                registered.append(reg)
                if was_update:
                    updated.append(reg)

        return {
            "scanned": scanned,
            "registered": registered,
            "updated": updated,
            "agents": sorted(agents_set),
        }

    # -------------------------------------------------------------- get_active_prompt
    def get_active_prompt(self, agent_name: str) -> tuple[str, str, str]:
        """返回 ``(prompt_id, version_label, content)``：取该 agent 的 ACTIVE 行最高版本。

        ``version`` 形如 ``"v3"`` / ``"v10"``，按 ``CAST(substr(version,2) AS INTEGER)``
        数字排序取最大——避免字典序下 ``"v9" > "v10"`` 的误判。

        ``version_label`` 形如 ``"director:v3"``，与 ai_call_logs.prompt_version 一致。
        无任何 ACTIVE 行 → :class:`PromptNotFoundError`。
        """
        conn = get_connection(self.db_path)
        try:
            agent_id = self._get_agent_id(conn, agent_name)
            if agent_id is None:
                raise PromptNotFoundError(agent_name)
            row = conn.execute(
                """
                SELECT prompt_id, version, content
                FROM prompts
                WHERE agent_id = ? AND status = 'ACTIVE'
                ORDER BY CAST(substr(version, 2) AS INTEGER) DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PromptNotFoundError(agent_name)
        return row["prompt_id"], f"{agent_name}:{row['version']}", row["content"]

    # -------------------------------------------------------------- list_agents
    def list_agents(self) -> list[dict[str, Any]]:
        """列出所有已注册的 agents（按 name 升序）。"""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT agent_id, name, role, config_json, created_at, updated_at
                FROM agents
                ORDER BY name ASC
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- list_prompts
    def list_prompts(self, agent_name: str) -> list[dict[str, Any]]:
        """列出指定 agent 的所有 prompts（按 version 数字降序）。"""
        conn = get_connection(self.db_path)
        try:
            agent_id = self._get_agent_id(conn, agent_name)
            if agent_id is None:
                return []
            rows = conn.execute(
                """
                SELECT prompt_id, agent_id, version, content, status, created_at, updated_at
                FROM prompts
                WHERE agent_id = ?
                ORDER BY CAST(substr(version, 2) AS INTEGER) DESC
                """,
                (agent_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- helpers
    def _get_agent_id(self, conn: sqlite3.Connection, agent_name: str) -> str | None:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE name = ?", (agent_name,)
        ).fetchone()
        return row["agent_id"] if row else None

    def _upsert_agent(self, conn: sqlite3.Connection, agent_name: str) -> str:
        """确保 agent 行存在；返回 agent_id。"""
        existing = self._get_agent_id(conn, agent_name)
        if existing is not None:
            return existing
        agent_id = new_id("ag")
        now = now_iso()
        cap = _capability_for(agent_name)
        conn.execute(
            """
            INSERT INTO agents (agent_id, name, role, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (agent_id, agent_name, cap, json.dumps({"capability": cap}, ensure_ascii=False), now, now),
        )
        return agent_id

    def _upsert_prompt(
        self, agent_name: str, version_n: int, content: str
    ) -> tuple[tuple[str, int], bool]:
        """upsert 一个 (agent, version) → prompt 行。

        返回 ``((agent_name, version_n), was_updated)``：
        - 新增 → ``was_updated=False``。
        - content 变化（已存在）→ ``was_updated=True``，content 与 updated_at 已刷新。
        - content 完全相同 → ``was_updated=False``（updated_at 也刷一下便于审计）。
        """
        version_label = f"v{version_n}"
        now = now_iso()
        conn = get_connection(self.db_path)
        try:
            agent_id = self._upsert_agent(conn, agent_name)
            row = conn.execute(
                """
                SELECT prompt_id, content FROM prompts
                WHERE agent_id = ? AND version = ?
                """,
                (agent_id, version_label),
            ).fetchone()
            if row is None:
                prompt_id = new_id("prm")
                conn.execute(
                    """
                    INSERT INTO prompts
                        (prompt_id, agent_id, version, content, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (prompt_id, agent_id, version_label, content, now, now),
                )
                conn.commit()
                return ((agent_name, version_n), False)
            was_updated = row["content"] != content
            conn.execute(
                "UPDATE prompts SET content = ?, updated_at = ?, status = 'ACTIVE' WHERE prompt_id = ?",
                (content, now, row["prompt_id"]),
            )
            conn.commit()
            return ((agent_name, version_n), was_updated)
        finally:
            conn.close()


__all__ = ["PromptRegistry", "AGENT_TO_CAPABILITY", "capability_for", "normalize_agent_name"]


def capability_for(agent_name: str) -> str:
    """公开接口：按 agent 名取 capability（与 :func:`packages.core.model_router.capability_for` 对齐）。"""
    return _capability_for(agent_name)


def normalize_agent_name(raw: str) -> str:
    """公开接口：把文件名解析出的 agent 名（可能含连字符）规范化为下划线形式。"""
    return _normalize_agent_name(raw)


AGENT_TO_CAPABILITY: dict[str, str] = dict(_AGENT_TO_CAPABILITY)
"""Agent 名 → capability 名（与 :data:`packages.core.model_router.AGENT_CAPABILITY` 必须相等）。"""
