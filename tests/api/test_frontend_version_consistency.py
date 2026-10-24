"""web 三处版本副本的机器看守（2026-10-23 task-4 / D-16）。

背景
----
发版纪律（AGENTS.md「版本纪律」）要求 **六处** 版本同步：

1. ``pyproject.toml`` 的 ``[project].version``（**单一源**）
2. ``apps/web/package.json`` 的 ``version``
3. ``apps/web/package-lock.json`` 的 ``version`` + ``packages[""].version``
4. ``uv.lock`` 里 ``name = "novelos"`` 段的 ``version``
5. ``.claude-plugin/plugin.json`` 的 ``metadata.novelosVersion``
6. ``skills/novelos/SKILL.md`` 的 ``metadata.novelosVersion``

2026-10-23 实测：``check_skill_package.py`` 只校 **3 处**（1 / 5 / 6）⇒
**web 三处（2 / 3 / 4）零看守**——任一处漏同步，没有任何机器拦它。
本文件把 web 三处钉住。

判别方式
--------
把 ``pyproject.toml``（单一源）改成**假版本** ⇒ **web 三处的检查必须同时报红**；
恢复后必须**同时报绿**。

另有一条**覆盖面**判别（V4-1）：``check_app_version_consistency`` 产出的**每一条**
检查都必须已被本文件显式登记（``WEB_CHECK_IDS`` ∪ ``NON_WEB_CHECK_IDS``）
⇒ 检查器新增第 4 处 web 版本副本时**必红**（原先按手挑的 3 个 id 过滤 ⇒ 被静默吞掉）。

⚠️ **不动仓库里的真文件**：变更只发生在 ``tmp_path`` 里的**副本**上
（并发跑时真 pyproject 随时可能被别的实现者改）。
⚠️ **断言打在实际读取到的值上**（版本号本身），不是「函数被调用」。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "novelos"
SCRIPT = SKILL_DIR / "scripts" / "check_skill_package.py"

#: web 三处版本副本对应的**检查 id**（与 ``check_skill_package.py`` 里一一对应）。
WEB_CHECK_IDS = (
    "app-version-consistency/web-package-json",
    "app-version-consistency/web-package-lock",
    "app-version-consistency/uv-lock",
)

#: 参与「六处版本一致」判定的文件（相对仓库根）。
_VERSION_FILES = (
    Path("pyproject.toml"),
    Path("apps/web") / "package.json",
    Path("apps/web") / "package-lock.json",
    Path("uv.lock"),
)

#: 假版本（刻意与仓库现值不同 ⇒ 任何「真的比了值」的检查都会红）。
FAKE_VERSION = "0.0.0-task4-fake"


def _load_checker():
    """把契约自检脚本当模块加载（``skills/novelos/scripts`` 不是包）。"""
    spec = importlib.util.spec_from_file_location("novelos_check_skill_package", SCRIPT)
    assert spec is not None and spec.loader is not None, f"无法加载 {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()

#: **非 web 侧**的 ``app-version-consistency/*`` 检查 id（skill 自身 + pyproject 交叉
#: 验证 + 各平台插件清单侧）。
#:
#: ⚠️ 平台侧的名字**从检查器自己的 ``PLUGIN_MANIFESTS`` 推导**（不手挑）⇒
#: 检查器新增一个平台清单时，这里自动跟着长；**新增一个「web 版本副本」检查
#: （例如 ``app-version-consistency/web-vite-config``）时不在本集合里** ⇒
#: 由 :func:`test_checker_produces_no_unclassified_version_check` 判红。
NON_WEB_CHECK_IDS = frozenset(
    {
        "app-version-consistency/skill",
        "app-version-consistency/pyproject",
    }
    | {f"app-version-consistency/{platform}" for platform in CHECKER.PLUGIN_MANIFESTS}
)


def _copy_version_files(dest: Path) -> Path:
    """把参与版本判定的文件**复制**到 ``dest``（真仓库文件一个字节都不动）。"""
    for rel in _VERSION_FILES:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    return dest


def _set_pyproject_version(repo_root: Path, version: str) -> None:
    """把副本 ``pyproject.toml`` 的 ``[project].version`` 改成 ``version``。

    只改 ``[project]`` 段里的那一个键（``[tool.*]`` 段的同名字段不许碰）。
    """
    path = repo_root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^(\[project\][^\[]*?^version\s*=\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{version}"',
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1, "副本 pyproject.toml 的 [project].version 定位失败（正则失效？）"
    path.write_text(new_text, encoding="utf-8")


def _web_checks(repo_root: Path) -> dict[str, object]:
    """跑契约自检，取出 **web 三处** 的检查结果（check_id → Check）。"""
    results = CHECKER.check_app_version_consistency(SKILL_DIR, repo_root)
    return {c.check_id: c for c in results if c.check_id in WEB_CHECK_IDS}


def test_real_repo_web_version_copies_match_pyproject() -> None:
    """仓库现状：web 三处的版本副本必须与 ``pyproject``（单一源）**逐字一致**。

    断言打在**实际读取到的版本号**上（不是「函数被调用」）。
    """
    app_version = CHECKER._read_pyproject_version(REPO_ROOT)
    assert app_version, "仓库根的 pyproject.toml 必须声明 [project].version"

    package_json = json.loads((REPO_ROOT / "apps/web/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (REPO_ROOT / "apps/web/package-lock.json").read_text(encoding="utf-8")
    )
    assert package_json["version"] == app_version, (
        f"apps/web/package.json version={package_json['version']!r} ≠ "
        f"pyproject [project].version={app_version!r}"
    )
    assert package_lock["version"] == app_version, (
        f"apps/web/package-lock.json version={package_lock['version']!r} ≠ "
        f"pyproject [project].version={app_version!r}"
    )
    assert package_lock["packages"][""]["version"] == app_version, (
        f"apps/web/package-lock.json packages[\"\"].version="
        f"{package_lock['packages']['']['version']!r} ≠ "
        f"pyproject [project].version={app_version!r}"
    )
    assert CHECKER._read_uvlock_version(REPO_ROOT) == app_version, (
        "uv.lock 里 name = \"novelos\" 段的 version ≠ pyproject [project].version="
        f"{app_version!r}"
    )

    results = _web_checks(REPO_ROOT)
    assert set(results) == set(WEB_CHECK_IDS), (
        f"web 三处的检查 id 不齐：实测 {sorted(results)}，期望 {sorted(WEB_CHECK_IDS)}"
    )
    for check_id, check in results.items():
        assert check.ok, f"{check_id} 报红：{check.message}"
        assert repr(app_version) in check.message, (
            f"{check_id} 的诊断里没有实测版本 {app_version!r}：{check.message}"
        )


def test_fake_pyproject_version_makes_every_web_check_red(tmp_path: Path) -> None:
    """把单一源（``pyproject``）改成假版本 ⇒ **web 三处的检查必须同时报红**。

    ⇒ 判别点：``check_skill_package.py`` 里那三条 web 检查**真的比了值**。
    撤掉其中任一条 ⇒ 本条红（该 check_id 不再出现 / 不再报红）。
    """
    repo = _copy_version_files(tmp_path / "repo")
    _set_pyproject_version(repo, FAKE_VERSION)

    results = _web_checks(repo)
    assert set(results) == set(WEB_CHECK_IDS), (
        f"web 三处的检查 id 不齐：实测 {sorted(results)}，期望 {sorted(WEB_CHECK_IDS)}"
    )
    for check_id, check in results.items():
        assert not check.ok, (
            f"{check_id} 在 pyproject 改成假版本 {FAKE_VERSION!r} 后**仍报绿**：{check.message}"
        )
        assert FAKE_VERSION in check.message, (
            f"{check_id} 的诊断里没有假版本 {FAKE_VERSION!r}：{check.message}"
        )


def test_restoring_pyproject_version_makes_every_web_check_green(tmp_path: Path) -> None:
    """恢复单一源 ⇒ **web 三处的检查必须同时报绿**（红→绿闭环，不是单向判红）。"""
    repo = _copy_version_files(tmp_path / "repo")
    original_version = CHECKER._read_pyproject_version(repo)
    assert original_version, "副本 pyproject.toml 必须声明 [project].version"

    _set_pyproject_version(repo, FAKE_VERSION)
    assert all(
        not check.ok for check in _web_checks(repo).values()
    ), "前提不成立：改成假版本后 web 三处没有全红"

    _set_pyproject_version(repo, original_version)
    results = _web_checks(repo)
    assert set(results) == set(WEB_CHECK_IDS), (
        f"web 三处的检查 id 不齐：实测 {sorted(results)}，期望 {sorted(WEB_CHECK_IDS)}"
    )
    for check_id, check in results.items():
        assert check.ok, f"{check_id} 在恢复后仍报红：{check.message}"
        assert repr(original_version) in check.message, (
            f"{check_id} 的诊断里没有恢复后的版本 {original_version!r}：{check.message}"
        )


def test_checker_produces_no_unclassified_version_consistency_check() -> None:
    """``check_app_version_consistency`` 产出的**每一条**检查都必须已被本文件分类。

    为什么需要这条（V4-1：手挑域 → 全集推导）
    -----------------------------------------
    本文件原先只按 ``WEB_CHECK_IDS``（**手写的 3 个 id**）过滤检查器的输出
    ⇒ 检查器**新增第 4 处 web 版本副本**时，它的 check_id 不在 ``WEB_CHECK_IDS`` 里
    ⇒ 被 :func:`_web_checks` 的过滤**静默吞掉** ⇒ 上面三条用例照旧全绿
    ⇒ 「新增了一处版本副本，却没有任何机器看守它」。

    ⇒ 本条断言「**全集 ⊆ 已知集合**」：检查器产出任何**未登记**的检查 ⇒ 必红。
    （登记 = 把 id 加进 ``WEB_CHECK_IDS`` 或 ``NON_WEB_CHECK_IDS``；两者都是
    **显式**登记，不是自动放行。）

    ⚠️ 这条**不是**「把 web 三处的断言重复一遍」：它守的是**覆盖面**——
    「检查器新增了检查 ⇒ 必须有人显式决定这条新检查要不要被守」。
    """
    results = CHECKER.check_app_version_consistency(SKILL_DIR, REPO_ROOT)
    produced = {c.check_id for c in results}
    allowed = set(WEB_CHECK_IDS) | set(NON_WEB_CHECK_IDS)

    unknown = sorted(produced - allowed)
    assert not unknown, (
        "check_app_version_consistency 产出了**未登记**的检查 id："
        f"{unknown}\n"
        "⇒ 它不属于 web 三处（WEB_CHECK_IDS），也不属于 skill/plugin 类"
        "（NON_WEB_CHECK_IDS）⇒ 本文件对它**零看守**。\n"
        "处置：把它登记进 WEB_CHECK_IDS / NON_WEB_CHECK_IDS（并补相应断言），"
        "或确认它不需要被守后显式豁免（写清理由）。"
    )

    # 反向：登记在案的 web 三处必须**真的被产出**（防「登记了却没实现」）。
    missing = sorted(set(WEB_CHECK_IDS) - produced)
    assert not missing, (
        f"WEB_CHECK_IDS 登记了 {missing}，但检查器**没有产出**它们 "
        "⇒ 登记与实现已分叉"
    )


def test_selftest_covers_the_web_version_checks() -> None:
    """脚本自己的 ``--selftest`` 必须**验** web 三处（否则 CI 上是「跑而不验」）。

    CI 的「Contract self-checks」那一步**只跑脚本自检** ⇒ 新看守若不进
    ``--selftest`` 的变异矩阵，CI 上就只跑不验（2026-10-23 对抗审查 A2 实测过这个缺口）。

    断言打在**自检的真实输出**上（每条变异的 ``预期检查 <id> 已报红`` 行）。
    """
    ok, log = CHECKER.run_selftest(SKILL_DIR, REPO_ROOT)
    for check_id in WEB_CHECK_IDS:
        hits = [line for line in log if f"预期检查 {check_id} 已报红" in line]
        assert hits, (
            f"--selftest 的变异矩阵里**没有**覆盖 {check_id} ⇒ 该看守在 CI 上是"
            f"「跑而不验」（自检输出：\n" + "\n".join(log) + "\n）"
        )
    assert ok, "--selftest 失败：\n" + "\n".join(log)
