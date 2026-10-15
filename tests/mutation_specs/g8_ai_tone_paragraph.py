"""G8 ``_last_paragraph`` 段切分口径收敛的突变规格（2026-10-15 批次六）。

被测机制：``AI-ENDING-SUMMARY`` 的「末段」取法——从「内联按空行切段」收敛到模块内
唯一切段入口 ``_split_paragraphs``（``\\n+``）。三条突变分别打**三个不同的失效方向**，
不是同一条改三遍：

* **M1 口径回退**（题目要求的第 ① 条）：把唯一切段入口换回内联空行切段。
  预期红：口径收敛用例 + 「量具动得了」用例 + 源码扫描型用例（**三条独立判别点**）。
* **M2 取末端**：段切分入口保持不变，只把 ``[-1]`` 改成 ``[0]``。
  这一条专防「用例只验了『用了某个函数』」——M2 下 M1 那条判别点大多仍绿
  （口径确实收敛了，只是取错了端），必须由取末段用例独立抓住。
* **M3 规则扫全章**：``_last_paragraph`` 完好，把规则的输入改成整章。
  这一条专防「判别点只钉在 helper 上、没钉在**接线**上」——M2 之外的第三种失效，
  与 M1/M2 都不同：helper 全对，但规则拿错了东西。

⚠️ **为什么没有「阈值判据恒假/恒真 ⇒ 阈值不变式用例必红」那条**
------------------------------------------------------------
题目要求第 ② 条针对**阈值**。本规则**没有阈值**，且这不是遗漏而是实测结论：
人类侧 19 章与生成侧 97 章命中**均为 0** ⇒ 分位数为 0、人类侧上沿也是 0 ⇒
「人类侧上沿 < warn 阈值 < 生成侧上沿」在 ``0.0 < x < 0.0`` 上**无解**。按坑区
「阈值只取本仓实测分布」「阈值落在实测分布之外 = 死规则」，此处造一个非零阈值就是
凭空造数，再对着它写一条恒真不变式——那是量具在表演，不是在看守。
故此处以 M2/M3 补足第二条，方向均为**真实机制**。详见
``packages/core/quality/ai_patterns.py::_last_paragraph`` docstring。

每条锚点在目标文件里**恰好命中 1 次**（2026-10-15 用 Python 直接读文件核过，
不靠肉眼）：M1/M2 在 ``_last_paragraph`` 内，M3 在 ``_scan_ending_summary`` 首行。
"""

MUTATIONS = [
    # ① 口径回退：内联按空行切段（M1）
    (
        "G8-M1-ending-summary-split-reverts-to-blank-line",
        "packages/core/quality/ai_patterns.py",
        "    paragraphs = _split_paragraphs(prose)",
        '    paragraphs = [p.strip() for p in prose.split("\\n\\n") if p.strip()]',
        "tests/unit/quality/test_ai_ending_summary_paragraph.py",
    ),
    # ② 取末端：切分入口不变，只把末段取成首段（M2）
    (
        "G8-M2-ending-summary-takes-first-segment",
        "packages/core/quality/ai_patterns.py",
        "    return paragraphs[-1] if paragraphs else prose",
        "    return paragraphs[0] if paragraphs else prose",
        "tests/unit/quality/test_ai_ending_summary_paragraph.py",
    ),
    # ③ 接线：规则本身扫全章而非末段（M3）
    (
        "G8-M3-ending-summary-rule-scans-whole-chapter",
        "packages/core/quality/ai_patterns.py",
        "    last_para = _last_paragraph(prose)",
        "    last_para = prose",
        "tests/unit/quality/test_ai_ending_summary_paragraph.py",
    ),
]
