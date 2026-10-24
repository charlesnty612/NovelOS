"""分支写路径推 ``story_states.state_version``（R-1，2026-10-24）。

缺陷形状（AGENTS 硬规则 2 根治条件②「写路径推 version」）
---------------------------------------------------------
``branches`` 表进 director 装配 payload 的 ``plot_graph_excerpt.unresolved_branches``
段（只取 **ACTIVE 未解决分支**）⇒ 改分支状态就是改了**权威输入**。
而三条写路径此前**只 commit()、不推** ``story_states.state_version``：

1. 建分支（``packages/core/story_state/branches.py::create_branch``）；
2. 合并分支（``branches.py::promote_branch``，两处 ``UPDATE ... status='MERGED'``）；
3. 临时分支归档（``packages/core/simulation/service.py::_archive_branch``，
   ``status='ARCHIVED'``）。

⇒ 同 ``state_version`` 下重跑同一章 ⇒ planner 拿着**改动前的分支窗口**排计划。

⇒ 修复 = 三条写路径**同事务**（同连接）调用
``packages.core/story_state/versioning.py::bump_project_state_version``
（core 层唯一实现；域层 ``domain/project/service.py`` 的同名函数改为转发）。

⚠️ **为什么写路径推版本、而不是只靠键维度**
键维度（``plot_fp`` = plot_events + branches 的内容指纹）已经能捕获分支变化；
但硬规则 2 的根治条件是「① 键维度齐全 **且** ② 写路径推 ``state_version``」——
只做 ① 是治标（键维度一旦漏了别的输入，照样脏命中）。本组用例钉住 ②。

判别口径
--------
* **断言打在真实值上**：``story_states`` 表里该项目的 **MAX(state_version) 实读值**。
* **正向不变量**：无 ``story_states`` 行（从未 commit）⇒ 推进函数**不造行**
  （沿用域层写路径的既有边界）。
* **内容级旁证**：归档后 director payload 的 ``unresolved_branches`` 里不再有该分支
  （这正是「改状态 = 改权威输入」的机制本体）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from packages.core.context_engine import build_director_input
from packages.core.context_engine.cache import _cache_reset
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.simulation.service import _archive_branch
from packages.core.story_state.service import StoryStateService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ctx_e_branch_version_push.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = f"prj_brv_{uuid4().hex[:10]}"
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at) VALUES (?, ?, '前提', '科幻', NULL, 'ACTIVE', ?, ?)",
        (pid, "分支版本推进测试", now, now),
    )
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
        (cid, pid, number, f"第{number}章", now, now),
    )
    return cid


def _max_state_version(db_path: Path, pid: str) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS sv FROM story_states WHERE project_id = ?",
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["sv"] or 0)


def _state_rows(db_path: Path, pid: str) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM story_states WHERE project_id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def _seed(tmp_path: Path, *, with_snapshot: bool = True) -> dict:
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    ch1 = _insert_chapter(db, pid, 1)
    if with_snapshot:
        # 生产写路径：init_genesis 落 state_deltas + commits + story_states v1。
        StoryStateService(db).init_genesis(pid, ch1)
    return {"db": db, "project": pid, "chapter": ch1}


# ---------------------------------------------------------------------------
# 写点 1：建分支
# ---------------------------------------------------------------------------


def test_create_branch_advances_state_version(tmp_path):
    """建分支 ⇒ 同事务推 ``story_states.state_version``（+1）。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    before = _max_state_version(db, pid)
    assert before == 1, "夹具前提：genesis 落 v1"

    StoryStateService(db).create_branch(pid, "支线甲")

    assert _max_state_version(db, pid) == before + 1, (
        "建分支必须推 state_version（分支行进 director payload 的未解决分支段）"
    )


# ---------------------------------------------------------------------------
# 写点 2：合并分支（MERGED）
# ---------------------------------------------------------------------------


def test_promote_branch_advances_state_version(tmp_path):
    """合并分支 ⇒ 推 ``state_version``（+1）。

    用**空分支**（无 commit）⇒ 合并路径上**唯一**的版本变化就是这次推进
    （按序重放 commit 的推进不存在 ⇒ 断言不被别的机制兜住）。
    """
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    branch = StoryStateService(db).create_branch(pid, "支线乙")
    before = _max_state_version(db, pid)

    StoryStateService(db).promote_branch(pid, branch["branch_id"])

    assert _max_state_version(db, pid) == before + 1, (
        "合并分支必须推 state_version（分支状态进 director payload）"
    )


# ---------------------------------------------------------------------------
# 写点 3：临时分支归档（ARCHIVED）
# ---------------------------------------------------------------------------


def test_archive_branch_advances_state_version(tmp_path):
    """归档临时分支 ⇒ 推 ``state_version``（+1）。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    branch = StoryStateService(db).create_branch(pid, "sim-20261024T000000Z")
    before = _max_state_version(db, pid)

    conn = get_connection(db)
    try:
        _archive_branch(conn, branch["branch_id"])
    finally:
        conn.close()

    assert _max_state_version(db, pid) == before + 1, (
        "归档分支必须推 state_version（simulation 的 ARCHIVED 写路径）"
    )


def test_archived_branch_leaves_director_payload(tmp_path):
    """内容级旁证：归档后该分支不再出现在 director payload 的未解决分支段。"""
    ids = _seed(tmp_path)
    db, pid, ch1 = ids["db"], ids["project"], ids["chapter"]
    branch = StoryStateService(db).create_branch(pid, "sim-20261024T010000Z")
    _cache_reset()

    before = build_director_input(db, pid, ch1, "意图")
    names_before = [
        b["branch_id"] for b in before["plot_graph_excerpt"]["unresolved_branches"]
    ]
    assert branch["branch_id"] in names_before, "夹具前提：新建分支应在未解决分支窗口里"

    conn = get_connection(db)
    try:
        _archive_branch(conn, branch["branch_id"])
    finally:
        conn.close()

    after = build_director_input(db, pid, ch1, "意图")
    names_after = [
        b["branch_id"] for b in after["plot_graph_excerpt"]["unresolved_branches"]
    ]
    assert branch["branch_id"] not in names_after, (
        f"归档后分支必须离开未解决分支窗口，实得 {names_after!r}"
    )


# ---------------------------------------------------------------------------
# 正向不变量：项目尚无快照行 ⇒ 推进**不造行**
# ---------------------------------------------------------------------------


def test_branch_write_without_snapshot_row_creates_no_row(tmp_path):
    """项目从未 commit（无 ``story_states`` 行）⇒ 建分支**不造**快照行。"""
    ids = _seed(tmp_path, with_snapshot=False)
    db, pid = ids["db"], ids["project"]
    assert _state_rows(db, pid) == 0, "夹具前提：项目尚无快照行"

    StoryStateService(db).create_branch(pid, "支线丙")

    assert _state_rows(db, pid) == 0, (
        "无快照行时不得凭空造行（沿用域层写路径的既有边界）"
    )
    assert _max_state_version(db, pid) == 0
