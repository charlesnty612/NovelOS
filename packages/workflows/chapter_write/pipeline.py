"""chapter_write 工作流（Sprint 4-A + P0 ScenePlanner 真实化 + P1 Polisher + P1 规划合并消费）。

节点列表：
- ``load_plan`` (Transform) —— 读 chapters.plan_json 准备 director_plan 输入。
- ``scene_planner`` (AI) —— P0 新增：调 scene_planner agent 把 director_plan 翻译为
  结构化 Scene Plan（含 slots / 冲突 / 信息边界 / 结尾钩子）。
  **P1（2026-09-14）起优先读 chapter-plan 落库的 scene_plan**
  （``chapter_scene_plans``，迁移 0027）：命中即跳过 LLM 调用（``scene_planner_status=
  'from_plan'``）；未命中（老章 / 合并调用只回了计划段 / 0027 未迁移的老库）保留本节点
  的单次调用路径。任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级到原 stub
  机械映射逻辑，**不**阻断 writer run（ch3 实证过该风险真实，降级路径必须保留）。
- ``writer`` (AI) —— 调 writer agent 生成本章 prose。**P0-2 生成期字数闭环**
  （2026-09-18）：首次产出低于字数带下限（``status='under'``）时，在同一节点内
  **以 ``mode='write'`` 整章重写**（携带 ``length_directive`` 显式扩写指令），最多
  ``NOVELOS_WRITER_LENGTH_RETRIES``（默认 2）次额外尝试，任一次落入带内即停；最终取
  「离带最近」的一次（带内优先，同距保留更早的一次）。首次即带内 ⇒ 恰好 1 次调用。
  之所以必须 write 而非 revise：revise 由 writer-v3.md 规则 20 + §6.1 钉死为「逐字
  保留未提及部分 / 净增 ≤ +5%」，追不回大缺口且会产出成段重复（实证 ch_92bac068ff0d）。
  重写失败 / 输出非 dict → fail-soft 中止重写循环，保留已有产出（不阻断）。
- ``polisher`` (AI) —— P1 新增：以 writer 产出的 prose + scan_ai_patterns(prose)
  确定性命中为输入，做去 AI 腔的文风级润色；不改情节 / 人物 / 事实。
  任何失败（prompt 缺失 / provider 异常 / 输出不合规 / 长度守恒失败）→ 兜底回退
  writer 原始 prose，**不**阻断 save_draft。
  mock 直通：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，跳过润色
  原样透传（既有 mock 测试不受影响）。
  P0 提速条件触发：确定性预检（``scan_ai_patterns`` + 题材禁词 + ``req_q7``）三源
  全零命中 ⇒ ``polisher_status='skipped_clean'``，prose 原样透传不调 LLM；
  任一命中 ⇒ 走上述原润色路径（行为零变化）。
- ``length_check`` (Transform) —— 字数闭环实测节点（V3.7+ P1）：
  用 :func:`packages.core.quality.wordcount.classify_prose_length` 权威口径实测
  polished_prose（缺则 writer_output.prose）字数，读项目级 word_band_json 覆盖
  （与 chapter_review._basic_checks_node 同口径）。
  输出 ``length_report = {visible_chars, target, band_low, band_high, status,
  within_band}`` + ``target_word_count`` + ``word_band_cfg`` 三个独立 key
  （便于 condense 节点与 save_draft 复用），并把 ``length_check_passed`` 布尔
  写入 ctx 供 condense 路由判定。
- ``condense`` (AI) —— 仅在 length_check 未通过、payload 超带上限且未超过 2 轮时执行。
  复用 polisher 的 capability 绑定（``capability_for('polisher')``），prompt
  携带实测字数、目标带上下限、需净减比例，遵循 writer-v1 规则 20 的压缩纪律
  （优先砍铺垫/重复意象/冗词，不砍节拍、不删场景、保持文风与既有设定用语）。
  P0 提速压缩预算门：payload 未超 ``band_high``（``_CONDENSE_PAYLOAD_TRIGGER_RATIO``
  × 带上限）⇒ ``condense_status='skipped_below_budget'`` + ``condense_skipped=True``，
  不调 LLM（欠字 / 陈旧 report 形态：压缩无余量，且只会把正文压得更短）。
  「≤2 轮」压缩循环在节点内部兑现：调 LLM → ``visible_chars`` 实测 → 仍超
  ``band_high`` 且轮数未满 → 再调（每轮 payload 都用最新实测数字重建）。
  mock 守卫与 polisher（``_polish_node``）同款语义：``mock_providers`` 为空
  （生产）⇒ ``mock_script=None`` 走真实 provider；mock 流（有 mock_providers
  但没配 ``condense`` 脚本）⇒ 原样透传 ``skipped_no_mock``。
  任何失败 → 兜底保留当前 polished_prose 放行，``condense_status='failed'``，
  **不**阻断 save_draft。两轮后仍超带 → ``condense_status='over_band_after_2_rounds'``
  放行，save_draft 在 deviations 中标注压缩后实测字数与偏差。
  节点回写压缩后的 ``length_report`` / ``length_check_passed``（覆盖上游
  length_check 的旧值），下游 save_draft 因此读到压缩后真相。
- ``save_draft`` (State) —— 写 drafts 表 + chapters.status PLANNED→DRAFTED。
  优先取 ``polished_prose``（无则回落 ``writer_output.prose``）。
  word_count 直接对最终 prose 取权威实测（``visible_chars``）：既不读 condense
  之前的 ``length_report`` 旧值（length_check 在 condense 上游、压缩后不再重跑），
  也不采信 self_report.word_count（实证 ch4-6：writer 自报 3008-3172
  vs 实际 4721-5842，偏差 50%+）。超带偏差注记追加到 chapter plan_json.deviations。
"""


# 2026-09-27 B-1 拆分（硬规则 1）：本文件 1932 行单文件巨型 → 拆「5 子模块 + 门面」。
# 门面只保留 4 个调 run_agent 的节点与 WORKFLOW 组装，其余辅助逐字迁出：
#   scene_planner.py / writer.py / polisher.py / length.py / save_draft.py
#
# **为何 4 个节点不迁出（与先例的差异，须知悉）**：测试对
# ``monkeypatch.setattr(cw_pipeline, "run_agent", ...)`` 有 18 处打点
# （test_chapter_write_length_closure.py 8 处 / test_chapter_write_skip_gates.py 9 处 /
# test_chapter_write_length_retry.py 1 处）。节点若迁到子模块且子模块自带 run_agent
# 绑定，patch 会打在门面属性上而失效——属「改测试才能拆得干净」，而本任务禁止改测试。
# ⇒ 4 节点留在门面，门面约 1100 行仍 <1500 红线，硬规则 1 的整改目标（拆掉超标文件）已达成。
# （test_target_word_count_genre_pack / test_chapter_write_writer_capability 只访问门面不打点）

import logging
import sqlite3
from typing import Any

from packages.core.agent_runtime.revision_fidelity import preserved_ratio
from packages.core.agent_runtime.runner import run_agent
from packages.core.agent_runtime.structured_output import strip_think_blocks
from packages.core.context_engine import build_writer_input
from packages.core.context_engine.builders import (
    _REFERENCE_CANON_CONSUMER_PLANNER,
    _reference_canon_excerpt,
)
from packages.core.db import get_connection, is_missing_schema_error
from packages.core.model_router.router import capability_for
from packages.core.quality.wordcount import (
    DEFAULT_TARGET_WORD_COUNT,
    classify_prose_length,
    visible_chars,
)
from packages.core.workflow_runtime.engine import WorkflowNode
from packages.workflows.chapter_plan.scene_plan_store import load_scene_plan

from .length import (
    _CONDENSE_PAYLOAD_TRIGGER_RATIO,
    _MAX_CONDENSE_ROUNDS,
    _length_check_node,
    _resolve_chapter_word_band,
    _resolve_target_word_count,
)
from .polisher import _polish_precheck
from .save_draft import _save_draft_node
from .scene_planner import (
    _collect_scene_planner_inputs,
    _load_plan_node,
    _scene_planner_fallback,
)
from .writer import (
    _LENGTH_RETRY_DEFAULT,
    _LENGTH_RETRY_ENV,
    _REVISION_CHECKLIST_MARKER,
    _best_writer_attempt,
    _build_length_retry_payload,
    _latest_draft_text,
    _measure_writer_attempt,
    _parse_revision_checklist,
    _resolve_writer_context_mode,
    _resolve_writer_length_retries,
)

_log = logging.getLogger(__name__)


def _scene_planner_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Scene Planner AI 节点（P1 起兼任「读落库 scene_plan」入口）。

    行为：
    1. **P1 优先**：读 ``chapter_scene_plans``（迁移 0027，由 chapter-plan 的
       director_planner 合并调用落库）。命中（有行且 ``scenes`` 非空）→ 直接透出
       ``scene_plan``，``scene_planner_status='from_plan'``，**不调 LLM**。
    2. 未命中（老章 / 合并调用只回了计划段 / 0027 未迁移的老库）→ 走原路径：
       取 loaded_plan + chapter 元信息 + 项目角色/地点，组装 scene_planner payload。
    3. 调 ``run_agent(..., agent_name='scene_planner', expected='scene_planner',
       mock_script=...)``；runner 内部走 parse_json → validate_contract("scene_planner")
       → 写 ai_call_logs。
    4. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败 / 输出字段缺失）→ 降级
       到 ``_scene_planner_fallback``，**不**抛错、**不**阻断 writer。
    5. 输出顶层保留 ``scene_plan`` key（与 stub 兼容），同时把原始 AI 输出放入
       ``scene_planner_output`` 供观测。

    支持 mock：``ctx["mock_providers"]["scene_planner"]`` 与 writer/critic 同机制。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    mock_script = (ctx.get("mock_providers") or {}).get("scene_planner")

    # P1：chapter-plan 已落库 scene_plan（迁移 0027）→ 命中即跳过本次 LLM 调用。
    # 只透出 scenes（与既有 scene_planner 路径同形：下游 build_writer_input 只消费
    # 该键），provenance 单独放 scene_planner_source 供 run 详情审计。
    persisted = load_scene_plan(db_path, chapter_id)
    if persisted is not None:
        persisted_scenes = (persisted.get("scene_plan") or {}).get("scenes")
        if isinstance(persisted_scenes, list) and persisted_scenes:
            return {
                "scene_plan": {"scenes": persisted_scenes},
                "scene_planner_output": None,
                "scene_planner_status": "from_plan",
                "scene_planner_source": {
                    "source": persisted.get("source"),
                    "prompt_version": persisted.get("prompt_version"),
                    "run_id": persisted.get("run_id"),
                    "updated_at": persisted.get("updated_at"),
                },
            }
        _log.warning(
            "chapter_write.scene_planner persisted scene_plan unusable "
            "(empty/invalid scenes) → fallback to LLM path: chapter_id=%s", chapter_id,
        )

    payload = _collect_scene_planner_inputs(db_path, chapter_id, plan)
    # 题材库 P1b：本节点产出透出题材包消费审计键（供 run detail / 测试观测）；
    # 直接从已装配的 payload 取（不额外查库），非 dict → None。
    genre_pack_audit = payload.get("_genre_pack_consumed")
    genre_pack_audit = genre_pack_audit if isinstance(genre_pack_audit, dict) else None
    try:
        out = run_agent(
            db_path,
            "scene_planner",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="scene_planner",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("scene_planner")
            ),
        )
        if not isinstance(out, dict):
            raise ValueError(f"scene_planner output not dict: {type(out).__name__}")
        scenes = out.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("scene_planner output missing non-empty 'scenes'")
        # 只把 scene_planner 的核心 scenes 包装成 scene_plan；保留完整输出供观测。
        # F4 修复：scene_planner 节点输出透出 ``_reference_canon_consumed`` 审计键
        # 到 ctx（→ checkpoint_json），与 director / writer 节点的审计闭环对齐。
        # 仅透出审计键（{canon_id, consumed_fields}），不把 reference_canon 业务
        # 数据塞进 checkpoint（payload 内的 reference_canon 业务键仅供 scene_planner
        # 本节点消费，不进入 ctx）。
        scene_planner_audit: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp = get_connection(db_path)
        try:
            chap_row = conn_sp.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp.close()
        if project_id_for_audit:
            try:
                conn_sp2 = get_connection(db_path)
                try:
                    _, scene_planner_audit = _reference_canon_excerpt(
                        conn_sp2,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit = None
                finally:
                    conn_sp2.close()
            except Exception:  # noqa: BLE001 —— 审计键获取失败不阻断主流程
                scene_planner_audit = None
        ret: dict[str, Any] = {
            "scene_plan": {"scenes": scenes},
            "scene_planner_output": out,
            "scene_planner_status": "ok",
        }
        if scene_planner_audit is not None:
            ret["_reference_canon_consumed"] = scene_planner_audit
        if genre_pack_audit is not None:
            ret["_genre_pack_consumed"] = genre_pack_audit
        return ret
    except Exception as exc:  # noqa: BLE001 —— 任何失败均降级，不阻断 writer
        _log.warning(
            "chapter_write.scene_planner degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        fallback = _scene_planner_fallback(ctx)
        # scene_planner 失败/降级时也尝试透出审计键（与 writer 失败兜底语义对齐），
        # 便于「该章生成消费了哪份 canon」在审计层可观测。
        scene_planner_audit_fb: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp3 = get_connection(db_path)
        try:
            chap_row = conn_sp3.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp3.close()
        if project_id_for_audit:
            try:
                conn_sp4 = get_connection(db_path)
                try:
                    _, scene_planner_audit_fb = _reference_canon_excerpt(
                        conn_sp4,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit_fb = None
                finally:
                    conn_sp4.close()
            except Exception:  # noqa: BLE001
                scene_planner_audit_fb = None
        ret_fb: dict[str, Any] = {
            **fallback,
            "scene_planner_output": None,
            "scene_planner_status": "failed",
            "scene_planner_error": str(exc),
        }
        if scene_planner_audit_fb is not None:
            ret_fb["_reference_canon_consumed"] = scene_planner_audit_fb
        # 题材包审计键在降级路径同样透出（payload 在 try 之前已装配，降级不改变
        # 「该章 scene 规划消费了哪份题材包」这一事实）。
        if genre_pack_audit is not None:
            ret_fb["_genre_pack_consumed"] = genre_pack_audit
        return ret_fb


def _writer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    scene_plan = ctx["scene_plan"]
    context_mode = _resolve_writer_context_mode(ctx)
    loaded_plan = ctx.get("loaded_plan") or {}
    # 目标字数优先级：ctx 覆盖 > plan 显式 > 绑定题材包 pacing.chapter_words.target
    # > 全仓单源常量 DEFAULT_TARGET_WORD_COUNT（3000）；解析单点在
    # packages.core.genre.target_words.resolve_target_word_count。
    # 解析一次：既喂装配，也喂节点内欠带判定与重写指令（**同一把数字**，不得两处各算）。
    target_word_count = _resolve_target_word_count(ctx, loaded_plan)
    payload = build_writer_input(
        db_path,
        chapter_id,
        scene_plan,
        target_word_count=target_word_count,
        context_mode=context_mode,
        # 2026-09-16 F-10 ②：作者硬性要求进 writer payload。此前这条链是断的——
        # write 端点收下 author_intent 写进 ctx，但装配层没有这个形参，铁律一个
        # 模型都没看见（新书01 ch1-6 实证：价签快照越界 5/6、字数靠题材包 target 蒙）。
        author_intent=ctx.get("author_intent") or None,
    )

    # 修订模式（基于现有 draft 局部修改）注入：读最新 draft content +
    # plan_json.revision_note，二者皆非空 → mode='revise'。
    if ctx.get("fresh_write"):
        # 全新重写：忽略旧稿与改稿意见（用于跨模型文风对比）
        draft_text = ""
        revision_note = ""
    else:
        draft_text = _latest_draft_text(db_path, chapter_id)
        revision_note = loaded_plan.get("revision_note") or ""
    mode = "revise" if (revision_note and draft_text) else "write"
    payload["mode"] = mode
    payload["draft_text"] = draft_text
    if revision_note:
        # revision_note 是 writer 输入契约的**顶层**字段（writer-v1.md §4.1：mode /
        # draft_text / revision_note 同级）；build_writer_input 只产出 director_plan
        # 结构、不注入该键，故此处顶层写入即为落点（无嵌套副本）。
        payload["revision_note"] = revision_note

    mock_script = (ctx.get("mock_providers") or {}).get("writer")
    # 能力路由：revise 与 write 同走 creative_writing（**2026-09-15 推翻原设计**）。
    # 原设计把 revise 当「定向局部修改」走 light 档（=审校档，省创作额度）；
    # 实证推翻（书1 ch1）：门禁触发的改稿实际是**整章扩写**，审校档不做长文创作，
    # 结果是 re-write 时把整段原文重出一遍（同一句 v1 出现 1 次 → v2 出现 2 次）
    # 且字数仍欠带（1460 → 1815 CJK，带下限 2125）。改稿是创作行为，不是审校行为。
    # 留痕：AGENTS.md 坑区「改稿轮整段写重」；测试见
    # tests/workflow/test_chapter_write_writer_capability.py::revise 用例。
    writer_capability_override: str | None = None

    def _call_writer(input_payload: dict[str, Any]) -> Any:
        """调 writer agent（首次与欠带重写共用同一调用路径 / capability / mock）。"""
        return run_agent(
            db_path,
            "writer",
            input_payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="writer",
            mock_script=mock_script,
            capability_override=writer_capability_override,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("writer")
            ),
        )

    _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)
    max_length_retries = _resolve_writer_length_retries(ctx)


    out = _call_writer(payload)
    outputs: list[Any] = [out]
    payloads: list[dict[str, Any]] = [payload]
    attempts: list[dict[str, Any]] = [
        _measure_writer_attempt(
            attempt=1, mode=mode, out=out,
            target=target_word_count, band_cfg=band_cfg,
        )
    ]

    # ---- 生成期内字数闭环（P0-2）：欠带即在同一节点内整章重写 ----
    # 触发条件严格为「低于带下限」（status='under'）；在带内 / 超带都不重写
    # （超带由下游 condense 负责压缩）。首次即在带 ⇒ 恰好 1 次 writer 调用。
    while len(attempts) - 1 < max_length_retries and attempts[-1]["status"] == "under":
        retry_payload = _build_length_retry_payload(payload, attempts[-1])
        retry_no = len(attempts) + 1
        _log.warning(
            "chapter_write.writer under band → rewriting in write mode: "
            "chapter_id=%s attempt=%d/%d visible=%d band_low=%d",
            chapter_id, retry_no, max_length_retries + 1,
            attempts[-1]["visible_chars"], attempts[-1]["band_low"],
        )
        try:
            retry_out = _call_writer(retry_payload)
        except Exception as exc:  # noqa: BLE001 —— 重写失败不阻断（保留已有产出）
            _log.warning(
                "chapter_write.writer length retry degraded: chapter_id=%s attempt=%d err=%s",
                chapter_id, retry_no, exc,
            )
            break
        if not isinstance(retry_out, dict):
            break
        payloads.append(retry_payload)
        outputs.append(retry_out)
        attempts.append(
            _measure_writer_attempt(
                attempt=retry_no, mode="write", out=retry_out,
                target=target_word_count, band_cfg=band_cfg,
            )
        )

    # 取「离带最近」的一次（带内优先，同距保留更早的一次）作为本节点的产出：
    # 下游 polisher / length_check / save_draft 一律按这一次的正文继续。
    accepted = _best_writer_attempt(attempts)
    payload = payloads[accepted]
    out = outputs[accepted]
    mode = attempts[accepted]["mode"]
    scene_plan_injected = payload.get("scene_plan")
    scene_word_budget = (
        scene_plan_injected.get("_scene_word_budget")
        if isinstance(scene_plan_injected, dict) else None
    )

    # 修订模式机读核销表（writer-v1.md §6.1 第 11 条）：从 prose 末尾剥离
    # `---REVISION-CHECKLIST---` 尾块，前半 = 真正正文（落 drafts.content），
    # 后半 = 核销表（落 run 节点 revision_checklist 产出，便于审查改稿意见是否
    # 被真正执行）。fail-soft：尾块缺失/JSON 坏 → checklist=None + log warning，
    # 正文保持原样不阻断。
    #
    # 2026-09-21 检修 m5：剥离条件从「mode == 'revise'」改为「**内容里真的有尾块**」。
    # 原条件依赖 prompt 纪律（writer-v3.md 明令 write 模式不要输出尾块），但那是契约
    # 不是强制：模型一旦在 write / length-retry（mode='write'）里吐了尾块，尾块就会
    # 原样落进 ``drafts.content`` 并抬高 word_count，而 length_check / 字数带都按含
    # 尾块的数字判定。看内容剥离后：revise 语义不变（缺失仍 warning），write 模式下
    # 的偶发尾块被静默清掉、revision_checklist 仍为 None（不进审计面）。
    revision_checklist: list[dict[str, Any]] | None = None
    if isinstance(out, dict):
        raw_prose = out.get("prose") or ""
        had_marker = _REVISION_CHECKLIST_MARKER in raw_prose
        clean_prose, parsed_checklist = _parse_revision_checklist(raw_prose)
        if parsed_checklist is not None:
            if mode == "revise":
                # revise：核销表是机读契约 → 进 ctx 审计面
                revision_checklist = parsed_checklist
            else:
                # write / length-retry：尾块属 prompt 违约（writer-v3.md 明令只 revise
                # 输出）。剥掉防落库，但**不进审计面**——审计面只登记「定向改稿的
                # 核销承诺」，写模式的偶发尾块不是作者可见的承诺。
                _log.info(
                    "chapter_write.writer stray revision_checklist stripped: "
                    "chapter_id=%s mode=%s (prompt 约定该尾块只属 revise，已按内容剥离、不进审计面)",
                    chapter_id, mode,
                )
        elif mode == "revise" and had_marker:
            _log.warning(
                "chapter_write.writer revision_checklist parse failed: "
                "chapter_id=%s mode=revise (missing marker or bad JSON)",
                chapter_id,
            )
        if had_marker:
            # 分隔行出现过（无论 JSON 好坏）就用 clean_prose 覆盖——保证下游
            # polisher / save_draft 不会把核销表尾块混进草稿正文 / word_count 统计。
            out["prose"] = clean_prose
            # word_count 只统计剥离后正文：模型在 self_report.word_count 里通常按
            # 全文长度（含核销表 JSON 行）统计，会拉高 word_count 字段；此处覆写为
            # clean_prose 的字符数，确保 save_draft 落库的 word_count 与
            # drafts.content 实际长度一致。
            sr = out.get("self_report")
            if isinstance(sr, dict):
                sr["word_count"] = len(clean_prose)
                out["self_report"] = sr

    # ---- 改稿审计面 revision_audit（2026-09-21「改稿审计断链」检修）----
    # 问题 1a：revise 首稿欠带触发翻模（``_build_length_retry_payload`` 强制
    # mode='write'）时，被采纳的终稿是 write 轮 → checklist 分支不命中，
    # ``revision_checklist`` 为 None 且**无任何痕迹**——核销承诺随首稿静默蒸发。
    # revision_audit 把「请求的 mode → 终稿 mode」的翻模与核销表状态显式落进节点
    # 产出（ctx → checkpoint_json / run detail），翻模不再是无痕事件；
    # ``revision_checklist`` 语义保持现状（终稿 revise 且解析成功才非 None）。
    # checklist_status 枚举：
    # - recorded：终稿 revise 且核销表解析成功（进审计面）；
    # - missing：终稿 revise 但尾块缺失；
    # - bad_json：终稿 revise 且尾块存在但 JSON 坏（已剥、未进审计面）；
    # - superseded_by_under_band_rewrite：请求 revise、终稿被欠带重写翻成 write（本次检修
    #   补上的盲区——审计面至少能看到「翻模发生过」）；
    # - not_applicable_write：终稿本来就是 write（首写 / fresh_write / 请求即 write）。
    requested_mode = payloads[0].get("mode")
    audit_ratio: float | None = None
    deviations_count = 0
    if requested_mode == "revise" and mode != requested_mode:
        checklist_status = "superseded_by_under_band_rewrite"
    elif mode != "revise":
        checklist_status = "not_applicable_write"
    elif isinstance(out, dict) and parsed_checklist is not None:
        checklist_status = "recorded"
    elif isinstance(out, dict) and had_marker:
        checklist_status = "bad_json"
    else:
        checklist_status = "missing"
    if mode == "revise" and isinstance(out, dict):
        # 终稿是 revise 时才可算保真比（翻模/写模式的比值没有「保真」语义）；
        # out["prose"] 此时已是剥尾块后的正文，与校准口径一致。
        upstream_text = payload.get("draft_text")
        final_prose = out.get("prose") or ""
        if isinstance(upstream_text, str) and upstream_text and final_prose:
            audit_ratio = preserved_ratio(upstream_text, final_prose)
        audit_sr = out.get("self_report")
        audit_devs = audit_sr.get("deviations") if isinstance(audit_sr, dict) else None
        if isinstance(audit_devs, list):
            deviations_count = len(audit_devs)
    revision_audit = {
        "requested_mode": requested_mode,
        "final_mode": mode,
        "checklist_status": checklist_status,
        "preserved_ratio": round(audit_ratio, 4) if audit_ratio is not None else None,
        "deviations_count": deviations_count,
    }

    # V3.1.1 V-P0：writer 本节点真实落库模型 id（mock 路径无 ai_call_logs 行 → None）。
    # 用于 drafts.model_id 记录真实 provider/model，避免列表页无法区分模型。
    # 2026-09-16 版本口径统一：同一次查询顺带取 prompt_version——它是**本 run 实际加载
    # 的提示词版本**（权威值，run_agent 落库时写入），供 save_draft 写 drafts 用。
    writer_model_id: str | None = None
    writer_prompt_version: str | None = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT model_id, prompt_version FROM ai_call_logs "
            "WHERE run_id = ? AND node_run_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, ctx.get("_current_node_run_id")),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # 收窄（2026-10-16 批次十）：上方注释主张的降级范围只有「mock 路径无
        # ai_call_logs 行 → None」——那是**查不到行**，不是 DB 故障。原裸 except 会把
        # ``database is locked`` / ``disk I/O error`` 同样读成 None，而本键正是
        # 「本 run 真实落库模型 id」的权威源：save_draft 取不到就写
        # ``model_id="mock/mock"``（save_draft.py:143）⇒ **真实模型调用在审计面被
        # 记成 mock**，且 ``prompt_version`` 悄悄降级到 ACTIVE 标签（save_draft.py:55）。
        # 审计面记一条不存在的记录，与 fast_mode 审批人记 "human" 同族。
        if not is_missing_schema_error(exc):
            raise
        row = None
    finally:
        conn.close()
    if row is not None:
        writer_model_id = row["model_id"]
        writer_prompt_version = row["prompt_version"]
    # writer_input 透出到 ctx（→ checkpoint_json）供测试断言；生产仅作为可观测钩子。
    # revision_checklist：尾块解析成功时为 list（revise 的审计面；2026-09-21 m5 起
    # write 模式下的偶发尾块也解析但不进此键——见上方剥离段注释），其余情况为 None
    # （尾块缺失或 JSON 坏）。进 ctx → checkpoint_json / run 节点产出，run detail
    # 可见，便于审查改稿意见是否被真正执行。
    #
    # P0-2 生成期字数闭环的可观测键（均进 run 节点产出 / run detail）：
    # - ``writer_length_attempts``：每次 writer 调用的实测（attempt / mode /
    #   visible_chars / status / distance / in_band）——首次欠带后的整章重写轮次
    #   与结果一目了然（此前欠带是「静默完成」，只在 review 才暴露）；
    # - ``writer_length_accepted_attempt``：被采纳的尝试序号（1-based）；
    # - ``scene_word_budget``：writer payload 里 scene 预算装配的元字段
    #   （``_inject_scene_word_budget`` 产出：declared_sum / budget_sum / rebalanced /
    #   thin_plan / warning）——薄计划（目标 ≥2000 却只 1 个 scene）在 run 层可见，
    #   不必下钻到体积较大的 writer_input。
    return {
        "writer_output": out,
        "_writer_context_mode": context_mode,
        "writer_input": payload,
        "writer_model_id": writer_model_id,
        "writer_prompt_version": writer_prompt_version,
        "revision_checklist": revision_checklist,
        "revision_audit": revision_audit,
        "writer_length_attempts": attempts,
        "writer_length_accepted_attempt": accepted + 1,
        "scene_word_budget": scene_word_budget,
        # 2026-10-15 批次（六）G9：装配级 token 预算元字段提到**顶层**，
        # 与上面 ``scene_word_budget`` 同款理由——run 层可见，不必下钻。
        # ⚠️ 必须单独提出来：``_cap_checkpoint_payload``（engine.py）按顶层键体积
        # **从大到小**替换超阈值的键，而 ``writer_input`` 实测可达 ~227KB
        # ⇒ **预算越超，meta 越先随大键一起被换成截断标记**（meta 自身仅 79 字节，
        # 却是第一个消失的那个）。提到顶层后它永不被截，超预算在 run 记录里可见。
        # ⚠️ 只提键：不改 payload、不改 prompt、不改缓存键语义。
        "writer_assembly_meta": payload.get("_assembly_meta"),
    }


def _polish_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """P1 Polisher AI 节点（去 AI 腔文风润色）。

    行为：
    1. 取 ``writer_output.prose``，跑确定性预检 ``_polish_precheck``
       （scan_ai_patterns + 题材禁词 + req_q7 三源）。
    2. mock 直通判定：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，
       跳过润色原样透传（既有用例零回归；优先级高于下方的条件触发门）。
    3. **P0 提速条件触发**：预检三源全零命中 → 跳过润色（``polisher_status=
       'skipped_clean'`` + ``polish_skipped=True`` + ``polish_precheck`` 命中统计），
       ``polished_prose`` 沿用 writer 产出；任一命中 → 走原润色路径（行为零变化）。
    4. 组装 polisher payload（draft_text + ai_findings + style_constraints）。
    5. 调 ``run_agent(..., agent_name='polisher', expected='polisher', mock_script=...)``。
    6. 长度守恒：``polished_text`` 与原文 ``visible_chars`` 差距 >10% → 兜底回退原文。
    7. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败）→ 兜底回退原文，
       写 ``polisher_status='failed'``，**不**阻断 save_draft。
    8. 输出 ``polished_prose`` 进 ctx，``save_draft_node`` 优先取。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    writer_output = ctx.get("writer_output") or {}
    raw_prose = strip_think_blocks(writer_output.get("prose") or "")
    mock_providers = ctx.get("mock_providers") or {}

    # mock 直通：既有 mock 流（仅含 writer、不含 polisher）原样透传，
    # 保证 test_chapter_write_revise_mode / test_chapter_write_scene_planner 等
    # 既有测试零回归；优先级高于下方条件触发门（mock 语义不抢）。
    if mock_providers and ("writer" in mock_providers) and ("polisher" not in mock_providers):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "mock 直通：未配置 polisher mock，原样透传 writer prose",
            "polisher_status": "passthrough",
            "ai_findings": [],
        }

    # P0 提速：条件触发预检（确定性检测器，零 LLM）。三源全零命中 ⇒ 无 AI 腔 /
    # 无题材禁词 / 句长与 marker 密度均在既有阈值内 ⇒ 跳过润色省一次 LLM 往返，
    # ``polished_prose`` 直接沿用 writer 产出（save_draft 的「polished_prose 优先、
    # 缺失回落 writer_output.prose」逻辑已就位，数据形态与 mock 直通路径同形）。
    # 任一命中 ⇒ 落回下方原润色路径，行为零变化。
    precheck = _polish_precheck(raw_prose, db_path, chapter_id)
    if (
        precheck["ai_pattern_hit_count"] == 0
        and precheck["forbidden_word_hit_count"] == 0
        and precheck["q7_issue_count"] == 0
    ):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "确定性预检零命中：跳过润色（沿用 writer 产出）",
            "polisher_status": "skipped_clean",
            "polish_skipped": True,
            "polish_skip_reason": (
                "预检三源零命中：ai_pattern_hits=0 / forbidden_word_hits=0 / "
                f"q7_issues=0（payload visible_chars={visible_chars(raw_prose)}）"
            ),
            "ai_findings": [],
            "polish_precheck": precheck,
        }

    ai_findings = precheck["ai_pattern_hits"]
    payload: dict[str, Any] = {
        "agent": "polisher",
        "prompt_version": "polisher:v1",
        "draft_text": raw_prose,
        "ai_findings": ai_findings,
        "style_constraints": {
            "language": "zh-Hans",
            "pov": "third_person_limited",
            "forbidden_words": ["仿佛", "如同", "宛如"],
        },
    }

    mock_script = mock_providers.get("polisher")
    try:
        out = run_agent(
            db_path,
            "polisher",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="polisher",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("polisher")
            ),
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败均兜底回退原文
        _log.warning(
            "chapter_write.polisher degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": f"润色失败已回退原文：{exc}",
            "polisher_status": "failed",
            "polisher_error": str(exc),
            "ai_findings": ai_findings,
        }

    if not isinstance(out, dict):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出非 dict，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }
    polished = out.get("polished_text")
    if not isinstance(polished, str):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出 polished_text 非字符串，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }

    # 长度守恒：polished 与原文 visible_chars 差距 >10% → 回退原文（兜底）
    orig_len = visible_chars(raw_prose)
    new_len = visible_chars(polished)
    if orig_len > 0:
        delta = abs(new_len - orig_len) / orig_len
        if delta > 0.10:
            _log.warning(
                "chapter_write.polisher length drift: chapter_id=%s orig=%d new=%d delta=%.2f",
                chapter_id, orig_len, new_len, delta,
            )
            return {
                "polished_prose": raw_prose,
                "polish_changes_summary": (
                    f"润色超长度限制（Δ{delta:.0%}），已回退原文"
                ),
                "polisher_status": "length_drift",
                "ai_findings": ai_findings,
            }

    changes_summary = out.get("changes_summary") or ""
    return {
        "polished_prose": polished,
        "polish_changes_summary": changes_summary,
        "polisher_status": "ok",
        "ai_findings": ai_findings,
        "polisher_output": out,
    }


def _condense_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：超带时调 polisher capability 做压缩（节点内自循环，最多 2 轮）。

    触发条件：``length_check_passed`` False 且 ``condense_rounds < _MAX_CONDENSE_ROUNDS (2)``
    **且 payload 超带上限**（``visible_chars(payload) > _CONDENSE_PAYLOAD_TRIGGER_RATIO
    × band_high``；P0 提速压缩预算门，见模块顶部常量区）。
    不触发条件：带内 / 已达 2 轮 / payload 未超带上限（``skipped_below_budget``）
    → 直接放行（均不调 LLM、不计轮）。

    mock 守卫（与 ``_polish_node`` 同款语义，V3.9 批次 1.1 修复生产不可达）：
    - ``mock_providers`` 为 None / {}（生产）⇒ ``mock_script=None`` 走真实 provider；
    - ``mock_providers`` 非空但未配 ``condense`` 脚本（writer-only 等 mock 流）
      ⇒ 原样透传 ``condense_status='skipped_no_mock'``，既有测试零回归。

    内循环（兑现「≤2 轮」承诺）：调 LLM → ``visible_chars`` 实测 → 仍超
    ``band_high`` 且轮数未满 → 再调；每轮 payload 都用最新实测数字重建。
    任一轮产出被拒收 / 调用失败即终止循环（不空转消耗轮次预算）。

    计数器约定（smart 审查 P2 一致性收口）：
    - **唯一计数键** ``ctx['condense_rounds']``（length_check / condense /
      save_draft / 偏差注记 全部读同一把 key）。
    - 计轮规则：每发起一次 LLM 调用（mock 或真实；异常 / 产出被拒收同样计）
      → ``condense_rounds + 1``；
      skipped_in_band / skipped_empty / skipped_no_mock / skipped_below_budget /
      over_band_after_2_rounds **均不计轮**（不 +1；早返回路径 preserve rounds_used 不动）。
    - 返回值补 ``condense_status`` 供观测（早返回路径也带，便于 run detail 一眼看清）。

    输出确定性守卫（防 LLM 返回空 / 更长 / 非字符串把坏草灌进 drafts）：
    - 空串 / 非 str / visible_chars >= 压缩前 visible_chars → 拒收，保留当前 prose，
      ``condense_status='failed_no_shrink'``，**计 1 轮**（调用过 LLM 但产出无效）。

    行为契约：
    - payload 必须携带实测数字与目标带（**不**让 LLM 自报字数自决目标）。
    - prompt 注入 writer-v1 §6.1 规则 20 压缩纪律（优先砍铺垫/重复意象/冗词，
      不砍节拍、不删场景、保持文风与既有设定用语）。
    - 任何失败（prompt 缺失 / provider 异常 / 契约失败）→ 兜底保留当前
      polished_prose 不动；``condense_status='failed'``，**不**阻断 save_draft。
    - 输出 ``polished_prose``（压缩后正文）+ 真实轮数 + **重算后的
      ``length_report`` / ``length_check_passed``**（节点产出 merge 进 ctx，覆盖
      上游 length_check 的旧值，使 save_draft 读到压缩后真相）。
    """
    db_path = ctx["db_path"]
    run_id = ctx.get("run_id", "")
    chapter_id = ctx["chapter_id"]
    mock_providers = ctx.get("mock_providers") or {}

    report = ctx.get("length_report") or {}
    rounds_used = int(ctx.get("condense_rounds") or 0)
    within = bool(ctx.get("length_check_passed"))

    # ---- 早返回路径：均不计轮，preserve rounds_used ----
    if within:
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "skipped_in_band",
            "condense_rounds": rounds_used,
        }

    if rounds_used >= _MAX_CONDENSE_ROUNDS:
        # 已达 2 轮上限仍超带：放行 + 标注偏差（save_draft 会写到 deviations）。
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "over_band_after_2_rounds",
            "condense_rounds": rounds_used,
            "condense_report": report,
            "length_report": report,
            "length_check_passed": False,
        }

    current_prose = ctx.get("polished_prose")
    if not isinstance(current_prose, str) or not current_prose.strip():
        # 没东西可压缩（length_check 走兜底 0 字路径）→ 跳过不调 LLM；不计轮。
        return {
            "polished_prose": current_prose or "",
            "condense_status": "skipped_empty",
            "condense_rounds": rounds_used,
        }

    # mock 守卫（同 _polish_node 语义）：mock 流（mock_providers 非空但没配
    # condense 脚本）→ 原样透传，既有 writer-only mock 测试零回归；生产链路
    # （mock_providers 为 None / {}）⇒ condense_mock=None ⇒ run_agent 走真实 provider。
    condense_mock = (
        mock_providers.get("condense") if isinstance(mock_providers, dict) else None
    )
    if mock_providers and not condense_mock:
        # 没调 LLM，不占轮次预算（preserve rounds_used）。
        return {
            "polished_prose": current_prose,
            "condense_status": "skipped_no_mock",
            "condense_rounds": rounds_used,
        }

    # ---- P0 提速·压缩预算门：payload 未超带上限 ⇒ 无压缩余量 → 跳过 ----
    # 判据 = ``_CONDENSE_PAYLOAD_TRIGGER_RATIO`` × band_high（模块顶部常量，取值依据
    # 见常量区注释：band_high 是「已无压缩余量」的既有权威边界）。带上限优先取
    # length_check 写回的 ``length_report.band_high``；直接调节点（单测 / 绕过
    # length_check）时按同源口径回读项目字数带，与下方 prompt 装配逐字同口径。
    # 位置：在 mock 守卫之后（mock 流的既有透传语义零变化）、真实 provider 路径之前。
    band_high = int(report.get("band_high") or 0)
    if band_high <= 0:
        band_target = int(
            report.get("target")
            or ctx.get("target_word_count")
            or DEFAULT_TARGET_WORD_COUNT
        )
        band_cfg_gate = ctx.get("word_band_cfg")
        if not isinstance(band_cfg_gate, dict):
            _, band_cfg_gate = _resolve_chapter_word_band(db_path, chapter_id)
        band_high = int(
            classify_prose_length(current_prose, band_target, **band_cfg_gate)[
                "band_high"
            ]
        )
    payload_chars = visible_chars(current_prose)
    trigger_chars = int(band_high * _CONDENSE_PAYLOAD_TRIGGER_RATIO)
    if payload_chars <= trigger_chars:
        # 未超带（含 status='under' 欠字形态：压缩只会更短，内循环又以
        # status != 'over' 为收工判据 ⇒ 白调一次 LLM 还把正文压坏）。不计轮：
        # 没调 LLM，preserve rounds_used（与其余早返回路径同款）。
        return {
            "polished_prose": current_prose,
            "condense_status": "skipped_below_budget",
            "condense_rounds": rounds_used,
            "condense_skipped": True,
            "condense_skip_reason": (
                f"payload {payload_chars} 字 ≤ 压缩预算阈值 {trigger_chars} 字"
                f"（带上限 {band_high} × {_CONDENSE_PAYLOAD_TRIGGER_RATIO:.2f}）："
                "未超带，无压缩余量"
            ),
            "condense_payload_chars": payload_chars,
            "condense_budget_chars": trigger_chars,
        }

    if not run_id:
        # 单测路径可能没传 run_id；安全降级为「不入库」占位
        run_id = ""

    target = int(report.get("target") or ctx.get("target_word_count") or DEFAULT_TARGET_WORD_COUNT)
    band_cfg = ctx.get("word_band_cfg")
    if not isinstance(band_cfg, dict):
        # 直接调节点（单测 / 绕过 length_check）时 ctx 无 word_band_cfg →
        # 回读项目级覆盖，与 length_check 同源同口径。
        _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)

    def _measure(text: str) -> dict[str, Any]:
        measured = classify_prose_length(text, target, **band_cfg)
        measured["within_band"] = measured["status"] == "in_band"
        return measured

    # 每轮都以「当前 prose 的实测」重建 payload：内循环第二轮必须携带压缩后的
    # 真实数字（不能让 LLM 拿上一轮的旧字数自决目标）。
    final_report = _measure(current_prose)
    rounds = rounds_used
    status = "ok"
    error_msg: str | None = None
    changes_summary = ""

    # ---- 内循环：调 LLM → visible_chars 实测 → 仍超 band_high 且轮数未满 → 再调 ----
    while rounds < _MAX_CONDENSE_ROUNDS:
        visible = int(final_report.get("visible_chars") or 0)
        band_low = int(final_report.get("band_low") or 0)
        band_high = int(final_report.get("band_high") or target)
        excess = max(0, visible - band_high)
        excess_pct = round(visible / target * 100, 1) if target > 0 else 0.0
        target_pct = round(target / visible * 100, 1) if visible > 0 else 0.0

        payload: dict[str, Any] = {
            "agent": "condense",
            "prompt_version": "condense:v1",
            "draft_text": current_prose,
            "length_context": {
                "visible_chars": visible,
                "target": target,
                "band_low": band_low,
                "band_high": band_high,
                "excess_chars": excess,
                "excess_pct": excess_pct,
                "target_pct": target_pct,
                "round": rounds + 1,
                "max_rounds": _MAX_CONDENSE_ROUNDS,
            },
            "condense_directive": (
                "实测当前正文「{visible} 字」，目标字数带 [low={low}, high={high}] "
                "（target={target}）。当前超出 band_high 约 {excess_pct}%（约 {excess} 字），"
                "需净减至 band_high 以内（净减至 target 的 {target_pct}% 量级）。\n\n"
                "压缩纪律（writer-v1 §6.1 规则 20）：\n"
                "1. 优先砍铺垫、重复意象、冗词、装饰性修辞；\n"
                "2. 不砍节拍、不删场景、不改情节线、不删关键人物动作；\n"
                "3. 保持既有文风与人称设定；\n"
                "4. 保持既有设定用语（角色名、地名、专有名词不替换）。\n\n"
                "输出 JSON：{{\"polished_text\": <压缩后正文>, "
                "\"changes_summary\": <改动说明>}}"
            ).format(
                visible=visible,
                low=band_low,
                high=band_high,
                target=target,
                excess_pct=excess_pct,
                excess=excess,
                target_pct=target_pct,
            ),
        }

        # 计轮先于调用：只要意图调 LLM 就算 1 轮（异常 / 拒收同样计），
        # 与既有计数器语义一致（旧实现 next_rounds = rounds_used + 1 在 try 之前）。
        rounds += 1

        try:
            out = run_agent(
                db_path,
                "polisher",  # 复用 polisher capability 绑定（capability_for('polisher')=creative_writing）
                payload,
                run_id,
                node_run_id=ctx.get("_current_node_run_id"),
                expected="polisher",
                mock_script=condense_mock,  # None（生产）⇒ run_agent 走真实 provider
                profile_id=(ctx.get("model_overrides") or {}).get(
                    capability_for("polisher")
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "chapter_write.condense degraded: chapter_id=%s err=%s",
                chapter_id, exc,
            )
            status = "failed"
            error_msg = str(exc)
            break

        if not isinstance(out, dict):
            status = "failed"
            break
        new_prose = out.get("polished_text")
        if not isinstance(new_prose, str):
            status = "failed"
            break

        # ---- 输出确定性守卫：拒空 / 拒更长 ----
        # 0 字 / 空串显然无效；visible_chars 没缩短（>= 原 visible）意味着 LLM 没做
        # 实压缩或反而扩写 —— 一律拒收，避免坏草灌进 drafts。
        new_visible = visible_chars(new_prose)
        if new_visible == 0 or new_visible >= visible:
            _log.warning(
                "chapter_write.condense rejected output: chapter_id=%s "
                "orig_visible=%d new_visible=%d",
                chapter_id, visible, new_visible,
            )
            status = "failed_no_shrink"
            error_msg = f"condense output visible_chars={new_visible} (orig={visible})"
            break

        # 采纳本轮压缩结果，并以压缩后文本重测（不再信任 length_check 旧值）。
        current_prose = new_prose
        changes_summary = str(out.get("changes_summary") or "")
        final_report = _measure(current_prose)
        if final_report["status"] != "over":
            # 已落回 band_high 以内（in_band / under）→ 压缩闭环达成，收工。
            status = "ok"
            break
        if rounds >= _MAX_CONDENSE_ROUNDS:
            # 轮次已耗尽仍超 band_high → 放行（save_draft 写偏差注记）。
            status = "over_band_after_2_rounds"
            break

    result: dict[str, Any] = {
        "polished_prose": current_prose,
        "condense_status": status,
        "condense_rounds": rounds,
        "condense_report": final_report,
        # 压缩后真相：覆盖上游 length_check 的旧 length_report / length_check_passed，
        # 使 save_draft 的 word_count 与偏差注记都按最终 prose 口径。
        "length_report": final_report,
        "length_check_passed": bool(final_report.get("within_band")),
    }
    if changes_summary:
        result["condense_changes_summary"] = changes_summary
    if error_msg:
        result["condense_error"] = error_msg
    return result

def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("load_plan", "Transform", _load_plan_node),
        WorkflowNode(
            "scene_planner", "AI", _scene_planner_node, agent_name="scene_planner"
        ),
        WorkflowNode("writer", "AI", _writer_node, agent_name="writer"),
        WorkflowNode("polisher", "AI", _polish_node, agent_name="polisher"),
        WorkflowNode("length_check", "Transform", _length_check_node),
        WorkflowNode("condense", "AI", _condense_node, agent_name="polisher"),
        WorkflowNode("save_draft", "State", _save_draft_node),
    ]


WORKFLOW = {
    "name": "chapter-write",
    "version": "v1",
    "description": (
        "Director Plan → Scene Planner(AI) → Writer prose → Polisher(AI) → "
        "length_check(权威实测+项目字数带) → condense(超带≤2轮,polisher capability) → "
        "drafts table; chapter status PLANNED→DRAFTED; "
        "scene_planner / polisher / condense 失败均降级不阻断 writer"
    ),
    "nodes": _build_nodes(),
    # V3.9 批次 2.4 checkpoint 写放大治理：此前本 workflow 无 exclude 清单，引擎
    # 每节点完成都把整份 ctx 序列化落 workflow_runs.checkpoint_json（engine.py:712）。
    # 生产实测：单章 checkpoint 25 万~28 万 UTF-8 字节（其中 writer_input 19K~23K 字符
    # ≈57~69KB），整章 7 节点 ⇒ 7 次该量级的重复序列化 UPDATE。
    # 语义：engine._scrub_ctx_for_checkpoint（engine.py:73-84）只在**落盘前**浅拷贝
    # 剔除顶层键，run 内 ctx 对象不受影响——同 run 上下游节点（writer → polisher →
    # length_check → condense → save_draft）照常读到全量键，执行结果零变化。
    #
    # resume 安全性（批次 2.4 第二步核对结论）：
    # - 本链 7 节点全是 Transform / AI / State，**无 Human 节点**，没有任何节点抛
    #   PauseRequested（本模块不 import 该异常）→ run 永不 PAUSED → engine.resume
    #   （engine.py:398-438，唯一从 checkpoint_json 反序列化重建 ctx 的路径）不可达；
    # - 崩溃恢复 recover_interrupted_runs（engine.py:923-1015）只把残留 RUNNING run /
    #   节点行标 FAILED，**不读也不重建 checkpoint 里的 ctx** → 无「恢复后键缺失」场景；
    # - checkpoint 因此只服务运行详情审计展示。下方「必保留」组已覆盖各节点
    #   ctx.get(...) 兜底所需的键，未来若新增 Human 节点也不会静默缺键。
    # 口径提醒：本清单只对 chapter-write 成立，勿照搬到有 Human 节点 / resume 消费者的
    # workflow（如 chapter-commit 的 checkpoint_json['quality_gate'] 是前端消费契约）。
    "checkpoint_exclude": [
        # ---- 体积大头：writer 装配 payload ----
        # 生产实测 19KB（最大 125KB 的 run 里占 23KB）。仅作测试断言 / 可观测钩子
        # （_writer_node 注释自陈），run 内后续节点一律不读它——polisher /
        # length_check / condense / save_draft 读 writer_output / polished_prose。
        "writer_input",
        # ---- 节点镜像键：引擎除顶层 merge 外还会把整套节点产出存 ctx[node_id]
        #      （engine.py:670）→ 与 workflow_run_nodes.output_json 逐字节重复 ----
        # writer 镜像内含 writer_input，不剔除则上一条形同虚设（payload 仍会经镜像键
        # 落盘）；scene_planner / polisher / condense 镜像同理内含各自的大产出。
        # 7 个 node_id 全列，口径统一为「节点产出的权威副本在 workflow_run_nodes，
        # checkpoint 只留顶层 merge 的可读视图」。
        "load_plan",
        "scene_planner",
        "writer",
        "polisher",
        "length_check",
        "condense",
        "save_draft",
        # ---- 派生大对象：与顶层权威视图重复 ----
        # scene_planner_output：scene_planner agent 原始输出（含全部 scene slots /
        # 冲突 / 信息边界），顶层 scene_plan 已是下游消费的权威结构。
        "scene_planner_output",
        # polisher_output：polisher agent 原始输出，polished_text 与顶层
        # polished_prose 逐字重复（生产实测 8KB）。
        "polisher_output",
        # ---- 必保留（run 详情可读性 + 未来 resume 路径的节点兜底输入）----
        # 不在此清单：polished_prose / writer_output（最终正文与 writer 结构化产出，
        # 单章 3~10KB，run 详情主要可读产物，也是 save_draft 的 prose 来源）；
        # scene_plan / loaded_plan（writer 硬依赖 ctx["scene_plan"] 与章计划原文）；
        # length_report / length_check_passed / condense_rounds / condense_status /
        # target_word_count / word_band_cfg（字数闭环控制流小键，合计 <1KB）；
        # _reference_canon_consumed（参照系消费审计链，canon.py 明示随 ctx 落 checkpoint）；
        # db_path / project_id / chapter_id / run_id / writer_model_id 等执行控制键。
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_write.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


# 门面再导出的**有消费方**名字（2026-09-27 B-1）。`__all__` 收这些名字有两个作用：
# ① 保持既有深路径 import 行为不变（测试与 workflows/__init__ 逐字不动）；
# ② 让 ruff F401 对「纯再导出」不报警——无消费方的名字已从门面移除，不做无效再导出。
# 无自指元素；不收 _log / 各子模块名（它们不是本门面的再导出对象）。
# 共 16 名：WORKFLOW + 7 节点（WORKFLOW 组装 + 测试直接调用）+ 6 辅助 + 2 常量。
__all__ = [
    "WORKFLOW",
    # 节点（WORKFLOW 组装 + 测试直接调用）
    "_scene_planner_node",
    "_writer_node",
    "_polish_node",
    "_length_check_node",
    "_condense_node",
    "_save_draft_node",
    "_load_plan_node",
    # 测试直接 import 的辅助（test_skip_gates / test_length_closure /
    # test_writer_input_paged / test_context_engine / test_genre_pack_consumption_p1b）
    "_polish_precheck",
    "_resolve_writer_context_mode",
    "_resolve_writer_length_retries",
    "_resolve_chapter_word_band",
    "_resolve_target_word_count",
    "_collect_scene_planner_inputs",
    # 测试以 cw_pipeline.<名> 访问的模块常量（test_chapter_write_length_retry.py:263/272/286）
    "_LENGTH_RETRY_DEFAULT",
    "_LENGTH_RETRY_ENV",
]
