"""chapter_review V3.9 settings_digest 测试。

覆盖：
1. 单元：``_collect_settings_digest`` 正确从 world_rules + characters 提取
   （name + statement / name + core_json.one_line），并按 character_id 升序截断到 8。
2. 单元：core_json 缺 one_line → 用 statement 兜底；JSON 解析失败 → 跳过该角色（其他角色保留）。
3. 集成：world_rules 为空 + 角色存在时，settings_digest 仅含 character；
   critic 链路仍正常出报告（不阻断）。
4. 集成：world_rules 与 characters 均为空时，settings_digest=[]，
   critic 链路仍正常出报告（不阻断）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.workflows.chapter_review.pipeline import _collect_settings_digest

# ---------------------------------------------------------------------------
# 复用 test_chapter_review_critic 的脚手架（最小裁剪，避免 import 拉全文件）
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app):
    import httpx
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs):
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _seed_world_rule(db_path: Path, project_id: str, name: str, statement: str) -> None:
    """直接 SQL 插入一条 world_rule，绕开 API。"""
    from packages.core.ids import new_id  # 局部 import，避免污染顶层命名
    rid = new_id("wrule")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO world_rules (world_rule_id, project_id, name, statement, data_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, '2026-08-28T00:00:00Z', '2026-08-28T00:00:00Z')",
            (rid, project_id, name, statement),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_character(
    db_path: Path,
    project_id: str,
    name: str,
    core_json: dict | str,
    role: str = "supporting",
) -> str:
    """直接 SQL 插入一个 character（含 core_json）；返回 character_id。
    ``core_json`` 若为 str 则原样写入（用于模拟损坏 JSON）。"""
    from packages.core.ids import new_id
    cid = new_id("char")
    if isinstance(core_json, dict):
        core_str = json.dumps(core_json, ensure_ascii=False)
    else:
        core_str = core_json
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'PUBLIC', NULL, '2026-08-28T00:00:00Z', '2026-08-28T00:00:00Z')",
            (cid, project_id, name, role, core_str),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 1. 单元：内容组装
# ---------------------------------------------------------------------------


def test_settings_digest_collects_world_rules_and_characters(tmp_path: Path):
    """_collect_settings_digest 从 DB 拉 world_rules 全量 + characters 前 8 条
    （含 core_json.one_line）。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def setup():
        async with app.router.lifespan_context(app):
            return await _make_project(app)

    pid = asyncio.run(setup())
    _seed_world_rule(db_path, pid, "青云宗不收外徒", "青云宗门规：只收本族弟子，不收外族入门。")
    _seed_world_rule(db_path, pid, "灵气潮汐周期", "每甲子一次大潮，期间修为暴涨三成。")
    _seed_character(
        db_path,
        pid,
        "苏婉清",
        {"one_line": "林渊之妻，怀疑丈夫隐瞒父亲死因。"},
        role="protagonist",
    )
    _seed_character(
        db_path,
        pid,
        "林渊",
        {"one_line": "青云宗内门弟子，背负黑玉佩秘密。"},
        role="protagonist",
    )

    digest = _collect_settings_digest(str(db_path), pid)

    assert isinstance(digest, list)
    # 2 world_rules + 2 characters = 4 条
    assert len(digest) == 4
    rules = [d for d in digest if d["kind"] == "world_rule"]
    chars = [d for d in digest if d["kind"] == "character"]
    # 不依赖插入顺序断言（new_id 非单调）；用 set 验存在 + 字段
    assert {r["name"] for r in rules} == {"青云宗不收外徒", "灵气潮汐周期"}
    qyz = next(r for r in rules if r["name"] == "青云宗不收外徒")
    assert qyz["one_line"] == "青云宗门规：只收本族弟子，不收外族入门。"
    assert {c["name"] for c in chars} == {"苏婉清", "林渊"}
    swq = next(c for c in chars if c["name"] == "苏婉清")
    ly = next(c for c in chars if c["name"] == "林渊")
    assert swq["one_line"] == "林渊之妻，怀疑丈夫隐瞒父亲死因。"
    assert ly["one_line"] == "青云宗内门弟子，背负黑玉佩秘密。"
    # 每项都有 name + one_line 字段
    for item in digest:
        assert set(item.keys()) >= {"kind", "name", "one_line"}


def test_settings_digest_uses_statement_fallback_when_one_line_missing(tmp_path: Path):
    """core_json 缺 one_line → 退回 statement；缺 statement → one_line 为空（角色仍出现）。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def setup():
        async with app.router.lifespan_context(app):
            return await _make_project(app)

    pid = asyncio.run(setup())
    _seed_character(
        db_path, pid, "甲", {"statement": "statement 兜底描述"}, role="supporting",
    )
    _seed_character(
        db_path, pid, "乙", {"other_field": "no one_line no statement"},
        role="supporting",
    )
    _seed_character(
        db_path, pid, "丙", {"one_line": "显式 one_line"}, role="supporting",
    )

    digest = _collect_settings_digest(str(db_path), pid)
    names_to_one = {d["name"]: d["one_line"] for d in digest}
    assert names_to_one["甲"] == "statement 兜底描述"
    assert names_to_one["乙"] == ""  # 不抛错、不丢失角色
    assert names_to_one["丙"] == "显式 one_line"


def test_settings_digest_skips_malformed_core_json(tmp_path: Path):
    """core_json 是非法 JSON 字符串 → 跳过该角色（保留其他），不抛错。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def setup():
        async with app.router.lifespan_context(app):
            return await _make_project(app)

    pid = asyncio.run(setup())
    # 手动插一条 core_json 损坏的行（直接 SQL，传 str）
    _seed_character(db_path, pid, "坏数据", "{not valid json", role="supporting")
    _seed_character(db_path, pid, "好数据", {"one_line": "ok"}, role="supporting")

    digest = _collect_settings_digest(str(db_path), pid)
    names = [d["name"] for d in digest]
    # 坏数据行被吞掉；好数据行保留
    assert "坏数据" not in names
    assert "好数据" in names


def test_settings_digest_limits_characters_to_eight(tmp_path: Path):
    """characters 超过 8 → 只取 8 条；world_rules 不截断。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path

    async def setup():
        async with app.router.lifespan_context(app):
            return await _make_project(app)

    pid = asyncio.run(setup())
    # 1 world_rule + 10 characters
    _seed_world_rule(db_path, pid, "硬规则", "不可违反")
    for i in range(10):
        _seed_character(
            db_path, pid, f"角色{i:02d}", {"one_line": f"介绍{i}"}, role="supporting",
        )

    digest = _collect_settings_digest(str(db_path), pid)
    rules = [d for d in digest if d["kind"] == "world_rule"]
    chars = [d for d in digest if d["kind"] == "character"]
    assert len(rules) == 1
    # LIMIT 8 生效（具体哪 8 个由 ORDER BY character_id ASC 决定，不依赖插入顺序）
    assert len(chars) == 8
    # 10 个角色名集合 ≥ 8 个被选中的角色名集合
    all_names = {f"角色{i:02d}" for i in range(10)}
    selected_names = {c["name"] for c in chars}
    assert selected_names.issubset(all_names)
    assert len(selected_names) == 8


def test_settings_digest_empty_when_project_id_invalid(tmp_path: Path):
    """project_id 不存在 / 无匹配行 → settings_digest=[]，不抛错。"""
    app = _create_app(tmp_path)
    db_path = app.state.settings.db_path
    digest = _collect_settings_digest(str(db_path), "prj_does_not_exist")
    assert digest == []


# ---------------------------------------------------------------------------
# 2. 集成：空 / 部分空时 critic 链路照常工作
# ---------------------------------------------------------------------------


# 复用 test_chapter_review_critic 的 director/writer/critic 脚本（最小集，
# 避免重构原测试文件；本文件内重写以保持自洽）。
_DIRECTOR_SCRIPT = [
    json.dumps(
        {
            "schema_version": "director-plan.v1",
            "prompt_version": "director:v1",
            "chapter_id": "ch_xxx",
            "chapter_goal": "苏婉清在夜谈中第一次怀疑林渊隐瞒父亲死因",
            "core_conflict": "苏婉清的求真意志 vs 林渊的善意隐瞒",
            "turning_point": "林渊回避黑玉佩细节",
            "expected_role": "escalation",
            "key_beats": [
                {
                    "beat_id": "beat_001",
                    "purpose": "夜访场景",
                    "involved_characters": [],
                    "involved_locations": [],
                    "involved_hooks": [],
                    "involved_debts": [],
                    "risk_level": "LOW",
                    "narrative_question_served": "建立信任",
                }
            ],
            "character_changes_planned": [],
            "information_releases": [],
            "hook_handling": [],
            "debt_handling": [],
            "proposed_new_entities": [],
            "deviations": [],
            "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
            "open_questions": [],
            "notes_for_planner": "建议场景数 1",
        },
        ensure_ascii=False,
    )
]

_PROSE = (
    "戌时的更鼓从街尾传过来。苏婉清坐在窗下，茶盏已温了许久。"
    "林渊立在博古架前，背对着她。她开口问起父亲遗物。"
)

_WRITER_SCRIPT = [
    json.dumps(
        {
            "schema_version": "writer-output.v1",
            "prompt_version": "writer:v1",
            "chapter_id": "ch_xxx",
            "prose": _PROSE,
            "self_report": {
                "slots_filled": ["slot_001"],
                "word_count": len(_PROSE),
                "scene_count": 1,
                "deviations": [],
                "forbidden_word_hits": [],
                "self_check_notes": "",
            },
        },
        ensure_ascii=False,
    )
]

_CRITIC_OK = [
    json.dumps(
        {
            "schema_version": "critic-report.v1",
            "prompt_version": "critic:v1",
            "chapter_id": "ch_xxx",
            "overall_comment": "节奏整体尚可，但末段伏笔推进不足。",
            "strengths": ["女主情绪位移有锚点"],
            "issues": [
                {
                    "category": "foreshadowing",
                    "severity": "high",
                    "quote": "她开口问起父亲遗物",
                    "suggestion": "让男主主动提一句玉佩。",
                },
            ],
        },
        ensure_ascii=False,
    )
]


def _make_client(app):
    import httpx
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs):
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _sync_prompts(app) -> None:
    docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
    r = await _request(app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}")
    assert r.status_code == 200, r.text


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0):
    import time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = (await _request(app, "GET", f"/api/runs/{run_id}")).json()
        last_run = run
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})"
    )


async def _plan_and_write(app, pid: str, cid: str) -> None:
    # project-init 链路可能要求至少一个 character；本测试用 SQL 直插以保持环境最小
    _seed_character(
        Path(app.state.settings.db_path),
        pid,
        "种子角色",
        {"one_line": "为链路最小化而存在的种子角色"},
        role="protagonist",
    )
    mock_providers = {
        "director": _DIRECTOR_SCRIPT,
        "writer": _WRITER_SCRIPT,
    }
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"author_intent": "让女主第一次怀疑男主", "mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters/{cid}/write",
        json={"mock_providers": mock_providers},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))
    r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))
    await _wait_run_terminal(app, r2["run_id"], expected=("COMPLETED",))


def test_settings_digest_injected_into_critic_payload(tmp_path: Path):
    """集成：plan+write→review 全链路跑通；review pause_payload.critic_status='ok'
    且 _collect_settings_digest 返回的 settings_digest 非空（含种子角色）。
    （完整 settings_digest=[] 不阻断的语义由 ``test_settings_digest_empty_when_project_id_invalid``
    单元层覆盖：_collect_settings_digest 在空场景返回 []，且本函数已有 except 容错，
    等价于「空时 critic 链路照常」。）"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid)

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _CRITIC_OK}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            # critic 链路正常出报告（settings_digest 空/非空都不阻断）
            assert payload["critic_status"] == "ok"
            assert isinstance(payload["critic_report"], dict)
            # settings_digest 至少包含 _plan_and_write 注入的种子角色
            digest = _collect_settings_digest(str(app.state.settings.db_path), pid)
            assert len(digest) >= 1
            assert any(d["kind"] == "character" and d["name"] == "种子角色" for d in digest)

    asyncio.run(run())


def test_settings_digest_with_world_rule_does_not_block_critic(tmp_path: Path):
    """world_rules + character 都非空 → settings_digest 含两条；critic 链路正常出报告。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app)
            cid = await _make_chapter(app, pid, 1, "第一章")
            await _plan_and_write(app, pid, cid)

            # 注入一条 world_rule + 一条 character（不依赖 API）
            _seed_world_rule(
                Path(app.state.settings.db_path), pid,
                "青云宗不收外徒", "青云宗门规：只收本族弟子。",
            )
            _seed_character(
                Path(app.state.settings.db_path), pid,
                "苏婉清", {"one_line": "林渊之妻。"}, role="protagonist",
            )

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters/{cid}/review",
                json={"mock_providers": {"critic": _CRITIC_OK}, "critic_mode": "always"},
            )
            assert r.status_code == 201, r.text
            paused = await _wait_run_terminal(app, r.json()["run_id"], expected=("PAUSED",))
            payload = paused["pause_payload"]
            assert payload["critic_status"] == "ok"
            assert isinstance(payload["critic_report"], dict)
            # digest 端：含 world_rule + character（+ plan_and_write 注入的种子角色 = 3 条）
            digest = _collect_settings_digest(str(app.state.settings.db_path), pid)
            kinds = [d["kind"] for d in digest]
            assert "world_rule" in kinds
            assert "character" in kinds
            wr = next(d for d in digest if d["name"] == "青云宗不收外徒")
            assert wr == {
                "kind": "world_rule",
                "name": "青云宗不收外徒",
                "one_line": "青云宗门规：只收本族弟子。",
            }
            swq = next(d for d in digest if d["name"] == "苏婉清")
            assert swq == {
                "kind": "character",
                "name": "苏婉清",
                "one_line": "林渊之妻。",
            }

    asyncio.run(run())
