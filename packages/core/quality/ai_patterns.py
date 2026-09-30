"""AI 味/AI 腔确定性检测（去 AI 味）。

纯函数模块，不调用 LLM，全部基于正则与统计。规则对标 oh-story 项目的
``check-ai-patterns.js`` 思路，针对中文长篇小说的章节审校场景做了本地化扩展。

主要 API：
- :func:`scan_ai_patterns`：扫描正文，返回命中规则列表。
- :data:`AI_PATTERN_RULES` / :data:`AI_PATTERN_FORBIDDEN_WORDS`：模块级规则数据，便于扩展。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from packages.core.quality.beat_repeat import (
    DEFAULT_BEAT_MIN_COUNT,
    DEFAULT_BEAT_RATE_PER_1K,
    scan_beat_repeats,
)
from packages.core.quality.continuity_time import (
    DEFAULT_MAX_GAP_CHARS,
    DEFAULT_MIN_BACKSTEP_HOURS,
    scan_time_conflicts,
)
from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 模块级规则数据（易扩展）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 禁用词两层制（2026-09-15 分层重构）
# ---------------------------------------------------------------------------
# 起因：改造前是**一张「命中即报」的表**，结果 67% 的章节被报——实测是
# **忽然**（0.24 vs 人类 0.20 /千字，R=1.18 无区分力）与**突然**（0.19 vs 0）
# 两个中文小说正常用词在撑起全部噪声，而 19 个词里有 15 个两侧都近乎零命中。
# 同一形状（规则宣称的覆盖面 ≠ 实测覆盖面）的第三例，故按实测分层、分层给判定。

# 第一层：硬套话——公文/说明文腔与计划泄漏，本题材内没有正当用法，**命中即报**。
AI_PATTERN_HARD_CLICHES: list[str] = [
    "本章目标",          # plan_json 文本泄漏进正文（硬错误，不属风格问题）
    "与此同时",
    "值得一提的是",
    "总而言之",
    "总的来说",
    "综上所述",
    "空气中弥漫着",
    "冥冥之中",
]

# 第二层：常用叙述弱词——中文小说的正常用词，实测**无区分力或人类侧更高**
# （忽然 R=1.18 · 好像 人类 0.20 对生成 0.01）。不做「命中即报」，
# 只在**同章堆积**时报警：单词 ≥3 次，或本层合计 ≥6 次。
AI_PATTERN_WEAK_WORDS: list[str] = [
    "仿佛",
    "宛如",
    "如同",
    "似乎",
    "好像",
    "不禁",
    "骤然",
    "猛然",
    "忽然",
    "突然",
]

# 向后兼容：并集。既有调用方（chapter_review 的 forbidden_word_hits、题材包禁词
# 扫描、polisher 预检）按整表读，语义不变；变的是**触发条件**（weak 层需堆积）。
AI_PATTERN_FORBIDDEN_WORDS: list[str] = (
    AI_PATTERN_HARD_CLICHES + AI_PATTERN_WEAK_WORDS
)

# 弱词层堆积阈值（同章内）。
_WEAK_WORD_PER_WORD_LIMIT = 3
_WEAK_WORD_TOTAL_LIMIT = 6

# 章尾总结/升华体套话（只在最后一段检测）
_AI_ENDING_SUMMARY_PHRASES: list[str] = [
    "这一刻",
    "从此以后",
    "新的篇章",
    "命运的齿轮",
    "故事的结局",
    "画上了句号",
    "落下了帷幕",
    "一切都结束了",
    "新的旅程",
    "未来可期",
    "时光荏苒",
    "岁月如梭",
]

# 解释腔连接词/句式
_AI_EXPLAIN_PATTERNS: list[tuple[str, str | None]] = [
    # (regex, 简化标签)
    (r"因为[^。]{0,30}所以", "因为……所以……"),
    (r"换句话说", None),
    (r"也就是说", None),
    (r"换言之", None),
    (r"究其原因", None),
    (r"这意味着", None),
    (r"不难发现", None),
    (r"众所周知", None),
]

# 破折号阈值。沿革（每次收紧都留痕）：6（初版）→ 3.5（2026-09-15 按本仓旧语料
# p85；原值 6 是死规则——本仓实测 max 4.73 都够不到）→ **1.0（2026-09-16 按人类
# 基线书收紧）**。
# 依据（2026-09-16 实测，复算入口 scripts/ai_tone_calibrate.py）：文风锚点书榜一
# 《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字 **0.19/千字**，而我们新书 01
# 全弧 63625 字 **1.73/千字**（R=9.34）。3.5 落在两者**之外**——连我们自己的均值
# 都够不到，即 AGENTS.md 点名的「阈值落在实测分布之外 = 死规则」形状；而真正的
# 达标线接近零。取 1.0：对榜一基线留约 5 倍宽容（0.19×5≈0.95），对 1.73 立即报警。
# 判别：tests/unit/quality/test_human_baseline_ai_thresholds.py（改回 3.5 必红）。
# 语义为 float（本常量旧注解误写 int，2026-09-16 一并订正，与短段常量同款）。
DEFAULT_DASH_THRESHOLD_PER_1K = 1.0

# ---------------------------------------------------------------------------
# 2026-09-15：外部对照研究移植的四条算子
# ---------------------------------------------------------------------------
# 来源：lieflat-less-ai-tone（283 万字对照语料 / 629 篇 / 26 项候选特征）——
# 11 项通过检验（判定标准 R = 生成侧频率 ÷ 人类侧频率 ≥ 1.25），本组取其中
# 四项可正则化者。该研究同时证否了三项流行认知（比喻 / 正文设问 / 句长均匀度
# 均非生成侧偏高，其中设问方向相反：人类是生成侧的 17 倍），故本组不扩禁用词表。
#
# 本仓实测（33 章 / 87,599 字 生成侧 vs 4,843 字 人类样章；口径与来源同）：
#   破折号 R=2.83（研究 3.0）· 对举结构 0.76/千字（研究生成侧 0.73）·
#   段首短句 R=6.03（研究 4.4）——三项在本域（中文长篇网文）方向一致。
#
# 方法纪律（对齐来源研究的操作规程，亦与本仓「声称与实测分离」同源）：
# 算子在采信频率前**必须抽样检视命中实例**——正则匹配字面形态而不解析语义，
# 算子过宽会把正常写法计入。精确率核查入口：``scripts/ai_tone_calibrate.py``。
# 该研究公开的六次测量失误全部是「算子覆盖范围宽于规则名称」这一形状。

# 对举结构「不是 A 而是/是 B」——研究 R=3.4。人类侧亦有（0.22/千字），
# 故按**密度**报警而非单例判定。阈值取本仓实测值（0.76/千字）约两倍。
DEFAULT_CONTRAST_PAIR_RATE_PER_1K = 1.5

# 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名——见下）。
# 来源研究区分力最强项 R=4.4 是「段首直接给评价/结论而不标示评价对象」；
# 本仓的可正则化近似（段首 ≤12 字、无回指词、非对白、非人称起首）**抽 12 条
# 命中逐条看**：命中是「林策开口了」「万卷阁开了」「三天」这类**叙述短句独立成段**，
# 不是评价性评论；人类样章同样有该写法。故按算子**实际覆盖范围**改名登记，
# 不冒用来源特征名（该研究公开的六次失误全是这个形状）。
# 阈值沿革：5.5（2026-09-15，取本仓**旧语料**逐章 p90）→ 1.0（2026-09-16）→
# **1.0 不动（2026-09-27 复核）**。两次「1.0」的论证链都出过错，第二次是量具：
# 2026-09-16 写的依据是「榜一人类基线 0.00/千字，对生成侧 2.62 立即报警」，而那个
# 0.00 **不是测出来的**——`count_short_paras` 当时独自用 ``\n\n`` 切段，而榜一
# 侯府弧 19 章的 ``\n\n`` 总数 = 0，19 章被读成 19 个整章大段，规则在该语料上
# **构造上不可能命中**（详见 :func:`_split_paragraphs`）。口径统一为 ``\n+`` 后重测
# （2026-09-27，两侧语料：`\n\n`` 总数 = 人类 0 / 生成侧 3445）：
#   榜一侯府弧 19 章 / 37806 可见字：改口径 10 命中 / 0.265 每千字 / 6 章有命中 /
#   2 章超阈值；再叠加 `名字：` 说话人行守卫——该守卫**只替掉 2 处**：1 处人名说话人行
#   （`陆明河：“……”`）+ 1 处字段行（`宋沛年：`，人类书里也是排版件形态）→ **8 命中 /
#   0.212 每千字 / 6 章有命中 / 1 章超阈值**。
#   prj_bcb9d1930bd4 48 章 / 125024 可见字：257 → 260 命中（2.056 → 2.080，仅 ch33
#   一章计数 2→5）；叠加说话人行守卫后 → **260 命中 / 2.080 每千字 / 34 章超阈值**
#   （守卫在生成侧不改变命中数，见 :data:`_SPEAKER_LINE_RE` 的宽度校准）。
# 分离度 2.080 / 0.212 ≈ **9.8×**，与 2026-09-16 的 2.62/0.00 在同一量级，故
# **阈值 1.0 维持不变，本次修的是论证链不是阈值**。
# 「人类零容忍」的准确说法（2026-09-27 二次订正）：人类侧 8 命中逐条分类为
# **散文 5 / 分隔线 2 / 平台标记 1**，唯一超阈的 ch19（rate 2.29）靠的是 3 条
# **非散文**行（2 条分隔线 + 1 条 `【继续】`），该章散文只有 1 条 = 0.76/千字。
# 故**散文口径下人类零容忍成立**：5 命中 / **0.132 每千字 / 0 章超阈值**；登记的
# 1/19 章超阈完全由非散文行构成。（纯排版件/平台标记的排除规则**尚未实现**——那需要
# 自己的 20 条抽样论证，登记为待办；在此之前全口径的 0.212 就是实测值，不加修饰。）
# 判别：tests/unit/quality/test_human_baseline_ai_thresholds.py（改回 5.5 必红；
# 把段切分改回 ``\n\n`` 同样必红——人类侧命中数会掉到 0）。
# （初版算子只看段首句，把「灯芯闪了一下。苏婉清没有出声…」也算成短段，
#   分布虚高到均 5.06；修正为「整段就是那一句短句」后按新分布重定阈。）
DEFAULT_SHORT_PARA_RATE_PER_1K = 1.0

# 拟人化喻体（以职业/角色名词作喻体）——研究 R=7.3。
# **精确率警告**：正则分不出研究指出的真正差异——生成侧偏好「理想化的职业人格」
# （像一位智慧的导师），人类侧偏好「具体个人」（像一个老师傅）；二者字面同形。
# 故只在**同章出现 ≥N 次**时报警，不做事例级判定。
DEFAULT_ANTHRO_VEHICLE_MIN_COUNT = 3

# 译文句式：**不迁移**（2026-09-15 抽样核查后的裁决）。
# 来源研究在说明文语料上得 R=2.6~5.3，但本仓实测：生成侧 0.59/千字 vs
# 人类 1.15/千字（R=0.49，**方向相反**）；且「过长前置定语」算子抽 97 条命中，
# 95 条是误报（「看着两人的背影消失在堂后的门槛里。」根本不是长定语）。
# 精确率不合格 + 无区分力 → 两条都不满足，按纪律废弃，不保留死代码。
# 这条同时是「来源研究的结论不能整体照搬」的实证：体裁差异真实存在。

# ---------------------------------------------------------------------------
# 2026-09-17：可读性算子（长段 / 对话占比过低）+ 撤回其指标化产物（接词回声）
# ---------------------------------------------------------------------------
# 来源：书 prj_bcb9d1930bd4 全弧 48 章 / 122350 可见字 的读者反馈「可读性差」，
# 与文风锚点书榜一《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字 逐指标对照
# （复算入口 scripts/readability_audit.py，两处数字均由该脚本只读复现）：
#
#   指标                     榜一（人类基线）        我们（prj_bcb9d1930bd4）
#   单段 >100 可见字段落      7 段（779 段的 0.9%）   133 段（3061 段的 4.3%）
#   单段 >140 可见字段落      0 段                    16 段（最长 212 字）
#   每章 >100 字段落数        至多 1 段（19 章里 7 章） 最多 9 段（ch30）
#   对话占比（引号内可见字）   16.5%（6229/37806）     11.4%（13941/122350）
#
# 「单段 >120 字」是手机阅读的头号杀手（一段 5~6 行实心字），改造前无任何算子看守。
# 段切分口径见 :func:`_split_paragraphs`（``\n+``——外部锚点书是一行一段）。

# 长段阈值：单段 >100 可见字 → warning 档；>140 → error 档。
# **堆积门**（照弱词层 / 拟人喻体「人类侧同样有的写法不做命中即报」纪律）：
# 人类基线每章至多 1 段超 100 字（7/19 章各 1 段，最长 135 字）——单段超长是正常
# 写作，故 warning 要求**单章 ≥2 段**；而 >140 字段落在榜一 779 段里零出现，
# 单段即判 error（我们 16 段、最长 212 字，是真正的排版病）。
DEFAULT_LONG_PARA_WARN_CHARS = 100
DEFAULT_LONG_PARA_ERROR_CHARS = 140
DEFAULT_LONG_PARA_MIN_COUNT = 2

# 对话占比阈值：**只有 <12% → warning 这一档**（<8% → error 档已于 2026-09-17 撤回，
# 见下）。依据：榜一 16.5%（人类基线）· 番茄男频主流 25~40% · 本仓题材包
# style_constraints 对话区间 10~32%（我们 11.4%，贴着题材区间地板）。
# **口径**：对话＝成对 “…”（U+201C/U+201D）内的**可见字**；分母＝全章可见字
# （与 wordcount.visible_chars 同式）。落库前经 quality.normalize 统一引号形态，
# 故“” 是权威形态（「」/英式直引号在落库时已规整；归一化前调用会低估）。
# 注意与 guardrails._dialogue_chars 的差别：后者只认 ASCII `"` 且分母是 `len(draft)`
# ——两处口径不同源，本规则不做「对话占比过低」以外的判定，也不改那处。
# **章级提示而非违规判定**：单章方差极大（榜一 ch1 0.0% / ch19 40.6%），
# 弧级看守见 tests/unit/quality/test_readability_baseline.py。
#
# **撤回 error 档（2026-09-17，用户实测反馈）**：对话占比是**可优化指标**，不是
# 质量判据——逼模型凑指标必然产出灌水。原 <8% → error 档连同题材包/生成驱动里的
# 「对话占比 ≥20%」硬指标一起施行后，用户实测反馈「你现在的改法书一点就不好读了，
# 全是重复性的对话灌水，上一句说了啥，下一句接着重复一遍」，实证：
#   - ch3 验仓戏**为凑对话编造对白并破人设**：让主角主动交代「七成发霉率」、主动
#     透露「三天后什么价」（价签是他唯一底牌）；
#   - ch33 六句对白全是**接词回声**（「三百人，够不够守城？」→「三百。」→「三百人，
#     吃什么？」→「够七天。」→「七天之后呢？」→「七天之后，看你守不守得住。」），
#     每句复述上一句的词，六句下来局面毫无变化。
# 章级口径本身也会误报：人类锚点书 19 章里 8 章低于 12%、**首章 0.0%**——人类作者
# 写得出整章无对话的动作戏。故本规则**只作提示不作闸门**：任何情况只返回 warning，
# 绝不进 errors 桶（判别：tests/unit/quality/test_dialogue_low_warning_only.py）。
# 由此暴露的真实缺陷形状（指标化写作目标的副作用）见 F-18 与下面的 AI-DIALOGUE-ECHO。
DEFAULT_DIALOGUE_LOW_WARN_RATIO = 0.12

# ---------------------------------------------------------------------------
# 2026-09-17：接词回声（AI-DIALOGUE-ECHO）——把上面那个副作用的**症状**本身
# 做成算子：对话不该是「接龙复述」，而该是**信息递增**（每句带来新东西）。
# ---------------------------------------------------------------------------
# 为什么是「相邻对白」而不是「对话占比」：占比逼出来的病有具体形状可正则化，
# 而占比本身没有（人类 0.0% 的章合法）。两条一正一反：撤掉错误的闸门，留下症状的探针。
#
# 实测（复算入口 scripts/readability_audit.py，两侧语料同口径；摘要在该脚本汇总行）。
# **2026-09-27 复算订正**（此前登记的「26 对 / 20 章」已过期，且早于本次定义修正）：
#   修正前口径：书 prj_bcb9d1930bd4 48 章 → **34 对 / 24 章命中**（相邻对白对 674）；
#              榜一侯府弧 19 章 → 1 对 / 1 章（相邻对白对 61）。
#   修正后口径（重叠系数可比性守卫生效，见下）：**23 对 / 18 章**；人侧仍 1 对 / 1 章。
#   被守卫剔掉的 11 对（后句 ≥ 2 × 前句）**不是「全部合法写法、0 条真信号」**——那是
#   2026-09-27 首轮交付里的过强断言，对抗审查已打回。按逐条判档：
#     - 合法写法 6：ch13 引语式回应 / ch17 称呼+展开 / ch31 复述关键词后逐条驳斥 /
#       ch44 `即日。`+展开 / ch36 引词后驳斥 / ch9 议价引数；
#     - **存疑 3**：ch14 / ch32 让步式回应 / ch35 发令者复述命令以解释；
#     - **真信号 1**：ch44 `他们敢。`→`他们敢。他们正愁没理由。`（下方详述）。
#   **已知代价（登记，不改守卫）**：守卫在「复述关键词后补理由」家族内**与回声度反
#   相关**——它按长度比剔除，恰好剔掉这一族里字面重复较高的那些。ch44 该对
#   `他们敢。`(3 字) → `他们敢。他们正愁没理由。`(10 字) 是**逐字复述对方整句
#   （3/3）再追加理由**，字面重复比例 100%，比被判为真信号且**仍在守卫内**的 ch45
#   `他们退了。`→`他们退了。`（recall 1.00）只低一个量级。同一把尺子得出相反判档，
#   说明边界确实切错了家族。审计自己标的 3 条「存疑」（recall 0.83 / 1.00 / 0.75）
#   全部由 recall 分支保住、**一条都没被守卫碰**——即守卫的伤害面就是上面这 1~2 条。
#   替代判据实测过（`opening_repeat >= 2 且 共同字/后句 >= 0.25`）并**被否决**：人类侧
#   从 1 对涨到 4 对 / 3 章，更差。守卫本身站得住，**问题在判档措辞，不在实现**。
#
# **重叠系数的分母缺陷（2026-09-27 定性）**：`overlap = 共同实义字 ÷ min(前,后)`
# 是**对称**度量（问「两句用词是不是同一批」），而 `recall = 共同实义字 ÷ 后句`
# 是**非对称**度量（问「新句里有多少比例是复述」）。两种问法各自成立，但共用
# `min()` 分母会在一种形态下**测不到它声称的东西**：后句是前句的超集时
# （短称呼 + 长展开），`min()` 恒等于前句的字数，只要长句包含短句的字就得到
# ``overlap = 1.00``——那衡量的是「长句含短句的字」，不是「用词是同一批」。
# 实测 ch17 `“林昭！”` → `“林昭，你跟我走，茶馆比县仓安全。”`：前句实义 2 字、
# 后句 13 字、recall 2/13 = 0.15、overlap 1.00——**recall 说「后句 85% 是新东西」，
# overlap 却在说「完全重合」**，后者被前者证伪。该形态不携带回声信息。
#
# 守卫（:data:`_DIALOGUE_ECHO_ASYMMETRY_LIMIT` + :data:`_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS`，
# 合起来即 :attr:`DialogueEchoPair.overlap_comparable`）：**不对称是原则性的，
# 不是随手选的 2 倍**——recall 的分母取后句是设计使然，故「后句更短」那一侧由
# recall 正确兜住（`见周账房了？`→`见了。` recall 1.00 是「直接应答」，是登记在册的
# 必命中形态）；只有「后句是超集」这一侧 recall 必然走低（实测 11 对的 recall
# 落在 0.07~0.33）而 overlap 被机械拉到 1.00，才需要守卫。实测「两侧都只有 2 字」
# 时 overlap 同样恒为 1.00（3 对 2 字齐诵 `不退。`→`不退！` 即如此），故另设最小
# 可比字数 3。
#
# ⚠️ **11 对是薄样本**：这是**实测锚定**（本仓生成侧 34 对全量 + 人侧 1 对），
# **不是分布结论**，不构成「任何后句 ≥2 倍前句的对白都合法」的一般命题。
#
# **2.0 这个值是定义性选择，不是标定所得**（2026-09-27 对抗审查订正——首轮交付写成
# 「2.0 取自 11 对的下界」是**循环论证**：被剔集合的实测比值正是
# 2.00 / 2.50 / 2.83 / 3.00 / 3.33 / 4.07 / 5.18 / 6.00 / 6.50 / 6.50 / 11.43，
# 2.0 恰是该组最小值，把阈值放在待判集合的最小值上必然全剔，那是恒真式不是证据）。
# 参数扫描实测（本仓语料，min<3 守卫固定为 3）：
#   limit 1.5 / 1.88 / 1.95 / **2.0** → 生成侧**恒 23 对 / 18 章**（区间内无影响）
#   limit 2.05 / 2.2 / 2.5            → 24 对 / 19 章（回来的是 g32，比值**恰为 2.00**：
#                                        `back < 2.0*front` 即 `12 < 12` 为假被剔，
#                                        `2.05*6 = 12.3 > 12` 即真放回）
#   limit 2.83                        → 25 对 / 20 章（+g35 `你守后队`，比值 2.50）
#   limit 3.5 / 5.0                   → 28 / 29 对
#   人类侧在 [1.5, 5.0] 全程**恒 1 对 / 1 章**，无区分力（61 对候选的比值
#   p50 1.27 / p75 1.88 / p90 2.88 / p95 4.67 / max 24.50，`back>=2×front` 占 21.3%）。
# 结论：该值的正当性来自**度量的可解释性**（后句是超集 ⇒ `min()` 分母被前句钉死 ⇒
# overlap 读数机械失效），**不来自任何精度/召回标定**；精度代价见上面「已知代价」段。
#
# 两种形态分开定阈（实测两种都真实存在；只留其一都会漏掉用户实证里的对白）：
#   ① **复述型** ``recall >= 0.70``：后句实义字中 ≥70% 出现在前句（把对方的话基本
#      复述一遍）。例（2026-09-27 逐条复算的实测值）：`见周账房了？`→`见了`
#      （recall 1.00、开口 1 字）、`账上的数我知道。上月你在这儿当值？`→`我当值`
#      （recall 1.00）、`先把库房那几袋茶处理了。`→`那十几袋茶？`（recall 0.80）。
#      - 2026-09-27 订正：旧注释把 `你守后队。`→`后队有人殿后，你在那儿。`（实测
#        recall **0.30**）列为本形态样例，**举错例了**——它是下面的接词型命中
#        （重叠 0.75、开口 2），不是复述型。
#   ② **接词型** ``overlap >= 0.66`` 且**开口连续重合 ≥2 字**且**两侧可比**
#      （:attr:`DialogueEchoPair.overlap_comparable`）：后句开口就接对方的词。
#      例（均为实测命中且两侧可比）：`够七天。`→`七天之后呢？`（0.67、开口 2）、
#      `他临走前那晚…借据造假。`→`借据呢？`（0.67、开口 2）。
#      - 0.66 而非 0.70：上述必命中样例实测 2/3 = 0.67，0.70 会漏掉它（阈值落在
#        样例分布之外）；人侧 19 章在该形态上 0.66 与 0.70 **同为 0 对**——即 0.70
#        只减召回、不加精确率。
#      - 开口 ≥2 字是**精确率守卫**（实测逼出来的）：放开到 0，人侧仍 1 对不变，而
#        生成侧多出 22 对，**实测形态是「前句的词出现在后句中段/末尾」**——追问的
#        对象（`北山驿路。`→`你怎么知道北山驿路的事？`）、长回答里夹带
#        （`林出纳。`→`永丰号那边传话…说林出纳的名字已经上了册子。`）、术语复指
#        （`你这价——`→`三天后还你等量的粮。…`）。这类**开口不接词**，与接词无关。
#        - 2026-09-27 订正：旧注释称该守卫挡住「短促称呼/术语 + 长回答」型误报，
#          **实测没挡住**：`林昭！`→`林昭，你跟我走，茶馆比县仓安全。` 的开口连续
#          重合是 2，照样命中——因为后句恰好**以该称呼起首**。它挡的是「称呼出现在
#          后句中段」那一种（见上），不是全部。该形态改由重叠系数的可比性守卫拦。
# 精度口径：算子只看字面重合，**不判断对白是否有后果**——信息递增但用词撞车的正常
# 对白会被计入（人侧那 1 对即此类：「…我们明天一早就在城门口等吧」→「好啊，明儿个
# 我们就在城门口等他们。」），故 severity 恒 warning、附 samples 供人工过目。
DEFAULT_DIALOGUE_ECHO_MIN_RATIO = 0.70
DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP = 0.66
DEFAULT_DIALOGUE_ECHO_MIN_OPENING = 2
# 单侧实义字下限：单字应答（「嗯。」「好。」）不含信息、判它没有意义，不参与成对比较。
# ——这是**成对资格**的下限；重叠系数另有更高的**可比性**下限（下面两个常量）。
_MIN_DIALOGUE_ECHO_CHARS = 2
# 重叠系数的**可比性**下限（两项缺一即视为 overlap 不可解释，见上面「分母缺陷」段）：
#   ① 任一侧实义字 < 3 时 overlap 必为 1.00（分母落在 2 字那一侧），该读数不携带
#      信息；
#   ② 后句 ≥ 2 × 前句时后句是超集，min() 分母被前句钉死，overlap 同样必为 1.00。
#
# **两条守卫各自的实测作用**（2026-09-27 二次订正；首轮交付写的「① 覆盖 8 对」与
# 「① 是 0 增量 no-op」不能同时为真，现按触发 / 净抑制分列）：
#   prj_bcb9d1930bd4 48 章 / 34 对无守卫命中：
#     ① 触发 **11 对**、② 触发 **11 对**、①∩② = 3 对、① 独有 = 8 对；
#     ① 独有的 8 对**全部 recall ≥ 0.70**（`卷走？` / `退了！`×2 / `来了。` /
#     `三年。` / `走了。` / `即日。` / `见了。`）⇒ **① 独有抑制 0 对**，净抑制 11 对
#     全部来自 ②。① 是**定义性**守卫：撤掉它，`退了。`→`退了呀。` 这类 2/3 字对
#     会凭恒等于 1.00 的读数命中（合成用例判别）。
#   全库 196 份 draft / 无守卫 71 对：① 触发 21 对、① 独有 12 对、①∩② = 5 对；
#     ① 独有的 12 对**同样全部 recall ≥ 0.70** ⇒ **独有抑制 0 对**（跨项目一致）。
# 真信号 3 对（`不退。`→`不退！`×2 的 2→2、`他们退了。`→`他们退了。` 的 4→4）由
# recall 命中，不因两条守卫而丢失。
# ⚠️ 薄样本，实测锚定非分布结论。
_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS = 3
_DIALOGUE_ECHO_ASYMMETRY_LIMIT = 2.0

# 两条可读性规则的**最小判定长度**（照 _MIN_PROSE_CHARS_FOR_DENSITY 先例）：
# 短片段（单测样例、引文、题记）没有排版/配比的统计意义，一律不判。
# 生产章节目标 3000 字、带宽下限 1200，落在守卫之上不受影响。
_MIN_PROSE_CHARS_FOR_READABILITY = 600

# 命中数超阈值时 severity 由 warning 升级为 error
_SEVERITY_UPGRADE_LIMITS: dict[str, int | float] = {
    "AI-FORBIDDEN-WORD": 10,  # 总命中次数
    "AI-EXPLAIN-TONE": 5,
    "AI-PRONOUN-PILE": 5,
    "AI-TRIPLET-OPENING": 5,
}


@dataclass(frozen=True)
class AiPatternRule:
    """单条规则元数据，用于文档化与可扩展配置。"""

    rule_id: str
    severity: str  # 'warning' | 'error'，默认等级
    message: str
    description: str = ""


AI_PATTERN_RULES: list[AiPatternRule] = [
    AiPatternRule(
        rule_id="AI-FORBIDDEN-WORD",
        severity="warning",
        message="AI 高频套话/禁用词命中：{words}",
        description="检测仿佛、宛如、不禁、骤然、与此同时、值得一提的是、总而言之、空气中弥漫着等高频 AI 腔词。",
    ),
    AiPatternRule(
        rule_id="AI-TRIPLET-OPENING",
        severity="warning",
        message="连续 {count} 句以相同两字词「{word}」开头，疑似句式套路",
        description="连续 3 句及以上以同一个两字词开头。",
    ),
    AiPatternRule(
        rule_id="AI-PRONOUN-PILE",
        severity="warning",
        message="「他/她」主语排比堆砌 {count} 句",
        description="连续 3 句及以上以「他」或「她」开头，提示主语单调。",
    ),
    AiPatternRule(
        rule_id="AI-ENDING-SUMMARY",
        severity="warning",
        message="章尾出现总结/升华体套话：{words}",
        description="结尾段含这一刻、从此以后、新的篇章、命运的齿轮等。",
    ),
    AiPatternRule(
        rule_id="AI-PUNCT-ABUSE",
        severity="warning",
        message="破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description=(
            "每千字「——」或「……」超过阈值（默认 1.0）。2026-09-16 按人类基线书"
            "（榜一侯府弧 0.19/千字，我们 1.73/千字）由 3.5 收紧——见常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-EXPLAIN-TONE",
        severity="warning",
        message="解释腔高密度：{count} 处（{labels}）",
        description="「因为……所以……」「换句话说」「也就是说」等解释连接词高频出现。",
    ),
    AiPatternRule(
        rule_id="AI-CONTRAST-PAIR",
        severity="warning",
        message="对举结构「不是……而是……」密度偏高：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description="外部对照研究 R=3.4（生成侧 0.73 / 千字，人类侧 0.22）。按密度报警。",
    ),
    AiPatternRule(
        rule_id="AI-SHORT-PARA",
        severity="warning",
        message="短句独立成段密度偏高：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description=(
            "段落由一个 ≤12 字短句构成（无回指词、非对白含 `名字：` 说话人行、"
            "非人称起首），即「节拍器式」短段行文。人类基线书榜一侯府弧 "
            "**0.212/千字**（19 章 8 命中 / 6 章有命中 / 1 章超阈——该章 3 条命中"
            "全是非散文行，散文口径 0.132/千字 0 章超阈），我们新书 01 全弧"
            "**2.080/千字**（48 章 260 命中），分离度 9.8×，阈值 1.0；"
            "2026-09-27 修正段切分口径——此前登记的「人类 0.00」是 ``\\n\\n`` 切分"
            "造成的量具假象，见常量注释。取自来源研究「段首零回指评论」的可正则化"
            "近似，抽样核实后按实际覆盖范围改名。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-ANTHRO-VEHICLE",
        severity="warning",
        message="拟人化喻体（职业/角色类）同章 {count} 处",
        description=(
            "休眠守卫，**未校准**：来源研究 R=7.3（生成侧偏好理想化职业人格作喻体），"
            "但本仓生成侧与人类侧均零命中，无数据可校。正则亦无法区分研究指出的"
            "「理想化职业人格」（生成侧）与「具体个人」（人类侧，如「像一个老师傅」），"
            "故仅按同章次数报警、不做单例判定；一旦命中即为新信号。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-LONG-PARA",
        severity="warning",
        message="长段堆积：单章 {count} 段超过 {warn_chars} 可见字（最长 {max_chars} 字）",
        description=(
            "单段可见字超限（手机上一段 5~6 行实心字，可读性头号杀手）：>100 字计入长段，"
            "单章 ≥2 段报警（人类基线书每章至多 1 段）；任一段 >140 字直接 error"
            "（人类基线书 779 段中零出现）。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-DIALOGUE-LOW",
        severity="warning",
        message="对话占比过低：{dialogue_ratio:.1%}（{dialogue_chars}/{total_visible_chars} 可见字）",
        description=(
            "对话（成对“…”内的可见字）占全章可见字比例 <12% → **warning（唯一档位）**；"
            "人类基线书 16.5%、番茄男频主流 25~40%、本仓题材包区间 10~32%。"
            "可见字 <600 的片段不判（统计无意义）。**2026-09-17 撤回 <8% error 档**："
            "对话占比是可优化指标，逼模型凑数会产出灌水对白；低对话章合法（人类锚点"
            "首章 0.0% 整章无对话）。本规则只作提示、不作闸门，绝不进 errors 桶——"
            "症状探针见 AI-DIALOGUE-ECHO。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-DIALOGUE-ECHO",
        severity="warning",
        message="相邻对白接词回声 {count} 对（例：{sample}）",
        description=(
            "相邻两句对白之间「后句接前句的词」——复述型（后句实义字 ≥70% 出现在前句）"
            "或接词型（两侧实义字可比、开口连续重合 ≥2 字、重叠系数 ≥0.66）。"
            "实测：本仓 48 章 34 对 / 24 章命中（重叠系数可比性守卫生效后 23 对 / "
            "18 章），人类锚点书 19 章 1 对（信息递增但用词撞车，合法写法）。"
            "算子只看字面重合，**不判断对白是否有后果**，故 severity 恒 warning、附 "
            "samples 供人工过目。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-BEAT-REPEAT",
        severity="warning",
        message="同章远距小句复现 {count} 处（例：「{sample}」）",
        description=(
            "**字面代理**算子（名字里的「节拍重播」是它代理的对象，不是它测量的东西）："
            "句读片段之间**最长公共子串** ≥6 字、同时覆盖两段片段各自 ≥60% 字面、两处"
            "相距 ≥200 字——即同一段文字素材在章内被搬用了两次以上。补的缺口是"
            "**shingle / trigram 只抓复制粘贴、抓不到「同一节拍换一套词重播」**"
            "（ch2 章尾收据画面写了三遍，shingle 比 0.0030 判为干净）。"
            "实测：本库 92 章 307 处（1.17/千字）；人类锚点书 19 章 2 处（0.05/千字），"
            "**没有一章**达到堆积门 ≥2 处。系统抽样 52 条人工过目：43 真 / 9 误，"
            "误报为「专名/术语复指」与「共用骨架 + 不同宾语」两类。"
            "**不覆盖**：语义改写型重复（字面零重合）、数字/计量串复现（数字守卫显式"
            "排除）、跨章复现、宏观结构重复。severity 恒 warning（提示非闸门）。"
            "阈值与依据见 beat_repeat 模块 docstring 与常量注释。"
        ),
    ),
    # -----------------------------------------------------------------------
    # 2026-09-19：章内时点矛盾（continuity_time 模块；本组**不是 AI 味**，是连续性）
    # -----------------------------------------------------------------------
    # 由来：``guardrails.timeline_consistency`` 只校验 state delta（时间线状态机），
    # 全仓没有任何算子读**正文里的时间标记**——真实成书里「凌晨两点十七分」开场、
    # 同场景不隔断地写到「天际线已经有一层淡淡的亮了」，一条规则都没报。
    # 两条规则都走本表是为了复用**既有接线**（basic_checks → review_report.warnings
    # 与 deterministic_hints → critic payload），rule_id 用独立命名空间 ``CONT-``
    # 标明它不是 AI 味算子。severity 恒 warning、不含 error 路径。
    # 校准证据（92 章 / 人类基线 19 章逐条抽样表）见 continuity_time 模块 docstring。
    AiPatternRule(
        rule_id="CONT-CLOCK-DAYBREAK",
        severity="warning",
        message="深夜时钟与天亮标记矛盾：{count} 处（例：{sample}）",
        description=(
            "**字面**算子：深夜档钟点（前缀 凌晨/半夜/深夜/夜半 + 钟点，如「凌晨两点"
            "十七分」）与其后**同章**的天亮类标记（天亮/破晓/黎明/晨光/晨曦/天色泛白/"
            "清晨/早上…）配对，间距 ≤3000 字且中间**无时间推进标记**即报。"
            "它量的不是「叙事时间是否合理」，而是**两个字面标记之间的直接冲突**。"
            "**不覆盖**：时长合理性、倒叙/回忆（只靠黑名单近似排除，故回述型会漏）、"
            "无字面标记的场景、裸钟点（本仓实证「三点二十六分」= 下午 15:26，故不判早晚）。"
            "实测：生成侧 1 处 / 92 章（真命中 ch_2d2b57090ecd）；人类基线书 19 章"
            "**零个钟点标记**，机会数为 0，故人类侧数字不含信息、不可作零误报证据。"
        ),
    ),
    AiPatternRule(
        rule_id="CONT-TIME-BACKSTEP",
        severity="warning",
        message="同章相邻时点回退：{count} 处（例：{sample}）",
        description=(
            "**字面**算子：同章**相邻**两个可排序时点（钟点按前缀折算为一天内小时；"
            "时辰按起始小时折算：午时 11:00 / 亥时 21:00），后者比前者早 ≥2 小时即报。"
            "三条守卫：跨午夜顺行不算（23:47 → 次日 03:42）、左邻计划语不算"
            "（「明天辰时」）、右邻期限语不算（「子时之前」「子时将近」）。"
            "**不覆盖**：无字面标记的场景、裸钟点（歧义）、跨章时间线、时长合理性。"
            "实测：原始 6 条候选中 5 条是误报（上列三类守卫正是抽样抓出来的），"
            "加守卫后生成侧 1 处（真命中 prj_5be9256febad ch6：亥时 → 午时无交代回退）；"
            "人类侧 0 处且机会数为 0（基线书仅 2 处「时辰」，都不是时点）。"
        ),
    ),
]


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------


def _leading_chinese_bigram(sentence: str) -> str | None:
    """取句子开头（去空白/前导标点后）的前两个连续中文字符。"""
    cleaned = sentence.lstrip()
    if not cleaned:
        return None
    m = re.match(r"[\u4e00-\u9fff]{2}", cleaned)
    return m.group(0) if m else None


def _sentences(prose: str) -> list[str]:
    """按中文句末标点切分句子，过滤空句。"""
    parts = re.split(r"[。！？；\n]+", prose)
    return [p.strip() for p in parts if p.strip()]


def _last_paragraph(prose: str) -> str:
    """取正文最后一段（按空行分隔），若无空行则取全文。"""
    paragraphs = [p.strip() for p in prose.split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else prose


def _maybe_upgrade_severity(rule_id: str, count: int | float, base_severity: str) -> str:
    """命中数超过规则阈值时，把 warning 升级为 error。"""
    if base_severity != "warning":
        return base_severity
    limit = _SEVERITY_UPGRADE_LIMITS.get(rule_id)
    if limit is not None and count >= limit:
        return "error"
    return "warning"


# ---------------------------------------------------------------------------
# 各规则扫描器
# ---------------------------------------------------------------------------


def _scan_forbidden_words(prose: str) -> list[dict[str, Any]]:
    """禁用词两层扫描（2026-09-15 分层）。

    - **硬套话层**：命中即报（`AI_PATTERN_HARD_CLICHES`）。
    - **常用弱词层**：仅同章**堆积**才报——单词 ≥ ``_WEAK_WORD_PER_WORD_LIMIT``
      次，或本层合计 ≥ ``_WEAK_WORD_TOTAL_LIMIT`` 次。理由见模块内分层注释：
      这些是中文小说正常用词，实测无区分力（忽然 R=1.18 / 好像人类更高），
      「命中即报」会把 67% 的章节判成异常，噪声淹没信号。
    """
    hard_found = [(w, prose.count(w)) for w in AI_PATTERN_HARD_CLICHES]
    hard_found = [(w, c) for w, c in hard_found if c]

    weak_counts = {w: prose.count(w) for w in AI_PATTERN_WEAK_WORDS}
    weak_counts = {w: c for w, c in weak_counts.items() if c}
    weak_total = sum(weak_counts.values())
    piled = sorted(w for w, c in weak_counts.items() if c >= _WEAK_WORD_PER_WORD_LIMIT)
    weak_pile_up = bool(piled) or weak_total >= _WEAK_WORD_TOTAL_LIMIT

    words_found = [w for w, _ in hard_found]
    if weak_pile_up:
        words_found += piled or sorted(weak_counts)
    if not words_found:
        return []
    total_count = sum(c for _, c in hard_found) + (weak_total if weak_pile_up else 0)
    severity = _maybe_upgrade_severity("AI-FORBIDDEN-WORD", total_count, "warning")
    suffix = "（弱词同章堆积）" if weak_pile_up else ""
    return [
        {
            "rule_id": "AI-FORBIDDEN-WORD",
            "severity": severity,
            "message": f"AI 高频套话/禁用词命中{suffix}：{','.join(words_found)}",
            "count": total_count,
            "words": words_found,
            "weak_pile_up": weak_pile_up,
            "excerpt": prose[:120],
        }
    ]


def _scan_triplet_opening(prose: str) -> list[dict[str, Any]]:
    """连续 3 句以上以相同两字词开头。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        word = _leading_chinese_bigram(sents[i])
        if word is None:
            i += 1
            continue
        j = i + 1
        while j < len(sents):
            nxt = _leading_chinese_bigram(sents[j])
            if nxt == word:
                j += 1
            else:
                break
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-TRIPLET-OPENING", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-TRIPLET-OPENING",
                    "severity": severity,
                    "message": f"连续 {run_len} 句以相同两字词「{word}」开头，疑似句式套路",
                    "count": run_len,
                    "word": word,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


_PRONOUN_START_RE = re.compile(r"^[他她][\u4e00-\u9fff]")


def _scan_pronoun_pile(prose: str) -> list[dict[str, Any]]:
    """「他/她」开头句子排比堆砌。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        if not _PRONOUN_START_RE.match(sents[i]):
            i += 1
            continue
        j = i + 1
        while j < len(sents) and _PRONOUN_START_RE.match(sents[j]):
            j += 1
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-PRONOUN-PILE", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-PRONOUN-PILE",
                    "severity": severity,
                    "message": f"「他/她」主语排比堆砌 {run_len} 句",
                    "count": run_len,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


def _scan_ending_summary(prose: str) -> list[dict[str, Any]]:
    """章尾总结/升华体套话检测。"""
    last_para = _last_paragraph(prose)
    if not last_para:
        return []
    words_found: list[str] = []
    for phrase in _AI_ENDING_SUMMARY_PHRASES:
        if phrase in last_para and phrase not in words_found:
            words_found.append(phrase)
    if not words_found:
        return []
    return [
        {
            "rule_id": "AI-ENDING-SUMMARY",
            "severity": "warning",
            "message": f"章尾出现总结/升华体套话：{','.join(words_found)}",
            "count": len(words_found),
            "words": words_found,
            "excerpt": last_para[-160:],
        }
    ]


_DASH_OR_ELLIPSIS_RE = re.compile(r"——|…{2,}")


def _scan_punct_abuse(
    prose: str, *, threshold_per_1k: float = DEFAULT_DASH_THRESHOLD_PER_1K
) -> list[dict[str, Any]]:
    """破折号/省略号滥用：按 visible_chars 计算每千字出现次数。"""
    matches = list(_DASH_OR_ELLIPSIS_RE.finditer(prose))
    if not matches:
        return []
    wc = max(1, visible_chars(prose))
    rate = len(matches) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    base_severity = "error" if rate >= threshold_per_1k * 2 else "warning"
    return [
        {
            "rule_id": "AI-PUNCT-ABUSE",
            "severity": base_severity,
            "message": f"破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold_per_1k}）",
            "count": len(matches),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "excerpt": prose[:120],
        }
    ]


def _scan_explain_tone(prose: str) -> list[dict[str, Any]]:
    """解释腔连接词/句式高密度检测。"""
    labels: list[str] = []
    total = 0
    for pattern, label in _AI_EXPLAIN_PATTERNS:
        found = list(re.finditer(pattern, prose))
        if found:
            total += len(found)
            if label and label not in labels:
                labels.append(label)
    if total == 0:
        return []
    severity = _maybe_upgrade_severity("AI-EXPLAIN-TONE", total, "warning")
    label_text = ",".join(labels) if labels else "解释连接词"
    return [
        {
            "rule_id": "AI-EXPLAIN-TONE",
            "severity": severity,
            "message": f"解释腔高密度：{total} 处（{label_text}）",
            "count": total,
            "labels": labels,
            "excerpt": prose[:120],
        }
    ]


# ---------------------------------------------------------------------------
# 段切分口径（本模块**唯一**入口，三条段级算子共用）
# ---------------------------------------------------------------------------
# 按**任意换行序列**切段（``\n+``）。
#
# 为什么是 ``\n+`` 而不是 ``\n\n``（2026-09-27 复算实证）：文风锚点书等外部语料
# 是**一行一段**，榜一侯府弧 19 章的 ``\n\n`` 总数 = **0**。用 ``\n\n`` 切分会把
# 19 章读成 **19 个整章大段**（每段远超 12 字上限），于是段切分类算子在人类语料上
# **构造上不可能命中**——``AI-SHORT-PARA`` 此前登记的「人类基线 0.00/千字」不是测
# 出来的，是量具口径造成的假象。改用 ``\n+`` 后人类侧立刻出现 10 处命中。
# 本仓生成侧两口径只差 5 段（3479 → 3484，个别章存在单换行），所以口径统一对
# 生成侧几乎无影响、对人类侧是「从假象变回可测」。
#
# 短段 / 长段 / 相邻对白三条段级算子一律走本函数：口径分叉过一次就出过「人类基线是
# 量具假象」的事故，本批把段级三条收敛到同一入口。
#
# ⚠️ **不是全模块唯一入口**（首轮交付写成「不给第三次机会」是**假话**，2026-09-27
# 订正）：:func:`_last_paragraph`（``AI-ENDING-SUMMARY`` 专用）仍内联
# ``prose.split("\n\n")``，是第二份独立实现，**已登记为待修**。
# 触发条件 = 生产 draft 出现 CRLF 或单换行形态（那时它会把整章当末段扫）。
# 实测 dev 库 196 份 draft 中含 CR 的 **0 份** ⇒ 该形态当前不可达，故本轮不动它
# （改它要单独抽样论证「章尾升华体」在单换行语料上的分布，不在本批四项内）。
# 判别：tests/unit/quality/test_readability_rules.py（形状级断言：模块内
# ``prose.split(\"\\n\\n\")`` 只应出现在 _last_paragraph 一处）。
def _split_paragraphs(prose: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n+", prose) if p.strip()]


# --- 对举结构 ---------------------------------------------------------------

_CONTRAST_PAIR_RE = re.compile(r"不是[^，。！？；：\n]{1,20}[，,]\s*(?:而是|是)")


def count_contrast_pairs(prose: str) -> list[str]:
    """对举结构「不是 A 而是/是 B」的全部命中原文（供精确率抽样核查）。"""
    return [m.group(0) for m in _CONTRAST_PAIR_RE.finditer(prose)]


def _scan_contrast_pair(
    prose: str, *, threshold_per_1k: float = DEFAULT_CONTRAST_PAIR_RATE_PER_1K
) -> list[dict[str, Any]]:
    hits = count_contrast_pairs(prose)
    if not hits:
        return []
    wc = visible_chars(prose)
    if wc < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    rate = len(hits) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    return [
        {
            "rule_id": "AI-CONTRAST-PAIR",
            "severity": "warning",
            "message": (
                f"对举结构「不是……而是……」密度偏高：每千字 {rate:.1f} 处"
                f"（阈值 {threshold_per_1k}）"
            ),
            "count": len(hits),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名）---------------------------------------------------------

# 回指/指示成分：出现即视为「已标示评价对象」，不算零回指。
_ANAPHORA_RE = re.compile(r"[这那此该其]|上述|前者|后者|以上|方才|前面|下面")
# 对话/引语起首（排除对话，对话短句属正常写法）。
_DIALOGUE_START_RE = re.compile(r"^[「『\"'“]")
# **说话人前缀行**（2026-09-27 补）：`名字：` 形态。此前 ``_DIALOGUE_START_RE``
# 只认「以引号起首」，于是人类锚点书的说话人行（`陆明河：“……”`、`宋沛年：？`）
# 被当成非对白短段计入本规则——即人类语料的排版件替生成侧的 tic 交了税。
#
# **不能写成宽规则**（本项目的反复踩坑处）：`^[^：:]{1,6}[：:]` 会把正文里的普通
# 冒号一并吞掉——`源头：省府常平仓调拨`（字段行）、`现在他只记得：有人笑过`（正常
# 句子）、`跟着浮出第二行：十倍` 全部字面同构。两个分支的判据与**宽度都按实测校准**：
#
#   ① 冒号后紧跟引号，宽度 **{1,3}**——宽度证据只有 3 字：人类锚点 19 章 779 段里
#      走完前置过滤后带冒号的 ≤12 字段落**只有 2 条**，其中走①的仅 `陆明河：“……”`
#      （冒号前 3 字）。收窄到 {1,3} 的实测效果：人类侧**逐字不变**（8 / 0.212 /
#      6 章 / 1 章超阈），生成侧 258 → **260**。原因见下。
#   ② 冒号即段末，宽度 **{1,6}**——覆盖 `宋沛年：`（人类书）与 `价签：` 这类标签行。
#
# **为什么①必须是 {1,3}**（对抗审查打回项，2026-09-27）：宽度放到 {1,6} 时，生成侧
# 有 2 段 ≥4 字前缀的该形态被误豁免，而它们恰恰是本规则声称要抓的**叙述节拍行**——
# `林昭点点头：“知道了。”` / `霍铁没懂：“给谁？”`（「主语+动作+冒号+引语」）。收窄后
# 这 2 段回到命中，生成侧命中数 258 → 260（超阈章仍 34），人类侧不变。
#
# **已知能力边界**：② 分不清**人名行**与**常见名词标签行**——`宋沛年：` 与 `价签：`
# 字面同构，两侧语料实测生成侧 0 例。如实登记在 tests/unit/quality/
# test_readability_rules.py::test_short_para_speaker_line_detection_is_tight。
_SPEAKER_LINE_RE = re.compile(
    r"^(?:[^：:”“]{1,3}[：:]\s*[“「『\"]"  # 分支①：≤3 字 + 冒号 + 引语
    r"|[^：:”“]{1,6}[：:]\s*[。！？…”」]?\s*$)"  # 分支②：≤6 字 + 冒号即段末
)
# 人称代词起首的短句属叙述推进，另由 AI-PRONOUN-PILE 管，此处不重复计。
_PERSONAL_START_RE = re.compile(r"^[他她它]")
_PARA_OPENER_MAX_CHARS = 12
# 密度类规则的**最小判定长度**：几十字的字符串（单测样例、片段）算密度没有统计意义，
# 「1 处命中 / 17 字 = 58/千字」会稳定误报。低于此长度一律不判密度。
_MIN_PROSE_CHARS_FOR_DENSITY = 200


def count_short_paras(prose: str) -> list[str]:
    """短句独立成段的全部命中原文（供精确率抽样核查）。

    判定：段落首句（也是该段唯一可判内容）满足全部四条——
    ① 可见字 ≤ 12；② 非对白（不以引号起首、也不是 `名字：` 说话人行）；
    ③ 不含回指/指示成分；④ 非人称代词起首（那属叙述推进，另有规则管）。

    段切分走 :func:`_split_paragraphs`（``\\n+``），与同模块长段 / 相邻对白两条
    算子同口径——此前本函数独自用 ``\\n\\n``，使人类语料（一行一段）被读成整章
    单段、构造上不可能命中（见该函数注释）。

    抽样核实（2026-09-15，抽 12 条）：命中为「林策开口了」「万卷阁开了」这类
    **叙述短句独立成段**，非来源研究所述「零回指评价性评论」——故规则名按实际
    覆盖范围登记为 AI-SHORT-PARA，不冒用来源特征名。

    **本规则除「说话人行」外不做任何合法写法豁免**（2026-09-27 复核后的定论）：
    真信号（`曹淳。` 人名单独成段）与合法写法（`屯田册。` 文书抬头独立成段）在 ≤12 字
    窗口内**字面同构**，实测任何形态判据（无谓词 / 计量短语 / 字段行 / 排版件）的
    并集都要漏放约一半真信号，故那一族一律不加豁免。
    **唯一例外是 :data:`_SPEAKER_LINE_RE`（说话人行 = 排版件）**，它不适用上述否决
    理由，理由有三：① 它只命中**排版件**这一类，判据是「冒号位置」而非「段内有无
    谓语」——而「有无谓语」才是那个在 ≤12 字窗口里不可判的轴；② 两侧语料里它只对应
    **1 个人类校准实例**（`陆明河：“……”`）+ 1 处人类字段行 + 1 处生成侧标签行，
    漏放面被语料本身限死；③ 分支①的宽度按实测收窄到 {1,3} 后**不再触及生成侧的
    叙述节拍行**（`林昭点点头：“知道了。”` / `霍铁没懂：“给谁？”` 仍判命中），
    即收窄把「豁免真信号」的风险实测归零。
    """
    out: list[str] = []
    for para in _split_paragraphs(prose):
        # 整段就是那一句短句才算「独立成段」——只看段首句会把
        # 「灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。」误判成短段。
        if len(re.sub(r"\s", "", para)) > _PARA_OPENER_MAX_CHARS:
            continue
        first = re.split(r"[。！？；\n]", para, maxsplit=1)[0].strip()
        if not first:
            continue
        if _DIALOGUE_START_RE.match(first) or _PERSONAL_START_RE.match(first):
            continue
        if _SPEAKER_LINE_RE.match(first):
            continue
        if _ANAPHORA_RE.search(first):
            continue
        out.append(first)
    return out


def _scan_short_para(
    prose: str, *, threshold_per_1k: float = DEFAULT_SHORT_PARA_RATE_PER_1K
) -> list[dict[str, Any]]:
    hits = count_short_paras(prose)
    if not hits:
        return []
    wc = visible_chars(prose)
    if wc < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    rate = len(hits) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    return [
        {
            "rule_id": "AI-SHORT-PARA",
            "severity": "warning",
            "message": (
                f"短句独立成段密度偏高：每千字 {rate:.1f} 处（阈值 {threshold_per_1k}）"
            ),
            "count": len(hits),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 拟人化喻体 -------------------------------------------------------------

_ANTHRO_ROLE_WORDS = (
    "导师|老师|教师|医师|医生|专家|学者|智者|哲人|匠人|工匠|艺术家|指挥家|"
    "建筑师|设计师|工程师|裁判|考官|审查员|监察员|守卫|哨兵|管家|仆人|猎人|"
    "农夫|旅人|向导|领航员|旁观者|见证者|记录者|观察者|讲述者|会计|"
    "技师|维修工|侦探|法官|医生|教练|舵手|领队|总管"
)
_ANTHRO_VEHICLE_RE = re.compile(
    r"像(?:一位|一名|一个|位|名)[^，。！？；：\n]{0,10}?(?:" + _ANTHRO_ROLE_WORDS + r")"
)


def count_anthro_vehicles(prose: str) -> list[str]:
    """拟人化喻体（以职业/角色名词作喻体）的全部命中原文。"""
    return [m.group(0) for m in _ANTHRO_VEHICLE_RE.finditer(prose)]


def _scan_anthro_vehicle(
    prose: str, *, min_count: int = DEFAULT_ANTHRO_VEHICLE_MIN_COUNT
) -> list[dict[str, Any]]:
    hits = count_anthro_vehicles(prose)
    if len(hits) < min_count:
        return []
    return [
        {
            "rule_id": "AI-ANTHRO-VEHICLE",
            "severity": "warning",
            "message": f"拟人化喻体（职业/角色类）同章 {len(hits)} 处",
            "count": len(hits),
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 可读性：长段 -----------------------------------------------------------


def count_long_paragraphs(
    prose: str, *, warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS
) -> list[str]:
    """超过 ``warn_chars`` 可见字的段落原文（供精确率抽样核查）。"""
    return [p for p in _split_paragraphs(prose) if visible_chars(p) > warn_chars]


def _scan_long_para(
    prose: str,
    *,
    warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS,
    error_chars: int = DEFAULT_LONG_PARA_ERROR_CHARS,
    min_count: int = DEFAULT_LONG_PARA_MIN_COUNT,
) -> list[dict[str, Any]]:
    """长段堆积：单段可见字超限即计入，按段数/最大段长定档。

    - 段落判定与分母口径：``visible_chars``（去空白）——与全仓字数口径同一函数；
    - ``count`` = 单章长段（>warn_chars）数；``samples`` = 前 5 段开头 30 字，
      供人工抽样核对（算子只看字面长度，不判断内容是否真的「实心字」）；
    - 定档：任一段 > ``error_chars`` ⇒ error；否则长段数 ≥ ``min_count`` ⇒ warning。
    """
    if visible_chars(prose) < _MIN_PROSE_CHARS_FOR_READABILITY:
        return []
    long_paras = count_long_paragraphs(prose, warn_chars=warn_chars)
    over_error = [p for p in long_paras if visible_chars(p) > error_chars]
    if not over_error and len(long_paras) < min_count:
        return []
    lengths = [visible_chars(p) for p in long_paras]
    severity = "error" if over_error else "warning"
    return [
        {
            "rule_id": "AI-LONG-PARA",
            "severity": severity,
            "message": (
                f"长段堆积：单章 {len(long_paras)} 段超过 {warn_chars} 可见字"
                f"（最长 {max(lengths)} 字）"
            ),
            "count": len(long_paras),
            "error_count": len(over_error),
            "max_chars": max(lengths),
            "warn_chars": warn_chars,
            "error_chars": error_chars,
            "samples": [p[:30] for p in long_paras[:5]],
            "excerpt": long_paras[0][:120],
        }
    ]


# --- 可读性：对话占比 -------------------------------------------------------

# 成对弯双引号内的内容（U+201C/U+201D）。落库前 quality.normalize 已把
# 「」/英式直引号规整为“”，故此处不再兼容其它引号形态。
_DIALOGUE_PAIR_RE = re.compile(r"\u201c([^\u201d]*)\u201d")


def count_dialogue_visible_chars(prose: str) -> int:
    """成对 “…” 内的可见字数（去空白口径，与 :func:`visible_chars` 同式）。"""
    return sum(visible_chars(m.group(1)) for m in _DIALOGUE_PAIR_RE.finditer(prose))


def dialogue_ratio(prose: str) -> float:
    """对话占比 = 对话可见字 ÷ 全章可见字；空正文返回 0.0。"""
    total = visible_chars(prose)
    if total == 0:
        return 0.0
    return count_dialogue_visible_chars(prose) / total


def _scan_dialogue_low(
    prose: str,
    *,
    warn_ratio: float = DEFAULT_DIALOGUE_LOW_WARN_RATIO,
) -> list[dict[str, Any]]:
    """对话占比过低：<warn_ratio ⇒ **warning（唯一档位）**。

    error 档（<8%）已于 2026-09-17 撤回——理由与实证见模块内常量注释（逼模型凑
    对话指标会产出灌水对白，且人类锚点书写得出整章无对话）。本函数**不含任何
    返回 error 的路径**，severity 恒为 ``"warning"``。
    """
    total = visible_chars(prose)
    if total < _MIN_PROSE_CHARS_FOR_READABILITY:
        return []
    dialogue_chars = count_dialogue_visible_chars(prose)
    ratio = dialogue_chars / total
    if ratio >= warn_ratio:
        return []
    summary = (
        f"对话占比 {ratio:.1%}"
        f"（{dialogue_chars}/{total} 可见字，阈值 {warn_ratio:.0%}）"
    )
    return [
        {
            "rule_id": "AI-DIALOGUE-LOW",
            "severity": "warning",
            "message": f"对话占比过低：{summary}",
            "count": 1,
            "dialogue_ratio": round(ratio, 4),
            "dialogue_chars": dialogue_chars,
            "total_visible_chars": total,
            "warn_ratio": warn_ratio,
            # 2026-09-21 检修 m3：本规则的「证据」就是占比数字本身。此前没有任何
            # samples / words 键，``_ai_pattern_evidence`` 只能退回 ``excerpt``
            # （正文前 120 字）——读者看到一段与占比无关的原文，看不出在接受什么。
            "samples": [summary],
            "excerpt": prose[:120],
        }
    ]


# --- 可读性：相邻对白接词回声 -------------------------------------------------

# 实义字：汉字与数字（数字是接词回声的关键信号——「七天」「三百」「十四五两」这类
# 数字被后句复述，是本规则最能分辨「复述」与「真回答」的特征；标点/空白/字母不计）。
_SUBSTANTIVE_CHAR_RE = re.compile(r"[\u4e00-\u9fff0-9]")
# 抽样展示时的单侧截断长度（samples 只供人工过目，不参与判定）。
_ECHO_SAMPLE_MAX_CHARS = 40


@dataclass(frozen=True)
class DialogueEchoPair:
    """一对相邻对白及其回声指标（供抽样核对：算子只看字面，判定权在人和 LLM）。"""

    front: str  # 前句对白原文（引号内，已截断）
    back: str  # 后句对白原文（引号内，已截断）
    front_chars: str  # 前句实义字
    back_chars: str  # 后句实义字
    recall_ratio: float  # 复述型指标：后句实义字中出现在前句的比例
    overlap_ratio: float  # 接词型指标：共同实义字 ÷ 较短一侧实义字（**须配合 overlap_comparable 读**）
    opening_repeat: int  # 后句开口起、连续出现在前句中的实义字数

    @property
    def overlap_comparable(self) -> bool:
        """``overlap_ratio`` 这个读数在本对上是否**可解释**（2026-09-27）。

        ``overlap`` 的分母取 ``min(前,后)``，对称地问「两句用词是不是同一批」。
        分母落在薄的一侧时该问法失效——读数被机械地拉到 1.00，量的其实是
        「长句含短句的字」。两种失效形态：

        1. 任一侧实义字 < ``_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS``（3）——``min()``
           等于那一侧的 2 字，只要对方包含它就得 1.00；
        2. 后句 ≥ ``_DIALOGUE_ECHO_ASYMMETRY_LIMIT``（2.0）× 前句——后句是超集，
           分母恒等于前句；而此时 recall 必然走低（后句大半是新内容），两个指标
           互相证伪。

        只有 recall 是不对称的（分母恒为后句，问的是「新句里有多少是复述」），
        「后句更短」那一侧由它正确兜住，故本守卫**只挡超集方向**。

        不可比的对不因本属性而**不报**——复述型仍照常判定（2 字齐诵 `不退。`→
        `不退！` 就是靠 recall 命中的真信号）。本属性只决定 ``overlap_ratio``
        能不能用来触发接词型。
        """
        front_len, back_len = len(self.front_chars), len(self.back_chars)
        if min(front_len, back_len) < _MIN_DIALOGUE_ECHO_COMPARABLE_CHARS:
            return False
        return back_len < _DIALOGUE_ECHO_ASYMMETRY_LIMIT * front_len

    @property
    def sample(self) -> str:
        """``前句→后句`` 形式（命中样例的展示形态）。"""
        return f"{self.front}→{self.back}"


def _substantive_chars(text: str) -> str:
    """实义字序列：去标点/空白，保留汉字与数字。"""
    return "".join(_SUBSTANTIVE_CHAR_RE.findall(text))


def _dialogue_lines(prose: str) -> list[str]:
    """全章的**对白句**序列：以 “ 起首的段，取其引号内的对白文本。

    - 段切分与短段 / 长段两条规则同口径（:func:`_split_paragraphs`，``\\n+``）——
      外部锚点书一行一段、生产 draft 空行分段；
    - 段内夹带的叙述（``“三百。”曹淳说`` 的 ``曹淳说``）不计入比较：本规则量的是
      **对白之间**的接词，叙述是独立信息；
    - 只认“”（U+201C/U+201D）：落库前经 ``quality.normalize`` 统一引号形态。
    """
    lines: list[str] = []
    for para in _split_paragraphs(prose):
        if not para.startswith("\u201c"):
            continue
        quoted = "".join(m.group(1) for m in _DIALOGUE_PAIR_RE.finditer(para))
        if quoted:
            lines.append(quoted)
    return lines


def _opening_repeat_len(back: str, front: str) -> int:
    """后句开口起、在前句中**连续出现**的最长实义字序列长度。

    前缀是嵌套的（后句前 i 字出现 ⇒ 前 i-1 字也出现），故一旦失配即可停。
    「开口接词」是本规则的第二形态：例「够七天」→「**七天**之后呢」（2）。
    """
    length = 0
    while length < len(back) and back[: length + 1] in front:
        length += 1
    return length


def _echo_ratios(front: str, back: str) -> tuple[float, float]:
    """返回 ``(复述型比例, 重叠系数)``（均按实义字**多重集**取小计共同字）。

    - 复述型 = 共同字 ÷ **后句**字数——问「新句里有多少比例是复述对方的」，
      **非对称**，分母恒为后句，因此在两侧长度悬殊时仍然可解释（短后句被完整
      包含 ⇒ 1.00，那是「直接应答」的定义，如 `见周账房了？`→`见了。`）。
    - 重叠系数 = 共同字 ÷ **较短一侧**字数——问「两句的用词是不是同一批」，
      **对称**。对称问法要求两侧**质量可比**：分母落在薄的一侧时该读数不携带
      信息（被机械拉到 1.00）。故**本函数不声明它是否可解释**，可解释性由
      :attr:`DialogueEchoPair.overlap_comparable` 单独判定，调用方必须先看它。

    两者不可互相替代：短称呼 + 长展开时 recall 低（后句大半是新内容）而 overlap
    恒 1.00，两者互相证伪——此时以 recall 为准（实测 ch17 `林昭！`→`林昭，你跟我
    走，茶馆比县仓安全。`：recall 0.15 / overlap 1.00）。
    """
    front_count, back_count = Counter(front), Counter(back)
    front_total, back_total = sum(front_count.values()), sum(back_count.values())
    shared = sum(min(count, front_count[ch]) for ch, count in back_count.items())
    recall = shared / back_total if back_total else 0.0
    overlap = shared / min(front_total, back_total) if front_total and back_total else 0.0
    return recall, overlap


def dialogue_echo_pairs(prose: str) -> list[DialogueEchoPair]:
    """全部**相邻对白对**及其指标（未过滤；供抽样与阈值校准）。

    相邻 = 对白句序列里前后两项（中间夹叙述段不影响相邻性——夹在两句对白之间的一段
    动作描写不改变「后句接了前句的词」这一事实）。单侧实义字 <
    ``_MIN_DIALOGUE_ECHO_CHARS``（单字应答）不参与成对比较。
    """
    lines = _dialogue_lines(prose)
    pairs: list[DialogueEchoPair] = []
    for front_raw, back_raw in zip(lines, lines[1:]):
        front, back = _substantive_chars(front_raw), _substantive_chars(back_raw)
        if len(front) < _MIN_DIALOGUE_ECHO_CHARS or len(back) < _MIN_DIALOGUE_ECHO_CHARS:
            continue
        recall, overlap = _echo_ratios(front, back)
        pairs.append(
            DialogueEchoPair(
                front=front_raw[:_ECHO_SAMPLE_MAX_CHARS],
                back=back_raw[:_ECHO_SAMPLE_MAX_CHARS],
                front_chars=front,
                back_chars=back,
                recall_ratio=recall,
                overlap_ratio=overlap,
                opening_repeat=_opening_repeat_len(back, front),
            )
        )
    return pairs


def count_dialogue_echoes(
    prose: str,
    *,
    min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
) -> list[DialogueEchoPair]:
    """命中的接词回声对（同一对相邻对白最多计一次，两种形态是「或」关系）。

    - **复述型**：``recall_ratio >= min_ratio``——后句基本是前句的复述；
    - **接词型**：``overlap_comparable``（两侧实义字可比，见该属性）且
      ``overlap_ratio >= min_overlap`` 且 ``opening_repeat >= min_opening``
      ——后句开口就接对方的词。

    两者都命中时仍只记一对。``overlap_comparable`` 是**度量可解释性**的守卫，
    不是对本对白的豁免：不可比的对仍可凭 recall 命中（真信号形态即如此）。
    **不判对白是否有后果**（那既无法正则化，也不该由字面算子决定）——本规则是
    提示，不是闸门。
    """
    hits: list[DialogueEchoPair] = []
    for pair in dialogue_echo_pairs(prose):
        repeated = pair.recall_ratio >= min_ratio
        picking_up = (
            pair.overlap_comparable
            and pair.overlap_ratio >= min_overlap
            and pair.opening_repeat >= min_opening
        )
        if repeated or picking_up:
            hits.append(pair)
    return hits


def _scan_dialogue_echo(
    prose: str,
    *,
    min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
) -> list[dict[str, Any]]:
    """接词回声章级命中：``count`` = 对数、``samples`` = 前 5 对（``前句→后句``）。

    - 最小判定长度照 ``_MIN_PROSE_CHARS_FOR_DENSITY`` 先例：短片段（单测样例、引文）
      的配比没有统计意义，不判；
    - severity 恒 ``warning``（提示非闸门）——误报的判别权在人工/LLM 评审。
    """
    if visible_chars(prose) < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    hits = count_dialogue_echoes(
        prose, min_ratio=min_ratio, min_overlap=min_overlap, min_opening=min_opening
    )
    if not hits:
        return []
    samples = [p.sample for p in hits[:5]]
    return [
        {
            "rule_id": "AI-DIALOGUE-ECHO",
            "severity": "warning",
            "message": f"相邻对白接词回声 {len(hits)} 对（例：{samples[0]}）",
            "count": len(hits),
            "samples": samples,
            "excerpt": samples[0][:120],
        }
    ]


def _scan_beat_repeat(
    prose: str,
    *,
    min_count: int = DEFAULT_BEAT_MIN_COUNT,
    rate_per_1k: float = DEFAULT_BEAT_RATE_PER_1K,
) -> list[dict[str, Any]]:
    """同章远距小句字面复现（``AI-BEAT-REPEAT``）。

    机制、校准证据、**不覆盖范围**全部在 :mod:`packages.core.quality.beat_repeat`
    模块 docstring 里（本函数只是把该模块的章级判定接进统一扫描入口）。
    severity 恒 warning——它是提示不是闸门，不含 error 路径。
    """
    return scan_beat_repeats(prose, min_count=min_count, rate_per_1k=rate_per_1k)


def _scan_time_conflicts(
    prose: str,
    *,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[dict[str, Any]]:
    """章内时点矛盾（``CONT-CLOCK-DAYBREAK`` / ``CONT-TIME-BACKSTEP``）。

    机制、校准证据、**不覆盖范围**全部在
    :mod:`packages.core.quality.continuity_time` 模块 docstring 里（本函数只是把该
    模块的章级判定接进统一扫描入口——**不是 AI 味规则**，rule_id 用 ``CONT-`` 前缀
    与 AI 味算子区分）。severity 恒 warning，不含 error 路径。
    """
    return scan_time_conflicts(
        prose, max_gap_chars=max_gap_chars, min_backstep_hours=min_backstep_hours
    )


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def scan_ai_patterns(
    prose: str,
    *,
    dash_threshold_per_1k: float = DEFAULT_DASH_THRESHOLD_PER_1K,
    contrast_pair_threshold_per_1k: float = DEFAULT_CONTRAST_PAIR_RATE_PER_1K,
    short_para_threshold_per_1k: float = DEFAULT_SHORT_PARA_RATE_PER_1K,
    anthro_vehicle_min_count: int = DEFAULT_ANTHRO_VEHICLE_MIN_COUNT,
    long_para_warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS,
    long_para_error_chars: int = DEFAULT_LONG_PARA_ERROR_CHARS,
    long_para_min_count: int = DEFAULT_LONG_PARA_MIN_COUNT,
    dialogue_low_warn_ratio: float = DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    dialogue_echo_min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    dialogue_echo_min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    dialogue_echo_min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
    beat_min_count: int = DEFAULT_BEAT_MIN_COUNT,
    beat_rate_per_1k: float = DEFAULT_BEAT_RATE_PER_1K,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[dict[str, Any]]:
    """扫描正文中的 AI 味/AI 腔模式。

    返回命中列表，每条包含：
      - rule_id: 规则标识
      - severity: 'warning' | 'error'
      - message: 人类可读摘要
      - count?: 命中次数
      - excerpt?: 正文片段（用于定位）
      - 以及规则相关额外字段（words / word / rate / labels / samples 等）

    全部为确定性正则/统计规则，不调用 LLM。

    阈值均为关键词参数，便于按语料/体裁重新校准（本地校准入口：
    ``scripts/ai_tone_calibrate.py``）；不传即用模块默认值，行为稳定。
    """
    if not prose:
        return []
    hits: list[dict[str, Any]] = []
    hits.extend(_scan_forbidden_words(prose))
    hits.extend(_scan_triplet_opening(prose))
    hits.extend(_scan_pronoun_pile(prose))
    hits.extend(_scan_ending_summary(prose))
    hits.extend(_scan_punct_abuse(prose, threshold_per_1k=dash_threshold_per_1k))
    hits.extend(_scan_explain_tone(prose))
    hits.extend(_scan_contrast_pair(prose, threshold_per_1k=contrast_pair_threshold_per_1k))
    hits.extend(_scan_short_para(prose, threshold_per_1k=short_para_threshold_per_1k))
    hits.extend(_scan_anthro_vehicle(prose, min_count=anthro_vehicle_min_count))
    hits.extend(
        _scan_long_para(
            prose,
            warn_chars=long_para_warn_chars,
            error_chars=long_para_error_chars,
            min_count=long_para_min_count,
        )
    )
    hits.extend(
        _scan_dialogue_low(prose, warn_ratio=dialogue_low_warn_ratio)
    )
    hits.extend(
        _scan_dialogue_echo(
            prose,
            min_ratio=dialogue_echo_min_ratio,
            min_overlap=dialogue_echo_min_overlap,
            min_opening=dialogue_echo_min_opening,
        )
    )
    hits.extend(_scan_beat_repeat(prose, min_count=beat_min_count, rate_per_1k=beat_rate_per_1k))
    hits.extend(
        _scan_time_conflicts(
            prose, max_gap_chars=max_gap_chars, min_backstep_hours=min_backstep_hours
        )
    )
    return hits


__all__ = [
    "AI_PATTERN_FORBIDDEN_WORDS",
    "AI_PATTERN_HARD_CLICHES",
    "AI_PATTERN_RULES",
    "AI_PATTERN_WEAK_WORDS",
    "DEFAULT_ANTHRO_VEHICLE_MIN_COUNT",
    "DEFAULT_CONTRAST_PAIR_RATE_PER_1K",
    "DEFAULT_DASH_THRESHOLD_PER_1K",
    "DEFAULT_DIALOGUE_ECHO_MIN_OPENING",
    "DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP",
    "DEFAULT_DIALOGUE_ECHO_MIN_RATIO",
    "DEFAULT_DIALOGUE_LOW_WARN_RATIO",
    "DEFAULT_LONG_PARA_ERROR_CHARS",
    "DEFAULT_LONG_PARA_MIN_COUNT",
    "DEFAULT_LONG_PARA_WARN_CHARS",
    "DEFAULT_MAX_GAP_CHARS",
    "DEFAULT_MIN_BACKSTEP_HOURS",
    "DEFAULT_SHORT_PARA_RATE_PER_1K",
    "DialogueEchoPair",
    "count_anthro_vehicles",
    "count_contrast_pairs",
    "count_dialogue_echoes",
    "count_dialogue_visible_chars",
    "count_long_paragraphs",
    "count_short_paras",
    "dialogue_echo_pairs",
    "dialogue_ratio",
    "scan_ai_patterns",
]
