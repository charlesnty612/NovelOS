"""``scripts/tree_fingerprint.py`` 的口径看守（dev-b 批次二十）。

为什么给它补看守
----------------
这个脚本**定义着「闸门口径」本身**（跑测前后各取一次，逐位一致才算数），
却是仓里**零测试**的脚本之一（2026-10-22 批次十九实测：31 个 scripts 里 8 个
零测试，``tree_fingerprint.py`` 就在其中）⇒ 口径被人改掉时，**没有任何东西会响**。

本文件钉的是**已知性质**（AGENTS.md 坑区已逐字登记），不是缺陷：

- 口径 = ``{相对路径}|{字节长度}|{mtime 整秒}``，按行字典序排序，换行连接，
  取 SHA256 —— **哈希的是「路径 + 字节数 + mtime」，不含文件内容**；
- 扫描根固定为 ``packages/ tests/ scripts/ database`` ⇒ **``docs/`` 与
  ``apps/web/`` 不在指纹内**（现网口径如此，AGENTS 亦载明「指纹口径不含
  ``apps/web``，前端批次的等价保证是 vitest 全量 + ``git status``」）；
- 后缀只收 ``.py`` / ``.sql``；``__pycache__`` 一律跳过。

另有一条**精确值**看守（2026-10-24 补）：合成树（固定 mtime + 逐字节确定的内容）
的指纹必须**逐位等于**硬编码的 sha256 ⇒ 钉住**行格式口径本身**
（三要素 / 分隔符 / 排序 / 无尾换行 / UTF-8）；口径改了而性质用例照旧绿的那个洞，
由它挡住。

⚠️ **这些断言钉的是「性质」，不是「对错」**：若将来把口径改成内容哈希
（AGENTS 登记过：需连带新增 ``.gitattributes`` 统一行尾，风险远大于收益，
故已裁决不改），本文件会**故意变红** ⇒ 那时必须同批更新 AGENTS 坑区、
CHANGELOG 与本文件，而不是把断言放宽。

⚠️ **零 mtime 噪音**：全部用 ``tmp_path`` 里的合成小文件树，**不碰仓库里的
真文件**（``touch`` 仓库文件会污染跑测前后的指纹对比）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.tree_fingerprint import (
    DEFAULT_ROOTS,
    EXCLUDED_DIRS,
    SUFFIXES,
    compute,
    iter_source_files,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tree_fingerprint.py"

#: 固定 mtime（秒）——让「同 mtime」在测试里是**可构造**的，而不是碰运气。
FIXED_MTIME = 1_700_000_000

#: 合成树（:func:`_tree` 造的那棵）的**精确**指纹值。
#:
#: ⚠️ **这个哈希钉住的是「行格式口径」**：``{rel}|{size}|{int(mtime)}`` 三要素、
#: 按行字典序排序、``"\n"`` 连接且**无尾换行**、UTF-8 编码后取 SHA256。
#: 改其中任何一环（分隔符、多一个要素、排序键、尾换行、编码）⇒ 本用例必红。
#:
#: ⚠️ **历史哈希作废的规矩照旧**（AGENTS 坑区「闸门前取树指纹」条）：脚本 docstring
#: 明写「口径改动 = 历史哈希全部作废，须在 CHANGELOG 登记」⇒ 改口径时必须同批
#: 更新 AGENTS 坑区、CHANGELOG 与本常量，**不是把断言放宽**。
#:
#: ⚠️ **为什么合成树的文件必须用 ``write_bytes`` 写**（平台无关）：
#: ``write_text`` 在 Windows 上会把 ``\n`` 翻译成 ``\r\n``（``open(newline=None)``
#: 的写入侧换行翻译）⇒ 同样的源码在 Linux 与 Windows 上**字节数不同**
#: ⇒ 哈希不同 ⇒ 硬编码的期望值只在写它的那个平台上成立（CI 跑 ubuntu-latest ⇒ 必红）。
#: ⇒ 用 ``write_bytes`` 写**逐字节确定**的内容，期望值才跨平台成立。
EXPECTED_SYNTHETIC_TREE_SHA256 = "328a9dd67bf6dc2ae4d9e11660813ddcf09a7d3a5cb42f756780c1b23228bba8"


def _write(path: Path, content: str, *, mtime: int = FIXED_MTIME) -> Path:
    """写文件并把 mtime 钉成确定值（内容不变、mtime 可控）。

    ⚠️ 用 ``write_bytes``（**不是** ``write_text``）⇒ 内容**逐字节**确定
    ⇒ 跨平台（Windows 的 ``\\r\\n`` 翻译会被绕开）⇒ 见
    :data:`EXPECTED_SYNTHETIC_TREE_SHA256` 的说明。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    os.utime(path, (mtime, mtime))
    return path


def _tree(tmp_path: Path) -> Path:
    """造一棵覆盖全部四个根 + 两个范围外目录的合成树。"""
    base = tmp_path / "repo"
    _write(base / "packages" / "core" / "a.py", "x = 1\n")
    _write(base / "tests" / "unit" / "b.py", "y = 2\n")
    _write(base / "scripts" / "c.py", "z = 3\n")
    _write(base / "database" / "migrations" / "0001_init.sql", "CREATE TABLE t(id INT);\n")
    # 范围外（现网口径不含）
    _write(base / "docs" / "readme.md", "doc\n")
    _write(base / "apps" / "web" / "src" / "App.tsx", "export const A = 1;\n")
    return base


def _digest(base: Path) -> str:
    return compute(DEFAULT_ROOTS, base=base)["sha256"]


# ---------------------------------------------------------------------------
# 1）mtime 是哈希的输入（内容不变也变）
# ---------------------------------------------------------------------------


def test_touching_a_file_without_changing_content_changes_the_fingerprint(tmp_path):
    """**只改 mtime、内容一字未动** ⇒ 指纹变化。

    钉的是**已知性质**：mtime 是哈希的输入之一，且哈希**不含内容**——
    若哈希只含内容，``touch`` 不可能让它变。
    """
    base = _tree(tmp_path)
    before = _digest(base)

    target = base / "packages" / "core" / "a.py"
    os.utime(target, (FIXED_MTIME + 100, FIXED_MTIME + 100))

    after = _digest(base)
    assert after != before, "只改 mtime 必须改变指纹（mtime 是哈希输入）"
    # 内容确实一字未动——否则这条测试证明的就不是「不含内容」
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_restoring_the_exact_mtime_restores_the_fingerprint(tmp_path):
    """用 ``os.utime`` 精确还原 mtime ⇒ 指纹回到原值（**可逆**）。

    这条钉住「指纹是 mtime 的纯函数、不是单向的」⇒ 也就是它**可被复核**：
    同一棵树、同一口径 ⇒ 同一串哈希（AGENTS 坑区「没有实现的纪律不是闸门，
    是仪式」）。
    """
    base = _tree(tmp_path)
    before = _digest(base)

    target = base / "packages" / "core" / "a.py"
    os.utime(target, (FIXED_MTIME + 100, FIXED_MTIME + 100))
    assert _digest(base) != before

    os.utime(target, (FIXED_MTIME, FIXED_MTIME))
    assert _digest(base) == before, "精确还原 mtime 后指纹必须回到原值"


def test_content_is_not_part_of_the_hash_when_size_and_mtime_match(tmp_path):
    """**同字节数 + 同 mtime + 不同内容** ⇒ 指纹**不变**。

    钉的是**已知性质（也是已知局限）**：口径是「路径 + 字节数 + mtime」，
    **不含内容** ⇒ 等长改写不会被指纹看见。

    ⚠️ 登记依据：AGENTS 坑区「指纹口径不含文件内容」条（并载明「改成内容哈希
    需连带新增 ``.gitattributes`` 统一行尾，会让全部文件大面积改写 ⇒ 已裁决
    不改」）。**若将来口径改成内容哈希，本用例必须同批更新**，而不是被放宽。
    """
    base = _tree(tmp_path)
    target = base / "packages" / "core" / "a.py"
    before = _digest(base)
    size_before = target.stat().st_size

    target.write_bytes(b"x = 9\n")  # 与 "x = 1\n" 等长
    os.utime(target, (FIXED_MTIME, FIXED_MTIME))

    assert target.stat().st_size == size_before, "本用例的前提是**等长**改写"
    assert target.read_text(encoding="utf-8") != "x = 1\n", "内容必须真的变了"
    assert _digest(base) == before, (
        "等长 + 同 mtime 的内容改写**不该**改变指纹——口径里没有内容这一维"
    )


# ---------------------------------------------------------------------------
# 2）扫描范围（哪些目录在内 / 不在内）
# ---------------------------------------------------------------------------


def test_scope_is_exactly_the_four_declared_roots(tmp_path):
    """四个根在内；``docs/`` 与 ``apps/web/`` **不在**内（现网口径）。"""
    base = _tree(tmp_path)
    before = _digest(base)

    _write(base / "docs" / "other.md", "more docs\n")
    _write(base / "apps" / "web" / "src" / "Other.tsx", "export const B = 2;\n")
    assert _digest(base) == before, "docs/ 与 apps/web/ 不得进入指纹"

    _write(base / "packages" / "core" / "d.py", "w = 4\n")
    assert _digest(base) != before, "packages/ 下的新文件必须进入指纹"


def test_default_roots_are_the_four_declared_ones():
    """默认扫描根固定为 ``packages/ tests/ scripts/ database``。

    改这里会让**所有历史哈希作废**（脚本 docstring 明载）⇒ 属于须在 CHANGELOG
    登记的口径变更 ⇒ 必须由一条测试挡住「顺手加一个根」。
    """
    assert tuple(DEFAULT_ROOTS) == ("packages", "tests", "scripts", "database")


def test_only_py_and_sql_suffixes_are_counted(tmp_path):
    """只收 ``.py`` / ``.sql``；``.md`` / ``.ts`` / ``.tsx`` 不进指纹。"""
    base = _tree(tmp_path)
    before = _digest(base)

    _write(base / "packages" / "core" / "notes.md", "not code\n")
    _write(base / "packages" / "core" / "types.ts", "export const C = 3;\n")
    assert _digest(base) == before, "非 .py/.sql 后缀不得进入指纹"

    _write(base / "packages" / "core" / "e.sql", "CREATE TABLE u(id INT);\n")
    assert _digest(base) != before, ".sql 必须进入指纹"
    assert set(SUFFIXES) == {".py", ".sql"}


def test_pycache_directories_are_excluded(tmp_path):
    """``__pycache__`` 一律跳过（编译产物不得进指纹）。

    ⚠️ **探针形态（V3-2）**：放进去的是 ``__pycache__/x.py``（**``.py`` 后缀**）
    ⇒ 它**能过**后缀过滤 ⇒ 只有「目录排除」这条规则能挡住它。
    （原先放的是 ``a.cpython-312.pyc`` ⇒ 后缀过滤**自己**就挡住了 ⇒
    即便 ``EXCLUDED_DIRS`` 被清空，本用例**照样绿** ⇒ 它守的不是「目录排除」。）
    """
    base = _tree(tmp_path)
    before = _digest(base)

    _write(base / "packages" / "core" / "__pycache__" / "x.py", "bytecode\n")
    assert _digest(base) == before, "__pycache__ 不得进入指纹"
    assert "__pycache__" in EXCLUDED_DIRS


def test_synthetic_tree_has_the_exact_expected_sha256(tmp_path):
    """合成树的指纹必须**逐位等于**硬编码期望值（钉住「行格式口径」）。

    为什么需要这条（V3-1：行格式无钉子）
    -------------------------------------
    上面几条用例钉的都是**性质**（mtime 是输入 / 等长改写看不见 / 目录被排除 /
    后缀被过滤），**没有一条钉住「行格式本身」** ⇒ 把
    ``f"{rel}|{st.st_size}|{int(st.st_mtime)}"`` 改成别的形态（换分隔符、去掉
    mtime 那一维、排序键换成 ``str`` 默认排序、连接后多一个尾换行、编码改成
    latin-1……）⇒ 那些**性质**用例**全部照旧绿**（它们只看「变没变」），
    而闸门口径已经悄悄变了 ⇒ **历史哈希全部作废却无人知晓**。

    ⇒ 本条断言**精确的 sha256 值** ⇒ 口径任何一环改动 ⇒ 本条必红。

    ⚠️ **口径变更的处置规矩不变**（脚本 docstring + AGENTS 坑区）：真要改口径，
    必须同批更新 AGENTS 坑区、CHANGELOG 与 :data:`EXPECTED_SYNTHETIC_TREE_SHA256`
    ——**不是把本断言放宽**。
    """
    base = _tree(tmp_path)
    result = compute(DEFAULT_ROOTS, base=base)
    assert result["count"] == 4, f"合成树的文件数不对：{result}"
    assert result["sha256"] == EXPECTED_SYNTHETIC_TREE_SHA256, (
        "合成树的指纹与硬编码期望值**不一致** ⇒ 行的格式口径变了"
        "（三要素 / 分隔符 / 排序 / 尾换行 / 编码 任一环被改动）⇒ "
        "按 AGENTS 规矩，历史哈希已作废，须在 CHANGELOG 登记并同批更新本常量；"
        f"实测 {result['sha256']}，期望 {EXPECTED_SYNTHETIC_TREE_SHA256}"
    )


def test_missing_root_is_skipped_instead_of_crashing(tmp_path):
    """不存在的根被跳过（打 stderr），不抛异常、其余根照常计入。"""
    base = _tree(tmp_path)
    result = compute(("packages", "does_not_exist"), base=base)
    assert result["count"] == 1, "只有 packages/ 下的 1 个文件"
    assert len(result["sha256"]) == 64


def test_count_matches_the_number_of_included_files(tmp_path):
    """``count`` = 纳入指纹的文件数（与 ``iter_source_files`` 的条数一致）。"""
    base = _tree(tmp_path)
    files = iter_source_files(DEFAULT_ROOTS, base)
    result = compute(DEFAULT_ROOTS, base=base)
    assert result["count"] == len(files) == 4, "4 个根各 1 个文件"
    rels = sorted(p.relative_to(base).as_posix() for p in files)
    assert rels == [
        "database/migrations/0001_init.sql",
        "packages/core/a.py",
        "scripts/c.py",
        "tests/unit/b.py",
    ]


# ---------------------------------------------------------------------------
# 3）CLI 输出契约（只读：只 stat，不写任何字节）
# ---------------------------------------------------------------------------


def test_cli_prints_count_then_sha256(tmp_path):
    """CLI 打印 ``"<文件数> <64 位十六进制>"``（闸门脚本靠这一行做前后比对）。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    import json

    data = json.loads(proc.stdout)
    assert data["count"] > 0
    assert len(data["sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in data["sha256"])
    assert tuple(data["roots"]) == tuple(DEFAULT_ROOTS)
