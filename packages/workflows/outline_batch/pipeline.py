"""outline_batch 工作流（批量续规划下一批章节）。

产品背景：用户首批章节写完后（章节列表页），此前没有任何入口继续产生下一批
章节大纲——「+ 新建章节」是单章手动建。本工作流 = 一次 run 内由 LLM 基于
已写内容规划下一批 N 章（默认 10）并建为 PLANNED 章。

节点链（3 节点线性）：

- ``assemble`` (Transform) —— 直接 SQL/领域查询装配 planner 输入（**不走装配缓存**：
  一次性 run，无重复装配收益；也不推 state_version）：
    - 项目 premise / genre / name；
    - 已有章节列表（number / title / 摘要），摘要查 ``chapter_summaries`` 按章取
      （无行 = 空串），按 number 升序；**截断到最近 10 章**防 prompt 爆炸，
      另带 total_chapters / earliest_number 供 prompt 注明「更早章节只有摘要链」；
    - 未闭环伏笔（``hooks`` 表 status ∈ OPEN/ACTIVE/ESCALATED，含引入章号——
      口径与 ``builders_common/ledger.py`` 的 ``_HOOK_OPEN_STATUSES`` 一致）；
    - 叙事债务（``narrative_debts`` 表 status ∈ open/acknowledged——同文件
      ``_DEBT_OPEN_STATUSES`` 口径）；
    - 请求章数 ``chapter_count`` 与起始章号 ``start_number``（= 现有 max(number)+1，
      无章则 1）。
- ``planner`` (AI) —— 新 agent ``outline_batch_planner``（prompt 版本
  ``outline_batch_planner-v1``）。LLM 节点实现参照 project_init 的 AI 节点
  （``run_agent`` + ``profile_id=ctx.get("model_profile_id")`` 单次 run 级模型档案
  覆盖）。输出契约 JSON 见 ``docs/agents/prompts/outline_batch_planner-v1.md`` §3；
  数量/连续性核销在 :func:`packages.core.agent_runtime.structured_output.
  _validate_outline_batch_planner`（input_payload 分支，违规走 runner 既有
  output-invalid 重试）。
- ``persist`` (State) —— 复用**同一个**契约校验函数终检（写库前最后闸门，与
  runner 重试共用单点实现，不算两份逻辑）后逐条 INSERT INTO chapters
  （建章 SQL 形态参照 project_init pipeline 的建章段：status='PLANNED'、
  visibility/who_knows 缺省、volume_id 可空），单事务提交；
  ``plan_json`` **不写**（走 DDL ``DEFAULT '{}'``，即「计划未生成」的空 plan）：
  该列是 chapter-plan 的**生成槽**——write 的 ``load_plan`` 校验
  ``plan_json.chapter_goal`` 非空，大纲层章在此塞 synopsis 会造出「收下了却
  过不了校验」的伪 plan（违反「一列不得两用」纪律，AGENTS 硬规则 9）；大纲
  synopsis 只存 ``outline_json``，且**映射进 canonical 键** ``chapter_goal``
  （synopsis/title 原文并存）——planner 的「大纲优先」规则只认
  ``chapter_goal`` 等键，原样存 seed 时批次大纲会被判「字段为空」而整批失效
  （C-3 修复，2026-10-24）。写正文前必须先跑 chapter-plan（生成计划），
  planner 白名单覆盖写 plan_json。落库 ``prompt_version`` 走
  ``active_prompt_label`` 现算（与 chapter_plan.save_plan 同款防「硬编码标签漂移」；
  fallback = ``outline_batch_planner:v1``）。

产出：run checkpoint（persist 节点返回值）带 ``created_chapter_ids`` /
``start_number`` / ``chapter_count``。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from packages.core.agent_runtime.prompts import active_prompt_label
from packages.core.agent_runtime.runner import run_agent
from packages.core.agent_runtime.structured_output import (
    AgentOutputError,
    validate_outline_batch_planner,
)
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import WorkflowNode

_log = logging.getLogger(__name__)

WORKFLOW_NAME = "outline-batch"
PLANNER_AGENT = "outline_batch_planner"
PLANNER_PROMPT_VERSION_FALLBACK = "outline_batch_planner:v1"

DEFAULT_CHAPTER_COUNT = 10
MAX_CHAPTER_COUNT = 50
#: 装配给 planner 的「已有章节列表」截断窗（防 prompt 爆炸；更早章节只有摘要链口径）。
RECENT_CHAPTER_WINDOW = 10

# 与 builders_common/ledger.py 的口径一致（hooks 五态 / debts 四态的「未闭环」子集）；
# 独立声明避免 context_engine → workflows 的依赖方向问题（core 机制层不 import 业务包）。
_HOOK_OPEN_STATUSES = ("OPEN", "ACTIVE", "ESCALATED")
_DEBT_OPEN_STATUSES = ("open", "acknowledged")


def _assemble_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Transform：装配 outline_batch_planner 输入（不调 LLM、不走装配缓存）。"""
    db_path = ctx["db_path"]
    project_id = ctx["project_id"]
    chapter_count = int(ctx.get("chapter_count") or DEFAULT_CHAPTER_COUNT)

    conn = get_connection(db_path)
    try:
        proj = conn.execute(
            "SELECT name, genre, premise FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if proj is None:
            raise ValueError(f"project {project_id!r} not found")

        chap_rows = conn.execute(
            "SELECT chapter_id, number, title FROM chapters "
            "WHERE project_id = ? ORDER BY number ASC",
            (project_id,),
        ).fetchall()
        numbers = [int(r["number"]) for r in chap_rows]
        start_number = (max(numbers) + 1) if numbers else 1

        summary_by_no: dict[int, str] = {}
        for row in conn.execute(
            "SELECT chapter_no, summary FROM chapter_summaries WHERE project_id = ?",
            (project_id,),
        ).fetchall():
            summary_by_no[int(row["chapter_no"])] = row["summary"] or ""

        placeholders_hooks = ",".join("?" for _ in _HOOK_OPEN_STATUSES)
        hook_rows = conn.execute(
            "SELECT h.hook_id, h.name, h.importance, c.number AS introduced_no "
            "FROM hooks h LEFT JOIN chapters c ON c.chapter_id = h.introduced_chapter_id "
            f"WHERE h.project_id = ? AND h.status IN ({placeholders_hooks}) "
            "ORDER BY c.number ASC, h.created_at ASC",
            (project_id, *_HOOK_OPEN_STATUSES),
        ).fetchall()

        placeholders_debts = ",".join("?" for _ in _DEBT_OPEN_STATUSES)
        debt_rows = conn.execute(
            "SELECT d.debt_id, d.description, d.severity, c.number AS created_no "
            "FROM narrative_debts d LEFT JOIN chapters c ON c.chapter_id = d.created_chapter_id "
            f"WHERE d.project_id = ? AND d.status IN ({placeholders_debts}) "
            "ORDER BY c.number ASC, d.created_at ASC",
            (project_id, *_DEBT_OPEN_STATUSES),
        ).fetchall()
    finally:
        conn.close()

    recent = [
        {
            "number": int(r["number"]),
            "title": r["title"] or "",
            "summary": summary_by_no.get(int(r["number"]), ""),
        }
        for r in chap_rows[-RECENT_CHAPTER_WINDOW:]
    ]

    payload: dict[str, Any] = {
        "agent": PLANNER_AGENT,
        "prompt_version": PLANNER_PROMPT_VERSION_FALLBACK,
        "project": {
            "name": proj["name"] or "",
            "genre": proj["genre"] or "",
            "premise": proj["premise"] or "",
        },
        "recent_chapters": recent,
        "total_chapters": len(numbers),
        "earliest_number": min(numbers) if numbers else None,
        "open_hooks": [
            {
                "hook_id": r["hook_id"],
                "name": r["name"] or "",
                "importance": r["importance"],
                "introduced_chapter_no": (
                    int(r["introduced_no"]) if r["introduced_no"] is not None else None
                ),
            }
            for r in hook_rows
        ],
        "narrative_debts": [
            {
                "debt_id": r["debt_id"],
                "description": r["description"] or "",
                "severity": r["severity"],
                "created_chapter_no": (
                    int(r["created_no"]) if r["created_no"] is not None else None
                ),
            }
            for r in debt_rows
        ],
        "chapter_count": chapter_count,
        "start_number": start_number,
    }
    return {"outline_batch_input": payload}


def _planner_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：调 outline_batch_planner agent 规划下一批章节种子。"""
    db_path = ctx["db_path"]
    payload = ctx["outline_batch_input"]
    mock_script = (ctx.get("mock_providers") or {}).get(PLANNER_AGENT)
    out = run_agent(
        db_path,
        PLANNER_AGENT,
        payload,
        ctx.get("run_id") or "",
        node_run_id=ctx.get("_current_node_run_id"),
        expected=PLANNER_AGENT,
        mock_script=mock_script,
        profile_id=ctx.get("model_profile_id") or None,
    )
    if not isinstance(out, dict):
        raise AgentOutputError(
            f"outline_batch_planner output not dict: {type(out).__name__}"
        )
    return {"outline_batch_planner_output": out}


def _persist_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：校验 seeds 后逐条建章（PLANNED），单事务提交。

    写库前复用契约层校验函数终检（同一单点实现）：数量 == chapter_count、
    number 恰为 start_number..start_number+chapter_count-1 连续、title/synopsis
    非空 str——违规抛 :class:`AgentOutputError`（正常路径下 runner 的 output-invalid
    重试已先挡住；此处是「写库前最后闸门」的防御，防任何绕过契约层的路径静默写库）。
    """
    db_path = ctx["db_path"]
    project_id = ctx["project_id"]
    payload = ctx["outline_batch_input"]
    chapter_count = int(payload["chapter_count"])
    start_number = int(payload["start_number"])
    out = ctx.get("outline_batch_planner_output") or {}

    # 终检（与 runner 契约层同一单点实现）：违规抛 AgentOutputError。
    validate_outline_batch_planner(out, input_payload=payload)
    seeds = out["chapter_seeds"]

    # 落库 prompt 标签在开启事务前重算（active_prompt_label 自建短连接只读，
    # finally 关闭——放事务外不触碰「事务内另开连接写库」纪律；与 chapter_plan
    # 的 save_plan 同款：禁采模型回声，落库记录与 ACTIVE 真值同源）。
    planner_prompt_label = active_prompt_label(
        db_path, PLANNER_AGENT, fallback=PLANNER_PROMPT_VERSION_FALLBACK,
    )

    conn = get_connection(db_path)
    created_chapter_ids: list[str] = []
    try:
        for seed in seeds:
            # plan_json 不写（INSERT 不列该列 ⇒ DDL DEFAULT '{}'）：它是 chapter-plan
            # 的**生成槽**——write 的 load_plan 校验 plan_json.chapter_goal 非空，
            # 在此塞 synopsis 会造出「收下了却过不了校验」的伪 plan（「一列不得两用」，
            # AGENTS 硬规则 9）。大纲内容只存 outline_json；用户须先在章节
            # 详情页跑 chapter-plan（生成计划），planner 白名单覆盖写 plan_json。
            #
            # outline_json 键映射（C-3 修复，2026-10-24）：planner「大纲优先」规则
            # （docs/agents/prompts/director_planner-v4.md 规则 0）只绑定
            # outline.chapter_goal / key_beats / turning_point / core_conflict——
            # outline_json 原样存 seed（number/title/synopsis）时 ``chapter_goal``
            # 判「字段为空」⇒ planner 无视批次大纲自主规划，批次大纲整批失效。
            # 把 synopsis 原文映射进 canonical 键 ``chapter_goal``（经
            # resolve_chapter_outline 原样进入 build_director_input 的
            # chapter.outline 段）；synopsis/title 原文并存供前端/审计对回 seed。
            # 判别：tests/workflow/test_outline_batch.py::
            # test_outline_batch_synopsis_reaches_planner_outline_as_chapter_goal
            # （撤映射 ⇒ outline_json 无 chapter_goal 键 ⇒ 该用例必红）。
            synopsis = seed.get("synopsis")
            outline_payload = {
                "chapter_goal": synopsis,
                "synopsis": synopsis,
                "title": seed.get("title"),
            }
            chapter_id = new_id("ch")
            now = now_iso()
            conn.execute(
                """
                INSERT INTO chapters
                    (chapter_id, project_id, number, title, outline_json,
                     status, visibility, who_knows,
                     created_at, updated_at, volume_id)
                VALUES
                    (:chapter_id, :project_id, :number, :title, :outline_json,
                     :status, :visibility, :who_knows,
                     :created_at, :updated_at, :volume_id)
                """,
                {
                    "chapter_id": chapter_id,
                    "project_id": project_id,
                    "number": int(seed["number"]),
                    "title": seed.get("title"),
                    "outline_json": json.dumps(outline_payload, ensure_ascii=False),
                    "status": "PLANNED",
                    "visibility": "VISIBLE",
                    "who_knows": None,
                    "created_at": now,
                    "updated_at": now,
                    "volume_id": None,
                },
            )
            created_chapter_ids.append(chapter_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log.info(
        "outline_batch.persist: project=%s created=%d chapters (%d..%d)",
        project_id,
        len(created_chapter_ids),
        start_number,
        start_number + chapter_count - 1,
    )
    return {
        "persisted": True,
        "created_chapter_ids": created_chapter_ids,
        "start_number": start_number,
        "chapter_count": chapter_count,
        "prompt_version": planner_prompt_label,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("assemble", "Transform", _assemble_node),
        WorkflowNode("planner", "AI", _planner_node, agent_name=PLANNER_AGENT),
        WorkflowNode("persist", "State", _persist_node),
    ]


WORKFLOW = {
    "name": WORKFLOW_NAME,
    "version": "v1",
    "description": (
        "assemble → planner → persist；基于已写章节摘要链/未闭环伏笔/叙事债务，"
        "由 outline_batch_planner 一次规划下一批 N 章（默认 10）并建为 PLANNED 章"
    ),
    "nodes": _build_nodes(),
}


__all__ = ["WORKFLOW", "WORKFLOW_NAME", "DEFAULT_CHAPTER_COUNT", "MAX_CHAPTER_COUNT"]
