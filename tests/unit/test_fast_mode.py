"""快通道开关的单点解析与 ctx 注入（2026-10-14，配套迁移 0031 ``projects.fast_mode``）。

覆盖三层，每层都**刻意挑可证伪的断言**：

1. ``resolve_fast_mode`` 的四档优先级（请求体 > 项目列 > env > 默认），
   含「NULL 未设置 ≠ 0 显式关」这条三态语义；
2. ``_start_workflow_core`` 的 ctx 注入——fast 为真时写哪三个键、
   以及**注入顺序压过请求体的 quality_gate_mode="enforce"**；
3. 逃生舱：请求体显式 ``fast_mode=False`` 能压掉项目级开启。

突变验证记录（AGENTS §三.2 硬要求）：

- 撤掉 ``initial_ctx["quality_gate_mode"] = "report"`` → ``test_fast_mode_overrides_request_body_enforce``
  红（断言 body 的 enforce 被压成 report）。
- 撤掉 ``initial_ctx["human_input"]`` → ``test_fast_mode_true_injects_three_ctx_keys`` 红
  （**注**：此处原写的是一个不存在的用例名 ``test_fast_mode_pre_approves_high_risk_gate``，
  2026-10-15 审查订正——真实捕获者就是上面那条「三个 ctx 键齐全」的用例）。
  ⚠️ 但这条单测只证明 **ctx 里有这个键**，**不证明它真的放行了闸门**：
  端到端的放行判别见 ``tests/api/test_fast_mode_review_gate.py`` 的 HIGH 风险正反一对。
- 撤掉 ``initial_ctx["fast_mode"]`` → ``test_fast_mode_true_injects_three_ctx_keys`` 红。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.core.api.routers.workflows.common import StartWorkflowRequest
from packages.core.api.routers.workflows.control import _start_workflow_core
from packages.core.api.routers.workflows.fast_mode import (
    ENV_FAST_MODE,
    read_project_fast_mode,
    resolve_fast_mode,
)
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.domain.chapter.models import ChapterCreate
from packages.domain.chapter.service import ChapterService
from packages.domain.project.models import ProjectCreate
from packages.domain.project.service import ProjectService

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _project(db_path: str, name: str = "fm project") -> str:
    return ProjectService(db_path).create(ProjectCreate(name=name))["project_id"]


def _chapter(db_path: str, project_id: str) -> str:
    return ChapterService(db_path).create(
        project_id, ChapterCreate(number=1, title="fm chapter")
    )["chapter_id"]


def _set_project_fast_mode(db_path: str, project_id: str, value: int | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET fast_mode = ? WHERE project_id = ?", (value, project_id)
        )
        conn.commit()
    finally:
        conn.close()


class _CapturingEngine:
    """只截获 ``start_with_nodes_async`` 的 ctx，不真的跑节点。

    本组用例断言的是「ctx 被组装成什么样」，不是「工作流跑出什么」——
    后者归 ``tests/api/test_fast_commit_e2e.py``。用捕获而不是真跑，
    是为了让「注入顺序压过请求体 enforce」这类纯组装逻辑可被单测钉住。
    """

    def __init__(self) -> None:
        self.initial_ctx: dict[str, Any] | None = None

    def start_with_nodes_async(self, _name: str, _nodes, **kwargs) -> str:
        self.initial_ctx = kwargs.get("initial_ctx")
        return "wfr_captured"


def _start_capture(db_path: str, project_id: str, chapter_id: str, body) -> dict[str, Any]:
    engine = _CapturingEngine()
    _start_workflow_core(
        workflow_name="chapter-commit",
        engine=engine,  # type: ignore[arg-type]
        db_path=db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )
    assert engine.initial_ctx is not None, "engine 没收到底 ctx"
    return engine.initial_ctx


# ---------------------------------------------------------------------------
# 1. resolve_fast_mode 四档优先级
# ---------------------------------------------------------------------------


def test_default_is_off(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    assert resolve_fast_mode(db_path=db, project_id=pid, request_value=None) is False


def test_project_column_enables(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    assert read_project_fast_mode(db, pid) is True
    assert resolve_fast_mode(db_path=db, project_id=pid, request_value=None) is True


def test_project_column_explicit_zero_is_not_the_same_as_unset(tmp_path: Path, monkeypatch):
    """**三态语义的核心断言**：NULL（未设置）与 0（显式关）必须可区分。

    若 ``_coerce_fast_mode`` 把 0 也归一成 None，两者就会退化成同一个值，
    「作者明确关的」与「作者从没这回事」再也分不开。
    """
    monkeypatch.setenv(ENV_FAST_MODE, "1")  # env 开着
    db = _db(tmp_path)
    pid_off = _project(db, "off")
    pid_unset = _project(db, "unset")
    _set_project_fast_mode(db, pid_off, 0)  # 显式关
    # pid_unset 不动 → NULL

    assert read_project_fast_mode(db, pid_off) is False
    assert read_project_fast_mode(db, pid_unset) is None
    # 显式关的项目：项目列优先于 env → 关
    assert resolve_fast_mode(db_path=db, project_id=pid_off, request_value=None) is False
    # 未设置的项目：项目列是 NULL（非 False）→ 回落 env → 开
    assert resolve_fast_mode(db_path=db, project_id=pid_unset, request_value=None) is True


def test_request_body_beats_project_column_both_ways(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid_on = _project(db, "on")
    pid_off = _project(db, "off")
    _set_project_fast_mode(db, pid_on, 1)
    _set_project_fast_mode(db, pid_off, 0)
    # 请求体 false 压掉项目级开启（逃生舱）
    assert resolve_fast_mode(db_path=db, project_id=pid_on, request_value=False) is False
    # 请求体 true 压掉项目级关闭（单章临时开）
    assert resolve_fast_mode(db_path=db, project_id=pid_off, request_value=True) is True


def test_env_applies_only_when_project_unset(tmp_path: Path, monkeypatch):
    db = _db(tmp_path)
    pid_set = _project(db, "set")
    pid_unset = _project(db, "unset")
    _set_project_fast_mode(db, pid_set, 0)

    monkeypatch.setenv(ENV_FAST_MODE, "true")
    assert resolve_fast_mode(db_path=db, project_id=pid_unset, request_value=None) is True
    assert resolve_fast_mode(db_path=db, project_id=pid_set, request_value=None) is False

    monkeypatch.setenv(ENV_FAST_MODE, "off")
    assert resolve_fast_mode(db_path=db, project_id=pid_unset, request_value=None) is False


def test_env_garbage_is_ignored_not_guessed(tmp_path: Path, monkeypatch):
    """非法 env 值 → 回落默认关，**不猜**。猜错方向会让项目在作者不知情下开闸。"""
    monkeypatch.setenv(ENV_FAST_MODE, "yes-please-maybe")
    db = _db(tmp_path)
    pid = _project(db)
    assert resolve_fast_mode(db_path=db, project_id=pid, request_value=None) is False


def test_missing_project_row_falls_back(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    assert resolve_fast_mode(
        db_path=db, project_id="prj_does_not_exist", request_value=None
    ) is False


# ---------------------------------------------------------------------------
# 2. ctx 注入
# ---------------------------------------------------------------------------


def test_fast_off_leaves_ctx_clean(tmp_path: Path, monkeypatch):
    """快通道关闭时**三个键都不出现**——不是塞 False，是不塞。

    与既有字段（author_intent 等）同款「非 None 才写 ctx」纪律：
    下游按「键不存在」判定未指定，塞 False 会被误读成「作者显式要求关闭」。
    """
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    cid = _chapter(db, pid)
    ctx = _start_capture(db, pid, cid, StartWorkflowRequest())
    assert "fast_mode" not in ctx
    assert "quality_gate_mode" not in ctx
    assert "human_input" not in ctx


def test_fast_mode_true_injects_three_ctx_keys(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    cid = _chapter(db, pid)
    ctx = _start_capture(db, pid, cid, StartWorkflowRequest())
    assert ctx["fast_mode"] is True
    assert ctx["quality_gate_mode"] == "report"
    assert ctx["human_input"] == {"approved": True, "approver": "system:fast_mode"}


def test_fast_mode_overrides_request_body_enforce(tmp_path: Path, monkeypatch):
    """**注入顺序**：fast 为真时必须压过请求体 quality_gate_mode="enforce"。

    判别力：若把 fast 注入块移到请求体字段注入**之前**，请求体的 enforce 会
    覆盖掉 report，本用例必红。
    """
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    cid = _chapter(db, pid)
    ctx = _start_capture(
        db, pid, cid, StartWorkflowRequest(quality_gate_mode="enforce")
    )
    assert ctx["fast_mode"] is True
    assert ctx["quality_gate_mode"] == "report", "fast 未压过请求体 enforce"


def test_request_body_false_is_escape_hatch_from_project_fast(tmp_path: Path, monkeypatch):
    """项目级开着时，请求体显式 false 能让本次运行走常规闸门。"""
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    cid = _chapter(db, pid)
    ctx = _start_capture(db, pid, cid, StartWorkflowRequest(fast_mode=False))
    assert "fast_mode" not in ctx
    assert "human_input" not in ctx


def test_fast_mode_true_keeps_body_fields(tmp_path: Path, monkeypatch):
    """开快通道**不得**吞掉请求体的其它字段（铁律 9：端点收下的参数要有消费方）。"""
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    cid = _chapter(db, pid)
    ctx = _start_capture(
        db,
        pid,
        cid,
        StartWorkflowRequest(
            author_intent="铁律：禁写番外",
            target_word_count=2500,
            fresh_write=True,
            model_overrides={"creative_writing": "prof_x"},
        ),
    )
    assert ctx["fast_mode"] is True
    assert ctx["author_intent"] == "铁律：禁写番外"
    assert ctx["target_word_count"] == 2500
    assert ctx["fresh_write"] is True
    assert ctx["model_overrides"] == {"creative_writing": "prof_x"}


def test_approver_is_not_human(tmp_path: Path, monkeypatch):
    """审批人必须是 ``system:fast_mode``。

    commit 节点把它写进 ``commits.approver``；写成 "human" 等于在审计表里
    留一条**没人做过的审批记录**。这条断言就是那道看守。
    """
    monkeypatch.delenv(ENV_FAST_MODE, raising=False)
    db = _db(tmp_path)
    pid = _project(db)
    _set_project_fast_mode(db, pid, 1)
    cid = _chapter(db, pid)
    ctx = _start_capture(db, pid, cid, StartWorkflowRequest())
    assert ctx["human_input"]["approver"] != "human"
    assert ctx["human_input"]["approver"] == "system:fast_mode"
