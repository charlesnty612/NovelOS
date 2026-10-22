"""genre 域「结构钉」——模块清单 / 可直接导入 / 门面契约 / 职责边界。

2026-10-23 · novelos-quality-dev（批次 task-2）。

这份文件**不是**「这些模块没测试」。`packages/core/genre/` 的 8 个模块**全部有**行为
测试（`tests/unit/test_genre_*.py` / `tests/unit/test_signing_check_genre_opening.py`
/ `tests/unit/test_target_word_count_genre_pack.py` …），缺的是**结构看守**：

    改名 / 搬家 / 被合并 / 公开符号被悄悄挪走 ⇒ **不会有任何测试变红**。

⇒ 本文件补的正是这一层：把「域的结构」本身变成**可断言的对象**。

四层断言（每层都有可复算的断言，不是恒真断言）
--------------------------------------------
1. **模块清单**：**从目录派生** + 与名册**双向核对**。
2. **逐模块可直接导入** + 钉住符号**必须由该模块自己定义**（AST 口径）。
3. **门面 ``__all__``** 四条：无自指 / 有消费方 / 深路径可达 / 名册不静默缩小。
4. **职责边界**：各子模块**不得越权 import** 域内兄弟模块。

「从目录派生」而不是「手挑名单」——为什么
----------------------------------------
本仓已登记过同族事故（AGENTS 坑区「看守自己被悄悄缩小了执行域」）：**任何「固定清单
+ 计数/打点」的量具，一旦新增或搬迁一个被打点的名字而清单没同步，失效方向永远是
「读数变小」** ⇒ 变小会被读成进步。

⇒ 本文件的设计刻意避开该形状：

* **判别面（要守的东西）从目录派生**：每个落在 ``packages/core/genre/*.py`` 的模块
  **自动**进入「可直接导入」断言（判别面按目录现场数，不靠手抄名单）⇒ **新增模块
  不需要改本文件就已经在判别面里**（且第 1 层会因它「未入名册」而红，逼维护者
  显式登记它的机制单点）。
* **名册（:data:`_MODULE_SYMBOLS`）只登记「每个模块必须自备的证明」**：模块名 →
  它**自己定义**的钉住符号。登记一个新模块就必须同时钉它的符号 ⇒ 名册**不可能**
  悄悄变空。
* **名册之外的「允许的差集」显式登记**：:data:`_ALLOWED_NON_MODULE_FILES`（只有
  ``__init__.py``，理由见其注释）⇒ 例外不靠隐含，靠一行显式登记 + 注释理由。

⇒ 于是三个方向都红：
  ① 目录多出模块（未登记） ② 名册列了不存在的模块 ③ 登记了却没钉任何符号。

判据口径（逐条写明，避免「换个口径就换个结论」）
--------------------------------------------------
* **「定义」= AST 口径**：符号在**该模块源码里被定义**（``def`` / ``class`` /
  模块级赋值）。**不是** ``hasattr``——``hasattr`` 分不清「定义在这里」与「import
  进来用」（``models.py`` 就 import 了 ``model.GenrePack``，那是**合法消费**）。
* **深路径 import 仍可用** = ``packages.core.genre.<模块>`` 上该符号可取到。
* **「有消费方」= 域外 from-import 口径**：``packages/core/genre/`` 之外的任一
  ``.py`` 源码里以 ``from packages.core.genre[.<模块>] import <名字>`` 导入该名字。
  （深路径导入**算**消费方：门面导出的就是同一个符号对象。）
  唯一例外 :data:`_FACADE_NAMES_WITHOUT_CONSUMER` 显式登记（附理由）。
* **职责边界的依据** = 各模块**自己 docstring 里写明的职责**，逐条列在
  :data:`_ALLOWED_INTRA_DEPS` 的注释里；改动那张表必须在同一次改动里写明理由。

语料无关（不读任何书、不连真库）⇒ CI 上真红、不会 skip。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from packages.core import genre as _pkg
from packages.core.genre import __all__ as _FACADE_ALL

#: 本包在仓内的绝对导入前缀（深路径 import 的判据）。
_GENRE_PKG = "packages.core.genre"

#: 仓库源码目录（**消费方扫描的范围**）。
#: ⚠️ 刻意**不是** ``repo.rglob("*.py")``：那会把 ``.venv`` / ``node_modules`` /
#: ``tmp`` 一并吞进来 ⇒ 判别面被环境污染（量具失真同族）。
_SOURCE_DIRS = ("packages", "scripts", "tests", "database", "apps")


# ============================================================================
# 第 1 层：模块清单（目录派生 + 双向核对）
# ============================================================================


def _pkg_dir() -> Path:
    return Path(_pkg.__file__).parent


def _derive_module_files() -> set[str]:
    """**从目录现场派生**模块文件名集合（不写死任何模块名）。

    口径：``glob("*.py")`` 现场数（数量由集合相等隐含保证）。
    """
    return {p.name for p in _pkg_dir().glob("*.py")}


def _module_name(file_name: str) -> str:
    """``.py`` 文件名 → 可导入的模块名（``__init__.py`` 即包本身）。"""
    stem = file_name[:-3]
    return _GENRE_PKG if stem == "__init__" else f"{_GENRE_PKG}.{stem}"


def _read_source(path: Path) -> str:
    """读源码（``utf-8-sig``：容忍 BOM，与 Python import 的加载语义一致）。

    ⚠️ **口径纪律**：``ast.parse(str)`` **不容忍** BOM（而 ``import`` 走
    ``utf-8-sig`` 容忍）⇒ 用 ``utf-8`` 读会把一个**合法源文件**误判成「语法错误」。
    """
    return path.read_text(encoding="utf-8-sig")


def _defined_names(path: Path) -> frozenset[str]:
    """该模块**源码里定义**（而非 import 进来）的模块级名字。

    ``def`` / ``class`` / 模块级赋值（含带注解的 ``AnnAssign``）都算「定义」。
    ⇒ 「import 进来用」与「定义在这里」被分清。
    """
    tree = ast.parse(_read_source(path))
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


#: 名册：**每个子模块必须自备的证明** = 它**自己定义**的钉住符号。
#: 取的是各模块的「机制单点」（不是随便挑的名字）：搬走 / 改名 / 合并 ⇒ 对应条目必红。
#:
#: ⚠️ **改动本表必须在同一次改动里写明理由**——它不是「顺手加一行」的清单。
_MODULE_SYMBOLS: dict[str, tuple[str, ...]] = {
    # 读侧投影：题材包 payload → 各消费点的可消费形态（纯函数，除字带解析的 IO 例外）。
    "consumers.py": (
        "opening_rules",
        "critic_rubric",
        "forbidden_words",
        "count_forbidden_word_hits",
        "normalize_word_band",
        "resolve_chapter_word_band",
    ),
    # 资源 / 入参模型（pydantic）+ payload 消费子集视图 + schema 版本线常量。
    "model.py": (
        "GenrePack",
        "GenrePackCreate",
        "GenrePackUpdate",
        "GenrePackSummary",
        "GenrePackPayload",
        "GenrePayoffType",
        "parse_payload",
        "GENRE_PACK_SCHEMA_VERSION",
        "GENRE_PACK_SCHEMA_PATH",
    ),
    # API 出参模型（只描述三条绑定端点实际吐出的 JSON 键集）。
    "models.py": ("GenrePackBinding",),
    # SQL 文本 + 行 → dict 投影（无连接、无事务）。
    "queries.py": ("GET_PACK_SQL", "pack_row_to_dict", "pack_row_to_summary"),
    # CRUD / 项目绑定 / payload 校验（jsonschema 权威）。
    "service.py": ("GenrePackService", "BindStatus", "validate_payload"),
    # 章目标字数解析（题材包声明值插进优先级链）。
    "target_words.py": ("pack_chapter_words_target", "resolve_target_word_count"),
    # 核销层 v2（弧级配比 + 节奏红线 + untyped 显式化；report-only 不阻断）。
    "verifier.py": (
        "verify_chapter",
        "GenreCheckResult",
        "GenreIssue",
        "RATIO_DEVIATION_THRESHOLD",
    ),
}

#: **允许的差集**（显式登记的例外，每个都有理由）。
#:
#: 只有 ``__init__.py``：它是**门面**（纯再导出层）——实测它在自己的源码里**只定义**
#: ``__all__``（其余全是 ``from .x import y`` 的再导出）⇒ 对「本模块自己定义符号」
#: 这条判据天然不适用。它的结构由**第 3 层**（门面 ``__all__`` 四条）看守。
_ALLOWED_NON_MODULE_FILES = frozenset({"__init__.py"})


def test_every_genre_module_file_is_registered():
    """第 1 层（方向一 · 目录 → 名册）：目录里的每个 ``.py`` 都必须入册。

    ⇒ **新增模块未入册 ⇒ 必红**（防止「新模块进来却没人给它钉任何证明」）。
    """
    derived = _derive_module_files()
    registered = set(_MODULE_SYMBOLS) | set(_ALLOWED_NON_MODULE_FILES)
    unregistered = sorted(derived - registered)
    assert not unregistered, (
        f"packages/core/genre/ 下这些模块没进名册：{unregistered}"
        "（新增模块必须登记到 _MODULE_SYMBOLS 并钉住它的机制单点）"
    )


def test_every_registered_genre_module_exists_on_disk():
    """第 1 层（方向二 · 名册 → 目录）：名册列的每个模块都必须真的在目录里。

    ⇒ **名册列了不存在的模块 ⇒ 必红**（模块被改名/搬家后名册没跟上）。
    """
    derived = _derive_module_files()
    missing = sorted(set(_MODULE_SYMBOLS) - derived)
    assert not missing, (
        f"名册里的模块在 packages/core/genre/ 下不存在：{missing}"
        "（模块被改名 / 搬家 / 合并 ⇒ 名册必须同步）"
    )


def test_every_registered_genre_module_pins_at_least_one_symbol():
    """第 1 层（守门不自缩）：名册每一条**必须钉住 ≥1 个符号**。

    ⇒ 「登记了模块却没给任何证明」必红 ⇒ **名册不可能悄悄变空**（这正是
    AGENTS 坑区「看守自己被悄悄缩小了执行域」要防的形状）。
    """
    empty = sorted(name for name, syms in _MODULE_SYMBOLS.items() if not syms)
    assert not empty, f"这些模块登记了却没钉任何符号：{empty}"


# ============================================================================
# 第 2 层：逐模块可直接导入 + 钉住符号由本模块自己定义（AST 口径）
# ============================================================================


def test_every_genre_module_is_directly_importable():
    """第 2 层（a）：每个模块**不经过门面**都能直接 import 成功。

    门面的存在正是「深路径 import 无人验证」的成因 ⇒ 若某模块只能经门面触达
    （被合并进别的模块 / 被删），本条必红。
    """
    for file_name in sorted(_derive_module_files()):
        mod = importlib.import_module(_module_name(file_name))
        assert mod is not None, file_name


def test_pinned_symbols_are_defined_on_their_own_module():
    """第 2 层（b）：钉住符号必须**由该模块自己定义**（AST 口径，不用 ``hasattr``）。

    ⇒ 「符号被悄悄挪到别的模块」时，**原模块**这条必红。
    ⇒ 用「源码里定义」判定 ⇒ 合法地 import 别人的单点来用**不算**违规
    （``models.py`` 就 import 了 ``model.GenrePack``）。

    同时断言**深路径 import 仍可用**（``packages.core.genre.<模块>`` 上取得到）。
    ⚠️ 这两条对「模块级 def」而言是同一事实的两种说法（``getattr`` 必然成立）——
    合并在此，是为了让「深路径 import 仍可用」这条**契约**在测试里显式可见。
    真正能单独判别「公开面被摘」的是下一条（``__all__`` 归属）。
    """
    for file_name, symbols in sorted(_MODULE_SYMBOLS.items()):
        path = _pkg_dir() / file_name
        assert path.is_file(), f"{file_name} 不在 packages/core/genre/ 下"
        defined = _defined_names(path)
        mod = importlib.import_module(_module_name(file_name))
        for symbol in symbols:
            assert symbol in defined, f"{file_name} 上没有**定义** {symbol}"
            assert hasattr(mod, symbol), f"{file_name} 的深路径上取不到 {symbol}"


def test_pinned_symbols_are_declared_in_their_modules_public_surface():
    """第 2 层（d）：钉住符号必须列在**该模块自己的** ``__all__`` 里。

    ⇒ 「符号还在、还能 import，但模块已把它从自己的公开面摘掉」⇒ 本条必红。

    ⚠️ 这条与 2(b) **不是**同一条：``hasattr`` 看不见「公开面被摘」——
    模块级 ``def`` 一旦存在，``getattr`` 永远取得到，哪怕它已不在 ``__all__`` 里
    ⇒ 只测 2(b) 时这一类退化**测不出来**。
    """
    for file_name, symbols in sorted(_MODULE_SYMBOLS.items()):
        path = _pkg_dir() / file_name
        tree = ast.parse(_read_source(path))
        declared: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                declared = {
                    elt.value
                    for elt in ast.walk(node.value)
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
        assert declared, f"{file_name} 没有声明 __all__（本包的模块都声明公开面）"
        for symbol in symbols:
            assert symbol in declared, (
                f"{file_name} 的 __all__ 里没有 {symbol}（钉住符号被摘出公开面了）"
            )


# ============================================================================
# 第 3 层：门面 genre/__init__.py 的 __all__ 四条
# ============================================================================

#: 门面必须继续再导出的**关键公开名**（子集断言 ⇒ 新增导出不受影响，
#: 但**删掉**其中任何一个 ⇒ 必红）。
_FACADE_KEY_PUBLIC_NAMES = frozenset(
    {
        "GenrePackService",
        "GenrePack",
        "GenrePackCreate",
        "GenrePackUpdate",
        "GenrePackSummary",
        "GenrePackPayload",
        "GenrePayoffType",
        "BindStatus",
        "validate_payload",
        "verify_chapter",
        "GenreCheckResult",
        "GenreIssue",
        "RATIO_DEVIATION_THRESHOLD",
        "GENRE_PACK_SCHEMA_VERSION",
        "GENRE_PACK_SCHEMA_PATH",
        "opening_rules",
        "critic_rubric",
        "forbidden_words",
        "count_forbidden_word_hits",
        "CRITIC_RUBRIC_MAX_CHARS",
        "CRITIC_RUBRIC_TRUNCATED_KEY",
        "pack_chapter_words_target",
        "resolve_target_word_count",
    }
)

#: **允许的差集**：门面导出但**域外没有任何消费方**的名字（显式登记 + 理由）。
#:
#: ``GenrePayoffType`` —— payload 的 ``payoff_types[]`` 元素模型：
#:   ① **包内自消费**：``model.GenrePackPayload.payoff_types`` 的元素类型；
#:   ② 前端 ``apps/web/src/api/types.ts`` 有同款 TS 接口（跨语言消费方，Python 侧
#:      自然扫不到）；
#:   ③ 它是 **payload 契约的一部分**（删掉即改 payload 消费子集的公开面）。
#: ⇒ 仓内 Python 侧零域外导入方，**显式登记**而非硬凑。
_FACADE_NAMES_WITHOUT_CONSUMER = frozenset({"GenrePayoffType"})


def test_facade_all_has_no_self_referential_elements():
    """第 3 层（a）：``__all__`` **不得含自指元素**（AGENTS 硬规则 1）。

    自指 = 把包自己（或它的任一层限定名）当成对外导出的名字。
    """
    forbidden = {"genre", "__init__", "packages", "core", _GENRE_PKG}
    for name in _FACADE_ALL:
        assert name not in forbidden, f"__all__ 含自指元素 {name}"
        assert not name.startswith("packages."), f"__all__ 含自限定的自指元素 {name}"


def test_facade_all_names_resolve_on_the_package():
    """第 3 层（a′）：``__all__`` 里每个名字都能在**包上**解析到（无悬空导出）。

    ⇒ ``from packages.core.genre import *`` 会因悬空名直接失败 ⇒ 必红。
    """
    dangling = sorted(name for name in _FACADE_ALL if not hasattr(_pkg, name))
    assert not dangling, f"__all__ 里的 {dangling} 在包上解析不到（悬空导出）"


def _facade_name_consumers() -> dict[str, list[str]]:
    """扫描域外源码 ⇒ 每个门面名的消费方清单（``名字 -> ["文件:行", ...]``）。

    口径（见模块 docstring）：``from packages.core.genre[.<模块>] import <名字>``。
    """
    repo_root = Path(__file__).resolve().parents[3]
    names = set(_FACADE_ALL)
    hits: dict[str, list[str]] = {name: [] for name in names}
    for top in _SOURCE_DIRS:
        root = repo_root / top
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel.startswith(f"{_GENRE_PKG.replace('.', '/')}/"):
                continue  # 域内自消费不算
            try:
                tree = ast.parse(_read_source(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                mod = node.module or ""
                # ⚠️ 精确前缀：``packages.core.genre`` 或 ``packages.core.genre.<模块>``
                # ⇒ 不得用裸 ``startswith``（那会把 ``packages.core.genre_other``
                # 一并算进来 ⇒ **假消费方**）。
                if mod != _GENRE_PKG and not mod.startswith(_GENRE_PKG + "."):
                    continue
                for alias in node.names:
                    if alias.name in names:
                        hits[alias.name].append(f"{rel}:{node.lineno}")
    return hits


def test_facade_exported_names_all_have_a_consumer():
    """第 3 层（b）：门面导出的每个名字**都有消费方**（域外 from-import 口径）。

    ⇒ 导出一个**没人用**的名字 ⇒ 必红（要么真有人该用，要么该从门面摘掉）。
    ⇒ 唯一例外 :data:`_FACADE_NAMES_WITHOUT_CONSUMER` 显式登记（附理由）。
    """
    hits = _facade_name_consumers()
    orphan = sorted(name for name, sites in hits.items() if not sites)
    unregistered = sorted(set(orphan) - _FACADE_NAMES_WITHOUT_CONSUMER)
    assert not unregistered, (
        f"门面导出了这些没人用的名字：{unregistered}"
        "（要么补消费方，要么从 __all__ 摘掉；若确认无误请登记进 "
        "_FACADE_NAMES_WITHOUT_CONSUMER 并写明理由）"
    )
    stale = sorted(set(_FACADE_NAMES_WITHOUT_CONSUMER) - set(orphan))
    assert not stale, (
        f"这些名字已登记为「无消费方」但现在**有**消费方了：{stale}"
        "（请把登记删掉——差集只允许缩小，不允许遗忘）"
    )


def test_facade_exports_keep_their_deep_path_reachable():
    """第 3 层（c）：门面导出的每个名字，其**属主子模块的深路径**仍可达。

    ⇒ 「门面还在导出，但子模块已经不再提供它」⇒ 必红（深路径 import 失效）。
    ⇒ 属主**从目录派生**（AST 口径：哪个模块**定义**了这个符号），不写死映射表。
    """
    owners: dict[str, list[str]] = {}
    for path in sorted(_pkg_dir().glob("*.py")):
        if path.name in _ALLOWED_NON_MODULE_FILES:
            continue
        defined = _defined_names(path)
        for name in _FACADE_ALL:
            if name in defined:
                owners.setdefault(name, []).append(path.stem)

    for name in _FACADE_ALL:
        owner_list = owners.get(name, [])
        assert owner_list, f"门面导出的 {name} 在任何子模块里都没有**定义**它"
        assert len(owner_list) == 1, (
            f"{name} 被多个子模块同时定义（{owner_list}）⇒ 属主不唯一"
        )
        owner_mod = importlib.import_module(f"{_GENRE_PKG}.{owner_list[0]}")
        assert hasattr(owner_mod, name), (
            f"深路径 packages.core.genre.{owner_list[0]} 上取不到 {name}"
        )


def test_facade_keeps_its_key_public_api():
    """第 3 层（d）：关键公开名**必须仍在** ``__all__`` 里 ⇒ 门面公开面收缩必红。

    ⚠️ 这条**不是**「``__all__`` 精确相等」：新增导出不应让它变红
    （那属于「有序增长」，不是结构损坏）；但**删掉**一个既有导出 ⇒ 变红。
    """
    missing = sorted(_FACADE_KEY_PUBLIC_NAMES - set(_FACADE_ALL))
    assert not missing, f"门面 __all__ 丢了这些公开名：{missing}"


# ============================================================================
# 第 4 层：职责边界（各子模块不越权 import）
# ============================================================================

#: 各子模块**允许**依赖的域内兄弟模块。
#:
#: 依据 = 各模块**自己 docstring 里写明的职责**（逐条引在注释里）+ 源码实测：
#: * ``model`` —— 资源/入参模型；「本模块的 pydantic 模型是**宽松视图**」，
#:   无职责需要任何兄弟模块 ⇒ 零域内依赖。
#: * ``models`` —— API 出参模型；「本文件的 ``pack`` 直接复用 ``model.GenrePack``」
#:   ⇒ 只依赖 ``model``。
#: * ``queries`` —— 「本模块只放 SQL 文本与纯函数投影，不持有连接、不 commit；
#:   连接与事务在 service」⇒ 只依赖 ``model``（复用 payload 视图做摘要抽取）。
#: * ``service`` —— CRUD / 绑定；连接与事务归它 ⇒ 依赖 ``model`` + ``queries``。
#:   **不依赖** ``consumers`` / ``target_words`` / ``verifier``：那三者依赖
#:   ``service``，反向依赖即成环。
#: * ``consumers`` —— 读侧投影；docstring 明写「除上述 IO 例外，本模块只做投影」
#:   且唯一的 service 依赖是**函数内延迟导入** ⇒ **模块级**零域内依赖
#:   （见 :data:`_ALLOWED_DEFERRED_INTRA_DEPS`）。
#: * ``target_words`` —— 章目标字数；「本模块只读项目绑定题材包的 …」⇒ 依赖
#:   ``service``（读绑定）。
#: * ``verifier`` —— 核销层；自己开连接读库 ⇒ 只依赖 ``consumers``（复用字数带
#:   规范化单点）。
#: * ``__init__`` —— 门面；再导出 5 个子模块（``models`` / ``queries`` 是
#:   **深路径专用**，门面不导出）⇒ 依赖集合必须**恰好**等于这 5 个。
_ALLOWED_INTRA_DEPS: dict[str, frozenset[str]] = {
    "model": frozenset(),
    "models": frozenset({"model"}),
    "queries": frozenset({"model"}),
    "service": frozenset({"model", "queries"}),
    "consumers": frozenset(),
    "target_words": frozenset({"service"}),
    "verifier": frozenset({"consumers"}),
    "__init__": frozenset({"consumers", "model", "service", "target_words", "verifier"}),
}

#: **允许的延迟依赖**（只允许出现在**函数体内**，不得在模块级）。
#:
#: ``consumers`` → ``service``：``consumers._load_bound_word_band`` 里的
#: ``from .service import GenrePackService`` 是**函数内**延迟导入，源码注释明写
#: 「延迟导入：避免模块级 IO 依赖」⇒ 模块级出现它就是**破坏了那条设计**。
_ALLOWED_DEFERRED_INTRA_DEPS: dict[str, frozenset[str]] = {
    "consumers": frozenset({"service"}),
}


def _intra_import_targets(node: ast.ImportFrom | ast.Import) -> set[str]:
    """该 import 语句指向的**域内**模块名（非域内则忽略）。"""
    targets: set[str] = set()
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if node.level == 1:
            # 相对同包 import：``from . import queries`` / ``from .model import X``
            if mod:
                targets.add(mod.split(".")[0])
            else:
                targets.update(alias.name for alias in node.names)
        elif mod == _GENRE_PKG or mod.startswith(_GENRE_PKG + "."):
            rest = mod[len(_GENRE_PKG) :].lstrip(".")
            if rest:
                targets.add(rest.split(".")[0])
    else:
        for alias in node.names:
            if alias.name == _GENRE_PKG or alias.name.startswith(_GENRE_PKG + "."):
                rest = alias.name[len(_GENRE_PKG) :].lstrip(".")
                if rest:
                    targets.add(rest.split(".")[0])
    return targets


def _intra_imports_by_scope(path: Path) -> tuple[set[str], set[str]]:
    """该模块的域内 import ⇒ ``(模块级集合, 函数体内集合)``。

    「模块级」= import 语句是 ``tree.body`` 的直接子节点；其余（函数体 / 类体
    内的 import）归「函数体内」。
    """
    tree = ast.parse(_read_source(path))
    module_level_ids = {
        id(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    module_level: set[str] = set()
    nested: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        targets = _intra_import_targets(node)
        if id(node) in module_level_ids:
            module_level |= targets
        else:
            nested |= targets
    return module_level, nested


def test_genre_modules_do_not_cross_responsibility_lines():
    """第 4 层：各子模块**不得越权 import** 域内兄弟模块。

    ⇒ 「A 模块开始依赖它不该依赖的 B」⇒ 必红（分层被打穿时不会有任何行为测试变红）。

    两条判据分开判（**模块级** vs **函数体内**）：
    * **模块级**依赖 ⊆ :data:`_ALLOWED_INTRA_DEPS`；
    * **函数体内**的依赖额外允许 :data:`_ALLOWED_DEFERRED_INTRA_DEPS` 里那几条
      （延迟导入是有意设计，不是越权）。
    """
    for path in sorted(_pkg_dir().glob("*.py")):
        stem = path.stem
        allowed = _ALLOWED_INTRA_DEPS.get(stem)
        assert allowed is not None, f"{path.name} 没有登记职责边界（新增模块必须登记）"
        module_level, nested = _intra_imports_by_scope(path)
        illegal = sorted(module_level - allowed)
        assert not illegal, (
            f"{path.name} 在**模块级**依赖了越权模块 {illegal}"
            f"（允许的域内依赖 = {sorted(allowed)}）"
        )
        deferred = _ALLOWED_DEFERRED_INTRA_DEPS.get(stem, frozenset())
        illegal_nested = sorted(nested - allowed - deferred)
        assert not illegal_nested, (
            f"{path.name} 在函数体内依赖了越权模块 {illegal_nested}"
            f"（允许 = {sorted(allowed)} + 延迟导入 {sorted(deferred)}）"
        )


def test_genre_facade_reexports_every_submodule_it_is_supposed_to():
    """第 4 层（门面侧）：门面必须**再导出**它该再导出的全部子模块。

    ⇒ 「某个子模块被从门面上摘掉」（公开面悄悄缩水）⇒ 必红。
    ⇒ 判据是 :data:`_ALLOWED_INTRA_DEPS['__init__']`——门面的依赖集合必须
    **恰好**等于它（多一个 = 越权，少一个 = 公开面缩水）。
    """
    path = _pkg_dir() / "__init__.py"
    module_level, nested = _intra_imports_by_scope(path)
    expected = _ALLOWED_INTRA_DEPS["__init__"]
    assert module_level == expected, (
        "门面的域内依赖集合与登记不符："
        f"多出 {sorted(module_level - expected)} / 少了 {sorted(expected - module_level)}"
    )
    assert not nested, f"门面里出现了函数体内的域内 import：{sorted(nested)}（应全在模块级）"
