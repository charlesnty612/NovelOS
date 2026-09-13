"""M2 回归：checkpoint_exclude 必须覆盖「节点镜像键」（V3.9 全量检修）。

缺陷形状：chapter-commit / chapter-review 的 ``checkpoint_exclude`` 只列了数据键，
缺 ``node_id``。引擎除顶层 merge 外还会把整套节点产出存 ``ctx[node_id]``
（``engine.py`` ``ctx[node.node_id] = output``）→ 被排除的数据经镜像键原样落盘，
exclude 形同虚设（生产实测：commit run 89KB 中 59KB 是被排除数据）。

chapter-write 早已示范该口径（``checkpoint_exclude`` 收录全部 7 个 node_id）。

保留口径（本用例同时钉住，防止误伤）：
- Human 节点镜像键必须保留：``ctx[node_id].__pause_payload__`` 是前端审批卡契约
  （``apps/web/src/utils/pausePayload.ts`` 从 ``checkpoint_json[node_id]`` 取 payload；
  tests/workflow/test_checkpoint_exclude_promotion.py 断言其存在）；
- chapter-commit 的 ``quality_gate`` 镜像键必须保留：前端 QualityPanel 从
  ``checkpoint_json['quality_gate']`` 读参照系消费 / 改稿引导
  （``apps/web/src/hooks/useChapterRunOrchestration.ts``；
  chapter_write/pipeline.py 亦有「勿照搬」口径提醒）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_commit.pipeline import WORKFLOW as COMMIT_WORKFLOW
from packages.workflows.chapter_review.pipeline import WORKFLOW as REVIEW_WORKFLOW

# 非 Human 节点镜像键中「必须进 exclude」的集合（其余两个键为消费契约，见模块 docstring）
_COMMIT_MIRROR_EXCLUDE = {
    "build_observer_ctx",
    "observer",
    "inject_validate",
    "commit",
    "summarize",
}
_COMMIT_MIRROR_KEEP = {"high_risk_approval", "quality_gate"}
_REVIEW_MIRROR_EXCLUDE = {"basic_checks", "critic_review", "deep_review", "mark_reviewed"}
_REVIEW_MIRROR_KEEP = {"author_review"}


# ---------------------------------------------------------------------------
# helpers（与既有 workflow 集成测试同款：httpx ASGI + asyncio.run）
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _get_run_via_http(app, run_id: str) -> dict | None:
    async with _make_client(app) as client:
        r = await client.get(f"/api/runs/{run_id}")
    return None if r.status_code == 404 else r.json()


async def _wait_run_status(app, run_id: str, expected: tuple, timeout: float = 60.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        assert run is not None, f"run {run_id} 消失"
        last = run
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内到 {expected}（last={last}")


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _make_project(app, name: str = "mirror-keys") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, n: int = 1) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters", json={"number": n, "title": f"C{n}"}
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


_PROSE_HEAD = "测试正文。本章不涉及任何剧情。灯芯闪了一下。"
_PROSE_FILLER = "校验用正文片段，不含禁用词。"
_PROSE = _PROSE_HEAD + _PROSE_FILLER * 300


def _seed_plan_and_draft(db_path: Path, cid: str) -> None:
    """直插 plan_json + 一行草稿并把 chapter 推到 DRAFTED（绕开 plan/write 链）。"""
    plan = {
        "chapter_goal": "夜谈中女主第一次怀疑男主",
        "core_conflict": "求真 vs 隐瞒",
        "turning_point": "林渊回避黑玉佩细节",
        "expected_role": "setup",
        "expected_word_count": 3000,
        "key_beats": [],
        "deviations": [],
    }
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ?, status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), now_iso(), cid),
        )
        conn.execute(
            "INSERT INTO drafts "
            "(draft_id, chapter_id, version, content, created_by, prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'agent:writer:v1', 'writer:v1', 'mock/mock', ?)",
            (new_id("dr"), cid, _PROSE, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _critic_script() -> list[str]:
    """critic mock：quote 可溯源到 ``_PROSE``（软校验不溯源的 issue 会被丢弃）。"""
    return [
        json.dumps(
            {
                "schema_version": "critic-report.v1",
                "prompt_version": "critic:v1",
                "chapter_id": "x",
                "strengths": ["节奏紧凑"],
                "overall_comment": "整体可用，局部可加强",
                "issues": [
                    {
                        "rule_id": "REQ-Q7",
                        "category": "pacing",
                        "severity": "medium",
                        "message": "key_beats 覆盖度可加强",
                        "suggestion": "对照 plan.key_beats 补全",
                        "quote": "测试正文",
                        "location": "ch1",
                    }
                ],
                "revision_guidance": [],
                "self_check_notes": "ok",
            },
            ensure_ascii=False,
        )
    ]


def _observer_low_big_script(char_id: str, chapter_id: str, notes_chars: int = 30_000) -> list[str]:
    """observer mock：LOW 风险（不触发 human 审批）+ 30KB notes（撑大被排除数据）。

    注：notes 量级受 checkpoint 体积断言约束——mock_providers 本身会进 ctx
    （引擎 ``ctx["mock_providers"]``，不在本 workflow 的 exclude 清单内），
    故 mock 脚本体积需让「落盘后」的 checkpoint 仍在 50KB 断言内，
    同时「镜像源数据」显著超过 50KB（撤修复时它们会落回 checkpoint）。
    """
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "cc_low_big_001",
                        "op": "update",
                        "target_id": char_id,
                        "character_id": char_id,
                        "facet": "state",
                        "field": "location",
                        "before": "old",
                        "after": "new",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "scene_id": None,
                            "excerpt": "灯芯闪了一下",
                            "span": None,
                        },
                        "risk_level": "LOW",
                        "notes": "N" * notes_chars,
                    },
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]


def _checkpoint(db_path: Path, run_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT checkpoint_json FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"run {run_id} 不在 workflow_runs"
    return json.loads(row["checkpoint_json"] or "{}")


def _node_output(db_path: Path, run_id: str, node_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT output_json FROM workflow_run_nodes WHERE run_id = ? AND node_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, node_id),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["output_json"] is not None, (
        f"run {run_id} 无 {node_id!r} 节点产出"
    )
    return json.loads(row["output_json"])


def _node_output_bytes(db_path: Path, run_id: str, node_ids) -> int:
    """已产生的镜像节点产出合计字节数；未执行的节点跳过（PAUSE 时下游节点无行）。"""
    total = 0
    conn = get_connection(db_path)
    try:
        for node_id in node_ids:
            row = conn.execute(
                "SELECT output_json FROM workflow_run_nodes WHERE run_id = ? AND node_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (run_id, node_id),
            ).fetchone()
            if row is not None and row["output_json"] is not None:
                total += _json_bytes(json.loads(row["output_json"]))
    finally:
        conn.close()
    return total


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------------------
# 1) 静态契约：镜像键必须进 exclude（消费契约键除外）
# ---------------------------------------------------------------------------


def test_commit_exclude_covers_node_mirrors() -> None:
    exclude = set(COMMIT_WORKFLOW["checkpoint_exclude"])
    node_ids = {n.node_id for n in COMMIT_WORKFLOW["nodes"]}
    assert _COMMIT_MIRROR_EXCLUDE <= exclude, (
        f"commit exclude 缺镜像键：{sorted(_COMMIT_MIRROR_EXCLUDE - exclude)}；"
        f"实际清单={sorted(exclude)}"
    )
    assert node_ids == _COMMIT_MIRROR_EXCLUDE | _COMMIT_MIRROR_KEEP, (
        f"节点集合变化，本用例口径需同步：{sorted(node_ids)}"
    )
    for keep in _COMMIT_MIRROR_KEEP:
        assert keep not in exclude, (
            f"{keep!r} 是消费契约（前端 pause payload / QualityPanel），不得排除"
        )


def test_review_exclude_covers_node_mirrors() -> None:
    exclude = set(REVIEW_WORKFLOW["checkpoint_exclude"])
    node_ids = {n.node_id for n in REVIEW_WORKFLOW["nodes"]}
    assert _REVIEW_MIRROR_EXCLUDE <= exclude, (
        f"review exclude 缺镜像键：{sorted(_REVIEW_MIRROR_EXCLUDE - exclude)}；"
        f"实际清单={sorted(exclude)}"
    )
    assert node_ids == _REVIEW_MIRROR_EXCLUDE | _REVIEW_MIRROR_KEEP, (
        f"节点集合变化，本用例口径需同步：{sorted(node_ids)}"
    )
    assert "author_review" not in exclude, (
        "author_review 镜像键承载 __pause_payload__（前端 reviewer UI 契约），不得排除"
    )


# ---------------------------------------------------------------------------
# 2) 端到端（mock）：review PAUSE + commit COMPLETED 的 checkpoint 断言
# ---------------------------------------------------------------------------


def test_review_and_commit_checkpoints_exclude_mirrors(tmp_path: Path):
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            char_id = await _make_character(app, pid)
            cid = await _make_chapter(app, pid)
            _seed_plan_and_draft(db_path, cid)

            # ---- review：mock critic → PAUSE 在 author_review ----
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _critic_script()}},
            )
            assert r.status_code == 201, r.text
            review_run = await _wait_run_status(
                app, r.json()["run_id"], ("PAUSED", "FAILED", "COMPLETED")
            )
            assert review_run["status"] == "PAUSED", review_run.get("error")

            ckpt_review = _checkpoint(db_path, review_run["run_id"])
            for node_id in _REVIEW_MIRROR_EXCLUDE:
                assert node_id not in ckpt_review, (
                    f"review checkpoint 仍含节点镜像键 {node_id!r}；keys={sorted(ckpt_review)}"
                )
            for key in REVIEW_WORKFLOW["checkpoint_exclude"]:
                assert key not in ckpt_review, (
                    f"review checkpoint 仍含被排除键 {key!r}；keys={sorted(ckpt_review)}"
                )
            # Human 节点镜像保留（pause payload 前端契约）
            assert "__pause_payload__" in ckpt_review.get("author_review", {}), (
                "author_review.__pause_payload__ 丢失（前端 reviewer UI 断供）"
            )
            ckpt_review_bytes = _json_bytes(ckpt_review)
            top_sizes_review = sorted(
                ((k, _json_bytes(v)) for k, v in ckpt_review.items()),
                key=lambda kv: kv[1],
                reverse=True,
            )[:5]
            assert ckpt_review_bytes < 50_000, (
                f"review checkpoint {ckpt_review_bytes} 字节，超 50KB 上限；"
                f"最大键={top_sizes_review}"
            )
            # 区分度：镜像源确实存在且非空（排除不是「本来就没有」）+ 反事实体积
            basic_out = _node_output(db_path, review_run["run_id"], "basic_checks")
            assert "review_report" in basic_out and "review_inputs" in basic_out, basic_out.keys()
            mirror_review_bytes = _node_output_bytes(
                db_path,
                review_run["run_id"],
                ("basic_checks", "critic_review", "deep_review"),
            )
            assert mirror_review_bytes > 5_000, mirror_review_bytes
            assert ckpt_review_bytes + mirror_review_bytes > ckpt_review_bytes * 1.3, (
                f"反事实体积未显著变大：ckpt={ckpt_review_bytes} mirror={mirror_review_bytes}"
            )

            # resume approve → REVIEWED（排除清单不影响 resume 语义）
            r = await _request(
                app, "POST", f"/api/runs/{review_run['run_id']}/resume",
                json={"human_input": {"approved": True}, "auto_revise_max": 0},
            )
            assert r.status_code == 200, r.text
            await _wait_run_status(app, review_run["run_id"], ("COMPLETED", "FAILED"))
            ch = await _request(app, "GET", f"/api/chapters/{cid}")
            assert ch.json()["status"] == "REVIEWED", ch.json()

            # ---- commit：mock observer（LOW + 30KB notes）→ COMPLETED ----
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": {"observer": _observer_low_big_script(char_id, cid)}},
            )
            assert r.status_code == 201, r.text
            commit_run = await _wait_run_status(
                app, r.json()["run_id"], ("COMPLETED", "PAUSED", "FAILED")
            )
            assert commit_run["status"] == "COMPLETED", commit_run.get("error")
            commit_run_id = commit_run["run_id"]

            ckpt_commit = _checkpoint(db_path, commit_run_id)
            for node_id in _COMMIT_MIRROR_EXCLUDE:
                assert node_id not in ckpt_commit, (
                    f"commit checkpoint 仍含节点镜像键 {node_id!r}；keys={sorted(ckpt_commit)}"
                )
            for key in COMMIT_WORKFLOW["checkpoint_exclude"]:
                assert key not in ckpt_commit, (
                    f"commit checkpoint 仍含被排除键 {key!r}；keys={sorted(ckpt_commit)}"
                )
            # 消费契约键保留
            assert "quality_gate" in ckpt_commit, (
                "quality_gate 是前端 QualityPanel 消费契约，不得排除"
            )
            ckpt_commit_bytes = _json_bytes(ckpt_commit)
            top_sizes = sorted(
                ((k, _json_bytes(v)) for k, v in ckpt_commit.items()),
                key=lambda kv: kv[1],
                reverse=True,
            )[:5]
            assert ckpt_commit_bytes < 50_000, (
                f"commit checkpoint {ckpt_commit_bytes} 字节，超 50KB 上限；"
                f"最大键={top_sizes}"
            )
            # 区分度：被排除的镜像源数据远超 50KB（撤修复时它们会原样落 checkpoint）
            mirror_bytes = _node_output_bytes(
                db_path, commit_run_id, _COMMIT_MIRROR_EXCLUDE
            )
            assert mirror_bytes > 50_000, (
                f"镜像源数据 {mirror_bytes} 字节，未达 50KB 量级，本用例失去区分度"
            )
            assert ckpt_commit_bytes + mirror_bytes > 100_000, (
                "反事实体积（checkpoint + 镜像源）应远超 100KB"
            )

    asyncio.run(run())
