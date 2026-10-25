#!/usr/bin/env python3
"""思考档 8 档封闭集：后端 ``THINKING_MODES`` 与前端 ``THINKING_OPTIONS`` 的一致性巡检。

背景
----
「思考档」是 **8 档封闭集**（``default/off/on/adaptive/low/medium/high/max``），
仓里有**两份清单**：

- **后端唯一权威**：``packages/core/model_router/effective.py`` 的 ``THINKING_MODES``
  （``capability_bindings.thinking_override`` 的合法值域）
- **前端共享单点**：``apps/web/src/pages/AiSettingsThinkingOptions.ts`` 的
  ``THINKING_OPTIONS``（下拉选项数组）

⚠️ 两份清单之间此前**没有任何机器校验** ⇒ 一侧增删档，另一侧静默漂移：

- 后端加了档、前端没加 ⇒ 用户在下拉里**选不到**那个档（功能缺失，全程无报错）；
- 前端加了档、后端没加 ⇒ 后端 ``apply_thinking_override`` 抛 ``ValueError``
  （要等到写库才炸，而前端那边已经能选出来了）。

⇒ 本脚本把「两份清单必须逐字相等」变成机器看守（2026-10-24 审查遗留项：
「前后端 8 档映射机器看守」）。

口径与取舍
----------
1. **静态解析，不 import 执行**——与仓内先例
   ``skills/novelos/scripts/check_skill_package.py`` 同款（那个脚本也是
   stdlib-only、**不 import 仓内代码**，靠读文本做契约检查）。
   后端侧用 :mod:`ast` 解析 ``effective.py``、取**模块级** ``THINKING_MODES``
   的字符串字面量集合；前端侧用「去注释 ⇒ 定位数组字面量 → 括号配平扫描 →
   逐元素取 ``value:``」。
   ⚠️ **不 import 的理由**：``import packages.core.model_router.effective`` 会连带
   拉起 ``packages.core`` 的一整条 import 链（可能碰到环境 / 配置依赖），
   而巡检脚本应当**能在任意环境跑**（含 CI 的裸解释器）。
2. **解析失败 ≠ 一致**：文件缺失 / 解析不出集合 ⇒ 退出码 **2**。
   ⚠️ 绝不把「解析不出来」退化成「一致」——那是把**量具失真**当成合规。
3. **逐值报告差异**：分别点名「仅后端有」与「仅前端有」。
4. **额外做拼写校验**：前端每个 ``value`` 必须**逐字**落在后端档位的拼写上；
   忽略大小写后能对上的 ⇒ 定性为「大小写 / 拼写漂移」，否则「未知档位」。
5. **不硬编码档数 8**：那会变成「第三处副本」（AGENTS 纪律「副本是重复的义务」
   ——凡「同一事实要同步 N 处」，先问「能不能让 N 收敛成 1」）。
   ⚠️ **订正（2026-10-24 对抗审查 F5）**：本脚本**不约束档数**——「两侧集合
   相等」只保证**两份清单一致**，两侧同时从 8 档改成 7 档，本脚本照样 exit 0
   （两边确实相等）。**真正钉住「8 档」这个封闭集的是**
   ``tests/unit/test_capability_thinking_override.py``（它逐档断言
   ``THINKING_MODES`` 的成员集合）⇒ 职责分工：本脚本管「两侧逐字相等」，
   那个测试管「档数与成员」。
6. **只认模块级常量**（2026-10-24 对抗审查 F2）：后端侧**只扫** ``tree.body``
   顶层的赋值——函数体内 / 死分支（``if False:``）里的同名赋值**不是**模块级
   常量（Python 语义如此），旧实现用「全树遍历 + 取行号最大的」会被它们顶替
   ⇒ 解析到假清单 ⇒ **exit 0 假绿**。
7. **前端先去注释，再看候选**（2026-10-24 对抗审查 F1）：定位数组声明前先把
   注释内容替换成等长空格（逐字符状态机）⇒ 注释形态的旧清单无法劫持解析；
   同时全文**所有**候选声明都试解析，**内容不一致 ⇒ 不可判（exit 2）**
   ⇒ 「字符串字面量里藏一份清单」这类劫持也堵死。

退出码
------
- ``0``：两份清单一致（且前端拼写全部合规）
- ``1``：不一致（集合不等 / 前端拼写漂移 / 清单里有重复项）
- ``2``：文件缺失 / 读取失败 / 解析失败（**不可判** ⇒ 既不是合规也不是不合规）

用法
----
::

    python scripts/check_thinking_modes_consistency.py
    python scripts/check_thinking_modes_consistency.py --backend <effective.py> --frontend <options.ts>
    python scripts/check_thinking_modes_consistency.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

# ⚠️ 本机 Windows stdout 默认 cp936（AGENTS 坑区「Windows 默认 cp936 吞中文」家族）。
# 本脚本的输出全是中文诊断 ⇒ 不重配编码时，**在 GBK 控制台上全是乱码**，
# 复核者根本读不出「哪条检查没过」（同款处置见 check_skill_package.py 开头）。
try:  # pragma: no cover —— 极老解释器上 reconfigure 不存在时静默跳过
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

#: 相对仓库根的默认路径（后端唯一权威 / 前端共享单点）。
DEFAULT_BACKEND = Path("packages") / "core" / "model_router" / "effective.py"
DEFAULT_FRONTEND = Path("apps") / "web" / "src" / "pages" / "AiSettingsThinkingOptions.ts"

#: 后端权威常量的名字（``effective.py`` 里 ``THINKING_MODES = frozenset({...})``）。
BACKEND_CONST_NAME = "THINKING_MODES"

#: 前端共享单点的名字（``AiSettingsThinkingOptions.ts`` 里 ``const THINKING_OPTIONS = [...]``）。
FRONTEND_CONST_NAME = "THINKING_OPTIONS"

#: 前端数组声明的定位正则。
#:
#: ⚠️ 类型注解里**可能含分号**（``{ value: ThinkingMode; label: string }[]``）⇒
#: 注解段用 ``[^=]*?``（宽松到下一个 ``=``）而不是 ``[^=;]*``（会被分号截断）。
#:
#: ⚠️ 这个正则**只在「已去注释」的文本上跑**（见 :func:`_strip_js_comments`）⇒
#: 注释形态的旧清单不会命中；且**全文所有命中**都要试解析（见
#: :func:`extract_frontend_values`）⇒ 单个命中不必然是权威声明。
_FRONTEND_DECL_RE = re.compile(
    r"\bconst\s+" + FRONTEND_CONST_NAME + r"\b\s*(?::[^=]*?)?=\s*\["
)

#: 数组元素里的 ``value: '...'``（三种引号都认）。
_FRONTEND_VALUE_RE = re.compile(
    r"""\bvalue\s*:\s*(?:'([^']*)'|"([^"]*)"|`([^`]*)`)"""
)


# ---------------------------------------------------------------------------
# 检查结果
# ---------------------------------------------------------------------------


class Check:
    """一条检查的结果（与 ``check_skill_package.py`` 同款形态）。"""

    def __init__(self, check_id: str, ok: bool, message: str) -> None:
        self.check_id = check_id
        self.ok = ok
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.check_id, "ok": self.ok, "message": self.message}


class ParseError(Exception):
    """解析失败 ⇒ **不可判**（退出码 2；绝不当作「一致」）。"""


# ---------------------------------------------------------------------------
# 后端侧解析（AST，静态求值）
# ---------------------------------------------------------------------------


def _find_module_level_assignment(tree: ast.Module, name: str) -> ast.AST:
    """取**模块级**（``tree.body`` 顶层）的 ``name = ...`` 赋值节点。

    ⚠️ **只扫顶层，不做全树遍历**（2026-10-24 对抗审查 F2）：
    旧实现用 ``ast.walk`` + 「行号最大的那个」挑赋值 ⇒ **函数体内** /
    死分支（``if False:``）里的同名赋值会把模块级真常量**顶替掉** ⇒
    解析到的不是权威清单 ⇒ 两侧「看起来相等」⇒ **exit 0 假绿**。
    模块级常量只可能出现在 ``tree.body`` 的顶层语句里（函数体 / 条件分支
    里的赋值在 Python 语义下**不是**模块级常量）。

    ⚠️ **多处模块级同名赋值 ⇒ 不可判**（exit 2），不做「取最后一个」：
    「哪个生效」要靠 Python 执行语义判断，而本脚本刻意**不 import 执行**
    （见模块 docstring 口径 1）⇒ 静态层面对「多处定义」只能报不可判。
    """
    found: list[tuple[int, ast.AST]] = []
    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    break
        if value is None:
            continue
        found.append((getattr(node, "lineno", 0), value))
    if not found:
        raise ParseError(f"找不到模块级的 {name} 赋值")
    if len(found) > 1:
        lines = ", ".join(str(line) for line, _ in found)
        raise ParseError(f"模块级有 {len(found)} 处 {name} 赋值（第 {lines} 行）⇒ 不可判")
    return found[0][1]


def _collect_string_elements(node: ast.AST) -> list[str]:
    """从一个「集合字面量」节点收集字符串常量（保序）。

    支持的形态：``frozenset({...})`` / ``{...}`` / ``(...)`` / ``[...]``。
    ⚠️ 遇到**不能静态求值**的形态（变量引用 / ``*`` 解包 / 非字符串元素）
    一律抛 :class:`ParseError` ⇒ 退出码 2 ⇒ **不可判**，绝不静默当空集。
    """
    if isinstance(node, ast.Call):
        if len(node.args) != 1 or node.keywords:
            raise ParseError("构造调用形态无法静态求值（期望单参调用）")
        return _collect_string_elements(node.args[0])
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        out: list[str] = []
        for element in node.elts:
            if isinstance(element, ast.Starred):
                raise ParseError("集合里有 * 解包，无法静态求值")
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                raise ParseError("集合里有非字符串字面量")
            out.append(element.value)
        return out
    raise ParseError("不是集合字面量")


def extract_backend_modes(text: str) -> list[str]:
    """从后端源码里静态提取 ``THINKING_MODES`` 的成员（保序，保留重复）。

    ⚠️ 只认**模块级**常量（:func:`_find_module_level_assignment`）。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ParseError(f"不是合法 Python：{exc}") from exc
    value = _find_module_level_assignment(tree, BACKEND_CONST_NAME)
    return _collect_string_elements(value)


# ---------------------------------------------------------------------------
# 前端侧解析（去注释预处理 + 文本扫描 + 括号配平）
# ---------------------------------------------------------------------------


def _skip_string_literal(text: str, index: int) -> int:
    """``text[index]`` 是引号 ⇒ 返回**闭合引号之后**的下标。"""
    quote = text[index]
    i = index + 1
    n = len(text)
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == quote:
            return i + 1
        i += 1
    raise ParseError("字符串字面量未闭合")


#: 正则字面量前可能出现「除号上下文」的字符集合（启发式，见 :func:`_is_regex_start`）。
_JS_REGEX_PREV_CHARS = frozenset("([{,;:=!&|?%^~<>")
#: 正则字面量前可能是「关键字上下文」的词（``return /re/`` 这类）。
_JS_REGEX_PREV_KEYWORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "case",
        "do",
        "else",
        "yield",
        "await",
    }
)


def _is_regex_start(out: list[str], index: int) -> bool:
    """``out[index] == '/'`` ⇒ 判断此处是否**正则字面量**的开头（启发式）。

    ⚠️ JS 里 ``/`` 是正则还是除号只能靠**前一个有效字符**推断（标准启发式）。
    判错的后果都落在**保守**方向上：

    - 把除号当正则 ⇒ 可能一路扫到远处的 ``/`` ⇒ 中间那段被当注释清空
      ⇒ 解析多半失败 ⇒ **exit 2（不可判）**，不会假绿；
    - 把正则当除号 ⇒ 正则里的 ``//`` 可能被当成行注释 ⇒ 但正则里的斜杠
      必须写成 ``\\/``（转义）⇒ :func:`_strip_js_comments` 的「``/`` 前面是
      ``\\`` ⇒ 不是注释起点」这条兜底会挡住常见形态。

    ⚠️ ``out`` 是**已处理前缀**（注释已清空成空格）⇒ 往前找有效字符时
    不会撞进注释里。
    """
    j = index - 1
    while j >= 0 and out[j].isspace():
        j -= 1
    if j < 0:
        return True
    prev = out[j]
    if prev in _JS_REGEX_PREV_CHARS:
        return True
    if prev.isalnum() or prev in "_$":
        end = index
        while j >= 0 and (out[j].isalnum() or out[j] in "_$"):
            j -= 1
        return "".join(out[j + 1 : end]) in _JS_REGEX_PREV_KEYWORDS
    return False


def _skip_regex_literal(text: str, index: int) -> int:
    """``text[index] == '/'`` 且是正则字面量开头 ⇒ 返回闭合斜杠之后的下标。"""
    i = index + 1
    n = len(text)
    in_class = False
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "\n":
            raise ParseError("正则字面量未闭合")
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return i + 1
        i += 1
    raise ParseError("正则字面量未闭合")


def _strip_js_comments(text: str) -> str:
    """把 JS/TS 的**注释内容替换成等长空格**（换行结构原样保留）。

    为什么必须做（2026-10-24 对抗审查 F1 / F3）：

    - **F1（假绿）**：``const THINKING_OPTIONS = [`` 的定位若**不排除注释**，
      一段写在真实声明**之前**的**注释形态旧清单**就能劫持解析 ⇒ 解析到的
      不是真清单 ⇒ 两侧「看起来相等」⇒ **exit 0 假绿**；
    - **F3（假红）**：注释里的引号 / 方括号会让括号配平失败 ⇒ **合法 TS**
      被判 exit 2。

    口径：逐字符状态机，区分 **行注释 ``//``** / **块注释 ``/* */``** /
    **字符串字面量**（``'`` ``"`` `` ` ``）/ **正则字面量**。
    ⚠️ 注释内容替换成**等长空格**（不是删掉）⇒ 下标与行号全部保持不变 ⇒
    报错信息里的行号仍然指向真实源码位置。

    ⚠️ **已知边界**：本函数处理**整个文件** ⇒ 文件里**任何一处**字符串 / 正则 /
    块注释未闭合，都会让整个巡检 **exit 2**（不可判）。这是**保守方向**
    （宁可「不可判」也不假绿），且对当前真实文件实测通过。
    """
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == "/" and i > 0 and text[i - 1] == "\\":
            # 转义斜杠（多半是正则里的 ``\/``）⇒ **不是**注释起点。
            i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                out[j] = " "
                j += 1
            i = j
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            j = i + 2
            terminated = False
            while j < n:
                if text[j] == "*" and j + 1 < n and text[j + 1] == "/":
                    out[j] = " "
                    out[j + 1] = " "
                    j += 2
                    terminated = True
                    break
                if text[j] != "\n":
                    out[j] = " "
                j += 1
            if not terminated:
                raise ParseError("块注释未闭合")
            i = j
            continue
        if char in "'\"`":
            i = _skip_string_literal(text, i)
            continue
        if char == "/" and _is_regex_start(out, i):
            i = _skip_regex_literal(text, i)
            continue
        i += 1
    return "".join(out)


def _scan_array_literal(text: str, start: int) -> str:
    """``text[start] == '['`` ⇒ 返回该数组字面量的源码（含首尾方括号）。"""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        char = text[i]
        if char in "'\"`":
            i = _skip_string_literal(text, i)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise ParseError(
        "数组字面量未闭合（若源码含奇怪注释，请先确认注释预处理是否正确）"
    )


def _iter_object_literals(body: str) -> list[str]:
    """取出数组体里每个**顶层** ``{...}`` 对象字面量的源码。"""
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        char = body[i]
        if char in "'\"`":
            i = _skip_string_literal(body, i)
            continue
        if char == "{":
            start = i
            depth = 0
            while i < n:
                inner = body[i]
                if inner in "'\"`":
                    i = _skip_string_literal(body, i)
                    continue
                if inner == "{":
                    depth += 1
                elif inner == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if i >= n:
                raise ParseError("对象字面量未闭合")
            out.append(body[start : i + 1])
            i += 1
            continue
        i += 1
    return out


def _parse_frontend_array(text: str, match: re.Match[str]) -> list[str]:
    """解析**一处**候选声明指向的数组字面量，取每个元素的 ``value``（保序）。"""
    array_src = _scan_array_literal(text, match.end() - 1)
    body = array_src[1:-1]
    values: list[str] = []
    for element in _iter_object_literals(body):
        value_match = _FRONTEND_VALUE_RE.search(element)
        if value_match is None:
            raise ParseError(f"{FRONTEND_CONST_NAME} 的某个元素缺 value 字段")
        values.append(next(group for group in value_match.groups() if group is not None))
    if not values:
        raise ParseError(f"{FRONTEND_CONST_NAME} 解析不出任何 value")
    return values


def _line_of(text: str, offset: int) -> int:
    """``offset`` 处的**行号**（1 起）——注释预处理保留了换行 ⇒ 行号不失真。"""
    return text.count("\n", 0, offset) + 1


def extract_frontend_values(text: str) -> list[str]:
    """从前端源码里静态提取 ``THINKING_OPTIONS`` 的 ``value`` 集合（保序）。

    两道防线（2026-10-24 对抗审查 F1）——**叠加**而非二选一：

    1. **先去注释**（:func:`_strip_js_comments`）⇒ 注释形态的旧清单不再能被
       定位到（这一条同时修掉 F3 的假红）；
    2. **多候选一致性**：全文所有 ``const THINKING_OPTIONS = [`` 候选**全部**
       试解析；解析不出合法 value 集的候选忽略；能解析出的候选若**内容不一致**
       ⇒ 不可判 ⇒ exit 2（防「字符串字面量里藏一份清单」这类注释去不掉的劫持）。

    ⚠️ 为什么两条都要：单靠第 1 条，**字符串字面量**里的 ``const THINKING_OPTIONS
    = [...]`` 仍会被正则命中（字符串不是注释）；单靠第 2 条，注释里的旧清单
    会与真声明**内容不一致** ⇒ 合法文件也会被判 exit 2（假红）。
    """
    stripped = _strip_js_comments(text)
    candidates = list(_FRONTEND_DECL_RE.finditer(stripped))
    if not candidates:
        raise ParseError(f"找不到 {FRONTEND_CONST_NAME} 数组声明")
    parsed: list[tuple[int, list[str]]] = []
    errors: list[str] = []
    for match in candidates:
        try:
            values = _parse_frontend_array(stripped, match)
        except ParseError as exc:
            errors.append(f"第 {_line_of(stripped, match.start())} 行候选：{exc}")
            continue
        parsed.append((match.start(), values))
    if not parsed:
        raise ParseError(
            f"{FRONTEND_CONST_NAME} 的 {len(candidates)} 处候选声明都解析失败："
            + "；".join(errors)
        )
    if len({tuple(values) for _, values in parsed}) > 1:
        detail = "；".join(
            f"第 {_line_of(stripped, start)} 行候选 = {values}" for start, values in parsed
        )
        raise ParseError(
            f"{FRONTEND_CONST_NAME} 有 {len(parsed)} 处候选声明且内容不一致（不可判）：{detail}"
        )
    return parsed[0][1]


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def _duplicates(values: list[str]) -> list[str]:
    """重复项（保序去重后仍出现的）——清单里的重复项是 bug（同名两行）。"""
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def _spelling_problems(backend: frozenset[str], frontend: list[str]) -> list[str]:
    """前端 ``value`` 不落在后端档位**拼写**上的项（逐条定性）。"""
    canonical_by_lower = {mode.lower(): mode for mode in backend}
    problems: list[str] = []
    for value in frontend:
        if value in backend:
            continue
        canonical = canonical_by_lower.get(value.lower())
        if canonical is None:
            problems.append(f"{value!r}（未知档位）")
        else:
            problems.append(f"{value!r}（大小写 / 拼写漂移，对应后端 {canonical!r}）")
    return problems


def run_checks(backend: list[str], frontend: list[str]) -> list[Check]:
    """跑全部检查项（两侧集合已解析成功 ⇒ 纯比较，不再触碰文件系统）。"""
    backend_set = frozenset(backend)
    frontend_set = frozenset(frontend)
    checks: list[Check] = []

    # ① 后端权威清单
    backend_dupes = _duplicates(backend)
    checks.append(
        Check(
            "thinking-modes/backend-authority",
            not backend_dupes,
            (
                f"后端 {BACKEND_CONST_NAME} = {sorted(backend_set)}（{len(backend_set)} 档）"
                if not backend_dupes
                else f"后端 {BACKEND_CONST_NAME} 有重复成员：{backend_dupes}"
            ),
        )
    )

    # ② 前端选项清单
    frontend_dupes = _duplicates(frontend)
    checks.append(
        Check(
            "thinking-modes/frontend-options",
            not frontend_dupes,
            (
                f"前端 {FRONTEND_CONST_NAME} 的 value = {sorted(frontend_set)}"
                f"（{len(frontend_set)} 档）"
                if not frontend_dupes
                else f"前端 {FRONTEND_CONST_NAME} 有重复 value：{frontend_dupes}"
            ),
        )
    )

    # ③ 两份清单必须逐字相等
    only_backend = sorted(backend_set - frontend_set)
    only_frontend = sorted(frontend_set - backend_set)
    equal = not (only_backend or only_frontend)
    checks.append(
        Check(
            "thinking-modes/set-equality",
            equal,
            (
                f"后端 {BACKEND_CONST_NAME} 与前端 {FRONTEND_CONST_NAME} 逐字一致"
                f"（{len(backend_set)} 档）"
                if equal
                else f"两份清单不一致：仅后端有 {only_backend}；仅前端有 {only_frontend}"
            ),
        )
    )

    # ④ 前端 value 必须逐字落在后端档位的拼写上（防大小写 / 拼写漂移）
    spelling_problems = _spelling_problems(backend_set, frontend)
    checks.append(
        Check(
            "thinking-modes/frontend-spelling",
            not spelling_problems,
            (
                "前端每个 value 都逐字落在后端档位拼写上"
                if not spelling_problems
                else "前端 value 不在后端档位拼写上：" + "；".join(spelling_problems)
            ),
        )
    )
    return checks


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """脚本自身位置推导仓库根（``<repo>/scripts/check_*.py`` ⇒ 父目录的父目录）。"""
    return Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    """读源码文本。

    ⚠️ 用 ``utf-8-sig``：BOM 会让 :func:`ast.parse` 直接失败
    （AGENTS 坑区「预检的 ast.parse 不容忍 BOM」），而 ``utf-8-sig`` 对
    **无 BOM** 的文件同样正常解码 ⇒ 两种情况都能读。
    """
    return path.read_text(encoding="utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_thinking_modes_consistency.py",
        description="思考档 8 档封闭集：后端 THINKING_MODES 与前端 THINKING_OPTIONS 的一致性巡检。",
    )
    parser.add_argument(
        "--backend",
        help=f"后端权威文件（默认：{DEFAULT_BACKEND.as_posix()}）",
    )
    parser.add_argument(
        "--frontend",
        help=f"前端选项文件（默认：{DEFAULT_FRONTEND.as_posix()}）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    backend_path = (
        Path(args.backend).expanduser().resolve() if args.backend else repo_root / DEFAULT_BACKEND
    )
    frontend_path = (
        Path(args.frontend).expanduser().resolve() if args.frontend else repo_root / DEFAULT_FRONTEND
    )

    missing = [path for path in (backend_path, frontend_path) if not path.is_file()]
    if missing:
        for path in missing:
            sys.stderr.write(f"文件缺失：{path}\n")
        sys.stderr.write("不可判 ⇒ 退出码 2（既不是「一致」也不是「不一致」）。\n")
        return 2

    try:
        backend_text = _read_text(backend_path)
    except OSError as exc:
        sys.stderr.write(f"读取失败：{backend_path}（{exc}）\n")
        return 2
    try:
        frontend_text = _read_text(frontend_path)
    except OSError as exc:
        sys.stderr.write(f"读取失败：{frontend_path}（{exc}）\n")
        return 2

    try:
        backend_modes = extract_backend_modes(backend_text)
    except ParseError as exc:
        sys.stderr.write(f"后端解析失败（不可判）：{backend_path}（{exc}）\n")
        return 2
    try:
        frontend_values = extract_frontend_values(frontend_text)
    except ParseError as exc:
        sys.stderr.write(f"前端解析失败（不可判）：{frontend_path}（{exc}）\n")
        return 2

    checks = run_checks(backend_modes, frontend_values)
    failures = [check for check in checks if not check.ok]

    if args.json:
        payload = {
            "backend": str(backend_path),
            "frontend": str(frontend_path),
            "total": len(checks),
            "failed": len(failures),
            "checks": [check.as_dict() for check in checks],
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        for check in checks:
            mark = "PASS" if check.ok else "FAIL"
            sys.stdout.write(f"[{mark}] {check.check_id}: {check.message}\n")

    if failures:
        sys.stderr.write(
            f"\n{len(failures)}/{len(checks)} 项检查未通过（思考档前后端清单漂移）。\n"
        )
        return 1
    if not args.json:
        sys.stdout.write(f"\n全部 {len(checks)} 项检查通过：思考档清单前后端一致。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
