"""scripts/produce_chapters.py 端到端（临时库 + 独立端口 + mock_providers，无真实 LLM）。

按 ``scripts/smoke_e2e.py`` 的既有套路：起一个**临时数据库**的服务子进程（端口独立、
跑完连树杀掉并清理），用 HTTP 建项目 / 角色 / 章节与 mock 脚本，然后**把
``scripts/produce_chapters.py`` 当子进程真跑**，断言两个场景：

1. **正常章**：plan → write → review（PAUSE）→ 批准 → commit → ``COMMITTED``；
   驱动 exit 0，``--json`` 摘要里该章 ``ok=true``、章状态 COMMITTED（经 API 复核）。
2. **重复文本章**：mock writer 产出「同一段落出现两次」的草稿（报告无 error、字数在带内）
   —— 驱动必须在批准前算出章内重复率 > 8% 并**拒绝批准**：exit 2、
   ``failure.reason == "repetition_exceeds_threshold"``、章状态**不得**是 COMMITTED。

场景 2 就是本脚本存在的理由：没有这道闸门，驱动会静默批准重复文本。
字数带用**项目级覆盖**放宽（``word_band``）——这两个场景验的是批准判定，不是字数带。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SERVER_START_TIMEOUT_S = 60.0
STAGE_TIMEOUT_S = 240.0
HTTP_TIMEOUT_S = 60.0

# 干净正文（288 可见字；多段短段，实测 13 字 shingle 重复率 0.0）。
# 必须**不重复**：本测试的驱动带章内重复率闸门，重复的 fixture 会被正当地拦下。
CLEAN_PROSE = (
    "沈砚把最后一叠账册塞进木箱，箱盖压下去的时候发出一声闷响。\n\n"
    "窗外有人推着板车经过，车轮碾过青石板，颤了三下才停住。\n\n"
    "他伸手把灯芯拨短，屋里暗了下来，只剩桌角那一小圈黄。\n\n"
    "账房先生早上交来的钥匙还搁在袖袋里，铜齿硌着腕子，提醒他这处铺面已经换了东家。\n\n"
    "更鼓从街尾传过来，隔得不近，倒听得清楚。\n\n"
    "柜台底下压着一只缺口的白瓷碟，是前日收进来的抵账物，上头粘了半圈没洗净的酱色。\n\n"
    "他把碟子翻过来看了看底款，又放回原处，没有作声。\n\n"
    "后院的石榴树结得稀，枝杈伸过墙头，遮掉了对面粮行的半块招牌。\n\n"
    "学徒抱着扫帚在门槛边打盹，被脚步声惊醒，慌忙站直了身子。\n\n"
    "巷子里传来卖馄饨的吆喝，混着柴烟味，一路飘到了檐下。"
)

# 重复段落：同一段在一章内出现两次（实机 run 的病理样本）。
DUPLICATED_PARAGRAPH = (
    "茶壶里的水是温的，杯盏里的已经凉透，瓷器抵在嘴唇上带着一股沁凉。"
    "沈砚坐在这间包间的里侧，背靠墙，门在他身后，有人进来他第一个知道。"
)
DUPLICATED_PROSE = f"{DUPLICATED_PARAGRAPH}\n\n{DUPLICATED_PARAGRAPH}"


# ---------------------------------------------------------------------------
# 服务子进程（照搬 smoke_e2e 的临时库 + 独立端口套路）
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.3)
    raise AssertionError(f"GET {url} 未在 {timeout}s 内就绪: {last}")


def _kill_proc(proc: subprocess.Popen) -> None:
    """终止服务子进程（Windows 连树杀：.venv 的 python.exe 是启动器 stub）。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, check=False)
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is not None:
            return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# mock_providers（与 smoke_e2e / test_gate_revise_closure 同形）
# ---------------------------------------------------------------------------


def _director_script(chapter_id: str) -> list[str]:
    return [json.dumps({
        "schema_version": "director-plan.v1",
        "prompt_version": "director:v1",
        "chapter_id": chapter_id,
        "chapter_goal": "产线驱动端到端",
        "core_conflict": "无",
        "turning_point": "无",
        "expected_role": "setup",
        "key_beats": [{
            "beat_id": "beat_001", "purpose": "验证驱动", "involved_characters": [],
            "involved_locations": [], "involved_hooks": [], "involved_debts": [],
            "risk_level": "LOW", "narrative_question_served": "驱动是否走通",
        }],
        "character_changes_planned": [],
        "information_releases": [],
        "hook_handling": [],
        "debt_handling": [],
        "proposed_new_entities": [],
        "deviations": [],
        "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        "open_questions": [],
        "notes_for_planner": "",
    }, ensure_ascii=False)]


def _writer_script(chapter_id: str, prose: str) -> list[str]:
    return [json.dumps({
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v1",
        "chapter_id": chapter_id,
        "prose": prose,
        "self_report": {
            "slots_filled": ["slot_001"],
            "word_count": len(prose),
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "produce_chapters e2e mock",
        },
    }, ensure_ascii=False)]


def _critic_script(chapter_id: str) -> list[str]:
    return [json.dumps({
        "schema_version": "critic-report.v1",
        "prompt_version": "critic:v1",
        "chapter_id": chapter_id,
        "overall_comment": "无阻断性意见",
        "strengths": [],
        "issues": [],
    }, ensure_ascii=False)]


def _observer_script(chapter_id: str, character_id: str, excerpt: str) -> list[str]:
    return [json.dumps({
        "character_changes": [{
            "change_id": "cc_produce_e2e_1",
            "op": "add",
            "target_id": character_id,
            "character_id": character_id,
            "facet": "state",
            "field": "goal",
            "before": None,
            "after": "守住铺面",
            "confidence": 0.9,
            "evidence": {"chapter_id": chapter_id, "scene_id": "scene_001",
                         "excerpt": excerpt, "span": {"start": 0, "end": 8}},
            "risk_level": "LOW",
            "notes": "produce_chapters e2e",
            "visibility": "VISIBLE",
            "who_knows": [character_id],
            "reason": None,
        }],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }, ensure_ascii=False)]


def _write_mocks(path: Path, chapter_id: str, character_id: str, prose: str) -> Path:
    path.write_text(json.dumps({
        "director": _director_script(chapter_id),
        "writer": _writer_script(chapter_id, prose),
        "critic": _critic_script(chapter_id),
        "observer": _observer_script(chapter_id, character_id, prose[:8]),
    }, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """临时库 + 独立端口的服务子进程（模块级：两个场景共用一个服务）。"""
    tmp_dir = tmp_path_factory.mktemp("produce_chapters_e2e")
    db_path = tmp_dir / "e2e.db"
    log_path = tmp_dir / "server.log"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["NOVELOS_DB_PATH"] = str(db_path)
    env["NOVELOS_PORT"] = str(port)
    env["NOVELOS_QUALITY_GATE"] = "report"  # 本测试验驱动，不验门禁阻断
    env["PYTHONPATH"] = str(REPO_ROOT)
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "packages.core.api.main"],
        cwd=str(REPO_ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_http(f"{base_url}/api/health", timeout=SERVER_START_TIMEOUT_S)

        client = httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_S)
        # 项目：字数带覆盖放宽（两个场景验的是批准判定，不是字数带）
        resp = client.post("/api/projects", json={
            "name": "produce_chapters e2e",
            "premise": "驱动端到端",
            "word_band": {"low_ratio": 0.1, "high_ratio": 10, "floor": 10},
        })
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["project_id"]

        # 第二个项目：**不覆盖字数带**（默认带下限 1200），用于「改稿回路」场景——
        # 288 字草稿必然出 W-LEN-DEVIATION（可定向改稿的规则）⇒ 驳回改稿。
        resp = client.post("/api/projects", json={"name": "produce_chapters e2e (窄带)"})
        assert resp.status_code == 201, resp.text
        narrow_project_id = resp.json()["project_id"]

        resp = client.post(f"/api/projects/{project_id}/characters",
                           json={"name": "沈砚", "role": "protagonist"})
        assert resp.status_code == 201, resp.text
        character_id = resp.json()["character_id"]

        resp = client.post(f"/api/projects/{narrow_project_id}/characters",
                           json={"name": "沈砚", "role": "protagonist"})
        assert resp.status_code == 201, resp.text
        narrow_character_id = resp.json()["character_id"]

        for capability in ("reasoning", "creative_writing"):
            resp = client.post("/api/model-configs", json={
                "capability": capability, "provider": "mock", "model": "mock-model",
                "params_json": {}, "enabled": 1,
            })
            assert resp.status_code == 201, resp.text

        docs_dir = (REPO_ROOT / "docs" / "agents" / "prompts").as_posix()
        resp = client.post(f"/api/agents/sync?docs_dir={docs_dir}")
        assert resp.status_code == 200, resp.text

        yield {
            "base_url": base_url, "client": client, "project_id": project_id,
            "character_id": character_id,
            "narrow_project_id": narrow_project_id,
            "narrow_character_id": narrow_character_id,
            "tmp_dir": tmp_dir, "log_path": log_path,
        }
        client.close()
    finally:
        _kill_proc(proc)
        log_file.close()


def _add_chapter(server: dict, number: int, *, project_id: str | None = None) -> str:
    pid = project_id or server["project_id"]
    resp = server["client"].post(f"/api/projects/{pid}/chapters",
                                 json={"number": number, "title": f"e2e ch{number}"})
    assert resp.status_code == 201, resp.text
    return resp.json()["chapter_id"]


def _run_driver(
    server: dict,
    mocks_path: Path,
    chapters: str,
    *,
    project_id: str | None = None,
    extra_args: list[str] | None = None,
) -> tuple[int, dict, str]:
    """把 produce_chapters.py 当子进程真跑（--json：stdout 仅 JSON，日志在 stderr）。"""
    cmd = [
        sys.executable, str(REPO_ROOT / "scripts" / "produce_chapters.py"),
        "--project", project_id or server["project_id"],
        "--chapters", chapters,
        "--apply", "--json",
        "--base-url", server["base_url"],
        "--target-word-count", "300",
        "--mock-providers", str(mocks_path),
        "--poll-interval", "0.3",
        "--stage-timeout", str(STAGE_TIMEOUT_S),
        "--start-timeout", "60",
        *(extra_args or []),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=900)
    summary: dict = {}
    if proc.stdout.strip():
        try:
            summary = json.loads(proc.stdout)
        except ValueError:
            summary = {"_unparsed_stdout": proc.stdout[:500]}
    return proc.returncode, summary, proc.stderr


def _chapter_status(server: dict, chapter_id: str) -> str:
    resp = server["client"].get(f"/api/chapters/{chapter_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


def test_driver_produces_clean_chapter_to_committed(live_server):
    """场景 1：干净草稿 → 批准 → commit → COMMITTED，exit 0。"""
    chapter_id = _add_chapter(live_server, 1)
    mocks = _write_mocks(live_server["tmp_dir"] / "mocks_ch1.json",
                         chapter_id, live_server["character_id"], CLEAN_PROSE)

    code, summary, stderr = _run_driver(live_server, mocks, "1")

    assert code == 0, f"exit={code}\nstdout={summary}\nstderr={stderr[-3000:]}"
    assert summary["counts"] == {
        "selected": 1, "produced": 1, "skipped": 0, "failed": 0, "not_attempted": 0,
    }, summary
    record = summary["chapters"][0]
    assert record["ok"] is True, record
    assert record["final_status"] == "COMMITTED", record
    assert record["stages_run"] == ["plan", "write", "review", "commit"], record
    assert record["word_count"] == 288, record
    assert record["target_word_count"] == 300, record
    assert record["review_error_count"] == 0, record
    assert record["repetition_ratio"] == pytest.approx(0.0, abs=1e-6), record
    assert record["revise_rounds"] == 0 and record["failure"] is None, record
    assert _chapter_status(live_server, chapter_id) == "COMMITTED"


def test_driver_refuses_to_approve_duplicated_chapter(live_server):
    """场景 2：报告无 error，但草稿自身重复 ⇒ 拒绝批准、整轮停止（exit 2）。"""
    chapter_id = _add_chapter(live_server, 2)
    mocks = _write_mocks(live_server["tmp_dir"] / "mocks_ch2.json",
                         chapter_id, live_server["character_id"], DUPLICATED_PROSE)

    code, summary, stderr = _run_driver(live_server, mocks, "2")

    assert code == 2, f"exit={code}\nstdout={summary}\nstderr={stderr[-3000:]}"
    record = summary["chapters"][0]
    assert record["ok"] is False, record
    failure = record["failure"]
    assert failure["reason"] == "repetition_exceeds_threshold", failure
    assert failure["stage"] == "review", failure
    assert failure["repetition_ratio"] > 0.08, failure
    assert "拒绝批准" in failure["detail"], failure
    assert record["review_error_count"] == 0, record  # 报告本身没有 error
    assert summary["counts"]["failed"] == 1, summary
    # 章**没有**被推到 COMMITTED（停在 DRAFTED 等人工处置）
    assert _chapter_status(live_server, chapter_id) == "DRAFTED"


def test_driver_detects_auto_revise_child_run_and_bounds_rounds(live_server):
    """场景 3：可定向改稿的 error ⇒ 驳回改稿 → **轮询发现改稿子 run** → 轮次耗尽即停。

    窄带项目（默认字数带下限 1200）里 288 字草稿必然出 ``W-LEN-DEVIATION``（可定向改稿），
    驳回后服务端 auto_revise 回路自己跑 write → review 并再次 PAUSE——**子 run 不在 resume
    响应里**，驱动只能靠 `GET /projects/{pid}/runs` 认出更新的 PAUSED review run。
    `--max-revise-rounds 1` 限死轮次：第 2 次判为 revise 时失败收尾（exit 2），
    不留「无限改稿」的口子。
    """
    pid = live_server["narrow_project_id"]
    chapter_id = _add_chapter(live_server, 1, project_id=pid)
    mocks = _write_mocks(live_server["tmp_dir"] / "mocks_narrow.json",
                         chapter_id, live_server["narrow_character_id"], CLEAN_PROSE)

    code, summary, stderr = _run_driver(
        live_server, mocks, "1", project_id=pid, extra_args=["--max-revise-rounds", "1"]
    )

    assert code == 2, f"exit={code}\nstdout={summary}\nstderr={stderr[-4000:]}"
    record = summary["chapters"][0]
    failure = record["failure"]
    assert failure["reason"] == "revise_rounds_exhausted", failure
    assert failure["rule_ids"] == ["W-LEN-DEVIATION"], failure
    assert record["revise_rounds"] == 1, record
    assert record["review_error_rule_ids"] == ["W-LEN-DEVIATION"], record
    # 改稿子 run 是靠**轮询 run 列表**发现的（日志留痕），不是靠盯章状态
    assert "发现改稿子 run" in stderr, stderr[-2000:]
    # 服务端确实产出了第二条 chapter-review run（父 + 改稿子 run）
    resp = live_server["client"].get(f"/api/projects/{pid}/runs")
    assert resp.status_code == 200, resp.text
    reviews = [r for r in resp.json()
               if r.get("chapter_id") == chapter_id and r.get("workflow_name") == "chapter-review"]
    assert len(reviews) >= 2, reviews
