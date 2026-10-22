"""``scripts/migrate_backups_to_sync.py`` 的 **import 期副作用回归看守**（dev-b 批次二十）。

背景
----
这个脚本带**不可逆动作**（openssl 加密 + 删源备份）⇒ 越不可测越危险。
而它此前**根本不可测**：模块级调了 ``sys.stdout.reconfigure(...)`` ⇒
pytest 捕获期的 ``sys.stdout`` 没有 ``reconfigure`` 方法 ⇒ **任何 import 它的测试
都在收集期就崩**（AGENTS 坑区「模块级 ``sys.stdout.reconfigure(...)`` ⇒ pytest 里
import 就炸」）。

2026-10-22 批次二十已把重配**挪进 ``main()``**（CLI 行为不变：作为脚本运行时
仍在第一条 print 之前完成 UTF-8 重配）。本文件是那次修改的**回归看守**。

⚠️ **本文件绝不触发任何不可逆动作**：只 import 模块 + 调两个纯函数
（``human`` / ``dir_size``），**不碰** ``ensure_key`` / ``encrypt_file`` /
``encrypt_dir`` / ``main``（它们会写密钥文件、加密并删除真实备份）。

⚠️ **这类回归天然表现为「响亮失败」而不是「断言红」**：一旦有人把重配挪回模块级，
本文件里的 import 会当场 ``AttributeError``（响亮）⇒ **不需要**为它硬造一条
``[CAUGHT]`` 突变（且那种突变会表现为收集期 ERROR，与「断言红」不同形）。
本文件另配一条**结构断言**（AST）给出更直白的诊断：重配必须在 ``main()`` 里、
不得出现在模块顶层。

零真实 LLM、零内容仓接触、零 dev 库、**零不可逆操作**。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "migrate_backups_to_sync.py"


def _reconfigure_calls(nodes: list[ast.stmt]) -> list[ast.Call]:
    """在给定的语句列表里找 ``*.reconfigure(...)`` 调用（不进入函数/类体）。"""
    out: list[ast.Call] = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "reconfigure"
            ):
                out.append(sub)
    return out


def test_importing_the_module_does_not_touch_stdout():
    """import 该模块**不得**替换/重配 ``sys.stdout``。

    ⚠️ 这条在「重配被挪回模块级」时会**当场 AttributeError**（pytest 捕获期的
    ``sys.stdout`` 没有 ``reconfigure``）⇒ 响亮失败，而不是静默通过。
    """
    before_stream = sys.stdout
    before_encoding = getattr(sys.stdout, "encoding", None)

    import scripts.migrate_backups_to_sync  # noqa: F401 —— 本用例就是要 import 它

    assert sys.stdout is before_stream, "import 不得替换 sys.stdout"
    assert getattr(sys.stdout, "encoding", None) == before_encoding, (
        "import 不得重配 sys.stdout 的编码（模块级 reconfigure 的原症状）"
    )


def test_reconfigure_lives_inside_main_not_at_module_level():
    """结构断言：重配**在** ``main()`` 里，**不在**模块顶层。

    这条给出比「import 崩了」更直白的诊断：谁把重配挪回了模块级，一眼可见。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    module_level = _reconfigure_calls(tree.body)
    assert not module_level, (
        "模块顶层不得出现 *.reconfigure(...)——它让 import 期就崩（pytest 捕获期"
        "的 sys.stdout 没有该方法）⇒ 该脚本从此不可测"
    )

    main_fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    assert main_fn is not None, "main() 必须还在"
    inside = [
        c
        for c in ast.walk(main_fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "reconfigure"
    ]
    assert inside, "main() 入口必须做 UTF-8 重配（CLI 行为不变）"


def test_pure_helpers_are_now_importable_and_correct(tmp_path):
    """两个纯函数可测（此前因 import 期崩溃而完全测不了）。"""
    from scripts.migrate_backups_to_sync import dir_size, human

    assert human(1024) == "1.0 KB"
    assert human(3 * 1024 * 1024) == "3.0 MB"

    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 17)
    assert dir_size(f) == 17
    assert dir_size(tmp_path) == 17
