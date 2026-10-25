"""capability 名册的**派生守卫**（2026-10-25 批 · M8）。

背景：``outline_batch_planner``（outline-batch 工作流的 planner agent）的
prompt 已在册（``docs/agents/prompts/outline_batch_planner-v1.md``，经 lifespan
sync 进 ``prompts`` 表），但 **capability 名册两处都没登记它** ⇒
``capability_for`` 走「未登记 ⇒ 默认 reasoning」的**隐式**兜底。

⚠️ 「隐式兜底」的问题不是行为错，而是**行为不可区分**：登记成 ``reasoning``
与压根没登记，运行期行为**逐位相同** ⇒ 于是「补登记」这件事既无判据也无
看守 ⇒ 下次新增 agent 会**再次**漏掉（本仓已发生过一次）。

⇒ 本守卫把「prompt 存在的 agent 必须显式登记」变成机器不变量。

三条断言（两侧都从**真实来源**推导，**不手抄**名册）：

1. ``docs/agents/prompts/*.md`` 文件名派生的每个 agent id ⇒ 必须出现在
   ``router.AGENT_CAPABILITY`` 的键里（不许靠隐式兜底）；
2. ``AGENT_CAPABILITY`` 的每个 agent ⇒ 必须出现在 ``router.CAPABILITY_LABELS``
   里**它所属 capability** 的 ``agents`` 列表中（漏一处 ⇒ 页面不展示它）；
3. 反向：``CAPABILITY_LABELS`` 各 ``agents`` 列表里的每个**纯 agent id**
   ⇒ 必须能在 ``AGENT_CAPABILITY`` 查到，且查到的 capability 与所在列表一致
   （展示别名如 ``writer(revise·改稿)`` 带括号标注，不参与本条）。

第四条断言（**第三份名册副本**的守卫）：

4. ``agent_runtime.prompts._AGENT_TO_CAPABILITY``（agent_runtime 为避免循环依赖
   独立维护的那份）⇒ 必须与 ``router.AGENT_CAPABILITY`` **全等**。
   ⚠️ 两侧都从**真实名册**读（不手抄、不复制字面量）；任一侧新增/改名/删项而
   另一侧漏改 ⇒ 本断言必红（此前该漂移只被 ``tests/unit/test_model_router.py``
   与 ``tests/integration/test_director_planner_prompt_registration.py`` 看着，
   本文件作为「名册守卫」的落点，必须自己也钉住）。
"""

from __future__ import annotations

import re
from pathlib import Path

from packages.core.agent_runtime import prompts as agent_runtime_prompts
from packages.core.model_router.router import AGENT_CAPABILITY, CAPABILITY_LABELS

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"

_VERSION_SUFFIX_RE = re.compile(r"-v\d+$")


def _agent_ids_from_prompt_files() -> set[str]:
    """从 ``docs/agents/prompts/*.md`` 的文件名派生 agent id 集合。

    ``director_planner-v4.md`` → ``director_planner``；
    ``deconstructor-aggregate-v0.md`` → ``deconstructor_aggregate``（连字符归一）。
    """
    ids: set[str] = set()
    for path in PROMPTS_DIR.glob("*.md"):
        stem = _VERSION_SUFFIX_RE.sub("", path.stem)
        ids.add(stem.replace("-", "_"))
    return ids


def _agent_ids_in_labels() -> list[tuple[str, str]]:
    """``CAPABILITY_LABELS`` 里的 (capability, agent_id) 对（跳过展示别名）。"""
    pairs: list[tuple[str, str]] = []
    for capability, meta in CAPABILITY_LABELS.items():
        for agent in meta.get("agents", []):
            if "(" in agent:  # 展示别名（如 writer(revise·改稿)）不是 agent id
                continue
            pairs.append((capability, agent))
    return pairs


def test_prompt_files_cover_a_non_empty_agent_set():
    """前提守卫：派生确实拿到了 agent（空集 ⇒ 下面的断言全是恒真）。"""
    ids = _agent_ids_from_prompt_files()
    assert len(ids) >= 15, f"从 prompt 文件派生的 agent 太少，路径可能变了：{sorted(ids)}"
    assert "outline_batch_planner" in ids


def test_every_prompt_agent_is_explicitly_registered():
    """★prompt 在册的 agent 必须**显式**登记 capability（不许靠隐式兜底）。"""
    missing = sorted(
        agent for agent in _agent_ids_from_prompt_files() if agent not in AGENT_CAPABILITY
    )
    assert not missing, (
        f"这些 agent 有 prompt 却没有 capability 登记（当前只会隐式兜底 reasoning）：{missing}"
    )


def test_registered_agent_appears_in_its_capability_label_list():
    """★映射与展示名单必须**两侧同步**（只改一侧 ⇒ 页面不展示它）。"""
    problems: list[str] = []
    for agent, capability in AGENT_CAPABILITY.items():
        meta = CAPABILITY_LABELS.get(capability)
        if meta is None:
            problems.append(f"{agent} → capability {capability!r} 在 CAPABILITY_LABELS 里不存在")
            continue
        if agent not in meta.get("agents", []):
            problems.append(f"{agent} 未出现在 capability {capability!r} 的 agents 列表里")
    assert not problems, "；".join(problems)


def test_label_agent_lists_have_no_orphan_ids():
    """★反向：展示名单里的纯 agent id 必须能在映射里查到，且 capability 一致。"""
    problems: list[str] = []
    for capability, agent in _agent_ids_in_labels():
        mapped = AGENT_CAPABILITY.get(agent)
        if mapped is None:
            problems.append(f"{agent}（在 {capability} 展示名单里）不在 AGENT_CAPABILITY 中")
        elif mapped != capability:
            problems.append(
                f"{agent} 映射到 {mapped!r}，却出现在 {capability!r} 的展示名单里"
            )
    assert not problems, "；".join(problems)


def test_outline_batch_planner_is_registered_as_reasoning():
    """★本批修复点：outline_batch_planner 显式登记为 reasoning（与原隐式语义一致）。"""
    assert AGENT_CAPABILITY.get("outline_batch_planner") == "reasoning"
    assert "outline_batch_planner" in CAPABILITY_LABELS["reasoning"]["agents"]


def test_agent_runtime_registry_matches_router_registry():
    """★第三份名册副本必须与 router 名册**全等**（两侧真实名册，不手抄）。

    ``agent_runtime.prompts._AGENT_TO_CAPABILITY`` 是为避免循环依赖而独立维护
    的那份拷贝 ⇒ 两处新增/改名/删项而只改一侧时，运行期 capability 归属会静默
    分叉（页面展示与实际绑定不一致）。
    """
    runtime_registry = agent_runtime_prompts._AGENT_TO_CAPABILITY
    assert runtime_registry == AGENT_CAPABILITY, (
        "agent_runtime._AGENT_TO_CAPABILITY 与 model_router.AGENT_CAPABILITY 漂移："
        f"仅 runtime 有的 agent={sorted(set(runtime_registry) - set(AGENT_CAPABILITY))}，"
        f"仅 router 有的 agent={sorted(set(AGENT_CAPABILITY) - set(runtime_registry))}，"
        f"取值不同的 agent="
        f"{sorted(a for a in set(runtime_registry) & set(AGENT_CAPABILITY) if runtime_registry[a] != AGENT_CAPABILITY[a])}"
    )


def test_agent_runtime_public_export_matches_private_registry():
    """★``prompts.AGENT_TO_CAPABILITY``（公开导出）必须与私有名册一致。

    公开导出是导入时刻的**拷贝** ⇒ 若两者日后分叉（有人只改其中一处），
    消费公开导出的调用方会拿到过期的 capability 归属。
    """
    assert agent_runtime_prompts.AGENT_TO_CAPABILITY == agent_runtime_prompts._AGENT_TO_CAPABILITY
