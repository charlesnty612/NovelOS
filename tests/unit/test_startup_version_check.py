"""启动版本检查（2026-10-16 批次十一 · Lead 自办）的判别用例。

## 为什么要对它做突变验证

这是**新增功能**，且它的失败模式是「静默说错话」：它自己不崩、不影响功能，
只是给用户一句错误的行动建议。所以判别面必须覆盖「**该说而没说**」与
「**不该说却说了**」两个方向。

## 本组最重要的一条

``test_unresolvable_previous_commit_is_not_reported_as_no_rebuild``：
旧 commit 已不在本地历史里时，``git diff old..new`` 失败 ⇒ ``frontend_changed``
是 ``None``（**判不出来**）。此时**绝不能**输出「前端源码未变，无需重建 dist」
——那会让用户跳过唯一真正需要的动作。

⚠️ 这条不是假想：Lead 写第一版时恰好把 ``None`` 变成了 ``True``，
是**自查**发现的（不是测试抓的），所以这里补上判别把它钉住。
同族纪律见 AGENTS 坑区「量具没量到目标却报告成量过了」。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from packages.core.api.version_check import (
    STATE_FILENAME,
    check_for_update,
    current_head,
)

# ---------------------------------------------------------------- 夹具：真 git 仓库
# 用真 git（不 mock 子进程）——本模块的整个价值就在 git 输出的解析上，
# mock 掉 git 等于把被测物换成我们自己写的假实现。


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(repo: Path, paths: tuple[str, ...], message: str) -> str:
    for rel in paths:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"content of {rel} @ {message}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _text(notice) -> str:
    return "\n".join(notice.as_log_lines())


# ---------------------------------------------------------------------------
# 基本语义
# ---------------------------------------------------------------------------


def test_first_run_records_head_without_noticing(repo: Path, data_dir: Path):
    """首次启动**静默**记录当前 commit——那时没有「更新」可言。"""
    head = _commit(repo, ("README.md",), "init")

    assert check_for_update(repo, data_dir) is None

    assert (data_dir / STATE_FILENAME).read_text(encoding="utf-8").strip() == head


def test_same_head_second_time_is_silent(repo: Path, data_dir: Path):
    """没更新就不出声（否则每次重启都刷一遍，没法看）。"""
    _commit(repo, ("README.md",), "init")
    assert check_for_update(repo, data_dir) is None

    assert check_for_update(repo, data_dir) is None


def test_commit_change_is_reported(repo: Path, data_dir: Path):
    """只改后端：报「代码已更新」，且**明说不用重建前端**。"""
    first = _commit(repo, ("packages/core/x.py",), "c1")
    assert check_for_update(repo, data_dir) is None  # 记录 first

    second = _commit(repo, ("packages/core/y.py",), "c2")
    notice = check_for_update(repo, data_dir)

    assert notice is not None
    assert notice.previous == first
    assert notice.current == second
    assert notice.frontend_changed is False
    assert "无需重建" in _text(notice)


# ---------------------------------------------------------------------------
# 核心判别：该提示时提示
# ---------------------------------------------------------------------------


def test_frontend_change_produces_rebuild_instruction(repo: Path, data_dir: Path):
    """改了前端源码 ⇒ 必须给出**可执行**的重建命令。

    这条守的正是 ``dist/`` 不入库那个陷阱：新后端 + 旧界面且不报错。
    """
    _commit(repo, ("README.md",), "c1")
    assert check_for_update(repo, data_dir) is None

    _commit(repo, ("apps/web/src/App.tsx",), "c2 frontend")
    notice = check_for_update(repo, data_dir)

    assert notice is not None
    assert notice.frontend_changed is True
    text = _text(notice)
    # 断言的是**可复制粘贴的命令**而不是某个路径字面量：消息的交付物是
    # 「用户能照抄的一行」，钉路径字面量既脆又没测到真正的契约。
    assert "cd apps/web && npm install && npm run build" in text
    assert "新后端 + 旧界面" in text, "必须点明「不重建不报错」——否则用户看不出轻重"


def test_dependency_change_produces_sync_instruction(repo: Path, data_dir: Path):
    """改了依赖清单 ⇒ 提示 ``uv sync --extra dev``（**裸 uv sync 会剪掉 dev 工具**）。"""
    _commit(repo, ("README.md",), "c1")
    assert check_for_update(repo, data_dir) is None

    _commit(repo, ("pyproject.toml",), "c2 deps")
    notice = check_for_update(repo, data_dir)

    assert notice is not None
    assert notice.deps_changed is True
    text = _text(notice)
    assert "uv sync --extra dev" in text
    assert "不是裸 uv sync" in text


# ---------------------------------------------------------------------------
# 核心判别：**未知**不得被说成「不用做」
# ---------------------------------------------------------------------------


def test_unresolvable_previous_commit_is_not_reported_as_no_rebuild(
    repo: Path, data_dir: Path
):
    """⚠️ 本组最重要的一条。

    旧 commit 已不在本地历史里（rebase / squash / 换分支 / 重新克隆）
    ⇒ ``git diff old..new`` 失败 ⇒ ``frontend_changed`` 判不出来。

    此时输出里**绝不能**出现「前端源码未变，无需重建 dist」——
    那等于把「不知道」说成「不用做」，用户会因此跳过唯一真正需要的动作。
    """
    _commit(repo, ("README.md",), "c1")
    assert check_for_update(repo, data_dir) is None

    # 手工写一个本地不存在的 commit id（模拟 rebase 掉旧历史）
    (data_dir / STATE_FILENAME).write_text("0" * 40 + "\n", encoding="utf-8")
    _commit(repo, ("apps/web/src/App.tsx",), "c2")

    notice = check_for_update(repo, data_dir)

    assert notice is not None
    assert notice.frontend_changed is None, "判不出来时必须保留 None，不能塌成布尔"
    text = _text(notice)
    assert "无需重建" not in text, (
        "把「判不出来」说成「无需重建」——这是本仓登记的「量具没量到目标却报告成量过了」"
    )
    assert "未能判定" in text
    # ⚠️ 依赖侧必须**同样**保持「未知」。只钉前端的话，把 deps 的 None 塌成
    # False 会让这一段整句消失而上面两条断言照样绿——两条维度同形，判别也得同形。
    assert notice.deps_changed is None
    assert "依赖清单变化未能判定" in text


# ---------------------------------------------------------------------------
# 降级：绝不阻断启动
# ---------------------------------------------------------------------------


def test_non_git_directory_is_silent_and_does_not_raise(tmp_path: Path, data_dir: Path):
    """不是 git 仓库（zip 下载安装）⇒ 返回 None，**不抛**。

    诊断代码不能成为故障源——这一条被删掉的话，服务在 zip 安装下起不来。
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    assert current_head(plain) is None
    assert check_for_update(plain, data_dir) is None


def test_git_missing_from_path_is_silent(tmp_path: Path, data_dir: Path, monkeypatch):
    """PATH 里没有 git ⇒ 降级为「没检查」，不抛。"""

    repo = _make_repo(tmp_path)
    _commit(repo, ("README.md",), "c1")
    assert check_for_update(repo, data_dir) is None
    _commit(repo, ("README.md",), "c2")

    monkeypatch.setattr("packages.core.api.version_check.subprocess.run", _boom)

    assert check_for_update(repo, data_dir) is None


def _boom(*a, **k):
    raise FileNotFoundError("git 不在 PATH 里")


def test_state_file_unreadable_is_not_fatal(repo: Path, data_dir: Path):
    """状态文件是垃圾内容 ⇒ 当作「首次运行」重新记录，不抛。"""
    _commit(repo, ("README.md",), "c1")
    (data_dir / STATE_FILENAME).write_bytes(b"\xff\xfe\x00 not utf-8")

    assert check_for_update(repo, data_dir) is None
    assert (data_dir / STATE_FILENAME).read_text(encoding="utf-8").strip()
