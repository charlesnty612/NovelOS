"""``characters_fingerprint`` 的错误路径必须有看守（2026-10-16 批次十一 · Lead 自办）。

## 为什么补这个文件

突变闸门首轮报 **E1-7 存活**：那条突变把 ``characters_fingerprint`` 里
``if not is_missing_schema_error(exc): raise`` 撤掉（锁故障不再上抛、而是静默
退化成 ``_FINGERPRINT_NONE``），而 ``test_e1_character_cache_key.py`` 的 26 例
**一条都没红** ⇒ 新增代码的错误路径**无判别面**。

⚠️ 存活 ≠ 修复无效，按纪律先怀疑量具：查下来不是变异写错，而是**真的没有测试**
覆盖这两处 ``except``。

## 危害

角色指纹是 writer / director **两条缓存键共用的维度**。静默归 ``None`` 的意思是：
「读不到角色」与「角色已改」落到**同一键形态** ⇒ 同章重跑命中旧装配
⇒ 正文沿用改动前的角色设定——正是本函数存在的理由被自己拆掉。

## 判别纪律

* 每条反例都**带正控**：断言目标 SQL / 建连**确实被尝试过**，否则「因为没走到那条
  分支所以绿」与「守住了」在绿/red 上同形（坑区「夹具未触发闸门 ⇒ 空断言」）。
* 期望值取自常量语义（``_FINGERPRINT_NONE`` / 稳定哈希），**不抄实现输出**。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from packages.core.context_engine import cache as cache_mod
from packages.core.db import apply_migrations, get_connection


class _Boom:
    """在指定操作上抛给定异常的连接替身。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = ()):  # noqa: ANN401
        self.executed.append(sql.strip().split("\n")[0][:60])
        raise self._exc

    def close(self) -> None:  # 替身也要能被 finally 关掉
        pass


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "novelos.db"
    apply_migrations(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, created_at, updated_at)"
            " VALUES ('prj_x', 'p', '', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 正常态与哨兵语义
# ---------------------------------------------------------------------------


def test_no_project_id_is_none_sentinel(migrated_db: Path):
    """``project_id`` 为空 → ``_FINGERPRINT_NONE``（不是抛错）。"""
    assert cache_mod.characters_fingerprint(migrated_db, None) == cache_mod._FINGERPRINT_NONE
    assert cache_mod.characters_fingerprint(migrated_db, "") == cache_mod._FINGERPRINT_NONE


def test_empty_project_gets_a_real_hash_not_the_sentinel(migrated_db: Path):
    """项目确实没有角色 ⇒ **稳定哈希**，且与 ``None`` 哨兵**区分**。

    两者若共用键空间，「读不到角色」就会命中「空项目」的装配条目。
    """
    got = cache_mod.characters_fingerprint(migrated_db, "prj_x")
    assert got != cache_mod._FINGERPRINT_NONE
    assert got == cache_mod.characters_fingerprint(migrated_db, "prj_x"), "同内容须同值"


# ---------------------------------------------------------------------------
# 反例 A：缺表 / 缺列 ⇒ 降级为 None（结构缺失，允许）
# ---------------------------------------------------------------------------


def test_missing_table_degrades_to_sentinel(migrated_db: Path):
    """真的把 ``characters`` 表 DROP 掉 ⇒ 降级为 ``_FINGERPRINT_NONE``。

    正控：先直接执行指纹 SQL，确认它确实报 ``no such table``，
    否则本例的绿可能来自「压根没走到降级分支」。
    """
    conn = get_connection(migrated_db)
    try:
        conn.execute("DROP TABLE characters")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            conn.execute(cache_mod._CHARACTERS_FINGERPRINT_SQL, ("prj_x",)).fetchall()
    finally:
        conn.close()

    assert cache_mod.characters_fingerprint(migrated_db, "prj_x") == cache_mod._FINGERPRINT_NONE


# ---------------------------------------------------------------------------
# 反例 B：运行时故障 ⇒ **上抛**（这才是突变 E1-7 要防的方向）
# ---------------------------------------------------------------------------


def test_runtime_fault_propagates_not_degraded(migrated_db: Path, monkeypatch):
    """⚠️ 判别突变 ``E1-7``：查询遇 ``database is locked`` 必须**上抛**。

    撤掉收窄后本函数返回 ``_FINGERPRINT_NONE`` ⇒ 指纹维度失真 ⇒ 脏命中。
    """
    boom = _Boom(sqlite3.OperationalError("database is locked"))
    monkeypatch.setattr(cache_mod, "get_connection", lambda _p: boom)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        cache_mod.characters_fingerprint(migrated_db, "prj_x")

    assert boom.executed, "正控失败：目标 SQL 压根没被尝试过 ⇒ 本例会恒绿"


def test_connect_failure_runtime_propagates(migrated_db: Path, monkeypatch):
    """建连阶段就遇到运行时故障，同样必须上抛（第二处收窄）。"""

    def _boom_connect(_p):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cache_mod, "get_connection", _boom_connect)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        cache_mod.characters_fingerprint(migrated_db, "prj_x")


def test_connect_failure_missing_schema_degrades(migrated_db: Path, monkeypatch):
    """建连阶段的**结构缺失**仍应降级（正反对照，防收窄收过头）。"""

    def _boom_connect(_p):
        raise sqlite3.OperationalError("no such table: characters")

    monkeypatch.setattr(cache_mod, "get_connection", _boom_connect)

    assert cache_mod.characters_fingerprint(migrated_db, "prj_x") == cache_mod._FINGERPRINT_NONE


def test_readonly_error_propagates(migrated_db: Path, monkeypatch):
    """``attempt to write a readonly database`` 也属运行时故障，不许降级。"""
    boom = _Boom(sqlite3.OperationalError("attempt to write a readonly database"))
    monkeypatch.setattr(cache_mod, "get_connection", lambda _p: boom)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        cache_mod.characters_fingerprint(migrated_db, "prj_x")
    assert boom.executed
