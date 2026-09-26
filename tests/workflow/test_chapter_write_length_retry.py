"""chapter_write writer 节点：生成期字数闭环（P0-2，2026-09-18）。

需求（实证 prj_2567bb8de642 / ch_92bac068ff0d，target 2500 / 带 2125~2875）：
writer 首次产出 932 字（多次 713 / 854 / 1096 / 1199 / 1434），节点**静默完成**，
欠带一路滚到 review 才被 W-LEN 抓出；唯一推得动字数的是审校触发的 revise 回路，
而 revise 把正文改出一堆重复段落。⇒ 长度必须在生成期闭环。

契约（本文件逐条钉住）：
1. 首次产出即在带内 ⇒ **恰好 1 次** writer 调用（happy path 零回归）；
2. 首次欠带（visible < band_low）⇒ 节点内**再调** writer，且第二次的
   ``payload["mode"] == "write"``（不是 revise）、``draft_text == ""``、
   携带 ``length_directive``（kind='expand_under_band' + 缺口字数）；
3. 重写落带 ⇒ 采纳更长/更近带的一次（``writer_output.prose`` 是它）；
4. 次数有界：全部欠带时调用数 = 1 + ``NOVELOS_WRITER_LENGTH_RETRIES``（默认 2）；
   env=0 关闭重写；
5. 重写抛异常 ⇒ fail-soft（保留已有产出，节点不炸）；
6. 尝试记录进 ctx（``writer_length_attempts`` / ``writer_length_accepted_attempt``）。

策略：不跑引擎 / HTTP，直接构造最小 ctx 调 ``_writer_node``，monkeypatch
``run_agent`` 按调用序返回脚本产出（与 test_chapter_write_writer_capability.py 同款）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.workflows.chapter_write import pipeline as cw_pipeline

TARGET = 2500  # band_low=2125 / band_high=2875（默认 0.85/1.15/1200）

_UNDER = 900
_IN_BAND = 2500
_OVER = 3000


def _fresh_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path)
    return db_path


def _insert_chapter(db_path: str, *, chapter_id: str, number: int = 1) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("prj_len", "字数闭环测试", "ACTIVE", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chapter_id,
                "prj_len",
                number,
                "字数闭环测试",
                json.dumps({"chapter_goal": "测试", "expected_word_count": TARGET}),
                "PLANNED",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_draft(db_path: str, *, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "model_id, created_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
            (f"dr_{chapter_id}", chapter_id, content, "writer:v3", "mock/mock",
             "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _writer_output(prose: str) -> dict[str, Any]:
    return {
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v3",
        "chapter_id": "ch_len",
        "prose": prose,
        "self_report": {
            "slots_filled": [],
            "word_count": len(prose),
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "",
        },
    }


def _writer_ctx(
    db_path: str, chapter_id: str, *, plan: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "db_path": db_path,
        "run_id": "run_len_test",
        "chapter_id": chapter_id,
        "scene_plan": {"scenes": [{"scene_id": "scene_001", "purpose": "A"}]},
        "loaded_plan": plan if plan is not None else {
            "chapter_goal": "测试", "expected_word_count": TARGET,
        },
        "mock_providers": {"writer": ["{}"]},
        "fresh_write": False,
        "model_overrides": {},
        "_current_node_run_id": "nrun_len_test",
        "target_word_count": TARGET,
    }
    ctx.update(overrides)
    return ctx


def _scripted_run_agent(
    monkeypatch: pytest.MonkeyPatch, outputs: list[Any],
) -> list[dict[str, Any]]:
    """按调用序返回 ``outputs``（用尽后重复最后一条）；返回捕获列表。"""
    calls: list[dict[str, Any]] = []

    def fake_run_agent(db_path, agent_name, payload, run_id, **kw):
        calls.append({"agent_name": agent_name, "payload": payload, "kwargs": kw})
        out = outputs[min(len(calls) - 1, len(outputs) - 1)]
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(cw_pipeline, "run_agent", fake_run_agent)
    return calls


# ---------------------------------------------------------------------------
# 1) happy path：首次即在带内 ⇒ 恰好 1 次调用（零回归断言）
# ---------------------------------------------------------------------------


def test_writer_in_band_on_first_try_calls_once(monkeypatch, tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_in_band"
    _insert_chapter(db_path, chapter_id=cid)
    calls = _scripted_run_agent(monkeypatch, [_writer_output("中" * _IN_BAND)])

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert len(calls) == 1, f"带内不应重写；实际 {len(calls)} 次调用"
    assert out["writer_output"]["prose"] == "中" * _IN_BAND
    assert out["writer_length_accepted_attempt"] == 1
    assert out["writer_length_attempts"] == [
        {
            "attempt": 1,
            "mode": "write",
            "visible_chars": _IN_BAND,
            "target": TARGET,
            "band_low": 2125,
            "band_high": 2875,
            "status": "in_band",
            "distance": 0,
            "in_band": True,
        }
    ]


# ---------------------------------------------------------------------------
# 2) 欠带 ⇒ 节点内以 mode='write' 整章重写，且采纳更长的一次
# ---------------------------------------------------------------------------


def test_writer_under_band_rewrites_in_write_mode_and_keeps_longer(monkeypatch, tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_under"
    _insert_chapter(db_path, chapter_id=cid)
    calls = _scripted_run_agent(
        monkeypatch,
        [_writer_output("短" * _UNDER), _writer_output("长" * _IN_BAND)],
    )

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert len(calls) == 2, f"欠带应触发 1 次重写；实际 {len(calls)} 次调用"
    # 主断言：第二次必须走 write（不是 revise）——revise 的 +5% 上限追不回欠带
    retry_payload = calls[1]["payload"]
    assert retry_payload["mode"] == "write", (
        f"重写轮必须是 mode='write'（revise 被 +5% 上限钉死）；"
        f"实际 {retry_payload['mode']!r}"
    )
    assert retry_payload["draft_text"] == ""
    assert "revision_note" not in retry_payload
    directive = retry_payload["length_directive"]
    assert directive["kind"] == "expand_under_band"
    assert directive["previous_visible_chars"] == _UNDER
    assert directive["shortfall_chars"] == 2125 - _UNDER
    assert directive["band_low"] == 2125 and directive["band_high"] == 2875
    assert "整章重写" in directive["instruction"]
    # 采纳更长（落带）的一次
    assert out["writer_output"]["prose"] == "长" * _IN_BAND
    assert out["writer_length_accepted_attempt"] == 2
    assert [a["status"] for a in out["writer_length_attempts"]] == ["under", "in_band"]
    assert out["writer_input"]["mode"] == "write"
    assert out["writer_input"]["length_directive"]["kind"] == "expand_under_band"


def test_writer_revise_mode_under_band_retry_switches_to_write(monkeypatch, tmp_path: Path):
    """首次是 revise（有 draft + revision_note）且欠带 → 重写轮仍必须 mode='write'。"""
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_revise"
    _insert_chapter(db_path, chapter_id=cid)
    _insert_draft(db_path, chapter_id=cid, content="上一版正文。")
    calls = _scripted_run_agent(
        monkeypatch,
        [_writer_output("短" * _UNDER), _writer_output("长" * _IN_BAND)],
    )

    out = cw_pipeline._writer_node(
        _writer_ctx(
            db_path, cid,
            plan={
                "chapter_goal": "测试",
                "expected_word_count": TARGET,
                "revision_note": "第二段压缩到 2 句",
            },
        )
    )

    assert calls[0]["payload"]["mode"] == "revise"
    assert calls[0]["payload"]["draft_text"] == "上一版正文。"
    assert len(calls) == 2
    assert calls[1]["payload"]["mode"] == "write"
    assert calls[1]["payload"]["draft_text"] == ""
    # 采纳重写结果 ⇒ writer_input 记录的是重写轮的真实输入
    assert out["writer_input"]["mode"] == "write"
    assert out["writer_length_accepted_attempt"] == 2


# ---------------------------------------------------------------------------
# 3) 次数有界（默认 2）+ 取最优
# ---------------------------------------------------------------------------


def test_writer_retries_are_bounded_and_best_attempt_kept(monkeypatch, tmp_path: Path):
    """三次全欠带（900 / 1500 / 1900）⇒ 调用数 = 1 + 默认 2；采纳最长的 1900。"""
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_bounded"
    _insert_chapter(db_path, chapter_id=cid)
    calls = _scripted_run_agent(
        monkeypatch,
        [_writer_output("中" * 900), _writer_output("中" * 1500), _writer_output("中" * 1900)],
    )

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert len(calls) == 1 + cw_pipeline._LENGTH_RETRY_DEFAULT
    assert out["writer_length_accepted_attempt"] == 3
    assert out["writer_output"]["prose"] == "中" * 1900
    assert [a["visible_chars"] for a in out["writer_length_attempts"]] == [900, 1500, 1900]
    assert all(a["mode"] == "write" for a in out["writer_length_attempts"][1:])


def test_writer_retry_disabled_by_env_knob(monkeypatch, tmp_path: Path):
    """``NOVELOS_WRITER_LENGTH_RETRIES=0`` ⇒ 关闭生成期重写（只写一次）。"""
    monkeypatch.setenv(cw_pipeline._LENGTH_RETRY_ENV, "0")
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_knob_off"
    _insert_chapter(db_path, chapter_id=cid)
    calls = _scripted_run_agent(monkeypatch, [_writer_output("短" * _UNDER)])

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert len(calls) == 1
    assert out["writer_length_attempts"][0]["status"] == "under"


def test_writer_retry_env_knob_invalid_falls_back_to_default(monkeypatch, tmp_path: Path):
    """env 非法值 → 回落默认 2（不抛错，节点不炸）。"""
    monkeypatch.setenv(cw_pipeline._LENGTH_RETRY_ENV, "many")
    assert cw_pipeline._resolve_writer_length_retries({}) == cw_pipeline._LENGTH_RETRY_DEFAULT


def test_writer_ctx_knob_overrides_env(monkeypatch, tmp_path: Path):
    """ctx 显式 ``writer_length_retries`` 优先于 env（与 _resolve_auto_revise_max 同层级）。"""
    monkeypatch.setenv(cw_pipeline._LENGTH_RETRY_ENV, "0")
    assert cw_pipeline._resolve_writer_length_retries({"writer_length_retries": 1}) == 1


# ---------------------------------------------------------------------------
# 4) 重写失败 fail-soft（保留已有产出）
# ---------------------------------------------------------------------------


def test_writer_retry_failure_keeps_previous_attempt(monkeypatch, tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_retry_fail"
    _insert_chapter(db_path, chapter_id=cid)
    calls = _scripted_run_agent(
        monkeypatch,
        [_writer_output("短" * _UNDER), RuntimeError("provider down")],
    )

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert len(calls) == 2
    assert out["writer_output"]["prose"] == "短" * _UNDER  # 保留首次产出
    assert out["writer_length_accepted_attempt"] == 1
    assert len(out["writer_length_attempts"]) == 1  # 失败尝试不入册


# ---------------------------------------------------------------------------
# 5) 场景预算元字段随节点产出透出（薄计划信号在 run 层可见）
# ---------------------------------------------------------------------------


def test_writer_node_surfaces_scene_word_budget_meta(monkeypatch, tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    cid = "ch_len_budget_meta"
    _insert_chapter(db_path, chapter_id=cid)
    _scripted_run_agent(monkeypatch, [_writer_output("中" * _IN_BAND)])

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    meta = out["scene_word_budget"]
    assert meta["target"] == TARGET
    assert meta["scene_count"] == 1
    assert meta["budget_sum"] == TARGET
    assert meta["thin_plan"] is True  # target ≥2000 且只 1 个 scene
    assert "thin_plan" in meta["warning"]
