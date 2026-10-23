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
    """``--apply`` 不得丢掉 secrets.json 里已有的其它条目。"""
    db = tmp_path / "novelos.db"
    _seed_model_db(db, profiles=[("mprof_live01", "minimax", "sk-plain")])
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"api_keys": {"minimax": "sk-existing"}, "other": "keep"},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    mod.main(["secrets", "--db", str(db), "--secrets-file", str(secrets), "--apply"])
    capsys.readouterr()

    payload = json.loads(secrets.read_text(encoding="utf-8"))
    assert payload["api_keys"]["minimax"] == "sk-existing"
    assert payload["other"] == "keep"
    assert payload["api_keys"]["mprof_live01"] == "sk-plain"
