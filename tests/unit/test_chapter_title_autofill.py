# -*- coding: utf-8 -*-
"""章名自动回填：planner 输出 ``title`` ⇒ 空标题章节落库获名（2026-10-23 批次二十五）。

缺陷形状（AGENTS 坑区「白名单静默丢弃 / 数据源存在 ≠ 已进装配」）
------------------------------------------------------------------
用户新建章节 → 生成计划后，章名永远为空：
* ``chapter_plan/pipeline.py::_save_plan_node`` 的 ``plan_payload`` 白名单**没有 title**
  ⇒ 即便模型给了，也被静默丢弃（不落 ``plan_json``）；
* 该节点只 ``UPDATE chapters SET plan_json``，**从不写 ``chapters.title``**
  ⇒ 没有任何落库路径。
真实根因不是「提示词教了没接」——v3 输出契约**没有** ``title``（§7「不允许任何额外键」）；
本批升版 ``director_planner-v4``（§7 输出契约新增 ``title`` + 起名/回显规则），
落库侧同批接上（本文件即其判别面）。

四条判别 + 一条契约看守（各自对应一条突变，撤修复必红）
------------------------------------------------------
1. ``test_empty_title_is_backfilled`` —— 列值 NULL + planner 给名 ⇒ 落库有标题；
2. ``test_blank_title_column_is_backfilled`` —— 列值空串（TRIM 为空）同样回填；
3. ``test_author_title_is_never_overwritten`` —— 作者已填标题 + planner 给不同名 ⇒
   **原样保留**（正反一对，防「过修」；对应 ``TTL-2``）；
4. ``test_blank_planner_title_never_touches_column`` —— null / 空串 / 纯空白 ⇒ 不写且不报错；
5. ``test_plan_json_carries_planner_title`` —— 白名单真的登记了 title（对应 ``TTL-3``）；
6. ``test_prompt_v4_declares_title_in_output_schema`` —— v4 的 §7 输出契约必须声明 title
   且规则含「起名 / 回显」语义；同时钉「v3 是历史证据、输出契约里无 title」（对应 ``TTL-4``）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_plan.pipeline import _save_plan_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
PROMPT_V3 = PROMPTS_DIR / "director_planner-v3.md"
PROMPT_V4 = PROMPTS_DIR / "director_planner-v4.md"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "title_autofill.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "章名回填项目", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, *, title: str | None, number: int = 1) -> str:
    """``title=None`` ⇒ 列值 NULL；``title=""`` ⇒ 空串——两种「空标题」都要回填。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _run_save(db_path: Path, pid: str, cid: str, **overrides) -> dict:
    """直接驱动 save_plan 节点（State 节点），不经过引擎、不触发任何模型调用。"""
    output = {
        "schema_version": "director-plan.v1",
        "chapter_goal": "本章目标",
        "core_conflict": "本章冲突",
        "turning_point": "本章转折",
        "expected_role": "setup",
        "expected_word_count": 3000,
        "key_beats": [],
        "character_changes_planned": [],
        "information_releases": [],
        "hook_handling": [],
        "debt_handling": [],
        "deviations": [],
        "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        "open_questions": [],
        "notes_for_planner": None,
    }
    output.update(overrides)
    return _save_plan_node(
        {
            "db_path": db_path,
            "chapter_id": cid,
            "project_id": pid,
            "run_id": new_id("wfr"),
            "director_planner_output": output,
        }
    )


def _column(db_path: Path, cid: str, column: str) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {column} FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[column]


def _plan_json(db_path: Path, cid: str) -> dict:
    raw = _column(db_path, cid, "plan_json")
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# 1 / 2：空标题（NULL / 空串）⇒ 回填
# ---------------------------------------------------------------------------


def test_empty_title_is_backfilled(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title=None)

    _run_save(db_path, pid, cid, title="夜叩青石")

    assert _column(db_path, cid, "title") == "夜叩青石", (
        "空标题（NULL）章节必须获得 planner 起的章名——撤掉回填时本断言必红"
    )


def test_blank_title_column_is_backfilled(tmp_path: Path):
    """列值是空串（不是 NULL）⇒ 同样属「空标题」，必须回填（TRIM 口径）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="")

    _run_save(db_path, pid, cid, title="雁门残照")

    assert _column(db_path, cid, "title") == "雁门残照"


def test_tab_only_title_column_is_backfilled(tmp_path: Path):
    """列值是单个 TAB ⇒ 仍算空标题，必须回填（2026-10-23 批次二十七 S-1 真缺陷）。

    SQLite ``TRIM(x)`` **只吃 ASCII 空格**：``"\t"`` 的 ``LENGTH(TRIM(x))`` 仍为 1，
    而 Python ``strip()`` 视其为空 ⇒ 两侧口径分叉时回填**静默不生效**。现在两侧统一
    ``{空格, TAB, CR, LF}``（SQL 用 ``TRIM(title, ' ' || CHAR(9) || CHAR(10) || CHAR(13))``）。
    撤掉 SQL 里的 ``CHAR(9)`` ⇒ 本用例必红。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="\t")

    _run_save(db_path, pid, cid, title="夜叩青石")

    assert _column(db_path, cid, "title") == "夜叩青石", (
        "仅含 TAB 的列值必须被判为空标题并回填（旧 SQL TRIM 只吃空格 ⇒ 静默不生效）"
    )


def test_nbsp_title_is_treated_as_non_empty(tmp_path: Path):
    """NBSP（U+00A0）**两侧都不算空白**（刻意排除：它是正文可见字符，不可被模型名覆盖）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="\u00a0")

    _run_save(db_path, pid, cid, title="模型想改名")

    assert _column(db_path, cid, "title") == "\u00a0"


# ---------------------------------------------------------------------------
# 3：作者标题一字不改（正反一对，防「无条件覆盖」的过修）
# ---------------------------------------------------------------------------


def test_author_title_is_never_overwritten(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="作者自定的章名")

    _run_save(db_path, pid, cid, title="模型另起的名字")

    assert _column(db_path, cid, "title") == "作者自定的章名", (
        "作者已填标题必须一字不改——无条件覆盖（TTL-2）会在这里红"
    )


def test_author_title_with_surrounding_spaces_is_kept_verbatim(tmp_path: Path):
    """非空标题即使带首尾空白也不得被 TRIM 回写（「一字不改」的字面口径）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="  作者留白标题  ")

    _run_save(db_path, pid, cid, title="模型新名")

    assert _column(db_path, cid, "title") == "  作者留白标题  "


# ---------------------------------------------------------------------------
# 4：planner 给空 / 纯空白 ⇒ 不写、不报错
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", [None, "", "   ", "\t", "\n\t "])
def test_blank_planner_title_never_touches_column(tmp_path: Path, blank):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title=None)

    result = _run_save(db_path, pid, cid, title=blank)

    assert result["plan_saved"] is True
    assert _column(db_path, cid, "title") is None, (
        f"planner 给 {blank!r} ⇒ 视同没给，不得写空标题"
    )


def test_planner_title_is_stripped_before_backfill(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title=None)

    _run_save(db_path, pid, cid, title="  夜叩青石  ")

    assert _column(db_path, cid, "title") == "夜叩青石", "回填值应取 strip 后的非空串"


# ---------------------------------------------------------------------------
# 5：白名单登记 title（落 plan_json，可追溯**模型建议名**）
#
# ⚠️ S-4（2026-10-23 批次二十七）：`plan_json["title"]` 与 `chapters.title` **同键不同义**——
# 前者 = **模型建议名**（含作者标题优先时被拒的那个值），后者 = **生效名**（作者优先，
# 仅空标题才回填）。键名保留 `title`（与提示词 §7 契约同名，便于追溯模型原输出），
# 消费方必须按落库面区分，不得混取。
# ---------------------------------------------------------------------------


def test_plan_json_carries_model_suggested_title(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title=None)

    _run_save(db_path, pid, cid, title="夜叩青石")

    assert _plan_json(db_path, cid).get("title") == "夜叩青石", (
        "plan_payload 白名单必须登记 title（模型建议名；TTL-3 删白名单行 ⇒ 这里红）"
    )


def test_model_suggested_title_recorded_while_effective_title_kept(tmp_path: Path):
    """同键不同义：作者标题不被覆盖（**生效名**），plan_json 仍如实记模型值（**建议名**）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="作者自定")

    _run_save(db_path, pid, cid, title="模型另起")

    assert _column(db_path, cid, "title") == "作者自定"
    assert _plan_json(db_path, cid).get("title") == "模型另起"


# ---------------------------------------------------------------------------
# 6：提示词契约（v4 声明 title + 起名/回显规则；v3 作为历史证据不含该契约）
# ---------------------------------------------------------------------------


def test_prompt_v4_declares_title_in_output_schema():
    assert PROMPT_V4.is_file(), "v4 提示词必须新建（不得原地改 v3）"
    text = PROMPT_V4.read_text(encoding="utf-8")
    output_section = text.split("## 7. Output Schema", 1)[1].split("## 8.", 1)[0]

    assert '"title": "string 或 null"' in output_section, (
        "v4 的 §7 输出契约必须声明 title——TTL-4 删掉该行 ⇒ 本断言红"
    )
    assert "起名" in output_section, "§7 规则区必须写明「空标题由你起名」的语义"
    assert "回显" in output_section, "§7 规则区必须写明「非空标题原样回显 / 不改写」的语义"
    assert "空 ⇒ 由你在输出里起名" in text, "§5 输入契约的 chapter.title 旁注必须补上"

    # v3 是历史证据：其 §7 输出契约里**没有** title（防「原地改 v3」冒充新契约）
    v3_text = PROMPT_V3.read_text(encoding="utf-8")
    v3_output = v3_text.split("## 7. Output Schema", 1)[1].split("## 8.", 1)[0]
    assert '"title"' not in v3_output, "v3 不得被原地修改（历史证据必须保持原样）"
