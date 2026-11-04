"""B-1 评审与提交链实证探针（2026-11-04 B 档逐条实证批）。

覆盖（审计报告 §四 B-1）：
- B1-1 ``_latest_review_report_draft_version`` 不筛 ``wr.status``：被驳回/异常终止的
  review run 的 ``review_report.draft_version`` 会被质量门当「被审版本」。
- B1-2 显式 ``draft_version`` 指向不存在草稿 ⇒ 「量空正文放行」（审计写于批次三十五
  修复之前 ⇒ 预期**推翻**：现实现应判「不可检硬停」）。
- B1-3 delta 残留：门禁阻断前 delta 已落库（status='validated'），无任何清理路径；
  并验证残留行的误提交防线（乐观锁）。

只写仓外临时目录；零真实 LLM 调用。
"""

from __future__ import annotations

from probe_b_common import (  # noqa: I001  # pylint: disable=wrong-import-order
    Recorder,
    force_utf8_stdout,
    insert_chapter,
    insert_review_run,
    make_temp_db,
    verify_worktree_import,
)

force_utf8_stdout()
verify_worktree_import()

from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import new_id  # noqa: E402
from packages.core.quality.scoring import DRAFT_UNAVAILABLE, is_draft_unavailable  # noqa: E402
from packages.core.quality.service import build_quality_context  # noqa: E402
from packages.core.story_state.service import StoryStateService  # noqa: E402
from packages.domain.project.models import ProjectCreate  # noqa: E402
from packages.domain.project.service import ProjectService  # noqa: E402
from packages.workflows.chapter_commit.gate import _reviewed_draft_version  # noqa: E402


def make_project(db_path: str) -> str:
    return ProjectService(db_path).create(ProjectCreate(name="B1 探针项目"))["project_id"]


def insert_draft(db_path: str, chapter_id: str, version: int, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'human', datetime('now'))",
            (new_id("drf"), chapter_id, version, content),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    rec = Recorder("B-1 评审与提交链")
    db_path, tmp = make_temp_db("b1")

    # ---------------------------------------------------------------- B1-1
    # 场景：R1（COMPLETED，审的是 v2，先开始）；R2（FAILED=被驳回，审的是 v1，后开始）。
    # 最新草稿是 v2。按「COMPLETED 权威」口径应取 v2；按现实现（started_at DESC 无
    # status 过滤）会取 R2 的 v1 ⇒ 门禁去量一个「被人驳回的旧版本」。
    pid = make_project(db_path)
    cid = insert_chapter(db_path, pid)
    insert_draft(db_path, cid, 1, "被驳回的旧稿。" * 30)
    insert_draft(db_path, cid, 2, "人审通过的最新稿。" * 30)
    insert_review_run(db_path, cid, status="COMPLETED", draft_version=2,
                      started_at="2026-11-04T10:00:00+00:00")
    insert_review_run(db_path, cid, status="FAILED", draft_version=1,
                      started_at="2026-11-04T11:00:00+00:00")
    got = _reviewed_draft_version(db_path, cid, {})
    rec.confirm(
        "B1-1 gate._reviewed_draft_version 不筛 wr.status（FAILED run 的版本被采信）",
        f"最新草稿=v2、COMPLETED 评审=v2、FAILED(驳回) 评审=v1 ⇒ 实测返回 {got}"
        f"（取了 FAILED run 的 v1；同域 _latest_completed_review_end / commit 内层守卫"
        f"均筛 status='COMPLETED'，唯此处不筛）",
    ) if got == 1 else rec.refute(
        "B1-1 gate._reviewed_draft_version 不筛 wr.status",
        f"实测返回 {got}（预期 1 才能证明无 status 过滤）",
    )

    # ---------------------------------------------------------------- B1-2
    # 审计断言：显式 draft_version 指向不存在草稿 ⇒ 量空正文拿满分放行。
    # 批次三十五（2026-11-04）已改为「不可检硬停」⇒ 预期推翻。
    pid2 = make_project(db_path)
    cid2 = insert_chapter(db_path, pid2)
    insert_draft(db_path, cid2, 1, "存在的正文。" * 50)
    ctx = build_quality_context(db_path, project_id=pid2, chapter_id=cid2,
                                delta={}, snapshot_pre={}, draft_version=99)
    unavailable = ctx.draft == DRAFT_UNAVAILABLE
    if unavailable:
        rec.refute(
            "B1-2 显式 draft_version 不存在 ⇒ 量空正文放行",
            "实测 build_quality_context(draft_version=99) 返回 DRAFT_UNAVAILABLE 哨兵"
            "（service.py:872-885 批次三十五已改判「不可检」），gate.py:523/538-540 "
            "draft_unavailable ⇒ should_block 恒真 ⇒ 硬停；审计描述的是修复前状态",
        )
    else:
        rec.confirm(
            "B1-2 显式 draft_version 不存在 ⇒ 量空正文放行",
            f"实测 draft={ctx.draft[:40]!r}…（未判不可检）",
        )
    # 同场景分数面证据：不可检 ⇒ 子分 0 + 阻断级 issue，不是 100 分 0 issue。
    from packages.core.quality.engine import QualityEngine
    report = QualityEngine().evaluate(ctx)
    blocking = [i for i in report.issues if i.rule_id == "scoring_missing_subscore"]
    rec.refute(
        "B1-2（分数面）量空正文 ⇒ overall=100 / issues=[]",
        f"实测 overall={report.overall}、scoring_missing_subscore 出现 {len(blocking)} 条"
        f"（不可检 ⇒ 子分 0 + 阻断级 issue；is_draft_unavailable="
        f"{is_draft_unavailable(ctx.draft)}）",
    ) if (report.overall != 100 and blocking) else rec.confirm(
        "B1-2（分数面）量空正文 ⇒ overall=100 / issues=[]",
        f"实测 overall={report.overall}",
    )

    # ---------------------------------------------------------------- B1-3
    # delta 残留：submit_delta 落 'validated' 行；无清理路径；误提交被乐观锁挡住。
    svc = StoryStateService(db_path)
    delta = {
        "delta_id": new_id("dlt"),
        "delta_version": 1,
        "chapter_id": cid2,
        "workflow_run_id": "wfr_probe",
        "previous_state_version": 1,
        "created_by": "probe:b1",
        "created_at": "2026-11-04T00:00:00Z",
        "schema_version": "state-delta-v0",
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    submit = svc.submit_delta(delta)
    if submit.get("status") != "validated":
        rec.undecidable(
            "B1-3 delta 残留", f"夹具 delta 未通过校验：{submit.get('errors')}"
        )
        print(f"\n（临时库：{tmp}）")
        return rec.summary()
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM state_deltas WHERE delta_id = ?",
            (submit["delta_id"],),
        ).fetchone()
    finally:
        conn.close()
    residue_status = row["status"] if row else "<无行>"
    # 静态面：全仓无 DELETE FROM state_deltas。
    hits: list[str] = []
    for py in (tmp.parent).glob("**/*.py"):  # pragma: no cover — 不依赖此循环
        pass
    import packages
    pkg_root = packages.__path__[0]  # type: ignore[attr-defined]
    from pathlib import Path as _P
    for py in _P(pkg_root).rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "DELETE FROM state_deltas" in text:
            hits.append(str(py))
    rec.confirm(
        "B1-3 delta 残留：门禁阻断/FAILED 前 delta 已落库且无清理路径",
        f"submit_delta ⇒ status={residue_status}（commit 前即落库）；"
        f"packages/ 全树 'DELETE FROM state_deltas' 命中 {len(hits)} 处 {hits or ''}"
        f"（行永久残留；危害=存储增长 + 残留行停在可提交状态）",
    ) if residue_status == "validated" and not hits else rec.refute(
        "B1-3 delta 残留", f"status={residue_status}，DELETE 命中 {hits}",
    )
    # 残留行的误提交防线：无快照时当前版本 < delta.previous_state_version ⇒ 拒绝。
    stale_error = ""
    try:
        svc.commit_delta(submit["delta_id"], {"approver": "probe", "approved": True},
                         "wfr_probe")
    except Exception as exc:  # noqa: BLE001 —— 探针只关心是否被拒
        stale_error = f"{type(exc).__name__}: {exc}"
    rec.refute(
        "B1-3（防线补充）残留 validated delta 可被直接提交",
        f"实测 commit_delta(旧 delta) ⇒ {stale_error}"
        f"（版本错配被拒 ⇒ 残留行不能静默生效；版本恰好对齐时仍可手工提交——见报告）",
    ) if stale_error else rec.confirm(
        "B1-3（防线补充）残留 validated delta 可被直接提交",
        "实测旧 delta 提交成功 ⇒ 残留行可直接生效（需人工复核）",
    )

    print(f"\n（临时库：{tmp}）")
    return rec.summary()


if __name__ == "__main__":
    raise SystemExit(main())
