"""chapter_write · save_draft 段（2026-09-27 B-1 拆分自 pipeline.py）。

含 _save_draft_node：写 drafts 表 + chapters.status PLANNED→DRAFTED。
同包内搬移，逻辑逐字不变。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from packages.core.agent_runtime.prompts import active_prompt_label
from packages.core.agent_runtime.structured_output import strip_think_blocks
from packages.core.db import get_connection, is_missing_schema_error
from packages.core.ids import new_id, now_iso
from packages.core.quality.normalize import find_internal_identifiers, normalize_prose
from packages.core.quality.wordcount import visible_chars

from .length import _MAX_CONDENSE_ROUNDS

_log = logging.getLogger(__name__)
def _save_draft_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    writer_output = ctx.get("writer_output") or {}
    # P1：polished_prose 优先；缺失（passthrough / 失败兜底）则回落 writer 原始 prose。
    prose = strip_think_blocks(
        ctx.get("polished_prose") or writer_output.get("prose") or ""
    )
    # 2026-09-16 F-10 ③（确定性算子，不靠提示词）：落库前规整正文。
    # 实证：新书01 全弧引号三种混用（ch1/ch4「」、ch2/ch3 英文直引号、ch9 弯引号），
    # 规整目标 = 文风锚点（榜一侯府弧）的弯双引号，全弧 109/119 份 draft 有变更。
    # 内部标识（改稿轮把 recalled_passages 写进 ch2 v2）只检出、不静默删——删了
    # 句子就断；检出项进节点产出，供评审与人工返修定位。
    prose, _normalize_changes = normalize_prose(prose)
    _internal_ids = find_internal_identifiers(prose)
    # V3.9 批次 1.2 字数断链修复：word_count 直接对最终 prose 取权威实测
    # （visible_chars，去空白口径）。既不读 condense 之前的 length_report.visible_chars
    # （length_check 在 condense 上游、压缩后不再重跑，旧值会污染落库字数），
    # 也不采信 self_report.word_count（实证 ch4-6：自报 3008-3172 vs 实测 4721-5842，
    # 偏差 50%+）。
    word_count = visible_chars(prose)
    # 偏差注记的实测口径：condense 节点已把压缩后的 length_report merge 回 ctx
    # （批次 1.1 回写），此处读到的是压缩后真相，不是 length_check 的旧值。
    length_report = ctx.get("length_report") or {}
    # 版本口径统一（2026-09-16）：**不再采信模型自报**（writer_output.prompt_version
    # 是输入示例的回声，writer 升 v2 后仍报 v1）。权威优先级：
    # ① 本 run 的 ai_call_logs.prompt_version（run_agent 实载提示词版本，
    #    _writer_node 已取并放进 ctx）；
    # ② 当前 ACTIVE 提示词标签（mock 路径无调用日志时的兜底）；
    # ③ 常量兜底（空库 / 未 sync 的测试库）。
    prompt_version = ctx.get("writer_prompt_version") or active_prompt_label(
        db_path, "writer", fallback="writer:v1"
    )

    draft_id = new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        # 字数闭环偏差注记：仅当 condense_rounds >= 2（已穷尽压缩预算）且仍超带
        # 时才写「2 轮后仍超带」注记（smart 审查 P2：避免 round=0 / mock 流跳过等
        # 早返回路径也错误触发此文案）。批次 1.1 把「≤2 轮」循环收进 condense 节点
        # 后该分支真实可达（生产链路不再被 skipped_no_mock 截断）；注记中的
        # visible_chars 取压缩后 length_report，与落库 word_count（最终 prose 实测）
        # 同口径。其他场景（带内 / 单轮已落带）不写 deviations，避免污染 W-LEN 报告。
        condense_rounds_now = int(ctx.get("condense_rounds") or 0)
        if (
            condense_rounds_now >= _MAX_CONDENSE_ROUNDS
            and not bool(ctx.get("length_check_passed"))
            and isinstance(length_report, dict)
            and length_report.get("status") in ("over", "under")
        ):
            dev_entry = {
                "from": "word_count_target",
                "to": (
                    f"实测 {int(length_report.get('visible_chars') or word_count)} 字 "
                    f"(target={int(length_report.get('target') or 0)}, "
                    f"band=[{int(length_report.get('band_low') or 0)},"
                    f"{int(length_report.get('band_high') or 0)}], "
                    f"deviation_pct={float(length_report.get('deviation_pct') or 0.0)}%, "
                    f"status={length_report.get('status')})"
                ),
                "reason": (
                    "字数闭环 condense 2 轮后仍超带，按生产实证 ch4-6 规律"
                    "（writer 原始产出 4721-5842 字 vs 自报 3008-3172，偏差 50%+）"
                    "标记为 report-only 兜底；下游评审 W-LEN 报告应据此评估。"
                ),
                "source": "chapter_write.length_check",
                "round": condense_rounds_now,
                "created_at": now,
            }
            try:
                chap_row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?",
                    (chapter_id,),
                ).fetchone()
                plan_obj: dict[str, Any] = {}
                if chap_row is not None and chap_row["plan_json"]:
                    try:
                        parsed_plan = json.loads(chap_row["plan_json"])
                        if isinstance(parsed_plan, dict):
                            plan_obj = parsed_plan
                    except (TypeError, ValueError):
                        plan_obj = {}
                deviations = plan_obj.get("deviations")
                if not isinstance(deviations, list):
                    deviations = []
                deviations.append(dev_entry)
                plan_obj["deviations"] = deviations
                conn.execute(
                    "UPDATE chapters SET plan_json = ?, updated_at = ? "
                    "WHERE chapter_id = ?",
                    (json.dumps(plan_obj, ensure_ascii=False), now, chapter_id),
                )
            except sqlite3.OperationalError as exc:
                # 缺列 / 缺表：兜底不阻断 save_draft（极老库 / 迁移未跑）。
                #
                # 2026-10-16 批次十收窄：注释主张的就是「缺列 / 缺表」，故只让结构缺失
                # 降级。**这是本组风险最高的一处**——try 包住的是**写**
                # （``UPDATE chapters SET plan_json``），而 WAL 单写者：并发写/嵌套
                # 另开连接写库会等 busy_timeout 5s 后抛
                # ``OperationalError: database is locked``（AGENTS 坑区 M1-a 实证，
                # daemon 线程还会把 traceback 吞掉）。原先裸 except 会把那次写失败
                # 读成「本章没触发 condense 偏差」，而下游评审核销 W-LEN 意见正是
                # 依据这条 deviations 注记 ⇒ **注记没落库被当成「没有偏差」**，
                # 零告警。收窄后上抛：本 run 显式失败，作者重试即可。
                if not is_missing_schema_error(exc):
                    raise
        # 先看是否有 draft 行（按 chapter + version 累计）
        max_row = conn.execute(
            "SELECT MAX(version) AS v FROM drafts WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        next_v = (max_row["v"] or 0) + 1
        conn.execute(
            """
            INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,
                                prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                chapter_id,
                next_v,
                prose,
                # created_by 与 prompt_version 同值（历史形态即如此，质量模块按
                # 「writer:」前缀判定 AI 侧，见 quality/service.py::classify_created_by）；
                # 两列现在都取真值，不再硬编码 writer:v1。
                prompt_version,
                prompt_version,
                ctx.get("writer_model_id") or "mock/mock",
                now,
            ),
        )
        # chapters.status 推进/回退：
        # - PLANNED→DRAFTED：首写；
        # - DRAFTED：重跑追加新 draft 版本（人工改稿循环）；
        # - REVIEWED→DRAFTED：批准后改稿（双层评审 SOP 的 smart 二审可能推翻
        #   critic 批准）——新草稿使旧批准失效，回退 DRAFTED 强制重审；
        # 与 domain chapter 服务 _DRAFT_ALLOWED_STATUS={DRAFTED, REVIEWED} 口径一致；
        # COMMITTED/RELEASED 仍硬拒（须先 rollback commit）。
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        status = cur["status"]
        if status == "PLANNED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status == "REVIEWED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status != "DRAFTED":
            raise ValueError(
                f"chapter {chapter_id} status={status} 不允许 write，"
                "仅 PLANNED/DRAFTED/REVIEWED 可写"
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "draft_id": draft_id,
        "draft_version": next_v,
        "word_count": word_count,
        "prompt_version": prompt_version,
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
        # F-10 ③ 可观测钩子：规整改了什么、正文里还剩什么内部标识（只报不修）。
        "normalize_changes": _normalize_changes,
        "internal_identifiers": _internal_ids,
    }
