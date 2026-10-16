"""OOC 审查的判据必须非空（2026-10-16 批次十一 · Lead 自办）。

## 缺陷

``chapter_review/pipeline.py::_collect_settings_digest`` 此前用**顶层**
``core_json.one_line``（兜底 ``.statement``）作为 OOC 判据。而全仓
``"one_line"`` 只有 4 处、**全部是读取或构造**；三个 ``core_json`` 写入方
（``project_init`` / ``domain.character.service`` / ``story_state.write_through``）
只产**现行五键**：

    motivation / goal / conflict / distinctive_trait / relationships

⇒ ``critic-v1.md`` 规则 6「设定一致性（OOC）审查」拿到的 ``one_line`` 恒为
``""``，**对所有经 UI / project_init 创建的角色结构性失明**：作者看到「审过了」，
而人物约束一条都没进去。

## 为什么一直没被发现

``tests/workflow/test_chapter_review_settings_digest.py`` 的夹具用的就是
``{"one_line": ...}`` —— 一个**没有任何生产方会产出**的形状，绿灯靠虚构成立。
那是本仓坑区「夹具抄形状导致缺陷被断言」的**反向姊妹形状**：
那次是抄实现的错，这次是**凭空发明**了一个实现不产的形状。

⇒ 本文件的期望值**全部取自生产契约**（``project_init`` 的键集 /
``CharacterTab::CORE_FIELDS`` / ``character_designer-v1.md`` 的 relationships 形状），
没有一处是「跑一下看返回什么」。
"""

from __future__ import annotations

from typing import Any

from packages.workflows.chapter_review.pipeline import _collect_settings_digest, _core_one_line

#: 现行五键契约（与 project_init / CharacterTab 一致）
PRODUCTION_CORE: dict[str, Any] = {
    "motivation": "查明母亲失踪真相",
    "goal": "留在苏家不被赶走",
    "conflict": "闺蜜情谊对上家族立场",
    "distinctive_trait": "外柔内刚，记账极准",
    "relationships": [
        {"to_name": "苏挽", "relation_type": "bestie", "one_line": "从小的闺蜜"},
    ],
}


# ---------------------------------------------------------------------------
# 单点：_core_one_line
# ---------------------------------------------------------------------------


def test_production_shape_yields_non_empty_criterion():
    """核心：现行五键形状 ⇒ 判据非空。

    撤修复（改回读顶层 one_line/statement）⇒ 本用例必红。
    """
    line = _core_one_line(PRODUCTION_CORE)
    assert line, "现行五键形状被判成空 —— OOC 审查对真实角色结构性失明"
    assert "外柔内刚" in line
    assert "留在苏家不被赶走" in line


def test_relationship_is_part_of_the_criterion():
    """关系必须进判据 —— 否则「闺蜜」这条设定对审稿人根本不存在。"""
    line = _core_one_line(PRODUCTION_CORE)
    assert "苏挽" in line, f"relationships 未进判据：{line!r}"
    assert "从小的闺蜜" in line


def test_legacy_top_level_one_line_still_accepted():
    """反向不变量：旧形状（导入 / 历史数据）**不得**被这次修复打断。

    防「按契约改成只读五键」这种一刀切——那会让存量数据的 OOC 判据从
    「有值」变成「空」，比修之前更糟。
    """
    assert _core_one_line({"one_line": "林渊之妻，怀疑丈夫隐瞒父亲死因。"}) != ""


def test_empty_core_yields_empty_string_not_crash():
    """空 / 垃圾形状 ⇒ 空串，不抛（digest 允许空值，由 prompt 侧判「未声明」）。"""
    assert _core_one_line({}) == ""
    assert _core_one_line({"relationships": "not-a-list"}) == ""
    assert _core_one_line({"goal": 123, "relationships": [None, "x"]}) == ""
    assert _core_one_line({"motivation": "   "}) == ""


def test_relationship_only_core_still_produces_criterion():
    """只有 relationships（作者只填了这一项）⇒ 也必须有判据。"""
    line = _core_one_line({"relationships": [{"to_name": "苏挽", "one_line": "闺蜜"}]})
    assert "闺蜜" in line


def test_criterion_is_bounded():
    """超长输入必须截断（digest 会整段进 prompt，字符预算有界）。"""
    line = _core_one_line({"distinctive_trait": "长" * 5000})
    assert 0 < len(line) <= 260, f"判据未截断，长度 {len(line)}"


# ---------------------------------------------------------------------------
# 端到端：digest 里真的带上判据
# ---------------------------------------------------------------------------


def test_digest_carries_production_shape_criterion(tmp_path):
    """走真库端到端：五键形状的角色进 digest 后 ``one_line`` 必须非空。"""
    import json

    from packages.core.db import apply_migrations, get_connection
    from packages.core.ids import now_iso

    db_path = tmp_path / "novelos.db"
    apply_migrations(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, created_at, updated_at)"
            " VALUES ('prj_x', 'p', '', ?, ?)", (now_iso(), now_iso()),
        )
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json,"
            " visibility, created_at, updated_at)"
            " VALUES ('char_x', 'prj_x', '周宁', 'other', ?, 'PUBLIC', ?, ?)",
            (json.dumps(PRODUCTION_CORE, ensure_ascii=False), now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    digest = _collect_settings_digest(str(db_path), "prj_x")
    chars = [d for d in digest if d.get("kind") == "character"]

    assert chars, "digest 里没有角色条目"
    assert chars[0]["one_line"], "digest 里的角色 one_line 为空 ⇒ OOC 审查无从对照"
    assert "苏挽" in chars[0]["one_line"]
