"""chapter_write 写路径的 **DB 降级范围** 契约（2026-10-16 批次十 / E5）。

欠账形状
--------
裸 ``except sqlite3.OperationalError`` 把「运行时故障」当成「老库缺表」静默吞掉。
**危害不是崩溃，是降级被当成正常**：作者以为约束生效、产出却不按约束，且零告警。

本组（chapter_write）是**写路径为主**，因此价值最高——``save_draft`` 包在 try 里的
是真正的 ``UPDATE``，而 WAL 单写者下并发写 / 嵌套另开连接写库会在
``busy_timeout`` 5s 后抛 ``database is locked``（AGENTS 坑区 M1-a 实证）。

本文件覆盖的 5 处（每处一正反一对）
----------------------------------
| 站 | 被包住的语句 | 静默吞掉锁的后果 |
|---|---|---|
| ``writer._latest_draft_text`` | ``SELECT content FROM drafts`` | ``draft_text`` 空 ⇒ **revise 静默退化为 write 整章重写**，且保真闸门因 ``upstream`` 空直接 return ⇒ **闸门同时解除** |
| ``save_draft._save_draft_node`` | ``UPDATE chapters SET plan_json``（**写**） | 偏差注记没落库被读成「本章没触发 condense 偏差」 |
| ``scene_planner``（canon 读） | ``SELECT ... FROM reference_canons`` | emotion_curve / payoff_list 从模型视野消失 |
| ``scene_planner``（world_rules 读） | ``SELECT ... FROM world_rules`` | **项目级硬设定**（原著要素锁等）从模型视野消失 |
| ``pipeline._writer_node``（ai_call_logs 读） | ``SELECT model_id, prompt_version`` | 真实模型调用在 ``drafts.model_id`` 被记成 ``mock/mock``（审计面记了一条不存在的记录） |

**保留原样的站**（有理由，见本文件末尾常量 ``_PRESERVED_SITES`` 的引用文档）：
``pipeline._scene_planner_node`` 的 4 处审计键读取（``project_id`` / canon audit）——
它们只喂可观测键，且被**显式声明过宽的**外层契约包着（docstring 第 4 条「任何异常均降级」）。

判别
----
* 撤任一收窄（改回裸 ``except sqlite3.OperationalError``）⇒ 对应「必须上抛」用例红。
* 正反两例只差**异常消息**：``no such table`` 降级 / ``database is locked`` 上抛。
* 故障注入**按 SQL 定位**（``_SqlTripwire`` 的 needle）而不是「第几次 execute」——
  否则失败点会漂移到更靠前的语句，测出来的是另一个原因（本仓已登记的失效家族：
  「夹具自带噪声 ⇒ 失败点漂移」）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_write import pipeline as cw_pipeline
from packages.workflows.chapter_write import save_draft as cw_save_draft
from packages.workflows.chapter_write import scene_planner as cw_scene_planner
from packages.workflows.chapter_write import writer as cw_writer
from packages.workflows.chapter_write.save_draft import _save_draft_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

#: 「结构缺失」样例消息——必须**继续降级**（老库 / 迁移未跑）。
_SCHEMA_MESSAGES = ("no such table: nope_t", "no such column: nope_col")
#: 「运行时故障」样例消息——必须**上抛**，不得静默降级。
_FAULT_MESSAGES = (
    "database is locked",
    "disk I/O error",
    "database disk image is malformed",
    "attempt to write a readonly database",
)


# ---------------------------------------------------------------------------
# 故障注入：按 SQL 定位的 tripwire
# ---------------------------------------------------------------------------


class _SqlTripwire:
    """包住真连接：**只有** SQL 命中 ``needle`` 的那条才抛 ``OperationalError``。

    定位到语句而不是定位到「第 N 次 execute」：后者会让故障点在夹具里随代码顺序
    漂移（本仓已登记「夹具自带噪声 ⇒ 失败点漂移」——测到了机制但不是想测的那个）。
    其余 SQL 一律透传真连接，故被测站之外的行为保持真实。
    """

    def __init__(self, conn: sqlite3.Connection, needle: str, message: str) -> None:
        self._conn = conn
        self._needle = needle
        self._message = message

    def execute(self, sql: str, *args, **kwargs):  # noqa: ANN002, ANN003
        if self._needle in sql:
            raise sqlite3.OperationalError(self._message)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):  # noqa: ANN001
        return getattr(self._conn, name)


def _tripwire_get_connection(real, needle: str, message: str):  # noqa: ANN001, ANN202
    """返回一个 ``get_connection`` 替身：只在 ``needle`` 那条 SQL 上炸。"""

    def _get_connection(db_path):  # noqa: ANN001, ANN202
        return _SqlTripwire(real(db_path), needle, message)

    return _get_connection


class _BoomConn:
    """整条连接都炸（用于整段都在 try 内的站，如 ``_latest_draft_text``）。"""

    def __init__(self, message: str) -> None:
        self._message = message

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise sqlite3.OperationalError(self._message)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "e5.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words,
                                  status, created_at, updated_at)
            VALUES (?, 'p', NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path, project_id: str, *, plan_json: str = "{}", status: str = "PLANNED"
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,
                                  status, visibility, who_knows,
                                  created_at, updated_at)
            VALUES (?, ?, 1, 't', ?, ?, 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, plan_json, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _over_band_ctx(db_path: Path, chapter_id: str, prose: str = "中" * 5500) -> dict:
    """``condense_rounds`` 耗尽 + 超带 ⇒ 命中 save_draft 的 deviations 注记分支。"""
    return {
        "db_path": str(db_path),
        "chapter_id": chapter_id,
        "run_id": "run_e5",
        "writer_output": {
            "prose": prose,
            "prompt_version": "writer:v1",
            "self_report": {"word_count": 3008},
        },
        "polished_prose": prose,
        "length_check_passed": False,
        "length_report": {
            "visible_chars": 5500,
            "target": 3000,
            "band_low": 2550,
            "band_high": 3449,
            "status": "over",
            "within_band": False,
            "deviation_pct": 83.3,
        },
        "condense_rounds": 2,
        "writer_model_id": "mock/mock",
    }


def _writer_ctx(db_path: Path, chapter_id: str) -> dict:
    """驱动 ``_writer_node`` 的最小 ctx（正文落在带内，避免触发欠带重写）。"""
    return {
        "db_path": str(db_path),
        "run_id": "run_e5",
        "chapter_id": chapter_id,
        "scene_plan": {"scenes": []},
        "loaded_plan": {"chapter_goal": "测试", "expected_word_count": 3000},
        "mock_providers": {"writer": ["{any}"]},
        "fresh_write": False,
        "model_overrides": {},
        "_current_node_run_id": "nrun_e5",
        "target_word_count": 3000,
    }


def _fake_writer_run_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换 run_agent：返回合规 writer-output.v1，零真实 LLM 调用。"""

    def fake_run_agent(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": "ch_e5",
            "prose": "中" * 3000,  # 3000 ∈ 带内（2550~3449）⇒ 不触发欠带重写
            "self_report": {
                "slots_filled": [],
                "word_count": 3000,
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        }

    monkeypatch.setattr(
        "packages.workflows.chapter_write.pipeline.run_agent",
        fake_run_agent,
        raising=True,
    )


# ---------------------------------------------------------------------------
# 站 1：writer._latest_draft_text（revise → write 的静默翻转源头）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", _SCHEMA_MESSAGES)
def test_latest_draft_text_missing_schema_still_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """极老库缺 drafts 表 / 列 → 仍返回空串，不阻断 writer run（docstring 原口径）。"""
    monkeypatch.setattr(
        cw_writer, "get_connection", lambda *_a, **_k: _BoomConn(message)
    )
    assert cw_writer._latest_draft_text(str(tmp_path / "x.db"), "ch_1") == ""


@pytest.mark.parametrize("message", _FAULT_MESSAGES)
def test_latest_draft_text_runtime_fault_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """锁 / I/O / 损坏 / 只读必须上抛。

    吞掉的后果不是「少一份上下文」而是**行为翻转**：``pipeline.py:324`` 的
    ``draft_text and revision_note`` 判定会把 revise 降级成 write（整章重写），
    且 ``revision_fidelity`` 闸门因 ``upstream`` 为空直接 return（闸门同时解除）。
    """
    monkeypatch.setattr(
        cw_writer, "get_connection", lambda *_a, **_k: _BoomConn(message)
    )
    with pytest.raises(sqlite3.OperationalError, match=message.split(":")[0]):
        cw_writer._latest_draft_text(str(tmp_path / "x.db"), "ch_1")


# ---------------------------------------------------------------------------
# 站 2：save_draft._save_draft_node（**写**路径，本组风险最高）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", _SCHEMA_MESSAGES)
def test_save_draft_missing_schema_still_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """缺表 / 缺列 → deviations 注记放弃，草稿照常落库（原注释主张的降级）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    monkeypatch.setattr(
        cw_save_draft,
        "get_connection",
        _tripwire_get_connection(
            get_connection, "SELECT plan_json FROM chapters", message
        ),
    )

    out = _save_draft_node(_over_band_ctx(db_path, cid))

    assert out["chapter_id"] == cid
    conn = get_connection(db_path)
    try:
        draft = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ?", (cid,)
        ).fetchone()
        plan = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert draft is not None, "结构缺失时应降级但不阻断：草稿仍须落库"
    # 注记确实没写进去（这正是「缺表」的口径：没表就没法写注记，但草稿要留）
    assert "deviations" not in json.loads(plan["plan_json"] or "{}")


@pytest.mark.parametrize("message", _FAULT_MESSAGES)
def test_save_draft_write_fault_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """UPDATE 撞上锁 → 必须上抛，且**不得**把草稿落库。

    静默吞掉会让「偏差注记没落库」被读成「本章没触发 condense 偏差」——
    而下游评审核销 W-LEN 意见正是依据这条注记。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    monkeypatch.setattr(
        cw_save_draft,
        "get_connection",
        _tripwire_get_connection(
            get_connection, "SELECT plan_json FROM chapters", message
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match=message.split(":")[0]):
        _save_draft_node(_over_band_ctx(db_path, cid))

    conn = get_connection(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE chapter_id = ?", (cid,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert count == 0, "DB 故障时不得继续落库（否则注记丢失被读成「无偏差」）"


# ---------------------------------------------------------------------------
# 站 3：scene_planner · reference_canon 读
# ---------------------------------------------------------------------------


def _scene_planner_db(tmp_path: Path) -> tuple[Path, str]:
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    return db_path, cid


@pytest.mark.parametrize("message", _SCHEMA_MESSAGES)
def test_scene_planner_missing_schema_degrades_to_no_canon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """缺表 / 缺列 → 视为无 canon，不注入 reference_canon 键（原注释主张）。"""
    db_path, cid = _scene_planner_db(tmp_path)
    monkeypatch.setattr(
        cw_scene_planner,
        "get_connection",
        _tripwire_get_connection(get_connection, "FROM reference_canons", message),
    )
    payload = cw_scene_planner._collect_scene_planner_inputs(str(db_path), cid, {})
    assert "reference_canon" not in payload
    assert "_reference_canon_consumed" not in payload


@pytest.mark.parametrize("message", _FAULT_MESSAGES)
def test_scene_planner_canon_fault_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """canon 读取撞故障 → 上抛。

    静默吞掉会让 emotion_curve（张力曲线）与 payoff_list（Scene 级爽点排布）
    从模型视野里消失，而作者以为「这章按参照书的爽点节奏写」——零告警。
    与批次九 ``_genre_pack_excerpt`` 的收窄理由同形。
    """
    db_path, cid = _scene_planner_db(tmp_path)
    monkeypatch.setattr(
        cw_scene_planner,
        "get_connection",
        _tripwire_get_connection(get_connection, "FROM reference_canons", message),
    )
    with pytest.raises(sqlite3.OperationalError, match=message.split(":")[0]):
        cw_scene_planner._collect_scene_planner_inputs(str(db_path), cid, {})


# ---------------------------------------------------------------------------
# 站 4：scene_planner · world_rules 读（项目级硬设定）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", _SCHEMA_MESSAGES)
def test_scene_planner_missing_world_rules_schema_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """缺表 / 缺列 → world_rules_relevant 保留空列表，不阻断装配（原注释主张）。"""
    db_path, cid = _scene_planner_db(tmp_path)
    monkeypatch.setattr(
        cw_scene_planner,
        "get_connection",
        _tripwire_get_connection(get_connection, "FROM world_rules", message),
    )
    payload = cw_scene_planner._collect_scene_planner_inputs(str(db_path), cid, {})
    assert payload["world_state_excerpts"]["world_rules_relevant"] == []


@pytest.mark.parametrize("message", _FAULT_MESSAGES)
def test_scene_planner_world_rules_fault_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
):
    """world_rules 读取撞故障 → 上抛。

    静默吞掉的后果是**项目级硬设定**（原著要素锁等）从模型视野消失，
    正文违反硬约束而无人知情——比 canon 更严重（canon 是节奏参考，硬设定是约束）。
    """
    db_path, cid = _scene_planner_db(tmp_path)
    monkeypatch.setattr(
        cw_scene_planner,
        "get_connection",
        _tripwire_get_connection(get_connection, "FROM world_rules", message),
    )
    with pytest.raises(sqlite3.OperationalError, match=message.split(":")[0]):
        cw_scene_planner._collect_scene_planner_inputs(str(db_path), cid, {})


# ---------------------------------------------------------------------------
# 站 5：pipeline._writer_node · ai_call_logs 读（模型溯源）
# ---------------------------------------------------------------------------


def test_writer_node_missing_ai_call_logs_schema_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """无 ai_call_logs 表（极老库）→ model_id / prompt_version 为 None（原口径）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _fake_writer_run_agent(monkeypatch)
    monkeypatch.setattr(
        cw_pipeline,
        "get_connection",
        _tripwire_get_connection(
            get_connection, "FROM ai_call_logs", "no such table: ai_call_logs"
        ),
    )

    out = cw_pipeline._writer_node(_writer_ctx(db_path, cid))

    assert out["writer_model_id"] is None
    assert out["writer_prompt_version"] is None


def test_writer_node_ai_call_logs_fault_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """ai_call_logs 读取撞锁 → 上抛。

    静默吞掉会让 save_draft 取不到 model_id 而写 ``mock/mock``
    （``save_draft.py`` 的 ``ctx.get("writer_model_id") or "mock/mock"``）
    ⇒ **真实模型调用在 drafts 审计面被记成 mock**，prompt_version 另降级到
    ACTIVE 标签。审计面记一条不存在的记录，与 fast_mode 把预置批准记成 "human"
    同族——同一条硬约束的两个实例。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _fake_writer_run_agent(monkeypatch)
    monkeypatch.setattr(
        cw_pipeline,
        "get_connection",
        _tripwire_get_connection(get_connection, "FROM ai_call_logs", "database is locked"),
    )

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        cw_pipeline._writer_node(_writer_ctx(db_path, cid))
