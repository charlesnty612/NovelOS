"""质量域「结构钉」——模块清单 / 可直接导入 / 门面一致性 / 五族职责边界。

2026-10-22 批次二十 · novelos-dev-a。

名实要准（先说清楚这份文件**不是**什么）
----------------------------------------
它**不是**「这些模块没测试」。上轮实测（HEAD=d06a79a）表明：下列 6 个模块在
``tests/unit/quality/`` 内**零直接 import**——

    ``__init__`` / ``ai_patterns_dialogue`` / ``ai_patterns_paragraph`` /
    ``ai_patterns_sentence`` / ``ai_patterns_text`` / ``style_trend``

——但它们**大量被间接覆盖**（经 ``ai_patterns.py`` 门面与 ``quality`` 包门面触达），
行为层面有真实测试。缺的是**结构看守**：

    改名 / 搬家 / 被合并 / 公开符号被悄悄挪走 ⇒ **不会有任何域内测试变红**。

⇒ 本文件补的正是这一层：把「域的结构」本身变成**可断言的对象**。

四层断言（逐层都有可复算的断言，不是恒真断言）
--------------------------------------------
1. **模块清单**：``packages/core/quality/`` 下的 ``.py`` 文件名集合 **==** 钉住的
   字面集合 ⇒ **新增 / 删除 / 改名 / 合并必红**（集合相等，不是子集）。
2. **逐模块可直接导入**：不经过门面，逐个 ``importlib.import_module`` 成功；
   并为**每个**模块钉 1–3 个它自己的公开符号（含机制单点
   ``ai_patterns_text._split_paragraphs``、
   ``continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS``、
   ``issues.issue_gate`` / ``issues.BLOCKING_RULES``、
   ``engine.ai_pattern_issues``、``scoring`` 的 trigram 两档常量）。
3. **门面一致性**：``quality/__init__.py`` 的 ``__all__``——
   ① 每个名字都能在包上解析到（无悬空）；② **不含自指元素**（AGENTS 硬规则 1
   明写「门面 ``__all__`` 不得含自指元素」）；③ 无重复；④ **关键公开名必须仍在**
   （⇒ 门面公开面收缩必红）。
4. **五族职责边界**：``ai_patterns``（门面/注册表）/ ``_sentence`` / ``_paragraph``
   / ``_dialogue`` / ``_text`` 各自**拥有**哪些规则符号——
   ① 各族必须**定义**自己那批；② **任一族都不得定义别族**的钉住符号（跨族串味抓）；
   ③ 门面必须**再导出**四族全部钉住符号（公开面不得悄悄缩水）。

「拥有」怎么判定（关键口径）
--------------------------
**不是** ``hasattr``：一个族**import** 别的族的单点来用（``ai_patterns_paragraph``
就 import 了 ``ai_patterns_text._split_paragraphs``）是**合法消费**，不是「串味」。
⇒ 本文件用 **AST**：一个符号算某模块「拥有」，当且仅当它在**该模块的源码里被定义**
（``def`` / ``class`` / 模块级赋值）。⇒ 「import 进来用」与「定义在这里」被分清。

语料无关（无 ``require_reference_corpus``）⇒ CI 上真红、不会 skip。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from packages.core import quality as _pkg
from packages.core.quality import __all__ as _FACADE_ALL

# ============================================================================
# 第 1 层：模块清单（字面集合，集合相等）
# ============================================================================

#: ``packages/core/quality/`` 下的 ``.py`` 文件名全集（**现场 glob 数出来的 23 个**，
#: 2026-10-22 于 HEAD=43258dd 复核）。集合相等 ⇒ 增 / 删 / 改名 / 合并必红。
_EXPECTED_MODULE_FILES = frozenset(
    {
        "__init__.py",
        "aggregate.py",
        "ai_flavor.py",
        "ai_patterns.py",
        "ai_patterns_dialogue.py",
        "ai_patterns_paragraph.py",
        "ai_patterns_sentence.py",
        "ai_patterns_text.py",
        "ai_trace.py",
        "beat_repeat.py",
        "continuity_taxonomy.py",
        "continuity_time.py",
        "engine.py",
        "guardrails.py",
        "issues.py",
        "models.py",
        "normalize.py",
        "outline_check.py",
        "payoff.py",
        "scoring.py",
        "service.py",
        "style_trend.py",
        "wordcount.py",
    }
)


def _module_name(file_name: str) -> str:
    """``.py`` 文件名 → 可导入的模块名（``__init__.py`` 即包本身）。"""
    stem = file_name[:-3]
    return "packages.core.quality" if stem == "__init__" else f"packages.core.quality.{stem}"


def _defined_names(mod) -> frozenset[str]:
    """该模块**源码里定义**（而非 import 进来）的模块级名字。

    ``def`` / ``class`` / 模块级赋值（含带注解的 ``AnnAssign``）都算「定义」。
    ⇒ 「import 进来用」与「定义在这里」被分清。
    """
    source = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def test_quality_module_inventory_is_exact():
    """第 1 层：模块清单集合**相等**（不是子集）⇒ 增 / 删 / 改名 / 合并必红。

    口径：``glob("*.py")`` **现场数**（本文件不写死数量，只写死集合）⇒
    数量由集合相等隐含保证（多一个或少一个都会让集合不相等）。
    """
    pkg_dir = Path(_pkg.__file__).parent
    actual = frozenset(p.name for p in pkg_dir.glob("*.py"))
    assert actual == _EXPECTED_MODULE_FILES, (
        "质量域模块清单变了："
        f"多出 {sorted(actual - _EXPECTED_MODULE_FILES)} / "
        f"少了 {sorted(_EXPECTED_MODULE_FILES - actual)}"
    )


# ============================================================================
# 第 2 层：逐模块可直接导入 + 关键符号
# ============================================================================

#: 每个模块**自己必须定义**的符号（1–3 个）。取的是各模块的「机制单点」，
#: 不是随便挑的名字：搬走 / 改名 / 合并 ⇒ 对应条目必红。
_EXPECTED_PUBLIC_SYMBOLS: dict[str, tuple[str, ...]] = {
    "aggregate": ("compute_overall", "compute_overall_with_gates", "formula_hash"),
    "ai_flavor": ("marker_hits_per_kchars", "AI_CLICHES"),
    "ai_patterns": ("scan_ai_patterns", "AI_PATTERN_RULES", "AiPatternRule"),
    "ai_patterns_dialogue": (
        "dialogue_ratio",
        "count_dialogue_echoes",
        "count_dialogue_visible_chars",
    ),
    "ai_patterns_paragraph": (
        "count_short_paras",
        "count_long_paragraphs",
        "count_anthro_vehicles",
    ),
    "ai_patterns_sentence": ("count_contrast_pairs", "DEFAULT_DASH_THRESHOLD_PER_1K"),
    # 段切分的**唯一单点**（AGENTS 坑区「同一模块内切段口径必须唯一」）
    "ai_patterns_text": ("_split_paragraphs", "_MIN_PROSE_CHARS_FOR_READABILITY"),
    "ai_trace": ("compute_ai_trace", "intra_chapter_repetition", "cross_chapter_repetition"),
    "beat_repeat": ("scan_beat_repeats", "count_beat_repeats", "BeatRepeat"),
    "continuity_taxonomy": (
        "DETERMINISTIC_CONTINUITY_RULE_IDS",
        "is_continuity_rule_id",
        "taxonomy_category",
    ),
    "continuity_time": ("scan_time_conflicts", "time_conflicts", "DEFAULT_MIN_BACKSTEP_HOURS"),
    "engine": ("ai_pattern_issues", "QualityEngine"),
    "guardrails": ("schema_validity", "req_q6", "req_q8"),
    "issues": ("issue_gate", "BLOCKING_RULES", "CONFIRM_RULES"),
    "models": ("QualityContext", "QualityReport", "Issue"),
    "normalize": ("normalize_prose", "find_internal_identifiers"),
    "outline_check": ("check_outline", "MIN_BEATS_PER_CHAPTER", "HOOK_RATE_FLOOR"),
    "payoff": ("evaluate", "PayoffContext"),
    "scoring": (
        "score_style",
        "STYLE_TRIGRAM_WARN_THRESHOLD",
        "STYLE_TRIGRAM_CONFIRM_THRESHOLD",
    ),
    "service": ("QualityService", "build_quality_context", "compute_char_stats"),
    "style_trend": ("check_style_trend", "WINDOW", "DECAY_THRESHOLD"),
    "wordcount": ("visible_chars", "word_band", "resolve_band_config"),
}


def test_every_quality_module_is_directly_importable():
    """第 2 层（a）：每个模块**不经过门面**都能直接 import 成功。

    门面的存在正是「这 6 个模块零直接 import」的成因 ⇒ 若某模块只能经门面
    触达（被合并进别的模块 / 被删），本条必红。
    """
    for file_name in sorted(_EXPECTED_MODULE_FILES):
        mod = importlib.import_module(_module_name(file_name))
        assert mod is not None, file_name


def test_every_quality_module_has_a_direct_static_import_in_this_file():
    """第 2 层（a′）：每个模块在本文件里都有**直接静态 import**（**函数级**）。

    为什么是**函数级**而不是文件顶部的静态 import：模块被改名时，顶部 import 会让
    **整个文件收集失败**（红的原因是 ImportError，不是结构断言 ⇒ 假捕获）；
    函数级 import 则只让**本条用例**红，且报错指名道姓。

    这条存在的意义：把「域内零直接 import」这个**症状**也真正归零
    （AST 静态口径可复算），而不只是把**风险**关掉。

    ⚠️ **残留 1 个且是结构性的**：``__init__``（包自身）**无法**用有意义的静态
    import 表达——实测 ``from packages.core.quality import __init__`` **不报错**，
    但绑到的是**模块对象的 ``__init__`` 方法**（method-wrapper），**不是**那个
    子模块 ⇒ 典型的「收下即忘」静默错对象陷阱。
    ⇒ 故包自身走 ``from packages.core import quality``（等价且正确），
    它的公开面由**第 3 层**（``__all__`` 四条断言）看守。
    ⇒ 「域内零直接 import 的模块」实测：6 → **1**（只剩结构上不可静态 import 的
    ``__init__``）。
    """
    from packages.core import quality as _pkg_q
    from packages.core.quality import (
        aggregate,
        ai_flavor,
        ai_patterns,
        ai_patterns_dialogue,
        ai_patterns_paragraph,
        ai_patterns_sentence,
        ai_patterns_text,
        ai_trace,
        beat_repeat,
        continuity_taxonomy,
        continuity_time,
        engine,
        guardrails,
        issues,
        models,
        normalize,
        outline_check,
        payoff,
        scoring,
        service,
        style_trend,
        wordcount,
    )

    static = {
        "__init__": _pkg_q,
        "aggregate": aggregate,
        "ai_flavor": ai_flavor,
        "ai_patterns": ai_patterns,
        "ai_patterns_dialogue": ai_patterns_dialogue,
        "ai_patterns_paragraph": ai_patterns_paragraph,
        "ai_patterns_sentence": ai_patterns_sentence,
        "ai_patterns_text": ai_patterns_text,
        "ai_trace": ai_trace,
        "beat_repeat": beat_repeat,
        "continuity_taxonomy": continuity_taxonomy,
        "continuity_time": continuity_time,
        "engine": engine,
        "guardrails": guardrails,
        "issues": issues,
        "models": models,
        "normalize": normalize,
        "outline_check": outline_check,
        "payoff": payoff,
        "scoring": scoring,
        "service": service,
        "style_trend": style_trend,
        "wordcount": wordcount,
    }
    # 静态与动态两条路径必须解析到**同一个**模块对象（否则静态 import 只是摆设）
    for stem, mod in static.items():
        assert mod is not None, stem
        assert importlib.import_module(_module_name(stem + ".py")) is mod, stem


def test_key_public_symbols_are_defined_on_their_own_module():
    """第 2 层（b）：每个模块**自己定义**它那批钉住的符号（含机制单点）。

    ⇒ 公开符号被「悄悄挪到别的模块」时，**原模块**这条必红。
    用「源码里定义」判定 ⇒ 合法地 import 别人的单点来用**不算**违规。
    """
    for file_name in sorted(_EXPECTED_MODULE_FILES):
        stem = file_name[:-3]
        expected = _EXPECTED_PUBLIC_SYMBOLS.get(stem)
        if expected is None:
            continue
        mod = importlib.import_module(_module_name(file_name))
        defined = _defined_names(mod)
        for symbol in expected:
            assert symbol in defined, f"{file_name} 上没有**定义** {symbol}"


# ============================================================================
# 第 3 层：门面一致性（quality/__init__.py）
# ============================================================================

#: 门面必须继续再导出的**关键公开名**（子集断言 ⇒ 新增导出不受影响，
#: 但**删掉**其中任何一个 ⇒ 必红）。
_FACADE_KEY_PUBLIC_NAMES = frozenset(
    {
        "QualityEngine",
        "QualityContext",
        "QualityReport",
        "Issue",
        "make_issue",
        "issue_gate",
        "is_blocking_issue",
        "severity_config_fingerprint",
        "compute_overall",
        "compute_overall_with_gates",
        "summarize_gates",
        "formula_hash",
        "check_style_trend",
        "WINDOW",
        "DECAY_THRESHOLD",
        "payoff_evaluate",
        "ai_flavor",
        "guardrails",
        "scoring",
        "MVP_SEVERITY_MATRIX",
        "MVP_RULE_OVERRIDES",
        "BLOCKING_RULES",
        "CONFIRM_RULES",
        "visible_chars",
        "word_band",
        "classify_prose_length",
    }
)


def test_facade_all_names_resolve_to_real_attributes():
    """第 3 层（a）：``__all__`` 里每个名字都能在包上解析到（无悬空导出）。"""
    for name in _FACADE_ALL:
        assert hasattr(_pkg, name), f"__all__ 里的 {name} 在包上解析不到（悬空导出）"


def test_facade_all_has_no_self_referential_elements():
    """第 3 层（b）：``__all__`` **不得含自指元素**（AGENTS 硬规则 1）。

    自指 = 把包自己（或它的任一层限定名）当成对外导出的名字，例如
    ``"quality"`` / ``"__init__"`` / ``"packages"`` / ``"packages.core.quality"``。
    """
    forbidden = {"quality", "__init__", "packages", "core", "packages.core.quality"}
    for name in _FACADE_ALL:
        assert name not in forbidden, f"__all__ 含自指元素 {name}"
        assert not name.startswith("packages."), f"__all__ 含自限定的自指元素 {name}"


def test_facade_all_has_no_duplicates():
    """第 3 层（c）：``__all__`` 无重复（重复会让 ``import *`` 行为含糊）。"""
    assert len(_FACADE_ALL) == len(set(_FACADE_ALL))


def test_facade_keeps_its_key_public_api():
    """第 3 层（d）：关键公开名**必须仍在** ``__all__`` 里 ⇒ 门面公开面收缩必红。

    ⚠️ 这条**不是**「``__all__`` 精确相等」：新增导出不应让它变红
    （那属于「有序增长」，不是结构损坏）；但**删掉**一个既有导出 ⇒ 变红。
    """
    missing = sorted(_FACADE_KEY_PUBLIC_NAMES - set(_FACADE_ALL))
    assert not missing, f"门面 __all__ 丢了这些公开名：{missing}"


# ============================================================================
# 第 4 层：五族职责边界
# ============================================================================

#: 四个**子族**各自拥有的规则符号（门面 ``ai_patterns`` 单独列）。
#: 归属 = 「这个符号**定义**在这个族里」，是**防整理时把规则挪去别的族**的正向守卫。
_FAMILY_OWNED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "ai_patterns_sentence": ("count_contrast_pairs",),
    "ai_patterns_paragraph": (
        "count_short_paras",
        "count_long_paragraphs",
        "count_anthro_vehicles",
    ),
    "ai_patterns_dialogue": (
        "dialogue_ratio",
        "count_dialogue_echoes",
        "count_dialogue_visible_chars",
    ),
    "ai_patterns_text": ("_split_paragraphs",),
}

#: 门面 ``ai_patterns`` 自己拥有的符号（规则注册表 + 扫描入口）。
_FACADE_FAMILY_SYMBOLS = ("scan_ai_patterns", "AI_PATTERN_RULES", "AiPatternRule")


def test_each_sub_family_owns_its_pinned_rule_symbols():
    """第 4 层（a）：四个子族各自**定义**自己那批规则符号。"""
    for family, symbols in _FAMILY_OWNED_SYMBOLS.items():
        mod = importlib.import_module(f"packages.core.quality.{family}")
        defined = _defined_names(mod)
        for symbol in symbols:
            assert symbol in defined, f"{family} 上没有**定义**它自己拥有的 {symbol}"


def test_no_sub_family_defines_another_familys_rule_symbols():
    """第 4 层（b）：**任一族都不得定义别族**的钉住符号（跨族串味抓）。

    典型失效形状 = 「整理时把规则挪去别的族，但**旧的那份忘了删**」⇒ 两个族同时
    **定义**同一符号 ⇒ 本条必红（单看「新族有这个符号」是抓不到的）。
    ⚠️ 判据是「**定义**」而不是「能 import 到」：一族**import** 别的族的单点来用
    （``ai_patterns_paragraph`` 就 import 了 ``ai_patterns_text._split_paragraphs``）
    是**合法消费**，不算串味。
    """
    for family in _FAMILY_OWNED_SYMBOLS:
        mod = importlib.import_module(f"packages.core.quality.{family}")
        defined = _defined_names(mod)
        for other, symbols in _FAMILY_OWNED_SYMBOLS.items():
            if other == family:
                continue
            for symbol in symbols:
                assert symbol not in defined, (
                    f"{symbol} 属于 {other}，却也**定义**在 {family}（规则被挪族 / 旧份未删）"
                )


def test_facade_reexports_every_family_rule_symbol():
    """第 4 层（c）：门面 ``ai_patterns`` 必须**再导出**四族全部钉住符号 + 自己的。

    ⇒ 「整理时把某族的规则从门面上摘掉」必红（公开面不得悄悄缩水）。
    """
    facade = importlib.import_module("packages.core.quality.ai_patterns")
    for family, symbols in _FAMILY_OWNED_SYMBOLS.items():
        for symbol in symbols:
            assert hasattr(facade, symbol), (
                f"门面 ai_patterns 不再再导出 {family} 的 {symbol}（公开面缩水了）"
            )
    for symbol in _FACADE_FAMILY_SYMBOLS:
        assert hasattr(facade, symbol), f"门面 ai_patterns 不再暴露 {symbol}"
