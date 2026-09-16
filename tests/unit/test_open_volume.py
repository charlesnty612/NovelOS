"""scripts/open_volume.py 单测（开新卷 CLI：校验 / 落库 / 幂等）。

覆盖任务书给死的四条：
1. brief 校验：缺字段 / 章号不连续 / number 冲突 → 拒写且报错信息可读；
2. apply 后：volumes 行存在、章节数正确、outline_json 逐字段落库、volume_id 关联正确；
3. 幂等：连跑两次 apply → 第二次零新建（计数断言，不只返回码）；
4. 卷纲体检是报告器：alert 只提示不阻断。

另外钉住两条实现口径：
- 校验失败时 **dry-run 与 apply 都不写库**（apply 拒写）；
- dry-run 本身零写入。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.open_volume as open_volume  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _query(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = get_connection(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _make_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, "
        "status, created_at, updated_at) VALUES (?, '快穿之价签', NULL, NULL, NULL, "
        "'ACTIVE', ?, ?)",
        (pid, now, now),
    )
    return pid


def _make_volume(db_path: Path, project_id: str, number: int, *, title: str | None = None,
                 status: str = "active") -> str:
    vid = new_id("vol")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO volumes (volume_id, project_id, number, title, status, "
        "terminal_snapshot_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
        (vid, project_id, number, title if title is not None else f"第{number}卷", status, now, now),
    )
    return vid


def _make_chapter(db_path: Path, project_id: str, number: int, *, title: str,
                  volume_id: str | None = None, outline: dict | None = None,
                  status: str = "PLANNED") -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, outline_json, "
        "status, visibility, who_knows, created_at, updated_at, volume_id) "
        "VALUES (?, ?, ?, ?, '{}', ?, ?, 'VISIBLE', NULL, ?, ?, ?)",
        (cid, project_id, number, title,
         None if outline is None else json.dumps(outline, ensure_ascii=False),
         status, now, now, volume_id),
    )
    return cid


def _outline(n: int, **overrides) -> dict:
    outline = {
        "chapter_goal": f"第{n}章目标：主角连夜清点存粮并定下先手",
        "core_conflict": f"第{n}章冲突：库房账目与口信对不上",
        "turning_point": f"第{n}章转折：账册第三页露出涂改痕迹",
        "expected_role": "escalation",
        "key_beats": [f"第{n}章拍点一：摸进北库", f"第{n}章拍点二：贴出告示"],
        "character_changes_planned": ["主角从被动挨打转为主动布局"],
        "information_releases": ["账册被涂改"],
        "expected_word_count": 2500,
    }
    outline.update(overrides)
    return outline


def _chapter(n: int, **outline_overrides) -> dict:
    return {"number": n, "title": f"第{n}章", "outline": _outline(n, **outline_overrides)}


def _brief(*, number: int = 2, nums: tuple[int, ...] = (25, 26), title: str = "第二世界",
           arc_summary: str = "新位面开局：价签规则重排", **outline_overrides) -> dict:
    return {
        "volume": {"number": number, "title": title, "arc_summary": arc_summary},
        "chapters": [_chapter(n, **outline_overrides) for n in nums],
    }


def _write_brief(tmp_path: Path, brief: dict, name: str = "vol2.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _run(capsys, db_path: Path, project_id: str, brief_path: Path, *extra: str
         ) -> tuple[int, str]:
    argv = ["--db", str(db_path), "--project", project_id, "--brief", str(brief_path), *extra]
    code = open_volume.main(argv)
    return code, capsys.readouterr().out


def _run_json(capsys, db_path: Path, project_id: str, brief_path: Path, *extra: str) -> tuple[int, dict]:
    code, out = _run(capsys, db_path, project_id, brief_path, *extra, "--json")
    return code, json.loads(out)


def _chapter_count(db_path: Path, project_id: str) -> int:
    return _query(db_path, "SELECT COUNT(*) AS n FROM chapters WHERE project_id = ?",
                  (project_id,))[0]["n"]


# ---------------------------------------------------------------------------
# 1. brief 校验（拒写）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "chapter_goal", "core_conflict", "turning_point",
    "key_beats", "character_changes_planned", "information_releases", "expected_word_count",
])
def test_missing_outline_field_rejected(tmp_path: Path, capsys, field: str):
    """outline 任必填字段缺失 → error + 退出码 1 + 零写入（卷 2 不建）。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    slot = _chapter(25)
    slot["outline"].pop(field)
    brief = {"volume": {"number": 2, "title": "第二世界"}, "chapters": [slot]}
    path = _write_brief(tmp_path, brief)

    code, out = _run(capsys, db, pid, path, "--apply")

    assert code == 1, out
    assert f"outline.{field}" in out
    assert "拒写" in out
    assert _query(db, "SELECT COUNT(*) AS n FROM volumes WHERE project_id = ?", (pid,))[0]["n"] == 0
    assert _chapter_count(db, pid) == 0


def test_illegal_expected_role_rejected(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(25,), expected_role="unknown"))
    code, out = _run(capsys, db, pid, path, "--apply")
    assert code == 1
    assert "expected_role" in out and "非法" in out
    assert _chapter_count(db, pid) == 0


def test_empty_key_beats_rejected(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(25,), key_beats=[]))
    code, out = _run(capsys, db, pid, path)
    assert code == 1
    assert "key_beats" in out


def test_gap_chapter_numbers_rejected(tmp_path: Path, capsys):
    """章号不连续（25,27）→ CHAPTER-GAP，报错信息可读。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(25, 27)))
    code, out = _run(capsys, db, pid, path, "--apply")
    assert code == 1
    assert "CHAPTER-GAP" in out and "缺 [26]" in out


def test_out_of_order_chapter_numbers_rejected(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(26, 25)))
    code, out = _run(capsys, db, pid, path)
    assert code == 1
    assert "CHAPTER-ORDER" in out


def test_chapter_number_conflict_with_other_volume_rejected(tmp_path: Path, capsys):
    """章号已被卷 1 占用 → CHAPTER-CONFLICT，拒写（不覆盖别人的章、不建卷 2）。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    vol1 = _make_volume(db, pid, 1)
    _make_chapter(db, pid, 25, title="卷1的旧章", volume_id=vol1)

    path = _write_brief(tmp_path, _brief(nums=(25, 26)))
    code, out = _run(capsys, db, pid, path, "--apply")

    assert code == 1
    assert "CHAPTER-CONFLICT" in out and "卷 #1" in out
    rows = _query(db, "SELECT volume_id FROM chapters WHERE project_id = ? AND number = 25", (pid,))
    assert rows[0]["volume_id"] == vol1, "既有章不得被改写归属"
    assert _query(db, "SELECT COUNT(*) AS n FROM volumes WHERE project_id = ?", (pid,))[0]["n"] == 1


def test_volume_number_conflict_sealed_rejected(tmp_path: Path, capsys):
    """同号卷已 sealed（终态归档）→ 拒写。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    _make_volume(db, pid, 1)
    _make_volume(db, pid, 2, title="已归档的卷2", status="sealed")

    path = _write_brief(tmp_path, _brief(number=2, nums=(25,)))
    code, out = _run(capsys, db, pid, path, "--apply")

    assert code == 1
    assert "VOLUME-SEALED" in out
    assert _chapter_count(db, pid) == 0


def test_active_volume_requires_explicit_seal_flag(tmp_path: Path, capsys):
    """active 卷单例：不给 --seal-active 时拒写，提示里给出处置路径。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    _make_volume(db, pid, 1, title="第一世界")
    path = _write_brief(tmp_path, _brief(number=2, nums=(25,)))

    code, out = _run(capsys, db, pid, path, "--apply")

    assert code == 1
    assert "VOLUME-ACTIVE" in out and "--seal-active" in out
    assert _query(db, "SELECT COUNT(*) AS n FROM volumes WHERE project_id = ?", (pid,))[0]["n"] == 1
    assert _query(db, "SELECT status FROM volumes WHERE project_id = ?", (pid,))[0]["status"] == "active"


def test_bad_json_exit_code_2(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    code, _ = _run(capsys, db, pid, path)
    assert code == 2


def test_unknown_project_rejected(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(25,)))
    code, out = _run(capsys, db, "prj_not_exist", path, "--apply")
    assert code == 1
    assert "PROJECT-NOT-FOUND" in out


# ---------------------------------------------------------------------------
# 2. dry-run 零写入
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    _make_volume(db, pid, 1, title="第一世界")
    path = _write_brief(tmp_path, _brief(number=2, nums=(25, 26)))

    code, out = _run(capsys, db, pid, path, "--seal-active")

    assert code == 0, out
    assert "dry-run" in out and "未写库" in out
    assert _query(db, "SELECT COUNT(*) AS n FROM volumes WHERE project_id = ?", (pid,))[0]["n"] == 1
    assert _chapter_count(db, pid) == 0
    assert _query(db, "SELECT status FROM volumes WHERE project_id = ?",
                  (pid,))[0]["status"] == "active", "dry-run 不得封存"


# ---------------------------------------------------------------------------
# 3. apply：建卷 / 建章 / 写 outline / 挂卷
# ---------------------------------------------------------------------------


def test_apply_creates_volume_chapters_outline_and_assignment(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    _make_volume(db, pid, 1, title="第一世界")
    for n in range(1, 3):
        _make_chapter(db, pid, n, title=f"卷1章{n}", volume_id=_query(
            db, "SELECT volume_id FROM volumes WHERE project_id = ?", (pid,))[0]["volume_id"])
    brief = _brief(number=2, nums=(3, 4, 5), title="第二世界", arc_summary="新位面：价签重排")
    path = _write_brief(tmp_path, brief)

    code, out = _run(capsys, db, pid, path, "--apply", "--seal-active")
    assert code == 0, out

    vols = _query(db, "SELECT volume_id, number, title, status, arc_summary FROM volumes "
                      "WHERE project_id = ? ORDER BY number", (pid,))
    assert [v["number"] for v in vols] == [1, 2]
    assert vols[0]["status"] == "sealed", "--seal-active 应封存卷 1"
    assert vols[1]["status"] == "active"
    assert vols[1]["title"] == "第二世界"
    assert vols[1]["arc_summary"] == "新位面：价签重排"

    new_vol_id = vols[1]["volume_id"]
    rows = _query(db, "SELECT number, title, outline_json, volume_id, status FROM chapters "
                      "WHERE project_id = ? ORDER BY number", (pid,))
    assert len(rows) == 5, "既有 2 章 + 新开 3 章"
    for row in rows[2:]:
        assert row["volume_id"] == new_vol_id, "新章必须挂到新卷"
        assert row["status"] == "PLANNED"
        outline = json.loads(row["outline_json"])
        expected = _outline(row["number"])
        for key, value in expected.items():
            assert outline[key] == value, f"outline.{key} 未逐字段落库"
        assert outline["schema_version"] == open_volume.OUTLINE_SCHEMA_VERSION
    # 旧卷的章不被改归属
    assert {r["volume_id"] for r in rows[:2]} == {vols[0]["volume_id"]}


def test_apply_summary_counts_and_listing(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(1, 2)))

    code, summary = _run_json(capsys, db, pid, path, "--apply")

    assert code == 0
    assert summary["ok"] is True
    assert summary["volume"]["action"] == "create"
    assert summary["counts"]["volumes_created"] == 1
    assert summary["counts"]["chapters_created"] == 2
    assert summary["counts"]["chapters_assigned"] == 2
    assert summary["counts"]["outlines_updated"] == 0
    assert {c["number"] for c in summary["chapters"]} == {1, 2}
    assert all(c["action"] == "create" for c in summary["chapters"])
    assert any(a["action"] == "create_volume" for a in summary["actions"])


# ---------------------------------------------------------------------------
# 4. 幂等：连跑两次 → 第二次零新建/零改写
# ---------------------------------------------------------------------------


def test_apply_twice_is_idempotent(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(1, 2, 3)))

    code1, s1 = _run_json(capsys, db, pid, path, "--apply")
    assert code1 == 0
    assert s1["counts"]["volumes_created"] == 1
    assert s1["counts"]["chapters_created"] == 3
    vols_after_first = _query(db, "SELECT volume_id FROM volumes WHERE project_id = ?", (pid,))
    chapters_after_first = _query(
        db, "SELECT chapter_id, number, outline_json, volume_id FROM chapters "
            "WHERE project_id = ? ORDER BY number", (pid,))

    code2, s2 = _run_json(capsys, db, pid, path, "--apply")

    assert code2 == 0
    assert s2["counts"]["volumes_created"] == 0, "第二次不得重复建卷"
    assert s2["counts"]["chapters_created"] == 0, "第二次不得重复建章"
    assert s2["counts"]["outlines_updated"] == 0, "outline 一致时不得改写"
    assert s2["counts"]["chapters_assigned"] == 0, "已挂卷不得重复挂"
    assert s2["counts"]["volumes_sealed"] == 0
    assert [c["action"] for c in s2["chapters"]] == ["adopt"] * 3
    # 计数断言之外再看库：卷/章行数与 id 都不变
    assert _query(db, "SELECT volume_id FROM volumes WHERE project_id = ?", (pid,)) == vols_after_first
    assert _query(
        db, "SELECT chapter_id, number, outline_json, volume_id FROM chapters "
            "WHERE project_id = ? ORDER BY number", (pid,)) == chapters_after_first
    assert _chapter_count(db, pid) == 3
    # 第三次改走文本模式：零动作要如实报告（不得冒充 dry-run 话术）
    code3, out3 = _run(capsys, db, pid, path, "--apply")
    assert code3 == 0
    assert "无动作" in out3 and "dry-run" not in out3


def test_reapply_updates_outline_only_when_changed(tmp_path: Path, capsys):
    """brief 改了大纲 → 既有章只改 outline_json 并打印 diff 摘要。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    first = _write_brief(tmp_path, _brief(nums=(1, 2)), name="v1.json")
    code, _ = _run(capsys, db, pid, first, "--apply")
    assert code == 0

    second_brief = _brief(nums=(1, 2))
    second_brief["chapters"][1]["outline"]["chapter_goal"] = "第2章目标：改写成当众拆穿账目"
    second = _write_brief(tmp_path, second_brief, name="v2.json")

    code2, s2 = _run_json(capsys, db, pid, second, "--apply")

    assert code2 == 0
    assert s2["counts"]["chapters_created"] == 0
    assert s2["counts"]["outlines_updated"] == 1
    changed = [c for c in s2["chapters"] if c["action"] == "update-outline"]
    assert [c["number"] for c in changed] == [2]
    assert list(changed[0]["outline_diff"]) == ["chapter_goal"]
    row = _query(db, "SELECT outline_json FROM chapters WHERE project_id = ? AND number = 2",
                 (pid,))[0]
    assert json.loads(row["outline_json"])["chapter_goal"] == "第2章目标：改写成当众拆穿账目"
    # 未变的章不被改写：用 updated_at 判别（plan_json 等生成面列也不该被碰）
    assert _chapter_count(db, pid) == 2


def test_reapply_assigns_orphan_chapter(tmp_path: Path, capsys):
    """上次运行半途中断的残留（章已建、无卷归属）→ 复用并补挂，不重复建章。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(1, 2)))
    code, s1 = _run_json(capsys, db, pid, path, "--apply")
    assert code == 0
    vol_id = s1["volume"]["volume_id"]
    # 模拟中断：把第 2 章的卷归属清空
    _exec(db, "UPDATE chapters SET volume_id = NULL WHERE project_id = ? AND number = 2", (pid,))

    code2, s2 = _run_json(capsys, db, pid, path, "--apply")

    assert code2 == 0
    assert s2["counts"]["chapters_created"] == 0
    assert s2["counts"]["chapters_assigned"] == 1
    assert _chapter_count(db, pid) == 2
    row = _query(db, "SELECT volume_id FROM chapters WHERE project_id = ? AND number = 2", (pid,))
    assert row[0]["volume_id"] == vol_id


# ---------------------------------------------------------------------------
# 5. 卷纲体检：报告器不是闸门
# ---------------------------------------------------------------------------


def test_outline_alerts_reported_but_not_blocking(tmp_path: Path, capsys):
    """钩子命中禁止模式（DS-5）→ alert 出现在报告里，但仍可落库。"""
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    bad_beats = ["摸进北库", "故事刚刚开始"]
    path = _write_brief(tmp_path, _brief(nums=(1,), key_beats=bad_beats))

    code, summary = _run_json(capsys, db, pid, path, "--apply")

    assert code == 0, "alert 不阻断落库"
    rules = {f["rule_id"] for f in summary["outline_check"]["findings"]}
    assert "OUTLINE-BAD-HOOK" in rules
    assert summary["outline_check"]["alert_count"] >= 1
    assert summary["outline_check"]["position_to_chapter_number"] == {"ch1": 1}
    assert _chapter_count(db, pid) == 1


def test_outline_check_position_mapping_for_non_1_based_numbers(tmp_path: Path, capsys):
    db = _fresh_db(tmp_path)
    pid = _make_project(db)
    path = _write_brief(tmp_path, _brief(nums=(25, 26)))

    code, out = _run(capsys, db, pid, path)

    assert code == 0
    assert "命中项按位置编号：ch1=#25 … ch2=#26" in out
