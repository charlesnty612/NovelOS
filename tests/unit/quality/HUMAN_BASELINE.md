# 人类基线：语料在哪、为什么第三方拿不到、怎么复现

> 本文件是**事实记录**，不是宣传稿。按本仓「声称与实测分离」纪律：下面每一条
> 「已实测」都注明了复算入口；每一条「不可复现」都写明了**哪一部分结论**因此
> 对第三方失效。
>
> 关联文件：
> - 判定点（语料定位 + 「不可得怎么办」）：`tests/unit/quality/_human_baseline_gate.py`
> - 元测试（缺席必须响亮且可判定）：`tests/unit/quality/test_human_baseline_ci_visibility.py`
> - 可分发替代校准器：`scripts/human_baseline_synthetic.py`
> - 校准器用例：`tests/unit/quality/test_human_baseline_synthetic.py`

## 1. 语料在哪

人类基线语料 = **文风锚点书**榜一《快穿之人渣洗白手册》**侯府弧** 19 章 /
37806 可见字，位置：

```
D:/zcodeproject/NovelOS-Content/reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男/
```

即**内容仓**（`NovelOS-Content`）内的 `reference-books/` 命名空间——正文**不进软件仓**
（三仓分离，见 `AGENTS.md` §二 硬规则 5）。

内容仓按 **2026-09-14 用户裁决**「内容仓不推 GitHub」⇒ **软件仓的 `origin/master` 里
`reference-books` 命中 0 个文件**（实测）。

消费这份语料的**测试文件**共 5 个（判别面是**派生**的：凡调用
`gate.require_reference_corpus(` 的即算，见元测试里的
`_corpus_consumer_modules()`）：

| 文件 | 守的是什么 |
|---|---|
| `test_human_baseline_ai_thresholds.py` | 破折号 / 短段阈值的人类侧上限 |
| `test_readability_baseline.py` | 长段 / 对话占比的人类侧上限 |
| `test_dialogue_echo_baseline.py` | 接词回声的人类侧上限 |
| `test_ai_ending_summary_paragraph.py` | 末段切分口径的人类侧对照 |
| `test_continuity_time.py` | 时点矛盾算子的「人类侧机会数为 0」 |

## 2. 现状：CI 里「没跑」曾经等于「绿」

**历史事实（2026-10-19 实测确认）**：这 5 个文件此前各自带一个**裸
`pytest.skip`**（理由形如「内容仓参考书目录不存在：…」）。CI 只 checkout 软件仓 ⇒
语料不存在 ⇒ **5 个文件在 CI 中恒 skip** ⇒ **「人类基线看守在 CI 中从未生效过」，
而 CI 报绿**。

这是坑区「**量具的缺陷被当成被测物的性质**」的新形态：**一个恒 skip 的守卫等于
不存在，但 CI 报绿**。

**改批后的行为（2026-10-19）**：语料不可得不再是无声 skip，而是三件可判定的事
（实现全在 `_human_baseline_gate.py`）：

1. **状态记录**（JSONL，默认 `data/human_baseline_status.jsonl`：**每进程追加一行**，
   读侧按集合取并集——xdist 下 5 个基线文件分属不同进程，「读—改—写一个对象」
   会互相覆盖，实测丢过 5/14 条；追加式不丢）：记下**查过哪些路径**、
   **哪些模块的看守没跑**、**哪些用例因语料缺失而没跑**（逐用例 node id）、
   当时是否处于严格模式。`data/` 在 `.gitignore` 内，且树指纹口径只含
   `packages/tests/scripts/database` 的 `.py`/`.sql` ⇒ 既不污染仓库状态，也不影响
   「跑测前后指纹逐位一致」那道闸门。
2. **一条响亮的警告**：`pytest` 的 warnings summary 里出现一行带
   `HUMAN-BASELINE-CORPUS-UNAVAILABLE` 标记的警告；skip 理由里带同一个标记 ⇒
   CI 日志里「没跑」是**显式的一行**（`-rA` 也能 grep 到），不是空白。
3. **一个可判定的开关**：`NOVELOS_REQUIRE_HUMAN_BASELINE=1` ⇒ 语料不可得时
   **直接判红**（`pytest.fail`）而不是跳过。「缺席是否可接受」由**环境显式表态**，
   不再由「默认跳过」替所有人默默决定。

**默认形态仍是 skip**（CI 常态：内容仓不在 checkout 里）——但那一次 skip 现在
**带记录、带警告、带可翻红开关**。

## 3. 因此，哪些结论对第三方**不可复现**

明确点名（这一节是本文件存在的理由）：

| 结论 | 第三方能不能复现 | 为什么 |
|---|---|---|
| 「破折号阈值 1.0/千字 是按人类基线校准的（人类 0.19/千字）」 | **不能** | 人类侧数字来自不可分发的真实出版小说 |
| 「短段阈值 1.0/千字 是按人类基线校准的（人类 0.212/千字）」 | **不能** | 同上 |
| 「长段 >100 警告 / >140 错误 的依据是人类侧每章至多 1 段、最长 135 字」 | **不能** | 同上 |
| 「对话占比 12% 低于人类基线 16.5%」 | **不能** | 同上 |
| 「接词回声人类侧全弧只有 1 对 / 1 章」 | **不能** | 同上 |
| 「AI-ENDING-SUMMARY 的末段口径在人类侧是真末段（41 字）」 | **不能** | 同上 |
| 「时点矛盾算子在人类侧机会数为 0」 | **不能** | 同上 |
| 「算子的字面覆盖范围与规则名相符（抽样 20 条人工过目）」 | **不能**（人侧样本不可得） | 抽样对象含人类侧语料 |
| **算子本身是活的、阈值是工程判断** | **能** | 检测器代码在软件仓里，谁都能跑 |

⇒ **对外表述纪律**：说「阈值是校准过的」时，必须同时说清「校准用的是**不可分发的
人类基线**，第三方无法独立复核，只能复现**口径**」。**不得**把合成语料的数字当成
人类基线对外引用。

## 4. 可复现的替代路径

### 4.1 合成语料（可分发，仓库内自带）

```bash
python scripts/human_baseline_synthetic.py            # 人读表格
python scripts/human_baseline_synthetic.py --format json
python scripts/human_baseline_synthetic.py --out D:/report.json --format json
```

- 语料 = 本仓库**原创**的两段中文（不抄任何受版权保护的文本）：一段自然叙述
  （阴性半边），一段明显模板化的机器腔（阳性半边）。
- 算子 = 本仓**已有**的公开入口（`packages/core/quality/ai_patterns.py`），
  密度类规则走「阈值 0 探针」取算子自己算出的命中数（**不复制算子正则**）。
- 输出 = 每千字频率 + **R = 模板侧 ÷ 人类侧**（口径同
  `scripts/ai_tone_calibrate.py`）。
- 只读：不连库、不写仓库文件（`--out` 的路径由调用方指定）。

**它能证明什么**：算子在自然文本上是哑的、在模板文本上是响的 ⇒ **量具是活的**；
口径可复算 ⇒ 第三方能跑出同样的数。

**它不能证明什么**：任何**分布**结论。合成语料的分布是「本仓作者写的」分布，
不是「人写的」分布 ⇒ **不得用它改任何阈值**（脚本的报告里也带着这条警告，且
`test_human_baseline_synthetic.py` 有用例钉住这条警告不许被删）。

### 4.2 公版语料（用户自备）

```bash
python scripts/human_baseline_synthetic.py --corpus D:/texts/xiyouji
```

- `--corpus` 接受**文件**或**目录**（目录取其中 `*.txt`，按文件名排序拼接）。
- 适合放**公版**中文原文（如《西游记》《红楼梦》《三国演义》的公版文本）。
- **脚本不联网抓取**：语料从哪来、怎么获取，由使用者决定（版权与获取方式不在
  本仓授权范围内）。`test_human_baseline_synthetic.py` 有一条静态用例钉住
  「脚本里不出现网络访问形态」。

### 4.3 人类基线本身的复算入口（本机有语料时）

```bash
python scripts/ai_tone_calibrate.py --human-file D:/zcodeproject/NovelOS-Content/reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男
python scripts/readability_audit.py
```

### 4.4 交叉验证（2026-10-19 实测）：本脚本的口径与仓库登记的基线**逐位吻合**

把 §1 那份真实基线语料喂给 §4.1 的脚本（`--corpus`，只读），得到的数字与
本仓**登记的基线常量**逐位吻合：

| 指标 | 脚本实测 | 仓库登记值 | 登记处 |
|---|---|---|---|
| 可见字 | 37806 | 37806 | `HUMAN_VISIBLE_CHARS` |
| 段数 | 779 | 779 | `HUMAN_PARAGRAPHS` |
| 短句独立成段 | 0.212 /千字 | 0.212 /千字 | `HUMAN_SHORT_PARA_PER_1K` |
| 破折号 | 0.185 /千字 | 0.19 /千字 | `HUMAN_DASH_PER_1K`（四舍五入） |
| 对话占比 | 0.165 | 0.165 | `HUMAN_DIALOGUE_RATIO` |
| 长段 | 0.185 /千字（= 7 段） | 7 段 | `HUMAN_PARAS_OVER_100` |
| 接词回声 | 0.026 /千字（= 1 对） | 1 对 | `HUMAN_ECHO_PAIRS` |

⇒ **脚本的测量口径与基线登记口径同源**（不是另一套算法）。这意味着：任何拿到
**可分发语料**的人，跑同一个脚本就能得到与本仓登记值**可比**的数字——这正是
第三方最缺的那一块（阈值背后的量化依据）。

## 5. **不得**声称的内容

按「声称与实测分离」，下列说法**不成立**，不得写进任何文档或对外表述：

1. ❌「阈值在 CI 里被验证过」——CI 里那 5 个看守**恒 skip**（历史事实，见 §2），
   CI 从未跑过人类侧断言。
2. ❌「合成语料是等价的基线」——它只是**阳性对照语料**，不是分布模型。
3. ❌「检测器阈值已按人类基线校准 ⇒ 第三方可复现」——人类基线不可分发（§3）。
4. ❌「零命中 = 语料干净」——零命中也可能是**量具测不到**（本仓有实测先例：
   「人类基线 0.00/千字」曾是 `\n\n` 切段造成的量具假象）。本仓的对策是先量
   「量具在该语料上动得了动不了」。
5. ❌「语料缺席 = 阈值没问题」——缺席 = **没人看守**。看状态记录与警告，别看绿灯。

## 6. 同形状、本批**未处置**的一处（如实登记）

`test_ai_ending_summary_paragraph.py` 里还有一处**裸 `pytest.skip`**：生成侧语料
依赖 dev 库 `data/novelos.db`（同样不在 Git 里 ⇒ CI 里也恒 skip）。它属于
**生成侧**前提，不是人类基线，本批未并进 `_human_baseline_gate`（那个 gate 管的是
「人类基线语料」）。形状相同（无声 skip），**登记待排期**。

## 7. 变更纪律

- 语料路径**只归** `_human_baseline_gate.py` 一处管（`REFERENCE_SUBPATH`）；
  消费语料的测试文件**不得**再自带裸 `pytest.skip`（元测试有静态看守）。
- 改「语料缺失怎么办」的策略（默认跳过 / 默认判红）= 改**契约**，须在
  `AGENTS.md` 与 `CHANGELOG.md` 同步登记。
- 任何**新的**人类基线数字入库前，先用 §4.3 的入口复算，并把复算命令写进
  该数字的注释里。

## 8. 九份语料的**可达性登记**（2026-10-23 task-16 Q-5）

证据表 `packages/core/quality/detector_evidence.json` 的 `corpora` 段现在每条都带
`in_version_control` 与 `reproducible_by` 两个**机器可判**字段——本节是它们的
人类可读版本。

⚠️ **口径声明（2026-10-23 批次二十六审查订正）**：本节是**副本、无机器看守**——权威值在
JSON 的 `corpora` 段（受 `tests/unit/quality/test_detector_evidence.py` 的对应用例看守）。
改 JSON 的 `corpora` 时必须**人工同步本节**；本节漂移不会有任何测试变红。
（原文写「两处**必须一致**，由……用例看守」是**不成立的声称**：全仓没有任何用例读本文件。）

**实测结论：9/9 份语料都不在「任何人 clone 后能拿到」的位置。**

| corpus_id | 种类 | 章数 | 实际位置 | `in_version_control` | `reproducible_by` |
|---|---|---|---|---|---|
| `houfu_human_19ch` | human | 19 | 内容仓 `NovelOS-Content` 的 `reference-books/` | `false` | `本机` |
| `dev_db_92ch` | generated | 92 | 本机 dev 库 `data/novelos.db` | `false` | `本机` |
| `dev_db_97ch` | generated | 97 | 同上 | `false` | `本机` |
| `book01_48ch` | generated | 48 | 同上 | `false` | `本机` |
| `book01_short_para_48ch` | generated | 48 | 同上 | `false` | `本机` |
| `book01_arc_48ch` | generated | 48 | 同上 | `false` | `本机` |
| `external_batch_33ch` | generated | 33 | 同上 | `false` | `本机` |
| `human_sample_4` | human | 4 | 同上 | `false` | `本机` |
| `human_sample_1_batch` | human | 1 | 同上 | `false` | `本机` |

**为什么 9/9 都是 `false`**（实测，2026-10-23）：

1. **8 份生成侧/样章语料在 `data/novelos.db`**——`.gitignore` 含 `data/`
   ⇒ `git ls-files --error-unmatch data/novelos.db` 退出码 1（**未被追踪**）。
2. **人类锚点书在内容仓**——按 2026-09-14 用户裁决「内容仓不推 GitHub」，
   内容仓 `origin` 上 `reference-books` 命名空间**命中 0 个文件**（实测）⇒
   连内容仓的 clone 也拿不到这 19 章。

**因此（这条是本节存在的理由）**：证据表里两侧的**全部**实测数字、以及 15 条规则
description 里的 83 个数字，**对第三方一律不可复算**——它们依赖的语料不在任何
可分发位置。⇒ 本仓能主张的只有「**表内自洽**」，不是「**数字为真**」。
复算只能在本机做（入口见各条目的 `source.recompute_entry`）。

**可分发的替代**：`packages/core/quality/detector_evidence_corpus/` 下的 3 份
**合成**语料**在版本控制内** ⇒ 它们能验「阈值口径自洽」（阈值与算子对不对得上），
但**不能**验「阈值定得对不对」（分布结论需要真语料，见 §4）。

