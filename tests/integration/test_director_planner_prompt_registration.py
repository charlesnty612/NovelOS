"""P1 规划合并：director_planner prompt 注册 + capability 登记（上线前事项 3）。

覆盖：

1. ``docs/agents/prompts/director_planner-v1.md`` 经 ``PromptRegistry.sync_from_docs``
   注册为 ``('director_planner', 1)``，``get_active_prompt`` 取回内容与文件逐字一致
   （版本标签 ``director_planner:v1``）；
2. capability 三处登记一致：``agent_runtime.prompts._AGENT_TO_CAPABILITY`` ==
   ``model_router.AGENT_CAPABILITY``（两者必须相等，见 prompts.py 模块注释），
   且 ``director_planner`` 归 ``creative_writing``、在 ``CAPABILITY_LABELS`` 正文写作组内；
3. prompt 文件关键契约短语防回删（v0.2 加固：单对象硬约束 / 空输入白名单 /
   E-MRG-16 / E-MRG-17 / 配比分摊口径）——与 ``test_genre_prompt_contracts`` 同款口径。

prompt 内容 provenance：逐字节取自 `_refs/p1_merged_prompt_v2.md` 的 ``PROMPT-BEGIN`` /
``PROMPT-END`` 区间（v0.2，25545 字符 / sha256 前缀 ``28cdcb2150663075``——即二次 A/B
回放实测通过的定稿文本）。prompt 正文内的 ``prompt_version`` 示例值仍是
``director_planner:v0.2-draft``（与 A/B 证据同源，不改文本以保证 hash 可核对）；
注册侧版本标签 = 文件名版本 ``director_planner:v1``，二者映射关系由本测试钉住。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.agent_runtime.prompts import (
    AGENT_TO_CAPABILITY,
    PromptRegistry,
    capability_for,
)
from packages.core.db import apply_migrations
from packages.core.model_router import router as model_router
from packages.core.model_router.router import (
    AGENT_CAPABILITY,
    CAPABILITY_LABELS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
PROMPT_FILE = PROMPTS_DIR / "director_planner-v1.md"

# v0.2 加固关键短语（缺失即回删：任一消失都意味着 prompt 不再承载对应硬约束）。
_PROMPT_MARKERS: tuple[str, ...] = (
    # 单对象硬约束（解析兜底对应的 prompt 侧约束）
    "禁止输出两个或更多相邻 JSON 对象",
    "整段输出必须恰好是一个顶层 JSON 对象",
    # 空输入禁编造（白名单校验对应的 prompt 侧约束）
    "输入为空数组或键缺席时，两者必须为 `[]`",
    "禁止把 faction_id / character_id / location_id 填入 `hook_id` 或 `debt_id` 字段",
    # 验收规则
    "E-MRG-16",
    "E-MRG-17",
    # 配比分摊口径（P3）
    "ratio_declarations",
    "分摊口径（v0.2 加固）",
    # 双契约输出结构
    '"scene_plan"',
    "scene-plan.v1",
)


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "prompt_registry.db"
    apply_migrations(db_path)
    return db_path


def test_sync_from_docs_registers_director_planner(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    registry = PromptRegistry(db_path)
    result = registry.sync_from_docs(PROMPTS_DIR)

    assert ("director_planner", 1) in result["scanned"]
    assert ("director_planner", 1) in result["registered"]
    assert "director_planner" in result["agents"]

    prompt_id, version_label, content = registry.get_active_prompt("director_planner")
    assert prompt_id.startswith("prm_")
    assert version_label == "director_planner:v1"
    assert content == PROMPT_FILE.read_text(encoding="utf-8")

    agents = {a["name"]: a for a in registry.list_agents()}
    assert "director_planner" in agents
    assert agents["director_planner"]["role"] == "creative_writing"
    assert json.loads(agents["director_planner"]["config_json"]) == {
        "capability": "creative_writing"
    }


def test_sync_from_docs_is_idempotent_for_director_planner(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    registry = PromptRegistry(db_path)
    first = registry.sync_from_docs(PROMPTS_DIR)
    assert ("director_planner", 1) in first["registered"]

    second = registry.sync_from_docs(PROMPTS_DIR)
    assert ("director_planner", 1) in second["registered"]
    assert ("director_planner", 1) not in second["updated"], "内容未变不应触发 UPDATE"


def test_director_planner_prompt_keeps_v02_hardening_markers():
    text = PROMPT_FILE.read_text(encoding="utf-8")
    missing = [m for m in _PROMPT_MARKERS if m not in text]
    assert not missing, f"director_planner-v1.md 丢失 v0.2 加固短语：{missing}"
    # 双契约段（导演 required / 场景 required）都在
    section = text.split("## 7. Output Schema", 1)[1]
    assert "scene_plan" in section
    assert "scenes[]" in section


def test_director_planner_capability_registration_consistent():
    """capability 双表必须一致（agent_runtime 独立维护一份以避免循环依赖）。"""
    assert AGENT_TO_CAPABILITY == AGENT_CAPABILITY, (
        "agent_runtime._AGENT_TO_CAPABILITY 与 model_router.AGENT_CAPABILITY 漂移"
    )
    assert capability_for("director_planner") == "creative_writing"
    assert model_router.capability_for("director_planner") == "creative_writing"
    assert "director_planner" in CAPABILITY_LABELS["creative_writing"]["agents"]
    # 原 director 仍在 reasoning 组（chapter-plan 不再调用它，但注册与映射不删）
    assert "director" in CAPABILITY_LABELS["reasoning"]["agents"]
