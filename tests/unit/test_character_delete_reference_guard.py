"""删角色的**引用完整性**守卫（2026-10-23 task-7 · D-12 / M-10）。

缺陷（Lead + 审查实测，见 ``docs/impl/reviews/full-review-round2-2026-10-23.md`` §3.9）：
迁移 0022 摘掉了 ``relationships.from_character_id`` / ``to_character_id`` 的外键
（端点可以是 faction，FK 语义不再成立），而 ``CharacterService.delete``
（``packages/domain/character/service.py``）**只级联删 ``character_states``、不查引用**
⇒ 删角色后 ``relationships`` 留**孤儿行**，而 ``PRAGMA foreign_key_check``
**报不出来**（FK 已不存在 ⇒ 检查无对象）⇒ 孤儿行会一路留在库里。

对照：``packages/domain/world/service.py:544`` 有同款引用检查 ⇒ 两个域处置不一致。

本文件钉住五件事：

1. 角色是关系的 ``from_character_id`` ⇒ 删角色**抛错**；
2. 角色是关系的 ``to_character_id`` ⇒ 删角色**也抛错**（两端都要查）；
3. 无引用 ⇒ 删成功，且 ``character_states`` 一并清掉（既有行为不变）；
4. 抛错 ⇒ **什么都没删**（角色行、关系行都还在）——不许「先删后报错」；
5. 错误类型沿用 world 域同款（``packages/domain/world/service.py`` 的 ``ReferencedError``，
   ``http_status = 409``）⇒ 路由层「删除被引用 ⇒ 409」的契约在两个域之间一致。

⚠️ 本文件**只**用临时库 + 合成夹具：不碰 dev 库，不做真实 LLM 调用。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.character.models import CharacterCreate
from packages.domain.character.service import CharacterService
from packages.domain.world.service import ReferencedError as WorldReferencedError
from packages.domain.world.service import WorldService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# --------------------------------------------------------------------- 夹具


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
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _new_character(db_path: Path, project_id: str, name: str) -> str:
    row = CharacterService(db_path).create(
        project_id, CharacterCreate(name=name, role="supporting")
    )
    return row["character_id"]


def _insert_relationship(
    db_path: Path,
    project_id: str,
    from_id: str,
    to_id: str,
    relation_type: str = "ally",
) -> str:
    """直插一行关系（列形状 = 0022 之后的最终 DDL）。"""
    rid = new_id("rel")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO relationships
                   (relationship_id, project_id, from_character_id, to_character_id,
                    relation_type, state_json, last_state_version, visibility, who_knows)
               VALUES (?, ?, ?, ?, ?, '{}', 1, 'PUBLIC', NULL)""",
            (rid, project_id, from_id, to_id, relation_type),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _count(db_path: Path, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


# ------------------------------------------------------------------- 用例


def test_delete_character_referenced_as_from_endpoint_raises(tmp_path: Path) -> None:
    """① 角色是关系的 ``from_character_id`` ⇒ 删角色抛错（不删任何东西）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)

    with pytest.raises(WorldReferencedError) as exc:
        CharacterService(db_path).delete(a)

    msg = str(exc.value)
    assert a in msg, f"错误信息未点名角色 id：{msg!r}"
    assert "relationships" in msg, f"错误信息未点名被引用的表：{msg!r}"
    assert exc.value.http_status == 409, "删除被引用应对应 409（与 world 域同款）"


def test_delete_character_referenced_as_to_endpoint_raises(tmp_path: Path) -> None:
    """② 角色是关系的 ``to_character_id`` ⇒ **同样**抛错（两端都要查）。

    ⚠️ 只查 ``from_character_id`` 的半修实现会被这条抓住（本仓登记的「半修」形状）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)

    with pytest.raises(WorldReferencedError):
        CharacterService(db_path).delete(b)


def test_delete_character_without_relationship_succeeds(tmp_path: Path) -> None:
    """③ 无引用 ⇒ 删成功；``character_states`` 随角色一并清掉（既有行为不变）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _new_character(db_path, pid, "独行客")

    assert CharacterService(db_path).delete(cid) is True

    assert _count(db_path, "SELECT COUNT(*) FROM characters WHERE character_id = ?", (cid,)) == 0
    assert (
        _count(db_path, "SELECT COUNT(*) FROM character_states WHERE character_id = ?", (cid,))
        == 0
    )


def test_delete_unknown_character_returns_false(tmp_path: Path) -> None:
    """④ 删不存在的角色 ⇒ 返回 False（**不**抛错，既有契约不变）。"""
    db_path = _fresh_db(tmp_path)
    assert CharacterService(db_path).delete("char_does_not_exist") is False


def test_rejected_delete_leaves_character_and_relationship_intact(tmp_path: Path) -> None:
    """⑤ 抛错 ⇒ **什么也没删**：角色行、关系行都还在（不许「先删后报错」）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    rid = _insert_relationship(db_path, pid, a, b)

    with pytest.raises(WorldReferencedError):
        CharacterService(db_path).delete(a)

    assert _count(db_path, "SELECT COUNT(*) FROM characters WHERE character_id = ?", (a,)) == 1
    assert (
        _count(db_path, "SELECT COUNT(*) FROM character_states WHERE character_id = ?", (a,))
        == 1
    )
    assert (
        _count(db_path, "SELECT COUNT(*) FROM relationships WHERE relationship_id = ?", (rid,))
        == 1
    )


def test_orphan_relationship_is_invisible_to_foreign_key_check(tmp_path: Path) -> None:
    """⑥ **为什么这道守卫只能落在 Service 层**（本条的判别点）。

    0022 摘掉 FK 后，孤儿关系行**可以**存在；而 ``PRAGMA foreign_key_check``
    对它**报不出来**（FK 已不存在 ⇒ 检查无对象）⇒ DB 层查不出「有人在用这个角色」
    ⇒ 只能在删之前显式查引用。

    ⚠️ 若哪天有迁移把 FK 加回来，本用例会红（``foreign_key_check`` 能报出来了）
    ⇒ 那时该重新评估这条 Service 层守卫是否还需要——**不是缺陷，是提醒**。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)

    # 用裸 SQL 制造孤儿行（绕过 Service）——等价于缺陷形态：角色没了、关系还在。
    # ⚠️ 必须先删 ``character_states``：它对 ``characters`` 的 FK **仍在**（0001）
    #    且本仓连接开着 ``PRAGMA foreign_keys=ON`` ⇒ 直接删角色会被 FK 拦下。
    #    唯独 ``relationships`` 的两端 FK 被 0022 摘了 ⇒ 孤儿行才造得出来。
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM character_states WHERE character_id = ?", (a,))
        conn.execute("DELETE FROM characters WHERE character_id = ?", (a,))
        conn.commit()
    finally:
        conn.close()

    orphans = _count(
        db_path,
        "SELECT COUNT(*) FROM relationships WHERE from_character_id = ? OR to_character_id = ?",
        (a, a),
    )
    assert orphans == 1, "夹具前提：孤儿关系行真的存在"

    conn = sqlite3.connect(str(db_path))
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    assert violations == [], (
        "孤儿关系行被 PRAGMA foreign_key_check 报出来了 ⇒ 0022 的 FK 摘除口径变了，"
        "本仓对该守卫的前提需要重新评估"
    )


# ===========================================================================
# A3（对抗审查 B）：world 域同款孤儿洞 —— 删 faction 不查 relationships
# ===========================================================================
#
# 依据：``database/migrations/0022_faction_relationship_endpoints.sql:7-8 / :62-64``
# 明确「关系端点可以是 faction_id」⇒ 删 faction 同样会留孤儿关系行，
# 而 ``world/service.py::_delete`` 此前**只**查 ``locations`` 的 plot_events 引用。
# ⇒ 两个域共用 world 域的 ``assert_not_relationship_endpoint`` 守卫。


def _new_faction(db_path: Path, project_id: str, name: str) -> str:
    return WorldService(db_path).create_faction(project_id, name).id


def test_delete_faction_referenced_as_from_endpoint_raises(tmp_path: Path) -> None:
    """⑦ faction 作为关系端点 ⇒ 删 faction 抛错（world 域自己的错误类型）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    fac = _new_faction(db_path, pid, "赤月宗")
    other = _new_character(db_path, pid, "路人")
    _insert_relationship(db_path, pid, fac, other)

    with pytest.raises(WorldReferencedError) as exc:
        WorldService(db_path).delete_faction(fac)

    msg = str(exc.value)
    assert fac in msg, f"错误信息未点名 faction id：{msg!r}"
    assert "relationships" in msg, f"错误信息未点名被引用的表：{msg!r}"
    assert exc.value.http_status == 409, "删除被引用应对应 409"


def test_delete_faction_referenced_as_to_endpoint_raises(tmp_path: Path) -> None:
    """⑧ faction 作为 ``to_character_id`` 端点 ⇒ **同样**抛错（两端都查）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    fac = _new_faction(db_path, pid, "赤月宗")
    other = _new_character(db_path, pid, "路人")
    _insert_relationship(db_path, pid, other, fac)

    with pytest.raises(WorldReferencedError):
        WorldService(db_path).delete_faction(fac)


def test_delete_faction_without_relationship_succeeds(tmp_path: Path) -> None:
    """⑨ 无引用 ⇒ 删 faction 成功（既有行为不变）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    fac = _new_faction(db_path, pid, "孤宗")

    WorldService(db_path).delete_faction(fac)

    assert _count(db_path, "SELECT COUNT(*) FROM factions WHERE faction_id = ?", (fac,)) == 0


def test_rejected_faction_delete_leaves_faction_and_relationship_intact(
    tmp_path: Path,
) -> None:
    """⑩ 抛错 ⇒ **什么也没删**：faction 行、关系行都还在（不许「先删后报错」）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    fac = _new_faction(db_path, pid, "赤月宗")
    other = _new_character(db_path, pid, "路人")
    rid = _insert_relationship(db_path, pid, fac, other)

    with pytest.raises(WorldReferencedError):
        WorldService(db_path).delete_faction(fac)

    assert _count(db_path, "SELECT COUNT(*) FROM factions WHERE faction_id = ?", (fac,)) == 1
    assert (
        _count(db_path, "SELECT COUNT(*) FROM relationships WHERE relationship_id = ?", (rid,))
        == 1
    )


def test_character_and_world_share_one_relationship_guard() -> None:
    """⑪ 两域**共用同一份**守卫实现（不是各写一遍 ⇒ 下次不会再漏第二个域）。"""
    from packages.domain.character import service as character_service
    from packages.domain.world import service as world_service

    assert (
        character_service.assert_not_relationship_endpoint
        is world_service.assert_not_relationship_endpoint
    ), "两个域各写了一份守卫 ⇒ 同款缺陷会再漏一次"


# ===========================================================================
# A4：存量孤儿行**没有任何发现手段** ⇒ 只读诊断脚本
# ===========================================================================
#
# 域层守卫只挡「新的」孤儿；**已经**在库里的孤儿行仍需能回答
# 「有没有？」这个问题。迁移 0022 摘掉 FK ⇒ ``PRAGMA foreign_key_check`` 报不出。
# ⇒ ``scripts/check_orphan_relationships.py``（**只读**、不清理、不改数据）。

_ORPHAN_SCRIPT = REPO_ROOT / "scripts" / "check_orphan_relationships.py"


def _load_orphan_script():
    """按路径加载诊断脚本（``scripts/`` 不是包，用 importlib 加载）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_orphan_relationships_under_test", _ORPHAN_SCRIPT
    )
    assert spec is not None and spec.loader is not None, "诊断脚本加载失败"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db_fingerprint(db_path: Path) -> tuple[int, bytes]:
    data = db_path.read_bytes()
    return len(data), hash(data)


def test_orphan_diagnostic_detects_dangling_endpoint(tmp_path: Path) -> None:
    """⑫ 端点实体已不存在 ⇒ 诊断**报出**该行，并点名是哪个端点悬空。"""
    mod = _load_orphan_script()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    rid = _insert_relationship(db_path, pid, a, b)

    # 制造孤儿：角色没了，关系行还在（0022 摘了 FK ⇒ 这是可写状态）
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM character_states WHERE character_id = ?", (a,))
        conn.execute("DELETE FROM characters WHERE character_id = ?", (a,))
        conn.commit()
    finally:
        conn.close()

    with mod.open_readonly_db(db_path) as conn:
        orphans = mod.find_orphan_relationships(conn)
        total = mod.count_relationships(conn)

    assert total == 1, f"夹具前提：关系行确实还在（total={total}）"
    assert len(orphans) == 1, f"孤儿行没被诊断出来：{orphans}"
    assert orphans[0]["relationship_id"] == rid
    assert orphans[0]["dangling_endpoints"] == ["from_character_id"], (
        f"悬空端点判定错：{orphans[0]['dangling_endpoints']}"
    )


def test_orphan_diagnostic_exit_code_2_when_orphans_exist(tmp_path: Path) -> None:
    """⑬ 有孤儿 ⇒ 退出码 **2**（Lead 指定口径：0=无孤儿 / 2=有孤儿）。"""
    mod = _load_orphan_script()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM character_states WHERE character_id = ?", (a,))
        conn.execute("DELETE FROM characters WHERE character_id = ?", (a,))
        conn.commit()
    finally:
        conn.close()

    assert mod.main(["--db", str(db_path), "--json"]) == 2, "有孤儿时的退出码不是 2"


def test_orphan_diagnostic_exit_code_0_when_clean(tmp_path: Path) -> None:
    """⑭ 无孤儿 ⇒ 退出码 **0**。"""
    mod = _load_orphan_script()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)

    assert mod.main(["--db", str(db_path), "--json"]) == 0, "无孤儿时的退出码不是 0"


def test_orphan_diagnostic_does_not_modify_the_database(tmp_path: Path) -> None:
    """⑮ 诊断**只读**：跑完之后 DB 字节**逐位不变**（不许顺手清理）。"""
    mod = _load_orphan_script()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    a = _new_character(db_path, pid, "甲")
    b = _new_character(db_path, pid, "乙")
    _insert_relationship(db_path, pid, a, b)

    before = _db_fingerprint(db_path)
    assert mod.main(["--db", str(db_path), "--json"]) == 0
    after = _db_fingerprint(db_path)

    assert before == after, "诊断脚本改动了数据库（它必须只读）"


def test_orphan_diagnostic_accepts_faction_endpoint(tmp_path: Path) -> None:
    """⑯ **合法**的 faction 端点**不得**被误报成孤儿（0022：端点可以是 faction）。"""
    mod = _load_orphan_script()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    fac = _new_faction(db_path, pid, "赤月宗")
    human = _new_character(db_path, pid, "路人")
    _insert_relationship(db_path, pid, fac, human)

    with mod.open_readonly_db(db_path) as conn:
        orphans = mod.find_orphan_relationships(conn)

    assert orphans == [], f"faction 端点被误报成孤儿：{orphans}"
