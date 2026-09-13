"""题材库 P1a：GenrePackService（CRUD / payload 校验 / 项目绑定）单元测试。

覆盖（roadmap 批次 P1a 验收）：
1. payload jsonschema 校验：缺席字段宽容、类型 / 枚举 / 模式错误必拒；
2. CRUD：create（显式 pack_id / 自动 gp_ 前缀）/ list（含 ?genre_tag 过滤）/ get /
   update（提供 payload → version 自增）/ delete；
3. 绑定：bind（单 slot 覆盖）/ unbind（幂等）/ get_project_binding / count_bindings；
4. 隔离：题材包表与 reference_canons 互不影响（生命周期独立）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.genre import (
    GenrePackCreate,
    GenrePackService,
    GenrePackUpdate,
    validate_payload,
)
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 最小合法 payload（schema_version 锚点 + 一格爽点 + 结构模板 + pacing）。
_VALID_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "min_interval_chapters": 3,
            "fatigue_risk": "同型连打不加码则贬值",
            "verify_hint": "对手先施压后当众失势",
            "mapped_tropes": ["退婚反杀"],
            "source": "curated",
        }
    ],
    "structure_templates": {
        "structure_model": "单元剧：1 单元 = 1 卷",
        "arc_beat_template": [{"beat": "穿入", "chapters": "1~2", "content": "身份+处境"}],
    },
    "pacing": {"chapter_words": {"min": 2000, "target": 2500, "max": 3000}},
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


def _svc(tmp_path: Path) -> tuple[GenrePackService, Path]:
    db_path = _fresh_db(tmp_path)
    return GenrePackService(db_path), db_path


# ---------------------------------------------------------------------------
# 1. payload schema 校验
# ---------------------------------------------------------------------------


def test_validate_payload_accepts_minimal_and_full():
    """最小（仅版本锚点）与完整 payload 都通过——缺席字段语义宽容。"""
    assert validate_payload({"schema_version": "genre-pack.v1.0.0"}) == []
    assert validate_payload(_VALID_PAYLOAD) == []


def test_validate_payload_requires_schema_version():
    """缺版本锚点 → 拒绝（版本线是 payload 的身份锚）。"""
    errs = validate_payload({"payoff_types": []})
    assert errs and "schema_version" in errs[0]


def test_validate_payload_rejects_wrong_version_line():
    errs = validate_payload({"schema_version": "genre-pack.v2.0.0"})
    assert errs, "跨主版本 payload 必须被 v1 schema 拒绝"
    assert "schema_version" in errs[0]


def test_validate_payload_rejects_wrong_types():
    """各段类型错误（数组 / 对象 / 数字）逐项拒绝。"""
    base = {"schema_version": "genre-pack.v1.0.0"}
    assert validate_payload({**base, "payoff_types": "打脸"})
    assert validate_payload({**base, "structure_templates": []})
    assert validate_payload({**base, "pacing": "2500 字/章"})
    assert validate_payload({**base, "ratio_declarations": {"face_slap": 1.5}})
    assert validate_payload(
        {**base, "style_constraints": {"forbidden_words": "仿佛"}}
    )


def test_validate_payload_rejects_payoff_field_errors():
    """爽点条目字段级错误：缺必填 / 枚举外 / 类型错 / 模式错。"""
    base = {"schema_version": "genre-pack.v1.0.0"}
    assert validate_payload({**base, "payoff_types": [{"name": "打脸"}]})  # 缺 type_id
    assert validate_payload({**base, "payoff_types": [{"type_id": "face_slap"}]})  # 缺 name
    assert validate_payload(
        {**base, "payoff_types": [{"type_id": "face_slap", "name": "打脸", "strength": "XL"}]}
    )
    assert validate_payload(
        {
            **base,
            "payoff_types": [
                {"type_id": "face_slap", "name": "打脸", "min_interval_chapters": "三"}
            ],
        }
    )
    assert validate_payload(
        {**base, "payoff_types": [{"type_id": "FaceSlap", "name": "打脸"}]}
    )  # 非 snake_case


def test_validate_payload_rejects_unknown_top_level_key():
    """顶层键白名单：题材包 payload 只承载消费子集（其余维度留在内容仓）。"""
    errs = validate_payload({"schema_version": "genre-pack.v1.0.0", "platform_rules": {}})
    assert errs and "Additional properties" in errs[0]


def test_validate_payload_rejects_non_dict():
    assert validate_payload(["not", "a", "dict"])
    assert validate_payload(None)
    assert validate_payload("{}")


# ---------------------------------------------------------------------------
# 2. CRUD
# ---------------------------------------------------------------------------


def test_create_pack_persists_row_with_explicit_pack_id(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    pack = svc.create(
        GenrePackCreate(
            name="男主快穿", genre_tag="快穿", payload=_VALID_PAYLOAD,
            pack_id="genre-male-quicktrans-v1", source_path="genres/male-quicktrans",
        )
    )
    assert pack["pack_id"] == "genre-male-quicktrans-v1"
    assert pack["version"] == 1
    assert pack["source_path"] == "genres/male-quicktrans"
    assert pack["payload"]["payoff_types"][0]["type_id"] == "face_slap"
    assert pack["created_at"] and pack["updated_at"]


def test_create_pack_without_pack_id_generates_gp_prefix(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    pack = svc.create(
        GenrePackCreate(name="临时包", genre_tag="测试", payload={"schema_version": "genre-pack.v1.0.0"})
    )
    assert pack["pack_id"].startswith("gp_"), pack["pack_id"]


def test_create_pack_duplicate_pack_id_raises_integrity_error(tmp_path: Path):
    import sqlite3

    svc, _ = _svc(tmp_path)
    svc.create(GenrePackCreate(name="A", genre_tag="t", payload={}, pack_id="gp_dup"))
    with pytest.raises(sqlite3.IntegrityError):
        svc.create(GenrePackCreate(name="B", genre_tag="t", payload={}, pack_id="gp_dup"))


def test_list_packs_summary_and_genre_tag_filter(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.create(
        GenrePackCreate(name="快穿", genre_tag="快穿", payload=_VALID_PAYLOAD, pack_id="gp_kc")
    )
    svc.create(GenrePackCreate(name="都市", genre_tag="都市", payload={}, pack_id="gp_ds"))

    all_packs = svc.list_packs()
    assert {p["pack_id"] for p in all_packs} == {"gp_kc", "gp_ds"}
    assert len(svc.list_packs(genre_tag="快穿")) == 1

    kc = next(p for p in all_packs if p["pack_id"] == "gp_kc")
    # 摘要派生字段（列表端点不返回 payload 全文）
    assert "payload" not in kc
    assert kc["payoff_type_count"] == 1
    assert kc["structure_model"] == "单元剧：1 单元 = 1 卷"
    assert kc["chapter_words_target"] == 2500
    assert kc["bound_project_count"] == 0


def test_get_pack_returns_none_for_unknown(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    assert svc.get("gp_missing") is None


def test_update_bumps_version_when_payload_provided(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.create(
        GenrePackCreate(name="快穿", genre_tag="快穿", payload=_VALID_PAYLOAD, pack_id="gp_kc")
    )
    updated = svc.update(
        "gp_kc",
        GenrePackUpdate(payload={**_VALID_PAYLOAD, "pacing": {"chapter_words": {"target": 2400}}}),
    )
    assert updated is not None
    assert updated["version"] == 2, "提供 payload → version 自增（装配缓存键指纹跟随）"
    assert updated["payload"]["pacing"]["chapter_words"]["target"] == 2400


def test_update_without_payload_keeps_version_and_payload(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.create(
        GenrePackCreate(name="快穿", genre_tag="快穿", payload=_VALID_PAYLOAD, pack_id="gp_kc")
    )
    updated = svc.update("gp_kc", GenrePackUpdate(name="男主快穿"))
    assert updated is not None
    assert updated["name"] == "男主快穿"
    assert updated["version"] == 1
    assert updated["payload"]["payoff_types"][0]["type_id"] == "face_slap"


def test_update_unknown_pack_returns_none(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    assert svc.update("gp_missing", GenrePackUpdate(name="x")) is None


def test_delete_unbound_pack_removes_row(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.create(GenrePackCreate(name="快穿", genre_tag="快穿", payload={}, pack_id="gp_kc"))
    assert svc.delete("gp_kc") is True
    assert svc.get("gp_kc") is None
    assert svc.delete("gp_kc") is False, "已删除的 pack 再删返回 False（router 转 404）"


def test_delete_bound_pack_is_blocked_by_count_bindings(tmp_path: Path):
    """绑定检查口径：count_bindings > 0 → router 409，pack 不得被删（不静默解绑）。"""
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    svc.create(GenrePackCreate(name="快穿", genre_tag="快穿", payload={}, pack_id="gp_kc"))
    assert svc.count_bindings("gp_kc") == 0
    svc.bind(pid, "gp_kc")
    assert svc.count_bindings("gp_kc") == 1
    assert svc.get("gp_kc") is not None


# ---------------------------------------------------------------------------
# 3. 绑定 / 解绑
# ---------------------------------------------------------------------------


def test_bind_sets_project_genre_pack_id(tmp_path: Path):
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    svc.create(GenrePackCreate(name="快穿", genre_tag="快穿", payload=_VALID_PAYLOAD, pack_id="gp_kc"))

    code, binding = svc.bind(pid, "gp_kc")
    assert code == "ok", code
    assert binding is not None
    assert binding["bound"] is True
    assert binding["pack_id"] == "gp_kc"
    assert binding["pack"]["payload"]["schema_version"] == "genre-pack.v1.0.0"

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT genre_pack_id FROM projects WHERE project_id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert row["genre_pack_id"] == "gp_kc"


def test_bind_replaces_existing_binding_single_slot(tmp_path: Path):
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    svc.create(GenrePackCreate(name="A", genre_tag="t", payload={}, pack_id="gp_a"))
    svc.create(GenrePackCreate(name="B", genre_tag="t", payload={}, pack_id="gp_b"))
    svc.bind(pid, "gp_a")
    code, binding = svc.bind(pid, "gp_b")
    assert code == "ok"
    assert binding is not None and binding["pack_id"] == "gp_b"
    assert svc.count_bindings("gp_a") == 0
    assert svc.count_bindings("gp_b") == 1


def test_bind_unknown_pack_and_unknown_project(tmp_path: Path):
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    svc.create(GenrePackCreate(name="A", genre_tag="t", payload={}, pack_id="gp_a"))
    assert svc.bind(pid, "gp_missing")[0] == "pack_not_found"
    assert svc.bind("prj_missing", "gp_a")[0] == "project_not_found"


def test_unbind_clears_binding_and_second_unbind_is_not_bound(tmp_path: Path):
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    svc.create(GenrePackCreate(name="A", genre_tag="t", payload={}, pack_id="gp_a"))
    svc.bind(pid, "gp_a")

    code, _ = svc.unbind(pid)
    assert code == "ok"
    assert svc.get_project_binding(pid)["bound"] is False
    assert svc.unbind(pid)[0] == "not_bound"
    assert svc.unbind("prj_missing")[0] == "project_not_found"


def test_get_project_binding_unbound_and_unknown_project(tmp_path: Path):
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    binding = svc.get_project_binding(pid)
    assert binding == {"project_id": pid, "pack_id": None, "bound": False, "pack": None}
    assert svc.get_project_binding("prj_missing") is None


# ---------------------------------------------------------------------------
# 4. 独立性（与 reference_canons 生命周期互不干扰）
# ---------------------------------------------------------------------------


def test_genre_pack_binding_does_not_touch_reference_canons(tmp_path: Path):
    """双 slot：题材包绑定不写 canon 表；canon 存在也不影响题材包读取。"""
    svc, db_path = _svc(tmp_path)
    pid = _insert_project(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO reference_canons (canon_id, project_id, title, reader_profile, "
            "canon_json, report_md, status, created_at) VALUES ('can_x', ?, '参照书', "
            "'male_fantasy', '{}', '', 'active', ?)",
            (pid, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    svc.create(GenrePackCreate(name="A", genre_tag="t", payload={}, pack_id="gp_a"))
    svc.bind(pid, "gp_a")

    conn = get_connection(db_path)
    try:
        canons = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_canons WHERE project_id = ?", (pid,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert canons == 1, "题材包链路不得增删 canon 行"
    assert svc.get_project_binding(pid)["pack_id"] == "gp_a"
