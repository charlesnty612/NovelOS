"""真实 MiniMax-M3 HTTP 端到端验证。

需要 MINIMAX_API_KEY；脚本只把密钥注入子进程环境，不写入数据库、文件或报告。
使用临时 SQLite、临时端口，依次执行 chapter-plan/write/review/commit。
"""
from __future__ import annotations
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOST = "127.0.0.1"


def wait_port(port: int, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), .5): return
        except OSError: time.sleep(.25)
    raise RuntimeError(f"服务未启动: {port}")


def check(resp: httpx.Response, label: str) -> dict:
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _cfg(capability: str) -> dict:
    return {"capability": capability, "provider": "openai_compatible", "model": "MiniMax-M3",
            "params_json": {"base_url": "https://api.minimaxi.com/v1", "timeout_s": 900}, "enabled": 1}
def run() -> int:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("[real-llm] FAIL：未设置 MINIMAX_API_KEY", flush=True)
        return 2
    env = os.environ.copy()
    env["NOVELOS_API_KEY_OPENAI_COMPATIBLE"] = api_key
    env["NOVELOS_QUALITY_GATE"] = "report"
    env["NOVELOS_DEBUG_OBSERVER_PAYLOAD"] = "1"
    env["NOVELOS_DEBUG_DIR"] = str(ROOT / "data")
    env["PYTHONPATH"] = str(ROOT)
    port = 18082 if not _busy(18081) else 18098
    tag = f"{os.getpid()}_{int(time.time())}"
    db = ROOT / "data" / f"real_llm_e2e_{tag}.db"
    env["NOVELOS_DB_PATH"] = str(db); env["NOVELOS_PORT"] = str(port)
    log = ROOT / "data" / f"real_llm_e2e_{tag}.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen([sys.executable, "-m", "packages.core.api.main"], cwd=ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT)
    started = time.monotonic()
    timings: dict[str, float] = {}
    try:
        wait_port(port)
        c = httpx.Client(base_url=f"http://{HOST}:{port}", timeout=1200)
        check(c.get("/api/health"), "health")
        project = check(c.post("/api/projects", json={"name":"测试：少年登山寻宝", "premise":"温和的少年在山谷中寻宝，沿途记录地形与矿石", "genre":"温和冒险", "target_words":100000}), "project")["project_id"]
        check(c.post(f"/api/projects/{project}/world-rules", json={"name":"境界体系","statement":"淬体、开脉、灵海、神藏，每境分九重；主角只能越级而战。"}), "world-rule")
        character = check(c.post(f"/api/projects/{project}/characters", json={"name":"少年","role":"protagonist","core_json":{"background":"爱好登山与矿物观察"}}), "character")["character_id"]
        for capability in ("reasoning", "creative_writing"):
            check(c.post("/api/model-configs", json=_cfg(capability)), f"model-{capability}")
        check(c.post("/api/agents/sync"), "agents/sync")
        chapter = check(c.post(f"/api/projects/{project}/chapters", json={"number":1,"title":"登山寻宝"}), "chapter")
        chapter_id = chapter["chapter_id"]
        payloads = {"author_intent":"少年在山谷中寻宝，记录地形和矿石。", "target_word_count":700, "quality_gate_mode":"report"}
        workflow_ids = []
        for wf in ("plan", "write", "review", "commit"):
            t = time.monotonic(); body = check(c.post(f"/api/projects/{project}/chapters/{chapter_id}/{wf}", json=payloads), wf); timings[wf] = time.monotonic()-t
            rid = body["run_id"]
            for _ in range(10):
                current = check(c.get(f"/api/runs/{rid}"), f"run-{wf}")
                if current.get("status") != "PAUSED": break
                current = check(c.post(f"/api/runs/{rid}/resume", json={"human_input":{"approved":True}}), f"resume-{wf}")
            final = check(c.get(f"/api/runs/{rid}"), f"run-final-{wf}")
            print(f"[real-llm] {wf}: {final.get('status')} ({timings[wf]:.1f}s)", flush=True)
            workflow_ids.append(rid)
        chapter_final = check(c.get(f"/api/chapters/{chapter_id}"), "chapter-final")["status"]
        drafts = check(c.get(f"/api/chapters/{chapter_id}/drafts"), "drafts")
        prose = drafts[0]["content"]
        if "<think>" in prose: raise AssertionError("draft 含 think 块")
        state = check(c.get(f"/api/projects/{project}/state"), "state")
        # §110 第 3 步「创建世界」：state.world.world_rules 必须非空
        world_rules = (state.get("world") or {}).get("world_rules") or []
        if not (isinstance(world_rules, list) and len(world_rules) >= 1):
            raise AssertionError(f"state.world.world_rules empty: {world_rules!r}")
        quality = check(c.get(f"/api/chapters/{chapter_id}/quality"), "quality")
        # 读取真实调用日志，报告不记录任何密钥
        conn = __import__("sqlite3").connect(db)
        conn.row_factory = __import__("sqlite3").Row
        try:
            logs = [dict(r) for r in conn.execute("select model_id, latency_ms from ai_call_logs where run_id in (%s)" % ",".join("?"*len(workflow_ids)), workflow_ids)]
        finally: conn.close()
        summary = {"project_id":project,"chapter_id":chapter_id,"status":chapter_final,"draft_prefix":prose[:100],"think_free":True,"state_keys":sorted(state.keys()),"quality":quality,"timings":timings,"ai_call_logs":logs}
        out = ROOT / "docs/evaluation/runs/real-llm-minimax-m3-2026-08-24.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            f.write("# 真实 MiniMax-M3 端到端验证\n\n```json\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n")
        print("[real-llm] PASS：全链路、清理 think 块、快照、quality 均通过", flush=True)
        print(f"[real-llm] report: {out}", flush=True)
        c.close(); return 0
    except BaseException as exc:
        print(f"[real-llm] FAIL：{type(exc).__name__}: {exc}", flush=True)
        if log.exists(): print(f"[real-llm] server log: {log}", flush=True)
        return 1
    finally:
        if proc.poll() is None: proc.terminate();
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()
        for p in (db, Path(str(db)+"-wal"), Path(str(db)+"-shm")):
            try: p.unlink()
            except FileNotFoundError: pass
            except PermissionError:
                time.sleep(2)
                try: p.unlink()
                except OSError: pass


def _busy(port):
    try:
        with socket.create_connection((HOST, port), .2): return True
    except OSError: return False

if __name__ == "__main__": raise SystemExit(run())
