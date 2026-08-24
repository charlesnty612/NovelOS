"""End-to-end HTTP smoke for NovelOS（PRD §110 全链路的 HTTP 版本）。

目标：用临时数据库真实启动 ``python -m packages.core.api.main``，走完
project → character → model_config → chapter → 四工作流
（plan → write → review → commit）的端到端链路，断言：

1. GET /                                        → 200 + HTML（SPA 托管 apps/web/dist）
2. GET /api/health                              → 200，tables=31（31 业务表 + _migrations = 32 总表）
3. POST /api/projects                           → 201 + project_id
4. POST /api/projects/{pid}/characters          → 201 + character_id（1 个主角）
5. POST /api/projects/{pid}/world-rules         → 201 + world_rule_id（1 条世界规则；PRD §110 第 3 步「创建世界」）
6. POST /api/model-configs                      → 201（capability=reasoning 与 creative_writing 各 1 条 mock）
7. POST /api/projects/{pid}/chapters            → 201 + chapter_id（第 1 章）
8. 四条工作流端点（plan → write → review → commit）→ 201；
   review / commit 若返回 PAUSED → POST /runs/{rid}/resume {"human_input":{"approved":true}}
9. 最终断言：
   - chapters.status == "COMMITTED"
   - GET /projects/{pid}/state 快照含 observer 新增内容（hook / event / character state）
   - GET /projects/{pid}/state 快照含 world_rules（创建的世界规则进入快照）
   - GET /chapters/{cid}/drafts 至少 1 条 AI 草稿
   - GET /chapters/{cid}/quality 200 + report（report 模式）

执行：``python scripts/smoke_e2e.py``
- 默认端口 18081；若被占，自动回退 18099（NOVELOS_PORT 透传子进程）。
- 默认数据库 ./data/smoke_e2e_<pid>_<ts>.db；结束后删除（-wal/-shm 一并清理）。
- 失败抛 SystemExit(1)，成功打印 PASS 并 exit 0。

可重复：每次以 pid+时间戳后缀的临时 db + 子进程 uvicorn 隔离，幂等。

实现说明（关键契约，与 packages/core 对齐）：
- mock_providers 透传：``{agent_name: [json_str, ...]}``（engine 的 ctx["mock_providers"]，
  节点 fn 取出后交给 run_agent 的 mock_script → MockProvider.scripted，耗尽重复末条）。
- director 契约：schema_version == "director-plan.v1"（runner 最小校验）。
- writer 契约：schema_version == "writer-output.v1" + prose + self_report。
- observer 契约：顶层恰为 7 个 change 数组；条目受 docs/state-model/schemas/state-delta.schema.json
  约束（evidence 必含 chapter_id/excerpt，op=add 必含 after，change_id 全局唯一；
  risk_level=HIGH 会触发 high_risk_approval Human 暂停 → 全用 LOW 避免多余审批）。
- review 的 author_review / commit 的 high_risk_approval 均为 Human 节点 → PAUSED 后 resume。
- health.tables = 业务表数（31）；总表（含 _migrations）为 32。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18081
FALLBACK_PORT = 18099
SERVER_START_TIMEOUT_S = 30
ENDPOINT_READY_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 60.0

# health.tables = 业务表数（31 业务表；含 _migrations 总表 32，health 返回减 1 后的业务表数）
EXPECTED_BUSINESS_TABLES = 31


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    """阻塞等待 host:port 可连；超时抛 RuntimeError。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.2)
    raise RuntimeError(f"server not reachable at {host}:{port} within {timeout}s")


def _wait_for_http(url: str, timeout: float) -> None:
    """阻塞等待 url GET 200；超时抛 RuntimeError。"""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.3)
    raise RuntimeError(f"GET {url} not ready within {timeout}s: {last_err}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _kill_proc(proc: subprocess.Popen[bytes]) -> None:
    """终止 uvicorn 子进程。

    注意：Windows 下 ``CTRL_BREAK_EVENT`` 发给共享控制台会连带杀掉父进程
    （0xC000013A），因此这里只做温和的 ``terminate()`` → ``kill()`` 兜底。
    """
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


def _port_busy(port: int, host: str = DEFAULT_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Mock payloads（observer 需产出合法 delta：evidence.chapter_id 与 chapter 一致、
# op=add 必含 after、change_id 全局唯一、risk_level 全 LOW）
# ---------------------------------------------------------------------------


def _build_mock_payloads(chapter_id: str, character_id: str) -> dict[str, list[dict]]:
    """构造最小可执行的 mock 脚本（director / writer / observer）。"""
    director_payload = {
        "schema_version": "director-plan.v1",
        "prompt_version": "director:v1",
        "chapter_id": chapter_id,
        "chapter_goal": "识海古镜觉醒",
        "core_conflict": "林轩 vs 自身血脉枷锁",
        "turning_point": "林轩握碎古镜外层封印的瞬间",
        "expected_role": "setup",
        "key_beats": [
            {
                "beat_id": "beat_001",
                "purpose": "夜入禁地",
                "involved_characters": [character_id],
                "involved_locations": [],
                "involved_hooks": [],
                "involved_debts": [],
                "risk_level": "LOW",
            },
            {
                "beat_id": "beat_002",
                "purpose": "古镜觉醒",
                "involved_characters": [character_id],
                "involved_locations": [],
                "involved_hooks": [],
                "involved_debts": [],
                "risk_level": "LOW",
            },
        ],
        "character_changes_planned": [],
        "information_releases": [],
        "hook_handling": [],
        "debt_handling": [],
        "proposed_new_entities": [],
        "deviations": [],
        "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        "open_questions": [],
        "notes_for_planner": "smoke_e2e mock",
    }

    # prose 字数对齐 target_word_count=300（±15% 阈值；字数过低只记 warning 不阻断）
    writer_payload = {
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v1",
        "chapter_id": chapter_id,
        "prose": (
            "夜雨落入禁地。林轩伸手握住古镜，识海剧痛自血脉深处涌来。"
            "镜面浮现第一重残篇，他咬牙将体内灵气压入掌心。"
            "古镜外层封印炸裂，青光直冲云霄，禁地异象惊动巡夜弟子。"
            "林轩收摄心神，把残篇内容逐一记下，血脉中似有古音低鸣。"
            "他望向山下宗门灯火，握紧双拳：这具肉身，终于有了答案。"
            "雨势渐歇，林轩的身影消失在禁地深处。"
        ),
        "self_report": {
            "slots_filled": ["slot_001", "slot_002"],
            "word_count": 95,
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "smoke mock",
        },
    }

    observer_payload = {
        "character_changes": [
            {
                "change_id": "cc_smoke_001",
                "op": "add",
                "target_id": character_id,
                "character_id": character_id,
                "facet": "state",
                "field": "goal",
                "before": None,
                "after": "觉醒血脉传承",
                "confidence": 0.9,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_001",
                    "excerpt": "识海剧痛自血脉深处涌来",
                    "span": {"start": 10, "end": 30},
                },
                "risk_level": "LOW",
                "notes": "smoke observer",
                "visibility": "VISIBLE",
                "who_knows": [character_id],
                "reason": None,
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                "change_id": "ev_smoke_001",
                "op": "add",
                "target_id": "evt_smoke_ancient_mirror",
                "event_id": "evt_smoke_ancient_mirror",
                "type": "revelation",
                "cause": [],
                "effects": [],
                "participants": [character_id],
                "location": None,
                "time": {"timeline_day": 1, "in_story_date": "smoke_day_one"},
                "status": "recorded",
                "description": "smoke: 古镜外层封印炸裂",
                "confidence": 0.9,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_001",
                    "excerpt": "古镜外层封印炸裂，青光直冲云霄",
                    "span": {"start": 60, "end": 80},
                },
                "risk_level": "LOW",
                "notes": "smoke observer",
                "visibility": "VISIBLE",
                "who_knows": [character_id],
            }
        ],
        "resolved_hooks": [],
        "new_hooks": [
            {
                "change_id": "nh_smoke_001",
                "op": "add",
                "target_id": "hook_smoke_ancient_mirror",
                "hook_id": "hook_smoke_ancient_mirror",
                "name": "识海古镜传承",
                "importance": 0.8,
                "expected_payoff_chapter_id": chapter_id,
                "description": "smoke: 林轩握碎外层封印，后续代价未明",
                "confidence": 0.85,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_001",
                    "excerpt": "镜面浮现第一重残篇",
                    "span": {"start": 30, "end": 50},
                },
                "risk_level": "LOW",
                "notes": "smoke observer",
                "visibility": "RESTRICTED",
                "who_knows": [character_id],
            }
        ],
        "debt_changes": [],
    }
    return {
        "director": [director_payload],
        "writer": [writer_payload],
        "observer": [observer_payload],
    }


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def run_smoke() -> int:
    # 1. 选端口（默认 18081，被占则用 18099）。
    port = FALLBACK_PORT if _port_busy(DEFAULT_PORT) else DEFAULT_PORT

    # 2. 临时数据库（pid+时间戳后缀保证幂等）。
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}_{int(time.time())}"
    tmp_db = data_dir / f"smoke_e2e_{tag}.db"
    for ext in ("", "-wal", "-shm"):
        p = Path(str(tmp_db) + ext)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    # 3. 环境变量：临时 db + quality_gate=report（避免 enforce 阻断）。
    env = os.environ.copy()
    env["NOVELOS_DB_PATH"] = str(tmp_db)
    env["NOVELOS_PORT"] = str(port)
    env["NOVELOS_QUALITY_GATE"] = "report"
    env["PYTHONPATH"] = str(REPO_ROOT)

    log_path = data_dir / f"smoke_e2e_{tag}.log"
    log_file = open(log_path, "w", encoding="utf-8")

    # 4. 后台启动服务。
    cmd = [sys.executable, "-m", "packages.core.api.main"]
    print(f"[smoke] starting server: {' '.join(cmd)} (port={port}, db={tmp_db})")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://{DEFAULT_HOST}:{port}"
    api_base = f"{base_url}/api"

    failures: list[str] = []
    try:
        # 等待端口 + health 可读
        _wait_for_port(DEFAULT_HOST, port, timeout=SERVER_START_TIMEOUT_S)
        _wait_for_http(f"{api_base}/health", timeout=ENDPOINT_READY_TIMEOUT_S)
        print(f"[smoke] server ready at {base_url}")

        client = httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_S)

        # ---- (1) GET / SPA 托管（apps/web/dist 存在时返回 index.html）
        try:
            r = client.get("/")
            ct = r.headers.get("content-type", "")
            _assert(r.status_code == 200, f"GET / status={r.status_code}")
            _assert("text/html" in ct, f"GET / content-type={ct!r} expected text/html")
            _assert("<html" in r.text.lower() or "<!doctype" in r.text.lower(), "GET / not HTML")
            print(f"[smoke] OK GET /  (status=200, content-type={ct})")
        except AssertionError as e:
            failures.append(f"GET /: {e}")

        # ---- (2) GET /api/health → 200，tables=31（31 业务表 + _migrations = 32 总表）
        try:
            r = client.get("/api/health")
            _assert(r.status_code == 200, f"/api/health status={r.status_code}")
            body = r.json()
            _assert(body.get("status") == "ok", f"/api/health status field={body.get('status')!r}")
            _assert(
                body.get("tables") == EXPECTED_BUSINESS_TABLES,
                f"/api/health tables={body.get('tables')} != {EXPECTED_BUSINESS_TABLES}",
            )
            print(f"[smoke] OK GET /api/health  (tables={body['tables']})")
        except AssertionError as e:
            failures.append(f"/api/health: {e}")

        # ---- (3) POST /api/projects
        project_id = None
        try:
            r = client.post(
                "/api/projects",
                json={"name": "smoke_e2e", "premise": "smoke", "genre": "xianxia", "target_words": 100000},
            )
            _assert(r.status_code == 201, f"create project status={r.status_code} body={r.text[:200]}")
            project_id = r.json()["project_id"]
            _assert(project_id.startswith("prj_"), f"project_id format: {project_id!r}")
            print(f"[smoke] OK POST /api/projects  (project_id={project_id})")
        except AssertionError as e:
            failures.append(f"create project: {e}")

        # ---- (4) POST /api/projects/{pid}/characters（1 个主角）
        character_id = None
        if project_id:
            try:
                r = client.post(
                    f"/api/projects/{project_id}/characters",
                    json={"name": "林轩", "role": "protagonist", "core_json": {"background": "青云宗弟子"}},
                )
                _assert(r.status_code == 201, f"create character status={r.status_code} body={r.text[:200]}")
                character_id = r.json()["character_id"]
                print(f"[smoke] OK POST .../characters  (character_id={character_id})")
            except AssertionError as e:
                failures.append(f"create character: {e}")

        # ---- (5) POST /api/projects/{pid}/world-rules（PRD §110 第 3 步「创建世界」）
        world_rule_id = None
        world_rule_name = "smoke_jingjie_tixi"
        if project_id:
            try:
                r = client.post(
                    f"/api/projects/{project_id}/world-rules",
                    json={
                        "name": world_rule_name,
                        "statement": "淬体、开脉、灵海、神藏，每境分九重；主角只能越级而战。",
                    },
                )
                _assert(
                    r.status_code == 201,
                    f"create world-rule status={r.status_code} body={r.text[:200]}",
                )
                world_rule_id = r.json()["id"]
                _assert(
                    r.json().get("name") == world_rule_name,
                    f"world_rule.name={r.json().get('name')!r} != {world_rule_name!r}",
                )
                print(f"[smoke] OK POST .../world-rules  (world_rule_id={world_rule_id}, name={world_rule_name})")
            except AssertionError as e:
                failures.append(f"create world-rule: {e}")

        # ---- (6) POST /api/model-configs（capability=reasoning 与 creative_writing 各 1 条 mock）
        try:
            cfg_ids: dict[str, str] = {}
            for capability in ("reasoning", "creative_writing"):
                r = client.post(
                    "/api/model-configs",
                    json={
                        "capability": capability,
                        "provider": "mock",
                        "model": "mock-model",
                        "params_json": {},
                        "enabled": 1,
                    },
                )
                _assert(
                    r.status_code == 201,
                    f"create model_config({capability}) status={r.status_code} body={r.text[:200]}",
                )
                cfg_ids[capability] = r.json()["config_id"]
            print(
                f"[smoke] OK POST /api/model-configs  "
                f"(reasoning={cfg_ids['reasoning']}, creative_writing={cfg_ids['creative_writing']})"
            )
        except AssertionError as e:
            failures.append(f"create model_configs: {e}")

        # ---- (7) POST /api/projects/{pid}/chapters（第 1 章）
        chapter_id = None
        if project_id:
            try:
                r = client.post(
                    f"/api/projects/{project_id}/chapters",
                    json={"number": 1, "title": "smoke chapter 1"},
                )
                _assert(r.status_code == 201, f"create chapter status={r.status_code} body={r.text[:200]}")
                chapter_id = r.json()["chapter_id"]
                _assert(r.json().get("status") == "PLANNED", f"chapter.status={r.json().get('status')!r}")
                print(f"[smoke] OK POST .../chapters  (chapter_id={chapter_id})")
            except AssertionError as e:
                failures.append(f"create chapter: {e}")

        # 同步 prompts（run_agent 需要 ACTIVE prompt；workflow 内 AI 节点会查 prompts 表）
        try:
            r = client.post("/api/agents/sync")
            _assert(r.status_code == 200, f"agents/sync status={r.status_code}")
            print("[smoke] OK POST /api/agents/sync")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"agents/sync: {exc}")

        # ---- (8) 四个工作流端点（plan → write → review → commit）
        mocks: dict[str, list[str]] = {}
        if chapter_id and character_id:
            payloads = _build_mock_payloads(chapter_id, character_id)
            mocks = {agent: [json.dumps(p, ensure_ascii=False) for p in ps] for agent, ps in payloads.items()}

        def _start(workflow: str) -> dict:
            _assert(chapter_id and project_id, "chapter_id/project_id missing")
            r = client.post(
                f"/api/projects/{project_id}/chapters/{chapter_id}/{workflow}",
                json={
                    "mock_providers": mocks,
                    "quality_gate_mode": "report",
                    "target_word_count": 300,
                    "author_intent": "识海古镜觉醒",
                },
            )
            _assert(r.status_code == 201, f"start {workflow} status={r.status_code} body={r.text[:300]}")
            return r.json()

        def _resume_if_paused(payload: dict, workflow: str) -> None:
            run_id = payload.get("run_id")
            status = payload.get("status")
            if status != "PAUSED" or not run_id:
                return
            print(f"[smoke] ... {workflow} paused at human node (run_id={run_id}) → resume approve")
            # 防御：最多 resume 5 次（连续 Human 节点场景）
            for _ in range(5):
                r = client.post(f"/api/runs/{run_id}/resume", json={"human_input": {"approved": True}})
                _assert(r.status_code == 200, f"resume {run_id} status={r.status_code} body={r.text[:200]}")
                body = r.json()
                if body.get("status") != "PAUSED":
                    return
                run_id = body.get("run_id") or run_id
            raise AssertionError(f"run {run_id} stayed PAUSED after 5 resumes")

        workflow_ids: dict[str, str] = {}
        for wf in ("plan", "write", "review", "commit"):
            if not chapter_id:
                break
            try:
                body = _start(wf)
                _resume_if_paused(body, wf)
                workflow_ids[wf] = body["run_id"]
                print(f"[smoke] OK POST .../{wf}  (run_id={body['run_id']})")
            except AssertionError as e:
                failures.append(f"workflow {wf}: {e}")

        # ---- (9) 断言最终态
        if chapter_id:
            try:
                r = client.get(f"/api/chapters/{chapter_id}")
                _assert(r.status_code == 200, f"GET chapter status={r.status_code}")
                _assert(r.json().get("status") == "COMMITTED", f"chapter.status={r.json().get('status')!r} != COMMITTED")
                print("[smoke] OK chapter.status=COMMITTED")
            except AssertionError as e:
                failures.append(f"chapter final status: {e}")

            try:
                r = client.get(f"/api/projects/{project_id}/state")
                _assert(r.status_code == 200, f"GET state status={r.status_code}")
                snap = r.json()
                snap_text = json.dumps(snap, ensure_ascii=False)
                _assert("hook_smoke_ancient_mirror" in snap_text, "state snapshot missing observer hook")
                _assert("evt_smoke_ancient_mirror" in snap_text, "state snapshot missing observer event")
                # character_changes 的 op=add + facet=state → current_state.goal 写入快照
                _assert("觉醒血脉传承" in snap_text, "state snapshot missing observer character change (goal)")
                print("[smoke] OK /projects/{pid}/state contains observer content (hook/event/character)")
            except AssertionError as e:
                failures.append(f"state snapshot: {e}")

            try:
                r = client.get(f"/api/projects/{project_id}/state")
                _assert(r.status_code == 200, f"GET state status={r.status_code} (world_rules)")
                snap = r.json()
                world = snap.get("world") or {}
                world_rules = world.get("world_rules") or []
                _assert(
                    isinstance(world_rules, list) and len(world_rules) >= 1,
                    f"state.world.world_rules={world_rules!r} (expected list with >=1 entry)",
                )
                names = [r.get("name") for r in world_rules if isinstance(r, dict)]
                _assert(
                    world_rule_name in names,
                    f"world_rule {world_rule_name!r} not in state.world.world_rules names={names!r}",
                )
                print(
                    f"[smoke] OK /projects/{project_id}/state.world.world_rules contains {world_rule_name!r}  (n={len(world_rules)})"
                )
            except AssertionError as e:
                failures.append(f"state snapshot world_rules: {e}")

            try:
                r = client.get(f"/api/chapters/{chapter_id}/drafts")
                _assert(r.status_code == 200, f"GET drafts status={r.status_code}")
                drafts = r.json()
                _assert(len(drafts) >= 1, f"drafts len={len(drafts)} (expected >=1)")
                prose = drafts[0].get("content") or ""
                _assert(len(prose) > 0, "draft[0].content is empty")
                print(f"[smoke] OK /chapters/{chapter_id}/drafts  (n={len(drafts)}, prose chars={len(prose)})")
            except AssertionError as e:
                failures.append(f"drafts: {e}")

            try:
                r = client.get(f"/api/chapters/{chapter_id}/quality")
                _assert(r.status_code == 200, f"GET quality status={r.status_code}")
                report = r.json()
                _assert(
                    "overall" in report or "issues" in report,
                    f"quality report shape unexpected: keys={list(report.keys())[:5]}",
                )
                print(f"[smoke] OK /chapters/{chapter_id}/quality  (overall={report.get('overall')})")
            except AssertionError as e:
                failures.append(f"quality: {e}")

        client.close()

        if failures:
            print("\n[smoke] FAIL")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("\n[smoke] PASS  (PRD §110 全链路 9 组断言全绿)")
        return 0

    finally:
        # 清理
        _kill_proc(proc)
        log_file.close()
        # 删临时 db（保留日志便于排错）
        for ext in ("", "-wal", "-shm"):
            p = Path(str(tmp_db) + ext)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        if log_path.exists():
            print(f"[smoke] server log: {log_path}")


if __name__ == "__main__":
    raise SystemExit(run_smoke())
