"""B-6 / B-7 / B-8 杂项实证探针（2026-11-04 B 档逐条实证批）。

覆盖：
- B6-1 deconstruct run 归属列恒 NULL ⇒ 出现在**每个**项目的 run 列表。
- B6-2 deconstruct 启动无项目级并发守卫（静态：start_with_nodes 调用不带
  ``project_level_guard``，也不带 ``project_id``）。
- B6-9 chapters(project_id, number) 无 UNIQUE 约束 ⇒ 重号章可落库。
- B8-3 分支含 HIGH 风险 delta ⇒ promote 永远 409（无审批出路）。

只写仓外临时目录；零真实 LLM 调用。
"""

from __future__ import annotations

from probe_b_common import (  # noqa: I001  # pylint: disable=wrong-import-order
    Recorder,
    force_utf8_stdout,
    insert_chapter,
    make_temp_db,
    verify_worktree_import,
)

force_utf8_stdout()
verify_worktree_import()

from pathlib import Path  # noqa: E402

import packages  # noqa: E402
from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.core.story_state.exceptions import ApprovalRequiredError  # noqa: E402
from packages.core.story_state.service import StoryStateService  # noqa: E402
from packages.domain.chapter.service import ChapterService  # noqa: E402
from packages.domain.project.models import ProjectCreate  # noqa: E402
from packages.domain.project.service import ProjectService  # noqa: E402


def main() -> int:
    rec = Recorder("B-6 / B-7 / B-8 杂项")
    db_path, tmp = make_temp_db("b67")

    # ---------------------------------------------------------------- B6-1
    pid_a = ProjectService(db_path).create(ProjectCreate(name="项目A"))["project_id"]
    pid_b = ProjectService(db_path).create(ProjectCreate(name="项目B"))["project_id"]
    # 模拟 reference.py:471 的 run 行：chapter_id=None、不传 project_id ⇒ 归属列 NULL。
    run_id, wf_id = new_id("wfr"), new_id("wf")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'deconstruct-book', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, NULL, 'COMPLETED', NULL, '{}', ?, ?)",
            (run_id, wf_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    from packages.core.api.routers.workflows.runs import _run_visible_in_project

    chapter_service = ChapterService(db_path)
    run_row = {"project_id": None, "chapter_id": None}
    vis_a = _run_visible_in_project(run_row, chapter_service=chapter_service,
                                    project_id=pid_a)
    vis_b = _run_visible_in_project(run_row, chapter_service=chapter_service,
                                    project_id=pid_b)
    rec.confirm(
        "B6-1 deconstruct run 归属列 NULL ⇒ 出现在每个项目的 run 列表",
        f"chapter_id=NULL、project_id=NULL 的 run ⇒ 项目A 可见={vis_a}、"
        f"项目B 可见={vis_b}（_run_visible_in_project 第 3 分支『无从归属 ⇒ "
        f"保持全可见』；reference.py:471 的 start_with_nodes 调用不带 project_id= "
        f"⇒ 0037 归属列恒 NULL ⇒ 每个项目的运行记录都会看到别家拆书 run）",
    ) if (vis_a and vis_b) else rec.refute(
        "B6-1 deconstruct run 全项目可见", f"A={vis_a} B={vis_b}",
    )

    # ---------------------------------------------------------------- B6-2
    ref_src = (Path(packages.__path__[0]) / "core" / "api" / "routers"
               / "reference.py").read_text(encoding="utf-8")
    has_guard = "project_level_guard" in ref_src
    rec.confirm(
        "B6-2 deconstruct 启动无项目级并发守卫",
        f"api/routers/reference.py 全文 'project_level_guard' 命中 {has_guard}"
        f"（对照：outline_batch 走 control.py:554 project_level_guard=True；"
        f"deconstruct 的 chapter_id=None 也不受 idx_workflow_runs_active 保护 "
        f"⇒ 同项目并发 POST 拆书互不拦截）",
    ) if not has_guard else rec.refute(
        "B6-2 deconstruct 无项目级守卫", "reference.py 命中 project_level_guard",
    )

    # ---------------------------------------------------------------- B6-4
    # project_init 重跑删章的边界：已提交章被 commits FK 挡住；仅有草稿的章连草稿一起删。
    pid_d = ProjectService(db_path).create(ProjectCreate(name="项目D"))["project_id"]
    ch_committed = insert_chapter(db_path, pid_d)
    ch_draft_only = insert_chapter(db_path, pid_d)
    ch_d2 = insert_chapter(db_path, pid_d)
    now2 = now_iso()
    svc_d = StoryStateService(db_path)
    svc_d.init_genesis(pid_d, ch_d2)  # 建 v1 基线
    # 已提交章：走真实 submit+commit 落一条 commits 行（满足全部 FK）。
    _delta = {
        "delta_id": new_id("dlt"), "delta_version": 1, "chapter_id": ch_committed,
        "workflow_run_id": "wfr_probe", "previous_state_version": 1,
        "created_by": "probe:b64", "created_at": now2,
        "schema_version": "state-delta-v0", "character_changes": [],
        "world_changes": [], "relationship_changes": [], "new_events": [],
        "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
    }
    _sub = svc_d.submit_delta(_delta)
    if _sub.get("status") != "validated":
        rec.undecidable("B6-4 边界", f"夹具 delta 未过校验：{_sub.get('errors')}")
        print(f"\n（临时库：{tmp}）")
        return rec.summary()
    svc_d.commit_delta(_sub["delta_id"], {"approver": "probe", "approved": True},
                       "wfr_probe")
    conn = get_connection(db_path)
    try:
        # 仅有草稿的章：塞一条 drafts 行。
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, '未提交草稿', 'human', ?)",
            (new_id("drf"), ch_draft_only, now2),
        )
        conn.commit()
    finally:
        conn.close()
    fk_error = ""
    try:
        conn = get_connection(db_path)
        try:
            conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (ch_committed,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        fk_error = type(exc).__name__
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM drafts WHERE chapter_id = ?", (ch_draft_only,))
        conn.execute("DELETE FROM chapters WHERE chapter_id = ?", (ch_draft_only,))
        conn.commit()
        draft_only_deletable = True
    except Exception:  # noqa: BLE001
        draft_only_deletable = False
    finally:
        conn.close()
    rec.confirm(
        "B6-4 project_init 重跑删卷内全部章节：已提交章被 FK 挡（run FAILED、数据无损），"
        "仅有草稿的章连草稿一起删（真实可丢面）",
        f"删已提交章（有 commits 行）⇒ {fk_error or '成功（危险）'}；"
        f"删仅有草稿的章 ⇒ {'成功' if draft_only_deletable else '被拒'}。"
        f"对照 pipeline.py:1205-1210 的单事务注释（2026-10-23 已修原子性）⇒ "
        f"剩下的缺口正是无状态过滤对未提交内容的删除",
    ) if (fk_error and draft_only_deletable) else rec.refute(
        "B6-4 边界", f"fk_error={fk_error} draft_only_deletable={draft_only_deletable}",
    )

    # ---------------------------------------------------------------- B6-9
    dup_error = ""
    try:
        insert_chapter(db_path, pid_a, number=1)
        insert_chapter(db_path, pid_a, number=1)  # 同项目同章号第二次
    except Exception as exc:  # noqa: BLE001
        dup_error = f"{type(exc).__name__}: {exc}"
    conn = get_connection(db_path)
    try:
        n_dup = conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE project_id = ? AND number = 1",
            (pid_a,),
        ).fetchone()["n"]
    finally:
        conn.close()
    rec.confirm(
        "B6-9 chapters(project_id, number) 无 UNIQUE ⇒ 重号章可落库",
        f"同项目插入两个 number=1 ⇒ {dup_error or '成功'}；DB 实测同键行数={n_dup}"
        f"（0001 只有普通索引 idx_chapters_project_number。危害受限于调用方："
        f"outline_batch 顺序重跑走 max(number)+1 不重号，重号只在并发路径可达）",
    ) if (not dup_error and n_dup == 2) else rec.refute(
        "B6-9 重号章可落库", f"{dup_error} n={n_dup}",
    )

    # ---------------------------------------------------------------- B8-3
    pid_c = ProjectService(db_path).create(ProjectCreate(name="项目C"))["project_id"]
    ch_c = insert_chapter(db_path, pid_c)
    svc = StoryStateService(db_path)
    svc.init_genesis(pid_c, ch_c)  # 建 v1 基线（分支提交的 previous_state_version 前提）
    branch = svc.create_branch(project_id=pid_c, name="sim-probe",
                               base_state_version=1)
    branch_id = branch["branch_id"]
    delta = {
        "delta_id": new_id("dlt"),
        "delta_version": 1,
        "chapter_id": ch_c,
        "workflow_run_id": "wfr_probe",
        "previous_state_version": 1,
        "created_by": "probe:b83",
        "created_at": now_iso(),
        "schema_version": "state-delta-v0",
        "character_changes": [],
        "world_changes": [{
            "change_id": "wc_high",
            "op": "update",
            "target_id": "wr_1",
            "world_id": "wr_1",
            "world_kind": "rule",
            "field": "content",
            "before": "旧规",
            "after": "新规",
            "confidence": 0.9,
            "evidence": {"chapter_id": ch_c, "scene_id": None,
                         "excerpt": "规则变更"},
            "risk_level": "LOW",
        }],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    submit = svc.submit_delta(delta, branch_id=branch_id)
    promote_error = ""
    if submit.get("status") == "validated":
        # 分支上带 approved=True 落 HIGH 风险 delta（人工 API 路径可达）。
        svc.commit_delta(submit["delta_id"],
                         {"approver": "probe", "approved": True},
                         "wfr_probe", branch_id=branch_id)
        try:
            svc.promote_branch(pid_c, branch_id)
        except ApprovalRequiredError as exc:
            promote_error = f"ApprovalRequiredError(high_risk={exc.high_risk_change_ids})"
        except Exception as exc:  # noqa: BLE001
            promote_error = f"{type(exc).__name__}: {exc}"
    else:
        promote_error = f"submit 未过校验：{submit.get('errors')}"
    rec.confirm(
        "B8-3 分支含 HIGH 风险 delta ⇒ promote 永远 409（前后端无出路）",
        f"分支上人工 approved=True 提交 world_kind=rule 变更成功 ⇒ "
        f"promote_branch 实测 {promote_error}"
        f"（branches.py:594 重放走 main 时 author_approval 不带 approved=True "
        f"也不带 _skip_approval ⇒ commits.py:494 拒绝；promote_branch 签名"
        f"无审批参数、前端 BranchesPanel 只把 409 原文贴出来 ⇒ 死路）",
    ) if promote_error.startswith("ApprovalRequiredError") else rec.refute(
        "B8-3 promote HIGH 风险死路", promote_error,
    )

    print(f"\n（临时库：{tmp}）")
    return rec.summary()


if __name__ == "__main__":
    raise SystemExit(main())
