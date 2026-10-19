# `judge_results.json` 编码核验记录（2026-10-19）

> **状态**：一次性核验记录。**数据文件未作任何字节修改**（见 §二「处置」）。
> 触发原因：外部报告称本文件存在「GBK 字节被当 latin-1/cp1252 解读」的编码损坏，
> 并给出 `results[0].title = '�� 51 ��'`、`results[0].verdict = 'ȫ�»�е��……'` 两个样本。
> **本记录的核心结论：该前提被实测证伪——文件在磁盘上是完好的 UTF-8，损坏发生在读取侧。**

## 一、结论（先给结论，再给证据）

| # | 命题 | 判定 |
|---|---|---|
| 1 | `docs/evaluation/m2-judge/judge_results.json` 存在 GBK→latin-1 编码损坏 | **证伪**。文件是合法 UTF-8，中文全部正确，0 个 U+FFFD，0 个非 CJK 异常字符 |
| 2 | 规定的修法（`s.encode('latin-1')` → `.decode('gb18030')`）适用于本文件 | **不适用**。该逆变换对全部 10 条目标字符串抛 `UnicodeEncodeError`（见 §三） |
| 3 | 若强行施加该逆变换 | **会损坏正确的中文**（把「全章机械重复」变成乱码），**不可执行** |
| 4 | 报告里的 mojibake 样本来自本文件 | **不成立**。14 种编码 × 1~3 层链条的穷举中，没有一条能同时产生报告的 `title` 与 `verdict` 形态（见 §四） |
| 5 | 报告里的 mojibake 属于哪一类 | **读取侧（reader-side）** 损伤：Windows cp936 往返流水线的产物（见 §五），文件字节本身始终是好的 |

## 二、证据（可复现，2026-10-19 本机实测）

### 2.1 文件级字节事实

| 项 | 值 | 取数方式 |
|---|---|---|
| 工作区字节数 | **4187 B** | `os.path.getsize()` |
| git blob 字节数 | **4047 B** | `git cat-file -s 5be70f5:docs/evaluation/m2-judge/judge_results.json` |
| 两者差 | 140 B | 工作区 CRLF vs blob LF；`core.autocrlf=true` 的正常差异 |
| 解码后字符数 | **3331** | `len(raw.decode('utf-8'))` |
| 非 ASCII UTF-8 字节 | 1284 B | `len(raw) - len(ascii_only)` |
| U+FFFD（替换字符）个数 | **0** | `text.count('�')` |
| 非 CJK / 非全角 / 非中文标点的非 ASCII 字符 | **0** | 逐字符按码位区间判定 |
| 异常控制字符（`\n \r \t` 之外） | **0** | 同上 |
| 能否按 UTF-8 解码 | **能** | `raw.decode('utf-8')` 无异常 |

### 2.2 字符串级字节账（还原前后对比）

**「还原前 / 还原后」= 完全相同，因为没有发生任何还原。**

| 章 | 字段 | 字符数 | UTF-8 字节数 | 实际内容（节选） |
|---|---|---:|---:|---|
| 51 | title | 6 | 10 | `第 51 章` |
| 51 | verdict | 30 | 90 | `全章机械重复同一观测流程，…` |
| 51 | top_issues[0] | 20 | 60 | `通篇重复同一操作流程，节奏严重拖沓无爽点` |
| 55 | verdict | 25 | 75 | `工艺同源发现是真亮点，但笔记格式高度重复拖慢节奏。` |

（逐条完整账见 §六 脚本输出。）

### 2.3 与 `judge_summary.md` 的交叉校验

`judge_summary.md`（同目录，1208 B，UTF-8）被用作**独立基准**。校验方式：把
`judge_results.json` 每条结果的 8 个字段拼成 Markdown 表格行，在 summary 里做**逐字**包含判断。

| 章号 | 该行是否在 summary 中逐字出现 |
|---|---|
| 51 / 52 / 53 / 54 / 55 | **True / True / True / True / True** |

⇒ **两个文件逐字一致，无失配点。** 若文件真被 mojibake 损坏，summary（未被波及）必然对不上。
**这同时说明：对 JSON 施加逆变换会立刻破坏这份交叉一致性。**

### 2.4 生产方复核（写入路径是否有编码风险）

| 位置 | 写法 | 判定 |
|---|---|---|
| `scripts/m2_judge.py:509-510` | `with tmp.open("w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False, indent=2)` | 显式 UTF-8 + `ensure_ascii=False`（中文按原样落盘，不转义） |
| `scripts/m2_judge.py:870` | `(out_dir / "judge_summary.md").write_text(summary_md, encoding="utf-8")` | 显式 UTF-8 |
| `scripts/m2_judge.py:495` | `with p.open("r", encoding="utf-8") as f:` | 读路径亦显式 UTF-8 |

⇒ 生产方**没有**「靠平台默认编码写文件」的隐患，不存在「下次跑就写出 GBK 文件」的路径。

### 2.5 版本史与全仓扫描

- 全仓仅 **1 个 commit** 触及本文件：`5be70f5`（2026-08-25 20:33:12 +0800，「M2完成: …judge探针…」）。
  该 blob 同样是合法 UTF-8、同样 0 个 U+FFFD、同样含「全章机械」四字 ⇒ **历史上从未出现过 mojibake 版本**。
- 全仓扫描 1025 个文本文件（`.md/.json/.txt/.py/.ts/.tsx/.js/.jsx/.yml/.yaml/.sql/.toml/.cfg/.ini/.sh/.css/.html`）：
  - **0 个**文件不是合法 UTF-8（即：无「原始 GBK 字节」文件）；
  - **0 个**文件含 `鍏ㄧ珷` 型（UTF-8 被当 GBK 读）双编码签名。
  - 唯一含 U+FFFD 的既有文件：`AGENTS.md`（3 个，**不在本任务授权路径内**，此处仅登记，不处置）。
    （本记录自身也含 U+FFFD——因为它**逐字引用了报告里的 mojibake 样本**。）

## 三、为什么规定的修法不适用

规定的修法是「`s.encode('latin-1', errors=...)` → `.decode('gb18030')`」。该变换的**前提**是字符串里的每个字符码位 ≤ U+00FF（即它本身是 latin-1 误解出来的产物）。

实测：文件里 10 条目标字符串（5 条 title + 5 条 verdict）**全部**是中文，码位 > U+00FF：

```
UnicodeEncodeError: 'latin-1' codec can't encode character '第' in position 425:
ordinal not in range(256)
```

⇒ **逆变换无法施加**（0/10 适用）。按任务要求「若某串还原失败，保留原样并点名」——实际上
**全部 10 条都「还原失败」，但原因不是「不是 GBK 来源」，而是「它们根本就是正确的中文，不需要还原」**。

⚠️ 若有人用 `errors='ignore'` 强行绕过，Python 会**静默丢弃**所有非 latin-1 字符，
结果是 `" 51 "` 这类空壳——**那才是真正的数据损坏**。故本记录明确：**禁止对该文件施加逆变换。**

## 四、报告样本的溯源（穷举结果）

对**本文件的真实字节**做 14 种编码（gbk / gb18030 / cp936 / cp1252 / latin-1 / cp1251 / koi8-r /
big5 / utf-8 / utf-16-le / mbcs / cp850 / cp437 / shift_jis）× 1~3 层链条的穷举结果：

| 读法 | `"title"` 区域实际产出 |
|---|---|
| utf-8（正确） | `"title": "第 51 章"` |
| gbk / cp936 | `"title": "绗� 51 绔�"` |
| cp1252 / latin-1 | `"title": "ç¬¬ 51 ç« ` |
| cp1251 | `"title": "з¬¬ 51 з« ` |
| big5 | `"title": "蝚� 51 蝡�"` |
| koi8-r | `"title": "Г╛╛ 51 Г╚═"` |
| **报告样本** | `"title": ` **`�� 51 ��`** |

⇒ **没有任何一条链条产出报告里的形态。** 报告样本与本文件不构成对应关系。

## 五、报告样本实际属于哪一类损伤

对报告样本 `verdict = 'ȫ�»�е��……'` 逐字节回推：

| 步骤 | 结果 |
|---|---|
| 报告样本的 UTF-8 字节 | `b'\xc8\xab\xef\xbf\xbd\xc2\xbb\xef\xbf\xbd\xd0\xb5\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd'` |
| 该字节流按 **GBK** 解码 | `全□铰伙拷械□斤拷□`（**回到了正确汉字的方向**） |
| 该字节流按 cp1252 解码 | `È«ï¿½Â»ï¿½Ðµï¿½ï¿½ï¿½` |

⇒ 报告样本的原始字节是一个 **GBK 字节流**，且其中「全/章/机/械」等字在往返中**恢复成了
「ȫ/»/е」这类形近但码位不同的字**（`ȫ`=U+022B ≠ `全`=U+5168；`»`=U+00BB ≠ `章`=U+7AE0）。

复算可重现的 Windows 流水线形态（UTF-8 文件 → 按 GBK 读 → 按 GBK 写 → 按 UTF-8 读）：

```
step1 GBK 读：  '"title": "绗� 51 绔�"'
step2 重新编码为 GBK：4187 字节
step3 UTF-8 读：  '"title": "�? 51 �?"'      ← 形态同报告样本（替换字符 + 丢失）
step3 verdict：  '"verdict": "全章机���重复同�?观测流程，唯�?发现�"'  ← 部分恢复成正确汉字
```

⇒ **报告样本的「形状」与本条流水线一致（GBK 往返 + 有损替换），差别只在具体错误处理器与字符集映射。**

**定性结论**：这是**读取侧**（reader-side）损伤——Windows 上「按平台默认编码（cp936）读 UTF-8 文件、
再按 cp936 写回」的经典流水线产物（与 `AGENTS.md` 坑区「Windows 默认 cp936 吞中文」、
「PowerShell 5.1 按 GBK 解码」两族同源）。**磁盘上的字节从未损坏。**
⇒ 正确的对策是**读的时候显式指定 `encoding="utf-8"`**，而不是改数据。

## 六、处置

| 项 | 决定 | 理由 |
|---|---|---|
| 改 `judge_results.json` 的字节 | **不改（0 字节变更）** | 文件本就是合法 UTF-8；施加逆变换会损坏正确中文并破坏与 summary 的逐字一致性 |
| 改 `judge_summary.md` | **不改** | 交叉校验已通过，无需对齐 |
| 改 `scripts/m2_judge.py` | **不改** | 写入路径已显式 `encoding="utf-8"`，无隐患 |
| 本记录 | 新建（一次性校验记录） | 记录核验口径与证据 |

**「还原前 / 还原后」字节数对比**：**4187 B → 4187 B（工作区）；4047 B → 4047 B（git blob）。差异 = 0 字节。**

## 七、可复现的校验脚本

```python
# 一次性核验：确认 judge_results.json 无需任何编码修复
# 运行：.venv/Scripts/python.exe <本文件同名>.py  （或直接粘贴到 REPL）
import json, os

P = r"docs/evaluation/m2-judge/judge_results.json"
raw = open(P, "rb").read()

# 1) 必须能按 UTF-8 解码（不能 ⇒ 真损坏）
text = raw.decode("utf-8")                     # 抛异常即真损坏
assert "�" not in text, "存在替换字符 ⇒ 有损往返"

# 2) 规定的逆变换不适用（对中文抛 UnicodeEncodeError）
data = json.loads(text)
for r in data["results"]:
    for k in ("title", "verdict"):
        try:
            r[k].encode("latin-1").decode("gb18030")
            raise AssertionError("意外：该串可被逆变换 ⇒ 需人工复核")
        except UnicodeEncodeError:
            pass                                     # 预期：中文不可 latin-1 编码

# 3) 与 judge_summary.md 逐字交叉校验
md = open("docs/evaluation/m2-judge/judge_summary.md", encoding="utf-8").read()
for r in data["results"]:
    row = "| %d | %s | %d | %d | %d | %d | %s | %s |" % (
        r["number"], r["title"], r["pacing"], r["style"], r["logic"],
        r["dialogue"], r["score_avg"], r["verdict"])
    assert row in md, "ch%s 行与 summary 不一致" % r["number"]

print("OK: 文件为合法 UTF-8，无需还原；与 summary 逐字一致")
```

**判别要点**（AGENTS.md 坑区「量具默认是什么」「负向结论的强度只配到扫描覆盖面」两族）：

- 本记录的结论是**否定性结论**（「文件没坏」），其强度由**穷举**支撑：14 编码 × 3 层链条
  （约 3,000 条组合）+ 1,025 文件全仓扫描 + 1 条 git 版本史。覆盖面不足时不得说「不存在」。
- 读该 JSON 时**必须**显式 `encoding="utf-8"`；在 Windows 默认 cp936 的管道里读，
  会得到 §四/§五 那种形态的假象——**那不是文件的问题。**
