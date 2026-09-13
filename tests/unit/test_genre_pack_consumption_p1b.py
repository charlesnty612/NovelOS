"""题材库 P1b：scene_planner / writer 消费面 + 缓存键 pack 维度 + preview 状态行。

覆盖（roadmap P1b 验收口径）：
1. scene_planner 注入：绑定 → ``genre_pack`` 段含爽点摘要（type_id / name / strength /
   density_cap / min_interval_chapters / density_constraint 文本化）+ 溯源审计；
   未绑定 → 两键都不出现（零注入）；超预算 → ``__genre_pack_truncated__`` 标记。
2. 配比指令：题材声明 ``ratio_declarations`` 时注入声明文本 + 「逐 scene 标注
   scene_type 且字数分摊遵守配比」指令（``ratio_instruction``）；未声明 → 不注入。
3. writer 文风消费：题材 ``style_constraints`` 合并为 ``style_constraints.genre_style``
   子键（不覆盖既有键；作者样例存在时带 ``priority=below_author_style_samples``）。
4. writer 缓存键 pack 维度：换 pack version / 绑定 / 解绑必 miss（spy 计数判定）。
5. preview L1 ``genre_pack`` 状态行：绑定 / 未绑定都给状态。

突变验证（记录见交接）：把 ``writer_input.py`` 里的 ``genre_pack_ref`` 从 writer 缓存键
撤除 → 第 4 类用例必红；把 ``_genre_pack_excerpt`` 的 scene_planner / writer 分支撤除 →
第 1/3 类用例必红。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine import build_writer_input
from packages.core.context_engine.builders import _cache_reset
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.genre import GenrePackCreate, GenrePackService, GenrePackUpdate
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_write.pipeline import _collect_scene_planner_inputs

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PLAN: dict = {
    "chapter_goal": "第一章目标",
    "core_conflict": "冲突",
    "turning_point": "转折",
    "key_beats": [],
    "expected_word_count": 3000,
}

_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "min_interval_chapters": 3,
        },
        {"type_id": "contrast", "name": "反差", "strength": "s", "density_cap": "每章 ≤1 次"},
    ],
    "ratio_declarations": {"action": 0.7, "transition": 0.3},
    "style_constraints": {
        "pov": "third_person_limited",
        "sentence_style": "短句为主，对话推进",
        "forbidden_words": ["仿佛", "如同"],
    },
    "pacing": {"chapter_word_band": {"low": 2400, "high": 3600}},
}

_SCENE_PLAN: dict = {
    "scenes": [
        {"scene_id": "s1", "purpose": "开场", "scene_type": "action", "target_words": 1800},
        {"scene_id": "s2", "purpose": "过场", "scene_type": "transition", "target_words": 600},
    ]
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "题材项目") -> str:
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


def _insert_chapter(db_path: Path, pid: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, '{"chapter_goal": "x"}', now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _create_pack(
    db_path: Path, pack_id: str = "gp_kc", payload: dict | None = None,
) -> None:
    GenrePackService(db_path).create(
        GenrePackCreate(
            name="男主快穿", genre_tag="快穿",
            payload=_PAYLOAD if payload is None else payload, pack_id=pack_id,
        )
    )


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _insert_style_sample(db_path: Path, pid: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO author_style_samples (sample_id, project_id, title, content, "
            "created_at, updated_at) VALUES (?, ?, '样例', ?, ?, ?)",
            (new_id("sty"), pid, "短句。" * 50, now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. scene_planner 注入
# ---------------------------------------------------------------------------


def test_scene_planner_injects_payoff_summary_and_audit(tmp_path: Path):
    """绑定题材包 → scene_planner payload 含爽点摘要（密度约束文本化）+ 溯源审计。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")

    payload = _collect_scene_planner_inputs(str(db_path), cid, _PLAN)
    section = payload["genre_pack"]
    assert section["pack_id"] == "gp_kc"
    assert section["version"] == 1

    first = section["payoff_types"][0]
    assert first["type_id"] == "face_slap"
    assert first["name"] == "打脸"
    assert first["strength"] == "S"
    assert first["density_cap"] == "每卷 2~3 次"
    assert first["min_interval_chapters"] == 3
    assert first["density_constraint"] == "密度上限 每卷 2~3 次；同型最小间隔 3 章"

    assert payload["_genre_pack_consumed"]["consumed_fields"] == [
        "payoff_types", "ratio_declarations",
    ]
    # scene_planner 不吃结构模板 / pacing（那是 director 的面）
    assert "structure_templates" not in section
    assert "pacing" not in section


def test_scene_planner_unbound_no_genre_keys(tmp_path: Path):
    """未绑定题材包 → 不注入 genre_pack / _genre_pack_consumed（零行为变化）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    payload = _collect_scene_planner_inputs(str(db_path), cid, _PLAN)
    assert "genre_pack" not in payload
    assert "_genre_pack_consumed" not in payload


def test_scene_planner_ratio_instruction_present_when_declared(tmp_path: Path):
    """声明 ratio → 注入 ratio_declarations + ratio_instruction（含两条硬要求）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")

    section = _collect_scene_planner_inputs(str(db_path), cid, _PLAN)["genre_pack"]
    assert section["ratio_declarations"] == {"action": 0.7, "transition": 0.3}
    instruction = section["ratio_instruction"]
    assert "action=70%" in instruction and "transition=30%" in instruction
    assert "scene_type" in instruction
    assert "target_words" in instruction


def test_scene_planner_no_ratio_instruction_when_undeclared(tmp_path: Path):
    """未声明 ratio → 不出现 ratio_declarations / ratio_instruction 键。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(
        db_path,
        payload={
            "schema_version": "genre-pack.v1.0.0",
            "payoff_types": [{"type_id": "face_slap", "name": "打脸"}],
        },
    )
    GenrePackService(db_path).bind(pid, "gp_kc")

    section = _collect_scene_planner_inputs(str(db_path), cid, _PLAN)["genre_pack"]
    assert "payoff_types" in section
    assert "ratio_declarations" not in section
    assert "ratio_instruction" not in section
    assert "__genre_pack_truncated__" not in section


def test_scene_planner_payoff_summary_truncated_marker(tmp_path: Path):
    """爽点摘要超 1500 字符预算 → 截断并打 __genre_pack_truncated__ 标记。"""
    import json

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    payload = {
        "schema_version": "genre-pack.v1.0.0",
        "payoff_types": [
            {
                "type_id": f"type_{i:03d}",
                "name": f"类型{i}" * 10,
                "strength": "s",
                "density_cap": "每章 ≤1 次" * 5,
                "min_interval_chapters": i,
            }
            for i in range(60)
        ],
    }
    _create_pack(db_path, payload=payload)
    GenrePackService(db_path).bind(pid, "gp_kc")

    section = _collect_scene_planner_inputs(str(db_path), cid, _PLAN)["genre_pack"]
    assert section["__genre_pack_truncated__"] is True
    assert len(section["payoff_types"]) < 60
    assert len(json.dumps(section["payoff_types"], ensure_ascii=False)) <= 1500


# ---------------------------------------------------------------------------
# 2. writer 文风消费
# ---------------------------------------------------------------------------


def test_writer_merges_genre_style_without_overwriting(tmp_path: Path):
    """题材体例合并为 style_constraints.genre_style；既有键保持原值。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    payload = build_writer_input(db_path, cid, _SCENE_PLAN)
    style = payload["style_constraints"]
    # 既有默认键逐字段不变
    assert style["language"] == "zh-hans" or style["language"] == "zh-Hans"
    assert style["pov"] == "third_limited"
    assert style["forbidden_words"] == ["仿佛", "如同", "本章目标"]

    genre_style = style["genre_style"]
    assert genre_style["sentence_style"] == "短句为主，对话推进"
    assert genre_style["pov"] == "third_person_limited"  # 题材口径只在子键内
    assert genre_style["pack_id"] == "gp_kc"
    assert genre_style["version"] == 1
    assert genre_style["source"] == "gp_kc@1"
    # 无作者样例 → 不带优先级标记
    assert "priority" not in genre_style
    assert payload["_genre_pack_consumed"]["consumed_fields"] == ["style_constraints"]


def test_writer_author_style_samples_take_priority(tmp_path: Path):
    """有作者样例 → genre_style 带 below_author_style_samples 优先级标记。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _insert_style_sample(db_path, pid)
    _cache_reset()

    payload = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert payload["author_style_samples"]["samples"], "样例应被注入"
    assert (
        payload["style_constraints"]["genre_style"]["priority"]
        == "below_author_style_samples"
    )


def test_writer_unbound_and_styleless_pack_no_injection(tmp_path: Path):
    """未绑定 → 零注入；绑定但题材包未声明 style_constraints → 同样零注入。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    unbound = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert "genre_style" not in unbound["style_constraints"]
    assert "_genre_pack_consumed" not in unbound

    _create_pack(
        db_path,
        payload={"schema_version": "genre-pack.v1.0.0", "payoff_types": []},
    )
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    bound = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert "genre_style" not in bound["style_constraints"]
    assert "_genre_pack_consumed" not in bound


def test_writer_merge_helper_does_not_overwrite_existing_genre_style():
    """既有 genre_style 键（调用方显式配置）→ 合并助手返回 None（不覆盖）。"""
    from packages.core.context_engine.writer_input import _merge_genre_style

    inject = {"pack_id": "gp_a", "version": 2, "style_constraints": {"pov": "x"}}
    assert _merge_genre_style({"genre_style": {"mine": True}}, inject, has_author_style_samples=False) is None
    assert _merge_genre_style({"language": "zh-Hans"}, None, has_author_style_samples=False) is None
    merged = _merge_genre_style({}, inject, has_author_style_samples=False)
    assert merged is not None and merged["source"] == "gp_a@2"


# ---------------------------------------------------------------------------
# 3. writer 缓存键 pack 维度
# ---------------------------------------------------------------------------


def test_writer_cache_hit_when_pack_unchanged(tmp_path: Path, monkeypatch):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import writer_input as wi_mod

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    first = build_writer_input(db_path, cid, _SCENE_PLAN)
    second = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 1, "同参数 + 同题材包版本 → 命中缓存"
    assert first["style_constraints"] == second["style_constraints"]


def test_writer_cache_miss_after_pack_version_bump(tmp_path: Path, monkeypatch):
    """换 pack version（PUT 改 payload）必 miss——撤掉键维度会读回旧题材体例。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import writer_input as wi_mod

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    v1 = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert v1["style_constraints"]["genre_style"]["version"] == 1

    svc.update(
        "gp_kc",
        GenrePackUpdate(
            payload={**_PAYLOAD, "style_constraints": {"sentence_style": "改后的题材体例"}}
        ),
    )
    v2 = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "换 pack version 必须 miss（重新装配）"
    assert v2["style_constraints"]["genre_style"]["version"] == 2
    assert v2["style_constraints"]["genre_style"]["sentence_style"] == "改后的题材体例"

    v2_again = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "同版本再取应命中"
    assert v2_again["style_constraints"]["genre_style"]["version"] == 2


def test_writer_cache_miss_on_bind_and_unbind(tmp_path: Path, monkeypatch):
    """绑定 / 解绑都必 miss（题材包维度进 writer 键）；回到旧绑定命中各自条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    _cache_reset()

    from packages.core.context_engine import writer_input as wi_mod

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_kc")
    bound = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 1
    assert bound["style_constraints"]["genre_style"]["pack_id"] == "gp_kc"

    svc.unbind(pid)
    unbound_again = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "解绑必须 miss（未绑定条目此前未缓存）"
    assert "genre_style" not in unbound_again["style_constraints"]

    svc.bind(pid, "gp_kc")
    back_bound = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "回到同一 pack 版本应命中其条目"
    assert back_bound["style_constraints"]["genre_style"]["pack_id"] == "gp_kc"


def test_writer_cache_miss_on_switching_pack(tmp_path: Path, monkeypatch):
    """pack A → pack B 必 miss；回 A 命中 A 条目（两包条目并存）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(
        db_path, pack_id="gp_a",
        payload={**_PAYLOAD, "style_constraints": {"sentence_style": "A 体例"}},
    )
    _create_pack(
        db_path, pack_id="gp_b",
        payload={**_PAYLOAD, "style_constraints": {"sentence_style": "B 体例"}},
    )
    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_a")
    _cache_reset()

    from packages.core.context_engine import writer_input as wi_mod

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    a = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert a["style_constraints"]["genre_style"]["sentence_style"] == "A 体例"

    svc.bind(pid, "gp_b")
    b = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "换 pack 必须 miss"
    assert b["style_constraints"]["genre_style"]["sentence_style"] == "B 体例"

    svc.bind(pid, "gp_a")
    back = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 2, "回到 pack A 应命中其条目"
    assert back["style_constraints"]["genre_style"]["sentence_style"] == "A 体例"


# ---------------------------------------------------------------------------
# 4. scene_type 可选（缺席不报错）
# ---------------------------------------------------------------------------


def test_scene_type_optional_in_scene_plan(tmp_path: Path):
    """scene 无 scene_type 不报错；有则原样透传（不强制标注）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    without = {"scenes": [{"scene_id": "s1", "purpose": "开场"}]}
    payload = build_writer_input(db_path, cid, without)
    assert payload["scene_plan"]["scenes"][0]["target_words"] > 0
    assert "scene_type" not in payload["scene_plan"]["scenes"][0]

    _cache_reset()
    with_type = {"scenes": [{"scene_id": "s1", "purpose": "开场", "scene_type": "action"}]}
    payload2 = build_writer_input(db_path, cid, with_type)
    assert payload2["scene_plan"]["scenes"][0]["scene_type"] == "action"


# ---------------------------------------------------------------------------
# 4. 兼容：0025 未跑的旧库
# ---------------------------------------------------------------------------


def test_writer_cache_key_scene_plan_dimension(tmp_path: Path, monkeypatch):
    """scene_plan 变化自然换键（既有 scene_fp 维度）；与 pack 维度互不干扰。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import writer_input as wi_mod

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    build_writer_input(db_path, cid, _SCENE_PLAN)
    build_writer_input(db_path, cid, _SCENE_PLAN)
    assert calls["n"] == 1

    other_plan = {"scenes": [{"scene_id": "s1", "purpose": "另一场", "target_words": 3000}]}
    payload = build_writer_input(db_path, cid, other_plan)
    assert calls["n"] == 2, "scene_plan 变化必须 miss（scene_fp 维度）"
    assert payload["style_constraints"]["genre_style"]["pack_id"] == "gp_kc"


def test_writer_on_pre_0025_database_zero_injection(tmp_path: Path):
    """0025 未跑的旧库：peek 降级路径下 writer 键取 ``__none__``、零注入、不炸。"""
    import shutil

    mig_dir = tmp_path / "migrations_pre_0025"
    mig_dir.mkdir()
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name < "0025":
            shutil.copy(sql_file, mig_dir / sql_file.name)
    db_path = tmp_path / "old.db"
    apply_migrations(db_path, mig_dir)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    from packages.core.context_engine.builders_common import _peek_chapter_context

    assert _peek_chapter_context(db_path, cid)["genre_pack_ref"] is None
    payload = build_writer_input(db_path, cid, _SCENE_PLAN)
    assert "genre_style" not in payload["style_constraints"]
    assert "_genre_pack_consumed" not in payload


# ---------------------------------------------------------------------------
# 5. preview L1 题材包状态行
# ---------------------------------------------------------------------------


def test_preview_l1_genre_pack_unbound_status(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    out = preview_context(str(db_path), pid, cid)
    l1 = next(layer for layer in out["layers"] if layer["id"] == "L1")
    genre_items = [it for it in l1["items"] if it["kind"] == "genre_pack"]
    assert len(genre_items) == 1
    assert genre_items[0]["status"] == "unbound"
    assert "未绑定" in genre_items[0]["name"]


def test_preview_l1_genre_pack_bound_status(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    out = preview_context(str(db_path), pid, cid)
    l1 = next(layer for layer in out["layers"] if layer["id"] == "L1")
    item = next(it for it in l1["items"] if it["kind"] == "genre_pack")
    assert item["status"] == "bound"
    assert item["pack_id"] == "gp_kc"
    assert item["version"] == 1
    assert "爽点型 2 个" in item["name"]
    # director 面消费的段：payoff_types + pacing（本 payload 无 structure_templates）
    assert item["consumed_fields"] == ["payoff_types", "pacing"]
