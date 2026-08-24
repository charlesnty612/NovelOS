"""exporter 单元测试（V1.4 / Sprint 16）。

覆盖：
- ``build_txt`` 整书：UTF-8 BOM、按章节 number 升序、含正文与标题；
- ``build_txt`` 单章：仅指定 chapter_no 的章节；其它章不出现；
- ``build_txt`` 空项目：仅标题占位；
- ``build_docx``：合法 zip + ``document.xml`` 含正文字符串；
- ``build_fanqie_package``：含正文 + 分隔线 + 大纲；总正文 >=1 万字（或全部章节+大纲段落）；
- ``build_fanqie_package`` 空项目：仅分隔线 + 大纲占位；
- ``plan_to_outline`` 容错：None / 空 dict / 缺字段 → ``（暂无）``；
- COMMITTED 章节正文取最新 draft version；多版本时取 MAX(version)。

模式：直接调 ``packages.core.exporter`` 函数；用 ``tmp_path`` 临时 db。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.exporter import (
    ExportScope,
    build_docx,
    build_fanqie_package,
    build_txt,
    plan_to_outline,
)
from packages.core.ids import new_id, now_iso


def _setup(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _create_project(db_path: Path, name: str) -> str:
    conn = get_connection(db_path)
    try:
        pid = new_id("prj")
        conn.execute(
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, name, None, None, None, "ACTIVE", now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _create_chapter(db_path: Path, project_id: str, number: int, title: str | None) -> str:
    conn = get_connection(db_path)
    try:
        cid = new_id("ch")
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cid, project_id, number, title, "{}", "COMMITTED", "VISIBLE", None, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, chapter_id: str, version: int, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (new_id("dr"), chapter_id, version, content, "agent:writer:v1", now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _set_plan(db_path: Path, chapter_id: str, plan: dict) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# plan_to_outline 容错
# ---------------------------------------------------------------------------


def test_plan_to_outline_handles_none_and_empty():
    assert plan_to_outline(None) == "（暂无）"
    assert plan_to_outline({}) == "（暂无）"


def test_plan_to_outline_renders_known_fields():
    out = plan_to_outline(
        {
            "chapter_goal": "觉醒",
            "core_conflict": "内 vs 外",
            "turning_point": "镜碎",
            "expected_role": "setup",
            "key_beats": [{"purpose": "起"}, {"purpose": "承"}],
        }
    )
    assert "目标：觉醒" in out
    assert "冲突：内 vs 外" in out
    assert "转折：镜碎" in out
    assert "功能：setup" in out
    assert "1. 起" in out
    assert "2. 承" in out


# ---------------------------------------------------------------------------
# build_txt
# ---------------------------------------------------------------------------


def test_build_txt_book_orders_chapters_and_includes_body(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "测试书")
    c1 = _create_chapter(s.db_path, pid, 2, "第二")
    c2 = _create_chapter(s.db_path, pid, 1, "第一")
    c3 = _create_chapter(s.db_path, pid, 3, "第三")
    _insert_draft(s.db_path, c1, 1, "第二章正文")
    _insert_draft(s.db_path, c2, 1, "第一章正文")
    _insert_draft(s.db_path, c3, 1, "第三章正文")

    data = build_txt(str(s.db_path), pid, ExportScope(kind="book"))
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = data.decode("utf-8-sig")
    assert "测试书" in text
    # 章节按 number ASC 出现
    pos1 = text.index("第一章")
    pos2 = text.index("第二章")
    pos3 = text.index("第三章")
    assert pos1 < pos2 < pos3
    assert "第一章正文" in text
    assert "第二章正文" in text


def test_build_txt_uses_latest_draft_version(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "版本测试")
    cid = _create_chapter(s.db_path, pid, 1, "C1")
    _insert_draft(s.db_path, cid, 1, "v1-old")
    _insert_draft(s.db_path, cid, 2, "v2-new")
    data = build_txt(str(s.db_path), pid, ExportScope(kind="book"))
    text = data.decode("utf-8-sig")
    assert "v2-new" in text
    assert "v1-old" not in text


def test_build_txt_single_chapter_filters_by_number(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "单章测试")
    for n, t in [(1, "一"), (2, "二"), (3, "三")]:
        cid = _create_chapter(s.db_path, pid, n, t)
        _insert_draft(s.db_path, cid, 1, f"第{n}章正文")

    data = build_txt(str(s.db_path), pid, ExportScope(kind="chapter", chapter_no=2))
    text = data.decode("utf-8-sig")
    assert "第2章 二" in text
    assert "第2章正文" in text
    assert "第1章" not in text
    assert "第3章" not in text


def test_build_txt_empty_project_only_header(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "空项目")
    data = build_txt(str(s.db_path), pid, ExportScope(kind="book"))
    text = data.decode("utf-8-sig")
    assert "空项目" in text
    # 无章节 → 无 "第" 字
    assert "第" not in text


def test_build_txt_chapter_without_draft_shows_placeholder(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "占位")
    cid = _create_chapter(s.db_path, pid, 1, "C1")  # 无 draft
    data = build_txt(str(s.db_path), pid, ExportScope(kind="book"))
    text = data.decode("utf-8-sig")
    assert "本章尚无正文" in text
    assert "第1章" in text


# ---------------------------------------------------------------------------
# build_docx
# ---------------------------------------------------------------------------


def test_build_docx_is_valid_zip_with_body_text(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "Docx测试")
    cid = _create_chapter(s.db_path, pid, 1, "C1")
    _insert_draft(s.db_path, cid, 1, "正文一段\n正文二段")

    data = build_docx(str(s.db_path), pid, ExportScope(kind="book"))
    assert zipfile.is_zipfile(io.BytesIO(data))
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "[Content_Types].xml" in z.namelist()
    assert "word/document.xml" in z.namelist()
    doc = z.read("word/document.xml").decode("utf-8")
    assert "正文一段" in doc
    assert "正文二段" in doc
    assert "第1章" in doc


def test_build_docx_special_chars_are_xml_escaped(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "X")
    cid = _create_chapter(s.db_path, pid, 1, "C")
    _insert_draft(s.db_path, cid, 1, "含<尖括号>与&符号")
    data = build_docx(str(s.db_path), pid, ExportScope(kind="book"))
    z = zipfile.ZipFile(io.BytesIO(data))
    doc = z.read("word/document.xml").decode("utf-8")
    # 原文中的字面 "<" 在 docx 中应被转义为 &lt;
    assert "&lt;尖括号&gt;" in doc
    assert "&amp;" in doc
    # 原始未转义的字面 < 不能在 text 段里出现
    assert "<尖括号>" not in doc


# ---------------------------------------------------------------------------
# build_fanqie_package
# ---------------------------------------------------------------------------


def _make_long_chapters(db_path: Path, project_id: str, n: int, chars_each: int) -> list[str]:
    ids = []
    body = "字" * chars_each
    for i in range(1, n + 1):
        cid = _create_chapter(db_path, project_id, i, f"章{i}")
        _insert_draft(db_path, cid, 1, body)
        ids.append(cid)
    return ids


def test_build_fanqie_package_includes_outline_and_body(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "番茄测试")
    # 12 章 × 1200 字 = 14400 > 10000，应在第 8~9 章边界截断
    _make_long_chapters(s.db_path, pid, 12, 1200)
    data = build_fanqie_package(str(s.db_path), pid)
    text = data.decode("utf-8-sig")
    assert data.startswith(b"\xef\xbb\xbf")
    assert "========== 故事大纲 ==========" in text
    # 大纲段含章标题
    assert "第1章 章1" in text
    assert "第12章 章12" in text
    # 正文部分不超过 10000 字（在最近章节边界截断；至少含前几章）
    head, _, _ = text.partition("==========")
    body = head
    # body 中应含至少 1 章正文（章节边界截断）
    assert "第1章 章1" in body


def test_build_fanqie_package_full_when_short(tmp_path: Path):
    """总字数 < 10000 → 全量输出（不截断）。"""
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "短篇番茄")
    # 3 章 × 500 字 = 1500 < 10000
    for i in range(1, 4):
        cid = _create_chapter(s.db_path, pid, i, f"章{i}")
        _insert_draft(s.db_path, cid, 1, "字" * 500)
    data = build_fanqie_package(str(s.db_path), pid)
    text = data.decode("utf-8-sig")
    assert "第3章 章3" in text.split("==========")[0]


def test_build_fanqie_package_empty_project_still_has_delimiter(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "空番茄")
    data = build_fanqie_package(str(s.db_path), pid)
    text = data.decode("utf-8-sig")
    assert "========== 故事大纲 ==========" in text
    assert "（暂无）" in text  # 大纲段占位


def test_build_fanqie_package_uses_plan_json_outline(tmp_path: Path):
    s = _setup(tmp_path)
    pid = _create_project(s.db_path, "大纲渲染")
    c1 = _create_chapter(s.db_path, pid, 1, "章一")
    c2 = _create_chapter(s.db_path, pid, 2, "章二")
    _insert_draft(s.db_path, c1, 1, "短")
    _insert_draft(s.db_path, c2, 1, "短")
    _set_plan(
        s.db_path,
        c1,
        {
            "chapter_goal": "主角觉醒",
            "core_conflict": "灵根被封",
            "turning_point": "血脉初动",
            "key_beats": [{"purpose": "夜入禁地"}],
        },
    )
    _set_plan(s.db_path, c2, {"chapter_goal": "初次试炼"})

    data = build_fanqie_package(str(s.db_path), pid)
    text = data.decode("utf-8-sig")
    _, _, outline = text.partition("==========")
    assert "目标：主角觉醒" in outline
    assert "冲突：灵根被封" in outline
    assert "目标：初次试炼" in outline