"""skill 测试共用夹具（临时库 + 合成数据）。

**零真实 LLM、零内容仓接触、零 dev 库**：全部走 ``tmp_path`` 临时库
（``apply_migrations`` 建 0001~0032 全链）与合成行（直接 INSERT，不经工作流）。

⚠️ 本模块是**夹具**，不是被测物：里面的形状是「后端契约」的直接翻译
（DDL 列名 / service docstring / schema），**不是**「跑一下看返回什么」抄来的
（那是坑区「夹具从被测代码的实际输出里抄形状 ⇒ 缺陷被写进断言」的成因）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.skill import SkillService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def fresh_db(tmp_path: Path) -> Path:
    """建临时库并跑完整迁移链（含 0032 skills）。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def insert_project(db_path: Path, name: str = "skill 项目") -> str:
    """插一行项目（projects 表最小列集）。"""
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def insert_chapter(
    db_path: Path, pid: str, number: int = 1, *, plan_json: str = "{}",
) -> str:
    """插一行章节（chapters 表最小列集）。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def valid_payload(
    *,
    name: str = "对白潜台词",
    instructions: str = "不要直白说出情绪，用动作与停顿承载。",
    applies_to: list[str] | None = None,
    references: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """一份**合法**的 skill payload（schema 形状取自 skill.schema.json）。"""
    payload: dict[str, Any] = {
        "schema_version": "skill.v1.0.0",
        "name": name,
        "instructions": instructions,
    }
    if description is not None:
        payload["description"] = description
    if applies_to is not None:
        payload["applies_to"] = applies_to
    if references is not None:
        payload["references"] = references
    if checks is not None:
        payload["checks"] = checks
    return payload


def make_skill(
    db_path: Path,
    *,
    skill_id: str = "skl_fixture",
    name: str = "对白潜台词",
    instructions: str = "不要直白说出情绪，用动作与停顿承载。",
    applies_to: list[str] | None = None,
    references: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """建一条 skill（service 写路径，payload 过 jsonschema 校验）。"""
    svc = SkillService(db_path)
    body = payload if payload is not None else valid_payload(
        name=name,
        instructions=instructions,
        applies_to=applies_to,
        references=references,
        checks=checks,
    )
    return svc.create({
        "skill_id": skill_id,
        "name": name,
        "payload": body,
        "source_path": "skills/fixture",
    })


def bind_skills(db_path: Path, project_id: str, skill_ids: list[str]) -> list[dict[str, Any]]:
    """把 skill 绑定到项目（**列表顺序即注入顺序**）。"""
    code, skills = SkillService(db_path).bind(project_id, skill_ids)
    assert code == "ok", f"绑定失败：{code}"
    return skills or []


def skill_rows(db_path: Path, project_id: str) -> list[dict[str, Any]]:
    """项目当前绑定的 skill 行（装配层读到的形态）。"""
    out = []
    for s in SkillService(db_path).list_project_skills(project_id):
        out.append({
            "skill_id": s["skill_id"],
            "name": s["name"],
            "version": s["version"],
            "payload": s["payload"],
        })
    return out
