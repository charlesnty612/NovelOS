"""summarizer prompt 注册机制正式集成测试（V1.4 / Sprint 14-A 固化）。

前身：``docs/agents/scripts/verify_summarizer_sync.py``（ad-hoc 验证脚本，隔离 tmp DB）。
本测试把脚本里的核心断言固化进 pytest：

1. 用 ``tempfile.TemporaryDirectory()`` 建一个全新隔离 SQLite 数据库（不动项目库）。
2. 执行 ``apply_migrations``（创建 agents / prompts 等全部业务表）。
3. 对 ``docs/agents/prompts/`` 目录调 ``PromptRegistry.sync_from_docs`` 做一次完整 sync。
4. 校验 ``agents`` 表含 summarizer 行（capability=reasoning）；``prompts`` 表含
   (summarizer, v1) ACTIVE 行；``get_active_prompt('summarizer')`` 返回的 content 是
   summarizer-v1.md 的全文（以约定头部开头）。
5. 校验：再 sync 一次为幂等（``updated == []``）。

fixture 模式：参考 ``tests/integration/test_health.py`` 与
``tests/integration/test_chapters_api.py``（临时目录 + apply_migrations + 直连
PromptRegistry / get_connection，不依赖 HTTP 层）。

落地动机：
- 任务书 V1.4 要求把 ad-hoc 脚本固化为正式测试，与 CI 集成后防止 summarizer prompt
  漂移（agent capability / prompt 内容 / ACTIVE 行 缺失）而悄悄 FAILED。
- 与 chapter_commit pipeline 的 ``_summarize_node`` 共享 ``get_active_prompt('summarizer')``
  调用契约，因此 ACTIVE 行缺失会直接让章节提交后 summarize 节点降级为 failed（虽不阻断
  commit，但摘要缺失会触发回归）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from packages.core.agent_runtime import PromptRegistry
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
SUMMARIZER_MD = PROMPTS_DIR / "summarizer-v1.md"


def _make_isolated_db() -> tuple[str, Path, "tempfile.TemporaryDirectory[str]"]:
    """建一个全新隔离 SQLite DB，返回 ``(db_path_str, tmp_path, tmp_dir_handle)``。

    使用 :class:`tempfile.TemporaryDirectory` 让测试结束自动清理 tmp 目录，
    避免污染项目库 ``data/novelos.db``。
    """
    tmp_dir = tempfile.TemporaryDirectory(prefix="novelos_test_summarizer_")
    tmp_path = Path(tmp_dir.name)
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path), tmp_path, tmp_dir


def test_summarizer_prompt_registered_after_sync():
    """sync_from_docs 后：scanned/registered/agents/ACTIVE 行 + content 与源文件一致。"""
    assert PROMPTS_DIR.exists(), f"prompts dir missing: {PROMPTS_DIR}"
    assert SUMMARIZER_MD.exists(), f"summarizer prompt file missing: {SUMMARIZER_MD}"

    db_path, _tmp_path, tmp_dir = _make_isolated_db()
    try:
        registry = PromptRegistry(db_path)
        result = registry.sync_from_docs(str(PROMPTS_DIR))

        scanned = result["scanned"]
        registered = result["registered"]
        agents = result["agents"]

        # 1. summarizer 出现在 scanned / registered / agents 列表
        assert ("summarizer", 1) in scanned, f"summarizer not scanned: {scanned}"
        assert ("summarizer", 1) in registered, f"summarizer not registered: {registered}"
        assert "summarizer" in agents, f"summarizer not in agents: {agents}"

        # 2. agents 表含 summarizer（capability=reasoning）
        conn = get_connection(db_path)
        try:
            agent_row = conn.execute(
                "SELECT agent_id, name, config_json FROM agents WHERE name = ?",
                ("summarizer",),
            ).fetchone()
            assert agent_row is not None, "summarizer row not found in agents table"
            cfg = json.loads(agent_row["config_json"])
            assert cfg.get("capability") == "reasoning", (
                f"summarizer capability mismatch: {cfg}"
            )
            agent_id = agent_row["agent_id"]

            # 3. prompts 表含 (summarizer, v1) ACTIVE 行
            prompt_row = conn.execute(
                """
                SELECT prompt_id, version, content, status
                FROM prompts
                WHERE agent_id = ? AND version = ?
                """,
                (agent_id, "v1"),
            ).fetchone()
            assert prompt_row is not None, "summarizer:v1 prompt row not found"
            assert prompt_row["status"] == "ACTIVE", (
                f"summarizer:v1 status mismatch: {prompt_row['status']}"
            )

            # 4. 存储内容与源文件一致（约定头部开头 + 完整内容匹配）
            stored_content = prompt_row["content"]
            assert stored_content.startswith(
                "# Summarizer Agent Prompt — `summarizer:v1`"
            ), "stored content does not start with expected header"
            assert stored_content == SUMMARIZER_MD.read_text(encoding="utf-8"), (
                "stored content != file content"
            )

            # 5. get_active_prompt('summarizer') 返回一致
            prompt_id, version_label, content = registry.get_active_prompt("summarizer")
            assert version_label == "summarizer:v1", (
                f"version_label mismatch: {version_label}"
            )
            assert content == stored_content, "get_active_prompt content mismatch"
        finally:
            conn.close()
    finally:
        tmp_dir.cleanup()


def test_summarizer_prompt_sync_is_idempotent():
    """二次 sync_from_docs 必须幂等：updated == []，行数 / 内容不变。"""
    db_path, _tmp_path, tmp_dir = _make_isolated_db()
    try:
        registry = PromptRegistry(db_path)

        # 第一次 sync
        first = registry.sync_from_docs(str(PROMPTS_DIR))
        first_registered = list(first["registered"])
        first_updated = list(first["updated"])

        # 第二次 sync —— 不应新增也不应变更
        second = registry.sync_from_docs(str(PROMPTS_DIR))
        assert second["updated"] == [], (
            f"second sync not idempotent: updated={second['updated']}"
        )
        assert second["registered"] == first_registered, (
            f"second sync registered drift: first={first_registered} second={second['registered']}"
        )
        assert second["updated"] == first_updated, (
            "first sync should not mark existing summarizer:v1 as updated"
        )

        # prompts 表 summarizer:v1 仍唯一 ACTIVE 行
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                """
                SELECT p.prompt_id, p.status
                FROM prompts p
                JOIN agents a ON a.agent_id = p.agent_id
                WHERE a.name = ?
                """,
                ("summarizer",),
            ).fetchall()
            assert len(rows) == 1, f"summarizer prompts rowcount drift: {rows}"
            assert rows[0]["status"] == "ACTIVE", rows[0]["status"]
        finally:
            conn.close()
    finally:
        tmp_dir.cleanup()


def test_summarizer_get_active_prompt_returns_non_empty():
    """契约：summarizer 的 ACTIVE prompt content 必须非空（pipeline summarize 节点依赖）。"""
    db_path, _tmp_path, tmp_dir = _make_isolated_db()
    try:
        registry = PromptRegistry(db_path)
        registry.sync_from_docs(str(PROMPTS_DIR))
        prompt_id, version_label, content = registry.get_active_prompt("summarizer")
        assert prompt_id, "prompt_id should not be empty"
        assert version_label == "summarizer:v1"
        assert content and content.strip(), "summarizer prompt content must be non-empty"
        # 与 docs/agents/prompts/summarizer-v1.md 完整一致
        assert content == SUMMARIZER_MD.read_text(encoding="utf-8"), (
            "summarizer active prompt drifted from docs/agents/prompts/summarizer-v1.md"
        )
    finally:
        tmp_dir.cleanup()
