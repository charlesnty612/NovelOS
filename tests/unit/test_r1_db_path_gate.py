# -*- coding: utf-8 -*-
"""R1 落码判别用例（BUG-58 / BUG-59 修法落码批 · PM-196-R1-CODE）。

★ **本文件不在仓内**（仓内 ``tests/`` 不在本次写域 ⇒ 落码不得扩写域）⇒
它只存在于**仓外副本**侧，并另存一份到 ``门禁证据\\NovelOS\\`` 作交件证据。
★ PM DoD⑧：**副本来源 ＋ 被测字节 SHA256** 必须在跑批前报出 ⇒ 见 ``PROVENANCE``，
  且 :func:`test_00_副本来源与被测字节SHA_可报且自校验` 会在跑批时**重算**并比对
  （副本被改动 ⇒ 本用例必红）。

★ **落仓后本用例不得写仓内**：仓内落点只用**不创建**的路径判定（``not_data/`` **不 mkdir**、
``data/`` **不建文件**）⇒ 跑完仓内 ``git status`` 不出现任何新条目。

判别口径（PM 口径①／②／③ ＋ S-2 六条硬要求）：

- 库落点闸（``packages.core.db.check_db_path``）：仓内**仅** ``data/`` 子树放行
  （不放 ``tmp/``）；``parts[0]`` **精确匹配**（禁子串包含）⇒ ``data2/``、``not_data/`` 必拒；
- 许可通道＝**调用点 kwarg 为主**；环境变量 ``NOVELOS_ALLOW_REPO_LOCAL_DB``
  **只收紧不放宽**（env 存在**不得**兜底放行）；
- 分层门禁：库层 ``confirm: bool = False`` ⇒ 拒绝且**零副作用**；CLI ``--yes`` 映射到该形参；
- ``restore()`` 内部 3 处失败回滚自调用**显式传** ``confirm=True``（S-2 ③），
  且替换后失败时目标库**内容指纹逐位回到旧库**（S-2 ⑤⑥）。
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# DoD⑧：副本来源 ＋ 被测字节 SHA256
# ---------------------------------------------------------------------------
PROVENANCE = {
    "copy_source": "工作树复制（robocopy /E）——**非** HEAD 导出：被测字节＝本轮落码后的工作树",
    "src_repo": r"D:\zcodeproject\NovelOS",
    "copy_root": r"D:\zcodeproject\.osdevb_r1_20261025_162611\copy",
    "copied_at": "2026-10-25 16:41（Get-Date 实测，robocopy 完成时刻）",
    "excluded": ".git / .venv / data / tmp / node_modules / __pycache__ / .pytest_cache / "
                "secrets.json* / *.db / *.db-wal / *.db-shm",
    "tested_bytes_sha256": {
        "packages/core/db.py": "FD2209C8951777BF54B74729DD5CD8AEE4BC3220EB83EE22AB30A48B884B7197",
        "scripts/restore_db.py": "AA67EC1AA9FDAF16BA2DEE12F7711B45C52317873C2946EE34FD43756019C1AB",
        "scripts/m1_long_run.py": "164B62CA43AC385E5755FE996C868CE7880F78DD0618D9CE568B55B14D25B269",
        "scripts/ai_tone_calibrate.py": "302067C7FEC8C68704FBBE7C719BB189432AE72E7C5055B70D359AAB77FAF9CF",
        "scripts/m2_judge.py": "1F09DCA05A27368BA0D43FEDEEADEDC3FD33AFCF67BA0EFB0AB71AA98025B00C",
        "scripts/normalize_chapters.py": "AD913883F91EF4A7A8226BC5B62A66BA60B02D26FF49E6211BC9D8FC60E6CA25",
        "scripts/outline_check.py": "CF70A0ED8216DB8272A6273DCE3308F4A725749FB6359998BC31675A6382E452",
        "scripts/m1_report.py": "E47545B52991AF7FDF568C5BA698E5A04E7B6955AC865350B6C39097CEBB0D70",
        "scripts/inspect_stalled_runs.py": "5AEE5AB4D39F4A9441AE44E7162590BCED9130E7478BD26309B21A4EE29D094E",
        "scripts/check_orphan_relationships.py": "8787DEB7B4A8ECDC4CADF0C6651FB93EDCC05692D2C8416EFFA91F224C909598",
        "scripts/check_state_sync.py": "082AECF5196364429EEEFA8F49F18C531157C8515DC624FE458A6AFD927550F7",
        "scripts/delta_anchoring_calibrate.py": "A85A129A9E6EFE1E3F60D9AB3564D4DE9AD8AACEFC4FE98D9081B249194DAF55",
        "scripts/readability_audit.py": "BEF8B01AD7A257EFEC7B360FBB2736EB11CF90B2D00DA4A311CE53706D4AEBA6",
        "scripts/revise_fidelity_calibrate.py": "D2FA18A1207C74C01E3600C715161B2EE12E1DACDEEFA12DAF74A8FD1CA3B1B4",
        "scripts/backup_db.py": "FE96A9E63C3641FFAEC0E162D56AEB4EC2D05C8A0F5C32E024242E86BF9D3CEF",
        "tests/unit/quality/test_ai_ending_summary_paragraph.py": "A8829A3B29634A7666FFE7BC33E0D24C3D6BC91DF268FC0E9989A5062CB8EA1E",
    },
}

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from packages.core import db as core_db  # noqa: E402
from packages.core.db import (  # noqa: E402
    EXIT_DB_PATH_REJECTED,
    RepoLocalDbPathError,
    check_db_path,
    get_connection,
)


# --------------------------------------------------------------------------- 工具
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _content_fp(path: Path) -> str:
    """库**内容**指纹（表清单 ＋ 各表行内容）——与文件字节布局无关。"""
    conn = sqlite3.connect(str(path))
    try:
        tables = sorted(
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        h = hashlib.sha256()
        for t in tables:
            h.update(t.encode())
            for row in conn.execute(f'SELECT * FROM "{t}" ORDER BY 1'):
                h.update(repr(tuple(row)).encode())
        return h.hexdigest().upper()
    finally:
        conn.close()


def _make_db(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t_marker (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("DELETE FROM t_marker")
        conn.execute("INSERT INTO t_marker (v) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _repo_bad_dir() -> Path:
    """仓内**非** data/ 的落点（闸必须拒）。"""
    d = REPO_ROOT / "not_data"
    return d / "evil.db"


# --------------------------------------------------------------------------- DoD⑧
def test_00_副本来源与被测字节SHA_可报且自校验():
    """副本来源 ＋ 被测字节 SHA256：**重算比对**（副本被改动 ⇒ 本用例必红）。"""
    print("\n[PROVENANCE] " + repr(PROVENANCE["copy_source"]))
    print("[PROVENANCE] src_repo=" + PROVENANCE["src_repo"])
    print("[PROVENANCE] copy_root=" + PROVENANCE["copy_root"])
    print("[PROVENANCE] copied_at=" + PROVENANCE["copied_at"])
    print("[PROVENANCE] excluded=" + PROVENANCE["excluded"])
    for rel, want in PROVENANCE["tested_bytes_sha256"].items():
        p = REPO_ROOT / rel
        assert p.is_file(), f"被测文件缺失：{rel}"
        got = _sha(p)
        print(f"[TESTED-BYTES] {rel}  sha256={got}")
        assert got == want, f"被测字节与登记值不一致：{rel}\n  登记={want}\n  实测={got}"


# --------------------------------------------------------------------------- C-01 库落点闸
def test_C01_库落点闸_仓内非data_拒绝且零副作用():
    bad = _repo_bad_dir()
    with pytest.raises(RepoLocalDbPathError):
        check_db_path(bad)
    assert not bad.exists(), "闸拒绝后**不得**留下任何文件（零副作用）"
    with pytest.raises(RepoLocalDbPathError):
        get_connection(bad)
    assert not bad.exists(), "get_connection 拒绝后**不得**建库（零副作用）"


def test_C01_库落点闸_仓内data_放行():
    ok = REPO_ROOT / "data" / "gate_probe_ok.db"
    # ⚠️ **不创建**仓内任何文件（``data/`` 是禁写面）⇒ 只判「闸放行」这一步，零字节落盘。
    # （``get_connection`` 的实际放行由 ``tmp_path``（仓外）那条覆盖。）
    assert check_db_path(ok) == ok.resolve()


def test_C01_库落点闸_仓外放行(tmp_path):
    out = tmp_path / "outside.db"
    conn = get_connection(out)
    conn.close()
    assert out.exists()


def test_C01_库落点闸_tmp与data2与not_data_一律拒():
    """口径①：仓内**仅** data/（不放 tmp/）＋ parts[0] 精确匹配（禁子串包含）。"""
    for rel in ("tmp/evil.db", "data2/evil.db", "not_data/evil.db", "database/evil.db"):
        p = REPO_ROOT / rel
        with pytest.raises(RepoLocalDbPathError):
            check_db_path(p)
        assert not p.exists(), f"{rel} 被拒后不得留下文件"


def test_C01_env存在仍不得放行(monkeypatch):
    """口径②：env 只传播、**不得兜底放行** ⇒ env 置真值也必须拒。"""
    monkeypatch.setenv(core_db.ENV_ALLOW_REPO_LOCAL_DB, "1")
    with pytest.raises(RepoLocalDbPathError):
        check_db_path(_repo_bad_dir())
    with pytest.raises(RepoLocalDbPathError):
        get_connection(_repo_bad_dir())


def test_C01_env收紧值_连显式授权也拒(monkeypatch):
    """env 只收紧不放宽：置 0 ⇒ 连 allow_repo_local=True 也拒。"""
    monkeypatch.setenv(core_db.ENV_ALLOW_REPO_LOCAL_DB, "0")
    with pytest.raises(RepoLocalDbPathError):
        check_db_path(_repo_bad_dir(), allow_repo_local=True)


def test_C01_显式授权_仅放行仓内非data(monkeypatch):
    """调用点 kwarg 为主：allow_repo_local=True ⇒ 放行（env 不参与放行）。"""
    monkeypatch.delenv(core_db.ENV_ALLOW_REPO_LOCAL_DB, raising=False)
    p = _repo_bad_dir()
    assert check_db_path(p, allow_repo_local=True) == p.resolve()


def test_C01_库族四件套同目录同判():
    """库族四件套（.db/-wal/-shm/-journal）同目录 ⇒ 同判。"""
    d = REPO_ROOT / "not_data"
    for suffix in (".db", ".db-wal", ".db-shm", ".db-journal"):
        with pytest.raises(RepoLocalDbPathError):
            check_db_path(d / f"x{suffix}")


# --------------------------------------------------------------------------- S-2 确认闸（restore_db）
def test_S2_restore_无confirm_拒绝且零副作用(tmp_path):
    from scripts import restore_db

    target = tmp_path / "target.db"
    _make_db(target, "old")
    before = _sha(target)
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"not-a-real-enc")
    key = tmp_path / "key.bin"
    key.write_bytes(b"k")
    rc = restore_db.restore(enc, target, key, None)
    assert rc == restore_db.EXIT_USAGE, f"未确认必须退 2，实得 {rc}"
    assert _sha(target) == before, "未确认 ⇒ 目标库必须逐位未变"
    assert list(tmp_path.glob(".target.db.restore-*")) == [], "未确认 ⇒ 不得留临时文件"


def test_S2_rollback_无confirm_拒绝且零副作用(tmp_path):
    from scripts import restore_db

    target = tmp_path / "target.db"
    snap = tmp_path / "snap.db"
    _make_db(target, "old")
    _make_db(snap, "snap")
    before = _sha(target)
    restore_db.rollback(snap, target)
    assert _sha(target) == before, "未确认回滚 ⇒ 目标库必须逐位未变"
    assert snap.exists(), "未确认回滚 ⇒ 快照不得被消耗"


def test_S2_restore_confirmTrue_放行且目标被替换(tmp_path, monkeypatch):
    from scripts import restore_db

    target = tmp_path / "target.db"
    _make_db(target, "old")
    new_db = tmp_path / "new.db"
    _make_db(new_db, "new")
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"enc")
    key = tmp_path / "key.bin"
    key.write_bytes(b"k")

    def fake_decrypt(_enc, dest, _key):
        dest.write_bytes(new_db.read_bytes())

    monkeypatch.setattr(restore_db, "decrypt_to", fake_decrypt)
    rc = restore_db.restore(enc, target, key, None, confirm=True)
    assert rc == restore_db.EXIT_OK, f"确认后必须成功，实得 {rc}"
    assert _content_fp(target) == _content_fp(new_db), "目标库应变成新库内容"


def test_S2_替换后完整性失败_内部回滚且内容指纹回到旧库(tmp_path, monkeypatch):
    """S-2 ③⑤⑥：**替换后**（:368 路径）失败 ⇒ 内部回滚必须能执行 ⇒ 内容指纹逐位回到旧库。"""
    from scripts import restore_db

    target = tmp_path / "target.db"
    _make_db(target, "old")
    old_fp = _content_fp(target)
    new_db = tmp_path / "new.db"
    _make_db(new_db, "new")
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"enc")
    key = tmp_path / "key.bin"
    key.write_bytes(b"k")

    calls = {"n": 0}

    def fake_decrypt(_enc, dest, _key):
        dest.write_bytes(new_db.read_bytes())

    def fake_verdict(path):
        calls["n"] += 1
        return "ok" if calls["n"] == 1 else "database disk image is malformed"

    monkeypatch.setattr(restore_db, "decrypt_to", fake_decrypt)
    monkeypatch.setattr(restore_db, "integrity_verdict", fake_verdict)
    rc = restore_db.restore(enc, target, key, None, confirm=True)
    assert rc == restore_db.EXIT_INTEGRITY_FAILED, f"应退 4（完整性），实得 {rc}"
    assert calls["n"] >= 2, "必须真的走到替换后校验这一步"
    assert _content_fp(target) == old_fp, "失败自动回滚后目标库内容指纹必须逐位回到旧库"


def test_S2_替换后表数不符_内部回滚且内容指纹回到旧库(tmp_path, monkeypatch):
    """S-2 ⑥：**替换后**（:375 路径）表数不符 ⇒ 内部回滚必须能执行。"""
    from scripts import restore_db

    target = tmp_path / "target.db"
    _make_db(target, "old")
    old_fp = _content_fp(target)
    new_db = tmp_path / "new.db"
    _make_db(new_db, "new")
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"enc")
    key = tmp_path / "key.bin"
    key.write_bytes(b"k")

    calls = {"n": 0}

    def fake_decrypt(_enc, dest, _key):
        dest.write_bytes(new_db.read_bytes())

    def fake_count(path):
        calls["n"] += 1
        return 1 if calls["n"] == 1 else 999

    monkeypatch.setattr(restore_db, "decrypt_to", fake_decrypt)
    monkeypatch.setattr(restore_db, "count_tables", fake_count)
    rc = restore_db.restore(enc, target, key, 1, confirm=True)
    assert rc == restore_db.EXIT_TABLE_COUNT_MISMATCH, f"应退 5（表数），实得 {rc}"
    assert calls["n"] >= 2, "必须真的走到替换后表数核对这一步"
    assert _content_fp(target) == old_fp, "失败自动回滚后目标库内容指纹必须逐位回到旧库"


def test_S2_替换失败_内部回滚且内容指纹回到旧库(tmp_path, monkeypatch):
    """S-2 ③：**替换失败**（:358 路径）⇒ 内部回滚必须能执行（此路径 replace 未成功）。"""
    from scripts import restore_db

    target = tmp_path / "target.db"
    _make_db(target, "old")
    old_fp = _content_fp(target)
    new_db = tmp_path / "new.db"
    _make_db(new_db, "new")
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"enc")
    key = tmp_path / "key.bin"
    key.write_bytes(b"k")

    def fake_decrypt(_enc, dest, _key):
        dest.write_bytes(new_db.read_bytes())

    real_replace = os.replace
    state = {"n": 0}

    def flaky_replace(src, dst, *a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("注入的替换失败")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(restore_db, "decrypt_to", fake_decrypt)
    monkeypatch.setattr(restore_db.os, "replace", flaky_replace)
    rc = restore_db.restore(enc, target, key, None, confirm=True)
    assert rc == restore_db.EXIT_USAGE, f"替换失败应退 2，实得 {rc}"
    assert state["n"] >= 2, "内部回滚必须真的调用了 replace（闸不得卡死自动回滚）"
    assert _content_fp(target) == old_fp, "失败自动回滚后目标库内容指纹必须逐位回到旧库"


def test_S2_内部三处自调用显式传confirmTrue():
    """S-2 ③：源码级判据 —— 三处失败回滚自调用**显式**传 confirm=True（不是隐式豁免）。"""
    src = (REPO_ROOT / "scripts" / "restore_db.py").read_text(encoding="utf-8")
    assert src.count("rollback(snapshot_path, target, confirm=True)") == 3, (
        "三处内部自调用必须都显式传 confirm=True；实测 "
        f"{src.count('rollback(snapshot_path, target, confirm=True)')} 处"
    )
    assert "rollback(snapshot_path, target)\n" not in src, "不得残留未带 confirm 的内部自调用"


def test_S2_两个签名都带confirm且默认False():
    """S-2 ①：``restore()`` / ``rollback()`` **两个**签名都有 ``confirm: bool = False``。"""
    import inspect

    from scripts import restore_db

    for fn in (restore_db.restore, restore_db.rollback):
        sig = inspect.signature(fn)
        assert "confirm" in sig.parameters, f"{fn.__name__} 缺 confirm 形参"
        assert sig.parameters["confirm"].default is False, f"{fn.__name__} 的 confirm 默认必须 False"


def test_S2_cli_yes可选且默认False():
    """S-2 ②：CLI ``--yes`` **可选**、缺省 False、映射到库层形参。"""
    from scripts import restore_db

    src = (REPO_ROOT / "scripts" / "restore_db.py").read_text(encoding="utf-8")
    assert '"--yes"' in src and 'action="store_true"' in src
    assert "confirm=args.yes" in src, "CLI --yes 必须映射到库层 confirm"


# --------------------------------------------------------------------------- C-02 / C-03
def test_C02_restore_db_目标库落点闸_拒且零副作用(tmp_path, capsys):
    from scripts import restore_db

    bad = REPO_ROOT / "not_data" / "target_out.db"
    enc = tmp_path / "x.db.enc"
    enc.write_bytes(b"enc")
    rc = restore_db.main(["--enc", str(enc), "--out", str(bad)])
    err = capsys.readouterr().err
    assert rc == restore_db.EXIT_USAGE, f"库落点被拒应退 2，实得 {rc}"
    assert "[拒绝库落点]" in err, f"stderr 必须含闸的诊断标记；实得 {err!r}"
    assert not bad.exists(), "被拒 ⇒ 目标库不得被创建（零副作用）"


def test_C03_restore_db_备份源落点闸_拒且零副作用(tmp_path, capsys):
    from scripts import restore_db

    bad = REPO_ROOT / "not_data" / "src.enc"
    out = tmp_path / "out.db"
    rc = restore_db.main(["--enc", str(bad), "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == restore_db.EXIT_USAGE, f"库落点被拒应退 2，实得 {rc}"
    assert "[拒绝库落点]" in err, f"stderr 必须含闸的诊断标记；实得 {err!r}"
    assert not bad.exists() and not out.exists(), "被拒 ⇒ 零副作用"


# --------------------------------------------------------------------------- C-04
def test_C04_m1_long_run_默认data_dir已改仓外():
    """B4：``--data-dir`` 默认落点必须**仓外**（原 data/m1_run 落在软件仓内）。"""
    from scripts import m1_long_run

    parser = m1_long_run._build_parser()
    default = parser.get_default("data_dir")
    p = Path(default)
    resolved = p.resolve()
    assert not str(resolved).lower().startswith(str(REPO_ROOT).lower()), (
        f"--data-dir 默认落点仍在软件仓内：{resolved}"
    )
    assert "novelos_m1_run" in str(default)


def test_C04_m1_long_run_db落点闸_拒且零副作用(tmp_path, capsys):
    from scripts import m1_long_run

    bad = REPO_ROOT / "not_data" / "m1.db"
    rc = m1_long_run.main(["--db", str(bad), "--data-dir", str(tmp_path / "run"), "status"])
    err = capsys.readouterr().err
    assert rc == EXIT_DB_PATH_REJECTED, f"应退 2（EXIT_DB_PATH_REJECTED），实得 {rc}"
    assert "[拒绝库落点]" in err, f"stderr 必须含闸的诊断标记；实得 {err!r}"
    assert not bad.exists(), "被拒 ⇒ 不得建库"


# --------------------------------------------------------------------------- C-06..C-16 入口闸
_SCRIPT_CASES = [
    ("C06", "ai_tone_calibrate", ["--db"], []),
    ("C07", "m2_judge", ["--db"], []),
    ("C08", "normalize_chapters", ["--db", "--project", "p1"], []),
    ("C09", "outline_check", ["--db", "--project", "p1"], []),
    ("C10", "m1_report", ["--db"], []),
    ("C11", "inspect_stalled_runs", ["--db"], []),
    ("C12", "check_orphan_relationships", ["--db"], []),
    ("C13", "check_state_sync", ["--db"], []),
    ("C14", "delta_anchoring_calibrate", ["--db"], []),
    ("C15", "readability_audit", ["--db"], []),
    ("C16", "revise_fidelity_calibrate", ["--db"], []),
]


@pytest.mark.parametrize("cid,mod_name,argv_tail,extra", _SCRIPT_CASES)
def test_入口闸_仓内非data落点必拒且零副作用(cid, mod_name, argv_tail, extra, monkeypatch, capsys):
    """每个入口改点一条：仓内非 data/ 的库落点 ⇒ 拒绝（退出码 2）且零副作用。

    ★ 判据**不只看退出码**（argparse 用法错误也退 2 ⇒ 只看码值会假绿）
      ⇒ 另断言 stderr 含闸的诊断标记 ``[拒绝库落点]``。
    """
    mod = importlib.import_module(f"scripts.{mod_name}")
    bad = REPO_ROOT / "not_data" / f"{mod_name}.db"
    argv = [f"scripts/{mod_name}.py"] + argv_tail + [str(bad)] + list(extra)
    monkeypatch.setattr(sys, "argv", argv)
    raised = None
    rc = None
    try:
        rc = mod.main()
    except SystemExit as exc:  # 形如 outline_check 的 main() -> None ⇒ SystemExit(2)
        raised = exc.code
    err = capsys.readouterr().err
    assert raised == EXIT_DB_PATH_REJECTED or rc == EXIT_DB_PATH_REJECTED, (
        f"{cid} {mod_name}：仓内非 data/ 落点必须被拒（退出码 2），"
        f"实得 raised={raised} rc={rc}"
    )
    assert "[拒绝库落点]" in err, (
        f"{cid} {mod_name}：stderr 必须含闸的诊断标记（防 argparse 用法错误同退 2 的假绿）；"
        f"实得 stderr={err!r}"
    )
    assert not bad.exists(), f"{cid} {mod_name}：被拒后不得建库（零副作用）"


def test_入口闸_11个文件源码均含闸且在连接之前():
    """静态判别：11 个入口文件**都**含闸调用，且闸排在连接语句**之前**。"""
    files = [
        "ai_tone_calibrate", "m2_judge", "normalize_chapters", "outline_check", "m1_report",
        "inspect_stalled_runs", "check_orphan_relationships", "check_state_sync",
        "delta_anchoring_calibrate", "readability_audit", "revise_fidelity_calibrate",
    ]
    for name in files:
        src = (REPO_ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert "check_db_path(" in src, f"{name} 缺库落点闸调用"
        assert "RepoLocalDbPathError" in src, f"{name} 缺闸异常处理"
        gi = src.index("check_db_path(")
        ci = src.find("sqlite3.connect(")
        assert ci == -1 or gi < ci, f"{name}：闸必须排在连接语句之前"


# --------------------------------------------------------------------------- C-05 / C-17
def test_C05_backup_db_源库落点闸_拒且零副作用(tmp_path, capsys):
    """C-05：``backup_db.py`` 的**源库** ``--db`` 落点过闸（产物落点已由 :474 过闸 ⇒ 不动）。"""
    from scripts import backup_db

    bad = REPO_ROOT / "not_data" / "backup_src.db"
    raised = None
    try:
        backup_db.main(["--db", str(bad), "--out", str(tmp_path / "out"), "--keep", "1"])
    except SystemExit as exc:
        raised = exc.code
    err = capsys.readouterr().err
    assert raised == 2, f"源库落点被拒应退 2（EXIT_WRITE_PATH_REJECTED），实得 {raised}"
    assert "[拒绝库落点]" in err, f"stderr 必须含闸的诊断标记；实得 {err!r}"
    assert not bad.exists(), "被拒 ⇒ 不得建库（零副作用）"
    assert not (tmp_path / "out").exists(), "被拒 ⇒ 不得落任何产物（零副作用）"


def test_C17_测试文件_DEV_DB_落点过闸且仓内data放行():
    """C-17：``_DEV_DB``（仓内真库 ``data/novelos.db``）落点过闸；**只读 SELECT 也过闸**。"""
    src = (REPO_ROOT / "tests" / "unit" / "quality"
           / "test_ai_ending_summary_paragraph.py").read_text(encoding="utf-8")
    assert "check_db_path(_DEV_DB)" in src, "该文件必须对 _DEV_DB 落点过闸"
    gi = src.index("check_db_path(_DEV_DB)")
    ci = src.index("sqlite3.connect(str(_DEV_DB))")
    assert gi < ci, "闸必须排在连接语句之前"
    dev_db = REPO_ROOT / "data" / "novelos.db"
    assert check_db_path(dev_db) == dev_db.resolve(), "仓内 data/ 落点必须放行（不得误伤只读取数）"
