"""V3.9 批次 2.1：token 预算真兜底（``_assembly_meta`` + 预算参数化）测试。

覆盖：
- ``_estimate_tokens`` 口径 == preview 展示口径（单点提供）；
- ``_assembly_meta`` 三键语义：小 payload 不超标 / 大 payload 超标置位 / 幂等（不算自身）；
- 三个 build_* 入口的 payload 都带 ``_assembly_meta``；
- 超预算**不阻断也不改写**已装配内容（只置标记）；
- preview ``_TOKEN_BUDGET`` 与 ``_assembly_meta.token_budget`` 同源。

2026-10-15 批次（六）G9 新增：**告警接线**的判别用例（§5）。
此前 ``token_budget_exceeded`` 的全仓唯一读者就是本文件——「数据源存在 ≠ 已进装配」。
接线后它多了一个生产消费方（装配期 ``WARNING`` 日志），本节用例守的是**那个消费方**，
不是「meta 里有这个键」——后者恒真、测不到任何机制（坑区「夹具永远不触发闸门 ⇒
空断言」/「断言了结果 ≠ 判别了机制」）。故每条告警用例都配正反一对：真超预算必发、
正常装配必不发。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from packages.core.context_engine import preview as preview_mod
from packages.core.context_engine.builders import (
    build_director_input,
    build_observer_input,
    build_writer_input,
)
from packages.core.context_engine.builders_common import (
    _ASSEMBLY_TOKEN_BUDGET,
    _attach_assembly_meta,
    _estimate_tokens,
)

# 告警标识从**定义它的子模块**直取，不走包门面：门面 ``__all__`` 只保留「有生产消费方
# 的名字」（AGENTS.md 硬规则 1），一个只被本测试引用的标识不该为此进门面。
from packages.core.context_engine.builders_common.common import _ASSEMBLY_BUDGET_WARN_TAG
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 告警 logger 名（``common.py`` 的 ``logging.getLogger(__name__)``）。断言只收这一条
# logger，避免「别的模块也发过含同名字样的 warning」把用例撑绿。
_WARN_LOGGER = "packages.core.context_engine.builders_common.common"


def _warn_records(caplog) -> list[str]:
    """本模块 logger 发出的、含告警标识的日志消息原文列表。"""
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == _WARN_LOGGER and _ASSEMBLY_BUDGET_WARN_TAG in rec.getMessage()
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1, *, plan_json: str = "{}") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, f"第{number}章", plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(
    db_path: Path, project_id: str, name: str, core_json: str = "{}",
    *, inject_mode: str = "auto",
) -> str:
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                    visibility, who_knows, inject_mode, created_at, updated_at)
            VALUES (?, ?, ?, 'supporting', ?, 'VISIBLE', NULL, ?, ?, ?)
            """,
            (cid, project_id, name, core_json, inject_mode, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1. 估算口径
# ---------------------------------------------------------------------------


def test_estimate_tokens_matches_preview_estimator():
    """``_estimate_tokens`` 与 preview ``_token_estimate`` 逐值一致（单点口径）。"""
    for value in ({}, [], {"a": "中文字符串"}, {"k": ["x" * 100]}):
        assert _estimate_tokens(value) == preview_mod._token_estimate(value)
    # 口径：len(json.dumps(ensure_ascii=False)) // 4，至少 1
    payload = {"a": "字" * 10}
    assert _estimate_tokens(payload) == len(json.dumps(payload, ensure_ascii=False)) // 4
    assert _estimate_tokens({}) == 1  # 空对象仍给 1，便于 UI 区分 0 / 1


def test_preview_budget_and_meta_budget_share_one_constant():
    """preview 展示预算与 ``_assembly_meta.token_budget`` 同源。"""
    assert preview_mod._TOKEN_BUDGET == _ASSEMBLY_TOKEN_BUDGET == 8000


# ---------------------------------------------------------------------------
# 2. `_assembly_meta` 语义
# ---------------------------------------------------------------------------


def test_assembly_meta_small_payload_not_exceeded():
    """小 payload → exceeded=False，estimate 与 token_budget 齐备。"""
    payload = {"agent": "writer", "chapter": {"chapter_id": "ch_1"}}

    returned = _attach_assembly_meta(payload)

    assert returned is payload
    meta = payload["_assembly_meta"]
    assert meta["estimate_tokens"] == _estimate_tokens({"agent": "writer", "chapter": {"chapter_id": "ch_1"}})
    assert meta["token_budget"] == _ASSEMBLY_TOKEN_BUDGET
    assert meta["token_budget_exceeded"] is False
    assert "summary_truncated" not in meta  # 非 director 路径不写该键


def test_assembly_meta_large_payload_marks_exceeded_without_rewriting():
    """大 payload → exceeded=True，且**不改写**任何既有键（只加 meta）。"""
    body = {"filler": "x" * (_ASSEMBLY_TOKEN_BUDGET * 4 + 100)}
    payload = dict(body)

    _attach_assembly_meta(payload)

    assert payload["_assembly_meta"]["token_budget_exceeded"] is True
    # 内容零改写：除 meta 外逐字段一致
    assert {k: v for k, v in payload.items() if k != "_assembly_meta"} == body


def test_assembly_meta_idempotent_excludes_itself():
    """重复附加不累计自身体积：第二次的 estimate 与第一次相同。"""
    payload = {"agent": "director", "filler": "y" * 4000}

    _attach_assembly_meta(payload)
    first = payload["_assembly_meta"]["estimate_tokens"]
    _attach_assembly_meta(payload)
    second = payload["_assembly_meta"]["estimate_tokens"]

    assert first == second


def test_assembly_meta_carries_summary_truncated_flag():
    """director 路径可带 ``summary_truncated``（bool 归一化）。"""
    payload = {"a": 1}

    _attach_assembly_meta(payload, summary_truncated=0)

    assert payload["_assembly_meta"]["summary_truncated"] is False


# ---------------------------------------------------------------------------
# 3. 三个 build_* 入口都带 meta
# ---------------------------------------------------------------------------


def test_all_builders_attach_assembly_meta(tmp_path: Path):
    """director / writer / observer 三入口 payload 均带 `_assembly_meta`（小 fixture 不超标）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')

    for payload in (
        build_director_input(db_path, pid, cid, "意图"),
        build_writer_input(db_path, cid, {"purpose": "场景"}),
        build_observer_input(db_path, cid),
    ):
        meta = payload["_assembly_meta"]
        assert meta["token_budget"] == _ASSEMBLY_TOKEN_BUDGET
        assert meta["token_budget_exceeded"] is False
        assert meta["estimate_tokens"] > 0


def test_writer_paged_payload_meta_reflects_trimmed_size(tmp_path: Path):
    """paged 模式：meta 在分页裁剪**之后**重算（estimate ≤ full 模式）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    for i in range(10):
        _insert_character(db_path, pid, f"角色{i}", core_json=json.dumps({"personality": "x" * 200}))

    full = build_writer_input(db_path, cid, {}, context_mode="full")
    paged = build_writer_input(db_path, cid, {}, context_mode="paged")

    assert paged["context_mode"] == "paged"
    # meta 在分页裁剪后**重算**：估算对象是最终 payload 本体（不含 meta 自身），
    # 若沿用 uncached 阶段的旧估算会与之不符（本用例即钉住「不陈旧」）。
    paged_body = {k: v for k, v in paged.items() if k != "_assembly_meta"}
    full_body = {k: v for k, v in full.items() if k != "_assembly_meta"}
    assert paged["_assembly_meta"]["estimate_tokens"] == _estimate_tokens(paged_body)
    assert full["_assembly_meta"]["estimate_tokens"] == _estimate_tokens(full_body)


# ---------------------------------------------------------------------------
# 4. 大 payload 真超标：不阻断装配
# ---------------------------------------------------------------------------


def test_director_oversized_payload_flags_without_blocking(tmp_path: Path):
    """构造超大 director payload → exceeded=True，且装配内容仍完整（不阻断 / 不裁剪）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    # 40 个角色 × ~900 字 core_json ≈ 36000 字符 ≈ 9000 token > 8000
    # （inject_mode='always'：绕过触发降级，保证大头 core_json 真的进 payload）
    for i in range(40):
        _insert_character(
            db_path, pid, f"角色{i}",
            core_json=json.dumps({"personality": "设定" * 450}, ensure_ascii=False),
            inject_mode="always",
        )

    payload = build_director_input(db_path, pid, cid, "意图")

    assert payload["_assembly_meta"]["token_budget_exceeded"] is True
    # 不阻断：40 条角色 excerpt 全在（真裁剪仍靠各字段 cap / 摘要链预算）
    assert len(payload["character_state_excerpts"]) == 40
    assert payload["story_state_snapshot"]["current_state_version"] == 0


# ---------------------------------------------------------------------------
# 5. 告警接线（2026-10-15 批次六 G9）：消费方真的看到了超预算信号
# ---------------------------------------------------------------------------
#
# 判别纪律（坑区三条同源形状，见 §5 判别要求）：
#
# - 本节**不断言**「meta 里有 ``token_budget_exceeded`` 键」——那值本来一直在，
#   断言恒真、测不到接线（对应「断言了结果 ≠ 判别了机制」）；
# - 断言对象是**消费方收到的日志记录**（发没发这条告警 + 告警里带的数字对不对）；
# - 每条「必发」用例都配一条「必不发」的反向用例，否则「无条件每次装配都发一遍
#   warning」这种把真信号淹成噪声的接线同样能骗过断言（对应「闸门断言必须配反向用例」）。
#
# 为什么还要钉「告警里的数字」：只断言「有某条含标识的日志」的话，一条恒空的
# warning（``... exceeded`` 后面什么都不带）也能过——而那正是「量具没量到目标却报告
# 量过了」的形状。故要求日志带上**本次实测**的 estimate 与预算值。

# 触发一次真超标所需的字符数（估算口径 = len(json)//4，故字符数取预算 × 4 的数倍留余量）。
_OVERSIZE_FILLER_CHARS = _ASSEMBLY_TOKEN_BUDGET * 4 + 400


def _oversized_payload() -> dict[str, object]:
    """构造一份**确定**超预算的 payload（含 ``agent`` 键，与三个装配入口同形）。"""
    return {"agent": "writer", "filler": "字" * _OVERSIZE_FILLER_CHARS}


def test_oversized_assembly_emits_budget_warning_with_real_numbers(caplog):
    """真超标 → 消费方（告警日志）收到信号，且带的是**本次实测**的数字。"""
    payload = _oversized_payload()

    with caplog.at_level(logging.WARNING):
        _attach_assembly_meta(payload)

    messages = _warn_records(caplog)
    assert len(messages) == 1, f"超预算应恰好发一条告警，实得 {messages}"
    message = messages[0]
    # 数字必须是**实测值**而非模板值：与本次装配的 estimate / 预算逐字对上。
    # （恒空或写死的 warning 会在这一条红——这就是判别点。）
    assert f"estimate_tokens={payload['_assembly_meta']['estimate_tokens']}" in message
    assert f"budget={_ASSEMBLY_TOKEN_BUDGET}" in message
    assert "agent=writer" in message
    # 前置自证：夹具确实超标（防止「量具没量到目标」——桩没进那个分支也报绿）
    assert payload["_assembly_meta"]["token_budget_exceeded"] is True
    assert payload["_assembly_meta"]["estimate_tokens"] > _ASSEMBLY_TOKEN_BUDGET


def test_small_assembly_emits_no_budget_warning(caplog):
    """反向用例：正常装配**不得**发告警（否则真信号被噪声淹没）。"""
    payload = {"agent": "writer", "chapter": {"chapter_id": "ch_1"}}

    with caplog.at_level(logging.WARNING):
        _attach_assembly_meta(payload)

    assert payload["_assembly_meta"]["token_budget_exceeded"] is False
    assert _warn_records(caplog) == []


def test_budget_warning_path_does_not_alter_assembly(caplog):
    """「只告警不阻断」：发告警的那次调用，payload 内容逐字未变、返回同一对象。"""
    payload = _oversized_payload()
    body_before = {k: v for k, v in payload.items() if k != "_assembly_meta"}

    with caplog.at_level(logging.WARNING):
        returned = _attach_assembly_meta(payload)

    assert _warn_records(caplog), "本用例的前提是告警真的发了（否则「未改写」是恒真）"
    assert returned is payload
    assert {k: v for k, v in payload.items() if k != "_assembly_meta"} == body_before


def test_director_oversized_assembly_warns_with_agent_name(tmp_path: Path, caplog):
    """生产路径（``build_director_input``）真超标 → 消费方看到告警，且带对 agent 名。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    for i in range(40):
        _insert_character(
            db_path, pid, f"角色{i}",
            core_json=json.dumps({"personality": "设定" * 450}, ensure_ascii=False),
            inject_mode="always",
        )

    with caplog.at_level(logging.WARNING):
        payload = build_director_input(db_path, pid, cid, "意图")

    messages = _warn_records(caplog)
    assert len(messages) == 1, f"director 超标应恰好发一条告警，实得 {messages}"
    assert "agent=director" in messages[0]
    assert (
        f"estimate_tokens={payload['_assembly_meta']['estimate_tokens']}" in messages[0]
    )
    # 不阻断：装配内容仍完整（与 §4 同款，只置位不改写）
    assert len(payload["character_state_excerpts"]) == 40


