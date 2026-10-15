"""plot / relationships 列表端点的**脏行降级**契约（2026-10-15 批次（六））。

背景：本批给 ``GET /projects/{pid}/events`` 与 ``GET .../relationships`` 挂上
``response_model`` 之后，L1 成员实测发现一条**本批引入的新失效模式**：

    在挂 response_model 之前 ⇒ 脏数据只是「200 + 一个奇怪的形状」
    在挂 response_model 之后 ⇒ 同一行脏数据让 pydantic 抛
                                ResponseValidationError ⇒ **整个端点 500**，
                                干净行一起陪葬

根因不在契约，在 service：``_row_to_event`` / ``_row_to_relationship`` 走裸
``json.loads``，一行解析失败或形状不对（合法 JSON 但类型不符）就冒到路由层。

处置：service 侧加 ``_safe_json`` 兜底，解析失败/形状不符 ⇒ 该字段退回它的空值，
读路径继续可用。这与本仓既有约定同源（``decode_who_knows`` 的 docstring 明写
「解析失败 → None（兜底，避免污染调用方）」）。

⚠️ 本组用例守的是**降级方向**，不是「兜底存在」。反过来说：把 ``_safe_json``
换成裸 ``json.loads`` 必须让它们变红。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id


def _app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


def _seed_and_dirty(app) -> str:
    """建项目 + 一个角色，并直接往 DB 塞两行：一行干净、一行 JSON 脏。"""
    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                pid = (await c.post("/api/projects", json={"name": "脏行"})).json()["project_id"]
                char = (
                    await c.post(f"/api/projects/{pid}/characters", json={"name": "林昭"})
                ).json()["character_id"]
                return pid, char

    pid, char = None, None
    import asyncio

    pid, char = asyncio.run(go())

    conn = get_connection(app.state.settings.db_path)
    try:
        # 干净行
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json,"
            " participants_json, location_id, time_json, status, introduced_chapter_id,"
            " visibility, who_knows, description)"
            " VALUES (?,?,?,?,?,?,NULL,?,?,NULL,?,?,?)",
            (
                new_id("pev"), pid, "other", json.dumps(["因"]), "[]", "[]",
                json.dumps({"point": "夜"}), "planned", "PUBLIC", "[]", "干净行",
            ),
        )
        # 脏行：cause_json 存的是合法 JSON 但形状不对（字符串而非数组）
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json,"
            " participants_json, location_id, time_json, status, introduced_chapter_id,"
            " visibility, who_knows, description)"
            " VALUES (?,?,?,?,?,?,NULL,?,?,NULL,?,?,?)",
            (
                new_id("pev"), pid, "other", '"我是一个字符串"', "[]", "[]",
                json.dumps({"point": "晨"}), "planned", "PUBLIC", "[]", "脏行",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _seed_dirty_relationship(app, pid: str, char: str) -> None:
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "INSERT INTO relationships (relationship_id, project_id, from_character_id,"
            " to_character_id, relation_type, state_json, last_state_version,"
            " visibility, who_knows) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("rel"), pid, char, char, "ALLY", '"不是对象"', 1, "PUBLIC", "[]"),
        )
        conn.commit()
    finally:
        conn.close()


def _get(app, path: str):
    import asyncio

    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.get(path)

    return asyncio.run(go())


def test_events_list_survives_a_dirty_json_row(tmp_path: Path):
    """核心：脏行不得让整个端点 500，且**干净行仍要拿得到**。"""
    app = _app(tmp_path)
    pid = _seed_and_dirty(app)

    r = _get(app, f"/api/projects/{pid}/events")
    assert r.status_code == 200, f"一行脏数据就让整个列表 500：{r.text[:200]}"
    rows = r.json()
    assert len(rows) == 2, f"应返回 2 行（干净 + 脏），实得 {len(rows)}"
    clean = [x for x in rows if x["description"] == "干净行"]
    assert len(clean) == 1, f"干净行必须仍然可见（不能被脏行连累）：{rows}"


def test_dirty_row_degrades_to_empty_value_not_an_error(tmp_path: Path):
    """脏字段降级成它的空值（与「列本来为空」同形），而不是报错。"""
    app = _app(tmp_path)
    pid = _seed_and_dirty(app)

    rows = _get(app, f"/api/projects/{pid}/events").json()
    dirty = next(x for x in rows if x["description"] == "脏行")
    assert dirty["cause"] == [], f"cause_json 存字符串 ⇒ 应降级为 []，实得 {dirty['cause']!r}"

    clean = next(x for x in rows if x["description"] == "干净行")
    assert clean["cause"] == ["因"], f"干净行的值不得被影响：{clean['cause']!r}"


def test_relationships_list_survives_a_dirty_state_json(tmp_path: Path):
    """relationships 同款：``state_json`` 脏 ⇒ 端点仍 200，``state`` 降级为 ``{}``。"""
    import asyncio

    app = _app(tmp_path)
    pid = _seed_and_dirty(app)

    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                rows = (await c.get(f"/api/projects/{pid}/characters")).json()
                return rows[0]["character_id"]

    char = asyncio.run(go())
    _seed_dirty_relationship(app, pid, char)

    r = _get(app, f"/api/projects/{pid}/relationships")
    assert r.status_code == 200, f"脏 state_json 让整个端点 500：{r.text[:200]}"
    rows = r.json()
    assert len(rows) == 1, rows
    assert rows[0]["state"] == {}, f"应降级为 {{}}，实得 {rows[0]['state']!r}"


def test_malformed_json_text_also_degrades_instead_of_raising(tmp_path: Path):
    """不是合法 JSON 的文本（``not json``）也走同一兜底，不只挡「形状不对」。"""
    app = _app(tmp_path)
    pid = _seed_and_dirty(app)

    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json,"
            " participants_json, location_id, time_json, status, introduced_chapter_id,"
            " visibility, who_knows, description)"
            " VALUES (?,?,?,?,?,?,NULL,?,?,NULL,?,?,?)",
            (
                new_id("pev"), pid, "other", "not json at all", "[]", "[]",
                json.dumps({"point": "昏"}), "planned", "PUBLIC", "[]", "烂 JSON",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    r = _get(app, f"/api/projects/{pid}/events")
    assert r.status_code == 200, r.text[:200]
    bad = next(x for x in r.json() if x["description"] == "烂 JSON")
    assert bad["cause"] == [], bad


# ---------------------------------------------------------------------------
# 2026-10-16：relationships 的 ``who_knows`` / ``visibility`` 补上映射之后，
# **新暴露**的两个降级面。它们与上面四例**不是同一条路径**：
#
# - ``state_json`` 走 ``_safe_json``（JSON 文本 → 领域对象，失败退空值）；
# - ``who_knows`` 走 ``decode_who_knows``（JSON 文本 → list|None，失败退 None）；
# - ``visibility`` 走**裸取列**，不解析 ⇒ 它没有 JSON 兜底可言，但它有**缺列兜底**
#   （``row.keys()`` 判断，遗留防御），且它**不该**有值域兜底——
#   刻意不接 ``_validate_visibility``：读路径上一行脏 visibility 就 500，
#   正是 models.py docstring 警告的形状（裸 str 透出，由契约测试守枚举）。
#
# ⚠️ 反过来断言一下：把 ``who_knows`` 改回裸 ``json.loads`` **必须**让本节用例变红
# ——它会抛 JSONDecodeError ⇒ 整个列表 500，干净行一起陪葬。
# ---------------------------------------------------------------------------


def _seed_relationship(
    app, pid: str, rel_id: str, *, vis: str, knows: str | None, kind: str = "ALLY"
) -> None:
    """插一行 relationships，``visibility`` / ``who_knows`` 原样塞入（脏数据入口）。"""
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            "INSERT INTO relationships (relationship_id, project_id, from_character_id,"
            " to_character_id, relation_type, state_json, last_state_version,"
            " visibility, who_knows) VALUES (?,?,?,?,?,?,?,?,?)",
            (rel_id, pid, "a_from", "b_to", kind, "{}", 1, vis, knows),
        )
        conn.commit()
    finally:
        conn.close()


def _project_with_char(app) -> tuple[str, str]:
    pid = _seed_and_dirty(app)

    import asyncio

    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                rows = (await c.get(f"/api/projects/{pid}/characters")).json()
                return rows[0]["character_id"]

    return pid, asyncio.run(go())


def test_relationships_dirty_who_knows_degrades_to_null_not_500(tmp_path: Path):
    """``who_knows`` 存的不是合法 JSON ⇒ 降级为 ``null``，端点仍 200，干净行仍可见。"""
    app = _app(tmp_path)
    pid, _ = _project_with_char(app)
    _seed_relationship(app, pid, "rel_dirty_json", vis="PUBLIC", knows="not json at all")
    _seed_relationship(
        app, pid, "rel_clean", vis="RESTRICTED", knows=json.dumps(["char_z"]), kind="RIVAL"
    )

    r = _get(app, f"/api/projects/{pid}/relationships")
    assert r.status_code == 200, (
        f"一行脏 who_knows 就让整个 relationships 列表 500：{r.text[:200]}。"
        f"⚠️ 这是 2026-10-16 新暴露的面——补上映射前该列压根没被读，脏不脏都测不到"
    )
    rows = {x["relation_type"]: x for x in r.json()}
    assert len(rows) == 2, rows
    dirty = rows["ALLY"]
    assert dirty["who_knows"] is None, f"应降级为 null，实得 {dirty['who_knows']!r}"
    # ⚠️ 干净行不得被脏行连累（这是本文件的核心判别点，不是「脏行降级」本身）
    clean = rows["RIVAL"]
    assert clean["who_knows"] == ["char_z"], f"干净行的值被污染了：{clean['who_knows']!r}"


def test_relationships_wrong_shaped_who_knows_also_degrades(tmp_path: Path):
    """合法 JSON 但**形状不对**（字符串 / 含非字符串元素的数组）也降级为 ``null``。

    ``json.loads`` 不解析语义：这两行在 JSON 层面完全合法，只有 pydantic 校验才知道
    形状错了 ⇒ 不兜底就是 ``ResponseValidationError`` ⇒ 整个列表 500。
    """
    app = _app(tmp_path)
    pid, _ = _project_with_char(app)
    _seed_relationship(app, pid, "rel_str", vis="PUBLIC", knows='"我是一个字符串"', kind="ALLY")
    _seed_relationship(app, pid, "rel_mixed", vis="PUBLIC", knows='["ok", 7]', kind="RIVAL")

    r = _get(app, f"/api/projects/{pid}/relationships")
    assert r.status_code == 200, f"形状不对的 who_knows 让整个列表 500：{r.text[:200]}"
    rows = {x["relation_type"]: x for x in r.json()}
    assert rows["ALLY"]["who_knows"] is None, rows["ALLY"]
    assert rows["RIVAL"]["who_knows"] is None, rows["RIVAL"]


def test_relationships_visibility_is_surfaced_not_hardcoded(tmp_path: Path):
    """``visibility`` 透出 **DB 的值**，不是硬编码的 DDL 默认。

    ⚠️ 与键集断言**不同类**：键集断言只证明「键在」，证不了「键里装的是 DB 的值」。
    服务层若写成 ``visibility="PUBLIC"`` 硬编码，键集照样 9 个、契约测试照样全绿——
    这正是本缺陷「键在、内容是死的」的同款形状。夹具造两个不同值，任何一侧的硬编码
    都会被另一侧抓到。
    """
    app = _app(tmp_path)
    pid, _ = _project_with_char(app)
    _seed_relationship(app, pid, "rel_pub", vis="PUBLIC", knows=None, kind="ALLY")
    _seed_relationship(app, pid, "rel_hid", vis="HIDDEN", knows=None, kind="RIVAL")

    r = _get(app, f"/api/projects/{pid}/relationships")
    assert r.status_code == 200, r.text[:200]
    rows = {x["relation_type"]: x for x in r.json()}
    assert rows["ALLY"]["visibility"] == "PUBLIC", rows["ALLY"]
    assert rows["RIVAL"]["visibility"] == "HIDDEN", (
        f"visibility 必须透出 DB 的值而非硬编码：{rows['RIVAL']['visibility']!r}"
    )
    # who_knows 的 NULL 与 [] 是**不同**的两态（NULL=沿用默认，[]=显式置空），不许合并
    assert rows["ALLY"]["who_knows"] is None, rows["ALLY"]


def test_relationships_ddl_default_visibility_public_is_the_fallback(tmp_path: Path):
    """缺列兜底：``.keys()`` 分支的值逐字对齐 DDL ``DEFAULT 'PUBLIC'``。

    ⚠️ **这是把实现里的常量抄进断言的刻意反例，理由有二**：
    ① 该分支**在当前测试库里不可达**（0014/0022 都已在生产跑过，没有缺 ``visibility``
    列的库）——量具量不到它，就不能假装量到了；
    ② 所以本用例断言的**不是「分支可执行」而是「兜底值与 DDL 一致」**：直接查迁移文件里
    ``relationships`` 的 ``visibility`` 那一行的 DEFAULT，与 models.py /
    service.py 里两处字面量三方比对。真正的漂移（有人把默认改成 'RESTRICTED'）会被抓到，
    而「老库缺列」这条运行时路径仍只有读代码能验（已如实标注，不假装有覆盖）。
    """
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[2]
    migrations = repo_root / "database" / "migrations"
    sql = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(migrations.glob("00*relationship*.sql"))
    )
    sql += (migrations / "0014_knowledge_reveal.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE relationships   ADD COLUMN visibility TEXT NOT NULL DEFAULT 'PUBLIC';" in sql, (
        "迁移 0014 的 relationships.visibility DEFAULT 变了——兜底值需同步复算"
    )
    assert (
        "CREATE TABLE IF NOT EXISTS relationships_new" in sql
        and "visibility          TEXT NOT NULL DEFAULT 'PUBLIC'" in sql
    ), "迁移 0022 重建表时 visibility 的 DEFAULT 与 0014 不一致（历史值应逐字保留）"

    from packages.domain.plot.models import Relationship

    assert Relationship.__dataclass_fields__["visibility"].default == "PUBLIC"
    bare = Relationship("rel_1", "prj_1", "a", "b", "ALLY")
    assert bare.to_dict()["visibility"] == "PUBLIC"
    assert bare.to_dict()["who_knows"] is None
    # service 侧的缺列兜底字面量（同一常量出现在 models 与 service 两处，故两处都钉）
    service_src = (repo_root / "packages" / "domain" / "plot" / "service.py").read_text(
        encoding="utf-8"
    )
    assert 'row["visibility"] if "visibility" in row.keys() else "PUBLIC"' in service_src, (
        "service 的缺列兜底字面量变了（它必须与 DDL DEFAULT 一致）"
    )
