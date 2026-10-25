"""outline-batch workflow 测试（批量续规划下一批章节）。

零真实 LLM 调用：planner 节点全部走 mock_providers 脚本。

覆盖：
- 正常链：mock 合法 seeds ⇒ 章节表 +N、number 连续、status 全 PLANNED、
  plan_json 为空 plan（不写该列 ⇒ DDL DEFAULT '{}'——留给 chapter-plan 生成槽，
  write 的 load_plan 校验 chapter_goal，大纲层章须先跑计划）、
  outline_json = canonical 键映射结构（C-3，2026-10-24：chapter_goal ← synopsis
  原文，synopsis/title 原文并存）；
- **C-3 判别**：落库后 ``resolve_chapter_outline``（chapter.outline 段唯一来源）
  返回 ``chapter_goal == synopsis 原文``——撤 persist 侧映射该用例必红
  （outline_json 退回无 chapter_goal 键的形态）；
- mock 回断号 seeds ⇒ AgentOutputError（run FAILED），章节表零新增；
- mock 回数量不符 seeds ⇒ 同上；
- 已有 10 章时 start_number=11；
- **C-1/C-2 并发守卫**：同项目并发 start（project_level_guard）⇒ 恰 1 个成功
  或串行合法、章号零重复；守卫按 project_id 过滤 ⇒ 跨项目互不拦截；
- 注册看守：outline-batch 在工作流注册表中；未注册名返回 None（反向）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.context_engine.builders_common.common import resolve_chapter_outline
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime import engine as _engine_module
from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowRunConflict
from packages.core.workflow_runtime.runs import get_run
from packages.workflows.outline_batch import pipeline as _pipeline_module

from ._shared import _make_engine

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"


def _make_project(db_path, name: str = "批量续规划测试书") -> str:
    conn = get_connection(db_path)
    try:
        pid = new_id("prj")
        now = now_iso()
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, name, "少年得古籍，逆天改命", "玄幻", now, now),
        )
        conn.commit()
        return pid
    finally:
        conn.close()


def _make_chapter(db_path, project_id: str, number: int, title: str = "") -> str:
    conn = get_connection(db_path)
    try:
        cid = new_id("ch")
        now = now_iso()
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'COMMITTED', ?, ?)",
            (cid, project_id, number, title, now, now),
        )
        conn.commit()
        return cid
    finally:
        conn.close()


def _list_chapters(db_path, project_id: str) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT chapter_id, number, title, status, plan_json, outline_json "
            "FROM chapters WHERE project_id = ? ORDER BY number ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _synopsis(number: int) -> str:
    """第 number 章的 mock synopsis（脚本构造与断言共用同一份原文）。"""
    return (
        f"主角在旧城集市追查线索（第{number}章），撞上暗桩伏击，"
        "逼出幕后接引人的一角；情绪由疑惑转向警觉，"
        "同时推进铜哨伏笔的回收；章末停在接引人遁走、身份成谜的悬念上。"
    )


def _planner_script(start: int, count: int) -> list[str]:
    """合法 seeds 脚本：number 从 start 起连续 count 个。"""
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 集市暗桩",
            "synopsis": _synopsis(start + i),
        }
        for i in range(count)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _gapped_planner_script(start: int, count: int) -> list[str]:
    """断号 seeds 脚本：跳过 start+2 一章（数量仍够但 number 不连续）。"""
    seeds = [
        {
            "number": start + i if i != 2 else start + i + 1,
            "title": f"第{start + i}章 断号",
            "synopsis": "断号测试用梗概，足够长以通过非空校验，但 number 序列不连续。",
        }
        for i in range(count)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _short_count_planner_script(start: int, actual: int) -> list[str]:
    seeds = [
        {
            "number": start + i,
            "title": f"第{start + i}章 数量不足",
            "synopsis": "数量不足测试用梗概，足够长以通过非空校验。",
        }
        for i in range(actual)
    ]
    return [
        json.dumps(
            {
                "schema_version": 1,
                "prompt_version": "outline_batch_planner:v1",
                "chapter_seeds": seeds,
            },
            ensure_ascii=False,
        )
    ]


def _start_outline_batch(
    engine: WorkflowEngine,
    project_id: str,
    *,
    mock_providers: dict[str, list[str]] | None,
    chapter_count: int = 5,
) -> str:
    workflow = get_workflow("outline-batch")
    assert workflow is not None
    return engine.start_with_nodes(
        "outline-batch",
        workflow["nodes"],
        initial_ctx={
            "db_path": str(engine.db_path),
            "project_id": project_id,
            "chapter_count": chapter_count,
        },
        mock_providers=mock_providers,
    )


def test_outline_batch_end_to_end_creates_planned_chapters(tmp_path: Path):
    """正常链：空项目 mock 合法 seeds ⇒ +5 章、number 1..5、PLANNED、
    plan_json 空（不写）且大纲只存 outline_json。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _planner_script(1, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    chapters = _list_chapters(engine.db_path, pid)
    assert len(chapters) == 5
    assert [c["number"] for c in chapters] == [1, 2, 3, 4, 5]
    assert all(c["status"] == "PLANNED" for c in chapters)
    for c in chapters:
        # plan_json 不写 ⇒ INSERT 未列该列，DDL DEFAULT '{}' 生效 = 空 plan
        # （「计划未生成」；load_plan 读到它会正确报「run chapter-plan first」，
        # 不会再被伪 plan 骗过）。大纲内容只存 outline_json。
        assert json.loads(c["plan_json"]) == {}
        # outline_json = canonical 键映射结构（C-3）：chapter_goal ← synopsis 原文，
        # synopsis/title 原文并存供审计对回 seed。
        outline = json.loads(c["outline_json"])
        assert outline["chapter_goal"] == _synopsis(c["number"])
        assert outline["synopsis"] == _synopsis(c["number"])
        assert outline["title"] == c["title"]
    # checkpoint 带 created chapter_ids
    ckpt = run.get("checkpoint_json") or {}
    persist_out = ckpt.get("persist") or {}
    assert len(persist_out.get("created_chapter_ids") or []) == 5


def test_outline_batch_start_number_after_existing_chapters(tmp_path: Path):
    """已有 10 章（编号 1..10）时 start_number=11：新章 11..15，老章不动。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)
    for i in range(1, 11):
        _make_chapter(engine.db_path, pid, i, f"既有第{i}章")

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _planner_script(11, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    chapters = _list_chapters(engine.db_path, pid)
    assert len(chapters) == 15
    assert [c["number"] for c in chapters] == list(range(1, 16))
    new_rows = [c for c in chapters if c["number"] >= 11]
    assert all(c["status"] == "PLANNED" for c in new_rows)
    old_rows = [c for c in chapters if c["number"] <= 10]
    assert all(c["status"] == "COMMITTED" for c in old_rows)


def test_outline_batch_gapped_numbers_fail(tmp_path: Path):
    """mock 回断号 seeds ⇒ AgentOutputError（run FAILED），章节表零新增。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _gapped_planner_script(1, 5)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert "consecutive" in str(run.get("error") or "")
    assert _list_chapters(engine.db_path, pid) == []


def test_outline_batch_wrong_count_fail(tmp_path: Path):
    """mock 回数量不符 seeds（要 5 回 3）⇒ run FAILED，章节表零新增。"""
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine, pid, mock_providers={"outline_batch_planner": _short_count_planner_script(1, 3)}
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert "expected 5 chapter_seeds, got 3" in str(run.get("error") or "")
    assert _list_chapters(engine.db_path, pid) == []


def test_outline_batch_synopsis_reaches_planner_outline_as_chapter_goal(tmp_path: Path):
    """C-3 内容级判别：落库后 ``resolve_chapter_outline`` 返回
    ``chapter_goal == synopsis 原文``。

    ``resolve_chapter_outline`` 是 director 装配 ``chapter.outline`` 段的**唯一来源**
    （``builders_common/common.py``；``build_director_input`` 直接内联调用它），而
    planner「大纲优先」规则只绑定 ``outline.chapter_goal`` 等键——本用例断言的就是
    「批次大纲真的进了 planner 能看到的那份 payload」。

    **判别力**：撤掉 persist 侧映射（outline_json 改回 seed 原文
    ``{number,title,synopsis}``）⇒ 返回 dict 无 ``chapter_goal`` 键 ⇒ 本用例红。
    断言打在函数真实返回值上，不是落库 JSON 的形态复述。
    """
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)

    run_id = _start_outline_batch(
        engine,
        pid,
        mock_providers={"outline_batch_planner": _planner_script(1, 3)},
        chapter_count=3,
    )
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run

    conn = get_connection(engine.db_path)
    try:
        db_rows = conn.execute(
            "SELECT number, title, plan_json, outline_json FROM chapters "
            "WHERE project_id = ? ORDER BY number ASC",
            (pid,),
        ).fetchall()
    finally:
        conn.close()
    assert len(db_rows) == 3
    for row in db_rows:
        # 复刻 director 装配的读取路径：_row_to_chapter 同款两列解析 → resolve_chapter_outline
        chapter = {
            "plan_json": json.loads(row["plan_json"]) or {},
            "outline_json": json.loads(row["outline_json"]) or {},
        }
        outline = resolve_chapter_outline(chapter)
        assert outline["chapter_goal"] == _synopsis(int(row["number"])), (
            f"chapter {row['number']}: planner 可见的 outline.chapter_goal 必须是 "
            f"synopsis 原文（撤 persist 侧映射本断言必红）"
        )
        assert outline["title"] == row["title"]


class _FrozenRows:
    """冻结的查询结果（对齐用）：先取快照，再放行，调用方拿到的永远是同一份。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchmany(self, size: int = 1):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def __iter__(self):
        return iter(self._rows)


class _QueryAlignProxy:
    """把某个特定 SELECT 的**读取时刻**对齐到两线程会合点（并发窗口放大）。

    机制：代理 ``get_connection`` 返回的连接 ⇒ 在命中目标 SQL 时**先取结果快照**，
    再在 ``Barrier(2)`` 上会合（超时放行）⇒

    - 两个线程的该次读取**发生在同一逻辑时刻**（谁都看不到对方尚未落库的数据）；
    - 只有一个线程到达时（守卫生效 ⇒ 写锁把两次读取串行化）⇒ 会合超时放行，
      语义不变。

    ⚠️ **只读不改生产代码**：代理只在用例内经 ``monkeypatch`` 生效，
    ``packages/core/workflow_runtime/engine.py`` 与
    ``packages/workflows/outline_batch/pipeline.py`` 一字不动。
    """

    def __init__(self, match, timeout: float = 0.8):
        self._match = match
        self._timeout = timeout
        self.barrier = threading.Barrier(2)
        self.arrivals: list[str] = []
        self._local = threading.local()

    def wrap(self, conn):
        proxy = self

        class _ConnectionProxy:
            def __init__(self, real):
                object.__setattr__(self, "_real", real)

            def execute(self, sql, *args, **kwargs):
                real = object.__getattribute__(self, "_real")
                cur = real.execute(sql, *args, **kwargs)
                flat = " ".join(sql.split())
                if proxy._match(flat) and not getattr(proxy._local, "seen", False):
                    proxy._local.seen = True
                    proxy.arrivals.append(threading.current_thread().name)
                    rows = cur.fetchall()
                    try:
                        proxy.barrier.wait(timeout=proxy._timeout)
                    except threading.BrokenBarrierError:
                        pass
                    return _FrozenRows(rows)
                return cur

            def __getattr__(self, name):
                return getattr(object.__getattribute__(self, "_real"), name)

            def __setattr__(self, name, value):
                setattr(object.__getattribute__(self, "_real"), name, value)

        return _ConnectionProxy(conn)


def test_outline_batch_concurrent_starts_same_project_single_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """C-2 引擎级并发守卫：同项目两线程同时 start（project_level_guard=True）
    ⇒ 不产生重复章号（内容级断言）。

    - 若守卫生效（期望路径）：恰 1 个成功、1 个 :class:`WorkflowRunConflict`；
    - 若首 run 在后者守卫 SELECT 前已 COMPLETED（串行合法，窗口极小但理论存在）：
      两个都成功——此时章号必须连续不重复（第二次 start_number 顺延）；
    - **守卫失效的反事实**（无 BEGIN IMMEDIATE / 无 project_id 过滤）：两线程
      同时读到「无活跃 run」⇒ 两个 run 真并发 ⇒ 两种可观测症状，任一即本用例红：
      ① 两 run 并发写同一库撞 SQLite 单写者（``database is locked``）；
      ② 或各自从 start_number=1 插章 ⇒ chapters.number 重复
      （chapters 无 (project_id, number) 唯一索引，重复可发生）。

    ★ **加固（BUG-21 flake 子项 · 误绿 5/12 ≈ 42%）**：原实现有两个**检出缺口**，
    本批只补缺口、**不改断言语义**、**不新增判红规则**（合法窗口原样保留）：

    1. **mock 脚本原先按「谁先 append 到 results」选** ⇒ 两个线程在
       ``start_with_nodes`` 调用前**同时**求值（``results`` 都还是空的）⇒
       两线程常拿到同一支 ``(1, 2)``；而 pipeline 按库里 ``max(number)+1`` 算
       ``start_number`` ⇒ 另一支的契约校验炸（``expected [3, 4], got [1, 2]``）
       ⇒ **用例因契约失败而红，与守卫是否生效无关** ⇒ 绿灯不可信。
       改法：两线程统一用 ``[script(1,2), script(3,4)]``（runner 列表模式：第 1 次
       取 [0]、重试取 [1]）⇒ 无论算出哪个 ``start_number`` 都能命中 ⇒ 契约不再炸。
    2. **并发窗口放大**（两个对齐点，测试侧代理 ``get_connection``，不改生产代码）：
       ① **守卫 SELECT**（``engine.py:525-537``）⇒ 撤掉写锁时「两线程同时读到
       『无活跃 run』」**必然**发生（原先靠运气，12 次里只有 7 次撞上）；
       ② **章号读取**（``pipeline.py:96-102`` 的 chapters 查询）⇒ 两个并发 run
       算出的 ``start_number`` **必然相同** ⇒ 重复章号（守卫失效的既定症状）
       由**既有断言**（``len(numbers) == len(set(numbers))``）判红。
       守卫生效时写锁把两次守卫 SELECT 串行化 ⇒ 只有一个线程到达会合点 ⇒ 超时
       放行 ⇒ 原有语义不变。
    """
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid = _make_project(engine.db_path)
    workflow = get_workflow("outline-batch")
    assert workflow is not None

    # 对齐点①：守卫 SELECT（engine 模块的 get_connection）
    guard_align = _QueryAlignProxy(
        lambda flat: flat.startswith("SELECT")
        and "FROM workflow_runs" in flat
        and "wf.name = ?" in flat
    )
    monkeypatch.setattr(
        _engine_module,
        "get_connection",
        lambda db_path: guard_align.wrap(get_connection(db_path)),
    )
    # 对齐点②：章号读取（outline_batch pipeline 模块的 get_connection）
    number_align = _QueryAlignProxy(
        lambda flat: flat.startswith("SELECT")
        and "FROM chapters WHERE project_id = ?" in flat
        and "number" in flat
    )
    monkeypatch.setattr(
        _pipeline_module,
        "get_connection",
        lambda db_path: number_align.wrap(get_connection(db_path)),
    )

    barrier = threading.Barrier(2)
    results: list[str] = []
    conflicts: list[WorkflowRunConflict] = []
    errors: list[BaseException] = []
    # 两线程同一份脚本队列：runner 列表模式（第 1 次取 [0]、契约失败重试取 [1]）
    # ⇒ 无论 pipeline 算出 start_number=1 还是 3，总有一支命中 ⇒ 契约永不再炸。
    planner_scripts = [_planner_script(1, 2)[0], _planner_script(3, 2)[0]]

    def _start() -> None:
        barrier.wait()
        try:
            # 两个线程各自独立 mock_providers（脚本队列互不串扰）。
            run_id = engine.start_with_nodes(
                "outline-batch",
                workflow["nodes"],
                project_id=pid,
                project_level_guard=True,
                initial_ctx={
                    "db_path": str(engine.db_path),
                    "project_id": pid,
                    "chapter_count": 2,
                },
                mock_providers={"outline_batch_planner": list(planner_scripts)},
            )
            results.append(run_id)
        except WorkflowRunConflict as exc:  # 守卫生效路径
            conflicts.append(exc)
        except Exception as exc:
            # ★ 守卫失效时两 run 真并发 ⇒ SQLite 单写者 ⇒ 另一线程可能拿到
            #   ``database is locked``。**捕获并记下来**（不吞）：若让它逃出线程，
            #   pytest 会记成 PytestUnhandledThreadExceptionWarning（带 Traceback）
            #   ⇒ 红段就不是「内容级 FAIL」而是「崩溃红」。这里转成断言失败。
            errors.append(exc)

    threads = [threading.Thread(target=_start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    # ★ 守卫失效的并发写冲突（不是「崩了」，是「守卫没兜住」）⇒ 判红。
    assert not errors, (
        f"并发期间出现未预期异常（守卫失效 ⇒ 两 run 真并发写同一库）："
        f"{[repr(e) for e in errors]}"
    )

    # 内容级硬断言：DB 里该项目的章号集合无重复（守卫存在的意义）。
    conn = get_connection(engine.db_path)
    try:
        numbers = [
            int(r["number"])
            for r in conn.execute(
                "SELECT number FROM chapters WHERE project_id = ? ORDER BY number",
                (pid,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(numbers) == len(set(numbers)), f"并发产生了重复章号: {numbers}"
    assert len(numbers) in (2, 4), f"成功的 run 数与章节数不符: runs={results}, 章号={numbers}"

    if len(results) == 1:
        # 守卫生效主路径：恰 1 成功 1 拒绝
        assert len(conflicts) == 1
        msg = str(conflicts[0])
        assert "already has an active outline-batch run" in msg
        assert pid in msg, f"守卫消息必须带项目归属: {msg}"
    else:
        # 串行合法兜底路径：两批次章号必须互不重叠（1..2 与 3..4）
        assert sorted(numbers) == [1, 2, 3, 4], numbers


def test_outline_batch_guard_is_scoped_to_project(tmp_path: Path):
    """C-1 引擎级判别：项目 A 有活跃 outline-batch run 时，项目 B 的 start
    **不被拦**（守卫按 project_id 过滤）。

    反事实：守卫无 project_id 过滤（旧口径）⇒ B 也 WorkflowRunConflict ⇒ 本用例红。
    """
    engine = _make_engine(tmp_path)
    PromptRegistry(engine.db_path).sync_from_docs(_PROMPTS_DIR)
    pid_a = _make_project(engine.db_path, name="守卫归属测试·A")
    pid_b = _make_project(engine.db_path, name="守卫归属测试·B")
    workflow = get_workflow("outline-batch")
    assert workflow is not None

    # A 起跑（mock 到 COMPLETED 之前的活跃窗口：用 pending promise 形态不可行——
    # 同步引擎跑完才返回。改为手工插一条 RUNNING run 行模拟「A 正在跑」，
    # 该行带 project_id 归属，正是 0037 守卫读的形态。）
    run_a = engine.start_with_nodes(
        "outline-batch",
        workflow["nodes"],
        project_id=pid_a,
        project_level_guard=True,
        initial_ctx={"db_path": str(engine.db_path), "project_id": pid_a, "chapter_count": 2},
        mock_providers={"outline_batch_planner": _planner_script(1, 2)},
    )
    assert get_run(engine.db_path, run_a) is not None

    # A 再起：被守卫拦（同项目）——即使前一个 run 已 COMPLETED 也可能不拦（终态），
    # 故用手工 RUNNING 行构造确定性的「同项目活跃」场景。
    conn = get_connection(engine.db_path)
    try:
        wf_row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'outline-batch'"
        ).fetchone()
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, project_id, "
            "status, current_node, checkpoint_json, error, retry_count, started_at, ended_at) "
            "VALUES ('wfr_active_a', ?, NULL, ?, 'RUNNING', NULL, '{}', NULL, 0, 't', NULL)",
            (wf_row["workflow_id"], pid_a),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        # 同项目 ⇒ WorkflowRunConflict（detail 的 run_id 是本项目的活跃行）
        with pytest.raises(WorkflowRunConflict) as exc_info:
            engine.start_with_nodes(
                "outline-batch",
                workflow["nodes"],
                project_id=pid_a,
                project_level_guard=True,
                initial_ctx={
                    "db_path": str(engine.db_path),
                    "project_id": pid_a,
                    "chapter_count": 2,
                },
                mock_providers={"outline_batch_planner": _planner_script(10, 2)},
            )
        assert "wfr_active_a" in str(exc_info.value)
        assert pid_a in str(exc_info.value)
    finally:
        # 清理手工活跃行，防其影响后续若加的断言（测试库随 tmp_path 丢弃）
        conn = get_connection(engine.db_path)
        try:
            conn.execute("DELETE FROM workflow_runs WHERE run_id = 'wfr_active_a'")
            conn.commit()
        finally:
            conn.close()

    # 跨项目 ⇒ 不拦（201 语义）：B 正常起跑并走到 COMPLETED
    run_b = engine.start_with_nodes(
        "outline-batch",
        workflow["nodes"],
        project_id=pid_b,
        project_level_guard=True,
        initial_ctx={
            "db_path": str(engine.db_path),
            "project_id": pid_b,
            "chapter_count": 2,
        },
        mock_providers={"outline_batch_planner": _planner_script(1, 2)},
    )
    run_b_row = get_run(engine.db_path, run_b)
    assert run_b_row is not None and run_b_row["status"] == "COMPLETED"
    # B 的 run 行带 project_id 归属（0037 列被引擎写入）
    assert run_b_row["project_id"] == pid_b


def test_outline_batch_registered_and_unknown_names_are_none():
    """注册看守：outline-batch 在注册表中（3 节点线性）；未注册名返回 None。"""
    wf = get_workflow("outline-batch")
    assert wf is not None
    assert wf["name"] == "outline-batch"
    assert [n.node_id for n in wf["nodes"]] == ["assemble", "planner", "persist"]
    # 反向：未注册名不得假装存在（对应端点的「未注册 500」判据）
    assert get_workflow("outline-batch-not-registered") is None
