"""降级 peek 必须与主 JOIN 同形带出 ``outline_json``（2026-10-21 修）。

事故形状（P0-2）：装配缓存键的 ``outline_fp`` 由 ``peek["outline_json_raw"]`` 算
（``cache._fingerprint_outline_json``）。主 JOIN 因**任何结构缺失**失败时走
``_peek_chapter_context_degraded``，而该降级路径原先只读 ``number/project_id/
plan_json`` 三列 ⇒ ``outline_fp`` 恒为「指纹 None」的 ``"none"`` ⇒ **改
``chapters.outline_json`` 后同 state_version 重跑仍命中旧装配**，而 payload 里
``chapter.outline`` 已是新值（uncached 路径独立读章节行）—— 大纲改动对 director
装配完全无效、且零告警。这违反硬规则 2「主 JOIN 与降级路径的键形态必须逐字同形」。

**触发面不是「只有极老库」**：2026-10-21 实测 dev 库停在迁移 0031（缺
``skills`` / ``project_skills`` 表）⇒ 主 JOIN 每次都抛错 ⇒ 降级是常态。

判别（撤修复必红）：``test_degraded_peek_carries_outline_json`` 断言的
``outline_json_raw`` 在撤掉降级 SQL 的 ``outline_json`` 列后变 ``None``，同用例红。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.context_engine.builders_common.common import _peek_chapter_context
from packages.core.context_engine.cache import _fingerprint_outline_json

OUTLINE = '{"chapter_goal": "新大纲：主角改走暗线", "version": 9}'


def _make_db(path: Path, *, with_outline: bool) -> None:
    """只建 ``chapters`` 一张表 ⇒ 主 JOIN 必失败 ⇒ 强制走降级路径。

    ``with_outline=False`` 模拟 0028 之前的库（连列都没有），用于验证「列级再降
    一档」不会把极老库读成空态。
    """
    cols = "chapter_id TEXT PRIMARY KEY, number INTEGER, project_id TEXT, plan_json TEXT"
    if with_outline:
        cols += ", outline_json TEXT"
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE chapters ({cols})")
        if with_outline:
            conn.execute(
                "INSERT INTO chapters VALUES (?, ?, ?, ?, ?)",
                ("ch_degraded", 7, "prj_degraded", '{"plan": 1}', OUTLINE),
            )
        else:
            conn.execute(
                "INSERT INTO chapters VALUES (?, ?, ?, ?)",
                ("ch_degraded", 7, "prj_degraded", '{"plan": 1}'),
            )
        conn.commit()
    finally:
        conn.close()


def test_degraded_peek_carries_outline_json(tmp_path: Path) -> None:
    """核心判别：降级路径必须带出大纲原文，否则 outline_fp 恒 ``"none"``。"""
    db = tmp_path / "degraded.db"
    _make_db(db, with_outline=True)

    peek = _peek_chapter_context(str(db), "ch_degraded")

    assert peek["found"] is True
    assert peek["chapter_no"] == 7, "降级路径仍须读得到章节号"
    assert peek["plan_json_raw"] == '{"plan": 1}', "plan_json 维度不受本次改动影响"
    assert peek["outline_json_raw"] == OUTLINE, (
        "降级 peek 没有带出 outline_json ⇒ 缓存键 outline_fp 恒 'none' ⇒ "
        "改大纲后同 state_version 重跑会命中旧装配（硬规则 2：主 JOIN 与降级路径"
        "键形态必须逐字同形）"
    )
    # 把「读到了」翻译成「键维度真的变了」——这一步才是消费方口径。
    assert _fingerprint_outline_json(peek["outline_json_raw"]) != "none"


def test_degraded_peek_without_outline_column_still_reads_row(tmp_path: Path) -> None:
    """0028 之前的库（无 ``outline_json`` 列）⇒ 退三列档，不许崩、不许读成空态。"""
    db = tmp_path / "legacy.db"
    _make_db(db, with_outline=False)

    peek = _peek_chapter_context(str(db), "ch_degraded")

    assert peek["found"] is True, "缺列时读到空态会丢掉整章上下文"
    assert peek["chapter_no"] == 7
    assert peek["plan_json_raw"] == '{"plan": 1}'
    assert peek["outline_json_raw"] is None, "列不存在 ⇒ 该维度本来就没有输入"


def test_main_path_and_degraded_agree_on_outline(tmp_path: Path) -> None:
    """主 JOIN 与降级路径对同一行必须给出**同一份**大纲原文（同形要求的最小看守）。"""
    db = tmp_path / "both.db"
    _make_db(db, with_outline=True)
    # 库结构不全 ⇒ 只能走降级；两者的共同契约是「返回章节行原文，不做解析」。
    peek = _peek_chapter_context(str(db), "ch_degraded")
    assert peek["outline_json_raw"] == OUTLINE
    assert isinstance(peek["outline_json_raw"], str), "键指纹吃的是原文 str，不得预先解析"


def test_unopenable_db_returns_empty_peek(tmp_path: Path) -> None:
    """**已登记边界**：库根本打不开（损坏 / 不是 SQLite）⇒ 全空态，不抛。

    这是 ``_peek_chapter_context`` docstring 明写的口径（「连接打不开 → 全空态」），
    与「降级路径吞锁」是两件事——本用例把边界钉住，免得将来把它当成本次修复的
    回归点误改。
    """
    db = tmp_path / "broken.db"
    db.write_bytes(b"not a sqlite database at all" * 64)

    peek = _peek_chapter_context(str(db), "ch_degraded")

    assert peek["found"] is False
    assert peek["outline_json_raw"] is None


def test_locked_database_is_not_silently_downgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级路径**不得吞掉锁 / I/O**——静默降级会让缓存键维度失真且零告警。

    手段：让降级读抛 ``database is locked``（非缺表/缺列），断言它冒出来。
    判别：把 ``_peek_chapter_context_degraded`` 里那句
    ``if not _is_missing_schema_error(exc): raise`` 删掉（退回裸 ``except
    sqlite3.Error``）⇒ 本用例必红。
    """
    from packages.core.context_engine.builders_common import common

    db = tmp_path / "degraded.db"
    _make_db(db, with_outline=True)

    def boom(_conn: sqlite3.Connection, _chapter_id: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(common, "_read_fallback_chapter_row", boom)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        _peek_chapter_context(str(db), "ch_degraded")

