"""H2 判别：用户手动新增/编辑的角色是否进 story_state 快照（2026-10-16 批次 E2）。

被测机制（逐条对应任务书）：
- ``snapshot._load_characters``（SELECT characters 表）只在两个时刻跑：
  ① ``build_initial_state``（init_genesis / 无快照兜底）
  ② ``rebuild_snapshot_collections_from_db``（``commit_delta`` 事务内）
  ⇒ **genesis 与下一次 commit 之间**手工改/加的 DB 角色不进 ``story_states``。
- ``check_state_sync`` 能否发现这类漂移（巡检网在这个方向上有没有洞）。
- 快照 characters 段的**字段完整性**（Q1）与 writer 装配路径（Q2）。

判定纪律：
- 夹具只造一个失败点（一个项目 / 一个 genesis / 一个手工角色），断言失败时
  读实际错误串确认红在快照缺席上，不是更靠前的 HTTP 层。
- 期望值独立于实现：列口径取自 ``database/migrations/0001_init.sql`` +
  ``0010_trigger_keys.sql`` 的 DDL 与 ``packages/domain/character/models.py`` 的
  枚举；快照侧形状取自 ``snapshot.py`` 模块 docstring 自述的契约。不从
  「跑一下看返回什么」得来。
- 正反对照：test 3a/3b 只差 scene_plan 一处输入，必须一个全量一个降级——
  否则「一律 xxx」的实现照样全绿。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _fetch_latest_snapshot_json,
    _make_chapter,
    _make_meta,
    _make_project,
    _request,
)

# 快照 characters 条目的自述契约（snapshot.py 模块 docstring §形状）：
# {character_id, name, current_state, knowledge, beliefs, relationships, facet}
_SNAPSHOT_CHARACTER_ENTRY_KEYS = {
    "character_id",
    "name",
    "current_state",
    "knowledge",
    "beliefs",
    "relationships",
    "facet",
}

# characters 表的定义侧列（DDL 0001_init.sql L51-64 + 0010_trigger_keys.sql L23/26）。
# 与实现无关的第三方基准——防「自指断言恒真」：断言的是「这些列在快照里没有」，
# 名单由 DDL 推导，不是从跑出来的结果反推。
_DEFINITION_SIDE_COLUMNS = (
    "role",
    "core_json",
    "visibility",
    "who_knows",
    "aliases",
    "inject_mode",
)


def _snapshot_character_ids(state: dict) -> list[str]:
    return [c.get("character_id") for c in (state.get("characters") or [])]


async def _genesis(app, pid: str, chapter_id: str) -> None:
    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chapter_id})
    assert r.status_code == 201, r.text


async def _create_char(app, pid: str, name: str, **body) -> str:
    r = await _request(app, "POST", f"/api/projects/{pid}/characters", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _commit_v2(app, pid: str, chapter_id: str) -> dict:
    """提交一个空 delta（v1 → v2）。空 delta 仍走 commit_delta 全流程，
    含 ``rebuild_snapshot_collections_from_db``——即「下一次 commit 自愈」的入口。"""
    delta_id = "dlt_probe"
    delta = {
        **_make_meta(delta_id, chapter_id, 1),
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/commits",
        json={"delta_id": r.json()["delta_id"], "workflow_run_id": "wfr_probe"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------- 1. H2 主张复现


@pytest.mark.xfail(
    strict=True,
    reason=(
        "H2 复现：快照是滞后一版的物化结果，只在 init_genesis 与 commit_delta 的 "
        "DB 权威重建两个时刻刷新 ⇒ genesis 之后手工加的角色在下一次 commit 前缺席。"
        "E2 判定**不修**（见 CHANGELOG/汇报：它不是「闺蜜变业务员」的因果链，"
        "且补写要动 state_version 语义）。strict=True：谁把它修好，本用例 XPASS 即红，"
        "提醒移除标记。"
    ),
)
def test_manual_character_is_absent_from_current_snapshot(tmp_path: Path):
    """genesis 之后手动新增的角色，在下一次 commit 之前不进当前快照。

    H2 第一半主张（「手动新增的角色进不了 story_state 快照」）——**成立**。
    对照组：genesis **之前**建的角色必须进快照——否则红在别处。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="manual_char_gap")
            chap = await _make_chapter(app, pid)
            pre_id = await _create_char(app, pid, "对照组角色")
            await _genesis(app, pid, chap)

            manual_id = await _create_char(
                app, pid, "林昭", core_json={"relationships": "与女主是闺蜜"}
            )

            # 量具自检：DB 侧确实存在（否则红的是「角色根本没建出来」）
            r = await _request(app, "GET", f"/api/projects/{pid}/characters")
            assert r.status_code == 200, r.text
            assert manual_id in [c["character_id"] for c in r.json()], "前置：手动角色必须在 DB 里"

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200, r.text
            ids = _snapshot_character_ids(r.json())

            assert pre_id in ids, f"genesis 前的角色也不在快照里，红在别处：{ids}"
            assert manual_id in ids, f"手动新增角色缺席当前快照（state_version=1）：{ids}"

    asyncio.run(run())


# ------------------------------------------------------- 2. 自愈：下一次 commit 补回


def test_next_commit_picks_up_manual_character(tmp_path: Path):
    """快照是**滞后一版**而非永久缺失：下一次 commit 的 DB 权威重建会补上。

    与 test 1 构成「缺陷边界」的一对：test 1 说它现在不在，test 2 说它在
    commit 之后在。缺任何一个都会把结论说过头（永久缺失）或说过轻（无影响）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="manual_char_selfheal")
            chap = await _make_chapter(app, pid)
            await _genesis(app, pid, chap)
            manual_id = await _create_char(app, pid, "林昭")

            # commit 前：缺席
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert manual_id not in _snapshot_character_ids(r.json()), "前置：commit 前本就不该在"

            await _commit_v2(app, pid, chap)

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200, r.text
            body = r.json()
            ids = _snapshot_character_ids(body)
            assert body["state_version"] == 2, f"版本未推进：{body['state_version']}"
            assert manual_id in ids, f"commit 后仍未补回：{ids}"

            # 落库行（绕过 API 直接读 DB 列，确认不是 API 层拼出来的）
            snap = _fetch_latest_snapshot_json(tmp_path / "novelos.db", pid)
            assert manual_id in [
                c.get("character_id") for c in (snap.get("characters") or [])
            ], "story_states 行内仍缺该角色"

    asyncio.run(run())


# ------------------------------------------- 3. Q2 决定性：writer 装配读 DB 而非快照


def _writer_char_entry(payload: dict, character_id: str) -> dict | None:
    for c in payload.get("character_state_excerpts") or []:
        if isinstance(c, dict) and c.get("character_id") == character_id:
            return c
    return None


def test_writer_assembly_sees_manual_character_though_snapshot_does_not(tmp_path: Path):
    """快照缺席，但 writer payload 仍带该角色 ⇒ H2 的**因果链**不成立。

    「快照缺它」是事实；「模型因此看不到它」不是——writer 的角色数据来自
    ``_character_state_excerpts``（直查 characters 表），不经过 story_state。
    这是任务书 Q2，决定 H2 是不是真凶。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="manual_char_writer")
            chap = await _make_chapter(app, pid)
            await _genesis(app, pid, chap)
            manual_id = await _create_char(
                app, pid, "林昭", core_json={"relationships": "与女主是闺蜜"}
            )

            # 快照侧：确实缺（test 1 已证；这里再钉一次作前置）
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert manual_id not in _snapshot_character_ids(r.json()), "前置：快照本就不该有它"

            from packages.core.context_engine.writer_input import build_writer_input

            # scene_plan 提及角色名 → 触发 full 注入；relevance_trim 关闭以隔离
            # 触发策略这一个变量。
            payload = build_writer_input(
                str(tmp_path / "novelos.db"),
                chap,
                {"purpose": "林昭登场"},
                relevance_trim=False,
            )
            entry = _writer_char_entry(payload, manual_id)
            assert entry is not None, "writer payload 里完全没有该角色（H2 因果链成立）"
            assert entry.get("core_json", {}).get("relationships") == "与女主是闺蜜", (
                f"角色圣经内容未进 writer payload：{entry}"
            )

    asyncio.run(run())


def test_writer_degrades_unmentioned_character_to_name_only(tmp_path: Path):
    """真凶的形状：**触发/相关性裁剪**把角色砍成只剩名字，而不是快照缺它。

    与 ``test_writer_assembly_sees_manual_character_though_snapshot_does_not``
    构成正反一对：两用例只差 scene_plan 是否提及该角色，其余（项目、genesis、
    角色 core_json、快照状态）逐字相同 ⇒ 红在触发裁剪这一个机制上。

    反面后果：模型只看到「林昭（supporting）」，没有职业/关系 ⇒ 自造身份
    （外部报告的现象：圣经里是闺蜜、正文里以业务员出场）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="manual_char_degrade")
            chap = await _make_chapter(app, pid)
            await _genesis(app, pid, chap)
            manual_id = await _create_char(
                app, pid, "林昭", core_json={"relationships": "与女主是闺蜜"}
            )

            from packages.core.context_engine.writer_input import build_writer_input

            db = str(tmp_path / "novelos.db")
            # 提及 → full
            said = build_writer_input(db, chap, {"purpose": "林昭登场"}, relevance_trim=False)
            entry = _writer_char_entry(said, manual_id)
            assert entry is not None and "core_json" in entry, f"提及时本应 full：{entry}"

            # 未提及 → 降级（同一角色、同一 core_json，只差是否被提及）
            unsaid = build_writer_input(db, chap, {"purpose": "无关场景"}, relevance_trim=False)
            entry2 = _writer_char_entry(unsaid, manual_id)
            assert entry2 is not None, f"未提及时条目不应消失（suppressed）：{entry2}"
            assert "core_json" not in entry2, (
                f"未提及时角色圣经仍完整注入，裁剪没生效：{entry2}"
            )
            assert entry2.get("_injection") == "summary", f"降级形态与预期不符：{entry2}"

    asyncio.run(run())


# ------------------------------------------------------- 4. Q1：快照 characters 段完整性


def test_always_injected_character_survives_relevance_trim(tmp_path: Path):
    """``inject_mode='always'`` 的角色必须扛得住相关性裁剪。

    🔒 **本用例最初是 ``xfail(strict=True)``，E2 于 2026-10-16 16:33 实测复现了一个真缺陷**：
    ``inject_mode`` 从未进 ``_character_state_excerpts`` 的 excerpt，而
    ``relevance._is_character_relevant`` 用 ``char.get('inject_mode') == 'always'`` 当
    保留逃生门 ⇒ 该分支**恒假**。后果正是外部报告的形状：角色即使显式标了
    ``inject_mode='always'``（模型文档：「无视命中，永远完整注入」），只要名字没进
    ``scene_plan.characters`` / ``plan_json`` 的结构化 involved 字段（只出现在 purpose
    这类自由文本里），仍会被砍成 ``{id, name, relevance_summary}``，``core_json``
    （角色圣经的关系）整块消失 ⇒ 模型只见名字 ⇒ 自造职业。
    **E3 在同批并发中已修**（``builders_common/excerpts.py`` 把 ``inject_mode`` 透传进
    ``full_excerpt``），故本用例由 xfail 转绿并去掉标记——strict xfail 在这里的作用
    就是「谁修好就强制翻面」，它做到了。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="always_inject_relevance")
            chap = await _make_chapter(app, pid)
            cid = await _create_char(
                app,
                pid,
                "常驻角色",
                inject_mode="always",
                core_json={"personality": "泼辣"},
            )

            from packages.core.context_engine.writer_input import build_writer_input

            db = str(tmp_path / "novelos.db")
            # 名字只出现在 purpose 自由文本里（不在 scene_plan.characters 结构化字段）
            payload = build_writer_input(db, chap, {"purpose": "常驻角色推门而入"})
            entry = _writer_char_entry(payload, cid)
            assert entry is not None, f"条目不应消失：{payload['character_state_excerpts']}"
            assert "core_json" in entry, (
                f"inject_mode='always' 仍被相关性裁剪砍掉角色圣经：{sorted(entry)}"
            )

    asyncio.run(run())


def test_snapshot_character_entry_drops_definition_side_columns(tmp_path: Path):
    """快照角色条目丢掉了全部定义侧列（core_json 被 SELECT 后显式丢弃）。

    判别面 = ``_DEFINITION_SIDE_COLUMNS``（由 DDL 推导的列全集，非手挑）。
    反向锚点：``name``/``current_state`` 必须**在**快照里——否则红在「快照整体
    没建起来」，与被测机制无关。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="snapshot_char_fields")
            chap = await _make_chapter(app, pid)
            cid = await _create_char(
                app,
                pid,
                "林昭",
                role="supporting",
                core_json={"relationships": "与女主是闺蜜"},
                visibility="PUBLIC",
                aliases=["小林"],
                inject_mode="always",
            )
            await _genesis(app, pid, chap)

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            entries = [c for c in r.json()["characters"] if c["character_id"] == cid]
            assert len(entries) == 1, f"快照里没有该角色，红在别处：{r.json()['characters']}"
            entry = entries[0]

            # 反向锚点：契约内的字段必须在
            assert _SNAPSHOT_CHARACTER_ENTRY_KEYS.issubset(set(entry)), (
                f"契约字段缺失，红在别处：{sorted(entry)}"
            )
            # 正向：定义侧列一个都不在
            leaked = [c for c in _DEFINITION_SIDE_COLUMNS if c in entry]
            assert leaked == [], f"定义侧列意外出现在快照角色条目里：{leaked} / {sorted(entry)}"

    asyncio.run(run())


def test_snapshot_relationships_come_from_relationship_table(tmp_path: Path):
    """关系信息在快照里只有一条来源：``relationships`` 表（不是 core_json）。

    与 test 4 配套：说明「圣经里写了闺蜜」若只写在 core_json，快照侧确实读不到；
    要进快照必须落 relationships 表（observer / delta 通道）。

    ⚠️ relationships **没有 POST 端点**（``routers/plots.py:222`` 注明「S1 只读」，
    只有 GET），生产上只能由 delta/write_through 写入 ⇒ 本用例直插一行模拟
    observer 写穿后的形状，列取自 ``0001_init.sql:181-192`` 的 NOT NULL 列。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="snapshot_char_rel")
            chap = await _make_chapter(app, pid)
            a = await _create_char(app, pid, "甲", core_json={"relationships": "闺蜜"})
            b = await _create_char(app, pid, "乙")

            from packages.core.db import get_connection

            conn = get_connection(str(tmp_path / "novelos.db"))
            try:
                conn.execute(
                    """
                    INSERT INTO relationships
                        (relationship_id, project_id, from_character_id,
                         to_character_id, relation_type, state_json,
                         last_state_version)
                    VALUES (?, ?, ?, ?, 'friend', '{}', 1)
                    """,
                    ("rel_probe", pid, a, b),
                )
                conn.commit()
            finally:
                conn.close()

            await _genesis(app, pid, chap)
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            entry = next(c for c in r.json()["characters"] if c["character_id"] == a)
            assert entry["relationships"], "关系表有行但快照 relationships 为空"
            assert entry["relationships"][0]["relation_type"] == "friend"

    asyncio.run(run())


# ------------------------------------------------------- 5. 巡检网：check_state_sync


def test_check_state_sync_reports_manual_character_as_only_in_db(tmp_path: Path):
    """巡检网在这个方向上**有**洞的判据：只_in_db 漂移必须被报出来。

    期望独立于实现：``check_state_sync.COLLECTIONS`` 里 characters 的
    ``db_table/db_id_column`` 是脚本自己的常量——本用例直接用该常量取 DB 侧 id 集，
    再断言它落在报告的 ``only_in_db`` 里，而不是手抄一份名单。
    """
    from scripts.check_state_sync import COLLECTIONS, collect_reports, open_readonly_db

    cfg = next(c for c in COLLECTIONS if c["name"] == "characters")
    assert cfg["db_table"] == "characters" and cfg["db_id_column"] == "character_id"

    app = _create_app(tmp_path)
    captured: dict[str, str] = {}

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="manual_char_drift")
            chap = await _make_chapter(app, pid)
            await _genesis(app, pid, chap)
            captured["pid"] = pid
            captured["cid"] = await _create_char(app, pid, "林昭")

    asyncio.run(run())
    pid = captured["pid"]
    manual_id = captured["cid"]

    conn = open_readonly_db(tmp_path / "novelos.db")
    try:
        reports = collect_reports(conn, pid)
    finally:
        conn.close()

    assert len(reports) == 1, f"巡检没覆盖到该项目：{reports}"
    rep = reports[0]
    assert rep.no_snapshot is False, "前置：项目有快照，不该落进「无快照」小节"
    chars = next(r for r in rep.results if r.name == "characters")
    assert chars.only_in_db == [manual_id], (
        f"巡检未报出手工角色的 DB/快照漂移：only_in_db={chars.only_in_db}"
    )
    assert not chars.only_in_snapshot, "不应有反向漂移"
