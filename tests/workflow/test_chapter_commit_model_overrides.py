"""H1 后端测试：chapter-commit 单次 run 级 model_overrides 透传到 observer / summarizer。

V3.9.4 需求：commit 管线所有 ``run_agent`` 调用点（observer 全部路径 + summarizer
全部路径）都从 ``ctx['model_overrides']`` 取出 ``profile_id=`` 透传。

策略：参照 :file:`tests/workflow/test_chapter_write_writer_capability.py` 的写法，
monkeypatch ``packages.workflows.chapter_commit.pipeline.run_agent`` 抓 kwargs，
不走完整 HTTP/Workflow 引擎，直接调 :func:`_observer_node`。这样：
- 不依赖真实 provider；
- 不依赖 migration 副作用；
- 断言精确（profile_id=... 必传或必空）。

覆盖矩阵：
- 1. ``_observer_node`` 默认 split 路径（observer 双腿）→ 两次 run_agent 都带 ``profile_id=<observer key>``。
- 2. ``_observer_node`` off 单次路径（``ctx['observer_split']=False``）→ 1 次 run_agent 带 ``profile_id``。
- 3. summarizer（light 键）→ profile_id 不受 observer 键影响（负向断言：observer 键 ≠ light profile_id 互串）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.workflows.chapter_commit import pipeline as cc_pipeline

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _create_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _make_observer_ctx(
    *,
    db_path: str,
    observer_overrides: dict[str, str] | None,
    split: bool = True,
) -> dict[str, Any]:
    """构造 ``_observer_node`` 所需的最小 ctx。

    ``observer_input`` 是 ``build_observer_input`` 的输出壳：这里用空 dict 走通 split
    路径（``_trim_observer_input_for_leg`` 对空 dict 也能产生 leg 专用 payload）。
    """
    return {
        "db_path": db_path,
        "run_id": "run_h1_test",
        "chapter_id": "ch_h1",
        "observer_input": {
            "previous_state": {},
            "config": {"recent_event_ids": []},
        },
        "mock_providers": {
            "observer": [
                {
                    "character_changes": [],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
                {
                    "character_changes": [],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
            ],
        },
        "model_overrides": observer_overrides or {},
        "observer_split": split,
        "_current_node_run_id": "nrun_h1",
    }


def _patched_run_agent(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict],
):
    """monkeypatch commit pipeline 内的 run_agent，捕获所有 kwargs。

    返回结构合法的「合并 7 数组」dict，绕开后续 ``_merge_observer_legs`` / meta 聚合。
    """

    def fake_run_agent(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return {
            "character_changes": [],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }

    # 拆分后 run_agent 的消费方在 observer / summary 子模块（2026-09-06 批次三）；
    # 两处都 patch 以覆盖 observer 双腿与 summarizer 腿。
    monkeypatch.setattr(
        "packages.workflows.chapter_commit.observer.run_agent",
        fake_run_agent,
        raising=True,
    )
    monkeypatch.setattr(
        "packages.workflows.chapter_commit.summary.run_agent",
        fake_run_agent,
        raising=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_observer_split_path_threads_observer_profile_id_to_both_legs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """split 路径（默认）：observer 双腿 run_agent 都收到 profile_id=observer 键值。"""
    db_path = _create_db(tmp_path)
    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    observer_id = "mprof_observer_42"
    ctx = _make_observer_ctx(
        db_path=db_path,
        observer_overrides={"observer": observer_id},
        split=True,
    )

    cc_pipeline._observer_node(ctx)

    # split 路径走 observer_parallel（默认）→ 双腿并发（ThreadPoolExecutor）。
    # 至少两次 run_agent 调用（leg_a + leg_b），可选第 3 次为 summary_early。
    assert len(captured) >= 2, f"split 路径应至少调 2 次 run_agent，实际 {len(captured)}"
    # 仅断言前两次为 observer 双腿；第 3 次（如果存在）是 summary_early（summarizer），
    # 不应拿 observer 覆盖键（见下方负向测试）。
    leg_calls = captured[:2]
    for i, call in enumerate(leg_calls):
        kwargs = call["kwargs"]
        assert kwargs.get("profile_id") == observer_id, (
            f"observer 双腿第 {i} 次 run_agent 应透传 profile_id={observer_id!r}，"
            f"实际 {kwargs.get('profile_id')!r}"
        )
        # observer 键必须同时锁定 capability_override='observer'
        assert kwargs.get("capability_override") == "observer"


def test_observer_off_path_threads_observer_profile_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """off 单次路径（ctx['observer_split']=False）：1 次 run_agent 带 profile_id。"""
    db_path = _create_db(tmp_path)
    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    observer_id = "mprof_observer_off"
    ctx = _make_observer_ctx(
        db_path=db_path,
        observer_overrides={"observer": observer_id},
        split=False,
    )

    cc_pipeline._observer_node(ctx)

    # off 路径只调 1 次 observer（无 summary_early，无拆分）
    assert len(captured) == 1, (
        f"off 路径应只调 1 次 observer run_agent，实际 {len(captured)}"
    )
    kwargs = captured[0]["kwargs"]
    assert kwargs.get("profile_id") == observer_id, (
        f"off 路径应透传 profile_id={observer_id!r}，实际 {kwargs.get('profile_id')!r}"
    )


def test_observer_profile_id_absent_when_no_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """缺省零行为变更：ctx['model_overrides'] 缺或空时，profile_id=None 透传（不改变既有链路）。"""
    db_path = _create_db(tmp_path)
    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    # 场景 A：model_overrides 完全缺（None）
    ctx_a = _make_observer_ctx(
        db_path=db_path, observer_overrides=None, split=False,
    )
    cc_pipeline._observer_node(ctx_a)
    assert len(captured) == 1
    assert captured[0]["kwargs"].get("profile_id") is None

    # 场景 B：model_overrides 存在但不含 observer 键
    captured.clear()
    ctx_b = _make_observer_ctx(
        db_path=db_path, observer_overrides={"light": "mprof_unrelated"}, split=False,
    )
    cc_pipeline._observer_node(ctx_b)
    assert len(captured) == 1
    assert captured[0]["kwargs"].get("profile_id") is None


def test_observer_key_does_not_leak_to_summarizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """负向断言：observer 覆盖键只影响 observer；summarizer 不消费（mock 路径不消费
    profile_id，但即便有 summary_early 跑也应走 light 键而非 observer 键）。"""
    db_path = _create_db(tmp_path)
    captured: list[dict] = []
    _patched_run_agent(monkeypatch, captured)

    # 只设 observer 键，**不**设 light 键
    observer_id = "mprof_observer_only"
    ctx = _make_observer_ctx(
        db_path=db_path,
        observer_overrides={"observer": observer_id},
        split=True,
    )

    cc_pipeline._observer_node(ctx)

    # 找到 agent_name='summarizer' 的那次调用（如果有）
    summary_calls = [c for c in captured if c["args"][1] == "summarizer"]
    if summary_calls:
        for call in summary_calls:
            kwargs = call["kwargs"]
            # summarizer 拿到的 profile_id 必须不是 observer_id（应是 None 或 light 键值）
            assert kwargs.get("profile_id") != observer_id, (
                "summarizer 不应消费 observer 覆盖键"
            )
    # observer 双腿拿到 observer_id（已在前两个测试覆盖，这里再确认一次以兜底）
    observer_calls = [c for c in captured if c["args"][1] == "observer"]
    assert len(observer_calls) >= 2
    for call in observer_calls:
        assert call["kwargs"].get("profile_id") == observer_id
