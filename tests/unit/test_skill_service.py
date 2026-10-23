"""skill 的 CRUD / 项目多 slot 有序绑定（service 层）。

钉住的不变量：

1. **payload 变更 ⇒ version 自增**（装配缓存键指纹含 version；不自增 ⇒ 改完 skill
   命中旧装配 = 脏命中）。
2. **只改元信息（name / description / source_path）不 bump version**（version 的语义
   是「payload 版本」，不是「行版本」）。
3. **绑定是多 slot 有序**（``project_skills.ord``）：绑定顺序 = 注入顺序。
4. **绑定是覆盖式**：二次绑定先清旧绑定，不留孤儿行。
5. **解绑幂等**：未绑定也返回 ``not_bound``，不抛错。
6. **删 skill ⇒ 绑定行随之消失**（DDL 的 ``ON DELETE CASCADE`` 兜底）。

零真实 LLM、零内容仓接触、零 dev 库（临时库 + 合成行）。
"""

from __future__ import annotations

from packages.core.db import get_connection
from packages.core.skill import BindStatus, SkillService

from .skill_fixtures import (
    bind_skills,
    fresh_db,
    insert_project,
    make_skill,
    valid_payload,
)


def test_create_and_get_roundtrip(tmp_path):
    """创建后能读回全文（``payload`` 已解析为 dict）。"""
    db_path = fresh_db(tmp_path)
    svc = SkillService(db_path)
    created = svc.create({
        "skill_id": "skl_rt",
        "name": "对白潜台词",
        "description": "别直白说情绪",
        "payload": valid_payload(applies_to=["writer"]),
        "source_path": "skills/dialogue",
    })
    assert created["skill_id"] == "skl_rt"
    assert created["version"] == 1
    assert created["payload"]["instructions"]
    assert created["bound_project_count"] == 0

    fetched = svc.get("skl_rt")
    assert fetched is not None
    assert fetched["payload"] == created["payload"]
    assert fetched["source_path"] == "skills/dialogue"


def test_update_payload_bumps_version(tmp_path):
    """改 payload ⇒ version 自增（缓存键指纹含 version ⇒ 必 miss）。"""
    db_path = fresh_db(tmp_path)
    svc = SkillService(db_path)
    svc.create({"skill_id": "skl_v", "name": "n", "payload": valid_payload()})

    updated = svc.update("skl_v", {
        "payload": valid_payload(instructions="换一套说法。"),
    })
    assert updated is not None
    assert updated["version"] == 2, "payload 变更必须 bump version"
    assert updated["payload"]["instructions"] == "换一套说法。"


def test_update_metadata_keeps_version(tmp_path):
    """只改 name / description / source_path ⇒ version **不变**。

    version 的语义是「payload 版本」；元信息改动不进 payload ⇒ 不该触发缓存 miss。
    """
    db_path = fresh_db(tmp_path)
    svc = SkillService(db_path)
    svc.create({"skill_id": "skl_m", "name": "n", "payload": valid_payload()})

    updated = svc.update("skl_m", {"name": "新名字", "description": "新描述"})
    assert updated is not None
    assert updated["version"] == 1, "元信息改动不得 bump version"
    assert updated["name"] == "新名字"
    assert updated["description"] == "新描述"


def test_update_missing_skill_returns_none(tmp_path):
    """更新不存在的 skill → None（端点据此转 404）。"""
    db_path = fresh_db(tmp_path)
    assert SkillService(db_path).update("skl_nope", {"name": "x"}) is None


def test_bind_is_ordered_and_overwrites(tmp_path):
    """绑定：**多 slot 有序** + 覆盖式（不留孤儿行）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    make_skill(db_path, skill_id="skl_a", name="A")
    make_skill(db_path, skill_id="skl_b", name="B")
    make_skill(db_path, skill_id="skl_c", name="C")

    bound = bind_skills(db_path, pid, ["skl_c", "skl_a", "skl_b"])
    assert [s["skill_id"] for s in bound] == ["skl_c", "skl_a", "skl_b"], (
        "绑定顺序必须逐字保留（列表顺序即注入顺序）"
    )

    # 覆盖式：二次绑定只保留新集合
    bound2 = bind_skills(db_path, pid, ["skl_b"])
    assert [s["skill_id"] for s in bound2] == ["skl_b"]

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT skill_id FROM project_skills WHERE project_id = ?", (pid,)
        ).fetchall()
    finally:
        conn.close()
    assert [r["skill_id"] for r in rows] == ["skl_b"], "旧绑定行必须被清掉"


def test_bind_dedupes_keeping_first_position(tmp_path):
    """重复 skill_id 去重，保留**首次**出现的位置。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    make_skill(db_path, skill_id="skl_a", name="A")
    make_skill(db_path, skill_id="skl_b", name="B")
    bound = bind_skills(db_path, pid, ["skl_a", "skl_b", "skl_a"])
    assert [s["skill_id"] for s in bound] == ["skl_a", "skl_b"]


def test_bind_unknown_skill_writes_nothing(tmp_path):
    """绑定含不存在的 skill ⇒ **整体失败**，不留半套绑定。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    make_skill(db_path, skill_id="skl_a", name="A")
    svc = SkillService(db_path)

    code, _ = svc.bind(pid, ["skl_a", "skl_ghost"])
    assert code == BindStatus.SKILL_NOT_FOUND
    assert svc.list_project_skills(pid) == [], "失败时不得留下半套绑定"




def test_unbind_is_idempotent(tmp_path):
    """解绑幂等：第一次 OK，第二次 ``not_bound``（不抛错）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    make_skill(db_path, skill_id="skl_a", name="A")
    svc = SkillService(db_path)
    bind_skills(db_path, pid, ["skl_a"])

    code, remaining = svc.unbind(pid, "skl_a")
    assert code == BindStatus.OK
    assert remaining == []

    code2, _ = svc.unbind(pid, "skl_a")
    assert code2 == BindStatus.NOT_BOUND, "重复解绑必须幂等"


def test_unbind_unknown_project_returns_project_not_found(tmp_path):
    """项目不存在 ⇒ ``project_not_found``。"""
    db_path = fresh_db(tmp_path)
    code, _ = SkillService(db_path).unbind("prj_ghost", "skl_a")
    assert code == BindStatus.PROJECT_NOT_FOUND


def test_delete_removes_binding_rows(tmp_path):
    """删 skill ⇒ 绑定行随之消失（DDL 的 ``ON DELETE CASCADE`` 兜底）。"""
    db_path = fresh_db(tmp_path)
    pid = insert_project(db_path)
    make_skill(db_path, skill_id="skl_a", name="A")
    bind_skills(db_path, pid, ["skl_a"])
    svc = SkillService(db_path)

    assert svc.count_bindings("skl_a") == 1
    assert svc.delete("skl_a") is True

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM project_skills WHERE skill_id = ?", ("skl_a",)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [], "绑定行必须随 skill 删除一并消失"


def test_list_skills_filters_by_name(tmp_path):
    """列表按显示名精确过滤；摘要含派生字段。"""
    db_path = fresh_db(tmp_path)
    make_skill(db_path, skill_id="skl_a", name="对白潜台词", applies_to=["writer"])
    make_skill(db_path, skill_id="skl_b", name="悬念铺垫")
    svc = SkillService(db_path)

    assert len(svc.list_skills()) == 2
    filtered = svc.list_skills(name="对白潜台词")
    assert [s["skill_id"] for s in filtered] == ["skl_a"]
    assert filtered[0]["applies_to"] == ["writer"]
    assert filtered[0]["bound_project_count"] == 0


def test_summary_counts_references_and_checks(tmp_path):
    """摘要派生计数：reference_count / check_count 来自 payload。"""
    db_path = fresh_db(tmp_path)
    make_skill(
        db_path,
        skill_id="skl_cnt",
        name="N",
        references=[{"path": "p", "title": "t", "body": "b"}],
        checks=[{"check_id": "c1", "description": "d", "keywords": ["k"]}],
    )
    summary = SkillService(db_path).list_skills(name="N")[0]
    assert summary["reference_count"] == 1
    assert summary["check_count"] == 1
