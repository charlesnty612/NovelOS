"""思考档 8 档封闭集：前后端两份清单的机器看守（2026-10-24 审查遗留项）。

背景
----
「思考档」是 8 档封闭集（``default/off/on/adaptive/low/medium/high/max``），
仓里有**两份清单**：

- **后端唯一权威**：``packages/core/model_router/effective.py`` 的 ``THINKING_MODES``
- **前端共享单点**：``apps/web/src/pages/AiSettingsThinkingOptions.ts`` 的
  ``THINKING_OPTIONS``

两份清单此前**零机器校验** ⇒ 一侧增删档，另一侧静默漂移（后端加了档，前端
下拉里选不到；前端加了档，后端 ``apply_thinking_override`` 到写库时才抛
``ValueError``）。本文件用 ``scripts/check_thinking_modes_consistency.py``
把这条不变量钉住。

判别方式
--------
断言全部打在**真实退出码 + 真实输出**上（子进程跑脚本，不是「函数被调用」）：

- **A** 当前树一致 ⇒ exit 0，且 8 档名都在输出里（证明解析真的读到了集合，
  不是「解析不出来 ⇒ 恒绿」）；
- **B** 后端少一档 ⇒ exit 1，输出**点名**那一档；
- **C** 前端多一档 ⇒ exit 1，输出**点名**那一档；
- **D** 文件缺失 ⇒ exit 2（**既不是 0 也不是 1** ⇒ 不许把「读不到」当「一致」）；
- **E** 解析不出集合 ⇒ exit 2（同上：「解析失败当一致」是量具失真）；
- **F** 前端 value 大小写漂移 ⇒ exit 1，且输出点名「大小写 / 拼写漂移」；
- **G** **后端多一档**（前端没加）⇒ exit 1，输出点名「仅后端有」——
  这一方向**只有 set-equality 能判**（B/C 都被 frontend-spelling 旁路），
  缺了它就把「集合相等」这一项写成**跑而不验**；
- **H** 前端**注释形态的旧清单**劫持（真实声明前插注释）+ 真实数组多一档 ⇒
  **不许 exit 0**（旧实现会解析到注释里的旧清单 ⇒ 假绿）；
- **I** 后端**函数内**同名赋值顶替模块级常量 ⇒ exit 1（只认模块级）；
- **J** 后端**死分支**（``if False:``）同名赋值顶替模块级常量 ⇒ exit 1；
- **K** 合法 TS：数组内插**普通注释**（含未配对的 ``[`` 与 ``'``）⇒ **exit 0**
  （不许假红）。

⚠️ **不动仓库里的真文件**：所有变更只发生在 ``tmp_path`` 里的**副本**上
（并发跑时真文件随时可能被别的实现者改）。
⚠️ 子进程输出**两侧同时钉**：``env`` 里给 ``PYTHONIOENCODING`` / ``PYTHONUTF8``，
``subprocess.run`` 里给 ``encoding="utf-8"``（只给一边照样会被 cp936 中招——
AGENTS 坑区「子进程输出编码：父进程 text=True 缺 encoding=」）。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_thinking_modes_consistency.py"
BACKEND = REPO_ROOT / "packages" / "core" / "model_router" / "effective.py"
FRONTEND = REPO_ROOT / "apps" / "web" / "src" / "pages" / "AiSettingsThinkingOptions.ts"

#: 思考档 8 档封闭集（**逐个点名**，不是「集合相等」的等价写法——
#: 少一档时用例必须红，否则「封闭集」这个词就没有牙齿）。
EXPECTED_MODES = ("default", "off", "on", "adaptive", "low", "medium", "high", "max")

#: 用例 B 里从后端清单中删掉的那一档（选**末档** ``max``：删它不改动其余
#: 元素的相对位置 ⇒ 副本仍是合法 Python）。
DROPPED_MODE = "max"

#: 用例 C 里给前端清单加上的假档。
FAKE_MODE = "ultra"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """跑巡检脚本，返回带真实退出码与真实输出的结果。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )


def _module_level_modes_node(text: str) -> ast.Set | ast.List | ast.Tuple:
    """定位 ``effective.py`` 里**模块级** ``THINKING_MODES`` 的集合字面量节点。

    ⚠️ **独立于巡检脚本实现**（不复用它的解析函数）：夹具若依赖被测物的解析器，
    解析器坏了 ⇒ 夹具也跟着坏 ⇒ 用例可能因为「另一个原因」而红/绿
    （AGENTS 坑区「判别用例的夹具自带噪声 ⇒ 失败点漂移」）。
    ⚠️ 只看 **``tree.body`` 顶层**：函数体内 / ``if False:`` 死分支里的同名
    赋值**不是**模块级常量（那正是用例 I/J 要复现的劫持形状）。
    """
    tree = ast.parse(text)
    value: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "THINKING_MODES":
                value = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "THINKING_MODES":
                    value = node.value
    assert value is not None, "effective.py 顶层找不到 THINKING_MODES 赋值"
    if isinstance(value, ast.Call):
        assert len(value.args) == 1, "THINKING_MODES 的构造形态变了（不是单参调用）"
        value = value.args[0]
    assert isinstance(value, (ast.Set, ast.List, ast.Tuple)), (
        f"THINKING_MODES 不是集合字面量（{type(value).__name__}）"
    )
    return value


def _backend_variant_bytes(
    text: str,
    *,
    drop: str | None = None,
    extra: str | None = None,
) -> bytes:
    """在**模块级** ``THINKING_MODES`` 集合字面量里删一档 / 加一档，返回新字节。

    按 AST 位置改（字节偏移 ⇒ 对格式化不敏感），改的是**字符串字面量本身**
    ⇒ 副本仍是合法 Python（``{..., "high", }`` 合法）。
    """
    node = _module_level_modes_node(text)
    data = text.encode("utf-8")
    lines = data.splitlines(keepends=True)

    def _offset(lineno: int, col: int) -> int:
        # ast 的 col_offset 是**字节**偏移 ⇒ 必须在字节域里算
        return sum(len(line) for line in lines[: lineno - 1]) + col

    start = _offset(node.lineno, node.col_offset)
    end = _offset(node.end_lineno, node.end_col_offset)
    segment = data[start:end].decode("utf-8")
    assert segment.startswith("{") and segment.endswith("}"), (
        f"集合字面量形态变了：{segment[:60]!r}"
    )
    if drop is not None:
        pattern = re.compile(rf"""(['"]){re.escape(drop)}\1""")
        segment, count = pattern.subn("", segment, count=1)
        assert count == 1, f"THINKING_MODES 里没有 {drop!r}（用例前提不成立）"
    if extra is not None:
        segment = segment[:-1] + f', "{extra}"' + "}"
    return data[:start] + segment.encode("utf-8") + data[end:]


def _backend_copy_without(tmp_path: Path, drop: str) -> Path:
    """复制真实 ``effective.py`` 并从**模块级** ``THINKING_MODES`` 里删掉一个成员。"""
    text = BACKEND.read_text(encoding="utf-8")
    path = tmp_path / "effective_variant.py"
    path.write_bytes(_backend_variant_bytes(text, drop=drop))
    return path


def _backend_copy_with_extra(tmp_path: Path, extra: str) -> Path:
    """复制真实 ``effective.py`` 并给**模块级** ``THINKING_MODES`` 加一档。"""
    text = BACKEND.read_text(encoding="utf-8")
    path = tmp_path / "effective_extra.py"
    path.write_bytes(_backend_variant_bytes(text, extra=extra))
    return path


#: 死代码里的**同名赋值**（模块级真常量才是权威）——复现「旧实现被顶替 ⇒ 假绿」。
_HIJACK_IN_FUNCTION = '''

def _legacy_thinking_modes() -> frozenset[str]:
    """同名赋值的死代码：模块级常量才是权威（回归夹具）。"""
    THINKING_MODES = frozenset(
        {"default", "off", "on", "adaptive", "low", "medium", "high", "max"}
    )
    return THINKING_MODES
'''

#: 同上，但放在**死分支**（``if False:``）里。
_HIJACK_IN_DEAD_BRANCH = '''

if False:
    THINKING_MODES = frozenset(
        {"default", "off", "on", "adaptive", "low", "medium", "high", "max"}
    )
'''


def _backend_copy_with_hijack(tmp_path: Path, hijack: str) -> Path:
    """模块级清单**删一档** + 文件尾追加一段同名赋值（函数内 / 死分支）。

    ⇒ 旧实现（``ast.walk`` + 取行号最大的）会挑中**追加的那份**（8 档，与前端
    一致）⇒ **exit 0 假绿**；新实现只认模块级 ⇒ 少一档 ⇒ exit 1。
    """
    text = BACKEND.read_text(encoding="utf-8")
    path = tmp_path / "effective_hijack.py"
    path.write_bytes(_backend_variant_bytes(text, drop=DROPPED_MODE) + hijack.encode("utf-8"))
    return path


#: 前端数组声明的定位（与巡检脚本同款口径：类型注解里可能含分号 ⇒ 用 ``[^=]*``）。
_FRONTEND_DECL_RE = re.compile(r"(const\s+THINKING_OPTIONS\b[^=]*=\s*\[)")


def _frontend_copy_with_extra(tmp_path: Path, extra: str) -> Path:
    """复制真实前端文件并给 ``THINKING_OPTIONS`` 加一个假档。"""
    text = FRONTEND.read_text(encoding="utf-8")
    mutated, count = _FRONTEND_DECL_RE.subn(
        lambda m: m.group(1) + f"\n  {{ value: '{extra}', label: '假档位' }},",
        text,
        count=1,
    )
    assert count == 1, "前端 THINKING_OPTIONS 数组声明定位失败（正则失效？）"
    path = tmp_path / "AiSettingsThinkingOptions_variant.ts"
    path.write_text(mutated, encoding="utf-8")
    return path


def _frontend_copy_with_case_drift(tmp_path: Path, mode: str) -> Path:
    """复制真实前端文件并把某个 ``value`` 改成**首字母大写**（大小写漂移）。"""
    text = FRONTEND.read_text(encoding="utf-8")
    original = f"{{ value: '{mode}',"
    drifted = f"{{ value: '{mode.capitalize()}',"
    assert text.count(original) == 1, (
        f"前端 value {mode!r} 的定位失败（用例前提不成立）"
    )
    mutated = text.replace(original, drifted)
    path = tmp_path / "AiSettingsThinkingOptions_case_drift.ts"
    path.write_text(mutated, encoding="utf-8")
    return path


#: 真实声明的定位标记（**带 ``export``** ⇒ 不会撞上注释形态的同名片段）。
_FRONTEND_DECL_MARKER = "export const THINKING_OPTIONS"


def _frontend_copy_with_comment_hijack(tmp_path: Path, extra: str) -> Path:
    """真实声明**之前**插一段**注释形态的旧清单**（8 档，与后端一致），
    并给**真实数组**加一档 ``extra``。

    ⇒ 旧实现（不排除注释 + 取首个匹配）解析到**注释里的旧清单** ⇒ 8 档 == 后端
    ⇒ **exit 0 假绿**（真实数组多出的一档被完全看不见）；
    新实现必须解析到**真实声明** ⇒ 发现多出一档 ⇒ exit 1。
    """
    text = FRONTEND.read_text(encoding="utf-8")
    # ① 先给真实数组加一档（此时文件里只有一处声明，定位可靠）
    mutated, count = _FRONTEND_DECL_RE.subn(
        lambda m: m.group(1) + f"\n  {{ value: '{extra}', label: '假档位' }},",
        text,
        count=1,
    )
    assert count == 1, "前端 THINKING_OPTIONS 数组声明定位失败（正则失效？）"
    # ② 再在真实声明之前插注释形态的旧清单
    hijack = "\n".join(
        f"//   {{ value: '{mode}', label: '旧档位' }}," for mode in EXPECTED_MODES
    )
    block = "// const THINKING_OPTIONS = [\n" + hijack + "\n// ];\n"
    assert mutated.count(_FRONTEND_DECL_MARKER) == 1, "真实声明定位失败（用例前提不成立）"
    mutated = mutated.replace(_FRONTEND_DECL_MARKER, block + _FRONTEND_DECL_MARKER, 1)
    path = tmp_path / "AiSettingsThinkingOptions_comment_hijack.ts"
    path.write_text(mutated, encoding="utf-8")
    return path


def _frontend_copy_with_inner_comment(tmp_path: Path) -> Path:
    """在**数组内部**插一段普通注释（**故意含未配对的 ``[`` 与 ``'``**）。

    ⇒ 旧实现（把注释当代码）⇒ 字符串未闭合 / 括号配平失败 ⇒ **exit 2 假红**
    （合法 TS 被判不可判）；新实现先去注释 ⇒ 正常解析 ⇒ 两侧一致 ⇒ exit 0。
    """
    text = FRONTEND.read_text(encoding="utf-8")
    assert text.count(_FRONTEND_DECL_MARKER) == 1, "真实声明定位失败（用例前提不成立）"
    decl_at = text.index(_FRONTEND_DECL_MARKER)
    bracket_at = text.index("= [", decl_at)
    insert_at = bracket_at + len("= [")
    comment = "\n  // 说明：注释不是代码 —— 下面的 [ 与 ' 故意不配对（don't 这类写法）"
    mutated = text[:insert_at] + comment + text[insert_at:]
    path = tmp_path / "AiSettingsThinkingOptions_inner_comment.ts"
    path.write_text(mutated, encoding="utf-8")
    return path


def test_current_tree_is_consistent() -> None:
    """A 当前树：两份清单必须一致（exit 0），且 8 档名都在输出里。

    ⚠️ 断言 8 档名都出现，是为了证明脚本**真的解析出了集合**——
    否则「解析不出来 ⇒ 恒绿」也能满足 exit 0（坑区「夹具永远不触发闸门」）。
    """
    proc = _run_script()
    assert proc.returncode == 0, (
        f"当前树两份清单应一致，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    for mode in EXPECTED_MODES:
        assert mode in proc.stdout, (
            f"输出里没有档位 {mode!r} ⇒ 脚本可能没真正解析到集合\n{proc.stdout}"
        )


def test_backend_missing_one_mode_is_reported(tmp_path: Path) -> None:
    """B 后端少一档 ⇒ exit 1，且输出**点名**那一档。

    判别点：脚本必须**比了值**（不是「跑过就算」）。
    """
    variant = _backend_copy_without(tmp_path, DROPPED_MODE)
    proc = _run_script("--backend", str(variant))
    assert proc.returncode == 1, (
        f"后端少 {DROPPED_MODE!r} 档时应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert DROPPED_MODE in proc.stdout, (
        f"输出里没有点名缺失档 {DROPPED_MODE!r}\n{proc.stdout}"
    )
    assert "仅前端有" in proc.stdout, (
        f"输出没有定性「仅前端有」\n{proc.stdout}"
    )


def test_frontend_with_extra_mode_is_reported(tmp_path: Path) -> None:
    """C 前端多一档 ⇒ exit 1，且输出**点名**那一档。"""
    variant = _frontend_copy_with_extra(tmp_path, FAKE_MODE)
    proc = _run_script("--frontend", str(variant))
    assert proc.returncode == 1, (
        f"前端多 {FAKE_MODE!r} 档时应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert FAKE_MODE in proc.stdout, (
        f"输出里没有点名多出的档 {FAKE_MODE!r}\n{proc.stdout}"
    )
    assert "仅前端有" in proc.stdout, (
        f"输出没有定性「仅前端有」\n{proc.stdout}"
    )


def test_missing_file_exits_2(tmp_path: Path) -> None:
    """D 文件缺失 ⇒ exit 2（**不是 0 也不是 1**）。

    ⚠️ exit 0 = 「一致」⇒ 把「读不到」退化成 0 就是**把量具失真当合规**
    （AGENTS 坑区「解析失败当一致」家族）。
    """
    missing = tmp_path / "no_such_effective.py"
    proc = _run_script("--backend", str(missing))
    assert proc.returncode == 2, (
        f"后端文件缺失时应 exit 2，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "文件缺失" in proc.stderr, (
        f"stderr 没有说明「文件缺失」\n{proc.stderr}"
    )


def test_missing_frontend_file_exits_2(tmp_path: Path) -> None:
    """D' 前端文件缺失 ⇒ exit 2（与后端侧同款口径）。"""
    missing = tmp_path / "no_such_options.ts"
    proc = _run_script("--frontend", str(missing))
    assert proc.returncode == 2, (
        f"前端文件缺失时应 exit 2，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_unparsable_backend_exits_2(tmp_path: Path) -> None:
    """E 后端解析不出集合 ⇒ exit 2（不许当一致）。"""
    broken = tmp_path / "effective_broken.py"
    broken.write_text("SOMETHING_ELSE = 1\n", encoding="utf-8")
    proc = _run_script("--backend", str(broken))
    assert proc.returncode == 2, (
        f"后端解析失败时应 exit 2，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "解析失败" in proc.stderr, (
        f"stderr 没有说明「解析失败」\n{proc.stderr}"
    )


def test_unparsable_frontend_exits_2(tmp_path: Path) -> None:
    """E' 前端解析不出数组 ⇒ exit 2（不许当一致）。"""
    broken = tmp_path / "options_broken.ts"
    broken.write_text("export const SOMETHING_ELSE = 1;\n", encoding="utf-8")
    proc = _run_script("--frontend", str(broken))
    assert proc.returncode == 2, (
        f"前端解析失败时应 exit 2，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_frontend_case_drift_is_reported(tmp_path: Path) -> None:
    """F 前端 value 大小写漂移 ⇒ exit 1，且输出点名「大小写 / 拼写漂移」。

    判别点：拼写校验必须**独立**于集合相等（集合相等只会说「仅前端有
    'Default'」；拼写校验要**定性**出它与后端 'default' 是同一个档）。
    """
    variant = _frontend_copy_with_case_drift(tmp_path, "default")
    proc = _run_script("--frontend", str(variant))
    assert proc.returncode == 1, (
        f"前端大小写漂移时应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Default" in proc.stdout, (
        f"输出里没有点名漂移后的值 'Default'\n{proc.stdout}"
    )
    assert "大小写" in proc.stdout, (
        f"输出没有定性「大小写 / 拼写漂移」\n{proc.stdout}"
    )


def test_backend_extra_mode_is_reported(tmp_path: Path) -> None:
    """G **后端多一档**（前端没加）⇒ exit 1，且输出点名「仅后端有」。

    ⚠️ **这一条专属于 set-equality**：B/C 两个方向前端都有「后端不认的值」⇒
    ``frontend-spelling`` 也会红 ⇒ 把 set-equality 的判定改坏（恒真）时
    B/C 照样绿 ⇒ **跑而不验**。只有本用例的差集方向**只有集合相等能判**
    ⇒ 撤掉 set-equality ⇒ 本用例必红。
    """
    variant = _backend_copy_with_extra(tmp_path, FAKE_MODE)
    proc = _run_script("--backend", str(variant))
    assert proc.returncode == 1, (
        f"后端多 {FAKE_MODE!r} 档时应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "仅后端有" in proc.stdout, (
        f"输出没有定性「仅后端有」\n{proc.stdout}"
    )
    assert FAKE_MODE in proc.stdout, (
        f"输出里没有点名多出的档 {FAKE_MODE!r}\n{proc.stdout}"
    )


def test_frontend_comment_hijack_does_not_pass(tmp_path: Path) -> None:
    """H 注释形态的旧清单**不得劫持**解析 ⇒ 真实数组多出的一档必须被抓到。

    夹具：真实声明**之前**放一段**注释形态**的 8 档旧清单（与后端一致），
    真实数组里再加一档 ``ultra``。
    ⇒ 解析到注释里的旧清单 ⇒ 两侧相等 ⇒ **exit 0 假绿**；
    正确行为 = 解析真实声明 ⇒ 多一档 ⇒ exit 1。
    """
    variant = _frontend_copy_with_comment_hijack(tmp_path, FAKE_MODE)
    proc = _run_script("--frontend", str(variant))
    assert proc.returncode != 0, (
        f"注释劫持后仍 exit 0 ⇒ 解析取的不是真实声明（假绿）\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert FAKE_MODE in proc.stdout, (
        f"输出里没有点名真实数组多出的档 {FAKE_MODE!r}\n{proc.stdout}"
    )


def test_backend_function_scope_hijack_is_ignored(tmp_path: Path) -> None:
    """I **函数内**的同名赋值**不是**模块级常量 ⇒ 只认模块级 ⇒ exit 1。

    夹具：模块级清单删一档 + 文件尾追加一个函数，函数里给 ``THINKING_MODES``
    重新赋成 8 档。⇒ 旧实现（``ast.walk`` + 取行号最大的）会挑中**函数里那份**
    ⇒ 与前端一致 ⇒ **exit 0 假绿**；新实现只认模块级 ⇒ 少一档 ⇒ exit 1。
    """
    variant = _backend_copy_with_hijack(tmp_path, _HIJACK_IN_FUNCTION)
    proc = _run_script("--backend", str(variant))
    assert proc.returncode == 1, (
        f"函数内同名赋值不得顶替模块级常量，应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "仅前端有" in proc.stdout, (
        f"输出没有定性「仅前端有」（说明读到的不是模块级清单）\n{proc.stdout}"
    )
    assert DROPPED_MODE in proc.stdout, (
        f"输出里没有点名模块级缺失的档 {DROPPED_MODE!r}\n{proc.stdout}"
    )


def test_backend_dead_branch_hijack_is_ignored(tmp_path: Path) -> None:
    """J **死分支**（``if False:``）里的同名赋值同样**不是**模块级常量 ⇒ exit 1。

    与 I 同形状（同一个坑的两种位置）：``ast.walk`` 会走进死分支 ⇒ 旧实现
    照样被顶替 ⇒ 假绿。
    """
    variant = _backend_copy_with_hijack(tmp_path, _HIJACK_IN_DEAD_BRANCH)
    proc = _run_script("--backend", str(variant))
    assert proc.returncode == 1, (
        f"死分支里的同名赋值不得顶替模块级常量，应 exit 1，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "仅前端有" in proc.stdout, (
        f"输出没有定性「仅前端有」（说明读到的不是模块级清单）\n{proc.stdout}"
    )
    assert DROPPED_MODE in proc.stdout, (
        f"输出里没有点名模块级缺失的档 {DROPPED_MODE!r}\n{proc.stdout}"
    )


def test_frontend_inner_comment_is_not_a_false_red(tmp_path: Path) -> None:
    """K 合法 TS：数组内插普通注释 ⇒ **不许假红** ⇒ 两侧一致 ⇒ exit 0。

    夹具的注释**故意**含未配对的 ``[`` 与 ``'``：旧实现把注释当代码 ⇒
    字符串未闭合 ⇒ **exit 2**（合法 TS 被判「不可判」）⇒ 本用例抓的是
    「去注释预处理有没有真的生效」。
    """
    variant = _frontend_copy_with_inner_comment(tmp_path)
    proc = _run_script("--frontend", str(variant))
    assert proc.returncode == 0, (
        f"数组内的普通注释不应导致解析失败，却 exit {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    for mode in EXPECTED_MODES:
        assert mode in proc.stdout, (
            f"输出里没有档位 {mode!r} ⇒ 可能没真正解析到集合\n{proc.stdout}"
        )
