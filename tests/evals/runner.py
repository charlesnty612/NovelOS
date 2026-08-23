"""Regression Eval Runner（Sprint 4-B）。

按 ``docs/evaluation/quality-scoring-v0.md`` §6 的 MVP 收窄决策，本 Runner 落地
回归评测的最小骨架：跑通 ``golden`` 数据集中每个 case 的端到端工作流
（plan → write → review(approve) → commit(auto-approve HIGH)），并按
``expected.json`` 逐项断言 MVP 阶段的「阻断级」检查项：

- 流程完整性：四个 workflow 全部 ``COMPLETED``，``chapter.status == COMMITTED``；
- State 引擎递增：``state_version >= expected.state_version_min``（genesis v1 + 1 commit ≥ 2）；
- Observer 业务载荷：``observer_payload`` 中 ``delta_arrays_nonempty`` 数组全部非空；
- 快照物化：``story_states.snapshot_json`` 含 ``snapshot_contains.hooks_named`` 列出的 hook 名；
- Guardrail 阻断：MVP 阻断级（schema_validity / character_contradiction /
  world_rule_contradiction 三条）+ REQ-Q6/Q8（合规）—— Runner 仅做 schema_validity
  阻断级判定（其余两条属 Sprint 6 Quality 子模块）。``expected.no_guardrail_block`` 为 true 时，
  Runner 校验 ``commits.validation_json.guardrail_results`` 中 schema_validity 状态非 fail；
  ``observer_payload`` 通过 schema 校验器（任何必填字段缺失即视为 schema 阻断）。

Runner **不**打分、不调 LLM judge；Sprint 6 引入 Quality 子模块后在此扩展。

执行模型（与 S4-A 接口对齐）：
- 每个 case 一个临时 SQLite（``tmp_path / "novelos.db"``），``apply_migrations`` 起库；
- 通过 :class:`packages.core.workflow_runtime.engine.WorkflowEngine` 直接驱动四条流水线
  （``chapter-plan`` / ``chapter-write`` / ``chapter-review`` / ``chapter-commit``）；
- ``mock_providers`` 透传给 ``engine.start_with_nodes`` —— S4-A 已支持；
- ``author_review`` / ``high_risk_approval`` 自动 approve（``resume(human_input={"approved": True})``）。

异常处理：
- 任意 workflow run 抛异常 / 状态非预期 → RunResult.all_pass=False；
- 期望断言任一失败 → RunResult.all_pass=False；
- 不会重试（回归 Runner 要的是「发现失败」而非「绕过失败」）。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.core.db import apply_migrations, get_connection
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.workflows import get_workflow

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """单条断言结果。"""

    check: str
    pass_: bool = field(default=False)
    detail: str = ""

    @property
    def passed(self) -> bool:
        """外部断言访问属性统一为 ``passed``（避开 ``pass`` Python 关键字）。"""
        return self.pass_


@dataclass
class RunResult:
    """单个 case 的运行结果。

    除 ``checks`` 外，还暴露四个「基线签名」观察字段（quality-scoring-v0 §6
    Regression 基线判定的数据面映射，由 ``run_case`` 填充；断言失败时取实际值，
    供 ``tests/evals/regression_baseline.py`` 与基线比对）：
    - ``state_version``：最新 ``story_states.state_version``（int）；
    - ``delta_arrays``：observer 业务载荷中非空且为 list 的数组名（sorted list）；
    - ``hooks``：最新快照 ``snapshot_json.hooks[].name``（sorted list）；
    - ``guardrails_pass``：本 case 是否触发 MVP 阻断级 Guardrail
      （schema_validity fail / observer 7 数组结构缺失）。
    """

    case_name: str
    passed: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None
    traceback: str | None = None
    project_id: str | None = None
    chapter_id: str | None = None
    db_path: str | None = None
    state_version: int = 0
    delta_arrays: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    guardrails_pass: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _build_mock_providers(
    mocks: dict[str, list[dict[str, Any]]],
    chapter_id: str,
) -> dict[str, list[str]]:
    """把 mocks.json 的 dict 列表转为 ``engine.start_with_nodes`` 期望的 str 列表。

    同步把所有 ``evidence.chapter_id`` 字段重写为实际 ``chapter_id``（与 State Delta
    Validator 的 ``[business] evidence.chapter_id == chapter_id`` 约束对齐；详见
    ``packages/core/story_state/validator.py``）。
    AgentRunner 内部用 :class:`MockProvider` 把每个 str 元素当作一次 ``completion`` 的
    raw 文本；:func:`packages.core.agent_runtime.structured_output.extract_json` 会自动
    去围栏 + 截取首个 JSON 对象。
    """
    out: dict[str, list[str]] = {}
    for agent_name, responses in mocks.items():
        rewritten: list[str] = []
        for resp in responses:
            rewritten.append(_dump_json(_rewrite_evidence_chapter_id(resp, chapter_id)))
        out[agent_name] = rewritten
    return out


def _rewrite_evidence_chapter_id(obj: Any, chapter_id: str) -> Any:
    """递归把任意 dict 内的 ``evidence.chapter_id`` 替换为实际 chapter_id。

    适用于 observer 输出的 character_changes / world_changes / new_events / new_hooks 等
    含 evidence 的条目；其他字段不动。
    """
    if isinstance(obj, dict):
        new: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "evidence" and isinstance(v, dict) and "chapter_id" in v:
                ev = dict(v)
                ev["chapter_id"] = chapter_id
                new[k] = ev
            else:
                new[k] = _rewrite_evidence_chapter_id(v, chapter_id)
        return new
    if isinstance(obj, list):
        return [_rewrite_evidence_chapter_id(x, chapter_id) for x in obj]
    return obj


def _resolve_migrations_dir() -> Path:
    """定位 ``database/migrations``；从仓库根向上回退查找。"""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "database" / "migrations",  # repo_root / database / migrations
        here.parents[1] / "database" / "migrations",
        Path.cwd() / "database" / "migrations",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "database/migrations not found; tried: "
        + ", ".join(str(p) for p in candidates)
    )


# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_db(tmp_dir: Path) -> Path:
    """起一个临时 db 路径；apply_migrations 落地 DDL；返回 db_path。"""
    db_path = tmp_dir / "novelos.db"
    apply_migrations(db_path, migrations_dir=_resolve_migrations_dir())
    return db_path


def _force_wal_checkpoint(db_path: Path) -> None:
    """WAL 模式下，临时目录 cleanup 时 .db-wal/.db-shm 可能仍被持有 → PermissionError。

    Runner 在 tmp dir 销毁前显式 ``PRAGMA wal_checkpoint(TRUNCATE)``，让 SQLite 把
    WAL 内容刷回主文件并清空 sidecar 文件，避免 Windows 文件锁导致的 cleanup 报错。
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error:
        # 兜底：checkpoint 失败不影响 runner 结果
        pass


def _create_project(db_path: Path, project: dict[str, Any]) -> str:
    """建项目 → 返回 project_id。"""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, created_at, updated_at) "
            "VALUES (lower(hex(randomblob(12))), ?, ?, ?, ?, ?, ?)",
            (
                project["name"],
                project.get("premise"),
                project.get("genre"),
                project.get("target_words"),
                _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT project_id FROM projects WHERE rowid = ?", (cur.lastrowid,)
        ).fetchone()
        return row["project_id"]
    finally:
        conn.close()


def _slugify(name: str) -> str:
    """把人类可读名转成稳定 ID 片段。

    策略：保留字母数字下划线 + CJK 字符；其余字符替换为 ``_``；strip 前后 ``_``；
    空字符串兜底为 ``"x"``。允许 CJK 进入 ID 便于 golden mock 直接引用
    （如 ``char_林轩_0``），同时规避引号 / 空格等破坏 SQL/JSON 的字符。
    """
    import re

    s = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_").lower()
    return s or "x"


def _create_character(
    db_path: Path,
    project_id: str,
    character: dict[str, Any],
    *,
    index: int,
) -> str:
    """建角色（带 core_json + state v1 空行）。

    使用 ``char_<slug>_<index>`` 作为 character_id，便于 golden mocks 中 observer 引用
    真实存在的 ID（如 ``char_lin_xuan_0``）；slug 不可用时退回 hex。
    """
    slug = _slugify(character.get("name") or "")
    char_id = f"char_{slug}_{index}" if slug else f"char_{index:04d}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (
                character_id, project_id, name, role, core_json,
                visibility, who_knows, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'VISIBLE', NULL, ?, ?)
            """,
            (
                char_id,
                project_id,
                character["name"],
                character.get("role", "supporting"),
                _dump_json(character.get("core") or {}),
                _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
        conn.execute(
            """
            INSERT INTO character_states (
                character_id, state_version, state_json, visibility, who_knows, created_at
            ) VALUES (?, 1, '{}', 'VISIBLE', NULL, ?)
            """,
            (char_id, _now_iso()),
        )
        conn.commit()
        return char_id
    finally:
        conn.close()


def _create_chapter(db_path: Path, project_id: str, chapter: dict[str, Any]) -> str:
    """建章节；返回 chapter_id。

    使用 ``ch_<slug>`` 作为 chapter_id，便于 golden mocks 中 observer 引用真实存在的
    ID（如 ``ch_001_basic``）；slug 不可用时退回 hex。
    """
    slug = _slugify(str(chapter.get("title") or chapter.get("number") or ""))
    chap_id = f"ch_{slug}" if slug else "ch_unnamed"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters (
                chapter_id, project_id, number, title, status, plan_json,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, 'PLANNED', '{}', ?, ?
            )
            """,
            (
                chap_id,
                project_id,
                chapter["number"],
                chapter.get("title"),
                _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
        return chap_id
    finally:
        conn.close()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Workflow runners
# ---------------------------------------------------------------------------


def _sync_prompts(db_path: Path) -> None:
    """Runner 启动工作流前必须同步 prompts（与 API 端 ``/api/agents/sync`` 等价）。

    ``run_agent`` 内部会调 :meth:`PromptRegistry.get_active_prompt`，无 ACTIVE prompt
    即抛 :class:`PromptNotFoundError`，mock_script 路径也会走到该检查。
    """
    from packages.core.agent_runtime import PromptRegistry

    docs_dir = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"
    PromptRegistry(str(db_path)).sync_from_docs(docs_dir)


def _init_genesis_if_needed(db_path: Path, project_id: str, chapter_id: str) -> None:
    """对齐 :func:`packages.core.api.routers.workflows._init_genesis_if_needed`：
    chapter-write / review / commit 依赖 story_states；无快照则 init_genesis。
    """
    from packages.core.story_state.service import StoryStateService

    svc = StoryStateService(str(db_path))
    sv = int((svc.get_current_state(project_id) or {}).get("state_version") or 0)
    if sv < 1:
        svc.init_genesis(project_id, chapter_id)


def _run_workflow_with_resume(
    engine: WorkflowEngine,
    db_path: Path,
    workflow_name: str,
    chapter_id: str,
    project_id: str,
    mock_providers: dict[str, list[str]],
    *,
    initial_ctx_extra: dict[str, Any] | None = None,
    auto_approve_human: bool = True,
) -> str:
    """启动一个 workflow；遇 PAUSED 自动 approve resume（最多 5 次循环防呆）。

    返回最终 run_id。
    """
    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise ValueError(f"workflow {workflow_name!r} not registered")
    nodes = workflow["nodes"]

    # write / review / commit 依赖 story_states；按 API 端等价口径调 init_genesis
    if workflow_name in ("chapter-write", "chapter-review", "chapter-commit"):
        _init_genesis_if_needed(db_path, project_id, chapter_id)

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "chapter_id": chapter_id,
        "project_id": project_id,
    }
    if initial_ctx_extra:
        initial_ctx.update(initial_ctx_extra)

    run_id = engine.start_with_nodes(
        workflow_name,
        nodes,
        chapter_id=chapter_id,
        initial_ctx=initial_ctx,
        mock_providers=mock_providers,
    )
    if not auto_approve_human:
        return run_id

    # 循环 resume 直到非 PAUSED（防御性兜底：连续 5 次 PAUSED 视为异常）
    for _ in range(5):
        run = get_run(db_path, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id!r} disappeared after start")
        if run["status"] != "PAUSED":
            return run_id
        # 自动 approve
        engine.resume(run_id, nodes, human_input={"approved": True})
    raise RuntimeError(f"workflow {workflow_name!r} looped PAUSED > 5 times; abort")


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _collect_observer_payload(db_path: Path, chapter_id: str) -> dict[str, Any] | None:
    """从 chapter-commit 的 ai_call_logs 抽 observer 7 数组（业务载荷）。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT output_json FROM ai_call_logs
            WHERE agent_id = (SELECT agent_id FROM agents WHERE name = 'observer')
              AND run_id IN (
                  SELECT run_id FROM workflow_runs
                  WHERE workflow_id = (SELECT workflow_id FROM workflows WHERE name = 'chapter-commit')
                    AND chapter_id = ?
              )
            ORDER BY created_at ASC
            """,
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        payload = _parse_json(r["output_json"])
        if isinstance(payload, dict):
            return payload
    return None


def _check_final_chapter_status(
    db_path: Path, chapter_id: str, expected_status: str
) -> CheckResult:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    actual = row["status"] if row else "<missing>"
    return CheckResult(
        check=f"chapter.status=={expected_status}",
        pass_=actual == expected_status,
        detail=f"actual={actual}",
    )


def _check_state_version(
    db_path: Path, project_id: str, min_version: int
) -> CheckResult:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS v FROM story_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    actual = int(row["v"] or 0)
    return CheckResult(
        check=f"state_version>={min_version}",
        pass_=actual >= min_version,
        detail=f"actual={actual}",
    )


def _check_delta_arrays_nonempty(
    observer_payload: dict[str, Any] | None,
    array_names: list[str],
) -> tuple[CheckResult, list[str]]:
    """断言 observer 业务载荷中指定数组全部非空；同时返回实际非空数组名列表。

    返回值第二项供 Regression 基线比对（实际非空数组名集合变化即结构回归）。
    """
    actual: list[str] = []
    if observer_payload is not None:
        for name in array_names:
            arr = observer_payload.get(name)
            if isinstance(arr, list) and len(arr) > 0:
                actual.append(name)
    missing_or_empty = [n for n in array_names if n not in actual]
    return (
        CheckResult(
            check=f"observer.delta_arrays_nonempty=={array_names}",
            pass_=not missing_or_empty,
            detail=(
                "all non-empty"
                if not missing_or_empty
                else f"empty/missing arrays: {missing_or_empty}"
            ),
        ),
        sorted(actual),
    )


def _query_latest_state_version(db_path: Path, project_id: str) -> int:
    """查询最新 ``story_states.state_version``（int，无快照为 0）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS v FROM story_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["v"] or 0)


def _query_snapshot_hook_names(db_path: Path, project_id: str) -> list[str]:
    """查询最新快照 ``snapshot_json.hooks[].name``（sorted list，供基线比对）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM story_states WHERE project_id = ? "
            "ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    snap = _parse_json(row["snapshot_json"]) if row else None
    hooks = snap.get("hooks") if isinstance(snap, dict) else None
    names = []
    for h in hooks or []:
        if isinstance(h, dict) and isinstance(h.get("name"), str):
            names.append(h["name"])
    return sorted(names)


def _check_snapshot_hooks_named(
    db_path: Path, project_id: str, expected_names: list[str]
) -> CheckResult:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM story_states WHERE project_id = ? "
            "ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    snap = _parse_json(row["snapshot_json"]) if row else None
    if not isinstance(snap, dict):
        return CheckResult(
            check=f"snapshot.hooks contains {expected_names}",
            pass_=False,
            detail="snapshot missing or not a dict",
        )
    hooks = snap.get("hooks") or []
    actual_names: list[str] = []
    for h in hooks:
        if isinstance(h, dict) and isinstance(h.get("name"), str):
            actual_names.append(h["name"])
    missing = [n for n in expected_names if n not in actual_names]
    return CheckResult(
        check=f"snapshot.hooks contains {expected_names}",
        pass_=not missing,
        detail=(
            f"actual_names={actual_names}"
            if not missing
            else f"missing={missing}; actual_names={actual_names}"
        ),
    )


def _check_no_guardrail_block(
    db_path: Path, project_id: str, observer_payload: dict[str, Any] | None
) -> CheckResult:
    """MVP 阶段 Runner 仅做 schema_validity 阻断级判定（其余属 Sprint 6）。

    判定路径：
    1. 校验 observer 业务载荷符合 7 数组契约（顶层键齐全且为 list）；
       不符合 → 视为 schema 阻断 → fail。
    2. 从 commits 表读最新 ``validation_json.guardrail_results``，断言
       schema_validity 状态非 fail。
    """
    if observer_payload is None:
        return CheckResult(
            check="no_guardrail_block",
            pass_=False,
            detail="observer_payload missing → schema 阻断",
        )
    # 1) observer 7 数组结构校验（与 S3 _validate_observer 对齐）
    required = {
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    }
    keys = set(observer_payload.keys())
    missing = required - keys
    if missing:
        return CheckResult(
            check="no_guardrail_block",
            pass_=False,
            detail=f"observer schema 阻断：missing arrays {sorted(missing)}",
        )
    for k in required:
        if not isinstance(observer_payload[k], list):
            return CheckResult(
                check="no_guardrail_block",
                pass_=False,
                detail=f"observer schema 阻断：{k!r} must be list, got {type(observer_payload[k]).__name__}",
            )
    # 2) commits.guardrail_results
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT validation_json FROM commits WHERE project_id = ? "
            "ORDER BY resulting_state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return CheckResult(
            check="no_guardrail_block",
            pass_=False,
            detail="no commit row → 流程未跑完",
        )
    validation = _parse_json(row["validation_json"]) or {}
    results = validation.get("guardrail_results") or []
    failures = [
        r for r in results
        if r.get("name") == "schema_validity" and r.get("status") == "fail"
    ]
    return CheckResult(
        check="no_guardrail_block",
        pass_=not failures,
        detail=(
            "schema_validity=pass" if not failures
            else f"schema_validity=fail entries: {failures}"
        ),
    )


def _check_workflow_runs_completed(
    db_path: Path, chapter_id: str, expected_workflows: list[str]
) -> CheckResult:
    """所有 expected workflow run 全部 COMPLETED。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT wr.status, w.name FROM workflow_runs wr
            JOIN workflows w ON w.workflow_id = wr.workflow_id
            WHERE wr.chapter_id = ? AND w.name IN ({})
            """.format(",".join("?" for _ in expected_workflows)),
            (chapter_id, *expected_workflows),
        ).fetchall()
    finally:
        conn.close()
    by_name = {r["name"]: r["status"] for r in rows}
    bad = [
        f"{n}={by_name.get(n, '<missing>')}"
        for n in expected_workflows
        if by_name.get(n) != "COMPLETED"
    ]
    return CheckResult(
        check=f"workflow_runs completed: {expected_workflows}",
        pass_=not bad,
        detail="all COMPLETED" if not bad else f"non-completed: {bad}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_case(case_dir: Path | str) -> RunResult:
    """跑单个 golden case；返回 RunResult（带逐项断言明细）。

    异常处理：
    - 任何未捕获异常 → RunResult.error / traceback 填充，passed=False。
    - 缺文件 / JSON 非法 → 同上。
    """
    case_dir = Path(case_dir)
    case_name = case_dir.name
    result = RunResult(case_name=case_name, passed=True)

    try:
        # 1) 读取 fixture
        input_path = case_dir / "input.json"
        mocks_path = case_dir / "mocks.json"
        expected_path = case_dir / "expected.json"
        for p in (input_path, mocks_path, expected_path):
            if not p.exists():
                result.passed = False
                result.error = f"missing fixture: {p.name}"
                return result

        input_data = _parse_json(input_path.read_text(encoding="utf-8")) or {}
        mocks = _parse_json(mocks_path.read_text(encoding="utf-8")) or {}
        expected = _parse_json(expected_path.read_text(encoding="utf-8")) or {}

        # 2) 临时 db + 建项目/角色/章节
        # ``ignore_cleanup_errors=True`` 让 Windows WAL 文件锁偶发的 PermissionError 不冒到 stderr；
        # checkpoint WAL 已经把 WAL 内容刷回主文件，残留的 sidecar 文件下次 SQLite 打开会被复用。
        tmp_dir_obj = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_path = Path(tmp_dir_obj.name)
        db_path: Path | None = None
        try:
            db_path = _bootstrap_db(tmp_path)
            result.db_path = str(db_path)

            project_id = _create_project(db_path, input_data.get("project") or {})
            result.project_id = project_id
            for idx, ch in enumerate(input_data.get("characters") or []):
                _create_character(db_path, project_id, ch, index=idx)
            chapter_id = _create_chapter(
                db_path, project_id, input_data.get("chapter") or {}
            )
            result.chapter_id = chapter_id

            # 3) mock_providers 透传（dict → list[str]） + 同步 prompts（AI 节点需要 ACTIVE prompt）
            mock_providers = _build_mock_providers(mocks, chapter_id)
            _sync_prompts(db_path)

            # 4) 跑四条 workflow
            engine = WorkflowEngine(db_path)

            _run_workflow_with_resume(
                engine,
                db_path,
                "chapter-plan",
                chapter_id,
                project_id,
                mock_providers,
                initial_ctx_extra={
                    "author_intent": input_data.get("author_intent") or "",
                },
            )
            _run_workflow_with_resume(
                engine, db_path, "chapter-write", chapter_id, project_id, mock_providers
            )
            _run_workflow_with_resume(
                engine, db_path, "chapter-review", chapter_id, project_id, mock_providers
            )
            _run_workflow_with_resume(
                engine, db_path, "chapter-commit", chapter_id, project_id, mock_providers
            )

            # 5) 断言
            expected_status = expected.get("final_chapter_status") or "COMMITTED"
            result.checks.append(
                _check_workflow_runs_completed(
                    db_path,
                    chapter_id,
                    ["chapter-plan", "chapter-write", "chapter-review", "chapter-commit"],
                )
            )
            result.checks.append(
                _check_final_chapter_status(db_path, chapter_id, expected_status)
            )
            min_state_v = int(expected.get("state_version_min") or 2)
            state_version_check = _check_state_version(db_path, project_id, min_state_v)
            result.checks.append(state_version_check)
            result.state_version = _query_latest_state_version(db_path, project_id)
            observer_payload = _collect_observer_payload(db_path, chapter_id)
            delta_arrays = list(expected.get("delta_arrays_nonempty") or [])
            if delta_arrays:
                delta_check, actual_delta_arrays = _check_delta_arrays_nonempty(
                    observer_payload, delta_arrays
                )
                result.checks.append(delta_check)
                result.delta_arrays = actual_delta_arrays
            snap_contains = expected.get("snapshot_contains") or {}
            hook_names = list(snap_contains.get("hooks_named") or [])
            if hook_names:
                result.checks.append(
                    _check_snapshot_hooks_named(db_path, project_id, hook_names)
                )
            result.hooks = _query_snapshot_hook_names(db_path, project_id)
            if expected.get("no_guardrail_block"):
                guardrail_check = _check_no_guardrail_block(
                    db_path, project_id, observer_payload
                )
                result.checks.append(guardrail_check)
                result.guardrails_pass = guardrail_check.passed

            # 收尾：checkpoint WAL 让 tmp dir cleanup 不被 Windows 文件锁挡住
            _force_wal_checkpoint(db_path)
        finally:
            # 无论成功/异常，先 checkpoint WAL；再清理 tmp dir（PermissionError 容错）
            if db_path is not None:
                _force_wal_checkpoint(db_path)
            try:
                tmp_dir_obj.cleanup()
            except (PermissionError, OSError):
                # Windows WAL sidecar 文件锁偶发 cleanup 失败；不影响业务断言。
                # 业务失败（result.passed=False + checks FAIL）才是真正信号，cleanup
                # 噪声不写到 result.error 以免抢首行。
                pass

        # 6) 汇总
        result.passed = all(c.passed for c in result.checks)
        return result

    except BaseException as exc:  # noqa: BLE001
        result.passed = False
        result.error = f"{type(exc).__name__}: {exc}"
        result.traceback = traceback.format_exc()
        return result


def run_all(golden_dir: Path | str) -> tuple[int, list[RunResult]]:
    """遍历 golden_dir 下所有子目录（每个含 input.json）跑 :func:`run_case`。

    返回 ``(passed_count, results)``；``passed_count`` 为 passed=True 的 case 数。
    """
    golden_dir = Path(golden_dir)
    results: list[RunResult] = []
    if not golden_dir.exists():
        return 0, results
    case_dirs = sorted(
        d for d in golden_dir.iterdir()
        if d.is_dir() and (d / "input.json").exists()
    )
    for d in case_dirs:
        results.append(run_case(d))
    passed = sum(1 for r in results if r.passed)
    return passed, results


def discover_cases(golden_dir: Path | str) -> list[Path]:
    """返回 ``golden_dir`` 下含 ``input.json`` 的子目录列表（pytest 参数化用）。"""
    golden_dir = Path(golden_dir)
    if not golden_dir.exists():
        return []
    return sorted(
        d for d in golden_dir.iterdir()
        if d.is_dir() and (d / "input.json").exists()
    )


# 兼容 sqlite3 顶层异常类型别名（便于 caller 捕获）
_RUNNER_DB_ERRORS = (sqlite3.IntegrityError, sqlite3.OperationalError)

__all__ = [
    "CheckResult",
    "RunResult",
    "run_case",
    "run_all",
    "discover_cases",
]
