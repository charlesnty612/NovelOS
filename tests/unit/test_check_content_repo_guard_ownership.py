"""凭据收敛巡检的**归属闸门 + 可用键口径**回归（2026-10-23 Lead 复现的 D-18 修法）。

背景
----
Lead 亲自复现的 defect：``--prune-dangling`` 的归属闸门拿**已知集合**（档案 id ∪
连接 id ∪ provider ∪ env_name）当「这个库属于这个 secrets 文件」的证据 ⇒
**别名型键**（provider 级 / env 级键）能骗过闸门 ⇒ 错库时一次 prune 把真凭据
整条删掉、退出码仍是 0（报「合规」）。

修法（Lead 已拍板）：
- **A** 拆两个集合：``known_keys``（悬空判据）vs ``entity_keys``（**归属判据**，
  = profile_id ∪ connection_id ⇒ 携带项目身份）；
- **B** 归属闸门统一（含「已知集合为空」），``--force-all-dangling`` = **归属确认**；
- **C** ``--migrate`` 也要过归属闸门（防跨项目凭据混入）；
- **D** 可用键口径与生产一致（``isinstance(k, str) and isinstance(v, str) and v``），
  值畸形的条目**可见但绝不被删**；
- **E** 动作结果并进结论段（不得自相矛盾）；
- **R1** 归属提示必须由**动作层**发出（sticky）：动作后会重新采集 ⇒ 重新采集的
  结果**不能**代表「动作发生时」的口径。

⚠️ **为什么另起一个文件**：``tests/unit/test_check_content_repo_guard.py`` 已有
1477 行 ⇒ 再加本矩阵会**越过 1500 行硬规则**（AGENTS.md §二 硬规则 1）⇒ 本文件
承载**新增**用例，既有文件只做**必要调整**（见该文件内的注释）。
⚠️ 本文件**零真实凭据**：全部用仓库外的 ``tmp_path`` + **合成库** + **合成 secrets**。
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_content_repo_guard.py"

#: 归属未验证时的醒目提示（**必须**出现在文本与 ``--json`` 里）。
OWNERSHIP_WARNING = "归属未经自动验证"


@pytest.fixture()
def mod():
    """导入被测脚本（``scripts`` 是命名空间包，仓库根在 ``pythonpath`` 上）。"""
    return importlib.import_module("scripts.check_content_repo_guard")


def _seed_db(
    db_path: Path,
    *,
    profiles: list[tuple[str, str, str | None]] = (),
    connections: list[tuple[str, str, str | None]] = (),
) -> None:
    """种一张极简 ``model_profiles`` / ``model_connections`` 表（**合成夹具**）。

    ``profiles`` 元素 = ``(profile_id, provider, api_key | None)``；
    ``connections`` 元素 = ``(connection_id, provider, api_key | None)``。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE model_profiles ("
            "profile_id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
            "params_json TEXT NOT NULL DEFAULT '{}', connection_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE model_connections ("
            "connection_id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
            "params_json TEXT NOT NULL DEFAULT '{}')"
        )
        for pid, provider, key in profiles:
            params = {"model": "m"} if key is None else {"model": "m", "api_key": key}
            conn.execute(
                "INSERT INTO model_profiles(profile_id, provider, params_json) VALUES (?,?,?)",
                (pid, provider, json.dumps(params, ensure_ascii=False)),
            )
        for cid, provider, key in connections:
            params = {"model": "m"} if key is None else {"model": "m", "api_key": key}
            conn.execute(
                "INSERT INTO model_connections(connection_id, provider, params_json)"
                " VALUES (?,?,?)",
                (cid, provider, json.dumps(params, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()


def _write_secrets(path: Path, api_keys: dict | None) -> Path:
    """写一个合成 ``secrets.json``（``api_keys=None`` ⇒ 文件里**没有** api_keys 段）。"""
    payload = {} if api_keys is None else {"api_keys": api_keys}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _db_plaintext(db_path: Path, table: str, id_col: str, row_id: str) -> str | None:
    """读回库内 ``params_json.api_key``（``None`` ⇒ 已被清掉）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT params_json FROM {table} WHERE {id_col} = ?", (row_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0]).get("api_key")


# ---------------------------------------------------------------------------
# S1~S16 行为矩阵（Lead 已按此矩阵亲自复现过）
# ---------------------------------------------------------------------------

#: 每行 = (场景名, 库, secrets 的 api_keys, CLI 参数, 期望退出码, 期望落盘后的 api_keys)
#: ⚠️ 「期望落盘后的 api_keys」= ``None`` ⇒ 文件**逐字节不变**（用原始字节比对）。
MATRIX = [
    pytest.param(
        "S1_正确库_实体键_无悬空",
        {"profiles": [("mprof_real", "minimax", None)]},
        {"mprof_real": "sk"},
        ["--prune-dangling", "--apply"],
        0,
        {"mprof_real": "sk"},
        id="S1_correct_db_entity_key",
    ),
    pytest.param(
        "S2_正确库_1活1悬空",
        {"profiles": [("mprof_real", "minimax", None)]},
        {"mprof_real": "sk", "mprof_dead": "sk2"},
        ["--prune-dangling", "--apply"],
        0,
        {"mprof_real": "sk"},
        id="S2_correct_db_one_dangling",
    ),
    pytest.param(
        "S3_错库_纯实体键_拒绝",
        {"profiles": [("mprof_other", "minimax", None)]},
        {"mprof_real": "sk"},
        ["--prune-dangling", "--apply"],
        2,
        None,
        id="S3_wrong_db_entity_key_refused",
    ),
    pytest.param(
        "S4_错库_含provider级别名键_拒绝",
        {"profiles": [("mprof_other", "openai_compatible", None)]},
        {"mprof_real": "sk-REAL", "openai_compatible": "sk-SHARED"},
        ["--prune-dangling", "--apply"],
        2,
        None,
        id="S4_wrong_db_provider_alias_refused",
    ),
    pytest.param(
        "S5_错库_含env级别名键_拒绝",
        {"profiles": [("mprof_other", "openai_compatible", None)]},
        {"mprof_real": "sk-REAL", "NOVELOS_API_KEY_OPENAI_COMPATIBLE": "sk-E"},
        ["--prune-dangling", "--apply"],
        2,
        None,
        id="S5_wrong_db_env_alias_refused",
    ),
    pytest.param(
        "S6_错库_纯实体键_force放行",
        {"profiles": [("mprof_other", "openai_compatible", None)]},
        {"mprof_real": "sk"},
        ["--prune-dangling", "--apply", "--force-all-dangling"],
        0,
        {},
        id="S6_wrong_db_force_allowed",
    ),
    pytest.param(
        "S7_空库_拒绝",
        {},
        {"mprof_x": "sk"},
        ["--prune-dangling", "--apply"],
        2,
        None,
        id="S7_empty_db_refused",
    ),
    pytest.param(
        "S8_空库_force放行",
        {},
        {"mprof_x": "sk"},
        ["--prune-dangling", "--apply", "--force-all-dangling"],
        0,
        {},
        id="S8_empty_db_force_allowed",
    ),
    pytest.param(
        "S9_正确库_只有别名键_拒绝",
        {"profiles": [("mprof_real", "minimax", None)]},
        {"openai_compatible": "sk-SHARED"},
        ["--prune-dangling", "--apply"],
        2,
        None,
        id="S9_correct_db_alias_only_refused",
    ),
    pytest.param(
        "S10_正确库_别名键+死实体键_force放行",
        {"profiles": [("mprof_real", "openai_compatible", None)]},
        {"openai_compatible": "sk-SHARED", "mprof_dead": "sk2"},
        ["--prune-dangling", "--apply", "--force-all-dangling"],
        0,
        {"openai_compatible": "sk-SHARED"},
        id="S10_alias_plus_dead_entity_force_allowed",
    ),
    pytest.param(
        "S11_值是空串_不合规",
        {"profiles": [("mprof_real", "minimax", None)]},
        {"mprof_x": ""},
        ["--prune-dangling", "--apply"],
        1,
        {"mprof_x": ""},
        id="S11_empty_value_not_compliant",
    ),
    pytest.param(
        "S12_值是数字_不合规",
        {"profiles": [("mprof_real", "minimax", None)]},
        {"mprof_x": 123},
        ["--prune-dangling", "--apply"],
        1,
        {"mprof_x": 123},
        id="S12_numeric_value_not_compliant",
    ),
    pytest.param(
        "S14_首次迁移_归属未验证secrets为空",
        {"profiles": [("mprof_real", "minimax", "sk-plain")]},
        None,
        ["--migrate", "--apply"],
        0,
        {"mprof_real": "sk-plain"},
        id="S14_first_migration_secrets_empty",
    ),
    pytest.param(
        "S15_错库_migrate_拒绝",
        {"profiles": [("mprof_y", "minimax", "sk-plain")]},
        {"unrelated": "v"},
        ["--migrate", "--apply"],
        2,
        None,
        id="S15_wrong_db_migrate_refused",
    ),
    pytest.param(
        "S16_错库_migrate_force放行",
        {"profiles": [("mprof_y", "minimax", "sk-plain")]},
        {"unrelated": "v"},
        ["--migrate", "--apply", "--force-all-dangling"],
        1,  # ⚠️ Lead 订正：迁移后文件里仍有 1 个悬空键（unrelated）⇒ 退出码 1
        {"unrelated": "v", "mprof_y": "sk-plain"},
        id="S16_wrong_db_migrate_force_allowed",
    ),
]


@pytest.mark.parametrize(
    "scenario,db_spec,api_keys,extra_args,expect_exit,expect_api_keys",
    [p.values for p in MATRIX],
    ids=[p.id for p in MATRIX],
)
def test_secrets_ownership_matrix(
    mod,
    tmp_path,
    capsys,
    scenario,
    db_spec,
    api_keys,
    extra_args,
    expect_exit,
    expect_api_keys,
) -> None:
    """S1~S16 行为矩阵：退出码 + **落盘后的 api_keys 逐键比对**（真实值断言）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, **db_spec)
    secrets = _write_secrets(tmp_path / "secrets.json", api_keys)
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets",
            "--db", str(db),
            "--secrets-file", str(secrets),
            *extra_args,
        ]
    )
    capsys.readouterr()

    assert code == expect_exit, f"[{scenario}] 期望退出码 {expect_exit}，实际 {code}"
    if expect_api_keys is None:
        assert secrets.read_bytes() == before, f"[{scenario}] secrets.json 必须**逐字节不变**"
        assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"
    else:
        written = json.loads(secrets.read_text(encoding="utf-8"))
        assert written["api_keys"] == expect_api_keys, f"[{scenario}] 落盘后的 api_keys 必须逐键相等"


def test_s13_api_keys_section_missing_is_legal_empty_state(mod, tmp_path, capsys) -> None:
    """S13：``api_keys`` 段**不存在** ⇒ 合法空状态 ⇒ 退出码 0（反向不变量，不得过修）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", None)
    before = secrets.read_bytes()

    code = mod.main(
        ["secrets", "--db", str(db), "--secrets-file", str(secrets), "--prune-dangling", "--apply"]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    assert "合规" in out
    assert secrets.read_bytes() == before, "合法空状态 ⇒ 文件不得被改写"


@pytest.mark.parametrize("raw", [None, {}, {"api_keys": None}, {"api_keys": {}}])
def test_s13_api_keys_section_empty_shapes_are_legal(mod, tmp_path, capsys, raw) -> None:
    """S13 的四种「空」形态（不存在 / 空对象 / ``null``）⇒ 全部合法 ⇒ 退出码 0。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps(raw if raw is not None else {}), encoding="utf-8")

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 0, f"合法空状态 ⇒ 退出码 0，实际 {code}\n{out}"
    assert "不合规" not in out, "合法空状态**不得**判不合规"
    assert "不可用 0" in out, "合法空状态 ⇒ 不可用条目数必须为 0"
    assert "不可用条目（值不是非空字符串" not in out, "合法空状态**不得**列不可用条目"


# ---------------------------------------------------------------------------
# 归属闸门的**判据**（修法 A）：别名键**不得**当归属证据
# ---------------------------------------------------------------------------


def test_alias_key_cannot_satisfy_ownership_gate(mod, tmp_path, capsys) -> None:
    """**provider 级别名键**不得满足归属闸门（缺陷 1 的直接回归）。

    ⚠️ 判别力：若实现改回拿「已知集合」（含 provider / env_name）当归属证据 ⇒
    本用例**必红**（退出码会变成 0、``mprof_real`` 会被删掉）。
    """
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_other", "openai_compatible", None)])
    secrets = _write_secrets(
        tmp_path / "secrets.json", {"mprof_real": "sk-REAL", "openai_compatible": "sk-SHARED"}
    )
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"别名键不得满足归属闸门 ⇒ 退出码 2，实际 {code}\n{out}"
    assert secrets.read_bytes() == before, "真凭据**一个都不许删**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"


def test_entity_keys_are_profile_and_connection_ids_only(mod, tmp_path) -> None:
    """``entity_keys`` = profile_id ∪ connection_id ⇒ **不含** provider / env_name。"""
    db = tmp_path / "novelos.db"
    _seed_db(
        db,
        profiles=[("mprof_real", "openai_compatible", None)],
        connections=[("mconn_real", "openai_compatible", None)],
    )
    state = mod.collect_secrets_state(db, tmp_path / "secrets.json")

    assert state.entity_keys == {"mprof_real", "mconn_real"}
    assert "openai_compatible" not in state.entity_keys, "provider 名**不是**实体身份"
    assert "novelos_api_key_openai_compatible" not in state.entity_keys, "env 名**不是**实体身份"
    assert "openai_compatible" in state.known_keys, "悬空判据仍须含 provider 形态"


def test_connection_id_is_ownership_evidence(mod, tmp_path, capsys) -> None:
    """**连接 id** 也是实体身份 ⇒ secrets 里有连接级键 ⇒ 归属有据（不得被误拒）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, connections=[("mconn_real", "minimax", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", {"mconn_real": "sk"})

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "归属: 已验证" in out, out


# ---------------------------------------------------------------------------
# R1：归属提示必须由**动作层**发出（sticky）
# ---------------------------------------------------------------------------


def test_r1_force_prompt_survives_prune_deleting_last_key(mod, tmp_path, capsys) -> None:
    """force + prune 删掉**最后一个键** ⇒ 提示**不得**消失（动作层 sticky）。

    ⚠️ 缺陷形状：动作后会**重新采集** ⇒ 重新采集时没有键可比 ⇒ 归属分支不进 ⇒
    提示凭空消失 ⇒ 恰在最该警告的破坏性场景里沉默。
    """
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_other", "openai_compatible", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", {"mprof_real": "sk"})

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply", "--force-all-dangling",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    assert json.loads(secrets.read_text(encoding="utf-8"))["api_keys"] == {}, "键应被删掉"
    assert OWNERSHIP_WARNING in out, f"force 放行后的醒目提示**不得**消失：\n{out}"


def test_r1_force_migrate_must_not_claim_ownership_verified(mod, tmp_path, capsys) -> None:
    """force + migrate ⇒ 输出**不得**出现「归属: 已验证」（写盘时并无归属验证）。

    ⚠️ 缺陷形状：force 迁移把错库的实体 id 写进文件 ⇒ 重新采集时它命中库内实体
    ⇒ 反而显示「归属: 已验证」⇒ **误导**。
    """
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_y", "minimax", "sk-plain")])
    secrets = _write_secrets(tmp_path / "secrets.json", {"unrelated": "v"})

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply", "--force-all-dangling",
        ]
    )
    out = capsys.readouterr().out

    assert code == 1, f"迁移后仍有 1 个悬空键 ⇒ 退出码 1，实际 {code}\n{out}"
    assert "归属: 已验证" not in out, f"写盘时**没有**任何归属验证 ⇒ 不得报「已验证」：\n{out}"
    assert OWNERSHIP_WARNING in out, f"必须醒目提示：\n{out}"
    assert "归属: 未经自动验证" in out, out


def test_r1_force_prompt_is_on_the_action_lines(mod, tmp_path, capsys) -> None:
    """动作行本身也要带归属提示（只看动作行的人也必须知道这次是 force 放行的）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_other", "openai_compatible", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", {"mprof_real": "sk"})

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply", "--force-all-dangling",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    action_lines = [ln for ln in out.splitlines() if ln.strip().startswith("清理悬空条目:")]
    assert action_lines, f"必须看得见动作行：\n{out}"
    assert any(OWNERSHIP_WARNING in ln for ln in action_lines), (
        f"动作行必须带归属提示：{action_lines}"
    )


def test_r1_json_ownership_fields_use_pre_action_state(mod, tmp_path, capsys) -> None:
    """``--json`` 的 ``ownership_verified`` 必须按**动作前**的口径给出。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_y", "minimax", "sk-plain")])
    secrets = _write_secrets(tmp_path / "secrets.json", {"unrelated": "v"})

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply", "--force-all-dangling", "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1, payload
    assert payload["ownership_verified"] is False, "动作前没有归属证据 ⇒ 必须是 false"
    assert payload["force_all_dangling"] is True
    assert OWNERSHIP_WARNING in (payload["ownership_warning"] or ""), payload["ownership_warning"]
    assert any(OWNERSHIP_WARNING in ln for ln in payload["action_results"]), payload["action_results"]


def test_r1_non_force_run_has_no_ownership_warning(mod, tmp_path, capsys) -> None:
    """**没**用 force ⇒ 不得出现归属未验证的提示（否则就是噪声，警告会被忽略）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", {"mprof_real": "sk"})

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    assert OWNERSHIP_WARNING not in out, f"没用 force ⇒ 不得报归属未验证：\n{out}"
    assert "归属: 已验证" in out, out


# ---------------------------------------------------------------------------
# 修法 D：可用键口径（与生产解析函数一致）
# ---------------------------------------------------------------------------


def test_unusable_value_is_reported_but_never_pruned(mod, tmp_path, capsys) -> None:
    """值畸形（空串）⇒ **不合规**（可见）但**绝不**被 prune 删掉。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(tmp_path / "secrets.json", {"mprof_x": ""})
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    out = capsys.readouterr().out

    assert code == 1, f"值畸形 ⇒ 不合规（退出码 1），实际 {code}\n{out}"
    assert "不可用" in out, out
    assert "mprof_x" in out, "不可用条目**必须可见**（键名可打印，值不可打印）"
    assert secrets.read_bytes() == before, "不可用条目**绝不可**被 prune 删掉"


def test_unusable_value_matches_production_filter(mod, tmp_path) -> None:
    """可用键口径必须与**生产解析函数**一致（``secrets_store._read_file_fresh``）。"""
    from packages.core.secrets_store import _read_file_fresh

    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(
        tmp_path / "secrets.json", {"ok": "sk", "empty": "", "num": 123, "null": None}
    )

    state = mod.collect_secrets_state(db, secrets)
    production = _read_file_fresh(secrets)

    assert state.secrets_keys == sorted(production), "可用键口径必须与生产逐键一致"
    assert state.unusable_keys == ["empty", "null", "num"]
    assert state.secrets_entry_count == 4, "条目总数必须含不可用的条目"


def test_unusable_entries_are_counted_and_reported_in_json(mod, tmp_path, capsys) -> None:
    """``--json`` 必须给出 ``secrets_entry_count`` 与 ``unusable_keys``。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    # ⚠️ 可用键必须是**实体 id**（否则归属闸门先拦 ⇒ 退出码 2 而不是 1）
    secrets = _write_secrets(tmp_path / "secrets.json", {"mprof_real": "sk", "empty": ""})

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1, payload
    assert payload["secrets_entry_count"] == 2
    assert payload["secrets_key_count"] == 1, "可用键数（与生产口径一致）"
    assert payload["unusable_keys"] == ["empty"]
    assert any("不可用" in issue for issue in payload["issues"]), payload["issues"]


# ---------------------------------------------------------------------------
# 修法 C：migrate 也要过归属闸门（防跨项目凭据混入）
# ---------------------------------------------------------------------------


def test_migrate_refuses_on_wrong_db_and_keeps_db_plaintext(mod, tmp_path, capsys) -> None:
    """错库 + migrate ⇒ **拒绝执行**（零落盘、零备份、库内明文不清）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_y", "minimax", "sk-plain")])
    secrets = _write_secrets(tmp_path / "secrets.json", {"unrelated": "v"})
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"错库 ⇒ 必须拒绝迁移（退出码 2），实际 {code}\n{out}"
    assert "已中止迁移" in out, out
    assert secrets.read_bytes() == before, "secrets.json 必须**逐字节不变**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"
    assert _db_plaintext(db, "model_profiles", "profile_id", "mprof_y") == "sk-plain", (
        "拒绝执行 ⇒ 库内明文**不许**被清"
    )


def test_migrate_with_entity_evidence_is_allowed_even_with_dangling_key(
    mod, tmp_path, capsys
) -> None:
    """归属**有据**时，悬空条目**不拦**迁移（加法动作不受「悬空」闸门拦截）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", "sk-plain")])
    secrets = _write_secrets(
        tmp_path / "secrets.json", {"mprof_real": "sk-file", "mprof_dead": "sk-dead"}
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code != 2, f"归属有据 ⇒ 迁移不该被拦，实际 {code}\n{out}"
    written = json.loads(secrets.read_text(encoding="utf-8"))["api_keys"]
    assert "mprof_dead" in written, "悬空键**不该**被迁移顺手删掉（迁移不删东西）"
    assert written["mprof_real"] == "sk-file", "文件里的值优先（运行期本来就用它）"


def test_first_migration_notes_ownership_unverified(mod, tmp_path, capsys) -> None:
    """首次迁移（secrets 无 api_keys 段）⇒ 放行 + note 写明「归属未验证：secrets 为空」。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", "sk-plain")])
    secrets = _write_secrets(tmp_path / "secrets.json", None)

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    assert "归属未验证：secrets 为空" in out, f"note 必须写明归属未验证：\n{out}"
    written = json.loads(secrets.read_text(encoding="utf-8"))["api_keys"]
    assert written == {"mprof_real": "sk-plain"}
    assert _db_plaintext(db, "model_profiles", "profile_id", "mprof_real") is None, (
        "库内明文必须被清掉"
    )


# ---------------------------------------------------------------------------
# 修法 E：动作结果必须与结论**同段**（不得自相矛盾）
# ---------------------------------------------------------------------------


def test_action_result_appears_before_the_conclusion(mod, tmp_path, capsys) -> None:
    """动作行必须在**结论之前**（旧实现把「结论: 合规」印在动作之前 ⇒ 误导）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(
        tmp_path / "secrets.json", {"mprof_real": "sk", "mprof_dead": "sk2"}
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0, out
    action_at = out.index("清理悬空条目:")
    conclusion_at = out.index("结论:")
    assert action_at < conclusion_at, "动作结果必须**先于**结论出现：\n" + out
    assert "已执行" in out[action_at:action_at + 40], out


def test_json_conclusion_and_action_results_are_present(mod, tmp_path, capsys) -> None:
    """``--json`` 的结论行与动作结果**同段可见**（机器消费方能看到这次干了什么）。"""
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_real", "minimax", None)])
    secrets = _write_secrets(
        tmp_path / "secrets.json", {"mprof_real": "sk", "mprof_dead": "sk2"}
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply", "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0, payload
    assert payload["conclusion"].startswith("结论: 合规"), payload["conclusion"]
    assert any("清理悬空条目" in line for line in payload["action_results"]), payload
    assert payload["prune"]["removed"] == ["mprof_dead"], payload["prune"]


def test_migrate_is_blocked_by_missing_ownership_evidence(mod, tmp_path, capsys) -> None:
    """``--migrate`` **要**过归属闸门（**行为变更：修法 C**，2026-10-23）。

    ⚠️ **旧名** ``test_migrate_is_not_blocked_by_disjoint_known_set`` 断言「迁移不受
    闸门拦截」——那条口径已被**修法 C**取代：迁移会把**库内明文**搬进 secrets.json
    ⇒ 若 ``--db`` 指到别的库 ⇒ **跨项目凭据混入** ⇒ 迁移必须有归属证据
    （``api_keys`` 段非空 ⇒ 要求 secrets 的键 ∩ **实体身份** ≠ ∅）。
    ⇒ 现在：**拒绝执行**（退出码 2）、**零落盘**、库内明文**不清**。
    ⚠️ 「悬空」闸门**仍然**不拦迁移（那是另一个判断）——归属有据时迁移照常放行，
    见 ``tests/unit/test_check_content_repo_guard_ownership.py``。
    """
    db = tmp_path / "novelos.db"
    _seed_db(db, profiles=[("mprof_live01", "minimax", "sk-db-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {"sk-handwritten": "sk-x"}}), encoding="utf-8")
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"无归属证据 ⇒ 必须拒绝迁移（退出码 2），实际 {code}\n{out}"
    assert "已中止迁移" in out, f"必须**明确拒绝**：{out}"
    assert secrets.read_bytes() == before, "secrets.json 必须**逐字节不变**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"

    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "api_key" in params, "拒绝执行 ⇒ 库内明文**不许**被清"
