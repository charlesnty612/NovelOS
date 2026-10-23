"""director 装配缓存键的第 6/7 次同形状：5 个进 payload 的权威输入没有键维度。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 6/7 次**）
------------------------------------------------------------------
2026-10-23 第二轮审查（M-1 / D-7）实测抓到 director 侧**又缺 5 个维度**：

============================  ==========================  =============================
进 payload 的输入              payload 落点                 既有写路径（不推 state_version）
============================  ==========================  =============================
``hooks``（伏笔台账）          ``hook_ledger_excerpt`` /    ``LedgerService``（增删改核销）
                              ``open_foreshadow_list``
``narrative_debts``（叙事债务）``narrative_debt_excerpt``    ``LedgerService``
``plot_events`` + ``branches`` ``plot_graph_excerpt``        ``PlotService`` / 建分支
``projects.foreshadow_         ``open_foreshadow_list``      ``ProjectService.update``
  overdue_chapters``（逾期阈值） （overdue 判定）
**director 侧** ``chapters.   ``chapter.title``             ``ChapterService.update``
  title``（章标题）
============================  ==========================  =============================

⇒ 改数据后重跑同一章 ⇒ **命中旧装配** ⇒ planner 拿着改动前的数据排计划，**零告警**。

Lead 探针实测（临时库 + 合成行、零 LLM；断言打在 payload 实际内容上）::

    [A1 伏笔台账变化]     重装=False payload变化=False before=0      after=0      => 脏命中
    [A2 director 侧章标题] 重装=False payload变化=False before='旧标题' after='旧标题' => 脏命中
    [C1 对照组: 项目前提]  重装=True  payload变化=True  before='旧前提' after='新前提' => 已正确 miss

判别口径
--------
* **断言打在 payload 的实际内容上**（``hook_ledger_excerpt`` 里有没有那条伏笔、
  ``open_foreshadow_list`` 的 ``overdue`` 是不是 True、``chapter.title`` 是不是新标题），
  不是「键不等」——后者是结果，前者才是机制。
* **真实装配次数**用 ``_spy_uncached`` 数（命中返回**深拷贝** ⇒ 不能用对象身份判定）。
* **反向不变量**：什么都不改 ⇒ 必须命中（防「过度失效」把缓存改成永不命中）。

⚠️ **为什么这 5 条用裸 SQL 而不是走 service**
--------------------------------------------
本任务有**两条**修复，职责不同，必须能被分别证伪：

1. **键维度**（本文件 5 条场景用例）：改数据 ⇒ 键变 ⇒ 必然重装；
2. **写路径推 ``state_version``**（``test_*_write_path_advances_state_version`` 那几条）：
   任何写路径改数据 ⇒ ``story_states.state_version`` 变 ⇒ 必然重装。

若场景用例走 **service**（它会推 version），那么「撤掉键维度」这条突变**仍然绿**
（version 推了 ⇒ 照样 miss）⇒ 突变守不住键维度这条修复。
⇒ 故这 5 条用**裸 SQL** 写数据（绕过 version 推进）⇒ **只可能由键维度救回来** ⇒
撤维度必红；撤 version 推进则由另一组用例必红。两条修复各自可证伪。

**对照组**（不重复造轮子）：「改 ``projects.premise`` ⇒ 正确 miss」已存在同款，见
``tests/unit/test_ctx_b_meta_title_key.py::test_changing_project_premise_misses_director_cache``。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import packages.core.context_engine.director_input as di_mod
from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.context_engine import cache as cache_mod
from packages.core.context_engine.cache import _cache_reset
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.service import StoryStateService
from packages.domain.chapter.models import ChapterUpdate
from packages.domain.chapter.service import ChapterService
from packages.domain.ledger.models import HookCreate
from packages.domain.ledger.service import LedgerService
from packages.domain.plot.service import PlotService
from packages.domain.project.models import ProjectUpdate
from packages.domain.project.service import ProjectService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

TARGET_CHAPTER_NO = 5
"""目标章章号（伏笔在第 1 章引入 ⇒ 距今 4 章 ⇒ 阈值 ≤3 即逾期）。"""

HOOK_CHAPTER_NO = 1


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
    db_path = tmp_path / "ctx_c_ledger_key.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, *, overdue: int = 30) -> str:
    pid = f"prj_ckey_{uuid4().hex[:10]}"
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at, foreshadow_overdue_chapters)"
        " VALUES (?, ?, '前提', '科幻', NULL, 'ACTIVE', ?, ?, ?)",
        (pid, "台账缓存键测试", now, now, overdue),
    )
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int, title: str = "章") -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
        (cid, pid, number, title, now, now),
    )
    return cid


def _insert_hook(
    db_path: Path, pid: str, *, chapter_id: str | None = None,
    hook_id: str | None = None, name: str = "旧伏笔", status: str = "OPEN",
) -> str:
    hid = hook_id or new_id("hook")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id, status,"
        " importance, visibility, who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 0.5, 'RESTRICTED', NULL, ?, ?)",
        (hid, pid, name, chapter_id, status, now, now),
    )
    return hid


def _insert_debt(db_path: Path, pid: str, *, description: str = "旧债务") -> str:
    did = new_id("debt")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO narrative_debts (debt_id, project_id, description, created_chapter_id,"
        " severity, status, visibility, who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, NULL, 0.5, 'open', 'RESTRICTED', NULL, ?, ?)",
        (did, pid, description, now, now),
    )
    return did


def _insert_plot_event(db_path: Path, pid: str, *, type_: str = "conflict") -> str:
    eid = new_id("evt")
    _exec(
        db_path,
        "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json,"
        " participants_json, time_json, status, visibility, who_knows)"
        " VALUES (?, ?, ?, '[]', '[]', '[]', '{\"timeline_day\":1}', 'planned',"
        " 'RESTRICTED', NULL)",
        (eid, pid, type_),
    )
    return eid


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


def _seed(tmp_path: Path, *, overdue: int = 30, with_snapshot: bool = True) -> dict:
    """项目 + 5 章 + （可选）genesis 快照 + 1 条第 1 章引入的伏笔。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db, overdue=overdue)
    chapters = {
        n: _insert_chapter(db, pid, n, title=f"第{n}章") for n in range(1, TARGET_CHAPTER_NO + 1)
    }
    hook_id = _insert_hook(db, pid, chapter_id=chapters[HOOK_CHAPTER_NO])
    if with_snapshot:
        # 生产写路径：init_genesis 落 state_deltas + commits + story_states v1。
        StoryStateService(db).init_genesis(pid, chapters[HOOK_CHAPTER_NO])
    return {
        "db": db,
        "project": pid,
        "chapters": chapters,
        "target": chapters[TARGET_CHAPTER_NO],
        "hook": hook_id,
    }


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（命中返回深拷贝 ⇒ 不能用对象身份判定）。"""
    real = getattr(module, attr)
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _hook_names(payload: dict) -> list[str]:
    return [h["name"] for h in payload["hook_ledger_excerpt"]]


# ---------------------------------------------------------------------------
# 场景 1：伏笔台账（hooks）
# ---------------------------------------------------------------------------


def test_adding_hook_misses_director_cache_and_payload_shows_new_hook(tmp_path, monkeypatch):
    """新增伏笔 ⇒ 必须重装且 payload 的伏笔台账带上新伏笔。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert "旧伏笔" in _hook_names(first)
    assert calls["n"] == 1

    _insert_hook(db, pid, chapter_id=ids["chapters"][HOOK_CHAPTER_NO], name="新伏笔")

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, (
        "新增伏笔后必须重装（不得命中旧装配）——planner 会拿到改动前的伏笔台账"
    )
    assert "新伏笔" in _hook_names(second), (
        f"payload 的伏笔台账必须带上新伏笔，实得 {_hook_names(second)!r}"
    )


# ---------------------------------------------------------------------------
# 场景 2：叙事债务（narrative_debts）
# ---------------------------------------------------------------------------


def test_adding_debt_misses_director_cache_and_payload_shows_new_debt(tmp_path, monkeypatch):
    """新增叙事债务 ⇒ 必须重装且 payload 的债务段带上新债务。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert first["narrative_debt_excerpt"] == []
    assert calls["n"] == 1

    _insert_debt(db, pid, description="新债务")

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, "新增债务后必须重装（不得命中旧装配）"
    descriptions = [d["description"] for d in second["narrative_debt_excerpt"]]
    assert "新债务" in descriptions, (
        f"payload 的叙事债务段必须带上新债务，实得 {descriptions!r}"
    )


# ---------------------------------------------------------------------------
# 场景 3：情节图（plot_events + branches）
# ---------------------------------------------------------------------------


def test_adding_plot_event_misses_director_cache_and_payload_shows_new_event(tmp_path, monkeypatch):
    """新增情节事件 ⇒ 必须重装且 payload 的情节图带上新事件。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert first["plot_graph_excerpt"]["upcoming_planned_events"] == []
    assert calls["n"] == 1

    _insert_plot_event(db, pid, type_="revelation")

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, "新增情节事件后必须重装（不得命中旧装配）"
    event_ids = [
        e["event_id"]
        for e in second["plot_graph_excerpt"]["upcoming_planned_events"]
    ]
    assert len(event_ids) == 1, (
        f"payload 的情节图必须带上新事件，实得 {event_ids!r}"
    )


# ---------------------------------------------------------------------------
# 场景 4：项目级逾期阈值（projects.foreshadow_overdue_chapters）
# ---------------------------------------------------------------------------


def test_lowering_overdue_threshold_misses_cache_and_payload_marks_hook_overdue(
    tmp_path, monkeypatch,
):
    """调低逾期阈值 ⇒ 必须重装且 payload 的 open_foreshadow_list 标出逾期。"""
    ids = _seed(tmp_path, overdue=30)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert first["open_foreshadow_list"], "夹具前提：第 1 章引入的伏笔应进开放清单"
    assert first["open_foreshadow_list"][0]["overdue"] is False, (
        "阈值 30 时（第 1 章引入、第 5 章当前，距 4 章）不应判逾期"
    )
    assert calls["n"] == 1

    _exec(
        db,
        "UPDATE projects SET foreshadow_overdue_chapters = ?, updated_at = ?"
        " WHERE project_id = ?",
        (3, now_iso(), pid),
    )

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, "改逾期阈值后必须重装（不得命中旧装配）"
    assert second["open_foreshadow_list"][0]["overdue"] is True, (
        "阈值 3 时（距 4 章 > 3）必须判逾期——实得 "
        f"{second['open_foreshadow_list'][0]!r}"
    )


# ---------------------------------------------------------------------------
# 场景 5：director 侧章标题（chapters.title）
# ---------------------------------------------------------------------------


def test_renaming_chapter_misses_director_cache_and_payload_shows_new_title(
    tmp_path, monkeypatch,
):
    """改章标题 ⇒ 必须重装且 payload 的 chapter.title 是新标题（director 侧维度）。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert first["chapter"]["title"] == "第5章"
    assert calls["n"] == 1

    _exec(
        db,
        "UPDATE chapters SET title = ?, updated_at = ? WHERE chapter_id = ?",
        ("新章标题", now_iso(), target),
    )

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, "改章标题后必须重装（planner 会被告知旧标题）"
    assert second["chapter"]["title"] == "新章标题", (
        f"payload 必须带上新章标题，实得 {second['chapter']!r}"
    )


# ---------------------------------------------------------------------------
# 反向不变量：什么都不改 ⇒ 必须命中（防「过度失效」）
# ---------------------------------------------------------------------------


def test_unchanged_inputs_hit_director_cache(tmp_path, monkeypatch):
    """什么都不改 ⇒ 第二次必须命中缓存（实得 uncached 调用 1 次）。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    build_director_input(db, pid, target, "意图")
    build_director_input(db, pid, target, "意图")
    assert calls["n"] == 1, (
        f"输入未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )


def test_one_write_costs_exactly_one_reassembly(tmp_path, monkeypatch):
    """**过度失效**反向不变量：1 次写 ⇒ 恰好多付 1 次重装，其后继续命中。

    「写路径推 ``state_version``」是**粗**失效（整个项目所有以 state_version 为主线
    的键都 miss）⇒ 必须钉住「代价有界」：**不是**「每次装配都重装」。

    实测口径（本用例即守卫）：
    - 写前装配 2 次 ⇒ 未命中 1 次（冷启动）+ 1 次命中；
    - 写 1 次 ⇒ 再装配 ⇒ 未命中 2 次（多付 1 次）；
    - 其后再装配 3 次 ⇒ 未命中仍是 2 次（继续命中）。
    """
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    build_director_input(db, pid, target, "意图")
    build_director_input(db, pid, target, "意图")
    assert calls["n"] == 1, "写前：只应有冷启动那 1 次未命中"

    LedgerService(db).create_hook(pid, HookCreate(name="新伏笔"))

    build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, "写后第一次必须重装（state_version 变了）"

    for _ in range(3):
        build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, (
        f"1 次写只应多付 1 次重装，其后必须继续命中（实得未命中 {calls['n']} 次）——"
        "若持续增长 ⇒ 推 version 造成了「每次都重装」的过度失效"
    )


# ---------------------------------------------------------------------------
# writer 侧实测结论：writer 既不读 project_meta_fp、也不读逾期阈值
# ---------------------------------------------------------------------------


def test_writer_key_unaffected_by_overdue_threshold_change(tmp_path):
    """**实测结论**：改项目级逾期阈值 ⇒ writer 缓存键**逐位不变**。

    背景：本任务给「项目级逾期阈值」单列一维（``foreshadow_fp``）而**不**扩
    ``project_meta_fp``。若将来有人改主意去扩 ``project_meta_fp``，本用例会告诉他
    **writer 侧不受影响**——因为 writer 键里根本没有 ``project_meta_fp``（实测：
    改 ``projects.premise`` 同样不动 writer 键）。
    """
    db = _fresh_db(tmp_path)
    pid = _insert_project(db, overdue=30)
    cid = _insert_chapter(db, pid, 1, title="第1章")
    _cache_reset()

    def writer_key() -> tuple:
        build_writer_input(db, cid, {})
        keys = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "writer"]
        assert len(keys) == 1, f"应有 1 条 writer 键，实得 {len(keys)}"
        return keys[0]

    before = writer_key()

    _exec(
        db,
        "UPDATE projects SET foreshadow_overdue_chapters = ?, updated_at = ?"
        " WHERE project_id = ?",
        (3, now_iso(), pid),
    )

    after = writer_key()
    assert after == before, (
        "writer 侧不消费逾期阈值 ⇒ 其缓存键必须逐位不变（实得 "
        f"{before} → {after}）"
    )


# ---------------------------------------------------------------------------
# 写路径推 state_version（第二条修复：与键维度各自可证伪）
# ---------------------------------------------------------------------------


def test_ledger_write_path_advances_state_version(tmp_path):
    """LedgerService 写路径 ⇒ 同事务内推 ``story_states.state_version``。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    before = _max_state_version(db, pid)
    assert before == 1, "夹具前提：genesis 落 v1"

    LedgerService(db).create_hook(pid, HookCreate(name="新伏笔"))

    assert _max_state_version(db, pid) == before + 1, (
        "伏笔写路径必须推 state_version（否则键维度漏了别的输入时照样脏命中）"
    )


def test_plot_write_path_advances_state_version(tmp_path):
    """PlotService 写路径 ⇒ 同事务内推 ``story_states.state_version``。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    before = _max_state_version(db, pid)
    assert before == 1, "夹具前提：genesis 落 v1"

    PlotService(db).create_event(pid, "conflict")

    assert _max_state_version(db, pid) == before + 1, (
        "情节事件写路径必须推 state_version"
    )


def test_project_write_path_advances_state_version(tmp_path):
    """ProjectService 写路径 ⇒ 同事务内推 ``story_states.state_version``。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    before = _max_state_version(db, pid)
    assert before == 1, "夹具前提：genesis 落 v1"

    ProjectService(db).update(pid, ProjectUpdate(foreshadow_overdue_chapters=3))

    assert _max_state_version(db, pid) == before + 1, (
        "项目 PATCH 写路径必须推 state_version"
    )


def test_chapter_write_path_advances_state_version(tmp_path):
    """ChapterService 写路径 ⇒ 同事务内推 ``story_states.state_version``。"""
    ids = _seed(tmp_path)
    db, target = ids["db"], ids["target"]
    pid = ids["project"]
    before = _max_state_version(db, pid)
    assert before == 1, "夹具前提：genesis 落 v1"

    ChapterService(db).update(target, ChapterUpdate(title="新章标题"))

    assert _max_state_version(db, pid) == before + 1, (
        "章标题 PATCH 写路径必须推 state_version"
    )


def test_write_path_without_story_state_row_keeps_version_unchanged(tmp_path):
    """项目**还没有快照**时 ⇒ 不造快照行，``state_version`` 保持 0（不变）。

    口径（简报要求）：「若某条写路径没有对应 story_state 行 ⇒ 不要自己造快照行」。
    """
    ids = _seed(tmp_path, with_snapshot=False)
    db, pid = ids["db"], ids["project"]
    assert _max_state_version(db, pid) == 0, "夹具前提：该项目尚无 story_states 行"

    LedgerService(db).create_hook(pid, HookCreate(name="新伏笔"))

    assert _max_state_version(db, pid) == 0, (
        "无 story_state 行时不得造快照行（state_version 保持不变）"
    )
    conn = get_connection(db)
    try:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM story_states WHERE project_id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert int(rows["n"]) == 0, "无快照项目不得被写入 story_states 行"


# ---------------------------------------------------------------------------
# 对照组（引用既有同款，不重复造轮子）
# ---------------------------------------------------------------------------
# 「改 projects.premise ⇒ 正确 miss」已存在：
#   tests/unit/test_ctx_b_meta_title_key.py::test_changing_project_premise_misses_director_cache
# 它证明本文件的装置不是假阳性（同样的 spy + payload 断言口径）。
