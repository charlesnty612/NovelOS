#!/usr/bin/env python3
"""NovelOS 薄客户端（stdlib-only / 零第三方依赖）。

设计契约（与本仓「薄客户端」纪律一致）
--------------------------------------
1. **零业务逻辑**：本脚本只做「URL 拼装 + HTTP 调用 + JSON 打印」。
   字数带（word band）、门禁判定（block / confirm / auto）、章节状态机、
   Story State 推进——**全部留在服务端**。本脚本复刻任何一条判定逻辑，
   都会立刻与服务端产生第二份事实源。
2. **stdlib-only**：只用标准库。``--help`` 必须能在完全离线（无网络、
   无第三方包）的机器上退出码 0。
3. **唯一的客户端便利变换**：``--chapter-no N`` → ``chapter_id``。
   它是一次「列表端点查表」，不产生任何 NovelOS 侧的判断；除此之外
   所有参数都逐字透传给服务端。

子命令速览（``novelos.py help`` 可得）::

    doctor            探测本机 NovelOS 是否在跑
    ensure            服务不在跑时尝试拉起（不下载任何东西）
    project list|create|show
    chapter list|status|plan|write|review|commit
    run status|resume|approve
    state show|diff
    quality report|gate-override
    delivery verdict|signing-check
    export --kind txt|docx|submission
    help

退出码：``0`` 成功；``2`` 参数/用法错误；``3`` 服务不可达；``4`` 服务端返回 4xx；
``5`` 服务端返回 5xx；``6`` 本地 I/O 失败。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:18081"
DEFAULT_TIMEOUT = 60.0
HEALTH_PATH = "/api/health"

#: run 的终态（run 到这里就不再变化）。
TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
#: 轮询 ``--wait`` 的停止条件 = 终态 ∪ PAUSED（PAUSED 表示「在等人工」，不是失败）。
STOP_RUN_STATUSES = TERMINAL_RUN_STATUSES + ("PAUSED",)

#: ``export --kind`` → 服务端 ``format`` 查询参数的映射。
#: 服务端只认 ``txt / docx / fanqie``；``fanqie`` 就是「番茄投稿包」，
#: 这里额外接受更直白的别名 ``submission``。
EXPORT_KIND_TO_FORMAT = {
    "txt": "txt",
    "docx": "docx",
    "submission": "fanqie",
    "fanqie": "fanqie",
}

EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_CLIENT_ERROR = 4
EXIT_SERVER_ERROR = 5
EXIT_IO = 6


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class CliError(Exception):
    """带可操作提示与退出码的错误。

    **不抛裸 traceback**：本脚本的调用方是 coding agent，裸栈没有信息量，
    「哪个 URL 失败 / 服务器说了什么 / 下一步该干什么」才有。
    """

    def __init__(self, message: str, *, hint: str | None = None, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def emit(data: Any, *, pretty: bool) -> None:
    """打印机器可读结果（默认紧凑 JSON；``--pretty`` 缩进 + ensure_ascii=False）。"""
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False, default=str)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    sys.stdout.write(text + "\n")


# ---------------------------------------------------------------------------
# HTTP（stdlib urllib）
# ---------------------------------------------------------------------------


def _decode_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
    return {"_raw": text}


def _extract_detail(payload: Any) -> str:
    """把服务端的错误体压成一行人话。"""
    if isinstance(payload, dict):
        detail = payload.get("detail", payload.get("error"))
        if detail is not None:
            if isinstance(detail, str):
                return detail
            try:
                return json.dumps(detail, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(detail)
    if isinstance(payload, str):
        return payload
    return ""


def http_request(
    method: str,
    path: str,
    *,
    base_url: str,
    params: dict[str, Any] | None = None,
    body: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str, bytes]:
    """发一次 HTTP 请求，返回 ``(status, content_type, raw_body)``。

    4xx / 5xx **不抛异常**（由调用方决定怎么处理），只有「连不上」才抛
    :class:`CliError`。
    """
    url = base_url.rstrip("/") + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)

    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:  # 4xx / 5xx：服务端有话要说
        raw = exc.read() if hasattr(exc, "read") else b""
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", raw
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise CliError(
            f"无法连接 NovelOS 服务：{url}（{reason}）",
            hint=(
                "先探测：novelos.py doctor；"
                "再尝试拉起：novelos.py ensure；"
                "或手动启动：cd <仓库根> && python -m packages.core.api.main"
            ),
            exit_code=EXIT_UNREACHABLE,
        ) from exc
    except TimeoutError as exc:
        raise CliError(
            f"请求超时（{timeout}s）：{method} {url}",
            hint="长任务请改用 run status --wait --timeout 轮询；或调大 --timeout。",
            exit_code=EXIT_UNREACHABLE,
        ) from exc


def api_json(
    method: str,
    path: str,
    *,
    base_url: str,
    params: dict[str, Any] | None = None,
    body: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    allow_status: tuple[int, ...] = (),
) -> Any:
    """发一次请求并解析 JSON 响应；非 2xx 且不在 ``allow_status`` 内则抛错。"""
    status, content_type, raw = http_request(
        method, path, base_url=base_url, params=params, body=body, timeout=timeout
    )
    payload = _decode_body(raw, content_type) if raw else None
    if 200 <= status < 300 or status in allow_status:
        return payload
    detail = _extract_detail(payload) or raw.decode("utf-8", errors="replace")[:400]
    if 400 <= status < 500:
        raise CliError(
            f"{method} {path} → HTTP {status}：{detail}",
            hint="这是请求本身的问题（参数 / 状态机 / 归属校验），改请求后重试。",
            exit_code=EXIT_CLIENT_ERROR,
        )
    raise CliError(
        f"{method} {path} → HTTP {status}：{detail}",
        hint="服务端内部错误；用 novelos.py run status <run_id> 看该 run 的 error 字段。",
        exit_code=EXIT_SERVER_ERROR,
    )


# ---------------------------------------------------------------------------
# 仓库定位（ensure 用）
# ---------------------------------------------------------------------------


def detect_repo_root(explicit: str | None = None) -> Path:
    """定位 NovelOS 仓库根目录。

    顺序：``--repo-root`` 参数 > ``NOVELOS_REPO_ROOT`` 环境变量 > 脚本自身位置
    上溯三级（``<root>/skills/novelos/scripts/novelos.py`` → ``<root>``）。

    **显式给的路径一律严格校验**：指定了却不含 ``packages/core/api/main.py``
    ⇒ 直接报错退出，**不静默回落到自动探测**（否则用户打错路径会被"猜"到
    另一个仓库去起服务，那种错比报错更难查）。

    **不下载任何东西**：找不到就报错退出，并给出「怎么手动起」的指引。
    """
    entry = Path("packages") / "core" / "api" / "main.py"

    def _is_repo(path: Path) -> bool:
        return (path / entry).is_file()

    env_root = os.environ.get("NOVELOS_REPO_ROOT", "").strip()
    for source, raw in (("--repo-root", explicit), ("NOVELOS_REPO_ROOT", env_root)):
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise CliError(
                f"{source} 指定的路径无法解析：{raw}（{exc}）",
                hint="给一个包含 packages/core/api/main.py 的 NovelOS 仓库根目录。",
                exit_code=EXIT_IO,
            ) from exc
        if not _is_repo(resolved):
            raise CliError(
                f"{source} 指定的路径不是 NovelOS 仓库根：{resolved}",
                hint=f"该目录下应有 {entry.as_posix()}；请核对路径。",
                exit_code=EXIT_IO,
            )
        return resolved

    candidate = Path(__file__).resolve().parents[3]
    if _is_repo(candidate):
        return candidate
    raise CliError(
        "找不到 NovelOS 仓库根目录（应含 packages/core/api/main.py）。",
        hint=(
            "用 --repo-root <路径> 指定，或设环境变量 NOVELOS_REPO_ROOT；"
            "也可用 cd <仓库根> && python -m packages.core.api.main 手动起服务。"
        ),
        exit_code=EXIT_IO,
    )


def port_of(base_url: str) -> int | None:
    """从 base URL 里取端口（取不到返回 None）。"""
    parsed = urllib.parse.urlparse(base_url)
    try:
        return parsed.port
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# doctor / ensure
# ---------------------------------------------------------------------------


def probe_health(base_url: str, timeout: float) -> dict[str, Any] | None:
    """探测 ``/api/health``；不可达返回 None（不抛错）。"""
    try:
        status, content_type, raw = http_request("GET", HEALTH_PATH, base_url=base_url, timeout=timeout)
    except CliError:
        return None
    if status != 200:
        return None
    payload = _decode_body(raw, content_type)
    return payload if isinstance(payload, dict) else {"raw": payload}


def cmd_doctor(args: argparse.Namespace) -> int:
    health = probe_health(args.base_url, args.timeout)
    if health is None:
        raise CliError(
            f"NovelOS 服务未在 {args.base_url} 运行（探测 {args.base_url}{HEALTH_PATH} 无响应）。",
            hint=(
                "下一步：novelos.py ensure（尝试拉起）；"
                "若 ensure 也失败，用 cd <仓库根> && python -m packages.core.api.main 手动起，"
                "再重跑 novelos.py doctor。"
            ),
            exit_code=EXIT_UNREACHABLE,
        )
    emit({"ok": True, "base_url": args.base_url, "health": health}, pretty=args.pretty)
    return 0


def cmd_ensure(args: argparse.Namespace) -> int:
    health = probe_health(args.base_url, args.timeout)
    if health is not None:
        emit(
            {"ok": True, "started": False, "base_url": args.base_url, "health": health},
            pretty=args.pretty,
        )
        return 0

    repo_root = detect_repo_root(args.repo_root)
    log_path = Path(args.log_file) if args.log_file else Path(tempfile.gettempdir()) / "novelos-skill-server.log"
    env = dict(os.environ)
    port = port_of(args.base_url)
    if port is not None and port != 18081:
        env["NOVELOS_PORT"] = str(port)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        log_handle = log_path.open("ab")
    except OSError as exc:
        raise CliError(f"无法打开服务日志文件 {log_path}：{exc}", exit_code=EXIT_IO) from exc

    creationflags = 0
    if sys.platform == "win32":  # 让子进程不随本 CLI 的 Ctrl-C 一起死
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        process = subprocess.Popen(  # 命令固定为 [sys.executable, "-m", ...]
            [sys.executable, "-m", "packages.core.api.main"],
            cwd=str(repo_root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        raise CliError(
            f"拉起 NovelOS 服务失败：{exc}",
            hint=f"手动执行：cd {repo_root} && {sys.executable} -m packages.core.api.main",
            exit_code=EXIT_IO,
        ) from exc
    log_handle.close()

    deadline = time.monotonic() + args.start_timeout
    health = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        health = probe_health(args.base_url, min(args.timeout, 5.0))
        if health is not None:
            break
        time.sleep(0.4)

    if health is None:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        raise CliError(
            f"NovelOS 服务在 {args.start_timeout}s 内未就绪（进程 pid={process.pid}）。",
            hint=(
                f"日志尾部（{log_path}）：\n{tail}\n"
                f"手动排查：cd {repo_root} && {sys.executable} -m packages.core.api.main"
            ),
            exit_code=EXIT_UNREACHABLE,
        )

    emit(
        {
            "ok": True,
            "started": True,
            "base_url": args.base_url,
            "pid": process.pid,
            "repo_root": str(repo_root),
            "log_file": str(log_path),
            "health": health,
        },
        pretty=args.pretty,
    )
    return 0


# ---------------------------------------------------------------------------
# 章节号 → chapter_id（唯一的客户端便利变换）
# ---------------------------------------------------------------------------


def resolve_chapter_id(base_url: str, project_id: str, *, number: int | None, chapter_id: str | None) -> str:
    """把「第 N 章」解析成 ``chapter_id``。

    这是**查表**（读一次 ``GET /projects/{id}/chapters`` 再按 ``number`` 匹配），
    不是 NovelOS 侧的判定——服务端才是章节归属与状态的唯一事实源。
    """
    if chapter_id:
        return chapter_id
    if number is None:
        raise CliError("需要 --chapter-no N 或 --chapter-id <id> 之一。", exit_code=EXIT_USAGE)
    chapters = api_json("GET", f"/api/projects/{project_id}/chapters", base_url=base_url)
    rows = chapters if isinstance(chapters, list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("number") == number:
            found = row.get("chapter_id")
            if found:
                return str(found)
    known = [r.get("number") for r in rows if isinstance(r, dict)]
    raise CliError(
        f"项目 {project_id} 下没有第 {number} 章（现有章号：{known}）。",
        hint="用 novelos.py chapter list --project " + project_id + " 看现有章节。",
        exit_code=EXIT_CLIENT_ERROR,
    )


def _parse_json_arg(raw: str, flag: str) -> Any:
    """把命令行上的 JSON 字符串解析成对象（失败 ⇒ 可操作的用法错误）。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"{flag} 不是合法 JSON：{exc}",
            hint="例：--gate-override '{\"rule_ids\":[\"AI-BEAT-REPEAT\"],\"reason\":\"…\"}'",
            exit_code=EXIT_USAGE,
        ) from exc


# ---------------------------------------------------------------------------
# run 轮询
# ---------------------------------------------------------------------------


def wait_for_run(
    run_id: str,
    *,
    base_url: str,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    """轮询 ``GET /runs/{id}`` 直到 run 停止变化（终态或 PAUSED）。"""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while True:
        last = api_json("GET", f"/api/runs/{run_id}", base_url=base_url) or {}
        status = str(last.get("status", ""))
        if status in STOP_RUN_STATUSES or time.monotonic() >= deadline:
            return last
        time.sleep(max(interval, 0.1))


def start_workflow(
    workflow: str,
    args: argparse.Namespace,
    *,
    project_id: str,
) -> int:
    """起跑一个章节级 workflow（plan / write / review / commit）。"""
    body: dict[str, Any] = {}
    if args.author_intent is not None:
        body["author_intent"] = args.author_intent
    if args.target_word_count is not None:
        body["target_word_count"] = args.target_word_count
    if args.expected_role is not None:
        body["expected_role"] = args.expected_role
    if args.quality_gate_mode is not None:
        body["quality_gate_mode"] = args.quality_gate_mode
    if args.fresh_write:
        body["fresh_write"] = True
    if args.draft_version is not None:
        body["draft_version"] = args.draft_version
    if args.deep_review:
        body["deep_review"] = True
    if args.gate_override:
        # 服务端契约是**对象**（{"rule_ids":[…],"reason":"…"}）⇒ 这里必须解析成 dict，
        # 不能把原始字符串透传（那样 Pydantic 会判 422）。
        body["gate_override"] = _parse_json_arg(args.gate_override, "--gate-override")
    if args.fast_mode is not None:
        body["fast_mode"] = args.fast_mode
    if args.auto_commit:
        body["auto_commit"] = True

    chapter_id = resolve_chapter_id(
        args.base_url, project_id, number=args.chapter_no, chapter_id=args.chapter_id
    )
    payload = api_json(
        "POST",
        f"/api/projects/{project_id}/chapters/{chapter_id}/{workflow}",
        base_url=args.base_url,
        body=body,
    )
    run_id = str((payload or {}).get("run_id", ""))
    if args.wait and run_id:
        final = wait_for_run(
            run_id, base_url=args.base_url, timeout=args.wait_timeout, interval=args.wait_interval
        )
        emit(
            {
                "run_id": run_id,
                "workflow": workflow,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "started": payload,
                "final": final,
            },
            pretty=args.pretty,
        )
        return 0
    emit(
        {
            "run_id": run_id,
            "workflow": workflow,
            "project_id": project_id,
            "chapter_id": chapter_id,
            "started": payload,
        },
        pretty=args.pretty,
    )
    return 0


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def cmd_project_list(args: argparse.Namespace) -> int:
    params = {"include_archived": "true"} if args.include_archived else None
    emit(api_json("GET", "/api/projects", base_url=args.base_url, params=params), pretty=args.pretty)
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {"name": args.name}
    if args.genre is not None:
        body["genre"] = args.genre
    if args.premise is not None:
        body["premise"] = args.premise
    if args.target_words is not None:
        body["target_words"] = args.target_words
    if args.writing_bible is not None:
        body["writing_bible"] = args.writing_bible
    if args.fast_mode is not None:
        body["fast_mode"] = args.fast_mode
    emit(api_json("POST", "/api/projects", base_url=args.base_url, body=body), pretty=args.pretty)
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    emit(api_json("GET", f"/api/projects/{args.project_id}", base_url=args.base_url), pretty=args.pretty)
    return 0


def cmd_chapter_list(args: argparse.Namespace) -> int:
    emit(
        api_json("GET", f"/api/projects/{args.project_id}/chapters", base_url=args.base_url),
        pretty=args.pretty,
    )
    return 0


def cmd_chapter_status(args: argparse.Namespace) -> int:
    if args.chapter_id and not args.chapter_no:
        payload = api_json("GET", f"/api/chapters/{args.chapter_id}", base_url=args.base_url)
    else:
        if not args.project_id:
            raise CliError(
                "用 --chapter-no 时必须同时给 --project（或直接给 --chapter-id）。",
                exit_code=EXIT_USAGE,
            )
        chapter_id = resolve_chapter_id(
            args.base_url, args.project_id, number=args.chapter_no, chapter_id=args.chapter_id
        )
        payload = api_json("GET", f"/api/chapters/{chapter_id}", base_url=args.base_url)
    emit(payload, pretty=args.pretty)
    return 0


def cmd_chapter_workflow(workflow: str) -> Any:
    def _handler(args: argparse.Namespace) -> int:
        return start_workflow(workflow, args, project_id=args.project_id)

    return _handler


def cmd_run_status(args: argparse.Namespace) -> int:
    if args.wait:
        payload = wait_for_run(
            args.run_id, base_url=args.base_url, timeout=args.wait_timeout, interval=args.wait_interval
        )
    else:
        payload = api_json("GET", f"/api/runs/{args.run_id}", base_url=args.base_url)
    emit(payload, pretty=args.pretty)
    return 0


def cmd_run_resume(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {}
    if args.human_input:
        body["human_input"] = _parse_json_arg(args.human_input, "--human-input")
    if args.auto_revise_max is not None:
        body["auto_revise_max"] = args.auto_revise_max
    if args.regenerate:
        body["regenerate"] = True
    if args.author_intent is not None:
        body["author_intent"] = args.author_intent
    if args.target_word_count is not None:
        body["target_word_count"] = args.target_word_count
    payload = api_json("POST", f"/api/runs/{args.run_id}/resume", base_url=args.base_url, body=body)
    run_id = str((payload or {}).get("run_id", args.run_id))
    if args.wait:
        final = wait_for_run(
            run_id, base_url=args.base_url, timeout=args.wait_timeout, interval=args.wait_interval
        )
        emit({"run_id": run_id, "resumed": payload, "final": final}, pretty=args.pretty)
        return 0
    emit({"run_id": run_id, "resumed": payload}, pretty=args.pretty)
    return 0


def cmd_run_approve(args: argparse.Namespace) -> int:
    """人工决议：把 ``{"approved": true|false, ...}`` 作为 ``human_input`` 回灌。

    决议形状由服务端定义（``author_review`` 读 ``approved``；驳回并改稿再读
    ``revise`` / ``note``），本命令只负责把参数拼成那个对象。
    """
    human_input: dict[str, Any] = {"approved": not args.reject}
    if args.reject:
        if args.revise:
            human_input["revise"] = True
        if args.note is not None:
            human_input["note"] = args.note
    elif args.note is not None:
        human_input["note"] = args.note
    body: dict[str, Any] = {"human_input": human_input}
    if args.auto_revise_max is not None:
        body["auto_revise_max"] = args.auto_revise_max
    payload = api_json("POST", f"/api/runs/{args.run_id}/resume", base_url=args.base_url, body=body)
    run_id = str((payload or {}).get("run_id", args.run_id))
    if args.wait:
        final = wait_for_run(
            run_id, base_url=args.base_url, timeout=args.wait_timeout, interval=args.wait_interval
        )
        emit(
            {"run_id": run_id, "human_input": human_input, "resumed": payload, "final": final},
            pretty=args.pretty,
        )
        return 0
    emit({"run_id": run_id, "human_input": human_input, "resumed": payload}, pretty=args.pretty)
    return 0


def cmd_state_show(args: argparse.Namespace) -> int:
    params = {"branch_id": args.branch_id}
    if args.version is not None:
        payload = api_json(
            "GET",
            f"/api/projects/{args.project_id}/state/versions/{args.version}",
            base_url=args.base_url,
            params=params,
        )
    else:
        payload = api_json(
            "GET", f"/api/projects/{args.project_id}/state", base_url=args.base_url, params=params
        )
    emit(payload, pretty=args.pretty)
    return 0


def cmd_state_diff(args: argparse.Namespace) -> int:
    params = {"a": args.a, "b": args.b, "branch_id": args.branch_id}
    emit(
        api_json(
            "GET", f"/api/projects/{args.project_id}/state/diff", base_url=args.base_url, params=params
        ),
        pretty=args.pretty,
    )
    return 0


def cmd_quality_report(args: argparse.Namespace) -> int:
    if args.project_id:
        payload = api_json(
            "GET",
            f"/api/projects/{args.project_id}/quality",
            base_url=args.base_url,
            params={"limit": args.limit},
        )
    else:
        payload = api_json("GET", f"/api/chapters/{args.chapter_id}/quality", base_url=args.base_url)
    emit(payload, pretty=args.pretty)
    return 0


def cmd_quality_gate_override(args: argparse.Namespace) -> int:
    """生成 ``gate_override`` 对象（不提交）。

    服务端只认「``rule_ids`` 覆盖本次命中的**全部** confirm rule_id 且
    ``reason`` 非空」才放行；本命令只负责把命令行参数拼成那个 JSON。
    """
    if not args.rule_id:
        raise CliError("至少要有一个 --rule-id。", exit_code=EXIT_USAGE)
    if not (args.reason or "").strip():
        raise CliError("--reason 不能为空（服务端要求非空理由才放行）。", exit_code=EXIT_USAGE)
    override = {"rule_ids": list(args.rule_id), "reason": args.reason}
    emit({"gate_override": override}, pretty=args.pretty)
    return 0


def cmd_delivery_verdict(args: argparse.Namespace) -> int:
    emit(
        api_json(
            "GET", f"/api/projects/{args.project_id}/delivery-verdict", base_url=args.base_url
        ),
        pretty=args.pretty,
    )
    return 0


def cmd_delivery_signing_check(args: argparse.Namespace) -> int:
    emit(
        api_json("GET", f"/api/projects/{args.project_id}/signing-check", base_url=args.base_url),
        pretty=args.pretty,
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    fmt = EXPORT_KIND_TO_FORMAT[args.kind]
    params: dict[str, Any] = {"format": fmt}
    if args.chapter_no is not None:
        params["chapter_no"] = args.chapter_no
    status, content_type, raw = http_request(
        "GET",
        f"/api/projects/{args.project_id}/export",
        base_url=args.base_url,
        params=params,
    )
    if not (200 <= status < 300):
        detail = _extract_detail(_decode_body(raw, content_type))
        raise CliError(
            f"GET /api/projects/{args.project_id}/export → HTTP {status}：{detail}",
            hint="检查 --kind / --chapter-no；单章导出需要已存在的章节。",
            exit_code=EXIT_CLIENT_ERROR if 400 <= status < 500 else EXIT_SERVER_ERROR,
        )
    out_path = Path(args.out) if args.out else Path.cwd() / _default_export_name(args.project_id, fmt, args.chapter_no)
    try:
        out_path.write_bytes(raw)
    except OSError as exc:
        raise CliError(f"写文件失败 {out_path}：{exc}", exit_code=EXIT_IO) from exc
    emit(
        {
            "ok": True,
            "kind": args.kind,
            "format": fmt,
            "path": str(out_path.resolve()),
            "bytes": len(raw),
            "content_type": content_type,
        },
        pretty=args.pretty,
    )
    return 0


def _default_export_name(project_id: str, fmt: str, chapter_no: int | None) -> str:
    suffix = f"-ch{chapter_no}" if chapter_no is not None else "-book"
    ext = "docx" if fmt == "docx" else "txt"
    return f"{project_id}{suffix}.{ext}"


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def _add_wait_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait",
        action="store_true",
        help="起跑后轮询 run 直到终态（COMPLETED/FAILED/CANCELLED）或 PAUSED（等人工）",
    )
    parser.add_argument("--wait-timeout", type=float, default=900.0, help="--wait 的最长等待秒数（默认 900）")
    parser.add_argument("--wait-interval", type=float, default=2.0, help="--wait 的轮询间隔秒数（默认 2）")


def _add_chapter_selector(parser: argparse.ArgumentParser, *, project_required: bool = True) -> None:
    if project_required:
        parser.add_argument("--project", required=True, dest="project_id", help="项目 id（prj_...）")
    else:
        parser.add_argument("--project", dest="project_id", help="项目 id（prj_...）；用 --chapter-no 时必填")
    parser.add_argument("--chapter-no", type=int, help="章号（第 N 章）")
    parser.add_argument("--chapter-id", help="章节 id（ch_...）；给了就不查表")


def _add_workflow_body_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--author-intent", help="本次运行的作者铁律（追加在项目写作圣经之后）")
    parser.add_argument("--target-word-count", type=int, help="本章目标字数（100~100000）")
    parser.add_argument("--expected-role", help="本章期望的角色/职责描述")
    parser.add_argument(
        "--quality-gate-mode",
        choices=["enforce", "report"],
        help="质量门禁模式：enforce=阻断，report=只报告",
    )
    parser.add_argument("--fresh-write", action="store_true", help="全章重写（忽略改稿意见与既有草稿）")
    parser.add_argument("--draft-version", type=int, help="审校指定稿版本（chapter-review 用）")
    parser.add_argument("--deep-review", action="store_true", help="开启二审（deep_reviewer）")
    parser.add_argument(
        "--gate-override",
        help='门禁显式接受声明 JSON，如 {"rule_ids":["AI-BEAT-REPEAT"],"reason":"刻意的复沓"}',
    )
    parser.add_argument(
        "--fast-mode",
        dest="fast_mode",
        action="store_true",
        default=None,
        help="本次运行显式开快通道（关闸门不关判定）",
    )
    parser.add_argument(
        "--no-fast-mode",
        dest="fast_mode",
        action="store_false",
        help="本次运行显式关快通道（覆盖项目级设置）",
    )
    parser.add_argument("--auto-commit", action="store_true", help="写完自动衔接提交（仅 write 有意义）")
    _add_wait_flags(parser)


def build_parser() -> argparse.ArgumentParser:
    # 全局开关既可写在子命令**前**，也可写在子命令**后**（两种写法文档里都常见）。
    # 做法：顶层给默认值；再做一个 default=SUPPRESS 的"公共父解析器"挂到每个
    # 子解析器上——没写就不覆盖顶层的值，写了就以靠后的为准。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        default=argparse.SUPPRESS,
        help=f"NovelOS 服务地址（默认 {DEFAULT_BASE_URL}；也可用 NOVELOS_BASE_URL）",
    )
    common.add_argument(
        "--timeout", type=float, default=argparse.SUPPRESS, help="单次 HTTP 超时秒数（默认 60）"
    )
    common.add_argument(
        "--pretty", action="store_true", default=argparse.SUPPRESS, help="缩进 + 不转义非 ASCII（人读）"
    )

    parser = argparse.ArgumentParser(
        prog="novelos.py",
        description=(
            "NovelOS 薄客户端：只做 URL 拼装 + HTTP 调用 + JSON 打印，"
            "所有判定逻辑都留在服务端。先跑 `doctor` 确认服务在跑（或用 `ensure` 拉起）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  novelos.py doctor\n"
            "  novelos.py ensure\n"
            "  novelos.py project create --name '新书' --genre 玄幻\n"
            "  novelos.py chapter write --project prj_x --chapter-no 3 --target-word-count 3000\n"
            "  novelos.py chapter review --project prj_x --chapter-no 3 --wait\n"
            "  novelos.py chapter commit --project prj_x --chapter-no 3\n"
            "  novelos.py export --kind submission --project prj_x\n"
            "\n"
            "全局开关 --base-url / --timeout / --pretty 可写在子命令前或后。\n"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NOVELOS_BASE_URL", DEFAULT_BASE_URL),
        help=f"NovelOS 服务地址（默认 {DEFAULT_BASE_URL}；也可用 NOVELOS_BASE_URL）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次 HTTP 超时秒数（默认 60）")
    parser.add_argument("--pretty", action="store_true", help="缩进 + 不转义非 ASCII（人读）")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_doctor = sub.add_parser("doctor", help="探测本机 NovelOS 是否在跑", parents=[common])
    p_doctor.set_defaults(func=cmd_doctor)

    p_ensure = sub.add_parser("ensure", help="服务不在跑时尝试拉起（不下载任何东西）", parents=[common])
    p_ensure.add_argument("--repo-root", help="NovelOS 仓库根目录（默认自动探测）")
    p_ensure.add_argument("--log-file", help="服务日志文件（默认写临时目录）")
    p_ensure.add_argument(
        "--start-timeout", type=float, default=90.0, help="等待服务就绪的秒数（默认 90）"
    )
    p_ensure.set_defaults(func=cmd_ensure)

    p_project = sub.add_parser("project", help="项目：list / create / show", parents=[common])
    p_project_sub = p_project.add_subparsers(dest="subcommand", metavar="<sub>")
    pl = p_project_sub.add_parser("list", help="列出项目", parents=[common])
    pl.add_argument("--include-archived", action="store_true", help="含已归档项目")
    pl.set_defaults(func=cmd_project_list)
    pc = p_project_sub.add_parser("create", help="创建项目", parents=[common])
    pc.add_argument("--name", required=True, help="书名（1~200 字符）")
    pc.add_argument("--genre", help="题材")
    pc.add_argument("--premise", help="一句话简介")
    pc.add_argument("--target-words", type=int, help="全书目标字数")
    pc.add_argument("--writing-bible", help="项目级写作圣经（跨章长期铁律）")
    pc.add_argument("--fast-mode", dest="fast_mode", action="store_true", default=None, help="项目级开快通道")
    pc.add_argument(
        "--no-fast-mode", dest="fast_mode", action="store_false", help="项目级显式关快通道"
    )
    pc.set_defaults(func=cmd_project_create)
    ps = p_project_sub.add_parser("show", help="查看单个项目", parents=[common])
    ps.add_argument("--project", required=True, dest="project_id", help="项目 id")
    ps.set_defaults(func=cmd_project_show)

    p_chapter = sub.add_parser(
        "chapter", help="章节：list / status / plan / write / review / commit", parents=[common]
    )
    p_chapter_sub = p_chapter.add_subparsers(dest="subcommand", metavar="<sub>")
    chl = p_chapter_sub.add_parser("list", help="列出项目下的章节", parents=[common])
    chl.add_argument("--project", required=True, dest="project_id", help="项目 id")
    chl.set_defaults(func=cmd_chapter_list)
    chs = p_chapter_sub.add_parser("status", help="查看章节状态（章节详情）", parents=[common])
    _add_chapter_selector(chs, project_required=False)
    chs.set_defaults(func=cmd_chapter_status)
    for wf, helptext in (
        ("plan", "生成导演计划"),
        ("write", "写正文"),
        ("review", "审校"),
        ("commit", "提交（落 story state）"),
    ):
        wfp = p_chapter_sub.add_parser(wf, help=helptext, parents=[common])
        _add_chapter_selector(wfp)
        _add_workflow_body_flags(wfp)
        wfp.set_defaults(func=cmd_chapter_workflow(wf))

    p_run = sub.add_parser("run", help="运行：status / resume / approve", parents=[common])
    p_run_sub = p_run.add_subparsers(dest="subcommand", metavar="<sub>")
    rs = p_run_sub.add_parser("status", help="查看 run 详情", parents=[common])
    rs.add_argument("run_id", help="run id")
    _add_wait_flags(rs)
    rs.set_defaults(func=cmd_run_status)
    rr = p_run_sub.add_parser("resume", help="恢复 PAUSED 的 run", parents=[common])
    rr.add_argument("run_id", help="run id")
    rr.add_argument("--human-input", help="human_input 的 JSON 字符串")
    rr.add_argument("--auto-revise-max", type=int, help="自动改稿回路最大轮数（0~10）")
    rr.add_argument("--regenerate", action="store_true", help="重跑当前挂起节点")
    rr.add_argument("--author-intent", help="透传给改稿子 run 的作者铁律")
    rr.add_argument("--target-word-count", type=int, help="透传给改稿子 run 的目标字数")
    _add_wait_flags(rr)
    rr.set_defaults(func=cmd_run_resume)
    ra = p_run_sub.add_parser("approve", help="人工决议（通过 / 驳回 / 驳回并改稿）", parents=[common])
    ra.add_argument("run_id", help="run id")
    ra.add_argument("--reject", action="store_true", help="驳回（默认是批准）")
    ra.add_argument("--revise", action="store_true", help="驳回并要求改稿（配合 --reject）")
    ra.add_argument("--note", help="决议附注")
    ra.add_argument("--auto-revise-max", type=int, help="自动改稿回路最大轮数")
    _add_wait_flags(ra)
    ra.set_defaults(func=cmd_run_approve)

    p_state = sub.add_parser("state", help="Story State：show / diff", parents=[common])
    p_state_sub = p_state.add_subparsers(dest="subcommand", metavar="<sub>")
    st = p_state_sub.add_parser("show", help="当前快照（或指定版本）", parents=[common])
    st.add_argument("--project", required=True, dest="project_id", help="项目 id")
    st.add_argument("--version", type=int, help="快照版本号（不给则取当前）")
    st.add_argument("--branch", dest="branch_id", help="分支 id")
    st.set_defaults(func=cmd_state_show)
    sd = p_state_sub.add_parser("diff", help="两个版本之间的结构化 diff", parents=[common])
    sd.add_argument("--project", required=True, dest="project_id", help="项目 id")
    sd.add_argument("--a", type=int, required=True, help="基准版本号")
    sd.add_argument("--b", type=int, required=True, help="对比版本号")
    sd.add_argument("--branch", dest="branch_id", help="分支 id")
    sd.set_defaults(func=cmd_state_diff)

    p_quality = sub.add_parser("quality", help="质量：report / gate-override", parents=[common])
    p_quality_sub = p_quality.add_subparsers(dest="subcommand", metavar="<sub>")
    qr = p_quality_sub.add_parser("report", help="读质量报告", parents=[common])
    qr.add_argument("--chapter", dest="chapter_id", help="章节 id")
    qr.add_argument("--project", dest="project_id", help="项目 id（列全项目报告）")
    qr.add_argument("--limit", type=int, default=50, help="项目报告条数上限（默认 50）")
    qr.set_defaults(func=cmd_quality_report)
    qg = p_quality_sub.add_parser("gate-override", help="生成 gate_override 对象（不提交）", parents=[common])
    qg.add_argument("--rule-id", action="append", help="要显式接受的 confirm rule_id（可重复）")
    qg.add_argument("--reason", help="接受理由（服务端要求非空）")
    qg.set_defaults(func=cmd_quality_gate_override)

    p_delivery = sub.add_parser("delivery", help="交付：verdict / signing-check", parents=[common])
    p_delivery_sub = p_delivery.add_subparsers(dest="subcommand", metavar="<sub>")
    dv = p_delivery_sub.add_parser("verdict", help="逐章交付判定 + 项目汇总", parents=[common])
    dv.add_argument("--project", required=True, dest="project_id", help="项目 id")
    dv.set_defaults(func=cmd_delivery_verdict)
    dsc = p_delivery_sub.add_parser("signing-check", help="签约体检", parents=[common])
    dsc.add_argument("--project", required=True, dest="project_id", help="项目 id")
    dsc.set_defaults(func=cmd_delivery_signing_check)

    p_export = sub.add_parser("export", help="导出：txt / docx / 投稿包", parents=[common])
    p_export.add_argument("--kind", required=True, choices=sorted(EXPORT_KIND_TO_FORMAT), help="导出形态")
    p_export.add_argument("--project", required=True, dest="project_id", help="项目 id")
    p_export.add_argument("--chapter-no", type=int, help="只导某一章（不给则整书）")
    p_export.add_argument("--out", help="输出文件路径（默认写当前目录）")
    p_export.set_defaults(func=cmd_export)

    p_help = sub.add_parser("help", help="打印本帮助", parents=[common])
    p_help.set_defaults(func=None, help_only=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "help_only", False):
        parser.print_help()
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    if not getattr(args, "func", None):
        # 走到了某个分组但没给子命令（如 `chapter` 单独给）
        parser.parse_args([args.command, "--help"])
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except CliError as exc:
        payload = {"ok": False, "error": exc.message}
        if exc.hint:
            payload["hint"] = exc.hint
        sys.stderr.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write('{"ok": false, "error": "interrupted"}\n')
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
