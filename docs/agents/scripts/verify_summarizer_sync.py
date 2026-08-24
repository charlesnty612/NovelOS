"""summarizer prompt 注册机制验证脚本（V1.2.0 / Sprint 14-A）。

目的：在不动项目库 `data/novelos.db` 的前提下，证明
``docs/agents/prompts/summarizer-v1.md`` 满足 PromptRegistry.sync_from_docs
的解析规则，可被项目内 sync 机制接收并写入 agents / prompts 表。

用法：

```
python docs/agents/scripts/verify_summarizer_sync.py
```

行为：

1. 用 ``tempfile.TemporaryDirectory()`` 建一个全新隔离 SQLite 数据库。
2. 对该库执行 ``apply_migrations``（创建 agents / prompts 等全部业务表）。
3. 把 ``docs/agents/prompts`` 整目录（含 summarizer-v1.md）传给
   ``PromptRegistry.sync_from_docs`` 做一次完整 sync。
4. 校验：``agents`` 表含 summarizer 行；``prompts`` 表含 (summarizer, v1) ACTIVE 行；
   ``get_active_prompt('summarizer')`` 返回的 content 是 summarizer-v1.md 的全文。
5. 校验：再 sync 一次为幂等（``updated == []``）。
6. 打印 PASS / FAIL 摘要。

退出码：0=PASS；1=FAIL。

注意：本脚本不动 ``data/novelos.db``，所有 DB 操作均落在临时目录，
跑完自动清理（``TemporaryDirectory`` 退出时移除）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# 把仓库根加进 sys.path 以解析 packages.*
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from packages.core.agent_runtime import PromptRegistry  # noqa: E402
from packages.core.config import Settings  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402

PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"


def _row_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def main() -> int:
    if not PROMPTS_DIR.exists():
        print(f"FAIL: prompts dir missing: {PROMPTS_DIR}")
        return 1

    summarizer_md = PROMPTS_DIR / "summarizer-v1.md"
    if not summarizer_md.exists():
        print(f"FAIL: summarizer prompt file missing: {summarizer_md}")
        return 1

    with tempfile.TemporaryDirectory(prefix="novelos_verify_summarizer_") as tmp:
        tmp_path = Path(tmp)
        settings = Settings(data_dir=tmp_path, log_level="WARNING")
        apply_migrations(settings.db_path)

        registry = PromptRegistry(settings.db_path)
        result = registry.sync_from_docs(str(PROMPTS_DIR))

        scanned = result["scanned"]
        registered = result["registered"]
        agents = result["agents"]

        # 1. summarizer 出现在 scanned / registered / agents 列表
        assert ("summarizer", 1) in scanned, f"summarizer not scanned: {scanned}"
        assert ("summarizer", 1) in registered, f"summarizer not registered: {registered}"
        assert "summarizer" in agents, f"summarizer not in agents: {agents}"

        # 2. agents 表含 summarizer
        conn = get_connection(settings.db_path)
        try:
            agent_row = conn.execute(
                "SELECT agent_id, name, config_json FROM agents WHERE name = ?",
                ("summarizer",),
            ).fetchone()
            assert agent_row is not None, "summarizer row not found in agents table"
            agent_id = agent_row["agent_id"]
            cfg = json.loads(agent_row["config_json"])
            assert cfg.get("capability") == "reasoning", (
                f"summarizer capability mismatch: {cfg}"
            )

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

            stored_content = prompt_row["content"]
            assert stored_content.startswith(
                "# Summarizer Agent Prompt — `summarizer:v1`"
            ), "stored content does not start with expected header"
            assert stored_content == summarizer_md.read_text(encoding="utf-8"), (
                "stored content != file content"
            )

            # 4. get_active_prompt('summarizer') 能拿到
            prompt_id, version_label, content = registry.get_active_prompt("summarizer")
            assert version_label == "summarizer:v1", f"version_label mismatch: {version_label}"
            assert content == stored_content, "get_active_prompt content mismatch"

        finally:
            conn.close()

        # 5. 幂等性：第二次 sync 不应有 updated
        result2 = registry.sync_from_docs(str(PROMPTS_DIR))
        assert result2["updated"] == [], (
            f"second sync not idempotent: updated={result2['updated']}"
        )

        print("PASS: summarizer prompt registered via sync_from_docs")
        print(f"  scanned    = {scanned}")
        print(f"  agents     = {agents}")
        print(f"  prompt_id  = {prompt_id}")
        print(f"  version    = {version_label}")
        print(f"  chars      = {len(content)}")
        print(f"  updated(2) = {result2['updated']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())