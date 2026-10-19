"""**死参数守卫**：``skills`` 段必须被提示词声明，否则模型看不见它。

缺陷形状（本仓第二次同形）：``author_intent`` 事故——端点收下 → 写进 run ctx →
``build_writer_input()`` 无此形参 → 提示词模板里没有该字段 ⇒ **一个模型都没看见**，
全程无告警。

本仓的提示词是「一文件一整段」，**没有模板引擎、没有片段位**（19 份 prompt 零占位符）
⇒ 往 payload 加 ``skills`` 键**必须**有提示词声明它，否则就是死参数。

本文件钉住三条：

1. 5 个消费环节的**当前最高版本**提示词，其 §5 输入契约的 JSON 块里有**顶层**
   ``skills`` 键（解析验证，不是 grep——grep 会把正文里的提及也算进去）。
2. 提示词声明的 ``skills`` 子键集与**代码实际产出的段形状**逐字一致
   （``items`` / ``__skills_truncated__``）——契约漂移会当场现形。
3. 每份提示词都带「缺席语义 + 优先级裁决」说明段（``skills`` 段不得覆盖作者
   明确规定；``references`` 不是渐进披露）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packages.core.skill.consumers import SKILLS_TRUNCATED_KEY, build_skills_segment

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"

#: 5 个消费 ``skills`` 段的**角色**（与装配侧 ``_SKILL_CONSUMERS`` 逐字一致）。
SKILL_CONSUMER_AGENTS = ("writer", "director", "scene_planner", "critic", "deep_reviewer")

#: **合并体调用点**（2026-10-19 批次十三补）：``director_planner`` 一次调用同时扮演
#: director + scene_planner 两个角色 ⇒ 装配侧按 ``("director", "scene_planner")`` 重算
#: 并把同一段注入合并 payload。
#:
#: ⚠️ 它**不在** ``_SKILL_CONSUMERS`` 里（那是**角色**名册），**但它是活路径**——
#: chapter-plan 调的就是它（``chapter_plan/pipeline.py::_director_planner_node``），
#: 独立 director 节点已不再被 chapter-plan 调用 ⇒ **漏的恰好是主干**。
#:
#: 本条由实测缺口换来：首版实现给 5 份提示词升了版并声明了 ``skills`` 段，**唯独漏了
#: 合并体** ⇒ 合并 payload 里带着一个模型没被告知过的键（与「author_intent 死参数」
#: 事故同形：数据源存在 ≠ 已进装配，且**进了装配也不等于模型看得懂**）。
SKILL_MERGED_CALL_SITES = ("director_planner",)

#: 所有**必须声明** ``skills`` 段的提示词（角色 + 合并体调用点）。
SKILL_CONSUMER_PROMPTS = SKILL_CONSUMER_AGENTS + SKILL_MERGED_CALL_SITES

_JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)


def _latest_prompt(agent: str) -> Path:
    """该 agent 的**当前最高版本**提示词文件（数字序，不是字典序）。"""
    files = list(PROMPTS_DIR.glob(f"{agent}-v*.md"))
    assert files, f"{agent} 没有任何提示词文件（{PROMPTS_DIR}）"
    return max(files, key=lambda f: int(f.name.split("-v")[1].split(".")[0]))


def _contract_blocks(path: Path) -> list[dict]:
    """解析该文件的全部 ```json 块（跳过解析失败的块——示例片段不保证是合法 JSON）。"""
    text = path.read_text(encoding="utf-8")
    out: list[dict] = []
    for raw in _JSON_BLOCK_RE.findall(text):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def test_latest_prompt_of_every_skill_consumer_declares_skills_segment():
    """每个消费环节的**当前最高版本**提示词都必须声明 ``skills`` 段。

    为什么要钉「最高版本」而不是具体某个文件：将来有人升版（writer-v6.md…）却
    忘了声明 ``skills``，本用例当场红——否则那个版本一旦成为 ACTIVE，
    ``skills`` 段就静默退化成死参数（模型看不见）。
    """
    for agent in SKILL_CONSUMER_PROMPTS:
        path = _latest_prompt(agent)
        blocks = _contract_blocks(path)
        assert blocks, f"{path.name} 没有可解析的 §5 JSON 契约块"
        declaring = [b for b in blocks if "skills" in b]
        assert declaring, (
            f"{path.name}（{agent} 的当前最高版本）的输入契约里**没有顶层 skills 键**"
            f" ⇒ skills 段是死参数（模型看不见）"
        )
        skills = declaring[0]["skills"]
        assert "items" in skills, f"{path.name} 的 skills 段必须声明 items"
        assert SKILLS_TRUNCATED_KEY in skills, (
            f"{path.name} 的 skills 段必须声明 {SKILLS_TRUNCATED_KEY} 截断标记"
        )


def test_prompt_declared_shape_matches_code_shape():
    """提示词声明的 ``skills`` 子键集 == 代码实际产出的段形状（逐字一致）。

    契约漂移（提示词说 A、代码给 B）会让模型按不存在的字段行事；本用例当场现形。

    ⚠️ 代码的段形状**随是否截断而变**：未截断 = ``{items}``；截断 = 多一个
    ``__skills_truncated__``。故比对的是**两种形态的并集**。
    """
    row = {
        "skill_id": "skl_x",
        "name": "X",
        "version": 1,
        "payload": {
            "schema_version": "skill.v1.0.0",
            "name": "X",
            "instructions": "i",
        },
    }
    normal = build_skills_segment([row], "writer")
    assert normal is not None
    truncated = build_skills_segment(
        [{**row, "payload": {**row["payload"], "instructions": "长" * 5000}}],
        "writer",
    )
    assert truncated is not None
    code_keys = set(normal.keys()) | set(truncated.keys())
    assert SKILLS_TRUNCATED_KEY in code_keys, "夹具没造出截断形态，测试本身失效"

    for agent in SKILL_CONSUMER_PROMPTS:
        path = _latest_prompt(agent)
        blocks = _contract_blocks(path)
        declaring = [b for b in blocks if "skills" in b]
        assert declaring, f"{path.name} 未声明 skills 段"
        prompt_keys = set(declaring[0]["skills"].keys())
        assert prompt_keys == code_keys, (
            f"{path.name} 声明的 skills 子键 {sorted(prompt_keys)} "
            f"与代码实际产出 {sorted(code_keys)} 不一致"
        )


def test_every_skill_consumer_prompt_has_absence_and_priority_clause():
    """每份提示词都带「缺席语义 + 优先级裁决」说明段。"""
    for agent in SKILL_CONSUMER_PROMPTS:
        path = _latest_prompt(agent)
        text = path.read_text(encoding="utf-8")
        assert "**缺席语义**" in text, f"{path.name} 缺 skills 的缺席语义说明"
        assert "**优先级裁决**" in text, f"{path.name} 缺 skills 的优先级裁决说明"
        assert "不是**渐进披露" in text or "**不是**渐进披露" in text, (
            f"{path.name} 必须说明 references 不是渐进披露（模型无 tool use）"
        )


def test_skill_segment_is_not_merged_into_existing_sections():
    """``skills`` 是**独立新段**：不得塞进 ``genre_pack`` / ``style_constraints``。

    判别点在代码侧（payload 里两段各自独立），提示词侧只要求顶层键独立。
    """
    for agent in SKILL_CONSUMER_PROMPTS:
        path = _latest_prompt(agent)
        blocks = _contract_blocks(path)
        declaring = [b for b in blocks if "skills" in b]
        assert declaring, f"{path.name} 未声明 skills 段"
        top = declaring[0]
        for other in ("genre_pack", "style_constraints"):
            if other in top:
                assert "skills" not in top[other], (
                    f"{path.name}：skills 不得嵌进 {other} 段（必须是独立新段）"
                )
