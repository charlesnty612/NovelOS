# 「真吞异常」普查口径与数字（2026-10-23，批次二十四）

> **用途**：CHANGELOG 批次二十三写的两个数字（「真吞 16 处」与「其余 59 处未分诊」）经复核
> **与它们自己的文字不符**。本文把口径固定、把数字复算出来，供任何人逐位重跑。
> **来源**：Lead 独立探针（`tmp/verify_swallow_59.py`，2026-10-23）+ 对抗审查独立探针
> （另一套实现，结论逐位一致）。**两套独立实现同数**是本文数字唯一的可信度依据。

---

## 1. 口径定义（可复算）

- **对象**：`ast.ExceptHandler`（含裸 `except:`、`except Exception:`、`except <具体类型>:`）。
- **真吞** = handler 体内
  ① 没有任何 `raise`（AST 全遍历）；
  ② 没有任何日志调用（点分名里含 `log` / `logger` / `warn` / `error` / `exception` / `critical` 的 `Call`）。
- **解码**：**必须 `utf-8-sig`**。仓内 `packages/workflows/chapter_write/{polisher,save_draft,scene_planner}.py`
  三个文件带 UTF-8 BOM；用 naive `utf-8` 读会让 `ast.parse` 不报错但**静默丢掉 6 个 handler（含 1 个真吞）** —— 审查第一遍就因此得 179 ≠ 180。
- **分组**：A = `packages/core/quality` + `packages/domain`；B = `packages/core/api` + `packages/core/story_state` + `packages/workflows` + `scripts`。

## 2. 明细表（每格 = all / 有 raise / 有日志 / 不重抛 / 不重抛且无日志）

### A 组

| 目录#形态 | all | raise | logged | 不重抛 | **真吞** |
|---|---|---|---|---|---|
| core/quality#specific | 4 | 0 | 4 | 4 | **0** |
| packages/domain#Exception | 1 | 0 | 0 | 1 | **1** |
| packages/domain#specific | 23 | 12 | 16 | 11 | **6** |
| **合计** | **28** | 12 | 20 | **16** | **7** |

### B 组

| 目录#形态 | all | raise | logged | 不重抛 | **真吞** |
|---|---|---|---|---|---|
| core/api#Exception | 24 | 9 | 22 | 15 | **2** |
| core/api#specific | 144 | 127 | 110 | 17 | **15** |
| core/story_state#Exception | 1 | 1 | 0 | 0 | **0** |
| core/story_state#specific | 10 | 2 | 1 | 8 | **8** |
| packages/workflows#Exception | 44 | 2 | 34 | 42 | **8** |
| packages/workflows#specific | 58 | 17 | 17 | 41 | **36** |
| scripts#Exception | 26 | 1 | 2 | 25 | **24** |
| scripts#specific | 100 | 12 | 12 | 88 | **87** |
| **合计** | **407** | 171 | 104 | **236** | **180** |

## 3. 网格搜索：CHANGELOG 两个数字各自对应哪个口径

| 数字 | 成立口径 |
|---|---|
| **28** | A 组 / 全部 handler |
| **16** | A 组 / **仅「不重抛」**（B 类 9 处**加 `log.warning` 之前**的旧态） |
| **7** | A 组 / 不重抛 ∧ 不记日志（= 修后 A 类） |
| **407** | B 组 / 全部 handler |
| **236** | B 组 / 仅「不重抛」 |
| **180** | B 组 / 不重抛 ∧ 不记日志（本文「真吞」口径） |
| **59** | **只在**「(B 组**去掉 scripts**) ∧ 不重抛 ∧ 不记日志 ∧ **排除 `except Exception`**」下成立（69−10）——**CHANGELOG 原文点名了 scripts，数字却不含它** |

## 4. 结论（CHANGELOG 批次二十三的订正）

1. 「真吞（不重抛、不记日志）**16 处**」⇒ 口径写错：16 是**仅不重抛**、且是 **B 类修复前**的旧态；同口径的当前真吞是 **7**。
2. 「其余 **59 处**（core/api / core/story_state / workflows / scripts）未分诊」⇒ 数字与文字矛盾。如实三档：**236（四层 / 不重抛）**、**180（四层 / 不重抛且无日志）**；59 作废。

## 5. 复现

```python
# 用法：repo 根目录下 .venv/Scripts/python.exe tmp/verify_swallow_59.py
import ast, itertools, pathlib
ROOT = pathlib.Path(".").resolve()

def _log_call(node):
    if not isinstance(node, ast.Call):
        return False
    parts, cur = [], node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr); cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    text = ".".join(parts).lower()
    return any(t in text for t in ("log", "logger", "warn", "error", "exception", "critical"))

def _form(h):
    if h.type is None:
        return "bare"
    return "Exception" if isinstance(h.type, ast.Name) and h.type.id == "Exception" else "specific"

def scan(bases):
    cells = {}
    for base in bases:
        tag = "/".join(base.parts[-2:])
        for py in sorted(base.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8-sig"))   # ⚠️ 必须 utf-8-sig
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                cell = cells.setdefault(f"{tag}#{_form(node)}", {k: 0 for k in
                                        ("all", "raise", "logged", "no_raise", "no_raise_no_log")})
                cell["all"] += 1
                has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
                has_log = any(_log_call(n) for n in ast.walk(node))
                if has_raise: cell["raise"] += 1
                if has_log: cell["logged"] += 1
                if not has_raise:
                    cell["no_raise"] += 1
                    if not has_log: cell["no_raise_no_log"] += 1
    return cells

A = [ROOT / "packages/core/quality", ROOT / "packages/domain"]
B = [ROOT / "packages/core/api", ROOT / "packages/core/story_state",
     ROOT / "packages/workflows", ROOT / "scripts"]
for name, bases in (("A", A), ("B", B)):
    cells = scan(bases)
    for key in sorted(cells):
        print(name, key, cells[key])
```

## 6. 顺带记下的量具教训（同一批次踩到）

- **行数**：`Get-Content <f> | Measure-Object -Line` 在本仓**不能用来数行**——未加 `-Encoding UTF8` 时 PS 5.1 按 cp936 解码 UTF-8（577 行 → 448 行），`Measure-Object -Line` 又**只数非空行**（482）。真值以 `len(open(f, encoding="utf-8").read().splitlines())` / `wc -l` / `Get-Content -Encoding UTF8` 三法一致为准（本例 **577**）。
- **AST 统计**：`utf-8-sig` 是硬要求（见 §1）。
- 两条共同点：**量具的口径缺陷长得像被测量对象的性质**——前者会把健康文件报成「少 193 行」，后者会把正常模块报成「少 6 个 handler」。
