"""chapter_plan 工作流（Sprint 4-A；P1 规划合并 2026-09-14）。

节点列表：
- ``build_ctx`` (Transform) —— 调
  :func:`packages.workflows.chapter_plan.planner_input.build_director_planner_input`
  组装**合并输入**（director 段 ∪ 规划期段：可用角色 / 地点 / 文风 / 题材包配比声明）。
- ``director_planner`` (AI) —— 调 :func:`packages.core.agent_runtime.runner.run_agent` 跑
  ``director_planner`` agent：**一次调用**产出导演计划 + ``scene_plan`` 双契约
  （P1 前是 director 一次 + chapter-write 的 scene_planner 一次）。
  mock_providers 透传自 ctx（旧键 ``director`` 兼容，见 ``_director_planner_node``）。
- ``save_plan`` (State) —— 写 chapters.plan_json（导演段）+ **同一事务内**把
  ``scene_plan`` 落 ``chapter_scene_plans``（迁移 0027）；本轮无 scene_plan（计划-only
  降级）时删除旧行，避免陈旧场景配新计划。chapters.status 不变（保持 PLANNED）。

返回 ``run_id``（由 WorkflowEngine.start_with_nodes 给出）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from packages.core.agent_runtime.prompts import active_prompt_label
from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.genre.target_words import (
    pack_chapter_words_target,
    resolve_target_word_count,
)
from packages.core.model_router.router import capability_for
from packages.core.quality.wordcount import DEFAULT_TARGET_WORD_COUNT
from packages.core.workflow_runtime.engine import WorkflowNode

from .planner_input import (
    DIRECTOR_PLANNER_PROMPT_VERSION,
    build_director_planner_input,
)
from .scene_plan_store import clear_scene_plan, save_scene_plan

_log = logging.getLogger(__name__)


def _build_ctx_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Transform：组装 director_planner 合并输入（不调 LLM）。"""
    db_path = ctx["db_path"]
    project_id = ctx["project_id"]
    chapter_id = ctx["chapter_id"]
    intent = ctx.get("author_intent") or ""
    payload = build_director_planner_input(
        db_path,
        project_id,
        chapter_id,
        intent,
        # target 走共享解析（ctx 显式 → 题材包 pacing.chapter_words.target → 默认），
        # 与 write/review/scene_planner 同链同值（target 题材包化补齐 plan 侧）。
        target_word_count=resolve_target_word_count(
            db_path, chapter_id, explicit=ctx.get("target_word_count") or None,
            project_id=project_id,
        ),
        expected_role=ctx.get("expected_role", "setup"),
    )
    # 2026-10-15 批次（六）G9：装配级 token 预算元字段提到顶层（与 writer 侧同款理由
    # ——``_cap_checkpoint_payload`` 按体积从大到小截断顶层键，大容器先被整个替换，
    # 预算越超 meta 越先消失）。详见 chapter_write/pipeline.py 同名键的注释。
    # ⚠️ 只提键：不改 payload、不改 prompt、不改缓存键语义。
    return {
        "director_planner_input": payload,
        "director_planner_assembly_meta": payload.get("_assembly_meta"),
    }


def _director_planner_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：调 director_planner agent（合并调用：导演计划 + scene_plan 一次产出）。

    mock 通道：``mock_providers["director_planner"]`` 优先；**旧键 ``director`` 兼容**——
    本节点是原 director 节点的合并升级版，既有测试 / eval golden / smoke 脚本以
    ``director`` 键提供计划脚本（计划-only 形态），保留只读兼容避免二十余处无测试价值的
    批量改名；新代码 / 新脚本请直接用 ``director_planner`` 键。

    ``scene_plan_status`` 判定（供 save_plan 与 run 详情观测）：

    - ``ok``：输出含非空 ``scene_plan.scenes``（结构由
      :func:`...structured_output._validate_director_planner` 已校验）；
    - ``missing_in_output``：计划-only 输出（prompt 要求双契约，但计划可用不判失败）
      ——计划照常落库，scene 段留给 chapter-write 的既有 scene_planner 路径补齐。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    payload = ctx["director_planner_input"]
    mocks = ctx.get("mock_providers") or {}
    mock_script = mocks.get("director_planner")
    if mock_script is None:
        mock_script = mocks.get("director")
    out = run_agent(
        db_path,
        "director_planner",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="director_planner",
        mock_script=mock_script,
        profile_id=(ctx.get("model_overrides") or {}).get(
            capability_for("director_planner")
        ),
    )
    scenes = (out.get("scene_plan") or {}).get("scenes") if isinstance(out, dict) else None
    scene_plan_status = "ok" if (isinstance(scenes, list) and scenes) else "missing_in_output"
    if scene_plan_status != "ok":
        _log.warning(
            "chapter_plan.director_planner missing scene_plan (plan-only output): "
            "chapter_id=%s run_id=%s — chapter-write 将走 scene_planner 降级路径",
            ctx.get("chapter_id"), run_id,
        )
    return {
        "director_planner_output": out,
        "scene_plan_status": scene_plan_status,
    }


def _save_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：把导演段写入 chapters.plan_json + scene_plan 落 chapter_scene_plans。

    保字数契约：若章节当前 plan_json 已含 ``expected_word_count``（>0），
    重生成时继承该值，避免误点「生成计划」覆盖掉已规划的单章字数。
    兜底默认取 ``quality.wordcount.DEFAULT_TARGET_WORD_COUNT``（3000，全仓单源；
    与 project-init 的 DEFAULT_CHAPTER_WORD_COUNT 同口径）。

    scene_plan 同 run 落库（P1）：与 plan_json 的 UPDATE **同一事务**（同连接，不另开——
    AGENTS.md 坑区「事务内另开连接写库」）。本轮输出无 scene_plan 时**删除**旧行：
    旧 scene_plan 与刚写入的新计划不再对应，留下必被下游误用。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    project_id = ctx.get("project_id")
    director_output = ctx.get("director_planner_output") or {}

    # 落库 prompt 标签在**开启事务前**重算（两个落库面 plan_json / scene_plan 共用，
    # 故须早于二者的构造）：active_prompt_label 内部自建短连接只读 prompts 表并在
    # finally 关闭（prompts.py:141,156），放事务外不触碰「事务内另开连接」纪律
    # （AGENTS.md 坑区那条针对的是**写**库，此处是只读）。
    # 与 payload["prompt_version"]（planner_input.build_director_planner_input）是
    # **逐字同调用同回退**，故同一 run 的「装配声明」与两处落库记录全同源。
    # 禁采 director_output["prompt_version"]：那是模型照抄 payload 的**回声**
    # （可被改写 / 省略），与 ACTIVE 真值分叉即复现 2026-09-16 writer 事故形状。
    planner_prompt_label = active_prompt_label(
        db_path, "director_planner", fallback=DIRECTOR_PLANNER_PROMPT_VERSION,
    )

    # 读取原 plan_json.expected_word_count（如有）——保字数用
    inherit_expected: int | None = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if row is not None:
            existing_plan = _safe_load_json(row["plan_json"])
            if isinstance(existing_plan, dict):
                raw = existing_plan.get("expected_word_count")
                if isinstance(raw, (int, float)) and raw > 0:
                    inherit_expected = int(raw)
    finally:
        conn.close()

    director_word_count = director_output.get("expected_word_count")
    if isinstance(director_word_count, (int, float)) and director_word_count > 0:
        expected_word_count: int = int(director_word_count)
    elif inherit_expected is not None:
        expected_word_count = inherit_expected
    else:
        # 兜底链：绑定题材包的 pacing.chapter_words.target → 全仓单源 3000
        # （target 题材包化补齐 plan 侧；inherit 保护人工已规划值，位置不变）。
        expected_word_count = (
            pack_chapter_words_target(db_path, project_id)
            or DEFAULT_TARGET_WORD_COUNT
        )

    plan_payload: dict[str, Any] = {
        # 白名单：director 新增字段须**人工登记本表**，否则静默丢弃（不落 plan_json）。
        # scene_plan 不在此表：它落 chapter_scene_plans（见下），不进 plan_json。
        # title（2026-10-23 批次二十五；批次二十七订正注释与口径）：
        # 来源 = director_planner §7 输出契约的顶层 ``title``（v4 起声明）。v3 及以前
        # 的提示词无该键 ⇒ 本键**恒存在、值恒为 null**（白名单写死键名，不是「键缺席」）。
        # ⚠️ **本键 = 模型建议名**；**生效名见 ``chapters.title``**（作者标题优先，
        # 仅空标题才回填）——两个面同键不同义，消费方勿混取。
        "title": director_output.get("title"),
        "chapter_goal": director_output.get("chapter_goal"),
        "core_conflict": director_output.get("core_conflict"),
        "turning_point": director_output.get("turning_point"),
        "expected_role": director_output.get("expected_role"),
        "expected_word_count": expected_word_count,
        "key_beats": director_output.get("key_beats", []),
        "character_changes_planned": director_output.get("character_changes_planned", []),
        "information_releases": director_output.get("information_releases", []),
        "hook_handling": director_output.get("hook_handling", []),
        "debt_handling": director_output.get("debt_handling", []),
        "proposed_new_entities": director_output.get("proposed_new_entities", []),
        "deviations": director_output.get("deviations", []),
        "knowledge_leakage_check": director_output.get("knowledge_leakage_check", {}),
        "open_questions": director_output.get("open_questions", []),
        "notes_for_planner": director_output.get("notes_for_planner"),
        "schema_version": director_output.get("schema_version"),
        "prompt_version": planner_prompt_label,
    }

    scene_plan = director_output.get("scene_plan")
    scenes = scene_plan.get("scenes") if isinstance(scene_plan, dict) else None
    has_scene_plan = isinstance(scenes, list) and bool(scenes)

    # 2026-10-23 批次二十五：章名回填的**取值归一化**（与白名单分开，白名单保留原文）。
    # planner 给 null / 空串 / 纯空白 ⇒ 视同「没给」，不碰列；给出非空串 ⇒ strip 后采用。
    # 只在**空标题**上生效（作者已填标题一字不改，见下方 UPDATE 的 WHERE 守卫）。
    # ⚠️ **空白口径必须与 SQL 守卫逐字一致**（批次二十七 S-1，真缺陷修复）：
    # 两侧统一为 ``{空格, TAB, CR, LF}``（U+0020 / U+0009 / U+000D / U+000A）。
    # 刻意**排除 NBSP（U+00A0）**：它在正文里是可见字符，两侧都不当空白——
    # 否则「标题只有一个 NBSP」会被判空并被模型名覆盖（作者数据被悄悄改写）。
    planner_title_raw = director_output.get("title")
    planner_title = (
        planner_title_raw.strip(" \t\r\n")
        if isinstance(planner_title_raw, str) and planner_title_raw.strip(" \t\r\n")
        else None
    )

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
            (json.dumps(plan_payload, ensure_ascii=False), _now(), chapter_id),
        )
        # 空标题回填（同一事务、同一连接；不另开连接——AGENTS 坑区「事务内另开连接写库」）。
        # WHERE 守卫在**同一条语句里**判「当前列为空」⇒ 读-写竞态窗口为零；非空标题
        # 不会进入 SET，原作者名一字不改。
        # ⚠️ **SQLite 的 ``TRIM(x)`` 只吃 ASCII 空格**（批次二十七 S-1 实测：``"\t"`` 的
        # ``LENGTH(TRIM(x))`` 仍为 1）⇒ 显式给字符集 ``{空格, TAB, CR, LF}``，与上面的
        # Python ``strip(" \t\r\n")`` **逐字同口径**。NBSP 两边都不算空白（正文可见字符）。
        if planner_title is not None:
            conn.execute(
                "UPDATE chapters SET title = ? "
                "WHERE chapter_id = ? AND (title IS NULL OR "
                "TRIM(title, ' ' || CHAR(9) || CHAR(10) || CHAR(13)) = '')",
                (planner_title, chapter_id),
            )
        if has_scene_plan and project_id:
            scene_plan_id = save_scene_plan(
                conn,
                chapter_id=chapter_id,
                project_id=project_id,
                scene_plan=scene_plan,
                run_id=ctx.get("run_id"),
                source="director_planner",
                prompt_version=planner_prompt_label,
            )
        else:
            scene_plan_id = None
            removed = clear_scene_plan(conn, chapter_id)
            if removed:
                _log.warning(
                    "chapter_plan.save_plan invalidated %s stale scene_plan row(s) "
                    "(this run produced no scene_plan): chapter_id=%s",
                    removed, chapter_id,
                )
        conn.commit()
    finally:
        conn.close()
    return {
        "plan_saved": True,
        "plan_payload": plan_payload,
        "scene_plan_saved": bool(has_scene_plan and scene_plan_id),
        "scene_plan_id": scene_plan_id,
    }


def _now() -> str:
    from packages.core.ids import now_iso

    return now_iso()


def _safe_load_json(raw: Any) -> Any:
    """chapters.plan_json 列存的是 JSON 字符串；读时安全反序列化（失败返回 None）。"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            return None
    return None


def _build_nodes() -> list[WorkflowNode]:
    """构造节点列表。

    AI 节点（director_planner）从 ctx 读 ``_current_node_run_id``：引擎在每次调用节点 fn
    之前把当前节点行 id 注入 ctx（``engine._run_nodes`` 的
    ``ctx["_current_node_run_id"] = node_run_id``），fn 内无需自行查询。
    """
    return [
        WorkflowNode("build_ctx", "Transform", _build_ctx_node),
        WorkflowNode(
            "director_planner", "AI", _director_planner_node, agent_name="director_planner"
        ),
        WorkflowNode("save_plan", "State", _save_plan_node),
    ]


WORKFLOW = {
    "name": "chapter-plan",
    "version": "v1",
    "description": (
        "Director-Planner 合并调用（plan + scene_plan 一次产出）→ chapters.plan_json "
        "+ chapter_scene_plans（status stays PLANNED）"
    ),
    "nodes": _build_nodes(),
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_plan.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数（业务 pipeline 不再依赖 packages.workflows 中心）。


__all__ = ["WORKFLOW"]
