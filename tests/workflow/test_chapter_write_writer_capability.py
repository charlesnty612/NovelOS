"""chapter_write writer 节点 capability_override 路由断言。

需求：writer 节点 revise 模式（revision_note + 既有 draft）应给
``run_agent`` 传 ``capability_override="light"``；write / fresh_write
模式传 ``None``。

策略：不走完整 HTTP/Workflow 引擎，直接构造最小 ctx 调
``_writer_node``，并 monkeypatch ``run_agent`` 抓 kwargs。
这样不依赖真实 provider 与 migration 副作用，速度快、断言精确。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.workflows.chapter_write import pipeline as cw_pipeline

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _create_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _insert_chapter_with_draft(db_path: str, *, chapter_id: str, draft: str) -> None:
    """插入 project + chapter + 一份 draft，让 _latest_draft_text 能查到内容。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "proj_cap_test",
                "能力路由测试",
                "ACTIVE",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chapter_id,
                "proj_cap_test",
                1,
                "能力路由测试",
                json.dumps({"chapter_goal": "测试", "expected_word_count": 3000}, ensure_ascii=False),
                "PLANNED",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, model_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"dr_{chapter_id}_v1",
                chapter_id,
                1,
                draft,
                "agent:writer:v1",
                "mock/mock",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_chapter_only(db_path: str, *, chapter_id: str) -> None:
    """插入 chapter 但不挂 draft（write 模式用例）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "proj_cap_test",
                "能力路由测试",
                "ACTIVE",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chapter_id,
                "proj_cap_test",
                1,
                "能力路由测试",
                json.dumps({"chapter_goal": "测试", "expected_word_count": 3000}, ensure_ascii=False),
                "PLANNED",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _make_writer_ctx(
    *,
    db_path: str,
    chapter_id: str,
    loaded_plan: dict,
    mock_providers: dict | None,
) -> dict:
    return {
        "db_path": db_path,
        "run_id": "run_cap_test",
        "chapter_id": chapter_id,
        "scene_plan": {"scenes": []},
        "loaded_plan": loaded_plan,
        "mock_providers": mock_providers,
        "fresh_write": False,
        "model_overrides": {},
        "_current_node_run_id": "nrun_cap_test",
        "target_word_count": 3000,
    }


def _patched_run_agent(monkeypatch: pytest.MonkeyPatch, captured: list[dict]):
    """把 packages.core.agent_runtime.runner.run_agent 替换为捕获器。"""

    def fake_run_agent(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        # 返回合规 writer-output.v1 字典，避免 _writer_node 后续 db 逻辑报 schema 错误
        return {
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": "ch_cap_test",
            "prose": "captured prose",
            "self_report": {
                "slots_filled": [],
                "word_count": 6,
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }

    monkeypatch.setattr(
        "packages.workflows.chapter_write.pipeline.run_agent",
        fake_run_agent,
        raising=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_writer_node_revise_mode_passes_capability_light(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """revise 模式（既有 draft + revision_note）→ capability_override='light'。"""
    db_path = _create_db(tmp_path)
    chapter_id = "ch_cap_revise"
    _insert_chapter_with_draft(
        db_path,
        chapter_id=chapter_id,
        draft="既有 v1 草稿全文：苏婉清坐在窗下。",
    )

    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    ctx = _make_writer_ctx(
        db_path=db_path,
        chapter_id=chapter_id,
        loaded_plan={
            "chapter_goal": "测试",
            "expected_word_count": 3000,
            "revision_note": "第二段过场删掉",
        },
        mock_providers={"writer": ["{any}"]},  # 传 mock 仍要断言 capability_override 被传入
    )

    out = cw_pipeline._writer_node(ctx)

    assert len(captured) == 1, f"run_agent 应被调用 1 次，实际 {len(captured)} 次"
    kwargs = captured[0]["kwargs"]
    # 主断言：revise 模式下必须把 capability_override 显式钉为 'light'
    assert kwargs.get("capability_override") == "light", (
        f"revise 模式应传 capability_override='light'，实际 {kwargs.get('capability_override')!r}"
    )
    # 副断言：mode 走对路径
    assert out["writer_input"]["mode"] == "revise"
    # profile_id 仍走 model_overrides 路径（保持原行为）
    assert kwargs.get("profile_id") is None
    # mock_script 仍按 mock_providers['writer'] 传入（验证拦截位置正确）
    assert kwargs.get("mock_script") == ["{any}"]


def test_writer_node_write_mode_passes_capability_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """write 模式（无 revision_note）→ capability_override=None（走默认 creative_writing）。"""
    db_path = _create_db(tmp_path)
    chapter_id = "ch_cap_write"
    # 没插 draft，没 revision_note → 自然走 'write' 模式
    _insert_chapter_only(db_path, chapter_id=chapter_id)

    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    ctx = _make_writer_ctx(
        db_path=db_path,
        chapter_id=chapter_id,
        loaded_plan={"chapter_goal": "测试", "expected_word_count": 3000},
        mock_providers={"writer": ["{any}"]},
    )

    out = cw_pipeline._writer_node(ctx)

    assert len(captured) == 1
    kwargs = captured[0]["kwargs"]
    # 主断言：write 模式 capability_override 必须为 None
    assert kwargs.get("capability_override") is None, (
        f"write 模式应传 capability_override=None，实际 {kwargs.get('capability_override')!r}"
    )
    # 副断言：mode 走对路径
    assert out["writer_input"]["mode"] == "write"


def test_writer_node_fresh_write_passes_capability_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """fresh_write=True 即便有 draft + revision_note → mode='write' → capability_override=None。"""
    db_path = _create_db(tmp_path)
    chapter_id = "ch_cap_fresh"
    _insert_chapter_with_draft(
        db_path,
        chapter_id=chapter_id,
        draft="既有 v1 全文。",
    )

    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    ctx = _make_writer_ctx(
        db_path=db_path,
        chapter_id=chapter_id,
        loaded_plan={
            "chapter_goal": "测试",
            "expected_word_count": 3000,
            "revision_note": "全章重写",  # 即便存在，也会被 fresh_write 兜底
        },
        mock_providers={"writer": ["{any}"]},
    )
    ctx["fresh_write"] = True  # 触发 fresh_write 全章重写

    out = cw_pipeline._writer_node(ctx)

    assert len(captured) == 1
    kwargs = captured[0]["kwargs"]
    assert kwargs.get("capability_override") is None, (
        f"fresh_write=True 应保持 capability_override=None，实际 {kwargs.get('capability_override')!r}"
    )
    assert out["writer_input"]["mode"] == "write"
