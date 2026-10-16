"""读路径**降级范围**契约 · 计划期与审稿链（2026-10-16 批次十）。

背景
----
三处裸 ``except sqlite3.OperationalError`` 把运行时故障当成「老库缺表」静默吞掉，而各处
注释 / docstring 只声称覆盖「缺表 / 缺列」：

- ``chapter_plan/planner_input.py::load_project_style_constraints``（两条 SELECT + 开连接）；
- ``chapter_plan/scene_plan_store.py::load_scene_plan``（一条 SELECT + 开连接）；
- ``chapter_review/pipeline.py::_basic_checks_node`` 读 ``projects.word_band_json``（+ 外层 wrapper）。

危害形态不是崩溃，而是**降级被当成正常**（AGENTS.md 坑区同款）：

- 文风：回落 ``SCENE_PLANNER_DEFAULT_STYLE`` ⇒ 正文没带项目文风，零告警；
- scene_plan：读成「未命中」⇒ scene_planner 节点改走 LLM 重规划，多烧调用且场景计划换了一套；
- **字数带（审稿链，最重）**：读成「项目没配字数带」⇒ 评审报告**照常跑完**，但
  ``word_band`` / ``within_range`` / ``errors`` 全按默认 ±15% / ±30% 算。作者在 reviewer UI
  看到的是与项目设定不符的「在带内 + 零 warning」并据此放行——**不是审稿被跳过，是审稿给出
  了错的结论**，同族但更隐蔽：有报告、无告警。（这条由 C 组用例正面钉住。）

为什么「开连接」那一半也要收窄（实测，非推理）
----------------------------------------------
``packages.core.db.get_connection`` 的语句顺序是
``sqlite3.connect`` → ``PRAGMA foreign_keys`` → ``PRAGMA journal_mode = WAL`` →
``PRAGMA busy_timeout = 5000``——``journal_mode`` 排在 ``busy_timeout`` **之前**，
且「把库切成 WAL」本身要拿排他锁。本机临时库探针实测（Python 3.12 / SQLite 3.x）：

- 库**已是 WAL**（应用开过）时：并发未提交写事务下 ``get_connection`` 与裸 SELECT **都不抛**；
- 库**仍处 ``delete`` 日志模式**（= 老库形态，恰恰是本降级声称要覆盖的那批）时：
  ``get_connection`` 与裸 SELECT **双双抛** ``database is locked``。

⇒ 锁压根到不了下面两条 SELECT；只收窄 SELECT 那一半，对锁而言是**永远不可达的死代码**
（批次九 ``planner_input`` / ``scene_planner`` 踩过同款：内层收了、外层又吞 ⇒ 修复无效）。

夹具设计纪律（避开 AGENTS.md 已登记的失效家族）
--------------------------------------------
- 期望值**不抄被测代码的输出**：默认文风用硬编码字面量，并另用 ``test_default_style_constant_is_pinned``
  把模块常量本身钉住（否则 ``== SCENE_PLANNER_DEFAULT_STYLE`` 是自指断言）；
- **判别面覆盖全集**：每处三条注入点（开连接 / 首条查询 / 后条查询）都守，
  不手挑「代表性」的那条；
- **夹具真的进过那个分支**：C 组另有对照组 ``test_real_project_word_band_is_applied``（不注入，
  真实库 + 真实项目带 ⇒ 报告用项目带）+ ``test_project_band_differs_from_default_band``
  （默认带 ≠ 项目带）——缺任一条，C1 的「降级 ⇒ 默认带」就是恒真断言。
- 每个 ``except`` 都有**正反一对**（缺表 / 缺列 ⇒ 降级；锁 / I/O / 损坏 ⇒ 上抛）。

判别（撤修复必红）
----------------
- 任一 ``if not is_missing_schema_error(exc): raise`` 撤掉 ⇒ 对应负向用例红；
- ``chapter_review`` 外层 ``except Exception`` 若重新无差别吞 ⇒ C2/C3 红（守批次九那条教训）；
- 连接句柄泄漏（本组三处上抛路径都要求归还连接）由 ``test_no_connection_leaked_on_propagate`` 守。
"""

from __future__ import annotations

import gc
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_plan import planner_input as pi
from packages.workflows.chapter_plan import scene_plan_store as sps
from packages.workflows.chapter_review import pipeline as crp

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

#: 运行时故障的三条代表消息（``packages.core.db.is_missing_schema_error`` 的负向表）
_RUNTIME_FAILURES = [
    "database is locked",
    "disk I/O error",
    "database disk image is malformed",
]

#: ``load_project_style_constraints`` 降级时必须返回的文风——**硬编码字面量**，
#: 不取自 ``SCENE_PLANNER_DEFAULT_STYLE``（否则断言自指：常量一改断言跟着改，绿得没意义）。
_EXPECTED_DEFAULT_STYLE = {
    "language": "zh-Hans",
    "pov": "third_person_limited",
    "dialogue_ratio": 0.4,
    "forbidden_words": ["仿佛", "如同", "本章目标"],
}


# ---------------------------------------------------------------------------
# 夹具工具
# ---------------------------------------------------------------------------


class _SqlBoom:
    """包装真连接：SQL 含 ``marker`` 的语句抛 ``message``，其余原样走真库。

    判别点落在**异常消息**上（``is_missing_schema_error`` 的唯一输入），不依赖
    「跑一下看返回什么」——期望值独立于实现。
    """

    def __init__(self, inner: sqlite3.Connection, marker: str, message: str) -> None:
        self._inner = inner
        self._marker = marker
        self._message = message

    def execute(self, sql: str, *args: Any, **kwargs: Any):  # noqa: ANN401
        if self._marker in sql:
            raise sqlite3.OperationalError(self._message)
        return self._inner.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


class _FakeRow:
    """最小 ``sqlite3.Row`` 替身（只给 ``style_constraints_id`` 一个键）。"""

    def __init__(self, **values: Any) -> None:
        self._values = values

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        return self._values[key]


class _FixedRowConn:
    """连接替身：读 ``projects.style_constraints_id`` 恒返回给定值，**第二条**查询
    （``FROM style_constraints``）抛指定消息。

    匹配按 ``FROM projects`` / ``FROM style_constraints`` 区分两条语句——第二条的 SQL 文本
    里同样含 ``style_constraints_id``（WHERE 条件），按子串匹配会两条都落进同一分支。
    """

    def __init__(self, style_id: str | None, message: str) -> None:
        self._style_id = style_id
        self._message = message

    def execute(self, sql: str, *args: Any, **kwargs: Any):  # noqa: ANN401
        if "FROM projects" in sql:
            return self
        raise sqlite3.OperationalError(self._message)

    def fetchone(self) -> _FakeRow:
        return _FakeRow(style_constraints_id=self._style_id)

    def close(self) -> None:
        pass


def _migrated(tmp_path: Path) -> Path:
    db_path = tmp_path / "e4.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, *, word_band: dict | None = None) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
            " word_band_json, created_at, updated_at) VALUES (?,?,NULL,NULL,NULL,'ACTIVE',?,?,?)",
            (pid, "项目", None if word_band is None else json.dumps(word_band),
             now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
            " visibility, who_knows, created_at, updated_at)"
            " VALUES (?,?,?,'','{}','DRAFTED','VISIBLE',NULL,?,?)",
            (cid, project_id, 1, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,"
            " prompt_version, model_id, created_at) VALUES (?,?,?,?,'test:writer:v1',NULL,NULL,?)",
            (new_id("drf"), chapter_id, 1, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _patch_connect(monkeypatch, module, marker: str | None, message: str) -> None:
    """把 ``module.get_connection`` 换成「命中 marker 即抛」的连接工厂。"""

    def _factory(db_path):  # noqa: ANN001, ANN202
        inner = get_connection(str(db_path))
        if marker is None:
            return inner
        return _SqlBoom(inner, marker, message)

    monkeypatch.setattr(module, "get_connection", _factory)


def _patch_connect_missing(monkeypatch, module) -> None:
    """把 ``module.get_connection`` 换成「缺表/缺列」连接工厂（用于危害实证那一例：
    复现**降级路径本身**的效果，而不是复现故障——故障路径已由上面的负向用例守住）。"""
    real = module.get_connection

    def _factory(db_path):  # noqa: ANN001, ANN202
        return _SqlBoom(real(str(db_path)), "word_band_json",
                        "no such column: projects.word_band_json")

    monkeypatch.setattr(module, "get_connection", _factory)


# ---------------------------------------------------------------------------
# A. planner_input.load_project_style_constraints
# ---------------------------------------------------------------------------


def test_default_style_constant_is_pinned():
    """把降级返回值钉在**硬编码字面量**上——常量一改必红（A 组断言才不是自指的）。"""
    assert pi.SCENE_PLANNER_DEFAULT_STYLE == _EXPECTED_DEFAULT_STYLE


def test_real_schema_has_no_style_constraints_column(tmp_path: Path):
    """真实迁移库上「缺列」是**当前**事实（不是假想的老库）：

    ``projects.style_constraints_id`` / ``style_constraints`` 表在 ``database/migrations/``
    里从不存在 ⇒ 该查询在**每一个**库上都抛 ``no such column`` 并走降级分支。
    这条把「降级路径确实被每天走到」钉成事实，同时暴露出下面的**死功能**：
    项目级文风约束（``docs/agents/prompts/director-v1.md`` 声明的
    ``constraints.style_constraints_id``）没有任何列/表承载，
    ``director_input.py`` 那侧恒为 ``None``。
    """
    db_path = _migrated(tmp_path)
    pid = _insert_project(db_path)
    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            conn.execute("SELECT style_constraints_id FROM projects").fetchone()
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "style_constraints" not in tables
    # 该事实下的实际行为：走「缺列 ⇒ 降级」分支，返回默认文风。
    assert pi.load_project_style_constraints(str(db_path), pid) == _EXPECTED_DEFAULT_STYLE


# --- A1/A2：开连接那一半 ---


def test_connect_lock_propagates(tmp_path: Path, monkeypatch):
    """开连接抛 ``database is locked`` 必须上抛——不得静默回落默认文风。

    守的是 ``get_connection`` 外层那一句：``PRAGMA journal_mode = WAL`` 排在
    ``busy_timeout`` 之前，非 WAL 库上开连接本身就会因锁失败（见模块 docstring 探针）。
    """

    def _boom(_db_path):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pi, "get_connection", _boom)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        pi.load_project_style_constraints(str(tmp_path / "x.db"), "prj_x")


def test_connect_missing_schema_degrades(tmp_path: Path, monkeypatch):
    """反向钉住守卫的**形状**：不是把整段换成无条件 ``raise``。

    （生产上「开连接」不会抛「no such table」，故此例的作用是形状看守，不是真实路径覆盖。）
    """

    def _boom(_db_path):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("no such table: projects")

    monkeypatch.setattr(pi, "get_connection", _boom)
    assert (
        pi.load_project_style_constraints(str(tmp_path / "x.db"), "prj_x")
        == _EXPECTED_DEFAULT_STYLE
    )


# --- A3/A4：第一条查询（projects.style_constraints_id） ---


def test_projects_lookup_missing_column_degrades(tmp_path: Path, monkeypatch):
    _patch_connect(monkeypatch, pi, "style_constraints_id",
                   "no such column: projects.style_constraints_id")
    assert (
        pi.load_project_style_constraints(str(_migrated(tmp_path)), "prj_x")
        == _EXPECTED_DEFAULT_STYLE
    )


@pytest.mark.parametrize("message", _RUNTIME_FAILURES)
def test_projects_lookup_runtime_failure_propagates(
    tmp_path: Path, monkeypatch, message: str
):
    _patch_connect(monkeypatch, pi, "style_constraints_id", message)
    with pytest.raises(sqlite3.OperationalError, match=message):
        pi.load_project_style_constraints(str(_migrated(tmp_path)), "prj_x")


# --- A5/A6：第二条查询（style_constraints 表） ---


def test_style_table_missing_table_degrades(monkeypatch):
    """``style_id`` 非空但表不存在（0027 之前的极老库形态）⇒ 降级默认文风。"""
    monkeypatch.setattr(
        pi, "get_connection",
        lambda _p: _FixedRowConn("scs_1", "no such table: style_constraints"),
    )
    assert pi.load_project_style_constraints("x.db", "prj_x") == _EXPECTED_DEFAULT_STYLE


@pytest.mark.parametrize("message", _RUNTIME_FAILURES)
def test_style_table_runtime_failure_propagates(monkeypatch, message: str):
    monkeypatch.setattr(
        pi, "get_connection", lambda _p: _FixedRowConn("scs_1", message)
    )
    with pytest.raises(sqlite3.OperationalError, match=message):
        pi.load_project_style_constraints("x.db", "prj_x")


# ---------------------------------------------------------------------------
# B. scene_plan_store.load_scene_plan
# ---------------------------------------------------------------------------


def test_scene_plan_missing_table_degrades_to_none(tmp_path: Path, monkeypatch):
    _patch_connect(monkeypatch, sps, "chapter_scene_plans",
                   "no such table: chapter_scene_plans")
    assert sps.load_scene_plan(str(_migrated(tmp_path)), "ch_1") is None


def test_scene_plan_missing_column_degrades_to_none(tmp_path: Path, monkeypatch):
    _patch_connect(monkeypatch, sps, "payload_json",
                   "no such column: chapter_scene_plans.payload_json")
    assert sps.load_scene_plan(str(_migrated(tmp_path)), "ch_1") is None


@pytest.mark.parametrize("message", _RUNTIME_FAILURES)
def test_scene_plan_runtime_failure_propagates(tmp_path: Path, monkeypatch, message: str):
    _patch_connect(monkeypatch, sps, "chapter_scene_plans", message)
    with pytest.raises(sqlite3.OperationalError, match=message):
        sps.load_scene_plan(str(_migrated(tmp_path)), "ch_1")


def test_scene_plan_connect_failure_propagates(tmp_path: Path, monkeypatch):
    def _boom(_db_path):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sps, "get_connection", _boom)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        sps.load_scene_plan(str(tmp_path / "x.db"), "ch_1")


def test_scene_plan_no_row_still_returns_none(tmp_path: Path):
    """对照组：真实库 + 无行 ⇒ ``None``（收窄不得伤到「本来就没有」这条正常路径）。"""
    assert sps.load_scene_plan(str(_migrated(tmp_path)), "ch_absent") is None


# ---------------------------------------------------------------------------
# C. chapter_review._basic_checks_node 的项目级字数带
# ---------------------------------------------------------------------------


def _review_ctx(db_path: Path, chapter_id: str) -> dict:
    return {"db_path": db_path, "chapter_id": chapter_id, "target_word_count": 2000}


def test_project_band_differs_from_default_band():
    """C1 的前提守卫：项目带 ≠ 默认带。

    缺了它，「降级 ⇒ 报告用默认带」在带心附近可能两条路径同值 ⇒ 断言恒真。
    默认带 0.85/1.15 ⇒ (1700, 2300)；项目带 0.9/1.1/floor1500 ⇒ (1800, 2200)。
    """
    from packages.core.quality.wordcount import resolve_band_config, word_band

    default = word_band(2000, **resolve_band_config(None))
    custom = word_band(2000, **resolve_band_config(
        {"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}
    ))
    assert default == (1700, 2300)
    assert custom == (1800, 2200)
    assert default != custom


def test_real_project_word_band_is_applied(tmp_path: Path):
    """对照组（不注入任何故障）：真实库 + 真实项目带 ⇒ 报告用的是**项目带**。

    这条的作用是证明 C1/C2 的夹具**真的进过那个分支**——否则「断言某个降级的结果」
    可能压根没执行被读的那条查询（同 AGENTS.md「夹具永远不触发闸门 ⇒ 断言恒真」）。
    """
    from tests.unit.neutral_prose import neutral_prose

    db_path = _migrated(tmp_path)
    pid = _insert_project(
        db_path, word_band={"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, neutral_prose(2000))
    rep = crp._basic_checks_node(_review_ctx(db_path, cid))["review_report"]
    assert rep["word_band"] == {"low": 1800, "high": 2200}


def test_degraded_word_band_hides_a_real_deviation(tmp_path: Path, monkeypatch):
    """危害实证（**不是**修复守卫——修前修后都绿，只把「危害」从声称变成实测）。

    项目带 (1800, 2200) 下 1750 字的稿子**出带**（应出 1 条 warning）；
    同一份稿子在默认带 (1700, 2300) 下**在带内、零 warning**。
    ⇒ 一旦读带被静默降级（原先 ``database is locked`` 就会），评审报告照常跑完，
    结论却是错的：reviewer UI 上呈现「在带内 + 无告警」，作者据此放行。**零告警。**
    """
    from tests.unit.neutral_prose import neutral_prose

    db_path = _migrated(tmp_path)
    pid = _insert_project(
        db_path, word_band={"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, neutral_prose(1750))

    # ① 读得到项目带 ⇒ 出带、有 warning（项目作者会看到「这章偏短」）
    honest = crp._basic_checks_node(_review_ctx(db_path, cid))["review_report"]
    assert honest["word_band"] == {"low": 1800, "high": 2200}
    assert honest["within_range"] is False
    assert any("W-LEN-DEVIATION" in w for w in honest["warnings"])

    # ② 读带被降级成「无覆盖」⇒ 同一份稿子改按默认带判：在带内、零 warning
    _patch_connect_missing(monkeypatch, crp)
    lied = crp._basic_checks_node(_review_ctx(db_path, cid))["review_report"]
    assert lied["word_band"] == {"low": 1700, "high": 2300}
    assert lied["within_range"] is True
    assert not any("W-LEN-DEVIATION" in w for w in lied["warnings"])


def test_word_band_missing_column_degrades_to_default(tmp_path: Path, monkeypatch):
    """缺 ``projects.word_band_json`` 列（0023 未跑）⇒ 降级为「无覆盖」⇒ 报告用默认带。"""
    from tests.unit.neutral_prose import neutral_prose

    db_path = _migrated(tmp_path)
    pid = _insert_project(
        db_path, word_band={"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, neutral_prose(2000))
    _patch_connect(monkeypatch, crp, "word_band_json",
                   "no such column: projects.word_band_json")
    rep = crp._basic_checks_node(_review_ctx(db_path, cid))["review_report"]
    assert rep["word_band"] == {"low": 1700, "high": 2300}


@pytest.mark.parametrize("message", _RUNTIME_FAILURES)
def test_word_band_runtime_failure_propagates(
    tmp_path: Path, monkeypatch, message: str
):
    """锁 / I/O / 损坏 ⇒ **上抛**。

    守的是两层：① ``except sqlite3.OperationalError`` 内的收窄；② 外层
    ``except Exception`` 不得把刚收窄的范围原样放大（批次九同款教训——
    ``sqlite3.OperationalError`` 是 ``Exception`` 的子类，无差别再吞即让收窄变死代码）。
    任一层被撤，这条都红。
    """
    from tests.unit.neutral_prose import neutral_prose

    db_path = _migrated(tmp_path)
    pid = _insert_project(
        db_path, word_band={"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}
    )
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, neutral_prose(2000))
    _patch_connect(monkeypatch, crp, "word_band_json", message)
    with pytest.raises(sqlite3.OperationalError, match=message):
        crp._basic_checks_node(_review_ctx(db_path, cid))


def test_word_band_connect_failure_propagates(tmp_path: Path, monkeypatch):
    """**第二条**开连接失败 ⇒ 上抛。

    注入用「第 N 次调用才失败」而非「全部调用都失败」：``_basic_checks_node`` 里第一次
    ``get_connection``（读 chapters 行）外面另有一层 ``except Exception: pass``
    （本组**未改**，见汇报第 6 节），全部失败会被那一层先吞掉、压根到不了本用例守的
    这一处。第 2 次调用即读 ``projects.word_band_json`` 的 ``conn2``。
    """
    from tests.unit.neutral_prose import neutral_prose

    db_path = _migrated(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, neutral_prose(2000))

    real_get_connection = crp.get_connection
    calls = {"n": 0}

    def _maybe_boom(db_path_arg):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_get_connection(db_path_arg)

    monkeypatch.setattr(crp, "get_connection", _maybe_boom)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        crp._basic_checks_node(_review_ctx(db_path, cid))


def test_no_connection_leaked_on_propagate(tmp_path: Path, monkeypatch):
    """上抛路径也要归还连接（本组三处都在 ``except`` 里手动 ``close()``）。

    Windows 上泄漏的句柄会锁住临时库文件；本例用「连接对象被 GC 后仍可删除文件」来判。
    """
    db_path = _migrated(tmp_path)
    pid = _insert_project(db_path)
    _patch_connect(monkeypatch, pi, "style_constraints_id", "database is locked")
    with pytest.raises(sqlite3.OperationalError):
        pi.load_project_style_constraints(str(db_path), pid)

    _patch_connect(monkeypatch, sps, "chapter_scene_plans", "database is locked")
    with pytest.raises(sqlite3.OperationalError):
        sps.load_scene_plan(str(db_path), "ch_1")

    gc.collect()
    for suffix in ("", "-wal", "-shm"):
        leftover = tmp_path / f"e4.db{suffix}"
        if leftover.exists():
            leftover.unlink()  # 文件删得掉 ⇒ 没有活着的句柄
