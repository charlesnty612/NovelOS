"""题材库 P1a：director 装配的题材包注入 + 缓存键维度。

覆盖（roadmap 批次 P1a 验收：「builder 注入（绑定 vs 未绑定）」+「缓存键维度
（换 pack version 必 miss）」）：
1. 未绑定 → **零注入**（payload 无 ``genre_pack`` / ``_genre_pack_consumed`` 键，
   既有装配行为不变）；
2. 绑定 → ``genre_pack`` 段含结构模板摘要 + 爽点类型清单（密度约束文本化）+ pacing 摘要，
   并带 ``_genre_pack_consumed`` 溯源审计；
3. 缓存键含题材包指纹 ``<pack_id>@<version>``：换 pack / 换 version 必 miss，
   未变必命中（判定真装配用 spy 统计 ``_build_director_input_uncached`` 调用次数）；
4. 防御性：payload_json 非法 / consumer 未实现 / 爽点条数超上限。

注（P1b 2026-09-13 更新）：scene_planner / writer 两个 consumer 已在 P1b 落地，
本文件保留的 consumer 断言已收窄到「未实现 consumer 零注入」；P1b 消费面用例见
``test_genre_pack_consumption_p1b.py``。

突变验证：把 ``director_input`` 里的 ``payload["genre_pack"] = …`` 一段撤除后，
用例 2/3/4 必红（记录见 CHANGELOG/交接；本文件不断言实现细节以外的中间态）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine.builders import _cache_reset, build_director_input
from packages.core.db import apply_migrations, get_connection
from packages.core.genre import GenrePackCreate, GenrePackService, GenrePackUpdate
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "description": "对手欺压后当场反制",
            "applicable": "冲突升级段",
            "density_cap": "每卷 2~3 次",
            "min_interval_chapters": 3,
            "fatigue_risk": "同型连打不加码则贬值",
            "verify_hint": "对手先施压后当众失势",
        },
        {"type_id": "contrast", "name": "反差", "strength": "s", "density_cap": "每章 ≤1 次"},
    ],
    "structure_templates": {
        "structure_model": "单元剧：1 单元 = 1 卷",
        "full_book_skeleton": {"opening_arc": "第 1 卷展示流程与行事风格"},
        "arc_beat_template": [
            {"beat": "穿入", "chapters": "1~2", "content": "身份+处境", "must": "300 字内进冲突"},
            {"beat": "结算", "chapters": "17~18", "content": "任务结算"},
        ],
        "volume_count_expectation": {"min": 5, "typical": 6, "max": 10, "note": "长篇导向"},
    },
    "pacing": {
        "chapter_words": {"min": 2000, "target": 2500, "max": 3000},
        "density_rules": ["每章 ≥1 个小爽点"],
        "redlines": ["单元内「穿入→任务」不得超过 2 章"],
    },
}


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
            "VALUES (?, ?, ?, 'C', '{\"chapter_goal\": \"x\"}', 'PLANNED', 'VISIBLE', "
            "NULL, ?, ?)",
            (cid, pid, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（返回可变计数盒）。"""
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _create_pack(
    db_path: Path, pack_id: str = "gp_kc", payload: dict | None = None, version: int = 1,
) -> None:
    """建包（payload 走 service 校验；非法 payload 用 raw SQL 直插的用例见专用测试）。"""
    svc = GenrePackService(db_path)
    svc.create(
        GenrePackCreate(
            name="男主快穿", genre_tag="快穿", payload=_PAYLOAD if payload is None else payload,
            pack_id=pack_id,
        )
    )
    for _ in range(version - 1):
        svc.update(pack_id, GenrePackUpdate(payload=payload or _PAYLOAD))


# ---------------------------------------------------------------------------
# 1. 零注入
# ---------------------------------------------------------------------------


def test_director_payload_has_no_genre_pack_when_unbound(tmp_path: Path):
    """未绑定题材包 → payload 无 genre_pack / _genre_pack_consumed 键（既有行为不变）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")

    assert "genre_pack" not in payload
    assert "_genre_pack_consumed" not in payload


def test_director_payload_has_no_genre_pack_after_unbind(tmp_path: Path):
    """解绑后回到零注入（绑定 → 解绑 → 不再注入）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_kc")
    _cache_reset()
    assert "genre_pack" in build_director_input(db_path, pid, cid, "意图")

    svc.unbind(pid)
    _cache_reset()
    payload = build_director_input(db_path, pid, cid, "意图")
    assert "genre_pack" not in payload
    assert "_genre_pack_consumed" not in payload


# ---------------------------------------------------------------------------
# 2. 注入内容（结构模板摘要 + 爽点清单 + pacing 摘要 + 溯源）
# ---------------------------------------------------------------------------


def test_director_payload_injects_structure_payoffs_pacing(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    section = payload["genre_pack"]

    assert section["pack_id"] == "gp_kc"
    assert section["version"] == 1
    assert section["genre_tag"] == "快穿"

    # 结构模板摘要
    structure = section["structure_templates"]
    assert structure["structure_model"] == "单元剧：1 单元 = 1 卷"
    assert structure["full_book_skeleton"] == {"opening_arc": "第 1 卷展示流程与行事风格"}
    assert [b["beat"] for b in structure["arc_beat_template"]] == ["穿入", "结算"]
    assert structure["volume_count_expectation"]["typical"] == 6

    # 爽点类型清单（密度约束文本化）
    payoffs = section["payoff_types"]
    assert [p["type_id"] for p in payoffs] == ["face_slap", "contrast"]
    assert payoffs[0]["density_constraint"] == "密度上限 每卷 2~3 次；同型最小间隔 3 章"
    assert payoffs[0]["fatigue_risk"] == "同型连打不加码则贬值"
    assert payoffs[1]["density_constraint"] == "密度上限 每章 ≤1 次"

    # pacing 摘要
    assert section["pacing"]["chapter_words"]["target"] == 2500
    assert section["pacing"]["redlines"] == ["单元内「穿入→任务」不得超过 2 章"]

    # 溯源审计（与 _reference_canon_consumed 同款口径）
    audit = payload["_genre_pack_consumed"]
    assert audit["pack_id"] == "gp_kc"
    assert audit["version"] == 1
    assert audit["consumed_fields"] == ["structure_templates", "payoff_types", "pacing"]


def test_director_injection_and_canon_coexist(tmp_path: Path):
    """双 slot：canon 注入段与题材包注入段同时存在，互不覆盖。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO reference_canons (canon_id, project_id, title, reader_profile, "
            "canon_json, report_md, status, created_at) VALUES ('can_x', ?, '参照书', "
            "'male_fantasy', '{\"logline\": \"草根逆袭\"}', '', 'active', ?)",
            (pid, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    _cache_reset()
    payload = build_director_input(db_path, pid, cid, "意图")
    assert payload["reference_canon"]["logline"] == "草根逆袭"
    assert payload["genre_pack"]["pack_id"] == "gp_kc"
    assert "_reference_canon_consumed" in payload
    assert "_genre_pack_consumed" in payload


def test_director_payoff_types_capped(tmp_path: Path):
    """爽点条数超上限（40）→ 截断并打标记（防超大 payload 撑爆注入）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    payload = {
        "schema_version": "genre-pack.v1.0.0",
        "payoff_types": [
            {"type_id": f"type_{i:03d}", "name": f"类型{i}"} for i in range(45)
        ],
    }
    _create_pack(db_path, payload=payload)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    section = build_director_input(db_path, pid, cid, "意图")["genre_pack"]
    assert len(section["payoff_types"]) == 40
    assert section["__payoff_types_truncated__"] is True


def test_director_pacing_budget_truncation(tmp_path: Path):
    """pacing 段超字符预算 → 按优先级保留高优先键并打标记。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    payload = {
        "schema_version": "genre-pack.v1.0.0",
        "pacing": {
            "chapter_words": {"target": 2500},
            "redlines": ["红" * 200] * 20,
        },
    }
    _create_pack(db_path, payload=payload)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    section = build_director_input(db_path, pid, cid, "意图")["genre_pack"]
    assert section["pacing"]["chapter_words"]["target"] == 2500
    assert "redlines" not in section["pacing"], "超预算的低优先键应被裁掉"
    assert section["__pacing_truncated__"] is True


def test_director_skips_broken_payload_json(tmp_path: Path):
    """payload_json 非法（直插脏数据）→ 零注入且不抛错（装配路径容错）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_broken', '坏包', 't', 1, "
            "'{not json', NULL, ?, ?)",
            (now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_broken' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    assert "genre_pack" not in payload
    assert "_genre_pack_consumed" not in payload


def test_genre_pack_excerpt_unknown_consumer_is_noop(tmp_path: Path):
    """未实现的 consumer（observer / 未知值）不注入（P1b 已实现 writer / scene_planner）。"""
    from packages.core.context_engine.builders_common import _genre_pack_excerpt

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    conn = get_connection(db_path)
    try:
        for consumer in ("observer", "critic", ""):
            assert _genre_pack_excerpt(conn, pid, consumer=consumer) == (None, None)
        # P1b：scene_planner 消费 payoff_types（本 payload 未声明 ratio_declarations）
        planner_inject, planner_audit = _genre_pack_excerpt(
            conn, pid, consumer="scene_planner",
        )
        assert planner_inject is not None and "payoff_types" in planner_inject
        assert planner_audit["consumed_fields"] == ["payoff_types"]
        # writer 只吃 style_constraints——本 payload 未声明 → 仍是零注入
        assert _genre_pack_excerpt(conn, pid, consumer="writer") == (None, None)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. 缓存键维度（题材包指纹）
# ---------------------------------------------------------------------------


def test_director_cache_hit_when_pack_unchanged(tmp_path: Path, monkeypatch):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import director_input as di_mod

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")
    first = build_director_input(db_path, pid, cid, "意图")
    second = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, "同参数 + 同题材包版本 → 命中缓存，不重装"
    assert first["genre_pack"] == second["genre_pack"]


def test_director_cache_miss_after_pack_version_bump(tmp_path: Path, monkeypatch):
    """换 pack version（PUT 改 payload）必 miss——漏该键维度会读回旧题材包内容。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import director_input as di_mod

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")
    v1 = build_director_input(db_path, pid, cid, "意图")
    assert v1["genre_pack"]["version"] == 1

    svc.update(
        "gp_kc",
        GenrePackUpdate(payload={**_PAYLOAD, "pacing": {"chapter_words": {"target": 2400}}}),
    )
    v2 = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "换 pack version 必须 miss（重新装配）"
    assert v2["genre_pack"]["version"] == 2
    assert v2["genre_pack"]["pacing"]["chapter_words"]["target"] == 2400

    v2_again = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "同版本再取应命中"
    assert v2_again["genre_pack"]["version"] == 2


def test_director_cache_miss_on_bind_and_switch(tmp_path: Path, monkeypatch):
    """未绑定 → 绑定、pack A → pack B 都必 miss（题材包维度进键）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path, pack_id="gp_a")
    _create_pack(db_path, pack_id="gp_b")
    _cache_reset()

    from packages.core.context_engine import director_input as di_mod

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")
    unbound = build_director_input(db_path, pid, cid, "意图")
    assert "genre_pack" not in unbound

    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_a")
    bound_a = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "首次绑定必须 miss"
    assert bound_a["genre_pack"]["pack_id"] == "gp_a"

    svc.bind(pid, "gp_b")
    bound_b = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 3, "换 pack 必须 miss"
    assert bound_b["genre_pack"]["pack_id"] == "gp_b"

    # 回到 pack A：命中各自条目（键含 pack 指纹 → 两包条目并存）
    svc.bind(pid, "gp_a")
    back_a = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 3, "回到旧 pack 应命中其条目"
    assert back_a["genre_pack"]["pack_id"] == "gp_a"


def test_director_cache_miss_on_unbind(tmp_path: Path, monkeypatch):
    """解绑 → 回到零注入条目（不得命中绑定态缓存）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    svc = GenrePackService(db_path)
    svc.bind(pid, "gp_kc")
    _cache_reset()

    from packages.core.context_engine import director_input as di_mod

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")
    assert "genre_pack" in build_director_input(db_path, pid, cid, "意图")
    svc.unbind(pid)
    unbound = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "解绑必须 miss"
    assert "genre_pack" not in unbound


# ---------------------------------------------------------------------------
# 5. 兼容 / 边界
# ---------------------------------------------------------------------------


def test_director_input_on_pre_0025_database(tmp_path: Path):
    """0025 未跑的旧库：主 peek SQL 抛 OperationalError → 降级路径吞掉，零注入不炸。

    覆盖「SQLite ADD COLUMN + REFERENCES 只在新库可用」的兼容性前提：旧库升级前
    装配路径必须与无题材包时逐字段一致。
    """
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
    payload = build_director_input(db_path, pid, cid, "意图")
    assert "genre_pack" not in payload
    assert "_genre_pack_consumed" not in payload


def test_writer_input_only_consumes_style_section(tmp_path: Path):
    """writer 消费面 = 题材包 ``style_constraints`` 段（P1b）。

    本用例的题材包未声明该段 → writer 装配与 P1a 时期逐字段一致（无 ``genre_pack`` /
    ``_genre_pack_consumed`` / ``style_constraints.genre_style``）。
    声明该段的注入 / 合并 / 缓存键用例见 ``test_genre_pack_consumption_p1b.py``。
    """
    from packages.core.context_engine.builders import build_writer_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_pack(db_path)
    GenrePackService(db_path).bind(pid, "gp_kc")
    _cache_reset()

    writer_payload = build_writer_input(db_path, cid, {"purpose": "开场", "scenes": []})
    assert "genre_pack" not in writer_payload
    assert "_genre_pack_consumed" not in writer_payload
    assert "genre_style" not in writer_payload["style_constraints"]
