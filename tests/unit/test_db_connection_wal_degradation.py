"""``get_connection`` 在切 WAL 失败时**不得**抛（2026-10-16 批次十 · Lead 自办）。

## 为什么是缺陷而不是「无所谓」

``packages/core/db.py`` 旧注释写「sqlite 自动忽略失败」。**该注释已被实测推翻**：
``PRAGMA journal_mode = WAL`` 要拿排他锁，他连接持写锁时它**抛**
``sqlite3.OperationalError: database is locked``。

危害不在「建连接失败」，在**失败之后没人知道**：

    _peek_chapter_context / _peek_project_context
        except Exception -> return _empty_chapter_peek(...)
            -> state_version = 0 / genre_pack_ref = None
                -> 缓存键维度写成 0 与 '__none__'
                    -> 同 state_version 的脏命中（硬规则 2：漏键 = 脏命中）

即：**装配不会失败，它会「成功」地算出一个错的缓存键**。这比抛异常坏得多——
抛异常至少看得见。

## 为什么降级（而不是让异常冒出去）

WAL 是并发优化、不是正确性前提：退回 rollback journal 后功能完全不受影响，
只是并发降低。反之，让一个**可避免**的异常冒到「打不开库 → 全空态」的兜底里，
换来的却是一个静默的脏缓存键。⇒ 取降级，但必须留痕（``log.warning``）。

## 判别力

两条断言的正控都在用例里（见 ``_held_write_lock`` 的自检），确保红的原因就是
「get_connection 抛了」，而不是「锁没生效、根本没进那条分支」——后者是本仓登记的
「夹具未触发闸门 ⇒ 空断言」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.db import get_connection

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_delete_mode_db(path: Path) -> None:
    """建一个 **rollback journal（delete）模式**的库并写一行数据。

    必须显式钉住模式：sqlite 新建库默认就是 ``delete``，而一旦某次测试先把它切成
    WAL，后续同路径复用就会让「锁竞争」不再成立——夹具必须自证模式。
    """
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "delete", f"夹具前提不成立：新建库应是 delete 模式，实为 {mode!r}"


def _held_write_lock(db_path: Path) -> sqlite3.Connection:
    """返回一个**正持写锁**的连接（调用方负责 rollback/close）。

    ``BEGIN EXCLUSIVE`` + 未提交 INSERT ⇒ 从别的连接看过去是「锁被占着」。

    ⚠️ 正控是本用例的命门：若锁没真生效，被测代码走的就是「无竞争」路径，
    断言会**恒绿**而不代表任何东西（坑区「夹具永远不触发闸门 ⇒ 空断言」）。
    """
    holder = sqlite3.connect(str(db_path))
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("INSERT INTO t VALUES (2)")
    in_tx = holder.in_transaction
    wrote = holder.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    # 自检：必须仍在事务内且已产生未提交写，否则下面的竞争不成立。
    assert in_tx and wrote == 2, "正控失败：写锁没真持住，用例会恒绿"
    return holder


def _release(holder: sqlite3.Connection) -> None:
    """幂等释放持锁连接。

    用例自己放锁后，夹具 teardown 还会再放一次——直接 ``rollback``/``close`` 一个
    已关闭连接会抛 ``ProgrammingError``，把「用例通过」变成「夹具报错」。
    """
    try:
        holder.rollback()
    except sqlite3.ProgrammingError:
        pass
    try:
        holder.close()
    except sqlite3.ProgrammingError:
        pass


@pytest.fixture
def locked_db(tmp_path: Path):
    """delete 模式库 + 真实写锁竞争。yield ``(db_path, holder)``，用例自己决定何时放锁。"""
    db_path = tmp_path / "wal_lock.db"
    _make_delete_mode_db(db_path)
    holder = _held_write_lock(db_path)
    try:
        yield db_path, holder
    finally:
        _release(holder)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_get_connection_survives_write_lock_contention(locked_db):
    """核心：锁竞争下 ``get_connection`` **不抛**；放锁后连接完好可用。

    ⚠️ 断言为什么拆成两段：rollback journal 模式下 ``BEGIN EXCLUSIVE`` 挡的是
    **整个库文件的全部访问**——实测连不碰表的 ``SELECT 1`` 都报 ``database is locked``。
    而能挡住 ``PRAGMA journal_mode=WAL`` 的锁按定义就会挡读。所以：

    * 「不抛」必须在**持锁期间**断言（这才是被测的分支）；
    * 「连接可用」必须在**放锁之后**断言（否则量具根本没法量）。

    只断言前半段的话，一个返回半残连接的实现照样绿。
    """
    db_path, holder = locked_db
    conn = get_connection(db_path)  # ← 持锁期间调用：修复前此处抛 database is locked

    _release(holder)
    try:
        # 放锁后连接完好：能读数据、row_factory 生效、连接级 PRAGMA 都设上了
        assert [r["x"] for r in conn.execute("SELECT x FROM t").fetchall()] == [1]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_wal_is_actually_enabled_on_the_normal_path(tmp_path: Path):
    """反向不变量：无竞争时 **WAL 必须真的开起来**。

    防「把 PRAGMA 整行删掉也算修好」这种过度修正——降级分支必须在正常路径上
    是死代码。
    """
    db_path = tmp_path / "wal_ok.db"
    _make_delete_mode_db(db_path)
    conn = get_connection(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", f"正常路径上 WAL 没开起来（mode={mode!r}），降级分支成了常态"
    finally:
        conn.close()


def test_lock_contention_actually_blocks_a_direct_pragma(locked_db):
    """量具自检：证明这个夹具**真的**能触发那条失败路径。

    直接裸跑 ``PRAGMA journal_mode = WAL``（即被撤掉修复后的原始形态），
    必须抛——否则上面两条用例就是在一把没锁的库上空转。

    ⚠️ 这条**必须抛**：它就是「撤掉修复」的那个 mutant，钉住它等于钉住判别力本身。
    """
    db_path, holder = locked_db
    conn = sqlite3.connect(str(db_path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            conn.execute("PRAGMA journal_mode = WAL")
    finally:
        conn.close()
        _release(holder)
