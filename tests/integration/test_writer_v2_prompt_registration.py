"""F-10（2026-09-16）：author_intent 死参数的 writer 侧 prompt 契约注册。

覆盖（与 ``test_director_planner_prompt_registration.py`` 三条形态同款）：

1. ``docs/agents/prompts/writer-v2.md`` 经 ``PromptRegistry.sync_from_docs`` 注册为
   ``('writer', 2)`` 且为 writer 的最高 ACTIVE 版本（``get_active_prompt`` 取回
   ``writer:v2``，content 与文件逐字一致；v1 行保留在库内可回溯）；
2. 两处外科式追加防回删：§5 的 ``author_intent`` 输入契约段（硬性要求 / 冲突裁决
   「剧情服从 Scene Plan、风格与约束服从 author_intent」/ 不得原文抄进正文）+
   §6 规则 21（上下文标签、字段名与 payload 键名不得入文）；
3. 幂等重跑 ``updated == []``（内容未变不触发 UPDATE）。

provenance：v2 逐字节复制自 ``writer-v1.md`` 后仅追加上述两段（其余行保持逐字节
一致，便于 diff 审查）；v2 文件内的 ``prompt_version`` 示例值仍为 ``writer:v1``
（与 v1 底稿同源，不改文本），注册侧版本标签 = 文件名版本（v2），二者映射由本测试
钉住。触发背景：新书01 ch1-6 实证——write 端点收下的作者铁律全链无人读，且改稿轮
把内部字段名 ``recalled_passages`` 写进了正文。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.db import apply_migrations

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
PROMPT_FILE = PROMPTS_DIR / "writer-v2.md"

# 两处外科式追加的关键短语（缺失即回删：任一消失都意味着 prompt 不再承载对应契约）。
_PROMPT_MARKERS: tuple[str, ...] = (
    # §5 author_intent 输入契约段
    "关于 `author_intent`（2026-09-16 新增输入槽位）",
    "作者对本章的硬性要求",
    "剧情服从 Scene Plan、风格与约束服从 `author_intent`",
    "不得把它的原文抄进正文",
    # §6 规则 21：内部标识不入文
    "禁止上下文标签入文",
    "recalled_passages",
    "story_state",
    "chapter_goal",
    "payload 的键名",
    "即为废稿",
)


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "prompt_registry.db"
    apply_migrations(db_path)
    return db_path


def test_sync_from_docs_registers_writer_v2(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    registry = PromptRegistry(db_path)
    result = registry.sync_from_docs(PROMPTS_DIR)

    assert ("writer", 2) in result["scanned"]
    assert ("writer", 2) in result["registered"]
    assert "writer" in result["agents"]

    prompt_id, version_label, content = registry.get_active_prompt("writer")
    assert prompt_id.startswith("prm_")
    assert version_label == "writer:v2", "writer 的最高 ACTIVE 版本必须是 v2"
    assert content == PROMPT_FILE.read_text(encoding="utf-8")

    agents = {a["name"]: a for a in registry.list_agents()}
    assert agents["writer"]["role"] == "creative_writing"
    assert json.loads(agents["writer"]["config_json"]) == {
        "capability": "creative_writing"
    }
    # v1 行保留在库内（可回溯），v2 为最高版本
    versions = {p["version"] for p in registry.list_prompts("writer")}
    assert {"v1", "v2"} <= versions


def test_sync_from_docs_is_idempotent_for_writer_v2(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    registry = PromptRegistry(db_path)
    first = registry.sync_from_docs(PROMPTS_DIR)
    assert ("writer", 2) in first["registered"]

    second = registry.sync_from_docs(PROMPTS_DIR)
    assert ("writer", 2) in second["registered"]
    assert ("writer", 2) not in second["updated"], "内容未变不应触发 UPDATE"
    assert second["updated"] == [], f"整目录重跑不应有任何 updated：{second['updated']}"


def test_writer_v2_keeps_author_intent_and_no_internal_identifier_markers():
    text = PROMPT_FILE.read_text(encoding="utf-8")
    missing = [m for m in _PROMPT_MARKERS if m not in text]
    assert not missing, f"writer-v2.md 丢失新增契约短语：{missing}"
    # 外科式追加未破坏 v1 主体：§6.1 核销表机读契约仍在（管线按分隔行切分）
    assert "## 6.1 修订模式（mode='revise'）" in text
    assert "---REVISION-CHECKLIST---" in text
