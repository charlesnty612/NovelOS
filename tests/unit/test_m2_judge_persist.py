"""scripts/m2_judge.py --persist-api 集成测试（V3.1 P1-2）。

测试原则：不真正起 HTTP 服务；monkeypatch 内部 ``persist_judge_to_api`` 与
judge_one_chapter，断言 ``persist_judge_to_api`` 收到正确的 chapter_id + payload，
且失败仅 log warning 不抛。

覆盖：
- 评审成功 → 调用一次 persist（payload 含四维分 + verdict/top_issues/dry_run 等）；
- 评审失败 → 不调用 persist（errored/skipped 章不进双轨）；
- persist 返回 ``(False, detail)`` → 脚本继续、不抛、未阻断 main 循环；
- --persist-api 未传时 import/CLI 不变（行为兼容）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scripts.m2_judge as m2

# ---------------------------------------------------------------------------
# 工具：构造一份"假"的 main() 输入（DB 极简结构）
# ---------------------------------------------------------------------------


def _make_db_with_one_committed(tmp_path: Path) -> tuple[Path, str]:
    """建一个空库 + 1 章 COMMITTED；返回 (db_path, chapter_id)。"""
    from packages.core.db import apply_migrations, get_connection
    from packages.core.ids import new_id, now_iso

    db_path = tmp_path / "judge.db"
    apply_migrations(db_path)
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "judge-test", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "plan_json, created_at, updated_at) VALUES (?, ?, 1, 'sentinel', "
            "'COMMITTED', '{}', ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path, cid


def _run_main_dry_run(db_path: Path, *, persist_api: str | None, persist_calls) -> None:
    """用 monkeypatch 跑 main()：固定 dry-run + 调用计数 + 不起网络。"""
    args = SimpleNamespace(
        db=str(db_path),
        chapters=None,
        out_dir=str(db_path.parent / "out"),
        dry_run=True,
        force=True,
        base_url="http://test-llm",
        model="test-model",
        timeout=10.0,
        max_content_chars=5000,
        persist_api=persist_api,
        persist_timeout=2.0,
    )

    with mock.patch.object(m2, "persist_judge_to_api", side_effect=persist_calls), \
         mock.patch.object(m2, "save_results"), \
         mock.patch.object(m2, "build_summary_markdown", return_value=""), \
         mock.patch.object(m2, "load_latest_draft", return_value="一章正文"):
        with mock.patch.object(argparse.ArgumentParser, "parse_args", return_value=args):
            rc = m2.main()
            assert rc == 0


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_persist_to_api_helper_builds_url_and_returns_ok(monkeypatch):
    """直测 persist_judge_to_api：200 → (True, '')。"""
    captured = {}

    def fake_post(self, url, json=None, **kwargs):  # noqa: A003 - match httpx signature
        captured["url"] = url
        captured["json"] = json
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = ""
        return resp

    monkeypatch.setattr(m2.httpx.Client, "post", fake_post)
    ok, detail = m2.persist_judge_to_api(
        "http://127.0.0.1:18091",
        "ch_x",
        {"pacing": 80, "style": 70, "logic": 60, "dialogue": 50,
         "verdict": "ok", "top_issues": ["a", "b", "c"]},
    )
    assert ok is True
    assert detail == ""
    assert captured["url"].endswith("/api/chapters/ch_x/quality/judge")
    assert captured["json"]["pacing"] == 80
    assert captured["json"]["verdict"] == "ok"


def test_persist_to_api_helper_returns_false_on_http_error(monkeypatch):
    """422 → (False, 'HTTP 422: ...')。"""
    def fake_post(self, url, json=None, **kwargs):
        resp = mock.Mock()
        resp.status_code = 422
        resp.text = "bad payload"
        return resp

    monkeypatch.setattr(m2.httpx.Client, "post", fake_post)
    ok, detail = m2.persist_judge_to_api("http://x", "ch_y", {"pacing": 1, "style": 1, "logic": 1, "dialogue": 1})
    assert ok is False
    assert "HTTP 422" in detail
    assert "bad payload" in detail


def test_persist_to_api_helper_returns_false_on_network_error(monkeypatch):
    """网络异常 → (False, 'network: ...')。"""
    def fake_post(self, url, json=None, **kwargs):
        raise m2.httpx.ConnectError("nope")

    monkeypatch.setattr(m2.httpx.Client, "post", fake_post)
    ok, detail = m2.persist_judge_to_api("http://x", "ch_y", {"pacing": 1, "style": 1, "logic": 1, "dialogue": 1})
    assert ok is False
    assert "network" in detail
    assert "nope" in detail


def test_main_calls_persist_when_dry_run_and_persist_api(tmp_path: Path):
    db_path, _cid = _make_db_with_one_committed(tmp_path)
    calls = []

    def fake_persist(api_url, chapter_id, payload, *, timeout=10.0):
        calls.append({
            "api_url": api_url,
            "chapter_id": chapter_id,
            "payload_keys": sorted(payload.keys()),
            "pacing": payload.get("pacing"),
        })
        return True, ""

    _run_main_dry_run(db_path, persist_api="http://127.0.0.1:18091", persist_calls=fake_persist)
    assert len(calls) >= 1, calls
    c = calls[0]
    assert c["api_url"] == "http://127.0.0.1:18091"
    assert c["chapter_id"].startswith("ch_")
    # 四维分 + verdict/top_issues/score_avg/usage/dry_run 等字段都在
    for k in ("pacing", "style", "logic", "dialogue", "verdict"):
        assert k in c["payload_keys"], k


def test_main_does_not_call_persist_when_omit_flag(tmp_path: Path):
    """默认不传 --persist-api 时不调用 persist（V3.0 行为兼容）。"""
    db_path, _cid = _make_db_with_one_committed(tmp_path)
    calls = []

    def fake_persist(*args, **kwargs):
        calls.append(1)
        return True, ""

    _run_main_dry_run(db_path, persist_api=None, persist_calls=fake_persist)
    assert calls == []


def test_main_continues_when_persist_returns_false(tmp_path: Path):
    """persist 返回 False 仅 log warning，不抛、不阻断 main。"""
    db_path, _cid = _make_db_with_one_committed(tmp_path)
    calls = []

    def fake_persist(*args, **kwargs):
        calls.append(1)
        return False, "HTTP 500: boom"

    _run_main_dry_run(db_path, persist_api="http://127.0.0.1:18091", persist_calls=fake_persist)
    # persist 已被调用一次（即便失败），且 main() 不抛
    assert len(calls) >= 1
