"""角色圣经必须到达 writer：外部报告的端到端复现（2026-10-16 批次十一 · Lead 自办）。

## 报告原文

> 「角色圣经里面角色间的关系是闺蜜，结果在正文里，那个角色却以业务员出场；
>   另外，此角色是用户后面手动添加的角色，不知道是不是这个原因」

## 本文件钉的那条链

角色圣经存在 ``characters.core_json``（CharacterTab 可编辑，五键契约含 ``relationships``）。
生产 writer 走 ``context_mode="paged"`` + ``relevance_trim=True``，装配要过**三层裁剪**：

    L1 触发层  _apply_injection_policy → 未命中 ⇒ {id,name,role}   ← core_json 在此丢失
    L2 relevance 层                     → 未 involved ⇒ {id,name,relevance_summary}
    L3 分页层（_build_writer_input_paged）→ 不在 touched ⇒ {id,name,role,summary_marker}

⚠️ **修一层不修另一层等于没修**（L1 已丢的数据后层救不回来），反之亦然。
本文件同时钉住两端，撤任何一端都会红。

## 三条夹具纪律（都是本文件实际踩过的）

1. **project_id / chapter_id / character_id 必须逐用例唯一**：装配缓存是**进程内
   模块级 dict**，同 id + 其余键维度相同 ⇒ **跨用例脏命中**。本文件初版所有用例
   共用 ``prj_x`` / ``ch_1``，一个用例的断言实际读的是另一个用例的缓存条目，
   表现为「撤掉修复后测试仍全绿」——量具坏了，不是被测物对了。
2. **显式传** ``context_mode`` / ``relevance_trim``：``tests/unit/test_writer_input_paged.py``
   在**模块导入时**写 ``os.environ["NOVELOS_CONTEXT_RELEVANCE"]="off"``，``xdist``
   分组不同会让它串进本文件（E3 实测：单跑绿、全量红）。
3. **断言失败时第一件事是读实际形态**：本文件曾因「主角先在 relevance 被削」而报
   ``KeyError: '_injection'``，看着像键写错了，实则是**更靠前的机制**在起作用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.context_engine import build_writer_input
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import now_iso

#: 作者在 CharacterTab 手工写的角色圣经（现行五键契约）
AUTHOR_CORE = {
    "motivation": "查明母亲失踪真相",
    "goal": "留在苏家不被赶走",
    "conflict": "与苏挽的闺蜜情谊对上家族的立场",
    "distinctive_trait": "外柔内刚，记账极准",
    "relationships": [
        {"to_name": "苏挽", "relation_type": "bestie", "one_line": "从小的闺蜜"},
    ],
}

_INSERT_CHAR = (
    "INSERT INTO characters (character_id, project_id, name, role, core_json,"
    " visibility, aliases, inject_mode, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, 'PUBLIC', '[]', 'auto', ?, ?)"
)


def _seed(tmp_path: Path, tag: str) -> tuple[Path, dict[str, str]]:
    """建临时库 + 项目 + 一章 + 两个角色（主角 / 报告人手动添加的那个）。

    返回 ``(db_path, ids)``。``tag`` 逐用例唯一（见文件头纪律 1）。
    """
    ids = {
        "pid": f"prj_{tag}",
        "cid": f"ch_{tag}",
        "prod": f"char_prod_{tag}",
        "manual": f"char_manual_{tag}",
    }
    db_path = tmp_path / "novelos.db"
    apply_migrations(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, created_at, updated_at)"
            " VALUES (?, 'p', '', ?, ?)", (ids["pid"], now_iso(), now_iso()),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status,"
            " created_at, updated_at) VALUES (?, ?, 1, 'c1', 'DRAFTED', ?, ?)",
            (ids["cid"], ids["pid"], now_iso(), now_iso()),
        )
        for key, name, role in ((("prod"), "林渊", "protagonist"),
                                (("manual"), "周宁", "other")):
            conn.execute(
                _INSERT_CHAR,
                (ids[key], ids["pid"], name, role,
                 json.dumps(AUTHOR_CORE, ensure_ascii=False), now_iso(), now_iso()),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path, ids


def _entry(payload: dict, character_id: str) -> dict:
    """从 payload 里取出指定角色条目；找不到即失败（不静默跳过）。"""
    for c in payload.get("character_state_excerpts") or []:
        if isinstance(c, dict) and c.get("character_id") == character_id:
            return c
    raise AssertionError(
        f"payload 里没有 {character_id}；实际条目："
        f"{[c.get('character_id') for c in payload.get('character_state_excerpts') or []]}"
    )


def _assemble(db_path: Path, chapter_id: str, scene_characters: list[str]) -> dict:
    """走**生产口径**装配：paged + relevance on。"""
    return build_writer_input(
        db_path,
        chapter_id,
        {"characters": scene_characters},
        context_mode="paged",
        relevance_trim=True,
        target_word_count=2000,
        namespace="test",
    )


def _update_core(db_path: Path, character_id: str, core: dict) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE characters SET core_json = ? WHERE character_id = ?",
                     (json.dumps(core, ensure_ascii=False), character_id))
        conn.commit()
    finally:
        conn.close()


def _update_plan(db_path: Path, chapter_id: str, plan: dict) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE chapters SET plan_json = ? WHERE chapter_id = ?",
                     (json.dumps(plan, ensure_ascii=False), chapter_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 核心复现
# ---------------------------------------------------------------------------


def test_manual_character_in_scene_keeps_character_bible(tmp_path: Path):
    """**报告场景**：手动添加的角色进了本场 scene ⇒ 角色圣经必须完整到达。

    撤 L1（``_is_triggered`` 的 entity_id）或撤 L3（放行条件）任一，本用例必红。
    """
    db_path, ids = _seed(tmp_path, "scene")

    entry = _entry(_assemble(db_path, ids["cid"], [ids["manual"]]), ids["manual"])

    assert entry.get("core_json"), (
        "手动添加且本场出场的角色被削成了没有 core_json 的摘要行 —— "
        "模型只拿到一个名字，于是自造了职业（报告里的「业务员」）"
    )
    assert entry["core_json"]["relationships"][0]["one_line"] == "从小的闺蜜", (
        "闺蜜这条关系没有到达 writer"
    )


def test_protagonist_keeps_character_bible(tmp_path: Path):
    """主角也必须保住——实测修复前 ``characters_full == 0``，连主角都被削平。"""
    db_path, ids = _seed(tmp_path, "prodfull")

    entry = _entry(_assemble(db_path, ids["cid"], []), ids["prod"])  # 故意不给 scene

    assert entry.get("core_json"), "主角被 relevance/paged 层削平了"
    assert entry.get("role") == "protagonist"


def test_protagonist_kept_even_when_plan_never_mentions_them(tmp_path: Path):
    """⚠️ 判别 **L1** 那处：计划文本里**根本没提**主角时，主角仍须保住。

    与 :func:`test_protagonist_keeps_character_bible` 的区别：那条的章节
    ``plan_json`` 为空 ⇒ 走 ``trigger_corpus_empty`` 全量分支，判别不到 L1；
    本条把计划写成「夜访古董店」（不含任何角色名/id）⇒ ``trigger_corpus``
    非空且**触发不到主角** ⇒ 若 L1 不放行主角，主角会在 relevance 与 paged
    两层之**上游**就变成摘要，而那两层再想放行也救不回来。

    依据：:mod:`relevance` 的 docstring「主角与 ``inject_mode='always'`` 的实体始终
    完整保留」是**既有成文承诺**，此前在整条链上一处都不兑现（两层裁剪互相抵消）。
    """
    db_path, ids = _seed(tmp_path, "protoplan")
    _update_plan(db_path, ids["cid"], {"chapter_goal": "夜访古董店"})

    entry = _entry(_assemble(db_path, ids["cid"], []), ids["prod"])  # scene 也不给

    assert entry.get("core_json"), (
        "计划文本未提及主角时，L1 就把它降级了 —— 主角的身份与关系设定从此不到达 writer"
    )


def test_always_injected_character_keeps_character_bible(tmp_path: Path):
    """``inject_mode='always'``：0010 迁移注释承诺「无视命中，永远完整注入」。"""
    db_path, ids = _seed(tmp_path, "always")
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE characters SET inject_mode = 'always' WHERE character_id = ?",
                     (ids["manual"],))
        conn.commit()
    finally:
        conn.close()

    entry = _entry(_assemble(db_path, ids["cid"], []), ids["manual"])  # 不在本场

    assert entry.get("core_json"), "inject_mode='always' 的角色被削平 —— 0010 的承诺未兑现"


# ---------------------------------------------------------------------------
# 反向不变量：不得「一律全量」（那只是把问题反过来）
# ---------------------------------------------------------------------------


def test_untouched_out_of_scene_character_is_still_trimmed(tmp_path: Path):
    """既不在本场、又不是常驻/主角、还没被 commit 触达 ⇒ **仍应**被降级。

    防「上面三条放行条件写成永真」——那会让分页模式退化成 full 模式、
    token 预算失控，且是对本仓既有契约的无声推翻。
    """
    db_path, ids = _seed(tmp_path, "trim")

    entry = _entry(_assemble(db_path, ids["cid"], []), ids["manual"])  # 主角之外无人

    assert not entry.get("core_json"), (
        "不在本场、非常驻、非主角、无 commit 触达的角色仍被全量注入 —— "
        "分页模式的 token 预算前提被无声破坏"
    )
    assert entry.get("name") == "周宁", "降级形态仍须保留可识别信息（name）"


# ---------------------------------------------------------------------------
# 缓存维度：作者改完设定 / 改完计划后重跑同一章，必须重装（不脏命中）
# ---------------------------------------------------------------------------


def test_editing_character_bible_invalidates_cache(tmp_path: Path):
    """改 ``core_json`` 后同 ``chapter_no`` 重跑 ⇒ 必须返回新值（``chars_fp``）。"""
    db_path, ids = _seed(tmp_path, "cache1")
    scene = [ids["manual"]]

    first = _entry(_assemble(db_path, ids["cid"], scene), ids["manual"])
    assert first["core_json"]["goal"] == "留在苏家不被赶走"

    _update_core(db_path, ids["manual"], dict(AUTHOR_CORE, goal="离开苏家，独自查案"))

    second = _entry(_assemble(db_path, ids["cid"], scene), ids["manual"])
    assert second["core_json"]["goal"] == "离开苏家，独自查案", (
        "改角色圣经后重跑同一章命中了改动前的旧装配（脏命中）"
    )


@pytest.mark.parametrize("mutate", ["goal", "relationship"])
def test_cache_invalidates_on_every_authored_dimension(tmp_path: Path, mutate: str):
    """两条正例分开钉：文本型（goal）与结构型（relationships）都必须在键里。"""
    db_path, ids = _seed(tmp_path, f"cache2-{mutate}")
    scene = [ids["manual"]]
    _assemble(db_path, ids["cid"], scene)  # 先跑一次把条目写进缓存

    if mutate == "goal":
        new = dict(AUTHOR_CORE, goal="改过的目标")
    else:
        new = dict(AUTHOR_CORE, relationships=[{"to_name": "苏挽",
                                                "relation_type": "rival",
                                                "one_line": "反目"}])
    _update_core(db_path, ids["manual"], new)

    entry = _entry(_assemble(db_path, ids["cid"], scene), ids["manual"])
    assert entry["core_json"] == new, f"{mutate} 变更未使缓存失效"


def test_editing_plan_json_invalidates_cache(tmp_path: Path):
    """⚠️ 判别 ``plan_fp``（批次十一新增的 writer 键维度）。

    ``plan_json`` 进 payload 的 ``director_plan`` 四字段 + ``chapter.expected_role``
    + **触发扫描语料**（``_build_trigger_corpus``），但此前**从未入键**——
    director 键有 ``plan_fp``、writer 键没有，是 ``chars_fp`` 的逐字同形。
    后果：重排计划后同 ``state_version`` + 同 ``chapter_no`` 重跑 writer ⇒ 命中旧装配。

    判别点选 ``payload["director_plan"]["chapter_goal"]`` 而不是 excerpt 形态：
    excerpt 会被 L2/L3 裁剪、其形态变化会被 ``scene_fp`` 之外的别的维度搅浑；
    ``director_plan`` 是 ``plan_json`` 的**直接投影**，一个变量一个断言。
    主角恒全量 ⇒ 不受裁剪干扰。
    """
    db_path, ids = _seed(tmp_path, "planfp")

    # 先跑一次把「旧计划」的装配写进缓存（缺这一步就测不到「脏命中」）
    _update_plan(db_path, ids["cid"], {"chapter_goal": "夜访古董店"})
    assert _assemble(db_path, ids["cid"], [])["director_plan"]["chapter_goal"] == "夜访古董店"

    _update_plan(db_path, ids["cid"], {"chapter_goal": "夜探西市码头"})

    assert _assemble(db_path, ids["cid"], [])["director_plan"]["chapter_goal"] == "夜探西市码头", (
        "改 plan_json 后重跑同一章命中了旧装配 —— plan_fp 未进 writer 缓存键"
    )
