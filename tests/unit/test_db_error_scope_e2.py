"""判别测试：builders_common 三模块 8 处 ``except sqlite3.OperationalError`` 的降级范围。

批次十（2026-10-16）清欠账：8 处裸 except 把「迁移未跑 ⇒ 缺表/缺列」与「运行时故障
（锁 / I/O / 损坏 / 只读 / 磁盘满）」一起吞成同一个降级值。危害不是崩溃，而是**降级被
当成正常**：作者以为体裁约束生效、产出的正文却没带体裁，全程零告警。

判别结构（对着 AGENTS.md 已登记的失效家族设计）：
- **正反一对**：每处都跑「``no such table``/``no such column`` → 仍降级」与
  「``database is locked`` → 上抛」。撤掉守卫必红的是**反向那半**。
- **夹具必须真的进过那个分支**：stub conn 记录它见过的每条 SQL，用例断言目标 helper 的
  SQL **确实被尝试过**——否则「恒真断言」会把「压根没走到被测点」报成绿
  （坑区「夹具永远不触发闸门 ⇒ 落在正确节点上的断言恒为真」）。
- **期望值独立于实现**：降级哨兵一律硬编码（``0`` / ``None`` / 字面 ``30``），
  不从被测模块 import 常量——否则常量一改断言跟着改，永远测不出东西。
- **判别面覆盖全集**：A 组用一份 8 条注册表遍历（不是手挑几处）；
  D 组用 AST 扫三个源文件，断言**不存在**任何「``sqlite3.OperationalError`` 却没有
  ``as exc``」的 handler——新增第 9 处裸 except 会立刻红。
- **外层不再吞**（C 组，对应批次九 planner_input/scene_planner 的失败形状）：
  逐层验证 ``except sqlite3.Error`` → ``_peek_chapter_context_degraded`` → 5 个 helper
  这条链，以及 ``_peek_chapter_context`` 公开入口，收窄不会被外层重新吞回去。
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from packages.core.context_engine.builders_common import common as bc_common
from packages.core.context_engine.builders_common import ledger as bc_ledger
from packages.core.context_engine.builders_common import summaries as bc_summaries

_SRC_DIR = Path(bc_common.__file__).resolve().parent

# 三条真实故障消息（Lead 在 packages/core.db::is_missing_schema_error 实测过）。
LOCKED = "database is locked"
IO_ERR = "disk I/O error"
NO_TABLE = "no such table: nope_t"
NO_COLUMN = "no such column: nope_col"


class _NullRow(dict):
    """缺列读作 ``None`` 的假行（等价于真实 SQLite 行的 NULL 列）。

    ``sqlite3.Row`` 对未声明的列会抛 ``IndexError``，而生产里 helper 读到的本来就是
    「表里没有这列 ⇒ 该行不存在 / 该列为 NULL」。这里让缺失键返回 ``None``，
    与真实只读查询的形状一致，避免夹具比被测物更严格而制造假红。
    """

    def __missing__(self, key: str) -> None:
        return None


class _FakeCursor:
    """只实现 ``.fetchone()``——8 处 helper 都只用到这一个方法。"""

    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class FaultyConn:
    """按 SQL 文本注入故障的假连接。

    ``faults`` 是 ``[(子串, 异常消息), ...]``，按顺序匹配、首个命中即抛；
    未命中返回 ``default_row``。``seen`` 记录它**实际见过**的每条 SQL——
    用例靠它证明「夹具真的走到了被测点」（见模块 docstring 第 3 条）。
    """

    def __init__(
        self,
        faults: list[tuple[str, str]],
        *,
        default_row: Any = None,
    ) -> None:
        self._faults = faults
        self._default_row = default_row
        self.seen: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        self.seen.append(sql)
        for needle, message in self._faults:
            if needle in sql:
                raise sqlite3.OperationalError(message)
        if self._default_row is None:
            return _FakeCursor(_NullRow())
        if isinstance(self._default_row, dict) and not isinstance(
            self._default_row, _NullRow
        ):
            return _FakeCursor(_NullRow(self._default_row))
        return _FakeCursor(self._default_row)

    def saw(self, needle: str) -> bool:
        """目标 SQL 是否真的被尝试过（防止空断言恒真）。"""
        return any(needle in sql for sql in self.seen)

    def close(self) -> None:  # 与 sqlite3.Connection 同形，供 peek 的 finally 调用
        pass


# --------------------------------------------------------------------------------------
# A 组：8 处逐点判别（正反一对 × 8）
# --------------------------------------------------------------------------------------

# (用例 id, 被调用者, 用于唯一识别其 SQL 的子串, 缺结构时的消息, 降级哨兵)
SITES: list[tuple[str, Any, str, str, Any]] = [
    (
        "max_state_version",
        lambda conn: bc_common._project_max_state_version(conn, "p1"),
        "SELECT MAX(state_version) AS max_sv",
        NO_TABLE,
        0,
    ),
    (
        "word_band_json",
        lambda conn: bc_common._try_project_word_band_json(conn, "p1"),
        "SELECT word_band_json FROM projects",
        NO_COLUMN,
        None,
    ),
    (
        "writing_bible",
        lambda conn: bc_common._try_project_writing_bible(conn, "p1"),
        "SELECT writing_bible FROM projects",
        NO_COLUMN,
        None,
    ),
    (
        "active_canon_id",
        lambda conn: bc_common._try_active_canon_id(conn, "p1"),
        "SELECT canon_id FROM reference_canons",
        NO_TABLE,
        None,
    ),
    (
        "genre_pack_ref",
        lambda conn: bc_common._try_project_genre_pack_ref(conn, "p1"),
        "genre_pack_id || '@' || gp.version",
        NO_COLUMN,
        None,
    ),
    (
        "overdue_chapters",
        lambda conn: bc_ledger._project_overdue_chapters(conn, "p1"),
        "SELECT foreshadow_overdue_chapters FROM projects",
        NO_COLUMN,
        30,  # 硬编码 DDL DEFAULT 30，不 import _FORESHADOW_OVERDUE_CHAPTERS
    ),
    (
        "volume_id_by_id",
        lambda conn: bc_summaries._volume_id_of_chapter(conn, "c1"),
        "SELECT volume_id FROM chapters WHERE chapter_id = ?",
        NO_COLUMN,
        None,
    ),
    (
        "volume_id_by_no",
        lambda conn: bc_summaries._volume_id_of_chapter_no(conn, "p1", 3),
        "ORDER BY chapter_id ASC LIMIT 1",
        NO_COLUMN,
        None,
    ),
]

# 反向用例在 ``_project_overdue_chapters`` 上再加一条 I/O 故障：它吞的不是锁而是
# ``disk I/O error``，同一守卫必须一并挡住（否则「只挡锁」的半修会漏）。
EXTRA_FAULTS: list[tuple[str, Any, str, str, Any]] = [
    (
        "io_error",
        lambda conn: bc_ledger._project_overdue_chapters(conn, "p1"),
        "SELECT foreshadow_overdue_chapters FROM projects",
        IO_ERR,
        30,
    ),
]


def _site_ids() -> list[str]:
    return [s[0] for s in SITES]


@pytest.mark.parametrize(
    ("call", "needle", "missing_msg", "expected"),
    [(s[1], s[2], s[3], s[4]) for s in SITES],
    ids=_site_ids(),
)
def test_missing_schema_still_degrades(call, needle, missing_msg, expected) -> None:
    """正例：缺表 / 缺列（迁移未跑的极老库）仍按各 helper 既定口径降级。"""
    conn = FaultyConn([(needle, missing_msg)])
    result = call(conn)
    assert conn.saw(needle), "夹具没走到被测点，降级值等于空断言"
    assert result == expected


@pytest.mark.parametrize(
    ("call", "needle", "missing_msg", "expected"),
    [(s[1], s[2], s[3], s[4]) for s in SITES],
    ids=_site_ids(),
)
def test_locked_error_propagates(call, needle, missing_msg, expected) -> None:
    """反例：``database is locked`` 必须上抛，不得静默降级。

    撤掉 ``if not is_missing_schema_error(exc): raise`` ⇒ 本组全红。
    """
    conn = FaultyConn([(needle, LOCKED)])
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        call(conn)
    assert conn.saw(needle), "夹具没走到被测点"
    assert LOCKED in str(excinfo.value)
    assert not str(excinfo.value).startswith("no such"), "上抛的必须是故障而非缺结构"


@pytest.mark.parametrize(
    ("call", "needle", "io_msg", "expected"),
    [(f[1], f[2], f[3], f[4]) for f in EXTRA_FAULTS],
    ids=[f[0] for f in EXTRA_FAULTS],
)
def test_io_error_propagates(call, needle, io_msg, expected) -> None:
    """反例补强：I/O 故障同样上抛——守卫不是「只挡锁」的半修。"""
    conn = FaultyConn([(needle, io_msg)])
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        call(conn)
    assert conn.saw(needle), "夹具没走到被测点"
    assert io_msg in str(excinfo.value)


# --------------------------------------------------------------------------------------
# B 组：真实 SQL 正例（临时库 + 合成夹具；不指向 dev 库）
# --------------------------------------------------------------------------------------

def _old_schema_db(path: Path) -> None:
    """极老库形状：projects 缺三列、story_states / reference_canons / genre_packs 不存在。"""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE projects (project_id TEXT)")
    conn.execute("INSERT INTO projects VALUES ('p1')")
    conn.commit()
    conn.close()


def test_old_schema_db_degrades_end_to_end(tmp_path: Path) -> None:
    """真 SQL + 真缺结构：5 个 helper + ledger/summaries 各返回既定降级值。"""
    db = tmp_path / "old.db"
    _old_schema_db(db)
    # 用生产连接工厂（row_factory=sqlite3.Row + WAL）——helper 一律按 Row 取值。
    conn = bc_common.get_connection(db)

    assert bc_common._project_max_state_version(conn, "p1") == 0
    assert bc_common._try_project_word_band_json(conn, "p1") is None
    assert bc_common._try_project_writing_bible(conn, "p1") is None
    assert bc_common._try_active_canon_id(conn, "p1") is None
    assert bc_common._try_project_genre_pack_ref(conn, "p1") is None
    assert bc_ledger._project_overdue_chapters(conn, "p1") == 30
    assert bc_summaries._volume_id_of_chapter(conn, "c1") is None
    assert bc_summaries._volume_id_of_chapter_no(conn, "p1", 1) is None
    conn.close()


def test_full_schema_db_returns_real_values(tmp_path: Path) -> None:
    """正例的另一头：结构齐全时读到真值——收窄没把正常路径也拦掉。"""
    db = tmp_path / "full.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE projects (project_id TEXT, word_band_json TEXT,"
        " writing_bible TEXT, foreshadow_overdue_chapters INT,"
        " genre_pack_id TEXT)"
    )
    conn.execute(
        "INSERT INTO projects VALUES ('p1','{\"min\":2000}', '永不用弃"
        "用章', 12, 'gp_1')"
    )
    conn.execute("CREATE TABLE story_states (project_id TEXT, state_version INT)")
    conn.execute("INSERT INTO story_states VALUES ('p1', 7)")
    conn.execute(
        "CREATE TABLE reference_canons (canon_id TEXT, project_id TEXT,"
        " status TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO reference_canons VALUES ('cn_1','p1','active','2026-01-01')"
    )
    conn.execute("CREATE TABLE genre_packs (pack_id TEXT, version INT)")
    conn.execute("INSERT INTO genre_packs VALUES ('gp_1', 3)")
    conn.execute("CREATE TABLE chapters (chapter_id TEXT, project_id TEXT, number INT, volume_id TEXT)")
    conn.execute("INSERT INTO chapters VALUES ('c1','p1',1,'v1')")
    conn.commit()
    conn.close()

    conn = bc_common.get_connection(db)
    assert bc_common._project_max_state_version(conn, "p1") == 7
    assert bc_common._try_project_word_band_json(conn, "p1") == '{"min":2000}'
    assert bc_common._try_project_writing_bible(conn, "p1") == '永不用弃用章'
    assert bc_common._try_active_canon_id(conn, "p1") == "cn_1"
    assert bc_common._try_project_genre_pack_ref(conn, "p1") == "gp_1@3"
    assert bc_ledger._project_overdue_chapters(conn, "p1") == 12
    assert bc_summaries._volume_id_of_chapter(conn, "c1") == "v1"
    assert bc_summaries._volume_id_of_chapter_no(conn, "p1", 1) == "v1"
    conn.close()


# --------------------------------------------------------------------------------------
# C 组：外层不再吞（对应批次九「内层收了、外层无差别再吞 ⇒ 修复完全无效」）
# --------------------------------------------------------------------------------------

# 主 JOIN 含 ch.outline_json；降级 SQL 含 "number AS chapter_no"；helper 各有独有片段。
_MAIN_JOIN = "ch.outline_json"
_FALLBACK_SQL = "number AS chapter_no, project_id AS project_id"


def _degraded_path_conn(needle: str, exc_msg: str) -> FaultyConn:
    """让主 JOIN 以「缺列」失败 → 进降级读；helper 那一段注入目标故障。"""
    return FaultyConn(
        [
            (needle, exc_msg),
            (_MAIN_JOIN, NO_COLUMN),
        ],
        # 降级 SQL 成功时返回的行：dict 即可充当 sqlite3.Row 子集（[] + .keys()）。
        default_row={"chapter_no": 1, "project_id": "p1", "plan_json": "{}"},
    )


@pytest.mark.parametrize(
    ("needle", "label"),
    [
        ("SELECT MAX(state_version) AS max_sv", "max_state_version"),
        ("SELECT word_band_json FROM projects", "word_band_json"),
        ("SELECT writing_bible FROM projects", "writing_bible"),
        ("SELECT canon_id FROM reference_canons", "active_canon_id"),
        ("genre_pack_id || '@' || gp.version", "genre_pack_ref"),
    ],
)
def test_degraded_path_does_not_reswallow_locked(needle: str, label: str) -> None:
    """``_peek_chapter_context_conn`` 的 ``except sqlite3.Error`` → 降级读这条链上，
    helper 上抛的锁错误必须**穿出**该 except 块（而非被重新吞成全空态）。"""
    conn = _degraded_path_conn(needle, LOCKED)
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        bc_common._peek_chapter_context_conn(conn, "c1", project_id="p1")
    assert conn.saw(_FALLBACK_SQL), "没走到降级读，异常不是从被测路径冒出来的"
    assert conn.saw(needle), "夹具没走到被测点"
    assert LOCKED in str(excinfo.value)


@pytest.mark.parametrize(
    ("needle", "label"),
    [
        ("SELECT MAX(state_version) AS max_sv", "max_state_version"),
        ("SELECT word_band_json FROM projects", "word_band_json"),
        ("genre_pack_id || '@' || gp.version", "genre_pack_ref"),
    ],
)
def test_public_peek_entry_does_not_reswallow_locked(
    needle: str, label: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """公开入口 ``_peek_chapter_context`` 整链也不吞（含 ``get_connection`` 之后的 finally）。"""
    conn = _degraded_path_conn(needle, LOCKED)
    monkeypatch.setattr(bc_common, "get_connection", lambda _db_path: conn)
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        bc_common._peek_chapter_context(tmp_path / "x.db", "c1", project_id="p1")
    assert conn.saw(needle), "夹具没走到被测点"
    assert LOCKED in str(excinfo.value)


def test_degraded_path_still_degrades_on_missing_schema(monkeypatch) -> None:
    """C 组的正例：真·缺列时降级读照常产出（收窄没有把老库兼容打断）。"""
    conn = FaultyConn(
        [(_MAIN_JOIN, NO_COLUMN)],
        default_row={"chapter_no": 1, "project_id": "p1", "plan_json": "{}"},
    )
    info = bc_common._peek_chapter_context_conn(conn, "c1", project_id="p1")
    assert conn.saw(_FALLBACK_SQL)
    assert info["found"] is True
    assert info["chapter_no"] == 1
    assert info["plan_json_raw"] == "{}"


# --------------------------------------------------------------------------------------
# D 组：判别面覆盖全集——三个源文件里不再存在裸 OperationalError handler
# --------------------------------------------------------------------------------------

_TARGET_FILES = ("common.py", "ledger.py", "summaries.py")


def _bare_operational_error_handlers(path: Path) -> list[int]:
    """AST 找出 ``except sqlite3.OperationalError:`` 且**没有** ``as exc`` 的 handler。

    用 AST 而非正则：注释里出现的同名字面量（本文件就有 3 处）不会被误判，
    且判据是「语义」而非「文本形态」。
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig").removeprefix("﻿"))
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        exc = node.type
        names: list[str] = []
        if isinstance(exc, ast.Attribute):
            names = [exc.attr]
        elif isinstance(exc, ast.Name):
            names = [exc.id]
        if "OperationalError" in names and node.name is None:
            out.append(node.lineno)
    return sorted(out)


@pytest.mark.parametrize("filename", _TARGET_FILES)
def test_no_bare_operational_error_handler_remains(filename: str) -> None:
    """新增第 9 处裸 except 会立刻红——判别面不靠手挑名单。"""
    offenders = _bare_operational_error_handlers(_SRC_DIR / filename)
    assert offenders == [], (
        f"{filename} 仍有裸 `except sqlite3.OperationalError`（无 `as exc`）于行 "
        f"{offenders}：须经 `is_missing_schema_error` 收窄，否则锁 / I/O 被静默吞掉"
    )
