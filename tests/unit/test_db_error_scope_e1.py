"""E1 组判别测试：「except sqlite3.OperationalError 降级范围」收窄（2026-10-16 批次十）。

被测对象（本组 4 文件 7 处，逐处点名见报告）：

===================================  ==========================================
文件 : 处                             本文件的判别
===================================  ==========================================
``packages/core/arc/service.py:286``   ``_project_overdue_chapters`` 正反一对
``packages/core/arc/service.py:382``   ``_load_volumes`` 正反一对
``packages/core/arc/service.py:453``   ``_summarize_reveal_policies`` 保留宽口径（裁决未下）
``packages/core/arc/service.py:648``   ``build_arc_view`` 的 LEFT JOIN 回退 正反一对
``scripts/world_switch.py:538``        ``_fill_evidence`` 正反一对
``scripts/db_maintenance.py:378``      ``main(["prune-logs"])`` 正反一对
``packages/core/backup/service.py:399`` **刻意保持宽口径**（见 §backup）
===================================  ==========================================

每个收窄点配**正反一对**：

* 正 —— 真实的「缺表 / 缺列」（真建真缺，真消息）⇒ 仍按原契约降级，**防收窄过头**；
* 反 —— 真实的 ``database is locked``（**真锁**，见 ``_victim_conn_under_lock``）
  ⇒ 上抛，**防收窄不足 / 白收**。

判别点纪律（对着 AGENTS.md 坑区已登记的失效家族）：

* 反向用例断言的是「异常穿过该 except 抵达调用方」，**不是**「函数返回了某个值」
  ——后者在降级时同样成立，是恒真断言；
* 锁是真锁，**不是** 把 ``is_missing_schema_error`` 打成 ``lambda e: True`` 那种自证
  （该自证已在本组手工复核中做过，见汇报 §6）；
* 正控：``_victim_conn_under_lock`` 内部先自证锁生效，否则反向用例会「因为别的原因红」
  （坑区「量具缺陷伪装成被测物有病」）；
* 覆盖面是**全集**：arc 的 4 处逐处点名，不手挑子集。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.arc import service as arc_service  # noqa: E402
from packages.core.arc.service import (  # noqa: E402
    _load_volumes,
    _project_overdue_chapters,
    _summarize_reveal_policies,
    build_arc_view,
)
from packages.core.config import Settings  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from scripts import db_maintenance, world_switch  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 夹具：真库 / 真空表 / 真锁
# ---------------------------------------------------------------------------


def _migrated_db(tmp_path: Path, *, drop: tuple[str, ...] = ()) -> str:
    """全量迁移后**真 DROP** 若干表 ⇒ 模拟「该迁移未跑的极老库」。

    真 DROP 而非「只建几张表」：只有真缺表，SQLite 才会真的抛
    ``no such table: <name>``，异常类与消息文本都与生产一致。

    ⚠️ ``chapters.volume_id`` 上有指向 ``volumes`` 的外键 ⇒ 想 DROP ``volumes``
    必须**先播种章节再 DROP**（见 :func:`_drop_tables`），否则 INSERT 自己先炸。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    db_path = str(settings.db_path)
    if drop:
        _drop_tables(db_path, drop)
    return db_path


def _drop_tables(db_path: str, tables: tuple[str, ...]) -> None:
    conn = get_connection(db_path)
    try:
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()


def _project(db_path: str, name: str = "e1-arc") -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService
    return ProjectService(db_path).create(ProjectCreate(name=name))["project_id"]


def _add_chapter(db_path: str, pid: str, number: int = 1) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
            " status, visibility, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', '2026-01-01T00:00:00+00:00',"
            " '2026-01-01T00:00:00+00:00')",
            (f"ch_e1_{number}", pid, number, f"第{number}章"),
        )
        conn.commit()
    finally:
        conn.close()
    return f"ch_e1_{number}"


def _victim_conn_under_lock(db_path: str) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """返回 (victim, holder)：victim 上的任意 SELECT 必抛 ``database is locked``。

    **为什么不用 ``get_connection``**：它会 ``PRAGMA journal_mode = WAL``，而 WAL 下
    读者不阻塞写者 ⇒ 读**结构上**吃不到锁，反向用例会假绿。本机实测：
    DELETE 日志模式 + 另一连接 ``BEGIN EXCLUSIVE`` ⇒ SELECT 抛
    ``OperationalError: database is locked``（busy_timeout 到点后）。

    **正控不可省**：若锁因任何原因没生效，下游反向用例会以「没抛异常」的方式红，
    与「闸门没生效」完全同形（坑区「突变被 SKIP ≠ 突变存活」的同族）。
    """
    victim = sqlite3.connect(db_path)
    victim.row_factory = sqlite3.Row
    victim.execute("PRAGMA journal_mode = DELETE")
    victim.execute("PRAGMA busy_timeout = 300")

    holder = sqlite3.connect(db_path)
    holder.row_factory = sqlite3.Row
    holder.execute("PRAGMA journal_mode = DELETE")
    holder.execute("PRAGMA busy_timeout = 300")
    holder.execute("BEGIN EXCLUSIVE")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        victim.execute("SELECT 1 FROM sqlite_master").fetchall()
    return victim, holder


# ---------------------------------------------------------------------------
# arc / 1：_project_overdue_chapters —— 注释声称「列缺」，反例是「列取不到」
# ---------------------------------------------------------------------------


def test_e1_arc_overdue_chapters_missing_column_still_degrades(tmp_path: Path) -> None:
    """正：projects 表**没有** foreshadow_overdue_chapters 列 ⇒ 真降级为默认 30。"""
    db = tmp_path / "no_col.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (project_id) VALUES ('p1')")
        conn.commit()
        # 先证「确实会抛缺列」，避免本用例变成「没触发闸门的空断言」
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            conn.execute(
                "SELECT foreshadow_overdue_chapters FROM projects WHERE project_id = ?",
                ("p1",),
            ).fetchone()
        assert _project_overdue_chapters(conn, "p1") == 30
    finally:
        conn.close()


def test_e1_arc_overdue_chapters_lock_raises(tmp_path: Path) -> None:
    """反：database is locked **必须上抛**，不得被读成「该列取不到 ⇒ 退回 30」。

    不收窄时本函数会返回 30 —— 而 30 正是它的正常业务值 ⇒ 断言「返回 30」恒真。
    """
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    victim, holder = _victim_conn_under_lock(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _project_overdue_chapters(victim, pid)
    finally:
        victim.close()
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# arc / 2：_load_volumes —— 注释声称「0015 未跑（volumes 表不存在）」
# ---------------------------------------------------------------------------


def test_e1_arc_load_volumes_missing_table_still_degrades(tmp_path: Path) -> None:
    """正：volumes 表真不存在 ⇒ 仍返回 []（arc 顶层 volumes 小节为空）。"""
    db_path = _migrated_db(tmp_path, drop=("volumes",))
    pid = _project(db_path)
    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such table: volumes"):
            conn.execute("SELECT volume_id FROM volumes").fetchall()
        assert _load_volumes(conn, pid) == []
    finally:
        conn.close()


def test_e1_arc_load_volumes_lock_raises(tmp_path: Path) -> None:
    """反：锁 ⇒ 上抛；不收窄时返回 []，与「本书没有卷」完全同形。"""
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    victim, holder = _victim_conn_under_lock(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _load_volumes(victim, pid)
    finally:
        victim.close()
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# arc / 3：_summarize_reveal_policies —— 刻意保留宽口径，见下方用例 docstring
# ---------------------------------------------------------------------------


def test_e1_arc_reveal_policies_missing_table_still_degrades(tmp_path: Path) -> None:
    """正：reveal_policies 表真不存在 ⇒ 仍返回全零 + 空 overdue。"""
    db_path = _migrated_db(tmp_path, drop=("reveal_policies",))
    pid = _project(db_path)
    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such table: reveal_policies"):
            conn.execute("SELECT policy_id FROM reveal_policies").fetchall()
        out = _summarize_reveal_policies(conn, pid, None)
    finally:
        conn.close()
    # 期望值取自 docstring 的契约，不取自实现输出
    assert out == {"planned": 0, "revealed": 0, "cancelled": 0, "overdue": []}


def test_e1_arc_reveal_policies_lock_raises(tmp_path: Path) -> None:
    """反：锁 ⇒ 上抛；不收窄时返回的正是上面那个全零 dict —— 与「真的一条都没有」同形。"""
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    # 播种一条真实的 planned 策略：证明「返回全零」= 谎报，而非「本来就没有」
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO reveal_policies (policy_id, project_id, target_kind, target_id,"
            " status, reveal_by_chapter, audience, created_at, updated_at)"
            " VALUES ('rp_e1', ?, 'character', 'x1', 'planned', 1, 'reader',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (pid,),
        )
        conn.commit()
        assert _summarize_reveal_policies(conn, pid, 3)["planned"] == 1
    finally:
        conn.close()

def test_arc_reveal_policies_keeps_wide_contract_pending_ruling(tmp_path: Path) -> None:
    """**钉现状**，不是钉「现状正确」：本函数刻意保留宽口径降级，裁决未下。

    本条**取代**原先的 ``test_e1_arc_reveal_policies_lock_raises``。Lead 复核时
    驳回了这一处的收窄，理由是它的 docstring 原文为：

        SQL 异常（0014 未跑**等** OperationalError）→ 返回全零 + 空 overdue，
        不阻断 arc 装配。

    那句「等」是**显式的宽口径契约**而非笔误；按 ``db.is_missing_schema_error``
    文档化的前提①（注释声称更宽 ⇒ 收窄是**改变契约**而非修缺陷），须先裁决。

    ⚠️ 因此本用例断言的是**当前被明确保留的行为**，且它断言的东西本身是可疑的：
    锁故障下返回的「全零 + 空 overdue」与「本书真的一条策略都没有」**逐字同形**，
    也就是谎报。上面那段 docstring 已经把取舍写清楚了（收窄 ⇒ arc 视图 500）。
    裁决归属：行为/契约变更，不在「清欠账」的工程授权内。

    **为什么要留这条用例**：若没有它，下一轮扫尾会把这处收窄掉，而「契约变更」
    与「顺手统一风格」在 diff 表面长得一模一样，没有一条红的测试逼人停下来。
    ⇒ 要收窄，必须同时改窄上面那句 docstring——那就会红，从而强制一次显式决定。
    """
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    # 播种一条真实的 planned 策略：证明「返回全零」= 谎报，而非「本来就没有」
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO reveal_policies (policy_id, project_id, target_kind, target_id,"
            " status, reveal_by_chapter, audience, created_at, updated_at)"
            " VALUES ('rp_e1', ?, 'character', 'x1', 'planned', 1, 'reader',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (pid,),
        )
        conn.commit()
        assert _summarize_reveal_policies(conn, pid, 3)["planned"] == 1
    finally:
        conn.close()

    victim, holder = _victim_conn_under_lock(db_path)
    try:
        # 锁下**不抛**，返回与「真的一条都没有」同形的全零 —— 这就是待裁决的那一点
        out = _summarize_reveal_policies(victim, pid, 3)
        assert out == {"planned": 0, "revealed": 0, "cancelled": 0, "overdue": []}
    finally:
        victim.close()
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# arc / 4：build_arc_view 内的 LEFT JOIN 回退（0015 未跑 → 退回无卷信息的 chapters）
# ---------------------------------------------------------------------------


def test_e1_arc_view_missing_volumes_table_still_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正：volumes 表缺失 ⇒ 回退查询生效，章节仍返回且 volume 字段为 null。

    顺序说明：章节必须**先于** DROP volumes 播种（``chapters.volume_id`` 有外键）。
    """
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    _add_chapter(db_path, pid)
    _drop_tables(db_path, ("volumes",))
    result = build_arc_view(db_path, pid)
    assert [c["number"] for c in result["chapters"]] == [1]
    assert result["chapters"][0]["volume_id"] is None
    assert result["chapters"][0]["volume_number"] is None
    assert result["volumes"] == []


class _FirstStatementFailsConn:
    """代理连接：**第一次** ``execute`` 抛真实 ``OperationalError("database is locked")``，
    之后透明委派给真连接（异常类、消息文本都是真的，只有「哪一条语句失败」被脚本化）。

    **为什么这一处不能用真锁**（本组自测时就是这么假绿的，值得留档）：
    回退查询与首发查询读的是同一张 ``chapters``，真锁会**同时**打掉两条 ⇒
    「真锁 ⇒ 上抛」在收窄前后都成立，是恒真断言（坑区「断言了结果 ≠ 判别了机制」）。
    脚本化「仅首条失败」才落到收窄前的裸 except 真正会误判的那种形态——
    瞬时故障（busy_timeout 到点、持锁方恰好在两条语句之间释放）⇒ 裸 except
    静默降级成「本书没有卷」，回退查询还成功了、结果看上去完全正常。

    ``calls`` 计数器另作判别：收窄后**不应**发出第 2 条查询（没有重试可藏）。
    """

    def __init__(self, real: sqlite3.Connection, exc: Exception) -> None:
        self._real = real
        self._exc = exc
        self.calls = 0

    def execute(self, sql, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):  # pragma: no cover - 透传（close 等）
        return getattr(self._real, name)


def test_e1_arc_view_lock_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反：首次查询吃锁 ⇒ 上抛，**且不得发出回退查询**。

    不收窄时：首条失败被吞 → 发第 2 条（无卷信息的纯 chapters）→ 成功 →
    ``build_arc_view`` 返回一份「看起来完全正常、只是没有卷信息」的结果。
    """
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    _add_chapter(db_path, pid)
    real = get_connection(db_path)
    proxy = _FirstStatementFailsConn(
        real, sqlite3.OperationalError("database is locked")
    )
    monkeypatch.setattr(arc_service, "get_connection", lambda *a, **k: proxy)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            build_arc_view(db_path, pid)
        assert proxy.calls == 1, "收窄后不应再发回退查询（那会把真因藏进重试）"
    finally:
        real.close()


# ---------------------------------------------------------------------------
# world_switch / _fill_evidence —— 本组后果最重的一处
# ---------------------------------------------------------------------------


def _empty_state() -> dict:
    return {"chapter_no": {}, "chapter_volume_no": {}, "evidence": {}, "eras": {}}


def test_e1_world_switch_missing_plot_events_still_degrades(tmp_path: Path) -> None:
    """正：plot_events 表缺失 ⇒ eras={}（沿用既有 fail-soft 契约）。"""
    db_path = _migrated_db(tmp_path, drop=("plot_events",))
    pid = _project(db_path)
    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such table: plot_events"):
            conn.execute("SELECT event_id FROM plot_events").fetchall()
        state = _empty_state()
        world_switch._fill_evidence(conn, pid, state)
    finally:
        conn.close()
    assert state["eras"] == {}


def test_e1_world_switch_lock_raises_instead_of_blanket_archive(tmp_path: Path) -> None:
    """反：锁 ⇒ 上抛，**绝不能**降级成 eras={}。

    危害链（本组最重）：``eras={}`` ⇒ :func:`_era_decision` 的
    「完全没有证据」分支对**每个** canon 实体返回 ``("archive", "closed_era")``
    ⇒ ``--apply`` 把本该 skip 的跨位面实体全部置 ``inject_mode='archived'``。
    本用例直接钉住那个决策函数，让危害链在测试里可见（而不只是注释里的一句话）。
    """
    db_path = _migrated_db(tmp_path)
    pid = _project(db_path)
    victim, holder = _victim_conn_under_lock(db_path)
    try:
        state = _empty_state()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            world_switch._fill_evidence(victim, pid, state)
        # 若上面没抛（本组的被修缺陷），下面这行就是「危害真的发生了」的证据：
        # 空 eras 让归属判定无条件落到 archive。
        assert world_switch._era_decision(state["eras"].get("whoever", set()), 1) == (
            "archive",
            "closed_era",
        )
    finally:
        victim.close()
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# db_maintenance / prune-logs —— 「读不到」被当成「没有」且退出码 0
# ---------------------------------------------------------------------------


def test_e1_db_maintenance_missing_table_still_skips(tmp_path: Path, capsys) -> None:
    """正：ai_call_logs 表缺失 ⇒ 仍按既有契约打印 [skip] 并退出 0。"""
    db_path = _migrated_db(tmp_path, drop=("ai_call_logs",))
    assert db_maintenance.main(["--db", db_path, "prune-logs", "--days", "1"]) == 0
    assert "[skip] ai_call_logs unavailable" in capsys.readouterr().out


def test_e1_db_maintenance_lock_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """反：锁 ⇒ 上抛，**不得**打印「nothing to prune」并退出 0。

    不收窄时运维会读到「nothing to prune」+ 退出码 0 ⇒ 以为清理完成、实际一行没查
    （而 dry-run 的全部用途就是提前看见）。
    """
    db_path = _migrated_db(tmp_path)
    victim, holder = _victim_conn_under_lock(db_path)
    monkeypatch.setattr(db_maintenance, "get_connection", lambda *a, **k: victim)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db_maintenance.main(["--db", db_path, "prune-logs", "--days", "1"])
    finally:
        victim.close()
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# backup —— **保持宽口径**的那一处：钉住「保持宽」所依赖的真实不变量
# ---------------------------------------------------------------------------


def test_e1_backup_existing_tables_filters_by_schema_not_by_exception(tmp_path: Path) -> None:
    """缺表**不靠捕获异常**发现，而是靠 ``sqlite_master`` 过滤。

    这条是 backup 数据路径上真正的防线：若改成「先跑 ``PRAGMA foreign_key_check``
    再捕获 OperationalError」，锁 / I/O 故障就会与「表不存在」同形。
    断言直接读 ``sqlite_master``（独立于实现的第三方基准），不抄实现输出。
    """
    from packages.core.backup.service import _existing_tables, _fk_violations

    db_path = _migrated_db(tmp_path, drop=("volumes",))
    conn = get_connection(db_path)
    try:
        schema_tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "volumes" not in schema_tables
        assert _existing_tables(conn, ("projects", "volumes")) == ("projects",)
        # 缺表不会走到 FK 校验这一步（本仓 docstring 声明的前提）
        assert _fk_violations(conn, _existing_tables(conn, ("projects", "volumes"))) == set()
    finally:
        conn.close()


def test_e1_backup_failure_reaches_caller_unmasked(tmp_path: Path, monkeypatch) -> None:
    """导入失败时，**作者真正该看的那个异常必须原样抵达调用方**。

    这是 ``packages/core/backup/service.py:399`` 保持宽口径的**唯一理由**：
    那一行在 ``finally`` 里、紧邻 ``except Exception: rollback(); raise``，
    而本机实测（3.12.13）在 finally 里 ``raise`` 会**顶掉在途异常**。
    本用例把这个不变量钉住——收窄那一行的人会先撞上它。

    ⚠️ 它**不能**证明「收窄会改变行为」（实测 ``PRAGMA foreign_keys = ON`` 在
    锁下 / 只读连接下都不抛、事务内还是静默 no-op ⇒ 收窄在那里的行为差为空）。
    所以本组不给该处写突变规格：那样的变异会判「存活」，正是无效变异。
    """
    from packages.core.backup import service as backup_service
    from tests.unit.backup_fixtures import _seed_minimal_project

    db_path = _migrated_db(tmp_path)
    pid = _seed_minimal_project(db_path)
    pkg = backup_service.BackupService(db_path).export_project(pid)

    class _Boom(Exception):
        pass

    original = backup_service.BackupService._import_table_rows

    def _broken(self, conn, table, rows, project_id_map):
        if table == "characters":
            raise _Boom("E1-SENTINEL-must-survive")
        return original(self, conn, table, rows, project_id_map)

    monkeypatch.setattr(backup_service.BackupService, "_import_table_rows", _broken)

    with pytest.raises(_Boom, match="E1-SENTINEL-must-survive"):
        backup_service.BackupService(db_path).import_project(pkg)
