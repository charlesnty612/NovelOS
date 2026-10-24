"""``scripts/check_content_repo_guard.py`` 巡检脚本的回归测试（Task 6 / D-0）。

背景：硬规则 5「内容仓不推 GitHub」此前**只有纪律、没有守卫**——内容仓
``origin`` 仍指向 GitHub 且本地领先 17 个提交 ⇒ 一次误 push 即发布全书。

本文件钉住：

1. **只读**：脚本只允许调用只读 git 子命令（``rev-parse`` / ``remote -v`` /
   ``config --get`` / ``rev-list`` / ``status``）——**禁止** ``fetch`` / ``push`` /
   ``commit`` / ``add`` 等任何会改远端或本地引用的命令；
2. **结论正确**：push 通道开启且无 pre-push 钩子 ⇒ 「能被误推」+ 退出码非 0；
   push 已禁用或有 pre-push 钩子 ⇒ 退出码 0；
3. **巡检自身出错**（不是 git 仓库）⇒ 退出码 2。

⚠️ 本文件**不**碰真实内容仓（``D:/zcodeproject/NovelOS-Content``）——git 调用
全部被 monkeypatch 替换成合成输出，因此测试**零 git 写操作**、零真实仓接触。
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_content_repo_guard.py"


@pytest.fixture()
def mod():
    """导入被测脚本（``scripts`` 是命名空间包，仓库根在 ``pythonpath`` 上）。"""
    return importlib.import_module("scripts.check_content_repo_guard")


# ---------------------------------------------------------------------------
# 合成 git 输出
# ---------------------------------------------------------------------------


class _FakeGit:
    """按调用参数返回合成输出的 ``_run_git`` 替身，并记录全部调用。"""

    def __init__(
        self,
        *,
        is_repo: bool = True,
        fetch_url: str = "https://github.com/charlesnty612/NovelOS-Content.git",
        push_url: str | None = "https://github.com/charlesnty612/NovelOS-Content.git",
        hooks_path: str = "hooks",
        ahead: int | None = 17,
    ) -> None:
        self.is_repo = is_repo
        self.fetch_url = fetch_url
        self.push_url = push_url
        self.hooks_path = hooks_path
        self.ahead = ahead
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        self.calls.append(list(args))
        cmd = " ".join(args)
        if "rev-parse" in cmd and "--is-inside-work-tree" in cmd:
            return (0, "true\n", "") if self.is_repo else (128, "", "not a git repository")
        if "remote" in cmd and "-v" in cmd:
            lines = []
            if self.fetch_url:
                lines.append(f"origin\t{self.fetch_url} (fetch)")
            if self.push_url:
                lines.append(f"origin\t{self.push_url} (push)")
            return (0, "\n".join(lines) + ("\n" if lines else ""), "")
        if "--git-path" in cmd:
            # ``git rev-parse --git-path hooks`` 返回的是 **hooks 目录**
            return (0, f"{self.hooks_path}\n", "")
        if "rev-list" in cmd:
            if self.ahead is None:
                return (128, "", "no upstream configured")
            return (0, f"{self.ahead}\n", "")
        if "@{u}" in cmd:
            if self.ahead is None:
                return (128, "", "no upstream")
            return (0, "origin/master\n", "")
        return (0, "", "")


# ---------------------------------------------------------------------------
# 只读纪律
# ---------------------------------------------------------------------------


def test_script_exists_and_is_readonly_by_construction(mod) -> None:
    """脚本存在，且源码里不出现任何 git 写子命令。"""
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("git push", "git commit", "git add", "git fetch", "git reset"):
        assert forbidden not in text, f"巡检脚本不得出现写操作：{forbidden}"


def test_only_readonly_git_subcommands_are_used(mod, monkeypatch, tmp_path, capsys) -> None:
    """跑一遍巡检，断言**全部** git 调用都是只读子命令。"""
    fake = _FakeGit()
    monkeypatch.setattr(mod, "_run_git", fake)

    mod.main(["--content-dir", str(tmp_path)])
    capsys.readouterr()

    assert fake.calls, "脚本应当调用 git 读取状态"
    allowed = {"rev-parse", "remote", "rev-list", "config", "status", "symbolic-ref"}
    for call in fake.calls:
        sub = next((c for c in call if not c.startswith("-") and "." not in c), "")
        assert sub in allowed, f"巡检脚本调用了非只读 git 子命令：{call}"


# ---------------------------------------------------------------------------
# 结论与退出码
# ---------------------------------------------------------------------------


def test_push_enabled_without_hook_is_unsafe(mod, monkeypatch, tmp_path, capsys) -> None:
    """push 通道开启 + 无钩子 ⇒ 能被误推 + 退出码非 0。"""
    fake = _FakeGit(push_url="https://github.com/charlesnty612/NovelOS-Content.git", ahead=17)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 1, f"应被判为不合规（退出码 1），实际 {code}"
    assert "能被误推" in out
    assert "17" in out  # 领先提交数必须报出来


def test_disabled_push_url_is_safe(mod, monkeypatch, tmp_path, capsys) -> None:
    """push URL 已禁用 ⇒ 合规（退出码 0）。"""
    fake = _FakeGit(push_url="no_push", ahead=17)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0, f"push 已禁用时应合规（退出码 0），实际 {code}"
    assert "不能被误推" in out
    # fetch URL 必须仍然可见（禁用 push 不等于删掉 fetch）
    assert "github.com" in out


def test_installed_no_push_scheme_sentinel_is_recognised(mod, monkeypatch, tmp_path, capsys) -> None:
    """本机实际安装的哨兵 ``no-push://novelos-content-push-disabled`` 必须认成「已禁用」。

    判别：**仅凭 push URL**（不依赖钩子）也必须判为合规 ⇒ 钩子被删也不误报。
    """
    fake = _FakeGit(push_url="no-push://novelos-content-push-disabled", ahead=17)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "push URL 已禁用" in out
    assert "仍指向远端" not in out, "禁用哨兵不得被描述成「仍指向远端」"


def test_pre_push_hook_blocks_push(mod, monkeypatch, tmp_path, capsys) -> None:
    """有 pre-push 钩子 ⇒ 合规（退出码 0），但必须说明钩子是唯一屏障。"""
    # 钩子检测走**文件系统**（git 不报告钩子是否存在）⇒ 在临时目录里放一个钩子文件。
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    fake = _FakeGit(hooks_path="hooks", ahead=17)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "pre-push" in out
    assert "不能被误推" in out


def test_empty_pre_push_hook_is_not_a_barrier(mod, monkeypatch, tmp_path, capsys) -> None:
    """空钩子文件不构成屏障（否则「装了钩子」会被当成安全）。"""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text("", encoding="utf-8")

    fake = _FakeGit(hooks_path="hooks", ahead=17)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 1, "空钩子不构成屏障 ⇒ 仍判为能被误推"
    assert "空" in out


def test_non_git_directory_is_error(mod, monkeypatch, tmp_path, capsys) -> None:
    """不是 git 仓库 ⇒ 巡检自身出错（退出码 2）。"""
    fake = _FakeGit(is_repo=False)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path)])
    out = capsys.readouterr().out + capsys.readouterr().err

    assert code == 2
    assert "git" in out.lower()


def test_json_output_is_machine_readable(mod, monkeypatch, tmp_path, capsys) -> None:
    """``--json`` 输出机器可读结论（供 Lead 自动化核对）。"""
    fake = _FakeGit(ahead=3)
    monkeypatch.setattr(mod, "_run_git", fake)

    code = mod.main(["--content-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["can_be_pushed"] is True
    assert payload["ahead_count"] == 3
    assert payload["fetch_url"].startswith("https://")


# ---------------------------------------------------------------------------
# 凭据收敛（D-18）：悬空 secrets 条目 + 库内明文 api_key
# ---------------------------------------------------------------------------


def _seed_model_db(db_path: Path, *, profiles: list[tuple[str, str, str | None]]) -> None:
    """种一张极简 ``model_profiles`` / ``model_connections`` 表。

    ``profiles`` 元素 = ``(profile_id, provider, api_key | None)``。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE model_profiles ("
            "profile_id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
            "params_json TEXT NOT NULL DEFAULT '{}')"
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
        conn.commit()
    finally:
        conn.close()


def test_dangling_secrets_entry_is_reported(mod, tmp_path, capsys) -> None:
    """secrets.json 的键对不上任何档案 / 连接 / provider ⇒ 判为悬空 + 退出码非 0。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {"api_keys": {"mprof_dead000000": "sk-x", "mprof_live01": "sk-y"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 1, "有悬空条目 ⇒ 不合规（退出码 1）"
    assert "mprof_dead000000" in out
    assert "悬空条目" in out


def test_plaintext_api_key_in_db_is_reported(mod, tmp_path, capsys) -> None:
    """运行库里的明文 api_key 必须被点名（计数 + 档案 id），但**绝不打印密钥值**。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-super-secret-value")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {}}, ensure_ascii=False), encoding="utf-8")

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 1
    assert "mprof_live01" in out
    assert "sk-super-secret-value" not in out, "输出**绝不**包含密钥值"


def test_secrets_mode_ok_when_nothing_dangling(mod, tmp_path, capsys) -> None:
    """无悬空条目 + 库内无明文 ⇒ 合规（退出码 0）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_live01": "sk-y"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "合规" in out


def test_secrets_migrate_defaults_to_dry_run(mod, tmp_path, capsys) -> None:
    """迁移命令**默认 dry-run**：不写 secrets.json、不改库。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {}}, ensure_ascii=False), encoding="utf-8")

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    capsys.readouterr()

    assert code == 1
    # secrets.json 未被改写
    assert json.loads(secrets.read_text(encoding="utf-8"))["api_keys"] == {}
    # 库里明文仍在
    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "api_key" in params, "dry-run 不得改库内明文"


def test_secrets_migrate_apply_moves_key_to_file(mod, tmp_path, capsys) -> None:
    """``--apply``：key 落到 secrets.json、库内明文被清（**在临时库上验证**）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {}}, ensure_ascii=False), encoding="utf-8")

    mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets), "--apply"])
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert payload["api_keys"]["mprof_live01"] == "sk-plain"

    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "api_key" not in params, "迁移后库内不应再有明文 api_key"
    assert "model" in params, "迁移不得丢掉 params_json 的其它字段"


def test_secrets_migrate_apply_keeps_existing_entries(mod, tmp_path, capsys) -> None:
    """``--apply`` 不得丢掉 secrets.json 里已有的其它条目。

    ⚠️ **行为变更（修法 C）**：``--migrate`` 也要过归属闸门 ⇒ 本夹具只有 provider
    级别名键（无实体 id 键）⇒ 显式声明归属（``--force-all-dangling``）。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"minimax": "sk-existing"}, "other": "keep"},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--apply", "--force-all-dangling",
        ]
    )
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert payload["api_keys"]["minimax"] == "sk-existing"
    assert payload["other"] == "keep"
    assert payload["api_keys"]["mprof_live01"] == "sk-plain"


# ---------------------------------------------------------------------------
# 悬空条目清理（--prune-dangling）
#
# 为什么要它：守卫**检测**悬空条目并判「不合规」，但原先只给了「把库内明文搬进
# 文件」的迁移命令，没有给「清掉悬空条目」的出路 ⇒ 一次档案删除之后，
# secrets.json 里会永久留下一条指向**已删档案**的死密钥，巡检从此永远报不合规
# （红灯常亮 ⇒ 守卫被忽略）。
# ---------------------------------------------------------------------------


def test_prune_dangling_defaults_to_dry_run(mod, tmp_path, capsys) -> None:
    """``--prune-dangling`` 不加 ``--apply`` ⇒ **不写任何文件**。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    original = json.dumps(
        {"api_keys": {"mprof_dead000000": "sk-x", "mprof_live01": "sk-y"}, "other": "keep"},
        ensure_ascii=False,
    )
    secrets.write_text(original, encoding="utf-8")

    code = mod.main(
        ["secrets", "--db", str(db), "--secrets-file", str(secrets), "--prune-dangling"]
    )
    out = capsys.readouterr().out

    assert secrets.read_text(encoding="utf-8") == original, "dry-run 不得改文件"
    assert "未执行" in out, "dry-run 必须明说「未执行」"
    assert code == 1, "悬空条目未被清理 ⇒ 仍不合规"


def test_prune_dangling_apply_removes_only_dangling_keys(mod, tmp_path, capsys) -> None:
    """``--prune-dangling --apply`` ⇒ 只删悬空键；有效键与其它段**原样保留**。

    ⚠️ **只动 secrets.json**：库内明文**不动** ⇒ 巡检仍判「不合规」（明文还在）
    ⇒ 退出码 **1**。这是「一个开关只管一件事」的直接后果，也是刻意的：
    清理死密钥不该顺手把库内明文搬走（那会覆盖同名文件条目，用户看到的东西会变）。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {"api_keys": {"mprof_dead000000": "sk-x", "mprof_live01": "sk-y"}, "other": "keep"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert "mprof_dead000000" not in payload["api_keys"], "悬空键必须被删"
    assert payload["api_keys"]["mprof_live01"] == "sk-y", "有效键不得被动（清理不得触发迁移）"
    assert payload["other"] == "keep", "api_keys 以外的段不得丢"
    assert code == 1, "库内明文还在 ⇒ 仍不合规（退出码 1）"


def test_migrate_and_prune_together_reach_compliance(mod, tmp_path, capsys) -> None:
    """``--migrate --prune-dangling --apply`` ⇒ 两个动作都做 ⇒ 巡检转合规（退出码 0）。

    ⚠️ ``--force-all-dangling``（2026-10-23 交集闸门）：本夹具的 secrets 文件里
    **只有一个死键**、库里另有一个活档案 ⇒ secrets 的键与已知集合**零交集**
    ⇒ 「全部键都死」与「--db 指错了库」在数据上**不可区分** ⇒ 闸门默认**拒绝**
    （退出码 2）⇒ 本用例显式声明「我知道这个库是对的」。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_dead000000": "sk-x"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--prune-dangling", "--apply", "--force-all-dangling",
        ]
    )
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert "mprof_dead000000" not in payload["api_keys"], "悬空键必须被删"
    assert payload["api_keys"]["mprof_live01"] == "sk-plain", "库内明文必须已搬进文件"

    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "api_key" not in params, "迁移后库内不应再有明文 api_key"
    assert code == 0, "悬空清零 + 明文清零 ⇒ 合规（退出码 0）"


def test_prune_dangling_apply_keeps_db_plaintext_untouched(mod, tmp_path, capsys) -> None:
    """清理悬空条目**不动库**（它只管 secrets.json；清库内明文是 --migrate 的事）。

    ⚠️ **行为变更（修法 A）**：归属闸门只认实体身份 ⇒ 补一个实体 id 键（否则空转）。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {"api_keys": {"mprof_live01": "sk-y", "mprof_dead000000": "sk-x"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    capsys.readouterr()

    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "sk-plain" in params, "清理悬空条目不得顺手动库内明文"


# ⚠️ 2026-10-24 瘦身：本用例已并入参数化的
# ``test_malformed_secrets_file_is_refused_by_every_command``（参数 id = ``prune``）
# —— 「坏 JSON ⇒ 清理拒绝执行」在**三条命令**（巡检 / 清理 / 迁移）上同口径。


# ---------------------------------------------------------------------------
# F-1（CRITICAL，2026-10-23 对抗审查）：--prune-dangling 不得把
# 「诊断不可信」当成「全部悬空」
#
# 缺陷形状：--db 指向的库**存在但没有** model_profiles / model_connections 表
# （或表存在但 0 行）⇒ 「已知集合」为空 ⇒ **每个键都被判成悬空** ⇒ 落盘后
# api_keys = {}，退出码 0 = 「合规」（绿）⇒ 一次误指 --db（打错路径 /
# 未迁移的库 / 另一个项目库 / 空库）就可能**清空用户全部凭据**，且无备份、无确认。
#
# 修法：读表失败 / 已知集合为空 ⇒ **拒绝清理**（退出码 2，不写任何文件）；
# 真正落盘前**先备份**（secrets.json.bak-<时间戳>，同目录、原子）。
# ---------------------------------------------------------------------------


def _seed_db_without_model_tables(db_path: Path) -> None:
    """造一个**存在但没有** model_profiles / model_connections 表的库（合成夹具）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()
    finally:
        conn.close()


def _seed_model_db_empty_tables(db_path: Path) -> None:
    """造一个**有表但 0 行**的库（合成夹具）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE model_profiles ("
            "profile_id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
            "params_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute(
            "CREATE TABLE model_connections ("
            "connection_id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
            "params_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.commit()
    finally:
        conn.close()


def test_prune_dangling_refuses_when_model_tables_missing(mod, tmp_path, capsys) -> None:
    """库存在但**没有 model_profiles 表** ⇒ 拒绝清理（退出码 2）+ 文件**逐字未改**。"""
    db = tmp_path / "novelos.db"
    _seed_db_without_model_tables(db)
    secrets = tmp_path / "secrets.json"
    original = json.dumps(
        {"api_keys": {"sk-user-1": "v1", "sk-user-2": "v2"}}, ensure_ascii=False
    )
    secrets.write_text(original, encoding="utf-8")

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    err = capsys.readouterr().err

    assert code == 2, (
        f"诊断不可信 ⇒ 必须拒绝清理（退出码 2），实际 {code}\nstderr={err}"
    )
    assert "已中止清理" in err, f"必须**明确拒绝**（而不是「没什么可删」）：{err}"
    assert "model_profiles" in err, f"必须说清为什么拒绝：{err}"
    assert secrets.read_text(encoding="utf-8") == original, "secrets.json 必须**逐字未改**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝清理 ⇒ 不得产生备份文件"


def test_prune_dangling_refuses_when_model_tables_are_empty(mod, tmp_path, capsys) -> None:
    """库存在但表**0 行** ⇒ 同样拒绝清理（「全是死键」与「没有引用数据」不可区分）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db_empty_tables(db)
    secrets = tmp_path / "secrets.json"
    original = json.dumps({"api_keys": {"sk-user-1": "v1"}}, ensure_ascii=False)
    secrets.write_text(original, encoding="utf-8")

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    err = capsys.readouterr().err

    assert code == 2, f"0 行 ⇒ 已知集合为空 ⇒ 必须拒绝（退出码 2），实际 {code}\n{err}"
    assert "已中止清理" in err, f"必须**明确拒绝**：{err}"
    assert secrets.read_text(encoding="utf-8") == original, "secrets.json 必须**逐字未改**"
    assert "已知集合为空" in err, f"必须说清为什么拒绝：{err}"


def test_prune_dangling_with_missing_db_deletes_nothing(mod, tmp_path, capsys) -> None:
    """库**不存在** ⇒ 不删任何键（现有安全行为必须保持）。"""
    db = tmp_path / "nope.db"
    assert not db.exists(), "前提：库不得已存在"
    secrets = tmp_path / "secrets.json"
    original = json.dumps({"api_keys": {"sk-user-1": "v1"}}, ensure_ascii=False)
    secrets.write_text(original, encoding="utf-8")

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    err = capsys.readouterr().err

    assert secrets.read_text(encoding="utf-8") == original, "库不存在 ⇒ 不得删任何键"
    assert code == 2, "库读不到 ⇒ 「无法判定」⇒ 退出码 2（不得报绿）"
    assert "已中止清理" in err, f"必须**明确拒绝**：{err}"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝清理 ⇒ 不得产生备份文件"


def test_prune_dangling_apply_removes_dead_key_and_leaves_backup(mod, tmp_path, capsys) -> None:
    """反向对照：库正常 + 确有死键 ⇒ 清理**真的执行** + 备份已生成 + 活键仍在。"""
    db = tmp_path / "novelos.db"
    # 无明文 api_key ⇒ 清理后巡检应当合规（退出码 0）
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {"api_keys": {"mprof_dead000000": "sk-x", "mprof_live01": "sk-y"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    err = capsys.readouterr().err

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert "mprof_dead000000" not in payload["api_keys"], "死键必须被删"
    assert payload["api_keys"]["mprof_live01"] == "sk-y", "活键必须仍在"

    backups = list(tmp_path.glob("secrets.json.bak-*"))
    assert len(backups) == 1, f"清理前必须留**恰好一份**备份：{backups}"
    backup_payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "mprof_dead000000" in backup_payload["api_keys"], "备份必须含清理前的全部键"
    assert backups[0].name in err, f"备份路径必须在 stderr 可见：{err}"
    assert code == 0, "清理后无悬空、库内无明文 ⇒ 合规（退出码 0）"


def test_secrets_mode_reports_unknown_when_db_unreadable(mod, tmp_path, capsys) -> None:
    """库读不到 ⇒ 判「**无法判定**」（退出码 2），**不得**报绿（假合规）。"""
    db = tmp_path / "novelos.db"
    _seed_db_without_model_tables(db)
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {"sk-user-1": "v1"}}, ensure_ascii=False), encoding="utf-8")

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 2, f"读不到运行库 ⇒ 退出码 2（巡检自身出错），实际 {code}\n{out}"
    assert "无法判定" in out, out
    assert "合规（无悬空条目" not in out, "读不到运行库时**不得**报「合规」"


def test_read_failure_and_empty_known_set_are_distinguished(mod, tmp_path) -> None:
    """「读表失败」与「已知集合为空」是**两件事** ⇒ 必须能区分开。

    ⚠️ **行为变更（修法 B）**：``blocked_reasons`` 现在**只装读表失败** ⇒
    「已知集合为空」改由**归属闸门**承载 ⇒ 断言位置随之改变。
    """
    missing = tmp_path / "missing.db"
    _seed_db_without_model_tables(missing)
    state_missing = mod.collect_secrets_state(missing, tmp_path / "secrets.json")
    assert state_missing.read_errors, "缺表 ⇒ 必须记为**读表失败**"
    assert any("model_profiles" in r for r in state_missing.read_errors)
    assert state_missing.known_keys == set(), "读表失败 ⇒ 已知集合为空"
    assert state_missing.blocked_reasons, "读表失败 ⇒ 进 blocked_reasons（阻断整个巡检）"

    empty = tmp_path / "empty.db"
    _seed_model_db_empty_tables(empty)
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_x": "sk-x"}}, ensure_ascii=False), encoding="utf-8"
    )
    state_empty = mod.collect_secrets_state(empty, secrets)
    assert not state_empty.read_errors, "表在但 0 行 ⇒ **不是**读表失败"
    assert state_empty.known_keys == set(), "0 行 ⇒ 已知集合为空"
    assert state_empty.entity_keys == set(), "0 行 ⇒ 实体身份集合为空"
    assert state_empty.blocked_reasons == [], "空库**不是**读表失败 ⇒ 不进 blocked_reasons"
    assert state_empty.dangling_undecidable, "归属无据 ⇒ 悬空条目**不可判**"
    assert "已知集合为空" in state_empty.dangling_undecidable, state_empty.dangling_undecidable


# ---------------------------------------------------------------------------
# F-2（MAJOR，2026-10-23 对抗审查）：悬空判据漏了运行期第 3 优先级
# ``secrets[<env_name>]``
#
# 缺陷形状：判据只认 profile_id ∪ connection_id ∪ provider ⇒ 形如
# ``NOVELOS_API_KEY_KIMI`` 的键被判成悬空 ⇒ 跑 prune 后该 provider 的**文件级
# 密钥失效**，且 ``--migrate`` 永远造不回来（它只写 profile_id / connection_id）。
# 修法（双保险）：① known 集合补上 env_name 形态；② prune 前对每个候选键做
# 「**是否仍可解析**」复核（**走生产函数** ``resolve_profile_api_key``）。
# ---------------------------------------------------------------------------


def test_env_name_key_is_not_reported_as_dangling(mod, tmp_path, capsys) -> None:
    """env_name 形态的键**不是悬空条目**（已知集合必须含 env_name 形态）。

    ⚠️ **行为变更（修法 A）**：归属闸门只认**实体身份** ⇒ 本夹具补一个实体 id 键。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "kimi", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {
                "api_keys": {
                    "NOVELOS_API_KEY_KIMI": "sk-live-kimi",
                    "mprof_live01": "sk-profile",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert "悬空条目数: 0" in out, out
    assert code == 0, f"env_name 键仍被引用 ⇒ 不算悬空 ⇒ 合规（退出码 0），实际 {code}\n{out}"


def test_prune_dangling_keeps_env_name_key_still_resolvable(mod, tmp_path, capsys) -> None:
    """**仍在使用**的 env_name 键 ⇒ 不许删；确无引用的键照删。"""
    from packages.core.secrets_store import resolve_profile_api_key

    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "kimi", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {
                "api_keys": {
                    "NOVELOS_API_KEY_KIMI": "sk-live-kimi",
                    # ⚠️ 行为变更（修法 A）：归属闸门只认实体身份 ⇒ 补一个实体 id 键。
                    "mprof_live01": "sk-profile",
                    "mprof_dead000000": "sk-dead",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 前提（真实值）：**生产解析函数**确实在用这个键 ⇒ 它不是死键。
    resolved = resolve_profile_api_key(
        None,
        "kimi",
        ["NOVELOS_API_KEY_KIMI"],
        None,
        secrets={"NOVELOS_API_KEY_KIMI": "sk-live-kimi"},
    )
    assert resolved == "sk-live-kimi", "前提：运行期确实在用这个 env_name 键"

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert payload["api_keys"].get("NOVELOS_API_KEY_KIMI") == "sk-live-kimi", (
        "仍被运行期解析的 env_name 键**不得**被删"
    )
    assert "mprof_dead000000" not in payload["api_keys"], "确无引用的键必须被删"
    assert code == 0, "清理后合规（退出码 0）"


# ---------------------------------------------------------------------------
# 对抗审查 B 的 F-3 / F-4（2026-10-23）
# ---------------------------------------------------------------------------


#: 凭据模式的三条命令路径：``(命令名, 追加参数, 拒绝执行时的诊断词)``。
#:
#: - ``patrol``：只读巡检（无写动作 ⇒ 「拒绝执行」只体现在退出码与结论上）；
#: - ``prune``：``--prune-dangling --apply``（只动 secrets.json）；
#: - ``migrate``：``--migrate --apply``（搬库内明文进文件 + 清库内明文）。
#:
#: ⚠️ 三条路径**必须同口径**：同一个「不可判」形状 ⇒ 同一个退出码 2
#: ⇒ 写路径一律**拒绝执行**（不写文件、不写备份、不清库内明文）。
_COMMAND_PATROLS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("patrol", (), ""),
    ("prune", ("--prune-dangling", "--apply"), "已中止清理"),
    ("migrate", ("--migrate", "--apply"), "已中止迁移"),
)


def _db_plaintext_params(db_path: Path) -> str:
    """读回某档案的 ``params_json``（用于断言「库内明文有没有被动」）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row is not None else ""


@pytest.mark.parametrize(
    ("command", "extra_args", "refusal_marker"),
    _COMMAND_PATROLS,
    ids=[name for name, _a, _m in _COMMAND_PATROLS],
)
def test_malformed_secrets_file_is_refused_by_every_command(
    mod, tmp_path, capsys, command, extra_args, refusal_marker
) -> None:
    """**secrets.json 读不出来** ⇒ 三条命令**同口径**：不可判 ⇒ 退出码 2。

    ⚠️ 「文件读不出来」与「文件不存在」是**两件事**：后者是合法状态（还没迁过），
    前者是**巡检自身出错** ⇒ 与本轮 F-1 的纪律同一条线（判不出来时不得报绿）。

    ⚠️ **写路径必须响亮拒绝**（F-3 同款纪律）：清理 / 迁移**不得**把坏文件写成
    空/半截 ⇒ 文件**逐字节不变** + **零备份** + **不清库内明文**。

    ⚠️ 2026-10-24 瘦身：原「巡检侧一条 + 清理侧一条」⇒ 合成本矩阵，
    并**补上迁移侧一例**（原为全仓缺口：迁移遇坏 JSON 从未被钉住）。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text("{ this is not json ", encoding="utf-8")
    before = secrets.read_bytes()

    code = mod.main(
        ["secrets", "--db", str(db), "--secrets-file", str(secrets), *extra_args]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, (
        f"{command}：读不出 secrets.json ⇒ 不可判 ⇒ 退出码 2，实际 {code}\n{out}"
    )
    assert "解析失败" in out, out
    if refusal_marker:
        assert refusal_marker in out, f"{command}：必须**明确拒绝**（而不是「没什么可做」）：{out}"
    assert secrets.read_bytes() == before, f"{command}：坏文件不得被改写"
    assert not list(tmp_path.glob("secrets.json.bak-*")), (
        f"{command}：拒绝执行 ⇒ 不得产生备份文件"
    )
    assert "api_key" in _db_plaintext_params(db), f"{command}：拒绝执行 ⇒ 不得清库内明文"


# ⚠️ 2026-10-24 瘦身：原「secrets.json 不存在 ⇒ 仍报合规」已删——它被
# ``test_legal_empty_state_is_still_compliant``（参数 id = ``absent-file``）
# **严格更强**地蕴含（那里同时钉了 read_errors / blocked_reasons 为空）
# ⇒ 零判别力损失。


def test_migrate_backs_up_secrets_file_before_rewriting_it(mod, tmp_path, capsys) -> None:
    """``--migrate --apply`` 写盘前**必须**留备份（旧版本可回退）。

    ⚠️ 迁移要**重写整个** secrets.json ⇒ 旧版本必须可回退（与 ``--prune-dangling`` 同纪律）。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_live01": "sk-OLD-FILE"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    before = secrets.read_text(encoding="utf-8")

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    capsys.readouterr()
    assert code == 0

    backups = sorted(tmp_path.glob("secrets.json.bak-*"))
    assert backups, "迁移前必须留下 secrets.json 的备份（对抗审查 F-3）"
    assert backups[0].read_text(encoding="utf-8") == before, "备份必须是**迁移前**的逐字内容"


def test_migrate_does_not_overwrite_a_newer_value_in_secrets_file(mod, tmp_path, capsys) -> None:
    """同名条目且**值不同** ⇒ 保留 secrets.json 里的值（运行期本来就用它）+ 报冲突。

    ⚠️ 运行期优先级：``secrets[<profile_id>]`` **高于** ``params_json.api_key``
    ⇒ 文件里的值才是**生效**的那个 ⇒ 迁移拿**旧明文**顶掉它 = 用旧值覆盖新值。
    """
    from packages.core.secrets_store import resolve_profile_api_key

    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-DB-OLDER")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_live01": "sk-FILE-NEWER"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert code == 0, out

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert payload["api_keys"]["mprof_live01"] == "sk-FILE-NEWER", (
        "同名且值不同 ⇒ 必须保留 secrets.json 里的值（不得被旧明文顶掉）"
    )
    assert "冲突" in out, f"必须把冲突**说出来**（不得静默）\n{out}"

    # 库内明文照常清掉（解析结果不变：运行期用的是文件里的值）
    conn = sqlite3.connect(str(db))
    try:
        params = conn.execute(
            "SELECT params_json FROM model_profiles WHERE profile_id = 'mprof_live01'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "api_key" not in json.loads(params), "库内明文必须被清掉"

    resolved = resolve_profile_api_key(
        "mprof_live01", "minimax", [], None, secrets=dict(payload["api_keys"])
    )
    assert resolved == "sk-FILE-NEWER", "生效的密钥必须与迁移前一致（不得被旧明文改变）"


# ---------------------------------------------------------------------------
# F-3（major，2026-10-23 对抗审查 C）：``secrets.json`` **结构非法** ⇒ 不可判
#
# 缺陷形状：生产侧 ``packages/core/secrets_store._read_file_fresh()`` 对
# 「顶层不是对象」/「``api_keys`` 段不是对象」会 log warning 并**返回 {}**
# ⇒ 运行期**拿不到凭据**；而巡检只把「JSON 解析失败」计入 ``read_errors``
# ⇒ 结构非法时仍报「合规」（exit 0）⇒ **真出问题却报绿**，且三条路径口径分裂：
#   巡检 ⇒ 报绿；``--prune-dangling`` ⇒ 拿「空 api_keys」当基线去判定；
#   ``--migrate`` ⇒ **静默把用户原有键重置为 {}**。
#
# 修法：结构非法 = **不可判** = **退出码 2**；两条写路径**一律拒绝执行**
# （不写文件、不写备份、不清库）⇒ 三条路径同口径。
# ⚠️ **反向不变量（不得过修）**：``secrets.json`` 不存在 / 无 ``api_keys`` 段 /
# ``api_keys`` 为 ``null`` ⇒ 运行期 ``_read_file_fresh()`` **不报 warning**
# ⇒ **合法空状态** ⇒ **仍然 exit 0**。
# ---------------------------------------------------------------------------


def _seed_clean_db(db_path: Path) -> None:
    """造一个「已知集合非空、库内无明文」的库（合成夹具）。

    ⚠️ 已知集合必须**非空**：否则「已知集合为空」会**先于**结构问题成为不可判原因
    ⇒ 测的就不是「结构非法」这一条了。
    """
    _seed_model_db(db_path, profiles=[("mprof_live01", "minimax", None)])


#: 「结构非法」的形状矩阵：``(形状名, 文件原文, 诊断关键词)``。
#:
#: ⚠️ ``api_keys`` 段列**三种** JSON 类型（字符串 / 数组 / 数字）—— 运行期
#: ``_read_file_fresh()`` 对三者一律 log warning 并返回 ``{}`` ⇒ 同属「凭据读不出来」；
#: 但三种类型**互为对照**（判据若被误写成「只查字符串」⇒ 数组 / 数字两例变红）。
_INVALID_STRUCTURE_SHAPES: tuple[tuple[str, str, str], ...] = (
    ("api_keys_is_string", json.dumps({"api_keys": "sk-xxx"}), "api_keys 段必须是对象"),
    ("api_keys_is_list", json.dumps({"api_keys": ["a"]}), "api_keys 段必须是对象"),
    ("api_keys_is_number", json.dumps({"api_keys": 123}), "api_keys 段必须是对象"),
    ("root_not_object", json.dumps(["a"]), "顶层必须是对象"),
)


@pytest.mark.parametrize(
    ("shape", "command"),
    [
        pytest.param(shape, cmd, id=f"{shape[0]}-{cmd[0]}")
        for shape in _INVALID_STRUCTURE_SHAPES
        for cmd in _COMMAND_PATROLS
    ],
)
def test_structurally_invalid_secrets_file_is_refused_by_every_command(
    mod, tmp_path, capsys, shape, command
) -> None:
    """``secrets.json`` **结构非法** ⇒ 三条命令**同口径**：不可判 ⇒ 退出码 2。

    ⚠️ 真缺陷：运行期 ``_read_file_fresh()`` 会 log warning 并返回 ``{}``
    ⇒ **运行期拿不到凭据**；旧实现只记一条 note ⇒ 巡检报绿 ⇒ 真出问题却报绿。

    ⚠️ **写路径必须拒绝执行**：清理 / 迁移**不得**把「读不出来的空凭据」当成空基线
    落盘（那会**静默抹掉用户原有键**）⇒ 文件**逐字节不变** + **零备份**
    + **不清库内明文**。

    ⚠️ 2026-10-24 瘦身：原 6 个函数（巡检 ×2 / 清理 ×2 / 迁移 ×2）⇒ 合成本矩阵；
    并**补上「root 场景 ⇒ 拒绝执行 ⇒ 不得清库内明文」**（原先只有 api_keys 场景
    有这条断言 ⇒ root 场景是个缺口）。
    """
    shape_id, raw_text, reason = shape
    command_id, extra_args, refusal_marker = command
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(raw_text, encoding="utf-8")
    before = secrets.read_bytes()

    code = mod.main(
        ["secrets", "--db", str(db), "--secrets-file", str(secrets), *extra_args]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, (
        f"{command_id}/{shape_id}：结构非法 ⇒ 不可判 ⇒ 退出码 2，实际 {code}\n{out}"
    )
    assert reason in out, f"{command_id}/{shape_id}：必须说清为什么不可判：{out}"
    if refusal_marker:
        assert refusal_marker in out, (
            f"{command_id}/{shape_id}：必须**明确拒绝**（而不是「没什么可做」）：{out}"
        )
    assert "合规（无悬空条目" not in out, (
        f"{command_id}/{shape_id}：结构非法时**不得**报「合规」"
    )
    assert secrets.read_bytes() == before, (
        f"{command_id}/{shape_id}：拒绝执行 ⇒ secrets.json 必须**逐字节未改**"
    )
    assert not list(tmp_path.glob("secrets.json.bak-*")), (
        f"{command_id}/{shape_id}：拒绝执行 ⇒ 不得产生备份文件"
    )
    assert "api_key" in _db_plaintext_params(db), (
        f"{command_id}/{shape_id}：拒绝执行 ⇒ 不得清库内明文"
    )


@pytest.mark.parametrize(
    ("shape_id", "raw_text"),
    [
        pytest.param("missing_api_keys_section", "{}", id="missing-api-keys-section"),
        pytest.param(
            "null_api_keys_section",
            json.dumps({"api_keys": None}),
            id="null-api-keys-section",
        ),
        pytest.param("absent_file", None, id="absent-file"),
    ],
)
def test_legal_empty_state_is_still_compliant(
    mod, tmp_path, capsys, shape_id, raw_text
) -> None:
    """**合法空状态**（无 ``api_keys`` 段 / ``api_keys`` 为 ``null`` / 文件不存在）
    ⇒ **仍然 exit 0**（不得判不可判）。

    ⚠️ **反向不变量（防过修）**：运行期 ``if api_keys is None: return {}`` **不报
    warning** ⇒ 那是「没有凭据」而非「凭据读不出来」⇒ **不得**判不可判。
    """
    db = tmp_path / "novelos.db"
    _seed_clean_db(db)
    secrets = tmp_path / "secrets.json"
    if raw_text is None:
        assert not secrets.exists(), "前提：文件不得已存在"
    else:
        secrets.write_text(raw_text, encoding="utf-8")

    state = mod.collect_secrets_state(db, secrets)
    assert state.read_errors == [], (
        f"{shape_id}：合法空状态不得记读错误：{state.read_errors}"
    )
    assert state.blocked_reasons == [], (
        f"{shape_id}：合法空状态不得判不可判：{state.blocked_reasons}"
    )

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    out = capsys.readouterr().out

    assert code == 0, f"{shape_id}：合法空状态 ⇒ 仍然合规（退出码 0），实际 {code}\n{out}"
    assert "合规" in out, out


# ---------------------------------------------------------------------------
# F-6（minor，2026-10-23 对抗审查 C）：``--json`` 丢字段 + ``action_count`` 口径错
#
# 缺陷形状：① ``payload["migration"]`` 段**丢了** ``conflicts`` 与 ``backup_path``
# （文本模式其实有打印 ⇒ 两种输出模式口径不一致）；② ``action_count`` 用的是
# **候选**动作数 ⇒ dry-run（实际写入 0 条）却报候选数 ⇒ 机器消费方会把 dry-run
# 读成「已写入 N 条」。
# ---------------------------------------------------------------------------


def test_json_migration_reports_conflicts_and_backup_path(mod, tmp_path, capsys) -> None:
    """``--json`` 的 migration 段必须带 ``conflicts`` 与 ``backup_path``（文本模式同口径）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-DB-OLDER")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_live01": "sk-FILE-NEWER"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply", "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0, payload
    migration = payload["migration"]
    assert migration["conflicts"], "冲突必须进 --json（文本模式已打印，机器模式不得丢）"
    assert any("mprof_live01" in c for c in migration["conflicts"]), migration["conflicts"]
    assert migration["backup_path"], "备份位置必须进 --json"
    assert Path(migration["backup_path"]).is_file(), "备份路径必须指向真实文件"


def test_json_action_count_is_zero_on_dry_run_and_candidate_count_is_n(
    mod, tmp_path, capsys
) -> None:
    """dry-run ⇒ ``action_count == 0``（实际写入 0 条）而 ``candidate_count == N``。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(
        db,
        profiles=[
            ("mprof_live01", "minimax", "sk-plain1"),
            ("mprof_live02", "minimax", "sk-plain2"),
        ],
    )
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {}}, ensure_ascii=False), encoding="utf-8")

    code = mod.main(
        ["secrets", "--db", str(db), "--secrets-file", str(secrets), "--migrate", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1, "库内明文还在 ⇒ 仍不合规（退出码 1）"
    migration = payload["migration"]
    assert migration["applied"] is False
    assert migration["action_count"] == 0, "dry-run 实际写入 0 条（不得报候选数）"
    assert migration["candidate_count"] == 2, "候选动作 2 条"
    assert migration["conflicts"] == []
    assert migration["backup_path"] is None, "dry-run 不写文件 ⇒ 不得有备份路径"


def test_json_action_count_equals_written_count_after_apply(mod, tmp_path, capsys) -> None:
    """``--apply`` 后 ``action_count`` = **实际写入**条数（不再是候选数）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(
        db,
        profiles=[
            ("mprof_live01", "minimax", "sk-plain1"),
            ("mprof_live02", "minimax", "sk-plain2"),
        ],
    )
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"api_keys": {}}, ensure_ascii=False), encoding="utf-8")

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--migrate", "--apply", "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0, payload
    migration = payload["migration"]
    assert migration["applied"] is True
    assert migration["action_count"] == 2, "实际写入 2 条"
    assert migration["candidate_count"] == 2
    assert migration["backup_path"], "落盘前必须有备份"

    # 断言打在**真实落盘内容**上（不只是 payload 里的数字）
    written = json.loads(secrets.read_text(encoding="utf-8"))
    assert written["api_keys"]["mprof_live01"] == "sk-plain1"
    assert written["api_keys"]["mprof_live02"] == "sk-plain2"


def test_json_prune_action_count_is_zero_on_dry_run_and_n_after_apply(
    mod, tmp_path, capsys
) -> None:
    """``prune`` 段同样：dry-run ⇒ ``action_count == 0``；``--apply`` 后 = 实际删除条数。

    ⚠️ ``--force-all-dangling``（2026-10-23 交集闸门）：本夹具的 secrets 文件里
    **只有一个死键** ⇒ 与已知集合**零交集** ⇒ 闸门默认拒绝（退出码 2）
    ⇒ 本用例显式声明「我知道这个库是对的」。
    """
    db = tmp_path / "novelos.db"
    _seed_clean_db(db)
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_dead000000": "sk-x"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--json", "--force-all-dangling",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    prune = payload["prune"]

    assert code == 1, "悬空条目未清理 ⇒ 仍不合规（退出码 1）"
    assert prune["applied"] is False
    assert prune["action_count"] == 0, "dry-run 实际删除 0 条"
    assert prune["candidate_count"] == 1, "候选删除 1 条"
    assert prune["removed"] == []

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply", "--json", "--force-all-dangling",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    prune = payload["prune"]

    assert code == 0, payload
    assert prune["applied"] is True
    assert prune["action_count"] == 1, "实际删除 1 条"
    assert prune["candidate_count"] == 1
    assert prune["removed"] == ["mprof_dead000000"]
    assert prune["backup_path"], "清理前必须有备份"
    assert prune["conflicts"] == []


# ---------------------------------------------------------------------------
# 交集闸门（2026-10-23 Lead 自踩事故暴露的 critical）
#
# ⚠️ **事故经过**：Lead 跑 F-3 复验探针时，`--db` 指向了**合成夹具库**、而
#    secrets 文件是**仓库真实的那一份**（探针忘了传 `--secrets-file`）⇒
#    夹具库的已知集合（mprof_x / conn_x）与真实 secrets 的 4 个键**一个都对不上**
#    ⇒ 4 个键**全部被判悬空** ⇒ `--prune-dangling --apply` **一次清空整个凭据库**，
#    而退出码仍是 0（报「合规」）⇒ 靠**写前备份**才救回来。
#
# ⚠️ **根因**：上一轮的 F-1 修复只挡住了「已知集合**为空**」与「读表失败」两种形态，
#    **漏了第三种**：「已知集合**非空**但与 secrets 的键**零交集**」。
#    ⇒ 「已知集合非空」**不等于**「这个库属于这个 secrets 文件」。
#
# ⇒ 修法：**交集闸门** —— secrets 的键与已知集合**零交集** ⇒ 悬空条目
#    **不可判**（**不是**「全悬空」）⇒ 巡检 exit 2、清理**拒绝执行**（零落盘）。
#    ⚠️ `--migrate` **不受**它拦截：迁移是**加法**动作（不删任何东西）、
#    且它不依赖「悬空」判断 ⇒ 一条手写的 secrets 文件不该把迁移也堵死。
# ---------------------------------------------------------------------------


def test_patrol_with_disjoint_known_set_is_unknown_not_compliant(mod, tmp_path, capsys) -> None:
    """secrets 的键与运行库已知集合**零交集** ⇒ 不可判 ⇒ 退出码 2（**不得**报合规）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_other_project", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_aaaaaaaaaa": "sk-x", "mconn_bbbbbbbbbb": "sk-y"}}),
        encoding="utf-8",
    )

    code = mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets)])
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"零交集 ⇒ 不可判 ⇒ 退出码 2，实际 {code}\n{out}"
    assert "不可判" in out, f"必须说清为什么不可判：{out}"
    assert "合规（无悬空条目" not in out, "零交集时**不得**报「合规」"


def test_prune_dangling_refuses_when_known_set_is_disjoint(mod, tmp_path, capsys) -> None:
    """零交集 ⇒ `--prune-dangling --apply` **拒绝执行**（文件逐字节不变、零备份）。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_other_project", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    original = json.dumps({"api_keys": {"mprof_aaaaaaaaaa": "sk-x"}}, ensure_ascii=False)
    secrets.write_text(original, encoding="utf-8")
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"零交集 ⇒ 必须拒绝执行（退出码 2），实际 {code}\n{out}"
    assert "已中止清理" in out, f"必须**明确拒绝**（而不是「没什么可删」）：{out}"
    assert secrets.read_bytes() == before, "secrets.json 必须**逐字节未改**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"


def test_prune_dangling_refuses_when_db_is_a_different_project(mod, tmp_path, capsys) -> None:
    """**事故复现**：``--db`` 指向别的项目的库 ⇒ 清理**必须**被拒（而不是清空凭据库）。

    ⚠️ 这是 2026-10-23 Lead 自踩事故的**直接回归**：事故里 4 个真实凭据被一次
    prune 清空（靠写前备份救回）⇒ 本条钉住「指错库 ⇒ 一个键都不许删」。
    """
    db = tmp_path / "other_project.db"
    _seed_model_db(db, profiles=[("mprof_x", "openai", None)])
    secrets = tmp_path / "secrets.json"
    original = json.dumps(
        {"api_keys": {"mprof_bae369541c72": "sk-real-1", "mconn_0e1e79327d9c": "sk-real-2"}},
        ensure_ascii=False,
    )
    secrets.write_text(original, encoding="utf-8")
    before = secrets.read_bytes()

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 2, f"指错库 ⇒ 必须拒绝执行（退出码 2），实际 {code}\n{out}"
    assert secrets.read_bytes() == before, "真实凭据**一个都不许删**"
    assert not list(tmp_path.glob("secrets.json.bak-*")), "拒绝执行 ⇒ 不得产生备份文件"


def test_partial_overlap_still_judges_dangling(mod, tmp_path, capsys) -> None:
    """**部分**对得上 ⇒ 照常判悬空（反向不变量：防「交集闸门」把正常清理也堵死）。

    ⚠️ 闸门只在**零交集**时拦；只要有一个键对得上 ⇒ 正常判定 + 正常清理。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_live01": "sk-live", "mprof_dead000000": "sk-dead"}}),
        encoding="utf-8",
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 0, f"部分对得上 ⇒ 正常清理 ⇒ 退出码 0，实际 {code}\n{out}"
    data = json.loads(secrets.read_text(encoding="utf-8"))
    assert sorted(data["api_keys"]) == ["mprof_live01"], "只该删死键，活键必须留下"


def test_force_all_dangling_allows_cleaning_when_known_set_is_disjoint(
    mod, tmp_path, capsys
) -> None:
    """显式 ``--force-all-dangling`` ⇒ 零交集时**放行**（用户已确认 --db 是对的）。

    ⚠️ 这条钉住「闸门有出口」：**拒绝**必须是**可解除**的，否则「secrets 里
    只有死键」这种合法场景就永远清不掉。
    """
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_other_project", "minimax", None)])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"mprof_aaaaaaaaaa": "sk-x"}}), encoding="utf-8"
    )

    code = mod.main(
        [
            "secrets", "--db", str(db), "--secrets-file", str(secrets),
            "--prune-dangling", "--apply", "--force-all-dangling",
        ]
    )
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 0, f"显式确认后应放行（退出码 0），实际 {code}\n{out}"
    data = json.loads(secrets.read_text(encoding="utf-8"))
    assert data["api_keys"] == {}, "确认后照常清理"
