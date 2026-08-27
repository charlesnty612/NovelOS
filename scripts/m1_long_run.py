"""M1 长跑驱动：连续生产 N 章小说（HTTP 驱动已运行的 NovelOS 后端）。

用途
----
本脚本**不**自起服务、不**不**注入 MINIMAX_API_KEY；它假定外部已经把
``python -m packages.core.api.main`` 拉起来，并通过 ``--host`` / ``--port`` /
``--db`` 与服务端对齐。脚本按章节顺序驱动四工作流（plan → write → review →
commit），自动处理 review / commit 的 Human 节点 PAUSED（resume body 为
``{"human_input":{"approved":true}}``，与 ``packages.core.api.routers.workflows``
中 :class:`ResumeRequest` 对齐）。每章完成后立即采集 token / quality / state
指标，支持断点续跑与熔断（单章失败上限、token 预算、连续 HTTP 失败上限）。

用法示例
--------
**真实长跑**（先在另一个 shell 起好服务，再用本脚本驱动；key 由服务端环境变量注入）::

    # 终端 A：起服务（key 仅存在于服务进程 env，不写入 DB / 文件）
    MINIMAX_API_KEY=sk-xxx \\
    NOVELOS_PORT=18091 \\
    NOVELOS_DATA_DIR=data/m1_run \\
    python -m packages.core.api.main

    # 终端 B：建项目 + 50 章骨架
    python scripts/m1_long_run.py init --port 18091 --total-chapters 50

    # 跑前 5 章（预算 200k token，单章失败容忍 3 次）
    python scripts/m1_long_run.py run --port 18091 --from 1 --to 5 \\
        --budget-total-tokens 200000 --max-failures 3

    # 中断后再次同命令会自动跳过已完成章（断点续跑）
    python scripts/m1_long_run.py run --port 18091 --from 1 --to 50 \\
        --budget-total-tokens 200000

    # 看进度
    python scripts/m1_long_run.py status --port 18091

**mock 自测**（零成本验证脚本自身）::

    python scripts/m1_long_run.py --dry-run init --port 18091 --total-chapters 2
    python scripts/m1_long_run.py --dry-run run --port 18091 --from 1 --to 2
    python scripts/m1_long_run.py --dry-run status --port 18091

熔断语义
--------
1. 单章内 plan/write/review/commit 任一 FAILED → 按 chapters.status 重新计算
   续跑起点，仅重跑未完成的工作流（断点续跑语义）；同一工作流环节的尝试上限为
   ``--max-failures`` 次（默认 3）。任一工作流耗尽重试次数 → 整体停止、退出码非 0。
2. 累计 total_tokens 超过 ``--budget-total-tokens`` → 优雅停止（当前章走完或
   立即停止，看预算点位置）；已完成的章不被回滚，打印「如何续跑」提示。
3. HTTP 连接错误（``httpx.ConnectError`` / ``RemoteProtocolError``）连续 5 次
   → 整体停止、退出码非 0。

断点续跑语义
------------
- 进度文件 ``<data-dir>/progress.json`` 记录 ``completed[chapter_no]``；再次
  执行 ``run`` 时，已 complete 的章号直接跳过、不重发工作流。
- 未在 ``completed`` 的章节，run 阶段会先 GET /api/chapters/{ch_id} 取当前
  status，按映射推导应跑的工作流：
  - PLANNED → plan / write / review / commit
  - DRAFTED → write / review / commit（覆盖旧稿）
  - REVIEWED → 仅 commit（正文与评审结果保留）
  - COMMITTED / RELEASED → 视为已完成，跳过所有工作流，直接采集指标并标记 completed
- 单章失败时，按 chapters.status 重新计算起点后再重试；同一工作流环节的尝试
  上限为 ``--max-failures`` 次。
- 每章的 run_id / metrics 增量写入 ``<data-dir>/chapters/ch{NNN}.json``，
  状态变化时同步更新 ``progress.json`` 的 ``updated_at``。
- 单章失败会**清掉**该章号在 ``completed`` 中的旧记录再重试，避免「失败的章
  被记成 completed」。

依赖
----
仅标准库 + ``httpx``（项目已声明依赖）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# V3.7：复用 packages.core.quality.wordcount 的字数口径，避免脚本侧漂移
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from packages.core.quality.wordcount import (  # noqa: E402
    classify_prose_length,
    visible_chars,
)

# 路径约定：脚本与项目根平级
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18081
POLL_INTERVAL_S = 3.0
WORKFLOW_TIMEOUT_S = 900.0  # 单工作流超时（含 mock 长任务）
RESUME_RETRY = 20  # review / commit PAUSED 后 resume 轮询最大次数
HTTP_FAIL_THRESHOLD = 5  # 连续 HTTP 失败上限
TARGET_WORD_COUNT = 1800  # chapter-write 的目标字数（番茄单章最佳区间 1500-2200 的中位）
LOCK_FILE_NAME = ".run.lock"  # <data-dir>/.run.lock：run 子命令的单实例锁
RUN_LOCK_HELD_EXIT = 5  # 检测到另一实例仍在运行时的退出码


# ============================================================================
# 时间辅助
# ============================================================================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# 单实例文件锁
# ============================================================================
#
# 背景：M1 曾出现两个 uvicorn 进程并存 + 驱动脚本残留，导致 build_observer_ctx
# 节点被 SQLite 锁挂死 8h（py-spy 定位）。下方 ``_acquire_lock`` /
# ``_release_lock`` 在 ``cmd_run`` 入口/出口处对 ``<data-dir>/.run.lock`` 做
# 单实例防护：检测到 PID 仍存活则拒绝并退出码 5；--force-lock 强制接管用于
# 清理残留锁。


def _lock_path(data_dir: Path) -> Path:
    """返回 ``<data-dir>/.run.lock`` 路径；data_dir 不会被自动创建。"""
    return data_dir / LOCK_FILE_NAME


def _pid_alive(pid: int) -> bool:
    """跨平台判断 PID 是否仍在运行（``os.kill(pid, 0)``，不真杀进程）。

    - ``ProcessLookupError``（不存在）→ False。
    - ``PermissionError``（存在但权限不够）→ True（保守认定为活）。
    - 其他 ``OSError``（如 PID<=0）→ False，避免误锁。
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_meta(path: Path) -> dict[str, Any] | None:
    """读取锁文件 JSON；缺文件 / 解析失败 / 字段缺失 PID → 返回 None。"""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    return data


def _acquire_lock(data_dir: Path, force: bool) -> tuple[bool, dict[str, Any] | None]:
    """尝试获取 ``<data-dir>/.run.lock`` 单实例锁。

    返回 ``(acquired, conflicting_meta)``：
    - ``(True, None)`` 拿到锁（写入自己 PID）；或 force/接管了现存锁。
    - ``(False, meta)`` 当前已有活实例占用，``meta`` 是占锁方写入的 JSON。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(data_dir)
    my_meta: dict[str, Any] = {
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "host": socket.gethostname(),
    }

    existing = _read_lock_meta(lock_path)
    if existing is not None:
        existing_pid = existing.get("pid")
        alive = _pid_alive(existing_pid) if isinstance(existing_pid, int) else False
        if alive and not force:
            return False, existing
        # 已死 / 强制接管
        my_meta["force_took_over"] = bool(force) or not alive
        if existing.get("pid"):
            my_meta["previous_pid"] = existing["pid"]

    tmp = lock_path.with_suffix(lock_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(my_meta, f, ensure_ascii=False, indent=2)
    tmp.replace(lock_path)
    return True, None


def _release_lock(data_dir: Path) -> None:
    """删除锁文件；不存在时静默。"""
    try:
        _lock_path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass


# ============================================================================
# HTTP 客户端 + 通用响应校验
# ============================================================================


def _check(resp: httpx.Response, label: str) -> dict:
    """校验 HTTP 状态；>=400 直接抛 RuntimeError。"""
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


# ============================================================================
# 进度文件 IO
# ============================================================================


def _progress_path(data_dir: Path) -> Path:
    return data_dir / "progress.json"


def _chapters_dir(data_dir: Path) -> Path:
    return data_dir / "chapters"


def _load_progress(data_dir: Path) -> dict[str, Any]:
    p = _progress_path(data_dir)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # 损坏文件不阻断；重写
        return {}


def _save_progress(data_dir: Path, prog: dict[str, Any]) -> None:
    prog["updated_at"] = _now_iso()
    p = _progress_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def _save_chapter_detail(data_dir: Path, chapter_no: int, detail: dict[str, Any]) -> None:
    d = _chapters_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"ch{chapter_no:03d}.json"
    detail["updated_at"] = _now_iso()
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


# ============================================================================
# 工作流驱动
# ============================================================================


# 终态集合：轮询到此集合即停
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _start_workflow(client: httpx.Client, project_id: str, chapter_id: str, wf: str, body: dict[str, Any]) -> str:
    """POST 启动工作流，返回 run_id。失败抛 RuntimeError。"""
    resp = client.post(f"/api/projects/{project_id}/chapters/{chapter_id}/{wf}", json=body)
    return _check(resp, f"start-{wf}")["run_id"]


def _get_run(client: httpx.Client, run_id: str) -> dict[str, Any]:
    return _check(client.get(f"/api/runs/{run_id}"), f"get-run-{run_id}")


def _get_chapter_status(client: httpx.Client, chapter_id: str) -> str:
    """GET /api/chapters/{chapter_id}，返回 chapters.status 字符串。

    若 HTTP 失败或响应缺 status，回退为 'PLANNED'（最保守的全流程起点）。
    """
    try:
        resp = client.get(f"/api/chapters/{chapter_id}")
    except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
        return "PLANNED"
    if resp.status_code >= 400:
        return "PLANNED"
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return "PLANNED"
    status = (data or {}).get("status") or "PLANNED"
    return str(status)


# 四工作流定义顺序（与状态机 PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED 对齐）。
_WORKFLOW_CHAIN: tuple[str, ...] = ("plan", "write", "review", "commit")


class _WorkflowFailure(RuntimeError):
    """单工作流环节 FAILED 抛出的标记异常；外层用同章重试循环捕获并判定是否耗尽。"""

    pass


def _workflows_for_status(status: str) -> list[str]:
    """按 chapters.status 推导「还需要跑的」工作流列表。

    - PLANNED → plan / write / review / commit
    - DRAFTED → write / review / commit（覆盖旧稿；plan 已落 chapters.plan_json）
    - REVIEWED → 仅 commit（正文与评审结果保留）
    - COMMITTED / RELEASED → []（视为已完成，跳过工作流直接采集指标）
    - 未知状态 → 视为 PLANNED 全流程（保守起步）。
    """
    s = (status or "").upper()
    if s == "PLANNED":
        return list(_WORKFLOW_CHAIN)
    if s == "DRAFTED":
        return ["write", "review", "commit"]
    if s == "REVIEWED":
        return ["commit"]
    if s in ("COMMITTED", "RELEASED"):
        return []
    # 未知 / 空值 → 全流程
    return list(_WORKFLOW_CHAIN)


def _poll_run(
    client: httpx.Client,
    run_id: str,
    *,
    timeout_s: float = WORKFLOW_TIMEOUT_S,
    interval_s: float = POLL_INTERVAL_S,
) -> dict[str, Any]:
    """轮询 run 直到终态；PAUSED 自动 resume 后继续轮询。返回最终 run dict。"""
    deadline = time.monotonic() + timeout_s
    last_status: str | None = None
    pause_resume_count = 0
    while True:
        run = _get_run(client, run_id)
        status = run.get("status")
        if status != last_status:
            print(f"    [poll] run {run_id[:16]}… status={status}", flush=True)
            last_status = status
        if status == "PAUSED":
            if pause_resume_count >= RESUME_RETRY:
                raise RuntimeError(f"run {run_id} PAUSED 超过 {RESUME_RETRY} 次 resume 仍未结束")
            pause_resume_count += 1
            print(f"    [poll] PAUSED → resume（{pause_resume_count}/{RESUME_RETRY}）", flush=True)
            # 与 packages.core.api.routers.workflows.ResumeRequest 对齐：human_input 字段
            # review / commit 的 author_review / high_risk_approval 接受 {"approved": true}
            _check(client.post(f"/api/runs/{run_id}/resume", json={"human_input": {"approved": True}}), "resume")
            # 续期一下 deadline，避免 resume 后本轮就退出
            deadline = time.monotonic() + timeout_s
            continue
        if status in TERMINAL:
            return run
        if time.monotonic() > deadline:
            raise RuntimeError(f"run {run_id} 轮询超时（{timeout_s}s），last_status={status}")
        time.sleep(interval_s)


def _run_one_workflow(
    client: httpx.Client,
    project_id: str,
    chapter_id: str,
    wf: str,
    payload: dict[str, Any],
) -> tuple[str, str, float]:
    """驱动单个工作流（start → poll）。返回 (run_id, final_status, wall_s)。"""
    t0 = time.monotonic()
    run_id = _start_workflow(client, project_id, chapter_id, wf, payload)
    final = _poll_run(client, run_id)
    return run_id, final.get("status", "UNKNOWN"), time.monotonic() - t0


# ============================================================================
# 指标采集
# ============================================================================


def _aggregate_ai_logs(db_path: str, run_ids: list[str]) -> dict[str, Any]:
    """从 ai_call_logs 聚合 token / 调用次数 / 最大延迟。"""
    if not run_ids:
        return {
            "call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "retry_sum": 0,
            "max_latency_ms": 0,
        }
    placeholders = ",".join("?" * len(run_ids))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT token_usage_json, latency_ms, retry_count FROM ai_call_logs WHERE run_id IN ({placeholders})",
            run_ids,
        ).fetchall()
    finally:
        conn.close()
    call_count = len(rows)
    prompt_total = 0
    completion_total = 0
    total_total = 0
    retry_sum = 0
    max_latency = 0
    for row in rows:
        raw = row["token_usage_json"]
        if raw:
            try:
                d = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                d = {}
        else:
            d = {}
        prompt_total += int(d.get("prompt") or 0)
        completion_total += int(d.get("completion") or 0)
        total_total += int(d.get("total") or 0)
        retry_sum += int(row["retry_count"] or 0)
        lat = int(row["latency_ms"] or 0)
        if lat > max_latency:
            max_latency = lat
    return {
        "call_count": call_count,
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "total_tokens": total_total,
        "retry_sum": retry_sum,
        "max_latency_ms": max_latency,
    }


def _latest_quality(db_path: str, chapter_id: str) -> dict[str, Any]:
    """取最新 quality_reports 行（按 created_at DESC LIMIT 1）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT report_id, overall, scores_json, issues_json, created_at
            FROM quality_reports
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    scores_raw = row["scores_json"]
    issues_raw = row["issues_json"]
    try:
        scores = json.loads(scores_raw) if isinstance(scores_raw, str) else (scores_raw or {})
    except (TypeError, json.JSONDecodeError):
        scores = {}
    try:
        issues = json.loads(issues_raw) if isinstance(issues_raw, str) else (issues_raw or [])
    except (TypeError, json.JSONDecodeError):
        issues = []
    if not isinstance(scores, dict):
        scores = {}
    if not isinstance(issues, list):
        issues = []
    seven_keys = ("plot", "character", "continuity", "style", "pacing", "foreshadowing")
    seven_subs = {k: scores.get(k) for k in seven_keys}
    return {
        "report_id": row["report_id"],
        "overall": row["overall"],
        "seven_subs": seven_subs,
        "ai_trace": scores.get("ai_trace"),
        "meta": scores.get("_meta"),
        "issue_count": len(issues),
        "issues": issues,
        "created_at": row["created_at"],
    }


def _story_state(db_path: str, project_id: str) -> dict[str, Any]:
    """取当前快照的 state_version + snapshot_json 字节数；service 暴露 GET /api/projects/{pid}/state。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT state_version, snapshot_json FROM story_states
            WHERE project_id = ?
            ORDER BY state_version DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"state_version": 0, "snapshot_bytes": 0}
    snapshot = row["snapshot_json"] or ""
    return {"state_version": int(row["state_version"]), "snapshot_bytes": len(snapshot.encode("utf-8"))}


def _latest_prose_chars(db_path: str, chapter_id: str) -> int:
    """取该章最新 draft.content，去空白后字符数。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY created_at DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 0
    # V3.7：复用 packages.core.quality.wordcount.visible_chars，与全仓正文字数口径统一
    return visible_chars(row["content"] or "")


def _latest_prose_word_stats(
    db_path: str, chapter_id: str, target_word_count: int
) -> dict[str, Any]:
    """V3.7：取该章最新 draft 的字数分类（visible_chars / target / band / status / deviation_pct）。

    无 draft 或 db_path 为空时返回零值字典。``target_word_count<=0`` 时仍走 wordcount
    classify，band_low 会被 floor 保护。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? ORDER BY created_at DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    prose = (row["content"] or "") if row is not None else ""
    return classify_prose_length(prose, target_word_count)


# ============================================================================
# Mock provider payload（dry-run 用）
# ============================================================================


def _build_dry_run_mock_payloads(chapter_id: str, character_id: str) -> dict[str, list[str]]:
    """构造可在 mock 模式下完成四工作流的最小 mock_providers。

    与 scripts/smoke_e2e._build_mock_payloads 同源：director / writer / observer
    三类输出满足各契约 validator；新增 critic 与 summarizer（chapter-review /
    chapter-commit pipeline 需要）。
    """
    director_payload = {
        "schema_version": "director-plan.v1",
        "prompt_version": "director:v1",
        "chapter_id": chapter_id,
        "chapter_goal": "dry-run: 章节骨架",
        "core_conflict": "dry-run vs mock",
        "turning_point": "dry-run 推进一拍",
        "expected_role": "setup",
        "key_beats": [
            {
                "beat_id": "beat_dry_001",
                "purpose": "dry-run 起点",
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
        "notes_for_planner": "m1_long_run dry-run",
    }
    writer_payload = {
        "schema_version": "writer-output.v1",
        "prompt_version": "writer:v1",
        "chapter_id": chapter_id,
        "prose": (
            "夜风拂过山道，dry-run 主角在崖边回望来路。"
            "脑中旧事与新念交织，他握紧手中信物，向山下走去。"
            "远处传来一声鸟鸣，他的心绪随之沉静。"
            "这一程不过数里，他却走得很慢，仿佛想把所有细节都收进记忆。"
            "dry-run 推进一拍，路灯渐次亮起，街口人来人往。"
        ),
        "self_report": {
            "slots_filled": ["slot_dry_001"],
            "word_count": 120,
            "scene_count": 1,
            "deviations": [],
            "forbidden_word_hits": [],
            "self_check_notes": "dry-run mock",
        },
    }
    observer_payload = {
        "character_changes": [
            {
                "change_id": f"cc_dry_{chapter_id[-6:]}",
                "op": "add",
                "target_id": character_id,
                "character_id": character_id,
                "facet": "state",
                "field": "goal",
                "before": None,
                "after": "dry-run: 出发前往山下",
                "confidence": 0.9,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_dry_001",
                    "excerpt": "dry-run 主角在崖边回望来路",
                    "span": {"start": 0, "end": 12},
                },
                "risk_level": "LOW",
                "notes": "dry-run observer",
                "visibility": "VISIBLE",
                "who_knows": [character_id],
                "reason": None,
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                "change_id": f"ev_dry_{chapter_id[-6:]}",
                "op": "add",
                "target_id": f"evt_dry_{chapter_id[-6:]}",
                "event_id": f"evt_dry_{chapter_id[-6:]}",
                "type": "revelation",
                "cause": [],
                "effects": [],
                "participants": [character_id],
                "location": None,
                "time": {"timeline_day": 1, "in_story_date": "dry_day_one"},
                "status": "recorded",
                "description": "dry-run: 主角启程",
                "confidence": 0.9,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_dry_001",
                    "excerpt": "向山下走去",
                    "span": {"start": 20, "end": 24},
                },
                "risk_level": "LOW",
                "notes": "dry-run observer",
                "visibility": "VISIBLE",
                "who_knows": [character_id],
            }
        ],
        "resolved_hooks": [],
        "new_hooks": [
            {
                "change_id": f"nh_dry_{chapter_id[-6:]}",
                "op": "add",
                "target_id": f"hook_dry_{chapter_id[-6:]}",
                "hook_id": f"hook_dry_{chapter_id[-6:]}",
                "name": "dry-run 信物",
                "importance": 0.6,
                "expected_payoff_chapter_id": chapter_id,
                "description": "dry-run: 信物后续待揭晓",
                "confidence": 0.8,
                "evidence": {
                    "chapter_id": chapter_id,
                    "scene_id": "scene_dry_001",
                    "excerpt": "他握紧手中信物",
                    "span": {"start": 14, "end": 20},
                },
                "risk_level": "LOW",
                "notes": "dry-run observer",
                "visibility": "RESTRICTED",
                "who_knows": [character_id],
            }
        ],
        "debt_changes": [],
    }
    critic_payload = {
        "schema_version": "critic-report.v1",
        "prompt_version": "critic:v1",
        "chapter_id": chapter_id,
        "overall_comment": "dry-run 评审：剧情推进、节拍合理，无显著问题。",
        "strengths": ["dry-run 结构清晰", "dry-run 人物动机明确"],
        "issues": [
            {
                "category": "pacing",
                "severity": "low",
                "quote": "dry-run 推进一拍",
                "suggestion": "dry-run 可补充节拍细节",
            },
        ],
    }
    summarizer_payload = {"summary": "dry-run：主角启程前往山下，dry-run 信物待揭晓。"}
    return {
        "director": [json.dumps(director_payload, ensure_ascii=False)],
        "writer": [json.dumps(writer_payload, ensure_ascii=False)],
        "critic": [json.dumps(critic_payload, ensure_ascii=False)],
        # observer：list[1] 与生产一致；commit pipeline retry 取下一条，单条够用
        "observer": [json.dumps(observer_payload, ensure_ascii=False)],
        "summarizer": [json.dumps(summarizer_payload, ensure_ascii=False)],
    }


# ============================================================================
# 子命令：init
# ============================================================================


def cmd_init(args: argparse.Namespace) -> int:
    host: str = args.host
    port: int = args.port
    db_path: str | None = args.db
    total: int = args.total_chapters
    data_dir: Path = Path(args.data_dir).resolve()
    dry_run: bool = args.dry_run
    genre_preset: str | None = args.genre_preset

    data_dir.mkdir(parents=True, exist_ok=True)
    prog_path = _progress_path(data_dir)
    if prog_path.exists():
        print(f"[m1] FAIL：{prog_path} 已存在；如需重建请先删除（保留可断点续跑）", flush=True)
        return 1

    base_url = f"http://{host}:{port}"
    print(f"[m1] init: base={base_url} total={total} dry_run={dry_run}", flush=True)
    failures: list[str] = []
    client = httpx.Client(base_url=base_url, timeout=60.0)
    try:
        # 1. health（验证服务可达）
        try:
            _check(client.get("/api/health"), "health")
        except Exception as exc:  # noqa: BLE001
            print(f"[m1] FAIL：health 不可达 {exc}", flush=True)
            return 2

        # 2. 建项目
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        project_name = f"m1_{ts}"
        premise = "M1 long-run 驱动脚本自建项目：用于长跑试验、断点续跑与熔断自证。"
        payload = {
            "name": project_name,
            "premise": premise,
            "genre": genre_preset or "general",
            "target_words": total * TARGET_WORD_COUNT,
        }
        try:
            project_id = _check(client.post("/api/projects", json=payload), "create-project")["project_id"]
            print(f"[m1] project created: {project_id} name={project_name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"create-project: {exc}")
            return 1

        # 3. 1 主角 + 1 反派（PRD §110 节奏：保障后续 state 写入有 character_state 锚点）
        protagonist_id: str | None = None
        antagonist_id: str | None = None
        try:
            protagonist_id = _check(
                client.post(
                    f"/api/projects/{project_id}/characters",
                    json={
                        "name": "M1 Protagonist",
                        "role": "protagonist",
                        "core_json": {"background": "dry-run / long-run 试验主角"},
                    },
                ),
                "character-protagonist",
            )["character_id"]
            antagonist_id = _check(
                client.post(
                    f"/api/projects/{project_id}/characters",
                    json={
                        "name": "M1 Antagonist",
                        "role": "antagonist",
                        "core_json": {"background": "dry-run / long-run 试验反派"},
                    },
                ),
                "character-antagonist",
            )["character_id"]
            print(f"[m1] characters: protagonist={protagonist_id} antagonist={antagonist_id}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"create-character: {exc}")

        # 4. agents/sync（保证 prompts ACTIVE 行存在；workflow AI 节点会查 prompts 表）
        try:
            _check(client.post("/api/agents/sync"), "agents/sync")
            print("[m1] agents/sync OK", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"agents/sync: {exc}")

        # 5. 建 N 章 PLANNED 骨架
        chapter_ids: list[str] = []
        for n in range(1, total + 1):
            try:
                ch = _check(
                    client.post(
                        f"/api/projects/{project_id}/chapters",
                        json={"number": n, "title": f"第 {n} 章"},
                    ),
                    f"chapter-{n}",
                )
                chapter_ids.append(ch["chapter_id"])
            except Exception as exc:  # noqa: BLE001
                failures.append(f"chapter-{n}: {exc}")
                break
        print(f"[m1] chapters created: {len(chapter_ids)}/{total}", flush=True)

        # 6. 落盘 progress.json
        prog: dict[str, Any] = {
            "project_id": project_id,
            "host": host,
            "port": port,
            "db_path": db_path or "",
            "data_dir": str(data_dir),
            "genre_preset": genre_preset,
            "total_chapters": total,
            "protagonist_id": protagonist_id,
            "antagonist_id": antagonist_id,
            "chapter_ids": chapter_ids,
            "completed": {},
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "dry_run": dry_run,
        }
        _save_progress(data_dir, prog)
        print(f"[m1] progress written: {_progress_path(data_dir)}", flush=True)

        # 7. dry-run 模式：写一份 mock_providers.json 供 run 阶段读取（避免再次构建）
        # 真实模式不写文件；mock payload 直接在 run 时构建（每个 chapter_id 不同）。
        if dry_run:
            (data_dir / "dry_run.flag").write_text("1\n", encoding="utf-8")
            print("[m1] dry-run flag set; run 阶段将注入 mock_providers", flush=True)

        # 8. 提示外部：若非 dry-run，需要在 init 完成后另起 shell 创建 model_configs
        if not dry_run:
            print(
                "[m1] NOTE：真实模式需要先创建 model_configs（reasoning + creative_writing 各 1 条\n"
                "     openai_compatible + minimax base_url + api_key 从 env 读），例如：\n"
                "     curl -X POST http://HOST:PORT/api/model-configs -H 'Content-Type: application/json' \\\n"
                '       -d \'{"capability":"reasoning","provider":"openai_compatible","model":"MiniMax-M3",\n'
                '            "params_json":{"base_url":"https://api.minimaxi.com/v1","timeout_s":900,"api_key_env":"MINIMAX_API_KEY"},"enabled":1}\'',
                flush=True,
            )

        if failures:
            print(f"[m1] init 存在失败：{failures}", flush=True)
            return 1
        return 0
    finally:
        client.close()


# ============================================================================
# 子命令：run
# ============================================================================


def _http_with_fail_counter(base_url: str) -> tuple[httpx.Client, list[int]]:
    """构建一个普通 client；连续失败计数由调用方维护（http_fail_count 列表）。"""
    # 必须大于服务端模型配置的最长 LLM 超时（timeout_s=480），否则同步 POST 会被客户端先掐断；
    # 留轮询余量（workflow deadline=timeout_s + polling 间隙）。
    client = httpx.Client(base_url=base_url, timeout=900.0)
    return client, [0]


def _http_call(client: httpx.Client, http_fail_count: list[int], label: str, method: str, url: str, **kw: Any) -> dict:
    """带失败计数的 HTTP 调用；连接错计入连续失败。"""
    try:
        resp = getattr(client, method)(url, **kw)
    except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as exc:
        http_fail_count[0] += 1
        if http_fail_count[0] >= HTTP_FAIL_THRESHOLD:
            raise RuntimeError(f"HTTP 连续失败 {HTTP_FAIL_THRESHOLD} 次：last={exc}") from exc
        # 单次失败：等 1s 后让外层重试
        time.sleep(1.0)
        raise
    except httpx.HTTPError as exc:
        # 其他 httpx 异常：直接抛
        raise RuntimeError(f"HTTP error {label}: {exc}") from exc
    # 成功：重置计数器
    http_fail_count[0] = 0
    return _check(resp, label)


def cmd_run(args: argparse.Namespace) -> int:
    host: str = args.host
    port: int = args.port
    db_path: str | None = args.db
    data_dir: Path = Path(args.data_dir).resolve()
    dry_run: bool = args.dry_run
    budget: int | None = args.budget_total_tokens
    max_failures: int = args.max_failures
    from_n: int = args.from_chapter
    to_n: int = args.to_chapter
    force_lock: bool = args.force_lock

    data_dir.mkdir(parents=True, exist_ok=True)

    # 单实例锁：先于一切 IO，避免并发写同一 SQLite（M1 事故根因）。
    acquired, conflict = _acquire_lock(data_dir, force=force_lock)
    if not acquired and conflict is not None:
        cp = conflict.get("pid")
        cs = conflict.get("started_at")
        ch = conflict.get("host")
        print(
            f"[m1] FAIL：检测到另一实例仍在运行（pid={cp}, started_at={cs}, host={ch}）。"
            f" 如确认旧进程已死或残留锁：重跑时加 --force-lock 强制接管。",
            flush=True,
        )
        print(f"[m1] 锁文件：{_lock_path(data_dir)}", flush=True)
        return RUN_LOCK_HELD_EXIT
    if acquired:
        lock_note = "（force 接管）" if force_lock else ""
        print(f"[m1] 单实例锁已获取 pid={os.getpid()} {lock_note}", flush=True)

    # 锁一旦拿到，不论 run 中途抛什么异常，finally 都释放（即使 client 没创建）
    try:
        return _cmd_run_locked(args, host, port, db_path, data_dir, dry_run, budget, max_failures, from_n, to_n)
    finally:
        _release_lock(data_dir)


def _cmd_run_locked(
    args: argparse.Namespace,
    host: str,
    port: int,
    db_path: str | None,
    data_dir: Path,
    dry_run: bool,
    budget: int | None,
    max_failures: int,
    from_n: int,
    to_n: int,
) -> int:
    """cmd_run 拿到锁后的实际执行体；抽出函数便于嵌套 try/finally 不嵌套缩进。"""
    _ = args  # 当前 unused（保留接口以备将来加参数透传）
    prog = _load_progress(data_dir)
    if not prog or not prog.get("project_id"):
        print(f"[m1] FAIL：未发现 progress.json，请先 init（{_progress_path(data_dir)}）", flush=True)
        return 2

    project_id = prog["project_id"]
    chapter_ids: list[str] = prog.get("chapter_ids") or []
    protagonist_id = prog.get("protagonist_id")
    if not chapter_ids:
        print("[m1] FAIL：progress.json 中 chapter_ids 为空", flush=True)
        return 2

    base_url = f"http://{host}:{port}"
    print(
        f"[m1] run: base={base_url} range=[{from_n}, {to_n}] "
        f"budget={budget} max_failures={max_failures} dry_run={dry_run}",
        flush=True,
    )

    client, http_fail_count = _http_with_fail_counter(base_url)
    http_fail_count[0] = 0
    try:
        # 健康检查
        try:
            _http_call(client, http_fail_count, "health", "get", "/api/health")
        except Exception as exc:  # noqa: BLE001
            print(f"[m1] FAIL：health {exc}", flush=True)
            return 2

        # 累计 token（用于预算熔断）
        cumulative_total_tokens = 0
        for ch_no, ch_id in enumerate(chapter_ids, start=1):
            if ch_no < from_n or ch_no > to_n:
                continue
            if str(ch_no) in (prog.get("completed") or {}):
                print(f"[m1] ch{ch_no:03d} 跳过（progress.json 已 complete）", flush=True)
                continue
            # 预算检查
            if budget is not None and cumulative_total_tokens >= budget:
                print(
                    f"[m1] STOP：累计 token {cumulative_total_tokens} >= budget {budget}，"
                    f"已停止在 ch{ch_no:03d} 前。续跑命令：\n"
                    f"    python scripts/m1_long_run.py run --port {port} --from {ch_no} --to {to_n}"
                    + (" --budget-total-tokens NEW_BUDGET" if budget else ""),
                    flush=True,
                )
                return 3
            print(f"[m1] === ch{ch_no:03d} ===  chapter_id={ch_id}", flush=True)

            # 断点续跑：先查 chapters.status，再决定要跑哪些工作流。
            current_status = _get_chapter_status(client, ch_id)
            wfs_to_run = _workflows_for_status(current_status)
            if not wfs_to_run:
                print(
                    f"    [m1] 检测到 {current_status}（已完成或已发布），跳过工作流直接采集",
                    flush=True,
                )

            ok = False
            # 同一工作流环节的尝试上限（每工作流独立计数；与原文 max_failures 语义对齐）
            wf_attempts: dict[str, int] = {wf: 0 for wf in _WORKFLOW_CHAIN}
            # 整章级尝试计数（仅作日志 / 进度记录用，熔断以 wf_attempts 为准）
            chapter_attempts = 0

            detail: dict[str, Any] = {
                "chapter_no": ch_no,
                "chapter_id": ch_id,
                "chapter_attempts": chapter_attempts,
                "wf_attempts": dict(wf_attempts),
                "started_at": _now_iso(),
                "workflows": {},
                "failed_wf": None,
                "status": "RUNNING",
            }

            # 同章重试循环：单工作流 FAILED 时，按 chapters.status 重新计算续跑起点并继续，
            # 同一工作流累计尝试超过 --max-failures 才真正退出。
            while True:
                try:
                    payloads = _build_payloads(
                        dry_run=dry_run,
                        db_path=db_path,
                        ch_id=ch_id,
                        character_id=protagonist_id,
                        quality_gate_mode=getattr(args, "quality_gate_mode", "report"),
                    )
                    run_ids: list[str] = []
                    wall_total = 0.0

                    # 若需要跑工作流：按工作流列表循环；任一失败则按章节当前 status
                    # 重新计算续跑起点，对失败的工作流 +1 次尝试后重试。
                    while wfs_to_run:
                        chapter_attempts += 1
                        detail["chapter_attempts"] = chapter_attempts
                        detail["current_status"] = _get_chapter_status(client, ch_id)
                        wfs_to_run = _workflows_for_status(detail["current_status"])

                        for wf in wfs_to_run:
                            wf_attempts[wf] += 1
                            detail["wf_attempts"] = dict(wf_attempts)
                            if wf_attempts[wf] > max_failures:
                                detail["failed_wf"] = wf
                                raise _WorkflowFailure(
                                    f"工作流 {wf} 累计尝试 {wf_attempts[wf]} 次，超过上限 {max_failures}"
                                )

                            body = dict(payloads[wf])
                            run_id, final_status, wall_s = _run_one_workflow(
                                client, project_id, ch_id, wf, body,
                            )
                            run_ids.append(run_id)
                            wall_total += wall_s
                            detail["workflows"][wf] = {
                                "run_id": run_id,
                                "status": final_status,
                                "wall_s": round(wall_s, 2),
                                "attempt": wf_attempts[wf],
                            }
                            print(
                                f"    {wf:6s} → {final_status} ({wall_s:.1f}s, "
                                f"尝试 {wf_attempts[wf]}/{max_failures})  run={run_id[:16]}…",
                                flush=True,
                            )
                            if final_status != "COMPLETED":
                                detail["failed_wf"] = wf
                                raise _WorkflowFailure(f"工作流 {wf} 状态 {final_status}")

                        # 这一轮 wfs_to_run 全部成功 → 重新查 status，
                        # 若变 COMMITTED/RELEASED 则退出；否则循环结束（空列表）。
                        detail["current_status"] = _get_chapter_status(client, ch_id)
                        wfs_to_run = _workflows_for_status(detail["current_status"])
                        if not wfs_to_run:
                            break

                    # 采集指标
                    metrics = _aggregate_ai_logs(db_path, run_ids) if db_path else {
                        "call_count": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "retry_sum": 0,
                        "max_latency_ms": 0,
                    }
                    quality = _latest_quality(db_path, ch_id) if db_path else {}
                    state = _story_state(db_path, project_id) if db_path else {"state_version": 0, "snapshot_bytes": 0}
                    prose_chars = _latest_prose_chars(db_path, ch_id) if db_path else 0
                    # V3.7：补充 word_status / deviation_pct 观测字段；
                    # target 与脚本常量 TARGET_WORD_COUNT 对齐（与 chapter-write 传入一致）。
                    if db_path:
                        word_stats = _latest_prose_word_stats(db_path, ch_id, TARGET_WORD_COUNT)
                    else:
                        word_stats = classify_prose_length("", TARGET_WORD_COUNT)

                    detail["metrics"] = metrics
                    detail["quality"] = quality
                    detail["state"] = state
                    detail["prose_chars"] = prose_chars
                    detail["word_status"] = word_stats["status"]
                    detail["deviation_pct"] = word_stats["deviation_pct"]
                    detail["wall_total_s"] = round(wall_total, 2)
                    detail["completed_at"] = _now_iso()
                    detail["status"] = "COMPLETED"

                    cumulative_total_tokens += int(metrics.get("total_tokens") or 0)
                    print(
                        f"    metrics: total_tokens={metrics['total_tokens']} "
                        f"calls={metrics['call_count']} quality_overall={quality.get('overall')} "
                        f"state_version={state['state_version']} prose_chars={prose_chars}",
                        flush=True,
                    )

                    # 落盘 chNNN.json + progress.json
                    _save_chapter_detail(data_dir, ch_no, detail)
                    prog.setdefault("completed", {})[str(ch_no)] = {
                        "chapter_id": ch_id,
                        "run_ids": run_ids,
                        "wall_total_s": round(wall_total, 2),
                        "total_tokens": metrics["total_tokens"],
                        "quality_overall": quality.get("overall"),
                        "state_version": state["state_version"],
                        "completed_at": detail["completed_at"],
                    }
                    _save_progress(data_dir, prog)
                    ok = True
                    break
                except _WorkflowFailure as exc:
                    failed_wf = detail.get("failed_wf")
                    attempts_so_far = wf_attempts.get(failed_wf, 0) if failed_wf else 0
                    if attempts_so_far > max_failures:
                        # 真正耗尽：保存 FAILED 现场、清 completed、打印 STOP、退章。
                        detail["status"] = "FAILED"
                        detail["error"] = str(exc)
                        detail["current_status"] = _get_chapter_status(client, ch_id)
                        _save_chapter_detail(data_dir, ch_no, detail)
                        prog.setdefault("completed", {}).pop(str(ch_no), None)
                        _save_progress(data_dir, prog)
                        print(f"    [m1] ch{ch_no:03d} 失败：{exc}", flush=True)
                        print(
                            f"[m1] STOP：ch{ch_no:03d} 工作流失败（{failed_wf}）耗尽重试。"
                            f"已保留现场 ch{ch_no:03d}.json，下次 run 会按 chapters.status 断点续跑。",
                            flush=True,
                        )
                        ok = False
                        break
                    # 未耗尽：按状态断点继续重试（清掉旧 completed 保险）。
                    detail["status"] = "RETRYING"
                    detail["error"] = str(exc)
                    detail["current_status"] = _get_chapter_status(client, ch_id)
                    _save_chapter_detail(data_dir, ch_no, detail)
                    prog.setdefault("completed", {}).pop(str(ch_no), None)
                    _save_progress(data_dir, prog)
                    print(
                        f"    [m1] ch{ch_no:03d} {failed_wf} 失败（第 {attempts_so_far}/{max_failures} 次），"
                        f"按状态断点重试…",
                        flush=True,
                    )
                    # 让下次 while True 迭代的开头重新查 status 并推导 wfs_to_run
                    continue
                except RuntimeError as exc:
                    # 非工作流 FAILED 的 RuntimeError（HTTP 5 连败等）：按 FAILED 留痕并退章。
                    detail["status"] = "FAILED"
                    detail["error"] = str(exc)
                    detail["current_status"] = _get_chapter_status(client, ch_id)
                    _save_chapter_detail(data_dir, ch_no, detail)
                    prog.setdefault("completed", {}).pop(str(ch_no), None)
                    _save_progress(data_dir, prog)
                    print(f"    [m1] ch{ch_no:03d} 失败：{exc}", flush=True)
                    print(
                        f"[m1] STOP：ch{ch_no:03d} 工作流失败（{detail.get('failed_wf')}）耗尽重试。"
                        f"已保留现场 ch{ch_no:03d}.json，下次 run 会按 chapters.status 断点续跑。",
                        flush=True,
                    )
                    return 4
                except Exception as exc:  # noqa: BLE001
                    detail["status"] = "FAILED"
                    detail["error"] = f"{type(exc).__name__}: {exc}"
                    detail["current_status"] = _get_chapter_status(client, ch_id)
                    _save_chapter_detail(data_dir, ch_no, detail)
                    prog.setdefault("completed", {}).pop(str(ch_no), None)
                    _save_progress(data_dir, prog)
                    print(
                        f"    [m1] ch{ch_no:03d} 未知异常：{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    print(
                        f"[m1] STOP：ch{ch_no:03d} 异常。已保留现场 ch{ch_no:03d}.json，"
                        f"下次 run 会按 chapters.status 断点续跑。",
                        flush=True,
                    )
                    return 4

            if not ok:
                # 同章重试耗尽后退出 while True 循环 → 退整次 run。
                return 4

        print(f"[m1] run DONE. 累计 token={cumulative_total_tokens}", flush=True)
        return 0
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _build_payloads(  # noqa: PLR0913 — 长跑脚本统一构造入口
    *,
    dry_run: bool,
    db_path: str | None,
    ch_id: str,
    character_id: str | None,
    quality_gate_mode: str = "report",
) -> dict[str, dict[str, Any]]:
    """构造四工作流的 body。dry-run 注入 mock_providers；真实模式走正常 payload。"""
    base_body = {
        "target_word_count": TARGET_WORD_COUNT,
        "quality_gate_mode": quality_gate_mode,
    }
    if character_id:
        # 用 character_id 作为 author_intent 触发点，便于 reviewer 找到人物上下文
        base_body["author_intent"] = f"推进角色 {character_id} 的主线"
    if dry_run:
        if character_id is None:
            raise RuntimeError("dry-run 模式需要 progress.json 含 protagonist_id")
        mocks = _build_dry_run_mock_payloads(ch_id, character_id)
        return {
            "plan": {**base_body, "mock_providers": mocks, "expected_role": "setup"},
            "write": {**base_body, "mock_providers": mocks},
            "review": {**base_body, "mock_providers": mocks},
            "commit": {**base_body, "mock_providers": mocks},
        }
    return {
        wf: dict(base_body) for wf in ("plan", "write", "review", "commit")
    }


# ============================================================================
# 子命令：status
# ============================================================================


def cmd_status(args: argparse.Namespace) -> int:
    data_dir: Path = Path(args.data_dir).resolve()
    prog = _load_progress(data_dir)
    if not prog:
        print(f"[m1] status：未发现 progress.json（{_progress_path(data_dir)}）", flush=True)
        return 1
    completed: dict[str, Any] = prog.get("completed") or {}
    chapter_ids: list[str] = prog.get("chapter_ids") or []
    total = len(chapter_ids)
    done = len(completed)
    total_tokens = sum(int(v.get("total_tokens") or 0) for v in completed.values())
    overalls = [v.get("quality_overall") for v in completed.values() if v.get("quality_overall") is not None]
    last = None
    if completed:
        last_key = max(completed.keys(), key=lambda k: int(k))
        last = completed[last_key]
    print("=== m1_long_run status ===", flush=True)
    print(f"  data_dir           : {data_dir}", flush=True)
    print(f"  project_id         : {prog.get('project_id')}", flush=True)
    print(f"  dry_run            : {prog.get('dry_run')}", flush=True)
    print(f"  total_chapters     : {total}", flush=True)
    print(f"  completed          : {done}", flush=True)
    print(f"  pending            : {total - done}", flush=True)
    print(f"  cumulative_tokens  : {total_tokens}", flush=True)
    print(
        f"  quality overall    : count={len(overalls)} avg={sum(overalls)/len(overalls):.1f}"
        if overalls
        else "  quality overall    : (none)",
        flush=True,
    )
    if last:
        print(
            f"  last chapter       : ch{int(last_key):03d} total_tokens={last.get('total_tokens')} "
            f"quality={last.get('quality_overall')} state_version={last.get('state_version')}",
            flush=True,
        )
    print(f"  started_at         : {prog.get('started_at')}", flush=True)
    print(f"  updated_at         : {prog.get('updated_at')}", flush=True)
    return 0


# ============================================================================
# argparse
# ============================================================================


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m1_long_run.py",
        description="M1 long-run 驱动：连续生产 N 章小说（HTTP 驱动已运行的服务）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python scripts/m1_long_run.py init --port 18091 --total-chapters 50\n"
            "  python scripts/m1_long_run.py run --port 18091 --from 1 --to 50 --budget-total-tokens 200000\n"
            "  python scripts/m1_long_run.py status --port 18091\n"
            "  python scripts/m1_long_run.py --dry-run init --port 18091 --total-chapters 2\n"
        ),
    )
    p.add_argument(
        "--host",
        default=os.environ.get("NOVELOS_API_HOST", DEFAULT_HOST),
        help="服务 host（默认 127.0.0.1）",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NOVELOS_PORT") or DEFAULT_PORT),
        help="服务 port（默认 18081）",
    )
    p.add_argument(
        "--db",
        default=os.environ.get("NOVELOS_DB_PATH", ""),
        help="服务 SQLite 路径（用于直查 ai_call_logs / quality_reports / story_states；留空跳过指标）",
    )
    p.add_argument(
        "--data-dir",
        default=os.environ.get("M1_RUN_DATA_DIR", "data/m1_run"),
        help="进度文件 / 章节明细所在目录（默认 data/m1_run）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="mock 模式：init/run 全流程走通，但用 mock_providers 而非真实 LLM",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="创建项目、人物、N 章骨架")
    p_init.add_argument("--total-chapters", type=int, required=True, help="总章数")
    p_init.add_argument("--genre-preset", default=None, help="项目 genre（可留空）")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="按 --from/--to 跑四工作流，支持续跑 + 熔断")
    p_run.add_argument("--from", dest="from_chapter", type=int, required=True, help="起始章号（含）")
    p_run.add_argument("--to", dest="to_chapter", type=int, required=True, help="结束章号（含）")
    p_run.add_argument("--budget-total-tokens", type=int, default=None, help="累计 total_tokens 预算")
    p_run.add_argument("--max-failures", type=int, default=3, help="单章最大重试次数")
    p_run.add_argument(
        "--quality-gate-mode",
        choices=("report", "enforce"),
        default="report",
        help="commit 质量门禁模式（默认 report 保持长跑不阻断；enforce 用于验证门禁拦截）",
    )
    p_run.add_argument(
        "--force-lock",
        action="store_true",
        help=(
            "强制接管 <data-dir>/.run.lock（跳过 PID 存活探测）。"
            "用于清理残留锁或确认旧进程已死后接管；正常情况不要用。"
        ),
    )
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="打印进度摘要")
    p_status.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
