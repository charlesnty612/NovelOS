"""可读性算子的评审接线：新规则必须自动落到 review_report 的 warnings/errors。

接线点在 ``packages/workflows/chapter_review/pipeline._basic_checks_node``——
它把 ``scan_ai_patterns(prose)`` 的命中按 **severity** 分流（error → ``errors``
列表、其余 → ``warnings`` 文本行），故新规则只要自带 severity 就自动进桶，
不需要动那个文件。本文件用真实 DB 走一遍该节点，把这条接线钉住：

- 2 段 110 字（warning 档）→ ``warnings`` 出现 ``[AI-LONG-PARA]``、``errors`` 无它；
- 1 段 200 字（error 档）→ ``errors`` 出现 ``rule_id=AI-LONG-PARA`` 的结构化条目；
- 对话占比 0%（原 <8% → error，**2026-09-17 撤回**）→ 现在只进 ``warnings``，
  ``errors`` 里绝不出现 ``AI-DIALOGUE-LOW``（降级后的接线断言）；
- 相邻对白接词回声（``AI-DIALOGUE-ECHO``）→ 同样只进 ``warnings``。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.ai_patterns import dialogue_ratio
from packages.core.quality.wordcount import visible_chars
from packages.workflows.chapter_review.pipeline import _basic_checks_node

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_POOL = (
    "他站在柜台后头，把价签翻了个面。",
    "雨点打在油纸伞上，声音又密又匀。",
    "巷口的灯笼晃了两下，火光歪向一边。",
)


def _exact(chars: int) -> str:
    """恰好 ``chars`` 个可见字的段落（真实中文句子拼接，末句截断）。"""
    text = ""
    while visible_chars(text) < chars:
        text += _POOL[len(text) % len(_POOL)]
    out = text[:chars]
    assert visible_chars(out) == chars
    return out


def _filler(paragraphs: int = 6, chars: int = 90) -> str:
    return "\n\n".join(_exact(chars) for _ in range(paragraphs))


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_chapter_with_draft(db_path: Path, prose: str) -> str:
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, 1, '', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', "
            "NULL, NULL, ?)",
            (new_id("drf"), cid, prose, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _basic_checks(db_path: Path, chapter_id: str, prose: str) -> dict:
    return _basic_checks_node(
        {
            "db_path": str(db_path),
            "chapter_id": chapter_id,
            # 字数带内，避免 W-LEN-DEVIATION 混进断言
            "target_word_count": visible_chars(prose),
        }
    )["review_report"]


def test_long_para_warning_lands_in_warnings(tmp_path: Path):
    prose = _filler() + "\n\n" + "\n\n".join([_exact(110), _exact(110)])
    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks(db_path, cid, prose)

    hit = next(
        (h for h in report["ai_pattern_hits"] if h["rule_id"] == "AI-LONG-PARA"), None
    )
    assert hit is not None, "basic_checks 必须把长段命中带进 ai_pattern_hits"
    assert hit["severity"] == "warning"
    assert any("[AI-LONG-PARA]" in w for w in report["warnings"]), (
        f"warning 档应进 warnings 文本行，实际 warnings={report['warnings']}"
    )
    assert not [e for e in report["errors"] if e.get("rule_id") == "AI-LONG-PARA"]


def test_long_para_error_lands_in_errors(tmp_path: Path):
    prose = _filler() + "\n\n" + _exact(200)
    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks(db_path, cid, prose)

    errors = [e for e in report["errors"] if e.get("rule_id") == "AI-LONG-PARA"]
    assert len(errors) == 1, (
        f"error 档应进 errors 结构化列表，实际 errors={report['errors']}"
    )
    assert errors[0]["severity"] == "error"
    assert errors[0]["max_chars"] == 200


def test_dialogue_low_zero_dialogue_stays_warning(tmp_path: Path):
    """通篇无对话（0%）→ **只有 warning**（error 档 2026-09-17 撤回）。

    人类锚点书首章就是 0.0% 对话：该档会判人类正文违规，已撤。
    """
    prose = _filler(8, 90)
    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks(db_path, cid, prose)

    hits = [h for h in report["ai_pattern_hits"] if h["rule_id"] == "AI-DIALOGUE-LOW"]
    assert hits and hits[0]["severity"] == "warning", f"hits={hits}"
    assert hits[0]["dialogue_chars"] == 0
    assert not [e for e in report["errors"] if e.get("rule_id") == "AI-DIALOGUE-LOW"], (
        f"error 档已撤回，0% 对话也不得进 errors：{report['errors']}"
    )
    assert any("[AI-DIALOGUE-LOW]" in w for w in report["warnings"]), report["warnings"]


def test_dialogue_echo_lands_in_warnings(tmp_path: Path):
    """接词回声（warning 档）→ ``ai_pattern_hits`` 有条目、``warnings`` 有文本行。"""
    prose = (
        _filler(8, 90)
        + "\n\n“三百人，够不够守城？”\n\n“三百。”\n\n“三百人，吃什么？”\n\n“够七天。”"
        + "\n\n“七天之后呢？”\n\n“七天之后，看你守不守得住。”"
    )
    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks(db_path, cid, prose)

    hits = [h for h in report["ai_pattern_hits"] if h["rule_id"] == "AI-DIALOGUE-ECHO"]
    assert hits, f"echo 命中应进 ai_pattern_hits：{report['ai_pattern_hits']}"
    assert hits[0]["severity"] == "warning"
    assert hits[0]["count"] >= 1
    assert any("[AI-DIALOGUE-ECHO]" in w for w in report["warnings"])
    assert not [e for e in report["errors"] if e.get("rule_id") == "AI-DIALOGUE-ECHO"]


def test_dialogue_low_warning_lands_in_warnings(tmp_path: Path):
    """对话占比 11.9% 落在 warning 档（12% 之下）。"""
    # 叙述 878 可见字（短段拼接，避免顺带触发长段规则）+ 引号 2 + 对话 119 = 999
    prose = _filler(9, 90) + "\n\n" + _exact(68) + "\n\n" + "“" + _exact(119) + "”"
    assert dialogue_ratio(prose) < 0.12, dialogue_ratio(prose)

    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks(db_path, cid, prose)

    hits = [h for h in report["ai_pattern_hits"] if h["rule_id"] == "AI-DIALOGUE-LOW"]
    assert hits, "11.9% 应命中"
    assert hits[0]["severity"] == "warning"
    assert any("[AI-DIALOGUE-LOW]" in w for w in report["warnings"])
