"""读路径**降级范围**契约 · 组 E3（2026-10-16 批次十）：上下文装配 / 题材核销 / 检索。

本组收窄的 6 处 handler
----------------------
``context_engine/writer_input.py``
  1. 项目级字数带覆盖读（``projects.word_band_json``）；
  2. ``reveal_policies`` planned-reader 查询；
  3. ``characters/locations/factions.visibility='HIDDEN'`` 查询；
  4/5. 上述两条链各自的 ``get_connection`` 建连守卫（原为裸 ``except Exception``）。
``genre/verifier.py``
  6. ``_load_bound_pack``（题材包绑定读取）——**校验器失效方向**，见下。
``retrieval/service.py``
  7. ``_current_volume_id``（召回的同卷过滤判据）。

共同缺陷形状：裸 ``except sqlite3.OperationalError`` 把**运行时故障**（锁 / I/O /
损坏 / 只读 / 磁盘满）当成「迁移未跑的极老库」静默吞掉，而各处注释只声称覆盖
「缺表 / 缺列」。危害不是崩溃，是**降级被当成正常**。

三处降级方向各不相同，本组按方向分别立判别（别把「少了点东西」一律当同一件事）：

- **信息泄露**（writer 第 2/3 处）：静默 ``return 0`` = 「一条 HIDDEN 实体都没过滤」，
  本该拦住的实体连摘要一起留在 writer 上下文 → 剧透进正文。**比普通降级更糟**。
- **校验器失效**（verifier）：``database is locked`` 被读成「项目没绑定题材包」
  ⇒ ``bound=False`` + ``skipped=["no_binding"]`` + ``error=None`` ⇒ 本章题材核销整段
  跳过、零 issue、零告警。「读者可见性约束没核」被当成「没有约束可核」。
- **召回越卷**（retrieval）：被读成「本章属卷未知 ⇒ 不做同卷过滤」⇒ 换卷后新卷第一章
  召回上一世正文——正是该函数存在的全部理由。

两条硬纪律（本文件自身的设计约束）
--------------------------------
- **期望值独立于实现**：``skipped`` / ``error`` 的字面量取自
  :class:`~packages.core.genre.verifier.GenreCheckResult` 的字段语义与
  :func:`verify_chapter` 的 docstring 契约，**不是**跑一遍抄实际输出
  （本仓已登记「夹具从被测代码的实际输出里抄形状 ⇒ 绿灯把缺陷锁死」）。
- **判别面覆盖全集**：6 处 handler 逐处立判别，不手挑。少一条 = 该处的收窄裸奔。

⚠️ **如实登记的残余（不在本组分工内，收窄在这些链路上暂不可观测）**
- ``retrieval``：唯一生产调用方 ``director_input.py::_recall_passages`` 在
  ``search()`` 外层套裸 ``except Exception → return []``，会把上抛吞回「召回不到」。
- ``verifier``：``workflows/chapter_review/pipeline.py:149``
  ``if not result.bound: return None`` 会丢弃 ``bound=False`` 的结果，而绑定读取失败
  必然 ``bound=False`` ⇒ ``error`` / ``skipped`` 仍到不了 ``review_report``。
两者均已在本组源码注释与 CHANGELOG 点名，归属成员处置后本组判别才真正生效。

夹具纪律：全部用**临时库 + 合成夹具**，零真实 LLM 调用，不触碰 dev 库既有项目。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from packages.core.context_engine import writer_input as wi
from packages.core.db import apply_migrations, get_connection
from packages.core.genre import verifier as gv
from packages.core.genre.verifier import verify_chapter
from packages.core.ids import new_id, now_iso
from packages.core.retrieval import service as rsvc

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

#: 本组判「必须上抛」的运行时故障全集。三条都取自 Lead 在共享单点
#: ``packages/core/db.py::is_missing_schema_error`` docstring 里实测过的消息表，
#: **不是**临时编的：每一条都必须被判为「故障」而非「结构缺失」。
RUNTIME_DB_FAILURES = [
    "database is locked",
    "disk I/O error",
    "database disk image is malformed",
]


# ---------------------------------------------------------------------------
# 伪连接 / 伪游标（本组全部只走内存，夹具越薄越不掺噪）
# ---------------------------------------------------------------------------


class _Rows:
    """伪游标：同时提供 ``fetchone`` / ``fetchall``，行用普通 dict（调用方按下标取键）。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConn:
    """按 SQL 子串分派的伪连接。

    ``rules`` 为 ``[(sql_子串, 结果)]``：命中子串 → 结果是异常实例则抛出、是 list 则
    当作行集返回；未命中任何规则 → 返回空行集。刻意**不碰真库**：本组守的是
    「异常该怎么处置」这条分支。
    """

    def __init__(self, rules: list[tuple[str, Any]]) -> None:
        self._rules = rules
        self.closed = False

    def execute(self, sql: str, params: Any = ()):  # noqa: ANN001, ANN201
        flat = " ".join(sql.split())
        for needle, outcome in self._rules:
            if needle in flat:
                if isinstance(outcome, BaseException):
                    raise outcome
                return _Rows(outcome)
        return _Rows([])

    def close(self) -> None:
        self.closed = True


class _BoomConn:
    """伪连接：``execute`` 无条件抛指定消息的 ``OperationalError``。"""

    def __init__(self, message: str) -> None:
        self._message = message
        self.closed = False

    def execute(self, *args: Any, **kwargs: Any):  # noqa: ANN002, ANN003, ANN201
        raise sqlite3.OperationalError(self._message)

    def close(self) -> None:
        self.closed = True


class _SqlSubsetBoom:
    """真连接的薄包装：只有命中 ``needle`` 的 SQL 抛异常，其余原样透传。

    用于「同一条链里只有某一处查询失败」的场景——纯伪连接会连前置查询也炸掉，
    导致失败点漂移到被测代码**更靠前**的地方（本仓已登记该失效家族）。
    """

    def __init__(self, inner: sqlite3.Connection, needle: str, message: str) -> None:
        self._inner = inner
        self._needle = needle
        self._message = message

    def execute(self, sql: str, params: Any = ()):  # noqa: ANN001, ANN201
        if self._needle in " ".join(sql.split()):
            raise sqlite3.OperationalError(self._message)
        return self._inner.execute(sql, params)

    def close(self) -> None:
        self._inner.close()


#: 合成夹具：一行 planned + reader audience 的 policy（够走完「读到 policy →
#: 去查实体 visibility」两步，第二步才撞得到第 3 处 handler）。
_POLICY_ROWS = [
    {"target_kind": "character", "target_id": "char_hidden", "audience": "reader"},
]


def _seed_db(tmp_path: Path) -> tuple[Path, str, str]:
    """临时库 + 合成项目/章节（跑全链迁移，不碰 dev 库）。返回 ``(db_path, pid, cid)``。"""
    db_path = tmp_path / "e3.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    pid, cid = new_id("prj"), new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words,"
            " status, created_at, updated_at) VALUES (?,?,NULL,NULL,NULL,'ACTIVE',?,?)",
            (pid, "e3-synthetic", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
            " status, visibility, who_knows, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, pid, 1, "E3", '{"chapter_goal": "synthetic"}', "PLANNED",
             "VISIBLE", None, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path, pid, cid


# ---------------------------------------------------------------------------
# 1. writer 字数带覆盖读（writer_input.py · 第 1 处）
# ---------------------------------------------------------------------------


def _build_writer_with_word_band_failure(
    tmp_path: Path, monkeypatch, message: str,
):
    """跑真装配链，只让「读 projects.word_band_json」那一条 SQL 抛 ``message``。

    ⚠️ 必须走真库 + 薄包装而不是纯伪连接：这条查询前面还有
    ``SELECT * FROM chapters`` 与 ``_author_style_samples``，纯伪连接会把失败点
    顶到被测代码更靠前的地方，测出来的是「另一个原因」。
    """
    db_path, _pid, cid = _seed_db(tmp_path)

    def _gc(p: Any) -> Any:
        return _SqlSubsetBoom(
            get_connection(p), "word_band_json FROM projects", message,
        )

    monkeypatch.setattr(wi, "get_connection", _gc)
    return wi._build_writer_input_uncached(str(db_path), cid, {}, 3000)


def test_writer_word_band_missing_column_still_degrades(tmp_path, monkeypatch):
    """列缺失 ⇒ 视为无覆盖、照常装配（收窄不得把既有降级路径一起打死）。"""
    payload = _build_writer_with_word_band_failure(
        tmp_path, monkeypatch, "no such column: word_band_json",
    )
    assert payload["chapter"]["target_word_count"] == 3000
    band = payload["chapter"]["word_band"]
    assert set(band) == {"low", "high"}




@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_writer_word_band_runtime_failure_propagates(tmp_path, monkeypatch, message):
    """锁 / I/O / 损坏**不是**「项目没设字数带」——必须上抛，不得静默改用模块默认。"""
    with pytest.raises(sqlite3.OperationalError, match=message):
        _build_writer_with_word_band_failure(tmp_path, monkeypatch, message)


@pytest.mark.parametrize("raw", ["{not json", "[1, 2, 3]", '"a string"', "null"])
def test_writer_word_band_bad_value_still_degrades(tmp_path, monkeypatch, raw):
    """注释声称的另两条降级（值异常 / 解析失败）不受收窄影响——逐条钉住。

    防止把「收窄」做成「只保留一条降级路径」：这三条注释里都写了。
    """
    db_path, _pid, cid = _seed_db(tmp_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET word_band_json = ?", (raw,),
        )
        conn.commit()
    finally:
        conn.close()
    payload = wi._build_writer_input_uncached(str(db_path), cid, {}, 3000)
    band = payload["chapter"]["word_band"]
    assert set(band) == {"low", "high"}


def test_writer_word_band_override_applies_when_readable(tmp_path):
    """对照组：读得到就必须真的用上（否则上面那些降级用例可能全是空断言）。

    断言写成**关系**而非具体数字（不抄某次输出）：覆盖 ``low_ratio/high_ratio``
    把字数带**放宽**（见 :func:`resolve_band_config` 的默认值 0.85/1.15 与
    ``low_ratio∈(0,1]`` / ``high_ratio≥1`` 的校验规则），故覆盖后上下界都应更高。
    """
    db_path, _pid, cid = _seed_db(tmp_path)
    baseline = wi._build_writer_input_uncached(str(db_path), cid, {}, 3000)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET word_band_json = ?",
            (json.dumps({"low_ratio": 0.9, "high_ratio": 1.5}),),
        )
        conn.commit()
    finally:
        conn.close()

    covered = wi._build_writer_input_uncached(str(db_path), cid, {}, 3000)
    assert covered["chapter"]["word_band"]["low"] > baseline["chapter"]["word_band"]["low"]
    assert covered["chapter"]["word_band"]["high"] > baseline["chapter"]["word_band"]["high"]


# ---------------------------------------------------------------------------
# 2/3/4/5. writer 的 reveal_policy HIDDEN 过滤（writer_input.py · 第 2~5 处）
# ---------------------------------------------------------------------------


def test_reveal_policy_missing_table_still_degrades(monkeypatch):
    """0014 未跑 ⇒ 缺 ``reveal_policies`` 表 ⇒ 视为「无 policy」不阻断装配。"""
    monkeypatch.setattr(
        wi, "get_connection",
        lambda *_: _FakeConn([
            ("FROM reveal_policies", sqlite3.OperationalError("no such table: reveal_policies")),
        ]),
    )
    assert wi._filter_hidden_by_reveal_policies("no-such.db", "prj_x", {}) == 0


@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_reveal_policy_query_runtime_failure_propagates(monkeypatch, message):
    """读不到 policy ≠ 没有 policy：静默 0 会让 HIDDEN 实体连摘要一起进 writer 上下文。"""
    monkeypatch.setattr(
        wi, "get_connection",
        lambda *_: _FakeConn([
            ("FROM reveal_policies", sqlite3.OperationalError(message)),
        ]),
    )
    with pytest.raises(sqlite3.OperationalError, match=message):
        wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {})


@pytest.mark.parametrize("message", ["no such table: reveal_policies", "no such column: audience"])
def test_reveal_policy_query_schema_errors_degrade(monkeypatch, message):
    """结构缺失（缺表 / 缺列）两种形态都只降级——正反对的另一半。"""
    monkeypatch.setattr(
        wi, "get_connection",
        lambda *_: _FakeConn([("FROM reveal_policies", sqlite3.OperationalError(message))]),
    )
    assert wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {}) == 0


@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_hidden_entity_lookup_runtime_failure_propagates(monkeypatch, message):
    """第 3 处：policy 读到了、查 ``visibility`` 时炸掉 ⇒ 必须上抛。

    夹具先让 policy 查询**成功**返回一行 planned/reader policy，再让 HIDDEN 查询炸
    ——否则失败点会漂到第 2 处（测试测到了另一个机制，红了但理由不对）。
    """
    monkeypatch.setattr(
        wi, "get_connection",
        lambda *_: _FakeConn([
            ("FROM reveal_policies", _POLICY_ROWS),
            ("FROM characters", sqlite3.OperationalError(message)),
        ]),
    )
    with pytest.raises(sqlite3.OperationalError, match=message):
        wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {})




@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_reveal_policy_connect_failure_propagates(monkeypatch, message):
    """第 4 处：建连本身失败（原为裸 ``except Exception``）⇒ 锁 / I/O 必须上抛。"""
    def _boom(*_: Any) -> Any:
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(wi, "get_connection", _boom)
    with pytest.raises(sqlite3.OperationalError, match=message):
        wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {})


def test_reveal_policy_non_db_connect_error_still_degrades(monkeypatch):
    """反向对：非 DB 异常维持既有降级（本次**刻意**保留的 ``except Exception`` 分支）。

    少这一条 ⇒ 有人把整段砍成只接 ``OperationalError`` 也不会被测出来。
    """
    def _boom(*_: Any) -> Any:
        raise RuntimeError("not a db error")

    monkeypatch.setattr(wi, "get_connection", _boom)
    assert wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {}) == 0


@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_second_reveal_connect_failure_propagates(monkeypatch, message):
    """第 5 处：第二条链（查实体 visibility 前）的建连守卫 ⇒ 锁 / I/O 必须上抛。"""
    calls: list[int] = []

    def _gc(*_: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError(message)
        return _FakeConn([("FROM reveal_policies", _POLICY_ROWS)])

    monkeypatch.setattr(wi, "get_connection", _gc)
    with pytest.raises(sqlite3.OperationalError, match=message):
        wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {})
    assert len(calls) == 2, "夹具没走到第二次建连 ⇒ 用例测的不是第 5 处"


def test_second_reveal_connect_non_db_error_still_degrades(monkeypatch):
    """第 5 处的反向对：非 DB 异常仍降级。"""
    calls: list[int] = []

    def _gc(*_: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("not a db error")
        return _FakeConn([("FROM reveal_policies", _POLICY_ROWS)])

    monkeypatch.setattr(wi, "get_connection", _gc)
    assert wi._filter_hidden_by_reveal_policies("x.db", "prj_x", {}) == 0


# ---------------------------------------------------------------------------
# 6. 题材核销的绑定读取（genre/verifier.py）——「校验器失效」方向
# ---------------------------------------------------------------------------


def _patch_binding_reader(monkeypatch, message: str) -> None:
    """让 ``_load_bound_pack`` 的那条 JOIN 抛 ``message``，其余查询照常走真库。"""
    real = gv.get_connection

    def _gc(p: Any) -> Any:
        return _SqlSubsetBoom(real(p), "FROM projects pj", message)

    monkeypatch.setattr(gv, "get_connection", _gc)


def test_verify_chapter_missing_table_reports_no_binding(tmp_path, monkeypatch):
    """极老库缺 ``genre_packs`` ⇒ 落成「未绑定」，零 issue（既有语义不动）。"""
    db_path, _pid, cid = _seed_db(tmp_path)
    _patch_binding_reader(monkeypatch, "no such table: genre_packs")
    result = verify_chapter(str(db_path), cid)
    assert result.bound is False
    assert result.skipped == ["no_binding"]
    assert result.error is None


@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
def test_verify_chapter_runtime_failure_is_not_reported_as_no_binding(
    tmp_path, monkeypatch, message,
):
    """**本组最重要的一条**：锁 / I/O / 损坏必须落成「绑定读取失败」，不是「未绑定」。

    撤掉收窄（回到裸 ``except``）时 ``skipped`` 会变回 ``["no_binding"]`` 且
    ``error`` 为 None ⇒ 本用例必红。它同时钉住 ``error`` 里必须出现
    ``binding_read_failed``——这是 ``verify_chapter`` docstring 明写的契约
    （「任何读路径异常都不抛错，``error`` 字段记录原因」）。
    """
    db_path, _pid, cid = _seed_db(tmp_path)
    _patch_binding_reader(monkeypatch, message)
    result = verify_chapter(str(db_path), cid)

    assert result.bound is False
    assert result.skipped == ["genre_pack_unavailable"]
    assert "no_binding" not in result.skipped
    assert result.error is not None
    assert result.error.startswith("binding_read_failed:")
    assert message in result.error
    # 校验器**不抛错**的契约仍成立（本条不是要求改成 fail-closed）
    assert result.issues == []


def test_load_bound_pack_missing_table_returns_none(monkeypatch):
    """单元级正向：``_load_bound_pack`` 本身在缺表时返回 None（不被 raise 打断）。"""
    monkeypatch.setattr(
        gv, "get_connection",
        lambda *_: _FakeConn([
            ("FROM projects pj", sqlite3.OperationalError("no such table: genre_packs")),
        ]),
    )
    assert gv._load_bound_pack("x.db", "prj_x") is None


def test_load_bound_pack_runtime_failure_propagates(monkeypatch):
    """单元级反向：``_load_bound_pack`` 不再把锁 / I/O 吞成「未绑定」。

    ⚠️ 这条是 ``verify_chapter`` 那条的**前置**：调用方（``verify_chapter`` 第 1134 行）
    仍会把异常收成 ``error``，但那条门 ``if not result.bound: return None`` 会把结果
    整段丢弃——所以本组**两层都测**，缺任一层都会让对方的修复看起来"已生效"。
    """
    monkeypatch.setattr(
        gv, "get_connection",
        lambda *_: _FakeConn([
            ("FROM projects pj", sqlite3.OperationalError("database is locked")),
        ]),
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        gv._load_bound_pack("x.db", "prj_x")


# ---------------------------------------------------------------------------
# 7. 召回的同卷过滤（retrieval/service.py）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ["no such column: volume_id", "no such table: chapters"])
def test_current_volume_id_schema_error_returns_none(message):
    """迁移 0015 未跑 ⇒ 缺 ``chapters.volume_id`` ⇒ 保持旧行为（不做同卷过滤）。"""
    conn = _FakeConn([("FROM chapters", sqlite3.OperationalError(message))])
    assert rsvc._current_volume_id(conn, "prj_x", "ch_1", None) is None


@pytest.mark.parametrize("message", RUNTIME_DB_FAILURES)
@pytest.mark.parametrize("branch", ["by_id", "by_no"])
def test_current_volume_id_runtime_failure_propagates(message, branch):
    """两条查询分支（按 chapter_id / 按章号）都要守住——只测一条 = 判别面漏一半。"""
    conn = _FakeConn([("FROM chapters", sqlite3.OperationalError(message))])
    args = ("prj_x", "ch_1", 7) if branch == "by_id" else ("prj_x", None, 7)
    with pytest.raises(sqlite3.OperationalError, match=message):
        rsvc._current_volume_id(conn, *args)


def test_current_volume_id_returns_value_on_happy_path():
    """对照组：读得到就必须真的返回该卷（否则上面全是「反正都返回 None」的空断言）。"""
    conn = _FakeConn([("FROM chapters", [{"volume_id": "vol_2"}])])
    assert rsvc._current_volume_id(conn, "prj_x", "ch_1", None) == "vol_2"


def test_current_volume_id_null_volume_returns_none():
    """章节未挂卷（``volume_id IS NULL``）⇒ None = 不做同卷过滤（业务语义，非故障）。"""
    conn = _FakeConn([("FROM chapters", [{"volume_id": None}])])
    assert rsvc._current_volume_id(conn, "prj_x", "ch_1", None) is None
