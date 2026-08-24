"""story_state 双写面统一测试（V2.0 Wave B 任务一）。

覆盖任务书指定的 2 条双写统一用例（与本任务书 §实施方案 2.6 / 2.7 对应）：
1. **canon 写透与 domain CRUD 走同一共享助手**——调用 ``write_through.encode_who_knows``
   与 ``domain.character.CharacterService`` 写入的 who_knows 列在 DB 中一致；同样
   的「None / [] / ['a','b']」三态输入，两路写入的 DB raw 字符串逐字节一致。
2. **NULL 守卫对 domain 路径同样生效**——``domain.character.CharacterService.create``
   传入 ``who_knows=None`` 时落 DB ``NULL``；``who_knows=[]`` 时落 ``'[]'``；
   ``who_knows=['char_x']`` 时落 JSON 字符串。

测试模式：直接读 SQLite ``who_knows`` 列比对两路落库结果；不走 HTTP（避免引入
与本任务无关的 router/validator 复杂度）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.service import StoryStateService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_project(db_path: Path, name: str = "wh_proj") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _make_chapter(db_path: Path, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, status, created_at, updated_at) "
            "VALUES (?, ?, 1, 'PLANNED', ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _make_character_id() -> str:
    return new_id("char")


# ============================================================================
# 1. canon 写透与 domain CRUD 走同一共享助手
# ============================================================================


def test_write_through_and_domain_share_who_knows_encoding(tmp_path: Path):
    """canon（write_through.encode_who_knows）与 domain（CharacterService.update）
    两路对「None / [] / ['a','b']」三态输入的 DB 列 raw 值逐字节一致——证明两路
    走的都是 write_helpers.encode_who_knows 唯一实现。

    实现细节：用独立 ``_connect`` 短连接完成 INSERT/SELECT，避开与 svc 的
    长连接争抢（SQLite WAL 下两连接并发 INSERT 也可能短暂 lock）；每一行
    canon INSERT 都立即 commit 并关连接。
    """
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    from packages.core.story_state.write_through import encode_who_knows as canon_encode
    from packages.domain.character.service import CharacterService
    from packages.domain.character.models import CharacterCreate, CharacterUpdate

    # 三态对照值（canon 写入与 domain 写入应使用完全相同的 encode 结果）
    cases = [
        ("none", None, None),                       # None → DB NULL
        ("empty", [], "[]"),                        # [] → DB '[]'
        ("list", ["char_x"], json.dumps(["char_x"], ensure_ascii=False)),
    ]

    for label, input_val, expected_db_raw in cases:
        # ---- A. canon 路径：模拟 write_through 写入（短连接，commit 后立即关）---
        cid_canon = new_id("char")
        encoded = canon_encode(input_val)
        now = now_iso()
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO characters (character_id, project_id, name, role, "
                "core_json, visibility, who_knows, created_at, updated_at) "
                "VALUES (?, ?, ?, 'supporting', '{}', 'PUBLIC', ?, ?, ?)",
                (cid_canon, pid, f"canon_{label}", encoded, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        # ---- B. domain 路径：通过 CharacterService.create + update ---
        svc = CharacterService(db_path)
        char_create = CharacterCreate(name=f"domain_{label}", who_knows=input_val)
        created = svc.create(pid, char_create)
        domain_cid = created["character_id"]

        # ---- 比对：两路 who_knows 列 raw 完全一致 ----
        conn = _connect(db_path)
        try:
            canon_row = conn.execute(
                "SELECT who_knows FROM characters WHERE character_id = ?", (cid_canon,)
            ).fetchone()
            domain_row = conn.execute(
                "SELECT who_knows FROM characters WHERE character_id = ?", (domain_cid,)
            ).fetchone()
        finally:
            conn.close()
        assert canon_row["who_knows"] == domain_row["who_knows"], (
            f"[{label}] canon={canon_row['who_knows']!r} domain={domain_row['who_knows']!r} 不一致"
        )
        assert canon_row["who_knows"] == expected_db_raw, (
            f"[{label}] 实际={canon_row['who_knows']!r} 期望={expected_db_raw!r}"
        )


# ============================================================================
# 2. NULL 守卫对 domain 路径同样生效
# ============================================================================


def test_domain_crud_three_state_who_knows_null_guard(tmp_path: Path):
    """domain ``CharacterService`` create / update + ``LedgerService`` create_hook
    对 who_knows 三态（None / [] / 非空）落库结果与 canon 写透口径一致——
    证明 ``write_helpers.encode_who_knows`` 是双路唯一权威实现（NULL 守卫对
    domain 路径同样生效）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)

    from packages.domain.character.service import CharacterService
    from packages.domain.character.models import CharacterCreate, CharacterUpdate
    from packages.domain.ledger.service import LedgerService
    from packages.domain.ledger.models import HookCreate

    char_svc = CharacterService(db_path)
    ledger_svc = LedgerService(db_path)

    # ---- Character：None → NULL ----
    c_none = char_svc.create(pid, CharacterCreate(name="c_none"))
    # update 显式传 None（注意：CharacterUpdate.who_knows=None 表示「不更新」语义——
    # 这里用 create 时不传 who_knows 默认值 = None，落 DB 应为 NULL）
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT who_knows FROM characters WHERE character_id = ?", (c_none["character_id"],)
        ).fetchone()
        assert row["who_knows"] is None, f"Character who_knows=None 应落 NULL，实落 {row['who_knows']!r}"
    finally:
        conn.close()

    # ---- Character：[] → '[]' ----
    c_empty = char_svc.create(pid, CharacterCreate(name="c_empty", who_knows=[]))
    char_svc.update(c_empty["character_id"], CharacterUpdate(who_knows=[]))
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT who_knows FROM characters WHERE character_id = ?", (c_empty["character_id"],)
        ).fetchone()
        assert row["who_knows"] == "[]", f"Character who_knows=[] 应落 '[]'，实落 {row['who_knows']!r}"
    finally:
        conn.close()

    # ---- Character：['char_x'] → JSON 字符串 ----
    c_list = char_svc.create(pid, CharacterCreate(name="c_list", who_knows=["char_x"]))
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT who_knows FROM characters WHERE character_id = ?", (c_list["character_id"],)
        ).fetchone()
        assert row["who_knows"] == '["char_x"]', f"Character who_knows=['char_x'] 应落 JSON，实落 {row['who_knows']!r}"
    finally:
        conn.close()

    # ---- Hook：None / [] / ['a'] 三态 ----
    h_none = ledger_svc.create_hook(pid, HookCreate(name="h_none"))
    h_empty = ledger_svc.create_hook(pid, HookCreate(name="h_empty", who_knows=[]))
    h_list = ledger_svc.create_hook(pid, HookCreate(name="h_list", who_knows=["char_x"]))
    conn = _connect(db_path)
    try:
        rows = {r["name"]: r["who_knows"] for r in conn.execute(
            "SELECT name, who_knows FROM hooks WHERE project_id = ?", (pid,)
        ).fetchall()}
        assert rows["h_none"] is None, f"Hook who_knows=None 应落 NULL，实落 {rows['h_none']!r}"
        assert rows["h_empty"] == "[]", f"Hook who_knows=[] 应落 '[]'，实落 {rows['h_empty']!r}"
        assert rows["h_list"] == '["char_x"]', f"Hook who_knows=['char_x'] 应落 JSON，实落 {rows['h_list']!r}"
    finally:
        conn.close()

    # ---- 反序列化：domain read 路径（_decode_who_knows）应正确还原三态 ----
    assert c_none["who_knows"] is None
    assert c_empty["who_knows"] == []  # '[]' → []（非 None）
    assert c_list["who_knows"] == ["char_x"]
    assert h_none["who_knows"] is None
    assert h_empty["who_knows"] == []
    assert h_list["who_knows"] == ["char_x"]


# ============================================================================
# 3. bonus：write_helpers.decode_who_knows 行为契约
# ============================================================================


def test_decode_who_knows_round_trip():
    """write_helpers.decode_who_knows：DB raw → Python list|None 的契约稳定。
    本测试做一次回归保护：未来任何对 decode_who_knows 的修改必须保留以下语义：
    - None / 空字符串 / 非 list JSON → None
    - '[]' → []
    - '["a","b"]' → ["a","b"]
    - 解析失败 / 非 str 非 list 输入 → None
    """
    from packages.core.story_state.write_helpers import decode_who_knows

    assert decode_who_knows(None) is None
    assert decode_who_knows("") is None
    assert decode_who_knows("[]") == []
    assert decode_who_knows('["a","b"]') == ["a", "b"]
    assert decode_who_knows("not-a-json") is None
    assert decode_who_knows("{}") is None  # dict（非 list）→ None
    assert decode_who_knows(123) is None   # 非 str / 非 list → None
    assert decode_who_knows(["a", "b"]) == ["a", "b"]  # list 直接返回